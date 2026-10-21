---
name: ui-variants
description: "Build three real, measured options for one component or content region behind an overlay picker in the real page, then promote the chosen one and remove the harness. Use when the user invokes it or plainly asks for options, variants, alternatives, or directions to compare for a component or region. Do not use to fix, polish, or audit UI, or to define a component's prop variants."
---

# UI variants

Three options for one piece, each a different position on one axis, composed from the project's components and held to one measured floor. Sibling skill folders sit next to this skill's folder; run their scripts by that path, such as `$interface-craft/scripts/ui_measure.py`, and never import them.

## Work

First state the piece, axis, count, and cost. A fan-out costs about three full build runs, so ask before going past one piece, three variants, or one round.

1. **Scope one piece:** a component in its real parent, or one content region inside the existing shell, such as a metric card rather than the dashboard
2. **Ground.** Run `python3 $component-compose/scripts/ui_inventory.py inventory --brief`, `python3 $interface-craft/scripts/ui_measure.py init URL`, and a baseline `capture`. Take the template alternatives from `$ui-layout`. Write one fixture (default, empty, and long data) and one copy deck per `$content-hierarchy`.
3. **Name the axis.** Write one axis and three positions from [axes](references/axes.md) into `variants.json` before any variant code. Theses must not rhyme. Type and color are never axes.
4. **Write the harness** in `src/__variants__/<piece>/` from [picker](references/picker.md). Only the host imports variants.
5. **Build.**
   - **Page region:** round one is three skeleton structures you build yourself, read with `ui_measure.py xray`. Once the owner picks one, round two fans out on another axis.
   - **Component:** delegate three independent worker runs when the host supports delegation, one filled [worker brief](references/worker-brief.md) per position. Each worker owns one file, edits no `ui/`, token, or harness file, composes from the inventory, loops to zero FAIL and zero P1, and returns `variant.json`. If the host cannot delegate, build the positions in sequence and pass earlier variants' measured facts as must-differ constraints.
   - A failed variant gets one retry, then is dropped and named.
6. **Gate.** Run `ui_measure.py variants URL --param __variant --ids a,b,c --spec states.json` at 390, 768, and 1280 in each theme (until it ships, `capture URL?__variant=<id> --label <id>`). The floor: zero FAIL, WARNs fixed or justified, `$slop-removal` checks clear, default, empty, and long confirmed (`rule:state.confirmed`), zero P1 from `ui_inventory.py audit`, and `variant.distinct`.
7. **Present.** Say where the picker runs and how to flip it, then give the table below with no favorite.
8. **Promote** with [promote](references/promote.md): rebuild the winner in place on real data, move proposed variants into `ui/`, run `$design-system` Adopt for new values, `compare`, delete the harness, and prove zero leftovers

## How this improves on variant

Adapted from `variant` by Jakub Krehel (MIT), adding evidence for every claim, a check that rejects tints, composition from the inventory, empty and long states, independent workers, region skeletons, an overlay that leaves the layout as shipped, and a promotion that proves its cleanup.

## Deliver

```markdown
Picker: <URL>?__variant=<id>. Keys 1 to 3 switch and H hides. Axis: <axis>.

| Variant | Position | Right when | Costs | FAIL / WARN | Reused / created | Distinct | Evidence |
|---|---|---|---|---|---|---|---|
| <name> | <position> | <situation> | <price> | 0 / <n> | <n> / <n> | yes | [E##@390:default#light] |

Dropped: <variant and reason, or none>. Not verified: <what, why>.
```

## Worked example

**Piece:** a table's filter bar, Composition axis: Inline (`Select` and `Input` in a row), Sheet (a button opening a `Sheet`), and Rail (`Checkbox` groups in a column).
**Gate:** all at 0 FAIL and 0 P1 with three states confirmed, distinct at 1280 [E14@1280:default#light]; Rail created a count chip for a recorded gap.
**Costs:** Sheet hides active filters; Rail takes 240px of width.

## Gotchas

- Matching boxes and text with new paint is one option, not two; move a position or cut it
- Workers that read each other's files converge; briefs name the other positions and facts, never paths
- A worker has no skill loaded; write absolute script paths into every brief
- A worker that edits `ui/` or tokens changes the other two options; it lists the change under `proposed`
- Copying the winning file keeps fixture data and harness imports; rebuild on real data, then search for `__variant`
- Asked for a favorite, answer from how often the piece is seen and the product's density

## Boundaries

- Do not use to fix, polish, or audit existing UI; use `$interface-craft` instead
- Do not use to remove AI tells; use `$slop-removal` instead
- Do not use for a component's props or prop variants; use `$component-compose` instead
- Do not use to plan a shell or regions; use `$ui-layout` instead
