"""Regression tests for SESSION-137 HITL boundary gateways."""
from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path
from unittest import mock

import pytest

from mathart.workspace.asset_injector import AssetInjector
from mathart.workspace.atomic_downloader import AtomicDownloader, TransportResponse
from mathart.workspace.hitl_boundary import ManualInterventionRequiredError


class _TimeoutTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def open(self, url: str, *, offset: int = 0) -> TransportResponse:
        self.calls.append({"url": url, "offset": offset})
        raise TimeoutError("simulated HTTP Timeout while downloading model")


def _build_fake_comfy_tree(root: Path) -> None:
    (root / "models" / "controlnet").mkdir(parents=True, exist_ok=True)


def test_large_windows_symlink_privilege_boundary_raises_manual_error(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    src = cache / "huge.ckpt"
    src.write_bytes(b"0")

    comfy = tmp_path / "ComfyUI"
    _build_fake_comfy_tree(comfy)
    target = comfy / "models" / "controlnet" / "huge.ckpt"

    injector = AssetInjector(
        extra_cache_roots=[cache],
        platform_name="win32",
        large_file_copy_threshold_bytes=1,
    )

    winerror_1314 = OSError(errno.EPERM, "symlink privilege not held")
    with mock.patch.object(os, "symlink", side_effect=winerror_1314), mock.patch.object(
        shutil,
        "copy2",
        side_effect=AssertionError("large file copy must not run without user confirmation"),
    ):
        with pytest.raises(ManualInterventionRequiredError) as caught:
            injector.inject(
                asset_name="huge_model",
                target_path=str(target),
                expected_filename="huge.ckpt",
                expected_size=1,
            )

    error = caught.value
    assert error.code == "symlink_privilege_guard"
    assert any(option.key == "force_copy" for option in error.options)
    assert not target.exists()


def test_download_timeout_boundary_raises_manual_error_after_bounded_retries(tmp_path: Path) -> None:
    target = tmp_path / "downloads" / "model.bin"
    transport = _TimeoutTransport()
    downloader = AtomicDownloader(transport=transport, max_retries=3, backoff_seconds=0.0)

    with pytest.raises(ManualInterventionRequiredError) as caught:
        downloader.fetch(url="https://huggingface.co/example/model.bin", target_path=str(target))

    error = caught.value
    assert error.code == "network_timeout_guard"
    assert len(transport.calls) == 3
    assert error.context["attempts"] == 3
    assert not target.exists()
    assert not target.with_name(target.name + ".part").exists()
