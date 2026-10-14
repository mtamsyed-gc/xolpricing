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
