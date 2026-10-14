"""Stage 01 - standalone adapters (docs/data-contracts.md §2-3, §6).

    01a_adapter_line.yxmd      members.csv + claims_lines.csv + run_meta.csv  -> int_* (grain LINE)
    01b_adapter_claimant.yxmd  claimants.csv + exposure_monthly.csv + claims_aggregate_monthly.csv + run_meta.csv
                               -> int_* (grain CLAIMANT)

Outputs (staging .yxdb): int_claims, int_members (line only), int_exposure, int_claims_aggregate, int_run_meta;
checks_01a.csv / checks_01b.csv; recon_01a.csv / recon_01b.csv.

Currency: claims keep the original amounts and gain *_q amounts at the priced FX rate (ctl_fx); exposure premium and
the claims aggregate are in quote currency (they sum across members).
"""
from __future__ import annotations

import xol_common as C
from xol_common import X

F = X.F
MS = X.mstart


def num(field):
    return f'IIF(IsEmpty([{field}]), Null(), ToNumber([{field}]))'


def dt(field):
    return f'IIF(IsEmpty([{field}]), Null(), ToDate([{field}]))'


def txt(field, default=None):
    return f'IIF(IsEmpty([{field}]), {X.lit(default) if default else "Null()"}, Trim([{field}]))'


def month_end(d):
    return f'ToDate(DateTimeAdd(DateTimeAdd({MS(d)}, 1, "months"), -1, "days"))'


CONTRACT_COLS = {
    "members": ["member_id", "policy_id", "scheme_id", "policy_start", "policy_end", "cover_start", "cover_end", "plan",
                "annual_limit", "segment", "site", "currency", "written_premium", "dob", "gender", "relation"],
    "claims_lines": ["claim_line_id", "claim_id", "member_id", "policy_id", "treatment_date", "admission_date",
                     "discharge_date", "reported_date", "paid_date", "amount", "status", "currency", "event_id",
                     "cause_code", "icd_code", "segment"],
    "claimants": ["member_id", "scheme_id", "period_start", "period_end", "cohort_month", "first_treatment_date",
                  "paid_amount", "os_amount", "claim_count", "annual_limit", "currency", "event_id", "site", "segment"],
    "exposure_monthly": ["month", "segment", "lives_inforce", "lives_new", "written_premium", "earned_premium"],
    "claims_aggregate_monthly": ["incurred_month", "segment", "cohort_month", "paid_amount", "os_amount",
                                 "claim_count"],
    "run_meta": ["valuation_date", "listing_threshold", "currency_basis", "data_source_note"],
}


def read_contract_csv(wf, path, file_label, required, checks, stage_no):
    """CSV input + A01 on the raw columns, then a union with an empty template of every contract column, so a missing
    column arrives as Null instead of failing the workflow (the A01 STOP then stops the run with a clear message)."""
    raw = wf.input_csv(path, label=f"{file_label}.csv")
    required_columns(wf, raw, required, file_label, checks, stage_no)
    tpl = wf.text_input(CONTRACT_COLS[file_label], [], label=f"{file_label}: contract columns")
    return raw, wf.union([raw, tpl], label=f"{file_label} + contract columns", differ="Ignore")


def required_columns(wf, src, cols, file_label, checks, stage_no):
    """A01: every required column present in the file."""
    fi = wf.field_info(src, label=f"{file_label}: columns")
    req = wf.text_input(["req_col"], [[c] for c in cols], label=f"{file_label}: required columns")
    j = wf.join(req, fi, ["req_col"], ["Name"], label=f"{file_label}: required vs present")
    checks.append(C.check_formula(wf, j["L"], stage_no, "A01", "STOP", X.lit(file_label) + ' + "." + [req_col]',
                                  '"required column missing"'))


def run_meta(wf, v, folder, checks, stage_no):
    _, rm = read_contract_csv(wf, v.p(f"{folder}/run_meta.csv"), "run_meta", ["valuation_date"], checks, stage_no)
    rm = wf.formula(rm, [("kv", X.INT, "1"), ("valuation", X.DATE, dt("valuation_date")),
                         ("listing_thr", X.DBL, num("listing_threshold"))], label="valuation date")
    bad, _ = wf.filter(rm, "IsNull([valuation])", label="A12 valuation date")
    checks.append(C.check_formula(wf, bad, stage_no, "A12", "STOP", '"run_meta.valuation_date"',
                                  '"not an ISO date (yyyy-mm-dd)"'))
    return wf.select(rm, keep=["kv", "valuation", "listing_thr"], keep_unknown=False, label="valuation, threshold")


def fx_table(wf, v):
    fx = wf.input_yxdb(C.stage(v, "ctl_fx"), label="ctl_fx (stage 00)")
    return wf.select(fx, keep=["currency", "priced_rate_to_quote_ccy"], rename={"currency": "fx_ccy",
                                                                             "priced_rate_to_quote_ccy": "fx_rate"},
                     keep_unknown=False, label="FX rates")


def apply_fx(wf, src, ccy_field, checks, stage_no, item_expr, label):
    """Join the priced FX rate; currencies missing from ctl_fx raise A07 (STOP)."""
    j = wf.join(src, FX_CACHE[wf], [ccy_field], ["fx_ccy"], label=f"{label}: priced FX")
    miss, _ = wf.unique(j["L"], [ccy_field], label=f"{label}: missing currencies")
    checks.append(C.check_formula(wf, miss, stage_no, "A07", "STOP", item_expr, '"currency not in the FX table"'))
    return j["J"]


