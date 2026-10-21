# Color and Elevation

Defaults for palette roles, contrast, status colors, dark themes, depth, and layering. Use the project's tokens when they exist and add a role only when no existing one fits.

## Defaults

- Assign roles before values: canvas, surface, raised surface, border, three text tiers (primary, secondary, muted), accent, focus, and four status families (success, warning, danger, info). Each status family has a text color, a tint, and a border.
- Neutrals carry most of the interface. Tint the gray ramp faintly toward the brand hue and keep eight to ten steps. Use one accent for actions, selection, links, and focus, and at most two non-status hues on a screen.
- Contrast ratios against the real background: 4.5 for normal text (muted text included), 3 for large text, icons, control borders, and focus rings. Text over an image or gradient needs a scrim, and the checker reports it as unknown, which counts as unresolved.
- Use near-black rather than pure black on white. Pure `#000` on `#fff` glares at length, and a near-black from the neutral ramp keeps the palette coherent.
- Status never relies on color alone. Pair it with an icon or a word. Put dark text on light tints (for example amber 900 on amber 50), because yellow and orange fail contrast as text on white.
- Dark theme is a second set of values for the same roles. Surfaces get lighter as they rise (in OKLCH lightness, canvas near 0.18, surface 0.21, raised 0.25), text is off-white near 0.93, the accent loses 10 to 15 percent of its chroma, and status tints become low-lightness fills with light text. Recheck every text and fill pair, because a tint that passes in light mode often fails in dark mode.
- Pick one primary depth model per surface: a border, a tonal step, or a shadow. Using all three muddies the hierarchy.
- Shadows come in at most four steps: none, sm for resting cards, md for menus and popovers, lg for modals. Layer two soft shadows, such as `0 1px 2px rgb(0 0 0 / .06)` with `0 4px 12px rgb(0 0 0 / .08)`, and avoid colored glows.
- Radius scale, nesting, and shape rules live in `controls-and-shape.md`
- Reserve pills for tags, statuses, and short filters. Buttons, tabs, and inputs share one shape.
- Borders are 1px in a quiet neutral. Where two bordered elements touch, drop one border so the line is not doubled.
- z-index is a ladder: 0 base, 10 sticky, 20 dropdown, 30 backdrop, 40 modal, 50 toast. No value outside it.
- Charts use at most six categorical hues that also differ in lightness, label series directly instead of relying on a legend, and keep marks at 3:1 against the background.
- Gradients, glass, and blur are brand moments with a stated reason. Keep them off controls and data.

## Checks

- `rule:contrast.text` computes the ratio per text node with ancestor alpha composited, in each captured theme. Capture both themes with `--themes light,dark`.
- `rule:contrast.ui` covers borders, icons, and focus rings against their neighbors.
- `rule:color.budget` flags more than five text colors or more than two non-status hues.
- `rule:shape.depth` flags more than four shadow styles or six z-index values.

## What Goes Wrong

- Gray text on a tinted card that passes against white and fails against the tint.
- A success badge with a light-mode tint pasted into dark mode.
- Five accent colors, or an accent used on everything, so the primary action stops standing out.
- Muted text at `opacity: .5`, which changes with whatever is behind it.
- A card with a border, a large shadow, and a gradient, all at once.
- A dark theme made by inverting the light theme. Shadows disappear, brand colors vibrate, and the elevation order reverses.
- A modal at `z-index: 9999` fighting a toast at `99999`.
- White text on a photo with no scrim, which passes on the one image the designer tested.
