"""SESSION-059: Unity URP 2D native pipeline + XPBD VAT baking.

This module turns the SESSION-059 research into repository-native tools:

1. **Unity URP 2D native wiring**
   - Generate Editor/runtime/shader helpers for automatic Secondary Texture
     binding (`_NormalMap`, `_MaskTex`) and offline VAT playback.
2. **Taichi XPBD -> VAT baking**
   - Sample offline cloth motion from the Taichi XPBD backend and encode the
     result into texture-driven displacement assets consumable by Unity.
3. **Research-grounded metadata**
   - Preserve slot naming, UV alignment, and playback bounds so exported assets
     stay self-describing across future sessions.

Research provenance:
    - Unity Manual: Secondary Textures in URP 2D (`_NormalMap`, `_MaskTex`)
    - Thomas Vasseur / Dead Cells 3D-to-2D pipeline references
    - Miles Macklin et al. 2016: XPBD compliance-driven stable simulation
    - SideFX VAT for Unity workflow (offline vertex data -> shader replay)
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from .engine_import_plugin import EngineImportPluginGenerator
from .xpbd_taichi import (
    TaichiXPBDClothSystem,
    create_default_taichi_cloth_config,
    get_taichi_xpbd_backend_status,
)


@dataclass(frozen=True)
class SecondaryTextureBinding:
    """Convention for mapping repository channels to Unity URP 2D slots."""

    semantic: str
    unity_slot: str
    filename_suffixes: tuple[str, ...]


SECONDARY_TEXTURE_BINDINGS: tuple[SecondaryTextureBinding, ...] = (
    SecondaryTextureBinding(
        semantic="normal",
        unity_slot="_NormalMap",
        filename_suffixes=("_normal", "-normal", ".normal", "_n"),
    ),
    SecondaryTextureBinding(
        semantic="mask",
        unity_slot="_MaskTex",
        filename_suffixes=("_mask", "-mask", ".mask", "_m"),
    ),
)


@dataclass
class XPBDVATBakeConfig:
    """Configuration for offline cloth VAT baking."""

    asset_name: str = "xpbd_cloth"
    frame_count: int = 24
    fps: int = 24
    dt: float = 1.0 / 60.0
    particle_budget: int = 256
    displacement_scale: float = 1.0
    include_preview: bool = True
    allow_synthetic_fallback: bool = True


@dataclass
class VATBakeManifest:
    """Persisted metadata required by shader-side VAT playback."""

    name: str
    frame_count: int
    vertex_count: int
    texture_width: int
    texture_height: int
    fps: int
    bounds_min: list[float]
    bounds_max: list[float]
    channels: dict[str, str] = field(default_factory=dict)
    source_backend: str = "synthetic"
    vertex_layout: str = "texel = (vertex_index, frame_index)"
    playback_notes: list[str] = field(default_factory=list)
    research_provenance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class UnityNativePipelineAudit:
    """Simple repository-side audit of generated Unity helpers."""

    importer_exists: bool = False
    postprocessor_exists: bool = False
    vat_player_exists: bool = False
    vat_shader_exists: bool = False
    README_exists: bool = False
    all_pass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


UNITY_SECONDARY_TEXTURE_POSTPROCESSOR_CS = r'''using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.U2D.Sprites;
using UnityEngine;

/// <summary>
/// Automatically binds MathArt auxiliary textures to URP 2D sprite secondary
/// texture slots so SpriteRenderer + Light2D can consume them natively.
///
/// Research provenance:
///   - Unity URP 2D Secondary Textures (`_NormalMap`, `_MaskTex`)
///   - Dead Cells-style multi-channel sprite workflow
/// </summary>
public class MathArtSecondaryTexturePostprocessor : AssetPostprocessor
{
    private static readonly Dictionary<string, string[]> SlotSuffixes =
        new Dictionary<string, string[]>
    {
        { "_NormalMap", new[] { "_normal", "-normal", ".normal", "_n" } },
        { "_MaskTex", new[] { "_mask", "-mask", ".mask", "_m" } },
    };

    static void OnPostprocessAllAssets(
        string[] importedAssets,
        string[] deletedAssets,
        string[] movedAssets,
        string[] movedFromAssetPaths)
    {
        foreach (var assetPath in importedAssets)
        {
            if (!IsCandidateSpriteTexture(assetPath))
                continue;
            TryBindSecondaryTextures(assetPath);
        }
    }

    private static bool IsCandidateSpriteTexture(string assetPath)
    {
        if (!assetPath.EndsWith(".png", StringComparison.OrdinalIgnoreCase))
            return false;
        string lower = assetPath.ToLowerInvariant();
        if (lower.Contains("_normal") || lower.Contains("-normal") || lower.Contains(".normal"))
            return false;
        if (lower.Contains("_mask") || lower.Contains("-mask") || lower.Contains(".mask"))
            return false;
        return true;
    }

    private static void TryBindSecondaryTextures(string spritePath)
    {
        var importer = AssetImporter.GetAtPath(spritePath) as TextureImporter;
        if (importer == null)
            return;

        importer.textureType = TextureImporterType.Sprite;
        importer.spriteImportMode = SpriteImportMode.Single;
        importer.filterMode = FilterMode.Point;
        importer.mipmapEnabled = false;

        var spriteAsset = AssetDatabase.LoadAssetAtPath<Texture2D>(spritePath);
        if (spriteAsset == null)
            return;

        var factories = new SpriteDataProviderFactories();
        factories.Init();
        var dataProvider = factories.GetSpriteEditorDataProviderFromObject(spriteAsset);
        if (dataProvider == null)
            return;

        dataProvider.InitSpriteEditorDataProvider();
        var secondaryProvider = dataProvider.GetDataProvider<ISecondaryTextureDataProvider>();
        if (secondaryProvider == null)
            return;

        var textures = new List<SecondarySpriteTexture>();
        string directory = Path.GetDirectoryName(spritePath).Replace("\\", "/");
        string stem = Path.GetFileNameWithoutExtension(spritePath);

        foreach (var kv in SlotSuffixes)
        {
            string slot = kv.Key;
            var suffixes = kv.Value;
            Texture2D channel = null;
            foreach (var suffix in suffixes)
            {
                string candidate = directory + "/" + stem + suffix + ".png";
                channel = AssetDatabase.LoadAssetAtPath<Texture2D>(candidate);
                if (channel != null)
                    break;
            }

            if (channel != null)
            {
                textures.Add(new SecondarySpriteTexture
                {
                    name = slot,
                    texture = channel,
                });
            }
        }

        secondaryProvider.textures = textures.ToArray();
        dataProvider.Apply();
        importer.SaveAndReimport();
    }
}
'''


UNITY_VAT_PLAYER_CS = r'''using UnityEngine;

/// <summary>
/// Minimal runtime component for replaying MathArt VAT baked cloth on a mesh in
/// a 2D URP scene. The mesh should live on the XY plane and store vertex lookup
/// coordinates in UV1/uv2.
/// </summary>
[ExecuteAlways]
[RequireComponent(typeof(MeshFilter), typeof(MeshRenderer))]
public class MathArtVATPlayer : MonoBehaviour
{
    public Texture2D positionTexture;
    public int frameCount = 1;
    public float playbackFps = 24f;
    public Vector2 boundsMin = new Vector2(-1f, -1f);
    public Vector2 boundsMax = new Vector2(1f, 1f);
    public float displacementStrength = 1f;
    public bool loop = true;

    private MeshFilter _meshFilter;
    private MeshRenderer _meshRenderer;
    private MaterialPropertyBlock _block;

    private void Awake()
    {
        Cache();
        EnsureLookupUVs();
        PushStaticParameters();
    }

    private void OnEnable()
    {
        Cache();
        EnsureLookupUVs();
        PushStaticParameters();
    }

    private void OnValidate()
    {
        Cache();
        EnsureLookupUVs();
        PushStaticParameters();
    }

    private void Update()
    {
        if (_meshRenderer == null || frameCount <= 0)
            return;

        if (_block == null)
            _block = new MaterialPropertyBlock();

        float timeFrames = Application.isPlaying
            ? Time.time * playbackFps
            : (float)UnityEditor.EditorApplication.timeSinceStartup * playbackFps;
        float frame = loop ? Mathf.Repeat(timeFrames, frameCount) : Mathf.Clamp(timeFrames, 0f, frameCount - 1);

        _meshRenderer.GetPropertyBlock(_block);
        _block.SetFloat("_VatFrame", frame);
        _meshRenderer.SetPropertyBlock(_block);
    }

    private void Cache()
    {
        if (_meshFilter == null)
            _meshFilter = GetComponent<MeshFilter>();
        if (_meshRenderer == null)
            _meshRenderer = GetComponent<MeshRenderer>();
        if (_block == null)
            _block = new MaterialPropertyBlock();
    }

    private void PushStaticParameters()
    {
        if (_meshRenderer == null)
            return;

        _meshRenderer.GetPropertyBlock(_block);
        _block.SetTexture("_VATPositionTex", positionTexture);
        _block.SetFloat("_VatFrameCount", Mathf.Max(1, frameCount));
        _block.SetVector("_VatBoundsMin", boundsMin);
        _block.SetVector("_VatBoundsMax", boundsMax);
        _block.SetFloat("_DisplacementStrength", displacementStrength);
        _meshRenderer.SetPropertyBlock(_block);
    }

    private void EnsureLookupUVs()
    {
        if (_meshFilter == null || _meshFilter.sharedMesh == null)
            return;

        var mesh = _meshFilter.sharedMesh;
        var vertices = mesh.vertices;
        if (vertices == null || vertices.Length == 0)
            return;

        var lookup = new Vector2[vertices.Length];
        for (int i = 0; i < vertices.Length; i++)
            lookup[i] = new Vector2((i + 0.5f) / vertices.Length, 0.5f);

        mesh.uv2 = lookup;
    }
}
'''


UNITY_VAT_SHADER = r'''Shader "MathArt/VATSpriteLit"
{
    Properties
    {
        _MainTex ("Albedo", 2D) = "white" {}
        _NormalMap ("Normal Map", 2D) = "bump" {}
        _VATPositionTex ("VAT Position Texture", 2D) = "gray" {}
        _VatFrame ("VAT Frame", Float) = 0
        _VatFrameCount ("VAT Frame Count", Float) = 1
        _VatBoundsMin ("VAT Bounds Min", Vector) = (-1,-1,0,0)
        _VatBoundsMax ("VAT Bounds Max", Vector) = (1,1,0,0)
        _DisplacementStrength ("Displacement Strength", Float) = 1
    }

    SubShader
    {
        Tags
        {
            "RenderPipeline" = "UniversalPipeline"
            "RenderType" = "Transparent"
            "Queue" = "Transparent"
        }

        Pass
        {
            Tags { "LightMode" = "Universal2D" }
            Blend SrcAlpha OneMinusSrcAlpha
            Cull Off
            ZWrite Off

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag

            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                float2 uv : TEXCOORD0;
                float2 lookupUV : TEXCOORD1;
                float4 color : COLOR;
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
                float4 color : COLOR;
            };

            TEXTURE2D(_MainTex);
            SAMPLER(sampler_MainTex);
            TEXTURE2D(_NormalMap);
            SAMPLER(sampler_NormalMap);
            TEXTURE2D(_VATPositionTex);
            SAMPLER(sampler_VATPositionTex);
            float _VatFrame;
            float _VatFrameCount;
            float4 _VatBoundsMin;
            float4 _VatBoundsMax;
            float _DisplacementStrength;

            float2 DecodePosition(float4 encoded)
            {
                float2 minXY = _VatBoundsMin.xy;
                float2 maxXY = _VatBoundsMax.xy;
                return lerp(minXY, maxXY, encoded.xy);
            }

            Varyings vert(Attributes input)
            {
                Varyings output;
                float frameV = (_VatFrame + 0.5) / max(_VatFrameCount, 1.0);
                float2 sampleUV = float2(input.lookupUV.x, frameV);
                float4 encoded = SAMPLE_TEXTURE2D_LOD(_VATPositionTex, sampler_VATPositionTex, sampleUV, 0);
                float2 targetXY = DecodePosition(encoded);
                float2 delta = (targetXY - input.positionOS.xy) * _DisplacementStrength;
                float3 displaced = float3(input.positionOS.x + delta.x, input.positionOS.y + delta.y, input.positionOS.z);
                output.positionCS = TransformObjectToHClip(displaced);
                output.uv = input.uv;
                output.color = input.color;
                return output;
            }

            half4 frag(Varyings input) : SV_Target
            {
                half4 albedo = SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, input.uv) * input.color;
                half3 normal = UnpackNormal(SAMPLE_TEXTURE2D(_NormalMap, sampler_NormalMap, input.uv));
                half3 lightDir = normalize(half3(0.35, 0.55, 0.75));
                half NdotL = saturate(dot(normal, lightDir));
                half3 lit = albedo.rgb * (0.2 + 0.8 * NdotL);
                return half4(lit, albedo.a);
            }
            ENDHLSL
        }
    }
}
'''


UNITY_NATIVE_README = """# MathArt Unity URP 2D Native Pipeline

