---
name: short-drama-storyboard
description: 把已接受的中文短剧剧本和资产转成原文落实表、有戏剧动机的镜头、连续性边界与冻结关键帧提示词。用户提出“拆分镜/设计镜头/做镜头表”“写首帧/关键帧提示词”“检查轴线、站位、视线、持物连续性”，或需要在不生成媒体的前提下把竖屏短剧、漫剧的剧本内容转成可拍的画面时使用。
license: MIT
---

# 短剧分镜与冻结关键帧

先守住故事内容，再安排原文落实、空间和镜头，最后写冻结关键帧。不在这里写随时间
变化的运动提示词，也不改写剧本或资产事实。中文项目使用中文镜头目的、边界说明和
可复制提示词；ID 和字段名保持原样。

## 先定位套件

从本技能目录读取 `suite-ref.json`，按其中相对 `core_manifest` 定位唯一同级主技能与
套件清单；确认声明的 core、contract、recipe 和清单 hash 一致后再读写项目。
随后执行主技能的 [运行时预检](../short-drama/references/runtime-preflight.md)：先恢复事务、读取状态，再进入本阶段。
涉及制作形态时按需读取 [production-form-profiles.md](../short-drama/references/production-form-profiles.md)，只投影本阶段负责的形、层、材质、光、运动或声音。

## 按需读取资料

始终读取：

- 状态为 `accepted` 的 `screenplay.md` 与 `screenplay-index.jsonl`；
- 状态为 `accepted` 的资产、版本与相关连续性；
- `short-drama.json#/creator_authority/visual_direction` 中状态为 `accepted` 的视觉方向；
  若状态为 `unset`，就向创作者给出选择，不从对话记忆补造。

设计原文落实、场面调度、摄影机和剪切时读
[shot-craft.md](references/shot-craft.md)；只有写冻结帧时读
[keyframe-craft.md](references/keyframe-craft.md)。需要制作端的时长依据、景别与运镜
词表或时间片写法时读 [production-shot-grammar.md](references/production-shot-grammar.md)。
涉及背影、裁切、遮挡、画外或延迟揭示时读
[参考媒体权限与补拍契约](../short-drama/references/reference-media-and-pickups.md)。
只有所有权或过期传播不清楚时，才读核心所有权契约。

- 竖屏多人、单房对白、证据揭示、群体轴线或门内外视角：
  [blocking-playbooks.md](references/blocking-playbooks.md)
- 需要查看“剧本 → 原文落实 → 镜头 → 关键帧”的完整正例，或对白表演括注
  `（情绪）` 怎样同源投影到本镜表演状态与下游 `delivery`：
  [screenplay-to-keyframe-example.md](references/screenplay-to-keyframe-example.md)

## 工作流

### 1. 先确认每段原文由谁落实

从 [coverage-template.json](assets/coverage-template.json) 开始，接受后发布为
`episodes/<EP>/storyboard/coverage.json`。每个与制作有关的剧本段落都必须标明一种处理：

- `covered`：由一个或多个镜头落实；
- `intentional_repeat`：因表演或剪辑需要而有意重复，并写明理由；
- `omitted_with_reason`：有理由地省略；
- `nonvisual_context`：仅供理解、无需直接呈现的内容。

对白、动作、画面文字、画外音或关键音效还没有着落时，不要先追求漂亮镜头。
发布原文落实表时，`shot_refs` 必须逐条指向准确的镜头文件、已发布的 `hash` 和
`record_id`。裸 `shot_id` 只可表示同一镜头文件内的关系，不能证明审的是哪一版。

### 2. 先写镜头目的

使用 [shot-template.jsonl](assets/shot-template.jsonl)，接受后发布为
`episodes/<EP>/storyboard/shots.jsonl`。每个镜头先用一句话回答：

- 观众此刻必须注意什么、感到什么；
- 信息、情绪、观众立场或权力关系发生什么变化；
- 为什么要在这里切镜，而不是留在前一镜。

