# Changelog

Methodology and data-contract changes, newest first.

## 2026-11-08 — 0.9.1: swing rating and MDP (Results workbook)
Owner's request: results had no allowance for swing rates (min / max) or MDPs. Owner's decisions: swing rate =
loading × layer losses / exposure, clamped min / max; the rate basis follows the option's (expiring) basis; swing
replaces paid reinstatements; MDP adjustment at exposure scenarios.
- **New Results sheet Premium Terms** (formulas only; no stage or data-contract change, no re-run): per option the
  swing inputs (Swing YES/NO, rate basis override, loading, min, max, provisional rate) with a Status (STOP until
  complete), expected swing premium over the 999 recovery quantiles vs the flat technical premium, P(min) and P(max)
  apply, expected loss ratio, break-even min rate (closed form on the sorted quantiles), swing premium at the return
  periods, provisional premium and expected adjustment; MDP % and instalments with the deposit premium, MDP,
  instalment, and final / additional premium at five editable exposure scenarios. Working sheet `swing_calc`.
  XOL_MECHANICS §7.5; manual §7.5.
- C1 gains a column "Swing: expected premium (min - max)" for swing-rated options.
- C2 is unchanged: it still shows the flat premium (the swing premium in a return-period year needs the recovery of
  that iteration, which `out_struct_quantiles` does not carry; a later change if wanted).
- Existing live workbooks do not have the sheet: replace `data\output\live\XoL_Results.xlsx` with the 0.9.1
  template (save a dated copy first); the data sheets refill on the next stage 07.
- Tests: `tests/test_stage07.py` checks every new figure against numpy on the quantiles (break-even by brentq).

## 2026-11-07 — 0.9.0: Python only (Alteryx dropped from the XoL tool); `below_cutoff`
**Alteryx dropped (owner, 2026-11-07; this tool only — Merit and the ETE pipeline keep Alteryx).** The Python route
reproduces Designer exactly (laptop checks 2026-11-06/07; Designer baseline in the tests), so the tool no longer
installs, launches or mentions Alteryx:
- `tools\run_pipeline.bat` is now the Python command-line route (the former `run_pipeline_python.bat`, same
  arguments); the AlteryxEngineCmd launcher is gone. `check_merit.bat` (admin pack) runs on Python; `import_merit.bat`
  points to the page. `run_python.py` still refuses stage files left by an old Alteryx run (start from 00).
- Manual, RUNNING, README, guide, scope, methodology, data contracts, Control and Results workbook texts: Python and
  the page only; the manual's section 5 is rewritten around the page with the 0.9 screenshots; troubleshooting rows for
  the engine and Designer's Python environment replaced by the page / package rows.
- Kept, as history and regression: the stage definitions in `alteryx/workflows/` (internal; the folder keeps its name;
  the executor `tools/alteryx/sim.py` runs them), Designer's stored outputs and `tests/test_designer_twin.py`.
- User pack: no builders (`tools/alteryx/build_*.py`, `xmlkit.py`) and no Alteryx launcher; `tests/test_packs.py` now
  unpacks it and runs the live book 00 → 04a on its own.
- Next (0.10, option 2): stages rewritten as plain pandas / numpy modules, starting with stage 04 (seconds instead of
  minutes, a fraction of the memory), verified against the same oracles, the Designer baseline and the clean-room rebuild.

### `below_cutoff`: layers attaching below the fitting cut-off
Found by the independent reconciliation (0.8.0): a `fit_cutoff` above a layer's attachment silently dropped the
claims between the AP and the cut-off from that layer's stochastic cost (−20 % / −31 % on the synthetic book), with
only the indirect S05 flag. Owner's decision: let the user choose.
- **New Params `below_cutoff`** (required; default `STOP`; a workbook made before 0.9 needs the row from the
  template). `STOP`: stage 04 stops (S08) naming each layer attaching below the cut-off. `IGNORE`: the band is left
  out; S09 FLAG per layer with the band frequency and its deterministic cost (λ_band × mean layer loss over the band
  units) and the share of the layer it represents. `EMPIRICAL`: the band is simulated — λ_band from the adjusted units
  in (lowest active AP, u] with the same completion, weights, lives and incidence as λ; counts Poisson(λ_band) by
  inverse CDF on a fourth MINSTD stream; each band claim an (adjusted amount, cap) pair drawn uniformly from those
  units on a fifth stream; spliced with the tail claims before the layer step; S10 INFO per layer with the band's
  share of the mean recovery; S11 FLAG when the band is empty. Band claims stay out of `sim_gross` (below the
  stop-loss large threshold, covered by the attritional model). Methodology §8.
