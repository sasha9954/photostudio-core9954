# COMFY BRAIN Canon Specification (v1.1)

## 1) Mission of COMFY BRAIN

COMFY BRAIN is the **story and scene planner** of the PHOTOSTUDIO CLIP COMFY pipeline.

It is **not** an image generator and **not** a passive settings form. It is a decision layer that:
- ingests all available creative inputs;
- determines the narrative mission source;
- enforces continuity across scenes;
- derives timeline and scene segmentation;
- emits a structured `comfyPlan` for COMFY STORYBOARD.

Core mandate:
1. transform messy multimodal input into a coherent sequence of scene intents;
2. preserve identity/world/style continuity;
3. adapt planning strategy to selected output type (`comfy image` vs `comfy text`).

---

## 2) Inputs Canon

Supported input channels:
- `TEXT`
- `AUDIO`
- `CHARACTER_1`
- `CHARACTER_2`
- `CHARACTER_3`
- `ANIMAL`
- `GROUP`
- `LOCATION`
- `STYLE`
- `PROPS`

Input semantic roles:
- **Narrative mission / genre / restrictions:** primarily `TEXT`
- **Rhythm / timing / emotional pacing:** primarily `AUDIO`
- **Cast identity:** `CHARACTER_1/2/3`, `ANIMAL`, `GROUP`
- **World anchor:** `LOCATION`
- **Style lock / look language:** `STYLE`
- **Object anchors / symbolic continuity:** `PROPS`

`CHARACTER_1` is the default lead identity unless explicitly overridden by text or mode logic.

---

## 3) Meaningful Input Definition

### 3.1 Meaningful `TEXT`

`TEXT` is meaningful when all following conditions are met:
- not empty after normalization (trim, whitespace cleanup);
- contains at least one usable creative signal: genre, premise, plot task, ad thesis, constraint, director instruction, or target story type;
- not classified as garbage/random noise (e.g., accidental characters with no semantic intent).

### 3.2 Meaningful `AUDIO`

`AUDIO` is meaningful when all following conditions are met:
- file is accessible and valid for decoding;
- decoded stream is non-empty;
- duration is above minimal analysis threshold;
- signal quality is sufficient for at least coarse segmentation.

### 3.3 Meaningful `REF`

A reference node is meaningful when all following conditions are met:
- contains at least one valid image asset;
- image is accessible (not broken link/missing blob);
- image is not an empty placeholder.

A reference class (`CHARACTER_1`, `LOCATION`, etc.) is meaningful if at least one meaningful ref exists in that class.

### 3.4 No Meaningful Input

“No meaningful input” means:
- no meaningful `TEXT`, and
- no meaningful `AUDIO`, and
- no meaningful refs across all ref classes.

This state is blocking and must produce an error.

---

## 4) Public UI Contract

### 4.1 Visible controls (public)

Public Brain UI must expose:
- `MODE`: `clip | kino | reklama | scenario`
- `OUTPUT`: `comfy image | comfy text`
- `STYLE`: `realism | film | neon | glossy | soft`
- `Freeze style` toggle (optional but recommended)
- continuity toggle(s) / summary panel (optional)
- warnings/status area (required)

### 4.2 Internal-only controls (not primary public knobs)

Must stay internal:
- duration strategy
- scene split strategy
- timeline derivation
- fallback heuristics
- anchor selection rules
- reference prioritization internals

### 4.3 Duration policy

Legacy approach where **duration is a main public manual control** is non-canonical.

Canonical rule:
- duration is an **internal derived parameter**;
- it is computed from audio presence/type/length, mode, text structure, pacing density, and scene complexity.

`OUTPUT` selection (`comfy image` / `comfy text`) is mandatory and has high UI prominence.

---

## 5) Mode Canon

### 5.1 `clip`

Planning intent: music/video-clip logic with rhythmic visual storytelling.

