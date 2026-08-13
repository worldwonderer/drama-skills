---
name: short-drama
description: 基于文件系统初始化、继续、恢复和交付短剧或漫剧项目，并提供面向创作者的本地 Dashboard；也负责组织项目级 Look Development / 风格帧方向与授权生产观察校准。面向编剧、漫剧工作室与编导。用户提出“创建/继续短剧项目”“看进度/下一步”“做 Look Development”“用生产观察校准本项目”“打开 dashboard/短剧创作台”“恢复中断或不完整发布”“导出制作资料”，或任务跨多个环节、意图不明确而需要先判断当前状态与负责技能时使用；明确的写作、资产、提示词、分镜或审查请求由对应子 skill 直接处理。
license: MIT
---

# 短剧创作路由

这是轻量项目路由，不在本技能内代写故事、资产提示词、镜头、视频提示词或终审结论。

创作者可读的内容跟随项目 `short-drama.json#/language`（状态、差异、选择、下一步都算）；
送给图片/视频生成器的提示词正文跟随 `#/format/prompt_language`（默认 `en`）。
两者是分开的字段，不要用其中一个推断另一个——`status` 会同时报出这两个值，
完整规则见 [contract-and-ownership.md](references/contract-and-ownership.md) 的输出语言契约。

## 每次请求的起点

1. 使用用户明确路径，或从当前目录向上寻找最近的 `short-drama.json`。
2. 从本技能安装目录找到同一套件的其他技能，读取 `suite-manifest.json`；缺少技能或版本混用时先停止变更。
3. 只读 `short-drama.json` 与 `.short-drama/state.json` 摘要；不要一次加载全部创作文件。
4. 执行 `status` 或写入前先运行事务恢复。发现外部编辑冲突时保留原文件，提供
   `adopt`、`restore`、`merge` 三种处理，不静默覆盖。
5. 按创作者当前任务路由；不强制补走整条流水线。

入口、检查点、修订和交付见 [creator-workflow.md](references/creator-workflow.md)。
每次入口先执行 [runtime-preflight.md](references/runtime-preflight.md)，统一验证安装、恢复事务并读取项目状态。
所有权、文件过期标记 `stale`、隐私或恢复有疑问时读
[contract-and-ownership.md](references/contract-and-ownership.md)。
意图含混时读 [routing-examples.md](references/routing-examples.md)。
只在需要把规则 ID 定位到负责技能时读
[knowhow-index.md](references/knowhow-index.md)；路由只负责分派，不代替创作技能判断。
一张参考图可以决定什么、以及首尾帧契约见 [reference-roles.md](references/reference-roles.md)；
观众此刻可以知道什么见 [audience-reveal.md](references/audience-reveal.md)；
补拍与替代版和母版的关系见 [pickup-and-alternate.md](references/pickup-and-alternate.md)。
不同制作形态的执行翻译见 [production-form-profiles.md](references/production-form-profiles.md)。
需要在正式分镜前用人物、地点和高压力代表帧统一视觉语言时读
[look-development.md](references/look-development.md)。

## 意图路由

