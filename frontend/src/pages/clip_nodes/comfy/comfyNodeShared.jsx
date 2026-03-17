import React, { useRef } from "react";
import { Handle, Position } from "@xyflow/react";
import { API_BASE } from "../../../services/api";

export { Handle, Position, useRef };

const MODE_DISPLAY_META = {
  clip: { labelRu: "Клип", descriptionRu: "Ритм, монтаж и музыкальная энергия с динамичными сценами." },
  kino: { labelRu: "Кино", descriptionRu: "Драматургия, логика сцен и причинно-следственная подача." },
  reklama: { labelRu: "Реклама", descriptionRu: "Хук, ценность и акцент на продукте или ключевой идее." },
  scenario: { labelRu: "Сценарий", descriptionRu: "Структурная раскадровка с понятными сценами и narrative steps." },
};

const STYLE_DISPLAY_META = {
  realism: { labelRu: "Реализм", descriptionRu: "Натуральный свет, правдоподобная физика и живое изображение." },
  film: { labelRu: "Кино-стиль", descriptionRu: "Киношная цветокоррекция, драматичный свет и авторская подача." },
  neon: { labelRu: "Неон", descriptionRu: "Контрастный свет, цветные акценты и стилизованная атмосфера." },
  glossy: { labelRu: "Глянец", descriptionRu: "Премиальная подача, чистая картинка и коммерческий блеск." },
  soft: { labelRu: "Мягкий", descriptionRu: "Нежный свет, спокойная атмосфера и воздушная картинка." },
};

export function getModeDisplayMeta(mode = "clip") { return MODE_DISPLAY_META[String(mode || "clip").toLowerCase()] || MODE_DISPLAY_META.clip; }
export function getStyleDisplayMeta(stylePreset = "realism") { return STYLE_DISPLAY_META[String(stylePreset || "realism").toLowerCase()] || STYLE_DISPLAY_META.realism; }

const PORT_COLORS = {
  audio: "#ff5f7d", text: "#8bb8ff", ref_character_1: "#34d5d7", ref_character_2: "#00bcd4", ref_character_3: "#26c6da", ref_animal: "#ffb74d", ref_group: "#f06292", ref_location: "#b37bff", ref_style: "#ffc25b", ref_props: "#93dd6f", ref_items: "#93dd6f", comfy_plan: "#4dd8ff", comfy_video: "#7df9ff",
};

export function handleStyle(kind, extra = {}) {
  const color = PORT_COLORS[kind] || "#8c8c8c";
  return { background: color, width: 12, height: 12, border: "2px solid rgba(255,255,255,0.42)", boxShadow: `0 0 0 1px rgba(0,0,0,0.55), 0 0 10px ${color}99`, ...extra };
}

export function NodeShell({ title, icon, children, className = "", onClose }) {
  return (
    <div className={`clipSB_node ${className}`}>
      <div className="clipSB_nodeHeader"><div className="clipSB_nodeTitle">{icon}{title}</div>{onClose ? <button className="clipSB_close" onClick={onClose}>×</button> : null}</div>
      <div className="clipSB_nodeBody">{children}</div>
    </div>
  );
}

export function resolveAssetUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw) || raw.startsWith("data:")) return raw;
  if (raw.startsWith("/static/assets/") || raw.startsWith("/assets/")) return `${API_BASE}${raw}`;
  if (raw.startsWith("static/assets/")) return `${API_BASE}/${raw}`;
  return raw;
}
