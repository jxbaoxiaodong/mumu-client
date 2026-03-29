#!/usr/bin/env python3
"""
卡片缓存系统
- 后台生成所有智能卡片
- 缓存到本地
- 提供API访问
- 里程碑卡片只在当天显示
"""

import os
import json
import time
import random
import threading
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional

from card_protocol import normalize_cards, normalize_card

# 统一数据目录
DATA_DIR = Path.home() / "Documents" / "CZRZ"


class CardCache:
    """卡片缓存管理器"""

    MAX_COMPARISON_CARDS = 10

    def __init__(self, cache_dir: str = None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = DATA_DIR / "card_cache"

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "cards.json"
        self.shared_file = self.cache_dir / "shared.json"

        # 内存缓存
        self.cards: List[Dict] = []  # 普通卡片（标签卡片、对比卡片）
        self.milestone_cards: List[Dict] = []  # 里程碑卡片（只在当天显示）
        self.shared_cards: Dict[str, bool] = {}  # card_id -> shared
        self.last_update: float = 0
        self.update_interval = 3600  # 1小时更新一次

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
                    self.milestone_cards = normalize_cards(
                        data.get("milestone_cards", [])
                    )
                    self.last_update = data.get("last_update", 0)
                print(
                    f"[CardCache] 加载缓存: {len(self.cards)}张普通卡片, {len(self.milestone_cards)}张里程碑卡片"
                )
            except Exception as e:
                print(f"[CardCache] 加载缓存失败: {e}")
                self.cards = []

    def _save_cache(self):
        """保存缓存"""
        try:
            data = {
                "cards": self.cards,
                "milestone_cards": self.milestone_cards,
                "last_update": self.last_update,
                "generated_at": datetime.now().isoformat(),
            }
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(
                f"[CardCache] 保存缓存: {len(self.cards)}张普通卡片, {len(self.milestone_cards)}张里程碑卡片"
            )
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
        for existing in self.milestone_cards:
            if self._card_signature(existing) == signature:
                return True
        return False

    def generate_all_cards(
        self, baby_name: str = "宝宝", base_url: str = "http://localhost:3000"
    ) -> List[Dict]:
        """
        生成所有智能卡片

        Args:
            baby_name: 宝宝名字
            base_url: 基础URL

        Returns:
            卡片列表
        """
        print(f"[CardCache] 开始生成所有卡片...")
        all_cards = []

        try:
            from smart_card_generator import SmartCardGenerator

            generator = SmartCardGenerator()

            # 1. 生成标签主题卡片
            print("[CardCache] 生成标签主题卡片...")
            tags = generator.get_tags()
            if tags:
                tags = generator.merge_tags_with_llm(tags, max_tags=12)
            generated_tags = set()

            for tag_info in tags:  # 生成去重后的标签卡片（不强制全量）
                tag = tag_info["tag"]
                if tag in generated_tags:
                    continue

                try:
                    photos = generator.find_photos_by_tag(tag, limit=4)
                    if len(photos) >= 2:
                        # 只保留素材元数据，最终卡片由客户端按类型渲染
                        photo_paths = [
                            p["path"] for p in photos if Path(p["path"]).exists()
                        ]
                        if len(photo_paths) >= 2:
                            tag_themes = {
                                "爱笑": {"emoji": "😊", "title": "爱笑的天使"},
                                "活泼好动": {"emoji": "🏃", "title": "活力满满"},
                                "专注力好": {"emoji": "🎯", "title": "专注小达人"},
                                "好奇心强": {"emoji": "🔍", "title": "好奇探索家"},
                                "安静": {"emoji": "😴", "title": "安静小天使"},
                                "运动能力强": {
                                    "emoji": "💪",
                                    "title": "运动小健将",
                                },
                                "表情丰富": {"emoji": "😊", "title": "表情包达人"},
                                "探索欲强": {"emoji": "🌟", "title": "小小探索家"},
                                "乖巧": {"emoji": "🥰", "title": "乖巧宝贝"},
                                "睡眠安稳": {"emoji": "😴", "title": "安睡小天使"},
                            }
                            theme = tag_themes.get(
                                tag, {"emoji": "✨", "title": f"{tag}的宝宝"}
                            )

                            photo_dates = [
                                p.get("date", "") for p in photos if p.get("date")
                            ]
                            formatted_date = ""
                            if photo_dates:
                                latest_date = sorted(photo_dates)[-1]
                                try:
                                    if "-" in latest_date:
                                        date_obj = datetime.strptime(
                                            latest_date, "%Y-%m-%d"
                                        )
                                        formatted_date = f"{str(date_obj.year)[-2:]}.{date_obj.month:02d}.{date_obj.day:02d}"
                                except:
                                    pass

                            all_cards.append(
                                {
                                    "id": f"tag_{tag}_{int(time.time())}",
                                    "type": "tag_collage_card",
                                    "layout": "tag_collage",
                                    "category": "tag",
                                    "tag": tag,
                                    "photos": [
                                        {
                                            "path": p["path"],
                                            "date": p.get("date", ""),
                                        }
                                        for p in photos
                                        if p.get("path")
                                    ],
                                    "photo_paths": [
                                        p["path"] for p in photos if p.get("path")
                                    ],
                                    "title": f"{theme['emoji']} {theme['title']}",
                                    "subtitle": f"{baby_name}的{tag}时刻",
                                    "content": f"在{len(photos)}个瞬间中发现了这个可爱特质",
                                    "footer": "每一个瞬间都闪闪发光",
                                    "formatted_date": formatted_date,
                                }
                            )
                            generated_tags.add(tag)
                            print(f"  ✓ {tag}: {len(photos)}张照片")
                except Exception as e:
                    print(f"  ✗ {tag}: {e}")

            # 2. 生成成长对比卡片
            print("[CardCache] 生成成长对比卡片...")
            generated_scenes = set()

            # 使用LLM发现相似场景
            try:
                llm_scenes = generator.discover_similar_scenes_with_llm()
                print(f"[CardCache] LLM发现 {len(llm_scenes)} 个场景")

                for scene in llm_scenes:
                    scene_name = scene.get("name", "")
                    photos = scene.get("photos", [])

                    if len(photos) >= 2 and scene_name not in generated_scenes:
                        dated_photos = []
                        for p in photos:
                            date_str = p.get("date", "")
                            if date_str:
                                try:
                                    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                                    dated_photos.append((p, date_obj, date_str))
                                except:
                                    pass

                        if len(dated_photos) >= 2:
                            dated_photos.sort(key=lambda x: x[1])
                            month_groups = defaultdict(list)
                            for photo, date_obj, date_str in dated_photos:
                                month_groups[date_str[:7]].append(
                                    (photo, date_obj, date_str)
                                )

                            months = sorted(month_groups.keys())
                            target_spans = [1, 3, 6, 12]
                            added_scene_cards = 0
                            seen_pairs = set()

                            def add_comparison(before_item, after_item):
                                nonlocal added_scene_cards
                                before_photo, before_dt, before_date_raw = before_item
                                after_photo, after_dt, after_date_raw = after_item
                                before_path = before_photo.get("path", "")
                                after_path = after_photo.get("path", "")
                                if not before_path or not after_path:
                                    return

                                months_diff = (after_dt.year - before_dt.year) * 12 + (
                                    after_dt.month - before_dt.month
                                )
                                if months_diff < 1:
                                    return

                                before_formatted = f"{str(before_dt.year)[-2:]}.{before_dt.month:02d}.{before_dt.day:02d}"
                                after_formatted = f"{str(after_dt.year)[-2:]}.{after_dt.month:02d}.{after_dt.day:02d}"
                                card = {
                                    "id": f"compare_{scene_name}_{before_date_raw}_{after_date_raw}_{int(time.time() * 1000)}_{added_scene_cards}",
                                    "type": "comparison_card",
                                    "layout": "comparison",
                                    "category": "comparison",
                                    "scene": scene_name,
                                    "before_photo": before_path,
                                    "after_photo": after_path,
                                    "before_date": before_formatted,
                                    "after_date": after_formatted,
                                    "months_diff": months_diff,
                                    "title": f"⏳ {months_diff}个月的成长",
                                    "subtitle": f"{baby_name}的{scene_name}",
                                    "content": f"从{before_formatted}到{after_formatted}",
                                    "footer": "时光飞逝，成长可见",
                                    "date": datetime.now().strftime("%Y.%m.%d"),
                                    "photo_paths": [before_path, after_path],
                                    "photos": [
                                        {"path": before_path, "date": before_date_raw},
                                        {"path": after_path, "date": after_date_raw},
                                    ],
                                }
                                if not self.has_equivalent_card(card):
                                    all_cards.append(card)
                                    added_scene_cards += 1

                            for i, early_month in enumerate(months):
                                early_item = month_groups[early_month][0]
                                for span in target_spans:
                                    j = i + span
                                    if j >= len(months):
                                        continue
                                    recent_month = months[j]
                                    pair_key = (early_month, recent_month)
                                    if pair_key in seen_pairs:
                                        continue
                                    add_comparison(
                                        early_item, month_groups[recent_month][-1]
                                    )
                                    seen_pairs.add(pair_key)

                            if months:
                                add_comparison(
                                    month_groups[months[0]][0],
                                    month_groups[months[-1]][-1],
                                )

                            if added_scene_cards > 0:
                                generated_scenes.add(scene_name)
                                print(
                                    f"  ✓ [LLM] {scene_name}: 生成 {added_scene_cards} 张成长对比"
                                )
            except Exception as e:
                print(f"[CardCache] LLM场景发现失败: {e}")

            comparison_cards = [
                c for c in all_cards if c.get("type") == "comparison_card"
            ]
            if len(comparison_cards) > self.MAX_COMPARISON_CARDS:
                comparison_cards = sorted(
                    comparison_cards,
                    key=lambda c: (
                        -(c.get("months_diff") or 0),
                        c.get("before_date") or "",
                        c.get("after_date") or "",
                        c.get("id") or c.get("card_id") or "",
                    ),
                )[: self.MAX_COMPARISON_CARDS]
                comparison_set = {
                    self._card_signature(card) for card in comparison_cards
                }
                all_cards = [
                    c
                    for c in all_cards
                    if c.get("type") != "comparison_card"
                    or self._card_signature(c) in comparison_set
                ]

            # 3. 生成里程碑卡片（只在当天显示）
            print("[CardCache] 检查今日里程碑...")
            milestone_cards = generator.generate_all_milestone_cards(baby_name)
            for i, milestone_card in enumerate(milestone_cards):
                milestone_card["id"] = (
                    f"milestone_{milestone_card.get('milestone_name', '')}_{int(time.time())}_{i}"
                )
                milestone_card["category"] = "milestone"
                milestone_card["layout"] = milestone_card.get("layout") or "milestone_story"
                # 里程碑卡片单独存储，不加入all_cards
                self.milestone_cards.append(normalize_card(milestone_card))
                print(f"  ✓ 今日里程碑: {milestone_card.get('title', '')}")

            # 4. 生成扩展创意卡片（后台生成，数量不限）
            print("[CardCache] 生成扩展创意卡片...")
            try:
                from extended_card_generator import ExtendedCardGenerator

                extended_gen = ExtendedCardGenerator(str(generator.ai_db_path))
                extended_cards = extended_gen.generate_all_extended_cards(baby_name)

                print(f"[CardCache] 生成了 {len(extended_cards)} 张扩展卡片")

                # 先删除历史的同一天不同年卡片
                all_cards = [
                    c
                    for c in all_cards
                    if c.get("type") != "same_day_different_year_card"
                ]

                for i, card in enumerate(extended_cards):
                    card["id"] = f"extended_{card['type']}_{int(time.time())}_{i}"
                    card["category"] = "extended"

                    all_cards.append(card)

            except Exception as e:
                print(f"[CardCache] 扩展卡片生成失败: {e}")

        except Exception as e:
            print(f"[CardCache] 生成卡片失败: {e}")
            import traceback

            traceback.print_exc()

        deduped_cards = []
        seen_signatures = set()
        for card in all_cards:
            signature = self._card_signature(card)
            if signature in seen_signatures:
                continue
            seen_signatures.add(signature)
            deduped_cards.append(card)

        all_cards = deduped_cards
        all_cards = normalize_cards(all_cards)

        print(
            f"[CardCache] 生成完成: {len(all_cards)}张普通卡片, {len(self.milestone_cards)}张今日里程碑"
        )
        return all_cards

    def update_cache(
        self,
        baby_name: str = "宝宝",
        base_url: str = "http://localhost:3000",
        force: bool = False,
    ):
        """
        更新缓存

        Args:
            baby_name: 宝宝名字
            base_url: 基础URL
            force: 是否强制更新
        """
        now = time.time()

        if not force and (now - self.last_update) < self.update_interval:
            print(f"[CardCache] 缓存未过期，跳过更新")
            return

        print(f"[CardCache] 开始更新缓存...")

        # 先清空旧缓存，避免生成失败时继续展示过期卡片
        self.cards = []
        self.milestone_cards = []
        self._save_cache()

        # 清空里程碑卡片（每天重新计算）
        self.milestone_cards = []

        # 生成新卡片
        new_cards = self.generate_all_cards(baby_name, base_url)

        self.cards = new_cards or []
        self.last_update = now
        self._save_cache()

    def get_today_milestone_cards(self) -> List[Dict]:
        """
        获取今日里程碑卡片

        Returns:
            今日里程碑卡片列表
        """
        today = datetime.now().strftime("%Y-%m-%d")
        return [c for c in self.milestone_cards if c.get("show_date") == today]

    def get_all_cards(self) -> List[Dict]:
        """
        获取所有普通卡片（不含里程碑）

        Returns:
            普通卡片列表
        """
        return self.cards

    def get_random_cards(
        self, count: int = 5, exclude_ids: List[str] = None
    ) -> List[Dict]:
        """
        获取随机卡片

        Args:
            count: 数量
            exclude_ids: 排除的卡片ID

        Returns:
            卡片列表
        """
        if exclude_ids is None:
            exclude_ids = []

        available = [c for c in self.cards if c.get("id") not in exclude_ids]

        if len(available) <= count:
            return available

        return random.sample(available, count)

    def get_card_by_id(self, card_id: str) -> Optional[Dict]:
        """根据ID获取卡片"""
        for card in self.cards:
            if card.get("id") == card_id:
                return card
        # 也检查里程碑卡片
        for card in self.milestone_cards:
            if card.get("id") == card_id:
                return card
        return None

    def get_cache_status(self) -> Dict:
        """获取缓存状态"""
        return {
            "total_cards": len(self.cards),
            "milestone_cards": len(self.milestone_cards),
            "shared_count": len(self.shared_cards),
            "last_update": datetime.fromtimestamp(self.last_update).isoformat()
            if self.last_update
            else None,
            "cache_age_hours": (time.time() - self.last_update) / 3600
            if self.last_update
            else 0,
        }


# 全局实例
_card_cache = None


def get_card_cache() -> CardCache:
    """获取卡片缓存实例"""
    global _card_cache
    if _card_cache is None:
        _card_cache = CardCache()
    return _card_cache


def start_background_update(
    baby_name: str = "宝宝",
    base_url: str = "http://localhost:3000",
    interval: int = 3600,
):
    """
    启动后台更新线程

    Args:
        baby_name: 宝宝名字
        base_url: 基础URL
        interval: 更新间隔（秒）
    """

    def update_loop():
        cache = get_card_cache()
        while True:
            try:
                cache.update_cache(baby_name, base_url)
            except Exception as e:
                print(f"[CardCache] 后台更新失败: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=update_loop, daemon=True)
    thread.start()
    print(f"[CardCache] 后台更新线程已启动，间隔{interval}秒")


if __name__ == "__main__":
    # 测试
    cache = CardCache()
    cache.update_cache("沐沐", "http://localhost:3000", force=True)

    print("\n=== 缓存状态 ===")
    status = cache.get_cache_status()
    for k, v in status.items():
        print(f"  {k}: {v}")

    print("\n=== 随机5张卡片 ===")
    cards = cache.get_random_cards(5)
    for card in cards:
        print(f"  - {card.get('type')}: {card.get('title')}")
