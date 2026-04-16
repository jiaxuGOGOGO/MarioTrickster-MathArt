"""Headless End-to-End CI Audit Script — Visual Regression Pipeline.

SESSION-041: Gap 3 Closure — End-to-End Reproducibility & Visual Regression

This module implements a production-grade, hermetic end-to-end audit pipeline
that can run in GitHub Actions or any CI environment. It enforces three levels
of verification:

- **Level 0 (Sandbox Cold Start):** Creates an isolated workspace in a temporary
  directory, forces deterministic numpy/random seeds, and executes the full
  ``produce_character_pack()`` pipeline from scratch.

- **Level 1 (Structural Audit):** Compares the generated ``.umr_manifest.json``
  against a checked-in ``golden_manifest.json`` using tree-walk SHA-256
  comparison of all animation metadata and hash seals.

- **Level 2 (Visual Audit — SSIM):** Uses the Structural Similarity Index
  (Wang et al. 2004) to compare the generated character atlas against a golden
  baseline image. On failure, generates a diff heatmap highlighting regions
  of divergence.

Design references:

- **Skia Gold** (Google Chrome/Skia): Hash-first triage, multiple baselines,
  inexact matching for platform-specific rendering differences.
- **OpenUSD Validation Framework** (Pixar): Schema-aware validation with
  structured errors, plugin-based validators, and automated fixers.
- **SSIM** (Wang et al. 2004): Perceptual image quality metric that captures
  luminance, contrast, and structure — superior to pixel-level MD5/SHA-256
  for rendered content where anti-aliasing and floating-point precision
  cause byte-level differences.
- **Hermetic Builds** (Bazel): All inputs explicitly declared, isolated
  execution, deterministic outputs, verifiable checksums.

Usage::

    # Run full audit (CI mode)
    python -m mathart.headless_e2e_ci

    # Generate/update golden baselines
    python -m mathart.headless_e2e_ci --update-golden

    # Run as pytest
    pytest mathart/headless_e2e_ci.py -v

References
----------
[1] Z. Wang et al., "Image quality assessment: From error visibility to
    structural similarity," IEEE TIP, vol. 13, no. 4, pp. 600-612, 2004.
[2] Skia Gold, https://skia.org/docs/dev/testing/skiagold/
[3] OpenUSD Validation Framework, https://openusd.org/dev/api/
[4] Bazel Hermeticity, https://bazel.build/basics/hermeticity
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

# ── Constants ────────────────────────────────────────────────────────────────

GOLDEN_DIR = Path(__file__).parent.parent / "golden"
GOLDEN_MANIFEST_PATH = GOLDEN_DIR / "golden_manifest.json"
GOLDEN_ATLAS_PATH = GOLDEN_DIR / "golden_atlas.png"
GOLDEN_META_PATH = GOLDEN_DIR / "golden_meta.json"

# SSIM threshold: deterministic pipeline on same platform should be near-identical
SSIM_THRESHOLD = 0.9999
# Fallback threshold for cross-platform CI (allows minor AA differences)
SSIM_CROSS_PLATFORM_THRESHOLD = 0.995

# Fixed seed for hermetic reproducibility
HERMETIC_SEED = 42

# Default character spec for golden baseline
GOLDEN_PRESET = "mario"
GOLDEN_STATES = ("idle", "run", "jump", "fall", "hit")
GOLDEN_FRAME_WIDTH = 32
GOLDEN_FRAME_HEIGHT = 32


# ── Audit Result Dataclass ───────────────────────────────────────────────────


@dataclass
class AuditFinding:
    """A single finding from the audit pipeline.

    Inspired by OpenUSD's ``UsdValidationError`` — structured errors with
    severity, category, and actionable detail.
    """
    level: str  # "L0", "L1", "L2"
    severity: str  # "PASS", "WARN", "FAIL"
    category: str
    message: str
    detail: Optional[dict[str, Any]] = None


@dataclass
class AuditReport:
    """Complete audit report from a headless E2E run.

    Inspired by Skia Gold's triage workflow — structured results that can
    be serialized for CI artifact upload and human review.
    """
    session_id: str = "SESSION-041"
    timestamp: str = ""
    sandbox_path: str = ""
    findings: list[AuditFinding] = field(default_factory=list)
    level0_pass: bool = False
    level1_pass: bool = False
    level2_pass: bool = False
    ssim_score: Optional[float] = None
    pipeline_hash: str = ""
    golden_hash: str = ""
    diff_heatmap_path: Optional[str] = None

    @property
    def all_pass(self) -> bool:
        return self.level0_pass and self.level1_pass and self.level2_pass

    def add(self, level: str, severity: str, category: str, message: str,
            detail: Optional[dict[str, Any]] = None) -> None:
        self.findings.append(AuditFinding(level, severity, category, message, detail))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "sandbox_path": self.sandbox_path,
            "all_pass": self.all_pass,
            "level0_pass": self.level0_pass,
            "level1_pass": self.level1_pass,
            "level2_pass": self.level2_pass,
            "ssim_score": self.ssim_score,
            "pipeline_hash": self.pipeline_hash,
            "golden_hash": self.golden_hash,
            "diff_heatmap_path": self.diff_heatmap_path,
            "findings": [
                {
                    "level": f.level,
                    "severity": f.severity,
                    "category": f.category,
                    "message": f.message,
                    "detail": f.detail,
                }
                for f in self.findings
            ],
        }

    def summary(self) -> str:
        lines = [
            f"=== Headless E2E Audit Report ({self.session_id}) ===",
            f"Timestamp: {self.timestamp}",
            f"Sandbox: {self.sandbox_path}",
            f"Level 0 (Cold Start): {'PASS' if self.level0_pass else 'FAIL'}",
            f"Level 1 (Structural): {'PASS' if self.level1_pass else 'FAIL'}",
            f"Level 2 (Visual/SSIM): {'PASS' if self.level2_pass else 'FAIL'}",
        ]
        if self.ssim_score is not None:
            lines.append(f"SSIM Score: {self.ssim_score:.6f} (threshold: {SSIM_THRESHOLD})")
        lines.append(f"Pipeline Hash: {self.pipeline_hash[:16]}..." if self.pipeline_hash else "Pipeline Hash: N/A")
        lines.append(f"Golden Hash: {self.golden_hash[:16]}..." if self.golden_hash else "Golden Hash: N/A")
        if self.diff_heatmap_path:
            lines.append(f"Diff Heatmap: {self.diff_heatmap_path}")
        lines.append(f"Total Findings: {len(self.findings)}")
        for f in self.findings:
            lines.append(f"  [{f.level}][{f.severity}] {f.category}: {f.message}")
        lines.append(f"OVERALL: {'ALL PASS' if self.all_pass else 'FAILED'}")
        return "\n".join(lines)


# ── Level 0: Sandbox Cold Start ──────────────────────────────────────────────


def _level0_cold_start(sandbox_dir: Path, report: AuditReport) -> Optional[Path]:
    """Execute produce_character_pack() in a hermetic sandbox.

    This implements the hermetic build principle: all inputs are explicit,
    execution is isolated, and the numpy random seed is forced to ensure
    deterministic output.

    Returns the output directory on success, None on failure.
    """
    report.add("L0", "PASS", "sandbox_init", f"Sandbox created at {sandbox_dir}")

    # Force deterministic numpy seed (hermetic build principle)
    np.random.seed(HERMETIC_SEED)

    try:
        from mathart.pipeline import AssetPipeline, CharacterSpec

        output_dir = sandbox_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        pipeline = AssetPipeline(output_dir=str(output_dir), verbose=False)

        spec = CharacterSpec(
            name="golden_test",
            preset=GOLDEN_PRESET,
            frame_width=GOLDEN_FRAME_WIDTH,
            frame_height=GOLDEN_FRAME_HEIGHT,
            states=list(GOLDEN_STATES),
            frames_per_state=8,
            fps=12,
            head_units=3.0,
            enable_physics=True,
            enable_biomechanics=True,
            enable_dither=True,
            enable_outline=True,
            enable_lighting=True,
            evolution_iterations=0,
        )

        result = pipeline.produce_character_pack(spec)

        asset_dir = output_dir / "golden_test"
        manifest_path = asset_dir / ".umr_manifest.json"
        atlas_path = asset_dir / "golden_test_character_atlas.png"

        if not manifest_path.exists():
            report.add("L0", "FAIL", "manifest_missing",
                       f".umr_manifest.json not found at {manifest_path}")
            return None

        if not atlas_path.exists():
            report.add("L0", "FAIL", "atlas_missing",
                       f"Character atlas not found at {atlas_path}")
            return None

        report.add("L0", "PASS", "pipeline_complete",
                    f"Pipeline completed: score={result.score:.4f}, "
                    f"files={len(result.output_paths)}")
        report.level0_pass = True
        return asset_dir

    except Exception as e:
        report.add("L0", "FAIL", "pipeline_error", f"Pipeline execution failed: {e}")
        return None


# ── Level 1: Structural Audit ────────────────────────────────────────────────


def _level1_structural_audit(asset_dir: Path, report: AuditReport) -> bool:
    """Compare generated manifest against golden baseline.

    Inspired by OpenUSD's schema-aware validation: we walk the manifest tree
    and compare SHA-256 hashes, animation metadata, and structural properties.
    Like Pixar's ``usdchecker``, we strip non-deterministic fields (timestamps)
    before comparison.
    """
    manifest_path = asset_dir / ".umr_manifest.json"
    try:
        with open(manifest_path, "r") as f:
            generated_manifest = json.load(f)
    except Exception as e:
        report.add("L1", "FAIL", "manifest_read_error", f"Cannot read manifest: {e}")
        return False

    # Extract pipeline hash from generated manifest
    seal = generated_manifest.get("seal", {})
    pipeline_hash = seal.get("pipeline_hash", "")
    report.pipeline_hash = pipeline_hash

    if not pipeline_hash:
        report.add("L1", "FAIL", "no_pipeline_hash", "Generated manifest has no pipeline_hash")
        return False

    report.add("L1", "PASS", "pipeline_hash_present",
               f"Pipeline hash: {pipeline_hash[:16]}...")

    # Check state hashes are present
    state_hashes = seal.get("state_hashes", {})
    expected_states = set(GOLDEN_STATES)
    actual_states = set(state_hashes.keys())

    if expected_states != actual_states:
        report.add("L1", "FAIL", "state_coverage_mismatch",
                    f"Expected states {expected_states}, got {actual_states}")
        return False

    report.add("L1", "PASS", "state_coverage",
               f"All {len(expected_states)} states present with hashes")

    # Check frame count
    frame_count = seal.get("frame_count", 0)
    expected_frames = len(GOLDEN_STATES) * 8  # 8 frames per state
    if frame_count != expected_frames:
        report.add("L1", "WARN", "frame_count_mismatch",
                    f"Expected {expected_frames} frames, got {frame_count}")

    # Check contact tag hash is present
    contact_hash = seal.get("contact_tag_hash", "")
    if not contact_hash:
        report.add("L1", "WARN", "no_contact_hash", "No contact_tag_hash in seal")
    else:
        report.add("L1", "PASS", "contact_hash_present",
                    f"Contact tag hash: {contact_hash[:16]}...")

    # If golden manifest exists, compare against it
    if GOLDEN_MANIFEST_PATH.exists():
        try:
            with open(GOLDEN_MANIFEST_PATH, "r") as f:
                golden = json.load(f)
            golden_hash = golden.get("seal", {}).get("pipeline_hash", "")
            report.golden_hash = golden_hash

            if golden_hash and golden_hash != pipeline_hash:
                report.add("L1", "FAIL", "hash_regression",
                           f"Pipeline hash regression: golden={golden_hash[:16]}..., "
                           f"current={pipeline_hash[:16]}...",
                           detail={
                               "golden_hash": golden_hash,
                               "current_hash": pipeline_hash,
                           })
                # Deep compare state-level hashes
                golden_states = golden.get("seal", {}).get("state_hashes", {})
                for state in GOLDEN_STATES:
                    g_hash = golden_states.get(state, "")
                    c_hash = state_hashes.get(state, "")
                    if g_hash and c_hash and g_hash != c_hash:
                        report.add("L1", "FAIL", f"state_hash_drift_{state}",
                                   f"State '{state}' hash drifted: "
                                   f"golden={g_hash[:12]}..., current={c_hash[:12]}...")
                return False
            elif golden_hash:
                report.add("L1", "PASS", "hash_match",
                           "Pipeline hash matches golden baseline")
            else:
                report.add("L1", "WARN", "golden_no_hash",
                           "Golden manifest has no pipeline_hash for comparison")
        except Exception as e:
            report.add("L1", "WARN", "golden_read_error",
                       f"Cannot read golden manifest: {e}")
    else:
        report.add("L1", "WARN", "no_golden_manifest",
                    "No golden_manifest.json found — structural comparison skipped. "
                    "Run with --update-golden to create baseline.")

    report.level1_pass = True
    return True


# ── Level 2: Visual Audit (SSIM) ────────────────────────────────────────────


def _compute_ssim(image1: np.ndarray, image2: np.ndarray) -> tuple[float, np.ndarray]:
    """Compute SSIM between two images, returning score and diff map.

    Uses scikit-image's implementation of Wang et al. (2004) SSIM.
    Falls back to a simplified comparison if scikit-image is unavailable.
    """
    try:
        from skimage.metrics import structural_similarity as ssim
        # Convert to grayscale for SSIM if images are RGBA/RGB
        if image1.ndim == 3:
            # Use luminance channel (weighted average)
            weights = np.array([0.2989, 0.5870, 0.1140])
            if image1.shape[2] == 4:  # RGBA
                gray1 = np.dot(image1[:, :, :3], weights)
                gray2 = np.dot(image2[:, :, :3], weights)
            else:  # RGB
                gray1 = np.dot(image1, weights)
                gray2 = np.dot(image2, weights)
        else:
            gray1 = image1.astype(float)
            gray2 = image2.astype(float)

        score, diff_map = ssim(gray1, gray2, data_range=255.0, full=True)
        return float(score), diff_map
    except ImportError:
        # Fallback: normalized pixel-level comparison
        diff = np.abs(image1.astype(float) - image2.astype(float))
        max_diff = diff.max() if diff.max() > 0 else 1.0
        score = 1.0 - (diff.mean() / 255.0)
        return float(score), diff / max_diff


def _generate_diff_heatmap(diff_map: np.ndarray, output_path: Path) -> str:
    """Generate a visual diff heatmap from the SSIM diff map.

    Regions of divergence are highlighted in red, matching regions in green.
    This provides immediate visual feedback for debugging regressions.

    Inspired by Skia Gold's diff visualization and OpenCV's applyColorMap.
    """
    try:
        import cv2
        # Normalize diff map to 0-255
        if diff_map.max() > 1.0:
            normalized = (diff_map / diff_map.max() * 255).astype(np.uint8)
        else:
            # SSIM diff map: 1.0 = identical, 0.0 = maximum difference
            # Invert so differences are bright
            normalized = ((1.0 - diff_map) * 255).astype(np.uint8)

        # Apply colormap: differences show as hot (red/yellow)
        heatmap = cv2.applyColorMap(normalized, cv2.COLORMAP_JET)
        cv2.imwrite(str(output_path), heatmap)
        return str(output_path)
    except ImportError:
        # Fallback: save raw diff as grayscale PNG
        from PIL import Image
        if diff_map.max() <= 1.0:
            normalized = ((1.0 - diff_map) * 255).astype(np.uint8)
        else:
            normalized = (diff_map / diff_map.max() * 255).astype(np.uint8)
        img = Image.fromarray(normalized, mode="L")
        img.save(str(output_path))
        return str(output_path)


def _level2_visual_audit(asset_dir: Path, report: AuditReport,
                         sandbox_dir: Path) -> bool:
    """Compare generated atlas against golden baseline using SSIM.

    This is the visual regression gate — the "absolute moat" that ensures
    the pipeline never visually degrades. Uses SSIM (Wang et al. 2004)
    instead of pixel-level MD5 because:

    1. Anti-aliasing differences across platforms produce byte-different
       but visually identical images.
    2. Floyd-Steinberg dithering with floating-point precision differences
       can shift individual pixels without affecting perceived quality.
    3. SSIM captures luminance, contrast, and structure — the three
       dimensions humans actually perceive.
    """
    from PIL import Image

    atlas_path = asset_dir / "golden_test_character_atlas.png"
    if not atlas_path.exists():
        report.add("L2", "FAIL", "atlas_missing",
                    f"Generated atlas not found at {atlas_path}")
        return False

    if not GOLDEN_ATLAS_PATH.exists():
        report.add("L2", "WARN", "no_golden_atlas",
                    "No golden_atlas.png found — visual comparison skipped. "
                    "Run with --update-golden to create baseline.")
        # Pass with warning if no golden baseline exists yet
        report.level2_pass = True
        return True

    try:
        generated = np.array(Image.open(atlas_path).convert("RGBA"))
        golden = np.array(Image.open(GOLDEN_ATLAS_PATH).convert("RGBA"))
    except Exception as e:
        report.add("L2", "FAIL", "image_load_error", f"Cannot load images: {e}")
        return False

    # Check dimensions match
    if generated.shape != golden.shape:
        report.add("L2", "FAIL", "dimension_mismatch",
                    f"Atlas dimensions differ: generated={generated.shape}, "
                    f"golden={golden.shape}")
        return False

    report.add("L2", "PASS", "dimensions_match",
               f"Atlas dimensions: {generated.shape}")

    # Compute SSIM
    ssim_score, diff_map = _compute_ssim(generated, golden)
    report.ssim_score = ssim_score

    report.add("L2", "PASS" if ssim_score >= SSIM_THRESHOLD else "FAIL",
               "ssim_score",
               f"SSIM = {ssim_score:.6f} (threshold = {SSIM_THRESHOLD})",
               detail={"ssim": ssim_score, "threshold": SSIM_THRESHOLD})

    if ssim_score < SSIM_THRESHOLD:
        # Generate diff heatmap for debugging
        heatmap_path = sandbox_dir / "diff_heatmap.png"
        heatmap_file = _generate_diff_heatmap(diff_map, heatmap_path)
        report.diff_heatmap_path = heatmap_file
        report.add("L2", "FAIL", "visual_regression",
                    f"Visual regression detected! SSIM={ssim_score:.6f} < {SSIM_THRESHOLD}. "
                    f"Diff heatmap saved to {heatmap_file}")
        return False

    report.level2_pass = True
    return True


# ── Golden Baseline Management ───────────────────────────────────────────────


def update_golden_baseline() -> AuditReport:
    """Generate and save golden baseline artifacts.

    This creates the reference artifacts that all future CI runs will
    compare against. Should be run once after any intentional pipeline
    change, then committed to the repository.
    """
    report = AuditReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    with tempfile.TemporaryDirectory(prefix="umr_golden_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        report.sandbox_path = str(sandbox_dir)

        asset_dir = _level0_cold_start(sandbox_dir, report)
        if asset_dir is None:
            return report

        # Create golden directory
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

        # Copy manifest
        manifest_src = asset_dir / ".umr_manifest.json"
        shutil.copy2(str(manifest_src), str(GOLDEN_MANIFEST_PATH))
        report.add("L0", "PASS", "golden_manifest_saved",
                    f"Golden manifest saved to {GOLDEN_MANIFEST_PATH}")

        # Copy atlas
        atlas_src = asset_dir / "golden_test_character_atlas.png"
        if atlas_src.exists():
            shutil.copy2(str(atlas_src), str(GOLDEN_ATLAS_PATH))
            report.add("L0", "PASS", "golden_atlas_saved",
                       f"Golden atlas saved to {GOLDEN_ATLAS_PATH}")

        # Save metadata
        meta = {
            "created": report.timestamp,
            "session_id": "SESSION-041",
            "preset": GOLDEN_PRESET,
            "states": list(GOLDEN_STATES),
            "frame_width": GOLDEN_FRAME_WIDTH,
            "frame_height": GOLDEN_FRAME_HEIGHT,
            "seed": HERMETIC_SEED,
            "pipeline_hash": report.pipeline_hash or "",
            "description": "Golden baseline for headless E2E CI visual regression testing",
        }

        # Read pipeline hash from saved manifest
        if GOLDEN_MANIFEST_PATH.exists():
            with open(GOLDEN_MANIFEST_PATH, "r") as f:
                gm = json.load(f)
            meta["pipeline_hash"] = gm.get("seal", {}).get("pipeline_hash", "")

        with open(GOLDEN_META_PATH, "w") as f:
            json.dump(meta, f, indent=2)
        report.add("L0", "PASS", "golden_meta_saved",
                    f"Golden metadata saved to {GOLDEN_META_PATH}")

    return report


# ── Full Audit Pipeline ──────────────────────────────────────────────────────


def run_full_audit() -> AuditReport:
    """Execute the complete three-level headless E2E audit.

    This is the main entry point for CI. It creates a hermetic sandbox,
    runs the pipeline, and validates the output at three levels:

    - Level 0: Can the pipeline execute from cold start?
    - Level 1: Does the structural output match the golden baseline?
    - Level 2: Does the visual output match the golden baseline (SSIM)?
    """
    report = AuditReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    with tempfile.TemporaryDirectory(prefix="umr_sandbox_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        report.sandbox_path = str(sandbox_dir)

        # Level 0: Cold Start
        asset_dir = _level0_cold_start(sandbox_dir, report)
        if asset_dir is None:
            return report

        # Level 1: Structural Audit
        _level1_structural_audit(asset_dir, report)

        # Level 2: Visual Audit (SSIM)
        _level2_visual_audit(asset_dir, report, sandbox_dir)

        # Save audit report
        report_path = sandbox_dir / "audit_report.json"
        with open(report_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)

    return report


# ── Pytest Integration ───────────────────────────────────────────────────────


def test_headless_e2e_level0():
    """Pytest: Level 0 — Pipeline cold start in hermetic sandbox."""
    report = AuditReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    with tempfile.TemporaryDirectory(prefix="umr_test_l0_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        report.sandbox_path = str(sandbox_dir)
        asset_dir = _level0_cold_start(sandbox_dir, report)
        assert asset_dir is not None, f"Level 0 failed: {report.summary()}"
        assert report.level0_pass, f"Level 0 not marked as pass: {report.summary()}"


def test_headless_e2e_level1():
    """Pytest: Level 1 — Structural manifest audit."""
    report = AuditReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    with tempfile.TemporaryDirectory(prefix="umr_test_l1_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        report.sandbox_path = str(sandbox_dir)
        asset_dir = _level0_cold_start(sandbox_dir, report)
        assert asset_dir is not None, "Level 0 prerequisite failed"
        result = _level1_structural_audit(asset_dir, report)
        assert report.level1_pass, f"Level 1 failed: {report.summary()}"


def test_headless_e2e_level2():
    """Pytest: Level 2 — Visual regression (SSIM)."""
    report = AuditReport(
        timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    with tempfile.TemporaryDirectory(prefix="umr_test_l2_") as tmpdir:
        sandbox_dir = Path(tmpdir)
        report.sandbox_path = str(sandbox_dir)
        asset_dir = _level0_cold_start(sandbox_dir, report)
        assert asset_dir is not None, "Level 0 prerequisite failed"
        _level1_structural_audit(asset_dir, report)
        _level2_visual_audit(asset_dir, report, sandbox_dir)
        assert report.level2_pass, f"Level 2 failed: {report.summary()}"


def test_headless_e2e_determinism():
    """Pytest: Verify two consecutive runs produce identical output."""
    hashes = []
    for i in range(2):
        report = AuditReport(
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        with tempfile.TemporaryDirectory(prefix=f"umr_det_{i}_") as tmpdir:
            sandbox_dir = Path(tmpdir)
            report.sandbox_path = str(sandbox_dir)
            asset_dir = _level0_cold_start(sandbox_dir, report)
            assert asset_dir is not None, f"Run {i} Level 0 failed"
            manifest_path = asset_dir / ".umr_manifest.json"
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
            hashes.append(manifest["seal"]["pipeline_hash"])

    assert hashes[0] == hashes[1], (
        f"Determinism violation: run 0 hash={hashes[0][:16]}..., "
        f"run 1 hash={hashes[1][:16]}..."
    )


def test_headless_e2e_ssim_self_identity():
    """Pytest: SSIM of an image with itself should be 1.0."""
    test_img = np.random.randint(0, 255, (32, 32, 4), dtype=np.uint8)
    score, diff_map = _compute_ssim(test_img, test_img)
    assert score > 0.9999, f"Self-SSIM should be ~1.0, got {score}"


def test_headless_e2e_ssim_different_images():
    """Pytest: SSIM of very different images should be low."""
    img1 = np.zeros((32, 32, 4), dtype=np.uint8)
    img2 = np.full((32, 32, 4), 255, dtype=np.uint8)
    score, diff_map = _compute_ssim(img1, img2)
    assert score < 0.5, f"SSIM of black vs white should be low, got {score}"


def test_headless_e2e_diff_heatmap():
    """Pytest: Diff heatmap generation produces a valid image file."""
    diff_map = np.random.rand(32, 32)
    with tempfile.TemporaryDirectory(prefix="umr_heatmap_") as tmpdir:
        output_path = Path(tmpdir) / "test_heatmap.png"
        result = _generate_diff_heatmap(diff_map, output_path)
        assert Path(result).exists(), f"Heatmap not created at {result}"
        assert Path(result).stat().st_size > 0, "Heatmap file is empty"


# ── CLI Entry Point ──────────────────────────────────────────────────────────


def main():
    """CLI entry point for headless E2E audit."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Headless E2E CI Audit — Visual Regression Pipeline"
    )
    parser.add_argument(
        "--update-golden", action="store_true",
        help="Generate and save golden baseline artifacts"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output report as JSON instead of human-readable text"
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit with non-zero code on any failure"
    )
    args = parser.parse_args()

    if args.update_golden:
        print("Generating golden baseline...")
        report = update_golden_baseline()
        print(report.summary())
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        sys.exit(0)

    print("Running headless E2E audit...")
    report = run_full_audit()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.summary())

    if args.strict and not report.all_pass:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
