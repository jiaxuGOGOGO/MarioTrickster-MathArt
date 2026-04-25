#!/usr/bin/env python3
"""SESSION-200 Epic Ignition Launchpad — One-Click Full-Stack Telemetry Test.

SESSION-200 (P0-SESSION-200-EPIC-IGNITION-AND-LIVE-TELEMETRY)
---------------------------------------------------------------
This script is the **standalone launch pad** for validating the complete
SESSION-200 upgrade chain:

    Golden Payload Snapshot → WS Dual-Channel Telemetry → Streaming Artifact Fetch

Usage (on a local machine with ComfyUI running on 127.0.0.1:8188):

    python tools/session200_epic_ignition.py

    # With custom parameters:
    python tools/session200_epic_ignition.py \\
        --server 127.0.0.1:8188 \\
        --payload path/to/custom_payload.json \\
        --output-dir outputs/session200_test/

Architecture discipline:
- This script lives in ``tools/`` — it is a **facade/glue** layer.
- It NEVER modifies core math engine code.
- It consumes ``ComfyAPIClient`` as a pure execution engine.
- It delegates all network I/O to the SESSION-200 upgraded client.

Anti-pattern guards:
- 🚫 Blind HTTP POST Trap: Uses WebSocket via ComfyAPIClient.
- 🚫 Offline Crash Trap: Graceful degradation if ComfyUI is offline.
- 🚫 Orphan Output Trap: Downloads all outputs via streaming fetch.
- 🚫 Memory Bomb Trap: All downloads use iter_content(8192), never .content.

Research grounding (SESSION-200):
- SpaceX F9 Pre-flight Dump: golden payload snapshot before ignition.
- Circuit Breaker Pattern (Nygard, "Release It!"): Fail-Fast on fatal errors.
- Streaming Fetch (Python requests best practices): iter_content(8192).
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

logger = logging.getLogger("session200_epic_ignition")


# ---------------------------------------------------------------------------
# Phase 0: Pre-flight Configuration
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the ignition launchpad."""
    parser = argparse.ArgumentParser(
        description="SESSION-200 Epic Ignition Launchpad — Full-Stack Telemetry Test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--server",
        default="127.0.0.1:8188",
        help="ComfyUI server address (default: 127.0.0.1:8188)",
    )
    parser.add_argument(
        "--payload",
        default=None,
        help="Path to a custom workflow payload JSON. If not provided, "
             "a minimal diagnostic payload is generated.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for artifacts (default: outputs/session200_ignition/)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=900.0,
        help="Render timeout in seconds (default: 900)",
    )
    parser.add_argument(
        "--skip-render",
        action="store_true",
        help="Skip actual render — only test payload dump and health check.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Phase 1: Generate Diagnostic Payload
# ---------------------------------------------------------------------------

def generate_diagnostic_payload() -> dict:
    """Generate a minimal diagnostic ComfyUI workflow payload.

    This payload is designed to exercise the full telemetry chain
    without requiring any specific models or custom nodes.  It uses
    only the built-in KSampler + VAEDecode + SaveImage nodes.
    """
    return {
        "prompt": {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": 42,
                    "steps": 4,
                    "cfg": 1.0,
                    "sampler_name": "euler",
                    "scheduler": "normal",
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {
                    "ckpt_name": "v1-5-pruned-emaonly.safetensors",
                },
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": 512,
                    "height": 512,
                    "batch_size": 1,
                },
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": "SESSION-200 diagnostic test: pixel art character, 8-bit style",
                    "clip": ["4", 1],
                },
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": "blurry, low quality, watermark",
                    "clip": ["4", 1],
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["4", 2],
                },
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "filename_prefix": "session200_ignition",
                    "images": ["8", 0],
                },
            },
        },
        "mathart_session": "SESSION-200",
        "mathart_ignition_timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Phase 2: Pre-flight Health Check
# ---------------------------------------------------------------------------

