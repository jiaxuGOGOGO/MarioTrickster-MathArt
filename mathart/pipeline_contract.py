"""Pipeline Contract Enforcement — Data-Oriented Design for the MarioTrickster-MathArt trunk.

SESSION-040: CLI Pipeline Contract & End-to-End Determinism (攻坚战役三)

This module implements the strict data contract layer inspired by:

- **Mike Acton (CppCon 2014):** "The transformation of data is the only purpose of any program."
  All pipeline functions must accept and return canonical data shapes. Any bypass is a bug.

- **Pixar USD Schema Validation:** Validation as Quality Contract — inspectable, mergeable, automatable.
  Every pipeline output must pass schema validation before acceptance.

- **Glenn Fiedler (Gaffer on Games):** Deterministic lockstep — same inputs produce bit-identical outputs.
  SHA-256 hash seals guarantee pipeline reproducibility.

The ``UMR_Context`` frozen dataclass is the single source of truth for a pipeline run.
All entrypoints (``produce_character_pack``, CLI tools, exporters) must construct and pass
a ``UMR_Context`` rather than ad-hoc parameter bags. The ``PipelineContractGuard`` enforces
this at runtime with fail-fast semantics: any attempt to bypass the contract raises
``PipelineContractError`` immediately.

References
----------
[1] Mike Acton, "Data-Oriented Design and C++", CppCon 2014.
[2] NVIDIA Omniverse, "USD Validation — VFI Guide", 2026.
[3] Glenn Fiedler, "Deterministic Lockstep", Gaffer on Games, 2014.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional


# ── Custom Exception ─────────────────────────────────────────────────────────


class PipelineContractError(Exception):
    """Raised when a pipeline contract is violated.

    This is a fail-fast error: the pipeline must not silently degrade or
    fall back to legacy paths. If the contract is broken, the run is aborted
    immediately with a clear diagnostic message.

    Attributes
    ----------
    violation_type : str
        Category of the violation (e.g., ``"bypass_detected"``, ``"missing_context"``,
        ``"hash_mismatch"``, ``"legacy_path_invoked"``).
    detail : str
        Human-readable explanation of what went wrong.
    """

    def __init__(self, violation_type: str, detail: str) -> None:
        self.violation_type = violation_type
        self.detail = detail
        super().__init__(f"[PipelineContractError:{violation_type}] {detail}")


# ── Immutable Pipeline Context ───────────────────────────────────────────────


@dataclass(frozen=True)
class UMR_Context:
    """Immutable pipeline execution context — the single source of truth.

    Inspired by Mike Acton's Data-Oriented Design: this frozen dataclass
    captures every parameter needed for a deterministic pipeline run.
    Once created, it cannot be modified. To change parameters, create a
    new context via ``dataclasses.replace()``.

    The context is hashable and serializable. Its ``context_hash`` property
    produces a deterministic SHA-256 fingerprint of the configuration, which
    is used downstream by ``UMR_Auditor`` to seal pipeline outputs.

    Attributes
    ----------
    pipeline_version : str
        Semantic version of the pipeline contract (e.g., ``"0.31.0"``).
    session_id : str
        Identifier of the session that created this context.
    random_seed : int
        Master random seed for deterministic reproduction.
    character_name : str
        Name of the character being produced.
    preset : str
        Character preset identifier.
    states : tuple[str, ...]
        Ordered tuple of animation states to produce.
    frame_width : int
        Width of each output frame in pixels.
    frame_height : int
        Height of each output frame in pixels.
    fps : int
        Target frames per second.
    head_units : float
        Character proportions in head units.
    frames_per_state : int
        Default number of frames per animation state.
    state_frames : tuple[tuple[str, int], ...]
        Per-state frame count overrides as sorted key-value pairs.
    enable_physics : bool
        Whether the physics compliance projector is active.
    enable_biomechanics : bool
        Whether the biomechanics grounding projector is active.
    physics_stiffness : float
        Global stiffness scale for the PD controller.
    physics_damping : float
        Global damping scale for the PD controller.
    compliance_alpha : float
        Compliance blending factor for the compliant PD mode.
    biomechanics_zmp_strength : float
        ZMP correction strength for the biomechanics projector.
    enable_dither : bool
        Whether Floyd-Steinberg dithering is applied.
    enable_outline : bool
        Whether adaptive outlines are rendered.
    enable_lighting : bool
        Whether pseudo-normal lighting is applied.
    convergence_bridge : tuple[tuple[str, Any], ...]
        Sorted key-value pairs from the Layer 3 convergence bridge.
    extra : tuple[tuple[str, Any], ...]
        Additional frozen metadata for extensibility.
    """

    pipeline_version: str = "0.31.0"
    session_id: str = "SESSION-040"
    random_seed: int = 42
    character_name: str = ""
    preset: str = "mario"
    # SESSION-162: 单一真理源来自 MotionStateLaneRegistry；用 default_factory 推迟到实例化时再求值，
    # 避免 dataclass 装饰器在模块导入阶段被 mathart.animation.__init__ 的可选依赖（如 networkx）误伤。
    states: tuple[str, ...] = field(default_factory=lambda: tuple(
        __import__("mathart.animation.unified_gait_blender", fromlist=["get_motion_lane_registry"]).get_motion_lane_registry().names()
    ))
    frame_width: int = 192
    frame_height: int = 192
    fps: int = 12
    head_units: float = 3.0
    frames_per_state: int = 8
    state_frames: tuple[tuple[str, int], ...] = ()
    enable_physics: bool = True
    enable_biomechanics: bool = True
    physics_stiffness: float = 1.0
    physics_damping: float = 1.0
    compliance_alpha: float = 0.6
    biomechanics_zmp_strength: float = 0.3
    enable_dither: bool = True
    enable_outline: bool = True
    enable_lighting: bool = True
    convergence_bridge: tuple[tuple[str, Any], ...] = ()
    extra: tuple[tuple[str, Any], ...] = ()

    @property
    def context_hash(self) -> str:
        """Compute a deterministic SHA-256 hash of this context.

        The hash is derived from a canonical JSON serialization of all fields.
        Because the dataclass is frozen and all mutable containers are replaced
        with tuples, the serialization is deterministic.
        """
        canonical = json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary for JSON export."""
        return {
            "pipeline_version": self.pipeline_version,
            "session_id": self.session_id,
            "random_seed": self.random_seed,
            "character_name": self.character_name,
            "preset": self.preset,
            "states": list(self.states),
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "fps": self.fps,
            "head_units": self.head_units,
            "frames_per_state": self.frames_per_state,
            "state_frames": {k: v for k, v in self.state_frames},
            "enable_physics": self.enable_physics,
            "enable_biomechanics": self.enable_biomechanics,
            "physics_stiffness": self.physics_stiffness,
            "physics_damping": self.physics_damping,
            "compliance_alpha": self.compliance_alpha,
            "biomechanics_zmp_strength": self.biomechanics_zmp_strength,
            "enable_dither": self.enable_dither,
            "enable_outline": self.enable_outline,
            "enable_lighting": self.enable_lighting,
            "convergence_bridge": {k: v for k, v in self.convergence_bridge},
            "extra": {k: v for k, v in self.extra},
        }

    @classmethod
    def from_character_spec(cls, spec: Any, session_id: str = "SESSION-040",
                            convergence_bridge: Optional[dict[str, Any]] = None,
                            pipeline_version: str = "0.31.0") -> "UMR_Context":
        """Construct a UMR_Context from a CharacterSpec instance.

        This is the canonical bridge between the existing ``CharacterSpec``
        dataclass and the new immutable context contract. All downstream
        pipeline functions should receive the resulting ``UMR_Context``
        rather than the mutable ``CharacterSpec`` directly.
        """
        bridge_items = tuple(sorted((convergence_bridge or {}).items()))
        state_frame_items = tuple(sorted(spec.state_frames.items())) if spec.state_frames else ()

        return cls(
            pipeline_version=pipeline_version,
            session_id=session_id,
            random_seed=getattr(spec, "seed", 42) if hasattr(spec, "seed") else 42,
            character_name=spec.name,
            preset=spec.preset,
            states=tuple(spec.states),
            frame_width=spec.frame_width,
            frame_height=spec.frame_height,
            fps=spec.fps,
            head_units=spec.head_units,
            frames_per_state=spec.frames_per_state,
            state_frames=state_frame_items,
            enable_physics=spec.enable_physics,
            enable_biomechanics=spec.enable_biomechanics,
            physics_stiffness=spec.physics_stiffness,
            physics_damping=spec.physics_damping,
            compliance_alpha=getattr(spec, "compliance_alpha", 0.6),
            biomechanics_zmp_strength=spec.biomechanics_zmp_strength,
            enable_dither=spec.enable_dither,
            enable_outline=spec.enable_outline,
            enable_lighting=spec.enable_lighting,
            convergence_bridge=bridge_items,
        )


