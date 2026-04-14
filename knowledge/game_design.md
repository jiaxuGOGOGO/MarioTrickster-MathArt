# 游戏设计与机制

> 来源：MarioTrickster-Art/PROMPT_RECIPES + 乘法式设计 + 心流理论 + MarioTrickster 主项目

## 核心循环

游戏的核心循环定义了玩家在每一秒、每一分钟、每一小时内重复的行为模式。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 核心动词数量 | 2-5 | 核心循环中的动作种类数 | `level/wfc.py` 元素多样性约束 |
| 反馈延迟 | 0-500ms | 玩家操作到视觉反馈的延迟 | `animation/presets.py` 响应帧数 |

### 蒸馏洞察
> 核心循环决定了关卡中需要哪些元素类型，直接影响 WFC 的元素权重分布。

## 难度曲线

难度曲线描述了游戏挑战随时间的变化趋势。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 难度增长率 | 0.05-0.15 | 每关难度增长系数 | `level/wfc.py` 约束密度 |
| 休息关比例 | 0.15-0.25 | 低难度关卡占比 | `level/wfc.py` 空白区域比例 |
| 教学关数量 | 1-3 | 引入新机制的教学关卡数 | `level/templates.py` 教学片段 |

### 蒸馏洞察
> 难度曲线可以数学化为 WFC 中"危险元素密度"随关卡序号的函数。

## 乘法式设计

乘法式设计指游戏系统之间的交互产生大于各部分之和的涌现玩法。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 系统交互数 | 3-8 | 可以相互作用的游戏系统数量 | `level/templates.py` 元素组合规则 |
| 涌现比 | 2.0-10.0 | 涌现玩法数 / 设计玩法数 | 评估指标 |

### 蒸馏洞察
> 乘法式设计意味着 WFC 的邻接规则不应只有"允许/禁止"，还应有"组合增益"权重。

## 心流理论

心流是玩家在挑战与技能匹配时进入的最佳体验状态。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 挑战/技能比 | 0.8-1.2 | 心流区间的挑战与技能比值 | `level/wfc.py` 难度校准 |
| 心流持续时间 | 30-300s | 单次心流状态的目标持续时间 | 关卡长度设计 |

### 蒸馏洞察
> 心流理论为关卡长度和难度波动提供了数学约束。

## 关卡节奏

关卡节奏是紧张与放松交替的时间模式。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 紧张段长度 | 3-8 格 | 连续危险区域的格子数 | `level/wfc.py` 连续约束 |
| 放松段长度 | 2-5 格 | 安全区域的格子数 | `level/wfc.py` 空白约束 |
| 紧张/放松比 | 1.5-3.0 | 紧张段与放松段的长度比 | `level/wfc.py` 节奏参数 |

### 蒸馏洞察
> 关卡节奏可以编码为 WFC 的序列约束：连续 N 个危险格子后必须有 M 个安全格子。

## 实体蓝图库（PROMPT_RECIPES §实体蓝图库）

以下实体的视觉特征词用于 AI 生成和 SDF 预设参考。

### 角色与敌人

| 实体名称 | 视觉特征词 | SDF 预设 | 代码映射 |
|----------|------------|----------|----------|
| Mario（主角） | red cap, blue overalls | 角色 SDF 组合 | `animation/character_presets.py` mario |
| Trickster（幽灵形态） | purple ethereal glow, semi-transparent | 半透明 SDF | `animation/character_presets.py` trickster |
| SimpleEnemy（基础巡逻怪） | cute slime, green color | 球形 SDF | `sdf/primitives.py` circle_sdf |
| BouncingEnemy（弹跳怪） | spring-loaded robot, metallic | 矩形+弹簧 SDF | `sdf/effects.py` bounce_enemy |
| FlyingEnemy（飞行怪） | bat creature, dark purple, flapping wings | 三角形+翅膀 SDF | `sdf/effects.py` fly_enemy |

### 地形与平台

| 实体名称 | 视觉特征词 | 尺寸约束 | 代码映射 |
|----------|------------|----------|----------|
| Ground / Wall | stone texture, mossy | 32x32 tile | `level/wfc.py` ground_tile |
| Platform（普通平台） | wooden planks texture | 宽 ≥ 64px | `level/wfc.py` platform_tile |
| OneWayPlatform | thin platform, metal grating | 高 ≤ 8px | `level/wfc.py` oneway_tile |
| BouncyPlatform | bouncy mushroom, rubbery | 32x32 | `level/wfc.py` bouncy_tile |
| CollapsingPlatform | cracked stone, crumbling | 32x32 + 崩塌动画 | `level/wfc.py` collapse_tile |
| MovingPlatform | mechanical, yellow caution stripes | 32x32 + 移动轨迹 | `level/wfc.py` moving_tile |
| ConveyorBelt | conveyor belt, industrial | 32x32 + 方向指示 | `level/wfc.py` conveyor_tile |
| BreakableBlock | fragile brick, cracked terracotta | 32x32 + 破坏动画 | `level/wfc.py` breakable_tile |
| FakeWall | illusion wall, slightly faded stone | 32x32 (外观=普通墙) | `level/wfc.py` fake_wall_tile |

### 陷阱与机关

| 实体名称 | 视觉特征词 | SDF 预设 | 代码映射 |
|----------|------------|----------|----------|
| SpikeTrap（地刺） | sharp metal spikes, rusty | spike_sdf | `sdf/effects.py` spike_sdf |
| FireTrap（火焰陷阱） | roaring campfire, orange yellow | flame_sdf | `sdf/effects.py` flame_sdf |
| PendulumTrap（摆锤） | heavy spiked iron ball, dark metal | circle + chain SDF | `sdf/effects.py` pendulum_sdf |
| SawBlade（旋转锯片） | circular saw blade, spinning, silver | saw_blade_sdf | `sdf/effects.py` saw_blade_sdf |

### 交互物与 UI

| 实体名称 | 视觉特征词 | SDF 预设 | 代码映射 |
|----------|------------|----------|----------|
| Collectible（金币） | shiny gold coin, bright yellow | circle + glow | `sdf/effects.py` glow_sdf |
| Checkpoint（旗帜） | red checkpoint flag on pole | 矩形+三角形 | `sdf/primitives.py` flag_sdf |
| GoalZone（终点门） | ornate wooden door, magical aura | 矩形+光环 | `sdf/effects.py` goal_sdf |
| HiddenPassage（隐藏通道） | dark cave entrance, stone archway | 半圆+暗色 | `sdf/primitives.py` arch_sdf |

### 蒸馏洞察
> 实体蓝图库的视觉特征词是 AI 生成 prompt 的核心。SDF 预设提供程序化备选方案。两者互补：AI 生成高品质资产，SDF 生成占位符和变体。
