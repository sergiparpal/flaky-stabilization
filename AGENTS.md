# Repository Guidelines

## Project Structure & Module Organization

Runtime code lives in `flaky_stabilization/`. Feature stages are separated into
`history/`, `detective/`, `triage/`, `healer/`, `bugreport/`, `pii/`, and `incidents/`;
`orchestrator/` composes them, `storage/` owns SQLite infrastructure, and `common/` is the
dependency-light kernel. Keep Hermes-facing wiring in `registration.py` and `cli.py`.

There are **two composition roots**. `registration.py` composes the plugin onto a Hermes
`PluginContext`; `__main__.py` composes the same stage CLIs onto a plain argparse parser for the
standalone `flaky-stab` binary. The Hermes rule applies to it in reverse: `__main__.py` must
**never** import a Hermes surface, not even lazily — `tests/test_import_boundaries.py` enforces
that, and the point of the binary is that it runs where Hermes is not installed. Both roots drive
one `state.db` through one migration ladder; `tests/test_composition_roots.py` pins them to the
same schema version and the same paths. `action.yml` (the composite GitHub Action) and
`docs/examples/` wrap the standalone root — consumer workflows live in `docs/examples/`, never in
`.github/workflows/`, which would run them against this repository.

Tests mirror the package under `tests/<stage>/`. Cross-stage and integration checks live directly
under `tests/` and `tests/integration/`; stable tool contracts are captured in `tests/snapshots/`.
Operational scripts are in `scripts/`, plugin metadata is in `plugin.yaml`, and design records are
in `docs/`.

## Build, Test, and Development Commands

Use Python 3.11–3.14.

```bash
python3 -m pip install -e ".[dev]"       # editable install with pytest and Ruff
bash scripts/run_tests.sh                # sanctioned offline test suite
bash scripts/run_tests.sh tests/history  # run one stage
bash scripts/run_tests.sh -- -k migrate  # run a focused selection
ruff check .                             # lint and import-order checks
python3 -m pip wheel . --no-deps          # build a wheel
```

Set `HERMES_REPO=/path/to/hermes-agent` when running loader integration against a real checkout.

## Coding Style & Naming Conventions

Use four-space indentation, type hints for public interfaces, `snake_case` for modules/functions,
and `PascalCase` for classes. Ruff enforces `E`, `F`, `W`, `I`, `UP`, and `B` rules with a
100-character line limit. Source packages carry **no** per-file ignores — that debt was cleared,
so do not add new suppressions to `flaky_stabilization/*`. The remaining `per-file-ignores` in
`pyproject.toml` cover ported legacy test suites only (`tests/history`, `tests/detective`,
`tests/pii`, `tests/bugreport`, `tests/triage`, `tests/incidents`); keep them and do not
mechanically restyle those files. Maintain the enforced dependency direction:
`common` ← stages/storage ← `orchestrator` ← registration/CLI.

## Testing Guidelines

Write pytest files as `test_*.py` and name tests after observable behavior. Add tests beside the
affected stage and update snapshots only for intentional schema changes. Docker, end-to-end, and
live tests must use their declared markers; the default suite excludes all three and scrubs
credentials. Maintain at least 85% coverage:

```bash
bash scripts/run_tests.sh -- --cov=flaky_stabilization --cov-fail-under=85
```

GitHub Actions (`.github/workflows/ci.yml`) runs five jobs on push and pull request: `lint`
(`ruff check .`), `tests` (the offline suite on Python 3.11/3.12/3.13/3.14 with the 85% coverage gate),
`docker-tests` (the docker-marked sandbox tests against a real daemon —
`bash scripts/run_tests.sh tests/healer -- -m docker -vv`, which needs the toy-app fixture
dependencies installed), `build` (wheel build plus a smoke install that imports
`flaky_stabilization.register`), and `action-smoke` (the composite action run end to end against a
JUnit fixture — the only check that exercises `action.yml`, which no unit test can). Run the lint
and offline commands locally before pushing; the docker job is the one that catches sandbox
regressions the offline suite cannot.

## Commit & Pull Request Guidelines

Recent commits use short imperative subjects such as `Add LICENSE file` or scoped forms such as
`docs: update license to MIT`. Keep each commit focused. PRs should explain what changed, why,
user/developer impact, linked issues, and validation performed. Include screenshots only for
visible output changes and call out schema, migration, credential, PII, or external-write effects.

`main` is protected by a branch ruleset: **direct pushes are rejected**, and merging requires the
`ci-complete` status check to pass. So the loop is branch, push, open a PR, let CI go green, merge.
Review approvals are **not** required (the count is 0) — a solo change stays a one-person
operation, gated by CI rather than by a second pair of eyes.

`ci-complete` is a single aggregating job that fails unless `lint`, `tests`, `docker-tests`,
`build`, and `action-smoke` all succeeded. **Any new job must be added to its `needs:` list**, or it silently stops
gating anything. The ruleset requires that one stable name rather than the individual matrix legs
on purpose: a dropped Python version would otherwise become a required check that never reports
again and would block every merge permanently.

Three files tell consumers which ref of *this* repository's action to use: `README.md` and both
files in `docs/examples/`. They currently say `@v0.3.0`, the first release containing `action.yml`.
**Every release must repoint them at the new tag** — a doc that names a ref which cannot resolve
`action.yml` is a broken quickstart, and nothing in CI catches it (`action-smoke` runs the action
from the local checkout, by design). That is exactly how `@v0.2.1` survived in the docs after the
action landed in the commit *after* that tag.

GitHub Actions are pinned to **full commit SHAs** with a trailing `# vX.Y.Z` comment, never to tags
— a tag can be repointed by its upstream owner, a commit SHA cannot. Keep new `uses:` lines pinned
the same way, and keep `permissions: contents: read`, `persist-credentials: false`, and per-job
`timeout-minutes` in place. This posture is shared across all six plugin repositories in this
account.

## Security & Configuration

Never commit tokens, local databases, or incident/test evidence containing PII. Keep
`GITHUB_TOKEN` and `JIRA_API_TOKEN` in the environment. Preserve fail-closed PII gates, bounded
network clients, and approval checks on PR/Jira write paths.
