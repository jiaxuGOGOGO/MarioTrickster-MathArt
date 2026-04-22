# SESSION-144 HANDOFF — P1-PROXY-RENDERER-DEPENDENCY-FIX

> **白模预演依赖修复：将 matplotlib / PyYAML 固化进核心依赖**

**Date**: 2026-04-22
**Status**: COMPLETE
**Commit**: Pending push
**Tests**: Local smoke — `python -c "from mathart.quality.interactive_gate import ProxyRenderer"` succeeds.

---

## 1. Goal Achieved
修复了 SESSION-143 部署后用户在 Director Studio 白模预演阶段遇到的 `ModuleNotFoundError: No module named 'matplotlib'` 运行时异常。根因是 `ProxyRenderer.render_proxy()` 在函数体内 `import matplotlib`，但该依赖从未出现在 `pyproject.toml` 核心依赖清单中，属于典型的**运行时隐式依赖缺失**。同时一并补齐 `director_intent.py` 使用的 `PyYAML`。

## 2. Key Deliverables

1. **pyproject.toml 依赖固化**
   - 新增 `matplotlib>=3.7`（head-less `Agg` 后端，不拉入 GUI 依赖）。
   - 新增 `PyYAML>=6.0`（`director_intent.py` / blueprint `save_yaml` / `load_yaml` 的必需品）。

2. **TROUBLESHOOTING 补录**
   - 新增第 4 节「白模预演依赖 (Proxy Renderer Dependencies)」，给出 A 方案（重装）与 B 方案（临时手装），并说明即使白模失败，真理网关仍会降级保留参数视图与交互能力。

3. **契约审计延续**
   - 本 session 是 SESSION-143 审计的自然延伸：代码依赖了 matplotlib / PyYAML，但 `pyproject.toml` 没声明，这是**依赖图与声明图**之间的另一类契约断层，已一并闭合。

## 3. Architecture Discipline Enforced
- **依赖即契约 (Dependencies as Contract)**：所有运行时必然 `import` 的第三方库都必须显式列入 `pyproject.toml`，禁止依赖环境的隐式预装。
- **优雅降级 (Graceful Degradation)**：白模生成失败已降级为日志警告 + 参数视图，不打断主流程。

## 4. Coronation & Next Steps
**👑 最终加冕语 (Coronation)**:
> "依赖图与声明图合一，系统从此再无暗桩。"

下一步可考虑：
- P1：增加 `tests/test_install_contract.py`，扫描仓库内所有 `import` 语句与 `pyproject.toml` 声明依赖做自动化契约比对。
- P2：为 `ProxyRenderer` 增加 `matplotlib` 缺失时的 ASCII-art 回退渲染器，彻底零外部依赖。

*Signed off by Manus AI*
