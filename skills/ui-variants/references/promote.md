# Promote and clean up

Run these in order once the owner names a winner. Each step has a check; a harness left behind ships fixture data and a dev-only import path.

## Promote the winner

1. **Rebuild in place.** Implement the winner in the real component or route, on the real data source and props, following the project's conventions. Do not copy the variant file wholesale: real data replaces `fixture`, the deck's strings move into the component or the project's i18n, and no import points into `__variants__`.
2. **Move proposed variants into `ui/`.** Each `proposed:` comment becomes a real variant on the primitive (a `cva` variant or the library's equivalent). Each created component moves to the project's components folder with its gap noted in a comment or the PR. Run `python3 $component-compose/scripts/ui_inventory.py audit --files <changed files> --fail-on P1` and reach zero P1.
3. **Adopt new values.** Run `python3 $design-system/scripts/token_scan.py adopt <project> --files <changed files>` and apply each proposal: the nearest token, a retargeted token, a new role, or a recorded one-off. Never add a parallel token beside the role it replaces.
4. **Render the real states.** Capture the promoted piece with the screen's own `states.json` (loading, empty, error, and long), at 390, 768, and 1280 in the shipped themes. Loading and error were not part of the variant gate, so they are checked here. FAIL is zero and each WARN is fixed or justified.
5. **Compare.** Run `python3 $interface-craft/scripts/ui_measure.py compare <baseline run> <final run>` against the baseline captured before any variant work. Disclose every dropped, new, or reordered string in the report.

## Remove the harness

6. Delete the mount line from the parent and restore its direct render of the piece
7. Delete `src/__variants__/<piece>/` entirely: the losing variants, the winner's variant file, the picker, host, fixture, states, `variants.json`, and saved worker replies
8. Prove zero leftovers:
   - `grep -rn -e "__variant" -e "__variants__" -e "__setVariant" -e "data-variant-root" src` prints nothing
   - A search for each variant id and name finds nothing except names the winner kept on purpose
   - `git status --short` lists no harness path, and nothing new in `ui/` beyond the moved variants and components
   - The production bundle has no harness module. Check this only where a local production build is safe to run; some builds load analytics or error tracking.
9. Summarize the change with `git diff --name-only <ref> | python3 $component-compose/scripts/ui_inventory.py summary --base <ref>` for the Reused, Extended, Composed, and Created table
10. Report with evidence ids for every rendered claim, then run `python3 $interface-craft/scripts/ui_claims.py check report.md --source .` and fix what it rejects

## Another round instead

Keep the harness. Delete the losing variant files and their host entries, then run the axis step again with new positions around the direction the owner leaned toward. A second round may change the axis; it still has only one.

## No winner

Delete the mount line and the harness folder and run the leftover checks in step 8. The piece stays as it was, and `compare` against the baseline shows no change.
