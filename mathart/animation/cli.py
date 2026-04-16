"""CLI for skeletal animation generation — SESSION-040 UMR Contract Enforcement.

SESSION-040 UPGRADE: This CLI now enforces the UMR pipeline contract.
All animation generation goes through the ``AssetPipeline.produce_character_pack()``
trunk with a ``UMR_Context``. Direct invocation of legacy pose functions is
forbidden under the SESSION-040 data contract (Mike Acton DOD principle).

The old CLI directly called ``idle_animation()``, ``run_animation()``, etc.
and rendered them without UMR framing, physics compliance, or biomechanics
grounding. This was a contract bypass that could produce output inconsistent
with the main pipeline. The new CLI constructs a ``CharacterSpec`` and
delegates to the full trunk.

Usage::

    mathart-anim idle -o output_idle.png --size 32 --frames 8
    mathart-anim run --head-units 2.5 --seed 123
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..pipeline import AssetPipeline, CharacterSpec
from ..pipeline_contract import UMR_Context, PipelineContractError


VALID_STATES = {"idle", "run", "jump", "fall", "hit"}


def main(argv=None):
    """Entry point for the mathart-anim CLI.

    SESSION-040: All paths now go through the UMR trunk. Legacy pose
    functions are no longer directly invocable from this CLI.
    """
    parser = argparse.ArgumentParser(
        prog="mathart-anim",
        description="Generate skeletal animation sprite sheets (UMR trunk enforced).",
    )
    parser.add_argument(
        "action",
        choices=sorted(VALID_STATES),
        help="Animation state to generate.",
    )
    parser.add_argument("-o", "--output-dir", default="output/anim_cli")
    parser.add_argument("--size", type=int, default=32, help="Frame size in pixels.")
    parser.add_argument("--frames", type=int, default=8, help="Number of frames.")
    parser.add_argument("--head-units", type=float, default=3.0, help="Character head units.")
    parser.add_argument("--palette", default=None, help="Path to palette JSON.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument("--preset", default="mario", help="Character preset.")
    parser.add_argument(
        "--no-physics", action="store_true",
        help="Disable physics compliance projector.",
    )
    parser.add_argument(
        "--no-biomechanics", action="store_true",
        help="Disable biomechanics grounding projector.",
    )
    args = parser.parse_args(argv)

    # Construct a CharacterSpec that goes through the full UMR trunk
    char_spec = CharacterSpec(
        name=f"cli_{args.action}",
        preset=args.preset,
        frame_width=args.size,
        frame_height=args.size,
        fps=12,
        head_units=args.head_units,
        frames_per_state=args.frames,
        states=[args.action],
        enable_physics=not args.no_physics,
        enable_biomechanics=not args.no_biomechanics,
    )

    # Build UMR_Context — the immutable contract for this run
    context = UMR_Context.from_character_spec(char_spec, session_id="CLI-040")

    # Run through the full pipeline trunk
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    pipeline = AssetPipeline(output_dir=str(output_dir), verbose=True)

    try:
        result = pipeline.produce_character_pack(char_spec)
        print(
            f"[UMR-CLI] Generated {args.action} pack: "
            f"{len(result.output_paths)} artifacts, "
            f"score={result.score:.4f}, "
            f"context_hash={context.context_hash[:12]}..."
        )
        for path in result.output_paths:
            print(f"  -> {path}")
    except PipelineContractError as e:
        print(f"[CONTRACT VIOLATION] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
