# SESSION-047 — Gap B1 研究笔记（Jakobsen / Verlet / Distance Constraints）

## Trigger

用户明确要求：读取项目状态后，**开启研究协议**，围绕 **Gap B1: 刚柔耦合（二次动画）** 深读 **Thomas Jakobsen — Advanced Character Physics**，并将研究成果落地到项目代码、三层进化循环、待办列表、`SESSION_HANDOFF.md` 与 `PROJECT_BRAIN.json`，最终推送到 GitHub。

## Research Framing Block

| Field | Value |
|---|---|
| subsystem | 2D secondary animation / rigid-soft coupling for cape and hair |
| decision_needed | 是在现有仓库中扩展轻量 Jakobsen 风格 Verlet 链条，还是沿用更重的 XPBD / projector 路线；以及如何把它接入三层进化循环 |
| already_known | 仓库已有 `mathart/animation/particles.py` 的 Verlet 粒子经验、`physics_projector.py` 的位置空间 Verlet/PBD 投影框架、Gap C2 的三层桥接模板 |
| duplicate_forbidden | 不重复研究 Stable Fluids、motion vector、analytical SDF、已吸收的通用 Verlet 粒子入门资料；本轮只保留能直接指导 cape/hair 链条落地的 Jakobsen 系参考 |
| success_signal | 源资料必须提供：约束松弛、链条积分、锚点绑定、碰撞/阻尼/拖尾启发式、以及适配到当前 NumPy + 2D skeletal animation 架构的明确实现路径 |

## Duplicate Avoidance Boundary

- `DEDUP_REGISTRY.json` 已记录通用 `Verlet Integration for Game Physics` 参考，但**未吸收** Jakobsen 2001 的角色物理链条细节。
- `physics_projector.py` 已引用 Jakobsen，但当前公开能力更偏姿态投影与位置校正，尚未形成一个清晰、轻量、面向 **cape/hair chain attachment** 的专门子系统。
- 因此本轮研究目标不是“再学一次 Verlet 基础”，而是提炼 **Jakobsen 论文中最适合仓库新增轻量二次动画链条的机制**。

## Best New Sources by Topic

| Topic | Source type | Title | URL | Key takeaway | Novelty vs project | Integration value | Priority hint | Duplicate risk |
|---|---|---|---|---|---|---|---|---|
| theory / north star | paper | Advanced Character Physics | https://www.cs.cmu.edu/afs/cs/academic/class/15462-s13/www/lec_slides/Jakobsen.pdf | Jakobsen 原始 GDC 论文可直接深读 | 仓库仅引用未深度落地 cape/hair chain 版机制 | 最高，决定本轮实现结构 | high | low |
| implementation | repo | ClothDemo | https://github.com/davemc0/ClothDemo | 基于 Jakobsen 的交互式布料演示 | 提供链条/布料离散化与约束求解组织方式 | 可辅助校验数组布局与约束迭代方式 | high | low |
| implementation | article/pdf | Cloth Simulation on the GPU | https://http.download.nvidia.com/developer/presentations/2005/SIGGRAPH/ClothSimulationOnTheGPU.pdf | 对 Jakobsen 公式做工程化转写 | 可帮助确认 Verlet + constraint relaxation 的标准表述 | 中等，用于交叉验证 | medium | medium |
| production practice | forum/article | Set me straight on verlet integration/constraints/Jakobsen | https://gamedev.net/forums/topic/507705-set-me-straight-on-verlet-integrationconstraintsjakobsen/ | 面向围巾/腰带等角色挂件的实践问题 | 更贴近 cape/scarf 的游戏落地视角 | 若需要具体挂件实践可补充 | medium | medium |

## Recommended Next Step

立即对 `Jakobsen.pdf` 进行 **Deep Reading**，顺序提取：
1. particle state representation；
2. Verlet step；
3. distance constraint relaxation；
4. attachment / anchoring patterns；
5. damping / drag / collision heuristics；
6. 适合当前仓库的最小 2D cape-chain 变体。

## Do Not Re-Search Yet

