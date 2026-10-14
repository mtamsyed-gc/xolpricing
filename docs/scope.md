# Scope — XoL Pricing Tool (broker view)

Status: **draft for review** · 2026-10-14
Framework: adapted from a reinsurer-side health WCXL pricing workshop (14 sections). This tool is
built for a **broker**, so the reinsurer's capital, risk-transfer and target-metric solve are dropped
and a cedant-impact module is added.

---

## 1. Purpose

For a medical (health) portfolio and one or more candidate XoL structures, produce:

| | Output | Primary question answered |
|---|---|---|
| **A** | Expected claims cost to each layer | What will this layer cost on a best-estimate basis? |
| **B** | Indicative RI premium | What should the market charge, on high-level loadings? |
| **C** | Cedant impact | Is this structure good value for the cedant (recoveries vs premium, volatility of retained claims)? |

**Out of scope:** capital (EC / local / rating-agency), risk-transfer testing (ERD), reinsurer
NBEV / distributable earnings, solving premium to reinsurer target metrics, variable-rate /
experience-refund solve macros.

## 2. Platform split

| Layer | Tool | Rule |
|---|---|---|
| Data ingestion, cleaning, FX as-if, limits, accumulation, trend, completion, layering, burning cost, weights, outliers, credibility | **Alteryx** | *All* data manipulation and preparation is done in Alteryx — no Power Query, no Excel formulas acting on claim rows |
| Distribution fitting and frequency–severity simulation | **Alteryx** (preferred) | See §6 for the tool options |
| Assumptions entry, layer options, scenario definitions | **Excel** (Control workbook) | The single source of inputs, which Alteryx reads at run start |
| Loadings → RI premium, credibility-blend selection, cedant-impact exhibits, charts | **Excel** (Results workbook) | Built from summary tables Alteryx writes out, so changing a loading **does not need a re-run** |

Design consequences:
- There is no row limit (the workshop model capped input at 5,000 rows and 10 rating factors). Alteryx takes the full claim-line data.
- Accumulation happens **before** any size threshold. All lines are summed per person per period first, and only then filtered to ≥ x% of the lowest attachment point to keep outputs small. The workshop's "input claims > 50% of AP" rule therefore becomes an output filter, not an input constraint.
- Sensitivities run as a **scenario dimension** in a single Alteryx run (one row per scenario in the Control workbook), not as macros that overwrite and restore best-estimate cells. This removes the workshop's "assumptions not reverted" warning risk.

## 3. Workshop framework → broker treatment

| # | Workshop section | Treatment | Where | Broker adaptation |
|---|---|---|---|---|
| 1 | What the model does | **Adapt** | — | Per-person (specific) XoL first. Per-event and aggregate stop loss are later phases (decision D1) |
| 2 | Parameters & data inputs | **Keep** | Alteryx | Exposure on an RA or LO basis; claimant-level claims with rating factors; FX as-if; benefit/policy limits; segment flags; mix-risk check |
| 3 | Completion (IBNR/P, accumulation to YE, new unknown claims) | **Keep** | Alteryx | Two multipliers per year: pre-AP (existing claimers) and post-AP (new claimers, applied to XL amount and count) |
| 4 | Trend (claims and premium) | **Keep** | Alteryx | FGU trend vector; leveraged trend reported as a diagnostic; premium trend for % GWP quoting |
| 5 | Weighted average past experience | **Keep** | Alteryx | Default weight table plus custom weights with a mandatory justification field |
| 6 | Outliers | **Keep** | Alteryx | Threshold on the fully adjusted claim; outlier excess spread equally across years |
| 7 | Credibility | **Keep** | Alteryx → Excel | Z = √(n / n_full), with n_full a parameter (workshop default 64) |
| 8 | Reasonability of deterministic results | **Keep** | Excel | Trend charts of BC, frequency, severity and % GWP; comparison with the expiring rate trended by leveraged trend; min/max BC |
| 9 | Incidence rate adjustment | **Keep** | Alteryx | Overall multiplier on expected claims and on λ, for explained trends only |
| 10 | Stochastic modelling, L1/L2 | **Keep** | Alteryx | Used as the credibility complement, for AAD/AAL effects, for unused-capacity pricing, and for cedant-impact percentiles |
| 11 | Capital | **Drop** | — | — |
| 12 | Solving for the premium | **Replace** | Excel | High-level loading formula (§7) instead of a metric solve |
| 13 | Risk transfer | **Drop** | — | — |
| 14 | Documentation & annotation | **Keep** | Both | Run log, assumptions register, reconciliation pack, colour-coded input cells, no hard-codes |
| — | *(new)* Cedant impact | **Add** | Excel | §8 |

## 4. Inputs

### 4.1 Data (Alteryx)
- **Claims:** claim-line level, ideally the team's existing SchemeA claims output (decision D4). Required fields: member ID, treatment date (incurred trigger), payment date (paid trigger), payer share, currency, segment fields. Optional: benefit limit, ICD, provider.
- **Exposure:** member-level coverage start and end dates, or a monthly in-force / new-entrant summary.
  - RA basis: lives incepting or renewing in each month, **summed**.
  - LO basis: in-force lives in each month, **averaged**.
  - Both give life-years at risk per period.
