# 动画法则蒸馏知识

> 来源：MarioTrickster-Art/PROMPT_RECIPES + Disney 12 Principles 数学参数化

## 12 法则 → 数学映射

| 法则 | 数学实现 | 代码位置 |
|------|----------|----------|
| 挤压拉伸 | `squash_stretch(t)` → (sx, sy) 其中 sx*sy≈1（体积守恒） | `curves.py` |
| 预备动作 | jump 动画 t=0-0.3 阶段的反向蓄力 | `presets.py` |
| 弧线运动 | 关节旋转产生的自然弧线轨迹 | `skeleton.py` FK |
| 慢入慢出 | `ease_in_out(t, power)` 幂函数缓动 | `curves.py` |
| 跟随与重叠 | `spring(t, stiffness, damping)` 阻尼弹簧 | `curves.py` |
| 次要动作 | idle 动画中的呼吸+手臂摆动 | `presets.py` |
| 弹跳 | `bounce(t, bounces)` 多次弹跳衰减 | `curves.py` |

## 跑步周期关键帧

跑步是左右腿相位差 π 的正弦摆动。手臂与同侧腿反向（胸骨盆独立旋转）。膝关节仅在蹬地后屈曲（ROM 约束：仅后屈）。躯干有微小的 2 倍频上下颠簸。

## 动画帧数约定

| 动作 | 推荐帧数 | 循环 |
|------|----------|------|
| idle | 8 | 循环 |
| run | 8 | 循环 |
| jump | 8 | 单次 |
| fall | 6 | 单次 |
| hit | 4 | 单次 |
