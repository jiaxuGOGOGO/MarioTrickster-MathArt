# SESSION-086 Research: SparseCtrl + AnimateDiff + VHS Temporal Consistency Pipeline

## 1. SparseCtrl (Guo et al., 2023) — ECCV 2024

**Paper**: "SparseCtrl: Adding Sparse Controls to Text-to-Video Diffusion Models"
**Core Mechanism**: Injects temporally sparse condition maps (keyframes) into the temporal attention layers of a video diffusion model. Instead of requiring per-frame conditioning, SparseCtrl uses only 1 or a few keyframe images (RGB, depth, scribble) to guide the entire video generation.

**Key Architecture Points**:
- Implements a **Sparse Encoder** that processes condition images at sparse timesteps
- Three encoder variants: RGB, Depth, Scribble
- Built on top of AnimateDiff motion modules — requires AnimateDiff as the base temporal backbone
- Uses a **Domain Adapter** (standard SD LoRA) to correct weight biases introduced by sparse conditioning

**ComfyUI Node**: `ACN_SparseCtrlLoaderAdvanced`
- **class_type**: `ACN_SparseCtrlLoaderAdvanced`
- **Inputs**: `ckpt_path` (STRING), `controlnet_data` (optional dict), `timestep_keyframe` (optional), `sparse_settings` (SparseSettings), `model` (optional)
- **Output**: `sparse_ctrl_advanced` — a SparseCtrl model instance
- Provided by: ComfyUI-Advanced-ControlNet extension (Kosinkadink)
- `use_motion` parameter: set to False for non-AD workflows, True for AnimateDiff

## 2. AnimateDiff Motion Module (Guo et al., 2023)

**Paper**: "AnimateDiff: Animate Your Personalized Text-to-Image Diffusion Models without Specific Tuning"
**Core Mechanism**: Inserts temporal attention (motion) modules into a frozen text-to-image diffusion model, enabling it to generate temporally coherent video frames from a single latent batch.

**ComfyUI Node**: `ADE_AnimateDiffLoaderWithContext`
- **class_type**: `ADE_AnimateDiffLoaderWithContext`
- **Key Inputs**:
  - `model_name` (STRING): motion module checkpoint name (e.g., `v3_sd15_mm.ckpt`)
  - `beta_schedule` (STRING): `autoselect`, `sqrt_linear (AnimateDiff)`, `linear (AnimateDiff-SDXL)`, etc.
  - `context_options` (optional): Context window configuration for sliding window inference
- **Output**: Patched MODEL with motion module injected
- The **batch_size** of the EmptyLatentImage node controls the number of frames generated
- **context_length** in context options controls the sliding window size (typically 16)

**Critical Integration Note**: The Latent batch_size MUST match the desired frame count. AnimateDiff connects independent frames in latent space into a coherent video tensor through temporal attention.

## 3. ComfyUI-VideoHelperSuite (VHS) — Industrial Sequence I/O

**Repository**: Kosinkadink/ComfyUI-VideoHelperSuite
**Purpose**: Standard industrial-grade sequence frame I/O for ComfyUI workflows.

### VHS_LoadImagesPath Node
- **class_type**: `VHS_LoadImagesPath`
- **Key Inputs**:
  - `directory` (STRING): Directory path containing image sequence
  - `image_load_cap` (INT, default 0): Max images to load (0 = all)
  - `skip_first_images` (INT, default 0): Skip N initial images
  - `select_every_nth` (INT, default 1): Subsample every Nth image
  - `meta_batch` (optional VHS_BatchManager): Batch management
- **Outputs**: IMAGE (batch), MASK (batch), frame_count (INT)

### VHS_VideoCombine Node
- **class_type**: `VHS_VideoCombine`
- **Key Inputs**:
  - `images` (IMAGE batch): The generated frames
  - `frame_rate` (FLOAT): Output FPS
  - `filename_prefix` (STRING): Output filename prefix
  - `format` (STRING): Output format (e.g., "video/h264-mp4")
- **Output**: Combined video file

## 4. Workflow Topology for SparseCtrl + AnimateDiff + VHS

The production workflow requires these nodes in sequence:
1. **CheckpointLoaderSimple** → Load SD model
2. **ADE_AnimateDiffLoaderWithContext** → Inject motion module into model
3. **VHS_LoadImagesPath** (Normal sequence) → Load normal map frame sequence from directory
4. **VHS_LoadImagesPath** (Depth sequence) → Load depth map frame sequence from directory
5. **ACN_SparseCtrlLoaderAdvanced** → Load SparseCtrl model (RGB/Depth/Scribble)
6. **ControlNetApplyAdvanced** → Apply SparseCtrl conditioning with keyframe scheduling
7. **EmptyLatentImage** → Create latent batch (batch_size = frame_count)
8. **CLIPTextEncode** (positive/negative) → Text conditioning
9. **KSampler** → Sample with temporal coherence
10. **VAEDecode** → Decode latents to images
11. **VHS_VideoCombine** → Combine frames into video output

## 5. Key Design Constraints for MathArt Integration

1. **Frame Sequence, Not Single Image**: SparseCtrl processes VIDEO frame sequences. The injector MUST use `VHS_LoadImagesPath` with `directory` parameter, NOT single-image `LoadImage` nodes.
2. **Batch Size Synchronization**: The `EmptyLatentImage.batch_size` MUST equal the total frame count from the VHS loader.
3. **Context Length**: AnimateDiff context_length (typically 16) defines the temporal attention window.
4. **Offline-Safe Testing**: All tests validate JSON payload structure only — no HTTP requests to ComfyUI server.

## References

[1] Guo et al., "SparseCtrl: Adding Sparse Controls to Text-to-Video Diffusion Models", ECCV 2024. https://arxiv.org/abs/2311.16933
[2] Guo et al., "AnimateDiff: Animate Your Personalized Text-to-Image Diffusion Models", ICLR 2024. https://arxiv.org/abs/2307.04725
[3] Kosinkadink, "ComfyUI-AnimateDiff-Evolved", GitHub. https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved
[4] Kosinkadink, "ComfyUI-Advanced-ControlNet", GitHub. https://github.com/Kosinkadink/ComfyUI-Advanced-ControlNet
[5] Kosinkadink, "ComfyUI-VideoHelperSuite", GitHub. https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite
