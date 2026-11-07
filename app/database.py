import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
DB_PATH = os.path.join(DATA_DIR, "mstr_monitor.db")

DEFAULT_CONFIG: Dict[str, Any] = {
    # Treasury & Balance Sheet Parameters (Updated automatically or manually)
    "btc_holdings": 849327.0,
    "btc_avg_cost": 75451.0,
    "basic_shares": 422350000.0,
    "diluted_shares": 465000000.0,
    "total_debt": 8200000000.0,
    "preferred_stock": 1500000000.0,
    "cash_and_equivalents": 500000000.0,
    "software_ev": 1200000000.0,
    "annual_software_revenue": 465000000.0,
    "analyst_target_mean": 233.27,
    "analyst_target_high": 435.00,
    "analyst_target_low": 165.00,
    "analyst_count": 14,
    # Valuation Model Assumptions
    "target_basic_mnav": 1.65,
    "target_ev_mnav": 1.60,
    "annual_btc_yield_pct": 15.0,
    "discount_rate_pct": 8.0,
    "yield_horizon_years": 5.0,
    "optionality_premium_pct": 15.0,
    "auto_sync_fundamentals": True,
    # Weights for the 7 Valuation Metrics (must sum to 100 or will be normalized)
    "weights": {
        "target_mnav": 22.0,
        "ev_adjusted_mnav": 20.0,
        "btc_yield_dcf": 18.0,
        "ols_regression": 15.0,
        "analyst_consensus": 10.0,
        "balance_sheet_mtm": 10.0,
        "basic_nav_floor": 5.0,
    },
    # Refresh Interval in seconds (Default: 3600s = 1 hour)
    "refresh_interval_seconds": 3600,
}


def get_connection() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_utc TEXT NOT NULL,
                    mstr_price REAL NOT NULL,
                    btc_price REAL NOT NULL,
                    composite_fair_value REAL NOT NULL,
                    upside_pct REAL NOT NULL,
                    current_basic_mnav REAL NOT NULL,
                    current_ev_mnav REAL NOT NULL,
                    basic_nav_floor REAL NOT NULL,
                    target_mnav_val REAL NOT NULL,
                    ev_adjusted_val REAL NOT NULL,
                    btc_yield_dcf_val REAL NOT NULL,
                    ols_regression_val REAL NOT NULL,
                    analyst_consensus_val REAL NOT NULL,
                    balance_sheet_mtm_val REAL NOT NULL,
                    verdict TEXT NOT NULL,
                    raw_payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
                ON snapshots (timestamp_utc DESC)
                """
            )

            # Initialize default config if not exists
            cur = conn.execute("SELECT value FROM config WHERE key = 'app_config'")
            row = cur.fetchone()
            if not row:
                now_str = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO config (key, value, updated_at) VALUES (?, ?, ?)",
                    ("app_config", json.dumps(DEFAULT_CONFIG), now_str),
                )
    finally:
        conn.close()


def get_config() -> Dict[str, Any]:
    conn = get_connection()
    try:
        cur = conn.execute("SELECT value FROM config WHERE key = 'app_config'")
        row = cur.fetchone()
        if not row:
            return dict(DEFAULT_CONFIG)
        stored = json.loads(row["value"])
        merged = dict(DEFAULT_CONFIG)
        merged.update(stored)
        if "weights" in stored and isinstance(stored["weights"], dict):
            w = dict(DEFAULT_CONFIG["weights"])
            w.update(stored["weights"])
            merged["weights"] = w
        return merged
    finally:
        conn.close()


def update_config(new_values: Dict[str, Any]) -> Dict[str, Any]:
    current = get_config()
    for k, v in new_values.items():
        if k == "weights" and isinstance(v, dict):
            current["weights"].update({wk: float(wv) for wk, wv in v.items() if wk in current["weights"]})
        elif k in current:
            current[k] = v

    now_str = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO config (key, value, updated_at)
                VALUES ('app_config', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (json.dumps(current), now_str),
            )
    finally:
        conn.close()
    return current


def save_snapshot(valuation_data: Dict[str, Any]) -> None:
    conn = get_connection()
    try:
        models_map = {m["id"]: m["fair_value"] for m in valuation_data["models"]}
        with conn:
            conn.execute(
                """
                INSERT INTO snapshots (
                    timestamp_utc,
                    mstr_price,
                    btc_price,
                    composite_fair_value,
                    upside_pct,
                    current_basic_mnav,
                    current_ev_mnav,
                    basic_nav_floor,
                    target_mnav_val,
                    ev_adjusted_val,
                    btc_yield_dcf_val,
                    ols_regression_val,
                    analyst_consensus_val,
                    balance_sheet_mtm_val,
                    verdict,
                    raw_payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    valuation_data["timestamp_utc"],
                    valuation_data["market"]["mstr_price"],
                    valuation_data["market"]["btc_price"],
                    valuation_data["composite"]["fair_value"],
                    valuation_data["composite"]["upside_pct"],
                    valuation_data["mnav_metrics"]["current_basic_mnav"],
                    valuation_data["mnav_metrics"]["current_ev_mnav"],
                    models_map.get("basic_nav_floor", 0.0),
                    models_map.get("target_mnav", 0.0),
                    models_map.get("ev_adjusted_mnav", 0.0),
                    models_map.get("btc_yield_dcf", 0.0),
                    models_map.get("ols_regression", 0.0),
                    models_map.get("analyst_consensus", 0.0),
                    models_map.get("balance_sheet_mtm", 0.0),
                    valuation_data["composite"]["verdict_code"],
                    json.dumps(valuation_data),
                ),
            )
            # Keep up to 2,160 hourly snapshots (~90 days of hourly logs)
            conn.execute(
                """
                DELETE FROM snapshots
                WHERE id NOT IN (
                    SELECT id FROM snapshots ORDER BY id DESC LIMIT 2160
                )
                """
            )
    finally:
        conn.close()


def get_recent_snapshots(limit: int = 168) -> List[Dict[str, Any]]:
    """Return recent hourly snapshots (default 168 = 7 days of hourly data)."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            SELECT
                id,
                timestamp_utc,
                mstr_price,
                btc_price,
                composite_fair_value,
                upside_pct,
                current_basic_mnav,
                current_ev_mnav,
                basic_nav_floor,
                target_mnav_val,
                ev_adjusted_val,
                btc_yield_dcf_val,
                ols_regression_val,
                analyst_consensus_val,
                balance_sheet_mtm_val,
                verdict
            FROM snapshots
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        rows.reverse()
        return rows
    finally:
        conn.close()


def get_latest_snapshot_payload() -> Optional[Dict[str, Any]]:
    conn = get_connection()
    try:
        cur = conn.execute("SELECT raw_payload FROM snapshots ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if row:
            return json.loads(row["raw_payload"])
        return None
    finally:
        conn.close()
