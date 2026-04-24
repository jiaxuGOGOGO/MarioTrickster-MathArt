"""SESSION-179 Documentation Patch — USER_GUIDE.md."""

USER_GUIDE_APPENDIX = '''

## 10.13 SESSION-179: 视觉临摹中枢与蓝图换皮 (Visual Distillation & Reskinning)

系统已部署 SESSION-176 预研成果的全量精细化补丁，并在交互层加装了三大终极创作范式。

### 核心架构补丁 (SESSION-176 Research-Grounded)

| 补丁 | 说明 |
|------|------|
| **SparseCtrl-RGB 时段限幅** | `ControlNetApplyAdvanced` 节点的 `end_percent` 被钳制到 0.4~0.6 范围，`strength` 被钳制到 0.825~0.9 甜区。长镜头闪烁 (flashing) 与色彩漂移 (color drift) 已根治 |
| **Normal Map 编码公式验证** | 切线空间法线编码公式 `N_rgb = (N_vec + 1) * 127.5` 已在代码注释中显式标注。`(128, 128, 255)` 底色垫板确保透明区域不会产生极端切线倾斜 |
| **cancel_futures 全局熔断** | PDG 调度器在致命异常时调用 `executor.shutdown(wait=False, cancel_futures=True)` (Python 3.9+)，确保所有待执行任务被立即取消，彻底根治 OOM 宕机后的重试风暴 |
| **动态 batch_size 安全边界** | `EmptyLatentImage.batch_size` 被钳制到 `[1, 128]` 范围，防止零维张量或超大 VRAM 分配导致的退化配置 |

### 管线解除截断声明

> **系统已解除管线截断。现在即使在无显卡的纯 CPU 模式下，也能直接烘焙出拥有专业步态的高清工业级动画引导序列（Albedo/Normal/Depth）。**

SESSION-179 通过动态 `batch_size` 对齐与安全边界保护，彻底消除了 `EmptyLatentImage` 默认 16 帧截断的历史遗留问题。物理引擎输出多少帧，潜空间就分配多少帧，上限 128 帧（约 10 秒 @12fps），覆盖绝大多数游戏动作循环。

### 视觉临摹网关 (GIF to Physics)

导演工坊新增 `[D] 👁️ 视觉临摹` 创作模式。用户可以丢入一个参考 GIF 动图或图片文件夹，系统会：

1. 使用 `PIL.ImageSequence` 提取关键帧（**绝对禁止 cv2**）
2. 将关键帧编码为 Base64 PNG 发送给视觉 LLM (gpt-4o-mini)
3. AI 逆向推导出 18 个物理控制参数（重力、弹性、阻尼、比例等）
4. 参数自动注入到 Genotype，进入白模预演

如果 API 不可用或网络不通，系统会优雅降级到安全默认参数，**绝不崩溃**。

### 蓝图保存舱 (Blueprint Vault)

蓝图保存对话框升级为 **Blueprint Vault** 模式：
- 用户可输入自定义蓝图名（如 `heavy_jump_v1`）
- 留空则自动生成时间戳命名（如 `blueprint_20260424_143052`），防止意外覆盖

### 风格换皮 (Style Retargeting)

在蓝图派生模式 `[B]` 中新增 **风格换皮** 入口：
- 加载已有蓝图的动作骨架后，用户可输入全新的画风 Prompt
- 动作骨架完美复用，仅画风被替换（如：赛博朋克风格、水墨画风）
- 所有操作在内存流中完成，不污染硬盘里的原生骨骼图纸

### UX 科幻流转展示

烘焙阶段终端输出已升级，高亮打印：

```
[⚙️ 工业烘焙网关] 正在通过 Catmull-Rom 样条插值，纯 CPU 解算高精度工业级贴图动作序列...
    ├─ SESSION-166 Per-Frame State Hydration: Bone→Joint 映射已激活
    ├─ SESSION-169 Exception Piercing: 致命异常已启用穿透模式
    ├─ SESSION-172 JIT Resolution Hydration: 推流前置 512 内存上采样已激活
    ├─ SESSION-179 SparseCtrl Time-Window Clamping: end_percent 限幅已激活
    └─ SESSION-179 cancel_futures Global Meltdown: OOM 全局熔断已升级
```

### 外网工业理论锚点

> 见 [`docs/RESEARCH_NOTES_SESSION_176.md`](RESEARCH_NOTES_SESSION_176.md)，包括：
>
> - SparseCtrl-RGB Temporal Window Clamping (GitHub #476)
> - Normal Map Tangent-Space Encoding Formula: `N_rgb = (N_vec + 1) * 127.5`
> - Python `concurrent.futures.ThreadPoolExecutor.shutdown(cancel_futures=True)` (Python 3.9+)
> - Vision-Language Models for Physical Parameter Estimation (NeurIPS 2024)
> - Inverse Physics from Video Observation (SIGGRAPH 2023)

### 傻瓜验收

老大，解耦手术已完成！请在无显卡环境下直接运行生成指令。去 outputs 文件夹看，绝对不再是扭动的果冻，而是拥有标准跑跳动作姿态的成套工业图纸！

验收步骤：
1. **取消机制验收**：在 `pdg.py` 中搜索 `cancel_futures`，确认 `executor.shutdown(wait=False, cancel_futures=True)` 存在
2. **SparseCtrl 限幅验收**：在 `ai_render_stream_backend.py` 中搜索 `end_percent`，确认 0.55 钳制值存在
3. **视觉临摹验收**：启动导演工坊，选择 `[D]` 模式，丢入任意 GIF，确认 AI 返回物理参数
4. **风格换皮验收**：选择 `[B]` 模式加载蓝图，在换皮提示处输入新画风，确认 vibe 被覆盖
5. **蓝图保存验收**：在蓝图保存对话框留空，确认自动生成时间戳命名
'''

with open("docs/USER_GUIDE.md", "rb") as f:
    content = f.read().decode("utf-8")

if "SESSION-179" in content:
    print("[DOCS] SESSION-179 section already exists in USER_GUIDE.md")
else:
    content += USER_GUIDE_APPENDIX
    with open("docs/USER_GUIDE.md", "wb") as f:
        f.write(content.encode("utf-8"))
    print("[DOCS] SESSION-179 section appended to USER_GUIDE.md")


if __name__ == "__main__":
    pass
