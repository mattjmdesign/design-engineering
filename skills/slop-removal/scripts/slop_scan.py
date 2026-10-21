#!/usr/bin/env python3
"""slop_scan.py: find known AI-slop patterns in UI source and name the fix for each one.

Commands:
  scan PATH   scan JSX, TSX, HTML, CSS, and Tailwind class strings under PATH
  rules       print the rule catalog: id, severity, what it finds, and the fix
  selftest    build a fixture tree in a temp folder and check every rule against it

scan options:
  --files F [F ...]   scan only these files (relative to PATH or the current folder)
  --stdin             read the file list from stdin, one path per line
  --format md|json    output format (default md)
  --baseline FILE     report only findings beyond this baseline
  --write-baseline F  write today's findings to F and exit 0 (--force overwrites)
  --shrink            lower the baseline to today's counts (never raises it)
  --ignore FRAG       skip paths containing FRAG (repeatable)
  --rule ID           report only this rule (repeatable)
  --fail-on LEVEL     warn (default), info, or never: which findings give exit code 1
  --include-tests     also scan test, story, and fixture files (skipped by default)

Rules (each finding carries a message that names the fix):
  src.eyebrow_class          small uppercase tracked label above a heading, or a class named eyebrow or kicker
  src.gradient_text          text filled with a gradient (bg-clip-text with transparent text)
  src.glow_shadow            a colored, blurred shadow used as a glow
  src.transition_all         transition: all or transition-all instead of named properties
  src.bounce_easing          overshoot or bounce easing, animate-bounce, springs with high bounce
  src.infinite_pulse         a decorative animation that never stops (pulse, ping, breathe, float)
  src.entrance_everywhere    the same entrance animation repeated across a file or the project
  src.marketing_words        launch-page wording in product copy
  src.emoji_label            emoji used as an icon or label
  src.description_everywhere a description line under nearly every title, or one that restates its title
  src.allow_reason           a slop-allow comment without a reason, or naming an unknown rule

Opt out with a comment that carries a reason, on the line or the line above:
  // slop-allow: src.transition_all the bar width animates with the score
  {/* slop-allow: src.eyebrow_class the label states the plan tier */}
  /* slop-allow-file: src.marketing_words this file is the public landing copy */
An allow without a reason does not suppress anything and is itself a finding.

Limits: a scan reads text, not the rendered page. It cannot see a class built at runtime, a heading
rendered by another component, or what a word means in context. Every finding is a candidate, so
confirm it on the page. Copy findings never exceed WARN.

Trust boundary: reads files under PATH and writes only the baseline file named with
--write-baseline or --shrink. No network access and no subprocesses.
"""
import argparse
import bisect
import colorsys
import io
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict

SKIP_DIRS = {"node_modules", "vendor", "third_party", "third-party", "bower_components", "dist", "build", "out",
             "target", "coverage", "storybook-static", "__pycache__", "venv", "generated", "__generated__"}
CSS_EXT = {".css", ".scss", ".sass", ".less", ".pcss", ".postcss"}
MARKUP_EXT = {".html", ".htm", ".vue", ".svelte", ".astro"}
CODE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
JSX_EXT = {".js", ".jsx", ".tsx", ".mjs", ".cjs"} | MARKUP_EXT
MAX_BYTES = 1000000
GUARD_PATH = re.compile(
    r"(\.(test|spec|stories|story)\.[a-z]+$)|(\.d\.ts$)|(\.min\.(js|css)$)"
    r"|(/(__tests__|__mocks__|__snapshots__|tests?|e2e|cypress|playwright|stories|\.storybook|fixtures?|mocks?)/)")
GENERATED = re.compile(r"@generated|DO NOT EDIT|auto-?generated", re.I)

RULES = {}


def rule(rule_id, severity, summary, fix):
    RULES[rule_id] = {"severity": severity, "summary": summary, "fix": fix}


rule("src.eyebrow_class", "WARN", "Eyebrow or kicker over a heading",
     "Delete it, or fold its useful words into the heading. Keep a label only when it states a fact the page "
     "says nowhere else, and then allow it with that reason.")
rule("src.gradient_text", "WARN", "Gradient-filled text",
     "Use a solid text color from the project's tokens. Emphasis comes from size, weight, or position.")
rule("src.glow_shadow", "WARN", "Colored, blurred shadow used as a glow",
     "Use the project's one neutral elevation shadow, or none, and a border for definition. A focus state "
     "gets a ring, not a glow.")
rule("src.transition_all", "WARN", "transition: all instead of named properties",
     "Name the properties that change, for example transition-colors or transition-[opacity,translate].")
rule("src.bounce_easing", "WARN", "Bounce or overshoot easing",
     "Use an ease-out curve with no overshoot, such as cubic-bezier(0.22, 1, 0.36, 1), and keep spring "
     "bounce at 0.")
rule("src.infinite_pulse", "WARN", "Decorative animation that never stops",
     "Remove the loop. Show state with a static mark or one change when the state changes. Keep infinite "
     "motion for real loading (spinner, skeleton).")
rule("src.entrance_everywhere", "WARN", "The same entrance animation on many elements",
     "Keep one authored entrance, for the first result or the page lead, and delete the rest. Enter a group "
     "once, not each member.")
rule("src.marketing_words", "INFO", "Launch-page wording in product copy",
     "Write verb plus object in plain words: say what the person does and what they get.")
rule("src.emoji_label", "WARN", "Emoji used as an icon or label",
     "Replace it with the project's icon component, or delete it. Say the outcome in words.")
rule("src.description_everywhere", "INFO", "Description line under nearly every title",
     "Keep a description only when it adds a condition, a consequence, an example, or a next step. Delete "
     "the rest. Where the dialog library needs a description for screen readers, keep it visually hidden.")
rule("src.allow_reason", "WARN", "slop-allow comment without a usable reason",
     "Write the reason after the rule id, at least two words, for example: slop-allow: src.glow_shadow focus "
     "state of the live-call button.")


# ---------------------------------------------------------------------------------------------------
# Colors and lengths

HEX = re.compile(r"(?<![\w&#/-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b(?![\w-])")
FUNC = re.compile(r"\b(?:rgba?|hsla?|oklch|oklab|lab|lch|color)\([^)]*\)", re.I)
LEN = re.compile(r"(?<![\w.#-])(-?\d*\.?\d+)(px|rem|em)?(?![\w%])")
SATURATED_NAMES = re.compile(r"\b(red|orange|yellow|green|blue|purple|violet|indigo|cyan|magenta|pink|lime|teal|"
                             r"fuchsia|aqua|gold|rebeccapurple|dodgerblue|deepskyblue|hotpink|royalblue)\b", re.I)


def _alpha(text):
    if text is None:
        return 1.0
    text = text.strip()
    return min(1.0, float(text[:-1]) / 100) if text.endswith("%") else min(1.0, float(text))


def _enc(x):
    x = max(0.0, min(1.0, x))
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def parse_color(value):
    """Return (r, g, b, a) with channels 0..1, or None when the syntax is not understood."""
    v = value.strip().lower()
    if v.startswith("#"):
        h = v[1:]
        if not re.fullmatch(r"[0-9a-f]+", h) or len(h) not in (3, 4, 6, 8):
            return None
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)) + (a,)
    m = re.fullmatch(r"rgba?\(\s*([\d.]+%?)[ ,]+([\d.]+%?)[ ,]+([\d.]+%?)(?:\s*[,/]\s*([\d.]+%?))?\s*\)", v)
    if m:
        rgb = tuple(float(x[:-1]) / 100 if x.endswith("%") else float(x) / 255 for x in m.groups()[:3])
        return rgb + (_alpha(m.group(4)),)
    m = re.fullmatch(r"hsla?\(\s*([\d.]+)(?:deg)?[ ,]+([\d.]+)%[ ,]+([\d.]+)%(?:\s*[,/]\s*([\d.]+%?))?\s*\)", v)
    if m:
        r, g, b = colorsys.hls_to_rgb(float(m.group(1)) % 360 / 360, float(m.group(3)) / 100, float(m.group(2)) / 100)
        return (r, g, b, _alpha(m.group(4)))
    m = re.fullmatch(r"oklch\(\s*([\d.]+)(%?)\s+([\d.]+)\s+([\d.]+)(?:deg)?(?:\s*/\s*([\d.]+%?))?\s*\)", v)
    if m:
        lig = float(m.group(1)) / (100 if m.group(2) else 1)
        chroma, hue = float(m.group(3)), math.radians(float(m.group(4)))
        a_, b_ = chroma * math.cos(hue), chroma * math.sin(hue)
        l_ = (lig + 0.3963377774 * a_ + 0.2158037573 * b_) ** 3
        m_ = (lig - 0.1055613458 * a_ - 0.0638541728 * b_) ** 3
        s_ = (lig - 0.0894841775 * a_ - 1.2914855480 * b_) ** 3
        rgb = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_,
               -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
               -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)
        return tuple(_enc(x) for x in rgb) + (_alpha(m.group(5)),)
    return None


def saturation(rgb):
    hi, lo = max(rgb[:3]), min(rgb[:3])
    return 0.0 if hi <= 0 else (hi - lo) / hi


def to_px(number, unit):
    return float(number) * (16 if unit in ("rem", "em") else 1)


def split_top(value, sep=","):
    """Split on sep outside parentheses."""
    parts, depth, cur = [], 0, []
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def glow_layer(layer):
    """True when one shadow layer reads as a glow: not inset, a blur of 12px or more (8px when centered), and a
    saturated, visible color."""
    s = layer.strip()
    if not s or re.search(r"\binset\b", s):
        return False
    color, var_name, rest = None, "", s
    m = HEX.search(s) or FUNC.search(s)
    if m:
        color = parse_color(m.group(0))
        rest = s[:m.start()] + " " + s[m.end():]
    else:
        vm = re.search(r"var\(\s*(--[\w-]+)", s)
        if vm:
            var_name = vm.group(1)
        elif SATURATED_NAMES.search(s):
            color = (0.6, 0.2, 0.9, 1.0)
    rest = re.sub(r"[\w-]*\([^()]*(?:\([^()]*\)[^()]*)*\)", " ", rest)
    nums = [to_px(a, u) for a, u in LEN.findall(rest)]
    if len(nums) < 3:
        return False
    x, y, blur = nums[0], nums[1], nums[2]
    if blur < 12 and not (x == 0 and y == 0 and blur >= 8):
        return False
    if color is not None:
        return color[3] >= 0.15 and saturation(color) >= 0.4 and max(color[:3]) >= 0.25
    if var_name:
        return bool(re.search(r"glow|neon|brand|primary|accent|purple|violet|indigo|blue|cyan|pink|fuchsia", var_name, re.I))
    return False


def glow_in_value(value):
    """Return the first glowing layer of a box-shadow, text-shadow, or drop-shadow value, or None."""
    for layer in split_top(value):
        if glow_layer(layer):
            return layer.strip()
    return None


# ---------------------------------------------------------------------------------------------------
# Tokenizing: comments masked (newlines kept so line numbers stay true), string literals located

KEYWORDS = {"return", "case", "default", "in", "of", "typeof", "else", "yield", "await", "void", "delete", "new",
            "throw", "export", "import", "from", "as", "extends", "instanceof", "do"}
INTEREST = re.compile(r"/[/*]|[\"'`]|<!--")


