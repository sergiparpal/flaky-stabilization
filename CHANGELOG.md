# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- GitHub Actions CI: ruff lint, the offline test suite on Python 3.11/3.12/3.13
  with coverage enforced at 85%, and a wheel build with an archive integrity
  check. E2E-marked tests are excluded from the offline CI runs.

### Changed

- Repository renamed `hermes-flaky-stabilization` → `flaky-stabilization`; the
  GitHub slug, plugin manifest name, distribution name, and import package now
  all agree. Breaking for existing installs — reinstall and re-enable under the
  new name (no data migration needed; see `docs/DECISIONS.md`).

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
