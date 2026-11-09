# Running the tool (0.9.1: Python only; page on the GC UI kit)

Users: start with the page's **How to use** guide (`ui/xol/guide.html`) or the illustrated manual
(`docs/manual/USER_MANUAL.md` / `.html`). This page is the developer's run guide.

From 0.9.0 the tool runs in Python only. The stage definitions in `alteryx/workflows/` were first written for Alteryx
Designer; they are now executed by `tools/alteryx/sim.py`, which reproduces Designer's runs exactly (see *Verification
kept from the Alteryx period*). Alteryx is not installed, launched or needed.

## One-off setup
1. Unzip the user pack (or clone the repo). Keep the folder structure: the stages find their files by relative path.
2. Python 3.12 or later. Once, in a normal PowerShell window in the tool folder:
   `py -m pip install -r requirements-native.txt` (numpy, pandas, openpyxl, scipy from GC Artifactory).
3. Copy `excel/templates/XoL_Control.xlsx` to **`data/input/XoL_Control.xlsx`** and fill it in (yellow = required,
   blue = optional; hover a header or value for help; the Guide sheet lists every input; justify judgement inputs).
   The page offers the template when the file is missing.
4. The Results workbook (`data/output/live/XoL_Results.xlsx`) is copied from the template on the first run.

## Running
- **The page** (normal use): double-click `tools\xol_ui.bat`. It starts `ui\xol\serve.py`: the vendored **GC UI kit**
  server (`ui/kit/gc_server.py`, standard library only, kit build in `ui/kit/VERSION`) with this tool's manifest
  (`ui/xol/manifest.json`) and plugin (`ui/xol/xol_plugin.py`), and opens `http://127.0.0.1:8765/`. Sections:
  Overview (book and adapter, pre-run checklist), Inputs (the Control workbook edited in the page or in Excel —
  yellow = required, blue = optional, help per parameter, save keeps a backup in `data/output/ui_state/backups`
  and is refused while Excel holds the file; Params readiness including the fitting cut-off against layer
  attachments; the Input guide; the data files the adapter reads), Run and stages (from / to stage, live log,
  timeline, a card per stage with checks and tables), Results (Open in Excel, dated copy), Help (interactive guide,
  manual, documents, glossary, A–Z index; Ctrl+K searches everything). Keep the server window open; another port:
  `tools\xol_ui.bat 9000`. The server reads and opens files only under the manifest's `allowed_roots` (`data/`,
  `excel/templates/`, `ui/`, `docs/`), opens only workbook / CSV / text files, writes only the two Control workbooks
  (with backup) and `data/output/ui_state/` (run logs, action audit), never deletes, and runs one pipeline at a time.
  The kit is vendored from `mtamsyed-gc/gc-ui-kit` with its `tools/vendor.py`; never edit `ui/kit/` by hand
  (`tests/test_ui.py` checks the hashes). Page spec: `UI_STANDARD.md` in that repo (mirrored in ete-dashboard
  `docs/UI_STANDARD.md`). Tests: `tests/test_ui.py`.
- **Command line** (scripted runs): `tools\run_pipeline.bat` (or `py tools\run_python.py`), same stages and outputs:

```
tools\run_pipeline.bat synth 01a        synthetic book (admin pack), line adapter
tools\run_pipeline.bat live 01c         live data, Merit adapter
tools\run_pipeline.bat live 01a 04a     start at 04a (00-03 already run)
tools\run_pipeline.bat live 01c 00 02   stop after 02 (fourth argument: last stage)
```

  Stops at the first stage that ends with an error (a STOP check is an error); log `data\output\<variant>\python_run.log`;
  exit code 0 ok, 2 stopped or bad arguments. A run that starts after stage 00 needs this tool's own stage files
  (`stage/ENGINE.txt`); stage folders left by an old Alteryx run are refused (start from 00).
- **Time and memory**: on the owner's laptop the full chain at 10,000 iterations takes about 7–11 minutes, stage 04
  about 5–8 of them, peak Python memory about 9 GB (2026-11-06/07). On a 16 GB laptop close other programs, or set
  `iterations` to 2,000–5,000 for working runs. (0.10 rewrites stage 04 natively; see the CHANGELOG.)

