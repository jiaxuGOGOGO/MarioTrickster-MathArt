| Key | Value |
|---|---|
| Session | `SESSION-117` |
| Focus | `P1-HUMAN-31A` SMPL-like Shape Latent Integration (Skin-Bone Unification) |
| Status | `COMPLETE` |
| Working Branch | `main` |
| Validation | `65 PASS / 0 FAIL` (`pytest tests/test_genotype.py`), covering shape latent serialization, backward compatibility, skeleton deformation, bounds enforcement, and mutation/crossover propagation. |
| Full Regression | `1960 PASS` (All existing tests remain green, proving no breakage to legacy ECS or pipeline behavior) |
| Primary Files | `mathart/animation/genotype.py` (MODIFIED), `mathart/pipeline.py` (MODIFIED), `tests/test_genotype.py` (MODIFIED), `PROJECT_BRAIN.json`, `SESSION_HANDOFF.md` |

## Executive Summary

This session closes **P1-HUMAN-31A** — the critical integration of SMPL-like continuous shape latents into the ECS `CharacterGenotype` and the rendering/physics pipeline. The implementation bridges the gap between discrete structural genes (archetypes, templates) and the continuous `DistilledSMPLBodyModel` (SESSION-031), achieving true "skin-bone unification."

Previously, the pipeline suffered from a "skin-bone disconnect" anti-pattern: proportion modifiers altered the visual mesh (skin), but the underlying physics skeleton and joint coordinates remained rigidly tied to the base body template. This caused physics colliders and IK targets to drift away from the visual mesh when extreme proportions were applied.

The new pipeline completely eliminates this disconnect through a unified shape latent architecture:

1. **8-Dimensional Shape Latent**: Added `shape_latents` (8-dim continuous vector, bounded [-1, 1]) to `CharacterGenotype`, mapping directly to `SMPLShapeLatent` (stature, shoulder_width, hip_width, torso_height, arm_length, leg_length, head_scale, limb_thickness).
2. **Skin-Bone Unification**: `CharacterGenotype.build_shaped_skeleton()` now dynamically deforms the base skeleton using `DistilledSMPLBodyModel.apply_shape_to_skeleton()`. This ensures joint positions, bone lengths, and `head_units` accurately reflect the body shape.
3. **Pipeline Integration**: `AssetPipeline.produce_character_pack()` now uses the shaped skeleton for rendering, physics projection, secondary chains, and biomechanics, ensuring all subsystems operate on the exact same unified morphology.

## Research Alignment Audit

| Reference | Requested Principle | SESSION-117 Concrete Closure |
|---|---|---|
| SMPL Architecture (SIGGRAPH 2015) | Shape blendshapes MUST be applied before pose/skinning | `decode_to_style()` decodes shape latents into proportion modifiers *before* applying explicit slot overrides, matching the SMPL shape → pose → skinning convention. |
| Joint Regressor Matrix | Joint positions MUST be regressed from the deformed shape, not fixed | `build_shaped_skeleton()` delegates to `DistilledSMPLBodyModel` which recomputes joint coordinates and bone lengths based on the 8-dim shape latent. |
| ECS Data Contract Versioning | Genotype serialization MUST be backward compatible and JSON-safe | `from_dict()` implements graceful fallback (missing/None/short vectors degrade to neutral all-zero body). `to_dict()` explicitly casts `np.ndarray` to `List[float]` to prevent JSON serialization crashes. |

## What Changed in Code

| File | Change |
|---|---|
| `mathart/animation/genotype.py` | Added `SHAPE_LATENT_DIM`, `SHAPE_LATENT_BOUNDS`, `shape_latents` field to `CharacterGenotype`. Implemented JSON-safe `to_dict()` and backward-compatible `from_dict()`. Added `build_shaped_skeleton()`. Integrated shape latents into `decode_to_style()`, `mutate_genotype()` (Layer 2.5 truncated normal), `crossover_genotypes()`, and `enforce_genotype_bounds()`. |
| `mathart/pipeline.py` | Added `shape_latents` override knob to `CharacterSpec`. Updated `produce_character_pack()` to build and distribute the `_shaped_skeleton` to physics, secondary chains, biomechanics, and rendering loops. Added `shape_latents` to the exported manifest metadata. |
| `tests/test_genotype.py` | Added `TestShapeLatentGenotype` with 15 new white-box tests covering legacy JSON fallback, extreme latent skeleton deformation, JSON roundtrip safety, mutation/crossover propagation, and bounds enforcement. |
| `PROJECT_BRAIN.json` | Updated `current_focus` and `last_session_id` to reflect P1-HUMAN-31A closure. |
| `SESSION_HANDOFF.md` | Rewritten for SESSION-117. |

## White-Box Validation Closure

| Assertion | Evidence |
|---|---|
| Backward Compatibility | `test_legacy_dict_without_shape_latents_no_keyerror`, `test_legacy_dict_with_none_shape_latents`, `test_legacy_dict_with_short_shape_latents` prove old archives degrade gracefully to a neutral body. |
| Skeleton Deformation | `test_extreme_latents_change_femur_bone_length`, `test_extreme_latents_change_shoulder_position`, `test_extreme_latents_change_head_units` prove non-zero latents measurably alter the skeleton structure. |
| JSON Safety | `test_shape_latents_json_roundtrip`, `test_shape_latents_no_numpy_in_json` prove the genotype remains 100% JSON serializable without `TypeError: Object of type ndarray is not JSON serializable`. |
| Mutation & Crossover | `test_mutation_affects_shape_latents`, `test_crossover_mixes_shape_latents` prove the evolutionary operators correctly propagate the new genes. |
| Bounds Enforcement | `test_enforce_bounds_clamps_shape_latents` proves the hard clamp to [-1, 1] is strictly enforced. |

## Architecture Discipline Check

| Red Line | Result |
|---|---|
| No `np.ndarray` in JSON | Compliant. `to_dict()` explicitly uses `[float(v) for v in self.shape_latents]`. |
| Backward Compatibility | Compliant. Old JSON files without `shape_latents` load perfectly and default to a neutral body. |
| Skin-Bone Unification | Compliant. The pipeline now uses the exact same `_shaped_skeleton` for visual rendering, XPBD physics, and Jakobsen secondary chains. |

## Handoff Notes

- **P1-HUMAN-31A is fully closed.** The character genotype now supports continuous, evolvable body shapes that physically deform the underlying skeleton.
- The `shape_latents` vector is 8-dimensional and bounded to `[-1, 1]`. It maps to `stature`, `shoulder_width`, `hip_width`, `torso_height`, `arm_length`, `leg_length`, `head_scale`, and `limb_thickness`.
- Future work (e.g., P1-AI-1 or P1-NEW-10) can now safely evolve these shape latents, knowing that the physics engine and visual renderer will remain perfectly synchronized.
