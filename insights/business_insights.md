# Business Insights - Sales Performance & Commission Analytics

> **Synthetic data.** Nexora Business Solutions is fictional and every figure below comes from generated data.
> The insights show the analytical method: each one was derived from pipeline outputs, not written in advance.

## How these insights were produced

```
generate data -> validate & model -> calculate KPIs -> run 17 business queries + Python analysis -> read results -> write insights
```

* **FACT**: a number produced by the pipeline. The source file is named so it can be re-checked.
* **INTERPRETATION**: what the fact most likely means for the business. This is a judgement, not a measurement.
* **RECOMMENDATION**: the action the interpretation supports.

Source paths are relative to `reports/`. Headline numbers regenerate on every run in `insights/kpi_summary.md` and
`reports/analysis_results.json`.

---

## 1. The FY2025 miss is mainly a target-setting problem, not a sales collapse

**FACT**
* Net revenue grew **+12.9%**, from $60.4M to $68.2M. Allocated targets grew **+27.2%**, from $59.0M to $75.0M.
  Achievement therefore fell from **102.4% to 90.9%** (`sql_results/q01`).
* The 2024 to 2025 revenue bridge (LMDI decomposition, `analysis/yoy_bridge_company.csv`) breaks the change down as follows:
  * headcount: **+$7.4M**
  * deal volume: **+$3.6M**; deals per rep-month rose from 8.41 to 8.89
  * deal size and mix: **-$2.8M**
  * discount: +$0.2M
  * credit notes: -$0.5M

**INTERPRETATION**
Productivity per rep improved, since reps closed more deals. The gap comes from the target model: a 15% quota uplift was
applied on top of seven added seats. The negative deal-size effect is mostly a mix effect. SME, which has smaller deals,
grew fastest (+$5.0M). Within each department, deal size was almost flat (`yoy_bridge_department.csv`).

**RECOMMENDATION**
Build FY targets bottom-up: expected headcount × realistic productivity × market growth. Then run the headcount plan and
the quota uplift as one scenario, not two separate stretches. Add a mid-year re-forecast checkpoint.

---

## 2. Two markets explain 97% of the FY2025 execution gap, and UAE Channel broke in June 2025

**FACT**
* FY2025 execution gap (actual minus allocated target) by market (`analysis/plan_bridge_country.csv`):
  * UAE: **-$4.37M**
  * Egypt: **-$2.27M**
  * KSA: -$0.47M
  * Oman: **+$0.28M**
* UAE Channel achieved **102.8%** before June 2025 and **80.4%** from June 2025 onward. Over the same break:
  * deals per rep-month fell 7.6 → 7.0
  * discount rate rose **17.9% → 21.0%**
  * credit-note rate rose 2.4% → 3.4%
* Egypt Enterprise (85.1%) and Egypt Channel (87.5%) are the other teams below 90% (`sql_results/q03`).

**INTERPRETATION**
The UAE Channel break is sudden and has three symptoms at once: fewer orders, deeper discounts and more returns. That
pattern fits a partner-side event, such as losing or weakening a reseller or a competitor pricing move. It does not fit
a gradual decline in rep effort. Because the team kept its full target, the whole shortfall shows up as "rep
under-performance".

**RECOMMENDATION**
Run a partner-level review of UAE Channel, looking at order frequency by partner before and after May 2025. Put channel
discounts above 20% behind an approval workflow. If the partner loss is confirmed, re-base the H2 target instead of
paying for it through zero-commission months.

---

## 3. Deal volume, not pricing, separates under-performers from performers

**FACT** (`sql_results/q10`, fully ramped reps with at least 6 months, drivers indexed within each department)

| Segment | Reps | Deal-volume index | Deal-size index | Discount | Credit-note rate |
|---|---:|---:|---:|---:|---:|
| Under-performers (<80%) | 18 | **78.5** | 93.2 | 11.9% | **3.75%** |
| Performers (≥100%) | 34 | **116.2** | 103.6 | 10.4% | 1.81% |

