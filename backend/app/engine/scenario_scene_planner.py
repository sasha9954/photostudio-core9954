from __future__ import annotations

import json
from typing import Any

from app.engine.gemini_rest import post_generate_content

SCENE_PLAN_PROMPT_VERSION = "scene_plan_v1"
ALLOWED_ROUTES = {"i2v", "ia2v", "first_last"}
TURN_FUNCTION_HINTS = {
    "turn",
    "reveal",
    "payoff",
    "release",
    "callback",
    "climax",
    "afterimage",
    "resolution",
    "drop",
}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _round3(value: Any) -> float:
    try:
        return round(float(value), 3)
    except Exception:
        return 0.0


def _extract_json_obj(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        first, last = raw.find("{"), raw.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(raw[first : last + 1])
            except Exception:
                return {}
    return {}


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
    if not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part.get("text") or "")
    return "\n".join(chunks).strip()


def _build_scene_windows(audio_map: dict[str, Any]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for idx, row_raw in enumerate(_safe_list(audio_map.get("scene_candidate_windows")), start=1):
        row = _safe_dict(row_raw)
        t0 = _round3(row.get("t0"))
        t1 = _round3(row.get("t1"))
        if t1 <= t0:
            continue
        windows.append(
            {
                "scene_id": str(row.get("id") or f"sc_{idx}"),
                "t0": t0,
                "t1": t1,
                "duration_sec": _round3(row.get("duration_sec") or (t1 - t0)),
                "phrase_text": str(row.get("phrase_text") or "").strip(),
                "scene_function": str(row.get("scene_function") or "").strip(),
                "energy": str(row.get("energy") or "").strip(),
            }
        )
    return windows


def _build_scene_role_lookup(role_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(role_plan.get("scene_roles")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            lookup[scene_id] = row
    return lookup


def _build_scene_planning_context(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_windows = _build_scene_windows(audio_map)

    context = {
        "mode": "clip",
        "content_type": str(input_pkg.get("content_type") or ""),
        "format": str(input_pkg.get("format") or ""),
        "director_note": str(input_pkg.get("director_note") or input_pkg.get("note") or "")[:1200],
        "story_core": {
            "story_summary": str(story_core.get("story_summary") or "")[:1200],
            "opening_anchor": str(story_core.get("opening_anchor") or "")[:600],
            "ending_callback_rule": str(story_core.get("ending_callback_rule") or "")[:600],
            "global_arc": str(story_core.get("global_arc") or "")[:600],
        },
        "audio_map": {
            "sections": _safe_list(audio_map.get("sections")),
            "scene_windows": scene_windows,
            "cut_policy": _safe_dict(audio_map.get("cut_policy")),
        },
        "role_plan": {
            "global_roles": _safe_dict(role_plan.get("global_roles")),
            "world_continuity": _safe_dict(role_plan.get("world_continuity")),
            "scene_roles": _safe_list(role_plan.get("scene_roles")),
            "role_arc_summary": str(role_plan.get("role_arc_summary") or ""),
            "continuity_notes": _safe_list(role_plan.get("continuity_notes")),
        },
        "clip_scene_policy": {
            "target_route_mix_for_8_scenes": {"i2v": 4, "ia2v": 2, "first_last": 2},
            "ia2v_definition": "emotion-first performance shot; readable face/mouth; smooth camera; restrained motion",
            "i2v_definition": "baseline clip route for observation, transit, environment and connective montage scenes",
            "first_last_definition": "explicit state transition A->B for reveal/turn/payoff/release/callback scenes",
        },
    }
    aux = {"scene_windows": scene_windows, "role_lookup": _build_scene_role_lookup(role_plan)}
    return context, aux


def _build_prompt(context: dict[str, Any]) -> str:
    return (
        "You are SCENE PLAN stage for scenario pipeline.\\n"
        "Return STRICT JSON only. No markdown.\\n"
        "MODE IS CLIP ONLY.\\n"
        "Build final watchable scene plan from fixed scene windows.\\n"
        "Use scene windows exactly as provided.\\n"
        "Keep route mix intelligent (not random), preserve role/world continuity, and keep rhythm/emotional variation.\\n"
        "For 8 scenes target route mix 4 i2v / 2 ia2v / 2 first_last unless there is a strong reason.\\n"
        "Do NOT assign ia2v to every performance scene. Do NOT flatten all scenes into one route.\\n"
        "Preserve realism and coherent lighting/world progression from role_plan world continuity.\\n\\n"
        "ROUTE SEMANTICS (MANDATORY):\\n"
        "- i2v: baseline clip scene for observation/transit/environment/connective motion.\\n"
        "- ia2v: emotional performance shot, readable face, smooth motion, minimal abrupt full-body action.\\n"
        "- first_last: explicit visual transition/progression for reveal/turn/payoff/release/callback.\\n\\n"
        "Return EXACT contract keys:\\n"
        "{\\n"
        '  \"plan_version\": \"scene_plan_v1\",\\n'
        '  \"mode\": \"clip\",\\n'
        '  \"route_mix_summary\": {\"total_scenes\": 0, \"i2v\": 0, \"ia2v\": 0, \"first_last\": 0},\\n'
        '  \"scenes\": [{\"scene_id\": \"sc_1\", \"t0\": 0.0, \"t1\": 1.0, \"duration_sec\": 1.0, \"primary_role\": \"character_1\", \"active_roles\": [\"character_1\"], \"scene_presence_mode\": \"solo_observational\", \"scene_function\": \"setup\", \"route\": \"i2v\", \"route_reason\": \"\", \"emotional_intent\": \"\", \"motion_intent\": \"\", \"watchability_role\": \"\"}],\\n'
        '  \"scene_arc_summary\": \"\",\\n'
        '  \"route_strategy_notes\": [\"\"]\\n'
        "}\\n\\n"
        f"SCENE_PLANNING_CONTEXT:\\n{json.dumps(context, ensure_ascii=False)}"
    )


def _target_route_budget(total_scenes: int) -> dict[str, int]:
    if total_scenes <= 0:
        return {"i2v": 0, "ia2v": 0, "first_last": 0}
    if total_scenes == 8:
        return {"i2v": 4, "ia2v": 2, "first_last": 2}
    if total_scenes == 1:
        return {"i2v": 1, "ia2v": 0, "first_last": 0}
    if total_scenes == 2:
        return {"i2v": 1, "ia2v": 0, "first_last": 1}

    ia2v = max(1, int(round(total_scenes * 0.25)))
    first_last = max(1, int(round(total_scenes * 0.25)))
    i2v = total_scenes - ia2v - first_last
    while i2v < max(1, total_scenes // 2):
        if first_last > 1:
            first_last -= 1
        elif ia2v > 1:
            ia2v -= 1
        else:
            break
        i2v = total_scenes - ia2v - first_last
    if i2v < 1:
        i2v = 1
    overflow = i2v + ia2v + first_last - total_scenes
    while overflow > 0 and first_last > 1:
        first_last -= 1
        overflow -= 1
    while overflow > 0 and ia2v > 1:
        ia2v -= 1
        overflow -= 1
    while overflow > 0 and i2v > 1:
        i2v -= 1
        overflow -= 1
    return {"i2v": i2v, "ia2v": ia2v, "first_last": first_last}


def _route_scores(scene: dict[str, Any], idx: int, total: int) -> dict[str, int]:
    presence_mode = str(scene.get("scene_presence_mode") or "").strip().lower()
    scene_function = str(scene.get("scene_function") or "").strip().lower()
    performance_focus = bool(scene.get("performance_focus"))

    scores = {"i2v": 1, "ia2v": 0, "first_last": 0}

    if presence_mode in {"transit", "environment_anchor", "solo_observational"}:
        scores["i2v"] += 3
    if presence_mode in {"solo_performance", "private_release"}:
        scores["ia2v"] += 4
    if performance_focus:
        scores["ia2v"] += 4

    if any(hint in scene_function for hint in TURN_FUNCTION_HINTS):
        scores["first_last"] += 4
    if idx == total - 1:
        scores["first_last"] += 2
    if idx == 0:
        scores["i2v"] += 2

    if "release" in scene_function and performance_focus:
        scores["first_last"] += 2

    return scores


def _default_route(scene: dict[str, Any], idx: int, total: int) -> str:
    scores = _route_scores(scene, idx, total)
    return max(("i2v", "ia2v", "first_last"), key=lambda route: (scores[route], route == "i2v"))


def _rebalance_routes(scenes: list[dict[str, Any]], target: dict[str, int]) -> bool:
    if not scenes:
        return False

    changed = False

    def counts() -> dict[str, int]:
        return {route: sum(1 for row in scenes if row.get("route") == route) for route in ("i2v", "ia2v", "first_last")}

    cur = counts()
    total = len(scenes)
    for _ in range(total * 4):
        deficits = [route for route in ("i2v", "ia2v", "first_last") if cur[route] < target.get(route, 0)]
        surpluses = [route for route in ("i2v", "ia2v", "first_last") if cur[route] > target.get(route, 0)]
        if not deficits or not surpluses:
            break

        desired = deficits[0]
        best_idx = -1
        best_gain = -999
        for idx, row in enumerate(scenes):
            current_route = str(row.get("route") or "")
            if current_route not in surpluses:
                continue
            score = _route_scores(row, idx, total)
            gain = score.get(desired, 0) - score.get(current_route, 0)
            if gain > best_gain:
                best_gain = gain
                best_idx = idx

        if best_idx < 0:
            break

        row = scenes[best_idx]
        prev = str(row.get("route") or "")
        row["route"] = desired
        row["route_reason"] = (str(row.get("route_reason") or "").strip() + f" [route_rebalanced:{prev}->{desired}]").strip()
        changed = True
        cur = counts()

    return changed


def _normalize_scene_plan(raw_plan: dict[str, Any], *, scene_windows: list[dict[str, Any]], role_lookup: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool, str]:
    known_ids = {str(row.get("scene_id") or ""): row for row in scene_windows}
    raw_scenes_by_id: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(raw_plan.get("scenes")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id in known_ids:
            raw_scenes_by_id[scene_id] = row

    used_fallback = False
    normalized_scenes: list[dict[str, Any]] = []
    for idx, window in enumerate(scene_windows):
        scene_id = str(window.get("scene_id") or "")
        raw_row = _safe_dict(raw_scenes_by_id.get(scene_id))
        role_row = _safe_dict(role_lookup.get(scene_id))

        route_raw = str(raw_row.get("route") or "").strip().lower()
        route = route_raw if route_raw in ALLOWED_ROUTES else ""
        if not route:
            route = _default_route({**window, **role_row, **raw_row}, idx, len(scene_windows))
            used_fallback = True

        primary_role = str(raw_row.get("primary_role") or role_row.get("primary_role") or "").strip() or None
        active_roles = [
            str(item).strip() for item in _safe_list(raw_row.get("active_roles") or role_row.get("active_roles")) if str(item).strip()
        ]
        if primary_role and primary_role not in active_roles:
            active_roles.insert(0, primary_role)

        scene_presence_mode = str(raw_row.get("scene_presence_mode") or role_row.get("scene_presence_mode") or "solo_observational").strip()

        normalized_scenes.append(
            {
                "scene_id": scene_id,
                "t0": _round3(window.get("t0")),
                "t1": _round3(window.get("t1")),
                "duration_sec": _round3(window.get("duration_sec") or (_round3(window.get("t1")) - _round3(window.get("t0")))),
                "primary_role": primary_role,
                "active_roles": active_roles,
                "scene_presence_mode": scene_presence_mode,
                "scene_function": str(raw_row.get("scene_function") or window.get("scene_function") or "montage_progression").strip() or "montage_progression",
                "route": route,
                "route_reason": str(raw_row.get("route_reason") or "").strip() or "route_selected_by_policy",
                "emotional_intent": str(raw_row.get("emotional_intent") or "").strip() or "emotionally coherent clip beat",
                "motion_intent": str(raw_row.get("motion_intent") or "").strip() or "watchable realistic movement",
                "watchability_role": str(raw_row.get("watchability_role") or "").strip() or "maintain clip rhythm and visual continuity",
                "performance_focus": bool(role_row.get("performance_focus")),
            }
        )

    target_budget = _target_route_budget(len(normalized_scenes))
    if _rebalance_routes(normalized_scenes, target_budget):
        used_fallback = True

    route_counts = {route: sum(1 for row in normalized_scenes if row.get("route") == route) for route in ("i2v", "ia2v", "first_last")}

    plan = {
        "plan_version": SCENE_PLAN_PROMPT_VERSION,
        "mode": "clip",
        "route_mix_summary": {
            "total_scenes": len(normalized_scenes),
            "i2v": route_counts["i2v"],
            "ia2v": route_counts["ia2v"],
            "first_last": route_counts["first_last"],
        },
        "scenes": [
            {
                key: value
                for key, value in row.items()
                if key
                in {
                    "scene_id",
                    "t0",
                    "t1",
                    "duration_sec",
                    "primary_role",
                    "active_roles",
                    "scene_presence_mode",
                    "scene_function",
                    "route",
                    "route_reason",
                    "emotional_intent",
                    "motion_intent",
                    "watchability_role",
                }
            }
            for row in normalized_scenes
        ],
        "scene_arc_summary": str(raw_plan.get("scene_arc_summary") or "").strip() or "Clip scene progression with balanced route rhythm.",
        "route_strategy_notes": [str(item).strip() for item in _safe_list(raw_plan.get("route_strategy_notes")) if str(item).strip()] or [
            "i2v remains baseline route for connective watchability.",
            "ia2v reserved for emotionally readable performance beats.",
            "first_last reserved for explicit progression or payoff turns.",
        ],
    }

    validation_error = "" if normalized_scenes else "scene_plan_empty_after_normalization"
    return plan, used_fallback, validation_error


def build_gemini_scene_plan(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    context, aux = _build_scene_planning_context(package)
    scene_windows = _safe_list(aux.get("scene_windows"))
    role_lookup = _safe_dict(aux.get("role_lookup"))

    diagnostics = {
        "prompt_version": SCENE_PLAN_PROMPT_VERSION,
        "scene_count": len(scene_windows),
    }

    if not scene_windows:
        plan, used_fallback, validation_error = _normalize_scene_plan({}, scene_windows=scene_windows, role_lookup=role_lookup)
        diagnostics.update(
            {
                "route_counts": _safe_dict(_safe_dict(plan.get("route_mix_summary"))),
                "presence_modes": [],
                "route_flat": False,
            }
        )
        return {
            "ok": False,
            "scene_plan": plan,
            "error": "scene_windows_missing",
            "validation_error": validation_error or "scene_windows_missing",
            "used_fallback": True,
            "diagnostics": diagnostics,
        }

    prompt = _build_prompt(context)
    try:
        response = post_generate_content(
            api_key=str(api_key or "").strip(),
            model="gemini-2.5-pro",
            body={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=90,
        )
        if isinstance(response, dict) and response.get("__http_error__"):
            raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

        parsed = _extract_json_obj(_extract_gemini_text(response))
        scene_plan, used_fallback, validation_error = _normalize_scene_plan(
            parsed,
            scene_windows=scene_windows,
            role_lookup=role_lookup,
        )
        route_summary = _safe_dict(scene_plan.get("route_mix_summary"))
        presence_modes = sorted(
            {
                str(_safe_dict(role_lookup.get(str(row.get("scene_id") or ""))).get("scene_presence_mode") or "").strip()
                for row in _safe_list(scene_plan.get("scenes"))
            }
            - {""}
        )
        route_counts = {
            "i2v": int(route_summary.get("i2v") or 0),
            "ia2v": int(route_summary.get("ia2v") or 0),
            "first_last": int(route_summary.get("first_last") or 0),
        }
        diagnostics.update(
            {
                "route_counts": route_counts,
                "presence_modes": presence_modes,
                "route_flat": bool(_safe_list(scene_plan.get("scenes")) and len({r for r, c in route_counts.items() if c > 0}) <= 1),
            }
        )
        return {
            "ok": bool(_safe_list(scene_plan.get("scenes"))),
            "scene_plan": scene_plan,
            "error": "" if _safe_list(scene_plan.get("scenes")) else "invalid_scene_plan",
            "validation_error": validation_error,
            "used_fallback": used_fallback,
            "diagnostics": diagnostics,
        }
    except Exception as exc:  # noqa: BLE001
        scene_plan, used_fallback, validation_error = _normalize_scene_plan(
            {},
            scene_windows=scene_windows,
            role_lookup=role_lookup,
        )
        route_summary = _safe_dict(scene_plan.get("route_mix_summary"))
        route_counts = {
            "i2v": int(route_summary.get("i2v") or 0),
            "ia2v": int(route_summary.get("ia2v") or 0),
            "first_last": int(route_summary.get("first_last") or 0),
        }
        diagnostics.update(
            {
                "route_counts": route_counts,
                "presence_modes": sorted(
                    {
                        str(_safe_dict(role_lookup.get(str(row.get("scene_id") or ""))).get("scene_presence_mode") or "").strip()
                        for row in _safe_list(scene_plan.get("scenes"))
                    }
                    - {""}
                ),
                "route_flat": bool(_safe_list(scene_plan.get("scenes")) and len({r for r, c in route_counts.items() if c > 0}) <= 1),
            }
        )
        return {
            "ok": bool(_safe_list(scene_plan.get("scenes"))),
            "scene_plan": scene_plan,
            "error": str(exc),
            "validation_error": validation_error,
            "used_fallback": True,
            "diagnostics": diagnostics,
        }
