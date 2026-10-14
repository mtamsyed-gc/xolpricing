# Scope — XoL Pricing Tool (broker view)

Version 0.2 · 2026-10-14 · decisions D1–D9 agreed (§12)
Framework: adapted from a reinsurer-side health WCXL pricing workshop (14 sections). This tool is
built for a **broker**, so the reinsurer's capital, risk-transfer and target-metric solve are dropped
and a cedant-impact module is added.

---

## 1. Purpose

For a medical (health) portfolio and one or more candidate structures, produce:

| | Output | Primary question answered |
|---|---|---|
| **A** | Expected claims cost to each layer | What will this layer cost on a best-estimate basis? |
| **B** | Indicative RI premium | What should the market charge, on high-level loadings? |
| **C** | Cedant impact | Is this structure good value for the cedant (recoveries vs premium, volatility of retained claims)? |

**Out of scope:** capital (EC / local / rating-agency), risk-transfer testing (ERD), reinsurer
NBEV / distributable earnings, solving premium to reinsurer target metrics, variable-rate /
experience-refund solve macros.

## 2. Cover matrix

All three cover types are built on both treaty bases (D1).

| Cover | Unit of loss | Attachment / limit expressed as | RA basis | LO basis |
|---|---|---|---|---|
| **Per-person XoL** (specific) | Accumulated claims per insured life per period | Amount per life | ✔ | ✔ |
| **Per-event XoL** (catastrophe / accumulation) | Sum of claims across lives arising from one event | Amount per event, plus an optional minimum number of lives | ✔ | ✔ |
| **Aggregate stop loss** | Annual aggregate claims of the portfolio (gross, or net of per-person XoL) | Loss ratio % of EPI, or an amount | ✔ | ✔ |

The basis drives four mechanics, and each is parameterised:

| Mechanic | Risks attaching (RA) | Losses occurring (LO) |
|---|---|---|
| Exposure | Lives incepting or renewing in the treaty year (monthly inputs **summed**) | In-force lives during the treaty year (monthly inputs **averaged**) |
| Claims allocation | By policy inception cohort; up to 24 incurred months per treaty year | By incurred (or paid) date within the treaty year |
| Accumulation period (per-person, SL) | Member's policy year | Treaty year |
| Completion of expiring year | Large: IBNR + unearned + unwritten (§4.3) | Smaller: IBNR + remaining months to treaty year end |

Structures can be stacked:
- **Order of application** is a parameter: QS → per-person XoL → per-event XoL → stop loss, or user-defined.
- **Stop loss on net claims:** when it attaches to claims net of the XoLs, it consumes the simulated XoL recoveries iteration by iteration (§6).

## 3. Platform split

| Layer | Tool | Rule |
|---|---|---|
| Data ingestion, cleaning, FX as-if, limits, accumulation, event grouping, trend, completion, layering, burning cost, weights, outliers, credibility | **Alteryx** | *All* data manipulation and preparation is done in Alteryx — no Power Query, no Excel formulas acting on claim rows |
| Distribution fitting and simulation | **Alteryx** | Python tool (preferred), with a native-tool fallback (D6) |
| Assumptions entry, layer options, scenarios | **Excel** — Control workbook | The single source of inputs, which Alteryx reads at run start |
| Loadings → RI premium, credibility-blend selection, cedant-impact exhibits, charts | **Excel** — Results workbook | Built from summary and quantile tables Alteryx writes out, so changing a loading **does not need a re-run** |

Design consequences:
- **No row or rating-factor limits.** Accumulation happens before any threshold; the size threshold only trims what is written out.
- **Sensitivities run as a scenario dimension** in one Alteryx run (one row per scenario). There are no overwrite-and-restore macros.

## 4. Inputs

### 4.1 Input modes (D4)

