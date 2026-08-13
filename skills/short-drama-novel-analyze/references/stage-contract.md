# 原著分析阶段契约

## 目录

- [运行时预检](#运行时预检)
- [所有权边界](#所有权边界)
- [材料授权与只读转化](#材料授权与只读转化)
- [本阶段规则](#本阶段规则)

本文件是本技能的自包含契约：预检、所有权、材料边界与规则表都在这里，
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
   原样并先处理冲突，不要绕过 WAL、手改状态文件或假定上次写入成功。`status` 同时报出
   项目语言，创作者可读的分析产物跟随它。
3. **只通过公开生命周期写入**：负责人用 `publish` 原子发布候选，并给每个外部结构化引用
   提供精确 input hash。上游接受引用不继承候选状态。每次修订后重新运行适用的结构校验，
   并让下游刷新旧 hash。Agent 与索引脚本先写 `项目开发/source-analysis/_work/` 候选，
   校验通过后再发布到正式路径；不得直接覆盖正式分析产物。
4. **原文按整文件绑定**：`输入/` 下的原始材料没有可机器校验的记录身份，按整文件 hash
   绑定。原文一旦变化，`_index.json` 与其后全部分析都必须重建——沿用旧 span 会让每一条
   引用指向错误的位置，而且不会报错。

## 所有权边界

- **本阶段拥有**：`项目开发/source-analysis/` 下的章节索引、改编价值快评、逐章提取、
  剧情单元、节奏情绪、人物与设定候选、改编价值评估与分集候选。
- **本阶段继承**：创作者提供的原始材料与授权说明、项目语言、已接受的形式约束与制作形态。
- **本阶段不越权**：不改写 `输入/`；不写改编契约、创作简报、故事引擎或
  `adaptation-map.jsonl`（`$short-drama-develop` 拥有）；不建资产身份；不写场景、台词、
  分镜与提示词；不生成媒体；不批准自己的产物。

分析层与决策层分开的理由很直接：**分析可以被推翻，契约不能**。把两者写进同一份文件，
创作者就再也无法只推翻分析而保留已确认的改编承诺。

## 材料授权与只读转化

- 只处理创作者声明**合法持有、拥有使用权**的作品；授权不清时保留问题，不替创作者定案。
- 分析是转化性的：提取结构与功能，不复制原文成段落，不模仿原作文风生成新文本。
- 引用采用最短必要片段，并绑定 span 与 hash。功能摘要必须是**去引用**的重述。
- 通俗题材的暴力、复仇、背叛、情爱张力与黑暗伦理照常提取；个别片段无法处理时跳过并记录，
  不中止整章或整本。
- `输入/` 与其中的原始材料不进入交付包。

## 本阶段规则

### `NVA`

| ID | Class | Knowledge |
|---|---|---|
| NVA-01 | structural_invariant | All stages slice the source through one chapter index bound to the source hash; a changed source invalidates the index and everything derived from it. |
| NVA-02 | structural_invariant | Every extracted claim carries a source locator and span; a claim with no span cannot be cited downstream. |
| NVA-03 | structural_invariant | Aggregation may not start while chapter coverage is incomplete; missing chapters are named in every aggregate that inherits the gap. |
| NVA-04 | structural_invariant | Analysis records de-quoted function summaries, never copied source paragraphs. |
| NVA-05 | structural_invariant | A sampled stage states its own coverage and confines every claim to the chapters it read. |
| NVA-06 | reviewed_invariant | A function summary states what a passage does to character choice, information, power or relationship, not what happens in it. |
| NVA-07 | reviewed_invariant | Hard facts — levels, counts, distances, who said what, which chapters a character appears in — trace back to a description line or the source; an unsupported one is written as unstated, never filled in by plausibility. |
| NVA-08 | reviewed_invariant | Character merges preserve dramatic role, knowledge scope, relational position and causal bridge; only proper names and evidenced nicknames may merge, never descriptors or titles. |
| NVA-09 | reviewed_invariant | Adaptation value distinguishes what the screen can show from what only prose can deliver, and names the new carrier for each function it keeps. |
| NVA-10 | reviewed_invariant | Episode candidates are cut on local dramatic result and precise handoff, not on chapter count or word budget. |
| NVA-11 | craft_default | Triage a deterministic spread of chapters across the whole book and stop for the creator before committing to a full pass. |
| NVA-12 | taste_option | Where to open, which line to keep and which ending to promise remain creator choices; analysis may argue but never blocks. |

规则分级由高到低：`structural_invariant`（结构缺陷，阻断）、
`reviewed_invariant`（需证据判断）、`craft_default`（常用做法，可覆盖）、
`taste_option`（创作者选择，不作缺陷）。创作者已接受的事实优先于本表。
