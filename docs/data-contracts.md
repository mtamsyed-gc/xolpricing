# Data contracts

Version 0.1 · 2026-10-14 · companion to [`scope.md`](scope.md)

This document defines:
- §2: the input templates (standalone mode) and the ETE mapping (extension mode)
- §3: the **internal schema** both adapters write to
- §4: the **Control workbook** that drives a run
- §5: the **Results workbook** tables Alteryx writes back

## 1. Conventions

| Item | Rule |
|---|---|
| File format | CSV UTF-8 with one header row, or .xlsx (first sheet / named range). ETE inputs may be Parquet |
| Dates | ISO `yyyy-mm-dd`; months as the first day of the month |
| Amounts | Original transaction currency, signed as booked (reversals negative). FX as-if happens in stage 02, never in the source |
| Keys | Text, trimmed, upper-cased by the adapter. Composite keys use `\|` |
| Nulls | Empty cell. Never `0` for unknown |
| Names | Internal columns are `snake_case`. Template columns use the same names, so the standalone adapter only types and validates |
| Required | **R** = run stops if missing; **C** = conditionally required (condition stated); **O** = optional, feature disabled if missing |

## 2. Input modes

### 2.1 Standalone — line mode (`L`)
Full claim-line data plus member-level exposure. Enables every stage.

**`members.csv`** — one row per member coverage period

| Column | Type | Req | Notes |
|---|---|---|---|
| member_id | text | R | Unique per insured life within the cedant |
| policy_id | text | R | Group / individual policy |
| scheme_id | text | R | Master contract / employer group; event grouping key |
| policy_start | date | R | Policy inception: the **risk-attaching date** (RA cohort) |
| policy_end | date | R | Inclusive |
| cover_start | date | R | Member cover start (≥ policy_start for joiners) |
| cover_end | date | R | Member cover end, inclusive (≤ policy_end for leavers) |
| plan | text | O | Plan / category; drives the benefit limit lookup if `annual_limit` is blank |
| annual_limit | number | O | Per-member annual benefit limit, in currency |
| segment | text | O | Free segmentation field (line, country, product); a mix-risk check runs on it |
| site | text | O | Location / worksite; per-event concentration and clustering |
| currency | text | R | ISO code of the premium |
| written_premium | number | R | Premium for this cover period (gross, or the basis chosen in Control) |
| dob, gender, relation | date/text | O | Diagnostics only |

**`claims_lines.csv`** — one row per claim line

| Column | Type | Req | Notes |
|---|---|---|---|
| claim_line_id | text | R | Unique |
| claim_id | text | R | Reversals share the claim_id and are netted within it |
| member_id | text | R | Must match `members.member_id` (match rate gate ≥ 95%) |
| policy_id | text | O | Improves matching |
| treatment_date | date | R | Incurred trigger |
| admission_date, discharge_date | date | O | IP episode building, event clustering |
| reported_date | date | C | Required for the reported basis and the IBNR indicator |
| paid_date | date | C | Required for the paid trigger (`paid in N`) |
| amount | number | R | Payer share (reinsured base), signed |
| status | text | R | Mapped to `Paid` / `Outstanding` / `Rejected` / `Reversed` |
| currency | text | R | Transaction currency |
| event_id | text | O | Supplied event identifier; overrides clustering |
| cause_code | text | O | Accident / cause code; clustering key |
| icd_code | text | O | Diagnostics |
| segment | text | O | Falls back to the member's segment |

### 2.2 Standalone — claimant mode (`C`)
For cedants who supply a large-claimant listing plus aggregates. Disables line-level accumulation, member-level exposure and clustering. Per-event cover then needs a supplied event_id or scenario inputs.

**`claimants.csv`** — one row per member × accumulation period (already accumulated by the cedant)

