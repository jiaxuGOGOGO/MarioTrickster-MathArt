# SESSION-136 Dual Wizard Research Notes

## Source 1 — Twelve-Factor App / Config
- URL: https://12factor.net/config
- Key findings:
  - 配置必须与代码严格分离，尤其是外部服务凭证、部署相关参数与资源句柄。
  - 一个重要的检验标准是：代码库应当可以在任意时刻开源，而不会泄露任何凭证。
  - 相比散落的配置文件，环境变量更不易被误提交进代码仓库，并且具备语言/操作系统无关性。
  - 需要避免将配置按固定环境名打包为强耦合组，推荐使用彼此正交、可独立管理的细粒度配置项。

## Source 2 — PEP 810 Explicit Lazy Imports
- URL: https://peps.python.org/pep-0810/
- Key findings:
  - 惰性导入适合 CLI 工具、多子命令应用和大型依赖图场景，可降低启动时间、内存占用和无谓初始化。
  - 惰性导入应当是显式、局部、受控、渐进采用的工程决策，而不是全局魔法开关。
  - 将重型依赖推迟到实际路径中加载，有利于 `--help`、纯审计模式、CPU dry-run 等场景实现轻启动。
  - 对插件/注册表架构而言，惰性加载最适合通过“按模式分发时再导入实现模块”的方式落地，而非在顶层入口全量导入。

## Source 3 — OpenGitOps
- URL: https://opengitops.dev/
- Key findings:
  - GitOps 的核心原则可概括为：声明式、版本化且不可变、由软件代理自动拉取、持续对账与收敛。
  - 对本项目而言，知识蒸馏结果应当被视为“声明式知识状态”，由 Git 充当单一可信审计源，而不是散落在临时目录或手工说明中。
  - 因此 Git Agent 应当仅提交知识载体白名单，并在失败时优雅降级，保证主流程可恢复。

## Source 4 — GitHub Prompt Engineering Instructions
- URL: https://github.com/github/awesome-copilot/blob/main/instructions/ai-prompt-engineering-safety-best-practices.instructions.md
- Key findings:
  - 提示词资产应当像代码一样被版本化，记录变更理由，并在可能时保持向后兼容。
  - 标准化提示词文件适合以 Markdown 形式进入仓库，便于团队复用、审查与迭代。
  - 因此 `tools/PROMPTS/manus_cloud_distill.md` 应明确输入、输出格式、校验步骤、提交范围与失败回退策略。

## Source 5 — Edge-Cloud Collaboration Framework for Generative AI
- URL: https://arxiv.org/html/2401.01666v1
- Key findings:
  - 边云协同生成式 AI 的核心不是二选一，而是将大模型与小模型、本地能力与云端能力按任务特征协同编排。
  - 本地侧适合隐私敏感、资源可控、低延迟或离线能力；云端侧适合重认知、长链推理、集中式知识整理与统一版本沉淀。
  - 中间结果、策略与知识约束需要以结构化资产形式回传并沉淀，才能形成持续演进闭环。
  - 对本项目而言，这直接支持“本地科研蒸馏 + 云端 Manus 直推”双轨总线设计，而不是把所有知识蒸馏局限在单机内完成。
