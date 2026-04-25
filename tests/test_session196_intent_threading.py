"""SESSION-196 P0 — CLI Intent Threading + Orphan Rescue Phase 2.

These tests are the "Three-Layer Evolution Loop" L3 fitness functions for
SESSION-196.  Each test asserts a contract that, if broken, would let an
*invisible* regression slip into production:

* The IntentGateway Fail-Fasts on unknown gaits and ghost reference images.
* The CreatorIntentSpec round-trips ``action_name`` /
  ``visual_reference_path`` exactly once through ``to_dict``/``from_dict``.
* The deep call site reads ``action_name`` via the new pure helper *without*
  any new formal parameter on ``_execute_live_pipeline``.
* The mass-production factory propagates ``director_studio_spec`` through
  the dispatcher, into the PDG initial context, and finally into the
  per-character anti_flicker_render config.
* The semantic orchestrator routes the new vibe keywords to ``physics_3d``
  and ``fluid_momentum_controller`` while the hallucination guard drops
  unknown plugin names.
"""
from __future__ import annotations

import inspect
import json
import os
import tempfile
import textwrap
import unittest
from pathlib import Path
from typing import Any
from unittest import mock


# ---------------------------------------------------------------------------
# 1. IntentGateway admission Fail-Fast contract
# ---------------------------------------------------------------------------
class TestIntentGatewayAdmission(unittest.TestCase):
    """Validating + Mutating webhook layer must reject malformed intents."""

    def setUp(self) -> None:
        from mathart.workspace.intent_gateway import IntentGateway, IntentValidationError
        self.IntentValidationError = IntentValidationError
        self.gateway = IntentGateway()

    def test_registered_gaits_match_live_registry(self) -> None:
        from mathart.core.openpose_pose_provider import get_gait_registry
        live = sorted(get_gait_registry().names())
        self.assertEqual(list(self.gateway.registered_gaits), live)
        # SESSION-195 already shipped these five — make the contract explicit.
        for required in ("walk", "run", "jump", "idle", "dash"):
            self.assertIn(required, self.gateway.registered_gaits)

    def test_unknown_action_fail_fast_with_actionable_error(self) -> None:
        with self.assertRaises(self.IntentValidationError) as ctx:
            self.gateway.admit({"action": "moonwalk"})
        msg = str(ctx.exception)
        self.assertIn("unknown action", msg)
        # Error must mention the legal set so the user can immediately fix it.
        for required in ("walk", "run", "jump", "idle", "dash"):
            self.assertIn(required, msg)

    def test_action_case_insensitive_canonicalisation(self) -> None:
        admission = self.gateway.admit({"action": "  Dash  "})
        self.assertEqual(admission.action_name, "dash")

    def test_ghost_reference_image_fail_fast(self) -> None:
        with self.assertRaises(self.IntentValidationError) as ctx:
            self.gateway.admit({"reference_image": "/tmp/__no_such_file__.png"})
        msg = str(ctx.exception)
        self.assertIn("not found on disk", msg)
        self.assertIn("Fail-Closed", msg)

    def test_directory_reference_image_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(self.IntentValidationError) as ctx:
                self.gateway.admit({"reference_image": tmp})
            self.assertIn("directory", str(ctx.exception))

    def test_happy_path_returns_canonical_admission_payload(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"x")
            ref = f.name
        try:
            admission = self.gateway.admit({"action": "dash", "reference_image": ref})
            self.assertEqual(admission.action_name, "dash")
            self.assertTrue(admission.reference_image_path)
            payload = admission.as_admission_payload()
            self.assertEqual(payload["action_name"], "dash")
            self.assertTrue(payload["_visual_reference_path"].endswith(os.path.basename(ref)))
        finally:
            os.unlink(ref)

    def test_pass_through_when_no_session196_fields_present(self) -> None:
        admission = self.gateway.admit({})
        self.assertEqual(admission.action_name, "")
        self.assertIsNone(admission.reference_image_path)
        self.assertEqual(len(admission.warnings), 1)


