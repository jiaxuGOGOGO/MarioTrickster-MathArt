from __future__ import annotations

import json
from pathlib import Path

from mathart.core.knowledge_interpreter import interpret_knowledge


def test_full_rosetta_protocol_schema_round_trip(tmp_path: Path):
    payload = {
        "meta": {
            "source_book": "Full Spectrum Tech Art Notes",
            "vibe_summary": "高张力厚涂跳台世界",
        },
        "TimingParams": {"hit_stop_frames": 8, "step_rate": 3},
        "PhysicsParams": {
            "anticipation_weight": 2.5,
            "impact_reward_weight": 3.0,
            "squash_max_stretch": 1.4,
        },
        "StyleParams": {
            "toon_bands": 3,
            "shadow_hardness": 0.9,
            "oklab_palette": ["#FF5733", "#C70039", "#900C3F", "#581845"],
        },
        "FluidParams": {
            "fluid_resolution": 0.3,
            "emission_strength": 2.0,
        },
        "ClothParams": {
            "cloth_damping": 0.5,
            "cloth_stiffness": 0.8,
        },
        "EnvironmentParams": {
            "wfc_platform_spacing": 4.5,
            "vertical_bias": 0.8,
        },
    }
    path = tmp_path / "full_rosetta_protocol.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    knowledge = interpret_knowledge(path)

    assert knowledge.timing.hit_stop_frames == 8
    assert knowledge.timing.step_rate == 3
    assert knowledge.physics.anticipation_weight == 2.5
    assert knowledge.physics.impact_reward_weight == 3.0
    assert knowledge.physics.squash_max_stretch == 1.4
    assert knowledge.style.toon_bands == 3
    assert knowledge.style.shadow_hardness == 0.9
    assert knowledge.style.oklab_color_palette == ["#FF5733", "#C70039", "#900C3F", "#581845"]
    assert knowledge.fluid.metaball_resolution == 0.3
    assert knowledge.fluid.glow_intensity == 2.0
    assert knowledge.cloth.damping == 0.5
    assert knowledge.cloth.bend_stiffness == 0.8
    assert knowledge.environment.wfc_platform_spacing == 4.5
    assert knowledge.environment.vertical_bias == 0.8
