# 新电脑拉取与测试指南

本指南详细说明了如何在更换本地电脑后，拉取 MarioTrickster-MathArt 项目的最新代码，并验证所有功能（包括刚刚完成的 `TASK-002` Sprite CLI 工作流）是否正常运行。

## 1. 环境准备

在开始之前，请确保新电脑上已安装以下基础环境：

- **Git**：用于拉取代码。
- **Python 3.10 或更高版本**：项目依赖较新的 Python 特性。
- **虚拟环境工具**（推荐）：如 `venv` 或 `conda`，以隔离项目依赖。

## 2. 拉取代码与初始化

首先，将项目克隆到本地并安装开发依赖。

```bash
# 1. 克隆项目
git clone https://github.com/jiaxuGOGOGO/MarioTrickster-MathArt.git
cd MarioTrickster-MathArt

# 2. 创建并激活虚拟环境（可选但推荐）
python -m venv venv
source venv/bin/activate  # Windows 用户请使用 venv\Scripts\activate

# 3. 安装项目及其开发依赖
pip install -e ".[dev]"
```

安装完成后，你可以运行初始化脚本来确保项目的“大脑”状态（`PROJECT_BRAIN.json` 和 `SESSION_HANDOFF.md`）正确就绪：

```bash
python scripts/init_brain.py
```

## 3. 验证系统状态

项目提供了一个统一的 CLI 工具 `mathart-evolve`。你可以通过查看状态来确认系统是否正常加载了知识库和数学模型。

```bash
mathart-evolve status
```

正常情况下，你应该能看到：
- 知识库加载了 6 个规则文件。
- 数学模型注册表加载了 9 个模型。
- 当前运行模式（AUTONOMOUS 或 ASSISTED）。

## 4. 运行自动化测试

项目包含 380 个自动化测试，覆盖了所有的核心模块。运行这些测试是验证新环境是否配置正确的最快方法。

```bash
# 运行所有测试
pytest tests/ -v --tb=short

# 或者运行带有覆盖率报告的测试
pytest tests/ --cov=mathart --cov-report=term-missing
```

如果所有 380 个测试都显示 `PASSED`，说明你的本地环境已经完全就绪。

## 5. 测试新功能 (TASK-002)

在本次会话中，我们完成了 `TASK-002`，新增了 Sprite 参考图的 CLI 上传工作流。你可以通过以下步骤手动测试这些新功能：

### 5.1 添加单张 Sprite
准备一张测试用的 Sprite 图片（例如 `test_sprite.png`），然后运行：

```bash
mathart-evolve add-sprite path/to/test_sprite.png --type character --tags "test,character"
```

系统会自动分析该图片的色彩、形状和解剖学特征，并将其提取为数学约束存入 `knowledge/sprite_library.json`。

### 5.2 添加 Spritesheet（自动切帧）
如果你有一张包含多个动画帧的 Spritesheet（例如 `test_sheet.png`），你可以使用 `add-sheet` 命令：

```bash
# 自动检测网格大小并切帧
mathart-evolve add-sheet path/to/test_sheet.png

# 或者手动指定单元格大小
mathart-evolve add-sheet path/to/test_sheet.png --cell-size 32x32
```

系统会将切出的帧作为动画序列进行分析，提取运动幅度和循环质量等特征。

### 5.3 查看 Sprite 库状态
添加完成后，你可以查看当前 Sprite 库的统计信息和最佳参考图：

```bash
mathart-evolve sprites
```

## 6. 继续开发

如果你需要继续进行 `SESSION_HANDOFF.md` 中的其他任务（如 `TASK-003` 或 `TASK-004`），请确保在提交代码前运行 `ruff` 进行代码质量检查：

```bash
ruff check mathart/ tests/
```

祝你在新电脑上开发顺利！