Brain behavior:
- scene splitting follows beat/phrase/energy transitions when audio exists;
- text is interpreted as creative direction, not literal subtitles;
- supports narrative-over-song approach;
- prioritizes dynamic transitions near peaks/drops/chorus rises;
- tolerates symbolic montage and associative edits.

Typical cadence:
- short-to-medium scenes;
- high visual turnover;
- timeline continuity may be softened for energy.

### 5.2 `kino`

Planning intent: cinematic, director-style storytelling.

Brain behavior:
- longer, logically connected scenes;
- stronger cause-effect transitions;
- high continuity pressure (identity, space, time context);
- audio supports mood pacing but does not dominate dramatic logic;
- text is interpreted as screenplay-grade guidance.

Typical cadence:
- medium-to-long scenes;
- fewer abrupt cuts;
- stronger dramatic arc and coherence.

### 5.3 `reklama`

Planning intent: persuasive short-form structure around product/message.

Brain behavior (mandatory extraction targets):
- identify **hero product / hero object / hero idea**;
- define **audience promise**;
- plan **visual proof**;
- include **emotional hook**;
- include **persuasive payoff**;
- land on a **final ad beat (CTA-like beat)**.

Additional canonical requirements:
- if explicit ad thesis is missing, brain must not silently produce “just pretty scenes”;
- must emit warning `WARN_REKLAMA_NO_THESIS`;
- must synthesize a provisional thesis when enough signal exists (text/audio/refs).

Typical cadence:
- compact scenes;
- high semantic density;
- deliberate product/idea visibility continuity.

### 5.4 `scenario`

Planning intent: storyboard-author mode for logical narrative decomposition.

Brain behavior:
- emphasizes explicit story beats, scene objectives, and continuity notes;
- outputs pre-production-ready sequencing;
- less beat-reactive than `clip`, more structural than `kino`;
- text is treated as script brief first, stylistic influence second.

Typical cadence:
- balanced scene duration;
- explicit beat boundaries;
- structural continuity prioritized.

---

## 6) Narrative Source Arbitration

This section answers: **where does COMFY BRAIN take story meaning from?**

Canonical arbitration:
1. If meaningful `TEXT` exists → `TEXT` defines narrative mission.
2. If meaningful `TEXT` is absent but meaningful `AUDIO` exists → derive narrative mission from `AUDIO`.
3. If meaningful `TEXT` + meaningful `AUDIO` both exist → `TEXT` defines story mission, `AUDIO` defines rhythm/time framing.
4. If only meaningful refs exist → synthesize narrative mission from `MODE` + refs.
5. If nothing meaningful exists → blocking error (`ERR_NO_INPUT_DATA`).

Narrative mission is always explicit in plan metadata, even when inferred.

---

## 7) Source Priority Rules

Canonical priority stack (decision order):
1. `MODE` defines thinking model.
2. `OUTPUT` defines planning strategy and packaging constraints.
3. `TEXT` defines narrative task, intent, and constraints.
4. `AUDIO` defines timing, rhythm, and pacing contour.
5. `REFS` define cast/world/style/props anchors.

Interpretation notes:
- `OUTPUT = comfy image | comfy text` affects not only final format but planning depth and representation strategy.
- `OUTPUT` is therefore a high-priority planning control, not a post-processing option.

Conflict handling:
- if text narrative conflicts with raw lyric semantics, prefer text mission and audio rhythm;
- if refs conflict with text tone, preserve identity anchors while adapting scene semantics;
- if style ref conflicts with selected style preset, prefer frozen style when freeze is enabled; otherwise blend with warning.

---

## 8) Ref Usage Rules

Role map:
- `CHARACTER_1`: lead identity anchor
- `CHARACTER_2`: secondary lead
- `CHARACTER_3`: tertiary/support cast
- `ANIMAL`: animal actor anchor
- `GROUP`: collective ensemble/crowd anchor
- `LOCATION`: world/place anchor
- `STYLE`: visual language/style lock
- `PROPS`: object continuity anchors

