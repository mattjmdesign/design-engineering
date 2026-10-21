#!/usr/bin/env python3
"""token_scan.py: find design-token drift in source and check token contrast per theme.

Commands:
  scan PATH       raw colors, off-scale values, arbitrary Tailwind values, undefined var() names
  contrast PATH   WCAG contrast of token pairs in every theme found in the CSS
  inventory PATH  tokens by group with usage counts, unused tokens, duplicates
  adopt PATH      for each literal color, spacing, radius, or type value, propose one fix: the nearest token, a
                  retarget of the token itself, a new role, or a one-off (--files F... limits the proposals)
  selftest        run the scanner against built-in samples

Trust boundary: reads files under PATH and writes only the baseline file named with
--write-baseline or --shrink. No network access and no subprocesses.
"""
import argparse
import colorsys
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict

SKIP_DIRS = {"node_modules", "dist", "build", "out", "target", "coverage", "vendor", "venv", "__pycache__",
             "storybook-static"}
CSS_EXT = {".css", ".scss", ".sass", ".less", ".pcss", ".postcss"}
MARKUP_EXT = {".html", ".htm", ".vue", ".svelte", ".astro"}
CODE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mdx"}
MAX_BYTES = 1000000
TEST_PATH = re.compile(r"(\.(test|spec)\.[a-z]+$)|(/__(tests|mocks|snapshots)__/)|(/fixtures?/)|(\.d\.ts$)")
TOKEN_FILE = re.compile(r"(^|/)(tailwind\.config\.[a-z]+|theme\.(ts|js|mjs)|tokens?\.(ts|js|mjs|json))$")
ALLOW_VAR = [r"^--(tw|radix|headlessui|base-ui|vaul|sonner|cmdk|embla|swiper)-",
             r"^--(available|anchor|positioner|transform-origin|accordion-panel|collapsible-panel)(-|$)"]
TAILWIND_THEME_VAR = r"^--(spacing|color-(red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|slate|gray|zinc|neutral|stone)-\d+|color-(black|white))$"

HEX = re.compile(r"(?<![\w&#/-])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})\b(?![\w-])")
FUNC = re.compile(r"\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\([^)]*\)", re.I)
VAR_REF = re.compile(r"var\(\s*(--[\w-]+)\s*(,)?")
LENGTH = re.compile(r"^(-?\d*\.?\d+)(px|rem)$")
WRAPPER = re.compile(r"^@(media|layer|supports|container|scope)\b", re.I)
ROOTISH = re.compile(r"(^|[\s,>+~])(:root|html|body|:host|\*)(?=$|[\s,:.\[>+~)])"
                     r"|\[data-[\w-]*(theme|mode|scheme)[^\]]*\]"
                     r"|(^|[\s,])\.(dark|light|theme-[\w-]+)(?=$|[\s,:.\[>+~])|^@(theme|property)\b", re.I)
SPACING_PROP = re.compile(r"^(padding|margin|gap|row-gap|column-gap|grid-gap|inset|scroll-margin|scroll-padding)(-|$)")
RADIUS_PROP = re.compile(r"^border(-[a-z]+){0,2}-radius$")
TW_ARBITRARY = re.compile(r"(?<![\w\[])([a-z][a-z0-9]*(?:-[a-z0-9]+)*)-\[([^\]\s]+)\]")
TW_SPACING = re.compile(r"^(-?)(p|px|py|pt|pr|pb|pl|ps|pe|m|mx|my|mt|mr|mb|ml|ms|me|gap|gap-x|gap-y|space-x|space-y|inset|inset-x|inset-y)$")
TW_COLOR = re.compile(r"^(bg|text|border|ring|outline|fill|stroke|from|via|to|shadow|accent|caret|decoration|divide|placeholder)(-[a-z]+)?$")
JS_SPACING = re.compile(r"\b(padding|margin|gap|rowGap|columnGap)(Top|Right|Bottom|Left|Inline|Block|InlineStart|InlineEnd|BlockStart|BlockEnd)?\s*:\s*(-?\d+(?:\.\d+)?)\s*(?=[,}\s])")
INLINE_DECL = re.compile(r"(?<![\w-])((?:padding|margin|gap|row-gap|column-gap|font-size|border-radius)[a-z-]*)\s*:\s*([^;\"'`}{]+)")


def read_text(path):
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def walk(root):
    root = os.path.abspath(root)
    if os.path.isfile(root):
        yield root
        return
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and (not d.startswith(".") or d == ".storybook"))
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() in CSS_EXT | MARKUP_EXT | CODE_EXT:
                yield os.path.join(base, name)


