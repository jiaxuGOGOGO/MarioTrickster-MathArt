> **Task**: P0-SESSION-165-ARCHITECTURE-CLEANUP-AND-FAIL-LOUD
> **Date**: 2026-04-23
> **Scope**: 彻底清剿悬空导入断链、分辨率契约对齐与终端异常显性化告警

---

## 1. Dependency Inversion Principle (DIP / 依赖倒置原则)
核心领域代码（如 `mathart/workspace/mode_dispatcher.py`）绝对禁止硬编码反向导入外围或工具脚本。外部脚本应调用核心库，或将量产中枢重构内聚至核心模块内部，以消除跨边界的 `ModuleNotFoundError`。
- **参考出处**: Robert "Uncle Bob" Martin, *Clean Architecture* (2012)
- **本次应用**: `tools/run_mass_production_factory.py` 提拔迁移为 `mathart/factory/mass_production.py`，物理内聚解决 `sys.path` Hack 问题。

## 2. Explicit Error Surfacing (显性错误暴露与大声失败)
在前端 UI 捕获到内部调度异常时，防闪退（Graceful Degradation）是正确的，但绝对不能对用户“静默吞没异常（Swallowing Exceptions）”。必须在控制台标准输出打印高亮的错误提示，避免用户陷入“按了没反应”的假死错觉。
- **参考出处**: Joel Spolsky, *Fail Loudly: A Plea to Stop Hiding Bugs*
- **本次应用**: `mathart/cli_wizard.py` 中 `_dispatch_mass_production` 的 Catch-all 逻辑追加了 ANSI 红色的高亮终端打印。

## 3. Contract-Driven Default Hydration (契约驱动的默认值水合)
如果底层系统通过 PipelineContractError 设置了刚性的契约底线（如最低分辨率 64x64），上游的生成器和默认配置必须同步上调基准线，杜绝系统在开箱即用时触发断路器崩溃。
- **参考出处**: Bertrand Meyer, *Design by Contract* (1992)
- **本次应用**: 将工业烘焙的默认分辨率从 `32x32` 和 `64x64` 硬性提升至 `192x192`。

## 4. 动画轨迹平滑插值 (Catmull-Rom Spline)
- **参考出处**: Edwin Catmull and Raphael Rom, *A Class of Local Interpolating Splines* (1974)
- **本次应用**: 工业烘焙网关高亮提示 "正在通过 Catmull-Rom 样条插值，纯 CPU 解算高精度工业级贴图动作序列..."。
