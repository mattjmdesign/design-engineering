---
name: product-intent
description: "Clarify what to build and for whom before design or code, or reassess a shipped feature against its original promise. Use when a rough idea, client request, or vibe-coding prompt needs its user, outcome, and smallest useful scope settled, or when a feature goes unused and needs keep, fix, or cut decisions."
---

# Product intent

Turn the request into a decision the product must support. Use the user's language and existing brief before inventing a new framing.

## Work

- Identify the user, situation, current workaround, desired outcome, and business constraint. Separate evidence from assumptions.
- Trace one complete job from entry to a useful result. A list of screens is not a workflow.
- Set the smallest useful scope, explicit exclusions, and the question the next artifact should answer. Preserve features the user explicitly requested; explain tradeoffs before proposing alternatives.
- For existing work, compare the actual journey to the original promise. Keep what serves it, identify drift, and recommend a bounded correction.
- Ask only questions whose answers change scope or consequence. Continue with labeled, reversible assumptions where possible. Do not require an interview before a well-specified implementation task.

## Deliver

A compact brief: **user and situation → outcome → first workflow → scope → constraints → uncertainty → next action**. Use a short paragraph or table; omit empty categories. Include an observable acceptance condition without inventing research or targets.

Follow this shape:

**Concept:** ...
**User/situation:** ...
**Outcome:** ...
**First workflow:** ...
**Scope in / out:** ...
**Constraints/risks:** ...
**Assumptions / open questions:** ...
**Acceptance check:** ...
**Next artifact:** ...

Example: a crew member needs tomorrow's site assignment on a phone. Test whether they can find it and recognize a last-minute change. Offline access is a requirement to resolve, not a feature to assume.

Check that someone could use the brief to reject an attractive but irrelevant screen. To turn it into a UI pattern and its components, or into requirement lines on request, use `$ui-representation`; for the task path use `$user-flow`. These are optional next steps, not mandatory prerequisites.

## Worked example

**Concept:** Shared calendar for small construction crews so everyone knows which site they are on.
**User/situation:** Foremen assign crews in a WhatsApp group; crew members check phones on site, often on low-end Android without reliable connectivity.
**Outcome:** Everyone knows where they work tomorrow without asking anyone.
**First workflow:** Foreman creates the week's assignments → crew member opens today's view in under 10 seconds.
**Scope in:** Weekly assignment view per user and per site; foreman-only editing; notification on change.
**Scope out:** Time tracking, payroll, GPS check-in.
**Constraints/risks:** Low-end devices; intermittent connectivity; crews under 15 people.
**Assumptions / open questions:** Assume one foreman per crew; resolve whether assignments need recurring patterns or integration with an existing tool.
**Acceptance check:** A crew member finds tomorrow's site and recognizes a last-minute change; offline access is a resolved requirement, not an assumed feature.
**Next artifact:** `$ui-representation` for the crew member's weekly view.

## Gotchas

- A list of screens reads like progress but hides the missing job: without an entry, an action, and an observable result, it is still a screen list.
- Swapping a requested feature for a "better" one overrides the user's decision unless the tradeoff is explained.
- An invented target ("reduce churn by 20%") becomes a commitment the client quotes back.
- A client who names a solution ("we need an app") often has a narrower job; the current workaround shows what the solution must beat.
- Low usage has different causes (hard to find, wrong fit, broken); find out which before recommending a cut.
- A keep, fix, or cut call that defers the final decision needs a named measure, a checkpoint, and a threshold labeled as judgment; otherwise any later number can justify either outcome.

## Boundaries

- Do not use when the goal is settled and the question is which UI pattern and components present it, or an agreed brief needs requirement lines; use `$ui-representation` instead.
- Do not use when the goal is settled and the question is how the interface looks or feels; use `$interface-craft` instead.
- Do not use to compare design options for one settled piece; use `$ui-variants` instead.
