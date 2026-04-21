"""SESSION-122: 4D SDF parameter animation and tensorized track tests.

This suite validates the new research-grounded dynamic parameter layer:

1. Sparse keyframes upsample to dense parameter tensors in one vectorized pass.
2. Runtime SDF decoding accepts per-frame parameter contexts without breaking
   the legacy static morphology contract.
3. Volume-preserving squash/stretch can drive a dynamic 4D field whose
   extracted mesh bounding-box volume evolves continuously.
4. TimeSamples-style export remains available for interchange layers.
"""
from __future__ import annotations

import inspect
import time

import numpy as np

from mathart.animation.dimension_uplift_engine import (
    DualContouringExtractor,
    SDFDimensionLifter,
)
from mathart.animation.parameter_track import (
    ParameterKeyframe,
    ParameterTrack,
    ParameterTrackBundle,
    TimeAwareMorphologyEvaluator,
    volume_preserving_axis_link,
)
from mathart.animation.smooth_morphology import (
    MorphologyGenotype,
    MorphologyPartGene,
)


class TestTensorizedParameterTrack:
    """Validate dense time sampling and matrix contracts."""

    def test_vectorized_upsample_to_dense_parameter_matrix(self):
        bundle = ParameterTrackBundle(
            tracks={
                "parts.0.param_a": ParameterTrack(
                    name="parts.0.param_a",
                    keyframes=[
                        ParameterKeyframe(0.0, 0.10),
                        ParameterKeyframe(0.5, 0.18),
                        ParameterKeyframe(1.0, 0.12),
                    ],
                    interpolation="catmull_rom",
                    clip_min=0.02,
                ),
                "parts.0.blend_k": ParameterTrack(
                    name="parts.0.blend_k",
                    keyframes=[
                        ParameterKeyframe(0.0, 0.03),
                        ParameterKeyframe(1.0, 0.08),
                    ],
                    interpolation="linear",
                    clip_min=0.0,
                ),
            }
        )
        times = np.linspace(0.0, 1.0, 257, dtype=np.float64)
        sampled = bundle.sample_parameter_matrix(times)

        assert sampled.values.shape == (257, 2)
        assert sampled.track_names == ("parts.0.param_a", "parts.0.blend_k")
        assert np.all(np.isfinite(sampled.values))
        assert abs(sampled.values[0, 0] - 0.10) < 1e-9
        assert abs(sampled.values[-1, 0] - 0.12) < 1e-9
        assert abs(sampled.values[0, 1] - 0.03) < 1e-9
        assert abs(sampled.values[-1, 1] - 0.08) < 1e-9

    def test_sampling_hot_path_is_vectorized_and_fast_for_10000_frames(self):
        track = ParameterTrack(
            name="parts.0.param_a",
            keyframes=[
                ParameterKeyframe(0.0, 0.12),
                ParameterKeyframe(0.25, 0.18),
                ParameterKeyframe(0.5, 0.24),
                ParameterKeyframe(0.75, 0.18),
                ParameterKeyframe(1.0, 0.12),
            ],
            interpolation="catmull_rom",
            clip_min=0.02,
        )
        times = np.linspace(0.0, 1.0, 10_000, dtype=np.float64)

        warm = track.sample(times)
        assert warm.shape == (10_000,)
        assert np.all(np.isfinite(warm))

        measurements = []
        for _ in range(5):
            t0 = time.perf_counter()
            result = track.sample(times)
            measurements.append(time.perf_counter() - t0)
            assert result.shape == (10_000,)

        best = min(measurements)
        assert best < 0.02, f"10k-frame sampling too slow: best={best:.6f}s"

        src = inspect.getsource(ParameterTrack.sample)
        assert "for t in" not in src
        assert "for frame in" not in src

    def test_timesamples_export_matches_dense_sampling(self):
        track = ParameterTrack(
            name="parts.0.param_a",
            keyframes=[
                ParameterKeyframe(0.0, 0.10),
                ParameterKeyframe(1.0, 0.20),
            ],
            interpolation="linear",
        )
        times = np.linspace(0.0, 1.0, 5)
        dense = track.sample(times)
        samples = track.to_time_samples(times, dense)

        assert list(samples.keys()) == [0.0, 0.25, 0.5, 0.75, 1.0]
        assert abs(samples[0.0] - 0.10) < 1e-9
        assert abs(samples[1.0] - 0.20) < 1e-9


