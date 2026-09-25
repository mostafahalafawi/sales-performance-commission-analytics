-- =============================================================================
-- 04_business_analysis.sql  |  One query per business question
-- -----------------------------------------------------------------------------
-- Each block starts with "-- name:" and "-- question:". python/database.py
-- runs every block and saves the result to reports/sql_results/<name>.csv,
-- so each finding in insights/business_insights.md traces to a query.
-- Depends on views from 03_kpi_analysis.sql and 05_commission.sql.
-- =============================================================================


-- name: q01_actual_vs_target_by_year
-- question: What is actual sales vs target and plan, and what is achievement % by year?
SELECT year,
       ROUND(SUM(plan), 0)                                   AS capacity_plan_usd,
       ROUND(SUM(target), 0)                                 AS allocated_target_usd,
       ROUND(SUM(actual), 0)                                 AS actual_net_revenue_usd,
       ROUND(SUM(actual) - SUM(target), 0)                   AS variance_to_target_usd,
       ROUND(100.0 * SUM(actual) / SUM(target), 1)           AS achievement_pct,
       ROUND(100.0 * SUM(actual) / SUM(plan), 1)             AS plan_achievement_pct,
       ROUND(100.0 * SUM(gross_margin) / SUM(actual), 1)     AS gross_margin_pct
  FROM vw_company_month
 GROUP BY year
 ORDER BY year;


-- name: q02_country_performance_gap
-- question: Which countries have the largest performance gaps, and is the gap capacity (headcount) or execution?
-- Plan -> (capacity gap: vacancies & ramping hires) -> allocated target -> (execution gap) -> actual
WITH c AS (
    SELECT country_code, year,
           SUM(plan) AS plan, SUM(allocated_target) AS target, SUM(actual) AS actual
      FROM vw_country_month_performance
     GROUP BY country_code, year
)
SELECT dc.country_name, c.year,
       ROUND(c.plan, 0)                                  AS plan_usd,
       ROUND(c.actual, 0)                                AS actual_usd,
       ROUND(c.actual - c.plan, 0)                       AS total_gap_to_plan_usd,
       ROUND(c.target - c.plan, 0)                       AS capacity_gap_usd,      -- negative = unallocated quota
       ROUND(c.actual - c.target, 0)                     AS execution_gap_usd,
       ROUND(100.0 * c.actual / c.target, 1)             AS achievement_pct,
       ROUND(100.0 * c.actual / c.plan, 1)               AS plan_achievement_pct,
       RANK() OVER (PARTITION BY c.year ORDER BY c.actual / c.target DESC) AS country_rank
  FROM c
  JOIN dim_country dc ON dc.country_code = c.country_code
 ORDER BY c.year, country_rank;


-- name: q03_team_status_by_year
-- question: Which teams are above or below target, and how does their YTD position rank?
WITH t AS (
    SELECT team_key, year, SUM(allocated_target) AS target, SUM(actual) AS actual,
           SUM(plan) AS plan, AVG(active_reps) AS avg_reps, AVG(planned_headcount) AS planned_hc
      FROM vw_team_month_performance
     GROUP BY team_key, year
)
SELECT dt.team_name, dt.department_name, t.year,
       ROUND(t.target, 0) AS target_usd, ROUND(t.actual, 0) AS actual_usd,
       ROUND(100.0 * t.actual / t.target, 1)                          AS achievement_pct,
       ROUND(t.actual - t.target, 0)                                  AS variance_usd,
       ROUND(t.avg_reps, 1) AS avg_active_reps, t.planned_hc          AS planned_headcount,
       CASE WHEN t.actual >= t.target        THEN 'Above target'
            WHEN t.actual >= 0.9 * t.target  THEN 'Slightly below (90-99%)'
            ELSE 'Materially below (<90%)' END                        AS status,
       DENSE_RANK() OVER (PARTITION BY t.year ORDER BY t.actual / t.target DESC) AS team_rank
  FROM t
  JOIN dim_team dt ON dt.team_key = t.team_key
 ORDER BY t.year, team_rank;


