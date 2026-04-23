"""ComfyUI WebSocket execution client for production-grade workflow automation.

SESSION-087: Provides ``ComfyUIClient`` for end-to-end async execution
with WebSocket monitoring and graceful degradation.

SESSION-168: Added ``ComfyUIExecutionError`` — the Poison Pill exception
that tears the WebSocket listen loop on fatal ``execution_error`` events,
preventing catastrophic deadlock when ComfyUI reports unrecoverable crashes.
"""
from .comfyui_ws_client import ComfyUIClient, ComfyUIExecutionError, ExecutionResult

__all__ = ["ComfyUIClient", "ComfyUIExecutionError", "ExecutionResult"]