Reference classes:
- **Identity anchors:** characters, animal, group
- **World anchors:** location
- **Style anchors:** style
- **Object anchors:** props

Missing refs behavior:
- partial refs are valid;
- absent classes are synthesized from text/audio/mode context;
- planning must not fail solely due to missing optional refs.

Continuity behavior:
- identity anchors persist unless scene intent explicitly changes state;
- location continuity is maintained unless transition beat requires relocation;
- recurring props are propagated through relevant scenes.

---

## 9) `comfy image` Restrictions

Current limitation: `comfy image` accepts only **one image per scene**.

Mandatory per-scene rule:
- select exactly one `primaryImageAnchor`;
- convert additional refs into textual guidance.

Selection heuristic (descending):
1. lead actor (`CHARACTER_1`) when identity is central;
2. location when environment dominates;
3. key prop for object-centric beat;
4. animal when animal is narrative focus;
5. group when ensemble action defines scene.

Brain must explicitly retain non-selected refs as textual constraints, not drop them.

---

## 10) `comfy text` Rules

For `OUTPUT = comfy text`, brain prioritizes rich textual planning structure:
- detailed scene descriptions;
- cast/world/style articulation;
- continuity notes;
- camera/staging intent;
- mood and beat rationale.

`comfy text` output is a structured scene package, not a one-line prompt.

---

## 11) Scene Types Canon

COMFY BRAIN plans scenes as **functional scene types**, not only chronological chunks.

Canonical scene type set (extensible):
- `hero_intro` — introduces hero subject/product/idea.
- `world_establish` — establishes location/world logic.
- `emotion_beat` — delivers emotional charge shift.
- `conflict_beat` — introduces tension/problem/contrast.
- `transition` — bridges state/location/time changes.
- `chorus_peak` — high-energy peak (often audio-driven).
- `product_focus` — explicit product/offer demonstration.
- `payoff` — resolves setup with reward/proof.
- `finale` — closes narrative arc and leaves final imprint.

Mode influence:
- `clip` may cycle more through `transition` + `chorus_peak`;
- `kino` emphasizes `world_establish` + `conflict_beat` + `payoff`;
- `reklama` requires `hero_intro`/`product_focus`/`payoff`/`finale` coverage;
- `scenario` emphasizes explicit functional progression and traceable beat logic.

---

## 12) Continuity Classes

Canonical continuity classes:
- **identity continuity** — stable recognition of characters/animal/group across scenes.
- **world continuity** — coherent space/location/world rules.
- **style continuity** — stable visual language and render intent.
- **prop continuity** — persistent object presence/state when narratively relevant.
- **mood continuity** — emotional trajectory coherence.
- **timeline continuity** — coherent temporal progression and causal order.

Mode tuning examples:
- `clip` may relax timeline continuity for energy while keeping identity legible;
- `kino` strengthens identity/world continuity;
- `reklama` strengthens product visibility continuity and promise-proof continuity;
- `scenario` strengthens structural/timeline continuity.

---

## 13) Fallback Rules

### Case A: meaningful `TEXT + AUDIO + REFS`
- full synthesis mode;
- text sets mission, audio sets timing, refs lock anchors.

### Case B: meaningful `TEXT + AUDIO`, sparse refs
- generate world/cast defaults from text;
- keep audio-driven timeline;
- emit warning for weak identity anchors.

### Case C: only meaningful `AUDIO`
- infer mood/arc from audio dynamics;
- synthesize narrative mission from audio;
- emit warning: story objective inferred from audio.

### Case D: only meaningful `TEXT`
- build logical non-musical timeline;
- derive durations from semantic complexity;
- emit warning: no audio pacing source.

### Case E: only meaningful refs
- build visual/world continuity skeleton;
- synthesize mission from mode + refs;
- emit warning: missing explicit narrative brief.

### Case F: no meaningful input
- planning blocked;
- emit error `ERR_NO_INPUT_DATA`.

