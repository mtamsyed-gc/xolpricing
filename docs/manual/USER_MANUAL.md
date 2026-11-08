# XoL Pricing Tool — user manual

Version 0.9 · 7 November 2026 · screenshots taken on the owner's laptop (Windows 11, Excel 365, Chrome) during runs on
the synthetic portfolio: the page and the folders on 7 November 2026 (0.9), the workbook sheets on 22 October 2026.

The tool prices excess-of-loss reinsurance on a medical portfolio for the broker: expected claims cost per layer,
an indicative reinsurance premium from high-level loadings, and the cedant's position with each structure. It takes
claims and exposure data, a Control workbook of assumptions, and writes a Results workbook of exhibits. All data
work and simulation run in Python on your laptop, driven from a page in your browser; the assumptions and the
exhibits live in Excel. (Until 0.8 the stages also ran in Alteryx; from 0.9 Alteryx is not used.)

---

## 1. What you need

| Item | Where | Notes |
|---|---|---|
| The tool folder | `C:\Users\<you>\Documents\xolpricing` (any folder works; keep the structure) | From the release zip or `git clone`. Keep the path short and local (not OneDrive). |
| Python | 3.12 or later, installed per user | Runs every stage. Install the packages once, in a normal PowerShell window in the tool folder: `py -m pip install -r requirements-native.txt` (numpy, pandas, openpyxl, scipy). |
| A browser | Chrome or Edge | The page (`tools\xol_ui.bat`) runs everything and shows the results of every stage. |
| Excel | 2016 or later | Control workbook (inputs) and Results workbook (exhibits). No macros. |

The folder:

![Tool folder](img/01_repo_folder.jpg)

