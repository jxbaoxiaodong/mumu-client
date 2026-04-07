#!/usr/bin/env python3
"""
Shared card protocol helpers.

This module keeps card payloads consistent across the local cache and the
server-side database generator while preserving per-type layouts.
"""

from copy import deepcopy
from datetime import datetime
from typing import Dict, List, Any


CARD_LAYOUTS = {
    "tag_collage_card": "tag_collage",
    "comparison_card": "comparison",
    "smile_collection_card": "photo_grid",
    "little_traveler_card": "photo_grid",
    "sleep_evolution_card": "photo_grid",
    "love_hug_card": "photo_grid",
    "companion_stats_card": "stats_only",
    "same_day_different_year_card": "year_compare",
    "four_seasons_wardrobe_card": "season_gallery",
    "sleep_pose_collection_card": "photo_grid",
    "expression_mimic_card": "photo_pair",
    "user_annotation_card": "user_annotation",
    "time_album_card": "album_timeline",
    "generated_comic_card": "generated_comic",
    "virtual_role_card": "virtual_role",
    "story_character_card": "story_profile",
    "story_episode_card": "story_cover",
    "story_quote_card": "quote_spotlight",
    "story_skill_card": "skill_badge",
    "story_weather_card": "story_weather",
    "story_update_card": "story_update",
    "care_guide_card": "care_guide",
    "data_portrait_card": "stats_only",
    "emotion_weather_card": "stats_only",
    "home_to_world_card": "timeline_gallery",
    "collage_card": "tag_collage",
}


def format_card_date(date_str: str) -> str:
    """Format YYYY-MM-DD or YYYY.MM.DD into YY.MM.DD."""
    if not date_str:
        return ""

    text = str(date_str).strip()

    try:
        if "-" in text and len(text) >= 10:
            dt = datetime.strptime(text[:10], "%Y-%m-%d")
            return f"{str(dt.year)[-2:]}.{dt.month:02d}.{dt.day:02d}"
        if "." in text and len(text) >= 8:
            parts = text.split(".")
            if len(parts) >= 3 and len(parts[0]) == 2:
                return text[:8]
    except Exception:
        pass

    return text


def _normalize_photo_item(item: Any) -> Any:
    if isinstance(item, str):
        return item

    if not isinstance(item, dict):
        return item

    normalized = dict(item)
    path = normalized.get("path")
    if isinstance(path, str):
        normalized["path"] = path
    if "date" in normalized and normalized["date"]:
        normalized["date"] = normalized["date"]
    return normalized


def _collect_photo_entries(card: Dict) -> List[Dict]:
    entries: List[Dict] = []

    photos = card.get("photos")
    if isinstance(photos, list):
        for item in photos:
            normalized = _normalize_photo_item(item)
            if isinstance(normalized, dict) and normalized.get("path"):
                entries.append(normalized)

    if entries:
        return entries

    photo_paths = card.get("photo_paths")
    if isinstance(photo_paths, list):
        for path in photo_paths:
            if isinstance(path, str) and path:
                entries.append({"path": path})

    if entries:
        return entries

    for field in ["photo", "before_photo", "after_photo", "early_photo", "recent_photo"]:
        value = card.get(field)
        if isinstance(value, str) and value:
            entries.append({"path": value, "role": field})

    return entries


