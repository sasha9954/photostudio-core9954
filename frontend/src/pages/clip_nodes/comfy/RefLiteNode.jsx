import React from "react";
import { Handle, Position, NodeShell, handleStyle, resolveAssetUrl, useRef } from "./comfyNodeShared";

export default function RefLiteNode({ id, data, title, className, handleId }) {
  const inputRef = useRef(null);
  const maxFiles = 5;
  const refs = Array.isArray(data?.refs) ? data.refs.map((item) => ({ url: String(item?.url || "").trim(), name: String(item?.name || "").trim(), type: String(item?.type || "").trim() })).filter((item) => !!item.url).slice(0, maxFiles) : [];
  const canAddMore = refs.length < maxFiles;

  const openPicker = () => { if (canAddMore) inputRef.current?.click(); };
  const onInputChange = async (e) => { const files = Array.from(e.target.files || []); if (files.length) await data?.onPickImage?.(id, files); e.target.value = ""; };

  return (<>
    <Handle type="source" position={Position.Right} id={handleId} className="clipSB_handle" style={handleStyle(handleId)} />
    <NodeShell title={title} onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧷</span>} className={className}>
      <div className="clipSB_refLitePreview">{!refs.length ? <div className="clipSB_refLiteEmpty" onClick={openPicker} role="button" tabIndex={0}><span className="clipSB_refLiteEmptyPlus">+</span><span>нет изображений</span><span>добавь фото</span></div> : <div className="clipSB_refGrid clipSB_refLiteGrid">{refs.map((item, idx) => <div className="clipSB_refThumb" key={`${item.url}-${idx}`}><button className="clipSB_refLiteOpen" onClick={() => data?.onOpenLightbox?.(item.url)} title="Открыть фото"><img src={resolveAssetUrl(item.url)} alt={`${title} ${idx + 1}`} className="clipSB_refThumbImg" /></button><button className="clipSB_refThumbRemove" title="Удалить фото" onClick={() => data?.onRemoveImage?.(id, idx)}>×</button></div>)}{canAddMore ? <button className="clipSB_refAddTile" onClick={openPicker} title="Добавить изображение">+</button> : null}</div>}</div>
      <button className="clipSB_btn" onClick={openPicker} disabled={!canAddMore || !!data?.uploading}>{data?.uploading ? "Загрузка…" : refs.length ? "Добавить фото" : "Загрузить фото"}</button>
      <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/jpg,image/webp" multiple style={{ display: "none" }} onChange={onInputChange} />
    </NodeShell>
  </>);
}
