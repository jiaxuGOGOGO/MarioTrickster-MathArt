"""ComfyUI Backend — Dynamic Payload Mutation & Headless Render Client.

SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH)
----------------------------------------------------------
This package implements the end-to-end ComfyUI dynamic payload injection
and headless rendering closed loop:

1. ``comfy_mutator`` — Semantic JSON tree traversal mutator that injects
   upstream proxy images and vibe prompts into workflow_api.json blueprints
   using ``_meta.title`` string matching (NEVER hardcoded node IDs).

2. ``comfy_client`` — High-availability API client wrapping ``/upload/image``,
   ``/prompt``, ``/history``, ``/view``, and ``/free`` endpoints with
   timeout-guarded polling and VRAM garbage collection.

3. ``comfyui_render_backend`` — Registry-native ``@register_backend`` plugin
   that mounts as a Render Lane on the PDG production bus without modifying
   any trunk orchestrator code.

Architecture Discipline:
- Zero hardcoded ComfyUI node IDs — all addressing via ``_meta.title``
- Ephemeral asset upload via ``/upload/image`` multipart — no local path deps
- OOM prevention via ``/free`` VRAM garbage collection after each batch
- Strong-typed ``ArtifactManifest`` output with ``COMFYUI_RENDER_REPORT`` family
"""
from mathart.backend.comfy_mutator import ComfyWorkflowMutator, MutationError
from mathart.backend.comfy_client import ComfyAPIClient, RenderTimeoutError

__all__ = [
    "ComfyWorkflowMutator",
    "MutationError",
    "ComfyAPIClient",
    "RenderTimeoutError",
]
