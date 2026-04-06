#!/usr/bin/env python3
"""
宝宝成长日志生成器
为每个客户端生成个性化的成长记录
"""

import sys
from pathlib import Path

from database import list_news
from datetime import datetime
import requests as req
import uuid


def get_data_dir():
    """获取数据目录（服务端固定使用项目 data 目录）"""
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# 全局数据目录
DATA_DIR = get_data_dir()


def get_date_info():
    """获取日期信息（节日、星期几等）"""
    now = datetime.now()

    # 基础日期信息
    date_str = now.strftime("%Y年%m月%d日")
    weekday_names = [
        "星期一",
        "星期二",
        "星期三",
        "星期四",
        "星期五",
        "星期六",
        "星期日",
    ]
    weekday = weekday_names[now.weekday()]

    # 简单节日判断（可扩展）
    month_day = now.strftime("%m-%d")
    festivals = {
        "01-01": "元旦",
        "02-14": "情人节",
        "03-08": "妇女节",
        "05-01": "劳动节",
        "06-01": "儿童节",
        "10-01": "国庆节",
        "12-25": "圣诞节",
    }
    festival = festivals.get(month_day, None)

    return {
        "date": date_str,
        "weekday": weekday,
        "festival": festival,
        "month_day": month_day,
    }


def select_warm_news(news_list, count=2):
    """从新闻池中选出最温馨的新闻"""
    if not news_list:
        return []

    # 优先选择：动物、科技、体育、生活类
    preferred_categories = ["动物", "科技", "体育", "生活", "健康", "教育"]

    warm_news = []
    for news in news_list:
        cat = news.get("category", "其他")
        if cat in preferred_categories:
            warm_news.append(news)

    # 如果没有首选类别，返回前几条
    if not warm_news and news_list:
        warm_news = news_list[:count]

    return warm_news[:count]


def generate_baby_log_enhanced(
    city: str = "北京",
    baby_name: str = "宝宝",
    include_news: bool = True,
    user_note: str = None,
    photo_description: str = None,
    log_style: str = "简练",
) -> str:
    """
    生成宝宝成长日志（增强版）
    支持照片描述和用户补充

    Args:
        city: 城市名称
        baby_name: 宝宝名字
        include_news: 是否包含新闻
        user_note: 用户补充记录
        photo_description: 照片描述（AI分析或EXIF信息）
        log_style: 日志风格（简练/诗意/东北腔/详细/童趣/深情/IT男风格）
    """
    style_instructions = {
        "简练": "用温馨平淡、像真人随手记下生活的口吻来写，克制自然，不堆砌辞藻，少用感叹词和语气词，120字左右",
        "诗意": "用文艺清新的语言，可以加入诗意的比喻，150字左右",
        "东北腔": "用东北话的口吻写，幽默风趣，可以说'这孩子咋这么招人稀罕'、'杠杠的'等，100字左右",
        "详细": "详细记录当天的活动细节，时间、地点、做了什么，200字左右",
        "童趣": "用轻松可爱的语气，像给宝宝讲故事，150字左右",
        "深情": "用温暖感人的语言，表达父母对宝宝的爱，150字左右",
        "IT男风格": "用程序员风格写，可以用代码注释、技术术语，如'// 今日活动：户外探索 v1.0'，100字左右",
    }
    style_instruction = style_instructions.get(log_style, style_instructions["简练"])
    # 1. 获取日期信息
    date_info = get_date_info()

    # 2. 获取天气
    weather_data = get_weather_sync(city)
    if weather_data:
        weather_desc = weather_data.get("weather")
        temp_num = weather_data.get("temperature")
        temp = f"{temp_num}°C" if isinstance(temp_num, (int, float)) else temp_num
    else:
        weather_desc = None
        temp = None

    # 3. 获取新闻
    news_text = ""
    if include_news:
        all_news = list_news(limit=50)
        today = datetime.now().strftime("%Y-%m-%d")
        today_news = [n for n in all_news if n.get("date") == today]

        if not today_news:
            today_news = all_news[:5]

        warm_news = select_warm_news(today_news, 2)

        news_descriptions = []
        for news in warm_news:
            title = news.get("title", "")[:30]
            cat = news.get("category", "")
            news_descriptions.append(f"[{cat}]{title}")

        news_text = (
            "；".join(news_descriptions)
            if news_descriptions
            else "今天没有什么特别的新闻"
        )

    # 4. 构建AI提示词
    prompt_parts = [
        f"今天是{date_info['date']}，{date_info['weekday']}",
        f"地点：{city}",
    ]

    if weather_desc and temp:
        prompt_parts.append(f"天气：{weather_desc}，气温{temp}度")

    if photo_description:
        prompt_parts.append(f"\n今天拍了一张照片：{photo_description}")

    if user_note:
        prompt_parts.append(f"\n家长记录：{user_note}")

    if include_news and news_text:
        prompt_parts.append(f"\n今日热点：{news_text}")

    prompt_parts.append(f"\n请为{baby_name}写一篇成长日记。")
    prompt_parts.append(f"\n风格要求：{style_instruction}")
    prompt_parts.append("要求：")
    if weather_desc and temp:
        prompt_parts.append("1. 结合天气和季节特点")
    if photo_description:
        prompt_parts.append("2. 结合照片内容，描述宝宝的可爱瞬间")
    if user_note:
        prompt_parts.append("3. 融入家长的记录")
    if include_news:
        prompt_parts.append("4. 可以简单提及一件温暖的新闻")
    prompt_parts.append("5. 语气温和、平实，像家长晚上写下的一段真实日常")
    prompt_parts.append("6. 少用或不用感叹词、语气助词、夸张赞美")
    prompt_parts.append("7. 不要写成模板化鸡汤，也不要像作文或营销文案")
    prompt_parts.append("8. 结尾自然收住，不必强行升华或刻意祝福")

    prompt = "\n".join(prompt_parts)

    # 5. 调用AI生成
    from model_manager import model_manager

    text_config = model_manager.get_text_config()
    ai_token = text_config.get("api_token", "")
    ai_url = text_config.get("api_url", "")
    ai_model = text_config.get("model_name", "")

    if not ai_token:
        # 无AI时返回模板日志
        date_desc = f"{date_info['date']}，{date_info['weekday']}"
        return f"今天是{date_desc}，{city}{weather_desc}，气温{temp}。{baby_name}今天也要开开心心长大哦！💕"

    try:
        resp = req.post(
            ai_url,
            headers={"Authorization": f"Bearer {ai_token}"},
            json={
                "model": ai_model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.8,
                "max_tokens": 500,
            },
            timeout=1200,
        )

        if resp.status_code == 200:
            result = resp.json()
            log_content = (
                result.get("choices", [{}])[0].get("message", {}).get("content", "")
            )
            return log_content.strip()
    except Exception as e:
        print(f"[WARN] AI生成日志失败: {e}")

    # 失败时返回简单模板
    date_desc = f"{date_info['date']}，{date_info['weekday']}"
    return f"今天是{date_desc}，{city}{weather_desc}，气温{temp}。{baby_name}今天也要开开心心长大哦！💕"


