"""Two composition roots, one database.

``registration.register`` composes the plugin onto a Hermes ``PluginContext``;
``__main__.main`` composes the same stages onto a standalone parser. They drive
one ``state.db`` through one migration ladder, which is the cost of having two
roots and the thing most worth pinning: if they ever disagreed about the schema
or about where the file lives, an operator running both would get a database one
of them refuses to open, or two databases that each hold half the history.

Nothing here asserts a specific ``SCHEMA_VERSION`` — it asserts the two roots
agree on it, so a future migration cannot pass by teaching only one of them.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from flaky_stabilization import __main__ as standalone
from flaky_stabilization import paths, registration
from flaky_stabilization.storage import state

TEST_ID = "pkg.Mod::test_something"


def _drive_hermes_root(ctx) -> None:
    """Register the plugin the way Hermes does, then make it touch state.db.

    Registration alone opens nothing (connections are lazy by design), so the
    schema is only exercised by dispatching a tool — which is what the host
    would do next anyway.
    """
    registration.register(ctx)
    ctx.tools["is_flaky"]["handler"]({"test_id": TEST_ID})


def _drive_cli_root() -> None:
    """Same query, through the standalone parser."""
    assert standalone.main(["is-flaky", TEST_ID, "--format", "json"]) == 0


def _recorded_version(db_path) -> int:
    with closing(sqlite3.connect(db_path)) as conn:
        return state.current_version(conn)


# ---------------------------------------------------------------------------
# one ladder, either order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hermes_first", [True, False], ids=["hermes-first", "cli-first"])
def test_both_roots_converge_on_one_schema_version(profile_env, fake_ctx, capsys,
                                                   hermes_first):
    db_path = state.state_db_path()
    assert not db_path.exists()

    if hermes_first:
        _drive_hermes_root(fake_ctx)
        after_first = _recorded_version(db_path)
        _drive_cli_root()
    else:
        _drive_cli_root()
        after_first = _recorded_version(db_path)
        _drive_hermes_root(fake_ctx)
    capsys.readouterr()

    # The second root neither re-ran the ladder nor rolled it back.
    assert after_first == _recorded_version(db_path) == state.SCHEMA_VERSION


@pytest.mark.parametrize("hermes_first", [True, False], ids=["hermes-first", "cli-first"])
def test_neither_root_leaves_a_version_the_other_rejects(profile_env, fake_ctx, capsys,
                                                         hermes_first):
    """``ensure_schema`` refuses a database newer than the code reading it. A
    root that bumped the ladder on its own would lock the other one out."""
    if hermes_first:
        _drive_hermes_root(fake_ctx)
    else:
        _drive_cli_root()
    capsys.readouterr()

    # Whatever the first root wrote, a plain open (what the other root does)
    # must succeed rather than raise "newer than supported version".
    with closing(state.connect(state.state_db_path())) as conn:
        assert state.current_version(conn) == state.SCHEMA_VERSION


def test_the_second_root_reads_what_the_first_root_wrote(profile_env, fake_ctx,
                                                         tmp_path, capsys):
    """One home means one set of verdicts: a scan run from the CLI must be
    visible to the tool the Hermes root registers."""
    import json
    import re
    from datetime import UTC, datetime, timedelta
    from pathlib import Path

    fixtures = Path(__file__).resolve().parent / "history" / "fixtures"
    stamp = (datetime.now(UTC).replace(tzinfo=None, microsecond=0)
             - timedelta(days=1)).isoformat()
    xml = re.sub(r'timestamp="[^"]*"', f'timestamp="{stamp}"',
                 (fixtures / "pytest_junit_failures.xml").read_text(encoding="utf-8"),
                 count=1)
    (tmp_path / "run.xml").write_text(xml, encoding="utf-8")

    assert standalone.main(["ingest", str(tmp_path / "run.xml")]) == 0
    assert standalone.main(["scan", "--format", "json", "--min-fails", "1"]) == 0
    capsys.readouterr()

    registration.register(fake_ctx)
    verdict = json.loads(fake_ctx.tools["is_flaky"]["handler"](
        {"test_id": "src.auth.test_oauth::test_oauth_login"}))

    assert verdict["success"] is True
    assert verdict["status"] == "consistently_failing"


# ---------------------------------------------------------------------------
# one home
# ---------------------------------------------------------------------------


def test_both_roots_resolve_the_same_database_paths(profile_env, fake_ctx, capsys):
    from flaky_stabilization.history import storage as history_storage

    before = (state.state_db_path(), history_storage.default_db_path())
    _drive_hermes_root(fake_ctx)
    after_hermes = (state.state_db_path(), history_storage.default_db_path())
    _drive_cli_root()
    after_cli = (state.state_db_path(), history_storage.default_db_path())
    capsys.readouterr()

    assert before == after_hermes == after_cli
    assert after_cli[0].exists(), "the roots agreed on a path neither wrote to"


def test_hermes_registration_tolerates_the_cli_home_var(profile_env, fake_ctx,
                                                        monkeypatch, tmp_path, capsys):
    """A box running both must not break the plugin: with FLAKY_STAB_HOME set,
    the Hermes root follows it like every other caller of the ONE resolver,
    registers cleanly, and lands its data there."""
    cli_home = tmp_path / "standalone-home"
    monkeypatch.setenv(paths.CLI_HOME_ENV, str(cli_home))

    _drive_hermes_root(fake_ctx)
    capsys.readouterr()

    assert len(fake_ctx.tools) >= 16
    assert "flaky-stab" in fake_ctx.cli_commands
    assert state.state_db_path().exists()
    assert cli_home in state.state_db_path().parents