-- name: q04_consistent_outperformers
-- question: Which salespeople consistently outperform (>=100% in at least 9 of the last 12 months)?
WITH last12 AS (
    SELECT *
      FROM vw_rep_month_performance
     WHERE month_key > (SELECT MAX(month_key) - 100 FROM fact_targets)   -- Jan..Dec of latest year
       AND target IS NOT NULL
       AND is_ramp_month = 0
)
SELECT employee_code, full_name, dt.team_name,
       COUNT(*)                                                 AS months_measured,
       SUM(CASE WHEN achievement >= 1 THEN 1 ELSE 0 END)        AS months_on_target,
       ROUND(100.0 * SUM(actual) / SUM(target), 1)              AS achievement_pct,
       ROUND(SUM(actual), 0)                                    AS revenue_usd
  FROM last12 l
  JOIN dim_team dt ON dt.team_key = l.team_key
 GROUP BY employee_code, full_name, dt.team_name
HAVING COUNT(*) >= 9 AND SUM(CASE WHEN achievement >= 1 THEN 1 ELSE 0 END) >= 9
 ORDER BY months_on_target DESC, achievement_pct DESC;


-- name: q05_declining_reps
-- question: Which active salespeople show declining performance (last 3 months vs the 3 before)?
WITH r AS (
    SELECT rep_key, employee_code, full_name, team_key, month_key, actual, target,
           ROW_NUMBER() OVER (PARTITION BY rep_key ORDER BY month_key DESC) AS rn
      FROM vw_rep_month_performance
     WHERE target IS NOT NULL AND is_ramp_month = 0
),
w AS (
    SELECT rep_key, employee_code, full_name, team_key,
           MAX(CASE WHEN rn = 1 THEN month_key END)                                   AS latest_month,
           SUM(CASE WHEN rn BETWEEN 1 AND 3 THEN actual END)
             / SUM(CASE WHEN rn BETWEEN 1 AND 3 THEN target END)                      AS ach_last_3m,
           SUM(CASE WHEN rn BETWEEN 4 AND 6 THEN actual END)
             / SUM(CASE WHEN rn BETWEEN 4 AND 6 THEN target END)                      AS ach_prior_3m,
           SUM(CASE WHEN rn BETWEEN 7 AND 12 THEN actual END)
             / SUM(CASE WHEN rn BETWEEN 7 AND 12 THEN target END)                     AS ach_months_7_12
      FROM r
     GROUP BY rep_key, employee_code, full_name, team_key
    HAVING COUNT(*) >= 6
)
SELECT employee_code, full_name, dt.team_name, latest_month,
       ROUND(100 * ach_months_7_12, 1) AS ach_months_7_12_pct,
       ROUND(100 * ach_prior_3m, 1)    AS ach_prior_3m_pct,
       ROUND(100 * ach_last_3m, 1)     AS ach_last_3m_pct,
       ROUND(100 * (ach_last_3m - ach_prior_3m), 1) AS change_pp
  FROM w
  JOIN dim_team dt ON dt.team_key = w.team_key
 WHERE latest_month = (SELECT MAX(month_key) FROM fact_targets)       -- still employed
   AND ach_last_3m < ach_prior_3m
   AND ach_prior_3m < COALESCE(ach_months_7_12, ach_prior_3m + 1)     -- two consecutive steps down
   AND ach_last_3m < 0.90
 ORDER BY change_pp;


-- name: q06_monthly_trend
-- question: What is the monthly performance trend (MoM, YoY, 3-month moving average, YTD)?
SELECT month_key,
       ROUND(target, 0)                    AS target_usd,
       ROUND(actual, 0)                    AS actual_usd,
       ROUND(100 * achievement, 1)         AS achievement_pct,
       ROUND(variance, 0)                  AS variance_usd,
       ROUND(100 * mom_growth, 1)          AS mom_growth_pct,
       ROUND(100 * yoy_growth, 1)          AS yoy_growth_pct,
       ROUND(actual_3m_avg, 0)             AS actual_3m_moving_avg_usd,
       ROUND(ytd_actual, 0)                AS ytd_actual_usd,
       ROUND(100.0 * ytd_actual / ytd_target, 1) AS ytd_achievement_pct
  FROM vw_company_month
 ORDER BY month_key;


