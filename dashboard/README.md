# Power BI Dashboard - Build Specification

This file specifies the Power BI report page by page, so it can be rebuilt exactly from `data/processed/`.
The images in `images/` are **previews rendered in Python from the same tables**. Once the `.pbix` is built,
replace them with real Power BI screenshots (section 6).

* Report file: `dashboard/sales_performance_commission.pbix` (build from this spec)
* Theme: `dashboard/theme.json` (View → Themes → Browse for themes)
* Measures: `dashboard/dax_measures.md`
* Canvas: 16:9, 1280 × 720, page background `#F9F9F7`

---

## 1. Data model

**Get data → Text/CSV** from `data/processed/`:

| Table | Role | Key |
|---|---|---|
| dim_date | Date table (mark as date table on `date`) | date_key |
| dim_country, dim_department, dim_team, dim_employee, dim_product, dim_customer, dim_commission_plan | Dimensions | *_key / code |
| fact_sales | Fact (line grain) | line_id |
| fact_targets | Fact (rep × month) | rep_key + month_key |
| fact_team_plan | Fact (team × month) | team_key + month_key |
| kpi_rep_month, kpi_team_month, kpi_country_month | KPI layer (from `kpi_engine.py`) | |
| reports/analysis/pbi_plan_bridge_long.csv, pbi_yoy_bridge_long.csv, pbi_commission_scenarios.csv | Helper tables for waterfalls / what-if | |

**Relationships** (all single direction, one-to-many from dimension to fact):

| From (1) | To (*) | Column |
|---|---|---|
| dim_date | fact_sales, fact_targets, fact_team_plan, kpi_rep_month, kpi_team_month, kpi_country_month | date_key |
| dim_employee | fact_sales, fact_targets, kpi_rep_month | employee_key → rep_key |
| dim_team | fact_sales, fact_targets, fact_team_plan, kpi_rep_month, kpi_team_month | team_key |
| dim_product | fact_sales | product_key |
| dim_customer | fact_sales | customer_key |
| dim_country | dim_team, kpi_country_month | country_code |
| dim_department | dim_team | department_code |

Country and department filter the facts **through `dim_team`** (a small snowflake). Do not also relate
`dim_country` to `fact_sales`: that creates an ambiguous filter path, and Power BI would deactivate one of the
relationships. `dim_commission_plan` stays **disconnected**; only the DAX check measure reads it.
Hide all key columns and raw numeric columns once measures exist, so report authors only see measures.

**Calculated columns:** see the end of `dax_measures.md`. Sort `dim_date[month_name]` by `month_num`.

---

## 2. Global layout (every page)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ Page title + dynamic subtitle ([Title Executive])        Nexora | synthetic   │  60px
├──────────────────────────────────────────────────────────────────────────────┤
│ [Overview] [Sales] [Team & Rep] [Commission] [Trend]   Year ▾ Month ▾ Country ▾ Dept ▾ Team ▾ │ 40px
├──────────────────────────────────────────────────────────────────────────────┤
│                               page body                                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

* **Navigation:** a Page navigator (Insert → Buttons → Navigator → Page navigator); hide the tooltip and drill-through pages.
* **Slicers** (dropdown style, synced across pages with View → Sync slicers): `dim_date[year]`, `dim_date[month_name]`,
  `dim_country[country_name]`, `dim_department[department_name]`, `dim_team[team_name]`.
* **Status colors:** only through `[Achievement Color]`, and always paired with an icon (conditional formatting → Icons) or `[Achievement Status]`.

---

## 3. Pages

### Page 1: Executive Overview (`images/dashboard_overview.png`)

| Zone | Visual | Fields |
|---|---|---|
| KPI row (6 cards) | New card visual | `[Net Revenue]` (reference label `[YoY Growth %]`), `[Target]`, `[Achievement %]` (color `[Achievement Color]`), `[Variance]`, `[Commission]` (reference `[Commission to Revenue %]`), `[Active Reps (Period End)]` |
| Left, middle | Line and clustered column | X `dim_date[year_month]`; columns `[Net Revenue]`; line `[Target]` (same axis) |
| Right, middle | Clustered bar | Y `dim_country[country_name]`; X `[Achievement %]` with legend `dim_date[year]`; constant line at 100% |
| Bottom left | Bar | Y `dim_product[product_line]`; X `[Net Revenue]`; tooltip `[Gross Margin %]`; color by `is_strategic` |
| Bottom right | Clustered column | X `kpi_rep_month[tier_name]`; Y `% of rep-months`, i.e. `[Rep-Months]` shown as percent of column total; legend `plan_id` |
| Text box | Smart narrative or card | `[Insight Gap Text]` |

**Drill-down:** country bar → department → team (hierarchy `dim_team[country_name] > department_name > team_name`).

### Page 2: Sales Performance

