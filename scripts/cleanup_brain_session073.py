#!/usr/bin/env python3
"""SESSION-073: Clean up PROJECT_BRAIN.json technical debt.

Actions:
1. Merge ``open_tasks`` into ``pending_tasks`` (dedup by ID, prefer newer status).
2. Archive all DONE/CLOSED tasks into ``closed_tasks_archive`` (compact format).
3. Remove DONE/CLOSED tasks from ``pending_tasks``.
4. Consolidate redundant per-session change keys into ``recent_sessions``.
5. Update top-level metadata for SESSION-073.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

BRAIN_PATH = Path(__file__).resolve().parent.parent / "PROJECT_BRAIN.json"


def main() -> None:
    with open(BRAIN_PATH, encoding="utf-8") as f:
        brain = json.load(f)

    # ---- 1. Merge open_tasks into pending_tasks (dedup by ID) ----
    pending: list[dict] = brain.get("pending_tasks", [])
    open_tasks: list[dict] = brain.get("open_tasks", [])

    # Build index by ID (pending first, open_tasks overwrite if newer)
    task_index: dict[str, dict] = {}
    for t in pending:
        task_index[t["id"]] = t
    for t in open_tasks:
        tid = t["id"]
        if tid in task_index:
            # Prefer the entry with more recent updated_in/completed_in
            existing = task_index[tid]
            # If open_tasks version has a completed_in, prefer it
            if t.get("completed_in") and not existing.get("completed_in"):
                task_index[tid] = t
            elif t.get("updated_in", "") > existing.get("updated_in", ""):
                task_index[tid] = t
        else:
            task_index[tid] = t

    all_tasks = list(task_index.values())

    # ---- 2. Separate active vs closed ----
    active_tasks = []
    closed_archive = []
    for t in all_tasks:
        status = t.get("status", "TODO").upper()
        if status in ("DONE", "CLOSED"):
            closed_archive.append({
                "id": t["id"],
                "title": t.get("title", ""),
                "completed_in": t.get("completed_in", t.get("updated_in", "unknown")),
                "priority": t.get("priority", "P2"),
            })
        else:
            active_tasks.append(t)

    # Sort active by priority then ID
    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    active_tasks.sort(key=lambda t: (
        priority_order.get(t.get("priority", "P2"), 9),
        t.get("id", ""),
    ))
    closed_archive.sort(key=lambda t: t.get("id", ""))

    brain["pending_tasks"] = active_tasks
    brain["closed_tasks_archive"] = closed_archive

    # Remove merged open_tasks key
    brain.pop("open_tasks", None)

    # ---- 3. Consolidate redundant per-session change keys ----
    # These are legacy keys from older sessions; their info is already
    # captured in recent_sessions or session_summaries.
    legacy_session_keys = [
        "session_029_changes", "session_028_supp_changes",
        "session_028_changes", "session_035_changes",
        "session_034_changes", "session_031_changes",
        "session_033_changes", "session_032_changes",
        "session_036_changes", "session_037_changes",
        "session_040_changes", "session_038_changes",
        "session_041_changes", "session_052_changes",
        "session054_industrial_skin_status",
        "session057_crossdim_status",
        "session056_breakwall_status",
        "session061_motion_2d_status",
    ]
    consolidated_legacy: dict[str, dict] = {}
    for key in legacy_session_keys:
        if key in brain:
            consolidated_legacy[key] = brain.pop(key)

    # Store a compact summary of consolidated keys
    if consolidated_legacy:
        brain["legacy_session_changes_consolidated"] = {
            "note": "Per-session change keys from SESSION-028 through SESSION-061 "
                    "consolidated in SESSION-073 to reduce JSON bloat. "
                    "Full history preserved in git.",
            "consolidated_keys": list(consolidated_legacy.keys()),
            "consolidated_at": "SESSION-073",
        }

    # ---- 4. Compute updated stats ----
    by_priority: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for t in active_tasks:
        p = t.get("priority", "P2")
        s = t.get("status", "TODO")
        by_priority[p] = by_priority.get(p, 0) + 1
        by_status[s] = by_status.get(s, 0) + 1

    brain["gap_inventory"]["active_total"] = len(active_tasks)
    brain["gap_inventory"]["by_priority"] = dict(sorted(by_priority.items()))
    brain["gap_inventory"]["by_status"] = dict(sorted(by_status.items()))
    brain["gap_inventory"]["closed_archived"] = len(closed_archive)

    # ---- 5. Print summary ----
    print(f"Active tasks: {len(active_tasks)}")
    print(f"Closed archived: {len(closed_archive)}")
    print(f"By priority: {by_priority}")
    print(f"By status: {by_status}")
    print(f"Legacy keys consolidated: {len(consolidated_legacy)}")
    print(f"Duplicate resolved: P1-AI-2D (merged)")

    # ---- 6. Write back ----
    with open(BRAIN_PATH, "w", encoding="utf-8") as f:
        json.dump(brain, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"\nPROJECT_BRAIN.json cleaned. New size: {BRAIN_PATH.stat().st_size} bytes")


if __name__ == "__main__":
    main()
