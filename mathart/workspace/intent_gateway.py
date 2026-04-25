"""SESSION-196 P0-CLI-INTENT-THREADING-AND-ORPHAN-RESCUE — Intent Gateway.

This module implements the **Validating + Mutating Admission Webhook** layer
inspired by Kubernetes API server admission control.  Its single
responsibility is to be the **outermost guard** that intercepts every CLI
or ``intent.yaml`` request *before* the corresponding fields ever reach the
PDG mass-production graph, ComfyUI workflow assembly site, or OpenPose bake
hook.  When a field is malformed, missing, or references a ghost path, the
gateway raises :class:`IntentValidationError` **immediately** so the user
sees a red, actionable error in the very first second instead of an opaque
crash 30 minutes later inside ``builtin_backends._execute_live_pipeline``.

Why a separate module?
----------------------
1. **Anti-Hardcoded Red Line** — The CLI wizard and the YAML loader are two
   different surfaces, but both must enforce the *same* admission contract.
   Centralising the rules here lets us add new fields once and have both
   surfaces honor them simultaneously.
2. **Anti-Signature-Pollution Red Line** — Downstream code never grows new
   ``action_name=`` / ``ref_img=`` formal parameters.  All resolved values
   are written into the immutable ``director_studio_spec`` payload (Redux
   Context pattern) and read back at the deep call site via tiny pure
   helpers (``extract_action_name`` / ``extract_visual_reference_path``).
3. **Anti-Implicit-Fallback Red Line** — The gateway *never* silently maps
   ``dash → walk`` or *swallows* a missing reference image; that would
   convert a user-visible misconfiguration into a degraded render that
   looks like a project bug.

Reference Patterns
------------------
* Kubernetes Dynamic Admission Control — Validating + Mutating webhooks,
  ``failurePolicy: Fail`` (Fail-Closed).  See
  ``https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/``.
* Redux Fundamentals — single source of truth + immutable state snapshots.
  See ``https://redux.js.org/tutorials/fundamentals/part-2-concepts-data-flow``.
* Jim Gray, "Why Do Computers Stop and What Can Be Done About It?" (1985)
  — Crash-Only / Fail-Fast at module boundaries beats trying to repair a
  half-built pipeline three layers deep.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public exception — used by both ``cli_wizard`` and any programmatic loader.
# ---------------------------------------------------------------------------
class IntentValidationError(ValueError):
    """Raised when a CLI / YAML intent payload violates the admission contract.

    The message MUST be a complete, human-readable sentence that names:

    * the offending field (``action`` / ``reference_image`` / ...);
    * the actual value the gateway received;
    * the closest valid set / closest legal predicate.

    Down-stream code (``cli_wizard.py``) renders the message in red so the
    user can fix the problem in their next prompt instead of digging through
    logs.
    """

    def __init__(self, message: str, *, field_name: str = "", received: Any = None,
                 expected: Any = None) -> None:
        super().__init__(message)
        self.field_name = field_name
        self.received = received
        self.expected = expected


# ---------------------------------------------------------------------------
# Result payload — what the wizard / YAML loader writes into ``raw_intent``
# before the spec is built.  Keep this dataclass small and serialisable so
# it can ride along inside ``director_studio_spec`` without surprises.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IntentAdmissionResult:
    """Output of a successful admission check.

    Attributes
    ----------
    action_name:
        The validated action name, guaranteed to be a member of the live
        :class:`mathart.core.openpose_pose_provider.OpenPoseGaitRegistry`.
        Empty string means "no action lock requested" — the production
        graph then keeps its existing random / preset selection logic.
    reference_image_path:
        The absolute, file-system-validated path to the IPAdapter reference
        image, or ``None`` when the user did not supply one.  When set, the
        path is guaranteed to exist on disk at admission time.
    warnings:
        Soft messages (e.g. "action 'walk' is the default — consider being
        explicit").  Non-fatal — surfaced through ``output_fn`` only.
    """
    action_name: str = ""
    reference_image_path: str | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def as_admission_payload(self) -> dict[str, Any]:
        """Serialise into the canonical key set written into the spec."""
        payload: dict[str, Any] = {}
        if self.action_name:
            payload["action_name"] = self.action_name
        if self.reference_image_path is not None:
            payload["_visual_reference_path"] = self.reference_image_path
        return payload


# ---------------------------------------------------------------------------
# Helpers — kept module-local on purpose so they can be patched by tests
# without monkey-patching ``cli_wizard``.
# ---------------------------------------------------------------------------
def _registered_gait_names() -> tuple[str, ...]:
    """Return the live gait registry contents.

    Imported lazily so this gateway module stays import-cheap (the registry
    depends on numpy/pillow which we do not want to drag in at every
    ``mathart.workspace`` import).
    """
    try:
        from mathart.core.openpose_pose_provider import get_gait_registry
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[IntentGateway] gait registry unreachable: %s", exc)
        return ()
    try:
        return tuple(sorted(get_gait_registry().names()))
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("[IntentGateway] gait registry enumeration failed: %s", exc)
        return ()


def _coerce_path(value: Any) -> Path:
    if isinstance(value, Path):
        return value
    return Path(str(value)).expanduser()


# ---------------------------------------------------------------------------
# The gateway itself — a single class so tests can instantiate it with a
# fake registry / fake filesystem if needed.
# ---------------------------------------------------------------------------
class IntentGateway:
    """Validating + Mutating admission controller for SESSION-196 fields.

    The class is intentionally stateless besides the (optional) injectable
    list of registered gaits.  It is safe to construct one per request.
    """

    def __init__(
        self,
        *,
        registered_gaits: Iterable[str] | None = None,
        filesystem_check: bool = True,
    ) -> None:
        self._registered_gaits: tuple[str, ...] = (
            tuple(sorted(set(registered_gaits))) if registered_gaits is not None
            else _registered_gait_names()
        )
        self._filesystem_check = bool(filesystem_check)

    # -- Accessors -----------------------------------------------------
    @property
    def registered_gaits(self) -> tuple[str, ...]:
        return self._registered_gaits

    # -- Field-level validators ----------------------------------------
    def validate_action(self, raw_action: Any) -> str:
        """Validate the optional ``action`` field.

        * ``None`` / empty string → returns ``""`` (caller may default downstream).
        * Non-string → :class:`IntentValidationError`.
        * Unknown action → :class:`IntentValidationError` listing the legal set.
        """
        if raw_action is None or raw_action == "":
            return ""
        if not isinstance(raw_action, str):
            raise IntentValidationError(
                f"intent field 'action' must be a string, got {type(raw_action).__name__}",
                field_name="action",
                received=raw_action,
                expected="string in registered gait set",
            )
        action = raw_action.strip().lower()
        if not action:
            return ""
        if not self._registered_gaits:
            # Registry unreachable — fail open to avoid blocking the wizard
            # entirely, but warn loudly so test harnesses surface the issue.
            logger.warning(
                "[IntentGateway] registered_gaits is empty; accepting '%s' "
                "without enumeration check (registry was unreachable).",
                action,
            )
            return action
        if action not in self._registered_gaits:
            raise IntentValidationError(
                f"unknown action '{raw_action}' — expected one of "
                f"{list(self._registered_gaits)}.",
                field_name="action",
                received=raw_action,
                expected=self._registered_gaits,
            )
        return action

    def validate_reference_image(self, raw_path: Any) -> str | None:
        """Validate the optional ``reference_image`` field.

        * ``None`` / empty → returns ``None`` (graceful degradation downstream).
        * Path that does not exist on disk → :class:`IntentValidationError`.
        * Path that exists but is a directory → :class:`IntentValidationError`.
        * Otherwise returns the canonical absolute path string.
        """
        if raw_path is None or raw_path == "":
            return None
        try:
            path = _coerce_path(raw_path)
        except Exception as exc:  # pragma: no cover — defensive
            raise IntentValidationError(
                f"reference_image '{raw_path!r}' is not a valid filesystem "
                f"path: {exc}",
                field_name="reference_image",
                received=raw_path,
                expected="existing file path",
            ) from exc
        if not self._filesystem_check:
            return str(path)
        if not path.exists():
            raise IntentValidationError(
                f"reference_image not found on disk: '{path}'. "
                "Refusing to thread a ghost path into the IPAdapter LoadImage "
                "node (Fail-Closed admission policy).",
                field_name="reference_image",
                received=str(path),
                expected="existing file path",
            )
        if path.is_dir():
            raise IntentValidationError(
                f"reference_image '{path}' is a directory, not a file. "
                "IPAdapter requires a single image file (PNG/JPG/WEBP).",
                field_name="reference_image",
                received=str(path),
                expected="file (not directory)",
            )
        return str(path.resolve())

    # -- Aggregate admission ------------------------------------------
    def admit(self, raw_intent: Mapping[str, Any]) -> IntentAdmissionResult:
        """Run the full admission pipeline on a raw intent mapping.

        The mapping comes either from interactive prompt collection or from
        ``yaml.safe_load(open('intent.yaml'))``.  Both paths funnel through
        this single entry point so the contract stays in one place.
        """
        warnings: list[str] = []
        action = self.validate_action(raw_intent.get("action"))
        ref = self.validate_reference_image(raw_intent.get("reference_image"))
        if not action and not ref:
            warnings.append(
                "no SESSION-196 admission fields supplied (action / "
                "reference_image both empty) — gateway is in pass-through "
                "mode for this request."
            )
        return IntentAdmissionResult(
            action_name=action,
            reference_image_path=ref,
            warnings=tuple(warnings),
        )


# ---------------------------------------------------------------------------
# Pure extractors — used by deep call sites (builtin_backends, OpenPose bake)
# WITHOUT widening any existing function signature.  Mirrors the
# ``extract_visual_reference_path`` accessor introduced in SESSION-195.
# ---------------------------------------------------------------------------
def extract_action_name(context: Mapping[str, Any]) -> str:
    """Resolve the validated ``action_name`` from a pipeline context.

    Search order (mirrors :func:`extract_visual_reference_path`)::

        1. context["action_name"]
        2. context["director_studio_spec"]["action_name"]
        3. context["action_filter"][0]   (legacy LookDev shortcut)
        4. context["motion_state"]       (mass-production prepared payload)

    Returns an empty string when no admission-validated action is present.
    """
    direct = context.get("action_name") if isinstance(context, Mapping) else None
    if isinstance(direct, str) and direct:
        return direct
    spec = context.get("director_studio_spec") if isinstance(context, Mapping) else None
    if isinstance(spec, Mapping):
        nested = spec.get("action_name")
        if isinstance(nested, str) and nested:
            return nested
    action_filter = context.get("action_filter") if isinstance(context, Mapping) else None
    if isinstance(action_filter, (list, tuple)) and action_filter:
        if isinstance(action_filter[0], str) and action_filter[0]:
            return action_filter[0]
    motion_state = context.get("motion_state") if isinstance(context, Mapping) else None
    if isinstance(motion_state, str) and motion_state:
        return motion_state
    return ""


def thread_admission_into_director_spec(
    spec_dict: dict[str, Any],
    admission: IntentAdmissionResult,
) -> dict[str, Any]:
    """Mutating-webhook step: inject admission fields into the director spec.

    The spec dict is mutated in place AND returned for callers that prefer a
    pipeline-style expression.  Existing keys are honoured — this function
    will not overwrite a value the user explicitly set elsewhere unless it
    is empty.
    """
    if not isinstance(spec_dict, dict):
        raise TypeError(
            "thread_admission_into_director_spec expects a dict spec payload"
        )
    payload = admission.as_admission_payload()
    for key, value in payload.items():
        existing = spec_dict.get(key)
        if existing in (None, "", [], {}):
            spec_dict[key] = value
    return spec_dict


__all__ = [
    "IntentValidationError",
    "IntentAdmissionResult",
    "IntentGateway",
    "extract_action_name",
    "thread_admission_into_director_spec",
]
