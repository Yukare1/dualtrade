"""The event dispatcher may request reviews, but cannot grant native authority."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest


PATH = Path(__file__).resolve().parents[1] / "scripts" / "event-agent" / "service.py"
spec = importlib.util.spec_from_file_location("event_agent_service", PATH)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def candidate():
    return {"id": "candidate.1", "context": "a" * 64, "expires": 1055,
            "style": 0, "risk_ceiling": 40, "score": 70}


def eligible():
    report = {"issues": [], "context": "a" * 64}
    ea = {"demo": True, "mode": "demo", "orders_enabled": True,
          "day_observed": False, "day_observation_waiver_active": True,
          "day_observation_waiver_until": 1055}
    control = {"mode": "demo", "agent_review_required": True,
               "until": 1055, "styles": ["day"]}
    return report, ea, {}, control, candidate(), 1005


def test_entry_scope_and_expiry_fail_closed():
    args = eligible()
    assert service.entry_allowed(*args)
    for index, key, bad in ((0, "issues", ["EA_STALE"]),
                            (1, "orders_enabled", False),
                            (1, "day_observation_waiver_active", False),
                            (3, "until", 1005),
                            (3, "styles", ["swing"]),
                            (4, "context", "b" * 64)):
        changed = [dict(x) if isinstance(x, dict) else x for x in args]
        changed[index][key] = bad
        assert not service.entry_allowed(*changed)
    assert not service.entry_allowed(*args[:-1], 1055)


def test_claim_survives_restart_and_prevents_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "STATE", tmp_path)
    first = service.connect_state()
    assert service.claim(first, "candidate.1", 1001)
    first.close()
    second = service.connect_state()
    assert not service.claim(second, "candidate.1", 1002)
    service.conclude(second, "candidate.1", "MODEL_TIMEOUT")
    assert second.execute("SELECT outcome,tokens FROM attempts").fetchone() == ("MODEL_TIMEOUT", None)
    second.close()


def test_observation_readonly_preserves_failed_and_separate_styles(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "ROOT", tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    db = sqlite3.connect(runtime / "state.sqlite3")
    db.execute("CREATE TABLE kv(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    day = service.now_ny(1000).date().isoformat()
    rows = [("observation_pass", {"config_hash": "a" * 64}),
            ("observation:" + day, {"failed": True, "seconds": 17}),
            ("swing:observation_pass", {"config_hash": "b" * 64}),
            ("investment:observation:" + day, {"failed": False, "seconds": 0})]
    db.executemany("INSERT INTO kv VALUES(?,?)", [(key, json.dumps(value)) for key, value in rows])
    db.commit()
    before = db.total_changes
    outcome = service.observations(1000, "a" * 64)
    assert outcome["day"]["passed"] is True
    assert outcome["day"]["progress"]["failed"] is True
    assert outcome["swing"]["passed"] is False
    assert outcome["investment"]["progress"]["seconds"] == 0
    assert db.total_changes == before
    db.close()


@pytest.mark.parametrize("mode", ["unavailable", "malformed"])
def test_model_failure_never_publishes(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(service, "STATE", tmp_path)
    db = service.connect_state()
    report, ea, snap, control, request, _ = eligible()
    monkeypatch.setattr(service.time, "time", lambda: 1005)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"request": request, "quote": {"time": 1005}}), encoding="utf-8")
    from hashlib import sha256
    digest = sha256(evidence_path.read_bytes()).hexdigest()
    monkeypatch.setattr(service.agent_bridge, "inspect", lambda cfg, identity, now: {
        "evidence_path": str(evidence_path), "evidence_sha256": digest})
    monkeypatch.setattr(service.agent_bridge, "publish", lambda *args: pytest.fail("published on model failure"))

    def bad_model(*args):
        if mode == "unavailable":
            raise RuntimeError("CODEX_UNAVAILABLE")
        return {"action": "APPROVE"}, None

    outcome = service.handle({}, db, report, ea, snap, control, request, run_model_fn=bad_model)
    assert "CODEX_UNAVAILABLE" in outcome if mode == "unavailable" else outcome.startswith(("KeyError", "ValueError"))
    assert db.execute("SELECT outcome FROM attempts").fetchone()[0] == outcome
    db.close()


def test_validated_rejection_is_published_once(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "STATE", tmp_path)
    db = service.connect_state()
    report, ea, snap, control, request, _ = eligible()
    monkeypatch.setattr(service.time, "time", lambda: 1005)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps({"request": request, "quote": {"time": 1005}}), encoding="utf-8")
    from hashlib import sha256
    digest = sha256(evidence_path.read_bytes()).hexdigest()
    monkeypatch.setattr(service.agent_bridge, "inspect", lambda cfg, identity, now: {
        "evidence_path": str(evidence_path), "evidence_sha256": digest})
    review = {"schema": 1, "request_id": request["id"], "context": request["context"],
              "evidence_sha256": digest, "action": "REJECT", "risk_usd": 0,
              "win_probability": None, "rationale_tr": "Kanıt eksik olduğu için aday mevcut risk kuralları kapsamında reddedildi.",
              "checks": {key: False for key in service.CHECKS}}
    calls = []
    monkeypatch.setattr(service.agent_bridge, "validate_review", lambda *args: calls.append("validate"))
    monkeypatch.setattr(service.agent_bridge, "publish", lambda *args: calls.append("publish"))
    monkeypatch.setattr(service, "bridge", lambda cfg: tmp_path)
    assert service.handle({}, db, report, ea, snap, control, request,
                          run_model_fn=lambda *args: (review, 123)) == "PUBLISHED_REJECT"
    assert calls == ["validate", "publish"]
    assert not service.claim(db, request["id"], 1006)
    assert db.execute("SELECT outcome,tokens FROM attempts").fetchone() == ("PUBLISHED_REJECT", 123)
    db.close()


def test_thirty_minutes_empty_queue_uses_no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "STATE", tmp_path)
    monkeypatch.setattr(service, "load", lambda: {})
    clock = [1000.0]
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(service.time, "sleep", lambda interval: clock.__setitem__(0, clock[0] + interval))
    report = {"issues": [], "observations": {name: {"passed": False, "progress": {}}
                                          for name in ("day", "swing", "investment")}}
    monkeypatch.setattr(service, "health", lambda *args: (report, {}, {}, {}))
    monkeypatch.setattr(service.agent_bridge, "pending", lambda *args: [])
    monkeypatch.setattr(service, "run_model", lambda *args: pytest.fail("empty queue called model"))
    service.service(duration=1800)
    db = sqlite3.connect(tmp_path / "dispatch.sqlite3")
    assert db.execute("SELECT count(*) FROM attempts").fetchone()[0] == 0
    assert clock[0] >= 2800
    db.close()


def test_real_cli_usage_shape_is_counted_without_double_counting_cache():
    line = json.dumps({"type": "turn.completed", "usage": {
        "input_tokens": 15309, "cached_input_tokens": 1000,
        "cache_write_input_tokens": 0, "output_tokens": 207,
        "reasoning_output_tokens": 0}})
    total, usage = service.token_usage('not json\n' + line + '\n')
    assert total == 15516
    assert usage["cached_input_tokens"] == 1000
    assert service.token_usage('{"type":"turn.completed"}') == (None, None)
