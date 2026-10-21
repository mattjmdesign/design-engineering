# Representation

How to read what was handed over, read the data, and choose a pattern. The pattern answers the user's question about the data; components and layout come after it.

## Read the input

| Kind | Read it as | Watch for |
| --- | --- | --- |
| Request | A job with a solution attached ("we need a dashboard") | The named solution may be narrower or wider than the job. Keep it unless the job shows it fails, and explain the tradeoff |
| Outline | A draft order of content | An outline's order is the writer's sequence, not the user's priority |
| Data | Fields, types, counts, ranges, empty values, and change over time | A five-row sample hides the long name, the empty value, and the 400-row case |
| Meeting notes | Decisions, suggestions, and open items, each with who said it | A suggestion recorded as a decision, or a decision nobody owns |
| Feedback | A symptom, where it happens, how often, and a suggested fix | The suggested fix is a hypothesis about the problem; test it against the symptom |

## Read the data shape

Answer these before choosing:

- What is one record, and which field identifies it?
- How many records are there: one, a few (under about ten), dozens, or hundreds?
- Which fields does the user compare, and which only identify a record?
- Is there a time dimension, and does the user care about the trend or the latest value?
- What does the user do next with a record: open it, change it, approve it, or nothing?
- Who sees which records? A permission split changes what a list may show and what a count may say.

## Choose the pattern

| The user needs to | Pattern | Not (the usual default) | Because |
| --- | --- | --- | --- |
| Compare many records across several attributes | Table: identifier first, sortable columns, numbers right-aligned | Card grid | Cards break the shared columns that make comparison possible |
| Act on a few items now | List with status leading and the action trailing | Figure cards or a full table | A count says how many, not which; a table adds columns nobody scans |
| Read or edit one record | Detail: identity header, main column, and an aside of label and value pairs | A card per field, or a disabled form | Labels and values read as pairs; a disabled form looks broken |
| See change over time | Line chart with the current value stated; a sparkline beside a value in a row | Bars per day over months, or a pie | A line shows slope; a pie has no time axis |
| Compare a few quantities across categories | Bar chart sorted by value, labeled directly | Line chart, or a pie with many slices | Categories have no order to connect, and angles compare poorly |
| See parts of a whole | One stacked bar with labels, or a short table with percentages | A donut with six or more slices | Small slices cannot be compared |
| Know one number that drives a decision | One figure with its period and comparison | A row of four figures by habit | Extra figures dilute the one that matters |
| Track items through stages | List grouped by status; a board only when people move items between stages | A board by default | A board spends width on sparse columns and hides counts |
| See events in time order | Timeline or feed, newest first, grouped by day | A table of timestamps | Order and gaps matter more than columns |
| Plan by date | Calendar or agenda, with an agenda list on narrow screens | A table of dates | Position in time carries the meaning |
| Move through a hierarchy | Tree or nested list with a breadcrumb | Cards per level | Depth and position need to stay visible |
| Choose between a few options with tradeoffs | Comparison table with one column per option | Separate cards with unaligned rows | Rows must line up to compare |
| Enter or change data | One-column form grouped by concern | A grid of inputs | Eyes and keyboard move down one column |
| Read long text | A prose column capped at about 65 characters | Text across the full content width | Long lines lose the reader |
| Start from nothing | One block: what this is for, one sentence, one primary action | Feature cards and an illustration | The only useful act is the first one |

## Close calls

- **List or table:** a table once people compare two or more attributes across rows, and a list when they scan one thing and act on it
- **Drawer or page:** a drawer for a quick look that keeps the list in view, and a page for anything long, linkable, or compared with what is behind it
- **Chart or table:** a chart when the shape is the answer (rising, falling, an outlier), and a table when people need exact values. Many dashboards need both: the chart for the shape and the table for the lookup.
- **Still close:** when the call stays close after reading the task and the data, offer a `$ui-variants` run on that one region rather than choosing blind

## Defaults agents reach for

- The figure row, chart, and table dashboard, assembled before anyone knows which decision the page supports
- Equal cards for every collection, which make the one important item look like the rest
- A chart because the data has numbers, when the user needs one value or a list of names
- A new pattern for records that another screen already shows another way
- Tabs for two short sections that fit on one page
- A modal for long or multi-step work

## Acceptance checks

- Each check is observable on the rendered screen with the fixture: "with three late projects, all three show owner and due date at 390", not "the page is clear"
- Cover the empty case and the largest realistic case for every collection
- When the input says who may act, include a direct request from someone who may not
- A check that depends on an open question names that question

## Requirement lines on request

Write FR and NFR lines only when asked. One behavior per line, each with an example that a plausible wrong build would fail. A stated default is weaker than a pair of examples that separates right from wrong.

```text
FR-01: [actor + behavior]. Accept: [observable example]. Depends/unknown: [...]
NFR-01: [constraint + condition]. Verify: [scoped check]
```

```text
FR-01: An editor can rename a project; a viewer cannot. Accept: a viewer's direct rename request is rejected by the server and the stored name is unchanged. Depends: role data per organization.
NFR-01 (proposed, not agreed): The project list shows usable content within 2 s on the client's field phones over a throttled mobile profile. Verify: measure on that profile once the client confirms the target.
```

Close the list with out-of-scope items, assumptions, and open questions. Mark a proposed target as proposed until someone agrees to it.
