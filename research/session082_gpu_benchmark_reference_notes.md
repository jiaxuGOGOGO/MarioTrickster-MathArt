# SESSION-082 GPU Benchmark Reference Notes

## Source 1: Google Benchmark User Guide

- URL: https://github.com/google/benchmark/blob/main/docs/user_guide.md
- Key takeaways:
  - Warm-up is a first-class benchmark phase and should be excluded from reported results.
  - Repetitions are required for statistical stability; the framework reports aggregate statistics such as mean, median, standard deviation, and coefficient of variation.
  - Industrial microbenchmarks should avoid single cold-start timings and prefer aggregated steady-state summaries.
  - JSON / structured output is recommended for reproducibility and downstream analysis.

## Source 2: NVIDIA CUDA C++ Best Practices Guide

- URL: https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/
- Key takeaways:
  - CUDA work submission is often asynchronous with respect to the host, so host-side timers are invalid unless explicit synchronization is inserted before stopping the clock.
  - Performance optimization should be iterative and evidence-based (APOD), with correctness preserved while speed is improved.
  - Production code should check runtime failures and device/backend status explicitly rather than assuming GPU availability.

## Immediate implementation implications

- The Taichi GPU benchmark path must keep warm-up outside the measured window.
- Benchmark results should be aggregated from repeated steady-state samples, using median wall time rather than a single run.
- GPU timing must explicitly synchronize the Taichi runtime (`ti.sync()` through the runtime abstraction) before the timer stops.
- CI-safe degradation paths must remain graceful when CUDA is unavailable.

## Source 3: Taichi Docs — Synchronization between Kernels and Python Scope

- URL: https://docs.taichi-lang.org/docs/master/kernel_sync
- Key takeaways:
  - On GPU backends, kernel launches are queued asynchronously with respect to Python.
  - Timing a kernel with a Python timer without `ti.sync()` excludes the actual GPU execution time.
  - `ti.sync()` is required when the benchmark kernel does not itself force a host-visible dependency such as a return value or field readback.
  - On CPU backends, kernel calls are blocking, so the async timing hazard is GPU-specific.

## Source 4: NASA-STD-7009B

- URL: https://standards.nasa.gov/sites/default/files/standards/NASA/B/1/NASA-STD-7009B-Final-3-5-2024.pdf
- Key takeaways:
  - The standard frames verification and validation as evidence-driven activities, not visual plausibility checks.
  - Quantitative comparison against a referent or trusted baseline is required when claiming model fidelity.
  - For this task, the CPU NumPy path should serve as the reference lane for GPU parity checks, with explicit tolerances and recorded drift metrics.

## Consolidated SESSION-082 implementation rules

The benchmark subsystem must treat warm-up as a non-reported stabilization phase and steady-state repeated samples as the only admissible basis for the reported wall time. GPU timing must be closed by explicit runtime synchronization, because host timers alone only measure dispatch latency on asynchronous backends. Performance claims must travel together with correctness evidence, so every CPU-vs-GPU benchmark report needs quantitative parity metadata such as maximum drift and allclose-style tolerance outcomes, not just throughput.
