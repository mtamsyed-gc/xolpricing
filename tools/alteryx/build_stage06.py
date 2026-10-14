"""Stage 06 - aggregate stop loss and cedant-impact inputs, native Alteryx tools only (scope §6, §8; methodology §9).

Reads ctl_params, ctl_layers, ctl_programme, ctl_trend, ctl_completion (stage 00), int_run_meta, int_claims_aggregate,
int_members (stage 01), exp_claimants, exp_exposure_ty (stage 02), bc_adjusted, bc_weights (stage 03), sim_summary,
sim_gross, sim_iterations (stage 04), sim_ev_iterations (stage 05).

    Split     t_sl = max(sl_attritional_threshold, u). Large = accumulation units with adj > t_sl (simulated in stage 04
              as large_gross_sl); attritional = everything else.
    History   attritional(ty) = total claims(ty) - raw amount of large units(ty); total from the units (line data) or
              the monthly aggregate (claimant data), x QS retention. Adjusted with the FGU trend (no large-claim trend
              override) and the ATTRITIONAL completion factors (IBNR x UNEARNED x UNWRITTEN); per life on full-year
              exposure.
    Model     mean = weighted cost per life (stage 03 weights) x lives_Q x incidence; CoV = sample CoV of the yearly
              costs per life, floored at sl_cov_floor. Lognormal (default) or normal (truncated at 0).
              Normal draws by Box-Muller from two new MINSTD streams (offsets 2^28 and 3 x 2^27, below stream 1).
    Gross     per iteration = attritional + large_gross_sl.  Budget = net_claims_budget, else mean simulated gross.
    SL        attachment / limit from sl_measure (PCT_BUDGET, PCT_EPI, AMOUNT) on gross, or (NET_OF_XOL) on gross net of
              the Programme sheet's PP option and EV option (the EV recoveries net of that PP option):
              rec = min(max(base - AP, 0), L) x (1 - cop)
    Structures  NONE, each PP option, each EV option, each SL option (with the programme's XoLs when NET_OF_XOL), and the
              PROGRAMME (PP -> EV -> SL options of the Programme sheet) when evaluate_mode = PROGRAMME: net claims
              per iteration, quantiles (q = 0.001 ... 0.999, nearest rank) for Exhibit C2, recovery quantiles for C1.
    Exposed   PP: share of the latest treaty year's member days with benefit limit > AP; EV / SL: all lives.

Writes results/out_attritional_by_year.csv, out_gross_summary.csv, out_gross_iterations.csv, out_sl_iterations.csv,
out_struct_summary.csv, out_struct_quantiles.csv, out_exposed_lives.csv, checks_06.csv, recon_06.csv.
"""
from __future__ import annotations

import xol_common as C
from xol_common import X
import build_stage03 as B3
import build_stage04 as B4

M, A, BIG = B4.M, B4.A, B4.BIG
JUMP5 = {4: pow(A, 1 << 28, M), 5: pow(A, 3 * (1 << 27), M)}     # offsets below stream 1 (2^29): never overlap
TWO_PI = "6.283185307179586"
PARAMS = ["iterations", "seed", "sl_attritional_threshold", "sl_cov_floor", "sl_attritional_dist", "net_claims_budget",
          "reference_lives", "epi_quote_year", "incidence_adj", "treaty_basis", "claims_trigger",
          "qs_cedant_retention_pct", "years_included", "treaty_start", "evaluate_mode"]


def results(v, name):
    return v.p(f"{v.out_dir}/results/{name}.csv")


def nz(f):
    return f"IIF(IsNull([{f}]), 0, [{f}])"


def amount(measure, val):
    """Stop-loss attachment / limit in quote currency from its measure."""
    return (f'IIF(IsNull([{val}]), Null(), IIF([measure] = "PCT_EPI", [{val}] * [p_epi_quote_year], '
            f'IIF([measure] = "AMOUNT", [{val}], [{val}] * [budget])))')


def quantile_rows(wf, src, value, label):
    """Nearest-rank rows of `value` per struct_key at q = qm / 1000, qm = 1..999: rank = ceil(qm x n / 1000)."""
    srt = wf.sort(src, [("struct_key", "Ascending"), (value, "Ascending"), ("iter", "Ascending")],
                  label=f"{label}: sort")
    srt = wf.multirow(srt, "rk", X.INT64, "IIF([Row-1:struct_key] = [struct_key], [Row-1:rk] + 1, 1)",
                      label=f"{label}: rank")
    return srt


