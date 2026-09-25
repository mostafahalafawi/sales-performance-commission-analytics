"""Unit tests for the commission policy (tier boundaries, cap, clawback, plan versions).

Run:  python -m unittest discover -s tests -v     (or: pytest)
"""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from kpi_engine import add_commission  # noqa: E402
from utils import load_config, plan_for_month, tier_for_achievement  # noqa: E402


class TestTiers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()
        cls.p24 = plan_for_month(cls.cfg, pd.Period("2024-06", "M"))
        cls.p25 = plan_for_month(cls.cfg, pd.Period("2025-06", "M"))

    def test_plan_versions_by_effective_date(self):
        self.assertEqual(self.p24["plan_id"], "CP2024")
        self.assertEqual(self.p25["plan_id"], "CP2025")

    def test_2024_boundaries_are_half_open(self):
        cases = {0.7999: 0.0, 0.80: 0.005, 0.9999: 0.005, 1.00: 0.010, 1.1999: 0.010, 1.20: 0.015, 3.5: 0.015}
        for ach, rate in cases.items():
            self.assertEqual(tier_for_achievement(self.p24, ach)[1], rate, msg=f"achievement {ach}")

    def test_2025_gate_moved_to_85pct(self):
        self.assertEqual(tier_for_achievement(self.p25, 0.84)[1], 0.0)
        self.assertEqual(tier_for_achievement(self.p25, 0.85)[1], 0.005)
        self.assertEqual(tier_for_achievement(self.p24, 0.84)[1], 0.005)

    def test_negative_achievement_is_below_gate_not_accelerator(self):
        # net credit notes > invoices must never fall through to the top tier
        self.assertEqual(tier_for_achievement(self.p24, -0.3), ("T0 Below Gate", 0.0))

    def test_missing_target(self):
        self.assertEqual(tier_for_achievement(self.p24, np.nan), ("No Target", 0.0))


class TestCommissionCalculation(unittest.TestCase):
    """Small hand-built cases pushed through the real engine function."""

    @classmethod
    def setUpClass(cls):
        cls.cfg = load_config()

    def _run(self, target, lines):
        rep = pd.DataFrame([{"rep_key": 1, "month_key": 202406, "year": 2024, "target": target,
                             "actual": sum(l["net_amount_usd"] for l in lines)}])
        rep["achievement"] = rep["actual"] / rep["target"]
        fs = pd.DataFrame([{"rep_key": 1, "month_key": 202406, "days_since_original": np.nan, **l} for l in lines])
        return add_commission(self.cfg, rep, fs).iloc[0]

    def test_on_target_standard_products(self):
        r = self._run(100_000, [{"doc_type": "INV", "net_amount_usd": 105_000, "commission_weight": 1.0}])
        self.assertEqual(r["tier_name"], "T2 On Target")
        self.assertAlmostEqual(r["commission"], 1_050.0, places=2)

    def test_strategic_weight_applies_to_base_not_tier(self):
        r = self._run(100_000, [{"doc_type": "INV", "net_amount_usd": 90_000, "commission_weight": 1.25}])
        self.assertEqual(r["tier_name"], "T1 Approaching")          # tier from 90% unweighted
        self.assertAlmostEqual(r["commission"], 90_000 * 1.25 * 0.005, places=2)

    def test_cap_at_150pct_of_target(self):
        r = self._run(100_000, [{"doc_type": "INV", "net_amount_usd": 400_000, "commission_weight": 1.0}])
        self.assertEqual(r["is_capped"], 1)
        self.assertAlmostEqual(r["commission"], 150_000 * 0.015, places=2)

    def test_clawback_only_inside_window(self):
        lines = [{"doc_type": "INV", "net_amount_usd": 120_000, "commission_weight": 1.0},
                 {"doc_type": "CN", "net_amount_usd": -10_000, "commission_weight": 1.0, "days_since_original": 30},
                 {"doc_type": "CN", "net_amount_usd": -5_000, "commission_weight": 1.0, "days_since_original": 120}]
        r = self._run(100_000, lines)
        # achievement uses ALL credit notes: 105% -> T2; base deducts only the in-window one
        self.assertEqual(r["tier_name"], "T2 On Target")
        self.assertAlmostEqual(r["commissionable_base"], 110_000, places=2)
        self.assertAlmostEqual(r["commission"], 1_100.0, places=2)
        self.assertAlmostEqual(r["credit_notes_outside_window"], -5_000, places=2)


if __name__ == "__main__":
    unittest.main()
