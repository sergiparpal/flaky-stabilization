"""The shared no-agent cron-job installer (``cronjobs``).

Both `install-cron` and `install-cron --with-jira-sync` route through this module.
It used to exist twice — factored into helpers in ``detective.cli`` and re-inlined
in ``cli._install_jira_sync_job`` — and the copies had drifted. These tests pin
the behaviour the re-inlined copy had lost.
"""

from __future__ import annotations

import os
import shlex
import stat

import pytest

from flaky_stabilization import cronjobs

SCAN_JOB = cronjobs.CronJob("flaky-stab-scan.sh", "flaky-stabilization", "cron job",
                            command=("scan", "--format", "cron"))
JIRA_JOB = cronjobs.CronJob(
    "flaky-stab-jira-sync.sh", "flaky-stabilization-jira-sync", "jira-sync job",
    command=("jira", "sync", "--quiet"),
)


def test_the_production_jobs_are_what_these_tests_model():
    """Pins the two real job definitions to the fixtures above, so a change to
    either one fails here instead of quietly making this file test nothing."""
    from flaky_stabilization import cli as unified_cli
    from flaky_stabilization.detective import cli as detective_cli

    assert detective_cli._scan_job() == SCAN_JOB
    assert unified_cli._jira_sync_job() == JIRA_JOB


@pytest.mark.parametrize("job", [SCAN_JOB, JIRA_JOB])
def test_job_command_matches_the_shipped_shims_exec_line(job):
    """``CronJob.command`` and the shim's ``exec`` line are the same command
    written twice — one for the gateway, one for a plain crontab line. Drift
    between them would schedule a job that runs something else."""
    lines = cronjobs.shim_source(job.shim_name).read_text(encoding="utf-8").splitlines()
    exec_lines = [ln for ln in lines if ln.startswith("exec ")]
    assert exec_lines == [f"exec hermes flaky-stab {' '.join(job.command)}"]


# ---------------------------------------------------------------------------
# shim source resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", [SCAN_JOB, JIRA_JOB])
def test_shim_source_resolves_to_the_shipped_script(job):
    """One derivation of `<checkout>/scripts/`, not `parents[1]` in one caller and
    `parents[2]` in another (which agreed only because the files sat at different
    depths)."""
    src = cronjobs.shim_source(job.shim_name)
    assert src.is_file(), f"{job.shim_name} is not shipped at {src}"
    assert src.parent.name == "scripts"


def test_missing_shim_error_reports_and_creates_nothing(profile_env):
    problem = cronjobs.missing_shim_error("no-such-shim.sh")
    assert problem and "no-such-shim.sh" in problem
    assert not (profile_env / "scripts").exists(), "the check must not mkdir anything"


def test_missing_shim_error_is_none_when_present():
    assert cronjobs.missing_shim_error(SCAN_JOB.shim_name) is None


# ---------------------------------------------------------------------------
# install_shim: the 0700 dir + 0700 script the re-inlined copy had lost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", [SCAN_JOB, JIRA_JOB])
def test_install_shim_locks_down_both_the_dir_and_the_script(profile_env, hermes_on_path,
                                                             job):
    """The gateway executes what lands here on a schedule, so another local user
    must not be able to read — let alone replace — either.

    ``cli._install_jira_sync_job`` used to mkdir the directory without chmod'ing
    it; that was masked only because the detection job's installer always ran
    first. Called on its own, as here, the omission is visible.
    """
    assert not (profile_env / "scripts").exists()

    shim_dst = cronjobs.install_shim(job.shim_name)

    scripts_dir = profile_env / "scripts"
    assert stat.S_IMODE(os.stat(scripts_dir).st_mode) == 0o700, "scripts dir not 0700"
    assert stat.S_IMODE(os.stat(shim_dst).st_mode) == 0o700, "shim not 0700"
    assert shim_dst == scripts_dir / job.shim_name
    assert shim_dst.read_text(encoding="utf-8") == \
        cronjobs.shim_source(job.shim_name).read_text(encoding="utf-8")


def test_install_shim_tightens_a_preexisting_world_readable_dir(profile_env,
                                                                hermes_on_path):
    """A scripts/ dir left at 0755 by an earlier install is repaired, not accepted."""
    scripts_dir = profile_env / "scripts"
    scripts_dir.mkdir(parents=True)
    os.chmod(scripts_dir, 0o755)

    cronjobs.install_shim(SCAN_JOB.shim_name)

    assert stat.S_IMODE(os.stat(scripts_dir).st_mode) == 0o700


def test_install_shim_raises_oserror_for_the_caller_to_shape(profile_env, hermes_on_path,
                                                            monkeypatch):
    """Callers turn this into a one-line error + exit 1, never a traceback."""
    def boom(self, *args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr("pathlib.Path.write_text", boom)
    with pytest.raises(OSError, match="denied"):
        cronjobs.install_shim(SCAN_JOB.shim_name)


# ---------------------------------------------------------------------------
# the printed copy-paste command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", [SCAN_JOB, JIRA_JOB])
def test_printable_command_shell_quotes_user_supplied_parts(hermes_on_path, job):
    printable = cronjobs.printable_command(job, "*/5 9 * * *", "my channel")
    tokens = shlex.split(printable)
    assert "*/5 9 * * *" in tokens        # the whole cron expr stays one argument
    assert "my channel" in tokens         # the delivery channel stays one argument
    assert tokens[:3] == ["hermes", "cron", "create"]
    assert tokens[-2:] == ["--name", job.job_name]
    assert "--script" in tokens and job.shim_name in tokens


# ---------------------------------------------------------------------------
# create_job: never an error, always a usable fallback
# ---------------------------------------------------------------------------


def _fake_completed(returncode, stdout="", stderr=""):
    from types import SimpleNamespace

    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_create_job_success(hermes_on_path, monkeypatch, capsys):
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: _fake_completed(0, stdout="ok"))
    assert cronjobs.create_job(JIRA_JOB, "0 9 * * *", "local") == 0
    out = capsys.readouterr().out
    assert f"created cron job '{JIRA_JOB.job_name}'" in out


