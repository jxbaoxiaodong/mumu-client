"""
勋章配置模块
定义所有勋章的类型、规则和触发条件
"""

from typing import Dict, List


# 勋章配置
BADGE_CONFIG: Dict[str, Dict] = {
    # ==================== 连续上传勋章 ====================
    "bronze_care": {
        "name": "青铜关爱奖",
        "icon": "🥉",
        "description": "连续上传照片 7 天",
        "type": "streak",
        "threshold": 7,
        "level": 1,
        "rarity": "common"
    },
    "silver_care": {
        "name": "白银关爱奖",
        "icon": "🥈",
        "description": "连续上传照片 30 天",
        "type": "streak",
        "threshold": 30,
        "level": 2,
        "rarity": "uncommon"
    },
    "gold_care": {
        "name": "黄金关爱奖",
        "icon": "🥇",
        "description": "连续上传照片 100 天",
        "type": "streak",
        "threshold": 100,
        "level": 3,
        "rarity": "rare"
    },
    "diamond_care": {
        "name": "钻石关爱奖",
        "icon": "💎",
        "description": "连续上传照片 365 天",
        "type": "streak",
        "threshold": 365,
        "level": 4,
        "rarity": "epic"
    },
    
    # ==================== 宝宝成就勋章 ====================
    "dog_lover": {
        "name": "摸狗狗成就",
        "icon": "🐶",
        "description": "照片中出现可爱的狗狗",
        "type": "achievement",
        "keywords": ["狗", "狗狗", "puppy", "dog", "汪汪", "宠物狗"],
        "rarity": "common"
    },
    "cat_lover": {
        "name": "摸猫咪成就",
        "icon": "🐱",
        "description": "照片中出现可爱的猫咪",
        "type": "achievement",
        "keywords": ["猫", "猫咪", "kitty", "cat", "喵喵", "宠物猫"],
        "rarity": "common"
    },
    "climber": {
        "name": "爬梯子成就",
        "icon": "🧗",
        "description": "照片中出现梯子或攀爬动作",
        "type": "achievement",
        "keywords": ["梯子", "攀爬", "爬", "攀登", "climb", "ladder"],
        "rarity": "uncommon"
    },
    "swimmer": {
        "name": "游泳成就",
        "icon": "🏊",
        "description": "照片中出现游泳池或水",
        "type": "achievement",
        "keywords": ["游泳", "水", "泳池", "游泳池", "swim", "water", "戏水"],
        "rarity": "uncommon"
    },
    "birthday_star": {
        "name": "生日之星",
        "icon": "🎂",
        "description": "宝宝生日当天的照片",
        "type": "achievement",
        "rarity": "rare"
    },
    "christmas_baby": {
        "name": "圣诞宝宝",
        "icon": "🎄",
        "description": "圣诞节当天的照片",
        "type": "achievement",
        "rarity": "rare"
    },
    "first_stand": {
        "name": "第一次站立",
        "icon": "👶",
        "description": "宝宝第一次站立的珍贵时刻",
        "type": "achievement",
        "tag_keywords": ["第一次站立", "首次站立"],
        "rarity": "epic"
    },
    "first_walk": {
        "name": "第一次走路",
        "icon": "🚶",
        "description": "宝宝第一次走路的珍贵时刻",
        "type": "achievement",
        "tag_keywords": ["第一次走路", "首次走路", "学步"],
        "rarity": "epic"
    },
    "first_mom": {
        "name": "第一次叫妈妈",
        "icon": "👋",
        "description": "宝宝第一次叫妈妈的珍贵时刻",
        "type": "achievement",
        "tag_keywords": ["第一次叫妈妈", "首次叫妈妈"],
        "rarity": "epic"
    },
    
    # ==================== 照片数量勋章 ====================
    "photo_lover": {
        "name": "摄影爱好者",
        "icon": "📸",
        "description": "单日上传 10 张照片",
        "type": "count",
        "threshold": 10,
        "rarity": "common"
    },
    "photo_master": {
        "name": "摄影达人",
        "icon": "📷",
        "description": "单日上传 50 张照片",
        "type": "count",
        "threshold": 50,
        "rarity": "uncommon"
    },
    "photo_expert": {
        "name": "摄影大师",
        "icon": "📹",
        "description": "单日上传 100 张照片",
        "type": "count",
        "threshold": 100,
        "rarity": "rare"
    },
    
    # ==================== 特殊场景勋章 ====================
    "outdoor_explorer": {
        "name": "户外探险家",
        "icon": "🌞",
        "description": "户外场景照片",
        "type": "achievement",
        "keywords": ["户外", "公园", "草地", "阳光", "outdoor", "park"],
        "rarity": "common"
    },
    "home_time": {
        "name": "家庭时光",
        "icon": "🏠",
        "description": "室内温馨场景照片",
        "type": "achievement",
        "keywords": ["室内", "家里", "客厅", "卧室", "home", "indoor"],
        "rarity": "common"
    },
    "foodie_baby": {
        "name": "美食家",
        "icon": "🍽️",
        "description": "吃饭场景照片",
        "type": "achievement",
        "keywords": ["吃饭", "吃饭饭", "美食", "food", "eating", "meal"],
        "rarity": "common"
    },
    "sleepy_baby": {
        "name": "小睡虫",
        "icon": "😴",
        "description": "睡觉场景照片",
        "type": "achievement",
        "keywords": ["睡觉", "睡觉觉", "午睡", "sleep", "nap", "bed"],
        "rarity": "common"
    },
    "playful_baby": {
        "name": "玩耍达人",
        "icon": "🎪",
        "description": "玩耍场景照片",
        "type": "achievement",
        "keywords": ["玩耍", "玩", "游戏", "play", "fun", "toy"],
        "rarity": "common"
    },
    "bath_time": {
        "name": "洗澡时间",
        "icon": "🛁",
        "description": "洗澡场景照片",
        "type": "achievement",
        "keywords": ["洗澡", "沐浴", "bath", "shower", "water"],
        "rarity": "uncommon"
    },
    "reading_time": {
        "name": "阅读时光",
        "icon": "📚",
        "description": "阅读场景照片",
        "type": "achievement",
        "keywords": ["看书", "阅读", "读书", "read", "book", "story"],
        "rarity": "uncommon"
    },
    "music_lover": {
        "name": "音乐爱好者",
        "icon": "🎵",
        "description": "音乐场景照片",
        "type": "achievement",
        "keywords": ["音乐", "唱歌", "music", "sing", "song", "dance"],
        "rarity": "uncommon"
    },
}


def get_badge_config(badge_type: str) -> Dict:
    """获取勋章配置"""
    return BADGE_CONFIG.get(badge_type, {})


def get_all_badge_types() -> List[str]:
    """获取所有勋章类型"""
    return list(BADGE_CONFIG.keys())


def get_badges_by_type(badge_category: str) -> List[Dict]:
    """
    获取指定类型的勋章列表
    
    Args:
        badge_category: 勋章类型 (streak, achievement, count)
    
    Returns:
        勋章配置列表
    """
    return [
        {"type": badge_type, **config}
        for badge_type, config in BADGE_CONFIG.items()
        if config.get("type") == badge_category
    ]


def get_badge_rarity_color(rarity: str) -> str:
    """获取勋章稀有度颜色"""
    colors = {
        "common": "#999999",      # 灰色
        "uncommon": "#1E90FF",    # 蓝色
        "rare": "#9370DB",        # 紫色
        "epic": "#FF8C00",        # 橙色
        "legendary": "#FFD700"    # 金色
    }
    return colors.get(rarity, "#999999")
