"""Evaluate matured, recorded 12-month forecasts without fabricating history.
Run: python -m scripts.evaluate --database /path/to/mstr_monitor.db
"""

import argparse
import json
import sqlite3
from datetime import datetime, timedelta


def evaluate(rows):
    """At most one forecast per UTC day; first valid later quote within seven days.
    Overlapping annual cohorts are descriptive, not independent statistical trials.
    """
    rows = sorted(rows, key=lambda r: r["timestamp_utc"])
    cohorts = {}
    quotes = {}
    for row in rows:
        if row.get("model_version") != "2.0" or row.get("quality") == "stale":
            continue
        created = datetime.fromisoformat(row["timestamp_utc"])
        q = row["market"]["mstr"]
        observed = datetime.fromisoformat(q["observed_at"])
        if abs((created - observed).total_seconds()) > 96 * 3600:
            continue
        cohorts.setdefault(created.date(), row)
        quotes[observed] = q["price"]
    quotes = sorted(quotes.items())
    comparisons = []
    for row in cohorts.values():
        due = datetime.fromisoformat(row["timestamp_utc"]) + timedelta(days=365)
        target = next(
            ((t, p) for t, p in quotes if due <= t <= due + timedelta(days=7)), None
        )
        if not target:
            continue
        base = next(s for s in row["scenarios"] if s["name"] == "base")
        actual = target[1]
        predictions = {
            "scenario_base": base["terminal_price"],
            "unchanged_market_price": row["market"]["mstr"]["price"],
            "unchanged_net_asset_reference": row["backing"]["common_equity_reference"],
        }
        comparisons.append(
            {
                key: abs(value - actual) / actual * 100
                for key, value in predictions.items()
            }
        )
    return {
        "eligible_daily_cohorts": len(cohorts),
        "matured_cohorts": len(comparisons),
        "status": "descriptive_only" if comparisons else "insufficient_matured_history",
        "mean_absolute_percentage_error": {
            key: sum(r[key] for r in comparisons) / len(comparisons)
            for key in comparisons[0]
        }
        if comparisons
        else None,
        "notes": [
            "Forecasts use stored inputs only; no historical reconstruction.",
            "Twelve-month terminal price compared with first observed close at/after day 365 (up to 7 days later).",
            "Unchanged-price and unchanged-net-asset references are benchmarks.",
            "Overlapping cohorts are not independent. No predictive superiority claim.",
            "V1 composite results are archived but excluded; no comparable point-in-time model history.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    # Read-only URI prevents accidental creation or alteration of the production database.
    from pathlib import Path

    conn = sqlite3.connect(
        Path(args.database).resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        rows = [
            json.loads(r[0])
            for r in conn.execute(
                "SELECT raw_payload FROM snapshots_v2 ORDER BY timestamp_utc"
            )
        ]
    finally:
        conn.close()
    print(json.dumps(evaluate(rows), indent=2))


if __name__ == "__main__":
    main()
