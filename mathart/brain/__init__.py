"""PROJECT BRAIN — cross-session persistent memory and context system.

This module ensures that every new conversation with the AI agent
can seamlessly continue from where the last one left off.

The brain stores:
  1. Project state (current version, last iteration, best scores)
  2. Knowledge provenance (which session contributed which rules)
  3. Pending tasks (what was planned but not yet executed)
  4. Capability gaps (what external tools/hardware are needed)
  5. Evolution history (full log of all improvements)
  6. Session handoff notes (what the AI needs to know to resume)

Storage: PROJECT_BRAIN.json (machine-readable) + SESSION_HANDOFF.md (human-readable)

Usage in a new conversation:
  1. AI reads PROJECT_BRAIN.json to understand current state
  2. AI reads SESSION_HANDOFF.md for human-readable context
  3. AI continues from the last pending task
  4. All new work is recorded back to the brain

This is the "衔接" (continuity) mechanism that ensures the project
never loses context across conversation boundaries.
"""
from mathart.brain.memory import ProjectMemory, SessionHandoff
from mathart.brain.session_guard import SessionGuard, TaskFingerprintRecord, TaskRegistrationResult

__all__ = [
    "ProjectMemory",
    "SessionHandoff",
    "SessionGuard",
    "TaskFingerprintRecord",
    "TaskRegistrationResult",
]
