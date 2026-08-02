"""The standalone ``flaky-stab`` CLI root (``flaky_stabilization.__main__``).

The Hermes plugin composes the stage CLIs onto a host-supplied parser; this root
composes the same subcommands onto a parser it owns. These tests pin the entry
point's contract (exit codes, stdout/stderr split, mounted stage arguments) and
prove the whole path works with Hermes unimportable — the property that makes the
CLI standalone rather than a second front door onto the plugin.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from _doubles import block_hermes_imports

from flaky_stabilization import __main__ as standalone
from flaky_stabilization import __version__

FIXTURES = Path(__file__).resolve().parent / "history" / "fixtures"


@pytest.fixture
def no_hermes(monkeypatch):
    return block_hermes_imports(monkeypatch)


@pytest.fixture
def history_reset():
    from flaky_stabilization.history import storage

    storage.reset_for_tests()
    yield
    storage.reset_for_tests()


def _ingest_fixture(name: str, dest: Path, *, days_ago: int) -> Path:
    """Copy a shipped JUnit fixture to *dest* with a run timestamp *days_ago*.

    The fixtures carry fixed timestamps from when they were captured, which sit
    outside the 14-day detection window; only the stamp is rewritten so the rows
    the detector reads are the fixture's own.
    """
    stamp = (datetime.now(UTC).replace(tzinfo=None, microsecond=0)
             - timedelta(days=days_ago)).isoformat()
    xml = (FIXTURES / name).read_text(encoding="utf-8")
    xml, count = re.subn(r'timestamp="[^"]*"', f'timestamp="{stamp}"', xml, count=1)
    assert count == 1, f"{name} has no timestamp attribute to rewrite"
    dest.write_text(xml, encoding="utf-8")
    return dest


# ---------------------------------------------------------------------------
# entry-point contract
# ---------------------------------------------------------------------------


def test_bare_invocation_prints_status_and_succeeds(profile_env, capsys):
    assert standalone.main([]) == 0
    out = capsys.readouterr().out
    assert "state.db" in out and "history.db" in out


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        standalone.main(["--help"])
    assert excinfo.value.code == 0
    assert "flaky-stab" in capsys.readouterr().out


def test_version_reports_the_package_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        standalone.main(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize("subcommand", ["scan", "ingest", "list", "is-flaky",
                                        "install-cron", "prune", "jira", "migrate"])
def test_stage_subcommands_are_mounted(subcommand, capsys):
    """``--help`` on each subcommand proves the stage arg-adders ran — a parser
    missing them would exit 2 on the unknown command instead."""
    with pytest.raises(SystemExit) as excinfo:
        standalone.main([subcommand, "--help"])
    assert excinfo.value.code == 0
    assert subcommand in capsys.readouterr().out


def test_unknown_subcommand_exits_two_on_stderr(capsys):
    """Errors go to stderr, reports to stdout: a no-agent cron job delivers this
    process's stdout verbatim as its report."""
    with pytest.raises(SystemExit) as excinfo:
        standalone.main(["no-such-command"])
    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no-such-command" in captured.err


def test_build_parser_uses_the_binary_name():
    assert standalone.build_parser().prog == "flaky-stab"


# ---------------------------------------------------------------------------
# round trip: ingest → scan, through the standalone root only
# ---------------------------------------------------------------------------


def test_ingest_then_scan_reports_the_ingested_tests(profile_env, history_reset,
                                                     tmp_path, capsys):
    # Three failing runs: the default min_fails is 3, so this clears the bar.
    for i, days_ago in enumerate((5, 3, 2)):
        xml = _ingest_fixture("pytest_junit_failures.xml",
                              tmp_path / f"failures-{i}.xml", days_ago=days_ago)
        assert standalone.main(["ingest", str(xml)]) == 0
        assert "ingested" in capsys.readouterr().out
    basic = _ingest_fixture("pytest_junit_basic.xml", tmp_path / "basic.xml", days_ago=1)
    assert standalone.main(["ingest", str(basic)]) == 0
    capsys.readouterr()

    assert standalone.main(["scan", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    verdicts = {v["test_key"]: v for v in payload["verdicts"]}
    assert "src.auth.test_oauth::test_oauth_login" in verdicts
    assert "tests.test_math::test_add" in verdicts
    # Three ingested runs, all failing, no passes — not flaky, consistently failing.
    login = verdicts["src.auth.test_oauth::test_oauth_login"]
    assert login["status"] == "consistently_failing"
    assert (login["fails"], login["passes"]) == (3, 0)
    assert verdicts["tests.test_math::test_add"]["status"] == "stable"
    assert payload["scan"]["tests_examined"] == len(verdicts)


def test_list_reads_back_what_scan_persisted(profile_env, history_reset,
                                             tmp_path, capsys):
    xml = _ingest_fixture("pytest_junit_failures.xml", tmp_path / "run.xml", days_ago=1)
    assert standalone.main(["ingest", str(xml)]) == 0
    assert standalone.main(["scan", "--format", "json", "--min-fails", "1"]) == 0
    capsys.readouterr()

    assert standalone.main(["list", "--status", "consistently_failing"]) == 0
    assert "test_oauth_login" in capsys.readouterr().out


def test_is_flaky_is_mounted_on_the_standalone_root(profile_env, history_reset,
                                                    tmp_path, capsys):
    """The query the CI gate makes, through the standalone composition root."""
    xml = _ingest_fixture("pytest_junit_failures.xml", tmp_path / "run.xml", days_ago=1)
    assert standalone.main(["ingest", str(xml)]) == 0
    assert standalone.main(["scan", "--format", "json", "--min-fails", "1"]) == 0
    capsys.readouterr()

    rc = standalone.main(["is-flaky", "src.auth.test_oauth::test_oauth_login",
                          "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["success"] is True
    # Failing every run is not flaky — that is a real break, and the gate must
    # be able to tell the difference.
    assert payload["is_flaky"] is False
    assert payload["status"] == "consistently_failing"


# ---------------------------------------------------------------------------
# the point of the exercise: no Hermes anywhere
# ---------------------------------------------------------------------------


def test_status_succeeds_with_hermes_unimportable(profile_env, no_hermes, capsys):
    with pytest.raises(ImportError):
        __import__("hermes_constants")
    with pytest.raises(ImportError):
        __import__("hermes_cli.utils")

    assert standalone.main(["status"]) == 0
    assert "state.db" in capsys.readouterr().out


def test_ingest_and_scan_succeed_with_hermes_unimportable(profile_env, no_hermes,
                                                          history_reset, tmp_path,
                                                          capsys):
    xml = _ingest_fixture("pytest_junit_basic.xml", tmp_path / "run.xml", days_ago=1)
    assert standalone.main(["ingest", str(xml)]) == 0
    capsys.readouterr()
    assert standalone.main(["scan", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(v["test_key"].startswith("tests.test_math::") for v in payload["verdicts"])