| Zone | Visual | Fields |
|---|---|---|
| Top | Matrix | Rows Country → Department → Team; values `[Target]`, `[Net Revenue]`, `[Achievement %]` (icons), `[Variance]`, `[YTD Achievement %]`, `[Capacity Plan]`, `[Capacity Gap]` |
| Middle left | Decomposition tree | Analyze `[Variance]`; explain by country, department, team, rep, product line |
| Middle right | Waterfall | `pbi_plan_bridge_long[step]` × `SUM(amount_usd)`, slicer on `country_code` and `year` |
| Bottom | Table | Product line: `[Net Revenue]`, `[YoY Growth %]`, `[Gross Margin %]`, `[Discount Rate %]`, `[Credit Note Rate %]`, `[Strategic Mix %]` |

### Page 3: Team & Salesperson Performance (`images/performance_analysis.png`)

| Zone | Visual | Fields |
|---|---|---|
| Left | Clustered bar | Team × `[Achievement %]` by year, sorted by FY2025 |
| Right | Table (leaderboard) | `[Salesperson Rank]`, rep, team, `[YTD Net Revenue]`, `[YTD Target]`, `[YTD Achievement %]` (data bars), sparkline of `[Achievement %]` by month |
| Bottom left | Scatter | X `[Deal Volume Index]`, Y `[Achievement %]`, details `dim_employee[full_name]`, legend department |
| Bottom right | Clustered column | Driver profile from `reports/sql_results/q10` (optional import): deal-volume index, deal-size index, discount, credit-note rate by segment |

**Drill-through page `Rep Profile`** (hidden; drill-through field `dim_employee[employee_code]`):
monthly `[Net Revenue]` vs `[Target]`, `[Rolling 3M Achievement %]` line, tier history (`kpi_rep_month[tier_name]` by month),
deals / discount / credit-note cards, anomaly table filtered to the rep, and a transactions table from `fact_sales`.

**Tooltip page `Rep Tooltip`** (320 × 240, Page information → Allow use as tooltip): `[Target]`, `[Net Revenue]`,
`[Achievement %]`, tier, `[Deals]`, `[Discount Rate %]`, `[Credit Note Rate %]`. Assign it to the scatter and the leaderboard.

### Page 4: Commission & Incentives (`images/commission_incentives.png`)

| Zone | Visual | Fields |
|---|---|---|
| KPI row | Cards | `[Commission]`, `[Commission to Revenue %]`, `[Reps Paid %]`, `[Accelerator Share of Commission %]`, `[Capped Rep-Months]`, `[Commission Check Difference]` (must show 0.00) |
| Middle left | Column | X `kpi_rep_month[achievement_band]`; Y `[Commission to Revenue %]` |
| Middle right | Scatter | X `kpi_rep_month[achievement]`, Y `kpi_rep_month[commission]` (don't summarize) |
| Bottom left | Clustered column | `pbi_commission_scenarios`: year × scenario × commission |
| Bottom right | What-if panel | Slicers `Gate Threshold`, `Accelerator Rate`; cards `[Commission (What-if)]`, `[What-if vs Actual Commission]` |

### Page 5: Trend & Variance Analysis (`images/trend_analysis.png`)

| Zone | Visual | Fields |
|---|---|---|
| Top left | Line | X month; `[Achievement %]`, `[Rolling 3M Achievement %]`; constant line 100% |
| Top right | Matrix heatmap | Rows `dim_date[month_name]`; columns `dim_country[country_name]`; values `[Achievement %]` with a diverging background (red `#E34948` → gray `#F0EFEC` at 100% → blue `#2A78D6`) |
| Bottom left | Waterfall | Plan-to-actual bridge (`pbi_plan_bridge_long`) with breakdown by country |
| Bottom right | Waterfall | YoY revenue bridge (`pbi_yoy_bridge_long`, group = "Company") |
| Drawer | Table | MoM / YoY table: month, `[Net Revenue]`, `[MoM Growth %]`, `[YoY Growth %]`, `[Net Revenue 3M Avg]` |

---

## 4. Interactions & UX

* Edit interactions: KPI cards are **not** filtered by the product bar on Page 1 (they reflect slicers only).
* Bookmarks: "Monthly vs YTD" toggle on Page 2 swaps `[Achievement %]` ↔ `[YTD Achievement %]`.
* Drill-through: from any rep name to `Rep Profile`; from any team to Page 3 filtered.
* Accessibility: alt text on every visual (summary of what it shows); tab order set; never rely on color alone.

## 5. Validation before publishing

| Check | Expected (full period, no filters) |
|---|---|
| `[Net Revenue]` FY2024 / FY2025 | $60.4M / $68.2M (matches `insights/kpi_summary.md`) |
| `[Achievement %]` FY2025 | 90.9% |
| `[Commission]` FY2025 | $576K |
| `[Commission Check Difference]` | 0.00 |

## 6. Screenshots for the README

Export each page at **1920 × 1080** (File → Export → PDF, then crop; or Windows Snipping Tool at 100% zoom) and save as:

| Page | File |
|---|---|
| 1 Executive Overview | `images/dashboard_overview.png` |
| 3 Team & Salesperson | `images/performance_analysis.png` |
| 4 Commission & Incentives | `images/commission_incentives.png` |
| 5 Trend & Variance | `images/trend_analysis.png` |
| Model view (Power BI) | `images/data_model.png` (optional; the rendered ERD can stay) |

Keep the file names, so the README picks up the new images automatically.
