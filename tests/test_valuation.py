import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import httpx
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.config import Settings
from app.valuation_engine import (
    MarketDataError,
    asset_backing,
    build_valuation,
    changes,
    fetch_quote,
    project,
)
from app import database as db
from app import main


def market(now=None):
    now = now or datetime.now(timezone.utc)
    # Synthetic unit-test observations, never served by the production feed.
    return {
        k: {
            "price": p,
            "observed_at": now.isoformat(),
            "retrieved_at": now.isoformat(),
            "source": "SYNTHETIC TEST",
            "source_url": "https://example.test/",
            "kind": "synthetic",
        }
        for k, p in [("mstr", 100), ("btc", 100000)]
    }


def config():
    c = Settings().model_dump(mode="json")
    c.update(
        btc_holdings=1000,
        basic_shares=1000000,
        total_debt=10000000,
        preferred_stock=5000000,
        cash_and_equivalents=2000000,
        software_ev=3000000,
        annual_carry_cost=1000000,
        discount_rate_pct=10,
        funding_fee_pct=0,
    )
    return c


class ValuationTests(unittest.TestCase):
    def test_net_claims(self):
        b = asset_backing(config(), 100000)
        self.assertEqual(b["net_asset_per_share"], 90)
        self.assertEqual(b["gross_btc_per_share_usd"], 100)

    def test_conversion_releases_only_converted_claims(self):
        c = config()
        c.update(
            converted_debt=4000000,
            converted_preferred=1000000,
            conversion_new_shares=100000,
        )
        b = asset_backing(c, 100000)
        self.assertAlmostEqual(b["converted_net_asset_per_share"], 95000000 / 1100000)
        self.assertEqual(b["remaining_debt"], 6000000)
        self.assertEqual(b["remaining_preferred"], 4000000)

    def test_debt_does_not_create_free_net_assets(self):
        c = config()
        c.update(new_debt_usd=10000000, debt_rate_pct=8)
        s = {"btc_change_pct": 0, "asset_multiple": 1}
        funded = project(c, 100000, 100, s)
        plain = project(c, 100000, 100, s, False)
        self.assertAlmostEqual(
            funded["net_assets_per_share"] - plain["net_assets_per_share"], -0.8
        )
        self.assertAlmostEqual(funded["present_value"], funded["terminal_price"] / 1.1)

    def test_preferred_carry_and_claim(self):
        c = config()
        c.update(new_preferred_usd=10000000, preferred_rate_pct=10)
        s = {"btc_change_pct": 0, "asset_multiple": 1}
        self.assertAlmostEqual(
            project(c, 100000, 100, s)["net_assets_per_share"]
            - project(c, 100000, 100, s, False)["net_assets_per_share"],
            -1,
        )

    def test_equity_issuance_accretive_above_nav_dilutive_below(self):
        c = config()
        c["new_equity_usd"] = 10000000
        s = {"btc_change_pct": 0, "asset_multiple": 1}
        base = project(c, 100000, 100, s, False)["net_assets_per_share"]
        for issue, sign in [(150, 1), (50, -1)]:
            c["equity_issue_price"] = issue
            self.assertGreater(
                (project(c, 100000, 100, s)["net_assets_per_share"] - base) * sign, 0
            )

    def test_funding_fees_reduce_btc(self):
        c = config()
        c.update(new_debt_usd=10000000, funding_fee_pct=2)
        s = {"btc_change_pct": 0, "asset_multiple": 1}
        self.assertEqual(project(c, 100000, 100, s)["holdings"], 1098)

    def test_net_insolvency_visible_common_price_capped_at_zero(self):
        c = config()
        c["total_debt"] = 200000000
        b = asset_backing(c, 100000)
        self.assertLess(b["net_asset_per_share"], 0)
        self.assertEqual(b["common_equity_reference"], 0)
        self.assertEqual(
            project(c, 100000, 100, {"btc_change_pct": 0, "asset_multiple": 1})[
                "present_value"
            ],
            0,
        )

    def test_percent_denominators(self):
        d = changes(210.82, 128.93)
        self.assertAlmostEqual(d["upside_pct"], 63.515, places=2)
        self.assertAlmostEqual(d["discount_pct"], 38.844, places=2)

    def test_stale_dates_and_no_buy_signal(self):
        now = datetime.now(timezone.utc)
        payload = build_valuation(config(), market(now - timedelta(hours=100)), now)
        self.assertEqual(payload["quality"], "stale")
        self.assertNotIn("composite", payload)
        self.assertNotIn("historical_series", payload)
        self.assertTrue(
            all("funding_delta_nav_per_share" in s for s in payload["scenarios"])
        )

    def test_weekend_equity_quote_not_stale_btc_is(self):
        now = datetime.now(timezone.utc)
        p = build_valuation(config(), market(now - timedelta(hours=36)), now)
        self.assertFalse(p["market"]["mstr"]["stale"])
        self.assertTrue(p["market"]["btc"]["stale"])

    def test_quote_failure_never_substitutes_price(self):
        with patch("httpx.Client.get", side_effect=httpx.ConnectError("offline")):
            with self.assertRaises(MarketDataError):
                fetch_quote("MSTR")

    def test_invalid_conversion_and_nonfinite_rejected(self):
        for updates in [
            {"converted_debt": 1},
            {"conversion_new_shares": 1},
            {"conversion_carry_relief": 1},
            {"total_debt": float("nan")},
            {"btc_holdings": 0},
            {"refresh_interval_seconds": 1},
        ]:
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                Settings.model_validate(config() | updates)

    def test_future_evidence_and_incomplete_source_rejected(self):
        for evidence in [
            {"status": "reported"},
            {
                "status": "reported",
                "source_url": "https://example.test",
                "as_of": "2099-01-01",
            },
        ]:
            with self.assertRaises(ValidationError):
                Settings.model_validate(
                    config() | {"evidence": {"btc_holdings": evidence}}
                )

    def test_cash_shortfall_exposed(self):
        c = config()
        c["annual_carry_cost"] = 3000000
        p = build_valuation(c, market())
        self.assertEqual(p["scenarios"][1]["funding_gap_usd"], 1000000)
        self.assertTrue(any("aset USD" in w for w in p["warnings"]))


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = patch.object(db, "DB_PATH", os.path.join(self.tmp.name, "test.db"))
        self.path.start()

    def tearDown(self):
        self.path.stop()
        self.tmp.cleanup()

    def test_legacy_migration_preserves_old_payload_and_marks_inputs_unverified(self):
        conn = sqlite3.connect(db.DB_PATH)
        conn.execute(
            "CREATE TABLE config (key TEXT PRIMARY KEY,value TEXT NOT NULL,updated_at TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO config VALUES (?,?,?)",
            (
                "app_config",
                json.dumps({"basic_shares": 403640000, "preferred_stock": 1500000000}),
                "old",
            ),
        )
        conn.execute(
            "CREATE TABLE snapshots (id INTEGER PRIMARY KEY, raw_payload TEXT)"
        )
        conn.execute("INSERT INTO snapshots VALUES (1,?)", ('{"legacy":true}',))
        conn.commit()
        conn.close()
        db.init_db()
        db.init_db()
        self.assertEqual(db.get_config()["basic_shares"], 403640000)
        self.assertEqual(db.get_config()["evidence"], {})
        self.assertEqual(db.legacy_count(), 1)
        self.assertIsNone(db.get_latest_snapshot_payload())

    def test_snapshot_roundtrip_and_failed_refresh_retains_cache(self):
        db.init_db()
        payload = build_valuation(config(), market())
        db.save_snapshot(payload)
        self.assertEqual(db.get_latest_snapshot_payload()["model_version"], "2.0")
        with patch.object(
            main, "compute_full_valuation", side_effect=MarketDataError("offline")
        ):
            with TestClient(main.app) as client:
                import time

                for _ in range(100):
                    if main.STATE["last_error"]:
                        break
                    time.sleep(0.01)
                body = client.get("/api/valuation").json()
                self.assertEqual(body["valuation"]["market"]["mstr"]["price"], 100)
                self.assertIn("offline", body["scheduler"]["last_error"])
                self.assertEqual(client.get("/health").json()["status"], "degraded")
                self.assertEqual(client.post("/api/refresh").status_code, 503)

    def test_api_validation_and_config_persistence(self):
        payload = build_valuation(config(), market())
        with patch.object(main, "compute_full_valuation", return_value=payload):
            with TestClient(main.app) as client:
                self.assertEqual(
                    client.post("/api/config", json={"basic_shares": 0}).status_code,
                    422,
                )
                self.assertEqual(
                    client.post(
                        "/api/config",
                        json=config(),
                        headers={"Origin": "https://evil.test"},
                    ).status_code,
                    403,
                )
                self.assertEqual(
                    client.post("/api/config", json=config()).status_code, 200
                )
                self.assertEqual(db.get_config()["basic_shares"], 1000000)
                self.assertEqual(client.get("/api/history?limit=-1").status_code, 422)
                self.assertEqual(
                    client.get("/api/config/reviewed").json()["preferred_stock"],
                    13136000000,
                )


if __name__ == "__main__":
    unittest.main()
