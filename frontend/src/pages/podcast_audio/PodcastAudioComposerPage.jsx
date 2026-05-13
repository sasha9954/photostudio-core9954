import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  normalizeManualTimingAudio,
  readManualTimingProjectForNode,
} from "../clip_nodes/manual_timing/manualTimingDomain.js";
import "./PodcastAudioComposerPage.css";

function normalizeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function roundSeconds(value, digits = 3) {
  return Number(Math.max(0, normalizeNumber(value, 0)).toFixed(digits));
}

function formatTimer(value) {
  const totalMs = Math.max(0, Math.round(normalizeNumber(value, 0) * 1000));
  const minutes = Math.floor(totalMs / 60000);
  const seconds = Math.floor((totalMs % 60000) / 1000);
  const milliseconds = totalMs % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

function formatDuration(value) {
  const seconds = normalizeNumber(value, 0);
  return seconds > 0 ? formatTimer(seconds) : "00:00.000";
}

function SimpleAudioTimeline({ durationSec = 0, currentTimeSec = 0, onSeek }) {
  const safeDuration = Math.max(0, normalizeNumber(durationSec, 0));
  const timelineDuration = Math.max(1, safeDuration);
  const safeCurrent = Math.min(timelineDuration, roundSeconds(currentTimeSec));
  const leftPercentValue = Math.min(100, Math.max(0, (safeCurrent / timelineDuration) * 100));
  const leftPercent = `${leftPercentValue}%`;
  const playheadClassName = `simpleAudioPlayhead${leftPercentValue > 82 ? " labelLeft" : ""}`;

  const seekTo = (timeSec) => {
    if (!onSeek || safeDuration <= 0) return;
    onSeek(roundSeconds(Math.min(safeDuration, Math.max(0, timeSec))));
  };

  const seekFromEvent = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0;
    seekTo(ratio * safeDuration);
  };

  const seekFromKeyboard = (event) => {
    const stepSec = event.shiftKey ? 10 : 1;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      seekTo(safeCurrent - stepSec);
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      seekTo(safeCurrent + stepSec);
    }
    if (event.key === "Home") {
      event.preventDefault();
      seekTo(0);
    }
    if (event.key === "End") {
      event.preventDefault();
      seekTo(safeDuration);
    }
  };

  return (
    <div className="simpleAudioTimeline">
      <div
        aria-label="Аудио дорожка"
        className="simpleAudioTimelineBar"
        onClick={seekFromEvent}
        onKeyDown={seekFromKeyboard}
        role="button"
        tabIndex={0}
      >
        <div className="simpleAudioTimelineFill" style={{ width: leftPercent }} />
        <div className={playheadClassName} style={{ left: leftPercent }}>
          <span>{formatTimer(safeCurrent)}</span>
        </div>
      </div>
    </div>
  );
}

export default function PodcastAudioComposerPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const sourceNodeId = String(location.state?.sourceNodeId || searchParams.get("sourceNodeId") || "").trim();
  const stateAudio = normalizeManualTimingAudio(location.state?.audio);
  const storedManualTimingProject = useMemo(() => readManualTimingProjectForNode(sourceNodeId), [sourceNodeId]);
  const audio = useMemo(() => {
    if (stateAudio.url) return stateAudio;
    return normalizeManualTimingAudio(storedManualTimingProject?.audio);
  }, [stateAudio, storedManualTimingProject]);

  const audioRef = useRef(null);
  const [currentTimeSec, setCurrentTimeSec] = useState(0);
  const [durationSec, setDurationSec] = useState(() => normalizeNumber(audio.duration_sec, 0));
  const [isPlaying, setIsPlaying] = useState(false);

  useEffect(() => {
    setCurrentTimeSec(0);
    setDurationSec(normalizeNumber(audio.duration_sec, 0));
    setIsPlaying(false);
  }, [audio.url, audio.duration_sec]);

  const onBackToNode = () => {
    navigate("/studio/storyboard", {
      state: {
        focusManualTimingNodeId: sourceNodeId,
        manualBoardSkipOpenStateReason: "podcast_back_to_node",
        closeManualDirectorBoard: true,
        closeLegacyScenarioEditors: true,
      },
    });
  };

  const seekAudio = (timeSec) => {
    const nextTime = roundSeconds(timeSec);
    setCurrentTimeSec(nextTime);
    if (audioRef.current) audioRef.current.currentTime = nextTime;
  };

  const togglePlayback = async () => {
    const element = audioRef.current;
    if (!element) return;

    if (isPlaying) {
      element.pause();
      setIsPlaying(false);
      return;
    }

    if (element.ended || (element.duration && element.currentTime >= element.duration - 0.05)) {
      element.currentTime = 0;
      setCurrentTimeSec(0);
    }

    try {
      await element.play();
      setIsPlaying(true);
    } catch {
      setIsPlaying(false);
    }
  };

  const onLoadedMetadata = (event) => {
    const metadataDuration = normalizeNumber(event.currentTarget.duration, 0);
    if (metadataDuration > 0) setDurationSec(roundSeconds(metadataDuration));
    setCurrentTimeSec(roundSeconds(event.currentTarget.currentTime));
  };

  const onTimeUpdate = (event) => {
    setCurrentTimeSec(roundSeconds(event.currentTarget.currentTime));
  };

  return (
    <div className="podcastComposerPage">
      <header className="podcastComposerHeader">
        <div>
          <p className="podcastComposerEyebrow">Podcast Audio Composer</p>
          <h1>Подкаст / аудио</h1>
        </div>
        <button type="button" onClick={onBackToNode}>Назад к ноде</button>
      </header>

      {!audio.url ? (
        <div className="podcastComposerMessage">
          Аудио не найдено. Вернитесь к ноде и загрузите аудио.
        </div>
      ) : (
        <section className="podcastComposerCard" aria-label="Прослушивание аудио">
          <div className="podcastComposerAudioMeta">
            <div>
              <span>Файл</span>
              <strong>{audio.filename || "audio"}</strong>
            </div>
            <div>
              <span>Длительность</span>
              <strong>{formatDuration(durationSec || audio.duration_sec)}</strong>
            </div>
          </div>

          <SimpleAudioTimeline
            currentTimeSec={currentTimeSec}
            durationSec={durationSec || audio.duration_sec}
            onSeek={seekAudio}
          />

          <div className="podcastComposerControls">
            <button className="podcastComposerPlayButton" type="button" onClick={togglePlayback}>
              {isPlaying ? "■ Stop" : "▶ Play"}
            </button>
            <span className="podcastComposerTimer">{formatTimer(currentTimeSec)}</span>
          </div>

          <audio
            ref={audioRef}
            src={audio.url}
            preload="metadata"
            onEnded={() => setIsPlaying(false)}
            onLoadedMetadata={onLoadedMetadata}
            onPause={() => setIsPlaying(false)}
            onPlay={() => setIsPlaying(true)}
            onTimeUpdate={onTimeUpdate}
          />
        </section>
      )}
    </div>
  );
}
