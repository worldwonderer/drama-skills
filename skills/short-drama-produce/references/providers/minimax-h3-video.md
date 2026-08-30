# MiniMax H3 video adapter

Adapter command:

```json
{"command": ["python3", "/absolute/path/provider_adapters.py", "minimax-h3"], "timeout_seconds": 3600}
```

Required environment:

- `MINIMAX_API_KEY`: MiniMax API key.
- `MINIMAX_VIDEO_MODEL`: the exact enabled model ID. There is intentionally no default; the adapter
  never assumes which MiniMax video release an account has.
- `MINIMAX_VIDEO_RESOLUTIONS`: comma-separated subset the configured model actually accepts, drawn
  from `480P`, `768P`, `2K`. The published envelopes differ per release, so this is configuration,
  not a constant.
- `MINIMAX_VIDEO_MIN_DURATION` and `MINIMAX_VIDEO_MAX_DURATION`: the configured model's explicit
  inclusive duration range in whole seconds. Set both or neither; a job carrying `duration` without
  the profile fails closed.

Optional environment:

- `MINIMAX_VIDEO_BASE_URL` (default `https://api.minimax.io/v2`)
- `MINIMAX_VIDEO_RATIOS`: comma-separated subset of `adaptive`, `1:1`, `3:4`, `4:3`, `9:16`, `16:9`,
  `21:9`.
- `MINIMAX_VIDEO_POLL_INTERVAL` (default `5` seconds)
- `MINIMAX_VIDEO_TIMEOUT_SECONDS` (default `1800` seconds)

The job must have modality `video` and exactly one `.mp4` output. `duration` and `resolution` are
required and are only accepted when the runtime profile above permits their values. `ratio` is
optional for reference-conditioned jobs and **required** for text-to-video, where `adaptive` is
refused because there is no reference frame to adapt to.

The prompt is compiled into one `text` item of the multimodal `content` array and is refused above
7000 characters. Each declared reference becomes one further `content` item carrying an explicit
`role`: `first_frame`, `last_frame`, `reference_image`, `reference_video`, or `reference_audio`.
`first_frame` and `last_frame` may each appear once. Reference URLs must be HTTPS or `mm_file://`;
local references fail closed, exactly as for Seedance, because the public contract does not prove
that a local data URL is accepted. A deployment that needs image-to-video must add an external,
authorized upload step and call `compile_minimax_h3_payload` with the resulting URI; do not put a
temporary provider URL into the creator project.

The adapter creates an asynchronous task, polls `GET {base}/video_generation/{task_id}` until a
terminal state, and downloads `task.content.url` into a private temporary directory. Any unknown
status fails closed.

## What this model changes for the prompt itself

This release generates audio in the same pass as the picture. That is a **capability**, not a style:
what changes upstream is only which axes of the target-model profile a project declares — see the
video-prompt skill's target-model profile. Two consequences are worth stating here because they
show up as production defects rather than as API errors:

- an unstated audio channel is not a silent one. A shot that says nothing about music or speech can
  come back with invented score and invented lines, so the shot's own sound intent, including what
  must **not** be produced, has to be in the copyable body;
- readable on-screen text is likewise produced unless it is excluded, so a shot with no readable-text
  obligation states that exclusion instead of relying on the model to omit it.

Neither is written here as a fixed phrase, and this suite never injects one. The wording belongs to
the shot and to the project's declared prompt language.

Protocol reference: [MiniMax video generation API](https://platform.minimax.io/docs/api-reference/video-generation-v2-create).
