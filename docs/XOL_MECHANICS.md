# Mechanics of the XoL calculations

XoL pricing tool 0.6.1 · 2026-10-28. This document follows one claim from the cedant's file to the reinsurance premium
and shows every calculation on the way, in the order the tool performs it. It is the "how" behind `docs/methodology.md`
(the formula reference, organised by stage) and `docs/data-contracts.md` (the columns). Everything here is what the
code does: `tools/alteryx/build_stage00.py` … `build_stage07.py` and `tools/excel/build_results.py`, each re-implemented
independently in `tests/` and reproduced on the Alteryx engine (`tests/test_designer_twin.py`).

Notation: TY treaty year · Q quote year (the year being priced) · AP attachment point · L layer limit ·
u severity fitting threshold · lives_Q expected lives in Q · EPI expected premium income in Q.
All money is converted to the quote currency at the Control FX rates at intake; the formulas below are in that currency.

---

## 0. The pipeline in one picture

```
 cedant files ──► 01  adapter        one internal schema: members, exposure, claim lines
                   │                 (line mode L, claimant mode C, Merit mode E)
                   ▼
                  02  experience     treaty year of every claim and every unit of exposure;
                   │                 accumulation units (member × TY); indicated completion factors
                   ▼
                  03  burning cost   trend + pre-AP completion + capping per unit ──► layer loss per unit
                   │                 ──► post-AP completion ──► AAD/AAL ──► BC per year ──► weighted BC,
                   │                 credibility Z, deterministic expected loss E_det
                   ▼
                  04a severity fit   candidate distributions above u (Python); the selected survival as a grid
                  04  simulation     N ~ Poisson/NegBin, X ~ severity, cap; annual recoveries per layer;
                   │                 quantiles, reinstatement usage, blend E[L] = Z·E_det + (1−Z)·E_stoch
                  05  per-event      events from claim lines or scenarios; same layer mechanics on event losses
                  06  stop loss      attritional + large → gross annual claims; recoveries of each structure
                   ▼
                  07  results        premium = (E[L] + k·SD) / (1 − loadings) / (1 + E[reinstatement usage]);
                                     rates, exhibits C1 (structures) and C2 (cedant impact), as-if years
```

Two principles run through every stage. **Nothing is pre-aggregated:** every number is rebuilt from the unit level
(member × treaty year, or simulated claim) so that each layer, limit and co-participation is applied to the right
amount. **Every factor is explicit:** trend, completion, incidence and credibility are parameters in the Control
workbook with a justification column, and each run writes them to `out_run_log`.

---

## 1. Putting claims and exposure into treaty years (stage 02)

### 1.1 Treaty year of a date

The treaty year starting on `treaty_start` (must be the 1st of a month) in calendar year y covers
[treaty_start of y, treaty_start of y+1). For a date d:

    TY(d) = year(d) − 1[ mm-dd(d) < mm-dd(treaty_start) ]

With a 1 January treaty start, TY is simply the calendar year.

### 1.2 Which date decides a claim's treaty year

