# SESSION-041 Research Synthesis: End-to-End Reproducibility & Visual Regression Pipeline

> **Gap 3 Closure: Hermetic Builds, Visual Regression Testing, and Production-Grade CI**

## 1. Research Protocol

This document synthesizes findings from the Deep Reading Protocol applied to Gap 3 — the end-to-end reproducibility gap. The research covers four industrial pillars:

1. **Skia Gold** (Google Chrome/Skia rendering team)
2. **OpenUSD Validation Framework** (Pixar)
3. **SSIM — Structural Similarity Index** (Wang et al. 2004)
4. **Hermetic Builds** (Bazel, reproducible-builds.org)

---

## 2. Skia Gold — Industrial Visual Regression at Scale

### Architecture

Skia Gold is an image diff service developed by the Skia team at Google. It was originally built for Skia's internal usage and later adopted by Chromium, PDFium, and Flutter.

**Key architectural decisions:**

- **Hash-first triage**: Gold computes the hash of every produced image. If the hash matches an approved baseline, the test passes immediately without any pixel comparison. This is the fast path.
- **External diff service**: Unlike local image comparison tools, Gold runs comparisons on a GCE server, not on the testing machine. This decouples test execution from triage.
- **Multiple baselines per test**: Gold supports multiple approved images per test case. This handles legitimate platform-specific rendering differences (e.g., different GPU drivers producing slightly different anti-aliasing).
- **Inexact matching**: For tests prone to noise, Gold supports fuzzy matching with configurable parameters (Sobel filter, maximum differing pixels, per-channel thresholds).
- **Triage workflow**: New untriaged images appear in a web GUI. A human approves or rejects them. Approved images are added to the baseline set.

### Lessons for UMR Pipeline

| Skia Gold Concept | UMR Adaptation |
|---|---|
| Hash-first fast path | Already implemented via `ManifestSeal.pipeline_hash` |
| Multiple baselines per test | Not needed yet (deterministic pipeline should produce exactly one output) |
| Inexact matching | SSIM-based comparison for rendered atlas images |
| External diff service | Local pytest-based comparison (sufficient for our scale) |
| Triage GUI | Diff heatmap output as CI artifact for manual review |

---

## 3. OpenUSD Validation Framework — Schema-Aware Asset Validation

### Architecture

The OpenUSD Validation framework (v25.11+) provides a plugin-based system for validating assets against core rules, schema rules, and client-provided rules.

**Key concepts:**

- **UsdValidationValidator**: A single validation test that can produce zero or more named errors.
- **UsdValidationContext**: A collection of validators that run in parallel.
- **UsdValidationRegistry**: Central registry managing all validators, supporting lazy loading via plugins.
- **UsdValidationError**: Structured error with severity, sites, message, and optional fixer.
- **UsdValidationFixer**: Automated fix that can be applied to resolve specific errors.
- **Schema-aware traversal**: Validators can be associated with specific schema types and automatically include ancestor schema validators.

### `usddiff` Concept

While `usddiff` is not a standalone tool in the public USD toolset, the concept of "topology-aware diffing" is implemented through:

1. **`usdchecker`**: Validates USD files against evolving rules and metrics.
2. **Layer-level validation**: Checks composition arcs, references, and payloads.
3. **Prim-level validation**: Validates individual prims against their schema.
4. **Time-dependent validation**: Can validate across time intervals for animated assets.

### Lessons for UMR Pipeline

| USD Validation Concept | UMR Adaptation |
|---|---|
| Plugin-based validator registry | `VisualRegressionValidator` as a pluggable audit node |
| Schema-aware validation | Manifest schema validation (JSON structure checks) |
| Structured errors with severity | Audit report with categorized findings |
| Automated fixers | Golden baseline auto-update command |
| Parallel validation context | Multi-state parallel SSIM comparison |

---

## 4. SSIM — Structural Similarity Index

### Why Not Pixel-Level Comparison?

Pure pixel comparison (MD5/SHA-256 of image bytes, or pixel-by-pixel subtraction) fails for rendered content because:

1. **Anti-aliasing differences**: Different platforms produce slightly different sub-pixel rendering.
2. **Floating-point precision**: GPU vs CPU rendering paths may produce 1-bit differences in color channels.
3. **Dithering noise**: Floyd-Steinberg dithering with different random seeds produces visually identical but byte-different images.

### SSIM Formula

