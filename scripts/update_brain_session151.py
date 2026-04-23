#!/usr/bin/env python3
"""Update PROJECT_BRAIN.json for SESSION-151.

SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH):
End-to-end ComfyUI dynamic payload injection and headless render backend.
"""
import json
from pathlib import Path

BRAIN_PATH = Path(__file__).resolve().parent.parent / "PROJECT_BRAIN.json"

with open(BRAIN_PATH, "r", encoding="utf-8") as f:
    brain = json.load(f)

# --- Top-level metadata ---
brain["version"] = "v0.99.3"
brain["last_session_id"] = "SESSION-151"
brain["last_updated"] = "2026-04-23"
brain["total_iterations"] = brain.get("total_iterations", 741) + 8
brain["total_code_lines"] = brain.get("total_code_lines", 164850) + 1850

# --- Gap inventory: add SESSION-151 task ---
top_priorities = brain.get("gap_inventory", {}).get("top_priorities_ordered", [])
session_151_entry = {
    "id": "P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH",
    "title": "End-to-End ComfyUI Dynamic Payload Injection & Headless Render Backend",
    "priority": "P0",
    "status": "CLOSED",
    "session_landed": "SESSION-151",
    "modules": [
        "mathart/backend/__init__.py",
        "mathart/backend/comfy_mutator.py",
        "mathart/backend/comfy_client.py",
        "mathart/backend/comfyui_render_backend.py",
        "mathart/core/backend_types.py",
        "mathart/core/backend_registry.py",
        "mathart/core/artifact_schema.py"
    ],
    "tests": [
        "tests/test_comfyui_render_backend.py"
    ],
    "description": (
        "Implemented the complete ComfyUI headless render backend with industrial-grade "
        "BFF (Backend for Frontend) dynamic payload mutation. Three core modules: "
        "(1) comfy_mutator.py — semantic JSON tree traversal mutator using _meta.title markers "
        "(NEVER hardcoded node IDs), immutable blueprint pattern, full mutation audit ledger; "
        "(2) comfy_client.py — high-availability API client with ephemeral /upload/image multipart "
        "upload, WebSocket telemetry with HTTP poll fallback, timeout circuit breaker "
        "(RenderTimeoutError), VRAM garbage collection via /free endpoint, and mandatory output "
        "repatriation to outputs/production/; "
        "(3) comfyui_render_backend.py — @register_backend plugin (BackendType.COMFYUI_RENDER, "
        "ArtifactFamily.COMFYUI_RENDER_REPORT) with validate_config() backend-owned parameter "
        "normalization, graceful degradation when ComfyUI is offline, and strongly-typed "
        "ArtifactManifest output with full provenance metadata for downstream GA fitness evaluation. "
        "All three anti-pattern red lines enforced: no hardcoded node IDs, poll loop has sleep+timeout, "
        "all outputs repatriated to outputs/production/. 29/29 tests PASS."
    )
}
# Insert at position 0 (highest priority)
top_priorities.insert(0, session_151_entry)
brain["gap_inventory"]["top_priorities_ordered"] = top_priorities

