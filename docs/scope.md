# Scope — open decisions

Nothing below is decided yet. Each item is a question to settle before the build starts; record the
answer and date here, then move it into `methodology.md` / `data-contracts.md`.

## Cover
- [ ] Per-risk (per-claimant) XoL, per-event / catastrophe XoL, aggregate stop loss — which are in scope?
- [ ] Lines: medical reimbursement only, or group life / PA as well?
- [ ] Layer features to support: reinstatements (number, % cost), AAD, AAL, indexation / stability clause, limits on number of claimants per event

## Methods
- [ ] Experience rating (burning cost on trended, developed, capped claims) — as-if adjustments required?
- [ ] Exposure / curve rating where experience is thin — source of curves or severity benchmarks
- [ ] Credibility blend between experience and exposure — method and weights
- [ ] Frequency–severity simulation (in Excel vs Alteryx) or deterministic only
- [ ] Loadings: expenses, brokerage, cost of capital, profit margin — structure

## Data
- [ ] Input form: claimant-level claims, event-level, or both; exposure by member-month or by year
- [ ] Development / IBNR for large claims — factors supplied or derived
- [ ] Trend basis and default rate; claim inflation vs exposure growth
- [ ] Reuse of existing team Alteryx prep workflows (SchemeA output) as the entry point?

## Outputs
- [ ] Layer-level results table (expected loss, ROL, loss on line, rate on premium)
- [ ] Charts / exhibits for client-facing slides
- [ ] Reconciliation pack (raw → prepared → layered totals)

## Environment
- [ ] Alteryx Designer version on the GC machine
- [ ] Excel: macros permitted (.xlsm) or formula-only (.xlsx)
