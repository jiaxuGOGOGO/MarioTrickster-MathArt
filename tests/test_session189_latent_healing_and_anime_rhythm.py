"""SESSION-189: Latent Healing & Anime-Rhythm Subsampler — closure tests.

These tests lock in the contract established by
``P0-SESSION-189-LATENT-HEALING-AND-ANIME-RHYTHM`` and must remain green
for every subsequent session. They cover:

1. ``anime_rhythmic_subsample`` — non-linear Ease-In/Ease-Out curve,
   exactly 16 unique ascending indices when N > 16; passthrough when
   N <= 16.
2. ``jit_matte_and_upscale`` — alpha matting onto (128, 128, 255) normal
   base, LANCZOS upscale to 512×512, output mode guaranteed ``"RGB"``.
3. ``heal_guide_sequences`` — end-to-end healing for source/normal/depth
   triples, including mismatched-length defensive checks.
4. ``force_override_workflow_payload`` — semantic ``class_type`` scan
   that enforces 512×512 latent canvas, CFG ≤ 4.5, ControlNet /
   SparseCtrl strength ≤ 0.55, VideoCombine frame_rate override; NEVER
   relies on hardcoded node ids.
"""
from __future__ import annotations

import math

import pytest
from PIL import Image

from mathart.core.anti_flicker_runtime import (
    DEPTH_MATTE_RGB,
    LATENT_EDGE,
    MAX_FRAMES,
    NORMAL_MATTE_RGB,
    SOURCE_MATTE_RGB,
    anime_rhythmic_subsample,
    force_override_workflow_payload,
    heal_guide_sequences,
    jit_matte_and_upscale,
)


# ---------------------------------------------------------------------------
# 1. anime_rhythmic_subsample
# ---------------------------------------------------------------------------