def mask_comments(text, ext):
    """Blank comments but keep newlines so line numbers stay true."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    if ext not in (".css", ".pcss", ".postcss"):
        text = re.sub(r"(?<![:\"'`])//[^\n]*", blank, text)
    if ext in MARKUP_EXT or ext == ".mdx":
        text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    return text


def one_decl(decl, line, stack):
    if decl.startswith("@") or ":" not in decl:
        return
    prop, value = decl.split(":", 1)
    prop = prop.strip()
    if not prop.startswith("--"):
        prop = prop.lower()
    if re.fullmatch(r"-{0,2}[\w-]+", prop):
        yield (line, prop, re.sub(r"\s+", " ", value.replace("!important", "")).strip(), tuple(stack))


def css_decls(text):
    """Yield (line, prop, value, frames) for each declaration; frames are the enclosing block preludes."""
    stack, buf, line, buf_line, i, n = [], [], 1, 1, 0, len(text)
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
            stack.append("".join(buf).strip())
            buf, buf_line = [], line
        elif ch in "};":
            decl = "".join(buf).strip()
            if decl:
                for item in one_decl(decl, buf_line, stack):
                    yield item
            if ch == "}" and stack:
                stack.pop()
            buf, buf_line = [], line
        else:
            if ch == "\n":
                line += 1
            elif not buf and not ch.isspace():
                buf_line = line
            buf.append(ch)
        i += 1


def in_token_root(frames):
    for frame in reversed(frames):
        if WRAPPER.match(frame):
            continue
        return bool(ROOTISH.search(frame))
    return True


def theme_of(frames):
    joined = " ".join(frames).lower()
    if re.search(r"prefers-color-scheme\s*:\s*dark", joined):
        return "dark"
    plain = re.sub(r":not\([^)]*\)", "", joined)
    m = re.search(r"data-[\w-]*(?:theme|mode|scheme)\s*[~|^$*]?=\s*[\"']?([\w-]+)", plain)
    if m:
        return m.group(1)
    if re.search(r"\.dark\b", plain):
        return "dark"
    m = re.search(r"\.theme-([\w-]+)", plain)
    return m.group(1) if m else "light"


def _lin(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _enc(x):
    x = max(0.0, min(1.0, x))
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def _alpha(text):
    if text is None:
        return 1.0
    text = text.strip()
    return min(1.0, float(text[:-1]) / 100) if text.endswith("%") else min(1.0, float(text))


def parse_color(value):
    """Return (r, g, b, a) with channels 0..1, or None when the syntax is not understood."""
    v = value.strip().lower()
    if v in ("white", "#fff"):
        return (1.0, 1.0, 1.0, 1.0)
    if v == "black":
        return (0.0, 0.0, 0.0, 1.0)
    if v == "transparent":
        return (0.0, 0.0, 0.0, 0.0)
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


def luminance(rgb):
    r, g, b = (_lin(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    hi, lo = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def over(fg, bg):
    a = fg[3]
    return tuple(fg[i] * a + bg[i] * (1 - a) for i in range(3)) + (1.0,)


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in rgb[:3])


def resolve(name, values, depth=0):
    """Resolve a custom property to a literal through var() chains."""
    if depth > 10 or name not in values:
        return None
    value = values[name]
    m = re.fullmatch(r"var\(\s*(--[\w-]+)\s*(?:,\s*(.+))?\)", value.strip())
    if not m:
        return value
    inner = resolve(m.group(1), values, depth + 1)
    return inner if inner is not None else m.group(2)


class Project(object):
    def __init__(self, root, grid=4, spacing_policy="grid", token_files=(), ignore=(), only_tokens=()):
        self.root = os.path.abspath(root)
        self.base = self.root if os.path.isdir(self.root) else os.path.dirname(self.root)
        self.grid, self.policy = grid, spacing_policy
        self.token_files = list(token_files)
        self.ignore = list(ignore)
        self.only_tokens = list(only_tokens)
        self.tailwind = False
        self.tokens = defaultdict(dict)   # theme -> name -> value
        self.file_tokens = defaultdict(dict)  # rel -> {(theme, name): (value, line)}
        self.where = {}                   # (theme, name) -> (rel, line)
        self.theme_block = set()          # names defined inside @theme (used through utilities)
        self.defined, self.refs = set(), Counter()
        self.files = []                   # (rel, ext, masked text, is_token_file)
        self.decls = []                   # (rel, line, prop, value, frames, is_token_file)
        self.load()

    def rel(self, path):
        return os.path.relpath(path, self.base).replace(os.sep, "/")

    def load(self):
        for path in walk(self.root):
            rel = self.rel(path)
            if TEST_PATH.search("/" + rel) or any(frag in rel for frag in self.ignore):
                continue
            text = read_text(path)
            if text is None:
                continue
            ext = os.path.splitext(path)[1].lower()
            masked = mask_comments(text, ext)
            if ext in CSS_EXT and re.search(r"@import\s+[\"']tailwindcss|@tailwind\s", masked):
                self.tailwind = True
            is_token_file = bool(TOKEN_FILE.search(rel)) or any(t in rel for t in self.token_files)
            self.files.append((rel, ext, masked, is_token_file))
            blocks = []
            if ext in CSS_EXT:
                blocks.append((0, masked))
            elif ext in MARKUP_EXT:
                for m in re.finditer(r"<style[^>]*>(.*?)</style>", masked, re.S | re.I):
                    blocks.append((masked[:m.start(1)].count("\n"), m.group(1)))
            for offset, block in blocks:
                for line, prop, value, frames in css_decls(block):
                    self.decls.append((rel, line + offset, prop, value, frames, is_token_file))
                    if prop.startswith("--"):
                        self.defined.add(prop)
                        if in_token_root(frames):
                            theme = theme_of(frames)
                            self.file_tokens[rel][(theme, prop)] = (value, line + offset)
                            if any(re.match(r"^@theme\b", f, re.I) for f in frames):
                                self.theme_block.add(prop)
            for m in re.finditer(r"@property\s+(--[\w-]+)", masked):
                self.defined.add(m.group(1))
            for m in re.finditer(r"""['"`](--[\w-]+)['"`]\s*:|setProperty\(\s*['"`](--[\w-]+)""", masked):
                self.defined.add(m.group(1) or m.group(2))
            for m in VAR_REF.finditer(masked):
                self.refs[m.group(1)] += 1
        # The file that defines the most tokens is the system's source of truth, so it wins any conflict.
        for rel in sorted(self.file_tokens, key=lambda r: len(self.file_tokens[r])):
            if self.only_tokens and not any(f in rel for f in self.only_tokens):
                continue
            for (theme, name), (value, line) in self.file_tokens[rel].items():
                self.tokens[theme][name] = value
                self.where[(theme, name)] = (rel, line)
        self.finish_scales()

    def finish_scales(self):
        base = self.tokens.get("light", {})
        names = dict(base)
        for theme in self.tokens:
            names.update(self.tokens[theme])
        self.spacing, self.radius, self.type = {}, {}, {}
        for name, value in names.items():
            m = LENGTH.match(value.strip())
            if not m:
                continue
            px = float(m.group(1)) * (16 if m.group(2) == "rem" else 1)
            low = name.lower()
            if re.search(r"radius|rounded", low):
                self.radius[name] = px
            elif re.search(r"font-size|(^|-)text(-|$)|(^|-)fs(-|$)", low):
                self.type[name] = px
            elif re.search(r"space|spacing|gap|pad|margin|inset|gutter|(^|-)size(-|$)", low):
                self.spacing[name] = px
        self.colors = {}
        for theme in self.tokens:
            for name in self.tokens[theme]:
                lit = resolve(name, {**self.tokens.get("light", {}), **self.tokens[theme]})
                col = parse_color(lit) if lit else None
                if col and col[3] == 1.0:
                    names = self.colors.setdefault(to_hex(col), [])
                    if name not in names:
                        names.append(name)

    def spacing_ok(self, px):
        px = abs(px)
        if px in (0, 1, 2) or self.policy == "off":
            return True
        if px in self.spacing.values():
            return True
        return self.policy == "grid" and px % self.grid == 0

    def nearest(self, px):
        lo = (px // self.grid) * self.grid
        options = sorted({lo, lo + self.grid} | set(v for v in self.spacing.values() if abs(v - px) <= self.grid * 2))
        options.sort(key=lambda v: abs(v - px))
        text = " or ".join("%gpx" % v for v in options[:2])
        for name, v in self.spacing.items():
            if v == options[0]:
                return "%s (var(%s))" % (text, name)
        return text

    def nearest_color(self, col):
        """(names, distance, hex) of the token color closest to col; distance is 0 for an exact match. (None, None, None) with no tokens."""
        names = self.colors.get(to_hex(col))
        if names:
            return sorted(names), 0.0, to_hex(col)
        best, dist, best_hex = None, 1e9, None
        for hx, ns in self.colors.items():
            c2 = parse_color(hx)
            d = sum((col[i] - c2[i]) ** 2 for i in range(3)) ** 0.5 * 255
            if d < dist:
                best, dist, best_hex = ns, d, hx
        return (sorted(best), dist, best_hex) if best else (None, None, None)

    def color_hint(self, col):
        names, dist, _ = self.nearest_color(col)
        if names and dist == 0:
            return "use " + " or ".join("var(%s)" % n for n in names[:3])
        return "close to var(%s)" % names[0] if names and dist <= 24 else ""

    def nearest_token(self, scale, px, tol):
        """Tokens of one scale (name -> px) within tol px of px, nearest first; ties go to the smaller value."""
        items = sorted(scale.items(), key=lambda kv: (abs(kv[1] - px), kv[1], kv[0]))
        return [(n, v) for n, v in items if abs(v - px) <= tol]


def strip_vars(value):
    """Remove var(...) groups, fallbacks included, so a fallback literal is not read as drift."""
    out, i = [], 0
    while i < len(value):
        if value.startswith("var(", i):
            depth, j = 1, i + 4
            while j < len(value) and depth:
                depth += (value[j] == "(") - (value[j] == ")")
                j += 1
            out.append(" ")
            i = j
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def strip_funcs(value):
    """Remove calc(), var(), min(), max(), and clamp() groups, whatever is inside them, so a length that sits in a derived value
    or a fallback is not read as a literal. A radius derived from a container, such as calc(var(--radius-outer) - var(--inset)), is
    the fix this scanner recommends, not drift."""
    out, i = [], 0
    while i < len(value):
        m = re.compile(r"(?:calc|var|min|max|clamp)\(", re.I).match(value, i)
        if m:
            depth, j = 1, m.end()
            while j < len(value) and depth:
                depth += (value[j] == "(") - (value[j] == ")")
                j += 1
            out.append(" ")
            i = j
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


RADIUS_NOTE = ("radius is not in the radius scale; a value derived from a container may use "
               "calc(var(--radius-outer) - var(--inset)) instead of a new literal")


def strip_alpha_neutrals(col):
    return col[3] < 1.0 and (col[:3] == (0.0, 0.0, 0.0) or col[:3] == (1.0, 1.0, 1.0))


def iter_colors(value, strict_alpha=False):
    """Yield (text, rgba) for each color literal in a value, skipping var() groups, link targets, and black or white with alpha."""
    value = strip_vars(value)
    for m in list(HEX.finditer(value)) + list(FUNC.finditer(value)):
        col = parse_color(m.group(0))
        if col is None or (strip_alpha_neutrals(col) and not strict_alpha):
            continue
        if value[max(0, m.start() - 6):m.start()].lower().endswith(("href=\"", "href='", "url(")):
            continue
        yield m.group(0), col


def scan(project, allow_vars, strict_alpha=False):
    out, seen = [], set()

    def add(rule, rel, line, value, message, hint=""):
        key = (rule, rel, line, value)
        if key not in seen:
            seen.add(key)
            out.append({"rule": rule, "file": rel, "line": line, "value": value, "message": message, "hint": hint})

    def colors_in(value):
        return iter_colors(value, strict_alpha)

    def check_value(rel, line, prop, value):
        if SPACING_PROP.match(prop) and not re.search(r"\b(calc|clamp|min|max|var)\(", value):
            for part in value.split():
                m = LENGTH.match(part)
                if m:
                    px = float(m.group(1)) * (16 if m.group(2) == "rem" else 1)
                    if not project.spacing_ok(px):
                        add("off-scale", rel, line, "%s: %s" % (prop, part), "spacing %s is off the %dpx grid" % (part, project.grid), "use " + project.nearest(abs(px)))
        elif prop == "font-size" and len(project.type) >= 3:
            m = LENGTH.match(value.strip())
            if m:
                px = float(m.group(1)) * (16 if m.group(2) == "rem" else 1)
                if px not in project.type.values():
                    add("off-scale", rel, line, "font-size: %s" % value, "font size is not in the type scale", "tokens: " + ", ".join(sorted(project.type)[:6]))
        elif RADIUS_PROP.match(prop) and len(project.radius) >= 2:
            for part in strip_funcs(value).split():
                m = LENGTH.match(part)
                if m:
                    px = float(m.group(1)) * (16 if m.group(2) == "rem" else 1)
                    if px not in project.radius.values() and px not in (0, 9999, 999):
                        add("off-scale", rel, line, "%s: %s" % (prop, part), RADIUS_NOTE, "tokens: " + ", ".join(sorted(project.radius)[:6]))

    for rel, line, prop, value, frames, is_token_file in project.decls:
        is_def = prop.startswith("--") and in_token_root(frames)
        if not is_def and not is_token_file:
            for lit, col in colors_in(value):
                add("raw-color", rel, line, lit.lower(), "raw color in %s" % prop, project.color_hint(col))
            check_value(rel, line, prop, value)
    for rel, ext, text, is_token_file in project.files:
        lines = text.split("\n")
        for no, raw in enumerate(lines, 1):
            for m in VAR_REF.finditer(raw):
                name, has_fallback = m.group(1), bool(m.group(2))
                if not has_fallback and name not in project.defined and not any(re.search(p, name) for p in allow_vars):
                    add("undefined-var", rel, no, name, "var(%s) has no definition and no fallback" % name)
            if ext in CSS_EXT or is_token_file:
                continue
            if ext in CODE_EXT | MARKUP_EXT:
                if not (ext in MARKUP_EXT and no and "<style" in raw):
                    for lit, col in colors_in(raw):
                        add("raw-color", rel, no, lit.lower(), "raw color in source", project.color_hint(col))
                for m in TW_ARBITRARY.finditer(raw):
                    util, arg = m.group(1), m.group(2)
                    if arg.startswith("var(") or arg.startswith("--"):
                        continue
                    if TW_COLOR.match(util) and (HEX.search(arg) or FUNC.search(arg)):
                        continue
                    sm, lm = TW_SPACING.match(util), LENGTH.match(arg)
                    if sm and lm:
                        px = float(lm.group(1)) * (16 if lm.group(2) == "rem" else 1)
                        if not project.spacing_ok(px):
                            add("off-scale", rel, no, "%s-[%s]" % (util, arg), "arbitrary spacing is off the %dpx grid" % project.grid, "use " + project.nearest(abs(px)))
                    elif util == "text" and lm and len(project.type) >= 3:
                        px = float(lm.group(1)) * (16 if lm.group(2) == "rem" else 1)
                        if px not in project.type.values():
                            add("off-scale", rel, no, "text-[%s]" % arg, "arbitrary font size is not in the type scale")
                    elif re.match(r"^rounded(-[a-z]{1,2})?$", util) and lm and len(project.radius) >= 2:
                        px = float(lm.group(1)) * (16 if lm.group(2) == "rem" else 1)
                        if px not in project.radius.values():
                            add("off-scale", rel, no, "%s-[%s]" % (util, arg), "arbitrary " + RADIUS_NOTE)
                for m in JS_SPACING.finditer(raw):
                    px = float(m.group(3))
                    if not project.spacing_ok(px):
                        add("off-scale", rel, no, "%s%s: %s" % (m.group(1), m.group(2) or "", m.group(3)), "inline spacing is off the %dpx grid" % project.grid, "use " + project.nearest(abs(px)))
                for m in INLINE_DECL.finditer(raw):
                    check_value(rel, no, m.group(1).lower(), m.group(2).strip())
    # a Tailwind arbitrary color is a raw color too
    for rel, ext, text, is_token_file in project.files:
        if ext in CSS_EXT or is_token_file:
            continue
        for no, raw in enumerate(text.split("\n"), 1):
            for m in TW_ARBITRARY.finditer(raw):
                if TW_COLOR.match(m.group(1)) and (HEX.search(m.group(2)) or FUNC.search(m.group(2))):
                    col = parse_color(m.group(2).replace("_", " "))
                    if col and not (strip_alpha_neutrals(col) and not strict_alpha):
                        add("raw-color", rel, no, m.group(2).lower(), "arbitrary color class %s-[...]" % m.group(1), project.color_hint(col))
    return sorted(out, key=lambda f: (f["rule"], f["file"], f["line"]))


def keyed(findings):
    counts = Counter()
    for f in findings:
        counts["%s|%s|%s" % (f["rule"], f["file"], f["value"])] += 1
    return counts


def cmd_scan(args):
    project = Project(args.path, args.grid, args.spacing_policy, args.token_file or [], args.ignore or [])
    findings = scan(project, ALLOW_VAR + ([TAILWIND_THEME_VAR] if project.tailwind else []) + (args.allow_var or []), args.strict_alpha)
    counts = keyed(findings)
    base_counts, new_keys = {}, set()
    if args.baseline and os.path.isfile(args.baseline):
        with open(args.baseline) as fh:
            base_counts = json.load(fh).get("counts", {})
        for key, n in counts.items():
            if n > base_counts.get(key, 0):
                new_keys.add(key)
    elif args.baseline and not (args.write_baseline or args.shrink):
        print("baseline file %s not found; create it with --write-baseline" % args.baseline, file=sys.stderr)
        return 2
    if args.write_baseline:
        if os.path.exists(args.write_baseline) and not args.force:
            print("%s exists; use --shrink to lower it or --force to overwrite" % args.write_baseline, file=sys.stderr)
            return 2
        with open(args.write_baseline, "w") as fh:
            json.dump({"version": 1, "counts": dict(sorted(counts.items()))}, fh, indent=1)
        print("baseline written: %d keys, %d violations" % (len(counts), sum(counts.values())))
        return 0
    shrunk = 0
    if args.shrink and args.baseline:
        merged = {k: min(v, counts.get(k, 0)) for k, v in base_counts.items() if counts.get(k, 0) > 0}
        shrunk = sum(base_counts.values()) - sum(merged.values())
        with open(args.baseline, "w") as fh:
            json.dump({"version": 1, "counts": dict(sorted(merged.items()))}, fh, indent=1)
    shown = [f for f in findings if not args.baseline or "%s|%s|%s" % (f["rule"], f["file"], f["value"]) in new_keys]
    summary = Counter(f["rule"] for f in shown)
    if args.format == "json":
        print(json.dumps({"findings": shown, "summary": summary, "baselined": sum(base_counts.values()), "shrunk": shrunk}, indent=1))
    else:
        label = "new violations" if args.baseline else "violations"
        print("token_scan: %d %s (%s)" % (len(shown), label, ", ".join("%s %d" % kv for kv in sorted(summary.items())) or "none"))
        if args.baseline:
            print("baselined: %d, shrunk this run: %d" % (sum(base_counts.values()), shrunk))
        per_rule = Counter()
        for f in shown:
            per_rule[f["rule"]] += 1
            if per_rule[f["rule"]] <= args.max:
                print("  %s %s:%d  %s  %s%s" % (f["rule"], f["file"], f["line"], f["value"], f["message"], ("  -> " + f["hint"]) if f["hint"] else ""))
        for rule, n in sorted(per_rule.items()):
            if n > args.max:
                print("  ... %d more %s findings (raise --max)" % (n - args.max, rule))
    return 1 if shown else 0


def theme_values(project):
    base = project.tokens.get("light", {})
    themes = {"light": dict(base)}
    for theme, vals in project.tokens.items():
        if theme != "light":
            merged = dict(base)
            merged.update(vals)
            themes[theme] = merged
    return themes


def discover_pairs(names):
    have, groups = set(names), []
    surfaces = [n for n in ("--surface", "--background", "--bg", "--canvas", "--color-surface", "--color-background", "--color-bg") if n in have]
    for s in surfaces:
        for fg in ("--text", "--foreground", "--fg", "--text-primary", "--text-secondary", "--text-muted", "--muted-foreground", "--color-text", "--color-foreground", "--color-text-muted"):
            if fg in have:
                groups.append(([(fg, s)], 4.5))
        for ring in ("--focus", "--ring", "--focus-ring", "--color-focus", "--color-ring"):
            if ring in have:
                groups.append(([(ring, s)], 3.0))
    for n in sorted(have):
        for suffix in ("-foreground", "-text"):
            if n.endswith(suffix) and n[:-len(suffix)] in have and n[:-len(suffix)] not in ("-", ""):
                alts = [(n, n[:-len(suffix)])]
                alts += [(n, s) for s in surfaces if suffix == "-text"]
                groups.append((alts, 4.5))
        m = re.match(r"^--on-([\w-]+)$", n)
        if m and "--" + m.group(1) in have:
            groups.append(([(n, "--" + m.group(1))], 4.5))
    return groups


def cmd_contrast(args):
    project = Project(args.path, 4, "off", [], [], args.tokens or [])
    if not args.tokens and project.file_tokens:
        largest = max(project.file_tokens, key=lambda r: len(project.file_tokens[r]))
        project = Project(args.path, 4, "off", [], [], [largest])
        print("token source: %s (%d definitions); add files with --tokens" % (largest, len(project.file_tokens[largest])), file=sys.stderr)
    themes = theme_values(project)
    if not project.tokens:
        print("no custom properties found in token roots under %s" % args.path, file=sys.stderr)
        return 2
    explicit = []
    for spec in args.pair or []:
        parts = spec.split(":")
        explicit.append(([("--" + parts[0].lstrip("-"), "--" + parts[1].lstrip("-"))], float(parts[2]) if len(parts) > 2 else 4.5))
    failures, rows = 0, []
    for theme, values in sorted(themes.items()):
        groups = explicit if args.only_pairs else explicit + discover_pairs(values)
        for alts, need in groups:
            best = None
            for fg_name, bg_name in alts:
                fg_lit, bg_lit = resolve(fg_name, values), resolve(bg_name, values)
                fg = parse_color(fg_lit) if fg_lit else None
                bg = parse_color(bg_lit) if bg_lit else None
                if fg is None or bg is None or bg[3] < 1.0:
                    cand = (None, fg_name, bg_name, fg_lit, bg_lit)
                else:
                    cand = (contrast_ratio(over(fg, bg) if fg[3] < 1.0 else fg, bg), fg_name, bg_name, fg_lit, bg_lit)
                if best is None or (cand[0] or 0) > (best[0] or 0):
                    best = cand
            ratio, fg_name, bg_name, fg_lit, bg_lit = best
            status = "UNKNOWN" if ratio is None else ("PASS" if ratio >= need else "FAIL")
            failures += status != "PASS"
            inherited = [n for n in (fg_name, bg_name) if theme != "light" and n not in project.tokens.get(theme, {})]
            rows.append({"theme": theme, "fg": fg_name, "bg": bg_name, "ratio": None if ratio is None else round(ratio, 2), "need": need, "status": status,
                         "fg_value": fg_lit, "bg_value": bg_lit, "inherited_from_light": inherited})
    if args.format == "json":
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            note = ("  (inherits light: %s)" % ", ".join(r["inherited_from_light"])) if r["inherited_from_light"] else ""
            print("%-8s %-7s %-22s on %-18s %s (need %s)%s" % (r["theme"], r["status"], r["fg"], r["bg"], "n/a" if r["ratio"] is None else "%.2f" % r["ratio"], r["need"], note))
        print("%d pair(s) checked, %d not passing" % (len(rows), failures))
    return 1 if failures else 0


def cmd_inventory(args):
    project = Project(args.path, args.grid, "off", [])
    base = project.tokens.get("light", {})
    groups = defaultdict(list)
    for name in sorted(set(n for t in project.tokens.values() for n in t)):
        value = base.get(name) or next(t[name] for t in project.tokens.values() if name in t)
        kind = "color" if parse_color(resolve(name, base) or value) else ("radius" if name in project.radius else "type" if name in project.type else "spacing" if name in project.spacing else "other")
        groups[kind].append((name, value, project.refs.get(name, 0), name in project.theme_block))
    unused = [n for k in groups for n, v, r, tw in groups[k] if r == 0 and not tw]
    dup = defaultdict(list)
    for hx, names in project.colors.items():
        if len(names) > 1:
            dup[hx] = sorted(names)
    data = {"groups": {k: [{"name": n, "value": v, "refs": r} for n, v, r, _ in vs] for k, vs in groups.items()},
            "unused": unused, "duplicate_colors": dup, "themes": sorted(project.tokens)}
    if args.format == "json":
        print(json.dumps(data, indent=1))
        return 0
    print("themes: %s" % ", ".join(data["themes"]))
    for kind, items in sorted(groups.items()):
        print("\n%s (%d)" % (kind, len(items)))
        for n, v, r, tw in items:
            print("  %-28s %-26s refs %d%s" % (n, v[:26], r, " (@theme)" if tw else ""))
    print("\nunused (no var() reference; @theme tokens are used through utilities): %s" % (", ".join(unused) or "none"))
    for hx, names in sorted(dup.items()):
        print("same value %s: %s" % (hx, ", ".join(names)))
    return 0


# --------------------------------------------------------------------------- adopt

THEME_UTILITY = [
    (re.compile(r"^--color-(.+)$"), r"(?:bg|text|border|ring|fill|stroke|from|via|to|outline|divide|decoration|accent|caret|shadow)-%s(?![\w-])"),
    (re.compile(r"^--radius-(.+)$"), r"rounded(?:-[a-z]{1,2})?-%s(?![\w-])"),
    (re.compile(r"^--text-(.+)$"), r"text-%s(?![\w-])"),
]
ROLE_FAMILY = [
    (re.compile(r"^(background|bg|fill|from|via|to)"), "surface", ("--surface", "--bg", "--background", "--color-surface", "--color-background", "--color-bg")),
    (re.compile(r"^(color|text|caret|decoration)"), "text", ("--text", "--fg", "--foreground", "--color-text", "--color-fg")),
    (re.compile(r"^(border|outline|ring|divide|stroke)"), "border", ("--border", "--color-border", "--stroke", "--color-stroke")),
    (re.compile(r"shadow"), "shadow", ("--shadow", "--color-shadow")),
]
SCALE_FAMILY = {"spacing": ("--space", "spacing"), "radius": ("--radius", "radius"), "type": ("--text", "font size")}


def px_values(prop, value):
    """Yield (kind, px, text) for each length a declaration sets on spacing, radius, or font size."""
    def px_of(part):
        m = LENGTH.match(part)
        return float(m.group(1)) * (16 if m.group(2) == "rem" else 1) if m else None
    if SPACING_PROP.match(prop):
        if re.search(r"\b(calc|clamp|min|max|var)\(", value):
            return
        for part in value.split():
            px = px_of(part)
            if px is not None and abs(px) > 2:
                yield "spacing", abs(px), part
    elif prop == "font-size":
        px = px_of(value.strip())
        if px is not None:
            yield "type", px, value.strip()
    elif RADIUS_PROP.match(prop):
        for part in strip_funcs(value).split():
            px = px_of(part)
            if px is not None and px not in (0, 9999, 999):
                yield "radius", px, part


def color_key(col):
    return to_hex(col) if col[3] == 1.0 else "%s/%g" % (to_hex(col), round(col[3], 2))


def collect_literals(project, strict_alpha=False):
    """Every color, spacing, radius, and type literal outside token definitions: dicts of kind, key, file, line, text, prop."""
    out, seen = [], set()

    def add(kind, key, rel, line, text, prop):
        sig = (kind, key, rel, line, text)
        if sig not in seen:
            seen.add(sig)
            out.append({"kind": kind, "key": key, "file": rel, "line": line, "text": text, "prop": prop})

    for rel, line, prop, value, frames, is_token_file in project.decls:
        if (prop.startswith("--") and in_token_root(frames)) or is_token_file:
            continue
        for lit, col in iter_colors(value, strict_alpha):
            add("color", color_key(col), rel, line, lit.lower(), prop)
        for kind, px, text in px_values(prop, value):
            add(kind, round(px, 2), rel, line, "%s: %s" % (prop, text), prop)
    for rel, ext, text, is_token_file in project.files:
        if ext in CSS_EXT or is_token_file or ext not in CODE_EXT | MARKUP_EXT:
            continue
        for no, raw in enumerate(text.split("\n"), 1):
            if not (ext in MARKUP_EXT and "<style" in raw):
                for lit, col in iter_colors(raw, strict_alpha):
                    add("color", color_key(col), rel, no, lit.lower(), "source")
            for m in TW_ARBITRARY.finditer(raw):
                util, arg = m.group(1), m.group(2)
                if arg.startswith("var(") or arg.startswith("--"):
                    continue
                if TW_COLOR.match(util) and (HEX.search(arg) or FUNC.search(arg)):
                    col = parse_color(arg.replace("_", " "))
                    if col and not (strip_alpha_neutrals(col) and not strict_alpha):
                        add("color", color_key(col), rel, no, arg.lower(), util)
                    continue
                lm = LENGTH.match(arg)
                if not lm:
                    continue
                px = float(lm.group(1)) * (16 if lm.group(2) == "rem" else 1)
                if TW_SPACING.match(util) and abs(px) > 2:
                    add("spacing", round(abs(px), 2), rel, no, "%s-[%s]" % (util, arg), util)
                elif util == "text":
                    add("type", round(px, 2), rel, no, "text-[%s]" % arg, util)
                elif re.match(r"^rounded(-[a-z]{1,2})?$", util) and px not in (0, 9999, 999):
                    add("radius", round(px, 2), rel, no, "%s-[%s]" % (util, arg), util)
            for m in JS_SPACING.finditer(raw):
                px = float(m.group(3))
                if abs(px) > 2:
                    add("spacing", round(abs(px), 2), rel, no, "%s%s: %s" % (m.group(1), m.group(2) or "", m.group(3)), m.group(1))
            for m in INLINE_DECL.finditer(raw):
                for kind, px, tx in px_values(m.group(1).lower(), m.group(2).strip()):
                    add(kind, round(px, 2), rel, no, "%s: %s" % (m.group(1).lower(), tx), m.group(1).lower())
    return out


def utility_uses(project, name):
    """How often Tailwind utilities consume an @theme token, since var() counts miss those."""
    for pat, template in THEME_UTILITY:
        m = pat.match(name)
        if m and name in project.theme_block:
            rx = re.compile(template % re.escape(m.group(1)))
            return sum(len(rx.findall(text)) for rel, ext, text, tf in project.files if ext not in CSS_EXT and not tf)
    return 0


def scope_paths(project, files):
    """--files entries as paths relative to the project: absolute paths, paths from PATH, and paths from the current folder all work."""
    out = []
    for f in files or []:
        if os.path.isabs(f):
            rel = os.path.relpath(f, project.base)
        elif os.path.exists(os.path.join(project.base, f)) or not os.path.exists(f):
            rel = os.path.normpath(f)
        else:
            rel = os.path.relpath(os.path.abspath(f), project.base)
        out.append(rel.replace(os.sep, "/"))
    return out


def in_scope(rel, files):
    if not files:
        return True
    return any(rel == f or rel.startswith(f.rstrip("/") + "/") or rel.endswith("/" + f) for f in files)


def strip_step(name):
    """'--space-3' gives '--space'; a name with no step, like '--spacing', stays as it is."""
    parts = name.lstrip("-").split("-")
    return "--" + "-".join(parts[:-1] if len(parts) > 1 else parts)


def role_hint(project, kind, props):
    """A naming stub for a new token: the family of the existing tokens it joins, with the role left for a person to name."""
    names = sorted(set(n for t in project.tokens.values() for n in t))
    if kind == "color":
        top = Counter(p.lstrip("-") for p in props).most_common(1)[0][0] if props else "color"
        for pat, family, prefixes in ROLE_FAMILY:
            if pat.match(top):
                found = next((n for pre in prefixes for n in names if n == pre or n.startswith(pre + "-")), None)
                prefix = (found if found in prefixes else strip_step(found)) if found else "--" + family
                return "%s-<role>" % prefix
        return "--color-<role>"
    scale = {"spacing": project.spacing, "radius": project.radius, "type": project.type}[kind]
    stems = Counter(strip_step(n) for n in scale if "--" not in n[2:])
    prefix = sorted(stems.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if stems else SCALE_FAMILY[kind][0]
    return "%s-<step>" % prefix


def times(n):
    return "%d time%s" % (n, "" if n == 1 else "s")


def fmt_value(kind, key):
    if kind == "color":
        return key
    return "%gpx" % key


def adopt_plan(project, lits, files=None, grid=4):
    """One proposal per distinct literal: replace, retarget, add, or keep. Evidence counts the whole project; proposals cover the scope."""
    tol = {"spacing": max(1, grid // 2), "radius": 2, "type": 1}
    groups = defaultdict(list)
    for L in lits:
        groups[(L["kind"], L["key"])].append(L)
    scales = {"spacing": project.spacing, "radius": project.radius, "type": project.type}

    def near_px(kind, px):
        return project.nearest_token(scales[kind], px, tol[kind])

    def near_color(key):
        if "/" in key:
            return [], None
        col = parse_color(key)
        names, dist, _ = project.nearest_color(col)
        return (names, dist) if names and dist <= 24 else ([], None)

    def token_uses(kind, names, value_key):
        return sum(project.refs.get(n, 0) + utility_uses(project, n) for n in names) + len(groups.get((kind, value_key), []))

    # retarget: a literal that already stands in for a token, used far more than the token's own value
    retarget = {}
    candidates = defaultdict(list)   # token tuple -> [(count, key)]
    for (kind, key), members in groups.items():
        if kind == "color":
            names, dist = near_color(key)
            if names and dist:
                candidates[(kind, tuple(names))].append((len(members), key))
        else:
            near = near_px(kind, key)
            if near and abs(near[0][1] - key) > 0.001:
                candidates[(kind, (near[0][0],))].append((len(members), key))
    for (kind, names), cands in candidates.items():
        count, key = max(cands, key=lambda c: (c[0], str(c[1])))
        if kind == "color":
            old_key = next((hx for hx, ns in project.colors.items() if sorted(ns) == list(names)), None)
        else:
            old_key = round(scales[kind][names[0]], 2)
        if old_key is None:
            continue
        old_uses = token_uses(kind, names, old_key)
        if count >= 3 and count >= 2 * max(old_uses, 1):
            retarget[(kind, key)] = {"tokens": list(names), "from": fmt_value(kind, old_key), "token_uses": old_uses,
                                     "also": sorted(fmt_value(kind, k) for c, k in cands if k != key)}
    out = []
    for (kind, key), members in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        scoped = [m for m in members if in_scope(m["file"], files)]
        if not scoped:
            continue
        base = {"kind": kind, "value": fmt_value(kind, key), "count": len(members), "in_scope": len(scoped),
                "files": sorted(set(m["file"] for m in members))[:5], "where": ["%s:%d" % (m["file"], m["line"]) for m in scoped[:3]],
                "props": sorted(set(m["prop"] for m in scoped))[:4]}
        if (kind, key) in retarget:
            r = retarget[(kind, key)]
            base.update(action="retarget", token=r["tokens"][0], tokens=r["tokens"], to=fmt_value(kind, key), **{"from": r["from"]})
            base["token_uses"] = r["token_uses"]
            base["also"] = r["also"]
            base["reason"] = ("%s stands in for %s %s and the token itself is used %s: change the token once (every theme), "
                              "then use var(%s) at the call sites" % (fmt_value(kind, key), r["tokens"][0], times(len(members)), times(r["token_uses"]), r["tokens"][0]))
            out.append(base)
            continue
        if kind == "color":
            names, dist = near_color(key)
            if names:
                base.update(action="replace", token=names[0], tokens=names, exact=dist == 0,
                            reason=("matches %s exactly" % ", ".join(names[:3])) if dist == 0 else "RGB distance %.0f from %s, inside the snap limit of 24" % (dist, names[0]))
                out.append(base)
                continue
        else:
            near = near_px(kind, key)
            if near:
                exact = abs(near[0][1] - key) < 0.001
                base.update(action="replace", token=near[0][0], tokens=[n for n, v in near if v == near[0][1]], exact=exact,
                            reason="equals %s (%s)" % (near[0][0], fmt_value(kind, near[0][1])) if exact
                            else "%s is %gpx from %s (%s)" % (fmt_value(kind, key), abs(near[0][1] - key), near[0][0], fmt_value(kind, near[0][1])))
                tied = sorted(set(n for n, v in near if abs(abs(v - key) - abs(near[0][1] - key)) < 0.001 and v != near[0][1]))
                if tied:
                    base["reason"] += "; %s is as close, so pick by role" % ", ".join(tied)
                out.append(base)
                continue
        if len(members) >= 3:
            hint = role_hint(project, kind, [m["prop"] for m in scoped])
            base.update(action="add", name_hint=hint,
                        reason="used %s in %d file%s and no existing token is within tolerance; add one role token (%s, named by role, not by value) and use it everywhere"
                               % (times(len(members)), len(base["files"]), "" if len(base["files"]) == 1 else "s", hint))
        else:
            base.update(action="keep", reason="used %s and no existing token is within tolerance; leave it as a one-off" % times(len(members)))
        out.append(base)
    return out


def cmd_adopt(args):
    project = Project(args.path, args.grid, "off", args.token_file or [], args.ignore or [])
    if not project.tokens:
        print("no custom properties found in token roots under %s; define the roles first" % args.path, file=sys.stderr)
        return 2
    lits = collect_literals(project, args.strict_alpha)
    files = scope_paths(project, args.files)
    plan = adopt_plan(project, lits, files, args.grid)
    counts = Counter(p["action"] for p in plan)
    if args.format == "json":
        print(json.dumps({"summary": counts, "tolerance": {"color_rgb": 24, "spacing_px": max(1, args.grid // 2), "radius_px": 2, "type_px": 1},
                          "proposals": plan}, indent=1))
        return 0
    sections = [("replace", "Replace with the nearest token"), ("retarget", "Retarget a token"), ("add", "Add a role"), ("keep", "Keep as a one-off")]
    print("# Token adoption plan")
    print("%d literal values in %d group(s) at %s%s: %s" % (len(lits), len(plan), args.path, " (limited to %s)" % ", ".join(args.files) if args.files else "",
          ", ".join("%s %d" % (a, counts.get(a, 0)) for a, _ in sections)))
    for action, title in sections:
        items = [p for p in plan if p["action"] == action]
        if not items:
            continue
        print("\n## %s (%d)" % (title, len(items)))
        for p in items[:args.max]:
            where = ", ".join(p["where"][:2])
            if action == "replace":
                print("- `%s` (%s) x%d -> `var(%s)`: %s. At %s" % (p["value"], ", ".join(p["props"][:2]), p["in_scope"], p["token"], p["reason"], where))
            elif action == "retarget":
                print("- `%s: %s` -> `%s`: %s. At %s" % (p["token"], p["from"], p["to"], p["reason"], where))
            elif action == "add":
                print("- `%s` (%s) x%d -> `%s`: %s. At %s" % (p["value"], ", ".join(p["props"][:2]), p["count"], p["name_hint"], p["reason"], where))
            else:
                print("- `%s` (%s) x%d: %s. At %s" % (p["value"], ", ".join(p["props"][:2]), p["count"], p["reason"], where))
        if len(items) > args.max:
            print("- ... %d more (raise --max or use --format json)" % (len(items) - args.max))
    if plan:
        print("\nNo proposal adds a token beside one that already covers the role: a literal near a token is replaced or retargets it.")
    return 0


SAMPLE = {
    "src/tokens.css": """:root{--surface:#ffffff;--text:#1a1a1a;--text-muted:#bbbbbb;--action:#b94a00;--action-text:#ffffff;--space-1:4px;--space-2:8px;--space-3:12px;--radius-sm:4px;--radius-md:8px;--text-sm:12px;--text-md:14px;--text-lg:16px;--focus:#0b57d0}
[data-theme="dark"]{--surface:#14110f;--text:#f2ede8;--focus:#8ab4f8}
""",
    "src/button.css": """/* #123456 in a comment is ignored */
.btn{padding:13px 16px;background:#3b82f6;border-radius:7px;color:var(--text);margin:-8px;gap:10px;border:1px solid rgba(0,0,0,.1);box-shadow:0 1px 2px rgba(0,0,0,.1);font-size:13px}
.ok{padding:12px 16px;background:#ffffff;border-radius:8px;font-size:14px}
.x{color:var(--nope);outline:2px solid var(--focus, #000)}
""",
    "src/card.css": """.derived{border-radius:calc(var(--radius-md) - 4px)}
.spaced{border-radius:calc( var(--radius-md) - 4px )}
.fallback{border-radius:var(--radius-sm, 3px )}
.mixed{border-radius:8px 8px calc( 12px - 4px ) 0}
.literal{border-radius:7px}
""",
    "src/App.tsx": """export const A = () => (
  <div className="p-[13px] bg-[#fff] text-[var(--text)] gap-[16px] rounded-[7px]" style={{ padding: 13, marginTop: 8 }}>
    <a href="#section">x</a>
  </div>
);
""",
}


ADOPT_SAMPLE = {
    "src/tokens.css": """:root{--surface:#ffffff;--text:#1a1a1a;--brand:#3b82f6;--space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--radius-sm:4px;--radius-md:8px;--text-sm:12px;--text-md:14px;--text-lg:16px}
[data-theme="dark"]{--surface:#14110f;--text:#f2ede8}
""",
    "src/a.css": """.card{background:#ffffff;padding:13px 16px;border-radius:9px;color:#10b981}
.x{border-radius:9px;margin-top:22px}
""",
    "src/b.css": """.y{border-radius:9px;color:#10b981;background:var(--surface)}
.z{border-radius:9px;padding:var(--space-3)}
""",
    "src/c.tsx": """export const C = () => <div className="rounded-[9px] text-[#10b981]" style={{ borderRadius: "var(--radius-md)", color: "#7c3aed" }} />
""",
}


def adopt_selftest(expect):
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in ADOPT_SAMPLE.items():
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            with open(os.path.join(tmp, rel), "w") as fh:
                fh.write(text)
        project = Project(tmp, 4, "off")
        plan = adopt_plan(project, collect_literals(project))
        by = dict(((p["kind"], p["value"]), p) for p in plan)

        def act(kind, value):
            return by.get((kind, value), {}).get("action")
        expect(act("color", "#ffffff") == "replace" and by[("color", "#ffffff")]["token"] == "--surface" and by[("color", "#ffffff")]["exact"], "adopt: an exact color match is replaced with its token")
        expect(act("spacing", "13px") == "replace" and by[("spacing", "13px")]["token"] == "--space-3" and not by[("spacing", "13px")]["exact"], "adopt: 13px snaps to --space-3")
        expect(act("spacing", "16px") == "replace" and by[("spacing", "16px")]["exact"], "adopt: an on-scale literal is replaced with its token")
        retarget = by.get(("radius", "9px"), {})
        expect(retarget.get("action") == "retarget" and retarget.get("token") == "--radius-md" and retarget.get("from") == "8px" and retarget.get("to") == "9px",
               "adopt: 9px used five times where --radius-md is used once retargets the token: %s" % retarget)
        expect(act("color", "#10b981") == "add" and by[("color", "#10b981")]["name_hint"] == "--text-<role>", "adopt: a color used three times with no token near is a new role named in the text family: %s" % by.get(("color", "#10b981")))
        expect(act("color", "#7c3aed") == "keep" and act("spacing", "22px") == "keep", "adopt: single uses with no token near are one-offs")
        adds = [p["value"] for p in plan if p["action"] == "add"]
        expect(adds == ["#10b981"], "adopt: never adds a token beside one that covers the role, only %s: %s" % ("#10b981", adds))
        expect(sorted(set(p["action"] for p in plan)) == ["add", "keep", "replace", "retarget"], "adopt: all four outcomes appear")
        scoped = adopt_plan(project, collect_literals(project), ["src/b.css"])
        expect(sorted(p["value"] for p in scoped) == ["#10b981", "9px"], "adopt: --files limits proposals but keeps whole-project evidence: %s" % [p["value"] for p in scoped])
        expect([p["action"] for p in scoped if p["value"] == "9px"] == ["retarget"], "adopt: the retarget still counts uses outside the given files")
        old, sys.stdout = sys.stdout, _Buf()
        try:
            code = main(["adopt", tmp, "--files", "src/a.css", "--format", "json"])
            data = json.loads(sys.stdout.text)
            sys.stdout = _Buf()
            code_md = main(["adopt", tmp])
            md = sys.stdout.text
        finally:
            sys.stdout = old
        expect(code == 0 and code_md == 0 and sorted(data["summary"]) == ["add", "keep", "replace", "retarget"], "adopt: json output parses and carries every outcome")
        expect("## Retarget a token (1)" in md and "## Replace with the nearest token (3)" in md and "## Add a role (1)" in md and "## Keep as a one-off (2)" in md,
               "adopt: markdown headings are in sentence case: %s" % [l for l in md.splitlines() if l.startswith("#")])


def cmd_selftest(_args):
    failures = []

    def expect(cond, label):
        if not cond:
            failures.append(label)
    expect(abs(contrast_ratio(parse_color("#777777"), parse_color("#ffffff")) - 4.48) < 0.01, "contrast #777 on #fff is 4.48")
    expect(abs(contrast_ratio(parse_color("#000"), parse_color("#fff")) - 21.0) < 0.01, "black on white is 21")
    expect(parse_color("oklch(0.5 0 0)") is not None and abs(luminance(parse_color("oklch(1 0 0)")) - 1.0) < 0.01, "oklch white parses")
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in SAMPLE.items():
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            with open(os.path.join(tmp, rel), "w") as fh:
                fh.write(text)
        project = Project(tmp)
        found = scan(project, ALLOW_VAR)
        values = {(f["rule"], f["value"]) for f in found}
        for rule, value in [("raw-color", "#3b82f6"), ("off-scale", "padding: 13px"), ("off-scale", "gap: 10px"), ("off-scale", "border-radius: 7px"),
                            ("off-scale", "font-size: 13px"), ("raw-color", "#fff"), ("off-scale", "p-[13px]"), ("off-scale", "rounded-[7px]"),
                            ("off-scale", "padding: 13"), ("undefined-var", "--nope"), ("raw-color", "#ffffff")]:
            expect((rule, value) in values, "finds %s %s" % (rule, value))
        for rule, value in [("raw-color", "#123456"), ("off-scale", "margin: -8px"), ("off-scale", "padding: 12px"), ("off-scale", "gap-[16px]"),
                            ("raw-color", "rgba(0,0,0,.1)"), ("undefined-var", "--focus"), ("off-scale", "marginTop: 8"), ("raw-color", "#000")]:
            expect((rule, value) not in values, "does not flag %s %s" % (rule, value))
        card = sorted(f["value"] for f in found if f["file"] == "src/card.css")
        expect(card == ["border-radius: 7px"], "radius values inside calc() and var() are not flagged, a literal is: %s" % card)
        note = [f["message"] for f in found if f["file"] == "src/card.css"]
        expect(note and "calc(var(--radius-outer) - var(--inset))" in note[0] and "instead of a new literal" in note[0], "the radius message names the derived form")
        hint = [f["hint"] for f in found if f["value"] == "#ffffff"]
        expect(hint and "var(--surface)" in hint[0], "suggests the matching token for #ffffff")
        counts = keyed(found)
        expect(counts and sum(counts.values()) == len(found), "baseline keys cover findings")
        base_file = os.path.join(tmp, "baseline.json")
        ns = argparse.Namespace(path=tmp, grid=4, spacing_policy="grid", token_file=[], ignore=[], allow_var=[], strict_alpha=False, baseline=None,
                                write_baseline=base_file, shrink=False, force=False, format="text", max=25)
        quiet = open(os.devnull, "w")
        old, sys.stdout = sys.stdout, quiet
        try:
            expect(cmd_scan(ns) == 0, "write baseline exits 0")
            ns.write_baseline, ns.baseline = None, base_file
            expect(cmd_scan(ns) == 0, "scan within baseline exits 0")
            with open(os.path.join(tmp, "src/new.css"), "w") as fh:
                fh.write(".n{padding:11px;color:#abcdef}")
            expect(cmd_scan(ns) == 1, "new violations exit 1")
            with open(os.path.join(tmp, "src/new.css"), "w") as fh:
                fh.write(".n{padding:12px}")
            with open(os.path.join(tmp, "src/button.css"), "w") as fh:
                fh.write(SAMPLE["src/button.css"].replace("padding:13px 16px;", "padding:12px 16px;"))
            ns.shrink = True
            expect(cmd_scan(ns) == 0, "scan after a fix exits 0")
            with open(base_file) as fh:
                expect("off-scale|src/button.css|padding: 13px" not in json.load(fh)["counts"], "baseline shrank after the fix")
            cargs = argparse.Namespace(path=tmp, pair=None, only_pairs=False, tokens=["tokens.css"], format="json")
            sys.stdout = io_buf = _Buf()
            code = cmd_contrast(cargs)
            sys.stdout = old
            rows = json.loads(io_buf.text)
            expect(code == 1, "contrast flags the muted text pair")
            muted = [r for r in rows if r["fg"] == "--text-muted" and r["theme"] == "light"]
            expect(muted and muted[0]["status"] == "FAIL", "text-muted on surface fails in light")
            dark_action = [r for r in rows if r["theme"] == "dark" and r["fg"] == "--action-text"]
            expect(dark_action and dark_action[0]["inherited_from_light"], "dark action pair reports inherited values")
        finally:
            sys.stdout = old
            quiet.close()
    adopt_selftest(expect)
    for label in failures:
        print("FAIL " + label)
    print("selftest %s (%d failure%s)" % ("FAILED" if failures else "OK", len(failures), "" if len(failures) == 1 else "s"))
    return 1 if failures else 0


class _Buf(object):
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s

    def flush(self):
        pass


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("path")
    s.add_argument("--grid", type=int, default=4, help="spacing grid in px (default 4)")
    s.add_argument("--spacing-policy", choices=["grid", "tokens", "off"], default="grid", help="grid: multiples of --grid or tokens pass; tokens: only token values pass")
    s.add_argument("--token-file", action="append", help="path fragment of a file that defines tokens (skipped for raw-color)")
    s.add_argument("--ignore", action="append", help="path fragment to skip, for example an intentional art or marketing area")
    s.add_argument("--allow-var", action="append", help="regex of custom property names to treat as defined elsewhere")
    s.add_argument("--strict-alpha", action="store_true", help="also flag black or white with alpha (shadows and scrims)")
    s.add_argument("--baseline", help="JSON baseline; only violations beyond it are reported")
    s.add_argument("--write-baseline", help="write today's violations to this file and exit")
    s.add_argument("--shrink", action="store_true", help="lower the baseline to today's counts (never raises it)")
    s.add_argument("--force", action="store_true")
    s.add_argument("--max", type=int, default=25, help="findings printed per rule")
    s.add_argument("--format", choices=["text", "json"], default="text")
    c = sub.add_parser("contrast")
    c.add_argument("path")
    c.add_argument("--pair", action="append", help="FG:BG[:MIN] token names without or with leading dashes, for example text:surface:4.5")
    c.add_argument("--only-pairs", action="store_true", help="skip pair discovery")
    c.add_argument("--tokens", action="append", help="path fragment of the file(s) that define the tokens to check; default is every file, largest wins")
    c.add_argument("--format", choices=["text", "json"], default="text")
    i = sub.add_parser("inventory")
    i.add_argument("path")
    i.add_argument("--grid", type=int, default=4)
    i.add_argument("--format", choices=["text", "json"], default="text")
    ad = sub.add_parser("adopt", help="propose, for each literal value, the nearest token, a token retarget, a new role, or a one-off")
    ad.add_argument("path")
    ad.add_argument("--files", nargs="+", help="only propose for literals in these files or folders (paths from PATH or from the current folder)")
    ad.add_argument("--grid", type=int, default=4, help="spacing grid in px; the spacing snap distance is half of it (default 4)")
    ad.add_argument("--token-file", action="append", help="path fragment of a file that defines tokens (skipped)")
    ad.add_argument("--ignore", action="append", help="path fragment to skip")
    ad.add_argument("--strict-alpha", action="store_true", help="also consider black or white with alpha (shadows and scrims)")
    ad.add_argument("--max", type=int, default=25, help="proposals printed per action")
    ad.add_argument("--format", choices=["md", "json"], default="md")
    sub.add_parser("selftest")
    args = ap.parse_args(argv)
    try:
        return {"scan": cmd_scan, "contrast": cmd_contrast, "inventory": cmd_inventory, "adopt": cmd_adopt, "selftest": cmd_selftest}[args.cmd](args)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
