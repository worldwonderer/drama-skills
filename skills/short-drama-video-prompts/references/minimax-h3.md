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

用 `<Picture 1>`、`<Video 1>`、`<Audio 1>` 指向当次 job 的实际素材，并分别说明保留什么、不得控制什么。
角色始终使用同一个说话人 ID；中文对白只出现在 `<d>[Chinese] ...</d>` 内。禁字幕写进可见文字约束，
与获准的画内文字分开。

## 连续段

H3 的首/尾帧模式与 full-reference 模式互斥，不能在同一请求混用 `first_frame`/`last_frame` 和
`reference_image`/`reference_video`/`reference_audio`。连续段需要上一段实际视频和实际尾帧时，统一走
full-reference：

- 上一段实际视频：`reference_video`，正文称 `<Video 1>`，控制动作、节奏和声音连续；
- 该视频的实际尾帧：`reference_image`，正文称 `<Picture 1>`，只作为新段开场的可见姿态与构图锚点。

不要把尾帧标成 `first_frame`。这不是措辞偏好，而是 H3 官方接口的互斥输入契约。

## 时长

MiniMax-H3 的整数时长是 4–15 秒。短动作在至少 4 秒的镜头内完成并 hold 已接受终点；超过 15 秒的
镜头在分镜阶段按闭合状态拆开。本阶段不偷改镜头秒数。

依据：MiniMax-H3 官方 base/ref prompt writing guides 与 MiniMax v2 video generation API。
