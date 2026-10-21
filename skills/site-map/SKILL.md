---
name: site-map
description: "Plan or revise which pages exist, how navigation is organized, what belongs in the URL, and which areas are public, signed-in, or admin. Use when creating IA for an app, site, dashboard, or prototype, renaming routes, or deciding whether view state belongs in the URL."
---

# Site map

Map where users expect tasks and information to live. Start from vocabulary, frequency, and relationships rather than mirroring database tables.

## Work

- Inspect the existing routes and requirements. List actual URLs separately from framework folders; route groups are organizational, not authorization boundaries.
- Name which routes share a shell (the same rail, header, and content column) and which stand alone, such as sign-in and marketing. `$ui-layout` designs each shell, and `$interface-craft` compares the routes of one group on the rendered page.
- Identify public, authenticated, organization-scoped, and privileged areas. Name where server checks enforce access, including direct requests.
- Choose navigation based on depth and task frequency. Keep labels predictable; do not invent future routes without a current reason.
- Put non-sensitive shareable view state in URL parameters. Keep credentials, personal input, and private drafts out of URLs, which can enter history, logs, and referrers.
- Define deep-link, reload, back/forward, not-found, and expired-session behavior. Validate return destinations before redirecting.
- For a revision, preserve existing URLs where possible; otherwise map redirects and check incoming links, bookmarks, metadata, and search indexing.

## Deliver

A concise route tree plus **user job, access rule, navigation location, URL state, migration/unknown** where needed. Distinguish proposed endpoints from existing ones.

Follow this shape:

Route tree: / → ... (mark proposed vs existing)
Navigation: ...
Shells: ...
Conventions: ...
Access: ...
Migration/unknowns: ...

Example: `/projects?status=active` can preserve a list view; `/projects/:id` still checks that the signed-in user may read that project. Hiding the sidebar link does not protect the record.

Check that the primary task can be found and reopened through a direct URL. Use `$user-flow` for the sequence inside a task and `$content-hierarchy` for one page's content priority.

## Worked example

Route tree:
/ (existing)
├── pricing (existing)
├── docs (existing)
├── login (existing)
├── (dashboard) → /projects, /projects/:id, /team, /settings/billing (proposed)
└── (admin) → /users (proposed)
Navigation: top nav for marketing; sidebar inside dashboard; nested settings nav; breadcrumbs in admin.
Shells: (dashboard) and (admin) share the app shell (sidebar, header, one content column); /, pricing, and docs share the marketing shell; login stands alone.
Conventions: existing lowercase kebab-case kept; collections as routes; shareable view state in search params, e.g. `/projects?status=active&sort=updated`; keep unsaved steps in component state; never encode auth in URLs.
Access: dashboard requires login; admin requires admin role; enforce with server checks on `/projects/:id`, not hidden links.
Behavior: an expired session on `/projects/:id` goes to login and returns only to a validated in-app path; an unknown project ID shows not-found inside the dashboard shell.
Migration/unknowns: preserve existing marketing URLs; confirm "Projects" vs "Jobs" vocabulary before locking URLs; reserve `/reports` and `/api-keys` without building them.

## Gotchas

- Route groups and nested layouts look like security boundaries in the file tree but only organize code.
- URLs land in browser history, server logs, and referrer headers; filters belong there, tokens and draft text do not.
- Navigation copied from database tables uses the system's words; name sections by the tasks people do most.
- A login return URL taken from the query string can send users to another site; accept only in-app paths.
- A renamed route silently breaks bookmarks, shared links, and search results unless the old URL redirects.
- Role and permission names in a plan or on a prototype page read as the real access model; label them draft and unenforced until server checks exist, or reviewers approve the wording instead of the rules.

## Boundaries

- Do not use when the question is one task sequence; use `$user-flow` instead.
- Do not use to write requirement lines for behavior or permissions; use `$ui-representation` instead. Missing requirements are not a blocker: map routes from the brief and mark proposed areas.
- Do not use to design a shell's regions, widths, or grid; use `$ui-layout` instead.
- Do not use for one page's content priority; use `$content-hierarchy` instead.
