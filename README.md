# Design Engineering

13 skills for product designers building software with AI coding agents. Plan interfaces, build with existing components, and check the result in the browser.

## Install

### Codex

```bash
codex plugin marketplace add mattjmdesign/design-engineering
codex plugin add design-engineering@design-engineering
```

### Claude Code

```bash
claude plugin marketplace add mattjmdesign/design-engineering
claude plugin install design-engineering@design-engineering
```

### OpenCode and other agents

```bash
npx skills add mattjmdesign/design-engineering
```

Select your agent and installation scope in the installer.

### Pi

```bash
pi install git:github.com/mattjmdesign/design-engineering@main
```

## Use a skill

Describe the work, or name a skill in your prompt:

```text
Use $interface-craft to improve this screen and verify the rendered result.
Use $ui-representation to turn these notes into a task, facts, and component map.
Use $design-system to audit the tokens before changing shared styling.
```

Load the skill that fits your task. There is no required sequence.

## Skills

| Area | Skill | Use it for |
| --- | --- | --- |
| Define | `product-intent` | Settle the user, outcome, and smallest useful scope. |
| Define | `ui-representation` | Turn notes, data, or feedback into a UI pattern and component map. |
| Structure | `site-map` | Plan routes, navigation, URL state, and access areas. |
| Structure | `user-flow` | Map a task through success, interruption, cancellation, and recovery. |
| Structure | `content-hierarchy` | Rank a page's information and actions, then write its copy deck. |
| Structure | `state-coverage` | Find and make loading, empty, error, permission, and recovery states renderable. |
| Build | `ui-layout` | Plan grids, regions, widths, gutters, shells, and responsive behavior. |
| Build | `component-compose` | Reuse the repository's primitives and record real component gaps. |
| Build | `design-system` | Define, audit, migrate, adopt, and guard tokens and shared styling. |
| Refine | `ui-variants` | Compare three measured options for one component or content region. |
| Refine | `slop-removal` | Remove generated UI tells using source and rendered checks. |
| Verify | `interface-craft` | Bring a built interface to a clean, measured baseline. |
| Context | `agent-context` | Keep repository instructions accurate and useful to the next agent. |

For previous skill names, see the [migration guide](migration.json).

## Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and the [skill authoring guide](docs/SKILL-AUTHORING.md) for editing guidance.

## License

Original material is [MIT licensed](LICENSE). See [third-party notices](THIRD-PARTY-NOTICES.md) for credited adaptations and their licenses.
