"""ComfyUI Backend — static V6 initializer and headless client utilities.

The full-array AI render stream backend was archived in `_legacy_archive_v5`.
This package now exposes only the retained static ComfyUI mutation/client lane.
"""
from mathart.backend.comfy_mutator import ComfyWorkflowMutator, MutationError
from mathart.backend.comfy_client import ComfyAPIClient, RenderTimeoutError

__all__ = [
    "ComfyWorkflowMutator",
    "MutationError",
    "ComfyAPIClient",
    "RenderTimeoutError",
]
