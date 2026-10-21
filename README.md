# Design Engineering

Design Engineering is a portable set of Agent Skills for product designers working in code. It helps an agent clarify intent, choose the right UI representation, plan structure and flows, compose from existing primitives, compare measured options, remove generated UI noise, and verify the rendered result.

The repository has one plain `skills/<name>/SKILL.md` directory per skill. The same tree can be copied into a host's Agent Skills directory. The root `plugin.json` is the portable Agent Plugins manifest; `.codex-plugin/plugin.json` and `.claude-plugin/plugin.json` are compatibility manifests for Codex and Claude.

## Skills

| Area | Skill | Use it for |
| --- | --- | --- |
| Define | `product-intent` | Settle the user, outcome, and smallest useful scope. |
| Define | `ui-representation` | Turn notes, data, or feedback into a UI pattern and component map. |
| Structure | `site-map` | Plan routes, navigation, URL state, and access areas. |
| Structure | `user-flow` | Map a task through success, interruption, cancellation, and recovery. |
| Structure | `content-hierarchy` | Rank a page's information and actions, then write its copy deck. |
| Structure | `state-coverage` | Find and make loading, empty, error, permission, and recovery states renderable. |
| Build | `ui-layout` | Plan grids, regions, widths, gutters, shells, and responsive behavior. |
| Build | `component-compose` | Reuse the repository's primitives and record real component gaps. |
| Build | `design-system` | Define, audit, migrate, adopt, and guard tokens and shared styling. |
| Refine | `ui-variants` | Compare three measured options for one component or content region. |
| Refine | `slop-removal` | Remove generated UI tells using source and rendered checks. |
| Verify | `interface-craft` | Bring a built interface to a clean, measured baseline. |
| Context | `agent-context` | Keep repository instructions accurate and useful to the next agent. |

`testable-requirements` remains as a one-release redirect to `ui-representation` for the 2.x name. The older short names and removals are recorded in [`migration.json`](migration.json).

## Install

For a host that implements the Agent Skills directory convention, install the repository's `skills/` tree in the host's skills directory. A local checkout can be used without a package manager:

```text
/path/to/design-engineering/skills/<skill-name>/SKILL.md
```

The [OpenCode skills documentation](https://docs.opencode.ai/docs/skills/) lists `.opencode/skills`, `.claude/skills`, and `.agents/skills` as compatible project and global locations. OpenCode can therefore use this repository through a project `.agents/skills` copy or the host's global skills directory. Pi implements the Agent Skills format and can discover project `.agents/skills` directories; its package install is the easiest way to keep the supporting scripts and references together.

Once this repository is public:

```bash
# Generic Agent Skills installer
npx skills add mattjmdesign/design-engineering

# Pi package install (the package.json pi.skills entry points at ./skills)
pi install git:github.com/mattjmdesign/design-engineering@main
```

Codex and ChatGPT can load the portable plugin through the included local marketplace:

```bash
# From a local checkout
codex plugin marketplace add /path/to/design-engineering

# From GitHub after publication
codex plugin marketplace add mattjmdesign/design-engineering
codex plugin marketplace list
codex plugin add design-engineering@design-engineering
```

Then install or enable `design-engineering` from the Codex Plugins Directory. The same repository includes the Claude-compatible marketplace manifest:

```bash
claude plugin marketplace add mattjmdesign/design-engineering
claude plugin install design-engineering@design-engineering
```

The plain Agent Skills layer is the compatibility boundary. A host named Prime, Prime Agent, or another client can use the `skills/` directories if it implements the Agent Skills convention; this repository does not claim a client-specific adapter or install command without that host's documentation.

## Work with a skill

Ask for the task and name the skill when you want to force the route:

```text
Use $interface-craft to improve this screen and verify the rendered result.
Use $ui-representation to turn these notes into a task, facts, and component map.
Use $design-system to audit the tokens before changing shared styling.
```

Automatic invocation remains enabled for every active skill. The redirect is explicit-only so it does not compete with `ui-representation`.

## Verify a checkout

This repository has no runtime dependencies. Run the structural checks from its root:

```bash
python3 scripts/validate.py
bash scripts/check.sh
npm pack --dry-run
```

The checks validate manifest identity, skill frontmatter, internal references, routing fixtures, and the bundled deterministic helpers. The package manifest uses an explicit file whitelist, so `npm pack --dry-run` also confirms that caches and browser evidence are excluded. The browser-based `interface-craft` self-test is separate because it needs a local Chrome or Chromium:

```bash
python3 skills/interface-craft/scripts/_selftest.py
python3 skills/interface-craft/scripts/_claims_selftest.py
```

Read [`docs/SKILL-AUTHORING.md`](docs/SKILL-AUTHORING.md) before changing a skill. Keep references and scripts beside the skill that owns them. Do not add client-specific credentials, network services, or tool permissions to a portable skill.

## Source and license

This personal repository is a snapshot of the Design Engineering plugin work in Matt's private work checkout. The exact source revision, captured working-tree digest and timestamp, snapshot tree digest, and per-file hashes are recorded in [`source-manifest.json`](source-manifest.json) and [`snapshot/file-hashes.sha256`](snapshot/file-hashes.sha256). The work checkout is not a dependency and is not modified by this repository.

The package is MIT licensed. See [`LICENSE`](LICENSE).
Credited adaptations and their upstream license links are listed in [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md).
