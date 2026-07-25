---
name: short-drama-image-prompts
description: 为已确认的短剧角色、造型、场景、视角、道具和状态编写或修改可复制的通用资产图片提示词。用户提到角色设定图、三视图、人物参考图、场景设定图、场景空镜、场景板、道具图、造型或状态变体、局部编辑提示词，或要求用自然语言修改现有图片提示词时直接使用；只产出结构化规格与 Markdown 文本，不生成图片，也不调用模型或供应商接口。
license: MIT
---

# 短剧资产图片提示词

把已接受的资产事实写成“能认出、能复用、能区分状态”的参考图提示词。这里的产物是提示词，不是图片。

## 先定位套件

从本技能目录读取 `suite-ref.json`，按其中相对 `core_manifest` 定位唯一同级主技能与
套件清单；确认声明的 core、contract、recipe 和清单 hash 一致后再读写项目。
随后执行主技能的 [运行时预检](../short-drama/references/runtime-preflight.md)：先恢复事务、读取状态，再进入本阶段。
涉及制作形态时按需读取 [production-form-profiles.md](../short-drama/references/production-form-profiles.md)，只投影本阶段负责的形、层、材质、光、运动或声音。

## 进入条件与边界

- 可从现成项目直接进入，不要求先做故事开发；先定位 `short-drama.json` 和版本一致的主技能。
- 所有权、下游文件何时变为 `stale` 或项目状态不清时，才读取主技能的
  [所有权契约](../short-drama/references/contract-and-ownership.md)；需要定位规则 ID
  或解释审查问题时，才读取 [规则索引](../short-drama/references/knowhow-index.md)
  中相关 `IMG-*`、`AST-*`、`CON-*` 条目。
- 输入必须是已接受的 `CHAR/LOOK`、`LOC/VIEW` 或 `PROP/PSTATE` 精确 ID 与快照引用。未决指代、冲突变体或未知状态退回 `$short-drama-assets`，不代猜。
- 始终读取 `short-drama.json#/creator_authority/{visual_direction,production_profile}` 中状态为
  `accepted` 的视觉方向与制作形态：它决定本阶段可执行的形状语言、线条/表面处理、材质对光的
  响应与层拆；若状态为 `unset`，就向创作者给出选择，不从对话记忆补造，也不用默认审美冒充
  已接受形态。形态只决定可执行词汇，不决定身份、地理、持物、可读文字政策与故事状态。
- 若创作者明确要求全链预览，可对唯一且没有 `unresolved` 问题的资产提案写
  `candidate` 提示词；来源引用加 `authority:candidate`，文档标明 `provisional` 和
  `not delivery-ready`，不得声称已经 `accepted`。
- 本技能只负责构图、提示词专用约束，以及局部修改中的修改项 `changes` 和保留项 `preserve`；身份、地理和资产状态仍由资产技能负责。
- 始终保留不绑定供应商的通用提示词。不得创建图片、媒体任务、接口请求、模型参数、轮询记录或画质结论。

## 按任务加载资料

| 任务 | 必读资料 |
|---|---|
| 新建任意资产提示词 | [通用配方与视觉锚点](references/common-recipe.md) |
| 人物设定图 | 加读 [人物与造型](references/character-and-look.md) |
| 造型、视角或道具状态的版本 | 加读 [造型与状态变体](references/look-and-state-variant.md) |
| 场景空镜或不同观察方向 | 加读 [场景与地理](references/location-plate.md) |
| 道具或 道具状态 | 加读 [道具、尺度与文字](references/prop-plate.md) |
| 局部修改或自然语言改提示词 | 加读 [编辑与修订](references/edit-and-revision.md) |
| 自检、复核、失败诊断 | [审查量表与合成案例](references/review-and-fixtures.md) |
| 生产端三视图/场景方位/物品版式配方 | [生产资产图配方](references/production-sheet-recipes.md) |
| 参考图只决定身份、构图或尺度等指定内容 | [参考图、揭示时机与补拍](../short-drama/references/reference-media-and-pickups.md) |

写规格时使用 [结构化规格模板](assets/image-prompt-spec.jsonl.md)，交付文本使用 [Markdown 模板](assets/image-prompts.md)。只加载当前类型所需资料。

## 工作流

### 1. 确认目的而非先堆风格词

先回答：这张参考图以后要帮助谁保持什么一致？选择一种主类型：

