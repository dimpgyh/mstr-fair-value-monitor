import unittest
from datetime import datetime, timedelta, timezone
from scripts.evaluate import evaluate


class EvaluationTests(unittest.TestCase):
    def row(self, stamp, price=100, prediction=120):
        return {
            "model_version": "2.0",
            "timestamp_utc": stamp.isoformat(),
            "quality": "indicative",
            "market": {"mstr": {"price": price, "observed_at": stamp.isoformat()}},
            "scenarios": [{"name": "base", "terminal_price": prediction}],
            "backing": {"common_equity_reference": 90},
        }

    def test_no_matured_history_no_accuracy_claim(self):
        r = evaluate([self.row(datetime(2026, 1, 1, tzinfo=timezone.utc))])
        self.assertEqual(r["status"], "insufficient_matured_history")
        self.assertIsNone(r["mean_absolute_percentage_error"])

    def test_point_in_time_prediction_against_future_observation(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = [
            self.row(start),
            self.row(start + timedelta(hours=1), prediction=999),
            self.row(start + timedelta(days=365), price=120),
        ]
        r = evaluate(rows)
        self.assertEqual(r["matured_cohorts"], 1)
        self.assertEqual(r["mean_absolute_percentage_error"]["scenario_base"], 0)
        self.assertAlmostEqual(
            r["mean_absolute_percentage_error"]["unchanged_market_price"], 100 / 6
        )

    def test_old_quote_at_later_snapshot_cannot_settle_forecast(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        later = self.row(start + timedelta(days=365), 120)
        later["market"]["mstr"]["observed_at"] = (
            start + timedelta(days=364)
        ).isoformat()
        self.assertEqual(evaluate([self.row(start), later])["matured_cohorts"], 0)

    def test_stale_and_legacy_excluded(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        row = self.row(start)
        row["quality"] = "stale"
        other = self.row(start)
        other["model_version"] = "1.0"
        self.assertEqual(evaluate([row, other])["eligible_daily_cohorts"], 0)
