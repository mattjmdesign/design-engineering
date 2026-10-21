#!/usr/bin/env python3
"""ui_claims.py: check that the evidence a report cites is real, untampered, fresh, and fits what the report says.

  ui_claims.py check REPORT.md [--evidence DIR] [--source ROOT] [--format text|json] [--strict]
  ui_claims.py selftest [--keep]

`ui_claims.py REPORT.md` is shorthand for `ui_claims.py check REPORT.md`.

A report cites evidence written by ui_measure.py with ids such as [E03@390:error#dark] (for a page from `routes`, the
state is the route: [E05@1280:inbox#light]), marks anything it did not verify with (unverified: a reason of three or more
words), and cites source code with [src:path:line]. check reads the
report and the evidence folder (default ./.ui-evidence) and prints one line per problem: the report line number, a
rule id, and a one-line fix. EVIDENCE.md in this folder lists the rules and the invariants they rest on.

Exit status: 0 no failures, 1 at least one failure, 2 usage or tool error.

What a pass means: every cited id exists, its files still match the hashes the tool wrote, its numbers agree with the
sentence that quotes them, it was captured at the width, state, and theme the cite says, and no source file is newer
than the capture. What it cannot mean: that the author looked at anything, that the evidence was not forged by someone
who recomputed every hash, or that a true cite is attached to a sentence it really supports.

Trust boundary
  * check only reads files: the report, the evidence folder, and (with --source) file names and modification times.
    It starts no process and opens no network connection.
  * Everything copied from the audited page (selectors, text, URLs, file names in a manifest) is untrusted data. It is
    stripped of control characters and never echoed past 80 characters, and nothing in it is read as an instruction.
  * The report is parsed as text. Nothing in it is run or fetched.

Compatible with Python 3.9. Standard library only.
"""
import argparse
import bisect
import calendar
import hashlib
import json
import os
import re
import sys
import time
import traceback
import unicodedata

sys.dont_write_bytecode = True  # keep __pycache__ out of the plugin folder
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

TOOL_VERSION = 1
EVIDENCE_DIR = ".ui-evidence"
ECHO_LIMIT = 80
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_REPORT_LINES = 60000
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_PNG_BYTES = 256 * 1024 * 1024
MAX_SOURCE_FILES = 100000
MAX_SENTENCE_CHARS = 4000
MAX_RUNS = 500

# One row per rule: (id, severity, what it proves). Kept in step with the table at the end of EVIDENCE.md; the selftest checks that every id has a failing report and a row there.
RULES = (
    ("claims.unknown-id", "FAIL", "A cited evidence id exists in the evidence root, so no evidence was invented or mistyped."),
    ("claims.integrity", "FAIL", "Cited evidence is untampered: file hashes match the manifest and the files agree with each other."),
    ("claims.fit", "FAIL", "The width, state (for routes evidence, the route), and theme in a cite match the evidence, and the sentence names no other width, theme, state, or route."),
    ("claims.unconfirmed-state", "FAIL", "A claim does not rest on a state that never rendered (state_confirmed is false)."),
    ("claims.number", "FAIL", "Contrast ratios, px values (for routes evidence, against shell.json), FAIL/WARN/INFO counts, and zero-claims agree with the cited evidence."),
    ("claims.unmeasured", "FAIL", "A registered screenshot (measured false) backs only how-it-looks claims, never a measured one."),
    ("claims.stale", "FAIL", "With --source, no source file is newer than the capture that is cited. Without it, a WARN says so."),
    ("claims.text-changes", "FAIL", "Text that compare lists as dropped, new, changed, or reordered for a cited run is disclosed in the report, and every dropped text that compare could not classify is quoted with what became of it: deleted as slop, merged, moved, or replaced."),
    ("claims.src-citation", "FAIL", "A [src:path:line] citation names an existing file with at least that many lines."),
    ("claims.uncited", "WARN (FAIL with --strict)", "A sentence that asserts a verified or measured result carries a cite or an unverified marker."),
    ("claims.unverified-marker", "FAIL", "(unverified: reason) carries a reason of at least three words; a bare (unverified) is rejected."),
)

SOURCE_EXTENSIONS = frozenset((
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".styl", ".pcss", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts",
    ".cts", ".vue", ".svelte", ".astro", ".mdx", ".liquid", ".njk", ".hbs", ".handlebars", ".ejs", ".pug", ".erb", ".php", ".twig", ".svg"))
SKIP_DIRS = frozenset((
    "node_modules", ".git", ".ui-evidence", "dist", "build", "out", ".next", ".nuxt", ".svelte-kit", ".turbo", ".cache", ".parcel-cache",
    "__pycache__", ".venv", "venv", "coverage", ".pytest_cache", ".mypy_cache", ".vercel", ".output", ".astro", "target", ".gradle", "Pods", ".idea", ".vscode",
    "bower_components", "storybook-static", "playwright-report", "test-results"))
KNOWN_WIDTHS = frozenset((320, 360, 375, 390, 393, 412, 414, 428, 430, 768, 820, 834, 1024, 1180, 1194, 1280, 1366, 1440, 1536, 1600, 1680, 1728, 1920, 2560))
TIERS = ("FAIL", "WARN", "INFO")


class ToolError(Exception):
    pass


# ----------------------------------------------------------------------------
# small helpers

def safe(value, limit=ECHO_LIMIT):
    """Make page-derived or report-derived text safe to print: no control, format, or separator characters, at most limit characters."""
    text = value if isinstance(value, str) else str(value)
    kept = []
    for ch in text:
        kept.append(" " if unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp") else ch)
    text = " ".join("".join(kept).split())
    if len(text) > limit:
        text = text[:max(limit - 3, 0)] + "..."
    return text


def read_bytes(path, cap):
    with open(path, "rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise ValueError("file is larger than %d bytes" % cap)
    return data


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path, cap=MAX_PNG_BYTES):
    digest, total = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk:
                break
            total += len(chunk)
            if total > cap:
                raise ValueError("file is larger than %d bytes" % cap)
            digest.update(chunk)
    return digest.hexdigest()


def png_size(path):
    """Width and height from the PNG header, or None when the file is not a PNG."""
    with open(path, "rb") as handle:
        head = handle.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def as_dict(value):
    return value if isinstance(value, dict) else {}


def as_list(value):
    return value if isinstance(value, list) else []


def num_text(value):
    value = float(value)
    return str(int(value)) if value == int(value) else ("%g" % value)


def utc_epoch(text):
    try:
        return calendar.timegm(time.strptime(str(text), "%Y-%m-%dT%H:%M:%SZ"))
    except (ValueError, OverflowError):
        return None


def norm_text(text):
    return " ".join(str(text).lower().split())


def same_length(match):
    return " " * len(match.group(0))


def blank_spans(text, spans):
    """Replace each (start, end) span with spaces so offsets stay valid."""
    if not spans:
        return text
    chars = list(text)
    for start, end in spans:
        for i in range(max(start, 0), min(end, len(chars))):
            chars[i] = " "
    return "".join(chars)


# ----------------------------------------------------------------------------
# results

class Results(object):
    """Findings in report order. Messages are single lines; every page-derived string in them went through safe()."""

    def __init__(self, strict):
        self.strict = strict
        self.items = []
        self._seen = set()

    def add(self, severity, rule, line, message, fix):
        key = (severity, rule, line, message)
        if key in self._seen:
            return
        self._seen.add(key)
        self.items.append({"line": line, "severity": severity, "rule": rule, "message": message, "fix": fix})

    def fail(self, rule, line, message, fix):
        self.add("FAIL", rule, line, message, fix)

    def warn(self, rule, line, message, fix):
        self.add("WARN", rule, line, message, fix)

    def count(self, severity, rule=None):
        return sum(1 for i in self.items if i["severity"] == severity and (rule is None or i["rule"] == rule))

    def sorted_items(self):
        order = {"FAIL": 0, "WARN": 1}
        return sorted(self.items, key=lambda i: (i["line"] == 0, i["line"], order.get(i["severity"], 2), i["rule"], i["message"]))


# ----------------------------------------------------------------------------
# report parsing: lines -> blocks -> sentences

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}#{1,6}(?:\s+|$)")
_TABLE_ROW = re.compile(r"^\s*\|")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$")
_HRULE = re.compile(r"^ {0,3}(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$")
_LIST = re.compile(r"^(\s*)(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?")
_QUOTE = re.compile(r"^ {0,3}>\s?")
_SKIP_BODY = re.compile(r"^\s*claims\s*:\s*skip\b", re.I)

_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1", re.S)
_URL = re.compile(r"https?://\S+")
_DQUOTE = re.compile(r'"[^"\n]{2,}"|\u201c[^\u201d\n]{2,}\u201d')
_HEXCOLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_ABBREV = re.compile(r"\b(?:e\.g|i\.e|etc|vs|approx|cf|incl|esp|fig|no|ca|al|st|mr|mrs|dr)\.", re.I)
_BOUNDARY = re.compile(r"([.!?]+[\"'\u201d\u2019)\]*_]*)(\s+)(?=[A-Z\"\u201c'(\[`*_<])")


class Block(object):
    __slots__ = ("kind", "rows", "skip", "text", "offsets", "lines")

    def __init__(self, kind, skip=False):
        self.kind, self.rows, self.skip = kind, [], skip
        self.text, self.offsets, self.lines = "", [], []

    def add(self, number, text):
        self.rows.append((number, text))

    def finish(self):
        parts, pos = [], 0
        for number, text in self.rows:
            self.offsets.append(pos)
            self.lines.append(number)
            parts.append(text)
            pos += len(text) + 1
        self.text = " ".join(parts)

    def line_at(self, pos):
        index = bisect.bisect_right(self.offsets, pos) - 1
        return self.lines[max(index, 0)] if self.lines else 0


class Sentence(object):
    def __init__(self, block, start, end):
        self.block, self.start, self.end = block, start, end
        self.text = block.text[start:end]
        self.line = block.line_at(start)
        self.kind = block.kind
        self.skip = block.skip
        self.cites, self.srcs, self.markers, self.spans = [], [], [], []
        self.plain = ""

    def line_at(self, pos):
        return self.block.line_at(self.start + pos)


class Report(object):
    def __init__(self):
        self.lines = []
        self.blocks = []
        self.sentences = []
        self.skips = 0
        self.fence_lines = set()

    def text_lines(self):
        """(line number, text) for every line outside code fences, with HTML comments removed."""
        for block in self.blocks:
            for number, text in block.rows:
                yield number, text


def _split_comments(line, state):
    """Remove HTML comments from one line. state is [in_comment, body]. Returns (visible text, number of skip markers)."""
    visible, markers, pos = [], 0, 0
    while True:
        if state[0]:
            end = line.find("-->", pos)
            if end < 0:
                state[1] += line[pos:] + " "
                break
            if _SKIP_BODY.match(state[1] + line[pos:end]):
                markers += 1
            state[0], state[1] = False, ""
            pos = end + 3
        else:
            start = line.find("<!--", pos)
            if start < 0:
                visible.append(line[pos:])
                break
            visible.append(line[pos:start])
            state[0], state[1] = True, ""
            pos = start + 4
    return "".join(visible), markers


def parse_report(raw):
    report = Report()
    lines = re.split(r"\r\n|\r|\n", raw.lstrip("\ufeff"))
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) > MAX_REPORT_LINES:
        raise ToolError("the report has more than %d lines" % MAX_REPORT_LINES)
    report.lines = lines
    state = {"current": None, "pending_skip": False}

    def start(kind):
        block = Block(kind, skip=state["pending_skip"])
        state["pending_skip"] = False
        return block

    def flush():
        block = state["current"]
        state["current"] = None
        if block is not None and block.rows:
            block.finish()
            report.blocks.append(block)

    def single(kind, number, text):
        flush()
        block = start(kind)
        block.add(number, text)
        block.finish()
        report.blocks.append(block)
        return block

    fence = None
    comment = [False, ""]
    for number, line in enumerate(lines, 1):
        if not comment[0] and fence is not None:
            report.fence_lines.add(number)
            m = _FENCE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line.strip().strip(fence[0]).strip():
                fence = None
            continue
        if not comment[0]:
            m = _FENCE.match(line)
            if m:
                flush()
                fence = m.group(1)
                report.fence_lines.add(number)
                continue
        visible, found = _split_comments(line, comment)
        report.skips += found
        text = visible.rstrip()
        line_skip = False
        if found:
            if text.strip():
                line_skip = True
            else:
                state["pending_skip"] = True
        if not text.strip():
            flush()
            continue
        if _HRULE.match(text) or _TABLE_SEP.match(text):
            flush()
            continue
        if _HEADING.match(text):
            block = single("heading", number, re.sub(r"^ {0,3}#{1,6}\s*", "", text).rstrip("# ").strip())
        elif _TABLE_ROW.match(text):
            body = text.strip()
            block = single("table", number, body[1:] if body.startswith("|") else body)
        elif _QUOTE.match(text):
            if state["current"] is None or state["current"].kind != "quote":
                flush()
                state["current"] = start("quote")
            block = state["current"]
            block.add(number, _QUOTE.sub("", text, count=1))
        else:
            m = _LIST.match(text)
            if m:
                flush()
                state["current"] = start("list")
                block = state["current"]
                block.add(number, text[m.end():])
            else:
                if state["current"] is None:
                    state["current"] = start("para")
                block = state["current"]
                block.add(number, text.strip())
        if line_skip:
            block.skip = True
    flush()
    for block in report.blocks:
        for start_pos, end_pos in split_spans(block):
            report.sentences.append(Sentence(block, start_pos, end_pos))
    return report


def _mask_for_split(text):
    masked = _CODE_SPAN.sub(lambda m: "`" + "x" * (len(m.group(0)) - 1), text)
    masked = _URL.sub(lambda m: "x" * len(m.group(0)), masked)
    masked = re.sub(r"\[[^\[\]\n]{0,300}\]", lambda m: "[" + "x" * (len(m.group(0)) - 2) + "]", masked)
    masked = re.sub(r"\([^()\n]{0,300}\)", lambda m: "(" + "x" * (len(m.group(0)) - 2) + ")", masked)
    masked = _ABBREV.sub(lambda m: m.group(0).replace(".", "x"), masked)
    return masked.replace("...", "xxx")


