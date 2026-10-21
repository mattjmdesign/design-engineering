# Type

Defaults for scale, weight, measure, numerals, and fonts. When the project defines a type scale, use it; these fill gaps.

## Defaults

- Define roles before sizes: page title, section heading, body, label or meta, and data or code. Use the fewest roles that carry the hierarchy. A role is one token set (size, line height, weight, color), so same-level headings never differ.
- Product scale: 12, 14, 16, 20, 24, 32, 40. Body is 14 for dense tools and 16 for reading or touch, and below 14 it reads small. Page title 24 to 28, section heading 16 to 20, meta 12 to 13. Use six sizes or fewer on one screen.
- Marketing scale: body 16 to 18, subhead 18 to 22, page headline 40 to 64 using `clamp()` so it never exceeds the available width. Keep section headings two to three steps below the headline.
- Line height is unitless and lands on the 4px grid: 12 over 16, 14 over 20, 16 over 24, 20 over 28, 24 over 32, 32 over 40, 40 over 48. Body stays between 1.4 and 1.65, large headings between 1.1 and 1.3.
- Hierarchy comes from size, weight, and color value together. Use two or three weights at least two steps apart (400 with 600, not 400 with 500, and 700 only for display), and skip a weight when unsure. Avoid weight 300 below 18px.
- Cap prose at 65 to 75 characters per line with `max-width: 65ch`. Tables, forms, and code are not prose.
- Tabular numerals (`font-variant-numeric: tabular-nums`) for tables, prices, counters, and times. Right-align numeric columns, keep decimals consistent, and put units in a smaller muted run.
- Headings, captions, and short descriptions use `text-wrap: balance`, which browsers apply to short blocks only (six lines or fewer in Chromium, ten in Firefox). Body copy uses `text-wrap: pretty`, which is slower, so use it on longer blocks only. Skip both on code and under `white-space: nowrap`. Long identifiers and URLs use `overflow-wrap: anywhere`.
- Break headings by phrase, not by width
- Use real typographic characters: curly quotes, an en dash for ranges, one ellipsis character
- Sentence case for headings, buttons, and labels unless the brand says otherwise.
- A system font stack is a sound default for product UI. A brand font is a decision: at most two families and four files, `font-display: swap`, and a `size-adjust` fallback so the swap does not move the layout.
- Text colors come from the neutral ramp in three tiers (primary, secondary, muted), with no more than five text colors on a screen. Do not gray text with `opacity`, which breaks contrast on tinted surfaces.
- Truncation is a last resort. Prefer wrapping to two lines. Truncate a secondary value only when the full value is one click away, and never truncate identifiers people must compare.

## Checks

- `rule:type.scale` flags more than six sizes, more than three weights, adjacent sizes closer than 1.08 in ratio, and text under 12px.
- `rule:type.line_height` flags body text outside 1.4 to 1.65.
- `rule:type.wrap` flags a heading that ends on a short last line without balance or pretty wrapping
- `rule:type.role` flags same-level headings or repeated component titles that differ in size, weight, or color
- `rule:type.measure` flags prose outside 45 to 80 characters per line.
- `rule:type.numeric` flags numeric columns without tabular numerals or right alignment.
- `rule:font.fallback` flags web fonts that swap without a metric-matched fallback.
- `rule:color.budget` counts distinct text colors.

## What Goes Wrong

- Seven sizes on one screen because each section chose its own, including 13, 14, and 15px side by side, which the eye reads as a mistake
- Bold everywhere. When every label is 600, nothing is emphasized; make most text regular and let one level carry weight.
- Captions at 11px in a pale gray on a tinted card.
- A paragraph set at line height 1.2, or a heading at 1.6.
- A 110 character line because the container fills a wide screen.
- Centered paragraphs and centered form labels. Center a short focal message at most.
- Proportional digits in a column of money, so the right edge jitters.
- A 72px headline that wraps one word per line at 390px.
- Faux bold or italic because the loaded font file lacks the weight.
