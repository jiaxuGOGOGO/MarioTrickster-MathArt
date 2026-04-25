"""
MathArt WebUI — SESSION-202 P0-ZERO-DEFECT-WEB-WORKSPACE.

Pure Python Gradio-based visual production console providing:
- Left panel: control area (action dropdown, reference image upload, VFX toggles)
- Right panel: progress monitoring and gallery/video playback

Architecture: Independent module that bridges Web UI inputs to the
underlying ``CreatorIntentSpec`` + ``IntentGateway`` + pipeline dispatch,
following the IoC Registry Pattern (严禁修改主干 if/else).
"""

__all__ = ["create_app", "WebUIBridge"]