- **Premium:** GWP / GEP by period. Needed for % GWP metrics and the premium trend.
- **History window:** up to 5 years. 2–5 years supported. A single year triggers a warning.

### 4.2 Control workbook (Excel → Alteryx)
| Group | Parameters |
|---|---|
| General | Cedant, country, line, quote currency, FX to USD, treaty start, quote year, run ID |
| Reinsurance basis | RA / LO; aggregation (per person per year vs per event); claims trigger (incurred in 12 / paid in N); run-off months; product line |
| Data controls | FX table (priced rate per currency); benefit/sub-limit table; policy limit; output threshold; years included |
| Trend | FGU trend by year (client / market / general inflation / residual components); one-off adjustment; premium trend |
| Completion | Pre-AP factor by year (IBNR/P + accumulation to YE); post-AP factor by year (new unknown claims); optional factors by AP band |
| Weights | Months complete by year; default vs custom; custom weights and justification |
| Outliers | Threshold (value or rule: 1.5 × IQR) |
| Credibility | Full-credibility claim count; complement source (stochastic / benchmark / expiring trended) |
| Incidence | Overall incidence adjustment |
| Existing QS | Cedant retention on QS; order of application (QS first or XL first) |
| Layer options | Up to N options: AP, limit, AAL, AAD, cedant co-participation; optional L2 (AP, limit) |
| Stochastic | Iterations (≤ 10,000+), seed, fitting cut-off, candidate distributions, years included in fit |
| Expiring | Expiring structure, rate and experience (for comparison) |
| Scenarios | One row per sensitivity: overrides to trend, completion, weights, incidence, FX |

## 5. Calculation chain (Alteryx stages)

Each stage writes control totals, and the next stage reconciles to them. An unexplained movement stops the run. Expected exclusions (e.g. zero-amount lines) are logged rather than flagged.

| Stage | Workflow | Logic |
|---|---|---|
| 00 | `00_read_control` | Read the Control workbook, validate parameters (types, ranges, weights summing to 100%), explode scenarios |
| 01 | `01_load_claims`, `01_load_exposure` | Ingest; map to the internal schema; record row and amount totals |
| 02 | `02_standardise` | FX as-if (convert back to original currency, then at the priced rate); apply benefit sub-limits; apply QS retention if QS operates first |
| 03 | `03_accumulate` | Sum to claimant × period on the chosen basis (RA/LO, trigger, run-off cut); compute exposure in life-years |
| 04 | `04_adjust` | × cumulative FGU trend to the quote year; × pre-AP completion; cap at policy limit; flag outliers on the adjusted amount |
| 05 | `05_layer` (batch macro over options) | XL = max(0, min(Limit, Adj − AP)) × (1 − cedant retention of layer); × post-AP completion on amount and count; AAD/AAL applied at year level |
| 06 | `06_burning_cost` | By year: count, frequency per 1,000, severity, BC pppy, BC % trended GWP. Apply weights (non-outlier) and equal weights (outlier excess). Credibility Z. Leveraged-trend diagnostic. Sensitivity lines (straight-mean variants) |
| 07 | `07_fit_severity` | Fit candidate severity distributions to adjusted claims above the cut-off; goodness of fit; selected distribution and parameters (§6) |
| 08 | `08_simulate` | Frequency–severity simulation by option, plus L2 and the notional layer; aggregate per iteration after AAD/AAL; write a summary and a quantile table |
| 09 | `09_write_results` | Write summary tables, the quantile table, the run log and the reconciliation pack to the Results workbook |

## 6. Stochastic module

### 6.1 Method
- **Frequency:** Poisson. λ is the expected number of claims above the AP: the deterministic count after trend, completion and incidence adjustment, weighted across years. Negative binomial is optional when the historical counts show overdispersion.
- **Severity:** fitted to adjusted FGU claims above the fitting cut-off (default cut-off = AP; lower it when there are too few claims).
  - Fit the distribution **conditional on exceeding the cut-off** (left-truncated MLE). This avoids the separate truncation and shift steps the workshop needed in @Risk.
  - If the cut-off is below the AP, λ is rescaled to the AP using the fitted survival function.
- **Candidates:** lognormal, gamma, Weibull, Pareto, and GPD above the threshold.
  - Selection uses AIC/BIC and KS/AD statistics, plus a check that the fitted mean excess over the AP is close to the deterministic average. A large gap means the fit is poor.
- **Iteration:** draw N claims ~ Poisson(λ); draw a severity for each claim; apply AP and limit per claim; apply AAD and AAL to the annual total; apply the cedant co-participation.
- **Layer 2** (when both layers are priced):
  - A = S_L1(cut-off₂) / S_L1(AP₁), where S is the survival function.
  - B = S_L2,untruncated(AP₂) / S_L2,untruncated(cut-off₂).
  - λ₂ = λ₁ × A × B.
- **Unused capacity:** if the limit is far above the largest observed claim, price experience only up to that claim. The capacity above it is priced as a notional layer from the fitted severity tail.
- **Reproducibility:** a fixed seed per run, recorded in the run log.