- Outputs: `out_sim_summary` gains `below_cutoff`, `band_from`, `lambda_band`, `n_band`, `mean_recovery_band`;
  `out_sim_iterations` gains `recovery_band`. With the cut-off at the lowest AP (the default) every output is
  unchanged (base variant identical to 0.8.0 iteration by iteration).
- Front end: the Params readiness table reads the Layers sheet and shows, before the run, which layers attach below
  the cut-off and what the chosen mode will do (red for STOP, amber for IGNORE, blue for EMPIRICAL); the Input guide
  and the guide's step 3 explain the three options with a table; codes S08–S11 in the guide's code table; the
  workbook's Params comment and Guide sheet carry the same text.
- Tests: `test_stage04.band()` runs the three modes on a copy of the synthetic book with the cut-off at 150k:
  S08 stops; IGNORE equals the oracle without band claims and S09 quotes the deterministic band cost; EMPIRICAL
  equals the oracle with band claims (streams 4, 5) iteration by iteration, its mean band layer loss equals the
  deterministic band cost within 3 SE, and layers attaching at or above the cut-off are unchanged between modes.

## 2026-11-07 — 0.8.0: browser interface
- **Independent reconciliation of stage 04** (`tests/independent/`). A separate agent rebuilt the per-person
  simulation from a neutral specification and parameter packs only (seed, iterations, λ, φ, count distribution, u,
  M, the selected severity distribution and its parameters, the caps in order, the layer terms); it never saw the
  repository. Eight variants of the synthetic book: base (Pareto, Poisson, seed 12345, 2,000 iterations), seed 777,
  GPD, lognormal (boundary fit, forced by `fit_selected`), negative binomial (φ = 1.80), `fit_cutoff` 150,000,
  `severity_source = NATIVE`, and Designer's stored 10,000-iteration run. Findings: (1) the seeded rebuild reproduces
  every iteration of every layer (max relative difference 3e-12), Designer's run included; (2) the tool's mean
  recovery is within 1.4 SE of the closed-form λ·E[layer loss] on all 32 layer-variants, and a 200,000-iteration
  Monte Carlo with an independent generator agrees with the closed form within 1.5 SE, so the 50-point-per-decade
  grid adds no visible bias for GPD or lognormal; (3) movements agree: GPD −1.0 / −6.6 / −1.2 / −3.7 % on the four
  options in both the tool and the closed form, lognormal −0.3 / −2.3 / −0.4 / −1.2 %, negative binomial 0 % on the
  mean with SD +11 / +2 / +12 / +5 % (as expected: more dispersion, same mean), seed change ±0.7 % (Monte Carlo
  noise at 2,000 iterations), cut-off 150k −19.6 / −1.7 / −30.7 / −2.5 % in the tool against −19.4 / −1.3 / −30.6 /
  −2.3 % closed form. (4) `sd_recovery` is the population SD (n), not the sample SD (n − 1): documented here. (5) A
  fitting cut-off above a layer's attachment removes the claims between the AP and u from that layer's stochastic
  cost (the −20 % and −31 % above): the S05 flag fires (deterministic / stochastic 1.28 and 1.51), as designed;
  worth a dedicated check (owner to decide).
- **Interactive guide (same day).** `ui/xol/guide.html` ("How to use" in the page header; also opens from disk):
  the user picks the data they have (line data, claimant listing, Merit outputs) and the nine steps adapt (install,
  start the page, Control workbook, data files, run, checks, Results workbook, re-runs, troubleshooting), each with
  a screenshot of the real window from the laptop run of 2026-11-07 (`ui/xol/img`), a "Done when" checkbox (kept
  in the browser) and buttons that open the template, the input folder and the workbooks through the page. The
  server serves `ui/` read-only and accepts `file://` calls from the guide (Origin null only).
