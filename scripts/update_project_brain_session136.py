from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROJECT_BRAIN = ROOT / "PROJECT_BRAIN.json"


def main() -> None:
    data = json.loads(PROJECT_BRAIN.read_text(encoding="utf-8"))
    data["last_session_id"] = "SESSION-136"
    data["last_updated"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    data["validation_pass_rate"] = (
        "SESSION-136 audit: 4/4 dual-wizard dispatcher assertions PASS, 2/2 dynamic_cli_ipc regressions PASS, "
        "plus py_compile syntax validation for config_manager / git_agent / mode_dispatcher / cli_wizard / cli. "
        "Total: 6/6 targeted tests PASS. Research grounded in Twelve-Factor Config, PEP 810 lazy imports, "
        "OpenGitOps, GitHub prompt-versioning practice, and edge-cloud collaborative GenAI architecture."
    )

    closed_task = {
        "id": "P0-SESSION-132-133-DUAL-WIZARD",
        "priority": "P0",
        "title": "Dual Wizard Distillation Bus, Local/Cloud Split, and GitOps Knowledge Sync",
        "status": "CLOSED",
        "estimated_effort": "high",
        "description": "CLOSED in SESSION-136. Landed mathart/workspace/config_manager.py for local API credential onboarding with mandatory .gitignore shielding; added mathart/workspace/mode_dispatcher.py with strongly-typed SessionContext contracts and lazy-loaded production/evolution/local-distill/dry-run strategies; added mathart/cli_wizard.py top-level guided entry; added mathart/workspace/git_agent.py whitelist-only GitOps knowledge sync; added tools/PROMPTS/manus_cloud_distill.md Prompt-as-Code cloud distillation protocol; documented research grounding in docs/research/SESSION-136-DUAL-WIZARD-RESEARCH.md; and added tests/test_dual_wizard_dispatcher.py. Local verification: 6/6 targeted tests PASS.",
        "completed_in": "SESSION-136",
        "updated_in": "SESSION-136",
        "primary_files": [
            "mathart/workspace/config_manager.py",
            "mathart/workspace/mode_dispatcher.py",
            "mathart/workspace/git_agent.py",
            "mathart/cli_wizard.py",
            "mathart/cli.py",
            "tests/test_dual_wizard_dispatcher.py",
            "tools/PROMPTS/manus_cloud_distill.md",
            "docs/research/SESSION-136-DUAL-WIZARD-RESEARCH.md"
        ]
    }

    packaging_task = {
        "id": "P0-SESSION-137-PACKAGING-HARDENING",
        "priority": "P0",
        "title": "Standalone Client Packaging Hardening for TUI/Desktop Distribution",
        "status": "OPEN",
        "estimated_effort": "medium",
        "description": "Before shipping PyInstaller-style standalone .exe/.app builds for non-technical artists, harden four areas: (1) path resolution must pivot from source-tree-relative assumptions to runtime-aware app-data directories and frozen-binary resource lookup; (2) dynamic lazy imports used by the wizard/dispatcher must be declared to the packager via hidden-import manifests or hook files so production/evolution/distill lanes are not stripped; (3) local API credential storage must migrate from repo-root .env assumptions toward per-user secure config locations with explicit export/import tooling; and (4) embedded Git/GitHub workflows need fallback UX for machines without Git credentials or without a writable clone. Also audit browserless/manual handoff paths, code-signing readiness, and installer upgrade-safe config migration.",
        "added_in": "SESSION-136",
        "updated_in": "SESSION-136"
    }

    pending = [task for task in data.get("pending_tasks", []) if task.get("id") not in {closed_task["id"], packaging_task["id"]}]
    pending.insert(0, packaging_task)
    pending.insert(1, closed_task)
    data["pending_tasks"] = pending

    PROJECT_BRAIN.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
