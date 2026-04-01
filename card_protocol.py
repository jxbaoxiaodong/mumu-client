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
    "data_portrait_card": "stats_only",
    "emotion_weather_card": "stats_only",
    "home_to_world_card": "timeline_gallery",
    "special_milestone_card": "milestone_story",
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

    elif card_type == "special_milestone_card":
        assets["milestone_name"] = normalized.get("milestone_name") or normalized.get("title", "")
        assets["show_date"] = normalized.get("show_date") or normalized.get("date") or ""

    normalized["assets"] = assets
    return normalized


def normalize_cards(cards: List[Dict]) -> List[Dict]:
    return [normalize_card(card) for card in cards]
