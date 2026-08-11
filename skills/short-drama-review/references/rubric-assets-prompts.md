# Assets And Asset-Image Prompt Rubric

## Occurrence and decision

- Does every extracted occurrence point to a source block/hash?
- Is it production-relevant rather than every noun in the screenplay?
- Is the decision explicit: reuse, new identity, new variant, or unresolved?
- Were pronouns, aliases, groups, memories, portraits, and screen content handled
  without guessing?

## Identity versus variant

### Character / Look

Identity: stable face/body/hair anchors, distinguishing marks, voice/behavioral
identity. Look: wardrobe, makeup, hair arrangement, injury, dirt/wetness, disguise,
age/weathering state, validity range.

Fail when a costume change creates a new person or incompatible Looks mix in one
prompt without story reason.

Relationship labels may guide current blocking, gaze, and social presentation,
but they do not prove beauty, ugliness, body type, skin tone, or facial morality.
Fail when protagonist/antagonist status silently invents those identity traits;
creator-approved idealization or deliberate counter-casting remains a taste choice.

### Voice direction (optional, `AST-07`–`AST-12`)

Only reviewed when a project records `voice_direction`. Timbre is carried by a
bound reference recording, not by prose: identity is the reference binding plus
the accepted pronunciations, and the text alongside it exists to select or judge
a candidate, never to stand in for one.

Fail when:

- a project has no reference and a paragraph of adjectives is standing in for
  identity — that record must read `reference: null` and 待选型 instead;
- a binding omits what it controls and what it must not, or lets the take's
  emotion, the recording space (reverb, mic distance) or background noise into
  identity (`AST-08`);
- a claim about what is audible in a reference has no creator description and no
  authorized listening observation, yet admission is not `unverified` (`AST-09`);
- one proper noun appears with two accepted spellings anywhere in the 设定集
  (`AST-10`) — this one is structural and blocks;
- two characters designed together are bound to confusable references and no
  distinguishing trait names the nearest neighbour (`AST-11`);
- a selection criterion is an emotion word, carries no counter-example, or the
  list has grown past a handful (`AST-12`);
- reference audio bytes, a provider parameter, a model name, a task field or a
  URL appear in the 设定集 or the delivery package.

The rendered `voice-casting.md` is a cache. A finding against it is a finding
against the accepted record it projects, routed to assets — never a request to
hand-edit the derived text.

### Location / View

Identity: architecture, layout, entrances, zones, anchors, materials, navigation.
View: camera-facing orientation/zone, time/weather/light state, visible anchors.

Fail when each camera angle becomes a new unrelated location or geography changes
silently between scene and plate.

Views of one Location sharing a time/weather state must also share key-light source,
warm/cool priority, contrast direction, and practical on/off state (`IMG-10`). Compare
the group side by side—each plate reading well alone is not evidence. Fail when an
unrecorded difference would read as two times or two rooms once the plates are
intercut; orientation-driven back/front lighting and occlusion shadows are expected
and need only a stated source.

### Prop / State

Identity: scale, shape, material, function, moving parts, marks, text policy.
State: owner/hand/location, open/closed, clean/damaged/wet/bloodied, contents,
validity.

Fail when a prop teleports, changes scale/material, or readable evidence is erased.

## Prompt recipe review

All prompt types need purpose, exact binding, identifying facts, current variant,
composition, background, lighting, text policy, constraints, and exclusions. Then
apply type-specific criteria:

- Does each media reference declare one purpose, the facts it may copy,
  the facts it must not import, and a pixel/text admission decision?
- If a reference is composition-, scale-, or effect-only, did the prompt avoid
  borrowing its identity, wardrobe, content, text, count, or story state?

- **Character sheet:** one identity and coherent Look; useful reference views;
  neutral enough background/light to recognize anchors; no story action chain.
- **Location plate:** navigable geography, orientation, fixed anchors, material,
  palette, light direction, atmosphere; normally empty of cast.
- **Prop plate:** scale cue, shape, materials, wear, function/moving parts, current
  state, viewing angle, isolation, text policy.
- **Edit delta:** exact target/hash/region, changes, preserve set, expected
  continuity impact; no unrelated regeneration.

## Prompt quality failures

- quality/style boilerplate appears before or instead of identity/geography/scale;
- prompt copies the whole 设定集 rather than the needed variant;
- character description mixes immutable anchors with accidental pose;
- location prose is rich but cannot orient entrances/zones;
- one character or prop is assigned to mutually exclusive positions, hands, or
  relationship lines in the same frozen instant;
- prop has no scale or text state;
- negative constraints contradict the required visible fact;
- edit request says “make better” without target/change/preserve;
- rendered Markdown differs from accepted structured spec;
- private URL, source ID, provider task field, or operator complaint leaks in.
- “use the whole reference” does not say what may or may not be copied.

Prompt prose elegance is secondary to recognition, reuse, continuity, and clear
control of the current operation.
