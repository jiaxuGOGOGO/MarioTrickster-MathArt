# 视觉特效设计

> 来源：MarioTrickster-Art/PROMPT_RECIPES（松岡エフェクトグラフィックス/吉田誠治/Telecom Bible/Telecom2）

## 粒子系统基础

粒子系统是游戏特效的数学核心，每个粒子由位置、速度、生命周期定义。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 粒子生命周期 | 0.1-3.0s | 单个粒子从生成到消亡的时间 | `sdf/effects.py` particle_lifetime |
| 发射速率 | 5-100/s | 每秒生成的粒子数 | `sdf/effects.py` emit_rate |
| 初始速度范围 | 1-20 px/frame | 粒子初始速度 | `sdf/effects.py` initial_velocity |
| 重力影响 | 0.0-2.0 | 粒子受重力影响的系数 | `sdf/effects.py` gravity_scale |

### 蒸馏洞察
> 粒子系统的每个参数都是纯数学的，完美契合数学驱动的理念。

## 像素特效模式

像素游戏中的特效需要在有限分辨率下表现力。

| 特效类型 | 帧数 | 扩散半径 | 颜色变化 | 代码映射 |
|----------|------|----------|----------|----------|
| 跳跃烟尘 | 3-5 | 2-4 px | 白→灰→透明 | `sdf/effects.py` dust_preset |
| 着地冲击 | 4-6 | 3-6 px | 白→黄→透明 | `sdf/effects.py` impact_preset |
| 受击闪烁 | 2-3 | 0 px | 全白→原色 | `animation/presets.py` hit_flash |
| 收集闪光 | 4-8 | 4-8 px | 黄→白→透明 | `sdf/effects.py` collect_preset |

### 蒸馏洞察
> 每种特效都可以参数化为"帧数×扩散×颜色曲线"的三维空间，用数学生成而非手绘。

## 屏幕震动

屏幕震动是游戏手感（Game Feel）的重要组成部分。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 震动幅度 | 1-8 px | 最大偏移像素数 | 主项目相机系统 |
| 震动频率 | 20-60 Hz | 震动的频率 | 主项目相机系统 |
| 衰减时间 | 0.1-0.5s | 震动从最大到静止的时间 | 主项目相机系统 |
| 衰减曲线 | 指数/线性 | 震动衰减的数学曲线 | 主项目相机系统 |

### 蒸馏洞察
> 屏幕震动的数学模型是阻尼振荡：`A * e^(-bt) * sin(ωt)`，与弹簧阻尼系统同构。

## エフェクト基本形状5分類法則（松岡エフェクトグラフィックス）

| 形状タイプ | 代表エフェクト | SDF 生成方法 | 代码映射 |
|------------|----------------|--------------|----------|
| ライン形 | ビーム/雷/集中線 | 線分SDF + ノイズ | `sdf/effects.py` line_effect |
| 面形 | 爆発/衝撃波 | 円/楕円SDF + 拡散 | `sdf/effects.py` area_effect |
| 粒子形 | 火花/星/破片 | 点群SDF + 物理 | `sdf/effects.py` particle_effect |
| 体積形 | 煙/雲/炎 | ノイズSDF + 密度 | `sdf/effects.py` volume_effect |
| 複合形 | 魔法/必殺技 | 上記の組み合わせ | `sdf/effects.py` composite_effect |

### 蒸馏洞察
> 全エフェクトは 5 つの基本形状の組み合わせで構成可能。SDF の布尔演算で合成。

## エフェクト発生消滅フロー法則（松岡エフェクトグラフィックス）

| フェーズ | 時間比率 | 特性 | 代码映射 |
|----------|----------|------|----------|
| 発生 (Birth) | 0.0-0.2 | 急速に拡大、高輝度 | `sdf/effects.py` effect_birth |
| 成長 (Growth) | 0.2-0.4 | 最大サイズへ、ピーク輝度 | `sdf/effects.py` effect_growth |
| 持続 (Sustain) | 0.4-0.7 | サイズ維持、輝度微減 | `sdf/effects.py` effect_sustain |
| 消滅 (Decay) | 0.7-1.0 | 縮小+透明化、輝度急減 | `sdf/effects.py` effect_decay |

### 蒸馏洞察
> エフェクトのライフサイクルは ADSR エンベロープと同構造。4 パラメータで全フェーズを制御。

