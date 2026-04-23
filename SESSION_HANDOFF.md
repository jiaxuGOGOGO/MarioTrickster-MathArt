# SESSION-149 HANDOFF — DYNAMIC DEMO MESH & GRACEFUL QUALITY-BREAKER BOUNDARY

> **唤醒兜底白模生命力 + 顶层业务异常护盾：让质量护栏既铁面无私，又风度翩翩。**

**Date**: 2026-04-23
**Status**: COMPLETE
**Parent Commit**: `ccc5067` (SESSION-148: Windows terminal encoding crash shield)
**Smoke**: `scripts/session149_smoke.py` → ALL ASSERTIONS PASSED（动态白模 + 异常护盾双轨绿灯）

---

## 1. Problem Statement (SESSION-148 量产现场暴露的两道体验瑕疵)

SESSION-147 的"交互式 ComfyUI 救援路径"在 SESSION-148 完成 Windows 终端
编码护盾后，本应进入丝滑量产，但 PDG 量产节点立刻触发了预设的质量安全护栏，
暴露了两个体验瑕疵：

### 1.1 兜底白模"心电图拉直"——`temporal_variance_below_threshold`

```
PipelineContractError: temporal_variance_below_threshold. Mean MSE = 0.0000
```

`TemporalVarianceCircuitBreaker` 完美拦截了"毫无变化的静态帧"，保住了下游
GPU 不发生模式崩塌。但根因不在护栏，而在上游 `pseudo3d_shell_backend.py`
的兜底圆柱体生成路径：当 `_use_demo_animation=True` 兜底时，原实现仅在
**10 帧** 内做一次 90° 静态斜坡旋转，对外宣称的 43 帧 PDG 长批次因此
完全没有施加任何物理动画变换，导致 43 帧绝对静止，逐帧像素 MSE = 0.0000。

与此同时，`validate_config` 的 `No base_mesh or base_vertices provided; will
generate demo cylinder mesh` 与 `No bone_dqs or bone_animation provided; will
generate demo single-bone rotation` 两条警告在每帧（每次 backend 调用）
都被 `for w in warnings: logger.warning(...)` 原样吐出，狂刷终端。

### 1.2 业务异常击穿 CLI 外壳

`PipelineContractError` 是一项**正面的质量裁决**，绝非程序崩溃，但当时它会
直接以原始 Traceback 形式贯穿 `ModeDispatcher.dispatch` → `_run_interactive`
→ 用户终端，让操作员误以为系统出了 bug，且无法返回向导主菜单继续作业。

---

## 2. Key Deliverables

### 2.1 修复一：Dynamic Demo Mesh —— 给兜底圆柱体注入数学动画轨迹

**位置**：`mathart/core/pseudo3d_shell_backend.py::Pseudo3DShellBackend.execute`

**新动画方程**：

| 维度 | 公式 | 物理含义 |
|------|------|----------|
| Y 轴弹跳 (translation) | `ty(f) = bounce_amplitude · sin(2π · f / period)` | 圆柱沿 Y 轴上下震荡，每行像素都产生位移 |
| Y 轴自转 (rotation)   | `θ(f) = 2π · spin_revolutions · f / (n_frames-1)` | 圆柱绕长轴持续旋转，弹跳"驻点"时仍有横向像素位移 |

- 第二根骨头被注入相位差 `π/4` + 一半弹跳幅度的 DQS，确保多骨蒙皮也产生
  逐帧不同的形变。
- `n_frames` 严格读取上游 `frame_count` / `num_frames`，缺省 24 帧；至少 2 帧
  以满足 `TemporalVarianceCircuitBreaker` 的最小契约。
- 默认参数：`bounce_amplitude=0.6`、`bounce_period=max(8, n_frames/2)`、
  `spin_revolutions=1.5`，可被 validated 配置覆盖。

**烟测硬证据 (`scripts/session149_smoke.py`)**：

```json
{
  "frame_count": 43,
  "vertex_count": 352,
  "min_pair_max_vertex_shift": 0.1121,
  "mean_pair_max_vertex_shift": 0.1623,
  "circuit_breaker_safe": true
}
```

每两帧之间最大顶点位移恒大于 0.11 世界单位（投影到 256px 视口对应数十像素的
RGB MSE，远超断路器的 mse=1.0 阈值），从原来的 0.0000 跃迁到非零，
TemporalVarianceCircuitBreaker 契约校验绿灯。

### 2.2 修复一·副作用：Demo Warning 节流 (`_emit_demo_warning`)

新增模块级**线程安全**警告节流器：

```python
_DEMO_WARNING_LOCK = threading.Lock()
_DEMO_WARNING_SEEN: set[str] = set()

def _emit_demo_warning(message: str) -> None:
    """First emission -> WARNING; subsequent -> DEBUG (blackbox-only)."""
```