- Stable Fluids / Jos Stam
- Motion vector / neural rendering references
- 通用 PBD / XPBD 教程（除非 Jakobsen 方案不足以支撑本轮实现）

## Deep Reading Notes — Jakobsen Paper (Part 1)

### Core mechanisms already extracted

| Mechanism | Paper detail | Direct implication for this project |
|---|---|---|
| Velocity-less state | 粒子只存 `x` 和 `x_prev`，速度由 `x - x_prev` 隐式表示 | 非常适合 2D cape/hair 链条，因为状态小、稳定性高、便于和现有 pose/UMR 输出做逐帧绑定 |
| Verlet step | `x_new = x + (x - x_prev) + a * dt^2`；将系数从 `2` 降到 `1.99` 可引入拖曳 | 项目中可以把链条阻尼建模成 `drag` 系数，而不必再额外维护复杂速度阻尼器 |
| Projection for contact | 碰撞/接触不通过 penalty springs，而是把违法位置直接投影回合法区域 | 对 2D 披风/头发，这意味着可以优先做 **边界/地面/包围盒投影**，而不必立刻上更复杂的碰撞响应 |
| Distance constraint relaxation | 对每个 stick 反复施加局部修正：`delta=x2-x1`，`diff=(|delta|-rest)/|delta|`，然后对两端按质量反比分摊修正 | 这是最关键的链条求解核心，适合直接用 NumPy 写 `solve_distance_constraints()` |
| Early stop / adaptive iterations | 局部约束迭代不必完全收敛；即使单帧只做少量迭代，系统也会在后续帧继续收敛 | 很适合本仓库：可以把 `iterations` 暴露为轻量质量/速度权衡参数，供三层进化循环自动调节 |
| Infinite mass anchors | 若某粒子应固定，只需令 `invmass = 0` | 非常适合把 cape/hair 根节点挂到 chest/head/neck 等刚体锚点上 |
| Cloth/chain as particles + constraints | 布料本质就是粒子网格 + 边约束；链条是其最小特例 | 本轮无需一开始做完整 cloth sheet，先做 **1D cape chain / hair strand chain** 就能满足用户目标 |
| Sqrt approximation | 约束修正里可用平方根近似加速 | 对当前仓库不是首要必须，但可作为后续性能优化/蒸馏规则保留 |

### Gap mapping against current repository

| Current repository state | What is missing for Gap B1 |
|---|---|
| `physics_projector.py` 已有位置空间 Verlet/PBD 表述 | 缺少专门面向 **kinematic rigid skeleton -> attached soft chain** 的轻量公开 API |
| `particles.py` 已有粒子系统经验 | 缺少“固定根锚点 + 连续距离约束 + 角色局部加速度驱动”的二次动画链条模板 |
| 三层进化循环已有 C1/C2/C3 模式 | 缺少 Gap B1 的 research record、bridge/status、审计与待办回写 |

### Provisional implementation decision

当前最合理路线不是直接扩展 XPBD，而是：

1. 新增一个**轻量 Jakobsen 风格 secondary chain 模块**；
2. 用 `anchor_position` / `anchor_velocity` / `anchor_acceleration` 驱动链条首端；
3. 为 cape 和 hair 分别提供预设；
4. 再把该模块接入 `physics_projector.py` 或 `pipeline.py` 的角色渲染/动画输出路径；
5. 最后仿照 Gap C2 的方式接入三层演化桥接与审计。

## Deep Reading Notes — Engineering Reference (ClothDemo)

| Observed point | Why it matters |
|---|---|
| 仓库 README 明确使用 **Verlet integration + multiple iterations of constraint satisfaction** | 再次验证 Jakobsen 的最小工程主线在现代 demo 中仍然成立，不需要先上 XPBD 才能得到可信二次动画 |
| 约束类型包含 **Rod / Slider / Point constraints** | 提醒本项目的首版实现不应只做 distance constraint，还应预留 root anchor / axis limit / point pin 等扩展接口 |
| 工程强调 60 fps 场景下通过提高常数与并行化获得实用性能 | 对当前仓库的启示是：先做小规模 chain（cape/hair strands），再视效果和性能决定是否扩展到 sheet cloth |
| 有 moving object collision 与 AABB collision object | 说明角色挂件系统的下一步合理路线是从简单 **capsule / AABB / silhouette proxy** 碰撞体开始，而不是一步做到完整 self-collision |

