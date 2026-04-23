#!/usr/bin/env python3
"""SESSION-154 Smoke Test — Knowledge Enforcer Gate Registry.

5 assertions that verify the core deliverables without network access.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

PASS = 0
FAIL = 0


def _check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}  — {detail}")


def main() -> int:
    global PASS, FAIL

    # ── Test 1: Registry loads both enforcers ──────────────────────
    try:
        from mathart.quality.gates.enforcer_registry import (
            KnowledgeEnforcerRegistry,
            get_enforcer_registry,
        )
        KnowledgeEnforcerRegistry.reset()
        registry = get_enforcer_registry()
        names = registry.list_all()
        _check(
            "test_registry_loads_both_enforcers",
            "pixel_art_enforcer" in names and "color_harmony_enforcer" in names,
            f"got: {names}",
        )
    except Exception as exc:
        _check("test_registry_loads_both_enforcers", False, str(exc))

    # ── Test 2: PixelArtEnforcer clamps canvas_size ────────────────
    try:
        from mathart.quality.gates.pixel_art_enforcer import PixelArtEnforcer
        enforcer = PixelArtEnforcer()
        result = enforcer.validate({"canvas_size": 256})
        _check(
            "test_pixel_art_canvas_clamp",
            result.params["canvas_size"] == 64 and len(result.violations) == 1,
            f"canvas_size={result.params.get('canvas_size')}, violations={len(result.violations)}",
        )
    except Exception as exc:
        _check("test_pixel_art_canvas_clamp", False, str(exc))

    # ── Test 3: ColorHarmonyEnforcer clamps fill_light_ratio ───────
    try:
        from mathart.quality.gates.color_harmony_enforcer import ColorHarmonyEnforcer
        enforcer = ColorHarmonyEnforcer()
        result = enforcer.validate({"fill_light_ratio": 0.8})
        _check(
            "test_color_harmony_fill_light_clamp",
            result.params["fill_light_ratio"] == 0.5 and len(result.violations) >= 1,
            f"fill_light_ratio={result.params.get('fill_light_ratio')}, violations={len(result.violations)}",
        )
    except Exception as exc:
        _check("test_color_harmony_fill_light_clamp", False, str(exc))

    # ── Test 4: enforce_render_params chains both enforcers ────────
    try:
        from mathart.quality.gates.enforcer_integration import enforce_render_params
        corrected, results = enforce_render_params(
            {"canvas_size": 256, "interpolation": "bilinear", "fill_light_ratio": 0.9},
            verbose=False,
        )
        _check(
            "test_enforce_render_params_chain",
            corrected["canvas_size"] == 64
            and corrected["interpolation"] == "nearest"
            and corrected["fill_light_ratio"] == 0.5,
            f"corrected={corrected}",
        )
    except Exception as exc:
        _check("test_enforce_render_params_chain", False, str(exc))

    # ── Test 5: Docs parity — USER_GUIDE.md §6 exists ─────────────
    try:
        repo_root = Path(__file__).resolve().parent.parent
        user_guide = repo_root / "docs" / "USER_GUIDE.md"
        content = user_guide.read_text(encoding="utf-8")
        _check(
            "test_docs_parity",
            "知识执法网关" in content
            and "PixelArtEnforcer" in content
            and "ColorHarmonyEnforcer" in content
            and "Policy-as-Code" in content,
            "Missing expected content in USER_GUIDE.md §6",
        )
    except Exception as exc:
        _check("test_docs_parity", False, str(exc))

    # ── Summary ────────────────────────────────────────────────────
    print("=" * 60)
    print(f"  Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