-- name: q07_commission_by_tier
-- question: How much commission is generated and how does it change by achievement tier?
SELECT plan_id, tier_name,
       COUNT(*)                                                     AS rep_months,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY plan_id), 1) AS pct_of_rep_months,
       ROUND(SUM(actual), 0)                                        AS net_revenue_usd,
       ROUND(SUM(commission), 0)                                    AS commission_usd,
       ROUND(100.0 * SUM(commission) / SUM(SUM(commission)) OVER (PARTITION BY plan_id), 1) AS pct_of_commission,
       ROUND(100.0 * SUM(commission) / NULLIF(SUM(actual), 0), 2)   AS commission_to_revenue_pct,
       SUM(is_capped)                                               AS capped_rep_months
  FROM vw_commission_rep_month
 GROUP BY plan_id, tier_name
 ORDER BY plan_id, tier_name;


-- name: q08_sales_vs_commission_curve
-- question: What is the relationship between sales achievement and commission earned?
WITH b AS (
    SELECT CASE WHEN achievement < 0.6 THEN '<60%'
                WHEN achievement < 0.8 THEN '60-79%'
                WHEN achievement < 1.0 THEN '80-99%'
                WHEN achievement < 1.2 THEN '100-119%'
                WHEN achievement < 1.5 THEN '120-149%'
                ELSE '150%+' END AS achievement_band,
           CASE WHEN achievement < 0.6 THEN 1 WHEN achievement < 0.8 THEN 2 WHEN achievement < 1.0 THEN 3
                WHEN achievement < 1.2 THEN 4 WHEN achievement < 1.5 THEN 5 ELSE 6 END AS band_order,
           actual, commission, target
      FROM vw_commission_rep_month
     WHERE target IS NOT NULL
)
SELECT achievement_band,
       COUNT(*)                                            AS rep_months,
       ROUND(AVG(actual), 0)                               AS avg_revenue_usd,
       ROUND(AVG(commission), 0)                           AS avg_commission_usd,
       ROUND(100.0 * SUM(commission) / SUM(actual), 2)     AS commission_to_revenue_pct,
       ROUND(SUM(commission), 0)                           AS total_commission_usd
  FROM b
 GROUP BY achievement_band, band_order
 ORDER BY band_order;


-- name: q09_product_line_contribution
-- question: Which product lines contribute most to revenue and margin, and how are they trending?
WITH p AS (
    SELECT dp.product_line, fs.month_key / 100 AS year,
           SUM(fs.net_amount_usd)                                                    AS revenue,
           SUM(fs.gross_margin_usd)                                                  AS gm,
           SUM(CASE WHEN fs.doc_type = 'INV' THEN fs.discount_amount_usd ELSE 0 END) AS disc,
           SUM(CASE WHEN fs.doc_type = 'INV' THEN fs.gross_amount_usd ELSE 0 END)    AS gross,
           -SUM(CASE WHEN fs.doc_type = 'CN' THEN fs.net_amount_usd ELSE 0 END)      AS credited,
           MAX(dp.is_strategic)                                                      AS is_strategic
      FROM fact_sales fs
      JOIN dim_product dp ON dp.product_key = fs.product_key
     GROUP BY dp.product_line, year
)
SELECT product_line, is_strategic,
       ROUND(SUM(CASE WHEN year = 2024 THEN revenue END), 0)                     AS revenue_2024_usd,
       ROUND(SUM(CASE WHEN year = 2025 THEN revenue END), 0)                     AS revenue_2025_usd,
       ROUND(100.0 * (SUM(CASE WHEN year = 2025 THEN revenue END)
                     / SUM(CASE WHEN year = 2024 THEN revenue END) - 1), 1)      AS yoy_growth_pct,
       ROUND(100.0 * SUM(revenue) / SUM(SUM(revenue)) OVER (), 1)                AS revenue_share_pct,
       ROUND(100.0 * SUM(gm) / SUM(revenue), 1)                                  AS gross_margin_pct,
       ROUND(100.0 * SUM(gm) / SUM(SUM(gm)) OVER (), 1)                          AS margin_share_pct,
       ROUND(100.0 * SUM(disc) / SUM(gross), 1)                                  AS discount_rate_pct,
       ROUND(100.0 * SUM(credited) / SUM(revenue + credited), 2)                 AS credit_note_rate_pct
  FROM p
 GROUP BY product_line, is_strategic
 ORDER BY SUM(revenue) DESC;