FX_CACHE = {}


def exposure_from_members(wf, mem, valk):
    """Fan each member period over calendar months up to valuation: earned life-years, earned premium (quote ccy),
    month-average lives; written LY and premium booked in the member's cover-start month."""
    m = wf.join(mem, valk, ["k"], ["kv"], label="members + valuation")["J"]
    m, _ = wf.filter(m, "[cover_start] <= [valuation]", label="covers started by valuation")
    m = wf.formula(m, [("eff_end", X.DATE, X.dmin("[cover_end]", "[valuation]")),
                       ("end_month", X.DATE, MS("[eff_end]"))], label="earning window")
    fan = wf.generate_rows(m, "exposure_month", X.DATE, MS("[cover_start]"), "[exposure_month] <= [end_month]",
                           'ToDate(DateTimeAdd([exposure_month], 1, "months"))', label="one row per month")
    fan = wf.formula(fan, [
        ("mend", X.DATE, month_end("[exposure_month]")),
        ("days", X.INT, X.ddiff(X.dmin("[mend]", "[eff_end]"), X.dmax("[exposure_month]", "[cover_start]")) + " + 1"),
        ("mdays", X.INT, X.ddiff("[mend]", "[exposure_month]") + " + 1"),
        ("earned_ly", X.DBL, "[days] / 365"),
        ("earned_premium", X.DBL, "[written_premium_q] * [days] / [cov_days]"),
        ("lives_inforce", X.DBL, "[days] / [mdays]"),
        ("written_ly", X.DBL, "0"), ("written_premium", X.DBL, "0")], label="earned in month")
    fan = wf.select(fan, keep=["segment", "cohort_month", "exposure_month", "earned_ly", "earned_premium",
                               "lives_inforce", "written_ly", "written_premium"], keep_unknown=False, label="earned rows")
    wr = wf.formula(mem, [("exposure_month", X.DATE, MS("[cover_start]")),
                          ("earned_ly", X.DBL, "0"), ("earned_premium", X.DBL, "0"), ("lives_inforce", X.DBL, "0"),
                          ("written_ly", X.DBL, "[cov_days] / 365"), ("written_premium", X.DBL, "[written_premium_q]")],
                    label="written in cover-start month")
    wr = wf.select(wr, keep=["segment", "cohort_month", "exposure_month", "earned_ly", "earned_premium",
                             "lives_inforce", "written_ly", "written_premium"], keep_unknown=False, label="written rows")
    u = wf.union([fan, wr], label="earned + written")
    return wf.summarize(u, [("segment", "GroupBy", "segment"), ("cohort_month", "GroupBy", "cohort_month"),
                            ("exposure_month", "GroupBy", "exposure_month"), ("earned_ly", "Sum", "earned_ly"),
                            ("written_ly", "Sum", "written_ly"), ("earned_premium", "Sum", "earned_premium"),
                            ("written_premium", "Sum", "written_premium"), ("lives_inforce", "Sum", "lives_inforce")],
                        label="int_exposure grain")


INT_CLAIMS = ["claim_key", "claim_id", "member_id", "policy_id", "scheme_id", "cohort_month", "treatment_date",
              "reported_date", "paid_date", "admission_date", "discharge_date", "amount_paid", "amount_os",
              "amount_paid_q", "amount_os_q", "currency", "fx_rate", "status", "annual_limit", "segment", "site",
              "event_id", "cause_code", "grain", "source_mode", "source_file", "source_row"]


def recon(wf, parts, v, stage_no):
    """parts: list of (tool, table, measure, field or None for row count). An empty source still gives a 0 row."""
    outs = []
    for src, table, measure, field in parts:
        lab = [("stage", X.STR, X.lit("S" + stage_no)), ("table", X.STR, X.lit(table)),
               ("measure", X.STR, X.lit(measure))]
        k = wf.formula(src, lab + [("value", X.DBL, f"[{field}]" if field else "1")], label=f"recon {table} {measure}")
        k = wf.select(k, keep=["stage", "table", "measure", "value"], keep_unknown=False, label="recon fields")
        z = wf.text_input(["stage", "table", "measure", "value"], [["S" + stage_no, table, measure, "0.0"]],
                          label=f"recon zero {table} {measure}")
        u = wf.union([k, z], label=f"recon {table} {measure} + zero")
        outs.append(wf.summarize(u, [("stage", "GroupBy", "stage"), ("table", "GroupBy", "table"),
                                     ("measure", "GroupBy", "measure"), ("value", "Sum", "value")],
                                 label=f"recon {table} {measure}"))
    u = wf.union(outs, label="recon rows")
    wf.output_csv(u, v.p(f"{v.out_dir}/checks/recon_{stage_no}.csv"), label=f"recon_{stage_no}.csv")


