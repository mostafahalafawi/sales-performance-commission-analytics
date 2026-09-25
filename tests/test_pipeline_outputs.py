"""Integration tests on pipeline outputs (run after `python run_pipeline.py`).

Checks that the validation layer catches every injected defect, that the model
reconciles, and that Python and SQL agree.
"""
import json
import sqlite3
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from utils import load_config  # noqa: E402

CFG = load_config()
PROC, RAW = CFG["paths"]["processed"], CFG["paths"]["raw"]


@unittest.skipUnless((PROC / "kpi_rep_month.csv").exists(), "run the pipeline first")
class TestPipelineOutputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dq = pd.read_csv(PROC / "dq_rule_log.csv").set_index("rule_id")
        cls.manifest = json.loads((RAW / "_defect_manifest.json").read_text())

    def test_injected_defects_are_detected(self):
        pairs = {"DQ-S01": "duplicate_lines", "DQ-S03": "future_date", "DQ-S06": "orphan_sku",
                 "DQ-S08": "invalid_currency", "DQ-S10": "negative_qty_on_invoice", "DQ-S13": "zero_price",
                 "DQ-T01": "target_revisions", "DQ-T04": "missing_targets"}
        for rule, key in pairs.items():
            # >= because a duplicated defective row is counted by both rules
            self.assertGreaterEqual(self.dq.loc[rule, "rows_affected"], self.manifest[key], msg=rule)
        self.assertGreaterEqual(self.dq.loc["DQ-S04", "rows_affected"], self.manifest["missing_rep"])
        self.assertGreaterEqual(self.dq.loc["STD-02", "rows_affected"], self.manifest["country_variant"])

    def test_row_reconciliation(self):
        raw = len(pd.read_csv(RAW / "raw_sales_lines.csv"))
        clean = len(pd.read_csv(PROC / "fact_sales.csv"))
        quarantine = len(pd.read_csv(PROC / "quarantine_sales_lines.csv"))
        self.assertEqual(raw, clean + quarantine + int(self.dq.loc["DQ-S01", "rows_affected"]))

    def test_integrity_checks_all_pass(self):
        self.assertTrue(pd.read_csv(PROC / "dq_integrity_checks.csv")["passed"].all())

    def test_sql_assertions_pass(self):
        con = sqlite3.connect(CFG["paths"]["database"])
        try:
            dq = pd.read_sql("SELECT * FROM vw_dq_assertions", con)
        finally:
            con.close()
        self.assertTrue((dq["failing_rows"] == 0).all(), dq[dq["failing_rows"] > 0])

    def test_python_sql_reconciliation(self):
        rec = pd.read_csv(PROC / "reconciliation_python_vs_sql.csv")
        self.assertTrue(rec["passed"].all())

    def test_team_revenue_ties_to_fact(self):
        fs = pd.read_csv(PROC / "fact_sales.csv")
        team = pd.read_csv(PROC / "kpi_team_month.csv")
        self.assertAlmostEqual(fs["net_amount_usd"].sum(), team["actual"].sum(), places=0)


if __name__ == "__main__":
    unittest.main()
