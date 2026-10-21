# States and Feedback

Defaults for loading, empty, error, success, disabled, and long-content states, plus the feedback surface each one uses. `state-harness.md` covers how to force and capture each state.

## Defaults

- List the states a surface can reach before styling it: ready, loading (first load), refreshing, empty (first use), empty (no results), error (recoverable), error (no permission), partial, offline, success, disabled, long content, and many items. Design the ones that apply, not the ones that look good.
- Loading: show nothing for the first 200 to 300ms, then a skeleton that repeats the final structure at its real size when the shape is known, or a 16 to 20px spinner next to the label for an action. Once shown, keep it for at least 400ms so it does not flash. Reserve the final height so content arriving does not push anything.
- Refreshing keeps the old content visible and dimmed or marked, with a small "Updated 2 min ago" note when decisions depend on freshness.
- Empty (first use): a heading that says what will live here, one sentence on how it gets filled, and one primary action. Add a small illustration or icon only if it helps, and never fabricate sample data to fill the space.
- Empty (no results): echo the query or filters, offer "Clear filters", and suggest a broader search. This is a different message from "nothing exists yet".
- Error: say what failed, what it affects, and what to do next, with a Retry button when retrying can help. Keep entered work. Put the message where the failure happened: inline for a field, in the region for a data view, as a banner for the page. Technical detail goes behind a "Details" disclosure.
- Permission: name the scope and the owner who can grant access, with a request or return action. Do not present it as a generic failure.
- Success: confirm in place when the person is looking at the result. Use a toast (4 to 6 seconds, pausable, dismissible, announced politely) for background work. Offer Undo for destructive actions instead of a confirmation dialog when undo is possible.
- Feedback surface by scope: inline for one field, region status for a section, toast for a non-blocking result, banner for a persistent page condition, dialog only for a decision that cannot wait. Never put a failure with recovery actions in a toast.
- Optimistic updates show the new state at once and roll back with an explanation if the server refuses.
- Buttons keep width and label while pending, prevent a second submit, and show the outcome next to the button.
- Disabled elements say why nearby, or are replaced by guidance when the person can resolve the cause.
- Motion: 120 to 160ms ease-out for hover and focus, 200 to 300ms for overlays and disclosure, animating opacity and transform only. Honor `prefers-reduced-motion` by swapping movement for an instant or fading change.
- Long and many: a 60-character name wraps or truncates by design, 200 rows virtualize or paginate, a count of 1,284 fits its badge, and a missing avatar has an initial fallback.

## Checks

- Every state in the matrix is captured with `ui_measure.py states`. A state counts as rendered only when its `expect` element is present, which `rule:state.confirmed` enforces as FAIL.
- `rule:overflow.clip` and `rule:overlap.text` run in each state, so long content and error messages are tested for clipping.
- `rule:console.errors` and `rule:network.failed` expose a state that was forced through the wrong path.
- `rule:media.images` flags images that distort, upscale, or ship far larger than they display.

## What Goes Wrong

- Only the loaded state exists, and every other state is a blank region or a stack trace.
- The page-wide spinner replaces content that was already on screen.
- A skeleton of three large blocks that becomes a dense table.
- "No results" shown for an account that has never created anything.
- An error toast that vanishes before it can be read and takes its Retry button with it.
- "Something went wrong" with no cause, effect, or next step.
- Success announced twice, as a toast and as an inline banner.
- A disabled primary button with no hint about what is missing.
- A loading label that changes width and moves the neighboring button.
- A forced state that never rendered because the app shows the ready state while a request is still failing, so the screenshot proves nothing.
