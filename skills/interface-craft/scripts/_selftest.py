"""_selftest.py: checks the measurement engine against pages whose contents are known.

Run it with `ui_measure.py selftest`. It renders the pages in selftest/, drives the real
command line, and compares what comes out with selftest/expected.json and with the schema
blocks in EVIDENCE.md. A clean page must produce no FAIL and no WARN; every planted defect
must be found by its rule id. The checker is tested like code because a checker that cannot
find a known defect, or that fails a clean page, is worse than no checker.

Compatible with Python 3.9.
"""
import contextlib
import hashlib
import importlib
import io
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time

import _engine
import _png
import _policy
import _rules

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = os.path.join(HERE, "selftest")
EVIDENCE_MD = os.path.join(HERE, "EVIDENCE.md")
TIME_BUDGET_S = 70


class Tester(object):
    def __init__(self):
        self.passed, self.failed = 0, 0
        self.failures = []

    def check(self, name, ok, detail=""):
        if ok:
            self.passed += 1
            sys.stdout.write("PASS %s\n" % name)
        else:
            self.failed += 1
            self.failures.append(name)
            sys.stdout.write("FAIL %s%s\n" % (name, (": " + detail) if detail else ""))
        return ok


# ----------------------------------------------------------------------------
# schema blocks from EVIDENCE.md

def parse_blocks(text):
    blocks = {}
    for match in re.finditer(r"<!-- schema:(\w+) -->\s*```json\n(.*?)\n```", text, re.S):
        blocks[match.group(1)] = json.loads(match.group(2))
    return blocks


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _type_ok(value, t, defs):
    if t == "any":
        return True
    if t == "string":
        return isinstance(value, str)
    if t == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return _is_num(value)
    if t == "bool":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    if t == "object":
        return isinstance(value, dict)
    if t == "list":
        return isinstance(value, list)
    if t == "rect":
        return isinstance(value, list) and len(value) == 4 and all(_is_num(v) for v in value)
    if t == "rgba":
        return isinstance(value, list) and len(value) == 4 and all(_is_num(v) for v in value)
    if t == "rgb":
        return isinstance(value, list) and len(value) == 3 and all(isinstance(v, int) for v in value)
    if t == "size":
        return isinstance(value, list) and len(value) == 2 and all(isinstance(v, int) for v in value)
    if t.startswith("enum:"):
        return value in t[5:].split(",")
    if t.startswith("@"):
        errs = []
        check_shape(value, defs[t[1:]], defs, "", errs)
        return not errs
    return False


def check_shape(value, skel, defs, path, errs):
    if isinstance(skel, str):
        t = skel
        if t.endswith("?"):
            t = t[:-1]
            if value is None:
                return
        if not any(_type_ok(value, alt, defs) for alt in t.split("|")):
            errs.append("%s: %s is not %s" % (path or "value", json.dumps(value)[:60], skel))
        return
    if isinstance(skel, list):
        if not isinstance(value, list):
            errs.append("%s: expected a list" % path)
            return
        for i, item in enumerate(value):
            check_shape(item, skel[0], defs, "%s[%d]" % (path, i), errs)
        return
    if not isinstance(value, dict):
        errs.append("%s: expected an object" % path)
        return
    required = dict((k, v) for k, v in skel.items() if not k.endswith("?") and k != "*")
    optional = dict((k[:-1], v) for k, v in skel.items() if k.endswith("?"))
    for key, sub in required.items():
        if key not in value:
            errs.append("%s.%s: missing" % (path, key))
        else:
            check_shape(value[key], sub, defs, "%s.%s" % (path, key), errs)
    for key, val in value.items():
        if key in required:
            continue
        if key in optional:
            check_shape(val, optional[key], defs, "%s.%s" % (path, key), errs)
        elif "*" in skel:
            check_shape(val, skel["*"], defs, "%s.%s" % (path, key), errs)
        else:
            errs.append("%s.%s: key is not documented in EVIDENCE.md" % (path, key))


