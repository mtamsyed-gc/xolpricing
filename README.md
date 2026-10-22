# XoL Pricing Tool

Broker-side excess-of-loss pricing tool for medical (health) portfolios. For each candidate
structure (per-person XoL, per-event XoL, aggregate stop loss; RA or LO) it produces (A) expected layer claims cost, (B) an indicative RI premium on high-level
loadings, and (C) cedant impact — recoveries vs premium and retained-claims volatility.

**Alteryx** does all data preparation, burning cost, distribution fitting and simulation.
**Excel** holds the inputs (Control workbook) and the loadings, cedant-impact exhibits and charts
(Results workbook).

> Status (0.6, 2026-10-22): all phases built — Control (00), adapters for line, claimant and ETE / Merit data (01a/b/c; Merit v7 final outputs read as written),
> experience (02), per-person burning cost (03), severity candidates with the Python tool (04a) and simulation (04),
> per-event XoL (05), stop loss and programme structures (06), Results workbook with pricing, C1, C2, expiring,
> severity and scenarios (07). `tools\run_pipeline.bat` runs the chain with the Alteryx engine. Every workflow runs
> clean in Designer 2026.2 and matches the independent oracles exactly.
> How to run: [`docs/RUNNING.md`](docs/RUNNING.md). Design: [`docs/scope.md`](docs/scope.md),
> [`docs/data-contracts.md`](docs/data-contracts.md).

## Synthetic data

`python tools/synth/generate.py` rebuilds `data/synthetic/` and `tests/reconciliation/` (seed 20261014,
valuation 2025-09-30). It is a fictional UAE group-medical book (AED with some USD schemes, ~6–9k lives,
2020–2025 cohorts, 8% FGU trend, Pareto large-claim tail, plan benefit limits, three accumulation
events). `synthetic_truth.json` holds the ultimates by RA and LO year, split into reported / IBNR /
unearned / unwritten, so completion and trend logic can be tested against known answers.
`python tests/validate_synthetic.py` runs the stage-01 checks.

## Structure

```
alteryx/
  workflows/      .yxmd  generated workflows, one per stage (LIVE: data/input -> data/output/live)
    synth/        same workflows wired to data/synthetic (SYNTH variant)
  macros/         .yxmc  reusable macros (e.g. trending, capping, layer loss split)
  apps/           .yxwz  analytic apps, if a user-facing front end is needed
excel/
  templates/      XoL_Control.xlsx (inputs) and XoL_Results.xlsx (exhibits), both generated
data/
  templates/      header-only CSV input templates (standalone mode)
  synthetic/      synthetic portfolio in every input mode: line/, claimant/, ete/ (committed)
  input/          real run inputs (git-ignored, never committed)
  output/         run outputs (git-ignored)
docs/             scope.md, data-contracts.md, RUNNING.md, CHANGELOG.md
tools/alteryx/    workflow builders (build_all.py), xmlkit + strict simulator (vendored from ete-dashboard)
tools/excel/      Control and Results workbook specs and builders
tools/python/     sevfit.py: severity fitting, embedded in the 04a Python tool
tools/run_pipeline.bat   runs every workflow with AlteryxEngineCmd.exe
tools/synth/      synthetic data generator (seeded, reproducible)
tests/
  run_all.py              every test + structural check of the generated workflows
  validate_synthetic.py   reference implementation of the stage-01 adapter checks
  test_stage00.py … test_stage07.py   simulator runs vs independent oracles, incl. broken-input cases
  test_designer_twin.py   the simulator and oracles reproduce Designer's stored outputs
  twin_stage04/05/06.py   compare a Designer run with the numpy oracles
  reconciliation/         synthetic_controls.json (observed totals), synthetic_truth.json (ultimates)
```

## Conventions

- **Synthetic data only in git.** No cedant data, TPA headers or client names are committed.
- **Relative paths in Alteryx.** Before committing a workflow, use *Options → Advanced Options →
  Workflow Dependencies → All Relative* so it runs from any clone location.
- **Workflows are generated, not hand-edited.** Change the builder, run `tools/alteryx/build_all.py`, then
  `tests/run_all.py`. Only Designer tools whose XML is taken from the team's 2025.2 workflows are used.
- **Workbook code lives in `excel/vba/` as well as in the workbook** (if VBA is ever needed). Export modules after
  every change so VBA changes are reviewable in diffs.
- **Numbered stages.** Workflows are named `NN_stage_name.yxmd` (e.g. `01_load_claims.yxmd`) and
  each stage writes a control total that the next stage reconciles to.
- **Commit messages** state what changed and why; methodology changes also get an entry in
  `docs/CHANGELOG.md`.

## Getting started

1. Clone the repo.
2. Open workflows in Alteryx Designer from `alteryx/workflows/`.
3. Follow [`docs/RUNNING.md`](docs/RUNNING.md): Control workbook, then workflows 00 → 07, then the Results workbook.
4. Point inputs at `data/synthetic/` for test runs, or `data/input/` for live runs.
