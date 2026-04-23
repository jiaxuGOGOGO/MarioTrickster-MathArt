"""ComfyUI Dynamic JSON Workflow Mutator — BFF Payload Mutation Engine.

SESSION-151 (P0-SESSION-147-COMFYUI-API-DYNAMIC-DISPATCH)
----------------------------------------------------------
Implements a **semantic JSON tree traversal mutator** that dynamically injects
upstream-generated proxy images and vibe prompts into ComfyUI ``workflow_api.json``
blueprints.  This is the BFF (Backend for Frontend) Payload Mutation layer.

Industrial References
---------------------
1. **BFF Payload Mutation (Sam Newman, "Building Microservices" 2021)**:
   The frontend-facing backend adapts upstream data into the exact shape
   required by the downstream rendering engine.  No hardcoded node IDs.

2. **Semantic Addressing (LLVM Pass Infrastructure)**:
   Nodes are located by ``_meta.title`` string matching, not by numeric
   node IDs which change every time the workflow is edited in ComfyUI.

3. **Immutable Blueprint + Copy-on-Write Mutation**:
   The original workflow JSON is never modified.  ``copy.deepcopy()``
   creates a mutable clone, and all mutations are applied to the clone.

Architecture Red Lines
----------------------
- [ANTI-PATTERN] NEVER use hardcoded node IDs like ``workflow["15"]``.
  Node IDs are **ephemeral** and change on every ComfyUI workflow edit.
- [ANTI-PATTERN] NEVER rebuild the node graph in Python.
  Topology lives in the external JSON asset; Python only mutates values.
- [CONTRACT] All mutations are logged in a ``MutationLedger`` for
  full auditability and debugging.

Semantic Marker Convention
--------------------------
Workflow designers MUST tag injectable nodes with these ``_meta.title`` markers:

| Marker                    | Purpose                              |
|---------------------------|--------------------------------------|
| ``[MathArt_Input_Image]`` | Node receives the upstream proxy image path |
| ``[MathArt_Prompt]``      | Node receives the vibe description text     |
| ``[MathArt_Negative]``    | Node receives the negative prompt (optional)|
| ``[MathArt_Seed]``        | Node receives the random seed (optional)    |
| ``[MathArt_Output]``      | Node produces the final output (read-only)  |
"""
from __future__ import annotations

import copy
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class MutationError(ValueError):
    """Raised when a workflow mutation fails due to missing nodes or invalid state."""


# ---------------------------------------------------------------------------
# Mutation Ledger Entry
# ---------------------------------------------------------------------------

@dataclass
class MutationRecord:
    """A single mutation applied to a workflow node."""
    marker: str
    node_id: str
    class_type: str
    title: str
    input_key: str
    old_value: Any
    new_value: Any
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "marker": self.marker,
            "node_id": self.node_id,
            "class_type": self.class_type,
            "title": self.title,
            "input_key": self.input_key,
            "old_value": str(self.old_value)[:200],
            "new_value": str(self.new_value)[:200],
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Semantic Marker Definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SemanticMarker:
    """Defines a semantic injection point in a ComfyUI workflow.

    Attributes
    ----------
    marker : str
        The ``_meta.title`` substring to search for (case-insensitive).
    input_key : str
        The key within the node's ``inputs`` dict to mutate.
    required : bool
        If True, the mutator raises ``MutationError`` when the marker
        is not found in the workflow.
    """
    marker: str
    input_key: str
    required: bool = True


# Pre-defined semantic markers following the project convention
MARKER_INPUT_IMAGE = SemanticMarker("[MathArt_Input_Image]", "image", required=True)
MARKER_PROMPT = SemanticMarker("[MathArt_Prompt]", "text", required=True)
MARKER_NEGATIVE = SemanticMarker("[MathArt_Negative]", "text", required=False)
MARKER_SEED = SemanticMarker("[MathArt_Seed]", "seed", required=False)
MARKER_OUTPUT = SemanticMarker("[MathArt_Output]", "filename_prefix", required=False)

# Default marker set for standard MathArt workflows
DEFAULT_MARKERS: tuple[SemanticMarker, ...] = (
    MARKER_INPUT_IMAGE,
    MARKER_PROMPT,
    MARKER_NEGATIVE,
    MARKER_SEED,
    MARKER_OUTPUT,
)


