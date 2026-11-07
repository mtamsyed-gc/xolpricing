"""Reconcile the tool's stage-04 outputs with the independent rebuild, per variant and layer."""
import json
from pathlib import Path
import numpy as np, pandas as pd

V = Path('INDEP/variants'); C = Path('INDEP/clean')
rows = []
for d in sorted(V.iterdir()):
    name = d.name
    if not (C / name / 'independent_summary.csv').exists():
        rows.append({'variant': name, 'note': 'no independent result'}); continue
    pack = json.load(open(d / 'param_pack.json'))
    tool = pd.read_csv(d / 'out_sim_summary.csv', encoding='utf-8-sig').set_index('option_id')
    its_t = pd.read_csv(d / 'out_sim_iterations.csv', encoding='utf-8-sig')
    ind = pd.read_csv(C / name / 'independent_summary.csv').set_index('option_id')
    its_i = pd.read_csv(C / name / 'independent_iterations.csv')
    n = int(tool.iterations.iloc[0])
    for o in tool.index:
        t, i = tool.loc[o], ind.loc[o]
        a = its_t[its_t.option_id == o].sort_values('iter').recovery.to_numpy()
        b = its_i[its_i.option_id == o].sort_values('iter').recovery.to_numpy()
        exact_rel = float(np.max(np.abs(a - b) / np.maximum(np.abs(a), 1.0))) if len(a) == len(b) else np.nan
        n_diff = int(np.sum(~np.isclose(a, b, rtol=1e-9, atol=1e-6))) if len(a) == len(b) else -1
        se = i.mc_sd_sample / np.sqrt(200000)
        rows.append({
            'variant': name, 'opt': o, 'label': t.l_label, 'dist': t.sev_dist, 'freq': t.freq_dist, 'n': n,
            'tool_mean': t.mean_recovery, 'exact_mean': i.exact_mean, 'exact_maxrel': exact_rel, 'iters_differ': n_diff,
            'tool_sd': t.sd_recovery, 'exact_sd_sample': i.exact_sd_sample, 'exact_sd_pop': i.exact_sd_pop,
            'tool_p99': t.p99, 'exact_p99': i.exact_p99,
            'mc_mean': i.mc_mean, 'cf_mean': i.cf_mean, 'tool_vs_cf_%': 100 * (t.mean_recovery / i.cf_mean - 1),
            'tool_vs_cf_se': (t.mean_recovery - i.cf_mean) / (t.sd_recovery / np.sqrt(n)),
            'mc_vs_cf_se': (i.mc_mean - i.cf_mean) / se,
            'tool_aboveM': t.mean_above_max_observed, 'exact_aboveM': i.exact_mean_aboveM, 'cf_belowM': i.cf_mean_belowM,
        })
df = pd.DataFrame(rows)
pd.set_option('display.width', 250); pd.set_option('display.float_format', lambda x: f'{x:,.6g}')
print(df.to_string(index=False))
df.to_csv('INDEP/reconciliation.csv', index=False)
