# Contributing

Keep changes small enough to review against the skill's real routing problem. Read [`docs/SKILL-AUTHORING.md`](docs/SKILL-AUTHORING.md), edit the narrowest skill, and keep its references and scripts beside it.

Before opening a pull request:

```bash
python3 scripts/validate.py
bash scripts/check.sh
git diff --check
```

When a skill's description or name changes, update its routing fixtures and `migration.json` if an existing invocation changes. When a package manifest version changes, update all three manifests, `package.json`, and `CHANGELOG.md` together.

Do not publish, install into a user's global agent directory, or change the work plugin from a contribution branch. Those are release actions for the repository owner.