同时按事实填写 `audience_visibility`：准确来源、现在展示还是暂缓展示、可见或可听的
载体，以及各自的 `reveal_trigger`（何时揭示）和 `protection_method`（怎样防止提前泄露）。
遮挡不是默认的画面风格：它既不能提前泄露剧本保留的信息，也不能藏掉本镜必须交代的
证据或反应。

之后才选择景别和摄影机行为。镜头不是给动作段落加几个摄影形容词。

### 3. 绑定空间和资产

绑定准确的场景及视角、人物及造型、道具及状态和剧本来源段落，并建立：

- 位置、朝向、视线、屏幕运动方向与轴线；
- 进出路线和不随镜头改变的场景锚点；
- 双手与持物、伤势与服装、文字状态、光线方向；
- 有权威性的镜头开始边界和结束边界。

可见字样必须通过 `text_treatment_refs` 指向资产负责人已接受的文字政策。预览只能
指向带 `authority: candidate` 的候选政策。镜头和关键帧可以决定构图怎样让文字可见，
但不得把 `exact_readable` 偷换成装饰字、凭空写新文案，或用自由文本代替政策引用。

需要的资产或状态缺失、含混时，向资产或编剧环节提出修订，不要猜绑定关系。
若创作者要求从头到尾预览，只能针对唯一且不是 `unresolved` 的提案建立临时的原文落实、
镜头和关键帧。候选 `ArtifactRef` 要标明 `authority: candidate`，不得写成已接受的绑定，
也不得获得最终批准。

### 4. 设计能够制作的镜头

短镜头通常围绕一个主要动作，再保留让观众读懂后果所需的反应；这不是镜头数量公式。
一个镜头守不住空间关系、表演、对白或信息变化时就拆开；新切镜没有增加注意重点或
戏剧价值时就合并。

时长表示剪辑意图。只有明确的计时算术可以机械检查；一般的可拍性必须结合本镜内容判断。

### 5. 默认每镜一个冻结关键帧

使用 [keyframe-template.jsonl](assets/keyframe-template.jsonl) 写结构化来源，发布为
`episodes/<EP>/storyboard/keyframes.jsonl`；再用
[keyframe-prompts.md](assets/keyframe-prompts.md) 渲染可复制的派生文本。结构化关键帧
保存只属于单帧的选择；Markdown 不是第二份事实来源。

把已接受镜头的开始边界和准确资产版本，落到一个可以同时存在的瞬间：焦点、构图、
摄影机与镜头焦段、空间锚点、姿态、目光、双手与持物、表情、光线、排除项。

关键帧不得包含“先、再、最后”、表演变化过程、运镜过程或正在变化的环境；时间变化
交给 `$short-drama-video-prompts`。

### 6. 校验并呈现

先做原文落实、参考图权限和连续性的结构检查，再按制作资料自检。按顺序呈现：

1. 尚未落实的原文与 `unresolved` 项；
2. 按场分组的镜头表；
3. 可复制的关键帧提示词；
4. 相对剧本原意发生的差异；
5. 需要创作者接受的选择。

本技能不能自行终审；终审交给 `$short-drama-review`。

## 修订

若运动提示词环节要求修改镜头开始或结束边界，负责人仍是本技能。对照剧本原意审查
提议，修改镜头，展示哪些旧产物已经关闭或刷新，并更新关键帧、运动提示词和终审。
运动提示词文件不得悄悄变成第二份边界事实。

## 边界

- 不生成图片或视频。
- 镜头绑定资产，不把完整外观描述复制到每一镜。
- 外部制作单位不等于创作镜头本身的编号。
- 镜头数量、每次切镜秒数、焦段分布都不是通用定律。
- 新增或删除故事事实，必须先由编剧环节修订。

## 所有产物

- `episodes/<EP>/storyboard/coverage.json`
- `episodes/<EP>/storyboard/shots.jsonl`
- `episodes/<EP>/storyboard/keyframes.jsonl`
- `episodes/<EP>/storyboard/keyframe-prompts.md`（仅派生文本）
