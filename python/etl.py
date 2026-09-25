"""ETL: raw CRM/ERP extracts -> validated, conformed star schema (data/processed).

Steps
    1. extract      read raw CSVs
    2. standardise  trim/case, multi-format dates, country aliases, SKU format
    3. validate     rule engine (validation.py) -> DROP / QUARANTINE / FIX / FLAG
    4. repair       zero prices rebuilt from the product master
    5. conform      FX to USD, team-at-date via assignment history, product economics
    6. model        dimensions, facts, bridge table
    7. reconcile    row-count and value reconciliation raw -> clean + quarantine
    8. load         CSVs for Power BI + data-quality report

Run:  python python/etl.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from utils import ensure_dirs, get_logger, load_config, month_range, read_csv_checked
from validation import SALES_RULES, TARGET_RULES, evaluate, integrity_checks

log = get_logger("etl")

COUNTRY_ALIASES = {
    "egypt": "EG", "egy": "EG", "eg": "EG", "arab republic of egypt": "EG",
    "saudi arabia": "SA", "ksa": "SA", "saudi": "SA", "sa": "SA", "kingdom of saudi arabia": "SA",
    "uae": "AE", "u.a.e": "AE", "u.a.e.": "AE", "united arab emirates": "AE", "ae": "AE",
    "oman": "OM", "sultanate of oman": "OM", "om": "OM",
}
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"]


# ---------------------------------------------------------------------------
# 1. Extract
# ---------------------------------------------------------------------------
def extract(cfg) -> dict[str, pd.DataFrame]:
    raw = cfg["paths"]["raw"]
    files = ["sales_lines", "sales_targets", "team_plan", "employees", "rep_assignments", "teams",
             "customers", "products", "fx_rates", "commission_plans"]
    data = {f: read_csv_checked(raw / f"raw_{f}.csv", dtype={"rep_code": "string", "original_document_no": "string"})
            for f in files}
    for k, v in data.items():
        log.info("extracted %-18s %7d rows", k, len(v))
    return data


# ---------------------------------------------------------------------------
# 2. Standardise
# ---------------------------------------------------------------------------
def parse_dates(s: pd.Series) -> pd.Series:
    """Parse mixed date formats; unparseable values become NaT (caught by DQ-S02)."""
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    remaining = s.astype("string").str.strip()
    for fmt in DATE_FORMATS:
        parsed = pd.to_datetime(remaining, format=fmt, errors="coerce")
        fill = out.isna() & parsed.notna()
        out[fill] = parsed[fill]
    return out


def standardise_sales(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    fixes = []
    df = df.copy()

    iso = df["document_date"].astype("string").str.match(r"^\d{4}-\d{2}-\d{2}$").fillna(False)
    df["document_date"] = parse_dates(df["document_date"])
    fixes.append({"rule_id": "STD-01", "description": "Non-ISO date formats normalised to ISO",
                  "rows_affected": int((~iso & df["document_date"].notna()).sum())})

    raw_country = df["country"].astype("string").str.strip().str.lower()
    df["country_code"] = raw_country.map(COUNTRY_ALIASES)
    canonical = {"egypt", "saudi arabia", "uae", "oman"}
    was_variant = ~df["country"].astype("string").isin(["Egypt", "Saudi Arabia", "UAE", "Oman"])
    fixes.append({"rule_id": "STD-02", "description": "Country name variants mapped to ISO-2 codes",
                  "rows_affected": int((was_variant & df["country_code"].notna()).sum())})

    clean_sku = df["sku"].astype("string").str.strip().str.upper()
    fixes.append({"rule_id": "STD-03", "description": "SKU codes trimmed and upper-cased",
                  "rows_affected": int((clean_sku != df["sku"]).sum())})
    df["sku"] = clean_sku

    df["currency"] = df["currency"].astype("string").str.strip().str.upper().fillna("")
    df["rep_code"] = df["rep_code"].astype("string").str.strip()
    df["doc_type"] = df["doc_type"].astype("string").str.strip().str.upper()
    for c in ["quantity", "unit_list_price_local", "discount_pct", "net_amount_local"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    del canonical
    return df, fixes


# ---------------------------------------------------------------------------
# 3-5. Validate, repair, conform
# ---------------------------------------------------------------------------
def assignment_lookup(asg: pd.DataFrame, rep: pd.Series, dates: pd.Series) -> pd.Series:
    """Return the team_id a rep belonged to on each date (point-in-time join)."""
    a = asg.copy()
    a["start_date"] = pd.to_datetime(a["start_date"])
    a["end_date"] = pd.to_datetime(a["end_date"]).fillna(pd.Timestamp("2099-12-31"))
    probe = pd.DataFrame({"rep_code": rep.values, "d": dates.values, "_i": np.arange(len(rep))})
    m = probe.merge(a, on="rep_code", how="left")
    m = m[(m["d"] >= m["start_date"]) & (m["d"] <= m["end_date"])]
    m = m.drop_duplicates("_i", keep="last").set_index("_i")["team_id"]
    return pd.Series(m.reindex(np.arange(len(rep))).values, index=rep.index)


def process_sales(cfg, data) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict]]:
    sales, fixes = standardise_sales(data["sales_lines"])
    products = data["products"]
    fx = data["fx_rates"]
    ctx = {
        "start": pd.Timestamp(cfg["project"]["start_date"]), "end": pd.Timestamp(cfg["project"]["end_date"]),
        "rep_codes": set(data["employees"].loc[data["employees"]["role"] == "Sales Representative", "employee_code"]),
        "skus": set(products["sku"]), "customer_codes": set(data["customers"]["customer_code"]),
        "currencies": set(fx["currency"]),
    }
    flags, dq_log = evaluate(sales, SALES_RULES, ctx)

    actions = dq_log.set_index("rule_id")["action"]
    drop_mask = flags[[r for r in flags if actions[r] == "DROP"]].any(axis=1)
    q_cols = [r for r in flags if actions[r] == "QUARANTINE"]
    quarantine_mask = flags[q_cols].any(axis=1) & ~drop_mask

    quarantine = sales[quarantine_mask].copy()
    quarantine["dq_failed_rules"] = flags.loc[quarantine_mask, q_cols].apply(
        lambda r: ";".join(r.index[r.values]), axis=1)
    clean = sales[~drop_mask & ~quarantine_mask].copy()

    # -- repair zero prices from product master (DQ-S13 FIX) --------------------
    fx_map = fx.set_index(["fx_month", "currency"])["rate_per_usd"]
    clean["fx_month"] = clean["document_date"].dt.strftime("%Y-%m")
    clean["fx_rate"] = fx_map.reindex(pd.MultiIndex.from_arrays([clean["fx_month"], clean["currency"]])).values
    price_usd = clean["sku"].map(products.set_index("sku")["list_price_usd"])
    fix_mask = flags.loc[clean.index, "DQ-S13"]
    clean.loc[fix_mask, "unit_list_price_local"] = (price_usd[fix_mask] * clean.loc[fix_mask, "fx_rate"]).round(2)
    clean.loc[fix_mask, "net_amount_local"] = (clean.loc[fix_mask, "quantity"] * clean.loc[fix_mask, "unit_list_price_local"]
                                               * (1 - clean.loc[fix_mask, "discount_pct"])).round(2)

    # -- team at time of sale; credit notes inherit the original invoice's rep/team --
    clean["team_id"] = assignment_lookup(data["rep_assignments"], clean["rep_code"], clean["document_date"])
    inv_team = clean[clean["doc_type"] == "INV"].drop_duplicates("document_no").set_index("document_no")
    is_cn = clean["doc_type"] == "CN"
    clean.loc[is_cn, "team_id"] = clean.loc[is_cn, "original_document_no"].map(inv_team["team_id"])
    clean["original_date"] = clean["original_document_no"].map(inv_team["document_date"])

    # post-conformance rules: unresolvable team, orphan credit notes
    extra = []
    no_team = clean["team_id"].isna() & ~is_cn
    orphan_cn = is_cn & clean["original_date"].isna()
    # over-credit: credited value exceeds the CLEAN value of the original invoice
    # (happens when part of the original invoice was quarantined upstream)
    local_usd = clean["net_amount_local"] / clean["fx_rate"]
    invoiced = local_usd[~is_cn].groupby(clean.loc[~is_cn, "document_no"]).sum()
    credited = (-local_usd[is_cn]).groupby(clean.loc[is_cn, "original_document_no"]).sum()
    over = credited[credited > invoiced.reindex(credited.index).fillna(0) + 1.0].index
    over_credit = is_cn & clean["original_document_no"].isin(over) & ~orphan_cn
    for rid, desc, mask in [("DQ-S16", "Rep not assigned to any team on the document date", no_team),
                            ("DQ-S17", "Credit note whose original invoice is missing or quarantined", orphan_cn),
                            ("DQ-S18", "Credit note exceeds the clean value of its original invoice", over_credit)]:
        extra.append({"rule_id": rid, "table": "sales_lines", "description": desc, "severity": "HIGH",
                      "action": "QUARANTINE", "rows_affected": int(mask.sum()),
                      "pct_of_rows": round(100 * mask.sum() / len(sales), 3)})
        mask = mask.reindex(clean.index, fill_value=False)
        if mask.any():
            q = clean.loc[mask].copy()
            q["dq_failed_rules"] = rid
            quarantine = pd.concat([quarantine, q[quarantine.columns.intersection(q.columns)]])
            clean = clean.loc[~mask]
            log.info("%s QUARANTINE %6d rows  %s", rid, int(mask.sum()), desc)
    is_cn = clean["doc_type"] == "CN"
    dq_log = pd.concat([dq_log, pd.DataFrame(extra)], ignore_index=True)

    # -- USD economics -----------------------------------------------------------
    prod = products.set_index("sku")
    strategic = set(cfg["strategic_product_lines"])
    clean["gross_amount_usd"] = (clean["quantity"] * clean["unit_list_price_local"] / clean["fx_rate"]).round(2)
    clean["net_amount_usd"] = (clean["net_amount_local"] / clean["fx_rate"]).round(2)
    clean["discount_amount_usd"] = (clean["gross_amount_usd"] - clean["net_amount_usd"]).round(2)
    clean["cost_amount_usd"] = (clean["quantity"] * clean["sku"].map(prod["list_price_usd"])
                                * clean["sku"].map(prod["unit_cost_pct"])).round(2)
    clean["gross_margin_usd"] = (clean["net_amount_usd"] - clean["cost_amount_usd"]).round(2)
    weight = cfg["commission_plans"][0]["strategic_weight"]
    clean["commission_weight"] = np.where(clean["sku"].map(prod["product_line"]).isin(strategic), weight, 1.0)
    clean["days_since_original"] = (clean["document_date"] - pd.to_datetime(clean["original_date"])).dt.days
    dim = clean["document_date"].dt.days_in_month
    clean["is_month_end_window"] = (clean["document_date"].dt.day > dim - cfg["analysis"]["month_end_days"]).astype(int)

    dup = int(drop_mask.sum())
    recon = {"raw_rows": len(sales), "dropped_duplicates": dup, "quarantined": len(quarantine),
             "clean_rows": len(clean)}
    recon["balanced"] = recon["raw_rows"] == dup + recon["quarantined"] + recon["clean_rows"]
    log.info("reconciliation raw=%d = clean %d + quarantine %d + duplicates %d -> %s",
             recon["raw_rows"], recon["clean_rows"], recon["quarantined"], dup,
             "OK" if recon["balanced"] else "MISMATCH")
    if not recon["balanced"]:
        raise ValueError("Row reconciliation failed - pipeline stopped")
    fixes_df = pd.DataFrame(fixes)
    fixes_df[["table", "severity", "action"]] = ["sales_lines", "LOW", "FIX"]
    dq_log = pd.concat([fixes_df, dq_log], ignore_index=True)
    return clean, quarantine, dq_log, [recon]


def process_targets(data) -> tuple[pd.DataFrame, pd.DataFrame]:
    t = data["sales_targets"].copy()
    ctx = {"rep_codes": set(data["employees"]["employee_code"])}
    flags, dq_log = evaluate(t, TARGET_RULES, ctx)
    keep = ~flags.any(axis=1)
    return t[keep].copy(), dq_log


# ---------------------------------------------------------------------------
# 6. Model
# ---------------------------------------------------------------------------
def build_dim_date(cfg) -> pd.DataFrame:
    d = pd.DataFrame({"date": pd.date_range(cfg["project"]["start_date"], cfg["project"]["end_date"], freq="D")})
    d["date_key"] = d["date"].dt.strftime("%Y%m%d").astype(int)
    d["year"] = d["date"].dt.year
    d["quarter"] = "Q" + d["date"].dt.quarter.astype(str)
    d["month_num"] = d["date"].dt.month
    d["month_name"] = d["date"].dt.strftime("%b")
    d["month_key"] = d["date"].dt.strftime("%Y%m").astype(int)
    d["year_month"] = d["date"].dt.strftime("%Y-%m")
    d["month_start_date"] = d["date"].dt.to_period("M").dt.start_time
    d["day_of_month"] = d["date"].dt.day
    d["days_in_month"] = d["date"].dt.days_in_month
    d["weekday_name"] = d["date"].dt.day_name()
    d["is_month_end_window"] = (d["day_of_month"] > d["days_in_month"] - cfg["analysis"]["month_end_days"]).astype(int)
    d["month_index"] = (d["year"] - d["year"].min()) * 12 + d["month_num"]
    return d[["date_key", "date", "year", "quarter", "month_num", "month_name", "month_key", "year_month",
              "month_start_date", "day_of_month", "days_in_month", "weekday_name", "is_month_end_window", "month_index"]]


def build_model(cfg, data, sales, targets) -> dict[str, pd.DataFrame]:
    months = month_range(cfg["project"]["start_date"], cfg["project"]["end_date"])
    countries = pd.DataFrame([{"country_code": k, "country_name": v["name"], "currency": v["currency"],
                               "region": v["region"]} for k, v in cfg["countries"].items()])
    departments = pd.DataFrame([{"department_code": k, "department_name": v} for k, v in cfg["departments"].items()])

    emp = data["employees"].copy()
    managers = emp[emp["role"] == "Sales Manager"]
    teams = data["teams"].merge(departments, on="department_code").merge(countries[["country_code", "country_name"]],
                                                                         on="country_code")
    teams = teams.merge(managers[["employee_code", "full_name"]].rename(
        columns={"employee_code": "manager_code", "full_name": "manager_name"}), on="manager_code", how="left")
    teams.insert(0, "team_key", range(1, len(teams) + 1))
    dim_team = teams[["team_key", "team_id", "team_name", "department_code", "department_name", "country_code",
                      "country_name", "manager_code", "manager_name", "hc_2024", "hc_2025"]]

    # employees: current team = latest assignment
    asg = data["rep_assignments"].copy()
    asg["start_date"] = pd.to_datetime(asg["start_date"])
    asg["end_date"] = pd.to_datetime(asg["end_date"])
    latest = asg.sort_values("start_date").drop_duplicates("rep_code", keep="last").set_index("rep_code")["team_id"]
    emp["current_team_id"] = emp["employee_code"].map(latest).fillna(emp["managed_team_id"])
    emp = emp.merge(teams[["team_id", "manager_code", "country_code", "department_code"]].rename(
        columns={"team_id": "current_team_id"}), on="current_team_id", how="left")
    emp.loc[emp["role"] == "Sales Manager", "manager_code"] = None
    emp["hire_date"] = pd.to_datetime(emp["hire_date"])
    emp["exit_date"] = pd.to_datetime(emp["exit_date"])
    emp["employment_status"] = np.where(emp["exit_date"].notna(), "Exited", "Active")
    end = pd.Timestamp(cfg["project"]["end_date"])
    tenure_m = ((emp["exit_date"].fillna(end) - emp["hire_date"]).dt.days / 30.44)
    emp["tenure_band"] = pd.cut(tenure_m, [-1, 6, 12, 24, 48, 999],
                                labels=["0-6m", "6-12m", "1-2y", "2-4y", "4y+"]).astype(str)
    emp["is_new_hire_in_period"] = (emp["hire_date"] >= pd.Timestamp(cfg["project"]["start_date"])).astype(int)
    emp.insert(0, "employee_key", range(1, len(emp) + 1))
    dim_employee = emp[["employee_key", "employee_code", "full_name", "role", "hire_date", "exit_date",
                        "employment_status", "tenure_band", "is_new_hire_in_period", "current_team_id",
                        "manager_code", "country_code", "department_code"]]

    prod = data["products"].copy()
    prod["is_strategic"] = prod["product_line"].isin(cfg["strategic_product_lines"]).astype(int)
    prod["commission_weight"] = np.where(prod["is_strategic"] == 1, cfg["commission_plans"][0]["strategic_weight"], 1.0)
    prod.insert(0, "product_key", range(1, len(prod) + 1))

    cust = data["customers"].copy()
    cust.insert(0, "customer_key", range(1, len(cust) + 1))

    # bridge: rep x month (team as of the 15th, tenure, ramp flag)
    ramp_n = len(cfg["quota"]["ramp_factors"])
    reps = dim_employee[dim_employee["role"] == "Sales Representative"]
    rows = []
    for m in months:
        mid = m.to_timestamp() + pd.Timedelta(days=14)
        active = asg[(asg["start_date"] <= mid) & (asg["end_date"].isna() | (asg["end_date"] >= mid))]
        for _, a in active.iterrows():
            hire = reps.loc[reps["employee_code"] == a["rep_code"], "hire_date"].iloc[0]
            tenure = (m - hire.to_period("M")).n
            rows.append({"rep_code": a["rep_code"], "month_key": int(m.strftime("%Y%m")), "team_id": a["team_id"],
                         "tenure_months": tenure, "is_ramp_month": int(tenure < ramp_n)})
    bridge = pd.DataFrame(rows)

    key_emp = dim_employee.set_index("employee_code")["employee_key"]
    key_team = dim_team.set_index("team_id")["team_key"]
    bridge["rep_key"] = bridge["rep_code"].map(key_emp)
    bridge["team_key"] = bridge["team_id"].map(key_team)

    # fact_sales
    fs = sales.copy()
    fs["date_key"] = fs["document_date"].dt.strftime("%Y%m%d").astype(int)
    fs["month_key"] = fs["document_date"].dt.strftime("%Y%m").astype(int)
    fs["rep_key"] = fs["rep_code"].map(key_emp)
    fs["team_key"] = fs["team_id"].map(key_team)
    fs["customer_key"] = fs["customer_code"].map(cust.set_index("customer_code")["customer_key"])
    fs["product_key"] = fs["sku"].map(prod.set_index("sku")["product_key"])
    fs["quantity"] = fs["quantity"].astype(int)
    fs["unit_list_price_usd"] = (fs["unit_list_price_local"] / fs["fx_rate"]).round(2)
    fact_sales = fs[["line_id", "document_no", "doc_type", "original_document_no", "date_key", "month_key",
                     "rep_key", "team_key", "customer_key", "product_key", "country_code", "currency", "fx_rate",
                     "quantity", "unit_list_price_usd", "discount_pct", "gross_amount_usd", "discount_amount_usd",
                     "net_amount_usd", "cost_amount_usd", "gross_margin_usd", "commission_weight",
                     "days_since_original", "is_month_end_window", "net_amount_local"]].sort_values(["date_key", "line_id"])

    # fact_targets (team as of the 15th from the bridge)
    t = targets.copy()
    t["month_key"] = t["target_month"].str.replace("-", "").astype(int)
    t["rep_key"] = t["rep_code"].map(key_emp)
    t = t.merge(bridge[["rep_key", "month_key", "team_key", "is_ramp_month"]], on=["rep_key", "month_key"], how="left")
    t["date_key"] = t["month_key"] * 100 + 1
    fact_targets = t[["rep_key", "team_key", "month_key", "date_key", "target_amount_usd", "revision_no",
                      "is_ramp_month"]].sort_values(["month_key", "rep_key"])

    tp = data["team_plan"].copy()
    tp["month_key"] = tp["plan_month"].str.replace("-", "").astype(int)
    tp["team_key"] = tp["team_id"].map(key_team)
    tp["date_key"] = tp["month_key"] * 100 + 1
    fact_team_plan = tp[["team_key", "month_key", "date_key", "planned_headcount", "plan_amount_usd"]]

    plans = data["commission_plans"].copy()
    plans.insert(0, "plan_tier_key", range(1, len(plans) + 1))

    return {
        "dim_date": build_dim_date(cfg), "dim_country": countries, "dim_department": departments,
        "dim_team": dim_team, "dim_employee": dim_employee, "dim_product": prod, "dim_customer": cust,
        "dim_commission_plan": plans, "ref_fx_rates": data["fx_rates"],
        "bridge_rep_team_month": bridge[["rep_key", "month_key", "team_key", "tenure_months", "is_ramp_month"]],
        "fact_sales": fact_sales, "fact_targets": fact_targets, "fact_team_plan": fact_team_plan,
    }


def target_gap_check(model) -> dict:
    """FLAG: rep-months where the rep was active but no target exists."""
    b = model["bridge_rep_team_month"][["rep_key", "month_key"]]
    t = model["fact_targets"][["rep_key", "month_key"]].assign(has=1)
    m = b.merge(t, how="left", on=["rep_key", "month_key"])
    missing = m[m["has"].isna()]
    return {"rule_id": "DQ-T04", "table": "targets", "description": "Active rep-month with no target (commission not computable)",
            "severity": "HIGH", "action": "FLAG", "rows_affected": len(missing),
            "pct_of_rows": round(100 * len(missing) / len(b), 3)}, missing


def write_dq_report(cfg, dq_log, recon, integrity, missing_targets) -> None:
    out = cfg["paths"]["processed"]
    dq_log.to_csv(out / "dq_rule_log.csv", index=False)
    integrity.to_csv(out / "dq_integrity_checks.csv", index=False)
    missing_targets.to_csv(out / "dq_missing_targets.csv", index=False)
    r = recon[0]
    lines = ["# Data Quality Report", "",
             "_Auto-generated by `python/etl.py` on every pipeline run._", "",
             "## Row reconciliation (sales lines)", "",
             "| Raw rows | Clean rows | Quarantined | Duplicates dropped | Balanced |", "|---:|---:|---:|---:|:---:|",
             f"| {r['raw_rows']:,} | {r['clean_rows']:,} | {r['quarantined']:,} | {r['dropped_duplicates']:,} | "
             f"{'Yes' if r['balanced'] else 'No'} |", "",
             "## Rules triggered", "", "| Rule | Table | Description | Severity | Action | Rows | % |",
             "|---|---|---|---|---|---:|---:|"]
    for _, x in dq_log.iterrows():
        pct = "" if pd.isna(x.get("pct_of_rows")) else f"{x['pct_of_rows']:.2f}"
        lines.append(f"| {x['rule_id']} | {x['table']} | {x['description']} | {x['severity']} | {x['action']} | "
                     f"{int(x['rows_affected']):,} | {pct} |")
    lines += ["", "## Post-load integrity checks", "", "| Check | Description | Result |", "|---|---|:---:|"]
    for _, x in integrity.iterrows():
        lines.append(f"| {x['check_id']} | {x['description']} | {'PASS' if x['passed'] else 'FAIL'} |")
    (out / "dq_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    cfg = load_config()
    ensure_dirs(cfg)
    data = extract(cfg)
    sales, quarantine, dq_sales, recon = process_sales(cfg, data)
    targets, dq_targets = process_targets(data)
    model = build_model(cfg, data, sales, targets)
    gap_rule, missing = target_gap_check(model)
    dq_log = pd.concat([dq_sales, dq_targets, pd.DataFrame([gap_rule])], ignore_index=True)
    integrity = integrity_checks(model)
    if not integrity["passed"].all():
        raise ValueError("Integrity checks failed - see log above")

    out = cfg["paths"]["processed"]
    for name, df in model.items():
        df.to_csv(out / f"{name}.csv", index=False)
        log.info("loaded %-24s %7d rows", name, len(df))
    quarantine.to_csv(out / "quarantine_sales_lines.csv", index=False)
    write_dq_report(cfg, dq_log, recon, integrity, missing)
    log.info("ETL complete. Quarantined %d lines; DQ report -> %s", len(quarantine), out / "dq_report.md")


if __name__ == "__main__":
    main()
