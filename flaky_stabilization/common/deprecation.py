"""One-shot deprecation warnings for the legacy compatibility surface.

The legacy env vars (``FLAKY_HEALER_*``, ``HERMES_CI_TRIAGE_*``,
``JIRA_BASE_URL``/``JIRA_EMAIL``, ``HERMES_JIRA_STRICT_REDACTION``) and the
legacy ``test-history/config.json`` precedence level keep working until 1.0,
but every use should tell the operator once — via logging only, never in tool
results or model-facing output.

Message convention::

    DEPRECATED: <legacy thing> is a legacy fallback removed in 1.0; use <replacement>.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("flaky_stabilization.deprecation")

# Keys already warned about in this process. Guarded by _lock so concurrent
# tool handlers cannot double-fire the same warning.
_seen: set[str] = set()
_lock = threading.Lock()


def warn_once(key: str, message: str) -> None:
    """Log *message* at WARNING the first time *key* is seen in this process.

    *key* must be stable per legacy name (e.g. ``env:FLAKY_HEALER_SANDBOX``,
    ``config:test-history/config.json``) so each legacy fallback fires at most
    once no matter how many call sites consult it.
    """
    with _lock:
        if key in _seen:
            return
        _seen.add(key)
    logger.warning(message)


def warn_env_once(var: str, replacement: str) -> None:
    """:func:`warn_once` for a legacy environment variable named *var*."""
    warn_once(
        f"env:{var}",
        f"DEPRECATED: the {var} environment variable is a legacy fallback "
        f"removed in 1.0; use {replacement}.",
    )
