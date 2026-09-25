"""KPI engine: one consistent calculation of every KPI in the project.

Outputs (data/processed/ and loaded into SQLite as tables):
    kpi_rep_month      rep x month: target, actual, achievement, variance, MoM, YTD,
                       rolling 3M, drivers, ranks, commission tier / base / payout
    kpi_team_month     team x month: plan, allocated target, actual, capacity gap, YTD, rank
    kpi_country_month  country x month roll-up with rank

The commission here is calculated in pandas, independently of
sql/05_commission.sql. reconcile_with_sql() compares the two for every
rep-month and fails the pipeline on any difference above USD 0.01.

Run:  python python/kpi_engine.py
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from utils import get_logger, load_config, plan_for_month, read_csv_checked, tier_for_achievement

log = get_logger("kpi_engine")


def safe_div(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where((b == 0) | np.isnan(b), np.nan, a / b)
    return out


def load(cfg) -> dict[str, pd.DataFrame]:
    p = cfg["paths"]["processed"]
    names = ["fact_sales", "fact_targets", "fact_team_plan", "bridge_rep_team_month", "dim_team", "dim_employee"]
    return {n: read_csv_checked(p / f"{n}.csv") for n in names}


# ---------------------------------------------------------------------------
# Rep x month
# ---------------------------------------------------------------------------
def rep_month_kpis(cfg, d) -> pd.DataFrame:
    fs = d["fact_sales"]
    inv = fs["doc_type"] == "INV"
    fs = fs.assign(
        inv_net=np.where(inv, fs["net_amount_usd"], 0.0),
        cn_net=np.where(~inv, fs["net_amount_usd"], 0.0),
        inv_gross=np.where(inv, fs["gross_amount_usd"], 0.0),
        inv_disc=np.where(inv, fs["discount_amount_usd"], 0.0),
        me_net=np.where(inv & (fs["is_month_end_window"] == 1), fs["net_amount_usd"], 0.0),
        strat_net=np.where(inv & (fs["commission_weight"] > 1), fs["net_amount_usd"], 0.0),
        inv_doc=np.where(inv, fs["document_no"], None),
    )
    agg = fs.groupby(["rep_key", "month_key"]).agg(
        actual=("net_amount_usd", "sum"), invoiced_revenue=("inv_net", "sum"), credit_notes=("cn_net", "sum"),
        gross_list_value=("inv_gross", "sum"), discount_value=("inv_disc", "sum"),
        gross_margin=("gross_margin_usd", "sum"), deals=("inv_doc", "nunique"),
        month_end_revenue=("me_net", "sum"), strategic_revenue=("strat_net", "sum"),
    ).reset_index()

    b = d["bridge_rep_team_month"].merge(d["dim_team"][["team_key", "team_name", "country_code", "department_code"]],
                                         on="team_key")
    t = d["fact_targets"][["rep_key", "month_key", "target_amount_usd"]].rename(columns={"target_amount_usd": "target"})
    r = b.merge(t, on=["rep_key", "month_key"], how="left").merge(agg, on=["rep_key", "month_key"], how="left")
    fill = [c for c in agg.columns if c not in ("rep_key", "month_key")]
    r[fill] = r[fill].fillna(0)
    r = r.merge(d["dim_employee"][["employee_key", "employee_code", "full_name"]]
                .rename(columns={"employee_key": "rep_key"}), on="rep_key")
    r = r.sort_values(["rep_key", "month_key"]).reset_index(drop=True)
    r["year"] = r["month_key"] // 100

    r["achievement"] = safe_div(r["actual"], r["target"])
    r["variance"] = r["actual"] - r["target"]
    r["discount_rate"] = safe_div(r["discount_value"], r["gross_list_value"])
    r["avg_deal_size"] = safe_div(r["invoiced_revenue"], r["deals"])
    r["credit_note_rate"] = safe_div(-r["credit_notes"], r["invoiced_revenue"])
    r["month_end_share"] = safe_div(r["month_end_revenue"], r["invoiced_revenue"])
    r["strategic_mix"] = safe_div(r["strategic_revenue"], r["invoiced_revenue"])
    r["gross_margin_pct"] = safe_div(r["gross_margin"], r["actual"])

    g = r.groupby("rep_key")
    r["prev_actual"] = g["actual"].shift(1)
    r["mom_growth"] = safe_div(r["actual"] - r["prev_actual"], r["prev_actual"])
    gy = r.groupby(["rep_key", "year"])
    r["ytd_actual"] = gy["actual"].cumsum()
    r["ytd_target"] = gy["target"].cumsum()
    r["ytd_achievement"] = safe_div(r["ytd_actual"], r["ytd_target"])
    r3a = g["actual"].rolling(3, min_periods=1).sum().reset_index(level=0, drop=True)
    r3t = g["target"].rolling(3, min_periods=1).sum().reset_index(level=0, drop=True)
    r["rolling_3m_achievement"] = safe_div(r3a, r3t)

    r["performance_band"] = np.select(
        [r["target"].isna(), r["achievement"] >= 1.2, r["achievement"] >= 1.0, r["achievement"] >= 0.8],
        ["No Target", "Exceeding (>=120%)", "On Target (100-119%)", "Near Target (80-99%)"],
        default="Below Target (<80%)")
    has_t = r["target"].notna()
    r.loc[has_t, "rank_in_team"] = r[has_t].groupby(["team_key", "month_key"])["achievement"].rank(
        method="min", ascending=False)
    r.loc[has_t, "rank_in_company"] = r[has_t].groupby("month_key")["achievement"].rank(method="min", ascending=False)
    return r.drop(columns=["prev_actual"])


def add_commission(cfg, r: pd.DataFrame, fs: pd.DataFrame) -> pd.DataFrame:
    """Apply the versioned commission plan (see docs/commission_policy.md)."""
    months = sorted(r["month_key"].unique())
    plan_by_month = {m: plan_for_month(cfg, pd.Period(f"{m // 100}-{m % 100:02d}", "M")) for m in months}

    fs = fs.copy()
    fs["clawback_days"] = fs["month_key"].map(lambda m: plan_by_month[m]["clawback_window_days"])
    inv = fs["doc_type"] == "INV"
    in_window = (~inv) & (fs["days_since_original"] <= fs["clawback_days"])
    fs["w_inv"] = np.where(inv, fs["net_amount_usd"] * fs["commission_weight"], 0.0)
    fs["w_claw"] = np.where(in_window, fs["net_amount_usd"] * fs["commission_weight"], 0.0)
    fs["cn_outside"] = np.where((~inv) & ~in_window, fs["net_amount_usd"], 0.0)
    w = fs.groupby(["rep_key", "month_key"])[["w_inv", "w_claw", "cn_outside"]].sum().reset_index()

    r = r.merge(w, on=["rep_key", "month_key"], how="left")
    r[["w_inv", "w_claw", "cn_outside"]] = r[["w_inv", "w_claw", "cn_outside"]].fillna(0)
    r = r.rename(columns={"w_inv": "weighted_invoiced", "w_claw": "weighted_clawback",
                          "cn_outside": "credit_notes_outside_window"})
    r["weighted_base"] = r["weighted_invoiced"] + r["weighted_clawback"]
    r["plan_id"] = r["month_key"].map(lambda m: plan_by_month[m]["plan_id"])
    cap_mult = r["month_key"].map(lambda m: plan_by_month[m]["cap_multiple_of_target"])
    r["cap_amount"] = cap_mult * r["target"]

    tiers = [tier_for_achievement(plan_by_month[m], a) for m, a in zip(r["month_key"], r["achievement"])]
    r["tier_name"] = [t[0] for t in tiers]
    r["commission_rate"] = [t[1] for t in tiers]
    base = np.minimum(np.maximum(r["weighted_base"], 0), r["cap_amount"])
    r["commissionable_base"] = np.where(r["target"].isna(), 0.0, base)
    r["commission"] = r["commissionable_base"] * r["commission_rate"]
    r["is_capped"] = (r["target"].notna() & (r["weighted_base"] > r["cap_amount"])).astype(int)
    r["effective_commission_rate"] = safe_div(r["commission"], r["actual"])
    bins = [-np.inf, 0.6, 0.8, 1.0, 1.2, 1.5, np.inf]
    labels = ["1 <60%", "2 60-79%", "3 80-99%", "4 100-119%", "5 120-149%", "6 150%+"]
    r["achievement_band"] = pd.cut(r["achievement"], bins=bins, labels=labels, right=False).astype(str)
    r.loc[r["target"].isna(), "achievement_band"] = "No Target"
    return r


# ---------------------------------------------------------------------------
# Team / country
# ---------------------------------------------------------------------------
def team_month_kpis(d, rep: pd.DataFrame) -> pd.DataFrame:
    fs = d["fact_sales"]
    rev = fs.groupby(["team_key", "month_key"]).agg(actual=("net_amount_usd", "sum"),
                                                    gross_margin=("gross_margin_usd", "sum")).reset_index()
    tgt = d["fact_targets"].groupby(["team_key", "month_key"])["target_amount_usd"].sum().rename("allocated_target")
    hc = d["bridge_rep_team_month"].groupby(["team_key", "month_key"]).agg(
        active_reps=("rep_key", "count"), ramping_reps=("is_ramp_month", "sum"))
    comm = rep.groupby(["team_key", "month_key"])["commission"].sum().rename("commission")
    tp = d["fact_team_plan"].rename(columns={"plan_amount_usd": "plan"})
    t = (tp.merge(d["dim_team"][["team_key", "team_id", "team_name", "country_code", "department_code"]], on="team_key")
         .merge(rev, on=["team_key", "month_key"], how="left")
         .merge(tgt, on=["team_key", "month_key"], how="left")
         .merge(hc, on=["team_key", "month_key"], how="left")
         .merge(comm, on=["team_key", "month_key"], how="left"))
    t[["actual", "gross_margin", "allocated_target", "active_reps", "ramping_reps", "commission"]] = \
        t[["actual", "gross_margin", "allocated_target", "active_reps", "ramping_reps", "commission"]].fillna(0)
    t = t.sort_values(["team_key", "month_key"]).reset_index(drop=True)
    t["year"] = t["month_key"] // 100
    t["achievement"] = safe_div(t["actual"], t["allocated_target"])
    t["plan_achievement"] = safe_div(t["actual"], t["plan"])
    t["variance_to_target"] = t["actual"] - t["allocated_target"]
    t["variance_to_plan"] = t["actual"] - t["plan"]
    t["capacity_gap"] = t["plan"] - t["allocated_target"]
    t["vacant_seats"] = (t["planned_headcount"] - t["active_reps"]).clip(lower=0)
    t["commission_to_revenue"] = safe_div(t["commission"], t["actual"])
    gy = t.groupby(["team_key", "year"])
    t["ytd_actual"] = gy["actual"].cumsum()
    t["ytd_target"] = gy["allocated_target"].cumsum()
    t["ytd_plan"] = gy["plan"].cumsum()
    t["ytd_achievement"] = safe_div(t["ytd_actual"], t["ytd_target"])
    prev = t.groupby("team_key")["actual"].shift(1)
    t["mom_growth"] = safe_div(t["actual"] - prev, prev)
    t["team_rank"] = t.groupby("month_key")["achievement"].rank(method="dense", ascending=False)
    return t


def country_month_kpis(team: pd.DataFrame) -> pd.DataFrame:
    cols = ["plan", "allocated_target", "actual", "gross_margin", "active_reps", "planned_headcount", "commission"]
    c = team.groupby(["country_code", "month_key", "year"])[cols].sum().reset_index()
    c = c.sort_values(["country_code", "month_key"]).reset_index(drop=True)
    c["achievement"] = safe_div(c["actual"], c["allocated_target"])
    c["plan_achievement"] = safe_div(c["actual"], c["plan"])
    c["variance_to_target"] = c["actual"] - c["allocated_target"]
    c["variance_to_plan"] = c["actual"] - c["plan"]
    gy = c.groupby(["country_code", "year"])
    c["ytd_achievement"] = safe_div(gy["actual"].cumsum(), gy["allocated_target"].cumsum())
    prev = c.groupby("country_code")["actual"].shift(1)
    c["mom_growth"] = safe_div(c["actual"] - prev, prev)
    c["country_rank"] = c.groupby("month_key")["achievement"].rank(method="dense", ascending=False)
    return c


# ---------------------------------------------------------------------------
def reconcile_with_sql(cfg, rep: pd.DataFrame) -> pd.DataFrame:
    """Compare pandas KPIs/commission with the SQL views, rep-month by rep-month."""
    con = sqlite3.connect(cfg["paths"]["database"])
    try:
        sql_c = pd.read_sql("SELECT rep_key, month_key, actual, target, tier_name, commission FROM vw_commission_rep_month", con)
        sql_p = pd.read_sql("SELECT rep_key, month_key, ytd_actual, rolling_3m_achievement, rank_in_team "
                            "FROM vw_rep_month_performance", con)
    finally:
        con.close()
    m = rep.merge(sql_c, on=["rep_key", "month_key"], suffixes=("", "_sql")).merge(
        sql_p, on=["rep_key", "month_key"], suffixes=("", "_sql"))
    checks = {
        "row_count": len(m) == len(rep) == len(sql_c),
        "actual": np.allclose(m["actual"], m["actual_sql"], atol=0.01),
        "ytd_actual": np.allclose(m["ytd_actual"], m["ytd_actual_sql"], atol=0.01),
        "rolling_3m": np.allclose(m["rolling_3m_achievement"].fillna(-1), m["rolling_3m_achievement_sql"].fillna(-1), atol=1e-9),
        "rank_in_team": bool((m["rank_in_team"].fillna(-1) == m["rank_in_team_sql"].fillna(-1)).all()),
        "tier": bool((m["tier_name"] == m["tier_name_sql"]).all()),
        "commission": np.allclose(m["commission"], m["commission_sql"], atol=0.01),
    }
    out = pd.DataFrame([{"check": k, "passed": bool(v)} for k, v in checks.items()])
    out["python_total_commission"] = round(rep["commission"].sum(), 2)
    out["sql_total_commission"] = round(sql_c["commission"].sum(), 2)
    for _, x in out.iterrows():
        (log.info if x.passed else log.error)("reconcile %-13s %s", x.check, "PASS" if x.passed else "FAIL")
    if not out["passed"].all():
        raise ValueError("Python vs SQL reconciliation failed")
    return out


def main() -> None:
    cfg = load_config()
    d = load(cfg)
    rep = add_commission(cfg, rep_month_kpis(cfg, d), d["fact_sales"])
    team = team_month_kpis(d, rep)
    country = country_month_kpis(team)
    recon = reconcile_with_sql(cfg, rep)

    out = cfg["paths"]["processed"]
    con = sqlite3.connect(cfg["paths"]["database"])
    try:
        for name, df in [("kpi_rep_month", rep), ("kpi_team_month", team), ("kpi_country_month", country)]:
            if "date_key" not in df.columns:                           # first of month -> dim_date relationship
                df.insert(2, "date_key", df["month_key"] * 100 + 1)
            df.to_csv(out / f"{name}.csv", index=False)
            df.to_sql(name, con, if_exists="replace", index=False)
            log.info("wrote %-18s %6d rows", name, len(df))
    finally:
        con.close()
    recon.to_csv(out / "reconciliation_python_vs_sql.csv", index=False)
    log.info("total commission USD %,.0f | reps paid in %.0f%% of rep-months".replace(",", ""),
             rep["commission"].sum(), 100 * (rep["commission"] > 0).mean())


if __name__ == "__main__":
    main()