- **Two packs** (`tools/release/make_packs.py` → `dist/`): the **user pack** holds the page, guide, Python route,
  live workflows, templates and docs and no synthetic data, tests, reference material or builders; the page hides
  the synthetic book when `data/synthetic` is absent (manifest `requires`). The **admin pack** is the whole
  repository. `tests/test_packs.py` checks the contents and serves the unpacked user pack.
Owner's ask: a contained workflow in one browser page, so a pricing does not mean switching between Explorer,
command windows and Excel; Excel stays where it is the interface (Control and Results workbooks); the page notices
a saved workbook; the theme and behaviour are shared so the other tools can adopt the same front end.
- **`tools\xol_ui.bat`** starts `tools/ui/serve.py` (standard library only, 127.0.0.1, port 8765) and opens the
  page. One page, top to bottom: Inputs (Control workbook: Open in Excel, last saved, data files present for the
  chosen adapter), Run (Python route, from / to stage, live log, stage timeline), one card per stage in run order
  (status pill from `checks_NN.csv`: OK / flags / STOP, the check rows, the stage's reconciliation and result tables,
  Open CSV), Results workbook (Open in Excel, save a dated copy, open the output folder).
- **Change detection.** The server records the Control workbook's mtime when a run starts; a later save shows a
  banner with a Re-run button. Outputs written before the current run started show as "earlier run" (grey), so a
  partial run cannot be mistaken for a full one. A STOP opens its card and marks the run and the header.
- **Reusable kit.** `ui/kit/gc_ui.css` (tokens: navy / teal on white; pills, cards, tables, banners, log) and
  `ui/kit/gc_ui.js` (API calls, polling, table rendering, cards). A tool page is `ui/<tool>/manifest.json`
  (variants, adapters, stages and their files) plus a thin `index.html`; `serve.py` reads the manifest.
- **Safety.** The server binds to localhost, opens and reads files only under `data/` and `excel/templates/`
  (resolved paths, `..` refused), never deletes, allows one run at a time, and logs every open and copy to
  `data/output/<variant>/ui_actions.log`. Run state is in `ui_run.json`, the log in `ui_run.log`.
- Tests: `tests/test_ui.py` (served files, state, a run of 00-03 through the API, table endpoint, change
  detection, planted STOP, refusals). Not in scope yet: the Alteryx engine route from the page, editing Params in
  the browser, charts.
- **Input help (same day).** Every user input now says what to enter, its options and an example, and whether it is
  required. Control workbook (`build_control.py`, both templates rebuilt): required input cells yellow, optional
  blue, labels grey; row 3 (not read by the tool) marks every column required / optional; every header and every
  Params value cell carries a comment (required or optional | type or options | example | description); new
  **Guide** sheet listing every sheet and column; README legend. Content lives in `control_spec.PARAM_EXAMPLES` and
  `COLUMN_HELP` (one source for the workbook, the page and the manual). Page: `/api/params` reads the Params sheet of
  the chosen book and shows a readiness table (required ● / optional ○, blank required values in red, values outside
  the allowed list amber, rows missing from a pre-0.7 workbook named); `/api/spec` feeds an Input guide card
  mirroring the Guide sheet; the page's own controls (book, adapter) carry help text. Tests in `test_ui.py`.
- Server: `/api/open` opens only workbook, CSV, text, log, JSON and Markdown files (and folders); a script dropped
  under `data/` is refused rather than executed. The Run card is rebuilt only when the run state changes.
- Laptop check of the page (2026-11-07, `Documents\20261107`, Chrome): started from `start_ui.cmd`, run 00 → 07 from
  the page in 6 min 50 s (stage 04 298 s), live log and stage chips, Open in Excel on the Control workbook, stage
  card with its flag and tables, dated copy of the Results workbook, Open Results in Excel. Two fixes from it: a
  card is rebuilt only when its status or files change (open tables survived the polling badly before), and a
  flagged card opens itself when the flag first appears.
- Laptop check (0.7.0, 2026-11-06, `Documents\20261106_v2`): `run_pipeline_python.bat synth 01a` on Python 3.14.6,
  every stage ok in 641 s (stage 04 484 s), peak Python working set 9.3 GB of 15.8 GB; 43 output files equal
  Designer's stored run; the Results workbook recalculates with 0 formula errors.

