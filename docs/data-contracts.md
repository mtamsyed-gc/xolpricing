# Data contracts

Version 0.4 · 2026-10-14 · companion to [`scope.md`](scope.md)

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
| Amounts | Original transaction currency, signed as booked (reversals negative). The stage-01 adapter adds quote-currency amounts at the priced FX rate (`ctl_fx`): claims keep both; exposure premium and the claims aggregate are in quote currency because they sum across members |
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
| **members** | `dim_life` | One row per LifeKey (a repeated LifeKey stops the run: A14) |
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

Files and parameters (stage `01c_adapter_ete.yxmd`, folder `data/input/ete/`, synthetic `data/synthetic/ete/`):
`dim_life.csv`, `fact_claims.csv`, `fact_member_month.csv` as exported by the ete pipeline, plus `run_meta.csv`
(valuation_date, listing_threshold, currency_basis, data_source_note), which the ETE pipeline does not write.
`ete_tpa_filter` (comma list, blank = all), `segment_field` (one of Policy Type, Plan / Category, TPA, Payer Name,
Master Contract ID, Currency, UWY; default Policy Type) and `premium_basis` (GROSS / GROSSNET / NET) are Params.
member_id = LifeKey; FX at the member's currency. Claim lines with `InExperience = 0` or `StatusClass = Rejected`
are excluded and counted in `recon_01c.csv`. Member months of a LifeKey with no `dim_life` row (or outside the TPA
filter) are excluded and flagged (A15).

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
| amount_paid, amount_os | number | Original currency (Paid and Reversed lines → paid; Outstanding → os; Rejected lines excluded) |
| amount_paid_q, amount_os_q | number | Quote currency at the priced rate |
| currency, fx_rate | text / number | Transaction currency and the priced rate applied |
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
| cohort_month | date | Policy inception month (RA). C mode: equal to exposure_month (exposure not split by cohort; `exposure_by_cohort` = false; stage 03 spreads written lives over 12 months for the RA view) |
| exposure_month | date | |
| earned_ly | number | Life-years earned in the month |
| written_ly | number | Booked at the cohort month (RA) |
| earned_premium, written_premium | number | Quote currency |
| lives_inforce | number | Month-average in-force lives |

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
| `tbl_completion` | Year × component × side × ap_band | year, component (`IBNR` / `UNEARNED` / `UNWRITTEN`), side (`PRE_AP` / `POST_AP_AMOUNT` / `POST_AP_COUNT` / `ATTRITIONAL` — total claims below the stop-loss large threshold, stop loss only), ap_band_from (default 0), selected_factor, indicated_factor (written back by Alteryx, read-only), justification |
| `tbl_weights` | Year | year, months_complete, use_default (1/0), custom_weight, justification |
| `tbl_layers` | Cover × option × layer | cover (`PP` / `EV` / `SL`), option_id (1–4), layer (1/2), label (O), limit_basis (`AMOUNT` / `TO_BENEFIT_LIMIT`), ap, limit, aal, aad, co_participation_pct, reinstatements_n (blank = unlimited), reinstatement_cost_pct, reinst_pro_rata_time (1/0), sl_claims_basis (`GROSS` / `NET_OF_XOL`), sl_measure (`PCT_BUDGET` / `PCT_EPI` / `AMOUNT`), sl_ap, sl_limit, active |
| `tbl_programme` | Order | step, cover (`QS` / `PP` / `EV` / `SL`; a QS step is informative, the retention is `qs_cedant_retention_pct`), option_id, active. Used by `evaluate_mode = PROGRAMME` (structure `4 PROGRAMME`) and by NET_OF_XOL stop losses; order PP → EV → SL (G12) |
| `tbl_event_rule` | Exactly one row (E01) | id_source (`SUPPLIED` / `CLUSTER`), window_days, group_key (`SCHEME` / `SITE` / `CAUSE` / `SCHEME+SITE` / `SCHEME+CAUSE`), min_lives |
| `tbl_ev_scenarios` | Scenario | scenario_name, lives, cost_per_life (quote ccy), return_period_yrs (blank = deterministic only), site. lives and cost > 0 (E02) |
| `tbl_loadings` | Cover | cover, reinsurer_expense_pct, brokerage_pct, profit_margin_pct, premium_tax_pct, risk_load_k |
| `tbl_expiring` | Cover × layer | cover, layer, ap, limit (blank = to benefit limit), rate_basis (`PPPY` / `PCT_EPI` / `FLAT`), rate, premium (FLAT), recoveries_by_year (text, `2023:120000; 2024:0`) |
| `tbl_scenarios` | Scenario × override | scenario_id (≥ 1), scenario_name, target_table (`Trend` / `Completion` / `Weights` / `Params` / `FX` / `Layers` / `EvScenarios`), target_key (`ALL` or `column=value`; Params: `param_id=x`), target_column (numeric column; Params: `value`), operation (`SET` / `MULT` / `ADD`), override_value. Checks X01–X04 |

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
| General | scenario_id (blank / 0 = best estimate; n = apply Scenarios rows with that id) | — |
| Programme | evaluate_mode (STANDALONE / PROGRAMME) | STANDALONE |
| Stochastic | iterations, seed, fit_cutoff (blank = lowest PP AP), fit_candidates, fit_selected (blank = lowest AIC), severity_source (PYTHON/NATIVE), fit_years, freq_model (POISSON/NEGBIN), ev_freq_source (EXPERIENCE/SCENARIOS/BOTH), sl_attritional_threshold, sl_cov_floor, sl_attritional_dist (LOGNORMAL/NORMAL) | 10000, 12345, POISSON, BOTH, 100000, 0.05, LOGNORMAL |
| Cedant impact | net_claims_budget (blank = best-estimate gross claims), reference_lives, return_periods (comma list), currency_display (QUOTE/USD) | 20000; "5,50,250" |

