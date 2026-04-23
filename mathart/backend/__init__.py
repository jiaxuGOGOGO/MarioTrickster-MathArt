"""ComfyUI Backend — Dynamic Payload Mutation, Headless Render Client & AI Stream.

SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH)
SESSION-163 (P0-SESSION-161-COMFYUI-API-BRIDGE)
----------------------------------------------------------
This package implements the end-to-end ComfyUI dynamic payload injection,
headless rendering closed loop, and full-array artifact hydration streaming:

1. ``comfy_mutator`` — Semantic JSON tree traversal mutator that injects
   upstream proxy images and vibe prompts into workflow_api.json blueprints
   using ``_meta.title`` string matching (NEVER hardcoded node IDs).

2. ``comfy_client`` — High-availability API client wrapping ``/upload/image``,
   ``/prompt``, ``/history``, ``/view``, and ``/free`` endpoints with
   timeout-guarded polling and VRAM garbage collection.

3. ``comfyui_render_backend`` — Registry-native ``@register_backend`` plugin
   that mounts as a Render Lane on the PDG production bus without modifying
   any trunk orchestrator code.

4. ``ai_render_stream_backend`` — Full-array artifact hydration backend that
   iterates all motion actions from the dynamic registry, streams baked guide
   sequences (Albedo/Normal/Depth) to ComfyUI with circuit breaker protection,
   and hydrates the pipeline context with renamed AI-rendered outputs.

Architecture Discipline:
- Zero hardcoded ComfyUI node IDs — all addressing via ``_meta.title``
- Ephemeral asset upload via ``/upload/image`` multipart — no local path deps
- OOM prevention via ``/free`` VRAM garbage collection after each batch
- Circuit breaker (Michael Nygard) with exponential backoff + jitter
- Strong-typed ``ArtifactManifest`` output with ``COMFYUI_RENDER_REPORT`` and
  ``AI_RENDER_STREAM_REPORT`` families
"""
from mathart.backend.comfy_mutator import ComfyWorkflowMutator, MutationError
from mathart.backend.comfy_client import ComfyAPIClient, RenderTimeoutError

__all__ = [
    "ComfyWorkflowMutator",
    "MutationError",
    "ComfyAPIClient",
    "RenderTimeoutError",
]
