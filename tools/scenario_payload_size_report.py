#!/usr/bin/env python3
"""
Lightweight JSON size estimator for scenario-stage payload/response objects.

Usage:
  python tools/scenario_payload_size_report.py path/to/payload.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TARGET_PATHS = [
    "storyboardPackage",
    "storyboardPackage.diagnostics",
    "storyboardPackage.diagnostics.events",
    "storyboardPackage.input.connected_context_summary",
    "storyboardPackage.refs_inventory",
    "storyboardPackage.scene_plan",
    "storyboardPackage.scene_prompts",
    "storyboardPackage.final_storyboard",
    "directorOutput",
    "directorOutput.diagnostics",
    "diagnostics",
    "context_refs",
    "connected_context_summary",
]


def _walk_path(root: Any, path: str) -> Any:
    cur = root
    for token in path.split('.'):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(token)
    return cur


def _size_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _human(num: int) -> str:
    if num < 1024:
        return f"{num} B"
    if num < 1024 * 1024:
        return f"{num / 1024:.2f} KiB"
    return f"{num / (1024 * 1024):.2f} MiB"


def _top_level_breakdown(data: dict[str, Any]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for key, value in data.items():
        out.append((key, _size_bytes(value)))
    out.sort(key=lambda item: item[1], reverse=True)
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tools/scenario_payload_size_report.py <json_file>")
        return 2

    src = Path(sys.argv[1])
    if not src.exists():
        print(f"File not found: {src}")
        return 2

    raw = src.read_text(encoding="utf-8")
    data = json.loads(raw)

    total = _size_bytes(data)
    print(f"Total JSON size: {_human(total)} ({total} bytes)\n")

    print("Tracked sections:")
    for path in TARGET_PATHS:
        value = _walk_path(data, path)
        if value is None:
            continue
        sz = _size_bytes(value)
        extra = ""
        if path.endswith("events") and isinstance(value, list):
            extra = f"; items={len(value)}"
        print(f"- {path}: {_human(sz)} ({sz} bytes){extra}")

    if isinstance(data, dict):
        print("\nTop-level contribution:")
        for key, sz in _top_level_breakdown(data)[:20]:
            print(f"- {key}: {_human(sz)} ({sz} bytes)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
