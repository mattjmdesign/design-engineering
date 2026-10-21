# Tables and Lists

Defaults for choosing a structure, laying out columns and rows, and keeping collections honest at zero, one, and many items.

## Defaults

- Pick the structure from the task. A list scans and selects, a table compares values across shared columns, cards hold distinct objects with mixed content, and a description list pairs labels with values. Equal cards are not a default for a collection.
- Table rows are 48px (40 compact, 56 with a secondary line). The header row is 40px, sticky, and in the secondary tier at 12 to 13px weight 600. Cell padding is 12 to 16px inline, and the first column starts on the page's key line.
- Align by data type: text left, numbers right with tabular numerals, dates and statuses left, and a header always aligns with its cells. Fixed widths for numbers, dates, statuses, and actions, flexible width for the text column, and `table-layout: fixed` once there are many rows.
- The identifier column comes first, in weight 600 and linked when it opens the record. The actions column is last, has a fixed width, and is visible without hover.
- Separate rows with a 1px hairline rather than zebra stripes. Hover tints a clickable row, selection is a different and stronger tint, and the focus ring stays visible on top of both.
- Missing values show a dash, never an empty cell. Dates, currency, and units use one format per column.
- Truncate only secondary columns, to one line with an ellipsis and the full value in the detail view. Identifiers that people compare wrap or stay whole.
- A list row has a leading slot (icon or avatar, 32 to 40px), primary text with one line of secondary text, and a trailing slot for status or action. Slots keep a fixed width so rows align down the list. Dividers start at the text edge.
- Show the real count: "Showing 1 to 50 of 214". A number in a heading or tab ("14 specs") equals the rows it describes.
- Filters sit above the collection. Active filters appear as removable chips with one "Clear all". The sort indicator appears on the sorted header only, and `aria-sort` is set.
- Bulk selection uses a 40px checkbox column. When something is selected, a bar shows the count and the actions, and "Select all" states its scope (this page, or all 214).
- Pagination suits reference tables. Infinite scroll or virtualization suits feeds, and it must keep the footer reachable and restore position when returning from a detail view.
- Narrow screens choose one of: priority columns with the rest in a detail row, a card per record, or horizontal scroll with a sticky first column. Do not shrink the type to fit.
- Loading skeletons repeat the row structure at its real height, so the content arrives without a shift.
- Stress every collection with zero, one, a typical count, 100 or more, and a name that wraps to three lines.

## Checks

- `rule:content.count` compares the numbers written in the UI with the largest repeated collection on the page.
- `rule:type.numeric` flags numeric columns that are not right-aligned with tabular numerals.
- `rule:align.near_miss` flags headers and cells whose left edges differ by a few pixels.
- `rule:target.size`, `rule:a11y.names`, and `rule:a11y.clickable` cover row actions, icon buttons, and rows that act as links but are plain `div` elements.
- `rule:overflow.clip` flags text cut off inside cells.
- In the state matrix, capture `empty`, `no-results`, `loading`, `error`, and a long-content variant.

## What Goes Wrong

- A heading says "13 specs" over a table of 14 rows, because the count came from a different list.
- Row actions that appear only on hover, so touch and keyboard users never see them.
- Numbers left-aligned, or right-aligned without tabular numerals, so digits do not line up.
- Every cell truncated, which hides the identifiers people came to compare.
- A column whose width changes as filters change.
- A "No results" message that also appears when the account has no data at all.
- Cards used for a 60-row collection that people need to sort and scan.
- Selected, hovered, and focused rows that look the same.
- A skeleton of three tall cards that turns into a dense table.
- Infinite scroll with the footer links below an endless list.
