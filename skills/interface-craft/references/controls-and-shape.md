# Controls and Shape

Defaults for button labels, control sizes, and corner radii; the project's own tokens win.

## Buttons and Labels

- Set height with `min-height` from the control ladder in `spacing-and-rhythm.md` and put `white-space: nowrap` on the label, so a button grows sideways and never taller
- If the label does not fit, shorten the copy first (verb first, usually one or two words). Then widen the control, stack controls full width at narrow widths, or move the action into a labeled menu. Never shrink the font or let the label wrap.
- Tabs, nav items, and badges follow the same rule, and a badge holds at most three words
- The same action keeps one label everywhere, in sentence case. A confirmation button names the action ("Delete project" beside "Cancel"), and a toggle is labeled by its on state.
- With an icon and a label, take 2 to 4px off the padding on the icon side so the pair looks centered
- An icon-only button is square at the ladder height and carries an accessible name
- Truncate non-button text only when the full text is one hover or click away

## Radii

- Keep a scale of at most four tokens. With none in the project, start from 4, 8, 16, and full: a 16 card with an 8 inset holds an 8 control, and an 8 control with a 4 inset holds a 4 tag. No screen types its own value.
- Inner radius is outer radius minus the inset (padding plus border), never below zero. Building outward, outer radius is inner radius plus the inset.
- A 12 card with 8 padding gives a 4 inner radius, and a 16 card with 8 padding gives 8. When the padding is at least the card's radius, the curves no longer interact, so the control keeps its own token.
- Choose tokens so the equation holds, for example card 16 = control 8 + inset 8
- When an inset cannot fit the tokens, derive the value in CSS as `calc(var(--radius-card) - var(--inset-card))`. Never type a literal.
- A child flush with the container edge stays at 0 and lets the container clip it (`overflow: clip`), or takes the container's radius
- Pills and circles are separate shapes and do not follow the formula
- The same component has the same radius on every screen

## Checks

- `rule:control.wrap` flags a button, tab, nav item, or badge label that wraps onto more than one line. Button-like controls fail and the rest warn.
- `rule:control.height` flags unequal heights in a row and more than three heights overall
- `rule:shape.nested` flags an inner radius that is not the container's radius minus the inset
- `rule:shape.radius` flags too many distinct radii, and any radius that is neither a project token nor explained by nesting

## What Goes Wrong

- A wrapping label that makes a button taller, fixed by shrinking the font
- A 12 card with 8 padding around an 8 radius button pinches the corners; the answer is 4
- A one-off radius of 10 that is neither a token nor derived
- A square-cornered image in a rounded card
- Pills mixed with square tags in one row
