# AGENTS.md — Design Engineering

This repository is Matt Mitchell's personal distribution of the Design Engineering Agent Skills. It packages 13 active skills plus the explicit `testable-requirements` redirect under `skills/<name>/`.

## Repository map

- `skills/<name>/SKILL.md` — portable instructions loaded on demand.
- `skills/<name>/agents/openai.yaml` — Codex presentation metadata and invocation policy for each skill.
- `skills/<name>/references/` and `skills/<name>/scripts/` — resources owned by that skill and referenced by relative path.
- `plugin.json` — portable Agent Plugins manifest.
- `.codex-plugin/plugin.json` — Codex compatibility manifest and skill directory declaration.
- `.claude-plugin/plugin.json` — Claude compatibility manifest.
- `package.json` — Pi package metadata and `pi.skills` declaration.
- `scripts/validate.py` — structural validator for this copy.
- `source-manifest.json` — source revision and snapshot digests.
- `snapshot/file-hashes.sha256` — immutable per-file hashes for the copied source snapshot.
- `licenses/` — full license texts for credited upstream adaptations.
- `THIRD-PARTY-NOTICES.md` — attribution and license links for credited adaptations.

## Editing rules

1. Read `docs/SKILL-AUTHORING.md` before changing a skill.
2. Keep `SKILL.md` portable. Do not add client-specific tool names, credentials, MCP servers, or private project instructions.
3. Keep supporting files inside the owning skill folder and use relative paths.
4. Keep the short skill name in frontmatter equal to its directory name. Use lowercase letters, digits, and single hyphens.
5. Update routing fixtures when a description changes. Keep the redirect explicit-only.
6. Bump all package manifests together and add a matching changelog heading.
7. Run `python3 scripts/validate.py` and `bash scripts/check.sh` before publication.

The work plugin source is a separate, actively changing repository. It is recorded as provenance only; do not edit it from this checkout.

## Verification

Structural checks do not prove that an agent chose or followed a skill well. For a meaningful change, run the affected helper's self-test and perform a real task in a host that can load the skill. Browser-based `interface-craft` checks require Chrome or Chromium and are intentionally separate from the dependency-free default check.
