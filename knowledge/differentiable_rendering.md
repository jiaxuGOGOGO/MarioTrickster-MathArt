# 可微渲染（Differentiable Rendering）

> 来源汇总：数学驱动统一美术生产深度研究报告/Manus AI、DiffVG/Li et al. 2020、nvdiffrast/Laine et al. 2020

## 可微渲染基础（来源：数学驱动研究报告 维度七）

可微渲染使渲染过程对参数可求导，从而通过梯度下降优化美术参数。

| 概念 | 说明 | 代码映射 |
|------|------|----------|
| 前向渲染 | 参数→图像的正向计算 | `mathart/evolution/diff_render.py` |
| 反向传播 | 图像损失→参数梯度的逆向计算 | `mathart/evolution/diff_render.py` |
| 感知损失 | 用VGG特征空间计算图像相似度 | `mathart/evolution/diff_render.py` |
| 风格损失 | Gram矩阵衡量纹理风格差异 | `mathart/evolution/diff_render.py` |

### 参数优化范围

| 参数 | 值/范围 | 说明 | 代码映射 |
|------|---------|------|----------|
| `learning_rate` | 0.001-0.05 | 梯度下降学习率 | `mathart/evolution/diff_render.py` |
| `iterations` | 50-500 | 优化迭代次数 | `mathart/evolution/diff_render.py` |
| `perceptual_weight` | 0.5-2.0 | 感知损失权重 | `mathart/evolution/diff_render.py` |
| `style_weight` | 0.1-1.0 | 风格损失权重 | `mathart/evolution/diff_render.py` |
| `l2_weight` | 0.01-0.1 | L2重建损失权重 | `mathart/evolution/diff_render.py` |

### 蒸馏洞察
> 这意味着：可微渲染是将"内循环"从遗传算法升级到梯度下降的关键技术。遗传算法每代需要评估population_size次，而梯度下降每步只需1次前向+1次反向传播，效率提升10-100倍。

## 2D可微渲染的实用近似（来源：DiffVG）

对于2D像素画，完整可微渲染过于复杂，以下是实用近似方案：

| 方案 | 精度 | 速度 | 适用场景 |
|------|------|------|----------|
| 遗传算法（当前） | 中 | 慢 | 参数空间<20维 |
| 有限差分梯度 | 中 | 中 | 参数空间<50维 |
| 完整可微渲染 | 高 | 快（GPU） | 参数空间>50维 |
| 神经网络代理 | 高 | 极快 | 高频迭代 |

### 升级路径
1. **当前**：遗传算法（已实现）
2. **近期**：有限差分梯度近似（无需GPU）
3. **中期**：集成PyTorch可微渲染（需GPU）
4. **远期**：训练神经网络代理模型（需大量数据）

## 外部依赖需求（能力缺口）

> ⚠️ **需要用户配合**：完整可微渲染需要以下外部工具：
> - **GPU**：NVIDIA GPU（CUDA 11.8+）用于PyTorch加速
> - **PyTorch**：`pip install torch torchvision`
> - **nvdiffrast**：NVIDIA可微光栅化库
> - **DiffVG**：2D矢量图可微渲染库
>
> 当前实现使用CPU遗传算法作为替代，在GPU可用时自动切换到梯度下降。
