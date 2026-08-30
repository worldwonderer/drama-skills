---
name: short-drama
description: 基于文件系统初始化和继续短剧或漫剧项目，提供 creator-first 五文档路由、本地 Dashboard、制作形态与 Look Development 决策。用户提出“创建/继续短剧项目”“看进度/下一步”“做 Look Development”“打开 dashboard/短剧创作台”“导出制作资料”，或任务跨多个创作阶段时使用；明确的写作、资产、提示词、分镜或审查请求由对应子 skill 直接处理。
license: MIT
---

# 短剧创作路由

本技能负责项目初始化、跨阶段路由、制作形态与 Dashboard。各阶段正文由对应 owner 完成。

## Quick Start

所有项目统一使用 [creator-first 工作流](references/creator-workflow.md)：每集按需维护
`剧本.md`、`视觉设定.md`、`分镜.md`、`图片提示词.md`、`视频提示词.md`，不建立并行的结构化创作真相。
具体写法见 [五份创作文档](references/creator-documents.md)。

## 路由

| 用户要做什么 | owner / 行为 |
|---|---|
| 开发点子、系列承诺、改编和分集地图 | `$short-drama-develop`，仅在用户需要时 |
| 已有多集完整剧本/散稿识别分集 | `$short-drama-develop` 按实际边界建立临时索引 |
| 分析长篇原著 | `$short-drama-novel-analyze`，仅在用户需要时 |
| 写或改单集剧本 | `$short-drama-write` → `剧本.md` |
| 拆人物、造型、地点、道具 | `$short-drama-assets` → `视觉设定.md` |
| 写资产图片提示词 | `$short-drama-image-prompts` → `图片提示词.md` |
| 做镜头和冻结关键帧 | `$short-drama-storyboard` → `分镜.md` |
| 写视频/时间线音乐提示词 | `$short-drama-video-prompts` → `视频提示词.md` |
| 实际生成媒体 | `$short-drama-produce`，先预览，再显式确认，最后运行 |
| 审稿或校验 | `$short-drama-review`，仅在用户点名时 |
| 初始化、Dashboard、归档点名文档 | 本技能 |

`项目开发/` 中的长材料分析与分集索引是可选分析工作区，不参与单集布局判定；写任何一集仍只维护
该集的五份 creator-first Markdown。

现成剧本可直接拆资产；已有视觉事实可直接写图片提示词或分镜；已有分镜可直接写视频提示词。
不要为补齐名义流水线伪造上游。

## 执行请求

1. 找到用户给出的项目或资料，只读当前任务的直接输入。
2. 把用户点名的完整范围交给相应 owner；批次只控制上下文，自动续跑。
3. 只有真实创作分叉才询问；不要拿 schema、目录、事务或检查器询问创作者。
4. 范围完成后一次回报完成内容、关键决定、真实未决项和可选下一步。
5. 不自动开始用户没点名的审查、归档或生产。

## 初始化与 Dashboard

需要项目配置时运行：

```bash
python3 {技能目录}/scripts/project_tool.py init ./my-drama --title "示例短剧"
```

`init` 只建立配置和空目录；第一次创作时再把文档写入 `剧集/<EP>/`，不预建空文件。
项目定位与安全写入见 [运行预检](references/runtime-preflight.md)。用户明确要求 Dashboard 时运行：

```bash
python3 {技能目录}/scripts/dashboard_server.py --workspace <workspace> --port 0 --detach --open
```

`--detach` 让服务进程脱离当前 shell 独立运行，会话结束、终端关闭或智能体退出都不会带走它；
链接因此在整个创作期间保持有效。运行中的地址、端口和 pid 记录在
`<workspace>/.short-drama/dashboard.json`（仅本人可读），日志在同目录 `dashboard.log`：

```bash
python3 {技能目录}/scripts/dashboard_server.py --workspace <workspace> --status   # 打印当前链接
python3 {技能目录}/scripts/dashboard_server.py --workspace <workspace> --stop     # 停止
```

同一 workspace 已有在跑的 Dashboard 时，再次启动只会打印同一个链接，不再开第二个端口；确实要换
端口或换令牌时加 `--restart`。不加 `--detach` 时行为不变：前台运行，Ctrl-C 结束。

Dashboard 展示和编辑创作文件，不负责工作流编排或媒体生产。

## 项目级创作决定

制作形态、视觉方向、播放面和集长目标确实约束多个阶段时，展示选择及影响后由用户决定。
Look Development 是可选分支，不是进入图片提示词或分镜的固定门槛。

按问题只读取一份相关知识：

- 规则分级与 owner 路由：[规则与路由索引](references/knowhow-index.md)
- 输出语言、稳定 ID、所有权与安全边界：[契约与所有权](references/contract-and-ownership.md)
- 实拍、二维、三维、水墨、Q 版、国漫的形态差异：[制作形态](references/production-form-profiles.md)
- 需要比较代表帧时：[Look Development](references/look-development.md)
- 参考图能控制什么：[参考角色](references/reference-roles.md)
- 遮挡、延迟揭示和观众知情时机：[观众揭示](references/audience-reveal.md)
- 母版、补拍和替代版的职责：[补拍与替代](references/pickup-and-alternate.md)

## 生产与交付边界

外部生产永远保留 `preview -> explicit confirm -> run`。归档只复制用户点名的当前文档和成品，排除
私有输入、凭据、绝对路径与隐藏运行状态；不为归档补造审批、哈希或第二套内容。

用户问“做完了怎么导出/交付给我”时，用 `export` 打包当前状态：

```bash
python3 {技能目录}/scripts/project_tool.py export <project> --out <项目外目录>
```

它把每集现有的五份创作文档和 `剧集/<EP>/制作成果/` 复制到 `--out`，附 `manifest.json` 与
`checksums.sha256`，并排除 `输入/`、`交付/` 和 `.short-drama/`。只要一部分时加
`--episode EP001`（可重复）；只要文字时加 `--no-media`；覆盖旧目录加 `--overwrite`。
`--out` 必须在项目之外。

`export` 是**当前状态快照**，manifest 里 `asserts_approval` 恒为 `false`：它不声称任何审查或
创作者接受。需要带审批证据的正式交付包仍然只有 `package`/`verify` 那条路径。

## 安装维护

只有安装、升级或排障时运行 `python3 scripts/selftest.py`。
