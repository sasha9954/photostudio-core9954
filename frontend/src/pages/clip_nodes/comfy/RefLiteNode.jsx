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

const STORY_ROLE_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "main", label: "Главный персонаж" },
  { value: "secondary", label: "Второй персонаж" },
  { value: "support", label: "Поддержка" },
  { value: "antagonist", label: "Антагонист" },
  { value: "minor", label: "Эпизодический" },
  { value: "group", label: "Группа / массовка" },
];
const IDENTITY_LABEL_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "девушка", label: "Девушка" },
  { value: "парень", label: "Парень" },
  { value: "женщина", label: "Женщина" },
  { value: "мужчина", label: "Мужчина" },
  { value: "ребёнок", label: "Ребёнок" },
  { value: "животное", label: "Животное" },
  { value: "группа людей", label: "Группа людей" },
  { value: "другое", label: "Другое" },
];
const GENDER_HINT_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "female", label: "Женский" },
  { value: "male", label: "Мужской" },
  { value: "not_applicable", label: "Не применимо / неизвестно" },
];
const APPEARANCE_MODE_OPTIONS = [
  { value: "auto", label: "Авто", tooltip: "Система решает по роли и сцене." },
  { value: "story_visible", label: "Везде по смыслу", tooltip: "Персонаж может появляться и в lip-sync, и в обычных i2v сценах." },
  { value: "lip_sync_only", label: "Только lip-sync", tooltip: "Персонаж появляется только в вокальных/lip-sync сценах; в i2v история раскрывается через среду и детали." },
  { value: "background_only", label: "Фон / силуэт", tooltip: "Персонаж виден частично: силуэт, спина, плечо, фигура в тени, но не главный объект." },
  { value: "offscreen_voice", label: "За кадром", tooltip: "Персонаж не появляется визуально и работает как голос/рассказчик." },
];
const BINDING_TYPE_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "held", label: "В руках" },
  { value: "nearby", label: "Рядом" },
  { value: "worn", label: "На персонаже" },
  { value: "pocketed", label: "В кармане / сумке" },
  { value: "shared", label: "Общий предмет" },
  { value: "environment", label: "Часть мира / окружения" },
];
const LINKED_CHARACTER_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "character_1", label: "character_1" },
  { value: "character_2", label: "character_2" },
  { value: "character_3", label: "character_3" },
  { value: "shared", label: "Общий" },
  { value: "world", label: "Мир" },
];
const CHARACTER_VIEW_SLOTS = [
  { key: "front_primary", title: "Фронт / основной", hint: "Обязательный canonical identity ref", required: true },
  { key: "side_profile", title: "Бок / профиль", hint: "Support: профиль, силуэт, форма головы", required: false },
  { key: "performance_medium", title: "Полутело / lip-sync", hint: "Support: мимика, рот, performance", required: false },
  { key: "back_optional", title: "Сзади / optional", hint: "Support только для back-facing сцен", required: false },
];

const IDENTITY_GENDER_DEFAULT = {
  "девушка": "female",
  "женщина": "female",
  "парень": "male",
  "мужчина": "male",
};
const NON_HUMAN_IDENTITY = new Set(["ребёнок", "животное", "группа людей", "другое"]);

function normalizeStoryRole(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return ["auto", "main", "secondary", "support", "antagonist", "minor", "group"].includes(normalized) ? normalized : "auto";
}

function normalizeIdentityLabel(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return ["auto", "девушка", "парень", "женщина", "мужчина", "ребёнок", "животное", "группа людей", "другое"].includes(normalized) ? normalized : "auto";
}

function normalizeGenderHint(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return ["auto", "female", "male", "not_applicable"].includes(normalized) ? normalized : "auto";
}

function normalizeBindingType(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "carried") return "nearby";
  return ["auto", "held", "nearby", "worn", "pocketed", "shared", "environment"].includes(normalized) ? normalized : "auto";
}

function normalizeLinkedCharacter(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return ["auto", "character_1", "character_2", "character_3", "shared", "world"].includes(normalized) ? normalized : "auto";
}

function normalizeAppearanceMode(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "only_lipsync" || normalized === "lip-sync only") return "lip_sync_only";
  if (normalized === "voice_only") return "offscreen_voice";
  if (normalized === "silhouette") return "background_only";
  return ["auto", "story_visible", "lip_sync_only", "background_only", "offscreen_voice"].includes(normalized) ? normalized : "auto";
}

