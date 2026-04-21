# SESSION-122 Handoff — P1-2 Per-frame SDF Parameter Animation

## Goal & Status
**Objective**: close **P1-2 — Per-frame SDF parameter animation** by introducing a runtime-safe dynamic parameter layer for `smooth_morphology.py`, while preserving the static genotype trunk and grounding all animated-parameter decisions in external research.

**Status**: **CLOSED**.

The landed implementation adds a new tensorized parameter-track subsystem in `mathart/animation/parameter_track.py`, extends `MorphologyPartGene.build_sdf()` and `MorphologyGenotype.decode_to_sdf()` with an optional `parameter_context` override channel, and validates dynamic 4D SDF behavior through white-box tests that exercise dense track sampling, runtime shape evolution, OpenUSD-style `TimeSamples` export, and volume-preserving dynamic mesh continuity.[1] [2] [3] [4] [5]

## Research Alignment Audit
The implementation was intentionally constrained by five external references, and the raw working notes were saved to `research/session122_external_reference_notes.md`.

| Reference pillar | Practical rule adopted in code | Why it matters here |
|---|---|---|
| Inigo Quilez — Smooth minimum | Animate primitive parameters and blend radii, not post-hoc mesh vertices; keep smooth-union `k` bounded and continuous.[1] | This preserves the original SDF modeling logic and avoids breaking the static morphology trunk. |
| OpenUSD — TimeCodes / TimeSamples | Separate sparse authored keyframes from dense sampled values, and keep a serializable `time -> value` mapping.[2] | This shaped `ParameterTrack.to_time_samples()` and the sparse→dense contract used by `ParameterTrackBundle`. |
| OpenUSD spline animation proposal | Treat spline/keyframe data as the authoritative sparse source and dense samples as a runtime realization.[4] | This informed the choice to add a wrapper-layer animation system instead of hard-coding exporter concerns into the morphology core. |
| Catmull-Rom / cubic Hermite interpolation | Use an interpolating, local-control, **C1-continuous** cubic scheme for animated parameter tracks.[5] | This prevents visible derivative jumps in breathing / pulsation / morph envelopes. |
| Disney-style squash & stretch volume preservation | When one axis expands, linked orthogonal axes should contract so gross volume remains approximately stable.[3] | This drove `volume_preserving_axis_link()` and the dynamic mesh-bbox continuity regression. |

> “Splines and time samples are not competing ideas in animation interchange; sparse authoring and dense evaluation should coexist.” This was the key interoperability lesson extracted from the OpenUSD references and carried into the new parameter-track layer.[2] [4]

## Architecture Decisions Locked
The most important architectural decision is that **dynamic parameter animation now lives in a sibling wrapper layer, not as a destructive rewrite of the static morphology genotype**. `ParameterTrack`, `ParameterTrackBundle`, `SampledParameterMatrix`, and `TimeAwareMorphologyEvaluator` live in `mathart/animation/parameter_track.py`, while `smooth_morphology.py` remains the authoritative static SDF trunk.

The second locked decision is that **runtime animation enters the SDF trunk only through an optional `parameter_context` dictionary**. This means every pre-existing caller of `MorphologyPartGene.build_sdf()` and `MorphologyGenotype.decode_to_sdf()` still behaves exactly as before when no context is supplied, but animated callers can override `param_a`, `param_b`, `param_c`, `scale_x`, `scale_y`, `offset_x`, `offset_y`, `rotation`, `blend_k`, `global_scale`, and `bilateral_symmetry` on a frame-by-frame basis.

The third locked decision is that **the hot path is vectorized over the entire time tensor**. The dense interpolation kernel in `ParameterTrack.sample()` uses `np.searchsorted`, batched Hermite basis evaluation, and gathered knot/tangent tensors. There is no Python `for t in frames` loop in the sampling hot path, and the white-box test explicitly audits the function source for that red line while also benchmarking a 10,000-frame sample run.

The fourth locked decision is that **4D SDF use remains streamed rather than cached**. `TimeAwareMorphologyEvaluator` samples dense parameter tensors once, then resolves a single frame context at a time for decoding. This deliberately avoids materializing a full `[x, y, z, t]` distance cache, which would be the wrong memory trade-off for the current CPU-first repository lane.

