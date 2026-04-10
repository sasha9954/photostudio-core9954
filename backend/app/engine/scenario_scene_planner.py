from __future__ import annotations

import json
from typing import Any

from app.engine.gemini_rest import post_generate_content

SCENE_PLAN_PROMPT_VERSION = "scene_plan_v1"
SCENE_PLAN_MODEL = "gemini-3.1-pro-preview"
ALLOWED_ROUTES = {"i2v", "ia2v", "first_last"}
GENERIC_ENVIRONMENT_FAMILIES = {"urban", "city", "interior", "outdoor"}
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
FIRST_LAST_EXCLUSION_HINTS = {"transit", "environment_anchor", "location_change", "world_jump", "montage"}
IA2V_ADJACENCY_PENALTY = 9
FIRST_LAST_ADJACENCY_PENALTY = 2


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


def _build_scene_world_summary(role_plan: dict[str, Any], story_core: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    world = _safe_dict(role_plan.get("world_continuity"))
    environment_family = str(world.get("environment_family") or "").strip()
    country = str(world.get("country_or_region") or "").strip()
    location_progression = [str(item).strip() for item in _safe_list(world.get("location_progression")) if str(item).strip()]
    style_anchor = str(world.get("style_anchor") or "").strip()
    realism_contract = str(world.get("realism_contract") or "").strip()
    story_summary = str(story_core.get("story_summary") or "").strip()
    opening_anchor = str(story_core.get("opening_anchor") or "").strip()

    is_generic_env = environment_family.lower() in GENERIC_ENVIRONMENT_FAMILIES or len(environment_family) < 5
    strengthened_environment_family = environment_family
    world_planning_summary = environment_family
    used_strengthened_summary = False

    if is_generic_env:
        summary_parts: list[str] = []
        if realism_contract:
            summary_parts.append(realism_contract)
        if country:
            summary_parts.append(f"{country} setting")
        if location_progression:
            summary_parts.append(f"location flow: {' -> '.join(location_progression[:4])}")
        if style_anchor:
            summary_parts.append(style_anchor)
        if opening_anchor:
            summary_parts.append(f"opening anchor: {opening_anchor}")
        if story_summary:
            summary_parts.append(f"story arc: {story_summary}")
        world_planning_summary = "; ".join(summary_parts)[:700] or "grounded contemporary public-to-private progression"
        strengthened_environment_family = world_planning_summary
        used_strengthened_summary = True

    return (
        {
            "environment_family": environment_family,
            "strengthened_environment_family": strengthened_environment_family[:400],
            "world_planning_summary": world_planning_summary,
            "country_or_region": country,
            "location_progression": location_progression,
            "style_anchor": style_anchor,
            "realism_contract": realism_contract,
        },
        used_strengthened_summary,
    )


def _build_scene_planning_context(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_windows = _build_scene_windows(audio_map)
    world_summary, world_summary_used = _build_scene_world_summary(role_plan, story_core)

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
            "world_summary": world_summary,
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
    aux = {
        "scene_windows": scene_windows,
        "role_lookup": _build_scene_role_lookup(role_plan),
        "world_summary_used": world_summary_used,
    }
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
        "Route spacing policy: ia2v scenes must not be adjacent; spread ia2v as rare emotional accents with at least one non-ia2v between them whenever possible.\\n"
        "first_last scenes should not be adjacent to another first_last unless unavoidable. Keep route rhythm staggered, not paired.\\n"
        "Preserve realism and coherent lighting/world progression from role_plan world continuity.\\n\\n"
        "WATCHABILITY ROLE (MANDATORY): viewer-facing clip function of the scene, not role name.\\n"
        "Each scene.watchability_role must be a short phrase that says why this scene matters to the viewer/clip arc.\\n"
        "Avoid weak labels (hero/main character/character_1/route names/raw scene_function duplicates).\\n\\n"
        "ROUTE SEMANTICS (MANDATORY):\\n"
        "- i2v: baseline clip scene for observation/transit/environment/connective motion.\\n"
        "- ia2v: emotional performance shot, readable face, smooth motion, minimal abrupt full-body action.\\n"
        "- first_last: controlled micro-transition only (near-neighbor A->B states in same world, same location family, same hero, same lighting family, same outfit continuity, same framing family; only one controlled action/state changes).\\n"
        "Choose first_last only for reveal/turn/payoff threshold moments where continuity can hold; avoid first_last for implied location/world/style jumps.\\n\\n"
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


def _is_first_last_candidate(scene: dict[str, Any], idx: int, total: int) -> bool:
    scene_function = str(scene.get("scene_function") or "").strip().lower()
    presence_mode = str(scene.get("scene_presence_mode") or "").strip().lower()
    has_turn = any(hint in scene_function for hint in TURN_FUNCTION_HINTS)
    has_exclusion = any(hint in presence_mode or hint in scene_function for hint in FIRST_LAST_EXCLUSION_HINTS)
    return bool(has_turn and not has_exclusion) or (idx == total - 1 and "release" in scene_function)


def _route_adjacency_penalty(scenes: list[dict[str, Any]], idx: int, route: str) -> int:
    if route not in ALLOWED_ROUTES:
        return 0
    penalty = 0
    for near_idx in (idx - 1, idx + 1):
        if near_idx < 0 or near_idx >= len(scenes):
            continue
        near_route = str(_safe_dict(scenes[near_idx]).get("route") or "")
        if route == "ia2v" and near_route == "ia2v":
            penalty += IA2V_ADJACENCY_PENALTY
        if route == "first_last" and near_route == "first_last":
            penalty += FIRST_LAST_ADJACENCY_PENALTY
    return penalty


def _has_adjacent_route(scenes: list[dict[str, Any]], route_name: str) -> bool:
    route = str(route_name or "")
    for idx in range(1, len(scenes)):
        if str(_safe_dict(scenes[idx - 1]).get("route") or "") == route and str(_safe_dict(scenes[idx]).get("route") or "") == route:
            return True
    return False


def _route_scores(scene: dict[str, Any], idx: int, total: int, *, scenes: list[dict[str, Any]] | None = None) -> dict[str, int]:
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

    if _is_first_last_candidate(scene, idx, total):
        scores["first_last"] += 4
    elif any(hint in scene_function for hint in TURN_FUNCTION_HINTS):
        scores["first_last"] += 1
    if idx == total - 1:
        scores["first_last"] += 2
    if idx == 0:
        scores["i2v"] += 2

    if "release" in scene_function and performance_focus:
        scores["first_last"] += 2
    if scenes:
        for route in ("i2v", "ia2v", "first_last"):
            scores[route] -= _route_adjacency_penalty(scenes, idx, route)

    return scores


def _default_route(scene: dict[str, Any], idx: int, total: int, *, scenes: list[dict[str, Any]] | None = None) -> str:
    scores = _route_scores(scene, idx, total, scenes=scenes)
    return max(("i2v", "ia2v", "first_last"), key=lambda route: (scores[route], route == "i2v"))


def _is_weak_watchability_role(value: str, *, route: str, scene_function: str) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return True
    weak_exact = {
        "character_1",
        "character 1",
        "hero",
        "main character",
        "protagonist",
        "lead",
        "i2v",
        "ia2v",
        "first_last",
    }
    if raw in weak_exact:
        return True
    if raw == str(route or "").strip().lower():
        return True
    fnorm = str(scene_function or "").strip().lower()
    return bool(fnorm and raw == fnorm)


def _infer_watchability_role(scene: dict[str, Any], idx: int, total: int) -> str:
    scene_function = str(scene.get("scene_function") or "").strip().lower()
    presence_mode = str(scene.get("scene_presence_mode") or "").strip().lower()
    route = str(scene.get("route") or "").strip().lower()
    performance_focus = bool(scene.get("performance_focus"))
    is_final = idx == max(total - 1, 0)

    if "environment_anchor" in presence_mode or "environment_anchor" in scene_function:
        return "anchor world and atmosphere"
    if "transit" in presence_mode or "transit" in scene_function:
        return "carry momentum between spaces"
    if ("setup" in scene_function or idx == 0) and "observational" in presence_mode:
        return "establish hero in public world"
    if route == "ia2v" and (performance_focus or any(k in scene_function for k in {"tension", "conflict", "pressure"})):
        return "deepen emotional connection through performance"
    if route == "first_last" and any(k in scene_function for k in {"reveal", "turn", "transform", "transition", "callback"}):
        return "mark visual transformation"
    if "private_release" in presence_mode or "private_release" in scene_function:
        return "deliver cathartic release"
    if is_final and any(k in scene_function for k in {"release", "afterimage", "resolution", "payoff"}):
        return "close arc with emotional payoff"
    if is_final:
        return "close arc with calm payoff"
    if route == "ia2v":
        return "deepen emotional connection through performance"
    if route == "first_last":
        return "mark transition into the next emotional state"
    return "sustain watchable continuity and momentum"


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

        desired = sorted(deficits, key=lambda route: (target.get(route, 0) - cur[route]), reverse=True)[0]
        best_idx = -1
        best_gain = -999
        for idx, row in enumerate(scenes):
            current_route = str(row.get("route") or "")
            if current_route not in surpluses:
                continue
            score = _route_scores(row, idx, total, scenes=scenes)
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

    for _ in range(total * 3):
        if not _has_adjacent_route(scenes, "ia2v"):
            break
        improved = False
        for idx in range(1, total):
            if str(scenes[idx - 1].get("route") or "") != "ia2v" or str(scenes[idx].get("route") or "") != "ia2v":
                continue
            candidate_indices = [idx - 1, idx]
            for target_idx in candidate_indices:
                row = scenes[target_idx]
                current_route = str(row.get("route") or "")
                alternatives = [r for r in ("i2v", "first_last") if cur[r] < target.get(r, 0) or current_route == "ia2v"]
                best_route = ""
                best_gain = -999
                for alt in alternatives:
                    score = _route_scores(row, target_idx, total, scenes=scenes)
                    gain = score.get(alt, 0) - score.get(current_route, 0)
                    gain += _route_adjacency_penalty(scenes, target_idx, current_route) - _route_adjacency_penalty(scenes, target_idx, alt)
                    if gain > best_gain:
                        best_gain = gain
                        best_route = alt
                if best_route:
                    row["route"] = best_route
                    row["route_reason"] = (
                        str(row.get("route_reason") or "").strip() + f" [route_spacing:ia2v_adjacent->{best_route}]"
                    ).strip()
                    changed = True
                    cur = counts()
                    improved = True
                    break
            if improved:
                break
        if not improved:
            break

    return changed


def _normalize_scene_plan(raw_plan: dict[str, Any], *, scene_windows: list[dict[str, Any]], role_lookup: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool, str, int]:
    known_ids = {str(row.get("scene_id") or ""): row for row in scene_windows}
    raw_scenes_by_id: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(raw_plan.get("scenes")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id in known_ids:
            raw_scenes_by_id[scene_id] = row

    used_fallback = False
    watchability_fallback_count = 0
    normalized_scenes: list[dict[str, Any]] = []
    for idx, window in enumerate(scene_windows):
        scene_id = str(window.get("scene_id") or "")
        raw_row = _safe_dict(raw_scenes_by_id.get(scene_id))
        role_row = _safe_dict(role_lookup.get(scene_id))

        route_raw = str(raw_row.get("route") or "").strip().lower()
        route = route_raw if route_raw in ALLOWED_ROUTES else ""
        if not route:
            route = _default_route({**window, **role_row, **raw_row}, idx, len(scene_windows), scenes=normalized_scenes)
            used_fallback = True

        primary_role = str(raw_row.get("primary_role") or role_row.get("primary_role") or "").strip() or None
        active_roles = [
            str(item).strip() for item in _safe_list(raw_row.get("active_roles") or role_row.get("active_roles")) if str(item).strip()
        ]
        if primary_role and primary_role not in active_roles:
            active_roles.insert(0, primary_role)

        scene_presence_mode = str(raw_row.get("scene_presence_mode") or role_row.get("scene_presence_mode") or "solo_observational").strip()

        scene_function = str(raw_row.get("scene_function") or window.get("scene_function") or "montage_progression").strip() or "montage_progression"
        watchability_role_raw = str(raw_row.get("watchability_role") or "").strip()
        scene_row = {
            "scene_id": scene_id,
            "t0": _round3(window.get("t0")),
            "t1": _round3(window.get("t1")),
            "duration_sec": _round3(window.get("duration_sec") or (_round3(window.get("t1")) - _round3(window.get("t0")))),
            "primary_role": primary_role,
            "active_roles": active_roles,
            "scene_presence_mode": scene_presence_mode,
            "scene_function": scene_function,
            "route": route,
            "route_reason": str(raw_row.get("route_reason") or "").strip() or "route_selected_by_policy",
            "emotional_intent": str(raw_row.get("emotional_intent") or "").strip() or "emotionally coherent clip beat",
            "motion_intent": str(raw_row.get("motion_intent") or "").strip() or "watchable realistic movement",
            "watchability_role": watchability_role_raw,
            "performance_focus": bool(role_row.get("performance_focus")),
        }
        if _is_weak_watchability_role(watchability_role_raw, route=route, scene_function=scene_function):
            scene_row["watchability_role"] = _infer_watchability_role(scene_row, idx, len(scene_windows))
            watchability_fallback_count += 1
            used_fallback = True
        normalized_scenes.append(scene_row)

    target_budget = _target_route_budget(len(normalized_scenes))
    if _rebalance_routes(normalized_scenes, target_budget):
        used_fallback = True

    route_counts = {route: sum(1 for row in normalized_scenes if row.get("route") == route) for route in ("i2v", "ia2v", "first_last")}
    has_adjacent_ia2v = _has_adjacent_route(normalized_scenes, "ia2v")
    has_adjacent_first_last = _has_adjacent_route(normalized_scenes, "first_last")
    route_spacing_warning = "adjacent_ia2v_detected" if has_adjacent_ia2v else ("adjacent_first_last_detected" if has_adjacent_first_last else "")

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
        "route_spacing": {
            "has_adjacent_ia2v": has_adjacent_ia2v,
            "has_adjacent_first_last": has_adjacent_first_last,
            "warning": route_spacing_warning,
        },
    }

    validation_error = "" if normalized_scenes else "scene_plan_empty_after_normalization"
    return plan, used_fallback, validation_error, watchability_fallback_count


def build_gemini_scene_plan(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    context, aux = _build_scene_planning_context(package)
    scene_windows = _safe_list(aux.get("scene_windows"))
    role_lookup = _safe_dict(aux.get("role_lookup"))
    world_summary_used = bool(aux.get("world_summary_used"))

    diagnostics = {
        "prompt_version": SCENE_PLAN_PROMPT_VERSION,
        "used_model": SCENE_PLAN_MODEL,
        "scene_count": len(scene_windows),
        "watchability_fallback_count": 0,
        "world_summary_used": world_summary_used,
    }

    if not scene_windows:
        plan, used_fallback, validation_error, watchability_fallback_count = _normalize_scene_plan(
            {},
            scene_windows=scene_windows,
            role_lookup=role_lookup,
        )
        diagnostics.update(
            {
                "route_counts": _safe_dict(_safe_dict(plan.get("route_mix_summary"))),
                "presence_modes": [],
                "route_flat": False,
                "watchability_fallback_count": int(watchability_fallback_count),
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
            model=SCENE_PLAN_MODEL,
            body={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=90,
        )
        if isinstance(response, dict) and response.get("__http_error__"):
            raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

        parsed = _extract_json_obj(_extract_gemini_text(response))
        scene_plan, used_fallback, validation_error, watchability_fallback_count = _normalize_scene_plan(
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
        spacing = _safe_dict(scene_plan.get("route_spacing"))
        diagnostics.update(
            {
                "route_counts": route_counts,
                "presence_modes": presence_modes,
                "route_flat": bool(_safe_list(scene_plan.get("scenes")) and len({r for r, c in route_counts.items() if c > 0}) <= 1),
                "watchability_fallback_count": int(watchability_fallback_count),
                "scene_plan_has_adjacent_ia2v": bool(spacing.get("has_adjacent_ia2v")),
                "scene_plan_has_adjacent_first_last": bool(spacing.get("has_adjacent_first_last")),
                "scene_plan_route_spacing_warning": str(spacing.get("warning") or ""),
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
        scene_plan, used_fallback, validation_error, watchability_fallback_count = _normalize_scene_plan(
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
        spacing = _safe_dict(scene_plan.get("route_spacing"))
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
                "watchability_fallback_count": int(watchability_fallback_count),
                "scene_plan_has_adjacent_ia2v": bool(spacing.get("has_adjacent_ia2v")),
                "scene_plan_has_adjacent_first_last": bool(spacing.get("has_adjacent_first_last")),
                "scene_plan_route_spacing_warning": str(spacing.get("warning") or ""),
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
