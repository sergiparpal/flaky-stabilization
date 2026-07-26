"""Environment/config resolution and defaults (stdlib only).

Resolution order for every key that exists in the unified config's ``healer``
section (plan D5): ``FLAKY_HEALER_*`` env var > ``config.json`` ``healer``
section > built-in default. When the unified package is unavailable (flat
legacy import context) or the file is missing/unreadable, behavior degrades
to the historical env-only resolution.
"""

from __future__ import annotations

import contextlib
import contextvars
import os
from pathlib import Path

PLUGIN_NAME = "hermes-flaky-healer"

# The profile home to resolve config.json from, scoped to the current heal run.
# Bound at the handler boundary (where ``ctx`` is known) so the config section
# is read from the SAME home as ``data_dir(ctx)`` — see :func:`bind_run`. Unset
# (the default) means "resolve the home the ctx-free way" (env/host helpers),
# which preserves the historical behavior for CLI and direct/test callers.
# A ``ContextVar`` (not a module global) so concurrent heals in one async pool
# cannot clobber each other's home.
_run_home: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "flaky_healer_run_home", default=None
)
# Per-run memo of the loaded ``healer`` section, so building one PR does not
# re-read config.json three times. ``None`` outside a bound run ⇒ no caching
# (each call reads fresh, which is what tests that repoint HERMES_HOME rely on).
_run_cfg_cache: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "flaky_healer_run_cfg_cache", default=None
)


@contextlib.contextmanager
def bind_run(ctx=None):
    """Scope healer config resolution to *ctx*'s profile home for one run.

    Fixes the home divergence where ``data_dir(ctx)`` honored
    ``ctx.hermes_home`` but ``_unified_healer_config()`` read the ``healer``
    section from the ctx-free ``paths.get_hermes_home()`` — so under a profile
    whose ``ctx.hermes_home`` differed from the host default, patches/state
    landed under one home while ``sandbox``/``burnin``/``base_branch``/
    ``allow_subprocess_pr`` were read from another. Binding the same home the
    data dir uses keeps policy and storage on one profile. Also installs a
    fresh per-run config memo. A falsy ``ctx.hermes_home`` binds ``None`` (the
    ctx-free fallback), so behavior is unchanged when the two already agree.
    """
    home = getattr(ctx, "hermes_home", None) if ctx is not None else None
    home_token = _run_home.set(Path(home) if home else None)
    cache_token = _run_cfg_cache.set({})
    try:
        yield
    finally:
        _run_cfg_cache.reset(cache_token)
        _run_home.reset(home_token)

ENV_DATA_DIR = "FLAKY_HEALER_DATA_DIR"
ENV_BURNIN = "FLAKY_HEALER_BURNIN"
ENV_SANDBOX = "FLAKY_HEALER_SANDBOX"
ENV_DOCKER_IMAGE = "FLAKY_HEALER_DOCKER_IMAGE"
ENV_GIT_TOOL = "FLAKY_HEALER_GIT_TOOL"
ENV_PR_TOOL = "FLAKY_HEALER_PR_TOOL"
ENV_BASE_BRANCH = "FLAKY_HEALER_BASE_BRANCH"
ENV_SUBPROC_PASS = "FLAKY_HEALER_SUBPROC_PASS_ENV"
ENV_GITHUB_API = "FLAKY_HEALER_GITHUB_API"
ENV_ALLOW_SUBPROC_PR = "FLAKY_HEALER_ALLOW_SUBPROCESS_PR"
ENV_SUBPROC_ISOLATE_NET = "FLAKY_HEALER_SUBPROC_ISOLATE_NET"
ENV_RUN_CONCURRENCY = "FLAKY_HEALER_RUN_CONCURRENCY"

