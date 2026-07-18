# 生产质量门：环节判据、指令优先级与返工预防

来自对完整生产链返工原因的定性案例阅读，示例均为合成改写。审查方法见
[review-method.md](review-method.md)。本文展开 `REV-08` 与 `REV-09`：检查具体
义务是否被保留，而不是用语料规模、固定数量或所谓命中率给工艺背书。

**第一性原则：每个环节都是下游的输入。质量门要指出丢失了哪项意义、状态、覆盖
或执行约束，以及应由哪个 owner 修复。**

## 一、先分清能阻断什么

- `structural_invariant`：引用、哈希、显式时长、同一区间 camera 冲突、已声明
  start/end 不一致等本地可证明事实；validator 可阻断。
- `reviewed_invariant`：剧情意义是否丢失、人物反应是否承接后果、动作是否可执行、
  指令是否被语义改写；独立 reviewer 引用证据给 `REVISE`。
- `craft_default`：常用镜头、密度、布光、提示词组织方式；只能给 warning，并允许
  创作者说明理由覆盖。
- `taste_option`：气质、镜头偏好、节奏风格与表现强度；不得单独成为质量缺陷。

不要把“和示例不一样”写成 finding。先说明违反了哪个 accepted 事实或会造成什么
下游损失。

## 二、跨环节返工机制

### 1. 覆盖丢失

剧本中的动作、台词、反应、可读文字或转场在分镜/提示词中消失，或一个缩短后的
重试 prompt 只保留漂亮画面却不再完成同一叙事职责。

检查：

- coverage 对每个生产相关 block 是否有 disposition；
- 每个 authoritative action 是否只实现一次；
- 关键结果落地后，观众是否能看到受影响者的反应或后果；
- 重编组前后，source block、shot purpose、start/end 与 audio refs 是否仍相同。

### 2. 表演只有标签

“震惊、愤怒、电影感”没有触发、处理、选择与落点，删掉台词后只剩静态姿势。
修复不是堆更多同义词，而是把故事变化落成注视对象、手部选择、重心、停顿、距离、
呼吸或道具处理。

### 3. 空间与状态漂移

人物位置、朝向、手、持物、伤势、Look、时间、天气或光线在相邻镜之间无原因变化。
先核对 owner 的 end/start boundary 和 asset variant；若剧情确实需要变化，向 owner
请求 delta/shot revision，不在 prompt 里偷偷补故事。

选择性环境变换还要核对 `VID-11`：触发、精确目标范围、结束几何/状态与 preserve set
是否同时存在；相邻但非目标的人物、道具、文字承载面和空间锚点是否保持原位置、数量与
状态。只写“门消失”“地面折叠”而不写留下什么及什么必须不变，属于可执行性 finding。

### 4. 文本与声音边界混乱

- exact-readable 道具文字与全局 no-text 同时出现；
- no-text 或 exact-readable 镜头绑定了带冲突标牌、界面、号码、水印或字幕的参考图，
  却只追加负面词，没有裁切、清理、遮罩、替换或退回 reference owner；
- VO/OS 被写成角色对口型说话；
- prompt 新增来源没有的台词、音乐、爆炸或环境事件；
- 角色名/资产引用进入台词内文；
- SFX、环境底噪与对白方向没有来源或相互遮蔽。

### 5. 负面补丁淹没动作

为防止模型缺陷叠加大量禁令，真正的 start、action、performance 与 end 被挤成几句。
只保留目标模型已确认需要的条件规则；把通用故事与连续性义务留在权威 spec，不在
每次失败后永久增加一条全局禁令。

## 三、指令优先级与改写回归（REV-09）

同一 prompt 可能同时接收故事、分镜、项目风格和目标模型约束。按以下顺序消解：

1. **accepted 故事事实与对白/文字语义**：不能被视觉偏好改写；
2. **accepted shot coverage、purpose、start/end/duration**：motion 只能实现，不能
   重定边界；
3. **accepted asset identity/variant 与连续性 delta**：绑定正确状态；
4. **creator-accepted visual direction / production profile**：规定摄影、节奏、声音
   与文本策略，但不能覆盖前三项；
5. **目标模型条件**：只处理表达与已知能力边界；无法实现上层义务时报告 loss，
   不能静默删除；
6. **装饰性偏好**：镜头花样、泛质量词和未选择的建议最先让位。

若两条同级 accepted 规则显式冲突，阻断并交回各 owner/创作者决策；不要用“最高
优先级”措辞覆盖另一条事实。

### 每次重写或重编组后的回归表

