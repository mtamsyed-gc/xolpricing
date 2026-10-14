"""Stage 05 - per-event XoL (catastrophe / accumulation), native Alteryx tools only (scope §2, §6; methodology §10).

Reads int_claims, int_run_meta (stage 01), exp_exposure_ty (stage 02), bc_weights, bc_lives (stage 03), ctl_params,
ctl_layers, ctl_eventrule, ctl_evscenarios, ctl_programme, ctl_trend (stage 00).

    Events    SUPPLIED: lines with an event_id. CLUSTER adds accident-coded lines (cause_code, no event_id) grouped by
              group_key and cause, a new event starting when the admission (else treatment) date is more than
              window_days after the current cluster's first date. Events need >= min_lives lives; the event's treaty
              year is that of its first date (both bases).
    Lives     per-life event cost = sum of the life's event lines x QS retention x large-claim trend to the quote year,
              capped at the benefit / policy limit. Net of the programme's PP option (each life's cost through that
              option's layers) for the programme view.
    Det.      event loss -> EV layer (AP, limit, co-participation), AAD / AAL on each treaty year's total; burning cost
              per life on full-year exposure, stage-03 weights; expected cost = BC x lives_Q x incidence.
    Freq.     experience: Poisson, lambda = sum_ty w_ty x events_ty / exposure_ty x lives_Q x incidence; scenarios:
              Poisson(1 / return period) each (ev_freq_source EXPERIENCE / SCENARIOS / BOTH).
    Size      experience event: lives drawn from the historical events' life counts, each life's cost drawn from the
              pooled historical per-life costs; scenario event: lives x cost per life.
    RNG       MINSTD streams at offsets 2^24 (experience counts), 2^25 (lives), 2^26 (per-life costs), 2^27 (scenario
              counts): below the stop-loss streams (2^28 ...) and the per-person streams (2^29 ...).
Writes results/out_ev_events.csv, out_ev_bc_by_year.csv, out_ev_bc_summary.csv, out_ev_scenarios.csv,
out_ev_sim_summary.csv, out_ev_sim_quantiles.csv, out_ev_iterations.csv; stage sim_ev_iterations; checks_05, recon_05.
"""
from __future__ import annotations

import xol_common as C
from xol_common import X
import build_stage03 as B3
import build_stage04 as B4

M, A, BIG = B4.M, B4.A, B4.BIG
OFFSETS = {6: 1 << 24, 7: 1 << 25, 8: 1 << 26, 9: 1 << 27}
JUMP = {j: pow(A, o, M) for j, o in OFFSETS.items()}
QUANTILES = B4.QUANTILES
PARAMS = ["iterations", "seed", "incidence_adj", "ev_freq_source", "full_cred_n", "treaty_basis",
          "qs_cedant_retention_pct", "years_included", "treaty_start", "policy_limit"]


def nz(f):
    return f"IIF(IsNull([{f}]), 0, [{f}])"


def xl_amount(loss, ap="[ap]", lim="[lim]", cop="[cop]"):
    over = f"({loss} - {ap})"
    return f"IIF({over} <= 0, 0, IIF({over} < {lim}, {over}, {lim})) * (1 - {cop})"


def annual_terms(wf, src, label):
    """AAD / AAL on the annual total [xl] -> [recovery], [reinst_usage]; [xl_net] -> [recovery_net], [reinst_usage_net]."""
    def after(x):
        return f"IIF([{x}] - {nz('aad')} > 0, [{x}] - {nz('aad')}, 0)"
    agg = (f"IIF(!IsNull([aal]), [aal], IIF(IsNull([reinstatements_n]) OR IsNull([limit]), {BIG}, "
           "(1 + [reinstatements_n]) * [limit]))")
    out = [("agg_limit", X.DBL, agg)]
    for x, r, ru in (("xl", "recovery", "reinst_usage"), ("xl_net", "recovery_net", "reinst_usage_net")):
        out += [(r, X.DBL, f"IIF({after(x)} < [agg_limit], {after(x)}, [agg_limit])"),
                (ru, X.DBL, "IIF(IsNull([reinstatement_cost_pct]) OR IsNull([limit]) OR [limit] <= 0, 0, "
                            f"[reinstatement_cost_pct] * IIF(IsNull([reinstatements_n]), [{r}], "
                            f"IIF([{r}] < [reinstatements_n] * [limit], [{r}], [reinstatements_n] * [limit])) / [limit])")]
    return wf.formula(src, out, label=label)


def pp_net(wf, src, cost, cap, ppl, idf, label):
    """Each row's cost net of the programme PP option (its layers applied to the cost): adds [net_<cost>]."""
    x = C.append(wf, wf.formula(src, [("kpx", X.INT, "1")], label="key"), ppl, "kpx", "kpp", f"{label}: x PP layers")
    lim = (f'IIF([pp_basis] = "TO_BENEFIT_LIMIT", IIF({cap} >= {BIG}, {BIG}, {cap} - [pp_ap]), '
           f'IIF(IsNull([pp_limit]), {BIG}, [pp_limit]))')
    x = wf.formula(x, [("pp_rec_l", X.DBL, f"IIF({lim} > 0, {xl_amount(cost, '[pp_ap]', lim, '[pp_cop]')}, 0)")],
                   label=f"{label}: PP recovery by layer")
    r = wf.summarize(x, [(idf, "GroupBy", f"pr_{idf}"), ("pp_rec_l", "Sum", "pp_rec")], label=f"{label}: PP recovery")
    j = wf.left_join(src, r, [idf], [f"pr_{idf}"], label=f"{label}: + PP recovery")
    return wf.formula(j, [(f"net_{cost.strip('[]')}", X.DBL, f"{cost} - {nz('pp_rec')}")], label=f"{label}: net of PP")


