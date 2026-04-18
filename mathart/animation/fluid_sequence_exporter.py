"""
Fluid Simulation Sequence-Frame Exporter & Unity VFX Graph Bridge.

SESSION-062: Phase 4 — Environment Closed-Loop & Content Volume.

This module extends the existing ``FluidDrivenVFXSystem`` with the ability
to export simulation results as **flipbook atlases** and **flow-map textures**
suitable for consumption by Unity's **VFX Graph**.  It also generates a
Unity C# controller script that implements **velocity inheritance** — the
technique of passing the character's runtime velocity into the VFX Graph
so that particle effects (sword qi, dash smoke, etc.) realistically follow
the character's movement trajectory.

Research foundations:
  1. **Jos Stam — Stable Fluids (SIGGRAPH 1999)**: Semi-Lagrangian advection
     + pressure projection for divergence-free flow.  Our ``FluidGrid2D``
     already implements this.
  2. **Unity VFX Graph — Flipbook Player**: Texture atlas animation for
     particles, stepping through sub-images via ``texIndex`` attribute.
  3. **Unity — Inherit Velocity Module**: Parent velocity → particle velocity
     transfer with Current/Initial modes and multiplier control.
  4. **Taichi Lang (optional)**: GPU-accelerated stable fluids for higher
     resolution / real-time preview.  Graceful fallback to NumPy.

Architecture::

    FluidDrivenVFXSystem.simulate_and_render()
        ↓  list[Image.Image] + diagnostics
    FluidSequenceExporter
        ├─ export_density_atlas()   → flipbook PNG (NxN grid of frames)
        ├─ export_velocity_atlas()  → flow-map PNG (RG = normalized vx,vy)
        ├─ export_vfx_manifest()    → JSON manifest for Unity VFX Graph
        └─ export_all()             → complete export bundle

    Unity side:
    FluidVFXController.cs
        ├─ Load vfx_manifest.json
        ├─ Configure VFX Graph flipbook from atlas
        ├─ Read character Rigidbody2D.velocity each frame
        └─ Apply velocity inheritance to VFX Graph
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from PIL import Image

from .fluid_vfx import (
    FluidDrivenVFXSystem,
    FluidVFXConfig,
    FluidGrid2D,
    FluidGridConfig,
    FluidImpulse,
    FluidFrameDiagnostics,
    default_character_obstacle_mask,
)


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass
class FluidSequenceConfig:
    """Configuration for fluid sequence frame export."""

    # Simulation
    frame_count: int = 24
    canvas_size: int = 64
    driver_mode: str = "smoke"
    seed: int = 42

    # Atlas layout
    atlas_columns: int = 0  # 0 = auto (ceil(sqrt(frame_count)))
    atlas_padding: int = 0

    # Velocity encoding
    velocity_scale: float = 1.0  # Normalization scale for velocity field
    velocity_encoding: str = "rg_centered"  # "rg_centered" (0.5=zero) or "rg_unsigned"

    # Export options
    export_density_atlas: bool = True
    export_velocity_atlas: bool = True
    export_individual_frames: bool = False
    export_manifest: bool = True
    export_unity_controller: bool = True

    # Quality
    atlas_format: str = "PNG"  # PNG or EXR (if available)

    def effective_columns(self) -> int:
        if self.atlas_columns > 0:
            return self.atlas_columns
        return math.ceil(math.sqrt(self.frame_count))


@dataclass
class FluidSequenceManifest:
    """Manifest describing exported fluid VFX assets for Unity."""

    # Identity
    generator: str = "MarioTrickster-MathArt/FluidSequenceExporter"
    session: str = "SESSION-062"
    driver_mode: str = "smoke"

    # Atlas info
    density_atlas_path: str = ""
    velocity_atlas_path: str = ""
    atlas_columns: int = 0
    atlas_rows: int = 0
    frame_count: int = 0
    frame_width: int = 64
    frame_height: int = 64
    atlas_width: int = 0
    atlas_height: int = 0

    # Velocity encoding
    velocity_scale: float = 1.0
    velocity_encoding: str = "rg_centered"
    velocity_zero_value: float = 0.5  # Value representing zero velocity

    # Simulation parameters
    grid_size: int = 32
    dt: float = 1.0 / 60.0
    diffusion: float = 0.00025
    viscosity: float = 0.0001

    # Diagnostics summary
    mean_flow_energy: float = 0.0
    max_flow_speed: float = 0.0
    total_density_mass: float = 0.0

    # Unity VFX Graph hints
    suggested_lifetime: float = 1.0
    suggested_spawn_rate: int = 30
    suggested_inherit_velocity_multiplier: float = 0.7

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FluidSequenceExportResult:
    """Result of a fluid sequence export operation."""

    manifest_path: str = ""
    density_atlas_path: str = ""
    velocity_atlas_path: str = ""
    unity_controller_path: str = ""
    frame_paths: list[str] = field(default_factory=list)
    manifest: Optional[FluidSequenceManifest] = None
    success: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.manifest:
            d["manifest"] = self.manifest.to_dict()
        return d


# ── Atlas Builder ────────────────────────────────────────────────────────────


class FlipbookAtlasBuilder:
    """Build a flipbook texture atlas from a sequence of frames.

    Arranges frames in a grid layout suitable for Unity VFX Graph's
    Flipbook Player block.  The atlas dimensions are:
      width  = columns × frame_width
      height = rows × frame_height
    """

    @staticmethod
    def build(
        frames: list[Image.Image],
        columns: int = 0,
        padding: int = 0,
    ) -> tuple[Image.Image, int, int]:
        """Build a flipbook atlas from frames.

        Parameters
        ----------
        frames : list[Image.Image]
            Sequence of RGBA frames.
        columns : int
            Number of columns (0 = auto).
        padding : int
            Padding between frames in pixels.

        Returns
        -------
        tuple[Image.Image, int, int]
            (atlas_image, columns, rows)
        """
        n = len(frames)
        if n == 0:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0)), 0, 0

        fw, fh = frames[0].size
        cols = columns if columns > 0 else math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)

        atlas_w = cols * (fw + padding) - padding
        atlas_h = rows * (fh + padding) - padding
        atlas = Image.new("RGBA", (atlas_w, atlas_h), (0, 0, 0, 0))

        for i, frame in enumerate(frames):
            col = i % cols
            row = i // cols
            x = col * (fw + padding)
            y = row * (fh + padding)
            # Resize if needed
            if frame.size != (fw, fh):
                frame = frame.resize((fw, fh), Image.Resampling.LANCZOS)
            atlas.paste(frame, (x, y))

        return atlas, cols, rows


# ── Velocity Field Renderer ──────────────────────────────────────────────────


class VelocityFieldRenderer:
    """Render fluid velocity fields as flow-map textures.

    Encodes the 2D velocity field into image channels:
    - **RG-centered** (default): R = vx * 0.5 + 0.5, G = vy * 0.5 + 0.5
      (0.5 gray = zero velocity, standard flow-map convention)
    - **RG-unsigned**: R = |vx|, G = |vy|, B = sign encoding
    """

    @staticmethod
    def render_velocity_frame(
        fluid: FluidGrid2D,
        canvas_size: int,
        scale: float = 1.0,
        encoding: str = "rg_centered",
    ) -> Image.Image:
        """Render current velocity field as an RGBA image.

        Parameters
        ----------
        fluid : FluidGrid2D
            The fluid grid to sample.
        canvas_size : int
            Output image size.
        scale : float
            Velocity normalization scale.
        encoding : str
            Encoding mode.

        Returns
        -------
        Image.Image
            RGBA image with velocity encoded in channels.
        """
        n = fluid.n
        # Extract interior velocity fields
        u = fluid.u[1:n+1, 1:n+1].copy()
        v = fluid.v[1:n+1, 1:n+1].copy()

        # Normalize by scale
        if scale > 0:
            u = u / scale
            v = v / scale

        # Resize to canvas size
        u_img = Image.fromarray(
            ((u + 1.0) * 127.5).clip(0, 255).astype(np.uint8)
        ).resize((canvas_size, canvas_size), Image.Resampling.BILINEAR)
        v_img = Image.fromarray(
            ((v + 1.0) * 127.5).clip(0, 255).astype(np.uint8)
        ).resize((canvas_size, canvas_size), Image.Resampling.BILINEAR)

        u_arr = np.asarray(u_img, dtype=np.float64) / 255.0
        v_arr = np.asarray(v_img, dtype=np.float64) / 255.0

        # Build RGBA
        rgba = np.zeros((canvas_size, canvas_size, 4), dtype=np.uint8)

        if encoding == "rg_centered":
            # R = vx * 0.5 + 0.5, G = vy * 0.5 + 0.5, B = speed, A = 255
            rgba[:, :, 0] = (u_arr * 127.5 + 127.5).clip(0, 255).astype(np.uint8)
            rgba[:, :, 1] = (v_arr * 127.5 + 127.5).clip(0, 255).astype(np.uint8)
            speed = np.sqrt(u_arr**2 + v_arr**2)
            rgba[:, :, 2] = (speed * 255).clip(0, 255).astype(np.uint8)
            rgba[:, :, 3] = 255
        else:
            # Unsigned encoding
            rgba[:, :, 0] = (np.abs(u_arr) * 255).clip(0, 255).astype(np.uint8)
            rgba[:, :, 1] = (np.abs(v_arr) * 255).clip(0, 255).astype(np.uint8)
            # B channel: sign bits (bit 0 = u sign, bit 1 = v sign)
            signs = ((u_arr >= 0).astype(np.uint8) * 127 +
                     (v_arr >= 0).astype(np.uint8) * 127)
            rgba[:, :, 2] = signs
            rgba[:, :, 3] = 255

        return Image.fromarray(rgba, mode="RGBA")


# ── Main Exporter ────────────────────────────────────────────────────────────


class FluidSequenceExporter:
    """Export fluid simulation as flipbook atlases for Unity VFX Graph.

    This exporter:
    1. Runs the fluid simulation via ``FluidDrivenVFXSystem``.
    2. Captures per-frame density images and velocity fields.
    3. Packs them into flipbook atlas textures.
    4. Generates a JSON manifest describing the assets.
    5. Optionally generates a Unity C# controller script.

    Usage::

        from mathart.animation.fluid_sequence_exporter import (
            FluidSequenceExporter, FluidSequenceConfig
        )

        config = FluidSequenceConfig(
            frame_count=24,
            canvas_size=64,
            driver_mode="slash",
        )
        exporter = FluidSequenceExporter(config)
        result = exporter.export_all(output_dir="./vfx_export")
    """

    def __init__(self, config: Optional[FluidSequenceConfig] = None):
        self.config = config or FluidSequenceConfig()

    def _create_vfx_config(self) -> FluidVFXConfig:
        """Create a FluidVFXConfig matching our sequence config."""
        mode = self.config.driver_mode
        cs = self.config.canvas_size
        if mode == "dash":
            return FluidVFXConfig.dash_smoke(canvas_size=cs)
        elif mode == "slash":
            return FluidVFXConfig.slash_smoke(canvas_size=cs)
        else:
            return FluidVFXConfig.smoke_fluid(canvas_size=cs)

    def simulate(
        self,
        obstacle_mask: Optional[np.ndarray] = None,
    ) -> tuple[list[Image.Image], FluidDrivenVFXSystem]:
        """Run the fluid simulation and return frames + system reference.

        Returns
        -------
        tuple[list[Image.Image], FluidDrivenVFXSystem]
            (density_frames, vfx_system)
        """
        vfx_config = self._create_vfx_config()
        vfx_config = FluidVFXConfig(
            canvas_size=self.config.canvas_size,
            fluid=vfx_config.fluid,
            emit_rate=vfx_config.emit_rate,
            max_particles=vfx_config.max_particles,
            particle_lifetime_min=vfx_config.particle_lifetime_min,
            particle_lifetime_max=vfx_config.particle_lifetime_max,
            particle_size_min=vfx_config.particle_size_min,
            particle_size_max=vfx_config.particle_size_max,
            particle_follow_strength=vfx_config.particle_follow_strength,
            particle_drag=vfx_config.particle_drag,
            particle_jitter=vfx_config.particle_jitter,
            density_radius=vfx_config.density_radius,
            velocity_radius=vfx_config.velocity_radius,
            density_gain=vfx_config.density_gain,
            smoke_alpha=vfx_config.smoke_alpha,
            color_birth=vfx_config.color_birth,
            color_mid=vfx_config.color_mid,
            color_death=vfx_config.color_death,
            particle_color=vfx_config.particle_color,
            seed=self.config.seed,
            driver_mode=self.config.driver_mode,
            source_x=vfx_config.source_x,
            source_y=vfx_config.source_y,
            source_wobble=vfx_config.source_wobble,
            source_velocity_scale=vfx_config.source_velocity_scale,
        )

        system = FluidDrivenVFXSystem(vfx_config)
        frames = system.simulate_and_render(
            n_frames=self.config.frame_count,
            obstacle_mask=obstacle_mask,
        )
        return frames, system

    def render_velocity_frames(
        self, system: FluidDrivenVFXSystem
    ) -> list[Image.Image]:
        """Re-simulate to capture velocity field frames.

        Since the fluid state evolves during simulation, we run a second
        pass with the same parameters to capture velocity snapshots.
        For efficiency, we use the same fluid grid state.
        """
        # Re-create and re-simulate to capture velocity at each step
        vfx_config = self._create_vfx_config()
        fluid = FluidGrid2D(vfx_config.fluid)

        if self.config.driver_mode in {"dash", "slash"}:
            fluid.set_obstacle_mask(
                default_character_obstacle_mask(vfx_config.fluid.grid_size)
            )

        velocity_frames: list[Image.Image] = []
        renderer = VelocityFieldRenderer()

        # Track max velocity for normalization
        max_vel = 0.01
        temp_system = FluidDrivenVFXSystem(vfx_config)

        for frame_idx in range(self.config.frame_count):
            impulse = temp_system._default_impulse(
                frame_idx, self.config.frame_count
            )
            temp_system._apply_impulse(impulse)
            temp_system.fluid.step()

            # Track max velocity
            speed = temp_system.fluid.interior_speed()
            frame_max = float(np.max(speed)) if speed.size else 0.01
            max_vel = max(max_vel, frame_max)

            vel_frame = renderer.render_velocity_frame(
                temp_system.fluid,
                self.config.canvas_size,
                scale=max(self.config.velocity_scale, max_vel),
                encoding=self.config.velocity_encoding,
            )
            velocity_frames.append(vel_frame)

        return velocity_frames

    def export_all(
        self,
        output_dir: str = ".",
        prefix: str = "fluid_vfx",
        obstacle_mask: Optional[np.ndarray] = None,
    ) -> FluidSequenceExportResult:
        """Export complete fluid VFX bundle for Unity.

        Generates:
        - Density flipbook atlas (PNG)
        - Velocity flow-map atlas (PNG)
        - VFX manifest (JSON)
        - Unity C# controller script

        Parameters
        ----------
        output_dir : str
            Output directory.
        prefix : str
            Filename prefix.
        obstacle_mask : np.ndarray, optional
            Character obstacle mask.

        Returns
        -------
        FluidSequenceExportResult
        """
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        result = FluidSequenceExportResult()

        # 1. Simulate
        density_frames, system = self.simulate(obstacle_mask)

        # 2. Build density atlas
        if self.config.export_density_atlas:
            atlas, cols, rows = FlipbookAtlasBuilder.build(
                density_frames,
                columns=self.config.effective_columns(),
                padding=self.config.atlas_padding,
            )
            density_path = out / f"{prefix}_density_atlas.png"
            atlas.save(str(density_path))
            result.density_atlas_path = str(density_path)

        # 3. Build velocity atlas
        vel_cols, vel_rows = 0, 0
        if self.config.export_velocity_atlas:
            velocity_frames = self.render_velocity_frames(system)
            vel_atlas, vel_cols, vel_rows = FlipbookAtlasBuilder.build(
                velocity_frames,
                columns=self.config.effective_columns(),
                padding=self.config.atlas_padding,
            )
            velocity_path = out / f"{prefix}_velocity_atlas.png"
            vel_atlas.save(str(velocity_path))
            result.velocity_atlas_path = str(velocity_path)

        # 4. Export individual frames
        if self.config.export_individual_frames:
            frames_dir = out / f"{prefix}_frames"
            frames_dir.mkdir(exist_ok=True)
            for i, frame in enumerate(density_frames):
                fp = frames_dir / f"frame_{i:04d}.png"
                frame.save(str(fp))
                result.frame_paths.append(str(fp))

        # 5. Build manifest
        cols = self.config.effective_columns()
        rows_count = math.ceil(self.config.frame_count / cols)
        diag = system.last_diagnostics
        mean_energy = float(np.mean([d.mean_flow_energy for d in diag])) if diag else 0.0
        max_speed = float(np.max([d.max_flow_speed for d in diag])) if diag else 0.0
        total_mass = float(np.sum([d.density_mass for d in diag])) if diag else 0.0

        manifest = FluidSequenceManifest(
            driver_mode=self.config.driver_mode,
            density_atlas_path=result.density_atlas_path,
            velocity_atlas_path=result.velocity_atlas_path,
            atlas_columns=cols,
            atlas_rows=rows_count,
            frame_count=self.config.frame_count,
            frame_width=self.config.canvas_size,
            frame_height=self.config.canvas_size,
            atlas_width=cols * self.config.canvas_size,
            atlas_height=rows_count * self.config.canvas_size,
            velocity_scale=self.config.velocity_scale,
            velocity_encoding=self.config.velocity_encoding,
            grid_size=system.config.fluid.grid_size,
            dt=system.config.fluid.dt,
            diffusion=system.config.fluid.diffusion,
            viscosity=system.config.fluid.viscosity,
            mean_flow_energy=round(mean_energy, 6),
            max_flow_speed=round(max_speed, 4),
            total_density_mass=round(total_mass, 4),
            suggested_lifetime=system.config.particle_lifetime_max,
            suggested_spawn_rate=system.config.emit_rate * 3,
            suggested_inherit_velocity_multiplier=0.7,
        )

        if self.config.export_manifest:
            manifest_path = out / f"{prefix}_manifest.json"
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest.to_dict(), f, indent=2, ensure_ascii=False)
            result.manifest_path = str(manifest_path)

        result.manifest = manifest

        # 6. Generate Unity controller
        if self.config.export_unity_controller:
            controller_path = generate_fluid_vfx_controller(str(out))
            result.unity_controller_path = controller_path

        result.success = True
        return result


# ── Unity C# Controller Generator ───────────────────────────────────────────


UNITY_FLUID_VFX_CONTROLLER_CS = r'''using System;
using System.IO;
using UnityEngine;
using UnityEngine.VFX;

/// <summary>
/// Controls a Unity VFX Graph driven by pre-baked fluid simulation data.
///
/// This controller implements **velocity inheritance**: the character's
/// runtime velocity is passed into the VFX Graph so that particle effects
/// (sword qi, dash smoke, etc.) realistically follow the character's
/// movement trajectory.
///
/// Research provenance:
///   - Jos Stam: Stable Fluids (SIGGRAPH 1999)
///   - Unity VFX Graph: Flipbook Player + Set Velocity from Map
///   - Unity: Inherit Velocity Module (Current/Initial modes)
///
/// Setup:
///   1. Attach this component to a GameObject with a VisualEffect component.
///   2. Assign the character's Rigidbody2D reference.
///   3. Load the VFX manifest JSON (auto or manual).
///   4. Assign density and velocity atlas textures.
///
/// VFX Graph Requirements:
///   - Exposed Texture2D: "DensityAtlas" and "VelocityAtlas"
///   - Exposed Vector2: "CharacterVelocity"
///   - Exposed Float: "InheritVelocityMultiplier"
///   - Exposed Int: "FlipbookColumns" and "FlipbookRows"
///   - Exposed Float: "FrameCount" and "FrameRate"
/// </summary>
[RequireComponent(typeof(VisualEffect))]
public class FluidVFXController : MonoBehaviour
{
    [Header("References")]
    [Tooltip("The character's Rigidbody2D for velocity inheritance")]
    public Rigidbody2D characterRigidbody;

    [Tooltip("Optional: TextAsset containing the VFX manifest JSON")]
    public TextAsset manifestAsset;

    [Header("Atlas Textures")]
    [Tooltip("Pre-baked density flipbook atlas")]
    public Texture2D densityAtlas;

    [Tooltip("Pre-baked velocity flow-map atlas")]
    public Texture2D velocityAtlas;

    [Header("Velocity Inheritance")]
    [Tooltip("How much of the character's velocity is inherited by particles")]
    [Range(0f, 2f)]
    public float inheritVelocityMultiplier = 0.7f;

    [Tooltip("Velocity inheritance mode")]
    public VelocityInheritMode inheritMode = VelocityInheritMode.Current;

    [Header("Flipbook Settings")]
    public int flipbookColumns = 5;
    public int flipbookRows = 5;
    public int frameCount = 24;
    public float frameRate = 12f;

    [Header("Runtime State")]
    [SerializeField] private Vector2 _currentCharacterVelocity;
    [SerializeField] private Vector2 _inheritedVelocity;
    [SerializeField] private float _currentFlipbookFrame;

    public enum VelocityInheritMode
    {
        Current,    // Apply character velocity every frame
        Initial,    // Apply only at particle birth
        Blended     // Blend between current and initial
    }

    private VisualEffect _vfx;
    private float _flipbookTimer;

    // VFX Graph property IDs (cached for performance)
    private static readonly int _densityAtlasId = Shader.PropertyToID("DensityAtlas");
    private static readonly int _velocityAtlasId = Shader.PropertyToID("VelocityAtlas");
    private static readonly int _charVelocityId = Shader.PropertyToID("CharacterVelocity");
    private static readonly int _inheritMultId = Shader.PropertyToID("InheritVelocityMultiplier");
    private static readonly int _flipColsId = Shader.PropertyToID("FlipbookColumns");
    private static readonly int _flipRowsId = Shader.PropertyToID("FlipbookRows");
    private static readonly int _frameCountId = Shader.PropertyToID("FrameCount");
    private static readonly int _frameRateId = Shader.PropertyToID("FrameRate");
    private static readonly int _currentFrameId = Shader.PropertyToID("CurrentFrame");

    private void Awake()
    {
        _vfx = GetComponent<VisualEffect>();

        if (manifestAsset != null)
        {
            LoadManifest(manifestAsset.text);
        }
    }

    private void Start()
    {
        ApplySettings();
    }

    private void Update()
    {
        UpdateVelocityInheritance();
        UpdateFlipbook();
    }

    /// <summary>Load settings from a VFX manifest JSON string.</summary>
    public void LoadManifest(string json)
    {
        try
        {
            var manifest = JsonUtility.FromJson<VFXManifest>(json);
            if (manifest != null)
            {
                flipbookColumns = manifest.atlas_columns;
                flipbookRows = manifest.atlas_rows;
                frameCount = manifest.frame_count;
                frameRate = 1f / Mathf.Max(manifest.dt, 0.001f);
                inheritVelocityMultiplier = manifest.suggested_inherit_velocity_multiplier;

                Debug.Log($"FluidVFXController: Loaded manifest — " +
                          $"{frameCount} frames, {flipbookColumns}x{flipbookRows} atlas, " +
                          $"mode={manifest.driver_mode}");
            }
        }
        catch (Exception e)
        {
            Debug.LogWarning($"FluidVFXController: Failed to parse manifest: {e.Message}");
        }
    }

    /// <summary>Apply current settings to the VFX Graph.</summary>
    public void ApplySettings()
    {
        if (_vfx == null) return;

        if (densityAtlas != null && _vfx.HasTexture(_densityAtlasId))
            _vfx.SetTexture(_densityAtlasId, densityAtlas);

        if (velocityAtlas != null && _vfx.HasTexture(_velocityAtlasId))
            _vfx.SetTexture(_velocityAtlasId, velocityAtlas);

        if (_vfx.HasInt(_flipColsId))
            _vfx.SetInt(_flipColsId, flipbookColumns);
        if (_vfx.HasInt(_flipRowsId))
            _vfx.SetInt(_flipRowsId, flipbookRows);
        if (_vfx.HasFloat(_frameCountId))
            _vfx.SetFloat(_frameCountId, frameCount);
        if (_vfx.HasFloat(_frameRateId))
            _vfx.SetFloat(_frameRateId, frameRate);
        if (_vfx.HasFloat(_inheritMultId))
            _vfx.SetFloat(_inheritMultId, inheritVelocityMultiplier);
    }

    private void UpdateVelocityInheritance()
    {
        if (characterRigidbody == null || _vfx == null) return;

        _currentCharacterVelocity = characterRigidbody.velocity;

        switch (inheritMode)
        {
            case VelocityInheritMode.Current:
                _inheritedVelocity = _currentCharacterVelocity * inheritVelocityMultiplier;
                break;

            case VelocityInheritMode.Initial:
                // Initial mode: only update when VFX is first triggered
                // (handled by VFX Graph's Initialize context)
                _inheritedVelocity = _currentCharacterVelocity * inheritVelocityMultiplier;
                break;

            case VelocityInheritMode.Blended:
                // Smooth blend between current and previous
                _inheritedVelocity = Vector2.Lerp(
                    _inheritedVelocity,
                    _currentCharacterVelocity * inheritVelocityMultiplier,
                    Time.deltaTime * 8f
                );
                break;
        }

        if (_vfx.HasVector2(_charVelocityId))
            _vfx.SetVector2(_charVelocityId, _inheritedVelocity);
    }

    private void UpdateFlipbook()
    {
        if (_vfx == null || frameCount <= 0) return;

        _flipbookTimer += Time.deltaTime * frameRate;
        _currentFlipbookFrame = _flipbookTimer % frameCount;

        if (_vfx.HasFloat(_currentFrameId))
            _vfx.SetFloat(_currentFrameId, _currentFlipbookFrame);
    }

    /// <summary>Trigger the VFX effect (e.g., on sword slash).</summary>
    public void TriggerEffect()
    {
        if (_vfx != null)
        {
            _flipbookTimer = 0f;
            _vfx.Play();
        }
    }

    /// <summary>Stop the VFX effect.</summary>
    public void StopEffect()
    {
        if (_vfx != null)
            _vfx.Stop();
    }

    [Serializable]
    private class VFXManifest
    {
        public string generator;
        public string driver_mode;
        public int atlas_columns;
        public int atlas_rows;
        public int frame_count;
        public float dt;
        public float suggested_inherit_velocity_multiplier;
    }
}
'''


def generate_fluid_vfx_controller(output_dir: str) -> str:
    """Generate the Unity C# FluidVFXController script.

    Parameters
    ----------
    output_dir : str
        Directory to write the C# file.

    Returns
    -------
    str
        Path to the generated C# file.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    cs_path = out / "FluidVFXController.cs"
    cs_path.write_text(
        UNITY_FLUID_VFX_CONTROLLER_CS.strip(), encoding="utf-8"
    )
    return str(cs_path)


# ── Convenience Function ─────────────────────────────────────────────────────


def export_fluid_vfx_bundle(
    driver_mode: str = "smoke",
    frame_count: int = 24,
    canvas_size: int = 64,
    seed: int = 42,
    output_dir: str = ".",
) -> FluidSequenceExportResult:
    """One-shot: simulate fluid VFX and export complete Unity bundle.

    Usage::

        result = export_fluid_vfx_bundle(
            driver_mode="slash",
            frame_count=24,
            output_dir="./vfx_export"
        )
    """
    config = FluidSequenceConfig(
        frame_count=frame_count,
        canvas_size=canvas_size,
        driver_mode=driver_mode,
        seed=seed,
    )
    exporter = FluidSequenceExporter(config)
    return exporter.export_all(output_dir=output_dir)


__all__ = [
    "FluidSequenceConfig",
    "FluidSequenceManifest",
    "FluidSequenceExportResult",
    "FlipbookAtlasBuilder",
    "VelocityFieldRenderer",
    "FluidSequenceExporter",
    "generate_fluid_vfx_controller",
    "export_fluid_vfx_bundle",
]