### 6.2 Alteryx implementation options (decision D6, pending environment check)
| Option | Tools | Pros | Cons |
|---|---|---|---|
| a | **Python tool** (numpy / scipy) | Full control over truncated MLE, GPD and vectorised simulation; fast | Depends on scipy in Alteryx's bundled Python environment, or permission to install it |
| b | **R tool** (`fitdistrplus`, `actuar`) | Strong actuarial distribution library | Needs Predictive Tools (R) installed; package install permissions |
| c | **Native Prescriptive / Predictive tools** (Distribution Analysis, Simulation Sampling) | No code; ships with Predictive Tools | Limited distribution set, no truncated fit, less control over the layer logic |
| d | **Native-only fallback** | Generate Rows + Formula (inverse-CDF sampling) for simulation; method-of-moments lognormal fit | No MLE or goodness-of-fit tests; lognormal/Poisson only |

Recommended: **(a)**, with (d) as a fallback that always runs, so the tool still works if the code tools are blocked.

## 7. RI cost (B)

The calculation runs in Excel from Alteryx outputs, per layer option:

- **Expected layer loss:** E[L] = Z × BC_deterministic + (1 − Z) × complement.
  - Complement = stochastic mean, a benchmark rate, or the expiring rate trended by leveraged trend (user choice).
- **Risk load (optional):** k × σ[L], with σ from the simulation (standard-deviation principle).
- **Indicative RI premium:** (E[L] × incidence adj + risk load) / (1 − reinsurer expense % − brokerage % − profit margin % − premium tax %).
  - This is the same structure as the workshop's adjustment factor ≈ 1 / (1 − margin − expenses − brokerage − tax).
- **Quoted as:**
  - rate pppy (per member per year)
  - % of GWP (with the premium trend applied)
  - rate on line (premium / limit)
  - payback (limit / premium)
  - expected RI loss ratio (E[L] / premium)
- **Comparison:** against the expiring rate trended by leveraged trend, and against the min/max BC over the data period.

## 8. Cedant impact (C)

| Metric | Source |
|---|---|
| Expected recoveries vs RI premium; expected net cost of reinsurance = premium − E[recoveries], in absolute terms and as % of GWP | §7 and the simulation mean |
| Recovery distribution: P50 / P75 / P90 / P95 / P99; probability that recoveries ≥ premium | Alteryx quantile table (Excel lookups, so it stays live when loadings change) |
| Historical as-if: by year, recoveries under the proposed structure vs the as-if premium for that year's exposure; cumulative position | Stage 06 by-year output |
| Retained claims gross vs net of XoL: mean and P90 / P95 / P99 of the annual loss ratio | Simulation (descriptive volatility only; no capital) |
| Option comparison grid: A / B / C metrics side by side for all layer options | All of the above |
| QS interaction: cedant net position under QS-first vs XL-first | Stage 02/05 order parameter |

## 9. Reasonability, sensitivities, documentation
- **Diagnostics sheet:** charts of BC, frequency, severity and % GWP by year (look for systematic increases or decreases, which indicate under- or over-trending or a mix change); leveraged vs FGU trend; credibility warning when n < n_full; mix-risk warning when segments differ materially (price them separately rather than blending).
- **Sensitivities:** the scenario dimension (§2) covers weights, trend, completion, incidence and FX. Results are tabulated against the best estimate.
- **Run log:** run ID, user, timestamp, input file hashes, every parameter value, seed, Alteryx and workbook versions, and warnings raised.
- **Reconciliation pack:** raw → standardised → accumulated → adjusted → layered totals by year.
- **Spreadsheet standards:** a single input colour; no hard-codes in formulas; every judgement-based input has a justification cell that is carried into the run log.

## 10. Open decisions
| ID | Decision | Proposed default |
|---|---|---|
| D1 | Covers in phase 1 | Per-person XoL; per-event and aggregate stop loss in phase 2 |
| D2 | Number of layers | L1 plus optional L2; up to 4 layer options per run |
| D3 | Completion factors | User input by year; optional derivation from triangles later (link to the Ultimate Claims tool) |
| D4 | Input contract | SchemeA claims and premium outputs from existing team workflows |
| D5 | Default weight table | Own table by years available (2–5), not a replica of the workshop's "blended enhanced" algorithm |
| D6 | Stochastic engine | Python tool, with a native fallback (§6.2) |
| D7 | Excel ↔ Alteryx handshake | Control workbook (.xlsx) read by Alteryx; Results workbook (.xlsx) with data sheets overwritten by Alteryx and presentation sheets built on formulas; no VBA unless needed |
| D8 | Risk load | Optional k × σ; default k = 0 |
| D9 | Reinstatements | Not modelled (health per-person XoL normally has an annual aggregate limit); revisit for per-event cover |

## 11. Environment checks (GC machine)
- [ ] Alteryx Designer version
- [ ] Predictive Tools (R) installed?
- [ ] Python tool permitted? Is scipy available in the bundled environment?
- [ ] Can the Output tool overwrite named ranges while preserving formatting in the installed version?
- [ ] Excel macros permitted (relevant only if D7 changes)
