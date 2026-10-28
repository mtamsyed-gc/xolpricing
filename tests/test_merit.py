"""Merit v7 outputs into the XoL tool, read as written (contract test).

Fixture: tests/fixtures/merit_v7 = Merit's synthetic final outputs (ete-dashboard side_tools/merit/out/alteryx/csv,
v7.6.0, contract IP4-10) with company names replaced (tools/synth/merit_fixture.py); every row and column as Merit writes them.
Runs 00 -> 01c -> 02 in the simulator and checks against an independent pandas computation on the fixture:
  - members: one row per life-period (LifeKey x UWY; a life renewing into 2026 keeps two rows, no A14 stop);
  - claims: InExperience = 1, not rejected, treated on or before the manifest's valuation date, matched on
    LifeKey x UWY; paid / OS totals; the claim treated after the valuation date excluded with an A04 FLAG;
  - exposure: earned life-years and premium from fact_member_month up to the valuation date; written premium;
  - payer filter: both payers pooled -> A16 FLAG per payer; ete_payer_filter = one payer -> only its rows, no A16;
  - stage 02 runs on the result.
When an ete-dashboard clone sits next to this repository, the committed fixture must equal a fresh build from
Merit's current outputs: a Merit release that changes its output format fails here, not on a laptop.
"""
from __future__ import annotations

import pickle
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/alteryx"))
sys.path.insert(0, str(ROOT / "tools/synth"))
import xol_common as C  # noqa: E402
import build_stage00, build_stage01, build_stage02  # noqa: E401,E402
import sim  # noqa: E402
import merit_fixture  # noqa: E402

FIX = ROOT / "tests/fixtures/merit_v7"
ETE = ROOT.parent / "ete-dashboard"
results = []


def ok(name, cond):
    results.append((name, bool(cond)))


def close(name, got, exp, tol=1e-9):
    good = np.allclose(np.asarray(got, float), np.asarray(exp, float), rtol=tol, atol=1e-6)
    results.append((name, bool(good)))
    if not good:
        print(f"  MISMATCH {name}: got {got} exp {exp}")


def run(name, payer="", ete=None):
    out = ROOT / f"data/output/{name}"
    out.mkdir(parents=True, exist_ok=True)
    ctl = merit_fixture.test_control(ROOT / C.SYNTH.control, out / f"XoL_Control_{name}.xlsx", payer)
    v = C.Variant(name.upper(), out / "wf", "..\\..\\..\\..\\", str(ctl.relative_to(ROOT)).replace("\\", "/"),
                  "data/synthetic/line", "data/synthetic/claimant", f"data/output/{name}",
                  str((ete or FIX).relative_to(ROOT)).replace("\\", "/"))
    for b in (build_stage00.build(v), build_stage01.build_ete(v), build_stage02.build(v)):
        sim.run(b)
        ok(f"{name} {b.stem}: no STOP", not [m for m in sim.MESSAGES if m[0] == "Error"])
    return out


def load(out, n):
    with open(out / "stage" / f"{n}.yxdb", "rb") as f:
        fields, rows = pickle.load(f)
    return pd.DataFrame(rows, columns=list(fields))


def oracle(payer=None, fix=FIX):
    d = pd.read_csv(fix / "dim_life.csv", dtype={"UWY": str})
    m = pd.read_csv(fix / "fact_member_month.csv", dtype={"UWY": str})
    c = pd.read_csv(fix / "fact_claims.csv", dtype={"UWY": str})
    mf = pd.read_csv(fix / "run_manifest_claims.csv").set_index("Key").Value
    val = pd.Timestamp(mf["valuation_date"])
    if payer:
        d, c = d[d["Payer Name"] == payer], c[c["Payer Name"] == payer]
    keys = set(zip(d.LifeKey.str.upper(), d.UWY))
    m = m[[k in keys for k in zip(m.LifeKey.str.upper(), m.UWY)]]
    m = m[pd.to_datetime(m.MonthStart) <= val]
    ce = c[(c.InExperience == 1) & (c.StatusClass.str.upper() != "REJECTED")]
    after = ce[pd.to_datetime(ce.TreatmentDate) > val]
    ce = ce[pd.to_datetime(ce.TreatmentDate) <= val]
    ce = ce[[k in keys for k in zip(ce.LifeKey.str.upper(), ce.UWY)]]
    return dict(d=d, m=m, c=ce, after=after, val=val, n_payers=d["Payer Name"].nunique(),
                multi=(d.groupby("LifeKey").UWY.nunique() > 1).sum())


