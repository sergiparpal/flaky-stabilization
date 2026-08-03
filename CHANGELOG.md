# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0] — 2026-08-03

The flaky-detection half becomes usable without Hermes Agent, as a standalone
CLI and a GitHub Action. Healing, triage, and bug-report structuring stay
Hermes-only: they need a model and the host's approval pipeline. No new runtime
dependency and no change to the plugin surface.

**`state.db` moves to schema v2** (a rebuild of the triage FTS mirror; see
Fixed). The migration is automatic and touches only derived data, but a v2
database is refused by 0.2.1 and earlier — upgrade every process that opens the
same `state.db`, and do not point an older plugin at an upgraded one.

### Added

- `flaky-stab`, a standalone console script (`flaky_stabilization/__main__.py`,
  also runnable as `python -m flaky_stabilization`). It is a *second
  composition root*, not a second package: `registration.py` composes the
  plugin onto a Hermes `PluginContext`, `__main__.py` composes the same stage
  CLIs onto a plain argparse parser. One repository, one `state.db`, one schema
  ladder — see `docs/STANDALONE-CLI.md` for why extraction was rejected.
- `$FLAKY_STAB_HOME`, the standalone home override. It heads the resolver chain
  in `paths.get_hermes_home()`, ahead of the Hermes lookups, because an env var
  the operator set explicitly must beat ambient discovery. With it unset, path
  resolution is unchanged for every existing install — pinned branch by branch
  in `tests/test_paths_cli_home.py`.
- `flaky-stab is-flaky <test_id> [--format json]`: the single-test query a CI
  gate needs, which existed only as a tool before. A flaky verdict exits `0`
  like any other answer (`1` for an unreadable database, `2` for a malformed
  `test_id`), so a gate cannot mistake a broken database for "not flaky".
- `action.yml`, a composite GitHub Action wrapping `ingest`, `scan`, and
  `is-flaky`, with `is-flaky` / `result-json` / `flaky-count` outputs and a
  `state-dir` input that becomes `FLAKY_STAB_HOME`. Two copy-paste consumer
  workflows are in `docs/examples/`; they live there rather than in
  `.github/workflows/` so they cannot run against this repository. Until this
  entry ships in a tag, the docs pin the action by commit SHA — `v0.2.1`
  predates both `action.yml` and the `flaky-stab` console script, so it cannot
  serve either.
- `action-smoke`, a CI job running the composite action end to end against a
  JUnit fixture, added to `ci-complete`'s `needs:` list.

### Changed

- `install-cron` degrades honestly when no `hermes` CLI is on `PATH`: the
  installed shim execs the `flaky-stab` console script instead of
  `hermes flaky-stab`, and the printed command becomes a plain crontab line
  instead of a `hermes cron create` invocation the operator cannot run. The
  shim's exec line is retargeted at install time, so `set -euo pipefail` and
  the `exec` (which makes the scan's exit code the job's exit code) are
  untouched. Schedule validation is unchanged and still runs before anything is
  written.
- The test bootstrap (`tests/conftest.py`, `scripts/run_tests.sh`) scrubs the
  `FLAKY_STAB_` env prefix. Because the new variable outranks the throwaway
  `HERMES_HOME` the suite sets, a stray one in a developer's shell would have
  pointed every test at their real profile.
- Supported Python widens to include 3.14 (`requires-python = ">=3.11,<3.15"`),
  with a matching `tests` matrix leg so the claim is actually gated by CI.
- The unified config's `detective` section documents `test_history_db_path`,
  which the stage honours but only its own `DEFAULT_CONFIG` listed. Resolution
  is unchanged; it is now visible in the resolved view instead of passing
  through as an unknown key.
- `detective.source_schema_version` is **removed from both defaults dicts** — it
  was never configuration. Nothing read it: the version recorded per scan comes
  from the database, and the guard that *refuses* a `history.db` newer than this
  build reads `detective.domain` directly. Listing it as a key invited someone
  to wire it up, which would have turned a refusal into an operator-settable
  ceiling. `flaky-stab status` now reports it as `expects_history_schema`,
  outside the resolved config. A value left in an existing `config.json` is an
  inert passthrough — nothing to migrate.

