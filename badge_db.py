"""
勋章数据库操作函数
"""

from datetime import datetime
from typing import List, Dict, Optional
from models import Badge
from database import user_session, select


def save_badge(
    client_id: str,
    badge_type: str,
    badge_name: str,
    badge_icon: str,
    description: str,
    earned_date: str,
    trigger_date: str,
    trigger_photo: Optional[str] = None,
    level: int = 1,
    count: int = 1
) -> bool:
    """保存勋章"""
    try:
        with user_session(client_id) as session:
            badge = Badge(
                client_id=client_id,
                badge_type=badge_type,
                badge_name=badge_name,
                badge_icon=badge_icon,
                description=description,
                earned_date=earned_date,
                trigger_date=trigger_date,
                trigger_photo=trigger_photo,
                level=level,
                count=count,
            )
            session.add(badge)
        return True
    except Exception as e:
        print(f"[ERROR] 保存勋章失败: {e}")
        return False


def has_badge(client_id: str, badge_type: str) -> bool:
    """检查用户是否已有某勋章"""
    with user_session(client_id) as session:
        badge = session.exec(
            select(Badge).where(
                Badge.client_id == client_id,
                Badge.badge_type == badge_type
            )
        ).first()
        return badge is not None


def get_all_badges(client_id: str) -> List[Dict]:
    """获取用户所有勋章"""
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
    """获取勋章统计信息"""
    badges = get_all_badges(client_id)
    
    stats = {
        "total": len(badges),
        "by_type": {},
        "by_rarity": {},
        "recent": badges[:5]  # 最近5个
    }
    
    for badge in badges:
        badge_type = badge["type"]
        # 这里需要从 badge_config 获取类型和稀有度
        # 暂时简单统计
        pass
    
    return stats


def delete_badge(client_id: str, badge_id: int) -> bool:
    """删除勋章（调试用）"""
    try:
        with user_session(client_id) as session:
            badge = session.exec(
                select(Badge).where(Badge.id == badge_id)
            ).first()
            if badge:
                session.delete(badge)
                return True
        return False
    except Exception as e:
        print(f"[ERROR] 删除勋章失败: {e}")
        return False
