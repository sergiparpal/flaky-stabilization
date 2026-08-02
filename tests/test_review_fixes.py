"""Regressions for the code-review round on v0.2.1.

Each test pins one defect that was reproduced before the fix. Grouped by the
module they guard rather than by the review's numbering, so they stay findable
from the code they protect.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from flaky_stabilization.orchestrator import pipeline as P

# ---------------------------------------------------------------------------
# Orchestrator: a stage crash must not lose the pipeline_runs ledger row
# ---------------------------------------------------------------------------


def _pipeline(stages: P.Stages, recorded: list, config: dict | None = None) -> P.Pipeline:
    return P.Pipeline(stages=stages, config=config or {}, record_run=recorded.append)


def _flaky_triage(call, incident_context=None):
    return json.dumps({"success": True, "category": "flaky"})


def _bug_triage(call, incident_context=None):
    return json.dumps({"success": True, "category": "infra"})


HEAL_ARGS = {"log_url_or_path": "/nonexistent.log", "test_id": "t::a", "repo_dir": "/repo"}


@pytest.mark.parametrize(
    "heal_envelope, why",
    [
        ({"report": {"stable": True, "burnin": {"passed": "n/a"}}},
         "a non-numeric burn-in count used to raise ValueError out of run()"),
        ({"report": {"stable": True, "burnin": {"passed": {"nested": 1}}}},
         "a non-scalar burn-in count used to raise TypeError out of run()"),
        ({"report": "burn-in failed"},
         "a non-dict report used to raise AttributeError out of run()"),
    ],
)
def test_malformed_healer_envelope_still_writes_the_ledger_row(heal_envelope, why):
    recorded: list = []
    pipe = _pipeline(
        P.Stages(triage=_flaky_triage,
                 heal=lambda call: json.dumps(heal_envelope),
                 ingest_synthetic=lambda **kw: 1),
        recorded,
    )
    envelope = pipe.run(dict(HEAL_ARGS))
    assert envelope["success"] is True, why
    assert len(recorded) == 1, f"ledger row lost: {why}"


def test_a_raising_pii_gate_fails_closed_and_writes_the_ledger_row():
    recorded: list = []

    def boom(paths):
        raise RuntimeError("gate exploded")

    pipe = _pipeline(
        P.Stages(triage=_bug_triage,
                 bugreport=lambda call: json.dumps({"title": "t", "summary": "s"}),
                 gate=boom,
                 tracker=lambda call: json.dumps({"created": True})),
        recorded,
    )
    envelope = pipe.run({"log_url_or_path": "/nonexistent.log"})

    assert envelope["outcome"] == P.OUTCOME_NEEDS_ATTENTION
    assert len(recorded) == 1
    # Fail CLOSED: the tracker must not have been reached.
    assert "skipped" in envelope["stage_results"]["tracker"]
    assert "error" in envelope["stage_results"]["pii"]


def test_a_gate_returning_a_non_mapping_fails_closed():
    recorded: list = []
    pipe = _pipeline(
        P.Stages(triage=_bug_triage,
                 bugreport=lambda call: json.dumps({"title": "t", "summary": "s"}),
                 gate=lambda paths: "looks fine to me",
                 tracker=lambda call: json.dumps({"created": True})),
        recorded,
    )
    envelope = pipe.run({"log_url_or_path": "/nonexistent.log"})
    assert envelope["outcome"] == P.OUTCOME_NEEDS_ATTENTION
    assert "skipped" in envelope["stage_results"]["tracker"]


def test_spooled_evidence_is_removed_even_when_a_stage_crashes(tmp_path, monkeypatch):
    """The fetched CI log can carry captured output; the cleanup used to sit at
    the tail of run() and be skipped by exactly the crashes that matter."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    pipe = _pipeline(
        P.Stages(
            fetch_ci_logs=lambda call: json.dumps({"filtered_log": "secret-ish log body"}),
            triage=_flaky_triage,
            heal=lambda call: json.dumps(
                {"report": {"stable": True, "burnin": {"passed": "n/a"}}}),
            ingest_synthetic=lambda **kw: 1,
        ),
        [],
    )
    pipe.run({"build_id": "42", "repo": "o/r", "test_id": "t::a", "repo_dir": "/repo"})

    spool = tmp_path / "flaky-stabilization" / "pipeline-evidence"
    assert list(spool.glob("ci-log-*.log")) == []


def test_burnin_repeats_is_bounded_and_degrades():
    assert P._burnin_repeats({"passed": 7}) == 7
    assert P._burnin_repeats({"passed": "5"}) == 5              # historically permissive
    assert P._burnin_repeats({"runs": 3}) == 3                  # falls back to runs
    assert P._burnin_repeats({"passed": "n/a"}) == 1            # garbage degrades
    assert P._burnin_repeats({}) == 1
    assert P._burnin_repeats(None) == 1
    assert P._burnin_repeats({"passed": 10**9}) == P._MAX_BURNIN_FEEDBACK_RUNS


