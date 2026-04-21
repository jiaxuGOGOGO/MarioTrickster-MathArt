"""Artifact Schema — Pixar USD-Inspired Strongly-Typed Asset Contract.

SESSION-064: Paradigm Shift #2 — From Loose output_paths to Typed Artifact Manifests.

This module implements the **Artifact Schema** system inspired by Pixar's
Universal Scene Description (USD) and its validation framework:

    1. Every pipeline output is wrapped in an ``ArtifactManifest`` with
       explicit ``artifact_family`` and ``backend_type`` fields.
    2. Manifests are validated against a schema before acceptance —
       analogous to USD's ``usdchecker``.
    3. Manifests support composition (referencing other manifests) —
       analogous to USD's Composition Arcs.
    4. All manifests are serializable to JSON for persistence and auditing.

The key insight from USD is that **type ambiguity is the root of all
pipeline bugs**. When a function returns ``list[str]`` of file paths,
downstream consumers have no idea what those files represent. By wrapping
outputs in typed manifests, we eliminate this ambiguity entirely.

Architecture::

    ┌─────────────────────────────────────────────────────────┐
    │                    ArtifactManifest                      │
    │                                                         │
    │  artifact_family: "mesh_obj"                            │
    │  backend_type: "dimension_uplift"                       │
    │  version: "1.0.0"                                       │
    │  outputs: {                                             │
    │      "mesh": "/path/to/mesh.obj",                       │
    │      "material": "/path/to/material.mtl",               │
    │  }                                                      │
    │  metadata: {                                            │
    │      "vertex_count": 1024,                              │
    │      "face_count": 2048,                                │
    │  }                                                      │
    │  quality_metrics: {                                     │
    │      "feature_preservation": 0.92,                      │
    │  }                                                      │
    │  references: ["sprite_sheet_manifest_hash"]             │
    │  schema_hash: "sha256:..."                              │
    └─────────────────────────────────────────────────────────┘

References
----------
[1] Pixar, "OpenUSD Schema Validation", openusd.org, 2024.
[2] Pixar, "USD Composition Arcs", openusd.org, 2024.
[3] NVIDIA, "Omniverse Asset Validator", docs.nvidia.com, 2024.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Optional

from mathart.core.backend_types import (
    BackendType,
    backend_type_value,
)


# ---------------------------------------------------------------------------
# Artifact Family Enum
# ---------------------------------------------------------------------------

class ArtifactFamily(Enum):
    """Typed artifact families — the USD Schema equivalent.

    Each family defines what kind of output a backend produces.
    This eliminates the ambiguity of generic ``output_paths`` lists.

    SESSION-073 (P1-MIGRATE-3): Each family now exposes a
    ``required_metadata_keys()`` class method that returns the set of
    metadata keys the family mandates. ``validate_artifact()`` checks
    these keys, enforcing the Pixar USD Schema Compliance pattern.
    """
    SPRITE_SHEET = "sprite_sheet"
    SPRITE_SINGLE = "sprite_single"
    IMAGE_SEQUENCE = "image_sequence"
    MESH_OBJ = "mesh_obj"
    MESH_FBX = "mesh_fbx"
    VAT_BUNDLE = "vat_bundle"
    SHADER_HLSL = "shader_hlsl"
    SHADER_GLSL = "shader_glsl"
    SHADER_GDSHADER = "shader_gdshader"
    ANIMATION_SPINE = "animation_spine"
    ANIMATION_SPRITESHEET = "animation_spritesheet"
    LEVEL_TILEMAP = "level_tilemap"
    LEVEL_WFC = "level_wfc"
    VFX_FLIPBOOK = "vfx_flipbook"
    VFX_FLOWMAP = "vfx_flowmap"
    MATERIAL_BUNDLE = "material_bundle"
    KNOWLEDGE_RULES = "knowledge_rules"
    EVOLUTION_STATE = "evolution_state"
    META_REPORT = "meta_report"
    ENGINE_PLUGIN = "engine_plugin"
    AOT_MODULE = "aot_module"
    CEL_SHADING = "cel_shading"
    DISPLACEMENT_MAP = "displacement_map"
    MOTION_UMR = "motion_umr"
    PHYSICS_3D_MOTION_UMR = "physics_3d_motion_umr"
    COMPOSITE = "composite"
    # SESSION-084 (P1-AI-2D): Typed anti-flicker render report for externally
    # serialized ComfyUI workflow_api payloads and temporal evidence.
    ANTI_FLICKER_REPORT = "anti_flicker_report"
    # SESSION-075 (P1-DISTILL-1B): Benchmark report family for CPU/GPU
    # performance evidence and telemetry closure.
    BENCHMARK_REPORT = "benchmark_report"
    # SESSION-074 (P1-MIGRATE-2): Evolution report family.
    # Every migrated evolution bridge must produce an EVOLUTION_REPORT
    # manifest with mandatory metadata keys enforcing the Pixar USD
    # Schema Compliance pattern for evolution domain outputs.
    EVOLUTION_REPORT = "evolution_report"
    # SESSION-083 (P1-B4-1): RL training report family for Gymnasium /
    # rollout execution evidence and reproducible micro-batch telemetry.
    TRAINING_REPORT = "training_report"
    # SESSION-091 (P1-AI-2E): Motion-adaptive keyframe plan family.
    # Produced by MotionAdaptiveKeyframeBackend: per-frame nonlinearity
    # scores, selected keyframe indices, and SparseCtrl end_percent mapping.
    KEYFRAME_PLAN = "keyframe_plan"

    @classmethod
    def required_metadata_keys(cls, family_value: str) -> frozenset[str]:
        """Return the set of metadata keys mandated by a given family.

        SESSION-073 (P1-MIGRATE-3): Explicit schema contract per family.
        The ``PHYSICS_3D_MOTION_UMR`` family enforces ``physics_solver``,
        ``contact_manifold_count``, ``frame_count``, ``fps``, and
        ``joint_channel_schema``. Other families derive their requirements
        from the ``FAMILY_SCHEMAS`` registry.
        """
        _FAMILY_REQUIRED_METADATA: dict[str, frozenset[str]] = {
            cls.PHYSICS_3D_MOTION_UMR.value: frozenset({
                "physics_solver",
                "contact_manifold_count",
                "frame_count",
                "fps",
                "joint_channel_schema",
            }),
            cls.MOTION_UMR.value: frozenset({
                "frame_count",
                "fps",
                "joint_channel_schema",
            }),
            # SESSION-075 (P1-DISTILL-1B): Mandatory benchmark metadata so
            # downstream distillation and CI audits can compare device lanes
            # without guessing field names.
            cls.BENCHMARK_REPORT.value: frozenset({
                "solver_type",
                "frame_count",
                "wall_time_ms",
                "particles_per_second",
                "gpu_device_name",
                "speedup_ratio",
                "cpu_gpu_max_drift",
            }),
            # SESSION-074 (P1-MIGRATE-2): Mandatory metadata for evolution
            # reports.  Every evolution backend must declare how many cycles
            # it ran, the best fitness achieved, and how many knowledge
            # rules were distilled.  This prevents silent schema drift
            # across the 20+ evolution bridges.
            cls.EVOLUTION_REPORT.value: frozenset({
                "cycle_count",
                "best_fitness",
                "knowledge_rules_distilled",
            }),
            # SESSION-083 (P1-B4-1): Mandatory metadata for RL rollout /
            # training reports so downstream audit code can compare lanes
            # without inferring ad-hoc field names.
            cls.TRAINING_REPORT.value: frozenset({
                "mean_reward",
                "episode_length",
                "episodes_run",
                "trainer_mode",
            }),
            cls.ANTI_FLICKER_REPORT.value: frozenset({
                "preset_name",
                "frame_count",
                "fps",
                "keyframe_count",
                "guides_locked",
                "identity_lock_enabled",
            }),
            # SESSION-091 (P1-AI-2E): Mandatory metadata for keyframe plans
            # so downstream SparseCtrl integration and quality audits can
            # validate coverage without inferring field names.
            cls.KEYFRAME_PLAN.value: frozenset({
                "frame_count",
                "fps",
                "keyframe_count",
                "min_gap",
                "max_gap",
                "mean_nonlinearity",
                "contact_events_captured",
            }),
        }
        return _FAMILY_REQUIRED_METADATA.get(family_value, frozenset())


# ---------------------------------------------------------------------------
# Validation Error
# ---------------------------------------------------------------------------

class ArtifactValidationError(Exception):
    """Raised when an artifact manifest fails schema validation.

    Analogous to USD's validation error — the artifact is rejected
    and the pipeline reports the specific validation failure.
    """

    def __init__(self, field_name: str, message: str) -> None:
        self.field_name = field_name
        self.message = message
        super().__init__(f"[ArtifactValidation:{field_name}] {message}")


# ---------------------------------------------------------------------------
# Artifact Manifest
# ---------------------------------------------------------------------------

@dataclass
class ArtifactManifest:
    """Strongly-typed manifest for a pipeline output artifact.

    Every backend must return an ``ArtifactManifest`` instead of loose
    file paths. This is the USD Prim equivalent — it carries type
    information, metadata, quality metrics, and composition references.

    Attributes
    ----------
    artifact_family : str
        The type of artifact (must match an ``ArtifactFamily`` value).
    backend_type : str
        The backend that produced this artifact.
    version : str
        Semantic version of the artifact format.
    session_id : str
        Session that produced this artifact.
    timestamp : float
        Unix timestamp of artifact creation.
    outputs : dict[str, str]
        Named output files (key=role, value=path).
    metadata : dict[str, Any]
        Backend-specific metadata (vertex counts, frame counts, etc.).
    quality_metrics : dict[str, float]
        Quality scores from the producing backend's evaluation.
    references : list[str]
        Hashes of other manifests this artifact depends on
        (USD Composition Arc equivalent).
    tags : list[str]
        Free-form tags for categorization and search.
    schema_hash : str
        SHA-256 hash of the manifest content for integrity verification.
    """
    artifact_family: str
    backend_type: str | BackendType
    version: str = "1.0.0"
    session_id: str = "SESSION-064"
    timestamp: float = 0.0
    outputs: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    quality_metrics: dict[str, float] = field(default_factory=dict)
    references: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    schema_hash: str = ""

    def __post_init__(self) -> None:
        self.backend_type = backend_type_value(self.backend_type)
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if not self.schema_hash:
            self.schema_hash = self.compute_hash()

    def compute_hash(self) -> str:
        """Compute SHA-256 hash of the manifest content."""
        canonical = json.dumps(
            {
                "artifact_family": self.artifact_family,
                "backend_type": self.backend_type,
                "version": self.version,
                "session_id": self.session_id,
                "outputs": dict(sorted(self.outputs.items())),
                "metadata": dict(sorted(
                    (k, v) for k, v in self.metadata.items()
                    if not isinstance(v, (dict, list))
                )),
            },
            sort_keys=True,
            ensure_ascii=True,
        )
        return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary for JSON export."""
        return {
            "artifact_family": self.artifact_family,
            "backend_type": self.backend_type,
            "version": self.version,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "outputs": self.outputs,
            "metadata": self.metadata,
            "quality_metrics": self.quality_metrics,
            "references": self.references,
            "tags": self.tags,
            "schema_hash": self.schema_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArtifactManifest":
        """Deserialize from a dictionary."""
        return cls(
            artifact_family=data["artifact_family"],
            backend_type=data["backend_type"],
            version=data.get("version", "1.0.0"),
            session_id=data.get("session_id", "SESSION-064"),
            timestamp=data.get("timestamp", 0.0),
            outputs=data.get("outputs", {}),
            metadata=data.get("metadata", {}),
            quality_metrics=data.get("quality_metrics", {}),
            references=data.get("references", []),
            tags=data.get("tags", []),
            schema_hash=data.get("schema_hash", ""),
        )

    def to_ipc_payload(
        self,
        *,
        manifest_path: str | Path | None = None,
        requested_backend: str | None = None,
        status: str = "ok",
    ) -> dict[str, Any]:
        """Return a machine-consumable IPC payload for stdout delivery.

        The payload preserves the strongly typed manifest contract while adding
        absolute paths and a few transport-level fields that external callers
        such as Unity or subprocess harnesses need for direct deserialization.

        SESSION-068 Extension — Polymorphic ``payload`` key
        ---------------------------------------------------
        When ``metadata["payload"]`` is present, it is promoted to a top-level
        ``payload`` key in the IPC envelope. This enables downstream consumers
        to discriminate between:

        * **frame_sequence** (anti-flicker temporal backend) — OTIO-inspired
          time-series with per-frame path, role, and coherence score.
        * **texture_channels** (industrial sprite backend) — MaterialX/glTF
          PBR-inspired multi-channel material bundle with engine slot bindings.

        The ``backend_type`` field serves as the discriminator tag.
        """
        resolved_manifest_path = (
            str(Path(manifest_path).resolve()) if manifest_path else None
        )
        artifact_paths = {
            role: str(Path(path).resolve())
            for role, path in self.outputs.items()
        }

        # --- Build base envelope ---
        envelope: dict[str, Any] = {
            "status": status,
            "requested_backend": requested_backend or self.backend_type,
            "resolved_backend": self.backend_type,
            "artifact_family": self.artifact_family,
            "backend_type": self.backend_type,
            "version": self.version,
            "session_id": self.session_id,
            "schema_hash": self.schema_hash,
            "manifest_path": resolved_manifest_path,
            "artifact_paths": artifact_paths,
            "metadata": self.metadata,
            "quality_metrics": self.quality_metrics,
            "references": self.references,
            "tags": self.tags,
        }

        # --- Promote polymorphic payload to top level (SESSION-068) ---
        raw_payload = self.metadata.get("payload")
        if isinstance(raw_payload, dict):
            envelope["payload"] = raw_payload

        return envelope

    def save(self, path: str | Path) -> None:
        """Save manifest to a JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: str | Path) -> "ArtifactManifest":
        """Load manifest from a JSON file."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Schema Validation Rules
# ---------------------------------------------------------------------------

# Required fields per artifact family (analogous to USD Schema definitions)
FAMILY_SCHEMAS: dict[str, dict[str, Any]] = {
    ArtifactFamily.MESH_OBJ.value: {
        "required_outputs": ["mesh"],
        "required_metadata": ["vertex_count", "face_count"],
        "required_quality": [],
    },
    ArtifactFamily.SPRITE_SHEET.value: {
        "required_outputs": ["spritesheet"],
        "required_metadata": ["frame_count", "frame_width", "frame_height"],
        "required_quality": [],
    },
    ArtifactFamily.SPRITE_SINGLE.value: {
        "required_outputs": ["image"],
        "required_metadata": ["width", "height", "channels"],
        "required_quality": [],
    },
    ArtifactFamily.IMAGE_SEQUENCE.value: {
        "required_outputs": ["sequence_dir", "metadata_json"],
        "required_metadata": ["frame_count", "frame_width", "frame_height", "fps"],
        "required_quality": [],
    },
    ArtifactFamily.VAT_BUNDLE.value: {
        "required_outputs": ["position_tex", "manifest"],
        "required_metadata": ["frame_count", "vertex_count"],
        "required_quality": [],
    },
    ArtifactFamily.SHADER_HLSL.value: {
        "required_outputs": ["shader_source"],
        "required_metadata": ["shader_type"],
        "required_quality": [],
    },
    ArtifactFamily.ANIMATION_SPINE.value: {
        "required_outputs": ["spine_json"],
        "required_metadata": ["bone_count", "animation_count"],
        "required_quality": [],
    },
    ArtifactFamily.LEVEL_TILEMAP.value: {
        "required_outputs": ["tilemap_json"],
        "required_metadata": ["width", "height", "tile_count"],
        "required_quality": [],
    },
    ArtifactFamily.VFX_FLIPBOOK.value: {
        "required_outputs": ["atlas"],
        "required_metadata": ["frame_count", "atlas_width", "atlas_height"],
        "required_quality": [],
    },
    ArtifactFamily.VFX_FLOWMAP.value: {
        "required_outputs": ["flowmap"],
        "required_metadata": ["encoding"],
        "required_quality": [],
    },
    ArtifactFamily.MATERIAL_BUNDLE.value: {
        "required_outputs": ["albedo"],
        "required_metadata": ["channels"],
        "required_quality": [],
    },
    ArtifactFamily.KNOWLEDGE_RULES.value: {
        "required_outputs": ["rules_file"],
        "required_metadata": ["rule_count"],
        "required_quality": [],
    },
    ArtifactFamily.EVOLUTION_STATE.value: {
        "required_outputs": ["state_file"],
        "required_metadata": ["cycle_count"],
        "required_quality": [],
    },
    ArtifactFamily.META_REPORT.value: {
        "required_outputs": ["report_file"],
        "required_metadata": ["niche_count"],
        "required_quality": [],
    },
    ArtifactFamily.ENGINE_PLUGIN.value: {
        "required_outputs": ["plugin_source"],
        "required_metadata": ["engine", "plugin_type"],
        "required_quality": [],
    },
    ArtifactFamily.CEL_SHADING.value: {
        "required_outputs": ["shader_source"],
        "required_metadata": ["shader_type", "outline_method"],
        "required_quality": [],
    },
    ArtifactFamily.DISPLACEMENT_MAP.value: {
        "required_outputs": ["displacement"],
        "required_metadata": ["resolution", "depth_range"],
        "required_quality": [],
    },
    ArtifactFamily.MOTION_UMR.value: {
        "required_outputs": ["motion_clip_json"],
        "required_metadata": ["frame_count", "fps", "joint_channel_schema"],
        "required_quality": [],
    },
    # SESSION-071 (P1-XPBD-3): 3D-enriched UMR clip family. Inherits motion_umr
    # required fields and adds 3D solver provenance metadata so downstream
    # distillation (P1-DISTILL-1A) can discriminate physics-driven clips.
    ArtifactFamily.PHYSICS_3D_MOTION_UMR.value: {
        "required_outputs": ["motion_clip_json"],
        "required_metadata": [
            "frame_count",
            "fps",
            "joint_channel_schema",
            "physics_solver",
            "contact_manifold_count",
        ],
        "required_quality": [],
    },
    ArtifactFamily.COMPOSITE.value: {
        "required_outputs": [],
        "required_metadata": [],
        "required_quality": [],
    },
    ArtifactFamily.ANTI_FLICKER_REPORT.value: {
        "required_outputs": ["workflow_payload", "preset_asset", "temporal_report"],
        "required_metadata": [
            "preset_name",
            "frame_count",
            "fps",
            "keyframe_count",
            "guides_locked",
            "identity_lock_enabled",
        ],
        "required_quality": ["temporal_stability_score", "frame_count", "keyframe_count"],
    },
    # SESSION-075 (P1-DISTILL-1B): Benchmark evidence manifest.
    ArtifactFamily.BENCHMARK_REPORT.value: {
        "required_outputs": ["report_file"],
        "required_metadata": [
            "solver_type",
            "frame_count",
            "wall_time_ms",
            "particles_per_second",
            "gpu_device_name",
            "speedup_ratio",
            "cpu_gpu_max_drift",
        ],
        "required_quality": [],
    },
    # SESSION-074 (P1-MIGRATE-2): Evolution report schema.
    ArtifactFamily.EVOLUTION_REPORT.value: {
        "required_outputs": ["report_file"],
        "required_metadata": ["cycle_count", "best_fitness", "knowledge_rules_distilled"],
        "required_quality": [],
    },
    # SESSION-083 (P1-B4-1): RL training / rollout report schema.
    ArtifactFamily.TRAINING_REPORT.value: {
        "required_outputs": ["report_file"],
        "required_metadata": [
            "mean_reward",
            "episode_length",
            "episodes_run",
            "trainer_mode",
            "reference_state",
            "obs_dim",
            "act_dim",
        ],
        "required_quality": [],
    },
}


def validate_artifact(manifest: ArtifactManifest) -> list[str]:
    """Validate an artifact manifest against its family schema.

    Returns a list of validation errors (empty if valid).
    This is the ``usdchecker`` equivalent.

    Parameters
    ----------
    manifest : ArtifactManifest
        The manifest to validate.

    Returns
    -------
    list[str]
        List of validation error messages. Empty means valid.
    """
    errors: list[str] = []

    # 1. Check artifact_family is known
    valid_families = {f.value for f in ArtifactFamily}
    if manifest.artifact_family not in valid_families:
        errors.append(
            f"Unknown artifact_family: {manifest.artifact_family!r}. "
            f"Valid: {sorted(valid_families)}"
        )
        return errors  # Can't validate further

    # 2. Check backend_type is non-empty
    if not manifest.backend_type:
        errors.append("backend_type must not be empty")

    # 3. Check schema-specific requirements
    schema = FAMILY_SCHEMAS.get(manifest.artifact_family, {})

    for req_output in schema.get("required_outputs", []):
        if req_output not in manifest.outputs:
            errors.append(
                f"Missing required output {req_output!r} for family "
                f"{manifest.artifact_family!r}"
            )

    for req_meta in schema.get("required_metadata", []):
        if req_meta not in manifest.metadata:
            errors.append(
                f"Missing required metadata {req_meta!r} for family "
                f"{manifest.artifact_family!r}"
            )

    for req_quality in schema.get("required_quality", []):
        if req_quality not in manifest.quality_metrics:
            errors.append(
                f"Missing required quality metric {req_quality!r} for family "
                f"{manifest.artifact_family!r}"
            )

    # 4. Check hash integrity
    expected_hash = manifest.compute_hash()
    if manifest.schema_hash and manifest.schema_hash != expected_hash:
        errors.append(
            f"Schema hash mismatch: expected {expected_hash}, "
            f"got {manifest.schema_hash}"
        )

    # 5. SESSION-073 (P1-MIGRATE-3): required_metadata_keys() enforcement.
    required_keys = ArtifactFamily.required_metadata_keys(manifest.artifact_family)
    for rk in sorted(required_keys):
        if rk not in manifest.metadata:
            errors.append(
                f"Missing required metadata key {rk!r} mandated by "
                f"ArtifactFamily.required_metadata_keys() for family "
                f"{manifest.artifact_family!r}"
            )

    # 6. SESSION-073 (P1-MIGRATE-3): physics3d_telemetry sidecar deep
    #    validation — Borgmon / Prometheus time-series model compliance.
    if manifest.artifact_family == ArtifactFamily.PHYSICS_3D_MOTION_UMR.value:
        telemetry = manifest.metadata.get("physics3d_telemetry")
        if telemetry is not None:
            _tel_required = {"solver_wall_time_ms", "contact_count", "frame_count", "fps"}
            for tk in sorted(_tel_required):
                if tk not in telemetry:
                    errors.append(
                        f"physics3d_telemetry missing required key {tk!r}"
                    )
            fc = telemetry.get("frame_count", 0)
            for arr_key in ("solver_wall_time_ms", "contact_count"):
                arr = telemetry.get(arr_key)
                if isinstance(arr, (list, tuple)) and len(arr) != fc:
                    errors.append(
                        f"physics3d_telemetry[{arr_key!r}] length {len(arr)} "
                        f"!= frame_count {fc}"
                    )
            ccd_arr = telemetry.get("ccd_sweep_count")
            if ccd_arr is not None and isinstance(ccd_arr, (list, tuple)) and len(ccd_arr) != fc:
                errors.append(
                    f"physics3d_telemetry['ccd_sweep_count'] length {len(ccd_arr)} "
                    f"!= frame_count {fc}"
                )

    # 7. SESSION-078 (P1-DISTILL-4): optional cognition-sidecar validation for
    #    motion UMR artifacts. When attached, the sidecar must remain a strongly
    #    typed continuous-trace payload rather than an opaque blob.
    if manifest.artifact_family == ArtifactFamily.MOTION_UMR.value:
        telemetry = manifest.metadata.get("cognitive_telemetry")
        if telemetry is not None:
            _required = {"schema_version", "frame_count", "fps", "summary", "traces"}
            for tk in sorted(_required):
                if tk not in telemetry:
                    errors.append(
                        f"cognitive_telemetry missing required key {tk!r}"
                    )
            traces = telemetry.get("traces") if isinstance(telemetry, dict) else None
            if not isinstance(traces, list):
                errors.append("cognitive_telemetry.traces must be a list")
            elif traces:
                trace_required = {
                    "frame_index",
                    "time",
                    "phase",
                    "phase_kind",
                    "root_position",
                    "root_velocity",
                    "root_speed",
                    "root_jerk",
                    "contact_expectation",
                }
                head = traces[0]
                if isinstance(head, dict):
                    for tk in sorted(trace_required):
                        if tk not in head:
                            errors.append(
                                f"cognitive_telemetry.traces[0] missing required key {tk!r}"
                            )
                else:
                    errors.append("cognitive_telemetry.traces[0] must be a dict")

    return errors


def validate_artifact_strict(
    manifest: "ArtifactManifest",
    *,
    min_schema_version: str = "",
) -> list[str]:
    """Strict validation with schema version floor enforcement.

    SESSION-073 (P1-MIGRATE-3): Used by CI guard tests to reject manifests
    whose ``version`` is below ``min_schema_version``. This prevents silent
    schema downgrade — the Pixar USD ``usdchecker`` compliance pattern.

    Parameters
    ----------
    manifest : ArtifactManifest
        The manifest to validate.
    min_schema_version : str
        Minimum acceptable manifest version (semver string).
        When empty, no version floor is enforced.

    Returns
    -------
    list[str]
        List of validation error messages. Empty means valid.
    """
    errors = validate_artifact(manifest)
    if min_schema_version and manifest.version < min_schema_version:
        errors.append(
            f"Schema version downgrade blocked: manifest version "
            f"{manifest.version!r} < required minimum {min_schema_version!r}"
        )
    return errors


# ---------------------------------------------------------------------------
# Composite Manifest Builder
# ---------------------------------------------------------------------------

class CompositeManifestBuilder:
    """Builder for composite manifests that reference multiple sub-artifacts.

    This implements USD's Composition Arcs pattern — a composite manifest
    can reference and compose multiple sub-manifests.
    """

    def __init__(
        self,
        name: str,
        backend_type: str | BackendType = BackendType.COMPOSITE,
        session_id: str = "SESSION-064",
    ) -> None:
        self._name = name
        self._backend_type = backend_type_value(backend_type)
        self._session_id = session_id
        self._sub_manifests: list[ArtifactManifest] = []
        self._extra_metadata: dict[str, Any] = {}

    def add(self, manifest: ArtifactManifest) -> "CompositeManifestBuilder":
        """Add a sub-manifest to the composite."""
        self._sub_manifests.append(manifest)
        return self

    def with_metadata(self, key: str, value: Any) -> "CompositeManifestBuilder":
        """Add extra metadata to the composite."""
        self._extra_metadata[key] = value
        return self

    def build(self) -> ArtifactManifest:
        """Build the composite manifest."""
        references = [m.schema_hash for m in self._sub_manifests]
        all_families = list({m.artifact_family for m in self._sub_manifests})
        all_outputs: dict[str, str] = {}
        for m in self._sub_manifests:
            for key, val in m.outputs.items():
                prefixed_key = f"{m.backend_type}_{key}"
                all_outputs[prefixed_key] = val

        combined_quality: dict[str, float] = {}
        for m in self._sub_manifests:
            for key, val in m.quality_metrics.items():
                combined_quality[f"{m.backend_type}_{key}"] = val

        metadata = {
            "composite_name": self._name,
            "sub_artifact_count": len(self._sub_manifests),
            "sub_families": all_families,
            **self._extra_metadata,
        }

        return ArtifactManifest(
            artifact_family=ArtifactFamily.COMPOSITE.value,
            backend_type=self._backend_type,
            version="1.0.0",
            session_id=self._session_id,
            outputs=all_outputs,
            metadata=metadata,
            quality_metrics=combined_quality,
            references=references,
            tags=["composite"],
        )


# ---------------------------------------------------------------------------
# Pytest Integration
# ---------------------------------------------------------------------------

def test_artifact_manifest_creation():
    """ArtifactManifest can be created with required fields."""
    m = ArtifactManifest(
        artifact_family=ArtifactFamily.MESH_OBJ.value,
        backend_type="dimension_uplift",
        outputs={"mesh": "/tmp/test.obj"},
        metadata={"vertex_count": 100, "face_count": 200},
    )
    assert m.artifact_family == "mesh_obj"
    assert m.backend_type == "dimension_uplift"
    assert m.schema_hash.startswith("sha256:")
    assert m.timestamp > 0


def test_artifact_manifest_validation_pass():
    """Valid manifest passes validation."""
    m = ArtifactManifest(
        artifact_family=ArtifactFamily.MESH_OBJ.value,
        backend_type="dimension_uplift",
        outputs={"mesh": "/tmp/test.obj"},
        metadata={"vertex_count": 100, "face_count": 200},
    )
    errors = validate_artifact(m)
    assert errors == []


def test_artifact_manifest_validation_fail():
    """Invalid manifest fails validation with specific errors."""
    m = ArtifactManifest(
        artifact_family=ArtifactFamily.MESH_OBJ.value,
        backend_type="dimension_uplift",
        outputs={},  # Missing required 'mesh' output
        metadata={},  # Missing required metadata
    )
    errors = validate_artifact(m)
    assert len(errors) >= 2
    assert any("mesh" in e for e in errors)
    assert any("vertex_count" in e or "face_count" in e for e in errors)


def test_artifact_manifest_serialization():
    """ArtifactManifest round-trips through JSON."""
    m = ArtifactManifest(
        artifact_family=ArtifactFamily.SPRITE_SHEET.value,
        backend_type="motion_2d",
        outputs={"spritesheet": "/tmp/sheet.png"},
        metadata={"frame_count": 8, "frame_width": 32, "frame_height": 32},
        quality_metrics={"diversity": 0.85},
    )
    data = m.to_dict()
    m2 = ArtifactManifest.from_dict(data)
    assert m2.artifact_family == m.artifact_family
    assert m2.outputs == m.outputs
    assert m2.quality_metrics == m.quality_metrics


def test_composite_manifest_builder():
    """CompositeManifestBuilder composes multiple sub-manifests."""
    mesh = ArtifactManifest(
        artifact_family=ArtifactFamily.MESH_OBJ.value,
        backend_type="dim_uplift",
        outputs={"mesh": "/tmp/mesh.obj"},
        metadata={"vertex_count": 100, "face_count": 200},
    )
    shader = ArtifactManifest(
        artifact_family=ArtifactFamily.SHADER_HLSL.value,
        backend_type="cel_shading",
        outputs={"shader_source": "/tmp/cel.hlsl"},
        metadata={"shader_type": "cel"},
    )
    composite = (
        CompositeManifestBuilder("character_pack", session_id="SESSION-064")
        .add(mesh)
        .add(shader)
        .with_metadata("character_name", "mario")
        .build()
    )
    assert composite.artifact_family == ArtifactFamily.COMPOSITE.value
    assert len(composite.references) == 2
    assert composite.metadata["sub_artifact_count"] == 2
