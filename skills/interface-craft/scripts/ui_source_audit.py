#!/usr/bin/env python3
"""Conservatively scan frontend source for UI resilience and accessibility risks."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Pattern


DEFAULT_EXTENSIONS = {
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".astro",
}

IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "vendor",
    "out",
}

SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: str
    file: str
    line: int
    message: str
    evidence: str
    confidence: str = "candidate"


@dataclass(frozen=True)
class LineRule:
    rule: str
    severity: str
    pattern: Pattern[str]
    message: str


@dataclass(frozen=True)
class TagToken:
    tag: str
    attrs: str
    start: int
    end: int


LINE_RULES = (
    LineRule(
        "disabled-zoom",
        "P1",
        re.compile(
            r"(user-scalable\s*=\s*no|maximum-scale\s*=\s*1(?:[.,\"'\s]|$))", re.I
        ),
        "Browser zoom appears restricted; preserve user scaling.",
    ),
    LineRule(
        "outline-suppressed",
        "P1",
        re.compile(
            r"(outline\s*:\s*(?:none\b|0(?:\.0+)?(?:px|rem|em)?"
            r"(?=(?:\s+[^;}]*)?(?:;|}|$)))|"
            r"(?:^|[\s\"'])outline-none(?:[\s\"']|$))",
            re.I,
        ),
        "Focus outline is suppressed; confirm an equally visible focus-visible replacement.",
    ),
    LineRule(
        "positive-tabindex",
        "P1",
        re.compile(r"tabindex\s*=\s*[\"'{]?[1-9]\d*", re.I),
        "Positive tabindex can create an unexpected keyboard order.",
    ),
    LineRule(
        "masked-horizontal-overflow",
        "P2",
        re.compile(
            r"(overflow-x\s*:\s*hidden\b|(?:^|[\s\"'])overflow-x-hidden(?:[\s\"']|$))",
            re.I,
        ),
        "Horizontal overflow is hidden; verify this does not mask a sizing defect or essential content.",
    ),
    LineRule(
        "scrollbar-hidden",
        "P2",
        re.compile(
            r"(?<![-\w])(?:scrollbar-width|-ms-overflow-style)\s*:\s*none\b|"
            r"(?<![-\w])(?:scrollbarWidth|msOverflowStyle)\s*:\s*[\"']none[\"']|"
            r"(?:^|[\s\"'])\[&::-webkit-scrollbar\]:hidden(?:[\s\"']|$)",
            re.I,
        ),
        "A scrollbar appears intentionally hidden; verify scroll affordance and page-width stability across short and long content.",
    ),
    LineRule(
        "transition-all",
        "P2",
        re.compile(
            r"(transition(?:-property)?\s*:\s*all\b|(?:^|[\s\"'])transition-all(?:[\s\"']|$))",
            re.I,
        ),
        "Transitioning every property can animate layout unexpectedly; name the intended properties.",
    ),
    LineRule(
        "viewport-width-unit",
        "P2",
        re.compile(r"(?<![\w.-])100vw\b", re.I),
        "A 100vw region can include scrollbar width and cause horizontal overflow.",
    ),
    LineRule(
        "nowrap-risk",
        "P2",
        re.compile(
            r"(white-space\s*:\s*nowrap\b|(?:^|[\s\"'])whitespace-nowrap(?:[\s\"']|$))",
            re.I,
        ),
        "No-wrap content needs a tested scroll, truncation, or responsive fallback.",
    ),
    LineRule(
        "large-fixed-width",
        "P2",
        re.compile(
            r"(?<![-\w])(?:width|min-width)\s*:\s*(?:[4-9]\d{2}|[1-9]\d{3,})px\b|"
            r"(?:^|[\s\"'])(?:w|min-w)-\[(?:[4-9]\d{2}|[1-9]\d{3,})px\](?:[\s\"']|$)",
            re.I,
        ),
        "Large fixed width may fail in narrower containers; verify intrinsic or bounded sizing.",
    ),
    LineRule(
        "tiny-text",
        "P2",
        re.compile(
            r"font-size\s*:\s*(?:[0-9]|1[01])px\b|"
            r"(?:^|[\s\"'])text-\[(?:[0-9]|1[01])px\](?:[\s\"']|$)",
            re.I,
        ),
        "Very small text may be unreadable; confirm this is nonessential and still accessible.",
    ),
    LineRule(
        "arbitrary-high-z-index",
        "P3",
        re.compile(
            r"z-index\s*:\s*(?:[1-9]\d{3,})\b|"
            r"(?:^|[\s\"'])z-\[(?:[1-9]\d{3,})\](?:[\s\"']|$)",
            re.I,
        ),
        "Large arbitrary z-index suggests an unmanaged layer system.",
    ),
    LineRule(
        "legacy-full-viewport-height",
        "P3",
        re.compile(r"(?<![\w.-])100vh\b|(?:^|[\s\"'])h-screen(?:[\s\"']|$)", re.I),
        "Full viewport height can misbehave with mobile browser chrome; verify dynamic viewport behavior.",
    ),
)

WEBKIT_SCROLLBAR_RULE = re.compile(
    r"::-webkit-scrollbar\s*\{(?P<body>[^{}]{0,2000})\}",
    re.I | re.S,
)
WEBKIT_SCROLLBAR_HIDING_DECLARATION = re.compile(
    r"display\s*:\s*none\b|"
    r"(?:width|height)\s*:\s*0(?:\.0+)?(?:px|rem|em)?"
    r"(?=\s*(?:!important\s*)?(?:;|$))",
    re.I,
)


def iter_start_tags(text: str) -> Iterable[TagToken]:
    """Yield HTML-like start tags without stopping at arrows inside JSX braces."""
    index = 0
    length = len(text)
    while index < length:
        if text[index] != "<" or index + 1 >= length or not text[index + 1].isalpha():
            index += 1
            continue

        start = index
        cursor = index + 1
        while cursor < length and (
            text[cursor].isalnum() or text[cursor] in {"_", "-", ":", "."}
        ):
            cursor += 1
        tag = text[index + 1 : cursor]
        attrs_start = cursor
        quote: str | None = None
        brace_depth = 0

        while cursor < length:
            character = text[cursor]
            if quote is not None:
                if character == "\\":
                    cursor += 2
                    continue
                if character == quote:
                    quote = None
            elif character in {'"', "'"} or character == chr(96):
                quote = character
            elif character == "{":
                brace_depth += 1
            elif character == "}" and brace_depth:
                brace_depth -= 1
            elif character == ">" and brace_depth == 0:
                yield TagToken(
                    tag=tag,
                    attrs=text[attrs_start:cursor],
                    start=start,
                    end=cursor + 1,
                )
                index = cursor + 1
                break
            cursor += 1
        else:
            index = start + 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan frontend source for conservative UI risk signals. Findings are "
            "candidates for review, not accessibility or design conformance results."
        )
    )
    parser.add_argument("paths", nargs="*", help="Files or directories to scan")
    parser.add_argument(
        "--format",
        choices=("text", "markdown", "json"),
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--extensions",
        help="Comma-separated extensions, such as .tsx,.css,.html",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Additional directory or filename to exclude; repeat as needed",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=5000,
        help="Maximum number of files to scan (default: 5000)",
    )
    parser.add_argument(
        "--fail-on",
        choices=("none", "P1", "P2", "P3"),
        default="none",
        help="Exit 1 when this severity or higher is found (default: none)",
    )
    parser.add_argument(
        "--list-rules",
        action="store_true",
        help="List rule identifiers and exit",
    )
    return parser.parse_args()


def normalized_extensions(raw: str | None) -> set[str]:
    if not raw:
        return set(DEFAULT_EXTENSIONS)
    result = set()
    for item in raw.split(","):
        value = item.strip().lower()
        if not value:
            continue
        result.add(value if value.startswith(".") else f".{value}")
    return result


def collect_files(
    raw_paths: Iterable[str],
    extensions: set[str],
    exclusions: set[str],
    max_files: int,
) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    seen: set[Path] = set()

    limit_reached = False

    def include(path: Path) -> bool:
        nonlocal limit_reached
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or path.suffix.lower() not in extensions:
            return True
        if path.name in exclusions or any(part in exclusions for part in path.parts):
            return True
        if len(files) >= max_files:
            if not limit_reached:
                errors.append(
                    f"file limit reached at {max_files}; narrow the scan path"
                )
            limit_reached = True
            return False
        seen.add(resolved)
        files.append(path)
        return True

    for raw_path in raw_paths:
        path = Path(raw_path)
        if not path.exists():
            errors.append(f"path not found: {raw_path}")
            continue
        if path.is_file():
            if not include(path):
                return sorted(files), errors
            continue
        for current_root, directory_names, file_names in os.walk(path):
            directory_names[:] = sorted(
                name
                for name in directory_names
                if name not in IGNORED_DIRECTORIES and name not in exclusions
            )
            for file_name in sorted(file_names):
                if not include(Path(current_root) / file_name):
                    return sorted(files), errors
    return sorted(files), errors


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def excerpt(text: str, start: int, end: int, limit: int = 180) -> str:
    value = " ".join(text[start:end].strip().split())
    if len(value) > limit:
        return value[: limit - 1] + "…"
    return value


def relative_name(path: Path, roots: list[Path]) -> str:
    resolved = path.resolve()
    for root in roots:
        try:
            return str(resolved.relative_to(root.resolve()))
        except ValueError:
            continue
    return str(path)


def add_finding(
    findings: list[Finding],
    seen: set[tuple[str, str, int]],
    finding: Finding,
) -> None:
    key = (finding.rule, finding.file, finding.line)
    if key not in seen:
        seen.add(key)
        findings.append(finding)


def has_attribute(attrs: str, name: str) -> bool:
    return re.search(rf"(?:^|\s){re.escape(name)}\s*=", attrs, re.I) is not None


def quoted_attribute_values(attrs: str, name_pattern: str) -> Iterable[str]:
    pattern = re.compile(
        rf"(?:^|\s)(?:{name_pattern})\s*=\s*"
        rf"(?P<quote>[\"'`])(?P<value>.*?)(?P=quote)",
        re.I | re.S,
    )
    for match in pattern.finditer(attrs):
        yield match.group("value")


def has_source_visible_image_geometry(attrs: str) -> bool:
    """Return whether an img tag visibly reserves both axes or an aspect ratio."""
    if has_attribute(attrs, "width") and has_attribute(attrs, "height"):
        return True

    if re.search(r"(?:^|\s)style:aspect-ratio\s*=", attrs, re.I):
        return True

    style_values = list(quoted_attribute_values(attrs, r"(?::|v-bind:)?style"))
    style_values.extend(
        match.group("value")
        for match in re.finditer(
            r"(?:^|\s)style\s*=\s*\{\{(?P<value>.*?)\}\}",
            attrs,
            re.I | re.S,
        )
    )
    for style_value in style_values:
        if re.search(r"(?:^|[;{,\s])aspect-?ratio\s*:", style_value, re.I):
            return True
        if re.search(r"(?:^|[;{,\s])width\s*:", style_value, re.I) and re.search(
            r"(?:^|[;{,\s])height\s*:", style_value, re.I
        ):
            return True

    for class_value in quoted_attribute_values(attrs, r"class(?:name)?"):
        if re.search(
            r"(?:^|[\s\"'`])aspect-(?!auto(?:[\s\"'`]|$))"
            r"(?:square|video|\[[^\]]+\]|[1-9]\d*(?:\/[1-9]\d*)?)"
            r"(?=[\s\"'`]|$)",
            class_value,
            re.I,
        ):
            return True
    return False


def scan_tags(
    text: str,
    file_name: str,
    findings: list[Finding],
    seen: set[tuple[str, str, int]],
) -> None:
    for token in iter_start_tags(text):
        tag = token.tag.lower()
        attrs = token.attrs
        attrs_lower = attrs.lower()
        line = line_number(text, token.start)
        tag_evidence = excerpt(text, token.start, token.end)

        if tag == "img" and not re.search(r"\balt\s*=", attrs, re.I):
            add_finding(
                findings,
                seen,
                Finding(
                    "image-missing-alt",
                    "P1",
                    file_name,
                    line,
                    "Image element has no explicit alt attribute; determine whether it is informative or decorative.",
                    tag_evidence,
                ),
            )

        if tag == "img" and not has_source_visible_image_geometry(attrs):
            add_finding(
                findings,
                seen,
                Finding(
                    "image-unreserved-geometry",
                    "P2",
                    file_name,
                    line,
                    "Image has no source-visible width/height pair or aspect-ratio reservation; verify that loading cannot shift surrounding content.",
                    tag_evidence,
                ),
            )

        if tag in {"div", "span"} and re.search(
            r"\bon(?:click|keydown|keyup|keypress)\s*=", attrs, re.I
        ):
            has_role = re.search(r"\brole\s*=", attrs, re.I)
            has_tabindex = re.search(r"\btabindex\s*=", attrs, re.I)
            if not has_role or not has_tabindex:
                add_finding(
                    findings,
                    seen,
                    Finding(
                        "nonsemantic-interactive",
                        "P1",
                        file_name,
                        line,
                        "Non-semantic element has an interaction handler without clear role and keyboard focusability; prefer a native control.",
                        tag_evidence,
                    ),
                )

        if tag in {"button", "a", "input", "select", "textarea"} and re.search(
            r"\baria-hidden\s*=\s*[\"'{]?\s*true\b", attrs, re.I
        ):
            add_finding(
                findings,
                seen,
                Finding(
                    "focusable-aria-hidden",
                    "P1",
                    file_name,
                    line,
                    "Potentially interactive element is hidden from the accessibility tree.",
                    tag_evidence,
                ),
            )

        before_tag = text[: token.start].lower()
        inside_form = before_tag.rfind("<form") > before_tag.rfind("</form")
        if tag == "button" and "type=" not in attrs_lower and inside_form:
            add_finding(
                findings,
                seen,
                Finding(
                    "button-missing-type",
                    "P2",
                    file_name,
                    line,
                    "Button has no explicit type; inside a form it defaults to submit.",
                    tag_evidence,
                ),
            )

        if re.search(r"\bonmouseenter\s*=", attrs, re.I) and not re.search(
            r"\bonfocus\s*=", attrs, re.I
        ):
            add_finding(
                findings,
                seen,
                Finding(
                    "hover-only-handler",
                    "P2",
                    file_name,
                    line,
                    "Mouse-enter behavior has no visible focus equivalent in the same element.",
                    tag_evidence,
                ),
            )


def scan_file(path: Path, file_name: str) -> tuple[list[Finding], str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return [], f"could not read {path}: {error}"

    if len(text) > 2_000_000:
        return [], f"skipped {path}: file is larger than 2 MB"
    lines = text.splitlines()
    if lines and max(map(len, lines)) > 20_000:
        return [], f"skipped {path}: appears minified or generated"

    findings: list[Finding] = []
    seen: set[tuple[str, str, int]] = set()

    source_offset = 0
    for number, source_line in enumerate(lines, start=1):
        for rule in LINE_RULES:
            match = rule.pattern.search(source_line)
            if match:
                if rule.rule == "nowrap-risk":
                    prefix = text[:source_offset]
                    block_start = prefix.rfind("{")
                    block_end = prefix.rfind("}")
                    if block_start > block_end:
                        selector = prefix[block_end + 1 : block_start].lower()
                        if any(
                            marker in selector
                            for marker in (
                                ".sr-only",
                                ".visually-hidden",
                                ".screen-reader",
                            )
                        ):
                            continue
                add_finding(
                    findings,
                    seen,
                    Finding(
                        rule.rule,
                        rule.severity,
                        file_name,
                        number,
                        rule.message,
                        excerpt(source_line, match.start(), match.end()),
                    ),
                )
        source_offset += len(source_line) + 1

    scan_tags(text, file_name, findings, seen)

    has_motion = re.search(
        r"(@keyframes\b|animation(?:-name)?\s*:|transition(?:-property)?\s*:)",
        text,
        re.I,
    )
    has_reduced_motion = re.search(r"prefers-reduced-motion", text, re.I)
    if (
        has_motion
        and not has_reduced_motion
        and path.suffix.lower()
        in {
            ".css",
            ".scss",
            ".sass",
            ".less",
            ".vue",
            ".svelte",
            ".astro",
        }
    ):
        line = line_number(text, has_motion.start())
        add_finding(
            findings,
            seen,
            Finding(
                "motion-without-local-reduction",
                "P2",
                file_name,
                line,
                "Motion is defined without a reduced-motion query in this file; verify that a shared reduction policy covers it.",
                excerpt(text, has_motion.start(), has_motion.end()),
            ),
        )

    for scrollbar_rule in WEBKIT_SCROLLBAR_RULE.finditer(text):
        hiding_declaration = WEBKIT_SCROLLBAR_HIDING_DECLARATION.search(
            scrollbar_rule.group("body")
        )
        if hiding_declaration:
            add_finding(
                findings,
                seen,
                Finding(
                    "scrollbar-hidden",
                    "P2",
                    file_name,
                    line_number(text, scrollbar_rule.start()),
                    "A scrollbar appears intentionally hidden; verify scroll affordance and page-width stability across short and long content.",
                    excerpt(text, scrollbar_rule.start(), scrollbar_rule.end()),
                ),
            )

    return findings, None


def render_text(findings: list[Finding], scanned: int, warnings: list[str]) -> str:
    lines = [
        "UI source risk scan",
        f"Scanned files: {scanned}",
        f"Candidates: {len(findings)}",
        "",
    ]
    for item in findings:
        lines.append(
            f"{item.severity} {item.file}:{item.line} [{item.rule}] {item.message}"
        )
        lines.append(f"  evidence: {item.evidence}")
    if not findings:
        lines.append("No configured risk signals found.")
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(
        [
            "",
            "These are conservative source candidates, not confirmed defects or an accessibility conformance result.",
        ]
    )
    return "\n".join(lines) + "\n"


def escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_markdown(findings: list[Finding], scanned: int, warnings: list[str]) -> str:
    lines = [
        "# UI source risk scan",
        "",
        f"- Scanned files: {scanned}",
        f"- Candidates: {len(findings)}",
        "",
    ]
    if findings:
        lines.extend(
            [
                "| Severity | Location | Rule | Candidate | Evidence |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for item in findings:
            lines.append(
                f"| {item.severity} | {escape_table(item.file)}:{item.line} | "
                f"{item.rule} | {escape_table(item.message)} | "
                f"{escape_table(item.evidence)} |"
            )
    else:
        lines.append("No configured risk signals found.")
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(
        [
            "",
            "> These are conservative source candidates, not confirmed defects or an accessibility conformance result.",
            "",
        ]
    )
    return "\n".join(lines)


def render_json(findings: list[Finding], scanned: int, warnings: list[str]) -> str:
    payload = {
        "scanned_files": scanned,
        "candidate_count": len(findings),
        "findings": [asdict(item) for item in findings],
        "warnings": warnings,
        "disclaimer": (
            "Source candidates are not confirmed defects or an accessibility "
            "conformance result."
        ),
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main() -> int:
    args = parse_args()
    if args.list_rules:
        for rule in LINE_RULES:
            print(f"{rule.severity} {rule.rule}: {rule.message}")
        print("P1 image-missing-alt: Image element has no explicit alt attribute.")
        print(
            "P1 nonsemantic-interactive: Non-semantic element may be acting as a control."
        )
        print(
            "P1 focusable-aria-hidden: Interactive element may be hidden from accessibility APIs."
        )
        print("P2 button-missing-type: Button may submit a form implicitly.")
        print(
            "P2 hover-only-handler: Pointer hover behavior may lack a focus equivalent."
        )
        print(
            "P2 image-unreserved-geometry: Image loading may lack a source-visible geometry reservation."
        )
        print(
            "P2 motion-without-local-reduction: Motion may lack reduced-motion handling."
        )
        return 0

    if not args.paths:
        print("at least one file or directory path is required", file=sys.stderr)
        return 2
    if args.max_files < 1:
        print("--max-files must be at least 1", file=sys.stderr)
        return 2

    extensions = normalized_extensions(args.extensions)
    exclusions = set(args.exclude)
    files, warnings = collect_files(args.paths, extensions, exclusions, args.max_files)
    if not files:
        if not warnings:
            warnings.append("no supported frontend source files found")
        for warning in warnings:
            print(warning, file=sys.stderr)
        return 2

    roots = [
        path if path.is_dir() else path.parent
        for path in (Path(raw_path) for raw_path in args.paths)
        if path.exists()
    ]
    findings: list[Finding] = []
    scanned_files = 0
    for path in files:
        file_findings, warning = scan_file(path, relative_name(path, roots))
        findings.extend(file_findings)
        if warning:
            warnings.append(warning)
        else:
            scanned_files += 1

    findings.sort(
        key=lambda item: (
            SEVERITY_ORDER[item.severity],
            item.file,
            item.line,
            item.rule,
        )
    )

    if args.format == "json":
        output = render_json(findings, scanned_files, warnings)
    elif args.format == "markdown":
        output = render_markdown(findings, scanned_files, warnings)
    else:
        output = render_text(findings, scanned_files, warnings)
    sys.stdout.write(output)

    if args.fail_on != "none":
        if warnings:
            return 2
        threshold = SEVERITY_ORDER[args.fail_on]
        if any(SEVERITY_ORDER[item.severity] <= threshold for item in findings):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
