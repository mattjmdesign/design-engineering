#!/usr/bin/env python3
"""ui_inventory.py: read a React app's component layer before building, then check new code against it.

Commands:
  inventory [PATH] [--brief] [--json]     stack from imports, every ui/ component with variants and the import line to use
  audit [PATH] [--files F... | --stdin]   composition findings, each naming the component and variant to use instead
  summary [PATH] [--base LABEL]           Reused, Extended, Composed, Created table; changed files come from stdin
  selftest                                run the scanner and every rule against a built-in fixture app

Rules (P1 fails by default; --fail-on P2|P3|none changes that):
  P1 system.raw_element       a raw <button>, <input>, <select>, <textarea>, <table>, or <hr> where ui/ has the component
  P1 system.primitive_bypass  a Base UI, Radix, or React Aria import outside ui/ (the wrapper in ui/ owns it)
  P1 system.name_shadow       a local Button, Card, Modal, or similar that renders the raw element instead of the ui/ one
  P2 system.variant_restated  color, shape, padding, or type classes on a ui/ component that a variant already covers
  P2 system.skin_copy         a container that repeats the skin of a ui/ component (Card, Alert, Badge, and so on)
  P2 system.repeated_classes  one class set of six or more tokens, three or more times: a missing variant or component
  P3 system.near_duplicate    three or more components built from nearly the same ui/ parts: a missing primitive
  P3 system.suppression       an eslint-disable on a UI rule with no reason after "--"

A real gap is allowed with --allow RULE=PATH:reason (a reason is required), or in markup with
data-ui-allow="system.raw_element: reason". Allowed findings are listed and never fail the run.

Changed files for audit and summary come from `git diff --name-only main | python3 ui_inventory.py audit --stdin`
(`--name-status` output works too). PATH is optional on every command and defaults to the current directory.

Trust boundary: reads source files under PATH and writes only the baseline file named with --write-baseline
or --shrink. No network access and no subprocesses; git output reaches the script only through stdin.
"""
import argparse
import bisect
import fnmatch
import hashlib
import io
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict

SKIP_DIRS = {"node_modules", "dist", "build", "out", "coverage", "vendor", "venv", "target", "storybook-static",
             "__pycache__", ".next", ".nuxt", ".svelte-kit", ".turbo", ".git", ".cache", ".vercel", ".output"}
SRC_EXT = {".tsx", ".jsx", ".ts", ".js", ".mjs", ".cjs"}
JSX_EXT = {".tsx", ".jsx", ".js"}
MAX_BYTES = 800000
TEST_PATH = re.compile(r"(\.(test|spec|stories|story)\.[jt]sx?$)|(/__(tests|mocks|snapshots|fixtures)__/)"
                       r"|(/(test|tests|e2e|cypress|playwright)/)|(\.d\.ts$)")
UI_DIR_CANDIDATES = ["src/components/ui", "components/ui", "app/components/ui", "src/app/components/ui", "src/ui", "ui"]

RULES = {
    "system.raw_element": "P1", "system.primitive_bypass": "P1", "system.name_shadow": "P1",
    "system.variant_restated": "P2", "system.skin_copy": "P2", "system.repeated_classes": "P2",
    "system.allow_reason": "P2",
    "system.near_duplicate": "P3", "system.suppression": "P3",
}
SEV_RANK = {"P1": 1, "P2": 2, "P3": 3}
STYLE_GROUPS = ("variant", "variants", "tone", "intent", "color", "kind", "appearance")
SIZE_GROUPS = ("size", "density", "scale")

SHADCN_NAMES = set("""accordion alert alert-dialog aspect-ratio avatar badge breadcrumb button button-group calendar card
carousel chart checkbox collapsible combobox command context-menu dialog direction drawer dropdown-menu empty field form
hover-card input input-group input-otp item kbd label menubar native-select navigation-menu pagination popover progress
radio-group resizable scroll-area select separator sheet sidebar skeleton slider sonner spinner switch table tabs textarea
toast toggle toggle-group tooltip typography""".split())

# Libraries whose parts a ui/ file wraps. A page imports the wrapper, never the part.
PRIMITIVE_PKG = [
    ("Base UI", "base-ui", re.compile(r"^@base-ui(-components)?/react(/|$)")),
    ("Radix", "radix", re.compile(r"^(@radix-ui/react-(?!icons)[\w-]+|radix-ui)$")),
    ("React Aria", "react-aria", re.compile(r"^(react-aria(-components)?|@react-aria/[\w-]+)(/|$)")),
    ("Headless UI", "headless-ui", re.compile(r"^@headlessui/")),
    ("Ariakit", "ariakit", re.compile(r"^@ariakit/")),
    ("Ark UI", "ark-ui", re.compile(r"^@ark-ui/")),
    ("cmdk", "cmdk", re.compile(r"^cmdk$")),
    ("Vaul", "vaul", re.compile(r"^vaul$")),
    ("input-otp", "input-otp", re.compile(r"^input-otp$")),
    ("react-day-picker", "day-picker", re.compile(r"^react-day-picker$")),
    ("react-resizable-panels", "resizable", re.compile(r"^react-resizable-panels$")),
]
CENSUS_BASES = ("base-ui", "radix", "react-aria", "headless-ui", "ariakit")
# Utility parts a page may import: they style nothing and no wrapper replaces them.
PRIMITIVE_EXEMPT = re.compile(r"(use-render|merge-props|/utils$|csp-provider|direction|localization|react-slot$|/slot$"
                              r"|visually-hidden|compose-refs|react-id$|react-use-|react-portal$|react-primitive$)")
PRIMITIVE_EXEMPT_NAMES = {"Slot", "Slottable", "VisuallyHidden", "Portal", "Primitive", "useRender", "mergeProps"}
ICON_PKG = [
    ("Lucide", "lucide", re.compile(r"^lucide-react")),
    ("HugeIcons", "hugeicons", re.compile(r"^@hugeicons/")),
    ("Tabler", "tabler", re.compile(r"^@tabler/icons")),
    ("Phosphor", "phosphor", re.compile(r"^@phosphor-icons/")),
    ("Radix Icons", "radix-icons", re.compile(r"^@radix-ui/react-icons$")),
    ("Heroicons", "heroicons", re.compile(r"^@heroicons/")),
    ("Remix Icon", "remixicon", re.compile(r"^(@remixicon/|remixicon)")),
    ("react-icons", "react-icons", re.compile(r"^react-icons")),
]
RAW_CONTROLS = {"button", "input", "select", "textarea", "table", "hr"}
CONTAINER_TAGS = {"div", "section", "article", "aside", "li", "span", "p", "header", "footer", "nav", "ul", "ol", "a", "label",
                  "form", "fieldset", "dl", "figure", "main"}
# Overlay and control surfaces can not be replaced by a plain container, so their skins never count as a copy.
SKIN_DENY = re.compile(r"(popover|tooltip|dropdown|menu|select|dialog|sheet|drawer|hover|command|overlay|button|toggle|tab|input|"
                       r"textarea|switch|checkbox|sidebar|table|toast|sonner|combobox|calendar|carousel|scroll|resiz|slider|progress|"
                       r"separator|skeleton|spinner|avatar|breadcrumb|pagination)", re.I)
SURFACE_TOKENS = ("bg", "bc", "border", "rounded", "shadow")


# --------------------------------------------------------------------------- source scanner

TOKEN = re.compile(r"//|/\*|[\"'`/{}]")
STR_LINE = {"'": re.compile(r"(?:[^'\\\n]|\\[\s\S])*'"), '"': re.compile(r'(?:[^"\\\n]|\\[\s\S])*"')}
STR_WIDE = {"'": re.compile(r"(?:[^'\\]|\\[\s\S]){0,3000}'"), '"': re.compile(r'(?:[^"\\]|\\[\s\S]){0,3000}"')}
TPL_BODY = re.compile(r"[`\\]|\$\{")
REGEX_BODY = re.compile(r"(?:[^/\\\[\n]|\\.|\[(?:[^\]\\\n]|\\.)*\])+/")
REGEX_AFTER = re.compile(r"[dgimsuvy]*(?=[\s,;)\].}:?]|$)")
REGEX_WORDS = {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "else", "do", "yield", "await"}


def _regex_context(text, i):
    """True when a slash at i starts a regular expression and not a division or a closing JSX tag."""
    j = i - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    if j < 0:
        return True
    c = text[j]
    if c in "(,=:[!&|?{};~^%*+-":
        return True
    if c == ">":
        return j > 0 and text[j - 1] == "="
    if c.isalnum() or c in "_$":
        k = j
        while k >= 0 and (text[k].isalnum() or text[k] in "_$"):
            k -= 1
        return text[k + 1:j + 1] in REGEX_WORDS
    return False


def _scan(text):
    """Find comments, string contents, and regex literals. Returns three sorted lists of (start, end) spans."""
    n = len(text)
    comments, strings, regexes, tstack = [], [], [], []
    pos = 0

    def read_template(p):
        start = p
        while True:
            m = TPL_BODY.search(text, p)
            if not m:
                strings.append((start, n))
                return n
            tok = m.group()
            if tok == "\\":
                p = m.end() + 1
                continue
            strings.append((start, m.start()))
            if tok == "`":
                return m.end()
            tstack.append(0)
            return m.end()

    while pos < n:
        m = TOKEN.search(text, pos)
        if not m:
            break
        i, tok = m.start(), m.group()
        if tok == "//":
            if i > 0 and text[i - 1] == ":":
                pos = i + 2
                continue
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments.append((i, j))
            pos = j
        elif tok == "/*":
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            comments.append((i, j))
            pos = j
        elif tok == "/":
            pos = i + 1
            if _regex_context(text, i):
                rm = REGEX_BODY.match(text, i + 1)
                if rm and REGEX_AFTER.match(text, rm.end()):
                    regexes.append((i + 1, rm.end() - 1))
                    pos = rm.end()
        elif tok == "`":
            pos = read_template(i + 1)
        elif tok == "{":
            if tstack:
                tstack[-1] += 1
            pos = i + 1
        elif tok == "}":
            pos = i + 1
            if tstack:
                if tstack[-1] == 0:
                    tstack.pop()
                    pos = read_template(i + 1)
                else:
                    tstack[-1] -= 1
        else:
            # An apostrophe or quote glued to a word is JSX text ("don't"), not a string.
            if i > 0 and (text[i - 1].isalnum() or text[i - 1] in "_$"):
                pos = i + 1
                continue
            k = i - 1
            while k >= 0 and text[k] in " \t":
                k -= 1
            wide = k >= 0 and text[k] == "="  # a JSX attribute string may span lines
            sm = (STR_WIDE if wide else STR_LINE)[tok].match(text, i + 1)
            if not sm:
                pos = i + 1
                continue
            strings.append((i + 1, sm.end() - 1))
            pos = sm.end()
    return comments, sorted(strings), regexes


def _mask(text, spans, fill):
    out, last = [], 0
    for a, b in sorted(spans):
        if a < last:
            continue
        out.append(text[last:a])
        out.append(re.sub(r"[^\n]", fill, text[a:b]))
        last = b
    out.append(text[last:])
    return "".join(out)


class Source(object):
    """A source file in three views of the same length: text, code (comments blanked), struct (comments and string contents blanked)."""

    def __init__(self, text):
        self.text = text
        self.comments, self.strings, regexes = _scan(text)
        self.code = _mask(text, self.comments, " ")
        self.struct = _mask(self.code, self.strings + regexes, "\x01")
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", text)]
        self.string_starts = [a for a, _ in self.strings]

    def line(self, pos):
        return bisect.bisect_right(self.line_starts, pos)

    def strings_in(self, a, b):
        i = bisect.bisect_left(self.string_starts, a)
        while i < len(self.strings) and self.strings[i][0] < b:
            if self.strings[i][1] <= b:
                yield self.strings[i]
            i += 1


BRACKETS = re.compile(r"[()\[\]{}]")
SPLIT_TOK = re.compile(r"[,()\[\]{}]")
IDENT = re.compile(r"[\w$]+")
COLON = re.compile(r"\s*:")


def match_bracket(s, i):
    depth = 0
    for m in BRACKETS.finditer(s, i):
        if m.group() in "([{":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.start()
    return -1


def split_top(s, a, b):
    """Split s[a:b] at commas that sit outside any bracket. Returns (start, end) pairs, blanks dropped."""
    out, depth, last = [], 0, a
    for m in SPLIT_TOK.finditer(s, a, b):
        c = m.group()
        if c == ",":
            if depth == 0:
                out.append((last, m.start()))
                last = m.end()
        elif c in "([{":
            depth += 1
        else:
            depth -= 1
    out.append((last, b))
    return [(x, y) for x, y in out if s[x:y].strip()]


def parse_object(src, i):
    """Entries of the object literal opening at struct[i]: [(key, value_start, value_end)] and the closing index."""
    s = src.struct
    close = match_bracket(s, i)
    if close < 0:
        return [], i
    entries = []
    for a, b in split_top(s, i + 1, close):
        p = a
        while p < b and s[p].isspace():
            p += 1
        if s[p:p + 1] in ("\"", "'"):
            q = s.find(s[p], p + 1)
            if q < 0:
                continue
            key, p = src.text[p + 1:q], q + 1
        else:
            m = IDENT.match(s, p)
            if not m:
                continue
            key, p = m.group(), m.end()
        m2 = COLON.match(s, p)
        if m2 and m2.end() <= b:
            entries.append((key, m2.end(), b))
    return entries, close


def first_brace(s, a, b, ch="{"):
    """Index of ch when it is the first non-blank character in s[a:b], else -1."""
    seg = s[a:b]
    stripped = seg.lstrip()
    if stripped.startswith(ch):
        return a + (len(seg) - len(stripped))
    return -1


# --------------------------------------------------------------------------- JSX tags

TAG_START = re.compile(r"<([A-Za-z][\w.:-]*)")
ATTR_NAME = re.compile(r"[\w:.@$-]+")
WS = re.compile(r"\s*")


class Tag(object):
    __slots__ = ("name", "start", "end", "self_closing", "attrs")

    def __init__(self, name, start, end, self_closing, attrs):
        self.name, self.start, self.end, self.self_closing, self.attrs = name, start, end, self_closing, attrs


def parse_tags(src):
    """JSX opening tags in a file and the character spans of every render={...} expression."""
    s = src.struct
    n = len(s)
    tags, render_spans = [], []
    for m in TAG_START.finditer(s):
        i = m.start()
        prev = s[i - 1] if i else "\n"
        if prev.isalnum() or prev in "_$)]<":
            continue
        j = m.end()
        attrs, ok, end, selfc = {}, False, j, False
        while j < n:
            j = WS.match(s, j).end()
            if j >= n:
                break
            c = s[j]
            if c == ">":
                ok, end = True, j + 1
                break
            if c == "/" and s[j + 1:j + 2] == ">":
                ok, end, selfc = True, j + 2, True
                break
            if c == "{":
                k = match_bracket(s, j)
                if k < 0:
                    break
                j = k + 1
                continue
            am = ATTR_NAME.match(s, j)
            if not am:
                break
            name, j = am.group(), am.end()
            j2 = WS.match(s, j).end()
            if s[j2:j2 + 1] != "=":
                attrs[name] = ("bool", j, j)
                continue
            j = WS.match(s, j2 + 1).end()
            c = s[j:j + 1]
            if c in ("\"", "'"):
                k = s.find(c, j + 1)
                if k < 0:
                    break
                attrs[name] = ("str", j + 1, k)
                j = k + 1
            elif c == "{":
                k = match_bracket(s, j)
                if k < 0:
                    break
                attrs[name] = ("expr", j + 1, k)
                if name == "render":
                    render_spans.append((j + 1, k))
                j = k + 1
            else:
                break
        if ok:
            tags.append(Tag(m.group(1), i, end, selfc, attrs))
    return tags, render_spans


def attr_literal(src, tag, name):
    a = tag.attrs.get(name)
    if a and a[0] == "str":
        return src.text[a[1]:a[2]]
    return None


