# Worker brief

Each worker gets one filled copy of the template below as its whole Agent prompt. A worker sees nothing of this conversation and has no skill loaded, so the brief carries every path, rule, and command. Send all three Agent calls in one message so they run at the same time.

## Before you send

- Replace every `<slot>`. `<APP>` is the absolute path of the app folder that holds `src/`. Write absolute paths for the scripts too: sibling skill folders sit next to this skill's folder, so `<ENGINE>` is that folder's `interface-craft/scripts`, `<COMPOSE>` is `component-compose/scripts`, and `<SLOP>` is `slop-removal/scripts`.
- Paste the `inventory --brief` output and the scales from `.ui-evidence/config.json`. Do not tell the worker to go and find them; three workers reading the codebase three ways is how options drift off the system.
- Give the other positions and their measurable facts, never their file paths. A worker that reads another variant converges on it.
- The harness, fixture, copy deck, and mount line already exist and render. A worker that finds them broken returns `blocked`; it never repairs them.
- In a sequential run, use the same brief and fill "Must differ from" with the earlier variants' measured facts from their evidence

## Template

```text
You are building one variant of <piece> in <product>. Two other workers are building the other variants in the same folder at the same time. Build yours, prove it with the commands below, and reply with the JSON report at the end. Do not ask questions. If something outside your file blocks you, stop and return status "blocked" with the reason.

THE PIECE
<One sentence: what it is, where it renders, what it must do.>
Page: <URL>?__variant=<id>. The dev server is already running. Do not start, stop, or restart it.

YOUR VARIANT
id: <id>    name: <name>    axis: <axis>
Position: <position>
Thesis: <thesis>
Right when: <right when>    Costs: <costs>
Facts your variant must show when measured: <two or three facts from variants.json>
Must differ from (do not drift toward these):
- <other name>: <position>. Measured or expected facts: <facts>
- <other name>: <position>. Measured or expected facts: <facts>

FILES
You own exactly one file: <APP>/src/__variants__/<piece>/<id>.tsx. It default-exports a component that takes VariantProps from ./fixture, and its root element carries data-variant-root="<id>".
Read but never edit: fixture.ts, variants.json, states.json, picker.tsx, host.tsx, the parent page, everything under <UI_DIR>, and token, theme, and global style files. Do not open the other variant files.
If your position needs a new variant of an existing primitive (a Badge tone, a Button size), define it inside your file, mark it with a comment "proposed: <Component> <variant>: <why>", and list it under "proposed". If it needs a component the inventory lacks, build it inside your file only for a real gap and list it under "created" with the gap. Never edit <UI_DIR> to make your option work.

COMPONENTS YOU MUST COMPOSE FROM (inventory --brief)
<paste>

SCALES (from .ui-evidence/config.json)
<spacing base and steps, radius scale, control heights, type roles>
Use only these values and the project's tokens. No literal colors, no new font sizes, no new radii, no new shadows.

COPY AND DATA
Every visible string comes from `copy` in fixture.ts; add none and reword none. Data arrives as `data` for the states <default, empty, long>. Never hard-code values. The empty state names what is missing and offers the deck's one action.

LOOP (at most <3> rounds). Run every command from <APP>, so evidence lands in the shared <APP>/.ui-evidence/.
1. Capture: python3 <ENGINE>/ui_measure.py variants <URL> --param __variant --ids <id> --spec <APP>/src/__variants__/<piece>/states.json --widths 390,768,1280 --themes <themes>
   If the variants command does not exist yet: python3 <ENGINE>/ui_measure.py capture "<URL>?__variant=<id>" --label <id> --widths 390,768,1280 --themes <themes>. For states, copy states.json to a temp file outside the repo, set its url to "<URL>?__variant=<id>", and run python3 <ENGINE>/ui_measure.py states --spec <that copy> --label <id> with the same widths and themes.
2. Read the run's summary.md. Bring FAIL to zero. Fix each WARN or justify it in one line. Never hide overflow, shrink text, nudge a margin, or add !important to clear a finding.
3. python3 <COMPOSE>/ui_inventory.py audit <APP> --files <APP>/src/__variants__/<piece>/<id>.tsx --fail-on P1. Zero P1. Fix P2 where it costs nothing.
4. python3 <SLOP>/slop_scan.py scan <APP> --files <APP>/src/__variants__/<piece>/<id>.tsx, and read the slop.* rows in the capture summary. Clear them, or justify each in one line.
5. Confirm default, empty, and long all rendered (the state is confirmed in the evidence).
Type errors in other variant files are not yours. Fix only your own file's.

DONE WHEN
0 FAIL, every WARN fixed or justified, 0 P1, slop findings clear or justified, three states confirmed, and the measured facts still match your position, not the others.

REPLY WITH THIS JSON ONLY
{
  "id": "<id>",
  "status": "ready | failed | blocked",
  "file": "<APP>/src/__variants__/<piece>/<id>.tsx",
  "evidence": ["E##@390:default#light", "..."],
  "fail": 0,
  "warn_justified": ["rule.id: reason"],
  "states_confirmed": ["default", "empty", "long"],
  "audit_p1": 0,
  "slop_open": [],
  "composed": ["Card", "Badge"],
  "proposed": [{ "component": "Badge", "variant": "subtle", "why": "..." }],
  "created": [{ "name": "...", "gap": "..." }],
  "measured": { "<fact>": "<value with evidence id>" },
  "notes": "<one or two sentences, or empty>"
}
```

## After the replies

- Save each reply beside `variants.json` for the present table, and check every claim against its evidence before using it
- A `failed` or `blocked` variant gets one retry through SendMessage to the same worker, with the failing evidence ids and the one thing to change. A second failure drops it, and the table names it and the reason.
- Never fix a worker's file yourself to rescue it; that hides which brief failed and mixes your priors into its option
