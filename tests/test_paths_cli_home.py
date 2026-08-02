"""``FLAKY_STAB_HOME`` — the standalone CLI's home override.

Two things are pinned here, and the second matters more than the first:

1. the new override wins, and is expanded the way ``HERMES_HOME`` is;
2. with it **unset**, resolution is byte-identical to what it was before the
   var existed — all four pre-existing branches, in order. A regression there
   would relocate the data directory of every installed Hermes plugin, which is
   the single highest-risk failure mode of adding a second composition root.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _doubles import block_hermes_imports, fake_hermes_cli, fake_hermes_constants

from flaky_stabilization import paths


@pytest.fixture
def no_hermes(monkeypatch):
    return block_hermes_imports(monkeypatch)


@pytest.fixture
def sandboxed_tilde(profile_env, monkeypatch):
    """Make ``~`` expand inside the sandbox and return it.

    ``profile_env`` repoints ``Path.home()``, but ``os.path.expanduser`` reads
    ``$HOME`` — without this the expansion tests would resolve against the
    developer's real home directory.
    """
    fake_home = Path.home()
    monkeypatch.setenv("HOME", str(fake_home))
    return fake_home


# ---------------------------------------------------------------------------
# the new override
# ---------------------------------------------------------------------------


def test_cli_home_env_is_the_documented_name():
    assert paths.CLI_HOME_ENV == "FLAKY_STAB_HOME"


def test_cli_home_wins_and_is_returned(profile_env, monkeypatch, tmp_path):
    cli_home = tmp_path / "standalone-home"
    monkeypatch.setenv(paths.CLI_HOME_ENV, str(cli_home))
    assert paths.get_hermes_home() == cli_home


def test_cli_home_outranks_every_hermes_lookup(profile_env, monkeypatch, tmp_path):
    """Explicit beats ambient: an operator-set env var must win over whatever
    Hermes install happens to be importable on the box."""
    fake_hermes_constants(monkeypatch, tmp_path / "constants-home")
    fake_hermes_cli(monkeypatch, tmp_path / "cli-home")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))
    cli_home = tmp_path / "standalone-home"
    monkeypatch.setenv(paths.CLI_HOME_ENV, str(cli_home))

    assert paths.get_hermes_home() == cli_home


def test_cli_home_tilde_expands_to_an_absolute_path(sandboxed_tilde, monkeypatch):
    """A crontab / systemd EnvironmentFile sets ``FLAKY_STAB_HOME=~/x`` with no
    shell expansion; a literal ``~`` would mkdir ``./~`` under the CWD."""
    monkeypatch.setenv(paths.CLI_HOME_ENV, "~/flaky-state")

    home = paths.get_hermes_home()

    assert home.is_absolute()
    assert "~" not in str(home)
    assert home == sandboxed_tilde / "flaky-state"


def test_cli_home_expands_env_vars(profile_env, monkeypatch, tmp_path):
    monkeypatch.setenv("FLAKY_TEST_ROOT", str(tmp_path))
    monkeypatch.setenv(paths.CLI_HOME_ENV, "$FLAKY_TEST_ROOT/state")
    assert paths.get_hermes_home() == tmp_path / "state"


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_cli_home_is_ignored(profile_env, monkeypatch, value, tmp_path):
    """Unset and set-to-empty must behave the same: exported-but-empty is how a
    shell profile spells "not configured"."""
    monkeypatch.setenv(paths.CLI_HOME_ENV, value)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))

    assert paths.cli_env_home() is None
    with pytest.raises(ImportError):
        __import__("hermes_constants")  # sanity: not installed in the test env


# ---------------------------------------------------------------------------
# Invariant 3.1.1 — with the var unset, nothing changed
# ---------------------------------------------------------------------------


def test_hermes_constants_still_wins_when_cli_home_unset(profile_env, monkeypatch,
                                                         tmp_path):
    fake_hermes_constants(monkeypatch, tmp_path / "constants-home")
    fake_hermes_cli(monkeypatch, tmp_path / "cli-home")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))

    assert paths.get_hermes_home() == tmp_path / "constants-home"


def test_hermes_cli_is_the_second_branch_when_cli_home_unset(profile_env, monkeypatch,
                                                             tmp_path):
    fake_hermes_cli(monkeypatch, tmp_path / "cli-home")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))

    assert paths.get_hermes_home() == tmp_path / "cli-home"


def test_hermes_home_env_is_the_third_branch(profile_env, no_hermes, monkeypatch,
                                             tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "env-home"))

    assert paths.get_hermes_home() == tmp_path / "env-home"


def test_default_home_is_the_last_resort(profile_env, no_hermes, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)

    assert paths.get_hermes_home() == Path.home() / ".hermes"


def test_hermes_home_tilde_expansion_is_unchanged(sandboxed_tilde, no_hermes,
                                                  monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "~/hermes-data")

    assert paths.get_hermes_home() == sandboxed_tilde / "hermes-data"


# ---------------------------------------------------------------------------
# co-location: one home, one set of databases
# ---------------------------------------------------------------------------


def test_both_databases_resolve_under_the_cli_home(profile_env, monkeypatch, tmp_path):
    """The drift bug documented at ``history/storage.py:46``: a second resolver
    that skipped part of the chain put ``history.db`` somewhere ``state.db`` was
    not. Both must follow the ONE resolver, including the new branch."""
    from flaky_stabilization.history import storage as history_storage
    from flaky_stabilization.storage import state

    cli_home = tmp_path / "standalone-home"
    monkeypatch.setenv(paths.CLI_HOME_ENV, str(cli_home))
    history_storage.reset_for_tests()

    state_db = state.state_db_path()
    history_db = history_storage.default_db_path()

    assert state_db == cli_home / paths.DATA_DIR_NAME / state_db.name
    assert history_db.parent.parent == cli_home
    assert paths.get_data_dir() == cli_home / paths.DATA_DIR_NAME


def test_data_dir_under_the_cli_home_is_owner_only(profile_env, monkeypatch, tmp_path):
    """The 0700 promise travels with the new branch — the directory holds
    captured test output and incident rows either way."""
    import os
    import stat

    monkeypatch.setenv(paths.CLI_HOME_ENV, str(tmp_path / "standalone-home"))

    data_dir = paths.get_data_dir()

    assert stat.S_IMODE(os.stat(data_dir).st_mode) == 0o700
