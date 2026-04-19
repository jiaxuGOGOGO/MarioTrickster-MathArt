# SESSION-089 Research Notes: Dead Cells 3D→2D Industrial Pipeline

## 🔴 1. Motion Twin (Dead Cells) GDC 2018 — "Art Design Deep Dive"

**Source**: Thomas Vasseur, GDC / Gamasutra, Jan 2018

### Core Pipeline Paradigm
1. **3D Skeletal Animation → 2D Pixel Art**: Artist creates basic 3D model in 3DS Max, exports FBX with bone skeleton
2. **Homebrew Renderer**: Custom in-house tool renders mesh at very small size (50px character height) **without antialiasing** — this is the key to pixel art crispness
3. **Orthographic Camera**: Flat projection eliminates perspective distortion, ensuring 2D side-scroller visual consistency
4. **Multi-Channel Export**: Each frame exports as PNG + Normal Map for volume rendering via toon shader
5. **Cel-Shading on 3D Models**: Applied before render, rendered in low resolution without AA
6. **Pose-to-Pose Animation**: Keyframes first, interpolation frames added before/after (never in-between)
7. **Asset Reuse**: 3D models allow easy armor/weapon attachment and skeleton reuse across monsters

### Critical Technical Rules
- **NO anti-aliasing** during render — nearest-neighbor only
- **Low resolution output** (character ~50px tall)
- **Normal map co-export** for dynamic 2D lighting
- **Flickering pixels** acknowledged as unsolved trade-off (accepted for speed)

---

## 🔴 2. Arc System Works (Guilty Gear Xrd) GDC 2015 — Cel-Shading & Stepped Animation

**Source**: Junya Christopher Motomura, Technical Artist, ArcSystemWorks, GDC 2015

### Core Cel-Shading Principles
1. **Hard Threshold Lighting (Stepped Shading)**: Surface is either lit or not — NO smooth gradient transitions
   - Binary/ternary light bands: lit zone, shadow zone, optional rim highlight
   - Threshold function: `if (N·L > threshold) → lit_color else → shadow_color`
2. **Inner Lines via Distorted UV**: Muscle/detail lines baked into UV distortion, not geometry
3. **Frame Rate Reduction for 2D Feel**: Animations deliberately run at lower fps (12-15fps) while game runs at 60fps
   - "Stepped animation" / "hold frames" — same pose held for multiple game frames
   - Creates the "snappy" hand-drawn animation feel
4. **Camera-Dependent Vertex Displacement**: Vertices manually shifted per-camera-angle to maintain 2D silhouette appeal
5. **Outline Rendering**: Inverted-hull method with vertex normal extrusion for thick outlines

### Key Implementation Rules for Our Pipeline
- **Dot product N·L with hard step function**: `max(dot(N, L), 0)` → apply threshold bands
- **2-3 discrete light levels only** (no smooth ramp)
- **Frame decimation**: Output at 12fps or 15fps for pixel art punch
- **Silhouette priority**: 2D readability > 3D accuracy

---

## 🔴 3. Headless EGL / Software Rasterizer Architecture

### The Problem
- CI servers (GitHub Actions, Docker containers) have NO display server (no X11, no Wayland)
- Any call to GLFW, pygame.display, or X11-dependent OpenGL → instant crash
- Error: `X11 display not found`, `Failed to initialize GLFW`, `GLFWError`

### Solutions (Ranked by Preference for Our Pipeline)
1. **Pure NumPy Software Rasterizer** (BEST for our case)
   - Zero external dependencies
   - 100% CI-safe, no GPU required
   - Implement projection matrix, Z-buffer, fragment shading in pure NumPy
   - Performance sufficient for 64x64 / 128x128 sprite rendering

2. **OSMesa (Off-Screen Mesa)**
   - Mesa's CPU-only OpenGL implementation
   - `pip install PyOpenGL` + `PYOPENGL_PLATFORM=osmesa`
   - Requires `libosmesa6-dev` system package
   - Good for complex scenes but adds system dependency

3. **EGL Headless Context**
   - GPU-accelerated but requires NVIDIA drivers + EGL support
   - `eglGetDisplay(EGL_DEFAULT_DISPLAY)` → create PBuffer surface
   - Not available on all CI runners

### Our Architecture Decision
- **Primary**: Pure NumPy/SciPy software rasterizer (zero dependency, CI-safe)
- **Fallback**: OSMesa if available
- **NEVER**: GLFW, pygame, X11, or any windowed context

---

## Implementation Synthesis for P1-INDUSTRIAL-34C

### Rendering Pipeline Architecture
```
3D Mesh/Skeleton Data (UMR)
    │
    ▼
┌─────────────────────────────────────┐
│ OrthographicPixelRenderBackend      │
│ (Registry Plugin via @register_backend) │
│                                     │
│ 1. Orthographic Projection Matrix   │
│    ┌                    ┐           │
│    │ 2/w  0    0   0    │           │
│    │ 0    2/h  0   0    │           │
│    │ 0    0   -2/d  0   │           │
│    │ 0    0    0    1   │           │
│    └                    ┘           │
│                                     │
│ 2. Software Rasterizer (NumPy)      │
│    - Triangle setup & edge functions│
│    - Z-buffer depth test            │
│    - Barycentric interpolation      │
│                                     │
│ 3. Multi-Pass Output:               │
│    - Albedo (base color)            │
│    - Normal (world-space normals)   │
│    - Depth (linear Z)               │
│                                     │
│ 4. Cel-Shading Kernel:              │
│    - N·L dot product                │
│    - Stepped threshold banding      │
│    - 2-3 discrete light levels      │
│                                     │
│ 5. Nearest-Neighbor Downscale:      │
│    - PIL.Image.resize(NEAREST)      │
│    - NEVER bilinear/bicubic         │
│    - Assert: edge pixels α ∈ {0,255}│
└─────────────────────────────────────┘
    │
    ▼
ArtifactManifest (INDUSTRIAL_SPRITE)
```

### Anti-Pattern Traps to Avoid
1. ❌ Perspective projection → ✅ Pure orthographic matrix
2. ❌ Bilinear/bicubic interpolation → ✅ Nearest-neighbor ONLY
3. ❌ GLFW/X11 window creation → ✅ Pure NumPy software rasterizer
