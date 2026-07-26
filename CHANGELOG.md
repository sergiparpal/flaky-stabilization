# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Deprecated

- The legacy env overrides (`FLAKY_HEALER_*`, `HERMES_CI_TRIAGE_*`,
  `JIRA_BASE_URL`/`JIRA_EMAIL`, `HERMES_JIRA_STRICT_REDACTION`) and the legacy
  `test-history/config.json` precedence level: each use now logs one
  deprecation warning per process (logger `flaky_stabilization.deprecation`);
  all of them will be removed at 1.0. Move settings into
  `flaky-stabilization/config.json`. The `migrate` command is exempt — it is
  supposed to read legacy data.

### Fixed

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

[Unreleased]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/sergiparpal/flaky-stabilization/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/sergiparpal/flaky-stabilization/releases/tag/v0.1.0
