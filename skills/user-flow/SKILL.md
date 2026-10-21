---
name: user-flow
description: "Map one task step by step from entry to outcome, including each write's pending, saved, rejected, and unknown outcomes and the branches for errors, cancellation, and interruptions. Use when laying out onboarding, checkout, creation, editing, approval, or account flows, or deciding what happens after each user action."
---

# User flow

Make the path executable, including what happens when the system does not give a clean answer. Use the existing workflow and permissions as evidence.

## Work

- Name the actor, entry point, goal, required information, and success destination.
- Trace the shortest complete path. Name each step's screen and the component that carries its action, using an existing primitive where one fits, then the user action, system response, data owner, and visible feedback.
- Add relevant validation, authorization, timeout, conflict, cancellation, session expiry, and back-navigation branches. Avoid an exhaustive list of unreachable states.
- For writes, distinguish pending, committed, rejected, and outcome-unknown. A lost response may follow a successful write; define reconciliation or server-enforced idempotency before retrying.
- Decide what input survives interruption and for how long. Do not automatically store sensitive drafts in localStorage; match persistence to the data and shared-device risk.
- On a running feature, reproduce the path where possible and mark untested branches. Preserve product intent while correcting friction.

## Deliver

A compact flow diagram or ordered path with a branch table: **trigger → visible state → recovery → data/permission contract**. Include acceptance examples for consequential branches.

Follow this shape:

```markdown
## User flow: [goal]
**User:** ... **Entry:** ... **Success:** ...
Path: [Screen: component] → ... → [Success screen]
Branches: trigger → visible state → recovery → data/permission contract
UI states: ...
Open questions: ...
```

Example: a timed-out project creation first checks the operation's result before offering another create. A disabled button helps prevent repeat clicks but does not guarantee one server write.

Verify entry, success, interruption, and return behavior with the applicable input methods. Use `$state-coverage` for cross-screen coverage and `$component-compose` to build a step from the system's components.

## Worked example

```markdown
## User flow: Create project

**User:** Project manager (authenticated) **Entry:** Dashboard "New project" button **Success:** Project created; user lands on its detail page.
Path: [Dashboard: existing `Button` "New project"] → [Create form: existing `Dialog` and `Form`] → validation pass → existing `POST /api/projects` → [Project detail: existing `Toast` "Project created"]
Branches:
- Validation error → inline field errors; keep all input → correct and resubmit → no write yet.
- API failure (network/500) → error plus retry; preserve form → retry the same operation → check result before re-creating.
- Name exists (409) → field error → rename and resubmit.
- Cancel/back with non-empty fields → confirm discard → discard draft.
- Expired session → login with return URL; restore draft → resume.
UI states: idle; submitting with spinner and disabled button ("Create project" → "Creating…"); error banner.
Data/permission contract: `POST /api/projects` requires auth; validate name and teamId from the session; define idempotency before retry.
V1 scope: defer templates and bulk import.
Open questions: auto-invite team members on create?
```

## Gotchas

- After a timeout the write may have succeeded; a plain retry is how duplicates get created.
- A disabled button stops a second click, not a second request from a retry or another tab.
- Drafts in browser storage survive logout on shared devices; decide per field whether a draft may outlive the session.
- A link in a notification or shared message also reaches people who may not act on it; give that visit its own state instead of a blank page or a leak.
- An exhaustive branch list buries the branches a real user can reach; mark the rest out of scope.
- One generic "Something went wrong" merges pending, saved, rejected, and unknown, which need different next actions.
- Success copy must match what the write did: "Saved" only after the server commits, and "Not saved" or "Not sent" beside a timer, local state, or a mock.

## Boundaries

- Do not use when page or route structure is the question; use `$site-map` instead.
- Do not use to choose or build the components a step uses; use `$component-compose` instead.
- Do not use to audit an existing screen for missing loading, empty, or error states; use `$state-coverage` instead.
- Do not use to implement the flow in code; build it as normal feature work.
