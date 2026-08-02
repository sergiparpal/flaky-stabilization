"""Tests for ``hermes flaky-stab install-cron``.

The single sanctioned subprocess (``hermes cron create``) is mocked, so these
tests never shell out to a real hermes CLI and have no side effects beyond the
temp HERMES_HOME.
"""

import argparse
import os
import shlex
import stat
from types import SimpleNamespace

from hermes_flaky_detective import cli, config


def _run(argv):
    parser = argparse.ArgumentParser(prog="flaky-stab")
    cli.setup_parser(parser)
    return cli.handle(parser.parse_args(argv))


def _shim_path(home):
    return home / "scripts" / "flaky-stab-scan.sh"


# ---------------------------------------------------------------------------
# shim install + config persistence (no job creation)
# ---------------------------------------------------------------------------


def test_no_create_installs_shim_and_prints_command(profile_env, hermes_on_path, capsys):
    assert _run(["install-cron", "--no-create", "--deliver", "local"]) == 0
    out = capsys.readouterr().out
    shim = _shim_path(profile_env)
    assert shim.exists()
    assert stat.S_IMODE(os.stat(shim).st_mode) == 0o700
    assert "hermes cron create" in out
    assert "--no-agent --script flaky-stab-scan.sh" in out
    assert "gateway" in out.lower()


def test_persists_resolved_options(profile_env, capsys):
    assert _run(["install-cron", "--no-create", "--schedule", "0 7 * * *",
                 "--deliver", "slack", "--window", "7", "--min-fails", "2"]) == 0
    cfg = config.get_config()
    assert cfg["schedule"] == "0 7 * * *"
    assert cfg["deliver"] == "slack"
    assert cfg["window_days"] == 7
    assert cfg["min_fails"] == 2


def test_printable_command_shell_quotes_arguments(hermes_on_path):
    # A schedule/deliver with spaces must stay single shell tokens in the printed
    # copy-paste command, so it round-trips through a shell parser intact.
    printable = cli._printable_cron_command("*/5 9 * * *", "my channel")
    tokens = shlex.split(printable)
    assert "*/5 9 * * *" in tokens          # the whole cron expr is one argument
    assert "my channel" in tokens           # the delivery channel is one argument
    assert tokens[:3] == ["hermes", "cron", "create"]
    assert tokens[-2:] == ["--name", cli.CRON_JOB_NAME]


def test_rejects_nonpositive_min_fails_without_persisting(profile_env, capsys):
    # A bad tunable is rejected (exit 2) before the shim or config is written.
    assert _run(["install-cron", "--no-create", "--min-fails", "0"]) == 2
    assert "min-fails" in capsys.readouterr().err
    assert not _shim_path(profile_env).exists()
    # config.json was not created by the aborted run.
    assert not config.config_path().exists()


# ---------------------------------------------------------------------------
# missing/failing shim source — clean error, config never mutated first
# ---------------------------------------------------------------------------


