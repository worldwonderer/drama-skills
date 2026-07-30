# 视频提示词审查量表与合成案例

## 目录

1. 审查顺序
2. 证据量表
3. 诊断目录
4. 合成正例
5. 合成反例

## 1. 审查顺序

先验证 refs、显式时长、camera interval、音频否定与 end equality，再由独立 reviewer 看 performability、action feasibility、camera motivation 和 semantic invention。Reviewer 写 finding，不改 motion、shot 或 screenplay。

Finding 必须含 artifact/hash、引用片段、影响、required fix、owner、severity、status。禁止用 prompt 长度、动词数量、固定动作/秒或“AI 味”替代证据。

## 2. 证据量表

| 维度 | 核心问题 | 必须引用 |
|---|---|---|
| Start fidelity | 第一动作是否从 keyframe/shot start 真正可接上？ | start 字段与 prompt 起始句 |
| Ordered action | actor、方向、接触、先后和结果是否清楚？ | action stages |
| Performance | 触发、处理、选择、landing 是否可见且符合 agenda？ | source beat/shot purpose + motion |
| Action budget | duration 内能否保留故事动作、对白与 reaction？ | duration、距离、台词、动作、camera |
| Camera | lock/move 是否一致，且因注意/压力/揭示而发生？ | camera interval + shot purpose |
| Environment | 是否只动了有依据的环境，未发明天气/事件？ | accepted continuity + environment |
| Dialogue/audio | exact refs、`speaker_ref`、可选 `voice_direction_ref`、VO/OS/SFX 与本场 delivery 是否分别保持？ | source audio fields + character voice direction + prompt |
| End fidelity | reported end 是否逐项等于 continuity out？ | end report + source end |
| Economy | frame 已承载外观是否被无谓倾倒？ | reference contents + copy block |
| Shot boundary | 是否偷改 duration/end/next shot，或在单个镜头内部藏未声明的 cut？ | source shot + motion |
| Segment integrity | 每个计时段是否只有一个连续视角？各段相加是否正好等于**所属镜头**的已接受时长？ | segment 列表 + accepted shot duration |
| Container arithmetic | 容器承载了哪些已接受镜头？容器时长是否等于成员时长之和？成员是否顺序连续、同一绑定链、不跨场次、各自可单独审查？ | container 成员列表 + 各镜 accepted duration |
| Deliverable text | 交付文本里是否只剩要拍的画面内容，没有文件名、版本号、锁定标记、草图指代或任务备注？ | prompt 正文 |

语义 finding 的修复应指出删/改哪一段 motion，或该向哪个 owner 发 revision request，而不是笼统说“动作自然一点”。

## 3. 诊断目录

