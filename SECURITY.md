# Security Policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/sergiparpal/flaky-stabilization/security/advisories/new)
rather than opening a public issue.

Expect an initial response within 7 days. If a report is confirmed, the fix and the advisory
are published together.

## Supported versions

Only the latest release on `main` receives security fixes.

## Scope

This repository is a Hermes plugin that diagnoses and stabilizes flaky tests. Unlike a purely
analytical tool, it **executes the target project's test suite**, including inside a Docker
sandbox — so the execution boundary is the main thing worth attention.

The parts most worth scrutiny are therefore:

- **Sandbox containment** — anything that lets test execution escape the Docker sandbox, or
  reach host resources the sandbox was meant to withhold.
- **Command construction** — any path where repository-supplied content (test names, config,
  paths) reaches a shell without proper quoting.
- **The helper scripts** in `scripts/`, in particular the Jira sync, which talks to an external
  system and therefore handles a credential.
- **Dependency supply chain** — the runtime and `dev` extras, plus the npm fixture under
  `tests/healer/fixtures/`.

Out of scope: whether a test correctly identified as flaky is genuinely flaky, and the
correctness of any suggested stabilization.
