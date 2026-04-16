"""Pipeline Auditor — End-to-End Deterministic Hash Sealing for the UMR Trunk.

SESSION-040: CLI Pipeline Contract & End-to-End Determinism (攻坚战役三)

This module implements the ``UMR_Auditor`` — the terminal node of the motion
pipeline that computes a deterministic SHA-256 hash seal over the complete
pipeline output. The design is inspired by:

- **Glenn Fiedler (Gaffer on Games):** "Determinism means given the same initial
  condition and the same set of inputs, your simulation gives exactly the same
  result. Exact down to the bit-level."

- **Pixar USD CI Validation:** Schema-aware validation that makes assets
  inspectable, mergeable, and automatable.

- **Blockchain-style integrity:** Each ``.umr_manifest.json`` contains a
  ``pipeline_hash`` that seals the entire output. If any upstream change
  pollutes the core logic, the hash changes and CI catches it.

The auditor operates on ``UnifiedMotionClip`` objects and produces a
``ManifestSeal`` — a frozen record of the pipeline's deterministic output
state. This seal is written to ``.umr_manifest.json`` alongside the
character pack artifacts.

References
----------
[1] Glenn Fiedler, "Deterministic Lockstep", Gaffer on Games, 2014.
[2] Glenn Fiedler, "Floating Point Determinism", Gaffer on Games, 2010.
[3] NVIDIA Omniverse, "USD Validation — VFI Guide", 2026.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .pipeline_contract import UMR_Context, PipelineContractError


# ── Manifest Seal ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ManifestSeal:
    """Immutable record of a deterministic pipeline output seal.

    This is the core artifact written to ``.umr_manifest.json``. It captures
    everything needed to verify that a pipeline run is reproducible: the
    context hash (input), the pipeline hash (output), and per-state hashes
    for granular integrity checking.

    Attributes
    ----------
    context_hash : str
        SHA-256 of the ``UMR_Context`` that produced this output.
    pipeline_hash : str
        SHA-256 of the complete pipeline output (all states combined).
    state_hashes : tuple[tuple[str, str], ...]
        Per-state SHA-256 hashes as sorted (state_name, hash) pairs.
    contact_tag_hash : str
        Separate SHA-256 for contact tag integrity across all frames.
    node_order : tuple[str, ...]
        Ordered tuple of pipeline nodes that were executed.
    frame_count : int
        Total number of frames produced across all states.
    timestamp : str
        ISO-8601 timestamp of when the seal was created.
    pipeline_version : str
        Version of the pipeline that produced this seal.
    session_id : str
        Session that created this seal.
    """

    context_hash: str = ""
    pipeline_hash: str = ""
    state_hashes: tuple[tuple[str, str], ...] = ()
    contact_tag_hash: str = ""
    node_order: tuple[str, ...] = ()
    frame_count: int = 0
    timestamp: str = ""
    pipeline_version: str = "0.31.0"
    session_id: str = "SESSION-040"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dictionary for JSON export."""
        return {
            "context_hash": self.context_hash,
            "pipeline_hash": self.pipeline_hash,
            "state_hashes": {k: v for k, v in self.state_hashes},
            "contact_tag_hash": self.contact_tag_hash,
            "node_order": list(self.node_order),
            "frame_count": self.frame_count,
            "timestamp": self.timestamp,
            "pipeline_version": self.pipeline_version,
            "session_id": self.session_id,
            "seal_version": "umr_manifest_seal_v1",
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManifestSeal":
        """Reconstruct a ManifestSeal from a dictionary."""
        state_hashes = tuple(sorted(data.get("state_hashes", {}).items()))
        return cls(
            context_hash=data.get("context_hash", ""),
            pipeline_hash=data.get("pipeline_hash", ""),
            state_hashes=state_hashes,
            contact_tag_hash=data.get("contact_tag_hash", ""),
            node_order=tuple(data.get("node_order", [])),
            frame_count=data.get("frame_count", 0),
            timestamp=data.get("timestamp", ""),
            pipeline_version=data.get("pipeline_version", "0.31.0"),
            session_id=data.get("session_id", "SESSION-040"),
        )


# ── UMR Auditor ──────────────────────────────────────────────────────────────


class UMR_Auditor:
    """Terminal pipeline node that computes deterministic hash seals.

    The auditor sits at the very end of the motion pipeline. After all
    states have been processed through the UMR trunk (phase generation,
    physics compliance, biomechanics grounding), the auditor:

    1. Serializes each frame's coordinates, contact tags, and render config
       into a canonical JSON string.
    2. Computes a per-state SHA-256 hash.
    3. Combines all state hashes into a single ``pipeline_hash``.
    4. Computes a separate ``contact_tag_hash`` for contact integrity.
    5. Packages everything into a ``ManifestSeal``.

    The seal is then written to ``.umr_manifest.json``. If a future code
    change pollutes the core logic and causes the hash to drift, CI will
    detect the mismatch and block the commit.

    Parameters
    ----------
    context : UMR_Context
        The frozen pipeline context for this run.
    """

    def __init__(self, context: UMR_Context) -> None:
        if not isinstance(context, UMR_Context):
            raise PipelineContractError(
                "missing_context",
                f"UMR_Auditor requires a UMR_Context, got {type(context).__name__}."
            )
        self._context = context
        self._state_data: dict[str, list[dict[str, Any]]] = {}
        self._node_order: list[str] = []

    def register_clip(self, state: str, clip_frames: list[dict[str, Any]],
                       node_order: Optional[list[str]] = None) -> None:
        """Register a processed motion clip for a given state.

        Parameters
        ----------
        state : str
            The animation state name (e.g., ``"idle"``, ``"run"``).
        clip_frames : list[dict]
            List of serialized ``UnifiedMotionFrame.to_dict()`` outputs.
        node_order : list[str], optional
            Pipeline node execution order for this clip.
        """
        self._state_data[state] = clip_frames
        if node_order and not self._node_order:
            self._node_order = list(node_order)

    def _canonicalize_frame(self, frame_dict: dict[str, Any]) -> str:
        """Produce a deterministic canonical string from a frame dictionary.

        The canonicalization strips non-deterministic metadata (timestamps,
        file paths) and sorts all keys to ensure identical inputs always
        produce identical strings regardless of insertion order.
        """
        canonical = {
            "time": round(float(frame_dict.get("time", 0.0)), 10),
            "phase": round(float(frame_dict.get("phase", 0.0)), 10),
            "frame_index": int(frame_dict.get("frame_index", 0)),
            "source_state": str(frame_dict.get("source_state", "")),
            "root_transform": frame_dict.get("root_transform", {}),
            "joint_local_rotations": frame_dict.get("joint_local_rotations", {}),
            "contact_tags": frame_dict.get("contact_tags", {}),
        }
        # Round all float values in nested dicts for determinism
        if isinstance(canonical["root_transform"], dict):
            canonical["root_transform"] = {
                k: round(float(v), 10) for k, v in sorted(canonical["root_transform"].items())
            }
        if isinstance(canonical["joint_local_rotations"], dict):
            canonical["joint_local_rotations"] = {
                k: round(float(v), 10) for k, v in sorted(canonical["joint_local_rotations"].items())
            }
        if isinstance(canonical["contact_tags"], dict):
            canonical["contact_tags"] = {
                k: bool(v) for k, v in sorted(canonical["contact_tags"].items())
            }
        return json.dumps(canonical, sort_keys=True, ensure_ascii=True)

    def _hash_state(self, state: str) -> str:
        """Compute SHA-256 for a single state's frame sequence."""
        frames = self._state_data.get(state, [])
        hasher = hashlib.sha256()
        hasher.update(state.encode("utf-8"))
        for frame_dict in frames:
            canonical = self._canonicalize_frame(frame_dict)
            hasher.update(canonical.encode("utf-8"))
        return hasher.hexdigest()

    def _hash_contacts(self) -> str:
        """Compute a separate SHA-256 over all contact tags for integrity."""
        hasher = hashlib.sha256()
        for state in sorted(self._state_data.keys()):
            for frame_dict in self._state_data[state]:
                tags = frame_dict.get("contact_tags", {})
                canonical = json.dumps(
                    {k: bool(v) for k, v in sorted(tags.items())},
                    sort_keys=True,
                    ensure_ascii=True,
                )
                hasher.update(canonical.encode("utf-8"))
        return hasher.hexdigest()

    def seal(self) -> ManifestSeal:
        """Compute the final deterministic seal for the entire pipeline run.

        Returns
        -------
        ManifestSeal
            Frozen record containing all hashes and metadata.
        """
        state_hashes: list[tuple[str, str]] = []
        total_frames = 0

        for state in sorted(self._state_data.keys()):
            state_hash = self._hash_state(state)
            state_hashes.append((state, state_hash))
            total_frames += len(self._state_data[state])

        # Combine all state hashes into a single pipeline hash
        pipeline_hasher = hashlib.sha256()
        pipeline_hasher.update(self._context.context_hash.encode("utf-8"))
        for state, state_hash in state_hashes:
            pipeline_hasher.update(f"{state}:{state_hash}".encode("utf-8"))
        pipeline_hash = pipeline_hasher.hexdigest()

        contact_hash = self._hash_contacts()

        return ManifestSeal(
            context_hash=self._context.context_hash,
            pipeline_hash=pipeline_hash,
            state_hashes=tuple(state_hashes),
            contact_tag_hash=contact_hash,
            node_order=tuple(self._node_order),
            frame_count=total_frames,
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            pipeline_version=self._context.pipeline_version,
            session_id=self._context.session_id,
        )

    def verify_against(self, expected_seal: ManifestSeal) -> bool:
        """Verify the current pipeline output against an expected golden master.

        Parameters
        ----------
        expected_seal : ManifestSeal
            The previously recorded seal to compare against.

        Returns
        -------
        bool
            True if the pipeline hash matches, False otherwise.

        Raises
        ------
        PipelineContractError
            If the hashes do not match (fail-fast).
        """
        current_seal = self.seal()
        if current_seal.pipeline_hash != expected_seal.pipeline_hash:
            raise PipelineContractError(
                "hash_mismatch",
                f"Pipeline hash mismatch: expected {expected_seal.pipeline_hash[:16]}..., "
                f"got {current_seal.pipeline_hash[:16]}... "
                f"This indicates a non-deterministic change in the core pipeline logic."
            )
        return True

    def save_manifest(self, path: str) -> ManifestSeal:
        """Compute the seal and write it to a ``.umr_manifest.json`` file.

        Parameters
        ----------
        path : str
            File path for the manifest JSON.

        Returns
        -------
        ManifestSeal
            The computed seal.
        """
        seal = self.seal()
        manifest = {
            "seal": seal.to_dict(),
            "context": self._context.to_dict(),
            "audit": {
                "states_registered": sorted(self._state_data.keys()),
                "total_frames": seal.frame_count,
                "node_order": list(seal.node_order),
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        return seal


# ── Contact Flicker Detector ─────────────────────────────────────────────────


class ContactFlickerDetector:
    """Detect illegal high-frequency contact tag oscillation.

    In a well-behaved animation, foot contacts should not toggle on/off
    faster than a physically plausible rate. High-frequency flickering
    indicates a bug in the phase generator or physics projector.

    The detector counts consecutive contact toggles within a sliding window.
    If the toggle rate exceeds the threshold, it flags the clip as suspect.

    Parameters
    ----------
    max_toggles_per_window : int
        Maximum allowed contact toggles within the window (default: 3).
    window_size : int
        Number of frames in the sliding window (default: 4).
    """

    def __init__(self, max_toggles_per_window: int = 3, window_size: int = 4) -> None:
        self.max_toggles = max_toggles_per_window
        self.window_size = window_size

    def check_clip(self, frames: list[dict[str, Any]]) -> dict[str, Any]:
        """Check a clip's contact tags for illegal flickering.

        Parameters
        ----------
        frames : list[dict]
            List of serialized frame dictionaries.

        Returns
        -------
        dict
            Report with ``"clean"`` (bool), ``"flicker_count"`` (int),
            and ``"flicker_frames"`` (list of frame indices where flicker was detected).
        """
        if len(frames) < 2:
            return {"clean": True, "flicker_count": 0, "flicker_frames": []}

        flicker_frames: list[int] = []
        contact_keys = ["left_foot", "right_foot", "left_hand", "right_hand"]

        for key in contact_keys:
            toggles: list[int] = []
            prev_val = None
            for i, frame in enumerate(frames):
                tags = frame.get("contact_tags", {})
                val = bool(tags.get(key, False))
                if prev_val is not None and val != prev_val:
                    toggles.append(i)
                prev_val = val

            # Sliding window check
            for i in range(len(toggles)):
                window_end = i
                while window_end < len(toggles) and toggles[window_end] - toggles[i] < self.window_size:
                    window_end += 1
                if window_end - i > self.max_toggles:
                    for j in range(i, window_end):
                        if toggles[j] not in flicker_frames:
                            flicker_frames.append(toggles[j])

        flicker_frames.sort()
        return {
            "clean": len(flicker_frames) == 0,
            "flicker_count": len(flicker_frames),
            "flicker_frames": flicker_frames,
        }
