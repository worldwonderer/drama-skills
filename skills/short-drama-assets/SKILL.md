---
name: short-drama-assets
description: "从短剧剧本拆解并统筹角色/造型、场景/视图、道具/状态和跨场连续性。用户说‘拆角色/场景/道具’、‘做资产表/角色表/场景表/道具表’、‘判断复用还是新变体’、‘更新造型/道具状态’，或拿现成剧本直接做视觉资产准备时使用；本 skill 不写资产图提示词，也不生成图片或视频。"
license: MIT
---

# 短剧资产拆解

把剧本文字变成**可追溯、可复用、能接续状态**的生产资产。重点不是数出
多少人名和名词，而是回答：屏幕上具体需要什么、它与已有资产是不是同一个、
此刻是哪种造型/视图/状态，以及变化怎样传到下一场和下一集。

## 先定位套件

从本技能目录读取 `suite-ref.json`，按其中相对 `core_manifest` 定位唯一同级主技能与
套件清单；确认声明的 core、contract、recipe 和清单 hash 一致后再读写项目。
随后执行主技能的 [运行时预检](../short-drama/references/runtime-preflight.md)：先恢复事务、读取状态，再进入本阶段。
涉及制作形态时按需读取 [production-form-profiles.md](../short-drama/references/production-form-profiles.md)，只投影本阶段负责的形、层、材质、光、运动或声音。

## 边界

- 资产事实只来自已接受剧本、已有 bible、连续性和创作者补充；不擅改剧情。
- 始终读取 `short-drama.json#/creator_authority/{visual_direction,production_profile}` 中状态为
  `accepted` 的视觉方向与制作形态：形态决定哪些身份锚点在本项目里根本可被表达——以剪影为
  识别通道的形态与以面部结构为识别通道的形态需要不同的锚点集合；若状态为 `unset`，就向
  创作者给出选择，不从对话记忆补造。形态可以改变锚点的表达通道与颗粒度，不得反过来改写
  已接受的身份、地理、持物归属、可读文字政策或故事状态。
- 只拥有 Character/Look、Location/View、Prop/State、occurrence reconciliation
  和资产状态 delta。知识/信念/目标/关系/情绪等 story-state 可以在连续性
  ledger 中被追踪，但只是带 write/develop source pointer 的投影，不是 assets
  的第二份真相。剧本语义归 `short-drama-write`，镜头手位/走位归
  `short-drama-storyboard`，图片提示词归 `short-drama-image-prompts`。
- 可直接接收现成剧本，不强迫补创意开发、故事引擎或集纲。
- 只产出文本/JSONL；不调用图片、视频、音频模型或 provider API。

共享契约见 `../short-drama/references/contract-and-ownership.md`；只在需要时加载
下列专项参考，不要一次读完所有文件。

## 入口判断

1. **已有已接受的 `screenplay.md` 与 index**：直接拆解。
2. **已有资产 bible，只需补集内出现与状态**：读取旧 ID，先判复用，再提新项。
3. **只有非 canonical 的中文剧本**：保留原字节到 `inputs/`；调用 write owner
   产生最小规范化预览、语义 diff 和未映射片段。创作者接受前不发布
   `screenplay.md`，拒绝也不改变原稿。接受后直接回来拆资产，不虚构 development。
4. **用户只要局部结果**（如“拆本场道具”）：仍执行 occurrence → decision，
   但只呈现所问范围及它依赖的连续性，不用强迫走完整项目流程。

缺少 index 时，不凭行号冒充稳定来源；先请 write owner 对剧本建立 block ID/hash。

## 工作流

### 1. 先读事实边界

读取本集剧本/index、已有 bible、上集 outgoing、创作者参考与文本政策，以及已接受的
视觉方向与制作形态。标记哪个版本已被接受。不要把旧 prompt、旧分镜或文件名当成资产真相。

### 2. 逐块提 occurrence，暂不创建资产

按 source block 收集出镜或生产必需的角色、地点、道具及其显式状态：造型、伤污、
所有者、持物手、位置、损坏、内容物、开闭、可读文字、时段、天气、光态和剧情作用。
每条 occurrence 先保持“剧本怎样写”的颗粒度，再与资产表对齐。

- 不猜“她”“那个人”“另一把钥匙”指谁；保留原称谓和证据，状态设为 unresolved。
- 区分出镜、画外声、屏幕/照片呈现、仅被提及；被提及不等于要做视觉资产。
- 不把每个名词都建档。只保留影响识别、复用、提示词、镜头或连续性的事实。
- occurrence 不反向 hash 引用未来 decisions；先写 locator，decision 再单向引用
  occurrence 的 exact snapshot。

