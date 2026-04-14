"""CLI for the Self-Evolution Engine.

Commands:
  mathart-evolve status          — Show evolution system status
  mathart-evolve distill <file>  — Distill knowledge from a file
  mathart-evolve registry        — Show math model registry
  mathart-evolve gaps            — Show capability gap report
  mathart-evolve eval <image>    — Evaluate an image's quality
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mathart-evolve",
        description="MarioTrickster-MathArt Self-Evolution Engine",
    )
    sub = parser.add_subparsers(dest="command")

    # status
    sub.add_parser("status", help="Show evolution system status")

    # distill
    p_distill = sub.add_parser("distill", help="Distill knowledge from a file")
    p_distill.add_argument("file", help="Path to PDF, Markdown, or text file")
    p_distill.add_argument("--source-name", default=None,
                           help="Override source name (default: filename)")
    p_distill.add_argument("--no-llm", action="store_true",
                           help="Use heuristic extraction only (no LLM)")

    # registry
    p_reg = sub.add_parser("registry", help="Show math model registry")
    p_reg.add_argument("--save", default=None,
                       help="Save registry to JSON file")

    # gaps
    sub.add_parser("gaps", help="Show capability gap report")

    # eval
    p_eval = sub.add_parser("eval", help="Evaluate image quality")
    p_eval.add_argument("image", help="Path to image file")
    p_eval.add_argument("--reference", default=None,
                        help="Reference image for style consistency check")

    args = parser.parse_args(argv)

    if args.command == "status":
        _cmd_status()
    elif args.command == "distill":
        _cmd_distill(args)
    elif args.command == "registry":
        _cmd_registry(args)
    elif args.command == "gaps":
        _cmd_gaps()
    elif args.command == "eval":
        _cmd_eval(args)
    else:
        parser.print_help()


def _cmd_status():
    from .engine import SelfEvolutionEngine
    engine = SelfEvolutionEngine(project_root=_find_project_root())
    engine.status()


def _cmd_distill(args):
    from .engine import SelfEvolutionEngine
    engine = SelfEvolutionEngine(project_root=_find_project_root())
    use_llm = not args.no_llm
    engine.outer_loop.use_llm = use_llm
    result = engine.outer_loop.distill_file(args.file, source_name=args.source_name)
    print(f"\n{result.summary()}")
    if result.knowledge_files_updated:
        print(f"Updated files: {', '.join(result.knowledge_files_updated)}")


def _cmd_registry(args):
    from .engine import SelfEvolutionEngine
    engine = SelfEvolutionEngine(project_root=_find_project_root(), verbose=False)
    print(engine.math_registry.summary_table())
    if args.save:
        engine.save_registry(args.save)
        print(f"\nRegistry saved to {args.save}")


def _cmd_gaps():
    from .engine import SelfEvolutionEngine
    engine = SelfEvolutionEngine(project_root=_find_project_root(), verbose=False)
    report = engine.capability_gap_report()
    print("\n✅ Covered:")
    for c in report["covered"]:
        print(f"  {c}")
    if report["experimental"]:
        print("\n~ Experimental:")
        for c in report["experimental"]:
            print(f"  {c}")
    if report["missing"]:
        print("\n✗ Missing:")
        for c in report["missing"]:
            print(f"  {c}")
    if report["recommendations"]:
        print("\n💡 Recommendations:")
        for r in report["recommendations"]:
            print(f"  → {r}")


def _cmd_eval(args):
    from PIL import Image
    from ..evaluator.evaluator import AssetEvaluator

    image = Image.open(args.image)
    reference = Image.open(args.reference) if args.reference else None
    evaluator = AssetEvaluator(reference=reference)
    result = evaluator.evaluate(image)
    print(result.summary())


def _find_project_root() -> Path:
    """Find the project root by looking for pyproject.toml."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


if __name__ == "__main__":
    main()
