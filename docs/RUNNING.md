# Running the tool (Designer 2025.2 format; tested on 2026.2)

Users: start with the illustrated manual, `docs/manual/USER_MANUAL.md` (or `USER_MANUAL.html`). This page is the
developer's run guide.

## One-off setup
1. Clone or download the repo. Keep the folder structure: workflows find their files by relative path.
2. Open `excel/templates/XoL_Control.xlsx`, save a copy as **`data/input/XoL_Control.xlsx`** and fill it in
   (yellow cells; justify judgement inputs).
3. Copy `excel/templates/XoL_Results.xlsx` to **`data/output/live/XoL_Results.xlsx`** (once per pricing; stage 07
   fills its data sheets on every run and keeps the presentation sheets).

## Input files
| Mode (`source_mode`) | Folder | Files (templates in `data/templates/`) |
|---|---|---|
| Line (`L`) | `data/input/line/` | `members.csv`, `claims_lines.csv`, `run_meta.csv` |
| Claimant (`C`) | `data/input/claimant/` | `claimants.csv`, `exposure_monthly.csv`, `claims_aggregate_monthly.csv`, `run_meta.csv` |
| ETE / Merit (`E`) | `data/input/ete/` | Merit's final outputs as written: `dim_life.csv`, `fact_member_month.csv`, `fact_claims.csv`, `run_manifest_claims.csv` (copied by `tools\import_merit.bat`) |

`data/input/` and `data/output/` are git-ignored: client data never goes into the repo.

## Run order (`alteryx/workflows/`, outputs under `data/output/live/`)
| Step | Workflow | Writes |
|---|---|---|
| 1 | `00_read_control.yxmd` | `stage/ctl_*.yxdb`, `checks/checks_00.csv`, `checks/out_scenario_applied.csv` |
| 2 | **one of** `01a_adapter_line.yxmd`, `01b_adapter_claimant.yxmd`, `01c_adapter_ete.yxmd` | `stage/int_*.yxdb`, `checks/checks_01x.csv`, `checks/recon_01x.csv` |
| 3 | `02_experience.yxmd` | `stage/exp_*`, `checks/out_completion_indicated.csv` (copy the indicated factors into the Control workbook if you accept them) |
| 4 | `03_burning_cost.yxmd` | `results/out_bc_summary.csv`, `out_bc_by_year.csv`, `out_large_claims.csv`, `out_annualisation.csv` |
| 5 | `04a_fit_severity.yxmd` (Python tool) | `results/out_fit_candidates.csv`, `out_fit_mean_excess.csv`, `out_fit_survival.csv`, `out_sev_table.csv`, `stage/sev_table.yxdb`, `checks/checks_04a.csv` |
| 6 | `04_simulate.yxmd` | `results/out_sim_*.csv`, `out_fit.csv` |
| 7 | `05_per_event.yxmd` | `results/out_ev_*.csv` |
| 8 | `06_stop_loss.yxmd` | `results/out_attritional_by_year.csv`, `out_gross_*.csv`, `out_sl_iterations.csv`, `out_struct_*.csv`, `out_exposed_lives.csv` |
| 9 | `07_results.yxmd` | `XoL_Results.xlsx` data sheets (close the workbook in Excel first), `results/out_expiring.csv`, `out_asif_by_year.csv`, `out_run_log.csv`, … |

Run every step in order after changing the Control workbook. Step 5 (04a) fits the severity candidates with the Python
tool; stage 04 uses the selected one when `severity_source = PYTHON` (default) and stops (S07) if 04a has not run
since the last 03. Without Python, set `severity_source = NATIVE` and skip 04a: stage 04 then fits the Pareto itself.
Read the Severity sheet (or `out_fit_candidates.csv`) before pricing; override the selection with `fit_selected`. Stage 05 runs even without an EV layer (it then writes
empty tables); stage 06 needs a PP layer or `fit_cutoff` (the large claims come from stage 04) and the `ATTRITIONAL`
completion factors for incomplete treaty years. `evaluate_mode = PROGRAMME` adds the stacked structure of the
Programme sheet (PP → EV → SL).

A STOP check raises an Error in the Results window and cancels the run (workflow setting *Cancel running workflow on
error*). FLAG and INFO checks are written to the checks CSV and to the `out_warnings` sheet. Read `checks_NN.csv`
after every step.