## 炎エフェクト法則（松岡エフェクトグラフィックス）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 炎の基本形状 | 不規則+底広先細 | 炎は底が広く先が細い | `sdf/effects.py` flame_shape |
| S字揺らめき | sin(t) + noise | 炎はS字カーブで揺らめく | `sdf/effects.py` flame_flicker |
| 風の影響 | 風方向に傾く | 風速に応じて炎が傾く | `sdf/effects.py` flame_wind |
| 重力の影響 | 上方向 | 炎は常に上向き（熱対流） | `sdf/effects.py` flame_gravity |

### 蒸馏洞察
> 炎の S 字揺らめきは `sin(y * freq + t) * amplitude` で数学的に生成。flame_sdf に既に実装。

## 爆発花型シルエット法則（松岡エフェクトグラフィックス）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 花弁数 | 5-8 | 爆発シルエットの花弁数 | `sdf/effects.py` explosion_petals |
| 花弁角度 | 360°/花弁数 | 等間隔配置 | `sdf/effects.py` explosion_angle |
| 花弁サイズ変動 | ±20% | 不規則さを加える | `sdf/effects.py` explosion_variance |
| 中心空洞 | 半径の 30-50% | 爆発中心は空洞 | `sdf/effects.py` explosion_hollow |

### 蒸馏洞察
> 爆発シルエットは star_sdf の花弁パラメータで直接生成可能。

## 雷・放電描画法則（松岡エフェクトグラフィックス）【核心規則】

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| ジグザグ角度 | 15°-45° | 雷の折れ曲がり角度 | `sdf/effects.py` lightning_zigzag |
| 分岐確率 | 0.2-0.4 | 各ノードでの分岐確率 | `sdf/effects.py` lightning_branch_prob |
| 線幅変化 | 幹=3px, 枝=1px | 幹から枝へ線幅が減少 | `sdf/effects.py` lightning_width |
| 遠近感 | 手前太・奥細 | 深度に応じた線幅変化 | `sdf/effects.py` lightning_depth |

### 蒸馏洞察
> 雷は electric_arc_sdf のセグメント数とランダム角度で既に実装。分岐を追加すれば完全対応。

## ビーム描画法則（松岡エフェクトグラフィックス）【核心規則】

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| ビーム基本幅 | 発射点=細、先端=太 | 徐々に太くなる | `sdf/effects.py` beam_width |
| 波状線スピード | sin(x * freq) | メインラインの波状表現 | `sdf/effects.py` beam_wave |
| リング数 | 3-8 | ビームを貫通するリング | `sdf/effects.py` beam_rings |
| バリエーション | ジグザグ/拡散/ねじれ/不規則 | ビームの形状変化 | `sdf/effects.py` beam_variant |

### 蒸馏洞察
> ビームは線分 SDF + sin 変調 + リング SDF の合成で生成可能。

## 魔法エフェクト描画法則（松岡エフェクトグラフィックス）

| 要素 | 形状 | SDF 生成方法 | 代码映射 |
|------|------|--------------|----------|
| 魔法陣 | 同心円+四角形 | ring_sdf + box_sdf | `sdf/effects.py` magic_circle |
| 渦 | 螺旋 | 極座標変換 + 回転 | `sdf/effects.py` magic_vortex |
| エネルギー放出 | 中心点から放射 | 粒子群 + 放射速度 | `sdf/effects.py` magic_energy |

### 蒸馏洞察
> 魔法陣は ring_sdf と box_sdf の布尔演算で直接生成可能。

## 破砕の材質別飛散法則（松岡エフェクトグラフィックス）

| 材質 | 破片形状 | 飛距離 | 落下速度 | 代码映射 |
|------|----------|--------|----------|----------|
| 石/コンクリート | 不規則多角形 | 短 | 速い | `sdf/effects.py` debris_stone |
| 木 | 細長い破片 | 中 | 中 | `sdf/effects.py` debris_wood |
| ガラス | 鋭い三角形 | 長 | 遅い（軽い） | `sdf/effects.py` debris_glass |
| 金属 | 歪んだ板状 | 短 | 速い（重い） | `sdf/effects.py` debris_metal |

### 蒸馏洞察
> 破片の飛散は質量(m)と初速(v0)から放物線軌道を計算：`y = v0y*t - 0.5*g*t^2`。

## 水しぶき飛散軌跡法則（松岡エフェクトグラフィックス）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 水滴数 | 10-30 | 飛散する水滴の数 | `sdf/effects.py` splash_count |
| 水滴サイズ | 1-4 px | 水滴の大きさ | `sdf/effects.py` splash_size |
| 飛散角度 | 30°-150° | 水面からの飛散角度範囲 | `sdf/effects.py` splash_angle |
| 波紋同心円 | 3-5 本 | 水面の波紋の同心円数 | `sdf/effects.py` ripple_count |