## 5. Results workbook (`excel/templates/XoL_Results.xlsx`)

Copy the template once to `data/output/<variant>/XoL_Results.xlsx`. Stage 07 replaces the data sheets in full each run
(Overwrite Sheet, one sheet at a time); the presentation sheets are formulas over them, through defined names that
find each column by its header, so a column added at the end never breaks a formula. Columns of every data sheet:
`tools/excel/results_spec.py` (single source; stage 07 and the template builder both read it). Each table is also
written as `results/<name>.csv`.

| Data sheet | Grain | Content (stage) |
|---|---|---|
| `out_params`, `out_loadings` | Run | Params and Loadings as used, after scenario overrides (00) |
| `out_run_log` | Item | Tool version, run time, scenario id and name, run meta, every parameter with its justification (07) |
| `out_warnings` | Check | stage, code, severity (STOP / FLAG / INFO), item, message, of stages 00–06 |
| `out_recon` | Stage × table × measure | Control totals of stages 01–06 |
| `out_completion_indicated` | Year × component | Indicated unearned / unwritten factors (02) |
| `out_bc_by_year`, `out_bc_summary`, `out_annualisation`, `out_large_claims` | PP option × layer (× year) | Burning cost (03) |
| `out_fit_candidates`, `out_fit_mean_excess`, `out_fit_survival` | Candidate (× AP / threshold) | Severity candidates (04a): parameters, log L, AIC, BIC, KS, ranks, boundary, selected; mean excess at the APs; survival vs Kaplan–Meier. Stage 03 empties them |
| `out_fit`, `out_sim_summary`, `out_sim_quantiles` | PP option × layer (× q) | Native Pareto diagnostics and per-person simulation (04), incl. `freq_dist`, `phi`, `sev_dist` |
| `out_ev_events`, `out_ev_bc_by_year`, `out_ev_bc_summary`, `out_ev_scenarios`, `out_ev_sim_summary`, `out_ev_sim_quantiles` | Event / EV option × layer | Per-event XoL (05) |
| `out_attritional_by_year`, `out_gross_summary`, `out_exposed_lives` | Year / run / option | Attritional model, gross claims, exposed lives (06) |
| `out_struct_summary` | Structure (`0 NONE`, `1 PP k`, `3 EV k`, `2 SL k`, `4 PROGRAMME`) | Mean / SD of recovery and net claims, P(no recovery), mean reinstatement usage (PP and EV layers), SL terms, budget, EPI, the options in the structure (06) |
| `out_struct_quantiles` | Structure × q (`qm` per mille) | Net claims, reinstatement usage at that iteration, recovery quantile (06) |
| `out_expiring`, `out_asif_by_year` | Expiring row (× year) | Matched option, expiring premium at the quote year and trended; as-if recoveries and premium at the quote-year level, premium at the year's level, actual recoveries (07) |
| `scn_summary` | Scenario × structure | One block per scenario_id; a run replaces only its own block (07) |

Presentation sheets: **Inputs** (currency and unit, lives, EPI, budget, return periods, expected-loss basis, loadings
from Control with overrides), **Pricing** (premium by option, methodology §11), **Reinsurance Structures**
(Exhibit C1), **Cedant Impact** (Exhibit C2), **Expiring**, **Severity** (methodology §8.1), **Scenarios**, **README**. A loading or input change
recalculates them without a re-run. Close the workbook in Excel before running stage 07.

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
| A11 | Claim status value not mapped (Paid / Outstanding / Rejected / Reversed, with common synonyms) | STOP |
| A12 | Required date or amount blank or not ISO (row excluded) | STOP |
| A13 | Claims whose member has no member record (excluded; listed by member) | FLAG |
| A14 | ETE: LifeKey repeated in `dim_life` | STOP |
| A15 | ETE: member months without a `dim_life` row (excluded; also outside the TPA filter) | FLAG |

Matching (line mode): tier 1 on member_id + policy_id; tier 2 on member_id with treatment inside the cover period
(latest cover wins). Each adapter also writes `recon_01a.csv` / `recon_01b.csv` / `recon_01c.csv`: rows in, rejected, unmatched, out,
and amount and exposure totals, so stage 02 can reconcile to it.

Missing columns: every input is unioned with an empty template of all contract columns, so an absent optional column
arrives as Null and an absent required column raises A01 with a readable message instead of a tool error.

## 7. Severity fit checks (stage 04a) and S07

| Code | Check | Severity |
|---|---|---|
| F01 | Candidate did not converge (left out of the selection) | FLAG |
| F02 | Unknown candidate in fit_candidates, fit_selected not a converged candidate, or nothing converged | STOP |
| F03 | Lowest BIC or KS is another candidate than the selected | INFO |
| F04 | Selected candidate: fitted / empirical mean excess at an AP outside 0.8–1.25 | FLAG |
| F05 | Fewer than 30 uncensored units above u (none: STOP) | FLAG |
| F06 | Boundary fit (no proper maximum above u; not auto-selected) | INFO |
| S07 | Stage 04: severity_source = PYTHON without a grid from 04a after the last 03 | STOP |
