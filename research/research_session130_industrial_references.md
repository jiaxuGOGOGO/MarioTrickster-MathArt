# SESSION-130 Industrial & Academic Reference Research

## 1. AnimateDiff / ControlNet Temporal Conditioning (Guo et al., 2023; SparseCtrl ECCV 2024)

**Core Insight**: Video diffusion models (AnimateDiff, SparseCtrl) use temporal attention layers that learn inter-frame motion patterns from training data. The conditioning inputs (depth maps, normal maps, edge maps, RGB reference frames) must exhibit **real geometric variation** between frames for the temporal attention to produce coherent motion.

**Critical Finding from SparseCtrl (Guo et al., ECCV 2024)**:
- SparseCtrl introduces a **condition encoder** that processes sparse control signals while leaving the pre-trained T2V model untouched
- Even with sparse conditioning (only a few keyframes), the model interpolates motion between keyframes using learned temporal priors
- **When ALL conditioning frames are identical (static)**: The temporal attention receives zero gradient signal for motion — it cannot distinguish frame 0 from frame N. This causes:
  - **Mode Collapse**: The model generates near-identical frames with only noise-level variation
  - **Temporal Flickering**: Without consistent motion signal, the denoising process produces incoherent per-frame artifacts
  - **Loss of Identity Consistency**: The model's self-attention has no temporal anchor, leading to character appearance drift

**Implication for MarioTrickster-MathArt**: The factory's guide sequence MUST contain frames with real geometric displacement (bone-driven pose changes, camera-relative position shifts) — not synthetic micro-jitter on a static image. The temporal attention needs to "see" actual object motion in the conditioning to produce coherent animation.

## 2. GDC Data-Driven Animation Pipelines (Industry Standard)

**Core Insight**: Industrial animation pipelines (Pixar, EA, Naughty Dog) enforce strict **frame-by-frame data flow contracts** between pipeline stages. The fundamental principle:

> "Every frame that enters the rendering stage must have been independently computed by the animation stage. Frame duplication is a data integrity violation equivalent to cache poisoning."

**Key Principles**:
- **1:1 Frame Correspondence**: If the animation system produces N frames of bone transforms, the rendering system must process exactly N independent render passes. No frame may be duplicated, interpolated, or synthesized from fewer source frames without explicit annotation.
- **Temporal Metadata Propagation**: Each frame carries metadata (timestamp, frame_index, source_animation_clip, bone_transform_hash) that must be preserved through the pipeline. This enables downstream quality assurance to verify that each rendered frame corresponds to a unique animation state.
- **Fail-Fast on Frame Count Mismatch**: If upstream produces 30 frames but downstream receives 16, the pipeline must halt — not silently pad or truncate.

**Implication for MarioTrickster-MathArt**: The factory must extract per-frame renders from the actual animation pipeline (orthographic projector driven by bone transforms from Motion2DPipeline), not generate a single render and replicate it.

## 3. Fail-Fast Data Integrity (Jim Gray, 1985; Defensive Programming)

**Core Insight**: Applied to temporal sequences, Fail-Fast means:

> "If a system expects a sequence of N distinct data points but receives N copies of the same data point, it must detect this at the boundary and refuse to proceed."

**Temporal Variance Circuit Breaker Pattern**:
- Before passing a frame sequence to any downstream consumer (ComfyUI, AI renderer, quality gate), compute a **temporal variance metric** (MSE between frame[0] and frame[k], pixel difference ratio, or numpy array variance across the time axis)
- If variance is below a threshold (effectively zero for duplicated frames), raise `PipelineContractError` immediately
- This is the "circuit breaker" — it prevents corrupted data from propagating through the pipeline and producing garbage output that wastes GPU hours

**Implementation Strategy**:
- Compute `np.mean((frame_0.astype(float) - frame_k.astype(float))**2)` for k = N//2 (middle frame)
- If MSE < threshold (e.g., 1.0 for uint8 images), the sequence is effectively static → raise error
- This catches both exact copies AND near-copies (like the SESSION-129 micro-jitter which has MSE < 0.1)

**Implication for MarioTrickster-MathArt**: A temporal variance circuit breaker must be installed at the boundary between guide sequence generation and AI render payload assembly. The threshold must be set high enough to reject synthetic micro-jitter (which doesn't help temporal attention) and only pass through sequences with real geometric motion.
