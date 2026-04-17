import numpy as np

from mathart.animation import (
    Skeleton,
    SDFSamplingGrid,
    bake_sdf_auxiliary_maps,
    render_character_maps_industrial,
)
from mathart.animation.analytic_sdf import circle_distance_gradient
from mathart.animation.character_presets import get_preset
from mathart.animation.presets import idle_animation


def test_bake_sdf_auxiliary_maps_circle_produces_depth_thickness_and_normals():
    def circle_sdf(x, y):
        return np.sqrt(x ** 2 + y ** 2) - 0.35

    baked = bake_sdf_auxiliary_maps(
        circle_sdf,
        grid=SDFSamplingGrid(width=33, height=33, x_min=-0.6, x_max=0.6, y_max=0.6, y_min=-0.6),
        analytic_gradient=circle_distance_gradient(0.35),
    )

    assert baked.normal_map_image.size == (33, 33)
    assert baked.depth_map_image.size == (33, 33)
    assert baked.thickness_map_image.size == (33, 33)
    assert baked.roughness_map_image.size == (33, 33)
    assert baked.mask_image.size == (33, 33)
    assert baked.metadata["inside_pixel_count"] > 0
    assert baked.metadata["gradient_source"] == "analytic"

    center = (16, 16)
    edge = (16, 8)
    outside = (0, 0)

    assert baked.inside_mask[center]
    assert baked.depth_values[center] > baked.depth_values[edge] > 0.0
    assert baked.thickness_values[center] > baked.thickness_values[edge] > 0.0
    assert baked.depth_values[outside] == 0.0
    assert baked.normal_vectors[center][2] > baked.normal_vectors[edge][2]
    assert 0.0 <= baked.roughness_values[center] <= 1.0
    assert 0.0 <= baked.roughness_values[edge] <= 1.0

    normal_rgba = np.array(baked.normal_map_image)
    depth_rgba = np.array(baked.depth_map_image)
    thickness_rgba = np.array(baked.thickness_map_image)
    roughness_rgba = np.array(baked.roughness_map_image)
    assert normal_rgba[center][3] == 255
    assert depth_rgba[outside][3] == 0
    assert thickness_rgba[center][0] > thickness_rgba[edge][0]
    assert roughness_rgba[center][3] == 255


def test_render_character_maps_industrial_returns_full_material_bundle():
    skeleton = Skeleton.create_humanoid()
    style, palette = get_preset("mario")
    pose = idle_animation(0.0)

    result = render_character_maps_industrial(
        skeleton,
        pose,
        style,
        width=32,
        height=32,
        palette=palette,
    )

    assert result.albedo_image.mode == "RGBA"
    assert result.normal_map_image.mode == "RGBA"
    assert result.depth_map_image.mode == "RGBA"
    assert result.thickness_map_image.mode == "RGBA"
    assert result.roughness_map_image.mode == "RGBA"
    assert result.mask_image.mode == "RGBA"
    assert result.albedo_image.size == (32, 32)
    assert result.normal_map_image.size == (32, 32)
    assert result.depth_map_image.size == (32, 32)
    assert result.thickness_map_image.size == (32, 32)
    assert result.roughness_map_image.size == (32, 32)
    assert result.mask_image.size == (32, 32)
    assert result.metadata["inside_pixel_count"] > 0
    assert result.metadata["part_count"] > 0
    assert result.metadata["gradient_source"] in {"analytic_union", "analytic_union_hybrid", "central_difference"}
    assert "thickness" in result.metadata["engine_channels"]
    assert "roughness" in result.metadata["engine_channels"]

    normal_rgba = np.array(result.normal_map_image)
    depth_rgba = np.array(result.depth_map_image)
    thickness_rgba = np.array(result.thickness_map_image)
    roughness_rgba = np.array(result.roughness_map_image)
    assert int(np.count_nonzero(normal_rgba[..., 3])) > 0
    assert int(np.count_nonzero(depth_rgba[..., 3])) > 0
    assert int(np.count_nonzero(thickness_rgba[..., 3])) > 0
    assert int(np.count_nonzero(roughness_rgba[..., 3])) > 0
    assert int(depth_rgba[..., :3].max()) > int(depth_rgba[..., :3].min())
    assert int(thickness_rgba[..., :3].max()) >= int(thickness_rgba[..., :3].min())