function resolveGenderHintDefault(identityLabel = "") {
  const normalized = normalizeIdentityLabel(identityLabel);
  if (IDENTITY_GENDER_DEFAULT[normalized]) return IDENTITY_GENDER_DEFAULT[normalized];
  if (NON_HUMAN_IDENTITY.has(normalized)) return "not_applicable";
  return "auto";
}

function getRefThumbCandidateSignature(item) {
  return buildRefImageCandidates(item).join("|");
}

function getStableRefItemKey(item, idx) {
  const explicitId = String(item?.id || "").trim();
  if (explicitId) return explicitId;
  const candidateSignature = getRefThumbCandidateSignature(item);
  if (candidateSignature) return `sig:${candidateSignature}`;
  return `idx:${idx}`;
}

const RefThumbImage = React.memo(function RefThumbImage({
  item,
  idx,
  title,
  handleId,
  onOpenLightbox,
}) {
  const candidates = useMemo(() => buildRefImageCandidates(item), [item]);
  const candidateSignature = useMemo(() => candidates.join("|"), [candidates]);
  const rerenderCountRef = useRef(0);
  const [candidateIndex, setCandidateIndex] = useState(0);
  const [thumbExhausted, setThumbExhausted] = useState(candidates.length === 0);
  const activeSrc = candidates[candidateIndex] || "";

  useEffect(() => {
    console.debug("[REF THUMB FIX] mount", { handleId, idx });
  }, [handleId, idx]);

  useEffect(() => {
    rerenderCountRef.current += 1;
    if (rerenderCountRef.current <= 3) {
      console.debug("[REF THUMB FIX] rerender", { handleId, idx, count: rerenderCountRef.current });
    }
  });

  useEffect(() => {
    console.debug("[REF THUMB FIX] candidateSignature changed", { handleId, idx });
    setCandidateIndex(0);
    setThumbExhausted(candidates.length === 0);
  }, [candidateSignature, candidates.length, handleId, idx]);

  if (thumbExhausted || !activeSrc) {
    return <div className="clipSB_refLiteEmpty" title="Thumbnail недоступен"><span>thumbnail недоступен</span></div>;
  }

  return (
    <button className="clipSB_refLiteOpen" onClick={() => onOpenLightbox?.(activeSrc)} title="Открыть фото">
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
}, (prevProps, nextProps) => {
  return (
    prevProps.idx === nextProps.idx
    && prevProps.title === nextProps.title
    && prevProps.handleId === nextProps.handleId
    && getRefThumbCandidateSignature(prevProps.item) === getRefThumbCandidateSignature(nextProps.item)
  );
});

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
  const storyRole = normalizeStoryRole(data?.storyRole || data?.story_role);
  const identityLabel = normalizeIdentityLabel(data?.identityLabel || data?.identity_label);
  const genderHint = normalizeGenderHint(data?.genderHint || data?.gender_hint);
  const bindingType = normalizeBindingType(data?.bindingType || data?.binding_type);
  const linkedCharacter = normalizeLinkedCharacter(data?.linkedCharacter || data?.linked_character);
  const appearanceMode = normalizeAppearanceMode(data?.appearanceMode || data?.screenPresenceMode || data?.appearance_mode || data?.screen_presence_mode);
  const onOpenLightbox = data?.onOpenLightbox;
  const isCharacter1Node = handleId === "ref_character";
  const normalizedCharacterViews = isCharacter1Node && data?.characterViews && typeof data.characterViews === "object"
    ? data.characterViews
    : {};
  const refsWithSlotMeta = refs.map((item, idx) => {
    const declaredViewType = String(item?.view_type || item?.viewType || "").trim().toLowerCase();
    const slotByView = CHARACTER_VIEW_SLOTS.find((slot) => slot.key === declaredViewType);
    const slot = slotByView || CHARACTER_VIEW_SLOTS[idx] || null;
    const slotFromState = slot ? normalizedCharacterViews?.[slot.key] : null;
    return {
      item,
      idx,
      slot,
      slotLabel: String(item?.label || slotFromState?.label || slot?.title || "").trim(),
    };
  });

  const openPicker = () => { if (canAddMore) inputRef.current?.click(); };
  const onInputChange = async (e) => { const files = Array.from(e.target.files || []); if (files.length) await data?.onPickImage?.(id, files); e.target.value = ""; };

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
        <>
          <div style={{ marginBottom: 10 }}>
            <div className="clipSB_small" style={{ marginBottom: 4 }}>Сюжетная роль:</div>
            <select
              className="clipSB_select"
              value={storyRole}
              onChange={(event) => data?.onField?.(id, "storyRole", normalizeStoryRole(event?.target?.value))}
              disabled={refStatus === "loading"}
            >
              {STORY_ROLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
          <div style={{ marginBottom: 10 }}>
            <div className="clipSB_small" style={{ marginBottom: 4 }}>Кто это:</div>
            <select
              className="clipSB_select"
              value={identityLabel}
              onChange={(event) => {
                const nextIdentity = normalizeIdentityLabel(event?.target?.value);
                data?.onField?.(id, "identityLabel", nextIdentity);
                if (genderHint === "auto" || genderHint === "not_applicable") {
                  data?.onField?.(id, "genderHint", resolveGenderHintDefault(nextIdentity));
                }
              }}
              disabled={refStatus === "loading"}
            >
              {IDENTITY_LABEL_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
          <div style={{ marginBottom: 10 }}>
            <div className="clipSB_small" style={{ marginBottom: 4 }}>Гендер:</div>
            <select
              className="clipSB_select"
              value={genderHint}
              onChange={(event) => data?.onField?.(id, "genderHint", normalizeGenderHint(event?.target?.value))}
              disabled={refStatus === "loading"}
            >
              {GENDER_HINT_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
          <div style={{ marginBottom: 10 }}>
            <div className="clipSB_small" style={{ marginBottom: 4 }}>Появление на экране:</div>
            <select
              className="clipSB_select"
              value={appearanceMode}
              onChange={(event) => data?.onField?.(id, "appearanceMode", normalizeAppearanceMode(event?.target?.value))}
              disabled={refStatus === "loading"}
              title={APPEARANCE_MODE_OPTIONS.find((option) => option.value === appearanceMode)?.tooltip || ""}
            >
              {APPEARANCE_MODE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value} title={option.tooltip}>{option.label}</option>
              ))}
            </select>
          </div>
        </>
      ) : null}
      <div style={{ marginBottom: 10 }}>
        <div className="clipSB_small" style={{ marginBottom: 4 }}>Связь с персонажем:</div>
        <select
          className="clipSB_select"
          value={bindingType}
          onChange={(event) => data?.onField?.(id, "bindingType", normalizeBindingType(event?.target?.value))}
          disabled={refStatus === "loading"}
        >
          {BINDING_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </div>
      <div style={{ marginBottom: 10 }}>
        <div className="clipSB_small" style={{ marginBottom: 4 }}>Связан с:</div>
        <select
          className="clipSB_select"
          value={linkedCharacter}
          onChange={(event) => data?.onField?.(id, "linkedCharacter", normalizeLinkedCharacter(event?.target?.value))}
          disabled={refStatus === "loading"}
        >
          {LINKED_CHARACTER_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      </div>
      {isCharacter1Node ? (
        <div className="clipSB_refSlotLegend">
          {CHARACTER_VIEW_SLOTS.map((slot, idx) => {
            const hasSlotImage = refsWithSlotMeta.some((entry) => entry.slot?.key === slot.key);
            return (
              <div key={`${id}-slot-${slot.key}`} className={`clipSB_refSlotLegendItem ${hasSlotImage ? "is-filled" : "is-empty"}`}>
                <div className="clipSB_refSlotLegendTitle">
                  {idx + 1}. {slot.title} {slot.required ? <span className="clipSB_refSlotLegendRequired">• обяз.</span> : null}
                </div>
                <div className="clipSB_refSlotLegendHint">{slot.hint}</div>
              </div>
            );
          })}
        </div>
      ) : null}
      <div className="clipSB_refLitePreview">{!refs.length ? <div className="clipSB_refLiteEmpty" onClick={openPicker} role="button" tabIndex={0}><span className="clipSB_refLiteEmptyPlus">+</span><span>нет изображений</span><span>добавь фото</span></div> : <div className="clipSB_refGrid clipSB_refLiteGrid">{refsWithSlotMeta.map(({ item, idx, slot, slotLabel }) => {
        return (
          <div className="clipSB_refThumb" key={getStableRefItemKey(item, idx)}>
            <RefThumbImage item={item} idx={idx} title={title} handleId={handleId} onOpenLightbox={onOpenLightbox} />
            {isCharacter1Node && slot ? <div className="clipSB_refSlotBadge">{slotLabel || slot.title}</div> : null}
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
