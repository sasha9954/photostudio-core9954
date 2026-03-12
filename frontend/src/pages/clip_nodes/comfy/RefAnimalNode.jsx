import React from "react";
import RefLiteNode from "./RefLiteNode";

export default function RefAnimalNode({ id, data }) {
  return <RefLiteNode id={id} data={data} title="ANIMAL" className="clipSB_nodeRefAnimal" handleId="ref_animal" />;
}
