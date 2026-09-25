# DAX Measures

Put all measures in a dedicated **`_Measures`** table (Enter data → empty table), grouped into display folders.
Table and column names match `data/processed/*.csv`. Formats: currency `$#,0`, percentages `0.0%`.

---

## 1. Revenue & drivers (display folder `1 Revenue`)

```DAX
Net Revenue =
SUM ( fact_sales[net_amount_usd] )
-- Invoices minus credit notes posted in the period. The number every KPI is measured on.

Invoiced Revenue =
CALCULATE ( [Net Revenue], fact_sales[doc_type] = "INV" )

Credit Notes =
CALCULATE ( [Net Revenue], fact_sales[doc_type] = "CN" )          -- negative value

Gross List Value =
CALCULATE ( SUM ( fact_sales[gross_amount_usd] ), fact_sales[doc_type] = "INV" )
-- What was sold at list price; the size of the business before discounting.

Discount Amount =
CALCULATE ( SUM ( fact_sales[discount_amount_usd] ), fact_sales[doc_type] = "INV" )

Discount Rate % =
DIVIDE ( [Discount Amount], [Gross List Value] )
-- Price realisation. Rising discount with falling volume = pricing pressure (see UAE Channel).

Credit Note Rate % =
DIVIDE ( - [Credit Notes], [Invoiced Revenue] )
-- Revenue quality. Under-performers run ~2x the rate of performers.

Gross Margin =
SUM ( fact_sales[gross_margin_usd] )

Gross Margin % =
DIVIDE ( [Gross Margin], [Net Revenue] )

Deals =
CALCULATE ( DISTINCTCOUNT ( fact_sales[document_no] ), fact_sales[doc_type] = "INV" )

Avg Deal Size =
DIVIDE ( [Invoiced Revenue], [Deals] )

Strategic Mix % =
DIVIDE (
    CALCULATE ( [Invoiced Revenue], dim_product[is_strategic] = 1 ),
    [Invoiced Revenue]
)

Month-End Share % =
DIVIDE (
    CALCULATE ( [Invoiced Revenue], fact_sales[is_month_end_window] = 1 ),
    [Invoiced Revenue]
)
-- Share of invoicing booked in the last 3 days of the month; > 50% combined with reversals = loading.
```

## 2. Target & achievement (display folder `2 Performance`)

```DAX
Target =
SUM ( fact_targets[target_amount_usd] )

Capacity Plan =
SUM ( fact_team_plan[plan_amount_usd] )
-- Planned headcount x full quota: what Finance budgeted if every seat were filled and ramped.

Achievement % =
DIVIDE ( [Net Revenue], [Target] )

Plan Achievement % =
DIVIDE ( [Net Revenue], [Capacity Plan] )

Variance =
[Net Revenue] - [Target]

Capacity Gap =
[Capacity Plan] - [Target]
-- Quota lost to vacant seats and ramping hires before anyone sells.

Achievement Status =
SWITCH (
    TRUE (),
    ISBLANK ( [Target] ), "No target",
    [Achievement %] >= 1.0, "On / above target",
    [Achievement %] >= 0.9, "Slightly below",
    "Materially below"
)

Achievement Color =
SWITCH (
    TRUE (),
    [Achievement %] >= 1.0, "#0CA30C",
    [Achievement %] >= 0.9, "#FAB219",
    "#D03B3B"
)
-- Use as Conditional formatting > Field value. Always pair with an icon or the status text.
```

## 3. Time intelligence (display folder `3 Time`)

`dim_date` must be marked as a date table (`dim_date[date]`).

```DAX
Net Revenue PM =
CALCULATE ( [Net Revenue], DATEADD ( dim_date[date], -1, MONTH ) )

MoM Growth % =
DIVIDE ( [Net Revenue] - [Net Revenue PM], [Net Revenue PM] )

Net Revenue PY =
CALCULATE ( [Net Revenue], SAMEPERIODLASTYEAR ( dim_date[date] ) )

YoY Growth % =
DIVIDE ( [Net Revenue] - [Net Revenue PY], [Net Revenue PY] )

YTD Net Revenue =
TOTALYTD ( [Net Revenue], dim_date[date] )

YTD Target =
TOTALYTD ( [Target], dim_date[date] )

YTD Achievement % =
DIVIDE ( [YTD Net Revenue], [YTD Target] )

MTD Net Revenue =
TOTALMTD ( [Net Revenue], dim_date[date] )

Rolling 3M Achievement % =
VAR _end = MAX ( dim_date[date] )
VAR _rev = CALCULATE ( [Net Revenue], DATESINPERIOD ( dim_date[date], _end, -3, MONTH ) )
VAR _tgt = CALCULATE ( [Target],      DATESINPERIOD ( dim_date[date], _end, -3, MONTH ) )
RETURN DIVIDE ( _rev, _tgt )
-- Smooths monthly timing noise; the basis for the decline watchlist.

Net Revenue 3M Avg =
DIVIDE (
    CALCULATE ( [Net Revenue], DATESINPERIOD ( dim_date[date], MAX ( dim_date[date] ), -3, MONTH ) ),
    3
)
-- 3-month moving average for trend lines (use at month granularity).
```

## 4. Commission (display folder `4 Commission`)

The commission is calculated in the pipeline (`kpi_rep_month`) because the tier is decided per rep-month. The DAX
check measure below re-derives the tier inside Power BI, so the report can show that both calculations agree.