方法与反例见 `references/occurrence-extraction.md`，记录形状见
`assets/occurrences.example.jsonl`。

### 3. 再做身份判断

把 occurrence 与已有 bible 逐项比对，只给四种提案：

- `reuse`：持续身份和本次所需变体均已存在；
- `new_variant`：同一身份，新增 Look/View/State；
- `new_asset`：持续身份、空间地理或物体功能/形制确实不同；
- `unresolved`：证据不足或多个候选都成立，等待创作者选择。

先问“同一个东西什么没变”，再问“这次什么变了、为何变、何时有效”。服装、伤势、
湿污、灯光、道具开合通常不是新身份；相机角度和瞬时姿势通常连新变体都不是。
不要为了少建记录而合并真正不同的资产。详见
`references/identity-vs-variant.md` 与 `assets/decisions.example.jsonl`。

### 4. 写最小可识别 bible

按类别沉淀，身份锚点与临时状态绝不混写：

- Character / Look：`references/character-and-look.md`；例见
  `assets/character-look.example.jsonl`
- Location / View：`references/location-and-view.md`；例见
  `assets/location-view.example.jsonl`
- Prop / State：`references/prop-and-state.md`；例见
  `assets/prop-state.example.jsonl`

每个新变体记录 base、变化、原因与有效范围。只写能帮助再次认出、复用、提示词
编写或连续性检查的事实；不堆砌“高级、精致、电影感”等泛化修饰。

### 5. 写变化，不复制整本 bible

为交接所需的资产状态变化记录 before、after、剧本原因、开始/结束边界和受影响 binding。
重点检查造型/伤势、持物/所有权、道具状态、地点时段/天气/光态以及跨集 outgoing。
若为审查需要把知识或关系状态放进 ledger，只保存权威字段的 artifact/hash/
field pointer 及必要投影；修订仍路由到 develop/write owner。
镜头内部姿势、视线、左右手和站位由 storyboard 边界拥有；资产记录只引用，不抢写。

详见 `references/continuity-delta.md` 与 `assets/continuity.example.jsonl`。

### 6. 给创作者看“决定”，而非只给清单

提交接受预览，按以下顺序呈现：

1. 建议复用（为什么是同一个）；
2. 建议新增变体（没变什么、变了什么、原因与有效期）；
3. 建议新增身份（区分它的持久证据）；
4. 未决项（原文证据、候选、每个选择的下游影响）；
5. 连续性变化与需带入下一集的 outgoing。

创作者可逐项接受、改名、合并、拆分或暂缓。**creator acceptance 是独立事实**：
抽取完成、结构校验通过、review 通过都不能替代创作者接受。只发布被接受的身份和
变体；任何 unresolved 都不得编译到图片提示词或分镜 binding。
若用户要求一次查看全链而中间没有接受回合，可生成 candidate 预览链：下游必须
标 `provisional`，ArtifactRef 加 `authority:candidate`，不得伪造 creator decision/
accepted snapshot，且不得交付。

### 7. 发布与修订

发布至 `bible/*.jsonl` 及 `episodes/<EP>/assets/{occurrences,decisions,continuity}.jsonl`。
每个非权威重复值都携带 owner artifact/hash/field pointer。资产修改后只标记依赖该
ID/variant 的提示词、镜头和 review 为 stale；不要重写无关资产或 screenplay。

## 规则分类与阻断

- `structural_invariant`：occurrence 必有 source block/hash；decision 必属四类；
  variant 有 base/cause/validity；binding 必须解析到已接受 ID。可机械阻断。
- `reviewed_invariant`：不可猜含混指代；不可把临时状态混入身份；delta 的剧情原因
  必须由证据支持。独立 reviewer 引用证据判定，owner 不自批。
- `craft_default`：身份不变时优先复用/变体；只跟踪下游有用事实。创作者可说明覆盖。
- `taste_option`：群演建为个体还是群组、同址空间拆分颗粒度、蒙太奇式跳变方式，
  由制作策略选择，不单独阻断。

## 完成条件

发布 C2 前使用 `references/asset-review-checklist.md`：来源和引用可解析；每个
occurrence 有明确 decision；未决项保持未决；身份/变体边界可信；连续性能够从
incoming 走到 outgoing；创作者已经接受本次变更。最终 approval 必须交给
`short-drama-review`，本 skill 只修订自己拥有的资产事实。
