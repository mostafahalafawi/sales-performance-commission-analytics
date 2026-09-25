"""Data-quality rule engine.

Each rule is declared once with an ID, severity and action, and returns a
boolean mask of offending rows. The ETL applies the actions; this module only
detects and logs, so the same rules can be re-run on any extract.

Actions
    FIX         value is repaired deterministically (the repair is logged)
    DROP        row is an exact duplicate and removed
    QUARANTINE  row is held out of the model in data/processed/quarantine_*.csv
    FLAG        row is kept but reported (e.g. rep-month with sales but no target)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from utils import get_logger

log = get_logger("validation")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    table: str
    description: str
    severity: str        # CRITICAL / HIGH / MEDIUM / LOW
    action: str          # FIX / DROP / QUARANTINE / FLAG
    check: Callable[[pd.DataFrame, dict], pd.Series]


# ---------------------------------------------------------------------------
# Sales-line rules (evaluated AFTER standardisation, BEFORE conformance)
# ---------------------------------------------------------------------------
def _in_window(df, ctx):
    return df["document_date"].notna() & ((df["document_date"] < ctx["start"]) | (df["document_date"] > ctx["end"]))


SALES_RULES: list[Rule] = [
    Rule("DQ-S01", "sales_lines", "Exact duplicate line (same line_id exported twice)", "HIGH", "DROP",
         lambda df, ctx: df.duplicated(subset=["line_id"], keep="first")),
    Rule("DQ-S02", "sales_lines", "Document date missing or unparseable", "CRITICAL", "QUARANTINE",
         lambda df, ctx: df["document_date"].isna()),
    Rule("DQ-S03", "sales_lines", "Document date outside reporting window (e.g. future-dated)", "HIGH", "QUARANTINE",
         _in_window),
    Rule("DQ-S04", "sales_lines", "Sales rep code missing", "CRITICAL", "QUARANTINE",
         lambda df, ctx: df["rep_code"].isna() | (df["rep_code"].astype(str).str.strip() == "")),
    Rule("DQ-S05", "sales_lines", "Rep code not found in employee master", "HIGH", "QUARANTINE",
         lambda df, ctx: df["rep_code"].notna() & ~df["rep_code"].isin(ctx["rep_codes"])),
    Rule("DQ-S06", "sales_lines", "SKU not found in product master (orphan product)", "HIGH", "QUARANTINE",
         lambda df, ctx: ~df["sku"].isin(ctx["skus"])),
    Rule("DQ-S07", "sales_lines", "Customer not found in customer master", "HIGH", "QUARANTINE",
         lambda df, ctx: ~df["customer_code"].isin(ctx["customer_codes"])),
    Rule("DQ-S08", "sales_lines", "Currency code invalid / not in FX table", "HIGH", "QUARANTINE",
         lambda df, ctx: ~df["currency"].isin(ctx["currencies"])),
    Rule("DQ-S09", "sales_lines", "Country could not be mapped to a known market", "HIGH", "QUARANTINE",
         lambda df, ctx: df["country_code"].isna()),
    Rule("DQ-S10", "sales_lines", "Negative quantity on an invoice (sign error; only credit notes may be negative)",
         "HIGH", "QUARANTINE", lambda df, ctx: (df["doc_type"] == "INV") & (df["quantity"] < 0)),
    Rule("DQ-S11", "sales_lines", "Positive quantity on a credit note", "HIGH", "QUARANTINE",
         lambda df, ctx: (df["doc_type"] == "CN") & (df["quantity"] > 0)),
    Rule("DQ-S12", "sales_lines", "Credit note without a reference to an original invoice", "MEDIUM", "QUARANTINE",
         lambda df, ctx: (df["doc_type"] == "CN") & df["original_document_no"].isna()),
    Rule("DQ-S13", "sales_lines", "Zero / missing price on invoice line (repaired from product list price)",
         "MEDIUM", "FIX", lambda df, ctx: (df["doc_type"] == "INV") &
         ((df["unit_list_price_local"].fillna(0) == 0) | (df["net_amount_local"].fillna(0) == 0))),
    Rule("DQ-S14", "sales_lines", "Discount outside 0-60% range", "MEDIUM", "QUARANTINE",
         lambda df, ctx: (df["discount_pct"] < 0) | (df["discount_pct"] > 0.60)),
    Rule("DQ-S15", "sales_lines", "Net amount inconsistent with qty x price x (1-discount) by > 1%", "MEDIUM", "FLAG",
         lambda df, ctx: ((df["quantity"] * df["unit_list_price_local"] * (1 - df["discount_pct"])
                           - df["net_amount_local"]).abs() > df["net_amount_local"].abs() * 0.01 + 1)
         & (df["unit_list_price_local"].fillna(0) != 0)),
]

TARGET_RULES: list[Rule] = [
    Rule("DQ-T01", "targets", "Superseded target revision (older revision of same rep-month)", "LOW", "DROP",
         lambda df, ctx: df.sort_values("revision_no").duplicated(["target_month", "rep_code"], keep="last")
         .reindex(df.index)),
    Rule("DQ-T02", "targets", "Target not positive", "HIGH", "QUARANTINE",
         lambda df, ctx: df["target_amount_usd"].fillna(0) <= 0),
    Rule("DQ-T03", "targets", "Target rep code not in employee master", "HIGH", "QUARANTINE",
         lambda df, ctx: ~df["rep_code"].isin(ctx["rep_codes"])),
]


def evaluate(df: pd.DataFrame, rules: list[Rule], ctx: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate rules; return (flags matrix, summary log).

    flags: one boolean column per rule_id, aligned to df.index.
    Rules are evaluated independently so one row can fail several rules; the
    ETL applies DROP -> QUARANTINE -> FIX precedence.
    """
    flags = pd.DataFrame(index=df.index)
    records = []
    for rule in rules:
        try:
            mask = rule.check(df, ctx).fillna(False).astype(bool)
        except Exception as exc:  # a broken rule must never silently pass data
            raise RuntimeError(f"Rule {rule.rule_id} failed to evaluate: {exc}") from exc
        flags[rule.rule_id] = mask
        records.append({"rule_id": rule.rule_id, "table": rule.table, "description": rule.description,
                        "severity": rule.severity, "action": rule.action, "rows_affected": int(mask.sum()),
                        "pct_of_rows": round(100 * mask.mean(), 3) if len(df) else 0.0})
        if mask.any():
            log.info("%s %-10s %6d rows  %s", rule.rule_id, rule.action, mask.sum(), rule.description)
    return flags, pd.DataFrame(records)


