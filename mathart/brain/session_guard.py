"""SessionGuard — cross-session task fingerprinting and duplicate-work prevention.

The existing project already tracks:
  - absorbed references (DEDUP_REGISTRY.json)
  - completed changes (DEDUP_REGISTRY.json)
  - pending tasks and history (PROJECT_BRAIN.json / SESSION_HANDOFF.md)
  - stagnation patterns (STAGNATION_LOG.md / DEDUP_REGISTRY.json)

What was still missing is a lightweight *task-level* memory that answers:
  1. Have we already attempted a very similar session goal?
  2. Which files / research areas were touched when we did?
  3. What must a new session read first to avoid wasting effort?

This module fills that gap with a deterministic fingerprint registry that can be
used without network access or external services.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "of", "for", "in", "on", "with",
    "by", "at", "is", "are", "be", "as", "from", "this", "that", "it",
    "项目", "当前", "继续", "工作", "实现", "以及", "针对", "进行", "继续工作",
    "project", "session", "task", "todo", "improve", "improvement", "continue",
}


@dataclass
class TaskFingerprintRecord:
    """A normalized record of a session-level task intent."""

    session_id: str
    goal: str
    fingerprint: str
    normalized_goal: str
    tokens: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    research_topics: list[str] = field(default_factory=list)
    outcome: str = "in_progress"
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskFingerprintRecord":
        return cls(**data)


@dataclass
class DuplicateCandidate:
    """A previously recorded task that looks similar to the new one."""

    session_id: str
    goal: str
    similarity: float
    outcome: str
    files: list[str] = field(default_factory=list)
    research_topics: list[str] = field(default_factory=list)


@dataclass
class TaskRegistrationResult:
    """Result of registering a task fingerprint."""

    fingerprint: str
    is_duplicate: bool
    duplicate_candidates: list[DuplicateCandidate] = field(default_factory=list)
    record_count: int = 0


class SessionGuard:
    """Persistent task fingerprint registry for anti-duplication workflows.

    The registry is intentionally simple and deterministic so it can be used in
    sandboxed, offline, or long-lived projects without additional dependencies.
    """

    REGISTRY_FILE = "TASK_FINGERPRINTS.json"
    STARTUP_FILES = [
        "SESSION_HANDOFF.md",
        "DEDUP_REGISTRY.json",
        "SESSION_PROTOCOL.md",
        "PROJECT_BRAIN.json",
        "STAGNATION_LOG.md",
    ]

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.registry_path = self.project_root / self.REGISTRY_FILE
        self.records = self._load()

    # ── Public API ─────────────────────────────────────────────────────────

    def register_task(
        self,
        session_id: str,
        goal: str,
        *,
        tags: Optional[list[str]] = None,
        files: Optional[list[str]] = None,
        research_topics: Optional[list[str]] = None,
        outcome: str = "in_progress",
        notes: str = "",
        similarity_threshold: float = 0.72,
    ) -> TaskRegistrationResult:
        """Register a task goal and detect whether it likely repeats past work."""
        normalized_goal = self.normalize_text(goal)
        tokens = self.tokenize(goal, extra=tags or [])
        fingerprint = self.build_fingerprint(goal, tags=tags, files=files, research_topics=research_topics)

        candidates = self.find_similar(
            goal,
            tags=tags,
            files=files,
            research_topics=research_topics,
            top_k=5,
            similarity_threshold=similarity_threshold,
        )
        exact_duplicate = any(c.similarity >= 0.999 for c in candidates)

        record = TaskFingerprintRecord(
            session_id=session_id,
            goal=goal,
            fingerprint=fingerprint,
            normalized_goal=normalized_goal,
            tokens=tokens,
            tags=sorted(set(tags or [])),
            files=sorted(set(files or [])),
            research_topics=sorted(set(research_topics or [])),
            outcome=outcome,
            notes=notes,
        )

        existing_keys = {(r.session_id, r.fingerprint) for r in self.records}
        if (record.session_id, record.fingerprint) not in existing_keys:
            self.records.append(record)
            self._save()

        return TaskRegistrationResult(
            fingerprint=fingerprint,
            is_duplicate=exact_duplicate,
            duplicate_candidates=candidates,
            record_count=len(self.records),
        )

    def update_outcome(self, session_id: str, fingerprint: str, outcome: str, notes: str = "") -> bool:
        """Update the outcome of a previously registered task."""
        updated = False
        for record in self.records:
            if record.session_id == session_id and record.fingerprint == fingerprint:
                record.outcome = outcome
                if notes:
                    record.notes = notes
                updated = True
                break
        if updated:
            self._save()
        return updated

    def find_similar(
        self,
        goal: str,
        *,
        tags: Optional[list[str]] = None,
        files: Optional[list[str]] = None,
        research_topics: Optional[list[str]] = None,
        top_k: int = 3,
        similarity_threshold: float = 0.55,
    ) -> list[DuplicateCandidate]:
        """Find similar prior tasks using token overlap and scope overlap."""
        target_tokens = set(self.tokenize(goal, extra=(tags or []) + (research_topics or [])))
        target_files = set(files or [])
        target_topics = set(research_topics or [])
        candidates: list[DuplicateCandidate] = []

        for record in self.records:
            overlap = self._jaccard(target_tokens, set(record.tokens))
            file_bonus = 0.0
            topic_bonus = 0.0
            if target_files and record.files:
                file_bonus = 0.15 * self._jaccard(target_files, set(record.files))
            if target_topics and record.research_topics:
                topic_bonus = 0.15 * self._jaccard(target_topics, set(record.research_topics))
            similarity = min(1.0, overlap + file_bonus + topic_bonus)
            if similarity >= similarity_threshold:
                candidates.append(
                    DuplicateCandidate(
                        session_id=record.session_id,
                        goal=record.goal,
                        similarity=round(similarity, 4),
                        outcome=record.outcome,
                        files=list(record.files),
                        research_topics=list(record.research_topics),
                    )
                )

        candidates.sort(key=lambda item: item.similarity, reverse=True)
        return candidates[:top_k]

    def startup_report(self, goal: str = "") -> dict[str, Any]:
        """Build a deterministic startup brief for new sessions.

        The report intentionally references existing project files so the next
        session can immediately know what to read and what repeated work to avoid.
        """
        dedup = self._read_json(self.project_root / "DEDUP_REGISTRY.json")
        brain = self._read_json(self.project_root / "PROJECT_BRAIN.json")

        absorbed = dedup.get("absorbed_references", {}) if isinstance(dedup, dict) else {}
        completed = dedup.get("completed_changes", {}) if isinstance(dedup, dict) else {}
        stagnation = dedup.get("known_stagnation_patterns", {}).get("patterns", []) if isinstance(dedup, dict) else []
        pending_tasks = brain.get("pending_tasks", []) if isinstance(brain, dict) else []

        return {
            "required_reads": list(self.STARTUP_FILES),
            "duplicate_candidates": [asdict(c) for c in self.find_similar(goal)] if goal else [],
            "absorbed_reference_count": sum(len(v) for v in absorbed.values() if isinstance(v, list)),
            "completed_change_count": sum(len(v) for v in completed.values() if isinstance(v, list)),
            "known_stagnation_pattern_count": len(stagnation),
            "pending_task_count": len(pending_tasks),
            "highest_priority_tasks": [
                task.get("id") or task.get("task_id")
                for task in pending_tasks[:5]
                if isinstance(task, dict)
            ],
        }

    # ── Deterministic helpers ───────────────────────────────────────────────

    @staticmethod
    def normalize_text(text: str) -> str:
        lowered = text.lower()
        lowered = re.sub(r"[^\w\u4e00-\u9fff]+", " ", lowered)
        lowered = re.sub(r"\s+", " ", lowered).strip()
        return lowered

    @classmethod
    def tokenize(cls, text: str, extra: Optional[list[str]] = None) -> list[str]:
        normalized = cls.normalize_text(text)
        tokens = [tok for tok in normalized.split(" ") if tok and tok not in STOPWORDS and len(tok) > 1]
        if extra:
            for item in extra:
                tokens.extend(
                    tok for tok in cls.normalize_text(item).split(" ")
                    if tok and tok not in STOPWORDS and len(tok) > 1
                )
        return sorted(set(tokens))

    @classmethod
    def build_fingerprint(
        cls,
        goal: str,
        *,
        tags: Optional[list[str]] = None,
        files: Optional[list[str]] = None,
        research_topics: Optional[list[str]] = None,
    ) -> str:
        parts = [cls.normalize_text(goal)]
        if tags:
            parts.extend(sorted(cls.normalize_text(tag) for tag in tags))
        if files:
            parts.extend(sorted(files))
        if research_topics:
            parts.extend(sorted(cls.normalize_text(topic) for topic in research_topics))
        canonical = "|".join(parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self) -> list[TaskFingerprintRecord]:
        if not self.registry_path.exists():
            return []
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            records = data.get("records", []) if isinstance(data, dict) else []
            return [TaskFingerprintRecord.from_dict(item) for item in records]
        except (json.JSONDecodeError, TypeError, KeyError):
            return []

    def _save(self) -> None:
        payload = {
            "version": "1.0",
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "records": [record.to_dict() for record in self.records],
        }
        self.registry_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _jaccard(a: set[Any], b: set[Any]) -> float:
        if not a and not b:
            return 1.0
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
