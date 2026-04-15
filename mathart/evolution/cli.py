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
  mathart-evolve init-workspace       — Create inbox/output directory structure
  mathart-evolve scan                 — Scan inbox and auto-process all files
  mathart-evolve pick                 — Open file picker to select and import files
  mathart-evolve texture [preset]     — Generate a noise texture
  mathart-evolve run                  — Run the evolution loop on a built-in target
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
    p_sprite.add_argument("image", nargs="?", default=None,
                          help="Path to sprite image file (omit to open file picker)")
    p_sprite.add_argument("--type", dest="sprite_type", default="unknown",
                          choices=["character", "tile", "effect", "ui", "unknown"],
                          help="Sprite type hint (default: auto-detect)")
    p_sprite.add_argument("--name", default=None,
                          help="Override sprite name (default: filename)")
    p_sprite.add_argument("--tags", default=None,
                          help="Comma-separated tags (e.g., 'mario,idle,16x16')")

    # add-sheet (TASK-002 extension: spritesheet support)
    p_sheet = sub.add_parser("add-sheet", help="Add a spritesheet (auto-cut into frames)")
    p_sheet.add_argument("image", nargs="?", default=None,
                         help="Path to spritesheet image file (omit to open file picker)")
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

    # init-workspace
    sub.add_parser("init-workspace", help="Create inbox/output directory structure")

    # scan
    sub.add_parser("scan", help="Scan inbox and auto-process all files")

    # pick
    p_pick = sub.add_parser("pick", help="Open file picker to select and import files")
    p_pick.add_argument("--type", dest="pick_type", default="sprite",
                        choices=["sprite", "sheet", "knowledge"],
                        help="What type of file to import (default: sprite)")

    # texture (TASK-004)
    p_tex = sub.add_parser("texture", help="Generate a noise texture")
    p_tex.add_argument("preset", nargs="?", default=None,
                       choices=["terrain", "clouds", "lava", "water", "stone", "magic"],
                       help="Texture preset (omit to list all)")
    p_tex.add_argument("--size", type=int, default=64,
                       help="Output size in pixels (default: 64)")
    p_tex.add_argument("--seed", type=int, default=42,
                       help="Random seed (default: 42)")
    p_tex.add_argument("--colormap", default=None,
                       help="Override colormap (gray/earth/sky/lava/water/stone/magic)")
    p_tex.add_argument("-o", "--output", default=None,
                       help="Output path (default: auto-save to output/textures/)")
    p_tex.add_argument("--all", dest="gen_all", action="store_true",
                       help="Generate all 6 presets at once")

    # run (TASK-009)
    p_run = sub.add_parser("run", help="Run the evolution loop on a built-in target")
    p_run.add_argument("--target", default="texture", choices=["texture"],
                       help="Built-in evolution target (default: texture)")
    p_run.add_argument("--preset", default="terrain",
                       choices=["terrain", "clouds", "lava", "water", "stone", "magic"],
                       help="Built-in preset to optimize (default: terrain)")
    p_run.add_argument("--mode", default="autonomous",
                       choices=["autonomous", "assisted"],
                       help="Run mode (default: autonomous)")
    p_run.add_argument("--iterations", type=int, default=12,
                       help="Evolution generations to run (default: 12)")
    p_run.add_argument("--population", type=int, default=8,
                       help="Population size per generation (default: 8)")
    p_run.add_argument("--size", type=int, default=64,
                       help="Output image size in pixels (default: 64)")
    p_run.add_argument("--seed", type=int, default=42,
                       help="Random seed (default: 42)")
    p_run.add_argument("-o", "--output", default=None,
                       help="Output image path (default: auto-save to output/textures/)")

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
    elif args.command == "init-workspace":
        _cmd_init_workspace()
    elif args.command == "scan":
        _cmd_scan()
    elif args.command == "pick":
        _cmd_pick(args)
    elif args.command == "texture":
        _cmd_texture(args)
    elif args.command == "run":
        _cmd_run(args)
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

    # If no image path given, try file picker
    if args.image is None:
        args.image = _pick_single_file("Select sprite image", "sprite")
        if args.image is None:
            print("No file selected.")
            return

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

    # If no image path given, try file picker
    if args.image is None:
        args.image = _pick_single_file("Select spritesheet image", "sheet")
        if args.image is None:
            print("No file selected.")
            return

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


