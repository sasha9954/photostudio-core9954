import React, { useRef, useState } from "react";
import { useReactFlow } from "@xyflow/react";

import { API_BASE } from "../../services/api";
import { Handle, NodeShell, Position, handleStyle } from "./comfy/comfyNodeShared";

const ACCEPT_ATTR = "video/*,.mp4,.mov,.webm,.mkv,.m4v";

function formatDuration(seconds) {
  const value = Number(seconds || 0);
  if (!Number.isFinite(value) || value <= 0) return "—";
  const whole = Math.max(0, Math.round(value));
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatBytes(size) {
  const value = Number(size || 0);
  if (!Number.isFinite(value) || value <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const scaled = value / (1024 ** index);
  return `${scaled >= 100 || index === 0 ? Math.round(scaled) : scaled.toFixed(1)} ${units[index]}`;
}

async function uploadAsset(file) {
  const fd = new FormData();
  fd.append("file", file);

  const res = await fetch(`${API_BASE}/api/assets/upload`, {
    method: "POST",
    body: fd,
    credentials: "include",
  });
  if (!res.ok) {
    let txt = "";
    try {
      txt = await res.text();
    } catch {
      // ignore
    }
    throw new Error(txt || `upload_failed:${res.status}`);
  }
  return await res.json();
}

async function extractVideoMetadata(file) {
  return await new Promise((resolve) => {
    let objectUrl = "";
    let resolved = false;
    const video = document.createElement("video");

    const finish = (value) => {
      if (resolved) return;
      resolved = true;
      try {
        video.pause();
        video.removeAttribute("src");
        video.load();
      } catch {
        // ignore
      }
      if (objectUrl) {
        try {
          URL.revokeObjectURL(objectUrl);
        } catch {
          // ignore
        }
      }
      resolve(value);
    };

    const readPoster = () => {
      try {
        const width = video.videoWidth || 0;
        const height = video.videoHeight || 0;
        if (!width || !height) {
          finish({
            durationSec: Number.isFinite(video.duration) ? video.duration : null,
            width,
            height,
            posterUrl: "",
          });
          return;
        }
        const canvas = document.createElement("canvas");
        const maxWidth = 640;
        const scale = Math.min(1, maxWidth / width);
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        const ctx = canvas.getContext("2d");
        if (!ctx) {
          finish({
            durationSec: Number.isFinite(video.duration) ? video.duration : null,
            width,
            height,
            posterUrl: "",
          });
          return;
        }
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        finish({
          durationSec: Number.isFinite(video.duration) ? video.duration : null,
          width,
          height,
          posterUrl: canvas.toDataURL("image/jpeg", 0.82),
        });
      } catch {
        finish({
          durationSec: Number.isFinite(video.duration) ? video.duration : null,
          width: video.videoWidth || 0,
          height: video.videoHeight || 0,
          posterUrl: "",
        });
      }
    };

    objectUrl = URL.createObjectURL(file);
    video.preload = "metadata";
    video.muted = true;
    video.playsInline = true;

    video.addEventListener("loadedmetadata", () => {
      const safeDuration = Number.isFinite(video.duration) ? video.duration : 0;
      const seekTarget = safeDuration > 0.24 ? Math.min(0.12, safeDuration / 3) : 0;
      try {
        video.currentTime = seekTarget;
      } catch {
        readPoster();
      }
    }, { once: true });

    video.addEventListener("seeked", readPoster, { once: true });
    video.addEventListener("loadeddata", readPoster, { once: true });
    video.addEventListener("error", () => finish({
      durationSec: null,
      width: 0,
      height: 0,
      posterUrl: "",
    }), { once: true });

    video.src = objectUrl;
  });
}

function buildVideoPayload({
  fileName = "",
  assetUrl = "",
  durationSec = null,
  mime = "",
  size = 0,
  posterUrl = "",
  width = 0,
  height = 0,
} = {}) {
  const safeFileName = String(fileName || "").trim();
  const safeAssetUrl = String(assetUrl || "").trim();
  const safeMime = String(mime || "").trim();
  const safeSize = Number(size || 0);
  const safeDuration = Number(durationSec || 0);
  const safePoster = String(posterUrl || "").trim();
  const preview = safeFileName || safeAssetUrl || "Видео (референс)";

  if (!safeAssetUrl && !safeFileName) return null;

  return {
    type: "video_ref",
    value: safeAssetUrl || safeFileName,
    preview,
    sourceLabel: "Видео (референс)",
    fileName: safeFileName,
    assetUrl: safeAssetUrl,
    url: safeAssetUrl,
    posterUrl: safePoster,
    meta: {
      kind: "video_ref",
      duration: Number.isFinite(safeDuration) && safeDuration > 0 ? safeDuration : null,
      mime: safeMime,
      size: Number.isFinite(safeSize) && safeSize > 0 ? safeSize : 0,
      width: Number(width || 0) || 0,
      height: Number(height || 0) || 0,
    },
  };
}

export default function VideoRefNode({ id, data }) {
  const { setNodes } = useReactFlow();
  const inputRef = useRef(null);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);

  const fileName = String(data?.fileName || "").trim();
  const assetUrl = String(data?.assetUrl || data?.url || "").trim();
  const posterUrl = String(data?.posterUrl || data?.previewImage || "").trim();
  const mime = String(data?.mime || data?.meta?.mime || "").trim();
  const size = Number(data?.size ?? data?.meta?.size ?? 0);
  const durationSec = Number(data?.durationSec ?? data?.meta?.duration ?? 0);
  const uploadError = String(data?.uploadError || "").trim();
  const isUploading = !!data?.uploading;
  const hasReadyValue = !!assetUrl;

  const setNodeData = (patch) => {
    setNodes((prev) => prev.map((node) => {
      if (node.id !== id) return node;
      return { ...node, data: { ...(node.data || {}), ...(patch || {}) } };
    }));
  };

  const onSelectFile = async (file) => {
    if (!file) return;

    const looksLikeVideo = String(file.type || "").startsWith("video/")
      || /\.(mp4|mov|webm|mkv|m4v)$/i.test(String(file.name || ""));

    if (!looksLikeVideo) {
      setNodeData({
        uploading: false,
        uploadError: "Файл не распознан как поддерживаемое видео. Используйте mp4 / mov / webm / mkv.",
      });
      return;
    }

    setNodeData({
      uploading: true,
      uploadError: "",
      fileName: file.name,
      mime: file.type || "",
      size: file.size || 0,
    });

    try {
      const [meta, upload] = await Promise.all([
        extractVideoMetadata(file),
        uploadAsset(file),
      ]);

      const nextPayload = buildVideoPayload({
        fileName: upload?.name || file.name,
        assetUrl: upload?.url || "",
        durationSec: meta?.durationSec,
        mime: file.type || "",
        size: file.size || 0,
        posterUrl: meta?.posterUrl || "",
        width: meta?.width || 0,
        height: meta?.height || 0,
      });

      if (!nextPayload?.assetUrl) {
        throw new Error("Не удалось получить URL загруженного видео.");
      }

      setNodeData({
        uploading: false,
        uploadError: "",
        fileName: upload?.name || file.name,
        assetUrl: upload.url,
        url: upload.url,
        durationSec: meta?.durationSec || null,
        mime: file.type || "",
        size: file.size || 0,
        posterUrl: meta?.posterUrl || "",
        previewImage: meta?.posterUrl || "",
        width: meta?.width || 0,
        height: meta?.height || 0,
        savedPayload: nextPayload,
        outputPayload: nextPayload,
      });
    } catch (error) {
      setNodeData({
        uploading: false,
        uploadError: error?.message || "Не удалось обработать видеофайл.",
      });
    }
  };

  const clearVideo = () => {
    setNodeData({
      uploading: false,
      uploadError: "",
      fileName: "",
      assetUrl: "",
      url: "",
      durationSec: null,
      mime: "",
      size: 0,
      posterUrl: "",
      previewImage: "",
      width: 0,
      height: 0,
      savedPayload: null,
      outputPayload: null,
    });
    setIsPreviewOpen(false);
  };

  const statusTitle = uploadError
    ? "Ошибка загрузки"
    : hasReadyValue
      ? "Видео готово"
      : "Выберите видеофайл";

  const statusBody = uploadError
    ? uploadError
    : hasReadyValue
      ? "Локальный видео-референс подключён и готов для narrative."
      : "Локальный видео-референс для narrative. Нода сохранит файл, метаданные и выходной payload.";

  return (
    <>
      <Handle type="source" position={Position.Right} id="video_ref" className="clipSB_handle" style={handleStyle("video_ref")} />

      <NodeShell
        title="ВИДЕО"
        icon={<span aria-hidden>🎬</span>}
        className="clipSB_nodeVideoRef"
        onClose={() => data?.onRemoveNode?.(id)}
      >
        <div className={`clipSB_videoRefCard ${hasReadyValue ? "isReady" : ""} ${uploadError ? "isError" : ""}`.trim()}>
          <div className={`clipSB_videoRefPoster ${posterUrl ? "hasImage" : ""}`.trim()}>
            {posterUrl ? (
              <img src={posterUrl} alt={fileName || "Видео"} className="clipSB_videoRefPosterImg" />
            ) : (
              <div className="clipSB_videoRefPosterPlaceholder">
                <span className="clipSB_videoRefPosterIcon" aria-hidden>▶</span>
                <span className="clipSB_videoRefPosterLabel">{hasReadyValue ? "MEDIA PREVIEW" : "LOCAL VIDEO"}</span>
              </div>
            )}
          </div>

          <div className="clipSB_videoRefContent">
            <div className="clipSB_videoRefEyebrow">VIDEO REF SOURCE</div>
            <div className="clipSB_videoRefTitle">{statusTitle}</div>
            <div className="clipSB_videoRefBody">{statusBody}</div>

            {fileName ? (
              <div className="clipSB_videoRefMetaGrid">
                <div className="clipSB_videoRefMetaRow">
                  <span>Файл</span>
                  <strong title={fileName}>{fileName}</strong>
                </div>
                <div className="clipSB_videoRefMetaRow">
                  <span>Длительность</span>
                  <strong>{formatDuration(durationSec)}</strong>
                </div>
                <div className="clipSB_videoRefMetaRow">
                  <span>Формат</span>
                  <strong>{mime || "—"}</strong>
                </div>
                <div className="clipSB_videoRefMetaRow">
                  <span>Размер</span>
                  <strong>{formatBytes(size)}</strong>
                </div>
              </div>
            ) : null}
          </div>
        </div>

        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          style={{ display: "none" }}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.currentTarget.value = "";
            onSelectFile(file);
          }}
        />

        <div className="clipSB_videoRefActions">
          <button type="button" className="clipSB_btn clipSB_videoRefPrimaryBtn" disabled={isUploading} onClick={() => inputRef.current?.click()}>
            {isUploading ? "Загрузка видео…" : hasReadyValue ? "Заменить видео" : "Выбрать видео"}
          </button>
          <button
            type="button"
            className="clipSB_btn clipSB_btnSecondary"
            disabled={!assetUrl}
            onClick={() => setIsPreviewOpen(true)}
          >
            Просмотр
          </button>
        </div>

        {hasReadyValue ? (
          <div className="clipSB_videoRefFootnote">
            Output handle <b>video_ref</b> отдаёт payload <code>video_ref</code> с asset URL, именем файла, preview и meta.
          </div>
        ) : null}

        {hasReadyValue ? (
          <button type="button" className="clipSB_videoRefClear" onClick={clearVideo}>
            Очистить источник
          </button>
        ) : null}

        {!hasReadyValue && !uploadError ? (
          <div className="clipSB_small">
            Компактный preview без встроенного плеера в теле ноды: постер, метаданные и отдельный просмотр по кнопке.
          </div>
        ) : null}

        {isPreviewOpen && assetUrl ? (
          <div className="clipSB_videoRefModal" onClick={() => setIsPreviewOpen(false)}>
            <div className="clipSB_videoRefModalDialog" onClick={(event) => event.stopPropagation()}>
              <div className="clipSB_videoRefModalHeader">
                <div className="clipSB_videoRefModalTitle" title={fileName || assetUrl}>{fileName || "Видео (референс)"}</div>
                <button type="button" className="clipSB_drawerClose" onClick={() => setIsPreviewOpen(false)} title="Закрыть">×</button>
              </div>
              <video className="clipSB_videoRefModalPlayer" src={assetUrl} poster={posterUrl || undefined} controls preload="metadata" />
            </div>
          </div>
        ) : null}
      </NodeShell>
    </>
  );
}
