# Scenario-stage payload bloat investigation (code-based)

## Scope and method

This report is based on direct code tracing across frontend request builders, backend stage orchestration, and Gemini call builders.
No assumptions were used: each statement maps to specific code paths.

---

## 1) FILE MAP (payload assembly points)

### Frontend (request + UI state)
- `frontend/src/pages/clip_nodes/comfy/comfyNarrativeDomain.js`
  - `buildScenarioDirectorRequestPayload` (base scenario payload)
  - `buildScenarioStageManualPayload` (manual stage payload for `scenario_stage_v1`)
  - `normalizeScenarioDirectorApiResponse` (response shaping, `storyboardPackage` + `directorOutput` propagation)
- `frontend/src/pages/ClipStoryboardPage.jsx`
  - `onScenarioPipelineRunStage` flow calls backend route with stage payload.
  - Stores `storyboardPackage`, `debugStoryboardPackage`, `pendingOutputs`, `directorOutput`, diagnostics/state mirrors.
  - Helpers `buildScenarioResponseSummary`, `stripRawScenarioPayload`, `clearScenarioPipelineDownstreamRuntime`.
- `frontend/src/pages/clip_nodes/comfy/ScenarioPipelineDebugEditor.jsx`
  - Renders full diagnostics and full raw package JSON in debug tabs.

### Backend route (frontend->backend + response shaping)
- `backend/app/api/routes/clip_comfy.py`
  - `/clip/comfy/scenario-director/generate`:
    - accepts whole frontend object (including `storyboardPackage`)
    - in `scenario_stage_v1` runs stage pipeline and returns **full package + duplicates** (`storyboardPackage`, `storyboardOut`, `directorOutput`, `diagnostics`).

### Backend stage pipeline (package lifecycle + stage outputs)
- `backend/app/engine/scenario_stage_pipeline.py`
  - `create_storyboard_package`
  - `_sync_stage_package_input`
  - `mark_stale_downstream`, `invalidate_downstream_stages`
  - `_append_diag_event` (event log accumulation with cap)
  - `_run_story_core_stage`, `_run_audio_map_stage`, `_run_role_plan_stage`, `_run_scene_plan_stage`, `_run_scene_prompts_stage`, `_run_finalize_stage`

### Backend Gemini stage-specific builders
- `backend/app/engine/scenario_stage_pipeline.py` (story_core Gemini call)
- `backend/app/engine/scenario_role_planner.py` (role_plan Gemini context)
- `backend/app/engine/scenario_scene_planner.py` (scene_plan Gemini context)
- `backend/app/engine/scenario_scene_prompter.py` (scene_prompts Gemini context)
- `backend/app/engine/audio_scene_segmenter.py` (audio_map Gemini transport: inline bytes or URL)

---

## 2) PAYLOAD FLOW (actual path)

1. Frontend builds stage payload in `buildScenarioStageManualPayload`.
2. Frontend sends payload to `/api/clip/comfy/scenario-director/generate`.
3. Backend route (scenario_stage_v1 branch):
   - uses incoming `storyboardPackage` (or creates new one),
   - syncs input snapshot,
   - runs stage(s),
   - returns full package + mirrored outputs.
4. Stage code builds **separate compact Gemini contexts** for each stage.
5. Gemini responses are normalized into package sections (`story_core`, `role_plan`, `scene_plan`, `scene_prompts`).
6. Route returns large response; frontend stores mirrored structures in debug/source node state.

---

## 3) 4-layer decomposition by stage

## A. UI STATE BLOAT

### Main contributors
- Frontend stores package in multiple places at once:
  - `storyboardPackage`
  - `debugStoryboardPackage`
  - `pendingOutputs.debugStoryboardPackage`
  - plus `directorOutput`, `storyboardOut`, `diagnostics` mirrors.
- Debug UI renders raw package and diagnostics JSON.
- `normalizeScenarioDirectorApiResponse` rehydrates a large `directorOutput` object with repeated fields copied from package/final storyboard.

### Effect
Large object is multiplied in React node state and appears visually “fatter” than what Gemini sees.

## B. FRONTEND -> BACKEND REQUEST BLOAT

### What is actually sent
For stage run frontend includes:
- `storyboardPackage` (often full package)
- `context_refs`, `connected_context_summary`
- `directorOutput`, `storyboardOut`, `scenarioPackage`, `master_output`, `options`, metadata extras
- selection/helper fields

For `story_core`, frontend has special lean package builder, but for other stages it sends full package.

### Important
Most of these fields are **not** forwarded to Gemini directly, but still inflate frontend->backend request body.

## C. BACKEND -> GEMINI CONTEXT BLOAT

### story_core (Gemini)
Sent parts are:
- prompt text from `_build_story_core_prompt`
- compact context (`CORE_INPUT_CONTEXT`) and compact assigned roles (string-truncated in prompt)
- optional inline reference images (character/props/location/style), max 1 per role

Not sent: `diagnostics.events`, route-level `directorOutput`, full response package.

### role_plan (Gemini)
`ROLE_PLANNING_CONTEXT` includes compact context:
- compact story_core, scene windows, roles inventory, audio dramaturgy, sections

Not sent: package diagnostics/event history/directorOutput.

### scene_plan (Gemini)
`SCENE_PLANNING_CONTEXT` includes:
- `story_core` (including `scenes` list)
- `audio_map.sections`, derived scene windows, cut policy, audio dramaturgy
- `role_plan.global_roles`, `role_plan.world_continuity`, `role_plan.scene_roles`, continuity notes
- capability canon block

