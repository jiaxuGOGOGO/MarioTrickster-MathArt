# 动画法则蒸馏知识

> 来源：MarioTrickster-Art/PROMPT_RECIPES + Disney 12 Principles 数学参数化（松岡/砂糖/室井/Telecom Bible/Telecom2/みにまる）

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

## コマ打ち帧数换算表（Telecom Bible）

| コマ打ち | FPS (24fps基准) | 游戏 60fps 换算 | 用途 | 代码映射 |
|----------|-----------------|-----------------|------|----------|
| 1コマ打ち | 24 fps (Full Animation) | 每帧换 1 张 = 60fps | 剧场版/高品质 | `animation/presets.py` koma_1 |
| 2コマ打ち | 12 fps (TV 标准) | 每 5 帧换 1 张 = 12fps | **TV 标准（默认）** | `animation/presets.py` koma_2 |
| 3コマ打ち | 8 fps (TV 省帧) | 每 7-8 帧换 1 张 = 8fps | 省帧/远景 | `animation/presets.py` koma_3 |

### 蒸馏洞察
> Unity AnimationClip SampleRate 应匹配コマ打ち设定。游戏默认使用 2コマ打ち = 12fps。

## 跑步周期关键帧

跑步是左右腿相位差 π 的正弦摆动。手臂与同侧腿反向（胸骨盆独立旋转）。膝关节仅在蹬地后屈曲（ROM 约束：仅后屈）。躯干有微小的 2 倍频上下颠簸。

### 跑步风格与体型法則（室井）
体型によって跑步スタイルが変化：痩せ型 = 大きなストライド + 軽い着地、筋肉型 = 力強い蹴り + 重い着地、太め型 = 小さなストライド + 上下動大。

### 蒸馏洞察
> 体型パラメータ（mass, height）から跑步の stride_length, bounce_amplitude を自動計算可能。

## 歩行周期法則（室井/砂糖）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 歩行周期帧数 | 12-16 帧 | 1歩の完全周期 | `animation/presets.py` walk_frames |
| 接地相比率 | 0.6-0.7 | 片足が地面に接している比率 | `animation/presets.py` contact_ratio |
| 踵キックバック | 踵→つま先→蹴り出し | 歩行の足運び3段階 | `animation/presets.py` heel_kickback |
| 腕振り角度 | ±15°-25° | 歩行時の腕振り範囲 | `animation/presets.py` arm_swing |
| 重心上下動 | 2-4 px | 歩行時の重心の上下移動 | `animation/presets.py` walk_bounce |

### 風力歩き表現法則（室井）
風の強さに応じた歩行変化：微風 = 髪/服のなびきのみ、強風 = 前傾 + 小さなストライド + 腕で顔を覆う。

### 蒸馏洞察
> 歩行周期は sin(t) ベースの関節角度関数で完全に数学化可能。

## 动画帧数约定

| 动作 | 推荐帧数 | 循环 | 代码映射 |
|------|----------|------|----------|
| idle | 8 | 循环 | `animation/presets.py` idle_animation |
| run | 8 | 循环 | `animation/presets.py` run_animation |
| jump | 8 | 单次 | `animation/presets.py` jump_animation |
| fall | 6 | 单次 | `animation/presets.py` fall_animation |
| hit | 4 | 单次 | `animation/presets.py` hit_animation |
| walk | 12 | 循环 | `animation/presets.py` walk_animation |
| attack | 6-8 | 单次 | `animation/presets.py` attack_animation |

## Squash & Stretch 体積保存法則（砂糖/Telecom2）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 挤压比 | sx * sy ≈ 1.0 | 体積保存の基本公式 | `animation/curves.py` squash_stretch |
| 最大拉伸 | sy ≤ 1.5 | 拉伸の上限（ゲームスプライト） | `animation/curves.py` max_stretch |
| 最大挤压 | sy ≥ 0.7 | 挤压の下限（ゲームスプライト） | `animation/curves.py` max_squash |
| 着地挤压帧数 | 2-3 帧 | 着地時の挤压持続 | `animation/presets.py` land_squash |
| 跳躍拉伸帧数 | 1-2 帧 | 跳躍時の拉伸持続 | `animation/presets.py` jump_stretch |

### 蒸馏洞察
> 体積保存は硬約束：sx * sy の偏差が 0.05 を超えたらエラー。

## 予備動作・フォロースルー法則（砂糖/Telecom）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| 予備動作比率 | 0.2-0.4 | 全動作時間に対する予備動作の比率 | `animation/presets.py` anticipation_ratio |
| フォロースルー比率 | 0.2-0.3 | 全動作時間に対するフォロースルーの比率 | `animation/presets.py` followthrough_ratio |
| 予備動作方向 | メイン動作の逆方向 | 予備動作は必ず逆方向 | `animation/presets.py` anticipation_dir |
| オーバーシュート | 1.05-1.15 | 目標位置を超えてから戻る比率 | `animation/curves.py` overshoot |

