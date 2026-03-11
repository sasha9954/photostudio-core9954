# COMFY BRAIN Canon Specification (v1)

## 1) Mission of COMFY BRAIN

COMFY BRAIN is the **story and scene planner** of the PHOTOSTUDIO CLIP COMFY pipeline.

It is **not** an image generator and **not** a passive form of settings. It is a decision layer that:
- ingests all available creative inputs;
- determines the primary source of narrative truth for the current run;
- enforces continuity across scenes;
- derives timeline and scene segmentation;
- emits a structured `comfyPlan` for COMFY STORYBOARD.

Core mandate:
1. transform messy multimodal input into a coherent sequence of scene intents;
2. preserve identity/world/style consistency;
3. map planning decisions to output format constraints (`comfy image` vs `comfy text`).

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
- **Narrative task / genre / restrictions:** primarily `TEXT`
- **Rhythm / timing / emotional pacing:** primarily `AUDIO`
- **Cast identity:** `CHARACTER_1/2/3`, `ANIMAL`, `GROUP`
- **World anchor:** `LOCATION`
- **Style lock / look language:** `STYLE`
- **Object anchors / symbolic continuity:** `PROPS`

`CHARACTER_1` is the default lead identity unless explicitly overridden by text or mode logic.

---

## 3) Public UI Contract for COMFY BRAIN

### 3.1 Visible controls (public)
Public Brain UI must expose:
- `MODE`: `clip | kino | reklama | scenario`
- `OUTPUT`: `comfy image | comfy text`
- `STYLE`: `realism | film | neon | glossy | soft`
- `Freeze style` toggle (optional but recommended)
- continuity toggle(s) / summary panel (optional)
- warnings/status area (required)

### 3.2 Internal-only controls (not primary public knobs)
Must stay internal:
- duration strategy
- scene split strategy
- timeline derivation
- fallback heuristics
- anchor selection rules
- reference prioritization internals

### 3.3 Duration policy correction (mandatory canon rule)
Legacy approach where **duration is a main public manual control** is considered non-canonical.

Canonical rule:
- duration is an **internal derived parameter**;
- it is automatically computed from: audio presence/type/length, mode, text structure, pacing density, and scene complexity.

`OUTPUT` selection (`comfy image` / `comfy text`) is mandatory and has higher UI importance than manual duration.

---

## 4) Mode Canon (real behavior, not labels)

### 4.1 `clip`
Planning intent: music/video-clip logic with rhythmic visual storytelling.

Brain behavior:
- scene splitting follows beat/phrase/energy transitions when audio exists;
- text is interpreted as creative direction (genre, premise, imagery), not literal subtitles;
- supports narrative-over-song approach (non-literal visual story over lyrics);
- prioritizes dynamic transitions around peaks/drops/chorus-like rises;
- tolerates symbolic montage and associative edits.

Typical cadence:
- short-to-medium scenes;
- higher visual turnover;
- continuity softened in favor of energy and emotional momentum.

### 4.2 `kino`
Planning intent: cinematic, director-style storytelling.

Brain behavior:
- longer, logically connected scenes;
- stronger cause-effect scene transitions;
- continuity pressure is high (character state, space, time-of-day, wardrobe cues);
- audio influences mood pacing but does not dominate dramatic logic;
- text instructions are interpreted as screenplay-level guidance.

Typical cadence:
- medium-to-long scenes;
- fewer abrupt cuts;
- stronger dramatic arc and visual coherence.

### 4.3 `reklama`
Planning intent: persuasive short-form structure around product/message.

Brain behavior:
- prioritizes selling point, hook, and persuasive visual proof;
- enforces dense structure with minimal wasted beats;
- scenes are organized around claim → support → payoff / CTA logic;
- if no explicit ad thesis exists, emits warning and synthesizes a provisional thesis from text/audio/refs.

Typical cadence:
- compact scenes;
- high semantic density;
- deliberate focus on product/idea visibility.

### 4.4 `scenario`
Planning intent: storyboard-author mode for logical narrative decomposition.

Brain behavior:
- emphasizes explicit story beats, scene objectives, and continuity notes;
- outputs clearer pre-production-ready sequencing;
- less beat-reactive than `clip`, more structural than `kino`;
- text is treated as script brief first, stylistic influence second.