# Pinned to the @playwright/test version in fixtures/toy-app/package.json so the
# image's bundled browsers match the project's playwright-core revision, AND by
# content digest so a moved/compromised tag cannot swap the image under us
# (the tag stays in the ref for readability; the @sha256 is what docker verifies).
# Override with FLAKY_HEALER_DOCKER_IMAGE to pin a different digest.
DEFAULT_DOCKER_IMAGE = (
    "mcr.microsoft.com/playwright:v1.61.0-noble"
    "@sha256:57b65fdc9ceabe0ef613124c7bbe2babcf9362c4d85e382fe3b03604e84b428a"
)
DEFAULT_BURNIN = (5, 10)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _warn_legacy_env(var: str, replacement: str) -> None:
    """One-shot deprecation warning for a ``FLAKY_HEALER_*`` env override.

    Imported lazily (like the unified config below) so the flat legacy import
    context — where the package is unavailable — keeps working silently.
    """
    try:
        from flaky_stabilization.common.deprecation import warn_env_once
    except Exception:  # pragma: no cover — flat import context without the package
        return
    warn_env_once(var, replacement)


def _unified_healer_config() -> dict:
    """The ``healer`` section of the unified flaky-stabilization config.json.

    The unified package resolves ``<hermes_home>`` itself via its ``paths``
    helper (registration binds ``ctx.hermes_home`` to the same profile; this
    module stays ctx-free by design). Imported lazily to avoid a circular
    import at package init, and every failure — package not importable,
    unreadable/absent file — degrades to ``{}`` so env-only behavior is
    preserved exactly when the section is unset.
    """
    cache = _run_cfg_cache.get()
    if cache is not None and "section" in cache:
        return cache["section"]
    try:
        from flaky_stabilization import config as unified_config
        from flaky_stabilization import paths as unified_paths
    except Exception:  # pragma: no cover — flat import context without the package
        return {}
    try:
        # The run-scoped home (bound from ctx.hermes_home) when set, else the
        # ctx-free host resolution — the same home data_dir(ctx) resolves, so
        # config and storage stay on one profile. get_hermes_home()/<dir>
        # rather than get_data_dir(): reading config must not create the data
        # directory as a side effect.
        home = _run_home.get() or unified_paths.get_hermes_home()
        section_dir = Path(home) / unified_paths.DATA_DIR_NAME
        section = unified_config.load_config(section_dir).get("healer")
    except Exception:  # noqa: BLE001 — config lookup must never break the healer
        return {}
    result = section if isinstance(section, dict) else {}
    if cache is not None:
        cache["section"] = result
    return result


def _cfg_str(key: str) -> str | None:
    """A non-empty string value for *key* from the unified section, else None."""
    value = _unified_healer_config().get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _cfg_bool(key: str) -> bool | None:
    """A bool value for *key* from the unified section, else None.

    Strict: only a real JSON bool counts, mirroring :func:`_coerce` in the
    unified config (``"true"``/``1`` must not silently flip a security-relevant
    key). ``None`` means "unset" so callers can distinguish it from ``False``.
    """
    value = _unified_healer_config().get(key)
    return value if isinstance(value, bool) else None


def _cfg_int(key: str) -> int | None:
    """An int value for *key* from the unified section, else None (bools excluded)."""
    value = _unified_healer_config().get(key)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) else None


def _cfg_str_list(key: str) -> list[str]:
    """A list of non-empty strings for *key*: a JSON list or a comma string.

    Accepting both shapes matches the triage side's ``_env_or_config_list``, so
    an operator moving ``FOO=a,b`` out of the environment can paste the same
    string into the config file instead of having to learn a list syntax.
    """
    value = _unified_healer_config().get(key)
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _resolve_hermes_home_root() -> Path:
    """The ctx-free Hermes home, via the unified resolver when importable.

    ``paths.get_hermes_home()`` is the ONE resolver (host helpers, then an
    expanded ``$HERMES_HOME``, then ``~/.hermes``). Reading the env var raw here
    used to skip its ``expanduser``/``expandvars`` step, so a crontab-style
    ``HERMES_HOME=~/hermes-data`` sent the healer's data dir — and the
    ``state.db`` ``_store_for`` derives from it — to a literal ``./~`` directory
    while every other stage resolved the real home. The flat legacy import
    context (no unified package) keeps the historical raw-env fallback.
    """
    try:
        from flaky_stabilization import paths as unified_paths
    except Exception:  # pragma: no cover — flat import context without the package
        hermes_home = os.environ.get("HERMES_HOME")
        return Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return unified_paths.get_hermes_home()


