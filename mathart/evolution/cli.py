"""CLI for the Self-Evolution Engine.

Commands:
  mathart-evolve status               — Show evolution system status
  mathart-evolve distill <file>       — Distill knowledge from a file
  mathart-evolve registry             — Show math model registry
  mathart-evolve gaps                 — Show capability gap report
  mathart-evolve eval <image>         — Evaluate an image's quality
  mathart-evolve add-sprite <image>   — Add a sprite reference to the library
  mathart-evolve add-sheet <image>    — Add a spritesheet (auto-cut into frames)
  mathart-evolve sprites              — Show sprite library status
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

    # add-sprite (TASK-002)
    p_sprite = sub.add_parser("add-sprite", help="Add a sprite reference to the library")
    p_sprite.add_argument("image", help="Path to sprite image file")
    p_sprite.add_argument("--type", dest="sprite_type", default="unknown",
                          choices=["character", "tile", "effect", "ui", "unknown"],
                          help="Sprite type hint (default: auto-detect)")
    p_sprite.add_argument("--name", default=None,
                          help="Override sprite name (default: filename)")
    p_sprite.add_argument("--tags", default=None,
                          help="Comma-separated tags (e.g., 'mario,idle,16x16')")

    # add-sheet (TASK-002 extension: spritesheet support)
    p_sheet = sub.add_parser("add-sheet", help="Add a spritesheet (auto-cut into frames)")
    p_sheet.add_argument("image", help="Path to spritesheet image file")
    p_sheet.add_argument("--type", dest="sprite_type", default="character",
                         choices=["character", "tile", "effect", "ui", "unknown"],
                         help="Sprite type hint (default: character)")
    p_sheet.add_argument("--name", default=None,
                         help="Override sprite name (default: filename)")
    p_sheet.add_argument("--cell-size", default=None,
                         help="Cell size as WxH (e.g., '32x32'). Auto-detect if omitted.")
    p_sheet.add_argument("--row", type=int, default=None,
                         help="Extract only this row (0-indexed). All rows if omitted.")

    # sprites (library status)
    sub.add_parser("sprites", help="Show sprite library status")

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
    elif args.command == "add-sprite":
        _cmd_add_sprite(args)
    elif args.command == "add-sheet":
        _cmd_add_sheet(args)
    elif args.command == "sprites":
        _cmd_sprites()
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
    print("\n+ Covered:")
    for c in report["covered"]:
        print(f"  {c}")
    if report["experimental"]:
        print("\n~ Experimental:")
        for c in report["experimental"]:
            print(f"  {c}")
    if report["missing"]:
        print("\n- Missing:")
        for c in report["missing"]:
            print(f"  {c}")
    if report["recommendations"]:
        print("\nRecommendations:")
        for r in report["recommendations"]:
            print(f"  -> {r}")


def _cmd_eval(args):
    from PIL import Image
    from ..evaluator.evaluator import AssetEvaluator

    image = Image.open(args.image)
    reference = Image.open(args.reference) if args.reference else None
    evaluator = AssetEvaluator(reference=reference)
    result = evaluator.evaluate(image)
    print(result.summary())


def _cmd_add_sprite(args):
    """Add a single sprite image to the library."""
    from PIL import Image
    from ..sprite.library import SpriteLibrary

    filepath = Path(args.image)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    root = _find_project_root()
    lib = SpriteLibrary(project_root=root)

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else None
    name = args.name or filepath.stem

    img = Image.open(filepath)
    fp, is_new = lib.add_sprite(
        image=img,
        source_name=name,
        source_path=str(filepath.resolve()),
        sprite_type=args.sprite_type,
        tags=tags,
    )

    if is_new:
        print(f"\n[NEW] Added sprite: {name}")
    else:
        print(f"\n[DUP] Sprite already in library: {name}")

    print(fp.quality_summary())
    print(f"\nLibrary now has {lib.count()} sprite(s)")

    # Show extracted constraints
    constraints = fp.to_constraints()
    if constraints:
        print("\nExtracted constraints:")
        for param, (lo, hi) in sorted(constraints.items()):
            print(f"  {param}: [{lo:.3f}, {hi:.3f}]")


def _cmd_add_sheet(args):
    """Add a spritesheet: auto-cut into frames, analyze, and store."""
    from PIL import Image
    from ..sprite.library import SpriteLibrary
    from ..sprite.sheet_parser import SpriteSheetParser

    filepath = Path(args.image)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    root = _find_project_root()
    lib = SpriteLibrary(project_root=root)
    parser = SpriteSheetParser()

    img = Image.open(filepath)
    name = args.name or filepath.stem

    # Parse spritesheet
    if args.cell_size:
        try:
            w, h = args.cell_size.lower().split("x")
            result = parser.parse_uniform(img, cell_width=int(w), cell_height=int(h))
        except ValueError:
            print(f"Error: Invalid cell size format: {args.cell_size} (expected WxH, e.g., 32x32)")
            sys.exit(1)
    else:
        result = parser.parse_auto(img)

    print(f"\n{result.summary()}")

    # Extract frames
    if args.row is not None:
        frames = [f.image for f in result.get_animation_row(args.row)]
        row_label = f"_row{args.row}"
    else:
        frames = [f.image for f in result.frames if f.has_content]
        row_label = ""

    if not frames:
        print("Error: No frames with content found in spritesheet")
        sys.exit(1)

    # Add as animation sequence
    fp, is_new = lib.add_frames(
        frames=frames,
        source_name=f"{name}{row_label}",
        source_path=str(filepath.resolve()),
        sprite_type=args.sprite_type,
    )

    if is_new:
        print(f"\n[NEW] Added animation: {name}{row_label} ({len(frames)} frames)")
    else:
        print(f"\n[DUP] Animation already in library: {name}{row_label}")

    print(fp.quality_summary())
    print(f"\nLibrary now has {lib.count()} sprite(s)")


def _cmd_sprites():
    """Show sprite library status."""
    from ..sprite.library import SpriteLibrary

    root = _find_project_root()
    lib = SpriteLibrary(project_root=root)

    if lib.count() == 0:
        print("\nSprite Library: empty")
        print("  Use 'mathart-evolve add-sprite <image>' to add references")
        return

    stats = lib.get_stats()
    print(f"\n{stats.summary()}")

    # Show best references
    best = lib.get_best_references(top_n=5)
    if best:
        print("\nTop references by quality:")
        for i, fp in enumerate(best, 1):
            print(f"  {i}. {fp.source_name} ({fp.sprite_type}) — "
                  f"quality={fp.quality_score:.3f}, "
                  f"colors={fp.color.color_count}")


def _find_project_root() -> Path:
    """Find the project root by looking for pyproject.toml."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


if __name__ == "__main__":
    main()
