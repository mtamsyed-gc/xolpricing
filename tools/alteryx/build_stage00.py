"""Stage 00 - read and validate the Control workbook.

Outputs (staging .yxdb):
    ctl_params      one row, every parameter typed (TEXT/LIST/CSV as text, NUMBER Double, INT Int64, DATE Date)
    ctl_<sheet>     each Control table, typed and with blank rows removed
    checks_00.csv   check records (STOP stops the run)

Checks:
    P01 required parameter blank (STOP)          P02 value not of its kind (STOP)
    P03 value not in the allowed list (STOP)     P04 parameter row missing from the sheet (STOP)
    P05 unknown parameter row (FLAG)
    T01 FX rate missing / not > 0 (STOP)         T02 trend year missing for an experience year or the quote year (STOP)
    T03 custom weights used and do not sum to 1 (STOP)
    T04 completion factor < 1 (FLAG)             T05 completion row for a year outside years_included (FLAG)
    L01 active PP/EV layer without AP > 0 (STOP) L02 AMOUNT layer without limit > 0 (STOP)
    L03 active SL layer incomplete (STOP)        L04 duplicate cover x option x layer (STOP)
    L05 no active layer (STOP)
"""
from __future__ import annotations

import xol_common as C  # sets sys.path for control_spec
import control_spec as S
from xol_common import X

KIND_RE = {"NUMBER": r"-?\d+(\.\d+)?([eE]-?\d+)?", "INT": r"-?\d+", "DATE": r"\d{4}-\d{2}-\d{2}",
           "CSV": r"[^,]+(,[^,]+)*", "TEXT": r".+", "LIST": r".+"}


TYPE_OF = {"NUMBER": X.DBL, "INT": X.INT64, "DATE": X.DATE, "TEXT": X.STR, "LIST": X.STR, "CSV": X.STR}


def typed_table(wf, sheet: str, v):
    """Read one Control sheet, drop blank rows, cast every column to its kind with a Select."""
    cols = S.TABLES[sheet][2]
    t = wf.input_xlsx(v.p(v.control), sheet, header_row=S.HEADER_ROW, label=f"Control: {sheet}")
    keep, _ = wf.filter(t, f"!IsNull([{cols[0][0]}])", label=f"{sheet}: non-blank rows")
    names = [c for c, *_ in cols]
    return wf.select(keep, keep=names, types={c: TYPE_OF[k] for c, k, *_ in cols}, keep_unknown=False,
                     label=f"{sheet}: typed columns")