## Input files
| Mode (`source_mode`) | Folder | Files (templates in `data/templates/`) |
|---|---|---|
| Line (`L`) | `data/input/line/` | `members.csv`, `claims_lines.csv`, `run_meta.csv` |
| Claimant (`C`) | `data/input/claimant/` | `claimants.csv`, `exposure_monthly.csv`, `claims_aggregate_monthly.csv`, `run_meta.csv` |
| ETE / Merit (`E`) | `data/input/ete/` | Merit's final outputs as written: `dim_life.csv`, `fact_member_month.csv`, `fact_claims.csv`, `run_manifest_claims.csv` (copied by `tools\import_merit.bat`) |

`data/input/` and `data/output/` are git-ignored: client data never goes into the repo.

## Stages (definitions in `alteryx/workflows/`, outputs under `data/output/live/`)
| Step | Stage | Writes |
|---|---|---|
| 1 | `00_read_control` | `stage/ctl_*`, `checks/checks_00.csv`, `checks/out_scenario_applied.csv` |
| 2 | **one of** `01a_adapter_line`, `01b_adapter_claimant`, `01c_adapter_ete` | `stage/int_*`, `checks/checks_01x.csv`, `checks/recon_01x.csv` |
| 3 | `02_experience` | `stage/exp_*`, `checks/out_completion_indicated.csv` (copy the indicated factors into the Control workbook if you accept them) |
| 4 | `03_burning_cost` | `results/out_bc_summary.csv`, `out_bc_by_year.csv`, `out_large_claims.csv`, `out_annualisation.csv` |
| 5 | `04a_fit_severity` | `results/out_fit_candidates.csv`, `out_fit_mean_excess.csv`, `out_fit_survival.csv`, `out_sev_table.csv`, `checks/checks_04a.csv` |
| 6 | `04_simulate` | `results/out_sim_*.csv`, `out_fit.csv`; band below the cut-off per `below_cutoff` (S08–S11) |
| 7 | `05_per_event` | `results/out_ev_*.csv` |
| 8 | `06_stop_loss` | `results/out_attritional_by_year.csv`, `out_gross_*.csv`, `out_sl_iterations.csv`, `out_struct_*.csv`, `out_exposed_lives.csv` |
| 9 | `07_results` | `XoL_Results.xlsx` data sheets (close the workbook in Excel first), `results/out_expiring.csv`, `out_asif_by_year.csv`, `out_run_log.csv`, … |

Run every step in order after changing the Control workbook. Step 5 (04a) fits the severity candidates; stage 04 uses
the selected one when `severity_source = PYTHON` (default) and stops (S07) if 04a has not run since the last 03;
`severity_source = NATIVE` skips the candidates and fits a single-parameter Pareto in stage 04. Read the Severity sheet
(or `out_fit_candidates.csv`) before pricing; override the selection with `fit_selected`. Stage 05 runs even without an
EV layer (it then writes empty tables); stage 06 needs a PP layer or `fit_cutoff` (the large claims come from stage 04)
and the `ATTRITIONAL` completion factors for incomplete treaty years. `evaluate_mode = PROGRAMME` adds the stacked
structure of the Programme sheet (PP → EV → SL).

A STOP check stops the run at that stage. FLAG and INFO checks are written to the checks CSV and to the `out_warnings`
sheet; the page shows them on each stage card.

