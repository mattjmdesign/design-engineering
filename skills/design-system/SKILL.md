---
name: design-system
description: "Define, audit, migrate, adopt, or guard a design system: semantic token roles with contrast-checked theme pairs, drift and duplicate findings by user impact, deprecation plans with prop mappings, experimental values folded into tokens, and checks that keep styling consistent. Use when starting a UI foundation or turning brand colors into roles, when hardcoded values multiply or components duplicate each other, when themes or focus rings break, or when DESIGN.md needs updating."
---

# Design system

Inspect existing tokens, components, consumers, and any checker before defining anything new; rendered behavior outranks a naming preference.

## Work

- **Define** (create or extend): choose roles before values: surface and text, action and its text, border, focus, and status pairs, plus type, spacing, and radius scales only where repetition warrants. Radius is a scale of at most four tokens plus a derived inner radius (`calc(outer - inset)`) for nested shapes; text roles (size, line height, weight, color) are tokens. Reuse current formats and names, and add a theme only when shipped. Measure each fill with its own text role in every shipped theme: text at 4.5:1, focus rings and other non-text edges at 3:1. Consume the roles in one real primitive and one screen to find missing ones, and run `python3 scripts/token_scan.py contrast PATH` to check every pair per theme.
- **Audit** (drift and duplicates): inventory only what the request touches, classify each difference as accidental or intentional, and rank by user consequence and spread: a broken keyboard path outranks two inconsistent colors. `token_scan.py inventory PATH` lists tokens with usage and duplicates, and `scan PATH` counts raw colors, off-scale values, and undefined variables.
- **Migrate** (deprecations): decide reuse, extension, or a new component per change. Name consumers that will change visibly, and retire an API only with a migration route.
- **Adopt** (after an experiment wins): `token_scan.py adopt PATH --files F...` proposes, for each literal value, the nearest token, a retarget when one literal consistently replaces a token's value, a new role, or a one-off. Never add a parallel token beside the role it replaces.
- **Guard** (when no agent is present): run the project's own checker first, such as a `design:check` script. When the project ships no checker, offer `token_scan.py scan PATH --write-baseline FILE` once, then `scan PATH --baseline FILE` in CI and `--shrink` as debt is paid, with the baseline committed.

Record the token, class, and component names agents may use, and the rules for adding one, in DESIGN.md or existing guidance; an undocumented system gets reinvented.

## Deliver

Give **Mode, Detected, Preserve, Changes, Guard**, with each change as issue → evidence → impact → fix → verification, confirmed facts separated from hypotheses, and no visual claim without a render. For Define, the changes are the source path, a role and value table, and per-theme contrast checks.

## Worked example

```markdown
## Design system review

**Mode:** audit, then migrate
**Detected:** tokens in `src/styles/globals.css`; primitives in `src/components/ui/`; no DESIGN.md; `design:check` passes
**Preserve:** DestructiveDialog's typed confirmation; the dense operations table
**Changes:**
| Issue | Evidence | Impact | Fix | Verification |
|---|---|---|---|---|
| Raw `#3b82f6` in feature code | Twice in `features/reports`; `design:check` scans only `components/` | Wrong in the dark theme | Use the action role; scan `features/` too | Render both themes |
| LegacyCard duplicates Card | Two callers; only the header slot differs | Divergent padding and focus | Add the slot to Card; map LegacyCard props; deprecate | Compare renders |
**Guard:** today's violations are the baseline; only new ones fail
```

## Gotchas

- A role name or lightness step does not guarantee contrast; measure the real text on the real surface in every shipped theme, and choose dark values per role, never by inverting light.
- Soft status tints sit near 1.1:1 on the surface and blur together; give each status a solid color at 3:1 on the surface for borders and icons, and check text on the tint at 4.5:1.
- A token for every measured value becomes a lookup table nobody uses; one-offs stay literal, but radii and text roles never do.
- A difference is not drift until you know why it exists; a denser table may be deliberate, and raw values in token sources, fixtures, or generated code are not defects.
- Merging components or editing a shared token changes every consumer; list each caller's props first, render a sample in each theme, and map old props to new, or both live forever.

## Boundaries

- Do not use for page shells, regions, or scroll behavior; use `$ui-layout` instead.
- Do not use to compose screens from existing components or audit a diff for primitive bypasses; use `$component-compose` instead.
- Do not use for screen spacing, type, or controls; use `$interface-craft` instead.
