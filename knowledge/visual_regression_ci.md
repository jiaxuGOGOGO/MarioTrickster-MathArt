# Visual Regression CI Pipeline — Knowledge Rules

> SESSION-041: Gap 3 Closure — End-to-End Reproducibility & Visual Regression

## Hard Constraints

1. **Hermetic Sandbox**: Every CI audit run MUST execute in an isolated temporary directory (`/tmp/umr_sandbox_*`). No state from previous runs may leak. The numpy random seed MUST be forced to `42` before pipeline execution.

2. **Three-Level Audit**: All three levels MUST pass for a CI run to be considered successful:
   - Level 0: Pipeline cold start produces valid `.umr_manifest.json` and atlas PNG
   - Level 1: Structural manifest matches `golden_manifest.json` (SHA-256 hash comparison)
   - Level 2: Visual atlas matches `golden_atlas.png` (SSIM > 0.9999)

3. **SSIM Over Pixel Hash**: Visual comparison MUST use SSIM (Structural Similarity Index), NOT pixel-level MD5/SHA-256 of image bytes. Rationale: anti-aliasing, dithering, and floating-point precision differences produce byte-different but visually identical images across platforms.

4. **Diff Heatmap on Failure**: When SSIM drops below threshold, the audit MUST generate a JET-colormap diff heatmap and upload it as a CI artifact. This enables immediate visual diagnosis without reproducing the failure locally.

5. **Golden Baseline Versioning**: `golden/golden_manifest.json`, `golden/golden_atlas.png`, and `golden/golden_meta.json` MUST be checked into the repository. Any intentional pipeline change that alters output MUST update the golden baseline via `--update-golden`.

## Heuristics

6. **SSIM Threshold Tuning**: Use 0.9999 for same-platform deterministic runs. If cross-platform CI shows false positives, relax to 0.995 (the cross-platform threshold). Never go below 0.95 — that's the human-perceptible boundary.

7. **State-Level Hash Granularity**: When Level 1 detects a hash regression, the audit reports which specific animation states drifted. This narrows debugging to the affected state generator rather than the entire pipeline.

8. **Dual-Run Determinism Check**: The `test_headless_e2e_determinism` test runs the pipeline twice and asserts identical hashes. This catches non-deterministic code paths (e.g., dict ordering, timestamp leaks) that single-run tests miss.

## Soft Defaults

9. **Golden Preset**: Default golden baseline uses `mario` preset with 5 states (idle, run, jump, fall, hit), 32x32 frames, 8 frames per state, seed 42. This covers the most exercised code paths.

10. **CI Job Dependency**: The `visual-regression` job runs after the `test` job succeeds. This ensures basic correctness before expensive E2E execution.

11. **Artifact Upload**: Diff heatmaps are uploaded only on failure to minimize CI storage costs. Successful runs produce no artifacts.

## Industrial References

- **Skia Gold** (Google): Hash-first triage, multiple baselines, inexact matching
- **OpenUSD Validation** (Pixar): Schema-aware validation, structured errors, plugin validators
- **SSIM** (Wang et al. 2004): Perceptual image quality metric
- **Hermetic Builds** (Bazel): Explicit inputs, isolated execution, deterministic outputs
