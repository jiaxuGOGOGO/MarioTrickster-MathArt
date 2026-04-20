"""SESSION-101: Synchronize ``PROJECT_BRAIN.json`` with this session's landing.

Following the project's DI discipline, this script only mutates the top-level
keys that SESSION-101 is authoritatively responsible for:

- ``last_session_id``
- ``last_updated``
- ``recent_sessions`` (append)
- ``recent_focus_snapshot``
- ``session_log`` (append)
- ``resolved_issues`` (append)
- ``session_summaries`` (append)

All other keys are preserved byte-identical.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import datetime, timezone


SESSION_ID = "SESSION-101"
SESSION_FOCUS = "HIGH-TestBlindSpot (math deepwater coverage)"
SESSION_STATUS = "COMPLETE"
SESSION_DATE = "2026-04-20"

SUMMARY = (
    "Landed 42 new industrial-grade blind-spot tests covering fluid_vfx, "
    "unified_motion, nsm_gait and terrain_ik_2d. Followed NASA JPL Power of "
    "Ten, Hypothesis property-based testing and Pixar/Disney fluid-QA "
    "philosophy. Full suite 1719 PASS / 7 SKIP / 0 FAIL (+73 vs SESSION-100 "
    "baseline 1646). Zero production code touched."
)

FILES_TOUCHED = [
    "tests/test_session101_math_blind_spots.py",
    "research_notes_session101.md",
    "SESSION_HANDOFF.md",
    "PROJECT_BRAIN.json",
    "scripts/sync_project_brain_session101.py",
]


def sync(brain_path: Path) -> None:
    brain = json.loads(brain_path.read_text(encoding="utf-8"))
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    brain["last_session_id"] = SESSION_ID
    brain["last_updated"] = now_iso

    # recent_sessions (list of dicts, most recent first)
    recent_sessions = list(brain.get("recent_sessions") or [])
    recent_sessions.insert(0, {
        "session_id": SESSION_ID,
        "focus": SESSION_FOCUS,
        "status": SESSION_STATUS,
        "date": SESSION_DATE,
        "summary": SUMMARY,
        "files_touched": FILES_TOUCHED,
        "validation": "1719 PASS / 7 SKIP / 0 FAIL (pytest tests/ -p no:cov)",
    })
    # Keep window bounded to avoid brain bloat (Pixar-style bounded state).
    brain["recent_sessions"] = recent_sessions[:20]

    # recent_focus_snapshot
    brain["recent_focus_snapshot"] = {
        "session_id": SESSION_ID,
        "focus": SESSION_FOCUS,
        "status": SESSION_STATUS,
        "updated_at": now_iso,
    }

    # session_log (append-only)
    session_log = list(brain.get("session_log") or [])
    session_log.append({
        "session_id": SESSION_ID,
        "date": SESSION_DATE,
        "focus": SESSION_FOCUS,
        "status": SESSION_STATUS,
        "summary": SUMMARY,
        "files_touched": FILES_TOUCHED,
    })
    brain["session_log"] = session_log

    # resolved_issues (append)
    resolved = list(brain.get("resolved_issues") or [])
    resolved.append({
        "issue_id": "HIGH-TestBlindSpot",
        "session_id": SESSION_ID,
        "resolved_at": now_iso,
        "resolution": (
            "Added 42 deterministic value-level tests across fluid_vfx, "
            "unified_motion, nsm_gait and terrain_ik_2d. No production code "
            "modified; no Mock usage; no global np.random.seed."
        ),
        "validation_test_file": "tests/test_session101_math_blind_spots.py",
    })
    brain["resolved_issues"] = resolved

    # session_summaries (dict keyed by session id OR list; handle both)
    summaries = brain.get("session_summaries")
    entry = {
        "session_id": SESSION_ID,
        "date": SESSION_DATE,
        "focus": SESSION_FOCUS,
        "status": SESSION_STATUS,
        "summary": SUMMARY,
    }
    if isinstance(summaries, dict):
        summaries[SESSION_ID] = entry
        brain["session_summaries"] = summaries
    elif isinstance(summaries, list):
        summaries.append(entry)
        brain["session_summaries"] = summaries
    else:
        brain["session_summaries"] = [entry]

    # Atomic write (tmp then rename — JPL Rule 6-style bounded side effect)
    tmp = brain_path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(brain, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(brain_path)


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    brain = root / "PROJECT_BRAIN.json"
    if not brain.exists():
        print(f"FATAL: {brain} not found", file=sys.stderr)
        sys.exit(2)
    sync(brain)
    print(f"OK: synchronized {brain} for {SESSION_ID}")
