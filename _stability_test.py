"""Multi-seed stability test for offline pixel-art pipeline."""
from pathlib import Path; import json, tempfile, numpy as np
from PIL import Image
from mathart.factory.mass_production import run_mass_production_factory

SEEDS = [1, 42, 100, 500, 999]
ACTIONS = ["walk", "run", "idle", "jump"]
results = []

for seed in SEEDS:
    for action in ACTIONS:
        try:
            out = Path(tempfile.mkdtemp(prefix=f'mathart_stab_{seed}_{action}_'))
            res = run_mass_production_factory(
                output_root=out, batch_size=1, pdg_workers=1, gpu_slots=1,
                seed=seed, skip_ai_render=True,
                action_filter=[action],
                vibe=f"2D像素游戏角色{action}动画，透明背景，像素风格")
            summary = json.loads(Path(res['summary_path']).read_text(encoding='utf-8'))
            rec = summary['records'][0]
            sp = rec['final_outputs']['sprite_asset_pack']
            qa = sp.get('distilled_quality_audit') or {}
            fps_list = sp.get('frame_paths') or []

            # measure sprite frames
            alphas, qcs, mses = [], [], []
            if len(fps_list) >= 2:
                arrs = [np.array(Image.open(p).convert('RGB'), dtype=np.float32) for p in fps_list[:8]]
                mses = [float(np.mean((arrs[i+1]-arrs[i])**2)) for i in range(len(arrs)-1)]
            for p in fps_list[:4]:
                im = Image.open(p).convert('RGBA')
                a = np.array(im)[:,:,3]; fg = a>8
                alphas.append(float(fg.mean()))
                if fg.any():
                    rgb = np.array(im)[:,:,:3][fg]
                    qcs.append(len({(int(r)//16,int(g)//16,int(b)//16) for r,g,b in rgb.reshape(-1,3)}))

            r = {
                'seed': seed, 'action': action,
                'frames': len(fps_list),
                'verdict': qa.get('verdict', '?'),
                'failed': qa.get('failed_checks', []),
                'alpha_mean': round(sum(alphas)/len(alphas),3) if alphas else 0,
                'qc_mean': round(sum(qcs)/len(qcs),1) if qcs else 0,
                'mse_mean': round(sum(mses)/len(mses),1) if mses else 0,
            }
            results.append(r)
            print(f"seed={seed} action={action}: frames={r['frames']} verdict={r['verdict']} alpha={r['alpha_mean']} qc={r['qc_mean']} mse={r['mse_mean']} failed={r['failed']}")
        except Exception as e:
            print(f"FAIL seed={seed} action={action}: {e}")
            results.append({'seed': seed, 'action': action, 'error': str(e)})

pass_count = sum(1 for r in results if r.get('verdict') == 'pass')
total = len(results)
print(f"\n=== SUMMARY: {pass_count}/{total} passed ===")
for r in results:
    if r.get('verdict') != 'pass':
        print(f"  FAIL/REVIEW: {r}")