def build(v):
    wf = X.WF("00_read_control", "Stage 00: read and validate the Control workbook")
    wf.dag = True
    checks = []

    # ------------------------------------------------------------------ Params
    wf.container("00.1 Params: read, check against the spec, pivot to one typed row")
    raw = wf.input_xlsx(v.p(v.control), "Params", header_row=S.HEADER_ROW, label="Control: Params")
    rows, _ = wf.filter(raw, "!IsEmpty([param_id])", label="parameter rows")
    rows = wf.formula(rows, [("pid", X.STR, "Trim([param_id])"),
                             ("val", X.STR, 'IIF(IsNull([value]), "", Trim([value]))')], label="trim id and value")
    rows = wf.select(rows, keep=["pid", "val", "justification"], keep_unknown=False, label="id, value, justification")
    spec = wf.text_input(["s_pid", "s_kind", "s_allowed", "s_required"],
                         [[p[0], p[3], p[4] or "-", 1 if p[5] else 0] for p in S.PARAMS], label="parameter spec")
    j = wf.join(spec, rows, ["s_pid"], ["pid"], label="spec x sheet")
    m = j["J"]
    re_expr = " ".join(f'IIF([s_kind] = "{k}", REGEX_Match([val], "{r}"),' for k, r in KIND_RE.items()) + " 1=0" + ")" * len(KIND_RE)
    m = wf.formula(m, [
        ("blank", X.BOOL, 'IsEmpty([val])'),
        ("kind_ok", X.BOOL, f'IIF(IsEmpty([val]), 1=1, {re_expr})'),
        ("allowed_ok", X.BOOL, 'IIF(IsEmpty([val]) OR [s_allowed] = "-", 1=1, '
                               'FindString("," + [s_allowed] + ",", "," + [val] + ",") >= 0)'),
    ], label="check value")
    p01, _ = wf.filter(m, "[blank] AND [s_required] = 1", label="P01 required blank")
    checks.append(C.check_formula(wf, p01, "00", "P01", "STOP", "[s_pid]", '"required parameter is blank"'))
    p02, _ = wf.filter(m, "![kind_ok]", label="P02 bad kind")
    checks.append(C.check_formula(wf, p02, "00", "P02", "STOP", "[s_pid]",
                                  '"value \'" + [val] + "\' is not a valid " + [s_kind]'))
    p03, _ = wf.filter(m, "![allowed_ok]", label="P03 not allowed")
    checks.append(C.check_formula(wf, p03, "00", "P03", "STOP", "[s_pid]",
                                  '"value \'" + [val] + "\' not in " + [s_allowed]'))
    checks.append(C.check_formula(wf, j["L"], "00", "P04", "STOP", "[s_pid]", '"parameter row missing from Params"'))
    checks.append(C.check_formula(wf, j["R"], "00", "P05", "FLAG", "[pid]", '"unknown parameter row (ignored)"'))

    good, _ = wf.filter(m, "[kind_ok] AND [allowed_ok]", label="valid values")
    good = wf.formula(good, [("k", X.INT, "1")], label="pivot key")
    piv = wf.formula(good, [(f"p_{p[0]}", X.STR, f'IIF([s_pid] = "{p[0]}" AND ![blank], [val], Null())')
                            for p in S.PARAMS], label="one column per parameter")
    wide = wf.summarize(piv, [("k", "GroupBy", "k")] + [(f"p_{p[0]}", "Max", f"p_{p[0]}") for p in S.PARAMS],
                        label="pivot to one row")
    typed = []
    for pid, _g, _u, kind, *_r in S.PARAMS:
        src = f"[p_{pid}]"
        if kind == "NUMBER":
            typed.append((pid, X.DBL, f"IIF(IsNull({src}), Null(), ToNumber({src}))"))
        elif kind == "INT":
            typed.append((pid, X.INT64, f"IIF(IsNull({src}), Null(), ToNumber({src}))"))
        elif kind == "DATE":
            typed.append((pid, X.DATE, f"IIF(IsNull({src}), Null(), ToDate({src}))"))
        else:
            typed.append((pid, X.STR, src))
    params = wf.formula(wide, typed, label="type each parameter")
    params = wf.select(params, keep=[p[0] for p in S.PARAMS], keep_unknown=False, label="typed parameters")
    wf.output_yxdb(params, C.stage(v, "ctl_params"), label="ctl_params")

    # ------------------------------------------------------------------ tables
    tables = {}
    for sheet in S.SHEETS:
        if sheet == "Params":
            continue
        wf.container(f"00.2 {sheet}: read and type")
        t = typed_table(wf, sheet, v)
        tables[sheet] = t
        wf.output_yxdb(t, C.stage(v, f"ctl_{sheet.lower()}"), label=f"ctl_{sheet.lower()}")

    # ------------------------------------------------------------------ table checks
    wf.container("00.3 Table checks")
    fx_bad, _ = wf.filter(tables["FX"], "IsNull([priced_rate_to_quote_ccy]) OR [priced_rate_to_quote_ccy] <= 0",
                          label="T01 FX rate")
    checks.append(C.check_formula(wf, fx_bad, "00", "T01", "STOP", "[currency]", '"priced rate missing or not > 0"'))

    # experience years + quote year must have a trend row
    pk = wf.formula(params, [("k", X.INT, "1"),
                             ("yrs", X.STR, 'IIF(IsNull([years_included]), "", [years_included]) + '
                                            'IIF(IsNull([quote_year]), "", "," + ToString([quote_year]))')],
                    label="years needing trend")
    yrs = wf.generate_rows(pk, "i", X.INT, "0", "[i] < 6", "[i] + 1", label="up to 6 years")
    yrs = wf.formula(yrs, [("yr_s", X.STR,
                            'REGEX_Replace([yrs], "^(?:[^,]*,){" + ToString([i]) + "}([^,]*).*$", "$1")'),
                           ("n_commas", X.INT, 'Length([yrs]) - Length(Replace([yrs], ",", ""))')],
                     label="i-th year")
    yrs, _ = wf.filter(yrs, '[i] <= [n_commas] AND REGEX_Match(Trim([yr_s]), "\\d{4}")', label="existing positions")
    yrs = wf.formula(yrs, [("need_year", X.INT64, "ToNumber(Trim([yr_s]))")], label="year as number")
    yrs = wf.select(yrs, keep=["need_year"], keep_unknown=False, label="needed years")
    tr = wf.select(tables["Trend"], keep=["year"], rename={"year": "trend_year"}, keep_unknown=False,
                   label="trend years")
    jt = wf.join(yrs, tr, ["need_year"], ["trend_year"], label="needed vs trend years")
    checks.append(C.check_formula(wf, jt["L"], "00", "T02", "STOP", "ToString([need_year])",
                                  '"no Trend row for this year"'))

    w = wf.formula(tables["Weights"], [("k", X.INT, "1"),
                                       ("cw", X.DBL, "IIF(IsNull([custom_weight]), 0, [custom_weight])"),
                                       ("custom", X.INT, "IIF([use_default] = 0, 1, 0)")], label="weights")
    ws = wf.summarize(w, [("k", "GroupBy", "k"), ("cw", "Sum", "sum_w"), ("custom", "Sum", "n_custom")],
                      label="weight totals")
    wbad, _ = wf.filter(ws, "[n_custom] > 0 AND ([sum_w] < 0.9999 OR [sum_w] > 1.0001)", label="T03 weights sum")
    checks.append(C.check_formula(wf, wbad, "00", "T03", "STOP", '"Weights"',
                                  '"custom weights sum to " + ToString([sum_w], 4) + ", not 1"'))

    cbad, _ = wf.filter(tables["Completion"], "!IsNull([selected_factor]) AND [selected_factor] < 1",
                        label="T04 factor < 1")
    checks.append(C.check_formula(wf, cbad, "00", "T04", "FLAG",
                                  'ToString([year]) + " " + [component] + " " + [side]',
                                  '"selected factor below 1 (negative development): confirm"'))
    cy = wf.select(tables["Completion"], keep=["year", "component", "side"], keep_unknown=False,
                   label="completion years")
    jc = wf.join(cy, yrs, ["year"], ["need_year"], label="completion vs experience years")
    cu, _ = wf.unique(jc["L"], ["year"], label="one per year")
    checks.append(C.check_formula(wf, cu, "00", "T05", "FLAG", "ToString([year])",
                                  '"completion rows for a year outside years_included (ignored)"'))

    lay, _ = wf.filter(tables["Layers"], "[active] = 1", label="active layers")
    lb = wf.formula(lay, [("lim_basis", X.STR, 'IIF(IsNull([limit_basis]), "AMOUNT", [limit_basis])'),
                          ("tag", X.STR, '[cover] + " option " + ToString([option_id]) + " layer " + ToString([layer])')],
                    label="layer tag")
    xs, sl = wf.filter(lb, '[cover] != "SL"', label="PP/EV vs SL")
    l1, _ = wf.filter(xs, "IsNull([ap]) OR [ap] <= 0", label="L01 AP")
    checks.append(C.check_formula(wf, l1, "00", "L01", "STOP", "[tag]", '"AP missing or not > 0"'))
    l2, _ = wf.filter(xs, '[lim_basis] = "AMOUNT" AND (IsNull([limit]) OR [limit] <= 0)', label="L02 limit")
    checks.append(C.check_formula(wf, l2, "00", "L02", "STOP", "[tag]", '"AMOUNT layer without a limit > 0"'))
    l3, _ = wf.filter(sl, "IsNull([sl_measure]) OR IsNull([sl_ap]) OR IsNull([sl_limit]) OR IsNull([sl_claims_basis])",
                      label="L03 SL fields")
    checks.append(C.check_formula(wf, l3, "00", "L03", "STOP", "[tag]",
                                  '"stop loss needs sl_claims_basis, sl_measure, sl_ap, sl_limit"'))
    _, dup = wf.unique(lb, ["cover", "option_id", "layer"], label="duplicates")
    checks.append(C.check_formula(wf, dup, "00", "L04", "STOP", "[tag]", '"duplicate cover x option x layer"'))
    lk = wf.formula(lb, [("k", X.INT, "1")], label="count key")
    hdr = wf.text_input(["k", "zero"], [[1, 0]], label="zero row")
    cnt = wf.union([lk, hdr], label="active layers + zero row", differ="Ignore")
    cnt = wf.summarize(cnt, [("k", "GroupBy", "k"), ("option_id", "CountNonNull", "n_active")], label="count active")
    l5, _ = wf.filter(cnt, "[n_active] = 0", label="L05 none active")
    checks.append(C.check_formula(wf, l5, "00", "L05", "STOP", '"Layers"', '"no active layer"'))

    wf.container("00.4 Check log")
    C.finish_checks(wf, checks, v, "00")
    return C.save(wf, v, "00_read_control.yxmd")


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build(v))