def split_spans(block):
    """Sentence spans of a block. Table rows and headings are one sentence; a segment that is only a cite or marker joins the one before."""
    text = block.text
    if not text.strip():
        return []
    if block.kind in ("table", "heading"):
        return [(0, len(text))]
    masked = _mask_for_split(text)
    spans, start = [], 0
    for m in _BOUNDARY.finditer(masked):
        spans.append((start, m.end(1)))
        start = m.end()
    spans.append((start, len(text)))
    merged = []
    for s, e in spans:
        if not text[s:e].strip():
            continue
        if merged and _only_references(text[s:e]):
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    # A cite written after the period belongs to the sentence before it: "No overflow at 390. [E05@390:default#light] Next sentence."
    adjusted = []
    for s, e in merged:
        if adjusted:
            cut = _leading_references(text[s:e])
            rest = text[s + cut:e].lstrip()
            if cut and rest[:1].isupper() or cut and rest[:1].isdigit():
                previous = text[adjusted[-1][0]:adjusted[-1][1]]
                if not _has_references(previous):
                    adjusted[-1] = (adjusted[-1][0], s + cut)
                    s = s + cut
                    while s < e and text[s].isspace():
                        s += 1
        adjusted.append((s, e))
    return adjusted


def _reference_spans(segment):
    refs = scan_refs(segment)
    return sorted(refs["spans"] + [(m["start"], m["end"]) for m in find_markers(blank_literals(segment))])


def _only_references(segment):
    return not re.sub(r"[\W_]+", "", blank_spans(segment, _reference_spans(segment)))


def _has_references(segment):
    return bool(_reference_spans(segment))


def _leading_references(segment):
    """Length of the run of references (cites, [src:...], unverified markers) at the start of a segment, or 0."""
    spans = _reference_spans(segment)
    pos = 0
    while True:
        while pos < len(segment) and segment[pos].isspace():
            pos += 1
        hit = [sp for sp in spans if sp[0] == pos]
        if not hit:
            break
        pos = max(sp[1] for sp in hit)
    return pos if pos and spans and spans[0][0] <= len(segment) - len(segment.lstrip()) else 0


# ----------------------------------------------------------------------------
# citations, source citations, and unverified markers inside a sentence

_CITE = re.compile(r"\bE(\d{1,6})@(\d{1,5}):([A-Za-z0-9_-]{1,40})#(light|dark|unknown)(?![\w-])")
_PARTIAL = re.compile(r"\bE(\d{1,6})@\d[^\s\[\],;)]{0,60}")
_BRACKET = re.compile(r"\[([^\[\]\n]{1,400})\]")
_BARE = re.compile(r"\bE(\d{1,6})\b(?![@\w-])")
_SRC = re.compile(r"\[src:([^\]\n]{1,300})\]", re.I)
_SRC_BODY = re.compile(r"^\s*(.+?)(?::(\d+)(?:\s*[-\u2013]\s*(\d+))?)?\s*$")
_RANGE_JOIN = re.compile(r"^\s*(?:to|through|thru|until|-|\u2013|\u2014|\.{2,3})\s*$", re.I)


class Cite(object):
    """One citation. kind is full (E03@390:error#dark), bare (E03), or partial (E03@390)."""

    def __init__(self, kind, eid, pos, raw, width=None, state=None, theme=None, expanded=False):
        self.kind, self.eid, self.pos, self.raw = kind, eid, pos, raw
        self.end = pos + len(raw)
        self.width, self.state, self.theme, self.expanded = width, state, theme, expanded
        self.group = None
        self.line = 0
        self.item = None

    @property
    def text(self):
        return "%s@%s:%s#%s" % (self.eid, self.width, self.state, self.theme) if self.kind == "full" else self.raw


def eid_of(number):
    return "E%02d" % int(number)


def scan_refs(text):
    """Find citations and [src:...] citations. Returns {"cites", "srcs", "spans"}; spans cover every reference so callers can blank them."""
    cites, srcs, spans = [], [], []
    src_spans = []
    for m in _SRC.finditer(text):
        body = _SRC_BODY.match(m.group(1))
        srcs.append({"path": body.group(1) if body else m.group(1).strip(), "start_line": int(body.group(2)) if body and body.group(2) else None,
                     "end_line": int(body.group(3)) if body and body.group(3) else None, "pos": m.start(), "start": m.start(), "end": m.end()})
        src_spans.append((m.start(), m.end()))
        spans.append((m.start(), m.end()))

    def in_src(pos):
        return any(a <= pos < b for a, b in src_spans)

    full_spans = []
    for m in _CITE.finditer(text):
        if in_src(m.start()):
            continue
        cites.append(Cite("full", eid_of(m.group(1)), m.start(), m.group(0), int(m.group(2)), m.group(3), m.group(4)))
        full_spans.append((m.start(), m.end()))
    for m in _BRACKET.finditer(text):
        if in_src(m.start()):
            continue
        inner = m.group(1)
        rest = _CITE.sub(same_length, inner)
        bare = list(_BARE.finditer(rest))
        residue = re.sub(r"\bE\d{1,6}\b|\band\b|\bto\b|[\s,;&+.\u2013\u2014-]", "", rest, flags=re.I)
        has_full = rest != inner
        if residue == "" and (bare or has_full):
            spans.append((m.start(), m.end()))
            for b in bare:
                cite = Cite("bare", eid_of(b.group(1)), m.start(1) + b.start(), b.group(0))
                cite.group = (m.start(), m.end(), len(bare))
                cite.end = m.end()
                cites.append(cite)
    for m in _PARTIAL.finditer(text):
        if in_src(m.start()) or any(a <= m.start() < b for a, b in full_spans):
            continue
        cites.append(Cite("partial", eid_of(m.group(1)), m.start(), m.group(0)))
        spans.append((m.start(), m.end()))
    for a, b in full_spans:
        spans.append((a, b))
    cites.sort(key=lambda c: c.pos)
    expanded = []
    for i, cite in enumerate(cites):
        expanded.append(cite)
        nxt = cites[i + 1] if i + 1 < len(cites) else None
        if (nxt is not None and cite.kind == "bare" and nxt.kind == "bare" and cite.group and nxt.group and cite.group[2] == 1 and nxt.group[2] == 1
                and _RANGE_JOIN.match(text[cite.group[1]:nxt.group[0]])):
            lo, hi = int(cite.eid[1:]), int(nxt.eid[1:])
            for n in range(lo + 1, min(hi, lo + 200)):
                filler = Cite("bare", eid_of(n), cite.pos, cite.raw, expanded=True)
                filler.end = cite.end
                expanded.append(filler)
    return {"cites": expanded, "srcs": srcs, "spans": spans}


def blank_literals(text):
    """Blank inline code, double-quoted text, and URLs. Offsets stay valid."""
    text = _CODE_SPAN.sub(same_length, text)
    text = _DQUOTE.sub(same_length, text)
    return _URL.sub(same_length, text)


def find_markers(text):
    """(unverified ...) markers with their reason word counts. text should already have literals blanked."""
    found = []
    for m in re.finditer(r"\(\s*unverified\b", text, re.I):
        depth, j = 1, m.end()
        while j < min(len(text), m.end() + 500) and depth:
            if text[j] == "(":
                depth += 1
            elif text[j] == ")":
                depth -= 1
            j += 1
        reason = text[m.end():j - 1] if depth == 0 else text[m.end():]
        reason = re.sub(r"^\s*[:;,\u2013\u2014-]\s*", "", reason)
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\u2019-]*", reason)
        found.append({"start": m.start(), "end": j, "words": len(words), "reason": reason.strip()})
    return found


def prepare_sentence(sentence):
    text = sentence.text[:MAX_SENTENCE_CHARS * 4]
    refs = scan_refs(text)
    literal_free = blank_literals(text)
    markers = find_markers(literal_free)
    sentence.cites, sentence.srcs, sentence.markers = refs["cites"], refs["srcs"], markers
    for cite in sentence.cites:
        cite.line = sentence.line_at(cite.pos)
    sentence.spans = refs["spans"] + [(m["start"], m["end"]) for m in markers]
    plain = blank_spans(literal_free, sentence.spans)
    plain = _HEXCOLOR.sub(same_length, plain)
    plain = re.sub(r"\[([^\]\n]*)\]\([^)\n]*\)", lambda m: "[" + m.group(1) + "]" + " " * (len(m.group(0)) - len(m.group(1)) - 2), plain)
    plain = re.sub(r"[*_~]+", " ", plain)
    sentence.plain = plain[:MAX_SENTENCE_CHARS]


# ----------------------------------------------------------------------------
# what counts as a claim: a conservative, lexical detector

_NEGCUE = re.compile(r"\b(?:not|never|\w+n't|cannot|unable|yet|without|needs?\s+to|should|must|will|would|could|might|may|to\s+be|remains?|pending|until)\b", re.I)
_PASS_NOT = r"(?!\s+(?:through|along|down|up|over|in|into|to|by|on|as|the\s+(?:props?|values?|data|ids?|state|callbacks?|handlers?|refs?|children|events?|arguments?|params?|class(?:es)?|styles?|tokens?)\b))"
_MEASURE_OBJ = r"(?:wcag|aa|aaa|contrast|ratio|targets?|minimum|min|threshold|guidelines?|standards?|requirements?|criteria|criterion|spec|accessibility|\d+(?:\.\d+)?\s*(?:px|:\s?1))"

CLAIM_PATTERNS = (
    # class A: words that assert a verified or measured result
    ("A", "verified", re.compile(r"\b(?:verified|confirmed|validated|verifies|confirms|validates)\b", re.I), True),
    ("A", "passes", re.compile(r"\b(?:passes|passed|passing)\b" + _PASS_NOT + r"|\b(?:all|every|each|both)\s+(?:\w+\s+){0,2}pass\b"
                               r"|\b(?:checks?|tests?|rules?|they|it|which|that|still|now|controls|buttons|targets|states|pages|widths|themes|captures|cases)\s+pass\b", re.I), False),
    ("A", "no overflow", re.compile(
        r"\bno\s+(?:horizontal\s+|sideways\s+|page\s+)?(?:overflow|scrolling|scroll|scrollbars?)\b"
        r"|\b(?:does(?:n't| not)|do(?:n't| not)|never)\s+(?:\w+\s+){0,2}(?:overflow|scroll\s+(?:sideways|horizontally))"
        r"|\bwithout\s+(?:any\s+)?(?:horizontal\s+)?(?:overflow|scrolling)\b|\bnothing\s+overflows\b|\bno\s+(?:layout\s+shift|clipping)\b", re.I), False),
    ("A", "meets", re.compile(r"\b(?:meets?|met|meeting|satisf(?:y|ies))\b(?:\s+\w+){0,5}?\s+" + _MEASURE_OBJ + r"\b", re.I), False),
    ("A", "contrast", re.compile(
        r"\bcontrast\s+ratios?\b|\bcontrast\s+(?:is|are|was|were|of|at|passes|fails|meets|exceeds|improves|improved|reaches|now|stays|remains|holds|checks?)\b"
        r"|\b(?:passes|meets|exceeds|fails|improved?|sufficient|adequate|insufficient|enough)\s+(?:wcag\s+)?(?:aa\s+)?(?:text\s+|colou?r\s+)?contrast\b"
        r"|\b(?:aa|aaa)\s+contrast\b|\bcontrast\s+(?:aa|aaa)\b", re.I), False),
    ("A", "tested at", re.compile(r"\b(?:tested|checked|measured|audited|exercised|captured|inspected)\s+(?:at|on|in|with|across|against|using|via|under)\b", re.I), False),
    ("A", "keyboard/focus", re.compile(
        r"\bkeyboard[- ](?:accessible|operable|reachable|navigable|only)\b|\b(?:reachable|operable|usable|navigable)\s+(?:by|with|via|using)\s+(?:the\s+)?keyboard\b"
        r"|\b(?:by|with|via|using)\s+(?:the\s+)?keyboard\s+(?:alone|only)\b|\bkeyboard\s+(?:access|support|navigation|traversal)\s+(?:works?|is|are|passes|verified|confirmed|ok|fine)\b"
        r"|\bfocus(?:\s+ring|\s+indicator|\s+outline)?\s+(?:is\s+|are\s+|stays\s+|remains\s+)?visible\b|\bvisible\s+focus\b|\bfocus-visible\s+(?:is|are|passes|works|verified|confirmed|present|shown)\b"
        r"|\b(?:focus|tab)\s+order\s+(?:is|are|matches|follows|stays|remains|holds|works|checked|verified|correct|logical|preserved)\b"
        r"|\btab(?:bing)?\s+(?:through|reaches|walk)\b|\b\d+\s+tab\s+stops?\b", re.I), False),
    ("A", "WCAG", re.compile(r"\bwcag\b(?=.*\b(?:meets?|passes?|passed|conforms?|conformance|compliant|complies|compliance|satisf\w+|violat\w+|fails?|failed|aa|aaa)\b)"
                              r"|\b(?:meets?|passes?|passed|conforms?|conformance|compliant|complies|compliance|satisf\w+|violat\w+|fails?|failed|aa|aaa)\b.*\bwcag\b", re.I), False),
    ("A", "renders", re.compile(r"\brenders?\s+(?:correctly|cleanly|properly|fine|well|identically|consistently|as\s+expected|without|with\s+no|at\s+\d|the\s+same|everywhere)\b"
                                 r"|\brendered?\s+(?:correctly|cleanly|properly|at)\b", re.I), False),
    ("A", "responsive", re.compile(r"\b(?:is|are|now|fully|truly|remains?|stays?)\s+(?:fully\s+)?responsive\b|\bresponsive\s+(?:at|across|down|from|behaviou?r|checks?)\b|\bworks?\s+(?:at|across)\s+\d", re.I), False),
    ("A", "FAIL/WARN/INFO count", re.compile(r"(?<![\w.])(?:\d+|(?i:no|zero|none))\s+(?:(?:justified|remaining|open|new|unresolved|known|accepted|residual|blocking|outstanding|total|distinct|real|actual|final|existing|unjustified|noted|expected|unexpected|tier)\s+)?(?:FAIL|WARN|INFO)s?\b"
                                         r"|\b(?:FAIL|WARN|INFO)\s*[:=]?\s*\d+\b"), False),
    ("A", "N findings", re.compile(r"(?<![\w.])(?:\d+|(?i:no|zero|none))\s+(?:(?:justified|remaining|open|new|unresolved|known|accepted|residual|blocking|outstanding|total|distinct)\s+)?findings?\b", re.I), False),
    ("A", "console", re.compile(r"\b(?:no|zero)\s+console\s+errors?\b|\bconsole\s+(?:is\s+)?clean\b|\bconsole\s+errors?\s+(?:fires?|is\s+logged|appears?|on\s+load)\b", re.I), False),
    ("S", "defect found", re.compile(
        r"\b(?:has|have|with)\s+no\s+(?:accessible\s+name|alt(?:\s+text|\s+attribute)?|visible\s+focus|focus\s+(?:ring|indicator|outline))\b"
        r"|\bno\s+(?:accessible\s+name|alt\s+attribute|visible\s+focus)\b|\bfocus\s+(?:ring|indicator|outline)\s+(?:is\s+|has\s+been\s+)?(?:removed|missing|invisible|hidden)\b"
        r"|\b(?:removed|missing)\s+focus\s+(?:ring|indicator|outline)\b|\bcannot\s+be\s+(?:reached|operated|activated|focused)\s+(?:with|by|using)\s+(?:the\s+)?keyboard\b"
        r"|\bscrolls?\s+(?:sideways|horizontally)\b|\bhas\s+horizontal\s+(?:scroll|overflow)\b|\bcontent\s+overflows\b", re.I), False),
    # class S and B: statements about what the page or its source does or sets; a [src:...] cite can back these, but not a class A result word
    ("B", "N px", re.compile(r"(?<![\w.#-])\d+(?:\.\d+)?\s?px\b", re.I), False),
    ("B", "N:1", re.compile(r"(?<![\d.:])\d{1,2}(?:\.\d{1,3})?\s?:\s?1(?![\d:])"), False),
)