| 创作者意图 | 路由 |
|---|---|
| 开发点子、故事承诺、系列、分集地图 | `$short-drama-develop` |
| 导入已有多集完整剧本/散稿并生成或补分集地图 | `$short-drama-develop` 先按文件实际结构建立候选边界索引，每次只切当前集并续跑；语义边界与 map 内容由 Agent 判断，脚本不硬编码 |
| 导入小说/长材料并做可追溯分集与资产候选预览 | `$short-drama-novel-analyze` → `$short-drama-develop` → 接受改编/分集 → `$short-drama-write` → 接受剧本 → `$short-drama-assets` |
| 判断一本长篇值不值得改、先看抽样快评 | `$short-drama-novel-analyze` |
| 写/改单集契约、因果节拍、剧本 | `$short-drama-write` |
| 拆人物/造型、地点/视图、道具/状态 | `$short-drama-assets` |
| 写人物/地点/道具/局部修改的图片提示词 | `$short-drama-image-prompts` |
| 做原文覆盖、镜头或冻结关键帧 | `$short-drama-storyboard` |
| 写动作/表演/运镜/声音视频提示词 | `$short-drama-video-prompts` |
| 定画风、制作形态或视觉方向 | `$short-drama` 本身：按 [production-form-profiles.md](references/production-form-profiles.md) 定位一张形态卡，产出候选并请创作者接受，再提升到 `creator_authority`。资产、图片提示词、分镜与视频提示词都要求形态已定，且都不自行选择，所以这一步必须在这里完成 |
| 做 Look Development / 风格帧 | `$short-drama` 先明确并接受可观察视觉方向 → `$short-drama-image-prompts` 写 `lookdev_frame` 文本规格；不生成图片，也不让风格参考接管身份、地理或剧情状态 |
| 用授权生产观察校准本项目 | `$short-drama-review` 绑定准确版本并诊断 → 对应 owner 做有 `preserve_set` 的定点修订 → 重新接受与 fresh re-review；没有授权观察时只报文字风险 |
| 校验、审查或发修订请求 | `$short-drama-review` |
| 只检查或诊断模板感、AI 味 | fresh `$short-drama-review`，只发带证据 finding |
| 直接去 AI 味、润色或定点改稿 | `$short-drama-write`，保留作者声音并展示语义差异 |
| 先检查再改 | fresh review → write owner 定点修订 → fresh re-review |
| 打开 Dashboard、短剧创作台或管理创作内容 | `$short-drama dashboard`：执行下方“本地 Dashboard”启动契约 |

创作者明确意图优先于名义上的“下一检查点”。Look Development 是可选的项目级分支，不是通用前置；
C2 资产接受后，图片提示词和分镜是平行分支；创作者只要其中一支时，不强迫等待另一支。

若当前上下文参与过目标文件的创作，审查路由必须优先启动 fresh reviewer agent/context，
只传目标、已接受限制和审查表；运行环境不支持 fresh agent 时透明降级为 `PROVISIONAL`
自检，不能把切换 Skill 名称当成独立审查。

“像不像模板/AI 写的”是诊断请求；“把它改掉”是 owner 修订请求。不要让 write owner
先自诊断再给自己签发结论，也不要让 reviewer 越权直接改正文。组合请求先冻结目标版本，
由 fresh reviewer 定位证据和损失，再交 write owner 只改被接受的范围，最后换一个 fresh
reviewer 对新 hash 做 re-review；任何无法取得独立上下文的环节都保持 `PROVISIONAL`。

## 初始化

没有项目且用户要初始化时：

1. 仅确认或合理推断可逆格式默认值：标题、语言、提示词语言、画幅、路径；
   集数/时长未知就留空。语言默认取创作者当前使用的语言，提示词语言默认 `en`；
   创作者明确要求提示词也用项目语言时按创作者的，不代为判断质量后果。
2. 复制项目模板，不覆盖已有创作者文件。
3. 建立空阶段目录和非公开输入边界。
4. 在 `short-drama.json#/creator_authority` 建立空的创作者限制、视觉方向和制作配置；
   它们初始为 `unset`，而资产及其下游都要求形态已定——所以初始化后要告知创作者：
   **定制作形态是下一个最有用的动作**，入口在本技能（见意图路由表），不在任何子技能；
   实际选择写入 [creator-decision.example.jsonl](assets/creator-decision.example.jsonl)
   所示的决定记录。
5. 记录套件版本、契约版本与五项彼此独立的空状态。
6. 告知项目路径和最有用的创作者动作。

初始化不生成故事引擎、剧本或资产设定表。

开发阶段若提交 `项目开发/director-brief.md`，先向创作者展示其相对当前
`visual_direction` / `production_profile` 的语义差异；只有明确接受后，路由才把相应选择
提升到 `short-drama.json#/creator_authority`。候选文件本身不具有 creator authority。

## 本地 Dashboard

当用户调用 `$short-drama dashboard` 时，不再转交子技能，直接启动本技能自带的
本地短剧创作台：

1. 先按 [runtime-preflight.md](references/runtime-preflight.md) 核对套件并恢复未完事务。
2. 工作目录位于某个项目内时，以该项目根为 `workspace`；否则使用用户明确
   给出的容器目录，未给出则使用当前目录。不扫描 workspace 之外。
