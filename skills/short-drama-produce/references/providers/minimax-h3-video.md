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
Set the job's `parameters.prompt_language` to the resolved video-prompt language; the compiler
consumes it when appending reference semantics and does not send it as a MiniMax request field.
`first_frame` and `last_frame` may each appear once. The published envelope also caps reference
conditioning at 9 `reference_image`, 3 `reference_video` and 3 `reference_audio` items, with video and
audio clips between 2 and 15 seconds each and at most 15 seconds in total per modality; the compiler
does not count them, so a configuration that can exceed those numbers has to bound them itself.
frame conditioning (`first_frame` / `last_frame`) and full-reference conditioning
(`reference_image` / `reference_video` / `reference_audio`) are mutually exclusive in one request.
For continuation, bind the previous actual video as `reference_video` and its actual tail as
`reference_image`; the compiler labels them `<Video 1>` and `<Picture 1>` in its appended contract.
Never relabel that tail as `first_frame` while a reference video is present. Reference URLs may be HTTPS, `mm_file://{file_id}`, or a `data:<mime>;base64,<...>` URI — all three
are documented inputs. A project-relative reference in the confirmed job is read from disk and sent
inline as a data URI, so binding an image in the creator documents is enough to run the job; no
upload service is required. Each file's bytes must match the media type its extension claims, and
the published per-modality caps apply: 30MB per image, 50MB per video, 15MB per audio clip, and 64MB
for the whole request measured after base64 expansion. A reference past those caps fails closed with
an explicit message; host it and bind an HTTPS URL instead of splitting it.

Each reference takes its provider role from the confirmed job's `reference_bindings[].role`, which is
where the creator document's `用途` is translated for this provider. A job that carries references
without those bindings fails closed rather than guessing a role.

The adapter creates an asynchronous task with `POST {base}/video_generation`, polls
`GET {base}/query/video_generation/{task_id}` until a terminal state, and downloads `task.content.url` into a private temporary directory. Any unknown
status fails closed.

## What this model changes for the prompt itself

This release generates audio in the same pass as the picture. Its direct prompt dialect is also
structured: base/first-frame/first-last-frame tasks use `integrated_multimodal_description`,
`overall_soundscape`, and `non_diegetic_music`; full-reference tasks use the six-section H3 reference
form. Chinese dialogue remains exact inside `<d>[Chinese] ...</d>`. Full details live in the
video-prompt skill's `references/minimax-h3.md`.

Audio generation is a **capability**, not a style:
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
