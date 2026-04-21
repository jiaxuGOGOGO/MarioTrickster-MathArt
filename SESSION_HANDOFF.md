# SESSION-121 Handoff: P1-NEW-9C Character Evolution 3.0 — Multi-Slot Equipment Library & Tensorized Bone-Socket Mounter

## Goal & Status
**Objective**: Implement P1-NEW-9C — extend `CharacterGenotype` with three new equipment families (torso overlay, hand item, foot accessory) implemented as parametric SDF-derived `Mesh3D` primitives, and ship a vectorised `TensorSocketMounter` that mounts those primitives onto the existing 2-D `Skeleton` via UE5-style sockets.
**Status**: `CLOSED`.

## Research Alignment Audit
The implementation was guided by three industry/academic references; full notes live in `docs/research/P1_NEW_9C_research_alignment.md`.

- **Unreal Engine 5 — Skeletal Mesh Sockets**: Adopted UE5's "named attach point parented to a bone with local SRT offsets" model verbatim. Our `SocketSpec` mirrors `USkeletalMeshSocket` (parent_bone + local_translation + local_rotation_deg + local_scale), and `build_socket_world_matrix` mirrors `GetSocketTransform` chained with the bone's component-space transform.
- **Inigo Quilez — Signed Distance Functions**: Adopted three SDF operator families and translated them to vertex space so we never have to march distance fields at runtime:
  - `opRound(box, r)` → vertex-normal dilation for breastplate / vest / boots / greaves;
  - `opOnion(d, t)` → twin-shell extrusion for the wizard robe;
  - `opElongate(p, h)` → half-extent stretching for the long sword and the mage staff.
- **Pixar OpenUSD — Reference Composition Arc**: Reused the already-shipped `compose_character_with_attachments` (in `mathart/core/physical_ribbon_backend.py`) which builds a `COMPOSITE` manifest referencing the base character + every attachment manifest. The new 3D equipment meshes plug straight into this contract as additional attachment manifests.

## Architecture Decisions Locked
1. **`SlotType.FOOT_ACCESSORY` is a first-class slot** — added to the canonical `SlotType` enum so foot equipment lives in `genotype.slots` rather than as ad-hoc style flags. Default `humanoid_standard` template now lists six available slots: `hat`, `face_accessory`, `torso_overlay`, `back_accessory`, `hand_item`, `foot_accessory`.
2. **3D equipment lives in a sibling module `mathart/animation/parts3d.py`**, not in `parts.py`. The 2-D SDF morphology library remains the authoritative consumer of `assemble_character(style)`; the new module owns Mesh3D primitives and the socket mounter so the 2D path stays untouched (zero risk of regression).
3. **Each part factory returns a typed `PartShape3D` carrier** (part_id + primitive label + Mesh3D + inflate_radius + half_extents). This metadata travels with the geometry so the renderer / Z-buffer combiner can later branch on primitive family without re-inspecting the mesh.
4. **`TensorSocketMounter.mount` is the hot path** and is implemented as a single `np.einsum` matmul broadcast over the (N, 4) homogeneous vertex batch — no Python per-vertex loops. Verified at < 1 ms per 3-attachment loadout (warm cache).
5. **Anti Z-fighting is a mathematical guarantee, not a numeric prayer**: every shell-style part has `inflate_radius > 0`, and the regression test `test_breastplate_vertex_norms_exceed_base` proves every inflated vertex sits strictly outside the underlying base box.
6. **Backward-compatible (de)serialisation**: legacy JSON archives that pre-date SESSION-121 (no torso/hand/foot equipment slots) round-trip through `CharacterGenotype.from_dict → to_dict` with byte-identical slot semantics. We do **not** auto-inject the new slots — this is an evolutionary-lineage safety guarantee.

## Code Change Table
| File | Action | Details |
|---|---|---|
| `mathart/animation/genotype.py` | Modified | Added `SlotType.FOOT_ACCESSORY`; appended six humanoid templates with full slot tuple; added 11 new `PART_REGISTRY` entries (3 torso overlays + none, 3 hand items + none, 3 foot accessories + none). |
| `mathart/animation/parts3d.py` | Added | New module: `PartShape3D`, `SocketSpec`, `MountedAttachment`, `TensorSocketMounter`, `DEFAULT_SOCKETS`, `PART_FACTORIES_3D`, plus 9 parametric primitive factories and `build_attachments_from_genotype`. |
| `tests/test_character_parts.py` | Added | 32 white-box tests across 6 sections (registry expansion, JSON backward compat, SDF-offset envelope, socket transform correctness, mounter end-to-end, genotype-driven pipeline). |
| `docs/research/P1_NEW_9C_research_alignment.md` | Added | Distilled notes for the three reference pillars. |

## White-Box Validation Closure
- **32/32** targeted tests in `tests/test_character_parts.py` PASS.
- Full repo regression: **2128 PASS / 8 SKIP / 20 FAIL**. The 20 failures **pre-exist on the baseline commit `0c9a84e` and are unrelated** to P1-NEW-9C — verified by `git stash && pytest` against the same set, which produced an identical failure list (high-precision VAT environment requirements, anti-flicker live-HTTP gates, SparseCtrl client polling). **Zero new regressions.**
- Microsecond hot-path budget: `TensorSocketMounter.mount` warm-cache cost < 1 ms per 3-attachment loadout (assertion enforced in `test_microsecond_class_hot_path`).

## P1-AI-1 Multi-Pass Z-Buffer Hand-off
Each `MountedAttachment` carries world-space vertices whose `z` coordinate is the depth source the P1-AI-1 multi-pass orthographic renderer (in `mathart/animation/orthographic_pixel_render.py`) will sample. The contract is:
- **Source of truth**: `MountedAttachment.mesh.vertices[:, 2]` after `TensorSocketMounter.transform_mesh` applies the socket's world matrix.
- **Layering rule**: a part with a strictly larger `inflate_radius` than the underlying base mesh is guaranteed to occupy a deeper-back / nearer-front shell, so the multi-pass Z-buffer can sort attachments by `(parent_bone, primitive, inflate_radius)` and emit the canonical depth gradient that ControlNet downstream-of-P1-AI-1 consumes.
- **Composite manifest plumbing**: wrap each mounted mesh in an `ArtifactManifest` (BackendType `MESH_OBJ`) and pass the list to `compose_character_with_attachments(...)`. The COMPOSITE manifest is what the renderer driver reads to schedule passes.

## Handoff / Next Steps (P1-NEW-9D suggested)
With the equipment library and socket mounter shipped, the next logical step is **P1-NEW-9D — Multi-pass character composition**: drive the P1-AI-1 orthographic renderer with the COMPOSITE manifest produced from `build_attachments_from_genotype`, including the depth-sorted sequencing required for ControlNet depth conditioning.
- **Current State**: equipment is geometrically correct and socket-mounted, but the renderer driver still consumes only the base character mesh.
- **Action Required**: extend the orthographic render scheduler to iterate `MountedAttachment` lists, accumulate per-primitive depth into the multi-pass Z-buffer, and serialise the result as a multi-channel sprite (albedo + depth + normal).