def attr_text(src, tag, name):
    a = tag.attrs.get(name)
    if not a:
        return None
    return src.text[a[1]:a[2]].strip() if a[0] != "bool" else "true"


def _comparison_operand(src, a, b):
    s = src.struct
    before = s[max(0, a - 8):a - 1].rstrip()
    after = s[b + 1:b + 8].lstrip()
    return before.endswith(("===", "!==", "==", "!=")) or after.startswith(("===", "!==", "==", "!="))


def _unconditional(src, a, b, vs, ve):
    """True when a string inside a className expression is a plain argument, not a branch of && or ?:."""
    s = src.struct
    opener = s[a - 1] if a > 0 else ""
    if opener in ("`", "}"):
        return True
    i = a - 2
    while i >= vs and s[i].isspace():
        i -= 1
    prev = s[i] if i >= vs else ""
    j = b + 1
    while j < ve and s[j].isspace():
        j += 1
    nxt = s[j] if j < ve else ""
    return prev in ("", "(", ",") and nxt in ("", ",", ")")


def tag_class_strings(src, tag, base_only=False):
    """Static class strings on a tag: the className literal, or every string inside a className expression.
    With base_only, strings that sit in a condition (cond && "x", a ? "x" : "y") are left out."""
    out = []
    for an in ("className", "class"):
        a = tag.attrs.get(an)
        if not a:
            continue
        kind, vs, ve = a
        if kind == "str":
            out.append(src.text[vs:ve])
        elif kind == "expr":
            for s0, e0 in src.strings_in(vs, ve):
                if _comparison_operand(src, s0, e0):
                    continue
                if base_only and not _unconditional(src, s0, e0, vs, ve):
                    continue
                out.append(src.text[s0:e0])
    return out


def tag_tokens(src, tag, base_only=False):
    out = []
    for text in tag_class_strings(src, tag, base_only):
        out.extend(text.split())
    return out


# --------------------------------------------------------------------------- imports, exports, chunks

IMPORT_RE = re.compile(r"\bimport\s+(type\s+)?([\w$\s,{}*]*?)\s*from\s*([\"'])(\x01*)\3")
SIDE_IMPORT_RE = re.compile(r"\bimport\s*([\"'])(\x01*)\1")
EXPORT_FROM_RE = re.compile(r"\bexport\s+(type\s+)?(\*(?:\s+as\s+[\w$]+)?|\{[^}]*\})\s*from\s*([\"'])(\x01*)\3")
EXP_DECL = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)
EXP_DEFAULT = re.compile(r"^export\s+default\s+(?:async\s+)?(?:function\*?|class)\s+([A-Za-z_$][\w$]*)", re.M)
EXP_LIST = re.compile(r"^export\s*(type\s*)?\{([^}]*)\}(?!\s*from\b)", re.M)
CHUNK_RE = re.compile(r"^(?:export\s+(?:default\s+)?)?(?:declare\s+)?(?:async\s+)?(?:function\b|const\b|let\b|var\b|class\b|type\b|"
                      r"interface\b|enum\b|abstract\b)|^export\b|^import\b", re.M)
CHUNK_NAME = re.compile(r"(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)")


class Imp(object):
    __slots__ = ("spec", "names", "default", "namespace", "type_only", "pos", "reexport", "target")

    def __init__(self, spec, names, default, namespace, type_only, pos, reexport=False):
        self.spec, self.names, self.default, self.namespace = spec, names, default, namespace
        self.type_only, self.pos, self.reexport, self.target = type_only, pos, reexport, None


def _parse_clause(clause):
    clause = clause.strip()
    names, default, namespace = [], None, None
    m = re.match(r"([A-Za-z_$][\w$]*)\s*(,|$)", clause)
    if m and not clause.startswith(("{", "*")):
        default = m.group(1)
        clause = clause[m.end():].strip()
    if clause.startswith("*"):
        m = re.match(r"\*\s*as\s+([\w$]+)", clause)
        if m:
            namespace = m.group(1)
    elif clause.startswith("{"):
        inner = clause[1:clause.rfind("}")] if "}" in clause else clause[1:]
        for part in inner.split(","):
            part = part.strip()
            if not part:
                continue
            typed = part.startswith("type ")
            if typed:
                part = part[5:].strip()
            if re.search(r"\s+as\s+", part):
                imp, loc = re.split(r"\s+as\s+", part, maxsplit=1)
            else:
                imp = loc = part
            names.append((imp.strip(), loc.strip(), typed))
    return names, default, namespace


def parse_imports(src):
    s, out = src.struct, []
    for m in IMPORT_RE.finditer(s):
        spec = src.text[m.start(4):m.end(4)]
        names, default, namespace = _parse_clause(m.group(2))
        type_only = bool(m.group(1)) or (bool(names) and all(t for _, _, t in names) and not default and not namespace)
        out.append(Imp(spec, names, default, namespace, type_only, m.start()))
    for m in SIDE_IMPORT_RE.finditer(s):
        out.append(Imp(src.text[m.start(2):m.end(2)], [], None, None, False, m.start()))
    for m in EXPORT_FROM_RE.finditer(s):
        spec = src.text[m.start(4):m.end(4)]
        names, _, _ = _parse_clause(m.group(2)) if m.group(2).startswith("{") else ([], None, None)
        out.append(Imp(spec, names, None, None, bool(m.group(1)), m.start(), True))
    return sorted(out, key=lambda x: x.pos)


def parse_exports(src):
    """Exported names as (exported, local) pairs, in file order."""
    s, out = src.struct, []
    for m in EXP_DECL.finditer(s):
        out.append((m.start(), m.group(1), m.group(1)))
    for m in EXP_DEFAULT.finditer(s):
        out.append((m.start(), "default", m.group(1)))
    for m in EXP_LIST.finditer(s):
        if m.group(1):
            continue
        for part in m.group(2).split(","):
            part = part.strip()
            if not part or part.startswith("type "):
                continue
            if re.search(r"\s+as\s+", part):
                loc, exp = re.split(r"\s+as\s+", part, maxsplit=1)
            else:
                loc = exp = part
            out.append((m.start(), exp.strip(), loc.strip()))
    for m in EXPORT_FROM_RE.finditer(s):
        if m.group(2).startswith("{") and not m.group(1):
            for part in m.group(2)[1:-1].split(","):
                part = part.strip()
                if part and not part.startswith("type "):
                    exp = re.split(r"\s+as\s+", part)[-1]
                    out.append((m.start(), exp.strip(), exp.strip()))
    return [(e, l) for _, e, l in sorted(out)]


class Chunk(object):
    __slots__ = ("start", "end", "name", "exported", "component")

    def __init__(self, start, end, name, exported):
        self.start, self.end, self.name, self.exported = start, end, name, exported
        self.component = bool(name and name[0].isupper() and not name.isupper())


def parse_chunks(src):
    s = src.struct
    starts = [m.start() for m in CHUNK_RE.finditer(s)]
    out = []
    for k, a in enumerate(starts):
        b = starts[k + 1] if k + 1 < len(starts) else len(s)
        m = CHUNK_NAME.match(s, a)
        out.append(Chunk(a, b, m.group(1) if m else None, s.startswith("export", a)))
    return out


# --------------------------------------------------------------------------- variants (cva and tv)

CVA_CALL = re.compile(r"\b(cva|tv)\s*\(")


def parse_cvas(src):
    """Every cva() or tv() call: name, base tokens, variant groups with option tokens, defaults, compound count."""
    s, out = src.struct, []
    for m in CVA_CALL.finditer(s):
        op = m.end() - 1
        close = match_bracket(s, op)
        if close < 0:
            continue
        pre = s[max(0, m.start() - 160):m.start()]
        nm = re.search(r"(?:const|let|var)\s+([\w$]+)\s*(?::[^=;]+)?=\s*$", pre)
        args = split_top(s, op + 1, close)
        base, cfg = [], -1
        if m.group(1) == "cva":
            if args:
                for a, b in src.strings_in(args[0][0], args[0][1]):
                    base.extend(src.text[a:b].split())
            for a, b in args[1:]:
                cfg = first_brace(s, a, b)
                if cfg >= 0:
                    break
        elif args:
            cfg = first_brace(s, args[0][0], args[0][1])
        info = {"name": nm.group(1) if nm else None, "kind": m.group(1), "line": src.line(m.start()), "base": base,
                "variants": {}, "tokens": {}, "defaults": {}, "compound": 0}
        if cfg >= 0:
            entries, _ = parse_object(src, cfg)
            d = dict((k, (vs, ve)) for k, vs, ve in entries)
            if "base" in d and m.group(1) == "tv":
                for a, b in src.strings_in(d["base"][0], d["base"][1]):
                    base.extend(src.text[a:b].split())
            if "variants" in d:
                vi = first_brace(s, d["variants"][0], d["variants"][1])
                if vi >= 0:
                    for gk, gvs, gve in parse_object(src, vi)[0]:
                        oi = first_brace(s, gvs, gve)
                        if oi < 0:
                            continue
                        opts, toks = [], {}
                        for ok, ovs, ove in parse_object(src, oi)[0]:
                            opts.append(ok)
                            toks[ok] = [t for a, b in src.strings_in(ovs, ove) for t in src.text[a:b].split()]
                        info["variants"][gk], info["tokens"][gk] = opts, toks
            if "defaultVariants" in d:
                di = first_brace(s, d["defaultVariants"][0], d["defaultVariants"][1])
                if di >= 0:
                    for k, vs, ve in parse_object(src, di)[0]:
                        info["defaults"][k] = src.text[vs:ve].strip().strip("\"'")
            if "compoundVariants" in d:
                ai = first_brace(s, d["compoundVariants"][0], d["compoundVariants"][1], "[")
                if ai >= 0:
                    aj = match_bracket(s, ai)
                    info["compound"] = sum(1 for a, b in split_top(s, ai + 1, aj) if s[a:b].lstrip().startswith("{"))
        out.append(info)
    return out


UNION_PROP = re.compile(r"([\w$]+)\??\s*:\s*((?:[\"'][\w-]+[\"']\s*\|\s*)+[\"'][\w-]+[\"'])")


def parse_props(src, chunk):
    """Destructured props of a component function as 'name' or 'name=default', plus union-typed props: {name: [options]}."""
    s = src.struct
    head_end = min(chunk.end, chunk.start + 600)
    m = re.compile(r"\(\s*\{").search(s, chunk.start, head_end)
    if not m:
        return [], {}
    brace = m.end() - 1
    close = match_bracket(s, brace)
    if close < 0:
        return [], {}
    names = []
    for a, b in split_top(s, brace + 1, close):
        seg = s[a:b].strip()
        if seg.startswith("..."):
            continue
        im = IDENT.match(seg)
        if not im:
            continue
        name = im.group()
        eq = seg.find("=")
        if eq > 0 and ":" not in seg[:eq]:
            default = src.text[a + (len(s[a:b]) - len(s[a:b].lstrip())) + eq + 1:b].strip().strip("\"'")
            names.append("%s=%s" % (name, default[:24]))
        else:
            names.append(name)
    param_close = match_bracket(s, m.start())
    unions = {}
    if param_close > close:
        for um in UNION_PROP.finditer(src.text, close, param_close):
            unions[um.group(1)] = re.findall(r"[\"']([\w-]+)[\"']", um.group(2))
    return names, unions


# --------------------------------------------------------------------------- class tokens

TEXT_SIZE = re.compile(r"^text-(xs|sm|base|lg|xl|[2-9]xl|\[\d[^\]]*\]|\(length:[^)]*\)|\[length:[^\]]*\])$")
TEXT_NONCOLOR = re.compile(r"^text-(left|center|right|justify|start|end|wrap|nowrap|balance|pretty|ellipsis|clip)$")
BG_NONCOLOR = re.compile(r"^bg-(clip|origin|none|no-repeat|repeat|cover|contain|auto|fixed|local|scroll|blend|gradient|linear|radial|"
                         r"conic|top|bottom|left|right|center|size|position|\[(url|length|position|image)|\((image|length|position))")
BORDER_NONCOLOR = re.compile(r"^border-([xytrbls]|e|[0-9]+|solid|dashed|dotted|double|hidden|none|collapse|separate|spacing)(-.*)?$")
OTHER_COLOR = re.compile(r"^(ring|outline|fill|stroke|accent|caret|decoration|placeholder|divide)-(?!\d|offset|inset|none|solid|dashed|"
                         r"dotted|double|auto|collapse|hidden)")


def base_token(tok):
    """Split a Tailwind class into (utility, prefix): 'hover:bg-red-500' gives ('bg-red-500', 'hover:')."""
    depth, last = 0, 0
    for k, ch in enumerate(tok):
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif ch == ":" and depth == 0:
            last = k + 1
    b = tok[last:]
    return b.strip("!"), tok[:last]


def category(base):
    """color, shape, padding, type, or other. Layout classes (margin, width, flex, position) are other, on purpose."""
    if base.startswith("rounded"):
        return "shape"
    if re.match(r"^-?p[xytrblse]?-", base):
        return "padding"
    if TEXT_SIZE.match(base) or re.match(r"^(font|leading|tracking)-", base) or base in ("uppercase", "lowercase", "capitalize", "italic"):
        return "type"
    if base.startswith("bg-") and not BG_NONCOLOR.match(base):
        return "color"
    if base.startswith("text-") and not TEXT_NONCOLOR.match(base):
        return "color"
    if base.startswith("border-") and not BORDER_NONCOLOR.match(base):
        return "color"
    if OTHER_COLOR.match(base):
        return "color"
    return "other"


def skin_features(tokens):
    """The surface of a class set: its fill, border, radius, and shadow, ignoring state variants."""
    feats = set()
    for t in tokens:
        b, pre = base_token(t)
        if pre:
            continue
        if b.startswith("bg-") and category(b) == "color":
            feats.add(("bg", b))
        elif b == "border":
            feats.add(("border", ""))
        elif b.startswith("border-") and category(b) == "color" and b != "border-transparent":
            feats.add(("bc", b))
        elif b.startswith("rounded"):
            feats.add(("rounded", ""))
        elif b.startswith("shadow"):
            feats.add(("shadow", ""))
    return feats


def has_surface(tokens):
    return any(f[0] in SURFACE_TOKENS for f in skin_features(tokens))


def kebab(name):
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).lower()


def words(name):
    return re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+", name)


# --------------------------------------------------------------------------- repository index