| Basis (`attachment_basis`) | Claim belongs to | Exposure measured as |
|---|---|---|
| RA (risks attaching) | TY of the **policy inception** (cohort month of the member's policy) | **written** life-years and written premium of the policies incepting in the TY |
| LO (losses occurring), trigger INCURRED | TY of the **treatment date** | **earned** life-years and earned premium in the TY |
| LO, trigger PAID | TY of the **payment date** (outstanding lines take the valuation date) | earned, as above |

Under RA a member who incepts on 1 November 2024 and is treated in March 2025 is a 2024 claim; under LO/INCURRED the
same claim is a 2025 claim. The exposure definition always matches the claim definition, so a burning cost is a ratio
of like to like.

`paid_runoff_months`, when set, excludes paid lines settled more than that many months after the TY end (E04 flags
them): this mirrors a treaty that counts only payments within a run-off window.

### 1.3 Existing quota share

If the cedant already cedes a quota share, every amount is multiplied by `qs_cedant_retention_pct` before anything
else: the XoL sits on the cedant's net retained account.

### 1.4 Accumulation units

A per-person XoL responds to the total of one life's claims over an accumulation period, not to single invoices. The
tool therefore sums claim lines into **units**:

    unit = member × TY                (default)
    unit = member × policy × TY       (pp_accumulation = POLICY_YEAR, line data)
    unit = the supplied period × TY   (claimant data: the cedant has already accumulated)

For each unit: `amount` = Σ paid + Σ outstanding (reversals net within their `claim_id`; rejected lines are
dropped), `n_lines`, the first treatment date, and the **benefit limit** = the largest annual limit among its lines
(from `annual_limit`, else the plan's limit in the Control Limits sheet; unknown = no cap).

Units whose TY is outside `years_included` are dropped and reconciled in `recon_02` (rows and amounts in = out +
excluded).

### 1.5 Exposure and indicated completion factors

For each TY the tool measures exposure in life-years (LY) and premium, on the basis of §1.2, and how much of the
year is in the data:

    m = min(12, months from TY start to the valuation month, inclusive)

| Indicated factor | RA | LO |
|---|---|---|
| UNEARNED (claims still to emerge on cover already written) | written premium ÷ premium earned to date, for the TY's cohorts | 1 (earned exposure needs none) |
| UNWRITTEN (cover still to be written in an incomplete year) | prior TY's full year ÷ prior TY over its first m months — premium ratio and LY ratio separately | same, on earned measures |
| Full-year exposure | LY × UNWRITTEN (LY ratio) | same |

These are **reference values** (`out_completion_indicated.csv`). The factors actually applied are the user's entries on
the Completion sheet: the tool never silently uses its own indication, because the ratio of a partial year to the
prior year carries the prior year's growth as well as its seasonality.

---

## 2. Bringing every unit to the quote year (stage 03)

A 2023 claim of 220,000 is not a 2027 claim of 220,000. Three adjustments, in this order, produce the unit's
**adjusted amount** `adj`:

### 2.1 Trend

The Trend sheet gives, per year k, the components and their weights:

    t_k = w_client·client_k + w_market·market_k + w_infl·inflation_k + residual_k
    (or large_claim_trend_pct, a single rate, when it is given)

    I_k  = I_{k−1} · (1 + t_k) · (1 + one_off_k),   I_first = 1
    T(ty) = I_Q / I_ty                                 trend factor from treaty year ty to the quote year

The one-off column takes step changes (a tariff change, a regulatory benefit extension) that are not part of the
run rate.

### 2.2 Pre-AP completion

The latest years are incomplete: claims are still being reported (IBNR), cover already written is still earning
(UNEARNED, RA only) and cover is still to be written (UNWRITTEN). Applied **to the amount of each unit** before the
layer is cut — "pre-AP" — they say how large the unit will eventually be:

    P(ty) = IBNR_PRE(ty) · UNEARNED_PRE(ty) · UNWRITTEN_PRE(ty)      missing factor = 1, B04 flags it

### 2.3 Capping

    adj = min( amount · T(ty) · P(ty),  benefit limit of the unit,  policy_limit )

Trend and completion can push a unit past what the policy would ever pay; the cap stops that. The uncapped value is
kept as `adj_uncapped`: the severity fit (§4) needs to know when a unit is censored at its cap.

### 2.4 Outliers

Among units with `adj ≥ output_threshold_pct_min_ap × lowest AP`, pooled over all years:

    IQR:   threshold = Q3 + k·(Q3 − Q1), quartiles by nearest rank (rank = ⌈p·n⌉)
    VALUE: threshold = the amount given
    NONE:  no outliers

A unit with `adj > threshold` is an outlier. Its layer loss is **not removed**: it is carried in a separate block
spread equally over the years (§3.4), so one catastrophic 2024 claimant does not make 2024 the worst year by
construction. On a heavy-tailed book 1.5 × IQR flags many units (52 on the synthetic book); `VALUE`, set after
reading `out_large_claims.csv`, is the recommended setting.

---

## 3. From adjusted units to the burning cost (stage 03)

### 3.1 Layer loss of a unit

For each active per-person layer (Layers sheet: `ap`, `limit`, `co_participation_pct`, `aad`, `aal`,
`reinstatements_n`, `reinstatement_cost_pct`):

    L  = limit                               (limit_basis AMOUNT)
       = benefit limit − AP                  (TO_BENEFIT_LIMIT; a unit with no known limit has no L)
    xl = max(0, min(L, adj − AP)) · (1 − co-participation)

The cedant's co-participation in the layer is taken off the layer loss, not off the attachment: a 10% co-participation
on a 250,000 xs 150,000 layer leaves the reinsurer 90% of every loss between 150,000 and 400,000.

### 3.2 Post-AP completion

The pre-AP factors made each known unit larger. They cannot create units that have not been reported, nor the part of
development that only enters the layer later (a unit at 140,000 today that will finish at 180,000 is invisible to the
layer now). The Completion sheet's **post-AP** factors do that, on the layer loss and on the count separately:

    xl_c = xl · IBNR_AMT · UNEARNED_AMT · UNWRITTEN_AMT
    n_c  = 1[xl > 0] · IBNR_CNT · UNEARNED_CNT · UNWRITTEN_CNT

Post-AP development depends on the attachment point: a high layer develops more (a larger share of its eventual
claims is below the AP at the valuation date). A Completion row may therefore carry `ap_band_from`; a layer uses, per
year, component and side, the row with the largest `ap_band_from ≤ AP`. One row per year without bands applies the
same factor to every layer.

### 3.3 Annual aggregate deductible and limit

AAD and AAL act on a **treaty year's total**, not on units:

    AAL = aal if given, else (1 + reinstatements_n) × L if reinstatements are limited, else unlimited
    xl_aa(ty) = min( max( Σ_units xl_c − AAD, 0 ), AAL )

and the result is split pro rata between the non-outlier and outlier blocks. This is a deterministic approximation
(B03 says so in the output): the annual features are priced properly by the simulation of §5, which sees the
distribution of the annual total rather than one historical value per year.

### 3.4 Burning cost by treaty year

With `exposure_full(ty)` = full-year life-years and `premium_full(ty)` = full-year premium (§1.5):

| Measure | Formula |
|---|---|
| Frequency per 1,000 lives | `n_c / exposure_full × 1000` |
| Severity (layer) | `xl_c / n_c` |
| BC per life (pppy) | `xl_aa / exposure_full`, as `bc_pppy_non` + `bc_pppy_out` |
| BC % of premium | `xl_aa / (premium_full × premium trend to Q)` — premium is trended with the Trend sheet's premium column so the ratio is at Q level |

### 3.5 Weighting the years

Default weights (D5), oldest → newest, by the number of years with data:

    5 years: 10 / 15 / 20 / 30 / 25 %      4 years: 15 / 20 / 35 / 30 %
    3 years: 25 / 40 / 35 %                2 years: 50 / 50 %

each multiplied by `months complete / 12` and renormalised, so a year that is three months in carries a quarter of
its nominal weight. Custom weights on the Weights sheet replace the defaults for all years as soon as one year has
`use_default = 0` (they must sum to 1: T03).

    BC_w = Σ_y w_y · BC_non,y  +  Σ_y (1/n_years) · BC_out,y

The outlier block is averaged flatly: the one big claimant is a fact about the book, not about the year it fell in.

### 3.6 Credibility

    n_xl = number of units reaching the layer (xl > 0) before post-AP factors, all years
    Z    = min(1, √(n_xl / full_cred_n))                      full_cred_n: Control (workshop default 64)

Z < 1 is flagged (B02); the complement is the stochastic result (§5.7), a benchmark or the expiring rate
(`complement_source`).

### 3.7 Leveraged trend

A layer trends faster than the ground-up claims: a 5% rise on a 180,000 claim is a 15% rise on its 30,000 excess over
150,000. The tool measures it on the data rather than assuming a formula:

    leveraged trend = Σ xl(units trended to Q) / Σ xl(units trended to Q − 1) − 1
    leverage factor = leveraged trend / FGU trend of Q

It is used for the expiring-premium comparison (§7.4) and reported for the reinsurer discussion.

### 3.8 Deterministic expected loss

    lives_Q = expected_lives_quote_year, else EPI / (premium per life of the latest TY × premium trend)
    E_det   = BC_w × lives_Q × incidence_adj
    (cross-check: BC % premium × EPI × incidence_adj)

`incidence_adj` is the single, justified adjustment for anything the data does not show (a benefit change, a
network change): 1 means none. Sensitivities written alongside: straight-mean BC, weighted frequency × pooled
severity, straight-mean frequency × pooled severity, min and max year.

---

## 4. Severity above the threshold (stages 04 and 04a)

The burning cost can only see claims that have happened; a 250,000 xs 150,000 layer on five years of data has a few
dozen units in it and nothing at all above the largest one. The stochastic model fits a severity distribution to the
tail and simulates.

### 4.1 Data

    u = fit_cutoff, else the lowest active per-person AP
    fitting set = adjusted units (trended, pre-AP completed, capped) with adj > u, all years
    a unit is censored when adj_uncapped ≥ its cap: the true claim is at least the cap, and that is all we know
    M = largest observed adjusted claim

### 4.2 Native Pareto (stage 04, `severity_source = NATIVE`)

A single-parameter Pareto above u, `S(x) = P(X > x | X > u) = (u / x)^α`, fitted by maximum likelihood with right
censoring:

    α  = n_uncensored / Σ_all units ln(adj / u)          s.e. = α / √n_uncensored

(censored units contribute to the denominator — their claim is at least the cap — but not to the numerator). The
closed form means the fit is reproducible by hand. `out_fit.csv` compares empirical and fitted exceedances at every
AP, at each layer's exhaustion point and at 250k / 500k / 1m / 2m; S05 flags a layer whose deterministic and
stochastic expected losses, compared below M, differ by more than 0.8–1.25×.

### 4.3 Candidate distributions (stage 04a, Python tool, `severity_source = PYTHON`, the default)

The same data, five candidates, the same censored, left-truncated likelihood:

    log L = Σ_uncensored log f(x) + Σ_censored log S(cap) − n · log S(u)

| Candidate | Parameters | Note |
|---|---|---|
| LOGNORMAL | μ, σ | |
| GAMMA | shape, scale | |
| WEIBULL | shape, scale | |
| PARETO | α (scale fixed at u) | closed form of §4.2 |
| GPD | ξ, σ on x − u | the extreme-value limit; ξ > 0 is heavy-tailed |

Nelder–Mead from three starting points, polished by a restart. A fit that ends at the edge of its parameter range
(|log parameter| > 30, or GPD ξ at −0.9 / 5) is a **boundary fit**: the candidate has no proper maximum above u and is
imitating another shape (typically a power law). It is flagged F06 and never selected automatically.

Selection: `fit_selected` when the user names one (with a justification), otherwise the lowest AIC among converged,
non-boundary candidates. BIC and KS (the largest gap between the fitted survival and the Kaplan–Meier estimate, which
respects censoring) are reported, and F03 notes when they rank another candidate first. The **mean excess** at each
AP — the average layer-type cost `E[min(X, cap, M) − AP | X > AP]` — is computed from the data and from each fit;
F04 flags a fit outside 0.8–1.25 of the data, which is the most direct test of whether the fit prices the layer.

### 4.4 The survival grid

The selected distribution is handed to the native simulation as a table, not as a formula, so stage 04 needs no
Python at run time:

    s_k   = 10^(−k/50),  k = 0 … 500        (survival from 1 down to 10⁻¹⁰, fifty points per decade)
    lx0_k = ln x  such that S(x) = s_k        (the conditional survival inverted)
    lx1_k = lx0_{k+1}

Drawing a claim from a uniform U:

    t = −50 · log10(U),  k = min(⌊t⌋, 499)
    ln X = lx0_k + (t − k) · (lx1_k − lx0_k)
    X = exp(ln X)

Linear in log X against log survival: exact for a Pareto (whose log-survival is linear in log x), and a 2% grid for
the others; beyond 10⁻¹⁰ the last segment is extended. Stage 03 empties the grid, so a stale fit cannot be used
against a new burning cost (S07 stops the run instead).

---

## 5. Simulating a treaty year (stage 04)

### 5.1 Frequency

    λ = Σ_ty w_ty · ( units above u in ty × POST_AP_COUNT(ty) ) / exposure_full(ty)  ×  lives_Q × incidence_adj

the same weights as §3.5, so the deterministic and stochastic views share the frequency assumption. The post-AP count
factors at u complete the recent years.

### 5.2 Dispersion

Year-to-year counts vary more than a Poisson predicts when the book itself changes. The Pearson statistic over the k
years, with n_ty the count above u and e_ty the full-year exposure,

    φ = Σ_ty (n_ty − e_ty · n/e)² / (e_ty · n/e)  /  (k − 1)         (φ = 1 with one year)

| `freq_model` | Count distribution |
|---|---|
| POISSON | Poisson(λ) |
| NEGBIN, φ > 1 | Negative binomial, mean λ, variance φ·λ: `r = λ / (φ − 1)`, `p = 1 / φ` |
| NEGBIN, φ ≤ 1 | Poisson (S03 says so) |

### 5.3 One iteration

For each of `n_iterations`:

1. **Count.** `N = #{k : CDF(k) < U₁}` from the pmf table, k = 0 … ⌈λ + 10√(φλ) + 20⌉ (λ > 700 stops: S02).
2. **Claims.** For each of the N claims: X from the grid with U₂ (§4.4); a cap drawn with U₃ uniformly from the
   fitting units' caps — this keeps the plan mix of the book without assuming that large claims come from large plans;
   `claim = min(X, cap)`.
3. **Layer loss** of each claim as §3.1 (TO_BENEFIT_LIMIT uses the drawn cap).
4. **Annual recovery** per layer: `recovery = min(max(Σ xl − AAD, 0), AAL)`.
5. **Reinstatement usage** as a fraction of the upfront premium:
   `r = reinstatement_cost_pct · min(recovery, n · L) / L` (unlimited reinstatements: `recovery / L`).
6. **Unused capacity.** The same claims limited to M, the largest observed claim: `recovery_aboveM` is the part of the
   recovery that comes from claim sizes the experience has never produced.

### 5.4 Random numbers

MINSTD (Lehmer: a = 48271, m = 2³¹ − 1), seed state `s0 = 1 + seed mod (m − 1)`. Three non-overlapping streams start
at `s0 · a^(j·2²⁹) mod m` (j = 1 counts, 2 severity, 3 caps), computed with 16-bit splitting so no intermediate product
exceeds 2⁵³ — the same arithmetic in Designer, in the simulator and in numpy, hence the same results for the same seed.
Stages 05 and 06 use further streams at lower offsets (§6).

### 5.5 Outputs

`out_sim_summary.csv`: λ, φ, the distribution used, α or the selected candidate, mean, SD, CoV, P(0), P50 … P99.5 of
the annual recovery, mean above-M recovery, mean reinstatement usage, the blend. `out_sim_quantiles.csv`: q = 0.001 …
0.999 by nearest rank. `out_sim_iterations.csv`: every iteration (the input to stage 06 and to the C1/C2 exhibits).

### 5.6 Two approximations to keep in view

Severity and cap are drawn independently (higher-limit plans may also have higher severities). Claims between
u and the stop-loss threshold (§6.2) appear both in the per-person simulation and in the attritional history.

### 5.7 Blend

    E[L] = Z · E_det  +  (1 − Z) · E_stoch(below M)  +  E_stoch(above M)

The credibility-weighted part compares like with like — both the burning cost and the simulation below M describe
the claims the experience can see — and the above-M part is added in full: no amount of experience in the layer says
anything about claims larger than any that has occurred.

---

## 6. Per-event XoL and aggregate stop loss (stages 05, 06)

Both reuse the layer mechanics of §3.1 and §3.3 on a different "unit".

### 6.1 Per-event (stage 05)

| Step | Rule |
|---|---|
| Events | Lines with an `event_id` (SUPPLIED), or clusters of accident-coded lines (CLUSTER) by the EventRule `group_key` (SCHEME, SITE, CAUSE, …) and cause, a new event when the admission (else treatment) date is more than `window_days` after the cluster's first date; at least `min_lives` lives |
| Per-life cost | Σ the life's event lines × QS retention × large-claim trend to Q, capped at its benefit / policy limit; event loss = Σ lives. Also net of the Programme's per-person option |
| Layer loss | `xl = max(0, min(L, loss − AP)) · (1 − co-participation)`, AAD / AAL per TY |
| Burning cost | recovery per TY / exposure_full, weights of §3.5; `E_det = BC × lives_Q × incidence_adj`; `Z = min(1, √(events in layer / full_cred_n))` |
| Simulation | Experience: Poisson(λ) events, each with a life count drawn from the historical events and per-life costs drawn from the pooled historical costs. Scenarios (EvScenarios sheet): each row a Poisson(1 / return period) event of `lives × cost per life`. `ev_freq_source` EXPERIENCE / SCENARIOS / BOTH |
| Blend | `E[L] = Z · E_det + (1 − Z) · E_stoch` |

### 6.2 Aggregate stop loss (stage 06)

The stop loss needs the whole year's claims, large and small:

    t_sl = max(sl_attritional_threshold, u)
    large(ty)        = units with adj > t_sl                 → simulated by stage 04 (Σ simulated claims > t_sl)
    attritional(ty)  = total claims(ty) − raw amount of the large units(ty)
    adjusted attritional(ty) = attritional(ty) × FGU trend to Q × IBNR·UNEARNED·UNWRITTEN (side ATTRITIONAL)
    a(ty) = adjusted attritional(ty) / exposure_full(ty)                       cost per life

    μ   = Σ_ty w_ty · a(ty) × lives_Q × incidence_adj
    CoV = max( sample CoV of a(ty), sl_cov_floor )
    attritional ~ LOGNORMAL(σ² = ln(1 + CoV²), m = ln μ − σ²/2)   or NORMAL truncated at 0

    G = attritional + large      per iteration, independent (Box–Muller normals from two further MINSTD streams)

Then for each structure (NONE; PP option k; EV option k; SL option k; PROGRAMME = PP → EV net of PP → SL net of
both): net claims = G − recoveries, where the stop-loss recovery is `min(max(base − AP, 0), L) × (1 − co-part.)` on
base = G (GROSS) or G − XoL recoveries (NET_OF_XOL), AP and L from `sl_measure` (% of the net claims budget, % of
EPI, or amounts). Quantiles per structure at q = 0.001 … 0.999 carry the reinstatement usage of the iteration at that
rank, which exhibit C2 needs.

---

## 7. From expected loss to premium (stage 07, Results workbook)

Stage 07 writes every table into the data sheets of `XoL_Results.xlsx`; the presentation sheets are formulas over
them, so a change on the Inputs sheet (loadings, expected-loss basis) recalculates without a re-run.

### 7.1 Expected loss

Per option, summed over its layers, on the basis chosen per option: DETERMINISTIC (E_det), STOCHASTIC (E_stoch) or
BLENDED (§5.7, the default). Stop loss: stochastic only.

### 7.2 Loadings and the target premium

Loadings by cover from the Control Loadings sheet, overridable on Inputs: reinsurer expenses, brokerage, profit,
premium tax (all % of premium) and a risk load k (× SD).

    P_target = ( E[L] + k · SD ) / ( 1 − Σ loadings )        SD = SD of the option's total annual recovery

### 7.3 Reinstatements and the upfront premium

The simulation gives E[r], the expected reinstatement usage as a fraction of the upfront premium (§5.3; layers
weighted by expected recovery). The reinsurer's total expected income is the upfront premium plus reinstatements:

    P · (1 + E[r]) = P_target     ⇒     P = P_target / (1 + E[r])
    E[reinstatement premium] = P · E[r]

P is split between layers 1 and 2 pro rata to their expected recoveries. Stop loss: no reinstatements.

### 7.4 Rates and exhibits

| Rate | Formula |
|---|---|
| per life | P / lives_Q |
| % EPI | P / EPI |
| per exposed life | P / exposed lives — PP: the share of the latest TY's member-days with benefit limit > AP (unknown = exposed) × lives_Q; EV and SL: all lives |
| per reference lives | per exposed life × reference lives |

**C1 Reinsurance Structures**, per option: exposed lives, P, rates, expected recoveries, expected cost of cover
`P + E[reinstatement premium] − E[L]`, and "recoveries exceed premium 1 in N years" = 1 / (share of the 999 recovery
quantiles above P).

**C2 Cedant Impact**, per structure: `surplus = budget − (net claims + Σ P + Σ_layers P_layer · usage)`, for the
average year (means) and at return periods N (net claims at q = 1 − 1/N with the usage of that iteration).

**Expiring**: the expiring row is matched to the PP option with the same AP and limit; its premium is restated at Q
(PPPY × lives_Q; % EPI × EPI; FLAT as given) and trended by the matched option's leveraged trend, then compared with
P. **As-if by year**: the matched option's `xl_aa` per TY (trended to Q) against the expiring premium at the
quote-year level (PPPY rate × (1 + leveraged trend) × exposure; % EPI × the year's premium × premium trend), and
actual untrended recoveries against the premium at that year's own level.

---

## 8. Worked example

One claim, one layer, then a year and a premium. Treaty start 1 January, LO / INCURRED, no quota share, quote year
2027, layer **250,000 xs 150,000**, 10% co-participation, no AAD, unlimited AAL, two reinstatements at 100%.

**Unit.** Member M1, TY 2024: lines paid 180,000 + outstanding 40,000 → `amount = 220,000`, benefit limit 1,000,000.

**Trend.** 8% a year for 2025, 2026, 2027, no one-offs: `T(2024) = 1.08³ = 1.259712`.

**Pre-AP completion** for 2024: IBNR_PRE 1.05, UNEARNED_PRE 1, UNWRITTEN_PRE 1 → `P = 1.05`.

    adj = min(220,000 × 1.259712 × 1.05, 1,000,000) = 290,993.47

**Layer loss.**

    xl   = max(0, min(250,000, 290,993.47 − 150,000)) × (1 − 0.10) = 140,993.47 × 0.9 = 126,894.12

**Post-AP completion** for 2024 at this AP: IBNR_AMT 1.10, IBNR_CNT 1.05 (the others 1):

    xl_c = 126,894.12 × 1.10 = 139,583.54          n_c = 1 × 1.05 = 1.05

**The year.** Suppose 2024's units together give `Σ xl_c = 2,400,000`, `Σ n_c = 18.9`, full-year exposure 48,000 LY,
full-year premium at Q level 96,000,000. No AAD / AAL, so `xl_aa = 2,400,000`.

    frequency  = 18.9 / 48,000 × 1,000 = 0.394 per 1,000 lives
    severity   = 2,400,000 / 18.9     = 126,984
    BC pppy    = 2,400,000 / 48,000   = 50.00
    BC % prem  = 2,400,000 / 96,000,000 = 2.50 %

**Five years, weighted.** BC pppy 2022 … 2026 = 42, 47, 55, 50, 58, all years complete, default weights:

    BC_w = 0.10×42 + 0.15×47 + 0.20×55 + 0.30×50 + 0.25×58 = 51.75

**Credibility.** 95 units reached the layer over the five years, `full_cred_n = 1,082`:

    Z = √(95 / 1,082) = 0.296

**Deterministic expected loss.** lives_Q = 50,000, incidence_adj = 1:

    E_det = 51.75 × 50,000 = 2,587,500

**Severity fit (native Pareto), u = 150,000.** Four units above u: 165,000, 180,000, 210,000 and one at its
300,000 cap (censored):

    Σ ln(adj/u) = ln 1.1 + ln 1.2 + ln 1.4 + ln 2.0 = 0.0953 + 0.1823 + 0.3365 + 0.6931 = 1.3073
    α = 3 / 1.3073 = 2.295        S(250,000) = (150/250)^2.295 = 0.310      S(400,000) = 0.105

A uniform draw U = 0.0123 gives `t = −50·log10(0.0123) = 95.5`, so the grid segment k = 95 and, for the Pareto,
`X = 150,000 × 0.0123^(−1/2.295) = 1,019,568` — above the layer's exhaustion point: this claim contributes the full
250,000 × 0.9 to the year.

**Count model.** λ = 19.7, φ = 1.8, `freq_model = NEGBIN`: negative binomial with `r = 19.7 / 0.8 = 24.6`,
`p = 1 / 1.8 = 0.556`, variance 35.5 instead of 19.7.

**Stochastic result and blend.** Simulated mean annual recovery below M = 2,700,000, above M = 150,000:

    E[L] = 0.296 × 2,587,500 + 0.704 × 2,700,000 + 150,000 = 2,816,665

**Premium.** SD of the annual recovery 1,900,000, risk load k = 0.10, loadings 5% expenses + 10% brokerage + 5%
profit = 20%, expected reinstatement usage E[r] = 0.35:

    P_target = (2,816,665 + 0.10 × 1,900,000) / (1 − 0.20) = 3,758,331
    P        = 3,758,331 / 1.35                              = 2,783,949
    E[reinstatement premium] = 2,783,949 × 0.35             =   974,382
    rate per life = 2,783,949 / 50,000 = 55.68      % EPI (EPI 120,000,000) = 2.32 %

Expected cost of cover to the cedant = 2,783,949 + 974,382 − 2,816,665 = 941,666 a year, the loadings and risk load
in money terms.

---

## 9. Checks that gate the calculation

Every stage writes `checks_<stage>.csv` with STOP (the run halts), FLAG (continue, shown in the Results Warnings
sheet) or INFO. The ones that bear directly on the mechanics above:

| Code | Stage | Condition |
|---|---|---|
| A03 | 01 | claim-to-member match rate < 95% (line data) |
| A04 | 01 | claims treated after the valuation date: STOP on line data (the file is inconsistent), FLAG and excluded on Merit data (its claims extract runs past the valuation date) |
| E03 / E04 | 02 | treaty start not the 1st of a month / paid lines outside the run-off window |
| B02 / B03 / B04 | 03 | Z < 1 / AAD-AAL approximation in use / a completion factor missing (taken as 1) |
| T03 | 00 | custom weights do not sum to 1 |
| S02 / S03 / S05 / S07 | 04 | λ > 700 / NEGBIN with φ ≤ 1 / deterministic vs stochastic outside 0.8–1.25 below M / no current severity grid |
| F02 – F06 | 04a | unknown candidate / BIC or KS prefer another / mean excess off by more than 0.8–1.25 / too few points / boundary fit |
| G01 – G12 | 06 | large units exceed total claims; t_sl below u; CoV floor applied; programme order; … |

`out_recon` reconciles rows and amounts in and out of every stage, and `out_run_log` records every parameter with its
justification — the audit trail for the number on the C1 exhibit.

---

## 10. Where each number lives

| Quantity | File (`data/output/<variant>/results/`) |
|---|---|
| Units with adj, outlier flag, layer loss | `out_large_claims.csv` |
| BC by year, annualisation steps | `out_bc_by_year.csv`, `out_annualisation.csv` |
| Weighted BC, Z, E_det, sensitivities, leveraged trend | `out_bc_summary.csv` |
| Indicated completion factors | `checks/out_completion_indicated.csv` |
| Severity candidates, mean excess, survival, grid | `out_fit_candidates.csv`, `out_fit_mean_excess.csv`, `out_fit_survival.csv`, `out_sev_table.csv` |
| Simulation | `out_sim_summary.csv`, `out_sim_quantiles.csv`, `out_sim_iterations.csv`, `out_fit.csv` |
| Per-event | `out_ev_*.csv` |
| Stop loss and structures | `out_attritional_by_year.csv`, `out_gross_*.csv`, `out_struct_*.csv`, `out_exposed_lives.csv` |
| Premium, exhibits | `XoL_Results.xlsx` — sheets Pricing, C1, C2, Expiring, Severity, Scenarios, Warnings, Run log |

Not in this version (scope §13 of the methodology): dependence between large and attritional claims, severity
depending on the plan limit, pro-rata-as-to-time reinstatements, correlation between event size and per-life cost.
