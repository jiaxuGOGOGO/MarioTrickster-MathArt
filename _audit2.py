from pathlib import Path; import json, tempfile, numpy as np
from PIL import Image
from mathart.factory.mass_production import run_mass_production_factory

out = Path(tempfile.mkdtemp(prefix='mathart_offline2_'))
res = run_mass_production_factory(
    output_root=out, batch_size=1, pdg_workers=1, gpu_slots=1,
    seed=200, skip_ai_render=True, vibe='2D像素角色')
summary = json.loads(Path(res['summary_path']).read_text(encoding='utf-8'))
rec = summary['records'][0]
sp = rec['final_outputs']['sprite_asset_pack']

fps = sp.get('frame_paths') or []
print(f'frame_paths count: {len(fps)}')
for p in fps[:4]:
    im = Image.open(p).convert('RGBA')
    a = np.array(im)[:,:,3]; fg = a>8
    rgb = np.array(im)[:,:,:3][fg]
    qc = len({(int(r)//16,int(g)//16,int(b)//16) for r,g,b in rgb.reshape(-1,3)}) if fg.any() else 0
    print(f'  {Path(p).name}: size={im.size} alpha={fg.mean():.3f} bbox={im.getbbox()} qc={qc}')

baked = rec['final_outputs']['guide_baking']
if baked.get('albedo_paths'):
    im0 = Image.open(baked['albedo_paths'][0]).convert('RGBA')
    w,h = im0.size
    bbox = im0.getbbox()
    print(f'guide bbox: {bbox} of {w}x{h}')
    if bbox:
        cov = (bbox[2]-bbox[0])*(bbox[3]-bbox[1])/(w*h)
        print(f'guide bbox area ratio: {cov:.3f}')

qa = sp.get('distilled_quality_audit') or {}
print('quality checks:')
for c in qa.get('checks') or []:
    name = c['name']; val = c['value']; thr = c['threshold']
    status = 'OK' if c['passed'] else 'FAIL'
    print(f'  {status} {name}: {val:.4f} {c["op"]} {thr:.4f}')

print('RESULT:', res['status'])
