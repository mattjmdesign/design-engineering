# Accessibility and Inclusive Interaction

A working guide for building and verifying accessible interfaces. For web work the baseline is WCAG 2.2 Level AA unless the project names another standard. For native software, map the same outcomes to platform semantics, text scaling, accessibility APIs, and input methods.

Keep three kinds of rule apart. **Baseline** rules are the WCAG success criteria. **Safety margins** are stronger defaults, such as 44px touch targets. **Craft heuristics** improve usability and are not conformance requirements. A scan or a short checklist never proves conformance, so report "no issues found in the tested scope" and name the scope.

## Semantics and Structure

- Start with native elements. Buttons act, links navigate, lists are lists, and tables hold tabular data with associated headers. Use ARIA only to complete a pattern native HTML cannot express, and implement every part of a composite pattern or none of it.
- Provide landmarks (header, nav, main, footer), one page title, and a heading order chosen for structure, not for visual size. Name repeated landmarks so they can be told apart.
- Keep DOM order equal to reading and focus order. CSS `order`, grid placement, and absolute positioning move content visually without moving it for keyboard and screen reader users.
- Mark decorative images empty and give informative images a useful alternative. Keep important text out of images.

## Keyboard and Focus

- Every pointer action has a keyboard path. Use no positive `tabindex`. Never remove an outline without a replacement ring that is at least as visible, in every state and on every background.
- Sticky headers, dialogs, and banners must not cover the focused element.
- Composite widgets (tabs, menus, listboxes, trees) follow their expected keys. Move focus only when context changes: opening a modal, or showing an error summary after a failed submit. Return focus to the trigger when a temporary surface closes. Avoid traps, including nested scroll regions and hidden offscreen controls.
- Provide a skip link on content-heavy pages.
- Test the whole primary task from a fresh load with the keyboard alone. A component-by-component spot check is not enough.

## Names, Instructions, and Errors

- Every control has a name that matches its visible label so voice control can target it. Icon-only controls get an accessible name and a visible tooltip.
- Put format requirements before or beside the field. Identify an invalid field in text, associate the message with it, and suggest the fix. Preserve entered values, and summarize errors when several fields fail.
- Do not rely on a placeholder, tooltip, color, position, or icon shape as the only instruction. Do not block paste or password managers.
- For legal, financial, destructive, or stored-data changes, offer review, correction, or reversal.

## Visual Access

- Contrast: 4.5 for normal text and 3 for large text, UI boundaries, icons, and focus rings, measured on the rendered pair in each theme. Muted text counts as text.
- Never use color alone for status, selection, validation, or chart meaning.
- Support 200 percent text resize, reflow at 320 CSS pixels wide without two-dimensional scroll, and the user text-spacing overrides (line height 1.5, paragraph spacing 2 times, letter spacing 0.12em, word spacing 0.16em). Do not set fixed heights on text containers, and do not disable zoom.
- Keep the interface usable in forced-colors mode.

## Pointer and Touch

- Targets are at least 24 by 24 CSS pixels (the WCAG minimum) and 44 by 44 for primary touch controls. The visible glyph may be smaller than the hit area, and the two stay aligned. Native platforms use their own guidance in their own units.
- Offer a single-pointer alternative to dragging, multipoint gestures, and path gestures. Let people cancel an accidental press, and keep unrelated destructive and primary targets apart.
- Content that appears on hover or focus must be dismissible, hoverable, and persistent until the trigger loses attention.

## Motion and Media

- Honor `prefers-reduced-motion`. Reduced does not mean zero: keep the state change as an instant or fading transition.
- Avoid flashing. Let people pause content that moves, scrolls, or updates by itself. Do not autoplay audio. Provide captions or transcripts for spoken media.

## Dynamic Content

- Use a polite live region for non-blocking results, assertive announcements only for urgent conditions, and no announcements for rapid intermediate updates.
- Keep loading, disabled, selected, expanded, sorted, invalid, and current states in sync between the visual UI and the accessibility tree.
- When the focused item disappears after a refresh or removal, move focus to the nearest logical target. Make timeouts discoverable and extendable when work could be lost.

## Verification Order

1. Inspect the accessibility tree for names, roles, and structure.
2. Complete the primary task by keyboard (or by touch plus the platform's traversal method on touch-first native targets).
3. Check focus visibility, order, traps, and overlay behavior.
4. Run the measurement tool and an automated scanner for detectable issues.
5. Verify contrast on rendered pairs in each theme.
6. Test 200 percent text resize and reflow at 320px wide.
7. Test reduced motion and forced colors where relevant.
8. Run one screen-reader flow for any custom or high-impact interaction.
9. Recheck after fixes.

`ui_measure.py` covers `rule:a11y.names`, `rule:a11y.clickable`, `rule:a11y.structure` (one h1, heading order, landmarks, image alternatives), `rule:focus.visible`, `rule:focus.order`, `rule:contrast.text`, `rule:contrast.ui`, and `rule:target.size`, and checks the 320px reflow case when run with `--widths 320`. It cannot judge screen reader output, forced colors, text-spacing overrides, or whether a flow makes sense. Mark those unverified unless a person or another tool checked them, and record the browser, viewport, input method, state, and tool for each check.

Authoritative sources: [WCAG 2.2](https://www.w3.org/TR/WCAG22/), [WAI tutorials](https://www.w3.org/WAI/tutorials/), and the [ARIA Authoring Practices Guide](https://www.w3.org/WAI/ARIA/apg/).
