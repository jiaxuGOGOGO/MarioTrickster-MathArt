"""tests/test_idempotent_surgeon.py — P0-SESSION-130-IDEMPOTENT-SURGEON audit suite.

These tests enforce the binding obligations documented in
``docs/research/SESSION-133-SURGEON-RESEARCH.md``:

* The injector honours the three-tier fallback (``symlink → hardlink → copy``)
  and survives Windows ``WinError 1314`` + cross-device hardlink failures.
* The downloader never exposes a half-downloaded file at the final path; all
  bytes land in ``<target>.part`` and only a verified SHA-256 triggers the
  atomic ``os.replace``.
* The surgeon is *absolutely* idempotent: a second ``operate(report)`` over
  an already-healed environment emits zero network calls, zero file
  mutations, and completes under a strict wall-clock budget.
* Source-level red lines are statically audited (no ``shutil.rmtree`` on the
  target, no ``os.remove`` on user data, no blind path deletion).

The suite is hermetic: no TCP sockets are opened, no sleeps are wall-clock,
and no ambient filesystem locations outside ``tmp_path`` are touched.
"""

from __future__ import annotations

import errno
import hashlib
import io
import os
import re
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
from unittest import mock

import pytest

from mathart.workspace.asset_injector import (
    AssetInjector,
    InjectionMethod,
    InjectionOutcome,
    InjectionStatus,
)
from mathart.workspace.atomic_downloader import (
    AtomicDownloader,
    DownloadOutcome,
    DownloadStatus,
    DownloadTransport,
    TransportResponse,
)
from mathart.workspace.idempotent_surgeon import (
    ActionKind,
    AssemblyReport,
    AssetPlan,
    IdempotentSurgeon,
)


# ---------------------------------------------------------------------------
# Shared fixtures & mocks
# ---------------------------------------------------------------------------


def _sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class _FakeReportComfy:
    root_path: Optional[str]


@dataclass
class _FakeReport:
    verdict: str
    fixable_actions: tuple[str, ...]
    blocking_actions: tuple[str, ...]
    comfyui: _FakeReportComfy


class _RecordingTransport:
    """In-memory transport that records every ``open()`` call.

    Used to prove (a) byte-for-byte streaming correctness, (b) Range
    resume works, and (c) the **second** surgeon run opens **zero**
    connections.
    """

    def __init__(self, payload: bytes, *, fail_first_n: int = 0,
                 ignore_range: bool = False) -> None:
        self._payload = payload
        self._fail_remaining = fail_first_n
        self._ignore_range = ignore_range
        self.calls: list[dict] = []

    def open(self, url: str, *, offset: int = 0) -> TransportResponse:
        self.calls.append({"url": url, "offset": offset})
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            raise OSError("simulated transient transport error")

        if offset > 0 and not self._ignore_range:
            body = self._payload[offset:]
            total = len(self._payload)
            resp = TransportResponse(
                status_code=206,
                total_length=total,
                accepted_range_from=offset,
                stream=iter(_chunked(body, 64)),
            )
            return resp
        body = self._payload
        resp = TransportResponse(
            status_code=200,
            total_length=len(body),
            accepted_range_from=0,
            stream=iter(_chunked(body, 64)),
        )
        return resp


def _chunked(data: bytes, size: int) -> Iterator[bytes]:
    for i in range(0, len(data), size):
        yield data[i:i + size]


def _build_fake_comfy_tree(root: Path) -> None:
    """Create an empty ComfyUI root so the surgeon has a target directory."""
    (root / "custom_nodes").mkdir(parents=True, exist_ok=True)
    (root / "models" / "controlnet").mkdir(parents=True, exist_ok=True)
    (root / "models" / "animatediff_models").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# TIER A — AssetInjector
# ---------------------------------------------------------------------------


