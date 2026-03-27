import React from "react";

function stopEvent(event) {
  event.stopPropagation();
}

async function writeTextToClipboard(text) {
  const payload = String(text || "");
  if (!payload.trim()) return false;
  try {
    await navigator.clipboard.writeText(payload);
    return true;
  } catch {
    const fallback = document.createElement("textarea");
    fallback.value = payload;
    fallback.setAttribute("readonly", "");
    fallback.style.position = "fixed";
    fallback.style.top = "-1000px";
    document.body.appendChild(fallback);
    fallback.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(fallback);
    return copied;
  }
}

function notifyCopied() {
  try {
    window.dispatchEvent(new CustomEvent("ps:notify", { detail: { type: "success", message: "Текст скопирован" } }));
  } catch {
    // ignore
  }
}

export default function ScenarioTabTextViewer({
  title = "",
  text = "",
  minRows = 4,
  copyLabel = "Копировать",
  copyAllLabel = "",
  onCopyAll = null,
  extraActions = null,
}) {
  const printable = String(text || "").trim() || "—";
  const rows = Math.max(Number(minRows) || 1, printable.split("\n").length);

  const handleCopy = async () => {
    const didCopy = await writeTextToClipboard(printable);
    if (didCopy) notifyCopied();
  };

  const handleCopyAll = async () => {
    if (typeof onCopyAll === "function") {
      const result = await onCopyAll();
      if (result !== false) notifyCopied();
      return;
    }
    const didCopy = await writeTextToClipboard(printable);
    if (didCopy) notifyCopied();
  };

  return (
    <div
      className="clipSB_scenarioTabTextViewer nodrag nopan nowheel"
      onMouseDown={stopEvent}
      onPointerDown={stopEvent}
    >
      <div className="clipSB_scenarioTabTextViewerHead nodrag nopan nowheel" onMouseDown={stopEvent} onPointerDown={stopEvent}>
        <div className="clipSB_scenarioTabTextViewerTitle">{title || "Текст"}</div>
        <div className="clipSB_scenarioTabTextViewerActions">
          {extraActions}
          {copyAllLabel ? (
            <button
              className="clipSB_btn clipSB_btnSecondary"
              type="button"
              onMouseDown={stopEvent}
              onPointerDown={stopEvent}
              onClick={handleCopyAll}
            >
              {copyAllLabel}
            </button>
          ) : null}
          <button
            className="clipSB_btn clipSB_btnSecondary"
            type="button"
            onMouseDown={stopEvent}
            onPointerDown={stopEvent}
            onClick={handleCopy}
          >
            {copyLabel}
          </button>
        </div>
      </div>

      <textarea
        className="clipSB_scenarioTabTextViewerInput nodrag nopan nowheel"
        readOnly
        rows={rows}
        value={printable}
        onMouseDown={stopEvent}
        onPointerDown={stopEvent}
      />
    </div>
  );
}