| 义务 | 来源证据 | 改写前实现 | 改写后实现 | 结果 |
|---|---|---|---|---|
| source coverage | block/coverage ref | 【填写】 | 【填写】 | 保留/丢失/改变 |
| shot purpose | shot ref | 【填写】 | 【填写】 | 保留/丢失/改变 |
| start/end/duration | boundary refs | 【填写】 | 【填写】 | 一致/冲突 |
| asset/continuity | binding/delta refs | 【填写】 | 【填写】 | 一致/漂移 |
| dialogue/text/audio | exact refs | 【填写】 | 【填写】 | 一致/新增/遗漏 |
| project profile | creator decision ref | 【填写】 | 【填写】 | 遵守/例外/冲突 |

**`reviewed_invariant · REV-09`**：正确引用了角色、场景和物品，只能证明绑定存在，
不能证明 prompt 覆盖了原动作、反应、台词与 accepted 项目指令。审查者必须比较
改写前后语义义务。

缩短重试或 provider packaging 若丢失 source action/reaction，应提供 loss report，
再由 storyboard/video owner 决定拆镜、延长、删减或请求上游 revision。

## 四、各环节证据问题

### 剧本

- 当前目标、阻碍、选择和局部结果能否在场上看见？
- 升级是否改变权力、信息、关系、资源、物理状态或代价，而非重复加大音量？
- 集尾钩子是否由本集已落地结果造成？
- 前史是否只通过当前需要的行为、证据、关系摩擦或有策略的对白进入？
- 对白策略、声音与信息权限是否区分人物？

### 资产与图片提示词

- 每项细节是否服务识别、复用、状态、功能或连续性？
- identity 与 Look、Location 与 View、Prop 与 State 是否分开？
- 白底/方位/产品板是否写入了只属于具体镜头的情绪、构图或光线？
- exact/graphic/no-text policy 是否与 prompt 一致？
- 输入参考图的可见文字也符合该 policy 吗？不符合时是否记录了准入处理，而不是指望
  no-text 删除参考像素？
- 定点修改是否写 exact target、changes 与 preserve set？

### 分镜与关键帧

- 每个 shot 为什么独立存在，改变了什么注意、压力、信息或观看位置？
- establishing、reaction、insert、recontextualization 是否有职责，而不是凑数量？
- keyframe 是否只有一个可冻结时刻，并准确投影 start boundary？
- authored shots 是否先独立成立，再进入生成单元编组？

### 视频提示词

- 第一动作是否能从 accepted keyframe/start 接起？
- ordered action 能否在 duration 内保住故事动作、表演、对白与落点？
- camera 是否由注意、压力或揭示驱动，并且区间不冲突？
- environment/audio 是否有 accepted 依据？
- 选择性折叠、消失、替换或复原是否声明目标范围、结束几何和 preserve set，且没有
  连带改变非目标人物/道具/文字/空间锚点？
- reported end 是否逐项等于 storyboard continuity out？
- 参考帧已有的外观是否被无谓重复，反而增加漂移？

## 五、生产纪律

1. **先 coverage，再选择密度**：镜头数量从意义、动作边界和反应职责推导，不从
   固定拍镜比倒推。
2. **从 action budget 反推时长**：必须保留的动作、对白与落点放不下时，删减、
   拆镜或请求 revision；不靠加快所有动作硬塞。
3. **每个 volume 只激活一个明确 profile**：不要混用两套模型条件或相反的服装/
   光影托管策略。
4. **文本策略双重核对**：prompt 约束与资产 text policy 都要一致；后处理能力不能
   为矛盾 prompt 开脱。
5. **定点修改一次改一个意图**：同时显式冻结不变项，记录该修改影响哪些下游。
6. **重试必须改变可解释变量**：同 prompt 重复提交不产生新知识；每次记录失败、
   改动与 preserve set。
7. **长范围先建记忆与停靠点**：可一次做完整 coverage，但接受、审查与回滚仍按
   创作者能理解的阶段进行。
8. **视频期发现故事问题就回 owner**：不让 prompt 用新台词、动作或状态替剧本补洞。

## 六、Finding 写法

合格 finding：

> `SHOT-014` 的 source block 要求听者在指控后把钥匙交回，当前 motion 只覆盖说话
> 人并以其特写结束；这删除了关系权力转移，也使下一镜“钥匙已在桌上”的 start
> 无来源。由 storyboard owner 补 reaction/transfer shot，或由 screenplay owner
> 明确删除转手；不要只在 motion 末尾发明钥匙落桌。

不合格 finding：

> 镜头太少、不够电影感、没有按参考比例、AI 味重。

`REV-08`/`REV-09` 都要求 artifact/hash、具体证据、影响、required fix、owner、
severity 与 status。固定字数、镜头数、比例和示例相似度只能帮助提问，不能替代
上述证据。
