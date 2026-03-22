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
  audio: "var(--family-audio)",
  text: "var(--family-text)",
  link: "var(--family-link)",
  video_ref: "var(--family-video-ref)",
  text_in: "var(--family-text)",
  audio_in: "var(--family-audio)",
  link_in: "var(--family-link)",
  video_ref_in: "var(--family-video-ref)",
  scenario_out: "var(--family-narrative)",
  voice_script_out: "var(--family-audio)",
  brain_package_out: "var(--family-brain)",
  brain_package: "var(--family-brain)",
  bg_music_prompt_out: "var(--family-music)",
  ref_character_1: "var(--family-ref-character)",
  ref_character_2: "var(--family-ref-character)",
  ref_character_3: "var(--family-ref-character)",
  ref_animal: "var(--family-ref-animal)",
  ref_group: "var(--family-ref-group)",
  ref_location: "var(--family-ref-location)",
  ref_style: "var(--family-ref-style)",
  ref_props: "var(--family-ref-items)",
  ref_items: "var(--family-ref-items)",
  comfy_plan: "var(--family-brain)",
  comfy_video: "var(--family-generation)",
  intro_context: "var(--family-text)",
};

const HANDLE_BASE_STYLE = {
  width: 12,
  height: 12,
  borderRadius: 999,
  border: "2px solid rgba(255,255,255,0.82)",
  opacity: 1,
};

export function handleStyle(kind, extra = {}) {
  const color = PORT_COLORS[kind] || "#8c8c8c";
  return { ...HANDLE_BASE_STYLE, background: color, boxShadow: `0 0 0 1px rgba(0,0,0,0.72), 0 0 0 2px color-mix(in srgb, ${color} 18%, transparent)`, ...extra };
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