3. 从本技能安装目录运行：

   ```text
   python3 <short-drama-skill-dir>/scripts/dashboard_server.py --workspace <workspace> --port 0 --open
   ```

4. 保持进程运行，回报脚本打印的完整回环地址（包含本次启动的会话片段）和停止方式。
   浏览器无法自动打开时，保留服务并让用户打开该完整地址。

Dashboard 只有一个创作页面：左侧是按项目与剧集整理的通俗内容目录，右侧是常驻正文；
打开项目后自动载入剧本，切换内容只替换正文，不打开新页面或浮层。待办与导出只作为
正文下方的简短提示。界面不显示文件树、真实路径、结构化格式、生命周期或工程详情入口。
Markdown 作为文稿阅读和编辑；结构化数据转换为卡片；图片和视频只预览。
`short-drama.json`、`.short-drama/**`、创作者决定、检查证据和导出校验均由系统维护，
不成为创作者导航。保存只表示保存修改，不能替代创作者明确采用某个版本。
它不连接外部数据源、不调用媒体生成接口，也不向浏览器注入密钥。
每次启动生成独立会话凭证和 API 路径；浏览器用地址片段换取 `HttpOnly` 本机会话，
项目 API 仅接受该会话。
具体参数见 [lifecycle-commands.md](references/lifecycle-commands.md#dashboard-启动)。

## 确定性工具

从本技能安装目录调用 `scripts/project_tool.py`，不依赖当前工作目录：

| 命令 | 用途 |
|---|---|
| `init` | 初始化最小项目 |
| `status` | 读取生命周期与恢复摘要 |
| `recover` | 恢复全部或指定事务 |
| `publish` | 通过预写日志发布 `candidate`，不附带接受或审查结论 |
| `accept` | 用创作者决定记录接受准确的 `candidate` 目标 |
| `review` | 用独立审查结论更新校验与审查状态 |
| `package` | 复验五轴、依赖和证据后生成文本交付包 |
| `verify` | 用交付包自带的校验和复核它，并报告未登记的新增文件 |

只有实际调用这些命令、诊断失败或核对记录格式时，才读取
[lifecycle-commands.md](references/lifecycle-commands.md) 中的完整调用示例、预写日志、接受、
审查、下游过期影响与打包约束。

## 状态与下一步

用创作者语言说明：

- 已存在且状态为 `accepted` 的来源；
- 状态为 `provisional`、`stale`、`blocked` 或待创作者接受的内容；
- 当前可并行进入的分支；
- 推动用户所求结果的最小动作。

除非用户要求诊断，不打印 `hash`、事务 ID、内部数据结构或原始状态内容。
五项状态彼此独立：构建、校验、创作者接受、独立审查和交付检查；不得用一个
`accepted` 冒充全部。

## 恢复与修订

恢复用户所问环节内最早未完成的操作，而不是全项目最早阶段。变更已确认内容前：

1. 指明负责修改的技能；
2. 展示拟议的语义变化；
3. 列出准确的下游受影响清单；
4. 在需要时取得创作者接受；
5. 交给负责人修改，并对新 `hash` 重新审查。

没有 `COMMIT` 的不完整事务回滚到已保存的上一版本；已有 `COMMIT` 的不完整事务继续到
`candidate` 并补齐状态。恢复必须先读后写、可重复运行；无法确认来源的外部改动必须标为
`conflict`，不能覆盖。

## 交付

先路由到 `$short-drama-review` 的 fresh 审查上下文校验，再在交付检查就绪时打包。只包含状态为 `accepted`
的剧本、清单、提示词、审查、创作者备注与校验和。排除二进制媒体、非公开输入、
机器状态、绝对路径、凭据、非公开来源材料和未批准草稿。

## 边界

- 只使用当前智能体的文本推理；不调用媒体生成或服务接口。
- 运行时不检索外部或非公开生产来源。
- 不把别处见过的案例提升为创作定律。
- 负责人不能审查自己的产物。
- 语义冲突不静默修复。
