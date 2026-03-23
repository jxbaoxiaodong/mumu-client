"""
勋章检测器
负责检测和生成各种类型的勋章
"""

import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path

from badge_config import BADGE_CONFIG, get_badge_config
from models import Badge
from database import (
    user_session,
    get_all_photo_tags,
    select,
    Session,
)


# ==================== 工具函数 ====================


def get_all_photo_dates(client_id: str, session: Session) -> List[str]:
    """
    获取用户所有有照片的日期
    
    Args:
        client_id: 客户端ID
        session: 数据库会话
    
    Returns:
        日期列表 (YYYY-MM-DD)
    """
    from models import PhotoTag
    
    tags = session.exec(
        select(PhotoTag).where(PhotoTag.client_id == client_id)
    ).all()
    
    dates = set()
    for tag in tags:
        try:
            # 从文件名解析日期
            date_part = tag.filename.split("_")[0]
            datetime.strptime(date_part, "%Y-%m-%d")
            dates.add(date_part)
        except:
            pass
    
    return sorted(list(dates))


def calculate_streak(dates: List[str]) -> int:
    """
    计算连续天数
    
    Args:
        dates: 日期列表 (已排序)
    
    Returns:
        最大连续天数
    """
    if not dates:
        return 0
    
    max_streak = 1
    current_streak = 1
    
    for i in range(1, len(dates)):
        prev_date = datetime.strptime(dates[i-1], "%Y-%m-%d").date()
        curr_date = datetime.strptime(dates[i], "%Y-%m-%d").date()
        
        if (curr_date - prev_date).days == 1:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 1
    
    return max_streak


def check_streak_badges(client_id: str, session: Session) -> List[str]:
    """
    检测连续上传勋章
    
    Args:
        client_id: 客户端ID
        session: 数据库会话
    
    Returns:
        新获得的勋章类型列表
    """
    # 获取所有有照片的日期
    photo_dates = get_all_photo_dates(client_id, session)
    
    # 计算连续天数
    current_streak = calculate_streak(photo_dates)
    
    # 检查是否达到勋章阈值
    earned_badges = []
    
    for badge_type, config in BADGE_CONFIG.items():
        if config.get("type") == "streak":
            if current_streak >= config["threshold"]:
                # 检查是否已获得该勋章
                existing = session.exec(
                    select(Badge).where(
                        Badge.client_id == client_id,
                        Badge.badge_type == badge_type
                    )
                ).first()
                
                if not existing:
                    earned_badges.append(badge_type)
    
    return earned_badges


def check_achievement_badges(
    client_id: str,
    date: str,
    photo_analysis: Dict,
    session: Session
) -> List[Dict]:
    """
    检测宝宝成就勋章
    
    Args:
        client_id: 客户端ID
        date: 日期 (YYYY-MM-DD)
        photo_analysis: 照片分析结果
        session: 数据库会话
    
    Returns:
        新获得的勋章信息列表
    """
    earned_badges = []
    
    # 合并所有文本内容
    text_content = ""
    trigger_photos = {}
    
    for photo in photo_analysis.get("photos", []):
        desc = photo.get("description", "")
        scene = photo.get("scene", "")
        activity = photo.get("activity", "")
        
        text_content += desc + " " + scene + " " + activity + " "
        
        # 记录每张照片的路径
        photo_path = photo.get("path", "")
        if photo_path:
            trigger_photos[desc] = photo_path
    
    # 检查关键词成就勋章
    for badge_type, config in BADGE_CONFIG.items():
        if config.get("type") == "achievement":
            # 检查是否已获得
            existing = session.exec(
                select(Badge).where(
                    Badge.client_id == client_id,
                    Badge.badge_type == badge_type
                )
            ).first()
            
            if existing:
                continue
            
            # 检查关键词
            keywords = config.get("keywords", [])
            for keyword in keywords:
                if keyword in text_content:
                    # 找到触发的照片
                    trigger_photo = None
                    for desc, path in trigger_photos.items():
                        if keyword in desc:
                            trigger_photo = os.path.basename(path)
                            break
                    
                    earned_badges.append({
                        "badge_type": badge_type,
                        "trigger_photo": trigger_photo
                    })
                    break
    
    # 检查标签成就勋章
    client_tags = get_all_photo_tags(client_id)
    
    for badge_type, config in BADGE_CONFIG.items():
        if config.get("type") == "achievement":
            tag_keywords = config.get("tag_keywords", [])
            if not tag_keywords:
                continue
            
            # 检查是否已获得
            existing = session.exec(
                select(Badge).where(
                    Badge.client_id == client_id,
                    Badge.badge_type == badge_type
                )
            ).first()
            
            if existing:
                continue
            
            # 检查标签
            for filename, tag_info in client_tags.items():
                tag = tag_info.get("tag", "")
                for keyword in tag_keywords:
                    if keyword in tag:
                        earned_badges.append({
                            "badge_type": badge_type,
                            "trigger_photo": filename
                        })
                        break
    
    return earned_badges


