# Decisions

## Phase 0 checkpoint (2026-07-09, answered by sergiparpal)

| # | Question | Answer |
|---|---|---|
| Q1 | Plugin name stays `flaky-stabilization`? | **Yes** — manifest name, package `flaky_stabilization`, data dir `<hermes_home>/flaky-stabilization/`, CLI `flaky-stab`. *(Originally answered as `hermes-flaky-stabilization`; renamed on 2026-07-26 to follow the repository rename — see the note below.)* |
| Q2 | Build net-new `jira_create_incident` write tool? | **Yes, build it, ship disabled** — `jira.enable_write: false` default, `check_fn` hides it without `JIRA_API_TOKEN`, approval-escalated via `pre_tool_call`, PII-gated (D6/D7). |
| Q3 | License MIT? | **Yes** — keep the project metadata consistent with the repo's MIT LICENSE. |
| Q4 | Keep `test-history` CLI alias? | **Yes** — `hermes test-history ingest|status|prune|rebuild-fts|config` unchanged (D2). |

Preflight: `scripts/preflight.sh` passed on 2026-07-09 (7 sibling repos, hermes-agent 0.18.2 at
`/home/sergi/hermes-agent`, Python 3.12.3, pytest 7.4.4, sqlite FTS5 available).

## Resolved design decisions (D1–D10)

Imported verbatim from PLAN.md §5 when the plan was deleted after full implementation
(2026-07-09). Cross-references like §2, §3.x, §10, §11 and Appendices A/B point into the
deleted plan — recover it with `git show 950c72b:PLAN.md`.

