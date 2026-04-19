#!/usr/bin/env python3
"""End-to-end one-click SparseCtrl + AnimateDiff pipeline runner.

SESSION-087 (P1-AI-2D-SPARSECTRL endpoint closure)
----------------------------------------------------
This script is the **production-grade glue code** that orchestrates the full
MarioTrickster-MathArt visual pipeline from physics simulation to pixel output:

    Physics Engine → SDF Render → Normal/Depth/RGB Bake
        → ComfyUI Preset Assembly → WebSocket Execution → Image Download

Usage (on a local machine with ComfyUI running on 127.0.0.1:8188):

    python tools/run_sparsectrl_pipeline.py

    # With custom parameters:
    python tools/run_sparsectrl_pipeline.py \\
        --server 127.0.0.1:8188 \\
        --frames 16 \\
        --width 512 --height 512 \\
        --steps 20 --cfg 7.5 \\
        --prompt "pixel art game character, Dead Cells style"

Architecture discipline:
- This script lives in ``tools/`` — it is a **facade/glue** layer.
- It NEVER modifies core math engine code.
- It consumes ``AntiFlickerRenderBackend`` output manifests and
  ``ComfyUIPresetManager`` payloads as pure data.
- It delegates network I/O to ``ComfyUIClient``.

Anti-pattern guards:
- 🚫 Blind HTTP POST Trap: Uses WebSocket via ComfyUIClient.
- 🚫 Offline Crash Trap: Graceful degradation if ComfyUI is offline.
- 🚫 Orphan Output Trap: Downloads all outputs to project directory.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mathart.animation.comfyui_preset_manager import ComfyUIPresetManager
from mathart.comfy_client.comfyui_ws_client import ComfyUIClient, ExecutionResult

logger = logging.getLogger("run_sparsectrl_pipeline")


# ---------------------------------------------------------------------------
# Phase 1: Generate guide frame sequences from physics engine
# ---------------------------------------------------------------------------

def generate_guide_sequences(
    *,
    output_dir: Path,
    frame_count: int = 16,
    width: int = 512,
    height: int = 512,
) -> dict[str, Path]:
    """Generate normal/depth/RGB guide frame sequences from the SDF renderer.

    This function invokes the project's industrial renderer to produce
    multi-channel guide frames from the procedural SDF character.

    Returns
    -------
    dict[str, Path]
        Mapping of channel name to directory path:
        ``{"normal": Path, "depth": Path, "rgb": Path}``
    """
    normal_dir = output_dir / "guides" / "normal"
    depth_dir = output_dir / "guides" / "depth"
    rgb_dir = output_dir / "guides" / "rgb"

    for d in (normal_dir, depth_dir, rgb_dir):
        d.mkdir(parents=True, exist_ok=True)

    try:
        from mathart.animation.skeleton import Skeleton
        from mathart.animation.parts import CharacterStyle
        from mathart.animation.presets import idle_animation
        from mathart.animation.industrial_renderer import IndustrialRenderer

        logger.info(
            "[Phase 1] Rendering %d guide frames at %dx%d...",
            frame_count, width, height,
        )

        skeleton = Skeleton.create_humanoid()
        style = CharacterStyle()
        renderer = IndustrialRenderer(width=width, height=height)

        for frame_idx in range(frame_count):
            t = frame_idx / max(frame_count - 1, 1)
            pose = idle_animation(skeleton, t)
            result = renderer.render(skeleton, pose, style)

            # Save each channel as individual frame
            result.normal_map_image.save(
                str(normal_dir / f"frame_{frame_idx:04d}.png")
            )
            result.depth_map_image.save(
                str(depth_dir / f"frame_{frame_idx:04d}.png")
            )
            result.albedo_image.save(
                str(rgb_dir / f"frame_{frame_idx:04d}.png")
            )

        logger.info(
            "[Phase 1] Guide sequences generated: %d frames per channel",
            frame_count,
        )

    except ImportError as e:
        logger.warning(
            "[Phase 1] Industrial renderer not available (%s). "
            "Creating placeholder guide frames for testing.",
            e,
        )
        _create_placeholder_frames(normal_dir, depth_dir, rgb_dir, frame_count, width, height)

    except Exception as e:
        logger.warning(
            "[Phase 1] Renderer error (%s). "
            "Creating placeholder guide frames for testing.",
            e,
        )
        _create_placeholder_frames(normal_dir, depth_dir, rgb_dir, frame_count, width, height)

    return {
        "normal": normal_dir,
        "depth": depth_dir,
        "rgb": rgb_dir,
    }


def _create_placeholder_frames(
    normal_dir: Path,
    depth_dir: Path,
    rgb_dir: Path,
    frame_count: int,
    width: int,
    height: int,
) -> None:
    """Create minimal placeholder PNG frames for offline testing."""
    try:
        from PIL import Image
    except ImportError:
        logger.error("PIL not available — cannot create placeholder frames")
        return

    for frame_idx in range(frame_count):
        for channel_dir, color in [
            (normal_dir, (128, 128, 255)),   # Normal map blue
            (depth_dir, (200, 200, 200)),     # Depth gray
            (rgb_dir, (100, 150, 200)),       # RGB reference
        ]:
            img = Image.new("RGB", (width, height), color)
            img.save(str(channel_dir / f"frame_{frame_idx:04d}.png"))

    logger.info(
        "[Phase 1] Placeholder guide frames created: %d frames per channel",
        frame_count,
    )


# ---------------------------------------------------------------------------
# Phase 2: Assemble SparseCtrl + AnimateDiff preset payload
# ---------------------------------------------------------------------------

def assemble_sparsectrl_payload(
    *,
    guide_dirs: dict[str, Path],
    prompt: str,
    negative_prompt: str,
    frame_count: int,
    width: int,
    height: int,
    steps: int,
    cfg_scale: float,
    seed: int,
    frame_rate: int,
) -> dict:
    """Assemble the ComfyUI workflow payload using the preset manager.

    This function is a thin wrapper around
    ``ComfyUIPresetManager.assemble_sequence_payload()``.
    It ONLY passes data — no topology manipulation.
    """
    logger.info("[Phase 2] Assembling SparseCtrl + AnimateDiff payload...")

    manager = ComfyUIPresetManager()
    payload = manager.assemble_sequence_payload(
        normal_sequence_dir=guide_dirs["normal"],
        depth_sequence_dir=guide_dirs["depth"],
        rgb_sequence_dir=guide_dirs["rgb"],
        prompt=prompt,
        negative_prompt=negative_prompt,
        frame_count=frame_count,
        width=width,
        height=height,
        steps=steps,
        cfg_scale=cfg_scale,
        seed=seed,
        frame_rate=frame_rate,
    )

    lock_manifest = payload.get("mathart_lock_manifest", {})
    logger.info(
        "[Phase 2] Payload assembled: %d nodes, %d bindings, seed=%d",
        lock_manifest.get("node_count", 0),
        len(lock_manifest.get("semantic_bindings", [])),
        lock_manifest.get("seed", -1),
    )

    return payload


# ---------------------------------------------------------------------------
# Phase 3: Execute via ComfyUIClient (WebSocket + graceful degradation)
# ---------------------------------------------------------------------------

def execute_on_comfyui(
    *,
    payload: dict,
    server_address: str,
    output_root: Path,
) -> ExecutionResult:
    """Submit payload to ComfyUI and wait for completion via WebSocket.

    If the server is offline, returns a degraded result without crashing.
    """
    logger.info("[Phase 3] Submitting to ComfyUI at %s...", server_address)

    client = ComfyUIClient(
        server_address=server_address,
        output_root=output_root,
    )

    # Health check first
    if not client.is_server_online():
        logger.warning(
            "[Phase 3] ComfyUI server at %s is OFFLINE. "
            "Payload has been assembled but cannot be executed. "
            "Start ComfyUI and re-run this script.",
            server_address,
        )
        return ExecutionResult(
            success=False,
            degraded=True,
            degraded_reason=f"ComfyUI server at {server_address} is offline",
        )

    result = client.execute_workflow(
        payload,
        run_label="sparsectrl_pipeline",
    )

    return result


# ---------------------------------------------------------------------------
# Phase 4: Save execution report
# ---------------------------------------------------------------------------

def save_execution_report(
    *,
    result: ExecutionResult,
    payload: dict,
    output_dir: Path,
    args: argparse.Namespace,
) -> Path:
    """Save a comprehensive execution report JSON."""
    report_dir = output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    report_path = report_dir / f"execution_report_{timestamp}.json"

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "pipeline": "sparsectrl_animatediff",
        "session": "SESSION-087",
        "execution_result": result.to_dict(),
        "parameters": {
            "server": args.server,
            "frames": args.frames,
            "width": args.width,
            "height": args.height,
            "steps": args.steps,
            "cfg": args.cfg,
            "seed": args.seed,
            "frame_rate": args.frame_rate,
            "prompt": args.prompt,
            "negative_prompt": args.negative_prompt,
        },
        "lock_manifest": payload.get("mathart_lock_manifest", {}),
    }

    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Also save the raw payload for debugging
    payload_path = report_dir / f"payload_{timestamp}.json"
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    logger.info("[Phase 4] Execution report saved: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "MarioTrickster-MathArt: End-to-end SparseCtrl + AnimateDiff pipeline.\n"
            "Generates physics-driven guide frames, assembles ComfyUI payload,\n"
            "and executes via WebSocket for zero-flicker pixel sequence output."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--server",
        default="127.0.0.1:8188",
        help="ComfyUI server address (default: 127.0.0.1:8188)",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=16,
        help="Number of frames to generate (default: 16)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=512,
        help="Frame width in pixels (default: 512)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=512,
        help="Frame height in pixels (default: 512)",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=20,
        help="Sampling steps (default: 20)",
    )
    parser.add_argument(
        "--cfg",
        type=float,
        default=7.5,
        help="CFG scale (default: 7.5)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=-1,
        help="Random seed (-1 for auto, default: -1)",
    )
    parser.add_argument(
        "--frame-rate",
        type=int,
        default=12,
        help="Output video frame rate (default: 12)",
    )
    parser.add_argument(
        "--prompt",
        default=(
            "pixel art game character sprite, Dead Cells style, "
            "detailed shading, clean linework, 2D side-scrolling action game"
        ),
        help="Style prompt for generation",
    )
    parser.add_argument(
        "--negative-prompt",
        default="blurry, low quality, distorted, deformed, 3D render, photo",
        help="Negative prompt",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: outputs/comfyui_renders/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Assemble payload but do not submit to ComfyUI",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    output_root = Path(args.output_dir) if args.output_dir else (
        PROJECT_ROOT / "outputs" / "comfyui_renders"
    )
    output_root.mkdir(parents=True, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = output_root / f"run_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("MarioTrickster-MathArt: SparseCtrl + AnimateDiff Pipeline")
    logger.info("=" * 70)

    # --- Phase 1: Generate guide frame sequences ---
    guide_dirs = generate_guide_sequences(
        output_dir=run_dir,
        frame_count=args.frames,
        width=args.width,
        height=args.height,
    )

    # --- Phase 2: Assemble ComfyUI payload ---
    payload = assemble_sparsectrl_payload(
        guide_dirs=guide_dirs,
        prompt=args.prompt,
        negative_prompt=args.negative_prompt,
        frame_count=args.frames,
        width=args.width,
        height=args.height,
        steps=args.steps,
        cfg_scale=args.cfg,
        seed=args.seed,
        frame_rate=args.frame_rate,
    )

    if args.dry_run:
        logger.info("[DRY RUN] Payload assembled but not submitted.")
        report_path = save_execution_report(
            result=ExecutionResult(
                success=False,
                degraded=True,
                degraded_reason="Dry run — payload not submitted",
            ),
            payload=payload,
            output_dir=run_dir,
            args=args,
        )
        logger.info("[DRY RUN] Report saved: %s", report_path)
        logger.info("[DRY RUN] Payload saved alongside report.")
        return 0

    # --- Phase 3: Execute on ComfyUI ---
    result = execute_on_comfyui(
        payload=payload,
        server_address=args.server,
        output_root=run_dir,
    )

    # --- Phase 4: Save report ---
    report_path = save_execution_report(
        result=result,
        payload=payload,
        output_dir=run_dir,
        args=args,
    )

    # --- Summary ---
    logger.info("=" * 70)
    if result.success:
        logger.info("PIPELINE COMPLETE — SUCCESS")
        logger.info("  Images: %d", len(result.output_images))
        logger.info("  Videos: %d", len(result.output_videos))
        logger.info("  Output: %s", result.output_dir)
        logger.info("  Time:   %.1fs", result.elapsed_seconds)
    elif result.degraded:
        logger.warning("PIPELINE COMPLETE — DEGRADED (server offline)")
        logger.warning("  Reason: %s", result.degraded_reason)
        logger.warning("  Payload assembled and saved to: %s", report_path)
        logger.warning(
            "  → Start ComfyUI on %s and re-run this script.",
            args.server,
        )
    else:
        logger.error("PIPELINE COMPLETE — FAILED")
        logger.error("  Error: %s", result.error_message)
    logger.info("=" * 70)

    return 0 if (result.success or result.degraded) else 1


if __name__ == "__main__":
    sys.exit(main())
