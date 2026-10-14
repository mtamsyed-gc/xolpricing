"""Stage 07 - Results workbook: every out_* table into <out_dir>/XoL_Results.xlsx (docs/data-contracts.md §5).

The workbook must exist (copy excel/templates/XoL_Results.xlsx there once): its presentation sheets (Inputs, Pricing,
Reinsurance Structures, Cedant Impact, Expiring, Scenarios) hold the formulas; this stage replaces only the data
sheets (Overwrite Sheet (Drop)), one sheet at a time through a Block Until Done chain.

Also builds: out_run_log (tool version, scenario, data mode, every parameter with its justification), out_warnings
(checks of every stage), out_recon, out_expiring and out_asif_by_year (expiring structure: rate trended by the matched
option's leveraged trend; as-if recoveries vs premium at the expiring rate by treaty year, actual recoveries parsed
from recoveries_by_year "2023:120000; 2024:0"), and scn_summary (one block of rows per scenario_id: earlier scenarios
kept, this scenario's rows replaced).
"""
from __future__ import annotations

import xol_common as C
from xol_common import X
import control_spec as S
import results_spec as R

WORKBOOK = "XoL_Results.xlsx"
CHK = ["00", "01", "02", "03", "04", "05", "06"]
REC = ["01", "02", "03", "04", "05", "06"]


def book(v):
    return v.p(f"{v.out_dir}/{WORKBOOK}")


