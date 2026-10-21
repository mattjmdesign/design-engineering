---
name: ui-representation
description: "Turn rough or clear input (a request, outline, data sample, meeting notes, or feedback) into the right UI: restate it as task, facts, and open questions, choose the representation that fits the task and data, map its parts to existing components, and say where it fits. Use when someone hands over notes, data, or feedback and asks what to build or how to show it, or asks for FR/NFR requirements or acceptance criteria."
---

# UI representation

Interpret the input before choosing anything; the pattern follows from the task and the data, not from habit.

## Work

1. **Interpret.** Name the input kind and restate it as the **task** (who needs to decide or do what), the **facts** the input states, and the **open questions** it leaves. If who it is for or why is unclear, stop and use `$product-intent`.
2. **Read what exists:** the data shape (fields, record count, the identifying field, what changes over time) and screens that already show the same entity. Run `python3 $component-compose/scripts/ui_inventory.py inventory --brief` for the components on hand; sibling skill folders sit next to this one.
3. **Choose.** Pick the pattern from [representation](references/representation.md) and name one alternative with the reason it loses. An existing pattern for the same entity beats a new one. When two stay close, offer a `$ui-variants` run instead of guessing.
4. **Map the parts** to existing components and variants. Gaps go to `$component-compose`.
5. **Place it:** the route, the region, and the `$ui-layout` template.
6. **Write acceptance checks** that are observable on the rendered screen with fixture data. Write FR and NFR lines only when asked, in the shape in the reference.

## Deliver

```markdown
## UI representation: [subject]

**Input:** [kind], [source]
**Task:** [who] needs to [decide or do what] using [which facts]
**Facts:** [only what the input or the code states]
**Open questions:** [each names the part it blocks]
**Representation:** [pattern], because [task and data]. Not [alternative], because [reason]
**Parts:** [part: component (variant)]; gaps: [part]
**Fits:** [route, region, layout template, matching screen]
**Acceptance:** [observable checks]
**Next:** [one step]
```

## Worked example

```markdown
## UI representation: late projects on the dashboard

**Input:** client feedback ("Managers keep missing late projects. Can we add a pie chart of status?"); projects API sample
**Task:** a manager needs to see which projects are late and who owns each
**Facts:** status is on track, at risk, or late; one owner and due date per project; managers see only their clients
**Open questions:** is "late" past the due date or a status someone sets (blocks the filter); can a manager reassign here (blocks the row action)
**Representation:** an attention list of late and at-risk projects by due date. Not a pie chart: it says four are late but not which four
**Parts:** row: `Item`; status: the `ProjectStatus` badge from /projects; owner: `Avatar` size `sm`; action: `Button` variant `ghost`; gaps: none
**Fits:** /dashboard, first block; dashboard template, attention strip
**Acceptance:** three late projects in the fixture all show owner and due date at 390 and 1280; with none, one line says so; other managers' clients never appear, even by direct request
**Next:** `$component-compose` to build the strip
```

## Gotchas

- Every field, count, name, and date comes from the input or the code. A missing fact is an open question, never a placeholder ("Acme Corp", "$12,400"); placeholders ship as claims.
- Feedback names a symptom and often a widget ("add a chart"). Find the problem first, and say when the suggested widget would not fix it.
- Agents default to card grids and the KPI row, chart, and table template. Comparison across attributes is a table, a few actionable items a list, one record a detail, and a trend a line chart.
- Meeting notes mix decisions with suggestions; build from the decisions and list each suggestion, with who made it, as a question.
- Hiding a control does not stop the action. When the input says who may act, a check covers a direct request from someone who may not.
- An endpoint, field, or size the code does not show is a proposal; label it, or it gets built as an existing contract.
- "Fast", "secure", and "accessible" cannot fail until each has a condition and a check.

## Boundaries

- Do not use when who it is for, or why, is still unclear; use `$product-intent` instead.
- Do not use to rank a page's content or write its copy; use `$content-hierarchy` instead.
- Do not use to set the grid, size regions, or place blocks; use `$ui-layout` instead.
- Do not use to decide whether to reuse, extend, or create a component; use `$component-compose` instead.
- Do not use for a task's step-by-step path; use `$user-flow` instead.