## Pricing from Merit outputs
Merit (ete-dashboard `side_tools/merit`) is used as it is; its final outputs feed the XoL tool and every further
adjustment is made in the XoL Control workbook.
1. Run Merit as usual. Its output folder (e.g. `C:\Merit_Client\04_Outputs`) holds `csv\dim_life.csv` etc.
2. `tools\import_merit.bat "C:\Merit_Client\04_Outputs"` copies the four files the XoL tool reads into
   `data\input\ete\` (Merit's folder is not changed).
3. Control workbook: `source_mode = E`; `ete_payer_filter` = the payer to price (Merit can hold several payers; A16
   flags a pooled run); optionally `ete_tpa_filter`; `premium_basis` GROSS or NET; plan limits in the Limits sheet
   (Merit has no member limits); completion factors as usual (Merit's IBNR ultimates are not used); per-event
   scenarios in EvScenarios (Merit has no event fields).
4. `tools\run_pipeline.bat live 01c`.
5. Read `checks_01c.csv`: A04 lists claims treated after the valuation date (excluded: Merit's claims extract can run
   past it), A17 void life-periods that carry claims, A16 pooled payers.

`tools\check_merit.bat` repeats the whole route on Merit's synthetic outputs (`tests\fixtures\merit_v7`) through the
engine, with its own test Control, and removes its inputs afterwards.

## Running everything from the command line
`tools\run_pipeline.bat` runs the workflows in order with `AlteryxEngineCmd.exe` (no Designer window), stops at the
first error and logs to `data\output\<variant>\engine_run.log`. Double-click it for the synthetic data, or from cmd:

```
tools\run_pipeline.bat synth 01a        (default)
tools\run_pipeline.bat live 01b         live data, claimant adapter
tools\run_pipeline.bat live 01a 04a     start at 04a (00-03 already run)
tools\run_pipeline.bat live 01c 00 02   stop after 02 (fourth argument: last stage)
```

The synthetic chain takes about 25 seconds on the owner's laptop (Designer 2026.2 engine, 2026-10-14). Exit codes:
0 ok, 1 warnings, 2 errors (a STOP check is an error).

## The Results workbook
Open `data/output/<variant>/XoL_Results.xlsx` after stage 07. Excel recalculates on opening. **Inputs** sets the
display currency and unit, reference lives, EPI and budget, return periods, the expected-loss basis and loading
overrides; **Pricing**, **Reinsurance Structures** (C1), **Cedant Impact** (C2), **Expiring** and **Scenarios** follow.
Inputs changes need no re-run. Save a copy under another name to keep a version: stage 07 rewrites the data sheets
of this file on every run.

If the template is ever re-saved by a tool other than Excel, keep its workbook relationships relative (Excel's
default): with absolute ones, Designer's Overwrite Sheet stores the sheets where Excel cannot find them
(`tools/excel/xlsx_norm.py`).

## Sensitivity scenarios
1. Add rows to the Scenarios sheet of the Control workbook with a new `scenario_id` (e.g. 1 = trend + 2pp:
   target_table Trend, target_key ALL, target_column client_pct, operation ADD, override_value 0.02).
2. Set Params `scenario_id` to that id and run steps 1–8. `checks/out_scenario_applied.csv` shows the rows each
   override changed.
3. The Scenarios sheet of the Results workbook keeps every scenario's rows; set `scenario_id` back to blank (or 0) for
   the best estimate. The other sheets show the last run.

## Trying it on synthetic data
The same workflows in `alteryx/workflows/synth/` read `data/synthetic/` and write `data/output/synth/`. Copy
`excel/templates/XoL_Results.xlsx` to `data/output/synth/` (the batch file does it) and run the nine steps with
`01a_adapter_line.yxmd`.
Expected: `recon_01a.csv` shows 55,370 lines in, 1,455 rejected, 0 unmatched, 53,915 out; every checks file has no
STOP; 04a selects PARETO (lognormal, gamma and Weibull end as boundary fits, F06); the Pricing sheet shows nine
options (PP 1–4, EV 1, SL 1–4) at 10,000 iterations.

## Twin check
Done on the owner's laptop (Designer 2026.2, 2026-10-14; 0.5 through the engine with `run_pipeline.bat`): all
workflows, 00 to 07, 04a and the three adapters, run with 0 errors and 0 warnings, and their outputs equal the
simulator (stages 00–03, 04a) and the numpy oracles (04, 05, 06 at 10,000 iterations, every iteration); the Designer-written Results workbook recalculates with 0 formula errors and its
exhibits equal an independent pandas computation. Designer outputs are stored in
`tests/reconciliation/designer_twin/` and guarded by `tests/test_designer_twin.py`. Repeat on the GC machine before
first live use: run the synthetic steps, then `python tests/twin_stage06.py <folder with the Designer CSVs>` (and
`twin_stage04.py`, `twin_stage05.py`). On the GC machine also confirm the Python tool's environment (Designer's base environment needs
numpy, pandas, scipy) and Designer's version: the Python tool XML is taken from Designer 2026.2.

## Rebuilding (developers)
Workflows and both workbook templates are generated, never edited by hand: change `tools/alteryx/build_stage*.py` or
`tools/excel/*.py`, then `python tools/alteryx/build_all.py` and `python tests/run_all.py`. Builds are byte-identical
for the same source.

## Designer notes (from the laptop runs)
- Opening a large workflow can take minutes. Close a workflow's tab before replacing its file, or Designer runs the
  copy it has open.
- A run's Results window should show 0 Errors and 0 Warnings; read `checks_NN.csv` for FLAGs.
