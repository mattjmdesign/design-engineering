# Copy rules

Write the words before the markup, and give every string one job. These rules cover product UI. A marketing page may set its own register, but the slot rules still hold.

## Write a copy deck first

Before markup, list every string in a table of element, slot, job, and string. The jobs are title, label, hint, value, status, action, error, and empty body. A slot with no job gets no string, so a description slot that a component offers stays empty unless the line passes the first rule below.

```markdown
| Element | Slot | Job | String |
|---|---|---|---|
| Invite dialog | header | title | Invite a teammate |
| Email field | below input | hint | The invite link expires in 7 days |
| Submit button | label | action | Send invite |
| Submit failed | below input | error | That address already has access. Enter another email. |
| Member list, none yet | body | empty body | No teammates yet |
```

## Rules

- A line under a heading adds a condition, consequence, example, or next step, or it goes. A subtitle that restates the heading in other words fails.
- Each fact appears once. A heading, subtitle, hint, and placeholder that repeat one fact make a screen reader read it four times.
- A hint sits beside its control, linked with `aria-describedby`, on the same side for every field in a form. The error replaces the hint in that slot instead of adding a line.
- A button is a verb plus an object in three words or fewer, such as "Send invite". The same action keeps one label across the heading, the button, and the toast.
- An error gives the cause and the fix: "Card declined. Use another card or contact your bank." A bare "Something went wrong" gives neither.
- An empty state names what is missing and offers one action: "No projects yet" with a "Create project" button.
- Success copy matches what happened. "Saved" appears only after the write commits; a draft, a queued send, or a mock says so.
- Case follows the project's convention. Do not mix title case and sentence case on one screen.
- Product UI uses plain verbs: no marketing words (for example "seamless", "powerful", or "unlock"), no "Oops", and no exclamation points.

## Check the deck

- Cover each heading and read the line under it; if it says the same thing, delete the line
- Read the deck top to bottom and strike any string whose fact already appeared
- Count the words on each button; four or more means a shorter verb or object
- After the build, `$slop-removal` checks the rendered page for restated headings, generic actions, and bare empty states

The hint and empty-state rules adapt published guidance from the GOV.UK Design System and Nielsen Norman Group; the wording here is this plugin's own.
