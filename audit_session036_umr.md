# SESSION-036 UMR 审计笔记

## 1. 已落地实现

本轮已将角色动画主干改造成 **Unified Motion Representation (UMR)** 数据总线。`UnifiedMotionFrame` 作为唯一共享帧合同，已覆盖 `time`、`phase`、`root_transform`、`joint_local_rotations`、`contact_tags` 五个强制字段，并通过 `UnifiedMotionClip` 承载整段状态动画。

主干 `AssetPipeline.produce_character_pack()` 已切换为：**意图状态 -> 相位/兼容基础姿态生成 -> 根运动 -> 物理顺应滤镜 -> 生物力学贴地滤镜 -> 渲染导出**。真实导出的 `session036_probe_character_manifest.json` 已验证该顺序被写入 `motion_contract.pipeline_order`，且每个状态均输出 `.umr.json` 中间表示。

`AnglePoseProjector.step_frame()` 与 `BiomechanicsProjector.step_frame()` 已成为 UMR 帧级滤镜节点，并增加 `layer_guard` 约束：下肢可被较大修正，脊柱只允许小幅调整，上肢和头部仅允许极轻量偏移，符合 AnimGraph 风格的职责边界。

`MotionFeatureExtractor.extract_umr_context()` 已提供 Layer 3 直接消费相位、接触、根运动上下文的入口，使后续 runtime motion matching、知识蒸馏和评分器不必再反向推断这些字段。

## 2. 测试与运行验证

已通过静态编译检查：`python3.11 -m compileall mathart`。

已通过回归测试：`python3.11 -m pytest -q tests/test_unified_motion.py tests/test_animation.py tests/test_physics_projector.py tests/test_character.py`，共 **73 项通过**。

已执行真实导出审计样例：`session036_probe`（状态：idle/run/jump，启用 physics + biomechanics），确认：

- 每个状态均导出 `.umr.json`
- manifest 中存在 `motion_contract`
- `motion_bus.audit.upper_body_override_flags == 0`
- `node_order == ["physics_compliance", "biomechanics_grounding"]`
- run 状态存在接触切换，jump 状态接触帧为 0，符合预期

## 3. 仍然存在的差距

1. **相位主干覆盖仍不完整**：目前 `run`/`walk` 走相位驱动，`idle`/`jump`/`fall`/`hit` 仍通过 legacy pose adapter 接入 UMR，只是被统一总线包装，尚未全部进入真正的 phase-driven trunk。
2. **CLI 干线未强制接入**：需要确认命令行入口不绕开 `produce_character_pack()` 的 UMR 主干。
3. **端到端可重复性仍需增强**：当前已有 UMR 回归测试，但缺少“从状态输入到中间表示、导出图集、审计字段”的完整零到一 reproducibility benchmark。
4. **Layer 3 仍是读取入口多于闭环优化器**：虽然评分器已能直接消费 UMR 上下文，但尚未形成“基于 UMR 的 runtime query / transition synthesis / 自动蒸馏回写”闭环。

## 4. 待办建议

- 将 `P1-PHASE-35A` 更新为 **PARTIAL**，因为 trunk enforcement 已进入 `AssetPipeline`，但 jump/fall/hit 仍未 phase-driven。
- 将 `P1-BENCH-35A` 保持 **TODO**，并在描述中加入对 `.umr.json`、manifest 审计和 trunk 节点顺序的校验要求。
- 新增一个 P1 级任务：**UMR-driven runtime query & transition synthesis**，明确把 Layer 3 从离线评估推进到基于统一运动总线的在线查询和过渡拼接。
- 新增一个 P1 级任务：**UMR contract propagation to CLI / exporters / distillation bus**，确保未来入口和导出器都不再旁路总线。
