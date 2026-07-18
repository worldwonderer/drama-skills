# 起点—变化—终点：Motion 通用配方

## 管辖 know-how

- `VID-01` — 将 accepted start/end、duration、dialogue 与 next-shot state 视为只读
  boundary。Motion 只实现这个区间，不改写它。
- `VID-02` — 按需组合：start anchor、subject action、performance change、camera、
  environment/audio、timing，最后输出 end-state report。
- `VID-03` — 当参考帧已承载外观和构图时，motion prose 专注“变化”和“保留”，
  不要重抄整本 visual bible。

## 结构字段必须真正控制本镜

**`VID-08 · reviewed_invariant`**：structured motion 必须命名本镜的 exact actor、
action、contact、performance path 与 result，不是可复制占位句。

Schema 句子不是 motion 内容。每个 `ordered_subject_motion` 都要写本镜的精确
actor/binding、trigger、observable action、direction/path、contact object、阶段结果和
order。不得在所有镜头复制“accepted subjects”、“accepted source action”、“到达
accepted end”。`performance_arc` 也要写具体 receive/process/choice/landing。

删除测试：若一条 structured item 原样复制到下一镜仍成立，它多半没有控制
任何东西。保留 schema，把值替换成本镜事实，并删掉无关物件/动作枚举。

### 选择性变换边界

**`VID-11 · reviewed_invariant`**：当一段 motion 只让环境或主体的一部分折叠、消失、
替换、破碎、复原或改变形态时，必须写清：

1. **触发**：哪个 accepted 动作、状态或声音开始变换；
2. **目标范围**：精确到哪一个人、物、表面、空间构件或局部，沿什么路径变化；
3. **结束几何/状态**：变换后留下、占据或暴露什么，不只写“消失了”；
4. **preserve set**：同框但不参与变换的人物、手持道具、文字承载面、空间锚点与
   continuity 状态保持什么位置和数量。

这是一项语义/可执行性审查，不能靠关键词 validator 证明。若目标模型会把邻近物一起
吞掉，先缩小目标范围、拆 motion 或增加阶段边界；不要只堆“其他不变”的泛化负面词。

## 末镜 handoff

**`VID-09 · structural_invariant`**：next-start 要么是已存在的 canonical ref，
要么是明确 provisional locator，不得是伪造 record/hash。

`next_start` 只有在目标已存在时才是 canonical ArtifactRef。最后一镜可对照已存在的
episode-card/map handoff；若下一集尚未建立，写 `next_start_locator` 与 provisional
status。不得伪造 `EP002-START` record 并计为 match。

## 目录

1. 目的、输入与权属
2. Boundary card
3. 七部分 motion recipe
4. 从 spec 到自然语言
5. 输出、失败征兆与检查

## 1. 目的、输入与权属

视频提示词不负责“想一个更精彩的镜头”，而是把已接受 shot 的变化写成可执行时间过程。输入：shot/keyframe 接受快照、exact assets、duration、purpose、start/end boundary、dialogue/VO/SFX/audio refs、continuity in/out、视觉/声音方向和可选能力 profile。

Shot/storyboard 拥有：地点/时间、asset variant、构图意图、duration、对话、起止姿态/位置/目光/双手/持物、continuity out 和 next start。Motion 对这些只读，只能报告是否按描述到达。

## 2. Boundary card

写提示词前先做内部卡片：

```text
SHOT / KEY：artifact + hash + field refs
PURPOSE：观众的注意、信息或情绪要怎样改变
DURATION：accepted duration
START：pose / balance / gaze / hands / held props / spatial relation
END：同一组字段的 accepted end_boundary
DIALOGUE/AUDIO：exact source refs + delivery intent already accepted
NEXT：next-shot start ref（comparison only）
```

### 最小 start anchor

只重复“开始运动必须知道”的状态：

- 重心在哪只脚、身体面向和是否坐/倚/跪；
- 目光落点与注意对象；
- 双手的位置、哪只手持什么、接触是否已发生；
- 与门、桌、人等行动相关对象的距离/方位；
- 正在持续的运动（若 keyframe 冻结的是运动中瞬间）。

参考帧已承载脸、服装全貌、墙面材料等内容时不重复。**`craft_default`** 是“复述运动约束，不复述视觉圣经”。但若 prompt 脱离 frame 就会把左右手/物体搞错，应保留局部事实。

## 3. 七部分 motion recipe

### 3.1 Start anchor

用现在时、静态句精确落点，避免“她准备站起来”这种已经偷放未来动作的写法。若 keyframe 与 shot start 不一致，先请求 storyboard 解决，不能任选其一。

### 3.2 Ordered subject action

每个 action stage 说明：actor、动作、方向/路径、对象/接触、先后条件、到达状态。优先因果连接：

```text
听见门外金属碰响后 → 目光先移向门缝 → 左手停止翻页并压住记录本 → 身体才从椅背离开
```

而非：

```text
她转头、翻书、站起、走向门、微笑、震惊，所有动作同时自然发生
```