### Fixed

- **A crashing stage no longer loses the `pipeline_runs` ledger row.** Three
  paths in `stabilize_test_failure` could raise straight out of `Pipeline.run`
  — a non-numeric burn-in count (`ValueError`), a non-dict healer `report`
  (`AttributeError`), and any exception from the PII gate. The tool contract
  held (the handler returns an error envelope), but no row was written for
  exactly the runs an operator queries the ledger for. A raising PII gate now
  fails **closed**, like the gate-unavailable branch beside it.
- **Spooled CI-log evidence is removed even when a run crashes.** The cleanup
  sat at the tail of `run()`, so the crashes above skipped it and left the
  fetched log on disk (0600, under the 0700 data dir) until the 24-hour sweep
  reaped it. It is now in a `finally`.
- **The heal → history feedback loop actually feeds back.** A stable burn-in
  writes one synthetic green run per repeat, but all of them shared a
  `source_file` and timestamp — and the detective dedups logical runs on
  exactly that pair, so a 10-run burn-in contributed **one** green run to the
  next sweep. Each repeat now gets its own identity. The repeat count is also
  parsed defensively and bounded (it decides how many rows one heal writes into
  the public `history.db`).
- **The triage FTS mirror delete is index-backed.** `triage_patterns_fts`
  declared `signature` `UNINDEXED`, so removing one mirror row was a full
  virtual-table scan — run twice per learned pattern plus once per pruned row,
  on the synchronous triage path, growing linearly with the mirror. Schema v2
  rebuilds the mirror with `signature` indexed and the delete targets a rowid
  (200 deletes against a 20k-row mirror: 0.332 s → 0.002 s). The fuzzy lookup's
  MATCH is now column-scoped to `sample`, so newly-indexed hex digests cannot
  enter its token space. `project` stays `UNINDEXED` — it is only ever an
  equality filter.
- **`shutdown()` no longer permanently disables incident context injection.**
  `PrefetchCache.shutdown()` set a `_closed` flag nothing cleared, so a
  shutdown → initialize cycle (session end → session start in a long-lived
  gateway) left every `prefetch()` returning `""` — silently, with no error and
  no log. `PrefetchCache.reopen()` revives it.
- **An empty `FLAKY_HEALER_SUBPROC_ISOLATE_NET` no longer disables subprocess
  network isolation.** It tested `is not None`, so a bare `export VAR=` in a CI
  wrapper turned a security-relevant default off. Empty now means "unset",
  matching every other env reader in the plugin (`allow_subprocess_pr`,
  `safehttp._allow_private`, `config._apply_env_overrides`).
  `FLAKY_HEALER_RUN_CONCURRENCY` is aligned for the same reason.
- `/stabilize` classifies its first token with `logfetch.is_remote` instead of
  `startswith("http")`, so a bare test id such as `httpclient_timeout` is no
  longer read as a log URL.
- `history.db_path_override` equal to the Hermes home is rejected with a clear
  message instead of passing containment and failing later with sqlite3's
  opaque "unable to open database file".
- `IncidentStore.prune_older_than` and `delete_keys_not_in` run their FTS-mirror
  and base-table deletes in one transaction, so a failure between them cannot
  leave the mirror pointing at rows that no longer exist.
- The Jira transport no longer re-wraps its own response-size-cap error as
  `request failed: …`, which buried the message and dropped `.status`.
- `logfetch.read_local` closes the raw fd if `os.fdopen` itself raises, and the
  subprocess sandbox raises `SandboxError` instead of asserting on a missing
  stdout pipe (`assert` is stripped under `python -O`).

## [0.2.1] — 2026-07-30

Repository infrastructure and documentation. No runtime code changed — the
plugin behaves identically to 0.2.0; only the version strings moved.

### Added

- Dependabot updates for `pip` and `github-actions`, weekly, capped at five
  open pull requests per ecosystem. The actions ecosystem is the one that
  matters here: workflow `uses:` lines are pinned to full commit SHAs, so
  without Dependabot a pinned action never picks up an upstream security fix.
