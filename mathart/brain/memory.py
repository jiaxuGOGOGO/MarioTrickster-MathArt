"""ProjectMemory — the persistent brain of the MarioTrickster-MathArt project.

Every significant action updates this brain so that a new AI session
can read it and immediately understand the project's current state,
history, and next steps.

File layout:
  PROJECT_BRAIN.json       — machine-readable full state
  SESSION_HANDOFF.md       — human-readable context for new sessions
  EVOLUTION_HISTORY.md     — chronological log of all improvements
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class CapabilityGap:
    """A capability that is needed but not yet available."""
    name:        str
    description: str
    requires:    str          # "GPU", "Unity", "external_api", etc.
    priority:    str          # "high", "medium", "low"
    blocked_by:  list[str]    = field(default_factory=list)


@dataclass
class PendingTask:
    """A task that was planned but not yet completed."""
    task_id:     str
    description: str
    priority:    str          # "high", "medium", "low"
    depends_on:  list[str]    = field(default_factory=list)
    created_at:  str          = ""
    context:     dict         = field(default_factory=dict)


@dataclass
class EvolutionRecord:
    """A record of a single evolution step."""
    session_id:  str
    timestamp:   str
    version:     str
    changes:     list[str]
    best_score:  float
    test_count:  int
    notes:       str = ""


@dataclass
class ProjectState:
    """Complete project state snapshot."""
    version:              str
    last_session_id:      str
    last_updated:         str
    best_quality_score:   float
    total_iterations:     int
    distill_session_id:   str          # Next distill session ID (e.g., "DISTILL-004")
    mine_session_id:      str          # Next mine session ID (e.g., "MINE-003")
    sprite_count:         int
    knowledge_rule_count: int
    math_model_count:     int
    pending_tasks:        list[PendingTask]     = field(default_factory=list)
    capability_gaps:      list[CapabilityGap]  = field(default_factory=list)
    evolution_history:    list[EvolutionRecord] = field(default_factory=list)
    custom_notes:         dict[str, str]        = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectState":
        pending = [PendingTask(**t) for t in d.pop("pending_tasks", [])]
        gaps    = [CapabilityGap(**g) for g in d.pop("capability_gaps", [])]
        history = [EvolutionRecord(**r) for r in d.pop("evolution_history", [])]
        return cls(
            **{k: v for k, v in d.items()
               if k not in ("pending_tasks", "capability_gaps", "evolution_history")},
            pending_tasks=pending,
            capability_gaps=gaps,
            evolution_history=history,
        )


# ── Core memory class ──────────────────────────────────────────────────────────

class ProjectMemory:
    """Persistent project brain that survives across conversation sessions.

    Parameters
    ----------
    project_root : Path, optional
        Root directory of the project.
    """

    BRAIN_FILE    = "PROJECT_BRAIN.json"
    HANDOFF_FILE  = "SESSION_HANDOFF.md"
    HISTORY_FILE  = "EVOLUTION_HISTORY.md"

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.root  = Path(project_root) if project_root else Path.cwd()
        self.state = self._load()

    # ── State management ───────────────────────────────────────────────────────

    def update_version(self, version: str) -> None:
        self.state.version = version
        self.state.last_updated = self._now()
        self._save()

    def record_evolution(
        self,
        session_id: str,
        version: str,
        changes: list[str],
        best_score: float,
        test_count: int,
        notes: str = "",
    ) -> None:
        """Record a completed evolution step."""
        record = EvolutionRecord(
            session_id=session_id,
            timestamp=self._now(),
            version=version,
            changes=changes,
            best_score=best_score,
            test_count=test_count,
            notes=notes,
        )
        self.state.evolution_history.append(record)
        self.state.last_session_id = session_id
        self.state.version = version
        self.state.last_updated = self._now()
        if best_score > self.state.best_quality_score:
            self.state.best_quality_score = best_score
        self._save()
        self._append_history(record)

    def add_pending_task(
        self,
        task_id: str,
        description: str,
        priority: str = "medium",
        depends_on: Optional[list[str]] = None,
        context: Optional[dict] = None,
    ) -> None:
        """Add a task to the pending queue."""
        task = PendingTask(
            task_id=task_id,
            description=description,
            priority=priority,
            depends_on=depends_on or [],
            created_at=self._now(),
            context=context or {},
        )
        # Avoid duplicates
        existing_ids = {t.task_id for t in self.state.pending_tasks}
        if task_id not in existing_ids:
            self.state.pending_tasks.append(task)
            self._save()

    def complete_task(self, task_id: str) -> bool:
        """Mark a pending task as completed."""
        before = len(self.state.pending_tasks)
        self.state.pending_tasks = [
            t for t in self.state.pending_tasks if t.task_id != task_id
        ]
        if len(self.state.pending_tasks) < before:
            self._save()
            return True
        return False

    def add_capability_gap(
        self,
        name: str,
        description: str,
        requires: str,
        priority: str = "medium",
    ) -> None:
        """Register a capability gap (e.g., needs GPU)."""
        existing = {g.name for g in self.state.capability_gaps}
        if name not in existing:
            gap = CapabilityGap(
                name=name,
                description=description,
                requires=requires,
                priority=priority,
            )
            self.state.capability_gaps.append(gap)
            self._save()

    def resolve_gap(self, name: str) -> bool:
        """Mark a capability gap as resolved."""
        before = len(self.state.capability_gaps)
        self.state.capability_gaps = [
            g for g in self.state.capability_gaps if g.name != name
        ]
        if len(self.state.capability_gaps) < before:
            self._save()
            return True
        return False

    def update_counters(
        self,
        sprite_count: Optional[int] = None,
        knowledge_rule_count: Optional[int] = None,
        math_model_count: Optional[int] = None,
        total_iterations: Optional[int] = None,
    ) -> None:
        """Update project counters."""
        if sprite_count is not None:
            self.state.sprite_count = sprite_count
        if knowledge_rule_count is not None:
            self.state.knowledge_rule_count = knowledge_rule_count
        if math_model_count is not None:
            self.state.math_model_count = math_model_count
        if total_iterations is not None:
            self.state.total_iterations = total_iterations
        self._save()

    def set_note(self, key: str, value: str) -> None:
        """Store a custom note."""
        self.state.custom_notes[key] = value
        self._save()

    def get_note(self, key: str, default: str = "") -> str:
        return self.state.custom_notes.get(key, default)

    def get_next_distill_id(self) -> str:
        """Get the next distill session ID and auto-increment."""
        current = self.state.distill_session_id
        next_id = self._increment_id(current)
        self.state.distill_session_id = next_id
        self._save()
        return current

    def get_next_mine_id(self) -> str:
        """Get the next mine session ID and auto-increment."""
        current = self.state.mine_session_id
        next_id = self._increment_id(current)
        self.state.mine_session_id = next_id
        self._save()
        return current

    # ── Handoff generation ─────────────────────────────────────────────────────

    def generate_handoff(self) -> str:
        """Generate a SESSION_HANDOFF.md for the next AI session.

        This document tells the next AI session everything it needs to know
        to continue the project seamlessly.
        """
        s = self.state
        lines = [
            "# SESSION HANDOFF — MarioTrickster-MathArt",
            "",
            "> **READ THIS FIRST** if you are starting a new conversation about this project.",
            "> This document is auto-generated and always reflects the latest project state.",
            "",
            "## Project Overview",
            f"- **Current version**: {s.version}",
            f"- **Last updated**: {s.last_updated}",
            f"- **Last session**: {s.last_session_id}",
            f"- **Best quality score achieved**: {s.best_quality_score:.3f}",
            f"- **Total iterations run**: {s.total_iterations}",
            "",
            "## Knowledge Base Status",
            f"- **Distilled knowledge rules**: {s.knowledge_rule_count}",
            f"- **Math models registered**: {s.math_model_count}",
            f"- **Sprite references**: {s.sprite_count}",
            f"- **Next distill session ID**: {s.distill_session_id}",
            f"- **Next mine session ID**: {s.mine_session_id}",
            "",
        ]

        # Pending tasks
        if s.pending_tasks:
            lines += ["## Pending Tasks (Priority Order)", ""]
            high   = [t for t in s.pending_tasks if t.priority == "high"]
            medium = [t for t in s.pending_tasks if t.priority == "medium"]
            low    = [t for t in s.pending_tasks if t.priority == "low"]
            for group, label in [(high, "HIGH"), (medium, "MEDIUM"), (low, "LOW")]:
                for task in group:
                    lines.append(f"- [{label}] `{task.task_id}`: {task.description}")
                    if task.depends_on:
                        lines.append(f"  - Depends on: {', '.join(task.depends_on)}")
            lines.append("")
        else:
            lines += ["## Pending Tasks", "- No pending tasks.", ""]

        # Capability gaps
        if s.capability_gaps:
            lines += ["## Capability Gaps (External Upgrades Needed)", ""]
            for gap in s.capability_gaps:
                lines.append(f"- **[{gap.priority.upper()}]** `{gap.name}`: {gap.description}")
                lines.append(f"  - **Requires**: {gap.requires}")
            lines.append("")
        else:
            lines += ["## Capability Gaps", "- No capability gaps.", ""]

        # Recent evolution
        if s.evolution_history:
            lines += ["## Recent Evolution History (Last 5 Sessions)", ""]
            for record in s.evolution_history[-5:]:
                lines.append(f"### {record.session_id} — v{record.version} ({record.timestamp[:10]})")
                lines.append(f"- Best score: {record.best_score:.3f} | Tests: {record.test_count}")
                for change in record.changes[:5]:
                    lines.append(f"  - {change}")
                if record.notes:
                    lines.append(f"  - Notes: {record.notes}")
                lines.append("")

        # Custom notes
        if s.custom_notes:
            lines += ["## Custom Notes", ""]
            for key, value in s.custom_notes.items():
                lines.append(f"**{key}**: {value}")
            lines.append("")

        # Instructions for next session
        lines += [
            "## Instructions for Next AI Session",
            "",
            "1. Read `PROJECT_BRAIN.json` for the full machine-readable state.",
            "2. Read `DISTILL_LOG.md` to see what knowledge has been distilled.",
            "3. Read `MINE_LOG.md` to see what math papers have been mined.",
            "4. Read `SPRITE_LOG.md` to see what sprite references are in the library.",
            "5. Check `STAGNATION_LOG.md` for any unresolved stagnation issues.",
            "6. Continue from the highest-priority pending task above.",
            "7. When the user uploads new PDFs, run the distill pipeline with the next session ID.",
            "8. When the user provides sprite images, run the sprite analyzer.",
            "9. Always push changes to GitHub after completing a task.",
            "",
            "---",
            f"*Auto-generated by ProjectMemory at {self._now()}*",
        ]

        handoff = "\n".join(lines)

        # Save to file
        handoff_path = self.root / self.HANDOFF_FILE
        handoff_path.write_text(handoff, encoding="utf-8")

        return handoff

    # ── Persistence ────────────────────────────────────────────────────────────

    def _load(self) -> ProjectState:
        """Load state from PROJECT_BRAIN.json."""
        brain_path = self.root / self.BRAIN_FILE
        if brain_path.exists():
            try:
                data = json.loads(brain_path.read_text(encoding="utf-8"))
                return ProjectState.from_dict(data)
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

        # Default initial state
        return ProjectState(
            version="0.5.0",
            last_session_id="SESSION-000",
            last_updated=self._now(),
            best_quality_score=0.0,
            total_iterations=0,
            distill_session_id="DISTILL-004",
            mine_session_id="MINE-001",
            sprite_count=0,
            knowledge_rule_count=0,
            math_model_count=0,
        )

    def _save(self) -> None:
        """Save state to PROJECT_BRAIN.json."""
        brain_path = self.root / self.BRAIN_FILE
        brain_path.write_text(
            json.dumps(self.state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _append_history(self, record: EvolutionRecord) -> None:
        """Append to EVOLUTION_HISTORY.md."""
        history_path = self.root / self.HISTORY_FILE
        if not history_path.exists():
            history_path.write_text(
                "# Evolution History — MarioTrickster-MathArt\n\n"
                "This file records every significant improvement to the project.\n\n",
                encoding="utf-8",
            )
        entry = (
            f"\n## {record.session_id} — v{record.version} ({record.timestamp[:10]})\n"
            f"- **Best score**: {record.best_score:.3f}\n"
            f"- **Tests**: {record.test_count}\n"
        )
        for change in record.changes:
            entry += f"- {change}\n"
        if record.notes:
            entry += f"\n> {record.notes}\n"
        with history_path.open("a", encoding="utf-8") as f:
            f.write(entry)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _increment_id(session_id: str) -> str:
        """Increment a session ID like DISTILL-003 → DISTILL-004."""
        match = re.match(r'^([A-Z_-]+)(\d+)$', session_id)
        if match:
            prefix = match.group(1)
            num    = int(match.group(2))
            return f"{prefix}{num + 1:03d}"
        return session_id + "_1"


# ── SessionHandoff helper ──────────────────────────────────────────────────────

class SessionHandoff:
    """Helper for reading and writing session handoff information.

    Usage at the START of a new session:
        handoff = SessionHandoff.read(project_root)
        print(handoff.summary())

    Usage at the END of a session:
        handoff = SessionHandoff(memory)
        handoff.write(changes=["Added X", "Fixed Y"], best_score=0.75)
    """

    def __init__(self, memory: ProjectMemory) -> None:
        self.memory = memory

    def write(
        self,
        session_id: str,
        changes: list[str],
        best_score: float,
        test_count: int,
        pending_tasks: Optional[list[dict]] = None,
        notes: str = "",
    ) -> str:
        """Write session handoff at the end of a session.

        Returns the generated handoff document text.
        """
        version = self.memory.state.version
        self.memory.record_evolution(
            session_id=session_id,
            version=version,
            changes=changes,
            best_score=best_score,
            test_count=test_count,
            notes=notes,
        )

        # Add any new pending tasks
        if pending_tasks:
            for task in pending_tasks:
                self.memory.add_pending_task(**task)

        return self.memory.generate_handoff()

    @staticmethod
    def read(project_root: Optional[Path] = None) -> "SessionHandoff":
        """Read the current session handoff."""
        memory = ProjectMemory(project_root)
        return SessionHandoff(memory)

    def summary(self) -> str:
        """Return a concise summary for the start of a new session."""
        s = self.memory.state
        lines = [
            f"PROJECT: MarioTrickster-MathArt v{s.version}",
            f"Last session: {s.last_session_id} | Best score: {s.best_quality_score:.3f}",
            f"Knowledge: {s.knowledge_rule_count} rules | {s.math_model_count} models | {s.sprite_count} sprites",
            f"Next distill ID: {s.distill_session_id} | Next mine ID: {s.mine_session_id}",
        ]
        if s.pending_tasks:
            high_priority = [t for t in s.pending_tasks if t.priority == "high"]
            if high_priority:
                lines.append(f"HIGH PRIORITY TASKS: {len(high_priority)}")
                for t in high_priority[:3]:
                    lines.append(f"  → {t.description}")
        if s.capability_gaps:
            lines.append(f"CAPABILITY GAPS: {len(s.capability_gaps)}")
            for g in s.capability_gaps[:3]:
                lines.append(f"  ⚠ {g.name}: requires {g.requires}")
        return "\n".join(lines)
