# MiniMax H3 提示词方言

只在目标档案明确写 `target_video_model: minimax-h3` 时使用。推荐档案：

```json
{
  "target_video_model": "minimax-h3",
  "video_prompt_dialect": "minimax-h3",
  "video_prompt_language": "en",
  "native_duration_seconds": {"min": 4, "max": 15},
  "supported_generation_modes": ["text", "first_frame", "first_last_frame", "reference"],
  "audio_generation": "same_pass"
}
```

H3 的结构字段用英文；这不等于把中文对白翻成英文。每句中文对白保持原文并写成
`<d>[Chinese] 逐字台词</d>`，由稳定说话人 ID 引出。不要在对白前增加外语、语气词或未写入剧本的开场句。

点名 MiniMax H3 只选定模型与方言，不表示创作者选了文生视频。当镜头需要人物、场景、道具或起始构图一致性时，
先按主 Skill 完成真实图片发现与缺口列表。有任何必要图片仍缺失时，不因 H3 同时支持 Base 模式就默默改用文生视频。

## Base / 首帧 / 首尾帧

文生视频、首帧和首尾帧使用三段结构：

```text
integrated_multimodal_description: [Shot 1] ...
overall_soundscape: ...
non_diegetic_music: ...
```

- 画面段按 `[Shot 1]`、`[Shot 2]` 写动作和镜头；单镜也保留 `[Shot 1]`。
- 声音段闭合所有对白、环境声与音效。无配乐时写 `non_diegetic_music: N/A`，不要留空让模型补乐。
- 首帧模式从输入帧的可见姿态开始；首尾帧模式只能到达上游已接受终点，不发明过渡后的新状态。

## 选哪一种模式

模式由本镜「输入参考图」里各槽位的 `用途` 决定，不由“想不想用多模态”决定：

| 本镜绑定的图 | H3 模式 | 正文结构 |
|---|---|---|
| 没有图，且创作者已明确选择文生视频 | Base（文生） | 三段 |
| 只有一张 `用途：起始帧` | `first_frame` | 三段 |
| 一张 `用途：起始帧` + 一张 `用途：结束帧` | `first_last_frame` | 三段 |
| 其他任何组合（起始帧与人物/地点/道具图同时存在，或只有人物/地点/道具图） | `reference`（full-reference） | 六段 |

H3 官方另有只给尾帧、由模型推断开场的 L2VA 模式。本套件不使用它：开场由分镜已接受的起点决定，
把它交给模型推断会让 `SHOT-...` 的起点失去权威。因此 `结束帧` 只在同时绑定 `起始帧` 时出现。

第四行是最常见、也最容易写错的一种。H3 的首/尾帧输入与参考输入**互斥**，所以「起始帧 + 角色板 +
场景板」不能拆成 `first_frame` 加 `reference_image`：整组统一走 full-reference，起始帧也以
`reference_image` 送入，正文按下面的标签编号引用。用哪一种模式在《视频提示词.md》里由 `用途` 组合读出，
不写进「生成方式」字段——该字段仍只有「文生视频」和「图生视频」两个值。

## Full-reference

任何 `reference_image`、`reference_video` 或 `reference_audio` 进入任务时，改用六段结构，段名和正文说明均用英文：

```text
subject_definitions: ...
summary: ...
retention_analysis: ...
detailed_description: [Shot 1] ...
overall_soundscape: ...
non_diegetic_music: ...
```

**这六段是一条提示词，一次整体提交。** 它们是同一个 `MOTION-...` 的可复制正文，从第一段到最后一段
一起放进那一次生成的提示词框，不是六次生成、也不是只提交第一段。三段结构的 Base / 首帧 / 首尾帧同理。
一镜一次生成，所以下一镜换成它自己那条完整正文。

### 素材标签怎么编号

正文用 `<Picture N>`、`<Video N>`、`<Audio N>` 指向本次 job 的实际素材，`<Subject N>` 指向可复用的可见内容。
**编号按同类素材在本镜「输入参考图」里的 `顺序` 递增**：第 1 张图片是 `<Picture 1>`，第 2 张是 `<Picture 2>`，
视频和音频各自从 1 开始。生产端 compiler 按同一顺序附加引用契约，所以文档里手写的编号必须和 `顺序` 一致；
两套编号不一致时模型会收到互相矛盾的素材说明，而接口不会报错。

因此写 full-reference 正文前，先把本镜的槽位按 `顺序` 列一遍，例如：

```text
REF-SHOT-START（顺序：1；用途：起始帧）  -> <Picture 1>
REF-XIAOYU-SHEET（顺序：2；用途：身份）  -> <Picture 2>
REF-STUDY-PLATE（顺序：3；用途：地理）    -> <Picture 3>
```

`subject_definitions` 把每个主体绑到它的标签上，句式为
`<Subject 1> is the ... in <Picture 2>, with ...`；`retention_analysis` 逐条写保留强度和出现的镜次，
视觉素材用 `fully_preserved`、`partially_preserved`、`attribute_transfer`、`weak_reference`，
音频素材用 `fully_copy`、`partially_copy`、`reference`、`weak_reference`，例如
`<Subject 1> (appears in [Shot 1]): fully_preserved - 短发轮廓、连帽外套配色与鞋型保留`。
每个标签在六段里保持同一写法，不留下没有定义的标签。

