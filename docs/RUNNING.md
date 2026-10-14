# Running the tool (Designer 2025.2 format; tested on 2026.2)

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
| ETE extension (`E`) | `data/input/ete/` | `dim_life.csv`, `fact_claims.csv`, `fact_member_month.csv` from the ete pipeline, plus `run_meta.csv` |

`data/input/` and `data/output/` are git-ignored: client data never goes into the repo.

## Run order (`alteryx/workflows/`, outputs under `data/output/live/`)
| Step | Workflow | Writes |
|---|---|---|
| 1 | `00_read_control.yxmd` | `stage/ctl_*.yxdb`, `checks/checks_00.csv`, `checks/out_scenario_applied.csv` |
| 2 | **one of** `01a_adapter_line.yxmd`, `01b_adapter_claimant.yxmd`, `01c_adapter_ete.yxmd` | `stage/int_*.yxdb`, `checks/checks_01x.csv`, `checks/recon_01x.csv` |
| 3 | `02_experience.yxmd` | `stage/exp_*`, `checks/out_completion_indicated.csv` (copy the indicated factors into the Control workbook if you accept them) |
| 4 | `03_burning_cost.yxmd` | `results/out_bc_summary.csv`, `out_bc_by_year.csv`, `out_large_claims.csv`, `out_annualisation.csv` |
| 5 | `04_simulate.yxmd` | `results/out_sim_*.csv`, `out_fit.csv` |
| 6 | `05_per_event.yxmd` | `results/out_ev_*.csv` |
| 7 | `06_stop_loss.yxmd` | `results/out_attritional_by_year.csv`, `out_gross_*.csv`, `out_sl_iterations.csv`, `out_struct_*.csv`, `out_exposed_lives.csv` |
| 8 | `07_results.yxmd` | `XoL_Results.xlsx` data sheets (close the workbook in Excel first), `results/out_expiring.csv`, `out_asif_by_year.csv`, `out_run_log.csv`, … |

Run every step in order after changing the Control workbook. Stage 05 runs even without an EV layer (it then writes
empty tables); stage 06 needs a PP layer or `fit_cutoff` (the large claims come from stage 04) and the `ATTRITIONAL`
completion factors for incomplete treaty years. `evaluate_mode = PROGRAMME` adds the stacked structure of the
Programme sheet (PP → EV → SL).

A STOP check raises an Error in the Results window and cancels the run (workflow setting *Cancel running workflow on
error*). FLAG and INFO checks are written to the checks CSV and to the `out_warnings` sheet. Read `checks_NN.csv`
after every step.

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
`excel/templates/XoL_Results.xlsx` to `data/output/synth/` and run the eight steps with `01a_adapter_line.yxmd`.
Expected: `recon_01a.csv` shows 55,370 lines in, 1,455 rejected, 0 unmatched, 53,915 out; every checks file has no
STOP; the Pricing sheet shows nine options (PP 1–4, EV 1, SL 1–4) at 10,000 iterations.

## Twin check
Done on the owner's laptop (Designer 2026.2, 2026-10-14): all workflows, 00 to 07 and the three adapters, run with
0 errors and 0 warnings, and their outputs equal the simulator (stages 00–03) and the numpy oracles (04, 05, 06 at
10,000 iterations, every iteration); the Designer-written Results workbook recalculates with 0 formula errors and its
exhibits equal an independent pandas computation. Designer outputs are stored in
`tests/reconciliation/designer_twin/` and guarded by `tests/test_designer_twin.py`. Repeat on the GC machine before
first live use: run the synthetic steps, then `python tests/twin_stage06.py <folder with the Designer CSVs>` (and
`twin_stage04.py`, `twin_stage05.py`).

## Rebuilding (developers)
Workflows and both workbook templates are generated, never edited by hand: change `tools/alteryx/build_stage*.py` or
`tools/excel/*.py`, then `python tools/alteryx/build_all.py` and `python tests/run_all.py`. Builds are byte-identical
for the same source.

## Designer notes (from the laptop runs)
- Opening a large workflow can take minutes. Close a workflow's tab before replacing its file, or Designer runs the
  copy it has open.
- A run's Results window should show 0 Errors and 0 Warnings; read `checks_NN.csv` for FLAGs.