def test_create_job_nonzero_falls_back_to_printing_with_the_job_description(
    hermes_on_path, monkeypatch, capsys
):
    monkeypatch.setattr("subprocess.run",
                        lambda *a, **k: _fake_completed(1, stderr="gateway not configured"))
    assert cronjobs.create_job(JIRA_JOB, "0 9 * * *", "local") == 1
    out = capsys.readouterr().out
    assert "gateway not configured" in out
    assert "could not create the jira-sync job automatically" in out
    assert "hermes cron create" in out
    assert "gateway" in out.lower()      # the gateway note the inlined copy omitted


def test_create_job_missing_hermes_cli_falls_back(hermes_on_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise FileNotFoundError("no hermes")

    monkeypatch.setattr("subprocess.run", boom)
    assert cronjobs.create_job(SCAN_JOB, "0 9 * * *", "local") == 0
    out = capsys.readouterr().out
    assert "could not run the hermes CLI" in out
    assert "hermes cron create" in out


# ---------------------------------------------------------------------------
# no Hermes on PATH: degrade honestly instead of printing an unrunnable command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job", [SCAN_JOB, JIRA_JOB])
def test_printable_command_becomes_a_plain_crontab_line(no_hermes_on_path, job):
    """Nothing named `hermes` may appear: an operator without the agent cannot
    run `hermes cron create`, and telling them to is a dead end."""
    line = cronjobs.printable_command(job, "0 9 * * *", "local")

    assert line == f"0 9 * * * flaky-stab {' '.join(job.command)}"
    assert "hermes" not in line
    schedule_fields, command = line.split(" ", 5)[:5], line.split(" ", 5)[5]
    assert len(schedule_fields) == 5          # a valid 5-field crontab time spec
    assert shlex.split(command) == ["flaky-stab", *job.command]


def test_crontab_line_accepts_a_macro_schedule(no_hermes_on_path):
    assert cronjobs.printable_command(SCAN_JOB, "@daily", "local") == \
        "@daily flaky-stab scan --format cron"


def test_crontab_line_command_is_fixed_whatever_the_schedule(no_hermes_on_path):
    """The quoting rule differs by branch and both are deliberate: the Hermes
    form is a shell command, so its user-supplied parts are `shlex.quote`d; a
    crontab line's first fields *are* the time spec, where a quote would make
    cron reject the entry. What matters is the same either way — nothing an
    operator types can extend or replace the command half, which is this job's
    own argv. (`validate_cron_schedule` rejects such a schedule long before
    here; this pins the blast radius if it ever did not.)
    """
    line = cronjobs.printable_command(SCAN_JOB, "0 9 * * * ; rm -rf /", "local")

    assert line.endswith(" flaky-stab scan --format cron")
    _, command = line.split("flaky-stab", 1)
    assert shlex.split("flaky-stab" + command) == ["flaky-stab", "scan", "--format", "cron"]


def test_manual_note_points_at_crontab_not_the_gateway(no_hermes_on_path, capsys):
    cronjobs.print_manual_command(cronjobs.printable_command(SCAN_JOB, "0 9 * * *", "local"))
    out = capsys.readouterr().out
    assert "crontab -e" in out
    assert "FLAKY_STAB_HOME" in out            # cron does not read a shell profile
    assert "gateway" not in out


def test_installed_shim_execs_the_console_script(profile_env, no_hermes_on_path):
    """The shim is retargeted at install time rather than branching inside the
    .sh: `set -euo pipefail` and the exec (which makes the scan's exit code the
    job's exit code) must survive exactly."""
    shim_dst = cronjobs.install_shim(SCAN_JOB.shim_name)
    text = shim_dst.read_text(encoding="utf-8")

    assert "exec flaky-stab scan --format cron" in text
    assert "hermes" not in text.splitlines()[-1]
    assert "set -euo pipefail" in text
    assert text.splitlines()[0] == "#!/usr/bin/env bash"
    assert stat.S_IMODE(os.stat(shim_dst).st_mode) == 0o700


def test_installed_shim_is_byte_identical_when_hermes_is_present(profile_env,
                                                                 hermes_on_path):
    shim_dst = cronjobs.install_shim(JIRA_JOB.shim_name)
    assert shim_dst.read_text(encoding="utf-8") == \
        cronjobs.shim_source(JIRA_JOB.shim_name).read_text(encoding="utf-8")


def test_create_job_without_hermes_prints_the_crontab_line_and_succeeds(
    no_hermes_on_path, monkeypatch, capsys
):
    """No subprocess is attempted: there is nothing to run it with."""
    def never(*a, **k):
        raise AssertionError("subprocess.run must not be called without a hermes CLI")

    monkeypatch.setattr("subprocess.run", never)

    assert cronjobs.create_job(SCAN_JOB, "0 9 * * *", "local") == 0
    out = capsys.readouterr().out
    assert "0 9 * * * flaky-stab scan --format cron" in out
    assert "crontab" in out
    assert "hermes cron create" not in out
