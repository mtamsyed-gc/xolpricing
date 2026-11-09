"""XoL page plugin (XU-1.0, tool 0.9.1): the tool state the GC UI kit server adds to /api/state, and the Control
workbook help the editor shows (D-UI6). Read-only; standard library + openpyxl (for the Params readiness only).

state(tool, runner) -> per variant present in this installation (synth needs data/synthetic; live always):
  control:   path, exists, mtime, in_use, changed_since_run (saved after the last run started)
  inputs:    the data files each adapter needs (exists, mtime)
  stages:    per stage: status none / ok / flag / stop (+ stale = written before the current run started),
             STOP / FLAG / INFO counts, the check rows (code, severity, item, message), the result tables present
  results:   the Results workbook (exists, mtime, in_use); overall status
  params:    the Params sheet read as the tool reads it: required blank, invalid list values, rows missing from an
             older workbook, the fit_cutoff advice against the active per-person layers (S08-S11)
  run:       the last run's variant / adapter / first / last from the kit's run record (args)
sheet_help(tool, wid, sheet, columns) -> the Control workbook's help from tools/excel/control_spec.py: column
  descriptions, required / optional, options, examples; Params rows: per-parameter help keyed by param_id.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                     # the tool folder (ui/xol/.. /..)
VERSION = "XU-1.0"
MAX_CHECK_ROWS = 400


def fmt_t(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else None


def mt(p: Path):
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def in_use(p: Path) -> bool:
    if not p.exists():
        return False
    if (p.parent / ("~$" + p.name)).exists():
        return True
    try:
        with open(p, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


_SPEC = None


def spec_module(tool):
    global _SPEC
    if _SPEC is None:
        path = tool.client / tool.m["control_spec"]
        sp = importlib.util.spec_from_file_location("control_spec_ui", path)
        mod = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(mod)
        _SPEC = mod
    return _SPEC


KINDS = {"TEXT": "text", "NUMBER": "number", "INT": "whole number", "DATE": "date yyyy-mm-dd", "LIST": "choice", "CSV": "comma list"}
LABEL_COLS = ("description", "allowed_values", "group", "unit", "param_id", "indicated_factor")


def read_checks(p: Path):
    try:
        with open(p, encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            rows = []
            for i, row in enumerate(r):
                if i >= MAX_CHECK_ROWS:
                    break
                rows.append(row)
            return rows
    except OSError:
        return []


# ----------------------------------------------------------------------------------------------------- state
def variants(tool):
    return [k for k, v in tool.m["variants"].items() if not v.get("requires") or (tool.client / v["requires"]).exists()]


def stage_list(tool, adapter):
    return [s for s in tool.m["stages"] if not s.get("adapter") or s["code"] == adapter]


def stage_status(tool, v: dict, s: dict, started_ts):
    od = tool.client / v["output"]
    chk = od / "checks" / s["checks"]
    t = mt(chk)
    info = {"code": s["code"], "name": s["name"], "title": s["title"], "checks_mtime": fmt_t(t), "status": "none",
            "stop": 0, "flag": 0, "info": 0, "checks": [], "tables": [], "stale": False, "checks_path": tool.rel(chk)}
    if t is None:
        return info
    recs = read_checks(chk)
    sev = [r.get("severity", "") for r in recs]
    info["stop"], info["flag"], info["info"] = sev.count("STOP"), sev.count("FLAG"), sev.count("INFO")
    info["checks"] = [{k: r.get(k, "") for k in ("code", "severity", "item", "message")} for r in recs if r.get("code") != "OK"]
    info["status"] = "stop" if info["stop"] else "flag" if info["flag"] else "ok"
    info["stale"] = bool(started_ts and t < started_ts - 1)
    for name in s.get("tables", []):
        p = None
        for sub in ("checks", "results"):
            c = od / sub / (name + ".csv")
            if c.exists():
                p = c
                break
        info["tables"].append({"name": name, "exists": p is not None, "mtime": fmt_t(mt(p)) if p else None, "path": tool.rel(p) if p else None})
    return info


_PARAMS_CACHE = {}


def params(tool, ctl: Path) -> dict:
    """The Params sheet as the tool reads it, with readiness flags and the fit_cutoff advice (cached by mtime)."""
    S = spec_module(tool)
    if not ctl.exists():
        return {"exists": False, "rows": [], "missing_required": [], "invalid": [], "not_in_sheet": []}
    key = (str(ctl), mt(ctl))
    if key in _PARAMS_CACHE:
        return _PARAMS_CACHE[key]
    from openpyxl import load_workbook
    try:
        wb = load_workbook(ctl, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001
        return {"exists": True, "error": f"cannot read: {e}", "rows": [], "missing_required": [], "invalid": [], "not_in_sheet": []}
    if "Params" not in wb.sheetnames:
        wb.close()
        return {"exists": True, "error": "no Params sheet", "rows": [], "missing_required": [], "invalid": [], "not_in_sheet": []}
    it = wb["Params"].iter_rows(values_only=True)
    for _ in range(S.HEADER_ROW - 1):
        next(it, None)
    head = [str(h).strip() if h is not None else "" for h in next(it, [])]
    values = {}
    for r in it:
        d = dict(zip(head, r))
        if d.get("param_id"):
            values[str(d["param_id"]).strip()] = (d.get("value"), d.get("justification"))
    layers = []
    if "Layers" in wb.sheetnames:
        it = wb["Layers"].iter_rows(values_only=True)
        for _ in range(S.HEADER_ROW - 1):
            next(it, None)
        lh = [str(h).strip() if h is not None else "" for h in next(it, [])]
        layers = [dict(zip(lh, r)) for r in it]
    wb.close()
    rows, missing, invalid = [], [], []
    for pid, g, u, k, a, req, desc in S.PARAMS:
        val, just = values.get(pid, (None, None))
        txt = "" if val is None else str(val).strip()
        blank = txt == ""
        bad = bool(a and not blank and txt.upper() not in a.split(",") and k == "LIST")
        if req and blank:
            missing.append(pid)
        if bad:
            invalid.append(pid)
        rows.append({"name": pid, "group": g, "value": txt, "justification": "" if just is None else str(just),
                     "required": bool(req), "blank": blank, "invalid": bad, "in_sheet": pid in values,
                     "options": a.split(",") if a else [], "unit": u, "example": S.PARAM_EXAMPLES.get(pid, ""),
                     "description": desc, "default": S.DEFAULTS.get(pid, "")})
    out = {"exists": True, "rows": rows, "missing_required": missing, "invalid": invalid,
           "not_in_sheet": [pid for pid, *_ in S.PARAMS if pid not in values], "mtime": fmt_t(mt(ctl)),
           "cutoff": cutoff_advice(layers, values)}
    _PARAMS_CACHE.clear()
    _PARAMS_CACHE[key] = out
    return out


def cutoff_advice(layers, values):
    """fit_cutoff against the active per-person layers' attachments, with the below_cutoff mode (0.9.0, S08-S11)."""
    try:
        pp = [l for l in layers if str(l.get("cover") or "").upper() == "PP" and str(l.get("active") or "").strip() in ("1", "1.0")
              and l.get("ap") not in (None, "")]
        if not pp:
            return None
        min_ap = min(float(l["ap"]) for l in pp)
        raw = values.get("fit_cutoff", (None, None))[0]
        u = float(raw) if raw not in (None, "") else min_ap
        mode = str(values.get("below_cutoff", (None, None))[0] or "STOP").strip().upper() or "STOP"
        below = [{"label": l.get("label") or f"PP option {l.get('option_id')}", "ap": float(l["ap"]), "option_id": l.get("option_id")}
                 for l in pp if float(l["ap"]) < u]
        return {"u": u, "min_ap": min_ap, "mode": mode, "layers_below": below, "cutoff_set": raw not in (None, "")}
    except (TypeError, ValueError):
        return None