def skip_braces(text, j, limit):
    depth, n = 1, len(text)
    while j < n and j < limit:
        c = text[j]
        if c in "\"'`":
            k = skip_string(text, j)
            j = j + 1 if k < 0 else k
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def skip_string(text, i):
    """Return the index after the string literal that opens at i, or -1 when it does not close."""
    q, n, j = text[i], len(text), i + 1
    limit = i + 50000
    if q != "`":
        while j < n:
            c = text[j]
            if c == "\\":
                j += 2
                continue
            if c == q:
                return j + 1
            if c == "\n":
                return -1
            j += 1
        return -1
    while j < n and j < limit:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "`":
            return j + 1
        if c == "$" and text[j + 1:j + 2] == "{":
            j = skip_braces(text, j + 2, limit)
            if j < 0:
                return -1
            continue
        j += 1
    return -1


def can_open(out, i):
    """Whether a quote at i can start a string. An apostrophe inside JSX text, as in "don't", cannot."""
    j = i - 1
    while j >= 0 and out[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    c = out[j]
    if c in "([{,:=?!&|+-*%^~;<>/\"'`":
        return True
    if c.isalnum() or c in "_$":
        k = j
        while k >= 0 and (out[k].isalnum() or out[k] in "_$"):
            k -= 1
        return "".join(out[k + 1:j + 1]) in KEYWORDS
    return False


def tokenize(text, ext):
    """Return (masked text, string literals as (start, end, quote)). Comments become spaces."""
    css = ext in CSS_EXT
    line_comments = ext not in (".css", ".pcss", ".postcss")
    out, strings, n, i = list(text), [], len(text), 0

    def blank(a, b):
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
    while True:
        m = INTEREST.search(text, i)
        if not m:
            break
        i, tok = m.start(), m.group(0)
        if tok == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif tok == "//":
            if not line_comments or (i > 0 and text[i - 1] == ":"):
                i += 2
                continue
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif tok == "<!--":
            if ext in MARKUP_EXT or ext == ".mdx":
                j = text.find("-->", i + 4)
                j = n if j < 0 else j + 3
                blank(i, j)
                i = j
            else:
                i += 4
        else:
            if not css and not can_open(out, i):
                i += 1
                continue
            end = skip_string(text, i)
            if end < 0:
                i += 1
                continue
            if not css:
                strings.append((i, end, tok))
            i = end
    return "".join(out), strings


# ---------------------------------------------------------------------------------------------------
# JSX helpers

TAG_OPEN = re.compile(r"<([A-Za-z][\w.]*)")
WS = re.compile(r"(?:\s|\{\s*\})*")
HEADING_TAG = re.compile(r"<(h[1-4]|(?:[A-Z][A-Za-z]*)?(?:Title|Heading))(?![\w.-])")
WRAPPER_TAGS = {"div", "span", "header", "hgroup", "section", "Fragment", "motion.div", "motion.span"}


def tag_end(text, i):
    """For the tag that opens at i, return (index after its closing '>', self_closing) or None."""
    n, j, depth, quote = len(text), i + 1, 0, None
    while j < n:
        c = text[j]
        if quote:
            if c == "\\":
                j += 2
                continue
            if c == quote:
                quote = None
        elif depth > 0:
            if c in "\"'`":
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
        elif c == "{":
            depth = 1
        elif c in "\"'":
            quote = c
        elif c == ">":
            return j + 1, text[j - 1] == "/"
        elif c == "<":
            return None
        j += 1
    return None


def element_end(text, i):
    """Return (index after the open tag, index where the close tag starts, index after the close tag) or None."""
    r = tag_end(text, i)
    if not r:
        return None
    open_end, self_closing = r
    if self_closing:
        return open_end, open_end, open_end
    name = TAG_OPEN.match(text, i).group(1)
    pat = re.compile(r"<(/?)%s(?![\w.-])" % re.escape(name))
    depth, pos = 1, open_end
    while True:
        m = pat.search(text, pos)
        if not m:
            return None
        if m.group(1):
            depth -= 1
            if depth == 0:
                gt = text.find(">", m.end())
                return (open_end, m.start(), gt + 1) if gt >= 0 else None
            pos = m.end()
        else:
            r2 = tag_end(text, m.start())
            if r2 and not r2[1]:
                depth += 1
            pos = r2[0] if r2 else m.end()


def owner_tag(text, pos):
    """The JSX tag whose opening tag contains position pos, as (name, start), or None."""
    lo = max(0, pos - 2000)
    i = text.rfind("<", lo, pos)
    while i >= 0:
        if i + 1 < len(text) and text[i + 1].isalpha():
            r = tag_end(text, i)
            if r and r[0] > pos:
                return TAG_OPEN.match(text, i).group(1), i
            return None
        i = text.rfind("<", lo, i)
    return None


def next_is_heading(text, pos, depth=0):
    """Whether the element that follows pos is a heading, or a plain wrapper whose first child is one."""
    pos = WS.match(text, pos).end()
    if text.startswith("<>", pos):
        return depth < 2 and next_is_heading(text, pos + 2, depth + 1)
    m = HEADING_TAG.match(text, pos)
    if m:
        return not re.search(r"Sub-?[Tt]itle", m.group(1))
    m = TAG_OPEN.match(text, pos)
    if m and m.group(1) in WRAPPER_TAGS and depth < 2:
        r = tag_end(text, pos)
        if r and not r[1]:
            return next_is_heading(text, r[0], depth + 1)
    return False


def inner_text(fragment):
    """Static text of a JSX fragment, or None when it holds an expression."""
    if "{" in fragment:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", fragment)).strip()


# ---------------------------------------------------------------------------------------------------
# Tailwind class strings

TW_UTIL = re.compile(
    r"^-?(?:flex|grid|block|inline|hidden|contents|table|absolute|relative|fixed|sticky|static|sr-only|not-sr-only|"
    r"truncate|uppercase|lowercase|capitalize|italic|underline|isolate|overflow|p[xytrblse]?|m[xytrblse]?|space|gap|"
    r"w|h|size|min|max|text|bg|border|rounded|shadow|ring|outline|font|tracking|leading|animate|transition|duration|"
    r"delay|ease|opacity|items|justify|self|place|content|z|top|left|right|bottom|inset|cursor|select|pointer-events|"
    r"resize|group|peer|from|via|to|fill|stroke|divide|object|aspect|col|row|order|grow|shrink|basis|translate|scale|"
    r"rotate|skew|origin|blur|backdrop|brightness|data|aria|line-clamp|whitespace|break|list|decoration|accent|caret|"
    r"scroll|snap|touch|will-change|columns|float|clear|visible|invisible|filter|drop-shadow|fade|slide|zoom|\[)"
    r"(?:$|[-/\[(:])")
STATE_VARIANT = re.compile(r"^(hover|focus|focus-visible|focus-within|active|group|peer|data|aria|disabled|open|checked|"
                           r"selected|in-|has-|not-|enabled|target|first|last|odd|even|empty|invalid|valid|placeholder|"
                           r"file|marker|selection|before|after)")
NON_STATE_VARIANT = re.compile(r"^(dark|light|sm|md|lg|xl|2xl|max-|min-|motion-reduce|motion-safe|print|rtl|ltr|"
                               r"supports-|portrait|landscape|contrast-|forced-colors|@)")
LOADING_GUARD = re.compile(r"skeleton|placeholder|loading|loader|spinner|shimmer|indeterminate|progress|busy|pending|"
                           r"streaming|thinking|saving|syncing|typing|generating|fetching|uploading|processing", re.I)


def split_variants(token):
    """Split 'md:hover:bg-red-500' into (['md', 'hover'], 'bg-red-500'), ignoring colons inside brackets."""
    parts, depth, cur = [], 0, []
    for ch in token:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth = max(0, depth - 1)
        if ch == ":" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    util = "".join(cur)
    return parts, util.lstrip("!")


def is_class_like(text):
    toks = text.split()
    if not toks or len(text) > 30000:
        return False
    hits = sum(1 for t in toks if TW_UTIL.match(split_variants(t)[1]))
    return hits >= 1 and hits * 2 >= len(toks)


def is_state_variant(variants):
    for v in variants:
        if NON_STATE_VARIANT.match(v):
            continue
        if STATE_VARIANT.match(v) or v.startswith("["):
            return True
    return False


ARB = re.compile(r"^(?:text-)?(drop-)?shadow-\[(.+)\]$")
SHADOW_COLOR = re.compile(
    r"^shadow-(?:(?:red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|"
    r"rose)-\d{2,3}|primary|brand|accent|secondary|destructive|danger|info|success|warning)(?:/(\d+))?$")
SHADOW_SIZE = re.compile(r"^shadow-(md|lg|xl|2xl)$")
TRACKING = re.compile(r"^tracking-(wide|wider|widest)$")
EYEBROW_NAME = re.compile(r"(?:^|[-_.])(eyebrow|kicker|overline|pre-?title|over-?title|super-?head|sur-?title|"
                          r"pre-?heading)(?:$|[-_])", re.I)
BOUNCY_NAMES = re.compile(r"bounce|elastic|jelly|rubber|boing", re.I)
PULSE_NAMES = re.compile(r"pulse|ping|glow|breath|throb|heartbeat|beat|float|bob|wiggle|wobble|drift|blob|shine|"
                         r"sparkle|twinkle|gradient-shift|aurora", re.I)
ANIM_KEYWORDS = {"infinite", "alternate", "alternate-reverse", "reverse", "normal", "both", "forwards", "backwards",
                 "none", "running", "paused", "linear", "ease", "ease-in", "ease-out", "ease-in-out", "step-start",
                 "step-end", "initial", "inherit"}
EASE_NAMES = re.compile(r"\b(easeOutBack|easeInBack|easeInOutBack|easeOutElastic|easeInElastic|easeInOutElastic|"
                        r"easeOutBounce|easeInBounce|easeInOutBounce|bounceOut|bounceIn|elasticOut|elasticIn)\b")
EASE_PROP_NAMES = re.compile(r"\bease\w*\s*:\s*['\"](backOut|backIn|backInOut|anticipate)['\"]")
BEZIER = re.compile(r"cubic-bezier\(([^)]*)\)")


def bezier_overshoots(args):
    """A curve whose y values leave 0..1 by more than 5 percent bounces. A 0.5 percent overshoot, as in some drawer
    curves, is a way to keep motion crisp, not a bounce."""
    nums = [float(x) for x in re.findall(r"-?\d*\.?\d+", args.replace("_", " "))]
    return len(nums) == 4 and any(y < -0.05 or y > 1.05 for y in (nums[1], nums[3]))


def anim_name(segment):
    """The keyframes name in one animation shorthand segment, or None."""
    seg = re.sub(r"[\w-]*\([^()]*(?:\([^()]*\)[^()]*)*\)", " ", segment)
    for tok in seg.split():
        if tok in ANIM_KEYWORDS or re.fullmatch(r"-?[\d.]+m?s|-?[\d.]+", tok):
            continue
        return tok
    return None


def property_hint(tokens):
    """CSS properties the element changes on a state, read from its variant classes, as a transition-[...] hint."""
    props = []

    def add(p):
        if p not in props:
            props.append(p)
    for tok in tokens:
        variants, util = split_variants(tok)
        if not is_state_variant(variants) and not re.match(r"^-?(translate|scale|rotate)-", util) \
                and not re.match(r"^opacity-", util):
            continue
        if re.match(r"^-?translate-", util):
            add("translate")
        elif re.match(r"^-?scale-", util):
            add("scale")
        elif re.match(r"^-?rotate-", util):
            add("rotate")
        elif re.match(r"^opacity-", util):
            add("opacity")
        elif re.match(r"^bg-(?!gradient|linear|none)", util):
            add("background-color")
        elif re.match(r"^text-(xs|sm|base|lg|[2-9]?xl|\[\d)", util):
            add("font-size")
        elif re.match(r"^text-(left|right|center|justify|start|end|wrap|nowrap|balance|pretty|ellipsis|clip)$", util):
            continue
        elif util.startswith("text-"):
            add("color")
        elif re.match(r"^border", util):
            add("border-color")
        elif re.match(r"^(shadow|ring|inset-ring)", util):
            add("box-shadow")
        elif re.match(r"^(w|min-w|max-w|size)-", util):
            add("width")
        elif re.match(r"^(h|min-h|max-h)-", util):
            add("height")
        elif re.match(r"^(fill|stroke)-", util):
            add(util.split("-")[0])
        elif re.match(r"^(blur|backdrop)", util):
            add("filter")
    return props


# ---------------------------------------------------------------------------------------------------
# Prose: marketing wording and emoji

LEXICON = [
    (r"seamless(?:ly)?", "seamless", "delete it and say what the product does"),
    (r"effortless(?:ly)?", "effortless", "delete it; ease is shown, not claimed"),
    (r"elevate (?:your|the) \w+", "elevate your", "say what improves, or delete it"),
    (r"unleash", "unleash", "say what the person can now do"),
    (r"supercharg\w*", "supercharge", "say what gets faster or better, and by how much"),
    (r"revolutioni[sz]\w*", "revolutionize", "state the change plainly"),
    (r"cutting[- ]edge", "cutting-edge", "delete it"),
    (r"next[- ]gen(?:eration)?", "next-generation", "delete it"),
    (r"state[- ]of[- ]the[- ]art", "state-of-the-art", "delete it"),
    (r"game[- ]chang\w*", "game-changing", "delete it"),
    (r"best[- ]in[- ]class", "best-in-class", "delete it"),
    (r"world[- ]class", "world-class", "delete it"),
    (r"empower\w*", "empower", "write 'let' or name the action"),
    (r"leverag(?:e|es|ed|ing)", "leverage", "write 'use'"),
    (r"streamlin\w*", "streamline", "write 'simplify', or name the step that goes away"),
    (r"unlock (?:the )?(?:power|potential|magic|full potential)", "unlock the power", "name what the person gets"),
    (r"robust", "robust", "delete it, or name the property (for example 'retries on failure')"),
    (r"delve", "delve", "delete it"),
    (r"transform your", "transform your", "write 'Turn X into Y'"),
    (r"to the next level", "to the next level", "delete it"),
    (r"all[- ]in[- ]one", "all-in-one", "delete it, or list what is included"),
    (r"ai[- ]powered|powered by ai", "AI-powered", "name what the feature does"),
    (r"(?:blazing|lightning)(?:ly)?[- ]fast", "blazing fast", "give the number or delete it"),
    (r"reimagin\w*", "reimagine", "delete it"),
    (r"harness(?:ing)? the power", "harness the power", "delete it"),
    (r"at your fingertips", "at your fingertips", "delete it"),
    (r"say goodbye to", "say goodbye to", "state the outcome"),
    (r"intuitive", "intuitive", "delete it; the interface shows it"),
    (r"powerful", "powerful", "name what it does"),
    (r"holistic", "holistic", "delete it"),
    (r"synerg\w*", "synergy", "delete it"),
    (r"turnkey", "turnkey", "delete it"),
    (r"mission[- ]critical", "mission-critical", "delete it"),
    (r"welcome to the future", "welcome to the future", "delete it"),
    (r"the future of", "the future of", "delete it"),
    (r"in just a few clicks", "in just a few clicks", "give the steps or delete it"),
]
LEXICON_RE = [(re.compile(r"\b(?:%s)\b" % pat, re.I), label, hint) for pat, label, hint in LEXICON]

_EMOJI_BMP = (u"\u231a\u231b\u23e9-\u23f3\u23f8-\u23fa\u24c2\u25aa\u25ab\u25b6\u25c0\u25fb-\u25fe\u2600-\u2604\u260e\u2611"
              u"\u2614\u2615\u2618\u261d\u2620\u2622\u2623\u2626\u262a\u262e\u262f\u2638-\u263a\u2640\u2642\u2648-\u2653"
              u"\u265f\u2660\u2663\u2665\u2666\u2668\u267b\u267e\u267f\u2692-\u2697\u2699\u269b\u269c\u26a0\u26a1\u26a7"
              u"\u26aa\u26ab\u26b0\u26b1\u26bd\u26be\u26c4\u26c5\u26c8\u26ce\u26cf\u26d1\u26d3\u26d4\u26e9\u26ea"
              u"\u26f0-\u26f5\u26f7-\u26fa\u26fd\u2702\u2705\u2708-\u270d\u270f\u2712\u2714\u2716\u271d\u2721\u2728"
              u"\u2733\u2734\u2744\u2747\u274c\u274e\u2753-\u2755\u2757\u2763\u2764\u2795-\u2797\u27a1\u27b0\u27bf"
              u"\u2934\u2935\u2b05-\u2b07\u2b1b\u2b1c\u2b50\u2b55\u3030\u303d\u3297\u3299")
_EMOJI_ONE = u"[\U0001f000-\U0001faff%s]" % _EMOJI_BMP
EMOJI = re.compile(u"%s(?:\ufe0f|\u200d%s|[\U0001f3fb-\U0001f3ff])*\ufe0f?" % (_EMOJI_ONE, _EMOJI_ONE))
EMOJI_ONLY = re.compile(u"^\\s*(?:%s)+\\s*$" % EMOJI.pattern)
GLYPH = re.compile(u"^[\u2713\u2715\u2717\u2718\\s]+(?=\\w)|(?<=\\w)[\\s\u2713\u2715\u2717\u2718]+$")
CODE_LIKE = re.compile(r"[=;{}]|=>|&&|\|\||\b(?:export|import|function|const|let|var|return|await|async|class|interface|"
                       r"type)\s")
JSX_TEXT = re.compile(r"(?<=[>}])([^<>{}]+)(?=[<{])")
CONSOLE_LINE = re.compile(r"console\.\w+\(")


# ---------------------------------------------------------------------------------------------------
# Per-file analysis

class FileScan(object):
    def __init__(self, rel, ext, raw):
        self.rel, self.ext, self.raw = rel, ext, raw
        self.masked, self.strings = tokenize(raw, ext)
        self.starts = [0] + [m.end() for m in re.finditer("\n", raw)]
        self.lines = raw.split("\n")
        self.findings = []
        self.entrance = defaultdict(list)
        self.marketing = []
        self.emoji_only = 0
        self.seen = set()

    def line_at(self, offset):
        return bisect.bisect_right(self.starts, offset)

    def line_text(self, no):
        return self.lines[no - 1] if 0 < no <= len(self.lines) else ""

    def add(self, rule_id, line, value, message, severity=None, anchor=None, snippet=None):
        key = (rule_id, line, value)
        if key in self.seen:
            return
        self.seen.add(key)
        self.findings.append({"rule": rule_id, "severity": severity or RULES[rule_id]["severity"], "file": self.rel,
                              "line": line, "anchor": anchor or line, "value": value[:90],
                              "snippet": (snippet if snippet is not None else value)[:90], "message": message})


def check_decl(fs, line, prop, value, context=""):
    """Checks that apply to one CSS declaration, wherever it was found."""
    p = prop if prop.startswith("--") else prop.lower()
    v = re.sub(r"\s+", " ", value.replace("!important", "")).strip()
    if p in ("transition", "transition-property", "-webkit-transition"):
        segs = [s.strip() for s in split_top(v)]
        hit = any(s.split(None, 1)[0] == "all" for s in segs if s) if p != "transition-property" else v == "all"
        if hit:
            fs.add("src.transition_all", line, "%s: %s" % (p, v[:30]),
                   "%s: %s transitions every property, including layout; name the ones that change, for example "
                   "transition: opacity .2s, transform .2s" % (p, v[:30]), snippet="%s: %s" % (p, v[:40]))
    if p in ("box-shadow", "text-shadow", "-webkit-box-shadow"):
        layer = glow_in_value(v)
        if layer:
            fs.add("src.glow_shadow", line, "%s: %s" % (p, layer[:50]),
                   "%s reads as a glow (colored and blurred); use the project's neutral elevation shadow or none"
                   % p, snippet="%s: %s" % (p, layer[:50]))
    if p in ("filter", "-webkit-filter", "backdrop-filter"):
        for m in re.finditer(r"drop-shadow\(((?:[^()]|\([^()]*\))*)\)", v):
            if glow_layer(m.group(1)):
                fs.add("src.glow_shadow", line, "drop-shadow(%s)" % m.group(1)[:50],
                       "drop-shadow reads as a glow (colored and blurred); use the neutral elevation shadow or none")
    for m in BEZIER.finditer(v):
        if bezier_overshoots(m.group(1)):
            fs.add("src.bounce_easing", line, "cubic-bezier(%s)" % re.sub(r"\s+", "", m.group(1)),
                   "this curve overshoots 1 or dips below 0, which reads as a bounce; use an ease-out curve such as "
                   "cubic-bezier(0.22, 1, 0.36, 1)")


def check_animations(fs, props, prelude):
    """props maps a property to (line, value) for one block; animation names decide bounce and pulse findings."""
    anims = []
    if "animation" in props:
        line, value = props["animation"]
        for seg in split_top(value):
            anims.append((anim_name(seg), "infinite" in seg.split(), line))
    elif "animation-name" in props:
        line, value = props["animation-name"]
        infinite = "infinite" in props.get("animation-iteration-count", (0, ""))[1]
        for nm in split_top(value):
            anims.append((nm.strip(), infinite, line))
    for name, infinite, line in anims:
        if not name or name.startswith("var("):
            continue
        if BOUNCY_NAMES.search(name) and not LOADING_GUARD.search(prelude + " " + fs.rel):
            fs.add("src.bounce_easing", line, "animation: %s" % name,
                   "the %s animation overshoots by design; use an ease-out curve and keep movement under 8px" % name)
        elif infinite and PULSE_NAMES.search(name) and not LOADING_GUARD.search(prelude + " " + name + " " + fs.rel):
            fs.add("src.infinite_pulse", line, "animation: %s infinite" % name,
                   "%s loops forever as decoration; remove the loop, or keep it only for a real loading state" % name)


def check_css_block(fs, prelude, decls, prelude_line):
    props = {}
    for line, prop, value in decls:
        if prop == "@apply":
            check_class_string(fs, value, line, line, None)
            continue
        props[prop] = (line, value)
        check_decl(fs, line, prop, value, prelude)
    check_animations(fs, props, prelude)
    clip = props.get("background-clip") or props.get("-webkit-background-clip")
    if clip and re.match(r"^text\b", clip[1].strip()):
        fill = props.get("-webkit-text-fill-color") or props.get("color")
        transparent = fill and "transparent" in fill[1]
        shimmer = "animation" in props and LOADING_GUARD.search(props["animation"][1])
        if transparent and not shimmer:
            fs.add("src.gradient_text", clip[0], "background-clip: text",
                   "text is filled with a gradient; use a solid text color from the project's tokens")
    eyebrow = EYEBROW_NAME.search(prelude)
    if eyebrow:
        styled = "text-transform" in props or "letter-spacing" in props
        fs.add("src.eyebrow_class", prelude_line, "selector: %s" % prelude.strip()[:50],
               "%s is an eyebrow style; delete the eyebrow or fold its words into the heading" % prelude.strip()[:50],
               severity="WARN" if styled else "INFO")


def css_blocks(text):
    """Return (prelude, [(line, prop, value)], prelude line) for every block of a stylesheet, nested blocks included."""
    frames = [{"prelude": "", "decls": [], "line": 1}]
    done = []
    buf, line, buf_line, i, n = [], 1, 1, 0, len(text)

    def push(decl, at):
        if decl.startswith("@apply"):
            frames[-1]["decls"].append((at, "@apply", decl[6:].strip()))
        elif decl.startswith("@") or ":" not in decl:
            return
        else:
            prop, value = decl.split(":", 1)
            prop = prop.strip()
            if not prop.startswith("--"):
                prop = prop.lower()
            if re.fullmatch(r"-{0,2}[\w-]+", prop):
                frames[-1]["decls"].append((at, prop, value.strip()))
    while i < n:
        ch = text[i]
        if ch in "\"'":
            j = i + 1
            while j < n and text[j] != ch:
                if text[j] == "\\":
                    j += 1
                if j < n and text[j] == "\n":
                    line += 1
                j += 1
            buf.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == "(":
            depth, j = 1, i + 1
            while j < n and depth:
                depth += (text[j] == "(") - (text[j] == ")")
                line += text[j] == "\n"
                j += 1
            buf.append(text[i:j])
            i = j
            continue
        if ch == "{":
            frames.append({"prelude": "".join(buf).strip(), "decls": [], "line": buf_line})
            buf, buf_line = [], line
        elif ch in "};":
            decl = "".join(buf).strip()
            if decl:
                push(decl, buf_line)
            if ch == "}" and len(frames) > 1:
                done.append(frames.pop())
            buf, buf_line = [], line
        else:
            if ch == "\n":
                line += 1
            if buf or not ch.isspace():
                if not buf:
                    buf_line = line
                buf.append(ch)
        i += 1
    done.append(frames[0])
    return [(f["prelude"], f["decls"], f["line"]) for f in done if f["decls"]]


def css_regions(fs):
    """Yield (css text, line offset) for stylesheets, <style> blocks, and styled-components templates."""
    if fs.ext in CSS_EXT:
        yield fs.masked, 0
        return
    if fs.ext in MARKUP_EXT:
        for m in re.finditer(r"<style[^>]*>(.*?)</style>", fs.masked, re.S | re.I):
            yield m.group(1), fs.masked[:m.start(1)].count("\n")
    for m in re.finditer(r"\b(?:styled(?:\.\w+|\([^)]*\))(?:\.attrs\([^)]*\))?|css|keyframes|createGlobalStyle)\s*`",
                         fs.masked):
        end = skip_string(fs.masked, m.end() - 1)
        if end > 0:
            body = re.sub(r"\$\{[^}]*\}", " ", fs.masked[m.end():end - 1])
            yield body, fs.masked[:m.end()].count("\n")


def check_class_string(fs, content, line, anchor, owner, start_offset=None):
    """Checks on one Tailwind class string. owner is (tag name, tag start offset) for an attribute, else None."""
    toks = content.split()
    if not toks:
        return
    parsed = [(t,) + split_variants(t) for t in toks]
    utils = [(t, v, u) for t, v, u in parsed]
    text = " ".join(toks)
    nearby = " ".join(fs.line_text(k) for k in range(max(1, line - 3), line + 1))
    guard = LOADING_GUARD.search(text) or LOADING_GUARD.search(nearby) or LOADING_GUARD.search(fs.rel)

    # gradient text
    if any(u in ("bg-clip-text", "[background-clip:text]") for _, _, u in utils) and \
            any(u in ("text-transparent", "text-fill-transparent", "[-webkit-text-fill-color:transparent]")
                for _, _, u in utils) and not re.search(r"shimmer|skeleton|loading", text):
        fs.add("src.gradient_text", line, "bg-clip-text text-transparent",
               "text is filled with a gradient (bg-clip-text with text-transparent); use a solid text color token",
               anchor=anchor)

    # glow
    for t, v, u in utils:
        m = ARB.match(u)
        if m:
            layer = glow_in_value(m.group(2).replace("_", " "))
            if layer:
                fs.add("src.glow_shadow", line, t[:70],
                       "%s reads as a glow (colored and blurred); use shadow-xs or shadow-sm, or none" % t[:60],
                       anchor=anchor)
        m = re.match(r"^\[(?:box-shadow|text-shadow|filter):(.+)\]$", u)
        if m and glow_in_value(m.group(1).replace("_", " ")):
            fs.add("src.glow_shadow", line, t[:70], "%s reads as a glow; use a neutral elevation shadow or none"
                   % t[:60], anchor=anchor)
    if any(SHADOW_SIZE.match(u) for _, _, u in utils):
        for t, v, u in utils:
            m = SHADOW_COLOR.match(u)
            if m and (m.group(1) is None or int(m.group(1)) >= 15):
                fs.add("src.glow_shadow", line, "%s + shadow size" % u,
                       "%s tints a large shadow with a color, which reads as a glow; drop the color and use a "
                       "neutral shadow-xs or shadow-sm" % u, anchor=anchor)
                break

    # transition-all
    for t, v, u in utils:
        if u == "transition-all":
            props = property_hint(toks)
            note = " (on Tailwind v3 the property is transform)" if set(props) & {"translate", "scale", "rotate"} else ""
            hint = ("; this element changes %s, so use transition-[%s]%s" % (", ".join(props), ",".join(props), note)) \
                if props else \
                "; name the changing properties, for example transition-colors or transition-[opacity,translate]"
            fs.add("src.transition_all", line, "transition-all",
                   "transition-all transitions every property, including layout" + hint, anchor=anchor)
            break

    # bounce, pulse
    for t, v, u in utils:
        if u == "animate-bounce" and not guard:
            fs.add("src.bounce_easing", line, "animate-bounce",
                   "animate-bounce loops an overshooting hop; remove it and show the state with a static mark",
                   anchor=anchor)
        elif u in ("animate-pulse", "animate-ping") or re.match(r"^animate-(glow|float|breathe|blob|wiggle|heartbeat|"
                                                                r"throb|gradient|shine|sparkle|twinkle)", u):
            vtxt = " ".join(v)
            if not guard and not LOADING_GUARD.search(vtxt):
                fs.add("src.infinite_pulse", line, u,
                       "%s loops forever as decoration; remove it, or show state with a static mark and keep "
                       "infinite motion for loading only" % u, anchor=anchor)
        m = re.match(r"^animate-\[(.+)\]$", u)
        if m and not guard:
            seg = m.group(1).replace("_", " ")
            name = anim_name(seg)
            if name and "infinite" in seg.split():
                if BOUNCY_NAMES.search(name):
                    fs.add("src.bounce_easing", line, u[:60], "%s overshoots by design; use an ease-out curve" % name,
                           anchor=anchor)
                elif PULSE_NAMES.search(name):
                    fs.add("src.infinite_pulse", line, u[:60], "%s loops forever as decoration; remove the loop"
                           % name, anchor=anchor)

    # entrance recipes, counted per file and project
    plain = [(t, u) for t, v, u in utils if not v or not is_state_variant(v)]
    plain_utils = [u for t, u in plain]
    if "animate-in" in plain_utils:
        parts = sorted({"fade" if re.match(r"^fade-in", u) else "zoom" if re.match(r"^zoom-in", u)
                        else "slide-" + u.split("-")[3] if re.match(r"^slide-in-from-", u) and len(u.split("-")) > 3
                        else None for u in plain_utils} - {None})
        if parts:
            fs.entrance["animate-in " + " ".join(parts)].append((line, anchor))
    for u in plain_utils:
        if re.match(r"^animate-(fade-in|fade-up|fade-in-up|fade-in-down|slide-up|slide-down|slide-in|reveal|appear|"
                    r"rise|enter)(?:-[\w]+)?$", u) or re.match(r"^animate__fade\w*$", u) or \
                u in ("fade-in-up", "fadeInUp", "fade-up", "fadeUp"):
            fs.entrance[u].append((line, anchor))

    # eyebrow
    names = [u for t, v, u in utils if EYEBROW_NAME.search(u) and not u.startswith(("text-", "bg-", "border-"))]
    if names:
        fs.add("src.eyebrow_class", line, "class %s" % names[0],
               "the class %s names an eyebrow; delete the eyebrow or fold its words into the heading" % names[0],
               anchor=anchor)
    elif owner and start_offset is not None:
        check_eyebrow_style(fs, utils, line, anchor, owner)


def check_eyebrow_style(fs, utils, line, anchor, owner):
    tag, start = owner
    if not (tag in ("p", "span", "div", "small", "strong", "b", "em") or tag.startswith("motion.")
            or tag in ("Text", "Typography", "Caption")):
        return
    base = [u for t, v, u in utils if not is_state_variant(v)]
    upper = any(u in ("uppercase", "[text-transform:uppercase]") for u in base)
    tracked = any(TRACKING.match(u) for u in base)
    if not tracked:
        for u in base:
            m = re.match(r"^tracking-\[(-?[\d.]+)(em|px|rem)\]$", u)
            if m and ((m.group(2) in ("em", "rem") and float(m.group(1)) >= 0.06) or
                      (m.group(2) == "px" and float(m.group(1)) >= 1)):
                tracked = True
    if not (upper and tracked):
        return
    sizes = [u for u in base if re.match(r"^text-(xs|sm|base|lg|xl|[2-9]xl|\[[\d.]+(px|rem)\])$", u)]
    for u in sizes:
        m = re.match(r"^text-\[([\d.]+)(px|rem)\]$", u)
        if u in ("text-base", "text-lg", "text-xl") or re.match(r"^text-[2-9]xl$", u) or \
                (m and to_px(m.group(1), m.group(2)) > 14):
            return
    end = element_end(fs.masked, start)
    if not end:
        return
    open_end, close_start, close_end = end
    text = inner_text(fs.masked[open_end:close_start])
    if text is not None and (len(text) > 60 or not text):
        return
    if next_is_heading(fs.masked, close_end):
        sig = " ".join(sorted(u for u in base if u == "uppercase" or TRACKING.match(u) or u in sizes))
        label = text if text is not None else "{...}"
        fs.add("src.eyebrow_class", line, sig,
               "small uppercase tracked label \"%s\" sits directly above a heading; delete it or fold its words "
               "into the heading" % label[:40], anchor=anchor, snippet="<%s> %s: %s" % (tag, sig, label[:30]))


def check_strings(fs):
    """Class strings, inline style strings, and prose, taken from the string literals."""
    text = fs.masked
    emoji_only = 0
    for s, e, q in fs.strings:
        content = fs.raw[s + 1:e - 1]
        if q == "`":
            content = re.sub(r"\$\{[^}]*\}", " ", content)
        if not content.strip():
            continue
        line = fs.line_at(s)
        if is_class_like(content):
            owner = owner_tag(text, s) if fs.ext in JSX_EXT else None
            check_class_string(fs, content, line, fs.line_at(owner[1]) if owner else line, owner, s)
            continue
        before = text[max(0, s - 30):s]
        if re.search(r"\bclass(?:Name)?\s*=\s*\{?\s*$", before):
            check_class_string(fs, content, line, line, None)
            continue
        if re.search(r"\bstyle\s*=\s*$", before) and re.match(r"^\s*[a-z-]+\s*:", content):
            for part in content.split(";"):
                if ":" in part:
                    prop, value = part.split(":", 1)
                    check_decl(fs, line, prop.strip(), value)
            continue
        if EMOJI_ONLY.match(content):
            emoji_only += 1
        check_prose(fs, content, s + 1, True)
    fs.emoji_only = emoji_only


def check_jsx_text(fs):
    if fs.ext not in JSX_EXT:
        return
    for m in JSX_TEXT.finditer(fs.masked):
        seg = m.group(1)
        if CODE_LIKE.search(seg) or not re.search(r"[A-Za-z\u00c0-\uffff\U0001f000-\U0001faff]", seg):
            continue
        check_prose(fs, seg, m.start(1), False)


def window(text, start, end, pad=24):
    """The text around start:end, trimmed to whole words and marked with ... where it was cut."""
    a, b = max(0, start - pad), min(len(text), end + pad)
    while 0 < a < start and not text[a - 1].isspace():
        a += 1
    while end < b < len(text) and not text[b].isspace():
        b -= 1
    return ("..." if a > 0 else "") + re.sub(r"\s+", " ", text[a:b]).strip() + ("..." if b < len(text) else "")


def check_prose(fs, content, offset, is_string):
    """Marketing wording and emoji in one string literal or JSX text node."""
    first = fs.line_at(offset)
    if CONSOLE_LINE.search(fs.line_text(first)):
        return
    if is_string and (re.match(r"^[\w@/.:#-]+$", content.strip()) and not EMOJI.search(content)):
        return
    words = len(re.findall(r"[A-Za-z']+", content))
    if words >= 2:
        hits = []
        for pat, label, hint in LEXICON_RE:
            for m in pat.finditer(content):
                hits.append((m.start(), label, hint, m.group(0)))
        if hits:
            hits.sort()
            pos, found = hits[0][0], hits[0][3]
            fs.marketing.append({"line": fs.line_at(offset + pos), "labels": [h[1] for h in hits],
                                 "hints": [(h[1], h[2]) for h in hits],
                                 "ctx": window(content, pos, pos + len(found), 28)})
    for m in EMOJI.finditer(content):
        ln = fs.line_at(offset + m.start())
        ctx = window(content, m.start(), m.end(), 20)
        fs.add("src.emoji_label", ln, m.group(0),
               "emoji %s used as an icon or label in \"%s\"; use the project's icon component or delete it"
               % (m.group(0), ctx), snippet=ctx)
    if words >= 2:
        gm = GLYPH.search(content.strip())
        if gm and not EMOJI.search(content):
            ln = fs.line_at(offset)
            ctx = re.sub(r"\s+", " ", content.strip())[:40]
            fs.add("src.emoji_label", ln, gm.group(0).strip() or "glyph",
                   "the text glyph %s stands in for an icon in \"%s\"; use the project's icon component"
                   % (gm.group(0).strip(), ctx), severity="INFO", snippet=ctx)


def check_text_patterns(fs):
    """Whole-text patterns: easing names, springs, motion entrances, eyebrow components."""
    text = fs.masked
    for m in BEZIER.finditer(text):
        if bezier_overshoots(m.group(1)):
            fs.add("src.bounce_easing", fs.line_at(m.start()), "cubic-bezier(%s)" % re.sub(r"[\s_]+", "", m.group(1)),
                   "this curve overshoots 1 or dips below 0, which reads as a bounce; use an ease-out curve such as "
                   "cubic-bezier(0.22, 1, 0.36, 1)")
    for m in re.finditer(r"\bease\w*\s*:\s*\[\s*(-?[\d.]+(?:\s*,\s*-?[\d.]+){3})\s*\]", text):
        if bezier_overshoots(m.group(1)):
            fs.add("src.bounce_easing", fs.line_at(m.start()), "ease: [%s]" % re.sub(r"\s+", "", m.group(1)),
                   "this curve overshoots 1 or dips below 0, which reads as a bounce; use an ease-out curve such as "
                   "[0.22, 1, 0.36, 1]")
    for m in list(EASE_NAMES.finditer(text)) + list(EASE_PROP_NAMES.finditer(text)):
        name = m.group(1)
        fs.add("src.bounce_easing", fs.line_at(m.start()), name,
               "%s overshoots its target; use an ease-out curve with no overshoot" % name)
    for m in re.finditer(r"\bbounce\s*:\s*(\d*\.?\d+)", text):
        value = float(m.group(1))
        around = text[max(0, m.start() - 160):m.end() + 160]
        if 0.3 <= value <= 1 and re.search(r"spring", around):
            fs.add("src.bounce_easing", fs.line_at(m.start()), "spring bounce: %s" % m.group(1),
                   "a spring with bounce %s overshoots; set bounce to 0 and let the layout settle" % m.group(1))
    for m in re.finditer(r"config\.(wobbly|bouncy)\b", text):
        fs.add("src.bounce_easing", fs.line_at(m.start()), "config.%s" % m.group(1),
               "the %s spring preset overshoots; use a default or stiff preset" % m.group(1))
    # motion entrances
    for m in re.finditer(r"\binitial\s*=\s*\{\{([^{}]*)\}\}", text):
        body = re.sub(r"\s+", "", m.group(1))
        if re.search(r"\bopacity:0\b", body) and re.search(r"\b(y|x|scale):-?\d", body) and "height" not in body:
            fs.entrance["motion initial " + body[:50]].append((fs.line_at(m.start()), fs.line_at(m.start())))
    views = [fs.line_at(m.start()) for m in re.finditer(r"\bwhileInView\s*=", text)]
    if len(views) >= 3:
        fs.entrance["whileInView"] = [(ln, ln) for ln in views]
    aos = [fs.line_at(m.start()) for m in re.finditer(r"\bdata-(?:aos|sal)\s*=", text)]
    if len(aos) >= 3:
        fs.entrance["data-aos"] = [(ln, ln) for ln in aos]
    # style objects and attributes
    for m in re.finditer(r"\b(transition|transitionProperty|boxShadow|textShadow|filter|animationName|"
                         r"animationTimingFunction|transitionTimingFunction)\s*:\s*(['\"`])((?:\\.|(?!\2)[^\n\\])*)\2",
                         text):
        prop = re.sub(r"([A-Z])", lambda k: "-" + k.group(1).lower(), m.group(1))
        check_decl(fs, fs.line_at(m.start()), prop, m.group(3))
    for m in re.finditer(r"\bstyle\s*=\s*\{\{(.{0,600}?)\}\}", text, re.S):
        body = m.group(1)
        if re.search(r"background-?[Cc]lip['\"]?\s*:\s*['\"]text", body) and \
                re.search(r"(?:TextFillColor|color)['\"]?\s*:\s*['\"]transparent", body):
            fs.add("src.gradient_text", fs.line_at(m.start()), "style backgroundClip: text",
                   "text is filled with a gradient; use a solid text color from the project's tokens")
    for m in re.finditer(r"<(Eyebrow|Kicker|Overline|PreTitle|Pretitle|Overtitle)(?![\w.-])", text):
        fs.add("src.eyebrow_class", fs.line_at(m.start()), "<%s>" % m.group(1),
               "<%s> renders an eyebrow; delete it or fold its words into the heading it sits above" % m.group(1))


STOP = set("a an the of to for and or in on at by with this that your you our is are be from it as into".split())


def words_of(text):
    return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP and len(w) > 1}


