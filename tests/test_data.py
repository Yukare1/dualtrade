import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from dualtrade.storage import Store, write_json, read_json
from dualtrade.fundamentals import point_in_time
from dualtrade.collector import encode_snapshot, resolve_earnings, Collector
from dualtrade.config import load, config_hash
from dualtrade.cli import start_demo
from dualtrade.broker import BrokerError


def test_latest_restatement_not_available_in_past():
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"val": 10, "accn": "old", "end": "2025-12-31"},
        {"val": 20, "accn": "new", "end": "2025-12-31"},
        {"val": 99, "accn": "unknown", "end": "2025-12-31"}]}}}}}
    acceptance = {"old":"2026-01-20T12:00:00Z", "new":"2026-05-20T12:00:00Z"}
    at = point_in_time(facts, acceptance, "2026-03-01T00:00:00Z")
    assert [r["val"] for r in at["facts"]["us-gaap"]["Revenues"]["units"]["USD"]] == [10]
    assert len(facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"]) == 3


def test_quota_persists_and_failed_request_counts(tmp_path):
    path = tmp_path / "state.db"
    s = Store(path)
    s.reserve("alpha", "2026-10-22", 2)
    Store(path).reserve("alpha", "2026-10-22", 2)
    with pytest.raises(RuntimeError): s.reserve("alpha", "2026-10-22", 2)
    s.reserve("alpha", "2026-10-23", 2)


def test_atomic_snapshot_and_end_count(tmp_path):
    cfg = load()
    snap = {"time": 1, "mode": "stop", "ready": False, "login": 123, "server": "Demo", "observation_ready": False,
            "risk":cfg["risk"], "contracts":[], "events":[{"time":10000}]}
    lines = encode_snapshot(snap).splitlines()
    assert len(lines[0].split("|")) == 16
    assert lines[-1] == "END|4"
    snap["server"] = "bad|value"
    with pytest.raises(ValueError): encode_snapshot(snap)
    write_json(tmp_path/"a.json", {"test": 1})
    assert read_json(tmp_path/"a.json") == {"test": 1}


def test_missing_and_conflicting_earnings_block():
    now = datetime(2026,10,22,tzinfo=timezone.utc).timestamp()
    assert resolve_earnings("AAPL", [], {}, now)[1] == "EARNINGS_COVERAGE_UNKNOWN"
    rows = [{"symbol":"AAPL", "reportDate": "2026-10-28"}, {"symbol":"AAPL", "reportDate":"2026-10-29"}]
    assert resolve_earnings("AAPL", rows, {}, now)[1] == "EARNINGS_DATES_CONFLICT"
    override = {"AAPL": {"verified":True, "source":"https://investor.apple.com/", "date":"2026-10-29", "checked_at":now}}
    assert resolve_earnings("AAPL", rows, override, now)[0]["date"] == "2026-10-29"


def test_unknown_health_and_future_timestamps_fail():
    assert not Collector.healthy({}, 1000, 100)
    assert not Collector.healthy({"success":2000}, 1000, 100)
    assert not Collector.healthy({"success":999,"error":"quota"},1000,100)
    assert Collector.healthy({"success":999,"data":[]},1000,100)


def test_broker_failure_preserves_known_exit_obligations(tmp_path):
    cfg = load(); cfg["bridge_directory"] = str(tmp_path)
    class Offline:
        def inspect(self): raise BrokerError("OFFLINE")
    old = {"contracts":[], "events":[{"time":10000}]}
    write_json(tmp_path/"snapshot.json", old)
    collector = Collector(cfg, broker=Offline(), store=Store(tmp_path/"state.db"))
    snap = collector.cycle()
    assert not snap["ready"] and snap["events"] == old["events"]


def test_cannot_start_without_observation_or_ea(tmp_path):
    cfg = load(); cfg["bridge_directory"] = str(tmp_path)
    result = start_demo(cfg, Store(tmp_path/"state.db"))
    assert not result["started"]
    assert not (tmp_path/"control.json").exists()


def test_observation_missing_start_and_outage_do_not_pass(tmp_path):
    cfg = load(); cfg["bridge_directory"] = str(tmp_path)
    c = Collector(cfg, broker=object(), store=Store(tmp_path/"state.db"))
    for now in range(2000, 10000, 5): c.observation(now, True, (1000, 10000), "hash")
    assert c.store.get("observation_pass") is None


def test_observation_whole_session_required(tmp_path):
    cfg = load(); cfg["bridge_directory"] = str(tmp_path)
    c = Collector(cfg, broker=object(), store=Store(tmp_path/"state.db"))
    for now in range(1900, 9706, 5): c.observation(now, True, (1000, 10000), "hash")
    assert c.store.get("observation_pass")["config_hash"] == "hash"
