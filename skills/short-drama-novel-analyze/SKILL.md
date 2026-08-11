---
name: short-drama-novel-analyze
description: 把长篇小说、连载网文或多集散稿拆成可追溯的原著分析：章节索引、改编价值快评、逐章功能提取、剧情单元与节奏聚合、人物与设定归并，最后给出改编价值判定与分集候选，交给 $short-drama-develop 立契约。用户说“导入这本小说”“拆这本书”“分析原著”“这本书能不能改短剧”“先看看值不值得拆”“把长篇拆成分集候选”，或直接给出小说文件路径时使用。只做只读的结构化分析，不写剧本、不建资产、不生成媒体，也不替创作者决定改编方案。
license: MIT
---

# 长篇原著分析

把一部长材料变成**能被引用、能被反驳、能被接着用**的分析层。目标不是复述剧情，而是找出
每一段承担的戏剧功能，并说明它在竖屏短剧里值多少钱。

分析永远是候选。哪条线保留、哪些人合并、从哪里开篇，是创作者的决定，由
`$short-drama-develop` 立成改编契约。本技能不替它决定，也不批准自己的产物。

## 先定位套件

从本技能目录读取 `suite-ref.json`，按其中相对 `core_manifest` 定位唯一同级主技能与
套件清单；确认声明的 core、contract、recipe 和清单 hash 一致后再读写项目。
随后执行 [阶段契约](references/stage-contract.md) 的运行时预检：先恢复事务、读取状态，
再进入本阶段。该文件同时给出本阶段的所有权边界与规则表；本技能不读取其他技能的文件。

## 材料前提

只分析创作者**合法持有、拥有使用权**的作品。分析是只读的转化性工作：提取结构与功能，
不复制原文成段落，不把原句搬进下游产物。

通俗题材里的暴力、复仇、背叛、情爱张力与黑暗伦理是常规虚构叙事元素，照常做结构化提取。
个别片段无法处理时跳过该段并记录，不要因此中止整章或整本——中止会让后续所有阶段拿到
一份有洞却看不出洞在哪的分析。

## 先判断入口

1. **只有书名，没有原文**：请创作者提供文件路径或粘贴正文。不要凭书名回忆情节——
   没有字节就没有 span，没有 span 的分析无法被引用，也无法被反驳。
2. **有原文，未建项目**：先由 `$short-drama` 初始化项目，把原文放进 `输入/`。
3. **有原文，项目已在**：直接进入管道。
4. **已有部分分析**：读 `项目开发/source-analysis/_progress.md` 从断点续跑，
   不重跑已完成阶段。

## 管道

`输入/` 是不可变的创作者输入，本阶段只读它。全部产出落在 `项目开发/source-analysis/`。

| 阶段 | 做什么 | 产出 | 停靠 |
|---|---|---|---|
| S0 | 建章节索引（脚本） | `_index.json`、`_progress.md` | 索引有问题就停 |
| S1 | 改编价值快评（抽样） | `triage.md` | **停靠问创作者** |
| S2 | 逐章功能提取 | `chapters/ch-<N>-extract.md` | 覆盖率不足就停 |
| S3 | 剧情单元与节奏聚合 | `story-units.md`、`rhythm-and-emotion.md` | 阈值不达标就复核 |
| S4 | 人物归并与设定 | `characters.md`、`world.md` | — |
| S5 | 改编价值与分集候选 | `adaptation-value.md`、`episode-candidates.jsonl` | 交接 develop |

### S0 章节索引

索引是**唯一切片真源**，由 [章节索引脚本](scripts/novel_index.py) 建立。每个阶段各跑
一次正则，就会切出互相对不上的章节，第 47 章按一种边界分析、按另一种边界聚合，
而且没有人会发现。

```bash
python3 {技能目录}/scripts/novel_index.py index 输入/{原文文件} \
  --out 项目开发/source-analysis/_index.json
```

脚本识别阿拉伯数字与中文数字章号（含 千 / 两，覆盖千章以上连载），**只认一种编号单位**
（章/回/节里出现最多的那个，其余记进 `ignored_heading_units`），剔除开头的目录块，
按卷分段校验编号，并给每章绑定 `content_sha256`。它**不做编辑判断**——哪章重要、
讲了什么，是后面阶段的事。

