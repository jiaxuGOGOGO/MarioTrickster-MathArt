"""Stability test — writes result to _stability_result.txt"""
from pathlib import Path; import json, tempfile, numpy as np, sys
from PIL import Image
from mathart.factory.mass_production import run_mass_production_factory

SEEDS = [1, 42, 100, 500, 999]
ACTIONS = ["walk", "run", "idle", "jump"]
results = []
log_lines = []

for seed in SEEDS:
    for action in ACTIONS:
        try:
            out = Path(tempfile.mkdtemp(prefix=f'mathart_s{seed}_{action}_'))
            res = run_mass_production_factory(
                output_root=out, batch_size=1, pdg_workers=1, gpu_slots=1,
                seed=seed, skip_ai_render=True,
                action_filter=[action],
                vibe=f"2D pixel art game character {action} animation, transparent bg")
            summary = json.loads(Path(res['summary_path']).read_text(encoding='utf-8'))
            rec = summary['records'][0]
            sp = rec['final_outputs']['sprite_asset_pack']
            qa = sp.get('distilled_quality_audit') or {}
            fps_list = sp.get('frame_paths') or []
            verdict = qa.get('overall_verdict') or qa.get('verdict') or '?'
            failed = qa.get('failed_checks') or []
            # measure
            alphas, qcs = [], []
            for p in fps_list[:4]:
                im = Image.open(p).convert('RGBA')
                a = np.array(im)[:,:,3]; fg = a>8
                alphas.append(float(fg.mean()))
                if fg.any():
                    rgb = np.array(im)[:,:,:3][fg]
                    qcs.append(len({(int(r)//16,int(g)//16,int(b)//16) for r,g,b in rgb.reshape(-1,3)}))
            mses = []
            if len(fps_list) >= 2:
                arrs = [np.array(Image.open(p).convert('RGB'), dtype=np.float32) for p in fps_list[:8]]
                mses = [float(np.mean((arrs[i+1]-arrs[i])**2)) for i in range(len(arrs)-1)]
            r = {
                'seed': seed, 'action': action, 'frames': len(fps_list),
                'verdict': verdict, 'failed': failed,
                'alpha': round(sum(alphas)/len(alphas),3) if alphas else 0,
                'qc': round(sum(qcs)/len(qcs),1) if qcs else 0,
                'mse': round(sum(mses)/len(mses),1) if mses else 0,
            }
            results.append(r)
            line = f"seed={seed} {action}: frames={r['frames']} verdict={r['verdict']} alpha={r['alpha']} qc={r['qc']} mse={r['mse']} failed={r['failed']}"
            log_lines.append(line)
        except Exception as e:
            line = f"EXCEPTION seed={seed} action={action}: {e}"
            log_lines.append(line)
            results.append({'seed': seed, 'action': action, 'verdict': 'exception', 'failed': [str(e)]})

passed = sum(1 for r in results if r.get('verdict') == 'pass')
total = len(results)
log_lines.append(f"\n=== {passed}/{total} PASSED ===")
for r in results:
    if r.get('verdict') != 'pass':
        log_lines.append(f"  NOTPASS: {r}")

out_text = "\n".join(log_lines)
Path(r"e:\unity project\exercise1\MarioTrickster-MathArt\_stability_result.txt").write_text(out_text, encoding='utf-8')
print(out_text)
