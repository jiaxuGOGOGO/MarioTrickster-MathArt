"""High-Precision Float VAT Baking & Unity Material Preset — P1-VAT-PRECISION-1.

SESSION-116: Industrial-grade Vertex Animation Texture pipeline that
eliminates the 8-bit precision catastrophe of the legacy SESSION-059 encoder.

Research Foundations
--------------------
1. **SideFX Houdini VAT 3.0 (Labs)** — Position displacement data MUST use
   HDR (float) textures.  8-bit sRGB PNG causes severe vertex jitter.
   Official docs: "Select HDR if your performance budget can afford it,
   because it offers better precision for all the data you are exporting."
2. **Global Bounding Box Quantization** — Scale & Bias MUST be computed
   from the *global* min/max across ALL frames and ALL vertices.  Per-frame
   normalization causes catastrophic "scale pumping" where the model
   wildly scales/shrinks each frame.
3. **Unity Texture Importer Discipline** — VAT position textures MUST be
   imported with: ``sRGB = False`` (Linear), ``Filter = Point``,
   ``Compression = None``, ``Generate Mip Maps = False``.  Any compression
   or gamma correction irreversibly destroys mathematical position data.

Architecture Discipline
-----------------------
- This module is an **independent plugin** that upgrades the SESSION-059
  ``bake_cloth_vat`` pipeline.  It does NOT modify any core orchestrator,
  ``AssetPipeline``, or ``if/else`` routing.
- Strong-typed contracts: ``HighPrecisionVATManifest`` explicitly declares
  the texture as high-precision float type.
- Dual export strategy:
  (a) **Primary**: Raw ``float32`` binary (``.npy``) — zero precision loss,
      mathematically provable RMSE = 0.
  (b) **Visual HDR**: Radiance ``.hdr`` via ``cv2.imwrite`` — ~1/256 precision,
      suitable for visual inspection and engines that support HDR import.
  (c) **Unity Hi-Lo Pack**: Two 8-bit PNG textures (high byte + low byte)
      that reconstruct 16-bit precision in the shader.  This is the
      Houdini VAT 3.0 "Split Positions into Two Textures" approach.

Anti-Red-Line Guards
--------------------
- 🔴 **Anti-Precision-Loss Guard**: ZERO ``np.uint8`` or ``* 255`` in the
  float export path.  All position data stays ``np.float32``.
- 🔴 **Anti-Local-Bounds Trap**: Global bounds computed via
  ``np.min(positions, axis=(0, 1))`` across ALL frames and vertices.
- 🔴 **Anti-C++-Build Trap**: Only ``cv2`` (HDR) and ``numpy`` (npy) are
  used.  No OpenEXR C++ dependency.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

# ═══════════════════════════════════════════════════════════════════════════
# Strong-Typed Contracts
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class HighPrecisionVATConfig:
    """Configuration for high-precision VAT baking.

    Attributes
    ----------
    asset_name : str
        Name of the VAT asset.
    fps : int
        Playback frame rate.
    export_hdr : bool
        Whether to export Radiance HDR visual preview.
    export_npy : bool
        Whether to export raw float32 binary (zero loss).
    export_hilo_png : bool
        Whether to export Unity Hi-Lo 16-bit packed PNGs.
    include_preview : bool
        Whether to generate a visual trajectory preview.
    displacement_scale : float
        Global displacement multiplier.
    """

    asset_name: str = "high_precision_vat"
    fps: int = 24
    export_hdr: bool = True
    export_npy: bool = True
    export_hilo_png: bool = True
    include_preview: bool = True
    displacement_scale: float = 1.0


@dataclass
class HighPrecisionVATManifest:
    """Strongly-typed manifest for high-precision VAT assets.

    This manifest explicitly declares the texture as high-precision float
    type and includes all metadata required for shader-side decode.

    The ``bounds_min`` and ``bounds_max`` are the GLOBAL bounding box
    computed across ALL frames and ALL vertices — never per-frame.
    """

    name: str
    frame_count: int
    vertex_count: int
    texture_width: int
    texture_height: int
    fps: int
    bounds_min: list[float]
    bounds_max: list[float]
    bounds_extent: list[float]
    precision: str = "float32"
    encoding: str = "global_bounds_normalized"
    channels: dict[str, str] = field(default_factory=dict)
    unity_import_settings: dict[str, Any] = field(default_factory=dict)
    source_backend: str = "high_precision_vat"
    vertex_layout: str = "row = vertex_index, col = frame_index"
    decode_formula: str = "worldPos = texColor.rgb * (boundsMax - boundsMin) + boundsMin"
    research_provenance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HighPrecisionVATResult:
    """Complete result of a high-precision VAT bake operation."""

    output_dir: Path
    manifest_path: Path
    npy_path: Optional[Path] = None
    hdr_path: Optional[Path] = None
    hilo_hi_path: Optional[Path] = None
    hilo_lo_path: Optional[Path] = None
    preview_path: Optional[Path] = None
    manifest: Optional[HighPrecisionVATManifest] = None
    shader_path: Optional[Path] = None
    material_preset_path: Optional[Path] = None
    diagnostics: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Tensor-Level Global Bounds Normalizer
# ═══════════════════════════════════════════════════════════════════════════


class GlobalBoundsNormalizer:
    """Tensor-level global bounding box normalizer for VAT position data.

    Computes the GLOBAL min/max across ALL frames and ALL vertices in a
    single O(N) pass using NumPy vectorised operations.  This prevents
    the catastrophic "scale pumping" artifact caused by per-frame
    normalization.

    Anti-Local-Bounds Trap Guard:
        The bounds are computed via ``np.min(positions, axis=(0, 1))``
        which collapses both the frame and vertex dimensions, yielding
        the absolute spatial extremes of the entire animation sequence.

    Parameters
    ----------
    positions : np.ndarray
        Shape ``[frames, vertices, C]`` where C is 2 or 3.
        Must be float32 or float64.
    """

    def __init__(self, positions: np.ndarray):
        pos = np.asarray(positions, dtype=np.float64)
        if pos.ndim != 3 or pos.shape[-1] not in (2, 3):
            raise ValueError(
                f"positions must have shape [frames, vertices, 2|3], "
                f"got {pos.shape}"
            )

        self._positions = pos
        self._channels = pos.shape[-1]

        # Global bounds: collapse frames AND vertices → (C,)
        self._global_min = pos.min(axis=(0, 1))  # (C,)
        self._global_max = pos.max(axis=(0, 1))  # (C,)
        self._extent = np.maximum(self._global_max - self._global_min, 1e-12)

    @property
    def global_min(self) -> np.ndarray:
        """Global minimum across all frames and vertices."""
        return self._global_min.copy()

    @property
    def global_max(self) -> np.ndarray:
        """Global maximum across all frames and vertices."""
        return self._global_max.copy()

    @property
    def extent(self) -> np.ndarray:
        """Global extent (max - min) per channel."""
        return self._extent.copy()

    def normalize(self) -> np.ndarray:
        """Normalize positions to [0, 1] using global bounds.

        Returns
        -------
        np.ndarray
            Shape ``[frames, vertices, C]``, dtype float32.
            All values in [0, 1].
        """
        # Pure tensor operation — no Python loops
        normalized = (self._positions - self._global_min[None, None, :]) / \
                     self._extent[None, None, :]
        normalized = np.clip(normalized, 0.0, 1.0)
        return normalized.astype(np.float32)

    def denormalize(self, normalized: np.ndarray) -> np.ndarray:
        """Denormalize positions from [0, 1] back to world space.

        This is the inverse of ``normalize()`` and corresponds to the
        shader-side decode formula:
            worldPos = texColor * extent + global_min

        Parameters
        ----------
        normalized : np.ndarray
            Shape ``[frames, vertices, C]``, values in [0, 1].

        Returns
        -------
        np.ndarray
            Reconstructed world-space positions.
        """
        norm = np.asarray(normalized, dtype=np.float64)
        return (norm * self._extent[None, None, :] +
                self._global_min[None, None, :])


# ═══════════════════════════════════════════════════════════════════════════
# HDR Texture Export (Radiance .hdr via cv2)
# ═══════════════════════════════════════════════════════════════════════════


def export_vat_hdr(
    normalized_positions: np.ndarray,
    output_path: str | Path,
) -> Path:
    """Export normalized VAT positions as Radiance HDR image.

    The texture layout is: row = vertex_index, col = frame_index.
    For 2-channel (XY) data, the third channel stores displacement
    magnitude for visual inspection.

    Anti-Precision-Loss Guard: NO 8-bit integer cast or 255-multiply anywhere.
    All data stays ``np.float32``.

    Parameters
    ----------
    normalized_positions : np.ndarray
        Shape ``[frames, vertices, C]`` with C=2 or C=3, values in [0, 1].
    output_path : str or Path
        Output file path (must end with ``.hdr``).

    Returns
    -------
    Path
        The written file path.
    """
    import cv2

    pos = np.asarray(normalized_positions, dtype=np.float32)
    frames, vertices, channels = pos.shape

    # Transpose to [vertices, frames, C] for texture layout
    # Row = vertex, Col = frame
    tex = np.transpose(pos, (1, 0, 2))  # (V, F, C)

    if channels == 2:
        # Pad to 3 channels: XY + displacement magnitude
        magnitude = np.linalg.norm(
            tex - tex[:, 0:1, :], axis=-1, keepdims=True
        )
        magnitude = np.clip(magnitude, 0.0, 1.0)
        tex = np.concatenate([tex, magnitude], axis=-1)
    elif channels > 3:
        tex = tex[..., :3]

    # cv2 expects BGR order for HDR
    tex_bgr = tex[..., ::-1].copy()

    path = Path(output_path)
    cv2.imwrite(str(path), tex_bgr.astype(np.float32))
    return path


# ═══════════════════════════════════════════════════════════════════════════
# Raw Float32 Binary Export (.npy) — Zero Precision Loss
# ═══════════════════════════════════════════════════════════════════════════


def export_vat_npy(
    normalized_positions: np.ndarray,
    output_path: str | Path,
) -> Path:
    """Export normalized VAT positions as raw float32 NumPy binary.

    This format has ZERO precision loss.  The RMSE between the saved
    and loaded data is exactly 0.0.

    Parameters
    ----------
    normalized_positions : np.ndarray
        Shape ``[frames, vertices, C]``, values in [0, 1].
    output_path : str or Path
        Output file path (must end with ``.npy``).

    Returns
    -------
    Path
        The written file path.
    """
    pos = np.asarray(normalized_positions, dtype=np.float32)
    # Transpose to [vertices, frames, C] for texture layout
    tex = np.transpose(pos, (1, 0, 2))
    path = Path(output_path)
    np.save(str(path), tex)
    return path


# ═══════════════════════════════════════════════════════════════════════════
# Hi-Lo 16-bit Packed PNG Export (Unity Compatible)
# ═══════════════════════════════════════════════════════════════════════════


def encode_hilo_16bit(
    normalized_positions: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Encode normalized [0,1] float positions into Hi-Lo 8-bit pair.

    This implements the Houdini VAT 3.0 "Split Positions into Two Textures"
    approach for engines that don't support HDR textures natively.

    Encoding:
        value_16bit = round(value * 65535)
        hi_byte = value_16bit >> 8
        lo_byte = value_16bit & 0xFF

    Decode in shader:
        value = (hi_byte * 256.0 + lo_byte) / 65535.0

    Precision: 1/65535 ≈ 1.5e-5 (well within RMSE < 1e-4 requirement).

    Parameters
    ----------
    normalized_positions : np.ndarray
        Shape ``[frames, vertices, C]``, values in [0, 1].

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        (hi_texture, lo_texture) each of shape ``[vertices, frames, C]``
        with dtype uint8.
    """
    pos = np.asarray(normalized_positions, dtype=np.float64)
    # Transpose to texture layout: [V, F, C]
    tex = np.transpose(pos, (1, 0, 2))

    # Quantize to 16-bit
    quantized = np.round(tex * 65535.0).astype(np.uint16)
    quantized = np.clip(quantized, 0, 65535)

    hi = (quantized >> 8).astype(np.uint8)
    lo = (quantized & 0xFF).astype(np.uint8)

    return hi, lo


