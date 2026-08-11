# Contract And Ownership

## Contents

1. Canonical DAG
2. Single-owner registry
3. Output language contract
4. Stable identities
5. Relationship fields without circular hashes
6. Rule classes
7. Trust and privacy boundaries
8. Recovery promises

## Canonical DAG

```text
development? -> screenplay.md -> screenplay-index.jsonl
                                  -> asset occurrences -> decisions -> accepted 设定集
                                                               /              \
                                                    image prompt specs        coverage
                                                           |                    |
                                                   image-prompts.md           shots
                                                                                |
                                                                            keyframes
                                                                                |
                                                                              motions
                                                               \              /
                                                                 review -> delivery

key scenes only: coverage -> audition? -> scene plan? -> shots
visual direction -> lookdev prompt specs (optional)
accepted 设定集 ----------------------^ when a frame depicts those assets
accepted screenplay blocks ----------^ when a high-pressure frame carries story state
```

All repeated values outside their owner carry an artifact hash and field pointer.
The direction of a pointer is also the direction of stale propagation. There is
no motion-to-shot or rendered-Markdown-to-spec authority edge.

## Single-owner registry

| Fact | Authority | Projection behavior |
|---|---|---|
| creator constraints, visual direction, production profile | creator fields in `short-drama.json` | all Skills reference exact accepted project hash/field; direct-entry projects do not rely on chat memory |
| look-development frame composition and prompt projection | image-prompts project-level specs | binds accepted creator direction and source facts; style references cannot own identity, geography, or story state |
| creator accept/reject decisions | creator `创作者决策/<artifact-id>.json` | lifecycle proof binds exact candidate targets and decision record/hash; `decided_by` names the creator or an authorized delegate, never a skill or agent |
| series promise, engine, arc, planned episode contract | develop | episode-card points to the accepted map record/hash |
| script-first standalone episode contract | write | active only when no develop-owned record exists; explicit authority migration if one is later adopted |
| scene execution plan, beats, screenplay | write | may project but not duplicate a develop-owned contract |
| scene/action/dialogue/production directive | write `screenplay.md` | index maps spans and hashes |
| voice record sheet | write `voice-record-sheet.jsonl` | line text projects the exact screenplay block and hash; per-shot audio realization stays with video-prompts and is referenced, not copied |
| planned knowledge/goal/relationship/handoff state | develop episode contract | write points to accepted record while projection mode is active |
| realized knowledge/belief/goal/relationship/emotion change | write screenplay/standalone contract | continuity ledgers carry source pointer, never a second value authority |
| block ID, kind, span, hash | write indexer | points to screenplay snapshot |
| character/location/prop identity and variants | assets | prompt/shot files reference exact ID+variant |
| asset-state delta and scene/episode asset ledger | assets | story-state entries are read-only develop/write projections |
| occurrence extraction | assets | points to source block/hash |
| asset image composition/edit choices | image-prompts spec | rendered prompt is cached |
| optional coverage audition and scene visual plan | storyboard | compares/records directing choices and scene visual/sound movement; only creator-selected accepted plans project into shots, never overwrite screenplay/assets or own shot boundaries |
| coverage, shot purpose, duration, binding, start/end boundary | storyboard | keyframe/motion reference boundary |
| keyframe focal point, composition, camera/lens, frozen staging | storyboard | rendered keyframe prompt is cached |
| motion order, performance path, camera/audio realization | video-prompts | end report compares with shot out-state |
| delivery container membership, order, and container duration | video-prompts `delivery-containers.jsonl` | member accepted durations are read-only storyboard projections carrying refs; container duration equals their sum; rendered container text is cached |
| source analysis layer: chapter index, adaptation triage, story units, entity candidates, adaptation value, episode candidates | novel-analyze `项目开发/source-analysis/` | candidates only; develop turns accepted ones into the adaptation map and contract, and may overturn any of them |
| finding, verdict, revision request | review | evidence points to reviewed artifact/hash |
| input-reference or generated-result production observation | creator or authorized observer in project-private evidence | exact project/prompt/spec/reference slots/config only; review may diagnose and route a bounded revision, never generalize it automatically |
| lifecycle, transactions, snapshots | shared core | metadata and hashes only |