def _cmd_init_workspace():
    """Create the inbox/output directory structure."""
    from ..workspace.manager import WorkspaceManager

    root = _find_project_root()
    ws = WorkspaceManager(project_root=root)
    created = ws.init_workspace()

    print("\nWorkspace initialized!")
    print("\nInbox directories (drop files here):")
    for d in created["inbox"]:
        print(f"  {d}/")
    print("\nOutput directories (results saved here):")
    for d in created["output"]:
        print(f"  {d}/")
    print("\nUsage:")
    print("  1. Drop sprite images into inbox/sprites/")
    print("  2. Drop spritesheets into inbox/sheets/")
    print("  3. Drop PDFs/Markdown into inbox/knowledge/")
    print("  4. Run: mathart-evolve scan")


def _cmd_scan():
    """Scan inbox and auto-process all files."""
    from ..workspace.manager import WorkspaceManager

    root = _find_project_root()
    ws = WorkspaceManager(project_root=root)

    if not ws.inbox.exists():
        print("Inbox not found. Run 'mathart-evolve init-workspace' first.")
        return

    found = ws.scan_inbox()
    total = sum(len(v) for v in found.values())

    if total == 0:
        print("\nInbox is empty — nothing to process.")
        print("  Drop files into inbox/sprites/, inbox/sheets/, or inbox/knowledge/")
        return

    print(f"\nFound {total} file(s) in inbox:")
    for cat, files in found.items():
        if files:
            print(f"  {cat}: {', '.join(f.name for f in files)}")

    print("\nProcessing...")
    counts = ws.process_inbox(verbose=True)

    print(f"\nDone! Processed: {counts['sprites']} sprites, "
          f"{counts['sheets']} sheets, {counts['knowledge']} knowledge files")
    if counts["skipped"] > 0:
        print(f"  Skipped: {counts['skipped']} (see errors above)")
    print("  Processed files moved to inbox/processed/")


def _cmd_pick(args):
    """Open file picker dialog to select and import files."""
    from ..workspace.manager import pick_files

    filetypes_map = {
        "sprite": [("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All", "*.*")],
        "sheet": [("Images", "*.png *.jpg *.jpeg"), ("All", "*.*")],
        "knowledge": [("Documents", "*.pdf *.md *.txt *.markdown"), ("All", "*.*")],
    }

    title_map = {
        "sprite": "Select sprite reference images",
        "sheet": "Select spritesheet images",
        "knowledge": "Select knowledge files (PDF/Markdown)",
    }

    files = pick_files(
        title=title_map.get(args.pick_type, "Select files"),
        filetypes=filetypes_map.get(args.pick_type),
        multiple=True,
    )

    if not files:
        print("No files selected (or no GUI available).")
        print("  Alternative: drop files into inbox/ and run 'mathart-evolve scan'")
        return

    print(f"\nSelected {len(files)} file(s):")
    for f in files:
        print(f"  {f.name}")

    # Process each file based on type
    if args.pick_type == "sprite":
        from PIL import Image
        from ..sprite.library import SpriteLibrary
        root = _find_project_root()
        lib = SpriteLibrary(project_root=root)
        for f in files:
            img = Image.open(f)
            fp, is_new = lib.add_sprite(
                image=img, source_name=f.stem,
                source_path=str(f.resolve()), sprite_type="unknown",
            )
            status = "NEW" if is_new else "DUP"
            print(f"  [{status}] {f.name} — quality={fp.quality_score:.3f}")
        print(f"\nLibrary now has {lib.count()} sprite(s)")

    elif args.pick_type == "sheet":
        # Delegate to add-sheet for each file
        for f in files:
            print(f"\nProcessing sheet: {f.name}")
            main(["add-sheet", str(f)])

    elif args.pick_type == "knowledge":
        from ..evolution.outer_loop import OuterLoopDistiller
        root = _find_project_root()
        distiller = OuterLoopDistiller(project_root=root, verbose=True)
        for f in files:
            result = distiller.distill_file(str(f))
            print(f"  [DISTILL] {f.name} — {result.rules_extracted} rules")


