---
name: short-drama-develop
description: 将中文小说、短剧或漫剧想法、梗概、改编材料或已有系列笔记发展成可追溯的改编方案、戏剧方向、创作简报、导演阐述、故事引擎与分集地图，并按题材与制作形态（画风）选择写法。用户提出“导入小说做短剧”“开发短剧/漫剧”“做故事设定/系列大纲/分集规划”“写导演阐述”“这个题材怎么写”“定画风/制作形态”“把这个点子变成短剧”或需要梳理人物冲突与集间交接时使用；已有剧本可直接进入写作、资产或审查流程，不强制补开发文件。
license: MIT
---

# 短剧开发

把创作者的意图变成能持续制造**选择、代价与状态变化**的故事系统。保留创作者的题材、结局和尺度选择；不要把流行套路当成剧情答案。

## 先定位套件

从本技能目录读取 `suite-ref.json`，按其中相对 `core_manifest` 定位唯一同级主技能与
套件清单；确认声明的 core、contract、recipe 和清单 hash 一致后再读写项目。
随后执行主技能的 [运行时预检](../short-drama/references/runtime-preflight.md)：先恢复事务、读取状态，再进入本阶段。
涉及制作形态时按需读取 [production-form-profiles.md](../short-drama/references/production-form-profiles.md)，只投影本阶段负责的形、层、材质、光、运动或声音。

## 先判断入口

1. **只有想法、题材或情绪目标**：完成方向探索，再建立创作简报与故事引擎。
2. **已有梗概、小说、改编材料或系列笔记**：保存来源并先列出已承诺事实、可改范围、
   来源片段与待确认项。长材料先做语义分段、功能账本和候选分集，不用正则人名/名词命中
   直接发布剧集或资产；不要偷换原意。
3. **方向已定，只需规划分集**：读取既有简报与引擎，直接制作或修订分集地图。
4. **已有剧本**：不要为了流程完整而虚构开发材料。写/改剧本交给 `$short-drama-write`；拆资产或后续制作可从相应技能直接进入。

信息足够时直接工作。只有会改变主角、冲突引擎、结局承诺或改编边界的缺口才需要提问。

## 每次执行

### 1. 锁定创作者契约

读取创作者输入和已接受文件，区分：

- 不可改的事实、主题、角色与结局承诺；
- 可探索的空白；
- 形式约束与制作边界；
- 目标观众反复能获得的情绪、信息或权力回报。

新项目复制 [creative-brief.md](assets/creative-brief.md)。若改动已接受事实，先展示语义差异与下游影响，不直接覆盖。

### 2. 探索真正不同的方向

方向未定时，提出少量**机制不同**的候选：改变主角策略、对抗来源、代价或持续冲突方式，而不只是换标题、职业和措辞。方向明确时直接深化，不为凑选项制造伪差异。

每个候选都说明：戏剧承诺、主角追求、对抗机制、反复回报、可升级的状态、长线终止条件、主要制作负担。让创作者选择或组合，并记录取舍理由；未选方案不进入既定事实。

需要完整方法时读取 [story-craft.md](references/story-craft.md)。题材已经确定时，可从
[genre-cards.md](references/genre-cards.md) 按索引取**一张**题材卡，用来校准该题材更常用的
压力来源、场面颗粒与制作负担；它是可被创作者一句话覆盖的参考，不替代方向选择，也不进入交付物。

### 3. 建立故事引擎

复制 [story-engine.md](assets/story-engine.md)，写清：

- 压力如何出现，主角通常如何应对；
- 对手或系统如何反制，主角为何不能轻易退出；
- 每轮冲突改变什么，而不是怎样重复得更响；
- 人物各自的目标、筹码、底线和关系压力；
- 哪种人物转变或事实揭开会终结这个引擎；
- 视觉、声音、场景规模和内容边界。

连续剧、长篇改编或人物纵向变化较重时，再读取
[serial-character-and-memory.md](references/serial-character-and-memory.md)：先建立只供
作者决策的前史储备，选择旧策略开始付出代价的叙事切入窗口，再记录人物被捍卫的
信念、压力测试、信息权限和跨集记忆。不要把前史储备直接倒进首集对白。

