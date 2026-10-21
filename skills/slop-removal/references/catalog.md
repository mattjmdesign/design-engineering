# Slop catalog

One entry per known tell: what it looks like, why it hurts, how it is detected, and the fix. Rendered rules (`slop.*`) run in every capture of `$interface-craft/scripts/ui_measure.py`, and their thresholds live in that skill's `scripts/defaults.json`. Source rules (`src.*`) run in this skill's `scripts/slop_scan.py`. "Judgment" means no detector can see it, so it is on the [judgment checklist](judgment.md).

Every fix removes, merges, swaps in a primitive, or rewrites. None restyles the tell or adds a new treatment. The catalog grows; see [adding a tell](#adding-a-tell).

## Copy

### Eyebrow over a heading
- **Looks like:** a short, small, uppercase, letter-spaced label right above a heading, such as "OVERVIEW" or "WHY TEAMS SWITCH"
- **Why it hurts:** one more line to read before the heading, carrying a category the heading already implies. Down a page it becomes a rhythm of labels with no information.
- **Detected by:** `rule:slop.eyebrow` (by default 34 characters or fewer, 14px or smaller, tracked 0.06em or more, within 32px above an h1 to h4 of 20px or more) and `src.eyebrow_class`
- **Fix:** delete it. If it holds a fact the page states nowhere else, such as a plan tier, fold the fact into the heading, or keep the label with an allow and that reason.

### Subtitle that restates its heading
- **Looks like:** "Projects" over "Manage all of your projects in one place"
- **Why it hurts:** the reader and the screen reader get one fact twice, and the content moves down a line
- **Detected by:** `rule:slop.restates_heading` (INFO: word overlap nominates, you judge) and `src.description_everywhere` when a description sits under nearly every title
- **Fix:** delete the line. Keep it only when it adds a condition, consequence, example, or next step.

### One fact in several slots
- **Looks like:** the heading, subtitle, hint, placeholder, and tooltip all say "Enter your email address"
- **Why it hurts:** a screen reader reads it four times, and the one slot that should carry a hint carries nothing new
- **Detected by:** `rule:slop.repeated_text` (one string of three or more words shown three or more times)
- **Fix:** say it once, in the slot whose job it is, per the copy rules in `$content-hierarchy`

### Marketing words in product UI
- **Looks like:** "Unlock powerful insights", "Seamlessly manage your team", "Supercharge your workflow"
- **Why it hurts:** a tool reads like an ad, and the words carry no fact the user can act on
- **Detected by:** `src.marketing_words`, and judgment for copy that arrives from data or a CMS
- **Fix:** a plain verb and the actual object, such as "See late projects"

### Generic action label
- **Looks like:** "Learn more", "Get started", "Submit", "Click here", "OK"
- **Why it hurts:** a list of identical links tells a screen reader user nothing, and the action's object is missing for everyone
- **Detected by:** `rule:slop.generic_action`
- **Fix:** a verb and an object in three words or fewer, such as "Create project" or "Read the guide"

### Bare or cheerful empty state
- **Looks like:** "Nothing here yet!", "No data", or an illustration with no way forward
- **Why it hurts:** the user learns nothing about what is missing or what to do next
- **Detected by:** `rule:slop.empty_state` (a short message with no action nearby)
- **Fix:** name what is missing and give one next action, such as "No projects yet" with "Create project"

### Emoji as an icon or label
- **Looks like:** "🚀 Launch", "✨ New", emoji bullets in a feature list
- **Why it hurts:** emoji render differently on every platform, are read aloud by name, and sit outside the icon set
- **Detected by:** `src.emoji_label`
- **Fix:** an icon from the project's set, or nothing

### Em dashes and welded arrows
- **Looks like:** two clauses joined by an em dash, or an arrow glyph welded to a label, such as "Continue →"
- **Why it hurts:** a common generated cadence that hides two sentences and weakens direct prose
- **Detected by:** `rule:slop.dash` (INFO)
- **Fix:** a period, comma, or colon; an arrow becomes the system's icon component or goes

### Invented proof
- **Looks like:** "Trusted by 10,000+ teams", "99.9% uptime", a logo wall, a quote from "Jane Doe, CEO"
- **Why it hurts:** it ships as a false claim, and a reviewer cannot tell it from a real one
- **Detected by:** judgment. A detector can match the shape of a number, never its truth.
- **Fix:** remove it, or source it from real data and say where it comes from

### Taglines and generated cadence
- **Looks like:** "Not just a tool. A partner." or "Built for teams who ship." under a product heading
- **Why it hurts:** it could sit on any product's page, so it says nothing about this one
- **Detected by:** judgment, with the competitor-name swap test
- **Fix:** delete it, or replace it with a specific fact about this product

### Apologetic or theatrical error
- **Looks like:** "Oops! Something went wrong." or "Uh oh, we hit a snag"
- **Why it hurts:** no cause and no fix, in a tone that makes light of a failure
- **Detected by:** judgment
- **Fix:** the cause and the fix, per the copy rules in `$content-hierarchy`

## Layout

### No underlying grid
- **Looks like:** edges that almost line up, gaps off the spacing scale, and each section at its own width
- **Why it hurts:** the eye finds no line to follow, so the page reads as assembled rather than designed
- **Detected by:** `rule:grid.spacing`, `rule:align.near_miss`, and `rule:layout.content`; `ui_measure.py xray RUN/E##` draws the key lines and near-miss deltas
- **Fix:** two to four key lines and one content container, planned with `$ui-layout`. Never nudge a margin to clear a finding.

### Cards inside cards
- **Looks like:** a bordered, rounded box holding another bordered, rounded box with its own padding
- **Why it hurts:** each level costs padding and an edge, and the hierarchy reads as boxes rather than content
- **Detected by:** `rule:slop.nested_cards`. A stock shadcn Card with its header, content, and footer passes.
- **Fix:** one level; group the inner content with space or a divider

### Card soup
- **Looks like:** a grid of cards, each an icon tile, a heading, and two lines, for things that are not equal peers
- **Why it hurts:** cards make every block look equally important, which hides the one that matters
- **Detected by:** judgment ("cards only for equal items")
- **Fix:** rows, a list, or plain grouping with space. Keep cards for peers a user compares or opens.

### Equal weight and stock page order
- **Looks like:** hero, three features, logos, testimonials, and a call to action on a product screen, every block the same size
- **Why it hurts:** the user's primary task competes with filler, and the squint test shows no focus
- **Detected by:** judgment (squint test)
- **Fix:** lead with the primary task and order by the user's decision, with `$content-hierarchy`

### Everything centered
- **Looks like:** headings, paragraphs, lists, and forms all centered
- **Why it hurts:** every line starts at a new x position, so reading and scanning both slow down
- **Detected by:** judgment
- **Fix:** left-align reading content and data. Center only a short, single-purpose moment.

### Hero numbers with no comparison
- **Looks like:** a row of large figures with no period, trend, or target
- **Why it hurts:** a number with no reference point supports no decision
- **Detected by:** judgment
- **Fix:** add the comparison that makes the number meaningful, or demote it to a label and value

## Visual

### Gradient text
- **Looks like:** a heading filled with a gradient through `background-clip: text`
- **Why it hurts:** contrast cannot be measured and changes along the line, and it reads as decoration
- **Detected by:** `rule:slop.gradient_text` and `src.gradient_text`
- **Fix:** the solid text token for that role

### Glows and colored shadows
- **Looks like:** a saturated, blurred shadow around a card or button
- **Why it hurts:** it implies elevation or focus that the element does not have, and it competes with the real focus ring
- **Detected by:** `rule:slop.glow` (by default a blur of 12px or more, alpha 0.15 or more, saturation 0.4 or more) and `src.glow_shadow`
- **Fix:** the system's elevation, or none

### Colored side stripe
- **Looks like:** a thick accent border on one side of a card or callout
- **Why it hurts:** color alone carries a status that is never named, or carries nothing at all
- **Detected by:** `rule:slop.side_stripe`
- **Fix:** remove it. A real status gets text or an icon inside the content.

### Decorative gradient fills and backgrounds
- **Looks like:** a purple-to-blue wash behind a hero, mesh blobs, grid or dot backgrounds
- **Why it hurts:** the surface draws more attention than the content and lowers the contrast of anything on it
- **Detected by:** judgment. Only gradient text has a rule today.
- **Fix:** the system's surface tokens

### Glass by reflex
- **Looks like:** a blurred, translucent panel sitting over a plain background
- **Why it hurts:** the effect has nothing to show through, and its contrast varies with what sits behind it
- **Detected by:** judgment
- **Fix:** a solid surface

### Second-wave default
- **Looks like:** one tell swapped for the next: a gradient replaced by cream with terracotta, near-black with one neon accent, or the gradient moved onto a border
- **Why it hurts:** the screen still reads as generated, now with a new identity the project never chose
- **Detected by:** judgment
- **Fix:** the project's own tokens, and nothing new

## Components

### Badges everywhere
- **Looks like:** three or more small pills in one card or row, such as "New", "Popular", "AI", and "Beta"
- **Why it hurts:** when everything is flagged, nothing is, and the real status badge is lost
- **Detected by:** `rule:slop.badge_density`
- **Fix:** a badge only for real state that changes; plain text or nothing for the rest

### A description on every card and dialog
- **Looks like:** every component's description slot filled with a line that restates its title
- **Why it hurts:** the same as a restating subtitle, multiplied by every card on the screen
- **Detected by:** `src.description_everywhere` and `rule:slop.restates_heading`
- **Fix:** leave the slot empty unless the line adds a fact

### One-off controls beside the primitives
- **Looks like:** a hand-rolled button, card, or picker next to the same thing in `ui/`
- **Why it hurts:** it drifts from the system on focus, states, and density, and the next screen copies it
- **Detected by:** `$component-compose`'s `ui_inventory.py audit` (`system.raw_element`, `system.primitive_bypass`, `system.skin_copy`) and `rule:component.conformance` on the render
- **Fix:** swap in the primitive or one of its variants

### Icon tile above every heading
- **Looks like:** a rounded, tinted square with an icon over each card or section heading
- **Why it hurts:** it adds a decorative row to every block and pushes the heading down
- **Detected by:** judgment
- **Fix:** remove it, or keep one inline icon where it speeds recognition

### Fake live indicator
- **Looks like:** a pulsing dot or a "Live" chip on data that is not live
- **Why it hurts:** a false claim about freshness, and constant motion in the corner of the eye
- **Detected by:** `src.infinite_pulse` and judgment
- **Fix:** remove it. Live data gets a static label with the time it last updated.

## Motion

### `transition: all`
- **Looks like:** `transition: all` in CSS or `transition-all` in Tailwind
- **Why it hurts:** layout and color properties animate by accident, which costs frames and moves things that should not move
- **Detected by:** `src.transition_all`
- **Fix:** name the properties that change

### Bounce and overshoot easing
- **Looks like:** `animate-bounce`, cubic-bezier curves past 1, springs with a high bounce
- **Why it hurts:** product UI that wobbles reads as a toy and slows repeated actions
- **Detected by:** `src.bounce_easing`
- **Fix:** the system's easing token

### Endless decorative animation
- **Looks like:** pulse, ping, float, or a marquee that never stops
- **Why it hurts:** constant motion pulls attention from the task and ignores reduced-motion settings
- **Detected by:** `src.infinite_pulse`
- **Fix:** stop it; give a state that matters a static cue

### The same entrance on every section
- **Looks like:** every block fades up as it scrolls into view
- **Why it hurts:** content arrives late on every scroll, and the effect stops meaning anything by the second section
- **Detected by:** `src.entrance_everywhere`
- **Fix:** one authored moment or none, and nothing under reduced motion

### Hover lift on static cards
- **Looks like:** a card that rises and gains shadow on hover but does nothing on click
- **Why it hurts:** it promises an action that does not exist
- **Detected by:** judgment
- **Fix:** no hover change on surfaces that are not interactive

## Rules not imported

Impeccable's detector flags overused fonts (Inter, Roboto, Geist), cream and purple palettes, italic serif heroes, and spacing it calls monotonous. This catalog leaves them out on purpose:

- They are taste, not tells. In shadcn/ui and Base UI repos, Inter and Geist are defaults the system chose, and neutral palettes are its tokens.
- A repo that uses one 4px step consistently is doing the right thing, and a monotony rule would flag the clean baseline this plugin builds toward
- Type, color, and the spacing scale are system decisions owned by `$design-system`. Slop removal keeps the project's look.

Several tells and the eyebrow definition are adapted from Impeccable's catalog (Paul Bakaus, Apache-2.0). The wording and the detectors here are this plugin's own.

## Adding a tell

1. **Confirm it is a tell.** It shows up on generated screens from more than one source, it costs the reader something (a fact, time, trust, or contrast), and its fix removes or merges. A preference about fonts, palettes, or radii is not a tell; take it to `$design-system`.
2. **Write the entry** in the right group with the four parts above. The fix deletes, merges, swaps in a primitive, or rewrites.
3. **Pick the detector and its id.** A tell that needs layout, computed style, or text in context is a rendered rule, `slop.<name>`, in interface-craft's engine with its thresholds in `defaults.json`. A class, word, or easing in code is a source rule, `src.<name>`, in this skill's `scripts/slop_scan.py`. A tell neither can see is "judgment" and gets a line in [judgment](judgment.md).
4. **Add fixtures:** one page or file that must fire and one near miss that must not, including a stock shadcn component. The selftests run them: `ui_measure.py selftest` and `slop_scan.py selftest`.
5. **Check precision before WARN.** A new rule ships at INFO. It moves to WARN only after at least 30 labeled hits on real screens from three or more repos show 0.9 precision or better. Copy rules never FAIL.
6. **Keep the guards.** Navigation, tables, breadcrumbs, native form labels, `aria-hidden` content, code, dates, and "Step N" are never read as copy.
7. **Land it together.** The id, the code, the fixtures, and this entry go in one change, and the catalog cites the id exactly as the script defines it.