def _cmd_run(args):
    """Run the inner evolution loop on a built-in target."""
    import json

    from ..distill.compiler import Constraint, ParameterSpace
    from ..sdf.noise import (
        TEXTURE_PRESETS,
        domain_warp,
        fbm,
        render_noise_texture,
        ridged_noise,
        turbulence,
    )
    from ..sprite.library import SpriteLibrary
    from ..workspace.manager import WorkspaceManager
    from .engine import SelfEvolutionEngine
    from .inner_loop import RunMode

    if args.target != "texture":
        raise ValueError(f"Unsupported evolution target: {args.target}")

    root = _find_project_root()
    ws = WorkspaceManager(project_root=root)
    tex_preset = TEXTURE_PRESETS[args.preset]

    space = ParameterSpace(name=f"evolve_{args.preset}_texture")

    def _add_range(name: str, lo: float, hi: float, default: float) -> None:
        space.add_constraint(Constraint(
            param_name=name,
            min_value=lo,
            max_value=hi,
            default_value=default,
        ))

    base = tex_preset.params
    _add_range("scale", max(2.0, float(base.get("scale", 8.0)) * 0.5),
               float(base.get("scale", 8.0)) * 1.75, float(base.get("scale", 8.0)))
    _add_range("octaves", 3.0, 8.0, float(base.get("octaves", 5)))
    if "lacunarity" in base:
        _add_range("lacunarity", 1.5, 3.2, float(base["lacunarity"]))
    if "persistence" in base:
        _add_range("persistence", 0.25, 0.85, float(base["persistence"]))
    if "gain" in base:
        _add_range("gain", 0.2, 0.85, float(base["gain"]))
    if "warp_strength" in base:
        _add_range("warp_strength", 0.1, 1.2, float(base["warp_strength"]))
    _add_range("contrast", 0.7, 1.5, 1.0)
    _add_range("brightness", 0.75, 1.25, 1.0)
    _add_range("transparent_below", 0.0, 0.35, 0.0)

    generators = {
        "fbm": fbm,
        "ridged_noise": ridged_noise,
        "turbulence": turbulence,
        "domain_warp": domain_warp,
    }

    sprite_lib = SpriteLibrary(project_root=root)
    palette = sprite_lib.export_palette() if sprite_lib.count() > 0 else None

    def generator(params, progress_callback=None):
        import numpy as np

        def emit(stage_image, step, total_steps):
            if progress_callback is not None:
                progress_callback(stage_image, step, total_steps)

        active = dict(tex_preset.params)
        active.update(params)
        active["octaves"] = max(1, int(round(active.get("octaves", 5))))
        active["scale"] = float(active.get("scale", tex_preset.params.get("scale", 8.0)))
        contrast = float(active.pop("contrast", 1.0))
        brightness = float(active.pop("brightness", 1.0))
        transparent_below = float(active.pop("transparent_below", 0.0))

        generator_fn = generators[tex_preset.generator]
        preview_params = dict(active)
        preview_params["octaves"] = max(1, min(active["octaves"], 2))
        preview = generator_fn(args.size, args.size, seed=args.seed, **preview_params)
        emit(render_noise_texture(preview, colormap="gray"), 1, 3)

        noise_arr = generator_fn(args.size, args.size, seed=args.seed, **active)
        noise_arr = np.clip((noise_arr - 0.5) * contrast + 0.5, 0.0, 1.0)
        noise_arr = np.clip(noise_arr * brightness, 0.0, 1.0)
        emit(render_noise_texture(noise_arr, colormap=tex_preset.colormap), 2, 3)

        final_img = render_noise_texture(
            noise_arr,
            colormap=tex_preset.colormap,
            palette=palette,
            transparent_below=transparent_below if transparent_below > 0 else None,
        )
        emit(final_img, 3, 3)
        return final_img

    mode = RunMode(args.mode)
    engine = SelfEvolutionEngine(project_root=root, mode=mode, verbose=True)
    result = engine.run(
        generator=generator,
        space=space,
        palette=palette,
        max_iterations=args.iterations,
        population_size=args.population,
        seed=args.seed,
    )

    if args.output:
        out_path = Path(args.output)
    else:
        out_dir = ws.get_output_path("textures", "").parent / "textures"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"evolution_{args.preset}_{args.size}px_seed{args.seed}.png"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if result.best_image is not None:
        result.best_image.save(out_path)
        print(f"Saved best image: {out_path}")
    else:
        print("Warning: best image was not available to save")

    meta_path = out_path.with_suffix(".json")
    meta_path.write_text(json.dumps({
        "target": args.target,
        "preset": args.preset,
        "mode": args.mode,
        "seed": args.seed,
        "iterations": result.iterations,
        "converged": result.converged,
        "best_score": result.best_score,
        "best_params": result.best_params,
        "history": result.history,
        "palette_source": "sprite_library" if palette else "none",
    }, indent=2), encoding="utf-8")
    print(f"Saved run metadata: {meta_path}")


