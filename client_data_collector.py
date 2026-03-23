"""
客户端数据收集模块
收集并存储每个宝宝的日志和照片到服务端数据库
"""

from datetime import datetime
from typing import Dict


def save_client_log(
    client_id: str, date: str, content: str, metadata: Dict = None
) -> bool:
    """
    保存客户端日志到数据库

    Args:
        client_id: 客户端ID
        date: 日期 (YYYY-MM-DD)
        content: 日志内容
        metadata: 元数据（城市、天气、照片文件名等）
    """
    try:
        from database import save_log
        from calendar_utils import get_calendar_info

        calendar_info = get_calendar_info(datetime.strptime(date, "%Y-%m-%d").date())

        weather_data = None
        if metadata:
            weather_data = {
                "city": metadata.get("city"),
                "temperature": metadata.get("temperature"),
                "condition": metadata.get("weather"),
            }

        calendar_data = {
            "weekday": calendar_info.get("weekday"),
            "lunar": calendar_info.get("lunar_display"),
        }

        save_log(
            client_id=client_id,
            date_str=date,
            content=content,
            weather=weather_data,
            calendar=calendar_data,
            is_ai_generated=False,
        )

        print(f"[COLLECT] 已保存日志到数据库: {client_id}/{date}")
        return True

    except Exception as e:
        print(f"[ERROR] 保存日志失败 {client_id}/{date}: {e}")
        return False


def save_client_photo_tag(
    client_id: str, filename: str, tag: str, note: str = ""
) -> bool:
    """保存客户端照片标签到数据库"""
    try:
        from database import set_photo_tag

        set_photo_tag(client_id, filename, tag, note)
        print(f"[COLLECT] 已保存标签: {client_id}/{filename} -> {tag}")
        return True

    except Exception as e:
        print(f"[ERROR] 保存标签失败 {client_id}/{filename}: {e}")
        return False
