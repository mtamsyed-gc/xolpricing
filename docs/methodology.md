# Methodology — per-person XoL, per-event XoL, aggregate stop loss and pricing (stages 00–07)

Version 0.5 · 2026-10-14. Every formula below is implemented in `tools/alteryx/build_stage00.py`–`build_stage07.py`
and `tools/excel/build_results.py`, and independently re-implemented in `tests/test_stage00.py`–`test_stage07.py`.
Designer 2026.2 reproduces the simulator and the oracles exactly (`tests/test_designer_twin.py`).
Notation: TY = treaty year, Q = quote year, AP = attachment point, L = layer limit.

## 1. Treaty year and accumulation (stage 02)

| Item | Rule |
|---|---|
| TY of a date d | Year in which the treaty year containing d starts: `year(d) − 1[mm-dd(d) < mm-dd(treaty_start)]`. Treaty start must be the 1st of a month (E03) |
| Claim TY, RA | TY of the policy inception (cohort month) |
| Claim TY, LO | TY of the treatment date (`INCURRED`) or the payment date (`PAID`; outstanding lines take the valuation date) |
| Paid run-off | If `paid_runoff_months` is set: paid lines with paid date > TY end + run-off are excluded (E04 flags them) |
| Existing QS | All amounts × `qs_cedant_retention_pct` (1 = no QS) |
| Accumulation unit | member × TY, plus × policy when `pp_accumulation = POLICY_YEAR` (line data). Claimant data: the supplied period × TY |
| Benefit limit of a unit | Max of the unit's line limits (quote currency) |
| Years | Units outside `years_included` are excluded (reconciled in `recon_02`) |

## 2. Exposure and indicated completion factors (stage 02)

| Item | RA | LO |
|---|---|---|
| Exposure (life-years) | Written in the TY: cohorts incepting in the TY (`written_ly`) | Earned in the TY (`earned_ly`) |
| Exposure premium | Written premium of the TY's cohorts | Earned premium in the TY |
| Months elapsed m | `min(12, months from TY start to valuation month + 1)` | same |
| Indicated UNEARNED | written premium ÷ earned-to-date premium of the TY's cohorts | 1 |
| Indicated UNWRITTEN | prior TY full-year ÷ prior TY over its first m months (premium; and life-years separately) | same, on earned |
| Full-year exposure | exposure × indicated UNWRITTEN (life-years ratio) | same |

Claimant mode (no member detail): each month's new lives are earned evenly over 12 months for the RA view.
Indicated factors are **reference values** written to `out_completion_indicated.csv`; the factors applied are the
user's selections in the Control workbook (D3), whose caveat about portfolio development applies.

## 3. Claimant adjustment (stage 03)

| Step | Formula |
|---|---|
| Annual trend, year k | `t_k = w_client·client + w_market·market + w_infl·inflation + residual` (or `large_claim_trend_pct` if given) |
| Trend index | `I_k = I_{k−1} · (1 + t_k)(1 + one_off_k)`, first year = 1 |
| Trend to quote year | `T(ty) = I_Q / I_ty` |
| Pre-AP completion | `P(ty) = IBNR_PRE · UNEARNED_PRE · UNWRITTEN_PRE` (missing factor = 1, B04 flags) |
| Post-AP factors by AP band | A Completion row may carry `ap_band_from`. A layer with attachment AP uses, per year, component and side, the row with the largest `ap_band_from ≤ AP` (blank = 0). The stage-04 frequency uses the bands at u. One row per year without bands gives the same factor to every layer |
| Adjusted amount | `adj = min( amount · T(ty) · P(ty), benefit limit, policy_limit )` |

## 4. Outliers

- Population: units with `adj ≥ output_threshold_pct_min_ap × lowest active AP`, pooled over years.
- `IQR`: threshold = Q3 + k·(Q3 − Q1), with quartiles by nearest rank (rank = ⌈p·n⌉). `VALUE`: the given amount. `NONE`: no outliers.
- A unit is an outlier if `adj > threshold`. Its layer loss goes to the outlier block, weighted equally across years.
- **Caution:** on a heavy-tailed book, 1.5 × IQR flags many units (synthetic data: about 230k, 52 units). Prefer `VALUE` set by inspection of `out_large_claims.csv`.

