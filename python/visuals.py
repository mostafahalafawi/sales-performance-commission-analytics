"""Render README visuals from pipeline outputs (no hard-coded numbers).

images/dashboard_overview.png      Executive Overview preview (Power BI page 1 layout)
images/performance_analysis.png    Team & salesperson performance / root cause
images/trend_analysis.png          Trend, seasonality and variance bridges
images/commission_incentives.png   Commission & incentive analytics
images/data_model.png              Star schema (ERD)
images/architecture.png            Pipeline architecture

The dashboard images are PREVIEWS rendered with matplotlib from the same KPI
tables Power BI consumes; dashboard/README.md specifies the Power BI build.

Run:  python python/visuals.py
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402
from matplotlib.ticker import MultipleLocator  # noqa: E402

from utils import get_logger, load_config, read_csv_checked  # noqa: E402

log = get_logger("visuals")

# Palette (validated categorical order, light mode) -------------------------
S = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
POS, NEG, TOTAL = "#2a78d6", "#e34948", "#52514e"
GOOD, WARN, CRIT = "#0ca30c", "#fab219", "#d03b3b"
PAGE, SURFACE = "#f9f9f7", "#fcfcfb"
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
COUNTRY_NAME = {"EG": "Egypt", "SA": "Saudi Arabia", "AE": "UAE", "OM": "Oman"}
COUNTRY_COLOR = {"EG": S[0], "SA": S[1], "AE": S[2], "OM": S[3]}

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9.5, "axes.edgecolor": BASE, "axes.labelcolor": INK2,
    "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.titlecolor": INK, "axes.titlelocation": "left", "axes.titlepad": 10, "figure.facecolor": PAGE,
    "axes.facecolor": SURFACE, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False,
    "legend.fontsize": 8.5, "savefig.facecolor": PAGE,
})


def usd(x, decimals=1):
    sign = "-" if x < 0 else ""
    x = abs(x)
    return f"{sign}${x / 1e6:,.{decimals}f}M" if x >= 1e6 else f"{sign}${x / 1e3:,.0f}K"


def card(fig, rect, title, value, sub="", sub_color=INK2):
    ax = fig.add_axes(rect)
    ax.set_axis_off()
    ax.add_patch(FancyBboxPatch((0, 0), 1, 1, boxstyle="round,pad=0,rounding_size=0.06",
                                transform=ax.transAxes, fc=SURFACE, ec=GRID, lw=1))
    ax.text(0.07, 0.74, title, fontsize=9, color=INK2, transform=ax.transAxes)
    ax.text(0.07, 0.36, value, fontsize=19, color=INK, weight="bold", transform=ax.transAxes)
    ax.text(0.07, 0.12, sub, fontsize=8.5, color=sub_color, transform=ax.transAxes)


def header(fig, title, subtitle):
    fig.text(0.02, 0.965, title, fontsize=17, weight="bold", color=INK)
    fig.text(0.02, 0.935, subtitle, fontsize=9.5, color=INK2)
    fig.text(0.98, 0.965, "Nexora Business Solutions (fictional) | synthetic data", fontsize=8.5,
             color=MUTED, ha="right")


def footer(fig, text):
    fig.text(0.02, 0.012, text, fontsize=8, color=MUTED)


def waterfall(ax, labels, values, total_idx):
    """values: absolute totals at total_idx positions, deltas elsewhere."""
    running, bottoms, heights, colors = 0.0, [], [], []
    for i, v in enumerate(values):
        if i in total_idx:
            bottoms.append(0)
            heights.append(v)
            colors.append(TOTAL)
            running = v
        else:
            bottoms.append(running if v >= 0 else running + v)
            heights.append(abs(v))
            colors.append(POS if v >= 0 else NEG)
            running += v
    x = np.arange(len(values))
    ax.bar(x, heights, bottom=bottoms, color=colors, width=0.62, edgecolor=SURFACE, linewidth=2)
    for i, v in enumerate(values):
        top = bottoms[i] + heights[i]
        txt = usd(v) if i in total_idx else ("+" if v >= 0 else "") + usd(v)
        ax.text(i, top + max(heights) * 0.015, txt, ha="center", va="bottom", fontsize=8.5, color=INK)
    ax.set_xticks(x, labels, fontsize=8.5)
    ax.yaxis.set_major_locator(MultipleLocator(2e6))
    ax.yaxis.set_major_formatter(lambda y, _: f"{y / 1e6:.0f}M")
    ax.grid(axis="x", visible=False)


# ---------------------------------------------------------------------------
def dashboard_overview(cfg, d, res, path):
    comp, cty, prod, tiers = d["company"], d["country"], d["q09"], d["q07"]
    fig = plt.figure(figsize=(16, 9))
    header(fig, "Executive Overview", "Sales performance vs target and incentive cost | FY2025 with FY2024 comparison")
    y25 = comp[comp["year"] == 2025]
    rev, tgt = res["revenue_2025"], res["target_2025"]
    cards = [
        ("Net revenue FY2025", usd(rev), f"{res['revenue_growth_pct']:+.1f}% vs FY2024", GOOD if res['revenue_growth_pct'] > 0 else CRIT),
        ("Target FY2025", usd(tgt), f"{res['target_growth_pct']:+.1f}% vs FY2024 target", INK2),
        ("Achievement", f"{res['achievement_2025_pct']:.1f}%", f"FY2024: {res['achievement_2024_pct']:.1f}%",
         CRIT if res['achievement_2025_pct'] < 95 else GOOD),
        ("Variance to target", usd(rev - tgt), "net revenue - allocated target", CRIT if rev < tgt else GOOD),
        ("Commission FY2025", usd(res["commission_2025"], 2), f"{res['commission_to_revenue_2025_pct']:.2f}% of revenue", INK2),
        ("Active reps (Dec-25)", f"{res['active_reps_end']}", f"{res['unique_reps']} reps over 24 months", INK2),
    ]
    for i, c in enumerate(cards):
        card(fig, [0.02 + i * 0.1633, 0.78, 0.153, 0.12], *c)

    # monthly actual vs target
    ax = fig.add_axes([0.045, 0.40, 0.55, 0.31])
    x = np.arange(len(comp))
    ax.bar(x, comp["actual"], color=S[0], width=0.72, label="Actual net revenue", edgecolor=SURFACE, linewidth=1.5)
    ax.plot(x, comp["target"], color=INK, lw=2, label="Target", marker="o", ms=3.5)
    ax.set_xticks(x[::2], [str(m)[:4] + "-" + str(m)[4:] for m in comp["month_key"]][::2], fontsize=8, rotation=0)
    ax.yaxis.set_major_formatter(lambda y, _: f"{y / 1e6:.0f}M")
    ax.set_title("Monthly net revenue vs target (USD)")
    ax.legend(loc="upper left", ncol=2)
    ax.grid(axis="x", visible=False)

    # country achievement 2024 vs 2025
    ax = fig.add_axes([0.66, 0.40, 0.32, 0.31])
    c = cty.groupby(["country_code", "year"])[["actual", "allocated_target"]].sum().reset_index()
    c["ach"] = 100 * c["actual"] / c["allocated_target"]
    order = c[c["year"] == 2025].sort_values("ach")["country_code"].tolist()
    yy = np.arange(len(order))
    for j, (yr, col) in enumerate([(2024, "#9ec5f4"), (2025, S[0])]):
        vals = [c[(c["country_code"] == k) & (c["year"] == yr)]["ach"].iloc[0] for k in order]
        ax.barh(yy + (j - 0.5) * 0.36, vals, height=0.34, color=col, label=f"FY{yr}", edgecolor=SURFACE)
        if yr == 2025:
            for yv, v in zip(yy, vals):
                ax.text(v + 1, yv + 0.18, f"{v:.1f}%", va="center", fontsize=8.5, color=INK)
    ax.axvline(100, color=INK, lw=1, ls="--")
    ax.set_yticks(yy, [COUNTRY_NAME[k] for k in order])
    ax.set_xlim(60, 120)
    ax.set_title("Achievement by country")
    ax.legend(loc="lower right")
    ax.grid(axis="y", visible=False)

    # product line revenue
    ax = fig.add_axes([0.125, 0.07, 0.33, 0.25])
    p = prod.sort_values("revenue_2025_usd")
    ax.barh(p["product_line"], p["revenue_2025_usd"], color=[S[6] if s else S[0] for s in p["is_strategic"]],
            height=0.6, edgecolor=SURFACE)
    for yv, (v, gm) in enumerate(zip(p["revenue_2025_usd"], p["gross_margin_pct"])):
        ax.text(v, yv, f"  {usd(v)} | GM {gm:.0f}%", va="center", fontsize=8.5, color=INK)
    ax.set_xlim(0, p["revenue_2025_usd"].max() * 1.45)
    ax.xaxis.set_major_formatter(lambda y, _: f"{y / 1e6:.0f}M")
    ax.set_title("FY2025 revenue by product line (violet = strategic, 1.25x weight)")
    ax.grid(axis="y", visible=False)

    # tier distribution
    ax = fig.add_axes([0.54, 0.07, 0.44, 0.25])
    t = tiers[tiers["tier_name"] != "No Target"]
    names = ["T0 Below Gate", "T1 Approaching", "T2 On Target", "T3 Accelerator"]
    for j, (plan, col) in enumerate([("CP2024", "#9ec5f4"), ("CP2025", S[0])]):
        v = [t[(t["plan_id"] == plan) & (t["tier_name"] == n)]["pct_of_rep_months"].sum() for n in names]
        ax.bar(np.arange(4) + (j - 0.5) * 0.36, v, width=0.34, color=col, label=plan.replace("CP", "Plan "),
               edgecolor=SURFACE)
        for xi, vi in enumerate(v):
            ax.text(xi + (j - 0.5) * 0.36, vi + 0.8, f"{vi:.0f}%", ha="center", fontsize=8, color=INK)
    ax.set_xticks(range(4), names)
    ax.set_ylabel("% of rep-months")
    ax.set_title("Incentive tier distribution")
    ax.legend(loc="upper right")
    ax.grid(axis="x", visible=False)
    footer(fig, "Preview rendered in Python from kpi_*.csv and reports/sql_results. Power BI build spec: dashboard/README.md")
    fig.savefig(path, dpi=110)
    plt.close(fig)


def performance_analysis(cfg, d, res, path):
    team, drv, lb, rep = d["q03"], d["q10"], d["q12"], d["rep"]
    fig = plt.figure(figsize=(16, 9))
    header(fig, "Team & Salesperson Performance", "Who is above or below target, and what separates under-performers from performers")

    # team dumbbell 2024 -> 2025
    ax = fig.add_axes([0.10, 0.52, 0.36, 0.36])
    p = team.pivot(index="team_name", columns="year", values="achievement_pct").sort_values(2025)
    yy = np.arange(len(p))
    ax.hlines(yy, p[2024], p[2025], color=BASE, lw=2)
    ax.scatter(p[2024], yy, color="#9ec5f4", s=55, zorder=3, label="FY2024", edgecolor=SURFACE, linewidth=1.5)
    ax.scatter(p[2025], yy, color=S[0], s=55, zorder=3, label="FY2025", edgecolor=SURFACE, linewidth=1.5)
    for yv, v, v0 in zip(yy, p[2025], p[2024]):
        right = v >= v0
        ax.text(v + (1.2 if right else -1.2), yv, f"{v:.0f}%", va="center", ha="left" if right else "right",
                fontsize=8, color=INK)
    ax.axvline(100, color=INK, lw=1, ls="--")
    ax.set_yticks(yy, p.index)
    ax.set_xlabel("Achievement %")
    ax.set_xlim(74, 118)
    ax.set_title("Team achievement FY2024 -> FY2025")
    ax.legend(loc="upper left")
    ax.grid(axis="y", visible=False)

    # driver profile
    ax = fig.add_axes([0.55, 0.52, 0.43, 0.36])
    metrics = [("deal_volume_index", "Deal volume\n(index)"), ("deal_size_index", "Deal size\n(index)"),
               ("discount_rate_pct", "Discount\nrate %"), ("credit_note_rate_pct", "Credit-note\nrate %"),
               ("strategic_mix_pct", "Strategic\nmix %")]
    base = drv[drv["segment"].str.startswith("3")].iloc[0]
    und = drv[drv["segment"].str.startswith("1")].iloc[0]
    rel = [100 * und[m] / base[m] for m, _ in metrics]
    colors = [NEG if (r < 100 and m in ("deal_volume_index", "deal_size_index", "strategic_mix_pct")) or
              (r > 100 and m in ("discount_rate_pct", "credit_note_rate_pct")) else POS for r, (m, _) in zip(rel, metrics)]
    ax.bar(range(len(metrics)), [r - 100 for r in rel], color=colors, width=0.6, edgecolor=SURFACE)
    for i, r in enumerate(rel):
        ax.text(i, (r - 100) + (3 if r >= 100 else -3), f"{r - 100:+.0f}%", ha="center",
                va="bottom" if r >= 100 else "top", fontsize=9, color=INK)
    ax.axhline(0, color=INK, lw=1)
    ax.set_xticks(range(len(metrics)), [l for _, l in metrics], fontsize=8.5)
    ax.set_ylabel("% difference, <80% vs >=100% reps")
    ax.set_title(f"Root-cause drivers: under-performers (n={int(und['reps'])}) vs performers (n={int(base['reps'])})")
    ax.set_ylim(min(r - 100 for r in rel) - 15, max(r - 100 for r in rel) + 15)
    ax.grid(axis="x", visible=False)

    # leaderboard top/bottom
    ax = fig.add_axes([0.17, 0.07, 0.29, 0.36])
    top = lb[lb["list"] == "Top 10"].head(6)
    bot = lb[lb["list"] == "Bottom 10"].tail(6)
    lbx = pd.concat([top, bot]).iloc[::-1]
    cols = [POS if l == "Top 10" else NEG for l in lbx["list"]]
    labels = [f"{n} ({t})" for n, t in zip(lbx["full_name"], lbx["team_name"])]
    ax.barh(range(len(lbx)), lbx["ytd_achievement_pct"], color=cols, height=0.62, edgecolor=SURFACE)
    for yv, v in enumerate(lbx["ytd_achievement_pct"]):
        ax.text(v + 1.5, yv, f"{v:.0f}%", va="center", fontsize=8, color=INK)
    ax.axvline(100, color=INK, lw=1, ls="--")
    ax.set_yticks(range(len(lbx)), labels, fontsize=8)
    ax.set_xlim(0, lbx["ytd_achievement_pct"].max() * 1.15)
    ax.set_title("FY2025 YTD achievement: top 6 and bottom 6 reps")
    ax.grid(axis="y", visible=False)

    # scatter: deals/month vs achievement (fully ramped rep-level FY2025)
    ax = fig.add_axes([0.55, 0.07, 0.43, 0.36])
    r = rep[(rep["year"] == 2025) & (rep["is_ramp_month"] == 0) & rep["target"].notna()]
    g = r.groupby(["rep_key", "department_code"]).agg(deals=("deals", "mean"), actual=("actual", "sum"),
                                                     target=("target", "sum"), n=("rep_key", "size")).reset_index()
    g = g[g["n"] >= 6]
    g["ach"] = 100 * g["actual"] / g["target"]
    g["deal_idx"] = 100 * g["deals"] / g.groupby("department_code")["deals"].transform("mean")
    for (dep, name), col in zip([("ENT", "Enterprise"), ("SME", "SME"), ("CHN", "Channel")], S[:3]):
        s = g[g["department_code"] == dep]
        ax.scatter(s["deal_idx"], s["ach"], s=34, color=col, label=name, alpha=0.9, edgecolor=SURFACE, linewidth=1)
    corr = np.corrcoef(g["deal_idx"], g["ach"])[0, 1]
    ax.axhline(100, color=INK, lw=1, ls="--")
    ax.set_xlabel("Deals per month (index, 100 = department average)")
    ax.set_ylabel("FY2025 achievement %")
    ax.set_title(f"Deal volume explains achievement (r = {corr:.2f})")
    ax.legend(loc="upper left")
    footer(fig, "Preview rendered in Python from reports/sql_results (q03, q10, q12) and kpi_rep_month.csv")
    fig.savefig(path, dpi=110)
    plt.close(fig)
    return corr


def trend_analysis(cfg, d, res, path):
    comp, seas = d["company"], d["q13"]
    fig = plt.figure(figsize=(16, 9))
    header(fig, "Trend & Variance Analysis", "Where the FY2025 gap came from: target growth, execution by market, and seasonality")

    ax = fig.add_axes([0.05, 0.53, 0.43, 0.34])
    x = np.arange(len(comp))
    ach = 100 * comp["actual"] / comp["target"]
    r3 = 100 * comp["actual"].rolling(3).sum() / comp["target"].rolling(3).sum()
    ax.plot(x, ach, color=S[0], lw=2, marker="o", ms=3.5, label="Monthly achievement")
    ax.plot(x, r3, color=S[1], lw=2, label="Rolling 3-month achievement")
    ax.axhline(100, color=INK, lw=1, ls="--")
    ax.set_xticks(x[::3], [f"{str(m)[:4]}-{str(m)[4:]}" for m in comp["month_key"]][::3], fontsize=8)
    ax.set_ylabel("Achievement %")
    ax.set_title("Company achievement trend")
    ax.legend(loc="lower left")

    ax = fig.add_axes([0.55, 0.53, 0.43, 0.34])
    months = seas["month_name"].tolist()
    for col, key, lab in [(S[0], "gcc_2025_ach_pct", "GCC FY2025"), (S[1], "egypt_2025_ach_pct", "Egypt FY2025"),
                          ("#9ec5f4", "gcc_2024_ach_pct", "GCC FY2024")]:
        ax.plot(months, seas[key], color=col, lw=2, marker="o", ms=3.5, label=lab)
    ax.axhline(100, color=INK, lw=1, ls="--")
    for m, lab in [("Mar", "Ramadan"), ("Aug", "Gulf summer")]:
        i = months.index(m)
        ax.axvspan(i - 0.4, i + 0.4, color=GRID, alpha=0.7, lw=0)
        ax.text(i, ax.get_ylim()[1] - 2, lab, ha="center", va="top", fontsize=8, color=INK2)
    ax.set_ylabel("Achievement %")
    ax.set_title("Seasonality vs flat target phasing")
    ax.legend(loc="lower right", ncol=3)

    # plan-to-actual bridge FY2025
    ax = fig.add_axes([0.05, 0.07, 0.43, 0.36])
    b = [r for r in res["plan_bridge_company"] if r["year"] == 2025][0]
    cty = {r["country_code"]: r["execution_effect"] for r in res["plan_bridge_country_2025"]}
    labels = ["Capacity\nplan", "Vacant\nseats", "Ramp &\nother", "Allocated\ntarget"] + \
             [f"{COUNTRY_NAME[k]}\nexecution" for k in ["AE", "EG", "SA", "OM"]] + ["Actual"]
    vals = [b["plan"], b["vacancy_effect"], b["ramp_and_other_effect"], b["allocated_target"]] + \
           [cty[k] for k in ["AE", "EG", "SA", "OM"]] + [b["actual"]]
    waterfall(ax, labels, vals, total_idx={0, 3, 8})
    ax.set_ylim(b["actual"] * 0.85, b["plan"] * 1.05)
    ax.set_title("FY2025 plan-to-actual bridge (USD)")

    # YoY LMDI bridge
    ax = fig.add_axes([0.55, 0.07, 0.43, 0.36])
    y = res["yoy_bridge_company"][0]
    labels = ["FY2024\nrevenue", "Headcount", "Deal\nvolume", "Deal size\n& mix", "Discount", "Credit\nnotes", "FY2025\nrevenue"]
    vals = [y["revenue_2024"], y["headcount_effect"], y["deal_volume_effect"], y["deal_size_effect"],
            y["discount_effect"], y["credit_note_effect"], y["revenue_2025"]]
    waterfall(ax, labels, vals, total_idx={0, 6})
    ax.set_ylim(y["revenue_2024"] * 0.85, y["revenue_2025"] * 1.08)
    ax.set_title("FY2024 -> FY2025 revenue bridge (LMDI decomposition, USD)")
    footer(fig, "Preview rendered in Python from vw_company_month, q13 and reports/analysis_results.json")
    fig.savefig(path, dpi=110)
    plt.close(fig)


def commission_incentives(cfg, d, res, path):
    curve, rep = d["q08"], d["rep"]
    sc = res["commission_scenarios"]
    fig = plt.figure(figsize=(16, 9))
    header(fig, "Commission & Incentives", "What the incentive plan pays for, how concentrated it is, and what a quarterly plan would change")
    cards = [
        ("Commission FY2024", usd(res["commission_2024"], 2), f"{res['commission_to_revenue_2024_pct']:.2f}% of revenue", INK2),
        ("Commission FY2025", usd(res["commission_2025"], 2), f"{res['commission_to_revenue_2025_pct']:.2f}% of revenue", INK2),
        ("Rep-months paid", f"{sc['pct_rep_months_paid_monthly']:.0f}%", f"quarterly plan: {sc['pct_rep_quarters_paid_quarterly']:.0f}% of rep-quarters", INK2),
        ("Top 20% of reps", f"{res['commission_concentration']['top20pct_share_of_commission']:.0f}%",
         f"of commission ({res['commission_concentration']['top20pct_share_of_revenue']:.0f}% of revenue)", INK2),
        ("Quarterly-plan what-if", f"{sc['quarterly_vs_monthly_pct']:+.1f}%", "commission cost vs monthly plan", GOOD if sc['quarterly_vs_monthly_pct'] < 0 else CRIT),
    ]
    for i, c in enumerate(cards):
        card(fig, [0.02 + i * 0.196, 0.78, 0.186, 0.12], *c)

    ax = fig.add_axes([0.05, 0.40, 0.43, 0.30])
    ax.bar(curve["achievement_band"], curve["commission_to_revenue_pct"], color=S[0], width=0.6, edgecolor=SURFACE)
    for i, v in enumerate(curve["commission_to_revenue_pct"]):
        ax.text(i, v + 0.03, f"{v:.2f}%", ha="center", fontsize=8.5, color=INK)
    ax.set_ylabel("Commission as % of revenue")
    ax.set_xlabel("Monthly achievement band")
    ax.set_title("Effective commission rate by achievement (cap bites at 150%+)")
    ax.grid(axis="x", visible=False)

    ax = fig.add_axes([0.55, 0.40, 0.43, 0.30])
    r = rep[rep["target"].notna()]
    ax.scatter(100 * r["achievement"].clip(upper=3), r["commission"], s=10, color=S[0], alpha=0.45, lw=0)
    for thr in (80, 100, 120):
        ax.axvline(thr, color=BASE, lw=1, ls="--")
    ax.set_xlim(0, 300)
    ax.set_xlabel("Monthly achievement % (clipped at 300%)")
    ax.set_ylabel("Commission (USD)")
    ax.set_title(f"Revenue vs commission per rep-month (Pearson r = {res['commission_concentration']['pearson_revenue_vs_commission_rep_month']:.2f})")

    ax = fig.add_axes([0.05, 0.07, 0.43, 0.24])
    by = sc["by_year"]
    yrs = sorted(by)
    for j, (k, col, lab) in enumerate([("commission_monthly", S[0], "Monthly tiers (actual plan)"),
                                       ("commission_quarterly", S[1], "Quarterly tiers (what-if)")]):
        v = [by[y][k] for y in yrs]
        ax.bar(np.arange(len(yrs)) + (j - 0.5) * 0.36, v, width=0.34, color=col, label=lab, edgecolor=SURFACE)
        for xi, vi in enumerate(v):
            ax.text(xi + (j - 0.5) * 0.36, vi * 1.01, usd(vi, 2), ha="center", va="bottom", fontsize=8.5, color=INK)
    ax.set_xticks(range(len(yrs)), [f"FY{y}" for y in yrs])
    ax.yaxis.set_major_formatter(lambda y, _: f"{y / 1e3:.0f}K")
    ax.set_ylim(0, max(max(by[y].values()) for y in yrs) * 1.35)
    ax.set_title("Commission cost: monthly vs quarterly measurement")
    ax.legend(loc="upper left", ncol=2)
    ax.grid(axis="x", visible=False)

    ax = fig.add_axes([0.55, 0.07, 0.43, 0.24])
    team = d["team"].groupby("team_name")[["commission", "actual"]].sum()
    team["rate"] = 100 * team["commission"] / team["actual"]
    team = team.sort_values("rate")
    ax.barh(team.index, team["rate"], color=S[0], height=0.6, edgecolor=SURFACE)
    for yv, v in enumerate(team["rate"]):
        ax.text(v + 0.01, yv, f"{v:.2f}%", va="center", fontsize=8.5, color=INK)
    ax.set_xlim(0, team["rate"].max() * 1.25)
    ax.set_xlabel("Commission as % of revenue")
    ax.set_title("Commission cost-to-revenue by team (FY2024-25)")
    ax.grid(axis="y", visible=False)
    footer(fig, "Preview rendered in Python from vw_commission_rep_month, q08 and commission scenarios in analysis.py")
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ---------------------------------------------------------------------------
def _table(ax, x, y, name, cols, color, w=0.17):
    h = 0.032 * (len(cols) + 1) + 0.01
    ax.add_patch(FancyBboxPatch((x, y - h), w, h, boxstyle="round,pad=0.004,rounding_size=0.008",
                                fc=SURFACE, ec=color, lw=1.6, zorder=2))
    ax.add_patch(FancyBboxPatch((x, y - 0.036), w, 0.036, boxstyle="round,pad=0.004,rounding_size=0.008",
                                fc=color, ec=color, lw=1.6, zorder=2))
    ax.text(x + 0.008, y - 0.019, name, fontsize=9.5, weight="bold", color="white", va="center", zorder=3)
    for i, c in enumerate(cols):
        key = c.startswith("PK") or c.startswith("FK")
        ax.text(x + 0.008, y - 0.058 - i * 0.032, c, fontsize=8, color=INK if key else INK2, va="center",
                weight="bold" if c.startswith("PK") else "normal", zorder=3)
    return (x, y - h, w, h)


def data_model(path):
    fig = plt.figure(figsize=(16, 10))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.text(0.02, 0.965, "Data Model - star schema", fontsize=17, weight="bold", color=INK)
    fig.text(0.02, 0.938, "3 fact tables + 1 bridge at month grain, 8 conformed dimensions, KPI layer written by the Python engine",
             fontsize=9.5, color=INK2)
    DIM, FACT, BR, KPI = S[0], S[1], S[6], S[2]
    t = {}
    t["fs"] = _table(ax, 0.415, 0.80, "fact_sales", ["PK line_id", "FK date_key", "FK rep_key", "FK team_key",
                     "FK customer_key", "FK product_key", "FK country_code", "doc_type (INV / CN)", "original_document_no",
                     "gross / discount / net_usd", "cost_usd, gross_margin_usd", "commission_weight", "days_since_original"], FACT, 0.18)
    t["ft"] = _table(ax, 0.415, 0.33, "fact_targets", ["PK rep_key + month_key", "FK team_key", "FK date_key (1st)",
                     "target_amount_usd", "revision_no", "is_ramp_month"], FACT, 0.18)
    t["tp"] = _table(ax, 0.64, 0.33, "fact_team_plan", ["PK team_key + month_key", "FK date_key (1st)",
                     "planned_headcount", "plan_amount_usd"], FACT)
    t["br"] = _table(ax, 0.19, 0.33, "bridge_rep_team_month", ["PK rep_key + month_key", "FK team_key",
                     "tenure_months", "is_ramp_month"], BR)
    t["dd"] = _table(ax, 0.19, 0.86, "dim_date", ["PK date_key", "month_key, year_month", "year, quarter",
                     "is_month_end_window"], DIM)
    t["de"] = _table(ax, 0.01, 0.62, "dim_employee", ["PK employee_key", "employee_code, full_name", "role, hire / exit",
                     "tenure_band", "current_team_id"], DIM, 0.16)
    t["dt"] = _table(ax, 0.19, 0.64, "dim_team", ["PK team_key", "team_id, team_name", "department_code / name",
                     "country_code, manager"], DIM)
    t["dp"] = _table(ax, 0.64, 0.86, "dim_product", ["PK product_key", "sku, product_line", "list_price_usd",
                     "unit_cost_pct", "is_strategic"], DIM)
    t["dc"] = _table(ax, 0.64, 0.64, "dim_customer", ["PK customer_key", "customer_code, segment", "industry, city",
                     "owning_team_id"], DIM)
    t["dco"] = _table(ax, 0.83, 0.86, "dim_country", ["PK country_code", "country_name", "currency, region"], DIM, 0.15)
    t["dcp"] = _table(ax, 0.83, 0.64, "dim_commission_plan", ["PK plan_tier_key", "plan_id, effective dates",
                      "tier min / max / rate", "weight, cap, clawback"], DIM, 0.15)
    t["kpi"] = _table(ax, 0.83, 0.33, "KPI layer", ["kpi_rep_month", "kpi_team_month", "kpi_country_month",
                      "vw_commission_rep_month", "vw_*_performance"], KPI, 0.15)

    def link(a, b, label=""):
        xa, ya, wa, ha = t[a]
        xb, yb, wb, hb = t[b]
        pa = (xa + wa / 2, ya + ha / 2)
        pb = (xb + wb / 2, yb + hb / 2)
        ax.annotate("", xy=pb, xytext=pa, arrowprops=dict(arrowstyle="-", color=BASE, lw=1.3,
                                                          connectionstyle="arc3,rad=0.0"), zorder=0)
        if label:
            ax.text((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2, label, fontsize=7.5, color=MUTED,
                    ha="center", va="center", backgroundcolor=PAGE, zorder=1)

    for dim in ["dd", "de", "dt", "dp", "dc", "dco"]:
        link("fs", dim, "1:*")
    for dim in ["de", "dt"]:
        link("ft", dim)
    link("tp", "dt")
    link("br", "de")
    link("br", "dt")
    link("kpi", "dcp")
    for i, (c, lab) in enumerate([(DIM, "Dimension"), (FACT, "Fact"), (BR, "Bridge"), (KPI, "KPI / analytical layer")]):
        ax.add_patch(FancyBboxPatch((0.02 + i * 0.14, 0.035), 0.018, 0.018, boxstyle="round,pad=0.002", fc=c, ec=c))
        ax.text(0.045 + i * 0.14, 0.044, lab, fontsize=9, va="center", color=INK2)
    ax.text(0.98, 0.044, "Relationships single-direction, dimension -> fact | Power BI model mirrors this schema",
            fontsize=8.5, color=MUTED, ha="right", va="center")
    fig.savefig(path, dpi=110)
    plt.close(fig)


def architecture(path):
    fig = plt.figure(figsize=(16, 4.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_axis_off()
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.text(0.02, 0.92, "Pipeline architecture", fontsize=17, weight="bold", color=INK)
    fig.text(0.02, 0.85, "One command (python run_pipeline.py) regenerates every dataset, table, KPI, chart and report",
             fontsize=9.5, color=INK2)
    steps = [
        ("Raw extracts", "generate_data.py\n10 CRM/ERP CSVs\nlocal currencies\n~1-2% injected defects", S[7]),
        ("ETL + validation", "etl.py / validation.py\n22 DQ rules + 3 fixes\nFX to USD, team-at-date\nrow reconciliation", S[1]),
        ("Star schema", "data/processed\n8 dims, 3 facts,\n1 bridge\nSQLite + CSV", S[0]),
        ("SQL layer", "01 schema, 02 DQ asserts\n03 KPI views\n04 17 business queries\n05 commission", S[6]),
        ("KPI engine", "kpi_engine.py\nrep / team / country KPIs\ncommission in pandas\nreconciled to SQL", S[2]),
        ("Analysis", "analysis.py\nLMDI bridges, anomalies\ndecline detection\ncommission what-ifs", S[4]),
        ("Outputs", "Power BI (5 pages)\nExcel management pack\nrep commission statements\ninsights + charts", S[5]),
    ]
    w, gap = 0.125, 0.0133
    for i, (title, body, col) in enumerate(steps):
        x = 0.02 + i * (w + gap)
        ax.add_patch(FancyBboxPatch((x, 0.14), w, 0.58, boxstyle="round,pad=0.005,rounding_size=0.015",
                                    fc=SURFACE, ec=col, lw=2))
        ax.add_patch(FancyBboxPatch((x, 0.62), w, 0.10, boxstyle="round,pad=0.005,rounding_size=0.015",
                                    fc=col, ec=col, lw=2))
        ax.text(x + w / 2, 0.67, title, ha="center", va="center", fontsize=10.5, weight="bold", color="white")
        ax.text(x + w / 2, 0.38, body, ha="center", va="center", fontsize=8.6, color=INK2, linespacing=1.6)
        if i < len(steps) - 1:
            ax.annotate("", xy=(x + w + gap + 0.002, 0.43), xytext=(x + w - 0.002, 0.43),
                        arrowprops=dict(arrowstyle="-|>", color=INK2, lw=1.4))
    ax.text(0.02, 0.05, "Quality gates stop the pipeline: row reconciliation (ETL), 11 integrity checks, 9 SQL assertions, "
            "Python-vs-SQL commission reconciliation to USD 0.01", fontsize=8.5, color=MUTED)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def load_inputs(cfg) -> tuple[dict, dict]:
    p, rs = cfg["paths"]["processed"], cfg["paths"]["reports"] / "sql_results"
    team = read_csv_checked(p / "kpi_team_month.csv")
    company = team.groupby(["month_key", "year"])[["actual", "allocated_target", "plan"]].sum().reset_index() \
        .rename(columns={"allocated_target": "target"})
    d = {"rep": read_csv_checked(p / "kpi_rep_month.csv"), "team": team, "company": company,
         "country": read_csv_checked(p / "kpi_country_month.csv")}
    for q in ["q03_team_status_by_year", "q07_commission_by_tier", "q08_sales_vs_commission_curve",
              "q09_product_line_contribution", "q10_underperformance_driver_profile",
              "q12_rep_leaderboard_latest_year", "q13_seasonality_vs_plan_phasing"]:
        d[q.split("_")[0]] = read_csv_checked(rs / f"{q}.csv")
    res = json.loads((cfg["paths"]["reports"] / "analysis_results.json").read_text())
    return d, res


def main() -> None:
    cfg = load_config()
    img = cfg["paths"]["images"]
    d, res = load_inputs(cfg)
    dashboard_overview(cfg, d, res, img / "dashboard_overview.png")
    corr = performance_analysis(cfg, d, res, img / "performance_analysis.png")
    trend_analysis(cfg, d, res, img / "trend_analysis.png")
    commission_incentives(cfg, d, res, img / "commission_incentives.png")
    data_model(img / "data_model.png")
    architecture(img / "architecture.png")
    res["deal_volume_vs_achievement_corr_2025"] = round(float(corr), 3)
    (cfg["paths"]["reports"] / "analysis_results.json").write_text(json.dumps(res, indent=2))
    log.info("rendered 6 images to %s", img)


if __name__ == "__main__":
    main()