### 蒸馏洞察
> 予備動作とフォロースルーは spring 関数の stiffness/damping で自動生成可能。

## エフェクトキャラ相互作用法則（松岡エフェクトグラフィックス）

エフェクトはキャラクターの動きと連動：攻撃エフェクト = キャラの攻撃方向に追従、被弾エフェクト = ダメージ方向から発生、環境エフェクト = キャラの位置に反応。

### エフェクトタイミングリズム法則（松岡）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| エフェクト開始遅延 | 0-2 帧 | アクション開始からエフェクト開始までの遅延 | `sdf/effects.py` effect_delay |
| エフェクトピーク | 全体の 0.3-0.4 | エフェクトが最大になるタイミング | `sdf/effects.py` effect_peak |
| エフェクト消滅 | 全体の 0.7-1.0 | エフェクトが消えるタイミング | `sdf/effects.py` effect_fade |

### 蒸馏洞察
> エフェクトのタイミングはキャラアニメーションの phase に同期させる必要がある。

## VFXループアニメ法則（松岡/Telecom2）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| ループ帧数 | 4-12 帧 | VFXループの1周期帧数 | `sdf/effects.py` vfx_loop_frames |
| ループ接続 | 最終帧 → 初期帧スムーズ | ループの繋ぎ目が見えないこと | `sdf/effects.py` vfx_loop_blend |
| 発生→持続→消滅 | 3段階フロー | エフェクトのライフサイクル | `sdf/effects.py` effect_lifecycle |

### 蒸馏洞察
> VFX ループは sin/cos ベースの周期関数で自然なループを保証可能。

## キャラモデル一貫性法則（Telecom2）

### 帧間一貫性（3アンカー）
1. **頭身比** — 全フレームで一定
2. **線幅** — 全フレームで一定
3. **頭部形状** — 全フレームで一定

### batch間一貫性（4指標）
1. 頭身比
2. 線幅
3. 色調
4. 頭部形状

### 蒸馏洞察
> 一貫性チェックは自動化可能：各フレームの頭身比/線幅/色調を計測して偏差を検出。

## 安全フレームオーバーサイズ生成法則（Telecom2）

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| オーバーサイズ倍率 | 1.2x-1.5x | 最終表示解像度に対する生成倍率 | `export/bridge.py` oversize_ratio |
| パン用余白 | 表示幅の 20-50% | カメラパン時の端露出防止 | `export/bridge.py` pan_margin |
| ズーム用余白 | 表示の 1.5x | カメラズーム時のピクセル化防止 | `export/bridge.py` zoom_margin |

### 蒸馏洞察
> カメラワークがある背景/スプライトは必ずオーバーサイズで生成。等倍はカメラ固定時のみ。

## セルレイヤー分離生成法則（Telecom2）

| レイヤー | 内容 | Unity Sorting Layer | 代码映射 |
|----------|------|---------------------|----------|
| BG | 背景 | Background | `export/bridge.py` layer_bg |
| 中景 | 中間オブジェクト | Midground | `export/bridge.py` layer_mid |
| キャラ | キャラクター | Character | `export/bridge.py` layer_char |
| 前景 | 前景オブジェクト | Foreground | `export/bridge.py` layer_fg |
| エフェクト | VFX | Effects | `export/bridge.py` layer_fx |

### 蒸馏洞察
> 各レイヤーは別々の AI 生成パスで、一貫した透視を維持。Unity Sorting Layer に直接マッピング。

## フレーム宽高比テーブル（Telecom Bible）

| 规格 | 宽高比 | 数值 | 用途 | 代码映射 |
|------|--------|------|------|----------|
| SD 标准 | 4:3 | 1.33:1 | レトロゲーム | `export/bridge.py` aspect_sd |
| HD 高清 | 16:9 | 1.78:1 | **現代ゲーム（默认）** | `export/bridge.py` aspect_hd |
| 映画 | 1.85:1 | 1.85:1 | シネマティック | `export/bridge.py` aspect_cinema |

### 蒸馏洞察
> KSampler 解像度設定に直接適用。ゲームスプライトは 16:9 基準。

## 動画QAチェックリスト（Telecom Bible）

| 检查项 | 说明 | 代码映射 |
|--------|------|----------|
| フレーム数 | 予定帧数と一致 | `tests/test_animation.py` frame_count |
| モデル準拠 | キャラモデルシートと一致 | `tests/test_animation.py` on_model |
| 線質 | 線幅一定、途切れなし | `tests/test_animation.py` line_quality |
| 動線流暢度 | スムーズな動き | `tests/test_animation.py` motion_smooth |
| 口型同期 | リップシンク | 主項目音声系統 |

### 蒸馏洞察
> QA チェックリストの数値項目は自動テストとして実装可能。
