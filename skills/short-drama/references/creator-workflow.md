# Creator Workflow

## Contents

1. Creator-owned authority
2. Product boundary
3. Creator entry points
4. Checkpoints and branches
5. Revision behavior
6. Delivery boundary

## Creator-owned authority

`short-drama.json#/creator_authority` exists even in script-first/direct-entry
projects. It owns accepted creator constraints, visual direction, and production
profile choices; `创作者决策/<artifact-id>.json` records accept/reject
decisions against exact candidate hashes, one file per artifact. Do not recover
these facts from chat memory or a prompt cache. An unset direction/profile
remains explicitly `unset`, not silently defaulted.

Use [creator-decision.example.jsonl](../assets/creator-decision.example.jsonl) as
the decision shape. Only a creator acceptance operation may advance the
`creator_acceptance` axis. Artifact acceptance records bind
`decision_kind: artifact_acceptance`, the lifecycle `artifact_id`, and the complete
candidate `target_hashes`; owner publish and independent review cannot impersonate it.
当接受记录还承担 Coverage Audition 方案选择时，同一独立决定额外写
`selected_audition_record_id` 与 `selected_approach_id`；正式场次计划分别绑定 audition 与该决定，
不得把决定引用写回 audition 本体后再接受，以免形成 hash 循环。

### Who `decided_by` may name

The whole authority model rests on this field, so it cannot mean “whoever typed
it”. `decided_by` names **the party that carries the consequence of being wrong**:

- `creator` — the person whose project this is, deciding directly.
- `<role>:<stable-id>` — a named delegate the creator authorized for a stated
  scope, such as a producer accepting production-profile choices. The
  authorization itself is a creator decision and must exist first; a delegate
  cannot widen their own scope.

An assistant, an agent, an owning skill, or a reviewer is **never** a valid
value. If no authorized party has decided, the artifact stays `pending` — a
decision record naming a skill as its decider is a fabricated decision, not a
faster one. When the creator is unreachable and work must continue, use the
provisional preview chain below instead of inventing a decider.

A delegate's decision is revocable by the creator without a supersession dispute:
record the creator's replacement decision with `supersedes_decision_id` pointing
at it. The reverse is not available — a delegate cannot supersede the creator.

## Product boundary

Use host-agent text reasoning to create and revise filesystem artifacts. Produce
scripts, asset decisions, image prompts, shot plans, keyframe prompts, video
prompts, findings, and text/JSON delivery packages.

Never call an image, video, or audio generation model. Never submit provider
tasks. Never require an external/private datastore lookup or legacy platform schema.

Treat the creator's accepted files as truth. Treat generated indexes, rendered
prompt Markdown, status, and delivery manifests as projections of that truth.

## Creator entry points

Route by the creator's actual job, not by an internal pipeline phase.

| Creator asks for | Owning skill | Minimum prerequisite |
|---|---|---|
| 从想法开发短剧 | `short-drama-develop` | creator brief or conversation |
| 从已有多集完整剧本生成或补分集地图 | `short-drama-develop` | preserved source; Agent-selected episode boundaries, then verified one-episode slices |
| 写/改一集剧本 | `short-drama-write` | idea, episode card, outline, or existing project |
| 从剧本拆人物场景道具 | `short-drama-assets` | accepted canonical script, or a source script to normalize with preview |
| 做 Look Development / 风格帧提示词 | `short-drama` → `short-drama-image-prompts` | accepted observable visual direction; asset facts when the frame depicts them |
| 写角色/场景/道具图片提示词 | `short-drama-image-prompts` | accepted asset decisions |
| 拆分镜/写关键帧 | `short-drama-storyboard` | accepted script/index and assets |
| 写视频提示词 | `short-drama-video-prompts` | accepted shots and keyframes |
| 检查或审稿 | `short-drama-review` | artifact set to review |
| 初始化、继续、恢复、交付 | `short-drama` | project path or desired new path |

Do not fabricate development artifacts merely to satisfy a direct entry. When a
creator brings a script, preserve the original bytes under `输入/`, propose a
canonical Markdown preview and semantic diff, and continue only after acceptance.

