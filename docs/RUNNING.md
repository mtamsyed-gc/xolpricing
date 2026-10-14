# Running the tool (Designer 2025.2 format; tested on 2026.2)

## One-off setup
1. Clone or download the repo. Keep the folder structure: workflows find their files by relative path.
2. Open `excel/templates/XoL_Control.xlsx`, save a copy as **`data/input/XoL_Control.xlsx`** and fill it in
   (yellow cells; justify judgement inputs).

## Input files (standalone)
| Mode | Folder | Files (templates in `data/templates/`) |
|---|---|---|
| Line (`source_mode = L`) | `data/input/line/` | `members.csv`, `claims_lines.csv`, `run_meta.csv` |
| Claimant (`source_mode = C`) | `data/input/claimant/` | `claimants.csv`, `exposure_monthly.csv`, `claims_aggregate_monthly.csv`, `run_meta.csv` |

`data/input/` and `data/output/` are git-ignored: client data never goes into the repo.

## Run order
| Step | Workflow (`alteryx/workflows/`) | Writes |
|---|---|---|
| 1 | `00_read_control.yxmd` | `data/output/live/stage/ctl_*.yxdb`, `checks/checks_00.csv` |
| 2 | `01a_adapter_line.yxmd` **or** `01b_adapter_claimant.yxmd` | `stage/int_*.yxdb`, `checks/checks_01a.csv`, `checks/recon_01a.csv` |
| … | later stages (02–10) as they are built | |

A STOP check raises an Error in the Results window and cancels the run (workflow setting *Cancel running workflow on
error*). FLAG checks are written to the checks CSV only. Read `checks_NN.csv` after every step.

## Trying it on synthetic data
The same workflows in `alteryx/workflows/synth/` read `data/synthetic/` and write `data/output/synth/`. Run
`00_read_control.yxmd` then `01a_adapter_line.yxmd`; `checks_01a.csv` should hold only the `OK` row and
`recon_01a.csv` should show 55,370 lines in, 1,455 rejected, 0 unmatched, 53,915 out.

## Twin check
Done on the owner's laptop (Designer 2026.2, 2026-10-14): stages 00, 01a and 01b match the simulator; the
Designer outputs are stored in `tests/reconciliation/designer_twin/`. Repeat on the GC machine before first live use.

### Procedure
The simulator outputs for the synthetic run are reproducible with `python tests/run_all.py`. After running the SYNTH
workflows in Designer, compare `data/output/synth/checks/recon_01a.csv` with the values above; any difference
points to a Designer behaviour the simulator does not model, and should be reported back before relying on the
LIVE variant.

## Rebuilding (developers)
Workflows are generated, never edited by hand: change `tools/alteryx/build_stage*.py`, then
`python tools/alteryx/build_all.py` and `python tests/run_all.py`.

## Designer notes (from the laptop runs)
- Designer opens each workflow from the launcher in a new window; opening a large workflow can take minutes.
- A run's Results window should show 0 Errors; read `checks_NN.csv` for FLAGs.