def data_dir(ctx=None) -> Path:
    """Profile-aware persistent data dir, created on demand.

    Unified-plugin adaptation (plan Phase 4 / §10): the default moved from
    ``plugins-data/hermes-flaky-healer/`` to the unified
    ``<hermes_home>/flaky-stabilization/`` directory (patches under
    ``patches/``, the ``learned-patterns.md`` mirror beside them). The
    ``$FLAKY_HEALER_DATA_DIR`` override keeps working and preserves any legacy
    location. Resolution order: $FLAKY_HEALER_DATA_DIR → ctx.hermes_home →
    the unified home resolver (host helpers / expanded $HERMES_HOME /
    ~/.hermes), then ``/flaky-stabilization``.
    """
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        _warn_legacy_env(
            ENV_DATA_DIR,
            "the default <hermes_home>/flaky-stabilization data directory")
        base = Path(env)
    else:
        hermes_home = getattr(ctx, "hermes_home", None)
        root = Path(hermes_home) if hermes_home else _resolve_hermes_home_root()
        base = root / "flaky-stabilization"
    base.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def _parse_burnin(raw: str) -> tuple[int, int] | None:
    try:
        m, n = raw.split(":")
        return max(1, int(m)), max(1, int(n))
    except ValueError:
        return None


def burnin() -> tuple[int, int]:
    """(reproduce_runs M, burn_in_runs N); FLAKY_HEALER_BURNIN='5:10' > config."""
    raw = os.environ.get(ENV_BURNIN, "")
    if raw:
        parsed = _parse_burnin(raw)
        if parsed is not None:
            _warn_legacy_env(
                ENV_BURNIN, "the `healer.burnin` key in flaky-stabilization/config.json")
            return parsed
    raw = _cfg_str("burnin") or ""
    if raw:
        parsed = _parse_burnin(raw)
        if parsed is not None:
            return parsed
    return DEFAULT_BURNIN


def github_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or None


def github_api_base() -> str:
    """GitHub API base for the CI-log client (GitHub Enterprise override).

    The env branch keeps its historical ``is not None`` test — an explicitly
    empty ``FLAKY_HEALER_GITHUB_API=`` has always meant "empty base", and that
    is not this change's call to make. An empty *config* value means unset.
    """
    env = os.environ.get(ENV_GITHUB_API)
    if env is not None:
        _warn_legacy_env(
            ENV_GITHUB_API, "the `healer.github_api` key in flaky-stabilization/config.json")
        return env.rstrip("/")
    return (_cfg_str("github_api") or "https://api.github.com").rstrip("/")


def sandbox_backend() -> str:
    """'auto' | 'docker' | 'subprocess'."""
    env = (os.environ.get(ENV_SANDBOX) or "").strip().lower()
    if env:
        _warn_legacy_env(
            ENV_SANDBOX, "the `healer.sandbox` key in flaky-stabilization/config.json")
        return env
    return (_cfg_str("sandbox") or "auto").lower()


def docker_image() -> str:
    env = os.environ.get(ENV_DOCKER_IMAGE)
    if env:
        _warn_legacy_env(
            ENV_DOCKER_IMAGE, "the `healer.docker_image` key in flaky-stabilization/config.json")
        return env
    return _cfg_str("docker_image") or DEFAULT_DOCKER_IMAGE