| code | classification | enforcer | 默认 severity | owner | 含义 |
|---|---|---|---|---|---|
| VID_SOURCE_REF_UNRESOLVED | structural_invariant | validator | error | video-prompts | shot/keyframe/dialogue/audio ref 未解析 |
| VID_EXPLICIT_TIMING_OVERFLOW | structural_invariant | validator | error | video-prompts | 显式时间终点/非重叠总量超 duration |
| VID_EXPLICIT_TIMING_SHORTFALL | structural_invariant | validator | error | video-prompts | 裁剪到镜头长度内的分段并集短于 duration；开头留空、中间留空与结尾留空都算，余量会被无来源动作填满 |
| VID_TIMING_MODE_INCONSISTENT | structural_invariant | validator | error | video-prompts | `timing_plan.mode` 写 `relative` 却有分段标 `explicit`，两种计时同时声明 |
| VID_EXPLICIT_TIMING_UNDECLARED_OVERLAP | structural_invariant | validator | error | video-prompts | 分段区间重叠但未声明重叠关系，合计无法判定 |
| VID_EXPLICIT_TIMING_UNPARSEABLE | structural_invariant | validator | error | video-prompts | 声明 explicit 却没有可读秒区间，无法做 VID-04 算术 |
| VID_DECLARED_TOTAL_MISMATCH | structural_invariant | validator | error | video-prompts | `declared_total_or_endpoint_seconds` 与分段实际覆盖不一致 |
| VID_DURATION_PROJECTION_STALE | structural_invariant | validator | error | video-prompts | `boundary_refs.duration.value_seconds` 与镜头已接受时长不一致 |
| VID_CAMERA_INTERVAL_CONFLICT | structural_invariant | validator | error | video-prompts | 同一区间 lock 与 move 等显式冲突 |
| VID_END_REPORT_MISMATCH | structural_invariant | validator | error | video-prompts | reported end 不等于 storyboard continuity out |
| VID_BOUNDARY_OVERRIDE | structural_invariant | validator | error | video-prompts | motion 写入 duration/end/next-shot override |
| VID_ACTION_INFEASIBLE | reviewed_invariant | reviewer | error | video-prompts/storyboard | 一般动作负载不可行或掩盖故事变化 |
| VID_SEMANTIC_INVENTION | reviewed_invariant | reviewer | error | video-prompts | 新造故事、关系、知识、状态或音频事实 |
| VID_CAMERA_UNMOTIVATED | craft_default | reviewer | warning | video-prompts | movement 无助于目的/注意变化 |
| VID_REFERENCE_DUMP | craft_default | reviewer | warning | video-prompts | bound frame 已带外观却重复整本 bible |
| VID_HIDDEN_CUT_IN_SEGMENT | reviewed_invariant | reviewer | error | video-prompts | 单个计时段内藏入视角或空间跳变，等于一次未申报的剪辑 |
| VID15_SHOT_PACKED_TWICE | structural_invariant | validator | error | video-prompts | 同一镜头被两个容器认领，全集时长凭空多一段 |
| VID15_MEMBER_IS_NOT_AN_EPISODE_SHOT | structural_invariant | validator | error | video-prompts | 容器成员不属于本集镜头集合 |
| VID15_MEMBER_SHOT_HAS_NO_DURATION | structural_invariant | validator | error | video-prompts | 被装箱的镜头没有数值时长，容器时长无从成立 |
| VID15_CONTAINER_DURATION_IS_NOT_THE_SUM | structural_invariant | validator | error | video-prompts | 容器时长不等于成员已接受时长之和 |
| VID15_EPISODE_TOTAL_DOES_NOT_RECONCILE | structural_invariant | validator | error | video-prompts | 容器加散镜不等于全集镜头时长总和 |

`VID15_*` 由 [container_check.py](../scripts/container_check.py) 执行。未装容器的散镜与
时长尚未确定的镜头**只报告不判错**：前者是合法的打包选择，后者是上游还没做完，把它们
写成缺陷会让"进行中"和"做错了"无法区分。
| VID_UNEXECUTABLE_MICRO_METRIC | craft_default | reviewer | warning | video-prompts | 亚秒偏移、厘米位移、角度数等读起来精确却无法执行也无法验证的计量 |
| VID_INNER_MONOLOGUE_ONLY | craft_default | reviewer | warning | video-prompts | 绝大多数段落只有内心活动，没有可拍的可见事件或有来源的声音 |
| VID_STYLE_ALTERNATIVE | taste_option | reviewer | note | video-prompts | 表演/摄影/声音风格的非阻断选择 |

语义问题只能由 reviewer 证据化判断；不要写正则把“缓慢”“同时”或动词数量变成错误。

## 4. 合成正例

以下人物、场景、对白均为虚构合成材料。

### Accepted boundary 摘要

- `SHOT-EP001-014`，duration `5.0s`，purpose：罗静听见门外有人试锁后，选择隐藏登记簿而非立刻逃跑；
- start：她坐在检修台边，左手翻开的登记簿，右手握笔，目光在页上；后方安全门位于她右后侧；
- dialogue：画外男声 `[OS] “里面有人吗？”`；
- end：她仍坐着，左手把登记簿压在工具盒下，右手握笔停在桌沿，目光锁向右后侧安全门；
- camera：接受的单镜头、固定轴线，可在触发后轻微推进。

### 合格 generic prompt

> 从参考帧的坐姿开始：她的左手仍按在翻开的登记簿上，右手握笔，目光落在页上，右后方是安全门。门外先传来一次短促的试锁声；她的笔尖立刻停住，目光先移向安全门，但身体没有起身。画外男声问“里面有人吗？”，她屏住一拍，没有回答，左手才把登记簿平稳滑入旁边工具盒下方，动作克制，避免纸页发声；右手始终握笔，最后停在桌沿。表演由专注工作转为警觉，再落到压住恐惧后的主动隐瞒。摄影机开头保持固定，在她决定藏起登记簿时做一次很短、很慢的推进，终点仍保持既定轴线，将她的左手、工具盒和望向安全门的视线纳入同一画面。维修间底噪持续，试锁声和画外问话清楚置于门的方向，无音乐突入。5 秒内完成，结尾保持她仍坐着、左手把登记簿压在工具盒下、右手握笔停在桌沿、目光锁向右后侧安全门。

