"""
照片标签管理模块
"""

from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path


def get_photo_tag(client_id: str, filename: str) -> Optional[Dict]:
    """获取单张照片的标签"""
    from database import get_photo_tag as db_get_photo_tag

    return db_get_photo_tag(client_id, filename)


def set_photo_tag(client_id: str, filename: str, tag: str, note: str = "") -> bool:
    """设置照片标签"""
    from database import set_photo_tag as db_set_photo_tag

    return db_set_photo_tag(client_id, filename, tag, note)


def delete_photo_tag(client_id: str, filename: str) -> bool:
    """删除照片标签"""
    from database import delete_photo_tag as db_delete_photo_tag

    return db_delete_photo_tag(client_id, filename)


def get_all_photo_tags(client_id: str) -> Dict[str, Dict]:
    """获取所有照片标签"""
    from database import get_all_photo_tags as db_get_all_photo_tags

    return db_get_all_photo_tags(client_id)


def get_all_tag_names(client_id: str) -> List[str]:
    """获取所有标签名称（去重）"""
    from database import get_all_tag_names as db_get_all_tag_names

    return db_get_all_tag_names(client_id)


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp"}


def is_video_file(filename: str) -> bool:
    """判断是否是视频文件"""
    return Path(filename).suffix.lower() in VIDEO_EXTENSIONS


def get_all_tagged_photos(client_id: str, media_folders: List[str]) -> List[Dict]:
    """
    获取所有带标签的照片和视频（通过索引查找）
    """
    from photo_manager import PhotoManager
    from pathlib import Path

    client_tags = get_all_photo_tags(client_id)

    if not client_tags:
        return []

    all_photos = []
    pm = PhotoManager(media_folders, Path(__file__).parent / "data")

    for filename, tag_info in client_tags.items():
        entry = pm.get_photo_by_filename(filename)

        if entry:
            photo_date = entry.get("date", "")
            if not photo_date:
                try:
                    date_part = filename.split("_")[0]
                    datetime.strptime(date_part, "%Y-%m-%d")
                    photo_date = date_part
                except:
                    photo_date = datetime.now().strftime("%Y-%m-%d")
        else:
            try:
                date_part = filename.split("_")[0]
                datetime.strptime(date_part, "%Y-%m-%d")
                photo_date = date_part
            except:
                photo_date = datetime.now().strftime("%Y-%m-%d")

        is_video = is_video_file(filename)

        all_photos.append(
            {
                "filename": filename,
                "url": f"/photo/{filename}",
                "thumbnail_url": f"/video/thumb/{filename}"
                if is_video
                else f"/photo/thumb/{filename}",
                "date": photo_date,
                "tag": tag_info.get("tag", ""),
                "note": tag_info.get("note", ""),
                "updated_at": tag_info.get("updated_at", ""),
                "is_video": is_video,
            }
        )

    all_photos.sort(key=lambda x: x["date"], reverse=True)

    return all_photos


if __name__ == "__main__":
    client_id = "test_client"

    set_photo_tag(
        client_id, "2024-03-07_143022.jpg", "第一次站立", "宝宝自己站起来了！"
    )

    tag = get_photo_tag(client_id, "2024-03-07_143022.jpg")
    print(f"标签: {tag}")

    all_tags = get_all_tag_names(client_id)
    print(f"所有标签: {all_tags}")
