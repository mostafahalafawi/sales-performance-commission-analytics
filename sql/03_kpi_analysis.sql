-- =============================================================================
-- 03_kpi_analysis.sql  |  Core KPI views (the SQL analytical layer)
-- -----------------------------------------------------------------------------
-- Conventions
--   actual  = net revenue in USD = invoices - credit notes posted in the month
--   target  = rep monthly target (latest revision)
--   achievement = actual / target          variance = actual - target
--   YTD resets every calendar year; ranks are recalculated within each month.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- vw_rep_month_base: one row per ACTIVE rep-month (from the bridge), with sales
-- drivers pre-aggregated. Reps with no sales in a month still appear (actual = 0).
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_rep_month_base;
CREATE VIEW vw_rep_month_base AS
WITH sales AS (
    SELECT rep_key,
           month_key,
           SUM(net_amount_usd)                                                   AS net_revenue,
           SUM(CASE WHEN doc_type = 'INV' THEN net_amount_usd ELSE 0 END)       AS invoiced_revenue,
           SUM(CASE WHEN doc_type = 'CN'  THEN net_amount_usd ELSE 0 END)       AS credit_notes,
           SUM(CASE WHEN doc_type = 'INV' THEN gross_amount_usd ELSE 0 END)     AS gross_list_value,
           SUM(CASE WHEN doc_type = 'INV' THEN discount_amount_usd ELSE 0 END)  AS discount_value,
           SUM(gross_margin_usd)                                                 AS gross_margin,
           COUNT(DISTINCT CASE WHEN doc_type = 'INV' THEN document_no END)       AS deals,
           SUM(CASE WHEN doc_type = 'INV' AND is_month_end_window = 1
                    THEN net_amount_usd ELSE 0 END)                              AS month_end_revenue,
           SUM(CASE WHEN doc_type = 'INV' AND commission_weight > 1
                    THEN net_amount_usd ELSE 0 END)                              AS strategic_revenue
      FROM fact_sales
     GROUP BY rep_key, month_key
)
SELECT b.rep_key,
       b.month_key,
       b.team_key,
       t.country_code,
       t.department_code,
       b.tenure_months,
       b.is_ramp_month,
       ft.target_amount_usd                          AS target,
       COALESCE(s.net_revenue, 0)                    AS actual,
       COALESCE(s.invoiced_revenue, 0)               AS invoiced_revenue,
       COALESCE(s.credit_notes, 0)                   AS credit_notes,
       COALESCE(s.gross_list_value, 0)               AS gross_list_value,
       COALESCE(s.discount_value, 0)                 AS discount_value,
       COALESCE(s.gross_margin, 0)                   AS gross_margin,
       COALESCE(s.deals, 0)                          AS deals,
       COALESCE(s.month_end_revenue, 0)              AS month_end_revenue,
       COALESCE(s.strategic_revenue, 0)              AS strategic_revenue
  FROM bridge_rep_team_month b
  JOIN dim_team t          ON t.team_key = b.team_key
  LEFT JOIN fact_targets ft ON ft.rep_key = b.rep_key AND ft.month_key = b.month_key
  LEFT JOIN sales s         ON s.rep_key  = b.rep_key AND s.month_key  = b.month_key;


