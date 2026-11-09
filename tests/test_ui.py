"""The browser page XU-1.0 (0.9.1) on the vendored GC UI kit: ui/kit (gc_server.py) + ui/xol (manifest, xol_plugin.py,
index.html), driven through its HTTP API on a free port, on the synthetic book.

1. ui/kit equals the vendored gc-ui-kit build (HASHES.json; a hand edit fails here, UI_STANDARD §14, D-UI1); the
   manifest's kit_build equals the vendored kit and the server; every file the manifest names exists in the repo.
2. /api/state: both books, 9 stages for adapter 01a, the synthetic line files present, Params readiness read from the
   synthetic Control (every required value filled); the blank template reports the client fields as blank.
3. A run of stages 00 to 03 through /api/run (action "run", positional args) finishes with exit code 0; the state then
   shows 00-03 with a status from their checks files, 04a-07 not run or stale; a second run while one is in progress
   and a bad stage order are refused.
4. /api/table serves a results CSV; /api/plugin/spec serves the Input guide; /api/sheet on the Control's Params sheet
   carries per-parameter help (options, description, kind input / check); a save through /api/sheet/save writes the
   cell, keeps a backup and reports the change (restored afterwards; the file's mtime then shows as changed since run).
5. A STOP row planted in checks_03.csv makes the stage and the overall status "stop" (restored afterwards).
6. /api/open refuses a path outside the allowed roots and a .cmd/.bat; page lint: no literal colour, no alert/confirm,
   every glossary used_in anchor is a card the page builds.
"""
import csv
import hashlib
import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui"
sys.path.insert(0, str(UI / "kit"))
import gc_server as GS  # noqa: E402

OUT = ROOT / "data/output/synth"
CTL = ROOT / "data/synthetic/XoL_Control_synthetic.xlsx"
FAILS = []


def report(name, good):
    print(f"{'PASS' if good else 'FAIL'}  {name}")
    if not good:
        FAILS.append(name)
    return bool(good)


class Srv:
    def __init__(self, manifest, client):
        self.httpd = None
        ev = threading.Event()

        def ready(h):
            self.httpd = h
            ev.set()
        self.t = threading.Thread(target=GS.serve, args=(manifest, client, 0, False), kwargs={"ready": ready}, daemon=True)
        self.t.start()
        ev.wait(5)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def get(self, path, raw=False):
        try:
            with urllib.request.urlopen(self.url + path, timeout=30) as r:
                b = r.read()
                return r.status, (b if raw or r.headers.get_content_type() != "application/json" else json.loads(b))
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                return e.code, json.loads(b)
            except ValueError:
                return e.code, b

    def post(self, path, body):
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def wait_run(self, secs=600):
        for _ in range(int(secs * 2)):
            st, s = self.get("/api/state")
            if not (s.get("run") or {}).get("running"):
                return s
            time.sleep(0.5)
        return s

    def stop(self):
        self.httpd.shutdown()


def book(s, vid="synth"):
    return s["plugin"]["books"][vid]