## 5. Layer loss (per unit × active PP layer)

| Item | Formula |
|---|---|
| Limit | `L` (AMOUNT) or `benefit limit − AP` (TO_BENEFIT_LIMIT; no limit if the benefit limit is unknown) |
| Layer loss | `xl = max(0, min(L, adj − AP)) · (1 − co-participation)` |
| Post-AP completion | `xl_c = xl · IBNR_AMT · UNEARNED_AMT · UNWRITTEN_AMT`; `n_c = 1[xl > 0] · IBNR_CNT · UNEARNED_CNT · UNWRITTEN_CNT` |
| AAD / AAL | On each TY's total: `xl_aa = min(max(xl_c − AAD, 0), AAL)`, split pro rata between non-outlier and outlier. AAL = given value, else (1 + reinstatements) × limit when reinstatements are limited. Deterministic approximation only (B03): the stochastic result prices these |

## 6. Burning cost by TY and in total

| Item | Formula |
|---|---|
| Frequency per 1,000 | `n_c / full-year exposure × 1000` |
| Severity | `xl_c / n_c` |
| BC per life (pppy) | `xl_aa / full-year exposure`, split non-outlier / outlier |
| BC % premium | `xl_aa / (full-year premium × premium trend to Q)` |
| Default weights (D5) | By years available, oldest → newest: 5 yrs 10/15/20/30/25%; 4 yrs 15/20/35/30%; 3 yrs 25/40/35%; 2 yrs 50/50%; then × months complete / 12 and renormalised |
| Custom weights | Used for all years when any year has `use_default = 0` (must sum to 1: T03) |
| Weighted BC | `Σ w_y·BC_non,y + Σ (1/n)·BC_out,y` |
| Credibility | `Z = min(1, √(n_xl / full_cred_n))`, n_xl = units reaching the layer before post-AP factors, all years (B02 flags Z < 1) |
| Sensitivities | straight-mean BC; weighted frequency × pooled severity; straight-mean frequency × pooled severity; min and max BC |
| Leveraged trend | `Σ xl(trended to Q) / Σ xl(trended to Q − 1) − 1`; leverage factor = leveraged ÷ FGU trend of Q |
| Lives in Q | `expected_lives_quote_year`, else EPI ÷ (premium per life of the latest TY × premium trend) |
| Expected cost (deterministic) | `BC_total × lives_Q × incidence_adj`; also `% premium BC × EPI × incidence_adj` as a cross-check |

The credibility blend with the complement (stochastic, benchmark or expiring) and the loadings are applied after
the stochastic stage (scope §7).

## 7. Outputs (data/output/<variant>/results)

`out_bc_by_year.csv`, `out_bc_summary.csv`, `out_large_claims.csv`, `out_annualisation.csv` (reported → + IBNR →
+ unearned → + unwritten, for layer amount and count), `checks/out_completion_indicated.csv`.

## 8. Stochastic model (stage 04, native Alteryx)

