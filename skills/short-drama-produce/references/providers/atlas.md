# Atlas Cloud adapter

Adapter command:

```json
{"command": ["python3", "/absolute/path/provider_adapters.py", "atlas"], "timeout_seconds": 3600}
```

Required environment:

- `ATLASCLOUD_API_KEY`: Atlas Cloud API key.
- `ATLASCLOUD_MODEL`: the exact model id. There is intentionally no default. The Atlas model id also
  selects the task — `bytedance/seedance-2.0/text-to-video`, `bytedance/seedance-2.0/image-to-video`,
  `google/nano-banana-pro/text-to-image`, `bytedance/seedream-v4` — so one configured adapter profile
  serves exactly one task and the profile name in the adapter config is what a job selects.

Optional environment:

- `ATLASCLOUD_BASE_URL` (default `https://api.atlascloud.ai/api/v1/model`)
- `ATLASCLOUD_POLL_INTERVAL` (default `6` seconds)
- `ATLASCLOUD_TIMEOUT_SECONDS` (default `1800` seconds)
- `ATLASCLOUD_MIN_DURATION` and `ATLASCLOUD_MAX_DURATION`: the configured model's explicit inclusive
  duration range. Set both or neither; a `duration` parameter without this profile fails closed.

This adapter covers both `image` and `video` jobs, because Atlas serves them through the same
submit-then-poll shape. Video jobs need exactly one `.mp4` output; image jobs need one `.png`,
`.jpg`, `.jpeg` or `.webp` output.

Accepted parameters — image: `size`. Video: `size`, `duration`, `shot_type`, `generate_audio`.
Anything else fails closed rather than being dropped silently.

- `size` is Atlas's `width*height` form with an asterisk (`1080*1920`). An `1920x1080` spelling is
  rejected by the API, so the compiler refuses it instead of forwarding it.
- `shot_type` is `single` or `multi`. The API rejects an empty string, so it is never forwarded.
- `generate_audio` is a boolean. Seedance generates synced audio by default and the provider blocks a
  clip whose generated score trips its copyright check, which fails the whole job — set
  `generate_audio: false` for a shot that does not need provider audio.

`compile_atlas_payload` accepts `first_frame` and `reference_image` roles with matching HTTPS,
`asset://` or base64 data URI values, and appends the same deterministic reference contract as the
other adapters using `@图片N`. Two limits are deliberate rather than accidental:

- Atlas carries every input image in one newline-separated `images` field and has **no per-reference
  role field**; the opening frame is simply the first entry. A `first_frame` binding that is not
  first fails closed instead of being sent where the provider would read it as a plain reference.
- Only image references are accepted. Reference video and reference audio have no place in this
  request shape, so a job binding them fails closed rather than losing them silently.

Local project references are sent inline as `data:<mime>;base64,<...>` by the bundled runtime, under
the same conservative per-modality guards the other adapters apply.

The adapter submits `POST /generateImage` or `POST /generateVideo`, polls
`GET /prediction/{id}` until a terminal state, and downloads `data.outputs[0]` into a private
temporary directory. Any unknown status fails closed.

Two verified provider behaviours worth planning around:

- `api.atlascloud.ai` answers the Python standard-library default `User-Agent` with
  `403 error code 1010`, so this adapter always sends an explicit agent on its JSON calls. The result
  CDN accepts the default agent, which is why only those calls override it.
- **Image containers and sizes are per-model.** `bytedance/seedream-v4` honoured a requested
  `1024*1536` exactly and returned JPEG bytes; `google/nano-banana-pro/text-to-image` returned JPEG
  at its own `1408x768` regardless of the requested size. So the job's output suffix must match what
  the configured model actually returns — a `.png` target for a JPEG-returning model fails closed
  after download rather than writing mislabelled bytes — and a model must be confirmed to honour
  `size` before a storyboard depends on its aspect ratio.

Protocol reference: [Atlas Cloud docs](https://www.atlascloud.ai/docs) ·
[model catalogue](https://www.atlascloud.ai/models).
