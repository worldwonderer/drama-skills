# 资产阶段契约

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

- **本阶段拥有**：人物/地点/道具的身份与变体；资产状态变化记录与场景/单集资产台账；
  出现证据的提取。
- **本阶段继承**：剧本给出的身份、地理与文字政策；故事状态条目是开发/剧本的只读投影。
- **本阶段不越权**：不决定镜头构图与动作终态，不改写剧情事实。台账里的故事状态只带来源
  指针，不构成第二个取值权威。

## 制作形态需要什么

视觉风格不是贴在提示词前面的标签。创作者已接受的视觉方向与制作形态由项目层决定并传入，
**本技能不加载形态卡，也不自行选择形态**；本节只说明本阶段需要形态回答什么、以及拿到
答案后投影成哪些字段。

形态决定属于 `craft_default`：创作者说明理由即可覆盖。形态不能创造新的
`structural_invariant`，也不能改写身份、地理、持物归属与可读文字政策。审查者不得单凭
形态偏好阻断交付。

不要用“加一句风格前缀”处理形态差异。前缀只改变检索标签；形态改变的是**必须出现和
可以省略的字段**，只有后者会被执行，也只有后者能被审查。

本阶段要向形态决定问四件事：

- **身份锚点载体**：靠什么让人物跨镜可辨认——轮廓、线条、比例、结构差异还是材质。
- **层级拆分**：身份层、环境层、可动层、效果层各包含什么，同一事实由谁负责。
- **材质与光色**：材料怎样响应光，色彩关系承担什么信息；不罗列质量词。
- **连续性必带项**：本形态下哪些字段必须逐镜传递、哪些可以省。不同形态答案差别很大，
  不要照抄别的形态的必带串。

本阶段新增：轮廓、材料、层拆、稳定比例与版本差异。

## 本阶段规则

### `AST`

| ID | Class | Knowledge |
|---|---|---|
| AST-01 | structural_invariant | Extract occurrences with source block/hash before creating or binding an asset. |
| AST-02 | reviewed_invariant | Reconcile each occurrence as reuse, new identity, new variant, or unresolved—never guess an ambiguous name/pronoun. |
| AST-03 | craft_default | Separate Character/Look, Location/View, and Prop/State. |
| AST-04 | reviewed_invariant | Persistent identifying anchors and mutable state are not mixed. |
| AST-05 | structural_invariant | Every downstream binding resolves to an accepted identity and valid variant. |
| AST-06 | craft_default | Track only asset facts needed for recognition, reuse, prompt writing, or continuity. |
| AST-07 | reviewed_invariant | Persistent voice identity and pronunciation refs stay separate from scene-level breath, emotion, volume, and delivery state. |
| AST-08 | reviewed_invariant | A voice reference binding states what it controls and what it must not; the take's emotion, its recording space, and its background never enter identity. |
| AST-09 | reviewed_invariant | A claim about what is audible in a reference requires a creator or rights-holder description, or an authorized listening observation bound to the inspected bytes; otherwise admission stays unverified. |
| AST-10 | structural_invariant | An accepted pronunciation of a proper noun appears in exactly one spelling across the 设定集. |
| AST-11 | reviewed_invariant | Characters designed together are not bound to confusable references; each names the audible trait telling it apart from its nearest neighbour, and names that character. |
| AST-12 | craft_default | Selection criteria are few, audible and counter-exampled; they judge a candidate reference or a clone result, they do not stand in for one. |

### `CON`

| ID | Class | Knowledge |
|---|---|---|
| CON-01 | structural_invariant | Linked end and next start states match or have an explicit owner revision. |
| CON-02 | reviewed_invariant | Knowledge, injury, ownership, weather, light, or physical state does not teleport/regress without story cause. |
| CON-03 | craft_default | Track downstream-relevant deltas, not the whole 设定集 in every shot. |
| CON-04 | structural_invariant | A delta records before, after, cause/source, effective range, and affected bindings. |
| CON-05 | taste_option | Declared montage, ellipsis, dream, or subjective imagery may intentionally break ordinary continuity. |
| CON-06 | structural_invariant | A delta's affected refs cover all existing consumers; future consumers remain locators until materialized. |

规则分级由高到低：`structural_invariant`（结构缺陷，阻断）、
`reviewed_invariant`（需证据判断）、`craft_default`（常用做法，可覆盖）、
`taste_option`（创作者选择，不作缺陷）。创作者已接受的事实优先于本表。