class TestAnimeRhythmicSubsample:
    def test_exact_length_when_oversupply(self) -> None:
        indices = anime_rhythmic_subsample(40, max_frames=16)
        assert len(indices) == 16

    def test_strictly_ascending_unique(self) -> None:
        indices = anime_rhythmic_subsample(40, max_frames=16)
        assert indices == sorted(set(indices))

    def test_endpoints_locked(self) -> None:
        indices = anime_rhythmic_subsample(40, max_frames=16)
        assert indices[0] == 0
        assert indices[-1] == 39

    def test_non_linear_kan_kyu_curve(self) -> None:
        """Middle gap must exceed the head/tail gaps — anime Impact beat."""
        indices = anime_rhythmic_subsample(100, max_frames=16)
        assert len(indices) == 16
        head_gap = indices[1] - indices[0]
        mid_gap = indices[len(indices) // 2] - indices[len(indices) // 2 - 1]
        tail_gap = indices[-1] - indices[-2]
        assert mid_gap > head_gap
        assert mid_gap > tail_gap

    def test_passthrough_when_under_budget(self) -> None:
        assert anime_rhythmic_subsample(10, max_frames=16) == list(range(10))

    def test_exactly_equal_to_budget(self) -> None:
        assert anime_rhythmic_subsample(16, max_frames=16) == list(range(16))

    def test_default_max_frames_is_16(self) -> None:
        indices = anime_rhythmic_subsample(40)
        assert len(indices) == MAX_FRAMES == 16

    def test_custom_budget(self) -> None:
        indices = anime_rhythmic_subsample(200, max_frames=8)
        assert len(indices) == 8

    def test_invalid_total_raises(self) -> None:
        with pytest.raises(ValueError):
            anime_rhythmic_subsample(0)

    def test_invalid_budget_raises(self) -> None:
        with pytest.raises(ValueError):
            anime_rhythmic_subsample(10, max_frames=0)


# ---------------------------------------------------------------------------
# 2. jit_matte_and_upscale
# ---------------------------------------------------------------------------


class TestJitMatteAndUpscale:
    def test_transparent_normal_gets_purple_blue_matte(self) -> None:
        rgba = Image.new("RGBA", (192, 192), (0, 0, 0, 0))
        healed = jit_matte_and_upscale(rgba, matte_rgb=NORMAL_MATTE_RGB)
        assert healed.mode == "RGB"
        assert healed.size == (LATENT_EDGE, LATENT_EDGE)
        assert healed.getpixel((0, 0)) == NORMAL_MATTE_RGB

    def test_opaque_pixels_survive_composite(self) -> None:
        rgba = Image.new("RGBA", (64, 64), (200, 50, 10, 255))
        healed = jit_matte_and_upscale(rgba, matte_rgb=NORMAL_MATTE_RGB)
        px = healed.getpixel((256, 256))
        assert px[0] >= 150 and px[1] <= 100 and px[2] <= 60

    def test_depth_default_matte_is_black(self) -> None:
        rgba = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        healed = jit_matte_and_upscale(rgba, matte_rgb=DEPTH_MATTE_RGB)
        assert healed.getpixel((0, 0)) == DEPTH_MATTE_RGB

    def test_rgb_input_passes_through_with_upscale(self) -> None:
        rgb = Image.new("RGB", (256, 256), (10, 20, 30))
        healed = jit_matte_and_upscale(rgb, matte_rgb=SOURCE_MATTE_RGB)
        assert healed.mode == "RGB"
        assert healed.size == (LATENT_EDGE, LATENT_EDGE)

    def test_target_edge_guard(self) -> None:
        rgb = Image.new("RGB", (32, 32), (0, 0, 0))
        with pytest.raises(ValueError):
            jit_matte_and_upscale(rgb, matte_rgb=SOURCE_MATTE_RGB, target_edge=8)


# ---------------------------------------------------------------------------
# 3. heal_guide_sequences
# ---------------------------------------------------------------------------


class TestHealGuideSequences:
    def _make_triplets(self, n: int, size: tuple[int, int] = (192, 192)):
        src = [Image.new("RGBA", size, (255, 100, 50, 255)) for _ in range(n)]
        nrm = [Image.new("RGBA", size, (128, 128, 255, 0)) for _ in range(n)]
        dep = [Image.new("RGBA", size, (0, 0, 0, 255)) for _ in range(n)]
        msk = [Image.new("L", size, 255) for _ in range(n)]
        return src, nrm, dep, msk

    def test_end_to_end_triplet_healing(self) -> None:
        src, nrm, dep, msk = self._make_triplets(40)
        out = heal_guide_sequences(
            source_frames=src,
            normal_maps=nrm,
            depth_maps=dep,
            mask_maps=msk,
        )
        assert len(out["source_frames"]) == MAX_FRAMES
        assert len(out["normal_maps"]) == MAX_FRAMES
        assert len(out["depth_maps"]) == MAX_FRAMES
        assert out["mask_maps"] is not None
        assert len(out["mask_maps"]) == MAX_FRAMES
        assert out["source_frames"][0].size == (LATENT_EDGE, LATENT_EDGE)
        assert out["normal_maps"][0].getpixel((0, 0)) == NORMAL_MATTE_RGB
        assert out["report"]["session"] == "SESSION-189"
        assert out["report"]["target_edge"] == LATENT_EDGE
        assert out["report"]["max_frames"] == MAX_FRAMES

    def test_mismatched_lengths_rejected(self) -> None:
        src, nrm, dep, _ = self._make_triplets(40)
        with pytest.raises(ValueError):
            heal_guide_sequences(
                source_frames=src,
                normal_maps=nrm[:-1],
                depth_maps=dep,
            )

    def test_passthrough_when_below_budget(self) -> None:
        src, nrm, dep, _ = self._make_triplets(8)
        out = heal_guide_sequences(
            source_frames=src,
            normal_maps=nrm,
            depth_maps=dep,
        )
        assert len(out["source_frames"]) == 8
        assert out["report"]["selected_indices"] == list(range(8))


# ---------------------------------------------------------------------------
# 4. force_override_workflow_payload
# ---------------------------------------------------------------------------


def _fresh_workflow() -> dict:
    # Intentionally non-contiguous, non-numeric-friendly node ids to prove
    # we NEVER rely on ``payload["5"]`` hardcoding.
    return {
        "node_latent_x": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 192, "height": 192, "batch_size": 40},
        },
        "sampler_main": {
            "class_type": "KSampler",
            "inputs": {"cfg": 9.5, "steps": 20, "denoise": 1.0},
        },
        "sampler_adv": {
            "class_type": "KSamplerAdvanced",
            "inputs": {"cfg": 7.5, "steps": 12},
        },
        "cn_normal": {
            "class_type": "ControlNetApplyAdvanced",
            "inputs": {"strength": 1.0, "start_percent": 0.0},
        },
        "cn_depth": {
            "class_type": "ControlNetApply",
            "inputs": {"strength": 0.9},
        },
        "sparse_loader": {
            "class_type": "ACN_SparseCtrlLoaderAdvanced",
            "inputs": {"motion_strength": 1.0},
        },
        "video_out": {
            "class_type": "VHS_VideoCombine",
            "inputs": {"frame_rate": 24, "filename_prefix": "m"},
        },
        "unrelated_string": "not-a-node",
    }


