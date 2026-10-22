from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(10):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, indent=2, allow_nan=False, default=str) + "\n")


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS budgets(provider TEXT, day TEXT, used INTEGER, PRIMARY KEY(provider,day));
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, time REAL, event TEXT, payload TEXT);
        """)

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, json.dumps(value, allow_nan=False, default=str)))

    def reserve(self, provider: str, day: str, limit: int):
        # Every attempted network call, including failures and retries, consumes quota.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute("SELECT used FROM budgets WHERE provider=? AND day=?", (provider, day)).fetchone()
            used = row[0] if row else 0
            if used >= limit:
                raise RuntimeError(f"{provider}: daily request budget exhausted")
            self.db.execute("INSERT OR REPLACE INTO budgets VALUES (?,?,?)", (provider, day, used + 1))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def audit(self, event, payload):
        with self.db:
            self.db.execute("INSERT INTO audit(time,event,payload) VALUES (?,?,?)", (time.time(), event, json.dumps(payload, default=str)))

    def budgets(self):
        return [dict(zip(("provider", "day", "used"), row)) for row in self.db.execute("SELECT * FROM budgets ORDER BY day DESC")]


@contextlib.contextmanager
def process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+b")
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                f.write(b"0"); f.flush(); f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        f.close()
