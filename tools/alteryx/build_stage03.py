"""Stage 03 - per-person XoL burning cost, deterministic (scope §5 stages 05-07; methodology: docs/methodology.md).

Reads exp_claimants, exp_exposure_ty (stage 02) and the Control tables (stage 00). Best-estimate scenario only.
Per accumulation unit (claimant x treaty year):
    adj = min( amount x trend(ty -> quote year) x PRE_AP completion(ty) ,  annual benefit limit ,  policy limit )
Per unit x active PP layer:
    xl  = max(0, min(L, adj - AP)) x (1 - co-participation)      L = limit, or benefit limit - AP (TO_BENEFIT_LIMIT)
    xl_c = xl x POST_AP_AMOUNT(ty);  n_c = 1[xl > 0] x POST_AP_COUNT(ty);  outlier units go to a separate block
Per layer x treaty year: AAD/AAL on the annual total, exposure projected to the full year (stage 02), BC per life,
BC % of trended premium. Weighted BC (non-outlier, weights) + equally weighted outlier BC; credibility
Z = min(1, sqrt(n / full_cred_n)); sensitivities; leveraged trend; expected cost for the quote year.

Writes results/out_bc_by_year.csv, out_bc_summary.csv, out_large_claims.csv, out_annualisation.csv,
stage bc_units (unit x layer detail for later stages), checks_03.csv, recon_03.csv.
"""
from __future__ import annotations

import xol_common as C
from xol_common import X

BIG = "1000000000000000"
PARAMS = ["quote_year", "policy_limit", "output_threshold_pct_min_ap", "outlier_method", "outlier_value",
          "iqr_multiplier", "full_cred_n", "incidence_adj", "epi_quote_year", "expected_lives_quote_year",
          "years_included"]
DEFAULT_WEIGHTS = {5: [0.10, 0.15, 0.20, 0.30, 0.25], 4: [0.15, 0.20, 0.35, 0.30], 3: [0.25, 0.40, 0.35],
                   2: [0.50, 0.50], 1: [1.0]}
COMP = [("IBNR", "PRE_AP"), ("UNEARNED", "PRE_AP"), ("UNWRITTEN", "PRE_AP"),
        ("IBNR", "POST_AP_AMOUNT"), ("UNEARNED", "POST_AP_AMOUNT"), ("UNWRITTEN", "POST_AP_AMOUNT"),
        ("IBNR", "POST_AP_COUNT"), ("UNEARNED", "POST_AP_COUNT"), ("UNWRITTEN", "POST_AP_COUNT")]


def fname(comp, side):
    return f"f_{comp.lower()}_{side.lower()}"


def results(v, name):
    return v.p(f"{v.out_dir}/results/{name}.csv")


def iif_max0(x):
    return f"IIF({x} > 0, {x}, 0)"


def trend_indices(wf, v, fgu=False):
    """Cumulative claims and premium trend indices by year, and the factors to the quote year.
    fgu=True ignores large_claim_trend_pct (attritional claims, stage 05)."""
    tr = wf.input_yxdb(C.stage(v, "ctl_trend"), label="ctl_trend (stage 00)")
    z = lambda f: f"IIF(IsNull([{f}]), 0, [{f}])"  # noqa: E731
    tr = wf.formula(tr, [
        ("t_comp", X.DBL, f"{z('weight_client')} * {z('client_pct')} + {z('weight_market')} * {z('market_pct')} + "
                          f"{z('weight_inflation')} * {z('inflation_pct')} + {z('residual_pct')}"),
        ("t_claims", X.DBL, "[t_comp]" if fgu else
         "IIF(IsNull([large_claim_trend_pct]), [t_comp], [large_claim_trend_pct])"),
        ("t_prem", X.DBL, z("premium_trend_pct")), ("t_adj", X.DBL, z("one_off_adj"))], label="annual trend")
    tr = wf.sort(tr, [("year", "Ascending")], label="by year")
    tr = wf.multirow(tr, "idx_c", X.DBL, "IIF([Row-1:idx_c] = 0, 1, [Row-1:idx_c] * (1 + [t_claims]) * (1 + [t_adj]))",
                     label="claims trend index")
    tr = wf.multirow(tr, "idx_p", X.DBL, "IIF([Row-1:idx_p] = 0, 1, [Row-1:idx_p] * (1 + [t_prem]))",
                     label="premium trend index")
    p = C.params_stream(wf, v, ["quote_year"], key="kq", label="quote year")
    q = C.append(wf, wf.formula(tr, [("kt", X.INT, "1")], label="key"), p, "kt", "kq", "trend + quote year")
    qrow, _ = wf.filter(q, "[year] = [p_quote_year]", label="quote-year row")
    qrow = wf.select(qrow, keep=["kt", "idx_c", "idx_p", "t_claims", "t_adj"],
                     rename={"kt": "kqq", "idx_c": "q_idx_c", "idx_p": "q_idx_p", "t_claims": "q_t", "t_adj": "q_adj"},
                     keep_unknown=False, label="quote-year index")
    f = C.append(wf, q, qrow, "kt", "kqq", "factors to quote year")
    f = wf.formula(f, [("trend_f", X.DBL, "[q_idx_c] / [idx_c]"), ("prem_trend_f", X.DBL, "[q_idx_p] / [idx_p]"),
                       ("trend_f_less1", X.DBL, "[q_idx_c] / [idx_c] / ((1 + [q_t]) * (1 + [q_adj]))")],
                   label="trend factors")
    return wf.select(f, keep=["year", "trend_f", "prem_trend_f", "trend_f_less1", "q_t"],
                     rename={"year": "tr_year"}, keep_unknown=False, label="trend factors by year")


