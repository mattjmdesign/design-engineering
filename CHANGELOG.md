# Changelog

Every release needs a heading here that matches the version in `.claude-plugin/plugin.json`; `scripts/validate.py` fails without it.

## 1.0.0

First personal release of the portable Design Engineering package. It carries the current 13 active skills and the one explicit `testable-requirements` redirect from the work plugin snapshot, with personal Agent Plugins, Codex, Claude, OpenCode, and Pi packaging metadata. The upstream 3.0.0 notes below describe the skill changes that this release contains.

The package also carries per-skill Codex presentation metadata, a portable delegation fallback in `ui-variants`, a verifiable per-file source snapshot, full license text and third-party notices for the credited MIT and Apache-2.0 adaptations, and an explicit note for the unverified Gesso provenance mention.

## 3.0.0

The plugin now centers on the rendered UI: 13 skills plus one redirect, down from 15 skills and two redirects.

### Added

- `ui-variants` builds three real, measured options for one component or content region and puts them behind a picker in the running app, so they are compared in place. It loads when you invoke it or plainly ask for options, variants, or alternatives. Each run varies one axis (Structure, Density, Emphasis, Composition, or Copy); type and color stay system decisions. Variants live in `src/__variants__/<piece>/`, are selected with `?__variant=<id>`, `window.__setVariant(id)`, or keys 1 to 5 in the picker, and production code never imports them. It defaults to three variants and builds five only when asked. The approach adapts Jakub Krehel's `variant` skill (MIT).
- `slop-removal` hunts and removes known AI slop from a screen, with rendered and source checks and a catalog built to grow as new tells appear. It cites the engine's rendered `slop.*` rules and ships `scripts/slop_scan.py`, a source scan for eyebrow classes, gradient text, glow shadows, `transition-all`, bounce easing, infinite pulses, entrance animations on every section, marketing words, emoji labels, and description slots filled everywhere. Every finding names its fix. Some thresholds adapt Impeccable (Paul Bakaus, Apache-2.0).
- `component-compose` ships `scripts/ui_inventory.py`. `inventory` lists the project's primitives, `audit` checks files or a diff for raw elements, primitive bypasses, shadowed names, restated variants, copied skins, repeated classes, near duplicates, and suppressions with a baseline ratchet, and `summary --base REF` writes a Reused, Extended, Composed, and Created table from a diff. Every message names the replacement, and a real gap is allowed with a recorded reason.
- `design-system` gains Adopt, a fifth mode. `token_scan.py adopt PATH --files F...` proposes, for each literal color, spacing, radius, or type value, the nearest token, a retarget of the token itself, a new role, or a one-off, and never adds a parallel token beside the role it replaces.
- `interface-craft` gains `ui_measure.py xray RUN/E##`, which draws numbered outlines, labeled key lines, shaded gutters, and near-miss deltas over one capture, with zoomed crops on a 4px lattice and a JSON sidecar. The picture never passes anything on its own.
- `interface-craft` gains `ui_measure.py variants URL --ids a,b,c`, which captures each variant as a labeled state at each width and theme, writes a comparison and a contact sheet, and flags variants that differ only in paint (`variant.distinct`).
- The engine adds rendered slop rules: `slop.eyebrow`, `slop.repeated_text`, `slop.generic_action`, `slop.empty_state`, `slop.nested_cards`, `slop.gradient_text`, `slop.glow`, `slop.side_stripe`, and `slop.badge_density` at WARN, and `slop.restates_heading` and `slop.dash` at INFO. Copy rules never fail a run. They skip navigation, tables, breadcrumbs, native form labels, hidden content, dates, and "Step N", and a stock shadcn Card passes. `data-ui-allow="rule.id: reason"` demotes a finding to INFO and is counted in the summary, and an allow without a reason is its own finding (`allow.reason`).
- `align.near_miss` also checks right and top edges, and evidence ids are allocated under a lock, so parallel workers can share one `.ui-evidence/` folder.
- `content-hierarchy` adds `references/copy.md`: a copy deck of element, slot, job, and string before markup, and rules for subtitles, repeated facts, hints, buttons, errors, empty states, success messages, case, and register.

### Renamed and reworked