* Across FY2025 reps, deal volume vs achievement shows a correlation of **r = 0.88** (`images/performance_analysis.png`).
* Strategic-product mix is *higher* among under-performers (32.7% vs 28.1%), so it is not a cause.

**INTERPRETATION**
Under-performers close about a third fewer deals than performers, while their deal sizes are only about 10% smaller. The
problem is pipeline volume, not negotiation. Their credit-note rate is about 2×, which suggests some deals are pushed
before the customer is ready.

**RECOMMENDATION**
Coach under-performers on pipeline generation (activity and meetings), not on discount discipline. Add leading
indicators such as opportunities created and pipeline coverage to the model. CRM pipeline data is listed under Future
Improvements.

---

## 4. Target phasing ignores Ramadan and the Gulf summer

**FACT** (`sql_results/q13`)
* GCC achievement drops to **83.7% in March 2025 (Ramadan)** and **76.2% in August 2025**. In 2024 the same pattern shows
  in March (88.8%) and August (82.9%).
* Finance phases targets with one company-wide monthly profile for all four markets.

**INTERPRETATION**
Part of the monthly "under-performance" is calendar, not people. A flat profile creates predictable red months in the
GCC. That produces false alarms in reviews and zero-commission months that reps cannot influence.

**RECOMMENDATION**
Phase targets by country, using the Hijri calendar for Ramadan and Eid and a summer profile for the GCC. The annual
target stays the same; only its monthly distribution changes.

---

## 5. Monthly tiers make payouts volatile. A quarterly plan would cost less and pay more reps

**FACT**
* 37.0% of rep-quarters contain both a below-gate month (<80%) and an accelerator month (≥120%) (`sql_results/q17`).
* In 18.6% of rep-quarters, the rep was paid at the accelerator rate in at least one month but missed target for the
  quarter.
* A what-if with the same rates, weights and cap, measured quarterly (`analysis_results.json → commission_scenarios`):
  * total commission would change by **-6.6%**; the FY2025 cost falls from $576K to **$496K** (-13.9%)
  * the share of periods with a payout rises from 56.5% of rep-months to **62.9% of rep-quarters**

**INTERPRETATION**
With about 6-12 deals a month, monthly achievement is mostly timing noise. Retroactive monthly tiers pay accelerators on
good-timing months and nothing on bad ones. The plan ends up rewarding deal timing more than sustained performance.

**RECOMMENDATION**
Measure tiers quarterly and pay monthly advances at T1 rates, with a quarterly true-up. This lowers cost and volatility
together, which is rare for a compensation change.

---

## 6. Incentive spend is concentrated in the accelerator tier; the 2025 gate change barely moved cost

**FACT**
* The T3 accelerator pays **73.3% (2024) and 74.8% (2025)** of all commission, from 31% and 26% of rep-months (`sql_results/q07`).
* The effective commission rate peaks at **1.60% of revenue at 120-149% achievement**. It falls to 1.21% at 150%+ because
  the 150% cap applies (324 capped rep-months) (`sql_results/q08`).
* The top 20% of reps earn **44.6%** of commission and deliver 40.8% of revenue (`commission_concentration`).
* Raising the gate from 80% to 85% in 2025 affected 44 rep-months (4.9%) and saved **$14.2K, 2.5% of FY2025 commission**
  (`sql_results/q14`).

**INTERPRETATION**
The plan's cost lever is the accelerator, not the gate. The 85% gate mostly removed small payouts from reps who were
already struggling, while accelerator payouts were unchanged.

**RECOMMENDATION**
If leadership wants to control incentive cost, redesign accelerator eligibility, for example by requiring quarterly
achievement of at least 100% to unlock T3. Consider reverting the gate to 80%, since it saves little and costs goodwill.

---

## 7. Month-end deal loading is a revenue-quality risk, and the clawback does not fully correct it

**FACT**
* 22.4% of invoiced revenue is booked in the last 3 days of each month, which is about 10% of calendar days.
* 12 rep-months across **5 reps** booked at least 50% of the month's invoicing in the month-end window. At least 25% of that
  revenue was reversed within 60 days, **$916K in total**. **7 of those 12 months were paid at the accelerator tier**
  (`sql_results/q11`).