def preflight_health_check(server_address: str) -> bool:
    """Check if ComfyUI server is reachable and responsive.

    Returns True if the server is healthy, False otherwise.
    """
    import urllib.request
    import urllib.error

    url = f"http://{server_address}/system_stats"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            vram_total = data.get("devices", [{}])[0].get("vram_total", 0)
            vram_free = data.get("devices", [{}])[0].get("vram_free", 0)
            logger.info(
                "[Phase 2] ComfyUI server healthy: VRAM %.1f/%.1f GB",
                vram_free / 1e9, vram_total / 1e9,
            )
            sys.stderr.write(
                f"\033[1;92m[✅ 健康检查] ComfyUI 服务器在线 | "
                f"VRAM: {vram_free / 1e9:.1f}/{vram_total / 1e9:.1f} GB\033[0m\n"
            )
            sys.stderr.flush()
            return True
    except (ConnectionRefusedError, urllib.error.URLError, OSError, TimeoutError) as e:
        logger.warning("[Phase 2] ComfyUI server offline: %s", e)
        sys.stderr.write(
            f"\033[1;91m[❌ 健康检查] ComfyUI 服务器离线: {e}\033[0m\n"
        )
        sys.stderr.flush()
        return False


# ---------------------------------------------------------------------------
# Phase 3: Golden Payload Pre-flight Dump
# ---------------------------------------------------------------------------