- `ci-complete`, a single aggregating CI job that fails unless `lint`, `tests`,
  `docker-tests`, and `build` all succeeded. `main`'s branch ruleset requires
  this one stable check name rather than the individual matrix legs: a dropped
  Python version would otherwise leave behind a required check that never
  reports again and blocks every merge permanently. Any new job must be added
  to its `needs:` list or it silently stops gating anything.
- `SECURITY.md`: private vulnerability reporting through GitHub Security
  Advisories, a 7-day initial response target, and a scope section pointing
  reviewers at sandbox containment, command construction, the `scripts/` Jira
  sync, and the dependency surface — the parts that matter in a tool that
  executes the target project's test suite.
- `AGENTS.md` now documents the `main` branch ruleset and the CI gating
  convention: direct pushes are rejected, merges require `ci-complete`, review
  approvals are not required, and actions stay pinned to full commit SHAs.

### Changed

- Install docs collapse install and enable into one `hermes plugins install
  sergiparpal/flaky-stabilization --enable` invocation (README, `MIGRATION.md`,
  `after-install.md`).
- README title reads "Flaky Stabilization"; the redundant release-history
  pointer at the foot of the README is gone.

### Fixed

- The `ci-complete` job was missing `timeout-minutes`, the one job in the
  workflow without it.

## [0.2.0] — 2026-07-26

Repository rename, CI, and the legacy-config deprecation path.

**Breaking for existing installs**: the plugin was renamed
`hermes-flaky-stabilization` → `flaky-stabilization`. Reinstall and re-enable
under the new name — the plugin id in `plugins.enabled`, the plugin directory,
the qualified skill name, the `pip install` name, and the import path all
changed. No data migration is needed; `history.db` and `state.db` are
untouched. See `MIGRATION.md` and `docs/DECISIONS.md`.

### Added

- GitHub Actions CI: ruff lint, the offline test suite on Python 3.11/3.12/3.13
  with coverage enforced at 85%, and a wheel build with an archive integrity
  check. E2E-marked tests are excluded from the offline CI runs.
- CI `docker-tests` job running the docker-marked sandbox tests against a real
  Docker daemon on ubuntu runners, plus new hardening tests: network egress
  blocked inside the container, the original project tree untouched after a
  sandboxed run, and docker infra exit codes (125/126/127) surfacing as
  `SandboxError` rather than fabricated test failures.
- Wheel smoke-install in the CI build job: the built wheel is installed and
  `flaky_stabilization.register` imported, so packaging errors fail the build
  instead of a user install.
- Five config keys that close the gap between what the deprecation warnings
  told operators to do and what was actually settable: `healer.github_api`,
  `healer.subproc_pass_env`, `healer.subproc_isolate_net`,
  `healer.run_concurrency`, and `jira.strict_redaction`. These behaviors were
  reachable only through `FLAKY_HEALER_GITHUB_API`,
  `FLAKY_HEALER_SUBPROC_PASS_ENV`, `FLAKY_HEALER_SUBPROC_ISOLATE_NET`,
  `FLAKY_HEALER_RUN_CONCURRENCY`, and `HERMES_JIRA_STRICT_REDACTION`, whose
  warnings said to move them into `config.json` where no matching key existed
  — so the advice could not be followed and the promised 1.0 removal would
  have deleted the only way to set them. Env still wins over the file, so
  existing installs are unaffected. Two of the five are security-relevant
  (`subproc_isolate_net`, `strict_redaction`) and are now inspectable in
  config rather than invisible in the environment. See `docs/DECISIONS.md` C1.
- `tests/common/test_deprecation.py::test_every_config_key_named_in_a_deprecation_message_exists`
  scans the package for deprecation replacement strings and fails if any names
  a key absent from `DEFAULTS`, so this class of defect cannot return.
- This `CHANGELOG.md`, backfilled for 0.1.0–0.1.2 and linked from the README.
- `docs/DECISIONS.md` entry H1: the healer is the plugin's differentiator —
  core, will grow, with docker CI coverage growing alongside it.

### Changed

