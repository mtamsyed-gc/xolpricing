"""The release packs (tools/release/make_packs.py): the user pack holds no synthetic data, tests, reference material
or synthetic builders; unpacked, its page serves the guide and offers the live book only. The admin pack holds every
tracked file."""
import json
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "release"))
import make_packs  # noqa: E402


def report(name, good):
    print(f"{'PASS' if good else 'FAIL'}  {name}")
    return bool(good)


def main() -> int:
    ok = True
    files = make_packs.tracked()
    user = make_packs.user_files(files)
    ok &= report("user pack: no data/synthetic, tests/, reference/, tools/synth, synth workflows",
                 not any(f.startswith(("data/synthetic/", "tests/", "reference/", "tools/synth/", "alteryx/workflows/synth/")) for f in user))
    ok &= report("user pack: no Alteryx launcher, no builders", not any(f in user for f in ("tools/run_pipeline_python.bat",
                 "tools/check_merit.bat", "tools/alteryx/xmlkit.py")) and not any(f.startswith("tools/alteryx/build_") for f in user))
    need = ["tools/xol_ui.bat", "tools/run_pipeline.bat", "ui/xol/serve.py", "ui/xol/xol_plugin.py", "ui/xol/glossary.json", "ui/kit/gc_server.py", "ui/kit/HASHES.json", "ui/kit/gc_tokens.css", "ui/kit/common_glossary.json", "tools/run_python.py", "tools/alteryx/sim.py", "tools/python/sevfit.py",
            "tools/excel/control_spec.py", "ui/xol/index.html", "ui/xol/guide.html", "ui/xol/manifest.json", "ui/kit/gc_ui.css",
            "excel/templates/XoL_Control.xlsx", "excel/templates/XoL_Results.xlsx", "alteryx/workflows/00_read_control.yxmd",
            "alteryx/workflows/07_results.yxmd", "data/templates/line_members.csv", "docs/manual/USER_MANUAL.html",
            "requirements-native.txt", "tools/import_merit.bat"]
    ok &= report("user pack: everything the page and the Python route need", all(n in user for n in need))
    ok &= report("admin pack: every tracked file", set(files) >= set(user) and "data/synthetic/XoL_Control_synthetic.xlsx" in files)

    u = make_packs.build("user", user, make_packs.START_USER)
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(u) as z:
            z.extractall(td)
        root = Path(td) / "xolpricing"
        ok &= report("user pack unpacks with data/input and data/output present", (root / "data/input").is_dir() and (root / "data/output").is_dir())
        # serve the unpacked copy with its own vendored kit: the synthetic book must be hidden
        import importlib
        sys.path.insert(0, str(root / "ui/kit"))
        for mod in ("gc_server",):
            sys.modules.pop(mod, None)
        GS = importlib.import_module("gc_server")
        sys.path.pop(0)
        tool = GS.Tool(root / "ui/xol/manifest.json", root)
        GS.Handler.tool, GS.Handler.runner = tool, GS.Runner(tool)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), GS.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        try:
            st = json.loads(urllib.request.urlopen(base + "/api/state", timeout=30).read())
            books = st["plugin"]["books"]
            ok &= report("unpacked page offers the live book only; no Control workbook yet",
                         st["plugin"]["variants"] == ["live"] and list(books) == ["live"] and not books["live"]["control"]["exists"])
            ok &= report("guide served", urllib.request.urlopen(base + "/page/guide.html", timeout=10).status == 200)
            sp = json.loads(urllib.request.urlopen(base + "/api/plugin/spec", timeout=10).read())
            ok &= report("input reference served from the pack's own spec", sp["sheets"][0]["sheet"] == "Params")
            ok &= report("kit served from the pack", urllib.request.urlopen(base + "/kit/gc_ui.js", timeout=10).status == 200)
        finally:
            httpd.shutdown()
            sys.modules.pop("gc_server", None)
        # the unpacked user pack runs on its own: live book, line data (copies of the synthetic files), stages 00-04a
        import shutil
        (root / "data/input/line").mkdir(parents=True, exist_ok=True)
        for f in ("members.csv", "claims_lines.csv", "run_meta.csv"):
            shutil.copy(ROOT / "data/synthetic/line" / f, root / "data/input/line" / f)
        shutil.copy(ROOT / "data/synthetic/XoL_Control_synthetic.xlsx", root / "data/input/XoL_Control.xlsx")
        r = subprocess.run([sys.executable, str(root / "tools/run_python.py"), "live", "01a", "00", "04a"], capture_output=True,
                           text=True, cwd=str(root))
        ok &= report("unpacked user pack runs live 00 -> 04a on its own (no builders, no tests)",
                     r.returncode == 0 and "Stages 00 to 04a ran." in r.stdout and
                     (root / "data/output/live/results/out_fit_candidates.csv").exists())
        if r.returncode:
            print(r.stdout[-1500:], r.stderr[-1500:])
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
