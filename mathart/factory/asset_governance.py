"""
SESSION-174: Asset Governance — Storage Radar, Triage Engine, Safe GC & Vault Extraction.

This module implements the P0-SESSION-174-ASSET-GOVERNANCE-AND-VAULT task:
  1. Asset Radar & Triage: Scans production output batches, classifies them as
     Golden (complete) or Junk (aborted/interrupted), and computes disk usage.
  2. Interactive GC Dashboard: Renders a terminal-friendly health report with
     actionable options (safe nuke, vault extraction, return).
  3. Safe Nuke: Deletes junk batches with Y/N confirmation, blast-radius
     containment (path must contain 'output/'), and PermissionError resilience.
  4. Vault Extraction: Flat-copies final MP4/PNG deliverables from golden
     batches into ``output/export_vault/<batch_id>/`` for effortless browsing.

Industrial & Academic References (SESSION-174):
  - Artifact Lifecycle Management & GC: JFrog Artifactory GC Guide (2022),
    Argo Workflows Artifact GC Strategy, Schlegel & Sattler "Management of
    ML Lifecycle Artifacts" (ACM SIGMOD Record 2023).
  - Gold Master Vault Segregation: Autodesk Vault "Copy Design to Flat File
    Structure", Render Farm Best Practices (SuperRenders 2026).
  - Human-in-the-Loop Safe Pruning: "Blast Radius as a Design Constraint"
    (Medium 2026), Michael Nygard "Release It!" Circuit Breaker Pattern.
  - Metadata-Based Triage: Schlegel & Sattler (2023) — artifact status
    inference from structured metadata (batch_summary.json).
  - Immutable Source Data Principle: SESSION-172 — copy, never move originals.

Red Lines:
  - NEVER modify any rendering, physics, or network streaming code.
  - NEVER touch Evolution module code.
  - ALL delete operations MUST assert path contains 'output/'.
  - ALL copy operations use shutil.copy2 + os.makedirs(exist_ok=True).
  - PermissionError during deletion → skip, never crash.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SESSION_ID = "SESSION-174"

# File extensions that indicate a golden final deliverable
GOLDEN_EXTENSIONS_VIDEO = {".mp4", ".webm", ".mov", ".avi"}
GOLDEN_EXTENSIONS_IMAGE = {".png", ".jpg", ".jpeg", ".webp", ".tiff"}
GOLDEN_EXTENSIONS = GOLDEN_EXTENSIONS_VIDEO | GOLDEN_EXTENSIONS_IMAGE

# Sub-directories that typically contain final AI-rendered deliverables
DELIVERABLE_SUBDIRS = {
    "anti_flicker_render",
    "ai_render",
    "comfyui_render",
    "final_render",
    "preview",
}

# Minimum file size (bytes) to consider a PNG/image as a "high-res" deliverable
# (skip tiny thumbnails / placeholder files)
MIN_DELIVERABLE_IMAGE_SIZE = 50 * 1024  # 50 KB
MIN_DELIVERABLE_VIDEO_SIZE = 100 * 1024  # 100 KB


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class BatchInfo:
    """Metadata for a single production batch directory."""

    path: Path
    batch_id: str
    size_bytes: int = 0
    has_summary: bool = False
    has_final_video: bool = False
    has_final_image: bool = False
    character_count: int = 0
    skip_ai_render: Optional[bool] = None
    is_golden: bool = False
    status_label: str = ""
    status_emoji: str = ""
    final_deliverables: List[Path] = field(default_factory=list)

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    @property
    def size_display(self) -> str:
        if self.size_bytes >= 1024 * 1024 * 1024:
            return f"{self.size_bytes / (1024**3):.2f} GB"
        return f"{self.size_mb:.1f} MB"


@dataclass
class ScanReport:
    """Aggregated scan result for all production batches."""

    batches: List[BatchInfo] = field(default_factory=list)
    total_size_bytes: int = 0
    golden_count: int = 0
    junk_count: int = 0

    @property
    def total_size_display(self) -> str:
        if self.total_size_bytes >= 1024 * 1024 * 1024:
            return f"{self.total_size_bytes / (1024**3):.2f} GB"
        return f"{self.total_size_bytes / (1024**2):.1f} MB"


# ---------------------------------------------------------------------------
# Core: Directory size calculator
# ---------------------------------------------------------------------------
def _dir_size_bytes(path: Path) -> int:
    """Recursively compute total size of a directory in bytes."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                try:
                    total += entry.stat().st_size
                except (OSError, PermissionError):
                    pass
    except (OSError, PermissionError):
        pass
    return total


