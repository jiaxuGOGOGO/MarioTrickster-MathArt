# SESSION-202 外网工业参考研究笔记

## 1. Gradio 响应式状态管理 (Reactive State Management)

**核心范式**：Gradio Blocks API 提供声明式 UI 组件与事件监听器绑定机制。
- 使用 `gr.Blocks()` 上下文管理器构建布局
- 通过 `.click()` / `.change()` 等事件监听器绑定 Python 回调函数
- 状态管理通过 `gr.State()` 组件实现跨事件的数据持久化
- 组件更新通过返回值自动映射到输出组件

**与本项目的映射**：
- 左侧控制区的下拉框（动作选择）→ `gr.Dropdown` 动态读取 `OpenPoseGaitRegistry`
- 参考图拖拽上传 → `gr.Image(type="filepath")` + `shutil.copy` 持久化
- VFX Toggle 开关 → `gr.Checkbox` 组件
- 右侧画廊 → `gr.Gallery` + `gr.Video` 组件

## 2. Event-Driven UI Update (事件驱动更新与流式透传)

**核心范式**：Gradio 原生支持 Python Generator 函数作为事件处理器。
- 使用 `yield` 替代 `return`，实现流式输出
- `gr.Progress()` 提供内置进度条追踪
- `progress(0.5, desc="Processing...")` 更新进度
- `progress.tqdm(iterable)` 自动追踪迭代进度
- 设置 `streaming=True` 在 Audio/Video 组件上实现流式播放

**关键代码模式**：
```python
def long_running_task(inputs, progress=gr.Progress()):
    progress(0, desc="初始化...")
    for step in progress.tqdm(range(100), desc="渲染中"):
        # 执行渲染步骤
        yield partial_result  # 流式返回中间结果
    yield final_result
```

**防页面假死**：
- 绝不使用阻塞函数绑定 UI 按钮
- 必须使用 yield 逐步返回状态
- Gradio 的 queue() 机制自动处理并发

## 3. Registry Pattern (控制反转 IoC 注册表模式)

**核心原则**：
- 新功能封装为独立 Backend/Lane 类
- 通过 `@register_backend` / `@register_enforcer` 装饰器自注册
- 严禁修改主干 if/else 路由逻辑
- 强类型契约：导出产物必须声明 `artifact_family` 和 `backend_type`

**本项目已有实现**：
- `MotionStateLaneRegistry` — 动作注册表
- `OpenPoseGaitRegistry` — 步态注册表
- `@register_backend` — 后端自注册装饰器
- `@register_enforcer` — 知识执行器自注册

## 4. The Pragmatic Programmer: Zero Broken Windows

**核心理念**：
- 带着遗留红灯构建新功能是自寻死路
- 必须先修复所有失败测试，再进行新开发
- 严禁 `@pytest.mark.skip` 或删除测试掩盖问题
- 必须找到字典契约不对齐的根因，实打实修绿

**本次应用**：
- 5 个 `TestUnifiedVFXHydration` 失败用例
- 4 个因 `networkx` 模块缺失（SESSION-198 Rasterizer 依赖）
- 1 个因返回值 `dead_water_pruned` vs 预期 `graceful_degradation`
- 需要对齐 SESSION-201 的 Intent 重构后的新契约
