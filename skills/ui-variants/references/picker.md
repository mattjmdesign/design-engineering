# Picker and harness

The picker is the overlay toggle that flips variants in the real page. It sits on top of the thing being judged, so build it from this spec and leave its look alone. The harness around it (host, fixture, states) is what lets an agent, a script, and a person reach every variant and state the same way.

## Contract

- `?__variant=<id>` selects a variant at load, so every variant is a link
- `window.__setVariant(id)` switches without a reload and returns true when the id exists
- `document.documentElement.dataset.variant` holds the active id
- States use the same shape: `?__state=<id>`, `window.__setState(id)`, and `dataset.state`. The double underscore keeps the param clear of any `state` param the app already reads.
- The picker root carries `data-state-switcher`. The engine skips that subtree when it measures, and a spec reaches a state or variant with a step such as `{"click": "[data-state-switcher] [data-state=empty]"}`.
- Keys 1 to 5 select variants in order, and `H` hides and shows the picker. Keys are ignored with a modifier held, inside inputs, textareas, selects, and editable text, and when the page already handled the event.

## Deliberately outside the design system

- One dark neutral surface, the system font stack, and inline styles. No project tokens, classes, or components, so the picker never becomes part of what is judged, and it does not follow the theme.
- No transition. Flipping is the run's highest-frequency action.
- It overlays a corner and never changes the page's layout, so the piece is measured in its real parent at its real width. Move it with `corner` when it covers the piece.
- Native buttons with `aria-pressed`, a labeled group, the browser's own focus ring, and a 24px minimum height
- The composition audit is meant to flag its raw buttons. Pass `--allow system.raw_element=src/__variants__/<piece>/picker.tsx:harness chrome outside the system` when auditing the folder, or audit only variant files.

## Reference implementation

Copy `picker.tsx` unchanged and edit only the import map in `host.tsx`. Together they are under 120 lines. They were type-checked under a strict TypeScript config (`noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `verbatimModuleSyntax`) and exercised with the engine on React 19 and Vite: keys, state buttons, `?__state=`, `H`, and a variant that throws.

```tsx
// picker.tsx: the dev-only variant picker from the ui-variants skill. It stays outside the design system; do not restyle it.
import { useCallback, useEffect, useState, type CSSProperties } from 'react'

export type VariantMeta = { id: string; name: string }
declare global { interface Window { __setVariant?: (id: string) => boolean; __setState?: (id: string) => boolean } }

// One choice kept in the URL, mirrored on <html data-variant|data-state>, and settable through window.__setVariant|__setState.
export function useChoice(param: string, ids: readonly string[], key: 'variant' | 'state', setter: '__setVariant' | '__setState') {
  const list = ids.join(',')
  const [active, setActive] = useState(() => {
    const id = new URLSearchParams(window.location.search).get(param)
    return id !== null && ids.includes(id) ? id : (ids[0] ?? '')
  })
  const select = useCallback((id: string) => {
    if (!list.split(',').includes(id)) return false
    const url = new URL(window.location.href)
    url.searchParams.set(param, id)
    window.history.replaceState(window.history.state, '', url)
    setActive(id)
    return true
  }, [param, list])
  useEffect(() => { document.documentElement.dataset[key] = active }, [key, active])
  useEffect(() => {
    window[setter] = select
    return () => { delete window[setter]; delete document.documentElement.dataset[key] }
  }, [setter, key, select])
  return [active, select] as const
}

const BAR: CSSProperties = {
  position: 'fixed', zIndex: 2147483647, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4,
  maxWidth: 'calc(100vw - 24px)', padding: 4, borderRadius: 10, background: 'rgb(20 20 20 / 0.92)',
  boxShadow: 'inset 0 0 0 1px rgb(255 255 255 / 0.12)', color: 'rgb(255 255 255 / 0.55)',
  font: '500 12px/16px system-ui, -apple-system, "Segoe UI", sans-serif', userSelect: 'none',
}
const CORNER = {
  'bottom-right': { right: 12, bottom: 12 }, 'bottom-left': { left: 12, bottom: 12 },
  'top-right': { right: 12, top: 12 }, 'top-left': { left: 12, top: 12 },
} as const
const LABEL: CSSProperties = { padding: '0 4px 0 8px' }
const item = (on: boolean): CSSProperties => ({
  minHeight: 24, padding: '4px 8px', border: 0, borderRadius: 6, font: 'inherit', cursor: 'pointer',
  background: on ? 'rgb(255 255 255 / 0.16)' : 'transparent', color: on ? '#fff' : 'rgb(255 255 255 / 0.7)',
})

type PickerProps = {
  axis: string; variants: readonly VariantMeta[]; active: string; onVariant: (id: string) => boolean
  states: readonly string[]; state: string; onState: (id: string) => boolean; corner?: keyof typeof CORNER
}

export function VariantPicker({ axis, variants, active, onVariant, states, state, onState, corner = 'bottom-right' }: PickerProps) {
  const [hidden, setHidden] = useState(false)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return
      const el = e.target instanceof HTMLElement ? e.target : null
      if (el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))) return
      if (e.key === 'h' || e.key === 'H') setHidden((h) => !h)
      const next = variants[Number(e.key) - 1]
      if (next) onVariant(next.id)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [variants, onVariant])
  if (hidden) return null
  return (
    <div data-state-switcher="" role="group" aria-label="Variant picker" style={{ ...BAR, ...CORNER[corner] }}>
      <span style={LABEL}>{axis}</span>
      {variants.map((v, i) => (
        <button key={v.id} type="button" data-variant={v.id} aria-pressed={v.id === active}
          onClick={() => onVariant(v.id)} style={item(v.id === active)}>{i + 1} {v.name}</button>
      ))}
      <span style={LABEL}>State</span>
      {states.map((s) => (
        <button key={s} type="button" data-state={s} aria-pressed={s === state}
          onClick={() => onState(s)} style={item(s === state)}>{s}</button>
      ))}
    </div>
  )
}
```

```tsx
// host.tsx: the only file that imports variants. It is deleted with the harness at promotion.
import { Component, lazy, Suspense, type ComponentType, type ReactNode } from 'react'
import { copy, fixture, STATES, type StateId, type VariantProps } from './fixture'
import { useChoice, VariantPicker } from './picker'
import spec from './variants.json'

