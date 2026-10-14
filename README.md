# XoL Pricing Tool

Broker-side excess-of-loss pricing tool for medical (health) portfolios. For each candidate
structure it produces (A) expected layer claims cost, (B) an indicative RI premium on high-level
loadings, and (C) cedant impact — recoveries vs premium and retained-claims volatility.

**Alteryx** does all data preparation, burning cost, distribution fitting and simulation.
**Excel** holds the inputs (Control workbook) and the loadings, cedant-impact exhibits and charts
(Results workbook).

> Status: scope drafted — see [`docs/scope.md`](docs/scope.md). Capital and risk transfer are out of scope.

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
  synthetic/      synthetic test data (committed)
  input/          real run inputs (git-ignored, never committed)
  output/         run outputs (git-ignored)
docs/             scope, methodology, data contracts, change log
tests/
  reconciliation/ expected control totals for synthetic runs
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
