"""Shared conventions for the XoL workflow builders.

Paths are relative to the workflow file (Designer resolves relative paths from the workflow's folder):
    LIVE   alteryx/workflows/NN_name.yxmd        reads data/input/,     writes data/output/live/
    SYNTH  alteryx/workflows/synth/NN_name.yxmd  reads data/synthetic/, writes data/output/synth/
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/alteryx"))
sys.path.insert(0, str(ROOT / "tools/excel"))

import xmlkit as X  # noqa: E402,F401


@dataclass(frozen=True)
class Variant:
    name: str            # LIVE | SYNTH
    wf_dir: Path         # where the .yxmd is written
    up: str              # relative path from wf_dir to the repo root (Windows separators)
    control: str         # Control workbook, relative to repo root
    line_dir: str        # standalone line-mode files
    claimant_dir: str    # standalone claimant-mode files
    out_dir: str         # staging + results

    def p(self, rel: str) -> str:
        return self.up + rel.replace("/", "\\")


LIVE = Variant("LIVE", ROOT / "alteryx/workflows", "..\\..\\", "data/input/XoL_Control.xlsx",
               "data/input/line", "data/input/claimant", "data/output/live")
SYNTH = Variant("SYNTH", ROOT / "alteryx/workflows/synth", "..\\..\\..\\", "data/synthetic/XoL_Control_synthetic.xlsx",
                "data/synthetic/line", "data/synthetic/claimant", "data/output/synth")
VARIANTS = (LIVE, SYNTH)


def stage(v: Variant, name: str) -> str:
    """Staging .yxdb written by one stage and read by later ones."""
    return v.p(f"{v.out_dir}/stage/{name}.yxdb")


def checks_csv(v: Variant, stage_no: str) -> str:
    return v.p(f"{v.out_dir}/checks/checks_{stage_no}.csv")


CHECK_FIELDS = ["stage", "code", "severity", "item", "message"]


def check_formula(wf, src, stage_no: str, code: str, severity: str, item_expr: str, msg_expr: str, label=""):
    """Turn the rows of src (already filtered to breaches) into check records."""
    t = wf.formula(src, [("stage", X.STR, X.lit("S" + stage_no)), ("code", X.STR, X.lit(code)),
                         ("severity", X.STR, X.lit(severity)), ("item", X.STR, item_expr),
                         ("message", X.STR, msg_expr)], label=label or f"{code} record")
    return wf.select(t, keep=CHECK_FIELDS, keep_unknown=False, label=f"{code} fields")


def finish_checks(wf, check_srcs: list, v: Variant, stage_no: str):
    """Union all check records with a header row (so the file always has the schema), write the CSV and raise a
    Designer Error for every STOP (the workflow's Cancel-on-error setting then stops the run)."""
    hdr = wf.text_input(CHECK_FIELDS, [["S" + stage_no, "OK", "INFO", "-", f"stage {stage_no} checks ran"]],
                        label="check header row")
    allc = wf.union([hdr] + check_srcs, label="all checks", differ="Ignore")
    wf.output_csv(allc, checks_csv(v, stage_no), label=f"checks_{stage_no}.csv")
    stop, _ = wf.filter(allc, '[severity] = "STOP"', label="STOP checks")
    wf.message(stop, '"[" + [code] + "] " + [item] + ": " + [message]', kind="Error", label="stop the run")
    return allc


def save(wf, v: Variant, filename: str) -> Path:
    v.wf_dir.mkdir(parents=True, exist_ok=True)
    p = v.wf_dir / filename
    p.write_text(wf.xml(), encoding="utf-8")
    return p


# --------------------------------------------------------------------------------------------- shared stream helpers
def params_stream(wf, v, names, key="kp", label="parameters"):
    """ctl_params (one row) reduced to `names`, each renamed p_<name>, plus a constant join key."""
    t = wf.input_yxdb(stage(v, "ctl_params"), label="ctl_params (stage 00)")
    t = wf.formula(t, [(key, X.INT, "1")], label=f"{label}: key")
    return wf.select(t, keep=[key] + names, rename={n: f"p_{n}" for n in names}, keep_unknown=False, label=label)


def meta_stream(wf, v, names, key="km", label="run meta"):
    t = wf.input_yxdb(stage(v, "int_run_meta"), label="int_run_meta (stage 01)")
    t = wf.formula(t, [(key, X.INT, "1")], label=f"{label}: key")
    return wf.select(t, keep=[key] + names, rename={n: f"m_{n}" for n in names}, keep_unknown=False, label=label)


def append(wf, src, const, src_key, const_key, label):
    """Append a one-row stream to every record of src (inner join on constant keys)."""
    return wf.join(src, const, [src_key], [const_key], label=label)["J"]


def ty_of(date_expr):
    """Treaty year containing a date; [ts_md] = treaty start month-day ("%m-%d") must be on the stream."""
    return f'DateTimeYear({date_expr}) - IIF(DateTimeFormat({date_expr}, "%m-%d") < [ts_md], 1, 0)'


def ty_start(ty_expr):
    """Treaty-year start date; [ts_mm] = treaty start month ("%m") must be on the stream (month-start treaties)."""
    return f'ToDate(ToString({ty_expr}) + "-" + [ts_mm] + "-01")'


def in_csv(csv_field, value_expr):
    """value (as text) is one of the comma-separated values in csv_field."""
    return f'FindString("," + {csv_field} + ",", "," + {value_expr} + ",") >= 0'


def years_stream(wf, src, csv_field, out="need_year", label="years"):
    """One record per year listed in a comma-separated text field (up to 8)."""
    y = wf.generate_rows(src, "i", X.INT, "0", "[i] < 8", "[i] + 1", label=f"{label}: positions")
    y = wf.formula(y, [("yr_s", X.STR, f'REGEX_Replace({csv_field}, "^(?:[^,]*,){{" + ToString([i]) + "}}([^,]*).*$", "$1")'),
                       ("n_commas", X.INT, f'Length({csv_field}) - Length(Replace({csv_field}, ",", ""))')],
                   label=f"{label}: i-th value")
    y, _ = wf.filter(y, '[i] <= [n_commas] AND REGEX_Match(Trim([yr_s]), "\\d{4}")', label=f"{label}: valid")
    y = wf.formula(y, [(out, X.INT64, "ToNumber(Trim([yr_s]))")], label=f"{label}: number")
    return wf.select(y, keep=[out], keep_unknown=False, label=label)


def recon_rows(wf, parts, v, stage_no):
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
