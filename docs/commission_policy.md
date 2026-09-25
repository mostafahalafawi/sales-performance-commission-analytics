# Commission Policy (fictional)

The incentive plan for Nexora's sales representatives. All parameters live in `config/config.yaml → commission_plans`
and nothing is hard-coded. Two independent implementations calculate the plan: `python/kpi_engine.py → add_commission`
and `sql/05_commission.sql`. The pipeline stops if they differ by more than USD 0.01 for any rep-month.

## 1. Measurement

| Item | Rule |
|---|---|
| Period | Calendar month |
| Revenue measured | Net revenue = invoices − credit notes **posted in the month** (USD) |
| Target | Rep's monthly target, latest revision |
| Achievement | Net revenue ÷ target |
| Eligibility | Rep active in the month (assigned to a team on the 15th) with a target |

## 2. Tiers (retroactive: the tier rate applies to the whole base)

| Tier | CP2024 (Jan-Dec 2024) | CP2025 (Jan-Dec 2025) | Rate |
|---|---|---|---:|
| T0 Below Gate | < 80% | **< 85%** | 0.0% |
| T1 Approaching | 80% - 99.99% | **85%** - 99.99% | 0.5% |
| T2 On Target | 100% - 119.99% | 100% - 119.99% | 1.0% |
| T3 Accelerator | ≥ 120% | ≥ 120% | 1.5% |

Bands are half-open: `min ≤ achievement < max`. Negative achievement (credit notes exceed invoices) falls in T0.

## 3. Commissionable base

```
weighted invoiced   = Σ invoice net revenue × product weight
weighted clawback   = Σ credit-note net revenue × product weight,
                      only for credit notes posted ≤ 90 days after the original invoice
weighted base       = weighted invoiced + weighted clawback
cap                 = 1.5 × monthly target
commissionable base = MIN( MAX(weighted base, 0), cap )
commission          = commissionable base × tier rate
```

| Parameter | Value | Why |
|---|---|---|
| Product weight | 1.25 for Cybersecurity and Managed Services, 1.0 otherwise | Steer the mix to strategic, high-margin lines |
| Cap | 150% of target | Prevents windfall payouts from one-off deals |
| Clawback window | 90 days | Revenue reversed soon after booking should not earn commission |

Design choices:
* **Tier is set on unweighted achievement.** Weighting changes how much is paid, not which tier the rep reaches.
* **Credit notes after 90 days** still reduce revenue and achievement but are not clawed back from commission.
* **Clawbacks are applied in the month they are posted, at that month's rate.** Insight 7 quantifies the leakage this
  creates and recommends booking them against the month of sale.
* **Credit notes posted after a rep has left** reduce team revenue but cannot be recovered from the rep.
* **Ramp months:** the target is reduced (40% / 65% / 85% in tenure months 1-3). The plan itself is unchanged.

## 4. Worked example (CP2025)

| Item | Value |
|---|---:|
| Target | 100,000 |
| Invoices: 70,000 standard + 40,000 Cybersecurity | 110,000 |
| Credit note on a 25-day-old Cybersecurity invoice | −8,000 |
| Credit note on a 120-day-old standard invoice | −2,000 |
| **Net revenue** | **100,000** |
| **Achievement** | **100% → T2 On Target (1.0%)** |
| Weighted invoiced = 70,000 + 40,000 × 1.25 | 120,000 |
| Weighted clawback = −8,000 × 1.25 (in window); the 120-day credit note is excluded | −10,000 |
| Weighted base | 110,000 |
| Cap = 1.5 × 100,000 | 150,000 |
| **Commission = 110,000 × 1.0%** | **1,100** |

## 5. Controls

* Unit tests pin the tier boundaries (79.99% / 80% / 85% / 119.99% / 120%), the cap, the clawback window and the
  handling of negative achievement (`tests/test_commission.py`).
* Every rep statement (`reports/rep_statements/`) recalculates with live Excel formulas and includes a check cell against
  the pipeline value.
* Rep-months without a target are flagged (DQ-T04) and paid nothing until the target is fixed.