def hidden_parent(text, pos):
    """Whether the tag open just before pos is still open and visually hidden (sr-only), as in a hidden sheet header."""
    lo = max(0, pos - 300)
    i = text.rfind("<", lo, pos)
    while i >= 0:
        if text[i + 1:i + 2].isalpha():
            r = tag_end(text, i)
            if r and r[0] <= pos and not r[1] and "sr-only" in text[i:r[0]] and "</" not in text[r[0]:pos]:
                return True
            return False
        i = text.rfind("<", lo, i)
    return False


COPY_TITLE = re.compile(r"^(?:h[1-3]|(?:Card|Section|Page|Panel|Widget|Tile|Feature|Hero)?(?:Title|Heading))$")
OVERLAY_TITLE = re.compile(r"^(?:AlertDialog|Dialog|Sheet|Drawer|Popover|Modal)(?:Title|Heading)$")


def check_descriptions(fs):
    """Count titles in card, section, and page headers that carry a static description line, and flag a description
    that restates its title anywhere. Overlays (dialogs, sheets) need a description for screen readers, so they only
    count toward restating. Lists, alerts, empty states, and fields are skipped: their lines are data or hints."""
    if fs.ext not in JSX_EXT:
        return
    text = fs.masked
    titles, pairs = 0, []
    muted = re.compile(r"text-(?:muted-foreground|(?:text-)?(?:secondary|tertiary|muted)\b|gray-|slate-|zinc-|"
                       r"neutral-|stone-)")
    for m in re.finditer(r"<([A-Za-z][A-Za-z0-9]*)(?![\w.-])", text):
        name = m.group(1)
        copy_title, overlay_title = COPY_TITLE.match(name), OVERLAY_TITLE.match(name)
        if not (copy_title or overlay_title):
            continue
        end = element_end(text, m.start())
        if not end:
            continue
        if hidden_parent(text, m.start()):
            continue
        if copy_title:
            titles += 1
        nxt = WS.match(text, end[2]).end()
        desc = re.compile(r"<([A-Z][A-Za-z]*Description)(?![\w.-])").match(text, nxt)
        if desc and re.match(r"<(?:Empty|Item|Alert(?!Dialog)|Field|Attachment)", desc.group(0)):
            continue
        if not desc and copy_title and re.match(r"^(?:h[1-3]|[A-Z]\w*Title)$", name):
            pm = re.compile(r"<p(?![\w.-])").match(text, nxt)
            tg = tag_end(text, nxt) if pm else None
            if tg and muted.search(text[nxt:tg[0]]):
                desc = pm
        if not desc:
            continue
        dend = element_end(text, nxt)
        if not dend or "sr-only" in text[nxt:dend[0]]:
            continue
        body = text[dend[0]:dend[1]]
        static = len(re.findall(r"[A-Za-z']+", re.sub(r"\{[^{}]*\}", " ", re.sub(r"<[^>]*>", " ", body))))
        pairs.append({"pos": m.start(), "title": inner_text(text[end[0]:end[1]]), "desc": inner_text(body),
                      "counted": bool(copy_title) and static >= 3})
    for pair in pairs:
        title, desc = pair["title"], pair["desc"]
        if title and desc:
            tw, dw = words_of(title), words_of(desc)
            if tw and dw and len(tw & dw) / float(len(tw)) >= 0.75 and len(dw - tw) <= 2:
                fs.add("src.description_everywhere", fs.line_at(pair["pos"]), "restates: %s" % title[:40],
                       "the description \"%s\" repeats its title \"%s\"; delete it or add a condition, consequence, "
                       "or next step" % (desc[:50], title[:30]), severity="WARN")
    counted = [pair for pair in pairs if pair["counted"]]
    if len(counted) >= 3 and titles and len(counted) / float(titles) >= 0.75:
        fs.add("src.description_everywhere", fs.line_at(counted[0]["pos"]), "title and description pairs",
               "%d of %d titled blocks in this file carry a description line; keep one only where it adds a "
               "condition, consequence, example, or next step" % (len(counted), titles))


