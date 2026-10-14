"""Run every test: python tests/run_all.py"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Tools whose XML is taken from the team's Designer 2025.2 workflows (via ete-dashboard xmlkit)
VERIFIED = {"ToolContainer", "TextBox", "TextInput", "DbFileInput", "DbFileOutput", "Formula", "Filter", "AlteryxSelect",
            "Join", "Union", "Summarize", "Sort", "Unique", "MultiRowFormula", "GenerateRows", "RecordID", "FieldInfo",
            "Message", "BlockUntilDone", "DynamicInput", "Directory"}


def structural() -> bool:
    ok = True
    for wf in sorted((ROOT / "alteryx/workflows").rglob("*.yxmd")):
        s = wf.read_text(encoding="utf-8")
        plugins = {p.split(".")[-1] for p in re.findall(r'Plugin="([^"]+)"', s)}
        ver = re.search(r'yxmdVer="([^"]+)"', s).group(1)
        bad = plugins - VERIFIED
        n = len(re.findall(r"<Node ToolID", s))
        print(f"  {wf.relative_to(ROOT)}: {n} nodes, yxmdVer {ver}, unverified tools: {sorted(bad) or 'none'}")
        ok &= not bad and ver == "2025.2"
    return ok


def main() -> int:
    ok = True
    for t in ("validate_synthetic.py", "test_stage00.py", "test_stage01.py", "test_stage02.py", "test_stage03.py", "test_stage04.py", "test_stage05.py",
              "test_designer_twin.py"):
        print(f"== {t}")
        r = subprocess.run([sys.executable, str(ROOT / "tests" / t)], capture_output=True, text=True)
        tail = [x for x in r.stdout.splitlines() if x.strip()][-3:]
        print("  " + "\n  ".join(tail))
        ok &= r.returncode == 0
    print("== structural")
    ok &= structural()
    print("ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