def main() -> int:
    # 1. kit and manifest
    hp = UI / "kit" / "HASHES.json"
    report("ui/kit/HASHES.json present (vendored with gc-ui-kit tools/vendor.py)", hp.exists())
    h = json.loads(hp.read_text(encoding="utf-8"))
    bad = [n for n, d in h["files"].items() if not (UI / "kit" / n).exists() or hashlib.sha256((UI / "kit" / n).read_bytes()).hexdigest() != d]
    report(f"vendored kit files match their hashes ({bad})", not bad)
    m = json.loads((UI / "xol" / "manifest.json").read_text(encoding="utf-8"))
    report(f"manifest kit_build {m.get('kit_build')} = vendored {h['kit']} = server {GS.KIT_VERSION}", m.get("kit_build") == h["kit"] == GS.KIT_VERSION)
    sys.path.insert(0, str(ROOT / "tools" / "alteryx"))
    from xol_common import VERSION
    report(f"manifest version {m['version']} = tool VERSION {VERSION}", m["version"] == VERSION)
    named = [m["help_doc"], m["guide"], m["manual"], m["control_template"], UI / "xol" / m["plugin"]] + [d["doc"] for d in m["help_docs"]] + \
            [m["variants"]["synth"]["control"], m["editable_workbooks"]["control_synth"]["path"]]
    miss = [str(n) for n in named if not (ROOT / n).exists()]
    report(f"every document, template, synthetic workbook and plugin the manifest names exists ({miss})", not miss)
    report("live Control is the user's file: not in the repo, same path in variants and editable_workbooks",
           not (ROOT / m["variants"]["live"]["control"]).exists() and m["variants"]["live"]["control"] == m["editable_workbooks"]["control_live"]["path"])
    report("launcher tools/xol_ui.bat starts ui/xol/serve.py", "ui\\xol\\serve.py" in (ROOT / "tools/xol_ui.bat").read_text(encoding="utf-8"))
    report("old tools/ui/serve.py removed", not (ROOT / "tools/ui/serve.py").exists())
    for d in ("checks", "results"):
        (OUT / d).mkdir(parents=True, exist_ok=True)
    srv = Srv(UI / "xol" / "manifest.json", ROOT)
    try:
        st, body = srv.get("/", raw=True)
        report("index.html served", st == 200 and b"gc_ui.js" in body and b"GC.shell" in body)
        report("kit css, js and tokens served", all(srv.get("/kit/" + f, raw=True)[0] == 200 for f in ("gc_ui.css", "gc_ui.js", "gc_tokens.css")))
        report("path escape refused", srv.get("/kit/../serve.py", raw=True)[0] in (400, 403, 404))
        # 2. state
        st, s = srv.get("/api/state")
        P = s["plugin"]
        report("state: both books (synth, live), plugin without error", st == 200 and set(P["variants"]) == {"synth", "live"} and not P.get("error"))
        b = book(s)
        codes = [c for c in m["stages"] if not c.get("adapter") or c["code"] == "01a"]
        report("state: 9 stages for adapter 01a in run order", [c["code"] for c in codes] == ["00", "01a", "02", "03", "04a", "04", "05", "06", "07"]
               and all(c["code"] in b["stages"] for c in codes))
        report("state: synthetic line files present", all(f["exists"] for f in b["inputs"] if f["mode"] == "01a"))
        report("state: synthetic Control present, Params read, every required value filled",
               b["control"]["exists"] and b["params"]["exists"] and not b["params"]["error"] and b["params"]["missing_required"] == [] and b["params"]["n"] >= 40)
        report("state: cutoff advice present (per-person layers found)", b["params"]["cutoff"] is not None and "mode" in b["params"]["cutoff"])
        # blank template: client fields blank — read through the plugin directly
        sys.path.insert(0, str(UI / "xol"))
        import xol_plugin as XP
        tool = GS.Tool(UI / "xol" / "manifest.json", ROOT)
        pt = XP.params(tool, ROOT / m["control_template"])
        report("params: blank template reports the required client fields as blank (cedant, ...)", pt["exists"] and "cedant" in pt["missing_required"])
        # 3. run 00 -> 03
        st, r = srv.post("/api/run", {"action": "run", "args": {"variant": "synth", "adapter": "01a", "first": "00", "last": "03"}})
        report("run 00 -> 03 accepted", st == 200 and r.get("ok") and r["run"]["command"][-4:] == ["synth", "01a", "00", "03"])
        st2, r2 = srv.post("/api/run", {"action": "run", "args": {"variant": "synth", "adapter": "01a", "first": "00", "last": "03"}})
        report("second run while running refused (409)", st2 == 409 and "progress" in json.dumps(r2).lower())
        s = srv.wait_run(300)
        b = book(s)
        report("run finished with exit code 0", s["run"]["rc"] == 0 and not s["run"]["running"])
        st, lg = srv.get("/api/log")
        report("log tail ends with the exit code", st == 200 and lg["lines"] and "exit code 0" in lg["lines"][-1])
        report("stages 00-03 have a status from their checks files, none stale",
               all(b["stages"][c]["status"] in ("ok", "flag") and not b["stages"][c]["stale"] for c in ("00", "01a", "02", "03")))
        report("stages after 03 are not run or stale", all(b["stages"][c]["status"] == "none" or b["stages"][c]["stale"] for c in ("04a", "04", "05", "06", "07")))
        report("overall status from the stages that ran", b["overall"] in ("ok", "flag"))
        report("state.plugin.run echoes the run's book and stages", P and s["plugin"]["run"] == {"variant": "synth", "adapter": "01a", "first": "00", "last": "03"})
        st, r3 = srv.post("/api/run", {"action": "run", "args": {"variant": "synth", "adapter": "01a", "first": "03", "last": "00"}})
        report("bad stage order refused by the launcher (non-zero exit)", st == 200 and srv.wait_run(60)["run"]["rc"] != 0)
        st, r4 = srv.post("/api/run", {"action": "run", "args": {"variant": "synth", "adapter": "01x", "first": "00", "last": "00"}})
        report("adapter outside choices refused", st == 400)
        # 4. tables, spec, sheet, save
        t3 = b["stages"]["03"]["tables"]
        bc = next((t for t in t3 if t["name"] == "out_bc_summary"), None)
        st, t = srv.get("/api/table?path=" + bc["path"] + "&limit=50") if bc and bc["exists"] else (0, {})
        report("table out_bc_summary: columns and rows", st == 200 and "bc_pppy_total" in t["columns"] and t["n_rows"] >= 1)
        report("table outside the allowed roots refused", srv.get("/api/table?path=../etc/passwd")[0] in (400, 403, 404))
        st, sp = srv.get("/api/plugin/spec")
        report("spec: Params plus every table sheet, each input with required / options / description",
               st == 200 and sp["sheets"][0]["sheet"] == "Params" and len(sp["sheets"]) >= 8 and all({"required", "options", "description"} <= set(i) for sh in sp["sheets"] for i in sh["inputs"]))
        st, sh = srv.get("/api/sheet?id=control_synth&sheet=Params&limit=60")
        cols = {c["name"]: c for c in sh["columns"]}
        report("sheet Params: value column is an input, group is locked, rows carry per-parameter help",
               st == 200 and cols["value"]["kind"] == "input" and cols["group"]["locked"] and sh.get("rows_help") and any(r.get("options") for r in sh["rows_help"].values()))
        # save: change 'cedant' then restore
        rows = sh["rows"]
        ci = [c["name"] for c in sh["columns"]]
        ridx = next(i for i, r in enumerate(rows) if r["v"][ci.index("param_id")] == "cedant")
        xrow = rows[ridx]["r"]
        bak = shutil.copy(CTL, CTL.with_suffix(".test_bak.xlsx"))
        try:
            body = {"id": "control_synth", "sheet": "Params", "mtime": sh["mtime_ts"], "edits": [{"row": xrow, "col": cols["value"]["col"], "value": "Test Cedant UI"}], "appends": []}
            st, sv = srv.post("/api/sheet/save", body)
            report("save through the page: one change, backup kept", st == 200 and sv.get("changed") == 1 and sv.get("backup"))
            st, sh2 = srv.get("/api/sheet?id=control_synth&sheet=Params&limit=60")
            report("saved cell read back", sh2["rows"][ridx]["v"][ci.index("value")] == "Test Cedant UI")
            st, sv2 = srv.post("/api/sheet/save", dict(body, edits=[{"row": body["edits"][0]["row"], "col": body["edits"][0]["col"], "value": "x"}]))
            report("save with a stale mtime refused (409)", st == 409)
            st, s = srv.get("/api/state")
            report("Control saved after the run start is reported as changed", book(s)["control"]["changed_since_run"])
            bk = ROOT / sv["backup"] if sv.get("backup") else None
        finally:
            shutil.copy(bak, CTL)
            Path(bak).unlink()
            if bk and bk.exists():
                bk.unlink()
        # 5. planted STOP
        chk = OUT / "checks" / "checks_03.csv"
        keep = chk.read_bytes()
        try:
            with chk.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                hdr = keep.split(b"\n")[0].decode().strip().split(",")
                w.writerow([{"stage": "S03", "code": "TEST_STOP", "severity": "STOP", "item": "test", "message": "planted by tests/test_ui.py"}.get(c, "") for c in hdr])
            st, s = srv.get("/api/state")
            report("planted STOP: stage 03 and overall status stop", book(s)["stages"]["03"]["status"] == "stop" and book(s)["overall"] == "stop")
        finally:
            chk.write_bytes(keep)
        # 6. open refusals, page lint
        st, _ = srv.post("/api/open", {"path": "../../etc/hosts"})
        report("open outside the allowed roots refused", st in (400, 403, 404))
        st, _ = srv.post("/api/open", {"path": "tools/xol_ui.bat"})
        report("open of a .bat refused", st in (400, 403, 404))
        page = (UI / "xol" / "index.html").read_text(encoding="utf-8")
        report("page: no literal colour, no alert / confirm", not re.search(r"#[0-9a-fA-F]{6}\b|\brgb\(", page) and not re.search(r"\b(alert|window\.confirm)\(", page))
        g = json.loads((UI / "xol" / "glossary.json").read_text(encoding="utf-8"))
        anchors = set(re.findall(r'id: "(card-[\w-]+)"', page)) | {"card-stage-" + c["code"] for c in m["stages"]}
        used = {u for e in (g if isinstance(g, list) else g["entries"]) for u in e.get("used_in", [])}
        report(f"glossary used_in anchors are cards the page builds ({sorted(used - anchors)})", used <= anchors)
    finally:
        srv.stop()
    print("PASS" if not FAILS else f"FAIL ({len(FAILS)}): " + "; ".join(FAILS))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
