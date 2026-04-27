"""V6 environment pipeline — knowledge-synced WFC level generation."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mathart.core.knowledge_interpreter import DEFAULT_KNOWLEDGE, interpret_knowledge
from mathart.level.wfc import WFCGenerator


@dataclass(frozen=True)
class V6EnvironmentResult:
    output_dir: str
    knowledge_source: str
    level_path: str
    metadata_path: str
    width: int
    height: int
    jump_profile: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_knowledge(output_dir: Path, local_path: str | None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "v6_environment_knowledge.json"
    payload = dict(DEFAULT_KNOWLEDGE)
    payload.setdefault("source_book", "Platformer Level Design + Animation Physics Notes")
    if local_path and Path(local_path).exists():
        loaded = json.loads(Path(local_path).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload.update(loaded)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


def _jump_profile(knowledge) -> dict[str, float]:
    stretch = float(knowledge.physics.squash_max_stretch)
    anticipation = float(knowledge.physics.anticipation_weight)
    impact = float(knowledge.physics.impact_reward_weight)
    max_jump_tiles = max(2.0, min(7.0, 2.4 + stretch * 1.2 + anticipation * 0.55))
    safe_gap_tiles = max(1.0, min(5.0, max_jump_tiles - 1.0 + impact * 0.25))
    rhythm = max(2.0, min(8.0, float(knowledge.timing.step_rate) + anticipation + impact))
    return {
        "max_jump_tiles": max_jump_tiles,
        "safe_gap_tiles": safe_gap_tiles,
        "rhythm_tiles": rhythm,
    }


def run_environment_pipeline(args: argparse.Namespace) -> V6EnvironmentResult:
    output_dir = Path(args.output_dir).resolve()
    knowledge_path = _ensure_knowledge(output_dir, args.knowledge_json)
    knowledge = interpret_knowledge(knowledge_path)
    profile = _jump_profile(knowledge)
    width = int(args.width or max(22, round(profile["rhythm_tiles"] * 5)))
    height = int(args.height or max(7, round(profile["max_jump_tiles"] + 4)))
    level = WFCGenerator(seed=args.seed).learn().generate(width=width, height=height, ensure_ground=True, ensure_spawn=True, ensure_goal=True)
    level_path = output_dir / "v6_wfc_environment_level.txt"
    level_path.write_text(level + "\n", encoding="utf-8")
    metadata = {
        "generator": "run_v6_environment_pipeline",
        "knowledge_source": knowledge.source_path,
        "width": width,
        "height": height,
        "jump_profile": profile,
        "timing": knowledge.timing.to_dict(),
        "physics": knowledge.physics.to_dict(),
        "wfc_contract": "terrain rhythm constrained by KnowledgeInterpreter jump capability",
    }
    metadata_path = output_dir / "v6_wfc_environment_meta.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return V6EnvironmentResult(
        output_dir=str(output_dir),
        knowledge_source=knowledge.source_path,
        level_path=str(level_path),
        metadata_path=str(metadata_path),
        width=width,
        height=height,
        jump_profile=profile,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run V6 knowledge-synced WFC environment pipeline.")
    parser.add_argument("--output-dir", default="outputs/v6_environment")
    parser.add_argument("--knowledge-json", default=None)
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--seed", type=int, default=42)
    return parser


def main() -> None:
    result = run_environment_pipeline(build_arg_parser().parse_args())
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
