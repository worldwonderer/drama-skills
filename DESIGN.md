# Design

## Source of truth

- **Status:** Active
- **Last refreshed:** 2026-08-02
- **Primary product surfaces:** `$short-drama dashboard` local project workspace
- **Evidence reviewed:** `skills/short-drama/assets/dashboard/*`, canonical project
  roots and declared artifact owners in `project_tool.py`, lifecycle output, and the
  local-first file-management model used by oh-story-claudecode.

## Brand

- **Personality:** cinematic, calm, editorial, trustworthy.
- **Trust signals:** exact file path, explicit save state, honest lifecycle gates,
  clear generated/fallback/rejected media labels.
- **Avoid:** generic admin-dashboard chrome, neon gradients, decorative clutter,
  hidden writes, or presenting generated previews as approved deliverables.

## Product goals

- Make a filesystem short-drama project understandable within ten seconds.
- Let creators safely find, read, preview, and edit project text.
- Make media review and lifecycle blockers visible without exposing private state.
- Keep the server local-only and dependency-free.
- **Non-goals:** database access, media generation from the browser, delivery-gate
  overrides, or full IDE behavior.
- **Success signals:** the active project and checkpoint are immediately clear;
  filenames do not become an unreadable wall; a creator can distinguish editable,
  protected, preview, fallback, and rejected content without reading implementation docs.

## Personas and jobs

- **Primary personas:** short-drama creator, director/editor, production reviewer.
- **Jobs:** inspect the current project, locate an episode artifact, compare visual
  assets, record a text decision, and understand why delivery is blocked.
- **Contexts:** desktop-first local review, occasional tablet/mobile inspection.

## Information architecture

- **Primary navigation:** project selector → all / development / writing / assets /
  storyboard and video / review and delivery.
- **Workspace:** grouped file browser → focused text or media viewer → lifecycle rail.
- **Content hierarchy:** project title and checkpoint first, file content second,
  operational metadata third.
- **Classification rule:** files follow their canonical project root and episode
  artifact area; media stays with the artifact it represents. Non-standard folders
  remain discoverable in All without becoming a product stage.

## Design principles

1. **Story before machinery:** translate lifecycle keys into creator-facing Chinese;
   retain exact technical values only as secondary evidence.
2. **One focus at a time:** keep navigation, document, and status visually separate.
3. **Honest state:** dirty, read-only, fallback, pending, and blocked states must never
   rely on color alone.
4. **Local-first restraint:** extend existing HTML/CSS/JS; add no framework or runtime dependency.

## Visual language

- **Color:** near-black ink panels, warm amber focus, cool blue informational state,
  red only for blockers/errors, green only for verified success.
- **Typography:** system sans for interface; system mono for paths and structured text.
- **Spacing/layout rhythm:** 4/8/12/16/24/32 px.
- **Shape/radius/elevation:** 8–12 px radius, thin borders, minimal shadow.
- **Motion:** 120–180 ms interface feedback; honor reduced motion.
- **Imagery/iconography:** media itself is the hero; use small text glyphs only when
  they remain understandable without the glyph.

## Components

- **Reuse:** project selector, stage tabs, file list, editor, preview, media viewer,
  lifecycle summary.
- **Change:** add project identity, stage/file counts, grouped file rows, selected and
  dirty states, safe Markdown/JSON preview, media facts, refresh and review affordances.
- **Variants:** editable/read-only/oversize; image/video; normal/pending/blocked/error.
- **Ownership:** tokens and layout live in `assets/dashboard/styles.css`; browser behavior
  lives in `assets/dashboard/app.js`; security remains server-owned.

## Accessibility

- **Target:** WCAG 2.1 AA where practical for this local tool.
- **Keyboard/focus:** visible focus rings; Cmd/Ctrl+S saves; tabs and file buttons are native controls.
- **Readability:** no status is color-only; paths have tooltips; text stays selectable.
- **Screen reader:** labeled navigation, live status, semantic headings and buttons.
- **Reduced motion:** disable nonessential transitions when requested.

## Responsive behavior

- **Desktop:** three columns.
- **Tablet:** navigation plus workspace; lifecycle becomes an inline summary.
- **Mobile:** stacked navigation and workspace with 44 px touch targets.
- **Touch/hover:** hover is supplemental; selected state is persistent.

## Interaction states

- **Loading:** explicit loading copy on project/file changes.
- **Empty:** explain when a workspace, stage view, or search has no matching files.
- **Error:** show the actionable server message in the live status area.
- **Success:** saved state includes timestamp and clears the dirty marker.
- **Disabled:** explain protected or oversize files rather than silently disabling controls.
- **Slow network:** local requests remain cancellable by selecting another file; stale responses
  must not replace the newly selected content.

## Content voice

- Direct creator-facing Chinese; short labels and concrete next states.
- Preserve exact filenames and machine values where they are evidence.
- Call previews “预演/预览”, not “成片”; call unapproved work “候选”, not “交付”.
- Interface copy names the object, current state, or available action. Protocol,
  security-boundary, authority, and design-rationale explanations stay in documentation.
- Use direct affirmative statements. Avoid contrast templates equivalent to
  “不是……而是……” and other rebuttal-style copy.

## Implementation constraints

- Python standard library server; vanilla HTML/CSS/JS; no new dependencies.
- Loopback-only Host/Origin and path/symlink protections remain non-negotiable.
- Browser rendering must not inject project HTML.
- Support current Safari/Chrome and Python 3.10+ on secure dir-fd platforms.
- Every server change requires unit/HTTP tests; every visual change requires a fresh
  desktop screenshot plus narrow-layout inspection.

## Open questions

- [ ] Whether a future public export workflow should be launched from the Dashboard;
  owner: product; impact: write authority and delivery security.
- [ ] Whether creator decisions should receive a dedicated structured schema;
  owner: lifecycle contract; impact: review UX.
