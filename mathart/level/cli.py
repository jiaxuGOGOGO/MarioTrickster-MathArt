"""CLI for WFC level generation."""
from __future__ import annotations
import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mathart-level",
        description="Generate ASCII level layouts via Wave Function Collapse.",
    )
    parser.add_argument(
        "--width", type=int, default=22, help="Level width in tiles (default: 22)"
    )
    parser.add_argument(
        "--height", type=int, default=7, help="Level height in tiles (default: 7)"
    )
    parser.add_argument(
        "--count", type=int, default=1, help="Number of levels to generate"
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument(
        "--output", type=str, default=None, help="Output file path (prints to stdout if omitted)"
    )
    parser.add_argument(
        "--no-ground", action="store_true", help="Don't force bottom row to be ground"
    )
    parser.add_argument(
        "--no-spawn", action="store_true", help="Don't ensure Mario spawn point"
    )
    parser.add_argument(
        "--no-goal", action="store_true", help="Don't ensure goal zone"
    )
    args = parser.parse_args(argv)

    from .wfc import WFCGenerator

    gen = WFCGenerator(seed=args.seed)
    gen.learn()

    results = []
    for i in range(args.count):
        level = gen.generate(
            width=args.width,
            height=args.height,
            ensure_ground=not args.no_ground,
            ensure_spawn=not args.no_spawn,
            ensure_goal=not args.no_goal,
        )
        results.append(level)

    output_text = "\n\n---\n\n".join(results)

    if args.output:
        Path(args.output).write_text(output_text, encoding="utf-8")
        print(f"Saved {args.count} level(s) to {args.output}")
    else:
        print(output_text)


if __name__ == "__main__":
    main()