## Checkpoints and branches

```text
C0 direction/story engine accepted as applicable (optional for script-first)
C1 episode card + screenplay accepted
C2 asset identities/variants/continuity accepted
   ├─ optional Look Development after the depicted character/location facts exist
   │    └─ high-pressure frame after its scene facts also exist
   ├─ C3a asset image prompts accepted
   └─ C3b coverage + shots + keyframes accepted
C4 video prompts accepted against C3b boundaries
C5 independent review approved + delivery verified
```

Look Development is a branch, not a universal checkpoint. Direction-only exploration remains a creator choice
inside the accepted visual direction; it is not a `lookdev_frame` artifact. Every frame waits for the accepted
character or location facts it depicts, and a high-pressure frame also waits for the relevant accepted scene facts.
It never blocks an ordinary scene. After C2, asset image prompts and storyboard are sibling branches. Never require asset
image prompts before designing coverage or shots. Video prompts project the shot boundaries defined by
storyboard; they cannot redefine the next shot.

Each checkpoint combines separate facts:

- build state;
- structural validation result;
- creator acceptance;
- independent review status;
- delivery gate.

Never use one `accepted` flag to mean all five.

Owner `publish` verifies only the UTF-8/Markdown/JSON/JSONL container and leaves
structural validation `not_run`. The independent verdict binds exact target and
findings hashes and is the only public operation that records
`pass | pass_with_warnings | fail` on the validation axis. Creator acceptance
does not imply structural validity, and an approval cannot coexist with failed
structural validation or open blockers.

### End-to-end preview without a live acceptance turn

A request such as “把整个流程做出来” authorizes drafting; it does **not** mean the
creator accepted every story choice, asset identity, Look/View/State, shot, or
prompt. Never manufacture creator-decision records or mark their status accepted.

When the creator wants a full preview but intermediate acceptance is unavailable,
continue only as a **provisional preview chain**:

- every dependent artifact says `status: provisional` or `candidate`;
- accepted upstream refs keep exact accepted hashes; refs to exact upstream
  candidates or targets in one atomic candidate publication add `authority: candidate`;
- no field is called `accepted_binding` and no accepted-snapshot hash is claimed;
- creator acceptance stays pending, independent review stays provisional, and
  delivery stays blocked;
- unresolved identity or story meaning still stops the affected dependency;
- later acceptance promotes each exact co-published candidate group through its
  owner transaction; downstream publications then refresh refs/hashes before approval.

This lets a creator inspect the whole production idea without confusing a
forecast with project truth.

## Revision behavior

1. Identify the artifact and exact owner.
2. Read only the owning craft reference and directly required upstream facts.
3. Propose a semantic change, not an unexplained rewrite.
4. Show a creator-readable diff and affected downstream artifacts.
5. Publish only after creator acceptance when the change alters canonical truth.
6. Freeze the accepted candidate's exact inputs. A new upstream candidate marks the
   direct and transitive downstream closure stale in its WAL transaction; do not
   regenerate or invalidate unrelated work. A path removed from a multi-file target
   set invalidates its dependents even though the old file bytes are preserved for
   recovery and comparison.
7. For JSON/JSONL, ensure every canonical ArtifactRef is either bound to a matching
   candidate output in the same publication or declared as an exact external input.
   Declare Markdown dependencies explicitly; omission is not a dependency-free claim.
8. Review and delivery recursively revalidate input hashes and accepted providers,
   including edits that happened outside the publish command.

Rendered prompt Markdown is a cached view. If a creator edits it manually, offer:

- **restore**: regenerate from the accepted structured spec;
- **adopt**: parse proposed changes into a spec diff and request acceptance;
- **merge**: reconcile both through a new preview/diff.

## Delivery boundary

Deliver approved text and JSON only. Exclude private references, absolute paths,
credentials, maintainer evidence, non-public source material, source URLs, transaction state,
and binary media. An intentional URL shown in the story must be declared as
on-screen text with provenance, text policy, and creator acceptance.
