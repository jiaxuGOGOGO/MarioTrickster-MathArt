# SESSION-068 Architecture Research: Reference Standards for P1-AI-2C & P1-INDUSTRIAL-34A

## 1. Hexagonal Architecture (Ports and Adapters) — Alistair Cockburn

**Core Principle**: The application core (domain logic) communicates with the outside world through Ports (abstract interfaces). Concrete implementations are Adapters that plug into those ports. The core is ignorant of which adapter is connected.

**Application to MarioTrickster-MathArt**:
- The CLI (`cli.py`) is the **Port** — it is the single command surface that is absolutely ignorant of specific business logic
- `anti_flicker_render` and `industrial_sprite` are **Adapters** — they implement the `BackendProtocol` and plug into the registry
- Parameter parsing and contract validation **must sink entirely into the backend (Adapter)** — the Port never inspects backend-specific parameters
- Duck Typing: backends only need to satisfy `name`, `meta`, `execute(context)` — no inheritance hierarchy required

**Red Lines**:
- CLI must NOT contain any backend-specific parameter parsing (no `if backend == "anti_flicker": parse_temporal_params()`)
- Config validation is the backend's responsibility via `validate_config()` method
- The registry is the IoC container — backends register themselves, the bus discovers them

## 2. OpenTimelineIO (OTIO) — Time-Series Manifest Contract

**Core Concept**: OTIO represents editorial timelines as nested JSON trees. Each clip has:
- `source_range`: start time + duration (RationalTime)
- `media_reference`: physical file path + available_range
- Tracks contain ordered sequences of clips with temporal relationships

**Application to Anti-Flicker (P1-AI-2C)**:
- Output is NOT a single image but a **time series** (frame sequence)
- The manifest must contain a `frame_sequence` array where each entry maps:
  - `frame_index`: integer frame number
  - `path`: absolute file path to the rendered frame
  - `role`: keyframe | propagated | interpolated
  - `temporal_coherence_score`: float [0,1]
  - `optical_flow_ref`: path to associated flow data (if applicable)
- The manifest must also include:
  - `time_range`: { start_frame, end_frame, fps }
  - `keyframe_plan`: which frames are keyframes vs propagated
  - `identity_lock_metadata`: reference frame, lock weight
  - `workflow_manifest`: ComfyUI workflow JSON path

## 3. MaterialX / glTF PBR — Multi-Channel Material Asset Structure

**Core Concept**: MaterialX defines materials as node graphs with typed inputs/outputs. glTF PBR uses a standardized channel model:
- `baseColorTexture` (Albedo)
- `normalTexture` (Normal map)
- `metallicRoughnessTexture` (packed channels)
- `emissiveTexture` (Emission)
- `occlusionTexture` (AO)

**Application to Industrial Sprite (P1-INDUSTRIAL-34A)**:
- Output is NOT a single image but a **structured material bundle** (texture channel tree)
- The manifest must contain a `texture_channels` dict where each entry maps:
  - Channel name → { path, dimensions, bit_depth, color_space, engine_slot }
- Required channels: `albedo`, `normal`, `depth`, `thickness`, `roughness`, `mask`
- Optional channels: `emission`, `sdf_field`, `contour`
- Bundle metadata:
  - `bundle_format`: "mathart" | "gltf_pbr" | "materialx"
  - `target_engine`: "unity_urp_2d" | "godot_4" | "generic"
  - `material_model`: "pbr_metallic" | "pbr_specular" | "toon_lit"
  - `dimensions`: { width, height }

## 4. Design Decisions for Implementation

### Backend Protocol Extension
- Add optional `validate_config(config: dict) -> tuple[dict, list[str]]` to backends
- Returns (validated_config, warnings)
- CLI checks for `validate_config` via duck typing before calling `execute()`
- If not present, config passes through unchanged

### Manifest Polymorphism
- `artifact_family` determines payload structure:
  - `composite` → payload contains `frame_sequence` (time-series)
  - `material_bundle` → payload contains `texture_channels` (channel tree)
- Top-level keys always present: `artifact_family`, `backend_type`, `status`
- `payload` key contains the family-specific structured data

### IPC Contract
- stdout JSON must have: `artifact_family`, `backend_type`, `payload`
- `payload.frame_sequence` for AI anti-flicker
- `payload.texture_channels` for industrial sprite
- No log pollution on stdout