_ADVISORY = re.compile(r"\b(?:should|shall|must|ought|could|would|might|recommend\w*|suggest\w*|consider\w*|propos\w+|prefer\w*|goals?|plans?\s+to|next\s+steps?|todo|to-do|"
                       r"needs?\s+to|need\s+to|requires?|required|unless|whether|if|aim(?:s|ing)?\s+(?:for|to))\b", re.I)
_IMPERATIVE = re.compile(r"^(?:please\s+)?(?:use|set|add|increase|decrease|reduce|raise|lower|change|make|keep|replace|move|update|apply|remove|avoid|ensure|check|verify|test|run|"
                         r"capture|open|read|see|try|switch|wrap|align|match|swap|rename|split|merge|group|bump|enlarge|shrink|give|put|show|hide|enable|disable|fix|compare|"
                         r"record|mark|write|cite|re-?run|re-?capture|confirm|snap|bring|push|shift|nudge|convert|adjust|tighten|loosen|widen|narrow|lift|restore|resize|trim|bump|clamp)\b", re.I)
_DISCLOSE = re.compile(r"\b(?:unverified|untested|unmeasured|unchecked|unconfirmed)\b"
                       r"|\b(?:not|never|unable\s+to|cannot|can't|couldn't|could\s+not|\w+n't|did\s+not|does\s+not|has\s+not|have\s+not|was\s+not|were\s+not|is\s+not|are\s+not)\b"
                       r"(?:\s+\w+){0,3}?\s+(?:verified|verify|measured|measure|tested|test|checked|check|captured|capture|confirmed|confirm|run|inspected|evaluated|audited|validated|assessed|exercised)\b"
                       r"|\bno\s+(?:measurements?|evidence|capture|verification)\b", re.I)
_SOURCE_VERBS = re.compile(r"\b(?:use|uses|used|using|set|sets|setting|defined?|defines|declared?|declares|specified|configured|styled|defaults?|increased?|decreased?|reduced?|raised?|"
                           r"lowered?|changed?|moved?|updated?|added|adds|removed?|replaced?|bumped|made|makes|grew|shrank|resized|tightened|loosened)\b", re.I)
_LABEL_CONFIDENCE = re.compile(r"\b(?:confidence|status)\s*[:\-]\s*$", re.I)


def detect_claims(plain):
    """Hits for words that assert a verified or measured result: [(class, label, position)]. Empty for questions, advice, and disclosures."""
    text = plain.strip()
    if not text or text.endswith("?"):
        return []
    if _ADVISORY.search(text) or _IMPERATIVE.match(text) or _DISCLOSE.search(text):
        return []
    hits = []
    for cls, label, pattern, guarded in CLAIM_PATTERNS:
        for m in pattern.finditer(plain):
            if guarded:
                before = plain[max(m.start() - 40, 0):m.start()]
                if _NEGCUE.search(before) or _LABEL_CONFIDENCE.search(before):
                    continue
            hits.append((cls, label, m.start()))
            break
    # A bare measurement beside a verb of use or change ("Cards use 16 px padding", "Increased the target to 44 px") says what the code sets, not what was measured.
    if hits and all(cls == "B" for cls, _l, _p in hits) and _SOURCE_VERBS.search(text):
        return []
    return hits


_MEASURED_HARD = re.compile(r"\d+(?:\.\d+)?\s?px\b|\d{1,2}(?:\.\d+)?\s?:\s?1\b|\b(?:FAIL|WARN|INFO)\b|\bfindings?\b|\b(?:passes|passed|fails|failed|meets|conforms|wcag|measured|verified|confirmed|ratio|overflows?)\b"
                            r"|\bno\s+(?:horizontal\s+)?(?:overflow|scroll)|\bconsole\s+errors?\b"
                            r"|\b\d+\s+(?:buttons?|links?|inputs?|images?|headings?|landmarks?|tab\s+stops?|controls?|forms?|tables?|lists?|elements?|nodes?)\b", re.I)
_MEASURED_SOFT = re.compile(r"\b(?:contrast|overflow|targets?|spacing|padding|margins?|gaps?|focus|keyboard|tab\s+order|grid|line[- ]height|font\s+size|type\s+scale|alignment|accessible\s+names?|alt\s+text|heading\s+levels?)\b", re.I)
_LOOKS = re.compile(r"\b(?:looks?|looked|appears?|feels?|seems?|visually|visual\s+impression|reads\s+as|resembles|balanced|composition|hierarchy|impression)\b", re.I)
_ACK_UNMEASURED = re.compile(r"\b(?:unmeasured|not\s+measured|no\s+measurements?|(?:can(?:not|'t)|does\s+not|doesn't|cannot)\s+(?:support|back|show|prove)|no\s+collected\s+facts|looks?\s+only|visual(?:ly)?\s+only)\b", re.I)
_ACK_UNCONFIRMED = re.compile(r"\b(?:unconfirmed|not\s+confirmed|did\s+not\s+render|didn't\s+render|failed\s+to\s+render|never\s+rendered|not\s+rendered|could\s+not\s+be\s+confirmed|"
                              r"state\.confirmed|failed\s+to\s+confirm|was\s+not\s+visible|never\s+appeared|did\s+not\s+appear|not\s+reachable|unreachable|"
                              r"could\s+not\s+be\s+(?:forced|reached|rendered)|matched\s+no\s+element|did\s+not\s+match)\b", re.I)


def is_measured_claim(plain):
    if _MEASURED_HARD.search(plain):
        return True
    return bool(_MEASURED_SOFT.search(plain)) and not _LOOKS.search(plain)


# ----------------------------------------------------------------------------
# evidence root

class Run(object):
    def __init__(self, name, path):
        self.name, self.path = name, path
        self.manifest = None
        self.load_error = None
        self.entries = []
        self.items = []
        self.problems = []
        self.warnings = []
        self.verified = False


class Item(object):
    """One manifest entry, its evidence file, and what verification found."""

    def __init__(self, run, entry):
        self.run, self.entry = run, entry
        self.eid = entry.get("id")
        self.ev = None
        self.loaded = None
        self.problems, self.warnings = [], []
        self.trusted = False
        self.captured = None

    @property
    def cite(self):
        return safe(self.entry.get("cite") or self.eid, 60)

    def get(self, key, default=None):
        return (self.ev or {}).get(key, default) if self.ev else self.entry.get(key, default)

    @property
    def width(self):
        return as_dict(self.get("viewport")).get("width")

    @property
    def state(self):
        return self.get("state")

    @property
    def theme(self):
        return self.get("theme")

    @property
    def measured(self):
        return self.get("measured") is not False

    @property
    def confirmed(self):
        return self.get("state_confirmed") is not False

    def summary(self, tier):
        value = as_dict(self.get("summary")).get(tier)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def rule(self, rid):
        return as_dict(as_dict(self.ev).get("rules")).get(rid)

    def findings(self, rid):
        return [f for f in as_list(as_dict(self.rule(rid)).get("findings")) if isinstance(f, dict)]