- `testable-requirements` becomes `ui-representation`. It takes rough or clear input (a request, outline, data, meeting notes, or feedback) to the right UI pattern, the components that build it, and where it fits, and writes requirement lists only when asked. `testable-requirements` stays for one release as a redirect.
- `page-layout` becomes `ui-layout` and covers layout from a component's insides to a whole page: an imagined grid under each component, key lines, gutters, width caps, and content-region templates. The page recipes moved here from `interface-craft`.
- `component-spec` becomes `component-compose`. It uses shadcn/ui, Base UI, and local primitives fully, and creates a custom component only for a real gap, which it never blocks.
- `interface-craft` is slimmer. Page recipes moved to `ui-layout`, the Measure step adds `xray`, slop work points to `slop-removal`, and running a UI without its backend (formerly repo-setup's fixture mode) is now a section of `references/state-harness.md`. `forms.md` asks for a group description only when it adds a condition, consequence, or example. A gotcha that repeated the engine's per-theme contrast check was cut to keep the skill under 800 words.

### Changed

- `site-map` names which routes share a shell, and labels role and permission text as draft until server checks exist (carried from code-prototype).
- `user-flow` names each step's screen and component, and requires success copy that matches what the write did (carried from code-prototype).
- `state-coverage` gives each state a way to force it and an `expect` target, keeps empty-state copy out of loading, and treats a state nobody rendered as planned (carried from code-prototype).
- `content-hierarchy` writes a copy deck before markup, warns against filling a component's description slot by reflex, and sends AI-tell removal to `slop-removal`. Its description now names labels, hints, errors, and empty states.
- `agent-context` writes a five-line UI block: where `ui/` lives, the reuse ladder, the check commands, named anti-patterns, and which rules are linted versus reviewed.
- `design-system`'s description names Adopt. A gotcha that repeated the Guard step's baseline advice was cut to keep the skill under 800 words.
- `product-intent` points to `ui-representation` and `ui-variants` for next steps.
- Skills may run, but never import, a sibling skill's script, written as `$skill-name/scripts/file.py`, and the validator checks that the path exists.
- Routing cases that expected removed or renamed skills now expect their replacements, and new cases cover shells, screens and components in flows, `expect` targets, the copy deck, the UI block, and Adopt.

### Removed

- `code-prototype` is removed. Its blind-judge baseline rose from 2.67 to 3.67, but its gotcha edit tied 3.00 to 3.00 on the held-out case. Its three proven rules moved to `site-map`, `user-flow`, and `state-coverage`, and comparing options in code is now `ui-variants`.
- `repo-setup` and `scripts/repo_doctor.py` are removed. The skill did not touch the rendered UI and had one logged call. Fixture mode moved into `interface-craft`. Its server-route authorization line had no remaining owner and was dropped.
- `release-readiness` is removed. It was off the UI mission, had one logged call, and its lift disappeared on Sonnet (100% with and without).
- `git-workflow` is removed. It had no effect on the UI and one logged call. Its dependency-PR line had no remaining owner and was dropped.
- The `design-tokens` and `system-audit` redirects are removed one release earlier than 2.0.0 planned.
- `interface-craft` drops `references/platform-adaptation.md` (native targets, off-target for web work) and `scripts/ui_source_audit.py` (an optional source preflight; `slop-removal` and `component-compose` now carry the source scans).

### Migration note

- Use `/design-engineering:ui-representation` where you used `/design-engineering:testable-requirements`. The old name redirects for one release.
- `/design-engineering:page-layout` and `/design-engineering:component-spec` stop working in this release. Use `/design-engineering:ui-layout` and `/design-engineering:component-compose`.
- `/design-engineering:code-prototype`, `repo-setup`, `release-readiness`, `git-workflow`, `design-tokens`, and `system-audit` stop working in this release. Use `design-system` for tokens and audits, ask for prototypes, setup, readiness reviews, and branch plans as normal requests, or restore a folder from release 2.0.0.

## 2.0.0

### Added

- `interface-craft` is rewritten around a clean baseline and measurement. It now enforces one spacing scale, shared alignment lines, one type hierarchy, controls that hold their shape, radii taken from tokens, and one shell on every route, and it keeps the project's look instead of adding a new one. `SKILL.md` is now a short loop (under 800 words, down from 1,415) and the references are 14 focused files in place of 8. New recipes give defaults and named checks for spacing and rhythm, controls and shape, type, color and elevation, forms, tables and lists, states and feedback, and common page types (with a content region of equal gutters and width caps), and a state harness reference explains how to force and capture every state, including a dev-only state switcher.
- `interface-craft` measures the rendered page. `scripts/ui_measure.py` drives a local Chrome over a private pipe and checks spacing against a 4px grid, type scale, contrast in each theme, target sizes (with WCAG's spacing exception), focus rings and order, overflow, accessible names, alignment, control heights, rhythm, radius and shadow counts, and numbers shown against the data. It captures state matrices with network forcing and forced pseudo-states, writes numbered screenshots and a 60-line summary, and compares a baseline run with a final run for dropped text and moved anchors. New checks flag button, tab, and badge labels that wrap, headings that end on a lone word, inner radii that are not the container radius minus the inset, radii outside the project's tokens, same-level headings that differ, content that hugs the region edge or has uneven gutters or no width cap, and sections whose columns differ. A `routes` command loads several routes and compares the rail, header, footer, content column, and page title across them. It needs Python 3.9 or newer and Chrome.
- `interface-craft` requires cited claims. `scripts/ui_claims.py` checks a report against that evidence and rejects invented ids, edited or stale evidence, mismatched widths, themes, or states, numbers that disagree with the measurements, undisclosed text changes, and invented source citations.
- `design-system` ships `scripts/token_scan.py`. It scans for raw colors, off-grid spacing, arbitrary Tailwind values, and undefined custom properties, keeps a shrink-only baseline, lists tokens with usage and duplicates, and checks contrast for every token pair in each theme.
- `repo-setup` ships `scripts/repo_doctor.py` with `env` (names the code reads against the example env files, including secrets in browser-exposed variables), `fresh` (proves a clean checkout by running the documented steps in a throwaway clone), and `sandbox` (a safe throwaway copy of a repository), plus `references/fixture-mode.md` for running a UI without its backend.

### Removed

- `stack-decision` is removed, and no skill replaces it. Ask for a stack comparison in a normal request, then use `repo-setup` once the stack is chosen.
- `vertical-slice` is removed, and no skill replaces it. A hidden-grader test (31 checks on a small tickets API: repeat-safe creates, stale-write conflicts, an audit log, per-organization access, restart and crash durability, and no secrets in logs) found that Haiku without the skill already passed 99% of checks, against 100% with it. The only visible difference was keeping request bodies out of logs (6 of 6 runs with the skill, 4 of 6 without), which six runs cannot confirm. Ask for the feature as a normal request and use `release-readiness` to judge whether it is safe to ship. Restore the folder from release 1.0.1 if you want it back.
- `interface-craft` drops the formal scorecard (`score_ui_audit.py`) and `analyze_layout_stability.py`, and the references `foundations`, `components-and-states`, `visual-quality`, and `review-and-verification`. Audit findings keep a short format in `references/verification.md`, and `compare` replaces the layout-stability helper. The Design Authorship reference became the shorter `triage-and-critique.md`, and its direction and signature steps were removed.

### Renamed and Merged

- `design-tokens` and `system-audit` become one skill, `design-system`, with four modes: Define (create or extend a foundation), Audit (drift, duplicates, and intentional versus accidental differences), Migrate (deprecation plans with prop mappings and consumer impact), and Guard (run the project's own checker, or ask to add a token scan with a baseline ratchet).
- `design-system` keeps the strongest gotchas of both skills, trimmed from 11 to 6, and moves "an undocumented system gets reinvented" into its Work steps.
- `design-tokens` and `system-audit` remain as redirect stubs for two releases. They are hidden from automatic routing, point to `design-system`, and will then be removed.
- component-spec, interface-craft, page-layout, release-readiness, and repo-setup point to `design-system` where they pointed to the old names. repo-setup no longer points to a stack skill; it plans around the stack the project has or the user names.

### Changed

- git-workflow lists the working directory before looking for repository policy, and says a new dependency lands as its own small PR instead of inside a restyle PR.
- user-flow adds a gotcha for the person who opens a shared link or notification but is not allowed to act on it.
- code-prototype adds three rules from review feedback (success copy must match what the prototype did, every state a reviewer must judge needs a visible control and a render, and role or permission text is labeled draft and unenforced on the page) and keeps them within six gotchas, with a pointer to interface-craft for the state harness.
- component-spec labels caller behavior, sizes, and sample names the repo does not show as proposals.
- content-hierarchy extends the invented-content gotcha to step counts and sequences.
- page-layout stops agents from stating pixel widths they estimated without a render.
- product-intent asks for a named measure, checkpoint, and threshold when a keep, fix, or cut call is deferred.
- release-readiness records accepted risk only when a person accepted it, and lists a deferral only when someone proposed it.
- repo-setup adds that moving a secret-bearing call into a server route still needs a caller authorization check.
- state-coverage reserves Must for what the code visibly causes.
- design-system carries the design-tokens warning that soft status tints blur together, and asks for a solid status color that reaches 3:1 on the surface, with text on the tint checked at 4.5:1.
- interface-craft verifies the actual viewport width of every render (`viewport.truth`) and measures contrast per theme.
- interface-craft no longer asks for a design direction or a signature. Improve keeps the project's look and fixes unevenness only, the loop captures at 390, 768, 1280, and 1920, and apps with more than one route get a routes step.
- page-layout, design-system, component-spec, and content-hierarchy each gain one line that carries a baseline lesson. page-layout asks for one shell component on every route with gutters and a max width per page type. design-system makes the radius scale, a derived inner radius, and text roles tokens. component-spec adds a label contract of maximum length, one line, one fixed height per size, and radius from the scale. content-hierarchy asks for verb-first button labels that fit on one line at 390px.
- Routing cases that expected `design-tokens` or `system-audit` now expect `design-system`, and cases that expected `stack-decision` were replaced.

### Migration Note

- Use `/design-engineering:design-system` where you used `/design-engineering:design-tokens` or `/design-engineering:system-audit`. The old names still run for two releases but only redirect.
- Anything that invokes `/design-engineering:stack-decision` or `/design-engineering:vertical-slice` stops working in this release.

## 1.0.1

- Plugin shows as "Design Engineering" in Claude instead of "Design engineering".

## 1.0.0

- First release: 18 design engineering skills, each named in two words that say what it does.
- Packaged for Claude Code only.