def completion_factors(wf, v):
    cp = wf.input_yxdb(C.stage(v, "ctl_completion"), label="ctl_completion (stage 00)")
    cp, _ = wf.filter(cp, "IIF(IsNull([ap_band_from]), 1=1, [ap_band_from] = 0)", label="base band (AP bands: phase 2)")
    cp = wf.formula(cp, [(fname(c, s), X.DBL, f'IIF([component] = "{c}" AND [side] = "{s}", [selected_factor], Null())')
                         for c, s in COMP], label="one column per component x side")
    cp = wf.summarize(cp, [("year", "GroupBy", "cf_year")] + [(fname(c, s), "Max", fname(c, s)) for c, s in COMP],
                      label="completion by year")
    one = lambda c, s: f"IIF(IsNull([{fname(c, s)}]), 1, [{fname(c, s)}])"  # noqa: E731
    cp = wf.formula(cp, [(fname(c, s), X.DBL, one(c, s)) for c, s in COMP] + [
        ("pre_f", X.DBL, " * ".join(f"[{fname(c, 'PRE_AP')}]" for c in ("IBNR", "UNEARNED", "UNWRITTEN"))),
        ("post_amt_f", X.DBL, " * ".join(f"[{fname(c, 'POST_AP_AMOUNT')}]" for c in ("IBNR", "UNEARNED", "UNWRITTEN"))),
        ("post_cnt_f", X.DBL, " * ".join(f"[{fname(c, 'POST_AP_COUNT')}]" for c in ("IBNR", "UNEARNED", "UNWRITTEN")))],
        label="products (missing factor = 1)")
    return cp


