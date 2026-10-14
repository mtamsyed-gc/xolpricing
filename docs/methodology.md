# Methodology — per-person XoL and aggregate stop loss (stages 02–05)

Version 0.3 · 2026-10-14. Every formula below is implemented in `tools/alteryx/build_stage02.py`–`build_stage05.py`
and independently re-implemented in `tests/test_stage02.py`–`test_stage05.py`.
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
| Fitting threshold u | `fit_cutoff`, else the lowest active per-person AP |
| Fitting data | Adjusted units (stage 03: trended, pre-AP completed, capped) with `adj > u`, all experience years |
| Severity | Single-parameter Pareto above u: `S(x) = (u/x)^α`. MLE with right-censoring at each unit's cap (a unit is censored when its uncapped amount reaches its cap): `α = n_uncensored / Σ ln(adj/u)`, standard error `α/√n_uncensored` |
| Fit diagnostics | `out_fit.csv`: empirical vs fitted exceedances at every AP, exhaustion point and 250k/500k/1m/2m; check S05 flags a layer whose deterministic / stochastic ratio (below the largest observed claim) is outside 0.8–1.25 |
| Frequency | Poisson. `λ = Σ_ty w_ty · (units > u in ty × POST_AP_COUNT(ty)) / full-year exposure_ty × lives_Q × incidence_adj`, weights as in §6. NEGBIN is not available natively (S03) |
| Claim count | Inverse CDF: `N = #{k : CDF(k) < U}` from a Poisson table k = 0…⌈λ + 10√λ + 20⌉ (λ > 700 stops: S02) |
| Claim | `X = u · U^(−1/α)`; cap drawn uniformly from the fitted units' caps (benefit / policy limit, preserving the plan mix); `claim = min(X, cap)` |
| Layer loss | As §5 on each simulated claim (TO_BENEFIT_LIMIT uses the drawn cap); annual `recovery = min(max(Σ xl − AAD, 0), AAL)`, AAL as in §5 |
| Reinstatements | Usage per iteration `r = cost% · min(recovery, n·L)/L` (unlimited: `recovery/L`), as a fraction of the upfront premium P. Excel solves P from the target: `P = target / (1 + E[r])`. Pro rata as to time not modelled (S06) |
| Unused capacity | Each claim is also limited to M = largest observed adjusted claim; `recovery_aboveM` is the part of the layer the experience cannot see |
| Blend | `E[L] = Z · E_det + (1 − Z) · E_stoch(below M) + E_stoch(above M)` (Z, E_det from stage 03) |
| RNG | MINSTD (Lehmer, a = 48271, m = 2³¹ − 1), state `s0 = 1 + seed mod (m − 1)`; three non-overlapping streams started at `s0 · a^(j·2²⁹) mod m` (j = 1 Poisson draws, 2 severity, 3 cap), computed with 16-bit splitting so every product stays below 2⁵³. The same seed gives identical results in Designer and the simulator |
| Outputs | `out_sim_summary.csv` (λ, α, mean, SD, CoV, P(0), P50–P99.5, above-M, reinstatement usage, blend), `out_sim_quantiles.csv` (q = 0.001, 0.005 … 0.995, 0.999, nearest rank), `out_sim_iterations.csv` (per iteration and layer: recovery, above-M, reinstatement usage, large-claim gross), `out_fit.csv` |

Independence of severity and cap is an approximation (higher-limit plans may also have higher severities); the
lognormal / GPD candidates and negative binomial frequency need the Python tool (D6) and are not in this version.

## 9. Aggregate stop loss and cedant-impact inputs (stage 05, native Alteryx)

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
| SL base claims | `sl_claims_basis`: GROSS = G; NET_OF_XOL = G − recoveries of the per-person option named in the Programme sheet (first active PP step; G04 stops if none) |
| SL recovery | `min(max(base − AP, 0), L) × (1 − co-participation)`; one layer per option (G10 flags a layer 2) |
| Structures | Without RI; each PP option (net = G − PP recoveries); each SL option (net = base − SL recovery; with its PP option when NET_OF_XOL) |
| Quantiles | Per structure, q = 0.001 … 0.999 (nearest rank, `rank = ⌈q·n⌉` computed as ⌈q_milli·n/1000⌉): net claims, the reinstatement usage of the iteration at that rank (by layer), and the cover's own recovery |
| Exhibit C2 | Surplus at return period N = budget − premium − (net claims quantile at 1 − 1/N); average year = budget − premium − mean net. Exact for structures without reinstatement premium; with reinstatements, the reinstatement premium is taken from the iteration at the net-claims quantile (G08) |
| Exposed lives | PP: share of the latest treaty year's member days whose benefit limit > AP (unknown limit counts as exposed), × lives_Q. EV and SL: all lives. Claimant data has no member limits: share 1 (G07) |
| Outputs | `out_attritional_by_year.csv`, `out_gross_summary.csv`, `out_gross_iterations.csv`, `out_sl_iterations.csv`, `out_struct_summary.csv`, `out_struct_quantiles.csv`, `out_exposed_lives.csv` |

Limitations: the large and attritional parts are independent; the attritional CoV rests on 2–5 annual points
(hence the floor); claims between u and t_sl that the per-person simulation sees also sit in the attritional history,
so with NET_OF_XOL and an AP below t_sl the PP recoveries on those claims are drawn independently of the attritional
amount (set t_sl at or below the lowest AP to avoid it). A stop loss needs the stage-04 simulation, so a PP layer or a
`fit_cutoff` must be present.

## 10. Not yet in this version

Scenario overrides (sensitivity runs), AP-band completion factors, the expiring-rate comparison, per-event cover
(phase 4) and the Results workbook exhibits.