# ── Contract Guard ───────────────────────────────────────────────────────────


class PipelineContractGuard:
    """Runtime enforcer for the pipeline data contract.

    The guard is instantiated once per pipeline run and validates every
    significant operation against the frozen ``UMR_Context``. It implements
    fail-fast semantics: violations raise ``PipelineContractError`` immediately
    rather than producing silently degraded output.

    This design follows Mike Acton's DOD principle: if the data shape is wrong,
    the program has no business continuing.
    """

    def __init__(self, context: UMR_Context) -> None:
        if not isinstance(context, UMR_Context):
            raise PipelineContractError(
                "missing_context",
                f"PipelineContractGuard requires a UMR_Context, got {type(context).__name__}."
            )
        self._context = context
        self._violations: list[dict[str, str]] = []

    @property
    def context(self) -> UMR_Context:
        return self._context

    @property
    def violations(self) -> list[dict[str, str]]:
        return list(self._violations)

    def require_umr_context(self, obj: Any, caller: str = "") -> None:
        """Assert that *obj* is a valid UMR_Context. Fail-fast if not."""
        if not isinstance(obj, UMR_Context):
            violation = {
                "type": "missing_context",
                "caller": caller,
                "received": type(obj).__name__,
            }
            self._violations.append(violation)
            raise PipelineContractError(
                "missing_context",
                f"{caller}: expected UMR_Context, got {type(obj).__name__}."
            )

    def reject_legacy_bypass(self, generator_mode: str, caller: str = "") -> None:
        """Reject any attempt to use the legacy_pose_adapter path.

        The legacy adapter was a transitional bridge. As of SESSION-040,
        all states must go through phase-driven or transient-phase generators
        that produce ``UnifiedMotionFrame`` natively. Invoking the legacy
        path is now a contract violation.
        """
        if generator_mode == "legacy_pose_adapter":
            violation = {
                "type": "legacy_path_invoked",
                "caller": caller,
                "generator_mode": generator_mode,
            }
            self._violations.append(violation)
            raise PipelineContractError(
                "legacy_path_invoked",
                f"{caller}: legacy_pose_adapter is forbidden under the SESSION-040 contract. "
                f"All states must use phase-driven or transient-phase generators."
            )

    def validate_node_order(self, expected: list[str], actual: list[str], caller: str = "") -> None:
        """Validate that pipeline nodes executed in the expected order."""
        if actual != expected:
            violation = {
                "type": "node_order_violation",
                "caller": caller,
                "expected": expected,
                "actual": actual,
            }
            self._violations.append(violation)
            raise PipelineContractError(
                "node_order_violation",
                f"{caller}: pipeline node order mismatch. "
                f"Expected {expected}, got {actual}."
            )

    def validate_required_fields(self, frame_dict: dict[str, Any],
                                  required: tuple[str, ...] = (
                                      "time", "phase", "root_transform",
                                      "joint_local_rotations", "contact_tags",
                                  ),
                                  caller: str = "") -> None:
        """Validate that a serialized frame contains all required UMR fields."""
        missing = [f for f in required if f not in frame_dict]
        if missing:
            violation = {
                "type": "missing_fields",
                "caller": caller,
                "missing": missing,
            }
            self._violations.append(violation)
            raise PipelineContractError(
                "missing_fields",
                f"{caller}: UMR frame missing required fields: {missing}."
            )

    def validate_hash_seal(self, expected_hash: str, actual_hash: str, caller: str = "") -> None:
        """Validate that a pipeline output hash matches the expected golden master."""
        if expected_hash != actual_hash:
            violation = {
                "type": "hash_mismatch",
                "caller": caller,
                "expected": expected_hash,
                "actual": actual_hash,
            }
            self._violations.append(violation)
            raise PipelineContractError(
                "hash_mismatch",
                f"{caller}: deterministic hash mismatch. "
                f"Expected {expected_hash[:16]}..., got {actual_hash[:16]}..."
            )

    def summary(self) -> dict[str, Any]:
        """Return a summary of the guard's state for manifest inclusion."""
        return {
            "context_hash": self._context.context_hash,
            "violation_count": len(self._violations),
            "violations": self._violations,
            "contract_version": "SESSION-040",
            "contract_status": "CLEAN" if not self._violations else "VIOLATED",
        }