def test_missing_shim_source_errors_before_persisting_config(profile_env, capsys, monkeypatch):
    # A site-packages install has no checkout scripts/ dir: install-cron must
    # verify the shim source BEFORE writing config.json and fail with a one-line
    # error (exit 1) — never an uncaught FileNotFoundError with config mutated.
    monkeypatch.setattr(cli, "SHIM_NAME", "no-such-shim.sh")
    assert _run(["install-cron", "--no-create", "--deliver", "local"]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "no-such-shim.sh" in err
    assert "Traceback" not in err
    assert not config.config_path().exists()          # config untouched
    assert not (profile_env / "scripts").exists()     # nothing installed


def test_shim_copy_failure_is_a_clean_error(profile_env, capsys, monkeypatch):
    # An install failure after the source check (e.g. permissions) must surface
    # as a one-line error + non-zero exit, not a traceback.
    def boom(self, *args, **kwargs):
        raise PermissionError("denied")

    # The shim is written, not copied: its exec line is retargeted at install
    # time when there is no hermes CLI to exec through.
    monkeypatch.setattr("pathlib.Path.write_text", boom)
    assert _run(["install-cron", "--no-create", "--deliver", "local"]) == 1
    err = capsys.readouterr().err
    assert "error:" in err and "denied" in err
    assert "Traceback" not in err


# ---------------------------------------------------------------------------
# job creation via the (mocked) subprocess
# ---------------------------------------------------------------------------


def test_create_success(profile_env, hermes_on_path, capsys, monkeypatch):
    calls = {}

    def fake_run(cmd, capture_output=False, text=False):
        calls["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="created job id=job_123", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert _run(["install-cron", "--deliver", "local", "--schedule", "0 9 * * *"]) == 0
    out = capsys.readouterr().out
    assert "created cron job 'flaky-stabilization'" in out
    # The subprocess was invoked with the documented arguments.
    assert calls["cmd"][:4] == ["hermes", "cron", "create", "0 9 * * *"]
    assert "--no-agent" in calls["cmd"]
    assert calls["cmd"][-2:] == ["--name", "flaky-stabilization"]
    assert _shim_path(profile_env).exists()


def test_create_failure_falls_back_to_printing(profile_env, hermes_on_path, capsys,
                                               monkeypatch):
    def fake_run(cmd, capture_output=False, text=False):
        return SimpleNamespace(returncode=1, stdout="", stderr="gateway not configured")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert _run(["install-cron", "--deliver", "local"]) == 1
    out = capsys.readouterr().out
    assert "gateway not configured" in out
    assert "hermes cron create" in out
    assert "gateway" in out.lower()
    # Shim is still installed even though the job was not created.
    assert _shim_path(profile_env).exists()


def test_create_when_hermes_cli_missing_falls_back(profile_env, hermes_on_path, capsys,
                                                   monkeypatch):
    def fake_run(cmd, capture_output=False, text=False):
        raise FileNotFoundError("hermes")

    monkeypatch.setattr("subprocess.run", fake_run)
    assert _run(["install-cron", "--deliver", "local"]) == 0
    out = capsys.readouterr().out
    assert "could not run the hermes CLI" in out
    assert "hermes cron create" in out
    assert _shim_path(profile_env).exists()


# ---------------------------------------------------------------------------
# no hermes CLI: install still works, and says how to schedule it with cron
# ---------------------------------------------------------------------------


def test_install_without_hermes_prints_a_crontab_line(profile_env, no_hermes_on_path,
                                                      capsys):
    assert _run(["install-cron", "--deliver", "local", "--schedule", "0 9 * * *"]) == 0
    out = capsys.readouterr().out
    assert "0 9 * * * flaky-stab scan --format cron" in out
    assert "hermes cron create" not in out
    assert "crontab -e" in out
    assert _shim_path(profile_env).exists()


def test_installed_shim_targets_the_console_script_without_hermes(profile_env,
                                                                  no_hermes_on_path,
                                                                  capsys):
    assert _run(["install-cron", "--no-create", "--deliver", "local"]) == 0
    text = _shim_path(profile_env).read_text(encoding="utf-8")
    assert "exec flaky-stab scan --format cron" in text
    assert "exec hermes" not in text
    assert "set -euo pipefail" in text
    assert stat.S_IMODE(os.stat(_shim_path(profile_env)).st_mode) == 0o700


def test_leading_dash_schedule_is_still_rejected_without_hermes(profile_env,
                                                               no_hermes_on_path,
                                                               capsys):
    """The schedule reaches a crontab line now instead of an argv, but it is
    validated on both paths and still before anything is written."""
    # `--schedule=<value>`: the form that gets a leading-dash value past argparse
    # and all the way to the validator, which is the point of the check.
    assert _run(["install-cron", "--schedule=--no-agent", "--deliver", "local"]) == 2
    captured = capsys.readouterr()
    assert "error:" in captured.err
    assert captured.out == ""
    assert not config.config_path().exists()
    assert not (profile_env / "scripts").exists()
