**中文** | [English](README_EN.md)

# Drama Skills

[![CI](https://github.com/zenstory-ai/drama-skills/actions/workflows/ci.yml/badge.svg)](https://github.com/zenstory-ai/drama-skills/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/zenstory-ai/drama-skills)](https://github.com/zenstory-ai/drama-skills/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/zenstory-ai/drama-skills)](LICENSE)

面向编剧、漫剧工作室和编导的 AI 短剧创作工作流。十个技能把一个点子或一部长篇材料，
一路做成分集剧本、资产设定、图片提示词、分镜关键帧和视频提示词，
用清晰的所有权与连续性衔接。适配 Claude Code、Codex 和其他
支持 Agent Skill 规范的运行环境。

新项目每集默认只维护五份 Markdown：`剧本.md`、`视觉设定.md`、`分镜.md`、
`图片提示词.md` 和 `视频提示词.md`。提示词预览并经用户明确确认后，也可通过
项目外配置的 adapter 执行图片、视频、TTS 和时间线音乐生产。

## 由来

这套技能来自我们自己的漫剧工作室产线：2025 年至今累计上千个 AI 短剧 / 漫剧项目，
中间换过几轮自建和开源工具。前后端加起来近 8 万行代码，在模型能力和需求的迭代速度
面前逐渐维护不动了。

后来干脆抛开自建的一体化图形工具，把历史项目工程和图片 / 视频提示词蒸馏成这套技能，
让制作人直接用 agent CLI 加文件维护工程、生成提示词，确认之后再送去生成——结果意外地顺手。
现在留在自建工具里的，只剩排队抽卡。

**刻意把确认放在生产之前**：提示词先落进文件，生产 skill 展示本次准确数量、内容、参考、
参数、输出和 adapter；用户看到预览并明确确认后才执行。任何内容或直接输入变化都会让确认
失效，已启动的失败任务也不能无确认重试。供应商凭据不进入项目；生产 Skill 自带
Seedance、GPT Image 2、MiniMax H3 视频与 MiniMax Music 的可选 adapter，但项目文件和其他 Skill 不绑定供应商。

## 安装

需要 **Python 3.9 或更新版本**（macOS 自带的即可）。直接告诉 Claude Code、
Codex 等支持导入 GitHub 仓库的智能体：

```
安装这些技能 https://github.com/zenstory-ai/drama-skills
```

<details>
<summary>手动链接（可安装全部，也可只链接需要的技能）</summary>

```bash
git clone https://github.com/zenstory-ai/drama-skills.git && cd drama-skills

# Claude Code
mkdir -p "$HOME/.claude/skills"
for skill in skills/*; do
  ln -s "$PWD/$skill" "$HOME/.claude/skills/$(basename "$skill")"
done

# Codex
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
for skill in skills/*; do
  ln -s "$PWD/$skill" "${CODEX_HOME:-$HOME/.codex}/skills/$(basename "$skill")"
done
```

每个技能都是独立安装单元；只使用写作、审查或生产等单一能力时，可以只链接对应目录。
`short-drama` 提供项目初始化、路由与 Dashboard，但不是其他技能的安装门禁。

</details>

调用写法随运行环境而变：Claude Code 用 `/short-drama`，Codex 用 `$short-drama`，
也可以不写前缀、直接用自然语言说明要做什么。两种写法在下文示例中可以互换。

## 快速开始

```
# 0. 有原著时（可选）：先抽样快评，再决定要不要全量拆
用 $short-drama-novel-analyze 快评 输入/这本小说.txt，先告诉我值不值得拆

# 已有多集完整剧本时（可选）：按文件实际结构索引，每次只读当前集，断点续做分集地图
用 $short-drama-develop 从 输入/剧本完整版.txt 生成分集地图；先识别这份文件的分集方式，不要整稿塞进上下文

# 1. 新建项目
用 $short-drama 初始化一个都市打脸题材的短剧项目，竖屏 9:16

# 2. 写第一集
用 $short-drama-write 写第 1 集：外卖员在高档餐厅被经理羞辱，亮出集团董事身份

# 3. 拆资产，写提示词与分镜（可在同一请求内连续完成）
用 $short-drama-assets 从第 1 集拆人物/场景/道具
需要统一视觉语言时，可选用 $short-drama 做 Look Development
用 $short-drama-image-prompts 为已接受的资产写参考图提示词
用 $short-drama-storyboard 给第 1 集做正式分镜与冻结关键帧
用 $short-drama-video-prompts 把分镜逐镜翻译成视频提示词

# 4. 明确确认后投产
用 $short-drama-produce 预览第 1 集已接受的图片、视频、TTS 或时间线音乐任务；等我确认后再执行

# 5. 需要时再审查
用 $short-drama-review 审查第 1 集的剧本与提示词
```

普通创作不运行安装 selftest，不为阶段批次生成 JSON/JSONL、指纹、QA 或覆盖表，也不在每个
场次/资产组/镜头组后停下来等“继续”。用户点名审查时，结论写入创作者可读的 Markdown。

示例都在 [examples/](examples/)。creator-first 的公开完整样例是
[《让你管账号》EP001](examples/creator-first/EP001/)；其余目录仅作为仓库维护和校验器回归夹具。
想把十个技能按漫剧产线从头串一遍（每步命令、产物与卡点），看
[漫剧创作全流程指引](docs/comic-drama-workflow.md)。

## 十个技能

```mermaid
flowchart LR
    classDef phase fill:#e8f4fd,color:#1a1a2e,stroke:#4a9be8,stroke-width:1px
    classDef final fill:#fce4ec,color:#333,stroke:#e57373,stroke-width:1px

    nva["原著分析<br/>$short-drama-novel-analyze"]:::phase
    dev["故事开发<br/>$short-drama-develop"]:::phase
    write["分集剧本<br/>$short-drama-write"]:::phase
    assets["资产决策<br/>$short-drama-assets"]:::phase
    img["图片提示词<br/>$short-drama-image-prompts"]:::phase
    sb["分镜/关键帧<br/>$short-drama-storyboard"]:::phase
    vid["视频提示词<br/>$short-drama-video-prompts"]:::phase
    prod["确认后生产<br/>$short-drama-produce"]:::phase
    rev["审查<br/>$short-drama-review"]:::final
    pkg["文本交付包"]:::final

    nva -.有原著时.-> dev
    dev -.可选.-> write --> assets
    assets --> img
    assets --> sb --> vid
    img --> prod
    vid --> prod
    prod --> rev --> pkg
```

| 技能 | 职责 |
|---|---|
| `short-drama` | 初始化、路由、视觉方向/Look Development 与 Dashboard |
| `short-drama-novel-analyze` | 长篇原著的抽样改编快评、章节索引、逐章功能提取、剧情单元与节奏、改编价值与分集候选 |
| `short-drama-develop` | 小说/长材料的可追溯改编、多集整稿的 Agent 主导切片与续跑、故事引擎、分集地图、导演阐述、题材与钩子手册 |
| `short-drama-write` | 单集目标、因果节拍、可拍剧本和项目选择的制作稿格式 |
| `short-drama-assets` | 人物/造型、地点/视图、道具/状态、可选的角色声音方向与连续性决策 |
| `short-drama-image-prompts` | Lookdev 风格帧、角色/场景/道具参考板提示词与定点修改说明 |
| `short-drama-storyboard` | 可选场次视觉计划与 Coverage Audition、原文落实、镜头、边界和冻结关键帧 |
| `short-drama-video-prompts` | 单镜动作、多人物表演与注意交接、摄影、声音、起止状态、补拍说明，以及跨镜时间线音乐规格 |
| `short-drama-produce` | 展示有边界的图片/视频/TTS/音乐任务，取得本次明确确认后通过外部 adapter 执行并记录结果；可选支持 Seedance、GPT Image 2、MiniMax H3 视频与 MiniMax Music |
| `short-drama-review` | 结构/内容审查、授权生产观察的项目级校准诊断与修订结论 |

`$short-drama` 是入口路由，负责初始化、继续和 Dashboard，把具体工作转给对应技能。交付直接选择
五份 Markdown 与成品，不为打包补建生命周期记录。
现成单集剧本可以直接进入规范化或资产拆解；多集整稿需要生成分集地图时，由开发技能按
文件实际结构建立一次索引、逐集切片并断点续跑；点子从故事开发进入。手上是一部长篇原著时，
先走 `$short-drama-novel-analyze` 抽样快评，值得拆再拆出分析层与分集候选，
再由故事开发把它立成改编契约。

三条单帧提示词路径职责不同：项目级 `lookdev_frame` 检验已接受视觉方向；资产提示词固定人物、
地点、道具的可复用事实；`storyboard` 的关键帧只投影本镜 start（执行方式需要时可增加只投影
`end_boundary` 的 end 帧）。三者都只负责文本规格；实际生成统一交给 `$short-drama-produce`
在展示准确任务并取得本次确认后执行。

关键场次可以在正式 shots 前增加一层稀疏导演决策：先比较真正不同的信息时机、观看位置与
表演空间，再接受场次视觉计划，让构图、空间、摄影和声音共同完成一个转向。普通场景跳过，
不规定宫格、方案数或镜头数。

## 演示

《孤身入魔》演示含项目设定、两集剧本和十二板分镜；下方 15 秒宣传样片为临时展示，非默认产物。

https://github.com/user-attachments/assets/ae88b444-06e5-4964-856c-91e619020f12

## 本地短剧创作台

在智能体里一句话启动（Codex 写作 `$short-drama dashboard`）：

```
/short-drama dashboard
```

macOS、Linux、WSL 与 Windows 原生都可运行。创作台以 `--detach` 独立进程运行，链接在整个创作期间
保持有效；`--status` 打印当前链接，`--stop` 关闭。

全部跑完后导出交付：`$short-drama` 用 `project_tool.py export <project> --out <项目外目录>`
把每集现有的五份 Markdown 和 `制作成果/` 复制成一份带清单和校验和的交付目录。

<img src="docs/assets/dashboard-zh.png" alt="短剧创作台：项目概览、分集进度、已有媒体与剧本正文" width="680">

## 致谢

[LINUX DO - The New Ideal Community](https://linux.do) — 社区支持
