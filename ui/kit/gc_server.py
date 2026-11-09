"""GC UI kit server 1.1.1 — the local browser server every GC tool page runs on (UI_STANDARD §2).

Standard library only (openpyxl is imported only when a workbook is read or saved). Python 3.12+. Windows first; runs
on Linux / macOS for tests.

    py serve.py --manifest <ui dir>/<tool>/manifest.json --client <client folder> [--port 8766] [--no-browser]

The page (<ui dir>/<tool>/index.html + <ui dir>/kit) talks to this process on 127.0.0.1 only:

  GET  /                                 the tool page
  GET  /kit/<file>                       the kit (css, js, glossary)
  GET  /page/<file>                      a file of the tool's page folder (manifest, glossary, images)
  GET  /doc/<relative path>              a help document under the client folder (md / html / txt / csv), allow-listed
  GET  /api/manifest                     the manifest as loaded (paths resolved, hidden sections removed)
  GET  /api/state                        server + run state, action states, workbook / file states, plugin state
  GET  /api/log?run=<id>                 tail of a run log (default: the current / last run)
  GET  /api/table?path=&sheet=&limit=    a CSV (or an xlsx sheet) under an allowed root as JSON rows
  GET  /api/workbook?id=                 an editable workbook: sheets and their sizes, lock state
  GET  /api/sheet?id=&sheet=&offset=&limit=&q=   one sheet: header, column help (options, input / check), rows
  GET  /api/glossary                     the merged glossary (kit common + tool)
  GET  /api/plugin/<name>?...            a read-only route the tool plugin defines as api_<name>(tool, query)
  POST /api/run    {action, args}        start a manifest action (a launcher) — one run at a time, stdin closed
  POST /api/stop                         terminate the running action
  POST /api/open   {path | what}         open a file / folder in its Windows application (allow-listed types only)
  POST /api/sheet/save {id, sheet, mtime, edits:[{row, col, value}], appends:[[...]]}   save with backup (§2.4)
  POST /api/copy   {path}                save a timestamped copy of a file next to it (allowed roots)
  POST /api/lenai  {id}                  prepare a LenAI folder: PROMPT.md, ATTACH/, README.md, EXPECTED_RETURN.md

Rules (UI_STANDARD §2.4): binds 127.0.0.1; reads and opens only under the manifest's allowed_roots; never deletes;
writes only the editable workbooks (on Save, after a timestamped backup, refusing when Excel holds the file), the
LenAI folders and its own state files; every run / open / save / prepare is appended to ui_actions.log.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

KIT_VERSION = "1.1.1"
MAX_ROWS = 500
LOG_TAIL = 300
BACKUPS_KEPT = 10
OPENABLE = {".xlsx", ".xlsm", ".csv", ".txt", ".log", ".json", ".md", ".html", ".htm", ".pdf", ".pbip", ".pbix",
            ".parquet", ".docx"}       # never a script or an executable
DOC_TYPES = {".md", ".html", ".htm", ".txt", ".csv", ".json", ".png", ".jpg", ".jpeg", ".svg", ".gif", ".css"}
INPUT_FILLS = {"FFFFF2CC", "00FFF2CC", "FFF2CC"}          # yellow = an input cell (GC workbook convention)
CHECK_FILLS = {"FFC00000", "00C00000", "C00000"}          # red = a check / computed cell
OPTIONAL_FILLS = {"FFDDEBF7", "00DDEBF7", "DDEBF7"}       # blue = an optional input cell (XoL Control convention)


# ------------------------------------------------------------------------------------------------- helpers
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def mtime(p: Path):
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def fmt_t(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else None


def read_csv_rows(p: Path, limit=MAX_ROWS, q: str = ""):
    with open(p, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        try:
            header = next(r)
        except StopIteration:
            return [], [], 0
        rows, n = [], 0
        ql = q.lower()
        for row in r:
            if ql and ql not in " ".join(row).lower():
                continue
            n += 1
            if n <= limit:
                rows.append(row)
    return header, rows, n


def file_in_use(p: Path) -> bool:
    """Excel (or another program) holds the workbook: the owner lock file, or the file cannot be opened for writing."""
    if (p.parent / ("~$" + p.name)).exists():
        return True
    try:
        with open(p, "r+b"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False


def cell_value(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) else v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        return int(v)
    return v


def typed(text, old):
    """A value typed in the page, as the cell should hold it: blank = empty; numbers stay numbers; dates as dates."""
    if text is None:
        return None
    if isinstance(text, (int, float, bool)):
        return text
    s = str(text).strip()
    if s == "":
        return None
    if isinstance(old, (datetime, date)) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            pass
    if re.fullmatch(r"[-+]?\d+", s) and not (len(s) > 1 and s[0] == "0"):
        try:
            return int(s)
        except ValueError:
            pass
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        try:
            return float(s)
        except ValueError:
            pass
    return s


class Tool:
    """A tool as its manifest describes it, bound to one client folder."""

    def __init__(self, manifest: Path, client: Path):
        self.manifest_path = manifest.resolve()
        self.m = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.client = client.resolve()
        self.page_dir = self.manifest_path.parent
        self.ui_dir = self.page_dir.parent
        self.kit_dir = (self.ui_dir / self.m.get("kit", "kit")).resolve()
        self.roots = [self.client / r for r in self.m.get("allowed_roots", ["."])]
        self.state_dir = self.client / self.m.get("state_dir", "ui_state")
        self._plugin = None
        self._meta = {}                 # (workbook path, mtime) -> {sheet: meta}: validations need a full load (slow)
        self._meta_lock = threading.Lock()

    # ---- paths
    def rel(self, p: Path) -> str:
        try:
            return p.resolve().relative_to(self.client).as_posix()
        except ValueError:
            return str(p)

    def path(self, rel: str) -> Path:
        return (self.client / rel.replace("\\", "/")).resolve()

    def allowed(self, p: Path) -> bool:
        try:
            rp = p.resolve()
        except OSError:
            return False
        for r in self.roots:
            try:
                rr = r.resolve()
            except OSError:
                continue
            if rp == rr or rr in rp.parents:
                return True
        return False

    def safe_rel(self, rel: str) -> Path | None:
        if not rel or ".." in rel.replace("\\", "/").split("/") or rel.startswith(("/", "\\")) or ":" in rel:
            return None
        p = self.path(rel)
        return p if self.allowed(p) else None

    # ---- plugin
    def plugin(self):
        if self._plugin is None and self.m.get("plugin"):
            import importlib.util
            path = (self.page_dir / self.m["plugin"]).resolve()
            sp = importlib.util.spec_from_file_location("gc_tool_plugin", path)
            mod = importlib.util.module_from_spec(sp)
            sys.path.insert(0, str(path.parent))
            sp.loader.exec_module(mod)
            self._plugin = mod
        return self._plugin

    # ---- manifest views
    def actions(self) -> dict:
        return self.m.get("actions", {})

    def action(self, aid: str) -> dict:
        if aid not in self.actions():
            raise ValueError(f"unknown action {aid}")
        return self.actions()[aid]

    def workbooks(self) -> dict:
        return self.m.get("editable_workbooks", {})

    def workbook(self, wid: str) -> tuple[dict, Path]:
        if wid not in self.workbooks():
            raise ValueError(f"unknown workbook {wid}")
        w = self.workbooks()[wid]
        p = self.path(w["path"])
        if not self.allowed(p):
            raise ValueError("workbook outside the allowed folders")
        return w, p

    def glossary(self) -> list[dict]:
        out, seen = [], set()
        for f in self.m.get("glossary", []):
            p = (self.kit_dir / f[4:]) if f.startswith("kit/") else (self.page_dir / f)
            if not p.exists():
                continue
            for e in json.loads(p.read_text(encoding="utf-8")):
                key = e["term"].lower()
                if key in seen:
                    out = [x for x in out if x["term"].lower() != key]     # the tool's entry replaces the common one
                seen.add(key)
                out.append(e)
        return sorted(out, key=lambda e: e["term"].lower())

    def file_state(self, rel: str) -> dict:
        p = self.path(rel)
        return {"path": rel, "exists": p.exists(), "mtime": fmt_t(mtime(p)), "size": p.stat().st_size if p.exists() else None,
                "in_use": file_in_use(p) if p.exists() and p.suffix.lower() in (".xlsx", ".xlsm") else False}

    # ---- workbooks (openpyxl)
    def sheet_meta(self, wb, ws, w: dict) -> dict:
        header_row = int(w.get("header_row", 1))
        head = [("" if c.value is None else str(c.value).strip()) for c in ws[header_row]]
        while head and head[-1] == "":
            head.pop()
        ncol = len(head)
        cols = []
        # data validations: list options per column (A1 ranges of the sheet, "A,B,C" literals, or a range elsewhere)
        dv_opts = {}
        try:
            for dv in ws.data_validations.dataValidation:
                if dv.type != "list" or not dv.formula1:
                    continue
                opts = self._dv_options(wb, ws, dv.formula1)
                for rng in str(dv.sqref).split():
                    m = re.match(r"\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?", rng)
                    if not m:
                        continue
                    c1 = _col_index(m.group(1))
                    c2 = _col_index(m.group(3)) if m.group(3) else c1
                    for ci in range(c1, c2 + 1):
                        dv_opts.setdefault(ci, opts)
        except Exception:  # noqa: BLE001 - validations are help, never a reason to fail
            pass
        first = header_row + 1
        for i, name in enumerate(head, 1):
            c = ws.cell(row=first, column=i)
            hc = ws.cell(row=header_row, column=i)
            fill = (c.fill.fgColor.rgb if c.fill is not None and c.fill.fgColor is not None else None)
            fill = fill if isinstance(fill, str) else None
            hfill = (hc.fill.fgColor.rgb if hc.fill is not None and hc.fill.fgColor is not None else None)
            hfill = hfill if isinstance(hfill, str) else None
            formula = isinstance(c.value, str) and c.value.startswith("=")
            # yellow data cell = an input; a red header, a red data cell or a formula = a check / computed column
            kind = "input" if fill in INPUT_FILLS else "optional" if fill in OPTIONAL_FILLS else \
                "check" if (fill in CHECK_FILLS or hfill in CHECK_FILLS or formula) else "text"
            note = ""
            if hc.comment is not None:
                note = str(hc.comment.text or "").strip()
            cols.append({"name": name, "col": i, "kind": kind, "options": dv_opts.get(i, []), "note": note,
                         "locked": name in set(w.get("readonly_columns", [])) or kind == "check"})
        return {"header_row": header_row, "columns": cols, "ncol": ncol}

    def _dv_options(self, wb, ws, formula: str) -> list[str]:
        f = formula.strip()
        if f.startswith('"') and f.endswith('"'):
            return [x.strip() for x in f[1:-1].split(",") if x.strip() != ""]
        m = re.match(r"^=?(?:'?([^'!]+)'?!)?\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)$", f.replace("$", "$"))
        if not m:
            return []
        sh = wb[m.group(1)] if m.group(1) and m.group(1) in wb.sheetnames else ws
        c1, r1, c2, r2 = _col_index(m.group(2)), int(m.group(3)), _col_index(m.group(4)), int(m.group(5))
        out = []
        for row in sh.iter_rows(min_row=r1, max_row=min(r2, r1 + 2000), min_col=c1, max_col=c2, values_only=True):
            for v in row:
                if v is not None and str(v).strip() != "":
                    out.append(str(v))
        return out

    def workbook_meta(self, wid: str) -> dict:
        """{sheet: meta} for an editable workbook, from one full openpyxl load per (path, mtime); cached. A large
        reference sheet (100k rows) makes the full load slow, so rows are read in read-only mode (sheet()) and the
        meta is warmed in the background at start (warm_meta)."""
        from openpyxl import load_workbook
        w, p = self.workbook(wid)
        key = (str(p), mtime(p))
        with self._meta_lock:
            if key in self._meta:
                return self._meta[key]
        wb = load_workbook(p)
        out = {}
        for ws in wb.worksheets:
            try:
                out[ws.title] = self.sheet_meta(wb, ws, w)
            except Exception:  # noqa: BLE001
                out[ws.title] = {"header_row": int(w.get("header_row", 1)), "columns": [], "ncol": 0}
        wb.close()
        with self._meta_lock:
            self._meta[key] = out
            for k in [k for k in self._meta if k[0] == str(p) and k != key]:
                del self._meta[k]
        return out

    def warm_meta(self):
        for wid in self.workbooks():
            try:
                if self.path(self.workbooks()[wid]["path"]).exists():
                    self.workbook_meta(wid)
            except Exception:  # noqa: BLE001
                pass

    def workbook_info(self, wid: str) -> dict:
        from openpyxl import load_workbook
        w, p = self.workbook(wid)
        info = {"id": wid, "title": w.get("title", p.name), "path": self.rel(p), "exists": p.exists(),
                "mtime": fmt_t(mtime(p)), "in_use": False, "sheets": [], "help": w.get("help", "")}
        if not p.exists():
            return info
        info["in_use"] = file_in_use(p)
        wb = load_workbook(p, read_only=True)
        ro = set(w.get("readonly_sheets", []))
        hidden = set(w.get("hidden_sheets", []))
        for ws in wb.worksheets:
            if ws.title in hidden:
                continue
            info["sheets"].append({"name": ws.title, "rows": max((ws.max_row or 1) - int(w.get("header_row", 1)), 0),
                                   "cols": ws.max_column or 0, "readonly": ws.title in ro,
                                   "note": (w.get("sheet_notes") or {}).get(ws.title, "")})
        wb.close()
        return info

    def sheet(self, wid: str, sheet: str, offset=0, limit=MAX_ROWS, q="") -> dict:
        from openpyxl import load_workbook
        w, p = self.workbook(wid)
        if not p.exists():
            raise ValueError("workbook not found")
        metas = self.workbook_meta(wid)
        if sheet not in metas or sheet in set(w.get("hidden_sheets", [])):
            raise ValueError(f"no sheet {sheet}")
        meta = metas[sheet]
        hr, ncol = meta["header_row"], meta["ncol"]
        wb = load_workbook(p, read_only=True, data_only=False)
        ws = wb[sheet]
        rows, n, ql = [], 0, q.lower()
        max_scan = hr + int(w.get("max_rows", 20000))
        r = hr
        for row in ws.iter_rows(min_row=hr + 1, max_row=max_scan, max_col=ncol, values_only=True):
            r += 1
            vals = [cell_value(v) for v in row] + [None] * (ncol - len(row))
            if all(v is None or v == "" for v in vals):
                continue
            if ql and ql not in " ".join("" if v is None else str(v) for v in vals).lower():
                continue
            n += 1
            if n > offset and len(rows) < limit:
                rows.append({"r": r, "v": vals})
        last = ws.max_row
        wb.close()
        out = {"id": wid, "sheet": sheet, "path": self.rel(p), "mtime": fmt_t(mtime(p)), "mtime_ts": mtime(p),
               "in_use": file_in_use(p), "readonly": sheet in set(w.get("readonly_sheets", [])),
               "columns": [dict(c) for c in meta["columns"]], "header_row": hr, "rows": rows, "n_rows": n, "offset": offset,
               "truncated": n > offset + len(rows), "max_row": last}
        pl = self.plugin()
        if pl is not None and hasattr(pl, "sheet_help"):
            try:
                extra = pl.sheet_help(self, wid, sheet, out["columns"]) or {}
                # the tool may refine the columns (description, required, options, example, kind) and add per-row help
                # (rows_help keyed by the value of key_col, applied to value_col: options, description, required …)
                for k in ("columns", "rows_help", "key_col", "value_col", "note"):
                    if k in extra:
                        out[k] = extra[k]
            except Exception as e:  # noqa: BLE001 - help is help, never a reason to fail
                out["help_error"] = f"{type(e).__name__}: {e}"
        return out

    def save_sheet(self, wid: str, sheet: str, seen_ts, edits: list, appends: list, force=False) -> dict:
        from openpyxl import load_workbook
        w, p = self.workbook(wid)
        if not p.exists():
            raise ValueError("workbook not found")
        if sheet in set(w.get("readonly_sheets", [])):
            raise ValueError(f"{sheet} is read-only in the page")
        if file_in_use(p):
            raise RuntimeError(f"{p.name} is open in Excel: close it there, then save again")
        cur = mtime(p)
        if seen_ts and cur and abs(cur - float(seen_ts)) > 1e-6 and not force:
            raise RuntimeError("the workbook changed on disk since the page read it: reload the sheet, then redo the edits")
        metas = self.workbook_meta(wid)
        wb = load_workbook(p)
        if sheet not in wb.sheetnames:
            wb.close()
            raise ValueError(f"no sheet {sheet}")
        ws = wb[sheet]
        meta = metas.get(sheet) or self.sheet_meta(wb, ws, w)
        locked = {c["col"] for c in meta["columns"] if c["locked"]}
        hr, ncol = meta["header_row"], meta["ncol"]
        changed = 0
        for e in edits:
            r, c = int(e["row"]), int(e["col"])
            if r <= hr or c < 1 or c > ncol:
                raise ValueError(f"cell out of range: row {r} col {c}")
            if c in locked:
                raise ValueError(f"column {meta['columns'][c - 1]['name']} is not editable")
            cell = ws.cell(row=r, column=c)
            if isinstance(cell.value, str) and cell.value.startswith("="):
                raise ValueError(f"row {r}, {meta['columns'][c - 1]['name']} holds a formula: not edited from the page")
            cell.value = typed(e.get("value"), cell.value)
            changed += 1
        last = ws.max_row or hr
        for row in appends:
            last += 1
            for c, v in enumerate(row[:ncol], 1):
                if c in locked:
                    continue
                src = ws.cell(row=hr + 1, column=c)        # the first data row's style carries to a new row
                cell = ws.cell(row=last, column=c, value=typed(v, None))
                try:
                    cell._style = src._style  # noqa: SLF001
                except Exception:  # noqa: BLE001
                    pass
            changed += 1
        if not changed:
            wb.close()
            return {"ok": True, "changed": 0, "mtime": fmt_t(cur), "mtime_ts": cur}
        bdir = p.parent / "_ui_backup"
        bdir.mkdir(exist_ok=True)
        bak = bdir / f"{p.stem}_{datetime.now():%Y%m%d_%H%M%S_%f}{p.suffix}"
        shutil.copy2(p, bak)
        olds = sorted(bdir.glob(f"{p.stem}_*{p.suffix}"))
        for o in olds[:-BACKUPS_KEPT]:
            try:
                o.unlink()                     # the backups are the server's own files (§2.4: never a user file)
            except OSError:
                pass
        tmp = p.with_name(p.stem + ".ui_tmp" + p.suffix)
        wb.save(tmp)
        wb.close()
        os.replace(tmp, p)
        with self._meta_lock:                      # the header and validations did not change: keep the meta
            self._meta[(str(p), mtime(p))] = metas
        return {"ok": True, "changed": changed, "backup": self.rel(bak), "mtime": fmt_t(mtime(p)), "mtime_ts": mtime(p)}

    # ---- LenAI folders (D-UI4)
    def lenai(self, lid: str) -> dict:
        assists = self.m.get("lenai", {})
        if lid not in assists:
            raise ValueError(f"unknown assist {lid}")
        a = assists[lid]
        folder = self.path(self.m.get("lenai_dir", "07_LenAI")) / f"{stamp()}_{lid}"
        folder.mkdir(parents=True, exist_ok=False)
        prompt = ""
        if a.get("prompt_file"):
            pf = self.path(a["prompt_file"])
            if pf.exists():
                prompt = pf.read_text(encoding="utf-8", errors="replace")
                if a.get("prompt_section"):
                    prompt = _md_section(prompt, a["prompt_section"]) or prompt
        prompt = prompt or a.get("prompt", "")
        pl = self.plugin()
        if pl is not None and hasattr(pl, "lenai_prompt"):
            prompt = pl.lenai_prompt(self, lid, a, prompt)        # the tool fills paths etc. (D-UI4)
        (folder / "PROMPT.md").write_text(prompt, encoding="utf-8")
        att = folder / "ATTACH"
        att.mkdir()
        copied, missing = [], []
        for rel in a.get("attach", []):
            src = self.path(rel)
            if src.exists() and self.allowed(src):
                if src.is_dir():
                    shutil.copytree(src, att / src.name)
                else:
                    shutil.copy2(src, att / src.name)
                copied.append(src.name)
            else:
                missing.append(rel)
        steps = a.get("steps") or ["Open LenAI and start a new chat.", "Attach every file in ATTACH\\ (drag them in).",
                                    "Paste PROMPT.md as the first message.",
                                    "Save LenAI's return where EXPECTED_RETURN.md says."]
        readme = [f"# {a.get('title', lid)} — LenAI folder", "", f"Prepared {now()} by the {self.m.get('title', 'tool')} page.", "",
                  "## What to do", ""] + [f"{i}. {s}" for i, s in enumerate(steps, 1)] + \
                 ["", "## Attached", ""] + [f"- {c}" for c in copied] + \
                 ([f"", "Missing (not found in the client folder): " + ", ".join(missing)] if missing else []) + \
                 ["", "## Where the return goes", "", a.get("return_to", "See EXPECTED_RETURN.md."), ""]
        (folder / "README.md").write_text("\n".join(readme), encoding="utf-8")
        (folder / "EXPECTED_RETURN.md").write_text(a.get("expected_return", "A Run Digest (see the LenAI encyclopedia)."),
                                                 encoding="utf-8")
        return {"ok": True, "folder": self.rel(folder), "attached": copied, "missing": missing}


def _col_index(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def _md_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    out, on, level = [], False, 0
    for ln in lines:
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            if on and len(m.group(1)) <= level:
                break
            if heading.lower() in m.group(2).lower():
                on, level = True, len(m.group(1))
                out.append(ln)
                continue
        if on:
            out.append(ln)
    return "\n".join(out).strip()


class Runner:
    """At most one action at a time; the state of the current / last run in <state_dir>/ui_run.json."""

    def __init__(self, tool: Tool):
        self.tool = tool
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.current: dict | None = None
        self.history: list[dict] = []

    @property
    def state_path(self) -> Path:
        return self.tool.state_dir / "ui_run.json"

    def load(self) -> dict | None:
        p = self.state_path
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return None

    def save(self, st: dict):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(st, indent=1), encoding="utf-8")
        hp = self.tool.state_dir / "ui_runs.jsonl"
        if st.get("finished"):
            with open(hp, "a", encoding="utf-8") as f:
                f.write(json.dumps({k: st[k] for k in ("id", "action", "args", "started", "finished", "rc", "log")}) + "\n")

    def runs(self, n=50) -> list[dict]:
        hp = self.tool.state_dir / "ui_runs.jsonl"
        if not hp.exists():
            return []
        lines = hp.read_text(encoding="utf-8").splitlines()[-n:]
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except ValueError:
                pass
        return out

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def command(self, a: dict, args: dict) -> list[str]:
        t = self.tool
        subst = {"client": str(t.client), "ui": str(t.ui_dir), "py": sys.executable, "page": str(t.page_dir)}
        key = "command_win" if sys.platform == "win32" and "command_win" in a else \
            "command_posix" if sys.platform != "win32" and "command_posix" in a else "command"
        cmd = [str(x).format(**subst) for x in a[key]]
        spec = a.get("args", {})
        for name, how in spec.items():
            v = args.get(name)
            if isinstance(how, dict) and how.get("positional"):
                # a positional slot in the launcher's fixed order; its "none" marker (default "-") when not given
                if v in (None, "", False):
                    cmd.append(str(how.get("default", "-")))
                else:
                    if how.get("choices") and str(v) not in how["choices"]:
                        raise ValueError(f"{name}: {v} is not one of {how['choices']}")
                    if how.get("pattern") and not re.fullmatch(how["pattern"], str(v)):
                        raise ValueError(f"{name}: {v!r} is not a valid value")
                    cmd.append(str(v))
                continue
            if v in (None, "", False):
                continue
            if isinstance(how, str):
                cmd += [how, str(v)] if how.startswith("-") else [how.format(value=v)]
            elif isinstance(how, dict):
                if how.get("flag") and v is True:
                    cmd.append(how["flag"])
                elif how.get("option"):
                    cmd += [how["option"], str(v)]
                elif how.get("choices"):
                    if str(v) not in how["choices"]:
                        raise ValueError(f"{name}: {v} is not one of {how['choices']}")
                    cmd.append(str(v))
        return cmd

    def start(self, aid: str, args: dict | None) -> dict:
        with self.lock:
            if self.running():
                raise RuntimeError("a run is already in progress")
            t = self.tool
            a = t.action(aid)
            args = args or {}
            cmd = self.command(a, args)
            t.state_dir.mkdir(parents=True, exist_ok=True)
            (t.state_dir / "logs").mkdir(exist_ok=True)
            rid = f"{stamp()}_{aid}"
            log = t.state_dir / "logs" / f"{rid}.log"
            cwd = t.path(a["cwd"]) if a.get("cwd") else t.client
            backups = []
            for rel in a.get("backup_before", []):           # a file the launcher overwrites: keep a copy first (§2.4)
                src = t.path(rel)
                if src.exists() and src.is_file() and t.allowed(src):
                    bdir = src.parent / "_ui_backup"
                    bdir.mkdir(exist_ok=True)
                    bak = bdir / f"{src.stem}_{datetime.now():%Y%m%d_%H%M%S_%f}{src.suffix}"
                    shutil.copy2(src, bak)
                    backups.append(t.rel(bak))
                    for o in sorted(bdir.glob(f"{src.stem}_*{src.suffix}"))[:-BACKUPS_KEPT]:
                        try:
                            o.unlink()
                        except OSError:
                            pass
            st = {"id": rid, "action": aid, "title": a.get("title", aid), "args": args, "command": cmd, "cwd": str(cwd),
                  "started": now(), "started_ts": time.time(), "finished": None, "rc": None,
                  "log": t.rel(log), "watch": a.get("outputs", []), "backups": backups}
            fh = open(log, "w", encoding="utf-8", errors="replace")
            fh.write(f"[{st['started']}] {subprocess.list2cmdline(cmd)}\n")
            fh.flush()
            env = dict(os.environ, GC_UI="1", PYTHONIOENCODING="utf-8")
            try:
                self.proc = subprocess.Popen(cmd, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=fh,
                                             stderr=subprocess.STDOUT, env=env)
            except OSError as e:
                fh.write(f"could not start: {e}\n")
                fh.close()
                raise RuntimeError(f"could not start {aid}: {e}") from e
            st["pid"] = self.proc.pid
            self.current = st
            self.save(st)
            threading.Thread(target=self._wait, args=(fh,), daemon=True).start()
            return st

    def _wait(self, fh):
        rc = self.proc.wait()
        st = self.current
        st["finished"], st["rc"] = now(), rc
        fh.write(f"[{st['finished']}] exit code {rc}\n")
        fh.close()
        self.save(st)

    def stop(self) -> bool:
        with self.lock:
            if not self.running():
                return False
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)
            else:
                self.proc.terminate()
            return True

    def status(self) -> dict | None:
        if self.current:
            st = dict(self.current)
            st["running"] = self.running()
            return st
        st = self.load()
        if st:
            st = dict(st)
            st["running"] = False
        return st


# ------------------------------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    tool: Tool
    runner: Runner
    quiet = True

    def log_message(self, fmt, *args):
        if not self.quiet:
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # ---- replies
    def send_json(self, obj, code=200):
        b = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        if self.headers.get("Origin") == "null":        # a page opened from disk (file://)
            self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, p: Path, inline_md=False):
        if not p.exists() or not p.is_file():
            return self.send_json({"error": "not found"}, 404)
        b = p.read_bytes()
        ctype = mimetypes.guess_type(str(p))[0] or ("text/markdown" if p.suffix == ".md" else "application/octet-stream")
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def audit(self, line):
        try:
            p = self.tool.state_dir / "ui_actions.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"[{now()}] {line}\n")
        except OSError:
            pass

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        t = self.tool
        try:
            if u.path == "/favicon.ico":
                self.send_response(204); self.end_headers(); return
            if u.path in ("/", "/index.html"):
                return self.send_file(t.page_dir / "index.html")
            if u.path.startswith("/kit/"):
                name = u.path[5:]
                if "/" in name or ".." in name:
                    return self.send_json({"error": "bad path"}, 400)
                return self.send_file(t.kit_dir / name)
            if u.path.startswith("/page/"):
                rel = u.path[6:]
                if ".." in rel or rel.startswith("/") or "\\" in rel:
                    return self.send_json({"error": "bad path"}, 400)
                return self.send_file(t.page_dir / rel)
            if u.path.startswith("/doc/"):
                rel = u.path[5:]
                from urllib.parse import unquote
                p = t.safe_rel(unquote(rel))
                if p is None or p.suffix.lower() not in DOC_TYPES:
                    return self.send_json({"error": "not a help document under the allowed folders"}, 404)
                return self.send_file(p)
            if u.path == "/api/manifest":
                return self.send_json(self.manifest_view())
            if u.path == "/api/state":
                return self.send_json(self.state())
            if u.path == "/api/log":
                return self.log(q.get("run"))
            if u.path == "/api/runs":
                return self.send_json({"runs": self.runner.runs()})
            if u.path == "/api/table":
                return self.table(q.get("path", ""), q.get("sheet"), int(q.get("limit", MAX_ROWS)), q.get("q", ""))
            if u.path == "/api/workbook":
                return self.send_json(t.workbook_info(q.get("id", "")))
            if u.path == "/api/sheet":
                return self.send_json(t.sheet(q.get("id", ""), q.get("sheet", ""), int(q.get("offset", 0)),
                                              int(q.get("limit", MAX_ROWS)), q.get("q", "")))
            if u.path == "/api/glossary":
                return self.send_json({"entries": t.glossary()})
            if u.path.startswith("/api/plugin/"):
                name = u.path[len("/api/plugin/"):]
                pl = t.plugin()
                fn = getattr(pl, "api_" + name, None) if pl is not None and re.fullmatch(r"\w+", name) else None
                if fn is None:
                    return self.send_json({"error": f"no plugin route {name}"}, 404)
                return self.send_json(fn(t, q))          # a read-only tool route: GET only, query string as a dict
            return self.send_json({"error": "not found"}, 404)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        except RuntimeError as e:
            return self.send_json({"error": str(e)}, 409)

    def manifest_view(self) -> dict:
        t = self.tool
        m = json.loads(json.dumps(t.m))
        m["_server"] = {"kit": KIT_VERSION, "client": str(t.client), "ui_dir": str(t.ui_dir), "platform": sys.platform}
        return m

    def state(self) -> dict:
        t = self.tool
        run = self.runner.status()
        files = {k: t.file_state(v) for k, v in (t.m.get("watch_files") or {}).items()}
        wbs = {}
        for wid, w in t.workbooks().items():
            p = t.path(w["path"])
            wbs[wid] = {"path": w["path"], "title": w.get("title", p.name), "exists": p.exists(), "mtime": fmt_t(mtime(p)),
                        "mtime_ts": mtime(p), "in_use": file_in_use(p) if p.exists() else False}
        out = {"tool": {"name": t.m["name"], "title": t.m["title"], "version": t.m.get("version", ""), "kit": KIT_VERSION,
                        "client": str(t.client), "client_name": t.client.name},
               "run": run, "runs": self.runner.runs(20), "files": files, "workbooks": wbs, "now": now(),
               "platform": sys.platform}
        pl = t.plugin()
        if pl is not None and hasattr(pl, "state"):
            try:
                out["plugin"] = pl.state(t, self.runner)
            except Exception as e:  # noqa: BLE001 - a plugin error must not take the page down
                out["plugin"] = {"error": f"{type(e).__name__}: {e}"}
        return out

    def log(self, rid):
        st = self.runner.status()
        p = None
        if rid:
            cand = self.tool.state_dir / "logs" / f"{rid}.log"
            if re.fullmatch(r"[\w\-]+", rid) and cand.exists():
                p = cand
        elif st and st.get("log"):
            p = self.tool.path(st["log"])
        lines = []
        if p and p.exists():
            with open(p, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()[-LOG_TAIL:]
        return self.send_json({"lines": lines, "running": bool(st and st.get("running")), "run": st})

    def table(self, rel, sheet, limit, q):
        p = self.tool.safe_rel(rel)
        if p is None or not p.exists():
            return self.send_json({"error": f"no table {rel}"}, 404)
        limit = max(1, min(limit, 5000))
        if p.suffix.lower() == ".csv":
            header, rows, n = read_csv_rows(p, limit, q)
        elif p.suffix.lower() in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(p, read_only=True, data_only=True)
            ws = wb[sheet] if sheet and sheet in wb.sheetnames else wb.worksheets[0]
            it = ws.iter_rows(values_only=True)
            header = [("" if v is None else str(v)) for v in next(it, [])]
            rows, n, ql = [], 0, q.lower()
            for r in it:
                vals = [cell_value(v) for v in r[:len(header)]]
                if all(v is None for v in vals):
                    continue
                if ql and ql not in " ".join("" if v is None else str(v) for v in vals).lower():
                    continue
                n += 1
                if n <= limit:
                    rows.append(["" if v is None else v for v in vals])
            sheets = wb.sheetnames
            wb.close()
            return self.send_json({"path": rel, "sheet": ws.title, "sheets": sheets, "columns": header, "rows": rows,
                                   "n_rows": n, "truncated": n > len(rows), "mtime": fmt_t(mtime(p))})
        else:
            return self.send_json({"error": "not a table"}, 400)
        return self.send_json({"path": rel, "columns": header, "rows": rows, "n_rows": n, "truncated": n > len(rows),
                               "mtime": fmt_t(mtime(p))})

    def do_OPTIONS(self):
        self.send_response(204)
        if self.headers.get("Origin") == "null":
            self.send_header("Access-Control-Allow-Origin", "null")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.end_headers()

    # ---- POST
    def do_POST(self):
        u = urlparse(self.path)
        t = self.tool
        try:
            b = self.body()
            if u.path == "/api/run":
                st = self.runner.start(b.get("action", ""), b.get("args") or {})
                self.audit(f"run {st['action']} {json.dumps(st['args'])} -> {st['log']}")
                return self.send_json({"ok": True, "run": st})
            if u.path == "/api/stop":
                ok = self.runner.stop()
                self.audit("stop requested")
                return self.send_json({"ok": ok})
            if u.path == "/api/open":
                return self.open_(b)
            if u.path == "/api/sheet/save":
                res = t.save_sheet(b.get("id", ""), b.get("sheet", ""), b.get("mtime"), b.get("edits") or [],
                                   b.get("appends") or [], bool(b.get("force")))
                self.audit(f"save {b.get('id')}/{b.get('sheet')}: {res.get('changed')} cells, backup {res.get('backup')}")
                return self.send_json(res)
            if u.path == "/api/copy":
                p = t.safe_rel(b.get("path", ""))
                if p is None or not p.is_file():
                    return self.send_json({"error": "file not found or outside the allowed folders"}, 404)
                dst = p.with_name(f"{p.stem}_{datetime.now():%Y%m%d_%H%M%S}{p.suffix}")
                shutil.copy2(p, dst)
                self.audit(f"copy {t.rel(p)} -> {dst.name}")
                return self.send_json({"ok": True, "path": t.rel(dst)})
            if u.path == "/api/lenai":
                res = t.lenai(b.get("id", ""))
                self.audit(f"lenai {b.get('id')} -> {res['folder']}")
                return self.send_json(res)
            return self.send_json({"error": "not found"}, 404)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)
        except RuntimeError as e:
            return self.send_json({"error": str(e)}, 409)
        except Exception as e:  # noqa: BLE001
            return self.send_json({"error": f"{type(e).__name__}: {e}"}, 500)

    def open_(self, b):
        t = self.tool
        rel = b.get("path", "")
        if b.get("what") == "workbook":
            w, p = t.workbook(b.get("id", ""))
        elif b.get("what") == "state":
            p = t.state_dir
        else:
            p = t.safe_rel(rel)
        if p is None or not p.exists():
            return self.send_json({"error": "file not found or outside the allowed folders"}, 404)
        if p.is_file() and p.suffix.lower() not in OPENABLE:
            return self.send_json({"error": f"{p.suffix} files are not opened from the page"}, 403)
        self.audit(f"open {t.rel(p)}")
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # noqa: S606 - the user's own application for this file type
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                if os.environ.get("GC_UI_TEST"):
                    return self.send_json({"ok": True, "path": t.rel(p), "test": True})
                subprocess.Popen(["xdg-open", str(p)])
        except OSError as e:
            return self.send_json({"error": f"could not open: {e}"}, 500)
        return self.send_json({"ok": True, "path": t.rel(p)})


def serve(manifest: Path, client: Path, port: int, open_browser: bool, quiet=True, ready=None):
    tool = Tool(manifest, client)
    Handler.tool, Handler.runner, Handler.quiet = tool, Runner(tool), quiet
    threading.Thread(target=tool.warm_meta, daemon=True).start()      # validations of the editable workbooks
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError:
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        print(f"port {port} is busy: using {httpd.server_address[1]}", flush=True)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"{tool.m.get('title', 'GC tool')} page (kit {KIT_VERSION}): {url}", flush=True)
    print(f"client folder: {tool.client}   (keep this window open; Ctrl+C or close it to stop)", flush=True)
    if open_browser:
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    if ready is not None:
        ready(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return port


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--client", required=True, help="the client / tool folder the page works on")
    ap.add_argument("--port", type=int, default=8765, help="0 = any free port")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    serve(Path(a.manifest), Path(a.client), a.port, not a.no_browser, quiet=not a.verbose)


if __name__ == "__main__":
    main()
