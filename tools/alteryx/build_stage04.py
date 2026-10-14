"""Stage 04 - per-person stochastic model, native Alteryx tools only (scope §6; methodology §9).

Reads bc_adjusted, bc_weights, bc_summary (stage 03), exp_exposure_ty (stage 02), ctl_params, ctl_layers.
    Fit      single-parameter Pareto above u (u = fit_cutoff, else lowest active PP AP) by MLE with censoring at the
             unit's cap (benefit / policy limit):  alpha = n_uncensored / sum ln(min(adj, cap) / u)
    Freq     Poisson, lambda = sum_ty w_ty x (units > u x post-AP count factor / full-year exposure) x lives_Q x incidence
    Sim      per iteration N ~ Poisson(lambda) (inverse CDF), X = u U^(-1/alpha), cap sampled from the fitted units'
             caps, claim = min(X, cap); layer loss per active PP layer, AAD / AAL / reinstatements on the annual total
    RNG      MINSTD (Lehmer, a = 48271, m = 2^31 - 1) seeded from `seed`; three non-overlapping streams by jump-ahead
             (Poisson draws, severity, cap). Same seed -> identical results in Designer and the simulator.
Writes results/out_fit.csv, out_sim_summary.csv, out_sim_quantiles.csv, out_sim_iterations.csv, stage sim_summary,
sim_iterations and sim_gross (per iteration: all large claims, and those above the stop-loss attritional threshold
t_sl = max(sl_attritional_threshold, u), for stage 05), checks_04.csv, recon_04.csv.
"""
from __future__ import annotations

import xol_common as C
from xol_common import X

M = 2147483647
A = 48271
BIG = "1000000000000000"
JUMP = {j: pow(A, j * (1 << 29), M) for j in (1, 2, 3)}       # stream start multipliers (build-time constants)
QUANTILES = [0.005 * i for i in range(1, 200)] + [0.001, 0.999]


def results(v, name):
    return v.p(f"{v.out_dir}/results/{name}.csv")


def modm(e):
    return f"(({e}) - {M} * Floor(({e}) / {M}))"


def nxt(x):
    return modm(f"{A} * {x}")


def jump(x, mult):
    """(mult * x) mod m without exceeding 2^53: split x into 16-bit high and low parts."""
    hi, lo = f"Floor({x} / 65536)", f"({x} - 65536 * Floor({x} / 65536))"
    return modm(f"{modm(f'{mult} * {hi}')} * 65536 + {mult} * {lo}")


def stream(wf, src, field, start_field, label):
    """Sequential MINSTD draws along the record order: field = state, u_<field> = state / m in (0, 1)."""
    t = wf.multirow(src, field, X.DBL, f"IIF([Row-1:{field}] = 0, {nxt(f'[{start_field}]')}, {nxt(f'[Row-1:{field}]')})",
                    label=f"{label}: MINSTD stream")
    return wf.formula(t, [(f"u_{field}", X.DBL, f"[{field}] / {M}")], label=f"{label}: uniform")


def layer_loss(claim, cap):
    """Per-person layer loss of a claim amount; [lim_basis], [ap], [limit], [cop] on the stream."""
    lim = (f'IIF([lim_basis] = "TO_BENEFIT_LIMIT", IIF({cap} >= {BIG}, {BIG}, {cap} - [ap]), '
           f'IIF(IsNull([limit]), {BIG}, [limit]))')
    over = f"({claim} - [ap])"
    return f"IIF({over} <= 0, 0, IIF({over} < {lim}, {over}, IIF({lim} > 0, {lim}, 0))) * (1 - [cop])"


