"""_slop.py: the slop rules of ui_measure.py.

These rules look for the tells that make a screen read as generated: eyebrow labels over headings, a subtitle that repeats its heading,
one line of copy said three times, "Learn more" buttons, bare empty states, cards inside cards, gradient text, glows, colored side
stripes, badges scattered through content, and em dashes. Every message names the fix, and every fix removes or merges something. None adds a
treatment.

The numbers live in defaults.json. The ids are slop.<name>. Copy rules never FAIL. A stock shadcn/ui Card passes every rule here.

Guards that apply to every copy rule: navigation, tables, breadcrumbs, native form labels, aria-hidden content, code, dates, and "Step N"
text are never read as copy. `data-ui-allow="slop.eyebrow: reason"` on an element, or on an ancestor, demotes its finding to INFO
(see _rules.allow_reason); the reason is required.

The rules' idea of what to look for is adapted from Impeccable's detector catalog (Paul Bakaus, Apache-2.0) and Gesso's rule catalog; the code
is original and works from the facts collect.js records.

Compatible with Python 3.9.
"""
import re
from collections import Counter, defaultdict

import _rules as R

clean, num = R.clean, R.num

# ----------------------------------------------------------------------------
# shared guards and helpers

_COPY_TAGS = frozenset(("nav", "table", "thead", "tbody", "tfoot", "tr", "td", "th", "caption", "colgroup", "pre", "code", "kbd", "samp"))
_COPY_ROLES = frozenset(("navigation", "table", "grid", "treegrid", "row", "rowgroup", "cell", "gridcell", "columnheader", "rowheader"))
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_TEXT = re.compile(
    r"(?:\b%(m)s\s+\d{1,2}(?:st|nd|rd|th)?\b|\b\d{1,2}(?:st|nd|rd|th)?\s+%(m)s|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    r"|\b\d+\s*(?:s|m|h|d|w|y|sec|min|hr|hrs|hour|day|week|month|year)s?\s+ago\b|^\s*(?:today|yesterday|tomorrow|now)\b)" % {"m": _MONTH}, re.I)
_STEP_TEXT = re.compile(r"^\s*step\s+\d+", re.I)
_STOP = frozenset("a an the and or of to in on for your you our my is are be it its this that these those with from by at as all any can will we us do does "
                  "has have into about more most each every per than then so if not no".split())


def copy_skip(c, i):
    """True when node i is in a place copy rules leave alone: navigation, a table, a breadcrumb, code, or aria-hidden content."""
    key = ("copyskip", i)
    if key not in c._memo:
        n = c.nodes[i]
        label = (n.get("al") or "").lower()
        classes = (n.get("c") or "").lower()
        c._memo[key] = bool(n.get("ah") or n["t"] in _COPY_TAGS or n.get("role") in _COPY_ROLES or "breadcrumb" in label or "breadcrumb" in classes
                            or (n["p"] >= 0 and copy_skip(c, n["p"])))
    return c._memo[key]


def form_label(c, i):
    """True when node i is a native form label: inside a label or legend."""
    j = i
    while j >= 0:
        if c.nodes[j]["t"] in ("label", "legend"):
            return True
        j = c.nodes[j]["p"]
    return False


def in_control(c, i):
    """True when node i is inside a link, button, or other control: its text is the control's label, not copy."""
    key = ("incontrol", i)
    if key not in c._memo:
        n = c.nodes[i]
        c._memo[key] = bool(n["t"] in ("a", "button", "summary", "select", "option") or n.get("role") in ("button", "link", "tab", "menuitem", "option")
                            or (n["p"] >= 0 and in_control(c, n["p"])))
    return c._memo[key]


def skip_text(text):
    """Dates and "Step N" are labels with a job, not copy to judge."""
    return bool(_DATE_TEXT.search(text) or _STEP_TEXT.match(text))


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", str(text).lower())).strip()


def content_words(text):
    out = set()
    for w in re.findall(r"[a-z0-9]+", str(text).lower()):
        if w in _STOP or len(w) < 2:
            continue
        if len(w) > 5 and w.endswith("ing"):
            w = w[:-3]
        elif len(w) > 4 and w.endswith("ed"):
            w = w[:-2]
        elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.add(w)
    return out


