# Changelog

Methodology and data-contract changes, newest first.

## 2026-10-14
- Data contracts v0.1: input templates (line, claimant, ETE mapping to ete-dashboard fact_claims / dim_life / fact_member_month), internal schema, capability flags, Control and Results workbook tables, adapter checks A01–A10.
- Synthetic data generator and data set (all three input modes), truth file with IBNR / unearned / unwritten split, validation script.
- Scope v0.2: decisions D1–D9 recorded. Cover matrix extended to per-person, per-event and aggregate stop loss on RA and LO; standalone and ETE input modes; expiring-year completion split into IBNR / unearned / unwritten gross-ups with indicated values; EPI input; reinstatements; build phasing.
- Scope drafted from the WCXL pricing workshop framework, adapted to the broker view (expected cost, indicative RI cost, cedant impact); capital and risk transfer dropped; all data prep and stochastic modelling assigned to Alteryx.
- Repository scaffold created.
