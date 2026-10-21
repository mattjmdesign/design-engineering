# Triage and Critique

Decide how much to change on each surface, then judge the render.

## Preserve the Project's Look

- Keep the existing look, tokens, and patterns, and add no new visual identity: no new type pairing, color field, or effect
- Fix unevenness only: values off the scale, roles that differ between screens, controls and radii that disagree, a shell that moves between routes
- A sound surface stays as it is: back that call with rendered evidence, and never restyle it to enlarge the diff

## Triage Each Surface

Classify each surface, not the whole product.

- **Preserve** when purpose, hierarchy, proportions, and components are sound and the baseline holds
- **Repair** when the direction is sound but a bounded defect hurts it: a control that wraps or clips, a layout shift, a contrast or focus failure, or a shared primitive with a repeatable flaw. Fix the root cause and recheck siblings.
- **Refine** when structure is sound but the baseline is uneven: spacing off the scale, type roles that differ, radii or shells that vary, or poor measure or alignment
- **Recompose** when the relationships fail: the purpose is not legible at a glance, everything weighs the same, the primary task is squeezed beside low-value content, or the responsive model only shrinks a weak desktop layout

Tie Refine and Recompose to comprehension or task flow, and never to boredom. If every primary surface warrants Preserve or Repair, say so instead of calling the work a redesign.

## Critique the Render

Source cannot judge a layout. Use the captures and the contact sheet.

1. **Thumbnail or squint:** where the eye lands, and whether one region dominates for a reason
2. **Actual size:** whether the task can be understood unaided, and whether type, measure, and spacing hold a steady cadence
3. **Comparison:** against the baseline at matching sizes and states, what became clearer, what got worse, and what was already stronger

Revise the highest-leverage residual issue and capture again. Preserve and Repair need no manufactured churn.

At actual size, check optical alignment of icons and text, shared edges across regions, heading breaks and orphans, doubled borders, nested radii, numeric alignment, button width while loading, popup placement near viewport edges, sticky layers at scroll boundaries, image crop, skeleton-to-content shift, one-item and many-item rhythm, and the dark theme.

## Keep Content Honest

- Populated, sparse, empty, and missing-media cases are design inputs. Do not fabricate people, scholarship, testimonials, activity, metrics, or capabilities.
- Do not surface drafts, dormant fields, or personal data because the renderer can reach them
- When optional media is absent, let text reclaim the space. A large generic placeholder advertises the missing content.