| Mode | Source | Alteryx entry point |
|---|---|---|
| **Standalone** (one-off work) | User-supplied data in the tool's input templates: (i) claim-line or claimant-level listing; (ii) **summary mode** — large-claim listing above a threshold plus aggregate exposure, premium and claims by year, for when only summary data exists | `01a_adapter_standalone` |
| **ETE extension** | Canonical payer-level SchemeA claims and premium outputs from the ete-dashboard pipeline | `01b_adapter_ete` |

Both adapters map to a single internal schema (`docs/data-contracts.md`, to be written). Everything downstream is mode-agnostic. Summary mode disables the stages that need line-level data:
- per-person accumulation from lines
- event grouping
- the member-level exposure build

The run log records which stages ran.

### 4.2 Data (Alteryx)
- **Claims:** member ID, policy/scheme ID, treatment date, payment date, payer share, currency, claim status. Optional: benefit limit, ICD, provider, location/site, event ID, cause code.
- **Exposure:** member-level coverage start and end dates, or monthly in-force and new-entrant counts by year. These give life-years at risk on the chosen basis.
- **Premium:** written and earned premium by month and year. Used for % GWP/EPI metrics, the premium trend, the stop loss loss-ratio history, and the indicative unearned and unwritten factors (§4.3).
- **History window:** 2–5 years. A single year triggers a warning.

### 4.3 Expiring-year completion — full annualised view

Each historical year, and the expiring year in particular, is brought to a full-year ultimate in three separately input components. Each is a multiplicative factor, entered as a **selected** value. Where data allows, Alteryx shows an **indicated** value beside it as a reference only.

| Component | Covers | Indicated value computed from data | Split |
|---|---|---|---|
| **IBNR / IBNP gross-up** | Claims incurred on earned exposure but not yet reported or paid | User input only (D3) | Pre-AP (existing claimers: accumulation on known lives) / post-AP (new claimers: count and amount) |
| **Unearned gross-up** | Future claims on policies already written but not yet earned | Written premium ÷ earned premium for risk written to date | Pre-AP / post-AP |
| **Unwritten gross-up** | Claims on cohort months not yet written (RA), or remaining treaty months (LO) | Expected full-year written premium (EPI or plan) ÷ written to date | Post-AP only (all new claimers) |

- **Pre-AP factors** apply to FGU claims before the attachment point, since they change severity. **Post-AP factors** apply to the XL amount and count after the attachment point, since they change frequency. This follows the workshop methodology.
- Factors are entered by year and can optionally vary by AP band, because large claims settle more slowly.
- The order of calculation (across rows vs down columns) changes how the gross-up splits between existing and new claimers. It is fixed in the methodology and documented.
- An **expiring-year annualisation exhibit** shows paid/reported → +IBNR → +unearned → +unwritten. Claims, counts, frequency and severity are shown at each step, matching the workshop's completion breakdown chart.

> **Input caveat (shown next to the input cells, D3):** Development factors derived at portfolio
> level are driven by high-frequency, low-severity claims. They may **understate** development for
> claims above an attachment point: large claims are reported later, case estimates move more, and
> they settle more slowly. Where possible, use large-claim-specific development or a case-by-case
> review of open large claims, and document the basis in the justification cell.

### 4.4 Control workbook (Excel → Alteryx)

