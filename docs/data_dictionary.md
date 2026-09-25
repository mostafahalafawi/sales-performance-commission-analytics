# Data Dictionary

All data is **synthetic**. Amounts are in USD unless the column name says `local`.
Keys ending in `_key` are integer surrogate keys; `*_code` / `*_id` are business keys from the source systems.

## Raw layer (`data/raw/`, produced by `generate_data.py`)

| File | Grain | Notes |
|---|---|---|
| `raw_sales_lines.csv` | invoice / credit-note line | ERP export: local currency, free-text country, mixed date formats, ~1-2% injected defects |
| `raw_sales_targets.csv` | rep x month x revision | Target revisions (keep latest), a few missing rep-months |
| `raw_team_plan.csv` | team x month | Finance capacity plan: planned headcount x full quota |
| `raw_employees.csv` | employee | Reps and managers, hire / exit dates |
| `raw_rep_assignments.csv` | rep x assignment period | Team history with start / end dates (transfers) |
| `raw_teams.csv` | team | Team, department, country, manager |
| `raw_customers.csv` | customer | Enterprise, SME and partner accounts |
| `raw_products.csv` | SKU | List price (USD) and unit cost % |
| `raw_fx_rates.csv` | month x currency | Local units per USD |
| `raw_commission_plans.csv` | plan x tier | Versioned commission tiers |

## Dimensions

### dim_date
| Column | Type | Description |
|---|---|---|
| date_key | int | YYYYMMDD, primary key |
| date | date | Calendar date |
| year, quarter, month_num, month_name | | Calendar attributes |
| month_key | int | YYYYMM, joins to month-grain facts |
| year_month | text | `2025-03` |
| month_start_date | date | First day of the month |
| day_of_month, days_in_month | int | |
| weekday_name | text | |
| is_month_end_window | 0/1 | Last N days of month (N = `analysis.month_end_days`, default 3) |
| month_index | int | 1..24, used for trend slopes |

### dim_country
| Column | Description |
|---|---|
| country_code | ISO-2 (EG, SA, AE, OM), primary key |
| country_name, currency, region | Region = GCC / North Africa |

### dim_department
| Column | Description |
|---|---|
| department_code | ENT, SME, CHN |
| department_name | Enterprise Sales, SME Sales, Channel & Partner Sales |

### dim_team
| Column | Description |
|---|---|
| team_key / team_id | Surrogate / business key (T01..T08) |
| team_name | e.g. "KSA Enterprise" |
| department_code, department_name, country_code, country_name | Team's department and market |
| manager_code, manager_name | Team manager |
| hc_2024, hc_2025 | Planned headcount per year |

