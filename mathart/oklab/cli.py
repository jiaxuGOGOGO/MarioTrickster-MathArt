"""CLI for OKLAB palette generation and image quantization."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from .palette import PaletteGenerator
from .quantizer import quantize_image


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="mathart-palette",
        description="Generate OKLAB palettes and quantize images.",
    )
    sub = parser.add_subparsers(dest="command")

    # ── generate ──
    gen = sub.add_parser("generate", help="Generate a palette")
    gen.add_argument("--harmony", default="warm_cool_shadow",
                     choices=["complementary", "analogous", "triadic",
                              "split_complementary", "warm_cool_shadow", "tonal_ramp"])
    gen.add_argument("--count", type=int, default=8)
    gen.add_argument("--hue", type=float, default=None, help="Base hue in degrees [0, 360]")
    gen.add_argument("--lightness", type=float, default=0.65)
    gen.add_argument("--chroma", type=float, default=0.14)
    gen.add_argument("--name", default="palette")
    gen.add_argument("--seed", type=int, default=None)
    gen.add_argument("-o", "--output", default="palette.json")

    # ── theme ──
    theme = sub.add_parser("theme", help="Generate a full theme palette set")
    theme.add_argument("--name", default="grassland")
    theme.add_argument("--hue", type=float, default=None, help="Base hue in degrees")
    theme.add_argument("--seed", type=int, default=None)
    theme.add_argument("-o", "--output-dir", default=".")

    # ── quantize ──
    quant = sub.add_parser("quantize", help="Quantize an image to a palette")
    quant.add_argument("image", help="Input image path")
    quant.add_argument("palette", help="Palette JSON path")
    quant.add_argument("-o", "--output", default="quantized.png")
    quant.add_argument("--dither", action="store_true")

    # ── preview ──
    prev = sub.add_parser("preview", help="Generate a palette preview image")
    prev.add_argument("palette", help="Palette JSON path")
    prev.add_argument("-o", "--output", default="preview.png")
    prev.add_argument("--swatch-size", type=int, default=64)

    args = parser.parse_args(argv)

    if args.command == "generate":
        hue_rad = np.deg2rad(args.hue) if args.hue is not None else None
        gen = PaletteGenerator(seed=args.seed)
        pal = gen.generate(
            harmony=args.harmony, base_hue=hue_rad,
            lightness=args.lightness, chroma=args.chroma,
            count=args.count, name=args.name,
        )
        pal.save_json(args.output)
        print(f"Palette saved: {args.output} ({pal.count} colors)")
        for hex_c, role in zip(pal.colors_hex, pal.roles):
            print(f"  {hex_c}  {role}")

    elif args.command == "theme":
        hue_rad = np.deg2rad(args.hue) if args.hue is not None else None
        gen = PaletteGenerator(seed=args.seed)
        palettes = gen.generate_theme_palette(theme_name=args.name, base_hue=hue_rad)
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        for key, pal in palettes.items():
            path = out_dir / f"{args.name}_{key}.json"
            pal.save_json(str(path))
            print(f"  {key}: {path} ({pal.count} colors)")

    elif args.command == "quantize":
        from .palette import Palette
        img = Image.open(args.image)
        pal = Palette.load_json(args.palette)
        result = quantize_image(img, pal, dither=args.dither)
        result.save(args.output)
        print(f"Quantized image saved: {args.output}")

    elif args.command == "preview":
        from .palette import Palette
        pal = Palette.load_json(args.palette)
        _render_preview(pal, args.output, args.swatch_size)
        print(f"Preview saved: {args.output}")

    else:
        parser.print_help()


def _render_preview(pal, output: str, swatch_size: int = 64) -> None:
    """Render a palette preview image with color swatches and hex labels."""
    from PIL import ImageDraw
    n = pal.count
    cols = min(n, 8)
    rows = (n + cols - 1) // cols
    w = cols * swatch_size
    h = rows * (swatch_size + 20) + 30
    img = Image.new("RGB", (w, h), (30, 30, 30))
    draw = ImageDraw.Draw(img)

    # Title
    draw.text((10, 5), f"{pal.name} ({n} colors)", fill=(200, 200, 200))

    srgb = pal.colors_srgb
    for i in range(n):
        col = i % cols
        row = i // cols
        x0 = col * swatch_size
        y0 = row * (swatch_size + 20) + 30
        color = tuple(int(c) for c in srgb[i])
        draw.rectangle([x0 + 2, y0, x0 + swatch_size - 2, y0 + swatch_size - 2], fill=color)
        # Hex label
        hex_str = pal.colors_hex[i]
        lum = 0.299 * srgb[i][0] + 0.587 * srgb[i][1] + 0.114 * srgb[i][2]
        text_color = (0, 0, 0) if lum > 128 else (255, 255, 255)
        draw.text((x0 + 4, y0 + swatch_size - 14), hex_str, fill=text_color)
        if i < len(pal.roles):
            draw.text((x0 + 4, y0 + 2), pal.roles[i][:8], fill=text_color)

    img.save(output)


if __name__ == "__main__":
    main()
