# XoL Pricing Tool

Broker-side excess-of-loss pricing tool for medical (health) portfolios. For each candidate
structure (per-person XoL, per-event XoL, aggregate stop loss; RA or LO) it produces (A) expected layer claims cost, (B) an indicative RI premium on high-level
loadings, and (C) cedant impact — recoveries vs premium and retained-claims volatility.

**Alteryx** does all data preparation, burning cost, distribution fitting and simulation.
**Excel** holds the inputs (Control workbook) and the loadings, cedant-impact exhibits and charts
(Results workbook).

> Status: scope v0.2 agreed; data contracts v0.1 and synthetic data in place — see
> [`docs/scope.md`](docs/scope.md) and [`docs/data-contracts.md`](docs/data-contracts.md).

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
  workflows/      .yxmd  data-prep workflows (one per stage, numbered)
  macros/         .yxmc  reusable macros (e.g. trending, capping, layer loss split)
  apps/           .yxwz  analytic apps, if a user-facing front end is needed
excel/
  templates/      pricing workbook template(s) (.xlsm / .xltx)
  vba/            exported VBA modules (.bas/.cls) — the diffable copy of workbook code
data/
  templates/      header-only CSV input templates (standalone mode)
  synthetic/      synthetic portfolio in every input mode: line/, claimant/, ete/ (committed)
  input/          real run inputs (git-ignored, never committed)
  output/         run outputs (git-ignored)
docs/             scope.md, data-contracts.md, CHANGELOG.md
tools/synth/      synthetic data generator (seeded, reproducible)
tests/
  validate_synthetic.py   reference implementation of the stage-01 adapter checks
  reconciliation/         synthetic_controls.json (observed totals), synthetic_truth.json (ultimates)
```

## Conventions

- **Synthetic data only in git.** No cedant data, TPA headers or client names are committed.
- **Relative paths in Alteryx.** Before committing a workflow, use *Options → Advanced Options →
  Workflow Dependencies → All Relative* so it runs from any clone location.
- **Workbook code lives in `excel/vba/` as well as in the workbook.** Export modules after every
  change so VBA changes are reviewable in diffs (`.xlsm` itself is binary).
- **Numbered stages.** Workflows are named `NN_stage_name.yxmd` (e.g. `01_load_claims.yxmd`) and
  each stage writes a control total that the next stage reconciles to.
- **Commit messages** state what changed and why; methodology changes also get an entry in
  `docs/CHANGELOG.md`.

## Getting started

1. Clone the repo.
2. Open workflows in Alteryx Designer from `alteryx/workflows/`.
3. Open the pricing template from `excel/templates/` (enable macros).
4. Point inputs at `data/synthetic/` for test runs, or `data/input/` for live runs.
