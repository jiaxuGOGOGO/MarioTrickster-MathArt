# P1-NEW-9C 研究对齐摘要

> 任务：Character Evolution 3.0 — 在 CharacterGenotype 中扩展 part registry，新增 `torso_overlay`、`hand_item`、`foot_accessory` 三类装备槽位，并通过张量化骨骼插槽挂载器（TensorSocketMounter）将装备 mesh 严格对齐到骨骼变换上。

本文档归档三大外网工业/学术参考的关键事实，并阐明它们如何映射到本仓库的具体落地决策。

---

## 1. Unreal Engine 5 Skeletal Mesh Sockets（工业参考）

**来源**：Epic Games 官方文档 `https://dev.epicgames.com/documentation/unreal-engine/skeletal-mesh-sockets-in-unreal-engine`

**核心事实**：

> "Sockets are dedicated attach points within the hierarchy of your Skeleton, which can be transformed relative to the Bone it is parented to. Once set up, you can attach your objects, weapons, and other actors to the Socket."

UE5 Socket 的形式语义可总结为：

| 字段             | 含义                                                                 |
| ---------------- | -------------------------------------------------------------------- |
| `parent_bone`    | 该 socket 所附着的骨骼名（继承骨骼的世界变换）                       |
| `relative_location` | 相对父骨骼的局部平移（vec3）                                      |
| `relative_rotation` | 相对父骨骼的局部旋转（quat / euler）                              |
| `relative_scale` | 相对父骨骼的局部缩放（vec3）                                         |

**最终世界变换**：`M_world = M_bone_world @ M_socket_local`，其中 `M_socket_local` 由 SRT（Scale × Rotation × Translation）组装为 4×4 齐次矩阵。Epic 强调使用 socket 而非"用数学估算 offset"，本质上是把"骨骼+局部偏移"这一抽象固化为一个稳定 API 边界，从而避免每个装备各自维护魔法数字。

**对本仓库的映射**：
- `mathart/animation/skeleton.py::Skeleton` 已提供每个 joint 的世界坐标（2D pivot）。我们在此基础上引入 `TensorSocketMounter`，对每个 part 调用 `(bone_world_3d, socket_local_xyz, socket_rotation_xyz_deg) → mesh_world_4x4`。
- 局部偏移与父骨骼解耦后，装备 mesh 可以独立被装备库参数化，符合 UE5 "socket 是 bone 子节点" 的契约。

---

## 2. Inigo Quilez · Signed Distance Functions（学术 / 数学参考）

**来源**：iquilezles.org `/articles/distfunctions/`

**核心算子原文（GLSL，经摘录）**：

```glsl
// Onion shell: turn a solid into a thin shell of given thickness
float opOnion( in float sdf, in float thickness ) { return abs(sdf) - thickness; }

// Round: inflate / dilate the surface by `rad` (creates rounded armor effect)
float opRound( in sdf3d primitive, in float rad ) { return primitive(p) - rad; }

// Elongate: stretch a base SDF along axis-aligned half-extents h
float opElongate( in sdf3d primitive, in vec3 p, in vec3 h ) {
    vec3 q = p - clamp(p, -h, h);
    return primitive( q );
}
```

**关键洞见**：
- **`opRound` 即 SDF 距离场偏移**——把基础 SDF 减去一个常数 `r`，等价于把零等值面向外法线方向膨胀 `r`。这是参数化生成"贴合躯干的胸甲"的最干净方法：先取躯干胶囊体 SDF，再 `-thickness` 得到一层比原躯干稍大的 shell。
- **`opElongate` 把球体拉成胶囊体**——参数化"长剑的剑身"时，把单位球体沿 Y 轴拉伸 `h.y = blade_length/2`，即得到截面圆形、长度可控的剑刃。
- **`opOnion` 用于开口装备**（例如肩甲）：先 onion 再切除，避免实体内部的死表面浪费三角形。

**对本仓库的映射**：
- 不是真用 ray-marching 做实时 SDF（本仓库走 Mesh3D 离散化路径），而是把 IQ 的算子语义"翻译"为 Mesh3D 顶点级操作：
  - `opRound` → 沿顶点法线方向把 base mesh 的所有顶点外推 `+r`，得到一层覆盖在躯干外的胸甲（避免 Z-fighting 的关键：始终用 `r > 0`，而不是与躯干同位）。
  - `opElongate` → 取标准球网格，y 分量按 `blade_length/2` 缩放，得到剑身。
