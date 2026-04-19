from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mathart.core.taichi_xpbd_backend import TaichiXPBDBackend


def run_case(output_dir: Path, name: str, device: str) -> dict:
    backend = TaichiXPBDBackend()
    ctx, warnings = backend.validate_config(
        {
            "output_dir": str(output_dir),
            "name": name,
            "benchmark_device": device,
            "benchmark_scenario": "free_fall_cloud",
            "benchmark_frame_count": 12,
            "benchmark_warmup_frames": 4,
            "benchmark_sample_count": 5,
            "particle_budget": 1024,
        }
    )
    manifest = backend.execute(ctx)
    report = json.loads(Path(manifest.outputs["report_file"]).read_text(encoding="utf-8"))
    return {
        "name": name,
        "device_request": device,
        "warnings": warnings,
        "manifest": {
            "artifact_family": manifest.artifact_family,
            "report_file": manifest.outputs["report_file"],
        },
        "report": report,
    }


def main() -> None:
    output_dir = REPO_ROOT / "reports" / "session082_gpu_benchmark"
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "cpu_request": run_case(output_dir, "session082_cpu", "cpu"),
        "gpu_request": run_case(output_dir, "session082_gpu", "gpu"),
    }
    summary_path = output_dir / "session082_benchmark_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