```DAX
Commission =
SUM ( kpi_rep_month[commission] )

Commission to Revenue % =
DIVIDE ( [Commission], SUM ( kpi_rep_month[actual] ) )
-- Incentive cost per $1 of revenue. FY2024 0.92%, FY2025 0.84%.

Commissionable Base =
SUM ( kpi_rep_month[commissionable_base] )

Rep-Months =
COUNTROWS ( kpi_rep_month )

Reps Paid % =
DIVIDE ( CALCULATE ( [Rep-Months], kpi_rep_month[commission] > 0 ), [Rep-Months] )

Capped Rep-Months =
CALCULATE ( [Rep-Months], kpi_rep_month[is_capped] = 1 )

Accelerator Share of Commission % =
DIVIDE (
    CALCULATE ( [Commission], kpi_rep_month[tier_name] = "T3 Accelerator" ),
    [Commission]
)

Commission (DAX check) =
SUMX (
    FILTER ( kpi_rep_month, NOT ISBLANK ( kpi_rep_month[target] ) ),
    VAR _ach  = kpi_rep_month[achievement]
    VAR _plan = kpi_rep_month[plan_id]
    VAR _rate =
        CALCULATE (
            MAX ( dim_commission_plan[commission_rate] ),
            FILTER (
                ALL ( dim_commission_plan ),
                dim_commission_plan[plan_id] = _plan
                    && dim_commission_plan[min_achievement] <= _ach
                    && dim_commission_plan[max_achievement] > _ach
            )
        )
    VAR _base = MIN ( MAX ( kpi_rep_month[weighted_base], 0 ), kpi_rep_month[cap_amount] )
    RETURN _base * _rate
)

Commission Check Difference =
ROUND ( [Commission] - [Commission (DAX check)], 2 )     -- must be 0.00
```

### What-if parameters (Modeling → New parameter → Numeric range)

| Parameter | Range | Default |
|---|---|---|
| `Gate Threshold` | 0.70 - 0.90, step 0.01 | 0.80 |
| `Accelerator Rate` | 0.010 - 0.020, step 0.001 | 0.015 |

```DAX
Commission (What-if) =
VAR _gate = [Gate Threshold Value]
VAR _acc  = [Accelerator Rate Value]
RETURN
SUMX (
    FILTER ( kpi_rep_month, NOT ISBLANK ( kpi_rep_month[target] ) ),
    VAR _ach  = kpi_rep_month[achievement]
    VAR _rate =
        SWITCH ( TRUE (),
            _ach < _gate, 0,
            _ach < 1.0,   0.005,
            _ach < 1.2,   0.010,
            _acc )
    RETURN MIN ( MAX ( kpi_rep_month[weighted_base], 0 ), kpi_rep_month[cap_amount] ) * _rate
)

What-if vs Actual Commission =
[Commission (What-if)] - [Commission]
```

## 5. Headcount & capacity (display folder `5 Capacity`)

```DAX
Active Reps (Period End) =
VAR _m = MAX ( dim_date[month_key] )
RETURN CALCULATE ( DISTINCTCOUNT ( kpi_rep_month[rep_key] ), kpi_rep_month[month_key] = _m )

Planned Headcount (Period End) =
VAR _m = MAX ( dim_date[month_key] )
RETURN CALCULATE ( SUM ( kpi_team_month[planned_headcount] ), kpi_team_month[month_key] = _m )

Vacant Seat-Months =
SUM ( kpi_team_month[vacant_seats] )

Ramping Rep-Months =
CALCULATE ( [Rep-Months], kpi_rep_month[is_ramp_month] = 1 )
```

## 6. Ranking (display folder `6 Rank`)

```DAX
Salesperson Rank =
IF (
    NOT ISBLANK ( [Target] ),
    RANKX (
        FILTER ( ALLSELECTED ( dim_employee[full_name] ), NOT ISBLANK ( [Target] ) ),
        [Achievement %], , DESC, DENSE
    )
)

Team Rank =
RANKX ( ALLSELECTED ( dim_team[team_name] ), [Achievement %], , DESC, DENSE )

Country Rank =
RANKX ( ALLSELECTED ( dim_country[country_name] ), [Achievement %], , DESC, DENSE )

Deal Volume Index =
VAR _dept = SELECTEDVALUE ( dim_employee[department_code] )
VAR _deptAvg =
    CALCULATE (
        AVERAGEX ( VALUES ( dim_employee[employee_key] ), [Deals] ),
        ALLSELECTED ( dim_employee ),
        dim_employee[department_code] = _dept
    )
RETURN DIVIDE ( [Deals], _deptAvg ) * 100
-- 100 = department average. The #1 root-cause driver (r = 0.88 with achievement).
```

## 7. Narrative (display folder `7 Narrative`)

```DAX
Title Executive =
"Net revenue " & FORMAT ( [Net Revenue] / 1e6, "$#,0.0" ) & "M | "
    & FORMAT ( [Achievement %], "0.0%" ) & " of target | "
    & FORMAT ( [YoY Growth %], "+0.0%;-0.0%" ) & " YoY"

Largest Gap Country =
VAR _t = TOPN ( 1, VALUES ( dim_country[country_name] ), [Variance], ASC )
RETURN CONCATENATEX ( _t, dim_country[country_name] )

Insight Gap Text =
"Largest gap: " & [Largest Gap Country] & " ("
    & FORMAT ( CALCULATE ( [Variance], dim_country[country_name] = [Largest Gap Country] ) / 1e6, "$#,0.0" )
    & "M vs target)"
```

## Calculated columns

| Table | Column | DAX | Purpose |
|---|---|---|---|
| dim_date | `Month Label` | `FORMAT ( dim_date[date], "mmm yy" )` sort by `month_key` | Axis labels |
| kpi_rep_month | already has `achievement_band`, `tier_name`, `performance_band` | n/a | Commission pages |
| dim_employee | `Rep Label` | `dim_employee[full_name] & " (" & dim_employee[employee_code] & ")"` | Unique rep labels |
