# Judgment checklist

What the detectors cannot see. Walk the list on the rendered screen after the capture and the source scan, at 390 and 1280. Each line is a question, how to check it, and what to do when it fails. Record every failed line in the report with the element and the fix.

## Content

- **Each line adds a fact.** Cover a line and read the screen without it. If nothing is lost, delete it. A line under a heading stays only when it adds a condition, consequence, example, or next step.
- **The competitor-name swap test.** Replace the product's name with a competitor's in every heading, description, and tagline. Any line that still reads as true is generic: rewrite it with a fact only this product can claim, or delete it. (Adapted from the specificity test in Impeccable's style guide, Paul Bakaus, Apache-2.0.)
- **Every number, logo, quote, and name is real.** Trace each to the API, the fixture labeled as a fixture, or a source the owner gave you. Remove anything you cannot trace; never replace it with a more plausible invention.
- **One noun and one verb per thing.** The heading, button, and toast for one action use the same words. "Create project" on the button and "New workspace added" in the toast is two names for one thing.
- **Each state says what is true.** A loading capture never shows empty-state copy, an error gives a cause and a fix, and success copy appears only after the write commits. The copy rules live in `$content-hierarchy`.

## Structure

- **Squint test.** Look at the `--squint` capture or blur the screenshot. One region should dominate, and it should be the primary task. If three regions tie, the hierarchy is the defect; take it to `$content-hierarchy`.
- **Cards only for equal items.** Each card set holds peers of one kind that a user compares or opens. A card holding one paragraph, a card inside a card, or three cards for unlike things becomes rows, a list, or plain grouping with space.
- **The order follows the user's decision.** Read the blocks top to bottom. A stock sequence (hero, features, logos, testimonials, call to action) on a product screen is a template, not a plan.
- **One grid.** Run `xray` on a capture and check that blocks share two to four key lines and one content column. A block on its own edge is either aligned or given a reason.

## Treatment

- **Every effect ties to data or the brief.** For each gradient, glow, blur, badge, dot, stripe, and animation, name the fact or brief line it serves. With no answer, remove it.
- **No second-wave default.** After the fixes, check that nothing new arrived: a cream ground with terracotta, near-black with one neon accent, or the gradient moved to a border. The fix uses the project's existing tokens only.
- **Every control is a primitive.** Each button, input, menu, and card comes from `ui/` or answers a recorded gap. Check with `$component-compose`'s audit.
- **Motion has a job.** Each animation responds to a user action or shows a state change, runs once, and stops under reduced motion.

## Before you report

- Every dropped string is classified: removed as which tell, merged into what, moved where, or rewritten as what
- Every `data-ui-allow` and `slop-allow` carries a reason a reviewer would accept, and the count is in the report
- Nothing on the list was fixed by restyling the tell