## 2026-11-06 — 0.7.0: Merit v8 safeguard, benefit limits from the data, Python route
Owner's three asks: (A) get the Merit safeguard working again, (B) a Python route so pricing does not depend on
Alteryx, (C) take the benefit limit from the data where it is available, else a global limit the user sets.
- **(A) Merit safeguard.** `tests/test_merit.py`'s check that the fixture equals a fresh build from Merit's current
  outputs failed after Merit moved to v8.0.11 (contract IP4-13; additive columns, two more manifest keys). Fixture
  `tests/fixtures/merit_v7` rebuilt from those outputs (same row counts, fictitious names); the 0.6 sample Designer
  ran on 2026-10-22 is kept unchanged as `tests/fixtures/merit_v7_twin` for the Designer comparison
  (`test_designer_twin.py`). 00 → 01c → 02 on the new fixture passes every contract check.
- **(C) Benefit limits.** Order, for every adapter: the data's own limit → the plan's limit on the Limits sheet → new
  Params `default_benefit_limit` (quote currency; blank = no limit) → none.
  - Merit (01c): Merit writes no limit amount, but where a claimant CAP binds on a life-period
    (Σ `PayerShareExcess` > 0) Σ `PayerShareCapped` equals the cap, so that becomes the life-period's limit. A cap
    that never binds is not visible in the outputs; those lives take the plan, then the default. Closes the open
    decision in data-contracts §2.3. Priced amounts stay the actual Paid / OS; stage 03 applies the limit as before.
  - Line (01a): members.csv `annual_limit` → plan → default. Claimant (01b): claimants.csv `annual_limit` → default.
  - New INFO checks A18 (count taking the limit from the data) and A19 (count on the default), written only when the
    case occurs. The default-limit row is new in Params: **a Control workbook made before 0.7 stops at P04**; add the
    row from `excel/templates/XoL_Control.xlsx` (blank value = previous behaviour).
  - Tests: planted binding cap, plan-limit and default cases on the Merit fixture (`test_merit.py`); blank member and
    claimant limits taking the default in line and claimant mode (`test_stage01.py`).