### 每张图带什么、不带什么

`用途` 决定这一张在正文里被要求保留什么：`起始帧` 只锚定开场构图与姿态，`身份` 只锚定脸型体态，
`地理` 只锚定空间关系，`造型状态` 只锚定服装与损污。把这条差异写进 `retention_analysis`，
不要让角色板顺带决定构图，也不要让场景板顺带决定人物长相。

角色始终使用同一个说话人 ID；中文对白只出现在 `<d>[Chinese] ...</d>` 内。禁字幕写进可见文字约束，
与获准的画内文字分开。
不要为了“多模态”把所有资产图都带上；每镜只绑定它真正可见且需要保持的起始帧、人物、地点或关键道具图。

### 一个完整例子

分镜里的槽位（三张图：本镜起始帧、角色板、场景板）：

```markdown
- 输入参考图：REF-SHOT002-START（顺序：1）· 剧集/EP001/制作成果/images/SHOT-EP001-002.png《SHOT-EP001-002 起始帧》（用途：起始帧；控制：起始构图、双人站位；不得控制：尚未发生的动作、终态）；REF-XIAOYU-SHEET（顺序：2）· 输入/参考图/小雨定妆.png《小雨定妆照》（用途：身份；控制：脸型、发型剪影、身高比例；不得控制：构图、动作、表情）；REF-STUDY-PLATE（顺序：3）· 输入/参考图/家庭书房.png《家庭书房场景图》（用途：地理；控制：书桌方位、书架墙、灯位；不得控制：人物身份、动作、道具状态）
```

对应的 full-reference 正文（`生成方式：图生视频`，模式为 `reference`）：

```text
subject_definitions: <Subject 1> is the elementary-school boy in <Picture 2>, with a round short black haircut and a light grey-blue hooded jacket over a white inner shirt. <Subject 2> is the home study in <Picture 3>, with the desk against the window wall, a full bookshelf on the left and a warm desk lamp. <Picture 1> is the opening composition of this shot.
summary: The boy leans over an open textbook at his desk and stops when he notices the printed map.
retention_analysis: <Picture 1> (appears in [Shot 1]): fully_preserved - overhead framing, desk edge position and the boy's seated placement open the shot exactly as supplied. <Subject 1> (appears in [Shot 1]): fully_preserved - face shape, hair silhouette and the jacket-over-shirt layering stay identical; his pose and expression are set by this shot, not by <Picture 2>. <Subject 2> (appears in [Shot 1]): partially_preserved - desk orientation, bookshelf wall and lamp position are kept; the props on the desk follow this shot.
detailed_description: [Shot 1] ...
overall_soundscape: ...
non_diegetic_music: N/A
```

三张图各自只负责一件事：`<Picture 1>` 给构图，`<Picture 2>` 给身份，`<Picture 3>` 给地理。
把这条差异写进 `retention_analysis`，角色板就不会顺带决定构图，场景板也不会顺带决定长相。

## 素材数量上限

一次生成最多 1 张首帧、1 张尾帧、9 张 `reference_image`、3 段 `reference_video`、3 段 `reference_audio`；
视频与音频每段 2–15 秒，且各自合计不超过 15 秒。分辨率取 `768P` 或 `2K`（`MiniMax-H3-Max` 另为 `480P`/`768P`）。
超过上限时在分镜阶段按重要性取舍，并写明放弃了哪些参考，不在正文里假装它们仍然生效。

本镜还没有起始帧图片时，它由 `$short-drama-produce` 从 `分镜.md` 的 `SHOT-...` 冻结关键帧生成；
拿到真实文件后由分镜 owner 绑成 `用途：起始帧` 的 `REF-...`，本阶段再原样抄入。

`输入参考图` 只登记参考**图片**。连续段需要的上一段实际视频与实际音频不走这个字段，由下面的
「连续段」小节和生产 job 的绑定承担；不要为了凑成一份清单把视频路径塞进 `REF-...`。

## 连续段

H3 的首/尾帧模式与 full-reference 模式互斥，不能在同一请求混用 `first_frame`/`last_frame` 和
`reference_image`/`reference_video`/`reference_audio`。连续段需要上一段实际视频和实际尾帧时，统一走
full-reference：

- 上一段实际视频：`reference_video`，正文称 `<Video 1>`，控制动作、节奏和声音连续；
- 该视频的实际尾帧：`reference_image`，正文称 `<Picture 1>`，只作为新段开场的可见姿态与构图锚点。

不要把尾帧标成 `first_frame`。这不是措辞偏好，而是 H3 官方接口的互斥输入契约。

## 时长

MiniMax-H3 的整数时长是 4–15 秒，`MiniMax-H3-Max` 是 5–15 秒（不支持 4 秒）。短动作在至少 4 秒的镜头内完成并 hold 已接受终点；超过 15 秒的
镜头在分镜阶段按闭合状态拆开。本阶段不偷改镜头秒数。

依据：MiniMax-H3 官方 base/ref prompt writing guides 与 MiniMax v2 video generation API。
