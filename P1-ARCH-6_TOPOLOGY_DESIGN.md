# P1-ARCH-6: Rich Topology-Aware Level Semantics Design Document

## 1. Executive Summary

本设计文档旨在为 MarioTrickster-MathArt 项目中的 `P1-ARCH-6` 任务（富拓扑感知的关卡语义底座）提供架构与算法方案。
目标是突破当前基于简单 ASCII 字符计数的关卡分析方法，引入纯张量计算（NumPy/SciPy）的拓扑提取器，将离散的 WFC 栅格升维为连续的、具有物理与语义属性的连通图与锚点系统。
同时，新功能将作为 `LevelTopologyBackend` 通过 Registry Pattern 无损挂载到 PDG v2 运行总线，并产出符合 `LEVEL_TOPOLOGY` 强类型契约的 ArtifactManifest。

## 2. Architecture Alignment & Discipline

根据项目红线与现有架构（`SESSION-064` ~ `SESSION-108`），设计需严格遵循以下准则：

1. **Registry Pattern & IoC**: 新的拓扑后端必须作为独立插件通过 `@register_backend` 注册，不修改主干调度逻辑。
2. **ArtifactManifest Contract**: 导出产物必须是强类型的 `LEVEL_TOPOLOGY` Manifest，确保数据落盘纯净且可追溯。
3. **Anti-OOM (Data-Oriented Design)**: 严禁使用 Python 原生双层 `for` 循环进行像素级遍历。必须使用 SciPy 2D 卷积 (`convolve2d`) 和 NumPy 掩码进行 O(1) 级别的模式匹配特征提取。
4. **Anti-Hardcoding**: 判别逻辑必须基于物理属性字典（如 `is_solid`）而非硬编码的瓦片字符。
5. **Anti-Data-Silo**: 拓扑数据结构必须使用严谨的 `@dataclass(frozen=True)` 定义，为后续 `P1-ARCH-5` (OpenUSD 导出) 铺路。

## 3. Data Structures: Frozen Dataclasses

在 `mathart/level/topology_types.py` 中定义以下核心结构：

```python
from dataclasses import dataclass, field
from typing import Tuple, List, Dict, Any
import numpy as np

@dataclass(frozen=True)
class SemanticAnchor:
    """Represents a discrete mounting point derived from pattern matching."""
    x: float
    y: float
    anchor_type: str
    normal_x: float
    normal_y: float
    properties: Dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class TraversalLane:
    """Represents a connected component of walkable surfaces."""
    lane_id: int
    surface_type: str
    bounds: Tuple[int, int, int, int]  # min_x, min_y, max_x, max_y
    area: int

@dataclass(frozen=True)
class TopologyTensors:
    """Container for the raw boolean masks and connectivity labels."""
    is_solid: np.ndarray
    is_walkable: np.ndarray
    is_wall: np.ndarray
    connected_components: np.ndarray
```

## 4. Tensor-Based Topology Extractor

`TopologyExtractor` 将使用 SciPy 2D 卷积进行模式匹配。

### 4.1 物理属性映射
首先将输入的 `logical_grid` (Tile IDs) 映射为布尔张量：
- `solid_mask`: 瓦片是否为刚体 (Solid, Platform, Wall, etc.)。

### 4.2 卷积核模式匹配 (Oskar Stålberg Dual Grid 启发)
使用 3x3 卷积核提取表面特征。例如，检测“平坦地面”（上方是空气，下方是实体）：
```python
import numpy as np
from scipy.signal import convolve2d

# 1 means solid, -1 means empty, 0 means don't care
kernel_floor = np.array([
    [ 0, -1,  0],
    [ 0,  1,  0],
    [ 0,  0,  0]
])
```
通过调整卷积核，可以并行提取出：
- `WalkableSurfaces` (地面、通行斜坡)
- `CollisionBoundaries` (墙体边缘、法线朝向)
- `DecorationAnchors` (内凹角、外凸角、天花板锚点)

### 4.3 连通组件分析 (Connected Components)
使用 `scipy.ndimage.label` 将 `WalkableSurfaces` 聚类为离散的 `TraversalLane`。
这为 AI 寻路和关卡分块提供了连续的图结构支持。

## 5. LevelTopologyBackend Integration

在 `mathart/core/builtin_backends.py` 或独立的 `mathart/core/level_topology_backend.py` 中实装：

```python
@register_backend(
    BackendType.LEVEL_TOPOLOGY,  # Need to add to backend_types.py
    display_name="Level Topology Extractor",
    version="1.0.0",
    artifact_families=(ArtifactFamily.LEVEL_TOPOLOGY.value,),
    input_requirements=("logical_grid",),
)
class LevelTopologyBackend:
    ...
    def execute(self, context: dict) -> ArtifactManifest:
        # 1. Read logical_grid from context (or upstream WFC manifest)
        # 2. Run TopologyExtractor (tensor ops)
        # 3. Serialize TopologyTensors to .npz
        # 4. Serialize SemanticAnchors & TraversalLanes to JSON
        # 5. Return ArtifactManifest
```

## 6. Implementation Plan

1. **Update Enums**: 在 `backend_types.py` 和 `artifact_schema.py` 中注册 `LEVEL_TOPOLOGY`。
2. **Core Algorithm**: 创建 `mathart/level/topology_extractor.py`。
3. **Backend Plugin**: 创建 `mathart/core/level_topology_backend.py`。
4. **CI & Tests**: 更新 `tests/test_ci_backend_schemas.py`，并编写针对张量提取的单元测试。
5. **Validation**: 运行 16 线程性能基准压测，确保不出现 OOM 或死锁。
