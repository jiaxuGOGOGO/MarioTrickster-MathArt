# SESSION-199 → SESSION-200 Handoff: Adaptive Variance Scheduling + Dead-Water Pruning + Safe Model Mapping

## 1. What was accomplished in SESSION-199

### 1.1 反臆想模型注入红线 — Safe ControlNet Model Mapping (CRITICAL FIX)

The fluid/physics ControlNet model mapping in `mathart/core/vfx_topology_hydrator.py`
was **inverted** relative to correct photometric-stereo and depth-topology theory.
SESSION-199 corrects this:

| Channel | Before (WRONG) | After (CORRECT) | Rationale |
|---|---|---|---|
| `FLUID_CONTROLNET_MODEL_DEFAULT` | `control_v11f1p_sd15_depth.pth` | `control_v11p_sd15_normalbae.pth` | Fluid momentum field ≈ surface normal perturbation (Woodham 1980 photometric stereo) |
| `PHYSICS_CONTROLNET_MODEL_DEFAULT` | `control_v11p_sd15_normalbae.pth` | `control_v11f1p_sd15_depth.pth` | Physics 3D deformation ≈ Z-axis depth displacement |

Full research justification: `docs/RESEARCH_NOTES_SESSION_199.md`.

### 1.2 PID 自适应增益调度 — Adaptive Variance-Based Strength Scheduler

New function `compute_adaptive_controlnet_strength()` with **反数学崩溃红线**:

```python
def compute_adaptive_controlnet_strength(
    pixel_variance: float,
    base_strength: float = 0.35,
    *,
    variance_scale: float = 0.5,
    min_strength: float = 0.10,
    max_strength: float = 0.90,
) -> float:
```

- Scales ControlNet strength proportionally to frame pixel variance
- Clamped to `[min_strength, max_strength]` safety window
- **NaN / Inf / negative variance → returns `min_strength`** (反数学崩溃红线)
- **Result is ALWAYS a finite float** — guaranteed non-NaN, non-Inf
- Already integrated into `hydrate_vfx_topology()` call path

### 1.3 死水动态剪枝 — Dead-Water Dynamic Pruning (反拓扑断裂红线)

Two new functions:

- **`should_prune_dead_water(pixel_variance, threshold=0.5)`** — Dead-water detection gate
- **`prune_controlnet_node_and_reseal_dag(workflow, apply_node_id)`** — DAG surgery:
  1. Reads pruned node's upstream positive/negative references
  2. Finds all downstream consumers
  3. Rewires downstream to point to upstream (bypass pruned node)
  4. Removes pruned node + orphaned VHS_LoadImagesPath + ControlNetLoader
  5. **KSampler conditioning wires remain perfectly closed**

Dead-water pruning is fully integrated into `hydrate_vfx_topology()`:
- Variance sampled from first PNG in each artifact directory
- Below threshold → channel skipped, report shows `mode: "dead_water_pruned"`
- Above threshold → adaptive strength applied, report shows adaptive_strength value

### 1.4 SESSION-068 E2E Test Fix (历史红灯清理)

`TestAntiFlickerRenderE2E` and `TestCrossBackendContract` in
`tests/test_session068_e2e.py` used `width=32, height=32` for
`anti_flicker_render`. SESSION-198 introduced a 64×64 minimum constraint.
All 11 occurrences updated to `width=64, height=64`.

### 1.5 UX 横幅扩展 — Industrial Baking Gateway Highlight

`emit_vfx_hydration_banner()` now includes:
- SESSION-197 VFX injection status (magenta)
- SESSION-199 adaptive scheduling per-channel strength (green)
- SESSION-199 dead-water pruning alerts (yellow)
- **工业烘焙网关** gateway status (cyan)

`hydrate_vfx_topology()` also calls `emit_industrial_baking_banner()` from
`anti_flicker_runtime.py` during the bake phase.

### 1.6 New Test Suite

`tests/test_session199_adaptive_scheduling.py` — **44 unit tests** across 6 groups:

| Test Group | Count | Coverage |
|---|---|---|
| Adaptive strength computation | 16 | Boundary values, mid-range, clamp invariant, return type |
| 反数学崩溃红线 (NaN/Inf defense) | 5 | NaN, Inf, -Inf, negative, all-input sweep |
| Model mapping regression guards | 3 | fluid→normalbae, physics→depth, not-identical |
| Dead-water detection gate | 9 | Zero, below/above threshold, NaN, Inf, custom threshold |
| DAG pruning & resealing | 7 | Node removal, KSampler rewire, upstream preserved, connectivity |
| Integration tests | 4 | Full flow dead-water prune, high-variance inject, node count |

All 44 tests pass.

---

## 2. Red Lines Preserved

| Red Line | Status |
|---|---|
| 反臆想模型注入红线 (correct model mapping) | ✅ Fixed and regression-guarded |
| 反数学崩溃红线 (NaN/Inf/除零全防御) | ✅ New in SESSION-199 |
| 反拓扑断裂红线 (pruning preserves DAG connectivity) | ✅ New in SESSION-199 |
| 反空投送幻觉红线 (os.path.exists on every artifact path) | ✅ Preserved |
| 反图谱污染红线 (DAG closure validation) | ✅ Preserved |
| 反静态死板红线 (zero base JSON modification) | ✅ Preserved |
| SESSION-198 Anti-Fake-Image Red Line (np.var > 0) | ✅ Preserved |
| SESSION-197 VFX Topology Hydration daisy-chain | ✅ Preserved (extended) |
| SESSION-196 Intent Threading | ✅ Preserved |
| _execute_live_pipeline signature | ✅ Untouched |

---

## 3. Current Test Status

