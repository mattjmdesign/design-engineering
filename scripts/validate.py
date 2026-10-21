#!/usr/bin/env python3
"""Validate the portable Design Engineering package without third-party dependencies."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SIBLING_SCRIPT_RE = re.compile(r"\$([a-z0-9]+(?:-[a-z0-9]+)*)/scripts/([^\s`),]+)")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PRIVATE_RE = re.compile(r"/Users/|tallwave-design|Tallwave", re.IGNORECASE)


class Checker:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def fail(self, message: str) -> None:
        self.errors.append(message)


def read_json(checker: Checker, path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        checker.fail(f"missing JSON file: {path.relative_to(ROOT)}")
        return {}
    except json.JSONDecodeError as exc:
        checker.fail(f"invalid JSON in {path.relative_to(ROOT)}: {exc}")
        return {}
    if not isinstance(value, dict):
        checker.fail(f"JSON root must be an object: {path.relative_to(ROOT)}")
        return {}
    return value


def frontmatter(checker: Checker, path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        checker.fail(f"frontmatter must start on line 1: {path.relative_to(ROOT)}")
        return {}
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        checker.fail(f"frontmatter is not closed: {path.relative_to(ROOT)}")
        return {}
    result: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            checker.fail(f"frontmatter line has no key/value separator in {path.relative_to(ROOT)}: {line}")
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def check_manifest(checker: Checker, relative: str, version: str | None = None) -> dict:
    value = read_json(checker, ROOT / relative)
    if value.get("name") != "design-engineering":
        checker.fail(f"{relative} must identify design-engineering")
    if version is not None and value.get("version") != version:
        checker.fail(f"{relative} version {value.get('version')!r} does not match {version!r}")
    if not value.get("description"):
        checker.fail(f"{relative} needs a description")
    return value


def check_evals(checker: Checker, skill_name: str, skill_dir: Path) -> None:
    for filename in ("evals.json", "triggers.json"):
        value = read_json(checker, skill_dir / "evals" / filename)
        if value.get("skill_name") != skill_name:
            checker.fail(f"{skill_name}/evals/{filename} has the wrong skill_name")
        cases = value.get("evals" if filename == "evals.json" else "cases")
        if not isinstance(cases, list) or not cases:
            checker.fail(f"{skill_name}/evals/{filename} needs at least one case")


def yaml_value(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*(.+?)\s*$", text)
    if not match:
        return None
    return match.group(1).strip().strip('"').strip("'")


def check_agent_metadata(checker: Checker, skill_dir: Path, is_redirect: bool) -> None:
    path = skill_dir / "agents" / "openai.yaml"
    if not path.is_file():
        checker.fail(f"missing Codex metadata: {path.relative_to(ROOT)}")
        return
    text = path.read_text(encoding="utf-8")
    for key in ("display_name", "short_description", "default_prompt"):
        value = yaml_value(text, key)
        if not value:
            checker.fail(f"{path.relative_to(ROOT)} needs interface.{key}")
    implicit = yaml_value(text, "allow_implicit_invocation")
    expected = "false" if is_redirect else "true"
    if implicit != expected:
        checker.fail(f"{path.relative_to(ROOT)} must set allow_implicit_invocation: {expected}")


def check_snapshot_hashes(checker: Checker) -> None:
    manifest = read_json(checker, ROOT / "source-manifest.json")
    source = manifest.get("source", {})
    snapshot = manifest.get("snapshot", {})
    if source.get("label") != "private work source":
        checker.fail("source-manifest.json must use the private work source label")
    if "repository" in source:
        checker.fail("source-manifest.json must not publish the private work repository URL")
    relative = snapshot.get("fileHashes")
    if relative != "snapshot/file-hashes.sha256":
        checker.fail("source-manifest.json must point to snapshot/file-hashes.sha256")
        return
    path = ROOT / relative
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except FileNotFoundError:
        checker.fail(f"missing snapshot hash manifest: {relative}")
        return
    hashes: set[str] = set()
    paths: set[str] = set()
    for line in lines:
        parts = line.split("  ", 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]) or not parts[1]:
            checker.fail(f"invalid snapshot hash entry: {line}")
            continue
        digest, file_path = parts
        hashes.add(digest)
        paths.add(file_path)
    expected = snapshot.get("fileCount")
    if isinstance(expected, int) and len(lines) != expected:
        checker.fail(f"snapshot hash count {len(lines)} does not match fileCount {expected}")
    if len(paths) != len(lines):
        checker.fail("snapshot hash manifest contains duplicate paths")


def check_internal_paths(checker: Checker, skill_dir: Path, skill_names: set[str]) -> None:
    for path in skill_dir.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".md", ".py", ".json", ".js"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if PRIVATE_RE.search(text):
            checker.fail(f"private work attribution or path appears in {path.relative_to(ROOT)}")
        for match in SIBLING_SCRIPT_RE.finditer(text):
            sibling, script = match.groups()
            if sibling not in skill_names:
                checker.fail(f"{path.relative_to(ROOT)} references unknown skill ${sibling}")
                continue
            if not (ROOT / "skills" / sibling / "scripts" / script).is_file():
                checker.fail(f"{path.relative_to(ROOT)} references missing script ${sibling}/scripts/{script}")
        for match in MARKDOWN_LINK_RE.finditer(text if path.suffix.lower() == ".md" else ""):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_path = (path.parent / target).resolve()
            if not target_path.exists():
                checker.fail(f"broken local link in {path.relative_to(ROOT)}: {target}")


def main() -> int:
    checker = Checker()
    root_manifest = check_manifest(checker, "plugin.json")
    version = root_manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        checker.fail("plugin.json version must be semantic version text")
        version = ""
    codex = check_manifest(checker, ".codex-plugin/plugin.json", version)
    claude = check_manifest(checker, ".claude-plugin/plugin.json", version)
    package = read_json(checker, ROOT / "package.json")
    if package.get("name") != "design-engineering" or package.get("version") != version:
        checker.fail("package.json identity or version does not match plugin manifests")
    package_files = package.get("files")
    required_package_files = {
        ".agents/",
        ".claude-plugin/",
        ".codex-plugin/",
        "docs/**/*.md",
        "licenses/*.txt",
        "scripts/*.py",
        "scripts/*.sh",
        "skills/**/*.html",
        "skills/**/*.js",
        "skills/**/*.json",
        "skills/**/*.md",
        "skills/**/*.py",
        "skills/**/*.yaml",
        "snapshot/*.sha256",
        "THIRD-PARTY-NOTICES.md",
    }
    if not isinstance(package_files, list) or not required_package_files.issubset(package_files):
        checker.fail("package.json files whitelist must include plugin metadata, skills, licenses, scripts, snapshot, and notices")
    if package.get("pi", {}).get("skills") != ["./skills"]:
        checker.fail("package.json must declare pi.skills as [\"./skills\"]")
    if codex.get("skills") != "./skills/":
        checker.fail(".codex-plugin/plugin.json must declare skills as ./skills/")
    if not isinstance(codex.get("interface"), dict) or not isinstance(claude, dict):
        checker.fail("Codex and Claude manifests need their presentation metadata")
    changelog = ROOT / "CHANGELOG.md"
    if not changelog.is_file() or f"## {version}" not in changelog.read_text(encoding="utf-8"):
        checker.fail(f"CHANGELOG.md needs a heading for {version}")
    check_snapshot_hashes(checker)

    skill_root = ROOT / "skills"
    skill_dirs = sorted(path for path in skill_root.iterdir() if path.is_dir() and not path.name.startswith("."))
    skill_names = {path.name for path in skill_dirs}
    if len(skill_dirs) != 14:
        checker.fail(f"expected 14 skill folders (13 active plus redirect), found {len(skill_dirs)}")
    for skill_dir in skill_dirs:
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.is_file():
            checker.fail(f"missing SKILL.md: {skill_dir.relative_to(ROOT)}")
            continue
        meta = frontmatter(checker, skill_file)
        if meta.get("name") != skill_dir.name:
            checker.fail(f"{skill_file.relative_to(ROOT)} name must match its folder")
        if not NAME_RE.fullmatch(meta.get("name", "")):
            checker.fail(f"invalid skill name in {skill_file.relative_to(ROOT)}")
        if not meta.get("description") or len(meta["description"]) > 1024:
            checker.fail(f"{skill_file.relative_to(ROOT)} needs a focused description under 1024 characters")
        words = len(skill_file.read_text(encoding="utf-8").split())
        if words > 1200:
            checker.fail(f"{skill_file.relative_to(ROOT)} is too long ({words} words)")
        is_redirect = meta.get("disable-model-invocation") == "true"
        check_agent_metadata(checker, skill_dir, is_redirect)
        if not is_redirect:
            check_evals(checker, skill_dir.name, skill_dir)
        check_internal_paths(checker, skill_dir, skill_names)

    if checker.errors:
        for error in checker.errors:
            print(f"FAIL {error}")
        print(f"{len(checker.errors)} validation error(s)")
        return 1
    print(f"OK design-engineering {version}: {len(skill_dirs)} skill folders, manifests, links, and eval fixtures validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