def state(tool, runner) -> dict:
    out = {"plugin": VERSION, "variants": variants(tool), "adapters": tool.m["adapters"], "books": {}}
    run = runner.status() if runner else None
    run_args = (run or {}).get("args") or {}
    run_variant = run_args.get("variant")
    for vid in out["variants"]:
        v = tool.m["variants"][vid]
        ctl = tool.client / v["control"]
        res = tool.client / v["output"] / tool.m["results_name"]
        started = run.get("started_ts") if run and run_variant == vid else None
        ctl_m = mt(ctl)
        inputs = []
        for grp in v.get("inputs", []):
            d = tool.client / grp["dir"]
            for f in grp["files"]:
                p = d / f
                inputs.append({"mode": grp["mode"], "label": grp["label"], "file": f, "path": tool.rel(p), "exists": p.exists(), "mtime": fmt_t(mt(p))})
        stages = {}
        for s in tool.m["stages"]:
            stages[s["code"]] = stage_status(tool, v, s, started)
        ran = [s for s in stages.values() if s["status"] != "none" and not s["stale"]]
        overall = "none"
        if run and run.get("running") and run_variant == vid:
            overall = "running"
        elif ran:
            overall = "stop" if any(s["status"] == "stop" for s in ran) else "flag" if any(s["status"] == "flag" for s in ran) else "ok"
        pr = params(tool, ctl)
        out["books"][vid] = {
            "label": v["label"],
            "control": {"path": tool.rel(ctl), "exists": ctl.exists(), "mtime": fmt_t(ctl_m), "in_use": in_use(ctl),
                        "changed_since_run": bool(run and run_variant == vid and ctl_m and run.get("started_ts") and ctl_m > run["started_ts"] + 0.5),
                        "workbook_id": v.get("workbook_id")},
            "inputs": inputs, "stages": stages, "overall": overall,
            "results": {"path": tool.rel(res), "exists": res.exists(), "mtime": fmt_t(mt(res)), "in_use": in_use(res)},
            "output": v["output"],
            "params": {k: pr.get(k) for k in ("exists", "error", "missing_required", "invalid", "not_in_sheet", "mtime", "cutoff")} | {"n": len(pr.get("rows", []))},
            "params_rows": pr.get("rows", []),
        }
    out["run"] = {"variant": run_variant, "adapter": run_args.get("adapter"), "first": run_args.get("first"), "last": run_args.get("last")} if run else None
    out["template"] = {"path": tool.m["control_template"], "exists": (tool.client / tool.m["control_template"]).exists()}
    out["now"] = fmt_t(time.time())
    return out