def _cmd_texture(args):
    """Generate noise textures."""
    from ..sdf.noise import generate_texture, TEXTURE_PRESETS
    from ..workspace.manager import WorkspaceManager

    # List presets if none specified
    if args.preset is None and not args.gen_all:
        print("\nAvailable texture presets:")
        print(f"{'Preset':<12} {'Description':<50} {'Generator':<15}")
        print("-" * 77)
        for name, tp in TEXTURE_PRESETS.items():
            print(f"{name:<12} {tp.description:<50} {tp.generator:<15}")
        print("\nUsage:")
        print("  mathart-evolve texture terrain              # Generate one")
        print("  mathart-evolve texture lava --size 128      # Custom size")
        print("  mathart-evolve texture --all                # Generate all 6")
        return

    root = _find_project_root()
    ws = WorkspaceManager(project_root=root)

    presets_to_gen = list(TEXTURE_PRESETS.keys()) if args.gen_all else [args.preset]

    for preset_name in presets_to_gen:
        img = generate_texture(
            preset=preset_name,
            width=args.size,
            height=args.size,
            seed=args.seed,
            colormap=args.colormap,
        )

        # Determine output path
        if args.output and not args.gen_all:
            out_path = Path(args.output)
        else:
            out_dir = ws.get_output_path("textures", "").parent / "textures"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"texture_{preset_name}_{args.size}px_seed{args.seed}.png"

        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(out_path))
        print(f"  [{preset_name}] Saved: {out_path} ({img.width}x{img.height})")

    if args.gen_all:
        print(f"\nGenerated {len(presets_to_gen)} textures in output/textures/")


def _pick_single_file(title: str, file_type: str) -> str | None:
    """Try to open file picker for a single file, return path or None."""
    from ..workspace.manager import pick_files

    filetypes_map = {
        "sprite": [("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All", "*.*")],
        "sheet": [("Images", "*.png *.jpg *.jpeg"), ("All", "*.*")],
    }

    files = pick_files(
        title=title,
        filetypes=filetypes_map.get(file_type),
        multiple=False,
    )
    return str(files[0]) if files else None


def _find_project_root() -> Path:
    """Find the project root by looking for pyproject.toml."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


if __name__ == "__main__":
    main()