def finish_entrance(fs):
    for key, occ in fs.entrance.items():
        if len(occ) >= 3:
            fs.add("src.entrance_everywhere", occ[0][0], key,
                   "%d elements in this file enter with the same animation (%s); keep one authored entrance and "
                   "delete the rest" % (len(occ), key[:40]), anchor=occ[0][1])


def finish_marketing(fs):
    total = sum(len(item["labels"]) for item in fs.marketing)
    for item in fs.marketing:
        sev = "WARN" if len(item["labels"]) > 1 or total >= 3 else "INFO"
        advice = "; ".join("%s: %s" % pair for pair in item["hints"])
        fs.add("src.marketing_words", item["line"], " + ".join(item["labels"]),
               "launch-page wording in \"%s\" (%s)" % (item["ctx"], advice), severity=sev, snippet=item["ctx"])


def analyze(fs):
    for text, offset in css_regions(fs):
        saved = fs.findings
        for prelude, decls, prelude_line in css_blocks(text):
            check_css_block(fs, prelude, [(ln + offset, p, v) for ln, p, v in decls], prelude_line + offset)
        fs.findings = saved
    if fs.ext in CODE_EXT or fs.ext in MARKUP_EXT:
        check_strings(fs)
        check_jsx_text(fs)
        check_text_patterns(fs)
        check_descriptions(fs)
        if fs.emoji_only >= 5 or re.search(r"emoji|reaction", fs.rel, re.I):
            fs.findings = [f for f in fs.findings if f["rule"] != "src.emoji_label"]
    finish_entrance(fs)
    finish_marketing(fs)