-- name: q10_underperformance_driver_profile
-- question: What distinguishes under-performers (<80%) from performers (>=100%)? (root-cause drivers)
WITH rep AS (           -- one row per rep over the full period, fully-ramped months only
    SELECT rep_key, department_code,
           SUM(actual) / SUM(target)                                       AS achievement,
           AVG(deals)                                                      AS deals_per_month,
           SUM(invoiced_revenue) / NULLIF(SUM(deals), 0)                   AS avg_deal_size,
           SUM(discount_value) / NULLIF(SUM(gross_list_value), 0)          AS discount_rate,
           -SUM(credit_notes) / NULLIF(SUM(invoiced_revenue), 0)           AS credit_note_rate,
           SUM(strategic_revenue) / NULLIF(SUM(invoiced_revenue), 0)       AS strategic_mix,
           SUM(month_end_revenue) / NULLIF(SUM(invoiced_revenue), 0)       AS month_end_share,
           COUNT(*)                                                        AS months
      FROM vw_rep_month_base
     WHERE target IS NOT NULL AND is_ramp_month = 0
     GROUP BY rep_key, department_code
    HAVING COUNT(*) >= 6
),
dept_avg AS (           -- normalise drivers within department (ENT deals are not SME deals)
    SELECT department_code, AVG(deals_per_month) AS d_deals, AVG(avg_deal_size) AS d_size
      FROM rep GROUP BY department_code
),
seg AS (
    SELECT r.*,
           r.deals_per_month / d.d_deals AS deals_index,
           r.avg_deal_size   / d.d_size  AS deal_size_index,
           CASE WHEN r.achievement <  0.80 THEN '1 Under-performer (<80%)'
                WHEN r.achievement <  1.00 THEN '2 Near target (80-99%)'
                ELSE '3 Performer (>=100%)' END AS segment
      FROM rep r JOIN dept_avg d ON d.department_code = r.department_code
)
SELECT segment,
       COUNT(*)                                   AS reps,
       ROUND(100 * AVG(achievement), 1)           AS avg_achievement_pct,
       ROUND(100 * AVG(deals_index), 1)           AS deal_volume_index,     -- 100 = department average
       ROUND(100 * AVG(deal_size_index), 1)       AS deal_size_index,
       ROUND(100 * AVG(discount_rate), 2)         AS discount_rate_pct,
       ROUND(100 * AVG(credit_note_rate), 2)      AS credit_note_rate_pct,
       ROUND(100 * AVG(strategic_mix), 1)         AS strategic_mix_pct,
       ROUND(100 * AVG(month_end_share), 1)       AS month_end_share_pct
  FROM seg
 GROUP BY segment
 ORDER BY segment;


-- name: q11_month_end_loading
-- question: Which rep-months show month-end deal loading followed by credit notes (quality-of-revenue risk)?
WITH me AS (
    SELECT fs.rep_key, fs.month_key,
           SUM(CASE WHEN fs.doc_type = 'INV' AND fs.is_month_end_window = 1 THEN fs.net_amount_usd ELSE 0 END) AS me_rev,
           SUM(CASE WHEN fs.doc_type = 'INV' THEN fs.net_amount_usd ELSE 0 END)                                AS inv_rev
      FROM fact_sales fs
     GROUP BY fs.rep_key, fs.month_key
),
reversed AS (          -- credit notes raised later against invoices booked in the month-end window
    SELECT i.rep_key, i.month_key, -SUM(cn.net_amount_usd) AS reversed_usd
      FROM fact_sales cn
      JOIN (SELECT DISTINCT document_no, rep_key, month_key
              FROM fact_sales WHERE doc_type = 'INV' AND is_month_end_window = 1) i
        ON i.document_no = cn.original_document_no
     WHERE cn.doc_type = 'CN' AND cn.days_since_original <= 60
     GROUP BY i.rep_key, i.month_key
)
SELECT e.employee_code, e.full_name, me.month_key,
       ROUND(me.inv_rev, 0)                              AS invoiced_usd,
       ROUND(100.0 * me.me_rev / me.inv_rev, 1)          AS month_end_share_pct,
       ROUND(r.reversed_usd, 0)                          AS reversed_within_60d_usd,
       ROUND(100.0 * r.reversed_usd / me.me_rev, 1)      AS pct_of_month_end_reversed,
       c.tier_name                                       AS tier_paid
  FROM me
  JOIN reversed r ON r.rep_key = me.rep_key AND r.month_key = me.month_key
  JOIN dim_employee e ON e.employee_key = me.rep_key
  LEFT JOIN vw_commission_rep_month c ON c.rep_key = me.rep_key AND c.month_key = me.month_key
 WHERE me.me_rev / me.inv_rev >= 0.50
   AND r.reversed_usd / me.me_rev >= 0.25
 ORDER BY r.reversed_usd DESC;


