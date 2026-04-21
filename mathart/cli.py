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

from mathart.pipeline import AssetPipeline
from mathart.core.artifact_schema import ArtifactManifest
from mathart.core.backend_registry import BackendMeta, get_registry
from mathart.core.backend_types import backend_alias_map, backend_type_value

LOGGER = logging.getLogger("mathart.cli")


def _configure_logging(*, quiet: bool = False) -> None:
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


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


def _manifest_path_for(manifest: ArtifactManifest, output_dir: str, backend_name: str) -> Path:
    file_name = f"{backend_type_value(backend_name)}_artifact_manifest.json"
    return Path(output_dir).resolve() / file_name


def _backend_payload(meta: BackendMeta) -> dict[str, Any]:
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

    return parser


def _emit_json(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _command_registry_list() -> int:
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
    registry = get_registry()
    meta, _ = registry.get_or_raise(backend_name)
    return meta.name


def _inject_runtime_callbacks(backend_name: str, context: dict[str, Any]) -> dict[str, Any]:
    resolved_name = _resolved_backend_name(backend_name)
    if resolved_name == "anti_flicker_render" and bool(context.get("comfyui", {}).get("live_execution", False)):
        context["_progress_callback"] = _build_stderr_progress_callback(resolved_name)
    return context


def _run_backend_and_emit(backend_name: str, context: dict[str, Any], output_dir: Path) -> int:
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
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