# --- Recent sessions: prepend SESSION-151 ---
recent = brain.get("recent_sessions", [])
session_151_recent = {
    "session_id": "SESSION-151",
    "focus": "ComfyUI BFF dynamic payload injection & headless render backend",
    "status": "COMPLETE",
    "date": "2026-04-23",
    "summary": (
        "Implemented the complete end-to-end ComfyUI headless render backend as a "
        "registry-native @register_backend plugin. Three core modules landed in mathart/backend/: "
        "(1) ComfyWorkflowMutator — BFF dynamic JSON tree traversal mutator that finds nodes "
        "by semantic _meta.title markers (NEVER hardcoded node IDs), performs immutable-blueprint "
        "deep-copy mutation with full audit ledger tracking every injection; "
        "(2) ComfyAPIClient — high-availability HTTP+WebSocket client with ephemeral /upload/image "
        "multipart push (no local path dependency), WebSocket execution telemetry with HTTP poll "
        "fallback, RenderTimeoutError circuit breaker (configurable timeout + poll_interval), "
        "VRAM garbage collection via POST /free, and mandatory output repatriation to "
        "outputs/production/ with timestamped filenames; "
        "(3) ComfyUIRenderBackend — @register_backend(BackendType.COMFYUI_RENDER) plugin with "
        "validate_config() backend-owned parameter normalization, graceful degradation manifests "
        "when ComfyUI is offline, and strongly-typed COMFYUI_RENDER_REPORT ArtifactManifest "
        "carrying prompt_id, mutation_count, render_elapsed_seconds, vram_freed, and blueprint_name "
        "for downstream GA fitness evaluation. Extended BackendType enum, BackendCapability enum, "
        "ArtifactFamily enum, and backend_registry auto-import chain. All three SESSION-151 "
        "anti-pattern red lines enforced and tested: (a) no hardcoded ComfyUI node IDs — "
        "source code audit confirms _meta.title semantic matching only; (b) poll loop deadlock "
        "prevention — time.sleep() + RenderTimeoutError verified by test; (c) output repatriation — "
        "all renders saved to outputs/production/. 29/29 tests PASS."
    ),
    "files_touched": [
        "mathart/backend/__init__.py",
        "mathart/backend/comfy_mutator.py",
        "mathart/backend/comfy_client.py",
        "mathart/backend/comfyui_render_backend.py",
        "mathart/core/backend_types.py",
        "mathart/core/backend_registry.py",
        "mathart/core/artifact_schema.py",
        "tests/test_comfyui_render_backend.py",
        "outputs/production/.gitkeep",
        "scripts/update_brain_session151.py",
        "SESSION_HANDOFF.md",
        "PROJECT_BRAIN.json"
    ],
    "validation": "tests/test_comfyui_render_backend.py -> 29/29 PASSED (mutator 9, client 8, backend 7, integration 3, red-line guards 2)",
    "next_gaps": [
        "P1-SESSION-151-GA-FITNESS-EVALUATOR: Wire COMFYUI_RENDER_REPORT metadata into GeneticAlgorithm fitness scoring function",
        "P1-SESSION-151-BATCH-RENDER-LANE: Add batch render support to PDG mass_production_factory ai_render_stage",
        "P1-SESSION-151-WEBSOCKET-PROGRESS-BAR: Surface WebSocket progress events to CLI wizard TUI",
        "P2-SESSION-151-MULTI-WORKFLOW-STRATEGY: Support multiple workflow blueprints per render batch (style A/B testing)",
        "P2-SESSION-151-COMFYUI-MODEL-CACHE: Pre-warm model cache before batch render to avoid cold-start latency"
    ]
}
recent.insert(0, session_151_recent)
brain["recent_sessions"] = recent

# --- Architecture decisions: add SESSION-151 ---
decisions = brain.get("architecture_decisions", [])
decisions.append({
    "session_id": "SESSION-151",
    "topic": "comfyui_bff_payload_mutation_and_headless_render_architecture",
    "decision": (
        "ComfyUI workflow manipulation MUST use semantic _meta.title marker matching "
        "(e.g., '[MathArt_Prompt]', '[MathArt_Input_Image]') for node discovery. "
        "Hardcoded numeric node IDs (e.g., '3', '15') are STRICTLY FORBIDDEN because "
        "ComfyUI regenerates node IDs on every workflow edit. Image assets MUST be "
        "uploaded via the /upload/image multipart endpoint (ephemeral push), NEVER "
        "referenced by local filesystem paths. The HTTP poll loop MUST include "
        "time.sleep(poll_interval) and a configurable timeout with RenderTimeoutError "
        "circuit breaker. All rendered outputs MUST be repatriated from ComfyUI's "
        "internal output folder to outputs/production/ with timestamped filenames. "
        "VRAM MUST be freed via POST /free after every render batch."
    ),
    "implication": (
        "The BFF mutation architecture makes the render pipeline resilient to "
        "workflow edits — artists can modify ComfyUI workflows freely without "
        "breaking the automation. The ephemeral upload pattern eliminates "
        "cross-platform path issues (Windows drive letters, WSL mount points). "
        "The timeout circuit breaker prevents terminal deadlocks during long renders. "
        "The output repatriation ensures all production assets are version-controlled "
        "and discoverable. The VRAM GC prevents OOM crashes during batch rendering. "
        "The strongly-typed COMFYUI_RENDER_REPORT manifest provides the exact "
        "metadata contract needed for the upcoming GA fitness evaluator to score "
        "render quality without inspecting image files directly."
    )
})
brain["architecture_decisions"] = decisions

# --- Write back ---
with open(BRAIN_PATH, "w", encoding="utf-8") as f:
    json.dump(brain, f, indent=2, ensure_ascii=False)

print("PROJECT_BRAIN.json updated for SESSION-151")
print(f"  version: {brain['version']}")
print(f"  last_session_id: {brain['last_session_id']}")
print(f"  total_iterations: {brain['total_iterations']}")
print(f"  total_code_lines: {brain['total_code_lines']}")