# ---------------------------------------------------------------------------------------------------
# slop-allow comments

ALLOW = re.compile(r"slop-allow(-file)?\s*:\s*([A-Za-z_.,*-]+)(.*)")
EMPTY_LINE = re.compile(r"\{?\s*\}?")


def parse_allows(fs):
    allows, masked_lines = [], fs.masked.split("\n")
    for no, raw in enumerate(fs.lines, 1):
        m = ALLOW.search(raw)
        if not m:
            continue
        ids = [r.strip() for r in m.group(2).split(",") if r.strip()]
        ids = [r if r.startswith("src.") else "src." + r for r in ids]
        reason = re.sub(r"\s*(\*/\s*\}?|-->)\s*$", "", m.group(3).strip()).strip(" -:*\t")
        covers = {no}
        if no - 1 < len(masked_lines) and EMPTY_LINE.fullmatch(masked_lines[no - 1].strip()):
            for k in range(no, len(masked_lines)):
                if masked_lines[k].strip() and not EMPTY_LINE.fullmatch(masked_lines[k].strip()):
                    covers.add(k + 1)
                    break
        allows.append({"line": no, "rules": ids, "reason": reason, "file_level": bool(m.group(1)),
                       "valid": len(reason.split()) >= 2 and len(reason) >= 8, "covers": covers})
    return allows