| Folder | Holds |
|---|---|
| `alteryx\workflows\` | The stage definitions, numbered in run order (internal: the tool runs them; the folder keeps its historical name) |
| `ui\` | The page and its guide |
| `data\input\` | **Your** inputs: the Control workbook and the data files (never committed) |
| `data\output\live\` | **Your** results: checks, results tables, the Results workbook, the run log |
| `excel\templates\` | `XoL_Control.xlsx` and `XoL_Results.xlsx`, the blank templates |
| `tools\` | The launchers: `xol_ui.bat` (the page), `run_pipeline.bat`, `import_merit.bat` |
| `docs\` | This manual, the method (`methodology.md`, `XOL_MECHANICS.md`), the data contracts |

The admin pack also holds `data\synthetic\` (a fictional portfolio for testing) and `tests\`.

The `tools` folder:

![tools folder](img/02_tools_folder.jpg)

| File | What it does |
|---|---|
| `xol_ui.bat` | Starts the page: the normal way to run the tool (section 5) |
| `run_pipeline.bat` | The same run from a command prompt, for scripted use (section 5.1) |
| `import_merit.bat` | Copies Merit's outputs into the tool's input folder (section 3.3) |
| `check_merit.bat` | Admin pack only: self-test of the Merit route on built-in synthetic data (section 8) |

---

## 2. One-off setup

1. Install the Python packages once (section 1).
2. Double-click `tools\xol_ui.bat`; the page opens. Set **Book** to *Live pricing*.
3. Copy `excel\templates\XoL_Control.xlsx` to `data\input\XoL_Control.xlsx` (the page offers the template with an
   **Open template** button until the file exists). This is the workbook you fill in.
4. Put your data in `data\input\line\`, `data\input\claimant\` or `data\input\ete\`, depending on the input mode
   (section 3). The page's **Open data\input** button opens the folder.
5. The Results workbook `data\output\live\XoL_Results.xlsx` is copied from the template on the first run. Stage 07
   fills its data sheets on every run and keeps your exhibits.

The page's **How to use** guide walks through the same steps with screenshots, adapted to your data source.

---

## 3. Input data

Three input modes. Set the mode with the `source_mode` parameter in the Control workbook.

### 3.1 Line mode (`L`): claim lines and a member list

The simplest route when the cedant gives claim-level data. Two CSV files plus a one-row `run_meta.csv` in
`data\input\line\`. Header-only templates are in `data\templates\`.

**`members.csv`**, one row per member per cover period:

| Column | Required | Content |
|---|---|---|
| member_id | yes | Unique per life, stable across years |
| policy_id | yes | Policy / contract id |
| scheme_id | yes | Employer group / master contract |
| policy_start, policy_end | yes | Policy inception and expiry |
| cover_start, cover_end | yes | The member's own cover dates |
| currency | yes | ISO code of the premium |
| written_premium | yes | Premium for that cover period |
| plan, annual_limit | recommended | Benefit limit (or a plan name looked up in the Control Limits sheet) |
| segment, site | optional | Segmentation; site for per-event grouping |

**`claims_lines.csv`**, one row per claim line, **all claims** (the stop loss needs the full book):

| Column | Required | Content |
|---|---|---|
| claim_line_id, claim_id | yes | Line id; claim id (reversals share it) |
| member_id | yes | Must match `members.csv` (95% or the run stops) |
| treatment_date | yes | Incurred date |
| amount | yes | Payer share, signed |
| status | yes | Paid / Outstanding / Rejected / Reversed (and common synonyms) |
| currency | yes | Transaction currency |
| reported_date, paid_date | recommended | Reported and paid dates |
| event_id, cause_code, admission_date | optional | Per-event cover from experience |

**`run_meta.csv`**: `valuation_date`, `currency_basis`.

Dates `yyyy-mm-dd`, numbers with `.` as the decimal point, UTF-8.

### 3.2 Claimant mode (`C`): large-claimant listing plus aggregates

When only a listing of large claimants is available: `claimants.csv`, `exposure_monthly.csv`,
`claims_aggregate_monthly.csv` and `run_meta.csv` in `data\input\claimant\`. Columns are in
`docs\data-contracts.md` §2.2. This mode has no member-level exposure and no event clustering.

### 3.3 Merit / ETE mode (`E`): Merit's final outputs

When the cedant is already processed by Merit (the interim MR pipeline), the tool reads Merit's final outputs as
they are. Nothing in Merit changes; every further adjustment (trend, completion, limits, layers) is made in the XoL
Control workbook.

A Merit output folder (here a test run) and its `csv` subfolder:

![Merit output folder](img/21_merit_output_folder.jpg)

![Merit csv folder](img/22_merit_csv_folder.jpg)

The tool reads four of these files: `dim_life.csv`, `fact_member_month.csv`, `fact_claims.csv` and
`run_manifest_claims.csv`. To copy them in, open a command prompt in the `tools` folder and run

```
import_merit.bat "C:\Merit_Client\04_Outputs"
```

(the path of Merit's output folder; the script finds the `csv` subfolder itself). It copies the four files to
`data\input\ete\` and leaves Merit's folder untouched. Then in the Control workbook set `source_mode = E` and
`ete_payer_filter` to the payer you are pricing (a Merit output can hold several payers).

What the tool does with Merit's data:

| Merit | XoL tool |
|---|---|
| One row per life per underwriting year | Kept as such: a renewing member has one row per year |
| Valuation date in the run manifest | Used as the valuation date. Claims treated after it (Merit's claims extract runs later) are excluded and listed in the checks file |
| Paid and outstanding amounts | Used. Merit's IBNR-completed ultimates are **not** used: your completion factors apply |
| No benefit-limit field; a claimant CAP shows as capped and excess payer share | A life-period whose CAP binds takes the cap as its limit; otherwise the Control Limits sheet, matched on `Plan / Category`; otherwise `default_benefit_limit`. Checks A18 / A19 count the CAP and default cases |
| No event fields | Per-event cover is priced from the scenarios you enter (EvScenarios sheet) |
| "Void ab initio" life-periods (blank cover dates) | Kept so their claims count, with no exposure; flagged |

---

## 4. The Control workbook

`data\input\XoL_Control.xlsx` holds every assumption. The tool reads each sheet by name; the header is always in
row 4. **Yellow** cells are required inputs, **blue** cells are optional (blank = the default the description names),
grey cells are labels or written back by the tool. Row 3 above every column says *required* or *optional*. Hover a
column header, or a Params value cell, for what to enter, the allowed options and an example; list inputs have a
drop-down of their options. The **Guide** sheet lists every sheet and column with the same information, and the
browser page shows it too (Input guide card) along with a readiness check of the Params sheet: a required value
left blank is named before you run. Every judgement input has a `justification` column that is carried into the
run log.

![Control README](img/03_control_readme.jpg)

### 4.1 Params

One row per parameter: `param_id`, `value`, with the allowed values and a description beside each. Required
parameters are marked. The run stops (check P01–P04) when a required value is blank, not of the right type, or not
in the allowed list.

![Params](img/04_control_params.jpg)

The parameters you will set on every pricing:

| Group | Parameter | Meaning |
|---|---|---|
| General | cedant, quote_ccy, treaty_start, quote_year | The placement; `treaty_start` must be the first of a month |
| Mode | source_mode | L, C or E (section 3) |
| Mode | ete_payer_filter, ete_tpa_filter | Merit mode: which payer / TPAs to price |
| Mode | premium_basis | GROSS, GROSSNET or NET premium for rates and EPI |
| Basis | treaty_basis, claims_trigger, pp_accumulation | RA or LO; incurred or paid; accumulation by policy year or treaty year |
| Exposure | epi_quote_year, expected_lives_quote_year | Expected premium and lives for the quote year |
| Data | years_included | The experience years, 2–5, comma-separated |
| Data | default_benefit_limit | Benefit limit for a life with none in the data or on the Limits sheet (blank = no limit) |
| Outliers | outlier_method, outlier_value | VALUE (an amount), IQR or NONE |
| Credibility | full_cred_n | Claims for full credibility (default 64) |
| Incidence | incidence_adj | Overall adjustment to expected claims (1 = none); justify |
| Programme | evaluate_mode | STANDALONE (each option alone) or PROGRAMME (stacked per the Programme sheet) |
| Stochastic | iterations, seed | 10,000 and a fixed seed; both go into the run log |
| Stochastic | severity_source, fit_selected | PYTHON uses the distribution selected in 04a; NATIVE the Pareto; `fit_selected` overrides the selection |
| Cedant impact | net_claims_budget, return_periods, currency_display | Budget for Exhibit C2 (blank = mean simulated gross); return periods; QUOTE or USD |

**The fitting cut-off (0.9).** `fit_cutoff` blank fits the severity distribution from the lowest active attachment,
which is the safe default. If you raise it, a layer attaching below it has a band of claims the fitted distribution does
not cover, and `below_cutoff` decides what happens: `STOP` (default: the run stops at stage 04 and names the layers),
`EMPIRICAL` (the band is simulated from the data's own claims and spliced with the fitted tail; S10 shows its share
of each layer), `IGNORE` (left out; S09 quotes the band's frequency and deterministic cost so you can justify it).
The page warns before the run.

### 4.2 Layers

Up to four options per cover. `cover` is PP (per person), EV (per event) or SL (stop loss). Per-person and
per-event layers take an attachment point, a limit (or `TO_BENEFIT_LIMIT`), AAD / AAL, co-participation and
reinstatements. Stop losses take `sl_measure` (PCT_BUDGET, PCT_EPI or AMOUNT), the attachment and limit on that
measure, and `sl_claims_basis` (GROSS or NET_OF_XOL). `active = 1` includes the row.

![Layers](img/05_control_layers.jpg)

### 4.3 Completion

Multiplicative factors by year, component (IBNR, UNEARNED, UNWRITTEN) and side: PRE_AP applies to each claim before
the attachment point; POST_AP_AMOUNT and POST_AP_COUNT to the layer amount and count after it; ATTRITIONAL to the
total claims used by the stop loss. `ap_band_from` lets a factor vary by attachment point (larger claims settle
more slowly). The tool writes an `indicated_factor` beside each unearned / unwritten input as a reference; the value
used is always your `selected_factor`. Read the caveat at the top of the sheet: portfolio development understates
development above an attachment point.

![Completion](img/06_control_completion.jpg)

### 4.4 Trend

The from-the-ground-up claims trend by calendar year, as weighted components (client, market, inflation) or a single
residual, with an optional one-off adjustment, a separate large-claim trend and the premium trend.

![Trend](img/07_control_trend.jpg)

### 4.5 Other sheets

| Sheet | Content |
|---|---|
| FX | Priced rate of each currency in the data to the quote currency |
| Limits | Benefit limit by plan (used when the data has no limit for the life; then `default_benefit_limit` in Params) |
| Weights | Months complete per year; default weights or custom weights with a justification |
| Programme | Order of application when `evaluate_mode = PROGRAMME`: PP → EV → SL, with the option used at each step |
| EventRule | How per-event claims are grouped: supplied event ids, or a clustering rule (window, grouping key, minimum lives) |
| EvScenarios | Named event scenarios: lives, cost per life, return period |
| Loadings | Reinsurer expenses, brokerage, profit margin, premium tax, risk load k, per cover (below) |
| Expiring | The expiring structure and rate, with its recoveries by year, for the comparison exhibit |
| Scenarios | Sensitivity overrides (below) |

![Loadings](img/08_control_loadings.jpg)

The indicative premium is `(expected loss + k × SD) / (1 − expenses − brokerage − profit − tax)`, net of the
expected reinstatement premium. The loadings can also be overridden on the Results workbook's Inputs sheet without a
re-run.

![Scenarios](img/09_control_scenarios.jpg)

A scenario is a set of rows with the same `scenario_id`, each changing one numeric column of one sheet (`SET`,
`MULT` or `ADD`). Set `scenario_id` in Params to run one; scenario 0 (blank) is the best estimate. Each scenario's
results are kept on the Results workbook's Scenarios sheet.

---

## 5. Running the tool

Double-click `tools\xol_ui.bat`. A small command window opens (leave it open: it is the page's server; closing it
closes the page) and the browser shows one page for the whole pricing. Nothing leaves your laptop. The Results
workbook must be **closed** in Excel when stage 07 runs.

![The page](img/10_page_top.png)

Choose the **Book** (*Live pricing*) and the **Data source** (adapter 01a, 01b or 01c, matching `source_mode`), check
that the Params table says *all required filled* and that every data file has a tick, then press **▶ Run**. The log
follows each stage; the chips turn green (OK), amber (flags) or red (STOP) as each finishes.

![A run in progress](img/10b_page_running.png)

The stages, in order:

| Stage | Name | Does |
|---|---|---|
| 00 | Read control | Reads and validates the Control workbook; applies the scenario |
| 01 | Adapter (a, b or c) | Reads your data into the tool's internal tables; data checks |
| 02 | Experience | Treaty years, exposure, indicated completion factors |
| 03 | Burning cost | Trend, completion, caps, layer losses, weights, credibility: the deterministic cost |
| 04a | Severity fit | Fits candidate severity distributions above the fitting cut-off |
| 04 | Simulate | Per-person simulation: frequency, severity, caps, layers, reinstatements; the band below the cut-off per `below_cutoff` |
| 05 | Per event | Event identification, event simulation with scenarios |
| 06 | Stop loss | Attritional model, gross claims, stop loss, programme, cedant-impact inputs |
| 07 | Results | Writes every table to the Results workbook; run log and warnings |

On a laptop the full chain at 10,000 iterations takes about 7–11 minutes, most of it stage 04, and uses up to about
9 GB of memory; on a 16 GB laptop close other programs or set `iterations` to 2,000–5,000 for working runs. A STOP stops
the run at that stage and opens its card (section 6).

![A stage card with a flag](img/10c_stage_card.png)

After a run the output folder (`data\output\live\`, **Open output folder** on the page) holds the checks, the results
tables, the staging files, the Results workbook and the run log `python_run.log`.

### 5.1 From a command prompt

`tools\run_pipeline.bat` runs the same stages with the same outputs, without the page (for scripted or overnight
runs). The log is `data\output\live\python_run.log`.

| Command (from a command prompt in `tools`) | Runs |
|---|---|
| `run_pipeline.bat live 01a` | Your data, line mode, all stages |
| `run_pipeline.bat live 01b` | Your data, claimant mode |
| `run_pipeline.bat live 01c` | Your data, Merit / ETE mode |
| `run_pipeline.bat live 01a 04a` | From stage 04a onwards (when 00–03 have already run) |
| `run_pipeline.bat live 01c 00 02` | Stages 00 to 02 only |

The results are those Alteryx Designer produced when the tool still used it: the Designer outputs of 14 October 2026
are kept with the tests and every test run checks that the tool still reproduces them, and stage 04 has also been
rebuilt independently from its specification and reconciled iteration by iteration (`tests\independent\`).

### 5.2 The browser page

Double-click `tools\xol_ui.bat`. A command window opens (leave it open) and the browser shows one page for the whole
pricing, top to bottom:

| Section | What you do there |
|---|---|
| Inputs | Choose the book (live; the admin pack also offers the synthetic book) and the data source. **Open in Excel** opens the Control workbook; after you save it, the page shows a banner and a **Re-run** button. The data files the chosen source needs are listed with a tick or a cross. |
| Run | Choose the first and last stage and press **Run**. The log follows the Python route live; the row of stage chips turns green, amber or red as each stage finishes. **Stop** ends the run. |
| Stages 00 to 07 | One card per stage. The pill on the right says OK, how many flags, or STOP. Open a card for the check messages, the reconciliation counts and the stage's result tables; **Open CSV** opens any of them in Excel. A card with a STOP opens by itself: fix what it names and re-run from 00. |
| Results workbook | **Open Results in Excel** after stage 07; **Save a dated copy** keeps a version before the next run overwrites the data sheets. |

**How to use** in the page header opens the interactive guide (`ui\xol\guide.html`): choose your data source and
follow the nine steps, each with a screenshot and a "Done when" box.

Grey "earlier run" pills mean that stage's outputs are older than the current run (for instance after running
00 to 03 only). Everything the page shows is in `data\output\<live|synth>\` as before; the page is a window on it,
not a second copy.

---

## 6. Checks

Every stage writes `checks\checks_NN.csv` with one row per finding:

| Severity | Meaning | What to do |
|---|---|---|
| STOP | The run cannot continue (a required input missing, a match rate below 95%, …) | Fix the input and re-run |
| FLAG | Something to review (claims after the valuation date, low credibility, several payers pooled, …) | Read it; decide whether it changes the pricing |
| INFO | For the record (which severity distribution was selected, …) | — |

The same rows appear on the Results workbook's `out_warnings` sheet, and `checks\recon_NN.csv` carries the control
totals of each stage (rows and amounts in and out), so you can follow every exclusion. The codes are listed in
`docs\data-contracts.md` §6–7.

---

## 7. The Results workbook

Open `data\output\live\XoL_Results.xlsx` after stage 07. Excel recalculates on opening; every exhibit is a formula
over the data sheets, so a change on the Inputs sheet updates everything without a re-run.

![Results README](img/12_results_readme.jpg)

### 7.1 Inputs

Display currency and unit, reference lives, the EPI and net claims budget, the return periods, the expected-loss
basis and the loadings. The "From the run" column shows the Control values; yellow cells override them.

![Inputs](img/13_results_inputs.jpg)

### 7.2 Pricing

One row per option: the deterministic, stochastic and blended expected loss, the SD of annual recoveries, the
loadings, the target premium, the expected reinstatement usage and the upfront premium, the rate per life and as %
of EPI, and the chance that recoveries exceed the premium.

![Pricing](img/14_results_pricing.jpg)

### 7.3 Reinsurance Structures (Exhibit C1)

The client view of each option: exposed lives, premium, premium per exposed life and per reference lives, expected
recoveries, expected cost of cover, and the return period at which recoveries exceed the premium.

![Exhibit C1](img/15_results_c1.jpg)

### 7.4 Cedant Impact (Exhibit C2)

The cedant's surplus or loss against the net claims budget, in an average year and at each return period, without
reinsurance and with each structure (and the stacked programme when `evaluate_mode = PROGRAMME`). The workings below
the exhibit show the premiums, mean net claims and quantiles behind each cell.

![Exhibit C2](img/16_results_c2.jpg)

### 7.5 Premium Terms (swing rating and MDP)

For quoting an option on **swing** terms instead of a flat rate, and for the **minimum & deposit premium**. Nothing
here needs a re-run, and the yellow inputs stay in the workbook when stage 07 runs again.

*Swing rating* (one row per option). Set **Swing** to YES, then enter the **swing loading** (100/80 = 1.25), the
**min rate** and the **max rate** in the rate basis shown (% EPI as a percentage, e.g. 1.85 %; PPPY as an amount per
life). The rate basis follows the expiring layer (PPPY or % EPI); override it in the yellow cell. The provisional
rate is optional (blank: the flat technical rate). The Status column says *STOP* until loading, min and max are
complete. For each of the 999 simulated years the swing premium is loading × losses, kept between the min and max
premium; the sheet shows its average against the flat technical premium, how often the min and max apply, the
reinsurer's expected loss ratio, the swing premium at your return periods, the expected adjustment against the
provisional premium and the **break-even min rate** (the min rate at which swing earns the same as the flat premium).
A swing-rated option has no paid reinstatements. Exhibit C1 shows the expected swing premium and its min–max range.

*MDP* (one row per option). Enter the MDP % (e.g. 85 %) and the instalments per year. The deposit premium is the
provisional premium (swing) or the upfront premium (flat). Edit the exposure scenarios in the yellow row (default
−20 % to +20 % of the estimate) to see the final premium and the additional premium due at each; the MDP is never
returned. The adjustment assumes claims move in proportion to exposure.

### 7.6 Expiring

The expiring rate trended against the indicated premium of the matching option, and the as-if experience of that
option by treaty year.

![Expiring](img/17_results_expiring.jpg)

### 7.7 Severity

The candidate distributions fitted in stage 04a: parameters, log-likelihood, AIC, BIC, KS, whether a fit ran to a
boundary, which one was selected and which one the simulation used. Below: the mean excess at each attachment point
(fitted against the data) and the survival at the diagnostic thresholds. Review this sheet before relying on the
stochastic result; override the selection with `fit_selected` in the Control workbook if you disagree with it.

![Severity](img/18_results_severity.jpg)

### 7.8 Scenarios

One row per sensitivity run, for the structure chosen in the yellow cell. Runs with different `scenario_id`s
accumulate; a re-run of the same id replaces its rows.

![Scenarios](img/19_results_scenarios.jpg)

### 7.9 Data sheets

The `out_*` sheets are written by the tool and replaced on every run. `out_run_log` lists the tool version, the run
time, every parameter with its justification and the data mode; `out_warnings` every check; `out_recon` the control
totals. Do not edit them.

![Data sheet](img/19b_results_datasheet.jpg)

---

## 8. Self-test of the Merit route (admin pack)

`tools\check_merit.bat` runs the Merit route on the synthetic Merit outputs that ship with the admin pack
(`tests\fixtures\merit_v7`): it places a test Control workbook and the four files in `data\input`, runs stages 00
to 02, removes them again, and then runs the synthetic ETE files through the same stages. It refuses to start when
`data\input` already holds your own Control workbook or data. It takes about a minute and ends with *CHECK PASSED*.

Run it after installing the tool on a new computer, and after a Merit release, before pricing from Merit data.

---

## 9. A pricing, start to finish

1. Get the data in (section 3). For a Merit cedant: run Merit, then `import_merit.bat "<Merit output folder>"`.
2. Fill in `data\input\XoL_Control.xlsx` (section 4). Justify every judgement input.
3. Close the Results workbook if it is open. In the page: Book *Live pricing*, the adapter of your data, **▶ Run**.
4. Read every stage card for STOPs and FLAGs. Fix and re-run if needed.
5. Read the stage 02 card (`out_completion_indicated`). If you accept the indicated unearned / unwritten factors, copy
   them into the Completion sheet and re-run from stage 00.
6. Open the Results workbook. Review the Severity sheet, then Pricing, C1 and C2. Set the display currency and
   unit and any loading override on the Inputs sheet.
7. For sensitivities: add Scenarios rows, set `scenario_id`, re-run from stage 00; the Scenarios sheet collects them.
8. Save a copy of the Results workbook under the placement's name: stage 07 rewrites the data sheets of the file in
   `data\output\live` on every run.

---

## 10. When something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| The page does not open / "site can't be reached" | The server window was closed, or port 8765 is taken | Double-click `tools\xol_ui.bat` again; or `tools\xol_ui.bat 9000` |
| "Python packages missing" in the log | The packages are not installed for this Python | `py -m pip install -r requirements-native.txt` in a normal PowerShell window |
| Stage 00 stops with P01–P04 | A required parameter blank, wrong type or not in the list | Fix the Params sheet |
| Stage 00 stops with P04 on `default_benefit_limit` | A Control workbook made before 0.7 | Add the row to Params (copy it from `excel\templates\XoL_Control.xlsx`; a blank value means no default limit) |
| "needs the stage files of a run of stage 00 by this tool" | The stage folder holds files of an old Alteryx run | Run from stage 00 |
| Stage 04 stops with a memory error | Too little free memory for the iterations | Close other programs, or lower `iterations` |
| Stage 01 stops with A03 | Fewer than 95% of claims match a member | Check the member id in both files |
| Stage 04 stops with S08 | `fit_cutoff` is above a layer's attachment and `below_cutoff` = STOP | Lower `fit_cutoff`, or set `below_cutoff` to EMPIRICAL / IGNORE (§4.1) |
| Stage 04 stops with S07 | `severity_source = PYTHON` but 04a has not run since the last 03 | Run from 04a, or set NATIVE |
| Stage 04a fails on `import scipy` | scipy not installed | Install the packages (section 1), or set `severity_source = NATIVE` and run from 04 |
| Stage 07 "cannot access the file" | The Results workbook is open in Excel | Close it and run from 07 |
| Exhibits show blanks | The workbook was not recalculated, or the run stopped before 07 | Check the log; open the workbook again |
| A sheet is missing from the workbook | A template re-saved by a tool other than Excel | Copy a fresh `excel\templates\XoL_Results.xlsx` |

The run log, `python_run.log` in the output folder, holds every message, stage by stage; the page shows it live.

---

## 11. Where the method is written up

- `docs\methodology.md`: every formula, stage by stage, with its checks.
- `docs\data-contracts.md`: every input column, every output column, every check code.
- `docs\RUNNING.md`: the developer's run guide, including rebuilding the stage definitions.
- `docs\CHANGELOG.md`: what changed in each version.
