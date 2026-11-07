"""Local browser UI server for a GC pricing tool (0.8.0). Standard library only; Python 3.12+.

    py tools\\ui\\serve.py                     serve ui/xol (default manifest), open the browser
    py tools\\ui\\serve.py --port 8765 --no-browser
    py tools\\ui\\serve.py --manifest ui/xol/manifest.json

The page (ui/<tool>/index.html + ui/kit) talks to this process on 127.0.0.1 only. The server:
  GET  /api/state?variant=&adapter=   tool, variant, adapter, workbook mtimes, input files, per-stage status, last run
  GET  /api/table?variant=&name=      a checks/ or results/ CSV as JSON rows (first MAX_ROWS rows)
  GET  /api/log?variant=              tail of the current / last run log
  POST /api/run    {variant, adapter, first, last}  start tools/run_python.py in the background (one run at a time)
  POST /api/stop                      terminate the running pipeline
  POST /api/open   {variant, what|path}  open a workbook / CSV in its Windows application (os.startfile)
  POST /api/copy   {variant}          save a timestamped copy of the Results workbook next to it

Allow-list: the server reads and opens files only under the repo folder, and only under the roots the manifest names
(data/, excel/templates/). It never deletes. Every open and copy is appended to data/output/<variant>/ui_actions.log.
Run state (route, arguments, started, finished, return code, Control mtime at start) is kept in
data/output/<variant>/ui_run.json so a reopened page shows the last run.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

VERSION = "0.8.0"
ROOT = Path(__file__).resolve().parents[2]
MAX_ROWS = 500
LOG_TAIL = 200


# ------------------------------------------------------------------------------------------------- helpers
def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mtime(p: Path):
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def fmt_t(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S") if t else None


def read_csv_rows(p: Path, limit=MAX_ROWS):
    with open(p, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        try:
            header = next(r)
        except StopIteration:
            return [], [], 0
        rows, n = [], 0
        for row in r:
            n += 1
            if n <= limit:
                rows.append(row)
    return header, rows, n


class Tool:
    """A tool as its manifest describes it: variants, adapters, stages and their files."""

    def __init__(self, manifest: Path):
        self.manifest_path = manifest
        self.m = json.loads(manifest.read_text(encoding="utf-8"))
        self.roots = [ROOT / r for r in self.m.get("allowed_roots", ["data", "excel/templates"])]
        self.runner = ROOT / self.m["runner"]
        self.page_dir = manifest.parent
        self.kit_dir = ROOT / self.m.get("kit", "ui/kit")

    # ---- paths
    def variant(self, v: str) -> dict:
        if v not in self.m["variants"]:
            raise ValueError(f"unknown variant {v}")
        return self.m["variants"][v]

    def out_dir(self, v: str) -> Path:
        return ROOT / self.variant(v)["output"]

    def control(self, v: str) -> Path:
        return ROOT / self.variant(v)["control"]

    def results(self, v: str) -> Path:
        return self.out_dir(v) / self.m["results_name"]

    def allowed(self, p: Path) -> bool:
        try:
            rp = p.resolve()
        except OSError:
            return False
        return any(rp == r.resolve() or r.resolve() in rp.parents for r in self.roots)

    def table_path(self, v: str, name: str) -> Path | None:
        if not name.replace("_", "").replace(".", "").isalnum() or ".." in name:
            return None
        name = name if name.endswith(".csv") else name + ".csv"
        for sub in ("checks", "results"):
            p = self.out_dir(v) / sub / name
            if p.exists() and self.allowed(p):
                return p
        return None

    # ---- state
    def stage_list(self, adapter: str) -> list[dict]:
        out = []
        for s in self.m["stages"]:
            s = dict(s)
            if s.get("adapter"):
                if s["code"] != adapter:
                    continue
            out.append(s)
        return out

    def stage_status(self, v: str, s: dict, run: dict | None) -> dict:
        od = self.out_dir(v)
        chk = od / "checks" / s["checks"]
        t = mtime(chk)
        started = run.get("started_ts") if run else None
        info = {"code": s["code"], "name": s["name"], "title": s["title"], "checks_mtime": fmt_t(t), "status": "none",
                "stop": 0, "flag": 0, "info": 0, "checks": [], "tables": []}
        if t is None:
            return info
        try:
            header, rows, _ = read_csv_rows(chk)
            recs = [dict(zip(header, r)) for r in rows]
        except OSError:
            recs = []
        sev = [r.get("severity", "") for r in recs]
        info["stop"], info["flag"], info["info"] = sev.count("STOP"), sev.count("FLAG"), sev.count("INFO")
        info["checks"] = [r for r in recs if r.get("code") != "OK"]
        info["status"] = "stop" if info["stop"] else "flag" if info["flag"] else "ok"
        info["stale"] = bool(started and t < started - 1)          # written by an earlier run
        for name in s.get("tables", []):
            p = self.table_path(v, name)
            info["tables"].append({"name": name, "exists": p is not None, "mtime": fmt_t(mtime(p)) if p else None})
        return info

    def input_files(self, v: str) -> list[dict]:
        out = []
        for grp in self.variant(v).get("inputs", []):
            d = ROOT / grp["dir"]
            for f in grp["files"]:
                p = d / f
                out.append({"mode": grp["mode"], "label": grp["label"], "file": f, "path": str(p.relative_to(ROOT)),
                            "exists": p.exists(), "mtime": fmt_t(mtime(p))})
        return out


class Runner:
    """At most one pipeline run at a time; state persisted in ui_run.json."""

    def __init__(self, tool: Tool):
        self.tool = tool
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.current: dict | None = None

    def state_path(self, v):
        return self.tool.out_dir(v) / "ui_run.json"

    def log_path(self, v):
        return self.tool.out_dir(v) / "ui_run.log"

    def load(self, v) -> dict | None:
        p = self.state_path(v)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
        return None

    def save(self, st: dict):
        p = self.state_path(st["variant"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st, indent=1), encoding="utf-8")

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, variant, adapter, first, last) -> dict:
        with self.lock:
            if self.running():
                raise RuntimeError("a run is already in progress")
            t = self.tool
            t.variant(variant)
            if adapter not in t.m["adapters"]:
                raise ValueError(f"unknown adapter {adapter}")
            codes = [s["code"] for s in t.stage_list(adapter)]
            if first not in codes or last not in codes or codes.index(first) > codes.index(last):
                raise ValueError("bad stage range")
            od = t.out_dir(variant)
            od.mkdir(parents=True, exist_ok=True)
            log = self.log_path(variant)
            st = {"variant": variant, "adapter": adapter, "first": first, "last": last, "route": "python",
                  "started": now(), "started_ts": time.time(), "finished": None, "rc": None,
                  "control_mtime": mtime(t.control(variant)), "command": [sys.executable, str(t.runner), variant,
                                                                         adapter, first, last]}
            fh = open(log, "w", encoding="utf-8")
            fh.write(f"[{st['started']}] {' '.join(st['command'])}\n")
            fh.flush()
            self.proc = subprocess.Popen(st["command"], cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT)
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
            self.proc.terminate()
            return True

    def status(self, v) -> dict | None:
        if self.current and self.current["variant"] == v:
            st = dict(self.current)
            st["running"] = self.running()
            return st
        st = self.load(v)
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
        b = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, p: Path):
        if not p.exists() or not p.is_file():
            return self.send_json({"error": "not found"}, 404)
        b = p.read_bytes()
        ctype = mimetypes.guess_type(str(p))[0] or "application/octet-stream"
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

    def audit(self, v, line):
        try:
            p = self.tool.out_dir(v) / "ui_actions.log"
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"[{now()}] {line}\n")
        except OSError:
            pass

    # ---- GET
    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/favicon.ico":
                self.send_response(204); self.end_headers(); return
            if u.path == "/" or u.path == "/index.html":
                return self.send_file(self.tool.page_dir / "index.html")
            if u.path.startswith("/kit/"):
                name = u.path[5:]
                if "/" in name or ".." in name:
                    return self.send_json({"error": "bad path"}, 400)
                return self.send_file(self.tool.kit_dir / name)
            if u.path.startswith("/page/"):
                name = u.path[6:]
                if "/" in name or ".." in name:
                    return self.send_json({"error": "bad path"}, 400)
                return self.send_file(self.tool.page_dir / name)
            if u.path == "/api/state":
                return self.send_json(self.state(q.get("variant", "synth"), q.get("adapter", "01a")))
            if u.path == "/api/table":
                return self.table(q.get("variant", "synth"), q.get("name", ""))
            if u.path == "/api/log":
                return self.log(q.get("variant", "synth"))
            return self.send_json({"error": "not found"}, 404)
        except ValueError as e:
            return self.send_json({"error": str(e)}, 400)

    def state(self, v, adapter):
        t = self.tool
        run = self.runner.status(v)
        ctl, res = t.control(v), t.results(v)
        ctl_m = mtime(ctl)
        stages = [t.stage_status(v, s, run) for s in t.stage_list(adapter)]
        overall = "none"
        ran = [s for s in stages if s["status"] != "none" and not s.get("stale")]
        if run and run.get("running"):
            overall = "running"
        elif ran:
            overall = "stop" if any(s["status"] == "stop" for s in ran) else \
                "flag" if any(s["status"] == "flag" for s in ran) else "ok"
        return {
            "tool": {"name": t.m["name"], "title": t.m["title"], "version": t.m.get("version", ""),
                     "server_version": VERSION, "repo": str(ROOT)},
            "variant": v, "adapter": adapter, "variants": list(t.m["variants"]), "adapters": t.m["adapters"],
            "control": {"path": str(ctl.relative_to(ROOT)), "exists": ctl.exists(), "mtime": fmt_t(ctl_m),
                        "changed_since_run": bool(run and ctl_m and run.get("control_mtime") and
                                                  ctl_m > run["control_mtime"] + 0.5)},
            "results": {"path": str(res.relative_to(ROOT)), "exists": res.exists(), "mtime": fmt_t(mtime(res))},
            "inputs": t.input_files(v),
            "stages": stages, "overall": overall, "run": run,
            "stage_codes": [s["code"] for s in t.stage_list(adapter)], "now": now(),
        }

    def table(self, v, name):
        p = self.tool.table_path(v, name)
        if p is None:
            return self.send_json({"error": f"no table {name}"}, 404)
        header, rows, n = read_csv_rows(p)
        return self.send_json({"name": name, "columns": header, "rows": rows, "n_rows": n, "truncated": n > len(rows),
                               "mtime": fmt_t(mtime(p)), "path": str(p.relative_to(ROOT))})

    def log(self, v):
        p = self.runner.log_path(v)
        lines = []
        if p.exists():
            with open(p, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()[-LOG_TAIL:]
        run = self.runner.status(v)
        return self.send_json({"lines": lines, "running": bool(run and run.get("running")), "run": run})

    # ---- POST
    def do_POST(self):
        u = urlparse(self.path)
        try:
            b = self.body()
            v = b.get("variant", "synth")
            self.tool.variant(v)
            if u.path == "/api/run":
                st = self.runner.start(v, b.get("adapter", "01a"), b.get("first", "00"), b.get("last", "07"))
                self.audit(v, f"run {st['command'][2:]}")
                return self.send_json({"ok": True, "run": st})
            if u.path == "/api/stop":
                ok = self.runner.stop()
                self.audit(v, "stop requested")
                return self.send_json({"ok": ok})
            if u.path == "/api/open":
                return self.open_(v, b)
            if u.path == "/api/copy":
                return self.copy_(v)
            return self.send_json({"error": "not found"}, 404)
        except (ValueError, RuntimeError) as e:
            return self.send_json({"error": str(e)}, 400)

    def resolve_open(self, v, b) -> Path | None:
        t = self.tool
        what = b.get("what")
        if what == "control":
            return t.control(v)
        if what == "results":
            return t.results(v)
        if what == "control_template":
            return ROOT / t.m["control_template"]
        if what == "table":
            return t.table_path(v, b.get("name", ""))
        if what == "folder":
            return t.out_dir(v)
        if what == "input":
            p = ROOT / b.get("path", "")
            return p if t.allowed(p) else None
        return None

    def open_(self, v, b):
        p = self.resolve_open(v, b)
        if p is None or not p.exists() or not self.tool.allowed(p):
            return self.send_json({"error": "file not found or outside the allowed folders"}, 404)
        self.audit(v, f"open {p.relative_to(ROOT)}")
        try:
            if sys.platform == "win32":
                os.startfile(str(p))  # noqa: S606 - the user's own application for this file type
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except OSError as e:
            return self.send_json({"error": f"could not open: {e}"}, 500)
        return self.send_json({"ok": True, "path": str(p.relative_to(ROOT))})

    def copy_(self, v):
        res = self.tool.results(v)
        if not res.exists():
            return self.send_json({"error": "no Results workbook yet"}, 404)
        dst = res.with_name(f"{res.stem}_{datetime.now():%Y%m%d_%H%M%S}{res.suffix}")
        shutil.copy2(res, dst)
        self.audit(v, f"copy {res.name} -> {dst.name}")
        return self.send_json({"ok": True, "path": str(dst.relative_to(ROOT))})


def serve(manifest: Path, port: int, open_browser: bool, quiet=True):
    tool = Tool(manifest)
    Handler.tool, Handler.runner, Handler.quiet = tool, Runner(tool), quiet
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    print(f"{tool.m['title']} UI {VERSION}: {url}   (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return port


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--manifest", default="ui/xol/manifest.json")
    ap.add_argument("--port", type=int, default=8765, help="0 = any free port")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args(argv)
    serve(ROOT / a.manifest, a.port, not a.no_browser, quiet=not a.verbose)


if __name__ == "__main__":
    main()