def read_text(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def load_json(path):
    """Read JSON or JSONC (comments, trailing commas). Returns {} when missing or unreadable."""
    text = read_text(path)
    if text is None:
        return {}
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        clean = re.sub(r",(\s*[}\]])", r"\1", Source(text).code)
        return json.loads(clean)
    except ValueError:
        return {}


def walk_dirs(root, depth):
    """Yield (dir, files) for root and its subfolders down to depth, skipping vendor and hidden folders."""
    stack = [(os.path.abspath(root), 0)]
    while stack:
        d, level = stack.pop(0)
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        yield d, [e for e in entries if os.path.isfile(os.path.join(d, e))]
        if level < depth:
            for e in entries:
                if e not in SKIP_DIRS and not e.startswith(".") and os.path.isdir(os.path.join(d, e)):
                    stack.append((os.path.join(d, e), level + 1))


def find_app_root(path, notes):
    """The folder that holds components.json, found below PATH first and then above it. Falls back to package.json, then PATH."""
    path = os.path.abspath(path)
    if os.path.isfile(path):
        path = os.path.dirname(path)
    found = [d for d, files in walk_dirs(path, 3) if "components.json" in files]
    if not found:
        up = path
        for _ in range(4):
            if os.path.isfile(os.path.join(up, "components.json")):
                found = [up]
                break
            parent = os.path.dirname(up)
            if parent == up:
                break
            up = parent
    if found:
        if len(found) > 1:
            notes.append("more than one components.json below the given folder; using %s (pass another folder to check it): %s"
                         % (os.path.relpath(found[0], path), ", ".join(os.path.relpath(f, path) for f in found[1:4])))
        return found[0], os.path.join(found[0], "components.json")
    pk = [d for d, files in walk_dirs(path, 2) if "package.json" in files]
    if pk:
        notes.append("no components.json found; using %s" % pk[0])
        return pk[0], None
    notes.append("no components.json or package.json found; using %s" % path)
    return path, None


class FileInfo(object):
    def __init__(self, rel, abs_path, text):
        self.rel, self.abs, self.text = rel, abs_path, text
        self.ext = os.path.splitext(rel)[1].lower()
        self.src = Source(text)
        self.imports = parse_imports(self.src)
        self.in_ui = False
        self.local_ui = {}       # local name -> (ui file rel, exported name)
        self.ui_modules = set()  # ui file rels this file imports
        self._tags = self._render = self._chunks = self._exports = None

    def _parse_jsx(self):
        if self._tags is None:
            self._tags, self._render = parse_tags(self.src) if self.ext in JSX_EXT else ([], [])

    @property
    def tags(self):
        self._parse_jsx()
        return self._tags

    @property
    def render_spans(self):
        self._parse_jsx()
        return self._render

    @property
    def chunks(self):
        if self._chunks is None:
            self._chunks = parse_chunks(self.src)
        return self._chunks

    @property
    def exports(self):
        if self._exports is None:
            self._exports = parse_exports(self.src)
        return self._exports

    def in_render(self, pos):
        return any(a <= pos <= b for a, b in self.render_spans)

    def chunk_at(self, pos):
        k = bisect.bisect_right([c.start for c in self.chunks], pos) - 1
        return self.chunks[k] if k >= 0 else None


class UiComp(object):
    """One exported component of a ui/ file and the variant groups its own function uses."""

    def __init__(self, name, ufile):
        self.name, self.ufile = name, ufile
        self.groups = {}    # group -> [options]
        self.tokens = {}    # group -> {option: [tokens]}
        self.defaults = {}
        self.unions = {}
        self.props = []
        self.skins = []     # [(variant option or None, tokens)]
        self.parts = []
        self.root = False


class UiFile(object):
    def __init__(self, fi):
        self.fi, self.rel = fi, fi.rel
        self.module = os.path.splitext(os.path.basename(fi.rel))[0]
        self.exports, self.components, self.helpers = [], [], []
        self.comps = {}       # exported component name -> UiComp
        self.compounds = {}   # root -> [part suffixes]
        self.wraps = []
        self.cvas = []
        self.specs = Counter()
        self.import_names = Counter()
        self.uses = Counter()
        self.use_files = set()
        self.provenance = "shadcn" if self.module in SHADCN_NAMES else "local"


class Repo(object):
    def __init__(self, path, ui_dir=None):
        self.notes = []
        self.start = os.path.abspath(path)
        self.app_root, self.cj_path = find_app_root(self.start, self.notes)
        self.cj = load_json(self.cj_path) if self.cj_path else {}
        self.pkg = load_json(os.path.join(self.app_root, "package.json"))
        self.ts_paths = self.load_ts_paths()
        self.files, self.by_rel = [], {}
        self.load_files()
        self.ui_dir = self.find_ui_dir(ui_dir)
        self.ui_files, self.ui_names, self.wrappers = {}, {}, {}
        self.index()

    def rel(self, abs_path):
        return os.path.relpath(abs_path, self.app_root).replace(os.sep, "/")

    def load_ts_paths(self):
        out = []
        try:
            names = sorted(n for n in os.listdir(self.app_root) if re.match(r"^(ts|js)config.*\.json$", n))
        except OSError:
            names = []
        for name in names:
            cfg = load_json(os.path.join(self.app_root, name)).get("compilerOptions", {})
            base = os.path.join(self.app_root, cfg.get("baseUrl", "."))
            for pat, targets in (cfg.get("paths") or {}).items():
                if targets:
                    out.append((pat.rstrip("*"), os.path.normpath(os.path.join(base, targets[0].rstrip("*"))), pat.endswith("*")))
        return out

    def load_files(self):
        for base, dirs, files in os.walk(self.app_root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(files):
                if os.path.splitext(name)[1].lower() not in SRC_EXT:
                    continue
                path = os.path.join(base, name)
                rel = self.rel(path)
                if TEST_PATH.search("/" + rel):
                    continue
                text = read_text(path)
                if text is not None:
                    fi = FileInfo(rel, path, text)
                    self.files.append(fi)
                    self.by_rel[rel] = fi

    def find_ui_dir(self, override):
        if override:
            for cand in (os.path.join(self.app_root, override), os.path.abspath(override), os.path.join(self.start, override)):
                if os.path.isdir(cand):
                    return cand
            self.notes.append("--ui-dir %s does not exist" % override)
        alias = (self.cj.get("aliases") or {}).get("ui")
        if alias:
            base = self.resolve_alias(alias)
            if base and os.path.isdir(base):
                return base
            if base:
                self.notes.append("components.json aliases.ui is %s but %s does not exist" % (alias, self.rel(base)))
        for cand in UI_DIR_CANDIDATES:
            p = os.path.join(self.app_root, cand)
            if os.path.isdir(p) and any(fi.abs.startswith(p + os.sep) for fi in self.files):
                return p
        for fi in self.files:
            if re.search(r"/components/ui/[^/]+$", "/" + fi.rel):
                return os.path.dirname(fi.abs)
        return None

    def resolve_alias(self, spec):
        for prefix, target, star in self.ts_paths:
            if star and spec.startswith(prefix):
                return os.path.normpath(os.path.join(target, spec[len(prefix):]))
            if not star and spec == prefix:
                return target
        for prefix in ("@/", "~/"):
            if spec.startswith(prefix):
                src = os.path.join(self.app_root, "src")
                return os.path.normpath(os.path.join(src if os.path.isdir(src) else self.app_root, spec[len(prefix):]))
        return None

    def resolve(self, spec, from_abs):
        """The project file an import specifier points to, as a rel path, or None."""
        if spec.startswith("."):
            base = os.path.normpath(os.path.join(os.path.dirname(from_abs), spec))
        else:
            base = self.resolve_alias(spec)
            if not base:
                return None
        for suffix in ("", ".tsx", ".ts", ".jsx", ".js", "/index.tsx", "/index.ts", "/index.jsx", "/index.js"):
            rel = self.rel(base + suffix)
            if rel in self.by_rel:
                return rel
        return None

    # -- indexing
    def index(self):
        if self.ui_dir:
            prefix = self.ui_dir + os.sep
            for fi in self.files:
                fi.in_ui = fi.abs.startswith(prefix)
        for fi in self.files:
            for imp in fi.imports:
                imp.target = self.resolve(imp.spec, fi.abs)
        for fi in self.files:
            if fi.in_ui:
                continue
            for imp in fi.imports:
                if imp.target and self.by_rel[imp.target].in_ui:
                    fi.ui_modules.add(imp.target)
                    for name, local, typed in imp.names:
                        fi.local_ui[local] = (imp.target, name)
        for fi in self.files:
            if fi.in_ui:
                self.ui_files[fi.rel] = self.analyze_ui(fi)
        for uf in self.ui_files.values():
            for name, comp in uf.comps.items():
                self.ui_names.setdefault(name, comp)
            for key in uf.wraps:
                self.wrappers.setdefault(key, []).append(uf)
        self.tally_use()

    def analyze_ui(self, fi):
        uf = UiFile(fi)
        src = fi.src
        for exp, local in fi.exports:
            if exp not in uf.exports:
                uf.exports.append(exp)
        uf.components = [e for e in uf.exports if e[:1].isupper() and not e.isupper()]
        uf.helpers = [e for e in uf.exports if e not in uf.components and e != "default"]
        uf.cvas = parse_cvas(src)
        local_of = dict((e, l) for e, l in fi.exports)
        chunk_by_name = dict((c.name, c) for c in fi.chunks if c.name)
        for name in uf.components:
            comp = UiComp(name, uf)
            chunk = chunk_by_name.get(local_of.get(name, name))
            if chunk:
                comp.props, comp.unions = parse_props(src, chunk)
                body = src.struct[chunk.start:chunk.end]
                root = self.root_tokens(fi, chunk)
                used = [cv for cv in uf.cvas if cv["name"] and re.search(r"\b%s\b" % re.escape(cv["name"]), body)]
                for cv in used:
                    for g, opts in cv["variants"].items():
                        comp.groups.setdefault(g, opts)
                        comp.tokens.setdefault(g, cv["tokens"][g])
                    comp.defaults.update(cv["defaults"])
                    comp.skins.append((None, root + cv["base"]))
                    for g in STYLE_GROUPS:
                        for opt, toks in cv["tokens"].get(g, {}).items():
                            comp.skins.append((opt, root + cv["base"] + toks))
                for g, opts in comp.unions.items():
                    comp.groups.setdefault(g, opts)
                if not used and root:
                    comp.skins.append((None, root))
            uf.comps[name] = comp
        for name in uf.components:
            roots = [r for r in uf.components if r != name and name.startswith(r) and name[len(r):len(r) + 1].isupper()]
            if roots:
                root = min(roots, key=len)
                uf.compounds.setdefault(root, []).append(name[len(root):])
                uf.comps[root].parts.append(name)
        for root in uf.compounds:
            uf.comps[root].root = True
        for imp in fi.imports:
            if imp.type_only:
                continue
            for label, fam, pat in PRIMITIVE_PKG:
                if not pat.match(imp.spec):
                    continue
                if re.match(r"^(radix-ui|@base-ui(-components)?/react)$", imp.spec):
                    uf.wraps.extend("%s#%s" % (imp.spec, nm) for nm, loc, typed in imp.names if not typed)
                    if imp.namespace:
                        uf.wraps.append("%s#*" % imp.spec)
                else:
                    uf.wraps.append(imp.spec)
        return uf

    def root_tokens(self, fi, chunk):
        """Class tokens on the first element with a className inside one component function."""
        for tag in fi.tags:
            if chunk.start <= tag.start < chunk.end and ("className" in tag.attrs or "class" in tag.attrs):
                return list(tag_tokens(fi.src, tag, True))
        return []

    def tally_use(self):
        for fi in self.files:
            if fi.in_ui:
                continue
            for tag in fi.tags:
                hit = fi.local_ui.get(tag.name.split(".")[0])
                uf = self.ui_files.get(hit[0]) if hit else None
                if uf:
                    uf.uses[hit[1]] += 1
            for imp in fi.imports:
                uf = self.ui_files.get(imp.target) if imp.target else None
                if uf and not imp.type_only:
                    uf.use_files.add(fi.rel)
                    uf.specs[imp.spec] += 1
                    for name, local, typed in imp.names:
                        uf.import_names[name] += 1

    # -- naming what to use
    def ui_spec(self, uf):
        """The import path for a ui file: the one most importers already use, else the alias for ui/ plus the file name."""
        if uf.specs:
            return uf.specs.most_common(1)[0][0]
        alias = (self.cj.get("aliases") or {}).get("ui") or "@/components/ui"
        return "%s/%s" % (alias.rstrip("/"), uf.module)

    def import_style(self):
        if not hasattr(self, "_style"):
            dq = sq = semi = bare = 0
            for fi in [f for f in self.files if not f.in_ui][:120]:
                for m in re.finditer(r"^import [^\n]*from (['\"])[^\n]*?\1(;?)$", fi.src.code, re.M):
                    dq += m.group(1) == '"'
                    sq += m.group(1) == "'"
                    semi += bool(m.group(2))
                    bare += not m.group(2)
            self._style = {"quote": '"' if dq >= sq else "'", "semi": semi > bare}
        return self._style

    def import_line(self, uf):
        names = [n for n, _ in uf.import_names.most_common(4) if n in uf.exports] or (uf.components[:1] or uf.exports[:1])
        st = self.import_style()
        return "import { %s } from %s%s%s%s" % (", ".join(names), st["quote"], self.ui_spec(uf), st["quote"], ";" if st["semi"] else "")

    def runner(self):
        for name, cmd in (("pnpm-lock.yaml", "pnpm dlx"), ("yarn.lock", "yarn dlx"), ("bun.lock", "bunx --bun"), ("bun.lockb", "bunx --bun")):
            if os.path.isfile(os.path.join(self.app_root, name)):
                return cmd
        return "npx"

    def add_hint(self, module):
        return "%s shadcn@latest add %s" % (self.runner(), module)

    def comp(self, name):
        return self.ui_names.get(name)

    def use(self, comp, variant=None, group="variant"):
        """'use `Button` from `@/components/ui/button`, variant `ghost`'"""
        text = "use `%s` from `%s`" % (comp.name, self.ui_spec(comp.ufile))
        if variant:
            text += ", %s `%s`" % (group, variant)
        return text


# --------------------------------------------------------------------------- inventory

def nfiles(n):
    return "%d file%s" % (n, "" if n == 1 else "s")


def family_of(spec, table):
    for label, fam, pat in table:
        if pat.match(spec):
            return label, fam
    return None


def build_stack(repo):
    """What the app really uses, counted from imports. components.json is read only to compare against."""
    prim, icon_direct, cn_files = Counter(), defaultdict(set), 0
    cn_from, tv_files, cva_files = Counter(), 0, 0
    labels = {}
    for fi in repo.files:
        seen = set()
        for imp in fi.imports:
            if imp.target or imp.type_only:
                continue
            hit = family_of(imp.spec, PRIMITIVE_PKG)
            if hit and hit[1] in CENSUS_BASES and hit not in seen:
                seen.add(hit)
                prim[hit[1]] += 1
                labels[hit[1]] = hit[0]
            hit = family_of(imp.spec, ICON_PKG)
            if hit:
                icon_direct[hit].add(fi.rel)
            if imp.spec == "tailwind-variants":
                tv_files += 1
            if imp.spec == "class-variance-authority":
                cva_files += 1
        for imp in fi.imports:
            if any(n == "cn" or l == "cn" for n, l, _ in imp.names):
                cn_files += 1
                cn_from[imp.spec] += 1
                break
    icons, registry = Counter(), {}
    for hit, files in icon_direct.items():
        if len(files) <= 3 and all(repo.by_rel[f].in_ui for f in files):
            weight = sum(len(repo.ui_files[f].use_files) for f in files if f in repo.ui_files)
            registry[hit[1]] = sorted(files)
            icons[hit[1]] = weight or len(files)
        else:
            icons[hit[1]] = len(files)
        labels[hit[1]] = hit[0]
    comp_prop = Counter()
    for uf in repo.ui_files.values():
        code = uf.fi.src.struct
        if re.search(r"\basChild\b", code):
            comp_prop["asChild"] += 1
        if re.search(r"\brender\b", code):
            comp_prop["render"] += 1
    cvas_outside = []
    for fi in repo.files:
        if not fi.in_ui and CVA_CALL.search(fi.src.struct):
            cvas_outside.append(fi.rel)
    tw = (repo.pkg.get("dependencies", {}) or {}).get("tailwindcss") or (repo.pkg.get("devDependencies", {}) or {}).get("tailwindcss")
    return {"primitives": dict(prim), "primitive_labels": labels, "icons": dict(icons), "icon_registry": registry,
            "cn_files": cn_files, "cn_from": dict(cn_from), "cva_files": cva_files, "tv_files": tv_files,
            "composition": dict(comp_prop), "cva_outside_ui": cvas_outside, "tailwind": tw,
            "cva_defs": sum(len(uf.cvas) for uf in repo.ui_files.values())}


def stack_warnings(repo, stack):
    out = []
    cj = repo.cj
    if cj:
        style = str(cj.get("style", ""))
        declared = "base-ui" if style.startswith("base") else ("radix" if style else None)
        prim = stack["primitives"]
        if declared and prim:
            top = max(prim, key=lambda k: prim[k])
            if top != declared and prim[top] > prim.get(declared, 0):
                out.append("components.json style `%s` implies %s, but imports show %s in %s and %s in %d. Trust the imports."
                           % (style, stack["primitive_labels"].get(declared, declared), stack["primitive_labels"].get(top, top),
                              nfiles(prim[top]), stack["primitive_labels"].get(declared, declared), prim.get(declared, 0)))
        lib = str(cj.get("iconLibrary", ""))
        names = {"lucide": "lucide", "tabler": "tabler", "hugeicons": "hugeicons", "phosphor": "phosphor", "remixicon": "remixicon"}
        icons = stack["icons"]
        if lib and icons:
            top = max(icons, key=lambda k: icons[k])
            if names.get(lib, lib) != top and icons[top] > icons.get(names.get(lib, lib), 0):
                out.append("components.json iconLibrary is `%s`, but imports show %s (%s)%s. Trust the imports."
                           % (lib, stack["primitive_labels"].get(top, top), nfiles(icons[top]),
                              " through " + ", ".join(stack["icon_registry"][top]) if top in stack["icon_registry"] else ""))
    comp = stack["composition"]
    if stack["primitives"].get("base-ui") and comp.get("asChild") and not comp.get("render"):
        out.append("Base UI parts compose with `render`, but ui/ uses `asChild`; check the wrapper's props before choosing one.")
    if stack["cva_outside_ui"]:
        out.append("cva or tv defined outside ui/ in %d file(s) (%s): a new look is a variant in ui/, not a local variants table."
                   % (len(stack["cva_outside_ui"]), ", ".join(stack["cva_outside_ui"][:3])))
    for note in repo.notes:
        out.append(note)
    return out


def layer_of(rel):
    parts = rel.split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    if not parts:
        return "."
    return "/".join(parts[:2]) if parts[0] == "components" and len(parts) > 2 else parts[0]


def build_inventory(repo):
    stack = build_stack(repo)
    ui = []
    for rel in sorted(repo.ui_files):
        uf = repo.ui_files[rel]
        groups = {}
        for name in uf.components:
            comp = uf.comps[name]
            if comp.groups and (comp.root or name not in sum((uf.comps[r].parts for r in uf.compounds), [])):
                groups[name] = dict((g, {"options": o, "default": comp.defaults.get(g)}) for g, o in comp.groups.items())
        ui.append({
            "file": rel, "module": uf.module, "provenance": uf.provenance, "import": repo.import_line(uf), "spec": repo.ui_spec(uf),
            "components": uf.components, "helpers": uf.helpers,
            "compounds": dict((r, sorted(set(p))) for r, p in uf.compounds.items()),
            "wraps": uf.wraps, "groups": groups,
            "cva": [{"name": c["name"], "kind": c["kind"], "line": c["line"], "compound_variants": c["compound"],
                     "variants": c["variants"], "defaults": c["defaults"]} for c in uf.cvas],
            "props": dict((n, uf.comps[n].props) for n in uf.components if uf.comps[n].props and (uf.comps[n].root or not uf.compounds)),
            "uses": dict(uf.uses), "used_in_files": len(uf.use_files),
            "registry": len(uf.components) > 25,
        })
    layers = defaultdict(list)
    for fi in repo.files:
        if fi.in_ui or fi.ext not in JSX_EXT:
            continue
        comps = []
        for exp, loc in fi.exports:
            if exp[:1].isupper() and any(c.name == loc and c.component for c in fi.chunks):
                comps.append(exp)
        if comps:
            layers[layer_of(fi.rel)].append({"file": fi.rel, "components": comps,
                                             "ui": sorted(repo.ui_files[m].module for m in fi.ui_modules if m in repo.ui_files)})
    return {"root": repo.app_root, "ui_dir": repo.rel(repo.ui_dir) if repo.ui_dir else None, "files_scanned": len(repo.files),
            "stack": stack, "warnings": stack_warnings(repo, stack), "ui": ui, "layers": dict(layers),
            "wrappers": sorted(repo.wrappers)}


def fmt_groups(entry, limit=0):
    out = []
    for comp, groups in entry["groups"].items():
        for g, info in groups.items():
            opts = [(o + "*" if o == info["default"] else o) for o in info["options"]]
            out.append((comp, g, opts))
    return out


def render_inventory(data, repo, brief):
    L = []
    st = data["stack"]
    prim = ", ".join("%s %s" % (st["primitive_labels"].get(k, k), nfiles(v)) for k, v in sorted(st["primitives"].items(), key=lambda kv: -kv[1])) or "none found"
    icons = ", ".join("%s %s%s" % (st["primitive_labels"].get(k, k), nfiles(v), " (via %s)" % ", ".join(st["icon_registry"][k]) if k in st["icon_registry"] else "")
                      for k, v in sorted(st["icons"].items(), key=lambda kv: -kv[1])) or "none found"
    variants = "cva x%d" % st["cva_defs"] if st["cva_defs"] else ("tv" if st["tv_files"] else "none in ui/")
    comp = ", ".join("%s in %s" % (k, nfiles(v)) for k, v in sorted(st["composition"].items())) or "n/a"
    L.append("ui_inventory: %s (ui: %s, %s scanned)" % (os.path.basename(data["root"]) or data["root"], data["ui_dir"] or "NOT FOUND", nfiles(data["files_scanned"])))
    L.append("stack (from imports; components.json is not trusted)")
    L.append("  primitives   %s" % prim)
    L.append("  icons        %s" % icons)
    L.append("  variants     %s; cn() in %s%s" % (variants, nfiles(st["cn_files"]), " from " + ", ".join(sorted(st["cn_from"])) if st["cn_from"] else ""))
    L.append("  tailwind     %s; composition %s" % (st["tailwind"] or "not in package.json", comp))
    if data["warnings"]:
        L.append("warnings")
        for w in data["warnings"][:5 if brief else 20]:
            L.append("  ! " + w)
    if not data["ui_dir"]:
        L.append("no ui/ folder found: nothing to reuse yet. Add the shadcn components the screen needs first, or pass --ui-dir.")
        return L
    L.append("ui/ components (%s); import line pattern: %s" % (nfiles(len(data["ui"])), data["ui"][0]["import"] if data["ui"] else "n/a"))
    reg = [u for u in data["ui"] if u["registry"]]
    shown = [u for u in data["ui"] if not u["registry"]]
    plain = []
    for u in shown:
        comps = u["components"]
        if u["uses"]:
            used = "x%d in %s" % (sum(u["uses"].values()), nfiles(u["used_in_files"]))
        else:
            used = "imported in %s" % nfiles(u["used_in_files"]) if u["used_in_files"] else "unused outside ui/"
        gl = fmt_groups(u)
        if brief:
            if u["compounds"]:
                names = "; ".join("%s: %s" % (r, " ".join(p[:6]) + (" +%d" % (len(p) - 6) if len(p) > 6 else "")) for r, p in u["compounds"].items())
            elif comps:
                names = " ".join(comps[:4]) + (" ..." if len(comps) > 4 else "")
            else:
                names = "helpers: " + " ".join(u["helpers"][:3])
            if not gl:
                plain.append("%s{%s}" % (u["module"], names))
                continue
            head = u["module"].ljust(14) + " " + names
            for comp_name, g, opts in gl[:2]:
                head += "  %s[%s]" % (g, " ".join(opts[:8]) + (" ..." if len(opts) > 8 else ""))
            L.append("  " + head[:170])
            continue
        tag = "[%s]" % u["provenance"]
        names = "; ".join("%s: %s" % (r, ", ".join(p)) for r, p in u["compounds"].items()) or ", ".join(comps) or "helpers: " + ", ".join(u["helpers"])
        L.append("  %s %s  %s  %s" % (u["module"], tag, names, used))
        for comp_name, g, opts in gl:
            L.append("      %s.%s: %s" % (comp_name, g, ", ".join(opts)))
        for c in u["cva"]:
            if c["compound_variants"]:
                L.append("      %s has %d compound variants" % (c["name"] or "cva", c["compound_variants"]))
        if u["props"]:
            first = sorted(u["props"])[0]
            L.append("      props (%s): %s" % (first, ", ".join(u["props"][first])))
        if u["wraps"]:
            L.append("      wraps: %s" % ", ".join(u["wraps"][:4]))
        L.append("      %s" % u["import"])
    if plain:
        L.append("  plain, no variants (module{Root: Parts}):")
        line = "   "
        for item in plain:
            if len(line) + len(item) > 118:
                L.append(line)
                line = "   "
            line += " " + item
        L.append(line)
    for u in reg:
        L.append("  %s: registry of %d exports (import by name)" % (u["module"], len(u["components"])))
    if data["layers"]:
        L.append("other layers (components outside ui/)")
        for layer, items in sorted(data["layers"].items()):
            names = [n for it in items for n in it["components"]]
            if brief:
                L.append("  %s (%d): %s" % (layer, len(names), ", ".join(names[:7]) + (" ..." if len(names) > 7 else "")))
            else:
                L.append("  %s (%d components in %d files)" % (layer, len(names), len(items)))
                for it in items:
                    L.append("    %s: %s%s" % (it["file"], ", ".join(it["components"]), "  <- ui: " + ", ".join(it["ui"]) if it["ui"] else ""))
    L.append("rule order: reuse a variant, extend ui/ with a variant or slot, install from the registry, compose, create only with a reason")
    if brief and len(L) > 58:
        keep = L[:50]
        keep.append("  ... %d more lines; run without --brief" % (len(L) - 51))
        keep.append(L[-1])
        return keep
    return L


# --------------------------------------------------------------------------- audit

ANCHORED = ("secondary", "primary", "accent")  # shadcn token names: only a color that starts with the word counts, not background-secondary
STATUS = ("destructive", "warning", "success", "info", "brand")
SEMANTICS = [
    ("destructive", re.compile(r"destructive|danger|error|critical|red|rose")),
    ("brand", re.compile(r"brand")),
    ("success", re.compile(r"success|green|emerald|teal")),
    ("warning", re.compile(r"warning|amber|yellow|orange")),
    ("info", re.compile(r"info|blue|sky|indigo")),
    ("secondary", re.compile(r"secondary|muted|zinc|slate|gray|neutral|stone")),
    ("primary", re.compile(r"primary")),
    ("accent", re.compile(r"accent")),
]


def semantics_of_tokens(tokens):
    out = set()
    colors = []
    for t in tokens:
        b, pre = base_token(t)
        if category(b) == "color" and b.startswith(("bg-", "border-", "ring-")):
            colors.append((b, pre))
            cname = b.split("-", 1)[1]
            for name, pat in SEMANTICS:
                if (pat.match(cname) if name in ANCHORED else pat.search(cname)):
                    out.add(name)
    solid = [b for b, pre in colors if b.startswith("bg-") and not pre]
    if any(b.startswith("border-") for b, pre in colors) and not solid:
        out.add("outline")
    if not solid and any(b.startswith("bg-") and pre.startswith(("hover:", "focus")) for b, pre in colors):
        out.add("ghost")
    if any(base_token(t)[0] == "underline" for t in tokens):
        out.add("link")
    return out


def semantics_of_option(name):
    low = name.lower()
    out = set()
    for sem, pat in SEMANTICS:
        if pat.search(low):
            out.add(sem)
    for key in ("outline", "ghost", "link"):
        if key in low:
            out.add(key)
    if low in ("default", "solid", "filled"):
        out.add("primary")
    return out


def best_option(comp, group, items, icon_only=False):
    """The option of one variant group that best matches a set of restated classes, as (option, score)."""
    opts = comp.groups.get(group, [])
    toks = comp.tokens.get(group, {})
    item_set = set(items)
    item_base = set(base_token(t)[0] for t in items)
    sem_items = semantics_of_tokens(items) if group in STYLE_GROUPS else set()
    best, best_score = None, 0
    for o in opts:
        otoks = toks.get(o, [])
        score = 2 * len(item_set & set(otoks)) + len(item_base & set(base_token(t)[0] for t in otoks))
        if group in STYLE_GROUPS:
            score += len(sem_items & semantics_of_option(o))
        else:
            low = o.lower()
            for t in items:
                b = base_token(t)[0]
                if icon_only and b == "rounded-full" and re.search(r"full|circle|pill|round", low):
                    score += 2
                m = re.match(r"^text-(xs|sm|lg|xl)$", b)
                if m and low in (m.group(1), "icon-" + m.group(1)):
                    score += 1
        if score > best_score:
            best, best_score = o, score
    return best, best_score


def suggest_option(comp, group, items, icon_only=False):
    return best_option(comp, group, items, icon_only)[0]


def skin_candidates(repo):
    if not hasattr(repo, "_skins"):
        out = []
        for comp in repo.ui_names.values():
            if SKIN_DENY.search(comp.name):
                continue
            for opt, toks in comp.skins:
                feats = skin_features(toks)
                if len(feats) >= 3 and any(f[0] == "bg" for f in feats):
                    out.append((comp, opt, feats))
        repo._skins = out
    return repo._skins


def skin_match(repo, tokens):
    """The ui/ component whose surface (fill, border, radius, shadow) a class set repeats, as (comp, option, matched) or None."""
    feats = skin_features(tokens)
    bgs = set(f for f in feats if f[0] == "bg")
    best = None
    for comp, opt, cf in skin_candidates(repo):
        if not (cf & bgs):
            continue
        matched = len(cf & feats)
        if matched < max(3, len(cf) - 1):
            continue
        key = (matched, sum(comp.ufile.uses.values()), opt is None)
        if best is None or key > best[0]:
            best = (key, comp, opt)
    return (best[1], best[2], best[0][0]) if best else None


def style_group(comp, cat):
    names = STYLE_GROUPS if cat == "color" else SIZE_GROUPS
    for g in comp.groups:
        if g in names:
            return g
    return None


def parse_allow_attr(text):
    """data-ui-allow="rule.id: reason; other.rule: reason" gives {rule: reason}."""
    out = {}
    for part in text.split(";"):
        if ":" in part:
            rule, reason = part.split(":", 1)
        else:
            rule, reason = part, ""
        if rule.strip():
            out[rule.strip()] = reason.strip()
    return out


def is_exempt(fi):
    return fi.in_ui or bool(re.search(r"(^|/)__variants__/[^/]+/picker\.[jt]sx?$", fi.rel))


class Audit(object):
    def __init__(self, repo, hints=False):
        self.repo, self.hints = repo, hints
        self.findings = []

    def add(self, rule, fi, pos, value, message, fix, tag=None, component=None, line=None):
        src = fi.src
        allowed = None
        if tag is not None and "data-ui-allow" in tag.attrs:
            raw = attr_literal(src, tag, "data-ui-allow")
            if raw is not None:
                grants = parse_allow_attr(raw)
                short = rule.replace("system.", "")
                for key, reason in grants.items():
                    if key in (rule, short, "*"):
                        allowed = reason or "no reason given"
                        if not reason:
                            self.add("system.allow_reason", fi, tag.start, key, "data-ui-allow for `%s` has no reason" % key,
                                     "write data-ui-allow=\"%s: why this is a real gap\"" % rule)
        ch = fi.chunk_at(pos) if component is None else None
        self.findings.append({"rule": rule, "sev": RULES[rule], "file": fi.rel, "line": line or src.line(pos), "value": value,
                              "message": message, "fix": fix, "allowed": allowed,
                              "component": component or (ch.name if ch and ch.component else None)})

    # ---- one file
    def file(self, fi):
        repo = self.repo
        self.suppressions(fi)
        if not repo.ui_dir:
            return
        self.primitive_bypass(fi)
        shadow_chunks, covered = self.name_shadow(fi)
        markdown = any(re.match(r"^(react-markdown|markdown-to-jsx|@mdx-js/|marked$|remark-react|rehype-react)", i.spec) for i in fi.imports)
        for tag in fi.tags:
            nm = tag.name
            if nm in RAW_CONTROLS:
                if tag.start in covered or (markdown and nm in ("table", "hr", "input")):
                    continue
                self.raw_element(fi, tag)
            elif nm in ("a", "img") and self.hints:
                self.raw_hint(fi, tag)
            elif nm in CONTAINER_TAGS:
                ch = fi.chunk_at(tag.start)
                if not (ch and ch.start in shadow_chunks):
                    self.skin_copy(fi, tag)
            else:
                hit = fi.local_ui.get(nm.split(".")[0])
                if hit:
                    self.variant_restated(fi, tag, hit)

    def suppressions(self, fi):
        ui_lint = re.compile(r"(shadcn|no-restyle|no-raw-colors|no-arbitrary-values|no-inline-styles|no-unknown-classes|forbid-elements|"
                             r"forbid-dom-props|no-restricted-imports|no-restricted-syntax|no-html-|design-system|tailwindcss/)")
        for a, b in fi.src.comments:
            text = fi.src.text[a:b]
            m = re.search(r"eslint-disable(?:-next-line|-line)?\b([\s\S]*)", text)
            if not m:
                continue
            body = m.group(1).replace("*/", "").strip()
            parts = re.split(r"\s--\s*|\s--$", body, maxsplit=1)
            rules = [r.strip() for r in parts[0].split(",") if r.strip()]
            reason = parts[1].strip() if len(parts) > 1 else ""
            hit = [r for r in rules if ui_lint.search(r)]
            if (hit or not rules) and len(reason) < 3:
                what = ", ".join(hit) if hit else "every rule"
                self.add("system.suppression", fi, a, what, "eslint-disable for %s has no reason" % what,
                         "add ` -- <why>` after the rule list, or use the component the rule points to instead")

    def primitive_bypass(self, fi):
        repo = self.repo
        for imp in fi.imports:
            if imp.type_only:
                continue
            hit = family_of(imp.spec, PRIMITIVE_PKG)
            if not hit or PRIMITIVE_EXEMPT.search(imp.spec):
                continue
            barrel = re.match(r"^(radix-ui|@base-ui(-components)?/react)$", imp.spec)
            if barrel:
                keys = ["%s#%s" % (imp.spec, n) for n, l, t in imp.names if not t and n not in PRIMITIVE_EXEMPT_NAMES]
                if imp.namespace:
                    keys.append("%s#*" % imp.spec)
            else:
                keys = [imp.spec] if not (imp.names and all(n in PRIMITIVE_EXEMPT_NAMES for n, l, t in imp.names)) else []
            for key in keys:
                module = kebab(key.split("#")[1]) if "#" in key and key.split("#")[1] != "*" else re.sub(r"^react-", "", key.split("/")[-1])
                module = {"cmdk": "command", "vaul": "drawer"}.get(module, module)
                owners = repo.wrappers.get(key) or []
                uf = next((u for u in owners if u.module == module), owners[0] if owners else None)
                if uf:
                    comp = next((r for r in uf.compounds if kebab(r) == module), None) or (next(iter(uf.compounds), None)) or (uf.components[:1] or [uf.module])[0]
                    use = "use `%s` from `%s`" % (comp, repo.ui_spec(uf))
                    self.add("system.primitive_bypass", fi, imp.pos, key, "imports `%s` directly; `%s` in ui/ already wraps it" % (key, uf.rel.split("/")[-1]), use)
                else:
                    if module in SHADCN_NAMES:
                        fix = "no `ui/%s` yet; install it with `%s`, then import the wrapper" % (module, repo.add_hint(module))
                    else:
                        fix = "no wrapper in ui/ yet; add `ui/%s.tsx` that owns the styling and import that" % module
                    self.add("system.primitive_bypass", fi, imp.pos, key, "imports `%s` directly and ui/ has no wrapper for it" % key, fix)

    # ---- raw elements
    def raw_hint(self, fi, tag):
        repo = self.repo
        if "data-ui-allow" in tag.attrs:
            return
        cands = sorted(set(n for f in repo.files if not f.in_ui for n, l in f.exports if n[:1].isupper() and re.search(r"Link$", n)))
        if tag.name == "a":
            href = attr_text(fi.src, tag, "href") or ""
            if cands and not re.match(r"^[\"']?(https?:|mailto:|#)", href):
                self.findings.append({"rule": "system.raw_element:hint", "sev": "P3", "file": fi.rel, "line": fi.src.line(tag.start),
                                      "value": "<a>", "message": "raw `<a>` for an in-app link",
                                      "fix": "use `%s` so navigation stays client-side" % cands[0], "allowed": None, "component": None})
        else:
            av = repo.comp("Avatar")
            if av:
                self.findings.append({"rule": "system.raw_element:hint", "sev": "P3", "file": fi.rel, "line": fi.src.line(tag.start),
                                      "value": "<img>", "message": "raw `<img>`", "fix": "if this is a person or workspace image, %s" % repo.use(av),
                                      "allowed": None, "component": None})

    def want_for(self, fi, tag):
        """Which ui/ component should replace a raw control, as (names to try, group source tokens)."""
        src, nm = fi.src, tag.name
        typ = (attr_literal(src, tag, "type") or "").lower()
        role = (attr_literal(src, tag, "role") or "").lower()
        if nm == "input":
            if typ in ("file", "hidden", "image"):
                return None
            return {"checkbox": ["Checkbox"], "radio": ["RadioGroup"], "range": ["Slider"]}.get(typ, ["Input"])
        if nm == "button":
            if role == "tab":
                return ["TabsTrigger"]
            if role == "switch":
                return ["Switch"]
            if role == "checkbox":
                return ["Checkbox"]
            if role == "menuitem":
                return ["DropdownMenuItem"]
            if "aria-pressed" in tag.attrs:
                return ["Toggle", "ToggleGroupItem", "Button"]
            return ["Button"]
        return {"select": ["NativeSelect", "Select"], "textarea": ["Textarea"], "table": ["Table"], "hr": ["Separator"]}[nm]

    def raw_element(self, fi, tag):
        repo, src = self.repo, fi.src
        if fi.in_render(tag.start) or re.search(r"\brender\s*:\s*\(?\s*$", src.struct[max(0, tag.start - 80):tag.start]):
            return
        want = self.want_for(fi, tag)
        if want is None:
            return
        comp = next((repo.comp(n) for n in want if repo.comp(n)), None)
        toks = tag_tokens(src, tag)
        if comp is None:
            module = kebab(want[-1])
            module = {"tabs-trigger": "tabs", "dropdown-menu-item": "dropdown-menu", "toggle-group-item": "toggle-group"}.get(module, module)
            fix = "ui/ has no %s; %s, then use it" % (" or ".join("`%s`" % n for n in want), "install it with `%s`" % repo.add_hint(module) if module in SHADCN_NAMES
                                                       else "add `ui/%s.tsx` first" % module)
            self.add("system.raw_element", fi, tag.start, "<%s>" % tag.name, "raw `<%s>` and ui/ does not have the component yet" % tag.name, fix, tag)
            return
        variant = None
        group = "variant"
        if comp.name == "Button" and "variant" in comp.groups:
            variant = suggest_option(comp, "variant", toks)
            if not variant:
                sems = semantics_of_tokens(toks)
                for pick in ("link", "outline", "ghost"):
                    if pick in sems and pick in comp.groups["variant"]:
                        variant = pick
                        break
            if not variant and not any(category(base_token(t)[0]) == "color" for t in toks) and "ghost" in comp.groups["variant"]:
                variant = "ghost"
        fix = repo.use(comp, variant, group)
        if comp.name == "Button" and not variant and "variant" in comp.groups:
            fix += "; pick the closest of %s" % ", ".join(comp.groups["variant"][:8])
        self.add("system.raw_element", fi, tag.start, "<%s>" % tag.name, "raw `<%s>` where `%s` exists in ui/" % (tag.name, comp.name), fix, tag)

    # ---- name shadow
    NAME_CATEGORY = {"Button": "button", "Btn": "button", "Card": "card", "Modal": "dialog", "Dialog": "dialog", "Sheet": "dialog",
                     "Drawer": "dialog", "Input": "input", "Textarea": "input", "Select": "select", "Badge": "badge", "Switch": "switch",
                     "Checkbox": "checkbox", "Tooltip": "tooltip", "Tabs": "tabs"}
    PHRASE = {"button": "a button", "card": "a card", "dialog": "a dialog", "input": "an input", "select": "a select", "badge": "a badge",
              "switch": "a switch", "checkbox": "a checkbox", "tooltip": "a tooltip", "tabs": "tabs"}
    CATEGORY_UI = {"button": ["Button"], "card": ["Card"], "dialog": ["Dialog", "Sheet", "Drawer", "AlertDialog"],
                   "input": ["Input", "Textarea", "InputGroup"], "select": ["Select", "NativeSelect", "Combobox"], "badge": ["Badge"],
                   "switch": ["Switch"], "checkbox": ["Checkbox"], "tooltip": ["Tooltip"], "tabs": ["Tabs"]}

    def name_shadow(self, fi):
        """Local components named like a ui/ part that render the raw markup instead. Returns (chunk starts, covered tag starts)."""
        repo, src = self.repo, fi.src
        flagged, covered = set(), set()
        for ch in fi.chunks:
            if not ch.component:
                continue
            tags = [t for t in fi.tags if ch.start <= t.start < ch.end]
            if not tags:
                continue
            used = set(fi.local_ui[t.name.split(".")[0]][1] for t in tags if t.name.split(".")[0] in fi.local_ui)
            name = ch.name
            last = words(name)[-1] if words(name) else name
            cat = self.NAME_CATEGORY.get(last)
            if not cat:
                continue
            cands = [repo.comp(n) for n in self.CATEGORY_UI[cat] if repo.comp(n)]
            if not cands or used & set(self.CATEGORY_UI[cat]):
                continue
            hit = self.renders_raw(fi, ch, tags, cat)
            if hit is None:
                continue
            variant = None
            comp = cands[0]
            if hit is not True and cat in ("input", "select"):
                want = self.want_for(fi, hit) or []
                comp = next((repo.comp(n) for n in want if repo.comp(n)), comp)
            if cat == "button" and "variant" in comp.groups and hit is not True:
                variant = suggest_option(comp, "variant", tag_tokens(src, hit))
            pos = ch.start if hit is True else hit.start
            self.add("system.name_shadow", fi, pos, name, "`%s` hand-builds %s%s instead of wrapping the ui/ one" % (
                name, self.PHRASE.get(cat, cat), "" if hit is True else " with a raw `<%s>`" % hit.name), repo.use(comp, variant),
                tag=None if hit is True else hit, component=name)
            flagged.add(ch.start)
            if hit is not True:
                covered.add(hit.start)
        return flagged, covered

    def renders_raw(self, fi, ch, tags, cat):
        """The raw tag (or True) when a component function renders its own version of a ui/ part."""
        src = fi.src
        body = src.struct[ch.start:ch.end]
        root = next((t for t in tags if "className" in t.attrs or "class" in t.attrs), None)
        for t in tags:
            if t.name in ("button",) and cat == "button" and not fi.in_render(t.start):
                return t
            if t.name == "input" and cat in ("input", "checkbox", "switch"):
                typ = (attr_literal(src, t, "type") or "").lower()
                if cat == "input" and typ not in ("file", "hidden", "checkbox", "radio", "range"):
                    return t
                if cat in ("checkbox", "switch") and typ == "checkbox":
                    return t
            if t.name == "textarea" and cat == "input":
                return t
            if t.name == "select" and cat == "select":
                return t
            role = (attr_literal(src, t, "role") or "").lower()
            if (cat == "switch" and role == "switch") or (cat == "checkbox" and role == "checkbox") or (cat == "tooltip" and role == "tooltip") \
                    or (cat == "tabs" and role == "tablist") or (cat == "dialog" and (role == "dialog" or "aria-modal" in t.attrs)):
                return t
            toks = tag_tokens(src, t, True)
            if cat == "card" and t is root and t.name in CONTAINER_TAGS and skin_match(self.repo, toks):
                return t
            if cat == "badge" and t is root and t.name in ("span", "div") and len(skin_features(toks)) >= 2 and any(base_token(x)[0].startswith("px-") for x in toks) \
                    and any(base_token(x)[0] in ("text-xs", "text-sm") for x in toks) and any(base_token(x)[0].startswith("rounded") for x in toks):
                return t
            if cat == "dialog" and "fixed" in toks and "inset-0" in toks:
                return t
        if cat == "dialog" and re.search(r"\bcreatePortal\s*\(", body):
            return True
        return None

    # ---- skin copy and restated variants
    SKIN_TAGS = {"div", "section", "article", "aside", "li", "form", "fieldset", "figure"}

    def skin_copy(self, fi, tag):
        if tag.name not in self.SKIN_TAGS:
            return
        toks = tag_tokens(fi.src, tag, True)
        if len(toks) < 3 or not self.card_like(toks):
            return
        hit = skin_match(self.repo, toks)
        if not hit:
            return
        comp, opt, matched = hit
        group = style_group(comp, "color")
        feats = sorted(set(f[1] or f[0] for f in skin_features(toks) if f[0] in SURFACE_TOKENS))
        self.add("system.skin_copy", fi, tag.start, "<%s> as %s" % (tag.name, comp.name),
                 "`<%s>` repeats the skin of `%s` (%s)" % (tag.name, comp.name, ", ".join(feats[:5])),
                 self.repo.use(comp, opt if opt and group else None, group or "variant"), tag)

    @staticmethod
    def card_like(toks):
        """A container that is a surface in its own right: not a scroll frame around a table, a floating control group, or a tight toolbar."""
        bases = [base_token(t)[0] for t in toks if not base_token(t)[1]]
        if any(b in ("absolute", "fixed", "sticky", "overflow-x-auto", "overflow-auto", "overflow-clip", "overflow-y-auto") for b in bases):
            return False
        pads = [b for b in bases if category(b) == "padding"]
        if pads and all(re.search(r"-(0|0\.5|px|1)$", b) for b in pads):
            return False
        if not pads and any(b.startswith("overflow-") for b in bases):
            return False
        return True

    def variant_restated(self, fi, tag, hit):
        """className on a ui/ component that sets a surface color or a shape the component already has a variant for."""
        repo, src = self.repo, fi.src
        uf = repo.ui_files.get(hit[0])
        comp = uf.comps.get(hit[1]) if uf else None
        if not comp or not comp.groups:
            return
        plain = []
        for t in tag_tokens(src, tag, True):
            b, pre = base_token(t)
            if not pre:
                plain.append((t, b))
        sided = any(re.match(r"^border-[xytrbl]$", b) for t, b in plain)  # a divider line: its color is layout, not the variant
        surface = [t for t, b in plain if category(b) == "color" and (b.startswith("bg-") or (b.startswith("border-") and not sided))]
        cats = defaultdict(list)
        if surface:
            cats["color"] = surface + [t for t, b in plain if category(b) == "color" and b.startswith("text-")]
        cats["shape"] = [t for t, b in plain if category(b) == "shape" and b not in ("rounded-none", "rounded")]
        if "size" not in tag.attrs:
            cats["padding"] = [t for t, b in plain if category(b) == "padding" and not re.search(r"-0$", b)]
            cats["type"] = [t for t, b in plain if category(b) == "type"]
        by_group = defaultdict(dict)
        for cat, items in cats.items():
            g = style_group(comp, cat)
            if g and items:
                by_group[g][cat] = items
        icon_only = (attr_literal(src, tag, "size") or "").startswith("icon") or any(re.match(r"^size-\d", b) for t, b in plain)
        for g, cs in by_group.items():
            items = [t for ts in cs.values() for t in ts]
            opt, score = best_option(comp, g, items, icon_only)
            current = attr_literal(src, tag, g)
            if current and opt != current:
                status = g in STYLE_GROUPS and bool(semantics_of_option(opt or "") & set(STATUS)) and score >= 1
                if not (status or (score >= 3 if g in STYLE_GROUPS else icon_only)):
                    opt = None
            what = "; ".join("%s `%s`" % (c, " ".join(ts[:3])) for c, ts in sorted(cs.items()))
            opts = comp.groups[g]
            if opt and opt == current:
                fix = "`%s=\"%s\"` already gives this look; drop the override, or add a variant to `%s` if the new look is wanted" % (
                    g, current, uf.rel.split("/")[-1])
            elif opt:
                fix = repo.use(comp, opt, g)
            else:
                fix = "pick the closest `%s` of %s, or add one to `%s`: a new look is a variant, not a className" % (
                    g, ", ".join(opts[:8]), uf.rel.split("/")[-1])
            self.add("system.variant_restated", fi, tag.start, "%s %s" % (comp.name, g),
                     "`%s` has a `%s` group (%s) and `className` restates %s" % (comp.name, g, ", ".join(opts[:6]), what), fix, tag)


def rule_repeated_classes(repo, audit, targets):
    sets = defaultdict(list)
    for fi in repo.files:
        if is_exempt(fi) or fi.ext not in JSX_EXT:
            continue
        for tag in fi.tags:
            for text in tag_class_strings(fi.src, tag, True):
                toks = text.split()
                key = frozenset(toks)
                if len(key) >= 6 and has_surface(toks):
                    sets[key].append((fi, tag))
    target_rels = set(fi.rel for fi in targets)
    for key, occ in sets.items():
        if len(occ) < 3:
            continue
        digest = hashlib.md5(" ".join(sorted(key)).encode("utf-8")).hexdigest()[:8]
        files = sorted(set(fi.rel for fi, _ in occ))
        shown = 0
        for fi, tag in occ:
            if fi.rel not in target_rels or shown >= 3:
                continue
            shown += 1
            head = tag.name.split(".")[0]
            hit = fi.local_ui.get(head)
            uf = repo.ui_files.get(hit[0]) if hit else None
            comp = uf.comps.get(hit[1]) if uf else None
            sample = " ".join(sorted(key)[:5]) + " ..."
            if comp:
                fix = "add a variant to `%s` (%s) and use it; a new look is a variant, not a className" % (
                    uf.rel.split("/")[-1], ", ".join("%s: %s" % (g, "/".join(o[:4])) for g, o in comp.groups.items()) or "it has no variant groups yet")
            else:
                sk = skin_match(repo, sorted(key))
                if sk:
                    fix = repo.use(sk[0], sk[1] if sk[1] and style_group(sk[0], "color") else None)
                else:
                    fix = "extract one component, or a variant on the nearest ui/ component, that owns this class set"
            audit.add("system.repeated_classes", fi, tag.start, digest,
                      "`<%s>` carries a class set (%s) that appears %d times in %s" % (tag.name, sample, len(occ), nfiles(len(files))), fix, tag)


NEAR_DUP_COMMON = {"button", "icon", "icons", "skeleton", "spinner", "separator", "label", "tooltip"}


def rule_near_duplicate(repo, audit, targets):
    common = set(rel for rel, uf in repo.ui_files.items() if len(uf.components) > 25 or uf.module in NEAR_DUP_COMMON)
    parts = dict((fi.rel, set(fi.ui_modules) - common) for fi in repo.files)
    cands = [fi for fi in repo.files if not is_exempt(fi) and fi.ext in JSX_EXT and len(parts[fi.rel]) >= 3
             and any(c.component for c in fi.chunks)]
    parent = dict((fi.rel, fi.rel) for fi in cands)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for i, a in enumerate(cands):
        for b in cands[i + 1:]:
            inter = len(parts[a.rel] & parts[b.rel])
            if inter / float(len(parts[a.rel] | parts[b.rel])) >= 0.75:
                parent[find(a.rel)] = find(b.rel)
    clusters = defaultdict(list)
    for fi in cands:
        clusters[find(fi.rel)].append(fi)
    target_rels = set(fi.rel for fi in targets)
    for members in clusters.values():
        if len(members) < 3:
            continue
        shared = set.intersection(*[parts[m.rel] for m in members])
        mods = sorted(repo.ui_files[m].module for m in shared if m in repo.ui_files)
        names = ", ".join(os.path.basename(m.rel) for m in members[:5])
        for fi in members:
            if fi.rel in target_rels:
                audit.add("system.near_duplicate", fi, 0, "cluster of %d" % len(members),
                          "%d components are built from nearly the same ui/ parts (%s): %s" % (len(members), ", ".join(mods) or "overlapping sets", names),
                          "a primitive may be missing: search the registry (`%s shadcn@latest search`), add it to ui/ once, and use it from each" % repo.runner(),
                          line=1)


def keyed(findings):
    counts = Counter()
    for f in findings:
        counts["%s|%s|%s" % (f["rule"], f["file"], f["value"])] += 1
    return counts


def parse_allows(items):
    out = []
    for item in items or []:
        m = re.match(r"^([\w.:*-]+)=([^:]+):(.*)$", item)
        if not m or len(m.group(3).strip()) < 3:
            raise ValueError("--allow needs RULE=PATH:reason with a reason of at least 3 characters, got %r" % item)
        rule = m.group(1) if "." in m.group(1) or m.group(1) == "*" else "system." + m.group(1)
        out.append((rule, m.group(2).strip(), m.group(3).strip()))
    return out


def apply_allows(findings, allows):
    for f in findings:
        if f["allowed"]:
            continue
        for rule, path, reason in allows:
            if (rule == "*" or rule == f["rule"].split(":")[0]) and (fnmatch.fnmatch(f["file"], path) or path in f["file"]):
                f["allowed"] = reason
                break


def run_audit(repo, files, hints=False, allows=()):
    audit = Audit(repo, hints)
    targets = [fi for fi in repo.files if not is_exempt(fi) and (files is None or fi.rel in files)]
    for fi in targets:
        audit.file(fi)
    if repo.ui_dir:
        rule_repeated_classes(repo, audit, targets)
        rule_near_duplicate(repo, audit, targets)
    apply_allows(audit.findings, allows)
    audit.findings.sort(key=lambda f: (SEV_RANK[f["sev"]], f["rule"], f["file"], f["line"]))
    return audit.findings, targets


# --------------------------------------------------------------------------- summary

def resolve_names(repo, lines):
    """Turn `path` or `STATUS<TAB>path` lines into [(status, rel)] for files inside the app. Deleted files are dropped."""
    out, seen = [], set()
    anchors = [os.getcwd(), repo.app_root]
    up = repo.app_root
    for _ in range(4):
        up = os.path.dirname(up)
        anchors.append(up)
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        status, path = "?", raw
        if "\t" in raw:
            head, path = raw.split("\t", 1)
            if re.match(r"^[AMDRCTU]\d*$", head.strip()):
                status = head.strip()[0]
                path = path.split("\t")[-1]
        if status == "D":
            continue
        for anchor in anchors:
            cand = os.path.normpath(os.path.join(anchor, path.strip()))
            if os.path.exists(cand) and (cand + os.sep).startswith(repo.app_root + os.sep):
                paths = [cand]
                if os.path.isdir(cand):
                    paths = [os.path.join(b, f) for b, ds, fs in os.walk(cand) for f in fs]
                for p in sorted(paths):
                    rel = repo.rel(p)
                    if rel not in seen:
                        seen.add(rel)
                        out.append((status, rel))
                break
    return out


NEAREST_STOP = {"the", "a", "of", "for", "and", "list", "view", "page", "item", "row"}


def nearest_ui(repo, name):
    ws = set(w.lower() for w in words(name)) - NEAREST_STOP
    best = None
    for cname, comp in repo.ui_names.items():
        score = len(ws & set(w.lower() for w in words(cname)))
        if score and (best is None or score > best[0]):
            best = (score, cname)
    return best[1] if best else None


def build_summary(repo, changes, base):
    changed = dict((rel, st) for st, rel in changes if rel in repo.by_rel)
    rels = set(rel for rel in changed if not repo.by_rel[rel].in_ui)
    findings, _ = run_audit(repo, rels)
    live = [f for f in findings if not f["allowed"]]
    extended, composed, created = [], [], []
    reused = defaultdict(Counter)   # ui module -> {component: uses}
    changed_ui = set(rel for rel in changed if repo.by_rel[rel].in_ui)
    for rel in sorted(changed_ui):
        uf = repo.ui_files.get(rel)
        if not uf:
            continue
        status = {"A": "new", "M": "modified"}.get(changed[rel], "new or modified")
        groups = {}
        for c in uf.comps.values():
            for g, o in c.groups.items():
                groups.setdefault(g, o)
        extended.append({"file": rel, "status": status, "components": uf.components, "variants": groups})
    for rel in sorted(rels):
        fi = repo.by_rel[rel]
        if fi.ext not in JSX_EXT:
            continue
        for tag in fi.tags:
            hit = fi.local_ui.get(tag.name.split(".")[0])
            if hit and hit[0] not in changed_ui and hit[0] in repo.ui_files:
                reused[repo.ui_files[hit[0]].module][hit[1]] += 1
        file_bypass = [f for f in live if f["file"] == rel and f["rule"] == "system.primitive_bypass"]
        for ch in fi.chunks:
            if not ch.component:
                continue
            tags = [t for t in fi.tags if ch.start <= t.start < ch.end]
            if not tags:
                continue
            used = sorted(set(fi.local_ui[t.name.split(".")[0]][1] for t in tags if t.name.split(".")[0] in fi.local_ui))
            raw = [f for f in live if f["file"] == rel and f["component"] == ch.name and f["rule"] in ("system.raw_element", "system.name_shadow")]
            row = {"component": ch.name, "file": rel, "built_from": used, "status": changed[rel]}
            if raw or (file_bypass and not used):
                row["why"] = sorted(set(f["rule"].split(".")[1] for f in raw + file_bypass))
                row["nearest"] = nearest_ui(repo, ch.name)
                created.append(row)
            else:
                row["layout_only"] = not used
                composed.append(row)
    counts = Counter(f["sev"] for f in live)
    reused_out = dict((mod, {"uses": sum(c.values()), "components": [n for n, _ in c.most_common()],
                             "registry": len(repo.ui_files[[r for r, u in repo.ui_files.items() if u.module == mod][0]].components) > 25})
                      for mod, c in reused.items())
    return {"base": base, "changed": len(changed), "outside_ui": len(rels), "reused": reused_out, "extended": extended,
            "composed": composed, "created": created, "findings": dict(counts), "allowed": sum(1 for f in findings if f["allowed"])}


def render_summary(s):
    L = ["composition vs %s: %d changed source files (%d outside ui/)" % (s["base"], s["changed"], s["outside_ui"])]
    parts = []
    for mod, info in sorted(s["reused"].items(), key=lambda kv: -kv[1]["uses"]):
        if info["registry"]:
            parts.append("%s x%d" % (mod, info["uses"]))
        else:
            names = info["components"]
            parts.append("%s x%d (%s%s)" % (mod, info["uses"], ", ".join(names[:3]), ", ..." if len(names) > 3 else ""))
    L.append("Reused    " + (", ".join(parts) or "none"))
    if s["extended"]:
        for i, e in enumerate(s["extended"]):
            vs = "; ".join("%s: %s" % (g, " ".join(o[:6])) for g, o in e["variants"].items())
            L.append("%s%s (%s)%s" % ("Extended  " if i == 0 else "          ", e["file"], e["status"], "  " + vs if vs else ""))
    else:
        L.append("Extended  none")
    built = [c for c in s["composed"] if not c["layout_only"]]
    layout = [c["component"] for c in s["composed"] if c["layout_only"]]
    if built:
        for i, c in enumerate(built[:12]):
            L.append("%s%s (%s) <- %s" % ("Composed  " if i == 0 else "          ", c["component"], c["file"], ", ".join(c["built_from"])))
        if len(built) > 12:
            L.append("          ... %d more (use --json)" % (len(built) - 12))
    else:
        L.append("Composed  none")
    if layout:
        L.append("Layout    %s (no ui/ parts, no raw controls)" % ", ".join(layout[:10]) + (" ..." if len(layout) > 10 else ""))
    if s["created"]:
        for i, c in enumerate(s["created"]):
            L.append("%s%s (%s): %s%s" % ("Created   " if i == 0 else "          ", c["component"], c["file"], ", ".join(c["why"]),
                                           "; nearest `%s`" % c["nearest"] if c["nearest"] else ""))
        L.append("          state for each: what you searched, the nearest component, and why it does not fit")
    else:
        L.append("Created   none")
    f = s["findings"]
    L.append("findings  %s%s" % (", ".join("%s %d" % (k, f[k]) for k in sorted(f)) or "none", " (allowed %d)" % s["allowed"] if s["allowed"] else ""))
    return L


# --------------------------------------------------------------------------- commands

def emit_json(obj):
    print(json.dumps(obj, indent=1, sort_keys=True))


def cmd_inventory(args):
    repo = Repo(args.path, args.ui_dir)
    data = build_inventory(repo)
    if args.json:
        emit_json(data)
    else:
        print("\n".join(render_inventory(data, repo, args.brief)))
    return 0


def collect_files(repo, args):
    if not args.files and not args.stdin:
        return None
    lines = list(args.files or [])
    if args.stdin:
        lines += sys.stdin.read().splitlines()
    return set(rel for _, rel in resolve_names(repo, lines))


def cmd_audit(args):
    repo = Repo(args.path, args.ui_dir)
    try:
        allows = parse_allows(args.allow)
    except ValueError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    files = collect_files(repo, args)
    findings, targets = run_audit(repo, files, args.hints, allows)
    live = [f for f in findings if not f["allowed"]]
    counts = keyed(live)
    base_counts, new_keys = {}, set()
    if args.baseline and os.path.isfile(args.baseline):
        with open(args.baseline) as fh:
            base_counts = json.load(fh).get("counts", {})
        new_keys = set(k for k, n in counts.items() if n > base_counts.get(k, 0))
    elif args.baseline and not (args.write_baseline or args.shrink):
        print("baseline file %s not found; create it with --write-baseline" % args.baseline, file=sys.stderr)
        return 2
    if args.write_baseline:
        if os.path.exists(args.write_baseline) and not args.force:
            print("%s exists; use --shrink to lower it or --force to overwrite" % args.write_baseline, file=sys.stderr)
            return 2
        with open(args.write_baseline, "w") as fh:
            json.dump({"version": 1, "counts": dict(sorted(counts.items()))}, fh, indent=1)
        print("baseline written: %d keys, %d findings" % (len(counts), sum(counts.values())))
        return 0
    shrunk = 0
    if args.shrink and args.baseline:
        merged = dict((k, min(v, counts.get(k, 0))) for k, v in base_counts.items() if counts.get(k, 0) > 0)
        shrunk = sum(base_counts.values()) - sum(merged.values())
        with open(args.baseline, "w") as fh:
            json.dump({"version": 1, "counts": dict(sorted(merged.items()))}, fh, indent=1)
    shown = [f for f in live if not args.baseline or "%s|%s|%s" % (f["rule"], f["file"], f["value"]) in new_keys]
    allowed = [f for f in findings if f["allowed"]]
    summary = Counter(f["sev"] for f in shown)
    if args.json:
        emit_json({"root": repo.app_root, "ui_dir": repo.rel(repo.ui_dir) if repo.ui_dir else None, "files_checked": len(targets),
                   "findings": shown, "allowed": allowed, "summary": dict(summary), "baselined": sum(base_counts.values()),
                   "shrunk": shrunk, "notes": repo.notes})
    else:
        label = "new" if args.baseline else "open"
        print("ui_inventory audit: %s %s (%s)%s  [%d files checked, ui: %s]" % (
            len(shown), label, ", ".join("%s %d" % (k, summary[k]) for k in sorted(summary)) or "clean",
            " allowed %d" % len(allowed) if allowed else "", len(targets), repo.rel(repo.ui_dir) if repo.ui_dir else "NOT FOUND"))
        if args.baseline:
            print("baselined: %d, shrunk this run: %d" % (sum(base_counts.values()), shrunk))
        for note in repo.notes:
            print("note: " + note)
        if not repo.ui_dir:
            print("note: no ui/ folder found, so only suppression findings can be reported; pass --ui-dir or add the shadcn components first")
        per_rule = Counter()
        for f in shown:
            per_rule[f["rule"]] += 1
            if per_rule[f["rule"]] <= args.max:
                print("  %s %s  %s:%d  %s  -> %s" % (f["sev"], f["rule"], f["file"], f["line"], f["message"], f["fix"]))
        for rule, n in sorted(per_rule.items()):
            if n > args.max:
                print("  ... %d more %s findings (raise --max)" % (n - args.max, rule))
        for f in allowed:
            print("  allowed %s  %s:%d  %s  (%s)" % (f["rule"], f["file"], f["line"], f["value"], f["allowed"]))
        if shown:
            print("a real gap: --allow RULE=PATH:reason, or data-ui-allow=\"RULE: reason\" on the element")
    if args.fail_on == "none":
        return 0
    limit = SEV_RANK[args.fail_on]
    return 1 if any(SEV_RANK[f["sev"]] <= limit for f in shown) else 0


def cmd_summary(args):
    if sys.stdin.isatty():
        print("error: summary reads the changed files from stdin, for example: git diff --name-status main | python3 ui_inventory.py summary --base main",
              file=sys.stderr)
        return 2
    repo = Repo(args.path, args.ui_dir)
    s = build_summary(repo, resolve_names(repo, sys.stdin.read().splitlines()), args.base)
    if args.json:
        emit_json(s)
    else:
        print("\n".join(render_summary(s)))
    return 0


# --------------------------------------------------------------------------- selftest

UI = "src/components/ui/"
PAT = "src/components/patterns/"

FIXTURE = {
    "components.json": '{"style": "radix-nova", "iconLibrary": "lucide", "aliases": {"ui": "@/components/ui", "utils": "@/lib/utils"}}',
    "package.json": '{"dependencies": {"@base-ui/react": "^1.0.0", "@hugeicons/react": "^1.0.0", "class-variance-authority": "^0.7.0", '
                    '"react": "^19.0.0", "tailwindcss": "^4.0.0"}}',
    "tsconfig.json": '{\n  // path alias, with a trailing comma on purpose\n  "compilerOptions": { "paths": { "@/*": ["./src/*"], }, },\n}\n',
    "src/lib/utils.ts": 'export function cn(...inputs: unknown[]) {\n  return inputs.filter(Boolean).join(" ")\n}\n',
    UI + "icons.tsx": 'import { HugeiconsIcon } from "@hugeicons/react"\n\nexport function PlusIcon() {\n  return <HugeiconsIcon />\n}\n',
    UI + "button.tsx": '''import * as React from "react"
import { Button as ButtonPrimitive } from "@base-ui/react/button"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"

/* Don't restyle this at the call site; it's the one Button. */
const buttonVariants = cva(
  "inline-flex shrink-0 items-center justify-center rounded-lg border border-transparent font-semibold",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        destructive: "bg-destructive text-white hover:bg-destructive/90",
        outline: "border-border bg-background hover:bg-muted",
        ghost: "hover:bg-muted", // quiet
        link: "text-primary underline-offset-4 hover:underline",
        "brand-outline": "border-border bg-card text-brand",
      },
      size: {
        sm: "h-7 px-3 text-sm",
        md: "h-9 px-3.5 text-sm",
        lg: "h-12 px-8 text-base",
        icon: "size-8",
        circle: "size-8 rounded-full p-0",
      },
    },
    defaultVariants: { variant: "default", size: "md" },
  }
)

function Button({ className, variant = "default", size = "md", ...props }: React.ComponentProps<typeof ButtonPrimitive> & VariantProps<typeof buttonVariants>) {
  return <ButtonPrimitive data-slot="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />
}

export { Button, buttonVariants }
''',
    UI + "card.tsx": '''import * as React from "react"
import { cn } from "@/lib/utils"

function Card({ className, size = "default", ...props }: React.ComponentProps<"div"> & { size?: "default" | "sm" }) {
  return (
    <div
      data-slot="card"
      className={cn("flex flex-col gap-4 rounded-xl border border-border bg-card py-4 text-card-foreground", className)}
      {...props}
    />
  )
}

function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-header" className={cn("grid gap-1 px-4", className)} {...props} />
}

function CardTitle({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-title" className={cn("text-base font-semibold", className)} {...props} />
}

function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="card-content" className={cn("px-4", className)} {...props} />
}

export { Card, CardHeader, CardTitle, CardContent }
''',
    UI + "badge.tsx": '''import { cva } from "class-variance-authority"
import { cn } from "@/lib/utils"

const badgeVariants = cva("inline-flex items-center rounded-full px-2 text-xs", {
  variants: {
    variant: { default: "bg-primary text-primary-foreground", secondary: "bg-secondary text-secondary-foreground", outline: "border text-foreground" },
  },
  compoundVariants: [{ variant: "outline", className: "bg-transparent" }],
  defaultVariants: { variant: "default" },
})

function Badge({ className, variant, ...props }: { className?: string; variant?: "default" | "secondary" | "outline" }) {
  return <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
''',
    UI + "toggle-group.tsx": '''import { ToggleGroup as ToggleGroupPrimitive } from "@base-ui/react/toggle-group"
import { Toggle as TogglePrimitive } from "@base-ui/react/toggle"
import { cn } from "@/lib/utils"

function ToggleGroup({ className, ...props }: React.ComponentProps<typeof ToggleGroupPrimitive>) {
  return <ToggleGroupPrimitive className={cn("inline-flex gap-1", className)} {...props} />
}

function ToggleGroupItem({ className, ...props }: React.ComponentProps<typeof TogglePrimitive>) {
  return <TogglePrimitive className={cn("rounded-md px-2", className)} {...props} />
}

export { ToggleGroup, ToggleGroupItem }
''',
    UI + "popover.tsx": '''import { Popover as PopoverPrimitive } from "@base-ui/react/popover"
import { cn } from "@/lib/utils"

function Popover(props: React.ComponentProps<typeof PopoverPrimitive.Root>) {
  return <PopoverPrimitive.Root {...props} />
}

function PopoverContent({ className, ...props }: React.ComponentProps<typeof PopoverPrimitive.Popup>) {
  return <PopoverPrimitive.Popup className={cn("rounded-lg border bg-popover p-3 shadow-md", className)} {...props} />
}

export { Popover, PopoverContent }
''',
    UI + "input-group.tsx": '''import { cn } from "@/lib/utils"

function InputGroup({ className, ...props }: React.ComponentProps<"div">) {
  return <div role="group" className={cn("flex h-9 items-center", className)} {...props} />
}

export { InputGroup }
''',
    UI + "command.tsx": '''import { Command as CommandPrimitive } from "cmdk"

function Command(props: React.ComponentProps<typeof CommandPrimitive>) {
  return <CommandPrimitive {...props} />
}

export { Command }
''',
    UI + "item.tsx": '''import { useRender } from "@base-ui/react/use-render"
import { cva } from "class-variance-authority"
import { cn } from "@/lib/utils"

const itemVariants = cva("flex items-center gap-3 rounded-md border text-sm", {
  variants: { variant: { default: "border-transparent", outline: "border-border", muted: "border-transparent bg-muted/50" } },
  defaultVariants: { variant: "default" },
})

function Item({ className, variant = "default", render, ...props }: { className?: string; variant?: "default" | "outline" | "muted"; render?: React.ReactElement }) {
  return useRender({ render, props: { className: cn(itemVariants({ variant }), className), ...props } })
}

export { Item }
''',
    UI + "empty.tsx": '''import { cn } from "@/lib/utils"

function Empty({ className, ...props }: React.ComponentProps<"div">) {
  return <div data-slot="empty" className={cn("flex flex-col items-center gap-2 text-center", className)} {...props} />
}

export { Empty }
''',
    UI + "input.tsx": '''import { Input as InputPrimitive } from "@base-ui/react/input"
import { cn } from "@/lib/utils"

function Input({ className, ...props }: React.ComponentProps<typeof InputPrimitive>) {
  return <InputPrimitive className={cn("h-9 rounded-md border px-3", className)} {...props} />
}

export { Input }
''',
    # real code that must produce findings
    PAT + "RawButtonRow.tsx": '''export function RawButtonRow({ onOpen }: { onOpen: () => void }) {
  return (
    <div className="flex items-center gap-2">
      <button type="button" onClick={onOpen} className="rounded px-2 py-1 text-sm hover:bg-muted">Open</button>
    </div>
  )
}
''',
    PAT + "FancyButton.tsx": '''export function FancyButton({ label }: { label: string }) {
  return <button type="button" className="rounded-full bg-primary px-6 text-white">{label}</button>
}
''',
    PAT + "InfoCard.tsx": '''export function InfoCard({ title }: { title: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <h3 className="text-base font-semibold">{title}</h3>
    </div>
  )
}
''',
    PAT + "CommentsToggle.tsx": '''import { ToggleGroup } from "@base-ui/react/toggle-group"

export function CommentsToggle() {
  return <ToggleGroup className="flex" />
}
''',
    PAT + "CountryPicker.tsx": '''import { Combobox } from "@base-ui/react/combobox"

export function CountryPicker() {
  return <Combobox.Root items={["Norway", "Peru"]} />
}
''',
    PAT + "StatPanel.tsx": '''export function StatPanel({ value }: { value: string }) {
  return (
    <section className="rounded-xl border border-border bg-card px-5 py-4">
      <p className="text-sm text-muted-foreground">{value}</p>
    </section>
  )
}
''',
    PAT + "DangerZone.tsx": '''import { Button } from "@/components/ui/button"

export function DangerZone() {
  return <Button className="bg-destructive text-white rounded-full px-6">Delete project</Button>
}
''',
    PAT + "ListA.tsx": '''export function ListA() {
  return <ul><li className="flex items-center gap-3 rounded-lg border px-4 py-3 text-sm">A</li></ul>
}
''',
    PAT + "ListB.tsx": '''export function ListB() {
  return <ul><li className="flex items-center gap-3 rounded-lg border px-4 py-3 text-sm">B</li></ul>
}
''',
    PAT + "ListC.tsx": '''export function ListC() {
  return <ul><li className="text-sm px-4 py-3 border rounded-lg gap-3 items-center flex">C</li></ul>
}
''',
    PAT + "Suppressed.tsx": '''export function Suppressed() {
  // eslint-disable-next-line react/forbid-elements
  const a = <form />
  // eslint-disable-next-line react/forbid-elements -- native file picker needs the bare element
  const b = <form />
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return <div>{a}{b}</div>
}
''',
    PAT + "AllowedRaw.tsx": '''export function AllowedRaw() {
  return (
    <>
      <button type="button" data-ui-allow="raw_element: a canvas control with its own hit testing">Pan</button>
      <button type="button" data-ui-allow="raw_element">Zoom</button>
    </>
  )
}
''',
    # legitimate custom components that must pass
    PAT + "ActionButton.tsx": '''import { Button } from "@/components/ui/button"

export function ActionButton({ label }: { label: string }) {
  return <Button variant="ghost" className="ml-auto w-full">{label}</Button>
}
''',
    PAT + "Sparkline.tsx": '''export function Sparkline({ d }: { d: string }) {
  return <svg viewBox="0 0 100 20" className="h-5 w-24"><path d={d} className="fill-none stroke-primary" /></svg>
}
''',
    PAT + "UploadField.tsx": '''import { Button } from "@/components/ui/button"

export function UploadField() {
  return (
    <label className="inline-flex">
      <input type="file" className="sr-only" />
      <Button variant="outline" size="sm" className="pointer-events-none">Upload</Button>
    </label>
  )
}
''',
    PAT + "TimelineRail.tsx": '''export function TimelineRail({ items }: { items: string[] }) {
  return (
    <ol className="border-l border-border pl-4">
      {items.map((item) => (
        <li key={item} className="relative flex items-center gap-2 py-1 text-sm">
          <span className="size-2 rounded-full bg-primary" />
          {item}
        </li>
      ))}
    </ol>
  )
}
''',
    PAT + "RenderPropRow.tsx": '''import { Item } from "@/components/ui/item"
import { Popover } from "@/components/ui/popover"

export function RenderPropRow() {
  const trigger = { render: (<button type="button" className="underline">More</button>) }
  return (
    <Popover>
      <Item render={<button type="button" className="w-full text-left" />} variant="outline">Row</Item>
      {trigger.render}
    </Popover>
  )
}
''',
    PAT + "LegalNote.tsx": r'''const quotes = /["'`]\/*/g // a regex literal with quotes in it
/* <button> inside a block comment is not a button */
const sample = "<button> inside a string is not a button"

export function LegalNote({ open, n }: { open: boolean; n: number }) {
  const cls = `flex ${open ? "gap-2" : `gap-${n}`} items-center`
  return (
    <p className={cls}>Don't forget: see ftp://files.test/terms <b>now</b></p>
  )
}
''',
    PAT + "MarkdownBits.tsx": '''import Markdown from "react-markdown"

export function MarkdownBits({ text }: { text: string }) {
  return <Markdown components={{ table: ({ children }) => <table className="w-full">{children}</table>, hr: () => <hr /> }}>{text}</Markdown>
}
''',
    PAT + "PickerA.tsx": '''import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Command } from "@/components/ui/command"
import { InputGroup } from "@/components/ui/input-group"
import { Popover, PopoverContent } from "@/components/ui/popover"

export function PickerA() {
  return <Popover><PopoverContent><InputGroup /><Command /><Badge>a</Badge><Button>Done</Button></PopoverContent></Popover>
}
''',
    PAT + "PickerB.tsx": '''import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Command } from "@/components/ui/command"
import { InputGroup } from "@/components/ui/input-group"
import { Popover, PopoverContent } from "@/components/ui/popover"

export function PickerB() {
  return <Popover><PopoverContent><InputGroup /><Command /><Badge>b</Badge><Button>Done</Button></PopoverContent></Popover>
}
''',
    PAT + "PickerC.tsx": '''import { Badge } from "@/components/ui/badge"
import { Command } from "@/components/ui/command"
import { InputGroup } from "@/components/ui/input-group"
import { Popover, PopoverContent } from "@/components/ui/popover"

export function PickerC() {
  return <Popover><PopoverContent><InputGroup /><Command /><Badge>c</Badge></PopoverContent></Popover>
}
''',
    "src/pages/Home.tsx": '''import { Popover } from "@base-ui/react/popover"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Empty } from "@/components/ui/empty"

export function Home() {
  return (
    <Card>
      <CardContent><Popover.Root /><Button>Go</Button></CardContent>
      <Empty className="rounded-lg border border-dashed border-border py-12 text-center text-sm">None yet</Empty>
    </Card>
  )
}
''',
    "src/pages/Inbox.tsx": '''import { Empty } from "@/components/ui/empty"

export function Inbox() {
  return <Empty className="rounded-lg border border-dashed border-border py-12 text-center text-sm">Nothing here</Empty>
}
''',
    "src/pages/Archive.tsx": '''import { Empty } from "@/components/ui/empty"

export function Archive() {
  return <Empty className="rounded-lg border border-dashed border-border py-12 text-center text-sm">Empty archive</Empty>
}
''',
}


class _Tty(object):
    def isatty(self):
        return True

    def read(self):
        return ""


def _run(argv, stdin_text=None):
    out, err = io.StringIO(), io.StringIO()
    old = (sys.stdout, sys.stderr, sys.stdin)
    sys.stdout, sys.stderr = out, err
    if stdin_text is not None:
        sys.stdin = io.StringIO(stdin_text) if isinstance(stdin_text, str) else stdin_text
    try:
        code = main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    finally:
        sys.stdout, sys.stderr, sys.stdin = old
    return code, out.getvalue(), err.getvalue()


def cmd_selftest(_args):
    failures = []

    def expect(cond, label):
        if not cond:
            failures.append(label)

    # ---- the scanner
    snippet = FIXTURE[PAT + "LegalNote.tsx"]
    src = Source(snippet)
    tags, _ = parse_tags(src)
    names = [t.name for t in tags]
    expect(names == ["p", "b"], "scanner: tags after an apostrophe and a URL in JSX text, none in comments or strings: %s" % names)
    expect(attr_literal(src, tags[0], "className") is None and tags[0].attrs["className"][0] == "expr", "scanner: expression className")
    expect(any(snippet[a:b].startswith("flex ") for a, b in src.strings), "scanner: template literal text is a string")
    expect(any(snippet[a:b] == "gap-2" for a, b in src.strings) and any(snippet[a:b] == "gap-" for a, b in src.strings),
           "scanner: strings nested inside a template expression")
    expect("<button>" not in src.struct, "scanner: comment and string contents are blanked in struct")
    js = '''const v = cva("base  classes", {
  variants: {
    tone: {
      // it's quoted: "x"
      "brand-soft": "bg-brand/10 text-brand",
      plain: 'text-foreground',
    },
  },
  compoundVariants: [{ tone: "plain", className: "p-0" }, { tone: "brand-soft", className: "p-1" }],
  defaultVariants: { tone: "plain" },
})
'''
    cv = parse_cvas(Source(js))
    expect(len(cv) == 1 and cv[0]["name"] == "v" and cv[0]["variants"] == {"tone": ["brand-soft", "plain"]}, "cva: quoted keys and an apostrophe in a comment: %s" % cv)
    expect(cv and cv[0]["defaults"] == {"tone": "plain"} and cv[0]["compound"] == 2 and cv[0]["base"] == ["base", "classes"], "cva: defaults, compounds, base")
    expect(cv and cv[0]["tokens"]["tone"]["brand-soft"] == ["bg-brand/10", "text-brand"], "cva: option tokens")
    for tok, want in [("bg-red-500", "color"), ("bg-clip-padding", "other"), ("text-sm", "type"), ("text-left", "other"), ("text-muted-foreground", "color"),
                      ("rounded-full", "shape"), ("px-4", "padding"), ("pointer-events-none", "other"), ("border-border", "color"), ("border-2", "other"),
                      ("hover:bg-muted", "color"), ("ml-auto", "other"), ("font-semibold", "type")]:
        expect(category(base_token(tok)[0]) == want, "category of %s is %s" % (tok, want))

    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in FIXTURE.items():
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                fh.write(text)
        repo = Repo(tmp)
        data = build_inventory(repo)
        by_module = dict((u["module"], u) for u in data["ui"])

        # ---- inventory
        expect(data["ui_dir"] == "src/components/ui", "inventory finds ui/ from the components.json alias: %s" % data["ui_dir"])
        btn = by_module.get("button", {})
        expect(btn.get("groups", {}).get("Button", {}).get("variant", {}).get("options") == ["default", "destructive", "outline", "ghost", "link", "brand-outline"],
               "inventory: button variants including a quoted key")
        expect(btn.get("groups", {}).get("Button", {}).get("size", {}).get("default") == "md", "inventory: button default size")
        expect(btn.get("import") == 'import { Button } from "@/components/ui/button"' or btn.get("import", "").endswith('"@/components/ui/button"'),
               "inventory: import line %s" % btn.get("import"))
        expect(by_module.get("card", {}).get("compounds") == {"Card": ["Content", "Header", "Title"]}, "inventory: Card compounds %s" % by_module.get("card", {}).get("compounds"))
        expect(by_module.get("card", {}).get("groups", {}).get("Card", {}).get("size", {}).get("options") == ["default", "sm"], "inventory: Card size union")
        expect(by_module.get("badge", {}).get("cva", [{}])[0].get("compound_variants") == 1, "inventory: badge compound variants")
        expect(by_module.get("toggle-group", {}).get("wraps") == ["@base-ui/react/toggle-group", "@base-ui/react/toggle"], "inventory: wrapped modules")
        expect(by_module.get("button", {}).get("provenance") == "shadcn" and by_module.get("empty", {}).get("provenance") == "shadcn", "inventory: provenance by name")
        expect("combobox" not in by_module, "inventory: combobox is missing from ui/")
        stack = data["stack"]
        expect(stack["primitives"].get("base-ui", 0) >= 4 and "radix" not in stack["primitives"], "stack: Base UI from imports")
        expect(stack["icons"].get("hugeicons", 0) >= 1 and stack["icon_registry"].get("hugeicons") == [UI + "icons.tsx"], "stack: HugeIcons through the icon registry")
        expect(any("radix-nova" in w and "Base UI" in w for w in data["warnings"]), "warning: components.json style disagrees with imports")
        expect(any("lucide" in w and "HugeIcons" in w for w in data["warnings"]), "warning: components.json iconLibrary disagrees with imports")
        code, out, _ = _run(["inventory", tmp, "--brief"])
        expect(code == 0 and len(out.splitlines()) < 60 and "variant[" in out and "Trust the imports" in out, "inventory --brief is short and carries the warnings")
        code, out, _ = _run(["inventory", tmp, "--json"])
        expect(code == 0 and json.loads(out)["ui"], "inventory --json parses")

        # ---- audit
        findings, targets = run_audit(repo, None)
        live = [f for f in findings if not f["allowed"]]

        def hits(rule, name):
            return [f for f in live if f["rule"] == rule and (f["file"] == name or f["file"].endswith("/" + name + ".tsx"))]
        raw = hits("system.raw_element", "RawButtonRow")
        expect(len(raw) == 1 and "`Button`" in raw[0]["fix"] and "variant `ghost`" in raw[0]["fix"] and "@/components/ui/button" in raw[0]["fix"],
               "raw button names Button and variant ghost: %s" % [f["fix"] for f in raw])
        fancy = hits("system.name_shadow", "FancyButton")
        expect(len(fancy) == 1 and "`Button`" in fancy[0]["fix"], "FancyButton is a name shadow")
        expect(not hits("system.raw_element", "FancyButton"), "the raw button inside a name shadow is not reported twice")
        info = hits("system.name_shadow", "InfoCard")
        expect(len(info) == 1 and "`Card`" in info[0]["fix"] and not hits("system.skin_copy", "InfoCard"), "a hand-built InfoCard is one name shadow")
        tog = hits("system.primitive_bypass", "CommentsToggle")
        expect(len(tog) == 1 and "`ToggleGroup`" in tog[0]["fix"] and "@/components/ui/toggle-group" in tog[0]["fix"], "primitive bypass names the wrapper")
        pick = hits("system.primitive_bypass", "CountryPicker")
        expect(len(pick) == 1 and "shadcn@latest add combobox" in pick[0]["fix"], "missing combobox: the fix says how to add it: %s" % [f["fix"] for f in pick])
        home = hits("system.primitive_bypass", "src/pages/Home.tsx")
        expect(len(home) == 1 and "`Popover`" in home[0]["fix"], "a Base UI import in a page names the wrapper")
        panel = hits("system.skin_copy", "StatPanel")
        expect(len(panel) == 1 and "`Card`" in panel[0]["fix"], "hand-built panel repeats the Card skin")
        danger = hits("system.variant_restated", "DangerZone")
        expect(any("variant `destructive`" in f["fix"] for f in danger), "restated color names variant destructive: %s" % [f["fix"] for f in danger])
        rep = [f for f in live if f["rule"] == "system.repeated_classes"]
        expect(sorted(set(f["file"].split("/")[-1] for f in rep if "List" in f["file"])) == ["ListA.tsx", "ListB.tsx", "ListC.tsx"],
               "repeated class set found in three files even when the token order differs")
        empties = [f for f in rep if "Empty" in f["message"]]
        expect(len(empties) == 3 and "empty.tsx" in empties[0]["fix"], "the same className on Empty three times asks for a variant in ui/empty.tsx")
        near = [f for f in live if f["rule"] == "system.near_duplicate"]
        expect(sorted(os.path.basename(f["file"]) for f in near) == ["PickerA.tsx", "PickerB.tsx", "PickerC.tsx"], "near-duplicate cluster of three pickers")
        sup = [f for f in live if f["rule"] == "system.suppression"]
        expect(len(sup) == 1 and sup[0]["line"] == 2 and "react/forbid-elements" in sup[0]["message"], "eslint-disable without a reason is reported once: %s" % sup)
        allowed = [f for f in findings if f["allowed"]]
        expect(len(allowed) == 2 and any("hit testing" in f["allowed"] for f in allowed), "data-ui-allow with a reason demotes the finding")
        expect(len([f for f in live if f["rule"] == "system.allow_reason"]) == 1, "data-ui-allow without a reason is its own finding")
        for clean in ("ActionButton", "Sparkline", "UploadField", "TimelineRail", "RenderPropRow", "LegalNote", "MarkdownBits"):
            bad = [f for f in live if f["file"].endswith("/" + clean + ".tsx")]
            expect(not bad, "legitimate component %s passes: %s" % (clean, [(f["rule"], f["message"]) for f in bad]))
        expect(not [f for f in live if f["file"].startswith(UI)], "ui/ is never audited")

        # ---- command line
        code, out, _ = _run(["audit", tmp])
        expect(code == 1 and "system.raw_element" in out and "a real gap: --allow" in out, "audit exits 1 on P1 findings")
        code, out, _ = _run(["audit", tmp, "--fail-on", "none"])
        expect(code == 0, "audit --fail-on none exits 0")
        code, out, _ = _run(["audit", tmp, "--files", PAT + "ActionButton.tsx", PAT + "Sparkline.tsx"])
        expect(code == 0 and "clean" in out, "audit --files on clean files exits 0: %s" % out)
        code, out, _ = _run(["audit", tmp, "--stdin"], "M\t%sRawButtonRow.tsx\nD\t%sGone.tsx\n" % (PAT, PAT))
        expect(code == 1 and "RawButtonRow" in out and "FancyButton" not in out, "audit --stdin reads name-status lines and skips deletions")
        code, out, _ = _run(["audit", tmp, "--stdin", "--json"], "%sRawButtonRow.tsx\n" % PAT)
        parsed = json.loads(out)
        expect(code == 1 and parsed["summary"].get("P1") == 1 and parsed["files_checked"] == 1, "audit --json carries the summary")
        code, out, _ = _run(["audit", tmp, "--files", PAT + "RawButtonRow.tsx", "--allow", "system.raw_element=RawButtonRow:native control with its own hit testing"])
        expect(code == 0 and "allowed" in out, "--allow with a reason turns a finding into an allowed one: %s" % out)
        code, out, err = _run(["audit", tmp, "--allow", "system.raw_element=RawButtonRow"])
        expect(code == 2 and "reason" in err, "--allow without a reason is refused")
        base_file = os.path.join(tmp, "baseline.json")
        code, out, _ = _run(["audit", tmp, "--write-baseline", base_file])
        expect(code == 0 and os.path.isfile(base_file), "write-baseline exits 0")
        code, out, _ = _run(["audit", tmp, "--baseline", base_file])
        expect(code == 0, "audit within the baseline exits 0: %s" % out[:200])
        with open(os.path.join(tmp, PAT + "NewRaw.tsx"), "w") as fh:
            fh.write('export function NewRaw() {\n  return <input type="text" className="border" />\n}\n')
        code, out, _ = _run(["audit", tmp, "--baseline", base_file])
        expect(code == 1 and "NewRaw" in out and "`Input`" in out, "a new raw input beyond the baseline exits 1 and names Input: %s" % out[:300])

        # ---- adding the missing wrapper changes the advice
        with open(os.path.join(tmp, UI + "combobox.tsx"), "w") as fh:
            fh.write('import { Combobox as ComboboxPrimitive } from "@base-ui/react/combobox"\n\nfunction Combobox(props: React.ComponentProps<typeof ComboboxPrimitive.Root>) {\n'
                     '  return <ComboboxPrimitive.Root {...props} />\n}\n\nexport { Combobox }\n')
        repo2 = Repo(tmp)
        f2, _ = run_audit(repo2, set([PAT + "CountryPicker.tsx"]))
        expect(len(f2) == 1 and "`Combobox`" in f2[0]["fix"] and "@/components/ui/combobox" in f2[0]["fix"], "after ui/combobox exists the bypass names it: %s" % [f["fix"] for f in f2])
        with open(os.path.join(tmp, PAT + "CountryPicker.tsx"), "w") as fh:
            fh.write('import { Combobox } from "@/components/ui/combobox"\nimport { Button } from "@/components/ui/button"\n\n'
                     'export function CountryPicker() {\n  return <Combobox items={["Norway"]}><Button>Pick</Button></Combobox>\n}\n')

        # ---- summary
        names = "A\t%scombobox.tsx\nA\t%sCountryPicker.tsx\nA\t%sFancyButton.tsx\nM\tsrc/pages/Home.tsx\n" % (UI, PAT, PAT)
        code, out, _ = _run(["summary", tmp, "--base", "main"], names)
        expect(code == 0 and out.splitlines()[0].startswith("composition vs main"), "summary prints the table")
        s = build_summary(Repo(tmp), resolve_names(Repo(tmp), names.splitlines()), "main")
        expect([e["file"] for e in s["extended"]] == [UI + "combobox.tsx"] and s["extended"][0]["status"] == "new", "summary: a new ui/ file is Extended")
        expect(any(c["component"] == "CountryPicker" and c["built_from"] == ["Button", "Combobox"] for c in s["composed"]), "summary: CountryPicker is Composed from ui/ parts: %s" % s["composed"])
        expect([c["component"] for c in s["created"]] == ["FancyButton"] and s["created"][0]["nearest"] == "Button", "summary: FancyButton is Created with its nearest component: %s" % s["created"])
        expect(s["reused"].get("button", {}).get("uses", 0) >= 2 and s["reused"].get("card", {}).get("uses") == 2, "summary: Reused counts ui/ parts that did not change: %s" % s["reused"])
        code, out, err = _run(["summary", tmp], _Tty())
        expect(code == 2 and "stdin" in err, "summary refuses to wait on a terminal")

    # ---- a repo with no ui/ folder
    with tempfile.TemporaryDirectory() as bare:
        os.makedirs(os.path.join(bare, "src"))
        with open(os.path.join(bare, "src", "App.tsx"), "w") as fh:
            fh.write("export function App() {\n  return <button>x</button>\n}\n")
        code, out, _ = _run(["audit", bare])
        expect(code == 0 and "NOT FOUND" in out, "no ui/ folder: audit says so and does not guess")
    for label in failures:
        print("FAIL " + label)
    print("selftest %s (%d failure%s)" % ("FAILED" if failures else "OK", len(failures), "" if len(failures) == 1 else "s"))
    return 1 if failures else 0


# --------------------------------------------------------------------------- command line

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("path", nargs="?", default=".", help="the app folder, or a repo that holds it (default .)")
        p.add_argument("--ui-dir", help="the ui/ folder when it is not the components.json alias or a usual place")
        p.add_argument("--json", action="store_true", help="print JSON instead of text")

    i = sub.add_parser("inventory", help="stack, ui/ components, variants, and the import line to use")
    common(i)
    i.add_argument("--brief", action="store_true", help="under 60 lines, for pasting into context")
    a = sub.add_parser("audit", help="composition findings for the changed files, each naming the component and variant to use")
    common(a)
    a.add_argument("--files", nargs="*", help="check only these files or folders (default: every source file outside ui/)")
    a.add_argument("--stdin", action="store_true", help="read the files to check from stdin, one per line (git diff --name-only works)")
    a.add_argument("--baseline", help="JSON baseline; only findings beyond it are reported")
    a.add_argument("--write-baseline", help="write today's findings to this file and exit")
    a.add_argument("--shrink", action="store_true", help="lower the baseline to today's counts (never raises it)")
    a.add_argument("--force", action="store_true")
    a.add_argument("--fail-on", choices=["P1", "P2", "P3", "none"], default="P1", help="lowest severity that exits 1 (default P1)")
    a.add_argument("--allow", action="append", help="RULE=PATH:reason for a real gap, for example system.raw_element=src/Upload.tsx:native file input")
    a.add_argument("--hints", action="store_true", help="also report raw <a> and <img> as P3 hints")
    a.add_argument("--max", type=int, default=25, help="findings printed per rule")
    s = sub.add_parser("summary", help="Reused, Extended, Composed, Created table; changed files come from stdin")
    common(s)
    s.add_argument("--base", default="base", help="a label for the table, usually the ref the diff was taken against (for example main)")
    s.add_argument("--stdin", action="store_true", help="accepted for symmetry with audit; summary always reads the changed files from stdin")
    sub.add_parser("selftest", help="run the scanner and every rule against a built-in fixture app")
    args = ap.parse_args(argv)
    try:
        return {"inventory": cmd_inventory, "audit": cmd_audit, "summary": cmd_summary, "selftest": cmd_selftest}[args.cmd](args)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
