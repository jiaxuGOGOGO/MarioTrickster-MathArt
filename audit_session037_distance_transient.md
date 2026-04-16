# SESSION-037 Audit — Distance Matching & Hit Recovery Transient Phases

## Scope

本次审计核对以下研究结论是否已经真实落入代码与导出产物，而非仅停留在笔记层：

| Research Claim | Expected Landing |
|---|---|
| Jump phase should be driven by **Distance-to-Apex** | Native jump transient phase calculator + UMR metadata + exported `.umr.json` |
| Fall phase should be driven by **Distance-to-Ground** | Native fall transient phase calculator + UMR metadata + exported `.umr.json` |
| Hit should be driven by a **one-way recovery deficit / action-goal progress** phase | Native hit transient phase calculator + UMR metadata + exported `.umr.json` |
| New semantics must join the **UMR trunk** and three-layer evolution loop | `AssetPipeline`, evaluator context bridge, tests, manifest/export audit |

## Code Landing Summary

| Area | Landing | Result |
|---|---|---|
| `mathart/animation/phase_driven.py` | Added `jump_distance_phase()`, `fall_distance_phase()`, `hit_recovery_phase()` and UMR-native `phase_driven_jump/fall/hit(_frame)` | **PASS** |
| `mathart/animation/presets.py` | Legacy `jump_animation`, `fall_animation`, `hit_animation` now delegate to transient phase drivers instead of fixed time slices | **PASS** |
| `mathart/animation/unified_motion.py` | UMR now preserves terminal `phase == 1.0` for transient phase kinds instead of wrapping to `0.0` | **PASS** |
| `mathart/pipeline.py` | `jump` / `fall` / `hit` now build clips through transient phase frame generators; root motion adjusted to ascent/descent semantics | **PASS** |
| `mathart/animation/motion_matching_evaluator.py` | Layer 3 context extraction now exposes `phase_kind`, `phase_source`, `distance_to_apex`, `distance_to_ground`, `impact_deficit`, `recovery_progress` | **PASS** |
| `tests/test_unified_motion.py` | Added transient phase monotonicity, UMR metadata, and export-path assertions | **PASS** |

## Validation Executed

### 1. Targeted Regression

Command executed:

```bash
python3.11 -m pytest tests/test_unified_motion.py -q
```

Result: **6/6 PASS**

### 2. Real Export Audit

Command executed:

```bash
PYTHONPATH=/home/ubuntu/work/MarioTrickster-MathArt python3.11 tools/session037_audit_probe.py
```

Export path:

- `session037_audit_output/session037_probe/session037_probe_character_manifest.json`
- `session037_audit_output/session037_probe/session037_probe_jump.umr.json`
- `session037_audit_output/session037_probe/session037_probe_fall.umr.json`
- `session037_audit_output/session037_probe/session037_probe_hit.umr.json`

Manifest summary observed:

| Field | Value |
|---|---|
| `summary.umr_state_coverage` | `4` |
| `summary.umr_contract` | `UnifiedMotionFrame` |
| `summary.umr_pipeline_ready_for_layer3` | `true` |
| audited states | `run`, `jump`, `fall`, `hit` |

## Evidence Against Each Research Requirement

### Jump = Distance-to-Apex

Observed in exported `session037_probe_jump.umr.json`:

| Evidence | Observed Value |
|---|---|
| `frames[*].metadata.phase_kind` | `distance_to_apex` |
| terminal frame `phase` | `1.0` |
| terminal frame `metadata.distance_to_apex` | `0.0` |
| terminal frame `metadata.is_apex_window` | `true` |

This confirms the jump clip no longer relies on a fixed time-sliced anticipation/launch/apex contract inside the UMR trunk; the exported phase is derived from spatial distance to apex.

### Fall = Distance-to-Ground

Observed in exported fall clip:

| Evidence | Expected / Observed |
|---|---|
| `frames[*].metadata.phase_kind` | `distance_to_ground` |
| metadata includes `distance_to_ground` | yes |
| metadata includes `fall_reference_height` | yes |
| landing-window semantics exported | yes (`is_landing_window`) |

This confirms the fall clip is now grounded in remaining height-to-ground rather than elapsed fall time.

### Hit = Action Goal Progress / Recovery Deficit

Observed in exported hit clip:

| Evidence | Expected / Observed |
|---|---|
| `frames[*].metadata.phase_kind` | `hit_recovery` |
| metadata includes `impact_deficit` | yes |
| metadata includes `recovery_progress` | yes |
| phase semantics | `1.0 = strong stun`, decays toward `0.0` |

This confirms the hit clip now follows a one-way recovery phase rather than a spring-timed recoil preset.

## Three-Layer Evolution Loop Check

| Loop Layer | SESSION-037 Landing | Result |
|---|---|---|
| Layer 1 — generation | Native transient phase pose generators for jump/fall/hit | **PASS** |
| Layer 2 — shared bus / correction chain | UMR clip generation + root semantics + export contract | **PASS** |
| Layer 3 — evaluation / distillation | Evaluator context can now read transient phase metadata directly | **PASS** |

## Issues Found During Audit

One important issue surfaced during audit and was fixed before session close:

> **Issue:** terminal transient phases were being wrapped by `phase % 1.0` inside `UnifiedMotionFrame`, causing apex/landing/recovery endpoints to collapse from `1.0` to `0.0`.

Resolution:

- `UnifiedMotionFrame.__post_init__` now preserves clamped `[0,1]` semantics for `distance_to_apex`, `distance_to_ground`, and `hit_recovery` phase kinds.
- `pose_to_umr()` applies the same rule when constructing transient phase frames.

## Remaining Gaps After SESSION-037

| Gap | Why still open |
|---|---|
| CLI still exports sprite sheets from pose functions rather than exposing first-class UMR clip outputs | The legacy API is now behavior-compatible, but full bus propagation is still incomplete |
| Distance sensing is currently analytic/proxy-based, not live scene raycast-driven | Good for trunk closure now, but true terrain-aware sensing remains a later upgrade |
| End-to-end reproducibility benchmark still needs a dedicated zero-to-export assertion suite | Current validation is strong but still targeted rather than full-trunk benchmark coverage |

## Audit Verdict

**PASS WITH TARGETED FOLLOW-UP.** The industrial and academic phase ideas requested in SESSION-037 were not merely cited; they were implemented as native transient phase calculators, routed through the UMR trunk, audited in exported artifacts, bridged into Layer 3 context extraction, and protected by regression tests.
