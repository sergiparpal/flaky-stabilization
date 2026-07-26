"""Deprecation warnings for the legacy compatibility surface fire exactly once."""

import logging

import pytest

from flaky_stabilization.common import deprecation
from flaky_stabilization.healer.flaky_healer import config as healer_config

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
