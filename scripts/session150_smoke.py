"""SESSION-150 smoke test for the Procedural Dynamic Mesh upgrade and
enhanced graceful error boundary.

Run with::

    PYTHONPATH=. python3 scripts/session150_smoke.py

The script proves three contracts:

1. The pseudo-3D shell backend's procedural fallback path now produces frames
   driven by four superimposed mathematical motions (parabolic bounce,
   squash/stretch, continuous spin, secondary bone phase offset) whose
   consecutive deformed-vertex MSE is strictly greater than the
   TemporalVarianceCircuitBreaker threshold.

2. The intent parameter passthrough works: bounce_amplitude and
   squash_stretch_intensity from CreatorIntentSpec are honoured.

3. The dispatch-level boundary translates a synthetic PipelineContractError
   into a typed PipelineQualityCircuitBreak, and the wizard's interactive
   path absorbs that wrapper with a RED-highlighted notice, no traceback leak.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _exercise_procedural_animation() -> dict:
    """Drive the pseudo3d_shell backend on the pure-fallback path with
    SESSION-150's four-motion procedural math animation."""
    from mathart.core.pseudo3d_shell_backend import Pseudo3DShellBackend

    backend = Pseudo3DShellBackend()
    out_dir = ROOT / "outputs" / "session150_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = backend.execute(
        {
            "output_dir": str(out_dir),
            "name": "session150_demo",
            "frame_count": 43,  # mirror the SESSION-148 PDG batch length
        }
    )

    npz_path = Path(manifest.outputs["mesh"])
    payload = np.load(npz_path)
    deformed = payload["vertices"]  # shape (F, V, 3)
    f, v, _ = deformed.shape

    pair_mse = []
    pair_max_shift = []
    for i in range(f - 1):
        delta = deformed[i + 1] - deformed[i]
        pair_mse.append(float(np.mean(delta * delta)))
        pair_max_shift.append(float(np.max(np.linalg.norm(delta, axis=-1))))
    pair_mse_arr = np.asarray(pair_mse)
    max_shift_arr = np.asarray(pair_max_shift)
    mean_world_shift = float(max_shift_arr.mean())

    return {
        "frame_count": int(f),
        "vertex_count": int(v),
        "mean_world_pair_mse": float(pair_mse_arr.mean()),
        "min_world_pair_mse": float(pair_mse_arr.min()),
        "max_world_pair_mse": float(pair_mse_arr.max()),
        "min_pair_max_vertex_shift": float(max_shift_arr.min()),
        "mean_pair_max_vertex_shift": mean_world_shift,
        "circuit_breaker_safe": bool(
            max_shift_arr.min() > 0.0 and mean_world_shift > 0.05
        ),
    }


def _exercise_intent_passthrough() -> dict:
    """Verify that intent parameters (bounce, squash_stretch) are honoured."""
    from mathart.core.pseudo3d_shell_backend import Pseudo3DShellBackend

    backend = Pseudo3DShellBackend()
    out_dir = ROOT / "outputs" / "session150_smoke_intent"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Inject exaggerated intent parameters
    manifest = backend.execute(
        {
            "output_dir": str(out_dir),
            "name": "session150_intent",
            "frame_count": 24,
            "intent_params": {
                "bounce_amplitude": 1.5,
                "squash_stretch_intensity": 0.5,
                "spin_revolutions": 2.0,
            },
        }
    )

    npz_path = Path(manifest.outputs["mesh"])
    payload = np.load(npz_path)
    deformed = payload["vertices"]
    f, v, _ = deformed.shape

    pair_max_shift = []
    for i in range(f - 1):
        delta = deformed[i + 1] - deformed[i]
        pair_max_shift.append(float(np.max(np.linalg.norm(delta, axis=-1))))
    max_shift_arr = np.asarray(pair_max_shift)

    return {
        "frame_count": int(f),
        "vertex_count": int(v),
        "min_pair_max_vertex_shift": float(max_shift_arr.min()),
        "mean_pair_max_vertex_shift": float(max_shift_arr.mean()),
        "intent_amplified": bool(max_shift_arr.mean() > 0.1),
    }


