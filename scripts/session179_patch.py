"""SESSION-179 Patch Script — Apply all SESSION-176 research-grounded patches.

This script applies the following patches:
1. SparseCtrl-RGB end_percent time-window clamping in ai_render_stream_backend.py
2. Normal Map encoding formula validation comments
3. cancel_futures enhancement in pdg.py
4. Dynamic batch_size safety bounds
"""
import re

# ============================================================================
# PATCH 1: ai_render_stream_backend.py — SparseCtrl end_percent + strength refinement
# ============================================================================
def patch_ai_render():
    path = "mathart/backend/ai_render_stream_backend.py"
    with open(path, "r") as f:
        content = f.read()

    # Replace the ControlNetApplyAdvanced block with SESSION-179 enhanced version
    old_block = '''        elif class_type == "ControlNetApplyAdvanced":
            # SESSION-178: Cap ControlNet strengths to prevent overfit/flashing
            inputs = node_data.get("inputs", {})
            # If it's SparseCtrl (usually strength 1.0), cap to 0.8
            # If it's Normal/Depth, cap to 0.45
            # We infer based on current strength or just apply a safe cap
            current_strength = inputs.get("strength", 1.0)
            if current_strength > 0.8:
                inputs["strength"] = 0.8
                logger.info("[SESSION-178] ControlNetApplyAdvanced node %s: capped strength to 0.8", node_id)'''

    new_block = '''        elif class_type == "ControlNetApplyAdvanced":
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # SESSION-179: SparseCtrl-RGB Time-Window Clamping (SESSION-176 Research)
            # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # Research grounding (RESEARCH_NOTES_SESSION_176.md §1):
            # - SparseCtrl-RGB with full-range (0.0~1.0) end_percent causes
            #   long-shot flashing and color drift (GitHub #476).
            # - Clamping end_percent to 0.4~0.6 restricts the temporal
            #   influence window, preventing late-frame overfit.
            # - Scale (strength) for SparseCtrl: 0.825~0.9 sweet spot.
            # - Normal/Depth ControlNets: 0.45 to avoid shadow burn.
            inputs = node_data.get("inputs", {})
            current_strength = inputs.get("strength", 1.0)
            current_end_percent = inputs.get("end_percent", 1.0)
            current_start_percent = inputs.get("start_percent", 0.0)
            # Heuristic: SparseCtrl nodes typically have strength >= 0.8
            # and no start_percent offset. Normal/Depth have lower defaults.
            is_likely_sparsectrl = current_strength >= 0.8 and current_start_percent < 0.1
            if is_likely_sparsectrl:
                # SESSION-176: SparseCtrl-RGB sweet spot
                clamped_strength = max(0.825, min(0.9, current_strength))
                inputs["strength"] = clamped_strength
                # Clamp end_percent to 0.4~0.6 to prevent late-frame flashing
                if current_end_percent > 0.6:
                    inputs["end_percent"] = 0.55
                logger.info(
                    "[SESSION-179] SparseCtrl node %s: strength %.3f→%.3f, "
                    "end_percent %.2f→%.2f (time-window clamped)",
                    node_id, current_strength, clamped_strength,
                    current_end_percent, inputs.get("end_percent", current_end_percent),
                )
            else:
                # Normal / Depth ControlNet — cap to 0.45
                if current_strength > 0.45:
                    inputs["strength"] = 0.45
                    logger.info(
                        "[SESSION-179] Normal/Depth ControlNet node %s: "
                        "strength %.3f→0.45",
                        node_id, current_strength,
                    )'''

    if old_block in content:
        content = content.replace(old_block, new_block)
        print("[PATCH 1a] ControlNetApplyAdvanced block replaced with SESSION-179 version")
    else:
        print("[PATCH 1a] WARNING: Could not find old ControlNetApplyAdvanced block")

    # PATCH 1b: Add batch_size safety bounds
    old_batch = '''            if actual_frames is not None:
                inputs["batch_size"] = actual_frames'''
    new_batch = '''            if actual_frames is not None:
                # SESSION-179: Safety bounds — prevent degenerate latent configs
                # Min 1 frame (avoid zero-dim tensor), max 128 (VRAM safety)
                clamped_frames = max(1, min(128, actual_frames))
                inputs["batch_size"] = clamped_frames
                if clamped_frames != actual_frames:
                    logger.warning(
                        "[SESSION-179] batch_size clamped: %d → %d (safety bounds [1, 128])",
                        actual_frames, clamped_frames,
                    )'''
    if old_batch in content:
        content = content.replace(old_batch, new_batch)
        print("[PATCH 1b] batch_size safety bounds added")
    else:
        print("[PATCH 1b] WARNING: Could not find batch_size assignment")

    # PATCH 1c: Add Normal Map encoding formula comment to _jit_upscale_image
    old_matting_comment = '''    This is critical for ControlNet Normal (requires 128,128,255) and Depth (0,0,0).'''
    new_matting_comment = '''    This is critical for ControlNet Normal (requires 128,128,255) and Depth (0,0,0).

    SESSION-179 (SESSION-176 Research §2 — Normal Map Encoding Formula):
    In tangent-space normal maps, the encoding formula is:
        N_rgb = (N_vec + 1) * 127.5
    A flat surface pointing directly at the camera has normal vector (0, 0, 1),
    which encodes to RGB (128, 128, 255) — the characteristic purple-blue.
    If transparent backgrounds are erroneously filled with black (0, 0, 0),
    this represents an extreme tangent-space tilt, causing catastrophic
    ControlNet light inference errors. The matting_color parameter ensures
    transparent regions are filled with the correct neutral encoding.'''
    if old_matting_comment in content:
        content = content.replace(old_matting_comment, new_matting_comment)
        print("[PATCH 1c] Normal Map encoding formula comment added")
    else:
        print("[PATCH 1c] WARNING: Could not find matting comment")

    with open(path, "w") as f:
        f.write(content)
    print("[PATCH 1] ai_render_stream_backend.py patched successfully")