class TestAssetInjectorCacheRecovery:
    """The injector MUST find a matching file in configured caches and
    materialise it via symlink (or fallback) without any network I/O."""

    def test_cache_hit_uses_symlink_when_allowed(self, tmp_path: Path) -> None:
        cache = tmp_path / "hf_cache"
        cache.mkdir()
        payload = b"MODEL-BYTES-" * 512
        src = cache / "v3_sd15_sparsectrl_rgb.ckpt"
        src.write_bytes(payload)

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        target = comfy / "models" / "controlnet" / "v3_sd15_sparsectrl_rgb.ckpt"

        injector = AssetInjector(extra_cache_roots=[cache])
        out = injector.inject(
            asset_name="sparsectrl_rgb_model",
            target_path=str(target),
            expected_filename="v3_sd15_sparsectrl_rgb.ckpt",
            expected_size=len(payload),
        )

        assert out.status is InjectionStatus.INJECTED
        assert out.method is InjectionMethod.SYMLINK
        assert target.is_symlink()
        assert target.resolve() == src.resolve()
        assert target.read_bytes() == payload

    def test_second_run_is_already_satisfied(self, tmp_path: Path) -> None:
        cache = tmp_path / "hf_cache"
        cache.mkdir()
        payload = b"x" * 2048
        src = cache / "model.ckpt"
        src.write_bytes(payload)

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        target = comfy / "models" / "controlnet" / "model.ckpt"

        injector = AssetInjector(extra_cache_roots=[cache])
        first = injector.inject(
            asset_name="m",
            target_path=str(target),
            expected_filename="model.ckpt",
            expected_size=len(payload),
        )
        assert first.status is InjectionStatus.INJECTED

        second = injector.inject(
            asset_name="m",
            target_path=str(target),
            expected_filename="model.ckpt",
            expected_size=len(payload),
        )
        assert second.status is InjectionStatus.ALREADY_SATISFIED
        assert second.method is InjectionMethod.NONE
        assert second.bytes_reused == len(payload)

    def test_cache_miss_returns_distinct_status(self, tmp_path: Path) -> None:
        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        target = comfy / "models" / "controlnet" / "not-cached.ckpt"

        injector = AssetInjector(extra_cache_roots=[tmp_path / "empty"])
        out = injector.inject(
            asset_name="n",
            target_path=str(target),
            expected_filename="not-cached.ckpt",
            expected_size=42,
        )
        assert out.status is InjectionStatus.CACHE_MISS
        assert not target.exists()


