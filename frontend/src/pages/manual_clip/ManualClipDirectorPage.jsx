import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  readManualClipBoardProjectForNode,
  writeManualClipBoardOpenState,
} from "../clip_nodes/manualProjectBackup.js";

export default function ManualClipDirectorPage() {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const params = new URLSearchParams(location.search || "");
    const sourceNodeId = String(params.get("sourceNodeId") || location.state?.sourceNodeId || "").trim();
    const project = location.state?.director_board || location.state?.project || readManualClipBoardProjectForNode(sourceNodeId) || null;
    writeManualClipBoardOpenState({
      isOpen: true,
      sourceNodeId,
      selectedSceneId: String(project?.selectedSceneId || project?.scenes?.[0]?.scene_id || "").trim(),
      project_id: String(project?.project_id || project?.projectId || "").trim(),
      input_signature: String(project?.input_signature || project?.inputSignature || "").trim(),
      routePath: "/studio/storyboard",
      updatedAt: Date.now(),
    });
    console.info('[MANUAL BOARD REDIRECT LEGACY ROUTE]', { sourceNodeId, route: "/studio/storyboard" });
    navigate("/studio/storyboard", {
      replace: true,
      state: {
        openManualDirectorBoard: true,
        sourceNodeId,
        director_board: project,
        project,
      },
    });
  }, [location.search, location.state, navigate]);

  return null;
}