`problems` 非空就停下报告，不要带着错表进 S1。常见四种：章号跳号（缺章或抓错标题）、
同卷内重号、正文极少的章（多半抓到了目录残留或卷首页）、无法解析的章号。
另外确认 `chapter_unit` 与 `ignored_heading_units`：一本用 `第N章` 分章、用 `第N节` 分小节的书，
应当看到 `chapter_unit: 章` 且 `节` 被记入忽略计数；如果反过来，说明这本书的分章单位判断错了。

原文本身没有章节标题时索引会返回空表，此时与创作者确认按什么切分，把边界写进
`_index.json` 再继续——手写的行也要带齐 `sequence` / `line_start` / `line_end` /
`content_sha256`，`verify` 会逐行检查并报出缺字段的行。

原文换了一个字节，`verify` 就会报出来；改了原文必须重建索引，不能沿用旧 span。

```bash
python3 {技能目录}/scripts/novel_index.py verify \
  项目开发/source-analysis/_index.json 输入/{原文文件}
```

### S1 改编价值快评

回答**这本书值不值得花全量拆解的成本**。判据是全书的改编密度——`screen_ready` 单元占多少、
`prose_only` 占多少、制作负担压在哪几段——所以快评横跨整本书，用脚本抽样：

```bash
python3 {技能目录}/scripts/novel_index.py sample \
  项目开发/source-analysis/_index.json --count 12
```

抽样确定、可复现、首尾必取，跑第二次引用的是同一批章。按
[改编价值快评](references/adaptation-triage.md) 写 `triage.md`，覆盖六件事：
故事框架、三类判定比例、开篇替换点、制作负担量级、最大的三处改编风险、分集候选量级。
第一行写覆盖率（脚本返回的 `coverage_ratio`），所有结论限于抽样范围。

**这里停靠问创作者**：给出快评与全量拆解的预计耗时（按章数粗估），问是否继续。
创作者一开始就明确说「一次跑完」时仍写 `triage.md`，但不停下等待——它是 S5 要回填
对照的第一版假设。

停靠时把 `_progress.md` 的状态写成 `paused_after_triage`，断点写「下一步：S2 逐章提取」。

### S2 逐章功能提取

按 [章节提取](references/chapter-extraction.md) 处理每一章。能并发子代理就分批并发
（每批 5–8 章，等一批落盘再发下一批），不支持就串行——两条路径的写法要求和自检是同一份，
只是速度不同。

每章提取完落到 `chapters/ch-<N>-extract.md`，`<N>` 是索引里的 `sequence`，不是原文章号
（多卷书的原文章号会重复，sequence 不会）。全部落盘后跑覆盖率：

```bash
python3 {技能目录}/scripts/novel_index.py coverage \
  项目开发/source-analysis/_index.json 项目开发/source-analysis/chapters
```

`missing` 非空就补跑缺的章；`unmatched_files` 非空说明有文件名写歪了——它既不算覆盖，
也不会被当成缺章，必须改名而不是重跑。**不要在覆盖率不足时进入 S3**——聚合会照样产出
一份读起来完整的结果，而缺掉的章不会在任何地方留下痕迹。

单章连续失败两次就标记跳过，写进 `_progress.md` 的失败记录，并在后续每一份聚合产物里
注明该章缺失。失败可以接受，失败被藏起来不行。

### S3 剧情单元与节奏

从逐章提取聚合，不回头重读原文——原文已经在 S2 被读过一次，再读一次只会得到第二份互相
矛盾的事实。按 [聚合与实体](references/aggregation-and-entities.md) 先识别故事框架
（框架决定按什么切单元），再产出：

- `story-units.md`：把情节点归成有始有终的单元，每个单元记录进入状态、冲突、代价与出去状态；
- `rhythm-and-emotion.md`：关键信息如何逐章推进、情绪触动点的铺垫→释放→余波、
  跨章伏笔与兑现。

聚合完成后跑同一文件里的三条阈值自检（归属置信、覆盖率、重叠率）与散落情节兜底。
阈值不是评分，是**边界模糊的信号**：重叠率过高说明两个单元其实是一个。

