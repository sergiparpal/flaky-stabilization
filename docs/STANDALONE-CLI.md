# Standalone CLI and GitHub Action — design note

**Date:** 2026-08-02
**Status:** implemented

Why the flaky-detection half runs without Hermes Agent as a *second composition
root* rather than an extracted package, and why healing is not part of it.

## The shape of the problem

Detection is useful to people who do not run Hermes Agent: ingest JUnit XML,
detect flaky tests, answer "is this test a known flake?" in CI. None of it needs
a model. But the code lived behind a plugin registration that only a Hermes host
calls.

Three options were on the table:

1. **Extract** `history` + `detective` into a separate distributable package.
2. **Fork** the repository into a Hermes plugin and a standalone tool.
3. **Add a second composition root** to the existing package.

## Why the second composition root won

The layering that made this a small change already existed. `registration.py` is
the only module that touches Hermes, frozen by
[`tests/test_import_boundaries.py`](../tests/test_import_boundaries.py); stages
receive `llm` and paths as injected parameters and never import upward. And
`cli.setup_cli(parser)` was written to receive a parser from the host — it does
not care who supplies it.

So the standalone binary needed a sibling to `registration.py`, not a new
package:

```
registration.py   → composes the plugin onto a Hermes PluginContext
__main__.py       → composes the same stage CLIs onto a plain parser
```

Extraction and forking share one disqualifying consequence: **two schema ladders
writing one SQLite file.** `state.db` has a `SCHEMA_VERSION` and a migration
ladder, and `ensure_schema` refuses a database newer than the code opening it.
Two packages evolving separately would eventually ship two different notions of
version 2, and an operator running both would find a database one of them
refuses to open — with no way back. Splitting also duplicates the test suite,
the path resolver, and the security posture (the PII gate, the read-only
`mode=ro` URI rule, the 0700/0600 hardening) at exactly the points where
divergence is silent.

One repository, one package, one `state.db`, one schema ladder, one test suite.
The cost is that two roots now drive that ladder, which is a real risk —
[`tests/test_composition_roots.py`](../tests/test_composition_roots.py) exists
solely to hold them together: same recorded version in either order, same
database paths, and Hermes registration still working with `FLAKY_STAB_HOME`
set.

## The rule that keeps it honest

`registration.py` is the only place Hermes surfaces may be imported.
`__main__.py` carries the mirror image: it must never import a Hermes surface at
all, not even inside a `try/except ImportError`. The architecture guard asserts
it statically. Without that rule the "standalone" binary would slowly acquire an
optional dependency on the thing it exists to work without.

## Path resolution

`$FLAKY_STAB_HOME` heads the chain in `paths.get_hermes_home()`, ahead of the
two Hermes lookups. An env var the operator set explicitly must beat ambient
discovery of whatever agent happens to be installed on the box — a CI job
pointing at `.flaky/` in the workspace should not be overruled by a Hermes
profile that happens to exist on the runner. With the variable unset, the four
pre-existing branches resolve exactly as before; that is the single
highest-risk regression in this change, so each branch has its own test.

The variable is namespaced to the binary rather than reusing `HERMES_HOME` so
the two can point at different profiles on one machine.

## Why healing stays Hermes-only

`heal_flaky_test` and `stabilize_test_failure` are deliberately not exposed
through the CLI or the Action. They need `ctx.llm`, and PR mode is gated by a
`pre_tool_call` approval hook. Opening pull requests from CI with a model and no
approval loop is precisely what that gate protects against; replacing it with
GitHub `environment:` reviewer rules would be a *new security model*, not a
port. If it is ever wanted, it belongs in its own change with its own review.

The core's standard-library-only property exists because Hermes supplies the
model. Adding a provider dependency to reach the healing half would forfeit
that for every user of the plugin, including the ones who never wanted it.

## CI state persistence

The Action stores its databases in a directory the workflow caches with
`actions/cache`. The detection window is 14 days
([`detective/domain.py`](../flaky_stabilization/detective/domain.py)) and GitHub
evicts a cache only after 7 days without a hit, so an active repository never
loses it, and durability beyond the window buys nothing the detector reads. A
pull-request branch can restore a cache written on `main` but not the reverse,
which is why the nightly job on `main` is the writer and pull-request jobs are
readers.

Long-horizon trend reporting (exporting verdicts for months of flakiness
evolution) is deliberately out of scope: that is reporting, not detection, and
the 14-day window makes it unnecessary for the core use case. If it is wanted
later it is an additive `flaky-stab export` plus a separate workflow, not a
change to the persistence chosen here.