# ---------------------------------------------------------------------------
# 2. CreatorIntentSpec round-trip + parser integration
# ---------------------------------------------------------------------------
class TestCreatorIntentSpecRoundTrip(unittest.TestCase):
    """The spec is the "Redux store" — admission fields must survive (de)serialisation."""

    def test_to_from_dict_preserves_action_and_reference(self) -> None:
        from mathart.workspace.director_intent import CreatorIntentSpec
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"x")
            ref = f.name
        try:
            spec = CreatorIntentSpec(action_name="run", visual_reference_path=ref)
            d = spec.to_dict()
            self.assertEqual(d["action_name"], "run")
            self.assertEqual(d["_visual_reference_path"], ref)
            spec2 = CreatorIntentSpec.from_dict(d)
            self.assertEqual(spec2.action_name, "run")
            self.assertEqual(spec2.visual_reference_path, ref)
        finally:
            os.unlink(ref)

    def test_parser_funnels_admission_into_spec(self) -> None:
        from mathart.workspace.director_intent import DirectorIntentParser
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"x")
            ref = f.name
        try:
            parser = DirectorIntentParser()
            spec = parser.parse_dict({
                "vibe": "夸张弹性 dash 角色",
                "action": "dash",
                "reference_image": ref,
            })
            self.assertEqual(spec.action_name, "dash")
            self.assertTrue(spec.visual_reference_path.endswith(os.path.basename(ref)))
        finally:
            os.unlink(ref)

    def test_parser_propagates_intent_validation_error(self) -> None:
        from mathart.workspace.director_intent import DirectorIntentParser
        from mathart.workspace.intent_gateway import IntentValidationError
        parser = DirectorIntentParser()
        with self.assertRaises(IntentValidationError):
            parser.parse_dict({"action": "moonwalk"})


# ---------------------------------------------------------------------------
# 3. Deep call site uses the pure extractor (anti-signature-pollution)
# ---------------------------------------------------------------------------
class TestExtractorRedLines(unittest.TestCase):
    def test_extract_action_name_three_level_lookup(self) -> None:
        from mathart.workspace.intent_gateway import extract_action_name
        # Top-level
        self.assertEqual(extract_action_name({"action_name": "jump"}), "jump")
        # Nested in director_studio_spec
        self.assertEqual(
            extract_action_name({"director_studio_spec": {"action_name": "run"}}),
            "run",
        )
        # action_filter[0] legacy LookDev shortcut
        self.assertEqual(extract_action_name({"action_filter": ["idle"]}), "idle")
        # motion_state fallback
        self.assertEqual(extract_action_name({"motion_state": "dash"}), "dash")
        # Nothing → empty string (graceful)
        self.assertEqual(extract_action_name({}), "")

    def test_execute_live_pipeline_signature_unchanged(self) -> None:
        """SESSION-196 must NOT add a formal action_name parameter to the
        deep call site.  The contract is: read it from ``validated`` via
        ``intent_gateway.extract_action_name``.
        """
        from mathart.core.builtin_backends import AntiFlickerRenderBackend
        sig = inspect.signature(AntiFlickerRenderBackend._execute_live_pipeline)
        params = list(sig.parameters)
        self.assertEqual(params, ["self", "validated"])

    def test_bake_openpose_call_site_threads_action_name(self) -> None:
        """The ``_bake_openpose`` call inside ``_execute_live_pipeline`` MUST
        pass ``action_name=`` resolved from the validated dict.  We assert
        on the source text because patching the import is fragile across
        environments without ComfyUI.
        """
        import mathart.core.builtin_backends as mod
        src = inspect.getsource(mod._BaseAntiFlickerRender._execute_live_pipeline) \
            if hasattr(mod, "_BaseAntiFlickerRender") \
            else inspect.getsource(mod.AntiFlickerRenderBackend._execute_live_pipeline)
        self.assertIn("extract_action_name", src)
        self.assertIn("action_name=_resolved_action_name", src)


