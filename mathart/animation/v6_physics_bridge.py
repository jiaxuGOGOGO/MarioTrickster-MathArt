"""V6 lightweight physics/VFX bridge for Blender animation_data.

The archived exporter scripts are no longer the serialization path.  This
module wakes the reusable solver assets directly and embeds compact physical
samples into each UMR frame's metadata so Blender receives one clean
``animation_data`` contract containing character motion, fluid particles,
soft-body points, and terrain probes.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np

from mathart.animation.fluid_vfx import FluidDrivenVFXSystem, FluidImpulse, FluidVFXConfig
from mathart.animation.terrain_ik_2d import TerrainProbe2D
from mathart.animation.xpbd_solver import XPBDChainPreset, XPBDSolver, XPBDSolverConfig, build_xpbd_chain, create_default_xpbd_presets
from mathart.core.knowledge_interpreter import ClothParams, EffectActivationParams, FluidParams
from mathart.utils.core_math import json_safe


def _root_xy(frame: Any) -> tuple[float, float]:
    root = getattr(frame, "root_transform", None)
    if root is not None:
        return (float(getattr(root, "x", 0.0)), float(getattr(root, "y", 0.0)))
    if isinstance(frame, dict):
        rt = frame.get("root_transform") or {}
        return (float(rt.get("x", 0.0)), float(rt.get("y", 0.0)))
    return (0.0, 0.0)


def _build_fluid_payload(frame_count: int, fps: int, params: FluidParams) -> list[dict[str, Any]]:
    if not params.enabled:
        return [{"diagnostics": {}, "particle_samples": [], "params": params.to_dict()} for _ in range(frame_count)]
    config = FluidVFXConfig.slash_smoke(canvas_size=64)
    config.fluid.dt = 1.0 / max(fps, 1)
    config.fluid.grid_size = int(params.grid_size)
    config.emit_rate = int(params.emit_rate)
    config.density_gain = float(params.density_gain)
    config.source_velocity_scale = float(params.velocity_scale)
    config.particle_size_min = float(params.particle_radius) * 32.0
    config.particle_size_max = float(params.particle_radius) * 64.0
    system = FluidDrivenVFXSystem(config)
    impulses = [
        FluidImpulse(
            center_x=0.38 + 0.18 * math.sin(i * 0.41),
            center_y=0.64 + 0.08 * math.cos(i * 0.27),
            velocity_x=10.0 * math.cos(i * 0.33),
            velocity_y=6.0 * math.sin(i * 0.29),
            density=0.9,
            radius=0.11,
            label="v6_magic_wake",
        )
        for i in range(frame_count)
    ]
    system.simulate_and_render(frame_count, driver_impulses=impulses)
    payload: list[dict[str, Any]] = []
    for diag in system.last_diagnostics:
        particles = [
            {
                "x": round(float(p.x), 5),
                "y": round(float(p.y), 5),
                "size": round(float(p.size), 4),
                "alpha": round(1.0 - float(p.age_ratio), 4),
            }
            for p in system.particles[:32]
            if p.alive
        ]
        payload.append({"diagnostics": diag.to_dict(), "particle_samples": particles, "params": params.to_dict()})
    while len(payload) < frame_count:
        payload.append({"diagnostics": {}, "particle_samples": [], "params": params.to_dict()})
    return payload


def _build_xpbd_payload(frame_count: int, fps: int, params: ClothParams) -> list[dict[str, Any]]:
    if not params.enabled:
        return [{"points": [], "diagnostics": {}, "params": params.to_dict()} for _ in range(frame_count)]
    solver = XPBDSolver(XPBDSolverConfig(sub_steps=2, solver_iterations=4, gravity=(0.0, -5.5)))
    rigid_idx = solver.add_particle((0.0, 0.72), mass=4.0)
    base = create_default_xpbd_presets()[0]
    preset = XPBDChainPreset(
        name=base.name,
        anchor_joint=base.anchor_joint,
        anchor_offset=base.anchor_offset,
        rest_direction=base.rest_direction,
        segment_count=int(params.segment_count),
        segment_length=float(params.segment_length),
        compliance=max(1e-9, 1e-7 * (1.0 + (1.0 - float(params.damping)))),
        damping_compliance=max(1e-7, 1e-5 * (1.0 + float(params.damping))),
        bending_compliance=float(params.bend_stiffness),
        particle_mass=float(params.weight),
        tip_mass_scale=base.tip_mass_scale,
        particle_radius=base.particle_radius,
    )
    chain = build_xpbd_chain(solver, preset, anchor_position=(0.0, 0.64), rigid_com_index=rigid_idx)
    payload: list[dict[str, Any]] = []
    dt = 1.0 / max(fps, 1)
    for i in range(frame_count):
        anchor_x = 0.08 * math.sin(i * 0.35)
        anchor_y = 0.70 + 0.04 * math.sin(i * 0.21)
        solver.update_position(rigid_idx, (anchor_x, anchor_y))
        diag = solver.step(dt)
        positions = solver.positions
        payload.append({
            "points": [
                {"x": round(float(positions[idx, 0]), 5), "y": round(float(positions[idx, 1]), 5), "z": 0.02 * n}
                for n, idx in enumerate(chain)
            ],
            "diagnostics": diag.to_dict(),
            "params": params.to_dict(),
        })
    return payload


def _build_terrain_payload(frames: Sequence[Any]) -> list[dict[str, Any]]:
    probe = TerrainProbe2D()
    payload: list[dict[str, Any]] = []
    for frame in frames:
        x, _y = _root_xy(frame)
        samples = probe.probe_ahead(x - 0.35, lookahead=0.7, n_samples=5)
        normal = probe.surface_normal_2d(x)
        payload.append({
            "samples": [{"x": round(float(sx), 5), "y": round(float(sy), 5)} for sx, sy in samples],
            "surface_normal": {"x": round(float(normal[0]), 5), "y": round(float(normal[1]), 5)},
        })
    return payload


def enrich_clip_with_physics_payload(
    clip: Any,
    *,
    fluid_params: FluidParams | None = None,
    cloth_params: ClothParams | None = None,
    effects: EffectActivationParams | None = None,
) -> Any:
    frames = list(getattr(clip, "frames", []) or [])
    frame_count = len(frames)
    if frame_count <= 0:
        return clip
    fps = int(getattr(clip, "fps", 12) or 12)
    fluid_params = fluid_params or FluidParams()
    cloth_params = cloth_params or ClothParams()
    effects = effects or EffectActivationParams()
    fluid = _build_fluid_payload(frame_count, fps, fluid_params) if effects.fluid_vfx else [{"diagnostics": {}, "particle_samples": [], "params": fluid_params.to_dict()} for _ in range(frame_count)]
    cloth = _build_xpbd_payload(frame_count, fps, cloth_params) if effects.cloth_xpbd else [{"points": [], "diagnostics": {}, "params": cloth_params.to_dict()} for _ in range(frame_count)]
    terrain = _build_terrain_payload(frames) if effects.terrain_ik else [{"samples": [], "surface_normal": {"x": 0.0, "y": 1.0}} for _ in range(frame_count)]
    for idx, frame in enumerate(frames):
        metadata = getattr(frame, "metadata", None)
        if isinstance(metadata, dict):
            metadata["v6_physics_payload"] = json_safe({
                "fluid_vfx": fluid[idx],
                "cloth_xpbd": cloth[idx],
                "terrain_ik": terrain[idx],
                "effect_activation": effects.to_dict(),
            })
    return clip
