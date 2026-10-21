---
name: slop-removal
description: "Hunt and remove known AI tells from a built screen: eyebrows, subtitles that restate headings, filler and marketing copy, repeated facts, generic buttons, nested cards, decorative gradients and glows, badges everywhere, and off-grid layout, using rendered and source checks and a catalog that grows. Use when a screen reads as AI-made, templated, or padded, or someone asks to remove slop. Not for writing new copy or fixing spacing drift."
---

# Slop removal

Removes what a generated template adds and a reader never needed, and keeps the project's look. Sibling skill folders sit next to this skill's folder; run their scripts by that path, such as `$interface-craft/scripts/ui_measure.py`, and never import them.

## Work

1. **Frame.** Name the screen, its primary task, and the look to keep. Skim the [catalog](references/catalog.md).
2. **Capture.** Run `python3 $interface-craft/scripts/ui_measure.py capture URL --phase baseline --widths 390,1280 --themes light,dark`. The `slop.*` rules run in every capture; read their rows in `summary.md`.
3. **Scan source.** Run this skill's `python3 scripts/slop_scan.py scan PATH --files <the screen's files>`. Each `src.*` finding names its fix and stays a candidate until the page confirms it.
4. **Judge.** Walk the [judgment checklist](references/judgment.md) for what detectors miss: invented proof, generic copy, card soup, stock page order, and second-wave defaults.
5. **Fix,** cheapest first:
   - Delete the line, badge, glow, stripe, or wrapper
   - Merge a real fact into the heading or the value it describes
   - Swap a one-off for a primitive from `$component-compose`'s inventory
   - Rewrite with the copy rules in `$content-hierarchy`

   Never restyle a tell or add a new treatment. Off-grid findings get key lines from `$ui-layout`, never nudged margins.
6. **Opt out honestly.** When a flagged element is right here, such as a product code in capitals, mark it `data-ui-allow="rule.id: reason"` on the page (the finding drops to INFO) or `// slop-allow: <src id> <reason>` in source. Every allow is counted. One without a reason does nothing and is its own finding (`rule:allow.reason`, `src.allow_reason`). FAIL is never demoted.
7. **Verify.** Capture again, run `compare BASELINE FINAL`, classify every dropped string, and run `python3 $interface-craft/scripts/ui_claims.py check report.md --source .`.

## Rules

| Where | Ids |
|---|---|
| Rendered, WARN | `rule:slop.eyebrow`, `rule:slop.repeated_text`, `rule:slop.generic_action`, `rule:slop.empty_state`, `rule:slop.nested_cards`, `rule:slop.gradient_text`, `rule:slop.glow`, `rule:slop.side_stripe`, `rule:slop.badge_density` |
| Rendered, INFO | `rule:slop.restates_heading`, `rule:slop.dash` |
| Source | `src.eyebrow_class`, `src.gradient_text`, `src.glow_shadow`, `src.transition_all`, `src.bounce_easing`, `src.infinite_pulse`, `src.entrance_everywhere`, `src.marketing_words`, `src.emoji_label`, `src.description_everywhere` |

Copy rules never FAIL. Navigation, tables, breadcrumbs, native form labels, `aria-hidden` content, dates, and "Step N" are skipped, and a stock shadcn Card passes.

## Deliver

```markdown
**Result:** <n> tells removed from <screen>; slop findings <before> to <after> [E##@1280:default#light].

| Tell | Where | Detected by | Fix | Evidence |
|---|---|---|---|---|
| <tell> | <element> | <rule id or judgment> | <deleted, merged, swapped, or rewritten> | [E##@390:default#light] |

**Dropped text:** "<string>": <removed as which tell, merged into what, moved where, or rewritten as what>
**Allows:** <count, each with its reason, or none>
**Not verified:** <what and why>
```

## Worked example

**Screen:** a usage dashboard with an eyebrow over each section, gradient headings, and a "Get started" button.
**Found:** four eyebrows, two gradient headings, and one generic action [E03@1280:default#light]; marketing words in the header source; "Trusted by 2,000 teams" traced to no data.
**Fixed:** eyebrows and the claim deleted, headings set to the solid text token, and the button rewritten. "Exports include archived specs" stayed because it adds a condition.
**Dropped text:** "OVERVIEW" (eyebrow, deleted); "Trusted by 2,000 teams" (invented proof, deleted); "Get started" (generic action, rewritten as "Create report").

## Gotchas

- Deleting every description clears the copy rules and loses facts; a line that adds a condition, consequence, example, or next step stays
- A softer gradient or a smaller eyebrow keeps the tell; delete or merge it
- Swapping one tell for another default, such as cream with terracotta or one neon accent, is still slop; use the project's tokens
- A stock shadcn Card with a header and content is one card, not nested cards
- Allows are for findings that are wrong here; clearing a page with them hides the defect, and the count shows it
- No rule can prove a number, logo, or quote true; trace each one even when nothing fires
- A source finding in dead code is not on the page; confirm it on the render before editing

## Boundaries

- Do not use for spacing, alignment, wrapping, radii, or shell drift; use `$interface-craft` instead
- Do not use to write a copy deck or new UI copy; use `$content-hierarchy` instead
- Do not use to replace one-off components across a codebase; use `$component-compose` instead
- Do not use for hardcoded colors or token drift; use `$design-system` instead
- Do not use to build options for a piece; use `$ui-variants` instead