Typical cadence:
- balanced scene duration;
- explicit beat boundaries;
- planning clarity prioritized over raw visual dynamism.

---

## 5) TEXT Handling Canon

`TEXT` is treated as a **creative brief / directing intent**, not only “onscreen text”.

TEXT can define:
- genre and tone;
- story objective;
- restrictions and must-have elements;
- target emotional trajectory.

Mode-specific text interpretation:
- `clip`: text sets narrative superstructure over rhythmic audio scaffolding;
- `kino`: text sets dramaturgy, character logic, and cinematic intent;
- `reklama`: text sets offer/thesis/audience persuasion angle;
- `scenario`: text sets beat-by-beat structural and storyboard requirements.

If `TEXT` is empty:
- Brain derives intent from `AUDIO` and refs;
- generates auto-brief assumptions;
- raises warning: “No text brief; narrative objective auto-generated.”

---

## 6) AUDIO Handling Canon

Supported audio categories:
- song
- instrumental
- ad voiceover
- narration / spoken story
- spoken mixed audio
- ambient background / atmosphere

Audio analysis responsibilities:
1. infer likely audio type;
2. detect temporal segments (energy, phrasing, emotional shifts, silence breaks, spoken blocks);
3. derive timing guidance for scene boundaries.

Critical non-literal rule:
- Brain must **not** blindly retell song lyrics.
- It should build an interesting cinematic narrative layer aligned with rhythm/emotion.

Mode-specific audio usage:
- `clip`: primary driver for segmentation and energetic pacing;
- `kino`: secondary driver; supports mood and tempo around story logic;
- `reklama`: supports memorability and emphasis points for claims;
- `scenario`: used as timing context when relevant, not as dominant structural authority.

Combined `TEXT + AUDIO` principle:
- audio provides rhythm/frame;
- text provides genre/story mission;
- brain synthesizes non-literal, cinematic scene plan.

---

## 7) Source Arbitration & Priority Rules

Canonical priority stack (decision order):
1. `MODE` defines thinking strategy.
2. `TEXT` defines narrative mission, constraints, genre intent.
3. `AUDIO` defines temporal rhythm and emotional contour.
4. `REFS` define cast/world/style/props anchors.
5. `OUTPUT` defines packing/formatting strategy of final plan.

Conflict handling:
- if text narrative conflicts with raw lyric semantics, prefer text mission and audio rhythm;
- if refs conflict with text tone, preserve identity anchors while adapting scene semantics;
- if style ref conflicts with selected UI style preset, prefer frozen style if freeze enabled; otherwise blend with explicit warning.

---

## 8) Reference Usage Rules (REFS)

Role map:
- `CHARACTER_1`: lead identity anchor
- `CHARACTER_2`: secondary lead
- `CHARACTER_3`: tertiary/support cast
- `ANIMAL`: animal actor anchor
- `GROUP`: collective ensemble/t crowd anchor
- `LOCATION`: world/place anchor
- `STYLE`: visual language/style lock
- `PROPS`: object anchor continuity

Reference classes:
- **Identity anchors:** characters, animal, group
- **World anchors:** location
- **Style anchors:** style
- **Object anchors:** props

Missing refs behavior:
- partial refs are valid;
- absent classes are synthesized from text/audio mode context;
- never block planning solely due to missing optional refs.

Continuity behavior:
- identity anchors persist unless scene intent explicitly changes state;
- location continuity is maintained unless transition beat demands relocation;
- props tagged as recurring are propagated across relevant scenes.

---

## 9) `comfy image` Restrictions Canon

Current limitation: COMFY IMAGE accepts only **one image per scene**.

Mandatory planning rule per scene:
- select exactly one `primaryImageAnchor`;
- all additional refs are converted to textual constraints/guidance.

Selection heuristic for primary anchor (descending):
1. scene lead actor (usually `CHARACTER_1`) when identity is central;
2. location when environment is dominant;
3. key prop when object-centric beat;
4. animal when animal is narrative focus;
5. group when ensemble action defines the scene.

Brain must explicitly note that non-selected refs are retained as textual guidance, not discarded.

---

## 10) `comfy text` Rules Canon

For `OUTPUT = comfy text`, Brain shifts emphasis to rich textual structure:
- detailed scene descriptions;
- cast/world/style articulation;
- continuity notes;
- camera and staging intent;
- mood and beat rationale.

