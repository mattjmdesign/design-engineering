# Component contract

Write a contract on the Create rung, or when revising an existing component's API. It records the decisions the component owns, so the next agent extends it instead of copying it. Scale it to the component: a simple badge needs a few lines, not an RFC.

## Decide

- **Purpose, consumers, and owner.** Name the layer: a product-agnostic primitive in `ui/`, a domain component near its feature, or a layout. Domain states belong near the feature even when several screens reuse them.
- **Why new.** What was searched (names and behaviors), the nearest existing component, and why it fails. Record this before the API.
- **Built from.** The `ui/` parts it composes. A created component still imports `Button`, `Popover`, and the rest from `ui/` rather than rendering raw elements.
- **API.** Inputs, events, controlled and uncontrolled behavior, defaults, and composition slots. Prefer one `variant` value over booleans that contradict each other.
- **States.** Every reachable data and interaction state. Keep server authorization and persistence out of the component, and name the server check that enforces an action.
- **Accessibility.** Native semantics, accessible name, keyboard model, focus entry and return, and feedback. Follow the platform pattern for that control; not every control uses the same keys. Base UI and Radix parts already implement most patterns, so compose them rather than rewriting the keyboard model.
- **Resilience.** Content limits, wrapping, responsive behavior, supported themes, and token use. A control or badge gets a label contract: maximum label length, one line with no wrapping, one fixed height per size, and a radius from the scale.
- **Revision.** For an existing component, list the callers that change and how old props map to new ones before changing the API.

## Template

```markdown
## Component contract: [Name]

**Purpose:**
**Layer:** primitive (ui/) | feature | layout
**Why new:** searched [...]; nearest [...]; fails because [...]
**Built from:** [ui/ parts]
**Used in:**
**Variants:**
**States:**
**Props:** inputs, events, defaults
**Accessibility:** semantics, keyboard, focus
**Tokens and responsive:**
**Acceptance:**
```

## Example

```markdown
## Component contract: ProjectStatus

**Purpose:** Show a project's lifecycle status next to its name in lists and headers.
**Layer:** feature component in the projects feature
**Why new:** searched badge, status, pill, tag; nearest `Badge`, which stays product-agnostic, so the status mapping lives here and composes it
**Built from:** `Badge`
**Used in:** ProjectList, ProjectDetailHeader
**Variants:** `status`: active, paused, completed, archived. Feature code maps each to a label and an existing Badge tone; an unknown server value renders "Unknown status" in the neutral tone.
**States:** non-interactive, so no hover, focus, or pressed state
**Props:** `status` (required). No children or style overrides, so the label cannot drift from the status.
**Accessibility:** text carries the meaning and color only reinforces it; the label never truncates
**Tokens and responsive:** uses the existing status tones, each contrast-checked in every shipped theme
**Acceptance:** every status and an unknown value render the right label and tone; a long translated label stays on one line without clipping
```

## What goes wrong

- Two booleans that cannot both be true (`isPrimary`, `isDanger`) are one `variant` value in disguise
- A truncated status or error label hides the one word the user needed; shorten the copy until it fits
- A primitive that knows domain values (`active`, `archived`) gets copied per feature; it should take a tone and a label
- Hiding a control inside the component does not stop the action; name the server check that does
- Parent behavior, sizes, key steps, and sample names the repo does not show are proposals; label them as requirements to confirm, not as facts
