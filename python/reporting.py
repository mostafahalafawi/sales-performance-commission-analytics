"""Automated reporting outputs (openpyxl).

    reports/management_pack.xlsx          formatted multi-sheet pack for leadership
    reports/rep_statements/*.xlsx         one self-service commission statement per rep
                                          (live Excel formulas + reconciliation cell)
    insights/kpi_summary.md               auto-generated fact sheet (numbers only)

Run:  python python/reporting.py
"""
from __future__ import annotations

import json

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from utils import get_logger, load_config, plan_for_month, read_csv_checked

log = get_logger("reporting")

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14, color="1F3A5F")
THIN = Side(style="thin", color="D9D9D9")
USD = '#,##0;[Red]-#,##0'
USD2 = '#,##0.00;[Red]-#,##0.00'
PCT = '0.0%'


def _format_sheet(ws, df: pd.DataFrame, formats: dict[str, str], start_row: int = 1) -> None:
    for c in range(1, len(df.columns) + 1):
        cell = ws.cell(row=start_row, column=c)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[start_row].height = 30
    for i, col in enumerate(df.columns, start=1):
        letter = get_column_letter(i)
        width = max(len(str(col)), *(len(str(v)) for v in df[col].head(200))) if len(df) else len(str(col))
        ws.column_dimensions[letter].width = min(max(10, width + 2), 42)
        fmt = formats.get(col)
        if fmt:
            for r in range(start_row + 1, start_row + len(df) + 1):
                ws.cell(row=r, column=i).number_format = fmt
    ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
    ws.auto_filter.ref = f"A{start_row}:{get_column_letter(len(df.columns))}{start_row + len(df)}"


def _achievement_scale(ws, col_idx: int, n_rows: int, start_row: int = 1) -> None:
    letter = get_column_letter(col_idx)
    ws.conditional_formatting.add(
        f"{letter}{start_row + 1}:{letter}{start_row + n_rows}",
        ColorScaleRule(start_type="num", start_value=0.7, start_color="F8696B",
                       mid_type="num", mid_value=1.0, mid_color="FFEB84",
                       end_type="num", end_value=1.3, end_color="63BE7B"))


# ---------------------------------------------------------------------------
def management_pack(cfg, rep, team, country, res) -> None:
    rs = cfg["paths"]["reports"] / "sql_results"
    path = cfg["paths"]["reports"] / "management_pack.xlsx"
    latest_year = int(rep["year"].max())

    summary = pd.DataFrame([
        ("Net revenue (USD)", res["revenue_2024"], res["revenue_2025"]),
        ("Allocated target (USD)", res["target_2024"], res["target_2025"]),
        ("Achievement", res["achievement_2024_pct"] / 100, res["achievement_2025_pct"] / 100),
        ("Variance to target (USD)", res["revenue_2024"] - res["target_2024"], res["revenue_2025"] - res["target_2025"]),
        ("Commission (USD)", res["commission_2024"], res["commission_2025"]),
        ("Commission / revenue", res["commission_to_revenue_2024_pct"] / 100, res["commission_to_revenue_2025_pct"] / 100),
    ], columns=["KPI", "FY2024", "FY2025"])

    cty = country.groupby(["country_code", "year"])[["plan", "allocated_target", "actual", "commission"]].sum().reset_index()
    cty["achievement"] = cty["actual"] / cty["allocated_target"]
    cty["plan_achievement"] = cty["actual"] / cty["plan"]
    cty["variance_to_target"] = cty["actual"] - cty["allocated_target"]

    tm = team.groupby(["team_name", "year"])[["plan", "allocated_target", "actual", "commission"]].sum().reset_index()
    tm["achievement"] = tm["actual"] / tm["allocated_target"]
    tm["variance_to_target"] = tm["actual"] - tm["allocated_target"]
    tm = tm.sort_values(["year", "achievement"], ascending=[True, False])

    r = rep[rep["year"] == latest_year]
    ytd = r.groupby(["employee_code", "full_name", "team_name"]).agg(
        months=("month_key", "count"), target=("target", "sum"), actual=("actual", "sum"),
        deals=("deals", "sum"), commission=("commission", "sum")).reset_index()
    ytd["achievement"] = ytd["actual"] / ytd["target"]
    ytd["rank"] = ytd["achievement"].rank(ascending=False, method="min").astype(int)
    ytd = ytd.sort_values("rank")

    sheets = {
        "Executive Summary": (summary, {}),
        "Country": (cty, {"plan": USD, "allocated_target": USD, "actual": USD, "commission": USD,
                          "achievement": PCT, "plan_achievement": PCT, "variance_to_target": USD}),
        "Team": (tm, {"plan": USD, "allocated_target": USD, "actual": USD, "commission": USD,
                      "achievement": PCT, "variance_to_target": USD}),
        f"Rep YTD {latest_year}": (ytd, {"target": USD, "actual": USD, "commission": USD2, "achievement": PCT}),
        "Commission by Tier": (read_csv_checked(rs / "q07_commission_by_tier.csv"), {"net_revenue_usd": USD, "commission_usd": USD}),
        "Monthly Trend": (read_csv_checked(rs / "q06_monthly_trend.csv"), {}),
        "Anomalies": (read_csv_checked(cfg["paths"]["reports"] / "analysis" / "anomalies.csv"),
                      {"target": USD, "actual": USD, "achievement": PCT, "robust_z": "0.00", "commission": USD2}),
        "Data Quality": (read_csv_checked(cfg["paths"]["processed"] / "dq_rule_log.csv"), {}),
    }
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        for name, (df, _) in sheets.items():
            df.to_excel(xw, sheet_name=name, index=False, startrow=2)
    wb = load_workbook(path)
    for name, (df, fmts) in sheets.items():
        ws = wb[name]
        ws["A1"] = f"{name} | {cfg['project']['company_name']} | synthetic data"
        ws["A1"].font = TITLE_FONT
        _format_sheet(ws, df, fmts, start_row=3)
        if "achievement" in df.columns:
            _achievement_scale(ws, list(df.columns).index("achievement") + 1, len(df), start_row=3)
    ws = wb["Executive Summary"]
    for row in range(4, 10):
        fmt = PCT if ws.cell(row=row, column=1).value in ("Achievement", "Commission / revenue") else USD
        for col in (2, 3):
            ws.cell(row=row, column=col).number_format = "0.00%" if ws.cell(row=row, column=1).value == "Commission / revenue" else fmt
    wb.save(path)
    log.info("management pack -> %s (%d sheets)", path.name, len(sheets))


