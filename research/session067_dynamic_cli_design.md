# SESSION-067 Dynamic CLI / IPC / Manifest 设计说明

本轮改造的首要目标，不是再增加一个命令行脚本，而是把 **CLI 提升为与主线管线共享同一注册表和同一发布面的第一公民管理进程**。依据 Twelve-Factor 的 Admin Processes 原则，后台管理进程必须与主程序共享同一代码库、同一配置与同一依赖隔离环境，因此新的包级命令入口必须直接建立在 `mathart` 正式包内部，并复用 `BackendRegistry`、`AssetPipeline` 与 `ArtifactManifest`，而不能通过仓库外脚本或测试专用胶水层旁路调用。[1]

在结构上，新的命令层必须严格遵守 **Facade + Command** 组合纪律。Facade 层只负责提供一个极薄的统一入口，例如 `python -m mathart registry list`、`python -m mathart run --backend urp2d_bundle`，它不能内嵌任何诸如 `if backend == "motion_2d"` 之类的静态业务路由。真正的可执行目标必须从 `BackendRegistry` 反射发现并动态构造帮助信息、命令分派和参数透传；而一次执行请求则被收敛为单一运行载荷，例如 `BackendRunRequest` 或等价字典对象，统一承载 `backend`、`output_dir`、`config_path` 与透传参数，再交由 pipeline/bridge 执行。[4] [5]

在 IPC 通信上，新的命令层必须把 **stdout 视为机器契约通道，而把 stderr 视为人类诊断通道**。这意味着帮助提示、日志、调试信息、异常摘要、依赖装载提示以及未来 AI/工业渲染后端的进度信息，都必须走 `stderr`。`stdout` 在成功路径下只能输出一段可被 `json.loads()` 直接解析的 JSON 文本，不允许出现多余空行、横幅、前缀、进度条或装饰性文字。这个约束直接来自 CLI Guidelines 对 stdout/stderr 分工的建议，并与 UNIX 静默原则高度一致。[2]

Manifest 契约应借鉴 Kubernetes 与 OpenUSD 的共同点：**资源必须可识别、可定位、可声明式描述**。因此，CLI 的成功结果不应只输出孤立文件路径数组，而应输出一个结构化清单，至少包含资源种类、后端种类、产物角色、绝对路径、版本与元数据。若当前 `ArtifactManifest` 已有 `artifact_family`、`backend_type`、`outputs`、`metadata`，则本轮需要补充一层更适合 IPC 消费的标准化 JSON 形态，例如附加 `manifest_path`、`artifact_paths`（绝对路径字典）、`requested_backend`、`resolved_backend`、`status` 等字段，用于 Unity C# 或其他外部进程进行强类型反序列化。[3] [6]

结合现有代码现状，本轮实现应采用如下接线策略。第一，新增包级 `mathart/__main__.py` 与专用 CLI 模块，使 `python -m mathart` 成为正式入口。第二，在 `BackendRegistry` 上增加命令反射所需的稳定只读元数据访问能力，例如列出 canonical backend、别名、能力、依赖与输入要求。第三，在 CLI 层新增 `registry` 和 `run` 两类门面命令，其中 `registry` 只读展示动态发现结果，`run` 则统一接收 `--backend`、`--output-dir`、`--config` 与重复 `--set key=value` 参数。第四，`run` 命令把配置文件与 `--set` 参数合并为上下文字典后，交给 `AssetPipeline.run_backend()` 或桥接层执行，并在成功后将 `ArtifactManifest` 规范化为 IPC JSON 写入 `stdout`。

真实后端接线方面，`urp2d_bundle` 不能继续只返回占位路径，而应直接调用现有 `UnityURP2DNativePipelineGenerator` 生成 Unity 原生目录结构，并将返回的目录/文件转写为 Manifest；同时在输出目录生成 `artifact_manifest.json` 供外部系统长期引用。`motion_2d` 也不应止于虚构 spritesheet 路径，而应优先接入 `Motion2DPipeline` 的真实执行链，至少输出一个真实的 Spine JSON 或等价动画工件。这样，CLI 的端到端联调测试才不是“打通空接口”，而是真正验证 registry → facade → backend → manifest → subprocess stdout 的闭环。

为了守住“禁止写死后端数组”的防线，本轮命令树的所有 backend 可选值、帮助文本与测试枚举都必须来自 `get_registry().all_backends()` 或等价反射接口，绝不允许在 CLI、测试或审计脚本中出现静态 backend 名列表。即使测试要验证 `urp2d_bundle`，也应该先通过 registry 校验它存在，再发起 subprocess 调用。这样未来接入 `anti_flicker_render` 与 `industrial_sprite` 时，CLI 无须再改 trunk，只需要后端完成注册即可被动态发现。

在测试方面，必须同时覆盖进程内与真实子进程两个层级。进程内测试负责验证命令解析、日志分流、Manifest 规范化与错误码行为；新增的 E2E 测试则必须使用 `subprocess.run([sys.executable, "-m", "mathart", "run", "--backend", "urp2d_bundle", ...], capture_output=True, text=True)` 之类的真实终端调用，断言 `stdout` 能被 `json.loads()` 成功解析、`stderr` 不污染机器输出、生成目录存在且 `manifest_path` 指向有效文件。这条测试是本轮是否真正完成 IPC 闭环的生产级判据。

最后，需要为后续 `P1-AI-2C` 与 `P1-INDUSTRIAL-34A` 预留两个微调位。其一，CLI 参数透传模型应允许嵌套键，例如 `render.temporal_consistency=high`、`guides.depth=true`、`industrial.normal_strength=0.8`，避免未来再新增专用 flag。其二，Manifest Schema 应准备容纳多通道资产、时序资产与外部依赖资产，例如增加 `channels`、`temporal_assets`、`auxiliary_assets` 或统一的 `artifacts` 列表结构，以免接入防闪烁工作流与工业渲染 bundle 时再次改写 IPC 契约。

## References

[1]: https://12factor.net/admin-processes "The Twelve-Factor App — Admin processes"
[2]: https://clig.dev/ "Command Line Interface Guidelines"
[3]: https://kubernetes.io/docs/concepts/overview/working-with-objects/ "Objects In Kubernetes"
[4]: https://refactoring.guru/design-patterns/facade "Facade"
[5]: https://refactoring.guru/design-patterns/command "Command"
[6]: https://openusd.org/dev/glossary.html "USD Terms and Concepts"