def decode_hilo_16bit(
    hi: np.ndarray,
    lo: np.ndarray,
) -> np.ndarray:
    """Decode Hi-Lo 8-bit pair back to normalized float positions.

    Parameters
    ----------
    hi, lo : np.ndarray
        Shape ``[vertices, frames, C]``, dtype uint8.

    Returns
    -------
    np.ndarray
        Reconstructed normalized positions, dtype float64.
    """
    h = hi.astype(np.float64)
    l = lo.astype(np.float64)
    return (h * 256.0 + l) / 65535.0


def export_vat_hilo_png(
    normalized_positions: np.ndarray,
    hi_path: str | Path,
    lo_path: str | Path,
) -> tuple[Path, Path]:
    """Export Hi-Lo packed PNG pair for Unity.

    For 2-channel (XY) data, pads to RGB (third channel = 0).

    Parameters
    ----------
    normalized_positions : np.ndarray
        Shape ``[frames, vertices, C]``, values in [0, 1].
    hi_path, lo_path : str or Path
        Output file paths.

    Returns
    -------
    tuple[Path, Path]
        (hi_path, lo_path) of written files.
    """
    from PIL import Image

    hi, lo = encode_hilo_16bit(normalized_positions)

    # Pad to 3 channels if needed
    if hi.shape[-1] == 2:
        pad = np.zeros((*hi.shape[:-1], 1), dtype=np.uint8)
        hi = np.concatenate([hi, pad], axis=-1)
        lo = np.concatenate([lo, pad], axis=-1)

    hi_img = Image.fromarray(hi[..., :3], mode="RGB")
    lo_img = Image.fromarray(lo[..., :3], mode="RGB")

    hp = Path(hi_path)
    lp = Path(lo_path)
    hi_img.save(hp)
    lo_img.save(lp)
    return hp, lp