SSIM compares images using three components:

- **Luminance**: `l(x,y) = (2*μx*μy + C1) / (μx² + μy² + C1)`
- **Contrast**: `c(x,y) = (2*σx*σy + C2) / (σx² + σy² + C2)`
- **Structure**: `s(x,y) = (σxy + C3) / (σx*σy + C3)`

Combined: `SSIM(x,y) = l(x,y)^α * c(x,y)^β * s(x,y)^γ`

Where C1, C2, C3 are stabilization constants derived from the dynamic range.

### scikit-image Implementation

```python
from skimage.metrics import structural_similarity as ssim

score = ssim(image1, image2, data_range=image2.max() - image2.min())
# Returns: float in [-1, 1], where 1.0 = identical

# With full diff map:
score, diff_map = ssim(image1, image2, full=True, data_range=255)
# diff_map: per-pixel SSIM values for heatmap generation
```

### Threshold Selection

| Use Case | SSIM Threshold | Rationale |
|---|---|---|
| Pixel art (deterministic, same platform) | > 0.9999 | Near-identical expected |
| Cross-platform rendering | > 0.995 | Allow minor AA differences |
| Perceptual similarity | > 0.95 | Human-noticeable threshold |
| **UMR CI (same seed, same platform)** | **> 0.9999** | **Deterministic pipeline must be near-identical** |

---

## 5. Hermetic Builds — Isolation Principles

### Core Definition

> "A hermetic build is a reproducible software build where all inputs are explicitly declared and isolated so outputs depend only on those inputs." — Bazel documentation

### Key Principles

1. **Explicit inputs**: All dependencies, seeds, and configurations are declared.
2. **Isolated execution**: Build runs in a clean environment, not polluted by developer machine state.
3. **Deterministic outputs**: Same inputs always produce same outputs.
4. **Verifiable**: Outputs can be verified against expected checksums.

### Application to UMR CI

| Hermetic Principle | UMR Implementation |
|---|---|
| Explicit inputs | `UMR_Context` frozen dataclass with all parameters |
| Isolated execution | `/tmp/umr_sandbox` with clean state |
| Deterministic outputs | Fixed `numpy.random` seed, SHA-256 hash seal |
| Verifiable | `golden_manifest.json` + golden atlas comparison |

---

## 6. Implementation Blueprint: `headless_e2e_ci.py`

### Architecture

```
headless_e2e_ci.py
├── Level 0: Sandbox Cold Start
│   ├── Create /tmp/umr_sandbox
│   ├── Force numpy/random seed
│   └── Execute produce_character_pack()
├── Level 1: Structural Audit
│   ├── Compare .umr_manifest.json vs golden_manifest.json
│   ├── Tree-walk SHA-256 comparison
│   └── Animation metadata validation
├── Level 2: Visual Audit (SSIM)
│   ├── Load golden atlas PNG
│   ├── Load generated atlas PNG
│   ├── Compute SSIM score
│   ├── Assert SSIM > 0.9999
│   └── On failure: generate diff heatmap
└── Level 3: Evolution Integration
    ├── Feed audit results to ContractEvolutionBridge
    ├── Update knowledge rules on new patterns
    └── Track regression trends
```

### GitHub Actions Integration

The CI workflow should add a dedicated `visual-regression` job that:

1. Installs `scikit-image` and `opencv-python-headless` as CI-only dependencies
2. Runs `headless_e2e_ci.py` in a clean sandbox
3. Uploads diff heatmaps as artifacts on failure
4. Blocks merge on any SSIM regression

---

## 7. References

1. Z. Wang, A. C. Bovik, H. R. Sheikh, E. P. Simoncelli, "Image quality assessment: From error visibility to structural similarity," IEEE TIP, vol. 13, no. 4, pp. 600-612, 2004.
2. Skia Gold documentation, https://skia.org/docs/dev/testing/skiagold/
3. Chromium GPU Pixel Testing with Gold, https://chromium.googlesource.com/chromium/src/+/lkgr/docs/gpu/gpu_pixel_testing_with_gold.md
4. OpenUSD Validation Framework, https://openusd.org/dev/api/md_pxr_usd_validation_usd_validation__r_e_a_d_m_e.html
5. Bazel Hermeticity, https://bazel.build/basics/hermeticity
6. scikit-image SSIM, https://scikit-image.org/docs/stable/api/skimage.metrics.html
