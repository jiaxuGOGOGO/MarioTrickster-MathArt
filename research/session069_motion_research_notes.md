# SESSION-069 Motion Research Notes

## Browser Findings Snapshot

### GitHub Repository Front Page

- Repository: `jiaxuGOGOGO/MarioTrickster-MathArt`
- Visible latest short commit on main: `2d5c2aa`
- Latest visible commit title on front page: `SESSION-068: High-dimensional backend code landing ...`
- Root contains `SESSION_HANDOFF.md`, `PROJECT_BRAIN.json`, `mathart/`, `tests/`, `research/` and other evolution/state files.

### PFNN Paper Viewer (`phasefunction.pdf`)

- Opened the original PDF viewer for **Phase-Functioned Neural Networks for Character Control**.
- Page 1 visually confirms the paper frames locomotion control as a **phase-conditioned continuous control problem**, not a discrete hardcoded state machine.
- Key design implication for this session: the motion core should treat phase as a continuous manifold coordinate that parameterizes motion synthesis, transition alignment, and runtime blending.
- Architectural consequence: the unified motion hub should preserve explicit phase metadata in every frame and avoid duplicate transition/gait trunks.

## Unreal Runtime Research Addendum

从 Unreal 的 **Dead Blending** 文档可直接提炼出三个运行时约束。第一，过渡并不是继续双路评估旧动画和新动画，而是利用切换瞬间记录下来的速度信息，对被切出的动画进行短时外推，并以指数衰减方式逐步消除残差。第二，衰减速率与源运动速度相对目标姿态的方向和幅值相关，因此过渡系统本质上是在衰减“残差状态”，而不是简单做线性插值。第三，控制参数采用 half-life 语义而非硬编码权重，这更适合作为统一运动核心中的物理参数契约。

从 Unreal 的 **Motion Matching** 文档可提炼出另外三个约束。其一，Motion Matching 被明确描述为一种对离散 State Machine/BlendSpace 的动态替代路径，运行时可以通过查询姿态数据库直接做姿态选择，而不需要额外拼接显式过渡图。其二，查询输入依赖骨骼位置、速度以及轨迹历史，因此统一运动主干必须保持时间序列特征的结构化、连续、可查询内存布局。其三，运动节点输出应服务于“响应式选择 + 最小过渡逻辑”，这意味着 `pipeline.py` 中的步态切换不应再分叉为多套隐式规则，而应统一收束到单一运动融合入口。