# ---------------------------------------------------------------------------
def rep_statements(cfg, rep, fs, products, customers) -> int:
    """Self-service commission statement per rep for the configured month."""
    month = pd.Period(cfg["reporting"]["statement_month"], "M")
    mk = int(month.strftime("%Y%m"))
    plan = plan_for_month(cfg, month)
    out_dir = cfg["paths"]["reports"] / "rep_statements"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in out_dir.glob("*.xlsx"):
        f.unlink()
    cur = rep[(rep["month_key"] == mk) & rep["target"].notna()]
    prod = products.set_index("product_key")
    cust = customers.set_index("customer_key")
    bold = Font(bold=True)
    box = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

    for _, r in cur.iterrows():
        wb = Workbook()
        ws = wb.active
        ws.title = "Statement"
        ws["A1"] = f"Commission Statement - {month.strftime('%B %Y')}"
        ws["A1"].font = TITLE_FONT
        ws["A2"] = f"{r['full_name']} ({r['employee_code']}) | {r['team_name']} | Plan {plan['plan_id']}"
        ws["A3"] = "Synthetic portfolio data. Figures recalculate with Excel formulas; row 18 checks them against the pipeline."
        ws["A3"].font = Font(italic=True, color="808080")

        rows = [
            ("Monthly target (USD)", r["target"], USD2),
            ("Net revenue (USD)", r["actual"], USD2),
            ("Achievement", "=B6/B5", PCT),
            ("Tier", r["tier_name"], None),
            ("Tier rate", r["commission_rate"], "0.00%"),
            ("Weighted invoiced revenue (strategic x1.25)", r["weighted_invoiced"], USD2),
            ("Clawback: credit notes within 90 days (weighted)", r["weighted_clawback"], USD2),
            ("Weighted commissionable base", "=B10+B11", USD2),
            ("Cap (150% x target)", f"={plan['cap_multiple_of_target']}*B5", USD2),
            ("Commissionable base after cap", "=MIN(MAX(B12,0),B13)", USD2),
            ("Commission payable (USD)", "=ROUND(B14*B9,2)", USD2),
            ("YTD commission (USD)", float(rep[(rep["rep_key"] == r["rep_key"]) & (rep["year"] == month.year)]["commission"].sum()), USD2),
            ("Check vs pipeline (should be 0.00)", f"=ROUND(B15-{round(r['commission'], 2)},2)", USD2),
        ]
        for i, (label, val, fmt) in enumerate(rows, start=5):
            ws.cell(row=i, column=1, value=label).border = box
            c = ws.cell(row=i, column=2, value=val)
            c.border = box
            if fmt:
                c.number_format = fmt
        ws["A15"].font = ws["B15"].font = bold
        ws.column_dimensions["A"].width = 52
        ws.column_dimensions["B"].width = 20

        # monthly history (YTD)
        hist = rep[(rep["rep_key"] == r["rep_key"]) & (rep["year"] == month.year)][
            ["month_key", "target", "actual", "achievement", "tier_name", "commission"]]
        ws2 = wb.create_sheet("YTD History")
        ws2.append(["Month", "Target", "Net revenue", "Achievement", "Tier", "Commission"])
        for h in hist.itertuples(index=False):
            ws2.append([str(h.month_key), h.target, h.actual, h.achievement, h.tier_name, h.commission])
        _format_sheet(ws2, pd.DataFrame(columns=["Month", "Target", "Net revenue", "Achievement", "Tier", "Commission"],
                                        data=hist.values), {"Target": USD, "Net revenue": USD, "Achievement": PCT,
                                                            "Commission": USD2})

        # transactions in the month
        tx = fs[(fs["rep_key"] == r["rep_key"]) & (fs["month_key"] == mk)].copy()
        tx["product"] = tx["product_key"].map(prod["product_name"])
        tx["customer"] = tx["customer_key"].map(cust["customer_name"])
        tx = tx[["document_no", "doc_type", "date_key", "customer", "product", "quantity", "discount_pct",
                 "net_amount_usd", "commission_weight", "days_since_original"]]
        ws3 = wb.create_sheet("Transactions")
        ws3.append(list(tx.columns))
        for t in tx.itertuples(index=False):
            ws3.append([None if pd.isna(v) else v for v in t])
        _format_sheet(ws3, tx, {"discount_pct": PCT, "net_amount_usd": USD2})
        wb.save(out_dir / f"Commission_Statement_{r['employee_code']}_{month}.xlsx")
    log.info("rep statements -> %d files for %s", len(cur), month)
    return len(cur)


