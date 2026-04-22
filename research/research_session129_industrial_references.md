# SESSION-129 Industrial & Academic Research References

## 1. Design by Contract (Bertrand Meyer, ETH Zurich / Eiffel)

**Source**: Meyer, B. "Design by Contract" — Chapter from *Advances in Object-Oriented Software Engineering*, Prentice Hall, 1991. Also: se.inf.ethz.ch/~meyer/publications/old/dbc_chapter.pdf

**Core Principles**:
- **Preconditions**: What must be true before a routine is called. The caller's obligation.
- **Postconditions**: What the routine guarantees after execution. The supplier's obligation.
- **Class Invariants**: Conditions that must hold for all instances at all observable points.
- **Violation = Bug**: A precondition violation is a bug in the *client* (caller). A postcondition violation is a bug in the *supplier* (callee). There is no "graceful degradation" — violation means the software is incorrect.

**Application to MarioTrickster-MathArt**:
- The `motion_2d_pipeline.py` quadruped export is a *supplier* whose postcondition is: "all animation track bone names exist in the setup skeleton". If this postcondition cannot be met, the routine must raise an exception — not silently output empty/misnamed tracks.
- The `AntiFlickerRenderBackend` has a *precondition*: "width and height must be provided and >= 64". If the caller violates this, the backend must refuse execution immediately.
- The factory's guide sequence generation has an *invariant*: "each frame in the guide sequence must be geometrically distinct from the previous frame". A `[single_image] * N` violates this invariant.

## 2. Fail-Fast / Crash-Only Software (Jim Gray, Tandem Computers)

**Source**: Gray, J. "Why Do Computers Stop and What Can Be Done About It?" — Tandem Computers Technical Report 85.7, 1985. Also referenced in SESSION-128 research.

**Core Principles**:
- **Fail-Fast Module**: "Each module checks its inputs and state; if anything is wrong, it immediately signals failure rather than trying to continue."
- **Crash-Only Software**: Systems designed so that the only way to stop them is to crash them, and the only way to start them is to recover from a crash. This eliminates the distinction between "clean shutdown" and "crash recovery."
- **Silent Failure is the Enemy**: A module that silently produces garbage output is far more dangerous than one that crashes loudly.

**Application to MarioTrickster-MathArt**:
- `width = kwargs.get('width', 32)` is a textbook anti-pattern: it silently degrades to 32px output instead of crashing when width is missing.
- `except KeyError: pass` swallows contract violations, allowing garbage to flow downstream.
- The 32x16 color block output from anti-flicker rendering is exactly the "silent failure" Gray warned about — the system appeared to work but produced unusable output.

## 3. Unreal Engine 5 Animation Retargeting & Bone Mapping

**Source**: Epic Games, "IK Rig Animation Retargeting in Unreal Engine" — dev.epicgames.com/documentation/unreal-engine/ik-rig-animation-retargeting-in-unreal-engine

**Core Principles**:
- **Chain-Based Mapping**: UE5 retargeting works by defining bone *chains* on both source and target skeletons. Chains must be explicitly mapped 1:1 between source and target.
- **Strict Name Resolution**: The auto-naming system uses a predefined common bone name list. If bone names don't match the expected vocabulary, the chain mapping fails and the user must manually resolve it.
- **Retarget Output Log**: UE5 provides a dedicated output log that displays warnings and errors for mismatched chains, missing bones, and unmapped regions. This is the "fail-loud" approach.
- **No Silent Passthrough**: If a bone chain on the source has no corresponding chain on the target, that animation data is *dropped* with a visible warning — not silently passed through with wrong bone names.

**Application to MarioTrickster-MathArt**:
- The quadruped gait export must enforce the same discipline: animation curves must map 1:1 to setup skeleton bones.
- If `fl_upper`, `fr_upper`, `hl_upper`, `hr_upper` are the canonical structural bone names, then animation tracks targeting any other names must be intercepted and rejected at export time.
- The 38-frame static Spine output was caused by exactly this: animation tracks targeted non-existent bone names, so Spine silently ignored them, producing zero motion.

## 4. Temporal Coherence in Video Diffusion (SparseCtrl / AnimateDiff)

**Source**: 
- Guo et al., "AnimateDiff: Animate Your Personalized Text-to-Image Diffusion Models without Specific Tuning" — ICLR 2024
- He et al., "SparseCtrl: Adding Sparse Controls to Text-to-Video Diffusion Models" — ECCV 2024
- Xia et al., "UniCtrl: Improving the Spatiotemporal Consistency of Text-to-Video Diffusion Models" — arXiv 2403.02332

**Core Principles**:
- **Temporal Attention Modules**: AnimateDiff inserts temporal attention layers that learn cross-frame motion patterns. These layers expect *varying* input across frames to learn meaningful temporal correlations.
- **Static Input Catastrophe**: Feeding N copies of the same static frame as ControlNet conditioning destroys the temporal attention signal. The model receives zero optical flow information and degenerates to per-frame independent generation, producing flickering or frozen output.
- **Sparse Control Strategy**: SparseCtrl addresses this by providing control signals at sparse keyframes and letting the model interpolate. But even sparse controls must show *genuine geometric variation* between keyframes.
- **Guide Sequence Requirements**: For ControlNet-conditioned video generation, the guide sequence (normal maps, depth maps, RGB references) must exhibit frame-to-frame geometric displacement that reflects the actual character motion. Otherwise, the temporal attention has nothing to attend to.

**Application to MarioTrickster-MathArt**:
- The factory's `[guide_image] * n_frames` pattern is the exact "static input catastrophe" described above. It feeds N identical frames to the temporal attention, destroying the model's ability to generate coherent motion.
- The fix must extract per-frame guide images from the motion2d pipeline or bone-driven rendering, ensuring each frame reflects the character's actual pose at that timestep.
- This is not just a quality issue — it's a fundamental architectural violation that makes the AI rendering stage produce output indistinguishable from single-image generation.