- `character_sheet`：识别同一人物的一套已接受造型；
- `location_plate`：固定一个地点的观察方向和地理；
- `prop_plate`：固定道具的尺度、形制、功能和当前状态；
- `look_state_variant`：在同一身份上突出有因果与有效范围的差异；
- `edit_delta`：对精确目标做有边界的修改，同时声明保留集。

一个规格只承担一个主要复用目的。需要不同造型、观察方向或状态时分开写，不把互相冲突的状态揉成“大全图”。

### 2. 整理输入

从接受快照记录：

1. 准确的资产 ID 与版本 ID；
2. 稳定识别点与本版本的变化；
3. 来源的 `artifact/hash/field`；
4. 用途、构图、背景、光线与文字政策；
5. 每张参考图的准确引用、单一作用、可参考内容、不可照搬内容与检查状态；只有
   创作者/参考图权利人的说明，或经过授权的输入参考图检查，才能给出像素/文字结论；
   前者写 `creator_described`，后者写 `visually_inspected`，都没有时保持 `unverified` 并列出风险；
6. 必须出现、必须保持和明确排除的内容；
7. 未决定项以及创作者的明确选择。

只带入当前操作必需的信息。私有引用在文本中仅写 `REF-*`，不泄露本地路径、网址或原始内容。

### 3. 按重要性写规格与通用提示词

按“用途/主体 → 识别点 → 状态差异 → 构图/方向/尺度/空间关系 → 材质/色彩/光线 → 背景 → 文字政策 → 排除/保留”组织。身份、地理、尺度和可读文字等重要事实先于“精致、电影感”等空泛审美词。

- `structural_invariant`：绑定准确的已接受 ID/版本；局部修改写清 `target/hash/region`、`changes`、`preserve` 和连续性影响；`readable` 不得与全局 `no-text` 并存。
- `reviewed_invariant`：人物在一个规格中保持同一身份和一套连贯造型；场景保持清楚的地理；道具保持可辨尺度、形制与功能。
- `reviewed_invariant`：参考图只决定已经声明的内容；构图、尺度或效果参考不得顺带改写身份、文字、人数或故事状态。
- `craft_default`：用少量可观察、彼此不重复的识别点；负面约束只防止当前风险，不写长篇万能禁词。
- `taste_option`：写实/绘制、镜头审美、色彩浓度、文风密度由创作者决定；一旦被写入
  `creator_authority` 并接受，它就不再是本阶段可自选的口味，而是必须投影的形态约束。

### 4. 做矛盾与可复用性审查

逐项检查：锚点是否互相打架；临时状态是否污染身份；空间关系是否能画在同一画面；构图是否服务参考用途；文字政策是否可执行；排除项是否误杀必需事实。语义质量用证据复核，不用词数、形容词数或固定提示词长度硬判。

### 5. 让创作者接受，再写正式产物

先展示人能读懂的预览：绑定对象、关键选择、警告与可复制提示词。接受后写：

- `episodes/<EP>/assets/image-prompt-specs.jsonl`：权威规格；
- `episodes/<EP>/assets/image-prompts.md`：由已接受规格和配方 `hash` 重新生成的文本版本。

跨文件发布遵循主技能的提交与恢复流程；不得以半成品覆盖已接受版本。

## 自然语言修订

用户可直接说“外套保持不变，只把袖口变湿”“场景里不要出现演员”。不要让用户编辑 JSONL。

1. 把请求整理成按字段列出的修改方案，并标记哪些事实由上游负责；
2. 展示 `before → after`、受影响的绑定和连续性，以及无法对应或会丢失的内容；
3. 等待接受或拒绝；拒绝时原规格与 Markdown 文本不变；
4. 接受后先提交规格，再从规格重新导出 Markdown 文本。

若 Markdown 文本被手改：`restore` 先预览恢复；`adopt` 只把能完整对应字段的改动变成
规格提案。无法对应的文句会阻断 `adopt`，绝不让派生文本反向成为事实来源。详见
[编辑与修订](references/edit-and-revision.md)。

## 完成标准

- 每个规格能追溯到准确的已接受资产与版本，且通用提示词可独立复制；
- 类型配方完整，重要事实在泛化审美词之前，无未决占位或内部工作指令；
- 局部修改同时说明改什么、保留什么、会影响哪些连续性；
- 已运行本地结构检查，再交给独立 `$short-drama-review` 结合来源资料审查内容；
- 交付中没有媒体、远程执行任务或接口信息、远端 ID、私有对应表或“生成成功”声明。
