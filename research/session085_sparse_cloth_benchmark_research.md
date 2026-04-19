# SESSION-085 Research Notes: Sparse Cloth Topology GPU Benchmark

## Task: P3-GPU-BENCH-1 — Extend Taichi GPU Benchmark with Sparse Cloth Topology

### 1. Taichi Sparse Data Structures (Yuanming Hu, SIGGRAPH Asia 2019)

**Key findings from Taichi documentation and papers:**

- Taichi provides `pointer`, `bitmasked`, and `dynamic` SNode types for spatially sparse data
- Sparse struct-fors automatically parallelize over only active cells, skipping empty regions
- Backend compatibility: LLVM-based backends (CPU/CUDA) offer full sparse functionality
- For cloth simulation benchmarking, the critical insight is that **constraint projection on a dense grid already exercises non-trivial memory access patterns** when using red-black (parity-based) Gauss-Seidel iteration with atomic operations
- The existing `TaichiXPBDClothSystem` already uses `ti.atomic_add` and `ti.atomic_max` for constraint error accumulation
- The parity-based constraint solving (even/odd coloring) is the standard GPU-friendly approach to avoid race conditions in parallel XPBD

**Implementation guidance:**
- The `sparse_cloth` benchmark scenario should use the existing dense Taichi cloth system with **constraints fully enabled** (structural + shear + bending)
- This creates a realistic sparse-topology workload: high-density particles with tightly connected distance constraints
- The constraint solve kernels exercise atomic operations and non-sequential memory access patterns
- No need to switch to actual SNode sparse layouts — the "sparsity" refers to the **topological sparsity of the constraint graph**, not memory-level sparsity

### 2. Google Benchmark / C++ Microbenchmark Discipline

**Key findings from Google Benchmark user guide:**

- **Warm-up phase**: `MinWarmUpTime()` runs the benchmark before timing begins to eliminate JIT compilation and cache cold-start effects. The existing backend already implements this via `warmup_frames`.
- **Repeated sampling**: `--benchmark_repetitions` flag enables multiple measurements. The existing backend uses `sample_count` with median aggregation.
- **Median statistic**: More robust than mean for performance measurements because it resists outliers from OS scheduling jitter.
- **Explicit synchronization**: GPU benchmarks must synchronize before stopping timers (already implemented via `ti.sync()`).
- **Isolation**: CPU fallback and GPU paths must be strictly separated. The backend already handles this via `benchmark_device` parameter.

**Implementation guidance:**
- The `sparse_cloth` scenario must use the same warm-up / repeated-sampling / median discipline
- For CI environments (no CUDA), automatically scale down to tiny grids on CPU
- For real-device runs, use production-scale grids (e.g., 128×128 = 16,384 particles)

### 3. NASA-STD-7009B — Physical Equivalence Parity

**Key findings from NASA-STD-7009B (March 2024 revision):**

- **Verification**: Determining that a model implementation accurately represents the developer's conceptual description and specifications
- **Validation**: Determining the degree to which a model is an accurate representation of the real world
- **Credibility assessment**: Simulation results must be paired with quantitative evidence of correctness
- Performance speedup claims without correctness proof are meaningless

**Implementation guidance for CPU/GPU parity:**
- After running `sparse_cloth` on both CPU (NumPy) and GPU (Taichi), extract final particle positions
- Compute `cpu_gpu_max_drift` (L∞ norm) and `cpu_gpu_rmse` (L2 norm)
- Assert both are within acceptable physical tolerance (1e-4 for constrained cloth due to f32 accumulation)
- The CPU reference solver must implement the **same XPBD algorithm** with constraints, not just free-fall
- Race conditions in GPU constraint projection can cause drift — this is exactly what we're testing

### 4. XPBD Constraint Projection & GPU Race Conditions

**Key findings from XPBD literature (Macklin et al.):**

- XPBD uses iterative constraint projection: for each constraint, compute correction and apply to particle positions
- On GPU, parallel constraint solving with shared particles causes **write conflicts (race conditions)**
- Standard mitigation: **graph coloring / parity-based partitioning** — solve even-indexed constraints first, then odd
- The existing Taichi backend already uses parity-based solving (`_solve_horizontal(0, ...)`, `_solve_horizontal(1, ...)`)
- With atomic operations for error accumulation, the solver is GPU-safe but may accumulate floating-point differences vs. sequential CPU execution
- Expected drift tolerance for f32 cloth: ~1e-4 to 1e-3 depending on grid size and iteration count

### 5. Implementation Plan

#### 5.1 New `sparse_cloth` Benchmark Scenario

- Add `sparse_cloth` to the valid scenario set in `TaichiXPBDBackend.validate_config()`
- Build config with `enable_constraints=True`, all constraint types active
- Disable collisions to isolate constraint-projection performance
- Disable pinning to let all particles move freely under gravity + constraints
- For CI: use tiny grid (8×8 = 64 particles)
- For real device: use production grid (128×128 = 16,384 particles)

#### 5.2 CPU NumPy Reference Solver for Constrained Cloth

- Implement sequential XPBD constraint projection in NumPy
- Must match the Taichi solver's algorithm: predict → solve constraints (structural + shear + bending) → finalize
- Use the same sub_steps, solver_iterations, compliance values
- This is the "ground truth" for parity comparison

#### 5.3 Parity Assertion

- After simulation, compare CPU and GPU final positions
- Compute max_drift and RMSE
- For `sparse_cloth`, use relaxed tolerance (1e-3) due to f32 parallel accumulation differences
- Report both metrics in the benchmark JSON

#### 5.4 CI Safety

- Detect CUDA availability via Taichi status check
- In CI (no CUDA): run `sparse_cloth` with tiny grid on CPU, verify report structure
- Heavy workloads only triggered by explicit ignition script or real CUDA detection

### References

[1] Yuanming Hu et al., "Taichi: A Language for High-Performance Computation on Spatially Sparse Data Structures," SIGGRAPH Asia 2019.
[2] Google Benchmark User Guide, https://github.com/google/benchmark/blob/main/docs/user_guide.md
[3] NASA-STD-7009B, "Standard for Models and Simulations," March 2024.
[4] Miles Macklin et al., "XPBD: Position-Based Simulation of Compliant Constrained Dynamics," 2016.
[5] Taichi Sparse Data Structures Documentation, https://docs.taichi-lang.org/docs/v1.5.0/sparse
[6] Xin Zhou, "Applications and Benefits of Voxel Constraints in Parallel XPBD Physics Simulation," Purdue University, 2024.
