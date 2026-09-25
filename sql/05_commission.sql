-- =============================================================================
-- 05_commission.sql  |  Commission calculation in SQL
-- -----------------------------------------------------------------------------
-- An independent SQL implementation of the documented commission policy
-- (docs/commission_policy.md). python/kpi_engine.py calculates the same thing
-- in pandas, and the pipeline reconciles the two to the cent. Two independent
-- implementations that agree give a payroll-grade result.
--
-- Policy (per rep, per month, plan chosen by effective date)
--   1. achievement      = net revenue / target
--   2. tier / rate      = plan tier where min <= achievement < max
--   3. weighted base    = invoiced revenue x product weight (1.25 strategic)
--                         + credit notes x weight, ONLY if posted within the
--                           clawback window (90 days) of the original invoice
--   4. capped base      = MIN(MAX(weighted base, 0), cap multiple x target)
--   5. commission       = capped base x tier rate
--   Rep-months without a target earn nothing and are flagged 'No Target'.
-- =============================================================================
DROP VIEW IF EXISTS vw_commission_rep_month;
CREATE VIEW vw_commission_rep_month AS
WITH plan AS (                                   -- one row per plan version
    SELECT DISTINCT plan_id, plan_name, effective_from, effective_to,
           strategic_weight, cap_multiple_of_target, clawback_window_days
      FROM dim_commission_plan
),
rep_month AS (                                   -- attach the plan in force
    SELECT r.rep_key, r.month_key, r.team_key, r.country_code, r.department_code,
           r.target, r.actual, r.is_ramp_month,
           p.plan_id, p.cap_multiple_of_target, p.clawback_window_days,
           printf('%04d-%02d-01', r.month_key / 100, r.month_key % 100) AS month_start
      FROM vw_rep_month_base r
      JOIN plan p
        ON printf('%04d-%02d-01', r.month_key / 100, r.month_key % 100)
           BETWEEN p.effective_from AND p.effective_to
),
weighted AS (                                    -- product-weighted base incl. clawbacks
    SELECT fs.rep_key, fs.month_key,
           SUM(CASE WHEN fs.doc_type = 'INV'
                    THEN fs.net_amount_usd * fs.commission_weight ELSE 0 END)          AS weighted_invoiced,
           SUM(CASE WHEN fs.doc_type = 'CN' AND fs.days_since_original <= p.clawback_window_days
                    THEN fs.net_amount_usd * fs.commission_weight ELSE 0 END)          AS weighted_clawback,
           SUM(CASE WHEN fs.doc_type = 'CN' AND fs.days_since_original >  p.clawback_window_days
                    THEN fs.net_amount_usd ELSE 0 END)                                 AS credit_notes_outside_window
      FROM fact_sales fs
      JOIN plan p
        ON printf('%04d-%02d-01', fs.month_key / 100, fs.month_key % 100)
           BETWEEN p.effective_from AND p.effective_to
     GROUP BY fs.rep_key, fs.month_key
),
calc AS (
    SELECT rm.*,
           rm.actual / NULLIF(rm.target, 0)                          AS achievement,
           COALESCE(w.weighted_invoiced, 0)                          AS weighted_invoiced,
           COALESCE(w.weighted_clawback, 0)                          AS weighted_clawback,
           COALESCE(w.credit_notes_outside_window, 0)                AS credit_notes_outside_window,
           COALESCE(w.weighted_invoiced, 0) + COALESCE(w.weighted_clawback, 0) AS weighted_base
      FROM rep_month rm
      LEFT JOIN weighted w ON w.rep_key = rm.rep_key AND w.month_key = rm.month_key
)
SELECT c.rep_key, c.month_key, c.team_key, c.country_code, c.department_code, c.plan_id,
       c.target, c.actual, c.achievement, c.is_ramp_month,
       c.weighted_invoiced, c.weighted_clawback, c.credit_notes_outside_window, c.weighted_base,
       c.cap_multiple_of_target * c.target                                      AS cap_amount,
       COALESCE(t.tier_name, 'No Target')                                       AS tier_name,
       COALESCE(t.commission_rate, 0)                                           AS commission_rate,
       CASE WHEN c.target IS NULL THEN 0
            ELSE MIN(MAX(c.weighted_base, 0), c.cap_multiple_of_target * c.target) END AS commissionable_base,
       CASE WHEN c.target IS NULL THEN 0
            ELSE MIN(MAX(c.weighted_base, 0), c.cap_multiple_of_target * c.target)
                 * COALESCE(t.commission_rate, 0) END                           AS commission,
       CASE WHEN c.target IS NOT NULL AND c.weighted_base > c.cap_multiple_of_target * c.target
            THEN 1 ELSE 0 END                                                   AS is_capped
  FROM calc c
  LEFT JOIN dim_commission_plan t
         ON t.plan_id = c.plan_id
        AND c.achievement >= t.min_achievement
        AND c.achievement <  t.max_achievement;