| Group | Parameters |
|---|---|
| General | Cedant, country, line, quote currency, FX to USD, treaty start, quote year, run ID, input mode |
| Treaty basis | RA / LO; claims trigger (incurred in 12 / paid in N); run-off months; per-person accumulation period |
| Exposure & premium | **Expected EPI for the quote year**; expected lives (or derived from the EPI ÷ trended average premium); premium trend |
| Data controls | FX table (priced rate per currency); benefit/sub-limit table; policy limit; output threshold; years included |
| **Trend** | FGU trend by year (client / market / general inflation / residual components); one-off adjustment; optionally different trend for large claims; premium trend |
| **Completion** | IBNR, unearned and unwritten factors by year, pre-AP and post-AP (§4.3), with optional AP-band variants; justification cells |
| Weights | Months complete by year; default vs custom; custom weights and justification |
| Outliers | Threshold (value or 1.5 × IQR rule) |
| Credibility | Full-credibility claim count (default 64); complement source |
| Incidence | Overall incidence adjustment, with justification |
| Event definition | Event ID field if supplied; otherwise a clustering rule: hours clause / date window (days), grouping key (scheme, site, cause), minimum lives per event |
| Stop loss basis | Gross or net of XoL; attritional/large split threshold; LR history adjustments |
| Existing QS | Cedant retention on QS; order of application |
| Layer options | Up to 4 options per cover type: AP, limit, AAL, AAD, cedant co-participation, reinstatements (§7.3); optional L2 |
| Stochastic | Iterations (≥ 10,000), seed, fitting cut-off, candidate distributions, years included in fit |
| Loadings | Reinsurer expenses %, brokerage %, profit margin %, premium tax %, risk load k (default 0) |
| Expiring | Expiring structure, rate and experience |
| Scenarios | One row per sensitivity: overrides to trend, completion, weights, incidence, FX, EPI |

## 5. Calculation chain (Alteryx stages)

Each stage writes control totals, and the next stage reconciles to them. An unexplained movement stops the run. Expected exclusions are logged, not flagged.

| Stage | Workflow | Logic |
|---|---|---|
| 00 | `00_read_control` | Read the Control workbook, validate parameters (types, ranges, weights summing to 100%, factor ≥ 1 warnings), explode scenarios |
| 01 | `01a_adapter_standalone` / `01b_adapter_ete` | Map to the internal schema; record row and amount totals |
| 02 | `02_standardise` | FX as-if (convert back to original currency, then at the priced rate); benefit sub-limits; QS retention if QS operates first |
| 03 | `03_exposure` | Life-years and premium by treaty year on the RA/LO basis; indicated unearned and unwritten factors |
| 04 | `04_accumulate` | Per-person: claimant × accumulation period. Per-event: group claims into events (ID or clustering rule). Stop loss: portfolio × treaty year, split into attritional/large |
| 05 | `05_adjust` | × cumulative FGU trend to the quote year; × pre-AP completion; cap at policy limit; incidence adjustment; outlier flag on the adjusted amount |
| 06 | `06_layer` (batch macro over cover × option) | XL = max(0, min(Limit, Adj − AP)) × (1 − co-participation); × post-AP completion on amount and count; AAD/AAL/reinstatements at year level; stop loss on adjusted annual LR vs attachment % EPI |
| 07 | `07_burning_cost` | By year: count, frequency per 1,000, severity, BC pppy, BC % EPI. Weighting (non-outlier), equal weights (outlier excess), credibility Z, leveraged-trend diagnostic, sensitivity variants, expiring-year annualisation exhibit |
| 08 | `08_fit` | Severity fits (per-person, per-event cost), lives-per-event distribution, attritional LR distribution (§6) |
| 09 | `09_simulate` | Joint simulation of all covers in application order; per-iteration recoveries, reinstatement premiums, retained claims; summary and quantile tables |
| 10 | `10_write_results` | Write to the Results workbook, plus the run log and reconciliation pack |

## 6. Stochastic module (Alteryx)

