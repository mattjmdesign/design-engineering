# Content regions

Templates for what goes inside the content region of a shell, which is where layout most often goes wrong when pages are not defined. Each template lists the regions in reading order, the tracks, the cap, the narrow behavior, two or three alternatives, and the mistake agents usually make. The project's shell, tokens, and recorded grid win over any number here, and every pixel value is a starting point that a render confirms.

## Shared rules

- One container owns the cap and gutters, and every section inside it shares its edges
- Peer columns follow the container's width (`auto-fit` or container queries), not the viewport, because a sidebar makes the content area narrower than the window
- Fractions apply to top-level blocks: one block gets a cap, a primary and secondary pair gets `2fr 1fr`, and three or more peers get `auto-fit`
- A page or group heading gets a description line only when it says something the heading cannot, such as a condition, a consequence, or the next step
- Every number, name, and chart value comes from real data or the brief; a template slot with nothing real to show is left out

## Dashboard

- **Regions:** page header (title, date range or filter, one action at the end edge); attention strip (what needs action now, by name); headline figures (four or fewer); primary chart beside a narrower module; detail table or activity, full width
- **Tracks:** figures `repeat(auto-fit, minmax(min(100%, 14rem), 1fr))`, or for exactly four, 4, 2, and 1 across by container query so no row holds a lone card; chart row `minmax(0, 2fr) minmax(0, 1fr)`; the table spans the column. Charts in one row share a height of at least 240px, label series directly, and have a table or text alternative for their key values.
- **Cap:** 1280 to 1440
- **Narrow:** figures go two by two, then one column; the module drops below the chart; the table keeps its identifier and status and scrolls locally or becomes one record per row
- **Filters:** when filters exist, one filter bar under the header drives every figure, chart, and table on the page
- **Alternatives:** list-first, where the attention list is the page and figures shrink to a summary line (operations work); primary plus aside, with one large chart or table at 2fr and filters and figures stacked at 1fr (analysis); figures only, a short grid of figures with sparklines when each one opens its own page
- **Usual mistake:** the figure row, chart, and table template with invented numbers, equal-weight cards, no attention strip, and no cap, so the table stretches across a wide monitor

## List

- **Regions:** page header (plural resource name, create action at the end edge); toolbar 16px above the collection (search, filters, sort, view toggle) with active filters as chips under it; one table or list in one container; a footer with the count or pagination at the table's inset
- **Tracks:** one column. The table takes the full cap only when its columns need it; a list of a few columns caps narrower.
- **Cap:** 1280 to 1440 for wide tables, about 960 for a few columns
- **Narrow:** search stays, filters move into a sheet behind one button, and the table becomes priority columns or one record per row
- **Alternatives:** a table when people compare columns; list rows with leading and trailing slots when people scan and act; cards only for objects with media or mixed content
- **Usual mistake:** toolbar controls at three heights and three left edges, a table whose edge misses the content column, and cards for a collection people need to sort

Column, row, and count rules for the collection itself are in `$interface-craft`'s tables-and-lists reference.

## List and detail

- **Regions:** a list pane of set width and a flexible detail pane with its own cap; each pane scrolls on its own
- **Tracks:** `minmax(20rem, 25rem) minmax(0, 1fr)` (list 320 to 400px); detail content caps at 720 to 960 inside its pane
- **Cap:** the shell's full content width; the detail's own cap keeps lines readable
- **Narrow:** below about 840px one pane shows at a time; opening an item shows the detail with Back, and the list keeps its scroll position. A placeholder fills the detail when nothing is selected.
- **Alternatives:** a list with a drawer for quick looks; a list that navigates to a detail page for long or linkable records; three panes (folders, list, detail) only when people switch folders often
- **Usual mistake:** both panes scroll with the page, the selected row has no selected state, and two panes squeeze into 390

## Settings

- **Regions:** section nav or tabs; groups stacked vertically, each with its heading in a left column and its fields on the right; save per group or a sticky bar that appears on change; the danger zone last, with its own border and each action naming its consequence
- **Tracks:** label column 240 to 300px, fields up to 560px (roughly `2fr 5fr` inside the cap); one column when narrow
- **Cap:** about 960 overall, fields 560 to 720
- **Descriptions:** a group gets a description only when it states a condition or consequence its heading cannot, such as "Applies to new projects only"
- **Narrow:** headings sit above their fields in one column, and the section nav becomes a select or a list page
- **Alternatives:** one page with an anchored section nav (a few groups); a list of sections that each open a page (many groups); tabs (three to five peer sections)
- **Usual mistake:** fields stretched across a wide screen, a description under every heading, card padding that drifts between groups, and one Save far below the fields it saves

## Record detail

- **Regions:** identity header (title, status, one metadata line, actions); tabs for peer sections when there are several; main column; aside of metadata as label and value pairs
- **Tracks:** main `minmax(0, 2fr)` up to 960px, aside `minmax(17.5rem, 1fr)` at 280 to 360px
- **Cap:** default width, about 1280, never full bleed
- **Narrow:** below about 900px the aside stacks under main or moves to a sheet, and secondary actions collapse into one menu beside the primary
- **Alternatives:** one column with metadata in the header (few fields); main plus aside (many fields); main plus a related list (a parent with many children)
- **Usual mistake:** one long column with metadata mixed into the body, or metadata in an equal-weight card beside the main content

## Form

- **Regions:** page header; one column of fields grouped by concern, with a heading per group when there is more than one; actions at the start edge of the field column, full width at 390; a sticky action bar for long forms
- **Tracks:** one column; each field's width follows its expected value, up to 480 to 560px
- **Cap:** about 720
- **Narrow:** the same column at full width, with 16px input text so the browser does not zoom
- **Alternatives:** a dialog for three fields or fewer; a multi-step flow of five steps or fewer when the form has distinct concerns; inline editing on the record for a single field
- **Usual mistake:** a two-column grid of inputs, full-width inputs for two-digit values, and a description under every group heading

Field, validation, and submit rules are in `$interface-craft`'s forms reference.

## First-run empty

- **Regions:** the shell stays; the content column holds one block with a title that says what to do, one sentence on the benefit and the next step, one primary action, and an optional secondary link
- **Tracks:** one block on the content start line, or centered in the region when the page has nothing else
- **Cap:** about 560 for the block, inside the page's normal column
- **Narrow:** the same block, with the action at full width
- **Alternatives:** an import or sample-data action beside the primary when setup is slow; a checklist of three steps or fewer when setup has distinct steps; the real empty table with a one-line prompt in its body when people already know the product
- **Usual mistake:** an icon in a circle over three feature cards, two primary buttons, or a hero that replaces the shell

## Sources

Region orders, tracks, and caps paraphrase public layout guidance from Shopify Polaris, IBM Carbon, Atlassian, GitHub Primer, GOV.UK, Material's canonical layouts, Stripe Apps, and Radix Themes. Several usual-mistake lines adapt Impeccable (Paul Bakaus, Apache-2.0).
