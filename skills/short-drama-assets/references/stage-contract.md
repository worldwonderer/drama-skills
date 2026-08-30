# 视觉设定阶段契约

本阶段只拥有 `剧集/<EP>/视觉设定.md` 中的人物/造型、地点/视图、道具/状态、声音身份与跨场连续性。
它继承剧本事实，不决定镜头瞬态、构图、动作终点或生成提示词。

每个判断在同一文档中写成复用、新身份、新变体或真实未决；来源用场景 ID，跨文档消费用可见标题和
稳定 ID。不要为身份决定建立 occurrence、decision、ledger 或接受记录。

## 本阶段规则

### `AST`

| ID | Class | Knowledge |
|---|---|---|
| AST-01 | structural_invariant | Before creating or reusing an asset, point to the scene ID and visible evidence that establishes it. |
| AST-02 | reviewed_invariant | Reconcile each occurrence as reuse, new identity, new variant, or unresolved—never guess an ambiguous name/pronoun. |
| AST-03 | craft_default | Separate Character/Look, Location/View, and Prop/State. |
| AST-04 | reviewed_invariant | Persistent identifying anchors and mutable state are not mixed. |
| AST-05 | structural_invariant | Every downstream reference resolves to a visible identity item and the stated current variant in `视觉设定.md`. |
| AST-06 | craft_default | Track only asset facts needed for recognition, reuse, prompt writing, or continuity. |
| AST-07 | reviewed_invariant | Persistent voice identity and pronunciation refs stay separate from scene-level breath, emotion, volume, and delivery state. |
| AST-08 | reviewed_invariant | A voice reference binding states what it controls and what it must not; the take's emotion, its recording space, and its background never enter identity. |
| AST-09 | reviewed_invariant | A claim about what is audible in a reference requires a creator or rights-holder description, or an authorized listening observation bound to the inspected bytes; otherwise admission stays unverified. |
| AST-10 | structural_invariant | One accepted pronunciation of a proper noun uses one spelling throughout `视觉设定.md`. |
| AST-11 | reviewed_invariant | Characters designed together are not bound to confusable references; each names the audible trait telling it apart from its nearest neighbour, and names that character. |
| AST-12 | craft_default | Selection criteria are few, audible and counter-exampled; they judge a candidate reference or a clone result, they do not stand in for one. |

### `CON`

| ID | Class | Knowledge |
|---|---|---|
| CON-01 | structural_invariant | Linked end and next start states match or have an explicit owner revision. |
| CON-02 | reviewed_invariant | Knowledge, injury, ownership, weather, light, or physical state does not teleport/regress without story cause. |
| CON-03 | craft_default | Track downstream-relevant deltas, not the whole 设定集 in every shot. |
| CON-04 | structural_invariant | A continuity change states before, after, cause/source scene, effective range, and affected visible IDs. |
| CON-05 | taste_option | Declared montage, ellipsis, dream, or subjective imagery may intentionally break ordinary continuity. |
| CON-06 | structural_invariant | A continuity change names every existing downstream document it affects; future work is described, not pre-created. |
| CON-07 | structural_invariant | A continuity lock declared in `视觉设定.md` fixes one verbatim surface plus its shot and image scope; every in-scope frozen keyframe, motion prompt, and named image prompt carries that surface unchanged. |

规则分级由高到低：`structural_invariant`（结构缺陷，阻断）、
`reviewed_invariant`（需证据判断）、`craft_default`（常用做法，可覆盖）、
`taste_option`（创作者选择，不作缺陷）。创作者已接受的事实优先于本表。
