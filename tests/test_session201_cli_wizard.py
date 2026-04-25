"""SESSION-201 P0-DIRECTOR-CLI-AND-INTENT-OVERHAUL — test suite.

Covers four contracts:

1. ``CreatorIntentSpec`` carries the new ``vfx_overrides`` field (CRD-style
   declarative toggle); empty-dict default keeps the legacy "vibe-only"
   heuristic path untouched (向下兼容红线).
2. ``IntentGateway.validate_vfx_overrides`` Fail-Closes on unknown keys and
   non-bool values, and accepts the SESSION-201 whitelist.
3. ``DirectorIntentParser.parse_dict`` honours ``vfx_overrides`` *after* the
   heuristic VFX resolution pass — explicit user toggles always win.
4. ``cli_wizard`` exposes ``--yes`` / ``--auto-fire`` and the headless
   ``_run_director_studio`` fast-path skips every blocking ``[Y/n]`` prompt
   while still printing the Pre-flight Manifest banner (CI log fidelity).
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from mathart import cli_wizard
from mathart.cli_wizard import (
    PREFLIGHT_MANIFEST_HEADER,
    _parse_vfx_overrides_flag,
    build_parser,
    confirm_ignition,
    prompt_reference_image_with_validation,
    prompt_vfx_overrides,
    render_preflight_manifest,
    select_action_via_wizard,
)
from mathart.workspace.director_intent import (
    CreatorIntentSpec,
    DirectorIntentParser,
)
from mathart.workspace.intent_gateway import (
    IntentGateway,
    IntentValidationError,
)


# ---------------------------------------------------------------------------
# (1) CreatorIntentSpec carries vfx_overrides + legacy backwards compat.
# ---------------------------------------------------------------------------
class TestSpecVfxOverridesField:
    def test_default_is_empty_dict(self):
        spec = CreatorIntentSpec()
        assert spec.vfx_overrides == {}, "默认 vfx_overrides 必须是空字典 (向下兼容红线)"

    def test_to_dict_round_trip(self):
        spec = CreatorIntentSpec()
        spec.vfx_overrides = {"force_fluid": True, "force_physics": False}
        d = spec.to_dict()
        assert d["vfx_overrides"] == {"force_fluid": True, "force_physics": False}
        # round-trip via from_dict
        rebuilt = CreatorIntentSpec.from_dict(d)
        assert rebuilt.vfx_overrides == {"force_fluid": True, "force_physics": False}

    def test_legacy_dict_without_field(self):
        # SESSION-200 era dump: no vfx_overrides key at all.
        legacy = {"action_name": "walk", "knowledge_grounded": False}
        spec = CreatorIntentSpec.from_dict(legacy)
        assert spec.vfx_overrides == {}
        # Spec must still serialise cleanly (向下兼容红线).
        assert "vfx_overrides" in spec.to_dict()


# ---------------------------------------------------------------------------
# (2) IntentGateway validation + admission.
# ---------------------------------------------------------------------------
class TestGatewayVfxOverridesValidation:
    def test_empty_passthrough(self):
        gw = IntentGateway(registered_gaits=("walk", "dash"), filesystem_check=False)
        assert gw.validate_vfx_overrides(None) == {}
        assert gw.validate_vfx_overrides({}) == {}
        assert gw.validate_vfx_overrides("") == {}

    def test_unknown_key_fail_closed(self):
        gw = IntentGateway(registered_gaits=("walk",), filesystem_check=False)
        with pytest.raises(IntentValidationError) as exc:
            gw.validate_vfx_overrides({"force_fluds": True})
        assert "force_fluds" in str(exc.value)

    def test_non_bool_value_fail_closed(self):
        gw = IntentGateway(registered_gaits=("walk",), filesystem_check=False)
        with pytest.raises(IntentValidationError):
            gw.validate_vfx_overrides({"force_fluid": "yes"})

    def test_whitelist_accepts_all_four_flags(self):
        gw = IntentGateway(registered_gaits=("walk",), filesystem_check=False)
        result = gw.validate_vfx_overrides(
            {
                "force_fluid": True,
                "force_physics": False,
                "force_cloth": True,
                "force_particles": False,
            }
        )
        assert result == {
            "force_fluid": True,
            "force_physics": False,
            "force_cloth": True,
            "force_particles": False,
        }

    def test_admit_threads_overrides_into_payload(self):
        gw = IntentGateway(registered_gaits=("walk",), filesystem_check=False)
        admission = gw.admit({"action": "walk", "vfx_overrides": {"force_fluid": True}})
        assert admission.vfx_overrides == {"force_fluid": True}
        payload = admission.as_admission_payload()
        assert payload["vfx_overrides"] == {"force_fluid": True}


# ---------------------------------------------------------------------------
# (3) DirectorIntentParser applies overrides AFTER heuristic resolution.
# ---------------------------------------------------------------------------
class TestParserAppliesOverrides:
    def test_force_on_adds_plugin(self):
        parser = DirectorIntentParser()
        spec = parser.parse_dict({
            "vibe": "calm walk",
            "vfx_overrides": {"force_fluid": True},
        })
        assert "fluid_momentum_controller" in spec.active_vfx_plugins
        assert spec.vfx_overrides == {"force_fluid": True}

    def test_force_off_removes_plugin(self):
        parser = DirectorIntentParser()
        # First parse with heuristic that may activate fluid.
        spec = parser.parse_dict({
            "vibe": "huge splash water flow",
            "vfx_overrides": {"force_fluid": False},
        })
        # SESSION-201: force-off MUST win over heuristic activation.
        assert "fluid_momentum_controller" not in spec.active_vfx_plugins

    def test_legacy_vibe_only_path_unchanged(self):
        parser = DirectorIntentParser()
        legacy = parser.parse_dict({"vibe": "graceful walk"})
        # Empty dict means heuristic stays in charge; no crash, no surprise.
        assert legacy.vfx_overrides == {}


# ---------------------------------------------------------------------------
# (4) cli_wizard surface — argparse + helpers + manifest banner.
# ---------------------------------------------------------------------------
class TestCliWizardSurface:
    def test_argparse_yes_flag(self):
        p = build_parser()
        ns = p.parse_args(["--mode", "5", "--yes"])
        assert ns.auto_fire is True

    def test_argparse_auto_fire_alias(self):
        p = build_parser()
        ns = p.parse_args(["--mode", "5", "--auto-fire"])
        assert ns.auto_fire is True

    def test_argparse_action_and_reference_image(self):
        p = build_parser()
        ns = p.parse_args(
            ["--mode", "5", "--action", "dash", "--reference-image", "/tmp/x.png"]
        )
        assert ns.action == "dash"
        assert ns.reference_image == "/tmp/x.png"

    def test_parse_vfx_overrides_flag(self):
        assert _parse_vfx_overrides_flag(None) == {}
        assert _parse_vfx_overrides_flag("") == {}
        assert _parse_vfx_overrides_flag("force_fluid=1,force_physics=0") == {
            "force_fluid": True,
            "force_physics": False,
        }
        assert _parse_vfx_overrides_flag("force_cloth") == {"force_cloth": True}

    def test_select_action_auto_fire_silent(self):
        # In auto_fire mode, no prompt may fire (CI guarantee).
        result = select_action_via_wizard(
            input_fn=lambda *_: pytest.fail("input must not be called"),
            output_fn=lambda *_: None,
            auto_fire=True,
        )
        assert result == ""

    def test_prompt_reference_image_auto_fire_silent(self):
        result = prompt_reference_image_with_validation(
            input_fn=lambda *_: pytest.fail("input must not be called"),
            output_fn=lambda *_: None,
            auto_fire=True,
        )
        assert result == ""

    def test_prompt_reference_image_retry_on_missing(self, tmp_path):
        # User says yes, then types a ghost path twice, then a real one.
        real = tmp_path / "ref.png"
        real.write_bytes(b"\x89PNG\r\n")
        responses = iter(["y", "/no/such/file.png", "/another/ghost.png", str(real)])

        def _inp(_):
            return next(responses)

        out_buf: list[str] = []
        result = prompt_reference_image_with_validation(
            input_fn=_inp,
            output_fn=out_buf.append,
            auto_fire=False,
        )
        assert result == str(real.resolve())
        # The retry banner must have fired at least once.
        assert any("路径不存在" in line for line in out_buf)

    def test_prompt_reference_image_cancel(self, tmp_path):
        responses = iter(["y", "cancel"])
        result = prompt_reference_image_with_validation(
            input_fn=lambda _: next(responses),
            output_fn=lambda *_: None,
            auto_fire=False,
        )
        assert result == ""

    def test_prompt_vfx_overrides_auto_fire_silent(self):
        result = prompt_vfx_overrides(
            input_fn=lambda *_: pytest.fail("input must not be called"),
            output_fn=lambda *_: None,
            auto_fire=True,
        )
        assert result == {}

    def test_prompt_vfx_overrides_collects_y_n(self):
        # Y to enable, then individual answers per flag.
        responses = iter(["y", "y", "n", "skip", ""])
        result = prompt_vfx_overrides(
            input_fn=lambda _: next(responses),
            output_fn=lambda *_: None,
            auto_fire=False,
        )
        assert result == {"force_fluid": True, "force_physics": False}

    def test_render_preflight_manifest_banner(self):
        class _S:
            def to_dict(self):
                return {
                    "action_name": "dash",
                    "_visual_reference_path": "/tmp/x.png",
                    "vfx_overrides": {"force_fluid": True},
                    "active_vfx_plugins": ["fluid_momentum_controller"],
                }

        out: list[str] = []
        render_preflight_manifest(spec=_S(), skip_ai_render=False, output_fn=out.append)
        joined = "\n".join(out)
        assert "黄金通告单" in joined
        assert "dash" in joined
        assert "fluid_momentum_controller" in joined

    def test_confirm_ignition_auto_fire_logs_banner(self):
        out: list[str] = []
        result = confirm_ignition(
            input_fn=lambda *_: pytest.fail("input must not fire"),
            output_fn=out.append,
            auto_fire=True,
        )
        assert result is True
        assert any("auto-fire" in line.lower() or "自动" in line for line in out)

    def test_confirm_ignition_default_yes(self):
        result = confirm_ignition(
            input_fn=lambda *_: "",
            output_fn=lambda *_: None,
            auto_fire=False,
        )
        assert result is True

    def test_confirm_ignition_explicit_no(self):
        result = confirm_ignition(
            input_fn=lambda *_: "n",
            output_fn=lambda *_: None,
            auto_fire=False,
        )
        assert result is False


# ---------------------------------------------------------------------------
# (5) Headless director studio fast-path — end-to-end smoke test that the
# new ``run_wizard`` route fires without blocking on stdin.
# ---------------------------------------------------------------------------
class TestHeadlessDirectorStudioSmoke:
    def test_run_wizard_routes_mode_5_to_director_studio(self, tmp_path):
        # We mock _run_director_studio to capture the kwargs and avoid the
        # heavy parser/orchestrator path.
        with mock.patch.object(cli_wizard, "_run_director_studio", return_value=0) as m:
            rc = cli_wizard.run_wizard(
                argv=[
                    "--mode", "5",
                    "--yes",
                    "--action", "dash",
                    "--reference-image", str(tmp_path / "ghost.png"),
                    "--vfx-overrides", "force_fluid=1,force_physics=0",
                    "--project-root", str(tmp_path),
                ],
                stdin_isatty=False,
                input_fn=lambda *_: "",
                output_fn=lambda *_: None,
            )
        assert rc == 0
        kwargs = m.call_args.kwargs
        assert kwargs["auto_fire"] is True
        assert kwargs["cli_action"] == "dash"
        assert kwargs["cli_reference_image"] == str(tmp_path / "ghost.png")
        assert kwargs["cli_vfx_overrides"] == {
            "force_fluid": True,
            "force_physics": False,
        }
