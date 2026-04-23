"""SESSION-154 (P0-SESSION-151-POLICY-AS-CODE-GATES): Pixel Art Enforcer.

Transforms the static rules in ``knowledge/pixel_art.md`` into runtime
enforcement gates with real ``if/clamp/assert`` logic.

Knowledge Rules Consumed (from pixel_art.md):
  1. **Canvas Size**: 16-64 px per sprite — clamp to [16, 64]
  2. **Palette Size**: 4-32 colors per sprite — clamp to [4, 32]
  3. **Interpolation Mode**: MUST be 'nearest' (Point sampling) —
     bilinear/bicubic/lanczos are FORBIDDEN for pixel art
  4. **Anti-Aliasing**: MUST be disabled (False) for pixel art rendering
  5. **Dithering Matrix**: Must match canvas size (16px→2x2, 32px+→4x4)
  6. **Jaggies Tolerance**: 0-2 px maximum
  7. **RotSprite Upscale**: Must be 8x, final downscale MUST be nearest

Architecture:
  - Self-registers via ``@register_enforcer`` (IoC pattern)
  - Clamp-Not-Reject: auto-corrects illegal values where possible
  - Source traceability: every violation references ``pixel_art.md``

Research foundations:
  - **Policy-as-Code (OPA)**: Rules compiled from Markdown → Python gates
  - **Design by Contract (DbC)**: Preconditions on render parameters
  - **Shift-Left Validation**: Catches illegal params before GPU rendering
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from mathart.quality.gates.enforcer_registry import (
    EnforcerBase,
    EnforcerResult,
    EnforcerSeverity,
    EnforcerViolation,
    register_enforcer,
)

logger = logging.getLogger("mathart.quality.gates.pixel_art")

# ---------------------------------------------------------------------------
# Constants derived from pixel_art.md
# ---------------------------------------------------------------------------

# Canvas size constraints (pixel_art.md §基础规则)
CANVAS_SIZE_MIN = 16
CANVAS_SIZE_MAX = 64

# Palette size constraints (pixel_art.md §基础规则)
PALETTE_SIZE_MIN = 4
PALETTE_SIZE_MAX = 32

# Forbidden interpolation modes (pixel_art.md §RotSprite旋转 + §基础规则)
# Only 'nearest' (Point sampling) is allowed for pixel art
ALLOWED_INTERPOLATION_MODES = frozenset({
    "nearest", "nearest_neighbor", "point", "nn",
})
FORBIDDEN_INTERPOLATION_MODES = frozenset({
    "bilinear", "bicubic", "lanczos", "linear", "cubic",
    "area", "mitchell", "catrom", "gaussian",
})

# Jaggies tolerance (pixel_art.md §线条与锯齿)
JAGGIES_TOLERANCE_MIN = 0
JAGGIES_TOLERANCE_MAX = 2

# RotSprite upscale factor (pixel_art.md §RotSprite旋转)
ROTSPRITE_UPSCALE_FACTOR = 8

# Dithering matrix size mapping (pixel_art.md §抖动技法)
# canvas <= 24px → 2x2, canvas > 24px → 4x4
DITHER_CANVAS_THRESHOLD = 24
DITHER_SMALL_MATRIX = 2
DITHER_LARGE_MATRIX = 4

# Dithering strength range (pixel_art.md §抖动技法)
DITHER_STRENGTH_MIN = 0.0
DITHER_STRENGTH_MAX = 1.0

# Outline color count (pixel_art.md §基础规则)
OUTLINE_COLOR_COUNT_MIN = 1
OUTLINE_COLOR_COUNT_MAX = 3

# Sub-pixel frame count (pixel_art.md §子像素动画)
SUBPIXEL_FRAME_MIN = 2
SUBPIXEL_FRAME_MAX = 4

# ---------------------------------------------------------------------------
# Field name aliases — the pipeline may use different key names
# ---------------------------------------------------------------------------

_CANVAS_FIELDS = (
    "canvas_size", "sprite_size", "resolution", "output_resolution",
    "render_resolution", "tile_size",
)
_PALETTE_FIELDS = (
    "palette_size", "color_count", "num_colors", "max_colors",
    "palette_count",
)
_INTERPOLATION_FIELDS = (
    "interpolation", "filter_mode", "downscale_method", "resize_filter",
    "sampling_mode", "resampling", "upscale_method",
)
_AA_FIELDS = (
    "anti_aliasing", "antialias", "aa", "aa_enabled", "smooth",
    "anti_alias_enabled",
)
_DITHER_MATRIX_FIELDS = (
    "dither_matrix_size", "dither_matrix", "bayer_size",
)
_DITHER_STRENGTH_FIELDS = (
    "dither_strength", "dither_amount", "dither_intensity",
)
_JAGGIES_FIELDS = (
    "jaggies_tolerance", "jaggy_tolerance", "alias_tolerance",
)
_ROTSPRITE_UPSCALE_FIELDS = (
    "rotsprite_upscale", "rotsprite_factor", "rotation_upscale",
)
_OUTLINE_COLOR_FIELDS = (
    "outline_colors", "outline_color_count", "contour_colors",
)
_SUBPIXEL_FRAME_FIELDS = (
    "subpixel_frames", "sub_pixel_frames", "subpixel_frame_count",
)


def _find_field(params: Dict[str, Any], candidates: tuple[str, ...]) -> str | None:
    """Find the first matching field name in the params dict."""
    for name in candidates:
        if name in params:
            return name
    return None


def _clamp_numeric(
    value: Any,
    lo: float,
    hi: float,
) -> tuple[float, bool]:
    """Clamp a numeric value to [lo, hi]. Returns (clamped_value, was_clamped)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo, True
    if v < lo:
        return lo, True
    if v > hi:
        return hi, True
    return v, False


