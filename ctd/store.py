"""SQLite state: contract versions, handovers, append-only audit log."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import os

DB = Path(os.environ.get("CTD_DB") or Path(__file__).resolve().parent.parent / "data" / "state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS contracts (
  contract_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY (contract_id, version));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL,
  subject TEXT NOT NULL, reason TEXT NOT NULL, diff TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS handovers (
  handover_id TEXT PRIMARY KEY, contract_id TEXT NOT NULL, pair TEXT NOT NULL, body TEXT NOT NULL,
  state TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, handover_id TEXT NOT NULL, to_state TEXT NOT NULL,
  by TEXT NOT NULL, at TEXT NOT NULL, reason TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, body TEXT NOT NULL, at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS tr_no_update BEFORE UPDATE ON transitions BEGIN SELECT RAISE(ABORT, 'transitions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS tr_no_delete BEFORE DELETE ON transitions BEGIN SELECT RAISE(ABORT, 'transitions are append-only'); END;
"""


import threading

WRITE_LOCK = threading.RLock()  # one writer at a time for multi-step actions (decide, new version); single process demo server


def now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def reset() -> None:
    DB.unlink(missing_ok=True)
    conn().close()


def audit(actor: str, action: str, subject: str, reason: str, diff: str = "") -> None:
    with conn() as c:
        c.execute("INSERT INTO audit (at, actor, action, subject, reason, diff) VALUES (?,?,?,?,?,?)",
                  (now(), actor, action, subject, reason, diff))


def audit_rows(limit: int = 50) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))]


def put_decision(key: str, body: dict) -> None:
    with conn() as c:
        c.execute("INSERT INTO decisions (key, body, at) VALUES (?,?,?)", (key, json.dumps(body), now()))


def decisions(key: str) -> list[dict]:
    with conn() as c:
        return [json.loads(r["body"]) | {"at": r["at"]} for r in c.execute("SELECT * FROM decisions WHERE key=? ORDER BY id", (key,))]
