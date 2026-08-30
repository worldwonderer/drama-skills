# 分镜阶段契约

本阶段只拥有 `剧集/<EP>/分镜.md` 中的 `SHOT-...`、镜头职责、来源、时长、起止边界、声音、视觉依据和
冻结关键帧。它继承剧本事实与视觉设定，不改写剧情或资产身份。

覆盖比较、场次视觉计划和调度推演都留在上下文；只有最终镜头与关键决定进入 `分镜.md`。来源使用
场景 ID。已存在的图片提示词条目可用 `IMG-...` 可见标题引用；它不是已生成图片的证明，也不是分镜的前置门禁。没有条目时直接引用《视觉设定.md》，不建立 coverage、shots、keyframes、audition 或接受记录。

## 本阶段规则

### `SHT`

| ID | Class | Knowledge |
|---|---|---|
| SHT-01 | structural_invariant | Every production-relevant scene action, line, sound and on-screen text is carried by a shot or explicitly omitted with a creative reason. |
| SHT-02 | reviewed_invariant | Each shot has a dramatic/viewing purpose and preserves its source meaning. |
| SHT-03 | craft_default | Keep a short shot focused on the smallest action/reaction unit that carries its purpose; combine or split it according to performance, information, and continuity rather than a fixed count. |
| SHT-04 | craft_default | Change framing/camera because attention, pressure, alignment, reveal, or rhythm changes. |
| SHT-05 | structural_invariant | A keyframe projects one shot boundary and the shot's declared visual basis. Any `IMG-...` used is an existing image-prompt heading, not a claim that generated media exists; when none is suitable, the shot names the relevant textual visual-setting entries without inventing an ID. |
| SHT-06 | reviewed_invariant | A keyframe is one freezeable instant, not an ordered action chain. |
| SHT-07 | taste_option | Lens vocabulary, tempo, and locked/handheld/formal style follow visual direction. |
| SHT-08 | reviewed_invariant | Each authoritative source action is realized once; repeated coverage adds reaction/detail/recontextualization rather than replaying it. |
| SHT-09 | reviewed_invariant | Exact Location/View orientation and visible anchors match the camera side used by the shot. |
| SHT-10 | reviewed_invariant | Rendered keyframe prose contains only facts from the boundary that keyframe declares. A start frame carries no state first created by the shot's motion or end; an end frame carries no state already spent before it. Neither frame borrows the other's facts. |
| SHT-11 | craft_default | When information changes another person's power, relationship, knowledge, or choice, preserve that reception visibly; shot count, framing, and duration follow the consequence and project profile. |
| SHT-12 | reviewed_invariant | Each audience-visibility fact binds its exact source, carrier, permission, trigger, and protection method; framing neither reveals that fact early nor hides the carrier this shot must communicate. |
| SHT-13 | reviewed_invariant | Multi-character blocking projects sourced, directed relationships into compatible positions, gaze, distance, and action lines for the current boundary. |
| SHT-14 | reviewed_invariant | A contested moving object preserves ownership, trajectory, direction, time/round state, and end location across cuts unless an authorized ellipsis says otherwise. |
| SHT-15 | reviewed_invariant | When the creator has declared delivery-surface overlay regions with their permanence and source, what a shot must be read for—face and gaze, readable evidence text, the decisive hand action—does not sit only inside those regions, and shots bind the declared version. An undeclared surface leaves the rule inactive: record it as unresolved and do not restage against a guessed region. |
| SHT-16 | structural_invariant | The episode duration is the arithmetic sum of visible shot durations; an unresolved duration stays visibly unresolved, and a target delta is reported rather than used as a universal quality gate. |
| SHT-17 | structural_invariant | A keyframe declares which boundary it freezes and binds that shot's matching boundary field. An end keyframe is a projection of `end_boundary`, never a second end-state authority, and per-shot keyframe count stays open: one start frame by default, an end frame only when the delivery workflow consumes it. Handing over a start/end pair delegates the motion between them to interpolation, so an action the shot exists for cannot rest on that gap alone. |
| SHT-18 | craft_default | For a scene where directing choice materially changes audience knowledge, alignment, spatial pressure, performance ownership, or the landing, an accepted scene visual plan may bridge project direction and shots; it binds exact screenplay blocks, direction/profile, Location/View and relevant asset states, ordinary scenes skip it, and it never owns screenplay facts or shot boundaries. |
| SHT-19 | reviewed_invariant | When a coverage audition is used, its approaches genuinely differ by knowledge timing, alignment, performance space, strongest image, landing, losses, or production fit; it uses no fixed option, grid, framing, or shot-count formula, and the selected approach is stated before formal shots are written. |
| SHT-20 | reviewed_invariant | Shot revision identity follows directing responsibility rather than array position or text similarity: reorder preserves IDs, insertion creates one, split/merge retires replaced IDs and creates successors, and active coverage plus downstream refs are reconciled before delivery. |
| SHT-21 | reviewed_invariant | A keyframe's copyable text carries only what will be visible; IDs, workflow notes, file paths and craft commentary stay outside the prompt. Two prompts that differ only by identifiers are a template, not two frames. |

### `CON`

| ID | Class | Knowledge |
|---|---|---|
| CON-01 | structural_invariant | Linked end and next start states match or have an explicit owner revision. |
| CON-02 | reviewed_invariant | Knowledge, injury, ownership, weather, light, or physical state does not teleport/regress without story cause. |
| CON-03 | craft_default | Track downstream-relevant deltas, not the whole 设定集 in every shot. |
| CON-04 | structural_invariant | A continuity change states before, after, cause/source scene, effective range, and affected visible IDs. |
| CON-05 | taste_option | Declared montage, ellipsis, dream, or subjective imagery may intentionally break ordinary continuity. |
| CON-06 | structural_invariant | A continuity change names every existing downstream document it affects; future work is described, not pre-created. |
| CON-07 | structural_invariant | A continuity lock declared in `视觉设定.md` fixes one verbatim surface plus its shot and image scope; every in-scope frozen keyframe, motion prompt, and named image prompt carries that surface unchanged. |

规则分级由高到低：`structural_invariant`（结构缺陷，阻断）、
`reviewed_invariant`（需证据判断）、`craft_default`（常用做法，可覆盖）、
`taste_option`（创作者选择，不作缺陷）。创作者已接受的事实优先于本表。
