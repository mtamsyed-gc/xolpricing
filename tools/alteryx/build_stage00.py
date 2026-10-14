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
    X01 scenario_id set but no Scenarios rows (STOP)   X02 target column not a numeric column of the table (STOP)
    X03 ADD / MULT value not a number, or MULT <= 0 (STOP)   X04 override matched no row (FLAG)
    E01 EventRule: not exactly one row (STOP)          E02 EV scenario without lives / cost > 0 (STOP)

Scenario overrides (Params scenario_id > 0): each Scenarios row of that id changes one numeric column of the rows of
its target table that match target_key (ALL, or column=value): SET replaces, then MULT multiplies, then ADD adds.
Params values are overridden before validation. checks/out_scenario_applied.csv lists rows matched per override.
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


NUM_RE = KIND_RE["NUMBER"]
TARGETS = next(a for c, _k, a, *_ in S.TABLES["Scenarios"][2] if c == "target_table").split(",")


def numeric_cols(sheet):
    return [(c, k) for c, k, *_ in S.TABLES[sheet][2] if k in ("NUMBER", "INT")]


def key_cols(sheet):
    return [(c, k) for c, k, *_ in S.TABLES[sheet][2] if k in ("TEXT", "LIST", "INT")]


def num_text(x):
    """A number as Params text: integers without decimals."""
    return f"IIF({x} = Round({x}, 1), ToString({x}, 0), ToString({x}, 10))"


def overrides_stream(wf, sc, rows):
    """Scenarios rows of the run's scenario_id (read from the raw Params rows), parsed."""
    sid = wf.filter(rows, '[pid] = "scenario_id"', label="scenario_id row")[0]
    sid = wf.formula(sid, [("ks", X.INT, "1"), ("sid", X.INT64,
                                                'IIF(REGEX_Match([val], "-?\\d+"), ToNumber([val]), 0)')],
                     label="scenario id")
    z = wf.text_input(["ks", "sid"], [[1, 0]], label="default scenario 0")
    sid = wf.summarize(wf.union([wf.select(sid, keep=["ks", "sid"], keep_unknown=False, label="sid"), z],
                                label="sid + default"), [("ks", "GroupBy", "ks"), ("sid", "Max", "sid")],
                       label="run scenario")
    ov = wf.record_id(sc, "ov_id", label="override id")
    ov = wf.formula(ov, [("ko", X.INT, "1")], label="key")
    ov = wf.join(ov, sid, ["ko"], ["ks"], label="+ run scenario")["J"]
    ov, _ = wf.filter(ov, "[sid] > 0 AND [scenario_id] = [sid]", label="this scenario's overrides")
    ov = wf.formula(ov, [
        ("k_all", X.BOOL, 'Uppercase(Trim([target_key])) = "ALL"'),
        ("k_col", X.STR, 'Uppercase(Trim(REGEX_Replace([target_key], "^([^=]*)=.*$", "$1")))'),
        ("k_val", X.STR, 'Trim(REGEX_Replace([target_key], "^[^=]*=(.*)$", "$1"))'),
        ("op", X.STR, "Uppercase(Trim([operation]))"), ("col", X.STR, "Trim([target_column])"),
        ("num_ok", X.BOOL, f'REGEX_Match(Trim([override_value]), "{NUM_RE}")'),
        ("v", X.DBL, f'IIF(REGEX_Match(Trim([override_value]), "{NUM_RE}"), ToNumber(Trim([override_value])), Null())'),
        ("lv", X.DBL, f'IIF(REGEX_Match(Trim([override_value]), "{NUM_RE}") AND '
                      f'ToNumber(Trim([override_value])) > 0, Log(ToNumber(Trim([override_value]))), 0)')],
        label="parse key, operation, value")
    return sid, ov


def override_terms(applies):
    return [("o_set", X.DBL, f'IIF({applies} AND [op] = "SET", [v], Null())'),
            ("o_add", X.DBL, f'IIF({applies} AND [op] = "ADD", [v], 0)'),
            ("o_mul", X.DBL, f'IIF({applies} AND [op] = "MULT", [lv], 0)'),
            ("o_n", X.DBL, f"IIF({applies}, 1, 0)")]