def poisson_counts(wf, draws, lam_src, key, label):
    """draws: rows with [iter] (+ key fields) and uniform [u_c]; lam_src: rows with the key, [lam], [k_max].
    N = #{k : CDF(k) < U} by a cross join on the key (lambda is small for events)."""
    ks = wf.generate_rows(lam_src, "k", X.INT64, "0", "[k] <= [k_max]", "[k] + 1", label=f"{label}: k = 0 .. k_max")
    ks = wf.sort(ks, [(key, "Ascending"), ("k", "Ascending")], label=f"{label}: order")
    ks = wf.multirow(ks, "pk", X.DBL, "IIF([k] = 0, Exp(-[lam]), [Row-1:pk] * [lam] / [k])", groupby=[key],
                     label=f"{label}: Poisson pmf")
    ks = wf.multirow(ks, "cdf", X.DBL, "[Row-1:cdf] + [pk]", groupby=[key], label=f"{label}: CDF")
    ks = wf.select(ks, keep=[key, "k", "cdf"], rename={key: f"c_{key}"}, keep_unknown=False, label=f"{label}: table")
    x = wf.join(draws, ks, [key], [f"c_{key}"], label=f"{label}: draws x CDF")["J"]
    x = wf.formula(x, [("below", X.INT64, "IIF([cdf] < [u_c], 1, 0)")], label=f"{label}: CDF below U")
    return wf.summarize(x, [("iter", "GroupBy", "n_iter_"), (key, "GroupBy", f"n_{key}"), ("below", "Sum", "n_ev")],
                        label=f"{label}: N per iteration")


