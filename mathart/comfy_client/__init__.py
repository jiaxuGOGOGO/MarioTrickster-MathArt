"""ComfyUI WebSocket execution client for production-grade workflow automation.

SESSION-087: Provides ``ComfyUIClient`` for end-to-end async execution
with WebSocket monitoring and graceful degradation.
"""
from .comfyui_ws_client import ComfyUIClient, ExecutionResult

__all__ = ["ComfyUIClient", "ExecutionResult"]
