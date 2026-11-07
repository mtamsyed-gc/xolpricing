"""Native stage 04 (tools/native/stage04.py, 0.10.0) against the executor running 04_simulate.yxmd (tools/alteryx/sim.py).

1. Equality with the executor. Each variant runs stages 00-04a in the executor, then stage 04 twice on the same stage
   files: the executor in one copy of the folder, the native module in another. Every output must be the same: the 8
   stage files (field names, order and types; every value with its Python type) and the 6 CSVs byte for byte, plus
   the Message tool's messages. Variants: line adapter (annual AAD and a paid reinstatement, as test_stage04), Poisson
   and negative binomial, PYTHON severity (Pareto, GPD, lognormal, gamma, Weibull) and NATIVE, another seed, the layer
   terms (AAL, co-participation, unlimited and capped reinstatements, pro rata as to time S06, a layer without a label),
   credibility below 1 with complement_source BENCHMARK, few claims above a high cut-off (S04, S05), NEGBIN without
   overdispersion (S03), lambda above 700 (S02 stop), below_cutoff STOP (S08), IGNORE (S09), EMPIRICAL (S10), an empty band
   (S11), a stale severity table (S07), 2,000 iterations, and the claimant (01b) and ETE (01c) adapters.
   Variants other than the adapters reuse the line adapter's stage 01-02 files (those stages read none of the
   parameters or layers changed here) and rerun 00, 03 and 04a.
2. Fallback: data outside the native path (no unit above u) raises stage04.Unsupported, and the runner then gives the
   executor's result; XOL_STAGE04=executor selects the executor.
3. Agreement at 10,000 iterations (the executor needs more than 6 GB there, so the comparison is with the references):
   Designer 2026.2's own stage-04 run (tests/reconciliation/designer_twin/stage04, on Designer's 04a grid),
   tests/twin_stage04.py (numpy oracle) on the native output, and the clean-room rebuild of tests/independent on every
   parameter pack (exact seeded run: recovery and above-M recovery by iteration).
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import os
import pickle
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/alteryx"))
sys.path.insert(0, str(ROOT / "tools/native"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests/independent"))
import xol_common as C  # noqa: E402
import build_stage00, build_stage01, build_stage02, build_stage03, build_stage04, build_stage04a  # noqa: E401,E402
import sim  # noqa: E402
import stage04 as N  # noqa: E402

OUT = ROOT / "data/output"
CTL0 = ROOT / C.SYNTH.control
TWIN = ROOT / "tests/reconciliation/designer_twin/stage04"
PACKS = ROOT / "tests/independent/packs"
STAGE_FILES = ["res_out_fit", "sim_gross", "sim_iterations", "res_out_sim_quantiles", "res_out_sim_summary",
               "sim_summary", "chk_04", "rec_04"]
CSV_FILES = ["results/out_fit.csv", "results/out_sim_iterations.csv", "results/out_sim_quantiles.csv",
             "results/out_sim_summary.csv", "checks/checks_04.csv", "checks/recon_04.csv"]
results: list[tuple[str, bool]] = []
timings: list[tuple[str, int, float, float]] = []


def check(name, ok):
    results.append((name, bool(ok)))
    if not ok:
        print(f"  FAIL {name}", flush=True)


# ------------------------------------------------------------------------------------------- variants
def set_rows(ws, edits: dict, col=3):
    """Params sheet: column A = id, column C = value."""
    for r in range(5, ws.max_row + 1):
        if ws.cell(r, 1).value in edits:
            ws.cell(r, col).value = edits[ws.cell(r, 1).value]


def edit_layers(ws, edits: dict):
    """Layers sheet: {(cover, option_id): {column: value}}."""
    hdr = [c.value for c in ws[4]]
    for r in range(5, ws.max_row + 1):
        k = (ws.cell(r, 1).value, ws.cell(r, 2).value)
        for col, val in edits.get(k, {}).items():
            ws.cell(r, hdr.index(col) + 1).value = val


def make_control(name, params=None, layers=None, base_edits=True) -> tuple[Path, C.Variant]:
    out = OUT / name
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    ctl = out / f"XoL_Control_{name}.xlsx"
    shutil.copy(CTL0, ctl)
    wb = load_workbook(ctl)
    p = {"iterations": "500"}
    if base_edits:                                          # as test_stage04: annual AAD on option 1, reinstatement on 2
        lay = {("PP", 1): {"aad": 1_000_000}, ("PP", 2): {"reinstatements_n": 1, "reinstatement_cost_pct": 1.0}}
        edit_layers(wb["Layers"], lay)
    p.update(params or {})
    set_rows(wb["Params"], p)
    if layers:
        edit_layers(wb["Layers"], layers)
    wb.save(ctl)
    v = C.Variant(name.upper(), out / "wf", "..\\..\\..\\..\\", f"data/output/{name}/{ctl.name}",
                  "data/synthetic/line", "data/synthetic/claimant", f"data/output/{name}")
    return out, v


def pipeline(name, adapter="line", params=None, layers=None, base=None, base_edits=True, fit=True):
    """Stages 00-04a in the executor. base: a folder whose stage 01-02 files are reused (00, 03, 04a rerun)."""
    out, v = make_control(name, params, layers, base_edits)
    if base is not None:
        for d in ("stage", "results", "checks"):
            shutil.copytree(base / d, out / d)
        steps = [build_stage00.build(v), build_stage03.build(v)]
    else:
        ad = {"line": build_stage01.build_line, "claimant": build_stage01.build_claimant, "ete": build_stage01.build_ete}
        steps = [build_stage00.build(v), ad[adapter](v), build_stage02.build(v), build_stage03.build(v)]
    if fit:
        steps.append(build_stage04a.build(v))
    for b in steps:
        sim.run(b)
        if [m for m in sim.MESSAGES if m[0] == "Error"]:
            raise RuntimeError(f"{name}: {b.stem} stopped: {sim.MESSAGES}")
    return out, v


def load(p: Path):
    with open(p, "rb") as f:
        return pickle.load(f)


def compare(a: Path, b: Path) -> tuple[bool, float, float, list[str]]:
    """Every stage-04 output of folder a vs folder b: (identical, max abs diff, max rel diff, notes)."""
    ok, mabs, mrel, notes = True, 0.0, 0.0, []
    for n in STAGE_FILES:
        fa, ra = load(a / "stage" / f"{n}.yxdb")
        fb, rb = load(b / "stage" / f"{n}.yxdb")
        if list(fa.items()) != list(fb.items()) or len(ra) != len(rb):
            ok = False
            notes.append(f"{n}: fields or row count differ ({len(ra)} vs {len(rb)})")
            continue
        bad = 0
        for x, y in zip(ra, rb):
            for f in fa:
                vx, vy = x[f], y[f]
                if type(vx) is type(vy) and (vx == vy or (isinstance(vx, float) and math.isnan(vx) and math.isnan(vy))):
                    continue
                bad += 1
                if isinstance(vx, float) and isinstance(vy, float):
                    d = abs(vx - vy)
                    mabs, mrel = max(mabs, d), max(mrel, d / max(abs(vx), abs(vy)))
        if bad:
            ok = False
            notes.append(f"{n}: {bad} values differ")
    for n in CSV_FILES:
        if (a / n).read_bytes() != (b / n).read_bytes():
            ok = False
            notes.append(f"{n}: CSV text differs")
    return ok, mabs, mrel, notes


def both(name, v, out, expect_codes=(), expect_stop=False):
    """Stage 04 by the executor (in out) and natively (in a copy): identical outputs and messages."""
    nat = OUT / f"{name}_native"
    shutil.rmtree(nat, ignore_errors=True)
    shutil.copytree(out, nat)
    t = time.time()
    sim.run(build_stage04.build(v))
    t_exec = time.time() - t
    m_exec, w_exec = list(sim.MESSAGES), list(sim.WARNINGS)
    t = time.time()
    m_nat = N.run(nat)
    t_nat = time.time() - t
    n_it = load(out / "stage/ctl_params.yxdb")[1][0]["iterations"]
    timings.append((name, n_it, t_exec, t_nat))
    ok, mabs, mrel, notes = compare(out, nat)
    codes = {r["code"] for r in load(nat / "stage/chk_04.yxdb")[1]}
    check(f"{name}: native = executor, every output (max abs diff {mabs:g}, max rel diff {mrel:g})"
          + (f" {notes}" if notes else ""), ok)
    check(f"{name}: same messages ({len(m_nat)} Error), no executor warning", m_exec == m_nat and not w_exec)
    for c in expect_codes:
        check(f"{name}: check {c} raised", c in codes)
    check(f"{name}: {'a' if expect_stop else 'no'} STOP", bool([m for m in m_nat if m[0] == "Error"]) == expect_stop)
    return nat


# ------------------------------------------------------------------------------------------- 1. equality
def equality():
    base, _ = pipeline("tn_base")                           # kept as it is: the source of the variants' stage 01-02 files
    out, v = pipeline("tn_line", base=base)
    both("tn_line", v, out)
    variants = [
        ("tn_negbin_native", {"freq_model": "NEGBIN", "severity_source": "NATIVE"}, None, ()),
        ("tn_gpd", {"fit_selected": "GPD"}, None, ()),
        ("tn_lognormal", {"fit_selected": "LOGNORMAL"}, None, ()),
        ("tn_gamma", {"fit_selected": "GAMMA"}, None, ()),
        ("tn_weibull", {"fit_selected": "WEIBULL"}, None, ()),
        ("tn_seed777", {"seed": "777"}, None, ()),
        ("tn_terms", {}, {("PP", 1): {"aal": 600000, "co_participation_pct": 0.2, "aad": None},
                          ("PP", 2): {"reinstatements_n": None, "reinstatement_cost_pct": 0.5},
                          ("PP", 3): {"reinstatements_n": 2, "reinstatement_cost_pct": 1.0, "reinst_pro_rata_time": 1},
                          ("PP", 4): {"aad": 200000, "aal": 5_000_000, "label": None}}, ("S06",)),
        ("tn_credibility", {"full_cred_n": "1000", "complement_source": "BENCHMARK", "benchmark_rate": "120"}, None, ()),
        ("tn_fewclaims", {"fit_cutoff": "450000", "below_cutoff": "IGNORE"}, None, ("S04", "S05", "S09")),
        ("tn_ignore", {"fit_cutoff": "150000", "below_cutoff": "IGNORE", "freq_model": "NEGBIN"}, None,
         ("S03", "S05", "S09")),
        ("tn_empirical", {"fit_cutoff": "150000", "below_cutoff": "EMPIRICAL"}, None, ("S10",)),
        ("tn_emptyband", {"fit_cutoff": "75200", "below_cutoff": "EMPIRICAL"}, None, ("S11",)),
        ("tn_2000", {"iterations": "2000"}, None, ()),
    ]
    for name, params, layers, codes in variants:
        out, v = pipeline(name, params=params, layers=layers, base=base)
        both(name, v, out, codes)
    out, v = pipeline("tn_stop", params={"fit_cutoff": "150000", "below_cutoff": "STOP"}, base=base)
    both("tn_stop", v, out, ("S08",), expect_stop=True)
    out, v = pipeline("tn_lambda700", params={"fit_cutoff": "5000", "iterations": "50"}, base=base)
    both("tn_lambda700", v, out, ("S02",), expect_stop=True)
    out, v = pipeline("tn_stale", base=base)                # stage 03 after 04a empties the severity table
    sim.run(build_stage03.build(v))
    both("tn_stale", v, out, ("S07",), expect_stop=True)
    for ad in ("claimant", "ete"):
        out, v = pipeline(f"tn_{ad}", adapter=ad, params={"iterations": "300"})
        both(f"tn_{ad}", v, out)
    return base


# ------------------------------------------------------------------------------------------- 2. fallback
def fallback(base):
    import run_python
    out, v = pipeline("tn_nofit", params={"fit_cutoff": "100000000", "severity_source": "NATIVE"}, base=base, fit=False)
    try:
        N.run(out)
        check("no unit above u: native raises Unsupported", False)
    except N.Unsupported as e:
        check(f"no unit above u: native raises Unsupported ({e})", True)
    wf = build_stage04.build(v)
    ref = OUT / "tn_nofit_ref"
    shutil.rmtree(ref, ignore_errors=True)
    shutil.copytree(out, ref)
    try:
        sim.run(build_stage04.build(make_variant_like(v, "tn_nofit_ref")))
        ref_msgs = list(sim.MESSAGES)
        ref_err = None
    except sim.SimError as e:
        ref_msgs, ref_err = None, str(e)
    try:
        msgs, warns, note = run_python.run_stage(sim, "04", wf, out)
        err = None
    except sim.SimError as e:
        msgs, note, err = None, "", str(e)
    same = (err is None) == (ref_err is None) and (err is not None or (msgs == ref_msgs and compare(out, ref)[0]))
    check(f"fallback: the runner gives the executor's result ({note or err[:60]})", same)
    # XOL_STAGE04=executor: the executor runs stage 04
    out2, v2 = pipeline("tn_env", base=base)
    os.environ[run_python.STAGE04_ENV] = "executor"
    try:
        msgs, warns, note = run_python.run_stage(sim, "04", build_stage04.build(v2), out2)
    finally:
        del os.environ[run_python.STAGE04_ENV]
    check(f"XOL_STAGE04=executor: executor used ({note})", "executor" in note)
    nat = OUT / "tn_env_native"
    shutil.rmtree(nat, ignore_errors=True)
    shutil.copytree(out2, nat)
    msgs2, _, note2 = run_python.run_stage(sim, "04", build_stage04.build(make_variant_like(v2, "tn_env_native")), nat)
    check(f"default: native used ({note2}), same result as the executor", "native" in note2 and msgs2 == msgs and
          compare(out2, nat)[0])


def make_variant_like(v, name):
    return C.Variant(name.upper(), OUT / name / "wf", "..\\..\\..\\..\\", v.control, v.line_dir, v.claimant_dir,
                     f"data/output/{name}")


# ------------------------------------------------------------------------------------------- 3. 10,000 iterations
def read_csv(p: Path, opener=open):
    with opener(p, "rt", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def recov(rows, field="recovery"):
    """{option_id: recovery by iteration}"""
    out = {}
    for r in sorted(rows, key=lambda r: (int(float(r["option_id"])), int(float(r["iter"])))):
        out.setdefault(int(float(r["option_id"])), []).append(float(r[field]))
    return {k: np.array(v) for k, v in out.items()}


def maxrel(a, b):
    return float(np.max(np.abs(a - b) / np.maximum(np.abs(b), 1.0)))


def at10k():
    import indep_rebuild as IR
    # the synthetic book as shipped (10,000 iterations), stages 00-04a in data/output/synth (as test_designer_twin)
    synth = OUT / "synth"
    for b in (build_stage00.build(C.SYNTH), build_stage01.build_line(C.SYNTH), build_stage02.build(C.SYNTH),
              build_stage03.build(C.SYNTH), build_stage04a.build(C.SYNTH)):
        sim.run(b)
    t = time.time()
    N.run(synth)
    t_nat = time.time() - t
    timings.append(("synth (own 04a grid)", 10000, float("nan"), t_nat))
    # twin_stage04.py: the numpy oracle on the native output
    tw = OUT / "tn_twin_native"
    shutil.rmtree(tw, ignore_errors=True)
    tw.mkdir(parents=True)
    for f in ("out_sim_summary.csv", "out_sim_iterations.csv", "out_sim_quantiles.csv"):
        shutil.copy(synth / "results" / f, tw / f)
    r = subprocess.run([sys.executable, str(ROOT / "tests/twin_stage04.py"), str(tw)], capture_output=True, text=True)
    check(f"twin_stage04.py on the native 10,000-iteration output: "
          f"{sum(1 for x in r.stdout.splitlines() if x.startswith('PASS'))} PASS", r.returncode == 0)
    # Designer's own run on Designer's 04a grid: inject the grid, run natively, compare with Designer's CSVs
    dsg = OUT / "tn_designer10k"
    shutil.rmtree(dsg, ignore_errors=True)
    shutil.copytree(synth, dsg)
    grid = read_csv(TWIN / "out_sev_table.csv")
    f, rows = load(dsg / "stage/sev_table.yxdb")
    rows = [{"k": int(float(g["k"])), "lx0": float(g["lx0"]), "lx1": float(g["lx1"]), "dist": g["dist"]} for g in grid]
    with open(dsg / "stage/sev_table.yxdb", "wb") as fh:
        pickle.dump((f, rows), fh)
    t = time.time()
    N.run(dsg)
    timings.append(("synth (Designer's 04a grid)", 10000, float("nan"), time.time() - t))
    mine = recov(read_csv(dsg / "results/out_sim_iterations.csv"))
    theirs = recov(read_csv(TWIN / "out_sim_iterations.csv.gz", gzip.open))
    worst = max(maxrel(mine[o], theirs[o]) for o in theirs)
    # clean-room rebuild, designer10k pack (Designer's 04a fit): every iteration of every layer
    indep("designer10k", IR, read_csv(dsg / "results/out_sim_iterations.csv"),
          json.loads((PACKS / "designer10k.json").read_text()))
    check(f"Designer 2026.2's 10,000-iteration run = native, every iteration of {len(theirs)} layers "
          f"(max rel diff {worst:.1e}, Designer's CSV precision)", set(mine) == set(theirs) and worst < 1e-12)
    sm = {int(float(r["option_id"])): r for r in read_csv(dsg / "results/out_sim_summary.csv")}
    sd = {int(float(r["option_id"])): r for r in read_csv(TWIN / "out_sim_summary.csv")}
    cols = [c for c in ("mean_recovery", "sd_recovery", "p50", "p90", "p99", "p995", "mean_above_max_observed",
                        "expected_loss_blended", "lambda", "alpha", "phi") if c in next(iter(sd.values()))]
    w = max(abs(float(sm[o][c]) - float(sd[o][c])) / max(abs(float(sd[o][c])), 1.0) for o in sd for c in cols)
    check(f"Designer's 10,000-iteration summary = native ({len(cols)} statistics per layer, max rel diff {w:.1e})",
          w < 1e-12)
    # the other packs: 2,000 iterations, each run through 00-04a with the pack's parameters, native stage 04
    plain = None
    for p in sorted(PACKS.glob("*.json"), key=lambda p: p.stem != "base"):        # base first: its stage 01-02 files
        pack = json.loads(p.read_text())                                            # serve the other packs
        if pack["variant"] == "designer10k":
            continue
        prm = {k: v for k, v in pack["params_changed"].items() if k != "note"}
        if "fit_cutoff" in prm:
            prm["below_cutoff"] = "IGNORE"      # packs predate 0.9.0: layers attaching below u were left out
        out, _ = pipeline(f"tn_pack_{pack['variant']}", params=prm, base_edits=False, base=plain)
        plain = plain or out
        t = time.time()
        N.run(out)
        timings.append((f"pack {pack['variant']}", pack["iterations"], float("nan"), time.time() - t))
        indep(pack["variant"], IR, read_csv(out / "results/out_sim_iterations.csv"), pack, out=out)


def indep(name, IR, nat_rows, pack, tol=1e-9, out=None):
    """The clean-room exact run of a pack vs the native per-iteration output."""
    if out is not None:                     # the pack describes this run: same frequency, threshold, caps
        s = read_csv(out / "results/out_sim_summary.csv")[0]
        same = (abs(float(s["lambda"]) / pack["lambda"] - 1) < 1e-12 and float(s["u_fit"]) == pack["u_fit"]
                and int(s["iterations"]) == pack["iterations"] and s["freq_dist"] == pack["freq_dist"])
        check(f"pack {name}: the run reproduces the pack (lambda {pack['lambda']:.4f}, u {pack['u_fit']:.0f})", same)
    sev = IR.build_severity(pack)
    res, _, _ = IR.exact_run(pack, sev)
    mine, above = recov(nat_rows), recov(nat_rows, "recovery_aboveM")
    w = max(max(maxrel(mine[o], res[o][0]), maxrel(above[o], res[o][1])) for o in res)
    check(f"pack {name}: clean-room rebuild = native, every iteration of {len(res)} layers "
          f"(recovery and above-M, max rel diff {w:.1e})", set(res) <= set(mine) and w < tol)


# ------------------------------------------------------------------------------------------- misc
def unit():
    """Building blocks: the grouped compensated sum equals Python's sum; the MINSTD jump equals the sequence."""
    rng = np.random.default_rng(7)
    vals = np.concatenate([rng.pareto(1.2, 5000) * 1e5, rng.random(3000) * 1e-3, np.zeros(50)])
    rng.shuffle(vals)
    cnt = rng.integers(0, 40, 400)
    cnt[-1] = len(vals) - cnt[:-1].sum() if cnt[:-1].sum() < len(vals) else 0
    cnt = cnt[np.cumsum(cnt) <= len(vals)]
    st = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    got = N._gsum(vals, st, cnt)
    exp = [float(sum(vals[s:s + c].tolist())) if c else 0.0 for s, c in zip(st, cnt)]
    check("grouped compensated sum = Python's sum (bit for bit)", got.tolist() == exp)
    x, seq = 987654, []
    for _ in range(10000):
        x = (48271 * x) % 2147483647
        seq.append(x / 2147483647)
    check("MINSTD by blocks = the sequential stream (bit for bit)", N._minstd(987654, 10000).tolist() == seq)
    sys.path.insert(0, str(ROOT / "tools/python"))
    import sevfit
    check("severity grid constants = sevfit's", (N.KSEV, N.DECADES) == (sevfit.K_PER_DECADE, sevfit.DECADES))


def main() -> int:
    t0 = time.time()
    unit()
    base = equality()
    fallback(base)
    at10k()
    for n, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {n}")
    print("\nstage 04 timing (seconds):")
    for name, it, te, tn in timings:
        print(f"  {name:<32} {it:>6} iterations   executor {te:7.2f}   native {tn:6.2f}")
    print(f"({time.time() - t0:.0f} s)")
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