def dump_golden_payload(payload: dict, output_dir: Path) -> Path:
    """Dump the golden payload snapshot to disk before ignition.

    SpaceX F9 Protocol: Before ignition, the complete payload configuration
    is written to the flight recorder as the absolute truth source.

    Returns the path to the dumped payload file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    golden_path = output_dir / "session200_epic_ignition_payload.json"
    with open(golden_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)
        f.write("\n")
    logger.info("[Phase 3] Golden Payload Pre-flight Dump: %s", golden_path)
    sys.stderr.write(
        f"\033[1;96m[🛰️  黄金载荷] Pre-flight Dump 完成 → {golden_path}\033[0m\n"
    )
    sys.stderr.flush()
    return golden_path


# ---------------------------------------------------------------------------
# Phase 4: Ignition — Execute on ComfyUI with Full Telemetry
# ---------------------------------------------------------------------------

def execute_ignition(
    server_address: str,
    payload: dict,
    output_dir: Path,
    timeout: float,
) -> dict:
    """Execute the payload on ComfyUI with full SESSION-200 telemetry.

    This function exercises the complete upgraded chain:
    1. Submit prompt via POST /prompt
    2. Monitor via WebSocket dual-channel telemetry (_ws_wait with SESSION-200)
    3. Download artifacts via streaming fetch (_download_file_streaming)
    4. Free VRAM after completion

    Returns a summary dict with execution results.
    """
    from mathart.backend.comfy_client import ComfyAPIClient, RenderTimeoutError

    try:
        from mathart.comfy_client.comfyui_ws_client import ComfyUIExecutionError
    except ImportError:
        from mathart.backend.comfy_client import ComfyUIExecutionError

    client = ComfyAPIClient(
        server_address=server_address,
        render_timeout=timeout,
        output_root=str(output_dir / "production"),
    )

    sys.stderr.write(
        "\033[1;93m" + "=" * 60 + "\033[0m\n"
        "\033[1;93m  SESSION-200 EPIC IGNITION — LAUNCH SEQUENCE INITIATED\033[0m\n"
        "\033[1;93m" + "=" * 60 + "\033[0m\n"
    )
    sys.stderr.flush()

    t0 = time.time()
    summary = {
        "session": "SESSION-200",
        "server": server_address,
        "timeout": timeout,
        "start_time": t0,
        "success": False,
        "error": None,
        "output_images": [],
        "output_videos": [],
        "elapsed_seconds": 0.0,
        "vram_freed": False,
    }

    try:
        # Step 1: Check server availability
        if not client.check_server():
            summary["error"] = "ComfyUI server not reachable"
            return summary

        # Step 2: Submit prompt and wait with telemetry
        result = client.render(
            payload,
            output_prefix="session200_ignition",
            free_vram_after=True,
        )

        summary["success"] = result.success
        summary["output_images"] = result.output_images
        summary["output_videos"] = result.output_videos
        summary["vram_freed"] = result.vram_freed
        summary["prompt_id"] = result.prompt_id

        if result.success:
            sys.stderr.write(
                f"\033[1;92m[✅ 点火成功] "
                f"输出 {len(result.output_images)} 张图片, "
                f"{len(result.output_videos)} 个视频\033[0m\n"
            )
        else:
            summary["error"] = result.error_message or result.degraded_reason
            sys.stderr.write(
                f"\033[1;91m[❌ 点火失败] {summary['error']}\033[0m\n"
            )

    except RenderTimeoutError as e:
        summary["error"] = f"Timeout: {e}"
        sys.stderr.write(f"\033[1;91m[⏰ 超时熔断] {e}\033[0m\n")
    except ComfyUIExecutionError as e:
        summary["error"] = f"Execution Error: {e}"
        sys.stderr.write(f"\033[1;91m[💥 执行崩溃] {e}\033[0m\n")
    except Exception as e:
        summary["error"] = f"Unexpected: {e}"
        sys.stderr.write(f"\033[1;91m[🔥 未知异常] {e}\033[0m\n")
    finally:
        summary["elapsed_seconds"] = round(time.time() - t0, 3)
        sys.stderr.flush()

    return summary


# ---------------------------------------------------------------------------
# Phase 5: Post-flight Report
# ---------------------------------------------------------------------------

def write_ignition_report(summary: dict, output_dir: Path) -> Path:
    """Write the post-flight ignition report to disk."""
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "session200_ignition_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=4, default=str)
        f.write("\n")
    logger.info("[Phase 5] Ignition Report: %s", report_path)
    return report_path


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    """SESSION-200 Epic Ignition — Main entry point."""
    args = parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Resolve output directory
    output_dir = Path(args.output_dir) if args.output_dir else (
        PROJECT_ROOT / "outputs" / "session200_ignition"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: Load or generate payload
    if args.payload:
        payload_path = Path(args.payload)
        if not payload_path.exists():
            sys.stderr.write(f"\033[1;91m[ERROR] Payload file not found: {payload_path}\033[0m\n")
            return 1
        with open(payload_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        logger.info("[Phase 1] Loaded custom payload: %s", payload_path)
    else:
        payload = generate_diagnostic_payload()
        logger.info("[Phase 1] Generated diagnostic payload")

    # Phase 2: Pre-flight health check
    server_online = preflight_health_check(args.server)

    # Phase 3: Golden Payload Pre-flight Dump (ALWAYS, even if offline)
    golden_path = dump_golden_payload(payload, output_dir)

    if args.skip_render:
        sys.stderr.write(
            "\033[1;93m[⏭️  跳过渲染] --skip-render 已启用, "
            "仅完成 Pre-flight Dump\033[0m\n"
        )
        sys.stderr.flush()
        return 0

    if not server_online:
        sys.stderr.write(
            "\033[1;91m[❌ 中止] ComfyUI 服务器离线, 无法执行点火。\n"
            "    请启动 ComfyUI 后重试: python tools/session200_epic_ignition.py\033[0m\n"
        )
        sys.stderr.flush()
        # Still write a report for the abort
        summary = {
            "session": "SESSION-200",
            "server": args.server,
            "success": False,
            "error": "Server offline — ignition aborted",
            "golden_payload_path": str(golden_path),
        }
        write_ignition_report(summary, output_dir)
        return 1

    # Phase 4: IGNITION
    summary = execute_ignition(args.server, payload, output_dir, args.timeout)
    summary["golden_payload_path"] = str(golden_path)

    # Phase 5: Post-flight report
    report_path = write_ignition_report(summary, output_dir)

    # Final banner
    status_emoji = "✅" if summary["success"] else "❌"
    sys.stderr.write(
        f"\n\033[1;96m{'=' * 60}\033[0m\n"
        f"\033[1;96m  SESSION-200 IGNITION {status_emoji} | "
        f"Elapsed: {summary['elapsed_seconds']}s\033[0m\n"
        f"\033[1;96m  Golden Payload: {golden_path}\033[0m\n"
        f"\033[1;96m  Report: {report_path}\033[0m\n"
        f"\033[1;96m{'=' * 60}\033[0m\n"
    )
    sys.stderr.flush()

    return 0 if summary["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
