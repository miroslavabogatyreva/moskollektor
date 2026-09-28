"""Replay the delivered synthetic toggle, after train_sensor_model.py has completed.

The UI uses p = 1-(1-p_real)(1-p_sim), level = max(component levels), and a
six-decimal stored score. Evaluate that exact policy on a synthetic overlay of
real and generated events; these metrics are SIMULATION-ONLY. No fitting/tuning.
Run with the same pinned environment as training. Writes combined_demo.json only.
"""
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent


def load_training():
    spec = importlib.util.spec_from_file_location('sensor_training', HERE / 'train_sensor_model.py')
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def max_level_masks(p_real, p_sim, artifact):
    """Return the actual runtime's high and high-or-watch masks."""
    real, sim = artifact['modes']['real'], artifact['modes']['sim']
    return {
        name: (p_real >= real['thresholds'][name]) | (p_sim >= sim['thresholds'][name])
        for name in ('high', 'watch')
    }


def runtime_parity(m, D, S, PRE, p_real, p_sim, artifact):
    """Use public score() on true full-precision features, not the replay matrix."""
    m.sensor_risk._MODEL = artifact
    rng = np.random.default_rng(m.SEED)
    rows = rng.choice(len(D.mt), min(1000, len(D.mt)), replace=False)
    for p, mode in ((p_real, 'real'), (p_sim, 'sim')):
        for threshold in artifact['modes'][mode]['thresholds'].values():
            rows = np.concatenate((rows, np.argsort(np.abs(p - threshold))[:30]))
    masks = max_level_masks(p_real, p_sim, artifact)
    worst_score_difference = 0.0
    for r in np.unique(rows):
        i, t = int(D.mc[r]), int(D.mt[r])
        c, kind = int(m.cid[i]), m.kinds[m.KIND[i]]
        at = m.datetime.fromtimestamp(t, m.MSK)
        starts = [m.datetime.fromtimestamp(int(s), m.MSK) for s in m.F.get(i, m.EMPTY)]
        pre = [m.datetime.fromtimestamp(int(s), m.MSK) for s in PRE.get(i, m.EMPTY)]
        passport = m.passports[c]
        eq = {'in_service': passport['in_service'], 'life': passport['life'], 'points': []}
        if c in m.CHK:
            eq['points'] = [{'kind': m.CHK[c][0], 'readings': [(d,) for d in m.CHK[c][1]]}]
        nb = tuple(round(np.expm1(D.X[r, j])) for j in (6, 7))
        real = m.sensor_risk.score(starts, None, at, nb=nb, kind=kind, mode='real')
        full = m.sensor_risk.score(starts, eq, at, nb=nb, kind=kind, mode='synthetic', pre=pre)
        expected_real = m.sensor_risk.level(p_real[r], artifact['modes']['real'])
        expected_full = 'high' if masks['high'][r] else 'watch' if masks['watch'][r] else 'normal'
        assert real['level'] == expected_real, ('real level parity', c, at, real, expected_real)
        assert full['level'] == expected_full, ('toggle level parity', c, at, full, expected_full)
        combined = 1 - (1 - p_real[r]) * (1 - p_sim[r])
        delta = max(abs(real['score'] - p_real[r]), abs(full['score'] - combined))
        worst_score_difference = max(worst_score_difference, delta)
        assert delta <= 1.1e-6, ('runtime score parity', c, at, delta)
    return {'sample_rows': len(np.unique(rows)), 'level_parity': True,
            'max_absolute_score_difference': worst_score_difference,
            'score_tolerance': 1.1e-6, 'includes_rows_nearest_both_thresholds': True}