- 这套数学保证：任何参数化装备只要在生成时指定 `inflate_radius > epsilon`，就在数学上可证不会与基础 mesh 共面，从根本上消灭装备穿模与深度抖动。

---

## 3. Pixar OpenUSD Reference Composition Arc（架构参考）

**来源**：NVIDIA Learn OpenUSD `/composition-basics/references.html` + `openusd.org/release/glossary.html`

**核心事实**：

> "A reference in Universal Scene Description is a composition arc that enables the composition of prims and their descendants onto other prims – this allows us to use references to aggregate larger scenes from smaller, modular units of scene description."

USD 的 Reference 工作模式：
1. 父场景（角色）通过 `prepend references = @./sword.usd@` 引用一个外部 prim。
2. USD 先组合被引用层栈，再叠加目标 prim 上的本地 override（例如位置 `xformOp:translate = (5, 0, 0)`）。
3. 这构成 LIVRPS 强度顺序中的 "R"，允许子资产模块化复用，而父场景仅声明"挂哪儿、改哪些覆写"。

**对本仓库的映射**：
- `CharacterGenotype.slots: dict[str, PartGene]` 即"角色 Prim 上的 reference 列表"。每个 `PartGene(part_id, part_type, ...)` 不存储 mesh 几何本身，只存"指向形态库某条目的引用 + 局部覆写"。
- `compose_character_with_attachments(...)` 充当 USD 的"composition engine"：
  1. 读取基础角色（torso/head 等）；
  2. 对每个 slot，从形态库取出 `PartShape3D`（被引用资产）；
  3. 通过 `TensorSocketMounter` 把局部覆写（socket xyz + rot）应用为齐次变换，写入最终 Mesh3D 列表（== composed prim 的世界级 geometry）。
- `ArtifactManifest.references: list[str]` 字段记录上游 manifest hash，正好对应 USD 的 "Composition Arc equivalent"（已由 `mathart/core/artifact_schema.py` 显式注释为 "USD Composition Arc equivalent"）。

---

## 4. 对齐总结：三参考如何共同支撑 P1-NEW-9C

| 维度       | UE5 Socket               | IQ SDF                          | OpenUSD Reference                |
| ---------- | ------------------------ | ------------------------------- | -------------------------------- |
| 解决问题   | 挂载点的稳定 API 契约    | 装备几何的参数化生成            | 模块化资产组合 + 覆写            |
| 数学工具   | 4×4 齐次仿射矩阵         | 距离场偏移 / 拉伸算子           | LIVRPS composition arc semantics |
| 本仓库落地 | `TensorSocketMounter`    | 形态库新增 SDF 化 Mesh3D 原语  | `PartGene + compose_*` 的 hash 链 |
| 验收要点   | 包围盒严格包络父骨骼     | 装备 mesh 与父 mesh 无 Z-Fight | 旧 JSON genotype 反序列化兼容    |

任何一项缺失都会在工业落地中暴露：缺 socket → 装备漂移；缺 SDF 偏移 → 共面 Z-Fighting；缺 reference 抽象 → 无法做内容流水线扩容。三者合在一起即是 Character Evolution 3.0 的最小工业完备集。

---

## 5. 与 SESSION-118 / P1-AI-1 的衔接

- SESSION-118 已交付 `Pseudo3DShellBackend`（DQS 体积蒙皮）。本次新增的装备 mesh 直接喂给 Pseudo3D shell 时，需要在 `PartShape3D` 携带 `bind_bone_index` 字段（已在本次实现里预留），后续再由 P1-HUMAN-31A 接 SMPL latent。
- P1-AI-1（Math-to-AI Pipeline）的多级 Z-Buffer 深度缓冲：装备 mesh 由 `TensorSocketMounter` 计算的世界坐标 z 值，可直接进入正交像素渲染器的 Z-buffer 序列化，对外作为 ControlNet depth 控制图。这就是为什么本次必须在 `Mesh3D` 顶点里携带正确的世界级 z 而不是局部 z——为下游 AI 通路守住几何真值。