def check_count_badges(
    client_id: str,
    date: str,
    photo_count: int,
    session: Session
) -> List[str]:
    """
    检测照片数量勋章
    
    Args:
        client_id: 客户端ID
        date: 日期 (YYYY-MM-DD)
        photo_count: 照片数量
        session: 数据库会话
    
    Returns:
        新获得的勋章类型列表
    """
    earned_badges = []
    
    for badge_type, config in BADGE_CONFIG.items():
        if config.get("type") == "count":
            # 检查是否已获得
            existing = session.exec(
                select(Badge).where(
                    Badge.client_id == client_id,
                    Badge.badge_type == badge_type
                )
            ).first()
            
            if existing:
                continue
            
            if photo_count >= config["threshold"]:
                earned_badges.append(badge_type)
    
    return earned_badges


def check_special_date_badges(
    client_id: str,
    date: str,
    session: Session
) -> List[str]:
    """
    检查特殊日期勋章（生日、节日等）
    
    Args:
        client_id: 客户端ID
        date: 日期 (YYYY-MM-DD)
        session: 数据库会话
    
    Returns:
        新获得的勋章类型列表
    """
    from database import get_client
    
    earned_badges = []
    month_day = date[5:]  # "MM-DD"
    
    # 检查生日
    client_info = get_client(client_id)
    if client_info:
        birthday = client_info.get("baby_birthday")
        if birthday and birthday[5:] == month_day:
            # 检查是否已获得
            existing = session.exec(
                select(Badge).where(
                    Badge.client_id == client_id,
                    Badge.badge_type == "birthday_star"
                )
            ).first()
            
            if not existing:
                earned_badges.append("birthday_star")
    
    # 检查圣诞节
    if month_day == "12-25":
        existing = session.exec(
            select(Badge).where(
                Badge.client_id == client_id,
                Badge.badge_type == "christmas_baby"
            )
        ).first()
        
        if not existing:
            earned_badges.append("christmas_baby")
    
    return earned_badges


# ==================== 勋章保存 ====================


def save_badge(
    client_id: str,
    badge_type: str,
    earned_date: str,
    trigger_date: str,
    trigger_photo: Optional[str] = None
) -> bool:
    """
    保存勋章
    
    Args:
        client_id: 客户端ID
        badge_type: 勋章类型
        earned_date: 获得日期
        trigger_date: 触发日期
        trigger_photo: 触发的照片文件名
    
    Returns:
        是否成功保存
    """
    try:
        config = get_badge_config(badge_type)
        if not config:
            return False
        
        with user_session(client_id) as session:
            badge = Badge(
                client_id=client_id,
                badge_type=badge_type,
                badge_name=config["name"],
                badge_icon=config["icon"],
                description=config["description"],
                earned_date=earned_date,
                trigger_date=trigger_date,
                trigger_photo=trigger_photo,
                level=config.get("level", 1),
                count=1
            )
            session.add(badge)
        
        print(f"[Badge] ✅ {client_id} 获得勋章: {config['name']}")
        return True
    
    except Exception as e:
        print(f"[Badge] ❌ 保存勋章失败: {e}")
        return False


def has_badge(client_id: str, badge_type: str) -> bool:
    """
    检查用户是否已有某勋章
    
    Args:
        client_id: 客户端ID
        badge_type: 勋章类型
    
    Returns:
        是否已有该勋章
    """
    with user_session(client_id) as session:
        badge = session.exec(
            select(Badge).where(
                Badge.client_id == client_id,
                Badge.badge_type == badge_type
            )
        ).first()
        return badge is not None


def get_all_badges(client_id: str) -> List[Dict]:
    """
    获取用户所有勋章
    
    Args:
        client_id: 客户端ID
    
    Returns:
        勋章列表
    """
    with user_session(client_id) as session:
        badges = session.exec(
            select(Badge)
            .where(Badge.client_id == client_id)
            .order_by(Badge.created_at.desc())
        ).all()
        
        return [
            {
                "type": badge.badge_type,
                "name": badge.badge_name,
                "icon": badge.badge_icon,
                "description": badge.description,
                "earned_date": badge.earned_date,
                "trigger_date": badge.trigger_date,
                "trigger_photo": badge.trigger_photo,
                "level": badge.level,
                "count": badge.count,
                "created_at": badge.created_at.isoformat() if badge.created_at else None
            }
            for badge in badges
        ]


def get_badge_stats(client_id: str) -> Dict:
    """
    获取勋章统计信息
    
    Args:
        client_id: 客户端ID
    
    Returns:
        统计信息
    """
    badges = get_all_badges(client_id)
    
    stats = {
        "total": len(badges),
        "by_type": {
            "streak": 0,
            "achievement": 0,
            "count": 0
        },
        "by_rarity": {
            "common": 0,
            "uncommon": 0,
            "rare": 0,
            "epic": 0,
            "legendary": 0
        },
        "recent": badges[:5]  # 最近5个
    }
    
    for badge in badges:
        badge_type = badge["type"]
        config = get_badge_config(badge_type)
        
        badge_category = config.get("type", "achievement")
        stats["by_type"][badge_category] = stats["by_type"].get(badge_category, 0) + 1
        
        rarity = config.get("rarity", "common")
        stats["by_rarity"][rarity] = stats["by_rarity"].get(rarity, 0) + 1
    
    return stats
