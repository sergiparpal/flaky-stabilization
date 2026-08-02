"""Installer for the plugin's no-agent cron jobs (the detection scan, the Jira sync).

Both jobs follow the same recipe: resolve the shim shipped in the checkout's
``scripts/``, write it into ``<hermes_home>/scripts/`` owner-only, then create the
job once via the single sanctioned ``hermes cron create`` subprocess — falling
back to *printing* the command when the gateway is not configured.

With no Hermes CLI on ``PATH`` the whole recipe degrades honestly rather than
printing a command the operator cannot run: the installed shim execs the
``flaky-stab`` console script directly, and the printed command becomes a plain
crontab line. One probe (:func:`hermes_cli_available`) drives both, so the two
halves cannot disagree.

That recipe used to exist twice: ``detective.cli`` factored it into helpers, and
``cli._install_jira_sync_job`` re-inlined it. The copies had already drifted —
the re-inlined one never chmod'd ``<hermes_home>/scripts/`` to 0700, and it
derived the shim source with ``parents[1]`` where the original used
``parents[2]`` (both happened to land on the checkout root only because the two
files sit at different depths). It also copied the shim with no existence check
and no ``OSError`` guard, so a site-packages install without ``scripts/`` raised
an uncaught ``FileNotFoundError`` out of the CLI.

The shim source is derived from *this* module's location, once.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR_NAME = "scripts"


@dataclass(frozen=True)
class CronJob:
    """One installable no-agent job.

    Built at *call* time from the caller's module constants, never frozen at
    import: ``tests`` monkeypatch ``cli.SHIM_NAME`` to simulate a missing shim.
    """

    shim_name: str   # e.g. "flaky-stab-scan.sh"
    job_name: str    # e.g. "flaky-stabilization" — the `hermes cron` job id
    description: str  # e.g. "cron job" — used in the operator-facing messages
    # The `flaky-stab` argv tail this job runs, e.g. ("scan", "--format", "cron").
    # It duplicates the shim's exec line by design — the shim is what the gateway
    # runs, this is what a plain crontab line runs — and
    # ``test_cronjobs`` asserts the two stay identical.
    command: tuple[str, ...] = ()


# The shipped shims exec through the Hermes CLI; with no Hermes on the box there
# is no `hermes` to exec through, so the installed copy targets the console
# script directly. Rewritten at install time rather than branched inside the .sh:
# the shims are deliberately minimal, and `set -euo pipefail` + `exec` semantics
# (the scan's exit code becoming the job's exit code) must survive untouched.
HERMES_EXEC_PREFIX = "exec hermes flaky-stab "
STANDALONE_EXEC_PREFIX = "exec flaky-stab "


def hermes_cli_available() -> bool:
    """True when the ``hermes`` CLI is on ``PATH``.

    The one probe both the shim rewrite and the printed command consult, so an
    install cannot produce a Hermes-flavoured crontab line next to a
    Hermes-free shim.
    """
    return shutil.which("hermes") is not None


def shim_source(shim_name: str) -> Path:
    """``<plugin checkout>/scripts/<shim_name>``."""
    return Path(__file__).resolve().parents[1] / SCRIPTS_DIR_NAME / shim_name


def missing_shim_error(shim_name: str) -> str | None:
    """``None`` when the shim source exists, else the operator-facing message.

    Callers check this *before* persisting anything, so a broken install (e.g.
    site-packages without the checkout's ``scripts/``) fails cleanly rather than
    leaving a half-written config behind. Creates nothing.
    """
    src = shim_source(shim_name)
    if src.exists():
        return None
    return (f"cron shim source not found at {src}; reinstall the plugin from a "
            f"checkout that ships {SCRIPTS_DIR_NAME}/{shim_name}, or copy that "
            f"script into <hermes_home>/{SCRIPTS_DIR_NAME}/ yourself.")


def shim_text(shim_name: str) -> str:
    """The shim's contents as they should land on disk for *this* box.

    Byte-identical to the shipped file when the Hermes CLI is present; with it
    absent, the single ``exec hermes flaky-stab …`` line is retargeted at the
    ``flaky-stab`` console script. Nothing else is touched.
    """
    text = shim_source(shim_name).read_text(encoding="utf-8")
    if hermes_cli_available():
        return text
    return "".join(
        STANDALONE_EXEC_PREFIX + line[len(HERMES_EXEC_PREFIX):]
        if line.startswith(HERMES_EXEC_PREFIX) else line
        for line in text.splitlines(keepends=True)
    )


def install_shim(shim_name: str) -> Path:
    """Write the shim into ``<hermes_home>/scripts/``; return its path.

    Both the directory and the script are 0700: whatever schedules this job
    executes what lands here, so another local user must not be able to read —
    let alone replace — either. Raises ``OSError`` on failure; callers turn that
    into a one-line error.
    """
    from . import paths

    scripts_dir = paths.get_hermes_home() / SCRIPTS_DIR_NAME  # must live here (§1.3)
    scripts_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(scripts_dir, 0o700)
    except OSError:
        pass  # best-effort: a filesystem without POSIX modes must not break install
    shim_dst = scripts_dir / shim_name
    # Written rather than copied so the exec line can be retargeted for a box
    # with no Hermes; the shipped file is reproduced verbatim when there is one.
    shim_dst.write_text(shim_text(shim_name), encoding="utf-8")
    try:
        os.chmod(shim_dst, 0o700)
    except OSError:
        pass
    return shim_dst


def crontab_line(job: CronJob, schedule: str) -> str:
    """A plain crontab entry for *job* — the no-Hermes form of scheduling.

    The schedule is emitted bare: a crontab line's first five fields *are* the
    time spec, so ``shlex.quote`` would turn a valid entry into one cron rejects.
    It is safe to do so because nothing here reaches a shell as a command —
    callers validate the schedule with ``domain.validate_cron_schedule`` (which
    rejects leading dashes and shell metacharacters) before anything is
    installed, and the command half of the line is fixed text plus the job's own
    argv, never operator input.
    """
    command = " ".join(("flaky-stab", *job.command))
    return f"{' '.join(schedule.split())} {command}"


def printable_command(job: CronJob, schedule: str, deliver: str) -> str:
    """The copy-paste command that schedules *job*.

    With the Hermes CLI on ``PATH`` that is ``hermes cron create``; without it,
    a plain crontab line (``deliver`` is a gateway concept and does not apply —
    cron mails the job's output to the local user).

    ``shlex.quote`` guards the user-supplied parts of the Hermes form so the
    printed command stays a single valid shell command even when a schedule or
    delivery channel contains spaces or shell metacharacters. (Real job creation
    uses argv, so it was never injectable; this only hardens the human-facing
    fallback string.)
    """
    if not hermes_cli_available():
        return crontab_line(job, schedule)
    return (f"hermes cron create {shlex.quote(schedule)} --no-agent "
            f"--script {job.shim_name} --deliver {shlex.quote(deliver)} "
            f"--name {job.job_name}")


def gateway_note() -> str:
    return ("Note: the gateway daemon must be running for the job to fire "
            "(`hermes gateway install`, then `hermes gateway`).")


def crontab_note() -> str:
    return ("Note: add that line with `crontab -e`; cron mails the job's output "
            "to the local user. Set FLAKY_STAB_HOME in the crontab too if the "
            "data lives outside ~/.hermes — cron does not read your shell "
            "profile.")


def manual_note() -> str:
    """The follow-up note for whichever scheduler the printed command targets."""
    return gateway_note() if hermes_cli_available() else crontab_note()


def print_manual_command(printable: str) -> None:
    """Print the manual scheduling command plus the note that goes with it."""
    print(f"  {printable}")
    print(manual_note())


def create_job(job: CronJob, schedule: str, deliver: str) -> int:
    """The one sanctioned subprocess: create *job* once.

    A missing hermes CLI or an unconfigured gateway is not an error — it prints
    the exact command to run by hand. (A cron-run session cannot create cron
    jobs, so this is only ever reached from the standalone CLI.)
    """
    printable = printable_command(job, schedule, deliver)
    if not hermes_cli_available():
        # No Hermes to create the job with, and nothing to gain from proving it
        # by subprocess: hand the operator a crontab line and exit successfully.
        # The shim installed above already execs `flaky-stab` directly.
        print("the hermes CLI is not installed; to schedule the "
              f"{job.description} with cron, add this line to your crontab:")
        print_manual_command(printable)
        return 0
    cron_cmd = ["hermes", "cron", "create", schedule, "--no-agent",
                "--script", job.shim_name, "--deliver", deliver,
                "--name", job.job_name]
    try:
        try:
            result = subprocess.run(
                cron_cmd, capture_output=True, text=True, timeout=30)
        except TypeError as exc:
            # Compatibility with minimal test/embedding shims that implement
            # the older subprocess.run surface without a timeout keyword.
            if "timeout" not in str(exc):
                raise
            result = subprocess.run(cron_cmd, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        print("timed out creating the cron job. To create it manually, run:")
        print_manual_command(printable)
        return 1
    except (FileNotFoundError, OSError) as exc:
        print(f"could not run the hermes CLI ({exc}). To create the job, run:")
        print_manual_command(printable)
        return 0

    if result.returncode == 0:
        out = (result.stdout or "").strip()
        if out:
            print(out)
        print(f"created cron job '{job.job_name}' (verify with `hermes cron list`).")
        return 0

    # Non-zero (e.g. gateway not configured): fall back to printing the command.
    detail = (result.stderr or result.stdout or "").strip()
    if detail:
        print(detail)
    print(f"could not create the {job.description} automatically. To create it, run:")
    print_manual_command(printable)
    return 1
