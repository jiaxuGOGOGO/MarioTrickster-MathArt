# SESSION-077 外部参考对齐审计

作者：**Manus AI**  
日期：2026-04-19

## 审计结论

本次重新联网核对后，当前 `P1-B3-1` 落地实现与用户指定的三条外部最高准则在**核心方向上保持一致**，因此**暂不需要新增代码修复**。一致性的关键原因在于：运行时步态参数的解析已经被放在 clip / lane 进入阶段完成，而不是留在逐帧热路径中；`blend_time` 与 `phase_weight` 已经能响应外部先验知识并真实进入统一步态计算核；`UnifiedGaitBlender` 仍然作为纯数学内核运行，没有直接耦合文件 I/O 或总线实现细节，而是消费外层注入的简单标量配置。[1] [2] [3]

> “The overriding rule that makes this architecture work is *The Dependency Rule*. This rule says that *source code dependencies* can only point *inwards*.” —— Robert C. Martin, *The Clean Architecture* [3]

> “At runtime, they continuously find the frame in the mocap database that simultaneously matches the current pose and the desired future plan, and transition with a small blend time to this other place in the data.” —— GDC Vault 对 Simon Clavet 2016 演讲摘要的页面说明 [2]

## 外部原则与当前代码映射

| 外部原则 | 联网复核后的关键点 | 当前代码对应位置 | 审计判断 |
| --- | --- | --- | --- |
| **EA Frostbite / Mike Acton DOD** | 数据导向设计强调数据布局、缓存友好和减少运行时不必要间接访问；性能关键路径应尽量先整理数据，再进入计算阶段。[1] | `resolve_unified_gait_runtime_config()` 在 `mathart/animation/unified_gait_blender.py` 中一次性解析 `blend_time` 与 `phase_weight`；`UnifiedMotionBackend.execute()` 在帧循环外调用该解析器，再通过 `lane.begin_clip()` 绑定配置。 | **一致** |
| **Ubisoft Motion Matching / Clavet 2016** | 运行时系统应持续依据当前姿态与未来计划选择更优帧，并以小混合时间执行响应性过渡；这意味着转场参数本质上应服务于运行时评估，而非被永久写死。[2] | `_LocomotionLane` 在构造时把已解析的 `blend_time` / `phase_weight` 注入 `UnifiedGaitBlender`；`sample_continuous_gait()` 与 `sample_pose_at_phase()` 中，`phase_weight` 会真实参与 FFT 相位锁定与 marker warp 混合。 | **一致** |
| **Clean Architecture / Dependency Rule** | 内层不应知道外层命名实体；跨边界应传递简单数据结构，而不是把框架或 I/O 机制直接渗透进核心计算层。[3] | `UnifiedGaitBlender` 仅消费构造函数参数和轻量运行时配置，不直接解析总线、不直接做文件落盘；文件输出与 manifest 生成保留在 `UnifiedMotionBackend.execute()` 的外层边界。 | **一致** |

## 逐项核对说明

### 1. 关于热路径 O(1) 与预解析参数

重新比对后，当前实现符合“**热路径不做配置查找**”这一要求。`resolve_unified_gait_runtime_config()` 只在外层调用一次，其内部才会通过 `runtime_distillation_bus.resolve_scalar(...)` 解析别名参数。进入 `_LocomotionLane` 之后，`UnifiedGaitBlender` 持有的是已经归一化完成的 `float` 标量；后续 `sample_continuous_gait()`、`sample_pose_at_phase()` 与 `apply_transition()` 并没有再次访问总线，也没有再次执行字符串别名解析。这一点与数据导向设计对热路径数据消费方式的要求一致。[1]

更具体地说，本次复核没有发现“每帧重新查 bus / 重新做 alias 解析”的回退问题。`UnifiedMotionBackend.execute()` 在 `for i in range(frame_count)` 之前就完成了 `gait_runtime_config` 的求值，随后逐帧只复用已经绑定到 lane/core 上的数值属性。因此，就“配置消费”这一维度而言，当前实现满足你要求的 **O(1)** 热路径纪律。

### 2. 关于运行时动态响应与步态参数先验

重新比对 Clavet 2016 的公开摘要后，当前实现与“转场参数应服务于运行时响应性”这一方向保持一致。现在的 `blend_time` 不再只是默认常量，它已经能由外部蒸馏总线提供先验值，并通过 lane 初始化进入真实步态核。`phase_weight` 也不是装饰性元数据，而是在 `sample_continuous_gait()` 中参与 leader phase 与 FFT anchor 的混合，并在非 leader gait 上参与 marker warp 后的相位短弧混合。因此，蒸馏知识确实会改变运行中的相位对齐行为，而不是停留在 manifest 或 metadata 层面。[2]

结合现有 E2E 回归测试，这个注入路径已经有“**极端参数会改变输出**”的证据保护。也就是说，本次联网复核后，我没有发现新的架构性偏差要求再去返工这条路径。

### 3. 关于依赖倒置与纯净数学内核

重新对照 Clean Architecture 原文后，当前边界划分也是合理的。`UnifiedGaitBlender` 内部没有直接触碰文件系统，也没有直接依赖 `RuntimeDistillationBus` 的具体实现；它接收的是已解析后的简单参数。真正负责调用总线、组织上下文、生成文件、保存 manifest 的仍然是外层 `UnifiedMotionBackend`。这使得控制流可以从外层进入核心数值核，但源码依赖方向仍然保持“外层依赖内层”的格局，与 Dependency Rule 的基本精神一致。[3]

此外，`TransitionSynthesizer.get_transition_quality()` 当前只是对统一核心的兼容出口，而非额外引入一个平行实现。这种“兼容外观层 + 单一真实计算核”的做法也没有破坏依赖边界，反而降低了历史兼容成本。

## 是否需要代码调整

本次联网复核后的结论是：**当前无需新增代码修复**。我没有发现与三条外部参考相冲突、且足以推翻现有实现方向的设计问题。现有代码在三点上已经成立：其一，参数解析发生在热路径外；其二，蒸馏参数会真实影响运行时步态相位计算；其三，数值核仍保持纯净注入边界。[1] [2] [3]

因此，后续动作应以**验证与留痕**为主，而不是无意义地重写已经对齐的路径。若后面要进一步增强这条链路，更值得继续投入的方向是：把更多认知科学或批量调参知识也同样预编译成 clip 级标量配置，再按同样方式注入统一运动核，而不是把新逻辑重新塞回每帧循环中。

## References

[1]: https://dataorienteddesign.com/site.php "Data-Oriented Design Resources"
[2]: https://www.gdcvault.com/play/1023280/Motion-Matching-and-The-Road "Motion Matching and The Road to Next-Gen Animation"
[3]: https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html "The Clean Architecture"