def build(v):
    wf = X.WF("05_per_event", "Stage 05: per-event XoL (event grouping, burning cost, scenarios, native simulation)")
    wf.dag = True
    checks, sn = [], "05"

    # ------------------------------------------------------------------ context
    wf.container("05.1 Inputs: parameters, event rule, EV layers, programme PP option, lives, RNG streams")
    par = C.params_stream(wf, v, PARAMS, key="kp", label="parameters")
    meta = C.meta_stream(wf, v, ["has_line_detail"], key="km")
    lv = wf.input_yxdb(C.stage(v, "bc_lives"), label="bc_lives (stage 03)")
    lv = wf.formula(lv, [("klv", X.INT, "1")], label="key")
    rule = wf.input_yxdb(C.stage(v, "ctl_eventrule"), label="ctl_eventrule (stage 00)")
    rule = wf.summarize(wf.formula(rule, [("kr", X.INT, "1")], label="key"),
                        [("kr", "GroupBy", "kr"), ("id_source", "First", "id_source"), ("window_days", "First", "window"),
                         ("group_key", "First", "group_key"), ("min_lives", "First", "min_lives")], label="event rule")
    ctx = C.append(wf, par, meta, "kp", "km", "+ run meta")
    ctx = C.append(wf, ctx, wf.select(lv, keep=["klv", "lives_quote_year"], keep_unknown=False, label="lives"), "kp",
                   "klv", "+ lives")
    ctx = C.append(wf, ctx, rule, "kp", "kr", "+ event rule")
    ctx = wf.formula(ctx, [
        ("ts_md", X.STR, 'DateTimeFormat([p_treaty_start], "%m-%d")'),
        ("src", X.STR, 'IIF(IsNull([p_ev_freq_source]), "BOTH", [p_ev_freq_source])'),
        ("cluster", X.BOOL, '[id_source] = "CLUSTER" AND [m_has_line_detail]'),
        ("s0", X.DBL, f"[p_seed] - {M - 1} * Floor([p_seed] / {M - 1}) + 1")], label="calendar, source, seed state")
    ctx = wf.formula(ctx, [(f"start{j}", X.DBL, B4.jump("[s0]", JUMP[j])) for j in OFFSETS], label="stream starts")
    lay = wf.input_yxdb(C.stage(v, "ctl_layers"), label="ctl_layers (stage 00)")
    ev, _ = wf.filter(lay, '[cover] = "EV" AND [active] = 1', label="active per-event layers")
    ev = wf.formula(ev, [("l_label", X.STR, 'IIF(IsNull([label]), "EV option " + ToString([option_id]), [label])'),
                         ("cop", X.DBL, nz("co_participation_pct")),
                         ("lim", X.DBL, f"IIF(IsNull([limit]), {BIG}, [limit])"),
                         ("lim_basis", X.STR, 'IIF(IsNull([limit_basis]), "AMOUNT", [limit_basis])'),
                         ("ke", X.INT, "1")], label="EV layer fields")
    ev = wf.select(ev, keep=["ke", "option_id", "layer", "l_label", "lim_basis", "ap", "limit", "lim", "aal", "aad",
                             "cop", "reinstatements_n", "reinstatement_cost_pct"], keep_unknown=False, label="EV layers")
    pg = wf.input_yxdb(C.stage(v, "ctl_programme"), label="ctl_programme (stage 00)")
    pg, _ = wf.filter(pg, '[cover] = "PP" AND [active] = 1 AND !IsNull([option_id])', label="programme PP option")
    pg = wf.record_id(wf.sort(pg, [("step", "Ascending")], label="by step"), "pg_rank", label="rank")
    pg, _ = wf.filter(pg, "[pg_rank] = 1", label="first PP step")
    ppl = wf.join(wf.select(pg, keep=["option_id"], rename={"option_id": "g_opt"}, keep_unknown=False, label="PP option"),
                  wf.formula(lay, [("kpp", X.INT, "1")], label="key"), ["g_opt"], ["option_id"],
                  label="programme PP layers")["J"]
    ppl, _ = wf.filter(ppl, '[cover] = "PP" AND [active] = 1', label="active PP layers")
    ppl = wf.formula(ppl, [("pp_basis", X.STR, 'IIF(IsNull([limit_basis]), "AMOUNT", [limit_basis])'),
                           ("pp_cop", X.DBL, nz("co_participation_pct"))], label="PP terms")
    ppl = wf.select(ppl, keep=["kpp", "g_opt", "layer", "pp_basis", "ap", "limit", "pp_cop"],
                    rename={"ap": "pp_ap", "limit": "pp_limit", "layer": "pp_layer"}, keep_unknown=False,
                    label="PP layer terms")

    # ------------------------------------------------------------------ events
    wf.container("05.2 Events: supplied ids, clustering of accident-coded lines, lives and per-life cost")
    cl = wf.input_yxdb(C.stage(v, "int_claims"), label="int_claims (stage 01)")
    cl = C.append(wf, wf.formula(cl, [("kc", X.INT, "1")], label="key"), ctx, "kc", "kp", "+ context")
    cl = wf.formula(cl, [("amt", X.DBL, f"({nz('amount_paid_q')} + {nz('amount_os_q')}) * [p_qs_cedant_retention_pct]"),
                         ("ev_date", X.DATE, "IIF(IsNull([admission_date]), [treatment_date], [admission_date])"),
                         ("ev_day", X.INT64, X.ddiff("[ev_date]", 'ToDate("2000-01-01")'))],
                    label="amount, event date")
    sup, rest = wf.filter(cl, "!IsNull([event_id]) AND Trim([event_id]) != \"\"", label="supplied event id")
    sup = wf.formula(sup, [("ev_key", X.STR, '"ID " + Uppercase(Trim([event_id]))'), ("ev_src", X.STR, '"SUPPLIED"')],
                     label="event key (supplied)")
    cand, _ = wf.filter(rest, '[cluster] AND !IsNull([cause_code]) AND Trim([cause_code]) != ""',
                        label="accident-coded lines (CLUSTER)")
    q = lambda f: f'IIF(IsNull([{f}]), "?", [{f}])'  # noqa: E731
    cand = wf.formula(cand, [("ckey", X.STR,
                              f'IIF([group_key] = "SCHEME", {q("scheme_id")}, IIF([group_key] = "SITE", {q("site")}, '
                              f'IIF([group_key] = "SCHEME+SITE", {q("scheme_id")} + "|" + {q("site")}, '
                              f'IIF([group_key] = "SCHEME+CAUSE", {q("scheme_id")}, "*")))) + "|" + '
                              'Uppercase(Trim([cause_code]))')], label="cluster key (group key + cause)")
    cand = wf.sort(cand, [("ckey", "Ascending"), ("ev_day", "Ascending"), ("claim_key", "Ascending")],
                   label="by key and date")
    cand = wf.multirow(cand, "c_day", X.INT64, "IIF([Row-1:c_day] = 0 OR [ev_day] - [Row-1:c_day] > [window], "
                                               "[ev_day], [Row-1:c_day])", groupby=["ckey"],
                       label="cluster start (window from the first date)")
    cand = wf.formula(cand, [("ev_key", X.STR, '"CL " + [ckey] + "|" + ToString([c_day])'),
                             ("ev_src", X.STR, '"CLUSTER"')], label="event key (cluster)")
    EVF = ["ev_key", "ev_src", "member_id", "scheme_id", "site", "cause_code", "ev_date", "amt", "annual_limit", "grain"]
    el = wf.union([wf.select(sup, keep=EVF, keep_unknown=False, label="supplied lines"),
                   wf.select(cand, keep=EVF, keep_unknown=False, label="clustered lines")], label="event lines")
    life = wf.summarize(el, [("ev_key", "GroupBy", "ev_key"), ("member_id", "GroupBy", "member_id"),
                             ("amt", "Sum", "life_amt"), ("annual_limit", "Max", "annual_limit"),
                             ("ev_date", "Min", "life_date")], label="per life in event")
    evs = wf.summarize(el, [("ev_key", "GroupBy", "e_key"), ("ev_src", "First", "ev_src"),
                            ("ev_date", "Min", "event_date"), ("member_id", "CountDistinct", "n_lives"),
                            ("scheme_id", "First", "scheme_id"), ("site", "First", "site"),
                            ("cause_code", "First", "cause_code"), ("grain", "First", "grain"),
                            ("amt", "Sum", "loss_raw")], label="per event")
    evs = C.append(wf, wf.formula(evs, [("kx", X.INT, "1")], label="key"), ctx, "kx", "kp", "+ context")
    evs = wf.formula(evs, [("ty", X.INT64, C.ty_of("[event_date]")),
                           ("in_years", X.BOOL, C.in_csv("[p_years_included]", "ToString(" + C.ty_of("[event_date]") + ")"))],
                     label="event treaty year")
    evs, _ = wf.filter(evs, "[in_years] AND [n_lives] >= [min_lives]", label="qualifying events (years, min lives)")
    trend = B3.trend_indices(wf, v)
    evs = wf.join(evs, wf.select(trend, keep=["tr_year", "trend_f"], keep_unknown=False, label="trend"), ["ty"],
                  ["tr_year"], label="+ large-claim trend")["J"]
    lf = wf.join(life, wf.select(evs, keep=["e_key", "ty", "trend_f", "p_policy_limit"], keep_unknown=False,
                                 label="event keys"), ["ev_key"], ["e_key"], label="lives of qualifying events")["J"]
    cap = (f"IIF(IsNull([annual_limit]) AND IsNull([p_policy_limit]), {BIG}, IIF(IsNull([annual_limit]), "
           "[p_policy_limit], IIF(IsNull([p_policy_limit]), [annual_limit], IIF([annual_limit] < [p_policy_limit], "
           "[annual_limit], [p_policy_limit]))))")
    lf = wf.formula(lf, [("cap", X.DBL, cap), ("adj_unc", X.DBL, "[life_amt] * [trend_f]"),
                         ("adj", X.DBL, "IIF([adj_unc] < [cap], [adj_unc], [cap])")], label="per-life cost to Q, capped")
    lf = wf.sort(lf, [("ev_key", "Ascending"), ("member_id", "Ascending")], label="fixed order")
    lf = wf.record_id(lf, "life_rank", label="life rank")
    lf = pp_net(wf, lf, "[adj]", "[cap]", ppl, "life_rank", "lives")
    eloss = wf.summarize(lf, [("ev_key", "GroupBy", "l_key"), ("adj", "Sum", "loss_adj"),
                              ("net_adj", "Sum", "loss_net_pp")], label="event loss (adjusted, net of PP)")
    evs = wf.join(evs, eloss, ["e_key"], ["l_key"], label="+ event loss")["J"]
    evs = wf.sort(evs, [("e_key", "Ascending")], label="fixed order")
    evs = wf.record_id(evs, "ev_rank", label="event rank")
    evo = wf.select(evs, keep=["e_key", "ev_src", "ty", "event_date", "n_lives", "scheme_id", "site", "cause_code",
                               "loss_raw", "trend_f", "loss_adj", "loss_net_pp"], rename={"e_key": "event_key"},
                    keep_unknown=False, label="out_ev_events fields")
    C.result(wf, evo, v, "out_ev_events")

    # ------------------------------------------------------------------ deterministic
    wf.container("05.3 Deterministic: event layer losses by treaty year, burning cost, scenarios")
    ex = wf.input_yxdb(C.stage(v, "exp_exposure_ty"), label="exp_exposure_ty (stage 02)")
    wts = wf.input_yxdb(C.stage(v, "bc_weights"), label="bc_weights (stage 03)")
    yrs = wf.join(wts, wf.select(ex, keep=["ty", "exposure_ly_full"], keep_unknown=False, label="exposure"), ["g_ty"],
                  ["ty"], label="weights + exposure")["J"]
    yrs = wf.formula(yrs, [("ky", X.INT, "1")], label="key")
    ex_ = C.append(wf, wf.formula(evs, [("kv", X.INT, "1")], label="key"), ev, "kv", "ke", "events x EV layers")
    ex_ = wf.formula(ex_, [("xl", X.DBL, xl_amount("[loss_adj]")), ("xl_net", X.DBL, xl_amount("[loss_net_pp]")),
                           ("n_xl", X.DBL, f"IIF({xl_amount('[loss_adj]')} > 0, 1, 0)"), ("n_e", X.DBL, "1")],
                     label="event layer loss")
    grid = C.append(wf, yrs, ev, "ky", "ke", "years x EV layers")
    grid = wf.formula(grid, [("xl", X.DBL, "0"), ("xl_net", X.DBL, "0"), ("n_xl", X.DBL, "0"), ("n_e", X.DBL, "0"),
                             ("ty", X.INT64, "[g_ty]")], label="zero rows")
    F_ = ["option_id", "layer", "ty", "xl", "xl_net", "n_xl", "n_e"]
    by = wf.summarize(wf.union([wf.select(ex_, keep=F_, keep_unknown=False, label="event amounts"),
                                wf.select(grid, keep=F_, keep_unknown=False, label="zero grid")], label="+ zero grid"),
                      [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"), ("ty", "GroupBy", "ty"),
                       ("xl", "Sum", "xl"), ("xl_net", "Sum", "xl_net"), ("n_xl", "Sum", "n_xl"),
                       ("n_e", "Sum", "n_events")], label="layer x treaty year")
    by = wf.join(by, wf.select(ev, keep=["option_id", "layer", "l_label", "limit", "aal", "aad", "reinstatements_n",
                                         "reinstatement_cost_pct"], rename={"option_id": "t_opt", "layer": "t_lay"},
                               keep_unknown=False, label="terms"), ["option_id", "layer"], ["t_opt", "t_lay"],
                 label="+ layer terms")["J"]
    by = wf.join(by, wf.select(yrs, keep=["g_ty", "weight", "exposure_ly_full"], keep_unknown=False, label="years"),
                 ["ty"], ["g_ty"], label="+ weight, exposure")["J"]
    by = annual_terms(wf, by, "annual AAD / AAL")
    by = wf.formula(by, [("bc_pppy", X.DBL, "[recovery] / [exposure_ly_full]"),
                         ("events_per_1000", X.DBL, "[n_events] / [exposure_ly_full] * 1000"),
                         ("w_bc", X.DBL, "[weight] * [bc_pppy]"),
                         ("w_freq", X.DBL, "[weight] * [n_events] / [exposure_ly_full]")], label="BC per life")
    byo = wf.select(by, keep=["option_id", "layer", "l_label", "ty", "exposure_ly_full", "n_events", "events_per_1000",
                              "n_xl", "xl", "recovery", "xl_net", "recovery_net", "bc_pppy", "weight"],
                    keep_unknown=False, label="out_ev_bc_by_year fields")
    C.result(wf, wf.sort(byo, [("option_id", "Ascending"), ("layer", "Ascending"), ("ty", "Ascending")],
                         label="order"), v, "out_ev_bc_by_year")
    sm = wf.summarize(by, [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"),
                           ("l_label", "First", "l_label"), ("w_bc", "Sum", "bc_pppy_weighted"),
                           ("w_freq", "Sum", "freq_weighted"), ("n_xl", "Sum", "n_xl_events"),
                           ("n_events", "Sum", "n_events")], label="weighted by layer")
    sm = C.append(wf, wf.formula(sm, [("ks", X.INT, "1")], label="key"), ctx, "ks", "kp", "+ context")
    sm = wf.formula(sm, [
        ("expected_cost_det", X.DBL, "[bc_pppy_weighted] * [lives_quote_year] * [p_incidence_adj]"),
        ("credibility_z", X.DBL, "IIF([n_xl_events] >= [p_full_cred_n], 1, Sqrt([n_xl_events] / [p_full_cred_n]))"),
        ("lambda_exp", X.DBL, "[freq_weighted] * [lives_quote_year] * [p_incidence_adj]")], label="det. cost, Z")
    smo = wf.select(sm, keep=["option_id", "layer", "l_label", "n_events", "n_xl_events", "bc_pppy_weighted",
                              "lives_quote_year", "expected_cost_det", "credibility_z", "lambda_exp"],
                    keep_unknown=False, label="out_ev_bc_summary fields")
    C.result(wf, wf.sort(smo, [("option_id", "Ascending"), ("layer", "Ascending")], label="order"), v,
             "out_ev_bc_summary")

    sc = wf.input_yxdb(C.stage(v, "ctl_evscenarios"), label="ctl_evscenarios (stage 00)")
    sc = wf.record_id(sc, "scen_rank", label="scenario rank")
    sc = C.append(wf, wf.formula(sc, [("kq", X.INT, "1")], label="key"), ctx, "kq", "kp", "+ context")
    sc = wf.formula(sc, [("loss", X.DBL, "[lives] * [cost_per_life]"),
                         ("rp", X.DBL, "IIF(IsNull([return_period_yrs]) OR [return_period_yrs] <= 0, Null(), "
                                       "[return_period_yrs])"), ("cost_cap", X.DBL, BIG)], label="scenario loss")
    sc = pp_net(wf, sc, "[cost_per_life]", "[cost_cap]", ppl, "scen_rank", "scenarios")
    sc = wf.formula(sc, [("loss_net_pp", X.DBL, "[lives] * [net_cost_per_life]")], label="scenario loss net of PP")
    sx = C.append(wf, sc, ev, "kq", "ke", "scenarios x EV layers")
    sx = wf.formula(sx, [("layer_loss", X.DBL, xl_amount("[loss]")),
                         ("layer_loss_net_pp", X.DBL, xl_amount("[loss_net_pp]"))], label="scenario layer loss")
    sxo = wf.select(sx, keep=["scenario_name", "lives", "cost_per_life", "return_period_yrs", "loss", "loss_net_pp",
                              "option_id", "layer", "l_label", "layer_loss", "layer_loss_net_pp"], keep_unknown=False,
                    label="out_ev_scenarios fields")
    C.result(wf, wf.sort(sxo, [("option_id", "Ascending"), ("layer", "Ascending"), ("scenario_name", "Ascending")],
                         label="order"), v, "out_ev_scenarios")

    # ------------------------------------------------------------------ simulation: counts
    wf.container("05.4 Simulation: experience and scenario event counts (Poisson by inverse CDF)")
    nst = wf.summarize(wf.formula(evs, [("kn", X.INT, "1")], label="key"),
                       [("kn", "GroupBy", "kn"), ("ev_rank", "Max", "n_hist")], label="historical events")
    npl = wf.summarize(wf.formula(lf, [("kn", X.INT, "1")], label="key"),
                       [("kn", "GroupBy", "kn2"), ("life_rank", "Max", "n_pool")], label="historical lives")
    # experience frequency does not depend on the layer: from the yearly event counts
    fr = wf.summarize(wf.formula(evs, [("one", X.DBL, "1")], label="count"), [("ty", "GroupBy", "f_ty"),
                                                                             ("one", "Sum", "n_ty")],
                      label="events by TY")
    fr = wf.left_join(yrs, fr, ["g_ty"], ["f_ty"], label="years + events")
    fr = wf.formula(fr, [("w_rate", X.DBL, f"[weight] * {nz('n_ty')} / [exposure_ly_full]")], label="weighted rate")
    fr = wf.summarize(fr, [("ky", "GroupBy", "kf"), ("w_rate", "Sum", "rate_w")], label="weighted event rate")
    sim = C.append(wf, ctx, fr, "kp", "kf", "+ event rate")
    nst0 = wf.summarize(wf.union([nst, wf.text_input(["kn", "n_hist"], [[1, 0]], label="zero")], label="+ zero"),
                        [("kn", "GroupBy", "kz"), ("n_hist", "Max", "n_hist")], label="events (0 if none)")
    sim = C.append(wf, wf.formula(sim, [("kn", X.INT, "1")], label="key"), nst0, "kn", "kz", "+ historical events")
    sim = C.append(wf, sim, wf.summarize(wf.union([npl, wf.text_input(["kn2", "n_pool"], [[1, 0]], label="zero")],
                                                  label="+ zero"), [("kn2", "GroupBy", "kz2"), ("n_pool", "Max", "n_pool")],
                                         label="lives (0 if none)"), "kn", "kz2", "+ historical lives")
    sim = wf.formula(sim, [
        ("lam", X.DBL, 'IIF(([src] = "EXPERIENCE" OR [src] = "BOTH") AND [n_hist] > 0, '
                       '[rate_w] * [lives_quote_year] * [p_incidence_adj], 0)'),
        ("k_max", X.INT64, "Ceil([lam] + 10 * Sqrt([lam]) + 20)"), ("src_key", X.INT64, "0")],
        label="experience lambda")
    its = wf.generate_rows(sim, "iter", X.INT64, "1", "[iter] <= [p_iterations]", "[iter] + 1", label="iterations")
    its = B4.stream(wf, its, "s6", "start6", "experience count uniforms")
    d6 = wf.formula(its, [("u_c", X.DBL, "[u_s6]")], label="uniform")
    n_exp = poisson_counts(wf, wf.select(d6, keep=["iter", "src_key", "u_c"], keep_unknown=False, label="draws"),
                           wf.select(sim, keep=["src_key", "lam", "k_max"], keep_unknown=False, label="lambda"),
                           "src_key", "experience")
    sch = wf.filter(sc, '!IsNull([rp]) AND ([src] = "SCENARIOS" OR [src] = "BOTH")', label="simulated scenarios")[0]
    sch = wf.formula(sch, [("lam", X.DBL, "1 / [rp]"), ("k_max", X.INT64, "Ceil([lam] + 10 * Sqrt([lam]) + 20)"),
                           ("src_key", X.INT64, "[scen_rank]"), ("kq2", X.INT, "1")], label="scenario lambda")
    ix = wf.join(wf.formula(wf.select(its, keep=["iter"], keep_unknown=False, label="iter"), [("kq2", X.INT, "1")],
                            label="key"),
                 wf.select(sch, keep=["kq2", "src_key"], rename={"kq2": "kq2r"}, keep_unknown=False,
                           label="scenario keys"), ["kq2"], ["kq2r"],
                 label="iterations x scenarios")["J"]
    ix = wf.sort(ix, [("iter", "Ascending"), ("src_key", "Ascending")], label="iteration-major order")
    ix = C.append(wf, wf.formula(ix, [("kq3", X.INT, "1")], label="key"),
                  wf.select(ctx, keep=["kp", "start9"], keep_unknown=False, label="stream 9"), "kq3", "kp", "+ stream")
    ix = B4.stream(wf, ix, "s9", "start9", "scenario count uniforms")
    ix = wf.formula(ix, [("u_c", X.DBL, "[u_s9]")], label="uniform")
    n_sc = poisson_counts(wf, wf.select(ix, keep=["iter", "src_key", "u_c"], keep_unknown=False, label="draws"),
                          wf.select(sch, keep=["src_key", "lam", "k_max"], keep_unknown=False, label="lambda"),
                          "src_key", "scenario")

    # ------------------------------------------------------------------ simulation: events
    wf.container("05.5 Simulation: experience events (lives and per-life costs resampled), scenario events")
    ne, _ = wf.filter(n_exp, "[n_ev] > 0", label="iterations with experience events")
    ne = C.append(wf, wf.formula(ne, [("kj", X.INT, "1")], label="key"),
                  wf.select(sim, keep=["kp", "n_hist", "n_pool", "start7", "start8"], keep_unknown=False,
                            label="constants"), "kj", "kp", "+ constants")
    ee = wf.generate_rows(ne, "j", X.INT64, "1", "[j] <= [n_ev]", "[j] + 1", label="one row per event")
    ee = wf.sort(ee, [("n_iter_", "Ascending"), ("j", "Ascending")], label="event order")
    ee = B4.stream(wf, ee, "s7", "start7", "lives uniforms")
    ee = wf.formula(ee, [("h_rank", X.INT64, "Floor([u_s7] * [n_hist]) + 1")], label="historical event index")
    ee = wf.join(ee, wf.select(evs, keep=["ev_rank", "n_lives"], keep_unknown=False, label="event lives"), ["h_rank"],
                 ["ev_rank"], label="+ lives")["J"]
    ll = wf.generate_rows(ee, "i", X.INT64, "1", "[i] <= [n_lives]", "[i] + 1", label="one row per life")
    ll = wf.sort(ll, [("n_iter_", "Ascending"), ("j", "Ascending"), ("i", "Ascending")], label="life order")
    ll = B4.stream(wf, ll, "s8", "start8", "per-life cost uniforms")
    ll = wf.formula(ll, [("c_rank", X.INT64, "Floor([u_s8] * [n_pool]) + 1")], label="historical life index")
    ll = wf.join(ll, wf.select(lf, keep=["life_rank", "adj", "net_adj"], keep_unknown=False, label="life costs"),
                 ["c_rank"], ["life_rank"], label="+ per-life cost")["J"]
    sev = wf.summarize(ll, [("n_iter_", "GroupBy", "iter"), ("j", "GroupBy", "ev_no"), ("adj", "Sum", "loss"),
                            ("net_adj", "Sum", "loss_net"), ("i", "Count", "lives")], label="experience event loss")
    sev = wf.formula(sev, [("source", X.STR, '"EXPERIENCE"')], label="source")
    ns, _ = wf.filter(n_sc, "[n_ev] > 0", label="iterations with scenario events")
    ns = wf.join(ns, wf.select(sch, keep=["src_key", "lives", "loss", "loss_net_pp", "scenario_name"],
                               rename={"src_key": "s_key"}, keep_unknown=False, label="scenario sizes"),
                 ["n_src_key"], ["s_key"], label="+ scenario size")["J"]
    ss = wf.generate_rows(ns, "j", X.INT64, "1", "[j] <= [n_ev]", "[j] + 1", label="one row per occurrence")
    ss = wf.formula(ss, [("iter", X.INT64, "[n_iter_]"), ("ev_no", X.INT64, "[n_src_key] * 1000 + [j]"),
                         ("loss_net", X.DBL, "[loss_net_pp]"), ("source", X.STR, '"SCENARIO " + [scenario_name]')],
                    label="scenario event")
    EF = ["iter", "ev_no", "source", "lives", "loss", "loss_net"]
    allev = wf.union([wf.select(sev, keep=EF, keep_unknown=False, label="experience"),
                      wf.select(ss, keep=EF, keep_unknown=False, label="scenario")], label="simulated events")

    # ------------------------------------------------------------------ simulation: layers
    wf.container("05.6 Simulation: EV layers per event, annual AAD / AAL / reinstatements, summary and quantiles")
    lx = C.append(wf, wf.formula(allev, [("ka", X.INT, "1")], label="key"), ev, "ka", "ke", "events x EV layers")
    lx = wf.formula(lx, [("xl", X.DBL, xl_amount("[loss]")), ("xl_net", X.DBL, xl_amount("[loss_net]")),
                         ("n_e", X.INT64, "1")], label="event layer loss")
    gz = C.append(wf, wf.formula(wf.select(its, keep=["iter"], keep_unknown=False, label="iterations"),
                                 [("kg", X.INT, "1"), ("xl", X.DBL, "0"), ("xl_net", X.DBL, "0"), ("n_e", X.INT64, "0")],
                                 label="zero rows"), ev, "kg", "ke", "iterations x EV layers")
    IF = ["option_id", "layer", "iter", "xl", "xl_net", "n_e"]
    it = wf.summarize(wf.union([wf.select(lx, keep=IF, keep_unknown=False, label="event amounts"),
                                wf.select(gz, keep=IF, keep_unknown=False, label="zero grid")], label="+ zero grid"),
                      [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"), ("iter", "GroupBy", "iter"),
                       ("xl", "Sum", "xl"), ("xl_net", "Sum", "xl_net"), ("n_e", "Sum", "n_events")],
                      label="by iteration")
    it = wf.join(it, wf.select(ev, keep=["option_id", "layer", "l_label", "limit", "aal", "aad", "reinstatements_n",
                                         "reinstatement_cost_pct"], rename={"option_id": "t_opt", "layer": "t_lay"},
                               keep_unknown=False, label="terms"), ["option_id", "layer"], ["t_opt", "t_lay"],
                 label="+ layer terms")["J"]
    it = annual_terms(wf, it, "annual AAD / AAL / reinstatements")
    ito = wf.select(it, keep=["iter", "option_id", "layer", "l_label", "n_events", "recovery", "recovery_net",
                              "reinst_usage", "reinst_usage_net"], keep_unknown=False, label="iteration fields")
    ito = wf.sort(ito, [("option_id", "Ascending"), ("layer", "Ascending"), ("iter", "Ascending")], label="order")
    wf.output_csv(ito, v.p(f"{v.out_dir}/results/out_ev_iterations.csv"), label="out_ev_iterations.csv")
    wf.output_yxdb(ito, C.stage(v, "sim_ev_iterations"), label="sim_ev_iterations")

    srt = wf.sort(ito, [("option_id", "Ascending"), ("layer", "Ascending"), ("recovery", "Ascending"),
                        ("iter", "Ascending")], label="by recovery")
    srt = wf.multirow(srt, "rk", X.INT64, "IIF([Row-1:option_id] = [option_id] AND [Row-1:layer] = [layer], "
                                          "[Row-1:rk] + 1, 1)", label="rank within layer")
    qs = wf.text_input(["q"], [[f"{q:.3f}"] for q in sorted(QUANTILES)], label="quantile grid")
    qs = C.append(wf, wf.formula(qs, [("kq4", X.INT, "1")], label="key"),
                  wf.select(ctx, keep=["kp", "p_iterations"], keep_unknown=False, label="n"), "kq4", "kp", "+ n")
    qs = wf.formula(qs, [("q_rank", X.INT64, "IIF(Ceil([q] * [p_iterations]) < 1, 1, Ceil([q] * [p_iterations]))")],
                    label="nearest rank")
    qj = wf.join(srt, wf.select(qs, keep=["q", "q_rank"], keep_unknown=False, label="ranks"), ["rk"], ["q_rank"],
                 label="quantile rows")["J"]
    qo = wf.select(qj, keep=["option_id", "layer", "l_label", "q", "recovery"], keep_unknown=False, label="quantiles")
    C.result(wf, wf.sort(qo, [("option_id", "Ascending"), ("layer", "Ascending"), ("q", "Ascending")], label="order"),
             v, "out_ev_sim_quantiles")
    st = wf.formula(ito, [("zero", X.DBL, "IIF([recovery] = 0, 1, 0)")], label="zero flag")
    st = wf.summarize(st, [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"),
                           ("l_label", "First", "l_label"), ("recovery", "Sum", "s_r"),
                           ("recovery_net", "Sum", "s_rn"), ("reinst_usage", "Sum", "s_ru"),
                           ("zero", "Sum", "n_zero"), ("n_events", "Sum", "s_ev"), ("iter", "Count", "n_iter"),
                           ("recovery", "Max", "max_recovery")], label="sums")
    st = wf.formula(st, [("mean_recovery", X.DBL, "[s_r] / [n_iter]"), ("mean_recovery_net_pp", X.DBL, "[s_rn] / [n_iter]"),
                         ("mean_reinst_usage", X.DBL, "[s_ru] / [n_iter]"), ("p_zero", X.DBL, "[n_zero] / [n_iter]"),
                         ("mean_events", X.DBL, "[s_ev] / [n_iter]")], label="means")
    dv = wf.join(ito, wf.select(st, keep=["option_id", "layer", "mean_recovery"],
                                rename={"option_id": "m_opt", "layer": "m_lay", "mean_recovery": "m_r"},
                                keep_unknown=False, label="mean"), ["option_id", "layer"], ["m_opt", "m_lay"],
                 label="+ mean")["J"]
    dv = wf.summarize(wf.formula(dv, [("d2", X.DBL, "([recovery] - [m_r]) * ([recovery] - [m_r])")], label="sq. dev."),
                      [("option_id", "GroupBy", "d_opt"), ("layer", "GroupBy", "d_lay"), ("d2", "Sum", "ss")],
                      label="deviation sums")
    st = wf.join(st, dv, ["option_id", "layer"], ["d_opt", "d_lay"], label="+ deviations")["J"]
    for qv, nm in ((0.5, "p50"), (0.9, "p90"), (0.95, "p95"), (0.99, "p99"), (0.995, "p995")):
        qq, _ = wf.filter(qo, f"[q] = {qv}", label=f"{nm} rows")
        qq = wf.select(qq, keep=["option_id", "layer", "recovery"],
                       rename={"option_id": f"{nm}_opt", "layer": f"{nm}_lay", "recovery": nm}, keep_unknown=False,
                       label=nm)
        st = wf.join(st, qq, ["option_id", "layer"], [f"{nm}_opt", f"{nm}_lay"], label=f"+ {nm}")["J"]
    st = wf.join(st, wf.select(sm, keep=["option_id", "layer", "expected_cost_det", "credibility_z", "lambda_exp",
                                         "lives_quote_year", "n_events"],
                               rename={"option_id": "b_opt", "layer": "b_lay", "n_events": "n_hist_events"},
                               keep_unknown=False, label="deterministic"), ["option_id", "layer"], ["b_opt", "b_lay"],
                 label="+ deterministic")["J"]
    st = wf.formula(st, [
        ("sd_recovery", X.DBL, "Sqrt([ss] / [n_iter])"),
        ("expected_loss_blended", X.DBL, "[credibility_z] * [expected_cost_det] + (1 - [credibility_z]) * "
                                         "[mean_recovery]"),
        ("expected_loss_pppy", X.DBL, "[expected_loss_blended] / [lives_quote_year]")], label="SD, credibility blend")
    sto = wf.select(st, keep=["option_id", "layer", "l_label", "n_iter", "lambda_exp", "mean_events", "n_hist_events",
                              "mean_recovery", "sd_recovery", "p_zero", "p50", "p90", "p95", "p99", "p995",
                              "max_recovery", "mean_reinst_usage", "mean_recovery_net_pp", "expected_cost_det",
                              "credibility_z", "expected_loss_blended", "expected_loss_pppy", "lives_quote_year"],
                    rename={"n_iter": "iterations"}, keep_unknown=False, label="out_ev_sim_summary fields")
    C.result(wf, wf.sort(sto, [("option_id", "Ascending"), ("layer", "Ascending")], label="order"), v,
             "out_ev_sim_summary")

    # ------------------------------------------------------------------ checks
    wf.container("05.7 Checks and recon")
    nh = C.append(wf, ctx, wf.select(sim, keep=["kp", "n_hist", "lam"], rename={"kp": "kh"}, keep_unknown=False,
                                     label="events"), "kp", "kh", "+ events")
    v1, _ = wf.filter(nh, "[n_hist] = 0", label="V01 no events")
    checks.append(C.check_formula(wf, v1, sn, "V01", "INFO", '"events"',
                                  '"no historical event qualifies: per-event frequency from scenarios only"'))
    v2, _ = wf.filter(nh, '[n_hist] = 0 AND [src] = "EXPERIENCE"', label="V02 nothing to simulate")
    checks.append(C.check_formula(wf, v2, sn, "V02", "FLAG", '"ev_freq_source"',
                                  '"EXPERIENCE chosen but no historical event: simulated EV recoveries are 0"'))
    v3, _ = wf.filter(evs, '[grain] = "CLAIMANT"', label="V03 claimant grain")
    checks.append(C.check_formula(wf, v3, sn, "V03", "FLAG", "[e_key]",
                                  '"claimant-listing event: per-life cost is the whole period total (overstated)"'))
    v4, _ = wf.filter(ctx, '[p_treaty_basis] = "RA"', label="V04 RA")
    checks.append(C.check_formula(wf, v4, sn, "V04", "INFO", '"treaty_basis"',
                                  '"risks attaching: events are allocated to the treaty year of the event date"'))
    v5, _ = wf.filter(sc, "[lives] < [min_lives]", label="V05 scenario lives")
    checks.append(C.check_formula(wf, v5, sn, "V05", "FLAG", "[scenario_name]",
                                  '"scenario has fewer lives than min_lives"'))
    v6, _ = wf.filter(sm, "[credibility_z] < 1", label="V06 credibility")
    checks.append(C.check_formula(wf, v6, sn, "V06", "FLAG", "[l_label]",
                                  '"credibility " + ToString([credibility_z], 2) + " (" + ToString([n_xl_events], 0) + '
                                  '" events in the layer): the stochastic result carries the weight"'))
    v7, _ = wf.filter(ev, '[lim_basis] = "TO_BENEFIT_LIMIT"', label="V07 basis")
    checks.append(C.check_formula(wf, v7, sn, "V07", "STOP", "[l_label]",
                                  '"per-event layers need an AMOUNT limit (TO_BENEFIT_LIMIT is per person)"'))
    C.recon_rows(wf, [(evs, "events", "rows", None), (evs, "events", "loss_adj", "loss_adj"),
                      (lf, "event_lives", "rows", None), (allev, "simulated_events", "rows", None),
                      (allev, "simulated_events", "loss", "loss"), (ito, "ev_iterations", "recovery", "recovery")],
                 v, sn)
    C.finish_checks(wf, checks, v, sn)
    return C.save(wf, v, "05_per_event.yxmd")


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build(v))
