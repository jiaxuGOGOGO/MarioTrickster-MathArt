# SESSION_TRACKER — MarioTrickster-MathArt

> 本文件是 AI 接手本项目的第一读档点。

## 当前状态

| 维度 | 状态 |
|------|------|
| 版本 | v0.1.0 — 初始搭建 |
| 测试 | 58 passed, 0 failed |
| 模块 | OKLAB ✅ · SDF ✅ · Animation ✅ · Export ✅ |
| CI | GitHub Actions 已配置 |
| 蒸馏知识 | 从 MarioTrickster-Art/PROMPT_RECIPES 迁移完成 |

## 架构速览

```
mathart/
  oklab/        → 色彩空间转换 + 调色板生成 + 图像量化
  sdf/          → 2D SDF 图元 + 布尔运算 + 游戏特效 + 渲染器
  animation/    → 骨骼系统 + 动画曲线 + 预设动作 + Sprite Sheet 渲染
  export/       → Unity 约束校验 + 元数据生成 + 批量导出
knowledge/      → 蒸馏知识文档（解剖/透视/色彩/动画/Unity规则）
tests/          → 58 个测试（unit + integration）
```

## 防坑警告

1. **OKLAB 色域钳制**：高饱和度颜色可能超出 sRGB 色域，`PaletteGenerator._gamut_clamp()` 会自动降低 chroma。如果生成的颜色看起来灰暗，检查 chroma 参数是否过高。
2. **SDF 轮廓宽度**：`outline_width` 在归一化坐标系下，0.03 ≈ 1 像素（32px 精灵）。过大会吃掉填充区域。
3. **骨骼 FK**：当前 `forward_kinematics()` 是简化版，仅处理旋转不处理平移偏移。复杂姿态可能需要完善。
4. **导出校验**：`AssetExporter` 强制要求 RGBA 模式，RGB 图片会被拒绝。

## Session 日志

| # | 日期 | 内容 |
|---|------|------|
| 1 | 2026-04-14 | 项目初始搭建：4 模块 + 58 测试 + CI + 蒸馏知识迁移 |
