# Axes and positions

One axis per round. Each variant takes a different position on it, and everything else either follows from that position or stays fixed. Three variants that move several axes at once give a result nobody can attribute, so the next piece starts from nothing.

## The five axes

| Axis | What varies | Measured by | Component positions | Region positions |
| --- | --- | --- | --- | --- |
| Structure | Grouping, order, columns, and what collapses at narrow widths | Blocks per row at each width, reading order, region count | Number first, Trend first, Comparison row | Summary over table, Table with side panel, Grouped sections |
| Density | Spacing step, row and control height, and how much fits | Control heights and gaps on the project's scale, rows visible at 768 | Comfortable, Standard, Compact | Cards, Rows, Dense table |
| Emphasis | Where the one filled action and the heaviest weight sit, and what recedes | First element in reading order, filled area, primary action position | Action-led, Value-led, Status-led | Task first, Overview first, Exceptions first |
| Composition | Which existing primitives assemble the piece | Component and `data-slot` names from the inventory | Card, Item list, Table row | Table, List and detail, Tabs over table |
| Copy | Which slots carry text and how much, from one copy deck | Text sequence and word count per slot | Labels only, Labels and hints, Next step inline | Headings only, Headings and one summary line, Guided first run |

### Structure

Positions come from the `$ui-layout` template alternatives for the region type. For a page region, round one builds these as skeletons and reads them with `xray` before any agent runs, because structure is the costliest decision to get wrong.

### Density

Every position sits on the project's control heights and spacing steps from `init`. A density that needs a new step is a `$design-system` decision, not a variant. A compact position still meets the 24px target floor.

### Emphasis

Each position still has one filled action per view. Emphasis moves weight and order; it never adds color the system does not have.

### Composition

Each position assembles primitives that exist in the inventory, such as a `Card` against an `Item` list against a `Table`. A position that needs a component the inventory lacks may build it only for a recorded gap, and the table shows it as created.

### Copy

Positions differ in which slots are filled, never in the facts or the register. Every string comes from the copy deck and follows the copy rules in `$content-hierarchy`; a "guided" position that adds filler fails the `$slop-removal` checks like any other. Tone is not a position: there is no playful variant.

## What never varies

- Type: families, scale, roles, and weights. These are system decisions owned by `$design-system`.
- Color: tokens, theme pairs, the accent, and semantic colors
- The radius scale, the elevation model, the focus ring, and motion tokens
- The facts: one fixture and one copy deck for all variants
- The floor: contrast, targets, focus, accessible names, keyboard reach, and no clipping at the narrowest width
- The shell and the neighbors around the piece
- The data contract with the parent (props in, events out), unless the axis is Composition and the change is listed as a cost

When the ask is "try other fonts" or "a few color schemes", say that type and color are system decisions, offer `$design-system`, and propose a real axis instead of building tints.

## Positions and theses

- Name each variant for its direction, such as Quiet, Ledger, or Split. Never Option A.
- Write the thesis as one sentence that names what leads and for whom: "The trend line leads, so a reviewer sees direction before the number."
- Record two or three measurable facts each position should produce, such as blocks per row at 1280, control height, the first text in reading order, or the number of filled elements. These become the workers' must-differ lists and the facts distinctness checks.
- Write right when and costs as a concrete situation and a concrete price: "Hides active filters behind a button", "Costs 240px of table width"

### The rhyme test

Two theses rhyme when they promise the same difference in other words, such as "clean and focused" and "minimal and calm". If two positions share all of their measurable facts, they rhyme; replace one. A thesis built on an adjective alone ("modern", "bold") always fails.

## Count

Default to three. Build five only when the owner asks; past five, comparing turns into scrolling. Keys 1 to 5 cover the maximum.

## `variants.json`

The orchestrator writes it before any variant code, and the host reads `axis` and each variant's `id` and `name` from it.

```json
{
  "piece": "metric-card",
  "route": "http://127.0.0.1:5173/usage",
  "axis": "Structure",
  "variants": [
    {
      "id": "number-first",
      "name": "Number first",
      "position": "One large value, trend below",
      "thesis": "The value leads, so a manager reads the count in one glance.",
      "right_when": "The number alone drives the next decision",
      "costs": "Trend reads as secondary",
      "must_differ": ["first text is the value", "one block per row at 390 and 1280"]
    }
  ]
}
```