def apply_allows(fs):
    """Split findings into (kept, allowed). A slop-allow without a reason suppresses nothing and is a finding."""
    allows = parse_allows(fs)
    kept, allowed = [], []
    for f in fs.findings:
        hit = None
        for a in allows:
            if a["valid"] and f["rule"] in a["rules"] and (a["file_level"] or f["line"] in a["covers"]
                                                           or f["anchor"] in a["covers"]):
                hit = a
                break
        if hit:
            allowed.append(dict(f, reason=hit["reason"]))
        else:
            kept.append(f)
    for a in allows:
        unknown = [r for r in a["rules"] if r not in RULES]
        if not a["valid"] or unknown:
            what = ("names unknown rule %s" % ", ".join(unknown)) if unknown else \
                "has no usable reason (write at least two words after the rule id)"
            kept.append({"rule": "src.allow_reason", "severity": "WARN", "file": fs.rel, "line": a["line"],
                         "anchor": a["line"], "value": "slop-allow", "snippet": fs.line_text(a["line"]).strip()[:90],
                         "message": "this slop-allow comment %s; it suppresses nothing until it does" % what})
    return kept, allowed


# ---------------------------------------------------------------------------------------------------
# Files

def guard_reason(rel, include_tests):
    parts = ("/" + rel).split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return "vendored or build folder"
    if not include_tests and GUARD_PATH.search("/" + rel):
        return "test, story, or fixture file"
    return None


def walk(root):
    root = os.path.abspath(root)
    if os.path.isfile(root):
        yield root
        return
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() in CSS_EXT | MARKUP_EXT | CODE_EXT:
                yield os.path.join(base, name)


def read_text(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def collect(root, files, ignore, include_tests):
    """Return ([(rel, abs)], skipped count)."""
    base = os.path.abspath(root) if os.path.isdir(root) else os.path.dirname(os.path.abspath(root))
    if files:
        paths = []
        for f in files:
            f = f.strip()
            if not f:
                continue
            cand = f if os.path.isabs(f) else (os.path.join(os.getcwd(), f) if os.path.exists(f) else os.path.join(base, f))
            if os.path.isfile(cand) and os.path.splitext(cand)[1].lower() in CSS_EXT | MARKUP_EXT | CODE_EXT:
                paths.append(os.path.abspath(cand))
    else:
        paths = list(walk(root))
    out, skipped = [], 0
    for p in paths:
        rel = os.path.relpath(p, base).replace(os.sep, "/")
        if rel.split("/", 1)[0] == os.pardir:
            rel = os.path.basename(p)
        if guard_reason(rel, include_tests) or any(frag in rel for frag in ignore):
            skipped += 1
            continue
        out.append((rel, p))
    return out, skipped


def scan_files(entries):
    findings, allowed, scanned, skipped = [], [], 0, 0
    entrance_global = defaultdict(list)
    for rel, path in entries:
        raw = read_text(path)
        if raw is None or GENERATED.search(raw[:400]):
            skipped += 1
            continue
        if max((len(l) for l in raw.split("\n")), default=0) > 8000:
            skipped += 1
            continue
        scanned += 1
        fs = FileScan(rel, os.path.splitext(path)[1].lower(), raw)
        analyze(fs)
        for key, occ in fs.entrance.items():
            if len(occ) < 3:
                entrance_global[key].append((rel, occ[0][0], len(occ)))
        kept, ok = apply_allows(fs)
        findings.extend(kept)
        allowed.extend(ok)
    for key, places in entrance_global.items():
        files = {p[0] for p in places}
        if len(files) >= 4 and sum(p[2] for p in places) >= 6 and not key.startswith(("motion", "whileInView", "data-aos")):
            rel, line, _ = sorted(places)[0]
            findings.append({"rule": "src.entrance_everywhere", "severity": "INFO", "file": rel, "line": line,
                             "anchor": line, "value": "project: " + key[:60], "snippet": key[:60],
                             "message": "the entrance animation (%s) repeats in %d files; keep one authored entrance "
                                        "and delete the rest" % (key[:40], len(files))})
    findings.sort(key=lambda f: (f["rule"], f["file"], f["line"]))
    return findings, allowed, scanned, skipped


# ---------------------------------------------------------------------------------------------------
# Commands

def keyed(findings):
    counts = Counter()
    for f in findings:
        counts["%s|%s|%s" % (f["rule"], f["file"], f["value"])] += 1
    return counts


def cmd_scan(args):
    if not os.path.exists(args.path):
        print("path not found: %s" % args.path, file=sys.stderr)
        return 2
    partial = args.files is not None or args.stdin
    if args.shrink and (partial or not args.baseline):
        print("--shrink needs --baseline and a full scan (no --files or --stdin)", file=sys.stderr)
        return 2
    files = list(args.files or [])
    if args.stdin:
        files.extend(sys.stdin.read().splitlines())
    if partial and not [f for f in files if f.strip()]:
        entries, skipped_guard = [], 0
    else:
        entries, skipped_guard = collect(args.path, files, args.ignore or [], args.include_tests)
    findings, allowed, scanned, skipped = scan_files(entries)
    counts = keyed(findings)
    base_counts = {}
    if args.baseline and os.path.isfile(args.baseline):
        with io.open(args.baseline, encoding="utf-8") as fh:
            base_counts = json.load(fh).get("counts", {})
    elif args.baseline and not args.write_baseline:
        print("baseline file %s not found; create it with --write-baseline" % args.baseline, file=sys.stderr)
        return 2
    if args.write_baseline:
        if os.path.exists(args.write_baseline) and not args.force:
            print("%s exists; use --shrink to lower it or --force to overwrite" % args.write_baseline, file=sys.stderr)
            return 2
        with io.open(args.write_baseline, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "counts": dict(sorted(counts.items()))}, fh, indent=1)
        print("baseline written: %d keys, %d findings" % (len(counts), sum(counts.values())))
        return 0
    shrunk = 0
    if args.shrink:
        merged = {k: min(v, counts.get(k, 0)) for k, v in base_counts.items() if counts.get(k, 0) > 0}
        shrunk = sum(base_counts.values()) - sum(merged.values())
        with io.open(args.baseline, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "counts": dict(sorted(merged.items()))}, fh, indent=1)
    shown = findings
    if args.baseline:
        # a baselined key hides that many findings; any beyond its count are new
        budget, shown = dict(base_counts), []
        for f in findings:
            key = "%s|%s|%s" % (f["rule"], f["file"], f["value"])
            if budget.get(key, 0) > 0:
                budget[key] -= 1
            else:
                shown.append(f)
    if args.rule:
        shown = [f for f in shown if f["rule"] in args.rule]
        allowed = [f for f in allowed if f["rule"] in args.rule]
    severity = Counter(f["severity"] for f in shown)
    summary = Counter(f["rule"] for f in shown)
    if args.format == "json":
        print(json.dumps({"tool": "slop_scan", "scanned": scanned, "skipped": skipped + skipped_guard,
                          "findings": shown, "allowed": allowed, "summary": dict(summary),
                          "severity": dict(severity), "baselined": sum(base_counts.values()), "shrunk": shrunk},
                         indent=1))
    else:
        print(render_md(shown, allowed, scanned, skipped + skipped_guard, severity, args, base_counts, shrunk))
    fail_levels = {"warn": ("WARN",), "info": ("WARN", "INFO"), "never": ()}[args.fail_on]
    return 1 if any(f["severity"] in fail_levels for f in shown) else 0


def render_md(shown, allowed, scanned, skipped, severity, args, base_counts, shrunk):
    label = "new findings" if args.baseline else "findings"
    sev = ", ".join("%s %d" % (k, severity[k]) for k in ("WARN", "INFO") if severity.get(k)) or "none"
    lines = ["# slop_scan", "",
             "Scanned %d files (%d skipped by guards): %d %s (%s), %d allowed by comment."
             % (scanned, skipped, len(shown), label, sev, len(allowed))]
    if args.baseline:
        lines.append("Baselined: %d, shrunk this run: %d." % (sum(base_counts.values()), shrunk))
    by_rule = defaultdict(list)
    for f in shown:
        by_rule[f["rule"]].append(f)
    for rule_id in sorted(by_rule):
        items = by_rule[rule_id]
        lines += ["", "## %s (%d)" % (rule_id, len(items)), "", "%s. Fix: %s" % (RULES[rule_id]["summary"],
                                                                                 RULES[rule_id]["fix"]), ""]
        for f in items[:args.max]:
            lines.append("- `%s:%d` %s `%s`: %s" % (f["file"], f["line"], f["severity"],
                                                    f["snippet"][:60].replace("`", "'"), f["message"]))
        if len(items) > args.max:
            lines.append("- ... %d more %s findings (raise --max)" % (len(items) - args.max, rule_id))
    if allowed:
        lines += ["", "## Allowed by comment (%d)" % len(allowed), ""]
        for f in allowed[:args.max]:
            lines.append("- `%s:%d` %s: %s" % (f["file"], f["line"], f["rule"], f["reason"]))
    return "\n".join(lines)


