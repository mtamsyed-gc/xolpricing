"""The browser UI server (tools/ui/serve.py, 0.8.0), driven through its HTTP API on a free port.

1. The page, kit and manifest are served; /api/state lists the synthetic book's stages and files.
2. A run of stages 00 to 03 started through /api/run finishes with return code 0; the state then shows 00-03 with a
   status from their checks files, 04a-07 as not run or stale, the run log tail ends with the exit code.
3. /api/table returns a results CSV with its columns and rows; names outside checks/ and results/ are refused.
4. A Control workbook saved after the run start is reported as changed_since_run.
5. A STOP row planted in checks_03.csv makes the stage and the overall status "stop" (restored afterwards).
6. /api/open refuses a path outside the allowed roots; a second /api/run while one is in progress is refused.
"""
import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "ui"))
import serve  # noqa: E402

OUT = ROOT / "data/output/synth"


def report(name, good):
    print(f"{'PASS' if good else 'FAIL'}  {name}")
    return bool(good)


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}"

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=10) as r:
                return r.status, r.headers.get("Content-Type", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, "", b""

    def json(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())


def main() -> int:
    tool = serve.Tool(ROOT / "ui/xol/manifest.json")
    serve.Handler.tool, serve.Handler.runner = tool, serve.Runner(tool)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    c = Client(httpd.server_address[1])
    ok = True
    try:
        st, ct, body = c.get("/")
        ok &= report("index.html served as html", st == 200 and "text/html" in ct and b"gc_ui.js" in body)
        ok &= report("kit css and js served", c.get("/kit/gc_ui.css")[0] == 200 and c.get("/kit/gc_ui.js")[0] == 200)
        ok &= report("manifest served", c.get("/page/manifest.json")[0] == 200)
        ok &= report("path escape refused", c.get("/kit/../serve.py")[0] in (400, 404))

        st, s = c.json("/api/state?variant=synth&adapter=01a")
        codes = [x["code"] for x in s["stages"]]
        ok &= report("state: 9 stages for adapter 01a in run order",
                     codes == ["00", "01a", "02", "03", "04a", "04", "05", "06", "07"])
        ok &= report("state: synthetic line files present", all(f["exists"] for f in s["inputs"] if f["mode"] == "01a"))
        st, s2 = c.json("/api/state?variant=synth&adapter=01c")
        ok &= report("state: adapter 01c swaps the adapter stage", [x["code"] for x in s2["stages"]][1] == "01c")

        st, r = c.json("/api/run", {"variant": "synth", "adapter": "01a", "first": "00", "last": "03"})
        ok &= report("run 00 -> 03 accepted", st == 200 and r["ok"] and r["run"]["pid"])
        st, r2 = c.json("/api/run", {"variant": "synth", "adapter": "01a", "first": "00", "last": "03"})
        ok &= report("second run while running refused", st == 400 and "in progress" in r2["error"])
        t0 = time.time()
        while time.time() - t0 < 240:
            st, lg = c.json("/api/log?variant=synth")
            if not lg["running"]:
                break
            time.sleep(2)
        ok &= report("run finished with exit code 0", not lg["running"] and lg["run"]["rc"] == 0 and
                     lg["lines"][-1].endswith("exit code 0"))
        st, s = c.json("/api/state?variant=synth&adapter=01a")
        by = {x["code"]: x for x in s["stages"]}
        ok &= report("stages 00-03 have a status from their checks files, none stale",
                     all(by[k]["status"] in ("ok", "flag") and not by[k]["stale"] for k in ("00", "01a", "02", "03")))
        ok &= report("stages after 03 are not run or stale",
                     all(by[k]["status"] == "none" or by[k]["stale"] for k in ("04a", "04", "05", "06", "07")))
        ok &= report("overall status from the stages that ran", s["overall"] in ("ok", "flag") and s["run"]["rc"] == 0)
        ok &= report("ui_run.json written", (OUT / "ui_run.json").exists())

        st, t = c.json("/api/table?variant=synth&name=out_bc_summary")
        ok &= report("table out_bc_summary: columns and rows", st == 200 and "bc_pppy_total" in t["columns"] and t["n_rows"] >= 1)
        ok &= report("table outside checks/results refused", c.json("/api/table?variant=synth&name=../python_run.log")[0] == 404)

        ctl = tool.control("synth")
        m = ctl.stat()
        try:
            ctl.touch()
            st, s = c.json("/api/state?variant=synth&adapter=01a")
            ok &= report("Control saved after the run start is reported as changed", s["control"]["changed_since_run"])
        finally:
            import os
            os.utime(ctl, (m.st_atime, m.st_mtime))
        st, s = c.json("/api/state?variant=synth&adapter=01a")
        ok &= report("Control restored: not changed", not s["control"]["changed_since_run"])

        chk = OUT / "checks/checks_03.csv"
        orig = chk.read_text(encoding="utf-8")
        try:
            chk.write_text(orig + 'S03,B99,STOP,test,planted stop for test_ui\n', encoding="utf-8")
            st, s = c.json("/api/state?variant=synth&adapter=01a")
            by = {x["code"]: x for x in s["stages"]}
            ok &= report("planted STOP: stage 03 and overall status stop",
                         by["03"]["status"] == "stop" and s["overall"] == "stop" and
                         any(r["code"] == "B99" for r in by["03"]["checks"]))
        finally:
            chk.write_text(orig, encoding="utf-8")

        st, sp = c.json("/api/spec")
        ok &= report("spec: Params plus every table sheet, each input with required / options / description",
                     st == 200 and [x["sheet"] for x in sp["sheets"]][:3] == ["Params", "FX", "Limits"] and
                     all("required" in i and "options" in i and "description" in i for sh in sp["sheets"] for i in sh["inputs"]) and
                     next(i for i in sp["sheets"][0]["inputs"] if i["name"] == "source_mode")["options"] == ["L", "C", "E"])
        st, pr = c.json("/api/params?variant=synth")
        ok &= report("params: synthetic Control has every required value filled",
                     st == 200 and pr["exists"] and pr["missing_required"] == [] and pr["invalid"] == [] and len(pr["rows"]) >= 40)
        tmpl = serve.Tool(ROOT / "ui/xol/manifest.json")
        tmpl.m["variants"]["synth"]["control"] = "excel/templates/XoL_Control.xlsx"
        pt = tmpl.params("synth")
        ok &= report("params: blank template reports the required client fields as blank (cedant, epi_quote_year...)",
                     "cedant" in pt["missing_required"] and "epi_quote_year" in pt["missing_required"] and
                     "iterations" not in pt["missing_required"] and pt["invalid"] == [])

        st, r = c.json("/api/open", {"variant": "synth", "what": "input", "path": "../etc/hosts"})
        ok &= report("open outside the allowed roots refused", st == 404)
        st, r = c.json("/api/open", {"variant": "synth", "what": "table", "name": "nope"})
        ok &= report("open of a missing table refused", st == 404)
        st, r = c.json("/api/run", {"variant": "synth", "adapter": "01a", "first": "03", "last": "02"})
        ok &= report("bad stage order refused", st == 400)
    finally:
        httpd.shutdown()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
