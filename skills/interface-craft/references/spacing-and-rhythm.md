# Spacing and Rhythm

Defaults for spacing, alignment, and sizing. When the project has its own scale, `ui_measure.py init` reads it and the checks target that scale instead; snap to the project's values and use these only to fill gaps.

## Defaults

- Base unit 4px. Layout steps on multiples of 8: 4, 8, 12, 16, 24, 32, 48, 64, 96. Use 12 only inside controls.
- Spacing nests, and each level is at least 1.5 times the one inside it: control internals 8 to 12, items 8 to 12, fields and cards 16 to 24, groups 32 to 40, sections 48 to 64, major transitions 64 to 96.
- Between groups, aim for at least twice the space used within them, for example 8 inside and 16 or more between. 1.5 times is the floor the check enforces.
- A heading binds to what it governs: space above is at least 1.5 times space below (for example 32 above and 12 below, or 24 above and 8 below).
- Page gutters 16 under 640px, 24 to 1024px, 32 above. Content width: data and app screens 1280 to 1440, detail and forms 720 to 960, settings columns 560 to 720, prose 65ch. Sections on one page share one content column whose edges do not move between sections (`rule:layout.content`).
- Panel padding 16 (dense), 24 (default), or 32 (spacious). Siblings share one value, and an inner container never pads more than its parent. Avoid a panel inside a panel; separate with space, a hairline, or a tonal surface.
- Control heights: 32 dense, 40 default, 48 touch or marketing. Use at most three per product. Controls in one row share a height, and an icon button is square at that height.
- Icons at 16, 20, or 24 with an 8px gap to the label (4 in dense rows). Match stroke weight to the text weight and center on the text, not the line box.
- Row heights on the same ladder: 40 dense, 48 default, 56 or 64 for two-line rows.
- Choose two to four vertical key lines per screen (page edge, content column, field column, trailing action edge). Elements that share a column share an x position.
- Set heights with `min-height` and `padding-inline`, not with vertical padding. Use `gap` for sibling spacing and avoid margin on the same pair, which doubles it.
- Group with proximity first. Add a border, tint, or shadow only for containment that proximity cannot express.

## Checks

- `rule:grid.spacing` flags authored padding, margin, and gap off the 4px grid, and `rule:grid.size` flags control and icon boxes. Hairlines of 1px pass. Each finding names the nearest on-grid value.
- `rule:scale.spacing` allows 8 or fewer distinct spacing values on a screen.
- `rule:component.conformance` flags the instance that differs from its siblings (one card with 20px padding among 24px cards).
- `rule:control.height` flags unequal heights in a row and more than three heights overall.
- `rule:align.near_miss` flags left edges 0.5 to 8px apart that read as one column.
- `rule:rhythm.group_ratio` and `rule:rhythm.heading` flag outer gaps that are not larger than inner gaps and headings that float between sections.

## What Goes Wrong

- Values typed by eye or copied from a design tool (13, 18, 22) instead of taken from the ladder.
- The same component padded differently on two screens because each was styled alone.
- A heading centered between two sections, so it reads as a caption of the one above.
- Padding added to nudge something into line. Fix the shared container or grid instead; a 2px nudge in one place breaks in the next breakpoint.
- Nested cards that each add padding, which compounds into a 48px inset and shrinks the content.
- A 36px input beside a 32px button, or a search field that is 4px taller than the buttons beside it.
- Borders that change an element's size when added. Use `box-sizing: border-box` and check the row after adding one.
- Equal gaps everywhere. A page with one spacing value has no hierarchy; the contrast between inner and outer gaps is what groups content.
