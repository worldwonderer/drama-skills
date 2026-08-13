# 多集整稿接入与断点续跑

当创作者给的是一份已经写好的多集剧本、散稿或合订稿，并希望生成或补齐
`项目开发/episode-map.jsonl` 时使用本流程。它不是小说改编分析，也不是把整份文件塞进一次
提示词：**Agent 判断这份文件实际怎样分集、每集讲了什么；脚本只保存精确定位并防止重复劳动。**

## 不变边界

- 原文件原样保存在 `输入/`；用户输入的 hash 与 byte span 按原始字节计算，CRLF 不做归一化。
- 自动标题识别只生成候选索引，不是对文件语义的裁决。不要为了迎合脚本而改写创作者原文。
- 不把整稿、全部分集正文或整季 map 塞进 heredoc、内联 Python、命令参数或单次模型上下文。
- `episode_intake.py` 不生成转折、回报、钩子等创作字段。它只做索引、校验、单集切片、
  完成集推导和 Agent 所写记录的原子合并。
- 批次没有通用固定值。Agent 先看索引里的各集 `byte_length`、实际复杂度和当前可用上下文，
  再为**本轮**明确选择 `--batch-size`；首轮宜保守，读过真实切片后可以调整。

## 1. 只建一次候选索引

```text
python3 <本技能目录>/scripts/episode_intake.py index \
  输入/<完整剧本> \
  --source-ref 输入/<完整剧本> \
  --out 项目开发/episode-intake-index.json
```

脚本识别短独立行形式的 `第N集`、`第N集 标题` 与 Markdown `# EP N`，统一写成规范
`EP001` ID。索引只含 source hash、行/字节跨度、每集 hash、长度与结构问题，不含正文。

退出码 `0` 表示机械结构自洽；退出码 `2` 表示候选已落盘但有缺号、重号、空集或未识别等
问题。`unmapped_spans` 会用 locator/hash 标出没有归入任何一集的前言、目录、集间说明或
尾注；Agent 必须逐个做小范围检查，决定它确实应排除还是要调整边界，不能让材料静默丢失。
此时根据候选中的 line span，只读取疑似边界附近的小段，判断**这份文件**使用的是
幕标题、分隔线、混合标题、目录加正文还是别的规律。不要扩大自动正则去猜作品语义。

非标准结构由 Agent 写一个很短的 JSONL 边界文件，每行只给规范 `episode_id` 与 1-based
`line_start`，必要时给 `line_end`；然后让工具推导精确 byte span 与 hash：

```jsonl
{"episode_id":"EP001","line_start":12}
{"episode_id":"EP002","line_start":58}
```

```text
python3 <本技能目录>/scripts/episode_intake.py manual-index \
  输入/<完整剧本> <临时边界.jsonl> \
  --source-ref 输入/<完整剧本> \
  --out 项目开发/episode-intake-index.json
```

`manual-index` 仍会机械阻断重号、跳号、倒序、空跨度和非规范 ID，但不会判断一幕是否应该
属于上一集还是下一集；这个选择仍由 Agent 结合文件内容完成。

## 2. 每次只读当前集

任何切片前都验证索引；原文件变化一个字节就重建，不沿用旧 locator：

```text
python3 <本技能目录>/scripts/episode_intake.py verify \
  项目开发/episode-intake-index.json 输入/<完整剧本>

python3 <本技能目录>/scripts/episode_intake.py progress \
  项目开发/episode-intake-index.json 输入/<完整剧本> \
  项目开发/episode-map.jsonl --batch-size <本轮由 Agent 决定>

python3 <本技能目录>/scripts/episode_intake.py slice \
  项目开发/episode-intake-index.json 输入/<完整剧本> EP001 \
  --out <临时单集文件>
```

只读取 `next_batch` 指向的单集切片。Agent 根据每集自己的进入状态、追求、阻力、因果升级、
局部结果和出去压力写 map 记录；需要连续性时只携带已接受的紧凑前情、上一集交接和已有 map
相关记录，不回读整稿。每完成一集先落到短 batch JSONL，随后再处理下一集。

## 3. 合并、发布与恢复

`merge` 读取当前 map，但必须把结果写到另一个候选文件，绝不直接覆盖当前项目事实：

```text
python3 <本技能目录>/scripts/episode_intake.py merge \
  项目开发/episode-intake-index.json 输入/<完整剧本> \
  <本轮 batch.jsonl> 项目开发/episode-map.jsonl \
  --out 项目开发/episode-map.next.jsonl \
  --batch-size <本轮由 Agent 决定>
```

它拒绝未知 ID、批内重号、超过本轮显式上限的记录和对已完成集的冲突重写；相同记录重放是
no-op。合并成功后，通过 core 的公开生命周期把 `episode-map.next.jsonl` 作为同一 artifact
的新候选发布，并绑定完整剧本与索引的精确 hash：

```text
python3 <core 技能目录>/scripts/project_tool.py publish <项目根> \
  --owner short-drama-develop --artifact-id series:episode-map \
  --output 项目开发/episode-map.jsonl=项目开发/episode-map.next.jsonl \
  --input 输入/<完整剧本>=<索引中的 source_sha256> \
  --input 项目开发/episode-intake-index.json=<索引文件的 sha256>
```

发布完成后再删除临时候选和单集切片。不要让 `merge` 直接覆盖
`项目开发/episode-map.jsonl`，也不要绕过 `project_tool.py` 手工替换它。

中断恢复时先运行 core `recover` / `status`，然后重新运行 `progress`。磁盘上的当前
`episode-map.jsonl` 是唯一完成真相；checkpoint 若需要只是由它重建的缓存，不能用
`last_completed` 猜连续进度。已有 `EP001`、`EP003` 时，`EP002` 仍会出现在 pending，已完成
记录不会重跑。全部 `pending` 清空后，再做所有者检查、创作者接受与独立审查。