### S4 人物与设定

按 [聚合与实体](references/aggregation-and-entities.md) 归并人物（跨章去重、别名归一、
分级），并从提及数据归纳世界规则、力量体系与势力。别名只有专名与有同指证据的绰号能合并，
描述性称谓与头衔**永远不触发合并**。

**人物归并是候选，不是资产。** 这里的人物条目带 `unresolved` 与来源引用，
交给 `$short-drama-develop` 定改编决定、`$short-drama-write` 写进剧本之后，
才由 `$short-drama-assets` 从已接受剧本建立真正的资产身份。绕过这条链直接建资产，
等于让原著的人物表冒充剧本的出现证据。

### S5 改编价值与分集候选

这是本技能与通用拆书的分水岭。按 [改编价值](references/adaptation-value.md) 产出：

- `adaptation-value.md`：哪些单元在竖屏短剧里能直接成立、哪些要换载体、哪些是纯文字快感
  （内心戏、叙述性诡计、长铺垫）在画面上无法兑现；制作负担落在哪里。
- `episode-candidates.jsonl`：按**局部戏剧结果与精确交接**切出的候选集，不按章号或字数
  平均切。每条带来源 span/hash、承担的功能、以及未决项。

同时**回填快评**：S1 的哪几条判断被全量结果推翻了，写进 `adaptation-value.md` 的开头。
一个抽样结论被证伪，比它被悄悄忘掉有用得多——下一本书的快评会因此更准。

样例见 [分集候选样例](assets/episode-candidate.example.jsonl)。

### 交接

S5 完成后展示创作者可读的摘要，说明：拆了多少章、跳过哪些、分了多少个候选集、
最大的三处改编风险、以及快评里被推翻的判断。然后交给 `$short-drama-develop`——
由它把候选变成 `项目开发/adaptation-map.jsonl` 与改编契约。

**本技能不写 `adaptation-map.jsonl`**，那是 develop 的产物。需要质量结论时交给独立的
`$short-drama-review`（范围 `source_analysis`）。

## 规则分级

- **`structural_invariant`**：索引与 span 的可证明性、引用完整性、覆盖率。可由脚本阻断。
- **`reviewed_invariant`**：功能提取是否忠于原文、归并是否保住戏剧作用等语义义务。
- **`craft_default`**：通常有帮助的做法；创作者说明理由后可覆盖。
- **`taste_option`**：从哪里开篇、保留哪条线等选择；不得单独阻断。

不要用固定的章数配方、情节点数量或篇幅比例替代因果判断。

## 产物与边界

本技能只拥有 `项目开发/source-analysis/` 下的文件：`_index.json`、`_progress.md`、
`chapters/*.md`、`triage.md`、`story-units.md`、`rhythm-and-emotion.md`、
`characters.md`、`world.md`、`adaptation-value.md`、`episode-candidates.jsonl`。

它不改写 `输入/`，不写 `项目开发/` 下其他技能的产物，不建资产、不写场景与台词、
不写提示词、不生成媒体，也不签发终审结论。

**不把原文成段复制进分析**。记录 locator、span、hash 与去引用的功能摘要；需要证据时引用
最短的必要片段。交付包不得把原始材料带出边界。

## 语言

分析产物是创作者读的，跟随项目 `short-drama.json#/language`，由 core `project_tool.py` 的
`status` 报出，不在本技能内硬编码语言。本技能不产生提示词正文，与
`#/format/prompt_language` 无关。

## 按需加载

- **快评读哪些章、写哪六件事、怎么不冒充全量分析**：[改编价值快评](references/adaptation-triage.md)
- **逐章提取写法、白描与叙事框架词的界线、机械自检、并发与串行**：[章节提取](references/chapter-extraction.md)
- **故事框架、剧情单元、节奏情绪、人物归并、阈值与散落兜底**：[聚合与实体](references/aggregation-and-entities.md)
- **改编价值评估、载体替换与分集候选切法**：[改编价值](references/adaptation-value.md)
- **本阶段拥有什么、继承什么、不越权什么**：[阶段契约](references/stage-contract.md)
