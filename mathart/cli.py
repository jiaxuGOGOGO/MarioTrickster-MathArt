"""Command-line interface for the MathArt registry-backed pipeline.

The CLI is intentionally thin: it discovers registered backends from the
registry at runtime, builds help text from backend metadata, and delegates the
actual work to the shared pipeline/registry infrastructure.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# SESSION-148: Global stdout/stderr safe-encoding shield (see cli_wizard.py
# for the full rationale).  Duplicated here because `python -m mathart` and
# direct ``mathart.cli`` invocations can enter the process BEFORE the
# wizard module is imported, which means the wizard-side guard would be
# installed too late to protect any registry/backend banner printed by
# ``_configure_logging`` or the argparse help path.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        # Never allow the shield to become a new failure mode.
        pass

LOGGER = logging.getLogger("mathart.cli")


def _configure_logging(*, quiet: bool = False) -> None:
    # SESSION-146: Install the blackbox flight recorder first so that
    # the file handler (DEBUG) + console handler (WARNING) are already
    # in place.  We then only adjust the *root* logger's level for
    # non-wizard CLI commands — never overwrite handlers with force=True,
    # which would destroy the blackbox file handler.
    try:
        from mathart.core.logger import install_blackbox
        install_blackbox()
    except Exception:  # pragma: no cover — defensive
        pass
    # For non-wizard CLI paths, the root logger may need a lower
    # threshold so that registry/backend INFO messages reach stderr.
    # But the blackbox console handler already gates at WARNING,
    # so we only touch the root level here.
    root = logging.getLogger()
    root.setLevel(logging.WARNING if quiet else logging.INFO)


def _parse_scalar(value: str) -> Any:
    text = value.strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    try:
        if text.startswith(("{", "[", '"')):
            return json.loads(text)
        if "." in text:
            return float(text)
        return int(text)
    except (ValueError, json.JSONDecodeError):
        return text


def _deep_set(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    parts = [part for part in dotted_key.split(".") if part]
    if not parts:
        raise ValueError("Parameter key cannot be empty")
    cursor = target
    for part in parts[:-1]:
        next_cursor = cursor.get(part)
        if not isinstance(next_cursor, dict):
            next_cursor = {}
            cursor[part] = next_cursor
        cursor = next_cursor
    cursor[parts[-1]] = value


def _load_json_config(config_path: str | None) -> dict[str, Any]:
    if not config_path:
        return {}
    path = Path(config_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Config JSON must contain an object at the top level")
    return data


def _merge_context(args: argparse.Namespace) -> dict[str, Any]:
    context = _load_json_config(args.config)
    for item in args.set_values:
        if "=" not in item:
            raise ValueError(f"Invalid --set value {item!r}; expected key=value")
        key, raw_value = item.split("=", 1)
        _deep_set(context, key.strip(), _parse_scalar(raw_value))
    context.setdefault("output_dir", str(Path(args.output_dir).resolve()))
    if args.name:
        context.setdefault("name", args.name)
    if args.session_id:
        context.setdefault("session_id", args.session_id)
    return context


def _manifest_path_for(manifest: Any, output_dir: str, backend_name: str) -> Path:
    from mathart.core.backend_types import backend_type_value

    file_name = f"{backend_type_value(backend_name)}_artifact_manifest.json"
    return Path(output_dir).resolve() / file_name


def _backend_payload(meta: Any) -> dict[str, Any]:
    from mathart.core.backend_types import backend_alias_map

    aliases = sorted(
        alias for alias, target in backend_alias_map().items()
        if target == meta.name and alias != meta.name
    )
    return {
        "name": meta.name,
        "display_name": meta.display_name,
        "version": meta.version,
        "artifact_families": list(meta.artifact_families),
        "capabilities": [cap.name for cap in meta.capabilities],
        "input_requirements": list(meta.input_requirements),
        "dependencies": list(meta.dependencies),
        "author": meta.author,
        "session_origin": meta.session_origin,
        "aliases": aliases,
    }


def _registry_backends_payload() -> list[dict[str, Any]]:
    from mathart.core.backend_registry import get_registry

    registry = get_registry()
    return [
        _backend_payload(meta)
        for _, (meta, _) in sorted(registry.all_backends().items())
    ]


def _registry_epilog() -> str:
    lines = ["Discovered backends from the live registry:"]
    for entry in _registry_backends_payload():
        families = ", ".join(entry["artifact_families"]) or "—"
        lines.append(f"  - {entry['name']}: {families}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mathart",
        description="Dynamic registry facade for MarioTrickster-MathArt.",
        epilog=_registry_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--quiet", action="store_true", help="Reduce stderr diagnostics.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    registry_parser = subparsers.add_parser(
        "registry",
        help="Inspect the live backend registry.",
    )
    registry_subparsers = registry_parser.add_subparsers(dest="registry_command", required=True)

    registry_subparsers.add_parser("list", help="Emit the full backend registry as JSON.")

    show_parser = registry_subparsers.add_parser(
        "show",
        help="Emit JSON metadata for one resolved backend.",
    )
    show_parser.add_argument("--backend", required=True, help="Backend name or alias.")

    run_parser = subparsers.add_parser(
        "run",
        help="Run one backend through the shared registry/pipeline bus.",
        epilog=_registry_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_parser.add_argument("--backend", required=True, help="Backend name or alias.")
    run_parser.add_argument("--output-dir", required=True, help="Directory for generated artifacts.")
    run_parser.add_argument("--config", default=None, help="Path to JSON config object.")
    run_parser.add_argument(
        "--set",
        dest="set_values",
        action="append",
        default=[],
        help="Dynamic parameter override in key=value form; dotted keys supported.",
    )
    run_parser.add_argument("--name", default=None, help="Optional artifact stem/name.")
    run_parser.add_argument("--session-id", default="CLI-067", help="Session id injected into context.")

    af_parser = subparsers.add_parser(
        "anti-flicker-render",
        aliases=["anti_flicker_render"],
        help="Run the anti_flicker_render backend in live ComfyUI mode with stderr progress and stdout JSON manifest.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    af_parser.add_argument("--output-dir", required=True, help="Directory for generated artifacts.")
    af_parser.add_argument("--config", default=None, help="Path to JSON config object.")
    af_parser.add_argument(
        "--set",
        dest="set_values",
        action="append",
        default=[],
        help="Dynamic parameter override in key=value form; dotted keys supported.",
    )
    af_parser.add_argument("--name", default=None, help="Optional artifact stem/name.")
    af_parser.add_argument("--session-id", default="CLI-108", help="Session id injected into context.")

    mass_parser = subparsers.add_parser(
        "mass-produce",
        aliases=["mass_produce"],
        help="Run the PDG-v2 industrial mass production asset factory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mass_parser.add_argument("--output-dir", required=True, help="Root directory under which the batch folder is created.")
    mass_parser.add_argument("--batch-size", type=int, default=20, help="Number of character work items to fan out.")
    mass_parser.add_argument("--pdg-workers", type=int, default=16, help="PDG mapped fan-out worker count.")
    mass_parser.add_argument("--gpu-slots", type=int, default=1, help="GPU concurrency budget for requires_gpu PDG work items.")
    mass_parser.add_argument("--seed", type=int, default=20260421, help="Deterministic root seed for SeedSequence splitting.")
    mass_parser.add_argument("--skip-ai-render", action="store_true", help="Skip the anti_flicker_render GPU lane for CPU-only dry runs.")
    mass_parser.add_argument("--comfyui-url", default="http://localhost:8188", help="ComfyUI server URL used when AI rendering is enabled.")

    v6_parser = subparsers.add_parser(
        "v6",
        aliases=["omniscient"],
        help="Run the V6 omniscient living-ecosystem pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    v6_parser.add_argument("--output-dir", default="outputs/v6_omniscient")
    v6_parser.add_argument("--asset-name", default="v6_mario_trickster")
    v6_parser.add_argument("--vibe", default="heroic cel-shaded trickster, clean sprite skin")
    v6_parser.add_argument("--knowledge-json", default=None)
    v6_parser.add_argument("--knowledge-url", default=None)
    v6_parser.add_argument("--dry-runs", type=int, default=128)
    v6_parser.add_argument("--fps", type=int, default=12)
    v6_parser.add_argument("--frame-count", type=int, default=24)
    v6_parser.add_argument("--run-blender", action="store_true")

    return parser


def _emit_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _command_registry_list() -> int:
    from mathart.core.backend_types import backend_alias_map

    _emit_json(
        {
            "status": "ok",
            "backend_count": len(_registry_backends_payload()),
            "backends": _registry_backends_payload(),
            "alias_map": backend_alias_map(),
        }
    )
    return 0


def _command_registry_show(backend_name: str) -> int:
    from mathart.core.backend_registry import get_registry

    registry = get_registry()
    meta, _ = registry.get_or_raise(backend_name)
    payload = {
        "status": "ok",
        "requested_backend": backend_name,
        "resolved_backend": meta.name,
        "backend": _backend_payload(meta),
    }
    _emit_json(payload)
    return 0


def _build_stderr_progress_callback(backend_name: str) -> Callable[[dict[str, Any]], None]:
    def _callback(event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type", "progress"))
        if event_type == "progress":
            node = event.get("node", "?")
            value = event.get("value", 0)
            maximum = event.get("max", 0)
            message = f"[{backend_name}] progress chunk={event.get('chunk_index', '?')}/{event.get('chunk_count', '?')} node={node} {value}/{maximum}"
        elif event_type in {"chunk_start", "chunk_complete"}:
            message = (
                f"[{backend_name}] {event_type} chunk={event.get('chunk_index', '?')}/{event.get('chunk_count', '?')} "
                f"frames={event.get('start_frame', '?')}..{event.get('end_frame', '?')}"
            )
        elif event_type == "prepare":
            message = f"[{backend_name}] prepare server={event.get('server_address', '?')}"
        elif event_type in {"offline", "timeout", "execution_error", "client_error"}:
            message = f"[{backend_name}] {event_type}: {event.get('message', '')}"
        else:
            message = f"[{backend_name}] {event_type}: {json.dumps(event, ensure_ascii=False)}"
        sys.stderr.write(message + "\n")
        sys.stderr.flush()

    return _callback


def _resolved_backend_name(backend_name: str) -> str:
    from mathart.core.backend_registry import get_registry

    registry = get_registry()
    meta, _ = registry.get_or_raise(backend_name)
    return meta.name


def _inject_runtime_callbacks(backend_name: str, context: dict[str, Any]) -> dict[str, Any]:
    resolved_name = _resolved_backend_name(backend_name)
    if resolved_name == "anti_flicker_render" and bool(context.get("comfyui", {}).get("live_execution", False)):
        context["_progress_callback"] = _build_stderr_progress_callback(resolved_name)
    return context


def _run_backend_and_emit(backend_name: str, context: dict[str, Any], output_dir: Path) -> int:
    from mathart.pipeline import AssetPipeline

    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_context = _inject_runtime_callbacks(backend_name, dict(context))
    pipeline = AssetPipeline(output_dir=str(output_dir), verbose=False)
    manifest = pipeline.run_backend(backend_name, runtime_context)
    manifest.session_id = runtime_context.get("session_id", manifest.session_id)
    manifest_path = _manifest_path_for(manifest, str(output_dir), backend_name)
    manifest.save(manifest_path)
    _emit_json(
        manifest.to_ipc_payload(
            manifest_path=manifest_path,
            requested_backend=backend_name,
        )
    )
    return 0


def _command_run(args: argparse.Namespace) -> int:
    context = _merge_context(args)
    output_dir = Path(args.output_dir).resolve()
    return _run_backend_and_emit(args.backend, context, output_dir)


def _command_anti_flicker_render(args: argparse.Namespace) -> int:
    context = _merge_context(args)
    comfyui = context.setdefault("comfyui", {})
    if not isinstance(comfyui, dict):
        raise ValueError("comfyui config must be an object for anti_flicker_render")
    comfyui.setdefault("live_execution", True)
    comfyui.setdefault("fail_fast_on_offline", True)
    output_dir = Path(args.output_dir).resolve()
    return _run_backend_and_emit("anti_flicker_render", context, output_dir)


def _command_mass_produce(args: argparse.Namespace) -> int:
    from mathart.factory.mass_production import run_mass_production_factory

    payload = run_mass_production_factory(
        output_root=Path(args.output_dir).resolve(),
        batch_size=args.batch_size,
        pdg_workers=args.pdg_workers,
        gpu_slots=args.gpu_slots,
        seed=args.seed,
        skip_ai_render=args.skip_ai_render,
        comfyui_url=args.comfyui_url,
    )
    _emit_json(payload)
    return 0


def _command_v6(args: argparse.Namespace) -> int:
    from mathart.workspace.run_v6_omniscient_pipeline import run_pipeline

    result = run_pipeline(args)
    _emit_json({"status": "ok", "pipeline": "v6_omniscient", "result": result.to_dict()})
    return 0


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    should_route_to_wizard = (
        not raw_argv
        or raw_argv[0] in {"wizard", "guided"}
        or "--mode" in raw_argv
    )
    if should_route_to_wizard:
        from mathart.cli_wizard import run_wizard

        wizard_args = raw_argv[1:] if raw_argv[:1] in (["wizard"], ["guided"]) else raw_argv
        return run_wizard(wizard_args)

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    _configure_logging(quiet=args.quiet)

    try:
        if args.command == "registry":
            if args.registry_command == "list":
                return _command_registry_list()
            if args.registry_command == "show":
                return _command_registry_show(args.backend)
        if args.command == "run":
            return _command_run(args)
        if args.command in {"anti-flicker-render", "anti_flicker_render"}:
            return _command_anti_flicker_render(args)
        if args.command in {"mass-produce", "mass_produce"}:
            return _command_mass_produce(args)
        if args.command in {"v6", "omniscient"}:
            return _command_v6(args)
        raise ValueError(f"Unsupported command: {args.command!r}")
    except Exception as exc:
        LOGGER.exception("CLI execution failed")
        error_payload = {
            "status": "error",
            "error_type": exc.__class__.__name__,
            "message": str(exc),
        }
        _emit_json(error_payload)
        return 1


__all__ = ["build_parser", "main"]
