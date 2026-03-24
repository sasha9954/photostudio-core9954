import React from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";

const RENDER_MODE_OPTIONS = [
  { value: "image_to_video", label: "image_to_video" },
  { value: "lip_sync", label: "lip_sync" },
  { value: "first_last", label: "first_last" },
];

export default function ScenarioStoryboardNode({ id, data }) {
  const scenes = Array.isArray(data?.scenes) ? data.scenes : [];
  const generationMap = data?.sceneGeneration && typeof data.sceneGeneration === "object" ? data.sceneGeneration : {};

  return (
    <>
      <Handle type="target" position={Position.Left} id="scenario_storyboard_in" className="clipSB_handle" style={handleStyle("scenario_storyboard_in")} />
      <Handle type="source" position={Position.Right} id="scenario_storyboard_out" className="clipSB_handle" style={handleStyle("scenario_storyboard_out")} />
      <NodeShell title="SCENARIO STORYBOARD" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎞️</span>} className="clipSB_nodeStoryboard">
        <div className="clipSB_small">Прямой editor от Scenario Director (без ComfyBrain).</div>
        {scenes.length === 0 ? <div className="clipSB_small" style={{ marginTop: 8 }}>Пусто. Подключите storyboard_out в scenario_storyboard_in.</div> : null}
        <div className="clipSB_storyboardSceneList">
          {scenes.map((scene, idx) => {
            const sceneKey = String(scene?.sceneId || `S${idx + 1}`);
            const runtime = generationMap[sceneKey] && typeof generationMap[sceneKey] === "object" ? generationMap[sceneKey] : {};
            return (
              <article key={sceneKey} className="clipSB_storyboardSceneCard">
                <div className="clipSB_storyboardSceneHeader">
                  <div>
                    <div className="clipSB_storyboardSceneId">{sceneKey}</div>
                    <div className="clipSB_storyboardSceneTime">{scene.t0}s → {scene.t1}s • {scene.durationSec}s</div>
                  </div>
                  <span className={`clipSB_storyboardSceneStatus clipSB_storyboardSceneStatus--${runtime.status || "not_generated"}`}>
                    {runtime.status || "not_generated"}
                  </span>
                </div>

                <div className="clipSB_storyboardSceneGrid">
                  <div className="clipSB_storyboardKv"><span>summary (RU)</span><strong>{scene.summaryRu || "—"}</strong></div>
                  <div className="clipSB_storyboardKv"><span>location (RU)</span><strong>{scene.locationRu || "—"}</strong></div>
                  <div className="clipSB_storyboardKv"><span>camera (RU)</span><strong>{scene.cameraRu || "—"}</strong></div>
                  <div className="clipSB_storyboardKv"><span>emotion (RU)</span><strong>{scene.emotionRu || "—"}</strong></div>
                  <div className="clipSB_storyboardKv"><span>actors</span><strong>{Array.isArray(scene.actors) && scene.actors.length ? scene.actors.join(", ") : "—"}</strong></div>
                  <div className="clipSB_storyboardKv"><span>render_mode</span><strong>{scene.renderMode || "image_to_video"}</strong></div>
                </div>

                <div className="clipSB_storyboardExecutor">
                  <div className="clipSB_storyboardExecutorRow">
                    <label className="clipSB_narrativeField">
                      <div className="clipSB_brainLabel">Render mode</div>
                      <select
                        className="clipSB_select clipSB_storyboardSelect"
                        value={scene.renderMode || "image_to_video"}
                        onChange={(event) => data?.onScenarioSceneUpdate?.(id, sceneKey, { renderMode: event.target.value })}
                      >
                        {RENDER_MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                      </select>
                    </label>
                    <button className="clipSB_btn" onClick={() => data?.onScenarioSceneGenerate?.(id, sceneKey)} type="button">Generate scene (EN)</button>
                  </div>

                  <div className="clipSB_storyboardPromptGrid">
                    <div className="clipSB_storyboardPromptCard">
                      <div className="clipSB_storyboardBlockTitle">Image prompt (RU display)</div>
                      <div className="clipSB_storyboardPromptBox">{scene.imagePromptRu || "—"}</div>
                    </div>
                    <div className="clipSB_storyboardPromptCard">
                      <div className="clipSB_storyboardBlockTitle">Image prompt (EN engine)</div>
                      <div className="clipSB_storyboardPromptBox">{scene.imagePromptEn || "—"}</div>
                    </div>
                  </div>
                  <div className="clipSB_storyboardPromptGrid">
                    <div className="clipSB_storyboardPromptCard">
                      <div className="clipSB_storyboardBlockTitle">Video prompt (RU display)</div>
                      <div className="clipSB_storyboardPromptBox">{scene.videoPromptRu || "—"}</div>
                    </div>
                    <div className="clipSB_storyboardPromptCard">
                      <div className="clipSB_storyboardBlockTitle">Video prompt (EN engine)</div>
                      <div className="clipSB_storyboardPromptBox">{scene.videoPromptEn || "—"}</div>
                    </div>
                  </div>

                  {scene.renderMode === "lip_sync" ? (
                    <div className="clipSB_small">
                      lip_sync: {scene.audioSliceStartSec}s → {scene.audioSliceEndSec}s ({scene.audioSliceExpectedDurationSec}s), phrase: {scene.localPhrase || "—"}
                    </div>
                  ) : null}
                  {scene.renderMode === "first_last" ? (
                    <div className="clipSB_small">
                      first_last: start(RU/EN)= {scene.startFramePromptRu || "—"} / {scene.startFramePromptEn || "—"} • end(RU/EN)= {scene.endFramePromptRu || "—"} / {scene.endFramePromptEn || "—"}
                    </div>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      </NodeShell>
    </>
  );
}
