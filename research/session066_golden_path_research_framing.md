# SESSION-066 Golden Path Research Framing

| Field | Content |
|---|---|
| **subsystem** | Golden Path architecture closure: strong artifact/backend contract, decorator-driven backend registry, federated lane orchestrator, registry-wide E2E guard, and three-layer evolution loop integration |
| **decision_needed** | Determine what new external references are still necessary to land the user's requested Golden Path changes without duplicating SESSION-064/065 architecture research, and map them to concrete code changes |
| **already_known** | The repository already has `ArtifactManifest`, `BackendRegistry`, `MicrokernelOrchestrator`, `ThreeLayerEvolutionLoop`, Dimension Uplift references (Dual Contouring, QEM, Vertex Normal Editing), Unity URP 2D bridge, industrial renderer path, anti-flicker bridge, and multiple evolution bridges |
| **duplicate_forbidden** | Do not re-search Dual Contouring, QEM, Vertex Normal Editing, DeepPhase FFT, KD-Tree motion matching, SparseCtrl, EbSynth, XPBD basics, or generic microkernel/plugin rhetoric already absorbed in SESSION-064/065 and `DEDUP_REGISTRY.json` |
| **success_signal** | A source is useful only if it gives a directly actionable implementation pattern for plugin registry hardening, federated orchestration meta-reporting, registry-wide scheduled validation, or contract/version migration strategy |

## Initial Hypothesis

The likely best path is not a fresh architecture rewrite but a **hardening pass** over the existing SESSION-064 microkernel foundation:

1. Introduce a strict `BackendType` enum and alias migration layer.
2. Bind `ArtifactManifest.backend_type` and backend metadata to the enum.
3. Convert the legacy `EvolutionOrchestrator` into a federated facade over the microkernel/evolution-loop path.
4. Add registry-wide E2E execution + scheduled CI.
5. Normalize legacy strong assets as explicit backends and update project memory.

## Stop Condition

Stop external search as soon as 2–3 strong sources confirm the same practical conclusion: harden the existing plugin system instead of rebuilding from scratch.
