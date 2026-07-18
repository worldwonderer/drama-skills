---
name: short-drama-develop
description: 将中文短剧想法、梗概、改编材料或已有系列笔记发展成可选择的戏剧方向、创作简报、故事引擎与分集地图。用户提出“开发短剧”“做故事设定/系列大纲/分集规划”“把这个点子变成短剧”或需要梳理人物冲突与集间交接时使用；已有剧本可直接进入写作、资产或审查流程，不强制补开发文件。
---

# 短剧开发

把创作者的意图变成能持续制造**选择、代价与状态变化**的故事系统。保留创作者的题材、结局和尺度选择；不要把流行套路当成剧情答案。

## 先判断入口

1. **只有想法、题材或情绪目标**：完成方向探索，再建立创作简报与故事引擎。
2. **已有梗概、改编材料或系列笔记**：先列出已承诺事实、可改范围和待确认项，再深化；不要偷换原意。
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

需要完整方法时读取 [story-craft.md](references/story-craft.md)。

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
- **需要区分揭示、反转、回报和钩子，或按题材选压力机制**：
  [reveal-reversal-payoff.md](references/reveal-reversal-payoff.md)
- **选题材方向、设计冷开场或规划集尾钩子类型时的生产经验分布**：
  [genre-and-hook-playbook.md](references/genre-and-hook-playbook.md)
- **起草项目级视觉方向与生产规则（导演阐述）**：
  [director-brief-craft.md](references/director-brief-craft.md)
- **跨技能事实所有权或修订影响不清楚时**：[contract-and-ownership.md](../short-drama/references/contract-and-ownership.md)
