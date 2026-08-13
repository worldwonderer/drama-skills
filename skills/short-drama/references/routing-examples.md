# Routing Examples

| Request | Route | Important behavior |
|---|---|---|
| “根据这个点子写第一集” | write, or develop if creator asks for alternatives | do not force series planning |
| “这份完整剧本有 54 集，帮我生成/补 episode map” | develop multi-episode intake | Agent inspects this file's boundaries; index once, slice one episode at a time, land small resumable batches; never inline or reread the whole season |
| “把这份旧剧本的人物场景道具拆出来” | assets via minimal write-owned intake | preserve original bytes; preview normalization |
| “先写所有角色定妆图提示词” | image-prompts | require accepted asset identities/Looks, not storyboard |
| “这段对话拆成镜头” | storyboard | establish coverage and purpose before camera decoration |
| “根据这些关键帧写视频提示词” | video-prompts | read shot boundary; do not rewrite it |
| “人物上一镜拿杯子下一镜没了，检查一下” | review | cite continuity evidence; route fix to owner |
| “检查这集有没有模板感/AI 味” | fresh review | 诊断模板感，只引用位置、损失和 owner，不改正文 |
| “直接把这集去 AI 味并保留人物口吻” | write | 做定点修订与语义 diff，不自称独立审查通过 |
| “先检查模板感，再按问题改掉并复查” | fresh review → write → fresh re-review | 冻结旧 hash；finding 路由给 owner；新 hash 由另一 fresh reviewer 复查 |
| “继续” | router | resume the requested/most recent owner, not blindly run all stages |

## Ambiguous request

For “帮我把这集做完”, show the current creator-facing state and offer no more
than three meaningful actions. Prefer the action that unblocks the requested
delivery. Avoid asking technical questions about schemas or transactions.

## Direct-entry rule

An artifact can be a valid entry even when upstream optional work is absent:

- existing script -> assets;
- accepted assets -> asset image prompts;
- accepted script + assets -> storyboard;
- accepted shots + keyframes -> video prompts.

Create only missing canonical prerequisites owned by the relevant skill. Never
backfill invented creative briefs or series arcs.