def lca(c, a, b):
    while a != b:
        if a < 0 or b < 0:
            return -1
        if c.depth[a] >= c.depth[b]:
            a = c.nodes[a]["p"]
        else:
            b = c.nodes[b]["p"]
    return a


def saturation(rgb):
    hi, lo = max(rgb[:3]), min(rgb[:3])
    return 0.0 if hi == 0 else (hi - lo) / float(hi)


def plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


def emit(c, rule, hits, headline, fix, key, worst=None, sub=None):
    """Turn hits ([{n, text?}]) into findings of one rule. Elements an allow covers become one INFO finding per reason; the rest become one
    finding at the rule's tier. `headline` makes the sentence for a list of hits; `fix` is appended to it, so every message names the fix."""
    T = c.cfg.th(rule)
    live, held = [], defaultdict(list)
    for h in hits:
        reason = R.allow_reason(c, rule, sub, h["n"])
        (held[reason].append(h) if reason else live.append(h))
    out = []

    def build(items, message, tier, **extra):
        first = items[0]
        return c.finding(rule, message, sub=sub, tier=tier, limit=420, value=len(items), count=len(items),
                         worst=worst(items) if worst else '"%s"' % clean(first.get("text") or "", 40), selector=c.path(first["n"]), fix=fix, key=key,
                         examples=[c.example(h["n"], text=clean(h.get("text") or "", 60)) for h in items[:T["max_examples"]]],
                         detail={"texts": [clean(t, 80) for h in items[:12] for t in (h.get("all_texts") or [h.get("text")]) if t][:24]}, **extra)
    if live:
        out.append(build(live, "%s. %s" % (headline(live), fix), None))
    for reason in sorted(held):
        out.append(build(held[reason], "%s; allowed by data-ui-allow (%s)" % (headline(held[reason]), clean(reason, 60)), "INFO",
                         allowed={"reason": clean(reason, 80)}))
        out[-1]["key"] = "allowed:" + key
    return out


# ----------------------------------------------------------------------------
# slop.eyebrow

def r_eyebrow(c):
    T = c.cfg.th("slop.eyebrow")
    heads = [t for t in c.texts if t.get("hd") and t["hd"] <= T["heading_max_level"] and (t.get("fs") or 0) >= T["heading_min_px"] and t["lr"]]
    if not heads:
        return 0, []
    cands = []
    for t in c.texts:
        fs = t.get("fs") or 0
        if t.get("hd") or not t["lr"] or not (T["min_chars"] <= t["len"] <= T["max_chars"]) or fs <= 0 or fs > T["max_font_px"] + 1e-9:
            continue
        text = t["t"].strip()
        letters = [ch for ch in text if ch.isalpha()]
        if len(letters) < 2 or not (t.get("tt") == "uppercase" or not any(ch.islower() for ch in letters)):
            continue
        if (t.get("ls") or 0) < T["min_tracking_em"] * fs - 1e-9:
            continue
        if R._is_ui_text(c, t["n"]) or copy_skip(c, t["n"]) or skip_text(text):
            continue
        cands.append(t)
    hits = []
    for e in cands:
        ex, ey, ew, eh = e["lr"][-1]
        best = None
        for h in heads:
            hx, hy, hw, hh = h["lr"][0]
            gap = hy - (ey + eh)
            if h is e or gap < -2 or gap > T["max_gap_px"]:
                continue
            if min(ex + ew, hx + hw) - max(ex, hx) < T["min_overlap_share"] * ew:
                continue
            if best is None or gap < best[0]:
                best = (gap, h)
        if best is None:
            continue
        top = best[1]["lr"][0][1]
        between = any(o is not e and o is not best[1] and any(ln[1] >= ey + eh - 1 and ln[1] + ln[3] <= top + 1 and min(ln[0] + ln[2], ex + ew) - max(ln[0], ex) > 0
                                                               for ln in o["lr"]) for o in c.texts)
        if not between:
            hits.append({"n": e["n"], "text": e["t"], "heading": best[1]["t"]})
    return len(cands), emit(
        c, "slop.eyebrow", hits,
        lambda hs: "%d eyebrow %s above a heading: \"%s\" over \"%s\"" % (len(hs), plural(len(hs), "label sits", "labels sit"), clean(hs[0]["text"], 30), clean(hs[0]["heading"], 40)),
        "Delete it, or fold the words that carry information into the heading.", "eyebrow")


