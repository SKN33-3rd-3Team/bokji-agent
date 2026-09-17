"""Select a metadata-balanced subset without consulting model answers."""
from collections import Counter
import json
from pathlib import Path
import random
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import eval_300_ollama as r


def main():
    source = ROOT / 'experiments/model_evaluation/local_300_20260916/comparison_runpod_20260916'
    target = source.with_name('comparison_runpod_60_20260916')
    manifest, mh, cases = r.validate_manifest(source)
    scenarios = r.read(source / 'scenarios.json')
    quota = dict(normal=5, missing=4, boundary=4, subject=3, unknown=2, trap=2)
    groups = {}
    for s in scenarios:
        groups.setdefault(s['variants'][0]['style'], []).append(s)
    rng = random.Random(60)
    for attempt in range(1, 1000001):
        selected = [s for group in groups.values() for s in rng.sample(group, 2)]
        if Counter(s['category'] for s in selected) != Counter(quota):
            continue
        if len({s['policy_id'] for s in selected}) != 8:
            continue
        break
    else:
        raise ValueError('No balanced selection')
    selected.sort(key=lambda s: s['id'])
    ids = {s['id'] for s in selected}
    selected_cases = [c for c in cases.values() if c['situation_id'] in ids]
    styles = Counter(c['style'] for c in selected_cases)
    assert len(selected_cases) == 60 and len(styles) == 10 and set(styles.values()) == {6}
    target.mkdir(exist_ok=False)
    for name in (*r.FROZEN, 'manifest.json', 'server_runpod.json'):
        shutil.copyfile(source / name, target / name)
    r.dump(target / 'selected_cases.json', selected_cases, exclusive=True)
    r.dump(target / 'selected_scenarios.json', selected, exclusive=True)
    selection = dict(created_at=r.now(), seed=60, accepted_draw=attempt,
        method='Draw two situations per first-variant style; accept first draw matching category quotas and all eight policies. Model answers are never read.',
        source_manifest_sha256=mh, situation_ids=sorted(ids),
        ordered_ids=[i for i in manifest['order'] if cases[i]['situation_id'] in ids],
        category_situations=quota, style_sentences=dict(styles),
        policy_situations=dict(Counter(s['policy_id'] for s in selected)),
        n1_applicable_sentences=sum(c['n1_applicable'] for c in selected_cases),
        selected_sha256={n:r.digest((target/n).read_bytes()) for n in ('selected_cases.json','selected_scenarios.json')})
    r.dump(target / 'selection.json', selection, exclusive=True)
    # Preserve the user-interrupted 300-case attempt separately from this run.
    hashes = {p.relative_to(ROOT).as_posix():r.digest(p.read_bytes())
              for p in source.rglob('*') if p.is_file()}
    r.dump(target / 'preserved_runpod_300_sha256.json', hashes, exclusive=True)
    r.dump(target / 'excluded_attempts.json', dict(reason='User selected balanced 60; prior 41 completed A.X cases and the deliberate tunnel interruption are excluded.',
        previous_base=str(source.relative_to(ROOT)), previous_completed_ax=41), exclusive=True)
    print(json.dumps(selection, ensure_ascii=True))


if __name__ == '__main__':
    main()
