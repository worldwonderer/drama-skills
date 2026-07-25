---
name: short-drama-review
description: 独立校验与审查文件系统短剧项目中的故事、剧本、资产、连续性、资产图片提示词、分镜、关键帧和视频提示词。用户提出“审稿/检查剧本”“检查资产或连续性”“检查图片/视频提示词”“审查或诊断模板感”，或判断一集能否交付文本或 JSON 时使用；只发布审查问题、审查结论和修订要求，不代替负责人修改来源文件。
license: MIT
---

# 短剧独立审查

独立审查并引用产物证据。只写审查问题、审查结论和按负责人分组的修订要求；
不在同一次审查中替负责人修改创作来源，也不接受负责人自审。默认使用创作者语言；
中文项目的审查问题、影响和修订要求使用中文，稳定的规则编号和 ID 保持原样。

## 先定位套件

从本技能目录读取 `suite-ref.json`，按其中相对 `core_manifest` 定位唯一同级主技能与
套件清单；确认声明的 core、contract、recipe 和清单 hash 一致后再读写项目。
随后执行主技能的 [运行时预检](../short-drama/references/runtime-preflight.md)：先恢复事务、读取状态，再进入本阶段。
涉及制作形态时按需读取 [production-form-profiles.md](../short-drama/references/production-form-profiles.md)，只投影本阶段负责的形、层、材质、光、运动或声音。

## 选择审查范围

声明一个或多个范围：

- `story_script`
- `assets_continuity`
- `image_prompts`
- `storyboard_keyframes`
- `video_prompts`
- `full_episode`
- `delivery_privacy`

只读对应的审查表。完整审查先读
[review-method.md](references/review-method.md)，再读三份审查表；制作端常见缺陷
与各环节判据见 [production-quality-gates.md](references/production-quality-gates.md)。
涉及参考图权限、遮挡式揭示或补拍版与替代版关系时加读
[参考媒体权限与补拍契约](../short-drama/references/reference-media-and-pickups.md)。
不预先加载所有创作资料。
证据来自项目产物和已接受限制，而非负责人的自我解释。
只有审查问题涉及“模板感、重复手法或 AI 味”时才读
[anti-template-repair.md](references/anti-template-repair.md)，用其诊断、修订示范与误报反例。

## 工作流

### 1. 先建立 fresh 审查上下文

若当前上下文参与过任一目标产物的创作或修订，优先启动一个 **fresh reviewer agent/context**，
只传目标路径与 hash、已接受限制、当前范围需要的审查表和输出模板。不要传负责人的自检结论、
预期答案或打算采用的修法。新审查者必须确认自己没有创作这些目标，重新读取当前字节后再判断。

在结论中记录 `requested_review_mode` 与 `effective_review_mode`。只有运行环境实际提供 fresh
context，且审查者没有写过目标产物时，`effective_review_mode` 才能写 `fresh_agent`，并记录该
context 的运行时标识。若当前运行环境不能启动 agent、启动失败或上下文已经受创作过程污染，
可以做所有者自检和列问题，但必须写 `self_check` 或 `unattested`，保持 `independent:false`，
结论只能是 `PROVISIONAL`，不能签发 `APPROVE` / `APPROVE_WITH_NOTES`。

`project_tool.py` 只能复验这些声明的结构、目标 hash 与证据文件，不能从 JSON 密码学证明
某个上下文真的 fresh。实际隔离必须由宿主在启动 reviewer 时建立；状态记录使用
`verification_scope: declared_provenance_structure`，不得把结构通过描述成运行时身份已验证。

### 2. 冻结审查目标

记录产物路径和 `hash`、创作者限制、审查范围与上游 `hash`。目标文件变化后，
旧审查问题变为 `stale`。状态为 `provisional` 或尚未接受的输入不能获得最终批准。

### 3. 先跑结构校验

先检查可证明事实：

- 数据结构、JSONL 和稳定 ID；
- 来源与资产引用；
- 原文落实情况；
- 准确资产版本，以及来源文字政策与本次呈现方法的对应关系；
- 明确时间段的总和；
- 生命周期与事务状态；
- 派生规格和配方的 `hash`；
- 负责人权限与隐私边界。

