# Designer twin run — 2026-10-14 (0.5: engine run with tools/run_pipeline.bat, log in engine_run.log)

Outputs written by **Alteryx Designer 2026.2** (AMP engine) on the owner's laptop, running the SYNTH workflows on
`data/synthetic/` at 10,000 iterations (line mode for stages 02–07). `tests/test_designer_twin.py` checks that the
simulator and the numpy oracles still reproduce them, so a builder change that would alter Designer's result is
caught here.

| Workflow | Designer run | Stored | Result |
|---|---|---|---|
| 00_read_control | 0 errors, 0 warnings | checks_00, out_scenario_applied | identical to the simulator |
| 01a / 01b / 01c adapters | 0 errors, 0 warnings | checks_01x, recon_01x | identical; recon diffs ≤ 1e-4 |
| 02_experience | 0 errors, 0 warnings | checks_02, recon_02, out_completion_indicated | identical (rel 1e-9) |
| 03_burning_cost | 0 errors, 0 warnings | checks_03, recon_03, out_bc_summary, out_bc_by_year, out_annualisation | identical (rel 1e-9) |
| 04a_fit_severity (Python tool) | 0 errors, 0 warnings | stage04a/ | candidates, flags, selection (PARETO), log-likelihoods and severity grid = simulator (scipy 1.16 vs local) |
| 04_simulate | 0 errors, 0 warnings | stage04/ | every iteration's recovery, quantiles, alpha, lambda = numpy oracle (rel 1e-7) |
| 05_per_event | 0 errors, 0 warnings | stage05/ | events, burning cost, every iteration (gross and net of PP), scenarios = numpy oracle |
| 06_stop_loss | 0 errors, 0 warnings | stage06/ | attritional draws, structures (PP, EV, SL), quantiles and reinstatement usage = numpy oracle |
| 07_results | 0 errors, 0 warnings | stage07/ (XoL_Results_designer.xlsx, checks_07) | all data sheets present with Designer's row counts; LibreOffice recalculation 0 errors; Pricing, C1, C2, Expiring and as-if = pandas computation |

History: the first run found `True`/`False` are not Designer constants (fixed in b4e4162); stage 04 once raised a
Group By on Double warning (fixed; the simulator now fails on any warning); stage 07 found Designer misplaces sheets
in workbooks with absolute relationships (fixed by `tools/excel/xlsx_norm.py`, 0.4).
