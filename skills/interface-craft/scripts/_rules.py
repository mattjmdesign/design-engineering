"""_rules.py: the rules behind ui_measure.py.

The in-page collector returns raw facts (geometry, resolved colors, text, control
probes). Every function here turns those facts into findings by applying the
numbers in defaults.json. Nothing in this module touches the network, the file
system, or Chrome: it is pure computation over a dict, so it can be unit tested
and re-run on saved facts.

Compatible with Python 3.9.
"""
import bisect
import math
import re
import statistics
from collections import Counter, defaultdict

# ----------------------------------------------------------------------------
# small utilities

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")


def clean(value, limit=200):
    """Page-derived text is untrusted: strip control characters and lone surrogates, collapse spaces, cap the length."""
    raw = str(value).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    text = " ".join(_CONTROL_CHARS.sub(" ", raw).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def deep_merge(base, extra):
    out = dict(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def num(value, places=2):
    """Round for display and storage without trailing noise."""
    if value is None:
        return None
    rounded = round(float(value), places)
    return int(rounded) if rounded == int(rounded) else rounded


def js_round(x):
    return int(math.floor(x + 0.5))


# ----------------------------------------------------------------------------
# color math (WCAG 2.x relative luminance; same constants as evals/design-engineering/suite.py)

def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(rgb):
    r, g, b = (_lin(v / 255.0) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b):
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def composite(fg, bg, alpha):
    return [js_round(fg[i] * alpha + bg[i] * (1 - alpha)) for i in range(3)]


def hex_to_rgb(text):
    value = text.strip().lstrip("#")
    if len(value) in (3, 4):
        value = "".join(ch * 2 for ch in value)
    if len(value) not in (6, 8):
        raise ValueError("not a hex color: %s" % text)
    return [int(value[i:i + 2], 16) for i in (0, 2, 4)]


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb[:3])


def to_oklch(rgb):
    r, g, b = (_lin(v / 255.0) for v in rgb[:3])
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (max(x, 0.0) ** (1.0 / 3.0) for x in (l, m, s))
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return lightness, math.hypot(a, b2), math.degrees(math.atan2(b2, a)) % 360


def hue_gap(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def nearest_grid(value, base):
    """Return (lower, upper) multiples of base around value."""
    lower = math.floor(value / base) * base
    return lower, lower + base


def on_grid(value, base, tolerance):
    return abs(value - base * round(value / base)) <= tolerance + 1e-9


# ----------------------------------------------------------------------------
# configuration access

class Cfg(object):
    """Thresholds and tiers. Lookups are strict: a missing key is a bug, not a silent default."""

    def __init__(self, merged):
        self.d = merged

    def t(self, rule, key):
        return self.d["thresholds"][rule][key]

    def th(self, rule):
        return self.d["thresholds"][rule]

    def cap(self, key):
        return self.d["capture"][key]

    def tier(self, rule, sub=None):
        if sub and (rule + ":" + sub) in self.d["subtiers"]:
            return self.d["subtiers"][rule + ":" + sub]
        return self.d["tiers"][rule]


# ----------------------------------------------------------------------------
# context

# Chrome's default padding for form controls (top, right, bottom, left). A control left at its default has no authored padding.
UA_CONTROL_PADDING = {"button": (1, 6, 1, 6), "input": (1, 2, 1, 2), "textarea": (2, 2, 2, 2)}


class Ctx(object):
    def __init__(self, raw, view, cfg, extras=None):
        self.raw = raw
        self.cfg = cfg
        self.view = view
        self.facts = raw.get("facts") or {}
        self.nodes = raw.get("nodes") or []
        self.texts = raw.get("texts") or []
        self.controls = raw.get("controls") or []
        self.images = raw.get("images") or []
        self.headings = raw.get("headings") or []
        self.tables = raw.get("tables") or []
        self.clicks = raw.get("clicks") or []
        self.fonts = raw.get("fonts") or {"used": [], "faces": []}
        extras = extras or {}
        self.ax = extras.get("ax") or {}
        self.focus = extras.get("focus")
        self.console = extras.get("console") or []
        self.network = extras.get("network") or {}
        self.state = extras.get("state") or {}
        self.project = extras.get("project") or {}
        self.errors = []
        self.details = {}
        self.fact_out = {}  # facts that rules measure (for example facts.content); the caller merges them into the evidence facts
        self._memo = {}
        count = len(self.nodes)
        self.end = list(range(count))
        self.children = [[] for _ in range(count)]
        self.depth = [0] * count
        for i in range(count - 1, -1, -1):
            parent = self.nodes[i]["p"]
            if parent >= 0:
                self.children[parent].append(i)
                if self.end[parent] < self.end[i]:
                    self.end[parent] = self.end[i]
        for kids in self.children:
            kids.reverse()
        for i in range(count):
            parent = self.nodes[i]["p"]
            self.depth[i] = self.depth[parent] + 1 if parent >= 0 else 0
        self.text_of = {}
        for rec in self.texts:
            self.text_of.setdefault(rec["n"], rec)
        self.text_nodes = sorted(self.text_of.keys())
        self.disabled = set()
        for ctl in self.controls:
            if ctl.get("dis"):
                self.disabled.add(ctl["n"])

    # tree helpers ---------------------------------------------------------
    def inside(self, ancestor, node):
        """True when node is ancestor itself or lies in its subtree."""
        return ancestor <= node <= self.end[ancestor]

    def in_disabled(self, node):
        j = node
        while j >= 0:
            if j in self.disabled:
                return True
            j = self.nodes[j]["p"]
        return False

    def has_text_below(self, i):
        k = bisect.bisect_left(self.text_nodes, i)
        return k < len(self.text_nodes) and self.text_nodes[k] <= self.end[i]

    def _twin(self, j):
        """True when a sibling shares this node's short selector, so :nth-of-type is needed to tell them apart."""
        parent = self.nodes[j]["p"]
        counts = self._memo.get(("twins", parent))
        if counts is None:
            counts = Counter(self.nodes[k]["s"] for k in (self.children[parent] if parent >= 0 else []))
            self._memo[("twins", parent)] = counts
        return counts[self.nodes[j]["s"]] > 1

    def path(self, i):
        segs = []
        j = i
        while j >= 0 and len(segs) < 3:
            node = self.nodes[j]
            if node["t"] == "body":
                break
            seg = node["s"]
            if node.get("nth") and self._twin(j):
                seg += ":nth-of-type(%d)" % node["nth"]
            segs.append(seg)
            if "#" in node["s"]:
                j = -1
                break
            j = node["p"]
        segs.reverse()
        prefix = "... > " if j >= 0 and self.nodes[j]["t"] != "body" else ""
        return clean(prefix + " > ".join(segs), 160)

    def rect(self, i):
        return self.nodes[i]["r"]

    def lca_distance(self, a, b):
        """Levels from a and from b up to their lowest common ancestor."""
        da, db = 0, 0
        x, y = a, b
        while x != y:
            if x < 0 or y < 0:
                return 99, 99
            if self.depth[x] >= self.depth[y]:
                x = self.nodes[x]["p"]
                da += 1
            else:
                y = self.nodes[y]["p"]
                db += 1
        return da, db

    # findings ---------------------------------------------------------------
    def finding(self, rule, message, sub=None, tier=None, limit=260, **extra):
        out = {"rule": rule, "tier": tier or self.cfg.tier(rule, sub), "message": clean(message, limit)}
        out.update(extra)
        return out

    def example(self, i, **extra):
        out = {"selector": self.path(i), "rect": self.rect(i)}
        out.update(extra)
        return out

    def is_layout(self, i):
        key = ("layout", i)
        if key not in self._memo:
            T = self.cfg.th("grid.spacing")
            n = self.nodes[i]
            w, h = n["r"][2], n["r"][3]
            vw = self.facts.get("innerWidth") or self.view["width"]
            self._memo[key] = bool(
                n["t"] in T["layout_tags"]
                or (w >= T["layout_min_width_fraction"] * vw and h >= T["layout_min_height_px"] and self.depth[i] <= 4))
        return self._memo[key]

    def spacing_values(self):
        """Authored padding, margin, and gap values: [(node, property, px)]."""
        if "spacing" in self._memo:
            return self._memo["spacing"]
        T = self.cfg.th("grid.spacing")
        out = []
        sides = ("top", "right", "bottom", "left")
        for i, n in enumerate(self.nodes):
            inline = n.get("d") == "inline"
            for prop, arr in (("padding", n.get("pad")), ("margin", n.get("mar"))):
                if not arr:
                    continue
                if prop == "padding" and n["t"] in UA_CONTROL_PADDING and tuple(v or 0 for v in arr) == UA_CONTROL_PADDING[n["t"]]:
                    continue  # the browser's own default, not something the author chose
                if prop == "margin" and n["t"] == "input" and n.get("ty") in ("checkbox", "radio") and all(abs(v or 0) <= 6 for v in arr):
                    continue
                for k, v in enumerate(arr):
                    if v is None or (inline and k in (0, 2)):
                        continue
                    if abs(v) <= T["hairline_px"]:
                        continue
                    out.append((i, "%s-%s" % (prop, sides[k]), v))
            gap = n.get("gap")
            if gap:
                for k, v in enumerate(gap):
                    if v is not None and abs(v) > T["hairline_px"]:
                        out.append((i, "row-gap" if k == 0 else "column-gap", v))
        self._memo["spacing"] = out
        return out


# ----------------------------------------------------------------------------
# generic helpers for rules

def overlay_layer(c, i):
    """Index of the nearest fixed or sticky box or dialog around node i, or -1 for the page itself. Content in different
    layers is meant to overlap (a modal over the page, a sticky bar over content), so rules that compare elements with
    each other compare only elements in the same layer."""
    key = ("layer", i)
    if key not in c._memo:
        n = c.nodes[i]
        if n.get("pos") in ("fixed", "sticky") or n["t"] == "dialog" or n.get("role") in ("dialog", "alertdialog"):
            c._memo[key] = i
        else:
            c._memo[key] = overlay_layer(c, n["p"]) if n["p"] >= 0 else -1
    return c._memo[key]


def _group_by(items, keyfunc):
    groups = defaultdict(list)
    for item in items:
        groups[keyfunc(item)].append(item)
    return groups


def _cap_findings(c, rule, findings, limit=30):
    """Keep E json small: past the limit, fold the rest into one summary finding of the same rule."""
    if len(findings) <= limit:
        return findings
    kept = findings[:limit]
    rest = findings[limit:]
    kept.append(c.finding(rule, "%d more findings of this rule are not listed" % len(rest),
                          tier=max((f["tier"] for f in rest), key=lambda t: {"INFO": 0, "WARN": 1, "FAIL": 2}[t]),
                          count=sum(f.get("count", 1) for f in rest), key="more"))
    return kept


# ----------------------------------------------------------------------------
# rules: evidence validity

def r_viewport_truth(c):
    f, v = c.facts, c.view
    tol = c.cfg.t("viewport.truth", "width_tolerance_px")
    out = []
    got = f.get("innerWidth")
    if got is None or abs(got - v["width"]) > tol:
        out.append(c.finding("viewport.truth",
                             "page laid out at %s px wide, not the requested %s px; screenshots and numbers describe the wrong width"
                             % (got, v["width"]), value=got, threshold=v["width"], unit="px",
                             worst="%s px (asked for %s)" % (got, v["width"]), key="innerWidth"))
    png_w = v.get("png_width")
    if png_w is not None and abs(png_w - v["width"] * v["dpr"]) > 1:
        out.append(c.finding("viewport.truth",
                             "screenshot is %s px wide but %s px at %sx should produce %s"
                             % (png_w, v["width"], v["dpr"], num(v["width"] * v["dpr"])), value=png_w,
                             threshold=num(v["width"] * v["dpr"]), unit="px", key="png_width"))
    if f.get("dpr") is not None and abs(f["dpr"] - v["dpr"]) > 0.01:
        out.append(c.finding("viewport.truth", "device pixel ratio is %s, not the requested %s" % (f["dpr"], v["dpr"]),
                             value=f["dpr"], threshold=v["dpr"], key="dpr"))
    sb = v.get("scrollbars") or {}
    if sb.get("mode") == "classic" and sb.get("native_px", 0) == 0:
        out.append(c.finding("viewport.truth",
                             "classic scrollbars were requested but this Chrome draws overlay scrollbars that take no layout space (scrollbar_px is %s); "
                             "a scrollbar-induced shift cannot show up in this run. Use --scrollbars emulated to reserve a %s px gutter"
                             % (f.get("scrollbar_px"), c.cfg.cap("scrollbar_emulated_px")), sub="scrollbars", value=f.get("scrollbar_px"),
                             key="scrollbars-overlay"))
    return 3, out


def r_viewport_meta(c):
    f = c.facts
    T = c.cfg.th("viewport.meta")
    if c.view["width"] > T["check_at_or_below_px"]:
        return 0, []
    meta = f.get("meta_viewport")
    if meta is None or "width=device-width" not in meta.replace(" ", ""):
        return 1, [c.finding("viewport.meta",
                             "no <meta name=viewport content=\"width=device-width, initial-scale=1\">: phones lay the page out at about 980 px and shrink it, which this emulation does not show",
                             value=meta, key="viewport-meta")]
    return 1, []


def r_theme_truth(c):
    want = c.view["theme"]
    got = c.facts.get("rendered_theme")
    if got is None or want == got:
        return 1, []
    if want == "dark":
        return 1, [c.finding("theme.truth",
                             "dark was requested with prefers-color-scheme but the page renders a light background; dark values are not being measured",
                             value=got, key="dark-not-applied")]
    return 1, [c.finding("theme.truth", "light was requested but the page renders a dark background", sub="info", tier="INFO",
                         value=got, key="light-dark")]


def r_state_confirmed(c):
    s = c.state or {}
    out = []
    if s.get("ready") is False:
        out.append(c.finding("state.confirmed", "the page was not ready: %s" % clean(s.get("ready_reason", "ready condition never met")),
                             value="not ready", key="ready"))
    if s.get("confirmed") is False:
        out.append(c.finding("state.confirmed",
                             "state '%s' was not rendered: %s" % (clean(s.get("id", "default"), 40), clean(s.get("reason", "expected element absent"))),
                             value="unconfirmed", key="state:%s" % s.get("id", "default")))
    checked = 1 if (s.get("expect") or s.get("ready") is not None) else 0
    return checked, out


def r_console_errors(c):
    out = []
    errors = [e for e in c.console if e.get("level") == "error"]
    unexpected = [e for e in errors if not e.get("expected")]
    expected = [e for e in errors if e.get("expected")]
    warns = [e for e in c.console if e.get("level") == "warning"]
    if unexpected:
        groups = _group_by(unexpected, lambda e: clean(e.get("text", ""), 120))
        top = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        out.append(c.finding("console.errors", "%d console error%s: %s" % (len(unexpected), "" if len(unexpected) == 1 else "s", top[0][0]),
                             value=len(unexpected), unit="errors", count=len(unexpected), worst="%d error%s" % (len(unexpected), "" if len(unexpected) == 1 else "s"),
                             key=top[0][0][:80], examples=[{"text": t, "count": len(g), "source": g[0].get("source")} for t, g in top[:5]]))
    if expected:
        out.append(c.finding("console.errors", "%d console error%s caused by a forced fault in this state" % (len(expected), "" if len(expected) == 1 else "s"),
                             sub="expected", value=len(expected), count=len(expected), expected=True, key="expected-faults",
                             examples=[{"text": clean(e.get("text", ""), 120)} for e in expected[:3]]))
    if warns:
        out.append(c.finding("console.errors", "%d console warning%s" % (len(warns), "" if len(warns) == 1 else "s"), sub="warnings",
                             value=len(warns), count=len(warns), key="warnings",
                             examples=[{"text": clean(e.get("text", ""), 120)} for e in warns[:3]]))
    return len(c.console), out


def r_network_failed(c):
    n = c.network or {}
    out = []
    failed = [r for r in n.get("failed", []) if not r.get("expected")]
    expected = [r for r in n.get("failed", []) if r.get("expected")]
    if failed:
        groups = _group_by(failed, lambda r: (r.get("path") or r.get("url", ""))[:100])
        top = sorted(groups.items(), key=lambda kv: -len(kv[1]))
        out.append(c.finding("network.failed", "%d request%s failed: %s (%s)" % (
            len(failed), "" if len(failed) == 1 else "s", top[0][0], clean(top[0][1][0].get("error", ""), 60)),
            value=len(failed), unit="requests", count=len(failed), worst="%d failed" % len(failed), key=top[0][0][:80],
            examples=[{"url": k, "error": clean(g[0].get("error", ""), 60), "status": g[0].get("status")} for k, g in top[:5]]))
    if expected:
        out.append(c.finding("network.failed", "%d request%s failed by design in this state" % (len(expected), "" if len(expected) == 1 else "s"),
                             sub="expected", value=len(expected), count=len(expected), expected=True, key="expected-faults"))
    blocked = n.get("blocked", [])
    if blocked:
        hosts = sorted(set(b.get("host", "?") for b in blocked))
        out.append(c.finding("network.failed", "%d request%s blocked by the safety policy (hosts: %s)" % (
            len(blocked), "" if len(blocked) == 1 else "s", ", ".join(hosts[:4])), sub="blocked", value=len(blocked), count=len(blocked),
            key="blocked:" + ",".join(hosts[:4]), examples=[{"url": clean(b.get("url", ""), 100)} for b in blocked[:4]]))
    return n.get("requests", 0), out


# ----------------------------------------------------------------------------
# rules: overflow and overlap

def r_overflow_page(c):
    f = c.facts
    T = c.cfg.th("overflow.page")
    tol = T["tolerance_px"]
    cw = f.get("clientWidth") or c.view["width"]
    sw = f.get("scrollWidth") or cw
    over = sw - cw
    hidden_x = f.get("root_overflow_x") in ("hidden", "clip") or f.get("body_overflow_x") in ("hidden", "clip")
    culprits = []
    skip_until = -1
    for i, n in enumerate(c.nodes):
        if i <= skip_until:
            continue
        x, y, w, h = n["r"]
        if n.get("pos") == "fixed" or x + w <= cw + tol:
            continue
        j, clipped = n["p"], False
        while j >= 0:
            anc = c.nodes[j]
            if anc.get("ov") and anc["ov"][0] in ("hidden", "clip", "auto", "scroll") and anc["r"][0] + anc["r"][2] <= cw + tol:
                clipped = True
                break
            j = anc["p"]
        if clipped:
            continue
        culprits.append(i)
        skip_until = c.end[i]
    out = []
    if over > tol:
        worst = max([c.nodes[i]["r"][0] + c.nodes[i]["r"][2] - cw for i in culprits] + [over])
        textual = [i for i in culprits if c.has_text_below(i) or any(ctl["n"] == i for ctl in c.controls)]
        top = sorted(culprits, key=lambda i: -(c.nodes[i]["r"][0] + c.nodes[i]["r"][2]))[:T["max_culprits"]]
        examples = [c.example(i, value=num(c.nodes[i]["r"][0] + c.nodes[i]["r"][2] - cw)) for i in top]
        if hidden_x and not textual:
            out.append(c.finding("overflow.page", "content extends %s px past the right edge but overflow-x is hidden and nothing readable is cut off" % num(worst),
                                 sub="clipped", value=num(worst), unit="px", count=len(culprits), selector=examples[0]["selector"] if examples else None,
                                 examples=examples, key="clipped-decor"))
        elif not culprits and (f.get("ignored_right") or 0) > cw + tol:
            out.append(c.finding("overflow.page", "the page is %s px wider than the viewport, caused only by elements marked as tooling (data-ui-measure-ignore or data-state-switcher)" % num(over),
                                 sub="ignored", value=num(over), unit="px", count=f.get("ignored_roots", 1), key="ignored-overflow"))
        else:
            msg = "page scrolls sideways by %s px at %s px wide" % (num(max(over, worst)), c.view["width"])
            if hidden_x:
                msg = "content is cut off %s px past the right edge by overflow-x: hidden at %s px wide" % (num(worst), c.view["width"])
            out.append(c.finding("overflow.page", msg, value=num(max(over, worst)), unit="px", threshold=tol, count=max(1, len(culprits)),
                                 worst="%s px over" % num(max(over, worst)), selector=examples[0]["selector"] if examples else None,
                                 examples=examples, key=examples[0]["selector"] if examples else "page"))
    out.extend(_region_overflow(c, T, tol))
    return 1, out


def _region_overflow(c, T, tol):
    """App shells that clip the document hide sideways overflow: the main scroll region scrolls instead."""
    vw, vh = c.view["width"], c.view.get("height") or 800
    found = []
    for i, n in enumerate(c.nodes):
        if not n.get("hs"):
            continue
        j, nested = n["p"], False
        while j >= 0:
            anc = c.nodes[j]
            if anc.get("ov") and (anc["ov"][0] in ("auto", "scroll") or anc["ov"][1] in ("auto", "scroll")):
                nested = True
                break
            j = anc["p"]
        x, y, w, h = n["r"]
        if not nested and w >= T["region_min_width"] * vw and h >= T["region_min_height"] * vh:
            found.append(i)
    out = []
    for i in sorted(found, key=lambda k: -c.nodes[k]["r"][2] * c.nodes[k]["r"][3])[:2]:
        n = c.nodes[i]
        sw, cwid = n["hs"]
        right = n["r"][0] + cwid
        culprits, skip_until = [], -1
        for k in range(i + 1, c.end[i] + 1):
            if k <= skip_until:
                continue
            m = c.nodes[k]
            if m["r"][0] + m["r"][2] <= right + tol:
                continue
            j, inner = m["p"], False
            while j > i:
                anc = c.nodes[j]
                if anc.get("ov") and anc["ov"][0] in ("auto", "scroll", "hidden", "clip"):
                    inner = True
                    break
                j = anc["p"]
            if inner:
                continue
            culprits.append(k)
            skip_until = c.end[k]
        top = sorted(culprits, key=lambda k: -(c.nodes[k]["r"][0] + c.nodes[k]["r"][2]))[:T["max_culprits"]]
        examples = [c.example(k, value=num(c.nodes[k]["r"][0] + c.nodes[k]["r"][2] - right)) for k in top]
        out.append(c.finding("overflow.page", "the main scroll region scrolls sideways at %s px wide: its content is %s px wide in a %s px region" % (vw, num(sw), num(cwid)),
                             sub="region", value=num(sw - cwid), unit="px", threshold=tol, count=max(1, len(culprits)), worst="%s px over" % num(sw - cwid),
                             selector=c.path(i), examples=examples, key="region:" + c.path(i)))
    return out


def r_overflow_clip(c):
    T = c.cfg.th("overflow.clip")
    out, groups, ell = [], defaultdict(list), []
    checked = 0
    for i, n in enumerate(c.nodes):
        sw = n.get("sw")
        if not sw:
            continue
        checked += 1
        clip = max(sw[0] - sw[1], sw[2] - sw[3])
        if clip < T["min_px"]:
            continue
        if n.get("te") or n.get("lc"):
            ell.append(i)
            continue
        if not c.has_text_below(i) and not any(ctl["n"] == i or c.inside(i, ctl["n"]) for ctl in c.controls):
            continue
        groups[n["s"]].append((i, clip))
    for sel, items in sorted(groups.items(), key=lambda kv: -max(x[1] for x in kv[1])):
        worst = max(items, key=lambda x: x[1])
        out.append(c.finding("overflow.clip", "%d element%s hide content beyond their box (up to %s px)" % (len(items), "" if len(items) == 1 else "s", num(worst[1])),
                             value=num(worst[1]), unit="px", count=len(items), worst="%s px hidden" % num(worst[1]), selector=c.path(worst[0]),
                             examples=[c.example(i, value=num(cl)) for i, cl in items[:5]], key=sel))
    if ell:
        out.append(c.finding("overflow.clip", "%d element%s truncate text with an ellipsis or line clamp; confirm nothing essential is hidden" % (len(ell), "" if len(ell) == 1 else "s"),
                             sub="ellipsis", value=len(ell), count=len(ell), selector=c.path(ell[0]), examples=[c.example(i) for i in ell[:5]], key="ellipsis"))
    return checked, _cap_findings(c, "overflow.clip", out)


def r_overlap_text(c):
    T = c.cfg.th("overlap.text")
    lines = []
    for t in c.texts:
        if "tf" in c.nodes[t["n"]]:
            continue
        for lr in t["lr"]:
            lines.append((lr[1], lr[1] + lr[3], lr[0], lr[0] + lr[2], t["n"]))
    lines.sort()
    active, pairs = [], {}
    for line in lines:
        active = [a for a in active if a[1] > line[0]]
        for a in active:
            if a[4] == line[4] or c.inside(a[4], line[4]) or c.inside(line[4], a[4]) or overlay_layer(c, a[4]) != overlay_layer(c, line[4]):
                continue
            w = min(a[3], line[3]) - max(a[2], line[2])
            h = min(a[1], line[1]) - max(a[0], line[0])
            if w <= 0 or h <= 0:
                continue
            area = w * h
            smaller = min((a[3] - a[2]) * (a[1] - a[0]), (line[3] - line[2]) * (line[1] - line[0]))
            if area >= T["min_area_px"] and smaller > 0 and area / smaller >= T["min_fraction"]:
                key = (min(a[4], line[4]), max(a[4], line[4]))
                pairs[key] = max(pairs.get(key, 0), area)
        active.append(line)
    out = []
    for (a, b), area in sorted(pairs.items(), key=lambda kv: -kv[1])[:T["max_pairs"]]:
        ta, tb = c.text_of[a]["t"], c.text_of[b]["t"]
        out.append(c.finding("overlap.text", "text overlaps text: \"%s\" and \"%s\" share about %s px2" % (clean(ta, 30), clean(tb, 30), num(area, 0)),
                             value=num(area, 0), unit="px2", count=1, worst="%s px2" % num(area, 0), selector=c.path(a),
                             examples=[c.example(a, text=clean(ta, 40)), c.example(b, text=clean(tb, 40))], key="%s|%s" % (c.nodes[a]["s"], c.nodes[b]["s"])))
    return len(c.texts), out


# ----------------------------------------------------------------------------
# rules: contrast

def _media_boxes(c):
    if "media" in c._memo:
        return c._memo["media"]
    boxes = []
    for i, n in enumerate(c.nodes):
        w, h = n["r"][2], n["r"][3]
        if n["t"] in ("img", "video", "canvas", "picture", "iframe", "object") or (n["t"] == "svg" and w * h >= 2500) or n.get("bgi"):
            boxes.append((i, n["r"], n.get("bgi") or n["t"]))
    c._memo["media"] = boxes
    return boxes


def _text_over_media(c, rec):
    node = rec["n"]
    for i, r, kind in _media_boxes(c):
        if c.inside(i, node):
            continue
        for lr in rec["lr"]:
            w = min(lr[0] + lr[2], r[0] + r[2]) - max(lr[0], r[0])
            h = min(lr[1] + lr[3], r[1] + r[3]) - max(lr[1], r[1])
            if w > 1 and h > 1:
                return "gradient" if kind == "gradient" else "image"
    return None


def r_contrast_text(c):
    T = c.cfg.th("contrast.text")
    fails, unknown = {}, {}
    checked = 0
    for t in c.texts:
        if c.in_disabled(t["n"]):
            continue
        checked += 1
        reason, stops = t.get("fgu"), None
        bg = t["bg"]
        if reason is None and bg.get("c") is None:
            reason, stops = bg.get("u") or "unresolved", bg.get("st")
        if reason is None:
            reason = _text_over_media(c, t)
        fg = t["fg"] or [0, 0, 0, 1]
        alpha = fg[3] * t.get("op", 1)
        if reason:
            entry = unknown.setdefault(reason, {"items": [], "stops": None})
            entry["items"].append(t)
            if stops:
                entry["stops"] = stops
            if stops:
                entry.setdefault("worst", 99.0)
                for s in stops:
                    entry["worst"] = min(entry["worst"], contrast_ratio(composite(fg, s[:3], alpha), s[:3]))
            continue
        back = bg["c"]
        eff = composite(fg, back, alpha)
        ratio = contrast_ratio(eff, back)
        fs, fw = t.get("fs") or 16, t.get("fw") or 400
        large = fs >= T["large_px"] or (fs >= T["large_bold_px"] and fw >= T["bold_weight"])
        need = T["large"] if large else T["normal"]
        if ratio < need - 1e-9:
            key = "%s on %s%s" % (to_hex(eff), to_hex(back), " large" if large else "")
            g = fails.setdefault(key, {"items": [], "need": need, "ratio": ratio, "eff": eff, "back": back})
            g["items"].append(t)
            g["ratio"] = min(g["ratio"], ratio)
    for ctl in c.controls:
        if ctl.get("dis") or ctl.get("sr"):
            continue
        own = (ctl.get("own") or {}).get("c")
        if own is None:
            continue
        for kind, spec in (("field text", ctl.get("fv")), ("placeholder", ctl.get("ph"))):
            if not spec or not spec.get("c"):
                continue
            checked += 1
            fg = spec["c"]
            eff = composite(fg, own, fg[3] * spec.get("op", 1))
            ratio = contrast_ratio(eff, own)
            fs, fw = spec.get("fs") or 16, spec.get("fw") or 400
            large = fs >= T["large_px"] or (fs >= T["large_bold_px"] and fw >= T["bold_weight"])
            need = T["large"] if large else T["normal"]
            if ratio < need - 1e-9:
                key = "%s on %s %s" % (to_hex(eff), to_hex(own), kind)
                g = fails.setdefault(key, {"items": [], "need": need, "ratio": ratio, "eff": eff, "back": own, "controls": True})
                g["items"].append({"n": ctl["n"], "t": (spec.get("t") or kind)})
                g["ratio"] = min(g["ratio"], ratio)
    out = []
    for key, g in sorted(fails.items(), key=lambda kv: kv[1]["ratio"]):
        worst = min(g["items"], key=lambda t: t["n"])
        out.append(c.finding("contrast.text", "%d text element%s at %.2f:1, needs %s:1 (%s)" % (
            len(g["items"]), "" if len(g["items"]) == 1 else "s", g["ratio"], num(g["need"]), key),
            value=round(g["ratio"], 2), threshold=g["need"], unit=":1", count=len(g["items"]), worst="%.2f:1 (needs %s)" % (g["ratio"], num(g["need"])),
            selector=c.path(worst["n"]), key=key,
            examples=[c.example(t["n"], text=clean(t["t"], 40), value=round(g["ratio"], 2)) for t in g["items"][:5]]))
    for reason, g in sorted(unknown.items()):
        items = g["items"]
        extra = ""
        if g.get("worst") is not None and g["stops"]:
            extra = "; lowest gradient stop gives %.2f:1" % g["worst"]
        out.append(c.finding("contrast.text", "%d text element%s sit on %s, so contrast cannot be computed%s" % (
            len(items), "" if len(items) == 1 else "s", {"image": "an image", "gradient": "a gradient"}.get(reason, reason), extra),
            sub="unknown", value="unknown", count=len(items), worst="unknown (%s)" % reason, selector=c.path(items[0]["n"]), unknown=True,
            key="unknown:" + reason, examples=[c.example(t["n"], text=clean(t["t"], 40)) for t in items[:5]]))
    return checked, out


_TEXT_FIELDS = ("text", "email", "password", "search", "tel", "url", "number", "date", "datetime-local", "month", "week", "time")


def r_contrast_ui(c):
    T = c.cfg.th("contrast.ui")
    groups = {}
    checked = 0
    for ctl in c.controls:
        if ctl.get("dis") or ctl.get("sr"):
            continue
        n = c.nodes[ctl["n"]]
        kind = ctl["k"]
        behind = ctl.get("behind") or {}
        if behind.get("c") is None:
            continue
        back = behind["c"]
        best, what = None, None
        if kind in ("select", "textarea") or (kind.startswith("input:") and kind[6:] in _TEXT_FIELDS):
            checked += 1
            best, what = 1.0, "no visible edge"
            if n.get("bw") and n.get("bc") and n["bc"][3] > 0 and max(n["bw"]) >= 1:
                eff = composite(n["bc"], back, n["bc"][3])
                ratio = contrast_ratio(eff, back)
                if ratio > best:
                    best, what = ratio, "border"
            if n.get("bg") and n["bg"][3] > 0.05:
                eff = composite(n["bg"], back, n["bg"][3])
                ratio = contrast_ratio(eff, back)
                if ratio > best:
                    best, what = ratio, "fill"
            for sh in n.get("sh") or []:
                if not sh["i"] and sh["b"] == 0 and sh["s"] >= 1 and sh["c"][3] > 0.05:
                    ratio = contrast_ratio(composite(sh["c"], back, sh["c"][3]), back)
                    if ratio > best:
                        best, what = ratio, "ring"
        elif ctl.get("ico") and ctl["ico"].get("c"):
            checked += 1
            own = (ctl.get("own") or {}).get("c") or back
            col = ctl["ico"]["c"]
            best, what = contrast_ratio(composite(col, own, col[3]), own), "icon"
        if best is not None and best < T["min"] - 1e-9:
            key = "%s|%s|%s" % (kind, what, round(best, 1))
            groups.setdefault(key, []).append((ctl["n"], best, what))
    out = []
    for key, items in sorted(groups.items(), key=lambda kv: min(x[1] for x in kv[1])):
        worst = min(items, key=lambda x: x[1])
        label = {"icon": "icon", "no visible edge": "field with no visible edge"}.get(worst[2], "field %s" % worst[2])
        out.append(c.finding("contrast.ui", "%d control%s with a %s at %.2f:1, needs %s:1" % (
            len(items), "" if len(items) == 1 else "s", label, worst[1], num(T["min"])),
            value=round(worst[1], 2), threshold=T["min"], unit=":1", count=len(items), worst="%.2f:1 (needs %s)" % (worst[1], num(T["min"])),
            selector=c.path(worst[0]), key=key, examples=[c.example(i, value=round(b, 2)) for i, b, _ in items[:5]]))
    return checked, out


# ----------------------------------------------------------------------------
# rules: targets, focus, accessibility

def _spacing_exception(ctl, targets):
    """WCAG 2.5.8: an undersized target passes when a 24 px circle on its center touches no other target
    and no other undersized target's circle."""
    x, y, w, h = ctl["tr"]
    cx, cy = x + w / 2.0, y + h / 2.0
    for other in targets:
        if other is ctl:
            continue
        ox, oy, ow, oh = other["tr"]
        ocx, ocy = ox + ow / 2.0, oy + oh / 2.0
        if (ox <= cx <= ox + ow and oy <= cy <= oy + oh) or (x <= ocx <= x + w and y <= ocy <= y + h):
            continue
        nx, ny = max(ox, min(cx, ox + ow)), max(oy, min(cy, oy + oh))
        if (cx - nx) ** 2 + (cy - ny) ** 2 < 144.0:
            return False
        if min(ow, oh) < 23.99 and (cx - ocx) ** 2 + (cy - ocy) ** 2 < 576.0:
            return False
    return True


def r_target_size(c):
    T = c.cfg.th("target.size")
    narrow = c.view["width"] <= T["warn_at_or_below_width"]
    fails, warns, extended, covered, spaced = defaultdict(list), defaultdict(list), [], [], []
    targets = [t for t in c.controls if not (t.get("dis") or t.get("sr"))]
    checked = 0
    for ctl in c.controls:
        if ctl.get("dis") or ctl.get("sr") or ctl.get("inl") or ctl.get("ine"):
            continue
        kind = ctl["k"]
        tr = ctl["tr"]
        side = min(tr[2], tr[3])
        checked += 1
        pr = ctl.get("pr")
        if pr and pr[1] >= 4 and pr[0] <= c.cfg.t("target.size", "occluded_min_hits") - 2:
            covered.append(ctl["n"])
        if side < T["fail_px"] - 0.01:
            small_ok = True
            if ctl.get("e24") == 1:
                extended.append(ctl["n"])
            elif _spacing_exception(ctl, targets):
                spaced.append(ctl["n"])
            else:
                small_ok = False
                fails["%s %spx" % (kind, num(side, 0))].append(ctl["n"])
            if small_ok and narrow and T["warn_px"] and ctl.get("e44") != 1:
                warns["%spx" % num(side, 0)].append(ctl["n"])
        elif narrow and T["warn_px"] and side < T["warn_px"] - 0.01 and ctl.get("e44") != 1:
            warns["%spx" % num(side, 0)].append(ctl["n"])
    out = []
    for key, idxs in sorted(fails.items(), key=lambda kv: min(min(c.rect(i)[2], c.rect(i)[3]) for i in kv[1])):
        side = min(min(c.rect(i)[2], c.rect(i)[3]) for i in idxs)
        out.append(c.finding("target.size", "%d target%s smaller than %s px (%s)" % (len(idxs), "" if len(idxs) == 1 else "s", num(T["fail_px"]), key),
                             value=num(side), threshold=T["fail_px"], unit="px", count=len(idxs), worst="%s px (needs %s)" % (num(side), num(T["fail_px"])),
                             selector=c.path(idxs[0]), key=key, examples=[c.example(i, value=num(min(c.rect(i)[2], c.rect(i)[3]))) for i in idxs[:5]]))
    for key, idxs in sorted(warns.items(), key=lambda kv: -len(kv[1])):
        side = min(min(c.rect(i)[2], c.rect(i)[3]) for i in idxs)
        out.append(c.finding("target.size", "%d target%s under %s px on a narrow screen (%s)" % (len(idxs), "" if len(idxs) == 1 else "s", num(T["warn_px"]), key),
                             sub="warn", value=num(side), threshold=T["warn_px"], unit="px", count=len(idxs), worst="%s px (aim for %s)" % (num(side), num(T["warn_px"])),
                             selector=c.path(idxs[0]), key="warn:" + key, examples=[c.example(i) for i in idxs[:5]]))
    if covered:
        out.append(c.finding("target.size", "%d control%s have corners covered by another element" % (len(covered), "" if len(covered) == 1 else "s"),
                             sub="occluded", value=len(covered), count=len(covered), selector=c.path(covered[0]), key="occluded", examples=[c.example(i) for i in covered[:5]]))
    if extended:
        out.append(c.finding("target.size", "%d small control%s have a hit area of at least %s px through padding or a pseudo-element" % (
            len(extended), "" if len(extended) == 1 else "s", num(T["fail_px"])), sub="extended", value=len(extended), count=len(extended),
            selector=c.path(extended[0]), key="extended", examples=[c.example(i) for i in extended[:3]]))
    if spaced:
        out.append(c.finding("target.size", "%d target%s under %s px pass the WCAG spacing exception (no other target within a 24 px circle)" % (
            len(spaced), "" if len(spaced) == 1 else "s", num(T["fail_px"])), sub="spaced", value=len(spaced), count=len(spaced),
            selector=c.path(spaced[0]), key="spaced", examples=[c.example(i) for i in spaced[:3]]))
    return checked, out


def _ring_kinds(cur, base):
    kinds = []
    base = base or {}
    o, bo = cur.get("o"), base.get("o")
    if o and o[0] not in ("none", "hidden") and o[1] > 0 and (o[2] is None or o[2][3] > 0.05) and (not bo or o != bo):
        kinds.append("outline")
    new = [s for s in (cur.get("sh") or []) if s not in (base.get("sh") or [])]
    if any(s["b"] > 0 or s["s"] > 0 for s in new):
        kinds.append("box-shadow")
    if base:
        if (cur["bw"] != base["bw"] or cur["bc"] != base["bc"]) and any(cur["bw"]):
            kinds.append("border")
        for label, key in (("background", "bg"), ("color", "fg"), ("text-decoration", "td"), ("transform", "tf"), ("filter", "f"), ("opacity", "op")):
            if cur.get(key) != base.get(key):
                kinds.append(label)
    return kinds


def _change_ratio(cur, base, behind):
    best = 1.0
    back = (behind or {}).get("c") or [255, 255, 255]
    pairs = [(cur.get("bg"), base.get("bg")), (cur.get("fg"), base.get("fg"))]
    pairs += list(zip(cur.get("bc") or [], base.get("bc") or []))
    for a, b in pairs:
        if a and b and a != b:
            best = max(best, contrast_ratio(composite(a, back, a[3]), composite(b, back, b[3])))
    return best


def _ring_contrast(stop):
    cur = stop["cur"]
    back = (stop.get("behind") or {}).get("c")
    if back is None:
        return None
    best = None
    o = cur.get("o")
    if o and o[0] not in ("none", "hidden", "auto") and o[1] > 0 and o[2] and o[2][3] > 0.05:
        best = contrast_ratio(composite(o[2], back, o[2][3]), back)
    for s in cur.get("sh") or []:
        if not s["i"] and s["s"] >= 1 and s["b"] == 0:
            r = contrast_ratio(composite(s["c"], back, s["c"][3]), back)
            best = r if best is None else max(best, r)
    return best


def focus_state(stop, T):
    """Classify one tab stop: ('ok'|'subtle'|'none', kinds, ring contrast)."""
    kinds = _ring_kinds(stop["cur"], stop.get("base"))
    for anc in stop.get("anc") or []:
        ak = _ring_kinds(anc["cur"], anc.get("base"))
        if "outline" in ak or "box-shadow" in ak:
            kinds = kinds + ["ancestor:" + k for k in ak if k in ("outline", "box-shadow")]
    strong = [k for k in kinds if k in ("outline", "box-shadow") or k.startswith("ancestor:")]
    if strong:
        return "ok", kinds, _ring_contrast(stop)
    if kinds:
        if stop.get("base") and _change_ratio(stop["cur"], stop["base"], stop.get("behind")) < T["subtle_change_min"] and not set(kinds) & {"border", "text-decoration", "transform", "filter"}:
            return "subtle", kinds, None
        return "ok", kinds, None
    return "none", kinds, None


def r_focus_visible(c):
    T = c.cfg.th("focus.visible")
    fo = c.focus
    if not fo or not fo.get("stops"):
        return 0, []
    none, subtle, low = [], [], []
    for stop in fo["stops"]:
        state, kinds, ring = focus_state(stop, T)
        if state == "none":
            none.append(stop)
        elif state == "subtle":
            subtle.append(stop)
        elif ring is not None and ring < T["ring_contrast_min"] - 1e-9:
            low.append((stop, ring))
    out = []
    total = len(fo["stops"])
    if none:
        names = ", ".join(s["s"] for s in none[:3])
        out.append(c.finding("focus.visible", "%d of %d tab stops show no visible focus indicator (%s)" % (len(none), total, names),
                             value=len(none), threshold=0, unit="stops", count=len(none), worst="%d of %d stops" % (len(none), total),
                             selector=none[0]["s"], key=none[0]["s"],
                             examples=[{"selector": s["s"], "rect": s["r"], "text": clean(s.get("txt", ""), 30)} for s in none[:5]]))
    if subtle:
        out.append(c.finding("focus.visible", "%d tab stop%s change only slightly on focus (no outline or ring)" % (len(subtle), "" if len(subtle) == 1 else "s"),
                             sub="subtle", value=len(subtle), count=len(subtle), selector=subtle[0]["s"], key="subtle:" + subtle[0]["s"],
                             examples=[{"selector": s["s"], "rect": s["r"]} for s in subtle[:5]]))
    if low:
        worst = min(low, key=lambda x: x[1])
        out.append(c.finding("focus.visible", "%d focus ring%s below %s:1 against the surrounding background" % (len(low), "" if len(low) == 1 else "s", num(T["ring_contrast_min"])),
                             sub="contrast", value=round(worst[1], 2), threshold=T["ring_contrast_min"], unit=":1", count=len(low),
                             worst="%.2f:1 (needs %s)" % (worst[1], num(T["ring_contrast_min"])), selector=worst[0]["s"], key="ring:" + worst[0]["s"],
                             examples=[{"selector": s["s"], "value": round(r, 2)} for s, r in low[:5]]))
    return total, out


def r_focus_order(c):
    T = c.cfg.th("focus.order")
    fo = c.focus
    if not fo or not fo.get("stops"):
        return 0, []
    stops = fo["stops"]
    out = []
    positive = [s for s in stops if s.get("ti", 0) > 0]
    if positive:
        out.append(c.finding("focus.order", "%d element%s use a positive tabindex, which overrides the natural order" % (len(positive), "" if len(positive) == 1 else "s"),
                             value=len(positive), count=len(positive), selector=positive[0]["s"], key="positive-tabindex",
                             examples=[{"selector": s["s"], "value": s["ti"]} for s in positive[:5]]))
    jumps = []
    for a, b in zip(stops, stops[1:]):
        ax, ay, bx, by = a["r"][0], a["r"][1], b["r"][0], b["r"][1]
        if by < ay - T["back_jump_px"]:
            jumps.append((a, b, "up %s px" % num(ay - by, 0)))
        elif abs(by - ay) <= T["row_px"] and bx < ax - T["left_jump_px"]:
            jumps.append((a, b, "left %s px in the same row" % num(ax - bx, 0)))
    if jumps:
        out.append(c.finding("focus.order", "tab order jumps backwards %d time%s: %s to %s (%s)" % (
            len(jumps), "" if len(jumps) == 1 else "s", jumps[0][0]["s"], jumps[0][1]["s"], jumps[0][2]),
            value=len(jumps), count=len(jumps), worst="%d backward jumps" % len(jumps), selector=jumps[0][1]["s"], key="back:" + jumps[0][1]["s"],
            examples=[{"selector": b["s"], "note": "after %s, %s" % (a["s"], why)} for a, b, why in jumps[:T["max_reports"]]]))
    if fo.get("wrapped") and fo.get("total") and not fo.get("truncated"):
        reached = set(s["i"] for s in stops if s["i"] >= 0)
        if len(reached) < fo["total"]:
            out.append(c.finding("focus.order", "%d of %d focusable elements were never reached by Tab" % (fo["total"] - len(reached), fo["total"]),
                                 value=fo["total"] - len(reached), count=fo["total"] - len(reached), key="unreached"))
    return len(stops), out


def r_a11y_names(c):
    groups = defaultdict(list)
    checked = 0
    for k, ctl in enumerate(c.controls):
        if ctl.get("dis"):
            continue
        checked += 1
        ax = c.ax.get(k)
        if ax is not None and ax.get("ignored"):
            continue
        name = ax.get("name") if ax is not None else ctl["nm"]["n"]
        source = ax.get("via") if ax is not None else ctl["nm"]["s"]
        if not (name or "").strip():
            groups[(ctl["k"], "none")].append(ctl["n"])
        elif source == "placeholder":
            groups[(ctl["k"], "placeholder")].append(ctl["n"])
    out = []
    for (kind, why), idxs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if why == "none":
            out.append(c.finding("a11y.names", "%d %s control%s have no accessible name" % (len(idxs), kind, "" if len(idxs) == 1 else "s"),
                                 value=len(idxs), count=len(idxs), worst="%d unnamed" % len(idxs), selector=c.path(idxs[0]), key="%s:none:%s" % (kind, c.nodes[idxs[0]]["s"]),
                                 examples=[c.example(i) for i in idxs[:5]]))
        else:
            out.append(c.finding("a11y.names", "%d %s field%s are named only by placeholder text" % (len(idxs), kind, "" if len(idxs) == 1 else "s"),
                                 sub="placeholder", value=len(idxs), count=len(idxs), selector=c.path(idxs[0]), key="%s:placeholder" % kind,
                                 examples=[c.example(i) for i in idxs[:5]]))
    return checked, out


def r_a11y_clickable(c):
    groups = defaultdict(list)
    for cl in c.clicks:
        if cl.get("fd"):
            key = ("wrapper", False)
        elif cl["via"] == "onclick" and not (cl.get("attr") or cl.get("cur")):
            key = ("handler", False)
        else:
            key = ("onclick" if cl["via"] == "onclick" else "cursor", cl["ti"])
        groups[key].append(cl["n"])
    out = []
    for (via, has_ti), idxs in sorted(groups.items()):
        if via == "wrapper":
            msg, sub = "%d element%s with click handlers wrap keyboard-reachable controls; confirm the same action works by keyboard", "wrapper"
        elif via == "handler":
            msg, sub = "%d element%s have click handlers but no pointer cursor (often event wrappers; React sets a no-op onclick on any element with onClick)", "handler"
        elif via == "onclick" and not has_ti:
            msg, sub = "%d element%s handle clicks but are not buttons or links and cannot be reached with the keyboard", None
        elif via == "onclick":
            msg, sub = "%d element%s handle clicks and are focusable but have no role", "tabindex"
        else:
            msg, sub = "%d element%s look clickable (cursor: pointer) but are not buttons or links", "cursor"
        out.append(c.finding("a11y.clickable", msg % (len(idxs), "" if len(idxs) == 1 else "s"), sub=sub, value=len(idxs), count=len(idxs),
                             selector=c.path(idxs[0]), key="%s:%s" % (via, c.nodes[idxs[0]]["s"]), worst="%d element%s" % (len(idxs), "" if len(idxs) == 1 else "s"),
                             examples=[c.example(i, text=clean(c.text_of[i]["t"], 30) if i in c.text_of else None) for i in idxs[:5]]))
    return len(c.clicks), out


_GENERIC_ALT = re.compile(r"^(image|photo|picture|icon|img|graphic|screenshot)?\s*\d*$|\.(png|jpe?g|gif|svg|webp|avif)$", re.I)


def r_a11y_structure(c):
    T = c.cfg.th("a11y.structure")
    out = []
    f = c.facts
    h1 = [h for h in c.headings if h["l"] == 1]
    if len(h1) != T["max_h1"]:
        out.append(c.finding("a11y.structure", "page has %d h1 headings; expected exactly %d" % (len(h1), T["max_h1"]),
                             value=len(h1), threshold=T["max_h1"], count=1, selector=c.path(h1[0]["n"]) if h1 else None, key="h1-count",
                             examples=[c.example(h["n"], text=clean(h["t"], 40)) for h in h1[:4]]))
    skips = []
    prev = None
    for h in c.headings:
        if prev is not None and h["l"] > prev["l"] + 1:
            skips.append((prev, h))
        prev = h
    if skips:
        a, b = skips[0]
        out.append(c.finding("a11y.structure", "heading levels skip: h%d is followed by h%d (%s)" % (a["l"], b["l"], clean(b["t"], 40)),
                             value="h%d>h%d" % (a["l"], b["l"]), count=len(skips), selector=c.path(b["n"]), key="heading-skip",
                             examples=[c.example(y["n"], text=clean(y["t"], 40), note="after h%d" % x["l"]) for x, y in skips[:4]]))
    mains = [i for i, n in enumerate(c.nodes) if n["t"] == "main" or n.get("role") == "main"]
    if len(mains) != 1:
        out.append(c.finding("a11y.structure", "page has %d main landmarks; expected 1" % len(mains), value=len(mains), threshold=1, count=1, key="main-count"))
    navs = [i for i, n in enumerate(c.nodes) if n["t"] == "nav" or n.get("role") == "navigation"]
    unlabeled = [i for i in navs if not c.nodes[i].get("al")]
    if len(navs) > 1 and len(unlabeled) > 1:
        out.append(c.finding("a11y.structure", "%d nav landmarks without distinct aria-label values" % len(unlabeled), value=len(unlabeled), count=len(unlabeled),
                             selector=c.path(unlabeled[0]), key="nav-labels", examples=[c.example(i) for i in unlabeled[:4]]))
    if not f.get("lang"):
        out.append(c.finding("a11y.structure", "the html element has no lang attribute", value="missing", count=1, key="lang"))
    if not (f.get("title") or "").strip():
        out.append(c.finding("a11y.structure", "the page has no title", value="missing", count=1, key="title"))
    missing = [im for im in c.images if im["alt"] is None and im.get("role") not in ("presentation", "none")]
    if missing:
        out.append(c.finding("a11y.structure", "%d image%s have no alt attribute" % (len(missing), "" if len(missing) == 1 else "s"), sub="img-alt",
                             value=len(missing), count=len(missing), worst="%d without alt" % len(missing), selector=c.path(missing[0]["n"]),
                             key="img-alt:" + c.nodes[missing[0]["n"]]["s"], examples=[c.example(im["n"], text=im.get("src")) for im in missing[:5]]))
    weak = [im for im in c.images if im["alt"] and _GENERIC_ALT.search(im["alt"].strip())]
    if weak:
        out.append(c.finding("a11y.structure", "%d image%s use a filename or generic word as alt text" % (len(weak), "" if len(weak) == 1 else "s"), sub="img-alt-quality",
                             value=len(weak), count=len(weak), selector=c.path(weak[0]["n"]), key="img-alt-quality",
                             examples=[c.example(im["n"], text=clean(im["alt"], 40)) for im in weak[:5]]))
    return len(c.headings) + len(c.images) + 4, out


# ----------------------------------------------------------------------------
# rules: grid, components, scale

def _token_values(c, kind):
    return [v for v in ((c.project.get("tokens") or {}).get(kind) or []) if isinstance(v, (int, float))]


def r_grid_spacing(c):
    T = c.cfg.th("grid.spacing")
    base, lbase, tol, lmin = T["base"], T["layout_base"], T["tolerance"], T["layout_min_px"]
    tokens = _token_values(c, "spacing")
    seen = defaultdict(lambda: {"count": 0, "props": Counter(), "items": [], "layout": False})
    total = off = 0
    for i, prop, v in c.spacing_values():
        a = abs(v)
        total += 1
        if any(abs(a - t) <= tol for t in tokens):
            continue
        layout = c.is_layout(i) and a >= lmin
        use = lbase if layout else base
        if on_grid(a, use, tol):
            continue
        off += 1
        entry = seen[(num(a, 2), use)]
        entry["count"] += 1
        entry["props"][prop.split("-")[0] if "gap" not in prop else "gap"] += 1
        entry["layout"] = layout
        if len(entry["items"]) < T["max_examples"] * 4:
            entry["items"].append((i, prop, v))
    c._memo["spacing_stats"] = {"total": total, "off_grid": off}
    out = []
    for (a, use), e in sorted(seen.items(), key=lambda kv: (-kv[1]["count"], kv[0][0])):
        lo, hi = nearest_grid(a, use)
        near = "%s or %s" % (num(lo), num(hi)) if lo > 0 else num(hi)
        props = ", ".join("%s x%d" % (k, v) for k, v in e["props"].most_common(3))
        items = e["items"]
        out.append(c.finding("grid.spacing", "%spx used %d time%s (%s), off the %dpx %sgrid; nearest on-grid %s" % (
            num(a), e["count"], "" if e["count"] == 1 else "s", props, use, "layout " if use == lbase and e["layout"] else "", near),
            value=num(a), threshold=use, unit="px", count=e["count"], worst="%spx -> %s" % (num(a), near),
            selector=c.path(items[0][0]), key="%s@%d" % (num(a), use),
            fix=("set an explicit value from the scale; this looks like a browser default in em" if (a % 1 > 0.05 and a % 1 < 0.95 and "margin" in e["props"]) else "use %s" % near),
            examples=[c.example(i, value=num(v), property=p) for i, p, v in items[:T["max_examples"]]]))
    return total, _cap_findings(c, "grid.spacing", out)


def r_grid_size(c):
    T = c.cfg.th("grid.size")
    base, tol = T["base"], T["tolerance"]
    tokens = _token_values(c, "spacing")
    seen = defaultdict(list)
    checked = 0
    control_nodes = {}
    for ctl in c.controls:
        control_nodes[ctl["n"]] = ctl
    for i, n in enumerate(c.nodes):
        w, h = n["r"][2], n["r"][3]
        ctl = control_nodes.get(i)
        dims = []
        if ctl and not ctl.get("dis") and not ctl.get("inl") and not ctl.get("sr"):
            kind = ctl["k"]
            native_choice = kind in ("input:checkbox", "input:radio") and not ctl.get("ha")
            if kind == "link":
                boxed = bool(n.get("bg") and n.get("bw"))
            else:
                boxed = bool(n.get("bg") or n.get("bw") or kind.startswith("input") or kind in ("button", "select", "textarea"))
            if boxed and not native_choice:
                dims.append(("height", h))
                if ctl.get("wa"):
                    dims.append(("width", w))
        elif n["t"] in ("svg", "img") and max(w, h) <= T["icon_max_px"] and min(w, h) >= 8:
            dims.extend([("width", w), ("height", h)])
        for label, v in dims:
            checked += 1
            if any(abs(v - t) <= tol for t in tokens) or on_grid(v, base, tol):
                continue
            seen[(num(v), label)].append(i)
    out = []
    for (v, label), idxs in sorted(seen.items(), key=lambda kv: (-len(kv[1]), kv[0][0])):
        lo, hi = nearest_grid(v, base)
        near = "%s or %s" % (num(lo), num(hi))
        out.append(c.finding("grid.size", "%d box%s %spx %s, off the %dpx grid; nearest on-grid %s" % (
            len(idxs), "" if len(idxs) == 1 else "es", num(v), label, base, near),
            value=num(v), threshold=base, unit="px", count=len(idxs), worst="%spx %s -> %s" % (num(v), label, near),
            selector=c.path(idxs[0]), key="%s-%s" % (label, num(v)), fix="use %s" % near,
            examples=[c.example(i, value=num(v), property=label) for i in idxs[:T["max_examples"]]]))
    return checked, _cap_findings(c, "grid.size", out)


def _mode(values):
    counts = Counter(values)
    top = counts.most_common(2)
    if not top:
        return None, 0
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None, top[0][1]
    return top[0][0], top[0][1]


def _bucket(v, tol):
    return None if v is None else round(v / tol) * tol


def r_component_conformance(c):
    T = c.cfg.th("component.conformance")
    tol = T["tolerance"]
    groups = defaultdict(list)
    for i, n in enumerate(c.nodes):
        if n["t"] in ("html", "body", "main", "svg") or n.get("d") == "inline":
            continue
        cls = n.get("c")
        if not cls and n["t"] not in ("li", "tr", "article"):
            continue
        if n["r"][2] < T["min_box_px"] or n["r"][3] < T["min_box_px"]:
            continue
        kids = [c.nodes[k]["t"] for k in c.children[i]][:8]
        groups[(n["t"], " ".join(sorted((cls or "").split())), ",".join(kids))].append(i)
    findings = []
    total_groups = 0
    inventory = []
    for sig, idxs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(idxs) < T["min_instances"] or len(idxs) > T["max_instances"]:
            continue
        total_groups += 1
        prints = {}
        for i in idxs:
            n = c.nodes[i]
            fp = {}
            pad = n.get("pad") or [0, 0, 0, 0]
            for k, side in enumerate(("top", "right", "bottom", "left")):
                fp["padding-" + side] = _bucket(pad[k], tol)
            gap = n.get("gap")
            if gap:
                fp["row-gap"], fp["column-gap"] = _bucket(gap[0], tol), _bucket(gap[1], tol)
            kids = [k for k in c.children[i] if c.nodes[k].get("d") != "inline" and c.nodes[k]["r"][2] > 0]
            if kids:
                r0 = c.nodes[i]["r"]
                k0 = c.nodes[kids[0]]["r"]
                content_w = r0[2] - sum(v or 0 for v in (n.get("pad") or [0, 0, 0, 0])[1::2])
                distributed = bool(n.get("jc"))
                if T.get("child_offsets"):
                    if k0[2] >= 0.9 * content_w:
                        fp["first-child-left"] = _bucket(k0[0] - r0[0], tol)
                    if not distributed:
                        fp["first-child-top"] = _bucket(k0[1] - r0[1], tol)
                stacked = all(c.nodes[b]["r"][1] >= c.nodes[a]["r"][1] + c.nodes[a]["r"][3] - 1 for a, b in zip(kids, kids[1:]))
                inrow = all(c.nodes[b]["r"][0] >= c.nodes[a]["r"][0] + c.nodes[a]["r"][2] - 1 for a, b in zip(kids, kids[1:]))
                for g, (a, b) in enumerate(zip(kids[:6], kids[1:7])):
                    ra, rb = c.nodes[a]["r"], c.nodes[b]["r"]
                    if distributed:
                        break
                    if stacked:
                        fp["gap-%d" % (g + 1)] = _bucket(rb[1] - (ra[1] + ra[3]), tol)
                    elif inrow:
                        fp["gap-%d" % (g + 1)] = _bucket(rb[0] - (ra[0] + ra[2]), tol)
            prints[i] = fp
        fields = set()
        for fp in prints.values():
            fields.update(fp.keys())
        modes, deviants = {}, defaultdict(list)
        for field in sorted(fields):
            vals = [prints[i].get(field) for i in idxs]
            mode, cnt = _mode(vals)
            modes[field] = mode
            if mode is None or cnt < 2:
                continue
            for i in idxs:
                v = prints[i].get(field)
                if v != mode:
                    deviants[i].append((field, v, mode))
        if len(inventory) < T["max_groups"] * 2:
            inventory.append({"signature": re.sub(r":nth-of-type\(\d+\)$", "", c.path(idxs[0])), "instances": len(idxs), "deviants": len(deviants),
                              "mode": dict((k, num(v)) for k, v in sorted(modes.items()) if v is not None)})
        if deviants:
            tag, cls, kids = sig
            label = c.nodes[idxs[0]]["s"]
            worst = max(deviants.items(), key=lambda kv: max(abs((d[1] or 0) - (d[2] or 0)) for d in kv[1]))
            wf = max(worst[1], key=lambda d: abs((d[1] or 0) - (d[2] or 0)))
            findings.append(c.finding("component.conformance", "%s x%d: %d deviate from the group (%s %s, group uses %s)" % (
                label, len(idxs), len(deviants), wf[0], num(wf[1]) if wf[1] is not None else "none", num(wf[2]) if wf[2] is not None else "none"),
                value=num(wf[1]) if wf[1] is not None else None, threshold=num(wf[2]) if wf[2] is not None else None, unit="px", count=len(deviants),
                worst="%s %s vs %s" % (wf[0], num(wf[1]), num(wf[2])), selector=c.path(worst[0]), key="%s|%s" % (label, wf[0]),
                detail={"instances": len(idxs), "mode": {k: num(v) for k, v in modes.items() if v is not None}},
                examples=[c.example(i, property=d[0], value=num(d[1]), mode=num(d[2])) for i, ds in list(deviants.items())[:5] for d in ds[:1]]))
    c._memo["component_groups"] = total_groups
    c.details["component.conformance"] = {"groups": inventory, "examined": total_groups}
    return total_groups, _cap_findings(c, "component.conformance", findings, T["max_groups"])


def r_scale_spacing(c):
    T = c.cfg.th("scale.spacing")
    q = T["round_to"]
    counts = Counter()
    for i, prop, v in c.spacing_values():
        counts[abs(round(v / q) * q)] += 1
    distinct = sorted(counts)
    if len(distinct) > T["max_values"]:
        listing = ", ".join("%s (%d)" % (num(v), counts[v]) for v in distinct[:14])
        return len(counts), [c.finding("scale.spacing", "%d distinct spacing values in use, more than %d: %s" % (len(distinct), T["max_values"], listing),
                                       value=len(distinct), threshold=T["max_values"], unit="values", count=len(distinct),
                                       worst="%d values (limit %d)" % (len(distinct), T["max_values"]), key="spacing-values",
                                       detail={"values": {num(v): counts[v] for v in distinct}})]
    return len(counts), []


# ----------------------------------------------------------------------------
# rules: type

def r_type_scale(c):
    T = c.cfg.th("type.scale")
    q = T["round_to"]
    sizes, weights, small = Counter(), Counter(), []
    for t in c.texts:
        fs = t.get("fs")
        if not fs:
            continue
        sizes[round(fs / q) * q] += t["len"]
        weights[int(round((t.get("fw") or 400) / 100.0)) * 100] += t["len"]
        if fs < T["min_px"] - 0.01:
            small.append(t)
    out = []
    ordered = sorted(sizes)
    if len(ordered) > T["max_sizes"]:
        out.append(c.finding("type.scale", "%d font sizes in use, more than %d: %s" % (len(ordered), T["max_sizes"], ", ".join(str(num(s)) for s in ordered)),
                             value=len(ordered), threshold=T["max_sizes"], unit="sizes", count=len(ordered), worst="%d sizes (limit %d)" % (len(ordered), T["max_sizes"]),
                             key="sizes", detail={"sizes": {num(s): sizes[s] for s in ordered}}))
    if len(weights) > T["max_weights"]:
        out.append(c.finding("type.scale", "%d font weights in use, more than %d: %s" % (len(weights), T["max_weights"], ", ".join(str(w) for w in sorted(weights))),
                             value=len(weights), threshold=T["max_weights"], unit="weights", count=len(weights), key="weights",
                             detail={"weights": {str(w): weights[w] for w in sorted(weights)}}))
    close = [(a, b) for a, b in zip(ordered, ordered[1:]) if b / a < T["min_adjacent_ratio"] - 1e-9]
    if close:
        a, b = min(close, key=lambda p: p[1] / p[0])
        out.append(c.finding("type.scale", "sizes %spx and %spx are only %.2fx apart, too close to read as different levels (needs %sx)" % (
            num(a), num(b), b / a, num(T["min_adjacent_ratio"])), value=round(b / a, 2), threshold=T["min_adjacent_ratio"], unit="x", count=len(close),
            worst="%spx vs %spx = %.2fx" % (num(a), num(b), b / a), key="ratio:%s-%s" % (num(a), num(b))))
    if small:
        smallest = min(small, key=lambda t: t["fs"])
        groups = _group_by(small, lambda t: num(t["fs"]))
        out.append(c.finding("type.scale", "%d text element%s below %spx (smallest %spx: \"%s\")" % (
            len(small), "" if len(small) == 1 else "s", num(T["min_px"]), num(smallest["fs"]), clean(smallest["t"], 30)),
            value=num(smallest["fs"]), threshold=T["min_px"], unit="px", count=len(small), worst="%spx (min %s)" % (num(smallest["fs"]), num(T["min_px"])),
            selector=c.path(smallest["n"]), key="min:%s" % num(smallest["fs"]),
            examples=[c.example(t["n"], value=num(t["fs"]), text=clean(t["t"], 30)) for t in sorted(small, key=lambda t: t["fs"])[:5]], detail={"sizes": {k: len(v) for k, v in groups.items()}}))
    return len(c.texts), out


def _is_ui_text(c, node):
    j = node
    while j >= 0:
        if c.nodes[j]["t"] in ("button", "a", "label", "select", "option", "summary", "th", "legend") or c.nodes[j].get("role") in ("button", "link", "tab", "menuitem"):
            return True
        j = c.nodes[j]["p"]
    return False


def r_type_line_height(c):
    T = c.cfg.th("type.line_height")
    groups = defaultdict(list)
    checked = 0
    for t in c.texts:
        fs = t.get("fs") or 0
        if t.get("hd") or t["len"] < T["body_min_chars"] or not (T["body_min_px"] <= fs <= T["body_max_px"]) or _is_ui_text(c, t["n"]):
            continue
        checked += 1
        ratio = (t["lh"] / fs) if t.get("lh") else T["normal_ratio"]
        if ratio < T["min"] - 1e-9 or ratio > T["max"] + 1e-9:
            groups[(round(ratio * 20) / 20.0, t.get("lh") is None)].append(t)
    out = []
    for (ratio, normal), items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        t0 = items[0]
        out.append(c.finding("type.line_height", "%d body text block%s at line-height %s%s, outside %s to %s" % (
            len(items), "" if len(items) == 1 else "s", num(ratio), " (normal)" if normal else "", num(T["min"]), num(T["max"])),
            value=num(ratio), threshold="%s-%s" % (num(T["min"]), num(T["max"])), unit="x", count=len(items), worst="%s (allowed %s-%s)" % (num(ratio), num(T["min"]), num(T["max"])),
            selector=c.path(t0["n"]), key="lh:%s" % num(ratio), examples=[c.example(t["n"], value=num(ratio), text=clean(t["t"], 30)) for t in items[:5]]))
    return checked, out


def r_type_measure(c):
    T = c.cfg.th("type.measure")
    narrow = c.view["width"] <= T["narrow_width"]
    lo = T["min_ch_narrow"] if narrow else T["min_ch"]
    hi = T["max_ch"]
    wide, thin = defaultdict(list), defaultdict(list)
    checked = 0
    for t in c.texts:
        fs = t.get("fs") or 0
        if t.get("hd") or not t.get("cw") or not t.get("ch") or not (T["body_min_px"] <= fs <= T["body_max_px"]) or _is_ui_text(c, t["n"]):
            continue
        measure = t["cw"] / t["ch"]
        checked += 1
        if t["ln"] >= T["min_lines_upper"] and measure > hi + 1e-9:
            wide[int(round(measure / 5.0)) * 5].append((t, measure))
        elif t["ln"] >= T["min_lines_lower"] and measure < lo - 1e-9:
            thin[int(round(measure / 5.0)) * 5].append((t, measure))
    out = []
    for label, groups, bound, verb in (("wide", wide, hi, "longer than"), ("narrow", thin, lo, "shorter than")):
        for _, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            worst = max(items, key=lambda x: abs(x[1] - bound))
            out.append(c.finding("type.measure", "%d paragraph%s with lines %s %s characters (%s ch)" % (
                len(items), "" if len(items) == 1 else "s", verb, num(bound), num(worst[1], 0)),
                value=num(worst[1], 0), threshold=bound, unit="ch", count=len(items), worst="%s ch (%s %s)" % (num(worst[1], 0), "max" if label == "wide" else "min", num(bound)),
                selector=c.path(worst[0]["n"]), key="measure-%s-%s" % (label, num(worst[1], 0)),
                examples=[c.example(t["n"], value=num(m, 0), text=clean(t["t"], 30)) for t, m in items[:5]]))
    return checked, out


_NUMERIC = re.compile(r"^[\s(+\-\u2212\u2013$\u20ac\u00a3\u00a5]*\d[\d,.\s]*\s*(?:%|[kKmMbB]|x|ms|s|px|[KMG]B)?\)?$")


def r_type_numeric(c):
    T = c.cfg.th("type.numeric")
    out = []
    checked = 0
    for tbl in c.tables:
        for ci, cells in enumerate(tbl["cols"]):
            cells = [x for x in (cells or []) if x["t"]]
            if len(cells) < T["min_cells"]:
                continue
            numeric = [x for x in cells if _NUMERIC.match(x["t"])]
            if len(numeric) / float(len(cells)) < T["numeric_share"]:
                continue
            checked += 1
            right = sum(1 for x in numeric if x["ta"] in ("right", "end")) / float(len(numeric))
            tab = sum(1 for x in numeric if "tabular-nums" in x["fvn"] or "tnum" in x["ffs"]) / float(len(numeric))
            missing = []
            if right < 0.8:
                missing.append("right alignment")
            if tab < 0.8:
                missing.append("tabular numerals")
            if missing:
                out.append(c.finding("type.numeric", "numeric column %d in table %s needs %s (e.g. \"%s\")" % (ci + 1, c.path(tbl["n"]), " and ".join(missing), clean(numeric[0]["t"], 20)),
                                     value=" + ".join(missing), count=len(numeric), selector=c.path(tbl["n"]), key="%s:col%d" % (c.nodes[tbl["n"]]["s"], ci + 1),
                                     worst="column %d: %s" % (ci + 1, " + ".join(missing)),
                                     examples=[{"selector": c.path(tbl["n"]), "text": x["t"], "align": x["ta"]} for x in numeric[:3]]))
    return checked, out


# ----------------------------------------------------------------------------
# rules: alignment and rhythm

def _container(c, i):
    """Nearest ancestor that draws a visible box, or the page. Edges are only expected to align inside one container."""
    j = c.nodes[i]["p"]
    while j >= 0:
        n = c.nodes[j]
        if (n.get("bg") and n["bg"][3] > 0.5) or n.get("bw") or n.get("sh") or n.get("bgi"):
            return j
        j = n["p"]
    return -1


def _positioned(c, i):
    """True for content that is not part of the normal flow: fixed, absolute, or sticky boxes, dialogs, and anything inside them."""
    key = ("positioned", i)
    if key not in c._memo:
        n = c.nodes[i]
        c._memo[key] = bool(n.get("pos") in ("fixed", "absolute", "sticky") or n["t"] == "dialog" or n.get("role") in ("dialog", "alertdialog")
                            or (n["p"] >= 0 and _positioned(c, n["p"])))
    return c._memo[key]


def _edge_findings(c, T, pairs, edge):
    """One finding per group of near-miss pairs that share the same two edge positions. `edge` is left, right, or top. Each pair is
    (node, edge position, node, edge position, difference): an element can carry two positions (its box and its text), so a pair records its own."""
    out = []
    sub = None if edge == "left" else edge
    for (x1, x2), items in sorted(pairs.items(), key=lambda kv: -len(kv[1]))[:T["max_pairs"]]:
        els = set()
        for p_, pv, q_, qv, _ in items:
            els.add((p_, pv))
            els.add((q_, qv))
        low = sorted(set(e for e, v in els if abs(v - x1) < 0.011))
        high = sorted(set(e for e, v in els if abs(v - x2) < 0.011))
        if not low or not high:
            continue
        minority, majority, mx, jx = (low, high, x1, x2) if len(low) <= len(high) else (high, low, x2, x1)
        lead = min(minority, key=lambda e: c.rect(e)[1])
        other = min(majority, key=lambda e: c.rect(e)[1])
        d = abs(x2 - x1)
        many = len(minority) != 1
        if edge == "left":
            message = "%d element%s start%s at %spx, %s px from the %spx edge shared by %d other element%s in the same container" % (
                len(minority), "" if not many else "s", "" if many else "s", num(mx), num(d), num(jx), len(majority), "" if len(majority) == 1 else "s")
        elif edge == "right":
            message = "%d element%s end%s at %spx, %s px from the %spx right edge shared by %d other element%s in the same container" % (
                len(minority), "" if not many else "s", "" if many else "s", num(mx), num(d), num(jx), len(majority), "" if len(majority) == 1 else "s")
        else:
            message = "%d element%s in a row start%s at y %spx, %s px from the %spx top edge shared by %d other element%s in the same container" % (
                len(minority), "" if not many else "s", "" if many else "s", num(mx), num(d), num(jx), len(majority), "" if len(majority) == 1 else "s")
        out.append(c.finding("align.near_miss", message, sub=sub,
                             value=num(d), threshold="0 or >%s" % num(T["max_px"]), unit="px", count=len(minority), worst="%s px apart" % num(d), selector=c.path(lead),
                             key=("%s|%s" % (num(d), c.nodes[lead]["s"])) if edge == "left" else "%s:%s|%s" % (edge, num(d), c.nodes[lead]["s"]),
                             detail={"edge": edge}, examples=[c.example(lead, **{edge: mx}), c.example(other, **{edge: jx})]))
    return out


def r_align_near_miss(c):
    T = c.cfg.th("align.near_miss")
    edges = T.get("edges") or ["left"]
    cands = []
    right_text = []
    for t in c.texts:
        if t["len"] < T["min_text_chars"] or _is_ui_text(c, t["n"]) or _positioned(c, t["n"]):
            continue
        r = c.rect(t["n"])
        if t.get("ta") in ("start", "left"):
            cands.append((t["n"], t["tx"], r[1], r[1] + r[3], t["tx"] + (t["lr"][0][2] if t["lr"] else r[2]), "text"))
        elif t.get("ta") in ("right", "end") and t["lr"] and not _in_landmark(c, t["n"], ("table",), ("table", "grid", "treegrid")):
            lx, _ly, lw, _lh = t["lr"][0]
            right_text.append((t["n"], lx, r[1], r[1] + r[3], lx + lw, "rtext"))
    ctl_nodes = set()
    for ctl in c.controls:
        if ctl.get("dis") or ctl.get("inl") or ctl.get("sr") or ctl["k"] in ("link", "input:checkbox", "input:radio") or _positioned(c, ctl["n"]):
            continue
        r = ctl["r"]
        ctl_nodes.add(ctl["n"])
        cands.append((ctl["n"], r[0], r[1], r[1] + r[3], r[0] + r[2], "ctl"))
    for i, n in enumerate(c.nodes):
        r = n["r"]
        if _positioned(c, i):
            continue
        if n["t"] == "img" and r[2] >= 24 and r[3] >= 24:
            cands.append((i, r[0], r[1], r[1] + r[3], r[0] + r[2], "img"))
        elif (n.get("bg") and n["bg"][3] > 0.5 or n.get("bw")) and r[2] >= 80 and r[3] >= 32 and n["t"] not in ("body", "main", "html", "tr", "td", "th") and i not in ctl_nodes:
            cands.append((i, r[0], r[1], r[1] + r[3], r[0] + r[2], "box"))
    cands.sort(key=lambda x: (x[2], x[1]))
    out = []

    # left edges: elements stacked in one container whose left edges are almost, but not quite, the same
    pairs = {}
    for ai in range(len(cands)):
        a = cands[ai]
        for bi in range(ai + 1, len(cands)):
            b = cands[bi]
            if b[2] - a[3] > T["max_gap_px"]:
                break
            if b[2] < a[2] + 4 or a[0] == b[0] or c.inside(a[0], b[0]) or c.inside(b[0], a[0]):
                continue
            if _container(c, a[0]) != _container(c, b[0]):
                continue
            d = abs(a[1] - b[1])
            if d < T["min_px"] or d > T["max_px"]:
                continue
            overlap = min(a[4], b[4]) - max(a[1], b[1])
            if overlap < min(T["min_overlap_px"], 0.3 * min(a[4] - a[1], b[4] - b[1])):
                continue
            key = (num(min(a[1], b[1])), num(max(a[1], b[1])))
            pairs.setdefault(key, []).append((a[0], a[1], b[0], b[1], d))
    out += _edge_findings(c, T, pairs, "left")

    # right edges: boxes, images, and right-aligned text stacked in one container whose right edges nearly match. Left-aligned stacks are
    # skipped (content-sized boxes end where their content does), and so are centered stacks.
    if "right" in edges:
        rc = sorted([x for x in cands if x[5] in ("box", "img")] + right_text, key=lambda x: (x[2], x[1]))
        pairs = {}
        for ai in range(len(rc)):
            a = rc[ai]
            for bi in range(ai + 1, len(rc)):
                b = rc[bi]
                if b[2] - a[3] > T["right_max_gap_px"]:
                    break
                if b[2] < a[2] + 4 or a[0] == b[0] or c.inside(a[0], b[0]) or c.inside(b[0], a[0]) or _container(c, a[0]) != _container(c, b[0]):
                    continue
                if max(c.lca_distance(a[0], b[0])) > T["lca_levels"]:
                    continue  # far apart in the markup: two unrelated blocks, not one stack
                d = abs(a[4] - b[4])
                if d < T["min_px"] or d > T["max_px"] or abs(a[1] - b[1]) <= T["max_px"] or abs((a[1] + a[4]) - (b[1] + b[4])) / 2.0 < T["min_px"]:
                    continue
                if min(a[4], b[4]) - max(a[1], b[1]) < min(T["min_overlap_px"], 0.3 * min(a[4] - a[1], b[4] - b[1])):
                    continue
                pairs.setdefault((num(min(a[4], b[4])), num(max(a[4], b[4]))), []).append((a[0], a[4], b[0], b[4], d))
        out += _edge_findings(c, T, pairs, "right")

    # top edges: boxes, controls, and images side by side in one row whose tops differ by a few pixels while neither their centers nor their
    # bottoms line up. Text is left out: type of different sizes has different tops on purpose.
    if "top" in edges:
        tc = sorted([x for x in cands if x[5] in ("box", "ctl", "img")], key=lambda x: (x[1], x[2]))
        pairs = {}
        for ai in range(len(tc)):
            a = tc[ai]
            for bi in range(ai + 1, len(tc)):
                b = tc[bi]
                if b[1] - a[4] > T["top_max_gap_px"]:
                    break
                if b[1] < a[4] - 1 or a[0] == b[0] or c.inside(a[0], b[0]) or c.inside(b[0], a[0]) or _container(c, a[0]) != _container(c, b[0]):
                    continue
                if max(c.lca_distance(a[0], b[0])) > T["lca_levels"]:
                    continue
                d = abs(a[2] - b[2])
                if d < T["min_px"] or d > T["max_px"] or abs(a[3] - b[3]) < T["min_px"] or abs((a[2] + a[3]) - (b[2] + b[3])) / 2.0 < T["min_px"]:
                    continue
                if min(a[3], b[3]) - max(a[2], b[2]) < 0.5 * min(a[3] - a[2], b[3] - b[2]):
                    continue
                pairs.setdefault((num(min(a[2], b[2])), num(max(a[2], b[2]))), []).append((a[0], a[2], b[0], b[2], d))
        out += _edge_findings(c, T, pairs, "top")
    return len(cands) + len(right_text), out


_ROW_KINDS = ("button", "select", "role:button")


def _is_row_control(ctl, node):
    k = ctl["k"]
    if ctl.get("dis") or ctl.get("sr") or ctl.get("inl"):
        return False
    if k.startswith("input:"):
        return k[6:] in _TEXT_FIELDS or k[6:] in ("submit", "button", "reset")
    return k in _ROW_KINDS or (k == "link" and node.get("bg") and node.get("bw"))


def r_control_height(c):
    T = c.cfg.th("control.height")
    tol = T["tolerance"]
    items = []
    for ctl in c.controls:
        node = c.nodes[ctl["n"]]
        if _is_row_control(ctl, node) and node["r"][3] >= T["min_px"]:
            items.append((ctl["n"], node["r"]))
    items.sort(key=lambda x: x[1][1] + x[1][3] / 2.0)
    rows = []
    for idx, r in items:
        placed = False
        for row in rows:
            ref = row[0][1]
            lo, hi = max(ref[1], r[1]), min(ref[1] + ref[3], r[1] + r[3])
            if overlay_layer(c, row[0][0]) == overlay_layer(c, idx) and hi - lo >= T["row_overlap"] * min(ref[3], r[3]):
                da, db = c.lca_distance(row[0][0], idx)
                if max(da, db) <= T["lca_levels"]:
                    row.append((idx, r))
                    placed = True
                    break
        if not placed:
            rows.append([(idx, r)])
    out = []
    for row in rows:
        if len(row) < 2:
            continue
        heights = [x[1][3] for x in row]
        if max(heights) - min(heights) > tol:
            lo = min(row, key=lambda x: x[1][3])
            hi = max(row, key=lambda x: x[1][3])
            out.append(c.finding("control.height", "controls in one row differ in height: %s is %spx and %s is %spx" % (
                c.nodes[hi[0]]["s"], num(hi[1][3]), c.nodes[lo[0]]["s"], num(lo[1][3])),
                value=num(max(heights) - min(heights)), threshold=tol, unit="px", count=len(row), worst="%s vs %s px" % (num(max(heights)), num(min(heights))),
                selector=c.path(hi[0]), key="row:%s|%s" % (c.nodes[hi[0]]["s"], c.nodes[lo[0]]["s"]),
                examples=[c.example(i, value=num(r[3])) for i, r in row[:6]]))
    distinct = Counter(round(r[3]) for _, r in items)
    if len(distinct) > T["max_distinct"]:
        out.append(c.finding("control.height", "%d different control heights in use, more than %d: %s" % (
            len(distinct), T["max_distinct"], ", ".join("%dpx (%d)" % (h, n) for h, n in sorted(distinct.items()))),
            value=len(distinct), threshold=T["max_distinct"], unit="heights", count=len(distinct), worst="%d heights (limit %d)" % (len(distinct), T["max_distinct"]),
            key="distinct-heights", detail={"heights": {str(h): n for h, n in sorted(distinct.items())}}))
    return len(items), out


def _ink_boxes(c):
    if "ink" in c._memo:
        return c._memo["ink"]
    ink = []
    seen = set()
    for t in c.texts:
        ink.append(t["n"])
        seen.add(t["n"])
    for ctl in c.controls:
        if ctl["n"] not in seen and not ctl.get("sr"):
            ink.append(ctl["n"])
            seen.add(ctl["n"])
    for i, n in enumerate(c.nodes):
        if i in seen:
            continue
        if n["t"] in ("img", "svg", "video", "canvas") or (n.get("bg") and n["bg"][3] > 0.3 and n["t"] not in ("body", "html")) or n.get("bw"):
            if n["r"][2] > 2 and n["r"][3] > 2:
                ink.append(i)
    c._memo["ink"] = ink
    return ink


def r_rhythm_heading(c):
    T = c.cfg.th("rhythm.heading")
    ink = _ink_boxes(c)
    out = []
    checked = 0
    for t in c.texts:
        if not t.get("hd"):
            continue
        h = t["n"]
        r = c.rect(h)
        top, bottom, left, right = r[1], r[1] + r[3], r[0], r[0] + r[2]
        above = below = None
        layer = overlay_layer(c, h)
        for i in ink:
            if i == h or c.inside(i, h) or c.inside(h, i) or overlay_layer(c, i) != layer:
                continue
            ri = c.rect(i)
            if min(right, ri[0] + ri[2]) - max(left, ri[0]) <= 0:
                continue
            ib, it = ri[1] + ri[3], ri[1]
            if ib <= top + 0.5 and top - ib <= T["max_distance_px"]:
                above = ib if above is None else max(above, ib)
            elif it >= bottom - 0.5 and it - bottom <= T["max_distance_px"]:
                below = it if below is None else min(below, it)
        if above is None or below is None:
            continue
        checked += 1
        ga, gb = max(0.0, top - above), max(0.0, below - bottom)
        if ga < T["min_ratio"] * gb - 1e-9 and gb > 0:
            out.append((h, ga, gb))
    findings = []
    for h, ga, gb in sorted(out, key=lambda x: x[1] / x[2])[:12]:
        ratio = ga / gb
        findings.append(c.finding("rhythm.heading", "heading \"%s\" has %spx above and %spx below (%.2fx); it should sit closer to the content it introduces, at least %sx more room above" % (
            clean(c.text_of[h]["t"], 30), num(ga), num(gb), ratio, num(T["min_ratio"])),
            value=round(ratio, 2), threshold=T["min_ratio"], unit="x", count=1, worst="%spx above vs %spx below" % (num(ga), num(gb)),
            selector=c.path(h), key="heading:%s" % c.nodes[h]["s"], examples=[c.example(h, text=clean(c.text_of[h]["t"], 30), above=num(ga), below=num(gb))]))
    return checked, findings


def r_rhythm_group_ratio(c):
    T = c.cfg.th("rhythm.group_ratio")
    out = []
    checked = 0
    flow = ("absolute", "fixed")
    listish = ("ul", "ol", "dl", "table", "thead", "tbody", "tfoot", "tr", "select", "nav")

    def visible_box(i):
        n = c.nodes[i]
        return bool((n.get("bg") and n["bg"][3] > 0.2) or n.get("bw") or n.get("sh") or n.get("bgi"))

    def block_kids(i):
        return [k for k in c.children[i] if c.nodes[k].get("d") != "inline" and c.nodes[k]["r"][3] > 0 and c.nodes[k].get("pos") not in flow]

    def stacked(kids):
        return all(c.nodes[b]["r"][1] >= c.nodes[a]["r"][1] + c.nodes[a]["r"][3] - 1 for a, b in zip(kids, kids[1:]))

    def gaps(kids):
        return [c.nodes[b]["r"][1] - (c.nodes[a]["r"][1] + c.nodes[a]["r"][3]) for a, b in zip(kids, kids[1:])]

    for i, n in enumerate(c.nodes):
        if n["t"] in listish:
            continue
        kids = block_kids(i)
        if len(kids) < 3 or not stacked(kids):
            continue
        # repeated unboxed groups (for example form fields), each a small stack of its own
        sigs = {}
        for pos, k in enumerate(kids):
            sigs.setdefault((c.nodes[k]["t"], c.nodes[k].get("c", "")), []).append(pos)
        for (tag, _cls), poss in sigs.items():
            if tag in listish or len(poss) < T["min_groups"]:
                continue
            members = [kids[p] for p in poss]
            if any(visible_box(k) for k in members):
                continue
            inner = []
            ok = True
            for k in members:
                sub = block_kids(k)
                if len(sub) < 2 or not stacked(sub):
                    ok = False
                    break
                inner.extend(g for g in gaps(sub) if g > 0)
            if not ok or not inner:
                continue
            outer = [c.nodes[kids[p + 1]]["r"][1] - (c.nodes[kids[p]]["r"][1] + c.nodes[kids[p]]["r"][3]) for p in poss if p + 1 in poss]
            outer = [g for g in outer if g >= 0]
            if not outer:
                continue
            checked += 1
            mi, mo = statistics.median(inner), statistics.median(outer)
            if mo < T["min_ratio"] * mi - 1e-9:
                out.append((i, mo, mi, len(members), "groups"))
        # flat label then control pairs
        pairs_in, pairs_out = [], []
        for idx in range(len(kids) - 1):
            a, b = kids[idx], kids[idx + 1]
            if c.nodes[a]["t"] == "label" and (c.nodes[b]["t"] in ("input", "select", "textarea") or c.nodes[b].get("role") in ("textbox", "combobox")):
                pairs_in.append(c.nodes[b]["r"][1] - (c.nodes[a]["r"][1] + c.nodes[a]["r"][3]))
                if idx + 2 < len(kids) and c.nodes[kids[idx + 2]]["t"] == "label":
                    pairs_out.append(c.nodes[kids[idx + 2]]["r"][1] - (c.nodes[b]["r"][1] + c.nodes[b]["r"][3]))
        if len(pairs_in) >= T["min_groups"] and pairs_out:
            checked += 1
            mi, mo = statistics.median([max(0, g) for g in pairs_in]), statistics.median([max(0, g) for g in pairs_out])
            if mi > 0 and mo < T["min_ratio"] * mi - 1e-9:
                out.append((i, mo, mi, len(pairs_in), "label-control pairs"))
    findings = []
    for i, mo, mi, count, kind in out[:8]:
        findings.append(c.finding("rhythm.group_ratio", "gap between %s is %spx but the gap inside them is %spx (%.2fx); the outer gap should be at least %sx" % (
            kind, num(mo), num(mi), mo / mi, num(T["min_ratio"])),
            value=round(mo / mi, 2), threshold=T["min_ratio"], unit="x", count=count, worst="%spx outer vs %spx inner" % (num(mo), num(mi)), selector=c.path(i),
            key="group:%s" % c.nodes[i]["s"], examples=[c.example(i, outer=num(mo), inner=num(mi))]))
    return checked, findings


# ----------------------------------------------------------------------------
# rules: shape, depth, color

def r_shape_radius(c):
    T = c.cfg.th("shape.radius")
    q = T["round_to"]
    vals = defaultdict(list)
    checked = 0
    for i, n in enumerate(c.nodes):
        rad = n.get("rad")
        if not rad or not (n.get("bg") or n.get("bw") or n.get("sh") or n["t"] in ("img", "button")):
            continue
        r = max(rad)
        w, h = n["r"][2], n["r"][3]
        checked += 1
        if r >= min(w, h) / 2.0 - 0.5:
            continue
        vals[round(r / q) * q].append(i)
    out = []
    if len(vals) > T["max_distinct"]:
        listing = ", ".join("%spx (%d)" % (num(v), len(idxs)) for v, idxs in sorted(vals.items()))
        out.append(c.finding("shape.radius", "%d corner radii in use, more than %d: %s" % (len(vals), T["max_distinct"], listing), value=len(vals),
                             threshold=T["max_distinct"], unit="radii", count=len(vals), worst="%d radii (limit %d)" % (len(vals), T["max_distinct"]),
                             key="radii", detail={"radii": {num(v): len(idxs) for v, idxs in sorted(vals.items())}},
                             examples=[c.example(idxs[0], value=num(v)) for v, idxs in sorted(vals.items(), key=lambda kv: len(kv[1]))[:4]]))
    out.extend(_untokened_radii(c, T, q))
    return checked, out


def r_shape_depth(c):
    T = c.cfg.th("shape.depth")
    shadows, zs = defaultdict(list), defaultdict(list)
    for i, n in enumerate(c.nodes):
        layers = [s for s in (n.get("sh") or []) if s["b"] > 0 or s["x"] or s["y"]]
        if layers:
            sig = ";".join("%s,%s,%s,%s,%s" % (num(s["x"]), num(s["y"]), num(s["b"]), num(s["s"]), to_hex(s["c"][:3])) for s in layers)
            shadows[sig].append(i)
        if n.get("z") is not None:
            zs[n["z"]].append(i)
    out = []
    if len(shadows) > T["max_shadows"]:
        out.append(c.finding("shape.depth", "%d distinct box shadows in use, more than %d" % (len(shadows), T["max_shadows"]), value=len(shadows),
                             threshold=T["max_shadows"], unit="shadows", count=len(shadows), worst="%d shadows (limit %d)" % (len(shadows), T["max_shadows"]), key="shadows",
                             examples=[c.example(idxs[0], value=sig[:60]) for sig, idxs in sorted(shadows.items(), key=lambda kv: -len(kv[1]))[:4]]))
    if len(zs) > T["max_z_values"]:
        out.append(c.finding("shape.depth", "%d distinct z-index values in use, more than %d: %s" % (len(zs), T["max_z_values"], ", ".join(str(z) for z in sorted(zs))),
                             value=len(zs), threshold=T["max_z_values"], unit="values", count=len(zs), worst="%d z values (limit %d)" % (len(zs), T["max_z_values"]), key="z-index"))
    return len(shadows) + len(zs), out


def r_color_budget(c):
    T = c.cfg.th("color.budget")
    q = T["text_quantum"]
    text_colors = Counter()
    chroma_items = []
    for t in c.texts:
        fg = t.get("fg") or [0, 0, 0, 1]
        back = (t.get("bg") or {}).get("c")
        eff = composite(fg, back, fg[3] * t.get("op", 1)) if back else fg[:3]
        text_colors[tuple(int(round(v / float(q))) for v in eff)] += t["len"]
        chroma_items.append(eff)
    for n in c.nodes:
        if n.get("bg") and n["bg"][3] >= 0.5:
            chroma_items.append(n["bg"][:3])
        if n.get("bc") and n.get("bw") and n["bc"][3] >= 0.5:
            chroma_items.append(n["bc"][:3])
    hues = []
    for rgb in chroma_items:
        lightness, chroma, hue = to_oklch(rgb)
        if chroma >= T["chroma_min"] and 0.1 < lightness < 0.98:
            hues.append(hue)
    clusters = []
    for h in sorted(hues):
        for cl in clusters:
            if hue_gap(h, cl[0]) <= T["hue_cluster_deg"] / 2.0:
                cl[1] += 1
                break
        else:
            clusters.append([h, 1])
    if len(clusters) > 1 and hue_gap(clusters[0][0], clusters[-1][0]) <= T["hue_cluster_deg"] / 2.0:
        clusters[0][1] += clusters[-1][1]
        clusters.pop()
    status = T["status_hue_ranges"]
    non_status = [cl for cl in clusters if not any(lo <= cl[0] < hi for lo, hi in status)]
    out = []
    if len(text_colors) > T["max_text_colors"]:
        out.append(c.finding("color.budget", "%d distinct text colors in use, more than %d" % (len(text_colors), T["max_text_colors"]), value=len(text_colors),
                             threshold=T["max_text_colors"], unit="colors", count=len(text_colors), worst="%d text colors (limit %d)" % (len(text_colors), T["max_text_colors"]),
                             key="text-colors", detail={"colors": {to_hex([v * q for v in k]): n for k, n in text_colors.most_common(12)}}))
    if len(non_status) > T["max_non_status_hues"]:
        out.append(c.finding("color.budget", "%d non-status hues in use, more than %d (hue angles %s)" % (
            len(non_status), T["max_non_status_hues"], ", ".join(str(int(round(cl[0]))) for cl in sorted(non_status))),
            value=len(non_status), threshold=T["max_non_status_hues"], unit="hues", count=len(non_status), worst="%d hues (limit %d)" % (len(non_status), T["max_non_status_hues"]),
            key="hues", detail={"hue_angles": [int(round(cl[0])) for cl in sorted(non_status)]}))
    return len(c.texts), out


# ----------------------------------------------------------------------------
# rules: content, media, fonts

_COUNT_LEAD = re.compile(r"^(?:showing |total:? |all )?(\d{1,5})\s+([A-Za-z][\w'-]*(?:\s+[A-Za-z][\w'-]*){0,2})$", re.I)
_COUNT_TRAIL = re.compile(r"^([A-Za-z][\w' -]{0,30}?)\s*[(\u00b7:]\s*(\d{1,5})\)?$")


def collections(c):
    """Largest runs of siblings that share a tag and class list: [(parent, count)]."""
    if "collections" in c._memo:
        return c._memo["collections"]
    found = []
    for i, kids in enumerate(c.children):
        if len(kids) < 3:
            continue
        groups = Counter((c.nodes[k]["t"], c.nodes[k].get("c", "")) for k in kids)
        sig, count = groups.most_common(1)[0]
        if sig[0] in ("li", "tr", "article", "div", "a", "section", "details", "dd") and count >= 3:
            found.append((i, count, sig))
    found.sort(key=lambda x: -x[1])
    c._memo["collections"] = found
    return found


def r_content_count(c):
    T = c.cfg.th("content.count")
    cols = [x for x in collections(c) if x[1] >= T["min_collection"]]
    if not cols:
        return 0, []
    parent, size, sig = cols[0]
    paged = any(re.search(r"\b(next|previous|load more|show more|page \d)\b", t["t"], re.I) for t in c.texts)
    out = []
    checked = 0
    for t in c.texts:
        text = t["t"].strip()
        n = None
        m = _COUNT_LEAD.match(text)
        if m:
            n = int(m.group(1))
        else:
            m = _COUNT_TRAIL.match(text)
            if m:
                n = int(m.group(2))
        if n is None or c.inside(parent, t["n"]):
            continue
        checked += 1
        if n == size:
            continue
        close = abs(n - size) <= max(T["close_abs"], T["close_ratio"] * size)
        if n < size or (close and not paged):
            if not close and n >= size:
                continue
            out.append(c.finding("content.count", "\"%s\" says %d but the largest repeated list (%s) shows %d" % (clean(text, 30), n, c.nodes[parent]["s"], size),
                                 value=n, threshold=size, unit="items", count=1, worst="label %d vs %d rendered" % (n, size), selector=c.path(t["n"]),
                                 key="count:%s" % c.nodes[t["n"]]["s"],
                                 examples=[c.example(t["n"], text=clean(text, 30), label=n, rendered=size), c.example(parent, rendered=size)]))
    return checked, out


def r_media_images(c):
    T = c.cfg.th("media.images")
    broken, nodims, distort, upscale, oversize, blocked_imgs = [], [], [], [], [], []
    dpr = c.view["dpr"]
    blocked_urls = set(b.get("url") for b in (c.network or {}).get("blocked", []))
    for im in c.images:
        n = c.nodes[im["n"]]
        w, h = n["r"][2], n["r"][3]
        svg = bool(im.get("svg")) or im["src"].endswith(".svg")
        if im["lz"] == "lazy" and not im["cmp"]:
            continue
        if im.get("su") and clean(im["su"], 160) in blocked_urls:
            blocked_imgs.append(im)
            continue
        if im["cmp"] and not im["ok"] and not svg:
            broken.append(im)
            continue
        if not im["dims"] and not im["ar"]:
            nodims.append(im)
        if im["ok"] and im["nw"] and im["nh"] and not svg:
            if im["fit"] == "fill" and h > 0 and abs((w / h) / (im["nw"] / float(im["nh"])) - 1) > T["distort_tolerance"]:
                distort.append(im)
            if w > 0 and im["nw"] < w * dpr * (1 - T["upscale_tolerance"]):
                upscale.append(im)
            elif w > 0 and im["nw"] > w * dpr * T["oversize_factor"]:
                oversize.append(im)
    out = []
    spec = [(broken, "broken", "%d image%s failed to load"),
            (distort, None, "%d image%s are stretched away from their natural aspect ratio"),
            (upscale, None, "%d image%s render larger than their pixel size and will look soft"),
            (nodims, "dims", "%d image%s have no width and height or aspect-ratio, which shifts layout when they load"),
            (oversize, "oversize", "%d image%s are much larger than needed"),
            (blocked_imgs, "blocked", "%d image%s were blocked by the safety policy (non-loopback host), so they cannot be judged")]
    for items, sub, msg in spec:
        if items:
            out.append(c.finding("media.images", msg % (len(items), "" if len(items) == 1 else "s"), sub=sub, value=len(items), count=len(items),
                                 selector=c.path(items[0]["n"]), key="%s:%s" % (sub or msg[:12], c.nodes[items[0]["n"]]["s"]), worst="%d image%s" % (len(items), "" if len(items) == 1 else "s"),
                                 examples=[c.example(im["n"], text=im.get("src")) for im in items[:5]]))
    return len(c.images), out


_SYSTEM_FAMILIES = ("system-ui", "ui-sans-serif", "ui-serif", "ui-monospace", "ui-rounded", "-apple-system", "blinkmacsystemfont")
_GENERIC_FAMILIES = ("serif", "sans-serif", "monospace", "cursive", "fantasy", "emoji", "math", "fangsong")


_FONT_SOURCE = re.compile(r"fonts\.(?:googleapis|gstatic)\.com|\.(?:woff2?|ttf|otf)(?:~|\?|$)|\.css(?:~|\?|$)|font", re.I)


def r_font_fallback(c):
    out = []
    # Remote font hosts are blocked by the safety policy, so a missing font is probably not the page's fault.
    blocked = [b for b in (c.network or {}).get("blocked", []) if _FONT_SOURCE.search(b.get("url", ""))]
    hosts = ", ".join(sorted(set(b.get("host") or "?" for b in blocked))[:3])
    note = ("; %d blocked request%s to %s may have supplied it" % (len(blocked), "" if len(blocked) == 1 else "s", hosts)) if blocked else ""
    for face in c.fonts.get("faces", []):
        if face["status"] == "error":
            out.append(c.finding("font.fallback", "font file for \"%s\" failed to load%s" % (clean(face["family"], 40), note), sub="blocked" if blocked else None,
                                 value="error", count=1, key="face:%s" % face["family"]))
    for used in c.fonts.get("used", []):
        skipped, landed = [], None
        for fam, ok in zip(used["fam"], used["avail"]):
            low = fam.lower()
            if low in _SYSTEM_FAMILIES or low in _GENERIC_FAMILIES or ok:
                landed = fam
                break
            skipped.append(fam)
        if not skipped:
            continue
        stack = clean(used["stack"], 80)
        names = ", ".join(clean(f, 24) for f in skipped[:4])
        if landed is None or landed.lower() in _GENERIC_FAMILIES:
            out.append(c.finding("font.fallback", "none of %s is available here, so text renders in the generic fallback (%d characters)%s" % (
                names, used["chars"], note or "; ship the font with @font-face or accept the fallback"), sub="blocked" if blocked else None,
                value="fallback", count=1, key="stack:%s" % stack, worst="generic fallback"))
        else:
            out.append(c.finding("font.fallback", "%s is not available; %s is used instead (%d characters)%s" % (names, clean(landed, 30), used["chars"], note), sub="chain",
                                 value="chain fallback", count=1, key="chain:%s" % stack))
    return len(c.fonts.get("used", [])), out


# ----------------------------------------------------------------------------
# rules: the clean baseline (wrapping, nesting, hierarchy, content column)
#
# These rules hold what a careful frontend engineer keeps by hand: controls that keep their shape, text that wraps on purpose, radii that
# nest, one style per heading level, and one content column with set gutters. Every number lives in defaults.json.

_BLOCK_TAGS = frozenset(("div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd", "section", "article", "header", "footer",
                         "figure", "figcaption", "blockquote", "pre", "table", "form", "fieldset", "aside", "nav", "main", "details", "address"))
_BLOCKY_DISPLAY = frozenset(("block", "flex", "grid", "list-item", "table", "flow-root", "-webkit-box", "table-row", "table-cell", "table-caption"))
_CLIP_VALUES = ("hidden", "clip", "auto", "scroll")


def subtree_texts(c, i):
    """Text records of node i and everything below it, in document order."""
    lo = bisect.bisect_left(c.text_nodes, i)
    hi = bisect.bisect_right(c.text_nodes, c.end[i])
    return [c.text_of[j] for j in c.text_nodes[lo:hi]]


def merge_lines(recs, fraction):
    """Visual lines of several text records as [top, bottom, left, right], top to bottom. Line rectangles whose tops differ by less than
    `fraction` of the line height (and under 2 px at the least) are one line: the rule collect.js applies inside one element."""
    rects = []
    for rec in recs:
        for lr in rec["lr"]:
            rects.append((lr[1], lr[1] + lr[3], lr[0], lr[0] + lr[2]))
    rects.sort()
    lines = []
    for top, bottom, left, right in rects:
        if lines and abs(top - lines[-1][0]) < max(2.0, fraction * (bottom - top)):
            line = lines[-1]
            line[1] = max(line[1], bottom)
            line[2] = min(line[2], left)
            line[3] = max(line[3], right)
        else:
            lines.append([top, bottom, left, right])
    return lines


def inline_texts(c, i):
    """Text records of node i plus those of plain inline descendants (links, emphasis, spans): the text that flows in node i's own lines."""
    out = []
    for rec in subtree_texts(c, i):
        j, flows = rec["n"], True
        while j != i:
            if c.nodes[j].get("d") != "inline":
                flows = False
                break
            j = c.nodes[j]["p"]
        if flows:
            out.append(rec)
    return out


def _blockified(c, j):
    """True when node j sits in a flex or grid container, so the browser turned its display into a block-level value."""
    p = c.nodes[j]["p"]
    d = (c.nodes[p].get("d") or "") if p >= 0 else ""
    return "flex" in d or "grid" in d


def _block_content(c, i, recs, tol):
    """True when the text of node i is laid out as separate blocks: a block-level element holds text (a paragraph, a heading, a div),
    or several text elements stack in a column. Cards that are buttons or links look like this, and they are meant to span several lines."""
    owners = [rec["n"] for rec in recs if rec["n"] != i]
    for j in owners:
        n = c.nodes[j]
        if n["t"] in _BLOCK_TAGS or (n.get("d") in _BLOCKY_DISPLAY and not _blockified(c, j)):
            return True
    by_parent = defaultdict(list)
    for j in owners:
        by_parent[c.nodes[j]["p"]].append(j)
    for p, kids in by_parent.items():
        if len(kids) < 2 or p < 0:
            continue
        d = c.nodes[p].get("d") or ""
        if "flex" in d and (c.nodes[p].get("fd") or "row") in ("column", "column-reverse"):
            return True
        if "grid" in d:
            boxes = sorted((c.nodes[j]["r"][1], c.nodes[j]["r"][1] + c.nodes[j]["r"][3]) for j in kids)
            if any(b[0] >= a[1] - tol for a, b in zip(boxes, boxes[1:])):
                return True
    return False


def _in_landmark(c, i, tags, roles):
    j = c.nodes[i]["p"]
    while j >= 0:
        n = c.nodes[j]
        if n["t"] in tags or n.get("role") in roles:
            return True
        j = n["p"]
    return False


def _has_chrome(n, alpha_min, border_min):
    """True when a box draws something of its own: a background fill or a border that is not transparent."""
    border = bool(n.get("bw") and max(n["bw"]) >= border_min and (n.get("bc") is None or n["bc"][3] > alpha_min))
    return bool((n.get("bg") and n["bg"][3] > alpha_min) or border)


def _forced_breaks(recs):
    """True when a <br> or a white-space value that keeps newlines breaks a line on purpose, so the lines say nothing about wrapping."""
    return any(rec.get("br") or rec.get("ws") in ("pre", "pre-wrap", "pre-line", "break-spaces") for rec in recs)


def _truncated(c, i, recs):
    """True when text is cut by an ellipsis or a line clamp, so the visible lines say nothing about wrapping."""
    for j in [i] + [rec["n"] for rec in recs]:
        n = c.nodes[j]
        if n.get("lc") or n.get("te") or n.get("d") == "-webkit-box":
            return True
    return False


def _inline_level(c, i):
    """Inline-level boxes, plus the items of a row flex container: they shrink to their content the way inline boxes do."""
    n = c.nodes[i]
    d = n.get("d") or ""
    if d.startswith("inline"):
        return True
    p = n["p"]
    if p >= 0 and "flex" in (c.nodes[p].get("d") or "") and (c.nodes[p].get("fd") or "row") in ("row", "row-reverse"):
        return True
    return False


# --- control.wrap -------------------------------------------------------------

_CONTROL_NOUN = {"button": "button", "nav": "navigation item", "menu": "menu item", "tab": "tab", "badge": "badge"}
_MENU_ROLES = ("menuitem", "menuitemcheckbox", "menuitemradio")


def r_control_wrap(c):
    T = c.cfg.th("control.wrap")
    found = defaultdict(list)  # subtier -> [(line count, node, label, width)]
    checked = 0
    in_control = [False] * len(c.nodes)
    for ctl in c.controls:
        for j in range(ctl["n"], c.end[ctl["n"]] + 1):
            in_control[j] = True

    def measure(i, max_chars):
        recs = subtree_texts(c, i)
        if not recs or _forced_breaks(recs) or _truncated(c, i, recs) or _block_content(c, i, recs, T["stack_tolerance_px"]):
            return None
        chars = sum(rec["len"] for rec in recs)
        if chars > max_chars:
            return None
        lines = merge_lines(recs, T["line_merge_fraction"])
        # A label wraps when one text element breaks its own text. Parts that only stack, such as a title with a count beside it that drops
        # below at a narrow width, are separate blocks, which the card-like exclusion leaves alone.
        if max(len(merge_lines([rec], T["line_merge_fraction"])) for rec in recs) < T["min_lines"]:
            return [], clean(" ".join(rec["t"] for rec in recs), 60)
        return lines, clean(" ".join(rec["t"] for rec in recs), 60)

    for ctl in c.controls:
        if ctl.get("sr") or ctl.get("inl") or ctl.get("ine"):
            continue
        i = ctl["n"]
        node = c.nodes[i]
        kind, role = ctl["k"], node.get("role")
        chromed = _has_chrome(node, T["chrome_min_alpha"], T["border_min_px"])
        padded = bool(node.get("pad")) and all((node["pad"][k] or 0) >= T["link_min_padding_px"] for k in (1, 3))
        in_nav = _in_landmark(c, i, ("nav",), ("navigation",))
        if role == "tab" or kind == "role:tab":
            sub = "tab"
        elif role in _MENU_ROLES or kind in ("role:menuitem", "role:menuitemcheckbox", "role:menuitemradio"):
            sub = "menu"
        elif role == "button" or kind in ("button", "role:button"):
            sub = "nav" if (not chromed and in_nav) else "button"
        elif kind in ("link", "role:link"):
            sub = "button" if (chromed and padded) else ("nav" if in_nav else None)
        else:
            sub = None
        if sub is None:
            continue
        got = measure(i, T["max_label_chars"])
        if got is None:
            continue
        checked += 1
        lines, label = got
        if len(lines) >= T["min_lines"]:
            found[sub].append((len(lines), i, label, node["r"][2]))
    skip_until = -1
    for i, n in enumerate(c.nodes):
        if i <= skip_until or in_control[i] or not n.get("rad") or not _has_chrome(n, T["chrome_min_alpha"], T["border_min_px"]) or not _inline_level(c, i):
            continue
        recs = subtree_texts(c, i)
        if not recs or sum(rec["len"] for rec in recs) > T["badge_max_chars"] or max(rec.get("fs") or 0 for rec in recs) > T["badge_max_font_px"]:
            continue
        skip_until = c.end[i]
        got = measure(i, T["badge_max_chars"])
        if got is None:
            continue
        checked += 1
        lines, label = got
        if len(lines) >= T["min_lines"]:
            found["badge"].append((len(lines), i, label, n["r"][2]))
    out = []
    for sub in ("button", "nav", "menu", "tab", "badge"):
        items = found.get(sub)
        if not items:
            continue
        items.sort(key=lambda x: (-x[0], x[3], x[1]))
        n_lines, i, label, width = items[0]
        noun = _CONTROL_NOUN[sub]
        lead = '%s "%s" is %spx wide and wraps to %d lines' % (noun, clean(label, 40), num(width, 0), n_lines)
        if len(items) > 1:
            lead = "%d %ss wrap, worst: %s" % (len(items), noun, lead)
        out.append(c.finding("control.wrap", lead + "; shorten the copy, then widen the control or stack the controls at narrow widths. Never shrink the font or let the height grow",
                             sub=sub, value=n_lines, threshold=1, unit="lines", count=len(items), worst="%d lines in %spx" % (n_lines, num(width, 0)), selector=c.path(i),
                             key="%s:%s" % (sub, c.nodes[i]["s"]),
                             fix="shorten the label, then give the %s more width or stack the group at this width; keep font size and height as they are" % noun,
                             examples=[c.example(j, text=clean(lab, 40), lines=k, width=num(w, 0)) for k, j, lab, w in items[:T["max_examples"]]]))
    return checked, out


# --- type.wrap ----------------------------------------------------------------

_NO_WRAP_TAGS = ("pre", "code", "kbd", "samp", "textarea", "input", "button", "summary", "label", "select", "option", "th", "legend", "script", "style")
_UI_ROLES = ("button", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "option")


def _short_last_line(lines, fraction):
    """(ratio, widest, last) when the last line is narrower than `fraction` of the widest line, else None."""
    widths = [line[3] - line[2] for line in lines]
    widest, last = max(widths), widths[-1]
    if widest <= 0 or last >= fraction * widest:
        return None
    return last / widest, widest, last


def r_type_wrap(c):
    T = c.cfg.th("type.wrap")
    api = c.facts.get("text_wrap_api")
    heads, blocks = [], []
    checked = 0
    seen = set()
    for h in c.headings:
        i = h["n"]
        recs = subtree_texts(c, i)
        if not recs or _forced_breaks(recs) or _truncated(c, i, recs) or _block_content(c, i, recs, T["stack_tolerance_px"]):
            continue
        seen.add(i)
        checked += 1
        lines = merge_lines(recs, T["line_merge_fraction"])
        if not (T["min_lines"] <= len(lines) <= T["heading_max_lines"]):
            continue
        style = (c.text_of.get(i) or recs[0]).get("tw")
        short = _short_last_line(lines, T["short_last_fraction"])
        if short and style not in ("balance", "pretty"):
            heads.append((i, short, len(lines), clean(h["t"], 60)))
    for rec in c.texts:
        i = rec["n"]
        n = c.nodes[i]
        if rec.get("hd") or i in seen or n.get("role") == "heading" or (n.get("d") or "inline").startswith("inline") or n["t"] in _NO_WRAP_TAGS or n.get("role") in _UI_ROLES:
            continue
        if _in_landmark(c, i, ("pre", "code", "textarea", "h1", "h2", "h3", "h4", "h5", "h6") + _NO_WRAP_TAGS, ("heading",) + _UI_ROLES) or rec.get("ws") == "nowrap":
            continue
        recs = inline_texts(c, i)
        if _truncated(c, i, recs) or _forced_breaks(recs):
            continue
        checked += 1
        lines = merge_lines(recs, T["line_merge_fraction"])
        if not (T["min_lines"] <= len(lines) <= T["text_max_lines"]):
            continue
        short = _short_last_line(lines, T["short_last_fraction"])
        if short and rec.get("tw") not in ("balance", "pretty"):
            blocks.append((i, short, len(lines), clean(rec["t"], 60)))
    out = []
    if api is None:
        if checked:
            out.append(c.finding("type.wrap", "this Chrome does not expose the computed text-wrap property, so wrapped headings and short last lines could not be judged",
                                 tier="INFO", value="unavailable", count=1, key="text-wrap-api"))
        return checked, out
    if heads:
        heads.sort(key=lambda x: (x[1][0], x[0]))
        i, (ratio, _widest, _last), n_lines, label = heads[0]
        lead = 'heading "%s" wraps to %d lines and the last line is %d%% of the widest' % (clean(label, 30), n_lines, round(ratio * 100))
        if len(heads) > 1:
            lead = '%d headings end on a short last line, worst "%s" at %d%% of the widest' % (len(heads), clean(label, 24), round(ratio * 100))
        out.append(c.finding("type.wrap", lead + "; set text-wrap: balance (browsers apply it only to short blocks: six lines or fewer in Chromium, ten in Firefox), never on code or pre",
                             value=round(ratio, 2), threshold=T["short_last_fraction"], unit="x", count=len(heads), worst="last line %d%% of widest" % round(ratio * 100),
                             selector=c.path(i), key="heading:%s" % c.nodes[i]["s"],
                             fix="add text-wrap: balance to the heading style; use text-wrap: pretty for body copy and leave code and pre alone",
                             examples=[c.example(j, text=clean(lab, 40), lines=k, last_line_share=round(s[0], 2)) for j, s, k, lab in heads[:T["max_examples"]]]))
    if blocks:
        blocks.sort(key=lambda x: (x[1][0], x[0]))
        i, (ratio, _widest, _last), n_lines, label = blocks[0]
        out.append(c.finding("type.wrap", "%d text block%s of 2 or 3 lines end%s on a short last line, worst %d%% of the widest; set text-wrap: pretty on body copy or balance on captions and subheads, never on code or pre" % (
            len(blocks), "" if len(blocks) == 1 else "s", "s" if len(blocks) == 1 else "", round(ratio * 100)),
            sub="text", value=round(ratio, 2), threshold=T["short_last_fraction"], unit="x", count=len(blocks), worst="last line %d%% of widest" % round(ratio * 100),
            selector=c.path(i), key="text:%s" % c.nodes[i]["s"],
            fix="set text-wrap: pretty on body copy, or text-wrap: balance on captions and subheads; never on code or pre",
            examples=[c.example(j, text=clean(lab, 40), lines=k, last_line_share=round(s[0], 2)) for j, s, k, lab in blocks[:T["max_examples"]]]))
    return checked, out


# --- shape.nested and shape.radius:untokened -----------------------------------

_LEN = re.compile(r"^\s*(-?\d*\.?\d+)\s*(px|rem|em)?\s*$")
_TOKEN_NAME = re.compile(r"^--[A-Za-z0-9_-]{1,48}$")


def _len_px(value, root=16.0):
    m = _LEN.match(str(value))
    if not m:
        return None
    v = float(m.group(1))
    return v if (m.group(2) or "px") == "px" else v * root


def _flat_gradient(n):
    """The color of a gradient whose stops are all one opaque color (a flat fill written as a gradient), else None."""
    stops = n.get("gst") if n.get("bgi") == "gradient" else None
    if not stops or any(s[3] < 0.999 for s in stops):
        return None
    first = stops[0]
    if all(max(abs(s[k] - first[k]) for k in range(3)) <= 3 for s in stops):
        return [int(round(sum(s[k] for s in stops) / float(len(stops)))) for k in range(3)]
    return None


def eff_backgrounds(c):
    """Color painted behind and including each node's own background, composited from its ancestors over the page canvas.
    None where an image or gradient makes it unknown."""
    if "effbg" in c._memo:
        return c._memo["effbg"]
    base = c.facts.get("page_bg") or c.facts.get("canvas") or [255, 255, 255]
    out = [None] * len(c.nodes)
    for i, n in enumerate(c.nodes):
        p = n["p"]
        under = out[p] if p >= 0 else base
        bg = n.get("bg")
        flat = _flat_gradient(n)
        if n.get("bgi") and flat is None:
            out[i] = None
        elif n.get("bgi"):
            out[i] = flat
        elif bg and bg[3] >= 0.999:
            out[i] = list(bg[:3])
        elif bg and under is not None:
            out[i] = composite(bg, under, bg[3])
        else:
            out[i] = under
    c._memo["effbg"] = out
    return out


def has_visible_edge(c, i, alpha_min, delta_min, border_min):
    """True when node i draws a visible edge of its own: a border, a shadow, an image or gradient, or a fill that differs from what is behind it."""
    key = ("edge", i, alpha_min, delta_min, border_min)
    if key in c._memo:
        return c._memo[key]
    n = c.nodes[i]
    bw = n.get("bw")
    res = False
    if bw and max(bw) >= border_min and (n.get("bc") is None or n["bc"][3] > alpha_min):
        res = True
    elif (n.get("bgi") and _flat_gradient(n) is None) or any(sh["c"][3] >= alpha_min for sh in n.get("sh") or []):
        res = True
    elif (n.get("bg") or _flat_gradient(n)) and ((n["bg"][3] if n.get("bg") else 1.0) >= alpha_min):
        eff = eff_backgrounds(c)
        p = n["p"]
        under = eff[p] if p >= 0 else (c.facts.get("page_bg") or c.facts.get("canvas") or [255, 255, 255])
        mine = eff[i]
        res = mine is None or under is None or max(abs(a - b) for a, b in zip(mine, under)) >= delta_min
    c._memo[key] = res
    return res


def is_pill(n, tol):
    """A pill or circle: the radius is at least half the shorter side."""
    rad = n.get("rad")
    return bool(rad) and max(rad) >= min(n["r"][2], n["r"][3]) / 2.0 - tol


def rounded_container(c, i, alpha_min, delta_min, border_min, need_edge=True):
    """(container, clips) for node i: the nearest ancestor with a corner radius and, when need_edge, a visible edge. `clips` is True when the
    container, or a rounded element between it and node i, clips its content (overflow other than visible), which hides a square child corner."""
    key = ("rcont", i, alpha_min, delta_min, border_min, need_edge)
    if key in c._memo:
        return c._memo[key]
    j, clips, res = c.nodes[i]["p"], False, None
    while j >= 0:
        n = c.nodes[j]
        if n.get("rad"):
            ov = n.get("ov")
            if ov and (ov[0] in _CLIP_VALUES or ov[1] in _CLIP_VALUES):
                clips = True
            if not need_edge or has_visible_edge(c, j, alpha_min, delta_min, border_min):
                res = (j, clips)
                break
        j = n["p"]
    c._memo[key] = res
    return res


def corner_gaps(c, i, j):
    """For node i inside the rounded container j: [(corner, dx, dy, inner_radius, child_radius)] for top-left, top-right, bottom-right, and
    bottom-left. dx and dy are the distances from the child's corner to the container's padding-box edges, and inner_radius is the container's
    radius at that corner minus its border width (the padding-box radius)."""
    ni, nj = c.nodes[i], c.nodes[j]
    x, y, w, h = ni["r"]
    cx, cy, cw, ch = nj["r"]
    bw = nj.get("bw") or [0, 0, 0, 0]
    left, top, right, bottom = cx + bw[3], cy + bw[0], cx + cw - bw[1], cy + ch - bw[2]
    crad = nj.get("rad") or [0, 0, 0, 0]
    rad = ni.get("rad") or [0, 0, 0, 0]
    spec = ((x - left, y - top, (bw[0], bw[3])), (right - (x + w), y - top, (bw[0], bw[1])),
            (right - (x + w), bottom - (y + h), (bw[2], bw[1])), (x - left, bottom - (y + h), (bw[2], bw[3])))
    return [(k, dx, dy, max(0.0, crad[k] - max(b)), rad[k]) for k, (dx, dy, b) in enumerate(spec)]


def _radius_names(c):
    """{px value: [custom property names]} for the radius tokens `init` recorded, shortest name first."""
    out = defaultdict(list)
    for name, value in ((c.project.get("token_properties") or {}).items()):
        if _TOKEN_NAME.match(str(name)) and re.search(r"radius|rounded|corner", name.lower()):
            px = _len_px(value)
            if px is not None:
                out[num(px)].append(name)
    return dict((k, sorted(v, key=lambda s: (len(s), s))) for k, v in out.items())


def _scale_note(c, expected, outer, inset):
    """(clause, resolution, fix) for a concentric radius: the clause says whether `expected` is on the project's radius scale, the resolution
    is the short way out for the message, and the fix lists the options with token names. None of them invents a value."""
    tokens = sorted(set(_token_values(c, "radius")))
    names = _radius_names(c)
    Tr = c.cfg.th("shape.radius")
    full, match = Tr["full_token_min_px"], Tr["token_match_px"]
    calc = "derive the inner radius in CSS as calc(var(--radius-outer) - var(--inset))"
    if not tokens:
        return ("and the project defines no radius tokens", "change the inset so the inner radius lands on a value already in use, or write calc(var(--radius-outer) - var(--inset))",
                "no radius tokens were found: (b) change the inset so the inner radius lands on a value already in use, or (c) %s" % calc)
    scale = ", ".join(str(num(t)) for t in tokens if t < full)
    if any(abs(expected - t) < match for t in tokens):
        hit = [t for t in tokens if abs(expected - t) < match][0]
        who = (" (var(%s))" % names[num(hit)][0]) if names.get(num(hit)) else ""
        return ("which is on the radius scale (%s)" % scale, "use that token, change the inset to land on another token, or write calc(var(--radius-outer) - var(--inset))",
                "(a) use the radius token equal to %spx%s, (b) change the inset so %spx minus the inset equals another radius token, or (c) %s" % (num(hit), who, num(outer), calc))
    below = [t for t in tokens if t <= outer + 0.01 and t < full]
    near = min(below, key=lambda t: abs(t - expected)) if below else None
    alt = ("; an inset of %spx gives the %spx token" % (num(outer - near), num(near))) if near is not None and outer - near > 0 else ""
    return ("which is not on the radius scale (%s)" % scale, "change the inset to land on a token, or write calc(var(--radius-outer) - var(--inset))",
            "(b) change the inset so %spx minus the inset equals an existing radius token%s, or (c) %s" % (num(outer), alt, calc))


def r_shape_nested(c):
    T = c.cfg.th("shape.nested")
    alpha, delta, border = T["fill_min_alpha"], T["edge_min_delta"], T["border_min_px"]
    groups = defaultdict(list)  # (actual, expected, outer, inset) -> [(diff, node, corner, container)]
    checked = 0
    for i, n in enumerate(c.nodes):
        w, h = n["r"][2], n["r"][3]
        media = n["t"] in ("img", "video", "canvas", "picture")
        if min(w, h) < T["min_box_px"] or is_pill(n, T["pill_tolerance_px"]) or not (media or has_visible_edge(c, i, alpha, delta, border)):
            continue
        cont = rounded_container(c, i, alpha, delta, border)
        if cont is None or is_pill(c.nodes[cont[0]], T["pill_tolerance_px"]):
            continue
        j, clips = cont
        worst, applies = None, False
        for k, dx, dy, inner, r in corner_gaps(c, i, j):
            if inner <= 0 or dx < -T["edge_tolerance_px"] or dy < -T["edge_tolerance_px"] or dx >= inner or dy >= inner or abs(dx - dy) > T["max_axis_gap_px"]:
                continue
            inset = (dx + dy) / 2.0
            if inset < T["flush_px"] and clips:
                continue
            applies = True
            expected = max(0.0, inner - inset)
            diff = abs(r - expected)
            if diff >= T["min_diff_px"] - 1e-9 and (worst is None or diff > worst[0]):
                worst = (diff, k, r, expected, inner, inset)
        checked += 1 if applies else 0
        if worst:
            diff, k, r, expected, inner, inset = worst
            groups[(num(r, 1), num(expected, 1), num(inner, 1), num(inset, 1))].append((diff, i, k, j))
    out = []
    for (r, expected, inner, inset), items in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        items.sort(key=lambda x: (-x[0], x[1]))
        diff, i, k, j = items[0]
        clause, resolution, fix = _scale_note(c, expected, inner, inset)
        corner = ("top-left", "top-right", "bottom-right", "bottom-left")[k]
        verb = "s" if len(items) == 1 else ""
        place = ("sit%s flush with a %spx container corner" % (verb, inner)) if inset < T["flush_px"] else ("sit%s %spx inside a %spx container corner" % (verb, inset, inner))
        out.append(c.finding("shape.nested", "%d element%s with a %spx %s radius %s; concentric nesting needs %spx (%spx minus %spx), %s: %s" % (
            len(items), "" if len(items) == 1 else "s", r, corner, place, expected, inner, inset, clause, resolution), limit=420,
            value=num(diff, 1), threshold=T["min_diff_px"], unit="px", count=len(items), worst="%spx where %spx fits" % (r, expected),
            selector=c.path(i), key="%s>%s:%s" % (c.nodes[i]["s"], c.nodes[j]["s"], r), fix=fix,
            detail={"actual": r, "expected": expected, "container_radius": inner, "inset": inset},
            examples=[c.example(a, corner=("top-left", "top-right", "bottom-right", "bottom-left")[kk], radius=r, container=c.path(b)) for _d, a, kk, b in items[:T["max_examples"]]]))
    return checked, _cap_findings(c, "shape.nested", out)


def _untokened_radii(c, T, q):
    """Radii in use that are not 0, a radius token, a pill or circle, or the value concentric nesting gives for the element's container."""
    tokens = sorted(set(_token_values(c, "radius")))
    if len(tokens) < T["untokened_min_tokens"]:
        return []
    scale = set(round(t / q) * q for t in tokens)
    Tn = c.cfg.th("shape.nested")
    alpha, delta, border = Tn["fill_min_alpha"], Tn["edge_min_delta"], Tn["border_min_px"]
    found = defaultdict(list)
    for i, n in enumerate(c.nodes):
        rad = n.get("rad")
        if not rad or not (n.get("bg") or n.get("bw") or n.get("sh") or n["t"] in ("img", "button")) or is_pill(n, T["pill_tolerance_px"]):
            continue
        containers = []
        for need_edge in (True, False):
            cont = rounded_container(c, i, alpha, delta, border, need_edge)
            if cont and cont[0] not in containers:
                containers.append(cont[0])
        for k, v in enumerate(rad):
            if v <= 0 or (round(v / q) * q) in scale:
                continue
            explained = False
            for j in containers:
                _k, dx, dy, inner, _r = corner_gaps(c, i, j)[k]
                for inset in (dx, dy, (dx + dy) / 2.0):
                    if inset >= -Tn["edge_tolerance_px"] and inner > 0 and abs(v - max(0.0, inner - inset)) <= T["nest_tolerance_px"] + 1e-9:
                        explained = True
            if not explained and i not in found[round(v / q) * q]:
                found[round(v / q) * q].append(i)
    if not found:
        return []
    names = _radius_names(c)
    parts, listing = [], {}
    for v in sorted(found, key=lambda x: (-len(found[x]), x)):
        near = min(tokens, key=lambda t: (abs(t - v), t))
        who = (" (var(%s))" % names[num(near)][0]) if names.get(num(near)) else ""
        parts.append("%spx x%d, nearest token %spx%s" % (num(v), len(found[v]), num(near), who))
        listing[num(v)] = {"count": len(found[v]), "nearest_token": num(near)}
    top = max(found, key=lambda x: (len(found[x]), -x))
    total = sum(len(v) for v in found.values())
    first = min(found[top])
    return [c.finding("shape.radius", "%d corner radius value%s not on the radius scale (%s): %s" % (
        len(found), " is" if len(found) == 1 else "s are", ", ".join(str(num(t)) for t in tokens if t < T["full_token_min_px"]), "; ".join(parts[:4])),
        sub="untokened", value=len(found), threshold=0, unit="values", count=total, worst="%spx x%d" % (num(top), len(found[top])), selector=c.path(first),
        key="untokened:%s" % ",".join(str(num(v)) for v in sorted(found)), detail={"values": listing},
        fix="use the nearest radius token for each value, or derive an inner radius from its container with calc(var(--radius-outer) - var(--inset)); do not add a new value",
        examples=[c.example(i, value=num(v)) for v in sorted(found, key=lambda x: (-len(found[x]), x)) for i in found[v][:1]][:T["max_examples"]])]


# --- type.role -----------------------------------------------------------------

_TITLE_CLASS = re.compile(r"(?:^|[\s_-])(?:title|heading|headline|name)(?:$|[\s_-])", re.I)


def landmark_of(c, i):
    """(kind, node) of the nearest landmark around node i: main, aside, nav, dialog, or ('other', -1)."""
    key = ("landmark", i)
    if key not in c._memo:
        j, res = i, ("other", -1)
        while j >= 0:
            n = c.nodes[j]
            t, role = n["t"], n.get("role")
            if t == "main" or role == "main":
                res = ("main", j)
            elif t == "aside" or role == "complementary":
                res = ("aside", j)
            elif t == "nav" or role == "navigation":
                res = ("nav", j)
            elif t == "dialog" or role in ("dialog", "alertdialog"):
                res = ("dialog", j)
            else:
                j = n["p"]
                continue
            break
        c._memo[key] = res
    return c._memo[key]


def _color_tier(rec, T):
    """A coarse tier for a text color: a hue bucket for colored text, else strong, medium, muted, or faint by contrast against its backdrop."""
    fg, back = rec.get("fg"), (rec.get("bg") or {}).get("c")
    stops = (rec.get("bg") or {}).get("st")
    if back is None and stops:
        back = [int(round(sum(s[k] for s in stops) / float(len(stops)))) for k in range(3)]  # the mean of a gradient's stops is close enough for a coarse tier
    if not fg or back is None:
        return None
    eff = composite(fg, back, fg[3] * rec.get("op", 1))
    _lightness, chroma, hue = to_oklch(eff)
    if chroma >= T["chroma_min"]:
        return "hue %d" % ((int(round(hue / T["hue_step_deg"])) * T["hue_step_deg"]) % 360)
    ratio = contrast_ratio(eff, back)
    for name, bound in zip(("strong", "medium", "muted"), T["neutral_contrast_tiers"]):
        if ratio >= bound:
            return name
    return "faint"


def _style_of(rec, T):
    return (rec.get("fs") or 0, int(round((rec.get("fw") or 400) / 100.0)) * 100, _color_tier(rec, T))


def _same_style(a, b, T):
    return abs(a[0] - b[0]) < T["size_diff_px"] - 1e-9 and a[1] == b[1] and (a[2] == b[2] or a[2] is None or b[2] is None)


def _style_text(s):
    return "%spx, weight %d, %s" % (num(s[0], 1), s[1], ("%s text" % s[2]) if s[2] and not s[2].startswith("hue") else (s[2] or "unknown color"))


def _cluster_styles(items, T):
    """Group (node, style) items whose styles match within the tolerance. Returns clusters as lists, in order of first appearance."""
    clusters = []
    for item in items:
        for cl in clusters:
            if _same_style(item[1], cl[0][1], T):
                cl.append(item)
                break
        else:
            clusters.append([item])
    return clusters


def r_type_role(c):
    T = c.cfg.th("type.role")
    groups = defaultdict(list)
    for h in c.headings:
        i = h["n"]
        rec = c.text_of.get(i) or (subtree_texts(c, i) or [None])[0]
        if not rec or not rec.get("fs"):
            continue
        kind, node = landmark_of(c, i)
        groups[(h["l"], kind, node if kind != "other" else -1)].append((i, _style_of(rec, T), clean(h["t"], 40)))
    out, flagged, checked = [], set(), 0
    for (level, kind, _node), items in sorted(groups.items()):
        if len(items) < 2:
            continue
        checked += 1
        clusters = _cluster_styles([(i, s) for i, s, _t in items], T)
        if len(clusters) < 2:
            continue
        label = dict((i, t) for i, _s, t in items)
        where = "in %s" % kind if kind != "other" else "outside landmarks"
        dominant = max(clusters, key=lambda cl: (len(cl), -cl[0][0]))
        share = len(dominant) / float(len(items))
        outliers = [it for cl in clusters if cl is not dominant for it in cl]
        ambiguous = (len(clusters) == 2 and all(len(cl) <= T["ambiguous_max_each"] for cl in clusters)) or share < T["dominant_share"] - 1e-9
        flagged.update(i for i, _s in outliers)
        key = "h%d:%s:%s" % (level, kind, ",".join(sorted(set(_style_text(s) for _i, s in outliers))))
        odd = outliers[0]
        gap = max(abs(s[0] - dominant[0][1][0]) for _i, s in outliers)
        if ambiguous:
            parts = ["%d use %s" % (len(cl), _style_text(cl[0][1])) for cl in clusters]
            out.append(c.finding("type.role", "h%d headings %s do not share one style (%s); this can be intentional, such as a hero beside section headings" % (level, where, "; ".join(parts[:3])),
                                 sub="ambiguous", value=num(gap, 1), threshold=T["size_diff_px"], unit="px", count=len(outliers), selector=c.path(odd[0]), key="ambiguous:" + key,
                                 examples=[c.example(i, text=label[i], style=_style_text(s)) for i, s in [it for cl in clusters for it in cl][:T["max_examples"]]]))
            continue
        out.append(c.finding("type.role", 'h%d headings %s differ: %d of %d use %s but "%s" uses %s; give same-level headings one style, or change the level of the odd one' % (
            level, where, len(dominant), len(items), _style_text(dominant[0][1]), clean(label[odd[0]], 30), _style_text(odd[1])),
            value=num(gap, 1), threshold=T["size_diff_px"], unit="px", count=len(outliers), worst="%s vs %s" % (_style_text(odd[1]), _style_text(dominant[0][1])),
            selector=c.path(odd[0]), key=key,
            fix="use %s for every h%d %s, or give the odd heading its own level and take its size from the type scale" % (_style_text(dominant[0][1]), level, where),
            examples=[c.example(i, text=label[i], style=_style_text(s)) for i, s in outliers[:T["max_examples"]]]))
    # repeated components: siblings with one class list should style their title the same way
    heading_nodes = set(h["n"] for h in c.headings)
    for kids in c.children:
        by_class = defaultdict(list)
        for k in kids:
            n = c.nodes[k]
            if n.get("c") and not (n.get("pos") in ("fixed", "absolute")):
                by_class[(n["t"], n["c"])].append(k)
        for _key, members in sorted(by_class.items()):
            if len(members) < T["component_min_siblings"]:
                continue
            titled = []
            for k in members:
                title = next((j for j in range(k + 1, c.end[k] + 1) if j in heading_nodes and j in c.text_of), None)
                if title is None:
                    title = next((rec["n"] for rec in subtree_texts(c, k) if _TITLE_CLASS.search(c.nodes[rec["n"]].get("c") or "")), None)
                if title is not None and c.text_of.get(title, {}).get("fs"):
                    titled.append((title, _style_of(c.text_of[title], T)))
            if len(titled) < T["component_min_siblings"]:
                continue
            checked += 1
            clusters = _cluster_styles(titled, T)
            if len(clusters) < 2:
                continue
            dominant = max(clusters, key=lambda cl: (len(cl), -cl[0][0]))
            outliers = [it for cl in clusters if cl is not dominant for it in cl if it[0] not in flagged]
            if len(dominant) / float(len(titled)) < T["dominant_share"] - 1e-9 or not outliers or len(outliers) > T["component_max_outliers"]:
                continue
            i, s = outliers[0]
            size_gap = abs(s[0] - dominant[0][1][0])
            sub = None if size_gap >= T["size_diff_px"] - 1e-9 else "ambiguous"
            out.append(c.finding("type.role", "%d %s siblings repeat one component, but the title of one is %s where the others use %s" % (
                len(members), c.nodes[members[0]]["s"], _style_text(s), _style_text(dominant[0][1])),
                sub=sub, value=num(size_gap, 1), threshold=T["size_diff_px"], unit="px", count=len(outliers), worst="%s vs %s" % (_style_text(s), _style_text(dominant[0][1])),
                selector=c.path(i), key="component:%s|%s" % (c.nodes[members[0]]["s"], _style_text(s)),
                fix="give every title in the repeated component the same style (%s)" % _style_text(dominant[0][1]),
                examples=[c.example(i, text=clean(c.text_of[i]["t"], 40), style=_style_text(s))]))
    return checked, _cap_findings(c, "type.role", out)


# --- layout.content ------------------------------------------------------------

_SIDEBAR_TAGS = ("aside", "nav")
_SIDEBAR_ROLES = ("complementary", "navigation")
_TABLE_PARTS = ("table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup")


def _layout_flags(c):
    """Per node: inside a horizontal scroller, and outside the normal flow (fixed, absolute, or in a dialog)."""
    if "layout_flags" not in c._memo:
        count = len(c.nodes)
        hs, fl = [False] * count, [False] * count
        for i, n in enumerate(c.nodes):
            p = n["p"]
            hs[i] = bool(n.get("hs")) or (p >= 0 and hs[p])
            fl[i] = bool(n.get("pos") in ("fixed", "absolute") or n["t"] == "dialog" or n.get("role") in ("dialog", "alertdialog") or (p >= 0 and fl[p]))
        c._memo["layout_flags"] = (hs, fl)
    return c._memo["layout_flags"]


def _content_nodes(c):
    """Sorted node indexes of controls and of media inside them, for the has-content test."""
    if "content_nodes" not in c._memo:
        ctl = sorted(set(x["n"] for x in c.controls))
        media = [i for i, n in enumerate(c.nodes) if n["t"] in ("img", "svg", "video", "canvas", "picture", "iframe", "table")]
        c._memo["content_nodes"] = (ctl, media)
    return c._memo["content_nodes"]


def _has_content(c, k):
    """True when box k holds something to see: text, a control, media, or a table."""
    if c.has_text_below(k):
        return True
    for arr in _content_nodes(c):
        pos = bisect.bisect_left(arr, k)
        if pos < len(arr) and arr[pos] <= c.end[k]:
            return True
    return False


def _content_children(c, i):
    """In-flow block children of node i that hold content."""
    _hs, fl = _layout_flags(c)
    out = []
    for k in c.children[i]:
        n = c.nodes[k]
        inline_text = (n.get("d") or "").startswith("inline") and n["t"] not in ("img", "svg", "button", "a", "input", "select", "textarea")
        if fl[k] or n["r"][2] <= 1 or n["r"][3] <= 1 or inline_text:
            continue
        if _has_content(c, k):
            out.append(k)
    return out


def _content_box(c, i):
    """Horizontal extent inside a box's border, any scrollbar, and padding; without the padding when it is not a plain length."""
    n = c.nodes[i]
    x, _y, w, _h = n["r"]
    bw = n.get("bw") or [0, 0, 0, 0]
    pad = n.get("pad") or [0, 0, 0, 0]
    sc = n.get("sc")
    left, right = (x + sc[0], x + sc[0] + sc[1]) if sc else (x + bw[3], x + w - bw[1])
    if pad[1] is None or pad[3] is None:
        return left, right
    return left + pad[3], right - pad[1]


def _shrunk(c, parent, child, tol):
    """True when the child is shrink-to-fit (width auto, no max-width) and narrower than the parent's content box: it is as wide as its
    content, not a column."""
    left, right = _content_box(c, parent)
    w = c.nodes[child]["r"][2]
    return bool(c.nodes[child].get("fit")) and w < (right - left) - tol


def _wraps_symmetrically(c, parent, child, tol):
    """True when the child's box fills the parent's content box or is centered in it: a plain wrapper or a width-capped container,
    not a block that sits against one side and not one that is only as wide as its content."""
    left, right = _content_box(c, parent)
    x, _y, w, _h = c.nodes[child]["r"]
    return abs((x - left) - (right - (x + w))) <= tol and not _shrunk(c, parent, child, tol)


def _extent(c, i, region, T, depth=0):
    """(left, right, container, kids) of the column that node i's content sits in, or None for a full-bleed band with no inner container.
    A visible box that is inset from the region is its own column. Transparent wrappers are looked through while they hold one child;
    otherwise the column is the content box inside the container's border and padding."""
    n = c.nodes[i]
    x, _y, w, _h = n["r"]
    tol = T["full_bleed_tolerance_px"]
    kids = _content_children(c, i)
    edge = has_visible_edge(c, i, T["fill_min_alpha"], T["edge_min_delta"], T["border_min_px"])
    full = x <= region[0] + tol and x + w >= region[1] - tol
    if edge and not full:
        return x, x + w, i, kids
    if len(kids) == 1 and i not in c.text_of and depth < T["max_depth"] and _content_children(c, kids[0]) and _wraps_symmetrically(c, i, kids[0], T["full_bleed_tolerance_px"]):
        return _extent(c, kids[0], region, T, depth + 1)  # a wrapper around one container (not a leaf such as an image, and not a block set against one side)
    if edge and full:
        return None
    left, right = _content_box(c, i)
    return left, right, i, kids


def _sidebars(c, main, T):
    """Navigation and aside boxes beside main: outside it, clear of it horizontally, and overlapping it vertically."""
    mx, my, mw, mh = c.nodes[main]["r"]
    vw = c.facts.get("innerWidth") or c.view["width"]
    out = []
    for i, n in enumerate(c.nodes):
        if not (n["t"] in _SIDEBAR_TAGS or n.get("role") in _SIDEBAR_ROLES) or c.inside(main, i) or c.inside(i, main):
            continue
        x, y, w, h = n["r"]
        if w < T["sidebar_min_px"] or w > T["sidebar_max_share"] * vw:
            continue
        if min(y + h, my + mh) - max(y, my) < T["sidebar_overlap_share"] * min(h, mh):
            continue
        if x + w <= mx + T["adjacent_tolerance_px"] or x >= mx + mw - T["adjacent_tolerance_px"]:
            out.append(i)
    return out


def content_column(c):
    """Where the main content sits at this width. Shared by layout.content and the multi-route comparison. Memoised.

    Returns None when the page has no main landmark, else a dict:
      main      node index of the main landmark
      region    [left, right] of the scroll region the content is laid out in, a sidebar left out (the window, or the box between a
                sidebar and the window edge, or the client box of an inner scroll container)
      column    [left, right] of the primary content column (a visible box that is inset from the region, else the content box inside
                the border and padding of the first container that holds more than one child)
      container node index of that container
      column_node  node index whose box or content the column is: the container, or, when the container has no padding of its own, the
                largest of its sections
      sections  [(node, left, right)] for the container's children, each measured by its own inner container; full-bleed bands with no
                inner container are left out
      aside     True when a navigation or aside box sits beside the column inside the region
      sidebars  node indexes of the navigation and aside boxes beside main
    """
    if "content_column" in c._memo:
        return c._memo["content_column"]
    T = c.cfg.th("layout.content")
    mains = [i for i, n in enumerate(c.nodes) if n["t"] == "main" or n.get("role") == "main"]
    res = None
    if mains:
        main = max(mains, key=lambda i: c.nodes[i]["r"][2] * c.nodes[i]["r"][3])
        sidebars = _sidebars(c, main, T)
        doc = [0.0, float(c.facts.get("clientWidth") or c.facts.get("innerWidth") or c.view["width"])]
        e, slot = main, None
        while True:
            p = c.nodes[e]["p"]
            if p < 0 or c.nodes[p]["t"] in ("body", "html"):
                break
            if any(c.inside(p, s) and not c.inside(e, s) for s in sidebars):
                slot = p
                break
            e = p
        region = list(doc)
        if slot is not None:
            sx, _sy, sw, _sh = c.nodes[slot]["r"]
            left, right = sx, sx + sw
            for s in sidebars:
                if c.inside(slot, s):
                    sbx, _sby, sbw, _sbh = c.nodes[s]["r"]
                    if sbx + sbw <= c.nodes[main]["r"][0] + T["adjacent_tolerance_px"]:
                        left = max(left, sbx + sbw)
                    else:
                        right = min(right, sbx)
            region = [max(doc[0], left), min(doc[1], right)]
        # the client box of the nearest scroll container, around main or inside it along its single-child chain, is the edge content meets
        chain = [e] if e != main else []
        j = main
        for _depth in range(T["max_depth"] + 1):
            chain.append(j)
            kids = _content_children(c, j)
            if len(kids) != 1 or not _content_children(c, kids[0]):
                break
            j = kids[0]
        for j in chain:
            sc, ov = c.nodes[j].get("sc"), c.nodes[j].get("ov")
            if sc and ov and (ov[1] in ("auto", "scroll") or ov[0] in ("auto", "scroll")) and c.nodes[j]["t"] not in ("body", "html"):
                ex = c.nodes[j]["r"][0]
                region = [max(region[0], ex + sc[0]), min(region[1], ex + sc[0] + sc[1])]
                break
        ext = _extent(c, main, region, T)
        if ext is not None:
            left, right, container, kids = ext
            sections = []
            for k in kids:
                got = _extent(c, k, region, T)
                if got is not None and not _shrunk(c, container, k, T["full_bleed_tolerance_px"]):
                    sections.append((k, got[0], got[1]))
            column_node = container
            if sections and abs(left - region[0]) <= T["full_bleed_tolerance_px"] and abs(right - region[1]) <= T["full_bleed_tolerance_px"]:
                # the container has no padding of its own and its sections carry the gutters: the column is where the largest section sits
                k, left, right = max(sections, key=lambda x: (c.nodes[x[0]]["r"][3] * (x[2] - x[1]), -x[0]))
                column_node = k
            aside = False
            for i, n in enumerate(c.nodes):
                if not (n["t"] in _SIDEBAR_TAGS or n.get("role") in _SIDEBAR_ROLES) or c.inside(i, container) or c.inside(container, i):
                    continue
                x, y, w, h = n["r"]
                cy, ch = c.nodes[container]["r"][1], c.nodes[container]["r"][3]
                tol = T["adjacent_tolerance_px"]
                if w >= T["sidebar_min_px"] and x >= region[0] - T["full_bleed_tolerance_px"] and x + w <= region[1] + T["full_bleed_tolerance_px"] \
                        and min(y + h, cy + ch) - max(y, cy) >= T["sidebar_overlap_share"] * min(h, ch) and (x + w <= left + tol or x >= right - tol):
                    aside = True
                    break
            res = {"main": main, "region": region, "column": [left, right], "container": container, "column_node": column_node, "sections": sections, "aside": aside,
                   "sidebars": sidebars}
    c._memo["content_column"] = res
    return res


def _gutter_min(T, width):
    steps = sorted(T["gutter_px"])
    need = steps[0][1]
    for floor, px in steps:
        if width >= floor:
            need = px
    return need


def _column_kind(c, info, T):
    """'text' when the column is mostly prose or form fields, 'data' when tables, grids, or charts take a larger share, else 'other'."""
    left, right = info["column"]
    container = info["column_node"]
    data, text = 0.0, 0.0
    skip_until = -1
    for i in range(container + 1, c.end[container] + 1):
        if i <= skip_until:
            continue
        n = c.nodes[i]
        w, h = n["r"][2], n["r"][3]
        area = w * h
        if n["t"] == "table" or n.get("role") in ("table", "grid", "treegrid"):
            data += area
            skip_until = c.end[i]
        elif n["t"] in ("svg", "canvas", "img", "video", "picture") and w * h >= T["media_min_area"] and w >= T["media_min_width"]:
            data += area
            skip_until = c.end[i]
        elif "grid" in (n.get("d") or "") and len(set(round(c.nodes[k]["r"][0]) for k in c.children[i])) >= 2 and len(c.children[i]) >= 3:
            data += area
            skip_until = c.end[i]
        elif n["t"] == "form" or n.get("role") == "form":
            text += area
            skip_until = c.end[i]
    for rec in c.texts:
        if container < rec["n"] <= c.end[container] and rec["len"] >= T["prose_min_chars"] and not _is_ui_text(c, rec["n"]) and not rec.get("hd"):
            r = c.nodes[rec["n"]]["r"]
            if not any(c.nodes[j]["t"] in ("table", "form") for j in _ancestors(c, rec["n"], container)):
                text += r[2] * r[3]
    for ctl in c.controls:
        field = (ctl["k"].startswith("input:") and ctl["k"][6:] in _TEXT_FIELDS) or ctl["k"] in ("select", "textarea")
        if field and container < ctl["n"] <= c.end[container]:
            text += c.nodes[ctl["n"]]["r"][2] * c.nodes[ctl["n"]]["r"][3]
    col_area = max(1.0, (right - left) * c.nodes[container]["r"][3])
    if text >= T["text_min_share"] * col_area and text > data:
        return "text"
    return "data" if data > text else "other"


def _ancestors(c, i, stop):
    j = c.nodes[i]["p"]
    while j > stop:
        yield j
        j = c.nodes[j]["p"]


def r_layout_content(c):
    T = c.cfg.th("layout.content")
    info = content_column(c)
    if info is None:
        c.fact_out["content"] = None
        return 0, []
    width = c.view["width"]
    region, (left, right) = info["region"], info["column"]
    out = []
    # the facts the multi-route comparison reuses
    c.fact_out["content"] = {"region": [num(region[0], 1), num(region[1], 1)], "column": [num(left, 1), num(right, 1)], "width": num(right - left, 1),
                             "gutters": [num(left - region[0], 1), num(region[1] - right, 1)], "aside": bool(info["aside"]),
                             "selector": c.path(info["column_node"])}
    # 1. gutters: content must keep a minimum inset from the region edges
    main = info["main"]
    hs, fl = _layout_flags(c)
    alpha, delta = T["fill_min_alpha"], T["edge_min_delta"]
    items = []  # (node, left, right)
    for rec in c.texts:
        i = rec["n"]
        if main < i <= c.end[main] and not hs[i] and not fl[i] and rec["lr"]:
            items.append((i, min(l[0] for l in rec["lr"]), max(l[0] + l[2] for l in rec["lr"])))
    for ctl in c.controls:
        i = ctl["n"]
        if main < i <= c.end[main] and not (ctl.get("sr") or ctl.get("ine") or ctl.get("inl") or hs[i] or fl[i]):
            items.append((i, ctl["r"][0], ctl["r"][0] + ctl["r"][2]))
    controls = set(ctl["n"] for ctl in c.controls)
    for i in range(main + 1, c.end[main] + 1):
        n = c.nodes[i]
        x, _y, w, h = n["r"]
        if i in controls or hs[i] or fl[i] or n["t"] in _TABLE_PARTS or n["t"] in ("img", "svg", "video", "canvas", "picture", "li") or w < T["card_min_width"] or h < T["card_min_height"]:
            continue
        if x <= region[0] + T["full_bleed_tolerance_px"] and x + w >= region[1] - T["full_bleed_tolerance_px"]:
            continue  # a full-bleed background: its content is judged on its own
        if has_visible_edge(c, i, alpha, delta, T["border_min_px"]):
            items.append((i, x, x + w))
    need = _gutter_min(T, width)
    extent = {}  # one entry per node: a control and its own text count once
    for i, l, r in items:
        extent[i] = (min(l, extent[i][0]), max(r, extent[i][1])) if i in extent else (l, r)
    for side, label in ((0, "left"), (1, "right")):
        low = []
        for i, (l, r) in sorted(extent.items()):
            inset = (l - region[0]) if side == 0 else (region[1] - r)
            if -T["gutter_overshoot_px"] <= inset < need - 0.01:
                low.append((inset, i))
        if low:
            low.sort()
            worst, i = low[0]
            out.append(c.finding("layout.content", "content sits %spx from the %s edge of the main region at %dpx wide; keep at least %dpx between content and the region edge" % (
                num(max(worst, 0), 0), label, width, need), sub="gutter", value=num(max(worst, 0), 1), threshold=need, unit="px", count=len(low),
                worst="%spx (needs %d)" % (num(max(worst, 0), 0), need), selector=c.path(i), key="gutter:%s" % label,
                fix="give the content region horizontal padding of at least %dpx from the spacing scale, so every block keeps that inset from the %s edge" % (need, label),
                examples=[c.example(j, inset=num(max(v, 0), 1)) for v, j in low[:T["max_examples"]]]))
    # 2. symmetry: equal insets on both sides unless the content sits beside an aside
    inset_l, inset_r = left - region[0], region[1] - right
    fill = (right - left) / max(1.0, region[1] - region[0])
    if not info["aside"] and abs(inset_l - inset_r) > T["asymmetry_px"] and fill >= T["asymmetry_min_fill"]:
        out.append(c.finding("layout.content", "the content column has %spx on the left and %spx on the right of the main region at %dpx wide; equal insets differ by more than %dpx" % (
            num(inset_l, 0), num(inset_r, 0), width, T["asymmetry_px"]), sub="asymmetry", value=num(abs(inset_l - inset_r), 1), threshold=T["asymmetry_px"], unit="px", count=1,
            worst="%spx vs %spx" % (num(inset_l, 0), num(inset_r, 0)), selector=c.path(info["column_node"]), key="asymmetry",
            fix="give the column equal left and right padding, or center it with margin-inline: auto and a max-width",
            examples=[c.example(info["column_node"], left_inset=num(inset_l, 1), right_inset=num(inset_r, 1))]))
    # 3. width cap on very wide screens
    if width >= T["cap_min_view_width"]:
        col_w = right - left
        kind = _column_kind(c, info, T)
        if kind == "text" and col_w > T["cap_text_px"]:
            out.append(c.finding("layout.content", "the form or prose column is %spx wide at %dpx wide; cap it at %dpx so lines and fields stay readable" % (
                num(col_w, 0), width, T["cap_text_px"]), sub="cap_text", value=num(col_w, 1), threshold=T["cap_text_px"], unit="px", count=1, worst="%spx (cap %d)" % (num(col_w, 0), T["cap_text_px"]),
                selector=c.path(info["column_node"]), key="cap_text", fix="set a max-width of %dpx on the content column and center it" % T["cap_text_px"],
                examples=[c.example(info["column_node"], width=num(col_w, 1))]))
        elif col_w > T["cap_px"]:
            out.append(c.finding("layout.content", "the primary content column is %spx wide at %dpx wide; consider capping it at %dpx" % (num(col_w, 0), width, T["cap_px"]),
                                 sub="cap", value=num(col_w, 1), threshold=T["cap_px"], unit="px", count=1, worst="%spx (cap %d)" % (num(col_w, 0), T["cap_px"]),
                                 selector=c.path(info["column_node"]), key="cap", fix="set a max-width of %dpx on the content column and center it" % T["cap_px"],
                                 examples=[c.example(info["column_node"], width=num(col_w, 1))]))
    # 4. section columns: top-level sections should share left and right edges
    secs = info["sections"]
    if len(secs) >= 2:
        lo, hi = T["columns_min_px"], T["columns_max_px"]
        involved, bad_l, bad_r = set(), set(), set()
        for a in range(len(secs)):
            for b in range(a + 1, len(secs)):
                dl, dr = abs(secs[a][1] - secs[b][1]), abs(secs[a][2] - secs[b][2])
                if lo - 1e-9 <= dl <= hi:
                    bad_l.update((round(secs[a][1]), round(secs[b][1])))
                    involved.update((a, b))
                if lo - 1e-9 <= dr <= hi:
                    bad_r.update((round(secs[a][2]), round(secs[b][2])))
                    involved.update((a, b))
        if involved:
            parts = []
            if bad_l:
                parts.append("left edges %s" % " and ".join("%spx" % v for v in sorted(bad_l)[:4]))
            if bad_r:
                parts.append("right edges %s" % " and ".join("%spx" % v for v in sorted(bad_r)[:4]))
            first = min(involved)
            spreads = [max(edges) - min(edges) for edges in (bad_l, bad_r) if edges]
            spread = max(spreads) if spreads else 0
            out.append(c.finding("layout.content", "%d top-level sections of main do not share one column at %dpx wide: %s" % (len(involved), width, "; ".join(parts)),
                                 sub="columns", value=num(spread, 1), threshold=lo, unit="px", count=len(involved), worst="%spx apart" % num(spread, 0),
                                 selector=c.path(secs[first][0]), key="columns:%s" % ",".join(str(v) for v in sorted(bad_l | bad_r)[:4]),
                                 fix="put every top-level section in the same container, or give them the same horizontal padding and max-width",
                                 examples=[c.example(secs[k][0], left=num(secs[k][1], 1), right=num(secs[k][2], 1)) for k in sorted(involved)[:T["max_examples"]]]))
    return len(items) + len(secs) + 1, out


# --- shell.consistency ------------------------------------------------------------
#
# One shell on every route: the header, rail, footer, content column, and page title match, and only the content region changes. Each page
# measures its own shell here and records it as facts.shell (the content column is facts.content, measured by layout.content). One page has
# nothing to compare with, so this rule reports no findings for it. `ui_measure.py routes` captures several pages and calls compare_shells,
# which compares those facts across the routes and puts each finding in the evidence of the route that deviates.

SHELL_FIX = "render one shared shell component for every route and let only the content region change"
SHELL_IGNORE_NAMES = {"banner": "banner", "header": "banner", "nav": "nav", "rail": "nav", "footer": "contentinfo", "contentinfo": "contentinfo",
                      "title": "title", "content": "content", "main": "content"}
_SHELL_SCOPE_TAGS = ("article", "section", "main", "aside", "nav", "dialog")
_SHELL_SCOPE_ROLES = ("article", "region", "main", "complementary", "navigation", "dialog", "alertdialog")
_NAV_ITEM_KINDS = ("link", "button", "summary", "role:link", "role:button", "role:menuitem", "role:tab", "role:treeitem")
_NOT_PRIMARY_NAV = re.compile(r"bread ?crumb|paginat|table of contents|on this page|skip", re.I)


def _main_node(c):
    """The main landmark that content_column uses: the largest main element or [role=main], else None."""
    mains = [i for i, n in enumerate(c.nodes) if n["t"] == "main" or n.get("role") == "main"]
    return max(mains, key=lambda i: c.nodes[i]["r"][2] * c.nodes[i]["r"][3]) if mains else None


def _page_level(c, tag, role):
    """Nodes that are the page's own header or footer: the tag outside article, section, main, aside, nav, and dialog, or an element with the role."""
    out = []
    for i, n in enumerate(c.nodes):
        explicit = n.get("role")
        if (explicit == role or (n["t"] == tag and not explicit and not _in_landmark(c, i, _SHELL_SCOPE_TAGS, _SHELL_SCOPE_ROLES))) \
                and n["r"][2] >= 1 and n["r"][3] >= 1:
            out.append(i)
    return out


def _shell_pos(c, i):
    """fixed or sticky when the box or an ancestor is, else static."""
    j = i
    while j >= 0:
        if c.nodes[j].get("pos") in ("fixed", "sticky"):
            return c.nodes[j]["pos"]
        j = c.nodes[j]["p"]
    return "static"


def _shell_border(c, i, side):
    """Border width on one side of a landmark's box (0 top, 1 right, 2 bottom, 3 left), counting a wrapper that shares that edge."""
    n = c.nodes[i]
    x, y, w, h = n["r"]
    edge = (y, x + w, y + h, x)[side]
    best = (n.get("bw") or [0, 0, 0, 0])[side]
    j = n["p"]
    while j >= 0 and c.nodes[j]["t"] not in ("body", "html"):
        m = c.nodes[j]
        mx, my, mw, mh = m["r"]
        if abs((my, mx + mw, my + mh, mx)[side] - edge) > 0.5:
            break
        best = max(best, (m.get("bw") or [0, 0, 0, 0])[side])
        j = m["p"]
    return best


def _nav_items(c, nav, min_px):
    """Node indexes of the navigation items inside a nav or rail: the largest group of links or buttons that share a tag and class list, so a
    logo or a user menu is left out and the current item (which often carries another class) does not matter. Every control when no two match."""
    groups = defaultdict(list)
    for ctl in c.controls:
        i = ctl["n"]
        if i == nav or not c.inside(nav, i) or ctl["k"] not in _NAV_ITEM_KINDS or ctl.get("sr") or ctl.get("ine") or ctl.get("inl"):
            continue
        n = c.nodes[i]
        if n["r"][2] < min_px or n["r"][3] < min_px:
            continue
        groups[(n["t"], n.get("c") or "")].append(i)
    if not groups:
        return []
    biggest = max(groups.values(), key=lambda g: (len(g), -g[0]))
    return biggest if len(biggest) >= 2 else sorted(i for g in groups.values() for i in g)


def _nav_stats(c, items, vertical):
    """Item count, median height, median gap between neighbors in one column or row, and the text size most items use."""
    if not items:
        return None
    heights = [c.nodes[i]["r"][3] for i in items]
    order = sorted(items, key=lambda i: c.nodes[i]["r"][1] if vertical else c.nodes[i]["r"][0])
    gaps = []
    for a, b in zip(order, order[1:]):
        ax, ay, aw, ah = c.nodes[a]["r"]
        bx, by, bw, bh = c.nodes[b]["r"]
        if vertical:
            side_by_side, gap = min(ax + aw, bx + bw) - max(ax, bx) > 0.5 * min(aw, bw), by - (ay + ah)
        else:
            side_by_side, gap = min(ay + ah, by + bh) - max(ay, by) > 0.5 * min(ah, bh), bx - (ax + aw)
        if side_by_side and gap >= -0.5:
            gaps.append(max(gap, 0.0))
    sizes = Counter()
    for i in items:
        rec = c.text_of.get(i) or (subtree_texts(c, i) or [None])[0]
        if rec and rec.get("fs"):
            sizes[num(rec["fs"], 1)] += rec["len"]
    return {"count": len(items), "height": num(statistics.median(heights), 1), "gap": num(statistics.median(gaps), 1) if gaps else None,
            "font_size": max(sizes.items(), key=lambda kv: (kv[1], kv[0]))[0] if sizes else None}


def _shell_box(c, i, side):
    n = c.nodes[i]
    bg = eff_backgrounds(c)[i]
    return {"selector": c.path(i), "rect": [num(v, 1) for v in n["r"]], "pos": _shell_pos(c, i),
            "bg": [int(round(v)) for v in bg[:3]] if bg is not None else None, "border": num(_shell_border(c, i, side), 1)}


def _shell_title(c, main):
    """The page title: the first h1 in main, else the first heading in main, else the first h1, else the first heading, with its type."""
    heads = c.headings
    inside = [h for h in heads if main is not None and c.inside(main, h["n"])]
    order = [h for h in inside if h["l"] == 1] + [h for h in inside if h["l"] != 1] + [h for h in heads if h["l"] == 1] + list(heads)
    seen = set()
    for h in order:
        if h["n"] in seen:
            continue
        seen.add(h["n"])
        rec = c.text_of.get(h["n"]) or (subtree_texts(c, h["n"]) or [None])[0]
        if not rec or not rec.get("fs"):
            continue
        fg, back = rec.get("fg"), (rec.get("bg") or {}).get("c")
        color = composite(fg, back, fg[3] * rec.get("op", 1)) if fg and back else ([int(v) for v in fg[:3]] if fg else None)
        n = c.nodes[h["n"]]
        top = n["r"][1] - (c.nodes[main]["r"][1] if main is not None else 0)
        return h["n"], {"selector": c.path(h["n"]), "text": clean(h["t"], 40), "rect": [num(v, 1) for v in n["r"]], "size": num(rec["fs"], 1),
                        "weight": int(round((rec.get("fw") or 400) / 100.0)) * 100, "color": color, "top": num(top, 1)}
    return None, None


def _header_edge(c, title, main, info):
    """How far the page header's box reaches past the content column: the header around the page title, inside main, against the column's left and
    right edges. Negative is left of the column, positive is right of it. None when the title has no header around it inside main."""
    if title is None or main is None or info is None:
        return None
    j = c.nodes[title]["p"]
    while j >= 0 and j != main and not (c.nodes[j]["t"] == "header" or c.nodes[j].get("role") == "banner"):
        j = c.nodes[j]["p"]
    if j < 0 or j == main:
        return None
    x, _y, w, _h = c.nodes[j]["r"]
    return {"selector": c.path(j), "left": num(x - info["column"][0], 1), "right": num(x + w - info["column"][1], 1)}


def shell_facts(c):
    """Where the header, rail, footer, main region, and page title sit on this page, and how they look: {banner, nav, main, contentinfo, title},
    each a dict or None, and edge, where the page header's box sits against the content column. The rail's items and the page title carry
    their type measurements."""
    T = c.cfg.th("shell.consistency")
    main = _main_node(c)
    out = {"banner": None, "nav": None, "main": None, "contentinfo": None, "title": None, "edge": None}
    heads = _page_level(c, "header", "banner")
    if heads:
        i = max(heads, key=lambda k: (int(round(c.nodes[k]["r"][2])), -c.nodes[k]["r"][1], -k))
        out["banner"] = _shell_box(c, i, 2)
    foots = _page_level(c, "footer", "contentinfo")
    if foots:
        i = max(foots, key=lambda k: (int(round(c.nodes[k]["r"][2])), c.nodes[k]["r"][1], k))
        out["contentinfo"] = _shell_box(c, i, 0)
    sidebars = set(_sidebars(c, main, c.cfg.th("layout.content"))) if main is not None else set()
    best, best_key = None, None
    for i, n in enumerate(c.nodes):
        role = n.get("role")
        is_nav = (n["t"] == "nav" and role in (None, "navigation")) or role == "navigation"
        is_aside = (n["t"] == "aside" and role in (None, "complementary")) or role == "complementary"
        if not (is_nav or is_aside) or n["r"][2] < 1 or n["r"][3] < 1 or _NOT_PRIMARY_NAV.search(n.get("al") or ""):
            continue
        if main is not None and (c.inside(main, i) or c.inside(i, main)):
            continue
        if _in_landmark(c, i, ("footer", "article", "dialog"), ("contentinfo", "article", "dialog", "alertdialog")):
            continue
        count = len(_nav_items(c, i, T["min_item_px"]))
        if not is_nav and count < 2:
            continue  # an aside is a rail only when it holds a list of links
        # a rail beside main first, then a nav in the page header, a nav over an aside, and the one with the most items
        key = (1 if i in sidebars else 0, 1 if _in_landmark(c, i, ("header",), ("banner",)) else 0, 1 if is_nav else 0, count, -i)
        if best_key is None or key > best_key:
            best, best_key = i, key
    if best is not None:
        box = best
        j = c.nodes[box]["p"]
        while j >= 0 and (c.nodes[j]["t"] == "aside" or c.nodes[j].get("role") == "complementary"):
            box, j = j, c.nodes[j]["p"]  # the rail is the outermost aside around the nav, the box a person sees
        x, y, w, h = c.nodes[box]["r"]
        vertical = h > w
        mx, my, mw, _mh = c.nodes[main]["r"] if main is not None else (0.0, 0.0, float(c.facts.get("innerWidth") or c.view["width"]), 0.0)
        if vertical:
            side = 1 if x + w / 2.0 <= mx + mw / 2.0 else 3
        else:
            side = 2 if y <= my else 0
        rec = _shell_box(c, box, side)
        rec["orientation"] = "vertical" if vertical else "horizontal"
        rec["items"] = _nav_stats(c, _nav_items(c, box, T["min_item_px"]), vertical)
        out["nav"] = rec
    if main is not None:
        out["main"] = {"selector": c.path(main), "rect": [num(v, 1) for v in c.nodes[main]["r"]]}
    title_node, out["title"] = _shell_title(c, main)
    out["edge"] = _header_edge(c, title_node, main, content_column(c))
    return out


def r_shell_consistency(c):
    c.fact_out["shell"] = shell_facts(c)
    c.details["shell.consistency"] = {"compared": False, "note": "one page was measured, so its shell was recorded and nothing was compared; ui_measure.py routes compares the shell across pages"}
    return sum(1 for k, v in c.fact_out["shell"].items() if v and k != "edge"), []


# What compare_shells compares: (landmark, subject in a message, subtier, [(measure, mode, wording)]). Modes: len and font are numbers with a
# tolerance, color is a per-channel tolerance, and cat is an exact match. A wording holds one %s for the value.
_BOX_MEASURES = (("x", "len", "starts %s from the left edge"), ("y", "len", "starts %s from the top"), ("width", "len", "is %s wide"),
                 ("height", "len", "is %s tall"), ("pos", "cat", "is %s"), ("bg", "color", "has the background %s"),
                 ("border", "len", "has a %s border on its content side"))
_SHELL_PLAN = (
    ("banner", "the header", "geometry", _BOX_MEASURES),
    ("nav", "the nav", "geometry", _BOX_MEASURES),
    ("nav", "the nav items", "nav", (("item_height", "len", "are %s tall"), ("item_gap", "len", "are %s apart"), ("item_font", "font", "use %s text"))),
    ("contentinfo", "the footer", "geometry", _BOX_MEASURES),
    ("title", "the page title", "title", (("size", "font", "is %s"), ("weight", "cat", "has the weight %s"), ("color", "color", "is %s"),
                                         ("top", "len", "sits %s below the top of main"))),
    ("content", "the content column", "content", (("left", "len", "starts %s from the left edge"), ("width", "len", "is %s wide"))),
)
_SHELL_PRESENCE = (("banner", "the header"), ("nav", "the nav"), ("main", "the main region"), ("contentinfo", "the footer"), ("title", "the page title"))
_SHELL_TABLE_KINDS = ("banner", "nav", "main", "contentinfo", "title", "content")
_INFO_TAIL = {"cap": "a narrower centered column reads as a width cap for this page type, so keep it only if the page needs it",
              "aside": "a side panel changes the column, so keep it only if the page needs it",
              "title_top": "a breadcrumb or notice above the title moves it, so keep it only if the page needs it"}
_SHELL_WORD = {"banner": "header", "nav": "nav", "main": "main region", "contentinfo": "footer", "title": "page title", "content": "content column"}
_MEASURE_WORD = {"x": "left edge", "y": "top edge", "pos": "position", "bg": "background", "item_height": "item height", "item_gap": "item gap",
                 "item_font": "item font size", "size": "font size", "top": "top offset", "left": "left edge"}
_SHELL_IGNORE_OF = {"banner": "banner", "nav": "nav", "contentinfo": "contentinfo", "title": "title", "content": "content", "main": "content"}


def _shell_flat(kind, shell, content):
    """One landmark of one page as a flat {measure: value}, or None when the page has none."""
    if kind == "content":
        if not content:
            return None
        col = content["column"]
        edge = (shell or {}).get("edge") or {}
        return {"left": col[0], "right": col[1], "width": content["width"], "gutter_left": content["gutters"][0], "gutter_right": content["gutters"][1],
                "aside": bool(content.get("aside")), "edge_left": edge.get("left"), "edge_right": edge.get("right")}
    rec = (shell or {}).get(kind)
    if not rec:
        return None
    x, y, w, h = rec["rect"]
    out = {"x": x, "y": y, "width": w, "height": h}
    if kind == "title":
        out.update({"size": rec["size"], "weight": rec["weight"], "color": rec["color"], "top": rec["top"]})
    elif kind != "main":
        out.update({"pos": rec["pos"], "bg": rec["bg"], "border": rec["border"]})
        if kind == "nav":
            items = rec.get("items") or {}
            out.update({"orientation": rec["orientation"], "item_count": items.get("count"), "item_height": items.get("height"),
                        "item_gap": items.get("gap"), "item_font": items.get("font_size")})
    return out


def _measure_applies(kind, measure, flat):
    """A value is compared only when it does not just follow the page: a static footer sits where the content ends, and a static rail is as tall as the page."""
    if flat.get(measure) is None:
        return False
    if kind == "contentinfo" and measure == "y":
        return flat["pos"] in ("fixed", "sticky")
    if kind == "nav" and measure == "height":
        return flat["orientation"] == "horizontal" or flat["pos"] in ("fixed", "sticky")
    return True


def _shell_value(measure, mode, v):
    if mode in ("len", "font"):
        return "%spx" % num(v, 1)
    if mode == "color":
        return to_hex(v)
    return str(int(v)) if measure == "weight" else str(v)


def _dominant(entries, mode, tol):
    """(dominant value, the entries that share it, the entries that do not) for entries [(cell, value)]. The dominant value is the one the most
    routes share within the tolerance; with a tie the route listed first wins."""
    def same(a, b):
        if mode in ("len", "font"):
            return abs(a - b) <= tol + 1e-9
        if mode == "color":
            return max(abs(p - q) for p, q in zip(a, b)) <= tol + 1e-9
        return a == b
    best = None
    for _cell, v in entries:
        share = [e for e in entries if same(e[1], v)]
        if best is None or len(share) > len(best[1]):
            best = (v, share)
    ids = set(id(e[0]) for e in best[1])
    return best[0], best[1], [e for e in entries if id(e[0]) not in ids]


class _ShellCompare(object):
    """State of one compare_shells call: thresholds, the ignored landmarks, and the result being built."""

    def __init__(self, cells, cfg, tol, ignore):
        self.T = cfg.th("shell.consistency")
        self.tol = self.T["tolerance_px"] if tol is None else tol
        self.skip = set(SHELL_IGNORE_NAMES[k] for k in ignore)
        self.fc = Ctx({"facts": {}}, {"width": 0}, cfg, {})
        self.out = {"findings": dict((c["id"], []) for c in cells), "checked": dict((c["id"], 0) for c in cells),
                    "table": dict((k, {}) for k in _SHELL_TABLE_KINDS), "dominant": dict((k, {}) for k in _SHELL_TABLE_KINDS), "list": []}

    def names(self, cells):
        limit = self.T["max_listed_routes"]
        names = [c["name"] for c in cells]
        return ", ".join(names[:limit]) + (", +%d more" % (len(names) - limit) if len(names) > limit else "")

    def add(self, cell, group, sub, kind, measure, message, rec, raw, dominant, share, deviating, **extra):
        """One finding for the route that deviates. raw is its value, dominant the value most routes share, share the cells that have it, and
        deviating every cell that differs, as [(cell, value)], so a reader of one finding sees all the routes that disagree."""
        detail = {"landmark": kind, "measure": measure, "width": cell["width"], "theme": cell["theme"], "route": cell["route"], "value": raw,
                  "dominant": dominant, "dominant_routes": [s["route"] for s in share],
                  "deviating": [{"route": c["route"], "id": c["id"], "value": v} for c, v in deviating], "routes": len(group)}
        example = {"route": cell["name"], "value": raw}
        for key in ("selector", "rect"):
            if rec and rec.get(key):
                example[key] = rec[key]
        detail["label"] = "page header edges" if sub == "edge" else ("%s %s" % (_SHELL_WORD[kind], _MEASURE_WORD.get(measure, measure)) if measure != "presence" else _SHELL_WORD[kind])
        parts = []
        for part in (sub, kind, measure):
            if part != "presence" and part not in parts:
                parts.append(part)
        key = ":".join(parts)
        f = self.fc.finding("shell.consistency", message, sub=sub, limit=480, count=1, key=key,
                            selector=(rec or {}).get("selector"), examples=[example], detail=detail, **extra)
        self.out["findings"][cell["id"]].append(f)
        self.out["list"].append({"width": cell["width"], "theme": cell["theme"], "landmark": kind, "measure": measure, "sub": sub, "tier": f["tier"],
                                 "route": cell["route"], "id": cell["id"], "value": raw, "dominant": dominant})

    def presence(self, group, flats):
        """A landmark present on some routes and missing on others. The majority decides; with a tie the first route's answer stands."""
        gone = set()
        width = group[0]["width"]
        for kind, subject in _SHELL_PRESENCE:
            if _SHELL_IGNORE_OF[kind] in self.skip:
                continue
            flags = [flats[kind][k] is not None for k in range(len(group))]
            for c in group:
                self.out["checked"][c["id"]] += 1
            yes = [c for c, p in zip(group, flags) if p]
            no = [c for c, p in zip(group, flags) if not p]
            if not yes or not no:
                continue
            majority = yes if len(yes) > len(no) or (len(yes) == len(no) and flags[0]) else no
            for cell in (no if majority is yes else yes):
                has = majority is no
                message = "%s is %s on %s at %dpx wide but %s on %s (%d of %d routes); %s" % (
                    subject, "present" if has else "missing", cell["name"], width, "missing" if has else "present", self.names(majority), len(majority), len(group), SHELL_FIX)
                state, other = ("present", "missing") if has else ("missing", "present")
                self.add(cell, group, "missing", kind, "presence", message, (cell["shell"] or {}).get(kind) if has else None, state, other, majority,
                         [(m, state) for m in (no if majority is yes else yes)], value=state, worst="%s (%s on %d of %d routes)" % (state, other, len(majority), len(group)))
                gone.add((kind, id(cell)))
        return gone

    def measures(self, group, flats, gone):
        width, theme = group[0]["width"], group[0]["theme"]
        cell_key = "%d#%s" % (width, theme)
        aside_of = dict((id(c), bool(f["aside"])) for c, f in zip(group, flats["content"]) if f is not None)
        aside_dom = _dominant([(c, aside_of[id(c)]) for c in group if id(c) in aside_of], "cat", 0)[0] if len(aside_of) >= 2 else None
        content_of = dict((id(c), f) for c, f in zip(group, flats["content"]) if f is not None)
        widths = [(c, f["width"]) for c, f in zip(group, flats["content"]) if f is not None]
        width_dom = _dominant(widths, "len", self.tol)[0] if len(widths) >= 2 else None
        for kind, subject, sub, plan in _SHELL_PLAN:
            if _SHELL_IGNORE_OF[kind] in self.skip:
                continue
            rows = [(c, f) for c, f in zip(group, flats[kind]) if f is not None and (kind, id(c)) not in gone]
            for measure, mode, wording in plan:
                entries = [(c, f[measure]) for c, f in rows if _measure_applies(kind, measure, f)]
                if len(entries) < 2:
                    continue
                tol = self.T["font_tolerance_px"] if mode == "font" else (self.T["color_tolerance"] if mode == "color" else self.tol)
                dominant, share, deviants = _dominant(entries, mode, tol)
                share_cells = [e[0] for e in share]
                for c, _v in entries:
                    self.out["checked"][c["id"]] += 1
                self.out["dominant"][kind].setdefault(cell_key, {"routes": len(group)})[measure] = dominant
                for cell, v in deviants:
                    note = " (no majority; the first route listed is the reference)" if len(share) == 1 else ""
                    here = sub
                    if kind == "title" and measure == "top":
                        here = "title_top"
                    if kind == "content":
                        mine = content_of[id(cell)]
                        if aside_dom is not None and aside_of.get(id(cell)) != aside_dom:
                            here = "aside"  # a route with a side panel, or without one, has a column of its own width on purpose
                        elif width_dom is not None and mine["width"] < width_dom * (1 - self.T["cap_share"]) and abs(mine["gutter_left"] - mine["gutter_right"]) <= self.tol:
                            here = "cap"  # a narrower column, centered: the width cap of a form or detail page
                    message = "%s %s on %s at %dpx wide but %s on %s (%d of %d routes)%s; %s" % (
                        subject, wording % _shell_value(measure, mode, v), cell["name"], width, _shell_value(measure, mode, dominant), self.names(share_cells),
                        len(share), len(group), note, _INFO_TAIL.get(here, SHELL_FIX))
                    extra = {"worst": "%s vs %s" % (_shell_value(measure, mode, v), _shell_value(measure, mode, dominant))}
                    if mode in ("len", "font"):
                        extra.update(value=num(abs(v - dominant), 1), threshold=tol, unit="px")
                    else:
                        extra["value"] = _shell_value(measure, mode, v)
                    rec = {"selector": (cell["content"] or {}).get("selector")} if kind == "content" else (cell["shell"] or {}).get(kind)
                    shown = lambda x: num(x, 2) if mode in ("len", "font") else x
                    self.add(cell, group, here, kind, measure, message, rec, shown(v), dominant, share_cells, [(c, shown(x)) for c, x in deviants], **extra)
        self.edges(group, flats, width)

    def edges(self, group, flats, width):
        """The page header reaches far past the content column on every route that has such a header: the header and the body do not share edges.
        It is the same on every route, so it is information for the owner, not a difference between routes."""
        if "content" in self.skip:
            return
        limit = self.T["edge_info_px"]
        rows = [(c, f) for c, f in zip(group, flats["content"]) if f is not None and f.get("edge_left") is not None]
        if len(rows) < 2 or not all(max(abs(f["edge_left"]), abs(f["edge_right"])) > limit for _c, f in rows):
            return
        for c, f in rows:
            side = lambda v, word: "%spx %s of" % (num(abs(v), 1), word)
            message = "at %dpx wide the page header starts %s the content column and ends %s it on all %d routes that have a page header, so the header and " \
                      "the body do not share edges; put both in one container with one width cap" % (
                          width, side(f["edge_left"], "left" if f["edge_left"] < 0 else "right"), side(f["edge_right"], "left" if f["edge_right"] < 0 else "right"), len(rows))
            worst = max((f["edge_left"], f["edge_right"]), key=abs)
            self.add(c, group, "edge", "content", "edge", message, {"selector": (c["shell"].get("edge") or {}).get("selector")}, [f["edge_left"], f["edge_right"]], [0, 0],
                     [r[0] for r in rows], [(r[0], [r[1]["edge_left"], r[1]["edge_right"]]) for r in rows], worst="%spx left and %spx right of the column" % (num(abs(f["edge_left"]), 1), num(abs(f["edge_right"]), 1)),
                     value=num(abs(worst), 1), threshold=limit, unit="px")

    def table(self, group, flats):
        cell_key = "%d#%s" % (group[0]["width"], group[0]["theme"])
        for kind in _SHELL_TABLE_KINDS:
            for c, flat in zip(group, flats[kind]):
                row = {"id": c["id"], "cite": c["cite"], "present": flat is not None}
                row.update(flat or {})
                self.out["table"][kind].setdefault(c["route"], {})[cell_key] = row


def compare_shells(cells, cfg, tol=None, ignore=()):
    """Compare the shells of several routes. cells: one dict per route and width, in the order the routes were given:
    {id, cite, route (the label), name (what a message calls the route), width, theme, shell (facts.shell), content (facts.content)}. Cells with the
    same width and theme are compared with each other, each measurement against the dominant value across the routes.

    Returns {"findings": {evidence id: [finding]}, "checked": {evidence id: count}, "table": {landmark: {route: {"WIDTH#THEME": measures}}},
    "dominant": {landmark: {"WIDTH#THEME": measures}}, "list": [one row per finding]}. A finding sits with the route that deviates."""
    cmp_ = _ShellCompare(cells, cfg, tol, ignore)
    groups = []
    for cell in cells:
        for g in groups:
            if g[0]["width"] == cell["width"] and g[0]["theme"] == cell["theme"]:
                g.append(cell)
                break
        else:
            groups.append([cell])
    for group in groups:
        flats = dict((kind, [_shell_flat(kind, c["shell"], c["content"]) for c in group]) for kind in _SHELL_TABLE_KINDS)
        cmp_.table(group, flats)
        if len(group) >= 2:
            gone = cmp_.presence(group, flats)
            cmp_.measures(group, flats, gone)
    return cmp_.out


# ----------------------------------------------------------------------------
# allows: data-ui-allow="rule.id: reason"
#
# An element (or an ancestor of it) can opt out of one finding on purpose. The finding stays in the evidence, demoted to INFO, with the
# reason in `allowed`, and summary.md counts it. An allow needs a reason; a bare allow demotes nothing and raises allow.reason. A FAIL
# finding is never demoted: accessibility and usability failures are not a matter of taste.

_ALLOW_SPLIT = re.compile(r";\s*(?=[a-z][a-z0-9_]*\.(?:[a-z0-9_]+|\*)(?::[a-z0-9_]+)?\s*:)")
_ALLOW_ENTRY = re.compile(r"^\s*([a-z][a-z0-9_]*\.(?:[a-z0-9_]+|\*)(?::[a-z0-9_]+)?)\s*(?::\s*(.*))?$", re.S)


def allow_entries(c):
    """{node: [(rule, reason)]} from every data-ui-allow attribute on the page. `rule` is "" when the entry does not start with a rule id."""
    if "allows" not in c._memo:
        found = {}
        for i, n in enumerate(c.nodes):
            raw = n.get("ua")
            if not raw:
                continue
            entries = []
            for part in _ALLOW_SPLIT.split(raw):
                m = _ALLOW_ENTRY.match(part)
                entries.append((m.group(1), (m.group(2) or "").strip()) if m else ("", part.strip()))
            found[i] = entries
        c._memo["allows"] = found
        c._memo["allow_applied"] = Counter()
    return c._memo["allows"]


def _allow_matches(entry_rule, rule, sub, key_sub=None):
    base, _colon, entry_sub = entry_rule.partition(":")
    if base.endswith(".*"):
        return rule.startswith(base[:-1])
    if base != rule:
        return False
    return not entry_sub or entry_sub in (sub, key_sub)


def reason_ok(c, reason):
    return len(re.findall(r"\w+", reason)) >= c.cfg.t("allow.reason", "min_reason_words")


def allow_reason(c, rule, sub, i, key_sub=None):
    """The reason an allow on node i, or on an ancestor of it, gives for `rule` (and subtier `sub`), or None. A bare allow gives None."""
    allows = allow_entries(c)
    if not allows or i is None:
        return None
    j = i
    while j >= 0:
        for k, (entry_rule, reason) in enumerate(allows.get(j, ())):
            if entry_rule and reason_ok(c, reason) and _allow_matches(entry_rule, rule, sub, key_sub):
                c._memo["allow_applied"][(j, k)] += 1
                return reason
        j = c.nodes[j]["p"]
    return None


def _locate(c, example):
    """The node an example (selector and rect) came from, or None."""
    if "rect_nodes" not in c._memo:
        by_rect = defaultdict(list)
        for i, n in enumerate(c.nodes):
            by_rect[tuple(n["r"])].append(i)
        c._memo["rect_nodes"] = by_rect
    rect = example.get("rect")
    if not rect:
        return None
    nodes = c._memo["rect_nodes"].get(tuple(rect), [])
    for i in nodes:
        if c.path(i) == example.get("selector"):
            return i
    return nodes[-1] if len(nodes) == 1 else None


def apply_allows(c, rid, findings):
    """Demote the WARN findings of rule `rid` whose every listed element carries a matching allow. Slop rules split their own hits."""
    if rid == "allow.reason" or not allow_entries(c):
        return findings
    out = []
    for f in findings:
        examples = f.get("examples") or []
        if f["tier"] != "WARN" or f.get("allowed") or f.get("unknown") or not examples or f.get("count", 1) > len(examples):
            out.append(f)
            continue
        key_sub = (f.get("key") or "").split(":")[0] if ":" in (f.get("key") or "") else None
        reasons = []
        for e in examples:
            reason = allow_reason(c, rid, None, _locate(c, e), key_sub)
            if not reason:
                break
            reasons.append(reason)
        else:
            f["tier"] = "INFO"
            f["allowed"] = {"reason": clean(reasons[0], 80)}
        out.append(f)
    return out


def r_allow_reason(c):
    """Every data-ui-allow needs a reason of at least two words, so an opt-out says why the finding does not apply."""
    allows = allow_entries(c)
    applied = c._memo.get("allow_applied") or Counter()
    out, listed = [], []
    for i, entries in sorted(allows.items()):
        for k, (entry_rule, reason) in enumerate(entries):
            listed.append({"rule": entry_rule or "?", "reason": clean(reason, 80), "selector": c.path(i), "applied": applied.get((i, k), 0)})
            if not entry_rule:
                out.append(c.finding("allow.reason", "data-ui-allow on %s does not start with a rule id and a reason; write data-ui-allow=\"rule.id: why this is right here\"" % c.path(i),
                                     value=0, count=1, worst="no rule id", selector=c.path(i), key="malformed:%s" % c.path(i),
                                     examples=[c.example(i, text=clean(c.nodes[i].get("ua") or "", 60))], fix="name the rule and give a reason"))
            elif not reason_ok(c, reason):
                out.append(c.finding("allow.reason", "data-ui-allow=\"%s\" on %s gives no reason, so it demotes nothing; add why the finding does not apply, for example \"%s: brand lockup\"" % (
                    entry_rule, c.path(i), entry_rule), value=len(reason.split()), threshold=c.cfg.t("allow.reason", "min_reason_words"), unit="words", count=1,
                    worst="reason missing" if not reason else "%d word reason" % len(reason.split()), selector=c.path(i), key="reason:%s:%s" % (entry_rule, c.path(i)),
                    examples=[c.example(i, text=clean(c.nodes[i].get("ua") or "", 60))], fix="add a reason of at least two words"))
    if listed:
        c.details["allow.reason"] = {"allows": listed[:40], "applied": sum(applied.values())}
    return len(listed), out


# ----------------------------------------------------------------------------
# registry

RULES = [
    ("viewport.truth", r_viewport_truth), ("viewport.meta", r_viewport_meta), ("theme.truth", r_theme_truth), ("state.confirmed", r_state_confirmed),
    ("console.errors", r_console_errors), ("network.failed", r_network_failed),
    ("overflow.page", r_overflow_page), ("overflow.clip", r_overflow_clip), ("overlap.text", r_overlap_text),
    ("contrast.text", r_contrast_text), ("contrast.ui", r_contrast_ui),
    ("target.size", r_target_size), ("focus.visible", r_focus_visible), ("focus.order", r_focus_order),
    ("a11y.names", r_a11y_names), ("a11y.clickable", r_a11y_clickable), ("a11y.structure", r_a11y_structure),
    ("grid.spacing", r_grid_spacing), ("grid.size", r_grid_size), ("component.conformance", r_component_conformance), ("scale.spacing", r_scale_spacing),
    ("type.scale", r_type_scale), ("type.line_height", r_type_line_height), ("type.measure", r_type_measure), ("type.numeric", r_type_numeric),
    ("align.near_miss", r_align_near_miss), ("control.height", r_control_height), ("rhythm.group_ratio", r_rhythm_group_ratio), ("rhythm.heading", r_rhythm_heading),
    ("shape.radius", r_shape_radius), ("shape.depth", r_shape_depth), ("color.budget", r_color_budget),
    ("content.count", r_content_count), ("media.images", r_media_images), ("font.fallback", r_font_fallback),
    ("control.wrap", r_control_wrap), ("type.wrap", r_type_wrap), ("shape.nested", r_shape_nested),
    ("type.role", r_type_role), ("layout.content", r_layout_content), ("shell.consistency", r_shell_consistency),
]
import _slop  # noqa: E402  (the slop rules read the helpers above, so they are registered once this module is complete)
RULES.extend(_slop.RULES)
RULES.append(("allow.reason", r_allow_reason))  # last: it reports how many allows the rules before it applied
RULE_IDS = [rid for rid, _ in RULES]
TIER_RANK = {"INFO": 0, "WARN": 1, "FAIL": 2}


def rule_status(findings):
    live = [f for f in findings if not f.get("expected")]
    if any(f["tier"] == "FAIL" for f in live):
        return "fail"
    warn = [f for f in live if f["tier"] == "WARN"]
    if warn:
        return "unknown" if all(f.get("unknown") for f in warn) else "warn"
    if any(f["tier"] == "INFO" for f in findings):
        return "info"
    return "pass"


def run_rules(ctx, only=None):
    """Run every rule. A rule that raises is reported as skipped, never allowed to hide the others."""
    out = {}
    for rid, fn in RULES:
        if only and rid not in only:
            continue
        try:
            checked, findings = fn(ctx)
            findings = apply_allows(ctx, rid, findings)
        except Exception as error:  # a defect in one rule must not hide the rest of the evidence
            ctx.errors.append("%s: %r" % (rid, error))
            out[rid] = {"status": "skipped", "tier": ctx.cfg.tier(rid), "checked": 0, "findings": [], "reason": "rule error: %s" % clean(repr(error), 120)}
            continue
        for k, f in enumerate(findings):
            f["id"] = "%s#%d" % (rid, k + 1)
            f.setdefault("count", 1)
        out[rid] = {"status": rule_status(findings), "tier": ctx.cfg.tier(rid), "checked": int(checked), "findings": findings}
        if rid in ctx.details:
            out[rid]["detail"] = ctx.details[rid]
    return out
