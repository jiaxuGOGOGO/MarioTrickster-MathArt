"""Workspace Manager — structured directory management for MarioTrickster-MathArt.

Provides:
  1. ``inbox/`` hot folder: drop files here, system auto-processes them
  2. ``output/`` classified output: generated assets sorted by type
  3. File picker dialog: GUI file selection when available
  4. Batch scan & process: auto-detect file types and route to correct handler

Directory structure::

    project_root/
    ├── inbox/                  ← Drop files here
    │   ├── sprites/            ← Sprite reference images
    │   ├── sheets/             ← Spritesheets to auto-cut
    │   ├── knowledge/          ← PDFs, Markdown for distillation
    │   └── processed/          ← Auto-moved after processing
    ├── output/                 ← Generated results
    │   ├── textures/           ← Noise textures
    │   ├── effects/            ← SDF effects
    │   ├── palettes/           ← Color palettes
    │   ├── characters/         ← Character sprites
    │   ├── levels/             ← Level layouts
    │   └── exports/            ← Unity-ready exports
    └── ...
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional


# Supported image extensions
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tga"}
# Supported knowledge extensions
KNOWLEDGE_EXTENSIONS = {".pdf", ".md", ".txt", ".markdown"}


class WorkspaceManager:
    """Manage inbox/output directory structure and file routing.

    Parameters
    ----------
    project_root : Path or str
        Root directory of the project.
    """

    # Inbox subdirectories
    INBOX_DIRS = ["sprites", "sheets", "knowledge"]
    # Output subdirectories
    OUTPUT_DIRS = ["textures", "effects", "palettes", "characters", "levels", "exports"]

    def __init__(self, project_root: Optional[Path] = None) -> None:
        self.root = Path(project_root) if project_root else Path.cwd()
        self.inbox = self.root / "inbox"
        self.output = self.root / "output"
        self.processed = self.inbox / "processed"

    def init_workspace(self) -> dict[str, list[str]]:
        """Create the full workspace directory structure.

        Returns a dict of created directories.
        """
        created = {"inbox": [], "output": []}

        # Inbox directories
        for sub in self.INBOX_DIRS:
            d = self.inbox / sub
            d.mkdir(parents=True, exist_ok=True)
            created["inbox"].append(str(d.relative_to(self.root)))

        # Processed directory
        self.processed.mkdir(parents=True, exist_ok=True)
        created["inbox"].append(str(self.processed.relative_to(self.root)))

        # Output directories
        for sub in self.OUTPUT_DIRS:
            d = self.output / sub
            d.mkdir(parents=True, exist_ok=True)
            created["output"].append(str(d.relative_to(self.root)))

        # Write a README in inbox
        readme = self.inbox / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Inbox\n\n"
                "将文件丢入对应子文件夹，然后运行 `mathart-evolve scan` 自动处理：\n\n"
                "| 文件夹 | 用途 | 支持格式 |\n"
                "|--------|------|----------|\n"
                "| `sprites/` | Sprite 参考图 | PNG, JPG, BMP, GIF, WebP |\n"
                "| `sheets/` | Spritesheet（自动切帧） | PNG, JPG |\n"
                "| `knowledge/` | 知识蒸馏素材 | PDF, Markdown, TXT |\n\n"
                "处理完成后文件会自动移入 `processed/` 目录。\n",
                encoding="utf-8",
            )

        return created

    def scan_inbox(self) -> dict[str, list[Path]]:
        """Scan inbox directories and categorize files.

        Returns
        -------
        dict mapping category to list of file paths.
        """
        found = {"sprites": [], "sheets": [], "knowledge": [], "unknown": []}

        for category in self.INBOX_DIRS:
            cat_dir = self.inbox / category
            if not cat_dir.exists():
                continue
            for f in sorted(cat_dir.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    found[category].append(f)

        # Also scan inbox root for unsorted files
        if self.inbox.exists():
            for f in sorted(self.inbox.iterdir()):
                if f.is_file() and not f.name.startswith(".") and f.name != "README.md":
                    ext = f.suffix.lower()
                    if ext in IMAGE_EXTENSIONS:
                        # Auto-detect: large images → sheets, small → sprites
                        from PIL import Image
                        try:
                            img = Image.open(f)
                            w, h = img.size
                            if w > 128 and h > 128 and (w / h > 2 or h / w > 2):
                                found["sheets"].append(f)
                            else:
                                found["sprites"].append(f)
                        except Exception:
                            found["unknown"].append(f)
                    elif ext in KNOWLEDGE_EXTENSIONS:
                        found["knowledge"].append(f)
                    else:
                        found["unknown"].append(f)

        return found

    def process_inbox(self, verbose: bool = True) -> dict[str, int]:
        """Process all files in inbox and move to processed.

        Returns
        -------
        dict with counts of processed files per category.
        """
        found = self.scan_inbox()
        counts = {"sprites": 0, "sheets": 0, "knowledge": 0, "skipped": 0}

        # Process sprites
        for f in found["sprites"]:
            try:
                self._process_sprite(f, verbose=verbose)
                self._move_to_processed(f)
                counts["sprites"] += 1
            except Exception as e:
                if verbose:
                    print(f"  [ERROR] {f.name}: {e}")
                counts["skipped"] += 1

        # Process sheets
        for f in found["sheets"]:
            try:
                self._process_sheet(f, verbose=verbose)
                self._move_to_processed(f)
                counts["sheets"] += 1
            except Exception as e:
                if verbose:
                    print(f"  [ERROR] {f.name}: {e}")
                counts["skipped"] += 1

        # Process knowledge files
        for f in found["knowledge"]:
            try:
                self._process_knowledge(f, verbose=verbose)
                self._move_to_processed(f)
                counts["knowledge"] += 1
            except Exception as e:
                if verbose:
                    print(f"  [ERROR] {f.name}: {e}")
                counts["skipped"] += 1

        return counts

    def _process_sprite(self, filepath: Path, verbose: bool = True) -> None:
        """Process a single sprite file."""
        from PIL import Image
        from ..sprite.library import SpriteLibrary

        lib = SpriteLibrary(project_root=self.root)
        img = Image.open(filepath)
        fp, is_new = lib.add_sprite(
            image=img,
            source_name=filepath.stem,
            source_path=str(filepath.resolve()),
            sprite_type="unknown",
        )
        status = "NEW" if is_new else "DUP"
        if verbose:
            print(f"  [{status}] Sprite: {filepath.name} — quality={fp.quality_score:.3f}")

    def _process_sheet(self, filepath: Path, verbose: bool = True) -> None:
        """Process a spritesheet file."""
        from PIL import Image
        from ..sprite.library import SpriteLibrary
        from ..sprite.sheet_parser import SpriteSheetParser

        lib = SpriteLibrary(project_root=self.root)
        parser = SpriteSheetParser()
        img = Image.open(filepath)
        result = parser.parse_auto(img)

        frames = [f.image for f in result.frames if f.has_content]
        if not frames:
            raise ValueError("No frames with content found")

        fp, is_new = lib.add_frames(
            frames=frames,
            source_name=filepath.stem,
            source_path=str(filepath.resolve()),
            sprite_type="character",
        )
        status = "NEW" if is_new else "DUP"
        if verbose:
            print(f"  [{status}] Sheet: {filepath.name} — {len(frames)} frames, quality={fp.quality_score:.3f}")

    def _process_knowledge(self, filepath: Path, verbose: bool = True) -> None:
        """Process a knowledge file for distillation."""
        from ..evolution.outer_loop import OuterLoopDistiller

        distiller = OuterLoopDistiller(project_root=self.root, verbose=False)
        result = distiller.distill_file(str(filepath))
        if verbose:
            print(f"  [DISTILL] {filepath.name} — {result.rules_extracted} rules extracted")

    def _move_to_processed(self, filepath: Path) -> None:
        """Move a processed file to the processed directory."""
        self.processed.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = self.processed / f"{ts}_{filepath.name}"
        shutil.move(str(filepath), str(dest))

    def get_output_path(self, category: str, filename: str) -> Path:
        """Get the output path for a generated file, creating dirs as needed.

        Parameters
        ----------
        category : str
            Output category (textures, effects, palettes, characters, levels, exports).
        filename : str
            Output filename.

        Returns
        -------
        Path to the output file.
        """
        out_dir = self.output / category
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir / filename

    def summary(self) -> str:
        """Return a summary of workspace status."""
        lines = []

        # Inbox status
        if self.inbox.exists():
            found = self.scan_inbox()
            total = sum(len(v) for v in found.values())
            lines.append(f"Inbox: {total} file(s) pending")
            for cat, files in found.items():
                if files:
                    lines.append(f"  {cat}/: {len(files)} file(s)")
        else:
            lines.append("Inbox: not initialized (run 'mathart-evolve init-workspace')")

        # Output status
        if self.output.exists():
            total_output = 0
            for sub in self.OUTPUT_DIRS:
                d = self.output / sub
                if d.exists():
                    count = len([f for f in d.iterdir() if f.is_file()])
                    if count > 0:
                        lines.append(f"  output/{sub}/: {count} file(s)")
                    total_output += count
            if total_output == 0:
                lines.append("Output: empty")
            else:
                lines.append(f"Output: {total_output} file(s) total")
        else:
            lines.append("Output: not initialized")

        # Processed count
        if self.processed.exists():
            processed_count = len([f for f in self.processed.iterdir() if f.is_file()])
            if processed_count > 0:
                lines.append(f"Processed: {processed_count} file(s) in history")

        return "\n".join(lines)


# ── File Picker ───────────────────────────────────────────────────────────────

def pick_files(
    title: str = "Select files",
    filetypes: Optional[list[tuple[str, str]]] = None,
    multiple: bool = True,
    initial_dir: Optional[str] = None,
) -> list[Path]:
    """Open a native file picker dialog.

    Falls back gracefully if no GUI is available (e.g., SSH session).

    Parameters
    ----------
    title : str
        Dialog title.
    filetypes : list of (description, pattern)
        File type filters, e.g., [("Images", "*.png *.jpg"), ("All", "*.*")].
    multiple : bool
        Allow selecting multiple files.
    initial_dir : str, optional
        Starting directory.

    Returns
    -------
    list of Path objects (empty if cancelled or no GUI).
    """
    if filetypes is None:
        filetypes = [
            ("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
            ("Documents", "*.pdf *.md *.txt"),
            ("All files", "*.*"),
        ]

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()  # Hide main window
        root.attributes("-topmost", True)  # Bring dialog to front

        if multiple:
            paths = filedialog.askopenfilenames(
                title=title,
                filetypes=filetypes,
                initialdir=initial_dir,
            )
        else:
            path = filedialog.askopenfilename(
                title=title,
                filetypes=filetypes,
                initialdir=initial_dir,
            )
            paths = (path,) if path else ()

        root.destroy()
        return [Path(p) for p in paths if p]

    except (ImportError, tk.TclError if "tk" in dir() else Exception):
        # No GUI available
        return []
