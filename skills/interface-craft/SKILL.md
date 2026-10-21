---
name: interface-craft
description: "Bring a web interface to a clean baseline and prove it on the rendered page: spacing, alignment, type, controls, radii, and the shell. Use when a screen looks uneven or hard to scan, spacing or alignment drifts between screens, button or tab labels wrap, corners or headings look inconsistent, the rail or header differs between pages, or a page needs a measured overflow, contrast, focus, and state check."
---

# Interface Craft

## Choose the Task

**Build** creates an interface, **Improve** changes existing work and keeps its intent, brand, and behavior, and **Audit** returns findings without editing.

## The Baseline

- **Spacing:** every padding, margin, and gap is a multiple of one base (4 and 8, or the project's own), and space grows outward from controls to sections (`rule:grid.spacing`)
- **Alignment:** a column shares one x position and a row shares one height and edge (`rule:align.near_miss`)
- **Type:** a few roles, each with one size, weight, and color everywhere, with weights two steps apart (`rule:type.role`)
- **Wrapping:** headings use `text-wrap: balance`, longer text uses `pretty`, and no heading ends on a lone word (`rule:type.wrap`)
- **Controls:** a button, tab, or badge keeps a fixed height and a one-line label; wrapping means shorter copy (`rule:control.wrap`)
- **Radii:** use the radius scale; an inner radius is the container's radius minus the inset, never below zero; an off-scale result changes the inset or the tokens, never a typed value (`rule:shape.nested`)
- **Shell:** the rail, header, and footer match on every route, and sections share one content column with set gutters and a width cap (`rule:shell.consistency`)
- **Function:** contrast, targets, focus, names, and every state hold at the captured widths (`rule:contrast.text`)

## The Loop

Run this skill's `scripts/` by path. They use a local headless Chrome, write only to `.ui-evidence/`, and cover web pages.

1. **Frame.** Name the primary action and the existing look to keep; add no new visual identity.
2. **Load.** Read matching references. New UI always needs spacing-and-rhythm, type, and controls-and-shape.
3. **Baseline.** Run `python3 scripts/ui_measure.py init URL` once, then `capture URL --phase baseline --ready SELECTOR` for Improve and Audit.
4. **Build with states.** Write `states.json` before styling: loading, empty, error, and long content at minimum.
5. **Measure and fix.** Run `capture URL --widths 390,768,1280,1920 --themes light,dark`. Bring FAIL to zero, fix or justify each WARN, and capture again. `xray RUN/E##` draws key lines and near-miss deltas; the picture passes nothing.
6. **Check routes.** For routes that share a shell, run `routes URL [URL ...]` and fix or justify each WARN.
7. **Render states.** Run `states URL --spec states.json --sheet`, critique the sheet at thumbnail and actual size, fix the top issue, and capture again.
8. **Compare.** For Improve, run `compare BASELINE_RUN FINAL_RUN`; no text may vanish unexplained.
9. **Report.** Lead with the result, under 500 words. Cite an evidence id for every claim or write `(unverified: reason)`, then run `python3 scripts/ui_claims.py check report.md --source .` and fix what it rejects.

If Chrome or the app cannot run, say so, register screenshots with `ui_measure.py register`, and mark measured claims unverified.

## References

| Need | Read |
| --- | --- |
| Spacing, alignment | [spacing-and-rhythm](references/spacing-and-rhythm.md) |
| Buttons, radii | [controls-and-shape](references/controls-and-shape.md) |
| Type, wrapping | [type](references/type.md) |
| Color, depth | [color-and-elevation](references/color-and-elevation.md) |
| Forms | [forms](references/forms.md) |
| Tables, lists | [tables-and-lists](references/tables-and-lists.md) |
| Loading, empty, error | [states-and-feedback](references/states-and-feedback.md) |
| Forcing states, no backend | [state-harness](references/state-harness.md) |
| Triage, critique | [triage-and-critique](references/triage-and-critique.md) |
| Breakpoints, overflow | [responsive-layout](references/responsive-layout.md) |
| Keyboard, focus | [accessibility](references/accessibility.md) |
| Evidence, claims | [verification](references/verification.md) |

## Worked Example

**Task:** Two settings pages have different gutters, and the Save label wraps at 390.
**Decisions:** One shared content column, a shorter label, and an inset of 8 so a 4 radius button nests in a 12 radius card.
**Evidence:** [E07] to [E09], 0 FAIL, 1 justified WARN. Routes [E10] show one shell.
**Gaps:** (unverified: screen reader pass not run).

## Gotchas

- A green measurement is not good design; an unrendered state proves nothing
- Run `init` so checks follow the project's scales; never edit tokens to pass a default
- Hacks that clear a WARN keep the defect: hidden overflow, nudged margins, `!important`, a smaller font
- Do not add effects to satisfy a check; fix the unevenness and keep the look

## Boundaries

- Do not use for planning shells, grids, or content regions; use `$ui-layout` instead
- Do not use for token roles, theme pairs, or drift; use `$design-system` instead
- Do not use to compose from the component system; use `$component-compose` instead
- Do not use for missing-state coverage; use `$state-coverage` instead
- Do not use to remove AI tells such as eyebrows or filler copy; use `$slop-removal` instead