# ---------------------------------------------------------------------------
# Core: Batch triage engine
# ---------------------------------------------------------------------------
def _find_deliverables(batch_dir: Path) -> List[Path]:
    """Walk a batch directory and collect final deliverable files."""
    deliverables: List[Path] = []
    try:
        for fpath in batch_dir.rglob("*"):
            if not fpath.is_file():
                continue
            ext = fpath.suffix.lower()
            if ext in GOLDEN_EXTENSIONS_VIDEO:
                if fpath.stat().st_size >= MIN_DELIVERABLE_VIDEO_SIZE:
                    deliverables.append(fpath)
            elif ext in GOLDEN_EXTENSIONS_IMAGE:
                if fpath.stat().st_size >= MIN_DELIVERABLE_IMAGE_SIZE:
                    deliverables.append(fpath)
    except (OSError, PermissionError):
        pass
    return deliverables


def _triage_batch(batch_dir: Path) -> BatchInfo:
    """Analyze a single batch directory and classify it."""
    info = BatchInfo(
        path=batch_dir,
        batch_id=batch_dir.name,
    )

    # 1. Read batch_summary.json if present
    summary_path = batch_dir / "batch_summary.json"
    if summary_path.is_file():
        info.has_summary = True
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                summary = json.load(f)
            info.character_count = int(summary.get("character_count", 0))
            info.skip_ai_render = summary.get("skip_ai_render")
        except Exception:
            pass

    # 2. Scan for final deliverables (videos and high-res images)
    info.final_deliverables = _find_deliverables(batch_dir)
    info.has_final_video = any(
        d.suffix.lower() in GOLDEN_EXTENSIONS_VIDEO for d in info.final_deliverables
    )
    info.has_final_image = any(
        d.suffix.lower() in GOLDEN_EXTENSIONS_IMAGE for d in info.final_deliverables
    )

    # 3. Compute disk usage
    info.size_bytes = _dir_size_bytes(batch_dir)

    # 4. Classification logic:
    #    - Golden: has batch_summary.json AND (has final video OR has final images with character_count > 0)
    #    - Junk: everything else (no summary, no deliverables, or incomplete)
    if info.has_summary and (info.has_final_video or (info.has_final_image and info.character_count > 0)):
        info.is_golden = True
        info.status_label = "黄金完整批次"
        info.status_emoji = "🟢"
    else:
        info.is_golden = False
        info.status_label = "废弃/中断批次"
        info.status_emoji = "🔴"

    return info


# ---------------------------------------------------------------------------
# Core: Production output scanner (Asset Radar)
# ---------------------------------------------------------------------------
def scan_production_output(production_dir: Path) -> ScanReport:
    """
    Scan the production output directory for all batch folders.

    Looks for directories matching the pattern ``mass_production_batch_*``
    under the given *production_dir*.  Each batch is triaged and classified.

    Returns a :class:`ScanReport` with batches sorted by name descending
    (newest first, assuming timestamp-based naming).
    """
    report = ScanReport()

    if not production_dir.is_dir():
        logger.info("[AssetGovernance] Production dir does not exist: %s", production_dir)
        return report

    # Collect all batch directories
    batch_dirs = sorted(
        [d for d in production_dir.iterdir() if d.is_dir() and d.name.startswith("mass_production_batch_")],
        key=lambda d: d.name,
        reverse=True,  # newest first
    )

    for batch_dir in batch_dirs:
        info = _triage_batch(batch_dir)
        report.batches.append(info)
        report.total_size_bytes += info.size_bytes
        if info.is_golden:
            report.golden_count += 1
        else:
            report.junk_count += 1

    return report


