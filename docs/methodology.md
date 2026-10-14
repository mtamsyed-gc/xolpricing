# Methodology — deterministic per-person XoL (stages 02–03)

Version 0.1 · 2026-10-14. Every formula below is implemented in `tools/alteryx/build_stage02.py` /
`build_stage03.py` and independently re-implemented in `tests/test_stage02.py` / `test_stage03.py`.
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
| AAD / AAL | On each TY's total: `xl_aa = min(max(xl_c − AAD, 0), AAL)`, split pro rata between non-outlier and outlier. Deterministic approximation only (B03): the stochastic result prices these |

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

## 8. Not yet in this version

Scenario overrides (sensitivity runs), AP-band completion factors, the expiring-rate comparison, and per-event / stop
loss covers (phases 2–4).