- Repository renamed `hermes-flaky-stabilization` → `flaky-stabilization`; the
  GitHub slug, plugin manifest name, distribution name, and import package now
  all agree. Breaking for existing installs — reinstall and re-enable under the
  new name (no data migration needed; see `docs/DECISIONS.md`).
- README restructured to lead with the default install: quickstart and
  default-install trust facts (stdlib-only runtime, token-gated tools,
  `jira.enable_write: false`, `suggest` heal mode, docker→subprocess fallback,
  fail-closed PII gate) moved to the first screen; the seven-plugin migration
  story moved below the fold. No facts removed.

### Documentation

- Complete, verified configuration reference in the README: every section and
  key of `config.json` with its real default (previously a partial prose
  summary that also named `pii.max_files` instead of `pii.default_max_files`,
  and omitted `triage.log_roots`/`token_hosts`/`allow_private`,
  `healer.docker_image`/`git_tool`/`pr_tool`/`allow_subprocess_pr`,
  `detective.deliver`/`report_scope`, `history.db_path_override`, and five
  `jira` transport keys). The five settings that widen a security boundary are
  called out together.
- Environment variables are documented as a single mapping table — every
  deprecated override paired with the `config.json` key that replaces it —
  plus the one documented exception, `FLAKY_HEALER_DATA_DIR`.
- README security model: the triage SSRF guard's actual contract — the
  `triage.allow_private` opt-in, the loopback/cloud-metadata block that holds
  regardless of it (including IPv4-mapped IPv6 literals), and the token-host
  allowlist.
- `AGENTS.md`: source packages no longer carry ruff per-file ignores (the
  remaining ones cover ported legacy test suites only), and the four CI jobs
  are described so contributors know what runs before they push.

### Deprecated

- The legacy env overrides (`FLAKY_HEALER_*`, `HERMES_CI_TRIAGE_*`,
  `JIRA_BASE_URL`/`JIRA_EMAIL`, `HERMES_JIRA_STRICT_REDACTION`) and the legacy
  `test-history/config.json` precedence level: each use now logs one
  deprecation warning per process (logger `flaky_stabilization.deprecation`),
  naming the specific `config.json` key that replaces it, and all of them will
  be removed at 1.0. Move settings into `flaky-stabilization/config.json`. The
  `migrate` command is exempt — it is supposed to read legacy data.
  `FLAKY_HEALER_DATA_DIR` is the one override that stays environment-only: it
  selects the directory that *contains* `config.json`, so it cannot live
  inside it; its warning points at the default data directory instead.

### Security

- Triage SSRF guard: `ip_blocked` now unwraps an IPv4-mapped IPv6 literal
  (`::ffff:a.b.c.d`) and judges the embedded IPv4 address. On Python < 3.13
  the mapped form answers `is_loopback`/`is_link_local` as `False`, so with
  the private-range opt-in (`HERMES_CI_TRIAGE_ALLOW_PRIVATE` /
  `triage.allow_private`) enabled, `https://[::ffff:127.0.0.1]/…` and
  `https://[::ffff:169.254.169.254]/…` could reach loopback and cloud-metadata
  addresses that are promised to stay blocked regardless of the opt-in.

### Removed

- The dead `FLAKY_HEALER_HARDLINK_COPY` setting: `copy_project` always
  byte-copies (hardlinks were rejected as an isolation mode), but the config
  accessor and its docstring still described hardlinking as live behavior. The
  flag, the unused copy helper, and the stale docstrings are gone; the
  temp-file + `os.replace` write in `apply_ops` stays as defense in depth.

### Fixed

- Orchestrator remote-vs-local log classification now uses
  `logfetch.is_remote` instead of a naive `startswith("http")`, so a LOCAL
  log whose path merely begins with "http" (e.g. `http_logs/run.log`) is
  again PII-gated as bug-branch evidence and fetched for the incident-context
  query.
- `validate_no_pii` now enforces its documented `MAX_SKIPPED` cap on per-file
  skip entries too (only report rows are dropped; any skip still forces
  `complete: false`, so the gate semantics are unchanged).