This generated folder extends the existing `.mathart` importer with two high-priority SESSION-059 capabilities:

1. **Automatic Secondary Texture wiring** for `_NormalMap` and `_MaskTex` via an editor postprocessor.
2. **Offline XPBD VAT playback** for cloth-like 2D mesh deformation using a runtime component and shader.

## Files

| Path | Purpose |
|---|---|
| `Editor/MathArtImporter.cs` | Existing `.mathart` bundle importer |
| `Editor/MathArtSecondaryTexturePostprocessor.cs` | Auto-binds normal/mask maps into Sprite secondary textures |
| `Runtime/MathArtVATPlayer.cs` | Pushes playback parameters to the VAT shader |
| `Shaders/MathArtLitShader.shader` | Existing lit sprite shader |
| `Shaders/MathArtVATLit.shader` | Mesh VAT playback shader for offline cloth deformation |

## Naming convention

Use the main sprite texture as the anchor. For example, given `hero_idle.png`, place sibling files such as `hero_idle_normal.png` and `hero_idle_mask.png` in the same folder. The postprocessor maps them to `_NormalMap` and `_MaskTex` respectively.

## VAT convention

VAT position textures are encoded so that:

- texture **width = vertex count**
- texture **height = frame count**
- each texel stores one vertex position for one frame
- playback bounds are read from the JSON manifest and pushed to the shader by `MathArtVATPlayer`
"""


@dataclass
class VATBakeResult:
    """Concrete output of a VAT bake operation."""

    output_dir: Path
    manifest_path: Path
    texture_path: Path
    preview_path: Optional[Path]
    manifest: VATBakeManifest
    diagnostics: dict[str, Any] = field(default_factory=dict)


def _build_synthetic_cloth_frames(
    frame_count: int,
    grid_size: int = 8,
) -> np.ndarray:
    """Fallback cloth motion used when Taichi backend is unavailable.

    The fallback keeps the exporter testable in CPU-only environments while the
    manifest records that synthetic data was used.
    """
    xs = np.linspace(-0.5, 0.5, grid_size, dtype=np.float32)
    ys = np.linspace(0.0, 1.0, grid_size, dtype=np.float32)
    grid_x, grid_y = np.meshgrid(xs, ys)
    rest = np.stack([grid_x.reshape(-1), grid_y.reshape(-1)], axis=1)

    frames: list[np.ndarray] = []
    for idx in range(frame_count):
        phase = idx / max(frame_count - 1, 1)
        sway = 0.08 * np.sin((grid_y.reshape(-1) * 5.0) + phase * math.tau)
        lift = 0.03 * np.cos((grid_x.reshape(-1) * 4.0) + phase * math.tau)
        positions = rest.copy()
        positions[:, 0] += sway.astype(np.float32)
        positions[:, 1] += lift.astype(np.float32)
        frames.append(positions)
    return np.stack(frames, axis=0)


def collect_taichi_cloth_frames(
    config: XPBDVATBakeConfig,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Collect cloth positions from Taichi XPBD for VAT baking."""
    backend = get_taichi_xpbd_backend_status()
    if getattr(backend, "available", False):
        cloth_cfg = create_default_taichi_cloth_config(
            particle_budget=max(4, int(config.particle_budget)),
        )
        cloth = TaichiXPBDClothSystem(cloth_cfg)
        frames: list[np.ndarray] = []
        diag_records: list[float] = []
        for _ in range(config.frame_count):
            diag = cloth.step(config.dt)
            positions = np.asarray(cloth.positions_numpy(), dtype=np.float32)
            positions = positions.reshape(-1, positions.shape[-1])
            frames.append(positions[:, :2] * float(config.displacement_scale))
            diag_records.append(float(getattr(diag, "max_velocity_observed", 0.0)))
        return np.stack(frames, axis=0), {
            "backend_available": True,
            "backend_name": getattr(backend, "backend_name", "taichi"),
            "max_velocity_peak": max(diag_records) if diag_records else 0.0,
        }

    if not config.allow_synthetic_fallback:
        raise RuntimeError("Taichi XPBD backend unavailable and fallback disabled")

    return _build_synthetic_cloth_frames(config.frame_count), {
        "backend_available": False,
        "backend_name": "synthetic_fallback",
        "max_velocity_peak": 0.0,
    }


