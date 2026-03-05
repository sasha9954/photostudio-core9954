import React from "react";
import { Handle, Position } from "@xyflow/react";

export default function AssemblyNode() {
  return (
    <div className="clipNode clipNodeAssembly">
      <div className="clipNodeHeader">
        <div className="clipNodeIcon">🎬</div>
        <div className="clipNodeTitle">ASSEMBLY</div>
      </div>

      <div className="clipNodeHint">
        скоро: склейка сцен, музыка, lip-sync, экспорт mp4
      </div>

      <button className="clipBtn" disabled>
        Собрать (скоро)
      </button>

      <Handle type="target" position={Position.Left} />
    </div>
  );
}