| Item | Rule |
|---|---|
| Fitting threshold u | `fit_cutoff`, else the lowest active per-person AP (post-AP completion bands at u, §3) |
| Fitting data | Adjusted units (stage 03: trended, pre-AP completed, capped) with `adj > u`, all experience years |
| Severity | Single-parameter Pareto above u: `S(x) = (u/x)^α`. MLE with right-censoring at each unit's cap (a unit is censored when its uncapped amount reaches its cap): `α = n_uncensored / Σ ln(adj/u)`, standard error `α/√n_uncensored` |
| Fit diagnostics | `out_fit.csv`: empirical vs fitted exceedances at every AP, exhaustion point and 250k/500k/1m/2m; check S05 flags a layer whose deterministic / stochastic ratio (below the largest observed claim) is outside 0.8–1.25 |
| Frequency | `λ = Σ_ty w_ty · (units > u in ty × POST_AP_COUNT(ty)) / full-year exposure_ty × lives_Q × incidence_adj`, weights as in §6 |
| Dispersion | Pearson `φ = Σ_ty (n_ty − e_ty·n/e)² / (e_ty·n/e) / (k − 1)` over the k experience years (n = counts above u, e = full-year exposure); φ = 1 with one year. Reported as `phi` |
| Count distribution | `freq_model = POISSON`: Poisson(λ). `NEGBIN` and φ > 1: negative binomial with mean λ and variance φ·λ (`r = λ/(φ − 1)`, `p = 1/φ`); NEGBIN with φ ≤ 1 falls back to Poisson (S03, INFO). `freq_dist` in the outputs says which was used |
| Claim count | Inverse CDF: `N = #{k : CDF(k) < U}` from a pmf table k = 0…⌈λ + 10√(φλ) + 20⌉ (λ > 700 stops: S02) |
| Claim | X from the severity grid of §8.1 (`severity_source = PYTHON`: the distribution selected in stage 04a; `NATIVE`: the Pareto above, for which the grid reproduces `X = u · U^(−1/α)` exactly); cap drawn uniformly from the fitted units' caps (benefit / policy limit, preserving the plan mix); `claim = min(X, cap)` |
| Layer loss | As §5 on each simulated claim (TO_BENEFIT_LIMIT uses the drawn cap); annual `recovery = min(max(Σ xl − AAD, 0), AAL)`, AAL as in §5 |
| Reinstatements | Usage per iteration `r = cost% · min(recovery, n·L)/L` (unlimited: `recovery/L`), as a fraction of the upfront premium P. Excel solves P from the target: `P = target / (1 + E[r])`. Pro rata as to time not modelled (S06) |
| Unused capacity | Each claim is also limited to M = largest observed adjusted claim; `recovery_aboveM` is the part of the layer the experience cannot see |
| Blend | `E[L] = Z · E_det + (1 − Z) · E_stoch(below M) + E_stoch(above M)` (Z, E_det from stage 03) |
| RNG | MINSTD (Lehmer, a = 48271, m = 2³¹ − 1), state `s0 = 1 + seed mod (m − 1)`; three non-overlapping streams started at `s0 · a^(j·2²⁹) mod m` (j = 1 Poisson draws, 2 severity, 3 cap), computed with 16-bit splitting so every product stays below 2⁵³. The same seed gives identical results in Designer and the simulator |
| Outputs | `out_sim_summary.csv` (λ, α, mean, SD, CoV, P(0), P50–P99.5, above-M, reinstatement usage, blend), `out_sim_quantiles.csv` (q = 0.001, 0.005 … 0.995, 0.999, nearest rank), `out_sim_iterations.csv` (per iteration and layer: recovery, above-M, reinstatement usage, large-claim gross), `out_fit.csv` |

Independence of severity and cap is an approximation (higher-limit plans may also have higher severities).

### 8.1 Severity candidates (stage 04a, Python tool)

| Item | Rule |
|---|---|
| Data | As §8: units above u, censored at their cap when the uncapped amount reaches it; the largest observed claim M |
| Likelihood | Conditional on X > u (left truncation): `log L = Σ_uncensored log f(x) + Σ_censored log S(c) − n log S(u)` |
| Candidates | `fit_candidates`: LOGNORMAL (μ, σ), GAMMA (shape, scale), WEIBULL (shape, scale), PARETO (α, scale u: the closed form of §8), GPD (ξ, σ on x − u). Nelder–Mead from three starting points, polished by a restart |
| Boundary fits | An optimum at an extreme of the parameter range (\|log parameter\| > 30, or ξ at −0.9 / 5) has no proper maximum above u: the candidate imitates another shape there (typically a power law). Flagged (F06), never selected automatically |
| Statistics | AIC = 2k − 2 log L; BIC = k ln n − 2 log L; KS = largest distance between the fitted conditional survival and the Kaplan–Meier estimate (censoring respected); mean excess at each AP, fitted `E[min(X, cap, M) − AP \| X > AP]` over the units' caps against the data (F04 outside 0.8–1.25); survival at the diagnostic thresholds of §8 |
| Selection | `fit_selected` when given (justify it), else the lowest AIC among converged, non-boundary candidates. F03 notes when BIC or KS rank another candidate first |
| Grid | The selected conditional survival is inverted at `s_k = 10^(−k/50)`, k = 0 … 500 (down to 10⁻¹⁰). Stage 04: `t = −50 log10 U`, `k = min(⌊t⌋, 499)`, `ln X = lx0_k + (t − k)(lx1_k − lx0_k)` (log-linear in the survival; exact for a Pareto; beyond 10⁻¹⁰ the last segment is extended) |
| Staleness | Stage 03 empties the fit; stage 04 stops (S07) when `severity_source = PYTHON` and no grid of the current burning cost exists |
| Outputs | `out_fit_candidates.csv`, `out_fit_mean_excess.csv`, `out_fit_survival.csv`, `out_sev_table.csv`; Results workbook sheet Severity |