class TestForceOverrideWorkflowPayload:
    def test_latent_forced_to_512(self) -> None:
        wf = _fresh_workflow()
        force_override_workflow_payload(wf, actual_batch_size=16)
        latent = wf["node_latent_x"]["inputs"]
        assert latent["width"] == 512
        assert latent["height"] == 512
        assert latent["batch_size"] == 16

    def test_cfg_capped_across_all_samplers(self) -> None:
        wf = _fresh_workflow()
        force_override_workflow_payload(wf)
        assert wf["sampler_main"]["inputs"]["cfg"] == 4.5
        assert wf["sampler_adv"]["inputs"]["cfg"] == 4.5

    def test_controlnet_and_sparsectrl_strength_capped(self) -> None:
        wf = _fresh_workflow()
        force_override_workflow_payload(wf)
        assert wf["cn_normal"]["inputs"]["strength"] == 0.55
        assert wf["cn_depth"]["inputs"]["strength"] == 0.55
        assert wf["sparse_loader"]["inputs"]["motion_strength"] == 0.55

    def test_video_frame_rate_override_optional(self) -> None:
        wf = _fresh_workflow()
        force_override_workflow_payload(wf, video_frame_rate=8)
        assert wf["video_out"]["inputs"]["frame_rate"] == 8

    def test_video_frame_rate_untouched_when_not_supplied(self) -> None:
        wf = _fresh_workflow()
        force_override_workflow_payload(wf)
        assert wf["video_out"]["inputs"]["frame_rate"] == 24

    def test_ignores_non_dict_entries(self) -> None:
        wf = _fresh_workflow()
        report = force_override_workflow_payload(wf)
        assert "touched_nodes" in report
        assert all(isinstance(node, dict) for node in wf.values() if hasattr(node, "get"))

    def test_batch_size_respects_max_frames_ceiling(self) -> None:
        wf = _fresh_workflow()
        force_override_workflow_payload(wf, actual_batch_size=40)  # caller forgot to clamp
        assert wf["node_latent_x"]["inputs"]["batch_size"] == MAX_FRAMES  # 16

    def test_report_shape(self) -> None:
        wf = _fresh_workflow()
        report = force_override_workflow_payload(wf, actual_batch_size=16, video_frame_rate=8)
        assert report["session"] == "SESSION-189"
        assert report["target_edge"] == 512
        assert report["cfg_ceiling"] == 4.5
        assert report["controlnet_strength_ceiling"] == 0.55
        assert report["video_frame_rate"] == 8
        class_types_touched = {entry["class_type"] for entry in report["touched_nodes"]}
        assert "EmptyLatentImage" in class_types_touched
        assert "KSampler" in class_types_touched
        assert "VHS_VideoCombine" in class_types_touched


# ---------------------------------------------------------------------------
# 5. Red-line guard: absolutely no proxy env tampering in this module.
# ---------------------------------------------------------------------------


def test_module_source_never_touches_proxy_env() -> None:
    import mathart.core.anti_flicker_runtime as module
    import inspect

    source = inspect.getsource(module)
    for forbidden in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy"):
        assert forbidden not in source, (
            f"SESSION-189 red line breached: anti_flicker_runtime.py must not "
            f"mention {forbidden!r}"
        )


def test_cosine_curve_monotonic_weights() -> None:
    """Self-check: the underlying 0.5 - 0.5 * cos(pi * t) curve is
    monotonically non-decreasing from 0 to 1 over t in [0, 1]."""
    previous = -1.0
    for k in range(21):
        t = k / 20
        w = 0.5 - 0.5 * math.cos(math.pi * t)
        assert w + 1e-9 >= previous
        previous = w
    assert abs(previous - 1.0) < 1e-9
