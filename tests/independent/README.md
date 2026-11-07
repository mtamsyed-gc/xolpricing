# Independent (clean-room) reconciliation of the stochastic model

Owner's ask (2026-11-07): recompute the simulation from the distribution parameters, seed and layer terms alone,
without the tool, and see whether it reconciles; vary the distributions and parameters and see whether it moves consistently.

- `SPEC.md`: the model as a neutral specification (from methodology §8): counts, severity grid, caps, layer loss, MINSTD
  streams, statistics; plus the two independent checks (own-RNG Monte Carlo, closed-form expected layer cost).
- `packs/<variant>.json`: the only inputs the rebuild received (seed, iterations, lambda, phi, freq_dist, u, M, the
  selected distribution and its parameters, the caps in order, the active PP layers).
- `indep_rebuild.py`: written by a separate agent that saw only `SPEC.md` and the packs (not this repository).
  `python3 -I indep_rebuild.py <variant>` from a folder holding `<variant>/param_pack.json`.
- `run_variants.py`: runs the tool (stages 00-04) under each variant and writes the packs; `compare.py` reconciles.
- `REPORT_2026-11-07.md`, `reconciliation_2026-11-07.csv`: the results.

Result, 2026-11-07 (8 variants x 4 layers): the exact seeded rebuild reproduces every iteration of the tool's output
(max relative difference 3e-12, 0 of 32 x n iterations differ), including Alteryx Designer's own 10,000-iteration run of
2026-10-14; the tool's mean recovery is within 1.4 standard errors of the closed-form expected cost on every layer;
a 200,000-iteration Monte Carlo with an independent generator agrees with the closed form within 1.5 SE; the tool's
`sd_recovery` is the population SD (divisor n). Movements under GPD, lognormal, negative binomial, another seed and a
higher fitting cut-off are the same in the tool and in the closed form to 0.1 pp (see the CHANGELOG entry).