# ----------------------------------------------------------------------------------------------------- editor help
def sheet_help(tool, wid, sheet, columns):
    """Control workbook help from control_spec: columns (description, required, options, example) and, on the Params
    sheet, per-row help keyed by param_id applied to the value column."""
    S = spec_module(tool)
    if sheet not in S.TABLES:
        return {}
    title, note, cols = S.TABLES[sheet]
    by_name = {name: (k, a, req) for name, k, a, req in cols}
    for c in columns:
        if c["name"] in by_name:
            k, a, req = by_name[c["name"]]
            d, ex = S.COLUMN_HELP.get(sheet, {}).get(c["name"], ("", ""))
            c["description"] = d or c.get("note") or ""
            c["example"] = ex
            c["required"] = bool(req) and c["name"] not in LABEL_COLS
            if not c.get("options") and k == "LIST" and a:
                c["options"] = a.split(",")
            if c["kind"] == "text" and c["name"] not in LABEL_COLS and sheet != "Params":
                c["kind"] = "input" if c["required"] else "optional"
            if c["name"] in LABEL_COLS and sheet == "Params":
                c["locked"] = c["name"] != "param_id" and c["name"] in ("description", "allowed_values", "group", "unit")
    out = {"columns": columns, "note": f"{title}. {note}"}
    if sheet == "Params":
        key = next((c["col"] for c in columns if c["name"] == "param_id"), None)
        val = next((c["col"] for c in columns if c["name"] == "value"), None)
        if key and val:
            out["key_col"], out["value_col"] = key, val
            out["rows_help"] = {pid: {"description": desc, "required": bool(req), "options": a.split(",") if a else [], "unit": u,
                                      "example": S.PARAM_EXAMPLES.get(pid, ""), "default": S.DEFAULTS.get(pid, ""), "kind": KINDS.get(k, k), "group": g}
                                for pid, g, u, k, a, req, desc in S.PARAMS}
    return out


def spec(tool) -> dict:
    """The input guide: every Control sheet and column (for the page's Input guide card)."""
    S = spec_module(tool)
    sheets = []
    params_rows = [{"name": pid, "group": g, "required": bool(req), "kind": KINDS.get(k, k), "unit": "" if u == "date" else u,
                    "options": a.split(",") if a else [], "example": S.PARAM_EXAMPLES.get(pid, ""), "description": d,
                    "default": S.DEFAULTS.get(pid, "")} for pid, g, u, k, a, req, d in S.PARAMS]
    sheets.append({"sheet": "Params", "title": S.TABLES["Params"][0], "note": S.TABLES["Params"][1], "inputs": params_rows})
    for sh in S.SHEETS:
        if sh == "Params":
            continue
        title, note, cols = S.TABLES[sh]
        ins = []
        for name, k, a, req in cols:
            d, ex = S.COLUMN_HELP.get(sh, {}).get(name, ("", ""))
            ins.append({"name": name, "required": bool(req) and name not in LABEL_COLS, "label": name in LABEL_COLS,
                        "kind": KINDS.get(k, k), "options": a.split(",") if (k in ("LIST", "INT") and "," in a) else [],
                        "example": ex, "description": d})
        sheets.append({"sheet": sh, "title": title, "note": note, "inputs": ins})
    return {"sheets": sheets, "header_row": S.HEADER_ROW}


def api_spec(tool, query):
    """GET /api/plugin/spec — the Input guide (kit route wrapper around spec())."""
    return spec(tool)
