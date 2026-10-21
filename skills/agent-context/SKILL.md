---
name: agent-context
description: "Create or revise repository instructions using verified commands, paths, architecture constraints, and review expectations. Use when creating AGENTS.md or CLAUDE.md, when agents invent commands or paths, or when instructions drift stale."
---

# Agent context

Record what an agent would otherwise guess incorrectly. Read existing context files and their scope before editing; keep one authoritative home for each rule.

## Work

- Verify project purpose, commands, runtime, paths, and architecture from the workspace. Never invent a test command or an existing component.
- Capture consequential boundaries: where secrets and authorization live, who owns cached data, token/component sources, and generated files.
- Include the actual checks and observable evidence expected for the work. Distinguish lint/build from rendered or behavioral verification.
- When the repo has UI, add a UI block of five lines: where `ui/` lives, the reuse ladder (a primitive as is, a variant, a composition, then a new component with its gap recorded), the check commands that exist, this repo's anti-patterns by name, and which rules a command enforces versus review. Point to DESIGN.md for names instead of copying them.
- Separate durable conventions from task-specific plans. Link focused supporting docs instead of embedding complete manuals.
- Reconcile contradictions and stale instructions, including blanket lockfile bans, duplicate formatting stacks, and obsolete paths. Preserve explicit user policies unless asked to change them.
- Match the client and file format already in use. If adding client-specific discovery rules, verify current client documentation; do not claim every client loads every filename.

## Deliver

Update the requested file, or provide a ready-to-use draft when edits were not requested. Usually include purpose, commands, important paths, non-obvious constraints, and verification. Omit empty sections and generic coding advice.

```markdown
# AGENTS.md

[One line: what the product is and who it serves]

## Commands
## Where things live
## UI
## Rules an agent would otherwise get wrong
## Verification
```

Check every referenced local command and path; label external or future dependencies. The file guides authorized work, not permission to publish or expand a task.

## Worked example

**Detected:** pnpm lockfile; `check`, `design:check`, and `test` scripts in `package.json`; shadcn primitives in `src/components/ui/`; `DESIGN.md` with token and component names; tokens in `src/styles/tokens.css`; server actions in `src/server/`; no end-to-end script. The old file banned lockfile edits and named a `dev` branch that no longer exists.
**Changed:** replaced the lockfile ban with the package-manager rule, removed the stale branch rule, and cut generic style advice.

```markdown
# AGENTS.md

Scheduling dashboard for a construction client. Versions and dependencies: see `package.json`.

## Commands
- `pnpm check`: lint and type checks; run before every PR
- `pnpm test`: unit tests. There is no end-to-end suite yet; do not claim one ran.
- Change dependencies with `pnpm add` or `pnpm remove` so `pnpm-lock.yaml` updates with them; do not hand-edit it.

## Where things live
- `src/styles/tokens.css` is the only token source; feature code uses token names, not raw color values
- `src/server/` enforces permissions for every write; hiding a button in the UI is not access control

## UI
- Primitives live in `src/components/ui/`; names and the rule for adding one are in `DESIGN.md`
- Reuse ladder: use a primitive as is, then a variant, then a composition; a new component names its gap in the PR
- Checks: `pnpm check` (lint, types) and `pnpm design:check` (raw colors, off-scale spacing)
- Anti-patterns: a raw `<button>` beside `Button`, a `className` restating a variant, a copied Card skin
- Linted: raw colors and spacing. Reviewed: reuse, copy, and layout; a passing lint is not approval

## Verification
- UI changes need a rendered check at narrow and wide widths plus a keyboard pass; `pnpm check` alone does not verify them
```

## Gotchas

- The usual invented command is a plausible one: `npm test` in a pnpm repository, or a script the README mentions but the manifest dropped.
- Context files load on every task, so a generic line ("write clean code") costs attention and changes nothing.
- A blanket ban ("never touch the lockfile") blocks legitimate work and gets ignored; state the safe way instead.
- Copies of the same rules in several client files drift apart; keep one source and point the others at it.
- A line that mentions a ticket, a date, or "for now" is a task plan; it belongs in the pull request.

## Boundaries

- Do not use to create the repository's structure, commands, or branch policy; build or settle those as normal work, then record what exists here.
- Do not use to write DESIGN.md's token and component names or the rules for adding them; use `$design-system` instead.
- Do not use to audit code for primitives it bypasses; use `$component-compose` instead.