def main():
    m = load_training()
    model_path = m.ROOT / 'backend/app/domain/sensor_model.json'
    artifact = json.loads(model_path.read_text())
    run = json.loads((HERE / 'run.json').read_text())
    model_hash = hashlib.sha256(model_path.read_bytes()).hexdigest()
    assert run.get('model_sha256') == model_hash, 'wait for matching completed training artifacts'
    assert artifact['moments'] == 'tick' and 'frozen' in artifact['version']
    m.load_data()
    failures = pd.read_csv(HERE / 'sim_failures.csv')
    precursors = pd.read_csv(HERE / 'sim_failures_precursors.csv')
    SIM = m.by_channel(zip(failures.channel_id, pd.to_datetime(failures.started_at, utc=True).astype('int64') // 10**9))
    PRE = m.by_channel(zip(precursors.channel_id, pd.to_datetime(precursors.observed_at, utc=True).astype('int64') // 10**9))
    union = {i: np.unique(np.concatenate((m.F.get(i, m.EMPTY), SIM.get(i, m.EMPTY))))
             for i in set(m.F) | set(SIM)}
    H = artifact['horizon_h']
    report = {
        'evidence': 'simulation-only retrospective overlay; not real-world sensor quality or probability calibration',
        'policy': 'actual toggle: p=1-(1-p_real)*(1-p_sim); level=max(real,sim); stored score rounded to 6 decimals',
        'model_version': artifact['version'], 'model_sha256': model_hash, 'horizon_h': H,
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'training_run_sha256': hashlib.sha256((HERE / 'run.json').read_bytes()).hexdigest(),
        'synthetic_input_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in (HERE / 'sim_failures.csv', HERE / 'sim_failures_precursors.csv')},
        'selection_policy': 'no new training, no new threshold/horizon selection',
    }
    for name, period in (('validation', m.SELECT), ('test', m.TEST)):
        valid, lo, hi = m.window_mask(m.TICKS, period, H)
        times = m.TICKS[valid]
        mc, mt = np.repeat(np.arange(m.C, dtype='int32'), len(times)), np.tile(times, m.C)
        mk = np.zeros(len(mt), dtype='int8'); mask = np.ones(len(mt), bool)
        D = m.Design(mc, mt, mk, m.real_features(mc, mt), 'real')
        S = m.Design(mc, mt, mk, m.sim_features(mc, mt, PRE), 'sim')
        pr = m.exported_proba(artifact['modes']['real'], D, mask)
        ps = m.exported_proba(artifact['modes']['sim'], S, mask)
        combined = np.round(1 - (1 - pr) * (1 - ps), 6)
        masks = max_level_masks(pr, ps, artifact)
        target = m.failures_in(union, lo, hi, H)
        # Strong causal simulation baseline: every observed warning suggests a
        # failure exactly PF days later. This never reads the true/false flag.
        pf_seconds = int(artifact['modes']['sim']['pf_days'] * 86400)
        scheduled = np.zeros(len(mt), dtype='int32')
        offsets = np.searchsorted(mc, np.arange(m.C + 1))
        for i, observations in PRE.items():
            a, b = offsets[i], offsets[i + 1]
            times_i = mt[a:b]
            upper = np.minimum(times_i, times_i + H * 3600 - pf_seconds)
            scheduled[a:b] = m.cnt(observations, times_i - pf_seconds, upper)
        synthetic_targets = m.failures_in(SIM, lo, hi, H)
        report[name] = {
            'cuts': [m.datetime.fromtimestamp(t, m.MSK).isoformat() for t in (lo, hi)],
            'candidate_rows': len(mt),
            'real_events': sum(map(len, m.failures_in(m.F, lo, hi, H).values())),
            'synthetic_events': sum(map(len, m.failures_in(SIM, lo, hi, H).values())),
            'union_events': sum(map(len, target.values())),
            'high': m.exact(mc[masks['high']], mt[masks['high']], target, H),
            'high_or_watch': m.exact(mc[masks['watch']], mt[masks['watch']], target, H),
            'high_lead_at_least_24h': m.exact(mc[masks['high']], mt[masks['high']], target, H, 86400) if H >= 24 else None,
            'ranking_stored_combined_score': m.ranked(D, mask, combined, union, target, H),
            'synthetic_timing_rule': {
                'definition': 'observed warning + fixed PF lies in (cut,cut+H]; true/false flag not used',
                'high': m.exact(mc[scheduled > 0], mt[scheduled > 0], synthetic_targets, H),
                'ranking': m.ranked(S, mask, scheduled, SIM, synthetic_targets, H),
            },
            'runtime_parity': runtime_parity(m, D, S, PRE, pr, ps, artifact),
        }
    output = HERE / 'combined_demo.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=1, allow_nan=False) + '\n')
    print(json.dumps({name: {key: report[name]['high'][key] for key in ('tp', 'fp', 'fn', 'precision', 'recall')}
                      for name in ('validation', 'test')}, indent=2))
    print(f'written {output}')


if __name__ == '__main__':
    main()