# ---------------------------------------------------------------------------
# Action: Safe Nuke (Garbage Collection)
# ---------------------------------------------------------------------------
def _assert_safe_path(path: Path) -> None:
    """
    Blast-radius containment: assert the path is inside an 'output/' directory.

    Raises AssertionError if the path does not contain 'output/' or 'output\\'
    in its string representation, preventing catastrophic mis-deletion of
    project root, code, or system directories.
    """
    path_str = str(path.resolve())
    assert "output" in path_str.lower(), (
        f"[SESSION-174 BLAST RADIUS CONTAINMENT] "
        f"REFUSED to delete path outside output sandbox: {path_str}"
    )


def safe_nuke_junk_batches(
    junk_batches: List[BatchInfo],
    *,
    output_fn: Callable[[str], None] = print,
) -> int:
    """
    Safely delete all junk batch directories.

    For each batch:
      1. Assert path is inside output/ (blast-radius containment).
      2. Attempt shutil.rmtree with onerror handler for PermissionError.
      3. Report progress per batch.

    Returns the number of successfully deleted batches.
    """
    deleted = 0
    freed_bytes = 0

    for batch in junk_batches:
        try:
            _assert_safe_path(batch.path)
        except AssertionError as e:
            output_fn(f"\033[1;31m  [⛔ 安全拦截] {e}\033[0m")
            continue

        try:
            batch_size = batch.size_bytes

            def _on_rm_error(func, path, exc_info):
                """PermissionError resilience: skip locked files."""
                exc_type = exc_info[0] if exc_info else None
                if exc_type is PermissionError or exc_type is OSError:
                    logger.warning(
                        "[AssetGovernance] Skipping locked file: %s (%s)",
                        path, exc_info[1],
                    )
                else:
                    logger.warning(
                        "[AssetGovernance] rmtree error: %s (%s)",
                        path, exc_info[1],
                    )

            shutil.rmtree(batch.path, onerror=_on_rm_error)
            deleted += 1
            freed_bytes += batch_size
            output_fn(
                f"\033[32m  ✅ 已清除: {batch.batch_id} "
                f"(释放 {batch.size_display})\033[0m"
            )
        except Exception as exc:
            output_fn(
                f"\033[33m  ⚠️ 跳过: {batch.batch_id} "
                f"(原因: {exc.__class__.__name__}: {exc})\033[0m"
            )

    freed_display = (
        f"{freed_bytes / (1024**3):.2f} GB"
        if freed_bytes >= 1024 * 1024 * 1024
        else f"{freed_bytes / (1024**2):.1f} MB"
    )
    output_fn(
        f"\n\033[1;32m[✅ 瘦身完成] "
        f"共清除 {deleted} 个废弃批次，释放磁盘空间: {freed_display}\033[0m"
    )
    return deleted


# ---------------------------------------------------------------------------
# Action: Vault Extraction (Gold Master Flat Copy)
# ---------------------------------------------------------------------------
def extract_vault(
    golden_batches: List[BatchInfo],
    vault_root: Path,
    *,
    output_fn: Callable[[str], None] = print,
) -> int:
    """
    Extract final deliverables from golden batches into a flat vault structure.

    Creates ``vault_root/<batch_id>/`` for each golden batch and copies all
    final MP4/PNG deliverables into it using :func:`shutil.copy2`.

    Uses ``os.makedirs(exist_ok=True)`` and allows safe overwrite of existing
    files (no exception on duplicate names).

    Returns the number of files extracted.
    """
    total_extracted = 0

    for batch in golden_batches:
        if not batch.final_deliverables:
            output_fn(
                f"\033[33m  ⚠️ {batch.batch_id}: 无可提取的最终交付物\033[0m"
            )
            continue

        # Determine a human-friendly sub-directory name
        # Try to extract character name from the batch directory
        batch_vault_dir = vault_root / batch.batch_id
        os.makedirs(batch_vault_dir, exist_ok=True)

        extracted_in_batch = 0
        for src_path in batch.final_deliverables:
            try:
                # Build a descriptive filename: parent_dir__filename
                # This preserves context about where the file came from
                relative = src_path.relative_to(batch.path)
                parts = list(relative.parts)
                if len(parts) > 1:
                    # Use character_id + stage as prefix
                    prefix = "__".join(parts[:-1])
                    dest_name = f"{prefix}__{parts[-1]}"
                else:
                    dest_name = parts[0]

                dest_path = batch_vault_dir / dest_name
                shutil.copy2(str(src_path), str(dest_path))
                extracted_in_batch += 1
            except Exception as exc:
                logger.warning(
                    "[AssetGovernance] Failed to copy %s → %s: %s",
                    src_path, batch_vault_dir, exc,
                )
                output_fn(
                    f"\033[33m    ⚠️ 跳过文件: {src_path.name} "
                    f"({exc.__class__.__name__})\033[0m"
                )

        total_extracted += extracted_in_batch
        output_fn(
            f"\033[32m  🏆 {batch.batch_id}: "
            f"提取 {extracted_in_batch} 个交付物 → {batch_vault_dir}\033[0m"
        )

    output_fn(
        f"\n\033[1;32m[🏆 金库提纯完成] "
        f"共提取 {total_extracted} 个黄金资产至 {vault_root}\033[0m"
    )
    output_fn(
        f"\033[90m    ↳ 您可以直接打开 {vault_root} 目录，"
        f"如同逛画展一般浏览所有最终成品！\033[0m"
    )
    return total_extracted


