# KPI Definitions

Every KPI is calculated once in `python/kpi_engine.py` and once in SQL (`sql/03_kpi_analysis.sql`,
`sql/05_commission.sql`). The pipeline reconciles the two. Power BI uses the same definitions as DAX measures
(`dashboard/dax_measures.md`).

| KPI | Formula | Grain | Business meaning |
|---|---|---|---|
| **Actual sales (net revenue)** | Σ net_amount_usd (invoices − credit notes posted in period) | any | Revenue the rep or team is measured on |
| **Gross list value** | Σ quantity × list price (invoices) | any | Value before discounts, i.e. the size of what was sold |
| **Target** | Σ target_amount_usd (latest revision) | rep → team → country | What each rep was asked to deliver |
| **Capacity plan** | planned headcount × full quota | team | What Finance budgeted if every seat were filled and ramped |
| **Achievement %** | Actual ÷ Target | any | Primary performance KPI; drives the commission tier |
| **Plan achievement %** | Actual ÷ Capacity plan | team+ | Performance against the budget, including vacancies |
| **Variance** | Actual − Target | any | Gap in USD |
| **Capacity gap** | Capacity plan − Target | team+ | Quota lost to vacancies and ramping hires before anyone sells |
| **MoM growth** | (Actual_m − Actual_m−1) ÷ Actual_m−1 | any | Short-term momentum |
| **YoY growth** | (Actual_m − Actual_m−12) ÷ Actual_m−12 | company / country | Growth net of seasonality |
| **YTD sales / target** | Running Σ from January of the same year | any | Cumulative position; resets each year |
| **YTD achievement %** | YTD sales ÷ YTD target | any | The number reviewed in quarterly business reviews |
| **Rolling 3M achievement** | Σ actual (3 months) ÷ Σ target (3 months) | rep | Smooths monthly noise; used for decline detection |
| **Deals** | Distinct invoice documents | rep+ | Volume driver |
| **Average deal size** | Invoiced revenue ÷ deals | rep+ | Size driver |
| **Discount rate** | Discount ÷ gross list value | any | Price realisation |
| **Credit-note rate** | −Credit notes ÷ invoiced revenue | any | Revenue quality / reversals |
| **Month-end share** | Invoiced revenue in last 3 days ÷ invoiced revenue | rep-month | Deal timing; high values plus reversals signal loading |
| **Strategic mix** | Strategic-line revenue ÷ invoiced revenue | any | Share of Cybersecurity and Managed Services |
| **Gross margin %** | (Net − cost) ÷ Net | any | Profitability of what is sold |
| **Commission** | See `commission_policy.md` | rep-month | Incentive payout |
| **Commission rate (tier)** | Rate from the tier table | rep-month | Marginal incentive for the month |
| **Effective commission rate** | Commission ÷ net revenue | any | Real cost of incentives per $ of revenue |
| **Commission-to-revenue** | Σ commission ÷ Σ net revenue | team+ | Incentive efficiency by team or country |
| **Incentive tier** | T0 Below Gate / T1 Approaching / T2 On Target / T3 Accelerator | rep-month | Tier band from achievement |
| **Salesperson rank** | RANK() of achievement within team and within company, per month; YTD rank in `q12` | rep | Leaderboards |
| **Team rank** | DENSE_RANK() of team achievement per month | team | |
| **Country rank** | DENSE_RANK() of country achievement per month | country | |

## Performance bands (rep-month)

| Band | Rule |
|---|---|
| Exceeding | achievement ≥ 120% |
| On Target | 100% ≤ achievement < 120% |
| Near Target | 80% ≤ achievement < 100% |
| Below Target | achievement < 80% |
| No Target | target missing (DQ-T04) and excluded |

## Analytical classifications

| Classification | Rule (configurable in `config.yaml → analysis`) |
|---|---|
| Consistent outperformer | ≥ 100% in at least 9 of the 12 months of the latest year (fully ramped months only) |
| Declining (trend) | Slope of rolling-3M achievement over the last 6 months ≤ −5 pp per month **and** latest rolling-3M < 90%; still employed |
| Declining (step, SQL q05) | Last 3 months < prior 3 months < months 7-12, and last 3 months < 90% |
| Anomaly | Robust z-score of monthly achievement vs the rep's own median / MAD, with abs(z) ≥ 3.5 |
| Month-end loading | ≥ 50% of the month's invoicing in the last 3 days **and** ≥ 25% of it reversed within 60 days |
| Under-performer / performer | Full-period achievement < 80% / ≥ 100% (fully ramped months, ≥ 6 months) |

## Variance decompositions

**Plan-to-actual bridge** (`analysis.plan_to_actual_bridge`):
`Plan + vacancy effect + ramp & other effect + execution effect = Actual`, where
vacancy effect = −vacant seats × quota per seat, execution effect = Actual − Target.

**Year-on-year revenue bridge** (LMDI, `analysis.yoy_revenue_bridge`):
`Revenue = rep-months × deals/rep-month × gross value/deal × (1 − discount rate) × (1 − credit-note rate)`.
Each factor's effect = L(R₁, R₀) × ln(x₁ / x₀), with L(a, b) = (a − b) / (ln a − ln b).
The effects add up exactly to the revenue change, with no unexplained residual.