-- -----------------------------------------------------------------------------
-- vw_rep_month_performance: achievement, variance, MoM, YTD, rolling 3M, ranks
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_rep_month_performance;
CREATE VIEW vw_rep_month_performance AS
WITH base AS (
    SELECT r.*,
           r.month_key / 100                                   AS year,
           r.actual / NULLIF(r.target, 0)                      AS achievement,
           r.actual - r.target                                 AS variance,
           r.discount_value / NULLIF(r.gross_list_value, 0)   AS discount_rate,
           r.invoiced_revenue / NULLIF(r.deals, 0)             AS avg_deal_size
      FROM vw_rep_month_base r
),
windowed AS (
    SELECT b.*,
           LAG(actual) OVER (PARTITION BY rep_key ORDER BY month_key)                AS prev_month_actual,
           SUM(actual) OVER (PARTITION BY rep_key, year ORDER BY month_key
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)       AS ytd_actual,
           SUM(target) OVER (PARTITION BY rep_key, year ORDER BY month_key
                             ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)       AS ytd_target,
           SUM(actual) OVER (PARTITION BY rep_key ORDER BY month_key
                             ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)               AS r3_actual,
           SUM(target) OVER (PARTITION BY rep_key ORDER BY month_key
                             ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)               AS r3_target
      FROM base b
)
SELECT w.rep_key,
       e.employee_code,
       e.full_name,
       w.month_key,
       w.year,
       w.team_key,
       w.country_code,
       w.department_code,
       w.tenure_months,
       w.is_ramp_month,
       w.target,
       w.actual,
       w.variance,
       w.achievement,
       (w.actual - w.prev_month_actual) / NULLIF(w.prev_month_actual, 0)            AS mom_growth,
       w.ytd_actual,
       w.ytd_target,
       w.ytd_actual / NULLIF(w.ytd_target, 0)                                        AS ytd_achievement,
       w.r3_actual / NULLIF(w.r3_target, 0)                                          AS rolling_3m_achievement,
       w.deals,
       w.avg_deal_size,
       w.discount_rate,
       w.credit_notes,
       w.gross_margin,
       w.month_end_revenue / NULLIF(w.invoiced_revenue, 0)                           AS month_end_share,
       w.strategic_revenue / NULLIF(w.invoiced_revenue, 0)                           AS strategic_mix,
       CASE WHEN w.target IS NULL           THEN 'No Target'
            WHEN w.achievement >= 1.20      THEN 'Exceeding (>=120%)'
            WHEN w.achievement >= 1.00      THEN 'On Target (100-119%)'
            WHEN w.achievement >= 0.80      THEN 'Near Target (80-99%)'
            ELSE 'Below Target (<80%)' END                                           AS performance_band,
       -- rank within team and company for the month (1 = best achievement)
       CASE WHEN w.target IS NOT NULL THEN
            RANK() OVER (PARTITION BY w.team_key, w.month_key
                         ORDER BY CASE WHEN w.target IS NULL THEN 1 ELSE 0 END, w.achievement DESC) END AS rank_in_team,
       CASE WHEN w.target IS NOT NULL THEN
            RANK() OVER (PARTITION BY w.month_key
                         ORDER BY CASE WHEN w.target IS NULL THEN 1 ELSE 0 END, w.achievement DESC) END AS rank_in_company
  FROM windowed w
  JOIN dim_employee e ON e.employee_key = w.rep_key;


-- -----------------------------------------------------------------------------
-- vw_team_month_performance
--   revenue  : attributed to the team at time of sale (fact_sales.team_key)
--   target   : sum of rep targets allocated to the team
--   plan     : capacity plan = planned headcount x full quota
--   capacity gap = plan - allocated target (vacancies + ramping new hires)
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_team_month_performance;
CREATE VIEW vw_team_month_performance AS
WITH rev AS (
    SELECT team_key, month_key,
           SUM(net_amount_usd)   AS actual,
           SUM(gross_margin_usd) AS gross_margin
      FROM fact_sales
     GROUP BY team_key, month_key
),
tgt AS (
    SELECT team_key, month_key, SUM(target_amount_usd) AS allocated_target
      FROM fact_targets
     GROUP BY team_key, month_key
),
hc AS (
    SELECT team_key, month_key,
           COUNT(*)            AS active_reps,
           SUM(is_ramp_month)  AS ramping_reps
      FROM bridge_rep_team_month
     GROUP BY team_key, month_key
),
joined AS (
    SELECT tp.team_key, tp.month_key, tp.month_key / 100 AS year,
           t.team_name, t.country_code, t.department_code,
           tp.planned_headcount, COALESCE(hc.active_reps, 0) AS active_reps,
           COALESCE(hc.ramping_reps, 0) AS ramping_reps,
           tp.plan_amount_usd AS plan,
           COALESCE(tgt.allocated_target, 0) AS allocated_target,
           COALESCE(rev.actual, 0) AS actual,
           COALESCE(rev.gross_margin, 0) AS gross_margin
      FROM fact_team_plan tp
      JOIN dim_team t ON t.team_key = tp.team_key
      LEFT JOIN rev ON rev.team_key = tp.team_key AND rev.month_key = tp.month_key
      LEFT JOIN tgt ON tgt.team_key = tp.team_key AND tgt.month_key = tp.month_key
      LEFT JOIN hc  ON hc.team_key  = tp.team_key AND hc.month_key  = tp.month_key
)
SELECT j.*,
       j.actual / NULLIF(j.allocated_target, 0)                              AS achievement,
       j.actual / NULLIF(j.plan, 0)                                          AS plan_achievement,
       j.actual - j.allocated_target                                         AS variance_to_target,
       j.plan - j.allocated_target                                           AS capacity_gap,
       SUM(j.actual) OVER (PARTITION BY j.team_key, j.year ORDER BY j.month_key)           AS ytd_actual,
       SUM(j.allocated_target) OVER (PARTITION BY j.team_key, j.year ORDER BY j.month_key) AS ytd_target,
       SUM(j.actual) OVER (PARTITION BY j.team_key, j.year ORDER BY j.month_key)
         / NULLIF(SUM(j.allocated_target) OVER (PARTITION BY j.team_key, j.year ORDER BY j.month_key), 0)
                                                                             AS ytd_achievement,
       (j.actual - LAG(j.actual) OVER (PARTITION BY j.team_key ORDER BY j.month_key))
         / NULLIF(LAG(j.actual) OVER (PARTITION BY j.team_key ORDER BY j.month_key), 0) AS mom_growth,
       DENSE_RANK() OVER (PARTITION BY j.month_key
                          ORDER BY j.actual / NULLIF(j.allocated_target, 0) DESC)      AS team_rank
  FROM joined j;