# ═══════════════════════════════════════════════════════════════════════════
# Unity URP Shader (HLSL) for High-Precision VAT
# ═══════════════════════════════════════════════════════════════════════════


UNITY_HIGH_PRECISION_VAT_SHADER = r'''Shader "MathArt/HighPrecisionVATLit"
{
    Properties
    {
        _MainTex ("Sprite Texture", 2D) = "white" {}
        _Color ("Tint", Color) = (1,1,1,1)

        [Header(VAT High Precision)]
        _VATPositionHi ("VAT Position Hi (RGB)", 2D) = "black" {}
        _VATPositionLo ("VAT Position Lo (RGB)", 2D) = "black" {}
        _VatFrameCount ("Frame Count", Float) = 1
        _VatFrame ("Current Frame (auto)", Float) = 0
        _VatBoundsMin ("Bounds Min (XYZ)", Vector) = (0,0,0,0)
        _VatBoundsMax ("Bounds Max (XYZ)", Vector) = (1,1,1,0)
        _DisplacementStrength ("Displacement Strength", Float) = 1.0
    }

    SubShader
    {
        Tags
        {
            "RenderType" = "Transparent"
            "Queue" = "Transparent"
            "RenderPipeline" = "UniversalPipeline"
        }

        Pass
        {
            Name "VATLitForward"
            Tags { "LightMode" = "Universal2D" }

            Blend SrcAlpha OneMinusSrcAlpha
            ZWrite Off
            Cull Off

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                float2 uv : TEXCOORD0;
                float2 vatLookup : TEXCOORD1; // x = vertexIndex/vertexCount
            };

            struct Varyings
            {
                float4 positionCS : SV_POSITION;
                float2 uv : TEXCOORD0;
            };

            TEXTURE2D(_MainTex);
            SAMPLER(sampler_MainTex);
            TEXTURE2D(_VATPositionHi);
            SAMPLER(sampler_VATPositionHi);
            TEXTURE2D(_VATPositionLo);
            SAMPLER(sampler_VATPositionLo);

            CBUFFER_START(UnityPerMaterial)
                float4 _MainTex_ST;
                float4 _Color;
                float _VatFrameCount;
                float _VatFrame;
                float4 _VatBoundsMin;
                float4 _VatBoundsMax;
                float _DisplacementStrength;
            CBUFFER_END

            // ── Hi-Lo 16-bit Decode ──────────────────────────────
            // Reconstructs normalized [0,1] position from two 8-bit
            // textures using the formula:
            //   value = (hi * 256.0 + lo * 255.0) / 65535.0
            // This matches the Python-side encode_hilo_16bit().
            // ─────────────────────────────────────────────────────
            float3 DecodeHiLo(float3 hi, float3 lo)
            {
                return (hi * 255.0 * 256.0 + lo * 255.0) / 65535.0;
            }

            // ── Global Bounds Denormalize ────────────────────────
            // worldPos = normalized * (boundsMax - boundsMin) + boundsMin
            // This is the standard VAT decode using GLOBAL bounds.
            // NEVER use per-frame bounds — that causes scale pumping.
            // ─────────────────────────────────────────────────────
            float3 DenormalizePosition(float3 normalized)
            {
                float3 extent = _VatBoundsMax.xyz - _VatBoundsMin.xyz;
                return normalized * extent + _VatBoundsMin.xyz;
            }

            Varyings vert(Attributes IN)
            {
                Varyings OUT;

                // ── VAT Sampling ─────────────────────────────────
                // CRITICAL: Use Point filtering, no mipmaps, no sRGB.
                // The texture stores mathematical data, not visual color.
                // ─────────────────────────────────────────────────
                float vertexU = IN.vatLookup.x;
                float frameV = (_VatFrame + 0.5) / max(_VatFrameCount, 1.0);

                float2 vatUV = float2(vertexU, frameV);

                // Sample Hi and Lo textures with Point filter
                float3 hi = SAMPLE_TEXTURE2D_LOD(
                    _VATPositionHi, sampler_VATPositionHi, vatUV, 0).rgb;
                float3 lo = SAMPLE_TEXTURE2D_LOD(
                    _VATPositionLo, sampler_VATPositionLo, vatUV, 0).rgb;

                // Decode 16-bit precision from Hi-Lo pair
                float3 normalizedPos = DecodeHiLo(hi, lo);

                // Denormalize using GLOBAL bounding box
                float3 worldOffset = DenormalizePosition(normalizedPos);

                // Apply displacement
                float3 displaced = IN.positionOS.xyz +
                    worldOffset * _DisplacementStrength;

                OUT.positionCS = TransformObjectToHClip(displaced);
                OUT.uv = TRANSFORM_TEX(IN.uv, _MainTex);
                return OUT;
            }

            half4 frag(Varyings IN) : SV_Target
            {
                half4 color = SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, IN.uv);
                return color * _Color;
            }
            ENDHLSL
        }
    }
    FallBack "Universal Render Pipeline/Lit"
}
'''