class TestDynamicMorphologySDF:
    """Validate runtime parameter-context decoding for the SDF trunk."""

    def test_runtime_parameter_context_changes_decoded_shape(self):
        genotype = MorphologyGenotype(
            parts=[
                MorphologyPartGene(primitive="circle", param_a=0.10, parent_index=-1),
            ],
            bilateral_symmetry=False,
        )
        sdf_static = genotype.decode_to_sdf()
        sdf_dynamic = genotype.decode_to_sdf(
            parameter_context={"parts.0.param_a": 0.22}
        )

        query_x = np.array([0.16], dtype=np.float64)
        query_y = np.array([0.0], dtype=np.float64)
        assert sdf_static(query_x, query_y)[0] > 0.0
        assert sdf_dynamic(query_x, query_y)[0] < 0.0

    def test_time_aware_evaluator_streams_frame_contexts(self):
        genotype = MorphologyGenotype(
            parts=[
                MorphologyPartGene(primitive="circle", param_a=0.12, parent_index=-1),
            ],
            bilateral_symmetry=False,
        )
        bundle = ParameterTrackBundle(
            tracks={
                "parts.0.param_a": ParameterTrack(
                    name="parts.0.param_a",
                    keyframes=[
                        ParameterKeyframe(0.0, 0.12),
                        ParameterKeyframe(1.0, 0.24),
                    ],
                    interpolation="linear",
                )
            }
        )
        evaluator = TimeAwareMorphologyEvaluator(genotype=genotype, track_bundle=bundle)
        times = evaluator.make_time_tensor(4)
        sampled = evaluator.sample_tracks(times)
        sdf0 = evaluator.frame_sdf(sampled, 0)
        sdf3 = evaluator.frame_sdf(sampled, 3)

        qx = np.array([0.18], dtype=np.float64)
        qy = np.array([0.0], dtype=np.float64)
        assert sdf0(qx, qy)[0] > 0.0
        assert sdf3(qx, qy)[0] < 0.0


class TestVolumePreservingDynamicField:
    """Validate dynamic 4D field behavior through extracted mesh continuity."""

    def test_volume_preserving_dynamic_mesh_bbox_changes_smoothly(self):
        genotype = MorphologyGenotype(
            parts=[
                MorphologyPartGene(primitive="circle", param_a=0.18, parent_index=-1),
            ],
            bilateral_symmetry=False,
        )
        breathe = ParameterTrack(
            name="parts.0.scale_y",
            keyframes=[
                ParameterKeyframe(0.0, 1.0),
                ParameterKeyframe(0.5, 1.5),
                ParameterKeyframe(1.0, 1.0),
            ],
            interpolation="catmull_rom",
            clip_min=0.4,
            clip_max=2.0,
        )
        times = np.linspace(0.0, 1.0, 7, dtype=np.float64)
        scale_y = breathe.sample(times)
        scales_xyz = volume_preserving_axis_link(scale_y, axis="y")

        extractor = DualContouringExtractor(resolution=14)
        lifter = SDFDimensionLifter()
        bbox_volumes = []

        for i in range(len(times)):
            context = {
                "parts.0.scale_x": float(scales_xyz[i, 0]),
                "parts.0.scale_y": float(scales_xyz[i, 1]),
            }
            sdf_2d = genotype.decode_to_sdf(parameter_context=context)
            depth = 0.24 * float(scales_xyz[i, 2])

            def sdf_3d(x: float, y: float, z: float) -> float:
                d2 = float(sdf_2d(np.array([x]), np.array([y]))[0])
                return max(d2, abs(z) - depth * 0.5)

            mesh = extractor.extract(sdf_3d, bounds=(-0.6, 0.6))
            assert mesh.vertex_count > 0
            extents = np.ptp(mesh.vertices, axis=0)
            bbox_volume = float(np.prod(extents))
            bbox_volumes.append(bbox_volume)

        bbox_volumes = np.asarray(bbox_volumes, dtype=np.float64)
        assert np.all(np.isfinite(bbox_volumes))
        relative_span = (bbox_volumes.max() - bbox_volumes.min()) / max(bbox_volumes.mean(), 1e-8)
        assert relative_span < 0.35

        first_derivative = np.diff(bbox_volumes)
        second_derivative = np.diff(first_derivative)
        assert np.max(np.abs(second_derivative)) < 0.08