缺少前置资料而无法审查目标时，停止后续内容审查；其他互不依赖的结构问题可以一次汇总。

### 4. 带证据审查内容与创作方法

重新查看当前资料，不采用负责人的自我辩解。每个审查问题包含：

- 稳定的问题编号、做法编号、问题类别和检查方式；
- 准确的文件、记录、段落、镜头或提示词及其 `hash`；
- `target_ref` 以及来源端和使用端的 `evidence_refs[]`；
- 必要的短引文或冲突字段；
- 对观众理解或制作的影响；
- 必须达到的修订结果，而不是藏在审查问题里的代写稿；
- 负责技能、严重程度和状态。

分类必须使用：

- `structural_invariant`：能够直接证明的结构错误；
- `reviewed_invariant`：证据成立时给出 `REVISE`；
- `craft_default`：说明影响的警告，可由创作者明确改写；
- `taste_option`：备选意见，不能单独阻断。

### 5. 跨层综合

优先守住剧本原意与连续性，而不是奖励华丽提示词。追踪：

```text
剧本事实 -> 资产决定 -> 镜头目的与边界
-> 冻结关键帧 -> 有序动作 -> 下一状态
```

造型版本错误、遗漏对白、改变动机、发明动作或破坏下一镜衔接时，提示词写得再详细也不能弥补。

### 6. 给出审查结论并分派修订

- `APPROVE`：没有阻断问题，常用做法符合已接受的创作意图；
- `APPROVE_WITH_NOTES`：没有阻断问题，只有可选改进；
- `REVISE`：存在结构错误、内容错误或违反已接受限制；
- `PROVISIONAL`：缺少独立审查者或已接受的前置资料。

按故事开发、剧本、资产、图片提示词、分镜和视频提示词分组。负责人修改后列出所有
变为 `stale` 的下游产物，并审查新 `hash`；审查者不编辑来源文件。

审查结论必须以结构化方式绑定准确的 `reviewed_artifacts`、当前 `findings_ref`、审查者
独立性和未关闭阻断问题数量。`findings_ref` 的 JSONL 中，每个未关闭的致命、错误或阻断
问题 ID 必须且只能出现一次，并与审查结论中的 `blocking_findings` 和数量完全一致。
隐藏未关闭问题、列入已关闭问题或引用不存在的 ID 都不能批准。没有这些证据时只能给
`PROVISIONAL`；一个状态字符串本身不能放行交付。模板故意以 `unattested` / `independent:false`
开始；fresh 审查者完成工作后才填写准确的运行时 provenance、被排除的负责人并改为
`independent:true`。只写一个审查者名称或手改布尔值都不能证明独立性。

## 审查表

- 故事承诺、因果、场景、行动、对白：
  [rubric-story-script.md](references/rubric-story-script.md)
- 资产身份/变体、连续性、资产图片提示词：
  [rubric-assets-prompts.md](references/rubric-assets-prompts.md)
- 原文落实、镜头、关键帧、视频提示词和跨镜状态：
  [rubric-visual-motion.md](references/rubric-visual-motion.md)

## 审查问题与严重程度

从 [finding-template.jsonl](assets/finding-template.jsonl) 建立审查问题，从
[verdict-template.json](assets/verdict-template.json) 建立审查结论。问题目录提供编号、类别、
默认检查方式、严重程度和负责人；审查问题记录本次目标的证据和状态。

- `fatal`：不安全或非公开内容被交付、事务损坏、缺少授权；
- `error`：阻断当前检查的结构或内容错误；
- `warning`：有具体影响的常用做法问题；
- `note`：创作选择、问题或不阻断交付的润色建议。

没有证据不要打分。不能只说“AI 味”；必须定位重复手法、用套话代替具体内容，或没有铺垫的文句模式，
并解释它伤害什么。

## 边界

- 不生成或查看已渲染媒体。
- 不从文字产物声称脸部一致、表演、口型、混音、剪辑或市场表现。
- 不把非公开制作观察变成通用审查标准。
- 审查问题只带创作者修订所需的必要证据；不泄露非公开输入、完整创作文本、
  网址或机器路径。
