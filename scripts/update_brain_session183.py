#!/usr/bin/env python3
"""Update PROJECT_BRAIN.json for SESSION-183."""
import json
from datetime import datetime

BRAIN_PATH = "PROJECT_BRAIN.json"

with open(BRAIN_PATH, "r") as f:
    brain = json.load(f)

# ── Update top-level fields ──────────────────────────────────────
brain["version"] = "v0.99.21"
brain["last_session_id"] = "SESSION-183"
brain["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# ── Add SESSION-183 task to top_priorities_ordered ────────────────
session_183_entry = {
    "id": "P0-SESSION-183-MICROKERNEL-HUB-AND-VAT-INTEGRATION",
    "title": "Microkernel Dynamic Dispatch Hub & High-Precision Float VAT Pipeline Integration",
    "priority": "P0",
    "status": "CLOSED",
    "session_landed": "SESSION-183",
    "modules": [
        "mathart/laboratory_hub.py",
        "mathart/core/high_precision_vat_backend.py",
        "mathart/core/backend_registry.py",
        "mathart/cli_wizard.py",
        "docs/USER_GUIDE.md",
        "docs/RESEARCH_NOTES_SESSION_183.md",
        "SESSION_HANDOFF.md",
        "PROJECT_BRAIN.json"
    ],
    "tests": [
        "tests/test_session183_laboratory_hub.py"
    ],
    "description": (
        "SESSION-183 Microkernel Hub & VAT Integration: "
        "(1) Built Laboratory Hub CLI — reflection-based dynamic microkernel dispatch center "
        "accessible via [6] in main menu. Uses BackendRegistry.all_backends() + Python __doc__ "
        "introspection to dynamically enumerate all registered backends as interactive numbered menu. "
        "ZERO hardcoded if/else routing. Circuit Breaker pattern for fail-safe execution. "
        "All experimental outputs sandboxed to workspace/laboratory/<backend_name>/. "
        "(2) Revived 978-line dormant high_precision_vat.py via Adapter pattern — "
        "high_precision_vat_backend.py wraps the existing HDR float VAT baking module "
        "as a registered backend (BackendType: high_precision_vat, ArtifactFamily: VAT_BUNDLE, "
        "Capability: VAT_EXPORT). Auto-discovered via importlib.import_module in get_registry(). "
        "Full Float32 precision pipeline: global bounding-box normalization, .npy + .hdr + Hi-Lo PNG "
        "triple export, Catmull-Rom spline synthetic physics when no upstream data. "
        "(3) UX anti-corrosion: Sci-fi baking banner '[⚙️ 工业烘焙网关] 正在通过 Catmull-Rom 样条插值...' "
        "printed during execution. (4) DaC compliance: USER_GUIDE.md Section 13 added. "
        "(5) BackendRegistry.reset() fixed to also reset _builtins_loaded for test isolation. "
        "Research grounding: Martin Fowler Feature Toggles, SideFX Houdini VAT 3.0 spec, "
        "Microkernel Architecture (POSA Vol.1). 9 tests, all passing. "
        "All red lines strictly observed: zero internal math modification, zero production vault pollution, "
        "zero hardcoded routing."
    )
}

# Insert at the beginning of top_priorities_ordered
if "gap_inventory" in brain and "top_priorities_ordered" in brain["gap_inventory"]:
    brain["gap_inventory"]["top_priorities_ordered"].insert(0, session_183_entry)

# ── Add to pending_tasks as well ─────────────────────────────────
brain["pending_tasks"].insert(0, session_183_entry)

# ── Write back ────────────────────────────────────────────────────
with open(BRAIN_PATH, "w") as f:
    json.dump(brain, f, indent=2, ensure_ascii=False)

print(f"✅ PROJECT_BRAIN.json updated: version={brain['version']}, "
      f"last_session={brain['last_session_id']}")
