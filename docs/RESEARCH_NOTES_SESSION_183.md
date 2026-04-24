# SESSION-183 Research Notes: Microkernel Hub & VAT Integration

## 1. Microkernel Architecture & Reflection-based Service Locator (IoC)

**Core Principles Applied:**
- **Registry Pattern (LLVM-inspired)**: Each backend self-registers via `@register_backend` decorator at import time. The `BackendRegistry` singleton discovers all registered backends and provides lookup by name, family, or capability. Trunk pipeline never needs modification when new backends are added.
- **Inversion of Control (Martin Fowler, 2004)**: The framework calls the plugins, not the other way around. Configuration Registry pattern: plugins register themselves, and the framework discovers them via introspection.
- **Python Reflection for Dynamic Menu Generation**: Using `__doc__`, `__class__.__name__`, and `_backend_meta` attributes to dynamically enumerate and render interactive menus. ZERO hardcoded if/else routing.
- **David Seddon's Three IoC Techniques in Python**: Dependency Injection, Configuration Registry, Subscriber Registry — our project uses Configuration Registry pattern.

**Implementation Mandate:**
- CLI Laboratory Hub MUST use `registry.all_backends()` to dynamically discover ALL registered backends
- Menu items MUST be generated via Python reflection (iterating over registry entries, reading `_backend_meta.display_name` and class `__doc__`)
- Future plugins auto-appear in menu with ZERO code changes to the hub

## 2. HDR Vertex Animation Textures (VAT) for AAA Pipelines

**SideFX Houdini VAT 3.0 Key Requirements:**
- Position displacement data MUST use HDR (float) textures — 8-bit sRGB PNG causes severe vertex jitter (Vertex Quantization Jitter)
- Global Bounding Box Quantization: Scale & Bias computed from global min/max across ALL frames and ALL vertices (never per-frame)
- Unity Texture Importer: sRGB=False (Linear), Filter=Point, Compression=None, Generate Mip Maps=False
- Dual export strategy: Raw float32 binary (.npy) for zero loss + Hi-Lo packed PNG pair for Unity 16-bit reconstruction

**Anti-Precision-Loss Guards:**
- ZERO `np.uint8` or `* 255` in float export path
- Global bounds via `np.min(positions, axis=(0, 1))` across ALL frames
- Only numpy/cv2 — no C++ dependencies

## 3. Feature Toggles & Sandboxed Execution (Martin Fowler, 2017)

**Feature Toggle Categories Applied:**
- **Experiment Toggles**: Laboratory Hub acts as an experiment toggle router — experimental backends are accessible but isolated
- **Ops Toggles / Kill Switches**: Experimental backends can be enabled/disabled without touching production code
- **Fail-Safe Pattern**: Experimental outputs MUST be physically isolated from production vault

**Sandboxed Execution Mandates:**
- All experimental backend outputs go to `workspace/laboratory/<backend_name>/` — NEVER to `output/production/`
- Physical path-level isolation between experimental sandbox and production vault
- Circuit Breaker pattern: any experimental failure is caught and contained, never propagates to production pipeline

## References

1. Chris Lattner, "LLVM", The Architecture of Open Source Applications, 2012
2. Martin Fowler, "Inversion of Control Containers and the Dependency Injection pattern", 2004
3. David Seddon, "Three Techniques for Inverting Control, in Python", 2019
4. Pete Hodgson / Martin Fowler, "Feature Toggles (aka Feature Flags)", 2017
5. SideFX, "Labs Vertex Animation Textures 3.0 render node", Houdini Documentation
6. Mouret & Clune, "MAP-Elites", arXiv:1504.04909, 2015