为何有效：开端只重复运动关键事实；动作按声音→接收→决定→隐藏排列；OS 不要求口型；camera 在选择时启动；结尾逐字段落到 accepted boundary，没有写下一镜。

## 5. 合成反例

### 反例 A：外观倾倒与边界改写

> 她有窄长脸、断眉、短卷发，穿墨绿工装、米白 T 恤、深色长裤和短靴，维修间墙壁每一处材料都清晰。她站起来跑到门外，把笔交给陌生人，然后下一镜已经来到街上。

Finding：reference 已携带外观而 motion 没写关键表演；更严重的是 start 从坐姿跳到站立、end 新增跑出/道具转手，并代写下一镜。修复需回到 accepted 坐姿 end；若逃跑是创作意图，向 storyboard 请求新 boundary/shot。

### 反例 B：动作超载但不能靠计数器判

> 5 秒内，她听完整句问话，翻完三页，把册子锁入抽屉，走过房间关闭两扇窗，拆下墙上话筒，打电话说两句，再回到原座位保持完全静止。

Reviewer 应引用房间距离、物件操作、对白和 landing 说明不可行；不能只说“有七个动词”。优先保留与隐藏决定有关的动作，其余删减或请求 split/extend。

### 反例 C：camera 显式矛盾

```text
0–5s：摄影机绝对锁定、没有任何移动。
1–4s：摄影机持续向前 dolly 并手持环绕角色一周。
```

同一区间的 lock/move 可结构阻断。创作者可选择 lock 或 move；若需先锁后推，写不重叠 transition。

### 反例 D：音频语义发明

> 画外问话后，突然响起爆炸，所有灯熄灭，她大喊“我承认了”。

若 source 没有爆炸、停电和这句对白，这会改变故事与 continuity。由 reviewer 引用 source 缺失与 prompt 新句给 `VID_SEMANTIC_INVENTION`，不是因关键词“爆炸”本身被正则禁止。

### 反例 E：段内藏切

```text
段 2（1.6–3.4s）：她把登记簿推入工具盒下方；随即是走廊外一只手停在门把上的近景；
再回到她的侧脸，眉心收紧。
```

技能正文已经规定单个镜头内部只保持一个剪辑边界，但违规通常不出现在 `camera` 字段冲突里，
它藏在**一个计时段内部**的连续叙述中，读起来只是“镜头很有节奏”。量表要能抓到它，
而不只是禁止它：逐段列出空间锚点、被摄主体和机位关系，段内出现没有过渡的空间或主体
跳变，就是一次未申报的剪辑。修复是向 storyboard 请求拆成两个 authored shots，或删掉插入的
外景段落；不能靠补一个转场词把它说圆。

注意与多镜容器区分：容器内**成员镜头之间**的空间与主体跳变是已申报的剪辑，不是缺陷。
判据是跳变落在哪里——落在成员镜头边界上，且该边界能追溯到一个已接受镜头，就成立；落在
某个成员镜头的计时段内部，就是段内藏切。容器时长与成员时长之和不符时另记一条容器算术
缺陷，不要把两者混成同一条 finding。

### 反例 F：不可执行的微计量

```text
0.00–0.37s：眉心下压 0.4 厘米；0.37–0.92s：右手向左平移 3 厘米，头部旋转 7 度；
0.92–1.41s：瞳孔收缩，肩线下沉 1.2 厘米。
```

这类写法读起来像精度，实际没有指定任何可执行的东西：执行端无法把厘米和度数对到画面
尺度，审查者也无法验证是否照做；同时它挤掉了真正需要写清的触发、接触与结果。修复是
换成可比较的相对量与接触事实——“手指移到杯沿并停住”“视线从对方脸上落到桌面签名处”。
只有上游提供、且确实能消除歧义时才写具体秒段（见 `motion-recipe.md` 3.6）。

### 反例 G：整段独白

```text
段 1：她想起母亲临走前那句话。段 2：她意识到自己再也回不去了。段 3：她下定决心。
```

三段都只有内心活动，没有一件可拍的事。没有可执行内容时，执行端会自行发明动作与表情
去填满时间，结果与剧本无关。修复是让每段至少落到一个可见事件（目光、接触、位移、决断
动作）或一段有来源的声音；确实需要内心过程时，用已接受的画外音承载，并写清它与画面
事件的相对时机。内心独白本身不是缺陷，把它当作整条提示词的唯一内容才是。
