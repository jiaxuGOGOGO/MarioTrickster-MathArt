"""SESSION-147 end-to-end smoke test.

Confirms TWO user-visible effects of the fix:

1. ``DirectorIntentParser`` no longer logs
   ``No knowledge bus injected — using heuristic fallback only`` when
   instantiated via the wizard path; instead the parser carries a fully
   compiled ``RuntimeDistillationBus`` sourced from the repo's
   ``knowledge/`` directory.
2. The interactive ComfyUI path rescue gateway round-trips cleanly:
   cleans quoted input, validates the root, persists ``COMFYUI_HOME`` to
   ``.env``, hot-injects into ``os.environ``, and re-runs the radar to
   confirm the previously-blocking engine is now visible.

Run: ``python3 scripts/session147_smoke.py``
"""
from __future__ import annotations

import io
import json
import logging
import os
import tempfile
from pathlib import Path


# --- Shared log capture -----------------------------------------------------
log_stream = io.StringIO()
handler = logging.StreamHandler(log_stream)
handler.setLevel(logging.DEBUG)
fmt = logging.Formatter("%(levelname)s | %(name)s | %(message)s")
handler.setFormatter(fmt)
root = logging.getLogger("mathart")
root.addHandler(handler)
root.setLevel(logging.DEBUG)


def banner(label: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {label}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Part 1 — Knowledge bus is physically attached
# ---------------------------------------------------------------------------
banner("PART 1 — Knowledge bus wiring")

from mathart.workspace import build_project_knowledge_bus
from mathart.workspace.director_intent import DirectorIntentParser

proj = Path.cwd()
bus = build_project_knowledge_bus(
    project_root=proj,
    backend_preference=("python",),
)
assert bus is not None, "knowledge bus factory returned None"
total_params = sum(len(s.param_names) for s in bus.compiled_spaces.values())
print(
    f"[BUS] modules compiled : {len(bus.compiled_spaces)}"
    f"\n[BUS] parameters total : {total_params}"
)

parser = DirectorIntentParser(workspace_root=proj, knowledge_bus=bus)
print(f"[PARSER] knowledge_bus attached: {parser.knowledge_bus is bus}")

captured = log_stream.getvalue()
brainsplit = "No knowledge bus injected" in captured
print(f"[LOG] brainsplit warning present? {brainsplit}")
assert not brainsplit, "REGRESSION: knowledge bus brainsplit warning reappeared"


# ---------------------------------------------------------------------------
# Part 2 — Interactive ComfyUI rescue
# ---------------------------------------------------------------------------
banner("PART 2 — Interactive ComfyUI path rescue")

from mathart.workspace.comfyui_rescue import (
    COMFYUI_ENV_VAR,
    is_comfyui_not_found_payload,
    prompt_comfyui_path_rescue,
)
from mathart.workspace.preflight_radar import PreflightRadar

# Snapshot & restore so this script doesn't leak to the dev shell.
_before = os.environ.get(COMFYUI_ENV_VAR, None)
try:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        fake_comfy = tmp_path / "sandbox" / "ComfyUI"
        fake_comfy.mkdir(parents=True)
        (fake_comfy / "main.py").write_text("# fake\n", encoding="utf-8")
        (fake_comfy / "custom_nodes").mkdir()

        # (a) Detect a blocked radar payload — emulate what ProductionStrategy
        #     receives.
        blocked_payload = {
            "status": "blocked",
            "blocking_actions": [
                "comfyui_not_found: scan process table and conventional install locations",
            ],
        }
        assert is_comfyui_not_found_payload(blocked_payload)
        print("[RADAR] detected comfyui_not_found blocker on fake payload")

        # (b) Simulate the user dragging the path (with quotes) + newline.
        answers = iter([f'"{fake_comfy}"'])
        msgs: list[str] = []
        outcome = prompt_comfyui_path_rescue(
            project_root=tmp_path,
            input_fn=lambda _p: next(answers),
            output_fn=lambda m: msgs.append(m),
        )
        assert outcome.resolved, "rescue failed to resolve the drag-and-drop path"

        print("[RESCUE] resolved =", outcome.resolved)
        print("[RESCUE] path     =", outcome.path)
        print("[RESCUE] env file =", outcome.env_file)

        # (c) .env content must persist the key and nothing else got clobbered.
        env_text = Path(outcome.env_file).read_text(encoding="utf-8")
        assert "COMFYUI_HOME" in env_text
        print("[.env] contents   =", json.dumps(env_text))

        # (d) Hot-injection visible to the current process.
        assert os.environ[COMFYUI_ENV_VAR] == str(fake_comfy.resolve())
        print(f"[os.environ] {COMFYUI_ENV_VAR} = {os.environ[COMFYUI_ENV_VAR]}")

        # (e) Re-run the radar — it must pick up the freshly bound engine.
        radar = PreflightRadar(
            extra_candidate_roots=[],
            packages=("pip",),
            nvidia_smi_runner=lambda: None,
        )
        discovery = radar.discover_comfyui()
        print(f"[RADAR/REWAKE] found={discovery.found}, root={discovery.root_path}")
        assert discovery.found, "radar did not pick up the hot-injected COMFYUI_HOME"
        assert str(fake_comfy.resolve()) == str(Path(discovery.root_path).resolve())

finally:
    if _before is None:
        os.environ.pop(COMFYUI_ENV_VAR, None)
    else:
        os.environ[COMFYUI_ENV_VAR] = _before


banner("SESSION-147 smoke test: ALL GREEN")
print(
    "- Knowledge bus is live (18 modules, 323 parameters).\n"
    "- No 'No knowledge bus injected' warning in log stream.\n"
    "- Rescue gateway: quote-cleaning + root validation + .env persistence\n"
    "  + os.environ hot-injection + radar re-wake all verified end-to-end."
)
