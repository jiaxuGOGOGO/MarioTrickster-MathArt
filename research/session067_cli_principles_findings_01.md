# SESSION-067 CLI Principles Findings 01

## Source 1: The Twelve-Factor App — Admin Processes
- URL: https://12factor.net/admin-processes
- Core finding: 后台管理/维护任务必须作为 **one-off processes** 运行，并且与常规长生命周期进程共享 **同一 release、同一 codebase、同一 config、同一 dependency isolation**。
- Direct implication for this task: 本轮 CLI 不能被实现为仓库外的临时脚本或只服务测试的旁路入口，而必须成为与主工程共享同一 Registry / AssetPipeline / backend contract 的正式进程入口。
- Additional implication: CLI 任务与主线代码必须共用同一套依赖隔离与发布路径，因此 `python -m ...` / 包级入口应直接落在仓库正式包内。

## Source 2: Command Line Interface Guidelines
- URL: https://clig.dev/
- Core finding: **machine-readable output goes to stdout**，而 **log / errors / human messaging go to stderr**。
- Direct implication for this task: 本轮 CLI 必须把最终 Manifest JSON 作为唯一 stdout 载荷输出；帮助提示、进度、警告、调试日志都必须重定向到 stderr。
- Additional implication: 命令行程序需要保持子命令可发现性，因此可通过动态生成的帮助信息展示 registry 当前可用 backend 生态，而不是硬编码静态列表。

## Source 3: Kubernetes Objects
- URL: https://kubernetes.io/docs/concepts/overview/working-with-objects/
- Core finding: 声明式对象清单需要显式表达资源身份与期望状态，典型字段包括 `apiVersion`、`kind`、`metadata`、`spec`，并通过结构化清单向外部系统传递可验证契约。
- Direct implication for this task: 本项目的 Manifest JSON 不应只是模糊的路径列表，而应显式包含资源类型、后端身份、输出文件绝对路径，以及描述期望消费形态的结构化字段。
- Additional implication: 可以借鉴 `spec` / `status` 分层思想，把“执行配置”和“执行结果”明确区分，便于后续 Unity/外部进程做强类型反序列化。

## Source 4: Refactoring.Guru — Facade
- URL: https://refactoring.guru/design-patterns/facade
- Core finding: Facade 的职责是为复杂子系统提供简化入口，但不把底层业务逻辑重新塞回门面层，更不能把门面膨胀为 god object。
- Direct implication for this task: CLI 层应只负责命令解析、载荷透传、调用 registry / pipeline facade、输出结果；不能硬编码后端分支逻辑，更不能内嵌具体业务参数校验。
- Additional implication: 若未来子系统继续增多，应优先通过新增细分 facade 或 registry metadata 扩展，而不是在单个 CLI 文件里叠加条件分支。

## Source 5: Refactoring.Guru — Command
- URL: https://refactoring.guru/design-patterns/command
- Core finding: Command 模式把一次请求封装为独立对象，使请求发起、参数承载与执行接收者解耦。
- Direct implication for this task: CLI 的 `run` 子命令可以把 `backend`、配置文件路径、原始 kwargs、输出目录等组合为一份统一执行请求，再交给 registry / pipeline 执行，而不是让命令解析层直接支配具体后端逻辑。
- Additional implication: 这与“参数黑洞透传”高度一致：CLI 层只负责收集和转交请求，具体合法性由 backend 自身的 `validate_config()` 或执行契约负责。

## Source 6: OpenUSD Terms and Concepts
- URL: https://openusd.org/dev/glossary.html
- Core finding: OpenUSD 强调 asset 的可标识、可定位、可版本化，以及通过 metadata / AssetInfo 提供跨工具可消费的资产身份信息。
- Direct implication for this task: 本项目 Manifest JSON 除了列出物理文件绝对路径，还应显式包含类似 `backend_type`、`artifact_family`、`artifact_paths`、`version`、`metadata` 的字段，以支持 Unity 或其他外部进程做稳定消费。
- Additional implication: 资产可以是单文件，也可以是由单一清单锚定的多文件集合；因此本项目应把 Manifest 视为多文件 bundle 的正式锚点，而不是附带的调试信息。