def _normalize_positions(
    positions: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pos = np.asarray(positions, dtype=np.float32)
    if pos.ndim != 3 or pos.shape[-1] != 2:
        raise ValueError("positions must have shape [frame_count, vertex_count, 2]")

    bounds_min = pos.min(axis=(0, 1))
    bounds_max = pos.max(axis=(0, 1))
    extent = np.maximum(bounds_max - bounds_min, 1e-6)
    normalized = (pos - bounds_min[None, None, :]) / extent[None, None, :]
    normalized = np.clip(normalized, 0.0, 1.0)
    return normalized, bounds_min, bounds_max


def encode_vat_position_texture(positions: np.ndarray) -> tuple[Image.Image, np.ndarray, np.ndarray]:
    """Encode [frames, vertices, 2] positions into an RGBA texture.

    R/G store normalized XY. B stores displacement magnitude for inspection.
    A stores opaque occupancy.
    """
    normalized, bounds_min, bounds_max = _normalize_positions(positions)
    frame_count, vertex_count, _ = normalized.shape

    magnitude = np.linalg.norm(normalized - normalized[0:1], axis=-1, keepdims=True)
    magnitude = np.clip(magnitude, 0.0, 1.0)

    rgba = np.zeros((frame_count, vertex_count, 4), dtype=np.uint8)
    rgba[..., 0:2] = np.round(normalized * 255.0).astype(np.uint8)
    rgba[..., 2:3] = np.round(magnitude * 255.0).astype(np.uint8)
    rgba[..., 3] = 255
    return Image.fromarray(rgba, mode="RGBA"), bounds_min, bounds_max


def build_vat_preview(positions: np.ndarray, size: int = 256) -> Image.Image:
    """Render a lightweight preview image of frame trajectories for audits."""
    normalized, _mn, _mx = _normalize_positions(positions)
    canvas = np.zeros((size, size, 4), dtype=np.uint8)
    palette = np.linspace(64, 255, normalized.shape[0], dtype=np.uint8)
    for frame_idx, frame in enumerate(normalized):
        color = palette[frame_idx]
        for vx, vy in frame:
            px = int(np.clip(vx, 0.0, 1.0) * (size - 1))
            py = int((1.0 - np.clip(vy, 0.0, 1.0)) * (size - 1))
            canvas[py, px] = [255, color, 96, 255]
    return Image.fromarray(canvas, mode="RGBA")


def bake_cloth_vat(
    output_dir: str | Path,
    *,
    config: Optional[XPBDVATBakeConfig] = None,
    positions: Optional[np.ndarray] = None,
) -> VATBakeResult:
    """Bake cloth motion into a Unity-friendly VAT directory."""
    cfg = config or XPBDVATBakeConfig()
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    diagnostics: dict[str, Any]
    if positions is None:
        positions, diagnostics = collect_taichi_cloth_frames(cfg)
    else:
        diagnostics = {
            "backend_available": False,
            "backend_name": "provided_positions",
            "max_velocity_peak": 0.0,
        }
        positions = np.asarray(positions, dtype=np.float32)

    texture, bounds_min, bounds_max = encode_vat_position_texture(positions)
    texture_path = out_dir / "vat_position.png"
    texture.save(texture_path)

    preview_path: Optional[Path] = None
    if cfg.include_preview:
        preview = build_vat_preview(positions)
        preview_path = out_dir / "vat_preview.png"
        preview.save(preview_path)

    frame_count, vertex_count, _ = positions.shape
    manifest = VATBakeManifest(
        name=cfg.asset_name,
        frame_count=int(frame_count),
        vertex_count=int(vertex_count),
        texture_width=int(vertex_count),
        texture_height=int(frame_count),
        fps=int(cfg.fps),
        bounds_min=[float(bounds_min[0]), float(bounds_min[1])],
        bounds_max=[float(bounds_max[0]), float(bounds_max[1])],
        channels={"position": texture_path.name},
        source_backend=str(diagnostics.get("backend_name", "synthetic")),
        playback_notes=[
            "Bind the position texture to _VATPositionTex.",
            "Store vertex lookup coordinates in uv2.x = (vertexIndex + 0.5) / vertexCount.",
            "Use the JSON bounds to decode normalized XY in the shader.",
        ],
        research_provenance=[
            "Unity URP 2D Secondary Textures manual",
            "Dead Cells 3D-to-2D multi-channel workflow references",
            "Macklin et al. 2016 XPBD",
            "SideFX VAT for Unity workflow",
        ],
    )
    manifest_path = out_dir / "vat_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return VATBakeResult(
        output_dir=out_dir,
        manifest_path=manifest_path,
        texture_path=texture_path,
        preview_path=preview_path,
        manifest=manifest,
        diagnostics=diagnostics,
    )


