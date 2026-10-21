---
name: state-coverage
description: "Audit an existing screen, component, or spec for the states people hit outside the happy path, and define what each shows and how users recover. Use when a screen shows blanks or endless spinners, loses input on failure, or needs loading, empty, error, permission, expired-session, offline, or retry coverage."
---

# State coverage

Find the interface people meet outside the ideal screenshot, from a spec, a preview, or a live screen. Incomplete work is the input, not a blocker.

## Work

- Identify actual data dependencies, roles, network calls, writes, and navigation paths.
- Separate initial loading from refresh; never-created data from filtered no-results; missing optional data from failed data; rejected writes from unknown outcomes.
- Inspect reachable permission, session, offline, conflict, partial-success, cancellation, and retry states. Mark irrelevant states rather than generating UI for them.
- For each gap, name the trigger, visible explanation, next action, retained input/data, and the layer responsible.
- Make each state renderable: name how to force it (a network rule, `?state=<id>`, or a step) and an `expect` target from its own markup. Sibling skill folders sit next to this one; `python3 $interface-craft/scripts/ui_measure.py states URL --spec states.json` confirms each one rendered.
- Prioritize data loss, blocked primary tasks, misleading success, and access failures before minor polish.
- On live work, reproduce with safe test data, and never exercise consequential production actions just to complete coverage.

## Deliver

Report a compact table: **scenario → observed/planned behavior → recovery → priority → force and expect**. Include copy when ambiguity causes the failure. If fixes were requested, implement the reachable states and replay them.

```markdown
## State coverage

| Scenario | Observed / planned behavior | Recovery | Priority | Force → expect |
|---|---|---|---|---|
|  |  |  | Must / Should / Later |  |

### Critical missing
- [item]

### Copy
- [item]
```

Check that errors stay visible where action is needed, pending work is distinguishable from completion, and input survives safely. Avoid skeletons, illustrations, toasts, or confirmation dialogs without a task reason.

## Worked example

```markdown
## State coverage

| Scenario | Observed / planned behavior | Recovery | Priority | Force → expect |
|---|---|---|---|---|
| Project list first load | Skeleton rows hold layout; no full-screen spinner | None needed | Must | Hang `/api/projects` → `[aria-busy=true]` |
| No projects yet | Empty panel with "Create project" action | Create project | Must | `/api/projects` returns `[]` → `text:No projects yet` |
| Refresh fails after data loaded | Keep stale list with "data from [time]" notice plus inline retry | Retry | Must | `?state=refresh-error` → `text:Data from` |
| Viewer opens admin settings | Settings hidden; direct URL shows explanation and a back action | Back to dashboard | Must | `?state=viewer` → `text:Only admins` |
| Create fails on network | Preserve form input; show inline error with retry | Retry | Must | Fail `POST /api/projects` → `[role=alert]` |
| Delete project | Confirm dialog naming the project before the mutation fires | Cancel / Delete | Must | Click Delete → `[role=alertdialog]` |

### Critical missing
- Delete project has no confirmation; mutation fires immediately

### Copy
- Refresh error: "We couldn't refresh your projects. Data from 09:41." Action: "Try again"
- Empty: "No projects yet. Create your first one."
```

## Gotchas

- Never-created empty and filtered no-results need different copy: one explains how to start, the other how to adjust the filter.
- A timeout does not mean the write failed; confirm the result before offering retry unless the contract makes retry safe.
- Replacing loaded data with a spinner on refresh hides what the user was reading; keep stale data with a retry.
- A loading capture that shows empty-state copy tells people their data is gone; give loading its own markup and `expect` target.
- An empty optional field is not an error; show errors only for failed requests, and say what failed.
- Clearing a form to display an error is the most common way state handling loses data.
- A state you did not render is a guess; each state a reviewer judges needs a way to force it and a capture, or it stays planned.
- Must is for what the code visibly causes, such as lost input or a silent failure; states that depend on auth, roles, or API behavior you could not read stay Should until confirmed.

## Boundaries

- Do not use to choose or build the components that render a state; use `$component-compose` instead.
- Do not use to map the step-by-step path of a task; use `$user-flow` instead. An unmapped flow is not a blocker: trace the path from the running screen.