def build(v):
    wf = X.WF("06_stop_loss", "Stage 06: attritional model, aggregate stop loss, net claims for cedant impact")
    wf.dag = True
    checks, sn = [], "06"

    # ------------------------------------------------------------------ context
    wf.container("06.1 Inputs: parameters, run meta, simulation constants, RNG streams")
    par = C.params_stream(wf, v, PARAMS, key="kp", label="parameters")
    meta = C.meta_stream(wf, v, ["has_line_detail", "has_member_exposure", "listing_threshold"], key="km")
    ss = wf.input_yxdb(C.stage(v, "sim_summary"), label="sim_summary (stage 04)")
    ss = wf.summarize(wf.formula(ss, [("ks", X.INT, "1")], label="key"),
                      [("ks", "GroupBy", "ks"), ("u_fit", "First", "u_fit"), ("lives_q", "Max", "lives_q")],
                      label="u and lives in quote year")
    ctx = C.append(wf, par, meta, "kp", "km", "+ run meta")
    ctx = C.append(wf, ctx, ss, "kp", "ks", "+ simulation constants")
    ctx = wf.formula(ctx, [
        ("t_sl", X.DBL, "IIF(IsNull([p_sl_attritional_threshold]) OR [p_sl_attritional_threshold] < [u_fit], [u_fit], "
                        "[p_sl_attritional_threshold])"),
        ("ref_lives", X.DBL, "IIF(IsNull([p_reference_lives]), [lives_q], [p_reference_lives])"),
        ("dist", X.STR, 'IIF(IsNull([p_sl_attritional_dist]), "LOGNORMAL", [p_sl_attritional_dist])'),
        ("ts_md", X.STR, 'DateTimeFormat([p_treaty_start], "%m-%d")'),
        ("ts_mm", X.STR, 'DateTimeFormat([p_treaty_start], "%m")'),
        ("is_ra", X.BOOL, '[p_treaty_basis] = "RA"'),
        ("s0", X.DBL, f"[p_seed] - {M - 1} * Floor([p_seed] / {M - 1}) + 1")], label="threshold, lives, calendar")
    ctx = wf.formula(ctx, [(f"start{j}", X.DBL, B4.jump("[s0]", JUMP5[j])) for j in (4, 5)], label="stream starts")

    # ------------------------------------------------------------------ attritional history
    wf.container("06.2 Attritional history by treaty year: total - large units, FGU trend, PRE_AP completion")
    ag = wf.input_yxdb(C.stage(v, "int_claims_aggregate"), label="int_claims_aggregate (stage 01)")
    ag = C.append(wf, wf.formula(ag, [("ka", X.INT, "1")], label="key"), ctx, "ka", "kp", "+ context")
    ag = wf.formula(ag, [
        ("amt", X.DBL, f"({nz('amount_paid')} + {nz('amount_os')}) * [p_qs_cedant_retention_pct]"),
        ("ty", X.INT64, "IIF([is_ra], " + C.ty_of("[cohort_month]") + ", " + C.ty_of("[incurred_month]") + ")")],
        label="aggregate: amount x QS, treaty year")
    ag, _ = wf.filter(ag, C.in_csv("[p_years_included]", "ToString([ty])"), label="years included")
    tot_a = wf.summarize(ag, [("ty", "GroupBy", "a_ty"), ("amt", "Sum", "total_agg")], label="aggregate by TY")
    un = wf.input_yxdb(C.stage(v, "exp_claimants"), label="exp_claimants (stage 02)")
    tot_u = wf.summarize(un, [("ty", "GroupBy", "u_ty"), ("amount_q", "Sum", "total_units")], label="units by TY")
    adj = wf.input_yxdb(C.stage(v, "bc_adjusted"), label="bc_adjusted (stage 03)")
    adj = C.append(wf, wf.formula(adj, [("kj", X.INT, "1")], label="key"),
                   wf.select(ctx, keep=["kp", "t_sl"], keep_unknown=False, label="t_sl"), "kj", "kp", "+ t_sl")
    lg, _ = wf.filter(adj, "[adj] > [t_sl]", label="large units (adj > t_sl)")
    lg_ty = wf.summarize(lg, [("ty", "GroupBy", "l_ty"), ("amount_q", "Sum", "large_raw"), ("adj", "Sum", "large_adj"),
                              ("acc_key", "Count", "n_large")], label="large units by TY")
    fx_, _ = wf.filter(adj, "[amount_q] > 0", label="units with an amount")
    fmax = wf.summarize(wf.formula(fx_, [("f_adj", X.DBL, "[adj_uncapped] / [amount_q]")], label="trend x completion"),
                        [("kj", "GroupBy", "kf"), ("f_adj", "Max", "f_max")], label="largest adjustment factor")

    wts = wf.input_yxdb(C.stage(v, "bc_weights"), label="bc_weights (stage 03)")
    ex = wf.input_yxdb(C.stage(v, "exp_exposure_ty"), label="exp_exposure_ty (stage 02)")
    yr = wf.join(wts, wf.select(ex, keep=["ty", "exposure_ly_full", "months_elapsed"], keep_unknown=False,
                                label="exposure"),
                 ["g_ty"], ["ty"], label="weights + exposure")["J"]
    yr = wf.left_join(yr, tot_a, ["g_ty"], ["a_ty"], label="+ aggregate total")
    yr = wf.left_join(yr, tot_u, ["g_ty"], ["u_ty"], label="+ unit total")
    yr = wf.left_join(yr, lg_ty, ["g_ty"], ["l_ty"], label="+ large units")
    trend = B3.trend_indices(wf, v, fgu=True)
    yr = wf.join(yr, wf.select(trend, keep=["tr_year", "trend_f"], keep_unknown=False, label="FGU trend"),
                 ["g_ty"], ["tr_year"], label="+ FGU trend")["J"]
    cp = wf.input_yxdb(C.stage(v, "ctl_completion"), label="ctl_completion (stage 00)")
    cp, _ = wf.filter(cp, '[side] = "ATTRITIONAL" AND IIF(IsNull([ap_band_from]), 1=1, [ap_band_from] = 0)',
                      label="ATTRITIONAL factors")
    cp = wf.formula(cp, [(f"f_{c.lower()}", X.DBL, f'IIF([component] = "{c}", [selected_factor], Null())')
                         for c in ("IBNR", "UNEARNED", "UNWRITTEN")], label="one column per component")
    cp = wf.summarize(cp, [("year", "GroupBy", "cf_year"), ("f_ibnr", "Max", "f_ibnr"), ("f_unearned", "Max", "f_unearned"),
                           ("f_unwritten", "Max", "f_unwritten")], label="by year")
    cp = wf.formula(cp, [("att_f", X.DBL, " * ".join(f"IIF(IsNull([{f}]), 1, [{f}])"
                                                     for f in ("f_ibnr", "f_unearned", "f_unwritten")))],
                    label="product (missing factor = 1)")
    cp = wf.select(cp, keep=["cf_year", "att_f"], keep_unknown=False, label="attritional completion")
    yr = wf.left_join(yr, cp, ["g_ty"], ["cf_year"], label="+ attritional completion")
    yr = C.append(wf, wf.formula(yr, [("ky", X.INT, "1")], label="key"), ctx, "ky", "kp", "+ context")
    yr = wf.formula(yr, [
        ("total_claims", X.DBL, f"IIF([m_has_line_detail], {nz('total_units')}, {nz('total_agg')})"),
        ("large_raw_", X.DBL, nz("large_raw")), ("n_large_", X.INT64, nz("n_large")),
        ("att_f_", X.DBL, "IIF(IsNull([att_f]), 1, [att_f])"),
        ("attritional_raw", X.DBL, "[total_claims] - [large_raw_]"),
        ("attritional_adj", X.DBL, "[attritional_raw] * [trend_f] * [att_f_]"),
        ("attritional_pppy", X.DBL, "[attritional_adj] / [exposure_ly_full]"),
        ("w_pppy", X.DBL, "[weight] * [attritional_pppy]"),
        ("pppy2", X.DBL, "[attritional_pppy] * [attritional_pppy]")], label="attritional cost per life")
    yr = wf.sort(yr, [("g_ty", "Ascending")], label="by treaty year")
    yo = wf.select(yr, keep=["g_ty", "weight", "exposure_ly_full", "total_claims", "n_large_", "large_raw_",
                             "attritional_raw", "months_elapsed", "trend_f", "att_f_", "attritional_adj",
                             "attritional_pppy", "t_sl"],
                   rename={"g_ty": "ty", "n_large_": "n_large", "large_raw_": "large_claims_raw",
                           "att_f_": "completion_f"},
                   keep_unknown=False, label="out_attritional_by_year fields")
    C.result(wf, yo, v, "out_attritional_by_year")

    # ------------------------------------------------------------------ attritional parameters
    wf.container("06.3 Attritional distribution for the quote year")
    ap_ = wf.summarize(yr, [("ky", "GroupBy", "ky"), ("w_pppy", "Sum", "mean_pppy"), ("attritional_pppy", "Sum", "s1"),
                            ("pppy2", "Sum", "s2"), ("g_ty", "Count", "n_years")], label="weighted mean, moments")
    ap_ = C.append(wf, ap_, ctx, "ky", "kp", "+ context")
    ap_ = wf.formula(ap_, [
        ("mean_unw", X.DBL, "[s1] / [n_years]"),
        ("var_s", X.DBL, "IIF([n_years] > 1, ([s2] - [n_years] * [mean_unw] * [mean_unw]) / ([n_years] - 1), Null())"),
        ("cov_hist", X.DBL, "IIF(IsNull([var_s]) OR [mean_unw] <= 0, Null(), Sqrt(IIF([var_s] > 0, [var_s], 0)) / "
                            "[mean_unw])"),
        ("cov_used", X.DBL, "IIF(IsNull([cov_hist]) OR [cov_hist] < [p_sl_cov_floor], [p_sl_cov_floor], [cov_hist])"),
        ("mu_attr", X.DBL, "[mean_pppy] * [lives_q] * [p_incidence_adj]"),
        ("sigma_ln", X.DBL, "Sqrt(Log(1 + [cov_used] * [cov_used]))"),
        ("m_ln", X.DBL, "IIF([mu_attr] > 0, Log([mu_attr]) - [sigma_ln] * [sigma_ln] / 2, Null())")],
        label="mean, CoV (floored), lognormal parameters")

    # ------------------------------------------------------------------ gross by iteration
    wf.container("06.4 Gross annual claims by iteration: attritional (Box-Muller on MINSTD) + large above t_sl")
    its = wf.generate_rows(ap_, "iter", X.INT64, "1", "[iter] <= [p_iterations]", "[iter] + 1", label="iterations")
    its = B4.stream(wf, its, "s4", "start4", "attritional uniform 1")
    its = B4.stream(wf, its, "s5", "start5", "attritional uniform 2")
    its = wf.formula(its, [
        ("z", X.DBL, f"Sqrt(-2 * Log([u_s4])) * Cos({TWO_PI} * [u_s5])"),
        ("attritional", X.DBL, 'IIF([dist] = "NORMAL", IIF([mu_attr] * (1 + [cov_used] * [z]) > 0, '
                               '[mu_attr] * (1 + [cov_used] * [z]), 0), IIF(IsNull([m_ln]), 0, '
                               'Exp([m_ln] + [sigma_ln] * [z])))')], label="attritional draw")
    sg = wf.input_yxdb(C.stage(v, "sim_gross"), label="sim_gross (stage 04)")
    gi = wf.join(its, wf.select(sg, keep=["iter", "large_gross", "large_gross_sl"], rename={"iter": "g_iter"},
                                keep_unknown=False, label="large claims"), ["iter"], ["g_iter"],
                 label="+ large claims")["J"]
    gi = wf.formula(gi, [("gross", X.DBL, "[attritional] + [large_gross_sl]")], label="gross = attritional + large")
    gi = wf.sort(gi, [("iter", "Ascending")], label="by iteration")
    wf.output_csv(wf.select(gi, keep=["iter", "attritional", "large_gross_sl", "gross"], keep_unknown=False,
                            label="out_gross_iterations fields"), results(v, "out_gross_iterations"),
                  label="out_gross_iterations.csv")      # large: CSV only
    gm = wf.summarize(gi, [("ky", "GroupBy", "kg"), ("gross", "Sum", "sum_gross"), ("attritional", "Sum", "sum_attr"),
                           ("large_gross_sl", "Sum", "sum_large"), ("iter", "Count", "n_iter")], label="gross sums")
    gm = wf.formula(gm, [("mean_gross", X.DBL, "[sum_gross] / [n_iter]")], label="mean gross")
    gv = C.append(wf, gi, wf.select(gm, keep=["kg", "mean_gross"], keep_unknown=False, label="mean"), "ky", "kg",
                  "+ mean gross")
    gv = wf.summarize(wf.formula(gv, [("d2", X.DBL, "([gross] - [mean_gross]) * ([gross] - [mean_gross])")],
                                 label="squared deviation"), [("ky", "GroupBy", "kg2"), ("d2", "Sum", "ss_gross")],
                      label="gross variance")
    gm = wf.join(gm, gv, ["kg"], ["kg2"], label="+ variance")["J"]
    gm = C.append(wf, gm, ap_, "kg", "ky", "+ attritional parameters")
    gm = wf.formula(gm, [
        ("budget", X.DBL, "IIF(IsNull([p_net_claims_budget]), [mean_gross], [p_net_claims_budget])"),
        ("budget_source", X.STR, 'IIF(IsNull([p_net_claims_budget]), "best-estimate gross (simulated mean)", '
                                 '"net_claims_budget input")'),
        ("sd_gross", X.DBL, "Sqrt([ss_gross] / [n_iter])"),
        ("mean_attritional_sim", X.DBL, "[sum_attr] / [n_iter]"),
        ("mean_large_sim", X.DBL, "[sum_large] / [n_iter]")], label="budget, gross statistics")
    gso = wf.select(gm, keep=["t_sl", "u_fit", "n_years", "mean_pppy", "mean_unw", "cov_hist", "cov_used", "dist",
                              "lives_q", "ref_lives", "p_incidence_adj", "mu_attr", "m_ln", "sigma_ln", "n_iter",
                              "mean_attritional_sim", "mean_large_sim", "mean_gross", "sd_gross", "budget",
                              "budget_source", "p_epi_quote_year"],
                    rename={"mean_pppy": "attritional_pppy_weighted", "mean_unw": "attritional_pppy_mean",
                            "dist": "attritional_dist", "lives_q": "lives_quote_year", "ref_lives": "reference_lives",
                            "p_incidence_adj": "incidence_adj", "mu_attr": "attritional_mean_q",
                            "n_iter": "iterations", "p_epi_quote_year": "epi"},
                    keep_unknown=False, label="out_gross_summary fields")
    C.result(wf, gso, v, "out_gross_summary")
    bud = wf.select(gm, keep=["kg", "budget", "p_epi_quote_year"], keep_unknown=False, label="budget")

    # ------------------------------------------------------------------ stop loss
    wf.container("06.5 Stop loss options: attachment and limit, base claims (gross or net of the PP option), recovery")
    lay = wf.input_yxdb(C.stage(v, "ctl_layers"), label="ctl_layers (stage 00)")
    sl_all, _ = wf.filter(lay, '[cover] = "SL" AND [active] = 1', label="active stop-loss options")
    sl, sl2 = wf.filter(sl_all, "[layer] = 1", label="layer 1 (an aggregate cover has one layer)")
    sl = wf.formula(sl, [
        ("l_label", X.STR, 'IIF(IsNull([label]), "SL option " + ToString([option_id]), [label])'),
        ("basis", X.STR, 'IIF(IsNull([sl_claims_basis]), "GROSS", [sl_claims_basis])'),
        ("measure", X.STR, 'IIF(IsNull([sl_measure]), "PCT_BUDGET", [sl_measure])'),
        ("cop", X.DBL, nz("co_participation_pct")), ("kl", X.INT, "1")], label="stop-loss fields")
    pg = wf.input_yxdb(C.stage(v, "ctl_programme"), label="ctl_programme (stage 00)")
    pg, _ = wf.filter(pg, '[active] = 1 AND !IsNull([option_id]) AND [cover] != "QS"', label="programme XoL / SL steps")
    pg = wf.sort(pg, [("cover", "Ascending"), ("step", "Ascending")], label="by cover, step")
    pg = wf.multirow(pg, "pg_rank", X.INT64, "IIF([Row-1:cover] = [cover], [Row-1:pg_rank] + 1, 1)", label="rank")
    pg, _ = wf.filter(pg, "[pg_rank] = 1", label="first step of each cover")
    pg = wf.formula(pg, [("kpg", X.INT, "1"), ("o_pp", X.INT64, 'IIF([cover] = "PP", [option_id], Null())'),
                         ("o_ev", X.INT64, 'IIF([cover] = "EV", [option_id], Null())'),
                         ("o_sl", X.INT64, 'IIF([cover] = "SL", [option_id], Null())'),
                         ("st_pp", X.INT64, 'IIF([cover] = "PP", [step], Null())'),
                         ("st_ev", X.INT64, 'IIF([cover] = "EV", [step], Null())'),
                         ("st_sl", X.INT64, 'IIF([cover] = "SL", [step], Null())')], label="option per cover")
    pg = wf.summarize(pg, [("kpg", "GroupBy", "kpg"), ("o_pp", "Max", "net_pp"), ("o_ev", "Max", "net_ev"),
                           ("o_sl", "Max", "prog_sl"), ("st_pp", "Max", "st_pp"), ("st_ev", "Max", "st_ev"),
                           ("st_sl", "Max", "st_sl")], label="programme options")
    sl = wf.left_join(sl, wf.select(pg, keep=["kpg", "net_pp", "net_ev"], keep_unknown=False, label="XoL options"),
                      ["kl"], ["kpg"], label="+ programme XoL options")
    sl = C.append(wf, sl, bud, "kl", "kg", "+ budget, EPI")
    sl = wf.formula(sl, [
        ("ap_amount", X.DBL, amount("measure", "sl_ap")),
        ("limit_amount", X.DBL, amount("measure", "sl_limit")),
        ("pp_key", X.INT64, 'IIF([basis] = "NET_OF_XOL" AND !IsNull([net_pp]), [net_pp], -1)'),
        ("ev_key", X.INT64, 'IIF([basis] = "NET_OF_XOL" AND !IsNull([net_ev]), [net_ev], -1)'),
        ("ev_net_pp", X.BOOL, '[basis] = "NET_OF_XOL" AND !IsNull([net_pp])'),
        ("s_key", X.STR, '"2 SL " + ToString([option_id])'),
        ("ap_pct_budget", X.DBL, "IIF([budget] > 0, [ap_amount] / [budget], Null())"),
        ("limit_pct_budget", X.DBL, "IIF([budget] > 0, [limit_amount] / [budget], Null())"),
        ("ap_pct_epi", X.DBL, "IIF([p_epi_quote_year] > 0, [ap_amount] / [p_epi_quote_year], Null())"),
        ("limit_pct_epi", X.DBL, "IIF([p_epi_quote_year] > 0, [limit_amount] / [p_epi_quote_year], Null())")],
        label="attachment and limit amounts")
    slc = wf.select(sl, keep=["kl", "s_key", "option_id", "layer", "l_label", "basis", "measure", "sl_ap", "sl_limit", "cop",
                              "net_pp", "pp_key", "net_ev", "ev_key", "ev_net_pp", "ap_amount", "limit_amount",
                              "ap_pct_budget", "limit_pct_budget",
                              "ap_pct_epi", "limit_pct_epi"], keep_unknown=False, label="stop-loss terms")

    # per-person option recoveries per iteration (all layers of the option; reinstatement usage by layer)
    si = wf.input_yxdb(C.stage(v, "sim_iterations"), label="sim_iterations (stage 04)")
    si = wf.formula(si, [("ru1", X.DBL, "IIF([layer] = 1, [reinst_usage], 0)"),
                         ("ru2", X.DBL, "IIF([layer] = 2, [reinst_usage], 0)")], label="reinstatement usage by layer")
    pp = wf.summarize(si, [("option_id", "GroupBy", "pp_opt"), ("iter", "GroupBy", "pp_iter"),
                           ("recovery", "Sum", "pp_rec"), ("ru1", "Sum", "pp_ru1"), ("ru2", "Sum", "pp_ru2")],
                      label="PP option recovery by iteration")
    ppl = wf.summarize(si, [("option_id", "GroupBy", "lb_opt"), ("l_label", "First", "pp_label"),
                            ("layer", "Max", "pp_layers")], label="PP option labels")

    # per-event option recoveries per iteration: gross basis and net of the programme PP option
    se = wf.input_yxdb(C.stage(v, "sim_ev_iterations"), label="sim_ev_iterations (stage 05)")
    se = wf.formula(se, [("ru1", X.DBL, "IIF([layer] = 1, [reinst_usage], 0)"),
                         ("ru2", X.DBL, "IIF([layer] = 2, [reinst_usage], 0)"),
                         ("rn1", X.DBL, "IIF([layer] = 1, [reinst_usage_net], 0)"),
                         ("rn2", X.DBL, "IIF([layer] = 2, [reinst_usage_net], 0)")], label="reinstatement usage by layer")
    evr = wf.summarize(se, [("option_id", "GroupBy", "ev_opt"), ("iter", "GroupBy", "ev_iter"),
                            ("recovery", "Sum", "ev_rec"), ("recovery_net", "Sum", "ev_rec_net"),
                            ("ru1", "Sum", "ev_ru1"), ("ru2", "Sum", "ev_ru2"), ("rn1", "Sum", "ev_rn1"),
                            ("rn2", "Sum", "ev_rn2")], label="EV option recovery by iteration")
    evl = wf.summarize(se, [("option_id", "GroupBy", "lb_ev"), ("l_label", "First", "ev_label")],
                       label="EV option labels")

    gk = wf.select(gi, keep=["ky", "iter", "gross"], keep_unknown=False, label="gross by iteration")
    sx = C.append(wf, gk, slc, "ky", "kl", "iterations x stop-loss options")
    sx = wf.left_join(sx, pp, ["pp_key", "iter"], ["pp_opt", "pp_iter"], label="+ PP recovery (NET_OF_XOL)")
    sx = wf.left_join(sx, evr, ["ev_key", "iter"], ["ev_opt", "ev_iter"], label="+ EV recovery (NET_OF_XOL)")
    sx = wf.formula(sx, [
        ("pp_used", X.DBL, nz("pp_rec")),
        ("ev_used", X.DBL, f"IIF([ev_net_pp], {nz('ev_rec_net')}, {nz('ev_rec')})"),
        ("base", X.DBL, "[gross] - [pp_used] - [ev_used]"),
        ("over", X.DBL, "IIF(IsNull([ap_amount]), 0, IIF([base] - [ap_amount] > 0, [base] - [ap_amount], 0))"),
        ("recovery", X.DBL, "IIF(IsNull([limit_amount]) OR [over] < [limit_amount], [over], [limit_amount]) * "
                            "(1 - [cop])"),
        ("net_claims", X.DBL, "[base] - [recovery]"),
        ("recovery_total", X.DBL, "[pp_used] + [ev_used] + [recovery]"),
        ("ru_l1", X.DBL, nz("pp_ru1")), ("ru_l2", X.DBL, nz("pp_ru2")),
        ("ru_ev1", X.DBL, f"IIF([ev_net_pp], {nz('ev_rn1')}, {nz('ev_ru1')})"),
        ("ru_ev2", X.DBL, f"IIF([ev_net_pp], {nz('ev_rn2')}, {nz('ev_ru2')})")], label="stop-loss recovery")
    sxo = wf.sort(wf.select(sx, keep=["option_id", "layer", "iter", "gross", "pp_used", "ev_used", "base", "recovery",
                                      "net_claims"], keep_unknown=False, label="out_sl_iterations fields"),
                  [("option_id", "Ascending"), ("layer", "Ascending"), ("iter", "Ascending")], label="order")
    wf.output_csv(sxo, results(v, "out_sl_iterations"), label="out_sl_iterations.csv")      # large: CSV only

    # ------------------------------------------------------------------ structures
    wf.container("06.6 Structures: none, each PP / EV / SL option, programme; net claims quantiles for Exhibit C2")
    SF = ["struct_key", "cover", "option_id", "pp_option", "ev_option", "sl_option", "l_label", "iter", "gross",
          "recovery", "recovery_total", "net_claims", "ru_l1", "ru_l2", "ru_ev1", "ru_ev2"]
    s_none = wf.formula(gk, [("struct_key", X.STR, '"0 NONE"'), ("cover", X.STR, '"NONE"'), ("option_id", X.INT64, "0"),
                             ("pp_option", X.INT64, "0"), ("ev_option", X.INT64, "0"), ("sl_option", X.INT64, "0"),
                             ("l_label", X.STR, '"Without RI"'),
                             ("recovery", X.DBL, "0"), ("recovery_total", X.DBL, "0"), ("net_claims", X.DBL, "[gross]"),
                             ("ru_l1", X.DBL, "0"), ("ru_l2", X.DBL, "0"), ("ru_ev1", X.DBL, "0"),
                             ("ru_ev2", X.DBL, "0")], label="structure: without RI")
    s_pp = wf.join(pp, wf.select(gk, keep=["iter", "gross"], rename={"iter": "gk_iter"}, keep_unknown=False,
                                 label="gross"), ["pp_iter"], ["gk_iter"], label="PP options x gross")["J"]
    s_pp = wf.join(s_pp, ppl, ["pp_opt"], ["lb_opt"], label="+ label")["J"]
    s_pp = wf.formula(s_pp, [
        ("struct_key", X.STR, '"1 PP " + ToString([pp_opt])'), ("cover", X.STR, '"PP"'),
        ("option_id", X.INT64, "[pp_opt]"), ("pp_option", X.INT64, "[pp_opt]"), ("ev_option", X.INT64, "0"),
        ("sl_option", X.INT64, "0"), ("l_label", X.STR, "[pp_label]"),
        ("iter", X.INT64, "[pp_iter]"), ("recovery", X.DBL, "[pp_rec]"), ("recovery_total", X.DBL, "[pp_rec]"),
        ("net_claims", X.DBL, "[gross] - [pp_rec]"), ("ru_l1", X.DBL, "[pp_ru1]"), ("ru_l2", X.DBL, "[pp_ru2]"),
        ("ru_ev1", X.DBL, "0"), ("ru_ev2", X.DBL, "0")], label="structure: PP option")
    s_ev = wf.join(evr, wf.select(gk, keep=["iter", "gross"], rename={"iter": "ge_iter"}, keep_unknown=False,
                                  label="gross"), ["ev_iter"], ["ge_iter"], label="EV options x gross")["J"]
    s_ev = wf.join(s_ev, evl, ["ev_opt"], ["lb_ev"], label="+ label")["J"]
    s_ev = wf.formula(s_ev, [
        ("struct_key", X.STR, '"3 EV " + ToString([ev_opt])'), ("cover", X.STR, '"EV"'),
        ("option_id", X.INT64, "[ev_opt]"), ("pp_option", X.INT64, "0"), ("ev_option", X.INT64, "[ev_opt]"),
        ("sl_option", X.INT64, "0"), ("l_label", X.STR, "[ev_label]"), ("iter", X.INT64, "[ev_iter]"),
        ("recovery", X.DBL, "[ev_rec]"), ("recovery_total", X.DBL, "[ev_rec]"), ("net_claims", X.DBL, "[gross] - [ev_rec]"),
        ("ru_l1", X.DBL, "0"), ("ru_l2", X.DBL, "0"), ("ru_ev1", X.DBL, "[ev_ru1]"), ("ru_ev2", X.DBL, "[ev_ru2]")],
        label="structure: EV option (gross basis)")
    s_sl = wf.formula(sx, [("struct_key", X.STR, '"2 SL " + ToString([option_id])'), ("cover", X.STR, '"SL"'),
                           ("pp_option", X.INT64, "IIF([pp_key] > 0, [pp_key], 0)"),
                           ("ev_option", X.INT64, "IIF([ev_key] > 0, [ev_key], 0)"),
                           ("sl_option", X.INT64, "[option_id]")], label="structure: SL option")
    # programme: PP -> EV (net of PP) -> SL (on its own basis), the Programme sheet's options
    pr = C.append(wf, gk, wf.select(pg, keep=["kpg", "net_pp", "net_ev", "prog_sl"], keep_unknown=False,
                                    label="programme"), "ky", "kpg", "iterations x programme")
    pr = C.append(wf, pr, wf.select(ctx, keep=["kp", "p_evaluate_mode"], keep_unknown=False, label="mode"), "ky", "kp",
                  "+ evaluate_mode")
    pr, _ = wf.filter(pr, '[p_evaluate_mode] = "PROGRAMME"', label="PROGRAMME mode only")
    pr = wf.formula(pr, [("pk_pp", X.INT64, "IIF(IsNull([net_pp]), -1, [net_pp])"),
                         ("pk_ev", X.INT64, "IIF(IsNull([net_ev]), -1, [net_ev])"),
                         ("pk_sl", X.INT64, "IIF(IsNull([prog_sl]), -1, [prog_sl])")], label="programme keys")
    pr = wf.left_join(pr, wf.select(pp, keep=["pp_opt", "pp_iter", "pp_rec", "pp_ru1", "pp_ru2"],
                                    rename={"pp_opt": "q_pp", "pp_iter": "q_ppi", "pp_rec": "q_pp_rec",
                                            "pp_ru1": "q_ru1", "pp_ru2": "q_ru2"}, keep_unknown=False, label="PP"),
                      ["pk_pp", "iter"], ["q_pp", "q_ppi"], label="+ PP")
    pr = wf.left_join(pr, wf.select(evr, keep=["ev_opt", "ev_iter", "ev_rec", "ev_rec_net", "ev_ru1", "ev_ru2", "ev_rn1",
                                               "ev_rn2"], rename={"ev_opt": "q_ev", "ev_iter": "q_evi"},
                                    keep_unknown=False, label="EV"), ["pk_ev", "iter"], ["q_ev", "q_evi"], label="+ EV")
    pr = wf.left_join(pr, wf.select(sx, keep=["option_id", "iter", "recovery", "basis"],
                                    rename={"option_id": "q_sl", "iter": "q_sli", "recovery": "q_sl_rec",
                                            "basis": "q_sl_basis"}, keep_unknown=False, label="SL"),
                      ["pk_sl", "iter"], ["q_sl", "q_sli"], label="+ SL")
    pr = wf.formula(pr, [
        ("struct_key", X.STR, '"4 PROGRAMME"'), ("cover", X.STR, '"PROGRAMME"'), ("option_id", X.INT64, "0"),
        ("pp_option", X.INT64, "IIF([pk_pp] > 0, [pk_pp], 0)"), ("ev_option", X.INT64, "IIF([pk_ev] > 0, [pk_ev], 0)"),
        ("sl_option", X.INT64, "IIF([pk_sl] > 0, [pk_sl], 0)"), ("l_label", X.STR, '"Programme"'),
        ("ev_u", X.DBL, f"IIF([pk_pp] > 0, {nz('ev_rec_net')}, {nz('ev_rec')})"),
        ("recovery", X.DBL, f"{nz('q_pp_rec')} + [ev_u] + {nz('q_sl_rec')}"), ("recovery_total", X.DBL, "[recovery]"),
        ("net_claims", X.DBL, "[gross] - [recovery]"), ("ru_l1", X.DBL, nz("q_ru1")), ("ru_l2", X.DBL, nz("q_ru2")),
        ("ru_ev1", X.DBL, f"IIF([pk_pp] > 0, {nz('ev_rn1')}, {nz('ev_ru1')})"),
        ("ru_ev2", X.DBL, f"IIF([pk_pp] > 0, {nz('ev_rn2')}, {nz('ev_ru2')})")], label="structure: programme")
    st = wf.union([wf.select(s, keep=SF, keep_unknown=False, label="structure fields")
                   for s in (s_none, s_pp, s_ev, s_sl, pr)], label="all structures")
    st = wf.sort(st, [("struct_key", "Ascending"), ("iter", "Ascending")], label="by structure, iteration")

    # quantiles: net claims (with the reinstatement usage of that iteration) and the cover's own recovery
    qg = wf.generate_rows(wf.select(gm, keep=["kg", "n_iter"], keep_unknown=False, label="n"), "qm", X.INT64, "1",
                          "[qm] <= 999", "[qm] + 1", label="q = 0.001 .. 0.999")
    qg = wf.formula(qg, [("q_rank", X.INT64, "IIF(Ceil([qm] * [n_iter] / 1000) < 1, 1, Ceil([qm] * [n_iter] / 1000))")],
                    label="nearest rank")
    qg = wf.select(qg, keep=["qm", "q_rank"], keep_unknown=False, label="ranks")
    rn = quantile_rows(wf, st, "net_claims", "net claims")
    qn = wf.join(rn, qg, ["rk"], ["q_rank"], label="net claims at q")["J"]
    qn = wf.select(qn, keep=["struct_key", "cover", "option_id", "pp_option", "ev_option", "sl_option", "l_label", "qm",
                             "net_claims", "ru_l1", "ru_l2", "ru_ev1", "ru_ev2"], keep_unknown=False, label="net quantiles")
    rr = quantile_rows(wf, st, "recovery", "recovery")
    qr = wf.join(rr, wf.select(qg, keep=["qm", "q_rank"], rename={"qm": "r_qm", "q_rank": "r_rank"},
                               keep_unknown=False, label="ranks"), ["rk"], ["r_rank"], label="recovery at q")["J"]
    qr = wf.select(qr, keep=["struct_key", "r_qm", "recovery"], rename={"struct_key": "r_key"}, keep_unknown=False,
                   label="recovery quantiles")
    qo = wf.join(qn, qr, ["struct_key", "qm"], ["r_key", "r_qm"], label="net + recovery quantiles")["J"]
    qo = wf.formula(qo, [("q", X.DBL, "[qm] / 1000")], label="q")
    qo = wf.select(qo, keep=["struct_key", "cover", "option_id", "pp_option", "ev_option", "sl_option", "l_label", "q",
                             "qm", "net_claims", "ru_l1", "ru_l2", "ru_ev1", "ru_ev2", "recovery"],
                   rename={"ru_l1": "ru_l1_at_q", "ru_l2": "ru_l2_at_q", "ru_ev1": "ru_ev1_at_q", "ru_ev2": "ru_ev2_at_q"},
                   keep_unknown=False, label="out_struct_quantiles fields")
    qo = wf.sort(qo, [("struct_key", "Ascending"), ("q", "Ascending")], label="order")
    C.result(wf, qo, v, "out_struct_quantiles")

    # summary (two-pass standard deviations)
    sm = wf.summarize(st, [("struct_key", "GroupBy", "struct_key"), ("cover", "First", "cover"),
                           ("option_id", "First", "option_id"), ("pp_option", "First", "pp_option"),
                           ("ev_option", "First", "ev_option"), ("sl_option", "First", "sl_option"),
                           ("l_label", "First", "l_label"), ("gross", "Sum", "s_gross"), ("recovery", "Sum", "s_rec"),
                           ("recovery_total", "Sum", "s_rec_t"), ("net_claims", "Sum", "s_net"),
                           ("ru_l1", "Sum", "s_ru1"), ("ru_l2", "Sum", "s_ru2"), ("ru_ev1", "Sum", "s_rv1"),
                           ("ru_ev2", "Sum", "s_rv2"), ("iter", "Count", "n_iter")],
                      label="structure sums")
    sm = wf.formula(sm, [("mean_gross", X.DBL, "[s_gross] / [n_iter]"), ("mean_recovery", X.DBL, "[s_rec] / [n_iter]"),
                         ("mean_recovery_total", X.DBL, "[s_rec_t] / [n_iter]"),
                         ("mean_net", X.DBL, "[s_net] / [n_iter]"), ("mean_ru_l1", X.DBL, "[s_ru1] / [n_iter]"),
                         ("mean_ru_l2", X.DBL, "[s_ru2] / [n_iter]"), ("mean_ru_ev1", X.DBL, "[s_rv1] / [n_iter]"),
                         ("mean_ru_ev2", X.DBL, "[s_rv2] / [n_iter]")], label="means")
    dv = wf.join(st, wf.select(sm, keep=["struct_key", "mean_recovery", "mean_net"],
                               rename={"struct_key": "m_key", "mean_recovery": "m_rec", "mean_net": "m_net"},
                               keep_unknown=False, label="means"), ["struct_key"], ["m_key"], label="+ means")["J"]
    dv = wf.formula(dv, [("d_rec", X.DBL, "([recovery] - [m_rec]) * ([recovery] - [m_rec])"),
                         ("d_net", X.DBL, "([net_claims] - [m_net]) * ([net_claims] - [m_net])"),
                         ("zero", X.DBL, "IIF([recovery] = 0, 1, 0)")], label="squared deviations")
    dv = wf.summarize(dv, [("struct_key", "GroupBy", "d_key"), ("d_rec", "Sum", "ss_rec"), ("d_net", "Sum", "ss_net"),
                           ("zero", "Sum", "n_zero")], label="deviation sums")
    sm = wf.join(sm, dv, ["struct_key"], ["d_key"], label="+ deviations")["J"]
    sm = wf.left_join(sm, wf.select(slc, keep=["s_key", "basis", "measure", "sl_ap", "sl_limit", "ap_amount",
                                               "limit_amount", "ap_pct_budget", "limit_pct_budget", "ap_pct_epi",
                                               "limit_pct_epi"], keep_unknown=False, label="SL terms"),
                      ["struct_key"], ["s_key"], label="+ SL terms")
    sm = wf.formula(sm, [("kz", X.INT, "1"),
                         ("sd_recovery", X.DBL, "Sqrt([ss_rec] / [n_iter])"), ("sd_net", X.DBL, "Sqrt([ss_net] / [n_iter])"),
                         ("p_zero", X.DBL, "[n_zero] / [n_iter]")], label="statistics")
    sm = C.append(wf, sm, bud, "kz", "kg", "+ budget")
    smo = wf.select(sm, keep=["struct_key", "cover", "option_id", "pp_option", "ev_option", "sl_option", "l_label",
                              "n_iter", "mean_gross", "mean_recovery", "sd_recovery", "p_zero", "mean_recovery_total",
                              "mean_net", "sd_net", "mean_ru_l1", "mean_ru_l2", "mean_ru_ev1", "mean_ru_ev2", "basis", "measure", "sl_ap", "sl_limit", "ap_amount",
                              "limit_amount", "ap_pct_budget", "limit_pct_budget", "ap_pct_epi", "limit_pct_epi",
                              "budget", "p_epi_quote_year"],
                    rename={"n_iter": "iterations", "basis": "sl_basis", "measure": "sl_measure",
                            "p_epi_quote_year": "epi"}, keep_unknown=False, label="out_struct_summary fields")
    C.result(wf, wf.sort(smo, [("struct_key", "Ascending")], label="order"), v, "out_struct_summary")

    # ------------------------------------------------------------------ exposed lives
    wf.container("06.7 Exposed lives: PP = share of the latest treaty year's member days with limit > AP")
    lt = wf.summarize(wf.formula(wts, [("kw", X.INT, "1")], label="key"), [("kw", "GroupBy", "kw"),
                                                                          ("g_ty", "Max", "latest_ty")],
                      label="latest treaty year")
    mb = wf.input_yxdb(C.stage(v, "int_members"), label="int_members (stage 01)")
    mb = C.append(wf, wf.formula(mb, [("kb", X.INT, "1")], label="key"),
                  wf.select(ctx, keep=["kp", "ts_md"], keep_unknown=False, label="calendar"), "kb", "kp", "+ calendar")
    mb = C.append(wf, mb, lt, "kb", "kw", "+ latest TY")
    mb = wf.formula(mb, [("m_ty", X.INT64, C.ty_of("[cohort_month]"))], label="member treaty year")
    mb, _ = wf.filter(mb, "[m_ty] = [latest_ty]", label="latest treaty year members")
    mb = wf.formula(mb, [("lim_key", X.STR, 'IIF(IsNull([annual_limit]), "NA", ToString([annual_limit], 0))'),
                         ("w", X.DBL, "[cov_days]")], label="limit key (text: no Group By on Double)")
    mb = wf.summarize(mb, [("lim_key", "GroupBy", "lim_key"), ("annual_limit", "First", "annual_limit"),
                           ("w", "Sum", "w"), ("kb", "First", "kb")], label="member days by limit")
    lay_all, _ = wf.filter(lay, "[active] = 1", label="active options")
    lay_all = wf.formula(lay_all, [
        ("l_label", X.STR, 'IIF(IsNull([label]), [cover] + " option " + ToString([option_id]), [label])'),
        ("lk", X.STR, '[cover] + "|" + ToString([option_id]) + "|" + ToString([layer])'), ("kx", X.INT, "1")],
        label="option key")
    ppx, _ = wf.filter(lay_all, '[cover] = "PP"', label="PP options")
    ppx = C.append(wf, wf.select(ppx, keep=["lk", "ap", "kx"], keep_unknown=False, label="PP AP"), mb, "kx", "kb",
                   "options x limits")
    ppx = wf.formula(ppx, [("w_exp", X.DBL, "IIF(IsNull([annual_limit]) OR [annual_limit] > [ap], [w], 0)"),
                           ("w_na", X.DBL, "IIF(IsNull([annual_limit]), [w], 0)")], label="exposed days")
    ppx = wf.summarize(ppx, [("lk", "GroupBy", "e_lk"), ("w_exp", "Sum", "w_exp"), ("w", "Sum", "w_all"),
                             ("w_na", "Sum", "w_na")], label="share by option")
    el = wf.left_join(lay_all, ppx, ["lk"], ["e_lk"], label="+ member shares")
    el = C.append(wf, el, wf.select(ctx, keep=["kp", "lives_q", "ref_lives", "m_has_member_exposure"],
                                    keep_unknown=False, label="lives"), "kx", "kp", "+ lives")
    el = wf.formula(el, [
        ("share_exposed", X.DBL, 'IIF([cover] = "PP" AND !IsNull([w_all]) AND [w_all] > 0, [w_exp] / [w_all], 1)'),
        ("exposed_lives", X.DBL, "[share_exposed] * [lives_q]"),
        ("basis_note", X.STR, 'IIF([cover] != "PP", "all lives", IIF(IsNull([w_all]) OR [w_all] <= 0, '
                              '"all lives (no member limits)", "member days with benefit limit > AP, latest TY"))'),
        ("w_na_", X.DBL, nz("w_na"))], label="exposed lives")
    elo = wf.select(el, keep=["cover", "option_id", "layer", "l_label", "ap", "share_exposed", "exposed_lives",
                              "lives_q", "ref_lives", "basis_note"],
                    rename={"lives_q": "lives_quote_year", "ref_lives": "reference_lives"}, keep_unknown=False,
                    label="out_exposed_lives fields")
    C.result(wf, wf.sort(elo, [("cover", "Descending"), ("option_id", "Ascending"), ("layer", "Ascending")],
                         label="order"), v, "out_exposed_lives")

    # ------------------------------------------------------------------ checks
    wf.container("06.8 Checks and recon")
    g1, _ = wf.filter(yr, "[attritional_raw] < 0", label="G01 negative attritional")
    checks.append(C.check_formula(wf, g1, sn, "G01", "STOP", '"TY " + ToString([g_ty])',
                                  '"large units exceed total claims: aggregate incomplete or listing inconsistent"'))
    g11, _ = wf.filter(yr, "[months_elapsed] < 12 AND IsNull([att_f])", label="G11 incomplete year")
    checks.append(C.check_formula(wf, g11, sn, "G11", "FLAG", '"TY " + ToString([g_ty])',
                                  'ToString([months_elapsed]) + " months elapsed and no ATTRITIONAL completion factor: '
                                  'attritional cost understated and CoV overstated"'))
    g2, _ = wf.filter(ctx, "!IsNull([p_sl_attritional_threshold]) AND [p_sl_attritional_threshold] < [u_fit]",
                      label="G02 threshold below u")
    checks.append(C.check_formula(wf, g2, sn, "G02", "FLAG", '"sl_attritional_threshold"',
                                  '"below the fitting threshold u: large / attritional split raised to u = " + '
                                  'ToString([u_fit], 0)'))
    g3, _ = wf.filter(ap_, "[n_years] < 3 OR IsNull([cov_hist]) OR [cov_hist] < [p_sl_cov_floor]",
                      label="G03 CoV floor")
    checks.append(C.check_formula(wf, g3, sn, "G03", "INFO", '"attritional CoV"',
                                  '"CoV floor used (" + ToString([cov_used], 3) + "; history: " + '
                                  'IIF(IsNull([cov_hist]), "n/a", ToString([cov_hist], 3)) + ", " + '
                                  'ToString([n_years]) + " years)"'))
    g4, _ = wf.filter(sl, '[basis] = "NET_OF_XOL" AND IsNull([net_pp]) AND IsNull([net_ev])', label="G04 no XoL option")
    checks.append(C.check_formula(wf, g4, sn, "G04", "STOP", "[l_label]",
                                  '"NET_OF_XOL needs an active PP or EV option in the Programme sheet"'))
    g12 = C.append(wf, wf.formula(pg, [("kq", X.INT, "1")], label="key"),
                   wf.select(ctx, keep=["kp", "p_evaluate_mode"], keep_unknown=False, label="mode"), "kq", "kp", "+ mode")
    g12, _ = wf.filter(g12, '[p_evaluate_mode] = "PROGRAMME" AND ((!IsNull([st_ev]) AND !IsNull([st_pp]) AND '
                            '[st_ev] < [st_pp]) OR (!IsNull([st_sl]) AND ((!IsNull([st_pp]) AND [st_sl] < [st_pp]) OR '
                            '(!IsNull([st_ev]) AND [st_sl] < [st_ev]))))', label="G12 order")
    checks.append(C.check_formula(wf, g12, sn, "G12", "FLAG", '"Programme"',
                                  '"order of application other than PP -> EV -> SL: the tool applies PP -> EV -> SL"'))
    g5, _ = wf.filter(ctx, '[p_claims_trigger] = "PAID" AND ![m_has_line_detail]', label="G05 paid trigger")
    checks.append(C.check_formula(wf, g5, sn, "G05", "FLAG", '"claims_trigger"',
                                  '"aggregate claims carry no paid month: attritional allocated by incurred month"'))
    g6 = C.append(wf, ctx, fmax, "kp", "kf", "+ largest factor")
    g6, _ = wf.filter(g6, "![m_has_line_detail] AND [m_listing_threshold] * [f_max] > [t_sl]", label="G06 listing")
    checks.append(C.check_formula(wf, g6, sn, "G06", "FLAG", '"listing_threshold"',
                                  '"claimants below the listing threshold can exceed t_sl once adjusted: some large '
                                  'claims stay in the attritional history"'))
    g7, _ = wf.filter(el, '[cover] = "PP" AND (IsNull([w_all]) OR [w_all] <= 0 OR [w_na_] > 0)', label="G07 limits")
    checks.append(C.check_formula(wf, g7, sn, "G07", "FLAG", "[l_label]",
                                  '"benefit limits missing for some or all lives: they are counted as exposed"'))
    g8, _ = wf.filter(sm, "[mean_ru_l1] + [mean_ru_l2] + [mean_ru_ev1] + [mean_ru_ev2] > 0", label="G08 reinstatements")
    checks.append(C.check_formula(wf, g8, sn, "G08", "INFO", "[l_label]",
                                  '"reinstatement premium in Exhibit C2 taken from the iteration at each net-claims '
                                  'quantile (approximation)"'))
    g10 = wf.formula(sl2, [("l_label", X.STR, 'IIF(IsNull([label]), "SL option " + ToString([option_id]), [label])')],
                     label="label")
    checks.append(C.check_formula(wf, g10, sn, "G10", "FLAG", "[l_label]",
                                  '"stop-loss layer 2 ignored: an aggregate option has one layer"'))
    g9, _ = wf.filter(sl, 'IsNull([sl_ap]) OR ([measure] != "PCT_BUDGET" AND [measure] != "PCT_EPI" AND '
                          '[measure] != "AMOUNT")', label="G09 SL terms")
    checks.append(C.check_formula(wf, g9, sn, "G09", "STOP", "[l_label]", '"sl_ap missing or sl_measure unknown"'))
    C.recon_rows(wf, [(yr, "attritional_by_year", "attritional_adj", "attritional_adj"),
                      (gi, "gross_iterations", "rows", None), (gi, "gross_iterations", "gross", "gross"),
                      (sg, "sim_gross", "rows", None), (sxo, "sl_iterations", "rows", None),
                      (sxo, "sl_iterations", "recovery", "recovery"), (st, "structures", "rows", None)], v, sn)
    C.finish_checks(wf, checks, v, sn)
    return C.save(wf, v, "06_stop_loss.yxmd")


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build(v))