def normalize_card(card: Dict) -> Dict:
    """Return a normalized copy of a card payload."""
    normalized = deepcopy(card)
    card_type = normalized.get("type") or normalized.get("card_type") or "default_card"
    normalized["type"] = card_type

    if "card_type" not in normalized and card_type:
        normalized["card_type"] = card_type

    if "layout" not in normalized or not normalized.get("layout"):
        normalized["layout"] = CARD_LAYOUTS.get(card_type, "default")

    assets = dict(normalized.get("assets") or {})
    photo_entries = _collect_photo_entries(normalized)

    if photo_entries:
        assets.setdefault("photos", photo_entries)
        normalized.setdefault("photos", photo_entries)
        normalized.setdefault("photo_paths", [p["path"] for p in photo_entries if p.get("path")])

    if card_type == "comparison_card":
        before_path = normalized.get("before_photo")
        after_path = normalized.get("after_photo")
        photo_paths = normalized.get("photo_paths") or []
        if not before_path and len(photo_paths) >= 1:
            before_path = photo_paths[0]
        if not after_path and len(photo_paths) >= 2:
            after_path = photo_paths[1]

        assets["before_photo"] = before_path
        assets["after_photo"] = after_path
        assets["before_date"] = normalized.get("before_date") or normalized.get("date_before") or ""
        assets["after_date"] = normalized.get("after_date") or normalized.get("date_after") or ""
        assets["before_label"] = format_card_date(
            normalized.get("before_date") or normalized.get("date_before") or ""
        )
        assets["after_label"] = format_card_date(
            normalized.get("after_date") or normalized.get("date_after") or ""
        )

    elif card_type == "expression_mimic_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["before_photo"] = photo_paths[0]
        if len(photo_paths) >= 2:
            assets["after_photo"] = photo_paths[1]
        assets["before_date"] = normalized.get("before_date") or ""
        assets["after_date"] = normalized.get("after_date") or ""
        assets["before_label"] = format_card_date(normalized.get("before_date") or "")
        assets["after_label"] = format_card_date(normalized.get("after_date") or "")
        assets["months_diff"] = normalized.get("months_diff")

    elif card_type == "user_annotation_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["photo"] = photo_paths[0]
        assets["tag_text"] = normalized.get("tag_text") or normalized.get("tag") or ""
        assets["note"] = normalized.get("note") or ""
        assets["photo_date"] = normalized.get("photo_date") or ""
        assets["photo_label"] = format_card_date(normalized.get("photo_date") or "")

    elif card_type == "time_album_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = normalized.get("cover_photo") or photo_paths[0]
        assets["timeline_entries"] = normalized.get("timeline_entries") or []
        assets["cover_title"] = normalized.get("cover_title") or ""
        assets["cover_summary"] = normalized.get("cover_summary") or ""
        assets["selected_days"] = normalized.get("selected_days")
        assets["source_days"] = normalized.get("source_days")
        assets["date_span"] = normalized.get("date_span") or ""
        assets["share_url"] = normalized.get("share_url") or "/storybook"

    elif card_type == "generated_comic_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        assets["style_name"] = normalized.get("style_name") or ""
        assets["style_tagline"] = normalized.get("style_tagline") or ""
        assets["style_palette"] = normalized.get("style_palette") or []
        assets["style_theme"] = normalized.get("style_theme") or ""
        assets["frame_shape"] = normalized.get("frame_shape") or "square"
        assets["reason_lines"] = normalized.get("reason_lines") or []
        assets["photo_date"] = normalized.get("photo_date") or ""
        assets["photo_label"] = format_card_date(normalized.get("photo_date") or "")
        assets["scene_label"] = normalized.get("scene_label") or ""
        assets["activity_label"] = normalized.get("activity_label") or ""
        assets["quality_label"] = normalized.get("quality_label") or ""
        assets["narrative_line"] = normalized.get("narrative_line") or ""
        assets["prompt_hint"] = normalized.get("prompt_hint") or ""
        assets["generated_image"] = (
            normalized.get("generated_image")
            or normalized.get("generated_image_url")
            or ""
        )
        assets["source_photo"] = normalized.get("source_photo") or ""
        assets["generation_mode"] = normalized.get("generation_mode") or ""
        assets["custom_prompt"] = normalized.get("custom_prompt") or ""

    elif card_type == "virtual_role_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        assets["generated_image"] = (
            normalized.get("generated_image")
            or normalized.get("generated_image_url")
            or ""
        )
        assets["source_photo"] = normalized.get("source_photo") or ""
        assets["generation_mode"] = normalized.get("generation_mode") or ""
        assets["role_name"] = normalized.get("role_name") or ""
        assets["role_display_name"] = normalized.get("role_display_name") or ""
        assets["role_archetype"] = normalized.get("role_archetype") or ""
        assets["role_subtitle"] = normalized.get("role_subtitle") or ""
        assets["role_traits"] = normalized.get("role_traits") or []
        assets["role_modifiers"] = normalized.get("role_modifiers") or []
        assets["role_palette"] = normalized.get("role_palette") or []
        assets["scene_label"] = normalized.get("scene_label") or ""
        assets["activity_label"] = normalized.get("activity_label") or ""
        assets["emotion_label"] = normalized.get("emotion_label") or ""
        assets["frame_shape"] = normalized.get("frame_shape") or "arch"
        assets["cover_frame_shape"] = normalized.get("cover_frame_shape") or ""
        assets["match_score"] = normalized.get("match_score")

    elif card_type == "story_character_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        assets["role_name"] = normalized.get("role_name") or ""
        assets["hero_archetype"] = normalized.get("hero_archetype") or ""
        assets["world_name"] = normalized.get("world_name") or ""
        assets["catchphrase"] = normalized.get("catchphrase") or ""
        assets["signature_traits"] = normalized.get("signature_traits") or []
        assets["ability_tracks"] = normalized.get("ability_tracks") or []
        assets["gear_tracks"] = normalized.get("gear_tracks") or []
        assets["story_engine"] = normalized.get("story_engine") or ""
        assets["story_motifs"] = normalized.get("story_motifs") or []
        assets["world_rules"] = normalized.get("world_rules") or []

    elif card_type == "story_episode_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        if len(photo_paths) >= 2:
            assets["supporting_photos"] = photo_paths[1:]
        assets["episode_no"] = normalized.get("episode_no")
        assets["episode_date"] = normalized.get("episode_date") or ""
        assets["role_name"] = normalized.get("role_name") or ""
        assets["hero_archetype"] = normalized.get("hero_archetype") or ""
        assets["scene_label"] = normalized.get("scene_label") or ""
        assets["fantasy_mission"] = normalized.get("fantasy_mission") or ""
        assets["skill_progress"] = normalized.get("skill_progress") or ""
        assets["gear_unlock"] = normalized.get("gear_unlock") or ""
        assets["quote"] = normalized.get("quote") or ""
        assets["story_tags"] = normalized.get("story_tags") or []

    elif card_type == "story_quote_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        assets["quote"] = normalized.get("quote") or ""
        assets["episode_date"] = normalized.get("episode_date") or ""
        assets["scene_label"] = normalized.get("scene_label") or ""
        assets["role_name"] = normalized.get("role_name") or ""
        assets["hero_archetype"] = normalized.get("hero_archetype") or ""

    elif card_type == "story_skill_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        assets["skill_name"] = normalized.get("skill_name") or ""
        assets["level"] = normalized.get("level")
        assets["reason"] = normalized.get("reason") or ""
        assets["evidence_count"] = normalized.get("evidence_count")
        assets["role_name"] = normalized.get("role_name") or ""
        assets["hero_archetype"] = normalized.get("hero_archetype") or ""

    elif card_type == "story_weather_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        assets["mood_mix"] = normalized.get("mood_mix") or []
        assets["dominant_emotion"] = normalized.get("dominant_emotion") or ""
        assets["role_name"] = normalized.get("role_name") or ""
        assets["hero_archetype"] = normalized.get("hero_archetype") or ""
        assets["world_name"] = normalized.get("world_name") or ""

    elif card_type == "story_update_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        assets["before_text"] = normalized.get("before_text") or ""
        assets["after_text"] = normalized.get("after_text") or ""
        assets["feedback_hint"] = normalized.get("feedback_hint") or ""
        assets["role_name"] = normalized.get("role_name") or ""
        assets["hero_archetype"] = normalized.get("hero_archetype") or ""

    elif card_type == "care_guide_card":
        photo_paths = normalized.get("photo_paths") or []
        if len(photo_paths) >= 1:
            assets["cover_photo"] = photo_paths[0]
        assets["guide_items"] = normalized.get("guide_items") or []
        assets["role_name"] = normalized.get("role_name") or ""
        assets["hero_archetype"] = normalized.get("hero_archetype") or ""
        assets["scene_label"] = normalized.get("scene_label") or ""

    elif card_type == "same_day_different_year_card":
        year_samples = normalized.get("year_samples") or []
        if isinstance(year_samples, list):
            assets["year_samples"] = year_samples
            if year_samples and "photo" in year_samples[0]:
                normalized.setdefault("photo_paths", [s["photo"]["path"] for s in year_samples if isinstance(s.get("photo"), dict) and s["photo"].get("path")])

    elif card_type == "four_seasons_wardrobe_card":
        seasons = normalized.get("seasons") or []
        if isinstance(seasons, list):
            assets["seasons"] = seasons

    elif card_type == "companion_stats_card":
        if normalized.get("companions"):
            assets["companions"] = normalized["companions"]

    elif card_type in {"data_portrait_card", "emotion_weather_card"}:
        if normalized.get("stats"):
            assets["stats"] = normalized["stats"]

    normalized["assets"] = assets
    return normalized


def normalize_cards(cards: List[Dict]) -> List[Dict]:
    return [normalize_card(card) for card in cards]