* The clawback deducts credit notes in the month they are posted, at that month's rate. Restating them to the month of
  sale would reduce commission by **$9.9K (0.87%)** (`commission_scenarios.clawback_leakage_usd`).
* 3 of the 24 statistical anomalies (robust z-score ≥ 3.5) are spikes concentrated in the month-end window
  (`analysis/anomalies.csv`).

**INTERPRETATION**
The financial leakage is small, but the behaviour is not. The same reps repeat the pattern and reach the accelerator tier
in those months. The current clawback recovers at the *next* month's rate, which is often 0% because a loaded month is
followed by a weak one.

**RECOMMENDATION**
1. Apply clawbacks to the month of the original sale and recalculate that month's tier.
2. Add the month-end reversal flag to the monthly sales-ops review.
3. Require manager sign-off for month-end deals above a threshold for the reps flagged here.

---

## 8. Performance drops before a rep leaves, which gives an early-warning signal

**FACT**
* The 17 reps who left during the period achieved **79.2%** in their last 0-2 months, against **107.9%** six or more
  months before exit (`sql_results/q16`).
* **7 active reps** now show a sustained decline: the slope of rolling 3-month achievement is ≤ -5pp per month over 6
  months and the latest value is below 90% (`analysis/declining_reps_trend.csv`). Three of them were above 140% six months
  ago.

**INTERPRETATION**
A sustained drop in rolling achievement is a leading indicator of attrition risk. Replacing a rep costs a 1-3 month
vacancy plus a ramp period (insight 9). Acting early on the watchlist is cheaper.

**RECOMMENDATION**
Send the decline watchlist to sales managers and HR every month, and hold a structured check-in (territory, pipeline,
engagement) with each rep on it.

---

## 9. Capacity gaps cost ~$1.7M a year, and ramp relief ends too early

**FACT**
* Vacant seats removed **$1.63M (2024) and $1.71M (2025)** from the capacity plan before any rep sold anything
  (`analysis/plan_bridge_company.csv`).
* New hires reach 112.9% in tenure month 2, when targets are reduced to 85%. Once ramp relief ends they drop to **82.7%,
  88.5% and 89.2% in months 3-5**, against 92.7% after month 6 (`sql_results/q15`).

**INTERPRETATION**
The 3-month ramp schedule (40/65/85%) is shorter than the time new hires actually take to become productive. New hires
land in the below-gate tier just as they come off ramp, which is a known driver of early attrition.

**RECOMMENDATION**
Extend ramp relief to 6 months (for example 40/65/85/90/95/100%), and start backfill hiring on resignation rather than
on the exit date.

---

## 10. Devices earn commission like core products but deliver almost no margin

**FACT** (`sql_results/q09`)
* Devices are **12.6% of revenue but 2.9% of gross margin** (10.3% GM), with the highest discount rate at 13.7%. They
  carry the same 1.0× commission weight as Connectivity.
* Cybersecurity has the best margin (**63.4% GM**, 25.2% of margin). The two strategic lines weighted at 1.25× grew
  **+9.9% and +11.8%**, below company growth of +12.9%.

**INTERPRETATION**
The commission plan pays on revenue, not margin. The 1.25× strategic weighting has not shifted the mix towards the
high-margin lines in this data.

**RECOMMENDATION**
Move to margin-aware weights, for example Devices at 0.5× and Cybersecurity at 1.5×, or pay on gross margin for hardware.
Track strategic mix as an explicit KPI on the Commission page.

---

## Data-quality notes that affect interpretation

* 31,547 raw sales lines break down as: **31,084** loaded, **275** quarantined, **188** duplicates dropped. The rows
  reconcile exactly (`data/processed/dq_report.md`).
* 6 active rep-months have no target and are excluded from achievement and commission (DQ-T04). They must be resolved
  before payroll.
* 8 credit notes were quarantined because their original invoice was itself quarantined or incomplete (DQ-S17/S18). They
  need a manual review.