| Test Suite | Status |
|---|---|
| `test_session199_adaptive_scheduling.py` | ✅ 44/44 PASS |
| `test_session068_e2e.py` | ✅ Fixed (64×64 constraint satisfied) |
| `test_session197_physics_bus_unification.py` | ✅ Unaffected |

---

## 4. 傻瓜验收指引 (Foolproof Acceptance Checklist)

**下一位 Agent 或人类审查者，请按以下步骤逐项验收：**

### 4.1 一键验证（30 秒完成）

```bash
cd /path/to/MarioTrickster-MathArt
python3 -m pytest tests/test_session199_adaptive_scheduling.py -v
# 预期：44 passed
```

### 4.2 模型映射验证

```python
from mathart.core.vfx_topology_hydrator import (
    FLUID_CONTROLNET_MODEL_DEFAULT,
    PHYSICS_CONTROLNET_MODEL_DEFAULT,
)
assert FLUID_CONTROLNET_MODEL_DEFAULT == "control_v11p_sd15_normalbae.pth"
assert PHYSICS_CONTROLNET_MODEL_DEFAULT == "control_v11f1p_sd15_depth.pth"
```

### 4.3 反数学崩溃验证

```python
from mathart.core.vfx_topology_hydrator import compute_adaptive_controlnet_strength
import math

result_nan = compute_adaptive_controlnet_strength(float('nan'))
assert result_nan == 0.1 and not math.isnan(result_nan)

result_inf = compute_adaptive_controlnet_strength(float('inf'))
assert result_inf == 0.1 and not math.isinf(result_inf)

result_neg = compute_adaptive_controlnet_strength(-100.0)
assert result_neg == 0.1
```

### 4.4 死水剪枝验证

```python
from mathart.core.vfx_topology_hydrator import should_prune_dead_water
assert should_prune_dead_water(0.0) is True   # 死水
assert should_prune_dead_water(0.1) is True   # 死水
assert should_prune_dead_water(5.0) is False  # 活水
```

### 4.5 DAG 连通性验证

```python
from mathart.core.vfx_topology_hydrator import prune_controlnet_node_and_reseal_dag

# 构建最小 DAG，剪枝后 KSampler 仍连通
# （详见 test_session199_adaptive_scheduling.py::TestPruneControlnetNodeAndResealDag）
```

### 4.6 历史红灯验证

```bash
grep -c "width=32\|height=32" tests/test_session068_e2e.py
# 预期：0（所有 32 已替换为 64）
```

---

## 5. Next Steps (SESSION-200 Suggestions)

**老大，SESSION-199 五项手术全部完成：**
1. ✅ 模型映射已修正（fluid→normalbae, physics→depth）
2. ✅ 自适应方差调度器已实装并集成
3. ✅ 死水动态剪枝 + DAG 缝合已实装并集成
4. ✅ 反数学崩溃红线全面防御
5. ✅ 历史红灯 test_session068 已清理

SESSION-200 建议方向：

1. **GPU 端到端真实联调**：在有显卡环境下运行完整 ComfyUI 管线，验证
   normalbae/depth 模型对 fluid/physics 条件图的实际引导效果。

2. **ComfyUI 节点对齐验证**：确认后端 ComfyUI 实例已安装 `VHS_LoadImagesPath`
   节点，并验证 normalbae/depth 模型对 512×512 PNG 序列的实际加载。

3. **性能基准**：对自适应调度器进行帧级性能基准测试，确保 `np.var()` 计算
   不成为管线瓶颈。

4. **多帧方差采样**：当前仅采样第一帧 PNG 的方差，考虑采样多帧取中位数
   以提高鲁棒性。

---

## 6. Strict Rules for Next Agent

* DO NOT revert `FLUID_CONTROLNET_MODEL_DEFAULT` to `depth.pth` — the
  normalbae mapping is correct per photometric stereo theory (SESSION-199
  research notes).
* DO NOT revert `PHYSICS_CONTROLNET_MODEL_DEFAULT` to `normalbae.pth` —
  the depth mapping is correct per Z-axis deformation theory.
* DO NOT modify the `_execute_live_pipeline` method signature.
* DO NOT lower `DEAD_WATER_VARIANCE_THRESHOLD` below 0.1 — this risks
  injecting pure-noise conditioning into the latent space.
* Any new artifact type MUST register via `extract_*_artifact_dir` pattern
  and validate with `os.path.exists()`.
* New ControlNet chains MUST splice into the existing daisy-chain (never
  parallel paths).
* All new nodes MUST use `_meta.title` with session tag for semantic addressing.
* `compute_adaptive_controlnet_strength()` is a pure function — keep it
  side-effect-free.
* `should_prune_dead_water()` is a pure function — keep it side-effect-free.
* `prune_controlnet_node_and_reseal_dag()` modifies the workflow in-place —
  always run DAG closure validation after calling it.

---

## 7. Files Modified in SESSION-199

| File | Operation | Lines Changed |
|---|---|---|
| `mathart/core/vfx_topology_hydrator.py` | Modified | +300 lines (model swap + adaptive scheduler + dead-water pruning + DAG reseal + UX banner) |
| `tests/test_session068_e2e.py` | Modified | 11 occurrences 32→64 |
| `tests/test_session199_adaptive_scheduling.py` | New | 500+ lines (44 tests) |
| `docs/RESEARCH_NOTES_SESSION_199.md` | New | Research notes |
| `docs/USER_GUIDE.md` | Appended | SESSION-199 DaC section |
| `SESSION_HANDOFF.md` | Rewritten | This file |
| `PROJECT_BRAIN.json` | Updated | v1.0.7, SESSION-199 entry |
