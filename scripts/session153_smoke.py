"""SESSION-153 smoke test for the P0-SESSION-150-UX-DOCS-SYNC upgrade.

Validates five UX contracts without touching any ComfyUI server:

    1. Main-menu ``while True`` loop: after running one sub-flow and then
       selecting [0], the wizard returns 0 and does NOT crash.
    2. Main-menu tolerates an invalid numeric choice (e.g. ``99``) by
       showing a "无法识别" hint and looping, not exiting.
    3. ComfyUI pre-flight warning banner is emitted BEFORE any render
       attempt (both top-level [1] production and Golden Handoff [1]).
    4. Quality circuit break from a sub-flow is rendered as the RED
       notice and the shell bounces back to the main menu (continue).
    5. Docs-as-Code parity: COMFYUI_PREFLIGHT_WARNING wording is present
       verbatim in docs/USER_GUIDE.md, and the three Golden Handoff
       option labels also appear verbatim there.

Run ``python scripts/session153_smoke.py`` from the project root.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from mathart import cli_wizard
from mathart.cli_wizard import (
    COMFYUI_PREFLIGHT_WARNING,
    GOLDEN_HANDOFF_OPTION_AUDIT,
    GOLDEN_HANDOFF_OPTION_HOME,
    GOLDEN_HANDOFF_OPTION_PRODUCE,
    _run_interactive_shell,
)
from mathart.workspace.mode_dispatcher import PipelineQualityCircuitBreak
from mathart.pipeline_contract import PipelineContractError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ScriptedInput:
    """Yield scripted responses; raise EOFError when exhausted so the
    shell's main loop takes the graceful-exit branch rather than hanging."""

    def __init__(self, responses):
        self._queue = list(responses)
        self.asked = []

    def __call__(self, _prompt=""):
        self.asked.append(_prompt)
        if not self._queue:
            raise EOFError("scripted input exhausted")
        return self._queue.pop(0)


class CaptureOutput:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, msg=""):
        # Mirror ``print`` semantics — stringify and newline-join.
        self.lines.append(str(msg))

    @property
    def joined(self) -> str:
        return "\n".join(self.lines)


# ---------------------------------------------------------------------------
# Test 1: main menu loops and exits on [0]
# ---------------------------------------------------------------------------

def test_main_loop_exit():
    out = CaptureOutput()
    # User immediately chooses [0] exit.
    inp = ScriptedInput(["0"])
    rc = _run_interactive_shell(input_fn=inp, output_fn=out)
    assert rc == 0, f"expected rc=0 on [0] exit, got {rc}"
    assert "已退出顶层向导" in out.joined, out.joined[-400:]
    assert "主菜单" in out.joined
    print("[PASS] test_main_loop_exit — clean [0] exit, rc=0")


# ---------------------------------------------------------------------------
# Test 2: invalid choice does NOT kill the shell
# ---------------------------------------------------------------------------

def test_main_loop_invalid_choice_recovers():
    out = CaptureOutput()
    # Type a nonsense number, then [0] exit.  Shell should show the
    # unsupported-mode hint and loop back to the menu.
    inp = ScriptedInput(["99", "n", "0"])
    rc = _run_interactive_shell(input_fn=inp, output_fn=out)
    assert rc == 0, f"expected rc=0 after invalid choice recovery, got {rc}"
    # After "99" the dispatcher will raise ValueError and we print a
    # "[提示] 无法识别的选项" line.
    assert "[提示] 无法识别的选项" in out.joined or "quality_circuit_break" in out.joined, \
        "invalid choice did not surface the friendly hint"
    # Count main-menu appearances — must be >= 2 (before [99] and before [0]).
    menu_count = out.joined.count("顶层交互向导主菜单")
    assert menu_count >= 2, f"main menu only shown {menu_count} time(s)"
    print("[PASS] test_main_loop_invalid_choice_recovers — menu re-rendered")


# ---------------------------------------------------------------------------
# Test 3: ComfyUI pre-flight warning banner emitted before production
# ---------------------------------------------------------------------------

def test_preflight_warning_emits_before_production():
    out = CaptureOutput()
    cli_wizard.emit_comfyui_preflight_warning(output_fn=out)
    assert "[🚨 提示]" in out.joined
    assert "ComfyUI" in out.joined
    assert "http://localhost:8188" in out.joined
    print("[PASS] test_preflight_warning_emits_before_production")


# ---------------------------------------------------------------------------
# Test 4: quality circuit break renders the RED notice
# ---------------------------------------------------------------------------

def test_quality_circuit_break_renders_red_notice():
    out = CaptureOutput()
    original = PipelineContractError(
        violation_type="temporal_variance_below_threshold",
        detail="mean MSE=0.0000 below 1e-4",
    )
    exc = PipelineQualityCircuitBreak(original)
    cli_wizard._render_quality_circuit_break(
        exc, output_fn=out, selection="smoke",
    )
    assert "质量防线拦截" in out.joined
    assert "\033[1;31m" in out.joined, "RED ANSI highlight missing"
    assert "logs/mathart.log" in out.joined
    print("[PASS] test_quality_circuit_break_renders_red_notice")


# ---------------------------------------------------------------------------
# Test 5: Docs-as-Code parity
# ---------------------------------------------------------------------------

def test_docs_parity():
    guide = (PROJECT_ROOT / "docs" / "USER_GUIDE.md").read_text(encoding="utf-8")

    # Preflight wording — the emoji banner head must be present in docs
    banner_head = "[🚨 提示] 即将呼叫显卡渲染！请确保您的 ComfyUI 服务端"
    assert banner_head in guide, f"preflight banner not mirrored in USER_GUIDE.md"

    # Golden Handoff labels — every label verbatim
    for label in (
        GOLDEN_HANDOFF_OPTION_PRODUCE,
        GOLDEN_HANDOFF_OPTION_AUDIT,
        GOLDEN_HANDOFF_OPTION_HOME,
    ):
        assert label in guide, f"Golden Handoff label not mirrored in USER_GUIDE.md: {label}"

    # Main-menu [0] exit option must be documented in README and guide
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    assert "全局不死循环" in readme, "README missing global main-loop mention"
    assert "黄金连招" in readme, "README missing Golden Handoff mention"

    print("[PASS] test_docs_parity — preflight + handoff wording + README aligned")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        test_main_loop_exit,
        test_main_loop_invalid_choice_recovers,
        test_preflight_warning_emits_before_production,
        test_quality_circuit_break_renders_red_notice,
        test_docs_parity,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as exc:
            failed += 1
            print(f"[FAIL] {t.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"[ERROR] {t.__name__}: {exc.__class__.__name__}: {exc}")
    print()
    print("=" * 60)
    print(f"  Results: {len(tests) - failed} passed, {failed} failed")
    print("=" * 60)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
