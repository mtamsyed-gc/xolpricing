"""Run the XoL pipeline (0.9.0: Python is the only route; the browser page tools/ui/serve.py calls this script).

    py tools\\run_python.py                       synthetic data, line adapter (01a), stages 00 to 07
    py tools\\run_python.py live 01c              live data, Merit adapter
    py tools\\run_python.py live 01c 00 04        stages 00 to 04 only (third / fourth argument: first / last stage)
    py tools\\run_python.py synth 01a 04          start at stage 04 (00-03 already run)

Runs the stage definitions in alteryx/workflows (live) or alteryx/workflows/synth (synth) with tools/alteryx/sim.py,
the Python executor. The definitions were first written for Alteryx Designer and the executor reproduces Designer's own
runs exactly (tests/test_designer_twin.py keeps that comparison); the tool does not use Alteryx from 0.9.0.
Stops at the first workflow that ends with an error (a STOP check is an error). Log: data/output/<variant>/python_run.log.

Starting after stage 00 needs stage files written by this script (stage/ENGINE.txt says "python"); stage files left by
an older Alteryx run are refused and the run must start from 00.

Needs Python 3.12+ with the packages in requirements-native.txt (numpy, pandas, openpyxl; scipy for stage 04a).
"""
from __future__ import annotations

import shutil
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "alteryx"))

ADAPTERS = {"01a": "01a_adapter_line", "01b": "01b_adapter_claimant", "01c": "01c_adapter_ete"}
STAGES = ["00", "AD", "02", "03", "04a", "04", "05", "06", "07"]
NAMES = {"00": "00_read_control", "02": "02_experience", "03": "03_burning_cost", "04a": "04a_fit_severity",
         "04": "04_simulate", "05": "05_per_event", "06": "06_stop_loss", "07": "07_results"}
MARKER = "ENGINE.txt"


class RunError(Exception):
    pass


def plan(adapter: str, first: str, last: str) -> list[tuple[str, str]]:
    """(stage code, workflow name) from first to last in run order; the adapter's code is its own (01a / 01b / 01c)."""
    codes = [adapter if s == "AD" else s for s in STAGES]
    names = {c: (ADAPTERS[adapter] if c == adapter else NAMES[c]) for c in codes}
    for s in (first, last):
        if s not in codes:
            raise RunError(f"Unknown stage {s}: use one of {', '.join(codes)}")
    i, j = codes.index(first), codes.index(last)
    if i > j:
        raise RunError(f"First stage {first} comes after last stage {last}")
    return [(c, names[c]) for c in codes[i:j + 1]]


def check_packages(steps) -> None:
    need = ["numpy", "pandas", "openpyxl"] + (["scipy"] if any(c == "04a" for c, _ in steps) else [])
    missing = []
    for m in need:
        try:
            __import__(m)
        except ImportError:
            missing.append(m)
    if missing:
        raise RunError(f"Python packages missing: {', '.join(missing)}. Install requirements-native.txt "
                       f"(py -m pip install -r requirements-native.txt) and run again.")


def main(argv: list[str]) -> int:
    variant = (argv[0] if len(argv) > 0 else "synth").lower()
    adapter = (argv[1] if len(argv) > 1 else "01a").lower()
    first = (argv[2] if len(argv) > 2 else "00").lower()
    last = (argv[3] if len(argv) > 3 else "07").lower()
    if variant not in ("synth", "live"):
        print(f"Unknown variant {variant}: use synth or live")
        return 2
    if adapter not in ADAPTERS:
        print(f"Unknown adapter {adapter}: use 01a, 01b or 01c")
        return 2
    try:
        steps = plan(adapter, first, last)
        check_packages(steps)
    except RunError as e:
        print(e)
        return 2
    import sim  # after the package check: the Python tool (stage 04a) imports numpy, pandas and scipy

    wf_dir = ROOT / "alteryx" / "workflows" / ("synth" if variant == "synth" else "")
    out = ROOT / "data" / "output" / variant
    for d in ("", "stage", "checks", "results"):
        (out / d).mkdir(parents=True, exist_ok=True)
    if not (out / "XoL_Results.xlsx").exists():
        shutil.copy(ROOT / "excel" / "templates" / "XoL_Results.xlsx", out / "XoL_Results.xlsx")
    marker = out / "stage" / MARKER
    if steps[0][0] != "00" and not (marker.exists() and marker.read_text(encoding="utf-8").startswith("python")):
        print(f"Stage {steps[0][0]} needs the stage files of a run of stage 00 by this tool (the stage folder holds "
              f"files of an older Alteryx run). Run from stage 00: py tools\\run_python.py {variant} {adapter} 00 {last}")
        return 2
    log = out / "python_run.log"
    started = datetime.now()
    lines = [f"XoL pipeline (Python): variant {variant}, adapter {ADAPTERS[adapter]}, stages {steps[0][0]} to "
             f"{steps[-1][0]}", f"Started {started:%Y-%m-%d %H:%M:%S}", f"Python {sys.version.split()[0]}"]
    print(lines[0] + f". Log: {log}")
    rc = 0
    for code, name in steps:
        t = time.time()
        print(f"[{datetime.now():%H:%M:%S}] {name} ...", flush=True)
        lines += ["", f"===== {name}  start {datetime.now():%H:%M:%S}"]
        try:
            sim.run(wf_dir / f"{name}.yxmd")
        except Exception as e:  # noqa: BLE001 - every failure stops the chain with its message in the log
            lines += [f"FAILED: {type(e).__name__}: {e}", traceback.format_exc()]
            print(f"   FAILED: {type(e).__name__}: {e}")
            rc = 2
            break
        errors = [m for m in sim.MESSAGES if m[0] == "Error"]
        warns = [m for m in sim.MESSAGES if m[0] == "Warning"] + [("Warning", f"{a}: {b}") for a, b in sim.WARNINGS]
        lines += [f"{k}: {m}" for k, m in sim.MESSAGES] + [f"Warning: {a}: {b}" for a, b in sim.WARNINGS]
        lines.append(f"===== {name}  end {datetime.now():%H:%M:%S}  {time.time() - t:.0f} s, "
                     f"{len(errors)} error(s), {len(warns)} warning(s)")
        if code == "00":
            marker.write_text(f"python {started:%Y-%m-%d %H:%M:%S}\n", encoding="utf-8")
        if errors:
            print(f"   STOPPED: {errors[0][1]}" + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""))
            rc = 2
            break
        print(f"   ok ({time.time() - t:.0f} s)" + (f", {len(warns)} warning(s): see the log" if warns else ""))
    lines.append(("ALL DONE " if rc == 0 else "STOPPED ") + f"{datetime.now():%Y-%m-%d %H:%M:%S}")
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print()
    if rc == 0:
        print(f"All workflows ran. Results workbook: {out / 'XoL_Results.xlsx'}" if steps[-1][0] == "07"
              else f"Stages {steps[0][0]} to {steps[-1][0]} ran.")
    else:
        print(f"Stopped: the last workflow ended with an error. Details in {log}")
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