# ----------------------------------------------------------------------------
# slop.restates_heading

def r_restates_heading(c):
    T = c.cfg.th("slop.restates_heading")
    heads = [t for t in c.texts if t.get("hd") and t["hd"] <= T["heading_max_level"] and t["lr"] and not copy_skip(c, t["n"])]
    checked, hits = 0, []
    for h in heads:
        hx, hy, hw, hh = h["lr"][-1]
        hwords = content_words(h["t"])
        if len(hwords) < T["min_shared_words"]:
            continue
        best = None
        for p in c.texts:
            if p is h or p.get("hd") or not p["lr"] or p["len"] < T["min_chars"] or (p.get("fs") or 0) > (h.get("fs") or 0) + 1e-9:
                continue
            px_, py_, pw, ph = p["lr"][0]
            gap = py_ - (hy + hh)
            if gap < -2 or gap > T["max_gap_px"] or min(hx + hw, px_ + pw) - max(hx, px_) < T["min_overlap_share"] * min(hw, pw):
                continue
            if best is None or gap < best[0]:
                best = (gap, p)
        if best is None:
            continue
        p = best[1]
        if R._is_ui_text(c, p["n"]) or copy_skip(c, p["n"]) or form_label(c, p["n"]):
            continue
        checked += 1
        pwords = content_words(p["t"])
        shared = hwords & pwords
        if len(shared) >= T["min_shared_words"] and len(shared) / float(len(hwords)) >= T["min_shared_share"] and len(pwords - hwords) <= T["max_new_words"]:
            hits.append({"n": p["n"], "text": p["t"], "heading": h["t"]})
    return checked, emit(
        c, "slop.restates_heading", hits,
        lambda hs: "%d %s its heading: \"%s\" under \"%s\"" % (len(hs), plural(len(hs), "line repeats", "lines repeat"), clean(hs[0]["text"], 50), clean(hs[0]["heading"], 30)),
        "Keep a line under a heading only when it adds a condition, a consequence, an example, or a next step; otherwise delete it.", "restates")


# ----------------------------------------------------------------------------
# slop.repeated_text

def r_repeated_text(c):
    T = c.cfg.th("slop.repeated_text")
    seen = defaultdict(list)  # normalised text -> [(node, shown text)]
    for t in c.texts:
        if copy_skip(c, t["n"]) or form_label(c, t["n"]) or skip_text(t["t"]):
            continue
        key = norm(t["t"])
        if len(key) >= T["min_chars"] or len(key.split()) >= T["min_words"]:
            seen[key].append((t["n"], t["t"]))
    for ctl in c.controls:
        ph = ctl.get("ph")
        if ph and ph.get("t") and not copy_skip(c, ctl["n"]):
            key = norm(ph["t"])
            if len(key) >= T["min_chars"] or len(key.split()) >= T["min_words"]:
                seen[key].append((ctl["n"], ph["t"]))
    hits = []
    for key, occ in seen.items():
        if len(occ) < T["min_count"]:
            continue
        nodes = [n for n, _t in occ]
        top = nodes[0]
        for n in nodes[1:]:
            top = lca(c, top, n)
        if top >= 0 and len(R.subtree_texts(c, top)) > T["max_scope_texts"]:
            continue  # the same line in a long list or a big region is a pattern of the page, not an echo inside one component
        if top >= 0 and top not in nodes:
            child_of = []
            for n in nodes:
                j = n
                while j >= 0 and c.nodes[j]["p"] != top:
                    j = c.nodes[j]["p"]
                child_of.append(j)
            sigs = set(c.nodes[j]["s"] for j in child_of if j >= 0)
            if len(set(child_of)) == len(nodes) and len(sigs) == 1:
                continue  # the same text in sibling rows or cards that share one structure is a pattern, not an echo
        hits.append({"n": nodes[0], "text": occ[0][1], "times": len(occ)})
    hits.sort(key=lambda h: -h["times"])
    return len(seen), emit(
        c, "slop.repeated_text", hits,
        lambda hs: "\"%s\" appears %d times in one place%s" % (clean(hs[0]["text"], 50), hs[0]["times"], "" if len(hs) == 1 else " (and %d more lines are repeated)" % (len(hs) - 1)),
        "Say each fact once: keep it in the heading or the action and delete the other copies.", "repeated",
        worst=lambda hs: "%dx \"%s\"" % (hs[0]["times"], clean(hs[0]["text"], 30)))