def kpi_summary(cfg, res) -> None:
    rs = cfg["paths"]["reports"] / "sql_results"
    q02 = read_csv_checked(rs / "q02_country_performance_gap.csv")
    q03 = read_csv_checked(rs / "q03_team_status_by_year.csv")
    lines = ["# KPI Summary (auto-generated)", "",
             "_Generated by `python/reporting.py` from pipeline outputs. Do not edit by hand; "
             "interpretation lives in `business_insights.md`._", "",
             "| KPI | FY2024 | FY2025 |", "|---|---:|---:|",
             f"| Net revenue | ${res['revenue_2024']:,.0f} | ${res['revenue_2025']:,.0f} |",
             f"| Allocated target | ${res['target_2024']:,.0f} | ${res['target_2025']:,.0f} |",
             f"| Achievement | {res['achievement_2024_pct']}% | {res['achievement_2025_pct']}% |",
             f"| Commission | ${res['commission_2024']:,.0f} | ${res['commission_2025']:,.0f} |",
             f"| Commission / revenue | {res['commission_to_revenue_2024_pct']}% | {res['commission_to_revenue_2025_pct']}% |",
             "", f"Revenue growth FY2025: **{res['revenue_growth_pct']:+}%** vs target growth **{res['target_growth_pct']:+}%**.", "",
             "## Country achievement", "", "| Country | Year | Achievement % | Execution gap (USD) | Capacity gap (USD) |",
             "|---|---|---:|---:|---:|"]
    for x in q02.itertuples():
        lines.append(f"| {x.country_name} | {x.year} | {x.achievement_pct} | {x.execution_gap_usd:,.0f} | {x.capacity_gap_usd:,.0f} |")
    lines += ["", "## Team status", "", "| Team | Year | Achievement % | Status |", "|---|---|---:|---|"]
    for x in q03.itertuples():
        lines.append(f"| {x.team_name} | {x.year} | {x.achievement_pct} | {x.status} |")
    (cfg["paths"]["insights"] / "kpi_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log.info("kpi summary -> insights/kpi_summary.md")


def main() -> None:
    cfg = load_config()
    p = cfg["paths"]["processed"]
    rep = read_csv_checked(p / "kpi_rep_month.csv")
    team = read_csv_checked(p / "kpi_team_month.csv")
    country = read_csv_checked(p / "kpi_country_month.csv")
    res = json.loads((cfg["paths"]["reports"] / "analysis_results.json").read_text())
    management_pack(cfg, rep, team, country, res)
    rep_statements(cfg, rep, read_csv_checked(p / "fact_sales.csv"), read_csv_checked(p / "dim_product.csv"),
                   read_csv_checked(p / "dim_customer.csv"))
    kpi_summary(cfg, res)


if __name__ == "__main__":
    main()