def test_burnin_feedback_gives_each_synthetic_run_its_own_identity():
    """The detective dedups logical runs on (source_file, effective timestamp).
    Every repeat sharing one source_file collapsed the whole feedback loop to a
    single green run in the next detection sweep."""
    calls: list = []
    pipe = _pipeline(
        P.Stages(triage=_flaky_triage,
                 heal=lambda call: json.dumps(
                     {"report": {"stable": True, "burnin": {"passed": 4, "runs": 4}}}),
                 ingest_synthetic=lambda **kw: calls.append(kw) or len(calls)),
        [],
    )
    pipe.run(dict(HEAL_ARGS))

    assert len(calls) == 4
    sources = {c["source_file"] for c in calls}
    assert len(sources) == 4, f"synthetic runs share a dedup key: {sources}"


def test_stabilize_command_reads_a_bare_test_id_as_a_test_id():
    """`startswith("http")` classified `httpclient_timeout` as a log URL."""
    captured: dict = {}

    class _Svc:
        def ensure_initialized(self, *a, **k):
            pass

    def fake_handler(ctx, incidents_service):
        def handler(args, **kwargs):
            captured.update(args)
            return json.dumps({"success": True, "outcome": "needs_attention",
                               "stage_results": {}, "notes": []})
        return handler

    original = P.make_tool_handler
    P.make_tool_handler = fake_handler
    try:
        P.make_command(object(), _Svc())("httpclient_timeout")
    finally:
        P.make_tool_handler = original

    assert captured.get("test_id") == "httpclient_timeout"
    assert "log_url_or_path" not in captured


# ---------------------------------------------------------------------------
# Incidents: the prefetch cache must survive a shutdown/initialize cycle
# ---------------------------------------------------------------------------


def test_prefetch_cache_revives_after_shutdown(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from flaky_stabilization.incidents import IncidentsService

    svc = IncidentsService()
    svc.initialize("s1")
    svc.store.upsert({"key": "INC-1", "summary": "checkout timeout", "status": "Open"})
    assert svc.prefetch("checkout timeout")

    svc.shutdown()
    svc.initialize("s2")
    assert svc.prefetch("checkout timeout"), (
        "shutdown() left the cache permanently closed, so context injection "
        "silently returned nothing for the rest of the process"
    )


def test_prefetch_reopen_is_a_noop_while_open(tmp_path, monkeypatch):
    from flaky_stabilization.incidents.prefetch import PrefetchCache

    cache = PrefetchCache(lambda q: f"built:{q}")
    assert cache.get("x", timeout=5) == "built:x"
    cache.reopen()
    assert cache.contains("x"), "reopen() must not clear a live cache"


# ---------------------------------------------------------------------------
# Healer config: an empty env var means "unset", never "off"
# ---------------------------------------------------------------------------


def test_empty_env_does_not_disable_subprocess_network_isolation(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from flaky_stabilization.healer.flaky_healer import config as hc

    monkeypatch.delenv("FLAKY_HEALER_SUBPROC_ISOLATE_NET", raising=False)
    assert hc.subproc_isolate_net() is True

    monkeypatch.setenv("FLAKY_HEALER_SUBPROC_ISOLATE_NET", "")
    assert hc.subproc_isolate_net() is True, (
        "a bare `export FLAKY_HEALER_SUBPROC_ISOLATE_NET=` silently turned "
        "network isolation off"
    )

    monkeypatch.setenv("FLAKY_HEALER_SUBPROC_ISOLATE_NET", "0")
    assert hc.subproc_isolate_net() is False  # an explicit value still wins


def test_empty_run_concurrency_env_falls_through_to_config(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from flaky_stabilization import config as uc
    from flaky_stabilization.healer.flaky_healer import config as hc

    path = uc.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"healer": {"run_concurrency": 4}}), encoding="utf-8")

    monkeypatch.setenv("FLAKY_HEALER_RUN_CONCURRENCY", "")
    assert hc.run_concurrency() == 4, "an empty env var must not shadow the config value"


# ---------------------------------------------------------------------------
# Triage pattern store: the FTS mirror delete must actually delete
# ---------------------------------------------------------------------------


def test_fts_delete_removes_exactly_one_mirror_row(tmp_path):
    from flaky_stabilization.triage.patterns import PatternStore

    store = PatternStore(tmp_path / "state.db")
    if not store.fts:
        pytest.skip("SQLite built without FTS5")
    try:
        store.record("proj", "sig-aaa", "flaky", "connection reset during checkout")
        store.record("proj", "sig-bbb", "infra", "disk quota exceeded on runner")
        count = lambda: store.conn.execute(  # noqa: E731
            "SELECT COUNT(*) FROM triage_patterns_fts").fetchone()[0]
        assert count() == 2, "record() leaked a stale mirror row"

        store._delete("proj", "sig-aaa")
        left = [r[0] for r in store.conn.execute("SELECT signature FROM triage_patterns_fts")]
        assert left == ["sig-bbb"]
    finally:
        store.close()


