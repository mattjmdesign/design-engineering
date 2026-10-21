# Forms

Defaults for layout, controls, validation, and submission. Pair with `states-and-feedback.md` for the states each field and button needs.

## Defaults

- One column. Put the label above the input, with 6 to 8px between them, 16 to 24px between fields, and 32 to 40px between groups. Give each group a heading when a form has more than one concern. A side-by-side label column (160 to 200px) is for dense settings screens only.
- Field width follows the expected value: 10 characters for a postal code, 20 for a name, full width up to 480 to 560px for email or a URL. A full-width input for a two-digit value signals nothing about the data.
- Inputs are 40px tall (32 dense, 48 touch) with 12px of inline padding. On mobile set input text to 16px or larger so the browser does not zoom on focus.
- Helper text sits 4 to 6px under the input in the secondary tier. The error replaces the helper in the same slot, in danger color with an icon and a sentence that says how to fix it. Reserve the slot's height so a message does not push the form down.
- Mark the minority: if most fields are required, tag the optional ones "(optional)". If most are optional, mark the required ones and explain the mark once at the top.
- Buttons match the input height. The primary action says verb and object ("Save changes", "Send invite"), and there is one per form. It aligns to the field column at the start edge (full width on mobile). In a dialog the primary sits at the end edge. Cancel is quiet, and a destructive action is separated from the primary by space or placement.
- Choose the control by the data. Radios for two to five visible exclusive options, checkboxes for independent options, a select from six options up, a segmented control for two to four views of the same data, and a switch only for an immediate on or off effect that needs no Save.
- Set `type`, `inputmode`, `autocomplete`, and `name` on every field. Use `type="text"` with `inputmode="numeric"` for identifiers and codes. A password field has a show toggle and allows paste.
- Validate on blur for format problems and on submit for required fields. Do not mark an empty field invalid while the person is still typing, and clear the error as soon as the value is corrected.
- On a failed submit, keep every value, move focus to the first invalid field, and when two or more fields fail show a summary at the top with links to each field. Associate every message with its field using `aria-describedby`.
- While a submit is pending, disable duplicate submission and keep the button's width and label steady. Swap the leading icon for a spinner instead of replacing the text with "Loading".
- A disabled control comes with a reason nearby. If the person can fix the cause, say how, instead of leaving a dead button.
- Long forms use sections, a sticky action bar, and autosave or a draft indicator. A settings page puts each group's heading in a left column of 240 to 300px and its fields in a right column up to 560px. Add a description under a group heading only when it states a condition, consequence, or example the heading does not; most groups need none.

## Checks

- `rule:control.height` flags inputs, selects, and buttons in one row with different heights.
- `rule:a11y.names` flags controls without an accessible name, including placeholder-only fields.
- `rule:target.size` flags checkboxes, radios, and icon buttons under 24px unless WCAG's spacing exception holds (no other target within a 24px circle on its center), and warns below 44px at 820px or narrower.
- `rule:focus.visible` and `rule:focus.order` Tab through the form and flag invisible rings and an order that differs from the visual order.
- `rule:grid.spacing` catches field gaps off the ladder.

## What Goes Wrong

- A placeholder used as the label, which disappears when typing starts and fails screen readers.
- An input at 36px beside a button at 32px, or a search field and its button misaligned by a few pixels.
- Inline errors that insert a line and push the submit button away mid-click.
- A red border as the only error signal.
- A disabled Save button with no explanation of what is missing.
- Required asterisks on every field with no legend.
- A form that clears itself after a server error.
- A switch used for a value that needs a Save button, so the change looks applied when it is not.
- A validation message that says "Invalid input" instead of what to change.
- Focus that stays on the submit button after a failed submit while the errors sit off screen.
