# Storyboard, Keyframe, And Motion Rubric

## 目录

- [Coverage](#coverage-and-meaning)
- [Shot](#shot-purpose-and-geography)
- [Keyframe](#frozen-keyframe)
- [Motion](#motion)
- [Continuity](#cross-shot-continuity)
- [Findings](#common-findings)
- [Production risks and authorized observations](#production-risk-and-authorized-observation-checklist-rev-08)

## Coverage and meaning

- Does every production-relevant block have a disposition?
- Are omissions/repeats explained?
- Does each shot preserve the source action, dialogue, information, and reaction?
- Has downstream work invented or deleted a story fact?

## Shot purpose and geography

- What changes for audience or character by the shot end?
- Why is this a new shot?
- Do framing and camera behavior serve attention, alignment, pressure, reveal, or
  rhythm rather than decorate the prose?
- Are Location/View, axis, screen direction, eyelines, entrances, positions,
  hands, and props coherent?
- Are exact asset variants bound?
- Does `audience_visibility` expose every fact this shot must carry while protecting
  facts the source withholds until a later action or cut?
- Do crop, occlusion, focus, back view, and offscreen space serve that information
  permission rather than accidentally reveal or conceal it?

## Frozen keyframe

- Can all described facts exist in one still instant?
- Does it project the authoritative shot start boundary?
- Are focal hierarchy, composition, camera/lens, geography, pose/gaze/hands/prop,
  expression, and light legible?
- Does it avoid ordered actions, performance arcs, camera moves, or transforming
  environments?

## Motion

- Does it start from the accepted frame/boundary?
- Are subject actions ordered and small enough to preserve intended performance?
- Does performance change express the story move rather than stack synonyms?
- Is camera behavior coherent and motivated?
- Are environment motion, dialogue, SFX, and audio included only when relevant?
- Does the end report match storyboard `end_boundary` and feed no unauthorized
  write into the next shot?
- When a reference frame carries appearance/composition, does motion prose avoid
  dumping the full visual bible?
- Does every claim about text-bearing reference pixels cite a creator/reference-owner
  description or an authorized input-reference observation? Without that evidence,
  is admission still `unverified` instead of pretending a negative prompt solved it?
- For any selective transform, are the accepted trigger, exact target scope,
  end geometry/state, and preserve set explicit? Do non-target people, props,
  text surfaces, and spatial anchors keep their required position, count, and state?
- Does each media reference state its exact purpose, what may be copied, and what
  must not be copied, rather than treating all visible pixels as authority?
- For a pickup or alternate, does `coverage_scope` bind each exact source obligation,
  map it to a current motion field or explicit disposition, and identify the exact
  master/supplement record IDs? A motion may request supersession, but only a
  downstream verdict may bind fixed alternate/master hashes and decide it. Use
  the [supersession decision example](../assets/supersession-decision.example.json)
  when that decision is needed.

Explicit timing that does not sum exactly to the accepted duration—over or
under—is a structural error. Untimed overload is a
reviewed feasibility question: cite which action/performance/story beat would be
lost, not a universal verbs-per-second formula.

## Cross-shot continuity

Trace Look, injury, dirt/wetness, emotion/knowledge, position/facing/gaze, hands,
held prop, prop owner/state, location/time/weather/light, axis, and screen
direction. Distinguish unexplained contradiction from declared montage,
ellipsis, dream, or deliberate disorientation.

## Common findings

- beautiful shot with no dramatic purpose;
- dialogue covered but consequential reaction absent;
- anonymous asset or wrong variant;
- cross-location/time action hidden in one ordinary shot;
- keyframe contains “first/then/finally”;
- locked camera also pans or pushes in the same interval;
- motion invents a grab, injury, transfer, relationship change, or line;
- a selective transform removes, copies, or reshapes a non-target person, prop,
  text surface, or spatial anchor because its preserve set was unspecified;
- end pose/prop state disagrees with next start;
- every emotional beat uses the same close-up/push template;
- provider batching is mistaken for editorial shot structure.
- a narrowed pickup silently replaces the master and drops dialogue, reaction,
  reveal, end-boundary, or accepted-directive obligations.

## Production-risk and authorized-observation checklist (REV-08)

本套件不查看媒体。先把下列项目当作文字风险，引用准确的 prompt/spec 说明为什么会造成
执行歧义或违背已接受约束；只有创作者提供了授权的文字观察记录时，才能称为已观察缺陷，
并把该记录作为外部证据引用。没有观察记录时保持结果未知，不从任务成功、结果地址或提示词
存在推断成片质量。

短剧生产中需要防范的文字风险或获授权观察包括：

- on-screen text or subtitle residue despite a declared no-text constraint;
- a text-bearing reference admitted under no-text or exact-readable policy
  without an allowed surface or a crop, clean, mask, or replace decision;
- background music implied where the audio boundary allows sound effects only;
- wardrobe or look drift against the bound reference (re-describing clothes the
  reference already fixes invites drift — flag redundant appearance prose);
- 180° axis violation or eyeline mismatch across consecutive shots without a
  declared transition;
- lip-sync mismatch: dialogue bound to the wrong character, inner monologue
  written as mouthed speech, or extra lines the source dialogue never contained;
- explicit segment timings that do not sum exactly to the shot duration, in either direction;
- emotion-intensity mismatch between stated level and described performance
  (a declared 9/10 rage performed as mild irritation, or the reverse).