def get_weather_sync(city="北京"):
    """同步获取天气（用于非异步环境）"""
    import urllib.parse

    try:
        city_encoded = urllib.parse.quote(city, safe="")
        resp = req.get(f"wttr.in/{city_encoded}?format=j1", timeout=1200)

        if resp.status_code == 200:
            data = resp.json()
            current = data.get("current_condition", [{}])[0]

            weather_code = current.get("weatherCode", "113")
            weather_desc_map = {
                "113": "晴",
                "116": "多云",
                "119": "阴",
                "122": "多云",
                "143": "雾",
                "176": "小雨",
                "179": "小雪",
                "182": "雨夹雪",
                "185": "冻雾",
                "200": "雷雨",
                "227": "大雪",
                "230": "暴雪",
            }

            return {
                "city": city,
                "temperature": current.get("temp_C", "20"),
                "weather": weather_desc_map.get(weather_code, "晴"),
                "humidity": current.get("humidity", "50"),
            }
    except Exception as e:
        print(f"[WARN] 天气获取失败: {e}")
        return None

    return {
        "city": city,
        "temperature": current.get("temp_C", "20"),
        "weather": weather_desc_map.get(weather_code, "晴"),
        "humidity": current.get("humidity", "50"),
    }


def add_notification(
    client_id: str, title: str, message: str, notification_type: str = "info"
):
    """为客户端添加通知"""
    import json

    # 加载通知文件
    notifications_file = DATA_DIR / "notifications.json"

    if notifications_file.exists():
        with open(notifications_file, "r", encoding="utf-8") as f:
            all_notifications = json.load(f)
    else:
        all_notifications = {}

    if client_id not in all_notifications:
        all_notifications[client_id] = []

    # 添加新通知
    notification = {
        "id": str(uuid.uuid4()),
        "title": title,
        "message": message,
        "type": notification_type,
        "created_at": datetime.now().isoformat(),
        "read": False,
    }
    all_notifications[client_id].append(notification)

    # 只保留最近20条通知
    all_notifications[client_id] = all_notifications[client_id][-20:]

    # 保存
    with open(notifications_file, "w", encoding="utf-8") as f:
        json.dump(all_notifications, f, indent=2, ensure_ascii=False)

    return notification
