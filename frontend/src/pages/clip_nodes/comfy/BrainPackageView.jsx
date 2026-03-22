import React from "react";

function normalizeText(value) {
  return String(value || "").trim();
}

function normalizeList(value) {
  if (Array.isArray(value)) return value.map((item) => normalizeText(item)).filter(Boolean);
  const text = normalizeText(value);
  return text ? [text] : [];
}

export function isBrainPackageObject(value) {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

export function getBrainPackageSceneLogic(brainPackage = null) {
  return normalizeList(brainPackage?.sceneLogic);
}

export function getBrainPackageEntities(brainPackage = null) {
  return normalizeList(brainPackage?.entities);
}

function BrainField({ label, value }) {
  const text = normalizeText(value) || "—";
  return (
    <div className="clipSB_brainPackageField">
      <span>{label}</span>
      <strong>{text}</strong>
    </div>
  );
}

export default function BrainPackageView({ brainPackage, variant = "tester", footer = null }) {
  if (!isBrainPackageObject(brainPackage)) return null;

  const entities = getBrainPackageEntities(brainPackage);
  const sceneLogic = getBrainPackageSceneLogic(brainPackage);
  const sourceOriginLabel = brainPackage?.sourceOrigin === "connected" ? "Подключённый источник" : "Источник не подключён";
  const rootClassName = `clipSB_brainPackageView clipSB_brainPackageView--${variant}`.trim();

  return (
    <div className={rootClassName}>
      <div className="clipSB_brainPackageGrid">
        <BrainField label="contentTypeLabel" value={brainPackage?.contentTypeLabel} />
        <BrainField label="styleLabel" value={brainPackage?.styleLabel} />
        <BrainField label="sourceLabel" value={brainPackage?.sourceLabel} />
        <BrainField label="sourcePreview" value={brainPackage?.sourcePreview} />
      </div>

      <div className="clipSB_brainPackageField">
        <span>source status</span>
        <strong>{sourceOriginLabel}</strong>
      </div>

      <div className="clipSB_brainPackageField">
        <span>entities</span>
        {entities.length ? (
          <div className="clipSB_brainPackageBadges" role="list" aria-label="Brain package entities">
            {entities.map((entity) => (
              <span key={entity} className="clipSB_brainPackageBadge" role="listitem">{entity}</span>
            ))}
          </div>
        ) : (
          <strong>—</strong>
        )}
      </div>

      <div className="clipSB_brainPackageField">
        <span>sceneLogic</span>
        {sceneLogic.length ? (
          <ol className="clipSB_brainPackageList">
            {sceneLogic.map((item) => <li key={item}>{item}</li>)}
          </ol>
        ) : (
          <strong>—</strong>
        )}
      </div>

      <BrainField label="audioStrategy" value={brainPackage?.audioStrategy} />
      <BrainField label="directorNote" value={brainPackage?.directorNote} />

      {footer ? <div className="clipSB_brainPackageFooter">{footer}</div> : null}
    </div>
  );
}