# ----------------------------------------------------------------------------
# slop.generic_action

def _action_label(text):
    """A control's label as the generic list spells it: lowercase, no arrows, ellipses, or punctuation."""
    t = re.sub(r"[\u2192\u2190\u00bb\u203a\u2026>]+", " ", text.strip().lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w' ]+", " ", t)).strip()


def r_generic_action(c):
    T = c.cfg.th("slop.generic_action")
    labels = set(T["labels"])
    checked, hits = 0, []
    for ctl in c.controls:
        if ctl.get("sr") or copy_skip(c, ctl["n"]) or ctl["k"] not in ("link", "button", "role:button", "role:link", "input:submit", "input:button"):
            continue
        recs = R.subtree_texts(c, ctl["n"])
        text = " ".join(r["t"] for r in recs).strip() if recs else ((ctl.get("nm") or {}).get("n") or "" if ctl["k"].startswith("input:") else "")
        if not text:
            continue
        checked += 1
        if _action_label(text) in labels:
            hits.append({"n": ctl["n"], "text": text})
    common = Counter(_action_label(h["text"]) for h in hits).most_common(1)
    return checked, emit(
        c, "slop.generic_action", hits,
        lambda hs: "%d %s a generic label (\"%s\")" % (len(hs), plural(len(hs), "control uses", "controls use"), clean(hs[0]["text"], 30)),
        "Name the action with a verb and what it acts on, such as \"Save spec\" or \"Read the changelog\".", "generic:" + (common[0][0] if common else ""),
        worst=lambda hs: '"%s"' % clean(hs[0]["text"], 30))


# ----------------------------------------------------------------------------
# slop.empty_state

_EMPTY = re.compile(
    r"^(?:(?:you (?:have|haven'?t|don'?t have) )?(?:no|zero) (?!thanks\b|longer\b|owner\b)(?:[a-z0-9'-]+ ){0,3}[a-z0-9'-]+s(?: (?:found|yet|available|here|to (?:show|display|see|view|review)))?"
    r"|(?:you (?:have|haven'?t|don'?t have) )?(?:no|zero) (?!thanks\b|longer\b|owner\b)(?:[a-z0-9'-]+ ){0,3}[a-z0-9'-]+ (?:found|yet|available|here|to (?:show|display|see|view|review))"
    r"|nothing (?:here|to (?:show|display|see|view|do)|found|yet|available|to review)"
    r"|there (?:is|are) no (?:[a-z0-9'-]+ ){0,3}[a-z0-9'-]+(?: (?:found|yet|available|here))?|there'?s nothing [a-z0-9' -]{0,30}"
    r"|(?:it|this (?:list|page|table|folder|space|inbox|view)) (?:is|looks) empty|empty|no data|no results)$", re.I)


def _region(c, i, levels):
    """The nearest ancestor of node i that holds more than the message itself, at most `levels` up."""
    j = c.nodes[i]["p"]
    for _ in range(levels):
        if j < 0:
            return -1
        if len(c.children[j]) > 1 or c.nodes[j]["t"] in ("section", "article", "aside", "main", "li", "dialog"):
            return j
        j = c.nodes[j]["p"]
    return j


def r_empty_state(c):
    T = c.cfg.th("slop.empty_state")
    ctl_nodes = sorted(ctl["n"] for ctl in c.controls if not ctl.get("sr"))
    checked, hits = 0, []
    for t in c.texts:
        text = re.sub(r"\s+", " ", t["t"]).strip().rstrip(".!")
        if t.get("hd") or len(text.split()) > T["max_words"] or len(text) < 4 or not _EMPTY.match(text):
            continue
        if R._is_ui_text(c, t["n"]) or copy_skip(c, t["n"]):
            continue
        checked += 1
        region = _region(c, t["n"], T["max_levels"])
        if region < 0:
            continue
        acts = [n for n in ctl_nodes if c.inside(region, n)]
        words = sum(len(r["t"].split()) for r in R.subtree_texts(c, region))
        if not acts and words <= T["max_region_words"] and len(text.split()) >= T["min_message_share"] * words:
            hits.append({"n": t["n"], "text": t["t"]})
    return checked, emit(
        c, "slop.empty_state", hits,
        lambda hs: "%d empty %s gives no next step: \"%s\"" % (len(hs), plural(len(hs), "state"), clean(hs[0]["text"], 40)),
        "Name what is missing and put the action under it (for example \"No specs yet\" with a \"Create spec\" button).", "empty")


# ----------------------------------------------------------------------------
# slop.nested_cards and the card test the badge rule shares

_CARD_SKIP_TAGS = frozenset(("button", "a", "input", "select", "textarea", "table", "thead", "tbody", "tr", "td", "th", "label", "summary", "img", "svg", "video",
                             "canvas", "pre", "code", "blockquote", "body", "html", "main", "option", "kbd", "figure", "dialog", "details"))
_CARD_SKIP_ROLES = frozenset(("alert", "status", "alertdialog", "dialog", "tooltip", "menu", "listbox", "button", "link", "tab", "menuitem", "option", "progressbar",
                              "slider", "switch", "checkbox", "radio", "note", "log", "marquee", "banner"))
_CARD_SKIP_SLOTS = frozenset(("alert", "badge", "button", "input", "textarea", "select", "tooltip", "popover", "toast", "skeleton", "progress", "separator",
                              "kbd", "table", "avatar", "callout", "banner"))


def is_card(c, i):
    """A card: a rounded box with its own edge or fill that holds real content. Controls, pills, callouts, code blocks, and tables are not cards."""
    key = ("card", i)
    if key in c._memo:
        return c._memo[key]
    T = c.cfg.th("slop.nested_cards")
    n = c.nodes[i]
    res = False
    rad = n.get("rad")
    if (n["t"] not in _CARD_SKIP_TAGS and n.get("role") not in _CARD_SKIP_ROLES and n.get("sl") not in _CARD_SKIP_SLOTS and not (n.get("d") or "").startswith("inline")
            and rad and max(rad) >= T["min_radius_px"] and n["r"][2] >= T["min_width_px"] and n["r"][3] >= T["min_height_px"] and not R.is_pill(n, 0.5)
            and not n.get("ah") and R.has_visible_edge(c, i, T["alpha_min"], T["delta_min"], T["border_min_px"])):
        recs = R.subtree_texts(c, i)
        res = sum(r["len"] for r in recs) >= T["min_text_chars"] or len(recs) >= 2
    c._memo[key] = res
    return res


def r_nested_cards(c):
    T = c.cfg.th("slop.nested_cards")
    hits, checked = [], 0
    for i, n in enumerate(c.nodes):
        if not n.get("rad") or max(n["rad"]) < T["min_radius_px"] or not is_card(c, i):
            continue
        checked += 1
        j = n["p"]
        while j >= 0:
            if c.nodes[j].get("rad") and is_card(c, j) and R.overlay_layer(c, j) == R.overlay_layer(c, i):
                recs = R.subtree_texts(c, i)
                outer = R.subtree_texts(c, j)
                hits.append({"n": i, "text": recs[0]["t"] if recs else "", "outer": outer[0]["t"] if outer else "", "outer_n": j})
                break
            j = c.nodes[j]["p"]
    return checked, emit(
        c, "slop.nested_cards", hits,
        lambda hs: "%d %s inside another card (for example \"%s\" inside \"%s\")" % (len(hs), plural(len(hs), "card sits", "cards sit"), clean(hs[0]["text"], 30), clean(hs[0]["outer"], 30)),
        "Keep one level: remove the inner border and fill and group with space or a divider.", "nested",
        worst=lambda hs: "%d nested" % len(hs))


# ----------------------------------------------------------------------------
# slop.gradient_text

def r_gradient_text(c):
    T = c.cfg.th("slop.gradient_text")
    hits, checked = [], 0
    for t in c.texts:
        if t.get("fgu") not in ("text-clip", "text-fill") or c.nodes[t["n"]].get("ah"):
            continue
        checked += 1
        n = c.nodes[t["n"]]
        if t.get("fgu") == "text-clip" and ((t.get("bg") or {}).get("u") == "gradient" or n.get("bgi") == "gradient"):
            hits.append({"n": t["n"], "text": t["t"]})
    return checked, emit(
        c, "slop.gradient_text", hits,
        lambda hs: "%d text %s filled with a gradient: \"%s\"" % (len(hs), plural(len(hs), "block is", "blocks are"), clean(hs[0]["text"], 40)),
        "Use one solid color from the project's tokens; gradient text also hides the real contrast.", "gradient_text")


# ----------------------------------------------------------------------------
# slop.glow

def _glow_layer(layer, T):
    return (not layer.get("i") and (layer.get("b") or 0) >= T["min_blur_px"] and layer["c"][3] >= T["min_alpha"] - 1e-9
            and saturation(layer["c"]) >= T["min_saturation"] - 1e-9)


def r_glow(c):
    T = c.cfg.th("slop.glow")
    hits, checked = [], 0
    for i, n in enumerate(c.nodes):
        layers = [l for l in (n.get("sh") or []) + (n.get("ts") or [])]
        if not layers or n.get("ah"):
            continue
        checked += 1
        if any(_glow_layer(l, T) for l in layers):
            recs = R.subtree_texts(c, i)
            hits.append({"n": i, "text": recs[0]["t"] if recs else c.nodes[i]["s"]})
    return checked, emit(
        c, "slop.glow", hits,
        lambda hs: "%d %s a colored glow (blur %spx or more, saturated color)" % (len(hs), plural(len(hs), "element has", "elements have"), num(T["min_blur_px"])),
        "Remove the glow; use one neutral shadow from the depth scale, or a solid border.", "glow",
        worst=lambda hs: "glow on %s" % c.nodes[hs[0]["n"]]["s"])


# ----------------------------------------------------------------------------
# slop.side_stripe

def r_side_stripe(c):
    T = c.cfg.th("slop.side_stripe")
    hits, checked = [], 0
    names = ((3, "left"), (1, "right"), (0, "top"))
    for i, n in enumerate(c.nodes):
        bw = n.get("bw")
        r = n["r"]
        if (not bw or max(bw) < T["min_width_px"] or n.get("ah") or n["t"] in ("body", "html", "main", "hr", "table", "tr", "td", "th")
                or r[2] < T["min_box_width_px"] or r[3] < T["min_box_height_px"] or copy_skip(c, i) or R.landmark_of(c, i)[0] == "nav"):
            continue
        checked += 1
        colors = n.get("bcs") or [n.get("bc")] * 4
        for idx, side in names:
            w = bw[idx]
            others = [bw[k] for k in range(4) if k != idx]
            col = colors[idx]
            if w < T["min_width_px"] or max(others) > max(0.5, w * T["max_other_share"]) or not col or col[3] < T["min_alpha"]:
                continue
            if R.to_oklch(col)[1] < T["chroma_min"]:
                continue
            if side == "top" and not (n.get("rad") and max(n["rad"]) >= T["top_min_radius_px"]):
                continue
            recs = R.subtree_texts(c, i)
            hits.append({"n": i, "text": recs[0]["t"] if recs else c.nodes[i]["s"], "side": side, "width": w})
            break
    return checked, emit(
        c, "slop.side_stripe", hits,
        lambda hs: "%d %s a colored %spx %s stripe (for example on %s)" % (len(hs), plural(len(hs), "box has", "boxes have"), num(hs[0]["width"]), hs[0]["side"], c.nodes[hs[0]["n"]]["s"]),
        "Remove the stripe; a real status gets text or an icon, not a colored edge.", "side_stripe",
        worst=lambda hs: "%spx %s stripe" % (num(hs[0]["width"]), hs[0]["side"]))


# ----------------------------------------------------------------------------
# slop.badge_density

def _badge_nodes(c):
    if "badges" in c._memo:
        return c._memo["badges"]
    T = c.cfg.th("slop.badge_density")
    out = []
    for i, n in enumerate(c.nodes):
        r = n["r"]
        if (not n.get("rad") or max(n["rad"]) < T["min_radius_px"] or r[3] > T["max_height_px"] or r[2] > T["max_width_px"] or n["t"] in ("button", "a", "input", "select", "textarea")
                or n.get("ah") or in_control(c, i) or copy_skip(c, i) or not R._has_chrome(n, T["alpha_min"], T["border_min_px"])):
            continue
        recs = R.subtree_texts(c, i)
        text = " ".join(x["t"] for x in recs).strip()
        if not recs or len(text) > T["max_chars"] or len(text.split()) > T["max_words"] or any((x.get("fs") or 0) > T["max_font_px"] + 1e-9 for x in recs):
            continue
        out.append((i, text))
    # a pill inside another badge-like box is the same badge
    keep = [(i, t) for i, t in out if not any(j != i and c.inside(j, i) for j, _t in out)]
    c._memo["badges"] = keep
    return keep


def _badge_container(c, i):
    j = c.nodes[i]["p"]
    while j >= 0:
        n = c.nodes[j]
        if is_card(c, j) or n["t"] in ("article", "section", "li", "aside", "form", "fieldset", "main", "header") or n.get("role") in ("article", "region", "listitem"):
            return j
        j = n["p"]
    return -1


def r_badge_density(c):
    T = c.cfg.th("slop.badge_density")
    badges = _badge_nodes(c)
    groups = defaultdict(list)
    for i, text in badges:
        groups[_badge_container(c, i)].append((i, text))
    hits = []
    for box, items in groups.items():
        if len(items) < T["min_per_container"] or len(set(c.nodes[i]["p"] for i, _t in items)) < 2:
            continue  # a row of tags is one group; badges scattered through the content are the tell
        hits.append({"n": items[0][0], "text": ", ".join(t for _i, t in items[:4]), "all_texts": [t for _i, t in items], "badges": len(items), "box": box})
    hits.sort(key=lambda h: -h["badges"])
    return len(badges), emit(
        c, "slop.badge_density", hits,
        lambda hs: "%d %s %d or more badges scattered through its content (most: %d, \"%s\")" % (len(hs), plural(len(hs), "box carries", "boxes carry"), T["min_per_container"], hs[0]["badges"], clean(hs[0]["text"], 40)),
        "Keep a badge only for a real state that changes; delete the rest, or say it in plain text.", "badges",
        worst=lambda hs: "%d badges in one box" % hs[0]["badges"])


# ----------------------------------------------------------------------------
# slop.dash

_DASH = re.compile(r"—|(?<=\s)–(?=\s)|(?<=\s)--(?=\s)")


def r_dash(c):
    c.cfg.th("slop.dash")
    hits, checked = [], 0
    for t in c.texts:
        if copy_skip(c, t["n"]) or form_label(c, t["n"]):
            continue
        checked += 1
        text = t["t"]
        if skip_text(text) or not _DASH.search(text) or re.fullmatch(r"[\s\u2014\u2013-]+", text):
            continue
        hits.append({"n": t["n"], "text": text, "dashes": len(_DASH.findall(text))})
    count = lambda hs: sum(h["dashes"] for h in hs)
    return checked, emit(
        c, "slop.dash", hits,
        lambda hs: "%d %s in %d text %s (for example \"%s\")" % (count(hs), plural(count(hs), "long dash", "long dashes"), len(hs), plural(len(hs), "block"), clean(hs[0]["text"], 50)),
        "Use a period, a comma, a colon, or parentheses instead of a long dash.", "dash",
        worst=lambda hs: "%d %s" % (count(hs), plural(count(hs), "dash", "dashes")))


RULES = [
    ("slop.eyebrow", r_eyebrow), ("slop.restates_heading", r_restates_heading), ("slop.repeated_text", r_repeated_text),
    ("slop.generic_action", r_generic_action), ("slop.empty_state", r_empty_state), ("slop.nested_cards", r_nested_cards),
    ("slop.gradient_text", r_gradient_text), ("slop.glow", r_glow), ("slop.side_stripe", r_side_stripe), ("slop.badge_density", r_badge_density),
    ("slop.dash", r_dash),
]
