#!/usr/bin/env python3
"""
宝宝成长日志生成器
为每个客户端生成个性化的成长记录
"""

import sys
import os
from pathlib import Path

# 添加当前目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from server_public import get_weather, load_api_config, load_clients
from database import list_news
from datetime import datetime
import requests as req
import uuid


def get_data_dir():
    """获取数据目录（兼容 PyInstaller 和普通运行）"""
    if hasattr(sys, "_MEIPASS"):
        data_dir = Path.home() / "Documents" / "CZRZ" / "data"
    else:
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


def generate_baby_log(city="北京", baby_name="宝宝") -> str:
    """
    生成宝宝成长日志（基础版）
    结合日期、天气、新闻，调用AI生成温馨内容
    """
    return generate_baby_log_enhanced(city, baby_name, True, None, None)


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
        "简练": "用简洁明了的语言，像日常记录，100字左右",
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
    prompt_parts.append("5. 用温暖、充满爱意的语气")
    prompt_parts.append("6. 结尾给宝宝一句祝福")

    prompt = "\n".join(prompt_parts)

    # 5. 调用AI生成
    from model_config import get_text_model_config

    text_config = get_text_model_config()
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
            timeout=600,
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
        resp = req.get(f"wttr.in/{city_encoded}?format=j1", timeout=600)

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


def save_baby_log(client_id: str, baby_name: str, log_content: str):
    """保存宝宝日志到文件（按客户端分离）"""
    today = datetime.now().strftime("%Y-%m-%d")
    log_dir = DATA_DIR / "baby_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 按客户端ID保存
    log_file = log_dir / f"{today}_{client_id}.txt"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(f"# {baby_name}的成长日志\n")
        f.write(f"# 生成时间: {datetime.now().isoformat()}\n")
        f.write("-" * 50 + "\n\n")
        f.write(log_content)

    return str(log_file)


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


def has_today_log(client_id: str) -> bool:
    """检查今天是否已经生成过日志（按客户端）"""
    today = datetime.now().strftime("%Y-%m-%d")
    log_dir = DATA_DIR / "baby_logs"
    log_file = log_dir / f"{today}_{client_id}.txt"
    manual_marker = log_dir / f"{today}_{client_id}.manual"

    # 检查是否有日志文件，或者手动生成的标记
    has_auto = log_file.exists() and log_file.stat().st_size > 10
    has_manual = manual_marker.exists()

    return has_auto or has_manual


def generate_log_for_client(client_id: str, client_info: dict, force: bool = False):
    """为单个客户端生成日志"""
    baby_name = client_info.get("baby_name", "宝宝")
    user_city = client_info.get("user_city")

    # 检查今天是否已生成
    if not force and has_today_log(client_id):
        print(f"  ⏭️ {baby_name}: 今日日志已存在，跳过")
        return True

    # 确定城市：优先使用用户选择的，否则用IP定位
    city = user_city if user_city else "北京"

    print(f"  📝 为 {baby_name} 生成日志（城市: {city}）...")

    try:
        # 尝试获取今日照片（使用AI智能选择和内容分析）
        photo_description = None
        try:
            from photo_manager import PhotoManager
            from select_best_photo import select_best_from_list, analyze_photo_content

            # 获取客户端媒体文件夹
            media_folders = client_info.get("media_folders", [])
            if media_folders:
                pm = PhotoManager(media_folders)
                today = datetime.now().strftime("%Y-%m-%d")
                today_photos = pm.get_photos_by_date(today)

                if today_photos:
                    print(f"    📸 今日共有 {len(today_photos)} 张照片")

                    # 获取照片完整路径
                    photo_paths = [p.get("path") for p in today_photos if p.get("path")]

                    if photo_paths:
                        # 使用AI选择最佳照片
                        print(f"    🤖 AI正在选择最佳照片...")
                        best_photos = select_best_from_list(photo_paths, select_n=1)

                        if best_photos:
                            best_photo_path = best_photos[0]
                            photo_filename = os.path.basename(best_photo_path)
                            print(f"    ✅ 选中照片: {photo_filename}")

                            # 使用AI分析照片内容
                            print(f"    🔍 AI正在分析照片内容...")
                            ai_description = analyze_photo_content(best_photo_path)

                            if ai_description:
                                photo_description = (
                                    f"今天拍了一张照片：{ai_description}"
                                )
                                print(f"    📝 照片描述: {ai_description[:50]}...")
                            else:
                                # AI分析失败，使用基础描述
                                try:
                                    time_part = (
                                        photo_filename.split("_")[1]
                                        if "_" in photo_filename
                                        else ""
                                    )
                                    if len(time_part) >= 4:
                                        photo_time = f"{time_part[:2]}:{time_part[2:4]}"
                                        photo_description = (
                                            f"今天{photo_time}拍了一张可爱的照片"
                                        )
                                    else:
                                        photo_description = "今天拍了一张可爱的照片"
                                except:
                                    photo_description = "今天拍了一张可爱的照片"
                        else:
                            print(f"    ⚠️ 照片选择失败，使用第一张")
                            photo_filename = today_photos[0].get("filename", "")
                            photo_description = "今天拍了一张可爱的照片"
        except Exception as e:
            print(f"    ⚠️ 获取/分析照片失败: {e}")

        # 生成日志（使用增强版）
        log_content = generate_baby_log_enhanced(
            city=city,
            baby_name=baby_name,
            include_news=True,
            user_note=None,
            photo_description=photo_description,
        )

        log_file = save_baby_log(client_id, baby_name, log_content)

        # 添加推送通知
        weather_info = get_weather_sync(city)
        weather_text = f"{city}天气{weather_info['weather']}，" if weather_info else ""
        add_notification(
            client_id=client_id,
            title=f"{baby_name}的今日成长日志已生成",
            message=f"今天是{datetime.now().strftime('%Y年%m月%d日')}，{weather_text}点击查看{baby_name}的今日成长记录！",
            notification_type="log",
        )

        print(f"  ✅ {baby_name}: 日志已保存并推送通知")
        return True
    except Exception as e:
        print(f"  ❌ {baby_name}: 生成失败 - {e}")
        return False


def main(force: bool = False):
    """
    主函数 - 为所有客户端生成个性化日志

    Args:
        force: 是否强制重新生成（即使今天已生成过）
    """
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 开始生成宝宝成长日志...")
    print("=" * 60)

    # 加载所有客户端
    clients = load_clients()

    if not clients:
        print("⚠️ 没有注册的客户端")
        return True

    print(f"📊 共 {len(clients)} 个客户端需要生成日志\n")

    success_count = 0
    skip_count = 0
    fail_count = 0

    for client_id, client_info in clients.items():
        if not client_info.get("enabled", True):
            print(f"  ⏭️ 客户端 {client_id[:8]}...: 已禁用，跳过")
            skip_count += 1
            continue

        result = generate_log_for_client(client_id, client_info, force)
        if result:
            if has_today_log(client_id) and not force:
                skip_count += 1
            else:
                success_count += 1
        else:
            fail_count += 1

    print("\n" + "=" * 60)
    print(f"✅ 成功: {success_count} | ⏭️ 跳过: {skip_count} | ❌ 失败: {fail_count}")
    print("=" * 60 + "\n")

    return fail_count == 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="强制重新生成")
    args = parser.parse_args()

    success = main(force=args.force)
    sys.exit(0 if success else 1)
