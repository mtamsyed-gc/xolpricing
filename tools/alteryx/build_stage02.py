"""Stage 02 - experience by treaty year (scope §5 stages 02-04).

Reads int_claims, int_exposure, int_run_meta (stage 01) and ctl_params (stage 00). Writes:
    exp_claimants      one row per accumulation unit x treaty year (all sizes; thresholds come later)
    exp_exposure_ty    treaty-year exposure, to-date and projected to the full year, with indicated factors
    out_completion_indicated.csv, checks_02.csv, recon_02.csv

Treaty year (TY) of a date d: the year in which the treaty year containing d starts (treaty start month-day).
    RA  claims: TY of the policy inception (cohort);  exposure: lives/premium WRITTEN in the TY (cohorts)
    LO  claims: TY of the treatment date (INCURRED) or payment date (PAID); exposure: EARNED in the TY
Accumulation unit: member x TY, plus x policy when pp_accumulation = POLICY_YEAR (line grain).
Indicated factors (reference for the Control workbook; never applied automatically):
    UNEARNED  (RA)  written premium / earned-to-date premium of the TY's cohorts
    UNWRITTEN       prior TY full-year / prior TY over the same months elapsed (premium and life-years)
"""
from __future__ import annotations

import xol_common as C
from xol_common import X

MS = X.mstart
PARAMS = ["treaty_basis", "claims_trigger", "paid_runoff_months", "pp_accumulation", "treaty_start", "years_included",
          "quote_year", "qs_cedant_retention_pct"]
META = ["valuation_date", "exposure_by_cohort", "source_mode"]


def with_context(wf, src, v, label):
    p = C.params_stream(wf, v, PARAMS, label=f"{label}: parameters")
    m = C.meta_stream(wf, v, META, label=f"{label}: run meta")
    s = wf.formula(src, [("kx", X.INT, "1")], label=f"{label}: key")
    s = C.append(wf, s, p, "kx", "kp", f"{label} + parameters")
    s = C.append(wf, s, m, "kx", "km", f"{label} + run meta")
    return wf.formula(s, [("ts_md", X.STR, 'DateTimeFormat([p_treaty_start], "%m-%d")'),
                          ("ts_mm", X.STR, 'DateTimeFormat([p_treaty_start], "%m")'),
                          ("val_month", X.DATE, MS("[m_valuation_date]")),
                          ("is_ra", X.BOOL, '[p_treaty_basis] = "RA"')], label=f"{label}: treaty calendar")