class Evidence(object):
    def __init__(self, path, explicit):
        self.path = os.path.abspath(path)
        self.explicit = explicit
        self.exists = os.path.isdir(self.path)
        self.runs = {}
        self.by_id = {}
        self.duplicates = {}
        self.notes = []
        self.cited = set()
        self.widths = set()
        self.states = set()
        self._shell = {}
        if self.exists:
            self._scan()

    # -- scanning -----------------------------------------------------------
    def _scan(self):
        if os.path.isfile(os.path.join(self.path, "manifest.json")):
            candidates = [(os.path.basename(self.path) or "run", self.path)]
        else:
            try:
                names = sorted(os.listdir(self.path))
            except OSError:
                self.notes.append("the evidence folder cannot be listed")
                return
            candidates = [(n, os.path.join(self.path, n)) for n in names if os.path.isfile(os.path.join(self.path, n, "manifest.json"))]
        for name, path in candidates[:MAX_RUNS]:
            run = Run(name, path)
            self.runs[name] = run
            try:
                run.manifest = json.loads(read_bytes(os.path.join(path, "manifest.json"), MAX_JSON_BYTES).decode("utf-8"))
                if not isinstance(run.manifest, dict) or not isinstance(run.manifest.get("evidence"), list):
                    raise ValueError("manifest.json has no evidence list")
            except (OSError, ValueError, RecursionError) as exc:
                run.load_error = safe(exc, 60)
                run.manifest = None
                self.notes.append("run %s: manifest.json is unreadable (%s)" % (safe(name, 40), run.load_error))
                continue
            for entry in run.manifest["evidence"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not re.match(r"^E\d{1,6}$", entry["id"]):
                    continue
                run.entries.append(entry)
                self.by_id.setdefault(entry["id"], []).append((run, len(run.entries) - 1))
                width = as_dict(entry.get("viewport")).get("width")
                if isinstance(width, int):
                    self.widths.add(width)
                state = entry.get("state")
                if isinstance(state, str) and state not in ("default", "foreign"):
                    self.states.add(state)
        self.duplicates = dict((eid, [r.name for r, _i in c]) for eid, c in self.by_id.items() if len(c) > 1)

    # -- routes ---------------------------------------------------------------
    @staticmethod
    def routes_of(run):
        """The routes a `routes` run recorded: [{label, url, path}]. Empty for any other run."""
        return [r for r in as_list(as_dict(run.manifest).get("routes")) if isinstance(r, dict) and isinstance(r.get("label"), str)]

    def is_route(self, run, label):
        return any(r["label"] == label for r in self.routes_of(run))

    def shell_table(self, run):
        """The parsed shell.json of a routes run, or None when the run has none, it cannot be read, or its hash differs from the manifest
        (a changed file is reported as an integrity failure, and its numbers are not trusted)."""
        if run.name not in self._shell:
            table = None
            for artifact in as_list(as_dict(run.manifest).get("artifacts")):
                artifact = as_dict(artifact)
                if artifact.get("kind") == "shell-table" and isinstance(artifact.get("file"), str):
                    path = self._child(run, artifact["file"])
                    try:
                        data = read_bytes(path, MAX_JSON_BYTES) if path is not None else b""
                        parsed = json.loads(data.decode("utf-8")) if data and sha256_bytes(data) == artifact.get("sha256") else None
                    except (OSError, ValueError, RecursionError):
                        parsed = None
                    table = parsed if isinstance(parsed, dict) else None
                    break
            self._shell[run.name] = table
        return self._shell[run.name]

    def suggest(self, cite):
        """Cites of existing evidence with the same width, state, and theme as a cite that did not resolve."""
        out = []
        for eid in sorted(self.by_id):
            for run, index in self.by_id[eid]:
                entry = run.entries[index]
                view = as_dict(entry.get("viewport"))
                if cite.kind == "full" and view.get("width") == cite.width and entry.get("state") == cite.state and entry.get("theme") == cite.theme:
                    out.append(safe(entry.get("cite") or eid, 60))
        return out[:3]

    def resolve(self, cite):
        candidates = self.by_id.get(cite.eid)
        if not candidates:
            return None
        chosen = candidates[0]
        if len(candidates) > 1 and cite.kind == "full":
            for run, index in candidates:
                if run.entries[index].get("cite") == cite.text:
                    chosen = (run, index)
                    break
        run, index = chosen
        self.verify_run(run)
        return run.items[index]

    # -- verification -------------------------------------------------------
    def _child(self, run, name):
        """Path of a file the manifest names, or None when the name is not a plain file name."""
        if not isinstance(name, str) or not name or name in (".", os.pardir) or os.sep in name or "/" in name or "\x00" in name or name.startswith("."):
            return None
        path = os.path.join(run.path, name)
        real_root = os.path.realpath(self.path)
        real = os.path.realpath(path)
        if os.path.commonpath([real_root, real]) != real_root:
            return None
        return path

    def _hash_file(self, run, name, expected, problems, loose):
        """Hash a file the manifest names. A missing file goes to loose (a failure only for cited evidence); a changed or unsafe one to problems."""
        path = self._child(run, name)
        if path is None:
            problems.append("manifest names %s, which is not a plain file name inside the run" % safe(name, 40))
            return
        try:
            actual = sha256_file(path)
        except (OSError, ValueError):
            loose.append("%s is missing or unreadable" % safe(name, 40))
            return
        if expected is None or actual != expected:
            problems.append("%s does not match its recorded sha256 (the file changed after capture)" % safe(name, 40))

    def verify_run(self, run):
        if run.verified:
            return
        run.verified = True
        manifest = run.manifest or {}
        if manifest.get("schema") != 1:
            run.problems.append("manifest schema %s is not supported (this checker reads schema 1)" % safe(manifest.get("schema"), 12))
        if manifest.get("complete") is False:
            run.warnings.append("manifest says the run is not complete, so it was interrupted")
        names = [e.get("id") for e in run.entries]
        for eid in sorted(set(n for n in names if names.count(n) > 1)):
            run.problems.append("id %s appears more than once in the manifest" % eid)
        for entry in run.entries:
            run.items.append(self._verify_entry(run, entry))
        for item in run.items:
            if item.eid not in self.cited and item.problems:
                run.problems.append("%s (not cited) %s" % (item.eid, item.problems[0]))
        self._verify_totals(run)
        for artifact in as_list(manifest.get("artifacts")):
            artifact = as_dict(artifact)
            self._hash_file(run, artifact.get("file"), artifact.get("sha256"), run.problems, run.problems)
        self._verify_shell_table(run)
        if manifest.get("run") and run.name != manifest.get("run"):
            run.warnings.append("the folder is named %s but the manifest says run %s (renamed?)" % (safe(run.name, 40), safe(manifest.get("run"), 40)))

    def _verify_entry(self, run, entry):
        item = Item(run, entry)
        problems = item.problems
        eid = entry.get("id")
        cited = eid in self.cited
        loose = problems if cited else run.warnings
        if eid in self.duplicates:
            loose.append("id %s is used by runs %s; ids must be unique across the evidence root" % (eid, ", ".join(safe(n, 30) for n in self.duplicates[eid][:4])))
        ev = None
        jname = entry.get("json")
        jpath = self._child(run, jname)
        if jpath is None:
            problems.append("manifest names %s, which is not a plain file name inside the run" % safe(jname, 40))
        else:
            try:
                data = read_bytes(jpath, MAX_JSON_BYTES)
            except (OSError, ValueError):
                loose.append("%s is missing or unreadable" % safe(jname, 40))
            else:
                if sha256_bytes(data) != entry.get("json_sha256"):
                    problems.append("%s does not match its recorded sha256 (the file changed after capture)" % safe(jname, 40))
                else:
                    try:
                        ev = json.loads(data.decode("utf-8"))
                    except (ValueError, RecursionError):
                        problems.append("%s is not valid JSON" % safe(jname, 40))
        full = as_dict(ev.get("full")) if isinstance(ev, dict) else {}
        squint = as_dict(ev.get("squint")) if isinstance(ev, dict) else {}
        for key, expected in (("png", entry.get("sha256")), ("full", full.get("sha256")), ("squint", squint.get("sha256"))):
            if entry.get(key) is not None and (key == "png" or isinstance(ev, dict)):
                self._hash_file(run, entry.get(key), expected, problems, loose)
        if ev is not None:
            try:
                self._check_evidence(run, entry, ev, problems)
            except (TypeError, ValueError, KeyError, AttributeError, IndexError, RecursionError):
                problems.append("the evidence file has a structure this checker cannot read")
        item.loaded = ev if isinstance(ev, dict) else None
        item.ev = item.loaded if not problems else None
        item.trusted = item.ev is not None
        if item.loaded is not None:
            item.captured = utc_epoch(item.loaded.get("captured_utc"))
            collector = as_dict(item.loaded.get("collector"))
            skipped = [rid for rid, r in as_dict(item.loaded.get("rules")).items() if isinstance(r, dict) and r.get("status") == "skipped"]
            if as_list(collector.get("errors")) or skipped:
                item.warnings.append(("claims.integrity", "%s has collector errors or skipped rules (%s); a skipped rule is never a pass" % (
                    safe(entry.get("cite") or eid, 40), ", ".join(safe(s, 30) for s in skipped[:3]) or "collector")))
            if (item.loaded.get("kind") == "page" and item.loaded.get("state") not in ("default", None) and not as_dict(item.loaded.get("state_detail")).get("expect")
                    and not self.is_route(run, item.loaded.get("state"))):
                item.warnings.append(("claims.unconfirmed-state", "%s was never checked: its states spec entry has no expect condition, and the tool counts such a state as rendered without looking" % safe(entry.get("cite") or eid, 40)))
        return item

    def _check_evidence(self, run, entry, ev, problems):
        """Cross-file invariants from EVIDENCE.md that can be checked from files. Returns False when something disagrees."""
        start = len(problems)
        if not isinstance(ev, dict):
            problems.append("the evidence file is not a JSON object")
            return False
        eid = entry.get("id")
        view = as_dict(ev.get("viewport"))
        if ev.get("schema") != 1:
            problems.append("evidence schema %s is not 1" % safe(ev.get("schema"), 12))
        if ev.get("id") != eid:
            problems.append("the file says id %s but the manifest says %s" % (safe(ev.get("id"), 12), eid))
        built = "%s@%s:%s#%s" % (ev.get("id"), view.get("width"), ev.get("state"), ev.get("theme"))
        if ev.get("cite") != built:
            problems.append("cite %s does not equal %s built from the file's own width, state, and theme" % (safe(ev.get("cite"), 50), safe(built, 50)))
        if entry.get("cite") != ev.get("cite"):
            problems.append("the manifest cite %s differs from the file cite %s" % (safe(entry.get("cite"), 50), safe(ev.get("cite"), 50)))
        for key in ("kind", "measured", "theme", "state", "state_confirmed", "viewport", "summary"):
            if entry.get(key) != ev.get(key):
                problems.append("the manifest entry and the evidence file disagree on %s" % key)
        kind = ev.get("kind")
        if (kind == "foreign") != (ev.get("measured") is False):
            problems.append("kind %s and measured %s do not go together" % (safe(kind, 12), safe(ev.get("measured"), 8)))
        rules = as_dict(ev.get("rules"))
        tally, seen = dict((t, 0) for t in TIERS), set()
        for rid, rule in rules.items():
            for finding in as_list(as_dict(rule).get("findings")):
                tier = as_dict(finding).get("tier")
                if isinstance(tier, str) and tier in tally:
                    tally[tier] += 1
                fid = as_dict(finding).get("id")
                if isinstance(fid, str):
                    if fid in seen:
                        problems.append("finding id %s appears twice in the file" % safe(fid, 40))
                    seen.add(fid)
        if as_dict(ev.get("summary")) != tally:
            problems.append("summary %s does not equal the findings in rules %s" % (safe(json.dumps(ev.get("summary"), sort_keys=True), 50), safe(json.dumps(tally, sort_keys=True), 50)))
        truth_failed = as_dict(rules.get("viewport.truth")).get("status") == "fail"
        if kind in ("page", "foreign") and not truth_failed:
            width, dpr = view.get("width"), view.get("dpr")
            size = ev.get("png_size")
            if is_num(width) and is_num(dpr) and isinstance(size, list) and size and is_num(size[0]) and abs(size[0] - width * dpr) > 1:
                problems.append("the screenshot is %s px wide but %s px at %sx should be %s px, and viewport.truth did not fail" % (
                    safe(size[0], 8), safe(width, 8), safe(dpr, 8), num_text(width * dpr)))
        if kind == "page" and not truth_failed:
            if as_dict(ev.get("facts")).get("innerWidth") != view.get("width"):
                problems.append("facts.innerWidth is %s but the requested width is %s, and viewport.truth did not fail" % (
                    safe(as_dict(ev.get("facts")).get("innerWidth"), 8), safe(view.get("width"), 8)))
        if ev.get("measured") is not False:
            confirmed_rule = as_dict(rules.get("state.confirmed")).get("status")
            if ev.get("state_confirmed") is False and confirmed_rule != "fail":
                problems.append("state_confirmed is false but rule state.confirmed did not fail")
            if ev.get("state_confirmed") is True and confirmed_rule == "fail":
                problems.append("state_confirmed is true but rule state.confirmed failed")
        if ev.get("png") != entry.get("png") or ev.get("sha256") != entry.get("sha256"):
            problems.append("the manifest and the evidence file disagree on the screenshot or its hash")
        png_name = ev.get("png")
        if png_name:
            path = self._child(run, png_name)
            if path is not None and os.path.isfile(path):
                try:
                    dims = png_size(path)
                except OSError:
                    dims = None
                if dims is None:
                    problems.append("%s is not a PNG file" % safe(png_name, 40))
                elif isinstance(ev.get("png_size"), list) and list(dims) != ev.get("png_size"):
                    problems.append("%s is %dx%d but the evidence file records %s" % (safe(png_name, 40), dims[0], dims[1], safe(json.dumps(ev.get("png_size")), 20)))
        elif ev.get("sha256") is not None or entry.get("sha256") is not None:
            problems.append("a screenshot hash is recorded but no screenshot file is named")
        return len(problems) == start

    def _verify_totals(self, run):
        manifest, entries = run.manifest or {}, run.entries
        summary = as_dict(manifest.get("summary"))
        sums = dict((t, 0) for t in TIERS)
        failing = 0
        for entry, item in zip(entries, run.items):
            for tier in TIERS:
                value = as_dict(entry.get("summary")).get(tier)
                sums[tier] += value if isinstance(value, int) and not isinstance(value, bool) else 0
            ev = item.loaded
            if isinstance(ev, dict):
                live = any(as_dict(f).get("tier") == "FAIL" and not as_dict(f).get("expected")
                           for r in as_dict(ev.get("rules")).values() for f in as_list(as_dict(r).get("findings")))
            else:
                live = as_dict(entry.get("summary")).get("FAIL", 0) not in (0, None)
            failing += 1 if live else 0
        for tier in TIERS:
            if summary.get(tier) != sums[tier]:
                run.problems.append("manifest summary %s is %s but the entries add up to %d" % (tier, safe(summary.get(tier), 10), sums[tier]))
        expect = {"evidence": len(entries), "captures": sum(1 for e in entries if e.get("kind") == "page"), "failing_evidence": failing}
        for key, value in expect.items():
            if summary.get(key) != value:
                run.problems.append("manifest summary %s is %s but the run has %d" % (key, safe(summary.get(key), 10), value))
        if manifest.get("exit_code") != (1 if failing else 0):
            run.problems.append("manifest exit_code %s does not follow failing_evidence %d" % (safe(manifest.get("exit_code"), 6), failing))

    def _verify_shell_table(self, run):
        """shell.json repeats numbers the evidence files hold. A table whose hash was recomputed after an edit still has to agree with them; the first
        disagreement is the run's one failure."""
        table = self.shell_table(run)
        if table is None:
            return
        by_id = dict((item.eid, item.loaded) for item in run.items)
        for kind, routes in as_dict(table.get("table")).items():
            for route, cells in as_dict(routes).items():
                for cell in as_dict(cells).values():
                    cell = as_dict(cell)
                    if cell.get("id") not in by_id:
                        run.problems.append("shell.json names the evidence %s, which the run does not have" % safe(cell.get("id"), 12))
                        self._shell[run.name] = None
                        return
                    ev = by_id[cell.get("id")]
                    if not isinstance(ev, dict):
                        continue  # a file that cannot be read is reported on its own
                    facts = as_dict(ev.get("facts"))
                    rec = as_dict(facts.get("content")) if kind == "content" else as_dict(as_dict(facts.get("shell")).get(kind))
                    if bool(cell.get("present")) != bool(rec):
                        run.problems.append("shell.json says the %s is %s on %s, but %s.json says the opposite" % (safe(kind, 20), "present" if cell.get("present") else "missing", safe(route, 30), safe(cell.get("id"), 12)))
                        self._shell[run.name] = None
                        return
                    if not rec:
                        continue
                    if kind == "content":
                        column = as_list(rec.get("column")) + [None, None]
                        pairs = (("left", column[0]), ("width", rec.get("width")))
                    else:
                        rect = as_list(rec.get("rect")) + [None] * 4
                        pairs = (("x", rect[0]), ("y", rect[1]), ("width", rect[2]), ("height", rect[3]))
                    for name, recorded in pairs:
                        if not (is_num(recorded) and is_num(cell.get(name)) and abs(recorded - cell[name]) < 1e-6):
                            run.problems.append("shell.json says the %s %s of %s is %s, but %s.json records %s" % (
                                safe(kind, 20), name, safe(route, 30), safe(cell.get(name), 12), safe(cell.get("id"), 12), safe(recorded, 12)))
                            self._shell[run.name] = None  # a table that disagrees with the evidence is not trusted for numbers
                            return

    # -- compare artifacts --------------------------------------------------
    def compare_artifacts(self, run):
        out = []
        try:
            names = sorted(os.listdir(run.path))
        except OSError:
            return out
        for name in names:
            if name.startswith("compare-") and name.endswith(".json"):
                try:
                    data = json.loads(read_bytes(os.path.join(run.path, name), MAX_JSON_BYTES).decode("utf-8"))
                except (OSError, ValueError, RecursionError):
                    self.notes.append("%s in run %s could not be read, so its text changes were not checked" % (safe(name, 40), safe(run.name, 40)))
                    continue
                if isinstance(data, dict):
                    out.append((name, data))
        return out


# ----------------------------------------------------------------------------
# text that compare reports as changed

_TEXT_KINDS = ("dropped", "new", "changed", "reordered")


def summarize_compare(data):
    """Counts and strings per kind of text change, across every pair in a compare artifact. `unclassified` lists the dropped texts that compare
    could not explain (as slop it flagged, an edit in place, text merged into another string, or text moved to another capture)."""
    per = dict((k, []) for k in _TEXT_KINDS)
    texts = {"dropped": [], "changed": []}
    unclassified = []
    for pair in as_list(data.get("pairs")):
        text = as_dict(as_dict(pair).get("text"))
        how = dict((str(as_dict(d).get("text")), as_dict(d).get("class")) for d in as_list(text.get("drops")))
        unclassified.extend(str(x) for x in as_list(text.get("dropped")) if how.get(str(x), "unclassified") == "unclassified")
        for kind in _TEXT_KINDS:
            items = as_list(text.get(kind))
            per[kind].append(len(items))
            if kind == "dropped":
                texts["dropped"].extend(str(x) for x in items)
            if kind == "changed":
                texts["changed"].extend(str(as_dict(x).get("from", "")) for x in items)
    distinct = {}
    for kind in _TEXT_KINDS:
        values = []
        for pair in as_list(data.get("pairs")):
            for x in as_list(as_dict(as_dict(pair).get("text")).get(kind)):
                values.append(json.dumps(x, sort_keys=True))
        distinct[kind] = len(set(values))
    summary = as_dict(data.get("summary"))
    accepted = {}
    totals = {}
    for kind in _TEXT_KINDS:
        totals[kind] = sum(per[kind])
        nums = set(per[kind]) | set([totals[kind], distinct[kind]])
        if isinstance(summary.get(kind), int):
            nums.add(summary[kind])
        accepted[kind] = nums
    return {"totals": totals, "accepted": accepted, "dropped": texts["dropped"], "changed_from": texts["changed"], "unclassified": unclassified}


# ----------------------------------------------------------------------------
# the checker

_ADVICE_FIX = {
    "claims.unknown-id": "cite an id printed in the run's manifest.json, or write (unverified: reason).",
    "claims.integrity": "capture again with ui_measure.py and cite the new evidence; never edit evidence files.",
    "claims.fit": "cite the evidence captured at the width, state, and theme the sentence describes, or correct the sentence.",
    "claims.unconfirmed-state": "make the state render until state.confirmed passes, capture again, or write (unverified: state would not render).",
    "claims.number": "quote the value the cited evidence file records, or write (unverified: reason).",
    "claims.unmeasured": "cite measured evidence from ui_measure.py capture, or limit the sentence to how the screenshot looks.",
    "claims.stale": "capture again after the last source change and cite the new evidence.",
    "claims.text-changes": "add a line such as \"Text changes: 2 dropped, 1 new, 0 changed, 0 reordered\", or quote each dropped text; then quote every unclassified drop with what became of it (deleted as slop, merged into another element, moved, or replaced).",
    "claims.src-citation": "cite a file and line that exist under the source root.",
    "claims.uncited": "add an evidence id such as [E03@390:default#light], or write (unverified: reason).",
    "claims.unverified-marker": "write (unverified: reason) with at least three words of reason.",
}

_WIDTH_CONTEXT = (
    re.compile(r"(?<!cap )(?<!capped )(?<!max )(?<!maxes )(?<!limit )(?<!limited )(?<!centered )(?<!stops )(?<!clamps )\b(?:at|@)\s*(\d{3,4}(?:\s*px)?(?:\s*(?:,|and|or|&|/|to|-)\s*\d{3,4}(?:\s*px)?)*)(?!\d)", re.I),
    re.compile(r"\b(\d{3,4})\s*-?\s*px\s*-?\s*(?:wide\s+)?(?:viewports?|screens?|breakpoints?|devices?|phones?|tablets?|desktops?|layouts?|views?|versions?|renders?|renderings?|captures?|screenshots?|widths?)\b", re.I),
    re.compile(r"\b(?:viewport|screen|breakpoint|device)\s+(?:width\s+)?(?:of\s+)?(\d{3,4})\b", re.I),
)
_THEME_PATTERNS = (
    re.compile(r"\b(light|dark)[ -](?:mode|theme|scheme|variant|capture|screenshot|render(?:ing)?|palette)\b", re.I),
    re.compile(r"\b(?:theme|mode|scheme)\s*[:=]?\s*(light|dark)\b", re.I),
    re.compile(r"\bprefers-color-scheme\s*[:=]?\s*(light|dark)\b", re.I),
    re.compile(r"\bin\s+(?:the\s+)?(light|dark)(?=\s*(?:[,.;:)]|and\b|or\b|at\b|on\b|for\b|only\b|too\b|as\b|than\b|$))", re.I),
    re.compile(r"#(light|dark)\b", re.I),
)
_THEME_PAIR = re.compile(r"\b(light|dark)\s*(?:and|or|/|&)\s*(light|dark)\b", re.I)
_BREAKPOINT_CUE = re.compile(r"(?:breakpoints?|media\s+quer\w+|collaps\w+|switch\w*|stack\w*|reflow\w*|kicks?\s+in|snaps?|breaks?\s+(?:to|into)|wraps?|hides?)\W+(?:\w+\W+){0,4}$", re.I)
_STATE_MENTION = re.compile(r"\b([a-z][a-z0-9_-]{1,39})\s+state\b", re.I)

_RATIO = re.compile(r"(?<![\d.:])(\d{1,2}(?:\.\d{1,3})?)\s?:\s?1(?![\d:])")
_PX = re.compile(r"(?<![\w.#-])(\d+(?:\.\d+)?)\s?px\b", re.I)
_THRESHOLD_CUE = re.compile(r"(?:needs?|minimum|min\b|at\s+least|under|below|above|exceeds?|guidelines?|recommend\w*|requires?|required|limit|max(?:imum)?|threshold|spec\b|standard|wcag|"
                            r"aim(?:s|ing)?\s+for|should|use|using|try|set\s+to|change\s+to|switch\s+to|make\s+it|nearest|instead|round(?:ed)?\s+to|snap(?:ped)?\s+to|on-grid)\W+(?:\w+\W+){0,3}$", re.I)
_TIER_A = re.compile(r"(?<![\w.])(\d+|(?i:no|zero|none))\s+(?:(?:justified|remaining|open|new|unresolved|known|accepted|residual|blocking|outstanding|total|distinct|real|actual|final|existing|unjustified|noted|expected|unexpected|tier)\s+)?(FAIL|WARN|INFO)s?\b")
_TIER_B = re.compile(r"\b(FAIL|WARN|INFO)\s*[:=]?\s*(\d+)\b")
_TIER_PAIR = re.compile(r"\b(FAIL|WARN|INFO)\s*[:=]?\s*(\d+)\s*(?:to|->|\u2192)\s*(\d+)\b")
_FINDINGS = re.compile(r"(?<![\w.])(\d+|(?i:no|zero|none))\s+(?:(?:justified|remaining|open|new|unresolved|known|accepted|residual|blocking|outstanding|total|distinct)\s+)?findings?\b", re.I)
_FROM_CUE = re.compile(r"(?:from|was|were|previously|before|baseline|originally|down\s+from|up\s+from|reduced\s+from)\W+(?:\w+\W+){0,2}$", re.I)
_RATIO_CUE = re.compile(r"\b(?:needs?|minimum|min|requires?|required|requirements?|thresholds?|limit|at\s+least|guidelines?|standards?|wcag|aa{1,2}|aim(?:s|ing)?\s+for|should|must|below|under|short\s+of)\b", re.I)
_THRESHOLD_AFTER = re.compile(r"^\s*(?:px\s*)?(?:\w+\s+){0,2}?(?:minimum|min|guidelines?|requirements?|thresholds?|limits?|standards?|recommendations?|maximum|max|rules?|spec|grid|or\s+(?:larger|more|greater|bigger|taller|wider|higher|lower|less|smaller))\b", re.I)
_AFTER_CUE = re.compile(r"(?:now|to|\u2192|->|reach(?:es|ing)?|up\s+to|improved\s+to|raised\s+to|increased\s+to|rises?\s+to|becomes?|became)\W+(?:\w+\W+){0,2}$", re.I)
_CONSOLE_N = re.compile(r"(?<![\w.])(\d+|(?i:no|zero))\s+console\s+errors?\b", re.I)
_TABSTOP_N = re.compile(r"(?<![\w.])(\d+)\s+(?:keyboard\s+)?tab\s+stops?\b", re.I)
_TARGET_N = re.compile(r"(?<![\w.])(\d+)\s+(?:tap\s+|touch\s+|click\s+)?targets?\b", re.I)
_NUMBER_IN_TEXT = re.compile(r"(?<![\w.#])(\d+(?:\.\d+)?)")

_TOPICS = (  # (topic, words that name it, rules whose findings carry its numbers, unused)
    ("overflow", re.compile(r"overflow\w*|scroll(?:s|ed|ing)?\s+(?:sideways|horizontally)|horizontal(?:ly)?\s+scroll\w*|wider\s+than|past\s+the\s+(?:right\s+)?edge|too\s+wide", re.I), ("overflow.page",), ()),
    ("control height", re.compile(r"(?:control|input|button|field)\s+heights?", re.I), ("control.height", "grid.size"), ()),
    ("target size", re.compile(r"\btargets?\b|hit[ -]areas?|tap\s+areas?|touch\s+areas?|\bbuttons?\b|\bcontrols?\b|\bicons?\b", re.I), ("target.size", "control.height"), ()),
    ("font size", re.compile(r"font[ -]?sizes?|text\s+size|type\s+size|smallest\s+(?:text|font|type)|fine[ -]print|\bfont\b", re.I), ("type.scale",), ()),
    ("spacing", re.compile(r"padding|margins?|\bgaps?\b|spacing|off[- ]grid|\bgrid\b", re.I), ("grid.spacing", "grid.size", "component.conformance", "scale.spacing", "align.near_miss"), ()),
)

_WORDS = re.compile(r"[A-Za-z][A-Za-z-]*")
# Fields of a finding that hold a px measurement of the thing found. control.height and scale.spacing carry a difference and a count, so their worst text is used instead.
_PX_FIELDS = {"overflow.page": ("value",), "target.size": ("value",), "grid.spacing": ("value",), "grid.size": ("value",), "type.scale": ("value",),
              "align.near_miss": ("value",), "component.conformance": ("value", "threshold")}


_CONTROL_NOUN = re.compile(r"\b(?:buttons?|controls?|inputs?|fields?|selects?|icons?|textbox(?:es)?|checkbox(?:es)?|chips?|toggles?)\b", re.I)
_FONT_NOUN = re.compile(r"font[ -]?sizes?|type\s+size|text\s+size|body\s+(?:text|copy)|caption|fine[ -]print|small\s+print", re.I)


def inventory_topic(plain, start, end):
    """"control height" for a px value that is a control's height ("a 20 px square icon button", "buttons are 44 px tall"), "font size" for type, else None."""
    left = re.split(r"[,;:()]", plain[max(start - 80, 0):start])[-1]
    right = re.split(r"[,;:().]", plain[end:end + 80], maxsplit=1)[0]
    left_words, right_words = _WORDS.findall(left), _WORDS.findall(right)
    if _FONT_NOUN.search(" ".join(left_words[-4:] + right_words[:2])):
        return "font size"
    heightish = (right_words[:1] and right_words[0].lower() in ("tall", "high", "height", "square")) or re.search(r"\bheights?\s+(?:of\s+|is\s+|was\s+|are\s+|to\s+|at\s+)?$", left, re.I)
    if heightish and _CONTROL_NOUN.search(" ".join(left_words[-5:] + right_words[:3])):
        return "control height"
    return None


def topic_near(plain, start, end):
    """The topic a px value belongs to: a topic word within four words before it or three after it, in the same clause. None when there is none."""
    left = re.split(r"[,;:()]", plain[max(start - 80, 0):start])[-1]
    right = re.split(r"[,;:().]", plain[end:end + 80], maxsplit=1)[0]
    window = " ".join(_WORDS.findall(left)[-4:] + ["|"] + _WORDS.findall(right)[:3])
    for topic in _TOPICS:
        if topic[1].search(window):
            return topic
    return None


_SHELL_LANDMARKS = (  # (landmark in shell.json, words that name it)
    ("title", re.compile(r"\bpage\s+titles?\b|\btitles?\b", re.I)),
    ("banner", re.compile(r"\b(?:header|banner|top\s?bar|app\s?bar)\b", re.I)),
    ("nav", re.compile(r"\b(?:rail|side\s?bar|side\s?nav\w*|nav(?:igation)?(?:\s+(?:items?|links?|rows?|bar))?)\b", re.I)),
    ("contentinfo", re.compile(r"\b(?:footer|contentinfo)\b", re.I)),
    ("content", re.compile(r"\b(?:content\s+(?:column|width|area|region)|column|main\s+(?:region|column))\b", re.I)),
)
_SHELL_NAME = {"banner": "header", "nav": "nav", "contentinfo": "footer", "title": "page title", "content": "content column"}


def shell_near(plain, start, end):
    """The shell landmark a px value is about: the landmark word nearest to it within four words before it or three after it, in the same clause."""
    left = re.split(r"[,;:()]", plain[max(start - 80, 0):start])[-1]
    right = re.split(r"[,;:().]", plain[end:end + 80], maxsplit=1)[0]
    window = " ".join(_WORDS.findall(left)[-4:] + ["|"] + _WORDS.findall(right)[:3])
    bar = window.index("|")
    best = None
    for name, pattern in _SHELL_LANDMARKS:
        for m in pattern.finditer(window):
            distance = bar - m.end() if m.end() <= bar else m.start() - bar
            if best is None or distance < best[0]:
                best = (distance, name)
    return best[1] if best else None


_SHELL_CONTRAST = re.compile(r"\b(?:but|while|whereas|than|other|others|elsewhere|rest|most|dominant|majority|versus|vs|instead|compared|against|unlike|differs?)\b", re.I)
_Z_SHELL = re.compile(r"\bno\s+shell\s+(?:differences?|drift|findings?|issues?)\b|\b(?:shell|rail|header|footer|nav(?:igation)?|page\s+title|content\s+column)\b[^.;]{0,50}"
                      r"\b(?:match(?:es)?|consistent|identical|the\s+same|unchanged)\b[^.;]{0,40}\b(?:every|all|each|both)\s+(?:\w+\s+)?routes?\b", re.I)
_Z_OVERFLOW = re.compile(
    r"\bno\s+(?:horizontal\s+|sideways\s+|page\s+)?(?:overflow|scrolling|scroll|scrollbars?)\b|\b(?:does(?:n't| not)|do(?:n't| not)|never)\s+(?:\w+\s+){0,2}(?:overflow|scroll\s+(?:sideways|horizontally))"
    r"|\bwithout\s+(?:any\s+)?(?:horizontal\s+)?(?:overflow|scrolling)\b|\bnothing\s+overflows\b", re.I)
_Z_CONTRAST = re.compile(r"\bcontrast\b[^.;]{0,40}\b(?:passes|pass|passed|meets|meet|sufficient|compliant)\b|\b(?:passes|meets|exceeds)\s+(?:wcag\s+)?(?:aa\s+)?(?:text\s+)?contrast\b|\bno\s+contrast\s+(?:failures?|issues?|problems?|violations?|findings?)\b", re.I)
_Z_CONSOLE = re.compile(r"\bconsole\s+(?:is\s+)?clean\b", re.I)
_Z_WCAG = re.compile(r"\b(?:passes|meets|conforms\s+to|complies\s+with|is\s+compliant\s+with)\s+wcag\b|\bwcag(?:\s+2(?:\.\d)?)?\s+(?:aaa?\s+)?(?:conformant|compliant)\b|\baaa?\s+(?:conformant|compliant)\b", re.I)
_Z_ALLPASS = re.compile(r"\b(?:everything|all\s+(?:\w+\s+){0,2}(?:checks?|rules?|tests?|captures?|widths?|themes?|states?))\s+(?:pass(?:es|ed)?|(?:is|are)\s+(?:clean|green))\b", re.I)
_WCAG_RULES = ("contrast.text", "contrast.ui", "target.size", "focus.visible", "a11y.names")
_Z_HEDGE = re.compile(r"\b(?:except|other\s+than|apart\s+from|besides|but|although|though|however|yet|still|only|baseline|before|previously|originally)\b", re.I)


_GROUP_GAP = re.compile(r"^[\s\[\],;/&+]*(?:(?:and|or|to|through|thru|-|\u2013|\u2014)[\s\[\],;/&+]*)?$", re.I)


def cite_groups(text, cites):
    """Runs of cites separated only by punctuation, "and", or "to": one run is one claim's evidence. Returns [[start, end, [cites]]]."""
    groups = []
    for cite in sorted(cites, key=lambda c: (c.pos, c.expanded)):
        if groups and (cite.pos < groups[-1][1] or _GROUP_GAP.match(text[groups[-1][1]:cite.pos])):
            groups[-1][1] = max(groups[-1][1], cite.end)
            groups[-1][2].append(cite)
        else:
            groups.append([cite.pos, cite.end, [cite]])
    return groups


class Checker(object):
    def __init__(self, report, evidence, results, source, report_path):
        self.report, self.evidence, self.results = report, evidence, results
        self.source, self.report_path = source, report_path
        self.cite_count = 0
        self.marker_count = 0
        self.uncited = 0
        self.occurrences = {}
        self.run_lines = {}
        self.item_objects = {}
        self.bare_lines = []
        self.internal_errors = []
        self.src_cited = False
        self.report_norm = norm_text("\n".join(report.lines))

    # -- driver -------------------------------------------------------------
    def run(self):
        for sentence in self.report.sentences:
            prepare_sentence(sentence)
            for cite in sentence.cites:
                self.evidence.cited.add(cite.eid)
        for sentence in self.report.sentences:
            try:
                self._sentence(sentence)
            except Exception as exc:  # fail closed: say where, keep every other finding
                self.internal_errors.append("line %d: %s" % (sentence.line, type(exc).__name__))
        for step in (self._item_findings, self._text_changes, self._global_warnings):
            try:
                step()
            except Exception as exc:
                self.internal_errors.append("%s: %s" % (step.__name__.strip("_"), type(exc).__name__))

    def fail(self, rule, line, message, fix=None):
        self.results.fail(rule, line, message, fix or _ADVICE_FIX[rule])

    # -- one sentence -------------------------------------------------------
    def _sentence(self, sentence):
        self._markers(sentence)
        self._sources(sentence)
        resolved = []
        for cite in sentence.cites:
            self.cite_count += 1
            item = self._resolve(sentence, cite)
            if item is not None:
                resolved.append((cite, item))
        trusted = [(c, i) for c, i in resolved if i.trusted]
        fit_ok = []
        for cite, item in trusted:
            if self._fit(sentence, cite, item):
                fit_ok.append((cite, item))
        if resolved:
            self._unconfirmed(sentence, resolved)
            self._unmeasured(sentence, resolved)
        if fit_ok:
            self._prose_fit(sentence, fit_ok)
            self._numbers(sentence, fit_ok)
        self._uncited_check(sentence)

    def _resolve(self, sentence, cite):
        if cite.kind == "partial":
            item = self.evidence.resolve(cite)
            if item is None:
                self.fail("claims.unknown-id", cite.line, "%s is not in the evidence root, and the citation is incomplete" % safe(cite.raw, 40),
                          "cite the id exactly as the manifest prints it, such as E03@390:default#light.")
                return None
            self.fail("claims.fit", cite.line, "%s is an incomplete citation: the state and theme are missing, so nothing can be matched" % safe(cite.raw, 40),
                      "write the whole cite, %s." % item.cite)
            return None
        item = self.evidence.resolve(cite)
        if item is None:
            hint = ""
            near = self.evidence.suggest(cite)
            if near:
                hint = " (evidence with that width, state, and theme: %s)" % ", ".join(near)
            where = "the evidence root has no item with that id"
            if not self.evidence.exists:
                where = "there is no evidence folder at %s" % safe(os.path.relpath(self.evidence.path), 60)
            elif not self.evidence.by_id:
                where = "the evidence folder holds no runs"
                if os.path.isdir(os.path.join(self.evidence.path, EVIDENCE_DIR)):
                    where += " (it contains %s: pass that folder as --evidence)" % EVIDENCE_DIR
            self.fail("claims.unknown-id", cite.line, "%s: %s%s" % (safe(cite.text, 60), where, hint))
            return None
        cite.item = item
        key = (item.run.name, item.eid, id(item))
        self.item_objects[key] = item
        self.occurrences.setdefault(key, []).append(cite.line)
        self.run_lines.setdefault(item.run.name, []).append(cite.line)
        if cite.kind == "bare" and not cite.expanded:
            self.bare_lines.append(cite.line)
        return item

    # -- cite against evidence ------------------------------------------------
    def _fit(self, sentence, cite, item):
        """The width, state, and theme in the cite match the evidence. Returns True when nothing disagrees."""
        if cite.kind != "full":
            return True
        parts = []
        if cite.width != item.width:
            parts.append("width %s but the evidence is %s" % (cite.width, safe(item.width, 8)))
        if cite.state != item.state:
            parts.append("state %s but the evidence is %s" % (safe(cite.state, 40), safe(item.state, 40)))
        if cite.theme != item.theme:
            parts.append("theme %s but the evidence is %s" % (cite.theme, safe(item.theme, 8)))
        if not parts:
            return True
        self.fail("claims.fit", cite.line, "%s says %s" % (safe(cite.text, 60), "; ".join(parts)),
                  "cite %s, or cite the capture that has the width, state, and theme you describe." % item.cite)
        return False

    def _unconfirmed(self, sentence, resolved):
        if _ACK_UNCONFIRMED.search(sentence.plain):
            return
        for cite, item in resolved:
            if item.trusted and item.measured and not item.confirmed:
                self.fail("claims.unconfirmed-state", cite.line, "%s never confirmed its state (state_confirmed is false), so it cannot back a claim" % safe(item.cite, 60))

    def _unmeasured(self, sentence, resolved):
        resolved = [(c, i) for c, i in resolved if i.trusted]
        if not resolved or any(i.measured for _c, i in resolved):
            return
        plain = sentence.plain
        if _ACK_UNMEASURED.search(plain) or not is_measured_claim(plain):
            return
        cite = resolved[0][0]
        self.fail("claims.unmeasured", cite.line, "%s is a registered screenshot with no measurements, so it cannot support a measured claim" % safe(resolved[0][1].cite, 60))

    # -- prose against the cites it sits beside ---------------------------------
    def _group_items(self, sentence, pairs, position):
        """Items cited by the first run of cites after position (else the last run before it). None when that run holds a cite that was not checked."""
        known = dict((id(c), i) for c, i in pairs)
        groups = cite_groups(sentence.text, sentence.cites)
        after = [g for g in groups if g[0] >= position]
        before = [g for g in groups if g[1] <= position]
        chosen = after[0] if after else (before[-1] if before else None)
        if chosen is None or any(id(c) not in known for c in chosen[2]):
            return None
        return [known[id(c)] for c in chosen[2]]

    def _prose_fit(self, sentence, pairs):
        items = [i for _c, i in pairs]
        themes = set(i.theme for i in items if i.theme in ("light", "dark"))
        states = set(str(i.state).replace("pseudo-", "") for i in items if i.state)
        plain = sentence.plain
        known = set(KNOWN_WIDTHS) | set(self.evidence.widths)
        line = pairs[0][0].line
        mentions = []
        for pattern in _WIDTH_CONTEXT:
            for m in pattern.finditer(plain):
                if _BREAKPOINT_CUE.search(plain[max(m.start() - 50, 0):m.start()]):
                    continue
                for x in re.finditer(r"\d{3,4}", m.group(1)):
                    if int(x.group(0)) in known:
                        mentions.append((int(x.group(0)), m.start(1) + x.end()))
        stray = []
        for width, end in mentions:
            group = self._group_items(sentence, pairs, end)
            if group is None:
                continue
            have = sorted(set(i.width for i in group if isinstance(i.width, int)))
            if have and width not in have and width not in [w for w, _h in stray]:
                stray.append((width, have))
        for width, have in stray[:2]:
            self.fail("claims.fit", line, "the sentence names %d px but the evidence cited beside it was captured at %s px" % (width, ", ".join(str(w) for w in have)),
                      "cite the capture made at %d px, or correct the width in the sentence." % width)
        if themes:
            said = set()
            for pattern in _THEME_PATTERNS:
                for m in pattern.finditer(plain):
                    said.add(m.group(1).lower())
            for m in _THEME_PAIR.finditer(plain):
                said.update((m.group(1).lower(), m.group(2).lower()))
            if said and not (said & themes):
                self.fail("claims.fit", line, "the sentence is about the %s theme but the cited evidence was captured in %s" % (
                    "/".join(sorted(said)), "/".join(sorted(themes))), "cite the capture made in the %s theme, or correct the sentence." % sorted(said)[0])
        self._route_fit(sentence, pairs, line)
        known_states = set(self.evidence.states)
        mentions = set()
        for m in _STATE_MENTION.finditer(plain):
            name = m.group(1).lower()
            for candidate in known_states:
                if str(candidate).lower().replace("pseudo-", "") == name:
                    mentions.add(name)
        if mentions and not (mentions & set(s.lower() for s in states)):
            self.fail("claims.fit", line, "the sentence is about the %s state but the cited evidence is the %s state" % (
                "/".join(sorted(mentions)), "/".join(sorted(safe(s, 30) for s in states))),
                "cite the capture of the %s state, or correct the sentence." % sorted(mentions)[0])

    def _route_fit(self, sentence, pairs, line):
        """A sentence about a route of a routes run cites that route: it names the path of another route of the run and none that it cites."""
        by_run = {}
        for _c, item in pairs:
            if self.evidence.is_route(item.run, item.state):
                by_run.setdefault(item.run.name, (item.run, set()))[1].add(item.state)
        text = blank_spans(sentence.text, sentence.spans)
        for run, cited in by_run.values():
            names = {}
            for route in self.evidence.routes_of(run):
                name = str(route.get("path") or "").split("?")[0]
                if name:
                    names.setdefault(name, set()).add(route["label"])
            if len(names) < 2:
                continue
            found = set()
            for name in sorted(names, key=len, reverse=True):
                if re.search(r"(?<![\w/.:~-])%s(?![\w/-])" % re.escape(name), text):
                    found |= names[name]
            if found and not (found & cited):
                shown = ", ".join(sorted(safe(label, 40) for label in found)[:3])
                self.fail("claims.fit", line, "the sentence is about the %s route but the cited evidence is the %s route" % (shown, ", ".join(sorted(safe(c, 40) for c in cited)[:3])),
                          "cite the capture of the %s route, or correct the route named in the sentence." % sorted(found)[0])

    # -- numbers ------------------------------------------------------------
    def _numbers(self, sentence, pairs):
        plain = sentence.plain
        items = [i for _c, i in pairs]
        evs = [i for i in items if i.trusted and isinstance(i.ev, dict) and i.measured]
        if not evs:
            return
        self._tier_counts(sentence, plain, evs, pairs)
        self._ratios(sentence, plain, evs)
        self._px_values(sentence, plain, evs, items)
        self._noun_counts(sentence, plain, evs)
        self._zero_claims(sentence, plain, evs)

    def _tier_accept(self, evs, tier):
        values, total = set(), 0
        run_values = {}
        for item in evs:
            count = item.summary(tier)
            values.add(count)
            total += count
            run_values[item.run.name] = as_dict(as_dict(item.run.manifest).get("summary")).get(tier)
        values.add(total)
        runs = [v for v in run_values.values() if isinstance(v, int)]
        values.update(runs)
        if len(runs) > 1:
            values.add(sum(runs))
        return values

    def _findings_accept(self, evs):
        values, totals = set(), dict((t, 0) for t in TIERS)
        for item in evs:
            counts = dict((t, item.summary(t)) for t in TIERS)
            for t in TIERS:
                totals[t] += counts[t]
            values.update((counts["FAIL"], counts["WARN"], counts["INFO"], counts["FAIL"] + counts["WARN"], sum(counts.values())))
        values.update((totals["FAIL"], totals["WARN"], totals["INFO"], totals["FAIL"] + totals["WARN"], sum(totals.values())))
        for run in set(i.run for i in evs):
            s = as_dict(as_dict(run.manifest).get("summary"))
            if all(isinstance(s.get(t), int) for t in TIERS):
                values.update((s["FAIL"], s["WARN"], s["INFO"], s["FAIL"] + s["WARN"], s["FAIL"] + s["WARN"] + s["INFO"]))
        return values

    @staticmethod
    def _past_tense(plain, start, evs):
        """True when the words before a number say it describes an older state ("was", "from", "baseline") and no cited item is a baseline run that could back it."""
        if any(as_dict(i.ev).get("phase") == "baseline" for i in evs):
            return False
        return bool(_FROM_CUE.search(plain[max(start - 30, 0):start]))

    @staticmethod
    def _advice(plain, pos):
        """True when the number sits in an instruction or a recommendation ("Snap the padding to 12 px", "should be 16 px"), which is not a claim about the evidence."""
        start = max(plain.rfind(",", 0, pos), plain.rfind(";", 0, pos), plain.rfind(":", 0, pos))
        clause = plain[start + 1:pos]
        return bool(_ADVISORY.search(clause) or _IMPERATIVE.match(clause.strip()))

    @staticmethod
    def _word_number(token):
        return 0 if token.lower() in ("no", "zero", "none") else int(token)

    def _tier_counts(self, sentence, plain, evs, pairs):
        claimed = {}
        for m in _TIER_A.finditer(plain):
            if self._past_tense(plain, m.start(), evs) or self._advice(plain, m.start()):
                continue
            claimed.setdefault(m.group(2), []).append((self._word_number(m.group(1)), m.start()))
        paired = set()
        for m in _TIER_PAIR.finditer(plain):
            paired.add((m.group(1), m.start()))
            claimed.setdefault(m.group(1), []).append((int(m.group(2)), m.start()))
            claimed[m.group(1)].append((int(m.group(3)), m.start()))
        for m in _TIER_B.finditer(plain):
            if (m.group(1), m.start()) in paired or self._past_tense(plain, m.start(), evs) or self._advice(plain, m.start()):
                continue
            claimed.setdefault(m.group(1), []).append((int(m.group(2)), m.start()))
        groups = cite_groups(sentence.text, sentence.cites)
        for tier, entries in claimed.items():
            if len(set(n for n, _p in entries)) >= 2 and len(groups) >= 2:
                for n, pos in entries:
                    near = self._group_items(sentence, pairs, pos)
                    if near is not None and n not in self._tier_accept(near, tier):
                        have = ", ".join("%s %d" % (safe(i.cite.split(":")[0], 20), i.summary(tier)) for i in near[:4])
                        self.fail("claims.number", sentence.line_at(pos), "the sentence says %d %s beside evidence that records %s" % (n, tier, have))
                continue
            accept = self._tier_accept(evs, tier)
            if any(n in accept for n, _p in entries):
                continue
            n, pos = entries[0]
            have = ", ".join("%s %d" % (safe(i.cite.split(":")[0], 20), i.summary(tier)) for i in evs[:4])
            self.fail("claims.number", sentence.line_at(pos), "the sentence says %d %s but the cited evidence records %s (%s)" % (n, tier, ", ".join(str(v) for v in sorted(accept)[:5]), have))
        for m in _FINDINGS.finditer(plain):
            if self._past_tense(plain, m.start(), evs) or self._advice(plain, m.start()):
                continue
            n = self._word_number(m.group(1))
            accept = self._findings_accept(evs)
            if n not in accept:
                self.fail("claims.number", sentence.line_at(m.start()), "the sentence says %d findings but the cited evidence records %s" % (n, ", ".join(str(v) for v in sorted(accept)[:6])))

    def _ratio_values(self, evs):
        """(measured, limits): contrast ratios the cited evidence records as measured, and the limits those findings were judged against."""
        measured, limits = set(), set()
        for item in evs:
            for rid in ("contrast.text", "contrast.ui", "focus.visible"):
                for f in item.findings(rid):
                    if f.get("unit") != ":1":
                        continue
                    if is_num(f.get("value")):
                        measured.add(float(f["value"]))
                    if is_num(f.get("threshold")):
                        limits.add(float(f["threshold"]))
                    for example in as_list(f.get("examples")):
                        if is_num(as_dict(example).get("value")):
                            measured.add(float(as_dict(example)["value"]))
        return measured, limits

    def _ratios(self, sentence, plain, evs):
        if re.search(r"\b(?:aspect|gap|spacing|rhythm|image|photo|video|golden|modular)\b", plain, re.I):
            return
        measured, limits = self._ratio_values(evs)
        if not measured:
            return
        # A limit is accepted only when the sentence uses it as one ("needs 4.5:1", "WCAG AA"); "contrast is 4.5:1" must match a measured value.
        quoted_limits = (limits | set((3.0, 4.5, 7.0))) if _RATIO_CUE.search(plain) else set()
        # A ratio that passes is never recorded, so a number after "to" or "now" may describe a clean item cited in the same sentence.
        clean_cited = any(not self._ratio_values([i])[0] for i in evs)
        for m in _RATIO.finditer(plain):
            token = m.group(1)
            if clean_cited and _AFTER_CUE.search(plain[max(m.start() - 30, 0):m.start()]):
                continue
            if self._past_tense(plain, m.start(), evs) or float(token) == 1.0 or self._advice(plain, m.start()):
                continue
            decimals = len(token.split(".")[1]) if "." in token else 0
            tolerance = 0.5 if decimals == 0 else 10.0 ** -decimals
            if any(abs(float(token) - v) <= tolerance + 1e-9 for v in measured | quoted_limits):
                continue
            real = ", ".join("%s:1" % num_text(v) for v in sorted(measured)[:4])
            self.fail("claims.number", sentence.line_at(m.start()), "the sentence says %s:1 but the cited evidence records contrast of %s" % (token, real))

    def _topic_values(self, evs, rules, topic, inventory=None):
        """(accepted px values, values to show) for a px claim about a topic, or (None, None) when the cited evidence has no finding for it.
        Only measurements count: a finding's value where it is a size, the sizes in its worst text, and the inventories of control heights and font sizes.
        Limits and suggested fixes do not, so a claim that sits exactly on a limit still has to match something measured."""
        accept, shown = set(), set()
        has = False
        for item in evs:
            for rid in rules:
                for f in item.findings(rid):
                    has = True
                    for key in _PX_FIELDS.get(rid, ()):
                        if is_num(f.get(key)):
                            accept.add(float(f[key]))
                            if key == "value":
                                shown.add(float(f[key]))
                    worst = re.sub(r"\([^)]*\)|->.*$", " ", str(f.get("worst") or ""))
                    for m in _NUMBER_IN_TEXT.finditer(worst):
                        accept.add(float(m.group(1)))
                    for example in as_list(f.get("examples")):
                        rect = as_dict(example).get("rect")
                        if isinstance(rect, list) and len(rect) == 4 and all(is_num(v) for v in rect[2:]):
                            accept.update((float(rect[2]), float(rect[3])))
            names = ()
            if inventory == "control height" or (topic == "target size" and item.ev):
                names = as_dict(as_dict(item.ev.get("tokens")).get("control_heights"))
            elif inventory == "font size":
                names = as_dict(as_dict(item.ev.get("tokens")).get("font_size"))
            for name in names:
                try:
                    accept.add(float(name))
                    shown.add(float(name))
                    has = True
                except ValueError:
                    pass
            if topic == "overflow" and has:
                facts = as_dict(item.ev.get("facts"))
                sw, cw, iw = facts.get("scrollWidth"), facts.get("clientWidth"), facts.get("innerWidth")
                for a, b in ((sw, cw), (sw, iw)):
                    if is_num(a) and is_num(b):
                        accept.add(float(a - b))
                for v in (sw, facts.get("bodyScrollWidth"), cw, iw):
                    if is_num(v):
                        accept.add(float(v))
        return (accept, shown) if has else (None, None)

    def _shell_numbers(self, items, landmark, contrast):
        """The numbers shell.json records for one landmark in the routes evidence a sentence cites: each item's own cell, and, when the sentence sets
        one route against the others, the dominant value at its width and the differences between them. None when the table has no such cell, so
        nothing is guessed."""
        accept, found = set(), False
        for item in items:
            table = as_dict(self.evidence.shell_table(item.run))
            cell = "%s#%s" % (item.width, item.theme)
            own = as_dict(as_dict(as_dict(table.get("table")).get(landmark)).get(item.state)).get(cell)
            dominant = as_dict(as_dict(as_dict(table.get("dominant")).get(landmark)).get(cell))
            for source in (own, dominant if contrast else {}):
                for key, value in as_dict(source).items():
                    if is_num(value) and key != "routes":
                        accept.add(float(value))
                        found = True
            if contrast:
                for key, value in as_dict(own).items():  # "16 px wider than the other routes" is a difference between two recorded values
                    if is_num(value) and is_num(dominant.get(key)):
                        accept.add(abs(float(value) - float(dominant[key])))
                for f in item.findings("shell.consistency"):
                    if is_num(f.get("value")) and f.get("unit") == "px":
                        accept.add(float(f["value"]))
        return accept if found else None

    def _px_values(self, sentence, plain, evs, items):
        widths = set(i.width for i in items if isinstance(i.width, int)) | set(self.evidence.widths)
        context_widths = set()
        for pattern in _WIDTH_CONTEXT:
            for m in pattern.finditer(plain):
                for n in re.findall(r"\d{3,4}", m.group(1)):
                    context_widths.add(int(n))
        shell_items = [i for i in evs if self.evidence.is_route(i.run, i.state) and self.evidence.shell_table(i.run) is not None]
        for m in _PX.finditer(plain):
            value = float(m.group(1))
            if int(value) == value and (int(value) in context_widths or int(value) in widths and value >= 280):
                continue
            if (_THRESHOLD_CUE.search(plain[max(m.start() - 40, 0):m.start()]) or _THRESHOLD_AFTER.search(plain[m.end():m.end() + 40])
                    or self._past_tense(plain, m.start(), evs) or self._advice(plain, m.start())):
                continue
            landmark = shell_near(plain, m.start(), m.end()) if shell_items else None
            if landmark is not None:
                accept = self._shell_numbers(shell_items, landmark, bool(_SHELL_CONTRAST.search(plain)))
                if accept is not None:
                    if not any(abs(value - v) <= 0.5 + 1e-9 for v in accept):
                        self.fail("claims.number", sentence.line_at(m.start()), "the sentence says %s px for the %s but shell.json records %s px for it" % (
                            m.group(1), _SHELL_NAME[landmark], ", ".join(num_text(v) for v in sorted(accept)[:8])))
                    continue
            inventory = inventory_topic(plain, m.start(), m.end())
            if inventory is not None:
                topic = [t for t in _TOPICS if t[0] == inventory][0]
            else:
                topic = topic_near(plain, m.start(), m.end())
            if topic is None:
                continue
            accept, shown = self._topic_values(evs, topic[2], topic[0], inventory)
            if accept is None:
                continue
            if any(abs(value - v) <= 0.5 + 1e-9 for v in accept):
                continue
            self.fail("claims.number", sentence.line_at(m.start()), "the sentence says %s px for %s but the cited evidence records %s px for it" % (
                m.group(1), topic[0], ", ".join(num_text(v) for v in sorted(shown)[:6]) or "other values"))

    def _noun_counts(self, sentence, plain, evs):
        pool = [i for i in evs if i.get("kind") == "page" and i.rule("console.errors")]
        for m in _CONSOLE_N.finditer(plain):
            if not pool:
                break
            n = self._word_number(m.group(1))
            counts = []
            for item in pool:
                errors = [c for c in as_list(item.ev.get("console")) if isinstance(c, dict) and c.get("level") == "error"]
                weights = [(c, c.get("count", 1) if is_num(c.get("count", 1)) else 1) for c in errors]
                counts.append((sum(w for c, w in weights if not c.get("expected")), sum(w for _c, w in weights)))
            if n == 0:
                bad = all(live > 0 for live, _every in counts)
                have = ", ".join(str(live) for live, _e in counts[:4])
            else:
                accepted = set(v for pair in counts for v in pair) | set([sum(live for live, _e in counts)])
                bad = n not in accepted
                have = ", ".join(str(v) for v in sorted(accepted))
            if bad:
                self.fail("claims.number", sentence.line_at(m.start()), "the sentence says %d console errors but the cited evidence records %s" % (n, have))
        for m in _TABSTOP_N.finditer(plain):
            n = int(m.group(1))
            stops = [as_dict(i.ev.get("counts")).get("tab_stops") for i in evs]
            stops = [s for s in stops if isinstance(s, int)]
            if stops and n not in stops:
                self.fail("claims.number", sentence.line_at(m.start()), "the sentence says %d tab stops but the cited evidence recorded %s" % (n, ", ".join(str(s) for s in sorted(set(stops)))))
        for m in _TARGET_N.finditer(plain):
            n = int(m.group(1))
            counts = set()
            for item in evs:
                for f in item.findings("target.size"):
                    if is_num(f.get("count")):
                        counts.add(int(f["count"]))
            if counts:
                counts.add(sum(counts))
                if n not in counts:
                    self.fail("claims.number", sentence.line_at(m.start()), "the sentence says %d targets but the cited evidence records %s" % (n, ", ".join(str(v) for v in sorted(counts))))

    def _live_fail(self, item, rid):
        return [f for f in item.findings(rid) if f.get("tier") == "FAIL" and not f.get("expected")]

    def _zero_claims(self, sentence, plain, evs):
        """A sentence that says nothing failed, cited only to evidence that records a failure. Every cited item must disagree before this fires."""
        if _Z_HEDGE.search(plain):
            return
        page_items = [i for i in evs if i.get("kind") == "page"]
        checks = (
            (_Z_OVERFLOW, ("overflow.page",), "no overflow", page_items),
            (_Z_CONTRAST, ("contrast.text",), "passing contrast", [i for i in evs if i.rule("contrast.text")]),
            (_Z_CONSOLE, ("console.errors",), "no console errors", page_items),
            (_Z_WCAG, _WCAG_RULES, "WCAG conformance", page_items),
            (_Z_ALLPASS, None, "that everything passes", page_items),
        )
        shell_pool = [i for i in evs if self.evidence.is_route(i.run, i.state) and i.rule("shell.consistency")]
        m = _Z_SHELL.search(plain)
        if m and shell_pool and all([f for f in i.findings("shell.consistency") if f.get("tier") in ("FAIL", "WARN") and not f.get("expected")] for i in shell_pool):
            self.fail("claims.number", sentence.line_at(m.start()), "the sentence claims a matching shell but the cited evidence (%s) has a shell.consistency finding" % (
                ", ".join(safe(i.cite.split(":")[0], 20) for i in shell_pool[:3])), "correct the claim to match the findings, or cite the routes where the shell matches.")
        for pattern, rules, label, pool in checks:
            m = pattern.search(plain)
            if not m or not pool:
                continue
            if rules is None:
                bad = all(i.summary("FAIL") > 0 for i in pool)
                named = "FAIL findings"
            else:
                bad = all(any(self._live_fail(i, r) for r in rules) for i in pool)
                named = "a FAIL finding for rule %s" % next((r for r in rules if self._live_fail(pool[0], r)), rules[0])
            if bad:
                worst = [safe(i.cite.split(":")[0], 20) for i in pool[:3]]
                self.fail("claims.number", sentence.line_at(m.start()), "the sentence claims %s but the cited evidence (%s) has %s" % (label, ", ".join(worst), named),
                          "correct the claim to match the findings, or cite the capture where it holds.")

    # -- uncited claims and markers ---------------------------------------------
    def _uncited_check(self, sentence):
        if sentence.kind in ("heading", "quote") or sentence.skip:
            return
        if sentence.cites or sentence.markers:
            return
        hits = detect_claims(sentence.plain)
        if not hits:
            return
        if sentence.srcs and all(cls in ("B", "S") for cls, _l, _p in hits):
            return
        self.uncited += 1
        labels = ", ".join(sorted(set(label for _c, label, _p in hits))[:3])
        snippet = safe(sentence.plain.strip(), 60)
        severity_fail = self.results.strict
        message = "asserts a result (%s) with no evidence cite or unverified marker: %s" % (labels, snippet)
        if severity_fail:
            self.results.fail("claims.uncited", sentence.line_at(hits[0][2]), message, _ADVICE_FIX["claims.uncited"])
        else:
            self.results.warn("claims.uncited", sentence.line_at(hits[0][2]), message, _ADVICE_FIX["claims.uncited"])

    def _markers(self, sentence):
        for marker in sentence.markers:
            self.marker_count += 1
            if marker["words"] < 3:
                self.fail("claims.unverified-marker", sentence.line_at(marker["start"]),
                          "(unverified) has %d word%s of reason, and a reason needs at least three" % (marker["words"], "" if marker["words"] == 1 else "s"))

    # -- [src:path:line] -----------------------------------------------------
    def _sources(self, sentence):
        for ref in sentence.srcs:
            self.src_cited = True
            line = sentence.line_at(ref["pos"])
            problem = self._src_problem(ref)
            if problem is None:
                continue
            if self.source is not None:
                self.fail("claims.src-citation", line, problem)
            else:
                self.results.warn("claims.src-citation", line, problem + " (no --source given, so the current directory was the root)",
                                  "pass --source ROOT so source citations are checked against the project root.")

    def _src_problem(self, ref):
        base = os.path.realpath(self.source if self.source is not None else os.getcwd())
        path = ref["path"]
        shown = safe(path, 60)
        if not path or os.path.isabs(path) or "\x00" in path:
            return "[src:%s] must be a relative path under the source root" % shown
        candidate = os.path.realpath(os.path.join(base, path))
        if os.path.commonpath([base, candidate]) != base:
            return "[src:%s] points outside the source root" % shown
        if not os.path.isfile(candidate):
            return "[src:%s] names a file that does not exist under the source root" % shown
        first, last = ref["start_line"], ref["end_line"]
        if first is None:
            return None
        need = last if last is not None else first
        if first < 1 or (last is not None and last < first):
            return "[src:%s] has an invalid line number" % shown
        try:
            have = count_lines(candidate, need)
        except OSError:
            return "[src:%s] cannot be read" % shown
        if have < need:
            return "[src:%s] cites line %d but the file has %d lines" % (shown, need, have)
        return None

    # -- once-per-item and once-per-run findings --------------------------------
    def _item_findings(self):
        newest = None
        if self.source is not None:
            newest = newest_source(self.source, set((os.path.realpath(self.evidence.path), os.path.realpath(self.report_path))))
        stale = {}
        for key in sorted(self.item_objects, key=lambda k: (min(self.occurrences[k]), k[1])):
            item = self.item_objects[key]
            lines = sorted(set(self.occurrences[key]))
            first, others = lines[0], lines[1:]
            tail = " (also cited on line%s %s)" % ("" if len(others) == 1 else "s", ", ".join(str(n) for n in others[:6])) if others else ""
            for problem in item.problems:
                self.fail("claims.integrity", first, "%s: %s%s" % (safe(item.cite, 60), problem, tail))
            for rule, warning in item.warnings:
                self.results.warn(rule, first, warning, "add an expect condition to the state in the states spec and capture again.")
            baseline = as_dict(item.ev).get("phase") == "baseline"  # the state before the work: cited on purpose, and older than the edits by design
            if newest is not None and item.trusted and not baseline and item.captured is not None and newest[0] is not None and int(newest[0]) > item.captured:
                stale.setdefault(item.run.name, []).append((first, int(newest[0]) - item.captured, item))
        for name in sorted(stale, key=lambda n: min(f for f, _s, _i in stale[n])):
            rows = stale[name]
            seconds = min(sec for _f, sec, _i in rows)
            cites = ", ".join(safe(i.cite.split(":")[0], 20) for _f, _s, i in rows[:4]) + (", +%d more" % (len(rows) - 4) if len(rows) > 4 else "")
            self.fail("claims.stale", min(f for f, _s, _i in rows), "source changed after capture: %s is at least %d second%s newer than the %d cited capture%s of run %s (%s)" % (
                safe(newest[1], 60), seconds, "" if seconds == 1 else "s", len(rows), "" if len(rows) == 1 else "s", safe(name, 40), cites))
        for name in sorted(self.run_lines):
            run = self.evidence.runs.get(name)
            if run is None:
                continue
            first = min(self.run_lines[name])
            for problem in run.problems:
                self.fail("claims.integrity", first, "run %s: %s" % (safe(run.name, 40), problem))
            for warning in run.warnings:
                self.results.warn("claims.integrity", first, "run %s: %s" % (safe(run.name, 40), warning), "capture again if you need a complete run.")
        if self.bare_lines:
            lines = sorted(set(self.bare_lines))
            self.results.warn("claims.fit", lines[0], "%d citation%s give only an id, so width, state, and theme were not matched (lines %s)" % (
                len(self.bare_lines), "" if len(self.bare_lines) == 1 else "s", ", ".join(str(n) for n in lines[:6])),
                "write the whole cite as the manifest prints it, such as E03@390:default#light.")

    def _text_changes(self):
        for name in sorted(self.run_lines):
            run = self.evidence.runs.get(name)
            if run is None or run.manifest is None:
                continue
            first = min(self.run_lines[name])
            for artifact, data in self.evidence.compare_artifacts(run):
                info = summarize_compare(data)
                if not any(info["totals"].values()):
                    continue
                ok, reason, at, note = self._disclosed(info)
                self._unclassified(info, artifact, run, first)
                if note:
                    self.results.warn("claims.text-changes", first, "%s: %s" % (safe(artifact, 50), note), "state the changed count too, as the compare summary prints it.")
                if not ok:
                    counts = ", ".join("%d %s" % (info["totals"][k], k) for k in _TEXT_KINDS)
                    quoted = info["dropped"][:1] + info["changed_from"][:1]
                    sample = (" First dropped or changed text: \"%s\"." % safe(quoted[0], 60)) if quoted else ""
                    self.fail("claims.text-changes", at or first, "%s lists text changes (%s) for the cited run %s, and the report does not disclose them: %s.%s" % (
                        safe(artifact, 50), counts, safe(run.name, 40), reason, sample))

    _CLASS_WORDS = (
        re.compile(r"\b(?:deleted|removed|cut|dropped)\b.*\bslop\b|\bslop\b.*\b(?:deleted|removed|cut)\b|\bas slop\b"),
        re.compile(r"\b(?:merged|folded|combined|collapsed)\b"),
        re.compile(r"\b(?:moved|relocated)\b"),
        re.compile(r"\b(?:replaced|reworded|rewritten|renamed|swapped)\b"),
    )

    def _classified(self, text):
        """True when a line of the report quotes `text` and says what became of it: deleted as slop, merged, moved, or replaced. The quote is cut out
        of the line first, so a dropped text that happens to contain the word "moved" classifies nothing."""
        needle = norm_text(re.sub(r"\.\.\.$", "", text.strip()))
        if len(needle) > 40:
            needle = needle[:40].rstrip()
        if not needle:
            return True
        for raw in self.report.lines:
            low = norm_text(raw)
            if needle not in low:
                continue
            if len(needle) < 4 and not re.search(r"[\"\u201c'`]\s*%s\s*[\"\u201d'`]" % re.escape(needle), low):
                continue
            rest = low.replace(needle, " ")
            if any(rx.search(rest) for rx in self._CLASS_WORDS):
                return True
        return False

    def _unclassified(self, info, artifact, run, first):
        """Every dropped text that compare could not explain has to be quoted in the report with what became of it."""
        missing = [t for t in info["unclassified"] if t.strip() and not self._classified(t)]
        if not missing:
            return
        self.fail("claims.text-changes", first, "%s lists %d dropped text item%s that compare could not classify for the cited run %s, and the report does not say what became of %s: "
                  "quote each one and say it was deleted as slop, merged into another element, moved, or replaced. First: \"%s\"%s." % (
                      safe(artifact, 50), len(missing), "" if len(missing) == 1 else "s", safe(run.name, 40), "it" if len(missing) == 1 else "them",
                      safe(missing[0], 60), "" if len(missing) == 1 else " (and %d more)" % (len(missing) - 1)))

    def _disclosed(self, info):
        """(disclosed, reason, line of the report's own text-changes line when it has one, note). Either a text changes line with the right counts, or every dropped text quoted.
        Dropped, new, and reordered text must be counted when above zero. Changed text must be counted only when nothing else changed; otherwise leaving it out earns a note."""
        totals = info["totals"]
        required = [k for k in ("dropped", "new", "reordered") if totals[k]] or (["changed"] if totals["changed"] else [])
        lines = [(n, t) for n, t in self.report.text_lines()]
        reason, at = "no line mentions text changes with the counts", None
        for index, (number, text) in enumerate(lines):
            if not re.search(r"\btext\s+changes?\b", text, re.I):
                continue
            window = " ".join(t for _m, t in lines[index:index + 4])
            missing, wrong, stated = [], [], set()
            for kind in _TEXT_KINDS:
                found = self._counts_for(window, kind)
                if found:
                    stated.add(kind)
                    if not (found & info["accepted"][kind]):
                        wrong.append("%s %s (compare has %d)" % (kind, "/".join(str(v) for v in sorted(found)), totals[kind]))
                elif kind in required:
                    missing.append(kind)
            if not missing and not wrong:
                note = None
                if totals["changed"] and "changed" not in stated:
                    note = "%d changed text item%s are not counted in the text changes line" % (totals["changed"], "" if totals["changed"] == 1 else "s")
                return True, "", None, note
            reason = "the text changes line is missing counts for %s%s" % (", ".join(missing) or "none", ("; wrong: " + ", ".join(wrong)) if wrong else "")
            at = number
        dropped = [t for t in info["dropped"] if t.strip()]
        if dropped and all(self._quoted(t) for t in dropped):
            return True, "", None, None
        return False, reason, at, None

    @staticmethod
    def _counts_for(window, kind):
        found = set()
        for m in re.finditer(r"(\d+|(?i:no|zero|none))\s+(?:\w+\s+)?%s\b" % kind, window, re.I):
            found.add(0 if m.group(1).lower() in ("no", "zero", "none") else int(m.group(1)))
        for m in re.finditer(r"\b%s\s*[:=]?\s*(\d+)\b" % kind, window, re.I):
            found.add(int(m.group(1)))
        return found

    def _quoted(self, text):
        needle = norm_text(re.sub(r"\.\.\.$", "", text.strip()))
        if len(needle) > 40:
            needle = needle[:40].rstrip()
        if not needle:
            return True
        if len(needle) < 4:
            return bool(re.search(r"[\"\u201c'`]\s*%s\s*[\"\u201d'`]" % re.escape(needle), self.report_norm))
        return needle in self.report_norm

    def _global_warnings(self):
        if self.source is None and self.src_cited:
            self.results.warn("claims.src-citation", 0, "[src:path:line] citations were checked against the current directory, not a project root",
                              "pass --source ROOT so source citations are checked against the project root.")
        if self.source is None and self.evidence.cited:
            self.results.warn("claims.stale", 0, "staleness was not checked: pass --source ROOT to compare source files with the capture times",
                              "run again with --source . (the project root).")
        for note in self.evidence.notes:
            self.results.warn("claims.integrity", 0, note, "check the evidence folder.")


# ----------------------------------------------------------------------------
# files

def count_lines(path, need):
    """Number of lines in a file, counting at most as far as need."""
    count, last, eof = 0, b"", False
    with open(path, "rb") as handle:
        while count < need:
            chunk = handle.read(1 << 16)
            if not chunk:
                eof = True
                break
            count += chunk.count(b"\n")
            last = chunk[-1:]
    if eof and last and last != b"\n":
        count += 1
    return count


def newest_source(root, skip):
    """(mtime, relative path) of the newest web source file under root, or (None, None). Build output, dependencies, and the evidence folder are skipped.
    A root with no web source at all is judged by every visible file instead."""
    web, anything, seen = (None, None), (None, None), 0
    root = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        real = os.path.realpath(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d))
                             and os.path.realpath(os.path.join(dirpath, d)) not in skip)
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = os.path.join(real, name)
            if os.path.islink(full) or os.path.realpath(full) in skip:
                continue
            try:
                mtime = os.stat(full).st_mtime
            except OSError:
                continue
            seen += 1
            found = (mtime, os.path.relpath(full, root))
            if anything[0] is None or mtime > anything[0]:
                anything = found
            if os.path.splitext(name)[1].lower() in SOURCE_EXTENSIONS and (web[0] is None or mtime > web[0]):
                web = found
            if seen >= MAX_SOURCE_FILES:
                break
        if seen >= MAX_SOURCE_FILES:
            break
    return web if web[0] is not None else anything


