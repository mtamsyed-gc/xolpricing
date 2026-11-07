"""Run stage 00-04 of the synthetic book under parameter variants; save the outputs and a parameter pack per variant."""
import json, shutil, subprocess, sys, csv
from pathlib import Path
from openpyxl import load_workbook
ROOT = Path('ROOT'); OUT = ROOT/'data/output/synth'; CTL = ROOT/'data/synthetic/XoL_Control_synthetic.xlsx'
DEST = Path('INDEP/variants')
VARIANTS = {
  'base':      {'iterations': '2000', 'seed': '12345'},
  'seed777':   {'iterations': '2000', 'seed': '777'},
  'gpd':       {'iterations': '2000', 'seed': '12345', 'fit_selected': 'GPD'},
  'lognormal': {'iterations': '2000', 'seed': '12345', 'fit_selected': 'LOGNORMAL'},
  'negbin':    {'iterations': '2000', 'seed': '12345', 'freq_model': 'NEGBIN'},
  'cutoff150': {'iterations': '2000', 'seed': '12345', 'fit_cutoff': '150000'},
  'native':    {'iterations': '2000', 'seed': '12345', 'severity_source': 'NATIVE'},
}
ORIG = CTL.read_bytes()
def set_params(vals):
    CTL.write_bytes(ORIG)
    wb = load_workbook(CTL); ws = wb['Params']
    for r in range(5, ws.max_row+1):
        pid = ws.cell(r,1).value
        if pid in vals: ws.cell(r,3).value = vals[pid]
    wb.save(CTL)
def layers():
    wb = load_workbook(CTL, read_only=True, data_only=True); ws = wb['Layers']
    rows = list(ws.iter_rows(min_row=4, values_only=True)); head = rows[0]
    return [dict(zip(head, r)) for r in rows[1:] if r[0] == 'PP' and r[head.index('active')] == 1]
def rd(p):
    with open(p, encoding='utf-8-sig') as f: return list(csv.DictReader(f))
for name, vals in VARIANTS.items():
    print('==', name, vals, flush=True)
    set_params(vals)
    last = '04'
    r = subprocess.run([sys.executable, str(ROOT/'tools/run_python.py'), 'synth', '01a', '00', last], capture_output=True, text=True)
    d = DEST/name; d.mkdir(parents=True, exist_ok=True)
    (d/'run.log').write_text(r.stdout + r.stderr)
    if r.returncode != 0:
        print('  FAILED', r.stdout[-400:]); continue
    for f in ('out_sim_summary.csv','out_sim_iterations.csv','out_sim_quantiles.csv','out_fit.csv','out_fit_candidates.csv','out_sev_table.csv','out_large_claims.csv'):
        shutil.copy(OUT/'results'/f, d/f)
    shutil.copy(OUT/'checks/checks_04.csv', d/'checks_04.csv'); shutil.copy(OUT/'checks/checks_04a.csv', d/'checks_04a.csv')
    summ = rd(d/'out_sim_summary.csv')[0]
    u = float(summ['u_fit'])
    units = [x for x in rd(d/'out_large_claims.csv') if float(x['adj']) > u]
    units.sort(key=lambda x: x['acc_key'])
    sel = [c for c in rd(d/'out_fit_candidates.csv') if c['selected'] == '1']
    sel = sel[0] if sel else None
    pack = {
      'variant': name, 'params_changed': vals,
      'seed': int(float(vals['seed'])), 'iterations': int(summ['iterations']),
      'lambda': float(summ['lambda']), 'phi': float(summ['phi']), 'freq_dist': summ['freq_dist'],
      'u_fit': u, 'max_observed': float(summ['max_observed']), 'severity_source': vals.get('severity_source', 'PYTHON'),
      'sev_dist': summ['sev_dist'], 'pareto_alpha_native': float(summ['alpha']),
      'selected_candidate': ({k: sel[k] for k in ('dist','p1_name','p1','p2_name','p2','u_fit','max_observed','n_fit','n_uncensored')} if sel else None),
      'n_fit': len(units), 'caps_in_order': [float(x['cap']) for x in units],
      'layers': [{k: l[k] for k in ('option_id','layer','label','limit_basis','ap','limit','aal','aad','co_participation_pct','reinstatements_n','reinstatement_cost_pct')} for l in layers()],
    }
    (d/'param_pack.json').write_text(json.dumps(pack, indent=1, default=str))
    print('  ok: lambda', round(pack['lambda'],3), pack['freq_dist'], pack['sev_dist'], 'n_fit', pack['n_fit'], 'mean opt1', round(float(summ['mean_recovery'])))
CTL.write_bytes(ORIG)