// One dynamic import per variant, so a file that fails to load or render breaks only its own option.
const files: Record<string, () => Promise<{ default: ComponentType<VariantProps> }>> = {
  quiet: () => import('./quiet'),
  ledger: () => import('./ledger'),
  split: () => import('./split'),
}
const views = Object.fromEntries(Object.entries(files).map(([id, load]) => [id, lazy(load)]))

class Boundary extends Component<{ id: string; children: ReactNode }, { error: string | null }> {
  override state = { error: null as string | null }
  static getDerivedStateFromError(error: Error) { return { error: error.message } }
  override render() {
    if (this.state.error === null) return this.props.children
    return <p role="alert">Variant {this.props.id} failed: {this.state.error}</p>
  }
}

export default function VariantHost() {
  const [active, setVariant] = useChoice('__variant', spec.variants.map((v) => v.id), 'variant', '__setVariant')
  const [state, setState] = useChoice('__state', STATES, 'state', '__setState')
  const View = views[active]
  return (
    <>
      <Boundary key={active} id={active}>
        <Suspense fallback={null}>{View && <View data={fixture[state as StateId]} copy={copy} />}</Suspense>
      </Boundary>
      <VariantPicker axis={spec.axis} variants={spec.variants} active={active} onVariant={setVariant}
        states={STATES} state={state} onState={setState} />
    </>
  )
}
```

- `override` is required under `noImplicitOverride` and harmless without it. A project without `resolveJsonModule` copies the ids and names from `variants.json` into a constant instead of importing it.
- A lint rule for fast refresh may warn that `picker.tsx` exports a hook. That is expected for dev-only harness code.

## Fixture and copy deck

`fixture.ts` holds the one set of facts every variant renders. Type it from the app's real models, and give each state an `expect` string only that state shows.

```ts
// fixture.ts: one set of facts for every variant. Type it from the app's real models.
import type { UsageMetric } from '@/types/usage'

export const STATES = ['default', 'empty', 'long'] as const
export type StateId = (typeof STATES)[number]

// The copy deck: one string per slot that has a job. Variants render these and add none.
export const copy = { title: 'Specs created', empty: 'No specs were created this month', action: 'Open the usage report' } as const

export const fixture: Record<StateId, UsageMetric[]> = { default: [/* the count the page really carries */], empty: [], long: [/* 60 rows, a 60-character name, a five-digit count */] }

export type VariantProps = { data: UsageMetric[]; copy: typeof copy }
```

## Mount line

One guarded line in the real parent renders the host when the param is present. It is the only production file the harness touches, and promotion removes it. The dev flag keeps the harness out of the production bundle.

```tsx
const VariantHost = import.meta.env.DEV ? lazy(() => import('@/__variants__/metric-card/host')) : null

// where the piece renders:
{VariantHost && new URLSearchParams(window.location.search).has('__variant')
  ? <Suspense fallback={null}><VariantHost /></Suspense>
  : <MetricCard {...props} />}
```

- Use the framework's own dev flag and alias. In a server-rendered app, load the host on the client only, because the hooks read `window`.
- When the app rewrites its own query string (filters, tabs, an open item), the param can drop on navigation. The choice survives in memory, and a reload falls back to the first variant.

## `states.json`

One spec covers every variant. The engine's `variants` command loads each id with `--param __variant`; until it ships, put the id in `url` and run the spec once per variant.

```json
{
  "screen": "metric-card",
  "url": "http://127.0.0.1:5173/usage?__variant=quiet",
  "ready_when": "[data-variant-root]",
  "states": {
    "default": { "expect": "text:Specs created" },
    "empty": { "steps": [{ "click": "[data-state-switcher] [data-state=empty]" }], "expect": "text:No specs were created this month" },
    "long": { "steps": [{ "click": "[data-state-switcher] [data-state=long]" }], "expect": "text:<the 60th row's name>" }
  }
}
```

- `ready_when` and `expect` must point at the piece, never at the picker. The engine skips the picker's subtree, so a target inside it never becomes visible and the state is reported unconfirmed.
- Each variant's root element carries `data-variant-root="<id>"`, which gives the spec a ready target without depending on one variant's markup
