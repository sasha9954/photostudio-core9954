import React, { useEffect, useMemo, useState } from "react";
import { Handle, Position, NodeShell, buildRefImageCandidates, handleStyle, useRef } from "./comfyNodeShared";
import { formatRefProfileDetails } from "./refProfileDetails";

const REF_STATUS_LABELS = {
  empty: "пусто",
  draft: "черновик",
  loading: "анализ...",
  ready: "готово",
  error: "ошибка",
};

const ROLE_TYPE_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "hero", label: "Главный" },
  { value: "antagonist", label: "Антагонист" },
  { value: "support", label: "Поддержка" },
];

export default function RefLiteNode({ id, data, title, className, handleId, showRoleSelector = false }) {
  const inputRef = useRef(null);
  const maxFiles = 5;
  const refs = Array.isArray(data?.refs)
    ? data.refs
      .map((item) => {
        return {
          ...(item && typeof item === "object" ? item : { value: String(item || "") }),
          name: String(item?.name || "").trim(),
          type: String(item?.type || "").trim(),
        };
      })
      .filter((item) => buildRefImageCandidates(item).length > 0)
      .slice(0, maxFiles)
    : [];
  const canAddMore = refs.length < maxFiles;
  const refStatus = String(data?.refStatus || (refs.length ? "draft" : "empty"));
  const isError = refStatus === "error";
  const shortLabel = String(data?.refShortLabel || "").trim();
  const uploadSoftError = String(data?.uploadSoftError || "").trim();
  const detailsOpen = !!data?.refDetailsOpen;
  const detailsLines = formatRefProfileDetails(data?.refHiddenProfile);
  const canToggleDetails = refStatus === "ready" && detailsLines.length > 0;
  const roleType = String(data?.roleType || "auto").trim().toLowerCase() || "auto";

  const openPicker = () => { if (canAddMore) inputRef.current?.click(); };
  const onInputChange = async (e) => { const files = Array.from(e.target.files || []); if (files.length) await data?.onPickImage?.(id, files); e.target.value = ""; };

  const RefThumbImage = ({ item, idx }) => {
    const candidates = useMemo(() => buildRefImageCandidates(item), [item]);
    const [candidateIndex, setCandidateIndex] = useState(0);
    const [thumbExhausted, setThumbExhausted] = useState(candidates.length === 0);
    const activeSrc = candidates[candidateIndex] || "";
    const candidateSignature = candidates.join("|");
    useEffect(() => {
      setCandidateIndex(0);
      setThumbExhausted(candidates.length === 0);
    }, [item, candidateSignature]);

    useEffect(() => {
      console.debug("[REF THUMB FIX] candidates", { handleId, idx, candidates });
    }, [handleId, idx, candidates]);

    if (thumbExhausted || !activeSrc) {
      return <div className="clipSB_refLiteEmpty" title="Thumbnail недоступен"><span>thumbnail недоступен</span></div>;
    }

    return (
      <button className="clipSB_refLiteOpen" onClick={() => data?.onOpenLightbox?.(activeSrc)} title="Открыть фото">
        <img
          src={activeSrc}
          alt={`${title} ${idx + 1}`}
          className="clipSB_refThumbImg"
          onError={() => {
            const nextIndex = candidateIndex + 1;
            if (nextIndex < candidates.length) {
              console.debug("[REF THUMB FIX] onError fallback", { handleId, idx, failed: activeSrc, next: candidates[nextIndex] });
              setCandidateIndex(nextIndex);
              setThumbExhausted(false);
            } else {
              console.debug("[REF THUMB FIX] onError fallback", { handleId, idx, failed: activeSrc, next: null });
              console.debug("[REF THUMB FIX] exhausted fallback", { handleId, idx, failed: activeSrc });
              setThumbExhausted(true);
            }
          }}
        />
      </button>
    );
  };

  return (<>
    <Handle type="source" position={Position.Right} id={handleId} className="clipSB_handle" style={handleStyle(handleId)} />
    <NodeShell title={title} onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧷</span>} className={`${className} ${refStatus === "draft" ? "clipSB_nodeRefDraft" : ""} ${isError ? "clipSB_nodeRefError" : ""}`.trim()}>
      <div className="clipSB_small" style={{ marginBottom: 8 }}>статус: {REF_STATUS_LABELS[refStatus] || refStatus}</div>
      {uploadSoftError ? <div className="clipSB_refWarningBadge">⚠ {uploadSoftError}</div> : null}
      {isError ? <div className="clipSB_refErrorBadge">⚠ {String(data?.refAnalysisError || "Не удалось проанализировать реф")}</div> : null}
      {refStatus === "ready" && shortLabel ? <div className="clipSB_refReadyBadge">label: {shortLabel}</div> : null}
      {canToggleDetails ? (
        <button className="clipSB_refToggleDetails" onClick={() => data?.onToggleDetails?.(id)}>
          {detailsOpen ? "Скрыть описание" : "Показать описание"}
        </button>
      ) : null}
      {canToggleDetails && detailsOpen ? (
        <div className="clipSB_refDetailsBox">
          {detailsLines.map((line, idx) => <div key={`${id}-details-${idx}`} className="clipSB_refDetailsLine">{line}</div>)}
        </div>
      ) : null}
      {showRoleSelector ? (
        <div style={{ marginBottom: 10 }}>
          <div className="clipSB_small" style={{ marginBottom: 4 }}>Тип роли:</div>
          <select
            className="clipSB_select"
            value={roleType}
            onChange={(event) => data?.onField?.(id, "roleType", String(event?.target?.value || "auto").trim().toLowerCase() || "auto")}
            disabled={refStatus === "loading"}
          >
            {ROLE_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
      ) : null}
      <div className="clipSB_refLitePreview">{!refs.length ? <div className="clipSB_refLiteEmpty" onClick={openPicker} role="button" tabIndex={0}><span className="clipSB_refLiteEmptyPlus">+</span><span>нет изображений</span><span>добавь фото</span></div> : <div className="clipSB_refGrid clipSB_refLiteGrid">{refs.map((item, idx) => {
        return (
          <div className="clipSB_refThumb" key={`${item.url || item.value || item.name || "ref"}-${idx}`}>
            <RefThumbImage item={item} idx={idx} />
            <button className="clipSB_refThumbRemove" title="Удалить фото" onClick={() => data?.onRemoveImage?.(id, idx)}>×</button>
          </div>
        );
      })}{canAddMore ? <button className="clipSB_refAddTile" onClick={openPicker} title="Добавить изображение">+</button> : null}</div>}</div>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="clipSB_btn" onClick={openPicker} disabled={!canAddMore || !!data?.uploading || refStatus === "loading"}>{data?.uploading ? "Загрузка…" : refs.length ? "Добавить фото" : "Загрузить фото"}</button>
      </div>
      <input ref={inputRef} type="file" accept="image/png,image/jpeg,image/jpg,image/webp" multiple style={{ display: "none" }} onChange={onInputChange} />
    </NodeShell>
  </>);
}