-- -----------------------------------------------------------------------------
-- vw_country_month_performance: roll-up of teams + country rank
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_country_month_performance;
CREATE VIEW vw_country_month_performance AS
WITH c AS (
    SELECT country_code, month_key, year,
           SUM(plan) AS plan, SUM(allocated_target) AS allocated_target, SUM(actual) AS actual,
           SUM(gross_margin) AS gross_margin, SUM(active_reps) AS active_reps,
           SUM(planned_headcount) AS planned_headcount
      FROM vw_team_month_performance
     GROUP BY country_code, month_key, year
)
SELECT c.*,
       c.actual / NULLIF(c.allocated_target, 0)   AS achievement,
       c.actual / NULLIF(c.plan, 0)               AS plan_achievement,
       c.actual - c.allocated_target              AS variance_to_target,
       SUM(c.actual) OVER (PARTITION BY c.country_code, c.year ORDER BY c.month_key)
         / NULLIF(SUM(c.allocated_target) OVER (PARTITION BY c.country_code, c.year ORDER BY c.month_key), 0)
                                                  AS ytd_achievement,
       DENSE_RANK() OVER (PARTITION BY c.month_key ORDER BY c.actual / NULLIF(c.allocated_target, 0) DESC)
                                                  AS country_rank
  FROM c;


-- -----------------------------------------------------------------------------
-- vw_company_month: company trend with 3-month moving average and running YTD
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_company_month;
CREATE VIEW vw_company_month AS
WITH m AS (
    SELECT month_key, year,
           SUM(plan) AS plan, SUM(allocated_target) AS target, SUM(actual) AS actual,
           SUM(gross_margin) AS gross_margin, SUM(active_reps) AS active_reps
      FROM vw_team_month_performance
     GROUP BY month_key, year
)
SELECT m.*,
       m.actual / NULLIF(m.target, 0)                                                   AS achievement,
       m.actual - m.target                                                              AS variance,
       (m.actual - LAG(m.actual) OVER (ORDER BY m.month_key))
         / NULLIF(LAG(m.actual) OVER (ORDER BY m.month_key), 0)                         AS mom_growth,
       (m.actual - LAG(m.actual, 12) OVER (ORDER BY m.month_key))
         / NULLIF(LAG(m.actual, 12) OVER (ORDER BY m.month_key), 0)                     AS yoy_growth,
       AVG(m.actual) OVER (ORDER BY m.month_key ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS actual_3m_avg,
       SUM(m.actual) OVER (PARTITION BY m.year ORDER BY m.month_key)                    AS ytd_actual,
       SUM(m.target) OVER (PARTITION BY m.year ORDER BY m.month_key)                    AS ytd_target
  FROM m;