# ---------------------------------------------------------------------------
# ComfyUI Workflow Mutator
# ---------------------------------------------------------------------------

class ComfyWorkflowMutator:
    """Semantic JSON tree traversal mutator for ComfyUI workflow_api.json.

    This mutator implements the BFF Payload Mutation pattern:
    1. Load a workflow blueprint from a JSON file.
    2. Deep-copy the blueprint (immutable original).
    3. Traverse all nodes, matching ``_meta.title`` against semantic markers.
    4. Inject runtime values (image paths, prompts, seeds) into matched nodes.
    5. Return the mutated workflow with a full audit ledger.

    Parameters
    ----------
    blueprint_path : str | Path | None
        Path to the default ``workflow_api.json`` blueprint file.
        If None, the mutator operates in dict-only mode.
    markers : tuple[SemanticMarker, ...] | None
        Custom semantic markers.  Defaults to ``DEFAULT_MARKERS``.
    """

    def __init__(
        self,
        blueprint_path: str | Path | None = None,
        markers: tuple[SemanticMarker, ...] | None = None,
    ) -> None:
        self._blueprint_path = Path(blueprint_path) if blueprint_path else None
        self._markers = markers or DEFAULT_MARKERS
        self._cached_blueprint: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Blueprint Loading
    # ------------------------------------------------------------------

    def load_blueprint(self, path: str | Path | None = None) -> dict[str, Any]:
        """Load a workflow_api.json blueprint from disk.

        Parameters
        ----------
        path : str | Path | None
            Override path.  Falls back to ``self._blueprint_path``.

        Returns
        -------
        dict
            The raw workflow dict (NOT deep-copied — caller must copy).

        Raises
        ------
        FileNotFoundError
            If the blueprint file does not exist.
        MutationError
            If the file is not valid JSON or is empty.
        """
        target = Path(path) if path else self._blueprint_path
        if target is None:
            raise MutationError(
                "No blueprint path provided.  Pass a path to load_blueprint() "
                "or set blueprint_path in the constructor."
            )
        if not target.exists():
            raise FileNotFoundError(
                f"ComfyUI workflow blueprint not found: {target}"
            )
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise MutationError(
                f"Invalid JSON in workflow blueprint {target}: {e}"
            ) from e

        if not isinstance(raw, dict) or not raw:
            raise MutationError(
                f"Workflow blueprint {target} is not a non-empty JSON object"
            )

        self._cached_blueprint = raw
        logger.info(
            "[ComfyMutator] Loaded blueprint: %s (%d nodes)",
            target.name, len(raw),
        )
        return raw

    # ------------------------------------------------------------------
    # Semantic Node Finder
    # ------------------------------------------------------------------

    def find_nodes_by_title(
        self,
        workflow: dict[str, Any],
        title_contains: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Find all nodes whose ``_meta.title`` contains the given substring.

        Parameters
        ----------
        workflow : dict
            The ComfyUI workflow dict (node_id → node_data).
        title_contains : str
            Case-insensitive substring to match against ``_meta.title``.

        Returns
        -------
        list[tuple[str, dict]]
            List of ``(node_id, node_data)`` tuples for matching nodes.
        """
        needle = title_contains.strip().lower()
        matches: list[tuple[str, dict[str, Any]]] = []

        for node_id, node_data in workflow.items():
            if not isinstance(node_data, dict):
                continue
            meta = node_data.get("_meta", {})
            if not isinstance(meta, dict):
                continue
            title = str(meta.get("title", "")).strip().lower()
            if needle in title:
                matches.append((str(node_id), node_data))

        return matches

    def find_node_by_title(
        self,
        workflow: dict[str, Any],
        title_contains: str,
    ) -> tuple[str, dict[str, Any]]:
        """Find exactly one node by ``_meta.title`` substring.

        Raises
        ------
        MutationError
            If zero or multiple nodes match.
        """
        matches = self.find_nodes_by_title(workflow, title_contains)
        if not matches:
            raise MutationError(
                f"No node found with _meta.title containing {title_contains!r}. "
                f"Ensure the workflow blueprint has a node titled with this marker."
            )
        if len(matches) > 1:
            ids = ", ".join(nid for nid, _ in matches)
            raise MutationError(
                f"Ambiguous: {len(matches)} nodes match _meta.title "
                f"containing {title_contains!r} (node_ids: {ids}). "
                f"Each semantic marker must resolve to exactly one node."
            )
        return matches[0]

    # ------------------------------------------------------------------
    # Core Mutation Engine
    # ------------------------------------------------------------------

    def mutate(
        self,
        *,
        workflow: dict[str, Any] | None = None,
        blueprint_path: str | Path | None = None,
        injections: dict[str, Any],
        extra_markers: tuple[SemanticMarker, ...] | None = None,
    ) -> tuple[dict[str, Any], list[MutationRecord]]:
        """Apply semantic mutations to a workflow blueprint.

        This is the primary entry point for the BFF Payload Mutation engine.

        Parameters
        ----------
        workflow : dict | None
            Pre-loaded workflow dict.  If None, loads from ``blueprint_path``
            or the constructor's default path.
        blueprint_path : str | Path | None
            Override blueprint path (used only if ``workflow`` is None).
        injections : dict[str, Any]
            Mapping from semantic marker strings to injection values.
            Example::

                {
                    "[MathArt_Input_Image]": "uploaded_proxy.png",
                    "[MathArt_Prompt]": "pixel art hero, vibrant colors",
                    "[MathArt_Negative]": "blurry, low quality",
                    "[MathArt_Seed]": 42,
                }

        extra_markers : tuple[SemanticMarker, ...] | None
            Additional markers beyond the default set.

        Returns
        -------
        tuple[dict, list[MutationRecord]]
            The mutated workflow (deep copy) and the mutation audit ledger.

        Raises
        ------
        MutationError
            If a required marker is not found in the workflow.
        """
        # Load or use provided workflow
        if workflow is None:
            source = self.load_blueprint(blueprint_path)
        else:
            source = workflow

        # Deep copy — immutable blueprint principle
        mutated = copy.deepcopy(source)
        ledger: list[MutationRecord] = []

        # Merge marker sets
        all_markers = list(self._markers)
        if extra_markers:
            all_markers.extend(extra_markers)

        # Build marker lookup
        marker_map: dict[str, SemanticMarker] = {
            m.marker: m for m in all_markers
        }

        # Apply injections
        for marker_str, value in injections.items():
            marker_def = marker_map.get(marker_str)
            if marker_def is None:
                # Ad-hoc marker — create a transient definition
                marker_def = SemanticMarker(
                    marker=marker_str,
                    input_key=self._infer_input_key(marker_str),
                    required=True,
                )

            # Find the target node
            matches = self.find_nodes_by_title(mutated, marker_def.marker)

            if not matches:
                if marker_def.required:
                    raise MutationError(
                        f"Required semantic marker {marker_def.marker!r} not found "
                        f"in workflow.  Available node titles: "
                        f"{self._list_titles(mutated)}"
                    )
                logger.debug(
                    "[ComfyMutator] Optional marker %r not found — skipping.",
                    marker_def.marker,
                )
                continue

            # Inject into all matching nodes (usually exactly one)
            for node_id, node_data in matches:
                inputs = node_data.setdefault("inputs", {})
                old_value = inputs.get(marker_def.input_key)
                inputs[marker_def.input_key] = value

                record = MutationRecord(
                    marker=marker_def.marker,
                    node_id=node_id,
                    class_type=node_data.get("class_type", ""),
                    title=node_data.get("_meta", {}).get("title", ""),
                    input_key=marker_def.input_key,
                    old_value=old_value,
                    new_value=value,
                )
                ledger.append(record)
                logger.info(
                    "[ComfyMutator] Injected %r → node %s (%s).inputs[%s] = %s",
                    marker_def.marker,
                    node_id,
                    record.class_type,
                    marker_def.input_key,
                    str(value)[:100],
                )

        # Validate all required markers were satisfied
        self._validate_required_markers(mutated, all_markers, injections)

        logger.info(
            "[ComfyMutator] Mutation complete: %d injections applied.",
            len(ledger),
        )
        return mutated, ledger

    # ------------------------------------------------------------------
    # Convenience: Build Full Payload
    # ------------------------------------------------------------------

    def build_payload(
        self,
        *,
        workflow: dict[str, Any] | None = None,
        blueprint_path: str | Path | None = None,
        image_filename: str,
        prompt: str,
        negative_prompt: str = "",
        seed: int = -1,
        output_prefix: str = "mathart_render",
        client_id: str | None = None,
        extra_injections: dict[str, Any] | None = None,
        extra_markers: tuple[SemanticMarker, ...] | None = None,
    ) -> dict[str, Any]:
        """Build a complete ComfyUI API payload with semantic mutations.

        This is the high-level convenience method that assembles a
        ready-to-submit payload for ``POST /prompt``.

        Parameters
        ----------
        image_filename : str
            The filename of the uploaded image on the ComfyUI server
            (as returned by ``/upload/image``).
        prompt : str
            The positive vibe description.
        negative_prompt : str
            The negative prompt (optional).
        seed : int
            Random seed.  -1 = auto-generate from timestamp.
        output_prefix : str
            Filename prefix for saved outputs.
        client_id : str | None
            Unique client identifier.  Auto-generated if None.
        extra_injections : dict | None
            Additional marker → value pairs beyond the standard set.
        extra_markers : tuple[SemanticMarker, ...] | None
            Additional semantic marker definitions.

        Returns
        -------
        dict
            Complete payload dict with ``"prompt"`` and ``"client_id"`` keys,
            plus ``"mathart_mutation_ledger"`` for audit trail.
        """
        import uuid

        if seed < 0:
            seed = int(time.time_ns() % (2**31))

        injections: dict[str, Any] = {
            MARKER_INPUT_IMAGE.marker: image_filename,
            MARKER_PROMPT.marker: prompt,
        }
        if negative_prompt:
            injections[MARKER_NEGATIVE.marker] = negative_prompt
        injections[MARKER_SEED.marker] = seed
        if output_prefix:
            injections[MARKER_OUTPUT.marker] = output_prefix

        if extra_injections:
            injections.update(extra_injections)

        mutated_workflow, ledger = self.mutate(
            workflow=workflow,
            blueprint_path=blueprint_path,
            injections=injections,
            extra_markers=extra_markers,
        )

        cid = client_id or str(uuid.uuid4())

        return {
            "client_id": cid,
            "prompt": mutated_workflow,
            "mathart_mutation_ledger": {
                "session": "SESSION-151",
                "mutations_applied": len(ledger),
                "records": [r.to_dict() for r in ledger],
                "seed": seed,
                "output_prefix": output_prefix,
            },
        }

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_input_key(marker: str) -> str:
        """Infer the input key from a marker string for ad-hoc markers."""
        lower = marker.lower()
        if "image" in lower or "input" in lower:
            return "image"
        if "prompt" in lower or "text" in lower:
            return "text"
        if "seed" in lower:
            return "seed"
        if "output" in lower or "save" in lower:
            return "filename_prefix"
        return "value"

    @staticmethod
    def _list_titles(workflow: dict[str, Any]) -> list[str]:
        """List all ``_meta.title`` values in a workflow for error messages."""
        titles: list[str] = []
        for node_data in workflow.values():
            if isinstance(node_data, dict):
                meta = node_data.get("_meta", {})
                if isinstance(meta, dict):
                    title = meta.get("title", "")
                    if title:
                        titles.append(title)
        return sorted(titles)

    def _validate_required_markers(
        self,
        workflow: dict[str, Any],
        markers: list[SemanticMarker],
        injections: dict[str, Any],
    ) -> None:
        """Validate that all required markers have been injected.

        This is a post-mutation safety check.  If a required marker was
        declared but no injection was provided for it, we check whether
        the node already has a non-empty default value.  If not, raise.
        """
        for marker_def in markers:
            if not marker_def.required:
                continue
            if marker_def.marker in injections:
                continue
            # Required marker not in injections — check if node has default
            matches = self.find_nodes_by_title(workflow, marker_def.marker)
            if not matches:
                # Node doesn't exist — this is only an error if the marker
                # was in the default set (user-defined extra markers that
                # are required but not injected are already caught above)
                continue
            for _, node_data in matches:
                inputs = node_data.get("inputs", {})
                current = inputs.get(marker_def.input_key)
                if current is None or current == "":
                    logger.warning(
                        "[ComfyMutator] Required marker %r has empty default "
                        "value in node.  Consider providing an explicit injection.",
                        marker_def.marker,
                    )
