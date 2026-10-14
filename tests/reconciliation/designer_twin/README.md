# Designer twin run — 2026-10-14

Outputs written by **Alteryx Designer 2026.2** (AMP engine) on the owner's laptop, running the SYNTH workflows
on `data/synthetic/`. `tests/test_designer_twin.py` checks that the simulator still reproduces them, so a builder
change that would alter Designer's result is caught here.

| Workflow | Designer run | Result vs simulator |
|---|---|---|
| 00_read_control | 1.5 s, 0 errors, 0 warnings | checks identical (OK only) |
| 01a_adapter_line | 2.7 s, 0 errors, 0 warnings | 12 recon measures, max abs diff 5e-6 |
| 01b_adapter_claimant | 1.3 s, 0 errors, 0 warnings | 7 recon measures, max abs diff 9e-8; same A09 flag |
| 02_experience | 0 errors, 0 warnings | recon, indicated factors: max rel diff 7e-15 |
| 03_burning_cost | 1.5 s, 0 errors, 0 warnings | summary, by-year, annualisation, 544 large-claim rows, checks (B02): max rel diff 2e-15 |

First attempt found one defect the simulator had allowed: `True`/`False` are not Designer constants (fixed in b4e4162).
