# Demo：一集短剧的创作者可读文本摘录链

本目录用四份易读 Markdown 展示剧本怎样交接到资产说明、分镜和视频提示词。它是
创作者可读的摘录链，不冒充 canonical JSON/JSONL、creator acceptance、独立审查或
交付包。题材为都市打脸（合成示例，情节、人物、地名均为原创虚构；制作做法来自
各技能的参考资料）。

| 文件 | 环节 | 对应技能 |
|---|---|---|
| [EP001-剧本.md](EP001-剧本.md) | 分集剧本（生产方言格式） | `short-drama-write` |
| [EP001-资产设定.md](EP001-资产设定.md) | 角色三视图 / 场景方位图 / 物品白底图 | `short-drama-assets` + `short-drama-image-prompts` |
| [EP001-分镜.md](EP001-分镜.md) | 原文落实 + 镜头职责 + 观众可见性 | `short-drama-storyboard` |
| [EP001-视频提示词.md](EP001-视频提示词.md) | 母版与补拍版的视频提示词约定 | `short-drama-video-prompts` |

阅读顺序展示主要交接；资产图片提示词与分镜可在资产接受后并行。可对照各文件开头的
“工艺要点”查看对应参考资料。

## 关于剧本格式

[EP001-剧本.md](EP001-剧本.md) 使用**生产方言格式**，用于展示创作者交来的稿件长什么样，
不是本套件的规范格式。规范格式见
[screenplay-format.md](../skills/short-drama-write/references/screenplay-format.md)；
`screenplay_index.py` 只解析规范语法，方言稿件要先经 `short-drama-write` 的规范化入口
转换并由创作者接受，之后才发布 `screenplay.md` 与索引。直接对方言稿件建索引会得到
零个 block 与一组 `content_outside_scene`，这是预期行为，不是工具故障。
