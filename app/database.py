"""V2 tables coexist with legacy tables. Old valuations are never relabeled as V2."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from app.config import Settings, default_settings

DATA_DIR = os.environ.get(
    "DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
)
DB_PATH = os.path.join(DATA_DIR, "mstr_monitor.db")
DEFAULT_CONFIG = default_settings()


@contextmanager
def connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS snapshots_v2 (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp_utc TEXT NOT NULL, raw_payload TEXT NOT NULL)"
        )
        if not conn.execute(
            "SELECT 1 FROM config WHERE key='app_config_v2'"
        ).fetchone():
            cfg = default_settings()
            old = conn.execute(
                "SELECT value FROM config WHERE key='app_config'"
            ).fetchone()
            if old:
                legacy = json.loads(old["value"])
                # Preserve existing capital inputs but discard unsupported dilution/weight heuristics.
                for key in (
                    "btc_holdings",
                    "basic_shares",
                    "total_debt",
                    "preferred_stock",
                    "cash_and_equivalents",
                    "software_ev",
                    "refresh_interval_seconds",
                ):
                    if key in legacy:
                        cfg[key] = legacy[key]
                cfg["annual_carry_cost"] = 0
                cfg["evidence"] = {}
            Settings.model_validate(cfg)
            conn.execute(
                "INSERT INTO config VALUES (?, ?, ?)",
                ("app_config_v2", json.dumps(cfg), now()),
            )


def now():
    return datetime.now(timezone.utc).isoformat()


def get_config():
    with connection() as conn:
        row = conn.execute(
            "SELECT value FROM config WHERE key='app_config_v2'"
        ).fetchone()
        return Settings.model_validate(
            json.loads(row["value"]) if row else default_settings()
        ).model_dump(mode="json")


def update_config(values):
    cfg = Settings.model_validate(values).model_dump(mode="json")
    with connection() as conn:
        conn.execute(
            "INSERT INTO config VALUES ('app_config_v2', ?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (json.dumps(cfg), now()),
        )
    return cfg


def save_snapshot(payload):
    with connection() as conn:
        conn.execute(
            "INSERT INTO snapshots_v2 (timestamp_utc, raw_payload) VALUES (?, ?)",
            (payload["timestamp_utc"], json.dumps(payload, allow_nan=False)),
        )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1096)).isoformat()
        conn.execute("DELETE FROM snapshots_v2 WHERE timestamp_utc < ?", (cutoff,))


def get_recent_snapshots(limit=168):
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, raw_payload FROM snapshots_v2 ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 8760)),),
        ).fetchall()
        return [
            dict(id=r["id"], **json.loads(r["raw_payload"])) for r in reversed(rows)
        ]


def get_latest_snapshot_payload():
    rows = get_recent_snapshots(1)
    return rows[0] if rows else None


def legacy_count():
    with connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='snapshots'"
        ).fetchone()
        return (
            conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
            if exists
            else 0
        )