Shot boundary owns start/end position, pose, gaze, hands, held props, and visible
continuity. Keyframes project those facts; they never override them. Motion end is
a comparison report, not a second end-state authority.

## Output language contract

Two fields, deliberately separate:

| Field | Governs | Default |
|---|---|---|
| `short-drama.json#/language` | every artifact a creator reads: screenplay, briefs, review notes, status text, Dashboard | `zh-CN` |
| `short-drama.json#/format/prompt_language` | prompt bodies handed to image and video generators | `en` |

Both are validated for well-formedness at `init` and reported by `status`, so a
skill reads them instead of assuming a default. Malformed tags are refused at
`init` rather than at use: nothing downstream re-checks the value, so an
unchecked tag would propagate into every artifact that claims to follow it.

The split exists because the two audiences fail differently. A creator reading
Chinese wants Chinese; most generators render English prompt text most
reliably. Collapsing the fields would make "show me this in English" silently
change what appears on screen, and "write this project in Korean" silently
degrade every prompt.

Three rules:

- **Creator-facing text follows `language`.** No skill hardcodes a language for
  content a creator reads. Where a creator states a different preference in
  conversation, that preference wins for that exchange and does not rewrite the
  project field.
- **Prompt bodies follow `prompt_language`.** A creator may set it to the
  project language; the suite does not silently override that choice, and does
  not claim a quality result either way.
- **Depicted language is neither of these.** What a character speaks and what
  readable on-screen text says come from the accepted asset records and the
  accepted text policy. Changing a description language never changes depicted
  content.

Which stage reads which field:

| Stage | `language` | `prompt_language` |
|---|---|---|
| core routing, status, Dashboard | yes | reports it, does not consume it |
| develop, write, assets | yes | no prompt body of its own |
| image-prompts | previews, warnings, revision summaries | `prompt_text` and every copyable prompt body |
| storyboard | shot purpose, boundary notes, creator-facing plans | rendered keyframe prompt body |
| video-prompts | end reports and creator-facing summaries | shot prompt body |
| review | findings, impact, revision requests | never authors a prompt body |

Stable rule IDs, asset IDs and lifecycle keys are not prose and stay verbatim
under both fields.

## Stable identities

Use creator-owned IDs:

- `EP001`, `SC001`, `BLK-...`, `BEAT-...`;
- `CHAR-...`, `LOOK-...`;
- `LOC-...`, `VIEW-...`;
- `PROP-...`, `PSTATE-...`;
- `SHOT-...`, `KEY-...`, `MOTION-...`.

Display names may change without changing identity. Reconcile nearby screenplay
edits using prior hashes and local context. When split/merge mapping is ambiguous,
show the ambiguity and request a creator decision; never guess silently.

### Canonical `ArtifactRef`

Every cross-artifact pointer uses one shape; stage-specific aliases such as
`artifact_hash`, `field_pointer`, or a string `path#id` are forbidden:

```json
{
  "owner": "short-drama-write",
  "artifact": "剧集/EP001/screenplay-index.jsonl",
  "hash": "<sha256>",
  "record_id": "BLK-EP001-SC001-A01",
  "field": "/optional/nested/field"
}
```

- `owner`, project-relative `artifact`, and file `hash` are required. An upstream
  input omits `authority` (or declares `accepted`) and binds an accepted snapshot.
  `authority: "candidate"` marks an exact candidate target: either a target
  co-published in the same transaction or an upstream candidate in a provisional
  preview chain. It cannot label an already accepted input or satisfy acceptance
  by itself. Before creator acceptance, every external candidate input must exist
  as an accepted provider at the same hash; atomic acceptance can then promote
  exact co-published groups without rewriting mutually bound bytes.
- `record_id` selects a stable JSONL/Markdown record; `field` is an RFC 6901-style
  pointer inside that record. File-level refs may omit both. Field-only JSON refs
  may omit `record_id`.
- `role`, `access`, or `expected_value` may extend a ref at the consuming layer;
  they never rename the five canonical keys.
- Template placeholders may use `<sha256>`; published refs use 64 lowercase hex.
- A locator for a future, not-yet-created artifact is named `*_locator`, not
  `*_ref`, and cannot satisfy an accepted binding. Its minimum shape is
  `owner`, project-relative `artifact`, a `selector` object, and a status such as
  `future_until_episode_materializes`; it deliberately has no `hash` or
  `record_id` claim until resolved.

