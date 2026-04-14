# Unity 硬约束蒸馏知识

> 来源：MarioTrickster 主仓库 TA_AssetValidator.cs / AI_SpriteSlicer.cs / LevelThemeProfile.cs

## 图片导入约束

| 参数 | 值 | 强制方式 |
|------|-----|----------|
| PPU (Pixels Per Unit) | 32 | TA_AssetValidator 自动校正 |
| Filter Mode | Point (no filter) | TA_AssetValidator 自动校正 |
| Alpha Is Transparency | true | TA_AssetValidator 自动校正 |
| 格式 | PNG, RGBA | AssetExporter 校验 |

## Pivot 约束

| 资产类型 | Pivot | 数值 (x, y) |
|----------|-------|-------------|
| 角色 (Characters) | Bottom Center | (0.5, 0.0) |
| 敌人 (Enemies) | Bottom Center | (0.5, 0.0) |
| 特效 (VFX) | Center | (0.5, 0.5) |
| 地形 (Environment) | Center | (0.5, 0.5) |
| 陷阱 (Hazards) | Center | (0.5, 0.5) |

## 命名约定

```
{name}_{variant}_v{version:02d}.png          # 静态精灵
{name}_{variant}_sheet_v{version:02d}.png     # 动画 Sprite Sheet
```

## 目录结构

```
Assets/Art/{Style}/{Category}/
  Style = Style_MathArt
  Category = Characters | Enemies | Environment | Hazards | VFX | UI
```

## LevelThemeProfile 插槽

基础精灵：`groundSprite`, `platformSprite`, `wallSprite`

角色精灵：`marioSprite`, `tricksterSprite`

元素精灵（18 个）：SpikeTrap, FireTrap, PendulumTrap, BouncingEnemy, BouncyPlatform, CollapsingPlatform, OneWayPlatform, MovingPlatform, HiddenPassage, FakeWall, GoalZone, Collectible, SimpleEnemy, SawBlade, FlyingEnemy, ConveyorBelt, Checkpoint, BreakableBlock

## 地形拼接规则

基础地面必须为 32x32 像素。平台必须水平方向无缝拼接。