def build(v):
    wf = X.WF("02_experience", "Stage 02: experience by treaty year (claims accumulation, exposure, indicated factors)")
    wf.dag = True
    checks, sn = [], "02"

    # ------------------------------------------------------------------ claims -> accumulation units
    wf.container("02.1 Claims: treaty year, run-off, accumulation unit")
    raw = wf.input_yxdb(C.stage(v, "int_claims"), label="int_claims (stage 01)")
    c = with_context(wf, raw, v, "claims")
    c = wf.formula(c, [
        ("amount_q", X.DBL, "([amount_paid_q] + [amount_os_q]) * [p_qs_cedant_retention_pct]"),
        ("paid_q", X.DBL, "[amount_paid_q] * [p_qs_cedant_retention_pct]"),
        ("os_q", X.DBL, "[amount_os_q] * [p_qs_cedant_retention_pct]"),
        ("trigger_date", X.DATE, 'IIF([p_claims_trigger] = "PAID" AND !IsNull([paid_date]), [paid_date], '
                                 'IIF([p_claims_trigger] = "PAID", [m_valuation_date], [treatment_date]))'),
        ("ty", X.INT64, "IIF([is_ra], " + C.ty_of("[cohort_month]") + ", " + C.ty_of("[trigger_date]") + ")"),
        ("ty_start", X.DATE, C.ty_start("[ty]")),
        ("runoff_end", X.DATE, 'IIF(IsNull([p_paid_runoff_months]), Null(), '
                               'ToDate(DateTimeAdd(DateTimeAdd([ty_start], 12 + [p_paid_runoff_months], "months"), '
                               '-1, "days")))'),
        ("late_paid", X.BOOL, "IIF(IsNull([runoff_end]) OR IsNull([paid_date]), 1=0, [paid_date] > [runoff_end])"),
        ("in_years", X.BOOL, C.in_csv("[p_years_included]", "ToString([ty])")),
        ("acc_key", X.STR, 'IIF([grain] = "CLAIMANT", [claim_key], [member_id] + '
                           'IIF([p_pp_accumulation] = "POLICY_YEAR" AND !IsNull([policy_id]), "|" + [policy_id], ""))'
                           ' + "|" + ToString([ty])')], label="treaty year, run-off, unit key")
    late, c = wf.filter(c, "[late_paid]", label="paid after run-off (excluded)")
    out_years, c = wf.filter(c, "![in_years]", label="outside years_included (excluded)")
    acc = wf.summarize(c, [
        ("acc_key", "GroupBy", "acc_key"), ("member_id", "GroupBy", "member_id"), ("ty", "GroupBy", "ty"),
        ("cohort_month", "Min", "cohort_month"), ("segment", "First", "segment"), ("scheme_id", "First", "scheme_id"),
        ("site", "First", "site"), ("annual_limit", "Max", "annual_limit"), ("treatment_date", "Min", "first_treatment"),
        ("treatment_date", "Max", "last_treatment"), ("amount_q", "Sum", "amount_q"), ("paid_q", "Sum", "paid_q"),
        ("os_q", "Sum", "os_q"), ("claim_key", "Count", "n_lines"), ("grain", "First", "grain")],
        label="accumulate per unit")
    wf.output_yxdb(acc, C.stage(v, "exp_claimants"), label="exp_claimants")

    # ------------------------------------------------------------------ exposure by treaty year
    wf.container("02.2 Exposure by treaty year, months elapsed, indicated factors")
    e = wf.input_yxdb(C.stage(v, "int_exposure"), label="int_exposure (stage 01)")
    e = with_context(wf, e, v, "exposure")
    e = wf.formula(e, [
        ("frac_c", X.DBL, 'IIF([m_exposure_by_cohort], 1, '
                          'IIF(DateTimeDiff([val_month], [cohort_month], "months") + 1 >= 12, 1, '
                          '(DateTimeDiff([val_month], [cohort_month], "months") + 1) / 12))'),
        ("ref_month", X.DATE, "IIF([is_ra], [cohort_month], [exposure_month])"),
        ("ty", X.INT64, C.ty_of("[ref_month]")),
        ("ty_start", X.DATE, C.ty_start("[ty]")),
        ("m_idx", X.INT, 'DateTimeDiff([ref_month], [ty_start], "months") + 1'),
        ("x_ly", X.DBL, "IIF([is_ra], [written_ly], [earned_ly])"),
        ("x_prem", X.DBL, "IIF([is_ra], [written_premium], [earned_premium])"),
        ("earned_prem_todate", X.DBL, "IIF([is_ra], IIF([m_exposure_by_cohort], [earned_premium], "
                                      "[written_premium] * [frac_c]), [earned_premium])")], label="basis exposure")
    bym = wf.summarize(e, [("ty", "GroupBy", "ty"), ("m_idx", "GroupBy", "m_idx"), ("x_ly", "Sum", "x_ly"),
                           ("x_prem", "Sum", "x_prem"), ("earned_prem_todate", "Sum", "earned_prem_todate")],
                       label="by treaty year x month index")
    tot = wf.summarize(bym, [("ty", "GroupBy", "ty"), ("x_ly", "Sum", "exposure_ly"), ("x_prem", "Sum", "exposure_prem"),
                             ("earned_prem_todate", "Sum", "earned_prem_todate")], label="treaty-year totals")
    # months elapsed in each TY at valuation (needs the calendar fields once per TY)
    cal = wf.summarize(e, [("ty", "GroupBy", "ty"), ("ty_start", "First", "ty_start"),
                           ("val_month", "First", "val_month")], label="TY calendar")
    cal = wf.formula(cal, [("el_raw", X.INT, 'DateTimeDiff([val_month], [ty_start], "months") + 1'),
                           ("months_elapsed", X.INT, "IIF([el_raw] > 12, 12, IIF([el_raw] < 0, 0, [el_raw]))")],
                     label="months elapsed")
    cal = wf.select(cal, keep=["ty", "months_elapsed"], keep_unknown=False, label="TY months elapsed")
    tot = wf.join(tot, wf.select(cal, keep=["ty", "months_elapsed"], rename={"ty": "c_ty"}, keep_unknown=False,
                                 label="calendar keys"), ["ty"], ["c_ty"], label="totals + months elapsed")["J"]
    # prior TY over the same months, and prior TY full year
    prev = wf.formula(bym, [("ty_next", X.INT64, "[ty] + 1")], label="as prior year of ty+1")
    prev = wf.select(prev, keep=["ty_next", "m_idx", "x_ly", "x_prem"], keep_unknown=False, label="prior-year months")
    pj = wf.join(prev, wf.select(cal, keep=["ty", "months_elapsed"], rename={"ty": "p_ty", "months_elapsed": "p_el"},
                                 keep_unknown=False, label="target TY"), ["ty_next"], ["p_ty"],
                 label="prior months x target TY")["J"]
    same, _ = wf.filter(pj, "[m_idx] <= [p_el]", label="same months as elapsed")
    same = wf.summarize(same, [("ty_next", "GroupBy", "s_ty"), ("x_ly", "Sum", "same_ly"),
                               ("x_prem", "Sum", "same_prem")], label="prior TY, same months")
    full = wf.summarize(prev, [("ty_next", "GroupBy", "f_ty"), ("x_ly", "Sum", "full_ly"),
                               ("x_prem", "Sum", "full_prem")], label="prior TY, full year")
    t1 = wf.left_join(tot, same, ["ty"], ["s_ty"], label="+ prior same months")
    t2 = wf.left_join(t1, full, ["ty"], ["f_ty"], label="+ prior full year")
    t2 = wf.formula(t2, [
        ("ind_unwritten_ly", X.DBL, "IIF([months_elapsed] >= 12, 1, IIF(IsNull([same_ly]) OR IsNull([full_ly]), Null(), "
                                    "IIF([same_ly] <= 0, Null(), [full_ly] / [same_ly])))"),
        ("ind_unwritten_prem", X.DBL, "IIF([months_elapsed] >= 12, 1, IIF(IsNull([same_prem]) OR IsNull([full_prem]), "
                                      "Null(), IIF([same_prem] <= 0, Null(), [full_prem] / [same_prem])))"),
        ("ind_unearned", X.DBL, "IIF([earned_prem_todate] > 0, [exposure_prem] / [earned_prem_todate], Null())"),
        ("exposure_ly_full", X.DBL, "[exposure_ly] * IIF(IsNull([ind_unwritten_ly]), 1, [ind_unwritten_ly])"),
        ("exposure_prem_full", X.DBL, "[exposure_prem] * IIF(IsNull([ind_unwritten_prem]), 1, [ind_unwritten_prem])")],
        label="indicated factors, full-year exposure")
    p_basis = C.params_stream(wf, v, ["treaty_basis", "years_included"], key="kb", label="basis")
    t2 = wf.formula(t2, [("kt", X.INT, "1")], label="key")
    t2 = C.append(wf, t2, p_basis, "kt", "kb", "+ basis")
    t2 = wf.formula(t2, [("ind_unearned_basis", X.DBL, 'IIF([p_treaty_basis] = "RA", [ind_unearned], 1)')],
                    label="unearned applies to RA only")
    expo = wf.select(t2, keep=["ty", "months_elapsed", "exposure_ly", "exposure_prem", "earned_prem_todate",
                               "ind_unwritten_ly", "ind_unwritten_prem", "ind_unearned_basis", "exposure_ly_full",
                               "exposure_prem_full"], rename={"ind_unearned_basis": "ind_unearned"},
                     keep_unknown=False, label="exp_exposure_ty")
    expo = wf.sort(expo, [("ty", "Ascending")], label="by treaty year")
    wf.output_yxdb(expo, C.stage(v, "exp_exposure_ty"), label="exp_exposure_ty")
    ind_u = wf.formula(expo, [("component", X.STR, '"UNEARNED"'), ("measure", X.STR, '"premium"'),
                              ("indicated_factor", X.DBL, "[ind_unearned]")], label="UNEARNED rows")
    ind_w = wf.formula(expo, [("component", X.STR, '"UNWRITTEN"'), ("measure", X.STR, '"premium"'),
                              ("indicated_factor", X.DBL, "[ind_unwritten_prem]")], label="UNWRITTEN rows")
    ind_l = wf.formula(expo, [("component", X.STR, '"UNWRITTEN"'), ("measure", X.STR, '"life_years"'),
                              ("indicated_factor", X.DBL, "[ind_unwritten_ly]")], label="UNWRITTEN LY rows")
    ind = wf.union([ind_u, ind_w, ind_l], label="indicated factors")
    ind = wf.select(ind, keep=["ty", "component", "measure", "months_elapsed", "indicated_factor"],
                    keep_unknown=False, label="indicated factor fields")
    wf.output_csv(ind, v.p(f"{v.out_dir}/checks/out_completion_indicated.csv"), label="out_completion_indicated.csv")

    # ------------------------------------------------------------------ checks and recon
    wf.container("02.3 Checks and recon")
    yk = wf.formula(p_basis, [("yk", X.INT, "1")], label="years key")
    yrs = C.years_stream(wf, yk, "[p_years_included]", label="experience years")
    jy = wf.join(yrs, wf.select(expo, keep=["ty", "exposure_ly"], keep_unknown=False, label="exposure years"),
                 ["need_year"], ["ty"], label="experience years vs exposure")
    checks.append(C.check_formula(wf, jy["L"], sn, "E01", "STOP", '"TY " + ToString([need_year])',
                                  '"no exposure for an experience year"'))
    z, _ = wf.filter(jy["J"], "[exposure_ly] <= 0", label="E01 zero exposure")
    checks.append(C.check_formula(wf, z, sn, "E01", "STOP", '"TY " + ToString([need_year])',
                                  '"zero exposure for an experience year"'))
    nb, _ = wf.filter(expo, "[months_elapsed] < 12 AND IsNull([ind_unwritten_prem])", label="E02 no prior year")
    checks.append(C.check_formula(wf, nb, sn, "E02", "FLAG", '"TY " + ToString([ty])',
                                  '"partial treaty year without a prior year: no indicated unwritten factor"'))
    pd_ = C.params_stream(wf, v, ["treaty_start"], key="kd", label="treaty start")
    ds, _ = wf.filter(pd_, 'DateTimeDay([p_treaty_start]) != 1', label="E03 treaty start not 1st")
    checks.append(C.check_formula(wf, ds, sn, "E03", "STOP", '"treaty_start"',
                                  '"treaty start must be the 1st of a month in this version"'))
    lu, _ = wf.unique(late, ["claim_key"], label="late lines")
    lsum = wf.summarize(wf.formula(lu, [("kk", X.INT, "1")], label="key"),
                        [("kk", "GroupBy", "kk"), ("amount_q", "Sum", "amt"), ("claim_key", "Count", "n")],
                        label="late-paid totals")
    checks.append(C.check_formula(wf, lsum, sn, "E04", "FLAG", '"paid run-off"',
                                  'ToString([n]) + " lines paid after the run-off excluded"'))
    C.recon_rows(wf, [(raw, "int_claims", "rows_in", None), (wf.formula(raw, [("aq", X.DBL, "[amount_paid_q] + [amount_os_q]")],
                                                                        label="amount in"), "int_claims", "amount_q_in", "aq"),
                      (late, "excluded", "late_paid_amount_q", "amount_q"),
                      (out_years, "excluded", "outside_years_amount_q", "amount_q"),
                      (acc, "exp_claimants", "rows_out", None), (acc, "exp_claimants", "amount_q", "amount_q"),
                      (expo, "exp_exposure_ty", "exposure_ly", "exposure_ly"),
                      (expo, "exp_exposure_ty", "exposure_prem", "exposure_prem")], v, sn)
    C.finish_checks(wf, checks, v, sn)
    return C.save(wf, v, "02_experience.yxmd")


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build(v))
