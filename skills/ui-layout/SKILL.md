---
name: ui-layout
description: "Lay out UI on an imagined grid, from a component's slots to whole pages and shells: regions, widths, gutters, key lines, responsive behavior, and which region scrolls. Use when planning a dashboard, landing, auth, or settings shell, when a page or region has no defined layout (what goes in a dashboard's center), when blocks miss a shared grid or caps are set per section, when a layout breaks at some widths, or when rows, toolbars, and cards need internal structure."
---

# UI layout

Imagine a 4px grid under every component and a column grid under the page, and put every edge on a line: a key line, a gutter, or a 4px step. Reuse the shell and widths that exist.

## Work

1. **Detect** the shell and the routes that share it, the spacing tokens, and every cap and gutter on page wrappers (`max-w-`, `container`, `px-`). With the app running, `python3 $interface-craft/scripts/ui_measure.py init URL` reads its scales; sibling skill folders sit next to this one. Three caps for one page type is drift, not a value to copy.
2. **Choose a template** from [content-regions](references/content-regions.md) or the shell and page [recipes](references/page-recipes.md), and name the alternatives it beat. Unknown priority comes from `$content-hierarchy`; an unknown pattern for the data from `$ui-representation`.
3. **Write the grid once** in DESIGN.md or AGENTS.md as five lines: base, gutters, cap, key lines, and template ([grid-and-widths](references/grid-and-widths.md)). Follow one that exists.
4. **Place the blocks** on the key lines, and slots inside components on the 4px grid ([component-internals](references/component-internals.md)).
5. **Define narrow behavior** by content pressure: what stacks, which pane becomes a page or sheet, and what scrolls locally.
6. **Verify with `$interface-craft`:** `ui_measure.py capture URL --widths 390,768,1280`, judge composition on the clean screenshots, then `python3 $interface-craft/scripts/ui_measure.py xray .ui-evidence/RUN/E##` for key lines, gutters, and near misses. Fix each flag at its shared owner (container, wrapper, or token) and capture again. The X-ray picture never passes anything alone.

## Rules

- One container owns the cap and gutters; children add no horizontal padding or `max-w-` (`rule:layout.content`)
- Fractions apply to top-level blocks only: one block gets a cap, primary plus secondary is `2fr 1fr`, and three or more peers `auto-fit` by container width
- Two to four key lines per screen
- Repeated rows get fixed-width icon and trailing slots
- Group gaps are at least 1.5 times inner gaps; aim for 2 (`rule:rhythm.group_ratio`)
- Persistent regions keep one size on every route (`rule:shell.consistency`), and one region scrolls

## Deliver

```markdown
## Layout plan: [page or region]

**Detected:** [shell, tokens, caps, gutters, with paths]
**Template:** [name]; alternatives: [two], rejected because [reason]
**Grid:** base; gutters; cap; key lines; template
**Regions in order:** [region: track, contents]
**Internals:** [fixed slots in rows, toolbars, cards]
**Narrow:** [what changes at 768 and 390]
**Verify:** [evidence ids, or (unverified: reason)]
```

## Worked example

```markdown
## Layout plan: dashboard content area

**Detected:** `AppSidebar` on signed-in routes; `max-w-7xl` on two pages, `max-w-6xl` on /reports; sections set their own `px-4 lg:px-6`
**Template:** dashboard; list-first and primary plus aside rejected, since managers need what is late and the trend
**Grid:** base 4; gutters 16, 24, 32 on the container; cap 1280 (the common `max-w-7xl`); key lines: content start and end, 2fr/1fr split; template: dashboard
**Regions in order:** header; attention list; four figures (4, 2, 1 across); chart 2fr beside activity 1fr; projects table
**Internals:** attention rows with fixed status and action slots
**Narrow:** activity drops below the chart; the table scrolls locally
**Verify:** E04 to E06; xray on E06 showed the chart card 4px inside content start, fixed by removing its `px-6`
```

## Gotchas

- `auto-fit` leaves four figures as three and a lone card at some widths; for a fixed count, step 4, 2, and 1 across with container queries.
- Without a render, do not state pixel widths estimated from character counts; name the element to measure and the width at which it matters.
- Viewport breakpoints misfire inside a sidebar shell, where the content area is narrower than the window; size peer columns by container width.
- A 12-column grid on an app page invites arbitrary spans (7 of 12); most app regions need two or three tracks.
- `overflow: hidden` on the page hides the too-wide child; fix the child, often a flex or grid item missing `min-width: 0`.

## Boundaries

- Do not use for what a page says or what comes first; use `$content-hierarchy` instead.
- Do not use to choose a table, list, chart, or detail; use `$ui-representation` instead.
- Do not use to polish type, controls, color, or radii on built screens; use `$interface-craft` instead.
- Do not use for which pages exist or how navigation is grouped; use `$site-map` instead.
- Do not use to choose token values; use `$design-system` instead.
