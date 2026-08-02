"""Profile-aware path resolution for the unified plugin.

Two storage roots exist by design (plan D4):

* ``<hermes_home>/test-history/`` — the keystone public data contract owned by
  the ported ``history`` stage (its own storage module resolves it; not here).
* ``<hermes_home>/flaky-stabilization/`` — everything private to this plugin:
  ``state.db``, ``config.json``, healer patches, the learned-patterns mirror.

Nothing is cached at module level: tests repoint ``HERMES_HOME`` per test.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_NAME = "flaky-stabilization"

# The standalone CLI's own home override (``flaky-stab``, ``__main__.py``).
# Namespaced to the binary rather than reusing ``HERMES_HOME``, so an operator
# running the CLI on a box that also has Hermes can keep the two apart.
CLI_HOME_ENV = "FLAKY_STAB_HOME"


def env_home() -> Path | None:
    """``$HERMES_HOME``, expanded, or ``None`` when unset/empty.

    ``expandvars`` + ``expanduser`` are applied because sources like a systemd
    ``EnvironmentFile`` or a crontab set ``HERMES_HOME=~/hermes-data`` with no
    shell expansion, and a literal ``~/...`` would otherwise become a *relative*
    path that ``get_data_dir`` mkdirs as ``./~`` under the CWD.

    Pure stdlib and Hermes-free, so modules that must not import Hermes even
    lazily (``triage.handlers``) can share this expansion instead of re-deriving
    it — the drift that let three resolvers disagree on the same env value.
    """
    raw = (os.environ.get("HERMES_HOME") or "").strip()
    if not raw:
        return None
    return Path(os.path.expanduser(os.path.expandvars(raw)))


def cli_env_home() -> Path | None:
    """``$FLAKY_STAB_HOME``, expanded, or ``None`` when unset/empty.

    Mirrors :func:`env_home` exactly, expansion included, and for the same
    reason: a crontab or a systemd ``EnvironmentFile`` sets
    ``FLAKY_STAB_HOME=~/flaky`` with no shell expansion, and a literal ``~/...``
    would otherwise become a *relative* path that ``get_data_dir`` mkdirs as
    ``./~`` under whatever the CWD happened to be.
    """
    raw = (os.environ.get(CLI_HOME_ENV) or "").strip()
    if not raw:
        return None
    return Path(os.path.expanduser(os.path.expandvars(raw)))


def default_home() -> Path:
    """The last-resort home when Hermes is absent and ``$HERMES_HOME`` is unset."""
    return Path.home() / ".hermes"


def get_hermes_home() -> Path:
    """Return the profile-aware home — the ONE resolver, for both roots.

    The chain, in order:

    1. ``$FLAKY_STAB_HOME`` — the standalone CLI's explicit override
    2. ``hermes_constants.get_hermes_home()``
    3. ``hermes_cli.utils.display_hermes_home()``
    4. ``$HERMES_HOME``
    5. ``~/.hermes``

    ``FLAKY_STAB_HOME`` outranks the Hermes lookups deliberately: an env var the
    operator set explicitly (a CI job's state dir, a throwaway profile) must beat
    ambient discovery of whatever Hermes happens to be installed on the box.
    With it unset, steps 2-5 resolve exactly as they did before it existed, so a
    Hermes install sees no change at all.

    Every stage delegates here (``history.storage``, ``detective.storage``,
    ``incidents.config``, ``triage``). Do not re-implement this chain: the
    copies that used to exist each tried a different subset of the lookups and
    skipped :func:`env_home`'s expansion, so the same ``HERMES_HOME`` resolved
    to different directories depending on which module asked.

    The Hermes imports are lazy (call time, not import time) so this module
    stays safe to import from the lightweight CLI path.
    """
    cli_home = cli_env_home()
    if cli_home is not None:
        return cli_home
    try:
        from hermes_constants import get_hermes_home as _core_home  # type: ignore

        return Path(_core_home())
    except Exception:
        pass
    try:
        from hermes_cli.utils import display_hermes_home  # type: ignore

        return Path(display_hermes_home())
    except Exception:
        pass
    return env_home() or default_home()


def get_data_dir() -> Path:
    """``<hermes_home>/flaky-stabilization/`` — created owner-only.

    The directory holds captured test output, triage excerpts and incident
    rows that can contain sensitive data; keep it ``0700`` so other local
    users cannot read it (or the SQLite WAL/-shm sidecars). Best-effort: a
    filesystem that cannot represent POSIX modes must not break the plugin.
    """
    data_dir = get_hermes_home() / DATA_DIR_NAME
    # mode=0o700 on creation closes the window between mkdir (default 0755) and
    # the chmod below during which the dir — and any sidecar written into it — is
    # world-listable. 0o700 is umask-safe (no group/other bits to mask off). The
    # chmod stays as belt-and-suspenders for a dir that already exists 0755.
    data_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    try:
        os.chmod(data_dir, 0o700)
    except OSError:
        pass
    return data_dir


def is_real_file_path(path: Path | str) -> bool:
    """False for the sentinel/URI paths SQLite accepts (``:memory:``, ``file:``)
    that have no on-disk file to mkdir-for or permission-harden."""
    s = str(path)
    return bool(s) and s != ":memory:" and not s.startswith("file:")


# Backwards-compatible private alias (pre-existing intra-package callers).
_is_real_file_path = is_real_file_path


def precreate_private(path: Path | str) -> None:
    """Create *path* ``0600`` *before* SQLite opens it, so a DB holding PII is
    never briefly exposed at the process umask (e.g. 0644). No-op if the file
    already exists or cannot be created; :func:`harden_db_files` chmods after
    connect regardless. Best-effort on non-POSIX mounts."""
    if not _is_real_file_path(path) or os.path.exists(path):
        return
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    except OSError:
        pass


def harden_db_files(path: Path | str) -> None:
    """Best-effort ``0600`` on a SQLite DB *and* its WAL/-shm/-journal sidecars.

    The sidecars are created by the first write transaction in WAL mode and hold
    the same recently-written rows as the main DB, so restricting only the main
    file leaves incident PII / captured test output group- or world-readable in
    the sidecars. Call after the schema is applied so the sidecars already exist.
    """
    if not _is_real_file_path(path):
        return
    base = str(path)
    for suffix in ("", "-wal", "-shm", "-journal"):
        p = base + suffix
        try:
            if os.path.exists(p):
                os.chmod(p, 0o600)
        except OSError:
            pass


def restrict_file(path: Path | str) -> None:
    """Best-effort ``0600`` on a data file (DB, config).

    For a live SQLite DB prefer :func:`harden_db_files`, which also covers the
    WAL/-shm/-journal sidecars.
    """
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_only_uri(db_path: Path | str) -> str:
    """The ``mode=ro`` connection URI for *db_path* (read-only, never writable).

    The path is percent-encoded (SQLite decodes percent-escapes in URI
    filenames): a raw ``#``, ``?``, or ``%`` would otherwise terminate or alter
    the URI's path part, silently dropping ``mode=ro`` and opening — or even
    creating — a *different* file read-write.

    Lives here rather than in a stage module because every read-only opener
    needs it; ``cli.status_text`` used to hand-build the un-encoded form.
    """
    from urllib.parse import quote

    return f"file:{quote(str(Path(db_path)), safe='/')}?mode=ro"
