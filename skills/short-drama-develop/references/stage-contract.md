# 开发阶段契约

## 目录

- [运行时预检](#运行时预检)
- [所有权边界](#所有权边界)
- [制作形态需要什么](#制作形态需要什么)
- [本阶段规则](#本阶段规则)

本文件是本技能的自包含契约：预检、所有权、形态输入与规则表都在这里，
不需要读取其他技能的文件。

## 运行时预检

进入本阶段前先完成这套轻量预检。它只检查安装完整性、项目事务状态和已记录的精确引用，
不评价创作内容。

1. **验证安装**：从本技能目录的 `suite-ref.json` 解析到逻辑安装路径中的 core，用当前
   环境可用的 Python 3 解释器运行 core 的 `scripts/suite_verify.py`。验证器沿逻辑安装
   路径逐一检查清单中的技能；混装、缺件、额外可执行文件或 hash 不一致时停止写入，
   也不要退回源码检出目录“借用”通过验证的兄弟技能。
2. **先恢复事务，再读状态**：定位项目根目录后，先运行 core 的 `scripts/project_tool.py`
   的 `recover`，再运行 `status`。`recover` 可重复执行；它报告 blocked 时保持创作者文件
   原样并先处理冲突，不要绕过 WAL、手改状态文件或假定上次写入成功。`status` 中的
   accepted/candidate 指针和阻断项是本阶段工作的当前事实。
3. **只通过公开生命周期写入**：负责人用 `publish` 原子发布候选，并给每个外部结构化引用
   提供精确 input hash。上游接受引用不继承候选状态。创作者接受、独立审查与内容修订是
   不同动作。每次修订后重新运行适用的结构校验，并让下游刷新旧 hash。打包是最终交付闸门，
   不是接受或审查命令；仍有阻断项时不打包。
4. **读共享 JSON/JSONL 时同时声明读了哪几条记录**：`设定集/*.jsonl` 与项目文件是全项目
   共享输入，只按整文件 hash 绑定会让后续任何一次增补把此前引用过它的产物全部标为
   `stale`。发布时对这类输入补 `--input-record <path>=<selector>`（JSONL 用记录 ID，
   JSON 用 RFC 6901 指针，每条一次），此后只有被绑定的记录变化才会影响本产物。
   Markdown 没有可机器校验的记录身份，仍按整文件绑定。

## 所有权边界

- **本阶段拥有**：系列承诺、冲突引擎、弧线、已规划的单集契约；已规划的知识/目标/关系/
  交接状态；改编取舍与题材选择。
- **本阶段继承**：创作者已接受的方向、约束、题材与受众承诺。
- **本阶段不越权**：不写逐镜事实，不指定供应商字段，不代替剧本决定场景怎么演。剧本环节
  只投影本契约，不复制它；契约变化时由本阶段发出修订，让下游刷新引用。

## 制作形态需要什么

视觉风格不是贴在提示词前面的标签。创作者已接受的视觉方向与制作形态由项目层决定并传入，
**本技能不加载形态卡，也不自行选择形态**；本节只说明本阶段需要形态回答什么、以及拿到
答案后投影成哪些字段。

形态决定属于 `craft_default`：创作者说明理由即可覆盖。形态不能创造新的
`structural_invariant`，也不能改写身份、地理、持物归属与可读文字政策。审查者不得单凭
形态偏好阻断交付。

不要用“加一句风格前缀”处理形态差异。前缀只改变检索标签；形态改变的是**必须出现和
可以省略的字段**，只有后者会被执行，也只有后者能被审查。

本阶段要向形态决定问三件事，答案写进创作简报，不写进剧本：

- **叙事职责**：这种形态要帮观众更快看懂什么、感到什么——不能只写“高级”“电影感”。
- **运动预算**：哪些段落必须全动作，哪些可以靠保持姿态、局部循环、视差或剪辑完成。
  这直接决定分集地图里哪些场面写得起、哪些要换写法。
- **未决试验**：哪些形态能力还没验证过，需要先做小样。

本阶段新增：叙事职责、形态假设、运动预算与未决试验。不产出形、材质、光或镜头层字段。

## 本阶段规则

### `STY`

| ID | Class | Knowledge |
|---|---|---|
| STY-01 | craft_default | State the promise as protagonist, pursuit, costly opposition, and recurring payoff. |
| STY-02 | craft_default | Build a repeatable conflict engine whose pressure can change power, knowledge, relationship, exposure, cost, or time. |
| STY-03 | reviewed_invariant | A beat/episode escalation must change a story state rather than repeat the same pressure louder. |
| STY-04 | craft_default | Enter with pressure active and deliver part of the promised payoff before the outgoing hook. |
| STY-05 | structural_invariant | Incoming/setup/payoff references resolve to known records or are explicitly unresolved. |
| STY-06 | taste_option | Hook, arc shape, episode count, and climax position follow the creator's format. |
| STY-07 | reviewed_invariant | Character/scene merges preserve dramatic function, knowledge permissions, relationship position, and causal bridges. |
| STY-08 | craft_default | Translate exposition through consequential behavior, evidence, spatial pressure, or dialogue strategy before adding neutral explanation. |
| STY-09 | reviewed_invariant | A reveal/reversal grows from established facts and changes a plan, explanation, relationship, or costly choice. |
| STY-10 | craft_default | Establish the recurring-payoff promise once the opening pressure makes it legible; an opening may imply, delay, or state it according to genre and creator intent. Plan each outgoing hook from the episode's local result rather than repeating a type by quota. |
| STY-11 | craft_default | Build only the prior-world reservoir needed to predict present choices, then enter where an established strategy begins to create visible cost. |
| STY-12 | reviewed_invariant | Claimed character progression cites a pressure test, choice or retreat, local result, cost, and changed visible strategy. |
| STY-13 | reviewed_invariant | Each episode produces a local dramatic result before its outgoing hook; serialization cannot rely only on pausing an unfinished action. |
| STY-14 | craft_default | Maintain compact serial memory for character strategy/state, relationships, information permissions, setup debt, rhythm, and exact handoff. |
| STY-15 | reviewed_invariant | Calibrate each information release to what its visible carrier directly supports, while keeping unproved identity, cause, motive, or mechanism explicit as unresolved inference. |
| STY-16 | craft_default | Before scene work, estimate each planned episode's shot and duration magnitude from the project's own accepted ratios, and resolve order-of-magnitude outliers in the map; the estimate informs the creator and never blocks delivery. |
| STY-17 | reviewed_invariant | A premise device separates its creator-accepted contract (scope, failure conditions, cost, whether its own declarations are reliable) from in-fiction disclosure; the contract is accepted before the device first takes effect, while disclosure may lag, stay partial, or be misstated by a character or the device itself. Every later device ability or exemption traces to a contract clause—an untraceable one is retroactive widening—and the audience not yet knowing every boundary is never itself a defect. |
| STY-18 | structural_invariant | A multi-episode source is read through a verified exact-byte episode index; source drift invalidates every old span, and resume derives missing IDs from the current episode map rather than a last-completed guess. |
| STY-19 | craft_default | For a multi-episode source, the Agent chooses each batch from this file's measured episode spans, semantic complexity, and available context, then reads only the current slices and compact accepted handoff; no fixed episode quota substitutes for that judgment. |

规则分级由高到低：`structural_invariant`（结构缺陷，阻断）、
`reviewed_invariant`（需证据判断）、`craft_default`（常用做法，可覆盖）、
`taste_option`（创作者选择，不作缺陷）。创作者已接受的事实优先于本表。
