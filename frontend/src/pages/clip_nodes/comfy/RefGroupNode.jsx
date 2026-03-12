import React from "react";
import RefLiteNode from "./RefLiteNode";

export default function RefGroupNode({ id, data }) {
  return <RefLiteNode id={id} data={data} title="GROUP / COLLECTIVE" className="clipSB_nodeRefGroup" handleId="ref_group" />;
}
