---
name: short-drama-produce
description: 在创作者明确确认后，执行短剧项目的图片、视频、TTS/配音或时间线音乐生产任务，并把结果与精简运行记录落回项目。用户说“生成这张图/这段视频/这句配音/这段配乐”“开始跑图/跑视频/合成语音/生成音乐”“把已确认提示词送去生产”，或要求批量执行已确认媒体任务时使用；不负责创作提示词、镜头、台词、歌词或声音身份，也绝不把预览、继续、预算说明或既有接受状态当作本次付费生产确认。
license: MIT
---

# 确认后生产

本技能只负责把已经写好的生产规格安全送到运行环境配置的 adapter。图片提示词仍归
`$short-drama-image-prompts`，视频提示词归 `$short-drama-video-prompts`，台词与录音表归
`$short-drama-write`，声音身份归 `$short-drama-assets`。

## Quick Start

只在用户明确要求实际生成后，从当前 `图片提示词.md`、`分镜.md` 或 `视频提示词.md` 中
取出本次提示词，建立一个有边界的运行 job。creator-first job 的 `source` 必须指向拥有这条提示词的
当前 Markdown，`source_entry` 必须点名对应的 `IMG-*` 或 `MOTION-*` 二级标题。存在真实参考图时，
还必须逐张填写 `reference_bindings` 的槽位、顺序、路径、中文名、用途以及允许/禁止控制范围；
`references` 可以省略并由绑定顺序生成，也可以作为相同顺序的显式镜像。输出放在
`剧集/<EP>/制作成果/`；这个 job 是生产工具的临时输入，不是第六份创作文档：

```bash
python3 {技能目录}/scripts/production_tool.py prepare <project> --job <临时-job.json>
```

先展示 `prepare` 的完整预览；此时不会调用供应商。

## 硬闸门

每次生产都必须经过以下四步，顺序不可合并：

1. 建立一个有边界的 job：一种 modality、明确数量、完整 prompt/spec、参考文件、参数、输出路径和 adapter profile。
2. 运行 `prepare`，把返回的完整预览展示给创作者，尤其是数量、prompt、source entry、
   reference bindings、references、outputs、overwrite 与 adapter。creator-first job 会在这一步机械核对
   所选标题里的可复制提示词，以及参考图槽位、顺序、路径、中文名和控制边界；任一漂移都 fail closed。
3. 等创作者在**看到这份预览之后**明确确认。只有明确同意这项当前任务，才运行 `confirm`；
   “继续”“都做完”“预算没问题”、上游内容已接受或之前确认过另一版，都不算本次生产确认。
4. 运行 `run`。它会在启动 adapter 前消费一次确认；成功或失败后再次执行都必须重新确认，
   防止失败重试意外产生第二笔费用。

job、prompt、参数、输出路径或直接输入任一变化，旧确认立即失效。不得代替创作者填写确认。
当前已确认 job 是本轮唯一工作单元；运行结束后回报结果并交还控制权，不自动准备下一批或启动审查。

`分镜.md` 的「输入参考图」路径只是创作阶段的可读依据与使用意图，不是生产输入快照。进入生产时，
creator-first job 必须从 `图片提示词.md` 或 `视频提示词.md` 的对应条目建立绑定；`prepare` 展示的
`reference_bindings`、`references` 与已确认 job 才是本次 adapter 实际读取哪些文件字节、各自允许
影响什么的权威。非 creator 的结构化规格可不填 `source_entry`/`reference_bindings`，继续只使用显式
`references`；但新的 image/video job 只要 `source` 指向 canonical `图片提示词.md` 或
`视频提示词.md` 就强制使用对应 selector，不能靠省略字段降级绕过。升级前已经 prepare 并落盘的
旧 job 仍可按原指纹读取。
新生产结果不自动回填或刷新分镜；需要把它改为后续输入时，由分镜 owner 修订文档，再建立新 job
并重新预览、确认。

## 命令

只在进入生产边界后把当前提示词和运行参数写成临时 JSON；视频与图片 job 的
`parameters.prompt_language` 跟随当前可复制正文已经解析出的提示词语言，使 adapter 追加的参考约束
使用同一种语言，而不是重新回退成固定英文。不要在创作阶段为每条提示词预建 job。
格式和 adapter 契约见
[adapter-contract.md](references/adapter-contract.md)。命令由
[production_tool.py](scripts/production_tool.py) 提供，然后运行：

```text
python3 <本技能目录>/scripts/production_tool.py prepare <project> --job <job.json>
python3 <本技能目录>/scripts/production_tool.py confirm <project> --job-id <id> --confirmation "CONFIRM <id> <code>"
python3 <本技能目录>/scripts/production_tool.py run <project> --job-id <id> --adapter-config <outside-project-config.json>
python3 <本技能目录>/scripts/production_tool.py status <project> --job-id <id>
python3 <本技能目录>/scripts/production_tool.py audit <project>
```

`prepare` 只验证并预览，不生产。`confirm` 只保存与当前 job 指纹绑定的一次性确认。
`run` 才启动 adapter。`audit` 只对账本地任务历史、失败后恢复、重复内容尝试和当前输出字节，
不会调用供应商，也不把技术成功、文件存在或哈希一致写成媒体质量结论。同一 job 存在未决
`running` attempt 时禁止重新 prepare、confirm 或 run；先等待完成或排查遗留 attempt。

## 输入选择