def apply_table(wf, t, sheet, ov, matched):
    """Apply the overrides of one Control table; `matched` collects (ov_id, rows matched) streams."""
    names = [c for c, *_ in S.TABLES[sheet][2]]
    t = wf.record_id(t, "rid", label=f"{sheet}: row id")
    o, _ = wf.filter(ov, f'[target_table] = "{sheet}"', label=f"{sheet}: overrides")
    x = wf.join(wf.formula(t, [("kt", X.INT, "1")], label="key"),
                wf.select(o, keep=["ko", "ov_id", "k_all", "k_col", "k_val", "op", "col", "v", "lv"],
                          keep_unknown=False, label="override terms"), ["kt"], ["ko"], label=f"{sheet}: rows x overrides")["J"]
    keym = " OR ".join(f'([k_col] = "{c.upper()}" AND ' + (f"ToString([{c}])" if k == "INT" else f"[{c}]")
                       + " = [k_val])" for c, k in key_cols(sheet))
    x = wf.formula(x, [("row_hit", X.BOOL, f"[k_all] OR {keym}")], label=f"{sheet}: key match")
    m = wf.summarize(wf.formula(x, [("hit", X.DBL, "IIF([row_hit], 1, 0)")], label="hit"),
                     [("ov_id", "GroupBy", "ov_id"), ("hit", "Sum", "n_rows")], label=f"{sheet}: rows per override")
    matched.append(m)
    terms, aggs = [], [("rid", "GroupBy", "o_rid")]
    for c, _k in numeric_cols(sheet):
        app = f'([row_hit] AND [col] = "{c}")'
        for nm, ty, ex in override_terms(app):
            terms.append((f"{nm}_{c}", ty, ex))
        aggs += [(f"o_set_{c}", "Max", f"s_{c}"), (f"o_add_{c}", "Sum", f"a_{c}"), (f"o_mul_{c}", "Sum", f"m_{c}"),
                 (f"o_n_{c}", "Sum", f"n_{c}")]
    x = wf.formula(x, terms, label=f"{sheet}: override terms by column")
    a = wf.summarize(x, aggs, label=f"{sheet}: terms per row")
    t = wf.left_join(t, a, ["rid"], ["o_rid"], label=f"{sheet}: + terms")
    new = []
    for c, k in numeric_cols(sheet):
        base = f"IIF(!IsNull([s_{c}]), [s_{c}], IIF(IsNull([{c}]), 0, [{c}]))"
        val = f"IIF(IsNull([s_{c}]) AND IsNull([{c}]) AND [a_{c}] = 0, Null(), {base} * Exp([m_{c}]) + [a_{c}])"
        if k == "INT":
            val = f"Round({val}, 1)"
        new.append((f"o_{c}", X.DBL, f"IIF(IsNull([n_{c}]) OR [n_{c}] = 0, [{c}], {val})"))
    t = wf.formula(t, new, label=f"{sheet}: overridden values")
    t = wf.sort(t, [("rid", "Ascending")], label=f"{sheet}: original order")
    num = {c: k for c, k in numeric_cols(sheet)}
    keep = [f"o_{c}" if c in num else c for c in names]
    return wf.select(t, keep=keep, rename={f"o_{c}": c for c in num},
                     types={f"o_{c}": TYPE_OF[k] for c, k in num.items()}, keep_unknown=False,
                     label=f"{sheet}: scenario applied")


