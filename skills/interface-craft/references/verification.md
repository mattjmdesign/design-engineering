# Verification

How to produce evidence, judge it, and report it. Keep the effort proportional: a button fix needs one capture and a sentence, and a redesign needs the full set. The tools cover web pages; for any other target, mark every measured claim unverified.

## Run the Tools

Run from the project with the app already serving on a local address.

```bash
python3 scripts/ui_measure.py init URL
python3 scripts/ui_measure.py capture URL --phase baseline --label before
python3 scripts/ui_measure.py capture URL --widths 390,768,1280,1920 --themes light,dark --phase final
python3 scripts/ui_measure.py routes URL [URL ...] [--widths 1280,390]
python3 scripts/ui_measure.py states URL --spec states.json --sheet
python3 scripts/ui_measure.py compare 001-baseline-before 002-final
python3 scripts/ui_claims.py check report.md --source .
```

- Evidence lands in `.ui-evidence/<NNN-phase-label>/`: `summary.md` (60 lines at most, grouped by cause), one `E##.png` and `E##.json` per capture, and a manifest. Read the summary first and open a JSON file only for a finding you must fix.
- `init` reads the project's own spacing, radius, and type tokens so the grid and scale checks target the project's scale. Run it once per project.
- `--ready SELECTOR` makes a capture wait for real content, not a skeleton
- `routes` loads each URL, compares the rail, header, footer, content column, and page title across them, and writes a normal evidence run, so `rule:shell.consistency` and `rule:layout.content` findings cite ids like any capture. Cite a route by its label in the state slot, such as `[E05@1280:inbox#light]`. Pass routes that share a shell, and run sign-in and marketing routes separately. Fix or justify each WARN and read INFO as possibly intentional. Check the manifest notes for a landmark that was not found, such as "No header was found". A clean run shows the shell matches in the default state; it does not prove one shared component.
- `--storage KEY=VALUE` seeds browser storage before the page runs, for first-run dialogs or a dev login flag. Only key names are recorded.
- A screenshot from another tool can be added with `ui_measure.py register shot.png --viewport 390x844`. It counts as unmeasured evidence and cannot back a measured claim.
- `xray RUN/E## [--region SELECTOR]` draws numbered outlines, key lines, shaded gutters, and near-miss deltas over one capture, with zoomed crops on a 4px lattice and a JSON sidecar. Use it to find what to fix; the picture passes nothing on its own, so cite the capture's measured findings.

## Read the Tiers

- **FAIL** is invalid evidence or an objective defect: page overflow, contrast under threshold, a target under 24px with a neighbor inside the 24px circle, an invisible focus ring, console errors, a state that did not render, unnamed controls. Bring FAIL to zero, or report each remaining one with its cause.
- **WARN** is a probable defect or drift from the project's scale. Fix it, or justify it in one line (intentional, third-party, or the project scale differs). A WARN with no line is unreviewed.
- **INFO** is inventory and documented exceptions. It needs no action.
- `rule:viewport.truth` fails when the page was laid out at a width other than the one requested. Fix the capture before trusting anything else in that run. `rule:theme.truth` warns when a requested theme did not change the page, which means the dark captures prove nothing. `rule:viewport.meta` notes a missing viewport tag on a page measured at 820px or narrower.
- Slop rules flag AI tells and never FAIL. At WARN: `rule:slop.eyebrow`, `rule:slop.repeated_text`, `rule:slop.generic_action`, `rule:slop.empty_state`, `rule:slop.nested_cards`, `rule:slop.gradient_text`, `rule:slop.glow`, `rule:slop.side_stripe`, and `rule:slop.badge_density`. At INFO: `rule:slop.restates_heading` and `rule:slop.dash`. Treat a WARN like any other; a hunt across the screen belongs to `$slop-removal`.
- `data-ui-allow="rule.id: reason"` on an element demotes that finding to INFO and is counted in the summary. An allow without a reason is its own finding, `rule:allow.reason`.
- When a finding is wrong, say which one and why. Never edit evidence. Project-wide decisions go in `.ui-evidence/config.json` under `overrides`, including `capture.ignore_selector` for third-party widgets and dev overlays.

## Cite Every Claim

Every statement about the rendered interface in a report carries an evidence id such as `[E03@390:error#dark]`, or the words `(unverified: reason)` with a reason of three words or more. A visual claim from a registered screenshot may cite it, and a measured claim may not.

`ui_claims.py` rejects unknown ids, evidence that was edited, a width, theme, or state that does not match the citation, a state that never confirmed, numbers that disagree with the JSON, evidence older than the source it covers, text that disappeared without disclosure, and invented `[src:path:line]` citations. It proves the evidence exists and fits. It cannot prove anyone looked, so say in a sentence what you looked at in the contact sheet.

## Safety

Test only a local or designated environment with fixtures. The tool answers writes locally and never sends them, but your own steps can click real controls, so do not run steps that would send, charge, delete, publish, or change permissions anywhere real. Use `--allow-remote` only for a site you are allowed to test, and never with a real account.

## Audit Findings

Use one record per root cause: observation, evidence id, user impact, cause, fix, and how to verify the fix. Name the systemic cause once and list the screens it touches. Order by consequence.

- **Severity:** P0 blocks the critical task or is unsafe. P1 materially impairs a primary task, is a major access barrier, or repeatedly breaks responsive use. P2 is local friction with a workaround. P3 is polish.
- **Confidence:** confirmed (observed), likely (strong evidence, not reproduced), or hypothesis. Keep it separate from severity and never lower severity because confidence is low.
- Tie aesthetic findings to hierarchy, comprehension, brand fit, or task behavior. Do not rewrite the product from personal taste.
- Mark a risk the team consciously accepts as accepted, not fixed. It stays unresolved.

## Done Means

- The task works, and the requested visual improvement is visible against the baseline.
- FAIL is zero, each WARN is fixed or justified, and every reachable state in `states.json` is confirmed.
- Narrow, intermediate, and wide captures exist for each changed surface, in each shipped theme.
- Keyboard and screen reader behavior that the tools cannot judge is either checked by hand or marked unverified.
- `ui_claims.py` passes, and the report lists what was not tested.