def build(v):
    wf = X.WF("04_simulate", "Stage 04: per-person stochastic model (native: censored Pareto, Poisson, MINSTD RNG)")
    wf.dag = True
    checks, sn = [], "04"

    # ------------------------------------------------------------------ inputs
    wf.container("04.1 Inputs: parameters, layers, threshold u")
    par = C.params_stream(wf, v, ["iterations", "seed", "fit_cutoff", "freq_model", "incidence_adj", "years_included",
                                  "sl_attritional_threshold"], key="kp", label="parameters")
    lay = wf.input_yxdb(C.stage(v, "ctl_layers"), label="ctl_layers (stage 00)")
    lay, _ = wf.filter(lay, '[cover] = "PP" AND [active] = 1', label="active per-person layers")
    lay = wf.formula(lay, [("lim_basis", X.STR, 'IIF(IsNull([limit_basis]), "AMOUNT", [limit_basis])'),
                           ("cop", X.DBL, "IIF(IsNull([co_participation_pct]), 0, [co_participation_pct])"),
                           ("l_label", X.STR, 'IIF(IsNull([label]), "PP option " + ToString([option_id]), [label])'),
                           ("kl", X.INT, "1")], label="layer fields")
    lay = wf.select(lay, keep=["kl", "option_id", "layer", "l_label", "lim_basis", "ap", "limit", "aal", "aad", "cop",
                               "reinstatements_n", "reinstatement_cost_pct", "reinst_pro_rata_time"],
                    keep_unknown=False, label="layers")
    minap = wf.summarize(lay, [("kl", "GroupBy", "kmin"), ("ap", "Min", "min_ap")], label="lowest AP")
    pu = C.append(wf, par, minap, "kp", "kmin", "+ lowest AP")
    pu = wf.formula(pu, [("u_fit", X.DBL, "IIF(IsNull([p_fit_cutoff]), [min_ap], [p_fit_cutoff])"),
                         ("s0", X.DBL, f"[p_seed] - {M - 1} * Floor([p_seed] / {M - 1}) + 1")],
                    label="threshold u, seed state")
    pu = wf.formula(pu, [(f"start{j}", X.DBL, jump("[s0]", JUMP[j])) for j in (1, 2, 3)] + [
        ("t_sl", X.DBL, "IIF(IsNull([p_sl_attritional_threshold]) OR [p_sl_attritional_threshold] < [u_fit], [u_fit], "
                        "[p_sl_attritional_threshold])")], label="stream starts, stop-loss large threshold")
    ctx = wf.select(pu, keep=["kp", "u_fit", "p_iterations", "p_incidence_adj", "p_freq_model", "start1", "start2",
                              "start3", "s0", "t_sl"], keep_unknown=False, label="simulation context")

    # ------------------------------------------------------------------ fit
    wf.container("04.2 Severity fit: Pareto above u with censoring at caps; diagnostics")
    adj = wf.input_yxdb(C.stage(v, "bc_adjusted"), label="bc_adjusted (stage 03)")
    adj = wf.formula(adj, [("kx", X.INT, "1")], label="key")
    adj = C.append(wf, adj, ctx, "kx", "kp", "+ context")
    fu, _ = wf.filter(adj, "[adj] > [u_fit]", label="units above u")
    fu = wf.formula(fu, [("censored", X.INT, "IIF([adj_uncapped] >= [cap], 1, 0)"),
                         ("ln_ratio", X.DBL, "Log([adj] / [u_fit])"),
                         ("n_unc", X.DBL, "IIF([adj_uncapped] >= [cap], 0, 1)")], label="censoring, log excess")
    fit = wf.summarize(fu, [("kx", "GroupBy", "kf"), ("n_unc", "Sum", "n_uncensored"), ("censored", "Sum", "n_censored"),
                            ("ln_ratio", "Sum", "sum_ln"), ("adj", "Max", "max_observed"), ("acc_key", "Count", "n_fit")],
                       label="fit sums")
    fit = wf.formula(fit, [("alpha", X.DBL, "IIF([sum_ln] > 0 AND [n_uncensored] > 0, [n_uncensored] / [sum_ln], Null())"),
                           ("alpha_se", X.DBL, "IIF([n_uncensored] > 0, [n_uncensored] / [sum_ln] / Sqrt([n_uncensored]), Null())")],
                     label="Pareto alpha (MLE, censored)")
    fitc = wf.select(fit, keep=["kf", "alpha", "alpha_se", "n_fit", "n_uncensored", "n_censored", "max_observed"],
                     keep_unknown=False, label="fit result")
    # caps to sample from: fitted units ranked in a fixed order
    caps = wf.record_id(wf.sort(fu, [("acc_key", "Ascending")], label="fixed order"), "cap_rank", label="cap rank")
    caps = wf.select(caps, keep=["cap_rank", "cap"], rename={"cap_rank": "c_rank", "cap": "s_cap"}, keep_unknown=False,
                     label="cap table")
    # diagnostics: empirical vs fitted survival at thresholds
    thr_l = wf.formula(lay, [("t", X.DBL, "[ap]"), ("t_label", X.STR, '"AP " + [l_label]')], label="AP thresholds")
    thr_l2, _ = wf.filter(lay, '[lim_basis] = "AMOUNT" AND !IsNull([limit])', label="layers with a fixed limit")
    thr_l2 = wf.formula(thr_l2, [("t", X.DBL, "[ap] + [limit]"), ("t_label", X.STR, '"exhaust " + [l_label]')],
                        label="exhaustion thresholds")
    fixed = wf.text_input(["t", "t_label"], [[x, f"{int(x):,}"] for x in (250000, 500000, 1000000, 2000000)],
                          label="round thresholds")
    thr = wf.union([wf.select(thr_l, keep=["t", "t_label"], keep_unknown=False, label="t"),
                    wf.select(thr_l2, keep=["t", "t_label"], keep_unknown=False, label="t"), fixed], label="thresholds")
    thr = wf.unique(thr, ["t"], label="distinct thresholds")[0]
    thr = wf.formula(thr, [("kt", X.INT, "1")], label="key")
    tx = C.append(wf, fu, wf.select(thr, keep=["kt", "t", "t_label"], keep_unknown=False, label="thr"), "kx", "kt",
                  "units x thresholds")
    tx = wf.formula(tx, [("ge", X.DBL, "IIF([adj] >= [t], 1, 0)")], label="exceeds t")
    tx = wf.formula(tx, [("t_key", X.STR, "ToString([t], 0)")], label="threshold key (text: no Group By on Double)")
    emp = wf.summarize(tx, [("t_key", "GroupBy", "t_key"), ("t", "First", "t"), ("t_label", "First", "t_label"),
                            ("ge", "Sum", "n_ge"), ("acc_key", "Count", "n")], label="empirical exceedances")
    emp = wf.formula(emp, [("kf2", X.INT, "1")], label="key")
    emp = C.append(wf, emp, fitc, "kf2", "kf", "+ fit")
    emp = C.append(wf, emp, wf.select(ctx, keep=["kp", "u_fit"], keep_unknown=False, label="u"), "kf2", "kp", "+ u")
    emp, _ = wf.filter(emp, "[t] > [u_fit]", label="thresholds above u")
    emp = wf.formula(emp, [("s_empirical", X.DBL, "[n_ge] / [n]"),
                           ("s_fitted", X.DBL, "IIF(IsNull([alpha]), Null(), Pow([u_fit] / [t], [alpha]))"),
                           ("expected_n", X.DBL, "IIF(IsNull([alpha]), Null(), [n] * Pow([u_fit] / [t], [alpha]))")],
                     label="survival: empirical vs fitted")
    fit_out = wf.select(emp, keep=["t", "t_label", "u_fit", "alpha", "alpha_se", "n_fit", "n_uncensored", "n_censored",
                                   "max_observed", "n_ge", "expected_n", "s_empirical", "s_fitted"],
                        keep_unknown=False, label="out_fit fields")
    wf.output_csv(wf.sort(fit_out, [("t", "Ascending")], label="by threshold"), results(v, "out_fit"), label="out_fit.csv")

    # ------------------------------------------------------------------ frequency
    wf.container("04.3 Frequency: lambda for the quote year")
    nby = wf.formula(fu, [("n_c", X.DBL, "[post_cnt_f_]")], label="completed count per unit")
    nby = wf.summarize(nby, [("ty", "GroupBy", "ty"), ("n_c", "Sum", "n_u")], label="units above u by TY")
    wts = wf.input_yxdb(C.stage(v, "bc_weights"), label="bc_weights (stage 03)")
    ex = wf.input_yxdb(C.stage(v, "exp_exposure_ty"), label="exp_exposure_ty (stage 02)")
    fy = wf.join(wts, wf.select(ex, keep=["ty", "exposure_ly_full"], keep_unknown=False, label="exposure"),
                 ["g_ty"], ["ty"], label="weights + exposure")["J"]
    fy = wf.left_join(fy, wf.select(nby, keep=["ty", "n_u"], rename={"ty": "n_ty"}, keep_unknown=False, label="counts"),
                      ["g_ty"], ["n_ty"], label="+ counts")
    fy = wf.formula(fy, [("freq_u", X.DBL, "IIF(IsNull([n_u]), 0, [n_u]) / [exposure_ly_full]"),
                         ("w_freq", X.DBL, "[weight] * [freq_u]"), ("kw", X.INT, "1")], label="frequency above u")
    lam = wf.summarize(fy, [("kw", "GroupBy", "kw"), ("w_freq", "Sum", "freq_u_weighted")], label="weighted frequency")
    bsum = wf.input_yxdb(C.stage(v, "bc_summary"), label="bc_summary (stage 03)")
    lives = wf.summarize(wf.formula(bsum, [("kv", X.INT, "1")], label="key"),
                         [("kv", "GroupBy", "kv"), ("lives_quote_year", "Max", "lives_q")], label="lives in quote year")
    lam = C.append(wf, lam, lives, "kw", "kv", "+ lives")
    lam = C.append(wf, lam, ctx, "kw", "kp", "+ context")
    lam = wf.formula(lam, [("lambda", X.DBL, "[freq_u_weighted] * [lives_q] * [p_incidence_adj]"),
                           ("k_max", X.INT64, "Ceil([lambda] + 10 * Sqrt([lambda]) + 20)")], label="lambda")
    lam = C.append(wf, lam, fitc, "kw", "kf", "+ fit")
    lam = wf.select(lam, keep=["kw", "lambda", "k_max", "freq_u_weighted", "lives_q", "u_fit", "alpha", "max_observed",
                               "n_fit", "p_iterations", "p_freq_model", "start1", "start2", "start3", "t_sl"],
                    keep_unknown=False, label="simulation constants")

    # ------------------------------------------------------------------ Poisson draws
    wf.container("04.4 Poisson claim counts by inverse CDF (merge of CDF table and iteration uniforms)")
    ks = wf.generate_rows(lam, "k", X.INT64, "0", "[k] <= [k_max]", "[k] + 1", label="k = 0 .. k_max")
    ks = wf.multirow(ks, "pk", X.DBL, "IIF([k] = 0, Exp(-[lambda]), [Row-1:pk] * [lambda] / [k])", label="Poisson pmf")
    ks = wf.multirow(ks, "cdf", X.DBL, "[Row-1:cdf] + [pk]", label="Poisson CDF")
    its = wf.generate_rows(lam, "iter", X.INT64, "1", "[iter] <= [p_iterations]", "[iter] + 1", label="iterations")
    its = stream(wf, its, "s1", "start1", "iteration uniforms")
    a_ = wf.formula(ks, [("val", X.DBL, "[cdf]"), ("typ", X.INT, "1"), ("iter", X.INT64, "0")], label="CDF records")
    b_ = wf.formula(its, [("val", X.DBL, "[u_s1]"), ("typ", X.INT, "0"), ("k", X.INT64, "-1")], label="draw records")
    mg = wf.union([wf.select(a_, keep=["val", "typ", "iter", "k"], keep_unknown=False, label="cdf"),
                   wf.select(b_, keep=["val", "typ", "iter", "k"], keep_unknown=False, label="draws")], label="merge")
    mg = wf.sort(mg, [("val", "Ascending"), ("typ", "Ascending"), ("iter", "Ascending")], label="by value (draws first on ties)")
    mg = wf.multirow(mg, "n_below", X.INT64, "[Row-1:n_below] + IIF([Row-1:typ] = 1, 1, 0)", label="CDF points below")
    nn, _ = wf.filter(mg, "[typ] = 0", label="draws")
    nn = wf.formula(nn, [("n_claims", X.INT64, "[n_below]"), ("kw", X.INT, "1")], label="N = #{k: CDF(k) < U}")
    nn = wf.select(nn, keep=["iter", "n_claims", "kw"], keep_unknown=False, label="claims per iteration")
    nn = wf.sort(nn, [("iter", "Ascending")], label="by iteration")

    # ------------------------------------------------------------------ claims
    wf.container("04.5 Claims: severity and cap draws")
    cl = wf.join(
        nn, wf.select(lam, keep=["kw", "u_fit", "alpha", "max_observed", "n_fit", "start2", "start3", "t_sl"],
                      rename={"kw": "kw2"}, keep_unknown=False, label="claim constants"), ["kw"], ["kw2"],
        label="+ constants")["J"]
    cl = wf.generate_rows(cl, "j", X.INT64, "1", "[j] <= [n_claims]", "[j] + 1", label="one row per claim")
    cl = wf.sort(cl, [("iter", "Ascending"), ("j", "Ascending")], label="claim order")
    cl = stream(wf, cl, "s2", "start2", "severity uniforms")
    cl = stream(wf, cl, "s3", "start3", "cap uniforms")
    cl = wf.formula(cl, [("x", X.DBL, "[u_fit] * Pow([u_s2], -1 / [alpha])"),
                         ("cap_rank", X.INT64, "Floor([u_s3] * [n_fit]) + 1")], label="severity, cap index")
    cl = wf.join(cl, caps, ["cap_rank"], ["c_rank"], label="+ sampled cap")["J"]
    cl = wf.formula(cl, [("claim", X.DBL, "IIF([x] < [s_cap], [x], [s_cap])")], label="claim = min(X, cap)")
    cl = wf.formula(cl, [("claim_belowM", X.DBL, "IIF([claim] < [max_observed], [claim], [max_observed])"),
                         ("kc", X.INT, "1")], label="claim limited to the largest observed")
    cl = wf.formula(cl, [("claim_sl", X.DBL, "IIF([claim] > [t_sl], [claim], 0)")], label="claim above t_sl")
    gross = wf.summarize(cl, [("iter", "GroupBy", "iter"), ("claim", "Sum", "large_gross"),
                              ("claim_sl", "Sum", "large_gross_sl")], label="large gross by iteration")
    sg = wf.left_join(wf.select(its, keep=["iter"], keep_unknown=False, label="all iterations"),
                      wf.select(gross, keep=["iter", "large_gross", "large_gross_sl"], rename={"iter": "g_it"},
                                keep_unknown=False, label="gross"), ["iter"], ["g_it"], label="+ gross (none = 0)")
    sg = wf.formula(sg, [("lg", X.DBL, "IIF(IsNull([large_gross]), 0, [large_gross])"),
                         ("lgs", X.DBL, "IIF(IsNull([large_gross_sl]), 0, [large_gross_sl])")], label="no claims = 0")
    sg = wf.select(sg, keep=["iter", "lg", "lgs"], rename={"lg": "large_gross", "lgs": "large_gross_sl"},
                   keep_unknown=False, label="sim_gross fields")
    wf.output_yxdb(wf.sort(sg, [("iter", "Ascending")], label="by iteration"), C.stage(v, "sim_gross"), label="sim_gross")

    # ------------------------------------------------------------------ layers
    wf.container("04.6 Layers: per-claim loss, annual AAD / AAL / reinstatements, iteration totals")
    cx = C.append(wf, cl, lay, "kc", "kl", "claims x layers")
    cx = wf.formula(cx, [("xl", X.DBL, layer_loss("[claim]", "[s_cap]")),
                         ("xl_below", X.DBL, layer_loss("[claim_belowM]", "[s_cap]"))], label="layer loss")
    it = wf.summarize(cx, [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"), ("iter", "GroupBy", "iter"),
                           ("xl", "Sum", "xl"), ("xl_below", "Sum", "xl_below"), ("xl", "Count", "n_claims_layer")],
                      label="by iteration")
    grid = C.append(wf, wf.formula(its, [("kg", X.INT, "1")], label="key"), lay, "kg", "kl", "iterations x layers")
    grid = wf.formula(grid, [("xl", X.DBL, "0"), ("xl_below", X.DBL, "0"), ("n_claims_layer", X.INT64, "0")],
                      label="zero rows")
    grid = wf.select(grid, keep=["option_id", "layer", "iter", "xl", "xl_below", "n_claims_layer"], keep_unknown=False,
                     label="grid")
    it = wf.summarize(wf.union([wf.select(it, keep=["option_id", "layer", "iter", "xl", "xl_below", "n_claims_layer"],
                                          keep_unknown=False, label="iteration amounts"), grid], label="+ zero grid"),
                      [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"), ("iter", "GroupBy", "iter"),
                       ("xl", "Sum", "xl"), ("xl_below", "Sum", "xl_below"), ("n_claims_layer", "Sum", "n_claims_layer")],
                      label="all iterations")
    it = wf.join(it, wf.select(lay, keep=["option_id", "layer", "l_label", "lim_basis", "limit", "aal", "aad",
                                          "reinstatements_n", "reinstatement_cost_pct"],
                               rename={"option_id": "t_opt", "layer": "t_lay"}, keep_unknown=False, label="terms"),
                 ["option_id", "layer"], ["t_opt", "t_lay"], label="+ layer terms")["J"]
    after_aad = "IIF([xl] - IIF(IsNull([aad]), 0, [aad]) > 0, [xl] - IIF(IsNull([aad]), 0, [aad]), 0)"
    it = wf.formula(it, [
        ("agg_limit", X.DBL, f"IIF(!IsNull([aal]), [aal], IIF(IsNull([reinstatements_n]) OR IsNull([limit]), {BIG}, "
                             "(1 + [reinstatements_n]) * [limit]))"),
        ("recovery", X.DBL, f"IIF({after_aad} < [agg_limit], {after_aad}, [agg_limit])"),
        ("aa_f", X.DBL, "IIF([xl] > 0, [recovery] / [xl], 1)"),
        ("recovery_aboveM", X.DBL, "([xl] - [xl_below]) * [aa_f]"),
        ("reinst_usage", X.DBL, "IIF(IsNull([reinstatement_cost_pct]) OR IsNull([limit]) OR [limit] <= 0, 0, "
                                "[reinstatement_cost_pct] * IIF(IsNull([reinstatements_n]), [recovery], "
                                "IIF([recovery] < [reinstatements_n] * [limit], [recovery], "
                                "[reinstatements_n] * [limit])) / [limit])")], label="annual terms")
    it = wf.left_join(it, wf.select(gross, keep=["iter", "large_gross"], rename={"iter": "g_iter"}, keep_unknown=False,
                                   label="gross"), ["iter"], ["g_iter"], label="+ large gross")
    it = wf.formula(it, [("large_gross_", X.DBL, "IIF(IsNull([large_gross]), 0, [large_gross])")], label="no claims = 0")
    ito = wf.select(it, keep=["iter", "option_id", "layer", "l_label", "recovery", "recovery_aboveM", "reinst_usage",
                              "n_claims_layer", "large_gross_"], rename={"large_gross_": "large_gross"},
                    keep_unknown=False, label="out_sim_iterations fields")
    ito = wf.sort(ito, [("option_id", "Ascending"), ("layer", "Ascending"), ("iter", "Ascending")], label="order")
    wf.output_csv(ito, results(v, "out_sim_iterations"), label="out_sim_iterations.csv")
    wf.output_yxdb(ito, C.stage(v, "sim_iterations"), label="sim_iterations")

    # ------------------------------------------------------------------ quantiles and summary
    wf.container("04.7 Quantiles (nearest rank) and summary with the deterministic blend")
    srt = wf.sort(ito, [("option_id", "Ascending"), ("layer", "Ascending"), ("recovery", "Ascending"),
                        ("iter", "Ascending")], label="by recovery")
    srt = wf.multirow(srt, "rk", X.INT64, "IIF([Row-1:option_id] = [option_id] AND [Row-1:layer] = [layer], "
                                          "[Row-1:rk] + 1, 1)", label="rank within layer")
    qs = wf.text_input(["q"], [[f"{q:.3f}"] for q in sorted(QUANTILES)], label="quantile grid")
    qs = wf.formula(qs, [("kq", X.INT, "1")], label="key")
    qn = C.append(wf, qs, wf.select(lam, keep=["kw", "p_iterations"], keep_unknown=False, label="n"), "kq", "kw",
                  "+ iterations")
    qn = wf.formula(qn, [("q_rank", X.INT64, "IIF(Ceil([q] * [p_iterations]) < 1, 1, Ceil([q] * [p_iterations]))")],
                    label="nearest rank")
    qj = wf.join(srt, wf.select(qn, keep=["q", "q_rank"], keep_unknown=False, label="ranks"), ["rk"], ["q_rank"],
                 label="quantile rows")["J"]
    qo = wf.select(qj, keep=["option_id", "layer", "l_label", "q", "recovery"], keep_unknown=False,
                   label="out_sim_quantiles fields")
    wf.output_csv(wf.sort(qo, [("option_id", "Ascending"), ("layer", "Ascending"), ("q", "Ascending")], label="order"),
                  results(v, "out_sim_quantiles"), label="out_sim_quantiles.csv")

    st = wf.formula(ito, [("r2", X.DBL, "[recovery] * [recovery]"), ("zero", X.DBL, "IIF([recovery] = 0, 1, 0)")],
                    label="moments")
    st = wf.summarize(st, [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"),
                           ("l_label", "First", "l_label"), ("recovery", "Sum", "sum_r"), ("r2", "Sum", "sum_r2"),
                           ("zero", "Sum", "n_zero"), ("recovery_aboveM", "Sum", "sum_above"),
                           ("reinst_usage", "Sum", "sum_ru"), ("iter", "Count", "n_iter"),
                           ("recovery", "Max", "max_recovery")], label="sums")
    st = wf.formula(st, [("mean_recovery", X.DBL, "[sum_r] / [n_iter]"),
                         ("sd_recovery", X.DBL, "Sqrt(IIF([sum_r2] / [n_iter] - Pow([sum_r] / [n_iter], 2) > 0, "
                                                "[sum_r2] / [n_iter] - Pow([sum_r] / [n_iter], 2), 0))"),
                         ("cov", X.DBL, "IIF([sum_r] > 0, [sd_recovery] / [mean_recovery], Null())"),
                         ("p_zero", X.DBL, "[n_zero] / [n_iter]"),
                         ("mean_above_max_observed", X.DBL, "[sum_above] / [n_iter]"),
                         ("mean_below_max_observed", X.DBL, "[mean_recovery] - [mean_above_max_observed]"),
                         ("mean_reinst_usage", X.DBL, "[sum_ru] / [n_iter]")], label="summary statistics")
    for qv, nm in ((0.5, "p50"), (0.75, "p75"), (0.9, "p90"), (0.95, "p95"), (0.99, "p99"), (0.995, "p995")):
        qq, _ = wf.filter(qo, f"[q] = {qv}", label=f"{nm} rows")
        qq = wf.select(qq, keep=["option_id", "layer", "recovery"], rename={"option_id": f"{nm}_opt", "layer": f"{nm}_lay",
                                                                            "recovery": nm}, keep_unknown=False,
                       label=nm)
        st = wf.join(st, qq, ["option_id", "layer"], [f"{nm}_opt", f"{nm}_lay"], label=f"+ {nm}")["J"]
    bs = wf.select(bsum, keep=["option_id", "layer", "credibility_z", "expected_cost_det", "bc_pppy_total"],
                   rename={"option_id": "b_opt", "layer": "b_lay"}, keep_unknown=False, label="deterministic")
    st = wf.join(st, bs, ["option_id", "layer"], ["b_opt", "b_lay"], label="+ deterministic")["J"]
    st = wf.formula(st, [("ks", X.INT, "1")], label="key")
    st = C.append(wf, st, lam, "ks", "kw", "+ simulation constants")
    st = wf.formula(st, [
        ("det_to_stoch_below", X.DBL, "IIF([mean_below_max_observed] > 0, [expected_cost_det] / [mean_below_max_observed], Null())"),
        ("expected_loss_blended", X.DBL, "[credibility_z] * [expected_cost_det] + (1 - [credibility_z]) * "
                                         "[mean_below_max_observed] + [mean_above_max_observed]"),
        ("expected_loss_pppy", X.DBL, "[expected_loss_blended] / [lives_q]")], label="credibility blend")
    sto = wf.select(st, keep=["option_id", "layer", "l_label", "p_iterations", "lambda", "u_fit", "alpha", "max_observed",
                              "lives_q", "mean_recovery", "sd_recovery", "cov", "p_zero", "p50", "p75", "p90", "p95",
                              "p99", "p995", "max_recovery", "mean_below_max_observed", "mean_above_max_observed",
                              "mean_reinst_usage", "expected_cost_det", "credibility_z", "det_to_stoch_below",
                              "expected_loss_blended", "expected_loss_pppy"],
                    rename={"p_iterations": "iterations"}, keep_unknown=False, label="out_sim_summary fields")
    sto = wf.sort(sto, [("option_id", "Ascending"), ("layer", "Ascending")], label="order")
    wf.output_csv(sto, results(v, "out_sim_summary"), label="out_sim_summary.csv")
    wf.output_yxdb(sto, C.stage(v, "sim_summary"), label="sim_summary")

    # ------------------------------------------------------------------ checks
    wf.container("04.8 Checks and recon")
    s1, _ = wf.filter(lam, "IsNull([alpha])", label="S01 no fit")
    checks.append(C.check_formula(wf, s1, sn, "S01", "STOP", '"severity fit"',
                                  '"no uncensored claim above u: cannot fit (lower fit_cutoff)"'))
    s2, _ = wf.filter(lam, "[lambda] > 700", label="S02 lambda too large")
    checks.append(C.check_formula(wf, s2, sn, "S02", "STOP", '"frequency"',
                                  '"lambda > 700: raise fit_cutoff (Poisson table underflows)"'))
    s3, _ = wf.filter(lam, '[p_freq_model] = "NEGBIN"', label="S03 NEGBIN")
    checks.append(C.check_formula(wf, s3, sn, "S03", "FLAG", '"frequency"',
                                  '"NEGBIN not available in the native engine: Poisson used"'))
    s4, _ = wf.filter(lam, "[n_fit] < 30", label="S04 few claims")
    checks.append(C.check_formula(wf, s4, sn, "S04", "FLAG", '"severity fit"',
                                  'ToString([n_fit], 0) + " claims above u: fit is uncertain (see alpha_se)"'))
    s5, _ = wf.filter(st, "!IsNull([det_to_stoch_below]) AND ([det_to_stoch_below] < 0.8 OR [det_to_stoch_below] > 1.25)",
                      label="S05 det vs stoch")
    checks.append(C.check_formula(wf, s5, sn, "S05", "FLAG", "[l_label]",
                                  '"deterministic / stochastic (below largest claim) = " + ToString([det_to_stoch_below], 2) + '
                                  '": fit may be poor (workshop rule)"'))
    s6, _ = wf.filter(lay, "!IsNull([reinst_pro_rata_time]) AND [reinst_pro_rata_time] = 1", label="S06 pro rata time")
    checks.append(C.check_formula(wf, s6, sn, "S06", "FLAG", "[l_label]",
                                  '"reinstatement pro rata as to time not modelled: amount only"'))
    C.recon_rows(wf, [(fu, "fit_units", "rows", None), (nn, "iterations", "rows", None),
                      (nn, "iterations", "claims", "n_claims"), (cl, "claims", "rows", None),
                      (cl, "claims", "amount", "claim"), (ito, "out_sim_iterations", "rows", None),
                      (ito, "out_sim_iterations", "recovery", "recovery")], v, sn)
    C.finish_checks(wf, checks, v, sn)
    return C.save(wf, v, "04_simulate.yxmd")


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build(v))
