# Component internals

Layout inside a component, on the 4px grid. Most components are a few slots in a row or a stack: fix the slots that repeat, let one slot flex, and put every edge on a 4px step.

## Slots

- Most rows are a leading slot, a content slot, and a trailing slot. Leading and trailing get fixed widths; content takes `minmax(0, 1fr)` and wraps or truncates by a stated policy.
- Slot sizes and the gaps between them are 4px steps. An icon box is 16, 20, or 24, with an 8px gap to its label (4 in dense rows).
- A slot keeps its width when it is empty, so a row without an icon still starts its text on the same line as the rows around it

## Repeated rows

- Set the tracks once on the row: `grid-template-columns: 2.5rem minmax(0, 1fr) 6rem auto` for avatar, text, status, and action. A trailing slot whose content varies (a badge, a date, a menu) gets a fixed width so it lines up down the list.
- Row heights follow the ladder: 40 dense, 48 default, 56 or 64 for two lines
- Dividers start at the content slot's edge, not the row's edge
- A list of many rows that people compare is a table; the row rules above are for scanning and acting. Table rules are in `$interface-craft`'s tables-and-lists reference.

## Toolbar

- One row, one control height (32 or 40), and one gap. Search comes first at a fixed width (280 to 360px), then filters, a flexible spacer, and the primary action at the end edge.
- The toolbar's start and end edges sit on the same key lines as the collection under it
- Active filter chips sit on their own row below, starting at the same edge
- When the row no longer fits, filters collapse into one Filters button before the search shrinks

## Card

- Header, content, and footer each use the card's padding (16 dense, 24 default) with one gap between them, and sibling cards share the same padding
- The title and the first line of content start on the same left edge. An icon sits in the header's leading slot, not floating above the title.
- A nested element's radius is the card's radius minus the inset (`rule:shape.nested`)
- Cards in a row share a height. When titles wrap differently, use `subgrid` rows or a fixed header height so bodies and footers still line up.
- A card inside a card is usually one container too many; separate the inner groups with space or a hairline instead

## Figure card

- A label (12 to 13px, muted), a value (28 to 32px, tabular numerals), and an optional change with sign, icon, and period
- Every figure in a row uses the same slot order, padding, and height, so values share one baseline across the row

## Form row

- Label above input with 6 to 8px between, and 16 to 24px between fields. Helper text and the error share one reserved slot under the input.
- Inputs and the button beside them share one height
- In a side-by-side layout the label column is a fixed track (160 to 200px, or 240 to 300px in settings), and every input starts on one key line

## Header row

- The title sits in the content slot and the actions in the trailing slot, with the actions' end edge on the content end key line
- Space above a section header is at least 1.5 times the space below it, so the header binds to what it governs

## Check

- Picture the 4px lattice over the component at 4x: every edge, baseline, and icon box lands on it, except 1px hairlines
- `python3 $interface-craft/scripts/ui_measure.py xray .ui-evidence/RUN/E## --region SELECTOR --zoom` crops one component at 4x with the lattice. `rule:grid.spacing` and `rule:grid.size` flag off-grid values, and `rule:align.near_miss` flags slots that miss their line by a few pixels.