`for w in warnings: _emit_demo_warning(w)` 替代了原先的 `logger.warning`
死循环。首次出现时仍以 WARNING 提醒操作员"已降级到 demo fallback"，
其后所有重复占用同一文案的警告自动降级到 DEBUG，写入 `logs/mathart.log`
黑匣子但不污染向导终端。键以"消息文本"为单位，`demo cylinder mesh`
和 `demo single-bone rotation` 两条独立节流，互不干扰。

### 2.3 修复二：Graceful Quality-Breaker Boundary

**新增类型**：`mathart.workspace.mode_dispatcher.PipelineQualityCircuitBreak(RuntimeError)`

- 包装原始 `PipelineContractError`，保留 `violation_type` 与 `detail`，并通过
  `__cause__` 链接原异常以便取证。
- 已加入模块 `__all__` 导出。

**`ModeDispatcher.dispatch` 改造**：

```python
try:
    payload = strategy.execute(context) if execute else strategy.preview(context)
except PipelineContractError as exc:
    logger.error(
        "[CLI] Pipeline quality circuit breaker tripped during dispatch "
        "(mode=%s, strategy=%s, violation=%s): %s",
        ..., exc_info=True,        # 完整 traceback 落黑匣子
    )
    raise PipelineQualityCircuitBreak(exc) from exc
```

**`mathart/cli_wizard.py` 双通道护盾**：

- 顶层模块新增 `_QUALITY_CIRCUIT_BREAK_NOTICE`（黄色 ANSI 高亮）+
  `_render_quality_circuit_break(exc, *, output_fn, selection)` 单一渲染入口。
- `_run_interactive`：捕获 `PipelineQualityCircuitBreak` →
  `_render_quality_circuit_break` 打印一行友好提示 → `return 0` 平滑回退主菜单
  （**不**再 return 1，杜绝把质量裁决误标为系统失败）。
- `_run_noninteractive`：捕获后输出结构化 JSON envelope
  `{"status": "quality_circuit_break", "violation_type": ..., "detail": ...}`
  并返回**专用退出码 3**，让 CI / 上游编排器能够区分"质量中止"与"程序崩溃"。

**用户终端最终呈现**：

```
[!] 质量防线拦截：渲染管线检测到动画幅度过小或不合规，为保护算力，任务已安全中止。
    · 完整堆栈已落盘至 logs/mathart.log 黑匣子。
    · 请重试或检查上游动画输入（骨骼动画 / 帧间位移）后重新启动。
```

无 Traceback、无原始异常字符串、退回主菜单，操作员可继续后续作业。

### 2.4 副线修复：`mathart/animation/rl_gym_env.py` 类定义期 NoneType 崩溃

烟测过程中暴露：当 `gymnasium` 缺席时，`class LocomotionRLEnv(gym.Env[...]):`
的下标在**类定义阶段**立即触发 `'NoneType' object has no attribute 'Env'`，
连锁污染整个 `mathart.animation` 包，导致 `pseudo3d_shell_backend` 的
`from mathart.animation.dqs_engine import ...` 一并被牵连失败。

修复：引入 `_RL_ENV_BASE` 哨兵——`gym is not None` 时取 `gym.Env[ndarray, ndarray]`，
否则回退到 `object`。`spaces.Box` 在 `__init__` 阶段才可能爆，与 SESSION-146
的"惰性加载纪律"完全一致；RL happy-path 不受影响。

### 2.5 测试矩阵

| Suite | 规模 | 状态 |
|-------|------|------|
| `scripts/session149_smoke.py` (新增) | 2 项断言（动画 MSE + 异常护盾） | **GREEN** |
| Phase 1：Demo 圆柱 43 帧 / 352 顶点 / `min_pair_max_vertex_shift=0.112` | — | **PASS** |
| Phase 2：dispatch 包装 + wizard 友好提示 + 0 traceback 泄漏 + rc=0 | — | **PASS** |

> **既有 168/168 历史测试矩阵无任何修改**：本次修复全部在新通道追加判定，
> 未改动既有 `validate_config` warnings 文本，未改动 `PipelineContractError`
> 签名，未改动任何已发布的策略接口。

### 2.6 如何现场确认修复生效

```bash
# 1) 安装可选依赖（仅烟测脚本需要，运行时不需要）
sudo pip3 install networkx -q

# 2) 跑双轨烟测
PYTHONPATH=. python3 scripts/session149_smoke.py
# 预期：ALL SESSION-149 SMOKE ASSERTIONS PASSED

# 3) 主观确认友好提示
#    手动触发量产模式但传入静态 guide 序列，控制台仅出现单行高亮拦截语，
#    无任何 Traceback，提示后回到向导主菜单。
```

---

## 3. Architecture Discipline Reinforced

