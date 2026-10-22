"""Transient polling failures block dispatch and require a new health check."""
import importlib.util
import json
from pathlib import Path
import sqlite3

import psutil
import pytest


PATH = Path(__file__).resolve().parents[1] / "scripts/event-agent/service.py"
spec = importlib.util.spec_from_file_location("resilient_event_service", PATH)
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "STATE", tmp_path)
    monkeypatch.setattr(service, "load", lambda: {})
    clock = [1000.0]
    monkeypatch.setattr(service.time, "time", lambda: clock[0])
    monkeypatch.setattr(service.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(service.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(service, "handle", lambda *args, **kwargs: pytest.fail("unexpected dispatch"))
    monkeypatch.setattr(service, "notify_windows", lambda *args: None)
    return tmp_path


def healthy():
    return ({"issues": [], "observations": {name: {"passed": False, "progress": {}}
            for name in ("day", "swing", "investment")}}, {}, {}, {})


@pytest.mark.parametrize("failure", [PermissionError("sharing collision"),
    json.JSONDecodeError("partial JSON", "{", 1), UnicodeDecodeError("utf8", b"\xff", 0, 1, "bad"),
    sqlite3.OperationalError("database is locked"), psutil.AccessDenied(123)])
def test_failed_health_recovers_only_after_fresh_check(isolated, monkeypatch, failure):
    calls = []
    def health(*args):
        calls.append("health")
        if len(calls) == 1:
            raise failure
        assert json.loads((isolated / "status.json").read_text())["issues"] == ["SERVICE_CYCLE_FAILED"]
        result = healthy()
        service.write_json(isolated / "status.json", result[0])
        return result
    monkeypatch.setattr(service, "health", health)
    def pending(*args):
        calls.append("pending")
        return []
    monkeypatch.setattr(service.agent_bridge, "pending", pending)
    service.service(duration=1)
    assert calls == ["health", "health", "pending"]
    assert json.loads((isolated / "status.json").read_text())["issues"] == []
    assert json.loads((isolated / "service-errors.jsonl").read_text())["error"]["type"] == type(failure).__name__


def test_pending_failure_invalidates_cached_health(isolated, monkeypatch):
    calls = []
    def health(*args):
        calls.append("health")
        result = healthy()
        if len(calls) > 1:
            result[0]["issues"] = ["EA_STALE"]
        return result
    monkeypatch.setattr(service, "health", health)
    def pending(*args):
        calls.append("pending")
        raise PermissionError("transient directory read")
    monkeypatch.setattr(service.agent_bridge, "pending", pending)
    service.service(duration=1)
    assert calls == ["health", "pending", "health"]


def test_once_reports_failure_and_closes_database(isolated, monkeypatch):
    db = service.connect_state()
    monkeypatch.setattr(service, "connect_state", lambda: db)
    monkeypatch.setattr(service, "health", lambda *args: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        service.service(once=True, dry_run=True)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")
    assert json.loads((isolated / "status.json").read_text())["issues"] == ["SERVICE_CYCLE_FAILED"]


def test_diagnostic_write_failure_does_not_prevent_recovery(isolated, monkeypatch):
    calls = []
    def health(*args):
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("read failed")
        return healthy()
    monkeypatch.setattr(service, "health", health)
    monkeypatch.setattr(service, "write_json", lambda *args: (_ for _ in ()).throw(PermissionError("status locked")))
    monkeypatch.setattr(service.agent_bridge, "pending", lambda *args: [])
    service.service(duration=1)
    assert len(calls) == 2
    assert (isolated / "service-errors.jsonl").exists()


def test_unexpected_failure_is_fatal_with_traceback_and_closed_db(isolated, monkeypatch):
    db = service.connect_state()
    monkeypatch.setattr(service, "connect_state", lambda: db)
    monkeypatch.setattr(service, "health", lambda *args: (_ for _ in ()).throw(RuntimeError("unexpected bug")))
    monkeypatch.setattr(service, "main", lambda: service.service(once=True))
    with pytest.raises(RuntimeError, match="unexpected bug"):
        service.entrypoint()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")
    error = json.loads((isolated / "service-errors.jsonl").read_text())
    assert error["issues"] == ["SERVICE_FATAL"]
    assert "RuntimeError: unexpected bug" in error["traceback"]


def test_error_log_rotates_to_one_backup(isolated, monkeypatch):
    monkeypatch.setattr(service, "ERROR_LOG_BYTES", 1)
    for _ in range(4):
        service.record_failure(PermissionError("locked"))
    assert sorted(path.name for path in isolated.glob("service-errors*")) == ["service-errors.jsonl", "service-errors.jsonl.1"]
    assert len((isolated / "service-errors.jsonl").read_text().splitlines()) == 1


def test_process_race_is_unhealthy(monkeypatch):
    monkeypatch.setattr(service.psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(service.psutil, "Process", lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
    assert service.process_alive(123) is False
