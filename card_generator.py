"""
成长卡片生成器 v4
智能卡片：基于AI画像标签生成主题卡片
"""

import random
import json
import os
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import requests

# 统一数据目录
DATA_DIR = Path.home() / "Documents" / "CZRZ"
COLLAGE_DIR = DATA_DIR / "collage"


# ========== 卡片风格 ==========

CARD_STYLES = {
    'warm': {
        'name': '温馨粉',
        'bg_color': '#FFF5F7',
        'accent_color': '#FF8FAB',
        'text_color': '#4A4A4A',
    },
    'fresh': {
        'name': '清新绿',
        'bg_color': '#F0FFF4',
        'accent_color': '#68D391',
        'text_color': '#2D3748',
    },
    'sunny': {
        'name': '阳光橙',
        'bg_color': '#FFFAF0',
        'accent_color': '#F6AD55',
        'text_color': '#744210',
    },
    'dream': {
        'name': '梦幻紫',
        'bg_color': '#FAF5FF',
        'accent_color': '#B794F4',
        'text_color': '#553C9A',
    },
}

# 合图风格
COLLAGE_STYLES = ['grid', 'polaroid', 'film', 'comparison', 'magazine']


def generate_smart_tag_card(
    baby_name: str = "宝宝",
    base_url: str = "http://localhost:3000"
) -> Optional[Dict]:
    """
    生成基于AI画像标签的智能卡片

    Args:
        baby_name: 宝宝名字
        base_url: 基础URL

    Returns:
        卡片数据
    """
    try:
        from smart_card_generator import SmartCardGenerator
        from photo_collage import PhotoCollageGenerator

        generator = SmartCardGenerator()

        # 获取标签和匹配的照片
        tags = generator.get_tags()
        if not tags:
            return None

        # 尝试每个标签，找到有足够照片的
        for tag_info in tags[:10]:
            tag = tag_info["tag"]
            try:
                photos = generator.find_photos_by_tag(tag, limit=4)
            except Exception as e:
                print(f"[Card] 查找照片失败: {e}")
                continue

            if len(photos) >= 2:
                # 生成合图
                photo_paths = [p["path"] for p in photos if Path(p["path"]).exists()]

                if len(photo_paths) >= 2:
                    try:
                        COLLAGE_DIR.mkdir(parents=True, exist_ok=True)

                        collage_gen = PhotoCollageGenerator(output_dir=str(COLLAGE_DIR))
                        collage_style = random.choice(COLLAGE_STYLES)

                        collage_path = collage_gen.generate(
                            photos=photo_paths,
                            style=collage_style,
                            title=f"{baby_name}的{tag}时刻",
                        )

                        if collage_path:
                            # 标签主题配置
                            tag_themes = {
                                "爱笑": {"emoji": "😊", "title": "爱笑的天使"},
                                "活泼好动": {"emoji": "🏃", "title": "活力满满"},
                                "专注力好": {"emoji": "🎯", "title": "专注小达人"},
                                "好奇心强": {"emoji": "🔍", "title": "好奇探索家"},
                                "安静": {"emoji": "😴", "title": "安静小天使"},
                                "运动能力强": {"emoji": "💪", "title": "运动小健将"},
                                "表情丰富": {"emoji": "😊", "title": "表情包达人"},
                                "探索欲强": {"emoji": "🌟", "title": "小小探索家"},
                                "乖巧": {"emoji": "🥰", "title": "乖巧宝贝"},
                                "睡眠安稳": {"emoji": "😴", "title": "安睡小天使"},
                            }

                            theme = tag_themes.get(tag, {"emoji": "✨", "title": f"{tag}的宝宝"})

                            # 获取照片拍摄日期
                            photo_dates = [p.get("date", "") for p in photos if p.get("date")]
                            formatted_date = ""
                            if photo_dates:
                                # 取最新的照片日期
                                latest_date = sorted(photo_dates)[-1]
                                try:
                                    if '-' in latest_date:
                                        date_obj = datetime.strptime(latest_date, '%Y-%m-%d')
                                        formatted_date = f"{str(date_obj.year)[-2:]}.{date_obj.month:02d}.{date_obj.day:02d}"
                                except:
                                    pass

                            return {
                                'type': 'tag_collage_card',
                                'tag': tag,
                                'photo': f"/collage/{Path(collage_path).name}",
                                'title': f"{theme['emoji']} {theme['title']}",
                                'subtitle': f"{baby_name}的{tag}时刻",
                                'content': f"在{len(photos)}个瞬间中发现了这个可爱特质",
                                'footer': '每一个瞬间都闪闪发光',
                                'formatted_date': formatted_date,
                                'photos_count': len(photos),
                                'collage_style': collage_style,
                            }
                    except Exception as e:
                        print(f"[Card] 生成合图失败: {e}")
                        continue

        return None

    except Exception as e:
        print(f"[Card] 生成智能标签卡片失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def generate_growth_comparison_card(
    baby_name: str = "宝宝",
    base_url: str = "http://localhost:3000"
) -> Optional[Dict]:
    """
    生成成长对比卡片（同场景不同时间）

    Args:
        baby_name: 宝宝名字
        base_url: 基础URL

    Returns:
        卡片数据
    """
    try:
        from smart_card_generator import SmartCardGenerator
        from PIL import Image, ImageDraw, ImageFont

        generator = SmartCardGenerator()
        comparison = generator.generate_comparison_card(baby_name)

        if not comparison:
            return None

        before_photo = comparison.get("before_photo", {})
        after_photo = comparison.get("after_photo", {})

        before_path = before_photo.get("path", "")
        after_path = after_photo.get("path", "")

        if not before_path or not after_path:
            return None

        if not Path(before_path).exists() or not Path(after_path).exists():
            return None

        # 生成对比合图
        COLLAGE_DIR.mkdir(parents=True, exist_ok=True)

        # 创建对比图
        try:
            img_before = Image.open(before_path)
            img_after = Image.open(after_path)
        except Exception as e:
            print(f"[Card] 打开图片失败: {e}")
            return None

        # 调整大小
        target_width = 400
        target_height = 500

        def resize_image(img, target_w, target_h):
            img_ratio = img.width / img.height
            target_ratio = target_w / target_h

            if img_ratio > target_ratio:
                new_w = target_w
                new_h = int(target_w / img_ratio)
            else:
                new_h = target_h
                new_w = int(target_h * img_ratio)

            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

            # 创建目标尺寸的画布
            canvas = Image.new('RGB', (target_w, target_h), (255, 255, 255))
            x = (target_w - new_w) // 2
            y = (target_h - new_h) // 2
            canvas.paste(img, (x, y))
            return canvas

        img_before = resize_image(img_before, target_width, target_height)
        img_after = resize_image(img_after, target_width, target_height)

        # 创建对比图（左右布局）
        padding = 20
        total_width = target_width * 2 + padding * 3
        total_height = target_height + padding * 2 + 60

        collage = Image.new('RGB', (total_width, total_height), (245, 245, 245))
        draw = ImageDraw.Draw(collage)

        # 粘贴图片
        collage.paste(img_before, (padding, padding + 40))
        collage.paste(img_after, (target_width + padding * 2, padding + 40))

        # 添加标签
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 18)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)
        except:
            font = ImageFont.load_default()
            small_font = font

        # 标题
        title = comparison.get("title", "成长对比")
        draw.text((total_width // 2, 15), title, fill=(80, 80, 80), font=font, anchor="mt")

        # 往昔/近日标签
        before_label = f"往昔 {comparison.get('before_date', '')}"
        after_label = f"近日 {comparison.get('after_date', '')}"

        draw.text((padding + target_width // 2, target_height + padding + 50), before_label, fill=(120, 120, 120), font=small_font, anchor="mt")
        draw.text((target_width + padding * 2 + target_width // 2, target_height + padding + 50), after_label, fill=(120, 120, 120), font=small_font, anchor="mt")

        # 保存
        import time
        scene_name = comparison.get('scene', 'growth')
        output_path = COLLAGE_DIR / f"comparison_{scene_name}_{int(time.time() * 1000)}.jpg"
        collage.save(output_path, quality=90)

        # 格式化日期为YY.MM.DD格式
        before_date_raw = comparison.get('before_date', '')
        after_date_raw = comparison.get('after_date', '')
        
        before_formatted = before_date_raw
        after_formatted = after_date_raw
        
        # 尝试格式化日期
        try:
            if before_date_raw and '-' in before_date_raw:
                date_obj = datetime.strptime(before_date_raw, '%Y-%m-%d')
                year = str(date_obj.year)[-2:]
                month = f"{date_obj.month:02d}"
                day = f"{date_obj.day:02d}"
                before_formatted = f"{year}.{month}.{day}"
        except:
            pass
            
        try:
            if after_date_raw and '-' in after_date_raw:
                date_obj = datetime.strptime(after_date_raw, '%Y-%m-%d')
                year = str(date_obj.year)[-2:]
                month = f"{date_obj.month:02d}"
                day = f"{date_obj.day:02d}"
                after_formatted = f"{year}.{month}.{day}"
        except:
            pass
        
        return {
            'type': 'comparison_card',
            'scene': comparison.get('scene', ''),
            'photo': f"/collage/{output_path.name}",
            'before_photo': before_photo,
            'after_photo': after_photo,
            'before_date': before_formatted,
            'after_date': after_formatted,
            'title': comparison.get('title', ''),
            'subtitle': comparison.get('subtitle', ''),
            'content': comparison.get('content', ''),
            'footer': '时光飞逝，成长可见',
            'date': datetime.now().strftime('%Y.%m.%d'),
            'months_diff': comparison.get('months_diff', 0),
        }

    except Exception as e:
        print(f"[Card] 生成对比卡片失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_recent_photos(base_url: str = "http://localhost:3000", limit: int = 5) -> List[Dict]:
    """获取最近的照片（从多天收集）"""
    try:
        # 获取有照片的日期
        resp = requests.get(f"{base_url}/api/photos/dates", timeout=120)
        if resp.status_code != 200:
            return []

        dates = resp.json().get('dates', [])
        if not dates:
            return []

        # 从多天收集照片，直到达到limit
        all_photos = []
        for date in dates[:7]:  # 最多查看7天
            resp = requests.get(f"{base_url}/api/photos/by_date/{date}", timeout=120)
            if resp.status_code == 200:
                photos = resp.json().get('photos', [])
                # 只返回图片，不要视频
                image_photos = [p for p in photos if p.get('filename', '').lower().endswith(('.jpg', '.jpeg', '.png'))]
                all_photos.extend(image_photos)
                if len(all_photos) >= limit:
                    break

        return all_photos[:limit]
    except Exception as e:
        print(f"[Card] 获取照片失败: {e}")
        return []


def get_photo_paths(photos: List[Dict], base_url: str = "http://localhost:3000") -> List[str]:
    """获取照片的本地路径（用于合图生成）"""
    paths = []
    for p in photos:
        # 优先使用path字段
        path = p.get('path', '')
        if path and Path(path).exists():
            paths.append(path)
    return paths


def generate_collage_card(
    photos: List[Dict],
    baby_name: str,
    style: str = 'grid',
    title: str = "",
    base_url: str = "http://localhost:3000"
) -> Optional[Dict]:
    """
    生成合图卡片

    Args:
        photos: 照片信息列表
        baby_name: 宝宝名字
        style: 合图风格 (grid, polaroid, film, comparison, magazine)
        title: 标题
        base_url: 基础URL

    Returns:
        卡片数据
    """
    if len(photos) < 2:
        return None

    try:
        from photo_collage import PhotoCollageGenerator

        # 获取本地路径
        photo_paths = get_photo_paths(photos, base_url)
        if len(photo_paths) < 2:
            print("[Card] 没有足够的本地照片路径")
            return None

        # 使用正确的输出目录
        COLLAGE_DIR.mkdir(parents=True, exist_ok=True)

        # 生成合图
        generator = PhotoCollageGenerator(output_dir=str(COLLAGE_DIR))
        collage_path = generator.generate(
            photos=photo_paths,
            style=style,
            title=title or f"{baby_name}的成长瞬间",
        )

        if not collage_path:
            return None

        # 返回卡片数据
        return {
            'type': 'collage_card',
            'collage_style': style,
            'photo': f"/collage/{Path(collage_path).name}",
            'title': title or f"🎨 {baby_name}的成长合辑",
            'subtitle': f"{len(photos)}张照片 · {style}风格",
            'content': f"精选{len(photos)}个美好瞬间",
            'footer': '每一帧都是爱的见证',
            'date': datetime.now().strftime('%Y.%m.%d'),
        }

    except Exception as e:
        print(f"[Card] 生成合图失败: {e}")
        return None


def get_photo_url(photo: Dict, base_url: str = "http://localhost:3000") -> str:
    """获取照片的访问URL"""
    # 优先使用url字段
    url = photo.get('url', '')
    if url:
        return f"{base_url}{url}"
    
    # 否则使用filename
    filename = photo.get('filename', '')
    if filename:
        return f"{base_url}/photo/{filename}"
    
    return ''


def generate_real_card(
    profile: Dict,
    baby_name: str,
    age_text: str,
    style: str = 'warm',
    base_url: str = "http://localhost:3000"
) -> Dict:
    """生成真正有价值的卡片"""
    
    # 获取真实照片
    photos = get_recent_photos(base_url, limit=3)
    photo_urls = [get_photo_url(p, base_url) for p in photos if p.get('filename')]
    
    # 获取真实画像数据
    total_events = profile.get('total_events', 0)
    total_tags = profile.get('total_tags', 0)
    tags = profile.get('tags', [])[:5]
    summary = profile.get('guidance', {}).get('summary', '')
    
    # 提取真实特质
    traits = []
    for t in tags:
        tag_name = t.get('tag', '')
        if tag_name:
            traits.append(tag_name)
    
    # 选择风格
    style_config = CARD_STYLES.get(style, CARD_STYLES['warm'])
    
    # 生成卡片内容
    today = datetime.now()
    date_str = today.strftime('%Y.%m.%d')
    
    # 根据数据生成不同的卡片类型
    card_types = []
    
    # 类型1：今日精选（有照片时）
    if photo_urls:
        # 获取照片拍摄日期
        photo_date = ""
        if photos and len(photos) > 0 and photos[0].get('date'):
            try:
                date_obj = datetime.strptime(photos[0]['date'], '%Y-%m-%d')
                photo_date = f"{str(date_obj.year)[-2:]}.{date_obj.month:02d}.{date_obj.day:02d}"
            except:
                pass
        card_types.append({
            'type': 'photo_card',
            'title': f'📸 {baby_name}的今日瞬间',
            'subtitle': date_str,
            'photo': photo_urls[0],
            'photo_date': photo_date,
            'content': f'记录第{total_events}个美好瞬间',
            'footer': '每一天都值得被记住',
        })
    
    # 类型2：成长画像
    if summary:
        card_types.append({
            'type': 'profile_card',
            'title': f'🌟 AI读懂了{baby_name}',
            'subtitle': '成长画像',
            'content': summary[:80] + '...' if len(summary) > 80 else summary,
            'traits': traits[:3],
            'footer': f'已记录 {total_events} 个瞬间 · 发现 {total_tags} 个特质',
        })
    
    # 类型3：里程碑
    if total_events >= 100:
        milestones = [100, 500, 1000, 2000, 5000]
        for m in milestones:
            if total_events >= m:
                milestone = m
        card_types.append({
            'type': 'milestone_card',
            'title': f'🏆 第{milestone}个瞬间！',
            'subtitle': '成长里程碑',
            'content': f'恭喜！{baby_name}的成长记录已突破{milestone}个瞬间',
            'footer': '每一个瞬间都是爱的见证',
        })
    
    # 类型4：特质卡片
    if traits:
        card_types.append({
            'type': 'trait_card',
            'title': f'✨ {baby_name}的小特质',
            'subtitle': 'AI发现',
            'traits': traits[:4],
            'content': f'在{total_events}个瞬间中，AI发现了这些可爱特质',
            'footer': '每个宝宝都是独一无二的',
        })

    # 类型5：照片合图（有足够照片时）
    if len(photos) >= 3:
        collage_style = random.choice(COLLAGE_STYLES)
        collage_card = generate_collage_card(
            photos=photos,
            baby_name=baby_name,
            style=collage_style,
            base_url=base_url,
        )
        if collage_card:
            card_types.append(collage_card)

    # 类型6：智能标签卡片（基于AI画像标签）
    smart_card = generate_smart_tag_card(baby_name=baby_name, base_url=base_url)
    if smart_card:
        card_types.append(smart_card)

    # 类型7：成长对比卡片（同场景不同时间）
    comparison_card = generate_growth_comparison_card(baby_name=baby_name, base_url=base_url)
    if comparison_card:
        card_types.append(comparison_card)

    # 类型8：特殊里程碑卡片
    try:
        from smart_card_generator import SmartCardGenerator
        milestone_gen = SmartCardGenerator()
        milestone_data = milestone_gen.generate_milestone_card(baby_name)
        if milestone_data:
            card_types.append({
                'type': 'special_milestone_card',
                'title': milestone_data.get('title', ''),
                'subtitle': milestone_data.get('subtitle', ''),
                'content': milestone_data.get('content', ''),
                'footer': milestone_data.get('footer', '每一个里程碑都值得纪念'),
                'date': milestone_data.get('date', date_str),
                'emoji': milestone_data.get('emoji', '🏆'),
                'milestone_name': milestone_data.get('milestone_name', ''),
            })
    except Exception as e:
        print(f"[Card] 生成里程碑卡片失败: {e}")

    # 随机选择一种类型
    if card_types:
        card = random.choice(card_types)
    else:
        # 默认卡片
        card = {
            'type': 'default_card',
            'title': f'💕 {baby_name}的成长',
            'subtitle': date_str,
            'content': f'已记录 {total_events} 个美好瞬间',
            'footer': '继续记录，发现更多',
        }
    
    # 添加通用信息
    card['baby_name'] = baby_name
    card['age_text'] = age_text
    card['style'] = style
    card['style_config'] = style_config
    # 格式化日期为YY.MM.DD格式
    if 'date' not in card or not card['date']:
        card['date'] = date_str
    date_val = card.get('date', '')
    if date_val:
        try:
            if '-' in date_val:
                date_obj = datetime.strptime(date_val, '%Y-%m-%d')
            elif '.' in date_val:
                date_obj = datetime.strptime(date_val, '%Y.%m.%d')
            else:
                date_obj = datetime.strptime(date_val, '%Y%m%d')
            year = str(date_obj.year)[-2:]
            month = f"{date_obj.month:02d}"
            day = f"{date_obj.day:02d}"
            card['formatted_date'] = f"{year}.{month}.{day}"
        except:
            card['formatted_date'] = date_val
    else:
        card['formatted_date'] = ''
    
    card['generated_at'] = datetime.now().isoformat()
    
    # 添加品牌信息
    card['brand'] = {
        'name': '小脚印',
        'slogan': '记录成长，发现美好',
    }
    
    return card


def log_share_event(card_type: str, shared: bool = True):
    """记录分享行为"""
    try:
        log_file = 'data/share_logs.json'
        os.makedirs('data', exist_ok=True)
        
        logs = []
        if os.path.exists(log_file):
            with open(log_file, 'r', encoding='utf-8') as f:
                logs = json.load(f)
        
        logs.append({
            'timestamp': datetime.now().isoformat(),
            'card_type': card_type,
            'shared': shared,
        })
        
        # 只保留最近100条
        logs = logs[-100:]
        
        with open(log_file, 'w', encoding='utf-8') as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Card] 记录分享失败: {e}")


def get_share_stats() -> Dict:
    """获取分享统计"""
    try:
        log_file = 'data/share_logs.json'
        if not os.path.exists(log_file):
            return {'total': 0, 'by_type': {}}
        
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = json.load(f)
        
        stats = {'total': len(logs), 'by_type': {}}
        for log in logs:
            t = log.get('card_type', 'unknown')
            stats['by_type'][t] = stats['by_type'].get(t, 0) + 1
        
        return stats
    except Exception as e:
        return {'total': 0, 'by_type': {}, 'error': str(e)}


# 兼容旧接口
def generate_card_content(
    profile: Dict,
    baby_name: str,
    age_text: str,
    today_photos: List = None,
    style: str = 'warm',
    template_id: str = None,
    base_url: str = "http://localhost:3000"
) -> Dict:
    """兼容旧接口"""
    return generate_real_card(
        profile=profile,
        baby_name=baby_name,
        age_text=age_text,
        style=style,
        base_url=base_url,
    )