`comfy text` output should be a structured scene package, not a short single-line prompt.

---

## 11) Fallback Rules (Incomplete Data Scenarios)

### Case A: `TEXT + AUDIO + REFS`
- full synthesis mode;
- text sets mission, audio sets timing, refs lock identity/world/style.

### Case B: `TEXT + AUDIO`, few refs
- generate world/cast defaults from text;
- audio-driven timeline retained;
- emit warning on weak identity anchoring.

### Case C: only `AUDIO`
- infer mood/arc from audio dynamics;
- auto-generate narrative hypothesis;
- emit warning: story objective inferred.

### Case D: only `TEXT`
- build logical (non-musical) timeline;
- scene durations derived from semantic complexity;
- emit warning: no audio pacing source.

### Case E: only `REFS`
- create visual/world continuity skeleton;
- generate neutral objective unless mode implies stronger defaults;
- emit warning: missing explicit narrative brief.

### Case F: no meaningful input
- planning blocked;
- emit error: insufficient source data.

### Case G: `mode = reklama` without ad thesis
- emit warning (or validation error if strict mode enabled): missing selling thesis;
- synthesize provisional thesis from text/audio/refs if possible.

### Case H: `output = comfy image` with no meaningful ref and no text
- emit warning: weak visual anchor quality;
- allow fallback to auto-generated anchor from inferred world only if audio/text carries enough signal, else block.

---

## 12) Warnings & Errors Canon (Mayaki)

### 12.1 Errors (blocking)
- `ERR_NO_INPUT_DATA`: no text, no audio, no refs.
- `ERR_OUTPUT_IMAGE_NO_ANCHOR`: output is `comfy image` but no anchor can be derived.
- `ERR_MODE_INVALID`: unsupported mode value.
- `ERR_OUTPUT_INVALID`: unsupported output value.

### 12.2 Warnings (non-blocking)
- `WARN_NO_TEXT_BRIEF`: narrative objective auto-generated.
- `WARN_NO_AUDIO_PACING`: logical timing used instead of musical timing.
- `WARN_SPARSE_REFS`: world/cast partially auto-synthesized.
- `WARN_IMAGE_SINGLE_ANCHOR_RULE`: only one image anchor will be used per scene.
- `WARN_REKLAMA_NO_THESIS`: ad thesis missing, provisional thesis inferred.
- `WARN_AUDIO_ONLY_STORY_INFERRED`: story created from audio mood/structure.
- `WARN_STYLE_CONFLICT_BLEND`: style sources conflict; blended strategy used.

Warnings must be surfaced in UI summary and embedded in metadata of the generated plan.

---

## 13) `comfyPlan` Output Contract (to COMFY STORYBOARD)

COMFY BRAIN outputs one structured object:
- `planMeta`
- `globalContinuity`
- `scenes[]`
- `warnings[]`
- `errors[]`

### 13.1 `planMeta` (recommended fields)
- `mode`
- `output`
- `stylePreset`
- `durationDerivedSec`
- `timelineSource` (`audio`, `text`, `hybrid`, `refs-only`)
- `inputCoverage` (what channels were present)

### 13.2 `globalContinuity`
- cast registry
- world/style locks
- recurring props
- continuity constraints

### 13.3 `scenes[]` (recommended per-scene schema)
- `sceneId`
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

### 13.4 Contract constraints
- every scene must include a narrative reason (`reasonWhyThisSceneExists`);
- time ranges must be monotonic and non-overlapping;
- for `comfy image`, each scene has max one image anchor;
- warnings/errors always returned, even if empty arrays.

---

## 14) Implementation Notes for Future Integration (non-code)

This canon is the source document for:
- Brain UI configuration model;
- frontend local planning logic;
- payload builder;
- COMFY STORYBOARD adapter;
- migration mapping from legacy engine behavior.

Integration constraints:
1. Do not expose derived duration as primary public control.
2. Always expose `OUTPUT` selection (`comfy image` / `comfy text`).
3. Keep planning deterministic enough for reproducibility with same inputs.
4. Preserve warning/error semantics end-to-end (Brain UI → payload → storyboard).
5. Implement mode behavior as genuinely distinct planning strategies, not preset labels.

Status of this document:
- normative canon for COMFY BRAIN behavior;
- implementation-agnostic by design;
- must be treated as baseline for subsequent engine/frontend/backend tasks.