| Component | Model | Parameterisation |
|---|---|---|
| Per-person frequency | Poisson (negative binomial optional) | λ = expected number of claims > AP: deterministic weighted count after trend, completion and incidence, scaled to quote-year exposure |
| Per-person severity | Left-truncated MLE above the fitting cut-off; candidates lognormal, gamma, Weibull, Pareto, GPD | Selection on AIC/BIC and KS/AD statistics, plus a check of the fitted vs deterministic mean excess over the AP |
| Layer 2 frequency | λ₂ = λ₁ × A × B | A = S_L1(cut-off₂) / S_L1(AP₁); B = S_L2,untruncated(AP₂) / S_L2,untruncated(cut-off₂) |
| Unused capacity | Notional layer from the fitted tail above the largest observed claim | Experience is priced only up to the largest claim |
| Per-event frequency | Poisson with low λ | From identified historical events where they exist; otherwise a benchmark or scenario input (e.g. 1-in-N-year event) |
| Per-event size | Lives per event × cost per life | Lives: empirical or a discrete distribution capped by the maximum concentration per site/scheme. Cost per life: from the per-person severity distribution, or a user scenario |
| Per-event scenarios | Deterministic | Lives at site × average claim per life × limit utilisation; reported alongside the stochastic result |
| Stop loss aggregate | Attritional + large | Attritional LR ~ lognormal/normal fitted to adjusted LR history below the large threshold (CoV floor input for short histories). Large claims from the per-person simulation. Aggregate = attritional + large (− XoL recoveries if net) |
| Reinstatements | Per iteration | Consumed limit drives reinstatement premium, pro rata to amount (and optionally time) |
| Reproducibility | Fixed seed | Recorded in the run log |

Implementation: Alteryx **Python tool** (numpy/scipy) with a **native fallback** (Generate Rows + Formula, inverse-CDF sampling, method-of-moments lognormal) that always runs (D6). Environment checks are in §13.

## 7. RI cost (B)

The calculation runs in Excel from Alteryx outputs, per cover × option.

### 7.1 Expected loss
E[L] = Z × BC_deterministic + (1 − Z) × complement.
- Complement = stochastic mean, a benchmark rate, or the expiring rate trended by leveraged trend (user choice).
- **Per-event cover** is usually exposure/scenario-led: Z defaults to 0 unless enough events have been identified.
- **Stop loss** uses the stochastic expected loss as primary, with the as-if experience as a check.

### 7.2 Premium
- **Indicative RI premium:** (E[L] + k × σ[L]) / (1 − reinsurer expenses − brokerage − profit margin − premium tax), with k defaulting to 0 (D8).
- **Quoted as:**
  - rate pppy (per member per year)
  - % of EPI
  - rate on line (premium / limit)
  - payback (limit / premium)
  - expected RI loss ratio
  - stop loss: premium as % of EPI, plus attachment and limit as loss ratio points
- **Comparison:** against the expiring rate trended by leveraged trend, and against the min/max BC over the data period.

### 7.3 Reinstatements (D9)
Applies to per-person and per-event covers.

| Parameter | Options |
|---|---|
| Number | 0 … n, or unlimited |
| Cost | Free, or % of the upfront premium (per reinstatement) |
| Pro rata | As to amount; optionally as to time |
| AAL | Derived as (1 + n) × limit, or input directly |

When reinstatements are paid:
- The upfront premium is solved so that upfront + E[reinstatement premium] = the target premium.
- The simulation supplies E[reinstatement premium] and its distribution.

## 8. Cedant impact (C)

| Metric | Source |
|---|---|
| Expected recoveries vs total RI cost (upfront + expected reinstatement premium); expected net cost of reinsurance, absolute and % of EPI | §7 and the simulation mean |
| Recovery distribution: P50 / P75 / P90 / P95 / P99; probability that recoveries ≥ RI cost | Alteryx quantile table (Excel lookups, so it stays live when loadings change) |
| Historical as-if: by year, recoveries under the proposed structure vs the as-if premium for that year's exposure; cumulative position | Stage 07 output |
| Retained claims gross vs net of the full programme: mean and P90 / P95 / P99 of the annual loss ratio | Simulation (descriptive volatility only; no capital) |
| Option comparison grid: A / B / C metrics side by side across covers and options, including combined programmes | All of the above |
| QS interaction: cedant net position under different orders of application | Order parameter |

## 9. Workshop framework → broker treatment

