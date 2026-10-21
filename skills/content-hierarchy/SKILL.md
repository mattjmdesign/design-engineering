---
name: content-hierarchy
description: "Decide a page's content priority and copy: rank its information and actions, write headings and a copy deck where each string has one job, and choose what stays visible or gets deferred on narrow screens. Use when a page has too much competing for attention, the main action gets missed, UI copy (labels, hints, errors, empty states) needs writing, or when planning what a page says before layout or visual design."
---

# Content hierarchy

Help the user make the page's primary decision. Inventory real content before deciding which blocks the page should contain.

## Work

- Name the user, page purpose, and next useful action. For a reading page, comprehension may be the main job rather than conversion.
- Rank essential decision information, supporting explanation, and incidental metadata. Keep consequences near consequential actions.
- Establish a descriptive title, meaningful section headings, and reading order. Do not use visual size to choose semantic heading levels.
- Reduce competing emphasis through grouping, position, and copy before adding borders or cards.
- Write a copy deck before markup: element, slot, job, and string for every piece of text. No job, no string. Button labels are a verb plus object that fits one line at 390px. Follow the [copy rules](references/copy.md).
- Set narrow-screen priority: what stays visible first, what can wait, and how users reach deferred content. Leave the mechanics (stacking, drawers, local scroll) to `$ui-layout`; essential content must remain available.
- For existing pages, identify keep, rewrite, move, and remove decisions with reasons. Do not fabricate proof, customer quotes, or product capabilities to make a layout convincing.

## Deliver

A ranked content outline with sample copy where useful, action labels, responsive order, and one observable acceptance condition. Keep the response proportional to the page.

Follow this shape:

**Page purpose:** ...
**Content inventory:** ...
**Primary decision:** ... **H1:** ...
Primary: ...
Secondary: ...
Tertiary: ...
Actions: Primary / Secondary / Destructive
Copy deck: element | slot | job | string
Responsive priority: ...
Move / defer / remove: ...
Accessibility notes: ...

Example: an invitation screen needs recipient, organization, role, and access consequence before “Send invitation.” A decorative illustration is secondary; the role explanation is not.

Check that the first visible information answers the user's immediate question, that heading/keyboard order matches the visual sequence, and that text expansion does not bury the action. Use `$ui-layout` for region sizing; `$interface-craft` for a rendered check of spacing, type, and controls.

## Worked example

**Page purpose:** Convince a visiting foreman to start a free trial.
**Content inventory:** Weekly board screenshot; three shipped features (crew roles, SMS change alerts, weekly board); pricing page; no customer quotes or usage numbers yet.
**Primary decision:** Start free trial. **H1:** Know where your crew is this week.
Primary: 3-step explanation (schedule → assign → notify); screenshot of the weekly board.
Secondary: the three shipped features in plain language.
Tertiary: pricing link, FAQ link, legal links.
Actions: Primary: Start free trial; Secondary: See pricing; Destructive: none.
Copy deck: the hero subtitle has no job the H1 does not already do, so it gets no string; trial button | label | action | Start free trial.
Responsive priority: H1 → CTA → screenshot → steps → features.
Move / defer / remove: Leave out a testimonial block until a real quote exists; move deep-dives to a features page; remove the second hero CTA.
Accessibility notes: single h1; h2 per section; CTA is a real link.

## Gotchas

- A heading that could sit on any product's page ("Features", "Overview") gives scanners nothing; state the section's point.
- Cards and borders make every block look equally important, which is the problem being fixed.
- A plausible quote, metric, feature, or step sequence makes the page look finished and ships as a false claim; if it is not in the inventory, leave the slot out.
- Choose heading levels from the outline, then style them; visual size and level are separate decisions.
- An action separated from its consequence gets clicked blind; put who is notified or what is deleted next to the control.
- A component's description slot invites a subtitle that restates its heading; leave the slot empty unless the line adds a condition, consequence, example, or next step.

## Boundaries

- Do not use when region sizing, page shells, or responsive mechanics are the question; use `$ui-layout` instead.
- Do not use when whole-site navigation is undecided; use `$site-map` instead.
- Do not use to strip AI tells such as eyebrows, gradient text, or filler subtitles from a built screen; use `$slop-removal` instead.