def cmd_rules(args):
    if args.format == "json":
        print(json.dumps([dict(id=k, **v) for k, v in sorted(RULES.items())], indent=1))
    else:
        for k, v in sorted(RULES.items()):
            print("%-28s %-5s %s\n%s  Fix: %s" % (k, v["severity"], v["summary"], " " * 34, v["fix"]))
    return 0


# ---------------------------------------------------------------------------------------------------
# Selftest. Each fixture marks the lines that must produce a finding with `expect: rule`, or the next line
# with `expect-next: rule`, inside a comment. Every other line must stay quiet.

FIXTURES = {
    "src/eyebrow.tsx": """export function Eyebrows() {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Initiative</p> {/* expect: src.eyebrow_class */}
      <h1 className="text-2xl font-bold">Roadmap</h1>
      <div className="stack">
        <span className="text-[11px] uppercase tracking-[0.12em]">Plans</span> {/* expect: src.eyebrow_class */}
        <div>
          <h2 className="text-lg">Pricing</h2>
        </div>
      </div>
      <p className="text-xs font-semibold uppercase tracking-wider">Daily errors</p>
      <div role="img" aria-label="chart" />
      <table><thead><tr><th className="text-xs uppercase tracking-wide">Name</th></tr></thead></table>
      <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Section label</h3>
      <label className="text-xs uppercase tracking-wide">Email</label>
      <h2 className="text-lg">Next</h2>
      <p className="text-base uppercase tracking-widest">Big label</p>
      <h2 className="text-lg">After big</h2>
      <span className="eyebrow">Launch</span> {/* expect: src.eyebrow_class */}
      <Eyebrow>Release notes</Eyebrow> {/* expect: src.eyebrow_class */}
      <p className="text-sm uppercase">No tracking</p>
      <h2 className="text-lg">After no tracking</h2>
      {/* slop-allow: src.eyebrow_class the label states the plan tier */}
      <p
        className="text-xs font-medium uppercase tracking-wide"
      >
        Team plan
      </p>
      <h1 className="text-xl">Billing</h1>
      <p
        className="text-xs font-medium uppercase tracking-wide" /* expect: src.eyebrow_class */
      >
        Team plan
      </p>
      <h1 className="text-xl">Seats</h1>
      <p className={`text-xs uppercase tracking-widest ${tone}`}>Kicker</p> {/* expect: src.eyebrow_class */}
      <h2 className="text-xl">After template</h2>
    </div>
  )
}
""",
    "src/gradient.tsx": """export function Gradient() {
  return (
    <>
      <h1 className="bg-gradient-to-r from-purple-500 to-blue-500 bg-clip-text text-transparent">Hello</h1> {/* expect: src.gradient_text */}
      <h2 style={{ backgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>Hi</h2> {/* expect: src.gradient_text */}
      <input className="bg-clip-padding placeholder:text-transparent" />
      <p className="text-transparent">Invisible on purpose</p>
      <span className="bg-clip-text text-transparent animate-shimmer-skeleton">Loading label</span>
    </>
  )
}
""",
    "src/glow.tsx": """export function Glow() {
  return (
    <>
      <div className="shadow-[0_0_24px_rgba(139,92,246,0.5)]" /> {/* expect: src.glow_shadow */}
      <div className="shadow-lg shadow-purple-500/25" /> {/* expect: src.glow_shadow */}
      <div className="drop-shadow-[0_0_8px_#6366f1]" /> {/* expect: src.glow_shadow */}
      <div style={{ boxShadow: '0 0 30px rgba(99, 102, 241, 0.6)' }} /> {/* expect: src.glow_shadow */}
      <div className="shadow-md" />
      <div className="shadow-[0_1px_2px_rgba(0,0,0,0.06)]" />
      <div className="shadow-[0_0_0_3px_var(--wash)]" />
      <div className="shadow-[inset_0_2px_42px_0_rgba(255,255,255,0.16),0_1px_2px_0_rgba(0,0,0,0.06)]" />
      <div className="shadow-sm shadow-primary/5" />
      <div className="shadow-[0_10px_30px_rgba(0,0,0,0.25)]" />
      <div style={{ boxShadow: '0 1px 3px rgba(0,0,0,0.1)' }} />
    </>
  )
}
""",
    "src/motion.tsx": """export function Motion({ open }) {
  return (
    <>
      <button className="transition-all duration-200 hover:bg-accent hover:text-xs">A</button> {/* expect: src.transition_all */}
      <div className="translate-x-1 opacity-0 transition-all group-hover:translate-x-0 group-hover:opacity-100" /> {/* expect: src.transition_all */}
      <div style={{ transition: 'all 0.3s ease' }} /> {/* expect: src.transition_all */}
      <button className="transition-colors hover:bg-accent">B</button>
      <button className="transition-[color,background-color] hover:bg-accent">C</button>
      <div className="ease-[cubic-bezier(0.68,-0.55,0.265,1.55)] duration-300" /> {/* expect: src.bounce_easing */}
      <div className="ease-[cubic-bezier(0.22,1,0.36,1)] duration-300" />
      <div className="animate-bounce" /> {/* expect: src.bounce_easing */}
      <div className="animate-bounce" aria-label="typing indicator" />
      <motion.div transition={{ type: 'spring', bounce: 0.5 }} /> {/* expect: src.bounce_easing */}
      <motion.div transition={{ type: 'spring', bounce: 0 }} dragTransition={{ bounceStiffness: 200 }} />
      <motion.div transition={{ type: 'spring', bounce: 0.25 }} />
      <span className="size-2 rounded-full bg-green-500 animate-pulse" /> {/* expect: src.infinite_pulse */}
      <div className="h-4 w-full animate-pulse rounded bg-muted" data-slot="skeleton" />
      <div className="data-indeterminate:animate-pulse" />
      <svg className="size-4 animate-spin" />
      <div className={cn('size-2', isStreaming && 'animate-pulse')} />
      <section className="animate-in fade-in slide-in-from-bottom-4 duration-700" /> {/* expect: src.entrance_everywhere */}
      <section className="animate-in fade-in slide-in-from-bottom-4 duration-500" />
      <section className="animate-in fade-in slide-in-from-bottom-4 delay-150" />
      <div className="data-open:animate-in data-open:fade-in-0" />
      <div className="data-open:animate-in data-open:fade-in-0" />
      <div className="data-open:animate-in data-open:fade-in-0" />
      <div className="animate-in fade-in-0" />
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} /> {/* expect: src.entrance_everywhere */}
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} />
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} />
      <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} />
      <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} />
      <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} />
    </>
  )
}
""",
    "src/easing.ts": """import { easeOutBack } from 'd3-ease' // expect: src.bounce_easing
export const drawer = 'ease-[cubic-bezier(0.45,1.005,0,1.005)]'
export const a = { ease: [0.34, 1.56, 0.64, 1] } // expect: src.bounce_easing
export const b = { ease: 'backOut' } // expect: src.bounce_easing
export const c = { ease: [0.16, 1, 0.3, 1] }
export const metrics = { bounce: 0.5 }
""",
    "src/styles.css": """/* .glow { box-shadow: 0 0 40px purple } is ignored inside a comment */
.eyebrow { /* expect: src.eyebrow_class */
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-size: 12px;
}
.hero-title {
  background: linear-gradient(90deg, #8b5cf6, #3b82f6);
  -webkit-background-clip: text; /* expect: src.gradient_text */
  -webkit-text-fill-color: transparent;
}
.shimmer-text {
  background-clip: text;
  -webkit-text-fill-color: transparent;
  animation: tw-shimmer 2s linear infinite;
}
.glow {
  box-shadow: 0 0 30px rgba(99, 102, 241, 0.6); /* expect: src.glow_shadow */
}
.card { box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1), 0 0 0 1px rgba(0, 0, 0, 0.05); }
.t1 { transition: all 0.3s ease; } /* expect: src.transition_all */
.t2 { transition: opacity 0.2s, transform 0.2s; }
.t3 { @apply transition-all duration-300; } /* expect: src.transition_all */
.b1 { animation: bounce 1s infinite; } /* expect: src.bounce_easing */
.b2 { animation-timing-function: cubic-bezier(0.34, 1.56, 0.64, 1); } /* expect: src.bounce_easing */
.p1 { animation: pulse 2s infinite; } /* expect: src.infinite_pulse */
.p2 { animation: o-dot-breathe 3.6s ease-in-out infinite; } /* expect: src.infinite_pulse */
.s1 { animation: spin 1s linear infinite; }
.skeleton { animation: pulse 2s infinite; }
.once { animation: pulse 2s 3; }
""",
    "src/copy.tsx": """export function Copy() {
  const label = 'Unlock the power of your specs' // expect: src.marketing_words
  return (
    <div>
      <p>Effortlessly turn ideas into specs.</p> {/* expect: src.marketing_words */}
      <p>Plain sentence about exports.</p>
      <p>Done \U0001f389</p> {/* expect: src.emoji_label */}
      <h2>\U0001f680 Get started</h2> {/* expect: src.emoji_label */}
      <Button aria-label="Warn">\u26a0\ufe0f</Button> {/* expect: src.emoji_label */}
      <p>\u2713 Saved to your workspace</p> {/* expect: src.emoji_label */}
      <p>Don't worry, it's saved.</p>
      <p>Use the leverage control on the chart</p> {/* expect: src.marketing_words */}
      {/* a seamless comment is ignored */}
      <input placeholder="Search specs" className="seamless-input" />
    </div>
  )
}
const seamless = 'x'
console.log('saved \u2713 done')
""",
    "src/descriptions.tsx": """export function Dialogs() {
  return (
    <>
      <DialogHeader>
        <DialogTitle>Share spec</DialogTitle> {/* expect: src.description_everywhere */}
        <DialogDescription>Share this spec with others.</DialogDescription>
      </DialogHeader>
      <CardHeader>
        <CardTitle>Billing</CardTitle>
        <CardDescription>Manage how you pay for the workspace.</CardDescription>
      </CardHeader>
      <CardHeader>
        <CardTitle>Members</CardTitle>
        <CardDescription className="sr-only">Hidden for screen readers.</CardDescription>
      </CardHeader>
      <EmptyHeader>
        <EmptyTitle>No specs</EmptyTitle>
        <EmptyDescription>Start one from an idea.</EmptyDescription>
      </EmptyHeader>
    </>
  )
}
""",
    "src/hidden-header.tsx": """export const H = () => (
  <SheetHeader className="sr-only">
    <SheetTitle>Sidebar</SheetTitle>
    <SheetDescription>Displays the mobile sidebar.</SheetDescription>
  </SheetHeader>
)
""",
    "src/many.tsx": """export function Many() {
  return (
    <>
      <CardHeader>
        <CardTitle>Alpha</CardTitle> {/* expect: src.description_everywhere */}
        <CardDescription>First thing to configure.</CardDescription>
      </CardHeader>
      <CardHeader>
        <CardTitle>Beta</CardTitle>
        <CardDescription>Second thing to configure.</CardDescription>
      </CardHeader>
      <CardHeader>
        <CardTitle>Gamma</CardTitle>
        <CardDescription>Third thing to configure.</CardDescription>
      </CardHeader>
    </>
  )
}
""",
    "src/allow.tsx": """export function Allow() {
  return (
    <>
      {/* slop-allow: src.transition_all the bar width animates with the score */}
      <div className="transition-all duration-700" />
      <div className="transition-all duration-700" /> {/* slop-allow: transition_all width follows the score */}
      <div className="transition-all" /> {/* expect: src.transition_all */}
      {/* expect-next: src.allow_reason */}
      {/* slop-allow: src.transition_all */}
      <div className="transition-all" /> {/* expect: src.transition_all */}
      {/* expect-next: src.allow_reason */}
      {/* slop-allow: src.not_a_rule because it is a typo */}
      <p className="text-xs uppercase tracking-wide">
        <span>ok</span>
      </p>
    </>
  )
}
""",
    "src/allow-file.tsx": """// slop-allow-file: src.marketing_words this file is the public landing copy
export const Landing = () => <p>Seamless, powerful, and cutting-edge.</p>
""",
    "src/probe.tsx": """const v = cva("inline-flex transition-all duration-200", { /* expect: src.transition_all */
  variants: { tone: { brand: "shadow-lg shadow-indigo-500/30", plain: "shadow-sm" } }, /* expect: src.glow_shadow */
})
export function Probe({ ok }) {
  const cls = `rounded-md ${ok ? 'p-2' : 'p-4'} transition-all` // expect: src.transition_all
  return (
    <div>
      <p>Don't use 'single' quotes in text, it's fine</p>
      <a href="https://example.com/a//b" className={cn("flex", ok && "animate-pulse")}>It's fine</a> {/* expect: src.infinite_pulse */}
    </div>
  )
}
""",
    "src/page.html": """<!doctype html>
<style>
.btn { transition: all .3s; } /* expect: src.transition_all */
.eyebrow { color: red } /* expect: src.eyebrow_class */
</style>
<!-- <div class="transition-all"> inside a comment is ignored -->
<p class="text-xs uppercase tracking-widest">Welcome</p> <!-- expect: src.eyebrow_class -->
<h1>Hello</h1>
<div style="box-shadow: 0 0 30px rgba(99,102,241,.5)">x</div> <!-- expect: src.glow_shadow -->
<div class="transition-colors">fine</div>
""",
    "src/page.vue": """<template>
  <button class="transition-all hover:bg-blue-500">Go</button> <!-- expect: src.transition_all -->
</template>
<style scoped>
.title { background-clip: text; color: transparent; } /* expect: src.gradient_text */
</style>
""",
    "src/picker.ts": """export const REACTIONS = ['\U0001F44D', '\U0001F389', '\U0001F525', '\U0001F600', '\U0001F680']
""",
    "src/enter/a.tsx": """export const A = () => <><div className="animate-fade-in-up" /> {/* expect: src.entrance_everywhere */}<div className="animate-fade-in-up" /></>
""",
    "src/enter/b.tsx": """export const B = () => <><div className="animate-fade-in-up" /><div className="animate-fade-in-up" /></>
""",
    "src/enter/c.tsx": """export const C = () => <div className="animate-fade-in-up" />
""",
    "src/enter/d.tsx": """export const D = () => <div className="animate-fade-in-up" />
""",
    "src/__tests__/skip.test.tsx": """export const t = <div className="transition-all shadow-[0_0_24px_rgba(139,92,246,0.5)]">\U0001f389 seamless</div>
""",
    "src/Button.stories.tsx": """export const S = <div className="animate-bounce animate-pulse">\U0001f389</div>
""",
    "node_modules/pkg/index.js": """module.exports = '<div class="transition-all">\U0001f389</div>'
""",
    "vendor/lib.css": """.x { transition: all 1s; }
""",
}