Potentially heavy for Gemini: full `story_core.scenes`, `role_plan.scene_roles`, `audio_map.sections`.

### scene_prompts (Gemini)
`SCENE_PROMPTS_CONTEXT` includes:
- compact story lock summaries,
- `audio_map.scene_windows` + sections + cut policy + audio dramaturgy,
- role_plan continuity subset,
- scene_plan scenes with many per-scene fields,
- capability canon.

Potentially heavy for Gemini: `scene_plan.scenes` and supporting context.

### audio_map (Gemini)
Audio transport:
- tries inline base64 bytes if local file and under threshold,
- else uses public file URI,
- records transport meta (inline size, source mode).

So Gemini-heavy traffic here is mainly **audio payload** (inline bytes), not diagnostics/directorOutput.

## D. BACKEND RESPONSE BLOAT

### Response duplication pattern
Route returns all at once:
- `storyboardPackage` (full package)
- `storyboardOut` (final storyboard mirror)
- `directorOutput` (stage_statuses + story_core + diagnostics mirror)
- `diagnostics` (same diagnostics again)

Frontend then mirrors again inside node state.

---

## 4) Biggest concrete contributors (field-level)

1. `storyboardPackage` full object in request/response and UI mirrors.
2. `diagnostics.events` (history list; capped to last 80, but still large if messages are long and persisted across runs).
3. `scene_plan.scenes` + `scene_prompts.scenes` + `audio_map.phrase_units/scene_candidate_windows` inside package.
4. Duplicated `connected_context_summary` / `context_refs` across:
   - package input,
   - final storyboard,
   - directorOutput,
   - UI runtime storyboard views.
5. Duplicated response-level mirrors:
   - top-level `diagnostics` and `directorOutput.diagnostics` and `storyboardPackage.diagnostics`.
6. Debug-heavy optional payloads:
   - `scene_plan_debug.original_scenes` (enabled by scene_plan debug flag).

---

## 5) What actually hits Gemini (explicit answer)

- Whole `directorOutput` does **not** go to Gemini.
- `diagnostics` / `diagnostics.events` do **not** go to Gemini stage prompts.
- Frontend raw response/debug mirrors do **not** go to Gemini.

Gemini-heavy stages are:
- `audio_map` (if inline audio bytes are used),
- `scene_plan` (large planning context),
- `scene_prompts` (scene-level prompt context).

Screen-heavy but Gemini-light contributors are:
- route response mirrors (`storyboardPackage`, `directorOutput`, duplicate diagnostics),
- frontend debug state duplication.

---

## 6) Accumulation/history between runs

### Confirmed by code
- `storyboardPackage` is sent back from frontend on each manual run.
- Backend keeps incoming package and mutates it stage-by-stage.
- `_append_diag_event` appends events and keeps last 80 entries, so history is retained across runs for same package lineage.

### Not infinite, but persistent
- Event history is bounded (80), not unbounded.
- However, because package is round-tripped FE<->BE and reused, old events and prior stage products remain until cleared/invalidated.

### Existing cleanup controls already present
- `invalidate_downstream_stages` / frontend `clearScenarioPipelineDownstreamRuntime` delete selected downstream package keys.
- This is existing invalidation controller behavior; no new controller is required for initial cleanup wins.

---

## 7) Existing controllers/normalizers already in place (do not over-amplify)

- Frontend:
  - pre-run invalidation (`applyScenarioPipelinePreRunInvalidation`)
  - downstream cleanup (`clearScenarioPipelineDownstreamRuntime`)
  - raw stripping helper (`stripRawScenarioPayload`)
- Backend:
  - stale marking (`mark_stale_downstream`)
  - stage-specific reset (`invalidate_downstream_stages` + `STAGE_SECTION_RESETTERS`)
  - diagnostics per-stage prefix cleanup (`_clear_stage_diagnostics`)
  - bounded event log in `_append_diag_event`

These mechanisms already control lifecycle. Bloat mostly comes from what is mirrored/returned, not from missing controllers.

---

## 8) Minimal safe fix plan (no big refactor)

Ordered by impact:

1. **Stop returning duplicate mirrors in stage response by default**
   - Keep `storyboardPackage` + minimal `meta` only.
   - Gate `directorOutput`/top-level `diagnostics` behind `debug=true` flag.

2. **Send lean package for all manual stages, not only story_core**
   - For non-required sections send only stage dependencies + stage_statuses minimal + compact diagnostics.

3. **Prune diagnostics echo in responses**
   - Keep counters/status, drop heavy lists (`events`, large nested summaries) unless debug mode.

4. **Normalize single source of truth for refs summary in runtime contract**
   - Keep only one canonical `context_refs` + one canonical `connected_context_summary` in runtime output.

5. **Trim frontend state duplication for debug package**
   - Store package once (`debugStoryboardPackage`) and derive UI mirrors lazily.

6. **Add lightweight size telemetry (already prepared by script below)**
   - Measure request/response section sizes and print top contributors before/after each run in debug/dev mode.

---

## 9) Lightweight instrumentation artifact

Added script:
- `tools/scenario_payload_size_report.py`

Purpose:
- takes a JSON payload/response dump,
- prints total byte size and heavy sections:
  - `diagnostics.events`
  - `directorOutput`
  - `storyboardPackage`
  - `context_refs`
  - `connected_context_summary`
  - scene plan/prompts sections,
- prints top-level key contribution ranking.

This is non-invasive and safe: no runtime pipeline behavior changes.
