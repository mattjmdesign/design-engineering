# Grid and widths

The grid under a page, how to choose it, and how to record it so every agent places blocks on the same lines.

## The imagined grid

Before placing anything, picture two grids:

- A 4px lattice under every component. Padding, gaps, control heights, and icon boxes land on it; 1px hairlines are the only exception.
- A column grid under the page: the content column between two gutters, split by the template's tracks. Block edges land on its key lines.

After placing, check every edge. It sits on a key line, on a gutter, or a 4px step from its neighbor. An edge 2px off a line is a defect, not a style.

## Four layers

1. **Template.** The page type fixes regions, tracks, cap, and gutters. Choose it first; it stops agents from inventing a 12-column grid for an app page.
2. **Fractions for top-level blocks.** Count the peer blocks in a row at 1280. One block gets a cap; a primary and a secondary get `minmax(0, 2fr) minmax(0, 1fr)`; three or more equals get `auto-fit`. A fixed count that must not leave a lone item on a row (four figures) steps through counts that divide it (4, 2, 1) with container queries instead. Components inside a block follow spacing tokens, not columns.
3. **Key lines.** Two to four vertical lines per screen where several edges agree: content start, content end, a split between tracks, and a label or field column. Repeated rows also share horizontal lines.
4. **Base unit.** 4px, with layout steps on 8. The spacing ladder and nesting ratios are in `$interface-craft`'s spacing-and-rhythm reference.

## Record the grid

Write five lines once in DESIGN.md or AGENTS.md, so the next agent reads them instead of guessing. The values below are an example; use the project's own.

```markdown
Grid
- Base: 4px; layout steps 8, 16, 24, 32, 48
- Gutters: 16 under 640, 24 to 1023, 32 above, set on the content container only
- Cap: data 1280 (`max-w-7xl`), detail and forms 960, settings fields 560, prose 65ch
- Key lines: content start, content end, the 2fr/1fr split on dashboards, the settings label column
- Template: one per page type, named from ui-layout's content regions (dashboard, list, settings, ...)
```

## Caps and gutters

| Page type | Cap | Note |
| --- | --- | --- |
| Data, dashboards, wide tables | 1280 to 1440 | Tables and charts may fill the column and stop at the cap |
| Record detail, forms | 720 to 960 | |
| Settings fields | 560 to 720 | Beside a 240 to 300px label column |
| Prose | About 65ch | |
| Sign-in | 360 to 420 | |
| Marketing sections | 1120 to 1200 inner container | Backgrounds may run full bleed |

Gutters step 16, 24, and 32 by width, and `rule:layout.content` checks the minimum at each captured width. When a project has no sizes at all, Radix Themes' four container widths (448, 688, 880, and 1136) are a reasonable set to start from.

## When the project has no recorded grid

1. Search the source for caps and gutters: `max-w-`, `container`, `mx-auto`, and `px-` on page wrappers, plus CSS `max-width` and any content-width variables. Count them by page type.
2. Keep the most common cap per page type. A cap used once is drift unless its page type differs.
3. Fill the remaining values from the table above, write the five lines, and move every page onto the one container.

## CSS that holds the grid

```css
.content { width: min(100% - 2 * var(--gutter), var(--cap)); margin-inline: auto; }
.split { display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: var(--space-6); }
.peers { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr)); gap: var(--space-4); }
.card { display: grid; grid-row: span 3; grid-template-rows: subgrid; }
```

In Tailwind, the content container is one wrapper such as `mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8`, peers are `grid grid-cols-[repeat(auto-fit,minmax(min(100%,16rem),1fr))]`, and the split is `grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)]`.

- `minmax(0, 1fr)` instead of `1fr` lets a track shrink below its content's width, which stops long labels from pushing the page wide
- `subgrid` lines up titles, bodies, and footers across sibling cards
- Container queries (`@container`, and Tailwind's `@container` with `@md:` variants) change a component by its own width; media queries are for page composition and user preferences

## Checking the grid

- `capture` records the content column, width, and gutters at every width, and `rule:layout.content` flags uneven gutters, a missing cap, and sections whose edges differ
- `xray` draws numbered outlines, labeled key lines, shaded gutters, and near-miss deltas, and adds 4x crops with the 4px lattice. Read the clean capture for composition first, then the X-ray for geometry, and let the findings decide.
- `rule:align.near_miss` flags edges a few pixels apart that should share a line
