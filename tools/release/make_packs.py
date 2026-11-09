"""Build the two release zips (0.8.0):

    python tools/release/make_packs.py            -> dist/xolpricing_user_<ver>.zip, dist/xolpricing_admin_<ver>.zip

User pack: what a pricing actuary needs to run the tool on client data, and nothing else: the page and its guide,
the Python route, the live workflows, the workbook templates, the CSV templates, the docs. No synthetic data, no
tests, no reference material, no builders for the synthetic book. The page hides the synthetic book when
data/synthetic is absent (manifest 'requires').
Admin pack: every file tracked in git (the whole repository at this commit), for the tool owner and the test suite.
Both carry START_HERE.txt. Only git-tracked files go in (never data/input, data/output or anything ignored).
"""
from __future__ import annotations

import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "alteryx"))
from xol_common import VERSION  # noqa: E402

USER_EXCLUDE_PREFIX = ("data/synthetic/", "tests/", "reference/", "tools/synth/", "tools/release/", "tools/docs/",
                       "alteryx/workflows/synth/", ".github/", "tools/alteryx/build_")
USER_EXCLUDE_FILES = {"tools/check_merit.bat", ".gitignore", ".gitattributes", "tools/alteryx/xmlkit.py"}

START_USER = f"""XoL Pricing Tool {VERSION} - user pack ({date.today():%Y-%m-%d})

1. Unzip anywhere (keep the folder structure). Once:  py -m pip install -r requirements-native.txt
2. Double-click  tools\\xol_ui.bat  -> the page opens at http://127.0.0.1:8765/
3. Press "How to use" in the page header (or open ui\\xol\\guide.html): the step-by-step guide with screenshots,
   adapted to your data (line data, claimant listing, Merit outputs). Ctrl+K searches the page, the glossary and
   the documents; the theme button switches to dark. Edit the Control workbook in the page or in Excel.
Illustrated manual: docs\\manual\\USER_MANUAL.html. Developer notes: docs\\RUNNING.md. Changes: docs\\CHANGELOG.md.
This pack holds no synthetic data: the page offers the live book only.
"""
START_ADMIN = f"""XoL Pricing Tool {VERSION} - admin pack ({date.today():%Y-%m-%d})

The whole repository at this release: user pack contents plus the synthetic book (data\\synthetic), the test suite
(tests\\, python tests\\run_all.py), the Designer comparison baselines, the builders (tools\\alteryx\\build_*.py,
tools\\excel\\build_*.py, tools\\synth), and tools\\release\\make_packs.py which built both packs.
The page shows both books (synthetic and live). Use this pack to test a change; give users the user pack.
"""


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ln for ln in out.splitlines() if ln and (ROOT / ln).is_file()]


def build(name: str, files: list[str], start: str) -> Path:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"xolpricing_{name}_{VERSION}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("START_HERE.txt", start)
        for f in files:
            z.write(ROOT / f, "xolpricing/" + f)
        for keep in ("data/input/.gitkeep", "data/output/.gitkeep"):
            if keep not in files:
                z.writestr("xolpricing/" + keep, "")
    return out


def user_files(files: list[str]) -> list[str]:
    return [f for f in files if not f.startswith(USER_EXCLUDE_PREFIX) and f not in USER_EXCLUDE_FILES]


def main() -> int:
    files = tracked()
    u = build("user", user_files(files), START_USER)
    a = build("admin", files, START_ADMIN)
    for p in (u, a):
        with zipfile.ZipFile(p) as z:
            print(f"{p.relative_to(ROOT)}: {len(z.namelist())} files, {p.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