def check(out, e, tag):
    mem, cl, ex = load(out, "int_members"), load(out, "int_claims"), load(out, "int_exposure")
    chk = pd.read_csv(out / "checks/checks_01c.csv")
    ok(f"{tag}: members = dim_life life-periods ({len(mem)}; {e['multi']} life renewing across UWYs kept twice)",
       len(mem) == len(e["d"]) and mem.groupby("member_id").policy_id.nunique().max() >= (2 if e["multi"] else 1))
    close(f"{tag}: written premium", [mem.written_premium_q.sum()], [e["d"].WrittenPrem_Gross.sum()])
    ok(f"{tag}: claims in experience, on or before {e['val'].date()}, matched on LifeKey x UWY ({len(cl)})",
       len(cl) == len(e["c"]) and set(cl.claim_key) == set(e["c"].ClaimKey.str.strip()))
    close(f"{tag}: paid, OS", [cl.amount_paid_q.sum(), cl.amount_os_q.sum()], [e["c"].Paid.sum(), e["c"].OS.sum()])
    close(f"{tag}: earned life-years, earned premium to the valuation date",
          [ex.earned_ly.sum(), ex.earned_premium.sum()], [e["m"].EarnedLY_Gross.sum(), e["m"].EarnedPrem_Gross.sum()])
    a04 = chk[chk.code == "A04"]
    ok(f"{tag}: {len(e['after'])} claim after the valuation date -> A04 FLAG, run continues",
       (len(e["after"]) == 0 and a04.empty) or (len(a04) == 1 and (a04.severity == "FLAG").all()))
    ok(f"{tag}: no STOP check", not (chk.severity == "STOP").any())
    return chk


def fixture_current():
    src = ETE / "side_tools/merit/out/alteryx/csv"
    if not src.exists():
        print("SKIP  fixture vs Merit's current outputs (no ete-dashboard clone next to this repository)")
        return
    with tempfile.TemporaryDirectory() as t:
        merit_fixture.build(src, Path(t))
        same = all((Path(t) / f"{f}.csv").read_bytes() == (FIX / f"{f}.csv").read_bytes() for f in merit_fixture.FILES)
    rev = subprocess.run(["git", "-C", str(ETE), "log", "-1", "--format=%h %s", "--", "side_tools/merit/out"],
                         capture_output=True, text=True).stdout.strip()
    ok(f"fixture = fresh build from Merit's outputs in ../ete-dashboard ({rev[:60]})", same)


def main() -> int:
    fixture_current()
    e = oracle()
    chk = check(run("tmerit"), e, "both payers")
    ok(f"both payers: A16 FLAG for each of the {e['n_payers']} payers",
       (chk.code == "A16").sum() == e["n_payers"] and (chk[chk.code == "A16"].severity == "FLAG").all())
    p = sorted(e["d"]["Payer Name"].unique())[0]
    e1 = oracle(p)
    chk1 = check(run("tmerit1", payer=p), e1, f"payer filter '{p}'")
    ok(f"payer filter: no A16, only {p}'s {len(e1['d'])} life-periods", not (chk1.code == "A16").any())
    # a claims extract running past the valuation date: one in-experience claim moved to 2026-08-15
    late = ROOT / "data/output/tmerit2/ete"
    late.mkdir(parents=True, exist_ok=True)
    for f in merit_fixture.FILES:
        shutil.copy(FIX / f"{f}.csv", late / f"{f}.csv")
    fc = pd.read_csv(late / "fact_claims.csv", dtype=str, keep_default_na=False)
    i = fc.index[fc.InExperience == "1"][0]
    fc.loc[i, "TreatmentDate"] = "2026-08-15"
    fc.to_csv(late / "fact_claims.csv", index=False, lineterminator="\n")
    e2 = oracle(fix=late)
    chk2 = check(run("tmerit2", ete=late), e2, "claim after the valuation date")
    ok(f"late claim: excluded and flagged ({chk2[chk2.code == 'A04'].message.iloc[0][:60]}...)",
       len(e2["after"]) == 1 and (chk2.code == "A04").sum() == 1)
    ex = load(ROOT / "data/output/tmerit", "exp_exposure_ty")
    ok(f"stage 02 on Merit data: treaty years {sorted(ex.ty.unique())}", set(ex.ty) >= {2025, 2026})
    for n, good in results:
        print(f"{'PASS' if good else 'FAIL'}  {n}")
    return 0 if all(g for _, g in results) else 1


if __name__ == "__main__":
    sys.exit(main())