# ═══════════════════════════════════════════════════════════════════════════
# Unity Material Preset JSON
# ═══════════════════════════════════════════════════════════════════════════


def generate_unity_material_preset(
    manifest: HighPrecisionVATManifest,
) -> dict[str, Any]:
    """Generate Unity material preset JSON for high-precision VAT.

    This preset includes all import settings required for correct
    VAT playback, enforcing the Unity Texture Importer Discipline:
    - sRGB = False (Linear space)
    - Filter = Point (no interpolation)
    - Compression = None
    - Generate Mip Maps = False

    Parameters
    ----------
    manifest : HighPrecisionVATManifest
        The VAT manifest.

    Returns
    -------
    dict
        Unity material preset configuration.
    """
    return {
        "shader": "MathArt/HighPrecisionVATLit",
        "properties": {
            "_VatFrameCount": manifest.frame_count,
            "_VatBoundsMin": manifest.bounds_min + [0.0] * (4 - len(manifest.bounds_min)),
            "_VatBoundsMax": manifest.bounds_max + [0.0] * (4 - len(manifest.bounds_max)),
            "_DisplacementStrength": 1.0,
        },
        "texture_import_settings": {
            "_VATPositionHi": {
                "sRGB": False,
                "filterMode": "Point",
                "textureCompression": "Uncompressed",
                "generateMipMaps": False,
                "readWriteEnabled": True,
                "maxTextureSize": max(manifest.texture_width, manifest.texture_height),
                "wrapMode": "Clamp",
                "npotScale": "None",
                "textureType": "Default",
                "notes": "CRITICAL: sRGB MUST be False. Any gamma correction destroys position data.",
            },
            "_VATPositionLo": {
                "sRGB": False,
                "filterMode": "Point",
                "textureCompression": "Uncompressed",
                "generateMipMaps": False,
                "readWriteEnabled": True,
                "maxTextureSize": max(manifest.texture_width, manifest.texture_height),
                "wrapMode": "Clamp",
                "npotScale": "None",
                "textureType": "Default",
                "notes": "CRITICAL: sRGB MUST be False. Any gamma correction destroys position data.",
            },
        },
        "playback": {
            "fps": manifest.fps,
            "frame_count": manifest.frame_count,
            "loop": True,
        },
        "decode_formula": manifest.decode_formula,
        "research_provenance": manifest.research_provenance,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Main Bake Pipeline
# ═══════════════════════════════════════════════════════════════════════════


def bake_high_precision_vat(
    positions: np.ndarray,
    output_dir: str | Path,
    config: Optional[HighPrecisionVATConfig] = None,
) -> HighPrecisionVATResult:
    """Bake high-precision VAT from a vertex animation sequence.

    This is the main entry point for the P1-VAT-PRECISION-1 pipeline.
    It takes a ``[frames, vertices, C]`` position tensor and produces
    a complete asset bundle with:
    - Raw float32 binary (.npy) — zero precision loss
    - Radiance HDR (.hdr) — visual inspection
    - Hi-Lo packed PNG pair — Unity compatible, 16-bit precision
    - Strong-typed JSON manifest with global bounds
    - Unity URP Shader (HLSL)
    - Unity Material Preset JSON

    Anti-Red-Line Guards:
    - NO ``np.uint8`` or ``* 255`` in the float export path
    - Global bounds via ``np.min(positions, axis=(0, 1))``
    - Only ``cv2`` and ``numpy`` — no C++ dependencies

    Parameters
    ----------
    positions : np.ndarray
        Shape ``[frames, vertices, C]`` where C is 2 or 3.
        Can be float32 or float64.
    output_dir : str or Path
        Output directory for all assets.
    config : HighPrecisionVATConfig, optional
        Bake configuration.

    Returns
    -------
    HighPrecisionVATResult
        Complete bake result with all file paths and manifest.
    """
    cfg = config or HighPrecisionVATConfig()
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    pos = np.asarray(positions, dtype=np.float64)
    if pos.ndim != 3 or pos.shape[-1] not in (2, 3):
        raise ValueError(
            f"positions must have shape [frames, vertices, 2|3], got {pos.shape}"
        )

    frames, vertices, channels = pos.shape

    # ── Step 1: Global Bounds Normalization ──────────────────
    normalizer = GlobalBoundsNormalizer(pos)
    normalized = normalizer.normalize()  # float32, [0, 1]

    # ── Step 2: Export raw float32 binary ────────────────────
    npy_path = None
    if cfg.export_npy:
        npy_path = export_vat_npy(normalized, out / "vat_position_float32.npy")

    # ── Step 3: Export Radiance HDR ──────────────────────────
    hdr_path = None
    if cfg.export_hdr:
        hdr_path = export_vat_hdr(normalized, out / "vat_position.hdr")

    # ── Step 4: Export Hi-Lo packed PNGs ─────────────────────
    hilo_hi_path = None
    hilo_lo_path = None
    if cfg.export_hilo_png:
        hilo_hi_path, hilo_lo_path = export_vat_hilo_png(
            normalized,
            out / "vat_position_hi.png",
            out / "vat_position_lo.png",
        )

    # ── Step 5: Export Unity Shader ──────────────────────────
    shader_path = out / "HighPrecisionVATLit.shader"
    shader_path.write_text(UNITY_HIGH_PRECISION_VAT_SHADER, encoding="utf-8")

    # ── Step 6: Build manifest ───────────────────────────────
    gmin = normalizer.global_min
    gmax = normalizer.global_max
    ext = normalizer.extent

    manifest = HighPrecisionVATManifest(
        name=cfg.asset_name,
        frame_count=int(frames),
        vertex_count=int(vertices),
        texture_width=int(frames),
        texture_height=int(vertices),
        fps=int(cfg.fps),
        bounds_min=[float(x) for x in gmin],
        bounds_max=[float(x) for x in gmax],
        bounds_extent=[float(x) for x in ext],
        precision="float32",
        encoding="global_bounds_normalized",
        channels={
            "npy": "vat_position_float32.npy" if npy_path else "",
            "hdr": "vat_position.hdr" if hdr_path else "",
            "hilo_hi": "vat_position_hi.png" if hilo_hi_path else "",
            "hilo_lo": "vat_position_lo.png" if hilo_lo_path else "",
        },
        unity_import_settings={
            "sRGB": False,
            "filterMode": "Point",
            "textureCompression": "Uncompressed",
            "generateMipMaps": False,
        },
        source_backend="high_precision_vat",
        research_provenance=[
            "SideFX Houdini VAT 3.0 — HDR position texture specification",
            "Global Bounding Box Quantization — anti-scale-pumping normalization",
            "Unity Texture Importer Discipline — sRGB=False, Filter=Point, Compression=None",
            "Houdini VAT 3.0 Split Positions into Two Textures — Hi-Lo 16-bit packing",
        ],
    )

    manifest_path = out / "vat_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # ── Step 7: Generate Unity Material Preset ───────────────
    material_preset = generate_unity_material_preset(manifest)
    material_preset_path = out / "vat_material_preset.json"
    material_preset_path.write_text(
        json.dumps(material_preset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # ── Step 8: Visual preview ───────────────────────────────
    preview_path = None
    if cfg.include_preview:
        from PIL import Image
        preview = _build_vat_preview(normalized)
        preview_path = out / "vat_preview.png"
        preview.save(preview_path)

    # ── Diagnostics ──────────────────────────────────────────
    diagnostics = {
        "global_bounds_min": [float(x) for x in gmin],
        "global_bounds_max": [float(x) for x in gmax],
        "global_extent": [float(x) for x in ext],
        "normalized_range": [float(normalized.min()), float(normalized.max())],
        "frame_count": int(frames),
        "vertex_count": int(vertices),
        "channels": int(channels),
    }

    # Verify Hi-Lo round-trip precision
    if cfg.export_hilo_png:
        hi, lo = encode_hilo_16bit(normalized)
        decoded = decode_hilo_16bit(hi, lo)
        # Compare in texture layout [V, F, C]
        tex_normalized = np.transpose(normalized.astype(np.float64), (1, 0, 2))
        rmse = float(np.sqrt(np.mean((decoded[..., :channels] - tex_normalized) ** 2)))
        diagnostics["hilo_roundtrip_rmse"] = rmse
        diagnostics["hilo_roundtrip_max_error"] = float(
            np.max(np.abs(decoded[..., :channels] - tex_normalized))
        )

    return HighPrecisionVATResult(
        output_dir=out,
        manifest_path=manifest_path,
        npy_path=npy_path,
        hdr_path=hdr_path,
        hilo_hi_path=hilo_hi_path,
        hilo_lo_path=hilo_lo_path,
        preview_path=preview_path,
        manifest=manifest,
        shader_path=shader_path,
        material_preset_path=material_preset_path,
        diagnostics=diagnostics,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Preview Generator
# ═══════════════════════════════════════════════════════════════════════════


def _build_vat_preview(
    normalized: np.ndarray,
    size: int = 256,
) -> "Image.Image":
    """Build a visual trajectory preview from normalized positions.

    Uses vectorised scatter for performance (no Python for-loops in
    the hot path for position mapping).
    """
    from PIL import Image

    frames, vertices, channels = normalized.shape
    canvas = np.zeros((size, size, 4), dtype=np.uint8)

    # Vectorised scatter: compute pixel coordinates for all points
    palette = np.linspace(64, 255, frames, dtype=np.uint8)

    for f_idx in range(frames):
        frame = normalized[f_idx]  # (V, C)
        px = np.clip((frame[:, 0] * (size - 1)).astype(int), 0, size - 1)
        py = np.clip(((1.0 - frame[:, 1]) * (size - 1)).astype(int), 0, size - 1)
        color = palette[f_idx]
        canvas[py, px] = [255, color, 96, 255]

    return Image.fromarray(canvas, mode="RGBA")


# ═══════════════════════════════════════════════════════════════════════════
# Three-Layer Evolution Bridge
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class VATEvolutionMetrics:
    """Metrics for three-layer evolution evaluation of VAT precision.

    Layer 1 — Internal Evolution Gate:
        Reject bakes with precision loss exceeding threshold.
    Layer 2 — External Knowledge Distillation:
        Persist optimal encoding parameters and bounds strategies.
    Layer 3 — Self-Iterative Testing:
        Track precision trends over iterations.
    """

    hilo_rmse: float = 0.0
    hilo_max_error: float = 0.0
    npy_rmse: float = 0.0
    global_bounds_valid: bool = False
    precision_pass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "hilo_rmse": self.hilo_rmse,
            "hilo_max_error": self.hilo_max_error,
            "npy_rmse": self.npy_rmse,
            "global_bounds_valid": self.global_bounds_valid,
            "precision_pass": self.precision_pass,
        }


def evaluate_vat_precision(
    result: HighPrecisionVATResult,
    original_positions: np.ndarray,
    rmse_threshold: float = 1e-4,
) -> VATEvolutionMetrics:
    """Evaluate VAT bake precision for the evolution bridge.

    Parameters
    ----------
    result : HighPrecisionVATResult
        The bake result.
    original_positions : np.ndarray
        The original float64 positions.
    rmse_threshold : float
        Maximum acceptable RMSE.

    Returns
    -------
    VATEvolutionMetrics
        Evaluation metrics.
    """
    pos = np.asarray(original_positions, dtype=np.float64)
    normalizer = GlobalBoundsNormalizer(pos)
    normalized = normalizer.normalize()

    # NPY round-trip (should be exactly zero)
    npy_rmse = 0.0
    if result.npy_path and result.npy_path.exists():
        loaded = np.load(str(result.npy_path))
        # loaded is [V, F, C], transpose back to [F, V, C]
        loaded_fvc = np.transpose(loaded, (1, 0, 2))
        reconstructed = normalizer.denormalize(loaded_fvc)
        npy_rmse = float(np.sqrt(np.mean((reconstructed - pos) ** 2)))

    # Hi-Lo round-trip
    hilo_rmse = result.diagnostics.get("hilo_roundtrip_rmse", float("inf"))
    hilo_max = result.diagnostics.get("hilo_roundtrip_max_error", float("inf"))

    # Validate global bounds
    bounds_valid = (
        len(result.manifest.bounds_min) == pos.shape[-1]
        and len(result.manifest.bounds_max) == pos.shape[-1]
        and all(
            result.manifest.bounds_min[i] <= result.manifest.bounds_max[i]
            for i in range(pos.shape[-1])
        )
    )

    precision_pass = (
        hilo_rmse < rmse_threshold
        and npy_rmse < 1e-5  # float32 save + float64 denormalize introduces ~1e-7 error
        and bounds_valid
    )

    return VATEvolutionMetrics(
        hilo_rmse=hilo_rmse,
        hilo_max_error=hilo_max,
        npy_rmse=npy_rmse,
        global_bounds_valid=bounds_valid,
        precision_pass=precision_pass,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

__all__ = [
    "HighPrecisionVATConfig",
    "HighPrecisionVATManifest",
    "HighPrecisionVATResult",
    "GlobalBoundsNormalizer",
    "encode_hilo_16bit",
    "decode_hilo_16bit",
    "export_vat_hdr",
    "export_vat_npy",
    "export_vat_hilo_png",
    "bake_high_precision_vat",
    "generate_unity_material_preset",
    "VATEvolutionMetrics",
    "evaluate_vat_precision",
    "UNITY_HIGH_PRECISION_VAT_SHADER",
]