def build(v):
    wf = X.WF("03_burning_cost", "Stage 03: per-person XoL deterministic burning cost")
    wf.dag = True
    checks, sn = [], "03"

    wf.container("03.1 Assumptions: trend, completion, layers, parameters")
    trend = trend_indices(wf, v)
    comp = completion_factors(wf, v)
    lay = wf.input_yxdb(C.stage(v, "ctl_layers"), label="ctl_layers (stage 00)")
    lay, _ = wf.filter(lay, '[cover] = "PP" AND [active] = 1', label="active per-person layers")
    lay = wf.formula(lay, [("lim_basis", X.STR, 'IIF(IsNull([limit_basis]), "AMOUNT", [limit_basis])'),
                           ("cop", X.DBL, "IIF(IsNull([co_participation_pct]), 0, [co_participation_pct])"),
                           ("l_label", X.STR, 'IIF(IsNull([label]), "PP option " + ToString([option_id]), [label])'),
                           ("aal_eff", X.DBL, "IIF(!IsNull([aal]), [aal], IIF(IsNull([reinstatements_n]) OR "
                                              "IsNull([limit]), Null(), (1 + [reinstatements_n]) * [limit]))"),
                           ("kl", X.INT, "1")], label="layer fields; AAL = (1 + reinstatements) x limit if not given")
    lay = wf.select(lay, keep=["kl", "option_id", "layer", "l_label", "lim_basis", "ap", "limit", "aal_eff", "aad", "cop"],
                    rename={"aal_eff": "aal"}, keep_unknown=False, label="layers")
    minap = wf.summarize(lay, [("kl", "GroupBy", "kmin"), ("ap", "Min", "min_ap")], label="lowest AP")
    par = C.params_stream(wf, v, PARAMS, key="kp", label="parameters")

    # ------------------------------------------------------------------ claimant adjustment
    wf.container("03.2 Claimants: trend, pre-AP completion, limits")
    u = wf.input_yxdb(C.stage(v, "exp_claimants"), label="exp_claimants (stage 02)")
    u = wf.join(u, trend, ["ty"], ["tr_year"], label="+ trend factor")["J"]
    u = wf.left_join(u, comp, ["ty"], ["cf_year"], label="+ completion (left)")
    u = wf.formula(u, [("pre_f_", X.DBL, "IIF(IsNull([pre_f]), 1, [pre_f])"),
                       ("post_amt_f_", X.DBL, "IIF(IsNull([post_amt_f]), 1, [post_amt_f])"),
                       ("post_cnt_f_", X.DBL, "IIF(IsNull([post_cnt_f]), 1, [post_cnt_f])"),
                       ("ku", X.INT, "1")], label="missing completion = 1")
    u = C.append(wf, u, par, "ku", "kp", "+ parameters")
    u = C.append(wf, u, minap, "ku", "kmin", "+ lowest AP")
    cap_expr = ("IIF(IsNull([annual_limit]) AND IsNull([p_policy_limit]), " + BIG + ", "
                "IIF(IsNull([annual_limit]), [p_policy_limit], IIF(IsNull([p_policy_limit]), [annual_limit], "
                "IIF([annual_limit] < [p_policy_limit], [annual_limit], [p_policy_limit]))))")
    u = wf.formula(u, [
        ("trended", X.DBL, "[amount_q] * [trend_f]"),
        ("adj_uncapped", X.DBL, "[trended] * [pre_f_]"),
        ("cap", X.DBL, cap_expr),
        ("adj", X.DBL, "IIF([adj_uncapped] < [cap], [adj_uncapped], [cap])"),
        ("adj_less1_uncapped", X.DBL, "[amount_q] * [trend_f_less1] * [pre_f_]"),
        ("adj_less1", X.DBL, "IIF([adj_less1_uncapped] < [cap], [adj_less1_uncapped], [cap])"),
        ("out_thr", X.DBL, "[p_output_threshold_pct_min_ap] * [min_ap]")], label="adjusted claimant amount")

    # ------------------------------------------------------------------ outlier threshold
    wf.container("03.3 Outlier threshold (value or Q3 + k x IQR, nearest rank)")
    big, _ = wf.filter(u, "[adj] >= [out_thr]", label="claimants above the output threshold")
    srt = wf.record_id(wf.sort(big, [("adj", "Ascending")], label="by adjusted amount"), "rk", label="rank")
    srt = wf.formula(srt, [("kn", X.INT, "1")], label="key")
    nn = wf.summarize(srt, [("kn", "GroupBy", "kn2"), ("rk", "Count", "n_big")], label="count")
    srt = C.append(wf, srt, nn, "kn", "kn2", "+ count")
    rank = lambda p: f"IIF(Floor({p} * [n_big]) = {p} * [n_big], {p} * [n_big], Floor({p} * [n_big]) + 1)"  # noqa: E731
    srt = wf.formula(srt, [("r1", X.INT64, rank("0.25")), ("r3", X.INT64, rank("0.75"))], label="quartile ranks")
    q1, _ = wf.filter(srt, "[rk] = [r1]", label="Q1 row")
    q3, _ = wf.filter(srt, "[rk] = [r3]", label="Q3 row")
    q1 = wf.select(q1, keep=["kn", "adj"], rename={"kn": "k1", "adj": "q1"}, keep_unknown=False, label="Q1")
    q3 = wf.select(q3, keep=["kn", "adj", "p_outlier_method", "p_outlier_value", "p_iqr_multiplier"],
                   rename={"kn": "k3", "adj": "q3"}, keep_unknown=False, label="Q3 + method")
    th = wf.join(q3, q1, ["k3"], ["k1"], label="quartiles")["J"]
    th = wf.formula(th, [("outlier_thr", X.DBL,
                          'IIF([p_outlier_method] = "VALUE", IIF(IsNull([p_outlier_value]), ' + BIG + ', [p_outlier_value]), '
                          'IIF([p_outlier_method] = "IQR", [q3] + IIF(IsNull([p_iqr_multiplier]), 1.5, '
                          '[p_iqr_multiplier]) * ([q3] - [q1]), ' + BIG + '))'), ("kth", X.INT, "1")],
                    label="outlier threshold")
    th = wf.select(th, keep=["kth", "outlier_thr", "q1", "q3"], keep_unknown=False, label="threshold")
    # no claimant above the output threshold: no outliers
    th0 = wf.text_input(["kth", "outlier_thr"], [[1, BIG]], label="fallback threshold")
    tha = wf.summarize(wf.union([th, th0], label="threshold + fallback", differ="Ignore"),
                       [("kth", "GroupBy", "kth"), ("outlier_thr", "Min", "outlier_thr"), ("q1", "Max", "q1"),
                        ("q3", "Max", "q3")], label="threshold (fallback if none)")
    u = C.append(wf, u, tha, "ku", "kth", "+ outlier threshold")
    u = wf.formula(u, [("outlier", X.INT, "IIF([adj] > [outlier_thr], 1, 0)")], label="outlier flag")
    adjo = wf.select(u, keep=["acc_key", "ty", "amount_q", "adj", "adj_uncapped", "cap", "annual_limit", "pre_f_",
                              "post_cnt_f_", "post_amt_f_", "outlier"], keep_unknown=False, label="bc_adjusted fields")
    wf.output_yxdb(adjo, C.stage(v, "bc_adjusted"), label="bc_adjusted")
    lc, _ = wf.filter(u, "[adj] >= [out_thr]", label="large-claim listing")
    lc = wf.select(lc, keep=["acc_key", "member_id", "ty", "segment", "scheme_id", "first_treatment", "amount_q",
                             "trend_f", "trended", "pre_f_", "adj_uncapped", "cap", "adj", "outlier", "outlier_thr"],
                   rename={"pre_f_": "pre_ap_f"}, keep_unknown=False, label="out_large_claims fields")
    wf.output_csv(wf.sort(lc, [("ty", "Ascending"), ("adj", "Descending")], label="listing order"),
                  results(v, "out_large_claims"), label="out_large_claims.csv")

    # ------------------------------------------------------------------ layering
    wf.container("03.4 Layering: unit x layer, post-AP completion, outlier split")
    xs, _ = wf.filter(u, "[adj] > [min_ap]", label="units above the lowest AP")
    xl = C.append(wf, xs, lay, "ku", "kl", "units x layers")

    def xl_of(adj):
        lim = ('IIF([lim_basis] = "TO_BENEFIT_LIMIT", IIF(IsNull([annual_limit]), ' + BIG + ', [annual_limit] - [ap]), '
               'IIF(IsNull([limit]), ' + BIG + ', [limit]))')
        over = f"({adj} - [ap])"
        return (f"IIF({over} <= 0, 0, IIF({over} < {lim}, {over}, IIF({lim} > 0, {lim}, 0))) * (1 - [cop])")
    xl = wf.formula(xl, [
        ("xl", X.DBL, xl_of("[adj]")), ("xl_less1", X.DBL, xl_of("[adj_less1]")),
        ("n_xl", X.DBL, "IIF([xl] > 0, 1, 0)"),
        ("xl_c", X.DBL, "[xl] * [post_amt_f_]"), ("n_c", X.DBL, "[n_xl] * [post_cnt_f_]"),
        ("xl_non", X.DBL, "IIF([outlier] = 1, 0, [xl_c])"), ("xl_out", X.DBL, "IIF([outlier] = 1, [xl_c], 0)"),
        ("n_non", X.DBL, "IIF([outlier] = 1, 0, [n_c])"), ("n_out", X.DBL, "IIF([outlier] = 1, [n_c], 0)")],
        label="layer loss")
    units = wf.select(xl, keep=["acc_key", "member_id", "ty", "option_id", "layer", "l_label", "adj", "outlier", "xl",
                                "xl_c", "n_xl", "n_c", "annual_limit", "first_treatment", "segment", "scheme_id"],
                      keep_unknown=False, label="bc_units")
    wf.output_yxdb(units, C.stage(v, "bc_units"), label="bc_units")

    # ------------------------------------------------------------------ by treaty year
    wf.container("03.5 By treaty year: AAD/AAL, exposure, frequency, severity, BC")
    ex = wf.input_yxdb(C.stage(v, "exp_exposure_ty"), label="exp_exposure_ty (stage 02)")
    yk = wf.formula(par, [("yk", X.INT, "1")], label="years key")
    yrs = C.years_stream(wf, yk, "[p_years_included]", out="g_ty", label="experience years")
    grid = C.append(wf, wf.formula(yrs, [("kg", X.INT, "1")], label="key"), lay, "kg", "kl", "years x layers")
    grid = wf.formula(grid, [("ty", X.INT64, "[g_ty]")] + [(f, X.DBL, "0") for f in
                                                          ("xl", "xl_c", "n_xl", "n_c", "xl_non", "xl_out",
                                                           "n_non", "n_out", "xl_less1")], label="zero rows")
    grid = wf.select(grid, keep=["option_id", "layer", "ty", "xl", "xl_c", "n_xl", "n_c", "xl_non", "xl_out", "n_non",
                                 "n_out", "xl_less1"], keep_unknown=False, label="grid")
    det = wf.select(xl, keep=["option_id", "layer", "ty", "xl", "xl_c", "n_xl", "n_c", "xl_non", "xl_out", "n_non",
                              "n_out", "xl_less1"], keep_unknown=False, label="unit layer amounts")
    by = wf.summarize(wf.union([det, grid], label="amounts + zero grid"),
                      [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"), ("ty", "GroupBy", "ty")] +
                      [(f, "Sum", f) for f in ("xl", "xl_c", "n_xl", "n_c", "xl_non", "xl_out", "n_non", "n_out",
                                               "xl_less1")], label="layer x treaty year")
    by = _join_layer(wf, by, lay)
    by = wf.join(by, wf.select(ex, keep=["ty", "months_elapsed", "exposure_ly_full", "exposure_prem_full"],
                               rename={"ty": "e_ty"}, keep_unknown=False, label="exposure"), ["ty"], ["e_ty"],
                 label="+ exposure")["J"]
    by = wf.join(by, wf.select(trend, keep=["tr_year", "prem_trend_f"], keep_unknown=False, label="premium trend"),
                 ["ty"], ["tr_year"], label="+ premium trend")["J"]
    by = wf.formula(by, [
        ("xl_aa", X.DBL, "IIF(IsNull([aal]) AND IsNull([aad]), [xl_c], "
                         "IIF(IsNull([aal]), " + iif_max0("[xl_c] - IIF(IsNull([aad]), 0, [aad])") + ", "
                         "IIF(" + iif_max0("[xl_c] - IIF(IsNull([aad]), 0, [aad])") + " < [aal], "
                         + iif_max0("[xl_c] - IIF(IsNull([aad]), 0, [aad])") + ", [aal])))"),
        ("aa_f", X.DBL, "IIF([xl_c] > 0, [xl_aa] / [xl_c], 1)"),
        ("freq_per_1000", X.DBL, "[n_c] / [exposure_ly_full] * 1000"),
        ("severity", X.DBL, "IIF([n_c] > 0, [xl_c] / [n_c], 0)"),
        ("bc_pppy_non", X.DBL, "[xl_non] * [aa_f] / [exposure_ly_full]"),
        ("bc_pppy_out", X.DBL, "[xl_out] * [aa_f] / [exposure_ly_full]"),
        ("bc_pppy", X.DBL, "[bc_pppy_non] + [bc_pppy_out]"),
        ("bc_pct_prem", X.DBL, "[xl_aa] / ([exposure_prem_full] * [prem_trend_f])"),
        ("bc_pct_prem_non", X.DBL, "[xl_non] * [aa_f] / ([exposure_prem_full] * [prem_trend_f])"),
        ("bc_pct_prem_out", X.DBL, "[xl_out] * [aa_f] / ([exposure_prem_full] * [prem_trend_f])")],
        label="annual measures")

    # ------------------------------------------------------------------ weights
    wf.container("03.6 Weights (default by years available x months complete, or custom)")
    wt = wf.input_yxdb(C.stage(v, "ctl_weights"), label="ctl_weights (stage 00)")
    yrw = wf.left_join(wf.formula(yrs, [("kw", X.INT, "1")], label="key"),
                       wf.select(wt, keep=["year", "months_complete", "use_default", "custom_weight"],
                                 keep_unknown=False, label="weights table"), ["g_ty"], ["year"], label="+ weights")
    yrw = wf.record_id(wf.sort(yrw, [("g_ty", "Ascending")], label="oldest first"), "pos", label="position")
    cnt = wf.summarize(yrw, [("kw", "GroupBy", "kw2"), ("pos", "Count", "n_years"),
                             ("use_default", "Min", "min_default")], label="years available")
    yrw = C.append(wf, yrw, cnt, "kw", "kw2", "+ years available")
    base = wf.text_input(["b_n", "b_pos", "b_w"], [[n, i + 1, w] for n, ws in DEFAULT_WEIGHTS.items()
                                                    for i, w in enumerate(ws)], label="default weight table")
    yrw = wf.join(yrw, base, ["n_years", "pos"], ["b_n", "b_pos"], label="+ default weights")["J"]
    yrw = wf.formula(yrw, [("w_raw", X.DBL, "[b_w] * IIF(IsNull([months_complete]), 12, [months_complete]) / 12")],
                     label="weight x months complete")
    ws = wf.summarize(yrw, [("kw", "GroupBy", "kw3"), ("w_raw", "Sum", "w_sum")], label="weight total")
    yrw = C.append(wf, yrw, ws, "kw", "kw3", "+ total")
    yrw = wf.formula(yrw, [("weight", X.DBL, "IIF(IsNull([min_default]), [w_raw] / [w_sum], IIF([min_default] = 0, "
                                             "IIF(IsNull([custom_weight]), 0, [custom_weight]), [w_raw] / [w_sum]))"),
                           ("weight_out", X.DBL, "1 / [n_years]")], label="final weights")
    yrw = wf.select(yrw, keep=["g_ty", "weight", "weight_out", "n_years"], keep_unknown=False, label="weights by TY")
    wf.output_yxdb(yrw, C.stage(v, "bc_weights"), label="bc_weights")
    by = wf.join(by, yrw, ["ty"], ["g_ty"], label="+ weights")["J"]
    byo = wf.select(by, keep=["option_id", "layer", "l_label", "ty", "months_elapsed", "exposure_ly_full",
                              "exposure_prem_full", "prem_trend_f", "n_xl", "n_c", "freq_per_1000", "xl", "xl_c",
                              "severity", "xl_aa", "xl_non", "xl_out", "bc_pppy", "bc_pppy_non", "bc_pppy_out",
                              "bc_pct_prem", "weight", "weight_out"], keep_unknown=False, label="out_bc_by_year fields")
    byo = wf.sort(byo, [("option_id", "Ascending"), ("layer", "Ascending"), ("ty", "Ascending")], label="order")
    wf.output_csv(byo, results(v, "out_bc_by_year"), label="out_bc_by_year.csv")

    # ------------------------------------------------------------------ summary
    wf.container("03.7 Summary: weighted BC, credibility, sensitivities, leveraged trend, quote-year cost")
    s = wf.formula(by, [("w_non", X.DBL, "[weight] * [bc_pppy_non]"), ("w_out", X.DBL, "[weight_out] * [bc_pppy_out]"),
                        ("w_pct", X.DBL, "[weight] * [bc_pct_prem_non] + [weight_out] * [bc_pct_prem_out]"),
                        ("w_freq", X.DBL, "[weight] * [freq_per_1000]"),
                        ("prem_q", X.DBL, "[exposure_prem_full] * [prem_trend_f]")], label="weighted pieces")
    sm = wf.summarize(s, [("option_id", "GroupBy", "option_id"), ("layer", "GroupBy", "layer"),
                          ("w_non", "Sum", "bc_pppy_weighted_non"), ("w_out", "Sum", "bc_pppy_outlier"),
                          ("w_pct", "Sum", "bc_pct_prem_weighted"), ("w_freq", "Sum", "freq_weighted"),
                          ("bc_pppy", "Sum", "bc_sum"), ("freq_per_1000", "Sum", "freq_sum"),
                          ("bc_pppy", "Min", "bc_min"), ("bc_pppy", "Max", "bc_max"), ("n_xl", "Sum", "n_xl_claims"),
                          ("n_c", "Sum", "n_completed"), ("xl_c", "Sum", "xl_c_total"), ("xl", "Sum", "xl_total"),
                          ("xl_less1", "Sum", "xl_less1_total"), ("n_years", "Max", "n_years"),
                          ("ty", "Max", "last_ty")], label="by layer")
    last = wf.select(by, keep=["option_id", "layer", "ty", "exposure_ly_full", "exposure_prem_full", "prem_trend_f"],
                     rename={"option_id": "l_opt", "layer": "l_lay", "ty": "l_ty"}, keep_unknown=False,
                     label="exposure by TY")
    sm = wf.join(sm, last, ["option_id", "layer", "last_ty"], ["l_opt", "l_lay", "l_ty"], label="+ latest TY exposure")["J"]
    sm = _join_layer(wf, sm, lay)
    sm = wf.formula(sm, [("ks", X.INT, "1")], label="key")
    sm = C.append(wf, sm, par, "ks", "kp", "+ parameters")
    tq = wf.summarize(trend, [("q_t", "Max", "base_trend")], label="quote-year trend")
    tq = wf.formula(tq, [("kbt", X.INT, "1")], label="key")
    sm = C.append(wf, sm, tq, "ks", "kbt", "+ base trend")
    sm = C.append(wf, sm, wf.select(tha, keep=["kth", "outlier_thr"], keep_unknown=False, label="threshold"),
                  "ks", "kth", "+ outlier threshold")
    sm = wf.formula(sm, [
        ("bc_pppy_total", X.DBL, "[bc_pppy_weighted_non] + [bc_pppy_outlier]"),
        ("credibility_z", X.DBL, "IIF([n_xl_claims] >= [p_full_cred_n], 1, Sqrt([n_xl_claims] / [p_full_cred_n]))"),
        ("sens_straight_mean", X.DBL, "[bc_sum] / [n_years]"),
        ("severity_pooled", X.DBL, "IIF([n_completed] > 0, [xl_c_total] / [n_completed], 0)"),
        ("sens_wfreq_x_sev", X.DBL, "[freq_weighted] / 1000 * [severity_pooled]"),
        ("sens_sfreq_x_sev", X.DBL, "[freq_sum] / [n_years] / 1000 * [severity_pooled]"),
        ("lev_trend", X.DBL, "IIF([xl_less1_total] > 0, [xl_total] / [xl_less1_total] - 1, Null())"),
        ("leverage_factor", X.DBL, "IIF(IsNull([lev_trend]) OR [base_trend] = 0, Null(), [lev_trend] / [base_trend])"),
        ("lives_quote_year", X.DBL, "IIF(IsNull([p_expected_lives_quote_year]), [p_epi_quote_year] / "
                                    "([exposure_prem_full] / [exposure_ly_full] * [prem_trend_f]), "
                                    "[p_expected_lives_quote_year])"),
        ("expected_cost_det", X.DBL, "[bc_pppy_total] * [lives_quote_year] * [p_incidence_adj]"),
        ("expected_cost_pct_epi", X.DBL, "[expected_cost_det] / [p_epi_quote_year]"),
        ("expected_cost_from_pct", X.DBL, "[bc_pct_prem_weighted] * [p_epi_quote_year] * [p_incidence_adj]")],
        label="summary measures")
    smo = wf.select(sm, keep=["option_id", "layer", "l_label", "lim_basis", "ap", "limit", "aad", "aal", "cop",
                              "n_years", "n_xl_claims", "credibility_z", "bc_pppy_weighted_non", "bc_pppy_outlier",
                              "bc_pppy_total", "bc_pct_prem_weighted", "sens_straight_mean", "sens_wfreq_x_sev",
                              "sens_sfreq_x_sev", "bc_min", "bc_max", "lev_trend", "base_trend", "leverage_factor",
                              "outlier_thr", "lives_quote_year", "expected_cost_det", "expected_cost_pct_epi",
                              "expected_cost_from_pct"], keep_unknown=False, label="out_bc_summary fields")
    smo = wf.sort(smo, [("option_id", "Ascending"), ("layer", "Ascending")], label="order")
    wf.output_csv(smo, results(v, "out_bc_summary"), label="out_bc_summary.csv")
    wf.output_yxdb(smo, C.stage(v, "bc_summary"), label="bc_summary")

    # ------------------------------------------------------------------ annualisation exhibit
    wf.container("03.8 Annualisation of each treaty year (reported -> + IBNR -> + unearned -> + unwritten)")
    an = wf.join(wf.select(by, keep=["option_id", "layer", "l_label", "ty", "xl", "n_xl"], keep_unknown=False,
                           label="reported layer loss"), comp, ["ty"], ["cf_year"], label="+ completion")
    an = wf.union([an["J"], an["L"]], label="(left join)", differ="Ignore")
    one = lambda f: f"IIF(IsNull([{f}]), 1, [{f}])"  # noqa: E731
    an = wf.formula(an, [
        ("xl_reported", X.DBL, "[xl]"),
        ("xl_ibnr", X.DBL, "[xl] * " + one(fname("IBNR", "POST_AP_AMOUNT"))),
        ("xl_unearned", X.DBL, "[xl_ibnr] * " + one(fname("UNEARNED", "POST_AP_AMOUNT"))),
        ("xl_unwritten", X.DBL, "[xl_unearned] * " + one(fname("UNWRITTEN", "POST_AP_AMOUNT"))),
        ("n_reported", X.DBL, "[n_xl]"),
        ("n_ibnr", X.DBL, "[n_xl] * " + one(fname("IBNR", "POST_AP_COUNT"))),
        ("n_unearned", X.DBL, "[n_ibnr] * " + one(fname("UNEARNED", "POST_AP_COUNT"))),
        ("n_unwritten", X.DBL, "[n_unearned] * " + one(fname("UNWRITTEN", "POST_AP_COUNT")))],
        label="annualisation steps (post-AP factors; pre-AP already in xl)")
    an = wf.select(an, keep=["option_id", "layer", "l_label", "ty", "xl_reported", "xl_ibnr", "xl_unearned",
                             "xl_unwritten", "n_reported", "n_ibnr", "n_unearned", "n_unwritten"], keep_unknown=False,
                   label="out_annualisation fields")
    wf.output_csv(wf.sort(an, [("option_id", "Ascending"), ("layer", "Ascending"), ("ty", "Ascending")], label="order"),
                  results(v, "out_annualisation"), label="out_annualisation.csv")

    # ------------------------------------------------------------------ checks and recon
    wf.container("03.9 Checks and recon")
    b2, _ = wf.filter(sm, "[credibility_z] < 1", label="B02 credibility < 1")
    checks.append(C.check_formula(wf, b2, sn, "B02", "FLAG", "[l_label]",
                                  '"credibility " + ToString([credibility_z], 2) + " (" + ToString([n_xl_claims], 0) + '
                                  '" XL claims < " + ToString([p_full_cred_n], 0) + "): blend with the complement"'))
    b1, _ = wf.filter(sm, "[n_xl_claims] = 0", label="B01 no XL claims")
    checks.append(C.check_formula(wf, b1, sn, "B01", "FLAG", "[l_label]", '"no claim reaches this layer in the data"'))
    b3, _ = wf.filter(sm, "!IsNull([aad]) OR !IsNull([aal])", label="B03 AAD/AAL")
    checks.append(C.check_formula(wf, b3, sn, "B03", "FLAG", "[l_label]",
                                  '"AAD/AAL applied to annual as-if totals: use the stochastic result for pricing"'))
    nc = wf.join(wf.formula(yrs, [("kc", X.INT, "1")], label="key"), comp, ["g_ty"], ["cf_year"],
                 label="experience years vs completion")
    checks.append(C.check_formula(wf, nc["L"], sn, "B04", "FLAG", '"TY " + ToString([g_ty])',
                                  '"no completion factors: 1 used"'))
    C.recon_rows(wf, [(u, "units", "rows_in", None), (u, "units", "amount_q", "amount_q"),
                      (u, "units", "adj", "adj"), (lc, "out_large_claims", "rows", None),
                      (units, "bc_units", "rows", None), (units, "bc_units", "xl_c", "xl_c"),
                      (byo, "out_bc_by_year", "xl_aa", "xl_aa"), (smo, "out_bc_summary", "rows", None)], v, sn)
    C.finish_checks(wf, checks, v, sn)
    return C.save(wf, v, "03_burning_cost.yxmd")


def _join_layer(wf, src, lay):
    """Attach the layer terms (label, AP, limit, AAD/AAL, co-participation) by option and layer."""
    lt = wf.select(lay, keep=["option_id", "layer", "l_label", "lim_basis", "ap", "limit", "aal", "aad", "cop"],
                   rename={"option_id": "t_opt", "layer": "t_lay"}, keep_unknown=False, label="layer terms")
    return wf.join(src, lt, ["option_id", "layer"], ["t_opt", "t_lay"], label="+ layer terms")["J"]


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build(v))
