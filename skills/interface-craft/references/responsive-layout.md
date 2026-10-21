# Responsive Layout and Content Resilience

Design layouts that survive real content, text scaling, and changing space. The CSS targets the web. For native apps, apply the same content-stress method to windows, size classes, safe areas, and dynamic type.

## Method

1. Start at the narrowest supported width with the complete primary task.
2. Add space until the composition, not a device name, justifies a topology change. Define what changes: columns, order, visibility, control grouping, navigation, or density.
3. Continue to a comfortable wide layout and cap any region whose readability stops improving.
4. Capture immediately before and after each change.

Use few breakpoints, chosen where the content breaks. Responsive design is not uniform shrinking. It can move supporting content below the primary task, turn a rail into a drawer, stack labels over fields, group a toolbar or move it into an overflow menu, keep one critical action sticky, and turn a table into priority columns, a scroller, or a record list. Preserve task order and information priority across all of them.

## Intrinsic Sizing

```css
.child { min-width: 0; }                                  /* flex or grid child holding text */
.row { grid-template-columns: minmax(0, 1fr) auto; }      /* a track that may shrink */
.frame { width: min(100% - 2 * var(--gutter), var(--max)); margin-inline: auto; }
.cards { grid-template-columns: repeat(auto-fit, minmax(min(100%, 18rem), 1fr)); }
.pad { padding-inline: clamp(1rem, 3vw, 3rem); }          /* only where fluid helps */
.full { min-height: 100svh; }                             /* min-height, so text can still grow it */
```

- Use container queries for components whose layout depends on their own width, and media queries for page composition and user preferences.
- Prefer `min-height` over `height` for any container that holds text.
- On mobile, `svh` fits the small visible viewport, `dvh` follows browser chrome (check that resizing during scroll does not reflow), and `vh` can be hidden behind expanded chrome. Respect safe-area insets for edge-pinned controls.
- Keep shell dimensions (sidebar, header, gutters) in one place so pages do not re-implement offsets.

## Scroll and Overflow

- Name the owner of each axis: one region scrolls vertically, only a deliberate region scrolls horizontally, and sticky regions say what they stick within. Avoid nested scroll areas, which trap wheel, touch, and keyboard.
- Pick a policy per constrained region: wrap when every line stays useful, scroll when spatial relationships matter, truncate only nonessential text whose full value is available elsewhere, move secondary content, and never clip essential text or controls.
- Repair unexpected overflow in order: find the element that actually protrudes (`rule:overflow.page` names the culprits, and warns when an app shell clips the document while its main scroll region scrolls sideways instead), check fixed widths, negative margins, transforms, absolute positioning, and unbroken strings, add `min-width: 0` to the right child, replace fixed tracks with `minmax(0, 1fr)`, then wrap or relocate content. `overflow-x: hidden` on the body hides the symptom and is not a repair.
- Use `overflow-wrap: anywhere` for URLs and identifiers. Use `word-break: break-all` only for data that tolerates splitting anywhere. Avoid `white-space: nowrap` on mixed-content rows unless the overflow is handled.
- Do not make the text smaller as the first fix. Change topology, width, wrapping, or priority first.

## Navigation and Actions

- Navigation changes presentation without changing meaning, and the current-location cue survives every topology. A hamburger needs a visible label or a familiar icon, keyboard operation, focus management, and a way to dismiss.
- Give action groups an order of priority. When they wrap, keep related label and control pairs together, move low-priority actions into a labeled overflow menu before shrinking labels into icons, and avoid a single orphaned action on its own line.
- Keep persistent bottom actions above safe areas and browser controls.
- Test headers with a long product name, many nav items, signed-in state, a banner, and browser zoom.

## Data-dense Surfaces

- Choose one strategy per table: horizontal scroll when comparison is the task (keep the key column visible), priority columns when secondary data can wait, a record list when use is per record, or a detail view when rows carry many actions. Do not turn every table into cards on mobile.
- Headers stay associated and visible on large sets, numbers stay aligned, and row actions work by keyboard and touch. Charts keep legends and labels readable without hover and offer a text or table alternative.

## Content Stress

Test more than width:

- Labels 30 to 50 percent longer, and translated copy
- A very long name, email, URL, identifier, and number
- Zero, one, typical, and many items; missing optional content
- Validation errors under several fields; loading, empty, partial, error, success, disabled, and permission states
- Text enlarged to 200 percent and a 320px wide viewport
- Reduced motion, forced colors, and the dark theme

## Stability

An intentional topology change is fine. An unexpected jump that makes people lose their place or mis-aim is not.

- Compare materially different states at one size: a short route against a long route, filtered against full results, skeleton against content, fallback font against web font, closed overlay against open, and with and without banners or validation.
- For client-routed apps, confirm the runtime is attached and the transition stays in the same document. A full reload hides router bugs.
- Compare persistent anchors (logo, header edge, main rail, primary action) by x, y, width, and height with `ui_measure.py compare`. A low layout-shift score alone does not prove stability.
- Headless captures can hide scrollbars. A classic scrollbar appearing on long pages shifts a centered layout by the scrollbar width, so also check that the root sets `scrollbar-gutter: stable` or that the layout does not depend on viewport width.
- Reserve intrinsic size for images, video, and embeds (width and height or `aspect-ratio`), make skeletons match final geometry, tune the fallback font to the web font, and keep loading labels and counters geometrically stable. Do not hide scrollbars to disguise movement.
- Prefer fixture-backed app states for content stress. Swapping text in the DOM shows wrapping pressure only, and labels as such. Never edit production content to test layout.

## Verification Matrix

| Dimension | Minimum evidence |
| --- | --- |
| Narrow | 390px, plus 320px for reflow |
| Intermediate | A width on each side of every topology change |
| Wide | 1280px or larger, and any max-width behavior |
| Content | Long label, dense, sparse, and an unbroken value |
| State | Loading, empty, error, success, disabled where reachable |
| Input | Keyboard, plus pointer or touch |
| Stability | A same-size transition with named anchors |

At every width, check for no page-level horizontal scroll, no clipped or overlapping content, no ambiguous control wrapping, logical reading and focus order, a visible primary action, no overlapping targets, sticky regions that do not cover content, and overlays that stay inside the viewport.
