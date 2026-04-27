"""tests/test_preflight_radar.py — Preflight Radar end-to-end assertions.

P0-SESSION-129-PREFLIGHT-RADAR audit suite.

The mandate from the operator (excerpt):

  > 必须新增测试 tests/test_preflight_radar.py。使用 Mock 伪造一个包含
  > "软链接模型" 的假 ComfyUI 目录树和带权限限制的假进程列表。强制断言：
  >   1. 雷达能从假进程表逆推出正确路径，
  >   2. 穿透软链接准确识别出模型已就绪（0 误报），
  >   3. 对无权限进程优雅跳过不抛错。

In addition, this suite locks in:
  4. The strict read-only red-line — the radar source contains no banned
     side-effecting calls (pip install, git clone, requests.get, os.makedirs).
  5. The decision matrix — READY / AUTO_FIXABLE / MANUAL_INTERVENTION_REQUIRED
     produced by ``_synthesize_verdict`` covers all branches.
  6. The Python environment probe degrades gracefully on missing packages.
  7. The GPU probe fabricates a clean ``MISSING`` verdict on a CPU-only host
     and an OK verdict on a synthetic ``nvidia-smi`` response.
  8. ``PreflightReport.to_json`` round-trips into a parseable JSON object.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional
from unittest import mock

import pytest

from mathart.workspace import preflight_radar as radar_mod
from mathart.workspace.preflight_radar import (
    AssetCheck,
    ComfyUIDiscovery,
    GPUProbe,
    HealthStatus,
    PreflightRadar,
    PreflightReport,
    PreflightVerdict,
    PythonEnvironmentProbe,
    REQUIRED_COMFYUI_ASSETS,
)


# ---------------------------------------------------------------------------
# Helpers — fake ComfyUI tree builder + fake psutil process surface
# ---------------------------------------------------------------------------

def _materialize_fake_comfyui(root: Path,
                              *,
                              symlink_models_to: Optional[Path] = None,
                              omit_assets: Iterable[str] = ()) -> Path:
    """Create a minimal fake ComfyUI directory tree for radar exercise.

    If ``symlink_models_to`` is supplied, the ComfyUI ``models`` directory
    is replaced with a symlink to that external location — exactly like the
    typical "models on a secondary HDD" power-user setup the radar must
    handle without false negatives.
    """

    root.mkdir(parents=True, exist_ok=True)
    (root / "main.py").write_text("# fake comfyui main.py\n")
    (root / "custom_nodes").mkdir(exist_ok=True)

    # Custom node: AnimateDiff-Evolved + sparse_ctrl module + controlnet_aux
    if "animatediff_evolved_node" not in omit_assets:
        ade_dir = root / "custom_nodes" / "ComfyUI-AnimateDiff-Evolved"
        ade_dir.mkdir(parents=True, exist_ok=True)
        (ade_dir / "__init__.py").write_text("# ade init\n")
        if "sparsectrl_loader_module" not in omit_assets:
            (ade_dir / "animatediff").mkdir(exist_ok=True)
            (ade_dir / "animatediff" / "sparse_ctrl.py").write_text(
                "# sparse_ctrl loader\n"
            )
    if "controlnet_aux_node" not in omit_assets:
        (root / "custom_nodes" / "comfyui_controlnet_aux").mkdir(
            parents=True, exist_ok=True
        )
    if "ipadapter_plus_node" not in omit_assets:
        (root / "custom_nodes" / "ComfyUI_IPAdapter_plus").mkdir(
            parents=True, exist_ok=True
        )

    # Models directory (potentially symlinked to a separate physical location)
    if symlink_models_to is not None:
        symlink_models_to.mkdir(parents=True, exist_ok=True)
        (symlink_models_to / "controlnet").mkdir(exist_ok=True)
        (symlink_models_to / "animatediff_models").mkdir(exist_ok=True)
        if "ipadapter_models_dir" not in omit_assets:
            (symlink_models_to / "ipadapter").mkdir(exist_ok=True)
        if "clip_vision_models_dir" not in omit_assets:
            (symlink_models_to / "clip_vision").mkdir(exist_ok=True)
        if "sparsectrl_rgb_model" not in omit_assets:
            (symlink_models_to / "controlnet" / "v3_sd15_sparsectrl_rgb.ckpt").write_bytes(
                b"\x00" * 32
            )
        if "animatediff_motion_module" not in omit_assets:
            (symlink_models_to / "animatediff_models" / "v3_sd15_mm.ckpt").write_bytes(
                b"\x00" * 32
            )
        models_link = root / "models"
        if models_link.exists() or models_link.is_symlink():
            models_link.unlink()
        os.symlink(symlink_models_to, models_link, target_is_directory=True)
    else:
        models_dir = root / "models"
        (models_dir / "controlnet").mkdir(parents=True, exist_ok=True)
        (models_dir / "animatediff_models").mkdir(parents=True, exist_ok=True)
        if "ipadapter_models_dir" not in omit_assets:
            (models_dir / "ipadapter").mkdir(parents=True, exist_ok=True)
        if "clip_vision_models_dir" not in omit_assets:
            (models_dir / "clip_vision").mkdir(parents=True, exist_ok=True)
        if "sparsectrl_rgb_model" not in omit_assets:
            (models_dir / "controlnet" / "v3_sd15_sparsectrl_rgb.ckpt").write_bytes(
                b"\x00" * 32
            )
        if "animatediff_motion_module" not in omit_assets:
            (models_dir / "animatediff_models" / "v3_sd15_mm.ckpt").write_bytes(
                b"\x00" * 32
            )

    return root


class _FakeAccessDenied(Exception):
    """Stand-in for ``psutil.AccessDenied`` — independent of psutil install."""


class _FakeNoSuchProcess(Exception):
    """Stand-in for ``psutil.NoSuchProcess``."""


class _FakeZombieProcess(Exception):
    """Stand-in for ``psutil.ZombieProcess``."""


class _FakeProc:
    """Minimal psutil.Process duck — exposes ``.info`` like ``process_iter``."""

    def __init__(self, info: dict[str, Any], *, raise_on_access: Optional[Exception] = None):
        self._info = info
        self._raise_on_access = raise_on_access

    @property
    def info(self) -> dict[str, Any]:
        if self._raise_on_access is not None:
            raise self._raise_on_access
        return self._info


class _FakePsutil:
    """Drop-in mock that mimics the slice of ``psutil`` the radar uses."""

    AccessDenied = _FakeAccessDenied
    NoSuchProcess = _FakeNoSuchProcess
    ZombieProcess = _FakeZombieProcess

    def __init__(self, processes: list[_FakeProc]):
        self._processes = processes

    def process_iter(self, attrs=None):  # noqa: ARG002 — signature mirror
        return list(self._processes)


# ---------------------------------------------------------------------------
# 1. Symlink-traversal correctness — zero false negatives
# ---------------------------------------------------------------------------

class TestSymlinkTransparentAssetProbe:
    def test_symlinked_models_directory_is_resolved(self, tmp_path: Path):
        comfy_root = tmp_path / "ComfyUI"
        external_models = tmp_path / "external_drive" / "MyModels"
        _materialize_fake_comfyui(comfy_root, symlink_models_to=external_models)

        radar = PreflightRadar(
            psutil_module=_FakePsutil(processes=[]),
            extra_candidate_roots=[comfy_root],
            nvidia_smi_runner=lambda: None,
        )
        report = radar.scan()

        assert report.comfyui.found, "radar must find the fake ComfyUI via filesystem heuristic"
        assert report.comfyui.root_path == str(comfy_root.resolve())
        # Every required asset must be classified as OK despite the symlink hop
        per_asset = {c.name: c for c in report.comfyui.asset_checks}
        for entry in REQUIRED_COMFYUI_ASSETS:
            check = per_asset[entry["name"]]
            assert check.exists, (
                f"asset {entry['name']!r} must exist behind symlink, "
                f"got resolved_path={check.resolved_path}"
            )
            assert check.status is HealthStatus.OK
        # The symlinked models must report resolved_path pointing at the
        # external drive, proving Path.resolve() actually traversed the link.
        rgb = per_asset["sparsectrl_rgb_model"]
        assert rgb.resolved_path is not None
        assert "external_drive" in rgb.resolved_path

    def test_missing_single_asset_marks_auto_fixable(self, tmp_path: Path):
        comfy_root = tmp_path / "ComfyUI"
        _materialize_fake_comfyui(
            comfy_root,
            omit_assets={"sparsectrl_rgb_model"},
        )
        radar = PreflightRadar(
            psutil_module=_FakePsutil(processes=[]),
            extra_candidate_roots=[comfy_root],
            nvidia_smi_runner=lambda: None,
        )
        report = radar.scan()

        assert report.comfyui.found
        assert report.comfyui.status is HealthStatus.DEGRADED
        assert report.verdict is PreflightVerdict.AUTO_FIXABLE
        assert any(
            "sparsectrl_rgb_model" in action for action in report.fixable_actions
        )


# ---------------------------------------------------------------------------
# 2. Process-table reverse lookup
# ---------------------------------------------------------------------------

class TestProcessReverseLookup:
    def test_radar_reverse_engineers_root_from_main_py_argv(self, tmp_path: Path):
        comfy_root = tmp_path / "ComfyUI"
        _materialize_fake_comfyui(comfy_root)

        fake_processes = [
            # (a) ComfyUI process the radar must lock onto
            _FakeProc({
                "pid": 4242,
                "name": "python3.11",
                "cmdline": ["python3.11", str(comfy_root / "main.py"),
                            "--listen", "0.0.0.0", "--port", "8188"],
                "cwd": str(comfy_root),
            }),
            # (b) An unrelated python process that mentions main.py for an
            #     entirely different project — must NOT poison detection.
            _FakeProc({
                "pid": 4243,
                "name": "python3.11",
                "cmdline": ["python3.11", "/var/tmp/other_project/main.py"],
                "cwd": "/var/tmp/other_project",
            }),
            # (c) A non-python noisemaker — must be ignored entirely.
            _FakeProc({
                "pid": 4244,
                "name": "nginx",
                "cmdline": ["nginx", "-g", "daemon off;"],
                "cwd": "/etc/nginx",
            }),
        ]

        radar = PreflightRadar(
            psutil_module=_FakePsutil(fake_processes),
            extra_candidate_roots=[],
            nvidia_smi_runner=lambda: None,
        )
        discovery = radar.discover_comfyui()

        assert discovery.found
        assert discovery.detection_method == "process_scan"
        assert discovery.process_pid == 4242
        assert discovery.root_path == str(comfy_root.resolve())


# ---------------------------------------------------------------------------
# 3. Defensive psutil iteration — never crashes on AccessDenied / Zombie
# ---------------------------------------------------------------------------

class TestPsutilSafetyNet:
    def test_access_denied_processes_are_silently_skipped(self, tmp_path: Path):
        comfy_root = tmp_path / "ComfyUI"
        _materialize_fake_comfyui(comfy_root)

        fake_processes = [
            _FakeProc({}, raise_on_access=_FakeAccessDenied("mocked")),
            _FakeProc({}, raise_on_access=_FakeNoSuchProcess("mocked")),
            _FakeProc({}, raise_on_access=_FakeZombieProcess("mocked")),
            _FakeProc({}, raise_on_access=OSError("mocked /proc lockdown")),
            _FakeProc({
                "pid": 9999,
                "name": "python",
                "cmdline": ["python", str(comfy_root / "main.py")],
                "cwd": str(comfy_root),
            }),
        ]
        radar = PreflightRadar(
            psutil_module=_FakePsutil(fake_processes),
            extra_candidate_roots=[],
            nvidia_smi_runner=lambda: None,
        )

        # The very call must not propagate any of the injected exceptions.
        discovery = radar.discover_comfyui()
        assert discovery.found
        assert discovery.process_pid == 9999

    def test_radar_scan_never_raises_even_with_no_psutil(self, tmp_path: Path):
        radar = PreflightRadar(
            psutil_module=None,
            extra_candidate_roots=[],
            nvidia_smi_runner=lambda: None,
        )
        report = radar.scan()
        assert isinstance(report, PreflightReport)
        assert report.verdict in PreflightVerdict


# ---------------------------------------------------------------------------
# 4. GPU probe — torch fallback chain
# ---------------------------------------------------------------------------

class TestGPUProbe:
    def test_no_gpu_no_torch_returns_missing(self):
        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            torch_module=None,
            nvidia_smi_runner=lambda: None,
        )
        gpu = radar.probe_gpu()
        assert gpu.available is False
        assert gpu.status is HealthStatus.MISSING
        assert gpu.detection_method == "none"

    def test_synthetic_nvidia_smi_csv_is_parsed(self):
        csv = "NVIDIA GeForce RTX 4090, 24564, 535.129.03\nNVIDIA RTX A6000, 49140, 535.129.03"
        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            torch_module=None,
            nvidia_smi_runner=lambda: csv,
        )
        gpu = radar.probe_gpu()
        assert gpu.available is True
        assert gpu.detection_method == "nvidia-smi"
        assert gpu.device_count == 2
        assert gpu.total_vram_mb == (24564, 49140)
        assert gpu.driver_version == "535.129.03"

    def test_torch_module_takes_precedence_over_smi(self):
        torch_stub = mock.MagicMock()
        torch_stub.cuda.is_available.return_value = True
        torch_stub.cuda.device_count.return_value = 1
        torch_stub.cuda.get_device_name.return_value = "NVIDIA H100"
        props = mock.MagicMock(total_memory=80 * 1024 * 1024 * 1024)
        torch_stub.cuda.get_device_properties.return_value = props
        torch_stub.version.cuda = "12.4"

        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            torch_module=torch_stub,
            nvidia_smi_runner=lambda: "should be ignored",
        )
        gpu = radar.probe_gpu()
        assert gpu.detection_method == "torch.cuda"
        assert gpu.device_names == ("NVIDIA H100",)
        assert gpu.total_vram_mb == (80 * 1024,)
        assert gpu.cuda_version == "12.4"


# ---------------------------------------------------------------------------
# 5. Python environment probe
# ---------------------------------------------------------------------------

class TestPythonEnvProbe:
    def test_all_packages_present_returns_ok(self):
        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            packages=("pip",),  # pip is guaranteed to be installed in CI
            nvidia_smi_runner=lambda: None,
        )
        env = radar.probe_python_env()
        assert env.status is HealthStatus.OK
        assert env.missing_packages == ()

    def test_missing_packages_degrade_status(self):
        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            packages=("pip", "definitely_does_not_exist_pkg_xyz"),
            nvidia_smi_runner=lambda: None,
        )
        env = radar.probe_python_env()
        assert env.status is HealthStatus.DEGRADED
        assert "definitely_does_not_exist_pkg_xyz" in env.missing_packages


# ---------------------------------------------------------------------------
# 6. Verdict synthesis — full decision matrix
# ---------------------------------------------------------------------------

class TestVerdictMatrix:
    def _full_ready_setup(self, tmp_path: Path) -> PreflightRadar:
        comfy_root = tmp_path / "ComfyUI"
        _materialize_fake_comfyui(comfy_root)
        return PreflightRadar(
            psutil_module=_FakePsutil([]),
            extra_candidate_roots=[comfy_root],
            packages=("pip",),
            torch_module=None,
            nvidia_smi_runner=lambda: "NVIDIA RTX 4090, 24564, 535.0",
        )

    def test_ready_when_everything_green(self, tmp_path: Path):
        radar = self._full_ready_setup(tmp_path)
        report = radar.scan()
        assert report.verdict is PreflightVerdict.READY, report.summary

    def test_auto_fixable_when_only_assets_missing(self, tmp_path: Path):
        comfy_root = tmp_path / "ComfyUI"
        _materialize_fake_comfyui(
            comfy_root, omit_assets={"animatediff_motion_module"},
        )
        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            extra_candidate_roots=[comfy_root],
            packages=("pip",),
            nvidia_smi_runner=lambda: None,
        )
        report = radar.scan()
        assert report.verdict is PreflightVerdict.AUTO_FIXABLE
        assert report.fixable_actions
        assert not report.blocking_actions

    def test_manual_when_no_comfy(self, tmp_path: Path):
        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            extra_candidate_roots=[tmp_path / "definitely_not_here"],
            packages=("pip",),
            nvidia_smi_runner=lambda: None,
        )
        report = radar.scan()
        assert report.verdict is PreflightVerdict.MANUAL_INTERVENTION_REQUIRED
        assert any("comfyui_not_found" in b for b in report.blocking_actions)

    def test_require_gpu_escalates_to_manual(self, tmp_path: Path):
        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            extra_candidate_roots=[],
            packages=("pip",),
            torch_module=None,
            nvidia_smi_runner=lambda: None,
            require_gpu=True,
        )
        report = radar.scan()
        assert report.verdict is PreflightVerdict.MANUAL_INTERVENTION_REQUIRED
        assert any("gpu_required_but_not_available" in b for b in report.blocking_actions)


# ---------------------------------------------------------------------------
# 7. Read-only red-line — source must contain no banned mutations
# ---------------------------------------------------------------------------

class TestReadOnlyRedLine:
    def test_source_contains_no_banned_side_effects(self):
        src = Path(radar_mod.__file__).read_text(encoding="utf-8")
        # Strip docstrings and comments before scanning so that prose
        # discussing the prohibition does not trip the assertion.
        # Conservative pass: drop lines starting with '#' and triple-quote blocks.
        no_comments = re.sub(r"#.*", "", src)
        no_docstrings = re.sub(r'"""[\s\S]*?"""', "", no_comments)

        banned_patterns = [
            r"\bpip install\b",
            r"\bgit clone\b",
            r"\brequests\.get\(",
            r"\bos\.makedirs\(",
            r"\burllib\.request\.urlretrieve\(",
        ]
        for pattern in banned_patterns:
            assert not re.search(pattern, no_docstrings), (
                f"banned mutation pattern {pattern!r} leaked into preflight_radar.py"
            )


# ---------------------------------------------------------------------------
# 8. JSON contract — report must round-trip through json.loads
# ---------------------------------------------------------------------------

class TestReportSerialization:
    def test_to_json_is_machine_parseable(self, tmp_path: Path):
        comfy_root = tmp_path / "ComfyUI"
        _materialize_fake_comfyui(comfy_root)
        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            extra_candidate_roots=[comfy_root],
            packages=("pip",),
            nvidia_smi_runner=lambda: None,
        )
        report = radar.scan()
        payload = json.loads(report.to_json())
        assert payload["verdict"] in {v.value for v in PreflightVerdict}
        assert payload["radar_version"] == "1.1.0"
        assert "gpu" in payload and "python_env" in payload and "comfyui" in payload
        assert isinstance(payload["fixable_actions"], list)
        assert isinstance(payload["blocking_actions"], list)
        # Asset checks must be projected into JSON, not lost in the cast.
        assert isinstance(payload["comfyui"]["asset_checks"], list)
        assert payload["comfyui"]["asset_checks"], "asset checks must be present"


# ---------------------------------------------------------------------------
# 9. Symlink anywhere on the path — broader coverage
# ---------------------------------------------------------------------------

class TestSymlinkOnIntermediateDirectory:
    def test_symlink_at_models_subtree_is_handled(self, tmp_path: Path):
        comfy_root = tmp_path / "ComfyUI"
        external_controlnet = tmp_path / "ssd_array" / "ckpts" / "cn"
        _materialize_fake_comfyui(comfy_root)
        # Replace just the controlnet/ subtree with an external symlink
        cn_target = comfy_root / "models" / "controlnet"
        for item in list(cn_target.iterdir()):
            item.unlink()
        cn_target.rmdir()
        external_controlnet.mkdir(parents=True, exist_ok=True)
        (external_controlnet / "v3_sd15_sparsectrl_rgb.ckpt").write_bytes(b"x" * 16)
        os.symlink(external_controlnet, cn_target, target_is_directory=True)

        radar = PreflightRadar(
            psutil_module=_FakePsutil([]),
            extra_candidate_roots=[comfy_root],
            nvidia_smi_runner=lambda: None,
        )
        discovery = radar.discover_comfyui()
        assert discovery.found
        cn_check = next(c for c in discovery.asset_checks
                        if c.name == "sparsectrl_rgb_model")
        assert cn_check.exists
        assert cn_check.is_symlink, "intermediate symlink must be flagged"
        assert "ssd_array" in (cn_check.resolved_path or "")