**D1 — Category/kind and the tracker stage.** The unified plugin is **`kind: standalone`** (the
real manifest field; the proposal's `category: general` does not exist). `jira-incidents` is folded
in as three plain tools (toolset `jira_incidents`) registered with `ctx.register_tool`, dropping
the `MemoryProvider` class entirely (a standalone plugin cannot register one — §2 row 16). PII
protection is preserved and strengthened: (a) the `redaction.py` module is ported verbatim and
every incident-facing tool response keeps its existing redact-then-emit path; (b) the lost
automatic `prefetch()` is replaced by a **`pre_llm_call` hook** (config-gated,
`incidents.context_injection`, default on) that runs a local-FTS-only, timeout-bounded (1.5 s),
redacted lookup of the user message and returns `{"context": ...}` — never a network call on the
turn path; (c) background Jira sync keeps the ported `SyncScheduler` (coalesced, debounced),
triggered non-blockingly from `on_session_start` and before incident tool reads, plus the optional
cron (D10). Net effect: the exclusive `memory.provider` slot is freed for other providers.

**D2 — `test-history` stays a keystone, absorbed with a frozen contract.** Absorb the code (one
repo to maintain) but freeze its public contract: identical tool names, schemas, toolset
(`test_history`), return shapes, error envelopes; identical DB path
**`<hermes_home>/test-history/history.db`** with `schema_version` pinned at 1 (external plugins —
`flaky-detective` itself proves it, plus release-readiness/regression-selector/api-fuzzer/
coverage-history per the proposal — consume both the tools *and* the raw DB file); and the
`test-history` CLI command name kept as an alias (CI scripts run `hermes test-history ingest`).
Alternative rejected: keeping `hermes-test-history` as an external dependency would leave a
two-plugin install, cross-repo version skew, and no way to fix the enrichment bug internally.

**D3 — Internal architecture: per-stage packages + a thin orchestrator.** One importable package
`flaky_stabilization` (with the proven root-shim `__init__.py` pattern from
bug-report-improver so the hyphenated plugin dir loads under Hermes and plain pytest alike).
Stage subpackages preserve each plugin's ports-and-adapters shape — **only the top-level
`__init__.py` and `registration.py` may import Hermes**; stages receive `llm`, `dispatch_tool`,
`hermes_home` as injected parameters (ci-triage's discipline, generalized). The orchestrator is
plain Python control flow (the fork and both feedback loops), calling stage functions directly —
no internal `dispatch_tool` except for host git/PR tools, which must keep going through the
approval pipeline.

**D4 — Two databases, not one and not six.** (1) `history.db` untouched at its current path — it
is a public, file-level data contract (D2). (2) A new consolidated
**`<hermes_home>/flaky-stabilization/state.db`** for all private state: detective verdicts + scan
runs, triage patterns (+FTS), healer runs/recipes/audit, incidents (+FTS)/links/meta, and a new
`pipeline_runs` ledger — full DDL in Appendix A, with healer-style versioned migrations
(`SCHEMA_VERSION`, ladder of idempotent steps). Migration of existing data is a CLI command that
**copies** from the four legacy DBs (originals untouched → trivial rollback), §10.

**D5 — Consolidated configuration.** `plugin.yaml` (Appendix B) declares `kind: standalone` and
**no `requires_env`** — both credentials are optional per-stage (`GITHUB_TOKEN` gates
`fetch_ci_logs` via `check_fn`; `JIRA_API_TOKEN` gates Jira sync/tools via `check_fn`), so a
missing token must not disable the whole plugin (§3.2). One JSON config file
`<hermes_home>/flaky-stabilization/config.json` with per-stage sections (defaults in Appendix B):
`history`, `detective` (window/min_fails/include_errors/schedule/deliver), `triage`, `healer`,
`pii`, `jira` (base_url/email/auth_mode/jql/field mapping/retention/**enable_write: false**),
`incidents` (context_injection, prefetch limits), `pipeline` (approvals + gate policy). Legacy
env vars (`FLAKY_HEALER_*`, `HERMES_CI_TRIAGE_*`, `JIRA_*`, `HERMES_JIRA_STRICT_REDACTION`) keep
working as overrides for compatibility; the config file is the documented home.

**D6 — Approvals and safety policy.** Four layers, all mechanically enforced:
1. **Sandbox-only modification**: the healer's copy-then-patch model is kept verbatim (Docker
   preferred; subprocess fallback keeps its PR-refusal guard).
2. **PR-only git flow through host approvals**: git/PR steps remain `ctx.dispatch_tool` calls to
   `terminal`/`create_pull_request` (configurable), so the host approval/redaction/budget pipeline
   applies; direct pushes to default branches stay structurally impossible.
3. **Plugin-side approval escalation** (net-new, the *correct* Hermes mechanism per §3.5): a
   `pre_tool_call` hook that returns `{"action":"approve", "message":..., "rule_key":...}` for
   `heal_flaky_test` with `mode="pr"` and for `jira_create_incident` (fail-closed). The healer's
   existing `pre_approval_request`/`post_approval_response` **audit observers** are kept — and the
   plan explicitly corrects the proposal: those hooks cannot gate anything.
4. **PII gate before any external output**: `jira_create_incident` (D7) refuses unless
   (a) every referenced evidence path passed `validate_no_pii` with `clean && complete` within the
   same call, and (b) all outbound text fields have passed `redaction.redact_text`. The
   orchestrator enforces the same gate on its bug branch. The gate is in the handler (deterministic),
   not in a hook (advisory).

**D7 — Tracker write-back is net-new and off by default.** No legacy code pushes to Jira (§2
row 9). The pipeline still needs an endpoint, so the plan adds a minimal, clearly-labeled-new tool
**`jira_create_incident`** (POST `/rest/api/{v}/issue`, reusing the hardened `jira_client`
transport), config-gated by `jira.enable_write: false`, `check_fn`-hidden without
`JIRA_API_TOKEN`, approval-escalated and PII-gated per D6. When disabled, the pipeline ends by
returning the structured, redacted ticket body for the user to file manually.

**D8 — The seven repos are deprecated and their code absorbed.** Code and tests are copied into
this repo (monorepo per stage, D3). Rationale: the tight dataflow coupling and the enrichment-bug
class of cross-repo contract drift are exactly what unification removes; coexistence is impossible
anyway because tool names would collide in the registry (§3.3). Each legacy repo gets a final
release + archived status + README pointer (§11). License: the unified repo is **MIT**, matching
the repository's LICENSE file and package metadata.

**D9 — The orchestrator implements the fork and both feedback loops (all net-new).** New tool
**`stabilize_test_failure`** (+ `/stabilize` slash command): ingest-or-locate failure evidence →
`is_flaky` + history lookups → `triage_pipeline_failure` (with **fixed** internal enrichment and
net-new incident-context enrichment: a redacted local-FTS lookup of the incidents index seeds the
triage prompt's prior-hint block — this implements the proposal's "jira → ci-triage feedback" for
real) → **fork** on category: `flaky`/`timeout` → healer branch (suggest or pr per policy); any
other category → bug branch: `improve_bug_report` → **`find_duplicate_incidents`** (net-new tool:
local FTS + trigram-ish token overlap against the incidents index — the honest version of the
proposal's fabricated `search_possible_duplicates`; no tracker round-trip needed) → PII gate →
`jira_create_incident` if enabled, else return the ticket body. **Loop closure**: after a stable
heal, the orchestrator writes the burn-in outcome back into `history.db` as a synthetic
`test_runs`/`test_cases` row set (source `flaky-healer-burnin`), so the next detective scan sees
the recovery — implementing the proposal's "fix feeds back into test-history" for real. Every
pipeline run is recorded in `pipeline_runs` (Appendix A).

**D10 — Cron follows the proven imperative pattern.** `hermes flaky-stab install-cron
[--schedule "0 9 * * *"] [--deliver ...] [--no-create]`: persists schedule to config, installs a
shim `flaky-stab-scan.sh` into `$HERMES_HOME/scripts/`, and shells
`hermes cron create <schedule> --no-agent --script flaky-stab-scan.sh --deliver <d> --name
flaky-stabilization` (printing the copy-paste command when the CLI/gateway is unavailable). The
shim runs `hermes flaky-stab scan --format cron` (detective sweep; changes-only output; empty
stdout = silent tick). Optionally the same subcommand can install a second job for Jira sync
(`--with-jira-sync`, runs `hermes flaky-stab jira sync --quiet`).

## Security-audit follow-up decisions (2026-07-10)

Resolving [issue #1] — two judgment calls deferred from the audit hardening in
`ec1bd84`. Both are resolved **option (a): leave as-is**. Recorded here so the choice reads as a
conscious trade-off rather than an oversight in a future review.

**S1 — No IP-address (or personal-name) detector in the PII gate.** The gate (`pii/detectors.py`,
run over evidence files by `orchestrator/gate.py`) intentionally does not flag IP addresses.
Rationale: CI evidence is dense with service/loopback IPs, so an IP detector would fail the gate on
most legitimate evidence and push operators to disable it (`require_pii_gate: false`) — a net loss
of protection. The actual disclosure surface is the **outbound** ticket, and the outbound redactor
(`pii/redaction.py`) masks IPv4 and IPv6 (added in `ec1bd84`). Personal-name detection in free text
needs an NLP model and is out of scope; structured name fields are still redacted wholesale, and
labelled names in prose are caught by the redactor. These deliberate blind spots are documented in
the `pii/detectors.py` module docstring so a `clean` verdict is not read as "no personal data
whatsoever." Revisit only if a concrete need appears; the preferred stricter option would be
public-IP-only detection (reusing `triage/safehttp.py`'s range logic), never a blanket IP match.

**S2 — Healer GitHub API transport is HTTPS-only, without private-IP SSRF blocking.** `ec1bd84`
made the healer's GitHub API base HTTPS-only (matching `jira_client`), which closes the material
risk — the bearer token traveling in cleartext over a plain-`http` base. The transport
(`healer/flaky_healer/ci/base.py`) is deliberately **not** given `safehttp.py`'s private-IP
blocklist: GitHub **Enterprise** legitimately resolves to internal/RFC1918 addresses, so blocking
private ranges would break every GHE deployment — the primary reason a team sets
`healer.github_api` (formerly `FLAKY_HEALER_GITHUB_API`). The base is operator-config (a config
key or env var), not remote-attacker input, so the residual SSRF risk is low. If defense-in-depth
here is ever wanted, the correct form is routing
through `safehttp`'s guarded opener with RFC1918 **allowed** (keeping the loopback/link-local/
cloud-metadata blocks and connect-time IP pinning), so GHE keeps working while
`169.254.169.254`/loopback targets are still refused.

## Healer-scope decision (2026-07-26, answered by sergiparpal)

**H1 — The healer is the plugin's differentiator; it is core and will grow.** The question:
the healer is ~4.9k LOC (about a third of the codebase), needs Docker to be at its best, and most
of its value ships in the default `suggest` mode — commit to it, freeze it, or defer? Answer:
**commit as differentiator**. Consequences: the docker-marked hardening tests added alongside this
decision (network egress blocked inside the container, the original tree untouched after a
sandboxed run, docker infra exit codes 125/126/127 surfacing as `SandboxError`) are the start of
CI coverage that grows with the healer, the `docker-tests` CI job keeps the real-daemon path
exercised on every push, and healer scope (new strategies, recipes, sandbox backends) may grow
without reopening this decision. Alternatives rejected: freezing scope (maintenance-only) and
deferring the question.

Related: the same improvement branch added one-shot deprecation warnings for the legacy
env/config fallbacks (`FLAKY_HEALER_*`, `HERMES_CI_TRIAGE_*`, `JIRA_BASE_URL`/`JIRA_EMAIL`,
`HERMES_JIRA_STRICT_REDACTION`, and the legacy `test-history/config.json` precedence level).
Removal at 1.0 is pre-approved: README.md and MIGRATION.md document them as "deprecated, removed
at 1.0", one row per key.

**C1 — every deprecation warning must name a config key that exists (2026-07-26).** The question:
five warned variables (`FLAKY_HEALER_GITHUB_API`, `FLAKY_HEALER_SUBPROC_PASS_ENV`,
`FLAKY_HEALER_SUBPROC_ISOLATE_NET`, `FLAKY_HEALER_RUN_CONCURRENCY`, and
`HERMES_JIRA_STRICT_REDACTION`) told the operator to "move it into
`flaky-stabilization/config.json`", but their accessors read the environment only and no matching
key existed — the advice could not be followed, and a 1.0 removal would have deleted the only way
to set the behavior. Answer: **add the five keys** (`healer.github_api`,
`healer.subproc_pass_env`, `healer.subproc_isolate_net`, `healer.run_concurrency`,
`jira.strict_redaction`) rather than reword the warnings to "env-only, no replacement planned".

Rationale: env-only settings are invisible to `flaky-stab status`, which summarizes
`load_config()` only — and two of the five are security-relevant (`subproc_isolate_net` disables
the subprocess sandbox's network isolation; `strict_redaction` toggles the egress PII canary), so
they are exactly the settings that must be inspectable and version-controlled. A permanent
env-only tier would also reopen the split this plugin exists to close, and it was already
inconsistent in practice: a GitHub Enterprise operator configured the triage side via
`triage.token_hosts`/`allow_private` in the file but the healer side via an env var. The change is
purely additive — env still wins over the file, so existing installs behave identically.

`subproc_pass_env` defaults to `null`, not `[]`, for the same reason as `triage.log_roots` and
`triage.token_hosts`: a typed list default makes `_coerce` degrade a comma-separated *string* back
to the default, and accepting both shapes lets an operator paste the value straight out of the env
var it replaces.

Carve-out: `FLAKY_HEALER_DATA_DIR` stays environment-only by design — it selects the directory
that *contains* `config.json`, so it cannot be configured from inside it. Its warning already
points at the default data directory rather than a config key, so it was never part of this
defect. Consequence: the invariant is now enforced by
`tests/common/test_deprecation.py::test_every_config_key_named_in_a_deprecation_message_exists`,
which scans the package for deprecation replacement strings and fails if any names a key absent
from `DEFAULTS`. Alternatives rejected: rewording the warnings (cheaper now, paid forever in a
split config surface and a permanent docs carve-out), and deferring to 1.0 (would ship a knowingly
wrong message).

## Repository rename (2026-07-26)

The GitHub repository was renamed `hermes-flaky-stabilization` → `flaky-stabilization`, and every
identifier that carried the old name was renamed with it, so the four names that used to diverge now
agree: GitHub slug `sergiparpal/flaky-stabilization`, plugin manifest name `flaky-stabilization`,
distribution name `flaky-stabilization`, and import package `flaky_stabilization`. The data
directory (`<hermes_home>/flaky-stabilization/`) and the `flaky-stab` CLI were already unprefixed
and are unchanged.

This is **breaking for existing installs** — the plugin id in `plugins.enabled`, the plugin
directory under `~/.hermes/plugins/`, the qualified skill name (`hermes-flaky-stabilization:
flaky-healer` → `flaky-stabilization:flaky-healer`), the `pip install` name, and the import path all
changed. Reinstall and re-enable under the new name; no data migration is needed.
