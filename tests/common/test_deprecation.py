"""Deprecation warnings for the legacy compatibility surface fire exactly once."""

import json
import logging
import re
from pathlib import Path

import pytest

from flaky_stabilization import config as unified_config
from flaky_stabilization.common import deprecation
from flaky_stabilization.healer.flaky_healer import config as healer_config
from flaky_stabilization.incidents import egress

DEP_LOGGER = "flaky_stabilization.deprecation"


@pytest.fixture(autouse=True)
def _fresh_registry(monkeypatch):
    """Each test sees an empty warned-keys set (the real one is process-wide)."""
    monkeypatch.setattr(deprecation, "_seen", set())


def _dep_records(caplog):
    return [r for r in caplog.records if r.name == DEP_LOGGER]


def test_warn_once_fires_exactly_once_per_key(caplog):
    with caplog.at_level(logging.WARNING, logger=DEP_LOGGER):
        deprecation.warn_once("env:X", "DEPRECATED: X is a legacy fallback.")
        deprecation.warn_once("env:X", "DEPRECATED: X is a legacy fallback.")
        deprecation.warn_once("env:X", "DEPRECATED: X is a legacy fallback.")
    assert len(_dep_records(caplog)) == 1


def test_warn_once_distinct_keys_fire_independently(caplog):
    with caplog.at_level(logging.WARNING, logger=DEP_LOGGER):
        deprecation.warn_once("env:A", "DEPRECATED: A is a legacy fallback.")
        deprecation.warn_once("env:B", "DEPRECATED: B is a legacy fallback.")
        deprecation.warn_once("env:A", "DEPRECATED: A is a legacy fallback.")
    messages = [r.getMessage() for r in _dep_records(caplog)]
    assert len(messages) == 2
    assert any("A" in m for m in messages) and any("B" in m for m in messages)


def test_legacy_env_var_read_warns_exactly_once(monkeypatch, caplog):
    """End to end: a FLAKY_HEALER_* override warns once per process."""
    monkeypatch.setenv("FLAKY_HEALER_SANDBOX", "subprocess")
    with caplog.at_level(logging.WARNING, logger=DEP_LOGGER):
        assert healer_config.sandbox_backend() == "subprocess"
        assert healer_config.sandbox_backend() == "subprocess"
    records = _dep_records(caplog)
    assert len(records) == 1
    message = records[0].getMessage()
    assert message.startswith("DEPRECATED: ")
    assert "FLAKY_HEALER_SANDBOX" in message
    assert "1.0" in message


def test_modern_config_path_does_not_warn(monkeypatch, caplog):
    """Without the legacy env var, the modern config path stays silent."""
    monkeypatch.delenv("FLAKY_HEALER_SANDBOX", raising=False)
    with caplog.at_level(logging.WARNING, logger=DEP_LOGGER):
        assert healer_config.sandbox_backend() in ("auto", "docker", "subprocess")
    assert _dep_records(caplog) == []


# --- the warning must name a key the operator can actually set ---------------

# Every replacement string of the form "`section.key` in
# flaky-stabilization/config.json" is an instruction the operator will follow.
# Five variables used to point at a section that had no matching key at all
# (FLAKY_HEALER_GITHUB_API / _SUBPROC_PASS_ENV / _SUBPROC_ISOLATE_NET /
# _RUN_CONCURRENCY, HERMES_JIRA_STRICT_REDACTION), so the advice could not be
# followed and a 1.0 removal would have deleted the only way to set them.
#
# Only literal references can be checked statically. The remaining sites build
# the name with an f-string (`config._apply_env_overrides`, `logfetch.
# _env_or_config_list`); the first is self-validating because it indexes
# DEFAULTS[section][key] on the same path, and the second is covered by the
# triage suite.
_KEY_REF = re.compile(r"`([a-z_]+)\.([a-z_]+)`\s+(?:key\s+)?in flaky-stabilization/config\.json")


def test_every_config_key_named_in_a_deprecation_message_exists():
    src_root = Path(unified_config.__file__).parent
    found: set[tuple[str, str]] = set()
    for path in src_root.rglob("*.py"):
        for section, key in _KEY_REF.findall(path.read_text(encoding="utf-8")):
            found.add((section, key))
    # Floor, not just non-empty: a phrasing change that silently stopped
    # matching would otherwise turn this into a vacuous pass.
    assert len(found) >= 12, f"only {len(found)} matched — did the message phrasing change?"
    missing = sorted(
        f"{section}.{key}"
        for section, key in found
        if key not in unified_config.DEFAULTS.get(section, {})
    )
    assert not missing, f"deprecation messages point at nonexistent config keys: {missing}"


def test_jira_strict_redaction_key_replaces_the_env_var(tmp_path, monkeypatch, caplog):
    """The canary reads `jira.strict_redaction`; the env var still wins and warns."""
    home = tmp_path / ".hermes"
    (home / "flaky-stabilization").mkdir(parents=True)
    (home / "flaky-stabilization" / "config.json").write_text(
        json.dumps({"jira": {"strict_redaction": True}}), encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_JIRA_STRICT_REDACTION", raising=False)

    with caplog.at_level(logging.WARNING, logger=DEP_LOGGER):
        assert egress.strict_enabled() is True
    assert _dep_records(caplog) == []

    monkeypatch.setenv("HERMES_JIRA_STRICT_REDACTION", "0")
    with caplog.at_level(logging.WARNING, logger=DEP_LOGGER):
        assert egress.strict_enabled() is False
    assert "jira.strict_redaction" in _dep_records(caplog)[0].getMessage()


def test_jira_strict_redaction_defaults_off_and_survives_a_broken_config(tmp_path, monkeypatch):
    """Fail-safe: an unreadable config leaves the canary off, never raising."""
    home = tmp_path / ".hermes"
    (home / "flaky-stabilization").mkdir(parents=True)
    (home / "flaky-stabilization" / "config.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("HERMES_JIRA_STRICT_REDACTION", raising=False)
    assert egress.strict_enabled() is False