def build(v):
    wf = X.WF("07_results", "Stage 07: write the Results workbook data sheets (run log, warnings, results, scenarios)")
    wf.dag = True
    checks, sn = [], "07"
    src = {}

    # ------------------------------------------------------------------ run log
    wf.container("07.1 Run log, parameters, loadings")
    prm = wf.input_yxdb(C.stage(v, "ctl_params"), label="ctl_params (stage 00)")
    src["out_params"] = prm
    src["out_loadings"] = wf.input_yxdb(C.stage(v, "ctl_loadings"), label="ctl_loadings (stage 00)")
    raw = wf.input_xlsx(v.p(v.control), "Params", header_row=S.HEADER_ROW, label="Control: Params (with justifications)")
    raw, _ = wf.filter(raw, "!IsEmpty([param_id])", label="parameter rows")
    raw = wf.formula(raw, [("item", X.STR, "Trim([param_id])"),
                           ("value_t", X.STR, 'IIF(IsNull([value]), "", Trim([value]))'),
                           ("just_t", X.STR, 'IIF(IsNull([justification]), "", [justification])')], label="text")
    raw = wf.select(raw, keep=["item", "value_t", "just_t"], rename={"value_t": "value", "just_t": "justification"},
                    keep_unknown=False, label="parameter log rows")
    meta = wf.input_yxdb(C.stage(v, "int_run_meta"), label="int_run_meta (stage 01)")
    sc = wf.input_yxdb(C.stage(v, "ctl_scenarios"), label="ctl_scenarios (stage 00)")
    pk = wf.formula(prm, [("kq", X.INT, "1"), ("sid", X.INT64, "IIF(IsNull([scenario_id]), 0, [scenario_id])")],
                    label="scenario id")
    scn = wf.join(wf.select(pk, keep=["kq", "sid"], keep_unknown=False, label="sid"),
                  wf.select(sc, keep=["scenario_id", "scenario_name"], keep_unknown=False, label="names"),
                  ["sid"], ["scenario_id"], label="scenario name")["J"]
    scn = wf.summarize(scn, [("kq", "GroupBy", "kn"), ("scenario_name", "First", "s_name")], label="one name")
    run = wf.left_join(wf.select(pk, keep=["kq", "sid"], keep_unknown=False, label="sid"), scn, ["kq"], ["kn"],
                       label="+ scenario name")
    run = wf.join(run, wf.formula(meta, [("km", X.INT, "1")], label="key"), ["kq"], ["km"], label="+ run meta")["J"]
    run = wf.formula(run, [
        ("run_at", X.STR, 'DateTimeFormat(DateTimeNow(), "%Y-%m-%d %H:%M")'),
        ("scen_name", X.STR, 'IIF([sid] = 0, "Best estimate", IIF(IsNull([s_name]), "Scenario " + ToString([sid]), '
                             '[s_name]))')], label="run time, scenario")
    rows = [("tool_version", X.lit(C.VERSION)), ("run_at", "[run_at]"), ("scenario_name", "[scen_name]"), ("source_mode", "[source_mode]"),
            ("valuation_date", 'DateTimeFormat([valuation_date], "%Y-%m-%d")'),
            ("listing_threshold", "ToString([listing_threshold], 0)"), ("date_precision", "[date_precision]")]
    rows += [(f, f'IIF([{f}], "yes", "no")') for f in ("has_reported_date", "has_paid_date", "has_member_exposure",
                                                        "has_line_detail", "exposure_by_cohort")]
    parts = []
    for i, (item, ex) in enumerate(rows):
        r = wf.formula(run, [("item", X.STR, X.lit(item)), ("value", X.STR, ex), ("justification", X.STR, '""')],
                       label=f"log: {item}")
        parts.append(wf.select(r, keep=["item", "value", "justification"], keep_unknown=False, label=f"log row {i}"))
    src["out_run_log"] = wf.union(parts + [raw], label="run log")

    # ------------------------------------------------------------------ warnings and recon
    wf.container("07.2 Warnings and recon of every stage")
    src["out_warnings"] = wf.union([wf.input_yxdb(C.stage(v, f"chk_{n}"), label=f"chk_{n}") for n in CHK],
                                   label="all checks")
    src["out_recon"] = wf.union([wf.input_yxdb(C.stage(v, f"rec_{n}"), label=f"rec_{n}") for n in REC],
                                label="all recon")
    for name in R.SHEETS:
        if name.startswith("out_") and name not in src and name not in ("out_expiring", "out_asif_by_year"):
            src[name] = wf.input_yxdb(C.stage(v, f"res_{name}"), label=f"res_{name}")

    # ------------------------------------------------------------------ expiring
    wf.container("07.3 Expiring structure: premium at the expiring rate, trended; as-if by treaty year")
    ex = wf.input_yxdb(C.stage(v, "ctl_expiring"), label="ctl_expiring (stage 00)")
    ex = wf.formula(ex, [("k_ap", X.STR, 'IIF(IsNull([ap]), "-", ToString([ap], 0))'),
                         ("k_lim", X.STR, 'IIF(IsNull([limit]), "BL", ToString([limit], 0))'),
                         ("k_lay", X.INT64, "IIF(IsNull([layer]), 1, [layer])"), ("ke", X.INT, "1")],
                    label="match keys")
    bs = wf.formula(src["out_bc_summary"], [("m_ap", X.STR, "ToString([ap], 0)"),
                                            ("m_lim", X.STR, 'IIF(IsNull([limit]), "BL", ToString([limit], 0))'),
                                            ("m_cover", X.STR, '"PP"')], label="option keys")
    bs = wf.select(bs, keep=["m_cover", "layer", "m_ap", "m_lim", "option_id", "lev_trend", "base_trend"],
                   rename={"layer": "m_layer", "option_id": "matched_option"}, keep_unknown=False, label="PP terms")
    ex = wf.left_join(ex, bs, ["cover", "k_lay", "k_ap", "k_lim"], ["m_cover", "m_layer", "m_ap", "m_lim"],
                      label="+ matching option (cover, layer, AP, limit)")
    lv = wf.input_yxdb(C.stage(v, "bc_lives"), label="bc_lives (stage 03)")
    lv = wf.formula(lv, [("kl", X.INT, "1")], label="key")
    ex = C.append(wf, ex, wf.select(lv, keep=["kl", "lives_quote_year"], keep_unknown=False, label="lives"), "ke", "kl",
                  "+ lives")
    ex = C.append(wf, ex, wf.select(wf.formula(prm, [("kpp", X.INT, "1")], label="key"),
                                    keep=["kpp", "epi_quote_year", "quote_year"], keep_unknown=False, label="EPI"),
                  "ke", "kpp", "+ EPI")
    trend_used = "IIF(IsNull([lev_trend]), IIF(IsNull([base_trend]), 0, [base_trend]), [lev_trend])"
    ex = wf.formula(ex, [
        ("lev_trend_used", X.DBL, trend_used), ("epi", X.DBL, "[epi_quote_year]"),
        ("expiring_premium_q", X.DBL, 'IIF([rate_basis] = "PPPY", [rate] * [lives_quote_year], '
                                      'IIF([rate_basis] = "PCT_EPI", [rate] * [epi_quote_year], '
                                      'IIF(IsNull([premium]), [rate], [premium])))'),
        ("expiring_premium_trended", X.DBL, "[expiring_premium_q] * (1 + [lev_trend_used])")], label="expiring premium")
    src["out_expiring"] = ex
    by = wf.select(src["out_bc_by_year"], keep=["option_id", "layer", "ty", "exposure_ly_full", "exposure_prem_full",
                                                "prem_trend_f", "xl_aa"], rename={"option_id": "b_opt", "layer": "b_lay"},
                   keep_unknown=False, label="as-if by year")
    ai = wf.join(ex, by, ["matched_option", "k_lay"], ["b_opt", "b_lay"], label="expiring x matched option years")["J"]
    yr = 'ToString([ty])'
    ai = wf.formula(ai, [
        # both at the quote-year level: the layer loss is trended to the quote year (stage 03), so the premium is the
        # expiring rate trended one year (as expiring_premium_trended) on that year's exposure; PCT_EPI on the year's
        # premium on-levelled as in bc_pct_prem; FLAT scaled by the year's exposure / quote-year lives
        ("asif_recovery", X.DBL, "[xl_aa]"),
        ("asif_premium", X.DBL, 'IIF([rate_basis] = "PPPY", [rate] * (1 + [lev_trend_used]) * [exposure_ly_full], '
                                'IIF([rate_basis] = "PCT_EPI", [rate] * [exposure_prem_full] * [prem_trend_f], '
                                'IIF(IsNull([premium]), [rate], [premium]) * (1 + [lev_trend_used]) * [exposure_ly_full] '
                                '/ [lives_quote_year]))'),
        # the same premium at the treaty year's own level, for the actual (untrended) recoveries
        ("premium_year_level", X.DBL, 'IIF([rate_basis] = "PCT_EPI", [rate] * [exposure_prem_full], '
                                      '[asif_premium] / Pow(1 + [lev_trend_used], [quote_year] - [ty]))'),
        ("rby", X.STR, 'IIF(IsNull([recoveries_by_year]), "", ";" + Replace([recoveries_by_year], ",", ";"))'),
        ("actual_recovery", X.DBL, f'IIF(REGEX_Match([rby], ".*;\\s*" + {yr} + "\\s*:\\s*-?[0-9.]+.*"), '
                                   f'ToNumber(REGEX_Replace([rby], ".*;\\s*" + {yr} + "\\s*:\\s*(-?[0-9.]+).*", "$1")), '
                                   'Null())')], label="as-if premium, actual recoveries")
    src["out_asif_by_year"] = wf.sort(ai, [("matched_option", "Ascending"), ("ty", "Ascending")], label="order")

    # ------------------------------------------------------------------ scenarios
    wf.container("07.4 Scenario summary: this scenario's rows replace its earlier rows; other scenarios are kept")
    el_pp = wf.summarize(src["out_sim_summary"], [("option_id", "GroupBy", "o"), ("expected_loss_blended", "Sum", "elb")],
                         label="PP expected loss by option")
    el_pp = wf.formula(el_pp, [("e_key", X.STR, '"1 PP " + ToString([o])')], label="key")
    el_ev = wf.summarize(src["out_ev_sim_summary"], [("option_id", "GroupBy", "o"),
                                                     ("expected_loss_blended", "Sum", "elb")],
                         label="EV expected loss by option")
    el_ev = wf.formula(el_ev, [("e_key", X.STR, '"3 EV " + ToString([o])')], label="key")
    elx = wf.select(wf.union([el_pp, el_ev], label="expected losses"), keep=["e_key", "elb"], keep_unknown=False,
                    label="blended by structure")
    sn_ = wf.left_join(src["out_struct_summary"], elx, ["struct_key"], ["e_key"], label="+ blended expected loss")
    sn_ = wf.formula(sn_, [("ks", X.INT, "1")], label="key")
    sn_ = C.append(wf, sn_, wf.select(run, keep=["kq", "sid", "scen_name", "run_at"], keep_unknown=False,
                                      label="scenario"), "ks", "kq", "+ scenario")
    sn_ = wf.formula(sn_, [("scenario_id", X.DBL, "[sid]"), ("scenario_name", X.STR, "[scen_name]"),
                           ("expected_loss_blended", X.DBL, "[elb]")], label="scenario fields")
    cols = R.SHEETS["scn_summary"]
    numeric = {"scenario_id", "budget", "mean_recovery", "sd_recovery", "mean_net", "sd_net", "expected_loss_blended"}
    new = wf.select(sn_, keep=cols, keep_unknown=False, label="this scenario's rows")
    old = wf.input_xlsx(book(v), "scn_summary", header_row=1, label="Results workbook: scn_summary (earlier runs)")
    old = wf.union([old, wf.text_input(cols, [], label="scn_summary columns")], label="+ columns", differ="Ignore")
    old = wf.select(old, keep=cols, types={c: (X.DBL if c in numeric else X.STR) for c in cols}, keep_unknown=False,
                    label="earlier rows, typed")
    old = C.append(wf, wf.formula(old, [("kz", X.INT, "1")], label="key"),
                   wf.select(run, keep=["kq", "sid"], keep_unknown=False, label="sid"), "kz", "kq", "+ this scenario")
    old, _ = wf.filter(old, "[scenario_id] != [sid]", label="other scenarios")
    old = wf.select(old, keep=cols, keep_unknown=False, label="other scenarios' rows")
    sm = wf.union([old, new], label="all scenarios")
    src["scn_summary"] = wf.sort(sm, [("scenario_id", "Ascending"), ("struct_key", "Ascending")], label="order")

    # ------------------------------------------------------------------ write
    wf.container(f"07.5 Write the data sheets of {WORKBOOK}, one after another (Block Until Done)")
    items = [(name, src[name], cols) for name, cols in R.SHEETS.items()]
    parts = []
    for k, (sheet, s_, cols) in enumerate(items):
        x = wf.record_id(s_, "__rn", label=f"{sheet}: row order")
        x = wf.select(x, keep=["__rn"] + cols, rename={c: f"s{k}_{c}" for c in cols}, keep_unknown=False,
                      label=f"{sheet}: columns prefixed s{k}_")
        parts.append(wf.formula(x, [("__sheet", X.INT, str(k))], label=f"sheet {k}: {sheet}"))
    cur = wf.union(parts, label="every sheet, one stream", differ="Ignore")
    k = 0
    while k < len(items):
        b = wf.block_until_done(cur, label=f"one sheet at a time ({k + 1}-{min(k + 2, len(items))})")
        for anchor in ("Output", "Output2"):
            if k < len(items):
                sheet, _, cols = items[k]
                f, _ = wf.filter((b, anchor), f"[__sheet] = {k}", label=sheet)
                f = wf.select(wf.sort(f, [("__rn", "Ascending")], label=f"{sheet}: order"), keep=[f"s{k}_{c}" for c in cols],
                              rename={f"s{k}_{c}": c for c in cols}, keep_unknown=False, label=f"{sheet}: column names")
                wf.output_xlsx(f, book(v), sheet, "Overwrite", label=f"{WORKBOOK} | {sheet}")
                k += 1
        cur = (b, "Output3")

    # ------------------------------------------------------------------ checks
    wf.container("07.6 Checks")
    e1, _ = wf.filter(ex, "IsNull([matched_option]) AND [cover] = \"PP\"", label="R01 no match")
    checks.append(C.check_formula(wf, e1, sn, "R01", "INFO", '"Expiring " + [cover] + " layer " + ToString([k_lay])',
                                  '"no active option with the same AP and limit: comparison by rate only"'))
    C.finish_checks(wf, checks, v, sn)
    return C.save(wf, v, "07_results.yxmd")


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build(v))