# ----------------------------------------------------------------------------
# output

def summary_line(checker, results, verified):
    failures = results.count("FAIL")
    uncited_mode = "FAIL" if results.strict else "WARN"
    return "ui_claims: citations checked %d, evidence items verified %d, uncited claims %d (%s), unverified markers %d, skips %d, failures %d => %s" % (
        checker.cite_count, verified, checker.uncited, uncited_mode, checker.marker_count, checker.report.skips, failures, "FAIL" if failures else "PASS")


def render_text(checker, results, verified):
    out = []
    for item in results.sorted_items():
        where = "line %d" % item["line"] if item["line"] else "report"
        out.append("%s %s [%s] %s Fix: %s" % (item["severity"], where, item["rule"], item["message"], item["fix"]))
    out.append(summary_line(checker, results, verified))
    return "\n".join(out) + "\n"


def render_json(checker, results, verified, args):
    failures = results.count("FAIL")
    payload = {
        "tool": {"name": "ui_claims", "version": TOOL_VERSION},
        "report": safe(args.report, 200), "evidence": safe(args.evidence or EVIDENCE_DIR, 200), "source": safe(args.source, 200) if args.source else None,
        "strict": bool(results.strict), "result": "fail" if failures else "pass",
        "summary": {"citations": checker.cite_count, "evidence_verified": verified, "uncited": checker.uncited, "unverified_markers": checker.marker_count,
                    "skips": checker.report.skips, "failures": failures, "warnings": results.count("WARN")},
        "findings": results.sorted_items(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=True) + "\n"


def write_out(text):
    try:
        try:
            sys.stdout.write(text)
        except UnicodeEncodeError:
            sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii"))
        sys.stdout.flush()
    except BrokenPipeError:  # the reader (such as head) closed the pipe: stop quietly, and keep exit-time flushing from complaining
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass


def cmd_check(args):
    try:
        size = os.path.getsize(args.report)
        if size > MAX_REPORT_BYTES:
            raise ToolError("the report is larger than %d bytes" % MAX_REPORT_BYTES)
        with open(args.report, "rb") as handle:
            raw = handle.read().decode("utf-8", "replace")
    except OSError as exc:
        raise ToolError("cannot read the report %s: %s" % (safe(args.report, 80), safe(exc.strerror or exc, 60)))
    if args.source is not None and not os.path.isdir(args.source):
        raise ToolError("--source %s is not a folder" % safe(args.source, 80))
    explicit = args.evidence is not None
    evidence_path = args.evidence if explicit else EVIDENCE_DIR
    if explicit and not os.path.isdir(evidence_path):
        raise ToolError("--evidence %s is not a folder" % safe(evidence_path, 80))
    report = parse_report(raw)
    evidence = Evidence(evidence_path, explicit)
    results = Results(args.strict)
    checker = Checker(report, evidence, results, os.path.abspath(args.source) if args.source is not None else None, args.report)
    checker.run()
    verified = sum(1 for item in checker.item_objects.values() if item.trusted and not item.run.problems)
    write_out(render_json(checker, results, verified, args) if args.format == "json" else render_text(checker, results, verified))
    if checker.internal_errors:
        sys.stderr.write("ui_claims: internal error while checking (%s); treat this report as unchecked\n" % ", ".join(checker.internal_errors[:4]))
        return 2
    return 1 if results.count("FAIL") else 0


def cmd_selftest(args):
    import _claims_selftest
    return _claims_selftest.run(args, sys.modules[__name__])


def build_parser():
    parser = argparse.ArgumentParser(prog="ui_claims.py", description="Check that the evidence a report cites is real, untampered, fresh, and fits what the report says.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="check one report against the evidence folder")
    p.add_argument("report", help="the report to check, such as report.md")
    p.add_argument("--evidence", help="evidence folder (default ./.ui-evidence)")
    p.add_argument("--source", metavar="ROOT", help="project root: enables the staleness check and verifies [src:path:line] citations against it")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--strict", action="store_true", help="make an uncited claim a failure instead of a warning")
    p.set_defaults(func=cmd_check)
    p = sub.add_parser("selftest", help="make real evidence with ui_measure.py and check the checker against good and bad reports")
    p.add_argument("--keep", action="store_true", help="keep the temporary folder")
    p.set_defaults(func=cmd_selftest)
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not argv[0].startswith("-") and argv[0] not in ("check", "selftest"):
        argv = ["check"] + argv
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
        sys.stderr.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ToolError as exc:
        sys.stderr.write("ui_claims: %s\n" % exc)
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("ui_claims: interrupted\n")
        return 2
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