## Pricing from Merit outputs
Merit (ete-dashboard `side_tools/merit`) is used as it is; its final outputs feed the XoL tool and every further
adjustment is made in the XoL Control workbook.
1. Run Merit as usual. Its output folder (e.g. `C:\Merit_Client\04_Outputs`) holds `csv\dim_life.csv` etc.
2. `tools\import_merit.bat "C:\Merit_Client\04_Outputs"` copies the four files the XoL tool reads into
   `data\input\ete\` (Merit's folder is not changed).
3. Control workbook: `source_mode = E`; `ete_payer_filter` = the payer to price (Merit can hold several payers; A16
   flags a pooled run); optionally `ete_tpa_filter`; `premium_basis` GROSS or NET; benefit limits: a life-period
   whose Merit CAP binds takes the cap, else its plan's limit on the Limits sheet, else `default_benefit_limit`
   (Params; blank = no limit); completion factors as usual (Merit's IBNR ultimates are not used); per-event
   scenarios in EvScenarios (Merit has no event fields).
4. In the page: Book *Live pricing*, adapter *01c*, Run (or `tools\run_pipeline.bat live 01c`).
5. Read `checks_01c.csv`: A04 lists claims treated after the valuation date (excluded: Merit's claims extract can run
   past it), A17 void life-periods that carry claims, A16 pooled payers, A18 / A19 how many life-periods take their
   limit from a Merit CAP / from the default.

`tools\check_merit.bat` (admin pack) repeats the whole route on Merit's synthetic outputs (`tests\fixtures\merit_v7`)
with its own test Control, and removes its inputs afterwards.

## The Results workbook
Open `data/output/<variant>/XoL_Results.xlsx` after stage 07. Excel recalculates on opening. **Inputs** sets the
display currency and unit, reference lives, EPI and budget, return periods, the expected-loss basis and loading
overrides; **Pricing**, **Reinsurance Structures** (C1), **Cedant Impact** (C2), **Expiring** and **Scenarios** follow.
Inputs changes need no re-run. Save a copy under another name to keep a version: stage 07 rewrites the data sheets
of this file on every run.

If the template is ever re-saved by a tool other than Excel, keep its workbook relationships relative (Excel's
default; `tools/excel/xlsx_norm.py` writes the templates that way).

## Sensitivity scenarios
1. Add rows to the Scenarios sheet of the Control workbook with a new `scenario_id` (e.g. 1 = trend + 2pp:
   target_table Trend, target_key ALL, target_column client_pct, operation ADD, override_value 0.02).
2. Set Params `scenario_id` to that id and run steps 1–8. `checks/out_scenario_applied.csv` shows the rows each
   override changed.
3. The Scenarios sheet of the Results workbook keeps every scenario's rows; set `scenario_id` back to blank (or 0) for
   the best estimate. The other sheets show the last run.

## Trying it on synthetic data (admin pack)
The page's *Synthetic book* (or `tools\run_pipeline.bat synth 01a`) reads `data/synthetic/` with the stage definitions
in `alteryx/workflows/synth/` and writes `data/output/synth/`.
Expected: `recon_01a.csv` shows 55,370 lines in, 1,455 rejected, 0 unmatched, 53,915 out; every checks file has no
STOP; 04a selects PARETO (lognormal, gamma and Weibull end as boundary fits, F06); the Pricing sheet shows nine
options (PP 1–4, EV 1, SL 1–4) at 10,000 iterations.

## Verification kept from the Alteryx period
Until 0.8 the stages also ran in Alteryx Designer. The Designer outputs of 2026-10-14 (synthetic book, 10,000
iterations, every stage and adapter) are kept in `tests/reconciliation/designer_twin/` and `tests/test_designer_twin.py`
checks on every test run that the Python executor still reproduces them (stages 00-03 and 04a equal, 04-06 every
iteration, the Results workbook's exhibits). `tests/independent/` holds the clean-room reconciliation of stage 04
(0.8.0, 0.9.0). Designer is no longer needed to run, test or release the tool.

## Rebuilding (developers)
The stage definitions (`alteryx/workflows/*.yxmd`, the folder keeps its historical name) and both workbook templates
are generated, never edited by hand: change `tools/alteryx/build_stage*.py` or `tools/excel/*.py`, then
`python tools/alteryx/build_all.py` and `python tests/run_all.py`. Builds are byte-identical for the same source.
Release packs: `python tools/release/make_packs.py` writes `dist/xolpricing_user_<ver>.zip` (no synthetic data,
tests, builders or reference material; the page offers the live book only) and `dist/xolpricing_admin_<ver>.zip`
(the whole repository).