## Relationship fields without circular hashes

Choose the field by the authority boundary, not by convenience:

- `*_ids` contains stable record/domain IDs whose definitions are inside the
  same artifact authority. It is a relationship index, not proof of a file
  snapshot. Typical examples are a beat's `because_of_ids` and an episode-map
  record's `setup_ids`/`payoff_ids`.
- Same-file base relationships use `base_look_id`, `base_view_id`, or the
  corresponding stable `*_id`; they never embed their own file hash.
- `*_refs` contains canonical `ArtifactRef` objects whenever the target fact is
  owned by another artifact or a particular target snapshot matters. Typical
  examples are `because_of_refs`, coverage `shot_refs`, and
  `text_treatment_refs`.
- Do not put `path#record`, a bare ID, or prose in `*_refs`. Do not fabricate a
  self-reference whose hash would change when the reference is inserted.
- If a same-artifact relationship later moves to a separate owner artifact,
  migrate the consumer from `*_ids` to `*_refs`; do not keep both as competing
  truth.

This distinction prevents circular hashes without weakening stale detection:
same-artifact IDs are validated inside the file, while every cross-artifact edge
names the owner, project-relative artifact, exact file hash, record, and field
when field precision is relevant.

## Rule classes

Every shipped rule and diagnostic uses one class:

- `structural_invariant`: locally provable; scripts may block.
- `reviewed_invariant`: semantic obligation; an evidence-citing reviewer may
  issue `REVISE` and block delivery.
- `craft_default`: usually useful; warn or ask, but preserve an override.
- `taste_option`: present alternatives; never block by itself.

A diagnostic declares code, class, enforcer, default severity, and owner skill.
Word counts, shot counts, emotional curves, action density, and patterns seen elsewhere
are never structural invariants unless the creator explicitly chose that format.

## Trust and privacy boundaries

Host-agent text inference is expected. Deterministic scripts make no outbound
connection and never call provider or media-generation APIs. Released skills do
not retrieve external or private production sources at runtime.

Private source study becomes shippable only through this chain:

```text
observation -> transferable mechanism -> rule class -> de-copy
-> synthetic example/counterexample -> shipping reference/template/rubric
-> privacy scan -> blind forward test
```

No private project identifier, plot passage, prompt sentence, URL, user data, or
internal schema field name enters released resources.

## Recovery promises

Creator source files are never replaced by partial output. Multi-file publishing
uses a write-ahead manifest, immutable prior/candidate snapshots, per-file
compare-and-swap, commit marker, and read-before recovery.

If live bytes are neither the expected prior nor candidate hash, preserve them as
a conflict and block. Offer adopt, restore, or merge; never overwrite an unknown
external edit. Transaction mechanics stay invisible unless recovery needs a
creator choice.

Acceptance freezes the candidate read set as exact `accepted_inputs`. Publishing a
changed upstream target carries the full direct/transitive downstream stale closure
inside the same WAL manifest, so forward recovery applies target bytes and stale
lifecycle state together.

A consumer of a shared `.json`/`.jsonl` input may additionally freeze
`accepted_input_records`: the canonical hash of each record it actually read. Its
`accepted_inputs` hash then records the binding-time snapshot, while validity is
judged record by record, so appending an unrelated character to a series 设定集 no
longer invalidates every episode that referenced that file. A bound record that
changes, disappears, or stops resolving uniquely still invalidates its consumers,
and so does any change to a whole-file-bound input. Markdown carries no
machine-checkable record identity and is always bound whole-file. Removing a path from a multi-file artifact's candidate
target set invalidates that former accepted/candidate target in the same closure.
The old bytes remain available for creator recovery, but after acceptance the path
has no accepted owner and cannot be delivered. Review and delivery recursively
require every accepted input hash and unique accepted provider to remain current;
dependency cycles, ambiguous providers, stale providers, and out-of-band upstream
edits block.

Before publication, every canonical-looking `ArtifactRef` in JSON/JSONL candidate
outputs is reconciled with the transaction read/write set. A ref to another output
in the same publication must carry that output's candidate hash; every other ref
must match an exact declared input path/hash. Missing or mismatched dependencies
fail before the WAL is written. Markdown dependencies cannot be inferred safely
from prose and remain an explicit owner declaration.