# ---------------------------------------------------------------------------
# Post-load integrity checks on the conformed model (all must pass)
# ---------------------------------------------------------------------------
def integrity_checks(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    fs, ft, dp = tables["fact_sales"], tables["fact_targets"], tables["dim_product"]
    de, dt, dc = tables["dim_employee"], tables["dim_team"], tables["dim_customer"]
    checks = [
        ("IC-01", "fact_sales.line_id is unique", fs["line_id"].is_unique),
        ("IC-02", "Every fact_sales.product_key exists in dim_product", fs["product_key"].isin(dp["product_key"]).all()),
        ("IC-03", "Every fact_sales.rep_key exists in dim_employee", fs["rep_key"].isin(de["employee_key"]).all()),
        ("IC-04", "Every fact_sales.team_key exists in dim_team", fs["team_key"].isin(dt["team_key"]).all()),
        ("IC-05", "Every fact_sales.customer_key exists in dim_customer", fs["customer_key"].isin(dc["customer_key"]).all()),
        ("IC-06", "Invoice lines have positive net USD; credit notes negative",
         bool(((fs["doc_type"] == "INV") == (fs["net_amount_usd"] > 0)).all())),
        ("IC-07", "gross - discount = net (USD, 0.01 tolerance)",
         bool(((fs["gross_amount_usd"] - fs["discount_amount_usd"] - fs["net_amount_usd"]).abs() < 0.01).all())),
        ("IC-08", "Cost never exceeds gross (sign-aware)",
         bool((fs["cost_amount_usd"].abs() <= fs["gross_amount_usd"].abs() + 0.01).all())),
        ("IC-09", "fact_targets unique per rep-month", not ft.duplicated(["rep_key", "month_key"]).any()),
        ("IC-10", "Every credit note references an invoice present in fact_sales",
         bool(fs.loc[fs["doc_type"] == "CN", "original_document_no"].isin(fs["document_no"]).all())),
        ("IC-11", "Credit note never dated before its original invoice",
         bool((fs.loc[fs["doc_type"] == "CN", "days_since_original"] >= 0).all())),
    ]
    out = pd.DataFrame(checks, columns=["check_id", "description", "passed"])
    for _, r in out.iterrows():
        (log.info if r.passed else log.error)("%s %s  %s", r.check_id, "PASS" if r.passed else "FAIL", r.description)
    return out