def apply_params(wf, rows, ov, matched):
    """Params overrides act on the text values before validation (target_column value, key param_id=<id>)."""
    r = wf.record_id(rows, "prid", label="param row id")
    o, _ = wf.filter(ov, '[target_table] = "Params"', label="Params overrides")
    x = wf.join(wf.formula(r, [("kt", X.INT, "1")], label="key"),
                wf.select(o, keep=["ko", "ov_id", "k_all", "k_col", "k_val", "op", "col", "v", "lv", "override_value"],
                          keep_unknown=False, label="override terms"), ["kt"], ["ko"], label="params x overrides")["J"]
    x = wf.formula(x, [("row_hit", X.BOOL, '[k_all] OR ([k_col] = "PARAM_ID" AND [k_val] = [pid])')],
                   label="param key match")
    matched.append(wf.summarize(wf.formula(x, [("hit", X.DBL, "IIF([row_hit], 1, 0)")], label="hit"),
                                [("ov_id", "GroupBy", "ov_id"), ("hit", "Sum", "n_rows")], label="params per override"))
    app = '([row_hit] AND [col] = "value")'
    x = wf.formula(x, override_terms(app) + [("o_txt", X.STR, f'IIF({app} AND [op] = "SET", Trim([override_value]), Null())')],
                   label="override terms")
    a = wf.summarize(x, [("prid", "GroupBy", "o_prid"), ("o_txt", "Max", "s_txt"), ("o_add", "Sum", "a_v"),
                         ("o_mul", "Sum", "m_v"), ("o_n", "Sum", "n_v")], label="terms per param")
    r = wf.left_join(r, a, ["prid"], ["o_prid"], label="params + terms")
    numeric = (f'(IIF(!IsNull([s_txt]), ToNumber([s_txt]), ToNumber([val])) * Exp([m_v]) + [a_v])')
    r = wf.formula(r, [("o_val", X.STR, f'IIF(IsNull([n_v]) OR [n_v] = 0, [val], IIF([a_v] = 0 AND [m_v] = 0, '
                                        f'IIF(IsNull([s_txt]), [val], [s_txt]), {num_text(numeric)}))')],
                   label="overridden value")
    r = wf.sort(r, [("prid", "Ascending")], label="original order")
    return wf.select(r, keep=["pid", "o_val", "justification"], rename={"o_val": "val"}, keep_unknown=False,
                     label="id, value, justification (scenario applied)")


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
    sc_raw = typed_table(wf, "Scenarios", v)
    sid, ov = overrides_stream(wf, sc_raw, rows)
    matched = []
    rows = apply_params(wf, rows, ov, matched)
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
        t = sc_raw if sheet == "Scenarios" else typed_table(wf, sheet, v)
        if sheet in TARGETS:
            t = apply_table(wf, t, sheet, ov, matched)
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

    # ------------------------------------------------------------------ scenario checks
    wf.container("00.4 Scenario overrides: checks and what was applied")
    nov = wf.summarize(wf.union([wf.select(wf.formula(ov, [("n", X.DBL, "1")], label="count"), keep=["ko", "n"],
                                           keep_unknown=False, label="n"),
                                 wf.text_input(["ko", "n"], [[1, 0]], label="zero")], label="overrides + zero"),
                       [("ko", "GroupBy", "kn"), ("n", "Sum", "n_ov")], label="overrides in this scenario")
    x1 = wf.join(sid, nov, ["ks"], ["kn"], label="scenario + count")["J"]
    x1, _ = wf.filter(x1, "[sid] > 0 AND [n_ov] = 0", label="X01 no rows")
    checks.append(C.check_formula(wf, x1, "00", "X01", "STOP", '"scenario " + ToString([sid])',
                                  '"scenario_id has no rows in Scenarios"'))
    valid = " OR ".join(f'([target_table] = "{t}" AND ' + C.in_csv(X.lit(",".join(c for c, _ in numeric_cols(t))),
                                                                   "[col]") + ")"
                        for t in TARGETS if t != "Params")
    x2, _ = wf.filter(ov, f'!(({valid}) OR ([target_table] = "Params" AND [col] = "value"))', label="X02 column")
    checks.append(C.check_formula(wf, x2, "00", "X02", "STOP", '[target_table] + "." + [col]',
                                  '"not a numeric column of the table (Params: value)"'))
    x3, _ = wf.filter(ov, '([op] = "ADD" OR [op] = "MULT") AND (![num_ok] OR ([op] = "MULT" AND [v] <= 0)) OR '
                          '([op] != "SET" AND [op] != "ADD" AND [op] != "MULT") OR '
                          '([op] = "SET" AND [target_table] != "Params" AND ![num_ok])', label="X03 value")
    checks.append(C.check_formula(wf, x3, "00", "X03", "STOP", '"scenario row " + ToString([ov_id])',
                                  '"operation must be SET / ADD / MULT with a number (MULT > 0)"'))
    mt = wf.summarize(wf.union(matched, label="rows matched"), [("ov_id", "GroupBy", "m_ov"), ("n_rows", "Sum", "n_rows")],
                      label="rows matched per override")
    ap_ = wf.left_join(ov, mt, ["ov_id"], ["m_ov"], label="overrides + rows matched")
    ap_ = wf.formula(ap_, [("rows_matched", X.DBL, "IIF(IsNull([n_rows]), 0, [n_rows])")], label="rows matched")
    x4, _ = wf.filter(ap_, "[rows_matched] = 0", label="X04 no match")
    checks.append(C.check_formula(wf, x4, "00", "X04", "FLAG", '"scenario row " + ToString([ov_id])',
                                  '"override matched no row (check target_key)"'))
    apo = wf.select(wf.sort(ap_, [("ov_id", "Ascending")], label="order"),
                    keep=["scenario_id", "scenario_name", "target_table", "target_key", "target_column", "operation",
                          "override_value", "rows_matched"], keep_unknown=False, label="out_scenario_applied fields")
    wf.output_csv(apo, v.p(f"{v.out_dir}/checks/out_scenario_applied.csv"), label="out_scenario_applied.csv")
    er = wf.summarize(wf.union([wf.formula(tables["EventRule"], [("k", X.INT, "1"), ("n", X.DBL, "1")], label="n"),
                                wf.text_input(["k", "n"], [[1, 0]], label="zero")], label="rule rows + zero",
                               differ="Ignore"), [("k", "GroupBy", "k"), ("n", "Sum", "n_rules")], label="rule rows")
    e1, _ = wf.filter(er, "[n_rules] != 1", label="E01 one rule")
    checks.append(C.check_formula(wf, e1, "00", "E01", "STOP", '"EventRule"', '"exactly one EventRule row needed"'))
    e2, _ = wf.filter(tables["EvScenarios"], "IsNull([lives]) OR [lives] <= 0 OR IsNull([cost_per_life]) OR "
                                             "[cost_per_life] <= 0", label="E02 scenario")
    checks.append(C.check_formula(wf, e2, "00", "E02", "STOP", "[scenario_name]",
                                  '"EV scenario needs lives > 0 and cost_per_life > 0"'))

    wf.container("00.5 Check log")
    C.finish_checks(wf, checks, v, "00")
    return C.save(wf, v, "00_read_control.yxmd")


if __name__ == "__main__":
    for v in C.VARIANTS:
        print("wrote", build(v))