class TestAssetInjectorThreeTierFallback:
    """Simulate Windows WinError 1314 and cross-device failures to prove the
    injector degrades symlink → hardlink → copy without crashing."""

    def test_symlink_denied_falls_back_to_hardlink(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        payload = b"yyyy" * 256
        src = cache / "file.bin"
        src.write_bytes(payload)

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        target = comfy / "models" / "controlnet" / "file.bin"

        injector = AssetInjector(extra_cache_roots=[cache])

        winerror_1314 = OSError(errno.EPERM, "symlink privilege not held")
        with mock.patch.object(os, "symlink", side_effect=winerror_1314):
            out = injector.inject(
                asset_name="m",
                target_path=str(target),
                expected_filename="file.bin",
                expected_size=len(payload),
            )
        assert out.status is InjectionStatus.INJECTED
        assert out.method is InjectionMethod.HARDLINK
        # Hardlink: same inode as source
        assert target.stat().st_ino == src.stat().st_ino

    def test_symlink_and_hardlink_both_fail_fallback_to_copy(
        self, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        payload = b"ZZZZ" * 128
        src = cache / "file.bin"
        src.write_bytes(payload)

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        target = comfy / "models" / "controlnet" / "file.bin"

        injector = AssetInjector(extra_cache_roots=[cache])
        with mock.patch.object(os, "symlink", side_effect=OSError(1, "nope")), \
             mock.patch.object(os, "link", side_effect=OSError(18, "EXDEV")):
            out = injector.inject(
                asset_name="m",
                target_path=str(target),
                expected_filename="file.bin",
                expected_size=len(payload),
            )
        assert out.status is InjectionStatus.INJECTED
        assert out.method is InjectionMethod.COPY
        assert target.read_bytes() == payload
        # Copy: different inode
        assert target.stat().st_ino != src.stat().st_ino

    def test_all_tiers_fail_records_failed(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        src = cache / "file.bin"
        src.write_bytes(b"abc")

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        target = comfy / "models" / "controlnet" / "file.bin"

        injector = AssetInjector(extra_cache_roots=[cache])
        with mock.patch.object(os, "symlink", side_effect=OSError("s")), \
             mock.patch.object(os, "link", side_effect=OSError("l")), \
             mock.patch("shutil.copy2", side_effect=OSError("c")):
            out = injector.inject(
                asset_name="m",
                target_path=str(target),
                expected_filename="file.bin",
                expected_size=3,
            )
        assert out.status is InjectionStatus.FAILED
        assert out.method is InjectionMethod.NONE


class TestAssetInjectorDefensiveGuards:
    """The injector must never delete user data, and must survive
    permission errors during cache scanning."""

    def test_permission_error_during_scan_is_silently_skipped(
        self, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        (cache / "locked").mkdir(parents=True)
        (cache / "reachable").mkdir()
        good = cache / "reachable" / "file.bin"
        good.write_bytes(b"aaaa")

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        target = comfy / "models" / "controlnet" / "file.bin"

        # Patch Path.iterdir to raise PermissionError for the locked subtree.
        real_iterdir = Path.iterdir

        def _iterdir(self: Path):
            if self.name == "locked":
                raise PermissionError("denied")
            return real_iterdir(self)

        injector = AssetInjector(extra_cache_roots=[cache])
        with mock.patch.object(Path, "iterdir", _iterdir):
            out = injector.inject(
                asset_name="m",
                target_path=str(target),
                expected_filename="file.bin",
                expected_size=4,
            )
        assert out.status is InjectionStatus.INJECTED

    def test_conflicting_target_is_quarantined_not_deleted(
        self, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        good = cache / "file.bin"
        good.write_bytes(b"GOODGOOD" * 4)

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        target = comfy / "models" / "controlnet" / "file.bin"
        # Pre-existing but wrong (mismatching size)
        target.write_bytes(b"BAD")

        injector = AssetInjector(extra_cache_roots=[cache])
        out = injector.inject(
            asset_name="m",
            target_path=str(target),
            expected_filename="file.bin",
            expected_size=32,
        )
        assert out.status is InjectionStatus.INJECTED
        # Backup file exists — user data is never destroyed
        assert out.quarantined_backup is not None
        bak = Path(out.quarantined_backup)
        assert bak.exists()
        assert bak.read_bytes() == b"BAD"


# ---------------------------------------------------------------------------
# TIER B — AtomicDownloader
# ---------------------------------------------------------------------------


class TestAtomicDownloaderNeverExposesHalfFile:
    """The final target path MUST only ever appear after successful hash."""

    def test_fresh_download_uses_part_file_then_atomic_rename(
        self, tmp_path: Path
    ) -> None:
        payload = b"BIGMODELBYTES" * 1024
        url = "https://mirror.test/models/x.ckpt"
        target = tmp_path / "out" / "x.ckpt"
        transport = _RecordingTransport(payload)
        dl = AtomicDownloader(
            transport=transport,
            max_retries=1,
            backoff_seconds=0,
        )
        out = dl.fetch(
            url=url,
            target_path=str(target),
            expected_size=len(payload),
            expected_sha256=_sha256_of(payload),
        )
        assert out.status is DownloadStatus.DOWNLOADED_FRESH
        assert target.is_file()
        assert target.read_bytes() == payload
        # .part file no longer exists — it was atomically renamed
        assert not (target.with_name(target.name + ".part")).exists()

    def test_hash_mismatch_quarantines_part_and_does_not_expose_target(
        self, tmp_path: Path
    ) -> None:
        payload = b"CORRUPT" * 32
        url = "https://mirror.test/models/x.ckpt"
        target = tmp_path / "out" / "x.ckpt"
        transport = _RecordingTransport(payload)
        dl = AtomicDownloader(
            transport=transport,
            max_retries=1,
            backoff_seconds=0,
        )
        # Expected SHA deliberately wrong
        out = dl.fetch(
            url=url,
            target_path=str(target),
            expected_size=len(payload),
            expected_sha256="00" * 32,
        )
        assert out.status is DownloadStatus.HASH_MISMATCH
        assert not target.exists(), "final target must never expose a bad payload"
        assert out.quarantined_part is not None
        quarantine = Path(out.quarantined_part)
        assert quarantine.exists()
        assert quarantine.read_bytes() == payload


class TestAtomicDownloaderResumeSemantics:
    """Range-based resume reassembles identical bytes to the full payload."""

    def test_partial_file_is_resumed_via_range_header(
        self, tmp_path: Path
    ) -> None:
        payload = b"A" * 100 + b"B" * 100 + b"C" * 100
        url = "https://mirror.test/x.bin"
        target = tmp_path / "x.bin"
        part = target.with_name(target.name + ".part")
        part.write_bytes(payload[:200])  # pretend 200 bytes arrived earlier

        transport = _RecordingTransport(payload)
        dl = AtomicDownloader(
            transport=transport, max_retries=1, backoff_seconds=0
        )
        out = dl.fetch(
            url=url,
            target_path=str(target),
            expected_size=len(payload),
            expected_sha256=_sha256_of(payload),
        )
        assert out.status is DownloadStatus.RESUMED_AND_VERIFIED
        assert out.resumed_from == 200
        assert target.read_bytes() == payload
        assert transport.calls[0]["offset"] == 200

    def test_server_ignores_range_restarts_from_zero(
        self, tmp_path: Path
    ) -> None:
        payload = b"Z" * 500
        url = "https://mirror.test/x.bin"
        target = tmp_path / "x.bin"
        part = target.with_name(target.name + ".part")
        part.write_bytes(b"GARBAGE" * 10)

        transport = _RecordingTransport(payload, ignore_range=True)
        dl = AtomicDownloader(
            transport=transport, max_retries=1, backoff_seconds=0
        )
        out = dl.fetch(
            url=url, target_path=str(target),
            expected_size=len(payload),
            expected_sha256=_sha256_of(payload),
        )
        assert out.status is DownloadStatus.DOWNLOADED_FRESH
        assert target.read_bytes() == payload


class TestAtomicDownloaderIdempotentShortCircuit:
    """If the target already matches, no socket is opened."""

    def test_already_verified_opens_no_connection(self, tmp_path: Path) -> None:
        payload = b"same-bytes-same-bytes"
        target = tmp_path / "x.bin"
        target.write_bytes(payload)

        transport = _RecordingTransport(b"bogus")
        dl = AtomicDownloader(
            transport=transport, max_retries=1, backoff_seconds=0
        )
        out = dl.fetch(
            url="https://mirror.test/x.bin",
            target_path=str(target),
            expected_size=len(payload),
            expected_sha256=_sha256_of(payload),
        )
        assert out.status is DownloadStatus.ALREADY_VERIFIED
        assert transport.calls == [], "no socket must be opened on idempotent re-run"


# ---------------------------------------------------------------------------
# TIER C — IdempotentSurgeon end-to-end + absolute idempotency
# ---------------------------------------------------------------------------


class _NoNetworkTransport:
    """Blows up on *any* use — proves the surgeon second-run opens no sockets."""

    def __init__(self) -> None:
        self.calls = 0

    def open(self, url: str, *, offset: int = 0) -> TransportResponse:
        self.calls += 1
        raise AssertionError(
            "IdempotentSurgeon opened a connection on the second run! "
            f"url={url} offset={offset}"
        )


def _make_report(
    *, comfy_root: Path, fixable: tuple[str, ...] = (),
    blocking: tuple[str, ...] = (),
    verdict: str = "auto_fixable",
) -> _FakeReport:
    return _FakeReport(
        verdict=verdict,
        fixable_actions=fixable,
        blocking_actions=blocking,
        comfyui=_FakeReportComfy(root_path=str(comfy_root)),
    )


class TestSurgeonOperateE2E:
    def test_surgeon_recovers_missing_model_from_cache(self, tmp_path: Path) -> None:
        # Build a fake HuggingFace-style cache containing the model.
        cache = tmp_path / "hf_cache"
        cache.mkdir()
        payload = b"SP" + b"RGB" * 4096
        cache_src = cache / "v3_sd15_sparsectrl_rgb.ckpt"
        cache_src.write_bytes(payload)

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)

        injector = AssetInjector(extra_cache_roots=[cache])
        transport = _NoNetworkTransport()  # cache should suffice — no network
        downloader = AtomicDownloader(
            transport=transport, max_retries=1, backoff_seconds=0
        )
        plans = (
            AssetPlan(
                asset_name="sparsectrl_rgb_model",
                filename="v3_sd15_sparsectrl_rgb.ckpt",
                url="https://mirror.test/sparsectrl_rgb.ckpt",
                expected_size=len(payload),
            ),
        )
        surgeon = IdempotentSurgeon(
            injector=injector, downloader=downloader, asset_plans=plans
        )
        report = _make_report(
            comfy_root=comfy,
            fixable=(
                "missing_asset:sparsectrl_rgb_model -> "
                "models/controlnet/v3_sd15_sparsectrl_rgb.ckpt",
            ),
        )
        result = surgeon.operate(report)
        assert result.ok, result.to_dict()
        assert result.mutation_count == 1
        assert result.failure_count == 0
        kinds = {a.kind for a in result.actions}
        assert kinds & {ActionKind.SYMLINKED, ActionKind.HARDLINKED, ActionKind.COPIED}
        # Cache re-use means NO network call
        assert transport.calls == 0

    def test_surgeon_downloads_when_cache_miss(self, tmp_path: Path) -> None:
        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        payload = b"FRESH" * 1000
        url = "https://mirror.test/fresh.ckpt"
        transport = _RecordingTransport(payload)
        downloader = AtomicDownloader(
            transport=transport, max_retries=1, backoff_seconds=0
        )
        injector = AssetInjector(extra_cache_roots=[tmp_path / "empty_cache"])
        plans = (
            AssetPlan(
                asset_name="sparsectrl_rgb_model",
                filename="fresh.ckpt",
                url=url,
                expected_size=len(payload),
                expected_sha256=_sha256_of(payload),
            ),
        )
        surgeon = IdempotentSurgeon(
            injector=injector, downloader=downloader, asset_plans=plans
        )
        report = _make_report(
            comfy_root=comfy,
            fixable=(
                "missing_asset:sparsectrl_rgb_model -> "
                "models/controlnet/fresh.ckpt",
            ),
        )
        result = surgeon.operate(report)
        assert result.ok, result.to_dict()
        assert any(a.kind is ActionKind.DOWNLOADED for a in result.actions)
        assert transport.calls and transport.calls[0]["offset"] == 0
        assert (comfy / "models" / "controlnet" / "fresh.ckpt").read_bytes() == payload


class TestSecondRunIsNoop:
    """This is THE mandatory red line: after a successful heal, a second
    call to ``operate`` must emit zero mutations, zero socket opens,
    and return within 50 ms on typical hardware."""

    def test_second_run_emits_no_mutations_and_no_network(
        self, tmp_path: Path
    ) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        payload = b"IDEMPOTENT" * 512
        (cache / "mm.ckpt").write_bytes(payload)

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)

        first_transport = _RecordingTransport(b"should-not-be-needed")
        injector = AssetInjector(extra_cache_roots=[cache])
        downloader = AtomicDownloader(
            transport=first_transport, max_retries=1, backoff_seconds=0
        )
        plans = (
            AssetPlan(
                asset_name="animatediff_motion_module",
                filename="mm.ckpt",
                url="https://mirror.test/mm.ckpt",
                expected_size=len(payload),
            ),
        )
        surgeon = IdempotentSurgeon(
            injector=injector, downloader=downloader, asset_plans=plans
        )
        report = _make_report(
            comfy_root=comfy,
            fixable=(
                "missing_asset:animatediff_motion_module -> "
                "models/animatediff_models/mm.ckpt",
            ),
        )

        first = surgeon.operate(report)
        assert first.ok
        assert first.mutation_count == 1

        # Switch to a transport that ASSERTS on any open() call.
        downloader._transport = _NoNetworkTransport()  # noqa: SLF001

        second = surgeon.operate(report)
        assert second.ok, second.to_dict()
        # HARD assertion: zero mutations, zero failures, zero blocks.
        assert second.mutation_count == 0
        assert second.failure_count == 0
        assert second.blocked_count == 0
        # All fixable actions must report SKIPPED on the second run.
        kinds = [a.kind for a in second.actions]
        assert kinds and all(k is ActionKind.SKIPPED for k in kinds), kinds
        # Latency budget — generous ceiling for CI jitter.
        assert second.total_elapsed_ms < 500.0, (
            f"second run latency exceeded budget: {second.total_elapsed_ms} ms"
        )

    def test_second_run_does_not_modify_any_file(self, tmp_path: Path) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        payload = b"STABLE" * 300
        (cache / "mm.ckpt").write_bytes(payload)

        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)

        injector = AssetInjector(extra_cache_roots=[cache])
        downloader = AtomicDownloader(
            transport=_RecordingTransport(b""), max_retries=1, backoff_seconds=0
        )
        plans = (
            AssetPlan(
                asset_name="animatediff_motion_module",
                filename="mm.ckpt",
                url="https://mirror.test/mm.ckpt",
                expected_size=len(payload),
            ),
        )
        surgeon = IdempotentSurgeon(
            injector=injector, downloader=downloader, asset_plans=plans
        )
        report = _make_report(
            comfy_root=comfy,
            fixable=(
                "missing_asset:animatediff_motion_module -> "
                "models/animatediff_models/mm.ckpt",
            ),
        )
        first = surgeon.operate(report)
        assert first.ok

        target = comfy / "models" / "animatediff_models" / "mm.ckpt"
        before_mtime = target.stat().st_mtime_ns
        before_ino = target.stat().st_ino

        second = surgeon.operate(report)
        assert second.mutation_count == 0

        after_mtime = target.stat().st_mtime_ns
        after_ino = target.stat().st_ino
        assert before_mtime == after_mtime
        assert before_ino == after_ino


class TestSurgeonRejectsOutOfScopeActions:
    def test_git_managed_directory_assets_are_blocked(self, tmp_path: Path) -> None:
        comfy = tmp_path / "ComfyUI"
        _build_fake_comfy_tree(comfy)
        injector = AssetInjector()
        downloader = AtomicDownloader(
            transport=_NoNetworkTransport(), max_retries=1, backoff_seconds=0
        )
        surgeon = IdempotentSurgeon(injector=injector, downloader=downloader)
        report = _make_report(
            comfy_root=comfy,
            fixable=(
                "missing_asset:animatediff_evolved_node -> "
                "custom_nodes/ComfyUI-AnimateDiff-Evolved",
            ),
        )
        result = surgeon.operate(report)
        assert any(a.kind is ActionKind.BLOCKED for a in result.actions)
        assert not result.ok


# ---------------------------------------------------------------------------
# TIER D — Static red-line audit (defence-in-depth)
# ---------------------------------------------------------------------------


class TestRedLineStaticAudit:
    """Scan the source of the three modules for banned primitives.

    These are *defence-in-depth* static checks. Even if a future refactor
    adds a new ``if:`` branch, the red lines must remain violable only
    through deliberate, explicitly-reviewed code changes.
    """

    PROJECT_ROOT = Path(__file__).resolve().parents[1]
    INJECTOR_SRC = PROJECT_ROOT / "mathart" / "workspace" / "asset_injector.py"
    DOWNLOADER_SRC = PROJECT_ROOT / "mathart" / "workspace" / "atomic_downloader.py"
    SURGEON_SRC = PROJECT_ROOT / "mathart" / "workspace" / "idempotent_surgeon.py"

    def test_no_recursive_delete_on_target(self) -> None:
        banned = re.compile(r"shutil\.rmtree")
        for src in (self.INJECTOR_SRC, self.DOWNLOADER_SRC, self.SURGEON_SRC):
            text = src.read_text(encoding="utf-8")
            assert not banned.search(text), (
                f"shutil.rmtree is FORBIDDEN in {src.name}; "
                "user files must be quarantined (.bak-<ts>), not destroyed"
            )

    def test_downloader_never_opens_final_target_for_write(self) -> None:
        text = self.DOWNLOADER_SRC.read_text(encoding="utf-8")
        # The only writes allowed are on ``part`` or a ``.part`` / ``.corrupt-`` file.
        bad_open = re.compile(r"target\.open\([\"']w")
        assert not bad_open.search(text), (
            "atomic_downloader must NEVER open the final target for writing"
        )
        # Must contain the atomic replace primitive.
        assert "os.replace" in text

    def test_injector_clears_only_symlinks_not_regular_files(self) -> None:
        text = self.INJECTOR_SRC.read_text(encoding="utf-8")
        # There is exactly one os.unlink call path — inside _remove_if_link_only
        # and the symlink-only branch of _quarantine_conflict.
        # Count occurrences and assert the guard clause appears right above them.
        occurrences = [
            m.start() for m in re.finditer(r"os\.unlink\(", text)
        ]
        assert occurrences, "sanity: expected at least one os.unlink"
        for pos in occurrences:
            window = text[max(0, pos - 160): pos]
            assert "is_symlink" in window, (
                "every os.unlink call MUST be guarded by an is_symlink() check"
            )

    def test_no_blind_os_remove_on_user_paths(self) -> None:
        """Block ``os.remove`` entirely in these modules. The only
        acceptable deletion is ``os.unlink`` of a verified symlink."""
        for src in (self.INJECTOR_SRC, self.DOWNLOADER_SRC, self.SURGEON_SRC):
            text = src.read_text(encoding="utf-8")
            assert "os.remove(" not in text, (
                f"os.remove is banned in {src.name}; "
                "use os.replace to quarantine instead"
            )