def git_tool() -> str:
    """Host tool name used to run git commands through the approval pipeline."""
    env = os.environ.get(ENV_GIT_TOOL)
    if env:
        _warn_legacy_env(
            ENV_GIT_TOOL, "the `healer.git_tool` key in flaky-stabilization/config.json")
        return env
    return _cfg_str("git_tool") or "terminal"


def pr_tool() -> str:
    """Host tool name used to open a pull request through the approval pipeline."""
    env = os.environ.get(ENV_PR_TOOL)
    if env:
        _warn_legacy_env(
            ENV_PR_TOOL, "the `healer.pr_tool` key in flaky-stabilization/config.json")
        return env
    return _cfg_str("pr_tool") or "create_pull_request"


def base_branch() -> str:
    env = os.environ.get(ENV_BASE_BRANCH)
    if env:
        _warn_legacy_env(
            ENV_BASE_BRANCH, "the `healer.base_branch` key in flaky-stabilization/config.json")
        return env
    return _cfg_str("base_branch") or "main"


def subproc_pass_env() -> list[str]:
    """Extra env var names the subprocess sandbox may copy from the host env.

    The subprocess backend builds its environment from scratch; this explicit
    allowlist exists for hosts where browsers need e.g. LD_LIBRARY_PATH.
    """
    raw = os.environ.get(ENV_SUBPROC_PASS, "")
    names = [v.strip() for v in raw.split(",") if v.strip()]
    if names:
        _warn_legacy_env(
            ENV_SUBPROC_PASS,
            "the `healer.subproc_pass_env` key in flaky-stabilization/config.json")
        return names
    return _cfg_str_list("subproc_pass_env")


def allow_subprocess_pr() -> bool:
    """Whether mode='pr' may proceed on a result validated in the WEAKER
    subprocess sandbox. Off by default: a subprocess heal ran (and any injected
    patch executed) on the host with no container isolation, so it must not be
    auto-promoted to a PR unless the operator explicitly opts in.
    """
    raw = os.environ.get(ENV_ALLOW_SUBPROC_PR)
    if raw is not None and raw.strip():
        _warn_legacy_env(
            ENV_ALLOW_SUBPROC_PR,
            "the `healer.allow_subprocess_pr` key in flaky-stabilization/config.json")
        return _truthy(raw)
    value = _cfg_bool("allow_subprocess_pr")
    return value if value is not None else False


def subproc_isolate_net() -> bool:
    """Whether the subprocess sandbox should drop external network via an
    unprivileged user+network namespace (loopback stays up for the Playwright
    webServer). Best-effort and probe-gated; on by default, set to 0/false to
    disable on hosts whose tests legitimately reach external services.
    """
    raw = os.environ.get(ENV_SUBPROC_ISOLATE_NET)
    if raw is not None:
        _warn_legacy_env(
            ENV_SUBPROC_ISOLATE_NET,
            "the `healer.subproc_isolate_net` key in flaky-stabilization/config.json")
        return _truthy(raw)
    value = _cfg_bool("subproc_isolate_net")
    return value if value is not None else True


def run_concurrency() -> int:
    """Max test runs to execute in parallel within one reproduce/burn-in phase.

    Default 1 (fully sequential — today's behavior). >1 runs the repeats
    concurrently, each in its OWN isolated project copy. Trade-offs the operator
    must size for:

    * Docker: every container still reserves ``--memory=2g``/``--cpus=2``, so N
      parallel runs need N×2 GiB RAM and 2N CPUs.
    * Subprocess: parallel runs rely on per-run network-namespace isolation
      (``healer.subproc_isolate_net``) for a private loopback; with net
      isolation off/unavailable they would collide on the webServer port, so
      keep this at 1 there.
    """
    raw = os.environ.get(ENV_RUN_CONCURRENCY)
    if raw is None:
        value = _cfg_int("run_concurrency")
        return max(1, value) if value is not None else 1
    _warn_legacy_env(
        ENV_RUN_CONCURRENCY,
        "the `healer.run_concurrency` key in flaky-stabilization/config.json")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1
