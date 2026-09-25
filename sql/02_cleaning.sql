-- =============================================================================
-- 02_cleaning.sql  |  SQL-side data-quality assertions on the loaded model
-- -----------------------------------------------------------------------------
-- Cleaning and standardisation happen in python/etl.py (rules in validation.py).
-- This file re-checks the result independently inside the database, so the
-- model is never trusted just because Python said so.
-- Every assertion must return failing_rows = 0. The pipeline stops otherwise.
-- =============================================================================
DROP VIEW IF EXISTS vw_dq_assertions;
CREATE VIEW vw_dq_assertions AS

-- A1: no duplicated sales lines (grain = one row per line_id)
SELECT 'A01' AS check_id, 'Duplicate sales lines' AS check_name,
       (SELECT COUNT(*) - COUNT(DISTINCT line_id) FROM fact_sales) AS failing_rows

UNION ALL
-- A2: invoices must be positive, credit notes negative
SELECT 'A02', 'Sign convention violated (INV <= 0 or CN >= 0)',
       (SELECT COUNT(*) FROM fact_sales
         WHERE (doc_type = 'INV' AND net_amount_usd <= 0)
            OR (doc_type = 'CN'  AND net_amount_usd >= 0))

UNION ALL
-- A3: orphan foreign keys (should be impossible with FK constraints, asserted anyway)
SELECT 'A03', 'Sales lines with unknown product / rep / team / customer',
       (SELECT COUNT(*) FROM fact_sales fs
          LEFT JOIN dim_product  p ON p.product_key   = fs.product_key
          LEFT JOIN dim_employee e ON e.employee_key  = fs.rep_key
          LEFT JOIN dim_team     t ON t.team_key      = fs.team_key
          LEFT JOIN dim_customer c ON c.customer_key  = fs.customer_key
         WHERE p.product_key IS NULL OR e.employee_key IS NULL
            OR t.team_key IS NULL OR c.customer_key IS NULL)

UNION ALL
-- A4: every credit note points to an invoice that exists
SELECT 'A04', 'Credit notes referencing a missing invoice',
       (SELECT COUNT(*) FROM fact_sales cn
         WHERE cn.doc_type = 'CN'
           AND NOT EXISTS (SELECT 1 FROM fact_sales i
                            WHERE i.document_no = cn.original_document_no AND i.doc_type = 'INV'))

UNION ALL
-- A5: a credit note can never exceed the value of its original invoice
SELECT 'A05', 'Credit note value larger than original invoice',
       (SELECT COUNT(*) FROM (
            SELECT cn.original_document_no,
                   -SUM(cn.net_amount_usd) AS credited,
                   (SELECT SUM(i.net_amount_usd) FROM fact_sales i
                     WHERE i.document_no = cn.original_document_no) AS invoiced
              FROM fact_sales cn
             WHERE cn.doc_type = 'CN'
             GROUP BY cn.original_document_no)
         WHERE credited > invoiced + 1.00)   -- USD 1 tolerance for FX rounding

UNION ALL
-- A6: financial identity gross - discount = net
SELECT 'A06', 'gross - discount <> net',
       (SELECT COUNT(*) FROM fact_sales
         WHERE ABS(gross_amount_usd - discount_amount_usd - net_amount_usd) > 0.01)

UNION ALL
-- A7: every target belongs to a rep who was active that month
SELECT 'A07', 'Targets for reps not active in that month',
       (SELECT COUNT(*) FROM fact_targets ft
         WHERE NOT EXISTS (SELECT 1 FROM bridge_rep_team_month b
                            WHERE b.rep_key = ft.rep_key AND b.month_key = ft.month_key))

UNION ALL
-- A8: invoices dated while the rep was not on any team
SELECT 'A08', 'Invoices booked by a rep outside an active assignment month',
       (SELECT COUNT(*) FROM fact_sales fs
         WHERE fs.doc_type = 'INV'
           AND NOT EXISTS (SELECT 1 FROM bridge_rep_team_month b
                            WHERE b.rep_key = fs.rep_key AND b.month_key = fs.month_key))

UNION ALL
-- A9: allocated rep targets should never exceed the team capacity plan by > 25%
SELECT 'A09', 'Team-months where allocated targets exceed plan by >25%',
       (SELECT COUNT(*) FROM (
            SELECT tp.team_key, tp.month_key, tp.plan_amount_usd,
                   SUM(ft.target_amount_usd) AS allocated
              FROM fact_team_plan tp
              LEFT JOIN fact_targets ft
                     ON ft.team_key = tp.team_key AND ft.month_key = tp.month_key
             GROUP BY tp.team_key, tp.month_key, tp.plan_amount_usd)
         WHERE allocated > plan_amount_usd * 1.25);


-- -----------------------------------------------------------------------------
-- Informational (not a failure): active rep-months without a target.
-- These are reported in the DQ report and excluded from achievement / commission.
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS vw_dq_missing_targets;
CREATE VIEW vw_dq_missing_targets AS
SELECT b.rep_key, e.employee_code, e.full_name, b.month_key, b.team_key
  FROM bridge_rep_team_month b
  JOIN dim_employee e ON e.employee_key = b.rep_key
  LEFT JOIN fact_targets ft ON ft.rep_key = b.rep_key AND ft.month_key = b.month_key
 WHERE ft.rep_key IS NULL;
