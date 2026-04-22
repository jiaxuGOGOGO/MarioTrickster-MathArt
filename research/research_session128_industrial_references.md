# SESSION-128 Research: Industrial & Academic Reference Synthesis

## 1. Pixar USD (Universal Scene Description) — Composition Semantics

**Core Principle: Lossless Hierarchical Composition with Strong-Type Schemas**

USD's composition system operates through LIVRPS (Local, Inherits, VariantSets, References, Payloads, Sublayers) — a strict strength ordering that determines how opinions from different layers are resolved. The key architectural insight for our project:

- **Strong-Type Schema Validation**: Every USD prim conforms to a typed schema (e.g., `UsdGeomMesh`, `UsdGeomXform`). The validation framework (`usdchecker`) rejects prims that violate schema constraints. This maps directly to our `ArtifactManifest` system — every pipeline output must declare its `artifact_family` and `backend_type`, and downstream consumers must validate these types before consumption.

- **Composition as Contract**: USD's composition arcs (references, payloads, sublayers) are not loose file includes — they are typed contracts. A reference to a `UsdGeomMesh` guarantees mesh data exists. If the referenced layer is missing or malformed, USD raises a composition error rather than silently degrading.

- **Application to Our Pipeline**: The `OrthographicPixelRenderBackend` must treat its `Mesh3D` input as a USD-style typed reference. If the upstream `Pseudo3DShellBackend` or `PhysicalRibbonBackend` fails to produce a valid `Mesh3D`, the render backend must raise a `PipelineContractError` (analogous to USD's composition error), not silently fall back to a demo sphere.

## 2. Bazel / Buck — Action Cache & Determinism

**Core Principle: Content-Addressable Hermetic Builds with Hash-Verifiable Artifacts**

Bazel's build model treats every action as a pure function: `output = f(inputs, tool)`. The action cache key is computed as `SHA-256(input_hashes + action_descriptor)`. This guarantees:

- **Hermeticity**: An action's output depends only on its declared inputs, never on ambient environment state. If inputs are identical, outputs are bit-for-bit identical.

- **Content-Addressable Storage**: Every artifact is stored by its content hash. Cache lookups are hash-based, not path-based. This eliminates "stale cache" bugs entirely.

- **Application to Our PDG**: Each PDG node's `rng_spawn_digest` must function like a Bazel action cache key — it is the cryptographic fingerprint of the RNG state that produced that node's output. By writing `rng_spawn_digest` into `ArtifactManifest.metadata` and `batch_summary.json`, we achieve Bazel-level hash verifiability: given the same root seed and DAG topology, every node must produce the same digest, and any deviation signals a determinism violation.

## 3. Data Mesh / Data Lake Archiving Paradigms

**Core Principle: Domain-Oriented Data Products with Centralized Delivery Contracts**

Zhamak Dehghani's Data Mesh (Martin Fowler, 2020) advocates:

- **Data as a Product**: Each domain team owns its data products end-to-end, including quality, discoverability, and delivery SLAs.

- **Self-Serve Data Infrastructure**: A centralized platform provides the tools, but domains own the data.

- **Federated Computational Governance**: Cross-domain standards ensure interoperability without centralized bottlenecks.

- **Application to Our Archive**: The `/archive` directory and `batch_summary.json` implement the "centralized delivery contract" aspect of Data Mesh. Each backend (domain) produces its own artifacts, but the archive backend (platform) enforces a unified delivery schema. The `batch_summary.json` is the federated governance layer — it provides a single queryable index across all character domains, with standardized fields for `rng_spawn_digest`, manifest paths, and delivery status.

## 4. Fail-Fast Principle (Jim Gray, Tandem Computers, 1985)

**Core Principle: Self-Checking Modules That Stop on Fault Detection**

Jim Gray's seminal paper "Why Do Computers Stop and What Can Be Done About It?" (Technical Report 85.7, Tandem Computers, 1985) established:

- **Fail-Fast Definition**: "Each module is self-checking. When it detects a fault, it stops." This is contrasted with fail-silent (module stops but doesn't report) and fail-soft (module continues in degraded mode).

- **Fault Containment Through Fail-Stop Software Modules**: Software modules should be designed to detect internal inconsistencies and halt immediately, rather than propagating corrupted state to downstream modules.

- **The Bohrbug-Heisenbug Hypothesis**: Most production software faults are Heisenbugs (transient, non-reproducible). Fail-fast + restart is the most effective strategy because it eliminates the corrupted state that caused the Heisenbug.

- **Application to Our Pipeline**: The `OrthographicPixelRenderBackend` currently violates the Fail-Fast principle by silently falling back to a demo sphere mesh when no real `Mesh3D` is provided. This is a textbook "fail-soft" anti-pattern that propagates corrupted state (fake geometry) downstream, causing the `generator_invariant` stagnation disaster (22,422 identical iterations). The fix is to convert this to a fail-fast module: if `Mesh3D` is absent or empty, raise `PipelineContractError` immediately. Better to crash early with a clear diagnostic than to silently produce 22,422 identical renders.

## Synthesis: Architectural Principles for SESSION-128

1. **USD-Style Strong-Type Consumption**: Render backends must validate input types as strictly as USD validates schema conformance. No fallback meshes.

2. **Bazel-Level Hash Auditability**: Every PDG node's RNG state must be captured as an immutable digest and propagated through the entire artifact chain.

3. **Data Mesh Centralized Delivery**: The archive directory is not optional — it is the delivery contract. `batch_summary.json` is the federated index.

4. **Jim Gray Fail-Fast**: Silent degradation is forbidden. Contract violations must halt the pipeline immediately with typed exceptions.
