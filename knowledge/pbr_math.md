# 物理基础渲染数学（PBR Math）

> 来源汇总：数学驱动统一美术生产深度研究报告/Manus AI、LearnOpenGL PBR Theory、Cook-Torrance BRDF/Coding Labs

## Cook-Torrance BRDF 模型（来源：数学驱动研究报告 维度二）

PBR通过统一的参数化模型描述所有材质，核心公式：
`L_o = ∫ f_r(p, ωi, ωo) * L_i * (n·ωi) dωi`

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `roughness` | 0.0-1.0 | 表面粗糙度，0=镜面，1=完全漫反射 | `mathart/sdf/renderer.py` |
| `metallic` | 0.0-1.0 | 金属度，0=非金属，1=纯金属 | `mathart/sdf/renderer.py` |
| `f0_dielectric` | 0.04 | 非金属基础反射率（菲涅尔F0） | `mathart/sdf/renderer.py` |
| `ggx_alpha` | roughness² | GGX法线分布函数的粗糙度参数 | `mathart/sdf/renderer.py` |

### 蒸馏洞察
> 这意味着：f0=0.04是所有非金属材质的通用基础反射率，这是物理常数。金属的F0来自基础颜色（albedo）。

## 2D像素画PBR降维应用（来源：数学驱动研究报告 维度二）

将3D PBR理念降维到2D像素画的实用方法。

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `normal_map_strength` | 0.5-2.0 | 法线贴图强度倍数 | `mathart/sdf/renderer.py` |
| `ambient_occlusion` | 0.1-0.5 | 环境光遮蔽强度 | `mathart/sdf/renderer.py` |
| `light_angle_deg` | -45 to 45 | 主光源角度（度），-45°≈左上角 | `mathart/animation/parts.py` |
| `rim_light_intensity` | 0.1-0.4 | 边缘光强度（增加立体感） | `mathart/animation/parts.py` |

### 蒸馏洞察
> 这意味着：即使是2D像素画，通过法线贴图+动态光源可以让同一个角色在不同场景自动呈现正确光影，无需手动调色。

## 材质预设参数表

| 材质类型 | roughness | metallic | 说明 |
|----------|-----------|----------|------|
| 皮肤 | 0.8 | 0.0 | 高漫反射，微弱次表面散射 |
| 布料 | 0.9 | 0.0 | 极高漫反射，无高光 |
| 金属盔甲 | 0.3 | 1.0 | 低粗糙度金属，强高光 |
| 木材 | 0.85 | 0.0 | 高漫反射，轻微纹理高光 |
| 石材 | 0.95 | 0.0 | 极高漫反射，几乎无高光 |
| 玻璃/水晶 | 0.05 | 0.0 | 极低粗糙度，强菲涅尔效果 |
