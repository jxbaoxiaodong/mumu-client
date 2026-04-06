#!/usr/bin/env python3
"""
卡片缓存系统
- 缓存服务端同步下发的每日卡片
- 维护本地分享状态
- 提供去重与查询能力
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from card_protocol import normalize_cards

# 统一数据目录
DATA_DIR = Path.home() / "Documents" / "CZRZ"


class CardCache:
    """卡片缓存管理器"""

    def __init__(self, cache_dir: str = None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = DATA_DIR / "card_cache"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "cards.json"
        self.shared_file = self.cache_dir / "shared.json"

        # 内存缓存
        self.cards: List[Dict] = []
        self.shared_cards: Dict[str, bool] = {}  # card_id -> shared
        self.last_update: float = 0

        # 加载缓存
        self._load_cache()
        self._load_shared()

    def _load_cache(self):
        """加载缓存"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.cards = normalize_cards(data.get("cards", []))
                    self.last_update = data.get("last_update", 0)
                print(f"[CardCache] 加载缓存: {len(self.cards)}张卡片")
            except Exception as e:
                print(f"[CardCache] 加载缓存失败: {e}")
                self.cards = []

    def _save_cache(self):
        """保存缓存"""
        try:
            data = {
                "cards": self.cards,
                "last_update": self.last_update,
                "generated_at": datetime.now().isoformat(),
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"[CardCache] 保存缓存: {len(self.cards)}张卡片")
        except Exception as e:
            print(f"[CardCache] 保存缓存失败: {e}")

    def _load_shared(self):
        """加载已分享记录"""
        if self.shared_file.exists():
            try:
                with open(self.shared_file, "r", encoding="utf-8") as f:
                    self.shared_cards = json.load(f)
                print(f"[CardCache] 加载分享记录: {len(self.shared_cards)}条")
            except Exception as e:
                print(f"[CardCache] 加载分享记录失败: {e}")
                self.shared_cards = {}

    def _save_shared(self):
        """保存已分享记录"""
        try:
            with open(self.shared_file, "w", encoding="utf-8") as f:
                json.dump(self.shared_cards, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CardCache] 保存分享记录失败: {e}")

    def mark_shared(self, card_id: str):
        """标记卡片已分享"""
        self.shared_cards[card_id] = True
        self._save_shared()

    def is_shared(self, card_id: str) -> bool:
        """检查卡片是否已分享"""
        return self.shared_cards.get(card_id, False)

    def _card_signature(self, card: Dict) -> str:
        """生成卡片内容指纹，用于去重"""
        card_type = card.get("type") or card.get("card_type") or ""
        photo_paths = card.get("photo_paths") or []
        if not isinstance(photo_paths, list):
            photo_paths = []
        normalized_paths = "|".join(str(p) for p in photo_paths if p)
        before_date = card.get("before_date") or card.get("date_before") or ""
        after_date = card.get("after_date") or card.get("date_after") or ""
        title = card.get("title") or ""
        subtitle = card.get("subtitle") or ""
        signature = f"{card_type}::{normalized_paths}::{title}::{subtitle}"
        if not normalized_paths:
            signature = f"{signature}::{before_date}::{after_date}"
        return signature

    def has_equivalent_card(self, card: Dict) -> bool:
        """检查缓存里是否已经有相同内容的卡片"""
        signature = self._card_signature(card)
        for existing in self.cards:
            if self._card_signature(existing) == signature:
                return True
        return False

    def get_all_cards(self) -> List[Dict]:
        """获取所有缓存卡片。"""
        return self.cards

    def get_card_by_id(self, card_id: str) -> Optional[Dict]:
        """根据ID获取卡片"""
        for card in self.cards:
            if card.get("id") == card_id:
                return card
        return None


# 全局实例
_card_cache = None


def get_card_cache() -> CardCache:
    """获取卡片缓存实例"""
    global _card_cache
    if _card_cache is None:
        _card_cache = CardCache()
    return _card_cache