## Code Change Table
| File | Action | Details |
|---|---|---|
| `mathart/animation/parameter_track.py` | Added | New tensorized dynamic-parameter lane: `ParameterTrack`, `ParameterTrackBundle`, `SampledParameterMatrix`, `TimeAwareMorphologyEvaluator`, and `volume_preserving_axis_link()`. |
| `mathart/animation/smooth_morphology.py` | Modified | Added scalar/bool context resolvers, per-part `resolve_parameters()`, optional `parameter_context` support in `build_sdf()` and `decode_to_sdf()`, and bounded runtime override guards. |
| `tests/test_sdf_animation.py` | Added | Six new white-box tests covering dense parameter-matrix upsampling, 10k-frame vectorized sampling, `TimeSamples` export, runtime shape evolution, streamed evaluator use, and dynamic mesh-bbox continuity. |
| `research/session122_external_reference_notes.md` | Added | Working research notes for IQ smooth min, OpenUSD time samples, OpenUSD spline animation, Catmull-Rom continuity, and squash/stretch volume preservation. |
| `PROJECT_BRAIN.json` | Updated | Marked **P1-2 CLOSED**, recorded SESSION-122 validation, and documented the new OpenUSD-compatible parameter-track groundwork. |
| `SESSION_HANDOFF.md` | Updated | Replaced prior handoff with the current session closure summary and next-step guidance. |

## White-Box Validation Closure
Local touched-lane validation is complete.

| Validation command / scope | Result |
|---|---|
| `python3.11 -m pytest -q tests/test_smooth_morphology.py tests/test_sdf_animation.py` | **52/52 PASS** |
| Legacy smooth morphology regression | **46/46 PASS** |
| New SESSION-122 dynamic animation suite | **6/6 PASS** |

The new validation suite closes three red lines simultaneously. First, it proves that sparse keyframes can be upsampled to a dense `[frames, params]` tensor without scalar frame loops. Second, it proves that `decode_to_sdf(parameter_context=...)` actually changes the decoded field in a mathematically meaningful way. Third, it proves that a breathing-style animated field, after extrusion and Dual Contouring extraction, evolves with a smoothly changing bounding-box volume instead of discontinuous jumps.

## OpenUSD / P1-ARCH-5 Hand-off Implication
SESSION-122 does **not** finish `P1-ARCH-5`, but it removes a meaningful blocker for that task. The repository now has a clean distinction between **sparse authored keyframes** and **dense sampled animated values**, and those dense values can already be serialized as `track_name -> {time: value}` dictionaries mirroring `TimeSamples` semantics.[2] [4]

The practical next step for `P1-ARCH-5` is therefore no longer “invent animated parameter storage,” but rather “map the already-existing parameter-track contract onto stable prim paths, attribute schemas, and exporter-owned usd/usda serialization.” In other words, the runtime math lane is now ready; the remaining work is scene-graph and file-format ownership.

## Recommended Next Steps
The most valuable immediate follow-up is **P1-ARCH-5**. The new parameter tracks should be promoted into a prim-style animated attribute layer with stable identities such as `</Character/Morphology/Part_0.radius>` and adapter-owned export logic. That would convert the current internal `TimeSamples`-compatible representation into true interchange output without pulling file-format concerns back into the SDF runtime.

The second follow-up is **P2-DIM-UPLIFT-13**. The dynamic SDF lane is now functionally correct, but animated mesh extraction still runs on the CPU. If animated morphology is going to drive production-scale preview renders or benchmark suites, the next leverage point is GPU-accelerated dense sampling and mesh extraction.

The third follow-up is **P1-AI-1**. Downstream neural-render and control pipelines should start consuming the new animated parameter tracks directly, rather than treating SDF morphology as a static single-frame source.

## References
[1]: https://iquilezles.org/articles/smin/ "Inigo Quilez — smooth minimum"
[2]: https://docs.nvidia.com/learn-openusd/latest/stage-setting/timecodes-timesamples.html "NVIDIA Learn OpenUSD — TimeCodes and TimeSamples"
[3]: https://adammadej.com/posts/202403-squashstretch/ "Adam Madej — Squash & Stretch and volume preservation"
[4]: https://github.com/PixarAnimationStudios/OpenUSD-proposals/blob/main/proposals/spline-animation/README.md "Pixar OpenUSD proposal — Spline Animation"
[5]: https://graphics.cs.cmu.edu/nsp/course/15-462/Fall04/assts/catmullRom.pdf "Christopher Twigg — Catmull-Rom splines"
