# Per-person excess-of-loss simulation: model specification (for an independent rebuild)

You are given, per variant, one file `param_pack.json`. Everything needed is in it; build the calculation from
this specification only.

## Inputs (param_pack.json)
- `seed`, `iterations` (n)
- `lambda` (expected annual number of claims above the threshold), `phi` (dispersion), `freq_dist`: "POISSON" or "NEGBIN"
- `u_fit` = u: the threshold; every simulated claim is a claim above u
- `max_observed` = M
- `sev_dist` and `selected_candidate` {dist, p1_name, p1, p2_name, p2}: the severity distribution of a claim X
  conditional on X > u. `severity_source` "NATIVE" means: single-parameter Pareto with `pareto_alpha_native`.
  Parametrisations:
  - PARETO: S(x | X > u) = (u / x)^alpha, x >= u.
  - GPD: S(x | X > u) = (1 + xi (x - u) / sigma)^(-1/xi), x >= u.
  - LOGNORMAL: ln X ~ N(mu, sigma^2) unconditionally; conditional survival S(x | X > u) = S_LN(x) / S_LN(u).
    (mu may be extreme; compute survival ratios in log space.)
- `caps_in_order`: a list of n_fit cap amounts (benefit / policy limits), in a fixed order.
- `layers`: active per-person layers, each with option_id, layer, limit_basis ("AMOUNT" or "TO_BENEFIT_LIMIT"),
  ap (attachment), limit (blank = unlimited), aal, aad (blank = none), co_participation_pct (blank = 0),
  reinstatements_n, reinstatement_cost_pct (blank: no reinstatements).

## Random numbers (exact reproduction)
MINSTD Lehmer generator: a = 48271, m = 2^31 - 1. State s0 = 1 + (seed mod (m - 1)).
Three independent streams, j = 1, 2, 3, each starting from start_j = s0 * a^(j * 2^29) mod m (compute the power
with modular exponentiation; use integer arithmetic throughout, never floats).
A stream yields state_1 = a * start_j mod m, state_2 = a * state_1 mod m, ...; the uniform is U = state / m
(open interval (0, 1)).
- Stream 1: one uniform per iteration, iterations 1 .. n in order (claim counts).
- Stream 2: one uniform per claim, claims in (iteration, claim index) order (severity).
- Stream 3: one uniform per claim, same order (cap).
Streams 2 and 3 are consumed only for iterations with N > 0; an iteration with N = 0 consumes nothing from them.

## Claim count per iteration
Probability table k = 0 .. k_max, k_max = ceil(lambda + 10 sqrt(lambda * (phi if NEGBIN else 1)) + 20).
- POISSON: p_0 = exp(-lambda), p_k = p_{k-1} * lambda / k.
- NEGBIN (used only when freq_dist = "NEGBIN"): r = lambda / (phi - 1), q = 1 / phi; p_0 = q^r,
  p_k = p_{k-1} * (r + k - 1) / k * (1 - q).
CDF(k) = p_0 + ... + p_k (cumulative, no renormalisation). With U from stream 1: N = #{k : CDF(k) < U}.

## Severity grid and draw
Grid: for k = 0 .. 500, x_k is the point with S(x_k | X > u) = 10^(-k / 50) (so x_0 = u); lx_k = ln x_k.
Rows k = 0 .. 499 carry lx0_k = lx_k and lx1_k = lx_{k+1}.
Draw: U from stream 2; t = -50 * log10(U); k = min(floor(t), 499); ln X = lx0_k + (t - k) * (lx1_k - lx0_k);
X = exp(ln X). (For a Pareto this is exact: X = u * U^(-1/alpha).)
Cap: U3 from stream 3; index = floor(U3 * n_fit) (0-based into caps_in_order); cap = that cap.
claim = min(X, cap). claim_belowM = min(claim, M).

## Layer loss
For a layer with attachment ap, limit term lim and co-participation cop:
- lim = (limit_basis TO_BENEFIT_LIMIT: cap - ap, where cap is the claim's drawn cap; unlimited if cap >= 1e15)
        (AMOUNT: limit, or unlimited if limit is blank). Unlimited = 1e15.
- over = claim - ap; xl = 0 if over <= 0; else min(over, lim) if lim > 0, else 0; then xl *= (1 - cop).
Annual, per iteration and layer: recovery = min(max(sum of xl over the iteration's claims - aad, 0), aal)
(aad blank = 0, aal blank = unlimited). recovery_belowM = the same computed with claim_belowM in place of claim;
recovery_aboveM = recovery - recovery_belowM. An iteration with no claims has recovery 0.
Reinstatements: none of the layers here has them (usage = 0).

## Summary per layer (over the n iterations)
mean_recovery, sd_recovery (report both the sample SD with n - 1 and the population SD with n), cov = sd / mean,
p_zero = share of iterations with recovery = 0, mean_above_max_observed = mean of recovery_aboveM,
quantiles p50, p75, p90, p95, p99, p995 by nearest rank: sorted ascending, value at position
max(1, ceil(q * n)) (1-based), q = 0.50, 0.75, 0.90, 0.95, 0.99, 0.995.

## Also required: a second, independent check that does not use the seeded stream
1. Monte Carlo with your own generator (numpy default_rng, 200,000 iterations), same model, same statistics.
2. Closed form: expected annual recovery per layer = lambda * E[xl(claim)], where
   E[xl(claim)] = (1/n_fit) * sum over caps c of integral from ap to min(ap + lim_c, c) of S(x | X > u) dx,
   times (1 - cop); lim_c as above (for TO_BENEFIT_LIMIT, lim_c = c - ap; for AMOUNT, the limit). Integrate
   numerically (scipy.integrate.quad with the survival function; split at the cap). For NEGBIN the mean is the
   same lambda. Also give the closed-form E[xl] restricted to claim_belowM (upper limit also min(., M)).