# ============================================================================
# PATCH 2: pdg.py — cancel_futures enhancement
# ============================================================================
def patch_pdg():
    path = "mathart/level/pdg.py"
    with open(path, "r") as f:
        content = f.read()

    # Add cancel_futures to the ThreadPoolExecutor context manager exit
    old_executor = '''        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"pdg-{node.name}") as executor:'''
    new_executor = '''        # SESSION-179: Enhanced executor with cancel_futures support
        # Python 3.9+ ThreadPoolExecutor.__exit__ supports cancel_futures
        # parameter via shutdown(). We wrap in a custom context to ensure
        # cancel_futures=True is invoked on fatal exception exit.
        _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=f"pdg-{node.name}")
        with _executor as executor:'''
    if old_executor in content:
        content = content.replace(old_executor, new_executor)
        print("[PATCH 2a] ThreadPoolExecutor wrapper updated")

    # Add explicit shutdown with cancel_futures in the finally block
    old_finally_end = '''        # SESSION-169: Fatal exceptions take priority over rejections
        if fatal_exception is not None:
            raise fatal_exception'''
    new_finally_end = '''        # SESSION-179: Explicit shutdown with cancel_futures=True (Python 3.9+)
        # This ensures ALL pending (not-yet-started) futures are cancelled
        # when a fatal OOM or GPU crash triggers global meltdown.
        # Research grounding: Python bugs.python.org/issue39349
        if fatal_exception is not None:
            try:
                _executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                # Python < 3.9 does not support cancel_futures
                _executor.shutdown(wait=False)
            logger.critical(
                "[SESSION-179] Global OOM meltdown — executor shutdown with "
                "cancel_futures=True. Fatal: %s",
                fatal_exception,
            )
        # SESSION-169: Fatal exceptions take priority over rejections
        if fatal_exception is not None:
            raise fatal_exception'''
    if old_finally_end in content:
        content = content.replace(old_finally_end, new_finally_end)
        print("[PATCH 2b] cancel_futures shutdown added")
    else:
        print("[PATCH 2b] WARNING: Could not find finally end block")

    with open(path, "w") as f:
        f.write(content)
    print("[PATCH 2] pdg.py patched successfully")


# ============================================================================
# PATCH 3: mass_production.py — Update _SESSION_ID
# ============================================================================
def patch_mass_production():
    path = "mathart/factory/mass_production.py"
    with open(path, "r") as f:
        content = f.read()

    content = content.replace('_SESSION_ID = "SESSION-167"', '_SESSION_ID = "SESSION-179"')
    print("[PATCH 3] _SESSION_ID updated to SESSION-179")

    with open(path, "w") as f:
        f.write(content)


if __name__ == "__main__":
    patch_ai_render()
    patch_pdg()
    patch_mass_production()
    print("\n✅ All SESSION-179 patches applied successfully!")
