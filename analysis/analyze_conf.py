"""Pre-specified analysis of the confirmatory runs (see HYPOTHESES.md).
Usage: python analyze_conf.py study_conf.csv
Requires pandas, numpy, scipy."""
import sys, re, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
from scipy import stats

CLASSES = ('v1L', 'v1H', 'v2L', 'v2H')

def completion(row):
    """Mean over sub-cloudlets of (waiting + execution), weighted by the number of sub-cloudlets."""
    num = den = 0.0
    for d in range(1, 6):
        for c in CLASSES:
            n = row.get(f'DC{d}_{c}_n', 0)
            if n and n > 0:
                num += n * (row.get(f'DC{d}_{c}_wait', 0) + row.get(f'DC{d}_{c}_exec', 0)); den += n
    return num / den, den

def holm(p):
    p = np.asarray(p, float); order = np.argsort(p); m = len(p); adj = np.empty(m); run = 0.0
    for i, j in enumerate(order):
        run = max(run, (m - i) * p[j]); adj[j] = min(1.0, run)
    return adj

def main(path):
    d = pd.read_csv(path)
    m = d.label.str.extract(r'R5_CONF_([ABC])_(CDC|ORT)_(SAKMLB|NKMLB90|ESCEL_CAP90)')
    d['topo'], d['br'], d['alg'] = m[0], m[1], m[2]
    d = d[d.topo.notna()].copy()
    ct = d.apply(completion, axis=1, result_type='expand'); d['CT'] = ct[0]; d['chunks'] = ct[1]
    print(f'runs: {len(d)}; seeds: {sorted(d.seed.unique())}')
    chk = d.groupby(['topo', 'br', 'seed']).chunks.nunique()
    print('sub-cloudlet counts identical across algorithms in every cell:', bool((chk == 1).all()))
    tests = [('H1', t, b, 'NKMLB90') for t in 'ABC' for b in ('CDC', 'ORT')] + \
            [('H2', t, b, 'ESCEL_CAP90') for t in 'AB' for b in ('CDC', 'ORT')]
    rows = []
    for h, t, b, other in tests:
        g = d[(d.topo == t) & (d.br == b)]
        P = g.pivot(index='seed', columns='alg', values='CT')
        if 'SAKMLB' not in P or other not in P or len(P.dropna()) < 2:
            rows.append((h, t, b, other, np.nan, np.nan, np.nan, np.nan, 0, 0)); continue
        x = (P['SAKMLB'] - P[other]).dropna().values; n = len(x)
        mu = x.mean(); se = x.std(ddof=1) / np.sqrt(n); tcrit = stats.t.ppf(.975, n - 1)
        p = stats.ttest_1samp(x, 0).pvalue
        rows.append((h, t, b, other, mu, mu - tcrit * se, mu + tcrit * se, p, int((x < 0).sum()), n))
    R = pd.DataFrame(rows, columns=['hyp', 'topology', 'broker', 'vs', 'diff_ms', 'ci_lo', 'ci_hi', 'p', 'seeds_lower', 'n_seeds'])
    ok = R.p.notna()
    R.loc[ok, 'p_holm'] = holm(R.loc[ok, 'p'].values)
    R['confirmed'] = (R.p_holm < 0.05) & (R.diff_ms < 0)
    pd.set_option('display.width', 200)
    print(R.round(4).to_string(index=False))
    print(f"\nconfirmed {int(R.confirmed.sum())} of {len(R)} pre-specified tests")
    # descriptive only: first-response metrics, same pairs
    print('\nDescriptive (no claim): mean paired difference SAKMLB minus comparison, ms')
    for h, t, b, other in tests:
        g = d[(d.topo == t) & (d.br == b)]
        out = []
        for col in ('RT_avg', 'DCPT_avg'):
            P = g.pivot(index='seed', columns='alg', values=col)
            out.append(f"{col[:-4]} {(P['SAKMLB'] - P[other]).mean():+.2f}" if other in P else f"{col[:-4]} n/a")
        print(f'  {h} {t} {b} vs {other}: ' + ', '.join(out))

if __name__ == '__main__':
    main(sys.argv[1])