“缓缓、自然、电影感”不能替代路径与结果。只描述画面内可见动作；心理词要落为呼吸、注视、肌肉张力、停顿、话语节奏或明确 VO。

若该动作包含选择性变换，按 `VID-11` 把触发、目标范围、结束几何/状态和 preserve set
写进相应 action stage；不能让相邻非目标人物、道具或已批准文字跟随目标一起改变。

### 3.3 Performance arc

表演不是表情标签序列。找到最小变化：

1. **触发**：外部声音/动作/台词或内部决定（来源可追溯）；
2. **接收**：注意被什么抓住；
3. **处理**：一两个细微信号，而非面部部件清单；
4. **决定**：角色采取/抑制什么行动；
5. **结果**：到达 accepted end 的情绪与身体状态。

- **`craft_default`**：把副词换成节奏和行为，“愤怒地说”可具体为先压低音量、吞回半句话、最后直呼对方全名——前提是符合台词与人物。
- **`reviewed_invariant`**：不能发明新信息、关系转变或角色意图。Reviewer 对照 shot purpose/剧本证据判断。
- **`taste_option`**：强度与外显程度可从克制到爆发，由导演选择，不强制“先微表情后爆发”。

### 3.4 Camera behavior

选择 deliberate lock-off 或一个主要 movement path。Move 写动机、开始条件、运动方向/速度、终点 framing；例如“当信封被推入光区时，机位做一次短促前移，停在手与印章的近景”，优于“镜头电影感推进”。详见 `camera-audio-continuity.md`。

### 3.5 Environment 与 audio

只让环境中对故事/连续性有意义的元素动：风推动窗帘、远处警示灯扫过墙、杯中水因桌面撞击晃动。不要给每个背景物同时加动画。Audio 引用 exact dialogue/VO/SFX；提示 delivery、同步意图、空间声源与层级，不改台词。

### 3.6 Timing

默认以相对阶段写节奏：开头短暂停留、中段动作加速、结尾留出反应。只有制作需要时写显式秒段。

- **`structural_invariant`**：明确标注的非重叠段落时长相加不得超过 shot duration；重叠段必须声明 overlap，而不是靠猜。
- **`reviewed_invariant`**：即使数学不超时，台词、动作、反应是否可完成仍由 reviewer 结合语言、强度和物理路径判断。
- 不设通用“每秒几字/几个动作”硬门槛。

### 3.7 End report

把描述结果投影回 boundary 字段：end pose、position、gaze、hands、held props、可见状态 delta。逐项对照 storyboard `end_boundary`：

```text
reported_end == source end：match
reported_end 不同：mismatch + storyboard revision request
prompt 未实现某字段：unrealized + motion revision
```

End report 永远不是下一镜权威输入。不能用 motion 的“更自然结尾”覆盖 accepted end。

## 4. 从 spec 到自然语言

### Copyable 骨架

```text
从[最小 start anchor]开始。[有序主体动作与路径]；表演从[触发后的外显状态]经过[处理/决定]变化到[accepted end 的可见结果]。
摄影机[lock-off 或一次有动机的 move，含节奏和终点]。[必要环境运动]。
对白/声音：[exact source 与 delivery/空间意图]。在[duration]内按[相对/显式节奏]完成，最终[只报告 accepted end]。
```

把元数据、hash、内部 rule ID 留在 Markdown 卡片，不放 copy block。避免 provider 参数、权重语法、任务指令、质量承诺。

### Reference-frame economy

- frame 已经表达：人物长相、整套服装、空间装修、静态构图、基础光线；通常省略。
- motion 仍需表达：左右手/持物、即将行动的关节与路径、目光目标、运动相关遮挡、会变化的环境、镜头运动。
- 能删去而不改变动作结果的 appearance 句，优先删。
- **`craft_default`** 不是“越短越好”；关键 boundary 不因节省字数而丢失。

## 5. 输出、失败征兆与检查

### 输出

- accepted motion spec：ordered actions/performance/camera/environment/audio/timing；
- start/end 只读 source refs 与 end comparison report；
- 一段 copyable generic prompt、必要 warning/loss note；
- 不含视频、音频、task ID、URL 或 API 状态。

### 失败征兆

- 从不同于 keyframe 的手势/持物开始；
- prompt 大半重写人物服装场景，动作只剩“自然移动”；
- 所有动作“同时”，接触/方向/先后不明；
- 选择性变换只写“消失/变形”，没有目标范围、结束几何或 preserve set；
- 表演只有“震惊—生气—悲伤”标签，没有触发和可见过程；
- end 新造了站位、道具转移或下一镜事实；
- 一条 prompt 内出现多次 editorial cut。

### 检查

1. **结构**：source refs、duration、explicit timings、camera interval、dialogue refs、end comparison。
2. **语义**：独立 reviewer 引用 shot 与 motion，判断 feasibility、performability、动机和无发明。
3. **默认**：动作经济、参考帧经济、单一主运镜可建议但允许有理由覆盖。
4. **选择**：表演强度、lock/move、节奏、声音密度由 accepted creative direction 决定。