# ============================================================================================ line mode
def build_line(v):
    wf = X.WF("01a_adapter_line", "Stage 01a: standalone line-mode adapter")
    wf.dag = True
    checks, sn = [], "01a"

    wf.container("01a.1 Run meta, FX and limits")
    valk = run_meta(wf, v, v.line_dir, checks, sn)
    FX_CACHE[wf] = fx_table(wf, v)
    lim = wf.input_yxdb(C.stage(v, "ctl_limits"), label="ctl_limits (stage 00)")
    lim = wf.select(lim, keep=["plan", "annual_limit"], rename={"plan": "lim_plan", "annual_limit": "plan_limit_q"},
                    keep_unknown=False, label="plan limits")

    # ------------------------------------------------------------------ members
    wf.container("01a.2 Members: type, limits, checks")
    mcols = ["member_id", "policy_id", "scheme_id", "policy_start", "policy_end", "cover_start", "cover_end",
             "currency", "written_premium"]
    mraw, mfull = read_contract_csv(wf, v.p(f"{v.line_dir}/members.csv"), "members", mcols, checks, sn)
    mem = wf.record_id(mfull, "src_row", label="source row")
    mem = wf.formula(mem, [
        ("member_id_t", X.STR, "Uppercase(Trim([member_id]))"), ("policy_id_t", X.STR, "Uppercase(Trim([policy_id]))"),
        ("scheme_id_t", X.STR, "Uppercase(Trim([scheme_id]))"),
        ("policy_start_d", X.DATE, dt("policy_start")), ("policy_end_d", X.DATE, dt("policy_end")),
        ("cover_start_d", X.DATE, dt("cover_start")), ("cover_end_d", X.DATE, dt("cover_end")),
        ("plan_t", X.STR, txt("plan")), ("limit_in", X.DBL, num("annual_limit")),
        ("segment_t", X.STR, txt("segment", "ALL")), ("site_t", X.STR, txt("site")),
        ("currency_t", X.STR, "Uppercase(Trim([currency]))"), ("wp", X.DBL, num("written_premium")),
    ], label="type members")
    mem = wf.select(mem, keep=["member_id_t", "policy_id_t", "scheme_id_t", "policy_start_d", "policy_end_d",
                               "cover_start_d", "cover_end_d", "plan_t", "limit_in", "segment_t", "site_t",
                               "currency_t", "wp", "src_row"],
                    rename={"member_id_t": "member_id", "policy_id_t": "policy_id", "scheme_id_t": "scheme_id",
                            "policy_start_d": "policy_start", "policy_end_d": "policy_end",
                            "cover_start_d": "cover_start", "cover_end_d": "cover_end", "plan_t": "plan",
                            "segment_t": "segment", "site_t": "site", "currency_t": "currency",
                            "wp": "written_premium"}, keep_unknown=False, label="typed members")
    bad_d, mem_ok = wf.filter(mem, "IsNull([policy_start]) OR IsNull([policy_end]) OR IsNull([cover_start]) OR "
                                   "IsNull([cover_end]) OR IsNull([written_premium])", label="A12 member dates/premium")
    checks.append(C.check_formula(wf, bad_d, sn, "A12", "STOP", '"members row " + ToString([src_row])',
                                  '"blank or non-ISO date, or blank premium (row excluded)"'))
    a02, _ = wf.filter(mem_ok, "[cover_start] > [cover_end] OR [policy_start] > [cover_start] OR "
                               "[cover_end] > [policy_end]", label="A02 date order")
    checks.append(C.check_formula(wf, a02, sn, "A02", "FLAG", '[member_id] + " " + [policy_id]',
                                  '"cover / policy dates out of order"'))
    memfx = apply_fx(wf, mem_ok, "currency", checks, sn, '"members currency " + [currency]', "members")
    jl = wf.join(memfx, lim, ["plan"], ["lim_plan"], label="plan limit lookup")
    mem2 = wf.union([jl["J"], jl["L"]], label="members (limit lookup left join)", differ="Ignore")
    mem2 = wf.formula(mem2, [
        ("annual_limit", X.DBL, "IIF(!IsNull([limit_in]), [limit_in] * [fx_rate], "
                                "IIF(IsNull([plan_limit_q]), Null(), [plan_limit_q]))"),
        ("cohort_month", X.DATE, MS("[policy_start]")),
        ("cov_days", X.INT, X.ddiff("[cover_end]", "[cover_start]") + " + 1"),
        ("written_premium_q", X.DBL, "[written_premium] * [fx_rate]"), ("k", X.INT, "1")],
        label="limit in quote ccy, cohort, days")
    int_members = wf.select(mem2, keep=["member_id", "policy_id", "scheme_id", "policy_start", "policy_end",
                                        "cover_start", "cover_end", "plan", "annual_limit", "segment", "site",
                                        "currency", "written_premium", "written_premium_q", "cohort_month",
                                        "cov_days"], keep_unknown=False, label="int_members fields")
    wf.output_yxdb(int_members, C.stage(v, "int_members"), label="int_members")

    wf.container("01a.3 Exposure: monthly fan-out by cohort")
    expo_in = wf.formula(int_members, [("k", X.INT, "1")], label="key")
    int_exposure = exposure_from_members(wf, expo_in, valk)
    wf.output_yxdb(int_exposure, C.stage(v, "int_exposure"), label="int_exposure")

    # ------------------------------------------------------------------ claims
    wf.container("01a.4 Claims: type, map status, match to members, checks")
    ccols = ["claim_line_id", "claim_id", "member_id", "treatment_date", "amount", "status", "currency"]
    craw, cfull = read_contract_csv(wf, v.p(f"{v.line_dir}/claims_lines.csv"), "claims_lines", ccols, checks, sn)
    cl = wf.record_id(cfull, "src_row", label="source row")
    cl = wf.formula(cl, [
        ("c_line", X.STR, "Trim([claim_line_id])"), ("c_claim", X.STR, "Trim([claim_id])"),
        ("c_member", X.STR, "Uppercase(Trim([member_id]))"), ("c_policy", X.STR,
                                                              'IIF(IsEmpty([policy_id]), "", Uppercase(Trim([policy_id])))'),
        ("c_treat", X.DATE, dt("treatment_date")), ("c_rep", X.DATE, dt("reported_date")),
        ("c_paid", X.DATE, dt("paid_date")), ("c_adm", X.DATE, dt("admission_date")),
        ("c_dis", X.DATE, dt("discharge_date")), ("c_amt", X.DBL, num("amount")),
        ("c_status_raw", X.STR, 'Uppercase(Trim([status]))'),
        ("c_status", X.STR, 'IIF([c_status_raw] IN ("PAID", "SETTLED"), "Paid", '
                            'IIF([c_status_raw] IN ("OUTSTANDING", "OS", "PENDING"), "Outstanding", '
                            'IIF([c_status_raw] IN ("REJECTED", "DECLINED"), "Rejected", '
                            'IIF([c_status_raw] IN ("REVERSED", "REVERSAL"), "Reversed", "UNMAPPED"))))'),
        ("c_ccy", X.STR, "Uppercase(Trim([currency]))"), ("c_event", X.STR, txt("event_id")),
        ("c_cause", X.STR, txt("cause_code")), ("c_seg", X.STR, txt("segment"))], label="type claims")
    cl = wf.select(cl, keep=["c_line", "c_claim", "c_member", "c_policy", "c_treat", "c_rep", "c_paid", "c_adm",
                             "c_dis", "c_amt", "c_status", "c_status_raw", "c_ccy", "c_event", "c_cause", "c_seg",
                             "src_row"], keep_unknown=False, label="typed claims")
    a11, cl = wf.filter(cl, '[c_status] = "UNMAPPED"', label="A11 status")
    a11u, _ = wf.unique(a11, ["c_status_raw"], label="unmapped values")
    checks.append(C.check_formula(wf, a11u, sn, "A11", "STOP", '"status " + [c_status_raw]',
                                  '"status value not mapped (Paid/Outstanding/Rejected/Reversed)"'))
    bad_c, cl = wf.filter(cl, "IsNull([c_treat]) OR IsNull([c_amt])", label="A12 claim date/amount")
    checks.append(C.check_formula(wf, bad_c, sn, "A12", "STOP", '"claims row " + ToString([src_row])',
                                  '"blank or non-ISO treatment date, or blank amount (row excluded)"'))
    clv = wf.join(wf.formula(cl, [("k", X.INT, "1")], label="key"), valk, ["k"], ["kv"], label="claims + valuation")["J"]
    a04, cl = wf.filter(clv, "[c_treat] > [valuation]", label="A04 after valuation")
    checks.append(C.check_formula(wf, a04, sn, "A04", "STOP", '"claim line " + [c_line]',
                                  '"treatment date after the valuation date"'))
    rej, cl = wf.filter(cl, '[c_status] = "Rejected"', label="rejected lines (excluded)")

    # match: tier 1 member + policy, tier 2 member with treatment inside cover
    memk = wf.select(int_members, keep=["member_id", "policy_id", "scheme_id", "cohort_month", "cover_start",
                                        "cover_end", "annual_limit", "segment", "site"],
                     rename={"member_id": "m_member", "policy_id": "m_policy", "segment": "m_seg"},
                     keep_unknown=False, label="member keys")
    j1 = wf.join(cl, memk, ["c_member", "c_policy"], ["m_member", "m_policy"], label="tier 1: member + policy")
    t1 = wf.formula(j1["J"], [("match_tier", X.INT, "1")], label="tier 1")
    j2 = wf.join(j1["L"], memk, ["c_member"], ["m_member"], label="tier 2: member")
    t2, _ = wf.filter(j2["J"], "[c_treat] >= [cover_start] AND [c_treat] <= [cover_end]", label="inside cover")
    t2, _ = wf.unique(wf.sort(t2, [("c_line", "Ascending"), ("cover_start", "Descending")], label="latest cover first"),
                      ["c_line"], label="one cover per line")
    t2 = wf.formula(t2, [("match_tier", X.INT, "2")], label="tier 2")
    matched = wf.union([t1, t2], label="matched lines")
    # unmatched = tier-2 left (no member) + tier-2 joined but outside every cover
    t2_keys = wf.select(t2, keep=["c_line"], rename={"c_line": "t2_line"}, keep_unknown=False, label="tier-2 lines")
    jo = wf.join(j1["L"], t2_keys, ["c_line"], ["t2_line"], label="tier-1 misses not matched in tier 2")
    unmatched = jo["L"]

    mk = wf.formula(matched, [("k", X.INT, "1"), ("n", X.DBL, "1"),
                              ("out_cover", X.DBL, "IIF([c_treat] < [cover_start] OR [c_treat] > [cover_end], "
                                                   "Abs([c_amt]), 0)"), ("abs_amt", X.DBL, "Abs([c_amt])")],
                    label="match counters")
    um = wf.formula(unmatched, [("k", X.INT, "1"), ("n", X.DBL, "0"), ("out_cover", X.DBL, "0"),
                                ("abs_amt", X.DBL, "Abs([c_amt])")], label="unmatched counters")
    um_n = wf.formula(um, [("n_un", X.DBL, "1")], label="unmatched flag")
    mk_n = wf.formula(mk, [("n_un", X.DBL, "0")], label="matched flag")
    allm = wf.union([mk_n, um_n], label="all lines", differ="Ignore")
    rate = wf.summarize(allm, [("k", "GroupBy", "k"), ("n", "Sum", "n_matched"), ("n_un", "Sum", "n_unmatched"),
                               ("out_cover", "Sum", "amt_out_cover"), ("abs_amt", "Sum", "amt_abs")],
                        label="match statistics")
    rate = wf.formula(rate, [("match_rate", X.DBL, "[n_matched] / ([n_matched] + [n_unmatched])"),
                             ("out_share", X.DBL, "[amt_out_cover] / [amt_abs]")], label="rates")
    a03, _ = wf.filter(rate, "[match_rate] < 0.95", label="A03 match rate")
    checks.append(C.check_formula(wf, a03, sn, "A03", "STOP", '"claims_lines"',
                                  '"member match rate " + ToString([match_rate] * 100, 2) + "% < 95%"'))
    a05, _ = wf.filter(rate, "[out_share] > 0.01", label="A05 out of cover")
    checks.append(C.check_formula(wf, a05, sn, "A05", "FLAG", '"claims_lines"',
                                  '"out-of-cover amount " + ToString([out_share] * 100, 2) + "% > 1%"'))
    unm1, _ = wf.unique(unmatched, ["c_member"], label="unmatched members")
    checks.append(C.check_formula(wf, unm1, sn, "A13", "FLAG", '"member " + [c_member]',
                                  '"claims with no member record (excluded)"'))

    wf.container("01a.5 Claims: FX, int_claims, aggregate")
    mfx = apply_fx(wf, matched, "c_ccy", checks, sn, '"claims currency " + [c_ccy]', "claims")
    ic = wf.formula(mfx, [
        ("claim_key", X.STR, "[c_line]"), ("claim_id", X.STR, "[c_claim]"), ("member_id", X.STR, "[c_member]"),
        ("policy_id", X.STR, "[m_policy]"), ("treatment_date", X.DATE, "[c_treat]"),
        ("reported_date", X.DATE, "[c_rep]"), ("paid_date", X.DATE, "[c_paid]"),
        ("admission_date", X.DATE, "[c_adm]"), ("discharge_date", X.DATE, "[c_dis]"),
        ("amount_paid", X.DBL, 'IIF([c_status] = "Outstanding", 0, [c_amt])'),
        ("amount_os", X.DBL, 'IIF([c_status] = "Outstanding", [c_amt], 0)'),
        ("amount_paid_q", X.DBL, "[amount_paid] * [fx_rate]"), ("amount_os_q", X.DBL, "[amount_os] * [fx_rate]"),
        ("currency", X.STR, "[c_ccy]"), ("status", X.STR, "[c_status]"),
        ("segment", X.STR, "IIF(IsNull([c_seg]), [m_seg], [c_seg])"), ("event_id", X.STR, "[c_event]"),
        ("cause_code", X.STR, "[c_cause]"), ("grain", X.STR, '"LINE"'), ("source_mode", X.STR, '"L"'),
        ("source_file", X.STR, '"claims_lines.csv"'), ("source_row", X.INT64, "[src_row]")], label="int_claims fields")
    ic = wf.select(ic, keep=INT_CLAIMS, keep_unknown=False, label="int_claims")
    wf.output_yxdb(ic, C.stage(v, "int_claims"), label="int_claims")

    neg = wf.summarize(ic, [("claim_id", "GroupBy", "claim_id"), ("amount_paid", "Sum", "p"),
                            ("amount_os", "Sum", "o")], label="net by claim")
    a06, _ = wf.filter(neg, "[p] + [o] < -0.005", label="A06 negative net")
    checks.append(C.check_formula(wf, a06, sn, "A06", "FLAG", '"claim " + [claim_id]', '"negative net amount"'))

    ag = wf.formula(ic, [("incurred_month", X.DATE, MS("[treatment_date]"))], label="incurred month")
    agg = wf.summarize(ag, [("segment", "GroupBy", "segment"), ("cohort_month", "GroupBy", "cohort_month"),
                            ("incurred_month", "GroupBy", "incurred_month"), ("amount_paid_q", "Sum", "amount_paid"),
                            ("amount_os_q", "Sum", "amount_os"), ("claim_id", "CountDistinct", "claim_count")],
                       label="int_claims_aggregate")
    wf.output_yxdb(agg, C.stage(v, "int_claims_aggregate"), label="int_claims_aggregate")

    wf.container("01a.6 Run meta, recon, checks")
    meta = wf.formula(valk, [
        ("source_mode", X.STR, '"L"'), ("valuation_date", X.DATE, "[valuation]"),
        ("listing_threshold", X.DBL, "0"), ("has_reported_date", X.BOOL, "1=1"), ("has_paid_date", X.BOOL, "1=1"),
        ("has_member_exposure", X.BOOL, "1=1"), ("has_line_detail", X.BOOL, "1=1"),
        ("exposure_by_cohort", X.BOOL, "1=1"), ("date_precision", X.STR, '"DAY"')], label="capability flags")
    meta = wf.select(meta, keep=["source_mode", "valuation_date", "listing_threshold", "has_reported_date",
                                 "has_paid_date", "has_member_exposure", "has_line_detail", "exposure_by_cohort",
                                 "date_precision"], keep_unknown=False, label="int_run_meta")
    wf.output_yxdb(meta, C.stage(v, "int_run_meta"), label="int_run_meta")
    recon(wf, [(craw, "claims_lines", "rows_in", None), (rej, "claims_lines", "rows_rejected_excluded", None),
               (unmatched, "claims_lines", "rows_unmatched_excluded", None), (ic, "int_claims", "rows_out", None),
               (ic, "int_claims", "amount_paid_q", "amount_paid_q"), (ic, "int_claims", "amount_os_q", "amount_os_q"),
               (mraw, "members", "rows_in", None), (int_members, "int_members", "rows_out", None),
               (int_members, "int_members", "written_premium_q", "written_premium_q"),
               (int_exposure, "int_exposure", "earned_ly", "earned_ly"),
               (int_exposure, "int_exposure", "written_premium", "written_premium"),
               (agg, "int_claims_aggregate", "amount_paid", "amount_paid")], v, sn)
    C.finish_checks(wf, checks, v, sn)
    return C.save(wf, v, "01a_adapter_line.yxmd")


