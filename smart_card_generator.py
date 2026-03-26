#!/usr/bin/env python3
"""
智能卡片生成器
- 基于AI画像标签生成主题卡片
- 相同标签的照片合成主题合图
- 同场景不同日期的成长对比
"""

import os
import json
import random
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import sqlite3


class SmartCardGenerator:
    """智能卡片生成器"""

    # 标签关键词映射（基于实际照片描述词汇统计）
    TAG_KEYWORDS = {
        "爱笑": ["笑", "微笑", "张嘴"],
        "活泼好动": ["爬", "坐", "趴", "站"],
        "专注力好": ["注视", "望", "睁眼"],
        "好奇心强": ["抓", "握", "望"],
        "安静": ["闭眼", "熟睡", "安睡"],
        "运动能力强": ["爬", "坐", "趴", "站"],
        "表情丰富": ["表情", "张嘴"],
        "探索欲强": ["抓", "握", "望", "注视"],
        "乖巧": ["闭眼", "熟睡", "安睡"],
        "睡眠安稳": ["熟睡", "安睡", "闭眼"],
        "安静观察型": ["注视", "望", "睁眼"],
    }

    # 场景对比关键词（用于生成成长对比卡片）
    SCENE_KEYWORDS = {
        "被抱着": ["抱", "被抱", "怀抱", "肩头", "肩膀"],
        "吃饭": ["吃饭", "进食", "辅食", "喂食", "奶嘴", "喝奶"],
        "睡觉": ["睡觉", "熟睡", "安睡", "睡眠", "闭眼"],
        "坐着玩": ["坐", "坐着", "坐在"],
        "躺着": ["躺", "躺着", "仰卧", "侧卧", "趴"],
        "户外探索": ["户外", "公园", "街道", "外面", "室外"],
        "婴儿车": ["婴儿车", "推车", "车"],
        "玩玩具": ["玩", "玩具", "抓", "握"],
    }

    # 更多场景关键词（扩展版）
    EXTENDED_SCENES = {
        "公园玩": ["公园", "草地", "滑梯", "秋千", "沙坑", "广场"],
        "商场逛街": ["商场", "超市", "购物", "逛街", "商店"],
        "看表演": ["跳舞", "表演", "舞台", "演出", "音乐", "广场舞"],
        "运动": ["球", "乒乓球", "篮球", "足球", "运动", "打球"],
        "洗澡": ["洗澡", "浴缸", "洗澡盆", "玩水", "浴室"],
        "阅读": ["书", "绘本", "阅读", "看书", "故事书"],
        "亲子互动": ["爸爸", "妈妈", "亲子", "互动", "玩耍", "陪玩"],
        "节日庆祝": ["生日", "蛋糕", "礼物", "庆祝", "节日", "蜡烛"],
        "外出游玩": ["旅游", "景点", "游玩", "出行", "旅行"],
        "宠物互动": ["狗", "猫", "宠物", "动物", "小狗", "小猫"],
        "游泳": ["游泳", "泳池", "游泳池", "戏水"],
        "爬行": ["爬", "爬行", "地上"],
        "站立": ["站", "站立", "扶站"],
        "走路": ["走", "走路", "学步", "迈步"],
        "吃饭辅食": ["吃饭", "辅食", "喂食", "米糊", "勺子"],
        "睡觉安睡": ["睡觉", "熟睡", "安睡", "睡眠", "摇篮"],
        "户外活动": ["户外", "外面", "室外", "阳台", "院子"],
        "室内玩耍": ["室内", "家里", "客厅", "卧室", "房间"],
        "拍照合影": ["合影", "合照", "拍照", "自拍", "全家福"],
        "玩具互动": ["玩具", "积木", "球", "娃娃", "小车"],
    }

    # 场景主题映射
    SCENE_THEMES = {
        "家里": "温馨居家时光",
        "户外": "户外探索之旅",
        "公园": "公园漫步记",
        "商场": "商场初体验",
        "餐厅": "美食初体验",
    }

    def __init__(self, ai_db_path: str = None):
        """
        初始化智能卡片生成器

        Args:
            ai_db_path: AI服务端数据库路径
        """
        if ai_db_path:
            self.ai_db_path = Path(ai_db_path)
        else:
            self.ai_db_path = (
                Path.home()
                / "projects"
                / "baby_health_ai"
                / "data"
                / "children"
                / "8aa59184-88a7-499f-a153-1c67fd7989cd.db"
            )

    def _get_db_connection(self):
        """获取数据库连接"""
        if not self.ai_db_path.exists():
            return None
        return sqlite3.connect(str(self.ai_db_path))

    def get_tags(self) -> List[Dict]:
        """获取所有标签"""
        conn = self._get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, tag, category, confidence, evidence
                FROM portrait_tags
                WHERE rejected = 0
                ORDER BY confidence DESC
            """)
            rows = cursor.fetchall()

            tags = []
            for row in rows:
                tags.append(
                    {
                        "id": row[0],
                        "tag": row[1],
                        "category": row[2],
                        "confidence": row[3],
                        "evidence": row[4],
                    }
                )
            return tags
        except Exception as e:
            print(f"[SmartCard] 获取标签失败: {e}")
            return []
        finally:
            conn.close()

    def find_photos_by_tag(self, tag: str, limit: int = 5) -> List[Dict]:
        """
        根据标签查找匹配的照片

        Args:
            tag: 标签名称
            limit: 最大返回数量

        Returns:
            匹配的照片列表
        """
        conn = self._get_db_connection()
        if not conn:
            return []

        # 获取标签关键词
        keywords = self.TAG_KEYWORDS.get(tag, [tag])

        try:
            cursor = conn.cursor()

            # 获取所有有file_path的照片
            cursor.execute("""
                SELECT id, file_path, context, created_at
                FROM events
                WHERE source_type = 'PHOTO'
                AND file_path IS NOT NULL
                AND file_path != ''
                ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()

            photos = []
            for row in rows:
                context = json.loads(row[2]) if row[2] else {}
                desc = context.get("photo_description", "")

                # 检查是否包含关键词
                matched = False
                for kw in keywords:
                    if kw in desc:
                        matched = True
                        break

                if matched:
                    photos.append(
                        {
                            "id": row[0],
                            "path": row[1],
                            "date": context.get("date", ""),
                            "description": desc,
                            "scene": context.get("scene", ""),
                            "activity": context.get("activity", ""),
                        }
                    )

                    if len(photos) >= limit:
                        break

            return photos
        except Exception as e:
            print(f"[SmartCard] 查找照片失败: {e}")
            return []
        finally:
            conn.close()

    def find_photos_by_scene(self, scene: str, limit: int = 10) -> List[Dict]:
        """
        根据场景查找照片

        Args:
            scene: 场景名称
            limit: 最大返回数量

        Returns:
            照片列表（按日期排序）
        """
        conn = self._get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT id, file_path, context, created_at
                FROM events
                WHERE source_type = 'PHOTO'
                AND file_path IS NOT NULL
                AND file_path != ''
                AND context LIKE '%"scene": "%{scene}%"%'
                ORDER BY created_at DESC
                LIMIT {limit}
            """)
            rows = cursor.fetchall()

            photos = []
            for row in rows:
                context = json.loads(row[2]) if row[2] else {}
                photos.append(
                    {
                        "id": row[0],
                        "path": row[1],
                        "date": context.get("date", ""),
                        "description": context.get("photo_description", ""),
                        "scene": context.get("scene", ""),
                        "activity": context.get("activity", ""),
                    }
                )

            return photos
        except Exception as e:
            print(f"[SmartCard] 查找场景照片失败: {e}")
            return []
        finally:
            conn.close()

    def generate_tag_card(self, tag: str, baby_name: str = "宝宝") -> Optional[Dict]:
        """
        生成标签主题卡片

        Args:
            tag: 标签名称
            baby_name: 宝宝名字

        Returns:
            卡片数据
        """
        # 查找匹配的照片
        photos = self.find_photos_by_tag(tag, limit=4)

        if len(photos) < 2:
            return None

        # 生成卡片
        tag_info = {
            "爱笑": {"emoji": "😊", "title": "爱笑的天使"},
            "活泼好动": {"emoji": "🏃", "title": "活力满满"},
            "专注力好": {"emoji": "🎯", "title": "专注小达人"},
            "好奇心强": {"emoji": "🔍", "title": "好奇探索家"},
            "安静": {"emoji": "😴", "title": "安静小天使"},
            "运动能力强": {"emoji": "💪", "title": "运动小健将"},
            "表情丰富": {"emoji": "😊", "title": "表情包达人"},
            "探索欲强": {"emoji": "🌟", "title": "小小探索家"},
        }.get(tag, {"emoji": "✨", "title": f"{tag}的宝宝"})

        return {
            "type": "tag_collage_card",
            "tag": tag,
            "photos": photos,
            "title": f"{tag_info['emoji']} {tag_info['title']}",
            "subtitle": f"{baby_name}的{tag}时刻",
            "content": f"在{len(photos)}个瞬间中发现了{baby_name}{tag}的特质",
            "footer": f"每一个瞬间都闪闪发光",
            "date": datetime.now().strftime("%Y.%m.%d"),
        }

    def find_photos_by_scene_keyword(
        self, scene_name: str, limit: int = 20
    ) -> List[Dict]:
        """
        根据场景关键词查找照片

        Args:
            scene_name: 场景名称（如"被抱着"、"吃饭"）
            limit: 最大返回数量

        Returns:
            照片列表（按日期排序）
        """
        # 合并基础场景和扩展场景
        all_scenes = {**self.SCENE_KEYWORDS, **self.EXTENDED_SCENES}
        keywords = all_scenes.get(scene_name, [])
        if not keywords:
            return []

        conn = self._get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, file_path, context, created_at
                FROM events
                WHERE source_type = 'PHOTO'
                AND file_path IS NOT NULL
                AND file_path != ''
                ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()

            photos = []
            for row in rows:
                context = json.loads(row[2]) if row[2] else {}
                desc = context.get("photo_description", "")

                # 检查是否包含关键词
                matched = False
                for kw in keywords:
                    if kw in desc:
                        matched = True
                        break

                if matched:
                    photos.append(
                        {
                            "id": row[0],
                            "path": row[1],
                            "date": context.get("date", ""),
                            "description": desc,
                            "scene": context.get("scene", ""),
                            "activity": context.get("activity", ""),
                        }
                    )

                    if len(photos) >= limit:
                        break

            return photos
        except Exception as e:
            print(f"[SmartCard] 查找场景照片失败: {e}")
            return []
        finally:
            conn.close()

    def discover_similar_scenes_with_llm(self) -> List[Dict]:
        """
        使用LLM提取场景关键词，然后用关键词匹配照片

        Returns:
            相似场景列表，每个场景包含场景名和照片列表
        """
        conn = self._get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor()

            # 获取所有照片的描述
            cursor.execute("""
                SELECT id, file_path, context, created_at
                FROM events
                WHERE source_type = 'PHOTO'
                AND file_path IS NOT NULL
                AND file_path != ''
                AND context IS NOT NULL
                ORDER BY created_at DESC
            """)
            rows = cursor.fetchall()

            if len(rows) < 10:
                return []

            # 提取照片描述
            photos_with_desc = []
            for row in rows:
                context = json.loads(row[2]) if row[2] else {}
                desc = context.get("photo_description", "")
                date = context.get("date", "")
                if desc and len(desc) > 5:
                    photos_with_desc.append(
                        {
                            "id": row[0],
                            "path": row[1],
                            "description": desc,
                            "date": date,
                        }
                    )

            if len(photos_with_desc) < 10:
                return []

            print(
                f"[SmartCard] 分析 {len(photos_with_desc)} 张照片描述，提取场景关键词..."
            )

            # 让LLM提取场景关键词
            prompt = f"""分析这些婴儿照片描述，提取出常见的场景关键词。