### Case G: `mode = reklama` without explicit thesis
- emit warning `WARN_REKLAMA_NO_THESIS`;
- synthesize provisional thesis if enough signal exists.

### Case H: `output = comfy image` with no meaningful anchor
- emit warning for weak visual anchor quality;
- if anchor cannot be derived from any meaningful source, emit blocking `ERR_OUTPUT_IMAGE_NO_ANCHOR`.

---

## 14) Warnings & Errors

### 14.1 Errors (blocking)
- `ERR_NO_INPUT_DATA` — no meaningful text/audio/refs.
- `ERR_OUTPUT_IMAGE_NO_ANCHOR` — `comfy image` selected but no scene anchor derivable.
- `ERR_MODE_INVALID` — unsupported mode value.
- `ERR_OUTPUT_INVALID` — unsupported output value.

### 14.2 Warnings (non-blocking)
- `WARN_NO_TEXT_BRIEF` — narrative objective auto-generated.
- `WARN_NO_AUDIO_PACING` — logical timing used instead of audio timing.
- `WARN_SPARSE_REFS` — world/cast partially synthesized.
- `WARN_IMAGE_SINGLE_ANCHOR_RULE` — one image anchor per scene limitation applied.
- `WARN_REKLAMA_NO_THESIS` — ad thesis absent, provisional thesis inferred.
- `WARN_AUDIO_ONLY_STORY_INFERRED` — narrative mission inferred from audio.
- `WARN_STYLE_CONFLICT_BLEND` — style conflict resolved by blend.

Warnings must be shown in UI summary and stored in generated plan metadata.

---

## 15) `comfyPlan` Output Contract

COMFY BRAIN emits one structured object:
- `planMeta`
- `globalContinuity`
- `scenes[]`
- `warnings[]`
- `errors[]`

### 15.1 `planMeta` (recommended fields)
- `mode`
- `output`
- `stylePreset`
- `durationDerivedSec`
- `timelineSource` (`audio`, `text`, `hybrid`, `refs-only`)
- `narrativeSource` (`text`, `audio`, `hybrid`, `mode+refs`, `none`)
- `inputCoverage`

### 15.2 `globalContinuity`
- cast registry
- world/style locks
- recurring props
- continuity constraints by class

### 15.3 `scenes[]` (recommended per-scene schema)
- `sceneId`
- `sceneType`
- `t0`, `t1`
- `sceneGoal`
- `storyBeat`
- `primarySubject`
- `secondarySubjects`
- `locationHint`
- `styleHint`
- `propsHint`
- `continuityRules`
- `cameraIntent`
- `mood`
- `reasonWhyThisSceneExists`
- `imagePromptBase`
- `textPromptBase`
- `primaryImageAnchor` (required for `comfy image`, optional otherwise)
- `textualRefGuidance[]`

### 15.4 Contract constraints
- every scene must include a narrative reason;
- scene ranges are monotonic and non-overlapping;
- for `comfy image`, max one image anchor per scene;
- warnings/errors always returned (including empty arrays when none);
- scene sequence must be traceable by scene function (`sceneType`).

---

## 16) Future Integration Notes

This canon is normative for:
- Brain UI configuration behavior;
- frontend planning orchestration;
- payload builders;
- COMFY STORYBOARD adapters;
- migration from legacy planning behavior.

Integration constraints:
1. Do not expose derived duration as primary public control.
2. Always expose `OUTPUT` selection (`comfy image` / `comfy text`) as high-priority control.
3. Keep planning deterministic enough for reproducibility with same inputs.
4. Preserve warning/error semantics end-to-end.
5. Implement mode behavior as truly distinct thinking strategies.
6. Keep narrative source arbitration explicit in metadata.
7. Treat non-script text briefs as fully valid narrative drivers.

Status:
- normative canon for COMFY BRAIN v1.1;
- implementation-agnostic by design;
- baseline for future backend/frontend/storyboard implementation phases.
