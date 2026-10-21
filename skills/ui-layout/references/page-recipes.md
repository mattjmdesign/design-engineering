# Page recipes

Starting structures for the shell and for screens outside the content-region templates, with the measurements that make them feel settled. Each is a default for density and order, not a visual identity, and the project's existing shell and tokens win over any number here. Dashboards, lists, list and detail, settings, record detail, forms, and first-run pages are in [content-regions](content-regions.md).

## App shell

- Sidebar 240 to 280px (64 to 72 collapsed), top bar 56 to 64px or none when the sidebar carries everything. The shell is fixed and exactly one region scrolls.
- Nav items are 36 to 40px tall with 20px icons. The current item has a tint, weight 600, and `aria-current`. Section labels are 12px in the muted tier.
- One shell component renders the rail, header, and footer on every route, so positions and sizes match and only the content region changes. Sign-in and marketing pages may use their own shell. For routes that share a shell, run `python3 $interface-craft/scripts/ui_measure.py routes URL [URL ...] [--widths 1280,390]`; `rule:shell.consistency` flags a rail, header, footer, content column, or page title that differs.

## Page header

- Breadcrumb (12 to 13px, optional), title (24 to 28px, weight 600), and actions at the end edge. Leave 24 to 32px before the content.
- Add a description line in the secondary tier only when it says something the title cannot: a condition, a consequence, or the next step. A description under every title restates it and pushes the content down.

## Content region

- The content region is the scroll region minus the shell, with equal left and right gutters of 16, 24, or 32 by width
- Cap the column by page type: data 1280 to 1440, detail and forms 720 to 960, settings fields 560 to 720, prose 65ch
- On wide screens the column stays beside the rail, or centered on a page without one, and never stretches edge to edge
- Tables and charts may fill the column but stop at the cap
- Only backgrounds, media, and bands run full bleed, and their inner container follows the column
- Sections share one column, so edges stay put
- Headings that are link targets under a sticky header get `scroll-margin-top`
- `rule:layout.content` flags content hugging the region edge, uneven gutters, no width cap on wide screens, and sections whose columns differ

## Sign-in

- One centered column, 360 to 420px wide: logo, 24px title, fields at 40px, a full-width primary, then secondary links. Single sign-on buttons match the input height.
- Errors appear inline above the fields. Keep the page free of marketing panels unless the brand calls for a split layout.

## Landing and pricing

- Nav 64 to 72px. Hero: headline 40 to 64px, a subhead of 18 to 20px at 60 characters or fewer per line, one primary and one secondary action, and real product proof beside or below.
- Section padding 64 to 96px vertical (48 on mobile) inside a 1120 to 1200px container. Change the layout when the content type changes. Four identical icon-and-heading cards are a template, not a design.
- Pricing shows three tiers or fewer with one emphasized. Feature rows share heights across tiers (use `subgrid` or fixed row heights) so they align. Prices come from the brief and nowhere else.

## Dialog and drawer

- Dialog 400 to 560px (480 default), 24px padding, a 18 to 20px title, a body that scrolls on its own, and a footer with actions at the end edge and the primary last. Keep it under 85vh with a visible close control.
- Drawer 480 to 640px. Use a dialog for a decision, a drawer for work in context, and a page for anything long, deep-linkable, or compared with what is behind it.

## Multi-step flow

- Five steps or fewer, with a progress indicator that names the steps. Each step asks one thing, offers Back, and keeps entered values. End with a summary before the irreversible action.

## What goes wrong

- The shell reimplemented per page, so the sidebar offset and gutters differ from screen to screen
- Two regions scrolling inside a scrolling page
- Sections on one page with different left edges because each set its own container
- A pricing grid whose feature rows misalign because each card sizes its own content
- A hero with a gradient word, two buttons, a glow, and a logo row that were not in the content