- **image**：读取 `图片提示词.md` 的当前 `IMG-*` 可复制正文、必要参考图和明确的输出尺寸/数量；
  creator-first job 使用 `source_entry` 锁定这一条。
- **video**：读取 `视频提示词.md` 的当前 `MOTION-*` 可复制正文，并核对 `分镜.md` 中对应镜头、
  冻结关键帧、时长与画幅；creator-first job 使用 `source_entry` 锁定这一条。连续段选择从上一段
  生成结果续接时，下一段 job 同时绑定上一段实际视频和从该视频取得的实际尾帧，并保留
  `continuity_video`、`actual_tail_frame` 的不同职责；供应商 role 由目标模型 adapter 翻译。H3 的这组
  输入统一译为 `reference_video + reference_image`，不能混成 `reference_video + first_frame`；不以
  计划尾帧或文字描述代替真实文件。
- **tts**：从 `剧本.md` 读取原句与表演要求，声音参考由用户或现有媒体明确提供。不得在生产 job
  中改词，也不为 TTS 新建第六份创作文档。
- **music**：读取 `视频提示词.md` 中创作者已确认的时间线音乐章节；主题曲使用已确认歌词，纯配乐
  不携带歌词。供应商不能精确承诺时长时，生成源音轨后仍由剪辑按文档里的混音意图完成落点、循环、
  淡入淡出和对白 ducking。

一个 job 不混合 modality。大批量工作拆成创作者能看清数量和成本边界的小 job；不为方便把整季
隐式塞进一次确认。

## Adapter 边界

adapter 配置必须在项目外，只包含 argv 命令和超时；凭据由 adapter 自己从进程环境或系统凭据
存储读取。项目 job、确认记录、运行记录和 Dashboard 都不得保存密钥。

脚本以 JSON stdin 调用 argv 数组，不使用 shell，不拼接命令。adapter 返回本地临时文件；工具只
接受与已确认 targets 完全一致的结果，并把完整文件原子复制到项目的 `剧集/<EP>/制作成果/`
目录。项目和上游 Skill 不写死供应商、模型或即将变化的 API。

内置图片/视频 compiler 会根据已确认的 `reference_bindings`，按顺序向供应商 prompt 附加一段确定性的
引用语义说明（中文名、用途、允许控制与不得控制范围）；不会把槽位名误当成要渲染进画面的文字。
外部 adapter 也必须保留这组语义或明确拒绝，不能只上传文件而静默丢失控制边界。

本技能可选提供四个 stdlib adapter，均通过项目外 adapter config 选择，凭据只从运行环境读取：

- [Seedance](references/providers/seedance.md)：模型/Endpoint ID 必须由账号显式配置；compiler 支持
  官方图片、视频和音频参考 role，内置 runtime 未配置可信上传时仍拒绝本地参考文件。
- [GPT Image 2](references/providers/gpt-image-2.md)：无参考图走 generation，有参考图走 edit；
  固定高保真引用并校验尺寸、格式与透明背景限制。
- [MiniMax Music](references/providers/minimax-music.md)：使用 `music-3.0` 与 hex 结果，区分主题曲
  和纯配乐，不伪造时长请求字段。
- [MiniMax H3 视频](references/providers/minimax-h3-video.md)：模型 ID、分辨率集合与时长区间必须
  由账号显式配置；提示词进 `content` 的 text 项，参考图按显式 role 绑定，本地参考在没有可信上传时
  fail closed。该模型与画面同一次生成声音，写法影响见视频提示词技能的目标模型能力档案。

这些 adapter 是已验证请求契约，不是账号可用性或生成质量保证；正式生产仍必须通过上面的本次
确认闸门，并由审查 Skill 判断产物质量。

仓库自带 `fixture_adapter.py` 只用于离线测试，不代表真实生成质量或默认生产 adapter。

## 结果与复核

成功后回报实际输出路径、媒体类型和运行状态；不要把“adapter 返回成功”写成质量结论。
多任务或重试后先运行 `audit`：终态失败按 `retryable` 路由，重试仍须新的明确确认；输出缺失或
文件的哈希或大小不再等于运行记录时，先复核当前字节或重新生产。`repeated_content` 只是成本与诊断信号，
不能自动判定同文重试合理或不合理；`running_attempt` 是未决运营状态，audit 必须返回 attention。
失败按三路走。超时、限流、服务端错误这类技术失败可做有上限重试。失败信息点名了被拒的是哪
一项输入——提示词文本、参考图或音频——就先改那一项再投：文本被拒改写那一句，把「一拳砸在
对方脸上，血顺着下巴滴」换成「一拳挥空，对方侧身避开，桌上的杯子被带倒」；参考图被拒换一张
构图与角色一致、画面本身合规的图；音频被拒重录那句台词。改动写进新的 job 重新 prepare，让创
作者在预览里看到改的是哪一项再确认；原样重投的那次确认不产生修复，只产生一笔费用。重复内容
缺陷回到对应 prompt/spec owner。
如需质量复核，报告可把已有结果另行交给 `$short-drama-review`；不要在生产调用中自动启动复核。
Dashboard 只负责展示这些文件和运行摘要，不提供 adapter 设置或生产按钮。

## 安装维护

只有安装、升级或排障时运行离线自检；普通创作和生产准备不运行：

```bash
python3 scripts/selftest.py
python3 scripts/provider_adapters.py --selftest
```