### Updated implementation stance

结合 Jakobsen 原文与 ClothDemo 工程实践，本轮实现应聚焦：

1. **一维链条优先**：先做 cape edge / hair strand 的 2D chain，而不是完整布片。
2. **固定锚点 + 外力拖尾**：根节点绑定骨骼，角色瞬时速度/加速度进入链条外力。
3. **距离约束多次松弛**：少量迭代即可得到可信二次动画，必要时可在 Layer 3 中自动调参。
4. **碰撞先做轻代理**：优先支持地面、边界、简单角色包围体或胶囊代理。
5. **接口可进化**：设计时预留 point/slider/limit 概念，便于未来从链条升级到更复杂刚柔耦合。

## Deep Reading Notes — Jakobsen Paper (Part 2)

| Mechanism | Extracted detail | Implementation implication |
|---|---|---|
| Rigid bodies as particles + constraints | 3D 刚体可由少量粒子和距离约束离散表示；3-4 次 relaxation 通常足够 | 对 2D 项目而言，骨骼端点附近的挂件无需完整连续体，可抽象为少量质点链条 |
| Collision at internal points | 碰撞点 `p` 可位于 stick 内部，并写成端点粒子的线性组合 `p = c1*x1 + c2*x2` | 若后续给 cape/hair 做 capsule/segment 碰撞，可把碰撞修正按 barycentric 权重分回相邻粒子 |
| Embedded proxy geometry | 可把外观几何嵌入约束粒子骨架中，并将碰撞点权重预计算 | 项目后续可把角色 silhouette/capsule 作为链条碰撞代理，而非直接对像素轮廓逐点求解 |
| Articulated bodies by sharing particles | 两刚体共享粒子可形成 pin joint / hinge，其他约束可继续加入 relaxation loop | 对本轮最重要的启发是：**根锚点就是和骨骼共享运动控制点**，不需要新建复杂刚体解算器 |
| Hitman simplification | 角色尸体本质上也可退化为 stick figure + angular / inequality constraints，而非完整刚体集合 | 进一步证明本项目完全可以采用“骨骼主运动 + 轻量 stick chain 次运动”的路线 |
| Self-collision as inequality distance constraints | 例如膝盖之间加入不等式距离约束防止穿插 | 对头发/披风可先实现最小间距或角色 body proxy 排斥，而不是上 full cloth self-collision |
| Environment collision as capped cylinders | stick 与三角形碰撞通过 capped cylinder 近似处理 | 对 2D 版本可对应为 **capsule / swept segment vs body proxy / ground line** |
| Motion control | 控制模拟对象时，直接移动粒子即可，系统会自然传播惯性与拖尾 | 对角色挂件最关键：根锚点随骨骼位置更新，链条其余部分通过 Verlet 自然跟随 |

### Final research conclusion for SESSION-047

对于 MarioTrickster-MathArt 当前阶段，最优落地方案已经足够明确：

1. **不直接做 XPBD 全耦合**，而是闭合用户要求的 Jakobsen 轻量方案；
2. 新增一个 `NumPy` 实现的 **2D secondary chain solver**，支持：
   - root anchor pinning；
   - Verlet integration；
   - distance constraints；
   - drag / gravity / inertia injection；
   - simple ground / body proxy collision；
   - cape / hair presets；
3. 将它作为现有 kinematic skeleton 的**外挂二次动画层**；
4. 再把性能/质量参数（segment count, iterations, drag, stiffness proxy, collision leakage）接入三层进化循环；
5. 把 XPBD 保留为更重的未来路线，但将 `P0-GAP-2` 重新表述为“先以 Jakobsen 链条闭合轻量刚柔耦合，再决定是否升级到 XPBD 双向耦合”。