### 蒸馏洞察
> 水しぶきは circle_sdf の粒子群 + 放物線軌道で生成。波紋は ring_sdf のアニメーション。

## 風の不可視表現法則（松岡エフェクトグラフィックス）

風は不可視 → 物体の動きと環境変化で表現。

| 風の強さ | 表現方法 | パラメータ | 代码映射 |
|----------|----------|------------|----------|
| 微風 | 髪/服のなびき | flutter_amplitude = 2-5° | `animation/presets.py` wind_flutter |
| 強風 | なびき+飛散物 | flutter_amplitude = 15-30° | `animation/presets.py` wind_strong |
| 竜巻 | 回転+螺旋+破片 | rotation_speed = 360°/s | `sdf/effects.py` tornado |

### 蒸馏洞察
> 風の表現は spring 関数の外力項として実装：`F_wind = wind_strength * sin(t * freq)`。

## エフェクトスケール感法則（松岡エフェクトグラフィックス）

| スケール | エフェクトサイズ | 速度 | 代码映射 |
|----------|------------------|------|----------|
| 小規模（手榴弾） | キャラの 1-2 倍 | 速い | `sdf/effects.py` scale_small |
| 中規模（魔法） | キャラの 3-5 倍 | 中 | `sdf/effects.py` scale_medium |
| 大規模（核爆発） | 画面全体 | 遅い（巨大感） | `sdf/effects.py` scale_large |

### 蒸馏洞察
> スケール感はエフェクトの速度と反比例：大きいエフェクトほどゆっくり動く。

## エフェクトレイヤー分離法則（松岡/Telecom2）

| レイヤー | 内容 | 描画順序 | 代码映射 |
|----------|------|----------|----------|
| 背景エフェクト | 環境光、霧、遠景パーティクル | 最背面 | `sdf/effects.py` layer_bg_fx |
| キャラ背面エフェクト | オーラ、影 | キャラの後ろ | `sdf/effects.py` layer_behind_char |
| キャラ前面エフェクト | 攻撃エフェクト、ヒットスパーク | キャラの前 | `sdf/effects.py` layer_front_char |
| 最前面エフェクト | フラッシュ、画面エフェクト | 最前面 | `sdf/effects.py` layer_screen_fx |

### 蒸馏洞察
> エフェクトレイヤーは Unity Sorting Layer に直接マッピング。export/bridge.py と連携。

## ゲーム内実装優先度テーブル（松岡エフェクトグラフィックス）

| 優先度 | エフェクト | 理由 |
|--------|------------|------|
| P0 (必須) | ヒットスパーク、着地煙塵、収集閃光 | ゲームプレイに直結 |
| P1 (重要) | 炎、爆発、雷 | 敵/トラップの視認性 |
| P2 (演出) | 魔法陣、ビーム、竜巻 | ボス戦/特殊演出 |
| P3 (環境) | 霧、風、水面反射 | 雰囲気作り |

### 蒸馏洞察
> P0 エフェクトは sdf/effects.py に最優先で実装。P1 以降は段階的に追加。

## 星々キラキラエフェクト法則（松岡エフェクトグラフィックス）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 星の線数 | 4-6 本 | 十字 or 六芒星 | `sdf/effects.py` star_lines |
| 線の長さ | 2-8 px | キラキラの大きさ | `sdf/effects.py` star_size |
| 点滅周期 | 0.5-2.0s | キラキラの点滅速度 | `sdf/effects.py` star_blink |
| 輝度変化 | sin(t) | 正弦波で明滅 | `sdf/effects.py` star_brightness |

### 蒸馏洞察
> 星キラキラは star_sdf + glow_sdf の組み合わせで生成。点滅は sin(t) で制御。

## F.I./F.O.転場効果法則（Telecom Bible）

| 効果 | 方向 | 持続帧数 | 代码映射 |
|------|------|----------|----------|
| フェードイン | 黒→画面 | 12-24 帧 | `sdf/effects.py` fade_in |
| フェードアウト | 画面→黒 | 12-24 帧 | `sdf/effects.py` fade_out |
| ホワイトアウト | 画面→白 | 6-12 帧 | `sdf/effects.py` white_out |

### 蒸馏洞察
> フェードは alpha 値の線形/イーズ補間で実装。ホワイトアウトは明度の補間。