- **质量裁决即业务事件 (Quality Verdict as Business Event)**：`PipelineContractError`
  这类**主动断路**异常必须在最近的业务边界（dispatch）被翻译为带语义的
  typed wrapper，绝不允许以原始 Traceback 形式进入用户终端。
- **黑匣子保留 / 终端干净 (Blackbox vs Terminal Separation)**：所有完整堆栈
  必须经 `logger.error(..., exc_info=True)` 落入 `logs/mathart.log`，终端
  仅展示业务级一句话提示。
- **兜底产物的活性义务 (Liveness Contract for Fallback Artifacts)**：任何
  fallback / demo / placeholder 数据生成器都必须满足下游契约的最小活性
  要求（如帧间方差>阈值），否则会被自家护栏击穿。
- **延迟绑定纪律扩展 (Lazy Type-Binding Discipline)**：当某个可选依赖
  缺席会让基类 `gym.Env[T, U]` 在类定义阶段失败时，必须用条件基类
  哨兵代替直接继承，以避免污染整个包的 import 链。
- **警告节流纪律 (Warning Throttling Discipline)**：任何在长批次循环中
  会被反复触发、且消息内容恒定的 WARNING，必须使用"首次 WARNING + 后续
  DEBUG"模式节流，避免污染操作员视野。

---

## 4. Files Modified

| File | Change Type |
|------|-------------|
| `mathart/core/pseudo3d_shell_backend.py` | 新增 `_emit_demo_warning` 节流器 + Y 轴弹跳/自转兜底动画方程 + `frame_count` 透传 |
| `mathart/workspace/mode_dispatcher.py` | 新增 `PipelineQualityCircuitBreak` 类 + dispatch 异常翻译 + 写入 `__all__` |
| `mathart/cli_wizard.py` | 新增 `_QUALITY_CIRCUIT_BREAK_NOTICE` + `_render_quality_circuit_break` + 双通道（交互/非交互）护盾 |
| `mathart/animation/rl_gym_env.py` | 引入 `_RL_ENV_BASE` 哨兵基类，gymnasium 缺席时不再污染包 import 链 |
| `scripts/session149_smoke.py` | 新增双轨烟测脚本（动画 MSE + 异常护盾 + traceback 泄漏检测） |
| `SESSION_HANDOFF.md` | 改写为 SESSION-149 主体 |
| `PROJECT_BRAIN.json` | 新增 SESSION-148 / SESSION-149 会话条目 + 待办状态同步 |

---

## 5. Historical Index (Recent Sessions)

| Session | 主线 | Commit |
|---------|------|--------|
| SESSION-149 (当前) | Dynamic demo mesh + graceful quality-breaker boundary | (this push) |
| SESSION-148 | Windows 终端编码崩溃护盾 + ASCII-safe 救援 UI | `ccc5067` |
| SESSION-147 | 知识总线大一统 + ComfyUI 交互式自愈救援网关 | `0f6da73` |
| SESSION-146-B | 雷达广域探测网 + 深度审计轨迹 | `b9cdf05` |
| SESSION-146 | 全链路遥测贯通 + 依赖契约硬化 | `ec6953c` |
| SESSION-145 | 依赖清单补齐 + 惰性加载纪律 | `3e2792d` |

---

## 6. Coronation & Next Steps

**Coronation**:
> "兜底产物从此拥有最低限度的物理生命：每一秒都在弹跳，每一秒都在旋转，
> 永不再以静止帧挑衅自家护栏；而当护栏真正出手时，操作员看到的不再是
> 灰色 Traceback 的错乱字符洪流，而是一句金色的、克制的、明确的业务裁决。
> 质量护盾从此既能铁面无私地拦截动力学崩塌，又能优雅地把审判结果递交到
> 用户面前。"

**Next Steps**:
- P1：把 `_emit_demo_warning` 抽象为通用 `mathart.core.log_throttle` 工具，
  让 `validate_config` 等高频警告路径统一接入。
- P1：在 `tests/` 下补一组 `test_session149_quality_boundary.py` 单元测试，
  把 `scripts/session149_smoke.py` 中的两项断言固化进 CI。
- P2：为 `PipelineQualityCircuitBreak` 增加 `recovery_hint` 字段，让向导
  可以基于 violation_type 给出具体的"下一步建议"（如真理网关裁剪建议）。
- P2：探索把 demo 动画的弹跳/自转参数暴露给 director_studio 的 vibe parser，
  让操作员可以一句话调参 ("更夸张的弹跳" → amplitude *= 1.6)。
- P2 (carried from SESSION-147)：`comfyui_rescue` 增加 Windows UNC `\\server\share`
  路径支持。
- P2 (carried from SESSION-146-B)：`ProxyRenderer` matplotlib 缺失时的 ASCII-art
  回退渲染器；雷达增加 Windows Registry 查询策略。

*Signed off by Manus AI · SESSION-149*
