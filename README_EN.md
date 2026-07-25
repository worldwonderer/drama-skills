[中文](README.md) | **English**

# Drama Skills

An AI short-drama creation suite for screenwriters, motion-comic studios, and
directors. It develops an idea or long-form source into episode scripts, asset
decisions, image prompts, storyboards/keyframes, video prompts, and independent
review records, while carrying creator decisions, source evidence, and
continuity through the entire chain. It works with Claude Code, Codex, and other
runtimes that support Agent Skills.

The current release delivers scripts, asset notes, storyboards, prompts, review
records, and structured text. It does not call image, video, or audio generation services,
and it does not infer rendered quality from prompt text or task status.

## Core ideas

Three principles run through the whole chain:

> 1. **A script delivers performable, producible facts.** Prefer action,
>    evidence, blocking, and dialogue strategy; use VO/OS, screen text, or
>    performance notes only when the creator deliberately chooses them.
> 2. **Assets own identity and state; storyboards own the current presentation.**
>    Reference-sheet layout, background, and view count follow the project's
>    reuse job, so each composition serves an explicit downstream purpose.
> 3. **Continuity is explicit engineering.** Compare authoritative shot
>    boundaries exactly, then repeat only the local anchors execution needs and
>    use project records as the shared memory across stages.

Above those three sits a **four-tier rule system** (structural invariant /
reviewed invariant / craft default / taste option) that separates deterministic
file and contract checks from evidence-based craft review and creator-owned style
choices.

Genre playbooks choose craft by pressure source, character strategy, audience
payoff, and production load. A production-form card then translates live action,
2D motion comics, stylized 3D, ink work, and other directions into shape, layers,
material, light, action, and sound across writing, assets, storyboards, and prompts.

## Production chain

```mermaid
flowchart LR
    classDef phase fill:#e8f4fd,color:#1a1a2e,stroke:#4a9be8,stroke-width:1px
    classDef final fill:#fce4ec,color:#333,stroke:#e57373,stroke-width:1px

    dev["Story development<br/>$short-drama-develop"]:::phase
    write["Episode script<br/>$short-drama-write"]:::phase
    assets["Asset decisions<br/>$short-drama-assets"]:::phase
    img["Image prompts<br/>$short-drama-image-prompts"]:::phase
    sb["Storyboard/keyframes<br/>$short-drama-storyboard"]:::phase
    vid["Video prompts<br/>$short-drama-video-prompts"]:::phase
    rev["Independent review<br/>$short-drama-review"]:::final
    pkg["Text delivery package"]:::final

    dev -.optional.-> write --> assets
    assets --> img
    assets --> sb --> vid
    img --> rev
    vid --> rev --> pkg
```

`$short-drama` is the entry router: it initializes, resumes, recovers, and
delivers projects, dispatching the actual work to the matching skill. An
existing screenplay can enter normalization or asset extraction directly, while
an idea or long-form source enters through story development.

There are two image-prompt paths with distinct ownership. `$short-drama-image-prompts`
writes reusable prompts for **asset references** (characters, locations, and
props). `$short-drama-storyboard` writes **shot keyframe prompts** representing
each authored shot's start state. Both share accepted asset facts while keeping
reusable design decisions separate from shot-specific presentation.

## Skills

| Skill | Responsibility |
|---|---|
| `short-drama` | Init, routing, state, recovery, acceptance/review lifecycle, delivery |
| `short-drama-develop` | Traceable novel/long-form adaptation, story engine, episode map, director brief, genre & hook playbook |
| `short-drama-write` | Episode contract, causal beats, performable screenplay, and the project's accepted production dialect |
| `short-drama-assets` | Character/Look, Location/View, Prop/State, continuity decisions |
| `short-drama-image-prompts` | Reusable character/location/prop reference prompts and scoped edit instructions |
| `short-drama-storyboard` | Source coverage, motivated shots, staging/continuity boundaries, and frozen keyframe prompts |
| `short-drama-video-prompts` | Ordered action, performance, camera/audio intent, timing, and exact start/end continuity |
| `short-drama-review` | Structural validation, evidence-based review, production quality gates, independent verdicts |

## Install

**Option 1** — just tell Claude Code / Codex or any agent that can import a
GitHub repository:

```
Install this skill suite: https://github.com/worldwonderer/drama-skills
```

**Option 2** — manual linking (the eight directories must stay siblings):

```bash
git clone https://github.com/worldwonderer/drama-skills.git && cd drama-skills

# Claude Code
mkdir -p "$HOME/.claude/skills"
for skill in skills/*; do
  ln -s "$PWD/$skill" "$HOME/.claude/skills/$(basename "$skill")"
done

# Codex
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
for skill in skills/*; do
  ln -s "$PWD/$skill" "${CODEX_HOME:-$HOME/.codex}/skills/$(basename "$skill")"
done
```

Remove any same-named skill links first — do not mix versions. Start from the
router skill; specific tasks can also invoke the matching skill directly.

### Invocation differs by runtime

| Runtime | How to invoke |
|---|---|
| Claude Code | `/short-drama`, `/short-drama-write`, … — or just describe the task in plain language |
| Codex | `$short-drama`, `$short-drama-write`, … |
| Other Agent Skill runtimes | Follow that runtime's skill-invocation convention, or describe the task in plain language |

Examples below use the `$` form; in Claude Code replace `$` with `/`, or drop the
prefix and state the task directly.

## Quick start

```
# 1. New project
Use $short-drama to init a vertical 9:16 urban face-slapping short-drama project

# 2. Write episode 1 around character choice, a local result, and exact handoff
Use $short-drama-write to write EP1: a delivery rider humiliated at a luxury
hotel turns out to be the group chairman

# 3. Assets; optionally run asset-reference prompts and storyboard keyframes in
#    parallel, then write video prompts
Use $short-drama-assets to extract characters/scenes/props from EP1
Use $short-drama-image-prompts to write reference prompts for accepted assets
Use $short-drama-storyboard to storyboard EP1
Use $short-drama-video-prompts to translate each authored shot into a video prompt

# 4. Independent review
Use $short-drama-review to review EP1's script and prompts
```

See [demo/](demo/) for a creator-facing excerpt chain: one episode's script →
asset sheets → storyboard → video prompts. It illustrates the main text
handoffs; it is not a complete project state, independent review, or delivery package.
