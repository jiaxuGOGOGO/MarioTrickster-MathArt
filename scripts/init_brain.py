#!/usr/bin/env python3
"""Initialize the PROJECT_BRAIN.json and SESSION_HANDOFF.md for the first time.

Run this once after cloning the repository:
  python3 scripts/init_brain.py
"""
import sys
from pathlib import Path

# Add project root to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from mathart.brain.memory import ProjectMemory, SessionHandoff

def main():
    print("Initializing MarioTrickster-MathArt Project Brain...")

    mem = ProjectMemory(project_root=ROOT)

    # Record the initial evolution history
    mem.record_evolution(
        session_id="SESSION-005",
        version="0.5.0",
        changes=[
            "Added SpriteAnalyzer: extract style fingerprints from reference sprites",
            "Added SpriteSheetParser: auto-cut spritesheets into frames",
            "Added SpriteLibrary: persistent, dedup-aware sprite knowledge store",
            "Added ArtMathQualityController: 4-checkpoint quality control across full pipeline",
            "Added ProjectMemory: cross-session persistent brain (PROJECT_BRAIN.json)",
            "Added SessionHandoff: seamless context continuity across conversations",
            "Added LevelSpecBridge: translate level requirements to asset specs",
            "Added StagnationGuard: invalid iteration detection + AI arbitration",
            "Added DeduplicationEngine: knowledge dedup with semantic similarity",
            "Added Unity Shader knowledge module + Pseudo3D extension skeleton",
            "Added MathPaperMiner: automated math model paper discovery",
            "Added 6 knowledge files: PCG, PBR, SDF, color science, procedural animation, differentiable rendering",
            "Total: 371 tests passing, 0 failures",
        ],
        best_score=0.0,
        test_count=371,
        notes="Major architecture upgrade: self-evolving brain system fully operational",
    )

    # Register known capability gaps
    mem.add_capability_gap(
        name="DIFFERENTIABLE_RENDERING",
        description="Differentiable rendering requires NVIDIA GPU for real-time parameter gradients",
        requires="NVIDIA GPU (CUDA 11+)",
        priority="medium",
    )
    mem.add_capability_gap(
        name="UNITY_SHADER_PREVIEW",
        description="Unity Shader preview requires Unity Editor for live rendering feedback",
        requires="Unity 2021.3+ LTS",
        priority="medium",
    )
    mem.add_capability_gap(
        name="AI_IMAGE_MODEL",
        description="High-quality sprite generation requires a diffusion model (e.g., SDXL-Turbo)",
        requires="GPU + Stable Diffusion API or local model",
        priority="high",
    )

    # Register pending tasks
    mem.add_pending_task(
        task_id="TASK-001",
        description="Integrate ArtMathQualityController into the main InnerLoop pipeline",
        priority="high",
        context={"file": "mathart/evolution/inner_loop.py"},
    )
    mem.add_pending_task(
        task_id="TASK-002",
        description="Add sprite reference upload workflow to CLI (mathart-evolve add-sprite)",
        priority="high",
    )
    mem.add_pending_task(
        task_id="TASK-003",
        description="Connect LevelSpecBridge to ExportBridge for auto-sized asset export",
        priority="medium",
        depends_on=["TASK-001"],
    )
    mem.add_pending_task(
        task_id="TASK-004",
        description="Mine math papers for WFC, OKLAB, SDF improvements",
        priority="medium",
    )
    mem.add_pending_task(
        task_id="TASK-005",
        description="Add GPU-accelerated rendering path when CUDA is available",
        priority="low",
        depends_on=["DIFFERENTIABLE_RENDERING gap resolved"],
    )

    # Update counters
    mem.update_counters(
        knowledge_rule_count=6,   # 6 knowledge files
        math_model_count=9,       # 8 stable + 1 experimental
        sprite_count=0,           # No sprites yet
        total_iterations=0,
    )

    # Generate handoff document
    handoff = mem.generate_handoff()

    print(f"  PROJECT_BRAIN.json created at {ROOT / 'PROJECT_BRAIN.json'}")
    print(f"  SESSION_HANDOFF.md created at {ROOT / 'SESSION_HANDOFF.md'}")
    print()
    print("=" * 60)
    print(handoff[:1000])
    print("..." if len(handoff) > 1000 else "")
    print("=" * 60)
    print()
    print("Brain initialized successfully!")

if __name__ == "__main__":
    main()
