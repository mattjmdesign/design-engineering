# State Harness

How to make every state of a screen renderable on demand, so each one can be captured, measured, and reviewed. A state exists only when a capture shows it, so forcing states is part of building and not a test added at the end.

## Pick a Way to Force Each State

| State | Method |
| --- | --- |
| Loading, empty, error, offline, slow, long list from an API | Network forcing in `states.json`. The app needs no change. |
| Hover, focus, active | A `pseudo` entry, which forces the pseudo-class in the browser |
| Selected, expanded, open dialog, wizard step | `steps` in `states.json` that type and click |
| Prototype, static page, or a state with no network path | The in-app contract: `?state=<id>` and `window.__setState(id)` |
| A person flipping through states by hand | The state switcher |

## The `states.json` File

One file per screen, kept in the repo next to the screen so the matrix is reviewed with the code.

```json
{
  "screen": "projects",
  "url": "http://127.0.0.1:5173/projects",
  "ready_when": "[data-ready]",
  "states": {
    "ready":   { "expect": "table tbody tr" },
    "loading": { "net": [{ "match": "/api/projects", "hang": true }], "expect": "[aria-busy=true]" },
    "empty":   { "net": [{ "match": "/api/projects", "json": [] }], "expect": "text:No projects yet" },
    "error":   { "net": [{ "match": "/api/projects", "status": 500 }], "expect": "[role=alert]" },
    "offline": { "offline": true, "expect": "text:offline" },
    "long":    { "net": [{ "match": "/api/projects", "json": ["...60 generated rows..."] }], "expect": "table tbody tr:nth-child(60)" }
  },
  "pseudo": [{ "sel": "button.primary", "states": ["hover", "focus-visible", "active"] }]
}
```

- `expect` is a CSS selector or `text:` match taken from that state's own markup. The state counts as rendered only when that target is visible within `timeout` seconds (3 by default). Otherwise it is reported as unconfirmed and `rule:state.confirmed` fails. Never loosen `expect` to make a state pass.
- `net` rules are checked in order. `match` is a substring, a `*` glob, or a `/regex/`. Actions are `hang` (a loading state), `fail` (a network error), or a response built from `status`, `json`, `body`, and `type`. A spec is plain JSON, so generate it with a short script when a fixture is large.
- `steps` (`click`, `type`, `press`, `wait`) run only when given and use real input events. Writes are always answered locally and never sent.
- Seed first-run flags with a `storage` object in the spec or `--storage KEY=VALUE`.
- Run `python3 scripts/ui_measure.py states URL --spec states.json --sheet --widths 390,1280 --themes light,dark` after the app is running.

## The In-App Contract

For prototypes and any state without a network path, expose three things:

- `?state=<id>` selects the state at load, so `capture URL?state=empty --label empty` works with no spec
- `window.__setState(id)` switches without a reload and returns true when applied
- `document.documentElement.dataset.state` holds the active id

A spec reaches these states through the switcher with a step such as `{"click": "[data-state-switcher] [data-state=empty]"}`.

Each state renders through the app's real component with fixture data, never a hard-coded copy of the markup, so the capture measures what ships.

## The State Switcher

A small dev-only control, like a variant toggle, that lists the state ids and calls `__setState`. It lets a person or an agent see every state without editing code.

```js
export function mountStateSwitcher(ids, setState) {
  const bar = document.createElement('div');
  bar.setAttribute('role', 'group');
  bar.setAttribute('aria-label', 'Preview state');
  bar.setAttribute('data-state-switcher', '');
  bar.style.cssText = 'position:fixed;left:12px;bottom:12px;z-index:50;display:flex;gap:4px;padding:4px;background:#111;border-radius:8px';
  for (const id of ids) {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = id; b.dataset.state = id; b.setAttribute('aria-pressed', 'false');
    b.style.cssText = 'font:12px/16px system-ui;padding:4px 8px;color:#fff;background:transparent;border:1px solid #444;border-radius:4px';
    b.onclick = () => { setState(id); bar.querySelectorAll('button').forEach(x => x.setAttribute('aria-pressed', String(x === b))); };
    bar.append(b);
  }
  document.body.append(bar);
}
```

- Mount it only in development or with `?states=1`, never in a production build.
- Keep `data-state-switcher` on its root. The measurement tool skips that subtree, which is why its inline styles may ignore the project's tokens.
- Wire `__setState` and the `state` attribute even when no one will ever look at the switcher, so a script or a person in devtools can flip states.

## The Contact Sheet

`--sheet` renders every captured state, width, and theme in one image. Review it at thumbnail size first, then cell by cell: a blank region, a raw error, a stack trace, or chrome that moves between states is a defect. Use it as the main input for critique, and cite its evidence id when reporting.

## Fixtures

- Type fixtures from the app's real models and keep them as local files. Use no customer data and no secrets.
- Cover the stress cases: a 60-character name, a three-line description, 200 rows, a count of 1,284, a missing optional field, and a failure body shaped like the real API error.
- Fixtures show how a state looks. They do not claim anything about the product, so never present fixture values as real metrics, names, or prices.

## Run Without a Backend

When the app cannot reach its API, or real services and data are unsafe to touch, serve fixtures from a local mock. Work in a throwaway copy of the repository, never in the real one.

1. List the API surface: the client's request helper, the base URL variable, the dev server proxy, and every endpoint the screens call.
2. Type the fixtures from the app's own types or schemas, generate them with a deterministic script, and include the stress cases above. Use invented people and no customer data.
3. Serve them from a small dependency-free mock on 127.0.0.1 only. Reads return fixtures. Writes get a plausible status and the response shape the UI expects, then are logged, never stored or forwarded. Unknown paths return 404 and are logged, which lists the endpoints still missing.
4. Shift every timestamp to the current time on each response, so relative times and ordering stay stable between runs.

- Proxy the API path through the dev server so the app stays same-origin
- Pass authentication with the app's dev-token variable, or add one that works only in development. The mock accepts any token and returns 401 for `expired` and `invalid`, which gives a cheap signed-out state.
- Start the dev server with only the variables it needs, on a free port, and never stop a process you did not start. Use the dev server, not a production build, which may load analytics or error tracking.
- Seed first-run flags with `--storage KEY=VALUE`, and hide dev overlays with `capture.ignore_selector` before judging a screen
- Copying `node_modules` between folders can break tool shims that embed absolute paths, so reinstall from the lockfile

The mock shows that the UI renders these shapes, not that the real server returns them. It says nothing about permissions, validation errors, pagination, concurrency, performance, or third-party integrations, and writes revert on the next fetch. Report every result as simulated.

## What Goes Wrong

- A forced loading state that never rendered, because the request failed fast and the app showed its empty state, so the screenshot proves nothing.
- A switcher that edits the DOM directly. The capture then measures markup that production never produces.
- States named after the code path ("state2") rather than the situation, so nobody can tell what is missing.
- A switcher left in a production bundle.
- Capturing only `ready` at 1280 and calling the screen verified.