def parse_expectations(files):
    expected = set()
    for rel, text in files.items():
        lines = text.split("\n")
        for no, line in enumerate(lines, 1):
            for kind, ids in re.findall(r"expect(-next)?:\s*([\w.,\s]+?)(?=\s*(?:\*/|-->|$))", line):
                target = no + 1 if kind else no
                for rid in re.split(r"[,\s]+", ids.strip()):
                    if rid:
                        expected.add((rid, rel, target))
    return expected


def selftest_args(path, **kw):
    ns = dict(path=path, files=None, stdin=False, format="md", baseline=None, write_baseline=None, shrink=False,
              force=False, ignore=None, rule=None, fail_on="warn", include_tests=False, max=50)
    ns.update(kw)
    return argparse.Namespace(**ns)


def cmd_selftest(_args):
    failures = []

    def expect(cond, label):
        if not cond:
            failures.append(label)
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in FIXTURES.items():
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            with io.open(os.path.join(tmp, rel), "w", encoding="utf-8") as fh:
                fh.write(text)
        entries, _ = collect(tmp, None, [], False)
        rels = sorted(rel for rel, _ in entries)
        expect("src/__tests__/skip.test.tsx" not in rels and "src/Button.stories.tsx" not in rels,
               "test and story files are skipped")
        expect(not any(r.startswith(("node_modules/", "vendor/")) for r in rels), "vendored folders are skipped")
        findings, allowed, scanned, _ = scan_files(entries)
        got = {(f["rule"], f["file"], f["line"]) for f in findings}
        want = parse_expectations({rel: FIXTURES[rel] for rel in rels})
        for item in sorted(want - got):
            expect(False, "missing finding %s %s:%d" % item)
        for item in sorted(got - want):
            snippet = [f["snippet"] for f in findings if (f["rule"], f["file"], f["line"]) == item]
            expect(False, "unexpected finding %s %s:%d %s" % (item + (snippet[:1],)))
        covered = {r for r, _, _ in want}
        for rid in RULES:
            expect(rid in covered, "rule %s has a fixture" % rid)
        expect(any(a["rule"] == "src.transition_all" and "score" in a["reason"] for a in allowed),
               "an allow with a reason is counted with its reason")
        expect(any(a["file"] == "src/allow-file.tsx" and a["rule"] == "src.marketing_words" for a in allowed),
               "a file-level allow covers the whole file")
        expect(all(f["message"] and f["severity"] in ("WARN", "INFO") for f in findings), "every finding has a message")

        # baseline round trip, exit codes, file selection, formats
        quiet, old, old_err = open(os.devnull, "w"), sys.stdout, sys.stderr
        base = os.path.join(tmp, "baseline.json")
        try:
            sys.stdout = sys.stderr = quiet
            expect(cmd_scan(selftest_args(tmp)) == 1, "scan with WARN findings exits 1")
            expect(cmd_scan(selftest_args(tmp, fail_on="never")) == 0, "--fail-on never exits 0")
            expect(cmd_scan(selftest_args(tmp, write_baseline=base)) == 0, "write baseline exits 0")
            expect(cmd_scan(selftest_args(tmp, write_baseline=base)) == 2, "write baseline refuses to overwrite")
            expect(cmd_scan(selftest_args(tmp, baseline=base)) == 0, "scan within the baseline exits 0")
            with io.open(os.path.join(tmp, "src/new.tsx"), "w", encoding="utf-8") as fh:
                fh.write('export const N = () => <div className="animate-pulse size-2 rounded-full bg-red-500" />\n')
            expect(cmd_scan(selftest_args(tmp, baseline=base)) == 1, "a new finding beyond the baseline exits 1")
            expect(cmd_scan(selftest_args(tmp, baseline=base, files=["src/glow.tsx"])) == 0,
                   "--files limits the scan to the named files")
            expect(cmd_scan(selftest_args(tmp, files=["src/new.tsx"])) == 1, "--files finds a finding in its file")
            with io.open(os.path.join(tmp, "src/new.tsx"), "w", encoding="utf-8") as fh:
                fh.write("export const N = () => <div />\n")
            expect(cmd_scan(selftest_args(tmp, baseline=base, shrink=True)) == 0, "shrink after a fix exits 0")
            expect(cmd_scan(selftest_args(tmp, baseline=os.path.join(tmp, "missing.json"))) == 2,
                   "a missing baseline exits 2")
            expect(cmd_scan(selftest_args(os.path.join(tmp, "nowhere"))) == 2, "a missing path exits 2")
            expect(cmd_scan(selftest_args(tmp, baseline=base, shrink=True, files=["src/glow.tsx"])) == 2,
                   "--shrink refuses a partial scan")
            sys.stdin = io.StringIO("src/easing.ts\nsrc/glow.tsx\n")
            expect(cmd_scan(selftest_args(tmp, stdin=True, rule=["src.bounce_easing"])) == 1, "--stdin reads a file list")
            sys.stdin = sys.__stdin__
            sys.stdout = buf = io.StringIO()
            code = cmd_scan(selftest_args(tmp, format="json", rule=["src.glow_shadow"]))
            data = json.loads(buf.getvalue())
            expect(code == 1 and data["summary"] == {"src.glow_shadow": sum(1 for r, _, _ in want if r == "src.glow_shadow")}, "json output counts the glow findings")
            expect(all(set(f) >= {"rule", "file", "line", "snippet", "message", "severity"} for f in data["findings"]),
                   "json findings carry rule, file, line, snippet, message, and severity")
            sys.stdout = buf = io.StringIO()
            cmd_scan(selftest_args(tmp, rule=["src.eyebrow_class"]))
            text = buf.getvalue()
            expect("## src.eyebrow_class" in text and "`src/eyebrow.tsx:4`" in text and "Fix:" in text,
                   "markdown output groups findings by rule with the fix")
        finally:
            sys.stdout, sys.stderr = old, old_err
            sys.stdin = sys.__stdin__
            quiet.close()
    expect(glow_layer("0 0 24px rgba(139,92,246,0.5)") and not glow_layer("0 4px 12px rgba(0,0,0,0.3)"),
           "glow_layer separates a colored glow from a neutral shadow")
    expect(bezier_overshoots("0.68,-0.55,0.265,1.55") and not bezier_overshoots("0.22, 1, 0.36, 1"),
           "bezier_overshoots separates a bounce from an ease-out")
    for label in failures:
        print("FAIL " + label)
    print("selftest %s (%d failure%s, %d rules, %d fixtures)" % ("FAILED" if failures else "OK", len(failures),
                                                                  "" if len(failures) == 1 else "s", len(RULES),
                                                                  len(FIXTURES)))
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="scan source for slop patterns")
    s.add_argument("path")
    s.add_argument("--files", nargs="*", help="scan only these files")
    s.add_argument("--stdin", action="store_true", help="read the file list from stdin, one path per line")
    s.add_argument("--format", choices=["md", "json"], default="md")
    s.add_argument("--baseline", help="JSON baseline; only findings beyond it are reported")
    s.add_argument("--write-baseline", help="write today's findings to this file and exit")
    s.add_argument("--shrink", action="store_true", help="lower the baseline to today's counts (never raises it)")
    s.add_argument("--force", action="store_true", help="let --write-baseline overwrite a file")
    s.add_argument("--ignore", action="append", help="path fragment to skip (repeatable)")
    s.add_argument("--rule", action="append", help="report only this rule id (repeatable)")
    s.add_argument("--fail-on", choices=["warn", "info", "never"], default="warn")
    s.add_argument("--include-tests", action="store_true", help="also scan test, story, and fixture files")
    s.add_argument("--max", type=int, default=25, help="findings printed per rule in markdown")
    r = sub.add_parser("rules", help="print the rule catalog")
    r.add_argument("--format", choices=["md", "json"], default="md")
    sub.add_parser("selftest", help="check every rule against built-in fixtures")
    args = ap.parse_args(argv)
    try:
        return {"scan": cmd_scan, "rules": cmd_rules, "selftest": cmd_selftest}[args.cmd](args)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