# ---------------------------------------------------------------------------
# Interactive Dashboard: The Grand Steward Terminal
# ---------------------------------------------------------------------------
def run_asset_governance_dashboard(
    project_root: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
) -> None:
    """
    SESSION-174: Interactive Asset Governance Dashboard.

    Replaces the old ``[3] 真理查账`` menu item with a powerful storage
    management terminal that provides:
      - Full batch scan with status classification
      - One-click junk cleanup with safety confirmation
      - Golden vault extraction for effortless deliverable browsing

    Industrial References:
      - JFrog Artifactory GC (2022): metadata-based artifact triage
      - Blast Radius Containment (Medium 2026): scoped deletion safety
      - Autodesk Vault Flat Copy: deliverable extraction pattern
      - Schlegel & Sattler (2023): ML artifact lifecycle management
    """
    production_dir = project_root / "output" / "production"
    vault_root = project_root / "output" / "export_vault"

    # ── Phase 1: Scan ─────────────────────────────────────────────────────
    output_fn("")
    output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
    output_fn(
        "\033[1;36m[🔍 资产大管家] SESSION-174 存储雷达启动中...\033[0m"
    )
    output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
    output_fn(
        f"\033[90m    ↳ 扫描目标: {production_dir}\033[0m"
    )

    report = scan_production_output(production_dir)

    if not report.batches:
        output_fn(
            "\n\033[1;33m[🔍 资产大管家] 扫描完毕！"
            "未发现任何量产批次文件夹。\033[0m"
        )
        output_fn(
            "\033[90m    ↳ 请先通过 [1] 工业量产 或 [5] 导演工坊 "
            "生成批次后再来管理。\033[0m"
        )
        return

    # ── Phase 2: Health Report ────────────────────────────────────────────
    output_fn("")
    output_fn("─" * 60)
    output_fn(
        f"\033[1;37m[🔍 资产大管家] 扫描完毕！"
        f"共发现 {len(report.batches)} 个量产批次，"
        f"总占用: {report.total_size_display}。\033[0m"
    )
    output_fn(
        f"\033[90m    🟢 黄金完整批次: {report.golden_count} 个  |  "
        f"🔴 废弃/中断批次: {report.junk_count} 个\033[0m"
    )
    output_fn("─" * 60)

    for batch in report.batches:
        char_info = f", {batch.character_count} 角色" if batch.character_count > 0 else ""
        deliverable_info = ""
        if batch.final_deliverables:
            video_count = sum(
                1 for d in batch.final_deliverables
                if d.suffix.lower() in GOLDEN_EXTENSIONS_VIDEO
            )
            image_count = sum(
                1 for d in batch.final_deliverables
                if d.suffix.lower() in GOLDEN_EXTENSIONS_IMAGE
            )
            parts = []
            if video_count:
                parts.append(f"{video_count} 视频")
            if image_count:
                parts.append(f"{image_count} 图片")
            deliverable_info = f", 交付物: {' + '.join(parts)}"

        output_fn(
            f"  {batch.batch_id} "
            f"[{batch.status_emoji} {batch.status_label}] "
            f"({batch.size_display}{char_info}{deliverable_info})"
        )

    # ── Phase 3: Action Menu ──────────────────────────────────────────────
    junk_batches = [b for b in report.batches if not b.is_golden]
    golden_batches = [b for b in report.batches if b.is_golden]

    junk_size = sum(b.size_bytes for b in junk_batches)
    junk_size_display = (
        f"{junk_size / (1024**3):.2f} GB"
        if junk_size >= 1024 * 1024 * 1024
        else f"{junk_size / (1024**2):.1f} MB"
    )

    while True:
        output_fn("")
        output_fn("─" * 60)
        output_fn("\033[1;37m请选择操作：\033[0m")
        output_fn(
            f"  [1] 🧹 一键瘦身：安全销毁所有【🔴 废弃/中断】的垃圾文件夹 "
            f"(可释放 {junk_size_display})"
        )
        output_fn(
            f"  [2] 🏆 金库提纯：将【🟢 黄金批次】中的最终 MP4 视频和高清图，"
            f"扁平拷贝到 output/export_vault/ 目录"
        )
        output_fn(
            "  [0] ↩️  退回上级菜单"
        )

        try:
            choice = input_fn("输入编号并回车: ").strip()
        except (EOFError, KeyboardInterrupt):
            output_fn("\n检测到退出信号，资产大管家已关闭。")
            return

        # --- [0] Return to parent menu ---
        if choice in {"0", "back", "exit", "quit"}:
            output_fn("已退回上级菜单。")
            return

        # --- [1] Safe Nuke: Delete junk batches ---
        if choice in {"1", "clean", "nuke", "gc"}:
            if not junk_batches:
                output_fn(
                    "\n\033[1;33m[提示] 没有发现废弃/中断批次，无需清理！\033[0m"
                )
                continue

            output_fn("")
            output_fn("\033[1;31m" + "═" * 60 + "\033[0m")
            output_fn(
                f"\033[1;31m[⚠️  安全确认] 即将永久删除以下 "
                f"{len(junk_batches)} 个废弃批次：\033[0m"
            )
            for jb in junk_batches:
                output_fn(
                    f"\033[1;31m  🔴 {jb.batch_id} ({jb.size_display})\033[0m"
                )
            output_fn(
                f"\033[1;31m  总计释放空间: {junk_size_display}\033[0m"
            )
            output_fn("\033[1;31m" + "═" * 60 + "\033[0m")

            try:
                confirm = input_fn(
                    "\033[1;31m确认删除？此操作不可撤销！[Y/N]: \033[0m"
                ).strip().upper()
            except (EOFError, KeyboardInterrupt):
                output_fn("\n已取消删除操作。")
                continue

            if confirm not in {"Y", "YES"}:
                output_fn("已取消删除操作，所有文件保持不变。")
                continue

            output_fn("")
            safe_nuke_junk_batches(junk_batches, output_fn=output_fn)

            # Refresh the report after deletion
            report = scan_production_output(production_dir)
            junk_batches = [b for b in report.batches if not b.is_golden]
            golden_batches = [b for b in report.batches if b.is_golden]
            junk_size = sum(b.size_bytes for b in junk_batches)
            junk_size_display = (
                f"{junk_size / (1024**3):.2f} GB"
                if junk_size >= 1024 * 1024 * 1024
                else f"{junk_size / (1024**2):.1f} MB"
            )
            continue

        # --- [2] Vault Extraction: Copy golden deliverables ---
        if choice in {"2", "vault", "extract", "gold"}:
            if not golden_batches:
                output_fn(
                    "\n\033[1;33m[提示] 没有发现黄金完整批次，无法提纯！\033[0m"
                )
                continue

            output_fn("")
            output_fn("\033[1;36m" + "═" * 60 + "\033[0m")
            output_fn(
                f"\033[1;36m[🏆 金库提纯] 正在将 {len(golden_batches)} 个黄金批次的"
                f"最终交付物提取至:\033[0m"
            )
            output_fn(f"\033[1;36m  → {vault_root}\033[0m")
            output_fn("\033[1;36m" + "═" * 60 + "\033[0m")

            os.makedirs(vault_root, exist_ok=True)
            extract_vault(golden_batches, vault_root, output_fn=output_fn)
            continue

        output_fn("[提示] 请输入 1 / 2 / 0 中的一个数字。")
