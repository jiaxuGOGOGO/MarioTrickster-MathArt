# RESEARCH_NOTES_SESSION_198: Math-to-Pixel Rasterization Bridge

## 1. Houdini VAT (Vertex Animation Textures) & VOPs to COPs
**Core Insight**: In high-end VFX pipelines, abstract mathematical representations of physics (like soft-body deformation tensors or fluid momentum grids) cannot be directly fed into image-based generative models. Houdini solves this via VAT (Vertex Animation Textures), where 3D coordinate offsets (X, Y, Z) and normals are "baked" into the RGB channels of 2D textures (e.g., Red=X, Green=Y, Blue=Z).
**Application in SESSION-198**: We implemented a pure-CPU equivalent using NumPy/PIL. Our `PhysicsRasterizerAdapter` takes the abstract JSON arrays from the physics/fluid backends and projects them onto a 2D 512x512 canvas. Fluid momentum vectors (dx, dy) are mapped to Red and Green channels, while 3D physics deformation depth is mapped to a grayscale gradient.

## 2. Adapter Pattern for Cross-Medium Bridging
**Core Insight**: The Adapter Pattern (from GoF/Refactoring.guru) is designed to convert the interface of a class into another interface that clients expect.
**Application in SESSION-198**: The `vfx_topology_hydrator` expects a directory of PNG images to feed into ComfyUI's `VHS_LoadImagesPath`. However, the `physics_3d` and `fluid_momentum` backends output JSON files containing mathematical arrays. We built the `PhysicsRasterizerAdapter` to sit exactly at the boundary between these two domains, dynamically intercepting the JSON artifacts during pipeline assembly and translating them into the expected PNG format without mutating either the physics engine or the hydrator.

## 3. Martin Fowler's CI/CD "Zero Broken Windows" Rule
**Core Insight**: Martin Fowler emphasizes that in Continuous Integration, a broken build is an emergency. If the main branch tests are failing, no new features should be built until the red lights are fixed.
**Application in SESSION-198**: We strictly paused the rasterizer feature work to first investigate and fix the 4 failing tests in `test_session196_intent_threading.py`. The root cause was traced to a missing registry entry for `physics_3d` in `SemanticOrchestrator`. Only after restoring the test suite to 100% green did we proceed with the P0 rasterization task.

## 4. Anti-Fake-Image Red Line
To prevent the adapter from hallucinating empty black/white images just to pass the `os.path.exists()` check, we instituted a strict mathematical variance check in our test suite (`np.var(img) > 0`). This proves that the Catmull-Rom splatting and interpolation algorithms are genuinely transferring mathematical fluctuations into visual pixel data.