### 4. 把系列运动落到分集

读取 [episode-design.md](references/episode-design.md)，再复制 [episode-map.jsonl](assets/episode-map.jsonl)。每一集记录进入状态、当集追求、阻力、因果升级、方向性转折、已兑现回报、出去的压力以及下一集必须继承的事实。

先保证每集产生局部戏剧结果、相邻集能精确交接，再讨论外部压力/情感负荷的双轨
节奏。集数、钩子类型、是否反转和高潮位置由创作者与项目决定。

### 5. 做所有者检查并交接

在提交预览前检查：

- 引用的集、铺垫和兑现记录可找到，未决项被明确标记；
- 候选确实改变机制，选定方向贯穿简报、引擎与分集地图；
- 每次升级能指出权力、信息、关系、暴露、资源、时间或代价的变化；
- 每集先兑现一部分当集承诺，再留下具体的决定、危险或问题；
- 没有把格式偏好冒充普遍规律。

这些是所有者自检，不是终审。展示创作者可读的新增/修改摘要，请创作者接受后再作为下游来源；需要质量结论时交给独立的 `$short-drama-review`。本技能不得批准自己的产物。

若项目需要导演阐述，本技能只产出 `development/director-brief.md` 候选，并标明它准备
写入的 `visual_direction` / `production_profile` 语义差异。创作者接受后，由
`$short-drama` 路由把选择提升到 creator authority；开发技能不直接改写该权威配置。

## 规则分级

- **`structural_invariant`**：本地可证明的引用、ID、显式状态矛盾；可由校验器阻断。
- **`reviewed_invariant`**：升级是否真实、承诺是否兑现等语义义务；只能由独立审查者引用证据判断。
- **`craft_default`**：通常有帮助的做法；创作者说明理由后可覆盖。
- **`taste_option`**：钩子、弧线、视角、结局气质等选择；不得单独阻断。

不要用固定的情节点、转折时刻、篇幅比例或数量配方替代因果判断。

## 产物与边界

本技能只拥有：

- `development/creative-brief.md`
- `development/story-engine.md`
- `development/director-brief.md`（项目需要时；仅为 creator authority 候选）
- `development/adaptation-map.jsonl`（长材料改编时；只保留输入 locator/span/hash、
  去引用的功能摘要、候选去向与未决项，不复制原文；例见
  [adaptation-map.example.jsonl](assets/adaptation-map.example.jsonl)）
- `development/series-arc.json`（项目需要时）
- `development/episode-map.jsonl`

它不写场景动作与台词，不拆资产，不写图片/视频提示词，不生成媒体，也不签发终审结论。剧本语义由 `$short-drama-write` 接管。

## 按需加载

- **承诺、引擎、人物压力、升级与铺垫兑现**：[story-craft.md](references/story-craft.md)
- **分集契约、因果节拍、集间交接与地图修订**：[episode-design.md](references/episode-design.md)
- **人物驱动力、前史与切入、跨集变化、信息权限、双轨节奏与恢复记忆**：
  [serial-character-and-memory.md](references/serial-character-and-memory.md)
- **有原材料、需要压缩/合并人物场景/把信息视觉化**：
  [adaptation-craft.md](references/adaptation-craft.md)
- **创作者提供对标作品、样例剧本或提示词，希望学习机制而非仿写表达**：
  [creative-reference-intake.md](references/creative-reference-intake.md)
- **需要区分揭示、反转、回报和钩子，或按题材选压力机制**：
  [reveal-reversal-payoff.md](references/reveal-reversal-payoff.md)
- **按题材选择冲突推进方式、设计开场或规划集尾钩子时的定性案例方法**：
  [genre-and-hook-playbook.md](references/genre-and-hook-playbook.md)
- **已确定题材，需要该题材更常用的压力、场面颗粒、钩子取向与制作难点**：
  [genre-cards.md](references/genre-cards.md)（索引与召回规范；一次只读一张卡）
- **起草项目级视觉方向与生产规则（导演阐述）**：
  [director-brief-craft.md](references/director-brief-craft.md)
- **跨技能事实所有权或修订影响不清楚时**：[contract-and-ownership.md](../short-drama/references/contract-and-ownership.md)