class UnityURP2DNativePipelineGenerator:
    """Generate Unity-native helpers for multi-channel sprites and cloth VAT."""

    def generate(self, output_dir: str | Path) -> dict[str, Path]:
        out_dir = Path(output_dir)
        EngineImportPluginGenerator().generate_unity_plugin(out_dir)

        editor_dir = out_dir / "Editor"
        runtime_dir = out_dir / "Runtime"
        shader_dir = out_dir / "Shaders"
        docs_dir = out_dir / "Docs"
        editor_dir.mkdir(parents=True, exist_ok=True)
        runtime_dir.mkdir(parents=True, exist_ok=True)
        shader_dir.mkdir(parents=True, exist_ok=True)
        docs_dir.mkdir(parents=True, exist_ok=True)

        (editor_dir / "MathArtSecondaryTexturePostprocessor.cs").write_text(
            UNITY_SECONDARY_TEXTURE_POSTPROCESSOR_CS,
            encoding="utf-8",
        )
        (runtime_dir / "MathArtVATPlayer.cs").write_text(
            UNITY_VAT_PLAYER_CS,
            encoding="utf-8",
        )
        (shader_dir / "MathArtVATLit.shader").write_text(
            UNITY_VAT_SHADER,
            encoding="utf-8",
        )
        (docs_dir / "MATHART_UNITY_URP2D_README.md").write_text(
            UNITY_NATIVE_README,
            encoding="utf-8",
        )

        return {
            "editor": editor_dir,
            "runtime": runtime_dir,
            "shaders": shader_dir,
            "docs": docs_dir,
        }

    def audit(self, output_dir: str | Path) -> UnityNativePipelineAudit:
        out_dir = Path(output_dir)
        audit = UnityNativePipelineAudit(
            importer_exists=(out_dir / "Editor" / "MathArtImporter.cs").exists(),
            postprocessor_exists=(out_dir / "Editor" / "MathArtSecondaryTexturePostprocessor.cs").exists(),
            vat_player_exists=(out_dir / "Runtime" / "MathArtVATPlayer.cs").exists(),
            vat_shader_exists=(out_dir / "Shaders" / "MathArtVATLit.shader").exists(),
            README_exists=(out_dir / "Docs" / "MATHART_UNITY_URP2D_README.md").exists(),
        )
        audit.all_pass = all([
            audit.importer_exists,
            audit.postprocessor_exists,
            audit.vat_player_exists,
            audit.vat_shader_exists,
            audit.README_exists,
        ])
        return audit


def generate_unity_urp_2d_native_pipeline(output_dir: str | Path) -> dict[str, Path]:
    """Convenience wrapper for Unity pipeline generation."""
    return UnityURP2DNativePipelineGenerator().generate(output_dir)


__all__ = [
    "SecondaryTextureBinding",
    "SECONDARY_TEXTURE_BINDINGS",
    "XPBDVATBakeConfig",
    "VATBakeManifest",
    "VATBakeResult",
    "UnityNativePipelineAudit",
    "UnityURP2DNativePipelineGenerator",
    "collect_taichi_cloth_frames",
    "encode_vat_position_texture",
    "build_vat_preview",
    "bake_cloth_vat",
    "generate_unity_urp_2d_native_pipeline",
]
