"""Advanced analysis on top of the KPI layer.

    1. plan_to_actual_bridge    plan -> vacancy -> ramp/other -> allocated target -> execution -> actual
    2. yoy_revenue_bridge       2024 -> 2025 revenue change decomposed (LMDI) into
                                headcount, deal volume, deal size, discount and credit-note effects
    3. detect_anomalies         robust z-score (median / MAD) on each rep's monthly achievement
    4. detect_declines          slope of rolling-3M achievement over the last 6 months
    5. commission_scenarios     what-if: clawback booked to the month of sale; quarterly tiers
    6. commission_concentration how concentrated is commission spend across reps

Every number used in insights/business_insights.md is written to
reports/analysis_results.json by this script.

Run:  python python/analysis.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from kpi_engine import add_commission, load as load_model, rep_month_kpis, safe_div
from utils import get_logger, load_config, plan_for_month, read_csv_checked, tier_for_achievement

log = get_logger("analysis")


# ---------------------------------------------------------------------------
def plan_to_actual_bridge(team: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """Split the gap between capacity plan and actual into capacity vs execution."""
    t = team.copy()
    t["quota_per_seat"] = safe_div(t["plan"], t["planned_headcount"])
    t["vacancy_effect"] = -t["vacant_seats"] * t["quota_per_seat"]
    keys = ["year"] + ([by] if by else [])
    g = t.groupby(keys).agg(plan=("plan", "sum"), vacancy_effect=("vacancy_effect", "sum"),
                            allocated_target=("allocated_target", "sum"), actual=("actual", "sum")).reset_index()
    g["ramp_and_other_effect"] = g["allocated_target"] - g["plan"] - g["vacancy_effect"]
    g["execution_effect"] = g["actual"] - g["allocated_target"]
    g["check"] = g["plan"] + g["vacancy_effect"] + g["ramp_and_other_effect"] + g["execution_effect"] - g["actual"]
    assert g["check"].abs().max() < 1, "bridge does not add up"
    return g.drop(columns="check")


def _lmdi(v1: float, v0: float) -> float:
    return (v1 - v0) / (np.log(v1) - np.log(v0)) if v1 != v0 else v1


def yoy_revenue_bridge(rep: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """LMDI additive decomposition of rep-attributed net revenue, 2024 -> 2025.

    revenue = rep_months x deals/rep-month x gross list value/deal
              x (1 - discount rate) x (1 - credit-note rate)
    Effects sum exactly to the revenue change.
    """
    keys = ["year"] + ([by] if by else [])
    g = rep.groupby(keys).agg(rep_months=("rep_key", "count"), deals=("deals", "sum"),
                              gross=("gross_list_value", "sum"), invoiced=("invoiced_revenue", "sum"),
                              net=("actual", "sum")).reset_index()
    g["deals_per_rm"] = g["deals"] / g["rep_months"]
    g["gross_per_deal"] = g["gross"] / g["deals"]
    g["realisation"] = g["invoiced"] / g["gross"]        # 1 - discount rate
    g["retention"] = g["net"] / g["invoiced"]            # 1 - credit-note rate
    factors = {"headcount_effect": "rep_months", "deal_volume_effect": "deals_per_rm",
               "deal_size_effect": "gross_per_deal", "discount_effect": "realisation",
               "credit_note_effect": "retention"}
    groups = [None] if not by else sorted(g[by].unique())
    rows = []
    for grp in groups:
        s = g if grp is None else g[g[by] == grp]
        y0, y1 = s[s["year"] == 2024].iloc[0], s[s["year"] == 2025].iloc[0]
        L = _lmdi(y1["net"], y0["net"])
        row = {"group": grp or "Company", "revenue_2024": y0["net"], "revenue_2025": y1["net"],
               "change": y1["net"] - y0["net"]}
        for name, col in factors.items():
            row[name] = L * np.log(y1[col] / y0[col])
        row["discount_rate_2024"], row["discount_rate_2025"] = 1 - y0["realisation"], 1 - y1["realisation"]
        row["deals_per_rm_2024"], row["deals_per_rm_2025"] = y0["deals_per_rm"], y1["deals_per_rm"]
        rows.append(row)
    out = pd.DataFrame(rows)
    resid = (out[list(factors)].sum(axis=1) - out["change"]).abs().max()
    assert resid < 1, f"LMDI residual {resid}"
    return out


def detect_anomalies(cfg, rep: pd.DataFrame) -> pd.DataFrame:
    """Robust z-score of each rep-month's achievement vs. the rep's own history."""
    thr = cfg["analysis"]["anomaly_mad_threshold"]
    r = rep[(rep["target"].notna()) & (rep["is_ramp_month"] == 0)].copy()
    med = r.groupby("rep_key")["achievement"].transform("median")
    mad = r.groupby("rep_key")["achievement"].transform(lambda x: (x - x.median()).abs().median())
    r["robust_z"] = safe_div(0.6745 * (r["achievement"] - med), mad)
    r["anomaly_type"] = np.select([r["robust_z"] >= thr, r["robust_z"] <= -thr], ["Spike", "Drop"], default="")
    a = r[r["anomaly_type"] != ""].copy()
    a["month_end_share"] = a["month_end_share"].round(3)
    a["revenue_quality_flag"] = np.where(
        (a["anomaly_type"] == "Spike") & (a["month_end_share"] >= cfg["analysis"]["month_end_share_threshold"]),
        "Spike concentrated in month-end window", "")
    cols = ["employee_code", "full_name", "team_name", "month_key", "target", "actual", "achievement",
            "robust_z", "anomaly_type", "month_end_share", "tier_name", "commission", "revenue_quality_flag"]
    return a[cols].sort_values("robust_z", ascending=False)


def detect_declines(cfg, rep: pd.DataFrame) -> pd.DataFrame:
    """Reps still active at period end whose rolling-3M achievement trends down."""
    a = cfg["analysis"]
    last = rep["month_key"].max()
    r = rep[(rep["target"].notna()) & (rep["is_ramp_month"] == 0)].sort_values(["rep_key", "month_key"])
    active = set(r.loc[r["month_key"] == last, "rep_key"])
    out = []
    for k, g in r[r["rep_key"].isin(active)].groupby("rep_key"):
        g = g.tail(a["decline_window_months"])
        if len(g) < a["decline_window_months"]:
            continue
        y = g["rolling_3m_achievement"].to_numpy() * 100
        slope = np.polyfit(np.arange(len(y)), y, 1)[0]
        if slope <= a["decline_slope_pp_per_month"] and y[-1] / 100 <= a["decline_max_latest_rolling"]:
            out.append({"employee_code": g["employee_code"].iloc[0], "full_name": g["full_name"].iloc[0],
                        "team_name": g["team_name"].iloc[0], "slope_pp_per_month": round(slope, 1),
                        "rolling_3m_6_months_ago_pct": round(y[0], 1), "rolling_3m_latest_pct": round(y[-1], 1),
                        "ytd_achievement_pct": round(100 * g["ytd_achievement"].iloc[-1], 1)})
    return pd.DataFrame(out).sort_values("slope_pp_per_month") if out else pd.DataFrame()


# ---------------------------------------------------------------------------
def commission_scenarios(cfg, d: dict, rep: pd.DataFrame) -> dict:
    """What-if analysis on the commission policy using the same engine."""
    fs = d["fact_sales"]
    base_total = rep["commission"].sum()

    # A) clawback booked against the month of the ORIGINAL sale (restates that month's tier)
    inv_month = fs[fs["doc_type"] == "INV"].drop_duplicates("document_no").set_index("document_no")["month_key"]
    fs_b = fs.copy()
    in_win = (fs_b["doc_type"] == "CN") & (fs_b["days_since_original"] <= 90)
    fs_b.loc[in_win, "month_key"] = fs_b.loc[in_win, "original_document_no"].map(inv_month)
    d_b = {**d, "fact_sales": fs_b}
    rep_b = add_commission(cfg, rep_month_kpis(cfg, d_b), fs_b)
    total_b = rep_b["commission"].sum()

    # B) quarterly measurement: tiers on quarterly achievement, same rates / cap / weights
    r = rep[(rep["target"].notna())].copy()
    r["quarter"] = r["year"].astype(str) + "Q" + (((r["month_key"] % 100) - 1) // 3 + 1).astype(str)
    q = r.groupby(["rep_key", "quarter", "year"]).agg(actual=("actual", "sum"), target=("target", "sum"),
                                                      weighted_base=("weighted_base", "sum"),
                                                      commission_monthly=("commission", "sum")).reset_index()
    q["achievement"] = q["actual"] / q["target"]
    rates = []
    for y, a in zip(q["year"], q["achievement"]):
        plan = plan_for_month(cfg, pd.Period(f"{y}-06", "M"))
        rates.append(tier_for_achievement(plan, a)[1])
    q["rate"] = rates
    q["commission_quarterly"] = np.minimum(np.maximum(q["weighted_base"], 0), 1.5 * q["target"]) * q["rate"]
    paid_reps_monthly = (r["commission"] > 0).mean()
    return {
        "baseline_commission_usd": round(base_total, 0),
        "clawback_to_sale_month_commission_usd": round(total_b, 0),
        "clawback_leakage_usd": round(base_total - total_b, 0),
        "clawback_leakage_pct": round(100 * (base_total - total_b) / base_total, 2),
        "quarterly_commission_usd": round(q["commission_quarterly"].sum(), 0),
        "quarterly_vs_monthly_pct": round(100 * (q["commission_quarterly"].sum() / q["commission_monthly"].sum() - 1), 1),
        "pct_rep_months_paid_monthly": round(100 * paid_reps_monthly, 1),
        "pct_rep_quarters_paid_quarterly": round(100 * (q["commission_quarterly"] > 0).mean(), 1),
        "by_year": q.groupby("year")[["commission_monthly", "commission_quarterly"]].sum().round(0).to_dict("index"),
    }


def commission_concentration(rep: pd.DataFrame) -> dict:
    per_rep = rep.groupby("rep_key").agg(commission=("commission", "sum"), revenue=("actual", "sum"))
    per_rep = per_rep.sort_values("commission", ascending=False)
    n = len(per_rep)
    top = per_rep.head(max(1, round(0.2 * n)))
    return {"reps": n, "top20pct_share_of_commission": round(100 * top["commission"].sum() / per_rep["commission"].sum(), 1),
            "top20pct_share_of_revenue": round(100 * top["revenue"].sum() / per_rep["revenue"].sum(), 1),
            "pearson_revenue_vs_commission_rep_month": round(rep[["actual", "commission"]].corr().iloc[0, 1], 3)}


# ---------------------------------------------------------------------------
def main() -> None:
    cfg = load_config()
    p = cfg["paths"]["processed"]
    rep = read_csv_checked(p / "kpi_rep_month.csv")
    team = read_csv_checked(p / "kpi_team_month.csv")
    d = load_model(cfg)
    out_dir = cfg["paths"]["reports"] / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    bridge_co = plan_to_actual_bridge(team)
    bridge_cty = plan_to_actual_bridge(team, "country_code")
    yoy_co = yoy_revenue_bridge(rep)
    yoy_cty = yoy_revenue_bridge(rep, "country_code")
    yoy_dept = yoy_revenue_bridge(rep, "department_code")
    anomalies = detect_anomalies(cfg, rep)
    declines = detect_declines(cfg, rep)
    scen = commission_scenarios(cfg, d, rep)
    conc = commission_concentration(rep)

    for name, df in {"plan_bridge_company": bridge_co, "plan_bridge_country": bridge_cty,
                     "yoy_bridge_company": yoy_co, "yoy_bridge_country": yoy_cty, "yoy_bridge_department": yoy_dept,
                     "anomalies": anomalies, "declining_reps_trend": declines}.items():
        df.to_csv(out_dir / f"{name}.csv", index=False)
        log.info("wrote %-24s %4d rows", name, len(df))

    # long-format tables for Power BI waterfall visuals ----------------------
    steps = [("1 Capacity plan", "plan"), ("2 Vacant seats", "vacancy_effect"),
             ("3 Ramp & other", "ramp_and_other_effect"), ("4 Execution", "execution_effect")]
    long = bridge_cty.melt(id_vars=["year", "country_code"], value_vars=[c for _, c in steps],
                           var_name="measure", value_name="amount_usd")
    long["step"] = long["measure"].map({c: n for n, c in steps})
    long.drop(columns="measure").sort_values(["year", "country_code", "step"]) \
        .to_csv(out_dir / "pbi_plan_bridge_long.csv", index=False)
    fx = ["headcount_effect", "deal_volume_effect", "deal_size_effect", "discount_effect", "credit_note_effect"]
    yl = pd.concat([yoy_co, yoy_cty]).melt(id_vars=["group"], value_vars=fx, var_name="driver", value_name="amount_usd")
    yl.to_csv(out_dir / "pbi_yoy_bridge_long.csv", index=False)
    pd.DataFrame([{"scenario": "Monthly tiers (actual plan)", "year": y, "commission_usd": v["commission_monthly"]}
                  for y, v in scen["by_year"].items()] +
                 [{"scenario": "Quarterly tiers (what-if)", "year": y, "commission_usd": v["commission_quarterly"]}
                  for y, v in scen["by_year"].items()]).to_csv(out_dir / "pbi_commission_scenarios.csv", index=False)

    # headline metrics for README / insights ---------------------------------
    yr = rep.groupby("year").agg(actual=("actual", "sum"), target=("target", "sum"), commission=("commission", "sum"),
                                 gm=("gross_margin", "sum"))
    tgt_growth = yr.loc[2025, "target"] / yr.loc[2024, "target"] - 1
    team_yr = team.groupby("year")[["plan", "actual", "allocated_target"]].sum()
    results = {
        "revenue_2024": round(team_yr.loc[2024, "actual"], 0), "revenue_2025": round(team_yr.loc[2025, "actual"], 0),
        "target_2024": round(team_yr.loc[2024, "allocated_target"], 0),
        "target_2025": round(team_yr.loc[2025, "allocated_target"], 0),
        "achievement_2024_pct": round(100 * team_yr.loc[2024, "actual"] / team_yr.loc[2024, "allocated_target"], 1),
        "achievement_2025_pct": round(100 * team_yr.loc[2025, "actual"] / team_yr.loc[2025, "allocated_target"], 1),
        "revenue_growth_pct": round(100 * (team_yr.loc[2025, "actual"] / team_yr.loc[2024, "actual"] - 1), 1),
        "target_growth_pct": round(100 * (team_yr.loc[2025, "allocated_target"] / team_yr.loc[2024, "allocated_target"] - 1), 1),
        "rep_target_growth_pct": round(100 * tgt_growth, 1),
        "commission_2024": round(yr.loc[2024, "commission"], 0), "commission_2025": round(yr.loc[2025, "commission"], 0),
        "commission_to_revenue_2024_pct": round(100 * yr.loc[2024, "commission"] / yr.loc[2024, "actual"], 2),
        "commission_to_revenue_2025_pct": round(100 * yr.loc[2025, "commission"] / yr.loc[2025, "actual"], 2),
        "gross_margin_pct": round(100 * yr["gm"].sum() / yr["actual"].sum(), 1),
        "active_reps_end": int((rep["month_key"] == rep["month_key"].max()).sum()),
        "unique_reps": int(rep["rep_key"].nunique()),
        "anomalies": int(len(anomalies)),
        "anomaly_spikes_month_end": int((anomalies["revenue_quality_flag"] != "").sum()),
        "declining_reps_trend": int(len(declines)),
        "plan_bridge_company": bridge_co.round(0).to_dict("records"),
        "plan_bridge_country_2025": bridge_cty[bridge_cty["year"] == 2025].round(0).to_dict("records"),
        "yoy_bridge_company": yoy_co.round(4).to_dict("records"),
        "yoy_bridge_country": yoy_cty.round(4).to_dict("records"),
        "yoy_bridge_department": yoy_dept.round(4).to_dict("records"),
        "commission_scenarios": scen,
        "commission_concentration": conc,
    }
    (cfg["paths"]["reports"] / "analysis_results.json").write_text(json.dumps(results, indent=2, default=float))
    log.info("revenue 2024 %.1fM -> 2025 %.1fM (%+.1f%%) vs target growth %+.1f%%",
             results["revenue_2024"] / 1e6, results["revenue_2025"] / 1e6, results["revenue_growth_pct"],
             results["target_growth_pct"])
    log.info("anomalies %d | declining reps %d | clawback leakage USD %.0f | quarterly vs monthly %+.1f%%",
             len(anomalies), len(declines), scen["clawback_leakage_usd"], scen["quarterly_vs_monthly_pct"])


if __name__ == "__main__":
    main()
