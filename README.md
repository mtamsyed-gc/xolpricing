# XoL Pricing Tool

Excess-of-loss reinsurance pricing tool for Life & Health (medical) business.
**Alteryx** handles data preparation (raw cedant/TPA claims and exposure → clean, capped, trended
loss and exposure tables); **Excel** handles the pricing engine, assumptions and presentation of
layer results.

> Status: repository scaffold only. Pricing methodology and data contracts are still to be agreed —
> see [`docs/scope.md`](docs/scope.md).

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