| # | Workshop section | Treatment | Broker adaptation |
|---|---|---|---|
| 1 | What the model does | **Extend** | Per-person, per-event and stop loss, each on RA and LO |
| 2 | Parameters & data inputs | **Keep** | Two input modes; no row limit; FX as-if; limits; mix-risk check |
| 3 | Completion | **Extend** | IBNR, unearned and unwritten as separate inputs with indicated values and the large-claim caveat |
| 4 | Trend | **Keep** | FGU trend; leveraged trend as a diagnostic; premium trend; optional large-claim trend |
| 5 | Weighted past experience | **Keep** | Own default weight table (D5); custom weights with justification |
| 6 | Outliers | **Keep** | Threshold on the adjusted claim; excess spread equally across years |
| 7 | Credibility | **Keep** | √(n / n_full) |
| 8 | Reasonability | **Keep** | Diagnostics sheet and expiring comparison |
| 9 | Incidence adjustment | **Keep** | Applied to expected claims and λ |
| 10 | Stochastic | **Extend** | In Alteryx; covers all three cover types plus reinstatements |
| 11 | Capital | **Drop** | — |
| 12 | Solving for premium | **Replace** | Loading formula (§7) |
| 13 | Risk transfer | **Drop** | — |
| 14 | Documentation | **Keep** | Run log, assumptions register, reconciliation pack, input colour coding, justification cells |
| — | Cedant impact | **Add** | §8 |

## 10. Reasonability, sensitivities, documentation
- **Diagnostics:** charts of BC, frequency, severity and % EPI by year (look for systematic increases or decreases, which indicate under- or over-trending or a mix change); leveraged vs FGU trend; credibility warning when n < n_full; mix-risk warning when segments differ materially (price them separately rather than blending).
- **Sensitivities:** the scenario dimension covers weights, trend, IBNR, unearned and unwritten factors, incidence, FX and EPI. Results are tabulated against the best estimate.
- **Run log:** run ID, user, timestamp, input mode, input file hashes, every parameter value and justification, seed, stages run, Alteryx and workbook versions, warnings.
- **Reconciliation pack:** raw → standardised → accumulated → adjusted → layered totals by year and cover.
- **Spreadsheet standards:** a single input colour; no hard-codes in formulas; caveat text next to judgement-based inputs.

## 11. Build phasing (proposed)

| Phase | Content |
|---|---|
| 1 | Internal schema and data contracts; standalone adapter; RA/LO exposure; per-person XoL deterministic chain (stages 00–07); Control and Results workbooks; synthetic data set |
| 2 | Stochastic per-person (fit, simulate, L2, unused capacity, reinstatements); RI cost; cedant impact |
| 3 | Aggregate stop loss (attritional + large); stacking and order of application |
| 4 | Per-event XoL (event grouping, lives-per-event, scenarios) |
| 5 | ETE adapter; summary-data mode hardening |

## 12. Decisions

| ID | Decision | Status |
|---|---|---|
| D1 | Covers | **Agreed:** per-person, per-event and aggregate stop loss, each on RA and LO |
| D2 | Layers | **Agreed:** L1 plus optional L2; up to 4 options per cover per run |
| D3 | Completion factors | **Agreed:** user input, with a caveat that portfolio development may not suit claims above an AP; indicated unearned/unwritten values shown as reference |
| D4 | Input contract | **Agreed:** standalone (incl. summary mode) and ETE-dashboard extension, through separate adapters to one internal schema |
| D5 | Default weights | **Agreed:** own table by years available (2–5) |
| D6 | Stochastic engine | **Agreed:** Python tool with a native fallback |
| D7 | Excel ↔ Alteryx handshake | **Agreed:** Control workbook (.xlsx) → Alteryx → Results workbook (.xlsx), no VBA unless needed |
| D8 | Risk load | **Agreed:** optional k × σ, default k = 0 |
| D9 | Reinstatements | **Agreed:** all options — number, free/paid, % cost, pro rata as to amount and/or time |

## 13. Environment checks (GC machine)
- [ ] Alteryx Designer version
- [ ] Predictive Tools (R) installed?
- [ ] Python tool permitted? Is scipy available in the bundled environment?
- [ ] Can the Output tool overwrite named ranges while preserving formatting in the installed version?
- [ ] ETE-dashboard output location/format reachable from Alteryx
