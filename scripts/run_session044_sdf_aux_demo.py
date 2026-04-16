from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mathart.animation import Skeleton, render_character_maps_industrial
from mathart.animation.character_presets import get_preset
from mathart.animation.presets import idle_animation


def main() -> None:
    root = ROOT
    out_dir = root / "evolution_reports" / "session044_aux_demo"
    out_dir.mkdir(parents=True, exist_ok=True)

    skeleton = Skeleton.create_humanoid()
    style, palette = get_preset("mario")
    pose = idle_animation(0.0)
    result = render_character_maps_industrial(
        skeleton,
        pose,
        style,
        width=32,
        height=32,
        palette=palette,
    )

    result.albedo_image.save(out_dir / "mario_idle_albedo.png")
    result.normal_map_image.save(out_dir / "mario_idle_normal.png")
    result.depth_map_image.save(out_dir / "mario_idle_depth.png")
    result.mask_image.save(out_dir / "mario_idle_mask.png")

    payload = {
        "session_id": "SESSION-044",
        "artifact_dir": str(out_dir.relative_to(root)),
        "style": "mario",
        "pose": "idle_animation(0.0)",
        "metadata": result.metadata,
    }
    (out_dir / "session044_aux_demo.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