def test_fts_delete_is_index_backed_not_a_full_scan(tmp_path):
    """A MATCH against an UNINDEXED column silently matches nothing, so the
    targeted delete is only correct while `signature` stays indexed (schema v2)."""
    from flaky_stabilization.triage.patterns import PatternStore

    store = PatternStore(tmp_path / "state.db")
    if not store.fts:
        pytest.skip("SQLite built without FTS5")
    try:
        plan = "\n".join(
            str(row[3]) for row in store.conn.execute(
                "EXPLAIN QUERY PLAN " + PatternStore._FTS_DELETE_SQL,
                PatternStore._fts_delete_params("proj", "abc123"))
        )
        assert "INDEX 0:M" in plan, f"mirror delete fell back to a full scan:\n{plan}"
    finally:
        store.close()


def test_fuzzy_lookup_cannot_match_a_signature_digest(tmp_path):
    """Indexing `signature` for the targeted delete also put hex digests into the
    searchable token space; the fuzzy query is column-scoped to `sample`."""
    from flaky_stabilization.triage.patterns import PatternStore

    store = PatternStore(tmp_path / "state.db")
    if not store.fts:
        pytest.skip("SQLite built without FTS5")
    try:
        store.record("proj", "deadbeef1234", "flaky", "timeout waiting for selector cart")
        assert store.lookup("proj", "other-sig", "waiting for selector cart") is not None
        assert store.lookup("proj", "other-sig", "deadbeef1234") is None
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Storage: the schema ladder
# ---------------------------------------------------------------------------


def test_v1_mirror_is_rebuilt_with_an_indexed_signature(tmp_path):
    """A state.db left at v1 carries an UNINDEXED `signature`; the v2 step
    rebuilds the mirror and preserves its rows."""
    from flaky_stabilization.storage import state

    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute("""CREATE TABLE schema_version(
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        state._migrate_to_v1(conn)
        conn.execute("INSERT INTO schema_version(version) VALUES (1)")
        conn.execute(
            "INSERT INTO triage_patterns "
            "(project, signature, category, occurrences, first_seen, last_seen, sample) "
            "VALUES ('p','sig1','flaky',1,'2026-01-01','2026-01-01','a sample line')")
        conn.execute(
            "INSERT INTO triage_patterns_fts (sample, project, signature) "
            "VALUES ('a sample line','p','sig1')")
        conn.commit()
        assert state.current_version(conn) == 1
    finally:
        conn.close()

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        state.ensure_schema(conn)
        assert state.current_version(conn) == state.SCHEMA_VERSION
        rows = conn.execute("SELECT signature FROM triage_patterns_fts").fetchall()
        assert [r[0] for r in rows] == ["sig1"], "the v2 rebuild dropped mirror rows"
    conn.close()


def test_ensure_schema_is_idempotent_across_the_whole_ladder(tmp_path):
    from flaky_stabilization.storage import state

    db = tmp_path / "state.db"
    for _ in range(3):
        conn = state.connect(db)
        try:
            assert state.current_version(conn) == state.SCHEMA_VERSION
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# history: db_path_override must name a file inside the home
# ---------------------------------------------------------------------------


def test_db_path_override_equal_to_the_home_is_rejected(tmp_path, monkeypatch):
    """It used to pass containment, then fail with sqlite3's opaque
    'unable to open database file'."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from flaky_stabilization import config as uc
    from flaky_stabilization.history import storage as hs

    path = uc.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"history": {"db_path_override": str(tmp_path)}}),
                    encoding="utf-8")
    hs.reset_for_tests()
    try:
        with pytest.raises(ValueError, match="within the Hermes home"):
            hs.get_db_path()
    finally:
        hs.reset_for_tests()


def test_db_path_override_inside_the_home_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from flaky_stabilization import config as uc
    from flaky_stabilization.history import storage as hs

    target = tmp_path / "nested" / "history.db"
    path = uc.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"history": {"db_path_override": str(target)}}),
                    encoding="utf-8")
    hs.reset_for_tests()
    try:
        assert hs.get_db_path() == Path(target)
    finally:
        hs.reset_for_tests()


# ---------------------------------------------------------------------------
# Jira client: an already-shaped error keeps its status
# ---------------------------------------------------------------------------


def test_response_cap_error_is_not_rewrapped():
    from flaky_stabilization.incidents import jira_client as jc

    class _HugeResponse:
        def read(self, n):
            return b"x" * n

        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        return _HugeResponse()

    original = jc.urllib.request.urlopen
    jc.urllib.request.urlopen = fake_urlopen
    try:
        with pytest.raises(jc.JiraError) as excinfo:
            jc._urllib_transport("GET", "https://x.example/y", {}, None, 5.0)
    finally:
        jc.urllib.request.urlopen = original

    assert "safety cap" in str(excinfo.value)
    assert "request failed" not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Config: the detective section documents every key the stage honours
# ---------------------------------------------------------------------------


def test_unified_and_detective_detective_defaults_agree():
    from flaky_stabilization import config as uc
    from flaky_stabilization.detective import config as dc

    assert set(uc.DEFAULTS["detective"]) == set(dc.DEFAULT_CONFIG)
    for key, value in dc.DEFAULT_CONFIG.items():
        assert uc.DEFAULTS["detective"][key] == value, key