- `storage.db.open_private` no longer mkdirs/pre-creates for `:memory:` or
  `file:` URI inputs, which would have created a bogus literal directory
  (e.g. one named `file:`) beside the process CWD.
- Healer tool envelopes mask credential token shapes in exception-derived
  text (handler errors, GitHub API errors, CI-log warnings, `pr_skipped`
  git-flow detail, `/heal` failures) — e.g. a push error echoing a
  credentialed remote URL no longer reaches the model in the clear.
- The subprocess sandbox re-reads the `FLAKY_HEALER_SUBPROC_ISOLATE_NET`
  gate on every run (only the host-capability probe is cached), so flipping
  it in a long-lived gateway takes effect without a restart.
- Healer data-dir resolution now delegates to the unified hermes-home resolver
  (host helpers, then an *expanded* `$HERMES_HOME`, then `~/.hermes`). The raw
  env read it used meant a crontab/EnvironmentFile-style `HERMES_HOME=~/...`
  (no shell expansion) sent the healer's patches — and the `state.db` its
  audit/recipe store derives from the data dir — to a literal `./~...`
  directory while every other stage resolved the real home. `ctx.hermes_home`
  and `$FLAKY_HEALER_DATA_DIR` still take precedence, and the flat legacy
  import context keeps the historical env-only fallback.
- Sandbox spec selection: both sandbox backends passed the target spec after a
  `--` terminator, which Playwright's CLI silently drops from the positional
  filter — every "single test" reproduce/burn-in run actually executed the
  whole suite, so unrelated failures could poison heal verdicts. Found by the
  new docker CI job on its first run. The spec is now a plain positional and a
  flag-shaped test id is refused outright (same injection guard, working
  selection).
- Cleared the suppressed lint debt in source packages (`B904` exception
  chaining, one `B905` `zip(strict=)`, `E501` overlong lines) and dropped the
  stale `flaky_stabilization/*` per-file-ignores from `pyproject.toml`;
  `tests/*` ignores for ported test suites remain.

## [0.1.2] — 2026-07-20

### Changed

- Licensing clarified: MIT `LICENSE` file added and project metadata updated to
  match, with contributor guidance (`AGENTS.md`) updated alongside.

## [0.1.1] — 2026-07-12

Security and reliability update.

### Security

- Fixed an approval-gate bypass and closed redaction gaps; tightened at-rest
  permissions on local databases and data directories.
- Recorded the two deliberate residual trade-offs (no IP/personal-name PII
  detector; healer GitHub transport HTTPS-only without private-IP blocking) as
  decisions S1/S2 in `docs/DECISIONS.md`.

### Changed

- Large internal refactor with no contract changes: shared `common/` kernel
  (redaction, HTTP, time utilities, state schema), formalized inter-stage
  boundaries with an import guard, decomposed the pii/triage/healer/incidents
  stages, unified hermes-home resolution, and extracted the cron-job installer.
- All CLI errors now route to stderr; the pipeline carries a `RunContext`.

### Fixed

- All findings from an exhaustive codebase review, including the drifted
  jira-sync cron copy and a dead `create_incident` config parameter.

## [0.1.0] — 2026-07-09

First release.

### Added

- Unified Hermes Agent plugin absorbing seven legacy plugins (test-history,
  flaky-detective, ci-triage, flaky-healer, bug-report-improver, pii-scan,
  jira-incidents) as per-stage packages with a thin orchestrator.
- `stabilize_test_failure` pipeline tool (+ `/stabilize`): history lookup →
  flakiness verdict → triage → healer branch (suggest/PR) or bug branch
  (report → duplicate check → PII gate → optional Jira write).
- Consolidated `state.db` (versioned migrations) beside the frozen
  `history.db` schema-v1 contract; `migrate` CLI copies legacy data with the
  originals untouched.
- Net-new `jira_create_incident` write tool, shipped disabled
  (`jira.enable_write: false`), token-gated, approval-escalated, and PII-gated.
- Cron installer (`install-cron`), unified `config.json`, and the fail-closed
  PII gate over evidence files.

[Unreleased]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sergiparpal/flaky-stabilization/releases/tag/v0.1.0