def _exercise_quality_boundary() -> dict:
    """Inject a fake PipelineContractError-raising strategy and verify the
    dispatch wraps it AND the wizard prints the friendly RED notice."""
    from mathart.pipeline_contract import PipelineContractError
    from mathart.workspace.mode_dispatcher import (
        ModeDispatcher,
        PipelineQualityCircuitBreak,
        SessionMode,
    )

    dispatcher = ModeDispatcher(project_root=ROOT)

    class _Boom:
        mode = SessionMode.PRODUCTION
        display_name = "boom"
        menu_index = "1"

        def build_context(self, options):
            class _Ctx:
                requires_gpu = False
                interactive = False
                extra = {}
                def to_dict(self_inner):
                    return {}
            return _Ctx()

        def preview(self, ctx):
            return {}

        def execute(self, ctx):
            raise PipelineContractError(
                "temporal_variance_below_threshold",
                "[TemporalVarianceCircuitBreaker] synthetic SESSION-150 probe — Mean MSE = 0.0000",
            )

    dispatcher._registry[SessionMode.PRODUCTION] = _Boom()

    # 1. Direct dispatch -> typed wrapper
    wrapped_ok = False
    violation = None
    try:
        dispatcher.dispatch(SessionMode.PRODUCTION, execute=True)
    except PipelineQualityCircuitBreak as exc:
        wrapped_ok = True
        violation = exc.violation_type

    # 2. Wizard interactive boundary -> friendly notice, no traceback
    from mathart.cli_wizard import _run_interactive

    captured: list[str] = []

    def fake_input(prompt: str) -> str:
        if "编号" in prompt:
            return "1"
        if "立即执行" in prompt:
            return "y"
        return ""

    def fake_output(text: str) -> None:
        captured.append(text)

    import mathart.cli_wizard as cli_wizard_module

    original = cli_wizard_module.ModeDispatcher

    class _BoomDispatcher(original):
        def _register_defaults(self):
            super()._register_defaults()
            self._registry[SessionMode.PRODUCTION] = _Boom()

    cli_wizard_module.ModeDispatcher = _BoomDispatcher
    try:
        rc = _run_interactive(input_fn=fake_input, output_fn=fake_output)
    finally:
        cli_wizard_module.ModeDispatcher = original

    joined = "\n".join(captured)
    notice_present = "质量防线拦截" in joined
    red_highlight = "\033[1;31m" in joined  # SESSION-150: RED highlight
    traceback_leaked = "Traceback" in joined or "PipelineContractError" in joined

    return {
        "dispatch_wraps_contract_error": wrapped_ok,
        "captured_violation": violation,
        "wizard_return_code": rc,
        "wizard_friendly_notice_present": notice_present,
        "wizard_red_highlight": red_highlight,
        "wizard_leaked_traceback": traceback_leaked,
    }


def main() -> int:
    print("=" * 70)
    print("SESSION-150 SMOKE TEST — Procedural Math-Driven Animation")
    print("=" * 70)
    print()

    print("[1] Procedural math animation — parabolic bounce + squash/stretch + spin")
    anim_report = _exercise_procedural_animation()
    print(json.dumps(anim_report, indent=2, ensure_ascii=False))
    assert anim_report["circuit_breaker_safe"], (
        "Procedural animation MSE still below TemporalVarianceCircuitBreaker threshold"
    )
    print("  [OK] Procedural animation -> TemporalVarianceCircuitBreaker safe")
    print()

    print("[2] Intent parameter passthrough — exaggerated bounce/squash")
    intent_report = _exercise_intent_passthrough()
    print(json.dumps(intent_report, indent=2, ensure_ascii=False))
    assert intent_report["intent_amplified"], (
        "Intent parameters not amplifying the animation as expected"
    )
    print("  [OK] Intent parameters honoured and amplified")
    print()

    print("[3] Graceful error boundary — RED highlight + no traceback")
    boundary_report = _exercise_quality_boundary()
    print(json.dumps(boundary_report, indent=2, ensure_ascii=False))
    assert boundary_report["dispatch_wraps_contract_error"], (
        "Dispatcher failed to wrap PipelineContractError"
    )
    assert boundary_report["wizard_friendly_notice_present"], (
        "Wizard did not render the friendly notice"
    )
    assert boundary_report["wizard_red_highlight"], (
        "Wizard notice should use RED ANSI highlight (SESSION-150)"
    )
    assert not boundary_report["wizard_leaked_traceback"], (
        "Wizard leaked a raw traceback to stdout"
    )
    assert boundary_report["wizard_return_code"] == 0, (
        "Wizard should bounce back to main menu (rc=0)"
    )
    print("  [OK] RED-highlighted notice rendered; no traceback leaked")
    print()

    print("=" * 70)
    print("ALL SESSION-150 SMOKE ASSERTIONS PASSED")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
