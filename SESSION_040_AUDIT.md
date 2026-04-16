# SESSION-040 全面审计清单

## 攻坚战役三：CLI 管线契约与端到端防绕过

### 1. 数据驱动暴政与强契约 (DOD & Contract Enforcement)

| 审计项 | 要求 | 实现文件 | 状态 |
|--------|------|----------|------|
| UMR_Context 不可变数据类 | frozen=True dataclass | `mathart/pipeline_contract.py` | ✅ |
| UMR_Context 确定性哈希 | 相同参数产生相同 context_hash | `pipeline_contract.py:UMR_Context.context_hash` | ✅ |
| UMR_Context.from_character_spec | 从 CharacterSpec 桥接 | `pipeline_contract.py` | ✅ |
| PipelineContractError | 自定义异常类 | `pipeline_contract.py` | ✅ |
| PipelineContractGuard | 运行时契约守卫 | `pipeline_contract.py` | ✅ |
| 斩断旁路：CLI 主入口只接收 UMR_Context | produce_character_pack 集成 | `mathart/pipeline.py:1823-1836` | ✅ |
| 斩断旁路：legacy_pose_adapter 路径消除 | idle 状态 phase-driven | `mathart/animation/phase_driven_idle.py` | ✅ |
| Fail-Fast 抛出 PipelineContractError | 未知状态/旧路径触发 | `pipeline.py:1506-1598` | ✅ |
| CLI 重写斩断旁路 | animation/cli.py 强制 UMR | `mathart/animation/cli.py` | ✅ |

### 2. 端到端确定性与哈希封印 (End-to-End Determinism & Golden Master)

| 审计项 | 要求 | 实现文件 | 状态 |
|--------|------|----------|------|
| UMR_Auditor 节点 | 管线末端审计器 | `mathart/pipeline_auditor.py` | ✅ |
| SHA-256 哈希计算 | 帧坐标+Contact Tags+渲染配置 | `pipeline_auditor.py:UMR_Auditor.seal()` | ✅ |
| 确定性验证 | 相同输入→相同哈希 | `tests/test_pipeline_contract.py::test_deterministic_hash_across_runs` | ✅ |
| .umr_manifest.json 输出 | 哈希封印写入文件 | `pipeline_auditor.py:save_manifest()` | ✅ |
| ManifestSeal 数据结构 | pipeline_hash + state_hashes + contact_tag_hash | `pipeline_auditor.py:ManifestSeal` | ✅ |
| Golden Master 验证 | verify_against() 方法 | `pipeline_auditor.py:verify_against()` | ✅ |
| Contact Tags 哈希 | 独立校验接触点 | `pipeline_auditor.py:contact_tag_hash` | ✅ |
| ContactFlickerDetector | 非法高频闪烁检测 | `pipeline_auditor.py:ContactFlickerDetector` | ✅ |
| CI 拦截能力 | 哈希变动触发 PipelineContractError | `pipeline_auditor.py:verify_against()` | ✅ |

### 3. 三层进化循环 (Three-Layer Evolution Cycle)

| 审计项 | 要求 | 实现文件 | 状态 |
|--------|------|----------|------|
| Layer 1 集成 | 契约验证纳入内循环 | `evolution/engine.py:evaluate_contract()` | ✅ |
| Layer 2 集成 | 知识蒸馏自动生成规则 | `evolution_contract_bridge.py:distill_contract_knowledge()` | ✅ |
| Layer 3 集成 | 契约合规作为适应度维度 | `evolution_contract_bridge.py:compute_contract_fitness_bonus()` | ✅ |
| ContractEvolutionBridge | 三层桥接模块 | `mathart/evolution/evolution_contract_bridge.py` | ✅ |
| 持久化状态 | .contract_evolution_state.json | `evolution_contract_bridge.py:_save_state()` | ✅ |
| Golden Master 追踪 | 跨周期哈希稳定性 | `ContractEvolutionState.hash_stability_streak` | ✅ |
| 知识规则自动生成 | pipeline_contract.md | `evolution_contract_bridge.py:_save_knowledge_rules()` | ✅ |
| Engine 状态报告 | status() 显示契约状态 | `evolution/engine.py:status()` | ✅ |
| Brain 持久化 | 契约状态写入 PROJECT_BRAIN | `evolution/engine.py:_update_brain()` | ✅ |

### 4. 测试覆盖

| 测试文件 | 测试数 | 状态 |
|----------|--------|------|
| tests/test_pipeline_contract.py | 27 | ✅ 全部通过 |
| tests/test_character_pipeline.py (回归) | 6 | ✅ 全部通过 |
| 全部非 scipy 测试 | 717 | ✅ 全部通过 |

### 5. 参考文献落地对照

| 参考 | 核心理念 | 落地位置 |
|------|----------|----------|
| Mike Acton CppCon 2014 DOD | 数据不可变、面向数据设计 | UMR_Context frozen dataclass |
| Pixar USD Schema Validation | Schema 验证 + CI 机制 | PipelineContractGuard + UMR_Auditor |
| Glenn Fiedler 确定性锁步 | 确定性物理、哈希校验 | SHA-256 hash seal + verify_against |
| Gaffer on Games | 相同输入→相同输出 | test_deterministic_hash_across_runs |
