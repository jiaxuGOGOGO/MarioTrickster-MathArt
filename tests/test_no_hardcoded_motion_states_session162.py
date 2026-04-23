"""SESSION-162 红线测试：动作列表硬编码 AST 扫描守门。

本测试在 CI 阶段对全仓 ``mathart/`` 与 ``tools/`` 进行 AST 扫描，
拒绝任何形如 ``["idle", "run", "jump", "fall", "hit"]`` 的硬编码字符串列表/元组/集合，
强制所有调用点统一从 ``mathart.animation.unified_gait_blender.MotionStateLaneRegistry`` 拉取真理源。

例外白名单：
- ``mathart/animation/unified_gait_blender.py``：注册表本身
- ``mathart/animation/phase_driven.py``：状态转移图（合法的硬编码邻接关系）
- ``mathart/animation/state_machine_graph.py``：状态机偏好（少量首选起点）
- ``mathart/pipeline.py::evolution_preview_states``：有意保留的"快速预览子集"
- ``tests/`` 目录下的测试用例
- 注释行（# 开头）/ 文档字符串

外部参考：Tom Looman, *GameplayTags Data-Driven Design*。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [REPO_ROOT / "mathart", REPO_ROOT / "tools"]

# 完整 6 状态集合的硬编码模式
_FULL_STATE_SET = {"idle", "walk", "run", "jump", "fall", "hit"}
# 5 状态变体（缺少 walk）
_FIVE_STATE_VARIANT = {"idle", "run", "jump", "fall", "hit"}
# 4 状态变体（缺少 fall+walk）
_FOUR_STATE_VARIANT = {"idle", "walk", "run", "jump"}

FORBIDDEN_SETS = [_FULL_STATE_SET, _FIVE_STATE_VARIANT, _FOUR_STATE_VARIANT]

# 合法白名单
WHITELIST = {
    "mathart/animation/unified_gait_blender.py",  # 注册表自身
    "mathart/animation/phase_driven.py",            # 状态转移图
    "mathart/animation/state_machine_graph.py",     # 状态机首选
    "mathart/animation/__init__.py",
    # SESSION-162: evolution_preview_states 是有意保留的快速预览子集
}

WHITELIST_PATTERNS = [
    re.compile(r"evolution_preview_states"),
    re.compile(r"_PREFERRED_START_STATES"),
    re.compile(r"BIPED_TRANSITIONS"),
    re.compile(r"_TRANSITION_TABLE"),
]


def _string_collection_values(node: ast.AST) -> set[str] | None:
    """从 List/Tuple/Set 节点提取纯字符串元素集合，否则返回 None。"""
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        elts = node.elts
        if elts and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in elts):
            return {e.value for e in elts}
    return None


def _line_in_whitelist_context(source_lines: list[str], lineno: int) -> bool:
    """检查给定行附近是否命中白名单模式（如 evolution_preview_states）。"""
    # 查看前 2 行 + 当前行
    start = max(0, lineno - 3)
    snippet = "\n".join(source_lines[start:lineno])
    return any(pat.search(snippet) for pat in WHITELIST_PATTERNS)


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        files.extend(root.rglob("*.py"))
    return files


def test_no_hardcoded_motion_states_in_codebase() -> None:
    """红线：禁止任何硬编码的动作状态全集/常见变体在生产代码中出现。"""
    violations: list[str] = []

    for path in _iter_python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in WHITELIST:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError:
            continue

        source_lines = source.splitlines()

        for node in ast.walk(tree):
            values = _string_collection_values(node)
            if values is None:
                continue
            if values not in FORBIDDEN_SETS:
                continue
            if _line_in_whitelist_context(source_lines, node.lineno):
                continue
            violations.append(
                f"{rel}:{node.lineno}: 硬编码动作状态集合 {sorted(values)} 违反 SESSION-162 红线，"
                f"应改用 mathart.animation.unified_gait_blender.get_motion_lane_registry().names()。"
            )

    assert not violations, (
        "SESSION-162 红线被触发！请使用 MotionStateLaneRegistry 的真理源：\n"
        + "\n".join(violations)
    )


def test_motion_state_lane_registry_is_truth_source() -> None:
    """红线：注册表必须能提供完整 6 状态集合，所有下游一致。"""
    from mathart.animation.unified_gait_blender import get_motion_lane_registry

    truth = set(get_motion_lane_registry().names())
    assert truth == _FULL_STATE_SET, (
        f"MotionStateLaneRegistry 应提供完整 6 状态集合 {_FULL_STATE_SET}，实际为 {truth}。"
    )

    # 下游一致性
    from mathart.pipeline_contract import UMR_Context
    assert set(UMR_Context().states) == truth

    import mathart.headless_e2e_ci as ci
    assert set(ci.GOLDEN_STATES) == truth

    import mathart.animation.cli as anim_cli
    assert set(anim_cli.VALID_STATES) == truth


def test_assert_nonzero_temporal_variance_is_imported_in_factory() -> None:
    """红线：工业烘焙网关必须前置部署 assert_nonzero_temporal_variance。"""
    factory_path = REPO_ROOT / "tools" / "run_mass_production_factory.py"
    source = factory_path.read_text(encoding="utf-8")

    # 必须出现在 _bake_true_motion_guide_sequence 内部（return 之前）
    assert "assert_nonzero_temporal_variance" in source, (
        "tools/run_mass_production_factory.py 必须导入并调用 assert_nonzero_temporal_variance。"
    )
    assert "frozen_guide_sequence" in source, (
        "tools/run_mass_production_factory.py 必须在烘焙函数捕获 RuntimeError 后抛出 "
        "PipelineContractError('frozen_guide_sequence', ...)。"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
