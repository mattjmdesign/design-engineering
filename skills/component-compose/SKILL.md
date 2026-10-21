---
name: component-compose
description: "Build UI from the components a repo already has (shadcn/ui, Base UI, local primitives): inventory them first, climb the reuse ladder (variant, extend ui/, registry, compose, then create with a stated reason), audit the changed files, and report what was reused, extended, composed, or created. Use when building or changing components or screens, when agents hand-roll buttons, cards, or pickers, or when a new component's props and keyboard contract need defining."
---

# Component compose

Build from what the repo has: the inventory is the catalog, and the audit catches what slips through. Creating a component is never blocked; it needs a reason.

## Work

1. **Inventory first.** From the app's root, run this skill's `python3 scripts/ui_inventory.py inventory --brief`. It reads the stack from imports, lists each `ui/` component with its variants and import line, and warns when `components.json` disagrees. If it cannot run, read `components/ui/` exports and variants by hand, and say so.
2. **Climb the ladder** for each part the change needs, and stop at the first rung that fits:
   1. **Reuse** a component with an existing variant or size.
   2. **Extend** the `ui/` component with a variant or slot, so the new look lives in one place.
   3. **Install** from the registry after checking it (`shadcn search` or `view`, then `shadcn add NAME`).
   4. **Compose** a feature component from `ui/` parts, in the feature's folder.
   5. **Create**, stating what was searched, the nearest component, and why it fails. Write its [contract](references/contract.md) and build it from `ui/` parts in the lowest layer that can own it.
3. **Promote the third copy.** When a composition appears a third time, make it a primitive in `ui/` and move the other copies onto it.
4. **Import the wrappers** on the canonical line the inventory prints (`@/components/ui/...`), never `@base-ui/react` or `@radix-ui/*` in feature code.
5. **Audit the changed files:** `git diff --name-only | python3 scripts/ui_inventory.py audit --stdin`, or `audit --files F...`. Fix every P1 (`system.raw_element`, `system.primitive_bypass`, `system.name_shadow`) and each P2, or allow a real gap with `--allow RULE=PATH:reason`. With existing debt, add `--baseline FILE`.
6. **Report.** `python3 scripts/ui_inventory.py summary --base REF` drafts the table from the diff; add each Created reason.

## Deliver

```markdown
## Composition report

**Stack:** [from imports]; components.json [agrees | is stale: ...]
| Part | Rung | Component | Note |
| --- | --- | --- | --- |
| [part] | Reused / Extended / Composed / Created | [name, variant] | [file; installed; or the Created reason] |
**Audit:** P1 [n], P2 [n], P3 [n]; allows: [rule, path, reason]
**Promote next:** [a composition seen three times, or none]
```

## Worked example

```markdown
## Composition report

**Stack:** Base UI and HugeIcons from imports; components.json is stale (says Radix and lucide), so triggers use `render`, not `asChild`
| Part | Rung | Component | Note |
| --- | --- | --- | --- |
| Assignee picker | Reused | `Combobox` | installed |
| Overdue tone | Extended | `Badge` variant `overdue` | `ui/badge.tsx`; replaces call-site color classes |
| Task row | Composed | `TaskRow` from `Item`, `Avatar`, `Badge`, `Combobox` | features/tasks |
| Workload bar | Created | `WorkloadBar` | searched progress, meter, bar; nearest `Progress` shows one value, not one segment per assignee |
**Audit:** P1 0, P2 0, P3 0
**Promote next:** none
```

## Gotchas

- `components.json` is often stale. Read the base library and icon set from imports, or you write `asChild` for a Base UI repo that uses `render`.
- A new look is a variant in `ui/`, not a `className` at the call site; color, padding, or radius classes on a `ui/` component fork it one screen at a time.
- Feature code imports the wrapper, never `@base-ui/react`; a direct import skips the system's styles and focus ring.
- A wrapper that renders a raw `<button>`, or a `<div>` with the card's border and radius, is a copy of the primitive under a new name.
- Duplicates hide under other names (`Pill`, `Tag`, `Chip`); search by behavior and callers, not by name.
- An allow or `eslint-disable` without a reason keeps the defect and hides it from the next agent.
- Sizes, keys, and parent behavior the repo does not show are proposals; label them so.

## Boundaries

- Do not use to define token roles, audit drift across the whole library, or plan a deprecation; use `$design-system` instead.
- Do not use to find missing loading, empty, or error states; use `$state-coverage` instead.
- Do not use to polish spacing, type, or radii on a rendered screen; use `$interface-craft` instead.
- Do not use to choose how data is shown; use `$ui-representation` instead.
- Do not use to produce several options for one component; use `$ui-variants` instead.