- **(B) Python route.** `tools/run_python.py` (and `tools/run_pipeline_python.bat`) runs the committed workflows in the
  strict executor `tools/alteryx/sim.py`, same arguments, inputs and outputs as `run_pipeline.bat`, log
  `python_run.log`. Stage files are route-specific: `stage/ENGINE.txt` records which route ran stage 00 and the
  Python route refuses to start later on Alteryx stage files (`run_pipeline.bat` now writes the marker too).
  `requirements-native.txt` pins numpy, pandas, openpyxl and scipy for it.
  - Executor: an output is released once its last consumer has run (the 10,000-iteration stage 04 ran out of memory
    holding every tool's output).
  - Verified in the build container on the synthetic book at 10,000 iterations: 00 → 07 in 320 s (stage 04 194 s,
    07 87 s), peak memory 13.3 GB. Stage 04, 05 and 06 outputs equal Designer's stored run file for file; 00–03
    checks, reconciliations and burning-cost tables equal it (`tests/test_python_route.py`); stage 04a selects the
    same distribution with the same severity grid, fitted parameters within 1e-5 relative (scipy 1.18 vs Designer's
    1.16). 13 GB suits the owner's laptop (63.5 GB); on 16 GB laptops lower `iterations` for working runs until
    stage 04 is vectorised.
- Laptop check: passed 2026-11-06 (see 0.8.0).

## 2026-10-28 — 0.6.1: Merit v7.6.0 re-verified
- Merit moved from v7.0.2 to v7.6.0 (contract IP4-10) since the fixture was built. Its changes are additive; the XoL tool reads columns by name, so the new `LifeID`, `ScopeStatus`, `ScopeRule`, `PayerShareCapped`, `PayerShareExcess` and the manifest's `contract_version` pass through unused. Fixture `tests/fixtures/merit_v7` rebuilt from the v7.6.0 synthetic outputs (totals unchanged; one empty member-month row no longer written); 00 → 01c → 02 and the Designer twin reconcile as before.
- `docs/XOL_MECHANICS.md`: the calculations end to end (claim → unit → adjusted amount → layer loss → burning cost → severity fit → simulation → premium), with a worked example.
- data-contracts §2.3: what Merit's scope register (EXCLUDE, CARVEOUT, RESHARE, CAP) does to the tables the XoL tool reads, and two open owner decisions (CAP → plan limits; STOP on an unknown `contract_version`).

## 2026-10-22 — 0.6: Merit outputs as an input
- Owner: take Merit v7's final outputs as they are (no change to Merit) and make every further adjustment in the XoL tool. Adapter 01c now reads Merit's `dim_life.csv`, `fact_member_month.csv`, `fact_claims.csv` and `run_manifest_claims.csv` as written:
  - members are **life-periods (LifeKey × UWY)**: a life renewing across UWYs no longer stops the run (A14 now checks LifeKey × UWY); member-months and claims are joined on LifeKey × UWY; policy_id = PolKey | UWY;
  - valuation date from Merit's run manifest (`valuation_date`); claims treated after it are **excluded with an A04 FLAG** (Merit's claims extract runs past the valuation date; owner: valuation = Merit's valuation date);
  - `ete_payer_filter` (new Control parameter) with the TPA filter; A16 flags several payers pooled in a run;
  - a life-period without cover dates (Merit "Void ab initio") is kept for its claims with zero cover days (A17 FLAG).
- `tools\import_merit.bat` copies Merit's four files into `data\input\ete`; `tools\check_merit.bat` runs the route on Merit's synthetic outputs through the engine; `run_pipeline.bat` takes a last-stage argument.
- `tests/test_merit.py` (contract test): fixture `tests/fixtures/merit_v7` = Merit v7.0.2's synthetic outputs with company names replaced in values and keys (`tools/synth/merit_fixture.py`); 00 → 01c → 02 against a pandas computation (life-periods, claims, paid / OS, earned exposure, payer filter, late claim, void life-period); and, with an ete-dashboard clone alongside, the fixture must equal a fresh build from Merit's current outputs.
- Synthetic ETE files carry `run_manifest_claims.csv` (Merit format) instead of `run_meta.csv`.
- Engine run on the owner's laptop (2026-10-22, driven from the desktop): `check_merit.bat` passed (Merit fixture 00 → 01c → 02 and synthetic ETE 00 → 01c, 0 errors, 0 warnings; checks, recon and indicated factors equal the simulator); the full synthetic chain 00 → 07 ran clean in 53 s. Two batch-file defects found and fixed on the first attempt: the engine does not create `stage` / `checks` / `results` under a new output folder (the script now does), and the stage loop lost the workflow name when `run_pipeline.bat` was called from another batch (rewritten with delayed expansion; stage codes rather than names). Twin baselines: 01c refreshed, `merit/` added, `test_designer_twin.py` checks the Merit route.
- `docs/manual/USER_MANUAL.md` (+ self-contained `USER_MANUAL.html`): end-to-end user manual with screenshots of every screen from that run.

## 2026-10-14 — 0.5: severity candidates (Python tool), engine runner
- Python permitted on GC (owner). New stage 04a (`04a_fit_severity.yxmd`): the Python tool runs tools/python/sevfit.py (embedded) on the stage-04 fit data — LOGNORMAL, GAMMA, WEIBULL, PARETO, GPD by left-truncated, right-censored MLE; AIC, BIC, KS against Kaplan–Meier, mean excess at the APs, survival at thresholds; boundary fits flagged and not auto-selected; selection by `fit_selected`, else lowest AIC; inverse-survival grid of the selected distribution. Checks F01–F06.
- Stage 04 draws severity from that grid when `severity_source = PYTHON` (new default) or from the native Pareto on the same grid (`NATIVE`, exact). S07 stops a PYTHON run without a fresh 04a; stage 03 empties the fit. `out_sim_summary.sev_dist`.
- Results workbook: Severity sheet; data sheets out_fit_candidates, out_fit_mean_excess, out_fit_survival; warnings include checks_04a.
- Simulator: Python tool emulation (script run with a stand-in `ayx.Alteryx`), Log10. Python tool XML taken from a workflow Designer 2026.2 saved (2026-10-14: environment Python 3.13, numpy 2.1, pandas 2.3, scipy 1.16).
- `tools/run_pipeline.bat`: the whole chain with AlteryxEngineCmd.exe, stop on first error, log file.
- Verified: tests/test_sevfit.py (likelihood = scipy.stats, parameter recovery on five families, grid = scipy.stats survival, KS = scipy kstest, Pareto mean excess closed form, flags); stage 04 PYTHON (Pareto grid = closed form), GPD selection and S07 against the oracle; engine run on the laptop (2026-10-14 23:15, 25 s): 0 errors, 0 warnings, 04a equal to the simulator (same selection, flags, grid; log L equal), 04–06 equal to the oracles at every iteration, workbook recalculates with 0 errors and its exhibits (incl. Severity) equal pandas. Baselines refreshed.

## 2026-10-14 — 0.4: all phases built
- **Results workbook fix (Designer):** Designer's Overwrite Sheet stored the rewritten data sheets at `xl//xl/worksheets/…` in a workbook whose relationships use absolute targets (openpyxl's default), so Excel and openpyxl saw only the presentation sheets. A probe in Designer 2026.2 confirmed it (absolute: corrupt; relative or Excel/LibreOffice-saved: correct). `tools/excel/xlsx_norm.py` now writes the Control and Results workbooks with relative relationships (and fixed timestamps, so builds are byte-identical); test_stage07 guards it.
- **As-if fix:** the as-if premium was deflated to each treaty year's level while the as-if recoveries are trended to the quote year, which overstated the as-if loss ratio by (1 + trend)^(Q − ty). Both are now at the quote-year level (expiring rate trended one year on the year's exposure; PCT_EPI on the on-levelled premium); a new `premium_year_level` column sits against the actual, untrended recoveries.
- Designer twin, all stages (Designer 2026.2, synthetic data, 10,000 iterations): 00, 01a/b/c, 02–07 run with 0 errors and 0 warnings; checks and recon of 00–03 equal the simulator; stages 04, 05 and 06 equal the numpy oracles iteration by iteration; the Designer-written Results workbook recalculates with 0 formula errors and its premiums, C1, C2, expiring and as-if figures equal an independent pandas computation. Baselines refreshed in tests/reconciliation/designer_twin (new: stage05, stage07, checks/recon 01c, out_scenario_applied).
- Stage 05 per-event XoL (SUPPLIED / CLUSTER events, per-life costs, deterministic burning cost, experience Poisson plus scenario return periods, resampled lives and per-life costs, results gross and net of the programme PP option). Stop loss renumbered to stage 06 and extended: EV structures, NET_OF_XOL nets PP and EV, `4 PROGRAMME` structure (PP → EV → SL) with `evaluate_mode = PROGRAMME`.
- Stage 07 and the Results workbook: run log, warnings, recon, pricing (target premium with k·σ and loadings, upfront premium net of expected reinstatement premium, layer split), C1, C2 with reinstatement premium at each return period, expiring comparison and as-if, scenario summary kept across scenario runs.
- Scenario overrides (stage 00; SET / MULT / ADD on Trend, Completion, Weights, Params, FX, Layers, EvScenarios), AP-band post-AP completion factors (stage 03 and the fit threshold), NEGBIN frequency from Pearson overdispersion (stage 04), ETE adapter 01c (dim_life, fact_claims, fact_member_month; TPA filter, segment field, premium basis).
- Builders: Select drop lists keep their order (a set made one Select's XML vary between builds).

## 2026-10-14 (earlier)
- Designer twin of 05_stop_loss (Designer 2026.2, owner's laptop, after a fresh 00 and 04 at 10,000 iterations): 0 errors, 0 warnings, every attritional draw, gross amount, structure mean / SD and net-claims / recovery quantile equals the numpy oracle. Baseline stored in tests/reconciliation/designer_twin/stage05 and guarded by test_designer_twin.py. The regenerated 04 also ran clean (0 warnings).
- Phase 3, stage 05 (05_stop_loss), native Alteryx: attritional model (total claims less units above t_sl = max(sl_attritional_threshold, u); FGU trend; new `ATTRITIONAL` completion side; weighted cost per life; CoV with floor; lognormal or normal by Box–Muller on two new MINSTD streams), gross claims = attritional + simulated large claims above t_sl, net claims budget (default the mean simulated gross), aggregate stop loss on PCT_BUDGET / PCT_EPI / AMOUNT, GROSS or NET_OF_XOL (PP option from the Programme sheet), structures (without RI, each PP option, each SL option) with net-claims and recovery quantiles for Exhibits C1 and C2, exposed lives by option. Checks G01–G11. Exact match to an independent numpy oracle (line and claimant mode).
- Control: parameter `sl_attritional_dist`; Completion side `ATTRITIONAL` (synthetic instance filled from synthetic_truth.json, total claims). Stage 04 now stages sim_gross and sim_iterations (outputs unchanged); stage 01b writes an empty int_members so a claimant run never reads a stale line-mode file. Simulator: Cos added.
- Designer twin of 04_simulate (Designer 2026.2, 10,000 iterations, 1,232,974 simulated claims): every iteration's recovery, quantiles, alpha and lambda equal the independent numpy oracle. One Designer warning (Group By on a Double) fixed; the simulator now fails on any warning Designer would raise.
- Stage 04 (04_simulate), native Alteryx stochastic model: censored single-parameter Pareto fit above u, Poisson frequency by inverse CDF, seeded MINSTD streams (identical in Designer and the simulator), claims capped at drawn benefit limits, per-person layers with AAD / AAL / reinstatement usage, unused capacity above the largest observed claim, credibility blend, quantiles and per-iteration output. Exact match to an independent numpy oracle. Stage 03 now derives AAL = (1 + reinstatements) × limit and stages bc_adjusted / bc_weights.
- Designer twin run of 02_experience and 03_burning_cost (Designer 2026.2): 0 errors, every output cell matches the simulator (max rel diff 2e-14). Baseline extended in tests/reconciliation/designer_twin.
- Stage 02 (02_experience: treaty-year allocation, run-off, accumulation units, RA/LO exposure, months elapsed, indicated UNEARNED/UNWRITTEN factors) and stage 03 (03_burning_cost: trend indices, completion, benefit/policy limits, IQR or value outliers, per-person layering incl. TO_BENEFIT_LIMIT, post-AP completion, AAD/AAL, default/custom weights, credibility, sensitivities, leveraged trend, quote-year expected cost, annualisation and large-claim exhibits). Simulator vs pandas oracle: all checks pass. docs/methodology.md added. Simulator: Sqrt added.
- Designer twin run complete (Designer 2026.2, owner's laptop): stages 00, 01a, 01b all clean and identical to the simulator (recon diffs ≤ 5e-6). Designer outputs stored in tests/reconciliation/designer_twin and guarded by tests/test_designer_twin.py.
- First Designer run (26.1, owner's laptop): stage 00 failed on `True` (not a Designer constant). Builders now use 1=1 / 1=0 and the simulator rejects True/False. Re-run: stage 00 clean (0 errors, 0 warnings, 27 files) and matches the simulator. Designer 26.1 opens the 2025.2-format workflows without conversion.
- Phase 1 build started. Workflows are generated by Python builders (tools/alteryx, xmlkit/sim vendored from ete-dashboard), Designer 2025.2, LIVE and SYNTH variants; every workflow is executed in the simulator against an independent pandas oracle.
- Stage 00 (read and validate the Control workbook) and stage 01 adapters (line, claimant) built and tested; Control workbook template and synthetic instance.
- Cedant-impact exhibit format fixed to the Hollard deliverable: structures table (exposed lives, premium per exposed and per reference lives, expected recoveries, cost of cover, 1-in-N recoveries exceed premium) and surplus/(loss) against the net claims budget at return periods. Stop loss can be expressed as % of the net claims budget; per-person limits can run to the benefit limit.
- Data contracts v0.2: priced FX applied in the stage-01 adapter (quote-currency amounts alongside originals); checks A11-A13; recon files.
- Data contracts v0.1: input templates (line, claimant, ETE mapping to ete-dashboard fact_claims / dim_life / fact_member_month), internal schema, capability flags, Control and Results workbook tables, adapter checks A01–A10.
- Synthetic data generator and data set (all three input modes), truth file with IBNR / unearned / unwritten split, validation script.
- Scope v0.2: decisions D1–D9 recorded. Cover matrix extended to per-person, per-event and aggregate stop loss on RA and LO; standalone and ETE input modes; expiring-year completion split into IBNR / unearned / unwritten gross-ups with indicated values; EPI input; reinstatements; build phasing.
- Scope drafted from the WCXL pricing workshop framework, adapted to the broker view (expected cost, indicative RI cost, cedant impact); capital and risk transfer dropped; all data prep and stochastic modelling assigned to Alteryx.
- Repository scaffold created.