### dim_employee
| Column | Description |
|---|---|
| employee_key / employee_code | Surrogate / business key (REP-###, MGR-##) |
| full_name | Synthetic name |
| role | Sales Representative or Sales Manager |
| hire_date, exit_date | Exit date empty for active employees |
| employment_status | Active / Exited (at period end) |
| tenure_band | 0-6m, 6-12m, 1-2y, 2-4y, 4y+ |
| is_new_hire_in_period | 1 if hired during the analysis window |
| current_team_id, manager_code, country_code, department_code | Latest assignment |

### dim_product
| Column | Description |
|---|---|
| product_key / sku | Surrogate / business key |
| product_name, product_line | 6 product lines, 20 SKUs |
| list_price_usd | Unit list price |
| unit_cost_pct | Cost as % of list price (drives gross margin) |
| is_strategic | 1 for Cybersecurity and Managed Services |
| commission_weight | 1.25 for strategic lines, else 1.0 |

### dim_customer
| Column | Description |
|---|---|
| customer_key / customer_code | Surrogate / business key |
| customer_name | Synthetic company name |
| country_code, customer_segment | Segment = Enterprise, SME, Partner |
| industry, city, owning_team_id, created_date | |

### dim_commission_plan
| Column | Description |
|---|---|
| plan_tier_key | Primary key |
| plan_id, plan_name, effective_from, effective_to | Plan version (CP2024, CP2025) |
| tier_order, tier_name | T0..T3 |
| min_achievement (inclusive), max_achievement (exclusive) | Tier band |
| commission_rate | Rate on commissionable base |
| strategic_weight, cap_multiple_of_target, clawback_window_days | Plan parameters |

## Facts and bridge

### fact_sales (grain: one invoice or credit-note line)
| Column | Description |
|---|---|
| line_id | Primary key |
| document_no, doc_type | `INV` (invoice) or `CN` (credit note) |
| original_document_no | For credit notes: the invoice being credited |
| date_key, month_key | Document date |
| rep_key, team_key | Seller and seller's team **at the time of sale** (credit notes inherit the original invoice's team) |
| customer_key, product_key, country_code | |
| currency, fx_rate, net_amount_local | Source currency values |
| quantity | Negative for credit notes |
| unit_list_price_usd, discount_pct | |
| gross_amount_usd | quantity x list price (list value) |
| discount_amount_usd | gross - net |
| net_amount_usd | **Revenue.** Negative for credit notes |
| cost_amount_usd, gross_margin_usd | Cost at unit_cost_pct; margin = net - cost |
| commission_weight | Copied from dim_product at load time |
| days_since_original | Credit notes: days between invoice and credit note (clawback window test) |
| is_month_end_window | 1 if dated in the last N days of the month |

### fact_targets (grain: rep x month, latest revision)
| Column | Description |
|---|---|
| rep_key, month_key | Primary key |
| team_key | Team the rep belonged to on the 15th of the month |
| date_key | First day of month (for the Power BI relationship) |
| target_amount_usd | Monthly target after ramp reduction |
| revision_no | Surviving revision |
| is_ramp_month | 1 in the first 3 months of tenure |

### fact_team_plan (grain: team x month)
| Column | Description |
|---|---|
| team_key, month_key, date_key | |
| planned_headcount | Seats in the plan |
| plan_amount_usd | planned_headcount x full quota (capacity plan) |

### bridge_rep_team_month (grain: active rep x month)
| Column | Description |
|---|---|
| rep_key, month_key | Primary key. A rep is active in a month if assigned on the 15th |
| team_key | Team as of the 15th |
| tenure_months | Months since hire month |
| is_ramp_month | tenure_months < 3 |

## KPI layer (written by `kpi_engine.py`)

### kpi_rep_month (grain: active rep x month)
Target, actual, variance, achievement, MoM growth, YTD actual / target / achievement, rolling 3M achievement,
deals, average deal size, discount rate, credit-note rate, month-end share, strategic mix, gross margin,
performance band, rank in team, rank in company, and the full commission calculation
(plan_id, tier_name, commission_rate, weighted_invoiced, weighted_clawback, credit_notes_outside_window,
weighted_base, cap_amount, commissionable_base, commission, is_capped, effective_commission_rate).

### kpi_team_month (grain: team x month)
Plan, allocated target, actual, gross margin, active reps, ramping reps, vacant seats, achievement,
plan achievement, variance to target / plan, capacity gap, commission, commission-to-revenue, YTD measures,
MoM growth, team rank.

### kpi_country_month (grain: country x month)
Country roll-up of the team measures with country rank.

## Data-quality outputs (`data/processed/`)
| File | Content |
|---|---|
| `dq_report.md` | Human-readable DQ report (reconciliation, rules, integrity checks) |
| `dq_rule_log.csv` | One row per rule with rows affected and action |
| `dq_integrity_checks.csv` | Post-load integrity checks |
| `dq_missing_targets.csv` | Active rep-months without a target |
| `quarantine_sales_lines.csv` | Rows held out, with the failed rule IDs |
| `reconciliation_python_vs_sql.csv` | Python engine vs SQL view reconciliation |
