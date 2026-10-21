# Skill authoring guide

Rules for writing and changing skills in every plugin in this repo. Short
version: keep each `SKILL.md` lean and human-readable, and put determinism
and evaluation around the skills, not in them.

## Naming

Plugin and skill names are kebab-case. Skill names use two words, subject
then deliverable, so the name alone says what the skill does:
`ui-layout`, `design-system`. Refer to a sibling skill in the same
plugin as `$skill-name`; `scripts/validate.py` fails when a reference does
not resolve. To run a sibling skill's script, write the path as
`$skill-name/scripts/file.py` (see "No shared runtime").

## Canonical model

Not every skill needs every section, but every skill follows the same
mental model:

- **Work** — the smallest reliable procedure: what evidence to inspect
  before deciding, then the steps. Procedures over declarations.
- **Deliver** — the output shape: an explicit template or format the
  agent must follow.
- **Worked example** — one filled example following the Deliver shape.
  Demonstrate evidence and reasoning; never establish a technology,
  architecture, sizing, branch, typography, framework, or product
  default unless it is intrinsic to the skill. Prefer detect →
  preserve → justify: show detected values, keep them, change only
  what the task justifies.
- **Gotchas** — 3–8 non-obvious mistakes agents repeatedly make on
  this work. Highest-value section per current authoring guidance;
  feed real observed corrections back here.
- **Boundaries** — adjacent skills and cases this skill must not own,
  as "do not use when … use `$other` instead" routing rules.

## Sentence test

Every sentence must pass: **would a competent agent likely get this
wrong without us saying it?** If not, remove it. Over-comprehensive
skills degrade performance by competing for model attention.

## Prove guidance with the eval loop

Every line of guidance earns its place through observed failure:

1. **Baseline first.** Freeze a realistic prompt with fixed inputs and
   render settings. Generate once without the new guidance and keep
   the output, however rough.
2. **Run a matched comparison.** Same prompt, inputs, model, and
   viewport with the guidance loaded. Score both blind against a
   short rubric: did the facts survive, is the reader's decision
   clear, is the recurring correction resolved.
3. **Encode in the narrowest place.** Judgment goes into prose as a
   rule, reusable mechanics into scripts or stylesheets, and anything
   mechanically checkable into a deterministic check. One correction,
   one home.
4. **Rerun the affected scenarios.** A fix that helps one artifact can
   quietly hurt another; verify before keeping it.
5. **Count complaints over time.** Group repeated review feedback,
   encode the fix, and watch whether that complaint becomes rarer.
   If it doesn't, the fix is wrong — unclear, unloading, missing a
   primitive, or needing a check instead of prose.

Keep a small holdout set hidden while editing so descriptions don't
overfit the test prompts. Final changes stay human-reviewed.

## Descriptions are a routing interface

Frontmatter `description` is what Claude reads when deciding whether to
load a skill. Lead with what the skill delivers, then "Use when …" with
the phrases people actually type. Keep it under 500 characters; Claude
Code truncates `description` plus `when_to_use` at 1,536 characters in
its skill listing. Where a plugin keeps `evals/triggers.json`, each suite
needs 5 positive and 5 negative cases with `expected_skill`, at least 3
of them near-misses. A description change that moves a boundary needs the
neighboring skill's description checked in the same pull request.

## Progressive disclosure

Keep `SKILL.md` lean (under ~700 words; `scripts/validate.py` warns
above 800 and fails above 1,000). Move variant-specific detail into
`references/` and link it from a "Load when needed" table. Scripts in
`scripts/` must be runnable helpers with clear run-vs-read intent,
never required imports: each skill carries everything it needs, or runs
a sibling skill's script (see "No shared runtime").

## Renaming and removing skills

Renaming or removing a skill is a major version. When you rename one,
keep the old name for two releases as a redirect stub: a folder holding
only a `SKILL.md` whose frontmatter sets `disable-model-invocation: true`,
a description that says it moved, and a one-paragraph body (under about
60 words) pointing to the new skill as `$new-name`. `scripts/validate.py`
treats it as a stub: it skips the gotcha, section, and eval checks,
requires the plugin README to list it under a "Moved" note, and rejects
`$old-name` references and `expected_skill` values that point at it.
Delete the stub once its two releases have passed.

## No shared runtime

Repository-level `scripts/` hold test infrastructure only. Skill
dependencies stay inside the plugin: no cross-plugin references, and no
cross-skill imports.

A skill may run, never import, a sibling skill's script. In skill text,
write the path as `$skill-name/scripts/file.py`, for example
`python3 $interface-craft/scripts/ui_measure.py capture URL`, and say
once in the skill that sibling skill folders sit next to this skill's
folder. This is safe because a plugin installs as a whole: every skill in
`plugins/<plugin>/` ships together, so the sibling script is always
there. It stays a command-line contract, not a code dependency: the
caller depends on the script's arguments and output, so either skill's
internals can change without breaking the other. `scripts/validate.py`
fails when the path names a skill that does not exist in the plugin, a
file that does not exist, or a removed or stub skill. Keep the path
inside the plugin: no `$skill-name` for another plugin, no repository
paths.

## Trust boundaries

Default to skills plus local helper scripts. A plugin that adds an MCP
server, hooks, or network calls needs a stated reason in its README and a
reviewer who checks what it can reach. `allowed-tools` can narrow a skill,
but it is never the security boundary.

## Claude Code frontmatter

`name` and `description` are required. Claude Code fields such as
`when_to_use`, `disable-model-invocation`, `allowed-tools`, and `model`
are allowed when a skill needs them; say why in the pull request.