# ============================================================================================ claimant mode
def build_claimant(v):
    wf = X.WF("01b_adapter_claimant", "Stage 01b: standalone claimant-mode adapter")
    wf.dag = True
    checks, sn = [], "01b"
    wf.container("01b.1 Run meta and FX")
    valk = run_meta(wf, v, v.claimant_dir, checks, sn)
    FX_CACHE[wf] = fx_table(wf, v)

    wf.container("01b.2 Claimant listing")
    raw, full = read_contract_csv(wf, v.p(f"{v.claimant_dir}/claimants.csv"), "claimants",
                                  ["member_id", "period_start", "period_end", "paid_amount", "os_amount", "currency"],
                                  checks, sn)
    c = wf.record_id(full, "src_row", label="source row")
    c = wf.formula(c, [
        ("c_member", X.STR, "Uppercase(Trim([member_id]))"), ("c_scheme", X.STR, txt("scheme_id")),
        ("ps", X.DATE, dt("period_start")), ("pe", X.DATE, dt("period_end")), ("cm", X.DATE, dt("cohort_month")),
        ("ft", X.DATE, dt("first_treatment_date")), ("pa", X.DBL, num("paid_amount")), ("oa", X.DBL, num("os_amount")),
        ("lim", X.DBL, num("annual_limit")), ("c_ccy", X.STR, "Uppercase(Trim([currency]))"),
        ("c_event", X.STR, txt("event_id")), ("c_site", X.STR, txt("site")), ("c_seg", X.STR, txt("segment", "ALL"))],
        label="type claimants")
    c = wf.select(c, keep=["src_row", "c_member", "c_scheme", "ps", "pe", "cm", "ft", "pa", "oa", "lim", "c_ccy",
                           "c_event", "c_site", "c_seg"], keep_unknown=False, label="typed claimants")
    bad, c = wf.filter(c, "IsNull([ps]) OR IsNull([pe]) OR IsNull([pa]) OR IsNull([oa])", label="A12 claimant row")
    checks.append(C.check_formula(wf, bad, sn, "A12", "STOP", '"claimants row " + ToString([src_row])',
                                  '"blank or non-ISO period date, or blank amount (row excluded)"'))
    cfx = apply_fx(wf, c, "c_ccy", checks, sn, '"claimants currency " + [c_ccy]', "claimants")
    ic = wf.formula(cfx, [
        ("claim_key", X.STR, '[c_member] + "|" + DateTimeFormat([ps], "%Y-%m-%d")'),
        ("claim_id", X.STR, "[claim_key]"), ("member_id", X.STR, "[c_member]"), ("policy_id", X.STR, "Null()"),
        ("scheme_id", X.STR, "[c_scheme]"),
        ("cohort_month", X.DATE, "IIF(IsNull([cm]), " + MS("[ps]") + ", [cm])"),
        ("treatment_date", X.DATE, "IIF(IsNull([ft]), [ps], [ft])"), ("reported_date", X.DATE, "Null()"),
        ("paid_date", X.DATE, "Null()"), ("admission_date", X.DATE, "Null()"), ("discharge_date", X.DATE, "Null()"),
        ("amount_paid", X.DBL, "[pa]"), ("amount_os", X.DBL, "[oa]"),
        ("amount_paid_q", X.DBL, "[pa] * [fx_rate]"), ("amount_os_q", X.DBL, "[oa] * [fx_rate]"),
        ("currency", X.STR, "[c_ccy]"), ("status", X.STR, '"CLAIMANT"'),
        ("annual_limit", X.DBL, "IIF(IsNull([lim]), Null(), [lim] * [fx_rate])"), ("segment", X.STR, "[c_seg]"),
        ("site", X.STR, "[c_site]"), ("event_id", X.STR, "[c_event]"), ("cause_code", X.STR, "Null()"),
        ("grain", X.STR, '"CLAIMANT"'), ("source_mode", X.STR, '"C"'), ("source_file", X.STR, '"claimants.csv"'),
        ("source_row", X.INT64, "[src_row]")], label="int_claims fields")
    ic = wf.select(ic, keep=INT_CLAIMS, keep_unknown=False, label="int_claims")
    wf.output_yxdb(ic, C.stage(v, "int_claims"), label="int_claims")

    wf.container("01b.3 Exposure (monthly, not split by cohort)")
    _, e = read_contract_csv(wf, v.p(f"{v.claimant_dir}/exposure_monthly.csv"), "exposure_monthly",
                             ["month", "lives_inforce", "written_premium", "earned_premium"], checks, sn)
    e = wf.formula(e, [("exposure_month", X.DATE, dt("month")), ("segment_t", X.STR, txt("segment", "ALL")),
                       ("lives_inforce_n", X.DBL, num("lives_inforce")),
                       ("written_ly", X.DBL, 'IIF(IsEmpty([lives_new]), 0, ToNumber([lives_new]))'),
                       ("wp", X.DBL, num("written_premium")), ("ep", X.DBL, num("earned_premium")),
                       ("k", X.INT, "1")], label="type exposure")
    e = wf.select(e, keep=["exposure_month", "segment_t", "lives_inforce_n", "written_ly", "wp", "ep", "k"],
                  keep_unknown=False, label="typed exposure")
    bad_e, e = wf.filter(e, "IsNull([exposure_month]) OR IsNull([lives_inforce_n])", label="A12 exposure row")
    checks.append(C.check_formula(wf, bad_e, sn, "A12", "STOP", '"exposure_monthly"',
                                  '"blank or non-ISO month, or blank lives (row excluded)"'))
    # amounts are in run_meta currency_basis; converted with the FX table
    _, rm = read_contract_csv(wf, v.p(f"{v.claimant_dir}/run_meta.csv"), "run_meta", ["currency_basis"], checks, sn)
    rm = wf.formula(rm, [("kr", X.INT, "1"), ("basis_ccy", X.STR, "Uppercase(Trim([currency_basis]))")],
                    label="currency basis")
    rm = wf.select(rm, keep=["kr", "basis_ccy"], keep_unknown=False, label="basis currency")
    e = wf.join(e, rm, ["k"], ["kr"], label="exposure + basis currency")["J"]
    e = apply_fx(wf, e, "basis_ccy", checks, sn, '"currency_basis " + [basis_ccy]', "exposure")
    e = wf.formula(e, [("segment", X.STR, "[segment_t]"), ("cohort_month", X.DATE, "[exposure_month]"),
                       ("earned_ly", X.DBL, "[lives_inforce_n] / 12"), ("lives_inforce", X.DBL, "[lives_inforce_n]"),
                       ("written_premium", X.DBL, "[wp] * [fx_rate]"), ("earned_premium", X.DBL, "[ep] * [fx_rate]")],
                   label="int_exposure fields")
    ie = wf.select(e, keep=["segment", "cohort_month", "exposure_month", "earned_ly", "written_ly", "earned_premium",
                            "written_premium", "lives_inforce"], keep_unknown=False, label="int_exposure")
    wf.output_yxdb(ie, C.stage(v, "int_exposure"), label="int_exposure")
    a09 = wf.formula(ie, [("k", X.INT, "1"), ("yr", X.INT, "DateTimeYear([exposure_month])")], label="year")
    a09 = wf.summarize(a09, [("yr", "GroupBy", "yr"), ("written_ly", "Sum", "new"), ("earned_ly", "Sum", "ely"),
                             ("exposure_month", "CountDistinct", "months")], label="by year")
    a09, _ = wf.filter(a09, "[months] = 12 AND [ely] > 0 AND ([new] / [ely] < 0.6 OR [new] / [ely] > 1.4)",
                       label="A09 new vs in-force")
    checks.append(C.check_formula(wf, a09, sn, "A09", "FLAG", '"year " + ToString([yr])',
                                  '"lives_new over 12 months vs average in-force outside 0.6-1.4"'))

    wf.container("01b.4 Claims aggregate and completeness")
    _, ag = read_contract_csv(wf, v.p(f"{v.claimant_dir}/claims_aggregate_monthly.csv"), "claims_aggregate_monthly",
                              ["incurred_month", "paid_amount", "os_amount"], checks, sn)
    ag = wf.formula(ag, [("im", X.DATE, dt("incurred_month")), ("seg", X.STR, txt("segment", "ALL")),
                         ("cm", X.DATE, dt("cohort_month")), ("pa", X.DBL, num("paid_amount")),
                         ("oa", X.DBL, num("os_amount")),
                         ("cc", X.DBL, 'IIF(IsEmpty([claim_count]), Null(), ToNumber([claim_count]))'),
                         ("k", X.INT, "1")], label="type aggregate")
    ag = wf.select(ag, keep=["im", "seg", "cm", "pa", "oa", "cc", "k"], keep_unknown=False, label="typed aggregate")
    ag = wf.join(ag, rm, ["k"], ["kr"], label="aggregate + basis currency")["J"]
    ag = apply_fx(wf, ag, "basis_ccy", checks, sn, '"currency_basis " + [basis_ccy]', "aggregate")
    ag = wf.formula(ag, [("segment", X.STR, "[seg]"), ("cohort_month", X.DATE, "IIF(IsNull([cm]), [im], [cm])"),
                         ("incurred_month", X.DATE, "[im]"), ("amount_paid", X.DBL, "[pa] * [fx_rate]"),
                         ("amount_os", X.DBL, "[oa] * [fx_rate]"), ("claim_count", X.DBL, "[cc]")],
                    label="int_claims_aggregate fields")
    iag = wf.select(ag, keep=["segment", "cohort_month", "incurred_month", "amount_paid", "amount_os", "claim_count"],
                    keep_unknown=False, label="int_claims_aggregate")
    wf.output_yxdb(iag, C.stage(v, "int_claims_aggregate"), label="int_claims_aggregate")
    # A08: listing complete above the threshold and not larger than the aggregate
    la = wf.formula(ic, [("k", X.INT, "1"), ("amt", X.DBL, "[amount_paid_q] + [amount_os_q]")], label="listing amount")
    la = wf.summarize(la, [("k", "GroupBy", "k"), ("amt", "Sum", "listing_total"), ("amt", "Min", "listing_min")],
                      label="listing totals")
    aa = wf.formula(iag, [("ka", X.INT, "1"), ("amt", X.DBL, "[amount_paid] + [amount_os]")], label="aggregate amount")
    aa = wf.summarize(aa, [("ka", "GroupBy", "ka"), ("amt", "Sum", "aggregate_total")], label="aggregate total")
    a8 = wf.join(la, aa, ["k"], ["ka"], label="listing vs aggregate")["J"]
    a8 = wf.join(a8, wf.select(valk, keep=["kv", "listing_thr"], keep_unknown=False, label="threshold"),
                 ["k"], ["kv"], label="+ threshold")["J"]
    a8, _ = wf.filter(a8, "[listing_total] > [aggregate_total] + 1 OR "
                          "(!IsNull([listing_thr]) AND [listing_min] < [listing_thr])", label="A08 completeness")
    checks.append(C.check_formula(wf, a8, sn, "A08", "STOP", '"claimants"',
                                  '"listing exceeds the aggregate or holds rows below listing_threshold"'))

    wf.container("01b.5 Run meta, recon, checks")
    meta = wf.formula(valk, [
        ("source_mode", X.STR, '"C"'), ("valuation_date", X.DATE, "[valuation]"),
        ("listing_threshold", X.DBL, "IIF(IsNull([listing_thr]), 0, [listing_thr])"),
        ("has_reported_date", X.BOOL, "1=0"), ("has_paid_date", X.BOOL, "1=0"),
        ("has_member_exposure", X.BOOL, "1=0"), ("has_line_detail", X.BOOL, "1=0"),
        ("exposure_by_cohort", X.BOOL, "1=0"), ("date_precision", X.STR, '"DAY"')], label="capability flags")
    meta = wf.select(meta, keep=["source_mode", "valuation_date", "listing_threshold", "has_reported_date",
                                 "has_paid_date", "has_member_exposure", "has_line_detail", "exposure_by_cohort",
                                 "date_precision"], keep_unknown=False, label="int_run_meta")
    wf.output_yxdb(meta, C.stage(v, "int_run_meta"), label="int_run_meta")
    # no member detail: write an empty, typed int_members so later stages never read a stale line-mode file
    em = wf.text_input(["member_id"], [["-"]], label="int_members schema row")
    em = wf.formula(em, [("policy_id", X.STR, '""'), ("scheme_id", X.STR, '""'), ("policy_start", X.DATE, "Null()"),
                         ("policy_end", X.DATE, "Null()"), ("cover_start", X.DATE, "Null()"),
                         ("cover_end", X.DATE, "Null()"), ("plan", X.STR, '""'), ("annual_limit", X.DBL, "Null()"),
                         ("segment", X.STR, '""'), ("site", X.STR, '""'), ("currency", X.STR, '""'),
                         ("written_premium", X.DBL, "Null()"), ("written_premium_q", X.DBL, "Null()"),
                         ("cohort_month", X.DATE, "Null()"), ("cov_days", X.INT, "Null()")], label="typed fields")
    em, _ = wf.filter(em, "1=0", label="no rows")
    wf.output_yxdb(em, C.stage(v, "int_members"), label="int_members (empty: claimant mode)")
    recon(wf, [(raw, "claimants", "rows_in", None), (ic, "int_claims", "rows_out", None),
               (ic, "int_claims", "amount_paid_q", "amount_paid_q"), (ic, "int_claims", "amount_os_q", "amount_os_q"),
               (ie, "int_exposure", "earned_ly", "earned_ly"), (ie, "int_exposure", "written_premium", "written_premium"),
               (iag, "int_claims_aggregate", "amount_paid", "amount_paid")], v, sn)
    C.finish_checks(wf, checks, v, sn)
    return C.save(wf, v, "01b_adapter_claimant.yxmd")


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build_line(v))
        print("wrote", build_claimant(v))
