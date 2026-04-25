# SESSION-197 Handoff: Physics Data Bus Unification

## 1. What was accomplished in SESSION-197

* **VFX Topology Hydrator** (`mathart/core/vfx_topology_hydrator.py`): New ECS-style system that scans the pipeline context for physics/fluid artifact components (`vfx_artifacts.fluid_flowmap_dir`, `vfx_artifacts.physics_3d_dir`) and dynamically injects ControlNet chains into the ComfyUI workflow at runtime. Follows the Houdini PDG model where physics artifacts are work items flowing through a dependency graph, transformed into conditioning streams at the assembly site.

* **ControlNet Daisy-Chain Injection**: Physics and fluid ControlNet nodes are spliced into the conditioning chain in strict serial topology (ONNX/TensorRT serial fusion pattern):
  ```
  CLIP → Normal → Depth → OpenPose → Fluid (0.35) → Physics (0.30) → KSampler
  ```
  Each `ControlNetApplyAdvanced` node takes `positive`/`negative` from the previous node's output. No parallel/disconnected paths (prevents "decapitation override").

* **Arbitrator Extension** (`openpose_skeleton_renderer.py`): Extended `arbitrate_controlnet_strengths` with SESSION-197 fluid/physics weight bands. New constants: `FLUID_VFX_CONTROLNET_STRENGTH=0.35`, `PHYSICS_VFX_CONTROLNET_STRENGTH=0.30`, `MAX_COMBINED_CONTROLNET_STRENGTH=3.50`. Dummy mesh mode reduces fluid to 0.30 and physics to 0.25.

* **Pipeline Integration** (`builtin_backends.py`): SESSION-197 VFX topology hydration call site injected after SESSION-195 IPAdapter late-binding, before the final exception handler. Re-runs arbitration after VFX injection to calibrate new node weights.

* **48 Interception Tests** (`test_session197_physics_bus_unification.py`): 10 test groups covering context extraction, fluid/physics injection, daisy-chain connectivity, DAG closure, arbitrator calibration, anti-static red line, UX banner, edge cases, and red line compliance. All 48 pass.

* **Research Notes** (`docs/RESEARCH_NOTES_SESSION_197.md`): Documented Houdini PDG, ECS, ONNX/TensorRT, and ComfyUI multi-ControlNet research findings.

* **User Guide** (`docs/USER_GUIDE.md`): Appended Section 26 with full SESSION-197 documentation.

* **UX Banner**: Sci-fi magenta banner emitted when VFX artifacts are successfully injected:
  ```
  [⚡ SESSION-197 VFX 拓扑注入] 物理/流体计算产物已动态织入 ControlNet 串联链路 → DAG 闭合验证通过
  ```

## 2. Red Lines Preserved

| Red Line | Status |
|---|---|
| SESSION-189 anchors (MAX_FRAMES=16, LATENT_EDGE=512, NORMAL_MATTE_RGB) | ✅ Untouched |
| SESSION-190 force_decouple_dummy_mesh_payload | ✅ Untouched |
| SESSION-193 arbitrate_controlnet_strengths base logic | ✅ Extended only |
| SESSION-194 OpenPose IoC contract | ✅ Untouched |
| SESSION-195 IPAdapter late-binding | ✅ Untouched |
| Zero base JSON preset modification (反静态死板红线) | ✅ |
| os.path.exists() on every artifact path (反空投送幻觉红线) | ✅ |
| DAG closure validation after injection (反图谱污染红线) | ✅ |
| Semantic node addressing (class_type + _meta.title) | ✅ |

## 3. Next Steps (SESSION-198 Suggestions)

* **P1-PHYSICS-3D-PLUGIN-REGISTRATION**: Register `physics_3d` in the `SemanticOrchestrator` plugin registry to fix 4 pre-existing SESSION-196 test failures.
* **P1-PHYSICS-RENDER-SEQUENCE-EXPORT**: Physics3D backend outputs UMR motion clips (JSON), not image sequences. Add a render-ready sequence exporter that converts physics deformation data into visual maps for ControlNet consumption.
* **P2-ADAPTIVE-WEIGHT-SCHEDULING**: Implement dynamic ControlNet weight scheduling based on artifact quality metrics (e.g., reduce fluid weight when flowmap has low variance).
* **P2-END-TO-END-GPU-VALIDATION**: Run a full GPU render with physics/fluid artifacts against a live ComfyUI server.

## 4. Strict Rules for Next Agent

* DO NOT modify the `_execute_live_pipeline` method signature.
* DO NOT change SESSION-197 ControlNet weight constants without research justification.
* Any new artifact type MUST register via `extract_*_artifact_dir` pattern and validate with `os.path.exists()`.
* New ControlNet chains MUST splice into the existing daisy-chain (never parallel paths).
* All new nodes MUST use `_meta.title` with session tag for semantic addressing.