照片描述示例（前100张）：
{chr(10).join([p["description"][:50] for p in photos_with_desc[:100]])}

请提取30-50个场景关键词，返回JSON格式：
{{"keywords": ["婴儿车", "公园", "洗澡", "睡觉", "吃饭", "被抱", "熟睡", "侧卧", "仰卧", "趴着", "坐着", "站着", "爬行", "户外", "商场", ...]}}

要求：
1. 关键词要具体，能准确匹配照片内容
2. 包括：场景（如公园、商场）、物品（如婴儿车、奶瓶）、动作（如爬行、睡觉）、姿势（如侧卧、坐着）
3. 关键词要常见，至少能匹配多张照片
4. 不要太泛泛（如"宝宝"、"婴儿"），要具体
5. 返回JSON，不要其他内容"""

            try:
                import requests
                from model_manager import model_manager

                config = model_manager.get_text_config()
                url = config.get("api_url")
                token = config.get("api_token")
                model = "qwen-plus"

                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1000,
                    "temperature": 0.3,
                }

                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }

                response = requests.post(
                    url, json=payload, headers=headers, timeout=300
                )

                if response.status_code != 200:
                    print(f"[SmartCard] LLM调用失败: {response.status_code}")
                    return []

                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    response_text = result["choices"][0]["message"]["content"]
                else:
                    return []

                # 解析JSON
                import re

                data = None
                try:
                    data = json.loads(response_text)
                except:
                    json_match = re.search(r"\{[\s\S]*\}", response_text)
                    if json_match:
                        try:
                            data = json.loads(json_match.group())
                        except:
                            pass

                if not data:
                    print(f"[SmartCard] JSON解析失败")
                    return []

                keywords = data.get("keywords", [])
                print(f"[SmartCard] LLM提取了 {len(keywords)} 个关键词")

                # 过滤掉太泛泛的关键词
                skip_keywords = [
                    "室内",
                    "特写",
                    "睁眼",
                    "闭眼",
                    "宝宝",
                    "婴儿",
                    "儿童",
                    "小孩",
                    "照片",
                    "图片",
                    "镜头",
                    "直视",
                    "注视",
                    "望向",
                    "表情",
                    "神态",
                    "面部",
                    "脸颊",
                    "五官",
                    "头部",
                    "身体",
                    "手",
                    "脚",
                    "衣服",
                    "穿着",
                    "背景",
                    "光线",
                    "拍摄",
                    "画面",
                    "场景",
                    "环境",
                    "木质家具",
                    "地砖",
                    "瓷砖",
                    "走廊",
                    "地板",
                    "墙面",
                    "天花板",
                    "家具",
                    "装饰",
                    "仰卧",
                    "侧卧",
                    "俯卧",
                    "躺着",
                    "趴着",  # 姿势太泛泛
                    "瓷砖墙",
                    "工地",
                    "窗边",
                    "人行道",
                    "床铺",  # 场景太泛泛
                ]

                filtered_keywords = [
                    k for k in keywords if k not in skip_keywords and len(k) >= 2
                ]
                print(
                    f"[SmartCard] 过滤后 {len(filtered_keywords)} 个关键词: {filtered_keywords[:15]}..."
                )

                # 用关键词匹配照片
                all_scenes = []
                for keyword in filtered_keywords:
                    matched_photos = []
                    for p in photos_with_desc:
                        if keyword in p["description"]:
                            matched_photos.append(p)

                    if len(matched_photos) >= 2:
                        # 检查时间跨度
                        dates = [p["date"] for p in matched_photos if p["date"]]
                        if len(set(dates)) >= 2:
                            from datetime import datetime as dt

                            date_objs = []
                            for d in dates:
                                try:
                                    date_objs.append(dt.strptime(d, "%Y-%m-%d"))
                                except:
                                    pass
                            if date_objs:
                                days_diff = (max(date_objs) - min(date_objs)).days
                                if days_diff >= 30:  # 至少1个月
                                    all_scenes.append(
                                        {
                                            "name": keyword,
                                            "photos": matched_photos,
                                            "days_diff": days_diff,
                                        }
                                    )
                                    print(
                                        f"  ✓ {keyword}: {len(matched_photos)}张, {days_diff // 30}个月"
                                    )

                print(f"[SmartCard] 共发现 {len(all_scenes)} 个有效场景")
                return all_scenes

            except Exception as e:
                print(f"[SmartCard] LLM分析失败: {e}")
                return []

        except Exception as e:
            print(f"[SmartCard] 发现相似场景失败: {e}")
            return []
        finally:
            conn.close()

    def find_photos_by_extended_scene(
        self, scene_name: str, limit: int = 20
    ) -> List[Dict]:
        """
        根据扩展场景关键词查找照片

        Args:
            scene_name: 场景名称
            limit: 最大返回数量

        Returns:
            照片列表
        """
        # 合并基础场景和扩展场景
        all_scenes = {**self.SCENE_KEYWORDS, **self.EXTENDED_SCENES}
        keywords = all_scenes.get(scene_name, [])
        if not keywords:
            return []

        return self.find_photos_by_scene_keyword(scene_name, limit)

    def generate_comparison_card(self, baby_name: str = "宝宝") -> Optional[Dict]:
        """
        生成成长对比卡片（同场景不同日期）

        找出相似场景的照片，选择时间跨度最大的两张进行对比

        Args:
            baby_name: 宝宝名字

        Returns:
            卡片数据
        """
        best_comparison = None
        best_months = 0

        # 遍历所有场景关键词
        for scene_name, keywords in self.SCENE_KEYWORDS.items():
            photos = self.find_photos_by_scene_keyword(scene_name, limit=20)

            if len(photos) < 2:
                continue

            # 按日期排序
            dated_photos = []
            for p in photos:
                date_str = p.get("date", "")
                if date_str:
                    try:
                        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                        dated_photos.append((p, date_obj, date_str))
                    except:
                        pass

            if len(dated_photos) < 2:
                continue

            # 按日期排序
            dated_photos.sort(key=lambda x: x[1])

            # 找时间跨度最大的两张
            oldest = dated_photos[0]
            newest = dated_photos[-1]

            days_diff = (newest[1] - oldest[1]).days
            months_diff = days_diff // 30

            # 至少间隔1个月，且选择跨度最大的
            if months_diff >= 1 and months_diff > best_months:
                best_months = months_diff
                best_comparison = {
                    "scene_name": scene_name,
                    "before_photo": oldest[0],
                    "after_photo": newest[0],
                    "before_date": oldest[2],
                    "after_date": newest[2],
                    "months_diff": months_diff,
                }

        if best_comparison:
            # 场景主题配置
            scene_themes = {
                "被抱着": {"emoji": "👨‍👧", "title": "爸爸的肩膀"},
                "吃饭": {"emoji": "🍼", "title": "吃饭时光"},
                "睡觉": {"emoji": "😴", "title": "安睡时刻"},
                "坐着玩": {"emoji": "🧸", "title": "坐着玩耍"},
                "躺着": {"emoji": "🛏️", "title": "躺着时光"},
                "户外探索": {"emoji": "🌳", "title": "户外探索"},
                "婴儿车": {"emoji": "🚗", "title": "推车出行"},
                "玩玩具": {"emoji": "🎮", "title": "玩玩具"},
            }

            theme = scene_themes.get(
                best_comparison["scene_name"],
                {"emoji": "📸", "title": best_comparison["scene_name"]},
            )

            return {
                "type": "comparison_card",
                "scene": best_comparison["scene_name"],
                "before_photo": best_comparison["before_photo"],
                "after_photo": best_comparison["after_photo"],
                "before_date": best_comparison["before_date"],
                "after_date": best_comparison["after_date"],
                "title": f"⏳ {best_comparison['months_diff']}个月的成长",
                "subtitle": f"{baby_name}的{theme['title']}",
                "content": f"从{best_comparison['before_date']}到{best_comparison['after_date']}",
                "footer": "时光飞逝，成长可见",
                "date": datetime.now().strftime("%Y.%m.%d"),
                "months_diff": best_comparison["months_diff"],
            }

        return None

    def generate_milestone_card(self, baby_name: str = "宝宝") -> Optional[Dict]:
        """
        生成特殊里程碑卡片

        包括：
        - 时间里程碑：满月、百天、半岁、一岁等
        - 记录数量里程碑：第100张、第500张照片等
        - 特质发现里程碑：发现10个、50个、100个特质

        Args:
            baby_name: 宝宝名字

        Returns:
            卡片数据
        """
        conn = self._get_db_connection()
        if not conn:
            return None

        try:
            cursor = conn.cursor()

            # 获取宝宝信息
            cursor.execute("SELECT name, birth_date FROM child LIMIT 1")
            child = cursor.fetchone()
            if not child:
                return None

            birth_date = child[1]
            birth = datetime.strptime(birth_date, "%Y-%m-%d")
            today = datetime.now()
            age_days = (today - birth).days

            # 获取照片数量
            cursor.execute(
                "SELECT COUNT(*) FROM events WHERE source_type='PHOTO' AND file_path IS NOT NULL"
            )
            photo_count = cursor.fetchone()[0]

            # 获取标签数量
            cursor.execute("SELECT COUNT(*) FROM portrait_tags WHERE rejected = 0")
            tag_count = cursor.fetchone()[0]

            # 定义所有可能的里程碑
            milestones = []

            # 时间里程碑
            time_milestones = [
                (30, "满月", "🎂"),
                (100, "百天", "百日"),
                (180, "半岁", "半年"),
                (365, "一岁", "🎂"),
                (730, "两岁", "🎂"),
            ]

            for days, name, emoji in time_milestones:
                if age_days >= days:
                    milestones.append(
                        {
                            "type": "time",
                            "name": name,
                            "emoji": emoji,
                            "value": days,
                            "priority": days,  # 优先级用天数
                            "title": f"{emoji} {baby_name}{name}啦！",
                            "subtitle": f"成长{days}天的纪念",
                            "content": f"从出生到现在，{baby_name}已经成长了{days}天",
                        }
                    )

            # 照片数量里程碑
            photo_milestones = [
                (100, "第100张照片", "📸"),
                (500, "第500张照片", "📸"),
                (1000, "第1000张照片", "📸"),
                (2000, "第2000张照片", "📸"),
            ]

            for count, name, emoji in photo_milestones:
                if photo_count >= count:
                    milestones.append(
                        {
                            "type": "photo_count",
                            "name": name,
                            "emoji": emoji,
                            "value": count,
                            "priority": count / 10,  # 优先级
                            "title": f"{emoji} {name}！",
                            "subtitle": f"记录成长的每一刻",
                            "content": f"恭喜！{baby_name}的成长记录已突破{count}张照片",
                        }
                    )

            # 特质发现里程碑
            tag_milestones = [
                (10, "发现10个特质", "🏷️"),
                (50, "发现50个特质", "🏷️"),
                (100, "发现100个特质", "🏷️"),
            ]

            for count, name, emoji in tag_milestones:
                if tag_count >= count:
                    milestones.append(
                        {
                            "type": "tag_count",
                            "name": name,
                            "emoji": emoji,
                            "value": count,
                            "priority": count,  # 优先级
                            "title": f"{emoji} {name}！",
                            "subtitle": f"AI读懂了{baby_name}",
                            "content": f"AI在{photo_count}张照片中发现了{count}个可爱特质",
                        }
                    )

            if not milestones:
                return None

            # 选择优先级最高的里程碑
            milestones.sort(key=lambda x: x["priority"], reverse=True)
            selected = milestones[0]

            return {
                "type": "milestone_card",
                "milestone_type": selected["type"],
                "milestone_name": selected["name"],
                "title": selected["title"],
                "subtitle": selected["subtitle"],
                "content": selected["content"],
                "footer": "每一个里程碑都值得纪念",
                "date": datetime.now().strftime("%Y.%m.%d"),
                "emoji": selected["emoji"],
                "value": selected["value"],
            }

        except Exception as e:
            print(f"[SmartCard] 生成里程碑卡片失败: {e}")
            return None
        finally:
            conn.close()

    def generate_all_milestone_cards(self, baby_name: str = "宝宝") -> List[Dict]:
        """
        生成所有里程碑卡片

        注意：里程碑卡片只在当天显示，不和其他卡片混在一起

        Args:
            baby_name: 宝宝名字

        Returns:
            里程碑卡片列表
        """
        conn = self._get_db_connection()
        if not conn:
            return []

        try:
            cursor = conn.cursor()

            # 获取宝宝信息
            cursor.execute("SELECT name, birth_date FROM child LIMIT 1")
            child = cursor.fetchone()
            if not child:
                return []

            birth_date = child[1]
            birth = datetime.strptime(birth_date, "%Y-%m-%d")
            today = datetime.now()
            today_str = today.strftime("%Y-%m-%d")
            age_days = (today - birth).days

            # 获取照片数量
            cursor.execute(
                "SELECT COUNT(*) FROM events WHERE source_type='PHOTO' AND file_path IS NOT NULL"
            )
            photo_count = cursor.fetchone()[0]

            # 获取标签数量
            cursor.execute("SELECT COUNT(*) FROM portrait_tags WHERE rejected = 0")
            tag_count = cursor.fetchone()[0]

            cards = []

            # ========== 时间里程碑（只在当天显示）==========
            time_milestones = [
                (30, "满月", "🎂"),
                (100, "百天", "🎉"),
                (180, "半岁", "🎈"),
                (365, "一岁", "🎂"),
                (730, "两岁", "🎂"),
            ]

            for days, name, emoji in time_milestones:
                # 只在当天显示
                if age_days == days:
                    cards.append(
                        {
                            "type": "special_milestone_card",
                            "milestone_type": "time",
                            "milestone_name": name,
                            "title": f"{emoji} {baby_name}{name}啦！",
                            "subtitle": f"成长{days}天的纪念",
                            "content": f"从出生到现在，{baby_name}已经成长了{days}天",
                            "footer": "每一个里程碑都值得纪念",
                            "date": today_str,
                            "emoji": emoji,
                            "value": days,
                            "show_date": today_str,  # 只在当天显示
                        }
                    )

            # ========== 节日里程碑（只在当天显示）==========
            # 计算节日
            festivals = self._get_festivals(today, birth)
            for festival in festivals:
                if festival["date"] == today_str:
                    cards.append(
                        {
                            "type": "special_milestone_card",
                            "milestone_type": "festival",
                            "milestone_name": festival["name"],
                            "title": f"{festival['emoji']} {festival['title']}",
                            "subtitle": f"{baby_name}的第{festival['count']}个{festival['name']}",
                            "content": festival.get("content", ""),
                            "footer": "每一个节日都值得纪念",
                            "date": today_str,
                            "emoji": festival["emoji"],
                            "value": festival["count"],
                            "show_date": today_str,
                        }
                    )

            # ========== 照片数量里程碑（暂不自动显示）==========
            # 注：照片数量里程碑不适合自动弹出，因为用户可能一次性导入大量照片
            # 这类里程碑可以在"里程碑墙"页面查看
            # photo_milestones = [
            #     (100, "第100张照片", "📸"),
            #     (500, "第500张照片", "📸"),
            #     (1000, "第1000张照片", "📸"),
            #     (2000, "第2000张照片", "📸"),
            # ]

            # ========== 特质发现里程碑（暂不自动显示）==========
            # 注：特质发现里程碑不适合自动弹出，因为AI分析可能一次性生成大量标签
            # tag_milestones = [
            #     (10, "发现10个特质", "🏷️"),
            #     (50, "发现50个特质", "🏷️"),
            #     (100, "发现100个特质", "🏷️"),
            # ]

            return cards

        except Exception as e:
            print(f"[SmartCard] 生成里程碑卡片失败: {e}")
            return []
        finally:
            conn.close()

    def _get_festivals(self, today: datetime, birth: datetime) -> List[Dict]:
        """
        获取节日信息

        Args:
            today: 今天日期
            birth: 宝宝出生日期

        Returns:
            节日列表
        """
        festivals = []
        today_str = today.strftime("%Y-%m-%d")
        year = today.year

        # 固定日期节日
        fixed_festivals = [
            ("01-01", "元旦", "🎊"),
            ("06-01", "儿童节", "🎈"),
            ("10-01", "国庆节", "🇨🇳"),
        ]

        for date_suffix, name, emoji in fixed_festivals:
            festival_date = f"{year}-{date_suffix}"
            if festival_date == today_str:
                # 计算是第几个节日
                count = year - birth.year
                if birth.strftime("%m-%d") > date_suffix:
                    count -= 1
                count = max(1, count + 1)

                festivals.append(
                    {
                        "name": name,
                        "date": festival_date,
                        "emoji": emoji,
                        "title": f"{name}快乐！",
                        "count": count,
                        "content": f"这是{birth.year}年出生的宝宝度过的第{count}个{name}",
                    }
                )

        # 春节（农历新年，需要计算）
        # 简化处理：使用固定日期近似（实际应该用农历计算）
        # 这里用 2025-01-29, 2026-02-17, 2027-02-06 等近似
        spring_festivals = {
            "2025-01-29": "春节",
            "2026-02-17": "春节",
            "2027-02-06": "春节",
            "2028-01-26": "春节",
        }

        if today_str in spring_festivals:
            # 计算是第几个春节
            count = 1
            for y in range(birth.year, year):
                sf_date = self._get_spring_festival_date(y)
                if sf_date and birth < datetime.strptime(sf_date, "%Y-%m-%d"):
                    pass
                if sf_date and datetime.strptime(sf_date, "%Y-%m-%d") < today:
                    count += 1

            festivals.append(
                {
                    "name": "春节",
                    "date": today_str,
                    "emoji": "🧧",
                    "title": "春节快乐！",
                    "count": count,
                    "content": f"恭喜发财，红包拿来！这是宝宝度过的第{count}个春节",
                }
            )

        # 宝宝生日
        birth_this_year = birth.replace(year=year)
        if birth_this_year.strftime("%Y-%m-%d") == today_str:
            age = year - birth.year
            festivals.append(
                {
                    "name": "生日",
                    "date": today_str,
                    "emoji": "🎂",
                    "title": f"{baby_name}生日快乐！"
                    if hasattr(self, "baby_name")
                    else "生日快乐！",
                    "count": age,
                    "content": f"今天是宝宝的{age}岁生日！",
                }
            )

        return festivals

    def _get_spring_festival_date(self, year: int) -> Optional[str]:
        """获取春节日期（近似）"""
        spring_festivals = {
            2025: "2025-01-29",
            2026: "2026-02-17",
            2027: "2027-02-06",
            2028: "2028-01-26",
            2029: "2029-02-13",
            2030: "2030-02-03",
        }
        return spring_festivals.get(year)

    def generate_smart_card(self, baby_name: str = "宝宝") -> Optional[Dict]:
        """
        智能生成卡片（随机选择类型）

        Args:
            baby_name: 宝宝名字

        Returns:
            卡片数据
        """
        # 获取所有标签
        tags = self.get_tags()

        if not tags:
            return None

        # 随机选择一个标签
        random.shuffle(tags)

        for tag_info in tags[:5]:  # 尝试前5个标签
            tag = tag_info["tag"]
            card = self.generate_tag_card(tag, baby_name)
            if card:
                return card

        # 如果标签卡片都失败，尝试对比卡片
        comparison_card = self.generate_comparison_card(baby_name)
        if comparison_card:
            return comparison_card

        return None


def get_smart_card_generator() -> SmartCardGenerator:
    """获取智能卡片生成器实例"""
    return SmartCardGenerator()


if __name__ == "__main__":
    # 测试
    generator = SmartCardGenerator()

    print("=== 标签列表 ===")
    tags = generator.get_tags()
    print(f"共 {len(tags)} 个标签")
    for t in tags[:10]:
        print(f"  - {t['tag']}: {t['evidence'][:30]}...")

    print("\n=== 查找'爱笑'相关照片 ===")
    photos = generator.find_photos_by_tag("爱笑")
    print(f"找到 {len(photos)} 张照片")
    for p in photos:
        print(f"  - {p['date']}: {p['description'][:40]}...")

    print("\n=== 生成智能卡片 ===")
    card = generator.generate_smart_card("沐沐")
    if card:
        print(f"卡片类型: {card['type']}")
        print(f"标题: {card['title']}")
        print(f"副标题: {card['subtitle']}")
    else:
        print("无法生成卡片")
