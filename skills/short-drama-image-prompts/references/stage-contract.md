# 图片提示词阶段契约

本阶段只拥有 `剧集/<EP>/图片提示词.md` 中的 `IMG-...` 项：用途、参考边界和可复制正文。它继承
视觉设定与项目视觉方向，不改写资产身份、地理、剧情状态或镜头边界。

每项参考都写清可以控制什么、不能控制什么；没有获授权的像素观察时保持未知。局部修改直接写目标、
变化、保持项和连续性影响，不建立 prompt spec、QA 或接受记录。

## 本阶段规则

### `IMG`

| ID | Class | Knowledge |
|---|---|---|
| IMG-01 | structural_invariant | Each `IMG-...` item names the exact visual-setting item and current variant it depicts. |
| IMG-02 | craft_default | Put distinguishing identity, geometry, scale, or state before generic quality language. |
| IMG-03 | reviewed_invariant | Character sheets preserve identity while depicting one coherent Look. |
| IMG-04 | reviewed_invariant | Location plates preserve geography, orientation, anchors, material, and light, normally without cast. |
| IMG-05 | reviewed_invariant | Prop plates preserve scale, shape, material, wear, function, and text policy. |
| IMG-06 | structural_invariant | Edit prompts declare exact target, changes, preserve set, and expected continuity impact. |
| IMG-07 | structural_invariant | Readable text cannot coexist with a global no-text constraint. |
| IMG-08 | reviewed_invariant | A claim about reference pixels requires a creator/reference-owner description or authorized input-reference observation bound to the inspected bytes; otherwise admission stays unresolved, and a negative prompt cannot stand in for evidence. |
| IMG-09 | reviewed_invariant | Each reference states its purpose, what may be copied, and what must not be copied; a composition-, scale-, or effect-only reference cannot redefine identity, content, text, or story state. |
| IMG-10 | reviewed_invariant | Views of one Location in the same time/weather state share key-light source, colour-temperature relation, and contrast direction; any difference cites a recorded cause and its delta. |
| IMG-11 | reviewed_invariant | A lookdev frame binds accepted visual direction and production profile across a declared character-expression, core-location, or high-pressure test axis; a high-pressure frame also binds exact screenplay blocks for story state and information permission, while style references may control only declared surface treatment and never identity, fixed geography, story state, cast count, or prop text. |
| IMG-12 | reviewed_invariant | Each real input reference has a stable `REF-...` slot binding explicit order, a visible project-relative path or other unambiguous artifact locator, a Chinese label, and may-control/must-not-control scope. Reordering preserves slot identity; replacing media explicitly revises that slot's locator. `IMG-...` remains reserved for image-prompt headings. |
| IMG-13 | structural_invariant | An `IMG-...` item named by a continuity lock in `视觉设定.md` carries that lock's surface verbatim in its copyable prompt. |

规则分级由高到低：`structural_invariant`（结构缺陷，阻断）、
`reviewed_invariant`（需证据判断）、`craft_default`（常用做法，可覆盖）、
`taste_option`（创作者选择，不作缺陷）。创作者已接受的事实优先于本表。
