"""Select documented successful runs and prepare model-blind review packets."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.eval_local_ollama import followup_metrics, summary

BASE = ROOT / 'experiments/model_evaluation/local_20260915'
RUNS = {'qwen': 'independent_stable', 'ax': 'independent_final', 'bllossom': 'independent_final'}

def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))

def lines(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines()]

def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def opaque(suite, model, cid):
    return hashlib.sha256(f'fixed-blind-7319:{suite}:{model}:{cid}'.encode()).hexdigest()[:12]

def collect(models, team=False):
    review = BASE / 'review'
    review.mkdir(exist_ok=True)
    packets, mapping, summaries = [], {}, {}
    for model in models:
        folder = BASE / ('team_final' if team else RUNS[model])
        cases = read(folder / 'cases.json')
        rows = lines(folder / f'{model}.jsonl')
        assert len(rows) == len(cases), (model, len(rows), len(cases))
        assert [r['id'] for r in rows] == [c['id'] for c in cases]
        for case, original in zip(cases, rows):
            row = deepcopy(original)
            if row.get('control'):
                continue
            key = opaque('team' if team else 'independent', model, row['id'])
            mapping[key] = {'model': model, 'id': row['id'], 'source': str(folder.relative_to(ROOT))}
            if team:
                packets.append({'blind_id': key, 'question_id': case['id'],
                    'question': case['question'], 'expectation': case['review_expectation'],
                    'n1_raw': [c.get('raw', c.get('error')) for c in row['n1']['calls']],
                    'slots': row['n1']['slots'], 'rule_baseline_slots': row['n1']['baseline']['slots'],
                    'fixed_state': case['state'], 'expression_excluded': case['id']==2,
                    'n13_raw': [c.get('raw', c.get('error')) for c in row['n13']['calls']],
                    'final': row['n13']['final'], 'rule_baseline_final': row['n13']['baseline']})
            else:
                if row['suite'] != 'n13':
                    row.update(followup_metrics(case, row['final'], row['calls']))
                    raw = row['generation']
                    # Invalid output is an objective format failure; no semantic claim is made about it.
                    raw_note = None if raw else '형식 실패. 유효한 생성 답변 없음. raw_faithful=null로 기록.'
                else:
                    raw = row['calls'][0].get('parsed') if row['calls'] else None
                    raw_note = None
                packets.append({'blind_id': key, 'case_id': case['id'], 'suite': case['suite'],
                    'question': case['question'], 'expectation': case['review_note'],
                    'expected_kind_reference': case.get('expected_kind'),
                    'source': case.get('policy', case.get('state')), 'raw_generation': raw,
                    'raw_note': raw_note, 'final': row['final'],
                    'rule_baseline': row.get('baseline')})
        if not team:
            summaries[model] = summary(rows)
    suffix = ('team' if team else 'independent') + '_' + '_'.join(models)
    random.Random(7319).shuffle(packets)
    save(review / f'{suffix}_packet.json', packets)
    save(review / f'{suffix}_mapping.json', mapping)
    if summaries:
        save(review / f'{suffix}_metrics.json', summaries)
    print(suffix, 'records', len(packets))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', nargs='+', required=True, choices=RUNS)
    parser.add_argument('--team', action='store_true')
    args = parser.parse_args()
    collect(args.models, args.team)
