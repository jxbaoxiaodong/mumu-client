#!/usr/bin/env python3
"""
成长记录系统 - 公网客户端最终版
从配置文件或环境变量读取服务端地址
"""

import os
import sys
import json
import io
import uuid
import shutil
import errno
import socket
import ctypes
import logging
import logging.handlers
import tempfile
import hashlib
import hmac
import ipaddress
import atexit
import re
import signal
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time
from pathlib import Path
from flask import (
    Flask,
    render_template,
    request,
    jsonify,
    redirect,
    send_file,
    send_from_directory,
    session,
)
from werkzeug.utils import secure_filename
import requests
import urllib3
from functools import lru_cache, wraps
from urllib.parse import quote, unquote, urlencode
from auth_utils import verify_signature as verify_hmac_signature
from card_protocol import normalize_cards
from help_content import (
    HELP_BOUNDARY_GUIDE,
    HELP_FAQ,
    HELP_TOPICS,
    HELP_TOPIC_ORDER,
    normalize_help_topic,
)
from photo_status import (
    PHOTO_STATUS_BLOCKED,
    PHOTO_STATUS_OK,
    PHOTO_STATUS_OTHER_ERROR,
    classify_terminal_photo_error,
    is_semantic_skip_photo_record,
)

# 抑制特定域名的 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ==================== 日志配置 ====================
def setup_logging():
    """配置客户端日志系统（兼容 PyInstaller 和普通运行）"""
    log_dir = Path.home() / "Documents" / "CZRZ" / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)

    # 创建日志记录器
    logger = logging.getLogger("czrz_client")
    logger.setLevel(logging.INFO)

    # 清除现有处理器
    logger.handlers.clear()

    # 文件处理器 - 按大小轮转，最大5MB，保留3个备份
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "client.log",
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # 格式化器
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# 初始化日志
logger = setup_logging()
logger.info("=" * 60)
logger.info("客户端日志系统初始化完成")


DEFAULT_SERVER_DOMAIN = "xiaohexia.ftir.fun"
HEARTBEAT_INTERVAL_SECONDS = 30

USER_DATA_DIR = Path.home() / "Documents" / "CZRZ"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_ASSETS_DIR = USER_DATA_DIR / "generated_assets"
GENERATED_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_MANUAL_CARD_FILE = GENERATED_ASSETS_DIR / "manual_cards.json"
RUNTIME_WEB_DIR = USER_DATA_DIR / "runtime_web"
RUNTIME_WEB_DIR.mkdir(parents=True, exist_ok=True)
DAILY_STORY_DIR = USER_DATA_DIR / "daily_stories"
DAILY_STORY_DIR.mkdir(parents=True, exist_ok=True)
DEFAULT_STORY_IMAGE_MODEL = "qwen-image-2.0-pro"
DEFAULT_STORY_IMAGE_SIZE = "1024*1024"
DEFAULT_AVATAR_URL = "/api/default-avatar"
DEFAULT_VIDEO_THUMB_URL = "/api/default-video-thumb"
INLINE_AVATAR_PLACEHOLDER = (
    "data:image/svg+xml;charset=UTF-8,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 120'%3E"
    "%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E"
    "%3Cstop offset='0%25' stop-color='%23f8f0e1'/%3E"
    "%3Cstop offset='100%25' stop-color='%23d9e7db'/%3E"
    "%3C/linearGradient%3E%3C/defs%3E"
    "%3Crect width='120' height='120' rx='60' fill='url(%23g)'/%3E"
    "%3Ccircle cx='60' cy='44' r='22' fill='%23ffffff' fill-opacity='0.9'/%3E"
    "%3Cpath d='M30 95c6-18 18-27 30-27s24 9 30 27' fill='%23ffffff' fill-opacity='0.9'/%3E"
    "%3C/svg%3E"
)

USER_CONFIG_FILE = USER_DATA_DIR / "config.json"
INLINE_ASSET_CACHE = {}


def normalize_avatar_url(value: str) -> str:
    avatar_url = (value or "").strip()
    avatar_dir = USER_DATA_DIR / "avatars"

    if avatar_url.startswith("/api/avatar/"):
        filename = avatar_url.rsplit("/", 1)[-1]
        if filename and (avatar_dir / filename).exists():
            return avatar_url

    if avatar_url and not avatar_url.startswith("/api/avatar/"):
        return avatar_url

    if avatar_dir.exists():
        candidates = sorted(
            (p for p in avatar_dir.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return f"/api/avatar/{candidates[0].name}"

    return DEFAULT_AVATAR_URL


def get_inline_asset_text(relative_path: str) -> str:
    """读取并缓存适合内联到模板的静态资源。"""
    asset_path = Path(__file__).parent / relative_path
    if not asset_path.exists():
        return ""

    try:
        mtime_ns = asset_path.stat().st_mtime_ns
    except OSError:
        return ""

    cache_key = str(asset_path)
    cached = INLINE_ASSET_CACHE.get(cache_key)
    if cached and cached.get("mtime_ns") == mtime_ns:
        return cached.get("content", "")

    try:
        content = asset_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"读取内联资源失败 {asset_path}: {e}")
        return ""

    INLINE_ASSET_CACHE[cache_key] = {"mtime_ns": mtime_ns, "content": content}
    return content


def normalize_news_titles(raw_news) -> list[str]:
    """把数据库或接口里的新闻字段统一转成标题列表。"""
    if not raw_news:
        return []

    if isinstance(raw_news, list):
        items = raw_news
    elif isinstance(raw_news, dict):
        items = [raw_news]
    elif isinstance(raw_news, str):
        stripped = raw_news.strip()
        if not stripped:
            return []
        try:
            decoded = json.loads(stripped)
            if isinstance(decoded, list):
                items = decoded
            elif isinstance(decoded, dict):
                items = [decoded]
            else:
                items = [str(decoded)]
        except Exception:
            items = [stripped]
    else:
        items = [raw_news]

    titles = []
    for item in items:
        if isinstance(item, dict):
            title = str(
                item.get("title")
                or item.get("headline")
                or item.get("content")
                or item.get("text")
                or ""
            ).strip()
        else:
            title = str(item or "").strip()
        if title:
            titles.append(title)
    return titles[:3]


def load_user_config():
    default_config = {
        "server_domain": DEFAULT_SERVER_DOMAIN,
        "server_url": f"https://{DEFAULT_SERVER_DOMAIN}",
        "client_id": None,
        "secret_key": None,
        "baby_name": "宝宝",
        "baby_gender": None,
        "baby_birthday": None,
        "user_city": None,
        "log_style": "简练",
        "custom_style": "",
        "media_folders": [],
        "admin_password": None,
        "is_paid": False,
        "subdomain": None,
        "public_url": None,
        "avatar_url": None,
        "client_version": "2.0.0",
    }

    if USER_CONFIG_FILE.exists():
        try:
            with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for key in default_config:
                    if key in saved:
                        default_config[key] = saved[key]
        except Exception as e:
            logger.warning(f"加载用户配置失败: {e}")

    env_domain = os.environ.get("SERVER_DOMAIN")
    if env_domain:
        default_config["server_domain"] = env_domain
        default_config["server_url"] = f"https://{env_domain}"

    return default_config


USER_CONFIG = load_user_config()

TEMPLATE_REMOTE_TIMEOUT = 2
DATE_API_REMOTE_TIMEOUT = 4
TEMPLATE_QUOTA_CACHE_TTL = 60
TEMPLATE_QUOTA_CACHE = {
    "expires_at": 0.0,
    "data": None,
}
ALBUM_COLLECTION_CACHE_TTL = 180
ALBUM_COLLECTION_CACHE = {}
ALBUM_COLLECTION_CACHE_LOCK = threading.Lock()
VIRTUAL_ROLE_GEN_LOCK = threading.Lock()
VIRTUAL_ROLE_PM_CACHE = {"media_folders": None, "manager": None}
ALBUM_INDEX_CACHE = {
    "data_dir": None,
    "index_manager": None,
    "index_mtime_ns": 0,
}


def save_user_config():
    try:
        with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(USER_CONFIG, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存用户配置失败: {e}")


def get_ai_identity_client_id(default: str = "") -> str:
    """健康 AI 使用的唯一身份 ID：统一回到 client_id。"""
    client_id = USER_CONFIG.get("client_id") or ""
    client = globals().get("public_client")
    if not client_id and client is not None:
        client_id = getattr(client, "client_id", "") or ""
    return client_id or default


def _story_base_dir(client_id: str = None) -> Path:
    cid = client_id or getattr(public_client, "client_id", None) or "default"
    base_dir = DAILY_STORY_DIR / cid
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _story_dir(date_str: str, client_id: str = None) -> Path:
    story_dir = _story_base_dir(client_id) / date_str
    story_dir.mkdir(parents=True, exist_ok=True)
    return story_dir


def _story_series_seed_file(client_id: str = None) -> Path:
    return _story_base_dir(client_id) / "series_profile_seed.json"


def _story_meta_file(date_str: str, client_id: str = None) -> Path:
    return _story_dir(date_str, client_id) / "story.json"


def _story_asset_url(date_str: str, filename: str) -> str:
    return f"/story-assets/{quote(date_str)}/{quote(filename)}"


def _generated_asset_dir(category: str) -> Path:
    safe_category = secure_filename(category or "misc") or "misc"
    target = GENERATED_ASSETS_DIR / safe_category
    target.mkdir(parents=True, exist_ok=True)
    return target


def _generated_asset_url(category: str, filename: str) -> str:
    return f"/generated-assets/{quote(category)}/{quote(filename)}"


@lru_cache(maxsize=1)
def _get_comic_style_library() -> tuple:
    try:
        from server_card_generator import ServerCardGenerator

        return tuple(dict(item) for item in ServerCardGenerator.ART_STYLE_LIBRARY)
    except Exception as e:
        logger.warning(f"[Comic] 加载风格库失败: {e}")
        return tuple()


def get_comic_style_presets() -> list:
    presets = [
        {
            "key": "auto",
            "name": "自动匹配",
            "tagline": "根据照片气质自动选最合适的画法",
            "palette": ["#f6c36a", "#93c9b7", "#fff8ef"],
        }
    ]
    for item in _get_comic_style_library():
        presets.append(
            {
                "key": item.get("theme") or item.get("name") or "style",
                "name": item.get("name") or "艺术风格",
                "tagline": item.get("tagline") or "",
                "palette": list(item.get("palette") or []),
            }
        )
    return presets


def _read_local_manual_card_store() -> list:
    if not LOCAL_MANUAL_CARD_FILE.exists():
        return []
    try:
        payload = json.loads(LOCAL_MANUAL_CARD_FILE.read_text(encoding="utf-8"))
        raw_cards = payload.get("cards", []) if isinstance(payload, dict) else payload
        return normalize_cards(raw_cards or [])
    except Exception as e:
        logger.warning(f"[Comic] 读取本地手动漫画卡失败: {e}")
        return []


def _write_local_manual_card_store(cards: list) -> None:
    try:
        LOCAL_MANUAL_CARD_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cards": normalize_cards(cards or []),
            "updated_at": datetime.now().isoformat(),
        }
        LOCAL_MANUAL_CARD_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning(f"[Comic] 写入本地手动漫画卡失败: {e}")


def load_local_manual_cards(card_type: str = "") -> list:
    cards = _read_local_manual_card_store()
    if card_type:
        return [card for card in cards if (card.get("type") or "") == card_type]
    return cards


def append_local_manual_cards(new_cards: list, keep: int = 48) -> list:
    existing = _read_local_manual_card_store()
    merged = []
    seen_ids = set()
    for card in list(new_cards or []) + list(existing or []):
        card_id = str(card.get("id") or card.get("card_id") or "").strip()
        if not card_id or card_id in seen_ids:
            continue
        seen_ids.add(card_id)
        merged.append(card)

    merged.sort(
        key=lambda item: str(item.get("generated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    merged = merged[: max(1, keep)]
    _write_local_manual_card_store(merged)
    return merged


def _get_cached_photo_manager():
    media_folders = tuple(
        str(Path(folder))
        for folder in (getattr(public_client, "media_folders", []) or [])
        if str(folder or "").strip() and Path(folder).exists()
    )
    if not media_folders:
        return None

    cached_folders = VIRTUAL_ROLE_PM_CACHE.get("media_folders")
    cached_manager = VIRTUAL_ROLE_PM_CACHE.get("manager")
    if cached_manager is not None and cached_folders == media_folders:
        return cached_manager

    try:
        from photo_manager import PhotoManager

        manager = PhotoManager(list(media_folders), public_client.data_dir)
    except Exception as e:
        logger.warning(f"[VirtualRole] 初始化 PhotoManager 失败: {e}")
        return None

    VIRTUAL_ROLE_PM_CACHE["media_folders"] = media_folders
    VIRTUAL_ROLE_PM_CACHE["manager"] = manager
    return manager


def _extract_card_photo_filename(candidate) -> str:
    text = str(candidate or "").strip()
    if not text:
        return ""
    if text.startswith("/photo/thumb/"):
        return Path(unquote(text.split("/photo/thumb/", 1)[1].split("?", 1)[0])).name
    if text.startswith("/photo/"):
        return Path(unquote(text.split("/photo/", 1)[1].split("?", 1)[0])).name
    return Path(unquote(text)).name


def _generated_asset_local_path(asset_url: str) -> Path | None:
    text = str(asset_url or "").strip()
    prefix = "/generated-assets/"
    if not text.startswith(prefix):
        return None
    return GENERATED_ASSETS_DIR / unquote(text[len(prefix) :])


def _resolve_virtual_role_source_record(card: dict) -> dict | None:
    assets = (card or {}).get("assets") or {}
    candidates = [
        assets.get("source_photo"),
        card.get("source_photo"),
        assets.get("cover_photo"),
        card.get("cover_photo"),
    ]
    candidates.extend((card or {}).get("photo_paths") or [])

    photo_manager = _get_cached_photo_manager()
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text or text.startswith("/generated-assets/"):
            continue
        path_obj = Path(text)
        if path_obj.exists():
            return {
                "path": str(path_obj),
                "filename": path_obj.name,
                "date": (card or {}).get("photo_date") or "",
            }

        filename = _extract_card_photo_filename(text)
        if not filename or photo_manager is None:
            continue
        entry = photo_manager.get_photo_by_filename(filename)
        if entry and entry.get("path") and Path(entry["path"]).exists():
            return entry
    return None


def _get_virtual_role_visual_hint(role_name: str) -> str:
    hints = {
        "小徐霞客": "travel diary illustration, gentle explorer outfit accents, map fragments, long road, breeze, mountain light",
        "小诸葛亮": "warm strategist portrait, bamboo scroll mood, layered study light, calm and observant energy",
        "小达芬奇": "inventive sketchbook atmosphere, handmade workshop details, curious tools, creative child inventor vibe",
        "小木兰": "soft heroic portrait, light armor accents, wind and open field, gentle but brave momentum",
        "小李白": "poetic dreamlike scene, moonlit breeze, lyrical brushwork, soft romantic atmosphere",
        "小梅兰芳": "stage-inspired portrait, elegant costume accents, refined gesture, theatrical light",
        "小居里": "quiet research portrait, soft scientific glow, curious instruments, calm focus",
    }
    return hints.get(str(role_name or "").strip(), "editorial character illustration, cinematic child portrait, refined environment storytelling")


def _build_virtual_role_image_prompt(card: dict, source_record: dict) -> str:
    baby_name = getattr(public_client, "baby_name", "宝宝")
    assets = (card or {}).get("assets") or {}
    role_name = assets.get("role_name") or card.get("role_name") or "虚拟角色"
    role_display_name = assets.get("role_display_name") or card.get("role_display_name") or role_name
    role_archetype = assets.get("role_archetype") or card.get("role_archetype") or role_name
    role_subtitle = assets.get("role_subtitle") or card.get("role_subtitle") or ""
    role_traits = assets.get("role_traits") or card.get("role_traits") or []
    role_modifiers = assets.get("role_modifiers") or card.get("role_modifiers") or []
    scene_label = assets.get("scene_label") or card.get("scene_label") or source_record.get("scene") or ""
    activity_label = assets.get("activity_label") or card.get("activity_label") or source_record.get("activity") or ""
    emotion_label = assets.get("emotion_label") or card.get("emotion_label") or ""
    description = (source_record.get("description") or "").strip()

    style_prompt = ""
    try:
        from server_card_generator import ServerCardGenerator

        style, _reasons = ServerCardGenerator()._match_art_style(source_record or {})
        style_prompt = style.get("prompt") or ""
    except Exception as e:
        logger.warning(f"[VirtualRole] 读取角色画风失败: {e}")

    prompt = (
        f"请参考这张宝宝真实照片，创作一张适合家长分享的单幅角色肖像海报。"
        f"主角固定为{baby_name}，必须保留宝宝本人五官特征、发型、体态、年龄感、衣服主色和真实场景线索，不能换脸，不能变成陌生孩子。"
        f"把她自然映射成“{role_archetype}”这一类人物气质，让人一眼看出还是{baby_name}本人，同时又能联想到这个角色。"
        f"允许加入少量与角色气质相符的服饰、道具或环境元素，但不要做夸张 cosplay。"
    )
    if role_display_name and role_display_name != role_archetype:
        prompt += f" 当前这张角色卡的展示名是：{role_display_name}。"
    if role_subtitle:
        prompt += f" 角色气质：{role_subtitle}。"
    if role_traits:
        prompt += f" 角色识别点：{'、'.join(str(item).strip() for item in role_traits[:4] if str(item).strip())}。"
    if role_modifiers:
        prompt += f" 复合特征：{'、'.join(str(item).strip() for item in role_modifiers[:4] if str(item).strip())}。"
    if scene_label:
        prompt += f" 当前场景线索：{scene_label}。"
    if activity_label:
        prompt += f" 当前动作主线：{activity_label}。"
    if emotion_label:
        prompt += f" 当前情绪底色：{emotion_label}。"
    if description:
        prompt += f" 真实素材摘要：{description}。"
    if style_prompt:
        prompt += f" 画面笔触与光线参考：{style_prompt}。"
    prompt += f" 视觉氛围可参考：{_get_virtual_role_visual_hint(role_archetype)}。"
    prompt += (
        " 只输出画面，不要出现任何中文、英文、数字、对白气泡、标题、logo、水印。"
        " 画面要温暖、有层次、有分享欲，像一张精心设计的角色海报。"
    )
    return prompt


def ensure_virtual_role_card_images(cards: list) -> bool:
    target_cards = [
        card for card in (cards or []) if (card.get("type") or card.get("card_type")) == "virtual_role_card"
    ]
    if not target_cards:
        return False

    changed = False
    with VIRTUAL_ROLE_GEN_LOCK:
        for card in target_cards:
            assets = card.setdefault("assets", {}) if isinstance(card, dict) else {}
            existing_image = (
                assets.get("generated_image")
                or card.get("generated_image")
                or card.get("generated_image_url")
                or ""
            )
            local_generated_path = _generated_asset_local_path(existing_image)
            if existing_image and (local_generated_path is None or local_generated_path.exists()):
                continue

            source_record = _resolve_virtual_role_source_record(card)
            if not source_record or not source_record.get("path"):
                continue

            prompt = _build_virtual_role_image_prompt(card, source_record)
            remote_url = _generate_image_with_reference(
                prompt,
                source_record["path"],
                operation="photo_to_comic",
                model_name=DEFAULT_STORY_IMAGE_MODEL,
                size=DEFAULT_STORY_IMAGE_SIZE,
            )
            if not remote_url:
                logger.warning("[VirtualRole] 角色形象图生成失败: 返回空结果")
                continue

            role_name = assets.get("role_name") or card.get("role_name") or "role"
            filename_seed = f"{role_name}|{source_record.get('path')}"
            filename = (
                f"virtual_role_{datetime.now().strftime('%Y%m%d%H%M%S')}_"
                f"{hashlib.md5(filename_seed.encode('utf-8')).hexdigest()[:8]}.jpg"
            )
            target_path = _generated_asset_dir("virtual_roles") / filename
            if not _save_generated_asset_image(remote_url, target_path):
                logger.warning("[VirtualRole] 角色形象图保存失败")
                continue

            source_filename = source_record.get("filename") or Path(source_record["path"]).name
            generated_url = _generated_asset_url("virtual_roles", filename)
            source_url = f"/photo/{quote(source_filename)}"
            card["generated_image"] = generated_url
            card["source_photo"] = source_url
            card["generation_mode"] = "auto"
            card["generation_model"] = DEFAULT_STORY_IMAGE_MODEL
            assets["generated_image"] = generated_url
            assets["source_photo"] = source_url
            assets["generation_mode"] = "auto"
            changed = True
            logger.info(f"[VirtualRole] 已生成角色形象图: {role_name} <- {source_filename}")
    return changed


def _extract_json_payload(text: str):
    if not text:
        return None

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)

    try:
        return json.loads(stripped)
    except Exception:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except Exception:
            return None
    return None


def _story_load_series_seed(client_id: str = None) -> dict:
    path = _story_series_seed_file(client_id)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    hero_archetype = _story_trim_text(payload.get("hero_archetype") or payload.get("title"), 18)
    world_name = _story_trim_text(payload.get("world_name") or payload.get("world"), 16)
    if not hero_archetype or not world_name:
        return {}
    return {
        "id": payload.get("id") or "series_seed",
        "title": hero_archetype,
        "world": world_name,
        "core": _story_trim_text(payload.get("core", ""), 48),
        "motifs": _story_dedup_texts(payload.get("motifs", []), limit=4, max_len=12),
        "signature_line": _story_trim_text(payload.get("signature_line", ""), 34),
        "catchphrase": _story_trim_text(payload.get("catchphrase", ""), 48),
    }


def _story_save_series_seed(client_id: str, seed_profile: dict) -> None:
    payload = {
        "id": seed_profile.get("id") or "series_seed",
        "hero_archetype": _story_trim_text(
            seed_profile.get("hero_archetype") or seed_profile.get("title"), 18
        ),
        "world_name": _story_trim_text(
            seed_profile.get("world_name") or seed_profile.get("world"), 16
        ),
        "core": _story_trim_text(seed_profile.get("core", ""), 48),
        "signature_line": _story_trim_text(seed_profile.get("signature_line", ""), 34),
        "catchphrase": _story_trim_text(seed_profile.get("catchphrase", ""), 48),
        "motifs": _story_dedup_texts(seed_profile.get("motifs", []), limit=4, max_len=12),
    }
    if not payload["hero_archetype"] or not payload["world_name"]:
        return
    try:
        _story_series_seed_file(client_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.info(f"[Story] 保存角色种子失败: {e}")


def _story_panel_image_url(date_str: str, panel: dict) -> str:
    image_file = panel.get("image_file") or ""
    if image_file:
        return _story_asset_url(date_str, image_file)

    source_filename = panel.get("source_filename") or ""
    if source_filename:
        if panel.get("is_video"):
            return f"/video/thumb/{quote(source_filename)}"
        return f"/photo/thumb/{quote(source_filename)}"

    return ""


def _normalize_story_payload(story: dict) -> dict:
    if not story:
        return None

    normalized = dict(story)
    date_str = normalized.get("date", "")
    panels = []
    for index, panel in enumerate(normalized.get("panels", []) or []):
        p = dict(panel or {})
        p.setdefault("index", index + 1)
        p["image_url"] = _story_panel_image_url(date_str, p)
        panels.append(p)
    normalized["panels"] = panels
    return normalized


def load_daily_story(date_str: str, client_id: str = None):
    meta_file = _story_meta_file(date_str, client_id)
    if not meta_file.exists():
        return None

    try:
        with open(meta_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_story_payload(data)
    except Exception as e:
        logger.warning(f"[Story] 读取故事缓存失败 {date_str}: {e}")
        return None


def save_daily_story(date_str: str, story: dict, client_id: str = None):
    story_dir = _story_dir(date_str, client_id)
    story = dict(story or {})
    story["date"] = date_str
    story["updated_at"] = datetime.now().isoformat()
    meta_file = story_dir / "story.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(story, f, ensure_ascii=False, indent=2)
    return _normalize_story_payload(story)


STORY_JOB_LOCK = threading.Lock()
STORY_GENERATION_JOBS = {}
STORY_JOB_RETENTION_SECONDS = 6 * 60 * 60


def _story_job_key(date_str: str, client_id: str = None) -> str:
    target_client = client_id or getattr(public_client, "client_id", None) or "default"
    return f"{target_client}:{date_str}"


def _prune_story_jobs():
    now_ts = time.time()
    expired = []
    with STORY_JOB_LOCK:
        for key, job in STORY_GENERATION_JOBS.items():
            updated_ts = job.get("updated_ts", now_ts)
            if now_ts - updated_ts > STORY_JOB_RETENTION_SECONDS:
                expired.append(key)
        for key in expired:
            STORY_GENERATION_JOBS.pop(key, None)


def _set_story_job(date_str: str, client_id: str = None, **updates) -> dict:
    key = _story_job_key(date_str, client_id)
    now_iso = datetime.now().isoformat()
    now_ts = time.time()
    with STORY_JOB_LOCK:
        job = dict(STORY_GENERATION_JOBS.get(key) or {})
        if not job:
            job = {
                "date": date_str,
                "client_id": client_id or getattr(public_client, "client_id", None),
                "status": "idle",
                "stage": "idle",
                "message": "",
                "progress": 0,
                "started_at": now_iso,
            }
        job.update(updates)
        job["date"] = date_str
        job["updated_at"] = now_iso
        job["updated_ts"] = now_ts
        STORY_GENERATION_JOBS[key] = job
        return dict(job)


def get_story_job(date_str: str, client_id: str = None) -> dict:
    _prune_story_jobs()
    key = _story_job_key(date_str, client_id)
    with STORY_JOB_LOCK:
        job = STORY_GENERATION_JOBS.get(key)
        return dict(job) if job else None


def _story_job_progress_callback(date_str: str, client_id: str = None):
    def callback(stage: str, message: str, progress: int = None):
        payload = {
            "status": "running",
            "stage": stage,
            "message": message,
        }
        if progress is not None:
            payload["progress"] = progress
        _set_story_job(date_str, client_id, **payload)

    return callback


def _run_story_generation_job(date_str: str, regenerate: bool = False, client_id: str = None):
    target_client = client_id or getattr(public_client, "client_id", None)
    try:
        _set_story_job(
            date_str,
            target_client,
            status="running",
            stage="preparing",
            message="正在准备故事素材...",
            progress=5,
            regenerate=bool(regenerate),
            started_at=datetime.now().isoformat(),
            finished_at=None,
        )
        story = build_daily_story(
            date_str,
            regenerate=regenerate,
            progress_callback=_story_job_progress_callback(date_str, target_client),
        )
        _set_story_job(
            date_str,
            target_client,
            status="success",
            stage="done",
            message="故事生成完成",
            progress=100,
            finished_at=datetime.now().isoformat(),
            source_mode=story.get("source_mode"),
            panel_count=len(story.get("panels") or []),
            updated_story_at=story.get("updated_at"),
        )
    except Exception as e:
        logger.error(f"[StoryJob] 后台生成失败 {date_str}: {e}")
        _set_story_job(
            date_str,
            target_client,
            status="error",
            stage="error",
            message=str(e),
            finished_at=datetime.now().isoformat(),
        )


def ensure_story_generation_job(date_str: str, regenerate: bool = False, client_id: str = None):
    target_client = client_id or getattr(public_client, "client_id", None)
    if not regenerate:
        cached_story = load_daily_story(date_str, target_client)
        if cached_story:
            job = _set_story_job(
                date_str,
                target_client,
                status="success",
                stage="done",
                message="故事已存在",
                progress=100,
                finished_at=datetime.now().isoformat(),
                source_mode=cached_story.get("source_mode"),
                panel_count=len(cached_story.get("panels") or []),
                updated_story_at=cached_story.get("updated_at"),
            )
            return job, False

    existing = get_story_job(date_str, target_client)
    if existing and existing.get("status") in {"queued", "running"}:
        return existing, False

    queued_job = _set_story_job(
        date_str,
        target_client,
        status="queued",
        stage="queued",
        message="已开始生成故事任务",
        progress=0,
        regenerate=bool(regenerate),
        started_at=datetime.now().isoformat(),
        finished_at=None,
    )
    thread = threading.Thread(
        target=_run_story_generation_job,
        args=(date_str, regenerate, target_client),
        daemon=True,
    )
    thread.start()
    return queued_job, True


INSTALL_KEY_SEED = "czrz-installer-v2"


def get_install_key(date_str=None):
    """生成安装密钥（与服务端算法相同）"""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    combined = f"{INSTALL_KEY_SEED}:{date_str}"
    hash_val = hashlib.md5(combined.encode()).hexdigest()
    return f"{hash_val[:4]}-{hash_val[4:8]}-{hash_val[8:12]}"


def create_signature(secret_key, method, path, timestamp, body=""):
    """创建 HMAC 签名"""
    message = f"{timestamp}{method.upper()}{path}{body}"
    signature = hmac.new(
        secret_key.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return signature


def add_signature_headers(headers, client_id, secret_key, method, path, body=""):
    """添加签名相关请求头"""
    timestamp = int(time.time())
    signature = create_signature(secret_key, method, path, timestamp, body)
    headers["X-Client-ID"] = client_id
    headers["X-Timestamp"] = str(timestamp)
    headers["X-Signature"] = signature
    return headers


def update_server_domain(new_domain: str):
    USER_CONFIG["server_domain"] = new_domain
    USER_CONFIG["server_url"] = f"https://{new_domain}"
    save_user_config()
    logger.info(f"服务端域名已更新: {new_domain}")


class ClientConfig:
    def __init__(self):
        self._data = {
            "server": {
                "domain": USER_CONFIG["server_domain"],
                "url": USER_CONFIG["server_url"],
            },
            "ai_service": {
                "enabled": True,
                "url": USER_CONFIG["server_url"],
            },
            "local": {
                "port": int(os.environ.get("CLIENT_PORT", "3000")),
                "auto_open_browser": True,
            },
            "features": {
                "enable_cloudflare_tunnel": True,
                "enable_auto_update": True,
            },
        }

    def get(self, key, default=None):
        if key == "ai_service":
            result = self._data.get("ai_service", {}).copy()
            result["child_id"] = get_ai_identity_client_id()
            return result
        return self._data.get(key, default)

    def __getitem__(self, key):
        result = self.get(key)
        if result is None:
            raise KeyError(key)
        return result


CLIENT_CONFIG = ClientConfig()


def get_family_code() -> str:
    """家庭码（复用 setup 的管理密码）"""
    code = ""
    try:
        if public_client and getattr(public_client, "admin_password", None):
            code = public_client.admin_password
    except Exception:
        code = ""
    if not code:
        code = USER_CONFIG.get("admin_password", "") or ""
    return str(code).strip()


def is_family_code_verified() -> bool:
    """当前会话是否已通过家庭码校验"""
    return bool(session.get("family_code_verified"))


def mark_family_code_verified() -> None:
    """标记会话已完成家庭码验证"""
    session["family_code_verified"] = True
    session.permanent = True


def verify_family_code(candidate: str) -> bool:
    """校验家庭码"""
    stored = get_family_code()
    if not stored:
        return True
    return str(candidate or "").strip() == stored


def _build_family_gate_response():
    """返回统一的家庭码拦截响应（API 用）"""
    next_path = request.full_path if request.query_string else request.path
    if next_path.endswith("?"):
        next_path = next_path[:-1]
    redirect_url = f"/family-access?next={quote(next_path or '/', safe='')}"
    return (
        jsonify(
            {
                "success": False,
                "message": "请先输入家庭码",
                "require_family_code": True,
                "redirect_url": redirect_url,
            }
        ),
        401,
    )


def require_local_or_password(f):
    """
    权限控制装饰器（统一为家庭码会话）：
    - 局域网/本机：免验证
    - 公网：首次输入家庭码，之后会话内与局域网一致
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        source_ip = get_request_source_ip()
        if _is_private_or_loopback_ip(source_ip):
            return f(*args, **kwargs)

        if is_family_code_verified():
            return f(*args, **kwargs)

        return _build_family_gate_response()

    return decorated_function


def is_server_signed_request() -> bool:
    """允许服务端使用客户端 secret_key 远程触发少量内部接口。"""
    secret_key = USER_CONFIG.get("secret_key") or getattr(
        globals().get("public_client"), "secret_key", ""
    )
    client_id = USER_CONFIG.get("client_id") or getattr(
        globals().get("public_client"), "client_id", ""
    )
    provided_client_id = (request.headers.get("X-Client-ID") or "").strip()
    provided_signature = (request.headers.get("X-Signature") or "").strip()
    timestamp_str = (request.headers.get("X-Timestamp") or "").strip()

    if not (secret_key and client_id and provided_client_id and provided_signature and timestamp_str):
        return False
    if provided_client_id != client_id:
        return False

    try:
        timestamp = int(timestamp_str)
    except Exception:
        return False

    path = request.path
    if request.query_string:
        path = f"{path}?{request.query_string.decode('utf-8', errors='ignore')}"

    body = ""
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        body = request.get_data(cache=True, as_text=True) or ""

    result = verify_hmac_signature(
        secret_key=secret_key,
        provided_signature=provided_signature,
        method=request.method,
        path=path,
        timestamp=timestamp,
        body=body,
    )
    if not result.get("valid"):
        logger.warning(f"[SECURITY] 服务端签名验证失败: {result.get('error')}")
        return False
    return True


def require_local_password_or_signature(f):
    """局域网/家庭码会话或服务端签名二选一。"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if is_server_signed_request():
            return f(*args, **kwargs)
        return require_local_or_password(f)(*args, **kwargs)

    return decorated_function


def is_local_request():
    """检查是否为本地请求"""
    source_ip = get_request_source_ip()
    return _is_private_or_loopback_ip(source_ip)


def is_loopback_request():
    """仅允许真正的本机回环访问。"""
    source_ip = get_request_source_ip()
    try:
        return ipaddress.ip_address(source_ip).is_loopback
    except Exception:
        return source_ip in {"127.0.0.1", "::1", "localhost"}


def _is_private_or_loopback_ip(ip_text: str) -> bool:
    if not ip_text:
        return False
    try:
        ip_obj = ipaddress.ip_address(ip_text.strip())
        return ip_obj.is_private or ip_obj.is_loopback
    except Exception:
        return False


def get_request_source_ip():
    """
    获取请求真实来源IP。
    - 直连请求：使用 request.remote_addr
    - 本机反向代理/隧道转发：仅当 remote_addr 为回环时，信任转发头
    """
    remote_addr = (request.remote_addr or "").strip()
    if _is_private_or_loopback_ip(remote_addr):
        cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
        if cf_ip:
            return cf_ip
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return xff.split(",")[0].strip()
    return remote_addr


def post_with_retry(session, url, json_data, max_retries=3, timeout=30):
    """带重试的POST请求"""
    for attempt in range(max_retries):
        try:
            response = session.post(url, json=json_data, timeout=timeout)
            if response.status_code < 500:
                return response
            print(
                f"[WARN] 服务端返回{response.status_code}，重试 {attempt + 1}/{max_retries}"
            )
        except Exception as e:
            print(f"[WARN] 请求失败: {e}，重试 {attempt + 1}/{max_retries}")
        if attempt < max_retries - 1:
            time.sleep(1)
    return None


class PublicClient:
    """公网客户端"""

    def __init__(self, auto_connect=True):
        self.server_domain = USER_CONFIG["server_domain"]
        self.server_url = USER_CONFIG["server_url"]
        self.client_id = USER_CONFIG["client_id"]
        self.secret_key = USER_CONFIG.get("secret_key")
        self.baby_name = USER_CONFIG["baby_name"]
        self.baby_gender = USER_CONFIG.get("baby_gender")
        self.baby_birthday = USER_CONFIG.get("baby_birthday")
        self.user_city = USER_CONFIG.get("user_city")
        self.log_style = USER_CONFIG.get("log_style", "简练")
        self.custom_style = USER_CONFIG.get("custom_style", "")
        self.media_folders = USER_CONFIG.get("media_folders") or []
        self.admin_password = USER_CONFIG.get("admin_password")
        self.is_paid = USER_CONFIG.get("is_paid", False)
        self.subdomain = USER_CONFIG.get("subdomain")
        self.public_url = USER_CONFIG.get("public_url")
        self.avatar_url = normalize_avatar_url(USER_CONFIG.get("avatar_url"))
        if USER_CONFIG.get("avatar_url") != self.avatar_url:
            USER_CONFIG["avatar_url"] = self.avatar_url
            save_user_config()
        self.client_version = USER_CONFIG.get("client_version", "2.0.0")
        self.index_after_date = USER_CONFIG.get("index_after_date", "")
        self.tunnel_active = False
        self.client_port = CLIENT_CONFIG["local"]["port"]
        self.tunnel_process = None
        self._pending_tunnel_credentials = None  # 待启动的 Tunnel 凭证
        self._tunnel_credentials = None  # 保存凭证用于重启
        self._last_tunnel_check = 0  # 上次检查 tunnel 的时间
        self.data_dir = USER_DATA_DIR

        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "CZRZ-Client/2.0", "Accept": "application/json"}
        )
        self.session.trust_env = False

        logger.info(
            f"客户端初始化: baby_name={self.baby_name}, client_id={self.client_id}"
        )

        if auto_connect and not self.is_first_run():
            self.connect_to_public_server(background_subdomain=True)

    def is_first_run(self) -> bool:
        """检查是否首次运行

        只检查 baby_name 和 media_folders，不检查 client_id
        因为 client_id 可能在注册时因服务端未就绪而未保存
        """
        return (
            not USER_CONFIG_FILE.exists()
            or not self.baby_name
            or self.baby_name == "宝宝"
            or not self.media_folders
        )

    def save_config(self):
        USER_CONFIG["client_id"] = self.client_id
        USER_CONFIG["secret_key"] = self.secret_key
        USER_CONFIG["baby_name"] = self.baby_name
        USER_CONFIG["baby_gender"] = self.baby_gender
        USER_CONFIG["baby_birthday"] = self.baby_birthday
        USER_CONFIG["user_city"] = self.user_city
        USER_CONFIG["log_style"] = self.log_style
        USER_CONFIG["custom_style"] = self.custom_style
        USER_CONFIG["media_folders"] = self.media_folders
        USER_CONFIG["admin_password"] = self.admin_password
        USER_CONFIG["is_paid"] = self.is_paid
        USER_CONFIG["subdomain"] = self.subdomain
        USER_CONFIG["public_url"] = self.public_url
        USER_CONFIG["server_domain"] = self.server_domain
        USER_CONFIG["server_url"] = self.server_url
        USER_CONFIG["avatar_url"] = self.avatar_url
        USER_CONFIG["client_version"] = self.client_version
        USER_CONFIG["index_after_date"] = getattr(self, "index_after_date", "")
        save_user_config()

    def signed_request(self, method, url, **kwargs):
        """发送带签名的请求"""
        from urllib.parse import urlparse, urlencode, quote

        headers = kwargs.pop("headers", {})
        json_data = kwargs.get("json")
        params = kwargs.get("params")
        body = json.dumps(json_data) if json_data else ""
        request_path = url

        if self.client_id and self.secret_key:
            # 构建完整路径（包括 query string），确保 URL 编码一致
            path = url.replace(self.server_url, "")

            # 解析 URL 并重新编码，确保签名和服务端一致
            parsed = urlparse(path)
            if parsed.query:
                # 重新编码 query string，确保中文字符被正确编码
                query_params = {}
                for param in parsed.query.split("&"):
                    if "=" in param:
                        key, value = param.split("=", 1)
                        query_params[key] = value
                # 使用 quote 对值进行编码，保持与服务端一致
                query_string = "&".join(
                    f"{k}={quote(str(v), safe='')}" for k, v in query_params.items()
                )
                path = f"{parsed.path}?{query_string}"
            elif params:
                query_string = urlencode(params)
                path = f"{path}?{query_string}"

            add_signature_headers(
                headers, self.client_id, self.secret_key, method, path, body
            )
            request_path = path

        kwargs["headers"] = headers
        kwargs.setdefault("verify", False)
        method_upper = method.upper()

        try:
            if method_upper == "GET":
                response = self.session.get(url, **kwargs)
            elif method_upper == "POST":
                response = self.session.post(url, **kwargs)
            elif method_upper == "PUT":
                response = self.session.put(url, **kwargs)
            elif method_upper == "DELETE":
                response = self.session.delete(url, **kwargs)
            else:
                response = self.session.request(method, url, **kwargs)
        except requests.exceptions.Timeout as e:
            reporter = globals().get("report_error")
            if callable(reporter):
                reporter(
                    "request_timeout",
                    f"{method_upper} {request_path} 请求超时",
                    str(e),
                )
            raise

        if response.status_code == 401:
            detail = ""
            try:
                detail = (response.text or "")[:200]
            except Exception:
                detail = ""
            reporter = globals().get("report_error")
            if callable(reporter):
                reporter(
                    "signature_401",
                    f"{method_upper} {request_path} 返回401",
                    detail,
                )

        return response

    def init_photo_index(self):
        """
        初始化照片索引
        - 扫描所有源文件夹
        - 建立索引（如果不存在）

        Returns:
            dict: {"first_time": bool, "estimated_files": int, "estimated_minutes": float}
        """
        media_folders = getattr(self, "media_folders", [])
        if not media_folders:
            print("⚠️ 未配置媒体文件夹，跳过索引初始化")
            return {"first_time": False, "error": "未配置媒体文件夹"}

        try:
            from photo_manager import PhotoManager

            pm = PhotoManager(media_folders, self.data_dir)

            # 检查是否已有索引
            index_file = self.data_dir / "photo_index" / "photo_index.json"
            if index_file.exists():
                # 已有索引，检查是否需要更新
                import json

                with open(index_file, "r", encoding="utf-8") as f:
                    index = json.load(f)
                total_files = len(index.get("photos", {})) + len(
                    index.get("videos", {})
                )
                print(f"📂 已有索引: {total_files} 个文件")

                # 异步扫描更新（不阻塞启动）
                import threading

                def async_scan():
                    try:
                        # 读取日期范围设置（从配置文件读取）
                        index_after = USER_CONFIG.get("index_after_date", "") or None
                        print(
                            f"[索引] 配置中的index_after_date: '{USER_CONFIG.get('index_after_date')}'"
                        )
                        print(f"[索引] 传递给scan的start_date: '{index_after}'")
                        result = pm.scan_existing_photos(start_date=index_after)
                        print(
                            f"✅ 索引更新完成: 新增 {result.get('new', 0)} 个文件, 总计 {result.get('total', 0)} 个"
                        )
                    except Exception as e:
                        print(f"⚠️ 索引更新失败: {e}")

                thread = threading.Thread(target=async_scan, daemon=True)
                thread.start()
                return {"first_time": False, "existing_files": total_files}
            else:
                # 首次建立索引，先快速估算文件数量
                print("🔍 首次建立照片索引...")

                estimated_files = 0
                for folder in media_folders:
                    folder_path = Path(folder)
                    if folder_path.exists():
                        for root, dirs, files in os.walk(folder_path):
                            dirs[:] = [
                                d
                                for d in dirs
                                if d
                                not in [
                                    "original",
                                    "photo_index",
                                    "by_date",
                                    "compressed",
                                    "thumbnails",
                                ]
                            ]
                            for f in files:
                                if Path(f).suffix.lower() in {
                                    ".jpg",
                                    ".jpeg",
                                    ".png",
                                    ".gif",
                                    ".bmp",
                                    ".tiff",
                                    ".webp",
                                    ".mp4",
                                    ".mov",
                                    ".avi",
                                    ".mkv",
                                    ".flv",
                                    ".wmv",
                                }:
                                    estimated_files += 1

                # 估算时间：每秒约处理 50 个文件
                estimated_seconds = estimated_files / 50
                estimated_minutes = estimated_seconds / 60

                print(
                    f"📊 预估 {estimated_files} 个文件，约 {estimated_minutes:.1f} 分钟"
                )

                return {
                    "first_time": True,
                    "estimated_files": estimated_files,
                    "estimated_minutes": round(estimated_minutes, 1),
                }

        except Exception as e:
            print(f"⚠️ 初始化照片索引失败: {e}")
            return {"first_time": False, "error": str(e)}

    def build_photo_index(self):
        """执行照片索引建立（首次）"""
        media_folders = getattr(self, "media_folders", [])
        if not media_folders:
            return {"success": False, "error": "未配置媒体文件夹"}

        try:
            from photo_manager import PhotoManager

            pm = PhotoManager(media_folders, self.data_dir)
            print("🔍 开始建立照片索引，这可能需要一些时间...")
            result = pm.scan_existing_photos()
            print(f"✅ 索引建立完成: 共 {result.get('total', 0)} 个文件")

            # 清理日历缓存
            self.clear_calendar_cache()

            return {"success": True, "result": result}
        except Exception as e:
            print(f"⚠️ 建立照片索引失败: {e}")
            return {"success": False, "error": str(e)}

    def clear_calendar_cache(self):
        """清理日历缓存"""
        try:
            from flask import current_app as app

            # 清理所有以 _photo_dates_cache_ 开头的属性
            attrs_to_delete = [
                attr
                for attr in dir(app)
                if attr.startswith("_photo_dates_cache_")
                or attr.startswith("_photo_dates_cache_time_")
            ]
            for attr in attrs_to_delete:
                delattr(app, attr)
            print(f"[Calendar] 清理了 {len(attrs_to_delete)} 个缓存项")
        except Exception as e:
            print(f"[WARN] 清理日历缓存失败: {e}")

    def save_credentials(self, credentials, subdomain=None):
        """保存 tunnel 凭证到本地

        Args:
            credentials: 凭证信息字典
            subdomain: 可选，子域名信息
        """
        try:
            tunnel_id = credentials.get("TunnelID", "")
            tunnel_secret = credentials.get("TunnelSecret", "")
            account_tag = credentials.get("AccountTag", "")

            if not tunnel_id or not tunnel_secret:
                print(f"⚠ 凭证信息不完整")
                return False

            creds_dir = self.data_dir / "cloudflared"
            creds_dir.mkdir(parents=True, exist_ok=True)
            creds_file = creds_dir / f"{tunnel_id}.json"

            # 保存凭证文件
            creds_data = {
                "AccountTag": account_tag,
                "TunnelSecret": tunnel_secret,
                "TunnelID": tunnel_id,
                "Endpoint": "",
            }

            with open(creds_file, "w", encoding="utf-8") as f:
                json.dump(creds_data, f, indent=2)

            # 同时保存到客户端目录（供启动脚本使用）
            client_creds_file = self.data_dir / "cloudflared_credentials.json"
            with open(client_creds_file, "w", encoding="utf-8") as f:
                json.dump(creds_data, f, indent=2)

            # 如果提供了子域名，保存到 client_config.json
            if subdomain:
                self._save_subdomain_to_config(subdomain)

            print(f"💾 凭证已保存到: {creds_file}")
            return True

        except Exception as e:
            print(f"❌ 保存凭证失败: {e}")
            return False

    def _save_subdomain_to_config(self, subdomain):
        """保存子域名到 client_config.json"""
        try:
            config_file = self.data_dir / "client_config.json"
            config = {}
            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)

            # 更新 server.domain
            if "server" not in config:
                config["server"] = {}
            config["server"]["domain"] = subdomain

            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            print(f"💾 子域名已保存: {subdomain}")
        except Exception as e:
            print(f"⚠ 保存子域名失败: {e}")

    def load_local_credentials(self):
        """读取本地保存的 tunnel 凭证

        优先级：
        1. client_tunnels/{client_id}/credentials.json（如果有 client_id）
        2. 遍历 client_tunnels/ 下所有子目录（如果没有 client_id 或上述不存在）
        3. cloudflared_credentials.json（备用位置）

        不依赖 client_id，确保即使注册失败也能加载本地凭证
        """
        try:
            # 1. 如果有 client_id，优先从对应目录读取
            if self.client_id:
                client_tunnel_dir = self.data_dir / "client_tunnels" / self.client_id
                client_creds_file = client_tunnel_dir / "credentials.json"
                if client_creds_file.exists():
                    with open(client_creds_file, "r", encoding="utf-8") as f:
                        credentials = json.load(f)
                    tunnel_id = credentials.get("TunnelID", "")
                    if tunnel_id:
                        creds_dir = self.data_dir / "cloudflared"
                        creds_dir.mkdir(parents=True, exist_ok=True)
                        creds_file = creds_dir / f"{tunnel_id}.json"
                        if creds_file.exists():
                            print(f"📂 发现本地凭证: {tunnel_id[:8]}...")
                            return credentials
                        else:
                            print("⚠ 本地凭证缓存缺失，尝试重建...")
                            with open(creds_file, "w", encoding="utf-8") as f:
                                json.dump(credentials, f, indent=2)
                            print(f"✅ 已创建凭证文件: {creds_file}")
                            return credentials

            # 2. 遍历 client_tunnels 下所有子目录（不依赖 client_id）
            client_tunnels_dir = self.data_dir / "client_tunnels"
            if client_tunnels_dir.exists() and client_tunnels_dir.is_dir():
                for subdir in client_tunnels_dir.iterdir():
                    if subdir.is_dir():
                        creds_file = subdir / "credentials.json"
                        if creds_file.exists():
                            with open(creds_file, "r", encoding="utf-8") as f:
                                credentials = json.load(f)
                            tunnel_id = credentials.get("TunnelID", "")
                            if tunnel_id:
                                cf_creds_file = (
                                    self.data_dir / "cloudflared" / f"{tunnel_id}.json"
                                )
                                if cf_creds_file.exists():
                                    print(f"📂 发现本地凭证(遍历): {tunnel_id[:8]}...")
                                    # 同时恢复 client_id
                                    if not self.client_id:
                                        self.client_id = subdir.name
                                        print(
                                            f"📝 从凭证目录恢复 client_id: {self.client_id[:8]}..."
                                        )
                                    return credentials
                                else:
                                    creds_dir = self.data_dir / "cloudflared"
                                    creds_dir.mkdir(parents=True, exist_ok=True)
                                    with open(
                                        cf_creds_file, "w", encoding="utf-8"
                                    ) as f:
                                        json.dump(credentials, f, indent=2)
                                    print(f"✅ 已创建凭证文件: {cf_creds_file}")
                                    if not self.client_id:
                                        self.client_id = subdir.name
                                    return credentials

            # 3. 从备用位置读取
            client_creds_file = self.data_dir / "cloudflared_credentials.json"
            if client_creds_file.exists():
                with open(client_creds_file, "r", encoding="utf-8") as f:
                    credentials = json.load(f)
                tunnel_id = credentials.get("TunnelID", "")
                if tunnel_id:
                    creds_dir = self.data_dir / "cloudflared"
                    creds_dir.mkdir(parents=True, exist_ok=True)
                    creds_file = creds_dir / f"{tunnel_id}.json"
                    if not creds_file.exists():
                        with open(creds_file, "w", encoding="utf-8") as f:
                            json.dump(credentials, f, indent=2)
                    print(f"📂 发现本地凭证(备用): {tunnel_id[:8]}...")
                    return credentials

            return None
        except Exception as e:
            print(f"⚠ 读取本地凭证失败: {e}")
            return None

    def load_local_subdomain(self):
        """从本地配置读取子域名信息"""
        try:
            config_file = self.data_dir / "client_config.json"
            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                # 从 server.domain 提取子域名
                domain = config.get("server", {}).get("domain", "")
                if domain:
                    return domain
            return None
        except Exception as e:
            print(f"⚠ 读取本地子域名失败: {e}")
            return None

    def _is_local_server_ready(self):
        """检查本地 Flask 服务是否已监听端口"""
        port = getattr(self, "client_port", None)
        if not port:
            return False

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.5)
                return sock.connect_ex(("127.0.0.1", int(port))) == 0
        except Exception:
            return False

    def _start_pending_tunnel_if_ready(self):
        """如果本地服务已就绪，立即启动待启动的 Tunnel"""
        if (
            self.tunnel_active
            or not self._pending_tunnel_credentials
            or not self.subdomain
            or not self._is_local_server_ready()
        ):
            return False

        credentials = self._pending_tunnel_credentials
        self._pending_tunnel_credentials = None
        self.start_cloudflare_tunnel(credentials)
        return True

    def _tunnel_restart_stamp_file(self):
        return self.data_dir / "tunnel_daily_restart.txt"

    def _load_tunnel_restart_stamp(self) -> str:
        try:
            return self._tunnel_restart_stamp_file().read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def _save_tunnel_restart_stamp(self, value: str) -> None:
        try:
            self._tunnel_restart_stamp_file().write_text(
                str(value or "").strip(),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"保存 Tunnel 重启标记失败: {e}")

    def connect_to_public_server(self, background_subdomain=False, auto_build_index=True):
        """强制连接到公网服务端"""
        print(f"🌐 强制连接到公网服务端: {self.server_url}")
        print(f"📡 注册地址: {self.server_url}/czrz/register")
        logger.info(f"连接到公网服务端: {self.server_url}")

        # 测试连接
        try:
            response = self.session.get(
                f"{self.server_url}/czrz/health",
                timeout=30,
                verify=False,  # 禁用 SSL 验证，避免证书错误
            )

            if response.status_code == 200:
                server_info = response.json()
                print("✅ 公网服务端连接成功!")
                print(f"📊 服务端版本: {server_info.get('version')}")
                print(f"🌍 公网域名: {server_info.get('public_domain')}")
                logger.info(f"公网服务端连接成功: version={server_info.get('version')}")

                try:
                    config_resp = self.session.get(
                        f"{self.server_url}/czrz/client/server-config",
                        timeout=30,
                        verify=False,
                    )
                    if config_resp.status_code == 200:
                        config_data = config_resp.json()
                        new_domain = config_data.get("server_domain")
                        if new_domain and new_domain != self.server_domain:
                            print(
                                f"🔄 服务端域名已更新: {self.server_domain} → {new_domain}"
                            )
                            self.server_domain = new_domain
                            self.server_url = config_data.get(
                                "server_url", f"https://{new_domain}"
                            )
                            update_server_domain(new_domain)
                            CLIENT_CONFIG["server"]["domain"] = new_domain
                            CLIENT_CONFIG["server"]["url"] = self.server_url
                except Exception as e:
                    logger.warning(f"获取服务端配置失败: {e}")

                # 如果已有 client_id，发送心跳
                if self.client_id:
                    heartbeat_ok = self.send_heartbeat()
                    if not heartbeat_ok:
                        # 心跳失败，需要重新注册
                        print("🔄 客户端未注册或已失效，需要重新注册...")
                        logger.warning("心跳失败，需要重新注册")
                        self.register_client(
                            background_subdomain=background_subdomain,
                            auto_build_index=auto_build_index,
                        )
                    else:
                        # 心跳成功，检查是否需要获取子域名和凭证并启动 tunnel
                        if self.subdomain and self.public_url:
                            print(f"💓 心跳成功，已有公网地址: {self.public_url}")
                            # 即使有公网地址，也需要确保 tunnel 正在运行
                            if not self.tunnel_active:
                                print("🔄 Tunnel 未运行，正在启动...")
                                self.fetch_subdomain(
                                    background=background_subdomain,
                                    timeout=20 if background_subdomain else 60,
                                )
                        else:
                            print("💓 心跳成功，获取子域名和凭证...")
                            self.fetch_subdomain(
                                background=background_subdomain,
                                timeout=20 if background_subdomain else 60,
                            )
                        if auto_build_index:
                            # 初始化照片索引
                            index_info = self.init_photo_index()
                            if index_info and index_info.get("first_time"):
                                # 首次建立索引，后台执行
                                import threading

                                def async_build():
                                    self.build_photo_index()

                                threading.Thread(target=async_build, daemon=True).start()
                else:
                    # 注册新客户端
                    self.register_client(
                        background_subdomain=background_subdomain,
                        auto_build_index=auto_build_index,
                    )
            else:
                print(f"⚠ 服务端响应异常: {response.status_code}")
                logger.warning(f"服务端响应异常: {response.status_code}")
                print("🔧 尝试HTTP连接...")
                self.try_http_connection()

        except requests.exceptions.Timeout as e:
            reporter = globals().get("report_error")
            if callable(reporter):
                reporter("request_timeout", "首次连接服务端超时", str(e))
            print(f"⏱ 连接超时: {e}")
            logger.error(f"连接超时: {e}")
            print("🔧 尝试HTTP连接...")
            self.try_http_connection()
        except requests.exceptions.SSLError as e:
            print(f"🔒 SSL证书错误: {e}")
            logger.error(f"SSL证书错误: {e}")
            print("🔧 尝试HTTP连接...")
            self.try_http_connection()
        except Exception as e:
            print(f"❌ 连接错误: {e}")
            logger.error(f"连接错误: {e}")
            print("🔧 尝试HTTP连接...")
            self.try_http_connection()

    def try_http_connection(self):
        """尝试HTTP连接"""
        http_url = f"http://{self.server_domain}"
        print(f"🔄 尝试HTTP连接: {http_url}")

        try:
            response = self.session.get(f"{http_url}/czrz/health", timeout=30)
            if response.status_code == 200:
                print("✅ HTTP连接成功")
                self.server_url = http_url
                self.connect_to_public_server()
            else:
                print(f"❌ HTTP连接失败: {response.status_code}")
                self._enter_local_mode(f"HTTP连接失败 ({response.status_code})")
        except requests.exceptions.Timeout as e:
            reporter = globals().get("report_error")
            if callable(reporter):
                reporter("request_timeout", "HTTP回退连接超时", str(e))
            print(f"❌ HTTP连接超时: {e}")
            self._enter_local_mode(f"HTTP连接超时: {e}")
        except Exception as e:
            print(f"❌ HTTP连接错误: {e}")
            self._enter_local_mode(f"HTTP连接错误: {e}")

    def _enter_local_mode(self, reason="服务端不可用"):
        """进入本地模式"""
        print("=" * 50)
        print(f"⚠️  无法连接到服务端: {reason}")
        print("🏠 已切换到【本地模式】运行")
        print("   - 数据仅保存在本地，不会同步到云端")
        print("   - AI 功能将不可用")
        print("   - 请检查网络连接或稍后重试")
        print("=" * 50)
        logger.warning(f"进入本地模式: {reason}")
        if not self.client_id:
            self.client_id = f"local_{uuid.uuid4().hex[:12]}"
            self.save_config()
            print(f"📱 已生成本地客户端ID: {self.client_id}")
        global LOCAL_MODE
        LOCAL_MODE = True

    def register_client(self, background_subdomain=False, auto_build_index=True):
        """注册客户端"""
        print("📝 注册新客户端到公网服务端...")

        install_key = get_install_key()
        print(f"🔑 安装密钥: {install_key}")

        device_info = {
            "baby_name": self.baby_name,
            "baby_gender": getattr(self, "baby_gender", None),
            "baby_birthday": getattr(self, "baby_birthday", None),
            "user_city": getattr(self, "user_city", None),
            "ip": self.get_local_ip(),
            "mac_address": self.get_mac_address(),
            "client_port": self.find_available_port(),
            "install_key": install_key,
        }

        try:
            response = self.session.post(
                f"{self.server_url}/czrz/register", json=device_info, timeout=60
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.client_id = data.get("client_id") or ""
                    self.is_paid = data.get("is_paid", False)
                    self.registered_at = (
                        data.get("registered_at") or datetime.now().isoformat()
                    )
                    secret_key = data.get("secret_key") or ""
                    if secret_key:
                        USER_CONFIG["secret_key"] = secret_key
                        self.secret_key = secret_key
                        print(f"🔐 密钥已保存")

                    print(f"✅ 注册成功!")
                    print(f"📱 客户端ID: {self.client_id}")
                    print(
                        "💠 AI服务状态: "
                        + ("已开通" if self.is_paid else "未开通")
                    )
                    logger.info(
                        f"注册成功: client_id={self.client_id}, is_paid={self.is_paid}"
                    )

                    self.save_config()
                    self.fetch_subdomain(
                        background=background_subdomain,
                        timeout=20 if background_subdomain else 60,
                    )
                    if auto_build_index:
                        # 初始化照片索引
                        index_info = self.init_photo_index()
                        if index_info and index_info.get("first_time"):
                            # 首次建立索引，后台执行
                            import threading

                            def async_build():
                                self.build_photo_index()

                            threading.Thread(target=async_build, daemon=True).start()
                else:
                    print(f"❌ 注册失败: {data.get('message')}")
                    logger.error(f"注册失败: {data.get('message')}")
            else:
                print(f"❌ 注册请求失败: {response.status_code}")
                logger.error(f"注册请求失败: {response.status_code}")

        except Exception as e:
            print(f"❌ 注册错误: {e}")
            logger.error(f"注册错误: {e}")
            self._enter_local_mode(f"注册失败: {e}")

    def fetch_subdomain(self, background=False, timeout=60):
        """获取子域名并启动 Cloudflare Tunnel"""
        if not self.client_id:
            return

        def do_fetch():
            """执行获取子域名的实际逻辑"""
            print("🔑 获取子域名...")
            logger.info("获取子域名...")

            try:
                response = self.signed_request(
                    "POST",
                    f"{self.server_url}/czrz/cloudflare/credentials",
                    json={"client_id": self.client_id},
                    timeout=timeout,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        client_info = data.get("client_info", {})
                        self.subdomain = client_info.get("subdomain") or ""
                        self.public_url = client_info.get("public_url") or ""

                        print(f"🎉 子域名获取成功!")
                        print(f"🌐 您的子域名: {self.subdomain}")
                        print(f"🔗 公网访问地址: {self.public_url}")
                        logger.info(f"子域名获取成功: {self.subdomain}")

                        # 保存凭证到本地
                        credentials = data.get("credentials", {})
                        if credentials:
                            self.save_credentials(credentials, self.subdomain)
                            self._pending_tunnel_credentials = credentials
                            if not self._start_pending_tunnel_if_ready():
                                print("⏳ Tunnel 将在本地服务就绪后自动启动...")
                        return True
                    else:
                        print(f"⚠ 获取子域名失败: {data.get('message')}")
                        logger.warning(f"获取子域名失败: {data.get('message')}")
                else:
                    print(f"⚠ 子域名请求失败: {response.status_code}")
                    logger.warning(f"子域名请求失败: {response.status_code}")

            except Exception as e:
                print(f"⚠ 获取子域名错误: {e}")
                logger.error(f"获取子域名错误: {e}")

            return False

        if background:
            thread = threading.Thread(
                target=lambda: self.fetch_subdomain(background=False, timeout=timeout),
                daemon=True,
            )
            thread.start()
            return

        # 先尝试一次
        if do_fetch():
            return

        # 如果失败，启动后台重试线程（每10秒重试一次）
        def retry_task():
            print("⏳ 启动后台重试，每10秒尝试获取子域名...")
            max_retries = 60  # 最多重试10分钟
            for i in range(max_retries):
                time.sleep(10)
                print(f"🔄 重试获取子域名 ({i + 1}/{max_retries})...")
                if do_fetch():
                    return
            print("⚠ 获取子域名重试超时（10分钟），将在下次心跳时继续重试")

        import threading

        thread = threading.Thread(target=retry_task, daemon=True)
        thread.start()

    def fetch_subdomain_only(self, background=False, timeout=60):
        """获取子域名信息但不启动 Tunnel（Tunnel 在 Flask 启动后启动）

        优先级：
        1. 先尝试读取本地已保存的凭证
        2. 如果本地凭证存在且有效，直接使用
        3. 如果本地凭证不存在或无效，再从服务端获取
        """
        if not self.client_id:
            return

        # 1. 先尝试读取本地凭证
        local_credentials = self.load_local_credentials()
        if local_credentials:
            tunnel_id = local_credentials.get("TunnelID", "")
            # 尝试从本地配置读取子域名
            local_domain = self.load_local_subdomain()
            if local_domain:
                # 构造子域名（假设格式为 tunnel_id前8位 + mumu.ftir.fun）
                # 或者直接使用保存的域名
                self.subdomain = local_domain
                self.public_url = f"https://{local_domain}"
                self._pending_tunnel_credentials = local_credentials
                print(f"✅ 使用本地凭证启动 Tunnel")
                print(f"🌐 子域名: {self.subdomain}")
                logger.info(f"使用本地凭证: {tunnel_id[:8]}...")
                self._start_pending_tunnel_if_ready()
                return

        def do_fetch():
            # 2. 本地凭证不存在，从服务端获取
            print("🔑 获取子域名...")
            logger.info("获取子域名...")

            try:
                response = self.signed_request(
                    "POST",
                    f"{self.server_url}/czrz/cloudflare/credentials",
                    json={"client_id": self.client_id},
                    timeout=timeout,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        client_info = data.get("client_info", {})
                        self.subdomain = client_info.get("subdomain") or ""
                        self.public_url = client_info.get("public_url") or ""

                        print(f"🎉 子域名获取成功!")
                        print(f"🌐 您的子域名: {self.subdomain}")
                        logger.info(f"子域名获取成功: {self.subdomain}")

                        # 保存凭证到本地
                        credentials = data.get("credentials", {})
                        if credentials:
                            # 保存凭证和子域名到本地
                            self.save_credentials(credentials, self.subdomain)
                            self._pending_tunnel_credentials = credentials
                            if not self._start_pending_tunnel_if_ready():
                                print("⏳ Tunnel 将在服务启动后自动启动...")
                    else:
                        print(f"⚠ 获取子域名失败: {data.get('message')}")
                        logger.warning(f"获取子域名失败: {data.get('message')}")
                else:
                    print(f"⚠ 子域名请求失败: {response.status_code}")
                    logger.warning(f"子域名请求失败: {response.status_code}")
                    # 如果是 5xx 错误，可能是服务端 tunnel 还没就绪，稍后重试
                    if response.status_code >= 500:
                        self._retry_fetch_subdomain()

            except Exception as e:
                print(f"⚠ 获取子域名错误: {e}")
                logger.error(f"获取子域名错误: {e}")

        if background:
            thread = threading.Thread(target=do_fetch, daemon=True)
            thread.start()
            return

        do_fetch()

    def _retry_fetch_subdomain(self):
        """在后台线程中延迟重试获取子域名"""

        def retry_task():
            import time

            max_retries = 3
            for i in range(max_retries):
                time.sleep(5)  # 等待 5 秒后重试
                print(f"🔄 重试获取子域名 ({i + 1}/{max_retries})...")
                try:
                    response = self.signed_request(
                        "POST",
                        f"{self.server_url}/czrz/cloudflare/credentials",
                        json={"client_id": self.client_id},
                        timeout=60,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("success"):
                            client_info = data.get("client_info", {})
                            self.subdomain = client_info.get("subdomain") or ""
                            self.public_url = client_info.get("public_url") or ""
                            print(f"🎉 子域名获取成功!")
                            logger.info(f"子域名获取成功: {self.subdomain}")
                            # 保存凭证并启动 Tunnel
                            credentials = data.get("credentials", {})
                            if credentials:
                                self.save_credentials(credentials, self.subdomain)
                                self.start_cloudflare_tunnel(credentials)
                            return
                except Exception as e:
                    print(f"⚠ 重试失败: {e}")
            print(f"⚠ 获取子域名重试 {max_retries} 次后仍失败")

        import threading

        thread = threading.Thread(target=retry_task, daemon=True)
        thread.start()

    def start_cloudflare_tunnel(self, credentials):
        """启动 Cloudflare Tunnel（使用配置文件方式）"""
        import platform
        import subprocess

        try:
            tunnel_id = credentials.get("TunnelID", "")
            if not tunnel_id:
                print("⚠ 凭证中缺少 TunnelID")
                return

            if not self.subdomain:
                print("⚠ 缺少子域名信息，无法启动 Tunnel")
                return

            creds_dir = self.data_dir / "cloudflared"
            creds_dir.mkdir(parents=True, exist_ok=True)
            creds_file = creds_dir / f"{tunnel_id}.json"

            with open(creds_file, "w", encoding="utf-8") as f:
                json.dump(credentials, f, indent=2)

            transport_protocol = "auto"

            config_content = f"""tunnel: {tunnel_id}
credentials-file: {creds_file}
protocol: {transport_protocol}

ingress:
  - hostname: {self.subdomain}
    service: http://localhost:{self.client_port}
  - service: http_status:404
"""
            config_file = self.data_dir / "tunnel_config.yml"
            with open(config_file, "w", encoding="utf-8") as f:
                f.write(config_content)

            system = platform.system()
            exe_name = "cloudflared.exe" if system == "Windows" else "cloudflared"

            which_path = shutil.which(exe_name)
            exe_paths = [
                self.data_dir / exe_name,
                self.data_dir / "bin" / exe_name,
                Path(sys._MEIPASS) / exe_name if hasattr(sys, "_MEIPASS") else None,
                Path(which_path) if which_path else None,
            ]
            exe_paths = [p for p in exe_paths if p is not None]

            exe_path = None
            for path in exe_paths:
                if path.exists() and path.is_file() and os.access(path, os.X_OK):
                    exe_path = path
                    break

            if not exe_path:
                print(
                    f"⚠ 未找到 {exe_name}，请确保它在系统 PATH 或 {self.data_dir / 'bin'} 中"
                )
                return

            cmd = [
                str(exe_path),
                "tunnel",
                "--config",
                str(config_file),
                "--protocol",
                transport_protocol,
                "run",
            ]

            print(f"🚀 启动 Cloudflare Tunnel...")
            print(f"   本地地址: http://localhost:{self.client_port}")
            print(f"   公网地址: {self.public_url}")
            print(f"   配置文件: {config_file}")

            if system == "Windows":
                # CREATE_NO_WINDOW = 0x08000000 - 隐藏窗口
                self.tunnel_process = subprocess.Popen(
                    cmd,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                self.tunnel_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )

            print(f"✅ Cloudflare Tunnel 已启动 (PID: {self.tunnel_process.pid})")
            self.tunnel_active = True
            self._tunnel_credentials = credentials  # 保存凭证用于重启
            self._last_tunnel_check = time.time()

        except Exception as e:
            print(f"⚠ 启动 Tunnel 失败: {e}")

    def stop_cloudflare_tunnel(self):
        """停止 Cloudflare Tunnel"""
        import subprocess
        import platform
        import signal

        try:
            system = platform.system()

            # 优先使用保存的进程引用停止
            if self.tunnel_process and self.tunnel_process.poll() is None:
                try:
                    self.tunnel_process.terminate()
                    self.tunnel_process.wait(timeout=30)
                    print(
                        f"🛑 Cloudflare Tunnel 已停止 (PID: {self.tunnel_process.pid})"
                    )
                except:
                    # 强制终止
                    self.tunnel_process.kill()
                    print(f"🛑 Cloudflare Tunnel 已强制停止")
            elif system == "Windows":
                # 备用：使用 taskkill（只杀当前用户的进程）
                subprocess.run(
                    [
                        "taskkill",
                        "/F",
                        "/IM",
                        "cloudflare.exe",
                        "/FI",
                        f"STATUS eq RUNNING",
                    ],
                    capture_output=True,
                    stderr=subprocess.DEVNULL,
                )
                print(f"🛑 Cloudflare Tunnel 已停止")
            else:
                # Linux/Mac: 查找并终止进程
                try:
                    result = subprocess.run(
                        ["pgrep", "-f", "cloudflared.*tunnel.*run"],
                        capture_output=True,
                        text=True,
                    )
                    if result.stdout:
                        pids = result.stdout.strip().split("\n")
                        for pid in pids:
                            try:
                                os.kill(int(pid), signal.SIGTERM)
                            except:
                                pass
                except:
                    pass
                print(f"🛑 Cloudflare Tunnel 已停止")

            self.tunnel_active = False
            self.tunnel_process = None

        except Exception as e:
            print(f"⚠ 停止 Tunnel 失败: {e}")

    def send_heartbeat(self):
        """发送心跳，返回是否成功"""
        if not self.client_id:
            install_key = get_install_key()
            print(f"🔑 无客户端ID，尝试注册，安装密钥: {install_key}")
            payload = {
                "client_id": None,
                "baby_name": self.baby_name,
                "ip": self.get_local_ip(),
                "mac_address": self.get_mac_address(),
                "client_port": self.client_port,
                "install_key": install_key,
            }
        else:
            payload = {
                "client_id": self.client_id,
                "baby_name": self.baby_name,
                "ip": self.get_local_ip(),
                "mac_address": self.get_mac_address(),
                "client_port": self.client_port,
            }

        try:
            global ERROR_REPORT_QUEUE
            errors_to_send = ERROR_REPORT_QUEUE.copy() if ERROR_REPORT_QUEUE else []
            payload["errors"] = errors_to_send

            try:
                from card_cache import get_card_cache

                cache = get_card_cache()
                payload["cached_daily_card_ids"] = [
                    card_id
                    for card_id in (
                        (card.get("id") or card.get("card_id"))
                        for card in cache.get_all_cards()
                    )
                    if card_id
                ]
            except Exception as e:
                print(f"⚠ 读取本地卡片ID失败: {e}")

            response = self.session.post(
                f"{self.server_url}/czrz/client/heartbeat",
                json=payload,
                timeout=30,
            )

            if errors_to_send:
                ERROR_REPORT_QUEUE = []

            if response.status_code == 200:
                data = response.json()

                if data.get("error") == "INVALID_INSTALL_KEY":
                    print(f"❌ 无效的安装密钥")
                    logger.error("无效的安装密钥")
                    return False

                if data.get("auto_registered"):
                    print(f"✅ 自动注册成功")
                    logger.info("自动注册成功")
                    self.client_id = data.get("client_id")
                    secret_key = data.get("secret_key")
                    if secret_key:
                        USER_CONFIG["secret_key"] = secret_key
                        self.secret_key = secret_key
                        print(f"🔐 密钥已保存")
                    self.save_config()

                # 服务端要求刷新配置（根据MAC匹配到已有客户端）
                if data.get("requires_refresh") and not data.get("auto_registered"):
                    new_client_id = data.get("client_id")
                    new_secret_key = data.get("secret_key")
                    if new_client_id and new_client_id != self.client_id:
                        print(f"🔄 服务端识别到已有客户端，更新配置...")
                        print(f"   原ID: {self.client_id}")
                        print(f"   新ID: {new_client_id}")
                        logger.info(f"根据MAC匹配更新客户端ID: {self.client_id} -> {new_client_id}")
                        self.client_id = new_client_id
                        if new_secret_key:
                            USER_CONFIG["secret_key"] = new_secret_key
                            self.secret_key = new_secret_key
                        self.save_config()
                        print(f"✅ 配置已更新，下次请求将使用新ID")

                if data.get("disabled"):
                    print(f"⚠ 客户端已被禁用: {data.get('message')}")
                    logger.warning(f"客户端已被禁用: {data.get('message')}")
                    print("🏠 切换到本地模式运行")
                    global LOCAL_MODE, CLIENT_DISABLED
                    LOCAL_MODE = True
                    CLIENT_DISABLED = True
                    return False

                logger.debug("心跳发送成功")

                # 保存服务端推送的每日卡片到本地缓存
                daily_cards = data.get("daily_cards", [])
                daily_card_ids = data.get("daily_card_ids", [])
                if daily_cards or daily_card_ids:
                    try:
                        from card_cache import get_card_cache

                        cache = get_card_cache()
                        removed = 0

                        # 删除不在服务端列表中的缓存卡片
                        # 只有当服务端返回了有效的卡片ID列表时才进行过滤
                        if daily_card_ids and len(daily_card_ids) > 0:
                            server_id_set = set(daily_card_ids)
                            before_count = len(cache.cards)
                            cache.cards = [
                                c
                                for c in cache.cards
                                if c.get("id") in server_id_set
                                or c.get("card_id") in server_id_set
                            ]
                            removed = before_count - len(cache.cards)
                            if removed > 0:
                                print(f"🗑️ 清理了 {removed} 张过期缓存卡片")

                        # 添加新卡片
                        added = 0
                        for card in daily_cards:
                            card_id = card.get("card_id")
                            if not cache.get_card_by_id(card_id) and not cache.has_equivalent_card(card):
                                card["id"] = card_id
                                card["category"] = "extended"
                                # 将照片文件名转为本地URL
                                if "photo_paths" in card and card["photo_paths"]:
                                    import urllib.parse

                                    card["photo_paths"] = [
                                        f"/photo/{urllib.parse.quote(p)}"
                                        if not p.startswith("/photo/")
                                        else p
                                        for p in card["photo_paths"]
                                    ]
                                cache.cards.append(card)
                                added += 1
                        if added > 0:
                            print(f"📦 从服务端新增 {added} 张卡片到本地缓存")

                        role_image_added = ensure_virtual_role_card_images(cache.cards)

                        # 只有当有实际变化时才保存缓存
                        if added > 0 or removed > 0 or role_image_added:
                            cache._save_cache()
                    except Exception as e:
                        print(f"⚠ 保存卡片失败: {e}")

                # 同步服务端公网地址（用于客户端连接服务端）
                server_public_url = data.get("server_public_url")
                if server_public_url:
                    if self.server_url != server_public_url:
                        self.server_url = server_public_url
                        self.save_config()
                        print(f"🔗 服务端地址已同步: {self.server_url}")
                        logger.info(f"服务端地址已同步: {self.server_url}")

                # 检查是否需要刷新凭证（tunnel被重新分配）
                if data.get("requires_refresh"):
                    print(f"🔄 凭证已失效，正在重新获取...")
                    logger.info("凭证已失效，正在重新获取...")
                    self.check_and_refresh_credentials()

                # 更新 AI 额度档位和配额信息（如果服务端返回了）
                if data.get("success") and "client_info" in data:
                    client_info = data["client_info"]
                    old_is_paid = self.is_paid
                    new_is_paid = client_info.get("is_paid", False)

                    if old_is_paid != new_is_paid:
                        self.is_paid = new_is_paid
                        self.save_config()
                        print(
                            "💠 AI服务状态已更新: "
                            + ("已开通" if new_is_paid else "未开通")
                        )
                        logger.info(f"AI服务状态已更新: is_paid={new_is_paid}")

                    # 同步 subdomain 和 public_url
                    server_subdomain = client_info.get("subdomain")
                    server_public_url = client_info.get("public_url")
                    if server_subdomain and server_public_url:
                        if (
                            self.subdomain != server_subdomain
                            or self.public_url != server_public_url
                        ):
                            self.subdomain = server_subdomain
                            self.public_url = server_public_url
                            self.save_config()
                            print(f"🔗 公网地址已同步: {self.public_url}")
                            logger.info(f"公网地址已同步: {self.public_url}")

                server_commands = data.get("commands") or []
                if isinstance(server_commands, list) and server_commands:
                    self.handle_server_commands(server_commands)

                return True
            else:
                print(f"⚠ 心跳失败: {response.status_code}")
                logger.warning(f"心跳失败: {response.status_code}")
                return False

        except requests.exceptions.Timeout as e:
            reporter = globals().get("report_error")
            if callable(reporter):
                reporter("request_timeout", "心跳请求超时", str(e))
            print(f"⚠ 心跳超时: {e}")
            logger.error(f"心跳超时: {e}")
            return False
        except Exception as e:
            print(f"⚠ 心跳错误: {e}")
            logger.error(f"心跳错误: {e}")
            return False

    def report_server_command_status(
        self,
        command_id: str,
        command_type: str,
        state: str,
        *,
        status: dict | None = None,
        message: str = "",
    ) -> bool:
        """向服务端回报通过心跳下发的命令执行状态。"""
        if not self.client_id:
            return False

        payload = {
            "client_id": self.client_id,
            "command_id": command_id,
            "command_type": command_type,
            "state": state,
            "message": message,
            "status": status or {},
        }

        try:
            response = self.signed_request(
                "POST",
                f"{self.server_url}/czrz/client/command-status",
                json=payload,
                timeout=30,
            )
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"[ServerCommand] 状态回报失败 {command_id}: {e}")
            return False

    def handle_server_commands(self, commands: list[dict]):
        """处理服务端经心跳下发的命令。"""
        for command in commands:
            if not isinstance(command, dict):
                continue

            command_id = (command.get("command_id") or "").strip()
            command_type = (command.get("command_type") or "").strip()
            if not command_id or not command_type:
                continue

            if command_type == "cancel_auto_review":
                payload = command.get("payload") or {}
                target_task_id = (payload.get("target_task_id") or "").strip()
                result = request_ai_auto_review_cancel(target_task_id)
                state = "completed" if result.get("success") else "failed"
                status = {}
                if result.get("task_id"):
                    status = _snapshot_ai_review_task(result["task_id"])
                self.report_server_command_status(
                    command_id,
                    command_type,
                    state,
                    status=status,
                    message=result.get("message", ""),
                )
                with REMOTE_AI_COMMANDS_LOCK:
                    REMOTE_AI_COMMANDS[command_id] = {
                        "state": state,
                        "completed_at": datetime.now().isoformat(),
                        "command_type": command_type,
                    }
                continue

            if command_type != "auto_review":
                logger.info(f"[ServerCommand] 忽略未知命令类型: {command_type}")
                self.report_server_command_status(
                    command_id,
                    command_type,
                    "rejected",
                    message=f"不支持的命令类型: {command_type}",
                )
                continue

            with REMOTE_AI_COMMANDS_LOCK:
                tracker = REMOTE_AI_COMMANDS.get(command_id)
                if tracker and tracker.get("state") not in {"failed", "completed"}:
                    continue
                REMOTE_AI_COMMANDS[command_id] = {
                    "state": "queued",
                    "received_at": datetime.now().isoformat(),
                    "command_type": command_type,
                }

            worker = threading.Thread(
                target=self._run_server_auto_review_command,
                args=(command_id, command_type),
                daemon=True,
            )
            with REMOTE_AI_COMMANDS_LOCK:
                REMOTE_AI_COMMANDS[command_id]["thread"] = worker
            worker.start()

    def _run_server_auto_review_command(self, command_id: str, command_type: str):
        """执行服务端通过心跳下发的自动刷新命令。"""
        start_result = start_ai_auto_review_task(reuse_running=True)
        if not start_result.get("success"):
            failure_message = start_result.get("message") or "客户端拒绝启动刷新任务"
            failure_status = {
                "task_id": "",
                "completed": True,
                "message": failure_message,
                "portrait_refresh": "failed",
                "portrait_llm_valid": False,
                "portrait_message": failure_message,
            }
            self.report_server_command_status(
                command_id,
                command_type,
                "failed",
                status=failure_status,
                message=failure_message,
            )
            with REMOTE_AI_COMMANDS_LOCK:
                REMOTE_AI_COMMANDS[command_id] = {
                    "state": "failed",
                    "completed_at": datetime.now().isoformat(),
                    "command_type": command_type,
                }
            return

        local_task_id = start_result.get("task_id")
        accepted_message = start_result.get("message") or "客户端已接收刷新命令"
        snapshot = _snapshot_ai_review_task(local_task_id)
        self.report_server_command_status(
            command_id,
            command_type,
            "accepted",
            status=snapshot,
            message=accepted_message,
        )

        last_fingerprint = ""
        final_state = "accepted"
        while True:
            snapshot = _snapshot_ai_review_task(local_task_id)
            if not snapshot:
                snapshot = {
                    "task_id": local_task_id or "",
                    "completed": True,
                    "message": "本地任务不存在",
                    "portrait_refresh": "failed",
                    "portrait_llm_valid": False,
                    "portrait_message": "本地任务不存在",
                }
                final_state = "failed"
                self.report_server_command_status(
                    command_id,
                    command_type,
                    final_state,
                    status=snapshot,
                    message=snapshot["message"],
                )
                break

            fingerprint = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            if snapshot.get("completed"):
                if snapshot.get("status") == "cancelled" or snapshot.get("cancelled"):
                    final_state = "cancelled"
                else:
                    final_state = (
                        "completed"
                        if snapshot.get("portrait_refresh") == "completed"
                        and snapshot.get("portrait_llm_valid") is True
                        else "failed"
                    )
                self.report_server_command_status(
                    command_id,
                    command_type,
                    final_state,
                    status=snapshot,
                    message=snapshot.get("message", ""),
                )
                break

            if fingerprint != last_fingerprint:
                self.report_server_command_status(
                    command_id,
                    command_type,
                    "running",
                    status=snapshot,
                    message=snapshot.get("message", ""),
                )
                last_fingerprint = fingerprint

            time.sleep(3)

        with REMOTE_AI_COMMANDS_LOCK:
            REMOTE_AI_COMMANDS[command_id] = {
                "state": final_state,
                "completed_at": datetime.now().isoformat(),
                "local_task_id": local_task_id,
                "command_type": command_type,
            }

    def start_heartbeat_loop(self):
        """启动后台心跳循环（短间隔轮询服务端命令）"""

        def heartbeat_worker():
            print(f"💓 心跳循环已启动（每 {HEARTBEAT_INTERVAL_SECONDS} 秒一次）")
            last_restart_day = self._load_tunnel_restart_stamp()
            heartbeat_failures = 0

            while True:
                try:
                    time.sleep(HEARTBEAT_INTERVAL_SECONDS)

                    # 检查并重启 Tunnel（每天凌晨 3 点）
                    now = datetime.now()
                    current_hour = now.hour
                    current_day = now.strftime("%Y-%m-%d")
                    if current_hour == 3 and last_restart_day != current_day:
                        if self._tunnel_credentials and self.tunnel_active:
                            print("🔄 定时重启 Tunnel（凌晨 3 点）...")
                            logger.info("定时重启 Tunnel")
                            self.stop_cloudflare_tunnel()
                            time.sleep(2)
                            self.start_cloudflare_tunnel(self._tunnel_credentials)
                            last_restart_day = current_day
                            self._save_tunnel_restart_stamp(current_day)

                    # 检查 Tunnel 进程是否存活
                    if self.tunnel_active and self._tunnel_credentials:
                        if (
                            self.tunnel_process
                            and self.tunnel_process.poll() is not None
                        ):
                            print("⚠ Tunnel 进程已退出，尝试重启...")
                            logger.warning("Tunnel 进程已退出，尝试重启")
                            self.tunnel_active = False
                            time.sleep(2)
                            self.start_cloudflare_tunnel(self._tunnel_credentials)

                    # 如果 tunnel 从未启动过，尝试启动
                    if not self.tunnel_active:
                        # 优先使用本地凭证
                        if not self._tunnel_credentials:
                            local_credentials = self.load_local_credentials()
                            if local_credentials:
                                self._tunnel_credentials = local_credentials
                                # 尝试读取本地子域名
                                local_domain = self.load_local_subdomain()
                                if local_domain:
                                    self.subdomain = local_domain
                                    self.public_url = f"https://{local_domain}"
                                print(f"📂 加载本地凭证用于启动 Tunnel")

                        # 如果有凭证，启动 tunnel
                        if self._tunnel_credentials and self.subdomain:
                            print("🔄 Tunnel 未启动，尝试启动...")
                            logger.info("Tunnel 未启动，尝试启动")
                            self.start_cloudflare_tunnel(self._tunnel_credentials)
                        elif not self._tunnel_credentials:
                            # 本地凭证也不存在，才从服务端获取
                            print("🔄 本地凭证不存在，尝试从服务端获取...")
                            logger.info("本地凭证不存在，尝试从服务端获取")
                            self.fetch_subdomain()

                    # 发送心跳
                    if self.client_id:
                        success = self.send_heartbeat()
                        if success:
                            if heartbeat_failures > 0:
                                print("✅ 心跳恢复成功")
                            heartbeat_failures = 0
                        else:
                            heartbeat_failures += 1
                            print("⚠ 心跳失败，可能需要重新连接")
                    else:
                        print("⚠ client_id 为空，尝试重新注册...")
                        logger.warning("client_id 为空，尝试重新注册")
                        self.register_client()
                        if self.client_id:
                            print("✅ 重新注册成功")
                            logger.info("重新注册成功")
                except Exception as e:
                    print(f"❌ 心跳循环错误：{e}")

        # 启动后台线程
        thread = threading.Thread(target=heartbeat_worker, daemon=True)
        thread.start()
        return thread

    def check_and_refresh_credentials(self):
        """
        检查是否需要刷新凭证，如果需要则重新获取
        返回: 是否成功刷新
        """
        try:
            response = self.signed_request(
                "POST",
                f"{self.server_url}/czrz/cloudflare/credentials",
                json={"client_id": self.client_id},
                timeout=60,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    credentials = data.get("credentials", {})
                    if credentials:
                        # 保存凭证到本地
                        self.save_credentials(credentials)

                        # 如果 tunnel 正在运行，先停止
                        if self.tunnel_active:
                            self.stop_cloudflare_tunnel()

                        # 启动 tunnel
                        self.start_cloudflare_tunnel(credentials)

                        print(f"🔄 凭证已刷新")
                        return True
            elif response.status_code == 403:
                # 客户端被禁用
                print(f"⚠ 客户端已被禁用，无法获取凭证")
                logger.warning("客户端已被禁用，无法获取凭证")
                print("🏠 公网访问功能不可用，仅可本地使用")
                return False

            print(f"⚠ 凭证刷新失败")
            return False

        except Exception as e:
            print(f"⚠ 凭证刷新异常: {e}")
            return False

    def get_local_ip(self):
        """获取本地IP"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def get_mac_address(self):
        """获取MAC地址"""
        try:
            import uuid

            mac = ":".join(
                [
                    "{:02x}".format((uuid.getnode() >> elements) & 0xFF)
                    for elements in range(0, 2 * 6, 8)
                ][::-1]
            )
            return mac
        except:
            return "00:00:00:00:00:00"

    def find_available_port(self):
        """
        查找可用端口 - 简化版（假设已有管理员权限）

        扫描范围：3000-3010, 8000-8010, 9000（缩小范围提高性能）
        逻辑：
        1. 按顺序扫描端口
        2. 遇到可用 → 立即使用
        3. 全部占用 → 报错退出
        """
        # 定义扫描端口范围（缩小范围提高性能）
        priority_ports = [
            3000,
            3001,
            3002,
            3003,
            3004,
            3005,
            3006,
            3007,
            3008,
            3009,
            3010,
        ]
        extended_ports = [
            8000,
            8001,
            8002,
            8003,
            8004,
            8005,
            8006,
            8007,
            8008,
            8009,
            8010,
            9000,
        ]

        # 合并去重
        all_ports = priority_ports + [
            p for p in extended_ports if p not in priority_ports
        ]

        # 记录被占用的端口用于错误提示
        occupied_ports = []

        print("🔍 扫描可用端口...")

        for port in all_ports:
            if self._is_port_available(port):
                print(f"✅ 使用端口: {port}")
                return port
            else:
                occupied_ports.append(port)

        # 全部占用，弹窗提示
        self._show_error_dialog(
            "端口全部被占用",
            "无法找到可用端口 (3000-3010, 8000-8010, 9000)\n"
            "所有端口均被其他程序占用。\n\n"
            "请关闭以下类型程序后重试:\n"
            "• Docker / WSL\n"
            "• 其他 Web 服务器\n"
            "• IDE 调试服务\n\n"
            "如仍无法解决，请联系技术支持。",
            occupied_ports,
        )
        sys.exit(1)

    def _is_port_available(self, port):
        """检查端口是否可用（简化版）"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            result = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            return result != 0  # 非0表示可用
        except:
            return False

    def _show_error_dialog(self, title, message, occupied_ports=None):
        """显示错误弹窗（Windows）"""
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
        except:
            print(f"\n❌ {title}")
            print(message)

        # 显示被占用的端口列表
        if occupied_ports:
            for port in occupied_ports[:15]:
                print(f"  - 端口 {port}")
            if len(occupied_ports) > 15:
                print(f"  ... 还有 {len(occupied_ports) - 15} 个端口被占用")
            print("\n常见占用者: Docker、WSL、其他Web服务器、IDE调试服务")

        input("\n按回车键退出...")
        sys.exit(1)


# ==================== Flask 应用配置 ====================
def sync_runtime_web_dir(folder_name: str) -> str:
    """将页面资源同步到客户端缓存目录，避免直接从源码目录读取。"""
    if hasattr(sys, "_MEIPASS"):
        source_dir = Path(sys._MEIPASS) / folder_name
    else:
        source_dir = Path(__file__).resolve().parent / folder_name

    target_dir = RUNTIME_WEB_DIR / folder_name
    if source_dir.exists():
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
        return str(target_dir)
    return str(source_dir)


def get_template_folder():
    """获取模板文件夹路径（兼容 PyInstaller）"""
    return sync_runtime_web_dir("templates")


def get_static_folder():
    """获取静态文件夹路径（兼容 PyInstaller）"""
    return sync_runtime_web_dir("static")


# 创建 Flask 应用
template_folder = get_template_folder()
static_folder = get_static_folder()

app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)

# 配置 Flask session
app.secret_key = USER_CONFIG.get("secret_key") or USER_CONFIG.get("client_id") or "mumu_default_secret_key"
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=3650)  # 10年，相当于永久有效
app.config['SESSION_COOKIE_NAME'] = 'mumu_session'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# 注意：在本地开发或非HTTPS环境下，不要设置 SESSION_COOKIE_SECURE


def _is_problematic_mobile_browser() -> bool:
    ua = (request.headers.get("User-Agent") or "").lower()
    return any(
        token in ua
        for token in (
            "vivo",
            "bbk",
            "funtouch",
            "oppo",
            "coloros",
        )
    )


@app.before_request
def enforce_family_code_access():
    """
    统一访问控制：
    - 局域网/本机：免家庭码
    - 公网：首次输入家庭码，之后会话内与局域网一致
    """
    path = request.path or "/"
    allow_prefixes = ("/static/",)
    allow_paths = {
        "/family-access",
        "/api/family-access",
        "/setup",
        "/api/setup",
        "/favicon.ico",
    }

    if path in allow_paths or any(path.startswith(p) for p in allow_prefixes):
        return None

    source_ip = get_request_source_ip()
    if _is_private_or_loopback_ip(source_ip):
        return None

    if is_family_code_verified():
        return None

    # 兼容未配置家庭码的旧数据，默认放行并写入会话
    if not get_family_code():
        mark_family_code_verified()
        return None

    next_path = request.full_path if request.query_string else path
    if next_path.endswith("?"):
        next_path = next_path[:-1]

    if path.startswith("/api/"):
        return _build_family_gate_response()

    if path == "/":
        return render_template(
            "family_access.html",
            baby_name=getattr(public_client, "baby_name", "宝宝"),
            next_url="/",
        )

    return redirect(f"/family-access?next={quote(next_path or '/', safe='')}")


@app.after_request
def apply_mobile_browser_compat_headers(response):
    try:
        path = request.path or "/"
        if _is_problematic_mobile_browser():
            response.headers["Alt-Svc"] = "clear"

        if path == "/family-access":
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    except Exception as e:
        logger.warning(f"兼容响应头写入失败: {e}")

    return response

# 调试信息
logger.info(f"模板文件夹: {template_folder}")
logger.info(f"静态文件夹: {static_folder}")
logger.info(f"模板是否存在: {os.path.exists(template_folder)}")
logger.info(f"静态文件夹是否存在: {os.path.exists(static_folder)}")


# 添加 datetime_format 过滤器
@app.template_filter("datetime_format")
def datetime_format(value, format="%Y-%m-%d %H:%M"):
    """格式化日期时间"""
    if isinstance(value, str):
        try:
            # 尝试解析 ISO 格式字符串
            from datetime import datetime

            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except:
            return value
    if hasattr(value, "strftime"):
        return value.strftime(format)
    return value


@app.context_processor
def inject_quota_info():
    """自动注入 AI 使用记录到模板。"""
    now = time.time()
    cached = TEMPLATE_QUOTA_CACHE.get("data")
    if cached and now < TEMPLATE_QUOTA_CACHE.get("expires_at", 0):
        return cached

    token_usage = {"total_tokens": 0, "total_prompt": 0, "total_completion": 0}
    image_usage = {"image_count": 0, "request_count": 0}
    ai_balance = 0.0
    ai_balance_enabled = False
    fetched_any = False

    if (
        public_client
        and hasattr(public_client, "client_id")
        and public_client.client_id
    ):
        try:
            resp = public_client.signed_request(
                "GET",
                f"{public_client.server_url}/czrz/client/token-usage",
                params={"client_id": public_client.client_id},
                timeout=TEMPLATE_REMOTE_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    token_usage = data.get("token_usage", token_usage)
                    image_usage = data.get("image_usage", image_usage)
                    ai_balance = data.get("ai_balance", ai_balance)
                    ai_balance_enabled = data.get("ai_balance_enabled", ai_balance_enabled)
                    fetched_any = True
        except:
            pass

    result = dict(
        quota_used=0,
        quota_total=0,
        quota_remaining=0,
        quota_percent=0,
        token_usage=token_usage,
        image_usage=image_usage,
        ai_balance=ai_balance,
        ai_balance_enabled=ai_balance_enabled,
    )

    if fetched_any or not cached:
        TEMPLATE_QUOTA_CACHE["data"] = result
        TEMPLATE_QUOTA_CACHE["expires_at"] = now + TEMPLATE_QUOTA_CACHE_TTL
        return result

    return cached


@app.context_processor
def inject_help_info():
    return {
        "help_topics": HELP_TOPICS,
        "help_topic_order": HELP_TOPIC_ORDER,
        "help_page_topics": [HELP_TOPICS[key] for key in HELP_TOPIC_ORDER if key != "overview"],
    }


@app.context_processor
def inject_android_companion_info():
    apk_path = get_android_companion_apk_path()
    user_agent = (request.headers.get("User-Agent") or "").lower()
    is_companion_app = "mumucompanion" in user_agent
    return {
        "android_companion_apk_available": bool(apk_path),
        "android_companion_apk_name": apk_path.name if apk_path else "",
        "android_companion_apk_url": "/android-companion/apk" if apk_path else "",
        "android_companion_page_url": "/android-companion",
        "is_companion_app": is_companion_app,
    }


# 全局客户端实例（在文件末尾创建，使用延迟连接）
public_client = None

# 本地模式标志（服务端不可用或被禁用时为True）
LOCAL_MODE = False
# 客户端被禁用标志
CLIENT_DISABLED = False


@app.route("/")
def index():
    """首页 - 首屏快速渲染，远程数据交给前端异步补齐"""
    from datetime import datetime

    perf_started = time.perf_counter()
    perf_parts = {}

    date_param = request.args.get("date")
    if date_param:
        try:
            datetime.strptime(date_param, "%Y-%m-%d")
            display_date = date_param
        except ValueError:
            display_date = datetime.now().strftime("%Y-%m-%d")
    else:
        display_date = datetime.now().strftime("%Y-%m-%d")

    if public_client.is_first_run():
        return redirect("/setup")

    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    display_datetime = datetime.strptime(display_date, "%Y-%m-%d")
    weekday = weekdays[display_datetime.weekday()]
    today = display_date

    client_id = public_client.client_id

    # 获取农历信息
    lunar_date = None
    step_started = time.perf_counter()
    try:
        from calendar_utils import get_calendar_info

        calendar_info = get_calendar_info(display_datetime.date())
        lunar_date = calendar_info.get("lunar_display")
    except Exception as e:
        print(f"[WARN] 获取农历失败: {e}")
    perf_parts["calendar_ms"] = round((time.perf_counter() - step_started) * 1000, 1)

    local_url = f"http://{public_client.get_local_ip()}:{public_client.client_port}"

    today_messages = []
    today_photos = []
    step_started = time.perf_counter()
    try:
        from photo_manager import PhotoManager
        from pathlib import Path

        media_folders = getattr(public_client, "media_folders", [])
        if media_folders:
            pm = PhotoManager(media_folders, public_client.data_dir)
            photo_list = pm.get_photos_by_date(display_date)
            for photo_info in photo_list:
                from database import get_photo_tag

                tag_info = get_photo_tag(
                    public_client.client_id, photo_info["filename"]
                )
                today_photos.append(
                    {
                        "filename": photo_info["filename"],
                        "path": photo_info.get("path", photo_info["filename"]),
                        "hash": photo_info.get("hash", ""),
                        "caption": tag_info.get("tag", "") if tag_info else "",
                        "date": display_date,
                        "is_video": Path(photo_info["filename"]).suffix.lower()
                        in [".mp4", ".mov", ".avi"],
                        "tag": tag_info,
                    }
                )
    except Exception as e:
        print(f"[WARN] 扫描照片失败：{e}")
    perf_parts["photo_scan_ms"] = round(
        (time.perf_counter() - step_started) * 1000, 1
    )

    perf_parts["theme_ms"] = 0.0

    default_photo = None
    default_is_video = False
    if today_photos:
        for p in today_photos:
            if not p.get("is_video"):
                default_photo = p.get("filename")
                break
        if not default_photo:
            default_photo = today_photos[0].get("filename")
            default_is_video = today_photos[0].get("is_video", False)

    # 首屏优先使用本地已缓存内容，避免依赖公网接口。
    featured_photo = None
    featured_photo_hash = None
    weather = None
    daily_story = None
    today_log = None
    news_titles = []
    news_item_display = None
    step_started = time.perf_counter()
    try:
        from database import get_featured_photo, get_log_dict

        if client_id:
            cached_log = get_log_dict(client_id, display_date)
            if cached_log and cached_log.get("content"):
                today_log = cached_log
            if cached_log and cached_log.get("weather"):
                weather = cached_log.get("weather")
            if cached_log:
                cached_calendar = cached_log.get("calendar") or {}
                if cached_calendar.get("weekday"):
                    weekday = cached_calendar.get("weekday")
                if cached_calendar.get("lunar"):
                    lunar_date = cached_calendar.get("lunar")
                news_titles = normalize_news_titles(cached_log.get("news"))
                if news_titles:
                    news_item_display = news_titles[0]

            local_featured = get_featured_photo(client_id, display_date)
            if local_featured:
                featured_photo = local_featured.get("filename") or None
                featured_photo_hash = local_featured.get("file_hash") or None

            daily_story = load_daily_story(display_date, client_id)
    except Exception as e:
        logger.info(f"[PERF] 首页读取本地缓存失败 {display_date}: {e}")
    perf_parts["local_cache_ms"] = round(
        (time.perf_counter() - step_started) * 1000, 1
    )

    initial_date_payload = {
        "success": True,
        "date": display_date,
        "is_today": display_date == datetime.now().strftime("%Y-%m-%d"),
        "photos": today_photos,
        "has_content": bool(today_photos or today_log or daily_story),
        "featured_photo": featured_photo,
        "featured_photo_hash": featured_photo_hash,
        "weather": weather,
        "lunar": lunar_date,
        "weekday": weekday,
        "story": daily_story,
        "log": {
            "content": today_log.get("content", ""),
            "generated_at": today_log.get("generated_at"),
        }
        if today_log and today_log.get("content")
        else None,
        "news": news_titles,
    }

    html = render_template(
        "index.html",
        baby_name=public_client.baby_name,
        client_id=public_client.client_id,
        is_paid=public_client.is_paid,
        subdomain=public_client.subdomain,
        public_url=public_client.public_url,
        today=today,
        weekday=weekday,
        lunar_date=lunar_date,
        today_log=today_log,
        news_item_display=news_item_display,
        local_url=local_url,
        client_version=getattr(public_client, "client_version", "2.0.0"),
        today_messages=today_messages,
        today_photos=today_photos,
        avatar_url=getattr(
            public_client, "avatar_url", DEFAULT_AVATAR_URL
        ),
        initial_avatar_url=INLINE_AVATAR_PLACEHOLDER,
        user_city=getattr(public_client, "user_city", ""),
        local_mode=LOCAL_MODE,
        client_disabled=CLIENT_DISABLED,
        default_photo=default_photo,
        default_is_video=default_is_video,
        featured_photo=featured_photo,
        featured_photo_hash=featured_photo_hash,
        daily_story=daily_story,
        index_after_date=getattr(public_client, "index_after_date", ""),
        weather=weather,
        initial_date_payload=initial_date_payload,
        inline_style_css=get_inline_asset_text("static/css/style.css"),
        inline_main_js=get_inline_asset_text("static/js/main.js"),
        defer_fontawesome=True,
        comic_style_presets=get_comic_style_presets(),
    )

    perf_parts["total_ms"] = round((time.perf_counter() - perf_started) * 1000, 1)
    logger.info(
        "[PERF] 首页首屏完成: date=%s source=%s parts=%s",
        display_date,
        "local" if is_local_request() else "public",
        perf_parts,
    )
    return html


@app.route("/help")
def help_page():
    """帮助中心。"""
    current_topic = normalize_help_topic(request.args.get("topic"))
    local_url = f"http://{public_client.get_local_ip()}:{public_client.client_port}"
    return render_template(
        "help.html",
        baby_name=public_client.baby_name,
        avatar_url=getattr(public_client, "avatar_url", DEFAULT_AVATAR_URL),
        client_id=public_client.client_id,
        public_url=public_client.public_url,
        local_url=local_url,
        baby_gender=getattr(public_client, "baby_gender", ""),
        baby_birthday=getattr(public_client, "baby_birthday", ""),
        user_city=getattr(public_client, "user_city", ""),
        local_mode=LOCAL_MODE,
        client_disabled=CLIENT_DISABLED,
        current_help_topic=current_topic,
        help_boundary_guide=HELP_BOUNDARY_GUIDE,
        help_faq=HELP_FAQ,
    )


CARD_CATEGORY_META = {
    "all": {
        "label": "全部卡片",
        "description": "把所有成长卡片混合成一条可以翻阅的画廊。",
    },
    "album": {
        "label": "时光相册",
        "description": "优先挑出更适合分享的照片，装订成一条有时间感的长卷预告。",
    },
    "comic": {
        "label": "漫画卡片",
        "description": "集中查看从照片墙手动生成的漫画图卡，以及虚拟角色卡。",
    },
    "moments": {
        "label": "精彩瞬间",
        "description": "把相册型、里程碑型和日常精选卡片集中浏览。",
    },
    "comparison": {
        "label": "成长对比",
        "description": "集中看时间变化、表情变化和同题材对照。",
    },
    "insight": {
        "label": "画像洞察",
        "description": "集中查看画像、情绪、陪伴与统计类卡片。",
    },
    "tags": {
        "label": "标签卡片",
        "description": "按手动标签和照片主题聚合浏览，不再单独拆到别的入口。",
    },
}

LEGACY_STORY_CARD_TYPES = {
    "story_character_card",
    "story_episode_card",
    "story_quote_card",
    "story_skill_card",
    "story_weather_card",
    "story_update_card",
    "care_guide_card",
}


def get_card_category_key(card: dict) -> str:
    card_type = (card or {}).get("type") or (card or {}).get("card_type") or ""
    if card_type == "time_album_card":
        return "album"
    if card_type in {"generated_comic_card", "virtual_role_card"}:
        return "comic"
    if card_type in {"tag_collage_card", "user_annotation_card", "collage_card"}:
        return "tags"
    if card_type in {
        "comparison_card",
        "expression_mimic_card",
        "same_day_different_year_card",
        "four_seasons_wardrobe_card",
    }:
        return "comparison"
    if card_type in {
        "data_portrait_card",
        "emotion_weather_card",
        "companion_stats_card",
    }:
        return "insight"
    return "moments"


def filter_visible_cards(cards: list) -> list:
    visible = []
    for card in cards or []:
        card_type = (card or {}).get("type") or (card or {}).get("card_type") or ""
        if card_type in LEGACY_STORY_CARD_TYPES:
            continue
        visible.append(card)
    return visible


def normalize_card_category(category: str) -> str:
    text = (category or "").strip().lower()
    return text if text in CARD_CATEGORY_META else "all"


def filter_cards_by_category(cards: list, category: str) -> list:
    category = normalize_card_category(category)
    if category == "all":
        return list(cards or [])
    return [card for card in (cards or []) if get_card_category_key(card) == category]


def summarize_card_categories(cards: list) -> list:
    all_cards = list(cards or [])
    summary = []
    for key, meta in CARD_CATEGORY_META.items():
        count = len(all_cards) if key == "all" else len(filter_cards_by_category(all_cards, key))
        summary.append(
            {
                "key": key,
                "label": meta["label"],
                "description": meta["description"],
                "count": count,
            }
        )
    return summary


def _extract_media_date_hint(filename: str) -> str:
    text = Path(filename or "").name
    if re.match(r"^\d{4}-\d{2}-\d{2}_", text):
        return text.split("_", 1)[0]
    return ""


def _build_synthetic_tag_cards(client_id: str, limit_tags: int = 12) -> list:
    try:
        from database import get_all_photo_tags

        tag_map = get_all_photo_tags(client_id) or {}
    except Exception as e:
        logger.info(f"[Card] 读取标签卡片素材失败: {e}")
        return []

    grouped = {}
    for filename, info in tag_map.items():
        tag_text = (info.get("tag") or "").strip()
        if not filename or not tag_text:
            continue
        grouped.setdefault(tag_text, []).append(
            {
                "path": filename,
                "date": _extract_media_date_hint(filename),
                "note": (info.get("note") or "").strip(),
            }
        )

    ranked_groups = sorted(
        grouped.items(),
        key=lambda item: (
            -len(item[1]),
            max((photo.get("date") or "") for photo in item[1]),
            item[0],
        ),
        reverse=False,
    )
    ranked_groups = sorted(
        ranked_groups,
        key=lambda item: (-len(item[1]), max((photo.get("date") or "") for photo in item[1]), item[0]),
    )

    cards = []
    for index, (tag_text, photos) in enumerate(ranked_groups[:limit_tags], start=1):
        ordered = sorted(
            photos,
            key=lambda photo: (photo.get("date") or "", photo.get("path") or ""),
        )
        cover = ordered[-4:] if len(ordered) > 4 else ordered
        note_texts = _story_dedup_texts(
            [item.get("note", "") for item in ordered if item.get("note")],
            limit=2,
            max_len=18,
        )
        cards.append(
            {
                "id": f"synthetic_tag_{index}_{hashlib.md5(tag_text.encode('utf-8')).hexdigest()[:8]}",
                "type": "tag_collage_card",
                "card_subtype": "manual_tag_group",
                "layout": "tag_collage",
                "tag": tag_text,
                "title": f"🏷️ {tag_text}时刻",
                "subtitle": f"把和“{tag_text}”有关的照片集中翻阅",
                "content": note_texts[0]
                if note_texts
                else f"当前共整理出 {len(ordered)} 张带“{tag_text}”标注的照片",
                "footer": "标签照片已并入成长卡片",
                "photos": cover,
                "photo_paths": [item.get("path", "") for item in cover if item.get("path")],
                "photo": cover[0].get("path", "") if cover else "",
            }
        )
    return cards


def augment_cards_with_synthetic_categories(cards: list, client_id: str) -> list:
    all_cards = list(cards or [])
    if any(get_card_category_key(card) == "tags" for card in all_cards):
        return all_cards
    return all_cards + _build_synthetic_tag_cards(client_id)


def augment_cards_with_local_manual_cards(cards: list) -> list:
    combined = list(cards or [])
    seen_ids = {
        str(card.get("id") or card.get("card_id") or "").strip()
        for card in combined
        if isinstance(card, dict)
    }
    for card in load_local_manual_cards():
        card_id = str(card.get("id") or card.get("card_id") or "").strip()
        if not card_id or card_id in seen_ids:
            continue
        seen_ids.add(card_id)
        combined.append(card)
    return combined


@app.route("/cards")
def cards_page():
    """成长卡片页面"""
    baby_name = USER_CONFIG.get("baby_name", "宝宝")
    initial_category = normalize_card_category(request.args.get("category") or "all")
    category_counts = summarize_card_categories([])
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()
        sync_card_cache_silently()
        all_cards = [
            convert_photo_path_for_client(card) for card in cache.get_all_cards()
        ]
        all_cards = filter_visible_cards(all_cards)
        all_cards = augment_cards_with_synthetic_categories(
            all_cards, public_client.client_id
        )
        all_cards = augment_cards_with_local_manual_cards(all_cards)
        category_counts = summarize_card_categories(all_cards)
        cards = filter_cards_by_category(all_cards, initial_category)
        cards = select_rotating_cards(cards, per_day=50)

        for card in cards:
            card_id = card.get("id", "")
            card["shared"] = cache.is_shared(card_id)
    except Exception as e:
        print(f"[Card] 读取缓存失败: {e}")
        cards = []

    avatar_url = getattr(
        public_client, "avatar_url", DEFAULT_AVATAR_URL
    )

    return render_template(
        "cards.html",
        baby_name=baby_name,
        cards=cards,
        avatar_url=avatar_url,
        initial_category=initial_category,
        category_counts=category_counts,
    )


@app.route("/setup")
def setup():
    """初次设置页面"""
    return render_template(
        "setup.html",
        is_first_run=public_client.is_first_run(),
        baby_name=getattr(public_client, "baby_name", ""),
        baby_gender=getattr(public_client, "baby_gender", ""),
        baby_birthday=getattr(public_client, "baby_birthday", ""),
        user_city=getattr(public_client, "user_city", ""),
        media_folders=getattr(public_client, "media_folders", []),
    )


@app.route("/family-access")
def family_access():
    """公网首次访问家庭码验证页"""
    if public_client.is_first_run():
        return redirect("/setup")

    source_ip = get_request_source_ip()
    if _is_private_or_loopback_ip(source_ip) or is_family_code_verified():
        next_url = request.args.get("next", "/")
        if not next_url.startswith("/"):
            next_url = "/"
        return redirect(next_url)

    next_url = request.args.get("next", "/")
    if not next_url.startswith("/"):
        next_url = "/"

    return render_template(
        "family_access.html",
        baby_name=getattr(public_client, "baby_name", "宝宝"),
        next_url=next_url,
    )


@app.route("/api/family-access", methods=["POST"])
def api_family_access():
    """校验家庭码并写入会话"""
    try:
        data = request.get_json(silent=True) or {}
        family_code = (data.get("family_code") or "").strip()
        next_url = data.get("next", "/")
        if not isinstance(next_url, str) or not next_url.startswith("/"):
            next_url = "/"

        if verify_family_code(family_code):
            mark_family_code_verified()
            return jsonify({"success": True, "message": "验证通过", "next": next_url})

        source_ip = get_request_source_ip()
        logger.warning(f"[SECURITY] 家庭码错误: ip={source_ip}, path={request.path}")
        return (
            jsonify({"success": False, "message": "家庭码错误，请询问家人或朋友"}),
            401,
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/setup", methods=["POST"])
def api_setup():
    """初次设置API"""
    try:
        data = request.get_json()
        baby_name = data.get("baby_name", "").strip()
        baby_gender = data.get("baby_gender", "").strip()
        baby_birthday = data.get("baby_birthday", "").strip()
        user_city = data.get("user_city", "").strip()
        log_style = data.get("log_style", "简练").strip()
        custom_style = data.get("custom_style", "").strip()
        media_folders = data.get("media_folders", [])
        family_code = data.get("family_code", "").strip()

        if not baby_name:
            return jsonify({"success": False, "message": "请输入宝宝名字"})

        if not media_folders or len(media_folders) == 0:
            return jsonify({"success": False, "message": "请至少选择一个文件夹"})

        if not public_client.admin_password and not family_code:
            return jsonify({"success": False, "message": "初次设置需要设置家庭码"})

        from pathlib import Path

        valid_folders = []
        for folder in media_folders:
            if folder.strip():
                folder_path = Path(folder.strip())
                if not folder_path.exists():
                    try:
                        folder_path.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        return jsonify(
                            {
                                "success": False,
                                "message": f"无法创建文件夹 {folder}: {e}",
                            }
                        )
                valid_folders.append(str(folder_path))

        public_client.baby_name = baby_name
        public_client.baby_gender = baby_gender if baby_gender else None
        public_client.baby_birthday = baby_birthday if baby_birthday else None
        public_client.user_city = user_city if user_city else None
        public_client.log_style = log_style if log_style else "简练"
        public_client.custom_style = custom_style
        public_client.media_folders = valid_folders

        index_after_date = data.get("index_after_date", "").strip()
        if index_after_date:
            public_client.index_after_date = index_after_date

        if family_code:
            public_client.admin_password = family_code

        # 保存压缩设置（如果提供了）
        compression_settings = data.get("compression_settings")
        if compression_settings:
            try:
                from video_compressor import get_compression_manager

                manager = get_compression_manager()
                if manager:
                    manager.save_settings(compression_settings)
                    print(f"💾 已保存初始压缩设置: {compression_settings}")
            except Exception as e:
                print(f"⚠️ 保存压缩设置失败: {e}")

        public_client.save_config()

        # 设置更新后，刷新依赖媒体目录的管理器
        try:
            from video_compressor import init_compression_manager

            init_compression_manager(
                public_client.data_dir,
                [Path(folder) for folder in valid_folders if Path(folder).exists()],
            )
        except Exception as e:
            print(f"⚠️ 刷新压缩管理器失败: {e}")

        try:
            from media_folder_shrinker import init_media_folder_shrinker

            init_media_folder_shrinker(
                public_client.data_dir,
                [Path(folder) for folder in valid_folders if Path(folder).exists()],
            )
        except Exception as e:
            print(f"⚠️ 刷新媒体压缩管理器失败: {e}")

        is_first_registration = not public_client.client_id
        if is_first_registration:
            print("🚀 首次设置完成，正在注册到服务端...")
            public_client.connect_to_public_server(
                background_subdomain=True,
                auto_build_index=False,
            )

        # 检查索引状态
        index_info = public_client.init_photo_index()

        return jsonify(
            {
                "success": True,
                "message": "设置完成",
                "baby_name": baby_name,
                "folders_count": len(valid_folders),
                "is_first_registration": is_first_registration,
                "index_info": index_info,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"设置失败: {str(e)}"})


@app.route("/api/photo-index/build", methods=["POST"])
@require_local_or_password
def build_photo_index_api():
    """执行照片索引建立（首次，后台执行）"""
    try:
        global scan_progress

        if scan_progress["running"]:
            return jsonify(
                {
                    "success": True,
                    "started": False,
                    "message": "索引建立已在进行中",
                }
            )

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "error": "未配置媒体文件夹"})

        start_date = getattr(public_client, "index_after_date", "") or None
        scan_progress = {
            "running": True,
            "current": 0,
            "total": 0,
            "message": "正在准备创建索引...",
            "result": None,
        }

        thread = threading.Thread(
            target=run_scan_in_background,
            args=(media_folders, public_client.data_dir, start_date, None),
            daemon=True,
        )
        thread.start()

        return jsonify(
            {
                "success": True,
                "started": True,
                "message": "索引建立已开始",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/photo-index/progress", methods=["GET"])
@require_local_or_password
def get_photo_index_progress():
    """获取首次索引建立进度"""
    global scan_progress
    return jsonify(scan_progress)


@app.route("/api/settings/current", methods=["GET"])
def get_current_settings():
    """获取当前设置"""
    try:
        return jsonify(
            {
                "success": True,
                "baby_name": getattr(public_client, "baby_name", ""),
                "baby_gender": getattr(public_client, "baby_gender", ""),
                "baby_birthday": getattr(public_client, "baby_birthday", ""),
                "user_city": getattr(public_client, "user_city", ""),
                "log_style": getattr(public_client, "log_style", "简练"),
                "custom_style": getattr(public_client, "custom_style", ""),
                "media_folders": getattr(public_client, "media_folders", []),
                "server_url": getattr(public_client, "server_url", ""),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def _choose_folder_with_native_dialog(current_path: str = "") -> str:
    initial_dir = ""
    if current_path:
        try:
            current = Path(current_path).expanduser()
            if current.is_file():
                current = current.parent
            if current.exists():
                initial_dir = str(current)
            elif current.parent.exists():
                initial_dir = str(current.parent)
        except Exception:
            initial_dir = ""

    if not initial_dir:
        initial_dir = str(Path.home())

    display_available = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    zenity_path = shutil.which("zenity")
    if zenity_path and display_available:
        cmd = [
            zenity_path,
            "--file-selection",
            "--directory",
            "--title=选择媒体文件夹",
            f"--filename={initial_dir.rstrip(os.sep) + os.sep}",
        ]
        try:
            import subprocess

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode == 0:
                selected = (result.stdout or "").strip()
                if selected:
                    return selected
            if result.returncode in {1, 5}:
                return ""
        except Exception as exc:
            logger.warning(f"本机目录选择器 zenity 调用失败: {exc}")

    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(
            title="选择媒体文件夹",
            initialdir=initial_dir,
            mustexist=False,
        )
        root.destroy()
        return (selected or "").strip()
    except Exception as exc:
        logger.warning(f"本机目录选择器 tkinter 调用失败: {exc}")

    raise RuntimeError("当前环境无法打开目录选择器，请继续手动输入路径")


@app.route("/api/settings/select-folder", methods=["POST"])
@require_local_or_password
def select_media_folder():
    """打开本机目录选择器，仅允许本机回环访问。"""
    try:
        if not is_loopback_request():
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "目录选择器只允许在本机浏览器中使用，远程访问时请手动输入路径",
                    }
                ),
                403,
            )

        data = request.get_json(silent=True) or {}
        current_path = (data.get("current_path") or "").strip()
        selected = _choose_folder_with_native_dialog(current_path)
        if not selected:
            return jsonify({"success": False, "cancelled": True, "message": "已取消选择"})
        return jsonify({"success": True, "path": str(Path(selected).expanduser())})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/share/family-text", methods=["GET"])
@require_local_or_password
def get_family_share_text():
    """生成给亲友的分享文案（含访问链接与家庭码）"""
    try:
        share_url = (
            getattr(public_client, "public_url", "")
            or request.host_url.rstrip("/")
        )
        family_code = get_family_code()
        baby_name = getattr(public_client, "baby_name", "宝宝")

        text_lines = [
            f"👶 {baby_name}的成长记录",
            f"访问链接：{share_url}",
            f"家庭码：{family_code or '（未设置）'}",
            "说明：首次打开输入家庭码，之后会自动记住。",
        ]
        text = "\n".join(text_lines)

        return jsonify(
            {
                "success": True,
                "text": text,
                "url": share_url,
                "family_code": family_code,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/settings/restart", methods=["POST"])
@require_local_or_password
def restart_client_service_api():
    """后台触发客户端重启：仅重启当前客户端与其 tunnel，不影响服务端"""
    try:
        base_dir = Path(__file__).resolve().parent
        restart_log = base_dir / "restart_client_from_web.log"

        import subprocess

        # 只处理客户端：清理旧客户端进程与客户端 cloudflared，然后拉起新客户端进程。
        # 延迟1秒启动，确保当前请求先返回给前端。
        cmd = (
            f"sleep 1; "
            f"cd '{base_dir}' && "
            f"echo \"[$(date '+%F %T')] web restart requested\" >> '{restart_log}'; "
            f"pkill -9 -f 'python.*client_public_final.py' 2>/dev/null || true; "
            "for pid in $(pgrep -u \"$(id -u)\" cloudflared 2>/dev/null || true); do "
            "  CMDLINE=$(cat /proc/$pid/cmdline 2>/dev/null | tr '\\0' ' ' || true); "
            "  if echo \"$CMDLINE\" | grep -qE 'cloudflared_combined|cloudflared_lab|cloudflared_config|xiaohexia|tunnel_config'; then "
            "    continue; "
            "  fi; "
            "  kill -9 $pid 2>/dev/null || true; "
            "done; "
            "for port in {3000..3010}; do "
            "  PIDS=$(lsof -ti :$port 2>/dev/null || true); "
            "  if [ -n \"$PIDS\" ]; then echo \"$PIDS\" | xargs kill -9 2>/dev/null || true; fi; "
            "done; "
            "if [ -d venv ]; then source venv/bin/activate || true; fi; "
            "nohup python client_public_final.py > client_service.log 2>&1 &"
        )
        subprocess.Popen(
            ["bash", "-lc", cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        return jsonify(
            {
                "success": True,
                "message": "客户端重启任务已启动（仅当前客户端）",
                "log_file": str(restart_log),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/profile")
def profile_page():
    """成长画像页面"""
    if public_client.is_first_run():
        return redirect("/setup")

    baby_birthday = getattr(public_client, "baby_birthday", None)
    age_text = ""
    if baby_birthday:
        from datetime import datetime

        try:
            birth = datetime.strptime(baby_birthday, "%Y-%m-%d")
            today = datetime.now()
            months = (today.year - birth.year) * 12 + (today.month - birth.month)
            days = today.day - birth.day
            if days < 0:
                months -= 1
            if months >= 12:
                years = months // 12
                remaining_months = months % 12
                if remaining_months > 0:
                    age_text = f"{years}岁{remaining_months}个月"
                else:
                    age_text = f"{years}岁"
            else:
                age_text = f"{months}个月"
        except:
            age_text = ""

    return render_template(
        "profile.html",
        baby_name=public_client.baby_name,
        baby_birthday=baby_birthday,
        age_text=age_text,
        client_id=public_client.client_id,
        avatar_url=getattr(
            public_client, "avatar_url", DEFAULT_AVATAR_URL
        ),
    )


@app.route("/settings")
def settings():
    """设置页面 - 重定向到首页，使用模态框"""
    return redirect("/")


@app.route("/usage")
def usage():
    """使用明细页面"""
    return render_template("usage.html")


@app.route("/health")
def health():
    """健康检查"""
    return jsonify(
        {
            "status": "ok",
            "client_id": public_client.client_id,
            "baby_name": public_client.baby_name,
            "is_paid": public_client.is_paid,
            "subdomain": public_client.subdomain,
            "public_url": public_client.public_url,
            "server_domain": public_client.server_domain,
            "server_connected": public_client.client_id is not None,
            "time": datetime.now().isoformat(),
            "version": "public-final-2.0.1",
        }
    )


@app.route("/api/usage/list")
def usage_list():
    """使用明细列表 - 从服务端获取"""
    try:
        filter_type = request.args.get("filter", "today")
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/client/usage-list",
            params={"client_id": public_client.client_id, "filter": filter_type},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "today_tokens": data.get("today_tokens", 0),
                        "today_image_count": data.get("today_image_count", 0),
                        "total_tokens": data.get("total_tokens", 0),
                        "total_image_count": data.get("total_image_count", 0),
                        "records": data.get("records", []),
                    }
                )
        return jsonify({"success": False, "message": "获取使用明细失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/photo/<filename>")
def get_photo(filename):
    """获取照片"""
    from flask import send_file
    from pathlib import Path

    try:
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        # 递归搜索所有媒体文件夹
        for folder in media_folders:
            folder_path = Path(folder)
            for file_path in folder_path.rglob(filename):
                if file_path.is_file():
                    return send_file(file_path, max_age=3600)

        return jsonify({"success": False, "message": "照片不存在"}), 404

    except Exception as e:
        print(f"[ERROR] 获取照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/photo/hash/<file_hash>")
def get_photo_by_hash(file_hash):
    """通过哈希获取照片 - 用于精选照片显示"""
    from flask import send_file
    from pathlib import Path

    try:
        from photo_manager import PhotoManager
        from video_compressor import get_compression_manager

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_hash(file_hash)

        if not entry or not Path(entry["path"]).exists():
            return jsonify({"success": False, "message": "照片不存在"}), 404

        # 获取压缩设置
        manager = get_compression_manager()
        if manager:
            settings = manager.get_settings()
            if settings.get("image_quality") != "原图" and not entry.get(
                "is_video", False
            ):
                compressed_path = manager.get_compressed_file_path(
                    Path(entry["path"]), entry["filename"]
                )
                if compressed_path and compressed_path.exists():
                    return send_file(compressed_path, max_age=86400)

        return send_file(Path(entry["path"]), max_age=3600)

    except Exception as e:
        print(f"[ERROR] 通过哈希获取照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ==================== 缩略图和视频服务 ====================

# 缩略图缓存目录
THUMB_DIR = USER_DATA_DIR / "thumbnails"
THUMB_DIR.mkdir(exist_ok=True)
PHOTO_THUMB_SIZE = (400, 400)
ALBUM_IMAGE_SIZE = (1400, 1400)


def ensure_default_video_thumb() -> Path:
    """生成客户端默认视频缩略图，避免读取源码静态资源。"""
    target = GENERATED_ASSETS_DIR / "default-video-thumb.jpg"
    if target.exists():
        return target

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 300), color="#2f3d4a")
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((135, 85, 265, 215), radius=28, fill="#f6f1e8")
    draw.polygon([(178, 120), (178, 180), (235, 150)], fill="#2f3d4a")
    draw.text((146, 238), "VIDEO", fill="#f6f1e8")
    img.save(target, quality=90)
    return target


def ensure_default_avatar() -> Path:
    """生成客户端默认头像，避免读取源码静态资源。"""
    target = GENERATED_ASSETS_DIR / "default-avatar.png"
    if target.exists():
        return target

    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (320, 320), color=(248, 240, 225, 255))
    draw = ImageDraw.Draw(img)
    draw.ellipse((26, 26, 294, 294), fill=(164, 195, 178, 255))
    draw.ellipse((110, 72, 210, 172), fill=(248, 240, 225, 255))
    draw.rounded_rectangle((86, 165, 234, 265), radius=52, fill=(248, 240, 225, 255))
    img.save(target)
    return target


def get_android_companion_apk_path():
    """返回安卓伴侣 APK 路径；没有现成包时返回 None。"""
    candidate_dirs = [
        Path(__file__).resolve().parent / "landing_page" / "download",
        USER_DATA_DIR / "android_companion",
        USER_DATA_DIR / "downloads",
    ]
    patterns = [
        "mumu成长助手*.apk",
        "mumu-android*.apk",
        "mumu-companion*.apk",
        "mumu*.apk",
    ]

    candidates = []
    for folder in candidate_dirs:
        if not folder.exists():
            continue
        for pattern in patterns:
            candidates.extend(folder.glob(pattern))

    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        return None
    candidates.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0]


def get_or_create_thumbnail(
    image_path: Path, size=(400, 400), cache_tag: str = "thumb"
) -> Path:
    """生成或获取照片缩略图（本地处理，自动修正EXIF方向）"""
    width, height = int(size[0]), int(size[1])
    safe_tag = re.sub(r"[^a-z0-9_-]+", "_", str(cache_tag or "thumb").lower())
    thumb_filename = (
        f"{image_path.stem}_{safe_tag}_{width}x{height}{image_path.suffix}"
    )
    thumb_path = THUMB_DIR / thumb_filename

    # 如果缩略图已存在且比原图新，直接返回
    if thumb_path.exists():
        if thumb_path.stat().st_mtime >= image_path.stat().st_mtime:
            return thumb_path

    # 生成缩略图
    try:
        from PIL import Image
        from PIL import ImageOps

        with Image.open(image_path) as img:
            # 根据EXIF方向自动旋转（解决手机照片方向问题）
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass  # 没有EXIF或处理失败，保持原样

            # 转换为RGB（处理RGBA等模式）
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # 等比例缩放
            img.thumbnail((width, height), Image.Resampling.LANCZOS)
            img.save(thumb_path, quality=85, optimize=True)

        return thumb_path
    except Exception as e:
        print(f"[WARN] 生成缩略图失败: {e}")
        return image_path  # 失败时返回原图


def get_or_create_video_thumbnail(video_path: Path) -> Path:
    """生成或获取视频缩略图（本地处理）
    优先使用 imageio-ffmpeg（自动处理旋转），失败时尝试 OpenCV
    """
    thumb_filename = f"{video_path.stem}_vthumb.jpg"
    thumb_path = THUMB_DIR / thumb_filename

    # 如果缩略图已存在且比原视频新，直接返回
    if thumb_path.exists():
        if thumb_path.stat().st_mtime >= video_path.stat().st_mtime:
            return thumb_path

    # 方法1: 使用 imageio-ffmpeg（自动处理旋转）
    try:
        import subprocess

        ffmpeg_path = None
        try:
            import imageio_ffmpeg

            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        except:
            pass

        if ffmpeg_path:
            cmd = [
                ffmpeg_path,
                "-y",
                "-i",
                str(video_path),
                "-ss",
                "00:00:01",
                "-vframes",
                "1",
                "-vf",
                "scale=400:-1",
                "-q:v",
                "2",
                str(thumb_path),
            ]

            subprocess.run(cmd, capture_output=True, timeout=300)

            if thumb_path.exists():
                return thumb_path
            else:
                raise Exception("ffmpeg 未能生成缩略图")

    except Exception as e:
        print(f"[INFO] imageio-ffmpeg 生成缩略图失败: {e}，尝试 OpenCV...")

    # 方法2: 使用 OpenCV
    try:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise Exception("无法打开视频文件")

        # 获取视频旋转信息
        rotation = 0
        try:
            # 尝试获取旋转元数据
            meta = int(cap.get(cv2.CAP_PROP_ORIENTATION_META))
            if meta == 90:
                rotation = cv2.ROTATE_90_CLOCKWISE
            elif meta == 180:
                rotation = cv2.ROTATE_180
            elif meta == 270:
                rotation = cv2.ROTATE_90_COUNTERCLOCKWISE
        except:
            pass

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frame_pos = int(fps * 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)

        ret, frame = cap.read()
        cap.release()

        if ret and frame is not None:
            # 应用旋转
            if rotation != 0:
                frame = cv2.rotate(frame, rotation)

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            from PIL import Image

            img = Image.fromarray(frame)
            img.thumbnail((400, 400), Image.Resampling.LANCZOS)
            img.save(thumb_path, quality=85, optimize=True)
            return thumb_path
        else:
            raise Exception("无法读取视频帧")

    except Exception as e1:
        print(f"[INFO] OpenCV 生成缩略图失败: {e1}")

    return ensure_default_video_thumb()


def _resolve_photo_path_by_filename(filename: str) -> Path | None:
    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders:
        return None

    from photo_manager import PhotoManager

    pm = PhotoManager(media_folders, public_client.data_dir)
    entry = pm.get_photo_by_filename(filename)
    if entry:
        photo_path = Path(entry["path"])
        if photo_path.exists():
            return photo_path

    for folder in media_folders:
        folder_path = Path(folder)
        for file_path in folder_path.rglob(filename):
            if file_path.is_file():
                return file_path
    return None


@app.route("/photo/thumb/<filename>")
def get_photo_thumb(filename):
    """获取照片缩略图"""
    from flask import send_file

    photo_path = None
    try:
        if not getattr(public_client, "media_folders", []):
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404
        photo_path = _resolve_photo_path_by_filename(filename)
        if not photo_path:
            return jsonify({"success": False, "message": "照片不存在"}), 404

        thumb_path = get_or_create_thumbnail(
            photo_path,
            size=PHOTO_THUMB_SIZE,
            cache_tag="thumb",
        )
        return send_file(thumb_path, max_age=86400)

    except Exception as e:
        print(f"[ERROR] 获取缩略图失败: {e}")
        if photo_path and photo_path.exists():
            try:
                print(f"[INFO] 缩略图失败，回退原图: {photo_path}")
                return send_file(photo_path, max_age=3600)
            except Exception as fallback_error:
                print(f"[ERROR] 回退原图也失败: {fallback_error}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/photo/album/<filename>")
def get_photo_album_image(filename):
    """获取时光相册专用中尺寸图片。"""
    from flask import send_file

    photo_path = None
    try:
        if not getattr(public_client, "media_folders", []):
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404
        photo_path = _resolve_photo_path_by_filename(filename)
        if not photo_path:
            return jsonify({"success": False, "message": "照片不存在"}), 404

        album_path = get_or_create_thumbnail(
            photo_path,
            size=ALBUM_IMAGE_SIZE,
            cache_tag="album",
        )
        return send_file(album_path, max_age=86400)

    except Exception as e:
        print(f"[ERROR] 获取时光相册图片失败: {e}")
        if photo_path and photo_path.exists():
            try:
                print(f"[INFO] 相册图失败，回退原图: {photo_path}")
                return send_file(photo_path, max_age=3600)
            except Exception as fallback_error:
                print(f"[ERROR] 回退原图也失败: {fallback_error}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/video/thumb/<filename>")
def get_video_thumb(filename):
    """获取视频缩略图（通过索引查找）"""
    from flask import send_file

    try:
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_filename(filename)

        if not entry:
            return jsonify({"success": False, "message": "视频不在索引中"}), 404

        video_path = Path(entry["path"])
        if not video_path.exists():
            return jsonify({"success": False, "message": "视频文件不存在"}), 404

        thumb_path = get_or_create_video_thumbnail(video_path)
        return send_file(thumb_path, max_age=86400)

    except Exception as e:
        print(f"[ERROR] 获取视频缩略图失败: {e}")
        return send_file(ensure_default_video_thumb(), max_age=86400)


@app.route("/video/<filename>")
def get_video(filename):
    """获取视频文件 - 根据设置返回原视频或压缩版"""
    from flask import send_file

    try:
        from photo_manager import PhotoManager
        from video_compressor import get_compression_manager

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_filename(filename)

        if not entry or not Path(entry["path"]).exists():
            return jsonify({"success": False, "message": "视频不存在"}), 404

        # 获取压缩设置
        manager = get_compression_manager()
        if manager:
            settings = manager.get_settings()
            # 如果设置不是原图，尝试返回压缩版
            if settings.get("video_quality") != "原图":
                compressed_path = manager.get_compressed_file_path(
                    Path(entry["path"]), filename
                )
                if compressed_path and compressed_path.exists():
                    return send_file(
                        compressed_path,
                        mimetype="video/mp4",
                        conditional=True,
                        download_name=filename,
                    )

        # 返回原视频
        return send_file(
            Path(entry["path"]),
            mimetype="video/mp4",
            conditional=True,
            download_name=filename,
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/voice/<filename>")
def get_voice(filename):
    """获取语音文件 - 从服务端代理"""
    from flask import Response

    try:
        # 安全检查：防止目录遍历
        if ".." in filename or "/" in filename:
            return jsonify({"success": False, "message": "无效文件名"}), 400

        # 从服务端获取语音文件
        upstream_headers = {}
        range_header = request.headers.get("Range")
        if range_header:
            upstream_headers["Range"] = range_header

        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/voice/{public_client.client_id}/{filename}",
            headers=upstream_headers,
            timeout=30,
            stream=True,
        )

        if response.status_code in (200, 206):
            passthrough_headers = {}
            for header_name in (
                "Content-Type",
                "Content-Length",
                "Content-Range",
                "Accept-Ranges",
                "Cache-Control",
                "ETag",
                "Last-Modified",
            ):
                header_value = response.headers.get(header_name)
                if header_value:
                    passthrough_headers[header_name] = header_value
            passthrough_headers["Content-Disposition"] = f"inline; filename={filename}"

            return Response(
                response.iter_content(chunk_size=8192),
                status=response.status_code,
                headers=passthrough_headers,
                direct_passthrough=True,
            )
        else:
            return jsonify(
                {"success": False, "message": "语音文件不存在", "filename": filename}
            ), 404

    except Exception as e:
        return jsonify({"success": False, "message": f"获取语音失败: {str(e)}"}), 500


@app.route("/api/photo/delete", methods=["POST"])
@require_local_or_password
def delete_photo():
    """删除照片 - 从索引中移除（不删除实际文件）"""
    try:
        data = request.get_json()
        filename = data.get("filename")

        if not filename:
            return jsonify({"success": False, "message": "缺少文件名"})

        # 使用 PhotoManager 从索引中移除
        media_folders = getattr(public_client, "media_folders", [])
        if media_folders:
            from photo_manager import PhotoManager

            pm = PhotoManager(media_folders, public_client.data_dir)
            success = pm.remove_photo(filename)

            if success:
                return jsonify(
                    {
                        "success": True,
                        "message": "照片已从索引中移除（文件仍保留在原始位置）",
                    }
                )

        return jsonify({"success": False, "message": "照片不存在或移除失败"})

    except Exception as e:
        return jsonify({"success": False, "message": f"删除失败: {str(e)}"})


@app.route("/api/photo/<filename>")
def get_photo_detail(filename):
    """获取照片详情 - 使用索引"""
    try:
        from photo_manager import PhotoManager

        media_folders = getattr(public_client, "media_folders", [])

        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"})

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_filename(filename)

        if entry:
            return jsonify(
                {
                    "success": True,
                    "filename": entry["filename"],
                    "path": entry["path"],
                    "date": entry["date"],
                    "url": f"/photo/{filename}",
                    "is_video": entry.get("is_video", False),
                    "size": entry.get("size", 0),
                }
            )
        else:
            return jsonify({"success": False, "message": "照片不存在"})

    except Exception as e:
        return jsonify({"success": False, "message": f"查询失败: {str(e)}"})


# ==================== 照片上传API ====================


def _get_upload_photo_manager():
    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders:
        raise ValueError("未配置媒体保存文件夹，请在设置中配置")

    from photo_manager import PhotoManager

    return PhotoManager(media_folders, public_client.data_dir)


def _save_uploaded_file_items(files, caption: str = "", upload_date: str = "") -> dict:
    """保存上传文件，供普通上传和系统分享导入共用。"""
    if not files:
        raise ValueError("没有选择文件")

    pm = _get_upload_photo_manager()

    saved_files = []
    skipped_files = []
    for file in files:
        if not file or not file.filename:
            continue

        safe_filename = secure_filename(file.filename)
        ext = Path(safe_filename).suffix.lower()
        is_video = ext in {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv"}

        target_path = pm.get_upload_target_path(safe_filename)
        if target_path.exists():
            print(f"⏭️ 跳过已存在文件: {safe_filename}")
            skipped_files.append(safe_filename)
            continue

        partial_path = target_path.with_name(f".{safe_filename}.uploading")
        partial_path.unlink(missing_ok=True)

        try:
            file.save(partial_path)

            if target_path.exists():
                print(f"⏭️ 并发上传已存在文件: {safe_filename}")
                skipped_files.append(safe_filename)
                partial_path.unlink(missing_ok=True)
                continue

            partial_path.replace(target_path)
            result = pm.register_saved_upload_with_date(
                target_path,
                safe_filename,
                upload_date,
                fast_hash=is_video,
            )
        except PermissionError:
            raise
        except Exception:
            partial_path.unlink(missing_ok=True)
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            raise

        if caption:
            result["caption"] = caption

        saved_files.append(result)

    return {
        "saved_files": saved_files,
        "skipped_files": skipped_files,
    }


UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024


def _get_upload_sessions_dir() -> Path:
    sessions_dir = Path(public_client.data_dir) / "upload_sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir


def _cleanup_upload_session_dir(session_dir: Path) -> None:
    try:
        shutil.rmtree(session_dir, ignore_errors=True)
    except Exception as e:
        logger.warning(f"清理上传会话失败: {e}")


def _get_upload_session_paths(upload_id: str) -> tuple[Path, Path, Path]:
    session_dir = _get_upload_sessions_dir() / upload_id
    meta_path = session_dir / "meta.json"
    partial_path = session_dir / "payload.part"
    return session_dir, meta_path, partial_path


def _load_upload_session_meta(upload_id: str) -> tuple[dict, Path, Path, Path]:
    session_dir, meta_path, partial_path = _get_upload_session_paths(upload_id)
    if not meta_path.exists():
        raise FileNotFoundError("上传会话不存在或已过期")

    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    return meta, session_dir, meta_path, partial_path


def _save_upload_session_meta(meta_path: Path, meta: dict) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)


def _commit_upload_session_file(partial_path: Path, target_path: Path) -> None:
    """将分片合并后的临时文件安全提交到目标路径，兼容跨磁盘目录。"""
    try:
        partial_path.replace(target_path)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    temp_target = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            dir=str(target_path.parent),
            prefix=f".{target_path.name}.",
            suffix=".part",
        ) as tmp_file:
            temp_target = Path(tmp_file.name)
            with open(partial_path, "rb") as src_file:
                shutil.copyfileobj(src_file, tmp_file, length=UPLOAD_CHUNK_SIZE)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        temp_target.replace(target_path)
        partial_path.unlink(missing_ok=True)
    except Exception:
        if temp_target and temp_target.exists():
            temp_target.unlink(missing_ok=True)
        raise


@app.route("/api/upload/sessions", methods=["POST"])
@require_local_or_password
def create_upload_session():
    """创建分片上传会话，避免大批量上传受单次请求大小限制。"""
    try:
        payload = request.get_json(silent=True) or request.form
        original_filename = (payload.get("filename") or "").strip()
        if not original_filename:
            return jsonify({"success": False, "message": "文件名为空"}), 400

        safe_filename = secure_filename(original_filename)
        if not safe_filename:
            return jsonify({"success": False, "message": "文件名无效"}), 400

        upload_date = (payload.get("date") or "").strip()
        caption = payload.get("caption", "")
        mime_type = (payload.get("mime_type") or "").strip()
        try:
            total_size = int(payload.get("total_size") or 0)
        except Exception:
            total_size = 0

        pm = _get_upload_photo_manager()
        target_path = pm.get_upload_target_path(safe_filename)
        if target_path.exists():
            return jsonify(
                {
                    "success": True,
                    "already_exists": True,
                    "message": f"{safe_filename} 已存在，已跳过",
                    "count": 0,
                    "skipped_count": 1,
                    "skipped_files": [safe_filename],
                }
            )

        upload_id = uuid.uuid4().hex
        session_dir, meta_path, partial_path = _get_upload_session_paths(upload_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "upload_id": upload_id,
            "filename": safe_filename,
            "original_filename": original_filename,
            "upload_date": upload_date,
            "caption": caption,
            "mime_type": mime_type,
            "total_size": total_size,
            "received_size": 0,
            "next_chunk_index": 0,
            "total_chunks": None,
            "created_at": datetime.now().isoformat(),
        }
        _save_upload_session_meta(meta_path, meta)
        partial_path.touch(exist_ok=True)

        return jsonify(
            {
                "success": True,
                "upload_id": upload_id,
                "chunk_size": UPLOAD_CHUNK_SIZE,
            }
        )
    except Exception as e:
        logger.exception(f"创建上传会话失败: {e}")
        return jsonify({"success": False, "message": f"创建上传会话失败: {str(e)}"}), 500


@app.route("/api/upload/sessions/<upload_id>/chunk", methods=["POST"])
@require_local_or_password
def upload_session_chunk(upload_id: str):
    """接收单个分片并顺序追加到临时文件。"""
    try:
        meta, _, meta_path, partial_path = _load_upload_session_meta(upload_id)

        chunk_file = request.files.get("chunk")
        if not chunk_file:
            return jsonify({"success": False, "message": "缺少分片文件"}), 400

        try:
            chunk_index = int(request.form.get("chunk_index", "-1"))
        except Exception:
            chunk_index = -1

        if chunk_index < 0:
            return jsonify({"success": False, "message": "分片序号无效"}), 400

        expected_index = int(meta.get("next_chunk_index", 0))
        if chunk_index != expected_index:
            return jsonify(
                {
                    "success": False,
                    "message": f"分片顺序不匹配，期望 {expected_index}，收到 {chunk_index}",
                    "next_chunk_index": expected_index,
                }
            ), 409

        total_chunks_raw = request.form.get("total_chunks")
        if total_chunks_raw not in (None, ""):
            try:
                meta["total_chunks"] = int(total_chunks_raw)
            except Exception:
                pass

        before_size = partial_path.stat().st_size if partial_path.exists() else 0
        with open(partial_path, "ab") as f:
            shutil.copyfileobj(chunk_file.stream, f, length=1024 * 1024)

        meta["received_size"] = partial_path.stat().st_size
        meta["next_chunk_index"] = expected_index + 1
        _save_upload_session_meta(meta_path, meta)

        return jsonify(
            {
                "success": True,
                "received_size": meta["received_size"],
                "chunk_size": meta["received_size"] - before_size,
                "next_chunk_index": meta["next_chunk_index"],
            }
        )
    except FileNotFoundError as e:
        return jsonify({"success": False, "message": str(e)}), 404
    except Exception as e:
        logger.exception(f"上传分片失败: {e}")
        return jsonify({"success": False, "message": f"上传分片失败: {str(e)}"}), 500


@app.route("/api/upload/sessions/<upload_id>/complete", methods=["POST"])
@require_local_or_password
def complete_upload_session(upload_id: str):
    """合并分片并落盘到最终目录，然后建立索引。"""
    target_path = None
    try:
        payload = request.get_json(silent=True) or request.form
        meta, session_dir, _, partial_path = _load_upload_session_meta(upload_id)

        total_chunks = meta.get("total_chunks")
        total_chunks_raw = payload.get("total_chunks") if payload else None
        if total_chunks_raw not in (None, ""):
            try:
                total_chunks = int(total_chunks_raw)
            except Exception:
                pass

        next_chunk_index = int(meta.get("next_chunk_index", 0))
        if total_chunks is not None and next_chunk_index < total_chunks:
            return jsonify(
                {
                    "success": False,
                    "message": f"分片未上传完整，已收到 {next_chunk_index}/{total_chunks}",
                }
            ), 400

        if not partial_path.exists():
            return jsonify({"success": False, "message": "上传临时文件不存在"}), 400

        pm = _get_upload_photo_manager()
        filename = meta["filename"]
        target_path = pm.get_upload_target_path(filename)
        if target_path.exists():
            _cleanup_upload_session_dir(session_dir)
            return jsonify(
                {
                    "success": True,
                    "message": f"{filename} 已存在，已跳过",
                    "count": 0,
                    "files": [],
                    "skipped_count": 1,
                    "skipped_files": [filename],
                }
            )

        target_path.parent.mkdir(parents=True, exist_ok=True)
        _commit_upload_session_file(partial_path, target_path)

        ext = Path(filename).suffix.lower()
        is_video = ext in {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv"}
        result = pm.register_saved_upload_with_date(
            target_path,
            filename,
            meta.get("upload_date") or "",
            fast_hash=is_video,
        )
        if meta.get("caption"):
            result["caption"] = meta["caption"]

        _cleanup_upload_session_dir(session_dir)
        return jsonify(
            {
                "success": True,
                "message": "成功上传 1 个文件",
                "count": 1,
                "files": [result],
                "skipped_count": 0,
                "skipped_files": [],
            }
        )
    except FileNotFoundError as e:
        return jsonify({"success": False, "message": str(e)}), 404
    except PermissionError as e:
        return jsonify(
            {
                "success": False,
                "message": "保存文件失败：目标文件夹没有写入权限，请检查文件夹权限或更换保存位置",
            }
        ), 500
    except Exception as e:
        logger.exception(f"完成上传会话失败: {e}")
        if target_path and target_path.exists():
            target_path.unlink(missing_ok=True)
        try:
            session_dir, _, partial_path = _get_upload_session_paths(upload_id)
            if not partial_path.exists():
                _cleanup_upload_session_dir(session_dir)
        except Exception:
            pass
        return jsonify({"success": False, "message": f"完成上传失败: {str(e)}"}), 500


@app.route("/upload", methods=["POST"])
@require_local_or_password
def upload_photos():
    """上传文件到本地文件夹"""
    try:
        files = request.files.getlist("files")
        caption = request.form.get("caption", "")
        upload_date = request.form.get("date", "")
        result = _save_uploaded_file_items(files, caption=caption, upload_date=upload_date)
        saved_files = result["saved_files"]
        skipped_files = result["skipped_files"]

        message = f"成功上传 {len(saved_files)} 个文件"
        if skipped_files:
            message += f"，跳过 {len(skipped_files)} 个已存在文件"

        return jsonify(
            {
                "success": True,
                "message": message,
                "count": len(saved_files),
                "files": saved_files,
                "skipped_count": len(skipped_files),
                "skipped_files": skipped_files,
            }
        )

    except PermissionError as e:
        print(f"[ERROR] 上传权限错误: {e}")
        return jsonify(
            {
                "success": False,
                "message": "保存文件失败：目标文件夹没有写入权限，请检查文件夹权限或更换保存位置",
            }
        )
    except Exception as e:
        print(f"[ERROR] 上传失败: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": f"上传失败: {str(e)}"})


@app.route("/android-companion")
def android_companion_page():
    """安卓伴侣入口：直接跳下载或首页，不再单独保留说明页。"""
    apk_path = get_android_companion_apk_path()
    if apk_path:
        return redirect("/android-companion/apk")
    return redirect("/")


@app.route("/android-companion/apk")
def download_android_companion_apk():
    """下载安卓伴侣 APK。"""
    apk_path = get_android_companion_apk_path()
    if not apk_path:
        return redirect("/android-companion")
    return send_file(
        apk_path,
        mimetype="application/vnd.android.package-archive",
        as_attachment=True,
        download_name=apk_path.name,
        max_age=3600,
    )


@app.route("/api/settings/log-style", methods=["POST"])
@require_local_or_password
def save_log_style():
    """保存日志风格设置"""
    try:
        data = request.get_json()
        log_style = data.get("log_style", "简练").strip()
        custom_style = data.get("custom_style", "").strip()

        public_client.log_style = log_style
        public_client.custom_style = custom_style
        public_client.save_config()

        return jsonify(
            {
                "success": True,
                "message": "日志风格已保存",
                "log_style": log_style,
                "custom_style": custom_style,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"保存失败: {str(e)}"})


@app.route("/api/settings/avatar", methods=["POST"])
@require_local_or_password
def upload_avatar():
    """上传头像"""
    try:
        from PIL import Image

        if "avatar" not in request.files:
            return jsonify({"success": False, "message": "没有选择文件"})

        file = request.files["avatar"]
        if not file or not file.filename:
            return jsonify({"success": False, "message": "文件名为空"})

        # 检查文件类型
        allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        ext = Path(file.filename).suffix.lower()
        if ext not in allowed_extensions:
            return jsonify(
                {"success": False, "message": "仅支持 JPG, PNG, GIF, WebP 格式"}
            )

        # 创建头像目录
        avatar_dir = USER_DATA_DIR / "avatars"
        avatar_dir.mkdir(exist_ok=True)

        # 保存并压缩头像
        avatar_owner = public_client.client_id or "local"
        avatar_filename = f"avatar_{avatar_owner}{ext}"
        avatar_path = avatar_dir / avatar_filename

        # 保存文件
        file.save(avatar_path)

        # 压缩并裁剪为圆形
        with Image.open(avatar_path) as img:
            # 转换为RGBA
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            # 裁剪为正方形（从中心）
            size = min(img.size)
            left = (img.width - size) // 2
            top = (img.height - size) // 2
            img = img.crop((left, top, left + size, top + size))

            # 缩放为 200x200
            img = img.resize((200, 200), Image.Resampling.LANCZOS)

            # 创建圆形遮罩
            mask = Image.new("L", (200, 200), 0)
            from PIL import ImageDraw

            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, 200, 200), fill=255)

            # 应用遮罩
            output = Image.new("RGBA", (200, 200), (255, 255, 255, 0))
            output.paste(img, (0, 0))
            output.putalpha(mask)

            # 保存
            output.save(avatar_path, "PNG")

        # 更新配置
        avatar_url = normalize_avatar_url(f"/api/avatar/{avatar_filename}")
        public_client.avatar_url = avatar_url
        public_client.save_config()

        return jsonify(
            {"success": True, "message": "头像上传成功", "avatar_url": avatar_url}
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"上传失败: {str(e)}"})


@app.route("/api/avatar/<filename>")
def get_avatar(filename):
    """获取头像文件"""
    try:
        avatar_dir = USER_DATA_DIR / "avatars"
        avatar_path = avatar_dir / filename

        if avatar_path.exists():
            return send_file(avatar_path, mimetype="image/png", max_age=86400)
        else:
            return send_file(ensure_default_avatar(), mimetype="image/png", max_age=86400)

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route(DEFAULT_AVATAR_URL)
def get_default_avatar():
    return send_file(ensure_default_avatar(), mimetype="image/png", max_age=86400)


@app.route(DEFAULT_VIDEO_THUMB_URL)
def get_default_video_thumb():
    return send_file(ensure_default_video_thumb(), mimetype="image/jpeg", max_age=86400)


@app.route("/api/photos/by_date/<date>")
def get_photos_by_date(date):
    """获取指定日期的照片"""
    try:
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"})

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)

        photos = pm.get_photos_by_date(date)

        return jsonify(
            {"success": True, "date": date, "count": len(photos), "photos": photos}
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


scan_progress = {
    "running": False,
    "current": 0,
    "total": 0,
    "message": "",
    "result": None,
}


def scan_progress_callback(current, total, message):
    """扫描进度回调"""
    global scan_progress
    scan_progress["current"] = current
    scan_progress["total"] = total
    scan_progress["message"] = message


def run_scan_in_background(media_folders, data_dir, start_date=None, end_date=None):
    """后台扫描线程"""
    global scan_progress
    from photo_manager import PhotoManager

    try:
        pm = PhotoManager(media_folders, data_dir)
        result = pm.scan_existing_photos(
            progress_callback=scan_progress_callback,
            start_date=start_date,
            end_date=end_date,
        )
        scan_progress["result"] = result
        scan_progress["message"] = (
            f"扫描完成：新增 {result['new']} 个，已有 {result['existing']} 个，总计 {result['total']} 个"
        )
    except Exception as e:
        scan_progress["result"] = {"error": str(e)}
        scan_progress["message"] = f"扫描失败：{str(e)}"
    finally:
        scan_progress["running"] = False


@app.route("/api/photos/scan", methods=["POST"])
@require_local_or_password
def scan_photos():
    """手动触发照片扫描（后台执行）"""
    global scan_progress

    if scan_progress["running"]:
        return jsonify({"success": False, "message": "扫描正在进行中，请稍候"})

    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders:
        return jsonify({"success": False, "message": "未配置媒体文件夹"})

    # 获取起始日期参数
    data = request.get_json() or {}
    start_date = data.get("start_date", "")

    # 保存日期范围设置到配置
    if start_date:
        public_client.index_after_date = start_date
        public_client.save_config()

    scan_progress = {
        "running": True,
        "current": 0,
        "total": 0,
        "message": "正在启动扫描...",
        "result": None,
    }

    import threading

    thread = threading.Thread(
        target=run_scan_in_background,
        args=(media_folders, public_client.data_dir, start_date or None, None),
    )
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "message": "扫描已开始"})


@app.route("/api/photos/scan/progress", methods=["GET"])
def get_scan_progress():
    """获取扫描进度"""
    global scan_progress
    return jsonify(scan_progress)


@app.route("/api/calendar/<month>")
def get_calendar_data(month):
    """获取指定月份的日历数据 - 优化版本，使用增量缓存"""
    try:
        year, month_num = map(int, month.split("-"))
        import calendar
        from datetime import datetime

        # 获取该月第一天和最后一天
        first_day = 1
        last_day = calendar.monthrange(year, month_num)[1]

        # 获取照片日期 - 优先使用按月缓存
        media_folders = getattr(public_client, "media_folders", [])
        photo_dates = set()

        if media_folders:
            cache_key = f"_photo_dates_cache_{month}"
            cache_time_key = f"_photo_dates_cache_time_{month}"

            # 检查缓存是否有效（5分钟内）
            cache_valid = False
            if hasattr(app, cache_key) and hasattr(app, cache_time_key):
                cache_age = (
                    datetime.now() - getattr(app, cache_time_key)
                ).total_seconds()
                if cache_age < 300:  # 5分钟缓存
                    cache_valid = True
                    photo_dates = getattr(app, cache_key)

            if not cache_valid:
                # 缓存失效，重新加载
                from photo_manager import PhotoManager

                pm = PhotoManager(media_folders, public_client.data_dir)

                # 使用优化的按月查询方法
                photo_dates = set(pm.get_dates_by_month(year, month_num))

                # 更新缓存
                setattr(app, cache_key, photo_dates)
                setattr(app, cache_time_key, datetime.now())

        # 从服务端获取该月日志状态
        log_dates = set()
        try:
            resp = public_client.signed_request(
                "GET",
                f"{public_client.server_url}/czrz/client/logs/dates",
                params={"client_id": public_client.client_id, "month": month},
                timeout=30,
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    log_dates = set(result.get("dates", []))
        except Exception as e:
            print(f"[WARN] 获取日志日期失败: {e}")

        # 为每一天生成数据
        calendar_data = {}
        for day in range(first_day, last_day + 1):
            date_str = f"{year}-{month_num:02d}-{day:02d}"

            has_photos = date_str in photo_dates
            has_log = date_str in log_dates

            calendar_data[date_str] = {
                "has_content": has_photos or has_log,
                "has_photos": has_photos,
                "has_log": has_log,
            }

        return jsonify({"success": True, "month": month, "data": calendar_data})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/date/<date>")
def get_date_details(date):
    """获取指定日期的详细信息（AJAX调用）- 优先从已保存数据读取"""
    try:
        from datetime import datetime

        request_started_at = time.perf_counter()
        fast_mode = request.args.get("fast", "").strip().lower() in {"1", "true", "yes"}
        skip_messages = request.args.get("skip_messages", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }

        is_today = date == datetime.now().strftime("%Y-%m-%d")

        # 获取照片
        photo_scan_started_at = time.perf_counter()
        media_folders = getattr(public_client, "media_folders", [])
        photos = []
        if media_folders:
            from photo_manager import PhotoManager
            from database import get_photo_tag

            pm = PhotoManager(media_folders, public_client.data_dir)
            photo_list = pm.get_photos_by_date(date)

            # 添加标签信息
            for photo_info in photo_list:
                tag_info = get_photo_tag(
                    public_client.client_id, photo_info["filename"]
                )
                photo_info["tag"] = tag_info
                photo_info["caption"] = tag_info.get("tag", "") if tag_info else ""
                photos.append(photo_info)
        photo_scan_elapsed_ms = int((time.perf_counter() - photo_scan_started_at) * 1000)

        response_data = {
            "date": date,
            "is_today": is_today,
            "photos": photos,
            "has_content": len(photos) > 0,
            "featured_photo": None,
            "featured_photo_hash": None,
            "story": None,
        }
        user_city = getattr(public_client, "user_city", None) or "上海"
        client_id = public_client.client_id

        try:
            from database import get_featured_photo, get_log_dict

            cached_log = get_log_dict(client_id, date) if client_id else None
            if cached_log:
                if cached_log.get("content"):
                    response_data["log"] = {
                        "content": cached_log.get("content", ""),
                        "generated_at": cached_log.get("generated_at")
                        or datetime.now().isoformat(),
                    }
                    response_data["has_content"] = True

                weather_payload = cached_log.get("weather")
                if weather_payload:
                    response_data["weather"] = weather_payload

                calendar_payload = cached_log.get("calendar") or {}
                if calendar_payload.get("lunar"):
                    response_data["lunar"] = calendar_payload.get("lunar")
                if calendar_payload.get("weekday"):
                    response_data["weekday"] = calendar_payload.get("weekday")

                cached_news = cached_log.get("news")
                normalized_news = normalize_news_titles(cached_news)
                if normalized_news:
                    response_data["news"] = normalized_news

            local_featured = get_featured_photo(client_id, date) if client_id else None
            if local_featured:
                response_data["featured_photo"] = (
                    local_featured.get("filename") or None
                )
                response_data["featured_photo_hash"] = (
                    local_featured.get("file_hash") or None
                )

            local_story = load_daily_story(date, client_id)
            if local_story:
                response_data["story"] = local_story
                response_data["has_content"] = True
        except Exception as e:
            logger.info(f"[PERF] 读取本地日志缓存失败 {date}: {e}")

        if fast_mode:
            response_data["deferred_remote"] = True
            total_elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
            logger.info(
                "[PERF] /api/date/%s fast total=%sms photos=%sms photos_count=%s cached_log=%s",
                date,
                total_elapsed_ms,
                photo_scan_elapsed_ms,
                len(photos),
                bool(response_data.get("log")),
            )
            return jsonify({"success": True, **response_data})

        def fetch_featured_payload():
            started_at = time.perf_counter()
            payload = {"featured_photo": None, "featured_photo_hash": None}
            try:
                featured_info = get_featured_photo_info(
                    date,
                    client_id,
                    timeout=DATE_API_REMOTE_TIMEOUT,
                )
                print(
                    f"[DEBUG] 获取精选照片: date={date}, client_id={client_id}, info={featured_info}"
                )
                if featured_info:
                    payload["featured_photo"] = featured_info.get("filename") or ""
                    payload["featured_photo_hash"] = (
                        featured_info.get("file_hash") or ""
                    )
            except Exception as e:
                print(f"[WARN] 获取精选照片失败: {e}")
            payload["_elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
            return payload

        def fetch_log_payload():
            started_at = time.perf_counter()
            payload = {}
            try:
                log_url = (
                    f"{public_client.server_url}/czrz/baby/log?city={user_city}&date={date}"
                )
                if client_id:
                    log_url += f"&client_id={client_id}"

                log_resp = public_client.signed_request(
                    "GET",
                    log_url,
                    timeout=DATE_API_REMOTE_TIMEOUT,
                )
                if log_resp.status_code == 200:
                    resp_data = log_resp.json()
                    if resp_data.get("success"):
                        if resp_data.get("log"):
                            payload["log"] = {
                                "content": resp_data.get("log", ""),
                                "generated_at": resp_data.get(
                                    "generated_at", datetime.now().isoformat()
                                ),
                            }

                        if resp_data.get("weather"):
                            payload["weather"] = resp_data.get("weather")
                        if resp_data.get("lunar"):
                            payload["lunar"] = resp_data.get("lunar")
                        if resp_data.get("weekday"):
                            payload["weekday"] = resp_data.get("weekday")
                        if resp_data.get("news"):
                            payload["news"] = resp_data.get("news")
            except Exception as e:
                print(f"[WARN] 从服务端获取日志失败: {e}")
            payload["_elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
            return payload

        with ThreadPoolExecutor(max_workers=3) as executor:
            featured_future = executor.submit(fetch_featured_payload)
            log_future = executor.submit(fetch_log_payload)
            messages_future = None
            if not skip_messages:
                def fetch_messages_payload():
                    started_at = time.perf_counter()
                    payload = {"messages": []}
                    try:
                        if client_id:
                            messages_url = (
                                f"{public_client.server_url}/czrz/messages/{client_id}/{date}"
                            )
                            messages_resp = public_client.signed_request(
                                "GET",
                                messages_url,
                                timeout=DATE_API_REMOTE_TIMEOUT,
                            )
                            if messages_resp.status_code == 200:
                                messages_data = messages_resp.json()
                                if messages_data.get("success"):
                                    payload["messages"] = messages_data.get("messages", [])
                    except Exception as e:
                        print(f"[WARN] 从服务端获取留言失败: {e}")
                    payload["_elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
                    return payload

                messages_future = executor.submit(fetch_messages_payload)

            featured_payload = featured_future.result()
            log_payload = log_future.result()
            messages_payload = messages_future.result() if messages_future else {}

        featured_elapsed_ms = featured_payload.pop("_elapsed_ms", 0)
        log_elapsed_ms = log_payload.pop("_elapsed_ms", 0)
        messages_elapsed_ms = messages_payload.pop("_elapsed_ms", 0)

        response_data.update(featured_payload)
        response_data.update(log_payload)
        response_data.update(messages_payload)
        if response_data.get("log") or response_data.get("messages"):
            response_data["has_content"] = True

        total_elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
        logger.info(
            "[PERF] /api/date/%s total=%sms photos=%sms featured=%sms log=%sms messages=%sms photos_count=%s messages_count=%s has_log=%s",
            date,
            total_elapsed_ms,
            photo_scan_elapsed_ms,
            featured_elapsed_ms,
            log_elapsed_ms,
            messages_elapsed_ms,
            len(photos),
            len(response_data.get("messages") or []),
            bool(response_data.get("log")),
        )

        return jsonify({"success": True, **response_data})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/story-assets/<date>/<filename>")
def get_story_asset(date, filename):
    """获取本地缓存的故事面板图片。"""
    asset_dir = _story_dir(date, public_client.client_id)
    file_path = asset_dir / filename
    if not file_path.exists():
        return jsonify({"success": False, "message": "故事图片不存在"}), 404
    return send_from_directory(asset_dir, filename)


@app.route("/generated-assets/<category>/<filename>")
def get_generated_asset(category, filename):
    """获取本地生成素材，例如漫画卡生成图。"""
    asset_dir = _generated_asset_dir(category)
    file_path = asset_dir / filename
    if not file_path.exists():
        return jsonify({"success": False, "message": "生成素材不存在"}), 404
    return send_from_directory(asset_dir, filename)


@app.route("/story/<date>")
def daily_story_page(date):
    """旧四格故事页已下线，兼容跳转到同日期范围的时光相册。"""
    return redirect("/storybook")


@app.route("/api/story/<date>")
def api_get_story(date):
    return jsonify(
        {
            "success": False,
            "message": "四格故事功能已下线，请改用时光相册和漫画卡片。",
            "story": None,
        }
    )


@app.route("/api/story/status/<date>")
def api_story_status(date):
    job = {
        "date": date,
        "status": "disabled",
        "stage": "disabled",
        "message": "四格故事功能已下线",
        "progress": 0,
    }
    return jsonify({"success": True, "job": job, "story": None, "has_story": False})


@app.route("/api/story/generate", methods=["POST"])
@require_local_or_password
def api_generate_daily_story():
    """旧四格故事生成接口已下线。"""
    return jsonify(
        {
            "success": False,
            "message": "四格故事功能已下线，请改用时光相册和漫画卡片。",
        }
    ), 410


@app.route("/api/log/save", methods=["POST"])
@require_local_or_password
def api_save_log():
    """保存编辑后的日志到服务端"""
    try:
        data = request.get_json()
        content = data.get("content", "")
        date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        metadata = data.get("metadata", {})

        if not content:
            return jsonify({"success": False, "message": "内容不能为空"})

        # 上报到服务端数据库
        try:
            report_data = {
                "client_id": public_client.client_id,
                "date": date,
                "content": content,
                "source_kind": "manual",
                "allow_overwrite_manual": True,
                "metadata": {
                    "city": metadata.get("city", public_client.user_city or "未知"),
                    "temperature": metadata.get("temperature"),
                    "condition": metadata.get("condition") or metadata.get("weather"),
                    "lunar": metadata.get("lunar"),
                    "weekday": metadata.get("weekday"),
                    "news": metadata.get("news"),
                },
            }

            resp = post_with_retry(
                public_client.session,
                f"{public_client.server_url}/czrz/log/save",
                report_data,
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    return jsonify({"success": True, "message": "保存成功"})
                else:
                    return jsonify(
                        {"success": False, "message": result.get("message", "保存失败")}
                    )
            else:
                return jsonify(
                    {"success": False, "message": f"服务端错误: {resp.status_code}"}
                )
        except Exception as e:
            print(f"[ERROR] 上报服务端失败: {e}")
            return jsonify({"success": False, "message": f"保存失败: {str(e)}"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photo/tag", methods=["POST", "DELETE"])
@require_local_or_password
def api_photo_tag():
    """照片标签管理，上报服务端存储"""
    try:
        if request.method == "POST":
            data = request.get_json()
            filename = data.get("filename")
            tag = data.get("tag", "").strip()
            note = data.get("note", "").strip()

            if not filename:
                return jsonify({"success": False, "message": "文件名不能为空"})

            if not tag:
                return jsonify({"success": False, "message": "标签不能为空"})

            resp = public_client.signed_request(
                "POST",
                f"{public_client.server_url}/czrz/client/photo/tag",
                json={
                    "client_id": public_client.client_id,
                    "filename": filename,
                    "tag": tag,
                    "note": note,
                },
                timeout=30,
            )

            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    return jsonify(
                        {
                            "success": True,
                            "message": "标签保存成功",
                            "filename": filename,
                            "tag": tag,
                        }
                    )
                else:
                    return jsonify(
                        {"success": False, "message": result.get("message", "保存失败")}
                    )
            else:
                return jsonify({"success": False, "message": "服务端请求失败"})

        elif request.method == "DELETE":
            data = request.get_json()
            filename = data.get("filename")

            if not filename:
                return jsonify({"success": False, "message": "文件名不能为空"})

            resp = public_client.signed_request(
                "DELETE",
                f"{public_client.server_url}/czrz/client/photo/tag",
                json={
                    "client_id": public_client.client_id,
                    "filename": filename,
                },
                timeout=30,
            )

            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    return jsonify(
                        {"success": True, "message": "标签已删除", "filename": filename}
                    )
                else:
                    return jsonify(
                        {"success": False, "message": result.get("message", "删除失败")}
                    )
            else:
                return jsonify({"success": False, "message": "服务端请求失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photos/tagged")
def api_tagged_photos():
    """获取所有带标签的照片（从服务端获取）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/client/photos/tagged",
            params={"client_id": public_client.client_id},
            timeout=30,
        )

        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "count": result.get("count", 0),
                        "photos": result.get("photos", []),
                    }
                )
            else:
                return jsonify(
                    {"success": False, "message": result.get("message", "获取失败")}
                )
        else:
            return jsonify({"success": False, "message": "服务端请求失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/notifications")
def api_get_notifications():
    """获取服务端通知"""
    try:
        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/notifications",
            params={"client_id": public_client.client_id},
            timeout=30,
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({"success": False, "message": "获取失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/feedback", methods=["POST"])
@require_local_or_password
def api_submit_feedback():
    """提交用户反馈"""
    try:
        data = request.get_json()
        name = data.get("name", "匿名用户")
        content = data.get("content", "").strip()

        if not content:
            return jsonify({"success": False, "message": "反馈内容不能为空"})

        # 保存到本地文件
        feedback_file = USER_DATA_DIR / "feedback.json"
        feedback_list = []

        if feedback_file.exists():
            try:
                feedback_list = json.loads(feedback_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.info(f"[反馈] 读取本地反馈失败: {e}")

        feedback_entry = {
            "id": len(feedback_list) + 1,
            "name": name,
            "content": content,
            "client_id": public_client.client_id,
            "baby_name": public_client.baby_name,
            "submitted_at": datetime.now().isoformat(),
        }

        feedback_list.append(feedback_entry)
        feedback_file.write_text(
            json.dumps(feedback_list, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 上报到服务端
        try:
            public_client.signed_request(
                "POST",
                f"{public_client.server_url}/czrz/client/feedback",
                json=feedback_entry,
                timeout=30,
            )
        except Exception as e:
            print(f"[WARN] 上报反馈到服务端失败: {e}")

        print(f"[INFO] 收到用户反馈: {name} - {content[:50]}...")

        return jsonify({"success": True, "message": "感谢您的反馈！"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/notifications/mark-read", methods=["POST"])
@require_local_or_password
def api_mark_notification_read():
    """标记通知为已读"""
    try:
        data = request.get_json()
        notification_id = data.get("notification_id")

        response = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/notifications/mark-read",
            json={
                "client_id": public_client.client_id,
                "notification_id": notification_id,
            },
            timeout=30,
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({"success": False, "message": "标记失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# 检测是否为 Windows EXE 打包环境
def is_windows_exe():
    """检测是否运行在 Windows EXE 打包环境"""
    return hasattr(sys, "_MEIPASS") and sys.platform == "win32"


def get_exe_path():
    """获取 Windows EXE 的实际路径（非临时目录）"""
    if is_windows_exe():
        # sys.executable 指向真实的 EXE 路径
        return Path(sys.executable).resolve()
    return None


def get_manual_update_url():
    """手动更新入口（非 Windows EXE 环境使用）"""
    base = (public_client.server_url or "").rstrip("/")
    if not base:
        return "/"
    return f"{base}/download"


@app.route("/api/version/check")
def api_check_version():
    """检查版本更新（支持多平台）"""
    try:
        # 当前版本（从配置文件中获取）
        current_version = getattr(public_client, "client_version", "2.0.0")

        # 确定当前平台
        if is_windows_exe():
            platform = "windows"
        elif sys.platform == "linux" or sys.platform.startswith("linux"):
            platform = "linux"
        elif sys.platform == "darwin":
            platform = "macos"
        else:
            platform = "unknown"

        # 请求对应平台的版本信息
        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/version/latest?platform={platform}",
            timeout=30,
        )

        if response.status_code == 200:
            data = response.json()
            latest_version = data.get("version", current_version)

            # 版本比较（支持 x.y.z 格式）
            def version_tuple(v):
                try:
                    return tuple(map(int, v.split(".")))
                except:
                    return (0, 0, 0)

            has_update = version_tuple(latest_version) > version_tuple(current_version)

            # 检测是否为 Windows EXE 环境
            windows_exe = is_windows_exe()
            exe_path = str(get_exe_path()) if windows_exe else None

            auto_update_supported = is_windows_exe()
            return jsonify(
                {
                    "success": True,
                    "has_update": has_update,
                    "current_version": current_version,
                    "latest_version": latest_version,
                    "description": data.get("description", ""),
                    "download_url": data.get("download_url", ""),
                    "file_type": data.get("file_type", "auto"),  # exe, tar.gz, zip
                    "is_windows_exe": windows_exe,
                    "exe_path": exe_path,
                    "platform": platform,
                    "auto_update_supported": auto_update_supported,
                    "manual_update_required": not auto_update_supported,
                    "manual_update_url": get_manual_update_url(),
                }
            )
        else:
            return jsonify({"success": False, "message": "检查失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/version/download", methods=["POST"])
@require_local_or_password
def api_download_update():
    """下载更新（支持多平台格式）"""
    try:
        if not is_windows_exe():
            return jsonify(
                {
                    "success": False,
                    "manual_update_required": True,
                    "manual_update_url": get_manual_update_url(),
                    "message": "当前平台请手动下载新版本覆盖安装",
                }
            )

        data = request.get_json() or {}
        download_url = data.get("download_url", "")
        expected_md5 = data.get("md5", "")
        file_type = data.get("file_type", "auto")  # exe, tar.gz, zip, binary

        if not download_url:
            return jsonify({"success": False, "message": "缺少下载地址"})

        # 完整URL
        if download_url.startswith("/"):
            download_url = f"{public_client.server_url}{download_url}"

        # 下载目录
        update_dir = Path(tempfile.gettempdir()) / "czrz_updates"
        update_dir.mkdir(exist_ok=True)

        # 下载文件
        response = public_client.signed_request(
            "GET", download_url, timeout=600, stream=True
        )  # 增加超时时间用于大文件
        response.raise_for_status()

        # 根据文件类型确定保存的文件名
        original_filename = download_url.split("/")[-1]

        if file_type == "exe" or original_filename.endswith(".exe"):
            filename = f"client_update_{file_type}.exe"
        elif (
            file_type == "tar.gz"
            or original_filename.endswith(".tar.gz")
            or original_filename.endswith(".tgz")
        ):
            filename = f"client_update_{file_type}.tar.gz"
        elif file_type == "zip" or original_filename.endswith(".zip"):
            filename = f"client_update_{file_type}.zip"
        else:
            # 保持原始文件名
            filename = original_filename if original_filename else "client_update"

        update_file = update_dir / filename

        # 下载并显示进度
        downloaded = 0

        with open(update_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    # 可以在这里添加进度回调

        # 验证MD5
        if expected_md5:
            import hashlib

            md5_hash = hashlib.md5()
            with open(update_file, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5_hash.update(chunk)

            actual_md5 = md5_hash.hexdigest()
            if actual_md5 != expected_md5:
                update_file.unlink()
                return jsonify(
                    {"success": False, "message": f"MD5校验失败，文件可能已损坏"}
                )

        return jsonify(
            {
                "success": True,
                "message": "下载完成",
                "download_path": str(update_file),
                "filename": filename,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"下载失败: {str(e)}"})


@app.route("/api/version/install", methods=["POST"])
@require_local_or_password
def api_install_update():
    """安装更新 - 静默替换当前客户端"""
    try:
        if not is_windows_exe():
            return jsonify(
                {
                    "success": False,
                    "manual_update_required": True,
                    "manual_update_url": get_manual_update_url(),
                    "message": "当前平台不支持自动安装，请手动更新",
                }
            )

        import subprocess
        import os
        import sys

        data = request.get_json() or {}
        update_file = data.get("update_file", "")

        if not update_file or not os.path.exists(update_file):
            return jsonify({"success": False, "message": "更新文件不存在"})

        update_path = Path(update_file)

        # 获取服务端版本号用于更新配置
        try:
            version_resp = public_client.signed_request(
                "GET", f"{public_client.server_url}/czrz/version/latest", timeout=30
            )
            new_version = (
                version_resp.json().get("version", "2.0.0")
                if version_resp.status_code == 200
                else "2.0.0"
            )
        except:
            new_version = "2.0.0"

        # Windows EXE 打包环境的特殊处理
        if is_windows_exe():
            exe_path = get_exe_path()
            if not exe_path:
                return jsonify({"success": False, "message": "无法获取 EXE 路径"})

            # EXE 文件路径
            exe_dir = exe_path.parent
            backup_exe = exe_path.with_suffix(".exe.bak")
            update_exe = Path(update_file)

            # 创建 Windows 批处理更新脚本
            batch_script = exe_dir / "_update_helper.bat"
            vbs_script = exe_dir / "_update_helper.vbs"  # 用于隐藏窗口

            batch_content = f'''@echo off
chcp 65001 >nul
title CZRZ 更新助手
echo 正在更新，请稍候...
timeout /t 3 /nobreak >nul

REM 备份原文件
if exist "{exe_path}" (
    copy /Y "{exe_path}" "{backup_exe}" >nul 2>&1
)

REM 替换文件
copy /Y "{update_exe}" "{exe_path}" >nul 2>&1
if errorlevel 1 (
    echo 更新失败，恢复备份...
    if exist "{backup_exe}" (
        copy /Y "{backup_exe}" "{exe_path}" >nul 2>&1
    )
    pause
    exit /b 1
)

REM 更新版本号
echo 更新配置文件...
python -c "import json; f=open(r'{Path.home() / "Documents" / "CZRZ" / "config.json"}','r',encoding='utf-8'); d=json.load(f); f.close(); d['client_version']='{new_version}'; f=open(r'{Path.home() / "Documents" / "CZRZ" / "config.json"}','w',encoding='utf-8'); json.dump(d,f,indent=2,ensure_ascii=False); f.close()" 2>nul

REM 清理临时文件
del /F /Q "{update_exe}" 2>nul
del /F /Q "{backup_exe}" 2>nul
del /F /Q "{batch_script}" 2>nul
del /F /Q "{vbs_script}" 2>nul

REM 启动新版本
echo 启动新版本...
start "" "{exe_path}"

exit
'''

            # VBS 脚本用于隐藏窗口运行
            vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "{batch_script}" & Chr(34), 0
Set WshShell = Nothing
'''

            with open(batch_script, "w", encoding="utf-8") as f:
                f.write(batch_content)

            with open(vbs_script, "w", encoding="utf-8") as f:
                f.write(vbs_content)

            # 使用 WScript 启动隐藏的更新进程
            subprocess.Popen(
                ["wscript.exe", str(vbs_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )

            return jsonify({"success": True, "message": "更新程序已启动，即将重启..."})

        else:
            # 普通 Python 环境的更新逻辑
            current_file = Path(__file__).resolve()
            backup_file = current_file.with_suffix(".py.bak")

            # 创建更新脚本
            update_script = current_file.parent / "_update_helper.py"
            script_content = f'''#!/usr/bin/env python3
import os
import sys
import time
import shutil
import json
from pathlib import Path

# 等待原进程退出
time.sleep(3)

try:
    # 备份原文件
    if os.path.exists("{current_file}"):
        shutil.copy2("{current_file}", "{backup_file}")
    
    # 替换文件
    shutil.copy2("{update_path}", "{current_file}")
    os.chmod("{current_file}", 0o755)
    
    # 更新配置文件中的版本号
    try:
        config_file = Path.home() / "Documents" / "CZRZ" / "config.json"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            config['client_version'] = "{new_version}"
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"[✓] 版本号已更新为: {new_version}")
    except Exception as e:
        print(f"[WARN] 更新版本号失败: {{e}}")
    
    # 启动新版本
    if sys.platform == 'win32':
        os.startfile("{current_file}")
    else:
        os.system("cd {current_file.parent} && ./start_client.sh")
    
    # 清理
    os.remove("{update_path}")
    os.remove("{update_script}")
    
except Exception as e:
    print(f"更新失败: {{e}}")
    # 恢复备份
    if os.path.exists("{backup_file}"):
        shutil.copy2("{backup_file}", "{current_file}")
'''

            with open(update_script, "w") as f:
                f.write(script_content)
            os.chmod(update_script, 0o755)

            # 后台运行更新脚本
            subprocess.Popen(
                [sys.executable, str(update_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            return jsonify({"success": True, "message": "更新程序已启动，即将重启..."})

    except Exception as e:
        return jsonify({"success": False, "message": f"安装失败: {str(e)}"})


# 创建客户端实例（不自动连接，等端口确定后再连接）
public_client = PublicClient(auto_connect=False)

# ==================== 留言功能（转发到服务端） ====================


@app.route("/message", methods=["POST"])
@require_local_or_password
def post_message():
    """提交留言（文字或语音）- 转发到服务端，支持角色选择"""
    try:
        message_type = request.form.get("type", "text")
        content = request.form.get("message", "").strip()
        audio_data = request.form.get("audio", "")
        author = request.form.get("author", "家人").strip()

        # 如果作者为空，默认为"家人"
        if not author:
            author = "家人"

        # 构建 JSON 数据转发到服务端
        json_data = {
            "client_id": public_client.client_id,
            "type": message_type,
            "message": content,
            "author": author,
        }

        if message_type == "voice":
            json_data["audio"] = audio_data

        # 发送到服务端
        response = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/messages",
            json=json_data,
            timeout=1200,  # 语音上传可能需要更长时间
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(
                {"success": False, "message": f"服务端错误: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"发送失败: {str(e)}"})


@app.route("/api/messages/<date>")
def get_messages(date):
    """获取指定日期的留言 - 从服务端获取"""
    try:
        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/messages/{public_client.client_id}/{date}",
            timeout=30,
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(
                {"success": False, "message": f"服务端错误: {response.status_code}"}
            )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/messages/<message_id>", methods=["DELETE"])
@require_local_or_password
def delete_message(message_id):
    """删除留言 - 发送到服务端"""
    try:
        response = public_client.signed_request(
            "DELETE",
            f"{public_client.server_url}/czrz/messages/{public_client.client_id}/{message_id}",
            timeout=30,
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(
                {"success": False, "message": f"服务端错误: {response.status_code}"}
            )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== 照片工具API ====================


@app.route("/api/photo/featured", methods=["GET", "POST"])
@require_local_or_password
def featured_photo_api():
    """获取/保存精选照片"""
    try:
        if request.method == "GET":
            client_id = request.args.get("client_id", public_client.client_id)
            date = request.args.get("date")
            response = public_client.signed_request(
                "GET",
                f"{public_client.server_url}/czrz/photo/featured",
                params={"client_id": client_id, "date": date},
                timeout=30,
            )
        else:
            data = request.get_json()
            response = public_client.signed_request(
                "POST",
                f"{public_client.server_url}/czrz/photo/featured/update",
                json=data,
                timeout=30,
            )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photos/remove-from-index", methods=["POST"])
@require_local_or_password
def remove_photos_from_index_api():
    """从索引中移除指定照片（不删除原文件）"""
    try:
        data = request.get_json()
        filenames = data.get("filenames", [])

        if not filenames:
            return jsonify({"success": False, "message": "未指定要移除的照片"})

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"})

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)

        removed = []
        for filename in filenames:
            try:
                pm.remove_photo(filename)
                removed.append(filename)
            except Exception as e:
                print(f"移除失败 {filename}: {e}")

        return jsonify(
            {
                "success": True,
                "message": f"已从索引移除 {len(removed)} 张照片",
                "removed": removed,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"移除失败: {str(e)}"})


@app.route("/api/ai/generate-log", methods=["POST"])
@require_local_or_password
def proxy_ai_generate_log():
    """
    AI生成日志 - 统一流程

    流程：
    1. ai_select 分析照片（一次AI调用）
    2. 过滤有效照片
    3. 处理视频语音
    4. 生成日志
    """
    import time

    start_time = time.time()

    try:
        data = request.get_json() or {}
        date = data.get("date")
        client_id = data.get("client_id") or getattr(public_client, "client_id", "")
        log_style = data.get("log_style", "简练")
        custom_style = data.get("custom_style", "")
        user_note = data.get("user_note", "")
        include_news = data.get("include_news", False)

        print(
            f"[INFO] 开始生成日志: date={date}, style={log_style}, include_news={include_news}"
        )

        media_folders = getattr(public_client, "media_folders", [])
        child_id = get_ai_identity_client_id(client_id)

        photo_analysis = None

        # 步骤1：AI分析照片
        if media_folders and date:
            try:
                from photo_manager import PhotoManager
                from select_best_photo import describe_photo_batch, analyze_all_photos

                pm = PhotoManager(media_folders, public_client.data_dir)
                all_media = pm.get_photos_by_date(date)
                photos = [m for m in all_media if not m.get("is_video", False)]

                if photos:
                    photo_paths = [
                        p["path"] for p in photos if Path(p.get("path", "")).exists()
                    ]
                    print(f"[INFO] 找到 {len(photo_paths)} 张照片，开始AI分析...")

                    step_start = time.time()
                    ai_result, _ = _describe_photos_with_block_fallback(
                        photo_paths,
                        child_id=child_id,
                        client_id=client_id,
                        date=date,
                        log_prefix="[INFO]",
                    )
                    logger.info(f"[TIME] AI分析耗时: {time.time() - step_start:.2f}s")

                    # 【调试】输出 AI 返回结果
                    logger.info(f"[DEBUG] AI 返回结果:")
                    logger.info(
                        f"  - photos 字段：{len(ai_result.get('photos', {}))}条"
                    )
                    logger.info(f"  - blurry: {len(ai_result.get('blurry', []))}张")
                    logger.info(f"  - no_baby: {len(ai_result.get('no_baby', []))}张")
                    logger.info(
                        f"  - duplicates: {len(ai_result.get('duplicates', []))}组"
                    )
                    if ai_result.get("photos"):
                        logger.info(
                            f"  - photos 样例：{list(ai_result['photos'].items())[:2]}"
                        )

                    blurry_count = len(ai_result.get("blurry", []))
                    no_baby_count = len(ai_result.get("no_baby", []))
                    dup_groups = ai_result.get("duplicates", [])
                    dup_count = sum(len(g) - 1 for g in dup_groups if len(g) > 1)
                    print(
                        f"[INFO] 模糊{blurry_count}张, 无宝宝{no_baby_count}张, 重复{dup_count}张"
                    )

                    photo_analysis = analyze_all_photos(
                        photo_paths,
                        max_photos=len(photo_paths),
                        client_id=client_id,
                        date=date,
                        ai_result=ai_result,
                    )
                    valid_photos = photo_analysis.get("photos", [])
                    print(f"[INFO] 有效照片: {len(valid_photos)}张")

            except Exception as e:
                print(f"[WARN] 照片分析失败: {e}")
                import traceback

                traceback.print_exc()

        # 步骤2：处理视频语音
        video_results = []
        if media_folders and date:
            try:
                from photo_manager import PhotoManager

                pm = PhotoManager(media_folders, public_client.data_dir)
                all_media = pm.get_photos_by_date(date)
                videos = [m for m in all_media if m.get("is_video", False)]

                if videos:
                    print(f"[INFO] 找到 {len(videos)} 个视频，开始语音识别...")

                    from video_audio_processor import (
                        process_video_speech,
                        analyze_language_ability,
                    )

                    for video in videos:
                        video_path = Path(video.get("path", ""))
                        if not video_path.exists():
                            continue

                        try:
                            step_start = time.time()
                            speech_result = process_video_speech(
                                video_path, max_duration=60
                            )
                            print(
                                f"[TIME] 视频语音处理耗时: {time.time() - step_start:.2f}s"
                            )

                            if speech_result.get("success") and speech_result.get(
                                "transcript"
                            ):
                                video_results.append(
                                    {
                                        "path": str(video_path),
                                        "transcript": speech_result["transcript"],
                                        "duration": speech_result.get("duration", 0),
                                    }
                                )
                        except Exception as e:
                            print(f"[WARN] 视频语音处理失败 {video_path}: {e}")

                    if video_results:
                        video_results.sort(
                            key=lambda x: x.get("duration", 0), reverse=True
                        )

                        baby_info = getattr(public_client, "baby_info", {}) or {}
                        baby_name = baby_info.get("baby_name", "宝宝")
                        age_months = baby_info.get("age_months", 0)

                        for vr in video_results:
                            analysis = analyze_language_ability(
                                vr["transcript"],
                                baby_name=baby_name,
                                age_months=age_months,
                            )
                            video_hash = _resolve_media_hash(vr["path"])

                            upload_resp = public_client.signed_request(
                                "POST",
                                f"{public_client.server_url}/czrz/speech/record",
                                json={
                                    "client_id": client_id,
                                    "date": date,
                                    "video_path": vr["path"],
                                    "file_hash": video_hash,
                                    "transcript": vr["transcript"],
                                    "duration": vr["duration"],
                                    "analysis": analysis,
                                },
                                timeout=30,
                            )
                            if upload_resp.status_code == 200:
                                print(
                                    f"[INFO] 语音记录已上传: {vr['transcript'][:50]}..."
                                )

            except Exception as e:
                print(f"[WARN] 视频语音处理失败: {e}")

        # 步骤3：生成日志
        request_data = {
            "client_id": client_id,
            "date": date,
            "user_note": user_note,
            "log_style": log_style,
            "custom_style": custom_style,
            "auto_save": True,
            "photo_analysis": photo_analysis,
            "include_news": include_news,
        }

        step_start = time.time()
        response = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/ai/generate-log",
            json=request_data,
            timeout=1200,
        )
        logger.info(f"[TIME] 服务端生成日志耗时: {time.time() - step_start:.2f}s")

        result = response.json()
        logger.info(f"[TIME] 总耗时: {time.time() - start_time:.2f}s")

        return jsonify(result), response.status_code
    except Exception as e:
        print(f"[ERROR] 生成日志失败: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photo/select-best", methods=["POST"])
@require_local_or_password
def select_best_photo_local():
    """本地选择精选照片（同时返回模糊和重复照片列表）"""
    try:
        data = request.get_json() or {}
        date = (data.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify(
                {
                    "success": False,
                    "message": "未配置媒体文件夹，请在设置中添加媒体文件夹",
                }
            )

        child_id = get_ai_identity_client_id(public_client.client_id)
        processed_photos = get_processed_photos(date, public_client.client_id)
        ensured = _ensure_photo_descriptions_for_entries(
            date,
            public_client.client_id,
            child_id=child_id,
            processed_photos=processed_photos,
            log_prefix="[精选照片]",
        )
        photo_records = ensured.get("photos", [])
        selected_record, ai_description, selection = _pick_featured_photo_from_records(
            photo_records,
            child_id=child_id,
        )

        if not selected_record or not selected_record.get("path"):
            return jsonify({"success": False, "message": "未能选出精选照片"})

        photo_path = selected_record.get("path", "")
        best_photo = Path(photo_path).name

        # 保存精选照片
        if best_photo:
            save_featured_photo_server(
                date,
                public_client.client_id,
                photo_path,
                ai_description or "",
            )

        return jsonify(
            {
                "success": True,
                "photo": best_photo,
                "ai_description": ai_description,
                "message": "已选择精选照片",
                "blurry": ensured.get("blurry", []),
                "duplicates": ensured.get("duplicates", []),
                "method": "description_select",
                "selection_reason": (selection.get("reasons") or [""])[0],
            }
        )

    except Exception as e:
        import traceback

        print(f"[ERROR] 选择精选照片失败: {e}")
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/ai/ask", methods=["POST"])
@require_local_or_password
def ai_ask():
    """向本地健康AI服务提问"""
    try:
        # 检查AI服务是否启用
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = get_ai_identity_client_id()
        if not child_id:
            return jsonify(
                {
                    "success": False,
                    "message": "未配置 client_id，请先完成客户端注册",
                }
            )

        # 通过服务端代理访问健康AI
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        data = request.get_json() or {}
        question = data.get("question", "")
        photo_filename = data.get("photo_filename", "")
        date = data.get("date", "")

        if not question:
            return jsonify({"success": False, "message": "请输入问题"})

        # 获取照片完整路径
        photo_path = None
        if photo_filename:
            from photo_manager import PhotoManager

            media_folders = getattr(public_client, "media_folders", [])
            pm = PhotoManager(media_folders, public_client.data_dir)
            photo_info = pm.get_photo_by_filename(photo_filename)
            if photo_info and os.path.exists(photo_info.get("path", "")):
                photo_path = photo_info["path"]

        # 调用AI服务
        payload = {
            "child_id": child_id,
            "question": question,
            "file_path": photo_path,
            "context": {
                "date": date,
                "city": getattr(public_client, "user_city", None),
                "baby_name": public_client.baby_name,
            },
        }

        response = public_client.signed_request(
            "POST",
            f"{server_url}/api/ai/ask",
            json=payload,
            timeout=1200,
        )

        if response.status_code == 200:
            result = response.json()
            return jsonify(
                {
                    "success": True,
                    "answer": result.get("answer", "抱歉，我暂时无法回答这个问题"),
                    "evidence_size": result.get("evidence_size", 0),
                }
            )
        else:
            return jsonify(
                {"success": False, "message": f"AI服务响应错误: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"请求失败: {str(e)}"})


@app.route("/api/ai/feedback", methods=["POST"])
@require_local_or_password
def ai_feedback():
    """提交画像反馈到服务端"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = get_ai_identity_client_id()
        if not child_id:
            return jsonify({"success": False, "message": "未配置 client_id"})

        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        data = request.get_json() or {}

        # 提取反馈文本
        feedback_text = (
            data.get("answer", "")
            or data.get("feedback_text", "")
            or data.get("user_correction", "")
        )
        if not feedback_text:
            return jsonify({"success": False, "message": "反馈内容不能为空"})

        payload = {
            "client_id": public_client.client_id,
            "feedback_text": feedback_text,
            "feedback_type": data.get("feedback_type", "user_input"),
        }

        response = public_client.signed_request(
            "POST",
            f"{server_url}/czrz/client/profile-feedback",
            json=payload,
            timeout=30,
        )

        if response.status_code == 200:
            return jsonify({"success": True, "message": "反馈已提交"})
        else:
            return jsonify(
                {"success": False, "message": f"提交失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"提交失败: {str(e)}"})


@app.route("/api/ai-access/payment/session", methods=["POST"])
def ai_access_create_payment_session():
    """前台代理：创建 AI 支付会话。"""
    try:
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"}), 500

        payload = request.get_json(silent=True) or {}
        response = public_client.signed_request(
            "POST",
            f"{server_url}/api/ai-access/payment/session",
            json=payload,
            timeout=30,
        )

        data = response.json()
        return jsonify(data), response.status_code
    except Exception as e:
        logger.warning(f"[AI支付] 创建支付会话失败: {e}")
        return jsonify({"success": False, "message": f"支付会话创建失败: {str(e)}"}), 500


@app.route("/api/ai-access/notice", methods=["GET"])
def ai_access_get_notice():
    """前台代理：读取 AI 开通说明，用于重开进度弹窗。"""
    try:
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"}), 500

        params = {
            "clientId": (request.args.get("clientId") or public_client.client_id or "").strip(),
            "operation": (request.args.get("operation") or "text").strip(),
        }

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai-access/notice",
            params=params,
            timeout=30,
        )

        data = response.json()
        return jsonify(data), response.status_code
    except Exception as e:
        logger.warning(f"[AI支付] 读取 AI 开通说明失败: {e}")
        return jsonify({"success": False, "message": f"AI 开通说明读取失败: {str(e)}"}), 500


@app.route("/api/ai-access/payment/orders", methods=["POST"])
def ai_access_create_payment_order():
    """前台代理：提交 AI 支付订单。"""
    try:
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"}), 500

        payload = request.get_json(silent=True) or {}
        response = public_client.signed_request(
            "POST",
            f"{server_url}/api/ai-access/payment/orders",
            json=payload,
            timeout=60,
        )

        data = response.json()
        return jsonify(data), response.status_code
    except Exception as e:
        logger.warning(f"[AI支付] 提交支付订单失败: {e}")
        return jsonify({"success": False, "message": f"支付订单提交失败: {str(e)}"}), 500


@app.route("/api/ai-access/payment/orders/<order_id>", methods=["GET"])
def ai_access_get_payment_order(order_id):
    """前台代理：查询 AI 支付订单状态。"""
    try:
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"}), 500

        params = {}
        lookup_token = (request.args.get("lookupToken") or "").strip()
        if lookup_token:
            params["lookupToken"] = lookup_token

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai-access/payment/orders/{quote(order_id, safe='')}",
            params=params,
            timeout=30,
        )

        data = response.json()
        return jsonify(data), response.status_code
    except Exception as e:
        logger.warning(f"[AI支付] 查询支付订单失败: {e}")
        return jsonify({"success": False, "message": f"支付订单状态读取失败: {str(e)}"}), 500


@app.route("/api/ai/profile", methods=["GET"])
def ai_get_profile():
    """获取宝宝成长画像数据"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = get_ai_identity_client_id()
        if not child_id:
            return jsonify({"success": False, "message": "未配置 client_id"})

        # 通过服务端代理访问健康AI
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai/children/{child_id}/profile",
            timeout=1200,
        )

        if response.status_code == 200:
            ai_data = response.json()
            profile_data = ai_data.get("profile", {})
            llm_valid = bool(ai_data.get("llm_valid", True))

            dimensions = profile_data.get("dimensions", {})
            guidance = profile_data.get("guidance", {})
            evolution = profile_data.get("evolution", {})

            personality = dimensions.get("personality", {})
            interests = dimensions.get("interests", {})
            development = dimensions.get("development", {})

            age_text = ""
            age_months = profile_data.get("age_months", 0)
            if age_months:
                if age_months >= 12:
                    years = age_months // 12
                    months = age_months % 12
                    age_text = f"{years}岁{months}个月" if months > 0 else f"{years}岁"
                else:
                    age_text = f"{age_months}个月"

            personality_scores = personality.get("scores", {})
            tags = profile_data.get("tags", [])
            if not tags:
                tags = [
                    {"tag": t, "category": "other", "confidence": 0.5}
                    for t in personality.get("traits", [])
                ]

            total_events = profile_data.get("total_events", 0)
            total_tags = profile_data.get("total_tags", len(tags))
            knowledge_growth = profile_data.get("knowledge_growth", {})
            familiarity = min(100, total_events * 2 + total_tags * 5)

            profile = {
                "age": age_text,
                "age_months": age_months,
                "llm_valid": llm_valid,
                "knowledge_level": int(familiarity),
                "completeness": min(100, int(familiarity)),
                "total_events": total_events,
                "total_tags": total_tags,
                "tags": tags,
                "milestones": profile_data.get("milestones", []),
                "knowledge_growth": knowledge_growth,
                "dimensions": {
                    "personality": {
                        "traits": personality.get("traits", []),
                        "scores": personality_scores,
                        "evidence": personality.get("evidence", []),
                    },
                    "interests": {
                        "current": interests.get("current", []),
                        "emerging": interests.get("emerging", []),
                        "faded": interests.get("faded", []),
                        "evidence": interests.get("evidence", []),
                    },
                    "development": development,
                },
                "guidance": {
                    "summary": guidance.get("summary", ""),
                    "strengths": guidance.get("strengths", []),
                    "areas_to_nurture": guidance.get("areas_to_nurture", []),
                    "areas_to_watch": guidance.get("areas_to_watch", []),
                    "suggestions": guidance.get("suggestions", []),
                    "activities": guidance.get("activities", []),
                    "development_notes": guidance.get("development_notes", ""),
                },
                "evolution": {
                    "recent_changes": evolution.get("recent_changes", []),
                    "trends": evolution.get("trends", {}),
                },
            }

            return jsonify({"success": True, "profile": profile, "llm_valid": llm_valid})
        else:
            return jsonify(
                {"success": False, "message": f"获取失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"})


@app.route("/api/ai/features", methods=["GET"])
def ai_get_features():
    """获取宝宝特征数据（用于雷达图）"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = get_ai_identity_client_id()
        if not child_id:
            return jsonify({"success": False, "message": "未配置 client_id"})

        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai/features/{child_id}",
            timeout=1200,
        )

        if response.status_code == 200:
            return response.json()
        else:
            return jsonify(
                {"success": False, "message": f"获取失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"})


# ========== 卡片缓存API ==========


@app.route("/api/card/cache/list")
def api_card_cache_list():
    """获取所有缓存的卡片"""
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()
        sync_card_cache_silently()
        requested_category = normalize_card_category(request.args.get("category") or "all")
        all_cards = [convert_photo_path_for_client(card) for card in cache.get_all_cards()]
        all_cards = filter_visible_cards(all_cards)
        all_cards = augment_cards_with_synthetic_categories(
            all_cards, public_client.client_id
        )
        all_cards = augment_cards_with_local_manual_cards(all_cards)
        category_counts = summarize_card_categories(all_cards)
        cards = filter_cards_by_category(all_cards, requested_category)
        cards = select_rotating_cards(cards, per_day=50)

        # 添加分享状态
        for card in cards:
            card_id = card.get("id", "")
            card["shared"] = cache.is_shared(card_id)

        return jsonify(
            {
                "success": True,
                "cards": cards,
                "total": len(cards),
                "category": requested_category,
                "category_counts": category_counts,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def convert_photo_path_for_client(obj):
    """将照片路径转换为客户端可访问的URL"""
    if not obj:
        return obj

    path_keys = {
        "path",
        "photo",
        "before_photo",
        "after_photo",
        "cover_photo",
        "cover_path",
        "thumbnail",
        "thumb",
        "src",
        "generated_image",
        "generated_image_url",
        "source_photo",
    }

    if isinstance(obj, str):
        if obj.startswith(("/photo/", "/story-assets/", "/generated-assets/", "/video/thumb/")):
            return obj
        import urllib.parse

        if obj.startswith("/"):
            filename = obj.split("/")[-1]
            return f"/photo/{urllib.parse.quote(filename)}"
        return f"/photo/{urllib.parse.quote(obj)}"

    if isinstance(obj, list):
        return [convert_photo_path_for_client(item) for item in obj]

    if isinstance(obj, dict):
        converted = {}
        for key, value in obj.items():
            if key in path_keys:
                converted[key] = convert_photo_path_for_client(value)
            elif key in {
                "photo_paths",
                "photos",
                "year_samples",
                "seasons",
                "evolution_photos",
                "timeline_entries",
                "secondary_photos",
                "supporting_photos",
                "assets",
                "cards",
            }:
                converted[key] = convert_photo_path_for_client(value)
            else:
                converted[key] = value
        return converted

    return obj


def select_rotating_cards(cards, per_day: int = 50, date_str: str = None):
    """
    按天轮换展示卡片：
    - 不删除、不过滤总库，仅决定“今天展示哪一页”
    - 稳定排序 + 按日偏移，避免每天都看同一批
    """
    if not cards:
        return []

    if per_day <= 0:
        return cards

    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    try:
        day_seed = datetime.strptime(date_str, "%Y-%m-%d").toordinal()
    except Exception:
        day_seed = datetime.now().toordinal()

    def stable_key(card):
        card_id = str(card.get("id") or card.get("card_id") or "")
        # 稳定哈希，保证每天分页可复现
        return hashlib.md5(card_id.encode("utf-8")).hexdigest()

    ordered_cards = sorted(cards, key=stable_key)
    total = len(ordered_cards)
    if total <= per_day:
        return ordered_cards

    # 先保证每个类别至少展示 1 张（如果有）
    by_type = {}
    for card in ordered_cards:
        card_type = card.get("type") or card.get("card_type") or "unknown"
        by_type.setdefault(card_type, []).append(card)

    selected = []
    selected_ids = set()
    for card_type, type_cards in sorted(by_type.items()):
        if len(selected) >= per_day:
            break
        if not type_cards:
            continue
        idx = day_seed % len(type_cards)
        chosen = type_cards[idx]
        card_id = chosen.get("id") or chosen.get("card_id")
        if card_id not in selected_ids:
            selected.append(chosen)
            selected_ids.add(card_id)

    # 再按日轮换补齐剩余名额
    remaining = per_day - len(selected)
    if remaining <= 0:
        return selected

    total_pages = (total + remaining - 1) // remaining
    page_idx = day_seed % total_pages
    start = page_idx * remaining
    end = start + remaining
    for card in ordered_cards[start:end]:
        card_id = card.get("id") or card.get("card_id")
        if card_id in selected_ids:
            continue
        selected.append(card)
        selected_ids.add(card_id)
        if len(selected) >= per_day:
            break

    # 如果还有空位，从头补齐
    if len(selected) < per_day:
        for card in ordered_cards:
            card_id = card.get("id") or card.get("card_id")
            if card_id in selected_ids:
                continue
            selected.append(card)
            selected_ids.add(card_id)
            if len(selected) >= per_day:
                break

    return selected


def sync_card_cache_silently():
    """静默从服务端同步一次卡片缓存"""
    try:
        if not public_client or not getattr(public_client, "client_id", None):
            return False
        return public_client.send_heartbeat()
    except Exception as e:
        print(f"[Card] 静默同步失败: {e}")
        return False


@app.route("/api/card/share", methods=["POST"])
@require_local_or_password
def api_card_share():
    """标记卡片已分享"""
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()

        data = request.get_json() or {}
        card_id = data.get("card_id", "")

        if not card_id:
            return jsonify({"success": False, "message": "缺少card_id"})

        cache.mark_shared(card_id)

        return jsonify({"success": True, "message": "已标记为已分享"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def get_local_profile_data():
    """获取本地画像数据（直接从服务端获取，不走API）"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return None

        child_id = get_ai_identity_client_id()
        if not child_id:
            return None

        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return None

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai/children/{child_id}/profile",
            timeout=1200,
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("profile", {})
        return None
    except Exception as e:
        print(f"[Card] 获取画像数据失败: {e}")
        return None


def _get_comic_style_by_key(style_key: str) -> dict:
    target = (style_key or "").strip().lower()
    for item in _get_comic_style_library():
        item_key = str(item.get("theme") or "").strip().lower()
        if target and item_key == target:
            return dict(item)
    return {}


def _default_comic_style() -> dict:
    return {
        "name": "晴光绘本",
        "tagline": "保留真实日常，再把光线和故事感往前推一格",
        "palette": ["#f6c36a", "#f7e3a6", "#93c9b7", "#fff8ef"],
        "theme": "sunlit",
        "frame_shape": "arch",
        "prompt": "soft illustrated storybook, warm daylight, airy brushwork, gentle highlights",
    }


def _select_comic_source_records(records: list, limit: int = 1) -> tuple[list, list]:
    candidates = [
        dict(item)
        for item in (records or [])
        if isinstance(item, dict)
        and item.get("path")
        and not item.get("is_video", False)
    ]
    if not candidates:
        return [], []

    limit = max(1, min(int(limit or 1), 3))
    if len(candidates) <= limit:
        return candidates[:limit], [
            (item.get("description") or "当天代表性照片").strip() for item in candidates[:limit]
        ]

    try:
        from select_best_photo import select_featured_photo_from_descriptions

        selection = select_featured_photo_from_descriptions(
            candidates,
            select_n=limit,
            child_id=public_client.client_id,
        )
        chosen_names = selection.get("selected_filenames") or [
            Path(path).name for path in (selection.get("selected") or [])
        ]
        reason_map = {}
        reasons = selection.get("reasons") or []
        for index, name in enumerate(chosen_names):
            reason_map[name] = reasons[index] if index < len(reasons) else ""

        candidate_map = {
            (item.get("filename") or Path(item.get("path", "")).name): item
            for item in candidates
        }
        chosen = []
        chosen_reasons = []
        for name in chosen_names:
            item = candidate_map.get(name)
            if not item:
                continue
            chosen.append(item)
            chosen_reasons.append(
                (reason_map.get(name) or item.get("description") or "当天代表性照片").strip()
            )
        if chosen:
            return chosen[:limit], chosen_reasons[:limit]
    except Exception as e:
        logger.warning(f"[Comic] 自动选图失败，改用本地排序: {e}")

    ranked = sorted(
        candidates,
        key=lambda item: (
            bool(item.get("has_baby", True)),
            len((item.get("description") or "").strip()),
            bool((item.get("scene") or "").strip()),
            bool((item.get("activity") or "").strip()),
        ),
        reverse=True,
    )
    chosen = ranked[:limit]
    return chosen, [
        (item.get("description") or "当天代表性照片").strip() for item in chosen
    ]


def _build_manual_comic_prompt(record: dict, style: dict, custom_prompt: str = "") -> str:
    baby_name = getattr(public_client, "baby_name", "宝宝")
    description = (record.get("description") or "").strip()
    scene = (record.get("scene") or "").strip()
    activity = (record.get("activity") or "").strip()
    custom_text = (custom_prompt or "").strip()

    prompt = (
        f"请参考这张宝宝真实照片，创作一张适合家长分享的单幅漫画海报。"
        f"主角固定为{baby_name}，必须保留宝宝本人五官特征、发型、体态、年龄感、衣服主色和当前真实场景元素，不能换脸，不能换成陌生孩子。"
        f"不要把原图直接卡通滤镜化，而是要重新组织构图、背景层次、镜头距离和光线，让它看起来像一张精心设计的封面图。"
    )
    if style.get("name"):
        prompt += f" 主要风格请走“{style.get('name')}”，方向是：{style.get('prompt') or style.get('tagline') or ''}。"
    if scene:
        prompt += f" 当前场景是：{scene}。"
    if activity:
        prompt += f" 当前动作主线是：{activity}。"
    if description:
        prompt += f" 真实素材摘要：{description}。"
    if custom_text:
        prompt += f" 家长额外希望加入的风格要求：{custom_text}。"
    prompt += (
        " 只输出画面，不要在图里出现任何中文、英文、数字、对白气泡、标题、路牌、logo、水印。"
        " 画面需要温暖、有层次、有分享欲。"
    )
    return prompt


def _build_manual_comic_card(
    record: dict,
    style: dict,
    generated_url: str,
    *,
    custom_prompt: str = "",
    selection_reason: str = "",
    auto_reasons: list | None = None,
) -> dict:
    from server_card_generator import ServerCardGenerator

    generator = ServerCardGenerator()
    filename = record.get("filename") or Path(record.get("path", "")).name
    source_photo_url = f"/photo/{quote(filename)}" if filename else ""
    frame_shape = generator._pick_album_frame_shape(
        record,
        style.get("frame_shape", "square"),
    )
    prompt_hint = style.get("prompt") or style.get("tagline") or ""
    if custom_prompt:
        prompt_hint = (
            f"{prompt_hint}；家长加料：{custom_prompt.strip()}"
            if prompt_hint
            else custom_prompt.strip()
        )

    reason_lines = []
    if selection_reason:
        reason_lines.append(selection_reason.strip())
    for item in (auto_reasons or []):
        text = str(item or "").strip()
        if text and text not in reason_lines:
            reason_lines.append(text)
    if custom_prompt:
        reason_lines.append("已叠加家长指定的风格要求")
    if not reason_lines:
        reason_lines.append("这是当天最适合被画成单幅海报的一张照片")

    generated_at = datetime.now().isoformat()
    card_id = (
        f"manual_comic_{datetime.now().strftime('%Y%m%d%H%M%S')}_"
        f"{hashlib.md5((filename + generated_at).encode('utf-8')).hexdigest()[:8]}"
    )
    return {
        "id": card_id,
        "card_id": card_id,
        "type": "generated_comic_card",
        "card_subtype": f"manual_{style.get('theme') or 'custom'}",
        "layout": "generated_comic",
        "title": f"🎨 {style.get('name') or '定制漫画'}",
        "subtitle": style.get("tagline") or "从照片墙手动生成的分享漫画卡",
        "content": (
            f"保留“{record.get('scene') or '当天场景'}”的真实感，"
            f"再把“{record.get('activity') or '当下动作'}”画得更有故事封面感。"
        ),
        "footer": "照片墙手动生成 · 已存入漫画卡片",
        "emoji": "🎨",
        "photo_paths": [source_photo_url] if source_photo_url else [],
        "generated_image": generated_url,
        "source_photo": source_photo_url,
        "style_name": style.get("name") or "定制漫画",
        "style_tagline": style.get("tagline") or "",
        "style_palette": list(style.get("palette") or []),
        "style_theme": style.get("theme") or "sunlit",
        "frame_shape": frame_shape,
        "reason_lines": reason_lines[:4],
        "photo_date": (record.get("date") or datetime.now().strftime("%Y-%m-%d")).strip(),
        "scene_label": record.get("scene") or "",
        "activity_label": record.get("activity") or "",
        "quality_label": "手动生成",
        "narrative_line": (record.get("description") or "把当下这一刻画成更适合分享的一张封面。").strip(),
        "prompt_hint": prompt_hint,
        "generation_mode": "manual",
        "custom_prompt": (custom_prompt or "").strip(),
        "generated_at": generated_at,
    }


@app.route("/api/cards/comic/generate", methods=["POST"])
@require_local_or_password
def api_generate_manual_comic_cards():
    try:
        data = request.get_json() or {}
        date_str = (data.get("date") or datetime.now().strftime("%Y-%m-%d")).strip()
        style_key = (data.get("style_key") or "auto").strip().lower()
        custom_prompt = (data.get("custom_prompt") or "").strip()
        requested_filenames = [
            Path(str(name or "")).name
            for name in (data.get("filenames") or [])
            if str(name or "").strip()
        ]

        preparation = {}
        records = _prepare_story_photo_records(
            date_str,
            public_client.client_id,
            allow_media_fallback=False,
            diagnostics=preparation,
        )
        selected_records = []
        selection_reasons = []
        if requested_filenames:
            record_map = {
                (item.get("filename") or Path(item.get("path", "")).name): item
                for item in records
                if isinstance(item, dict)
            }
            for filename in requested_filenames:
                item = record_map.get(filename)
                if not item:
                    continue
                selected_records.append(item)
                selection_reasons.append("家长手动选中的当天照片")
            selected_records = selected_records[:3]
            missing_requested = [
                filename
                for filename in requested_filenames
                if filename not in {
                    (item.get("filename") or Path(item.get("path", "")).name)
                    for item in selected_records
                }
            ]
            if missing_requested:
                return jsonify(
                    {
                        "success": False,
                        "message": _build_comic_generation_unavailable_message(
                            preparation,
                            missing_requested=missing_requested,
                        ),
                    }
                ), 400

        if not selected_records:
            selected_records, selection_reasons = _select_comic_source_records(records, limit=1)
        if not selected_records:
            return jsonify(
                {
                    "success": False,
                    "message": _build_comic_generation_unavailable_message(preparation),
                }
            ), 400

        base_style = _get_comic_style_by_key(style_key) if style_key != "auto" else {}
        if not base_style:
            base_style = _default_comic_style()

        from server_card_generator import ServerCardGenerator

        generator = ServerCardGenerator()
        category = "comic_cards"
        created_cards = []

        for index, record in enumerate(selected_records):
            source_path = record.get("path") or ""
            if not source_path or not Path(source_path).exists():
                return jsonify(
                    {
                        "success": False,
                        "message": (
                            f"漫画生成失败：照片源文件不存在或已被移动"
                            f"（{record.get('filename') or Path(source_path).name or 'unknown'}）。"
                        ),
                    }
                ), 400

            if style_key == "auto":
                style, auto_reasons = generator._match_art_style(record)
            else:
                style = dict(base_style)
                auto_reasons = []

            prompt = _build_manual_comic_prompt(record, style, custom_prompt)
            remote_url, image_error = _generate_image_with_reference_result(
                prompt,
                source_path,
                operation="photo_to_comic",
                model_name=DEFAULT_STORY_IMAGE_MODEL,
                size=DEFAULT_STORY_IMAGE_SIZE,
            )
            if not remote_url:
                return jsonify(
                    {
                        "success": False,
                        "message": (
                            f"漫画生成失败：{image_error or '图片模型没有返回结果。'}"
                        ),
                    }
                ), 502

            filename = (
                f"comic_{datetime.now().strftime('%Y%m%d%H%M%S')}_{index}_"
                f"{hashlib.md5((record.get('filename') or source_path).encode('utf-8')).hexdigest()[:8]}.jpg"
            )
            target_path = _generated_asset_dir(category) / filename
            if not _save_generated_asset_image(remote_url, target_path):
                return jsonify(
                    {
                        "success": False,
                        "message": "漫画生成失败：图片已生成，但保存到本地失败。",
                    }
                ), 500

            generated_url = _generated_asset_url(category, filename)
            selection_reason = selection_reasons[index] if index < len(selection_reasons) else ""
            created_cards.append(
                _build_manual_comic_card(
                    record,
                    style,
                    generated_url,
                    custom_prompt=custom_prompt,
                    selection_reason=selection_reason,
                    auto_reasons=auto_reasons,
                )
            )

        if not created_cards:
            return jsonify(
                {
                    "success": False,
                    "message": "漫画生成失败：没有生成出任何可保存的结果。",
                }
            ), 500

        append_local_manual_cards(created_cards)
        return jsonify(
            {
                "success": True,
                "message": f"已生成 {len(created_cards)} 张漫画卡片",
                "cards": created_cards,
            }
        )
    except Exception as e:
        logger.warning(f"[Comic] 生成漫画卡失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ==================== AI智能回顾 ====================

AI_REVIEW_TASKS = {}
REMOTE_AI_COMMANDS = {}
REMOTE_AI_COMMANDS_LOCK = threading.Lock()
HASH_BACKFILL_PM_CACHE = {"media_folders": None, "manager": None}
RAW_PHOTO_INDEX_CACHE = {"mtime": None, "by_path": {}, "by_filename": {}}


def _is_ai_review_running() -> bool:
    """检查是否已有AI刷新任务在执行"""
    for task in AI_REVIEW_TASKS.values():
        if not task.get("completed"):
            return True
    return False


def _get_active_ai_review_task():
    """返回当前未完成的 AI 刷新任务。"""
    for task_id, task in AI_REVIEW_TASKS.items():
        if not task.get("completed"):
            return task_id, task
    return None, None


def _snapshot_ai_review_task(task_id: str) -> dict:
    """返回适合回传服务端的精简状态。"""
    task = AI_REVIEW_TASKS.get(task_id)
    if not isinstance(task, dict):
        return {}
    return {
        "task_id": task_id,
        "status": task.get("status", ""),
        "processed": task.get("processed", 0),
        "total": task.get("total", 0),
        "success_count": task.get("success_count", 0),
        "skipped": task.get("skipped", 0),
        "message": task.get("message", ""),
        "cancel_requested": bool(task.get("cancel_requested")),
        "cancelled": bool(task.get("cancelled")),
        "portrait_refresh": task.get("portrait_refresh"),
        "portrait_llm_valid": task.get("portrait_llm_valid"),
        "portrait_message": task.get("portrait_message", ""),
        "completed": bool(task.get("completed")),
        "started_at": task.get("started_at"),
    }


def _cleanup_stale_ai_review_tasks(timeout_seconds: int = 6 * 60 * 60) -> bool:
    """
    清理可能卡死的AI刷新任务

    Returns:
        bool: 是否清理过任务
    """
    now_ts = time.time()
    cleaned = False

    for task_id, task in list(AI_REVIEW_TASKS.items()):
        if task.get("completed"):
            continue

        started_at = task.get("started_at")
        if not started_at:
            continue

        try:
            started_dt = datetime.fromisoformat(started_at)
        except Exception:
            continue

        if (now_ts - started_dt.timestamp()) > timeout_seconds:
            task["completed"] = True
            task["message"] = "任务超时已自动结束"
            task["status"] = "timeout"
            cleaned = True

    return cleaned


def request_ai_auto_review_cancel(task_id: str = "") -> dict:
    """请求取消当前或指定的 AI 刷新任务。"""
    target_task_id = ""
    target_task = None

    if task_id:
        candidate = AI_REVIEW_TASKS.get(task_id)
        if isinstance(candidate, dict) and not candidate.get("completed"):
            target_task_id = task_id
            target_task = candidate

    if not target_task:
        target_task_id, target_task = _get_active_ai_review_task()

    if not target_task_id or not isinstance(target_task, dict):
        return {"success": False, "message": "没有正在执行的刷新任务"}

    target_task["cancel_requested"] = True
    target_task["status"] = "cancelling"
    target_task["message"] = "已请求取消，等待当前步骤结束"
    target_task["cancel_requested_at"] = datetime.now().isoformat()
    return {"success": True, "task_id": target_task_id, "message": target_task["message"]}


def start_ai_auto_review_task(reuse_running: bool = False) -> dict:
    """启动 AI 智能回顾任务，可选择复用已在运行的任务。"""
    import threading

    _cleanup_stale_ai_review_tasks()

    if _is_ai_review_running():
        active_task_id, _ = _get_active_ai_review_task()
        if reuse_running and active_task_id:
            return {
                "success": True,
                "task_id": active_task_id,
                "already_running": True,
                "message": "刷新正在进行中，已复用现有任务",
            }
        return {"success": False, "message": "刷新正在进行中，请稍后再试"}

    ai_config = CLIENT_CONFIG.get("ai_service", {})
    if not ai_config.get("enabled", False):
        return {"success": False, "message": "AI服务未启用"}

    child_id = get_ai_identity_client_id()
    if not child_id:
        return {"success": False, "message": "未配置 client_id"}

    if not public_client.client_id:
        return {"success": False, "message": "未注册客户端，请先连接服务端"}

    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders:
        return {"success": False, "message": "未配置媒体文件夹"}

    task_id = str(uuid.uuid4())[:8]
    AI_REVIEW_TASKS[task_id] = {
        "status": "running",
        "processed": 0,
        "total": 0,
        "success_count": 0,
        "skipped": 0,
        "message": "正在扫描照片...",
        "cancel_requested": False,
        "cancelled": False,
        "portrait_refresh": None,
        "portrait_llm_valid": None,
        "portrait_message": "",
        "details": [],
        "completed": False,
        "started_at": datetime.now().isoformat(),
    }

    thread = threading.Thread(
        target=run_ai_auto_review,
        args=(task_id, media_folders, child_id, ai_config),
        daemon=True,
    )
    thread.start()

    return {"success": True, "task_id": task_id, "already_running": False}


@app.route("/api/ai/auto-review", methods=["POST"])
@require_local_password_or_signature
def start_ai_auto_review():
    """启动AI智能回顾任务"""
    try:
        result = start_ai_auto_review_task(reuse_running=False)
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/ai/auto-review/cancel", methods=["POST"])
@require_local_password_or_signature
def cancel_ai_auto_review():
    """取消正在执行的 AI 智能回顾任务。"""
    try:
        data = request.get_json(silent=True) or {}
        task_id = (data.get("task_id") or "").strip()
        result = request_ai_auto_review_cancel(task_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def run_ai_auto_review(task_id, media_folders, child_id, ai_config):
    """执行AI重新认识任务

    简化逻辑：对所有日期进行全量处理，基于哈希检查是否跳过
    - 处理所有索引中有日期的项
    - 数据库存在且哈希相等 → 跳过处理
    - 不存在或哈希不对 → 处理并保存/覆盖
    - 所有日期处理完毕后触发同步到健康AI和画像生成
    """
    from photo_manager import PhotoManager
    from datetime import datetime
    import re

    def is_valid_date(date_str: str) -> bool:
        """检查日期是否有效"""
        if not date_str:
            return False
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return False
        try:
            year, month, day = map(int, date_str.split("-"))
            if not (2000 <= year <= 2030):
                return False
            if not (1 <= month <= 12):
                return False
            if not (1 <= day <= 31):
                return False
            return True
        except:
            return False

    def cancel_if_requested(message: str) -> bool:
        task = AI_REVIEW_TASKS.get(task_id)
        if not isinstance(task, dict) or not task.get("cancel_requested"):
            return False

        task["cancelled"] = True
        task["completed"] = True
        task["status"] = "cancelled"
        task["message"] = message
        task["cancelled_at"] = datetime.now().isoformat()
        if task.get("portrait_refresh") in {None, "pending"}:
            task["portrait_refresh"] = "cancelled"
        if not task.get("portrait_message"):
            task["portrait_message"] = message
        return True

    try:
        pm = PhotoManager(media_folders, public_client.data_dir)
        server_client_id = public_client.client_id
        all_dates = pm.get_all_dates()

        today = datetime.now().strftime("%Y-%m-%d")

        # 获取索引日期过滤设置（从配置文件读取，确保重启后生效）
        index_after = USER_CONFIG.get("index_after_date", "") or ""
        logger.info(f"[AI回顾] index_after_date = '{index_after}'")

        # 过滤无效日期、未来日期和索引设置之前的日期，按日期倒序处理（最近优先）
        dates_to_process = sorted(
            [
                d
                for d in all_dates
                if d < today
                and is_valid_date(d)
                and (not index_after or d >= index_after)
            ],
            reverse=True,
        )

        if index_after:
            logger.info(f"[AI回顾] 索引日期过滤: 只处理 {index_after} 之后的日期")

        AI_REVIEW_TASKS[task_id]["total"] = len(dates_to_process)
        AI_REVIEW_TASKS[task_id]["message"] = f"正在准备处理数据..."

        success_count = 0
        skipped_count = 0
        import time

        if cancel_if_requested("刷新任务已取消"):
            return

        for i, date in enumerate(dates_to_process):
            try:
                if cancel_if_requested(f"刷新任务已取消，停止于 {date}"):
                    return

                AI_REVIEW_TASKS[task_id]["message"] = (
                    f"正在处理 {date} ({i + 1}/{len(dates_to_process)})"
                )

                # 【调试】输出当前处理的日期
                logger.info(f"\n{'=' * 60}")
                logger.info(f"[DEBUG] 处理日期：{date}")
                logger.info(f"{'=' * 60}")

                # 性能监控：记录开始时间
                date_start_time = time.time()

                # 获取当天所有媒体（照片+视频）
                all_media = pm.get_photos_by_date(date)
                photos = [p for p in all_media if not p.get("is_video", False)]
                videos = [p for p in all_media if p.get("is_video", False)]

                photo_paths = [p.get("path") for p in photos if p.get("path")]
                video_paths = [p.get("path") for p in videos if p.get("path")]

                # 简化逻辑：基于哈希检查是否处理
                needs_process = False
                processed_photos = []
                processed_videos = []
                unprocessed_photos = []
                unprocessed_videos = []

                # 1. 获取数据库中的照片哈希记录
                db_photos = get_processed_photos(date, server_client_id)
                db_photo_map = {}
                for db_photo in db_photos:
                    if isinstance(db_photo, dict):
                        path = db_photo.get("path", "")
                        db_hash = db_photo.get("hash", "") or db_photo.get(
                            "file_hash", ""
                        )
                        if path and db_hash:
                            db_photo_map[path] = db_hash

                # 2. 检查照片是否需要处理
                for photo_path in photo_paths:
                    # 如果数据库中没有记录，直接需要处理
                    if photo_path not in db_photo_map:
                        unprocessed_photos.append(photo_path)
                        continue

                    db_hash = db_photo_map[photo_path]

                    # 从photo_manager获取照片信息（包含索引中已有的哈希）
                    photo_hash = ""
                    try:
                        photo_info = pm.get_photo_by_path(photo_path)
                        if photo_info and "hash" in photo_info:
                            photo_hash = photo_info["hash"]
                    except Exception as e:
                        logger.info(f"[AI回顾] 获取照片哈希失败 {photo_path}: {e}")

                    if photo_hash and photo_hash == db_hash:
                        # 哈希匹配，跳过
                        processed_photos.append(photo_path)
                    else:
                        # 哈希不匹配、获取失败或没有哈希，需要处理
                        unprocessed_photos.append(photo_path)

                # 4. 检查视频是否需要处理
                db_videos = get_processed_videos(date, server_client_id)
                db_video_map = {}
                for db_video in db_videos:
                    if isinstance(db_video, dict):
                        db_path = db_video.get("video_path", "")
                        if db_path:
                            db_video_map[Path(db_path).name] = db_video
                    elif isinstance(db_video, str):
                        # 旧格式，没有哈希信息
                        db_video_map[Path(db_video).name] = None

                for video_path in video_paths:
                    video_name = Path(video_path).name
                    db_record = db_video_map.get(video_name)
                    db_hash = ""
                    if isinstance(db_record, dict):
                        db_hash = (db_record.get("file_hash", "") or "").strip()

                    # 情况1: 数据库中没有记录或没有哈希信息
                    if db_hash is None:
                        unprocessed_videos.append(video_path)
                        continue
                    if not db_hash:
                        unprocessed_videos.append(video_path)
                        continue
                    if not _video_record_has_completed_read(db_record):
                        unprocessed_videos.append(video_path)
                        continue

                    # 情况2: 有哈希信息，从photo_manager获取视频哈希
                    video_hash = ""
                    try:
                        # 尝试从photo_manager获取视频信息
                        # 注意：photo_manager可能也有视频的索引
                        video_info = None
                        for media in all_media:
                            if media.get("path") == video_path and media.get(
                                "is_video", False
                            ):
                                video_info = media
                                break

                        if video_info and "hash" in video_info:
                            video_hash = video_info["hash"]
                        else:
                            # 如果没有索引哈希，需要计算
                            hasher = hashlib.md5()
                            with open(video_path, "rb") as f:
                                for chunk in iter(lambda: f.read(8192), b""):
                                    hasher.update(chunk)
                            video_hash = hasher.hexdigest()
                    except Exception as e:
                        logger.info(f"[AI回顾] 获取视频哈希失败 {video_path}: {e}")

                    if video_hash and video_hash == db_hash:
                        processed_videos.append(video_path)
                    else:
                        unprocessed_videos.append(video_path)

                # 5. 决定是否处理该日期
                needs_analyze = len(unprocessed_photos) > 0
                needs_video_process = len(unprocessed_videos) > 0
                needs_process = needs_analyze or needs_video_process

                # 6. 处理跳过的情况
                if not needs_process:
                    skipped_count += 1
                    date_end_time = time.time()
                    process_time = date_end_time - date_start_time
                    logger.info(
                        f"[AI回顾] ★★★ 跳过 {date}：所有媒体哈希匹配，无变化 ★★★"
                    )
                    logger.info(
                        f"[AI回顾]   原因: 照片={len(processed_photos)}张匹配，视频={len(processed_videos)}个匹配"
                    )
                    logger.info(f"[AI回顾]   性能: 处理耗时 {process_time:.2f}秒")
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {
                            "date": date,
                            "success": True,
                            "message": "所有媒体哈希匹配，跳过",
                        }
                    )
                    AI_REVIEW_TASKS[task_id]["skipped"] = skipped_count
                    AI_REVIEW_TASKS[task_id]["processed"] = i + 1
                    continue

                logger.info(f"[AI回顾] ★★★ 需要处理 {date} ★★★")
                logger.info(
                    f"[AI回顾]   原因: 未处理照片={len(unprocessed_photos)}张，未处理视频={len(unprocessed_videos)}个"
                )

                if not photo_paths and not video_paths:
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {"date": date, "success": False, "message": "无有效媒体路径"}
                    )
                    AI_REVIEW_TASKS[task_id]["processed"] = i + 1
                    continue

                # 步骤1：如果需要分析照片，进行AI分析
                ai_result = None
                analysis_result = None
                needs_sync = False  # 是否需要同步到健康AI

                if needs_analyze and unprocessed_photos:
                    if cancel_if_requested(f"刷新任务已取消，停止于 {date}"):
                        return

                    # 只分析未处理的照片
                    try:
                        skipped_photo_records = []
                        ai_result, skipped_photo_records = _describe_photos_with_block_fallback(
                            unprocessed_photos,
                            child_id=child_id,
                            client_id=server_client_id,
                            date=date,
                        )

                        if skipped_photo_records:
                            status_counter = Counter(
                                (item.get("processed_status") or PHOTO_STATUS_OK)
                                for item in skipped_photo_records
                            )
                            blocked_count = status_counter.get(PHOTO_STATUS_BLOCKED, 0)
                            other_error_count = status_counter.get(
                                PHOTO_STATUS_OTHER_ERROR,
                                0,
                            )
                            terminal_photo_count = len(skipped_photo_records)
                            message = (
                                f"已标记为已处理: blocked={blocked_count}, "
                                f"other_error={other_error_count}"
                            )
                            logger.info(
                                f"[AI回顾] {date}: 终态异常图片 {terminal_photo_count} 张，{message}"
                            )
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": True,
                                    "message": message,
                                }
                            )
                            print(
                                f"[AI回顾] {date}: 终态异常图片{terminal_photo_count}张，{message}"
                            )

                        # 【调试】输出 AI 返回结果
                        if ai_result:
                            logger.info(f"[DEBUG] AI 返回结果:")
                            logger.info(
                                f"  - photos 字段：{len(ai_result.get('photos', {}))}条"
                            )
                            logger.info(
                                f"  - blurry: {len(ai_result.get('blurry', []))}张"
                            )
                            logger.info(
                                f"  - no_baby: {len(ai_result.get('no_baby', []))}张"
                            )
                            logger.info(
                                f"  - duplicates: {len(ai_result.get('duplicates', []))}组"
                            )
                            if ai_result.get("photos"):
                                logger.info(
                                    f"  - photos 样例：{list(ai_result['photos'].items())[:2]}"
                                )

                            blurry_count = len(ai_result.get("blurry", []))
                            no_baby_count = len(ai_result.get("no_baby", []))
                            dup_groups = ai_result.get("duplicates", [])
                            dup_count = sum(len(g) - 1 for g in dup_groups if len(g) > 1)

                            print(
                                f"[AI回顾] {date}: 模糊{blurry_count}张, 无宝宝{no_baby_count}张, 重复{dup_count}张"
                            )

                    except Exception as e:
                        error_msg = str(e)
                        normalized_error = error_msg.lower()
                        if (
                            "ai_access_required" in normalized_error
                            or "需要调用第三方付费大模型" in error_msg
                            or "all_models_exhausted" in normalized_error
                        ):
                            AI_REVIEW_TASKS[task_id]["completed"] = True
                            AI_REVIEW_TASKS[task_id]["status"] = "failed"
                            if "ALL_MODELS_EXHAUSTED:" in error_msg:
                                friendly_msg = error_msg.split("ALL_MODELS_EXHAUSTED:")[
                                    1
                                ]
                            else:
                                friendly_msg = error_msg
                            AI_REVIEW_TASKS[task_id]["message"] = friendly_msg
                            report_error(
                                "ai_review_access_required", friendly_msg, f"date={date}"
                            )
                            return
                        logger.info(f"[AI回顾] AI分析失败 {date}: {e}")
                        report_error(
                            "ai_review_failed", f"AI分析失败 {date}", error_msg
                        )
                        AI_REVIEW_TASKS[task_id]["details"].append(
                            {
                                "date": date,
                                "success": False,
                                "message": f"AI分析失败: {e}",
                            }
                        )
                        AI_REVIEW_TASKS[task_id]["processed"] = i + 1
                        continue

                # 步骤2：分析新照片并保存描述（如果有新照片）
                if needs_analyze and ai_result:
                    if cancel_if_requested(f"刷新任务已取消，停止于 {date}"):
                        return
                    try:
                        from select_best_photo import analyze_all_photos

                        # 分析新照片并保存到数据库
                        analyze_all_photos(
                            unprocessed_photos,  # 只分析新照片
                            max_photos=len(unprocessed_photos),
                            client_id=server_client_id,
                            date=date,
                            ai_result=ai_result,
                        )
                        print(
                            f"[DEBUG] 已保存 {len(unprocessed_photos)} 张新照片描述到数据库"
                        )
                        needs_sync = True
                    except Exception as e:
                        logger.info(f"[AI回顾] 保存新照片描述失败 {date}: {e}")

                # 步骤3：从数据库获取当天所有照片描述（用于后续处理）
                analysis_result = None
                try:
                    logger.info(f"[DEBUG] 从数据库获取当天所有照片描述")
                    all_descriptions = _filter_semantic_skip_photo_records(
                        get_processed_photos(date, server_client_id)
                    )

                    if all_descriptions:
                        # 构建 analysis_result 格式
                        valid_photos = []
                        all_scenes = set()
                        all_activities = set()
                        baby_count = 0
                        scenery_count = 0

                        for desc in all_descriptions:
                            photo_info = {
                                "path": desc.get("path", ""),
                                "description": desc.get("description", ""),
                                "has_baby": desc.get("has_baby", True),
                                "scene": desc.get("scene", ""),
                                "activity": desc.get("activity", ""),
                            }
                            valid_photos.append(photo_info)

                            if photo_info["has_baby"]:
                                baby_count += 1
                            else:
                                scenery_count += 1
                            if photo_info["scene"]:
                                all_scenes.add(photo_info["scene"])
                            if photo_info["activity"]:
                                all_activities.add(photo_info["activity"])

                        # 构建综合摘要
                        combined_parts = []
                        if baby_count > 0:
                            combined_parts.append(f"共{baby_count}张宝宝照片")
                        if scenery_count > 0:
                            combined_parts.append(f"{scenery_count}张场景照片")

                        descriptions = [
                            p["description"] for p in valid_photos if p["description"]
                        ]
                        if descriptions:
                            combined_parts.append(
                                "活动：" + "、".join(descriptions[:10])
                            )

                        combined_summary = (
                            "。".join(combined_parts)
                            if combined_parts
                            else "照片分析完成"
                        )

                        analysis_result = {
                            "photos": valid_photos,
                            "scenes": list(all_scenes),
                            "activities": list(all_activities),
                            "baby_photos": baby_count,
                            "scenery_photos": scenery_count,
                            "combined_summary": combined_summary,
                        }
                        logger.info(
                            f"[DEBUG] 从数据库获取到 {len(valid_photos)} 条照片描述"
                        )

                        AI_REVIEW_TASKS[task_id]["details"].append(
                            {
                                "date": date,
                                "success": True,
                                "message": f"有效照片{len(valid_photos)}张",
                            }
                        )
                        needs_sync = True
                    else:
                        # 数据库中没有照片描述
                        analysis_result = {
                            "photos": [],
                            "scenes": [],
                            "activities": [],
                            "baby_photos": 0,
                            "scenery_photos": 0,
                            "combined_summary": "",
                        }
                        logger.info(f"[DEBUG] 数据库中没有照片描述")
                except Exception as e:
                    logger.info(f"[AI回顾] 获取照片描述失败 {date}: {e}")
                    analysis_result = None

                # 步骤4：保存精选照片（总是生成或更新）
                featured_info = None
                existing_featured = get_featured_photo_info(date, server_client_id)
                should_select_featured = bool(
                    analysis_result and analysis_result.get("photos")
                ) and (needs_analyze or not existing_featured)

                if should_select_featured:
                    selected_record, ai_description, _selection = _pick_featured_photo_from_records(
                        analysis_result.get("photos", []),
                        child_id=child_id,
                    )

                    if selected_record and selected_record.get("path"):
                        best_photo = selected_record.get("path", "")
                        best_photo_filename = Path(best_photo).name

                        # 计算精选照片哈希用于比较
                        best_photo_hash = ""
                        try:
                            hasher = hashlib.md5()
                            with open(best_photo, "rb") as f:
                                for chunk in iter(lambda: f.read(8192), b""):
                                    hasher.update(chunk)
                            best_photo_hash = hasher.hexdigest()
                        except Exception as e:
                            logger.info(
                                f"[AI回顾] 计算精选照片哈希失败 {best_photo}: {e}"
                            )

                        existing_hash = (
                            existing_featured.get("file_hash", "")
                            if existing_featured
                            else ""
                        )

                        if not existing_featured or best_photo_hash != existing_hash:
                            save_featured_photo_server(
                                date,
                                server_client_id,
                                best_photo,
                                ai_description,
                                file_hash=best_photo_hash,
                            )
                            featured_info = {
                                "filename": best_photo_filename,
                                "ai_description": ai_description,
                                "file_hash": best_photo_hash,
                            }
                            needs_sync = True
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": True,
                                    "message": f"精选: {best_photo_filename[:25]}...",
                                }
                            )
                        else:
                            featured_info = existing_featured
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": True,
                                    "message": "精选照片哈希匹配，跳过",
                                }
                            )
                    else:
                        featured_info = existing_featured
                        AI_REVIEW_TASKS[task_id]["details"].append(
                            {
                                "date": date,
                                "success": False,
                                "message": "未选出精选照片",
                            }
                        )
                else:
                    featured_info = existing_featured
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {
                            "date": date,
                            "success": True,
                            "message": "无新照片，使用已有精选照片",
                        }
                    )

                # 步骤5：处理视频语音
                video_count = 0
                if needs_video_process and unprocessed_videos:
                    if cancel_if_requested(f"刷新任务已取消，停止于 {date}"):
                        return
                    try:
                        print(
                            f"[AI回顾] {date}: 找到 {len(unprocessed_videos)} 个未处理视频"
                        )
                        from video_audio_processor import (
                            process_video_speech,
                            analyze_language_ability,
                        )

                        baby_info = getattr(public_client, "baby_info", {}) or {}
                        baby_name = baby_info.get("baby_name", "宝宝")
                        age_months = baby_info.get("age_months", 0)
                        video_entry_map = {
                            item.get("path", ""): item
                            for item in videos
                            if item.get("path")
                        }

                        for video_path_str in unprocessed_videos:
                            if cancel_if_requested(f"刷新任务已取消，停止于 {date}"):
                                return

                            video_path = Path(video_path_str)
                            indexed_video_hash = (
                                video_entry_map.get(video_path_str, {}).get("hash") or ""
                            ).strip()
                            if not video_path.exists():
                                try:
                                    upload_resp = public_client.signed_request(
                                        "POST",
                                        f"{public_client.server_url}/czrz/speech/record",
                                        json={
                                            "client_id": server_client_id,
                                            "date": date,
                                            "video_path": str(video_path),
                                            "file_hash": indexed_video_hash,
                                            "transcript": "",
                                            "duration": 0,
                                            "analysis": _build_terminal_video_analysis(
                                                "索引存在但磁盘文件不存在",
                                                error_code="missing_source_file",
                                                note="视频文件不存在，按已处理跳过",
                                            ),
                                        },
                                        timeout=30,
                                        verify=False,
                                    )
                                    if upload_resp.status_code == 200:
                                        video_count += 1
                                        needs_sync = True
                                except Exception as upload_error:
                                    logger.info(
                                        f"[AI回顾] 视频缺失终态标记失败 {video_path}: {upload_error}"
                                    )
                                continue

                            try:
                                import time

                                time.sleep(2)
                                logger.info(
                                    f"[DEBUG-VIDEO] 开始处理视频: {video_path.name}"
                                )
                                speech_result = process_video_speech(
                                    video_path, max_duration=60
                                )

                                # 计算视频文件 hash
                                video_hash = indexed_video_hash or _resolve_media_hash(
                                    str(video_path)
                                )

                                # 准备上传数据
                                upload_data = {
                                    "client_id": server_client_id,
                                    "date": date,
                                    "video_path": str(video_path),
                                    "file_hash": video_hash,
                                    "duration": speech_result.get("duration", 0),
                                }

                                if speech_result.get("success") and speech_result.get(
                                    "transcript"
                                ):
                                    time.sleep(2)
                                    analysis = analyze_language_ability(
                                        speech_result["transcript"],
                                        baby_name=baby_name,
                                        age_months=age_months,
                                    )
                                    upload_data["transcript"] = speech_result[
                                        "transcript"
                                    ]
                                    upload_data["analysis"] = analysis
                                else:
                                    # 语音识别失败或无内容
                                    error_msg = speech_result.get("error", "未知错误")
                                    upload_data["transcript"] = ""
                                    upload_data["analysis"] = _build_terminal_video_analysis(
                                        error_msg,
                                        error_code="no_audio",
                                        note="语音识别失败或无有效语音",
                                    )

                                # 上传视频处理结果
                                upload_resp = public_client.signed_request(
                                    "POST",
                                    f"{public_client.server_url}/czrz/speech/record",
                                    json=upload_data,
                                    timeout=30,
                                    verify=False,
                                )

                                if upload_resp.status_code == 200:
                                    video_count += 1
                                    needs_sync = True
                                    logger.info(
                                        f"[AI回顾] 视频处理成功: {video_path.name}"
                                    )
                                else:
                                    logger.info(
                                        f"[AI回顾] 视频处理失败: status={upload_resp.status_code}"
                                    )

                            except Exception as e:
                                logger.info(f"[AI回顾] 视频处理失败 {video_path}: {e}")
                                try:
                                    upload_resp = public_client.signed_request(
                                        "POST",
                                        f"{public_client.server_url}/czrz/speech/record",
                                        json={
                                            "client_id": server_client_id,
                                            "date": date,
                                            "video_path": str(video_path),
                                            "file_hash": indexed_video_hash
                                            or _resolve_media_hash(str(video_path)),
                                            "transcript": "",
                                            "duration": 0,
                                            "analysis": _build_terminal_video_analysis(
                                                str(e),
                                                error_code="video_process_error",
                                            ),
                                        },
                                        timeout=30,
                                        verify=False,
                                    )
                                    if upload_resp.status_code == 200:
                                        video_count += 1
                                        needs_sync = True
                                except Exception as upload_error:
                                    logger.info(
                                        f"[AI回顾] 视频终态标记失败 {video_path}: {upload_error}"
                                    )

                        if video_count > 0:
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": True,
                                    "message": f"处理{video_count}个视频",
                                }
                            )
                        elif unprocessed_videos:
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": False,
                                    "message": f"视频处理失败",
                                }
                            )

                    except Exception as e:
                        logger.info(f"[AI回顾] 视频处理失败 {date}: {e}")

                # 获取照片描述用于生成日志
                all_descriptions = _filter_semantic_skip_photo_records(
                    get_processed_photos(date, server_client_id)
                )
                valid_photos = []
                for desc in all_descriptions:
                    if isinstance(desc, dict) and desc.get("description"):
                        valid_photos.append(desc)

                if valid_photos:
                    # 有照片描述，生成日志
                    photo_descs = [
                        p.get("description", "")
                        for p in valid_photos
                        if p.get("description")
                    ]

                    if photo_descs:
                        combined_desc = "、".join(photo_descs[:5])
                    else:
                        combined_desc = (
                            featured_info.get("ai_description")
                            if featured_info
                            else None
                        )

                    if combined_desc:
                        log_style = getattr(public_client, "log_style", "简练")
                        custom_style = getattr(public_client, "custom_style", "")
                        time.sleep(2)

                        try:
                            log_content = generate_ai_log(
                                date,
                                combined_desc,
                                server_client_id,
                                log_style,
                                custom_style,
                            )

                            if log_content:
                                featured_desc = (
                                    featured_info.get("ai_description")
                                    if featured_info
                                    else None
                                )
                                save_result = save_log_server(
                                    date, server_client_id, log_content, featured_desc
                                )
                                if save_result.get("success"):
                                    if not save_result.get("skipped"):
                                        needs_sync = True
                                    AI_REVIEW_TASKS[task_id]["details"].append(
                                        {
                                            "date": date,
                                            "success": True,
                                            "message": (
                                                "已有手动日志，跳过自动覆盖"
                                                if save_result.get("skipped")
                                                else "日志已生成"
                                            ),
                                        }
                                    )
                                else:
                                    AI_REVIEW_TASKS[task_id]["details"].append(
                                        {
                                            "date": date,
                                            "success": False,
                                            "message": save_result.get(
                                                "message", "日志保存失败"
                                            ),
                                        }
                                    )
                            else:
                                AI_REVIEW_TASKS[task_id]["details"].append(
                                    {
                                        "date": date,
                                        "success": False,
                                        "message": "日志生成失败",
                                    }
                                )
                        except Exception as e:
                            logger.info(f"[AI回顾] 生成日志失败 {date}: {e}")
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": False,
                                    "message": f"日志生成异常: {str(e)[:30]}",
                                }
                            )
                    else:
                        AI_REVIEW_TASKS[task_id]["details"].append(
                            {
                                "date": date,
                                "success": False,
                                "message": "无照片描述，无法生成日志",
                            }
                        )
                elif photo_paths:
                    # 有照片但无描述
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {
                            "date": date,
                            "success": False,
                            "message": "照片无描述，无法生成日志",
                        }
                    )
                else:
                    # 无照片
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {
                            "date": date,
                            "success": True,
                            "message": "无照片，跳过日志生成",
                        }
                    )

            except Exception as e:
                AI_REVIEW_TASKS[task_id]["details"].append(
                    {"date": date, "success": False, "message": str(e)[:50]}
                )

            # 记录处理结果
            if needs_sync:
                success_count += 1

            AI_REVIEW_TASKS[task_id]["success_count"] = success_count
            AI_REVIEW_TASKS[task_id]["skipped"] = skipped_count
            AI_REVIEW_TASKS[task_id]["processed"] = i + 1

            # 性能监控：记录结束时间
            date_end_time = time.time()
            process_time = date_end_time - date_start_time
            logger.info(f"[AI回顾]   性能: 处理 {date} 耗时 {process_time:.2f}秒")

            if cancel_if_requested(f"刷新任务已取消，停止于 {date}"):
                return

        # 计算实际处理天数（跳过不算）
        actual_processed = success_count

        if not dates_to_process:
            AI_REVIEW_TASKS[task_id]["message"] = "没有需要处理的日期"
        elif actual_processed == 0:
            AI_REVIEW_TASKS[task_id]["message"] = "完成！数据已是最新状态"
        elif skipped_count > 0:
            AI_REVIEW_TASKS[task_id]["message"] = "完成！AI刷新已完成"
        else:
            AI_REVIEW_TASKS[task_id]["message"] = "完成！AI刷新已完成"

        # 触发同步到健康AI并等待画像刷新结果
        try:
            server_url = USER_CONFIG.get("server_url", "")
            if server_url:
                import time as time_module

                if cancel_if_requested("刷新任务已取消，未继续同步到健康AI"):
                    return

                AI_REVIEW_TASKS[task_id]["portrait_refresh"] = "pending"
                AI_REVIEW_TASKS[task_id]["message"] = "正在同步到健康AI..."

                # 触发服务端同步（需要签名）
                public_client.signed_request(
                    "POST",
                    f"{server_url}/czrz/ai/sync-trigger",
                    json={
                        "client_id": public_client.client_id,
                        "child_id": child_id,
                    },
                    timeout=30,
                )
                logger.info("[AI回顾] 已触发服务端同步到健康AI")

                # 等待同步线程结束，避免画像刷新吃到半同步数据
                for _ in range(120):
                    if cancel_if_requested("刷新任务已取消，未继续生成画像"):
                        return
                    sync_status_resp = public_client.signed_request(
                        "GET",
                        f"{server_url}/czrz/ai/sync-status",
                        timeout=30,
                    )
                    if sync_status_resp.status_code != 200:
                        break
                    sync_status_payload = sync_status_resp.json() or {}
                    sync_status = sync_status_payload.get("status") or {}
                    if not sync_status.get("running"):
                        break
                    time_module.sleep(1)

                if cancel_if_requested("刷新任务已取消，未继续生成画像"):
                    return

                AI_REVIEW_TASKS[task_id]["message"] = "正在刷新画像..."
                refresh_resp = public_client.signed_request(
                    "POST",
                    f"{server_url}/czrz/profile/generate-trigger",
                    json={
                        "client_id": public_client.client_id,
                        "child_id": child_id,
                        "wait": False,
                    },
                    timeout=30,
                )
                if refresh_resp.status_code == 200:
                    refresh_payload = refresh_resp.json() or {}
                    if refresh_payload.get("success"):
                        portrait_deadline = time_module.time() + 3600
                        last_profile_message = refresh_payload.get("message") or ""
                        while time_module.time() < portrait_deadline:
                            if cancel_if_requested("刷新任务已取消，未继续等待画像完成"):
                                return

                            sync_status_resp = public_client.signed_request(
                                "GET",
                                f"{server_url}/czrz/ai/sync-status",
                                timeout=30,
                            )
                            if sync_status_resp.status_code != 200:
                                time_module.sleep(2)
                                continue

                            sync_status_payload = sync_status_resp.json() or {}
                            sync_status = sync_status_payload.get("status") or {}
                            profile_status = sync_status.get("profile_refresh") or "pending"
                            profile_message = (
                                sync_status.get("profile_refresh_message") or ""
                            )
                            profile_llm_valid = sync_status.get("profile_llm_valid")
                            if profile_message:
                                last_profile_message = profile_message

                            if profile_status in {"completed", "failed"}:
                                llm_valid = bool(profile_llm_valid)
                                AI_REVIEW_TASKS[task_id]["portrait_llm_valid"] = llm_valid
                                AI_REVIEW_TASKS[task_id]["portrait_refresh"] = profile_status
                                AI_REVIEW_TASKS[task_id]["portrait_message"] = (
                                    last_profile_message
                                    or (
                                        "画像生成完成"
                                        if profile_status == "completed"
                                        else "画像生成失败"
                                    )
                                )
                                break

                            time_module.sleep(2)
                        else:
                            AI_REVIEW_TASKS[task_id]["portrait_refresh"] = "pending"
                            AI_REVIEW_TASKS[task_id]["portrait_llm_valid"] = None
                            AI_REVIEW_TASKS[task_id]["portrait_message"] = (
                                last_profile_message or "画像仍在生成中"
                            )
                    else:
                        AI_REVIEW_TASKS[task_id]["portrait_refresh"] = "failed"
                        AI_REVIEW_TASKS[task_id]["portrait_llm_valid"] = False
                        AI_REVIEW_TASKS[task_id]["portrait_message"] = (
                            refresh_payload.get("message") or "画像刷新启动失败"
                        )
                else:
                    AI_REVIEW_TASKS[task_id]["portrait_refresh"] = "failed"
                    AI_REVIEW_TASKS[task_id]["portrait_llm_valid"] = False
                    AI_REVIEW_TASKS[task_id]["portrait_message"] = (
                        f"画像刷新请求失败: {refresh_resp.status_code}"
                    )
        except Exception as e:
            AI_REVIEW_TASKS[task_id]["portrait_refresh"] = "failed"
            AI_REVIEW_TASKS[task_id]["portrait_llm_valid"] = False
            AI_REVIEW_TASKS[task_id]["portrait_message"] = str(e)
            logger.info(f"[AI回顾] 触发同步/画像生成失败: {e}")

        if AI_REVIEW_TASKS[task_id].get("portrait_refresh") == "failed":
            AI_REVIEW_TASKS[task_id]["status"] = "failed"
            AI_REVIEW_TASKS[task_id]["message"] = (
                AI_REVIEW_TASKS[task_id].get("portrait_message")
                or "素材刷新完成，但画像生成失败"
            )
        elif AI_REVIEW_TASKS[task_id].get("portrait_refresh") == "completed":
            AI_REVIEW_TASKS[task_id]["status"] = "completed"
            AI_REVIEW_TASKS[task_id]["message"] = "完成！AI刷新和画像生成已完成"
        elif AI_REVIEW_TASKS[task_id].get("portrait_refresh") == "pending":
            AI_REVIEW_TASKS[task_id]["status"] = "completed"
            AI_REVIEW_TASKS[task_id]["message"] = "素材刷新完成，画像仍在生成中"
        else:
            AI_REVIEW_TASKS[task_id]["status"] = "completed"

        AI_REVIEW_TASKS[task_id]["completed"] = True

    except Exception as e:
        if AI_REVIEW_TASKS.get(task_id, {}).get("status") == "cancelled":
            AI_REVIEW_TASKS[task_id]["completed"] = True
        else:
            AI_REVIEW_TASKS[task_id]["completed"] = True
            AI_REVIEW_TASKS[task_id]["status"] = "failed"
            AI_REVIEW_TASKS[task_id]["message"] = f"任务失败: {str(e)}"


def get_featured_photo_info(date: str, client_id: str, timeout: int = 8) -> dict:
    """获取精选照片信息（从服务端获取，包含哈希）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/photo/featured",
            params={"client_id": client_id, "date": date},
            timeout=timeout,
        )
        logger.info(
            f"[DEBUG] get_featured_photo_info: status={resp.status_code}, url={resp.url}"
        )
        if resp.status_code == 200:
            result = resp.json()
            logger.info(f"[DEBUG] get_featured_photo_info: result={result}")
            if result.get("success") and result.get("photo"):
                return result["photo"]
        else:
            logger.info(
                f"[DEBUG] get_featured_photo_info: error status={resp.status_code}, text={resp.text[:200]}"
            )
    except Exception as e:
        logger.info(f"[DEBUG] get_featured_photo_info: exception={e}")
    return None


def _compute_file_hash(file_path: str) -> str:
    path_obj = Path(file_path or "")
    if not path_obj.exists() or not path_obj.is_file():
        return ""

    hasher = hashlib.md5()
    with open(path_obj, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _get_hash_backfill_photo_manager():
    media_folders = tuple(
        str(Path(folder))
        for folder in (getattr(public_client, "media_folders", []) or [])
        if folder and Path(folder).exists()
    )
    if not media_folders:
        return None

    cached_folders = HASH_BACKFILL_PM_CACHE.get("media_folders")
    cached_manager = HASH_BACKFILL_PM_CACHE.get("manager")
    if cached_manager is not None and cached_folders == media_folders:
        return cached_manager

    try:
        from photo_manager import PhotoManager

        manager = PhotoManager(list(media_folders), public_client.data_dir)
    except Exception as e:
        logger.info(f"[AI回顾] 初始化 PhotoManager 失败，跳过 hash 回填: {e}")
        return None

    HASH_BACKFILL_PM_CACHE["media_folders"] = media_folders
    HASH_BACKFILL_PM_CACHE["manager"] = manager
    return manager


def _load_raw_photo_index_maps():
    index_file = USER_DATA_DIR / "photo_index" / "photo_index.json"
    if not index_file.exists():
        return RAW_PHOTO_INDEX_CACHE

    try:
        current_mtime = index_file.stat().st_mtime
        if RAW_PHOTO_INDEX_CACHE.get("mtime") == current_mtime:
            return RAW_PHOTO_INDEX_CACHE

        payload = json.loads(index_file.read_text(encoding="utf-8"))
        by_path = {}
        by_filename = {}
        for bucket in ("photos", "videos"):
            entries = payload.get(bucket, {}) or {}
            if not isinstance(entries, dict):
                continue
            for entry in entries.values():
                if not isinstance(entry, dict):
                    continue
                file_hash = (entry.get("hash") or "").strip()
                file_path = (entry.get("path") or "").strip()
                filename = (entry.get("filename") or Path(file_path).name or "").strip()
                if not file_hash:
                    continue
                if file_path:
                    by_path[file_path] = file_hash
                if filename and filename not in by_filename:
                    by_filename[filename] = file_hash

        RAW_PHOTO_INDEX_CACHE["mtime"] = current_mtime
        RAW_PHOTO_INDEX_CACHE["by_path"] = by_path
        RAW_PHOTO_INDEX_CACHE["by_filename"] = by_filename
    except Exception as e:
        logger.info(f"[AI回顾] 读取原始索引失败，跳过原始索引回退: {e}")

    return RAW_PHOTO_INDEX_CACHE


def _resolve_media_hash(file_path: str, photo_manager=None) -> str:
    file_path = (file_path or "").strip()
    if not file_path:
        return ""

    resolved_hash = ""
    try:
        entry = (
            photo_manager.get_photo_by_path(file_path)
            if photo_manager and hasattr(photo_manager, "get_photo_by_path")
            else None
        )
        if not entry and photo_manager and hasattr(photo_manager, "get_photo_by_filename"):
            entry = photo_manager.get_photo_by_filename(Path(file_path).name)
        if entry:
            resolved_hash = (entry.get("hash") or "").strip()
        if not resolved_hash:
            raw_index = _load_raw_photo_index_maps()
            resolved_hash = (
                raw_index.get("by_path", {}).get(file_path)
                or raw_index.get("by_filename", {}).get(Path(file_path).name, "")
            ).strip()
        if not resolved_hash:
            resolved_hash = _compute_file_hash(file_path)
    except Exception as e:
        logger.info(f"[AI回顾] 解析媒体 hash 失败 {file_path}: {e}")
    return resolved_hash


def _backfill_missing_photo_hashes(date: str, client_id: str, photos: list) -> list:
    """用本地索引回填服务端旧记录中的空 hash，避免老素材被反复重读。"""
    if not photos or client_id != public_client.client_id:
        return photos

    photo_manager = _get_hash_backfill_photo_manager()
    if not photo_manager:
        return photos

    updated = []
    repaired_payload = []

    for item in photos:
        current = dict(item or {})
        file_path = (current.get("path") or "").strip()
        file_hash = (current.get("hash") or current.get("file_hash") or "").strip()

        if not file_path or file_hash:
            updated.append(current)
            continue

        resolved_hash = _resolve_media_hash(file_path, photo_manager)

        if resolved_hash:
            current["hash"] = resolved_hash
            repaired_payload.append(
                {
                    "path": file_path,
                    "hash": resolved_hash,
                    "description": current.get("description", "") or "",
                    "has_baby": current.get("has_baby", True),
                    "scene": current.get("scene", "") or "",
                    "activity": current.get("activity", "") or "",
                    "processed_status": current.get("processed_status", "") or PHOTO_STATUS_OK,
                    "processed_error_code": current.get("processed_error_code", "") or "",
                    "processed_error_detail": current.get("processed_error_detail", "") or "",
                }
            )
        updated.append(current)

    if repaired_payload:
        try:
            resp = public_client.signed_request(
                "POST",
                f"{public_client.server_url}/czrz/photos/descriptions",
                json={
                    "client_id": client_id,
                    "date": date,
                    "photos": repaired_payload,
                },
                timeout=30,
            )
            if resp.status_code == 200:
                logger.info(
                    f"[AI回顾] 已回填 {date} 缺失照片 hash: {len(repaired_payload)} 条"
                )
        except Exception as e:
            logger.info(f"[AI回顾] 回填照片 hash 上传失败: {e}")

    return updated


def _parse_language_analysis_payload(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return None
    return None


def _build_terminal_video_analysis(
    error_text: str,
    *,
    error_code: str = "other_error",
    note: str = "视频处理异常，按已处理跳过",
    processed_status: str = "other_error",
) -> dict:
    return {
        "error": str(error_text or "").strip(),
        "note": note,
        "processed_status": processed_status,
        "processed_error_code": error_code,
        "processed_error_detail": str(error_text or "").strip(),
    }


def _video_record_has_completed_read(record: dict) -> bool:
    """判断视频记录是否已经完成读取，或已明确确认素材无有效语音。"""
    if not isinstance(record, dict):
        return False

    transcript = str(record.get("transcript") or "").strip()
    if transcript:
        return True

    confirmed_invalid_markers = (
        "识别结果为空",
        "未检测到有效语音",
        "音频全是静音",
        "音频时长过短",
        "无有效语音",
        "无明显语音",
        "无语音",
        "静音",
        "语音识别失败或无有效语音",
        "音频提取失败",
        "output file does not contain any stream",
        "error opening output file",
        "error opening output files",
        "视频文件不存在",
        "索引存在但磁盘文件不存在",
    )

    raw_analysis_text = str(record.get("language_analysis") or "").strip()
    analysis = _parse_language_analysis_payload(record.get("language_analysis"))

    if isinstance(analysis, dict):
        processed_status = str(analysis.get("processed_status") or "").strip().lower()
        if processed_status and processed_status not in {"retry", "needs_retry", "pending"}:
            return True
        processed_error_code = str(analysis.get("processed_error_code") or "").strip().lower()
        if processed_error_code and processed_error_code not in {"retry", "needs_retry", "pending"}:
            return True
        error_text = str(analysis.get("error") or "").strip()
        note_text = str(analysis.get("note") or "").strip()
        combined_text = "\n".join(part for part in (error_text, note_text) if part)
        combined_text_lower = combined_text.lower()
        if any(marker.lower() in combined_text_lower for marker in confirmed_invalid_markers):
            return True
        return False

    raw_analysis_text_lower = raw_analysis_text.lower()
    if any(marker.lower() in raw_analysis_text_lower for marker in confirmed_invalid_markers):
        return True

    return False


def _backfill_missing_video_hashes(date: str, client_id: str, videos: list) -> list:
    """回填旧语音记录中的空视频 hash，避免视频被反复转写。"""
    if not videos or client_id != public_client.client_id:
        return videos

    photo_manager = _get_hash_backfill_photo_manager()
    if not photo_manager:
        return videos

    updated = []
    repaired_count = 0

    for item in videos:
        current = dict(item or {})
        video_path = (current.get("video_path") or "").strip()
        file_hash = (current.get("file_hash") or current.get("hash") or "").strip()

        if not video_path or file_hash:
            updated.append(current)
            continue

        resolved_hash = _resolve_media_hash(video_path, photo_manager)
        if not resolved_hash:
            updated.append(current)
            continue

        current["file_hash"] = resolved_hash
        upload_data = {
            "client_id": client_id,
            "date": date,
            "video_path": video_path,
            "file_hash": resolved_hash,
            "transcript": current.get("transcript", "") or "",
            "duration": current.get("duration", 0) or 0,
        }
        parsed_analysis = _parse_language_analysis_payload(
            current.get("language_analysis")
        )
        if parsed_analysis is not None:
            upload_data["analysis"] = parsed_analysis

        try:
            resp = public_client.signed_request(
                "POST",
                f"{public_client.server_url}/czrz/speech/record",
                json=upload_data,
                timeout=30,
            )
            if resp.status_code == 200:
                repaired_count += 1
        except Exception as e:
            logger.info(f"[AI回顾] 回填视频 hash 上传失败 {video_path}: {e}")

        updated.append(current)

    if repaired_count:
        logger.info(f"[AI回顾] 已回填 {date} 缺失视频 hash: {repaired_count} 条")

    return updated


def get_processed_photos(date: str, client_id: str) -> list:
    """获取某天已处理的照片列表（从服务端）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/photo/descriptions",
            params={"client_id": client_id, "date": date},
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                photos = result.get("photos") or result.get("data", {}).get("photos", [])
                return _backfill_missing_photo_hashes(date, client_id, photos)
    except Exception as e:
        logger.info(f"[AI回顾] 获取已处理照片失败: {e}")
    return []


def _filter_semantic_skip_photo_records(records: list) -> list:
    return [
        dict(item)
        for item in (records or [])
        if isinstance(item, dict) and not is_semantic_skip_photo_record(item)
    ]


def _empty_photo_describe_result() -> dict:
    return {
        "selected": [],
        "blurry": [],
        "duplicates": [],
        "no_baby": [],
        "photos": {},
        "blurry_filenames": [],
        "duplicate_filenames": [],
        "no_baby_filenames": [],
        "reasons": [],
    }


def _merge_photo_describe_results(base: dict, incoming: dict) -> dict:
    if not incoming:
        return base

    base.setdefault("photos", {}).update(incoming.get("photos", {}) or {})

    for list_key in ("selected", "blurry", "no_baby", "blurry_filenames", "no_baby_filenames", "reasons"):
        existing = base.setdefault(list_key, [])
        seen = set(existing)
        for item in incoming.get(list_key, []) or []:
            if item in seen:
                continue
            existing.append(item)
            seen.add(item)

    for group_key in ("duplicates", "duplicate_filenames"):
        existing = base.setdefault(group_key, [])
        seen = {tuple(group) for group in existing if isinstance(group, list)}
        for group in incoming.get(group_key, []) or []:
            if not isinstance(group, list):
                continue
            marker = tuple(group)
            if marker in seen:
                continue
            existing.append(group)
            seen.add(marker)

    return base


def _describe_photos_with_block_fallback(
    photo_paths: list,
    child_id: str,
    client_id: str,
    date: str,
    log_prefix: str = "[AI回顾]",
) -> tuple[dict | None, list]:
    from select_best_photo import (
        describe_photo_batch,
        save_blocked_photo_descriptions_to_server,
        save_other_error_photo_descriptions_to_server,
    )

    target_paths = [path for path in (photo_paths or []) if path]
    if not target_paths:
        return None, []

    merged = _empty_photo_describe_result()
    skipped_records = []
    chunk_size = 20

    for chunk_start in range(0, len(target_paths), chunk_size):
        chunk_paths = target_paths[chunk_start : chunk_start + chunk_size]

        try:
            chunk_result = describe_photo_batch(chunk_paths, child_id=child_id)
            _merge_photo_describe_results(merged, chunk_result)
            continue
        except Exception as e:
            batch_error = classify_terminal_photo_error(e)
            if not batch_error:
                raise

            logger.info(f"{log_prefix} 视觉批量分析触发终态异常，回退单张识别: {e}")
            blocked_paths = []

            for photo_path in chunk_paths:
                try:
                    single_result = describe_photo_batch([photo_path], child_id=child_id)
                    _merge_photo_describe_results(merged, single_result)
                except Exception as single_e:
                    single_error = classify_terminal_photo_error(single_e)
                    if not single_error:
                        raise
                    if single_error.get("processed_status") == PHOTO_STATUS_BLOCKED:
                        blocked_paths.append(photo_path)
                        continue
                    skipped_records.extend(
                        save_other_error_photo_descriptions_to_server(
                            client_id,
                            date,
                            [photo_path],
                            error_detail=single_error.get("processed_error_detail", ""),
                            error_code=single_error.get(
                                "processed_error_code",
                                PHOTO_STATUS_OTHER_ERROR,
                            ),
                        )
                    )

            if blocked_paths:
                skipped_records.extend(
                    save_blocked_photo_descriptions_to_server(
                        client_id,
                        date,
                        blocked_paths,
                    )
                )

    if skipped_records:
        status_counter = Counter(
            (item.get("processed_status") or PHOTO_STATUS_OK)
            for item in skipped_records
        )
        logger.info(
            f"{log_prefix} 终态异常图片已标记为已处理: "
            f"blocked={status_counter.get(PHOTO_STATUS_BLOCKED, 0)}, "
            f"other_error={status_counter.get(PHOTO_STATUS_OTHER_ERROR, 0)}"
        )

    has_describe_result = any(
        merged.get(key)
        for key in ("photos", "blurry", "duplicates", "no_baby")
    )
    return (merged if has_describe_result else None), skipped_records


def _story_trim_text(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    return cleaned[:limit]


def _story_dedup_texts(items: list, limit: int = 6, max_len: int = 24) -> list:
    seen = set()
    result = []
    for item in items or []:
        text = _story_trim_text(item, max_len)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _story_pick_seed(options: list, seed_source: str, salt: str = "") -> str:
    if not options:
        return ""
    digest = hashlib.md5(f"{seed_source}|{salt}".encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


def _story_score_keywords(text: str, keywords: list) -> int:
    raw = str(text or "")
    return sum(1 for word in keywords if word and word in raw)


def _story_guess_emotion(profile_bundle: dict, tag_lines: list, feedback_lines: list) -> str:
    emotion_keywords = {
        "开心": ["笑", "开心", "快乐", "高兴", "灿烂", "轻松"],
        "平静": ["安静", "平稳", "专注", "柔和", "稳定", "沉静"],
        "好奇": ["好奇", "探索", "观察", "发现", "研究", "新鲜"],
        "困倦": ["睡", "困", "午睡", "打哈欠", "休息"],
        "撒娇": ["抱", "依恋", "黏", "撒娇", "贴贴"],
        "哭闹": ["哭", "委屈", "闹", "不舒服", "烦躁"],
    }
    text_pool = " ".join(
        list(profile_bundle.get("traits", []))
        + list(profile_bundle.get("interest_current", []))
        + list(profile_bundle.get("recent_changes", []))
        + list(tag_lines or [])
        + list(feedback_lines or [])
    )
    scores = {}
    for emotion, keywords in emotion_keywords.items():
        scores[emotion] = sum(1 for word in keywords if word and word in text_pool)
    best = max(scores.items(), key=lambda item: item[1]) if scores else ("好奇", 0)
    return best[0] if best[1] > 0 else "好奇"


def _story_collect_domain_scores(text_pool: str) -> dict:
    domain_specs = {
        "focus": ["专注", "认真", "稳定", "安静", "平静", "观察", "研究", "沉稳"],
        "empathy": ["温柔", "安抚", "照顾", "照看", "回应", "贴心", "陪伴", "关心"],
        "mobility": ["站", "站立", "迈步", "走", "散步", "跑", "爬", "台阶", "楼梯", "大运动", "外出"],
        "exploration": ["好奇", "探索", "发现", "新鲜", "侦察", "线索", "观察", "巡路"],
        "language": ["说", "表达", "模仿", "聊天", "台词", "唱", "叫", "回应"],
        "craft": ["抓", "捏", "翻", "搭", "拼", "画", "积木", "精细"],
        "rhythm": ["音乐", "节奏", "跳舞", "律动"],
        "resilience": ["坚持", "反复", "勇敢", "耐心", "适应", "稳住", "练习"],
    }
    scores = {}
    for name, keywords in domain_specs.items():
        scores[name] = _story_score_keywords(text_pool, keywords)
    return scores


def _story_extract_growth_clues(text_pool: str) -> list:
    clue_specs = [
        ("站立", ["站立", "扶站", "站稳", "站起来"]),
        ("迈步", ["迈步", "走路", "散步", "往前走", "外出"]),
        ("爬台阶", ["台阶", "楼梯", "爬高", "上坡"]),
        ("大运动", ["大运动", "跑", "追", "冲", "平衡"]),
        ("专注观察", ["专注", "观察", "认真", "研究", "看了很久"]),
        ("安抚照看", ["安抚", "照顾", "陪伴", "贴贴", "回应"]),
        ("语言表达", ["表达", "模仿", "聊天", "说话", "台词"]),
        ("小手操作", ["抓", "捏", "翻", "搭", "拼", "画"]),
    ]
    clues = []
    for label, keywords in clue_specs:
        if _story_score_keywords(text_pool, keywords) > 0:
            clues.append(label)
    return _story_dedup_texts(clues, limit=5, max_len=14)


def _story_build_role_seed_prompt(
    seed_source: str,
    profile_bundle: dict,
    tag_candidates: list,
    interest_candidates: list,
    feedback_lines: list,
    panel_sources: list,
    growth_clues: list,
    top_scene: str,
    top_activity: str,
    top_emotion: str,
) -> str:
    gender_hint = (getattr(public_client, "baby_gender", "") or "").strip()
    if gender_hint:
        audience_hint = f"要贴合这个宝宝的年龄感与性别气质（{gender_hint}）"
    else:
        audience_hint = "要贴合这个宝宝的年龄感与真实气质"
    real_actions = _story_dedup_texts(
        [
            item.get("activity", "") or item.get("description", "")
            for item in panel_sources or []
        ]
        + [item.get("description", "") for item in panel_sources or []],
        limit=6,
        max_len=22,
    )
    prompt_lines = [
        f"名字：{public_client.baby_name}",
        f"性别：{getattr(public_client, 'baby_gender', '') or '未知'}",
        f"画像标签：{'、'.join(tag_candidates) or '暂无'}",
        f"兴趣线索：{'、'.join(interest_candidates) or '暂无'}",
        f"近期变化：{'、'.join(profile_bundle.get('recent_changes', [])) or '暂无'}",
        f"家长反馈：{'；'.join(feedback_lines[:3]) or '暂无'}",
        f"真实动作：{'、'.join(real_actions) or '暂无'}",
        f"常见场景：{top_scene or '暂无'}",
        f"当前活动：{top_activity or '暂无'}",
        f"情绪底色：{top_emotion or '好奇'}",
        f"成长线索：{'、'.join(growth_clues) or '暂无'}",
    ]
    return f"""你是儿童连载漫画的角色设定编辑。
请根据宝宝的长期画像信号，生成一个可以稳定连载的人物设定。

创作重点：
1. 角色和世界观要从真实信号里自然长出来，不要做词语换皮。
2. 角色{audience_hint}，温柔、具体、好记，不要成人化。
3. 这个设定必须允许日常小事反复进入剧情，例如观察、指认、被抱着、玩耍、外出、安静等待、运动尝试。
4. 不要套用固定题材池、角色池或示例母题；必须由画像和真实素材自己长出来。
5. 只给一个最终设定，不要候选，不要解释。

输出严格 JSON：
{{
  "hero_archetype": "8到16字的稳定角色称呼",
  "world_name": "6到12字的固定世界名",
  "core": "28字内，说明这个主角最像宝宝本人的核心气质",
  "signature_line": "34字内，稳定识别句",
  "catchphrase": "40字内，旁白口吻",
  "motifs": ["2到6字母题1", "2到6字母题2", "2到6字母题3"]
}}

不要 markdown，不要附加说明。
真实信号：
{chr(10).join(prompt_lines)}
稳定种子：{seed_source[:80]}
"""


def _story_generate_role_seed_via_llm(
    client_id: str,
    seed_source: str,
    profile_bundle: dict,
    tag_candidates: list,
    interest_candidates: list,
    feedback_lines: list,
    panel_sources: list,
    growth_clues: list,
    top_scene: str,
    top_activity: str,
    top_emotion: str,
) -> dict:
    if not getattr(public_client, "server_url", ""):
        return {}

    prompt = _story_build_role_seed_prompt(
        seed_source,
        profile_bundle,
        tag_candidates,
        interest_candidates,
        feedback_lines,
        panel_sources,
        growth_clues,
        top_scene,
        top_activity,
        top_emotion,
    )
    try:
        response = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/ai/proxy/text",
            json={
                "client_id": client_id or public_client.client_id,
                "prompt": prompt,
                "max_tokens": 900,
                "operation": "story_series_seed",
            },
            timeout=1200,
        )
        if response.status_code != 200:
            return {}
        result = response.json()
        if not result.get("success"):
            return {}
        payload = _extract_json_payload((result.get("result") or {}).get("content", ""))
        if not isinstance(payload, dict):
            return {}
        hero_archetype = _story_trim_text(payload.get("hero_archetype"), 18)
        world_name = _story_trim_text(payload.get("world_name"), 16)
        motifs = payload.get("motifs") or []
        if isinstance(motifs, str):
            motifs = re.split(r"[、,，；;\\s]+", motifs)
        if not hero_archetype or not world_name:
            return {}
        return {
            "id": "series_seed_llm",
            "title": hero_archetype,
            "world": world_name,
            "core": _story_trim_text(payload.get("core", ""), 48),
            "motifs": _story_dedup_texts(motifs, limit=4, max_len=12),
            "signature_line": _story_trim_text(payload.get("signature_line", ""), 34),
            "catchphrase": _story_trim_text(payload.get("catchphrase", ""), 48),
        }
    except Exception as e:
        logger.info(f"[Story] 角色种子生成失败: {e}")
        return {}


def get_story_series_profile(client_id: str, panel_sources: list = None, episodes: list = None) -> dict:
    for episode in reversed(episodes or []):
        cached = dict(episode.get("series_profile") or {})
        if cached and (cached.get("story_engine") or cached.get("story_motifs")):
            cached["role_name"] = public_client.baby_name
            cached.setdefault("world_name", episode.get("world_name") or "")
            cached.setdefault(
                "series_name",
                episode.get("series_name") or f"《{public_client.baby_name}的成长连环画》",
            )
            cached.setdefault("catchphrase", episode.get("catchphrase") or "")
            cached.setdefault("signature_traits", episode.get("signature_traits") or [])
            cached.setdefault("world_rules", episode.get("world_rules") or [])
            cached.setdefault("signature_line", episode.get("signature") or "")
            cached.setdefault("hero_archetype", episode.get("hero_archetype") or "")
            cached.setdefault("ability_tracks", episode.get("ability_tracks") or [])
            cached.setdefault("gear_tracks", episode.get("gear_tracks") or [])
            cached.setdefault("story_engine", episode.get("story_engine") or "")
            cached.setdefault("story_motifs", episode.get("story_motifs") or [])
            cached.setdefault("top_growth_clues", episode.get("top_growth_clues") or [])
            return cached

    profile_bundle = get_story_profile_bundle(client_id)
    feedback_lines = get_story_feedback_records(client_id, limit=4)
    filenames = [item.get("filename", "") for item in panel_sources or [] if item.get("filename")]
    tag_lines = get_story_tag_records(client_id, filenames, limit=6)

    scene_counter = Counter()
    activity_counter = Counter()
    for item in panel_sources or []:
        scene_text = _story_trim_text(item.get("scene", ""), 12)
        activity_text = _story_trim_text(item.get("activity", "") or item.get("description", ""), 14)
        if scene_text:
            scene_counter[scene_text] += 1
        if activity_text:
            activity_counter[activity_text] += 1

    top_scene = scene_counter.most_common(1)[0][0] if scene_counter else "家里"
    top_activity = activity_counter.most_common(1)[0][0] if activity_counter else (
        profile_bundle.get("interest_current", ["玩耍"])[0]
        if profile_bundle.get("interest_current")
        else "玩耍"
    )
    top_emotion = _story_guess_emotion(profile_bundle, tag_lines, feedback_lines)

    tag_candidates = _story_dedup_texts(
        list(profile_bundle.get("profile_tags", []))
        + list(profile_bundle.get("traits", []))
        + list(tag_lines),
        limit=6,
        max_len=14,
    )
    interest_candidates = _story_dedup_texts(
        list(profile_bundle.get("interest_current", []))
        + list(profile_bundle.get("interest_emerging", [])),
        limit=4,
        max_len=14,
    )
    speech_hints = _story_dedup_texts(
        list(profile_bundle.get("strengths", []))
        + list(profile_bundle.get("suggestions", [])),
        limit=4,
        max_len=18,
    )

    signal_pool = " ".join(
        list(profile_bundle.get("traits", []))
        + list(profile_bundle.get("interest_current", []))
        + list(profile_bundle.get("interest_emerging", []))
        + list(profile_bundle.get("profile_tags", []))
        + list(profile_bundle.get("recent_changes", []))
        + list(tag_lines)
        + list(feedback_lines)
        + list(speech_hints)
        + [item.get("description", "") for item in panel_sources or []]
        + [item.get("activity", "") for item in panel_sources or []]
        + [item.get("scene", "") for item in panel_sources or []]
    )
    domain_scores = _story_collect_domain_scores(signal_pool)
    growth_clues = _story_extract_growth_clues(signal_pool)

    seed_source = "|".join(
        [
            client_id or "",
            public_client.baby_name or "",
            top_scene,
            top_emotion,
            ",".join(sorted(name for name, score in domain_scores.items() if score > 0)[:3]),
            ",".join(growth_clues[:3]),
            ",".join(tag_candidates[:4]),
            ";".join(feedback_lines[:2]),
        ]
    )
    role_blueprint = _story_load_series_seed(client_id)
    if not role_blueprint:
        role_blueprint = _story_generate_role_seed_via_llm(
            client_id,
            seed_source,
            profile_bundle,
            tag_candidates,
            interest_candidates,
            feedback_lines,
            panel_sources,
            growth_clues,
            top_scene,
            top_activity,
            top_emotion,
        )
        if role_blueprint:
            _story_save_series_seed(client_id, role_blueprint)

    hero_archetype = role_blueprint.get("title") or role_blueprint.get("hero_archetype") or ""
    role_name = public_client.baby_name
    world_name = role_blueprint.get("world") or role_blueprint.get("world_name") or ""
    ability_tracks = []
    gear_tracks = []
    story_motifs = []
    world_rules = []
    story_engine = ""
    signature_traits = []

    signature_line = role_blueprint.get("signature_line") or ""
    catchphrase = role_blueprint.get("catchphrase") or ""
    series_name = f"《{public_client.baby_name}的成长连环画》"
    subtitle_line = (
        f"让{public_client.baby_name}始终保持同一主角画像，把每天真实的成长推进写成可持续更新的漫画连载。"
    )

    return {
        "role_name": role_name,
        "hero_archetype": hero_archetype,
        "world_name": world_name,
        "series_name": series_name,
        "catchphrase": catchphrase,
        "signature_line": signature_line,
        "signature_traits": signature_traits[:4],
        "world_rules": world_rules,
        "ability_tracks": ability_tracks,
        "gear_tracks": gear_tracks,
        "story_engine": story_engine,
        "story_motifs": story_motifs,
        "top_growth_clues": [],
        "top_scene": top_scene,
        "top_activity": top_activity,
        "top_emotion": top_emotion,
        "profile_tags": tag_candidates[:6],
        "subtitle_line": subtitle_line,
        "seed_source": seed_source,
    }


def _hydrate_story_series_fields(
    date_str: str,
    story: dict,
    client_id: str = None,
    series_profile: dict = None,
) -> dict:
    if not story:
        return None

    hydrated = _normalize_story_payload(story)
    merged_profile = dict(series_profile or {})
    cached_profile = dict(hydrated.get("series_profile") or {})
    if cached_profile:
        merged_profile.update(cached_profile)

    has_story_profile = hydrated.get("role_name") and hydrated.get("world_name")
    has_series_profile = merged_profile.get("role_name") and merged_profile.get("world_name")
    needs_profile = not (has_story_profile or has_series_profile)
    if needs_profile and client_id and date_str:
        try:
            panel_sources = _pick_story_panel_sources(
                _prepare_story_photo_records(date_str, client_id)
            )
            computed = get_story_series_profile(
                client_id,
                panel_sources=panel_sources,
                episodes=_get_story_prior_episodes(client_id, date_str, limit=6),
            )
            for key, value in computed.items():
                merged_profile.setdefault(key, value)
        except Exception as e:
            logger.info(f"[Story] 补齐故事世界观失败 {date_str}: {e}")

    role_name = public_client.baby_name
    world_name = hydrated.get("world_name") or merged_profile.get("world_name") or ""
    series_name = (
        hydrated.get("series_name")
        or merged_profile.get("series_name")
        or f"《{public_client.baby_name}的成长连环画》"
    )
    signature = hydrated.get("signature") or merged_profile.get("signature_line") or ""
    catchphrase = hydrated.get("catchphrase") or merged_profile.get("catchphrase") or ""
    hero_archetype = hydrated.get("hero_archetype") or merged_profile.get("hero_archetype") or ""
    signature_traits = hydrated.get("signature_traits") or merged_profile.get("signature_traits") or []
    world_rules = hydrated.get("world_rules") or merged_profile.get("world_rules") or []
    story_engine = hydrated.get("story_engine") or merged_profile.get("story_engine") or ""
    story_motifs = hydrated.get("story_motifs") or merged_profile.get("story_motifs") or []
    top_growth_clues = hydrated.get("top_growth_clues") or merged_profile.get("top_growth_clues") or []
    ability_tracks = hydrated.get("ability_tracks") or merged_profile.get("ability_tracks") or []
    gear_tracks = hydrated.get("gear_tracks") or merged_profile.get("gear_tracks") or []
    skill_progress = hydrated.get("skill_progress") or ""
    gear_unlock = hydrated.get("gear_unlock") or ""

    hydrated["role_name"] = role_name
    hydrated["world_name"] = world_name
    hydrated["series_name"] = series_name
    hydrated["signature"] = signature
    hydrated["catchphrase"] = catchphrase
    hydrated["hero_archetype"] = hero_archetype
    hydrated["signature_traits"] = signature_traits
    hydrated["world_rules"] = world_rules
    hydrated["story_engine"] = story_engine
    hydrated["story_motifs"] = story_motifs
    hydrated["top_growth_clues"] = top_growth_clues
    hydrated["ability_tracks"] = ability_tracks
    hydrated["gear_tracks"] = gear_tracks
    hydrated["skill_progress"] = skill_progress
    hydrated["gear_unlock"] = gear_unlock
    hydrated["fantasy_mission"] = hydrated.get("fantasy_mission") or ""
    hydrated["real_world_thread"] = hydrated.get("real_world_thread") or ""

    story_profile_payload = dict(merged_profile)
    story_profile_payload["role_name"] = role_name
    story_profile_payload["world_name"] = world_name
    story_profile_payload["series_name"] = series_name
    story_profile_payload["catchphrase"] = catchphrase
    story_profile_payload["signature_line"] = signature
    story_profile_payload["hero_archetype"] = hero_archetype
    story_profile_payload["signature_traits"] = signature_traits
    story_profile_payload["world_rules"] = world_rules
    story_profile_payload["story_engine"] = story_engine
    story_profile_payload["story_motifs"] = story_motifs
    story_profile_payload["top_growth_clues"] = top_growth_clues
    story_profile_payload["ability_tracks"] = ability_tracks
    story_profile_payload["gear_tracks"] = gear_tracks
    hydrated["series_profile"] = story_profile_payload
    return hydrated


def get_story_feedback_records(client_id: str, limit: int = 4) -> list:
    """读取最近画像反馈，帮助故事更贴近本人。"""
    try:
        from sqlmodel import Session, select
        from database import get_user_engine
        from models import ProfileFeedback

        engine = get_user_engine(client_id)
        with Session(engine) as session:
            rows = session.exec(
                select(ProfileFeedback)
                .order_by(ProfileFeedback.created_at.desc(), ProfileFeedback.id.desc())
                .limit(limit)
            ).all()
        return [_story_trim_text(row.feedback_text, 40) for row in rows if row.feedback_text]
    except Exception as e:
        logger.info(f"[Story] 获取画像反馈失败: {e}")
        return []


def get_story_tag_records(client_id: str, filenames: list = None, limit: int = 8) -> list:
    """读取近期手动标签，避免故事角色过度模板化。"""
    try:
        from sqlmodel import Session, select
        from database import get_user_engine
        from models import PhotoTag

        engine = get_user_engine(client_id)
        filenames = [name for name in (filenames or []) if name]
        with Session(engine) as session:
            query = select(PhotoTag).order_by(PhotoTag.updated_at.desc(), PhotoTag.id.desc())
            rows = session.exec(query).all()

        def collect(only_match: bool) -> list:
            picked = []
            seen = set()
            for row in rows:
                if only_match and filenames and row.filename not in filenames:
                    continue
                text = _story_trim_text(row.tag or "", 18)
                if not text or text in seen:
                    continue
                seen.add(text)
                picked.append(text)
                if len(picked) >= limit:
                    break
            return picked

        matched = collect(only_match=True)
        if matched or not filenames:
            return matched
        return collect(only_match=False)
    except Exception as e:
        logger.info(f"[Story] 获取手动标签失败: {e}")
        return []


def get_story_profile_bundle(client_id: str) -> dict:
    """提取适合故事系统使用的长期画像信号。"""
    profile = get_local_profile_data() or {}
    dimensions = profile.get("dimensions", {}) or {}
    personality = dimensions.get("personality", {}) or {}
    interests = dimensions.get("interests", {}) or {}
    guidance = profile.get("guidance", {}) or {}
    evolution = profile.get("evolution", {}) or {}

    traits = personality.get("traits", []) or []
    if isinstance(traits, list) and traits and isinstance(traits[0], dict):
        trait_texts = [item.get("trait") or item.get("name") or "" for item in traits]
    else:
        trait_texts = list(traits)

    tags = profile.get("tags", []) or []
    tag_texts = []
    for item in tags:
        if isinstance(item, dict):
            tag_texts.append(item.get("tag") or item.get("name") or "")
        elif isinstance(item, str):
            tag_texts.append(item)

    bundle = {
        "age": profile.get("age") or "",
        "traits": _story_dedup_texts(trait_texts, limit=4, max_len=14),
        "interest_current": _story_dedup_texts(interests.get("current", []), limit=4, max_len=14),
        "interest_emerging": _story_dedup_texts(interests.get("emerging", []), limit=3, max_len=14),
        "strengths": _story_dedup_texts(guidance.get("strengths", []), limit=3, max_len=18),
        "suggestions": _story_dedup_texts(guidance.get("activities", []), limit=3, max_len=18),
        "recent_changes": _story_dedup_texts(evolution.get("recent_changes", []), limit=3, max_len=20),
        "profile_tags": _story_dedup_texts(tag_texts, limit=6, max_len=14),
        "guidance_summary": _story_trim_text(guidance.get("summary") or "", 80),
    }
    return bundle


def _get_story_candidate_dates(client_id: str = None) -> list:
    dates = set()
    index_manager = _get_storybook_index_manager()
    if index_manager is not None:
        try:
            dates.update(index_manager.get_all_dates())
        except Exception as e:
            logger.info(f"[StoryBook] 获取照片日期失败: {e}")

    return sorted(
        [item for item in dates if re.match(r"^\d{4}-\d{2}-\d{2}$", str(item))],
        reverse=False,
    )


def _get_storybook_index_manager():
    data_dir = str(getattr(public_client, "data_dir", "") or "")
    if not data_dir:
        return None

    index_file = Path(data_dir) / "photo_index" / "photo_index.json"
    index_mtime_ns = index_file.stat().st_mtime_ns if index_file.exists() else 0
    cached_data_dir = ALBUM_INDEX_CACHE.get("data_dir")
    cached_mtime_ns = ALBUM_INDEX_CACHE.get("index_mtime_ns", 0)
    cached_manager = ALBUM_INDEX_CACHE.get("index_manager")
    if cached_manager is not None and cached_data_dir == data_dir and cached_mtime_ns == index_mtime_ns:
        return cached_manager

    try:
        from photo_index_manager import PhotoIndexManager

        manager = PhotoIndexManager(Path(data_dir), source_folders=None)
    except Exception as e:
        logger.info(f"[Album] 初始化 PhotoIndexManager 失败: {e}")
        return None

    ALBUM_INDEX_CACHE["data_dir"] = data_dir
    ALBUM_INDEX_CACHE["index_manager"] = manager
    ALBUM_INDEX_CACHE["index_mtime_ns"] = index_mtime_ns
    return manager


def _get_story_collection_cache_key(
    client_id: str = "",
    date_from: str = "",
    date_to: str = "",
    max_episodes: int = 0,
) -> tuple:
    data_dir = Path(str(getattr(public_client, "data_dir", "") or ""))
    index_file = data_dir / "photo_index" / "photo_index.json"
    index_mtime_ns = index_file.stat().st_mtime_ns if index_file.exists() else 0
    return (
        client_id or "",
        date_from or "",
        date_to or "",
        int(max_episodes or 0),
        str(data_dir),
        index_mtime_ns,
    )


def _get_story_collection_cache(cache_key: tuple):
    now_ts = time.time()
    with ALBUM_COLLECTION_CACHE_LOCK:
        cached = ALBUM_COLLECTION_CACHE.get(cache_key)
        if not cached:
            return None
        if now_ts >= cached.get("expires_at", 0):
            ALBUM_COLLECTION_CACHE.pop(cache_key, None)
            return None
        return cached.get("payload")


def _set_story_collection_cache(cache_key: tuple, payload: dict) -> dict:
    with ALBUM_COLLECTION_CACHE_LOCK:
        ALBUM_COLLECTION_CACHE[cache_key] = {
            "expires_at": time.time() + ALBUM_COLLECTION_CACHE_TTL,
            "payload": payload,
        }
    return payload


def _clear_story_collection_cache(client_id: str = "") -> None:
    with ALBUM_COLLECTION_CACHE_LOCK:
        if not client_id:
            ALBUM_COLLECTION_CACHE.clear()
            return
        for key in list(ALBUM_COLLECTION_CACHE.keys()):
            if key and key[0] == client_id:
                ALBUM_COLLECTION_CACHE.pop(key, None)


def _get_story_media_entries_by_dates(index_manager, candidate_dates: list[str]) -> dict[str, list]:
    if index_manager is None or not candidate_dates:
        return {}

    raw_index = getattr(index_manager, "index", None)
    if not isinstance(raw_index, dict):
        return {}

    allowed_dates = set(candidate_dates)
    grouped = defaultdict(list)
    for key in ("photos", "videos"):
        for file_hash, entry in (raw_index.get(key) or {}).items():
            date_str = entry.get("date")
            if date_str not in allowed_dates:
                continue
            path_str = entry.get("path", "")
            if not path_str or not Path(path_str).exists():
                continue
            path_obj = Path(path_str)
            grouped[date_str].append(
                {
                    "hash": file_hash,
                    "filename": entry.get("filename") or path_obj.name,
                    "path": path_str,
                    "url": f"/photo/{quote(entry.get('filename') or path_obj.name)}",
                    "date": date_str,
                    "is_video": entry.get("is_video", False),
                    "size": entry.get("size", 0),
                    "folder": str(path_obj.parent),
                }
            )

    for rows in grouped.values():
        rows.sort(key=lambda item: item.get("filename") or "")
    return grouped


def _album_format_display_date(date_str: str) -> str:
    try:
        dt = datetime.strptime((date_str or "")[:10], "%Y-%m-%d")
        return dt.strftime("%Y.%m.%d")
    except Exception:
        return date_str or ""


@lru_cache(maxsize=2048)
def _read_album_image_meta(path_str: str) -> dict:
    meta = {
        "width": 0,
        "height": 0,
        "aspect_ratio": 1.0,
        "frame_shape": "square",
        "quality_bonus": 0,
    }
    path = Path(path_str or "")
    if not path.exists():
        return meta

    try:
        from PIL import Image
        from PIL import ImageOps

        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            width, height = img.size
    except Exception:
        return meta

    if width <= 0 or height <= 0:
        return meta

    aspect_ratio = round(width / max(height, 1), 3)
    megapixels = (width * height) / 1_000_000
    file_kb = path.stat().st_size / 1024 if path.exists() else 0
    quality_bonus = min(18, round(megapixels * 3))
    if file_kb >= 280:
        quality_bonus += 4
    if 0.82 <= aspect_ratio <= 1.45:
        quality_bonus += 3

    if 0.92 <= aspect_ratio <= 1.08:
        frame_shape = "circle"
    elif aspect_ratio < 0.82:
        frame_shape = "arch"
    elif aspect_ratio > 1.45:
        frame_shape = "ticket"
    else:
        frame_shape = "square"

    return {
        "width": width,
        "height": height,
        "aspect_ratio": aspect_ratio,
        "frame_shape": frame_shape,
        "quality_bonus": quality_bonus,
    }


def _score_album_candidate(record: dict, featured_filename: str, meta: dict) -> tuple[int, list]:
    description = (record.get("description") or "").strip()
    scene = (record.get("scene") or "").strip()
    activity = (record.get("activity") or "").strip()
    has_baby = bool(record.get("has_baby", True))
    filename = Path(record.get("path") or "").name
    is_featured = bool(featured_filename) and filename == Path(featured_filename).name

    score = 42 + int(meta.get("quality_bonus", 0))
    if is_featured:
        score += 32
    if has_baby:
        score += 18
    if scene:
        score += 8
    if activity:
        score += 8
    if description:
        score += min(16, max(6, len(description) // 3))

    reasons = []
    if is_featured:
        reasons.append("当天精选")
    if meta.get("quality_bonus", 0) >= 12:
        reasons.append("画质稳定")
    if scene:
        reasons.append(f"{scene}有记忆点")
    if activity:
        reasons.append(f"{activity}动作清楚")
    if description:
        reasons.append("描述完整")

    return score, reasons[:3]


def _quick_score_album_candidate(record: dict, featured_filename: str) -> int:
    description = (record.get("description") or "").strip()
    scene = (record.get("scene") or "").strip()
    activity = (record.get("activity") or "").strip()
    has_baby = bool(record.get("has_baby", True))
    filename = Path(record.get("path") or "").name
    is_featured = bool(featured_filename) and filename == Path(featured_filename).name

    score = 42
    if is_featured:
        score += 32
    if has_baby:
        score += 18
    if scene:
        score += 8
    if activity:
        score += 8
    if description:
        score += min(16, max(6, len(description) // 3))
    return score


def _build_album_moment_title(record: dict, baby_name: str) -> str:
    scene = (record.get("scene") or "").strip()
    activity = (record.get("activity") or "").strip()
    description = (record.get("description") or "").strip()

    if any(word in activity for word in ["睡", "午睡", "休息", "安睡"]):
        return "把困意和夜灯收进相框"
    if any(word in activity for word in ["玩", "探索", "散步", "跑"]):
        return "把好奇心摆到画面中央"
    if any(word in scene for word in ["公园", "户外", "街道", "广场"]):
        return "风吹过的这一格"
    if any(word in scene for word in ["家里", "客厅", "卧室"]):
        return "在家里发亮的一小段"
    if description:
        return description[:16] + ("…" if len(description) > 16 else "")
    return f"{baby_name}被好好记住的一刻"


def _quality_label_from_score(score: int) -> str:
    if score >= 112:
        return "封面级"
    if score >= 96:
        return "长卷主镜头"
    if score >= 78:
        return "适合入册"
    return "补充页"


def _compress_album_days(day_items: list, max_moments: int) -> list:
    if len(day_items) <= max_moments:
        return list(day_items)

    if max_moments <= 2:
        return [day_items[0], day_items[-1]][:max_moments]

    selected = [day_items[0], day_items[-1]]
    used_dates = set()
    for item in selected:
        if item.get("date"):
            used_dates.add(item["date"])

    middle_items = day_items[1:-1]
    middle_slots = max(max_moments - 2, 0)
    step = len(middle_items) / float(middle_slots) if middle_slots else 0

    for index in range(middle_slots):
        start = int(index * step)
        end = max(start + 1, int((index + 1) * step))
        bucket = middle_items[start:end] or [middle_items[min(start, len(middle_items) - 1)]]
        chosen = max(
            bucket,
            key=lambda item: (
                item.get("score", 0),
                int(bool(item.get("primary", {}).get("is_featured"))),
                item.get("date") or "",
            ),
        )
        if chosen.get("date") in used_dates:
            continue
        used_dates.add(chosen.get("date"))
        selected.append(chosen)

    if len(selected) < max_moments:
        ranked_rest = sorted(
            day_items,
            key=lambda item: (
                -(item.get("score", 0)),
                -int(bool(item.get("primary", {}).get("is_featured"))),
                item.get("date") or "",
            ),
        )
        for item in ranked_rest:
            if item.get("date") in used_dates:
                continue
            used_dates.add(item.get("date"))
            selected.append(item)
            if len(selected) >= max_moments:
                break

    return sorted(selected, key=lambda item: item.get("date") or "")


def _build_album_chapter_title(month_key: str, moment_rows: list) -> str:
    label = month_key.replace("-", "年", 1) + "月"
    scene_counter = Counter()
    activity_counter = Counter()
    for row in moment_rows:
        if row.get("scene"):
            scene_counter[row["scene"]] += 1
        if row.get("activity"):
            activity_counter[row["activity"]] += 1
    top_scene = scene_counter.most_common(1)[0][0] if scene_counter else ""
    top_activity = activity_counter.most_common(1)[0][0] if activity_counter else ""
    if top_scene and top_activity:
        return f"{label} · {top_scene}里的{top_activity}"
    if top_scene:
        return f"{label} · {top_scene}这一章"
    if top_activity:
        return f"{label} · {top_activity}这一章"
    return f"{label} · 被认真存下来的日常"


def get_story_collection_data(
    client_id: str = None,
    date_from: str = "",
    date_to: str = "",
    generate_missing: bool = False,
    max_episodes: int = 0,
) -> dict:
    started_at = time.perf_counter()
    target_client = client_id or getattr(public_client, "client_id", None)
    if not target_client:
        return {"moments": [], "chapters": [], "cover_collage": []}

    cache_key = _get_story_collection_cache_key(
        target_client,
        date_from=date_from,
        date_to=date_to,
        max_episodes=max_episodes,
    )
    cached_collection = _get_story_collection_cache(cache_key)
    if cached_collection is not None:
        logger.info(
            f"[Album] 缓存命中: client={target_client} range={date_from or '*'}~{date_to or '*'} "
            f"moments={len(cached_collection.get('moments') or [])}"
        )
        return cached_collection

    def in_range(date_str: str) -> bool:
        if date_from and date_str < date_from:
            return False
        if date_to and date_str > date_to:
            return False
        return True

    candidate_dates = [item for item in _get_story_candidate_dates(target_client) if in_range(item)]

    index_manager = _get_storybook_index_manager()
    if index_manager is None:
        return _set_story_collection_cache(cache_key, {
            "title": f"{public_client.baby_name}时光相册",
            "subtitle": "先完成照片索引，系统才能自动装订时光相册。",
            "moments": [],
            "chapters": [],
            "cover_collage": [],
            "candidate_count": len(candidate_dates),
            "selected_count": 0,
            "selected_photo_count": 0,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })

    try:
        from database import (
            get_featured_photos_by_dates,
            get_photo_descriptions_by_dates,
        )

        media_entries_by_date = _get_story_media_entries_by_dates(index_manager, candidate_dates)
        description_rows_by_date = get_photo_descriptions_by_dates(target_client, candidate_dates)
        featured_rows_by_date = get_featured_photos_by_dates(target_client, candidate_dates)
    except Exception as e:
        logger.warning(f"[Album] 初始化失败: {e}")
        return _set_story_collection_cache(cache_key, {
            "title": f"{public_client.baby_name}时光相册",
            "subtitle": "相册初始化失败，请检查媒体索引和数据库。",
            "moments": [],
            "chapters": [],
            "cover_collage": [],
            "candidate_count": len(candidate_dates),
            "selected_count": 0,
            "selected_photo_count": 0,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })

    day_items = []
    total_photo_count = 0
    for date_str in candidate_dates:
        media_entries = media_entries_by_date.get(date_str) or []
        image_entries = [
            item
            for item in media_entries
            if not item.get("is_video", False)
        ]
        if not image_entries:
            continue
        total_photo_count += len(image_entries)

        entry_map = {Path(item.get("path", "")).name: item for item in image_entries}
        description_rows = description_rows_by_date.get(date_str) or []
        featured = featured_rows_by_date.get(date_str) or {}

        featured_filename = (featured.get("filename") or "").strip()
        candidates = []
        pre_candidates = []

        for row in description_rows:
            row_path = Path(row.get("path", "")).name
            entry = entry_map.get(row_path)
            if not entry or is_semantic_skip_photo_record(row):
                continue
            entry_name = Path(entry["path"]).name
            pre_candidates.append(
                {
                    "path": str(entry["path"]),
                    "url": f"/photo/{quote(entry_name)}",
                    "thumb_url": f"/photo/thumb/{quote(entry_name)}",
                    "album_url": f"/photo/album/{quote(entry_name)}",
                    "date": date_str,
                    "description": (row.get("description") or "").strip(),
                    "has_baby": bool(row.get("has_baby", True)),
                    "scene": (row.get("scene") or "").strip(),
                    "activity": (row.get("activity") or "").strip(),
                    "is_featured": bool(featured_filename and row_path == Path(featured_filename).name),
                    "quick_score": _quick_score_album_candidate(row, featured_filename),
                }
            )

        if pre_candidates:
            candidates = sorted(
                pre_candidates,
                key=lambda item: (
                    -(item.get("quick_score") or 0),
                    -int(bool(item.get("is_featured"))),
                    item.get("path") or "",
                ),
            )

        if not candidates:
            for entry in image_entries[:6]:
                entry_name = Path(entry.get("path", "")).name
                featured_hit = bool(featured_filename and entry_name == Path(featured_filename).name)
                candidates.append(
                    {
                        "path": str(entry["path"]),
                        "url": f"/photo/{quote(entry_name)}",
                        "thumb_url": f"/photo/thumb/{quote(entry_name)}",
                        "album_url": f"/photo/album/{quote(entry_name)}",
                        "date": date_str,
                        "description": "",
                        "has_baby": True,
                        "scene": "",
                        "activity": "",
                        "is_featured": featured_hit,
                        "quick_score": 58 + (28 if featured_hit else 0),
                    }
                )

        if not candidates:
            continue

        candidates = sorted(
            candidates,
            key=lambda item: (
                -(item.get("quick_score") or 0),
                -int(bool(item.get("is_featured"))),
                item.get("path") or "",
            ),
        )

        primary = candidates[0]
        secondary = []
        for candidate in candidates[1:]:
            if len(secondary) >= 2:
                break
            if (
                candidate.get("scene") != primary.get("scene")
                or candidate.get("activity") != primary.get("activity")
                or candidate.get("quick_score", 0) >= primary.get("quick_score", 0) - 12
            ):
                secondary.append(candidate)

        title = _build_album_moment_title(primary, public_client.baby_name)
        summary = primary.get("description") or "这一天值得被放进时间轴里慢慢回看。"
        day_items.append(
            {
                "date": date_str,
                "month_key": date_str[:7],
                "score": primary.get("quick_score", 0),
                "title": title,
                "summary": summary[:72] + ("…" if len(summary) > 72 else ""),
                "primary": primary,
                "secondary": secondary,
            }
        )

    moment_limit = 24 if len(day_items) <= 30 else 20
    selected_days = _compress_album_days(day_items, moment_limit)

    moments = []
    selected_photo_paths = []
    for index, item in enumerate(selected_days, start=1):
        primary = dict(item.get("primary") or {})
        secondary = [dict(row) for row in (item.get("secondary") or [])[:2]]
        featured_filename = Path(primary.get("path") or "").name if primary.get("is_featured") else ""
        primary_meta = _read_album_image_meta(primary.get("path") or "")
        primary_score, primary_reasons = _score_album_candidate(primary, featured_filename, primary_meta)
        primary_frame_shape = primary_meta.get("frame_shape") or "square"
        primary_quality_label = _quality_label_from_score(primary_score)

        processed_secondary = []
        for row in secondary:
            secondary_meta = _read_album_image_meta(row.get("path") or "")
            row["frame_shape"] = secondary_meta.get("frame_shape") or "square"
            processed_secondary.append(row)

        selected_photo_paths.append(primary.get("path") or "")
        selected_photo_paths.extend([row.get("path") or "" for row in processed_secondary])
        moments.append(
            {
                "index": index,
                "date": item.get("date") or "",
                "date_display": _album_format_display_date(item.get("date") or ""),
                "chapter_key": item.get("month_key") or "",
                "score": primary_score,
                "title": item.get("title") or "",
                "summary": item.get("summary") or "",
                "scene": primary.get("scene") or "",
                "activity": primary.get("activity") or "",
                "quality_label": primary_quality_label,
                "frame_shape": primary_frame_shape,
                "photo": primary.get("url") or "",
                "thumb": primary.get("thumb_url") or primary.get("url") or "",
                "album": primary.get("album_url") or primary.get("url") or "",
                "secondary_photos": [
                    {
                        "photo": row.get("url") or "",
                        "thumb": row.get("thumb_url") or row.get("url") or "",
                        "album": row.get("album_url") or row.get("url") or "",
                        "frame_shape": row.get("frame_shape") or "square",
                    }
                    for row in processed_secondary
                    if row.get("url")
                ],
                "reason_lines": primary_reasons,
                "layout": "cluster" if len(processed_secondary) >= 2 else ("split" if processed_secondary else "feature"),
                "is_featured": bool(primary.get("is_featured")),
            }
        )

    chapters_map = defaultdict(list)
    for moment in moments:
        chapters_map[moment.get("chapter_key") or "未分组"].append(moment)

    chapters = []
    for month_key, rows in sorted(chapters_map.items()):
        chapters.append(
            {
                "key": month_key,
                "title": _build_album_chapter_title(month_key, rows),
                "count": len(rows),
                "date_span": f"{rows[0]['date_display']} - {rows[-1]['date_display']}",
            }
        )

    cover_moment = max(
        moments,
        key=lambda item: (
            item.get("score", 0),
            int(bool(item.get("is_featured"))),
            item.get("date") or "",
        ),
        default={},
    )
    cover_collage = [
        {
            "photo": moment.get("photo") or "",
            "thumb": moment.get("thumb") or moment.get("photo") or "",
        }
        for moment in moments[:3]
        if moment.get("photo")
    ]
    date_span = ""
    if moments:
        date_span = f"{moments[0].get('date')} 至 {moments[-1].get('date')}"
    candidate_date_span = ""
    if candidate_dates:
        candidate_date_span = f"{candidate_dates[0]} 至 {candidate_dates[-1]}"

    payload = {
        "title": f"{public_client.baby_name}时光相册",
        "cover_image": cover_moment.get("photo") or "",
        "cover_thumb": cover_moment.get("thumb") or cover_moment.get("photo") or "",
        "cover_album": cover_moment.get("album") or cover_moment.get("photo") or "",
        "cover_title": "mumu成长日志系统 mumu.ftir.fun",
        "cover_summary": cover_moment.get("summary") or "",
        "cover_collage": cover_collage,
        "date_span": date_span,
        "candidate_date_span": candidate_date_span,
        "date_from": date_from,
        "date_to": date_to,
        "has_date_filter": bool(date_from or date_to),
        "candidate_count": len(candidate_dates),
        "selected_count": len(moments),
        "selected_photo_count": len([p for p in selected_photo_paths if p]),
        "total_photo_count": total_photo_count,
        "moments": moments,
        "chapters": chapters,
        "chapters_count": len(chapters),
        "missing_dates": [],
        "missing_count": 0,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
    logger.info(
        f"[Album] 构建完成: client={target_client} range={date_from or '*'}~{date_to or '*'} "
        f"dates={len(candidate_dates)} total_photos={total_photo_count} "
        f"moments={len(moments)} elapsed_ms={elapsed_ms}"
    )
    return _set_story_collection_cache(cache_key, payload)


def render_storybook_pdf(print_url: str) -> bytes:
    """使用 Playwright 将打印版时光相册页面导出为 PDF。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 1800})
        page.goto(print_url, wait_until="networkidle", timeout=120000)
        pdf_bytes = page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "10mm", "right": "8mm", "bottom": "12mm", "left": "8mm"},
        )
        browser.close()
        return pdf_bytes


def _list_story_media_entries(date: str) -> list:
    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders:
        return []

    try:
        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)
        return pm.get_photos_by_date(date) or []
    except Exception as e:
        logger.info(f"[Story] 获取原始素材失败: {e}")
        return []


def _build_processed_photo_map(processed_photos: list) -> dict:
    processed_map = {}
    for item in processed_photos or []:
        name = Path(item.get("path", "")).name
        if not name:
            continue
        processed_map[name] = dict(item)
    return processed_map


def _order_processed_photo_records(image_entries: list, processed_map: dict) -> list:
    ordered = []
    seen = set()
    for item in image_entries or []:
        name = item.get("filename", "")
        if not name or name not in processed_map or name in seen:
            continue
        seen.add(name)
        ordered.append(processed_map[name])
    for name, item in (processed_map or {}).items():
        if name in seen:
            continue
        seen.add(name)
        ordered.append(item)
    return ordered


def _ensure_photo_descriptions_for_entries(
    date: str,
    client_id: str,
    child_id: str = "",
    processed_photos: list = None,
    media_entries: list = None,
    max_photos: int | None = None,
    log_prefix: str = "[Photo]",
) -> dict:
    media_entries = media_entries if media_entries is not None else _list_story_media_entries(date)
    image_entries = [
        item
        for item in media_entries or []
        if not item.get("is_video", False) and Path(item.get("path", "")).exists()
    ]
    if not image_entries:
        return {
            "photos": processed_photos or [],
            "missing_count": 0,
            "analyzed_count": 0,
            "blurry": [],
            "duplicates": [],
            "no_baby": [],
        }

    processed_map = _build_processed_photo_map(processed_photos)
    entry_by_name = {}
    for item in image_entries:
        filename = item.get("filename", "") or Path(item.get("path", "")).name
        if filename:
            entry_by_name[filename] = item

    missing_entries = [
        item
        for item in image_entries
        if (item.get("filename") or Path(item.get("path", "")).name) not in processed_map
    ]
    target_paths = [
        item.get("path", "") for item in missing_entries if item.get("path")
    ]
    if max_photos is not None and max_photos > 0:
        target_paths = target_paths[:max_photos]

    describe_result = {
        "blurry_filenames": [],
        "duplicate_filenames": [],
        "no_baby_filenames": [],
    }
    if target_paths:
        try:
            from select_best_photo import analyze_all_photos

            logger.info(
                f"{log_prefix} 补齐素材分析: date={date}, to_analyze={len(target_paths)}, existing={len(processed_map)}"
            )
            describe_result, skipped_records = _describe_photos_with_block_fallback(
                target_paths,
                child_id=child_id or client_id,
                client_id=client_id,
                date=date,
                log_prefix=log_prefix,
            )

            for item in skipped_records:
                name = Path(item.get("path", "")).name
                if not name:
                    continue
                processed_map[name] = {
                    "path": item.get("path", ""),
                    "hash": item.get("hash", "") or item.get("file_hash", ""),
                    "description": item.get("description", "") or "",
                    "has_baby": item.get("has_baby", True),
                    "scene": item.get("scene", "") or "",
                    "activity": item.get("activity", "") or "",
                    "processed_status": item.get("processed_status", "") or PHOTO_STATUS_OK,
                    "processed_error_code": item.get("processed_error_code", "") or "",
                    "processed_error_detail": item.get("processed_error_detail", "") or "",
                }

            analysis = {"photos": []}
            if describe_result:
                analysis = analyze_all_photos(
                    target_paths,
                    max_photos=len(target_paths),
                    client_id=client_id,
                    date=date,
                    ai_result=describe_result,
                )

            for item in analysis.get("photos", []) or []:
                name = Path(item.get("path", "")).name
                if not name:
                    continue
                source_entry = entry_by_name.get(name, {})
                processed_map[name] = {
                    "path": item.get("path", ""),
                    "hash": source_entry.get("hash", "") or item.get("hash", ""),
                    "description": item.get("description", "") or "",
                    "has_baby": item.get("has_baby", True),
                    "scene": item.get("scene", "") or "",
                    "activity": item.get("activity", "") or "",
                    "processed_status": item.get("processed_status", "") or PHOTO_STATUS_OK,
                    "processed_error_code": item.get("processed_error_code", "") or "",
                    "processed_error_detail": item.get("processed_error_detail", "") or "",
                }
        except Exception as e:
            logger.warning(f"{log_prefix} 补齐素材分析失败，继续使用现有数据: {e}")

    return {
        "photos": _order_processed_photo_records(image_entries, processed_map),
        "missing_count": len(missing_entries),
        "analyzed_count": len(target_paths),
        "blurry": describe_result.get("blurry_filenames", []) or [],
        "duplicates": describe_result.get("duplicate_filenames", []) or [],
        "no_baby": describe_result.get("no_baby_filenames", []) or [],
    }


def _pick_featured_photo_from_records(photo_records: list, child_id: str = "") -> tuple:
    photo_records = _filter_semantic_skip_photo_records(photo_records)
    if not photo_records:
        return None, "", {}

    from select_best_photo import select_featured_photo_from_descriptions

    selection = select_featured_photo_from_descriptions(
        photo_records,
        select_n=1,
        child_id=child_id or public_client.client_id,
    )
    selected_paths = selection.get("selected", []) or []
    if not selected_paths:
        return None, "", selection

    selected_name = Path(selected_paths[0]).name
    selected_record = None
    for item in photo_records:
        if Path(item.get("path", "")).name == selected_name:
            selected_record = item
            break

    reasons = selection.get("reasons") or []
    description = (
        (selected_record or {}).get("description", "") or (reasons[0] if reasons else "") or "今日照片"
    )
    return selected_record, description, selection


def _build_photo_analysis_from_records(photo_records: list) -> dict:
    photo_records = _filter_semantic_skip_photo_records(photo_records)
    valid_photos = []
    all_scenes = set()
    all_activities = set()
    baby_count = 0
    scenery_count = 0

    for item in photo_records or []:
        photo_info = {
            "path": item.get("path", ""),
            "description": item.get("description", "") or "",
            "has_baby": item.get("has_baby", True),
            "scene": item.get("scene", "") or "",
            "activity": item.get("activity", "") or "",
        }
        valid_photos.append(photo_info)

        if photo_info["has_baby"]:
            baby_count += 1
        else:
            scenery_count += 1
        if photo_info["scene"]:
            all_scenes.add(photo_info["scene"])
        if photo_info["activity"]:
            all_activities.add(photo_info["activity"])

    combined_parts = []
    if baby_count > 0:
        combined_parts.append(f"共{baby_count}张宝宝照片")
    if scenery_count > 0:
        combined_parts.append(f"{scenery_count}张场景照片")

    descriptions = [p["description"] for p in valid_photos if p["description"]]
    if descriptions:
        combined_parts.append("活动：" + "、".join(descriptions[:10]))

    combined_summary = "。".join(combined_parts) if combined_parts else ""
    return {
        "photos": valid_photos,
        "scenes": list(all_scenes),
        "activities": list(all_activities),
        "baby_photos": baby_count,
        "scenery_photos": scenery_count,
        "combined_summary": combined_summary,
    }


def _analyze_story_photos_if_needed(date: str, client_id: str, processed_photos: list) -> list:
    """故事生成前补齐图片分析，确保场景图不会因为未分析而漏掉。"""
    child_id = get_ai_identity_client_id(client_id)
    ensured = _ensure_photo_descriptions_for_entries(
        date,
        client_id,
        child_id=child_id,
        processed_photos=processed_photos,
        log_prefix="[Story]",
    )
    return ensured.get("photos", [])


def get_story_speech_records(date: str, client_id: str) -> list:
    """获取某天视频语音转写，用于故事台词。"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/speech/records",
            params={"client_id": client_id, "date": date},
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return result.get("records", [])
    except Exception as e:
        logger.info(f"[Story] 获取语音记录失败: {e}")
    return []


def get_story_log_bundle(date: str, client_id: str) -> dict:
    """获取某天日志、天气和新闻信息。"""
    try:
        user_city = getattr(public_client, "user_city", None) or "上海"
        log_url = f"{public_client.server_url}/czrz/baby/log?city={user_city}&date={date}"
        if client_id:
            log_url += f"&client_id={client_id}"

        resp = public_client.signed_request("GET", log_url, timeout=30)
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return result
    except Exception as e:
        logger.info(f"[Story] 获取日志素材失败: {e}")
    return {}


def _resolve_story_media_entry(date: str, filename_or_path: str) -> dict:
    media_folders = getattr(public_client, "media_folders", [])
    from photo_manager import PhotoManager

    pm = PhotoManager(media_folders, public_client.data_dir) if media_folders else None

    text = filename_or_path or ""
    candidate_path = Path(text) if text else None
    if candidate_path and candidate_path.exists():
        return {
            "path": str(candidate_path),
            "filename": candidate_path.name,
            "is_video": candidate_path.suffix.lower() in [".mp4", ".mov", ".avi"],
        }

    if pm and text:
        filename = Path(text).name
        entry = pm.get_photo_by_filename(filename)
        if entry and Path(entry.get("path", "")).exists():
            return {
                "path": entry["path"],
                "filename": entry["filename"],
                "is_video": entry.get("is_video", False),
                "hash": entry.get("hash", ""),
            }

    if pm:
        for item in pm.get_photos_by_date(date):
            if item.get("filename") == Path(text).name:
                return {
                    "path": item.get("path", ""),
                    "filename": item.get("filename", ""),
                    "is_video": item.get("is_video", False),
                    "hash": item.get("hash", ""),
                }

    return {
        "path": text,
        "filename": Path(text).name if text else "",
        "is_video": Path(text).suffix.lower() in [".mp4", ".mov", ".avi"],
    }


def _prepare_story_photo_records(
    date: str,
    client_id: str,
    *,
    allow_media_fallback: bool = True,
    diagnostics: dict | None = None,
) -> list:
    media_entries = _list_story_media_entries(date)
    image_entries = [
        item
        for item in media_entries or []
        if not item.get("is_video", False) and Path(item.get("path", "")).exists()
    ]
    child_id = get_ai_identity_client_id(client_id)
    ensured = _ensure_photo_descriptions_for_entries(
        date,
        client_id,
        child_id=child_id,
        processed_photos=get_processed_photos(date, client_id),
        media_entries=media_entries,
        log_prefix="[Story]",
    )
    processed_photos = _filter_semantic_skip_photo_records(ensured.get("photos", []))

    if diagnostics is not None:
        diagnostics.clear()
        diagnostics.update(
            {
                "image_count": len(image_entries),
                "processed_count": len(ensured.get("photos", []) or []),
                "usable_count": len(processed_photos),
                "missing_count": int(ensured.get("missing_count") or 0),
                "analyzed_count": int(ensured.get("analyzed_count") or 0),
                "blurry_count": len(ensured.get("blurry", []) or []),
                "duplicate_count": len(ensured.get("duplicates", []) or []),
                "no_baby_count": len(ensured.get("no_baby", []) or []),
            }
        )

    records = []
    seen = set()

    for item in processed_photos:
        media_entry = _resolve_story_media_entry(date, item.get("path") or "")
        filename = media_entry.get("filename") or Path(item.get("path", "")).name
        if not filename or filename in seen:
            continue
        seen.add(filename)
        records.append(
            {
                "date": date,
                "filename": filename,
                "path": media_entry.get("path", item.get("path", "")),
                "is_video": media_entry.get("is_video", False),
                "hash": media_entry.get("hash", ""),
                "description": item.get("description", "") or "",
                "has_baby": item.get("has_baby", True),
                "scene": item.get("scene", "") or "",
                "activity": item.get("activity", "") or "",
            }
        )

    if records:
        return records

    if not allow_media_fallback:
        return []

    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders:
        return []

    from photo_manager import PhotoManager

    pm = PhotoManager(media_folders, public_client.data_dir)
    for item in pm.get_photos_by_date(date):
        if item.get("filename") in seen:
            continue
        seen.add(item.get("filename"))
        records.append(
            {
                "date": date,
                "filename": item.get("filename", ""),
                "path": item.get("path", ""),
                "is_video": item.get("is_video", False),
                "hash": item.get("hash", ""),
                "description": f"{public_client.baby_name}这一天记录下来的一个片段",
                "has_baby": True,
                "scene": "",
                "activity": "",
            }
        )

    return records


def _build_comic_generation_unavailable_message(
    diagnostics: dict,
    *,
    missing_requested: list | None = None,
) -> str:
    image_count = int(diagnostics.get("image_count") or 0)
    analyzed_count = int(diagnostics.get("analyzed_count") or 0)
    usable_count = int(diagnostics.get("usable_count") or 0)
    blurry_count = int(diagnostics.get("blurry_count") or 0)
    duplicate_count = int(diagnostics.get("duplicate_count") or 0)
    no_baby_count = int(diagnostics.get("no_baby_count") or 0)

    reason_parts = []
    if blurry_count > 0:
        reason_parts.append(f"模糊 {blurry_count} 张")
    if duplicate_count > 0:
        reason_parts.append(f"重复 {duplicate_count} 张")
    if no_baby_count > 0:
        reason_parts.append(f"未识别到宝宝 {no_baby_count} 张")
    reason_text = f"（{'，'.join(reason_parts)}）" if reason_parts else ""

    if image_count <= 0:
        return "当前日期没有可用于生成漫画的照片。"

    if missing_requested:
        names = "、".join(str(name) for name in missing_requested[:3])
        return (
            f"所选照片当前无法生成漫画：{names}。"
            f"这些照片尚未完成图片解读，或已被判定为不适合生成漫画{reason_text}。"
        )

    if usable_count > 0:
        return "当前日期已有可用素材，但当前选择的照片无法用于生成漫画。"

    if analyzed_count > 0 or reason_parts:
        return f"当前日期照片已尝试解读，但没有可用于生成漫画的照片{reason_text}。"

    return "当前日期照片尚未完成图片解读，暂时无法生成漫画。"


def _pick_story_panel_sources(photo_records: list) -> list:
    if not photo_records:
        return []

    unique = []
    seen = set()
    for item in photo_records:
        filename = item.get("filename", "")
        if not filename or filename in seen:
            continue
        seen.add(filename)
        unique.append(item)

    scenery = [p for p in unique if not p.get("has_baby", True) and not p.get("is_video")]
    baby = [p for p in unique if p.get("has_baby", True) and not p.get("is_video")]
    explorer = [
        p
        for p in baby
        if any(word in (p.get("description", "") + p.get("activity", "")) for word in ["看", "摸", "探索", "观察", "研究", "找"])
    ]
    energetic = [
        p
        for p in baby
        if any(word in (p.get("description", "") + p.get("activity", "")) for word in ["玩", "笑", "爬", "走", "跑", "拿"])
    ]
    calm = [
        p
        for p in unique
        if any(word in (p.get("description", "") + p.get("activity", "")) for word in ["睡", "安静", "休息", "看", "发呆", "靠"])
        and not p.get("is_video")
    ]

    selected = []

    def add_candidate(candidate):
        if not candidate:
            return
        filename = candidate.get("filename")
        if not filename or any(item.get("filename") == filename for item in selected):
            return
        selected.append(candidate)

    add_candidate(scenery[0] if scenery else None)
    add_candidate(baby[0] if baby else (unique[0] if unique else None))
    add_candidate(explorer[0] if explorer else (energetic[0] if energetic else (baby[1] if len(baby) > 1 else None)))
    add_candidate(calm[-1] if calm else (energetic[-1] if energetic else (scenery[-1] if scenery else unique[-1])))

    for item in unique:
        if len(selected) >= 4:
            break
        add_candidate(item)

    if selected:
        while len(selected) < 4:
            selected.append(dict(selected[-1]))

    return selected[:4]


def _get_story_recent_episode_memory(client_id: str, before_date: str, limit: int = 3) -> list:
    candidate_dates = [
        item for item in _get_story_candidate_dates(client_id) if item and item < before_date
    ]
    memories = []
    for date_str in sorted(candidate_dates, reverse=True):
        story = load_daily_story(date_str, client_id)
        if not story:
            continue
        memories.append(
            {
                "date": date_str,
                "title": story.get("title") or "",
                "summary": story.get("summary") or "",
                "fantasy_mission": story.get("fantasy_mission") or "",
                "skill_progress": story.get("skill_progress") or "",
                "gear_unlock": story.get("gear_unlock") or "",
                "hook": story.get("hook") or "",
                "hero_archetype": story.get("hero_archetype") or "",
                "signature": story.get("signature") or "",
            }
        )
        if len(memories) >= limit:
            break
    return list(reversed(memories))


def _get_story_prior_episodes(client_id: str, before_date: str, limit: int = 6) -> list:
    candidate_dates = [
        item for item in _get_story_candidate_dates(client_id) if item and item < before_date
    ]
    episodes = []
    for date_str in sorted(candidate_dates, reverse=True):
        story = load_daily_story(date_str, client_id)
        if not story:
            continue
        episodes.append(story)
        if len(episodes) >= limit:
            break
    return list(reversed(episodes))


def _build_story_prompt(
    date: str,
    log_bundle: dict,
    panels: list,
    speech_records: list,
    series_profile: dict,
) -> str:
    log_text = (log_bundle.get("log") or "").strip()
    news_list = log_bundle.get("news") or []
    speech_lines = _story_dedup_texts(
        [(item.get("transcript") or "").strip() for item in speech_records[:4]],
        limit=4,
        max_len=40,
    )
    client_id = public_client.client_id
    panel_filenames = [panel.get("filename", "") for panel in panels if panel.get("filename")]
    tag_lines = get_story_tag_records(client_id, panel_filenames, limit=6)
    feedback_lines = get_story_feedback_records(client_id, limit=4)
    profile_bundle = get_story_profile_bundle(client_id)
    recent_episodes = _get_story_recent_episode_memory(client_id, date, limit=3)

    material_lines = []
    for index, panel in enumerate(panels, start=1):
        material_lines.append(
            f"panel_{index}: 描述={panel.get('description', '')}; 场景={panel.get('scene', '')}; 活动={panel.get('activity', '')}; has_baby={panel.get('has_baby', True)}"
        )

    profile_lines = [
        f"年龄：{profile_bundle.get('age') or '未知'}",
        f"性格关键词：{'、'.join(profile_bundle.get('traits', [])) or '暂无'}",
        f"当前兴趣：{'、'.join(profile_bundle.get('interest_current', [])) or '暂无'}",
        f"新冒头兴趣：{'、'.join(profile_bundle.get('interest_emerging', [])) or '暂无'}",
        f"画像标签：{'、'.join(profile_bundle.get('profile_tags', [])) or '暂无'}",
        f"优势线索：{'、'.join(profile_bundle.get('strengths', [])) or '暂无'}",
        f"近期变化：{'、'.join(profile_bundle.get('recent_changes', [])) or '暂无'}",
        f"家长反馈：{'；'.join(feedback_lines) or '暂无'}",
        f"家长手动标签：{'、'.join(tag_lines) or '暂无'}",
        f"成长建议摘要：{profile_bundle.get('guidance_summary') or '暂无'}",
    ]

    continuity_lines = [
        (
            f"{item.get('date')}｜标题={item.get('title') or '上一集'}；"
            f"主线={item.get('fantasy_mission') or item.get('summary') or '暂无'}；"
            f"能力推进={item.get('skill_progress') or '暂无'}；"
            f"道具线索={item.get('gear_unlock') or '暂无'}；"
            f"摘要={item.get('summary') or item.get('hook') or '暂无'}"
        )
        for item in recent_episodes
    ]
    hero_archetype = (series_profile.get("hero_archetype") or "").strip()
    world_name = (series_profile.get("world_name") or "").strip()
    signature_line = (series_profile.get("signature_line") or "").strip()
    catchphrase = (series_profile.get("catchphrase") or "").strip()
    series_name = (
        series_profile.get("series_name")
        or f"《{public_client.baby_name}的成长连环画》"
    ).strip()
    hero_rule = (
        f"8. `hero_archetype` 字段必须原样沿用：{hero_archetype}"
        if hero_archetype
        else "8. `hero_archetype` 字段请完全根据长期画像、标签和真实素材自行定义，并在本集后续与后续剧集中保持稳定。"
    )
    world_rule = (
        f"9. `world_name` 字段必须原样沿用：{world_name}"
        if world_name
        else "9. `world_name` 字段请由你根据主角画像与长期叙事气质自行命名；不要套用通用世界名。"
    )
    signature_rule = (
        f"10. `signature` 字段必须优先原样沿用这句稳定识别句：{signature_line}"
        if signature_line
        else "10. `signature` 字段请你为这个主角首次写出一句稳定识别句，后续剧集应沿用。"
    )
    output_hero_hint = hero_archetype or "根据画像生成的稳定主角身份"
    output_world_hint = world_name or "根据画像生成的固定世界名"
    output_signature_hint = signature_line or "根据画像生成的稳定识别句"
    series_lines = [
        f"稳定主角名：{public_client.baby_name}",
        f"稳定主角身份：{hero_archetype or '当前还没有历史锁定值，本集请首次定义后保持稳定'}",
        f"固定世界名：{world_name or '当前还没有历史锁定值，本集请首次定义后保持稳定'}",
        f"固定系列名：{series_name}",
        f"稳定识别句：{signature_line or '当前还没有历史锁定值，本集请首次定义后保持稳定'}",
        f"旁白口吻：{catchphrase or '当前还没有历史锁定值，可由你根据主角气质首次定义'}",
    ]

    return f"""你是一位给低幼儿童写日更连环画的漫画作者，不是做词语替换的机器。请把下面真实素材写成同一主角、同一世界观下的一集四格漫画脚本。

核心要求：
1. 主角永远只有宝宝一个人，人设必须稳定；主角气质、说话口吻、做事方式要连续。
2. 如果最近几集信息存在，你要优先承接同一条主线；如果当天素材不适合强承接，也可以写成独立单集，但主角身份和叙事气质不能漂移。
3. 这是基于真实日常长出来的儿童漫画连载。你可以决定它更偏轻幻想、现实冒险、生活喜剧、童话比喻或其他合适表达，但必须由真实素材支撑；不要机械改词，也不要脱离素材乱编。
4. 你要自己根据当天真实素材，决定哪些动作、观察、情绪、互动值得被写成剧情推进；不要脱离照片、日志、语音与画像线索乱编。
5. 这集必须是一个完整单集漫画，固定 4 格，按给定 panel_1 ~ panel_4 顺序写，并形成清晰结构：
   - 第1格：开场和今日目标
   - 第2格：遇到小挑战或新线索
   - 第3格：宝宝主动行动，最好体现一种能力或装备
   - 第4格：任务完成，落到温柔、有结果的收尾
6. 无人像场景照片也很重要，它们可以作为开场、转场、氛围镜头或任务线索镜头。
7. `role_name` 字段必须原样输出为 {public_client.baby_name}
{hero_rule}
{world_rule}
{signature_rule}
11. `skill_progress` 必须体现本集能力成长，优先沿用既有能力线，写成“今天又推进了一格”的具体结果。
12. `gear_unlock` 不是玄幻装备名，而是本集真正用上的真实道具、熟悉物件或稳定辅助物。
13. 你要同时为每一格生成 `image_prompt`，让画图模型直接按该格剧本作画。
14. `image_prompt` 必须明确镜头、动作、情绪、剧情补画元素和环境细节，重点是“根据剧本重组画面”，不是“把照片直接卡通化”。
15. 输出严格为 JSON，不要 markdown，不要解释。

输出格式：
{{
  "title": "故事标题",
  "summary": "80字内故事摘要",
  "hook": "一句吸引人的追更文案",
  "role_name": "{public_client.baby_name}",
  "hero_archetype": "{output_hero_hint}",
  "signature": "{output_signature_hint}",
  "world_name": "{output_world_hint}",
  "fantasy_mission": "本集在故事世界里的主线任务，18字内",
  "real_world_thread": "这集对应现实里的主线，18字内",
  "skill_progress": "本集能力成长，18字内",
  "gear_unlock": "本集真实道具或辅助物线索，18字内",
  "panels": [
    {{"panel": 1, "title": "小标题", "caption": "这一格的旁白，30字内", "image_prompt": "给画图模型的详细中文提示词，80-140字"}},
    {{"panel": 2, "title": "小标题", "caption": "这一格的旁白，30字内", "image_prompt": "给画图模型的详细中文提示词，80-140字"}},
    {{"panel": 3, "title": "小标题", "caption": "这一格的旁白，30字内", "image_prompt": "给画图模型的详细中文提示词，80-140字"}},
    {{"panel": 4, "title": "小标题", "caption": "这一格的旁白，30字内", "image_prompt": "给画图模型的详细中文提示词，80-140字"}}
  ]
}}

日期：{date}
宝宝名字：{public_client.baby_name}
已有日志：{log_text or "暂无正式日志"}
新闻参考：{"；".join(news_list[:2]) if news_list else "无"}
语音线索：{"；".join(speech_lines) if speech_lines else "无"}
固定连载设定：
{chr(10).join(series_lines)}
最近几集延续线索：
{chr(10).join(continuity_lines) if continuity_lines else "暂无历史剧集，可先写成稳定人设下的独立单集"}
长期画像与个性线索：
{chr(10).join(profile_lines)}
面板素材：
{chr(10).join(material_lines)}
"""


def _generate_story_script(
    date: str,
    log_bundle: dict,
    panel_sources: list,
    speech_records: list,
    series_profile: dict,
) -> dict:
    prompt = _build_story_prompt(
        date, log_bundle, panel_sources, speech_records, series_profile
    )
    response = public_client.signed_request(
        "POST",
        f"{public_client.server_url}/czrz/ai/proxy/text",
        json={
            "client_id": public_client.client_id,
            "prompt": prompt,
            "max_tokens": 1400,
            "operation": "daily_story_script",
        },
        timeout=1200,
    )
    if response.status_code != 200:
        raise Exception(f"故事脚本生成失败: {response.status_code}")

    result = response.json()
    if not result.get("success"):
        raise Exception(result.get("message") or result.get("error") or "故事脚本生成失败")

    payload = _extract_json_payload(result.get("result", {}).get("content", ""))
    if not payload:
        raise Exception("故事脚本解析失败")
    return payload


def _image_to_data_url(image_path: str) -> str:
    from PIL import Image
    import io
    import base64

    img = Image.open(image_path).convert("RGB")
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def _generate_image_with_reference(
    prompt: str,
    source_path: str,
    *,
    operation: str = "daily_story_comic",
    model_name: str = DEFAULT_STORY_IMAGE_MODEL,
    size: str = DEFAULT_STORY_IMAGE_SIZE,
) -> str:
    image_url, _error = _generate_image_with_reference_result(
        prompt,
        source_path,
        operation=operation,
        model_name=model_name,
        size=size,
    )
    return image_url


def _generate_image_with_reference_result(
    prompt: str,
    source_path: str,
    *,
    operation: str = "daily_story_comic",
    model_name: str = DEFAULT_STORY_IMAGE_MODEL,
    size: str = DEFAULT_STORY_IMAGE_SIZE,
) -> tuple[str, str]:
    if not source_path or not Path(source_path).exists():
        return "", "参考照片不存在或文件已被移动。"

    try:
        response = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/ai/proxy/image",
            json={
                "client_id": public_client.client_id,
                "prompt": prompt,
                "images": [_image_to_data_url(source_path)],
                "model_name": model_name,
                "size": size,
                "operation": operation,
            },
            timeout=1200,
        )
    except Exception as e:
        logger.warning(f"[Image] 参考图生成请求失败: {e}")
        return "", f"图片模型请求失败：{e}"

    if response.status_code != 200:
        return "", f"图片模型接口返回异常状态：HTTP {response.status_code}"

    try:
        result = response.json()
    except Exception:
        return "", "图片模型返回内容无法解析。"

    if not result.get("success"):
        error_text = result.get("message") or result.get("error") or "未返回具体原因"
        logger.warning(f"[Image] 参考图生成失败: {error_text}")
        return "", f"图片模型生成失败：{error_text}"

    image_url = (result.get("result") or {}).get("image_url", "")
    if not image_url:
        return "", "图片模型未返回图片结果。"
    return image_url, ""


def _generate_story_panel_image(panel_prompt: str, source_path: str) -> str:
    return _generate_image_with_reference(
        panel_prompt,
        source_path,
        operation="daily_story_comic",
        model_name=DEFAULT_STORY_IMAGE_MODEL,
        size=DEFAULT_STORY_IMAGE_SIZE,
    )


def _download_story_image(image_url: str, target_path: Path) -> bool:
    if not image_url:
        return False
    try:
        if image_url.startswith("data:image/"):
            import base64

            header, encoded = image_url.split(",", 1)
            image_bytes = base64.b64decode(encoded)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "wb") as f:
                f.write(image_bytes)
            return True
        resp = requests.get(image_url, timeout=120, verify=False)
        if resp.status_code != 200:
            return False
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "wb") as f:
            f.write(resp.content)
        return True
    except Exception as e:
        logger.warning(f"[Story] 下载故事图片失败: {e}")
        return False


def _save_generated_asset_image(
    image_url: str,
    target_path: Path,
    *,
    max_side: int = 1280,
    quality: int = 84,
) -> bool:
    if not image_url:
        return False
    try:
        if image_url.startswith("data:image/"):
            import base64

            _header, encoded = image_url.split(",", 1)
            image_bytes = base64.b64decode(encoded)
        else:
            resp = requests.get(image_url, timeout=120, verify=False)
            if resp.status_code != 200:
                return False
            image_bytes = resp.content

        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(
                target_path,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
        return True
    except Exception as e:
        logger.warning(f"[Comic] 保存生成图失败: {e}")
        return False


def build_daily_story(date: str, regenerate: bool = False, progress_callback=None) -> dict:
    client_id = public_client.client_id
    logger.info(f"[Story] 开始生成: date={date}, regenerate={regenerate}")
    if callable(progress_callback):
        progress_callback("preparing", "正在准备故事素材...", 5)
    if not regenerate:
        cached_story = load_daily_story(date, client_id)
        if cached_story:
            logger.info(f"[Story] 命中缓存: date={date}")
            if callable(progress_callback):
                progress_callback("done", "故事已存在，直接返回缓存", 100)
            return cached_story

    panel_sources = _pick_story_panel_sources(_prepare_story_photo_records(date, client_id))
    if not panel_sources:
        raise Exception("这一天还没有可用素材，先上传或分析照片后再试")
    if callable(progress_callback):
        progress_callback("materials", "素材已准备，正在整理日志和语音...", 18)

    log_bundle = get_story_log_bundle(date, client_id)
    speech_records = get_story_speech_records(date, client_id)
    series_profile = get_story_series_profile(
        client_id,
        panel_sources=panel_sources,
        episodes=_get_story_prior_episodes(client_id, date, limit=6),
    )

    try:
        if callable(progress_callback):
            progress_callback("script", "正在编写今日故事脚本...", 32)
        script = _generate_story_script(
            date, log_bundle, panel_sources, speech_records, series_profile
        )
    except Exception as e:
        logger.warning(f"[Story] LLM故事脚本生成失败: {e}")
        raise Exception(f"LLM故事脚本生成失败，请检查模型或提示词: {e}")
    if callable(progress_callback):
        progress_callback("script_done", "故事脚本完成，开始绘制漫画分镜...", 46)

    story_dir = _story_dir(date, client_id)
    panels = []
    story_panels = script.get("panels") or []
    role_name = public_client.baby_name
    hero_archetype = script.get("hero_archetype") or series_profile.get("hero_archetype") or ""
    signature = script.get("signature") or series_profile.get("signature_line") or ""
    world_name = script.get("world_name") or series_profile.get("world_name") or ""
    ability_tracks = list(series_profile.get("ability_tracks") or [])
    gear_tracks = list(series_profile.get("gear_tracks") or [])
    story_engine = series_profile.get("story_engine") or ""
    story_motifs = list(series_profile.get("story_motifs") or [])
    top_growth_clues = list(series_profile.get("top_growth_clues") or [])
    skill_progress = script.get("skill_progress") or ""
    gear_unlock = script.get("gear_unlock") or ""
    fantasy_mission = script.get("fantasy_mission") or ""
    real_world_thread = script.get("real_world_thread") or ""
    story_profile_payload = dict(series_profile or {})
    story_profile_payload.update(
        {
            "role_name": role_name,
            "hero_archetype": hero_archetype,
            "world_name": world_name,
            "series_name": story_profile_payload.get("series_name")
            or f"《{public_client.baby_name}的成长连环画》",
            "catchphrase": story_profile_payload.get("catchphrase") or "",
            "signature_line": signature,
            "signature_traits": story_profile_payload.get("signature_traits") or [],
            "world_rules": story_profile_payload.get("world_rules") or [],
            "story_engine": story_engine,
            "story_motifs": story_motifs,
            "top_growth_clues": top_growth_clues,
            "ability_tracks": ability_tracks,
            "gear_tracks": gear_tracks,
        }
    )
    for index, source in enumerate(panel_sources[:4], start=1):
        if callable(progress_callback):
            progress_callback(
                f"panel_{index}",
                f"正在生成第 {index} 格漫画...",
                min(92, 46 + index * 12),
            )
        script_panel = story_panels[index - 1] if index - 1 < len(story_panels) else {}
        panel_title = script_panel.get("title") or f"第{index}格"
        panel_caption = script_panel.get("caption") or source.get("description") or ""
        panel_visual_prompt = (script_panel.get("image_prompt") or "").strip()
        source_filename = source.get("filename", "")
        scene_name = source.get("scene") or story_profile_payload.get("top_scene") or ""
        trait_anchor = "；".join((story_profile_payload.get("signature_traits") or [])[:2])
        prompt = (
            f"请基于这张真实照片和既定分镜剧本，绘制一格温暖、自然、细腻的无字儿童漫画分镜。"
            f"不要把原图直接卡通化，也不要只做描边上色；你需要根据剧情重新组织镜头、动作和环境细节，让它成为真正的漫画画面。"
            f"主角固定为{role_name}，必须保留宝宝本人五官特征、发型、体态、年龄感、衣服主色、真实场景关键元素和光线氛围，不要换脸，不要换人，不要添加陌生主角。"
            f"整体是柔和手绘漫画/儿童绘本风，属于同一部持续连载《{story_profile_payload.get('series_name') or f'《{public_client.baby_name}的成长连环画》'}》。"
        )
        if signature:
            prompt += f" 这个主角的长期识别句是：{signature}。"
        if hero_archetype:
            prompt += f" 主角气质是：{hero_archetype}。"
        if trait_anchor:
            prompt += f" 角色识别点：{trait_anchor}。"
        if fantasy_mission:
            prompt += f" 这一集的主线任务是：{fantasy_mission}。"
        if real_world_thread:
            prompt += f" 现实里的主线是：{real_world_thread}。"
        if scene_name:
            prompt += f" 当前真实场景是：{scene_name}。"
        if panel_title:
            prompt += f" 这一格的画面主题是“{panel_title}”。"
        if panel_caption:
            prompt += f" 这一格需要表现的动作和情绪是：{panel_caption}。"
        if panel_visual_prompt:
            prompt += f" 这格的剧本分镜提示词是：{panel_visual_prompt}。"
        if not source.get("has_baby", True):
            prompt += " 这是场景建立镜头，请保留地点与氛围感，让它像真正的开场或转场分镜。"
        else:
            prompt += " 画面只聚焦宝宝一个明确角色；请按剧本补出必要的动作延伸、环境线索和情节细节，但不要让其他人变成独立人物。"
        prompt += (
            " 只输出画面，不要在图中出现任何中文、英文、数字、拟声字、对白气泡、旁白框、字幕条、标题条、路牌、标牌、logo、水印。"
            " 不要把标题或旁白直接写在图片上，所有文字都留给页面排版。"
        )

        image_url = ""
        image_file = ""
        if not source.get("is_video") and source.get("path"):
            remote_url = _generate_story_panel_image(prompt, source["path"])
            if remote_url:
                image_file = f"panel_{index}.png"
                saved = _download_story_image(remote_url, story_dir / image_file)
                if saved:
                    image_url = _story_asset_url(date, image_file)
                else:
                    image_file = ""

        panel_data = {
            "index": index,
            "title": panel_title,
            "caption": panel_caption,
            "image_prompt": panel_visual_prompt,
            "source_filename": source_filename,
            "source_description": source.get("description", ""),
            "has_baby": source.get("has_baby", True),
            "is_video": source.get("is_video", False),
            "image_file": image_file,
            "image_url": image_url or _story_panel_image_url(date, {"source_filename": source_filename, "is_video": source.get("is_video", False)}),
        }
        panels.append(panel_data)
        logger.info(
            f"[Story] 面板完成: date={date}, panel={index}, "
            f"source={source_filename or 'unknown'}, image_file={image_file or 'fallback'}"
        )
        if callable(progress_callback):
            progress_callback(
                f"panel_{index}_done",
                f"第 {index} 格已完成",
                min(95, 50 + index * 11),
            )

    story_data = {
        "date": date,
        "title": script.get("title") or f"{public_client.baby_name}的今日故事",
        "summary": script.get("summary") or "把这一天的片段整理成了一个小连载。",
        "hook": script.get("hook") or "点开看看今天这一集。",
        "role_name": role_name,
        "hero_archetype": hero_archetype,
        "series_name": story_profile_payload.get("series_name")
        or f"《{public_client.baby_name}的成长连环画》",
        "world_name": world_name,
        "signature": signature,
        "catchphrase": story_profile_payload.get("catchphrase") or "",
        "fantasy_mission": fantasy_mission,
        "real_world_thread": real_world_thread,
        "skill_progress": skill_progress,
        "gear_unlock": gear_unlock,
        "story_engine": story_profile_payload.get("story_engine") or "",
        "story_motifs": story_profile_payload.get("story_motifs") or [],
        "top_growth_clues": story_profile_payload.get("top_growth_clues") or [],
        "signature_traits": story_profile_payload.get("signature_traits") or [],
        "world_rules": story_profile_payload.get("world_rules") or [],
        "ability_tracks": ability_tracks,
        "gear_tracks": gear_tracks,
        "series_profile": story_profile_payload,
        "style": "comic_4panel",
        "source_mode": "ai_comic" if any(p.get("image_file") for p in panels) else "photo_fallback",
        "panels": panels,
        "log_excerpt": (log_bundle.get("log") or "")[:240],
    }
    story_data = _hydrate_story_series_fields(
        date, story_data, client_id, series_profile=story_profile_payload
    )

    saved_story = save_daily_story(date, story_data, client_id)
    logger.info(
        f"[Story] 生成完成: date={date}, source_mode={saved_story.get('source_mode')}, "
        f"panels={len(saved_story.get('panels') or [])}"
    )
    if callable(progress_callback):
        progress_callback("done", "故事生成完成", 100)
    return saved_story


def get_processed_videos(date: str, client_id: str) -> list:
    """获取某天已处理的视频列表（从服务端）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/video/processed",
            params={"client_id": client_id, "date": date},
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return _backfill_missing_video_hashes(
                    date, client_id, result.get("videos", [])
                )
    except Exception as e:
        logger.info(f"[AI回顾] 获取已处理视频失败: {e}")
    return []


def save_featured_photo_server(
    date: str,
    client_id: str,
    photo_path: str,
    ai_description: str,
    file_hash: str = "",
):
    """保存精选照片到服务端（使用文件哈希）"""
    try:
        from photo_manager import PhotoManager
        from pathlib import Path as PathLib

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            logger.info("[AI回顾] 未配置媒体文件夹")
            return False

        pm = PhotoManager(media_folders, public_client.data_dir)
        filename = PathLib(photo_path).name

        entry = pm.get_photo_by_filename(filename)
        if not entry:
            logger.info(f"[AI回顾] 未找到照片索引: {filename}")
            return False

        if not file_hash:
            file_hash = entry.get("hash")
        if not file_hash:
            logger.info(f"[AI回顾] 照片缺少哈希: {filename}")
            return False

        resp = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/photo/featured/update",
            json={
                "client_id": client_id,
                "date": date,
                "file_hash": file_hash,
                "filename": filename,
                "ai_description": ai_description,
                "selected_by": "auto",
            },
            timeout=30,
        )
        if resp.status_code == 200:
            logger.info(f"[AI回顾] 精选照片已保存到服务端: {filename}")
            return True
        logger.info(
            f"[AI回顾] 保存精选照片到服务端失败: status={resp.status_code}"
        )
        return False
    except Exception as e:
        logger.info(f"[AI回顾] 保存精选照片到服务端失败: {e}")
        return False


def _check_token_quota() -> tuple:
    """检查 token 配额，返回 (can_use, used, limit)"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/client/token-check",
            params={"client_id": public_client.client_id},
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("can_use", True), data.get("used", 0), data.get("limit", 0)
    except Exception as e:
        print(f"[Token] 配额检查失败: {e}")
    return True, 0, 0


def _report_token_usage(usage: dict, operation: str = "ai_call", context: dict = None):
    """上报 token 使用到服务端"""
    try:
        resp = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/client/token-record",
            json={
                "client_id": public_client.client_id,
                "usage": usage,
                "operation": operation,
                "context": context or {},
            },
            timeout=30,
        )
        print(
            f"[Token] 上报 {operation}: {usage.get('total_tokens', 0)} tokens, 状态: {resp.status_code}"
        )
    except Exception as e:
        print(f"[Token] 上报失败: {e}")


def get_today_news():
    """从服务端获取今日新闻（内部调用）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/today-news",
            timeout=30,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("news"):
                return [n.get("title", "") for n in data["news"][:3]]
    except Exception as e:
        print(f"[新闻] 获取今日新闻失败: {e}")
    return []


def generate_ai_log(
    date: str,
    photo_description: str,
    client_id: str,
    log_style: str = "简练",
    custom_style: str = "",
    include_news: bool = True,
) -> str:
    """生成AI日志 - 通过服务端代理

    Args:
        date: 日期
        photo_description: 照片描述
        client_id: 客户端ID
        log_style: 日志风格
        custom_style: 自定义风格
        include_news: 是否包含今日新闻（默认True，用于AI重新认识）
    """
    weather = get_weather_for_date(date)
    news_list = get_today_news() if include_news else []

    style_instructions = {
        "简练": "用温馨平淡、像真人随手记下生活的口吻来写，克制自然，不堆砌辞藻，少用感叹词和语气词，120字左右",
        "诗意": "用文艺清新的语言，可以加入诗意的比喻，150字左右",
        "东北腔": "用东北话的口吻写，幽默风趣，可以说'这孩子咋这么招人稀罕'、'杠杠的'等，100字左右",
        "详细": "详细记录当天的活动细节，时间、地点、做了什么，200字左右",
        "童趣": "用轻松可爱的语气，像给宝宝讲故事，150字左右",
        "深情": "用温暖感人的语言，表达父母对宝宝的爱，150字左右",
        "IT男风格": "用程序员风格写，可以用代码注释、技术术语，如'// 今日活动：户外探索 v1.0'，100字左右",
    }

    if log_style == "自定义" and custom_style:
        style_instruction = custom_style
    else:
        style_instruction = style_instructions.get(
            log_style, style_instructions["简练"]
        )

    news_section = ""
    if news_list:
        news_section = f"\n今日新闻：\n{chr(10).join(f'- {n}' for n in news_list)}\n"

    prompt = f"""请根据以下信息为宝宝写一条成长日志。

日期：{date}
天气：{weather or "未知"}
{news_section}
照片内容：
{photo_description or "无照片"}

风格要求：{style_instruction}

注意事项：
1. 语言要自然、温和、平实，像父母随手写下的真实日记
2. 内容要基于照片描述，不要虚构
3. 如果照片中有风景、活动场景但看不到宝宝，说明是活动场景记录，也要记录
4. 少用或不用“呀、呢、啦、哦、真是、太...”这类语气词和感叹词，不要写得像低龄化模板作文
5. 不要堆砌形容词，不要刻意煽情，不要写成鸡汤或营销文案
6. 多写具体动作、场景和细节，让内容像真人回看当天生活的短记录
{"7. 如果有今日新闻，可以根据新闻内容与照片描述的关联性，自然地融入日志中。例如：宝宝爬得很快，恰好今天是马拉松比赛，可以联想宝宝以后会不会也喜欢跑步；宝宝在看球赛视频，恰好今天有足球比赛新闻，可以联系起来。不要生硬地附加在末尾，而是找到合适的切入点自然融入。如果新闻与照片内容没有关联，则不必强行加入。" if news_list else ""}

请直接输出日志内容，不要其他说明："""

    try:
        logger.info(f"[AI回顾] 通过服务端代理生成日志: {date}")
        resp = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/ai/proxy/text",
            json={
                "client_id": public_client.client_id,
                "prompt": prompt,
                "max_tokens": 500,
                "operation": "generate_log",
            },
            timeout=300,
        )

        logger.info(f"[AI回顾] API响应状态: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                log_content = data.get("result", {}).get("content", "")
                print(
                    f"[AI回顾] 生成日志成功，长度: {len(log_content) if log_content else 0}"
                )
                return log_content if log_content else None
            else:
                logger.info(f"[AI回顾] 代理错误: {data.get('error')}")
        else:
            logger.info(f"[AI回顾] 服务端错误: {resp.status_code}")
    except Exception as e:
        logger.info(f"[AI回顾] 生成日志失败: {e}")

    return None


def save_log_server(
    date: str, client_id: str, log_content: str, photo_description: str
):
    """保存日志到服务端数据库"""
    try:
        resp = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/log/save",
            json={
                "client_id": client_id,
                "date": date,
                "content": log_content,
                "source_kind": "auto",
                "allow_overwrite_manual": False,
                "metadata": {
                    "photo_description": photo_description,
                    "generated_by": "ai_auto_review",
                },
            },
            timeout=30,
        )
        result = resp.json() if resp is not None else {}
        if result.get("success"):
            if result.get("skipped"):
                logger.info(f"[AI回顾] {date} 已有手动日志，跳过自动覆盖")
            else:
                logger.info(f"[AI回顾] 日志已保存到服务端: {date}")
            return result
        logger.info(f"[AI回顾] 保存日志到服务端失败: {result}")
        return {
            "success": False,
            "message": result.get("message", "服务端保存失败"),
        }
    except Exception as e:
        logger.info(f"[AI回顾] 保存日志到服务端失败: {e}")
        return {"success": False, "message": str(e)}


def get_weather_for_date(date: str) -> str:
    """获取指定日期的天气（从服务端日志获取）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/baby/log",
            params={"client_id": public_client.client_id, "date": date},
            timeout=30,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success") and result.get("weather"):
                weather = result["weather"]
                if isinstance(weather, dict):
                    return weather.get("condition") or weather.get("city")
                return str(weather)
    except Exception as e:
        logger.info(f"[天气] 获取日志天气失败: {e}")
    return None


@app.route("/api/ai/auto-review/status/<task_id>")
@require_local_password_or_signature
def get_ai_review_status(task_id):
    """获取AI智能回顾任务状态"""
    if task_id in AI_REVIEW_TASKS:
        return jsonify(
            {"success": True, "status": AI_REVIEW_TASKS[task_id], "task_id": task_id}
        )
    else:
        return jsonify({"success": False, "message": "任务不存在"})


# ==================== 压缩服务 API ====================


def _get_media_folder_shrinker_manager():
    from media_folder_shrinker import init_media_folder_shrinker

    media_folders = [
        Path(folder)
        for folder in (getattr(public_client, "media_folders", []) or [])
        if Path(folder).exists()
    ]
    return init_media_folder_shrinker(public_client.data_dir, media_folders)


def _decorate_blurry_scan_result(result):
    result = dict(result or {})
    items = []
    for item in result.get("items", []) or []:
        row = dict(item)
        row["thumb_url"] = (
            "/api/media-quality/file-preview?thumb=1&path=" + quote(row["path"], safe="")
        )
        row["image_url"] = (
            "/api/media-quality/file-preview?path=" + quote(row["path"], safe="")
        )
        items.append(row)
    result["items"] = items
    return result


def _build_media_relative_path(file_path: Path) -> str:
    for folder in (getattr(public_client, "media_folders", []) or []):
        try:
            return str(Path(file_path).relative_to(Path(folder)))
        except Exception:
            continue
    return Path(file_path).name


def _decorate_duplicate_groups(result):
    result = dict(result or {})
    groups = []
    for group in result.get("groups", []) or []:
        row = dict(group)
        keep = dict(row.get("keep") or {})
        if keep.get("path"):
            keep["preview_url"] = (
                "/api/media-quality/file-preview?thumb=1&path="
                + quote(keep.get("path", ""), safe="")
            )
            keep["full_url"] = (
                "/api/media-quality/file-preview?path="
                + quote(keep.get("path", ""), safe="")
            )
        row["keep"] = keep

        duplicates = []
        for item in row.get("duplicates", []) or []:
            dup = dict(item)
            if dup.get("path"):
                dup["preview_url"] = (
                    "/api/media-quality/file-preview?thumb=1&path="
                    + quote(dup.get("path", ""), safe="")
                )
                dup["full_url"] = (
                    "/api/media-quality/file-preview?path="
                    + quote(dup.get("path", ""), safe="")
                )
            duplicates.append(dup)
        row["duplicates"] = duplicates
        groups.append(row)
    result["groups"] = groups
    return result


def _decorate_ai_deep_cleanup_result(result):
    result = dict(result or {})
    blurry_items = []
    for item in result.get("blurry_items", []) or []:
        row = dict(item)
        row["thumb_url"] = (
            "/api/media-quality/file-preview?thumb=1&path=" + quote(row["path"], safe="")
        )
        row["image_url"] = (
            "/api/media-quality/file-preview?path=" + quote(row["path"], safe="")
        )
        blurry_items.append(row)
    result["blurry_items"] = blurry_items
    result["duplicate_groups"] = _decorate_duplicate_groups(
        {"groups": result.get("duplicate_groups", []) or []}
    ).get("groups", [])
    return result


def _remove_media_paths_from_photo_index(raw_paths):
    removed_from_index = []
    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders or not raw_paths:
        return removed_from_index

    from photo_manager import PhotoManager

    pm = PhotoManager(media_folders, public_client.data_dir)
    for deleted_path in raw_paths:
        try:
            if pm.remove_photo_by_path(deleted_path):
                removed_from_index.append(deleted_path)
        except Exception:
            continue
    return removed_from_index


AI_DEEP_CLEANUP_LOCK = threading.Lock()
AI_DEEP_CLEANUP_THREAD = None
AI_DEEP_CLEANUP_STATUS = {
    "running": False,
    "task_id": None,
    "phase": "idle",
    "current": 0,
    "total": 0,
    "percent": 0,
    "current_date": None,
    "current_file": None,
    "message": "",
    "started_at": None,
    "finished_at": None,
    "error": None,
    "estimate": None,
    "result": None,
    "resource_hint": "AI 深度去重会按日期分批调用商业视觉模型，并在每批后释放内存。",
}


def _new_ai_deep_cleanup_status():
    return {
        "running": False,
        "task_id": None,
        "phase": "idle",
        "current": 0,
        "total": 0,
        "percent": 0,
        "current_date": None,
        "current_file": None,
        "message": "",
        "started_at": None,
        "finished_at": None,
        "error": None,
        "estimate": None,
        "result": None,
        "resource_hint": "AI 深度去重会按日期分批调用商业视觉模型，并在每批后释放内存。",
    }


def _collect_ai_deep_cleanup_inventory(sample_limit: int = 80):
    from photo_manager import PhotoManager

    media_folders = getattr(public_client, "media_folders", []) or []
    if not media_folders:
        return {"date_groups": [], "image_count": 0, "total_bytes": 0, "sample_paths": []}

    pm = PhotoManager(media_folders, public_client.data_dir)
    date_groups = []
    image_count = 0
    total_bytes = 0
    sample_paths = []
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".heic", ".heif"}

    for date in sorted(pm.get_all_dates() or []):
        entries = pm.get_photos_by_date(date) or []
        paths = []
        for item in entries:
            if item.get("is_video"):
                continue
            path = Path(item.get("path", ""))
            if not path.exists() or path.suffix.lower() not in image_exts:
                continue
            paths.append(str(path))
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
            if len(sample_paths) < sample_limit:
                sample_paths.append(str(path))
        if paths:
            image_count += len(paths)
            date_groups.append({"date": date, "paths": paths})

    return {
        "date_groups": date_groups,
        "image_count": image_count,
        "total_bytes": total_bytes,
        "sample_paths": sample_paths,
    }


def _estimate_ai_deep_cleanup_payload():
    inventory = _collect_ai_deep_cleanup_inventory()
    total_images = int(inventory.get("image_count") or 0)
    total_bytes = int(inventory.get("total_bytes") or 0)
    date_groups = inventory.get("date_groups") or []

    return {
        "total_images": total_images,
        "date_count": len(date_groups),
        "total_bytes": total_bytes,
        "vision_model_hint": "会调用商业图像识别模型逐批分析照片内容、模糊情况和相似重复。",
    }


def _build_ai_deep_blurry_item(path: str, date: str, photo_info: dict):
    source_info = dict(photo_info or {})
    return {
        "path": path,
        "filename": Path(path).name,
        "relative_path": _build_media_relative_path(Path(path)),
        "date": date,
        "size_bytes": Path(path).stat().st_size if Path(path).exists() else 0,
        "file_type": "image",
        "reason": source_info.get("description", "") or "AI 判定为模糊照片",
        "scene": source_info.get("scene", "") or "",
        "activity": source_info.get("activity", "") or "",
    }


def _build_ai_deep_duplicate_group(paths: list, date: str, photos_dict: dict):
    entries = []
    for raw_path in paths or []:
        path = Path(raw_path)
        if not path.exists():
            continue
        info = dict((photos_dict or {}).get(path.name, {}) or {})
        stat = path.stat()
        entries.append(
            {
                "path": str(path),
                "filename": path.name,
                "relative_path": _build_media_relative_path(path),
                "size_bytes": stat.st_size,
                "modified": stat.st_mtime,
                "file_type": "image",
                "date": date,
                "reason": info.get("description", "") or "AI 判定为相似重复照片",
            }
        )

    if len(entries) <= 1:
        return None

    keep = entries[0]
    duplicates = entries[1:]
    return {
        "group_id": hashlib.sha1(f"{date}|{'|'.join(item['path'] for item in entries)}".encode("utf-8")).hexdigest()[:12],
        "match_type": "ai_similar",
        "date": date,
        "keep": keep,
        "duplicates": duplicates,
        "duplicate_count": len(duplicates),
        "wasted_bytes": sum(int(item["size_bytes"]) for item in duplicates),
    }


@app.route("/api/media-folder-shrink/stats")
@require_local_or_password
def get_media_folder_shrink_stats():
    """获取媒体目录体积统计。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        stats = manager.collect_folder_stats()
        return jsonify({"success": True, "data": stats})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-folder-shrink/preview", methods=["POST"])
@require_local_or_password
def create_media_folder_shrink_preview():
    """生成原地压缩样例预览。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        data = request.get_json(silent=True) or {}

        def _as_bool(value, default=True):
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() not in {"0", "false", "no", "off", ""}

        preset_key = (data.get("preset_key") or "gentle").strip()
        include_images = _as_bool(data.get("include_images"), True)
        include_videos = _as_bool(data.get("include_videos"), True)
        preview = manager.create_preview(
            preset_key,
            include_images=include_images,
            include_videos=include_videos,
        )
        return jsonify({"success": True, "data": preview})
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-folder-shrink/preview/<token>", methods=["DELETE"])
@require_local_or_password
def clear_media_folder_shrink_preview(token):
    """清理样例预览。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        manager.cleanup_preview(token)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-folder-shrink/preview-file/<token>/<variant>")
@require_local_or_password
def get_media_folder_shrink_preview_file(token, variant):
    """读取样例预览文件。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        file_path = manager.get_preview_file(token, variant)
        if not file_path:
            return jsonify({"success": False, "message": "预览已失效"}), 404

        ext = file_path.suffix.lower()
        if ext in [".jpg", ".jpeg"]:
            mimetype = "image/jpeg"
        elif ext == ".png":
            mimetype = "image/png"
        elif ext == ".webp":
            mimetype = "image/webp"
        elif ext in [".mp4", ".m4v"]:
            mimetype = "video/mp4"
        elif ext == ".mov":
            mimetype = "video/quicktime"
        elif ext == ".mkv":
            mimetype = "video/x-matroska"
        else:
            mimetype = "application/octet-stream"
        return send_file(file_path, mimetype=mimetype, max_age=60)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-folder-shrink/start", methods=["POST"])
@require_local_or_password
def start_media_folder_shrink():
    """启动原地压缩任务。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        data = request.get_json(silent=True) or {}
        token = (data.get("token") or "").strip()
        result = manager.start_batch_compression(token)
        return jsonify({"success": True, "data": result, "message": "压缩任务已启动"})
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-folder-shrink/status")
@require_local_or_password
def get_media_folder_shrink_status():
    """获取原地压缩任务状态。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        return jsonify({"success": True, "data": manager.get_batch_status()})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/file-preview")
@require_local_or_password
def media_quality_file_preview():
    """按路径读取媒体文件预览。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        raw_path = (request.args.get("path") or "").strip()
        file_path = manager.resolve_media_path(raw_path)
        if not file_path or not file_path.exists():
            return jsonify({"success": False, "message": "文件不存在"}), 404

        use_thumb = (request.args.get("thumb") or "").strip() in {"1", "true", "yes"}
        source_ext = file_path.suffix.lower()
        is_video = source_ext in getattr(manager, "VIDEO_EXTENSIONS", set())
        if use_thumb and not is_video:
            preview_path = get_or_create_thumbnail(
                file_path,
                size=(360, 360),
                cache_tag="blur_preview",
            )
        elif use_thumb and is_video:
            preview_path = get_or_create_video_thumbnail(file_path)
        else:
            preview_path = file_path

        ext = preview_path.suffix.lower()
        if ext in [".jpg", ".jpeg"]:
            mimetype = "image/jpeg"
        elif ext == ".png":
            mimetype = "image/png"
        elif ext == ".webp":
            mimetype = "image/webp"
        elif ext == ".gif":
            mimetype = "image/gif"
        elif ext in [".mp4", ".m4v"]:
            mimetype = "video/mp4"
        elif ext == ".mov":
            mimetype = "video/quicktime"
        elif ext == ".mkv":
            mimetype = "video/x-matroska"
        elif ext == ".webm":
            mimetype = "video/webm"
        else:
            mimetype = "application/octet-stream"

        return send_file(preview_path, mimetype=mimetype, max_age=120)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/blurry/scan")
@require_local_or_password
def scan_blurry_media_images():
    """扫描明显模糊的图片候选。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        sensitivity = (request.args.get("sensitivity") or "standard").strip().lower()
        result = manager.scan_blurry_images(sensitivity=sensitivity)
        return jsonify({"success": True, "data": _decorate_blurry_scan_result(result)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/blurry/scan/start", methods=["POST"])
@require_local_or_password
def start_scan_blurry_media_images():
    """后台启动明显模糊图片扫描。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        data = request.get_json(silent=True) or {}
        sensitivity = (data.get("sensitivity") or "standard").strip().lower()
        result = manager.start_blurry_scan(sensitivity=sensitivity)
        return jsonify({"success": True, "data": result, "message": "模糊扫描已启动"})
    except ValueError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/blurry/scan/status")
@require_local_or_password
def get_scan_blurry_media_images_status():
    """获取模糊图片扫描状态。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        status = manager.get_blurry_scan_status()
        if status.get("result"):
            status["result"] = _decorate_blurry_scan_result(status["result"])
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/duplicates/scan")
@require_local_or_password
def scan_duplicate_media_files():
    """扫描同名重复的媒体文件。"""
    try:
        manager = _get_media_folder_shrinker_manager()
        result = manager.scan_same_name_duplicates()
        return jsonify({"success": True, "data": _decorate_duplicate_groups(result)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/duplicates/delete", methods=["POST"])
@require_local_or_password
def delete_duplicate_media_files():
    """批量删除确认后的同名重复照片。"""
    try:
        data = request.get_json(silent=True) or {}
        raw_paths = data.get("paths") or []
        if not raw_paths:
            return jsonify({"success": False, "message": "未选择要删除的同名重复照片"}), 400

        manager = _get_media_folder_shrinker_manager()
        result = manager.delete_files(raw_paths)

        removed_from_index = _remove_media_paths_from_photo_index(result.get("deleted"))

        return jsonify(
            {
                "success": True,
                "message": f"已删除 {len(result.get('deleted', []))} 张同名重复照片",
                "data": {
                    **result,
                    "removed_from_index": removed_from_index,
                },
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


def _run_ai_deep_cleanup_task(task_id: str, estimate: dict):
    global AI_DEEP_CLEANUP_STATUS

    try:
        from select_best_photo import analyze_all_photos
        import gc

        inventory = _collect_ai_deep_cleanup_inventory()
        date_groups = inventory.get("date_groups") or []
        total_dates = len(date_groups)
        total_images = int(inventory.get("image_count") or 0)
        processed_images = 0
        blurry_items = []
        duplicate_groups = []
        analyzed_dates = 0
        child_id = get_ai_identity_client_id(getattr(public_client, "client_id", ""))
        client_id = getattr(public_client, "client_id", "")

        with AI_DEEP_CLEANUP_LOCK:
            AI_DEEP_CLEANUP_STATUS = _new_ai_deep_cleanup_status()
            AI_DEEP_CLEANUP_STATUS.update(
                {
                    "running": True,
                    "task_id": task_id,
                    "phase": "running",
                    "current": 0,
                    "total": total_images,
                    "percent": 0,
                    "message": "正在启动 AI 深度清理任务",
                    "started_at": time.time(),
                    "estimate": estimate,
                }
            )

        for group_index, group in enumerate(date_groups, start=1):
            date = group["date"]
            photo_paths = group["paths"]
            with AI_DEEP_CLEANUP_LOCK:
                AI_DEEP_CLEANUP_STATUS["current_date"] = date
                AI_DEEP_CLEANUP_STATUS["message"] = f"正在分析 {date} 的照片"

            ai_result, _skipped = _describe_photos_with_block_fallback(
                photo_paths,
                child_id=child_id,
                client_id=client_id,
                date=date,
                log_prefix="[AI深度清理]",
            )

            if ai_result:
                analyze_all_photos(
                    photo_paths,
                    max_photos=len(photo_paths),
                    client_id=client_id,
                    date=date,
                    ai_result=ai_result,
                )
                photos_dict = ai_result.get("photos", {}) or {}
                for raw_path in ai_result.get("blurry", []) or []:
                    if raw_path:
                        blurry_items.append(
                            _build_ai_deep_blurry_item(raw_path, date, photos_dict.get(Path(raw_path).name, {}))
                        )
                for dup_group in ai_result.get("duplicates", []) or []:
                    normalized = _build_ai_deep_duplicate_group(dup_group, date, photos_dict)
                    if normalized:
                        duplicate_groups.append(normalized)

            processed_images += len(photo_paths)
            analyzed_dates += 1
            with AI_DEEP_CLEANUP_LOCK:
                AI_DEEP_CLEANUP_STATUS["current"] = processed_images
                AI_DEEP_CLEANUP_STATUS["total"] = total_images
                AI_DEEP_CLEANUP_STATUS["percent"] = (
                    int(round((processed_images / total_images) * 100)) if total_images else 100
                )
                AI_DEEP_CLEANUP_STATUS["current_file"] = None
                AI_DEEP_CLEANUP_STATUS["message"] = (
                    f"已完成 {analyzed_dates}/{total_dates} 个日期批次"
                )
            gc.collect()
            time.sleep(0.05)

        result = {
            "blurry_items": blurry_items,
            "duplicate_groups": duplicate_groups,
            "summary": {
                "analyzed_dates": analyzed_dates,
                "analyzed_images": total_images,
                "blurry_count": len(blurry_items),
                "duplicate_group_count": len(duplicate_groups),
                "duplicate_file_count": sum(len(group.get("duplicates", [])) for group in duplicate_groups),
            },
        }
        with AI_DEEP_CLEANUP_LOCK:
            AI_DEEP_CLEANUP_STATUS["running"] = False
            AI_DEEP_CLEANUP_STATUS["phase"] = "done"
            AI_DEEP_CLEANUP_STATUS["percent"] = 100
            AI_DEEP_CLEANUP_STATUS["finished_at"] = time.time()
            AI_DEEP_CLEANUP_STATUS["message"] = "AI 深度去重与模糊识别完成"
            AI_DEEP_CLEANUP_STATUS["result"] = result
            AI_DEEP_CLEANUP_STATUS["resource_hint"] = "结果已生成，可直接在弹窗里筛选删除。"
    except Exception as e:
        with AI_DEEP_CLEANUP_LOCK:
            AI_DEEP_CLEANUP_STATUS["running"] = False
            AI_DEEP_CLEANUP_STATUS["phase"] = "failed"
            AI_DEEP_CLEANUP_STATUS["finished_at"] = time.time()
            AI_DEEP_CLEANUP_STATUS["message"] = "AI 深度清理失败"
            AI_DEEP_CLEANUP_STATUS["error"] = str(e)


@app.route("/api/media-quality/ai-deep/estimate")
@require_local_or_password
def estimate_ai_deep_media_cleanup():
    """读取 AI 深度去重与模糊识别的待处理规模。"""
    try:
        estimate = _estimate_ai_deep_cleanup_payload()
        return jsonify({"success": True, "data": estimate})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/ai-deep/start", methods=["POST"])
@require_local_or_password
def start_ai_deep_media_cleanup():
    """启动 AI 深度去重与模糊识别。"""
    global AI_DEEP_CLEANUP_THREAD, AI_DEEP_CLEANUP_STATUS

    try:
        estimate = _estimate_ai_deep_cleanup_payload()

        with AI_DEEP_CLEANUP_LOCK:
            if AI_DEEP_CLEANUP_STATUS.get("running"):
                return jsonify({"success": False, "message": "已有 AI 深度清理任务正在运行中"}), 400

            task_id = uuid.uuid4().hex
            AI_DEEP_CLEANUP_STATUS = _new_ai_deep_cleanup_status()
            AI_DEEP_CLEANUP_STATUS.update(
                {
                    "running": True,
                    "task_id": task_id,
                    "phase": "starting",
                    "started_at": time.time(),
                    "message": "正在准备 AI 深度清理任务",
                    "estimate": estimate,
                }
            )

        AI_DEEP_CLEANUP_THREAD = threading.Thread(
            target=_run_ai_deep_cleanup_task,
            args=(task_id, estimate),
            daemon=True,
        )
        AI_DEEP_CLEANUP_THREAD.start()
        return jsonify({"success": True, "data": {"task_id": task_id}, "message": "AI 深度清理已启动"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/ai-deep/status")
@require_local_or_password
def get_ai_deep_media_cleanup_status():
    """获取 AI 深度清理状态。"""
    try:
        with AI_DEEP_CLEANUP_LOCK:
            status = dict(AI_DEEP_CLEANUP_STATUS)
        if status.get("result"):
            status["result"] = _decorate_ai_deep_cleanup_result(status["result"])
        return jsonify({"success": True, "data": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/ai-deep/delete", methods=["POST"])
@require_local_or_password
def delete_ai_deep_media_cleanup_files():
    """删除 AI 深度清理选中的照片。"""
    try:
        data = request.get_json(silent=True) or {}
        raw_paths = data.get("paths") or []
        if not raw_paths:
            return jsonify({"success": False, "message": "未选择要删除的照片"}), 400

        manager = _get_media_folder_shrinker_manager()
        result = manager.delete_files(raw_paths)
        removed_from_index = _remove_media_paths_from_photo_index(result.get("deleted"))
        return jsonify(
            {
                "success": True,
                "message": f"已删除 {len(result.get('deleted', []))} 张照片",
                "data": {**result, "removed_from_index": removed_from_index},
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/media-quality/blurry/delete", methods=["POST"])
@require_local_or_password
def delete_blurry_media_images():
    """批量删除已确认的模糊图片原文件。"""
    try:
        data = request.get_json(silent=True) or {}
        raw_paths = data.get("paths") or []
        if not raw_paths:
            return jsonify({"success": False, "message": "未选择要删除的图片"}), 400

        manager = _get_media_folder_shrinker_manager()
        result = manager.delete_files(raw_paths)

        removed_from_index = []
        media_folders = getattr(public_client, "media_folders", [])
        if media_folders and result.get("deleted"):
            from photo_manager import PhotoManager

            pm = PhotoManager(media_folders, public_client.data_dir)
            for deleted_path in result["deleted"]:
                try:
                    if pm.remove_photo_by_path(deleted_path):
                        removed_from_index.append(deleted_path)
                except Exception:
                    continue

        return jsonify(
            {
                "success": True,
                "message": f"已删除 {len(result.get('deleted', []))} 张模糊图片",
                "data": {
                    **result,
                    "removed_from_index": removed_from_index,
                },
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/compression/status")
def get_compression_status():
    """获取压缩状态"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if manager:
            status = manager.get_status()
            return jsonify({"success": True, "data": status})
        else:
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "enabled": False,
                        "ffmpeg_available": False,
                        "videos_compressed": 0,
                        "images_compressed": 0,
                        "queue_length": 0,
                        "current_file": None,
                    },
                }
            )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/compression/update", methods=["POST"])
@require_local_or_password
def update_compression_settings():
    """更新压缩设置并重新生成压缩文件"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"})

        data = request.get_json() or {}

        current_settings = manager.get_settings()
        settings_changed = (
            data.get("video_quality")
            and data.get("video_quality") != current_settings.get("video_quality")
            or data.get("image_quality")
            and data.get("image_quality") != current_settings.get("image_quality")
        )

        if settings_changed:
            manager.clear_compressed_files()
            print("🔄 设置已更改，清空现有压缩文件")

        if data:
            manager.save_settings(data)

        result = manager.compress_all()

        return jsonify(
            {
                "success": True,
                "message": "设置已保存，正在重新压缩所有文件"
                if settings_changed
                else "设置已保存",
                "data": result,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/compression/regenerate", methods=["POST"])
@require_local_or_password
def regenerate_compression():
    """重新生成所有压缩文件"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"})

        result = manager.regenerate_all()

        return jsonify(
            {"success": True, "message": "正在重新生成所有压缩文件", "data": result}
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/compression/check-ffmpeg")
def check_ffmpeg():
    """检查 FFmpeg 是否可用"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if manager:
            available = manager.is_ffmpeg_available()
            return jsonify(
                {
                    "success": True,
                    "available": available,
                    "message": "FFmpeg 可用" if available else "FFmpeg 不可用",
                }
            )
        else:
            return jsonify(
                {"success": False, "available": False, "message": "压缩管理器未初始化"}
            )

    except Exception as e:
        return jsonify({"success": False, "available": False, "message": str(e)})


@app.route("/video/compressed/<filename>")
def get_compressed_video(filename):
    """获取压缩后的视频（用于自动降级）"""
    try:
        from video_compressor import get_compression_manager
        from photo_manager import PhotoManager

        manager = get_compression_manager()
        media_folders = getattr(public_client, "media_folders", [])

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"}), 404

        if media_folders:
            pm = PhotoManager(media_folders, public_client.data_dir)
            entry = pm.get_photo_by_filename(filename)

            if entry and Path(entry["path"]).exists():
                source_path = Path(entry["path"])
                compressed_path = manager.get_compressed_file_path(
                    source_path, filename
                )

                if compressed_path and compressed_path.exists():
                    return send_file(
                        compressed_path,
                        mimetype="video/mp4",
                        conditional=True,
                        download_name=filename,
                    )

        return jsonify({"success": False, "message": "压缩视频不存在"}), 404

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/storybook")
def storybook_page():
    """时光相册在线页。"""
    collection = get_story_collection_data(
        public_client.client_id,
        generate_missing=False,
    )
    avatar_url = getattr(
        public_client, "avatar_url", DEFAULT_AVATAR_URL
    )
    return render_template(
        "storybook.html",
        baby_name=public_client.baby_name,
        collection=collection,
        avatar_url=avatar_url,
    )


@app.route("/storybook/print")
def storybook_print_page():
    """时光相册长卷打印版，用于浏览器打印或 PDF 导出。"""
    collection = get_story_collection_data(
        public_client.client_id,
        generate_missing=False,
    )
    avatar_url = getattr(
        public_client, "avatar_url", DEFAULT_AVATAR_URL
    )
    return render_template(
        "storybook_print.html",
        baby_name=public_client.baby_name,
        collection=collection,
        avatar_url=avatar_url,
    )


@app.route("/api/storybook")
def api_storybook():
    collection = get_story_collection_data(
        public_client.client_id,
        generate_missing=False,
    )
    return jsonify({"success": True, "collection": collection})


@app.route("/api/storybook/generate", methods=["POST"])
@require_local_or_password
def api_generate_storybook():
    """兼容旧接口：时光相册改为实时整理，不再依赖故事补齐。"""
    try:
        _clear_story_collection_cache(public_client.client_id)
        collection = get_story_collection_data(
            public_client.client_id,
            generate_missing=False,
        )
        return jsonify(
            {
                "success": True,
                "message": "时光相册现在会实时整理，无需再补齐故事缓存。",
                "collection": collection,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/storybook/pdf")
def storybook_pdf():
    """PDF 导出已下线，统一改为长图分享。"""
    return "PDF 导出已下线，请改用“下载长图”。", 410


@app.route("/image/compressed/<filename>")
def get_compressed_image(filename):
    """获取压缩后的图片（用于自动降级）"""
    try:
        from video_compressor import get_compression_manager
        from photo_manager import PhotoManager

        manager = get_compression_manager()
        media_folders = getattr(public_client, "media_folders", [])

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"}), 404

        if media_folders:
            pm = PhotoManager(media_folders, public_client.data_dir)
            entry = pm.get_photo_by_filename(filename)

            if entry and Path(entry["path"]).exists():
                source_path = Path(entry["path"])
                compressed_path = manager.get_compressed_file_path(
                    source_path, filename
                )

                if compressed_path and compressed_path.exists():
                    ext = Path(filename).suffix.lower()
                    mime_type = (
                        "image/jpeg" if ext in [".jpg", ".jpeg"] else f"image/{ext[1:]}"
                    )

                    return send_file(compressed_path, mimetype=mime_type)

        return jsonify({"success": False, "message": "压缩图片不存在"}), 404

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


MATERIAL_PROCESS_STATUS = {
    "running": False,
    "last_run": None,
    "last_result": None,
}

ERROR_REPORT_QUEUE = []
REPORTED_ERRORS = set()
MAX_ERROR_QUEUE_SIZE = 10


def report_error(error_type: str, message: str, details: str = ""):
    """添加错误到上报队列（去重）"""
    error_key = f"{error_type}:{message[:100]}"
    if error_key in REPORTED_ERRORS:
        return

    REPORTED_ERRORS.add(error_key)
    ERROR_REPORT_QUEUE.append(
        {
            "type": error_type,
            "message": message[:200],
            "details": details[:500],
            "time": datetime.now().isoformat(),
        }
    )

    if len(ERROR_REPORT_QUEUE) > MAX_ERROR_QUEUE_SIZE:
        ERROR_REPORT_QUEUE.pop(0)


def auto_process_and_upload_materials(target_date: str = None):
    """
    自动处理当天素材并上传到服务端
    包括：照片分析 + 视频语音识别
    """
    import time
    from datetime import datetime
    from pathlib import Path

    global MATERIAL_PROCESS_STATUS

    if MATERIAL_PROCESS_STATUS["running"]:
        print("[素材处理] 已有任务在运行，跳过")
        return

    MATERIAL_PROCESS_STATUS["running"] = True

    try:
        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")

        print(f"[素材处理] 开始处理 {target_date} 的素材...")

        client_id = getattr(public_client, "client_id", "")
        if not client_id:
            print("[素材处理] 未配置 client_id，跳过")
            return

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            print("[素材处理] 未配置媒体文件夹，跳过")
            return

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)
        all_media = pm.get_photos_by_date(target_date)

        if not all_media:
            print(f"[素材处理] {target_date} 没有素材，跳过")
            MATERIAL_PROCESS_STATUS["last_result"] = {
                "success": True,
                "message": "没有素材",
            }
            return

        photos = [m for m in all_media if not m.get("is_video", False)]
        videos = [m for m in all_media if m.get("is_video", False)]

        print(f"[素材处理] 找到 {len(photos)} 张照片, {len(videos)} 个视频")

        result = {"photos": 0, "videos": 0, "errors": []}

        # 处理照片
        if photos:
            try:
                image_entries = [p for p in photos if Path(p.get("path", "")).exists()]

                if image_entries:
                    print(f"[素材处理] 分析 {len(image_entries)} 张照片...")
                    step_start = time.time()
                    child_id = get_ai_identity_client_id(client_id)
                    ensured = _ensure_photo_descriptions_for_entries(
                        target_date,
                        client_id,
                        child_id=child_id,
                        processed_photos=get_processed_photos(target_date, client_id),
                        media_entries=image_entries,
                        max_photos=None,
                        log_prefix="[素材处理]",
                    )
                    photo_analysis = _build_photo_analysis_from_records(
                        ensured.get("photos", [])
                    )
                    print(
                        f"[素材处理] 照片分析完成，耗时 {time.time() - step_start:.1f}s，"
                        f"新增描述 {ensured.get('analyzed_count', 0)} 张"
                    )

                    upload_resp = public_client.signed_request(
                        "POST",
                        f"{public_client.server_url}/czrz/photos/analysis",
                        json={
                            "client_id": client_id,
                            "date": target_date,
                            "analysis": photo_analysis,
                        },
                        timeout=1200,
                    )
                    if upload_resp.status_code == 200:
                        result["photos"] = len(photo_analysis.get("photos", []))
                        print(f"[素材处理] 照片分析已上传")

            except Exception as e:
                result["errors"].append(f"照片处理失败: {e}")
                print(f"[素材处理] 照片处理失败: {e}")
                report_error("material_photo", f"照片处理失败", str(e))

        # 处理视频语音
        if videos:
            try:
                from video_audio_processor import (
                    process_video_speech,
                    analyze_language_ability,
                )

                video_results = []
                video_entry_map = {
                    item.get("path", ""): item for item in videos if item.get("path")
                }

                for video in videos:
                    video_path = Path(video.get("path", ""))
                    if not video_path.exists():
                        indexed_video_hash = (
                            video_entry_map.get(str(video_path), {}).get("hash") or ""
                        ).strip()
                        public_client.signed_request(
                            "POST",
                            f"{public_client.server_url}/czrz/speech/record",
                            json={
                                "client_id": client_id,
                                "date": target_date,
                                "video_path": str(video_path),
                                "file_hash": indexed_video_hash,
                                "transcript": "",
                                "duration": 0,
                                "analysis": _build_terminal_video_analysis(
                                    "索引存在但磁盘文件不存在",
                                    error_code="missing_source_file",
                                    note="视频文件不存在，按已处理跳过",
                                ),
                            },
                            timeout=30,
                        )
                        continue

                    try:
                        step_start = time.time()
                        speech_result = process_video_speech(
                            video_path, max_duration=60
                        )
                        print(
                            f"[素材处理] 视频处理耗时 {time.time() - step_start:.1f}s"
                        )

                        if speech_result.get("success") and speech_result.get(
                            "transcript"
                        ):
                            video_results.append(
                                {
                                    "path": str(video_path),
                                    "file_hash": (
                                        video_entry_map.get(str(video_path), {}).get("hash")
                                        or _resolve_media_hash(str(video_path))
                                    ),
                                    "transcript": speech_result["transcript"],
                                    "duration": speech_result.get("duration", 0),
                                }
                            )
                        else:
                            indexed_video_hash = (
                                video_entry_map.get(str(video_path), {}).get("hash") or ""
                            ).strip()
                            public_client.signed_request(
                                "POST",
                                f"{public_client.server_url}/czrz/speech/record",
                                json={
                                    "client_id": client_id,
                                    "date": target_date,
                                    "video_path": str(video_path),
                                    "file_hash": indexed_video_hash
                                    or _resolve_media_hash(str(video_path)),
                                    "transcript": "",
                                    "duration": speech_result.get("duration", 0),
                                    "analysis": _build_terminal_video_analysis(
                                        speech_result.get("error", "未知错误"),
                                        error_code="no_audio",
                                        note="语音识别失败或无有效语音",
                                    ),
                                },
                                timeout=30,
                            )
                    except Exception as e:
                        print(f"[素材处理] 视频处理失败 {video_path}: {e}")
                        indexed_video_hash = (
                            video_entry_map.get(str(video_path), {}).get("hash") or ""
                        ).strip()
                        public_client.signed_request(
                            "POST",
                            f"{public_client.server_url}/czrz/speech/record",
                            json={
                                "client_id": client_id,
                                "date": target_date,
                                "video_path": str(video_path),
                                "file_hash": indexed_video_hash
                                or _resolve_media_hash(str(video_path)),
                                "transcript": "",
                                "duration": 0,
                                "analysis": _build_terminal_video_analysis(
                                    str(e),
                                    error_code="video_process_error",
                                ),
                            },
                            timeout=30,
                        )

                # 按时长排序，优先先处理长视频，但不再截断数量
                if video_results:
                    video_results.sort(key=lambda x: x.get("duration", 0), reverse=True)

                    baby_info = getattr(public_client, "baby_info", {}) or {}
                    baby_name = baby_info.get("baby_name", "宝宝")
                    age_months = baby_info.get("age_months", 0)

                    for vr in video_results:
                        analysis = analyze_language_ability(
                            vr["transcript"], baby_name=baby_name, age_months=age_months
                        )

                        upload_resp = public_client.signed_request(
                            "POST",
                            f"{public_client.server_url}/czrz/speech/record",
                            json={
                                "client_id": client_id,
                                "date": target_date,
                                "video_path": vr["path"],
                                "file_hash": vr.get("file_hash", "") or "",
                                "transcript": vr["transcript"],
                                "duration": vr["duration"],
                                "analysis": analysis,
                            },
                            timeout=30,
                        )
                        if upload_resp.status_code == 200:
                            result["videos"] += 1

                    print(f"[素材处理] 语音记录已上传 {result['videos']} 条")

            except Exception as e:
                result["errors"].append(f"视频处理失败: {e}")
                print(f"[素材处理] 视频处理失败: {e}")
                report_error("material_video", f"视频处理失败", str(e))

        MATERIAL_PROCESS_STATUS["last_result"] = result
        print(f"[素材处理] 完成: 照片 {result['photos']}, 视频 {result['videos']}")

    except Exception as e:
        print(f"[素材处理] 失败: {e}")
        MATERIAL_PROCESS_STATUS["last_result"] = {"success": False, "error": str(e)}
        report_error("material_process", f"素材处理失败", str(e))

    finally:
        MATERIAL_PROCESS_STATUS["running"] = False
        MATERIAL_PROCESS_STATUS["last_run"] = datetime.now().isoformat()


def material_process_scheduler():
    """素材处理定时任务：每天18:00自动处理"""
    import time
    from datetime import datetime, timedelta

    print("[素材处理定时] 已启动，将在每天 18:00 自动处理素材")

    while True:
        try:
            now = datetime.now()
            target_time = now.replace(hour=18, minute=0, second=0, microsecond=0)

            if now >= target_time:
                target_time += timedelta(days=1)

            wait_seconds = (target_time - now).total_seconds()
            print(
                f"[素材处理定时] 下次处理时间: {target_time}, 等待 {wait_seconds / 3600:.1f} 小时"
            )

            time.sleep(wait_seconds)

            print(f"[素材处理定时] {datetime.now()} 开始自动处理素材")
            auto_process_and_upload_materials()

        except Exception as e:
            print(f"[素材处理定时] 错误: {e}")
            time.sleep(3600)


def show_welcome_dialog():
    """显示欢迎对话框（支持 Windows/macOS/Linux 打包环境）"""
    # 只在打包环境下显示
    if not hasattr(sys, "_MEIPASS"):
        return

    title = "宝宝成长记录系统"

    message = """欢迎使用宝宝成长记录系统！

【系统简介】
这是一款专为记录宝宝成长点滴设计的智能系统，
支持照片管理、AI 生成成长日志、公网分享等功能。

【使用步骤】
1. 首次使用会自动打开设置向导页面
2. 填写宝宝信息（名字、生日、性别）
3. 选择照片所在的文件夹
4. 点击"完成设置"保存配置
5. 系统会自动打开主页，开始记录美好时光

【功能说明】
• 首页：查看今日照片和成长日志
• 画像：AI 生成的宝宝性格兴趣画像
• 日历：按日期浏览历史记录
• 设置：修改配置、查看调用记录

【提示】
• 首次运行会自动打开浏览器
• 如需公网访问，请确保网络正常
• 日志文件位于：文档/CZRZ/logs/

点击"确定"开始使用！"""

    try:
        if sys.platform == "win32":
            # Windows: 使用 Windows API MessageBox
            MB_OK = 0
            MB_ICONINFORMATION = 64
            MB_SETFOREGROUND = 0x10000
            ctypes.windll.user32.MessageBoxW(
                0, message, title, MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND
            )
        elif sys.platform == "darwin":
            # macOS: 使用 osascript (AppleScript)
            import subprocess

            # AppleScript 语法显示对话框
            applescript = f'display dialog "{message}" with title "{title}" buttons {{"确定"}} default button "确定" with icon note'
            subprocess.run(["osascript", "-e", applescript], check=False)
        else:
            # Linux: 使用 zenity (GNOME) 或 kdialog (KDE)
            import subprocess

            # 优先尝试 zenity
            try:
                subprocess.run(
                    ["zenity", "--info", f"--title={title}", f"--text={message}"],
                    check=False,
                    capture_output=True,
                )
            except FileNotFoundError:
                # 如果 zenity 不存在，尝试 kdialog
                try:
                    subprocess.run(
                        ["kdialog", f"--title={title}", f"--msgbox={message}"],
                        check=False,
                        capture_output=True,
                    )
                except FileNotFoundError:
                    # 都不存在，跳过对话框
                    print("提示: 安装 zenity 或 kdialog 可显示图形欢迎对话框")
                    print(message)
    except Exception as e:
        print(f"显示欢迎对话框失败: {e}")


if __name__ == "__main__":
    # 显示欢迎对话框（仅 Windows 打包环境）
    show_welcome_dialog()

    print("\n" + "=" * 60)
    print("  成长记录系统 - 公网客户端")
    print("=" * 60)
    print(f"  强制连接: {public_client.server_domain}")
    print("=" * 60)

    # 使用统一的端口查找方法（排除1024以下端口，范围3000-9000）
    port = public_client.find_available_port()

    if port:
        public_client.client_port = port

        print(f"\n🚀 启动服务器 (端口: {port})")
        print(f"🌐 本地访问: http://localhost:{port}")
        print(f"🌐 局域网访问: http://{public_client.get_local_ip()}:{port}")
        print(f"📡 监听地址: 0.0.0.0:{port} (所有网络接口)")

        # 先获取子域名信息（不启动 tunnel）
        if not public_client.is_first_run():
            # 获取子域名但不启动 tunnel（tunnel 在 Flask 启动后启动）
            if public_client.client_id:
                public_client.fetch_subdomain_only(background=True, timeout=20)

        if public_client.public_url:
            print(f"🔗 公网访问: {public_client.public_url}")

        print("=" * 60)

        # 打开浏览器
        try:
            import webbrowser

            webbrowser.open(f"http://localhost:{port}")
            print("✅ 浏览器已打开")
        except Exception as e:
            print(f"⚠️ 打开浏览器失败: {e}")

        # 启动后台心跳循环
        if public_client.client_id:
            public_client.start_heartbeat_loop()
            print("✅ 心跳循环已启动")

        # 初始化压缩管理器
        try:
            from video_compressor import (
                init_compression_manager,
                get_compression_manager,
            )

            media_folders = [
                Path(f)
                for f in getattr(public_client, "media_folders", [])
                if Path(f).exists()
            ]
            init_compression_manager(public_client.data_dir, media_folders)

            manager = get_compression_manager()
            if manager:
                manager.start_worker()
                # 启动时自动压缩已有文件
                if manager.is_ffmpeg_available():
                    manager.compress_all()
                print("✅ 压缩服务已启动")
        except Exception as e:
            print(f"⚠️ 压缩服务初始化失败: {e}")

        # 启动素材处理定时任务
        if public_client.client_id:
            import threading

            material_thread = threading.Thread(
                target=material_process_scheduler, daemon=True
            )
            material_thread.start()
            print("✅ 素材处理定时任务已启动（每天 18:00）")

        # 启动 Tunnel（在后台线程中，等待 Flask 绑定端口后启动）
        def start_tunnel_after_flask():
            import time

            # 等待 Flask 启动（最多等待 5 秒）
            for _ in range(50):
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    result = sock.connect_ex(("127.0.0.1", port))
                    sock.close()
                    if result == 0:  # 端口已被绑定
                        break
                except:
                    pass
                time.sleep(0.1)

            # 等待凭证到位后再启动 Tunnel（兼容后台异步获取凭证）
            for _ in range(120):  # 最多再等 60 秒
                if public_client._start_pending_tunnel_if_ready():
                    return
                time.sleep(0.5)

        if public_client.client_id:
            tunnel_thread = threading.Thread(
                target=start_tunnel_after_flask, daemon=True
            )
            tunnel_thread.start()

        # 注册退出处理：确保退出时杀死 cloudflared 进程
        def cleanup_on_exit():
            if public_client.tunnel_active:
                print("\n🛑 正在停止 Cloudflare Tunnel...")
                public_client.stop_cloudflare_tunnel()

        atexit.register(cleanup_on_exit)

        # 信号处理：捕获 SIGTERM 和 SIGINT
        def signal_handler(signum, frame):
            print(f"\n收到信号 {signum}，正在退出...")
            cleanup_on_exit()
            sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        # 启动 Flask（threaded=True 支持多用户）
        try:
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
        except Exception as e:
            print(f"\n❌ 服务器启动失败: {e}")
            cleanup_on_exit()
            input("按回车键退出...")
    else:
        # 端口查找失败（find_available_port 内部已处理提示）
        pass