-- name: q12_rep_leaderboard_latest_year
-- question: Who are the top and bottom salespeople by YTD achievement in the latest year (rank within country)?
WITH y AS (
    SELECT rep_key, employee_code, full_name, country_code, team_key,
           SUM(actual) AS actual, SUM(target) AS target, SUM(is_ramp_month) AS ramp_months, COUNT(*) AS months
      FROM vw_rep_month_performance
     WHERE year = (SELECT MAX(month_key) / 100 FROM fact_targets) AND target IS NOT NULL
     GROUP BY rep_key, employee_code, full_name, country_code, team_key
    HAVING COUNT(*) >= 6
),
ranked AS (
    SELECT y.*, actual / target AS ytd_ach,
           RANK() OVER (ORDER BY actual / target DESC)                           AS company_rank,
           RANK() OVER (PARTITION BY country_code ORDER BY actual / target DESC) AS country_rank,
           COUNT(*) OVER ()                                                      AS n_reps
      FROM y
)
SELECT company_rank, country_rank, employee_code, full_name, dc.country_name, dt.team_name, months,
       ROUND(actual, 0) AS ytd_actual_usd, ROUND(target, 0) AS ytd_target_usd,
       ROUND(100 * ytd_ach, 1) AS ytd_achievement_pct,
       CASE WHEN company_rank <= 10 THEN 'Top 10' ELSE 'Bottom 10' END AS list
  FROM ranked
  JOIN dim_country dc ON dc.country_code = ranked.country_code
  JOIN dim_team dt ON dt.team_key = ranked.team_key
 WHERE company_rank <= 10 OR company_rank > n_reps - 10
 ORDER BY company_rank;


-- name: q13_seasonality_vs_plan_phasing
-- question: Does the target phasing match real seasonality (Ramadan, Gulf summer, year-end)?
SELECT dd.month_name, cm.month_key % 100 AS month_num,
       ROUND(100.0 * SUM(CASE WHEN c.region = 'GCC' AND cm.year = 2024 THEN cm.actual END)
                  / SUM(CASE WHEN c.region = 'GCC' AND cm.year = 2024 THEN cm.allocated_target END), 1) AS gcc_2024_ach_pct,
       ROUND(100.0 * SUM(CASE WHEN c.region = 'GCC' AND cm.year = 2025 THEN cm.actual END)
                  / SUM(CASE WHEN c.region = 'GCC' AND cm.year = 2025 THEN cm.allocated_target END), 1) AS gcc_2025_ach_pct,
       ROUND(100.0 * SUM(CASE WHEN c.region <> 'GCC' AND cm.year = 2024 THEN cm.actual END)
                  / SUM(CASE WHEN c.region <> 'GCC' AND cm.year = 2024 THEN cm.allocated_target END), 1) AS egypt_2024_ach_pct,
       ROUND(100.0 * SUM(CASE WHEN c.region <> 'GCC' AND cm.year = 2025 THEN cm.actual END)
                  / SUM(CASE WHEN c.region <> 'GCC' AND cm.year = 2025 THEN cm.allocated_target END), 1) AS egypt_2025_ach_pct
  FROM vw_country_month_performance cm
  JOIN dim_country c ON c.country_code = cm.country_code
  JOIN (SELECT DISTINCT month_num, month_name FROM dim_date) dd ON dd.month_num = cm.month_key % 100
 GROUP BY dd.month_name, cm.month_key % 100
 ORDER BY month_num;