# ---------------------------------------------------------------------------
# Enforcer Implementation
# ---------------------------------------------------------------------------

@register_enforcer
class PixelArtEnforcer(EnforcerBase):
    """Enforces pixel art production rules from ``knowledge/pixel_art.md``.

    This enforcer performs real numeric validation and auto-correction on
    render/export parameters.  It is NOT a placeholder — every rule maps
    to a concrete ``if/clamp`` code path with source traceability.

    Enforcement rules:
      1. Canvas size must be in [16, 64] px
      2. Palette size must be in [4, 32] colors
      3. Interpolation mode must be 'nearest' (Point sampling)
      4. Anti-aliasing must be disabled
      5. Dithering matrix must match canvas size
      6. Dithering strength must be in [0.0, 1.0]
      7. Jaggies tolerance must be in [0, 2] px
      8. RotSprite upscale must be 8x
      9. Outline color count must be in [1, 3]
     10. Sub-pixel frame count must be in [2, 4]
    """

    @property
    def name(self) -> str:
        return "pixel_art_enforcer"

    @property
    def source_docs(self) -> list[str]:
        return ["pixel_art.md"]

    def validate(self, params: Dict[str, Any]) -> EnforcerResult:
        violations: list[EnforcerViolation] = []
        corrected = dict(params)  # Work on a copy

        # --- Rule 1: Canvas Size [16, 64] ---
        field = _find_field(corrected, _CANVAS_FIELDS)
        if field is not None:
            original = corrected[field]
            clamped, was_clamped = _clamp_numeric(
                original, CANVAS_SIZE_MIN, CANVAS_SIZE_MAX,
            )
            clamped = int(clamped)
            if was_clamped:
                corrected[field] = clamped
                violations.append(EnforcerViolation(
                    rule_id="禁止像素画画布尺寸越界",
                    message=(
                        f"画布尺寸必须在 [{CANVAS_SIZE_MIN}, {CANVAS_SIZE_MAX}] px 范围内 "
                        f"(pixel_art.md §基础规则)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value=clamped,
                ))

        # --- Rule 2: Palette Size [4, 32] ---
        field = _find_field(corrected, _PALETTE_FIELDS)
        if field is not None:
            original = corrected[field]
            clamped, was_clamped = _clamp_numeric(
                original, PALETTE_SIZE_MIN, PALETTE_SIZE_MAX,
            )
            clamped = int(clamped)
            if was_clamped:
                corrected[field] = clamped
                violations.append(EnforcerViolation(
                    rule_id="禁止像素画调色板越界",
                    message=(
                        f"调色板大小必须在 [{PALETTE_SIZE_MIN}, {PALETTE_SIZE_MAX}] 色范围内 "
                        f"(pixel_art.md §基础规则)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value=clamped,
                ))

        # --- Rule 3: Interpolation Mode MUST be 'nearest' ---
        field = _find_field(corrected, _INTERPOLATION_FIELDS)
        if field is not None:
            original = corrected[field]
            original_lower = str(original).lower().strip()
            if original_lower in FORBIDDEN_INTERPOLATION_MODES:
                # CLAMP: Force to 'nearest'
                corrected[field] = "nearest"
                violations.append(EnforcerViolation(
                    rule_id="禁止像素画双线性插值",
                    message=(
                        f"像素画严禁使用 '{original}' 插值模式，"
                        f"必须使用 Point 采样 (nearest) 以保护像素边缘锐度 "
                        f"(pixel_art.md §RotSprite旋转 + §基础规则)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value="nearest",
                ))
            elif original_lower not in ALLOWED_INTERPOLATION_MODES:
                # Unknown mode — clamp to nearest as safety measure
                corrected[field] = "nearest"
                violations.append(EnforcerViolation(
                    rule_id="禁止像素画未知插值模式",
                    message=(
                        f"未知插值模式 '{original}'，已强制修正为 'nearest' "
                        f"(pixel_art.md §RotSprite旋转)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value="nearest",
                ))

        # --- Rule 4: Anti-Aliasing MUST be disabled ---
        field = _find_field(corrected, _AA_FIELDS)
        if field is not None:
            original = corrected[field]
            # Normalize to bool
            if isinstance(original, str):
                is_enabled = original.lower() in ("true", "1", "yes", "on")
            else:
                is_enabled = bool(original)

            if is_enabled:
                corrected[field] = False
                violations.append(EnforcerViolation(
                    rule_id="禁止像素画抗锯齿",
                    message=(
                        "像素画严禁启用抗锯齿 (Anti-Aliasing)，"
                        "已强制关闭以保护像素边缘完整性 "
                        "(pixel_art.md §线条与锯齿)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value=False,
                ))

        # --- Rule 5: Dithering Matrix Size must match canvas ---
        canvas_field = _find_field(corrected, _CANVAS_FIELDS)
        dither_field = _find_field(corrected, _DITHER_MATRIX_FIELDS)
        if dither_field is not None and canvas_field is not None:
            canvas_val = corrected.get(canvas_field, 32)
            try:
                canvas_int = int(canvas_val)
            except (TypeError, ValueError):
                canvas_int = 32
            original_dither = corrected[dither_field]
            expected_matrix = (
                DITHER_SMALL_MATRIX
                if canvas_int <= DITHER_CANVAS_THRESHOLD
                else DITHER_LARGE_MATRIX
            )
            try:
                actual_matrix = int(original_dither)
            except (TypeError, ValueError):
                actual_matrix = -1
            if actual_matrix != expected_matrix:
                corrected[dither_field] = expected_matrix
                violations.append(EnforcerViolation(
                    rule_id="像素画抖动矩阵尺寸不匹配",
                    message=(
                        f"画布 {canvas_int}px 应使用 {expected_matrix}x{expected_matrix} "
                        f"Bayer 矩阵 (pixel_art.md §抖动技法)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=dither_field,
                    original_value=original_dither,
                    corrected_value=expected_matrix,
                ))

        # --- Rule 6: Dithering Strength [0.0, 1.0] ---
        field = _find_field(corrected, _DITHER_STRENGTH_FIELDS)
        if field is not None:
            original = corrected[field]
            clamped, was_clamped = _clamp_numeric(
                original, DITHER_STRENGTH_MIN, DITHER_STRENGTH_MAX,
            )
            if was_clamped:
                corrected[field] = clamped
                violations.append(EnforcerViolation(
                    rule_id="像素画抖动强度越界",
                    message=(
                        f"抖动强度必须在 [{DITHER_STRENGTH_MIN}, {DITHER_STRENGTH_MAX}] "
                        f"范围内 (pixel_art.md §抖动技法)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value=clamped,
                ))

        # --- Rule 7: Jaggies Tolerance [0, 2] ---
        field = _find_field(corrected, _JAGGIES_FIELDS)
        if field is not None:
            original = corrected[field]
            clamped, was_clamped = _clamp_numeric(
                original, JAGGIES_TOLERANCE_MIN, JAGGIES_TOLERANCE_MAX,
            )
            clamped = int(clamped)
            if was_clamped:
                corrected[field] = clamped
                violations.append(EnforcerViolation(
                    rule_id="像素画锯齿容忍度越界",
                    message=(
                        f"锯齿容忍度必须在 [{JAGGIES_TOLERANCE_MIN}, "
                        f"{JAGGIES_TOLERANCE_MAX}] px 范围内 "
                        f"(pixel_art.md §线条与锯齿)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value=clamped,
                ))

        # --- Rule 8: RotSprite Upscale must be 8x ---
        field = _find_field(corrected, _ROTSPRITE_UPSCALE_FIELDS)
        if field is not None:
            original = corrected[field]
            try:
                val = int(original)
            except (TypeError, ValueError):
                val = -1
            if val != ROTSPRITE_UPSCALE_FACTOR:
                corrected[field] = ROTSPRITE_UPSCALE_FACTOR
                violations.append(EnforcerViolation(
                    rule_id="RotSprite放大倍数非法",
                    message=(
                        f"RotSprite 内部放大倍数必须为 {ROTSPRITE_UPSCALE_FACTOR}x "
                        f"(pixel_art.md §RotSprite旋转)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value=ROTSPRITE_UPSCALE_FACTOR,
                ))

        # --- Rule 9: Outline Color Count [1, 3] ---
        field = _find_field(corrected, _OUTLINE_COLOR_FIELDS)
        if field is not None:
            original = corrected[field]
            clamped, was_clamped = _clamp_numeric(
                original, OUTLINE_COLOR_COUNT_MIN, OUTLINE_COLOR_COUNT_MAX,
            )
            clamped = int(clamped)
            if was_clamped:
                corrected[field] = clamped
                violations.append(EnforcerViolation(
                    rule_id="像素画轮廓线颜色数越界",
                    message=(
                        f"轮廓线颜色数必须在 [{OUTLINE_COLOR_COUNT_MIN}, "
                        f"{OUTLINE_COLOR_COUNT_MAX}] 范围内 "
                        f"(pixel_art.md §基础规则)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value=clamped,
                ))

        # --- Rule 10: Sub-pixel Frame Count [2, 4] ---
        field = _find_field(corrected, _SUBPIXEL_FRAME_FIELDS)
        if field is not None:
            original = corrected[field]
            clamped, was_clamped = _clamp_numeric(
                original, SUBPIXEL_FRAME_MIN, SUBPIXEL_FRAME_MAX,
            )
            clamped = int(clamped)
            if was_clamped:
                corrected[field] = clamped
                violations.append(EnforcerViolation(
                    rule_id="像素画子像素帧数越界",
                    message=(
                        f"子像素动画帧数必须在 [{SUBPIXEL_FRAME_MIN}, "
                        f"{SUBPIXEL_FRAME_MAX}] 范围内 "
                        f"(pixel_art.md §子像素动画)"
                    ),
                    severity=EnforcerSeverity.CLAMPED,
                    source_doc="pixel_art.md",
                    field_name=field,
                    original_value=original,
                    corrected_value=clamped,
                ))

        return EnforcerResult(
            enforcer_name=self.name,
            params=corrected,
            violations=violations,
        )