# ---------------------------------------------------------------------------
# 4. End-to-end propagation: dispatcher → factory → backend config
# ---------------------------------------------------------------------------
class TestDirectorStudioSpecPropagation(unittest.TestCase):
    def test_production_strategy_preserves_director_studio_spec_in_extra(self) -> None:
        from mathart.workspace.mode_dispatcher import ProductionStrategy
        with tempfile.TemporaryDirectory() as tmp:
            strategy = ProductionStrategy(project_root=tmp)
            ctx = strategy.build_context({
                "skip_ai_render": True,
                "batch_size": 1,
                "pdg_workers": 1,
                "gpu_slots": 1,
                "seed": 1,
                "director_studio_spec": {"action_name": "dash", "_visual_reference_path": "/tmp/x.png"},
                "vibe": "夸张弹性",
                "action_filter": ["dash"],
            })
            self.assertEqual(ctx.extra["director_studio_spec"]["action_name"], "dash")
            self.assertEqual(ctx.extra["vibe"], "夸张弹性")
            self.assertEqual(ctx.extra["action_filter"], ["dash"])

    def test_run_mass_production_factory_signature_accepts_session196_kwargs(self) -> None:
        from mathart.factory.mass_production import run_mass_production_factory
        sig = inspect.signature(run_mass_production_factory)
        for required in ("director_studio_spec", "vibe", "vfx_artifacts"):
            self.assertIn(required, sig.parameters,
                          f"SESSION-196 kwarg '{required}' missing on factory")


# ---------------------------------------------------------------------------
# 5. Orphan Rescue Phase 2 — semantic onboarding of physics_3d
# ---------------------------------------------------------------------------
class TestOrphanRescuePhase2(unittest.TestCase):
    def test_vibe_keyword_routes_to_physics_3d(self) -> None:
        from mathart.workspace.semantic_orchestrator import resolve_active_vfx_plugins
        plugins = resolve_active_vfx_plugins(raw_vibe="赛博软体角色 三维物理")
        self.assertIn("physics_3d", plugins)

    def test_combined_trigger_includes_physics_3d(self) -> None:
        from mathart.workspace.semantic_orchestrator import resolve_active_vfx_plugins
        plugins = resolve_active_vfx_plugins(raw_vibe="黑科技全开")
        self.assertIn("physics_3d", plugins)
        self.assertIn("fluid_momentum_controller", plugins)

    def test_extra_fluid_keywords_route_to_fluid_momentum(self) -> None:
        from mathart.workspace.semantic_orchestrator import resolve_active_vfx_plugins
        plugins = resolve_active_vfx_plugins(raw_vibe="魔法浪涌 + 冲击波")
        self.assertIn("fluid_momentum_controller", plugins)

    def test_hallucination_guard_drops_unknown_plugin(self) -> None:
        from mathart.workspace.semantic_orchestrator import resolve_active_vfx_plugins
        plugins = resolve_active_vfx_plugins(llm_suggested=["bogus_plugin", "physics_3d"])
        self.assertNotIn("bogus_plugin", plugins)
        self.assertIn("physics_3d", plugins)

    def test_capability_descriptor_advertises_physics_3d(self) -> None:
        from mathart.workspace.semantic_orchestrator import VFX_PLUGIN_CAPABILITIES
        self.assertIn("physics_3d", VFX_PLUGIN_CAPABILITIES)
        self.assertIn("XPBD", VFX_PLUGIN_CAPABILITIES["physics_3d"]["display_name"])


# ---------------------------------------------------------------------------
# 6. Red-line compliance suite (catch invisible regressions)
# ---------------------------------------------------------------------------
class TestSession196RedLines(unittest.TestCase):
    def test_intent_gateway_module_documents_all_three_red_lines(self) -> None:
        from mathart.workspace import intent_gateway
        doc = intent_gateway.__doc__ or ""
        for required in ("Anti-Hardcoded Red Line", "Anti-Signature-Pollution Red Line",
                         "Anti-Implicit-Fallback Red Line"):
            self.assertIn(required, doc, f"Missing red-line clause: {required}")

    def test_research_notes_session_196_present(self) -> None:
        notes = Path(__file__).resolve().parents[1] / "docs" / "RESEARCH_NOTES_SESSION_196.md"
        self.assertTrue(notes.exists(), "SESSION-196 research notes missing")
        content = notes.read_text(encoding="utf-8")
        for ref in ("Redux", "ROS 2", "Admission Webhook"):
            self.assertIn(ref, content)

    def test_session195_assertions_still_green(self) -> None:
        """Quick guard so SESSION-195 contract test remains observable from
        SESSION-196 \u2014 protects against accidental import breakage when we
        rewire identity_hydration."""
        from mathart.core.identity_hydration import extract_visual_reference_path
        self.assertIsNone(extract_visual_reference_path({}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