-- name: q14_plan_change_impact
-- question: What did raising the commission gate from 80% to 85% in 2025 change?
-- Counterfactual: re-price 2025 rep-months at the 2024 gate using the same tier table.
WITH c25 AS (
    SELECT * FROM vw_commission_rep_month WHERE plan_id = 'CP2025' AND target IS NOT NULL
)
SELECT COUNT(*)                                                                        AS rep_months_2025,
       SUM(CASE WHEN achievement >= 0.80 AND achievement < 0.85 THEN 1 ELSE 0 END)    AS rep_months_between_80_85,
       ROUND(100.0 * SUM(CASE WHEN achievement >= 0.80 AND achievement < 0.85 THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                                                       AS pct_of_rep_months,
       ROUND(SUM(CASE WHEN achievement >= 0.80 AND achievement < 0.85
                      THEN commissionable_base * 0.005 ELSE 0 END), 0)                 AS commission_saved_usd,
       ROUND(SUM(commission), 0)                                                       AS commission_paid_2025_usd,
       ROUND(100.0 * SUM(CASE WHEN achievement >= 0.80 AND achievement < 0.85
                      THEN commissionable_base * 0.005 ELSE 0 END) / SUM(commission), 1) AS saving_pct_of_paid
  FROM c25;


-- name: q15_new_hire_ramp
-- question: How fast do new hires ramp, and are ramp targets realistic?
SELECT CASE WHEN tenure_months >= 6 THEN '6+' ELSE CAST(tenure_months AS TEXT) END AS tenure_month,
       COUNT(*)                                          AS rep_months,
       ROUND(AVG(target), 0)                             AS avg_target_usd,
       ROUND(AVG(actual), 0)                             AS avg_actual_usd,
       ROUND(100.0 * SUM(actual) / SUM(target), 1)       AS achievement_pct
  FROM vw_rep_month_performance
 WHERE target IS NOT NULL
   AND rep_key IN (SELECT employee_key FROM dim_employee WHERE is_new_hire_in_period = 1)
 GROUP BY CASE WHEN tenure_months >= 6 THEN '6+' ELSE CAST(tenure_months AS TEXT) END
 ORDER BY MIN(tenure_months);


-- name: q16_attrition_signal
-- question: Do leavers show a measurable performance drop before they exit (early-warning signal)?
WITH leavers AS (
    SELECT employee_key, CAST(strftime('%Y%m', exit_date) AS INTEGER) AS exit_month
      FROM dim_employee WHERE role = 'Sales Representative' AND exit_date IS NOT NULL
),
x AS (
    SELECT r.rep_key, r.actual, r.target,
           (l.exit_month / 100) * 12 + (l.exit_month % 100)
           - ((r.month_key / 100) * 12 + (r.month_key % 100)) AS months_before_exit
      FROM vw_rep_month_performance r
      JOIN leavers l ON l.employee_key = r.rep_key
     WHERE r.target IS NOT NULL AND r.is_ramp_month = 0
)
SELECT CASE WHEN months_before_exit <= 2 THEN '0-2 months before exit'
            WHEN months_before_exit <= 5 THEN '3-5 months before exit'
            ELSE '6+ months before exit' END            AS window,
       COUNT(DISTINCT rep_key)                          AS reps,
       COUNT(*)                                         AS rep_months,
       ROUND(100.0 * SUM(actual) / SUM(target), 1)      AS achievement_pct
  FROM x
 GROUP BY window
 ORDER BY window DESC;


-- name: q17_monthly_vs_quarterly_volatility
-- question: How volatile is monthly achievement, and would quarterly measurement change tier outcomes?
WITH m AS (
    SELECT rep_key, month_key, actual, target, achievement,
           (month_key / 100) * 10 + ((month_key % 100) + 2) / 3 AS quarter_key
      FROM vw_rep_month_performance
     WHERE target IS NOT NULL AND is_ramp_month = 0
),
q AS (
    SELECT rep_key, quarter_key,
           COUNT(*)                                                          AS months,
           SUM(actual) / SUM(target)                                         AS q_ach,
           MAX(CASE WHEN achievement >= 1.2 THEN 1 ELSE 0 END)               AS any_accelerator_month,
           MAX(CASE WHEN achievement < 0.8 THEN 1 ELSE 0 END)                AS any_below_gate_month
      FROM m GROUP BY rep_key, quarter_key HAVING COUNT(*) = 3
)
SELECT COUNT(*)                                                                         AS rep_quarters,
       ROUND(100.0 * AVG(any_accelerator_month * any_below_gate_month), 1)              AS pct_quarters_with_both_T0_and_T3_months,
       ROUND(100.0 * AVG(CASE WHEN q_ach BETWEEN 0.9 AND 1.1 THEN 1 ELSE 0 END), 1)     AS pct_quarters_within_10pp_of_target,
       ROUND(100.0 * AVG(CASE WHEN any_accelerator_month = 1 AND q_ach < 1.0 THEN 1 ELSE 0 END), 1)
                                                                                        AS pct_quarters_paid_accelerator_but_missed_quarter
  FROM q;