Environment: Designer's base Python environment (Designer 2026.2: Python 3.13, numpy 2.1, pandas 2.3, scipy 1.16).
On the synthetic book (Pareto by construction) Pareto is selected; lognormal, gamma and Weibull end as boundary fits.

## 9. Aggregate stop loss, programme and cedant-impact inputs (stage 06, native Alteryx)

| Item | Rule |
|---|---|
| Large / attritional split | `t_sl = max(sl_attritional_threshold, u)`. Large = accumulation units with adjusted amount > t_sl; they come from the stage-04 simulation (sum of simulated claims > t_sl per iteration). Attritional = everything else (G02 flags a threshold below u) |
| Attritional history | `attritional(ty) = total claims(ty) − raw amount of large units(ty)`. Total: the accumulation units (line data) or the monthly aggregate (claimant data, × QS retention; TY from cohort month (RA) or incurred month (LO); G05 flags a PAID trigger). G01 stops if large units exceed the total |
| Adjustment | × FGU trend to Q (the trend components, **not** `large_claim_trend_pct`) × attritional completion `IBNR · UNEARNED · UNWRITTEN` from the Completion sheet, side `ATTRITIONAL` (aggregate development of total claims; missing factor = 1, G11 flags an incomplete year with none) |
| Cost per life | `a(ty) = adjusted attritional / full-year exposure` (stage 02) |
| Mean | `μ = Σ w_ty · a(ty) × lives_Q × incidence_adj` (weights of §6) |
| Volatility | `CoV = max(sample CoV of a(ty), sl_cov_floor)` (G03 reports when the floor or a short history applies). Year-to-year variation of the trended, completed cost per life: it carries parameter and systemic variation, which dominates a portfolio's attritional claims |
| Distribution | `sl_attritional_dist`: LOGNORMAL (default; σ² = ln(1 + CoV²), m = ln μ − σ²/2) or NORMAL (truncated at 0) |
| Draws | Standard normal by Box–Muller, `z = √(−2 ln U₄) · cos(2π U₅)`, from two further MINSTD streams started at offsets 2²⁸ and 3·2²⁷ (below stream 1 at 2²⁹, so no overlap) |
| Gross claims | `G = attritional + large above t_sl`, per iteration. Attritional and large are independent |
| Net claims budget | `net_claims_budget`, else the mean simulated gross (so the average-year surplus without RI is 0) |
| SL attachment / limit | `sl_measure`: PCT_BUDGET × budget, PCT_EPI × EPI, AMOUNT as entered (blank limit = unlimited) |
| SL base claims | `sl_claims_basis`: GROSS = G; NET_OF_XOL = G − recoveries of the Programme sheet's PP option − recoveries of its EV option, the EV recoveries computed on event losses net of that PP option (G04 stops if the programme has neither) |
| SL recovery | `min(max(base − AP, 0), L) × (1 − co-participation)`; one layer per option (G10 flags a layer 2) |
| Structures | `0 NONE` (without RI); `1 PP k` (net = G − PP recoveries); `3 EV k` (net = G − EV recoveries, gross events); `2 SL k` (net = base − SL recovery, with the programme's XoLs when NET_OF_XOL); `4 PROGRAMME` when `evaluate_mode = PROGRAMME`: the Programme sheet's options in order PP → EV (net of PP) → SL (net of both), net = G − all recoveries. Columns `pp_option`, `ev_option`, `sl_option` name the options in each structure (G12 stops on a programme out of order) |
| Quantiles | Per structure, q = 0.001 … 0.999 (nearest rank, `rank = ⌈q·n⌉` computed as ⌈q_milli·n/1000⌉, `qm` = q in per mille): net claims, the reinstatement usage of the iteration at that rank (`ru_l1/2_at_q` for the PP layers, `ru_ev1/2_at_q` for the EV layers), and the cover's own recovery |
| Exhibit C2 inputs | Mean net claims and mean reinstatement usage per layer (average year); net-claims quantiles with the usage of the iteration at that rank (return periods). The surplus itself is computed in the Results workbook (§11) |
| Exposed lives | PP: share of the latest treaty year's member days whose benefit limit > AP (unknown limit counts as exposed), × lives_Q. EV and SL: all lives. Claimant data has no member limits: share 1 (G07) |
| Outputs | `out_attritional_by_year.csv`, `out_gross_summary.csv`, `out_gross_iterations.csv`, `out_sl_iterations.csv`, `out_struct_summary.csv`, `out_struct_quantiles.csv`, `out_exposed_lives.csv` |

Limitations: the large and attritional parts are independent; the attritional CoV rests on 2–5 annual points
(hence the floor); claims between u and t_sl that the per-person simulation sees also sit in the attritional history,
so with NET_OF_XOL and an AP below t_sl the PP recoveries on those claims are drawn independently of the attritional
amount (set t_sl at or below the lowest AP to avoid it). A stop loss needs the stage-04 simulation, so a PP layer or a
`fit_cutoff` must be present.

## 10. Per-event XoL (stage 05, native Alteryx)

| Item | Rule |
|---|---|
| Events, SUPPLIED | Claim lines carrying an `event_id`: one event per id |
| Events, CLUSTER | SUPPLIED events plus accident-coded lines (`cause_code` given, no `event_id`) grouped by the EventRule `group_key` (SCHEME, SITE, CAUSE, SCHEME+SITE or SCHEME+CAUSE) and cause; a new event starts when the admission date (else treatment date) is more than `window_days` after the current cluster's first date. Any event needs `min_lives` distinct lives. Treaty year = that of the event's first date (RA or LO) |
| Per-life cost | Σ the life's event lines × QS retention × large-claim trend to Q, capped at the benefit / policy limit. Event loss = Σ over its lives. For the programme view also each life's cost net of the Programme sheet's PP option (through that option's layers) |
| Layer loss | `xl = max(0, min(L, loss − AP)) · (1 − co-participation)`, AAD / AAL on each treaty year's total as §5 |
| Burning cost | Per treaty year: recovery / full-year exposure (stage 02), weights of §6; `E_det = BC × lives_Q × incidence_adj`; Z = min(1, √(events hitting the layer / full_cred_n)) |
| Frequency, experience | Poisson, `λ = Σ w_ty · events_ty / exposure_ty × lives_Q × incidence_adj` |
| Frequency, scenarios | Each EvScenarios row: Poisson(1 / return period); size = lives × cost per life. `ev_freq_source`: EXPERIENCE, SCENARIOS or BOTH (default) |
| Size, experience | Lives drawn from the historical events' life counts; each life's cost drawn from the pooled historical per-life costs (both trended and capped) |
| Annual result | Per iteration: Σ layer losses of all events, AAD / AAL; reinstatement usage as §8. Also net of the programme's PP option (`recovery_net`, `reinst_usage_net`), used by stage 06 |
| RNG | MINSTD streams at offsets 2²⁴ (experience counts), 2²⁵ (lives), 2²⁶ (per-life costs), 2²⁷ (scenario counts): below the stop-loss (2²⁸…) and per-person (2²⁹…) streams |
| Blend | `E[L] = Z · E_det + (1 − Z) · E_stoch` |
| Outputs | `out_ev_events.csv`, `out_ev_bc_by_year.csv`, `out_ev_bc_summary.csv`, `out_ev_scenarios.csv` (each scenario's layer loss, gross and net of PP), `out_ev_sim_summary.csv`, `out_ev_sim_quantiles.csv`, `out_ev_iterations.csv` |

Limitations: experience events are few (synthetic data: three), so the experience λ and the empirical size
distribution are thin; the scenarios carry the tail. Per-life costs are resampled independently of the event size.

## 11. Pricing and exhibits (Results workbook, stage 07)

Stage 07 writes every result table into the data sheets of `XoL_Results.xlsx`; the presentation sheets are formulas
over those sheets (defined names look columns up by header), so the workbook recalculates when an input changes.

| Item | Rule |
|---|---|
| Expected loss used | Inputs sheet, expected-loss basis: DETERMINISTIC, STOCHASTIC or BLENDED (default) per option, summed over its layers. Stop loss: stochastic only |
| Loadings | Control Loadings by cover (reinsurer expenses, brokerage, profit, premium tax, risk load k), overridable on the Inputs sheet |
| Target premium | `P_target = (E[L] + k · SD) / (1 − Σ loadings)`, SD = SD of the option's total annual recovery |
| Upfront premium | `P = P_target / (1 + E[r])`, E[r] = layer reinstatement usage weighted by layer expected recovery (§8); `E[reinstatement premium] = P · E[r]`. Stop loss: no reinstatements |
| Layer split | P split between layers 1 and 2 pro rata to their simulated expected recoveries (used for reinstatement premium by layer) |
| Rates | per life = P / lives_Q; % EPI = P / EPI; per exposed life = P / exposed lives (§9); per reference lives = per exposed life × reference lives |
| C1 (Reinsurance Structures) | Per option: exposed lives, premium, premium per exposed life and per reference lives, expected recoveries, expected cost of cover = P + E[reinstatement premium] − E[L], "recoveries exceed premium 1 in N years" = 1 / share of the 999 recovery quantiles above P |
| C2 (Cedant Impact) | Per structure: surplus = budget − (net claims + Σ upfront premiums + Σ_layers P_layer · reinstatement usage). Average year: mean net claims and mean usage. Return period N: net claims at q = 1 − 1/N with the usage of that iteration. Display in the chosen currency and unit |
| Expiring | Expiring row matched to the PP option with the same layer, AP and limit. Expiring premium at the quote year: PPPY rate × lives_Q; PCT_EPI rate × EPI; FLAT the premium; trended by (1 + the matched option's leveraged trend, else base trend), compared with the indicated upfront premium |
| As-if by year | Matched option's layer loss after AAD / AAL (`xl_aa`, trended to Q) against the expiring premium on that year's exposure, also at the quote-year level: PPPY rate × (1 + lev) × exposure; PCT_EPI rate × year's premium × premium trend; FLAT premium × (1 + lev) × exposure / lives_Q. Actual recoveries (`recoveries_by_year`, untrended) against the same premium at the year's own level (÷ (1 + lev)^(Q − ty); PCT_EPI: rate × year's premium) |
| Scenarios sheet | `scn_summary` keeps one block of rows per `scenario_id`: a run replaces its own scenario's rows and keeps the others, so base and sensitivities sit side by side |
| Run log / warnings | `out_run_log` (tool version, run time, scenario, data mode, every parameter with its justification), `out_warnings` (all checks of stages 00–06), `out_recon` |

## 12. Scenario overrides (stage 00)

With `scenario_id` > 0 in Params, each Scenarios row of that id changes one numeric column of its target table
(Trend, Completion, Weights, Params, FX, Layers, EvScenarios) in the rows matching `target_key` (`ALL` or
`column=value`; for Params `param_id=x`, column `value`). Order: SET replaces, then MULT multiplies (product of the
MULT rows), then ADD adds; a blank cell stays blank unless SET or ADD applies. Params are overridden before
validation. `checks/out_scenario_applied.csv` lists the rows each override matched (X04 flags none).

## 13. Not in this version

Dependence between large and attritional claims, severity depending on the plan limit, pro rata as to time
reinstatements, a correlation between event size and per-life cost, the Anderson–Darling statistic (not defined
under censoring as used here).
