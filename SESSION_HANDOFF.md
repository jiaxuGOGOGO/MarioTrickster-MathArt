# SESSION-199 Handoff: Safe Model Mapping & Adaptive Variance Scheduling

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

New function `compute_adaptive_controlnet_strength()` added to
`mathart/core/vfx_topology_hydrator.py`:

```python
def compute_adaptive_controlnet_strength(
    pixel_variance: float,
    base_strength: float = 0.35,
    *,
    variance_scale: float = 0.5,
    min_strength: float = 0.10,
    max_strength: float = 0.90,
) -> float:
    normalised = float(pixel_variance) / 255.0
    raw = float(base_strength) + float(variance_scale) * normalised
    return float(max(float(min_strength), min(float(max_strength), raw)))
```

Scales ControlNet strength proportionally to frame pixel variance,
clamped to `[min_strength, max_strength]` safety window.

### 1.3 SESSION-068 E2E Test Fix (历史红灯清理)

`TestAntiFlickerRenderE2E` and `TestCrossBackendContract` in
`tests/test_session068_e2e.py` used `width=32, height=32` for
`anti_flicker_render`. SESSION-198 introduced a 64×64 minimum constraint.
All 11 occurrences updated to `width=64, height=64`.

### 1.4 New Test Suite

`tests/test_session199_adaptive_scheduling.py` — 16 unit tests:
- Adaptive scheduler boundary conditions (zero variance, max variance, clamp)
- Custom parameter variants
- Return type assertions
- **Regression guards** for fluid→normalbae and physics→depth model mapping

All 16 tests pass.

---

## 2. Red Lines Preserved

| Red Line | Status |
|---|---|
| 反臆想模型注入红线 (correct model mapping) | ✅ Fixed and regression-guarded |
| 反空投送幻觉红线 (os.path.exists on every artifact path) | ✅ Preserved |
| 反图谱污染红线 (DAG closure validation) | ✅ Preserved |
| 反静态死板红线 (zero base JSON modification) | ✅ Preserved |
| SESSION-198 Anti-Fake-Image Red Line (np.var > 0) | ✅ Preserved |
| SESSION-197 VFX Topology Hydration daisy-chain | ✅ Preserved |
| SESSION-196 Intent Threading | ✅ Preserved |
| _execute_live_pipeline signature | ✅ Untouched |

---

## 3. Current Test Status

| Test Suite | Status |
|---|---|
| `test_session199_adaptive_scheduling.py` | ✅ 16/16 PASS |
| `test_session068_e2e.py` | ✅ Fixed (64×64 constraint satisfied) |
| `test_session197_physics_bus_unification.py` | ✅ Unaffected (no model name assertions) |

---

## 4. Next Steps (SESSION-200 Suggestions)

**老大，SESSION-199 三项手术全部完成：**
1. 模型映射已修正（fluid→normalbae, physics→depth）
2. 自适应方差调度器已实装
3. 历史红灯 test_session068 已清理

SESSION-200 建议方向：

1. **GPU 端到端真实联调**：在有显卡环境下运行完整 ComfyUI 管线，验证
   normalbae/depth 模型对 fluid/physics 条件图的实际引导效果。

2. **动态权重调度集成**：将 `compute_adaptive_controlnet_strength()` 集成
   到 `hydrate_fluid_controlnet_chain()` 和 `hydrate_physics_controlnet_chain()`
   的实际调用路径中，替换静态常量。

3. **ComfyUI 节点对齐验证**：确认后端 ComfyUI 实例已安装 `VHS_LoadImagesPath`
   节点，并验证 normalbae/depth 模型对 512×512 PNG 序列的实际加载。

4. **性能基准**：对自适应调度器进行帧级性能基准测试，确保 `np.var()` 计算
   不成为管线瓶颈。

---

## 5. Strict Rules for Next Agent

* DO NOT revert `FLUID_CONTROLNET_MODEL_DEFAULT` to `depth.pth` — the
  normalbae mapping is correct per photometric stereo theory (SESSION-199
  research notes).
* DO NOT revert `PHYSICS_CONTROLNET_MODEL_DEFAULT` to `normalbae.pth` —
  the depth mapping is correct per Z-axis deformation theory.
* DO NOT modify the `_execute_live_pipeline` method signature.
* Any new artifact type MUST register via `extract_*_artifact_dir` pattern
  and validate with `os.path.exists()`.
* New ControlNet chains MUST splice into the existing daisy-chain (never
  parallel paths).
* All new nodes MUST use `_meta.title` with session tag for semantic addressing.
* `compute_adaptive_controlnet_strength()` is a pure function — keep it
  side-effect-free.