def sha256_file(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def validate_run(run_dir, defs):
    """Schema and invariant checks from EVIDENCE.md. Returns a list of problems."""
    problems = []
    with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    errs = []
    check_shape(manifest, defs["manifest"], defs, "manifest", errs)
    problems += errs
    totals = {"FAIL": 0, "WARN": 0, "INFO": 0}
    failing = 0
    for entry in manifest["evidence"]:
        path = os.path.join(run_dir, entry["json"])
        if sha256_file(path) != entry["json_sha256"]:
            problems.append("%s: json hash does not match the manifest" % entry["id"])
        with open(path, encoding="utf-8") as handle:
            ev = json.load(handle)
        errs = []
        check_shape(ev, defs["evidence"], defs, ev["id"], errs)
        problems += errs
        if entry["png"]:
            if sha256_file(os.path.join(run_dir, entry["png"])) != entry["sha256"]:
                problems.append("%s: png hash does not match the manifest" % ev["id"])
            if ev["sha256"] != entry["sha256"]:
                problems.append("%s: file and manifest disagree on the png hash" % ev["id"])
        if "full" in entry and sha256_file(os.path.join(run_dir, entry["full"])) != ev["full"]["sha256"]:
            problems.append("%s: full-page png hash does not match" % ev["id"])
        if ev["cite"] != "%s@%d:%s#%s" % (ev["id"], ev["viewport"]["width"], ev["state"], ev["theme"]):
            problems.append("%s: cite %s does not follow the format" % (ev["id"], ev["cite"]))
        tally = {"FAIL": 0, "WARN": 0, "INFO": 0}
        ids = set()
        for rid, r in ev["rules"].items():
            for f in r["findings"]:
                tally[f["tier"]] += 1
                if f["id"] in ids:
                    problems.append("%s: duplicate finding id %s" % (ev["id"], f["id"]))
                ids.add(f["id"])
        if tally != ev["summary"]:
            problems.append("%s: summary %s does not match findings %s" % (ev["id"], ev["summary"], tally))
        for k in totals:
            totals[k] += ev["summary"][k]
        if any(f["tier"] == "FAIL" and not f.get("expected") for r in ev["rules"].values() for f in r["findings"]):
            failing += 1
        if ev["kind"] == "page":
            vt = ev["rules"].get("viewport.truth", {}).get("status")
            if vt != "fail":
                if ev["facts"].get("innerWidth") != ev["viewport"]["width"]:
                    problems.append("%s: innerWidth differs from viewport.width without a viewport.truth failure" % ev["id"])
                if ev["png_size"][0] != round(ev["viewport"]["width"] * ev["viewport"]["dpr"]):
                    problems.append("%s: png width differs from width x dpr without a viewport.truth failure" % ev["id"])
            missing = [rid for rid in _rules.RULE_IDS if rid not in ev["rules"]]
            if missing:
                problems.append("%s: rules did not run: %s" % (ev["id"], ", ".join(missing)))
            for key in ("innerWidth", "clientWidth", "scrollWidth", "dpr", "theme", "url", "dom_hash", "scrollbar_px", "ignored", "storage_keys"):
                if key not in ev["facts"]:
                    problems.append("%s: facts.%s is missing" % (ev["id"], key))
        if ev["kind"] != "foreign" and ev["state_confirmed"] is False and ev["rules"].get("state.confirmed", {}).get("status") != "fail":
            problems.append("%s: state_confirmed is false without a failing state.confirmed rule" % ev["id"])
        skipped = [rid for rid, r in ev["rules"].items() if r["status"] == "skipped"]
        if skipped:
            problems.append("%s: skipped rules: %s" % (ev["id"], ", ".join(skipped)))
        if ev["collector"]["errors"]:
            problems.append("%s: collector errors: %s" % (ev["id"], "; ".join(ev["collector"]["errors"][:2])))
    for a in manifest["artifacts"]:
        if sha256_file(os.path.join(run_dir, a["file"])) != a["sha256"]:
            problems.append("artifact %s hash does not match" % a["file"])
    if manifest["summary"]["FAIL"] != totals["FAIL"] or manifest["summary"]["failing_evidence"] != failing:
        problems.append("manifest summary does not match the evidence files")
    return problems


# ----------------------------------------------------------------------------
# helpers

def cli(um, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = um.main(argv)
    return code, out.getvalue(), err.getvalue()


def latest_run(root):
    runs = sorted(d for d in os.listdir(root) if re.match(r"^\d{3}-", d) and os.path.isdir(os.path.join(root, d)))
    return os.path.join(root, runs[-1])


def load_run(run_dir):
    with open(os.path.join(run_dir, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    evs = []
    for e in manifest["evidence"]:
        with open(os.path.join(run_dir, e["json"]), encoding="utf-8") as handle:
            evs.append(json.load(handle))
    return manifest, evs


def findings(evs, rule, tier=None, width=None, theme=None, contains=None):
    hits = []
    for ev in evs:
        if width and ev["viewport"]["width"] != width:
            continue
        if theme and ev["theme"] != theme:
            continue
        r = ev["rules"].get(rule)
        for f in (r["findings"] if r else []):
            if tier and f["tier"] != tier:
                continue
            blob = ("%s %s %s %s" % (f["message"], f.get("selector"), f.get("worst"), f.get("value"))).lower()
            if contains and contains.lower() not in blob:
                continue
            hits.append((ev, f))
    return hits


def selectors_of(finding):
    """The selector of a finding and of each of its examples."""
    return [finding.get("selector") or ""] + [(e.get("selector") or "") for e in finding.get("examples") or []]


def class_findings(evs, rule, cls, tier=None, width=None, contains=None):
    """Findings of a rule that name an element carrying the class `cls`, in the finding itself or in its examples."""
    pattern = re.compile(r"\.%s(?![\w-])" % re.escape(cls))
    hits = []
    for ev in evs:
        if width and ev["viewport"]["width"] != width:
            continue
        r = ev["rules"].get(rule)
        for f in (r["findings"] if r else []):
            if tier and f["tier"] != tier:
                continue
            if contains and contains.lower() not in f["message"].lower():
                continue
            if any(pattern.search(sel) for sel in selectors_of(f)):
                hits.append((ev, f))
    return hits


def oklch_to_rgb(lightness, chroma, hue_deg):
    """Independent OKLCH to sRGB (same formulas as the repo's eval suite), used to cross-check the canvas readback."""
    h = math.radians(hue_deg)
    a, b = chroma * math.cos(h), chroma * math.sin(h)
    l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    lin = (4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
           -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
           -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s)

    def enc(x):
        x = max(0.0, min(1.0, x))
        return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055
    return [int(round(255 * enc(x))) for x in lin]


def page(name):
    return os.path.join(PAGES, name)


# ----------------------------------------------------------------------------
# unit checks (no browser)

def unit_checks(T, exp):
    u = exp["unit"]
    for fg, bg, want in u["contrast"]:
        got = _rules.contrast_ratio(_rules.hex_to_rgb(fg), _rules.hex_to_rgb(bg))
        T.check("contrast %s on %s is %s:1" % (fg, bg, want), abs(got - want) < 0.005, "got %.4f" % got)
    for hexcolor, want in u["hue"]:
        _l, _c, hue = _rules.to_oklch(_rules.hex_to_rgb(hexcolor))
        T.check("oklch hue of %s is about %s" % (hexcolor, want), abs(hue - want) < 0.5, "got %.2f" % hue)
    T.check("composite blends alpha", _rules.composite([0, 0, 0], [255, 255, 255], 0.5) == [128, 128, 128])

    # PNG codec: every filter type, a blur, a crop, and a squint round trip
    w, h = 7, 9
    rgb = bytearray()
    for y in range(h):
        for x in range(w):
            rgb += bytes(((x * 37 + y * 11) % 256, (x * 13 + y * 53) % 256, (x * 5 + y * 71) % 256))
    png = _png.encode(w, h, rgb, filters=[0, 1, 2, 3, 4])
    w2, h2, back = _png.decode(png)
    T.check("png round trip with filters 0 to 4", (w2, h2, bytes(back)) == (w, h, bytes(rgb)))
    T.check("png header read", _png.read_size(png) == (w, h))
    cw, ch, cropped = _png.crop(back, w, h, 2, 3, 3, 2)
    T.check("png crop", (cw, ch) == (3, 2) and bytes(cropped[:3]) == bytes(_png.pixel(rgb, w, 2, 3)))
    flat = bytearray([100, 150, 200] * 25)
    T.check("blur keeps a flat image flat", bytes(_png.box_blur(flat, 5, 5, 2)) == bytes(flat))
    spike = bytearray(25 * 3)
    spike[(2 * 5 + 2) * 3:(2 * 5 + 2) * 3 + 3] = bytes([255, 255, 255])
    blurred = _png.box_blur(spike, 5, 5, 1)
    T.check("blur spreads a bright pixel", 27 <= _png.pixel(blurred, 5, 2, 2)[0] <= 29 and _png.pixel(blurred, 5, 1, 1)[0] > 0)
    T.check("squint returns a png", _png.read_size(_png.squint(png, 1)) == (w, h))

    # request policy and start URLs
    for url, method, allow, want in u["policy"]:
        got = _policy.request_decision(url, method, allow)[0]
        T.check("policy %s %s -> %s" % (method, url, want), got == want, "got %s" % got)
    for url, allow, want in u["start_url"]:
        try:
            _policy.check_start_url(url, allow)
            got = "ok"
        except _policy.PolicyError:
            got = "refuse"
        T.check("start url %r -> %s" % (url, want), got == want, "got %s" % got)
    try:
        _policy.safe_join(HERE, "selftest", "x" + os.sep + ".." + os.sep + ".." + os.sep + ".." + os.sep + "escape")
        escaped = False
    except _policy.PolicyError:
        escaped = True
    T.check("safe_join refuses a path that leaves the folder", escaped)

    # defaults.json and the rule registry agree
    with open(os.path.join(HERE, "defaults.json"), encoding="utf-8") as handle:
        d = json.load(handle)
    ids = set(_rules.RULE_IDS)
    T.check("every rule has exactly one tier", set(d["tiers"]) == ids, "difference %s" % sorted(set(d["tiers"]) ^ ids))
    T.check("every rule has thresholds", set(d["thresholds"]) == ids, "difference %s" % sorted(set(d["thresholds"]) ^ ids))
    bad_sub = [k for k in d["subtiers"] if k.split(":")[0] not in ids]
    T.check("every subtier belongs to a rule", not bad_sub, ", ".join(bad_sub))
    T.check("tiers are FAIL, WARN, or INFO", all(v in ("FAIL", "WARN", "INFO") for v in list(d["tiers"].values()) + list(d["subtiers"].values())))
    profile_rules = [r for p in d["profiles"].values() for r in (p.get("thresholds") or {})]
    T.check("profiles only override known rules", all(r in ids for r in profile_rules))
    src = open(os.path.join(HERE, "_rules.py"), encoding="utf-8").read() + open(os.path.join(HERE, "_slop.py"), encoding="utf-8").read()
    cited = set(re.findall(r'\.(?:t|th)\("([a-z0-9_.]+)"', src))
    T.check("every rule threshold cited in code exists in defaults.json", cited <= set(d["thresholds"]), ", ".join(sorted(cited - set(d["thresholds"]))))

    # type.wrap must not guess when Chrome exposes no computed text-wrap
    cfg = _rules.Cfg(_rules.deep_merge(dict((k, v) for k, v in d.items() if k != "profiles"), d["profiles"]["product"]))
    nodes = [{"i": 0, "p": -1, "t": "body", "s": "body", "d": "block", "r": [0, 0, 390, 200]},
             {"i": 1, "p": 0, "t": "h2", "s": "h2", "d": "block", "r": [16, 10, 358, 50]}]
    texts = [{"n": 1, "t": "A heading that wraps", "len": 20, "ln": 2, "lr": [[16, 10, 350, 25], [16, 35, 40, 25]], "tx": 16, "cl": 0, "fs": 20, "fw": 600, "lh": None, "ls": 0,
              "ff": "x", "fam": "x", "ta": "start", "fg": [0, 0, 0, 1], "op": 1, "bg": {"c": [255, 255, 255], "u": None}, "hd": 2}]
    for api, want_tier in ((None, "INFO"), ("text-wrap-style", "WARN")):
        raw = {"facts": {"text_wrap_api": api, "innerWidth": 390}, "nodes": nodes, "texts": texts, "headings": [{"n": 1, "l": 2, "t": "A heading that wraps"}]}
        _checked, found = _rules.r_type_wrap(_rules.Ctx(raw, {"width": 390, "height": 844, "dpr": 1, "theme": "light"}, cfg, {}))
        T.check("type.wrap with text_wrap_api %s gives one %s finding" % (api, want_tier), len(found) == 1 and found[0]["tier"] == want_tier
                and (api is not None or "does not expose" in found[0]["message"]), str(found[:1]))
    shell_unit_checks(T, cfg)


# ----------------------------------------------------------------------------
# shell.consistency without a browser: synthetic facts for several routes

def shell_cell(route, edits=None, width=1280, content_edits=None, drop=(), eid=None, theme="light"):
    """One route at one width, as the comparison reads it: facts.shell and facts.content. edits are {"landmark.key": value}."""
    shell = {"banner": {"selector": "header.top", "rect": [0, 0, width, 56], "pos": "sticky", "bg": [255, 255, 255], "border": 1},
             "nav": {"selector": "nav.rail", "rect": [0, 56, 240, 744], "pos": "static", "bg": [246, 248, 250], "border": 1, "orientation": "vertical",
                     "items": {"count": 5, "height": 36, "gap": 4, "font_size": 14}},
             "main": {"selector": "main", "rect": [240, 56, width - 240, 690]},
             "contentinfo": {"selector": "footer.foot", "rect": [240, 746, width - 240, 54], "pos": "static", "bg": [246, 248, 250], "border": 1},
             "title": {"selector": "h1", "text": "Title", "rect": [304, 80, 912, 35], "size": 28, "weight": 600, "color": [27, 31, 36], "top": 24},
             "edge": None}
    content = {"region": [240, width], "column": [304, 1216], "width": 912, "gutters": [64, 64], "aside": False, "selector": "div.col"}
    for path, value in (edits or {}).items():
        kind, _dot, key = path.partition(".")
        if key.startswith("items."):
            shell[kind]["items"][key[6:]] = value
        elif key == "rect":
            shell[kind]["rect"] = value
        else:
            shell[kind][key] = value
    content.update(content_edits or {})
    for kind in drop:
        shell[kind] = None
    eid = eid or route
    return {"id": eid, "cite": "%s@%d:%s#%s" % (eid, width, route, theme), "route": route, "name": "/" + route, "width": width, "theme": theme, "shell": shell,
            "content": None if "content" in drop else content}


def shell_unit_checks(T, cfg):
    cmp_ = _rules.compare_shells

    def found(result, route=None, key=None, tier=None):
        return [f for eid, fs in result["findings"].items() for f in fs if (route is None or f["detail"]["route"] == route) and (key is None or f["key"] == key)
                and (tier is None or f["tier"] == tier)]

    same = [shell_cell("a"), shell_cell("b"), shell_cell("c")]
    T.check("shell.consistency: routes with one shell have no finding", not found(cmp_(same, cfg)), str(found(cmp_(same, cfg))[:1]))
    wide = [shell_cell("a"), shell_cell("b"), shell_cell("c", {"nav.rect": [0, 56, 256, 744]})]
    r = cmp_(wide, cfg)
    hit = found(r, "c", "geometry:nav:width")
    T.check("shell.consistency: a rail 16px wider names the landmark, the dominant value, the deviating route and its value, the width, and the fix",
            len(hit) == 1 and hit[0]["tier"] == "WARN" and "the nav is 256px wide on /c at 1280px wide but 240px on /a, /b (2 of 3 routes)" in hit[0]["message"]
            and "render one shared shell component for every route and let only the content region change" in hit[0]["message"] and hit[0]["detail"]["dominant"] == 240
            and hit[0]["value"] == 16 and found(r, "a") == [] and found(r, "b") == [], str(hit)[:300])
    T.check("shell.consistency: the finding sits in the evidence of the deviating route only", [eid for eid, fs in r["findings"].items() if fs] == ["c"])
    near = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", {"nav.rect": [0, 56, 241, 744]})], cfg)
    over = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", {"nav.rect": [0, 56, 241.5, 744]})], cfg)
    T.check("shell.consistency: a difference within the tolerance passes and one beyond it is reported", not found(near) and len(found(over)) == 1)
    T.check("shell.consistency: the tolerance is an argument", not found(cmp_(wide, cfg, tol=16)) and len(found(cmp_(wide, cfg, tol=15.5))) == 1)
    two = cmp_([shell_cell("a"), shell_cell("b", {"nav.rect": [0, 56, 256, 744]})], cfg)
    T.check("shell.consistency: with two routes and no majority the first route is the reference, and the message says so",
            len(found(two, "b", "geometry:nav:width")) == 1 and "no majority; the first route listed is the reference" in found(two, "b")[0]["message"] and not found(two, "a"))
    gone = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", drop=("contentinfo",))], cfg)
    hit = found(gone, "c", "missing:contentinfo")
    T.check("shell.consistency: a footer missing on one route is a missing finding on that route",
            len(hit) == 1 and "the footer is missing on /c at 1280px wide but present on /a, /b (2 of 3 routes)" in hit[0]["message"] and hit[0]["worst"] == "missing (present on 2 of 3 routes)")
    only = cmp_([shell_cell("a", drop=("contentinfo",)), shell_cell("b", drop=("contentinfo",)), shell_cell("c")], cfg)
    T.check("shell.consistency: the majority decides, so a footer on one route among routes without one is the deviation",
            len(found(only, "c", "missing:contentinfo")) == 1 and "is present on /c" in found(only, "c")[0]["message"] and not found(only, "a"))
    tall = cmp_([shell_cell("a"), shell_cell("b", {"nav.rect": [0, 56, 240, 1400], "contentinfo.rect": [240, 1346, 1040, 54]}), shell_cell("c")], cfg)
    T.check("shell.consistency: a static rail's height and a static footer's y follow the page and are not compared", not found(tall), str(found(tall)[:1]))
    stuck = cmp_([shell_cell("a", {"contentinfo.pos": "fixed", "contentinfo.rect": [240, 744, 1040, 56]}), shell_cell("b", {"contentinfo.pos": "fixed", "contentinfo.rect": [240, 744, 1040, 56]}),
                  shell_cell("c", {"contentinfo.pos": "fixed", "contentinfo.rect": [240, 700, 1040, 56]})], cfg)
    T.check("shell.consistency: a fixed footer's y is compared", len(found(stuck, "c", "geometry:contentinfo:y")) == 1)
    top = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", {"title.size": 32, "title.weight": 700, "title.color": [0, 0, 0], "title.top": 40})], cfg)
    T.check("shell.consistency: the page title's size, weight, color, and top offset are each compared, and a moved title is information because a breadcrumb moves it",
            sorted(f["key"] for f in found(top, "c")) == ["title:color", "title:size", "title:weight", "title_top:title:top"]
            and [f["tier"] for f in found(top, "c", "title_top:title:top")] == ["INFO"], str(sorted(f["key"] for f in found(top, "c"))))
    T.check("shell.consistency: a font size 0.5px apart or a color 2 per channel apart is within the tolerance",
            not found(cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", {"title.size": 28.5, "title.color": [29, 33, 38]})], cfg)))
    items = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", {"nav.items.height": 40, "nav.items.gap": 8, "nav.items.font_size": 15})], cfg)
    T.check("shell.consistency: nav item height, gap, and font size are each compared",
            sorted(f["key"] for f in found(items, "c")) == ["nav:item_font", "nav:item_gap", "nav:item_height"], str(sorted(f["key"] for f in found(items, "c"))))
    col = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", content_edits={"column": [280, 1192], "width": 912, "gutters": [40, 88]})], cfg)
    T.check("shell.consistency: the content column's left edge and width are compared", [f["key"] for f in found(col, "c")] == ["content:left"] and found(col, "c")[0]["detail"]["dominant"] == 304)
    side = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", content_edits={"column": [304, 900], "width": 596, "aside": True})], cfg)
    T.check("shell.consistency: a column that differs on a route with a side panel is information (aside), not a warning",
            sorted(f["key"] for f in found(side, "c")) == ["aside:content:width"] and found(side, "c")[0]["tier"] == "INFO", str([(f["key"], f["tier"]) for f in found(side, "c")]))
    capped = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", content_edits={"column": [360, 1160], "width": 800, "gutters": [120, 120]})], cfg)
    T.check("shell.consistency: a column that is narrower and centered, the width cap of a form or detail page, is information (cap)",
            sorted((f["key"], f["tier"]) for f in found(capped, "c")) == [("cap:content:left", "INFO"), ("cap:content:width", "INFO")], str(sorted((f["key"], f["tier"]) for f in found(capped, "c"))))
    off = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", content_edits={"column": [304, 1104], "width": 800, "gutters": [64, 176]})], cfg)
    wider = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", content_edits={"column": [280, 1240], "width": 960, "gutters": [40, 40]})], cfg)
    T.check("shell.consistency: a narrower column that is not centered, and a wider one, stay warnings",
            [(f["key"], f["tier"]) for f in found(off, "c")] == [("content:width", "WARN")] and sorted((f["key"], f["tier"]) for f in found(wider, "c")) == [("content:left", "WARN"), ("content:width", "WARN")],
            str([(f["key"], f["tier"]) for f in found(off, "c") + found(wider, "c")]))
    ignore = cmp_(wide[:2] + [shell_cell("c", {"nav.rect": [0, 56, 256, 744]}, drop=("contentinfo",))], cfg, ignore=["footer", "nav"])
    T.check("shell.consistency: --ignore skips a landmark kind, with both its presence and its measurements", not found(ignore), str(found(ignore)[:1]))
    T.check("shell.consistency: every ignore name maps to a landmark kind", set(_rules.SHELL_IGNORE_NAMES.values()) == set(["banner", "nav", "contentinfo", "title", "content"]))
    split = cmp_([shell_cell("a"), shell_cell("b"), shell_cell("c", {"nav.rect": [0, 56, 256, 744]}),
                  shell_cell("a", None, 390, eid="a2"), shell_cell("b", None, 390, eid="b2"), shell_cell("c", None, 390, eid="c2")], cfg)
    T.check("shell.consistency: widths are compared separately, so a difference at 1280 is not reported at 390", [f["detail"]["width"] for f in found(split)] == [1280])
    edge = {"selector": "header.page", "left": -322.5, "right": 333.5}
    wide_edge = [shell_cell("a"), shell_cell("b"), shell_cell("c")]
    for c in wide_edge:
        c["shell"]["edge"] = dict(edge)
    info = cmp_(wide_edge, cfg)
    T.check("shell.consistency: a page header that reaches past the column on every route is one INFO finding per route",
            len(found(info, None, "edge:content", "INFO")) == 3 and not found(info, None, None, "WARN"), str([(f["key"], f["tier"]) for f in found(info)]))
    wide_edge[2]["shell"]["edge"] = {"selector": "header.page", "left": -2.0, "right": 1.5}
    T.check("shell.consistency: the header edge is not reported when one route's header lines up with the column", not found(cmp_(wide_edge, cfg)))
    T.check("shell.consistency: the table lists every landmark by route and width, present or not",
            sorted(r["table"]["contentinfo"]) == ["a", "b", "c"] and r["table"]["contentinfo"]["a"]["1280#light"]["present"] is True
            and gone["table"]["contentinfo"]["c"]["1280#light"]["present"] is False and r["dominant"]["nav"]["1280#light"]["width"] == 240)
    T.check("shell.consistency: the findings' tiers come from the subtiers in defaults.json", cfg.tier("shell.consistency", "missing") == "WARN" and cfg.tier("shell.consistency", "edge") == "INFO"
            and cfg.tier("shell.consistency", "aside") == "INFO")


# ----------------------------------------------------------------------------
# the evidence lock, text drops, and allows without a browser

def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def engine_unit_checks(T, um):
    import _lock
    with tempfile.TemporaryDirectory(prefix="ui-measure-lock-") as tmp:
        root = os.path.join(tmp, "ev")
        code = ("import sys; sys.path.insert(0, %r); import _lock; "
                "print(*[_lock.allocate_ids(%r, 3) for _ in range(20)], '|', *[_lock.allocate_run(%r, 'iteration', 'p%%d' %% _) for _ in range(10)])") % (HERE, root, root)
        procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, universal_newlines=True) for _ in range(4)]
        firsts, runs = [], []
        for proc in procs:
            out, _err = proc.communicate(timeout=60)
            ids, _bar, names = out.partition("|")
            firsts += [int(x) for x in ids.split()]
            runs += names.split()
        ids = sorted(n for f in firsts for n in range(f, f + 3))
        T.check("lock: four processes reserve 240 evidence numbers in blocks of 3 and no number repeats", ids == list(range(1, 241)), "%d ids, %d distinct" % (len(ids), len(set(ids))))
        T.check("lock: four processes create 40 run folders and no run number repeats", sorted(int(r[:3]) for r in runs) == list(range(1, 41)) and len(os.listdir(root)) >= 40,
                str(sorted(runs)[:4]))
        T.check("lock: the counter file records the last number handed out", int(open(os.path.join(root, ".ids")).read()) == 240)
        top = os.path.join(root, "041-iteration")
        os.makedirs(top)
        with open(os.path.join(top, "E500.json"), "w") as handle:
            handle.write("{}")
        T.check("lock: a number already on disk beats a lower counter, so an older evidence root keeps counting", _lock.allocate_ids(root, 1) == 501)
        header = {"tool": {"name": "ui_measure", "version": 1}}
        w1, w2 = um.RunWriter(root, "041-iteration", header), um.RunWriter(root, "041-iteration", header)
        w1.reserve(3)
        w2.reserve(3)
        mixed = [w1.alloc(), w2.alloc(), w1.alloc(), w2.alloc(), w1.alloc(), w2.alloc()]
        a, b = [int(x[1:]) for x in mixed[0::2]], [int(x[1:]) for x in mixed[1::2]]
        T.check("lock: each run reserves a block, so its ids stay consecutive when two runs write at once", a == list(range(a[0], a[0] + 3)) and b == list(range(b[0], b[0] + 3)) and not set(a) & set(b), str(mixed))
        try:
            with _lock.EvidenceLock(root):
                try:
                    _lock.EvidenceLock(root, timeout_s=0.2).__enter__()
                    blocked = False
                except _lock.LockTimeout:
                    blocked = True
        finally:
            pass
        with _lock.EvidenceLock(root, timeout_s=0.5):
            free = True
        T.check("lock: a second holder waits and then times out; the lock is free again after the first one leaves", blocked and free)
        try:
            with _lock.EvidenceLock(root):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        with _lock.EvidenceLock(root, timeout_s=0.5):
            T.check("lock: a lock is released when its holder raises", True)
        # the mkdir fallback and its stale-lock recovery
        mroot = os.path.join(tmp, "mk")
        os.makedirs(os.path.join(mroot, ".lock.d"))
        with open(os.path.join(mroot, ".lock.d", "owner"), "w") as handle:
            handle.write("%d 1" % _dead_pid())
        t0 = time.monotonic()
        with _lock.EvidenceLock(mroot, use_flock=False, timeout_s=2):
            held = os.path.isdir(os.path.join(mroot, ".lock.d"))
        T.check("lock: the mkdir fallback recovers a lock whose owner process is gone", held and time.monotonic() - t0 < 1.5 and not os.path.exists(os.path.join(mroot, ".lock.d")))
        with _lock.EvidenceLock(mroot, use_flock=False):
            try:
                _lock.EvidenceLock(mroot, use_flock=False, timeout_s=0.2).__enter__()
                waited = False
            except _lock.LockTimeout:
                waited = True
        T.check("lock: the mkdir fallback keeps out a second holder while the owner is alive", waited)

    # what became of each dropped text
    be = {"rules": {"slop.eyebrow": {"findings": [{"detail": {"texts": ["NEW RELEASE"]}, "examples": [{"text": "NEW RELEASE"}]}]},
                    "slop.generic_action": {"findings": [{"detail": {"texts": ["Learn more..."]}, "examples": []}]}}}
    fe = {"kind": "page", "cite": "E02@1280:default#light",
          "text_seq": [{"k": "p", "t": "Reviewable specs for product teams, from the first draft to approval, with every comment and decision kept in one place."}]}
    other = {"kind": "page", "cite": "E03@390:default#light", "text_seq": [{"k": "p", "t": "Moved line of copy"}]}
    text = {"dropped": ["NEW RELEASE", "Learn more about the changelog", "Reviewable specs for product teams", "Moved line of copy", "Gone line", "Say"],
            "changed": [{"from": "Old words", "to": "New words"}]}
    got = dict((d["text"], d) for d in um.classify_drops(text, be, fe, [fe, other]))
    T.check("compare: a text a slop rule flagged in the baseline is classified as slop and names the rule", got["NEW RELEASE"]["class"] == "slop" and got["NEW RELEASE"]["by"] == "slop.eyebrow")
    T.check("compare: a flagged text cut at 80 characters still matches the longer string it came from", got["Learn more about the changelog"]["class"] == "slop")
    T.check("compare: a text the final page still says inside a longer string is classified as merged", got["Reviewable specs for product teams"]["class"] == "merged")
    T.check("compare: a text another capture of the final run has is classified as moved and names its cite", got["Moved line of copy"]["class"] == "moved" and got["Moved line of copy"]["by"] == "E03@390:default#light")
    T.check("compare: an edit in place is classified as replaced", got["Old words"]["class"] == "replaced" and got["Old words"]["by"] == "New words")
    T.check("compare: a text nothing explains, and a short one that only appears inside other words, are unclassified",
            got["Gone line"]["class"] == "unclassified" and got["Say"]["class"] == "unclassified")

    # allows without a browser
    d = json.load(open(os.path.join(HERE, "defaults.json"), encoding="utf-8"))
    cfg = _rules.Cfg(_rules.deep_merge(dict((k, v) for k, v in d.items() if k != "profiles"), d["profiles"]["product"]))
    nodes = [{"i": 0, "p": -1, "t": "body", "s": "body", "d": "block", "r": [0, 0, 400, 300]},
             {"i": 1, "p": 0, "t": "div", "s": "div.a", "d": "block", "r": [10, 10, 100, 40], "ua": "align.near_miss: legacy toolbar offset; slop.*: marketing hero, approved"},
             {"i": 2, "p": 1, "t": "p", "s": "p", "d": "block", "r": [12, 12, 50, 20]},
             {"i": 3, "p": 0, "t": "div", "s": "div.b", "d": "block", "r": [10, 100, 100, 40], "ua": "align.near_miss"},
             {"i": 4, "p": 0, "t": "div", "s": "div.c", "d": "block", "r": [10, 200, 100, 40]}]
    c = _rules.Ctx({"facts": {}, "nodes": nodes}, {"width": 400, "height": 300, "dpr": 1, "theme": "light"}, cfg, {})
    T.check("allows: one attribute holds several entries, split at the next rule id, so a reason may contain a semicolon-free comma",
            [e[0] for e in _rules.allow_entries(c)[1]] == ["align.near_miss", "slop.*"] and _rules.allow_entries(c)[1][0][1] == "legacy toolbar offset")

    def f(node, tier="WARN", count=1):
        return {"rule": "align.near_miss", "tier": tier, "message": "m", "count": count, "key": "k", "examples": [{"selector": c.path(node), "rect": nodes[node]["r"]}]}
    out = _rules.apply_allows(c, "align.near_miss", [f(1), f(2), f(3), f(4), f(1, "FAIL"), f(1, count=3)])
    T.check("allows: a WARN finding whose element has an allow with a reason becomes INFO and keeps the reason", out[0]["tier"] == "INFO" and out[0]["allowed"] == {"reason": "legacy toolbar offset"})
    T.check("allows: an allow on an ancestor covers the element inside it", out[1]["tier"] == "INFO")
    T.check("allows: a bare allow with no reason demotes nothing", out[2]["tier"] == "WARN" and "allowed" not in out[2])
    T.check("allows: an element with no allow is left alone", out[3]["tier"] == "WARN")
    T.check("allows: a FAIL finding is never demoted", out[4]["tier"] == "FAIL")
    T.check("allows: a finding that lists fewer elements than it counts is left alone, because the rest are not covered", out[5]["tier"] == "WARN")
    T.check("allows: slop.* covers every slop rule and nothing else", _rules.allow_reason(c, "slop.glow", None, 2) == "marketing hero, approved" and _rules.allow_reason(c, "grid.size", None, 2) is None)
    _checked, bad = _rules.r_allow_reason(c)
    T.check("allows: allow.reason names the bare allow and only that one", len(bad) == 1 and bad[0]["tier"] == "WARN" and "div.b" in bad[0]["message"] and c.details["allow.reason"]["applied"] >= 2, str(bad)[:200])


# ----------------------------------------------------------------------------
# browser checks

def planted_checks(T, um, root, exp):
    case = exp["cases"]["bad"]
    code, out, err = cli(um, ["capture", page(case["page"]), "--out", root, "--label", "bad"] + case["args"])
    T.check("bad.html exits 1 because FAIL findings remain", code == 1, "exit %s %s" % (code, err[-300:]))
    run = latest_run(root)
    manifest, evs = load_run(run)
    for item in case["present"]:
        hits = findings(evs, item["rule"], item.get("tier"), item.get("width"), item.get("theme"), item.get("contains"))
        T.check("planted: %s found by %s" % (item["defect"], item["rule"]), bool(hits), "no %s %s finding" % (item.get("tier"), item["rule"]))
    for item in case["absent"]:
        hits = findings(evs, item["rule"], None, item.get("width"), item.get("theme"))
        T.check("absent: %s%s%s" % (item["rule"], (" at %s" % item["width"]) if item.get("width") else "", (" in %s" % item["theme"]) if item.get("theme") else ""),
                not hits, "unexpected %s" % (hits[0][1]["message"] if hits else ""))
    T.check("bad.html has at least %d planted defects found" % case["min_total_planted"],
            sum(1 for i in case["present"] if findings(evs, i["rule"], i.get("tier"), i.get("width"), i.get("theme"), i.get("contains"))) >= case["min_total_planted"])
    return run, evs


def run(args, um):
    t0 = time.monotonic()
    T = Tester()
    with open(os.path.join(PAGES, "expected.json"), encoding="utf-8") as handle:
        exp = json.load(handle)
    with open(EVIDENCE_MD, encoding="utf-8") as handle:
        defs = parse_blocks(handle.read())
    T.check("EVIDENCE.md has every schema block", set(defs) >= set(["finding", "rule", "viewport", "tiers", "facts", "counts", "tokens", "network", "evidence", "entry", "manifest", "compare", "config", "content", "shell", "shelltable"]),
            "found %s" % sorted(defs))
    unit_checks(T, exp)
    engine_unit_checks(T, um)

    keep = getattr(args, "keep", False)
    tmp = None if keep else tempfile.TemporaryDirectory(prefix="ui-measure-selftest-")
    root = tempfile.mkdtemp(prefix="ui-measure-selftest-") if keep else tmp.name
    try:
        sys.stdout.write("Evidence for this run: %s\n" % root)
        browser_checks(T, um, root, exp, defs)
    finally:
        if tmp is not None:
            tmp.cleanup()
        else:
            sys.stdout.write("Kept %s\n" % root)
    elapsed = time.monotonic() - t0
    sys.stdout.write("\n%d passed, %d failed in %.1fs (target %ds)\n" % (T.passed, T.failed, elapsed, TIME_BUDGET_S))
    if T.failures:
        sys.stdout.write("Failed: %s\n" % "; ".join(T.failures[:12]))
    return 0 if T.failed == 0 else 1


def browser_checks(T, um, root, exp, defs):
    cases = exp["cases"]

    # 1. a clean page: no FAIL, no WARN, tooling ignored, in light and dark
    good = cases["good"]
    code, out, err = cli(um, ["capture", page(good["page"]), "--out", root, "--label", "good"] + good["args"])
    run_dir = latest_run(root)
    manifest, evs = load_run(run_dir)
    T.check("good.html exits 0", code == 0, "exit %s %s" % (code, err[-200:]))
    fails = sum(e["summary"]["FAIL"] for e in evs)
    warns = sum(e["summary"]["WARN"] for e in evs)
    first_warn = ""
    for ev in evs:
        for rid, r in ev["rules"].items():
            for f in r["findings"]:
                if f["tier"] in ("FAIL", "WARN"):
                    first_warn = first_warn or "%s %s" % (rid, f["message"][:100])
    T.check("good.html has no FAIL findings in %d captures" % len(evs), fails <= good["max_fail"], first_warn)
    T.check("good.html has no WARN findings in %d captures" % len(evs), warns <= good["max_warn"], first_warn)
    groups_detail = evs[0]["rules"]["component.conformance"].get("detail", {}).get("groups", [])
    T.check("component.conformance lists every repeated group with its padding and gaps, even when all agree",
            len(groups_detail) >= 3 and all(g["deviants"] == 0 for g in groups_detail) and any("padding-top" in g["mode"] for g in groups_detail), str(groups_detail[:1]))
    T.check("good.html: tooling elements were ignored", all(e["facts"]["ignored"] >= good["min_ignored"] for e in evs), str([e["facts"]["ignored"] for e in evs]))
    seq_text = " ".join(x["t"].lower() for e in evs for x in e["text_seq"])
    T.check("good.html: ignored text is absent from text_seq", not any(w in seq_text.split() for w in good["forbid_text"]))
    problems = validate_run(run_dir, defs)
    T.check("good.html evidence matches the EVIDENCE.md schema and invariants", not problems, "; ".join(problems[:3]))
    sq = evs[0].get("squint")
    sq_ok = False
    if sq:
        sq_bytes = open(os.path.join(run_dir, sq["file"]), "rb").read()
        sq_w, sq_h, sq_rgb = _png.decode(sq_bytes)
        sq_ok = hashlib.sha256(sq_bytes).hexdigest() == sq["sha256"] and sq["size"] == [sq_w, sq_h] and 100 <= sq_w <= 130 and sq_h > sq_w
    T.check("--squint writes a small blurred copy of the screenshot", sq_ok, str(sq))
    T.check("every capture finished within 10s", all(e["timing_ms"]["total"] < 10000 for e in evs), str([e["timing_ms"]["total"] for e in evs]))
    good_run = run_dir
    good_evs = evs

    # 2. the planted defects, in two widths and two themes
    bad_run, bad_evs = planted_checks(T, um, root, exp)
    problems = validate_run(bad_run, defs)
    T.check("bad.html evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))
    summary_lines = open(os.path.join(bad_run, "summary.md"), encoding="utf-8").read().split("\n")
    T.check("summary.md is at most 60 lines", len(summary_lines) <= 61, "%d lines" % len(summary_lines))

    # 3. determinism: the same capture twice gives the same findings and the same pixels
    code, _o, _e = cli(um, ["capture", page("bad.html"), "--out", root, "--widths", "390", "--label", "again"])
    again_manifest, again = load_run(latest_run(root))
    first = [e for e in bad_evs if e["viewport"]["width"] == 390 and e["theme"] == "light"][0]
    strip = lambda ev: json.dumps(ev["rules"], sort_keys=True)
    T.check("capture is deterministic: same findings", strip(first) == strip(again[0]))
    T.check("capture is deterministic: same screenshot hash", first["sha256"] == again[0]["sha256"])

    # 4. themed page: color syntaxes the browser must normalise
    t = cases["themed"]
    code, out, err = cli(um, ["capture", page(t["page"]), "--out", root, "--label", "themed"] + t["args"])
    t_run = latest_run(root)
    _m, t_evs = load_run(t_run)
    for item in t["present"]:
        T.check("themed: %s" % item["defect"], bool(findings(t_evs, item["rule"], item["tier"], None, item["theme"], item["contains"])))
    for item in t["absent"]:
        T.check("themed: no %s in %s" % (item["rule"], item["theme"]), not findings(t_evs, item["rule"], None, None, item["theme"]))
    for theme, lch in t["oklch"].items():
        want = oklch_to_rgb(*lch)
        ev = [e for e in t_evs if e["theme"] == theme][0]
        got = _rules.hex_to_rgb(list(ev["tokens"]["text_colors"])[0])
        T.check("oklch(%s) in %s normalises to the same sRGB as an independent conversion" % (" ".join(str(v) for v in lch), theme),
                all(abs(a - b) <= t["channel_tolerance"] for a, b in zip(want, got)), "want %s got %s" % (want, got))
    light = [e for e in t_evs if e["theme"] == "light"][0]
    T.check("color-mix() page background resolves to the expected color", all(abs(a - b) <= 1 for a, b in zip(light["facts"]["page_bg"], t["page_bg_light"])), str(light["facts"]["page_bg"]))
    T.check("rendered_theme follows the requested scheme", [e["facts"]["rendered_theme"] for e in t_evs] == ["light", "dark"])
    problems = validate_run(t_run, defs)
    T.check("themed evidence matches the schema", not problems, "; ".join(problems[:3]))

    # 4b. edge cases that look like defects but are not: modal over the page, clipped text, browser defaults
    e = cases["edge"]
    code, out, err = cli(um, ["capture", page(e["page"]), "--out", root, "--label", "edge"] + e["args"])
    e_run = latest_run(root)
    _m, e_evs = load_run(e_run)
    T.check("edge cases: no FAIL findings", code == 0 and sum(ev["summary"]["FAIL"] for ev in e_evs) <= e["max_fail"],
            "exit %s %s" % (code, [f["message"][:80] for ev in e_evs for r in ev["rules"].values() for f in r["findings"] if f["tier"] == "FAIL"]))
    for item in e["present"]:
        T.check("edge cases: %s" % item["why"], bool(findings(e_evs, item["rule"], item.get("tier"), None, None, item.get("contains"))))
    for item in e["absent"]:
        hits = findings(e_evs, item["rule"], None, None, None, item.get("contains"))
        T.check("edge cases: %s" % item["why"], not hits, hits[0][1]["message"][:100] if hits else "")
    T.check("edge cases: Tab stays inside the open modal dialog", e_evs[0]["counts"]["tab_stops"] == e["tab_stops"] and e_evs[0]["rules"]["focus.visible"]["status"] == "pass"
            and e_evs[0]["facts"]["modal_open"] is True, "%s %s" % (e_evs[0]["counts"]["tab_stops"], e_evs[0]["rules"]["focus.visible"]["status"]))
    problems = validate_run(e_run, defs)
    T.check("edge case evidence matches the schema", not problems, "; ".join(problems[:3]))

    # 5. requests: remote hosts blocked, writes answered locally
    b = cases["blocked"]
    code, out, err = cli(um, ["capture", page(b["page"]), "--out", root, "--label", "blocked"] + b["args"])
    b_run = latest_run(root)
    b_manifest, b_evs = load_run(b_run)
    T.check("blocked.html: remote requests were blocked and listed", b_manifest["blocked_requests"] >= b["min_blocked"] and b["blocked_host"] in b_manifest["blocked_hosts"],
            str(b_manifest["blocked_hosts"]))
    T.check("blocked.html: writes were answered locally", b_manifest["stubbed_writes"] >= b["min_stubbed"], str(b_manifest["stubbed_writes"]))
    T.check("blocked.html: blocking is not a FAIL", sum(e["summary"]["FAIL"] for e in b_evs) <= b["max_fail"] and code == 0,
            "exit %s %s" % (code, [f["message"][:80] for e in b_evs for r in e["rules"].values() for f in r["findings"] if f["tier"] == "FAIL"]))
    T.check("blocked.html: the blocked image is INFO, not broken", bool(findings(b_evs, "media.images", "INFO", None, None, "blocked")))
    code, out, err = cli(um, ["capture", "http://example.com/", "--out", root])
    T.check("a remote URL is refused without --allow-remote (exit 2)", code == 2 and "refusing" in err, "exit %s %s" % (code, err[:120]))
    code, out, err = cli(um, ["capture", os.path.join(root, "no-such-page.html"), "--out", root])
    T.check("a missing page is a tool error (exit 2)", code == 2)
    code, out, err = cli(um, ["capture", page("seed.html"), "--out", root, "--storage", "novalue"])
    T.check("a malformed --storage is a tool error that does not echo the argument", code == 2 and "novalue" not in err, err[:120])

    # 6. state matrix with network faults, steps, pseudo-states, and a contact sheet
    s = cases["states"]
    code, out, err = cli(um, ["states", "--spec", page(s["spec"]), "--out", root] + s["args"])
    s_run = latest_run(root)
    s_manifest, s_evs = load_run(s_run)
    pages_by_state = dict((e["state"], e) for e in s_evs if e["kind"] == "page")
    for sid in s["confirmed"]:
        T.check("state %s is confirmed" % sid, sid in pages_by_state and pages_by_state[sid]["state_confirmed"] is True)
    for sid in s["unconfirmed"]:
        ev = pages_by_state.get(sid)
        T.check("state %s is not rendered, so it is unconfirmed and fails" % sid, bool(ev) and ev["state_confirmed"] is False and ev["rules"]["state.confirmed"]["status"] == "fail")
    T.check("states run exits 1 for the unconfirmed state", code == 1)
    err_ev = pages_by_state["error"]
    T.check("error state: console errors from the forced fault are INFO, not FAIL",
            err_ev["rules"]["console.errors"]["status"] == "info" and not [f for f in err_ev["rules"]["console.errors"]["findings"] if f["tier"] == "FAIL"])
    T.check("loading state: the hung request is not counted as a failure", pages_by_state["loading"]["network"]["hung"] == 1 and not pages_by_state["loading"]["rules"]["network.failed"]["findings"])
    T.check("saved state: the write was answered locally", s_manifest["stubbed_writes"] >= s["min_stubbed_writes"] and "Saved" in " ".join(x["t"] for x in pages_by_state["saved"]["text_seq"]))
    T.check("typed state: type and press steps ran and produced the validation message",
            pages_by_state["typed"]["state_confirmed"] and pages_by_state["typed"]["state_detail"]["steps"] == 2
            and "Name is too short" in " ".join(x["t"] for x in pages_by_state["typed"]["text_seq"]))
    T.check("opened state: Tab reaches every control after a click", pages_by_state["opened"]["rules"]["focus.order"]["status"] == "pass" and pages_by_state["opened"]["counts"]["tab_stops"] == 3)
    pseudo = [e for e in s_evs if e["kind"] == "pseudo"]
    hover = [e for e in pseudo if e["state"] == "pseudo-hover" and e["pseudo"]["selector"] == "#toggle"]
    T.check("pseudo hover changes the button and is saved as a crop", bool(hover) and hover[0]["pseudo"]["visible_change"] and hover[0]["png"])
    focus = [e for e in pseudo if e["state"] == "pseudo-focus-visible"]
    T.check("pseudo focus-visible shows a ring", bool(focus) and focus[0]["rules"]["focus.visible"]["status"] == "pass")
    missing = [e for e in pseudo if e["pseudo"]["selector"] == "#missing"]
    T.check("a pseudo selector that matches nothing is unconfirmed", bool(missing) and missing[0]["state_confirmed"] is False and missing[0]["png"] is None)
    if hover:
        png = open(os.path.join(s_run, hover[0]["png"]), "rb").read()
        width, height, rgb = _png.decode(png)
        T.check("a Chrome screenshot decodes and the hover fill is #e5ecff", _png.pixel(rgb, width, 20, height // 2) == (229, 236, 255), str(_png.pixel(rgb, width, 20, height // 2)))
    sheet = [a for a in s_manifest["artifacts"] if a["kind"] == "contact-sheet"]
    T.check("the contact sheet is a png covering every capture", bool(sheet) and _png.read_size(open(os.path.join(s_run, sheet[0]["file"]), "rb").read())[0] > 300 and len(sheet[0]["cells"]) == len(s["confirmed"]) + len(s["unconfirmed"]))
    problems = validate_run(s_run, defs)
    T.check("state evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))

    # 7. storage seeding, never leaking values
    sd = cases["seed"]
    code, out, err = cli(um, ["capture", page(sd["page"]), "--out", root, "--widths", "800", "--storage", "seed=ok", "--storage", "token=" + sd["value_secret"],
                              "--session-storage", "sess=1", "--ready", "text:Seed: ok", "--label", "seeded"])
    sd_run = latest_run(root)
    sd_manifest, sd_evs = load_run(sd_run)
    T.check("seeded storage is visible to the page before its scripts run", code == 0 and sd_evs[0]["text_seq"][0]["t"] == sd["heading_seeded"], str(sd_evs[0]["text_seq"]))
    T.check("facts.storage_keys lists key names only", sd_evs[0]["facts"]["storage_keys"] == {"local": ["seed", "token"], "session": ["sess"]}
            and sd_evs[0]["facts"]["storage_present"] == {"local": ["seed", "token"], "session": ["sess"]})
    leaked = []
    for dirpath, _dirs, files in os.walk(sd_run):
        for name in files:
            if name.endswith((".json", ".md")) and sd["value_secret"] in open(os.path.join(dirpath, name), encoding="utf-8").read():
                leaked.append(name)
    T.check("the seeded secret value appears in no evidence file", not leaked, ", ".join(leaked))
    T.check("the manifest and summary mention the seeding by name", sd_manifest["storage_keys"]["local"] == ["seed", "token"] and "token" in open(os.path.join(sd_run, "summary.md"), encoding="utf-8").read())
    code, out, err = cli(um, ["capture", "file://" + page(sd["page"]) + "?token=" + sd["url_secret"] + "#" + sd["url_secret"], "--out", root, "--widths", "800", "--label", "urlsecret"])
    u_run = latest_run(root)
    u_manifest, u_evs = load_run(u_run)
    leaked_url = []
    for dirpath, _dirs, files in os.walk(u_run):
        for name in files:
            if name.endswith((".json", ".md")) and sd["url_secret"] in open(os.path.join(dirpath, name), encoding="utf-8").read():
                leaked_url.append(name)
    T.check("a token in the page URL query or fragment is redacted from every evidence file", code == 0 and not leaked_url and "token=~" in u_manifest["url"] and u_manifest["url"].endswith("#~"),
            "%s %s" % (u_manifest["url"][-40:], leaked_url))
    T.check("redact_url keeps scheme, host, path, and query keys only", _policy.redact_url("https://user:pw@example.com/a/b?x=1&y=2#frag") == "https://~@example.com/a/b?x=~&y=~#~")
    spec_path = os.path.join(root, "seed-spec.json")
    with open(spec_path, "w", encoding="utf-8") as handle:
        json.dump({"screen": "seed", "url": page(sd["page"]), "storage": {"seed": "ok"}, "session_storage": {"sess": 1},
                   "states": {"seeded": {"expect": "text:Seed: ok"}}}, handle)
    code, out, err = cli(um, ["states", "--spec", spec_path, "--out", root, "--widths", "800"])
    sp_manifest, sp_evs = load_run(latest_run(root))
    T.check("a states spec can seed storage and expect the seeded text", code == 0 and sp_evs[0]["state_confirmed"] and sp_evs[0]["text_seq"][0]["t"] == sd["heading_seeded"], str(sp_evs[0]["text_seq"]))
    code, out, err = cli(um, ["capture", page(sd["page"]), "--out", root, "--widths", "800", "--label", "unseeded"])
    _m, un_evs = load_run(latest_run(root))
    T.check("without seeding the same page shows its missing state", un_evs[0]["text_seq"][0]["t"] == sd["heading_missing"] and un_evs[0]["facts"]["storage_keys"] == {"local": [], "session": []})
    cfg = um.load_config(root)[0]
    eng = _engine.Engine(cfg)
    try:
        view = _engine.parse_viewport("800", cfg)
        cell = eng.capture("file://" + page(sd["page"]), view, "light", storage={"local": {"seed": "ok"}, "session": {"sess": "1"}, "origin": "http://127.0.0.1:1"})
        heading = [x["t"] for x in cell["raw"]["texts"]][:1]
    finally:
        eng.close()
    T.check("seeding is skipped when the page is not on the seeded origin", heading == [sd["heading_missing"]] and cell["storage"]["present"] == {"local": [], "session": []}, str(heading))

    # 7b. a custom height and pixel ratio; --allow-remote leaves the dead proxy off but still loads a local page
    code, out, err = cli(um, ["capture", page("good.html"), "--out", root, "--widths", "390x700@2", "--allow-remote", "--label", "dpr"])
    d_manifest, d_evs = load_run(latest_run(root))
    d = d_evs[0]
    T.check("390x700@2 gives a 780 px wide screenshot, a 700 px tall viewport, and a true layout width",
            d["png_size"][0] == 780 and d["viewport"]["height"] == 700 and d["facts"]["innerHeight"] == 700 and d["facts"]["innerWidth"] == 390 and d["facts"]["dpr"] == 2
            and d["rules"]["viewport.truth"]["status"] == "pass", "%s %s" % (d["png_size"], d["rules"]["viewport.truth"]["status"]))
    T.check("--allow-remote is recorded in the manifest", d_manifest["allow_remote"] is True and d_manifest["url_kind"] == "file")
    T.check("a taller device pixel ratio keeps the page clean", d["summary"]["FAIL"] == 0 and d["summary"]["WARN"] == 0)

    # 8. profiles and project overrides
    cfg_root = os.path.join(root, "projectcfg")
    os.makedirs(cfg_root)
    with open(os.path.join(cfg_root, "config.json"), "w", encoding="utf-8") as handle:
        json.dump({"schema": 1, "profile": "dense", "overrides": {"thresholds": {"contrast.text": {"normal": 3.0}}}}, handle)
    code, out, err = cli(um, ["capture", page("bad.html"), "--out", cfg_root, "--widths", "390", "--no-focus"])
    c_manifest, c_evs = load_run(latest_run(cfg_root))
    T.check("--no-focus skips the Tab walk and says so", c_manifest["focus_walk"] is False and c_evs[0]["counts"]["tab_stops"] is None and c_evs[0]["rules"]["focus.visible"]["checked"] == 0)
    T.check("a project override lowers the contrast limit, so 3.19:1 passes", not findings(c_evs, "contrast.text"))
    T.check("the dense profile drops the 44 px target warning", not findings(c_evs, "target.size", "WARN", None, None, "aim for 44") and bool(findings(c_evs, "target.size", "FAIL")))

    # 9. init reads tokens; a five pixel project gets the different-base note
    init_root = os.path.join(root, "initcfg")
    code, out, err = cli(um, ["init", page("good.html"), "--out", init_root])
    with open(os.path.join(init_root, "config.json"), encoding="utf-8") as handle:
        conf = json.load(handle)
    problems = []
    check_shape(conf, defs["config"], defs, "config", problems)
    T.check("init reads spacing, radius, and font-size tokens from the page", conf["tokens"] == {"spacing": [8, 12, 16, 24, 32, 48], "radius": [8], "font_size": [14, 16, 20, 28]} and not problems,
            "%s %s" % (conf["tokens"], problems[:2]))
    T.check("init: a clean 4px page is not flagged", conf["off_grid_share"] == 0 and "different base" not in out)
    code, out, err = cli(um, ["init", page("fivegrid.html"), "--out", os.path.join(root, "initcfg5")])
    with open(os.path.join(root, "initcfg5", "config.json"), encoding="utf-8") as handle:
        conf5 = json.load(handle)
    T.check("init: more than 40% off the 4px grid prints the different-base note", "may use a different base" in out and conf5["suggested_base"] == 5, "%s %s" % (conf5["off_grid_share"], conf5["suggested_base"]))

    # 10. register a foreign screenshot
    shot = os.path.join(good_run, good_evs[0]["png"])
    reg_root = os.path.join(root, "registered")
    code, out, err = cli(um, ["register", shot, "--viewport", "390x844@1", "--out", reg_root])
    r_manifest, r_evs = load_run(latest_run(reg_root))
    T.check("a matching screenshot registers as unmeasured evidence", code == 0 and r_evs[0]["measured"] is False and r_evs[0]["kind"] == "foreign" and r_evs[0]["sha256"] == sha256_file(shot))
    T.check("registered evidence matches the schema", not validate_run(latest_run(reg_root), defs))
    code, out, err = cli(um, ["register", shot, "--viewport", "400x844@1", "--out", reg_root])
    T.check("a screenshot whose width does not match its viewport fails viewport.truth", code == 1)

    # 10b. an app shell that clips the document: the main scroll region scrolls sideways
    shell_head = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Shell</title>'
                  '<style>html,body{margin:0;height:100%;overflow:hidden}body{font:16px/1.5 system-ui}.shell{display:flex;height:100vh}.rail{width:64px;background:#eee}'
                  'main{flex:1;overflow:auto;padding:16px}.bar{width:600px;display:flex;gap:8px}table{width:900px;border-collapse:collapse}td,th{padding:8px;border:1px solid #ccc}</style></head><body><div class="shell"><nav class="rail" aria-label="Main">Menu</nav><main>')
    shell_bad = shell_head + '<h1>Region</h1><div class="bar"><input aria-label="Search" style="flex:1;height:40px"><button type="button" style="height:40px">Filter</button></div><p>Body text.</p></main></div></body></html>'
    shell_ok = shell_head + '<h1>Region</h1><div style="overflow-x:auto"><table><tr><th>Name</th><th>Owner</th><th>Status</th></tr><tr><td>Alpha</td><td>Sam</td><td>Open</td></tr></table></div></main></div></body></html>'
    for label, markup, expect_region in (("region-bad", shell_bad, True), ("region-ok", shell_ok, False)):
        path = os.path.join(root, label + ".html")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(markup)
        out_dir = os.path.join(root, label)
        code, out, err = cli(um, ["capture", path, "--out", out_dir, "--widths", "390", "--no-focus"])
        r_man, r_evs = load_run(latest_run(out_dir))
        found = [f for f in r_evs[0]["rules"]["overflow.page"]["findings"] if "main scroll region" in f["message"]]
        T.check("overflow.page: a main scroll region that scrolls sideways is %s" % ("reported as a WARN" if expect_region else "not reported when only an inner table scrolls"),
                (len(found) == 1 and found[0]["tier"] == "WARN") if expect_region else not found, "%s %s" % (code, [f["message"] for f in r_evs[0]["rules"]["overflow.page"]["findings"]]))

    # 11. compare: dropped, new, changed, and reordered text; anchors that moved
    with open(page("good.html"), encoding="utf-8") as handle:
        html = handle.read()
    variant = html.replace("<h1>Spec library</h1>", "<h1>Spec library v2</h1>") \
                  .replace('<p class="lead">Browse, review, and ship specs.', '<p class="lead">Browse and ship specs.') \
                  .replace("<footer class=\"wrap\"><p>Spec Library keeps specs reviewable.</p></footer>", "<footer class=\"wrap\"><p>Spec Library keeps specs reviewable.</p><p>New footer note.</p></footer>") \
                  .replace('<a href="#specs">Specs</a>\n      <a href="#start">Start</a>\n      <a href="#usage">Usage</a>', '<a href="#usage">Usage</a>\n      <a href="#start">Start</a>\n      <a href="#specs">Specs</a>') \
                  .replace(".lead { max-width: 65ch;", ".lead { padding-top: 32px; max-width: 65ch;") \
                  .replace('<li class="row"><a href="#s12">Billing view</a><span class="meta">Updated 1 month ago</span></li>', "")
    v_path = os.path.join(root, "good-variant.html")
    with open(v_path, "w", encoding="utf-8") as handle:
        handle.write(variant)
    code, out, err = cli(um, ["capture", v_path, "--out", root, "--widths", "1280", "--phase", "final", "--label", "variant"])
    v_run = latest_run(root)
    code, out, err = cli(um, ["compare", os.path.basename(good_run), os.path.basename(v_run), "--out", root])
    with open(os.path.join(v_run, "compare-%s.json" % os.path.basename(good_run)), encoding="utf-8") as handle:
        cmp_json = json.load(handle)
    problems = []
    check_shape(cmp_json, defs["compare"], defs, "compare", problems)
    summ = cmp_json["summary"]
    T.check("compare output matches the schema", not problems, "; ".join(problems[:3]))
    T.check("compare finds dropped, new, changed, and reordered text", summ["dropped"] >= 1 and summ["new"] >= 1 and summ["changed"] >= 1 and summ["reordered"] >= 1, str(summ))
    T.check("compare reports anchors that moved", summ["anchors_moved"] >= 1, str(summ))
    T.check("compare leaves unpaired captures listed", len(cmp_json["unpaired"]["baseline_only"]) >= 1)

    # 12. scrollbars: classic reports honestly, emulated reserves a gutter, compare attributes the shift
    code, out, err = cli(um, ["capture", page("good.html"), "--out", root, "--widths", "390", "--scrollbars", "classic", "--label", "classic"])
    cl_manifest, cl_evs = load_run(latest_run(root))
    native = cl_manifest["scrollbars"]["native_px"]
    if native == 0:
        T.check("classic scrollbars on a platform with overlay scrollbars are reported as INFO, not claimed", cl_evs[0]["facts"]["scrollbar_px"] == 0 and bool(findings(cl_evs, "viewport.truth", "INFO", None, None, "overlay")))
    else:
        T.check("classic scrollbars take layout space on this platform", cl_evs[0]["facts"]["scrollbar_px"] == native and cl_evs[0]["facts"]["clientWidth"] == 390 - native)
    short_html = "<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>Short</title><style>body{margin:0;font:16px/1.5 system-ui,sans-serif}.wrap{max-width:960px;margin:0 auto;padding:24px}h1{font-size:28px;line-height:1.25;margin:0}</style></head><body><main id=top class=wrap><h1>Spec library</h1></main></body></html>"
    short_path = os.path.join(root, "short.html")
    with open(short_path, "w", encoding="utf-8") as handle:
        handle.write(short_html)
    code, out, err = cli(um, ["capture", short_path, "--out", root, "--widths", "1280", "--scrollbars", "emulated", "--label", "short", "--phase", "baseline"])
    short_run = latest_run(root)
    code, out, err = cli(um, ["capture", page("good.html"), "--out", root, "--widths", "1280", "--scrollbars", "emulated", "--label", "long", "--phase", "final"])
    long_run = latest_run(root)
    _m, long_evs = load_run(long_run)
    _m, short_evs = load_run(short_run)
    T.check("emulated scrollbars reserve a 15 px gutter on a long page and none on a short one", long_evs[0]["facts"]["scrollbar_px"] == 15 and short_evs[0]["facts"]["scrollbar_px"] == 0,
            "%s %s" % (long_evs[0]["facts"]["scrollbar_px"], short_evs[0]["facts"]["scrollbar_px"]))
    code, out, err = cli(um, ["compare", os.path.basename(short_run), os.path.basename(long_run), "--out", root, "--scrollbars", "emulated"])
    with open(os.path.join(long_run, "compare-%s.json" % os.path.basename(short_run)), encoding="utf-8") as handle:
        sb_json = json.load(handle)
    causes = [m.get("cause", "") for p in sb_json["pairs"] for m in p["anchors_moved"]]
    T.check("compare attributes a scrollbar-induced shift to the scrollbar", any("scrollbar" in c for c in causes), str(causes[:3]))
    code, out, err = cli(um, ["compare", os.path.basename(short_run), os.path.basename(long_run), "--out", root, "--scrollbars", "classic"])
    T.check("compare notes when the runs did not use the asserted scrollbar mode", "Expected both runs to use classic" in out)

    baseline_checks(T, um, root, exp, defs)
    align_checks(T, um, root, exp, defs)
    slop_checks(T, um, root, exp, defs)


def baseline_checks(T, um, root, exp, defs):
    """The clean-baseline rules: control.wrap, type.wrap, shape.nested, shape.radius:untokened, type.role, and layout.content.
    Each fixture plants defects and carries clean twins that must stay silent."""
    cases = exp["cases"]

    def planted(evs, case, label):
        for item in case["present"]:
            hits = class_findings(evs, item["rule"], item["class"], item["tier"], None, item.get("contains")) if "class" in item else \
                findings(evs, item["rule"], item["tier"], item.get("width"), None, item.get("contains"))
            T.check("planted: %s found by %s%s" % (item["defect"], item["rule"], (" at %s" % item["width"]) if item.get("width") else ""), bool(hits),
                    "no %s %s finding in %s" % (item["tier"], item["rule"], label))
        for item in case["absent"]:
            hits = class_findings(evs, item["rule"], item["class"], None, None, item.get("contains")) if "class" in item else \
                findings(evs, item["rule"], None, item.get("width"), None, item.get("contains"))
            T.check("clean twin: %s" % item["why"], not hits, hits[0][1]["message"][:100] if hits else "")

    # control.wrap and type.wrap
    w = cases["wrap"]
    code, out, err = cli(um, ["capture", page(w["page"]), "--out", root, "--label", "wrap"] + w["args"])
    w_run = latest_run(root)
    _m, w_evs = load_run(w_run)
    T.check("wrap.html exits 1 because a button label wraps", code == 1, "exit %s %s" % (code, err[-200:]))
    planted(w_evs, w, "wrap.html")
    nav = class_findings(w_evs, "control.wrap", "wrap-nav", "WARN")
    msg = nav[0][1]["message"] if nav else ""
    T.check("control.wrap names the label, the width, and the line count, and gives the fix order",
            "Specifications overview" in msg and "126px" in msg and "2 lines" in msg and "shorten the copy" in msg and msg.find("shorten the copy") < msg.find("widen the control")
            and "stack the controls" in msg and "Never shrink the font" in msg and "height grow" in msg, msg)
    btn = class_findings(w_evs, "control.wrap", "wrap-btn", "FAIL")
    T.check("control.wrap groups the buttons of one kind in one finding and names the worst", bool(btn) and btn[0][1]["count"] == 2 and "2 buttons wrap" in btn[0][1]["message"], str(btn)[:200])
    tw = class_findings(w_evs, "type.wrap", "wrap-h", "WARN")
    msg = tw[0][1]["message"] if tw else ""
    T.check("type.wrap tells the fix: balance for headings, never on code or pre", "text-wrap: balance" in msg and "six lines" in msg and "ten in Firefox" in msg and "never on code or pre" in msg, msg)
    tw = class_findings(w_evs, "type.wrap", "wrap-p", "INFO")
    msg = tw[0][1]["message"] if tw else ""
    T.check("type.wrap tells the fix for body copy: pretty", "text-wrap: pretty" in msg, msg)
    T.check("every text-wrap value the page set is read from Chrome", w_evs[0]["facts"].get("text_wrap_api") in ("text-wrap-style", "text-wrap"), str(w_evs[0]["facts"].get("text_wrap_api")))
    problems = validate_run(w_run, defs)
    T.check("wrap.html evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))

    # shape.nested and shape.radius:untokened, with the radius scale read by init, and again with no radius tokens
    n = cases["nested"]
    n_root = os.path.join(root, "nestedcfg")
    cli(um, ["init", page(n["page"]), "--out", n_root])
    code, out, err = cli(um, ["capture", page(n["page"]), "--out", n_root, "--label", "nested"] + n["args"])
    n_run = latest_run(n_root)
    _m, n_evs = load_run(n_run)
    planted(n_evs, n, "nested.html")
    hit = class_findings(n_evs, "shape.nested", "nest-bad", "WARN")
    f = hit[0][1] if hit else {"message": "", "fix": "", "detail": {}}
    T.check("shape.nested says the expected radius, that it is on the radius scale, and how to resolve it without inventing a value",
            ("needs %spx" % n["expected_radius"]) in f["message"] and "on the radius scale" in f["message"] and "not on the radius scale" not in f["message"]
            and "use that token, change the inset" in f["message"] and "calc(var(--radius-outer) - var(--inset))" in f["message"]
            and "(a) use the radius token equal to %spx" % n["expected_radius"] in f.get("fix", "") and "(b) change the inset" in f.get("fix", "")
            and "calc(var(--radius-outer) - var(--inset))" in f.get("fix", "") and f.get("detail", {}).get("expected") == n["expected_radius"], str(f)[:300])
    un = [x for ev in n_evs for x in ev["rules"]["shape.radius"]["findings"] if "not on the radius scale" in x["message"]]
    T.check("shape.radius:untokened lists the values with counts and names the nearest token", len(un) == 1 and sorted(un[0]["detail"]["values"]) == n["untokened_values"]
            and "nearest token 8px" in un[0]["message"] and un[0]["tier"] == "WARN", str(un)[:300])
    T.check("shape.radius keeps its distinct-count finding", any(x["key"] == "radii" for ev in n_evs for x in ev["rules"]["shape.radius"]["findings"]))
    problems = validate_run(n_run, defs)
    T.check("nested.html evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))
    p_root = os.path.join(root, "nestedplain")
    code, out, err = cli(um, ["capture", page(n["page"]), "--out", p_root, "--label", "notokens"] + n["args"])
    _m, p_evs = load_run(latest_run(p_root))
    hit = class_findings(p_evs, "shape.nested", "nest-bad", "WARN")
    f = hit[0][1] if hit else {"message": "", "fix": ""}
    T.check("with no radius tokens shape.nested says so and gives only the inset and calc fixes", "defines no radius tokens" in f["message"] and "calc(var(--radius-outer)" in f["message"]
            and "use that token" not in f["message"] and "(b) change the inset" in f.get("fix", "")
            and "(c) derive the inner radius in CSS" in f.get("fix", "") and "(a)" not in f.get("fix", ""), str(f)[:300])
    T.check("shape.radius:untokened needs a radius scale of at least two tokens", not [x for ev in p_evs for x in ev["rules"]["shape.radius"]["findings"] if "not on the radius scale" in x["message"]])

    # type.role
    r = cases["role"]
    code, out, err = cli(um, ["capture", page(r["page"]), "--out", root, "--label", "role"] + r["args"])
    r_run = latest_run(root)
    _m, r_evs = load_run(r_run)
    planted(r_evs, r, "role.html")
    hero = class_findings(r_evs, "type.role", "role-hero", "INFO")
    T.check("type.role downgrades a two-style pair to the ambiguous subtier", bool(hero) and "intentional" in hero[0][1]["message"] and hero[0][1]["key"].startswith("ambiguous:"), str(hero)[:200])
    problems = validate_run(r_run, defs)
    T.check("role.html evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))

    # layout.content
    ct = cases["content"]
    code, out, err = cli(um, ["capture", page(ct["page"]), "--out", root, "--label", "content"] + ct["args"])
    c_run = latest_run(root)
    _m, c_evs = load_run(c_run)
    planted(c_evs, ct, "content.html")
    at390 = [e for e in c_evs if e["viewport"]["width"] == 390][0]["facts"]["content"]
    T.check("layout.content measures from the scroll region, so the 64px rail is not a gutter", at390["region"][0] == 64 and at390["gutters"] == [4, 20] and at390["aside"] is False, str(at390))
    problems = validate_run(c_run, defs)
    T.check("content.html evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))
    code, out, err = cli(um, ["capture", page(ct["good_page"]), "--out", root, "--label", "contentgood", "--widths", "390,1920", "--no-focus"])
    g_run = latest_run(root)
    _m, g_evs = load_run(g_run)
    T.check("the clean content column has no layout.content finding at 390 or 1920", not findings(g_evs, "layout.content"), str([f["message"][:80] for _e, f in findings(g_evs, "layout.content")]))
    gc = [e["facts"]["content"] for e in g_evs]
    T.check("the clean column records equal gutters and a width under the cap", all(g["gutters"][0] == g["gutters"][1] and g["width"] <= 960 for g in gc), str(gc))
    T.check("a shrink-wrapped hero title next to the sections is not compared as a column", not findings(g_evs, "layout.content", None, 1920, None, "do not share one column"))
    code, out, err = cli(um, ["capture", page(ct["inner_page"]), "--out", root, "--label", "contentinner", "--widths", "1280", "--no-focus"])
    i_run = latest_run(root)
    _m, i_evs = load_run(i_run)
    ic = i_evs[0]["facts"]["content"]
    T.check("a scroll container inside main sets the region: its client box leaves out the reserved scrollbar gutter, so a centered column has equal gutters",
            ic["region"][0] == 64 and ic["gutters"][0] == ic["gutters"][1] and ic["width"] == 912 and not findings(i_evs, "layout.content"), str(ic))
    problems = validate_run(i_run, defs)
    T.check("the inner scroll region evidence matches the schema", not problems, "; ".join(problems[:3]))
    code, out, err = cli(um, ["capture", page(ct["data_page"]), "--out", root, "--label", "contentdata", "--widths", "1920", "--no-focus"])
    d_run = latest_run(root)
    _m, d_evs = load_run(d_run)
    T.check("a table-dominant column over 1440 px is information (cap), not a warning", bool(findings(d_evs, "layout.content", "INFO", 1920, None, "primary content column"))
            and not findings(d_evs, "layout.content", "WARN"), str([(f["tier"], f["message"][:80]) for _e, f in findings(d_evs, "layout.content")]))
    problems = validate_run(g_run, defs) + validate_run(d_run, defs)
    T.check("the clean and table content evidence matches the schema", not problems, "; ".join(problems[:3]))

    routes_checks(T, um, root, exp, defs)


def shell_table_problems(run_dir, defs):
    """shell.json must agree with the evidence it tables: it is hashed in the manifest, covers every evidence id, and each cell repeats the id, the cite,
    and the numbers of the evidence it names. Returns a list of problems."""
    manifest, evs = load_run(run_dir)
    by_id = dict((e["id"], e) for e in evs)
    listed = [a for a in manifest["artifacts"] if a["kind"] == "shell-table"]
    if len(listed) != 1:
        return ["the manifest lists %d shell tables" % len(listed)]
    path = os.path.join(run_dir, listed[0]["file"])
    problems = []
    if sha256_file(path) != listed[0]["sha256"]:
        problems.append("shell.json does not match its hash in the manifest")
    if set(listed[0]["cells"]) != set(by_id):
        problems.append("the manifest says shell.json covers %s, not every evidence id" % sorted(listed[0]["cells"]))
    with open(path, encoding="utf-8") as handle:
        table = json.load(handle)
    check_shape(table, defs["shelltable"], defs, "shell.json", problems)
    labels = [r["label"] for r in manifest["routes"]]
    count = 0
    for kind, routes in table["table"].items():
        for route, cells in routes.items():
            if route not in labels:
                problems.append("%s has the route %s, which the manifest does not list" % (kind, route))
            for key, cell in cells.items():
                ev = by_id.get(cell["id"])
                if ev is None or ev["cite"] != cell["cite"] or ev["state"] != route or key != "%d#%s" % (ev["viewport"]["width"], ev["theme"]):
                    problems.append("%s/%s/%s does not match the evidence %s" % (kind, route, key, cell["id"]))
                    continue
                count += 1
                rec = ev["facts"].get("content") if kind == "content" else (ev["facts"].get("shell") or {}).get(kind)
                if cell["present"] != bool(rec):
                    problems.append("%s/%s/%s: present is %s but the evidence says %s" % (kind, route, key, cell["present"], bool(rec)))
                elif rec and kind != "content" and [cell["x"], cell["y"], cell["width"], cell["height"]] != rec["rect"]:
                    problems.append("%s/%s/%s: the rect differs from the evidence" % (kind, route, key))
                elif rec and kind == "content" and [cell["left"], cell["width"]] != [rec["column"][0], rec["width"]]:
                    problems.append("%s/%s/%s: the column differs from the evidence" % (kind, route, key))
    if count != len(labels) * len(set((e["viewport"]["width"], e["theme"]) for e in evs)) * 6:
        problems.append("shell.json has %d cells, not one per landmark, route, and width" % count)
    shown = sum(len(e["rules"]["shell.consistency"]["findings"]) for e in evs)
    if len(table["findings"]) != shown:
        problems.append("shell.json lists %d findings but the evidence holds %d" % (len(table["findings"]), shown))
    return problems


def routes_checks(T, um, root, exp, defs):
    """`ui_measure.py routes`: usage errors, a shell shared by two pages that must produce no finding, a deviant third page whose planted differences
    must each be found and named, --ignore, shell.json, the evidence id, and a report the claims checker accepts."""
    rt = exp["cases"]["routes"]
    a, b, c = [page(p) for p in rt["pages"]]
    before = set(os.listdir(root))
    code, out, err = cli(um, ["routes", a, "--out", root])
    T.check("routes: one URL is a usage error that says at least two are needed, and no run folder is made", code == 2 and "at least 2 URLs" in err and set(os.listdir(root)) == before, "%s %s" % (code, err[:160]))
    code, out, err = cli(um, ["routes", "--out", root])
    T.check("routes: no URL is the same usage error", code == 2 and "at least 2 URLs" in err)
    code, out, err = cli(um, ["routes", a, a, "--out", root])
    T.check("routes: the same URL twice is refused", code == 2 and "listed twice" in err)
    code, out, err = cli(um, ["routes", a, b, "--ignore", "sidebar", "--out", root])
    T.check("routes: an unknown --ignore name lists the landmark names", code == 2 and "banner, nav, footer, title, and content" in err, err[:160])
    code, out, err = cli(um, ["routes", a, b, "--tol", "-1", "--out", root])
    T.check("routes: a negative --tol is refused", code == 2 and "--tol" in err)
    help_out = io.StringIO()
    with contextlib.redirect_stdout(help_out):
        try:
            um.main(["routes", "--help"])
        except SystemExit:
            pass
    helptext = " ".join(help_out.getvalue().split())
    T.check("routes --help says what is compared, that a route with its own shell goes in a separate run, and how the id names the route",
            all(w in helptext for w in ("the header, the primary nav or rail, the footer", "separate routes run", "--ignore", "E03@1280:projects-specs#light", "URL [URL ...]", "--tol")), helptext[:200])
    # labels and names come from the URL path, never from a query value
    for url, label, name in (("http://127.0.0.1:5173/", "home", "/"), ("http://127.0.0.1:5173/projects/specs", "projects-specs", "/projects/specs"),
                             ("http://127.0.0.1:5173/inbox?tab=SECRET", "inbox-tab", "/inbox?tab=~"), ("http://127.0.0.1:5173/conflicts/c/1735cd69-c04f-40b9-b9ef-739940955acf", "conflicts-c-id", None),
                             ("http://127.0.0.1:5173/default", "route-default", "/default"), ("file://" + a, "shell-a", "shell-a.html")):
        T.check("routes: %s is the route %s" % ("a file URL" if url.startswith("file:") else (url.replace("http://127.0.0.1:5173", "")[:44] or "/"), label), um.route_slug(url) == label and (name is None or um.route_name(url) == name) and "SECRET" not in um.route_name(url),
                "%s %s" % (um.route_slug(url), um.route_name(url)))
    cfg = um.load_config(root)[0]
    plan = um.Routes(type("A", (), {"urls": ["http://127.0.0.1:5173/inbox", "http://127.0.0.1:5173/inbox?tab=x", "http://127.0.0.1:5173/inbox?tab=y"], "allow_remote": False, "ignore": None, "tol": None})(), cfg)
    T.check("routes: labels that repeat get a number, and every label is a state id", plan.labels == ["inbox", "inbox-tab", "inbox-tab-2"] and all(re.match(r"^[A-Za-z0-9_-]{1,40}$", l) for l in plan.labels), str(plan.labels))
    two = um.Routes(type("A", (), {"urls": ["http://127.0.0.1:5173/a", "http://127.0.0.1:5173/b"], "allow_remote": False, "ignore": None, "tol": None})(), cfg)
    fake = lambda eid, route, shell: {"id": eid, "cite": "%s@1280:%s#light" % (eid, route), "state": route, "viewport": {"width": 1280}, "theme": "light", "facts": {"shell": shell, "content": None},
                                      "rules": {"shell.consistency": {"status": "skipped", "tier": "WARN", "checked": 0, "findings": [], "reason": "rule error"}}, "summary": {"FAIL": 0, "WARN": 0, "INFO": 0}}
    evs = [fake("E01", "a", shell_cell("a")["shell"]), fake("E02", "b", None)]
    result = two.compare(evs, cfg)
    two.attach(evs, result, cfg)
    T.check("routes: a page whose shell could not be measured is left out and named, never compared as if every landmark were missing",
            two.unmeasured == ["E02"] and result["findings"] == {"E01": []} and evs[1]["rules"]["shell.consistency"]["status"] == "skipped"
            and evs[0]["rules"]["shell.consistency"]["status"] == "pass" and any("could not be measured on E02" in n for n in two.notes(evs, result)), str(two.notes(evs, result)))

    # 1. two pages that share one shell
    code, out, err = cli(um, ["routes", a, b, "--out", root, "--label", "same"] + rt["args"])
    s_run = latest_run(root)
    s_manifest, s_evs = load_run(s_run)
    T.check("routes: two pages with one shell exit 0, name their run NNN-routes-label, and make one evidence file per route and width",
            code == 0 and re.match(r"^\d{3}-routes-same$", os.path.basename(s_run)) and len(s_evs) == 4, "exit %s %s %s" % (code, os.path.basename(s_run), err[-200:]))
    T.check("routes: the route label is the state in every cite, and the manifest lists the routes",
            sorted(e["cite"].split("@", 1)[1] for e in s_evs) == ["1280:shell-a#light", "1280:shell-b#light", "390:shell-a#light", "390:shell-b#light"]
            and [r["label"] for r in s_manifest["routes"]] == ["shell-a", "shell-b"] and s_manifest["states"] == ["shell-a", "shell-b"] and s_manifest["url"] is None, str(s_manifest["routes"])[:200])
    hits = [(e["cite"], f["message"]) for e in s_evs for f in e["rules"]["shell.consistency"]["findings"]]
    T.check("routes: pages A and B alone produce no shell.consistency finding at 1280 or 390", not hits and all(e["rules"]["shell.consistency"]["checked"] > 0 for e in s_evs), str(hits[:2]))
    one = [e for e in s_evs if e["viewport"]["width"] == 1280][0]["facts"]["shell"]
    narrow = [e for e in s_evs if e["viewport"]["width"] == 390][0]["facts"]["shell"]
    T.check("routes: a page records its header, rail, footer, title, and items, and the rail turns horizontal at 390",
            one["banner"]["pos"] == "sticky" and one["banner"]["rect"] == [0, 0, 1280, 56] and one["nav"]["orientation"] == "vertical" and one["nav"]["rect"][2] == 240
            and one["nav"]["items"] == {"count": 5, "height": 36, "gap": 4, "font_size": 14} and one["contentinfo"] is not None and one["title"]["size"] == 28 and one["title"]["weight"] == 600
            and one["edge"] is None and narrow["nav"]["orientation"] == "horizontal" and narrow["nav"]["items"]["gap"] == 4, json.dumps(one)[:300])
    problems = validate_run(s_run, defs) + shell_table_problems(s_run, defs)
    T.check("routes: the run matches the EVIDENCE.md schema and invariants, and shell.json agrees with the evidence files", not problems, "; ".join(problems[:3]))
    lines = open(os.path.join(s_run, "summary.md"), encoding="utf-8").read().split("\n")
    T.check("routes: summary.md is at most 60 lines and says no shell difference was found", len(lines) <= 61 and "## Shell Differences" in lines and any(l.startswith("No difference above the tolerance") for l in lines))

    # 2. the deviant page
    code, out, err = cli(um, ["routes", a, b, c, "--out", root, "--label", "deviant"] + rt["args"])
    d_run = latest_run(root)
    d_manifest, d_evs = load_run(d_run)
    T.check("routes: a deviant page leaves no FAIL, so the exit is 0, and the run covers three routes at two widths", code == 0 and len(d_evs) == 6, "exit %s %s" % (code, err[-200:]))
    every = [(e, f) for e in d_evs for f in e["rules"]["shell.consistency"]["findings"]]
    for item in rt["present"]:
        hit = [(e, f) for e, f in every if f["key"] == item["key"] and e["viewport"]["width"] == item["width"] and (item.get("contains") or "") in f["message"]]
        ok = bool(hit) and all(e["state"] == rt["deviant"] and f["detail"]["route"] == rt["deviant"] and f["tier"] == "WARN" for e, f in hit)
        if ok and "value" in item:
            ok = hit[0][1]["value"] == item["value"] and hit[0][1]["detail"]["dominant"] == item["dominant"]
        if ok and "dominant" in item and "value" not in item:
            ok = hit[0][1]["detail"]["dominant"] == item["dominant"]
        T.check("routes: %s, found by %s at %d and naming the route %s" % (item["defect"], item["key"], item["width"], rt["deviant"]), ok, str([(e["cite"], f["key"]) for e, f in every])[:300])
    for item in rt["absent"]:
        hit = [(e, f) for e, f in every if f["key"].startswith(item["key"]) and (not item.get("width") or e["viewport"]["width"] == item["width"])]
        T.check("routes: %s" % item["why"], not hit, hit[0][1]["message"][:120] if hit else "")
    T.check("routes: every finding names the landmark, the dominant value, the deviating route, the width, and says to render one shared shell component",
            all(rt["fix"] in f["message"] and ("at %dpx wide" % f["detail"]["width"]) in f["message"] and f["detail"]["route"] in (e["state"],) and f["detail"]["dominant"] is not None for e, f in every), str(every[:1])[:200])
    T.check("routes: a route that agrees with the others has no finding in its evidence", all(not e["rules"]["shell.consistency"]["findings"] for e in d_evs if e["state"] != rt["deviant"]))
    sec = open(os.path.join(d_run, "summary.md"), encoding="utf-8").read().split("\n")
    shown = "\n".join(sec[sec.index("## Shell Differences"):])
    T.check("routes: the summary lists the shell differences first, grouped by cause, in at most 60 lines",
            len(sec) <= 61 and all(w in shown for w in ("nav width", "footer", "page title font size", "content column left edge", "shell-c")), shown[:400])
    problems = validate_run(d_run, defs) + shell_table_problems(d_run, defs)
    T.check("routes: the deviant run matches the schema and invariants, and shell.json agrees with the evidence", not problems, "; ".join(problems[:3]))
    uc = importlib.import_module("ui_claims")
    ids = dict((e["cite"].split("@")[1].split(":")[0] + "/" + e["state"], e["cite"]) for e in d_evs)
    report = os.path.join(root, "routes-report.md")
    with open(report, "w", encoding="utf-8") as handle:
        handle.write("The nav is 256 px wide on shell-c.html at 1280 px, 16 px wider than on the other two routes. [%s]\nThe page title is 28 px on shell-a.html at 1280 px. [%s]\n" % (ids["1280/shell-c"], ids["1280/shell-a"]))
    code, out, err = cli(uc, ["check", report, "--evidence", d_run, "--strict"])
    T.check("routes: the claims checker accepts a report that cites the routes evidence", code == 0 and "=> PASS" in out, out[-300:])

    # 3. --ignore
    code, out, err = cli(um, ["routes", a, b, c, "--out", root, "--label", "ignore"] + rt["ignore"])
    i_run = latest_run(root)
    _m, i_evs = load_run(i_run)
    keys = sorted(set(f["key"] for e in i_evs for f in e["rules"]["shell.consistency"]["findings"]))
    T.check("routes: --ignore footer,nav drops the footer and nav findings and keeps the title and content findings", keys == ["content:left", "title:size"], str(keys))
    T.check("routes: --ignore is recorded in each evidence file", all(e["rules"]["shell.consistency"]["detail"]["ignored"] == ["contentinfo", "nav"] for e in i_evs))
    problems = validate_run(i_run, defs) + shell_table_problems(i_run, defs)
    T.check("routes: the --ignore run matches the schema and invariants", not problems, "; ".join(problems[:3]))


def align_checks(T, um, root, exp, defs):
    """align.near_miss on right and top edges, each with clean twins that look alike but are meant to differ."""
    case = exp["cases"]["align"]
    code, out, err = cli(um, ["capture", page(case["page"]), "--out", root, "--label", "align"] + case["args"])
    run = latest_run(root)
    _m, evs = load_run(run)
    for item in case["present"]:
        T.check("planted: %s, found by %s" % (item["defect"], item["rule"]), bool(class_findings(evs, item["rule"], item["class"], item["tier"], None, item.get("contains"))),
                "no %s %s finding that names .%s" % (item["tier"], item["rule"], item["class"]))
    for item in case["absent"]:
        hits = class_findings(evs, item["rule"], item["class"], None, None, item.get("contains"))
        T.check("clean twin: %s" % item["why"], not hits, hits[0][1]["message"][:100] if hits else "")
    right = class_findings(evs, "align.near_miss", "miss-right-b", "WARN", None, "right edge")
    T.check("align.near_miss: a right-edge finding says how far apart the edges are and has the right subtier and detail",
            bool(right) and right[0][1]["value"] == 3 and right[0][1]["detail"]["edge"] == "right" and "3 px from" in right[0][1]["message"], str(right[:1])[:200])
    problems = validate_run(run, defs)
    T.check("align.html evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))


def slop_checks(T, um, root, exp, defs):
    """The slop rules and allows: three pages captured at once into one evidence root (which also proves ids and run numbers never collide), a page that
    trips every rule, its clean twin, a stock shadcn/ui Card that must pass, and compare's account of what the cleanup dropped."""
    sc = exp["cases"]["slop"]
    jobs = [("slop", sc["page"], ["--phase", "baseline"] + sc["args"]), ("slopclean", sc["clean_page"], ["--phase", "final"] + sc["args"]),
            ("slopcard", sc["card_page"], ["--widths", "800,390", "--no-focus"])]
    procs = [subprocess.Popen([sys.executable, os.path.join(HERE, "ui_measure.py"), "capture", page(p), "--out", root, "--label", label] + extra,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True) for label, p, extra in jobs]
    codes = {}
    for (label, _p, _e), proc in zip(jobs, procs):
        _o, err = proc.communicate(timeout=120)
        codes[label] = (proc.returncode, err[-160:])
    T.check("three captures at once into one evidence root all finish (exit 0 or 1)", all(c[0] in (0, 1) for c in codes.values()), str(codes))
    found = {}
    for d in sorted(os.listdir(root)):
        path = os.path.join(root, d)
        if re.match(r"^\d{3}-", d) and os.path.isfile(os.path.join(path, "manifest.json")):
            manifest, evs = load_run(path)
            if manifest["label"] in ("slop", "slopclean", "slopcard"):
                found[manifest["label"]] = (path, manifest, evs)
    T.check("each concurrent capture wrote its own run", sorted(found) == ["slop", "slopcard", "slopclean"] and all(m["complete"] for _p, m, _e in found.values()), str(sorted(found)))
    if sorted(found) != ["slop", "slopcard", "slopclean"]:
        return
    ids = [e["id"] for _p, _m, evs in found.values() for e in evs]
    numbers = [os.path.basename(p)[:3] for p, _m, _e in found.values()]
    T.check("evidence ids never collide across concurrent runs", len(ids) == len(set(ids)) == 4, str(ids))
    T.check("run numbers never collide across concurrent runs", len(set(numbers)) == 3, str(numbers))
    card_ids = sorted(int(e["id"][1:]) for e in found["slopcard"][2])
    T.check("a run's ids stay consecutive while other runs write", card_ids == list(range(card_ids[0], card_ids[0] + 2)), str(card_ids))
    T.check("the lock leaves nothing behind but its files", not os.path.exists(os.path.join(root, ".lock.d")) and int(open(os.path.join(root, ".ids")).read()) >= max(int(i[1:]) for i in ids))

    s_path, s_manifest, s_evs = found["slop"]
    slop_rules = [rid for rid in _rules.RULE_IDS if rid.startswith("slop.")] + ["allow.reason"]
    for item in sc["present"]:
        hits = class_findings(s_evs, item["rule"], item["class"], item["tier"], None, item.get("contains"))
        ok = bool(hits) and (not item.get("fix") or any(item["fix"].lower() in f["message"].lower() for _e, f in hits))
        T.check("planted: %s, found by %s as %s and the message names the fix" % (item["defect"], item["rule"], item["tier"]) if item.get("fix") else
                "planted: %s, found by %s as %s" % (item["defect"], item["rule"], item["tier"]), ok, "no %s %s finding that names .%s" % (item["tier"], item["rule"], item["class"]))
    for item in sc["absent"]:
        hits = [h for rule in slop_rules for h in class_findings(s_evs, rule, item["class"])]
        T.check("clean twin: %s" % item["why"], not hits, "%s %s" % (hits[0][1]["rule"], hits[0][1]["message"][:90]) if hits else "")
    for rule, want in sc["live_counts"].items():
        live = [f for ev in s_evs for f in ev["rules"][rule]["findings"] if not f.get("allowed")]
        T.check("%s finds exactly %d on slop.html, so no clean element is caught beside the planted one" % (rule, want), sum(f["count"] for f in live) == want, str([f["message"][:70] for f in live]))
    live_all = [f for ev in s_evs for rule in slop_rules if rule.startswith("slop.") for f in ev["rules"][rule]["findings"] if not f.get("allowed")]
    T.check("every slop finding states its fix in the message", live_all and all(f.get("fix") and f["fix"].lower() in f["message"].lower() for f in live_all), str([f["rule"] for f in live_all if not f.get("fix")]))
    T.check("copy rules never FAIL, and the tiers are the contract's", not [f for f in live_all if f["tier"] == "FAIL"]
            and set((f["rule"], f["tier"]) for f in live_all) == set((r, "INFO" if r in ("slop.restates_heading", "slop.dash") else "WARN") for r in sc["live_counts"]))
    allowed = [f for ev in s_evs for f in ev["rules"]["slop.eyebrow"]["findings"] if f.get("allowed")]
    T.check("an allow with a reason demotes the finding to INFO and keeps the reason", len(allowed) == 1 and allowed[0]["tier"] == "INFO" and allowed[0]["allowed"]["reason"] == sc["allow_reason"], str(allowed)[:200])
    summary = open(os.path.join(s_path, "summary.md"), encoding="utf-8").read()
    T.check("summary.md counts the allows and names the reason", "1 element allowed by data-ui-allow" in summary and "product name lockup" in summary and s_manifest["summary"]["allowed"] == 1, summary[-400:])
    detail = s_evs[0]["rules"]["allow.reason"].get("detail", {})
    T.check("allow.reason lists every allow on the page with how many findings each demoted", len(detail.get("allows", [])) == 2 and detail.get("applied") == 1, str(detail)[:200])
    problems = validate_run(s_path, defs)
    T.check("slop.html evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))

    c_path, _cm, c_evs = found["slopclean"]
    stray = [(rule, f["message"][:80]) for ev in c_evs for rule in slop_rules for f in ev["rules"][rule]["findings"]]
    T.check("slop-clean.html, the same page with every tell removed, has no slop or allow finding at any tier", not stray, str(stray[:2]))
    T.check("slop-clean.html still runs the slop rules over real candidates", c_evs[0]["rules"]["slop.generic_action"]["checked"] >= 3 and c_evs[0]["rules"]["slop.repeated_text"]["checked"] > 10)
    k_path, _km, k_evs = found["slopcard"]
    stray = [(rule, f["message"][:80]) for ev in k_evs for rule in slop_rules for f in ev["rules"][rule]["findings"]]
    T.check("a stock shadcn/ui Card passes every slop rule at 800 and 390", not stray and len(k_evs) == 2, str(stray[:2]))
    problems = validate_run(c_path, defs) + validate_run(k_path, defs)
    T.check("the clean and card evidence matches the schema and invariants", not problems, "; ".join(problems[:3]))
    for rid in slop_rules:
        T.check("%s has a tier in defaults.json and ran on every page" % rid, all(rid in ev["rules"] for ev in s_evs + c_evs + k_evs))

    # compare: where did the baseline's text go?
    code, out, err = cli(um, ["compare", os.path.basename(s_path), os.path.basename(c_path), "--out", root])
    with open(os.path.join(c_path, "compare-%s.json" % os.path.basename(s_path)), encoding="utf-8") as handle:
        cmp_json = json.load(handle)
    problems = []
    check_shape(cmp_json, defs["compare"], defs, "compare", problems)
    T.check("compare output with drops matches the schema", not problems, "; ".join(problems[:3]))
    drops = dict((d["text"], d) for p in cmp_json["pairs"] for d in p["text"]["drops"])

    def of(prefix):
        return [d for t, d in drops.items() if t.lower().startswith(prefix.lower())]
    T.check("compare: the eyebrow and the generic label the cleanup deleted are classified as slop and name their rules",
            all(of(t) and of(t)[0]["class"] == "slop" for t in sc["drops"]["slop"]) and of("New release")[0]["by"] == "slop.eyebrow" and of("Learn more")[0]["by"] == "slop.generic_action",
            str([(d["text"], d["class"], d["by"]) for d in drops.values()])[:300])
    T.check("compare: copy edited in place is classified as replaced", all(of(t) and of(t)[0]["class"] == "replaced" for t in sc["drops"]["replaced"]), str(of("Fast, reliable"))[:200])
    T.check("compare: a text the cleanup dropped that nothing explains is unclassified, counted in the summary, and listed in the report",
            all(of(t) and of(t)[0]["class"] == "unclassified" for t in sc["drops"]["unclassified"]) and cmp_json["summary"]["unclassified"] >= 1
            and "Unclassified drops" in out and "[slop: slop.eyebrow]" in out, out[:400])
    T.check("compare: every dropped text and every edited text has exactly one class", all(len([d for d in p["text"]["drops"] if d["text"] == t]) == 1 for p in cmp_json["pairs"] for t in p["text"]["dropped"] + [x["from"] for x in p["text"]["changed"]]))
