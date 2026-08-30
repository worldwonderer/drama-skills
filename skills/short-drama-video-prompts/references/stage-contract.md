# 视频提示词阶段契约

本阶段只拥有 `剧集/<EP>/视频提示词.md` 中的 `MOTION-...` 项与按需的时间线音乐章节。它继承镜头
起点、终点、时长、对白和冻结帧，不回写分镜或视觉设定。

每项正文只描述从当前冻结起点到终点的变化；多镜打包、补拍或替代关系确有需要时，也写在同一文档
的可读章节中，不建立 motion spec、container、readiness、QA 或接受记录。

## 本阶段规则

### `VID`

| ID | Class | Knowledge |
|---|---|---|
| VID-01 | structural_invariant | Motion reads but cannot rewrite shot start/end/duration/dialogue and next-shot state. |
| VID-02 | craft_default | Write start anchor, ordered subject motion, camera behavior, timing, and end report; add performance change and environment/audio only when this shot actually carries them. |
| VID-03 | structural_invariant | Choose text-to-video only when no real input image exists, and image-to-video only when the matching storyboard declares readable REF inputs. Copy those REF declarations unchanged. A text-to-video prompt carries a non-empty static visual anchor in the copyable text; only image-to-video may omit appearance/composition already carried by its real reference frame. |
| VID-04 | structural_invariant | Explicit segment timing sums exactly to its shot's accepted duration—neither exceeding it nor leaving an unallocated remainder. |
| VID-05 | reviewed_invariant | Untimed action load must be feasible enough to preserve the intended performance and story change. |
| VID-06 | structural_invariant | Locked and moving camera instructions cannot govern the same interval without an explicit transition. |
| VID-07 | taste_option | Camera may be locked or moving; audio/lip-sync detail follows the chosen production profile. |
| VID-08 | reviewed_invariant | Structured motion names this shot's exact subjects, actions, contacts, and results rather than reusable placeholders; when a performance path is present, it names only the actors and visible changes this shot actually carries. |
| VID-09 | structural_invariant | The next start names an existing `SHOT-...` or is explicitly unresolved; never invent a hidden source key, record or hash. |
| VID-10 | craft_default | Resolve one accepted production profile for the current delivery scope; local variants may coexist when their range and precedence are explicit, without overriding source coverage or exact-readable obligations. |
| VID-11 | reviewed_invariant | A selective transform names its trigger, exact target scope, end geometry/state, and preserve set so non-target people, props, text surfaces, and spatial anchors do not change with it. |
| VID-12 | reviewed_invariant | A pickup or alternate names stable `MOTION-...` IDs and maps each source requirement to this version, the master, another pickup, or a requested storyboard revision; it never silently replaces the master. |
| VID-13 | structural_invariant | A multi-shot delivery group contains contiguous shots from one scene with one geography/asset chain. Its duration equals the sum of member shots, and grouping changes neither shot boundaries nor per-shot reviewability. |
| VID-14 | craft_default | Music intent may be annotated per shot as a relative entry/exit/duck against neighbours, but its realization belongs to the timeline layer; no deliverable—single-shot or multi-shot container—carries a baked-in music bed unless the project accepted otherwise or the source is diegetic. Dialogue, off-screen sources, ambience, and event effects stay with the deliverable. |
| VID-15 | structural_invariant | Within one episode a shot belongs to at most one delivery group; grouped and loose shots account for the visible shot set exactly once. |
| VID-16 | reviewed_invariant | When performance changes, multi-character motion differentiates the actors who actually carry it and keeps each chosen signal readable in the accepted framing; it does not require an arc for non-performing shots, force every craft field, or duplicate one emotion across the cast. |
| VID-17 | reviewed_invariant | Multiple references use stable visible IDs and an explicit order, so reordering cannot silently change which reference controls which property. |
| VID-18 | reviewed_invariant | Readiness is reported from the current visible inputs and real blocking gaps, not persisted as a motion fact; text readiness never claims generated identity, performance, lip-sync, mix, edit or market quality. |
| VID-19 | reviewed_invariant | When the creator profile declares required literal tokens for a delivery route, that route's delivery text preserves them byte-for-byte and outside the verbatim-dialogue fence. Paraphrase, translation, reordering, or omission is treated as a defect because a literal-matching surface has no reason to reject the rewritten text—the failure is silent rather than reported. The suite asserts no specific surface's behaviour, and whether a given result took the route is returned adherence, provable only by a bound production observation. Tokens declare a route and never substitute for the start state, action, or endpoint. Absent a declared token list the suite invents none; until the profile exposes a machine-readable list at a pinned field path, the reviewer cites the profile against the delivery text rather than claiming mechanical enforcement. |
| VID-20 | reviewed_invariant | Packing routes change delivery granularity only, leaving shot boundaries, shot purpose, and per-shot reviewability intact. A single long-form generation carrying several accepted shots *is* a multi-shot container and is billed under VID-13 and VID-15; it introduces no separate accounting and no exemption from the contiguity, binding-chain, and scene-boundary constraints. A continuation route instead starts from a previously generated result, which is observation evidence and not an accepted artifact: the accepted shot start boundary stays the sole authority, and any claim about the observed state binds a production observation record or remains `unverified`. |
| VID-21 | craft_default | When the project's accepted production profile has generated imagery carry the frame, actions are written as high-frequency, whole-body or single-limb movements common in everyday footage; precise interception, invisible internal states, negative actions, and three-or-more-step two-handed choreography are rewritten into equivalent common-action combinations, with the dramatic information carried by combination and timing. The rule is inactive for live action or an undeclared profile, and a rewrite may never change accepted shot boundaries, terminal states, or screenplay fact. |
| VID-22 | reviewed_invariant | Copyable text carries only what will be filmed. IDs, workflow status, hashes, file paths and craft notes stay outside the prompt; negative intent is rewritten as a visible positive state. A prompt that only recites constraints renders as nothing. |
| VID-23 | reviewed_invariant | When the target model is declared to generate audio in the same pass, the copyable body states the sound this shot needs and the sound it must not produce; an unstated channel is filled by the execution end, not silenced. |

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
