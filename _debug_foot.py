from pathlib import Path; import json, tempfile
from mathart.factory.mass_production import run_mass_production_factory

for seed, action in [(1, "idle"), (500, "idle"), (999, "run")]:
    out = Path(tempfile.mkdtemp(prefix=f'mathart_foot_{seed}_{action}_'))
    res = run_mass_production_factory(
        output_root=out, batch_size=1, pdg_workers=1, gpu_slots=1,
        seed=seed, skip_ai_render=True, action_filter=[action],
        vibe=f"2D像素角色{action}")
    summary = json.loads(Path(res['summary_path']).read_text(encoding='utf-8'))
    rec = summary['records'][0]
    sp = rec['final_outputs']['sprite_asset_pack']
    foot = sp.get('foot_contact_report') or {}
    qa = sp.get('distilled_quality_audit') or {}
    foot_check = [c for c in qa.get('checks', []) if 'foot' in c['name'] or 'ground' in c['name']]
    print(f"seed={seed} action={action}:")
    print(f"  foot_contact: {json.dumps(foot.get('metrics', {}))}")
    for c in foot_check:
        print(f"  check {c['name']}: {c['value']:.2f} vs {c['threshold']:.2f} -> {'OK' if c['passed'] else 'FAIL'}")