| Column | Type | Req | Notes |
|---|---|---|---|
| member_id | text | R | |
| scheme_id | text | O | |
| period_start, period_end | date | R | Accumulation period (member's policy year for RA, treaty year for LO) |
| cohort_month | date | C | Policy inception month. Required for RA |
| first_treatment_date | date | O | Improves allocation to the treaty year on LO |
| paid_amount, os_amount | number | R | At valuation |
| claim_count | int | O | |
| annual_limit | number | O | |
| currency | text | R | |
| event_id, site | text | O | |
| segment | text | O | |

**`exposure_monthly.csv`** — segment × month

| Column | Type | Req | Notes |
|---|---|---|---|
| month | date | R | |
| segment | text | O | |
| lives_inforce | number | R | Average or month-end in-force (LO exposure; averaged) |
| lives_new | number | C | Lives incepting or renewing in the month (RA exposure; summed). Required for RA |
| written_premium, earned_premium | number | R | |

**`claims_aggregate_monthly.csv`** — segment × incurred month, **all** claims (needed for stop loss and frequency checks)

| Column | Type | Req | Notes |
|---|---|---|---|
| incurred_month | date | R | |
| segment | text | O | |
| cohort_month | date | O | RA allocation of attritional claims; if absent, apportioned by exposure |
| paid_amount, os_amount | number | R | |
| claim_count | number | O | |

**`run_meta.csv`** (both standalone modes): `valuation_date` (R), `listing_threshold` (C mode: amount above which `claimants.csv` is complete; 0 = complete listing), `currency_basis` (R), `data_source_note` (O).

### 2.3 ETE extension mode (`E`)
Reads the payer-level outputs of the ete-dashboard pipeline (SPEC P7 names; the interim pipeline writes the same tables under `out/`). Mapping current as of ete-dashboard `side_tools/interim_pipeline`, 2026-10-14.

| Internal | ETE source | Rule |
|---|---|---|
| **claims_line** | `fact_claims` | Filter `InExperience = 1` and `StatusClass ∉ {Rejected}` |
| claim_line_id | ClaimKey | |
| claim_id | ClaimID | |
| member_id | LifeKey | Payer \| TPA \| Policy \| Member, already normalised |
| policy_id | PolKey | |
| scheme_id | dim_life.`Master Contract ID` | Join on LifeKey |
| treatment_date | TreatmentDate | |
| reported_date | ReportedMonth | Month precision only; flagged in the run log |
| paid_date | PaidMonth | Month precision only |
| amount | PayerShare | Paid + OS at extract |
| status | StatusClass | Paid / Outstanding / Reversed |
| currency | dim_life.Currency | |
| segment | Configurable: any dim_life attribute (default `Policy Type`) | |
| event_id | x_EventID | When the engine supplies it (SPEC §8) |
| admission_date, discharge_date | Admission Date, Discharge Date | When supplied (SPEC §8 optional fields) |
| **members** | `dim_life` | One row per LifeKey × UWY |
| policy_start | PolicyStart | RA cohort |
| policy_end | PolicyEndAdj | |
| cover_start / cover_end | CovStart / CovEnd | |
| plan | `Plan / Category` | |
| written_premium | WrittenPrem_Gross / _GrossNet / _Net | Basis chosen in Control (matches the gc-mr-powerbi toggle) |
| **exposure** | `fact_member_month` | Used directly for earned LY and premium by month (no rebuild) |
| earned_ly | EarnedLY_Gross / _Net | Zero-premium rule already applied by the engine |
| earned_premium | EarnedPrem_* | |
| valuation_date | run manifest extract date | |
| *reference only* | `cf_selected`, `ultimates` | Portfolio CFs are shown next to the IBNR inputs as **reference**, with the large-claim caveat (D3); never applied automatically |

Mix of TPAs: a payer-level ETE output can contain several TPAs. `TPA` is carried as an attribute, and a per-TPA filter is available in Control.

## 3. Internal schema (adapter output)

Every adapter writes exactly these tables, and everything downstream reads only these.

**`int_claims`** — grain: claim line (L, E) or claimant-period (C); `grain` column = `LINE` / `CLAIMANT`

| Column | Type | Notes |
|---|---|---|
| claim_key | text | Line id, or member_id \| period_start |
| claim_id, member_id, policy_id, scheme_id | text | |
| cohort_month | date | Month of policy_start (RA allocation) |
| treatment_date, reported_date, paid_date | date | |
| admission_date, discharge_date | date | |
| amount_paid, amount_os | number | Original currency |
| currency | text | |
| status | text | Mapped class |
| annual_limit | number | From the claim, the member, or the plan lookup |
| segment, site, event_id, cause_code | text | |
| source_mode | text | L / C / E |
| source_file, source_row | text / int | Lineage |

**`int_members`** (L, E only) — grain: member coverage period; columns as in `members.csv` plus `cohort_month`, `cov_days`.

**`int_exposure`** — grain: segment × cohort_month × exposure_month

| Column | Type | Notes |
|---|---|---|
| segment | text | |
| cohort_month | date | Policy inception month (RA). C mode: from `lives_new`, with each cohort earned uniformly over 12 months |
| exposure_month | date | |
| earned_ly | number | Life-years earned in the month |
| written_ly | number | Booked at the cohort month (RA) |
| earned_premium, written_premium | number | |
| lives_inforce | number | |

**`int_claims_aggregate`** — grain: segment × cohort_month × incurred_month: `amount_paid`, `amount_os`, `claim_count`. In L and E modes it is built from `int_claims`; in C mode it comes from the monthly file.

**`int_run_meta`** — single row: run_id, source_mode, valuation_date, premium_basis, listing_threshold, capability flags (`has_reported_date`, `has_paid_date`, `has_member_exposure`, `has_line_detail`, `has_event_id`, `has_site`, `date_precision`), and a hash per input file.

**Capability flags drive stage availability:**

| Stage / feature | Requires |
|---|---|
| Per-person accumulation from lines | `has_line_detail` |
| Paid trigger | `has_paid_date` |
| Event clustering | `has_line_detail` and (`has_site` or scheme_id) |
| Supplied-event per-event pricing | `has_event_id` |
| Member-level RA exposure | `has_member_exposure` |
| Indicated unearned / unwritten factors | written and earned premium by cohort |

## 4. Control workbook (`excel/templates/XoL_Control.xlsx`)

Every input block is an **Excel Table** (or a named range) with a fixed name, which Alteryx reads by name. Formatting conventions:
- Yellow cells are inputs; there are no formulas in input tables.
- Each judgement input has a `justification` column, carried into the run log.
- Caveat text sits in a comment row above the block.

| Table name | Grain | Key columns |
|---|---|---|
| `tbl_params` | One row per scalar parameter | param_id, group, value, unit, allowed_values, description, justification |
| `tbl_fx` | Currency | currency, priced_rate_to_quote_ccy, rate_date |
| `tbl_limits` | Plan | plan, annual_limit, sub_limit_notes |
| `tbl_trend` | Year | year, client_pct, market_pct, inflation_pct, residual_pct, weight_client, weight_market, weight_inflation, one_off_adj, large_claim_trend_pct (O), premium_trend_pct |
| `tbl_completion` | Year × component × side × ap_band | year, component (`IBNR` / `UNEARNED` / `UNWRITTEN`), side (`PRE_AP` / `POST_AP_AMOUNT` / `POST_AP_COUNT`), ap_band_from (default 0), selected_factor, indicated_factor (written back by Alteryx, read-only), justification |
| `tbl_weights` | Year | year, months_complete, use_default (1/0), custom_weight, justification |
| `tbl_layers` | Cover × option × layer | cover (`PP` / `EV` / `SL`), option_id (1–4), layer (1/2), ap, limit, aal, aad, co_participation_pct, reinstatements_n (blank = unlimited), reinstatement_cost_pct, reinst_pro_rata_time (1/0), sl_basis (`GROSS` / `NET_OF_XOL`), sl_ap_pct_epi, sl_limit_pct_epi, active |
| `tbl_programme` | Order | step, cover (`QS` / `PP` / `EV` / `SL`), active |
| `tbl_event_rule` | Scalar set | id_source (`SUPPLIED` / `CLUSTER`), window_days, group_key (`SCHEME` / `SITE` / `CAUSE` combinations), min_lives |
| `tbl_ev_scenarios` | Scenario | scenario_name, lives, cost_per_life, return_period_yrs, site |
| `tbl_loadings` | Cover | cover, reinsurer_expense_pct, brokerage_pct, profit_margin_pct, premium_tax_pct, risk_load_k |
| `tbl_expiring` | Cover × layer | ap, limit, rate_basis, rate, premium, recoveries_by_year (text) |
| `tbl_scenarios` | Scenario × override | scenario_id, scenario_name, target_table, target_key, target_column, override_value |

**`tbl_params` minimum set**

| group | param_id | Example |
|---|---|---|
| General | cedant, country, line, quote_ccy, fx_quote_to_usd, treaty_start, quote_year, run_label | — |
| Mode | source_mode (L/C/E), input_folder, ete_tpa_filter, premium_basis (GROSS/GROSSNET/NET), segment_field | L |
| Basis | treaty_basis (RA/LO), claims_trigger (INCURRED/PAID), paid_runoff_months, pp_accumulation (POLICY_YEAR/TREATY_YEAR) | RA |
| Exposure | epi_quote_year, expected_lives_quote_year (blank = derive), history_years | — |
| Data | output_threshold_pct_min_ap, policy_limit, years_included | 50% |
| Outliers | outlier_method (VALUE/IQR), outlier_value, iqr_multiplier | IQR, 1.5 |
| Credibility | full_cred_n, complement_source (STOCHASTIC/BENCHMARK/EXPIRING), benchmark_rate | 64 |
| Incidence | incidence_adj | 1.00 |
| QS | qs_cedant_retention_pct | 100% |
| Stochastic | iterations, seed, fit_cutoff (blank = AP), fit_candidates, fit_years, freq_model (POISSON/NEGBIN), sl_attritional_threshold, sl_cov_floor | 10000, 12345 |

## 5. Results workbook (`excel/templates/XoL_Results.xlsx`)

Alteryx overwrites the `out_*` data sheets in full each run. Presentation sheets reference them by formula, and loadings are applied in Excel (scope §7).

| Sheet / table | Grain | Content |
|---|---|---|
| `out_run_log` | Run | Parameters, justifications, seed, capability flags, stages run, versions, file hashes |
| `out_warnings` | Warning | stage, code, severity (STOP/FLAG), message, measure, value |
| `out_recon` | Stage × year × measure | Rows, amounts, lives: in / out / excluded / explained / unexplained |
| `out_completion_indicated` | Year × component | Indicated unearned/unwritten factors (also written to `tbl_completion.indicated_factor`) |
| `out_annualisation` | Cover × option × year × step | Paid/reported → +IBNR → +unearned → +unwritten: amount, count, frequency, severity |
| `out_large_claims` | Claimant-period (or event) | FGU, as-if, trended, completed, capped, outlier flag, XL by option |
| `out_bc_by_year` | Scenario × cover × option × layer × year | Exposure, count, frequency/1000, severity, XL amount, BC pppy, BC % EPI, weight |
| `out_bc_summary` | Scenario × cover × option × layer | Weighted BC (non-outlier, outlier, total), Z, sensitivity variants, min/max, leveraged trend |
| `out_fit` | Cover × candidate | Parameters, log-likelihood, AIC, BIC, KS, AD, fitted vs deterministic mean excess, selected flag |
| `out_sim_summary` | Scenario × cover × option × layer | Mean, SD, CoV, P(0), P50–P99.5, E[reinstatement premium], λ used, iterations, seed |
| `out_sim_quantiles` | Scenario × cover × option × measure × q | q = 0.001 … 0.999 for recoveries, reinstatement premium, retained claims, retained LR (Excel lookups for cedant impact) |
| `out_asif_by_year` | Scenario × cover × option × year | As-if recoveries vs as-if premium, cumulative |
| `out_ev_scenarios` | Scenario | Deterministic per-event losses by option |

## 6. Validation at the adapters (stage 01)

| Code | Check | Severity |
|---|---|---|
| A01 | Required columns present and typed | STOP |
| A02 | cover_start ≤ cover_end; policy_start ≤ cover_start; cover_end ≤ policy_end | FLAG (rows listed) |
| A03 | Claim member match rate ≥ 95% (L) | STOP below |
| A04 | Treatment date ≤ valuation date | STOP |
| A05 | Treatment within the member's cover period | FLAG (out-of-cover amount ≤ 1%) |
| A06 | Net amount per claim_id ≥ 0 after netting reversals | FLAG |
| A07 | Currency present in `tbl_fx` | STOP |
| A08 | C mode: listing complete above `listing_threshold` (Σ listing ≤ Σ aggregate by year) | STOP if exceeded |
| A09 | Exposure: lives_new summed over 12 months vs average lives_inforce, within 0.6–1.4 | FLAG |
| A10 | ETE mode: payer totals in `fact_claims` reconcile to the ETE recon ledger | FLAG |
