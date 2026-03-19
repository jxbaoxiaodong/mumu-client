"""
AI主题生成器 - 根据用户描述AI生成自定义主题
使用Qwen-VL或其他AI模型生成主题配置
"""

import json
import os
import sys
import hashlib
import hmac
import time
import requests
from pathlib import Path


def create_signature(secret_key, method, path, timestamp, body=""):
    """创建 HMAC 签名"""
    message = f"{timestamp}{method.upper()}{path}{body}"
    signature = hmac.new(
        secret_key.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return signature


def add_signature_headers(headers, client_id, secret_key, method, path, body=""):
    """添加签名相关请求头"""
    timestamp = int(time.time())
    signature = create_signature(secret_key, method, path, timestamp, body)
    headers["X-Client-ID"] = client_id
    headers["X-Timestamp"] = str(timestamp)
    headers["X-Signature"] = signature
    return headers


def get_data_dir():
    """获取数据目录（兼容 PyInstaller 和普通运行）"""
    if hasattr(sys, "_MEIPASS"):
        data_dir = Path.home() / "Documents" / "CZRZ" / "data"
    else:
        data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_client_config() -> dict:
    """获取客户端配置（server_url, client_id, secret_key）"""
    try:
        config_file = Path.home() / "Documents" / "CZRZ" / "config.json"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                config = json.load(f)
            return {
                "server_url": config.get("server_url", ""),
                "client_id": config.get("client_id", ""),
                "secret_key": config.get("secret_key", ""),
            }
    except Exception:
        pass
    return {"server_url": "", "client_id": "", "secret_key": ""}


# 主题存储目录
THEMES_DIR = get_data_dir() / "themes"
THEMES_DIR.mkdir(parents=True, exist_ok=True)

# CSS变量模板 - 包含可调整的组件样式
CSS_TEMPLATE = """:root {{
  /* 主色调 */
  --primary-color: {primary_color};
  --primary-light: {primary_light};
  --primary-dark: {primary_dark};
  --secondary-color: {secondary_color};
  --accent-color: {accent_color};
  
  /* 页面背景 */
  --bg-color: {bg_color};
  --bg-gradient: {bg_gradient};
  
  /* 通用卡片背景 */
  --card-bg: {card_bg};
  
  /* 文字 */
  --text-primary: {text_primary};
  --text-secondary: {text_secondary};
  --text-light: {text_light};
  
  /* 装饰 */
  --border-radius: {border_radius};
  --card-shadow: {card_shadow};
  --hover-transform: {hover_transform};
  --transition-speed: {transition_speed};
  
  /* 字体 */
  --font-title: {font_title};
  --font-body: {font_body};
  
  /* 照片框样式 - 可由AI调整 */
  --photo-frame-bg: {photo_frame_bg};
  --photo-frame-border: {photo_frame_border};
  --photo-frame-shadow: {photo_frame_shadow};
  --photo-frame-radius: {photo_frame_radius};
  
  /* 日志框样式 - 可由AI调整 */
  --log-card-bg: {log_card_bg};
  --log-card-border: {log_card_border};
  --log-card-shadow: {log_card_shadow};
  --log-card-radius: {log_card_radius};
  
  /* 日志水印 - 可由AI设置 */
  --log-watermark-content: {log_watermark_content};
  --log-watermark-opacity: {log_watermark_opacity};
  --log-watermark-size: {log_watermark_size};
  
  /* 相册项样式 */
  --gallery-item-bg: {gallery_item_bg};
  --gallery-item-border: {gallery_item_border};
  --gallery-item-radius: {gallery_item_radius};
}}

/* 照片框样式 - 带装饰定位 */
.photo-frame-container {{
  background: var(--photo-frame-bg, var(--card-bg));
  border: var(--photo-frame-border, none);
  border-radius: var(--photo-frame-radius, var(--border-radius));
  box-shadow: var(--photo-frame-shadow, var(--card-shadow));
  transition: all var(--transition-speed);
  position: relative !important;
}}
.photo-frame-container:hover {{
  transform: var(--hover-transform);
}}

/* 日志框样式 - 带装饰定位 */
.log-card, .journal-card, .today-log-card {{
  background: var(--log-card-bg, var(--card-bg)) !important;
  border: var(--log-card-border, none) !important;
  border-radius: var(--log-card-radius, var(--border-radius)) !important;
  box-shadow: var(--log-card-shadow, var(--card-shadow)) !important;
  transition: all var(--transition-speed);
  position: relative !important;
  overflow: hidden !important;
}}

/* 日志水印 - 淡淡的背景装饰 */
.log-card::after, .journal-card::after, .today-log-card::after {{
  content: var(--log-watermark-content, '');
  position: absolute;
  bottom: 10px;
  right: 10px;
  font-size: var(--log-watermark-size, 60px);
  opacity: var(--log-watermark-opacity, 0.08);
  pointer-events: none;
  z-index: 1;
  transform: rotate(-15deg);
}}

/* 日志内容区域提高层级，确保文字在水印之上 */
.log-card .card-body, .journal-card .card-body, .today-log-card .card-body,
.log-card .card-content, .journal-card .card-content, .today-log-card .card-content {{
  position: relative;
  z-index: 2;
}}

/* 备选：使用CSS图案作为日志水印背景 */
.log-card.watermark-pattern, .journal-card.watermark-pattern {{
  background-image: 
    radial-gradient(circle at 20% 80%, rgba(0,0,0,0.03) 0%, transparent 50%),
    radial-gradient(circle at 80% 20%, rgba(0,0,0,0.03) 0%, transparent 50%);
}}

/* 相册网格项 - 带装饰定位 */
.gallery-item-uni {{
  background: var(--gallery-item-bg, transparent);
  border: var(--gallery-item-border, none);
  border-radius: var(--gallery-item-radius, 12px);
  overflow: hidden;
  transition: all var(--transition-speed);
  position: relative;
}}
.gallery-item-uni:hover {{
  transform: scale(1.05);
}}

/* 全局背景应用 */
body, .page-background {{
  background: var(--bg-gradient);
  background-attachment: fixed;
}}

/* 所有卡片继承变量 */
.card, .uni-card {{
  background: var(--card-bg);
  border-radius: var(--border-radius);
  box-shadow: var(--card-shadow);
}}"""


def get_theme_file(client_id: str) -> Path:
    """获取主题文件路径"""
    return THEMES_DIR / f"{client_id}.json"


def load_theme(client_id: str) -> dict:
    """加载客户端主题配置"""
    theme_file = get_theme_file(client_id)
    if theme_file.exists():
        with open(theme_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_theme(client_id: str, theme: dict):
    """保存客户端主题配置"""
    theme_file = get_theme_file(client_id)
    with open(theme_file, "w", encoding="utf-8") as f:
        json.dump(theme, f, ensure_ascii=False, indent=2)


def generate_css(theme: dict) -> str:
    """根据主题配置生成CSS"""
    variables = theme.get("variables", {})

    # 确保所有必需的变量都有默认值
    default_vars = {
        "photo_frame_bg": "var(--card-bg)",
        "photo_frame_border": "none",
        "photo_frame_shadow": "var(--card-shadow)",
        "photo_frame_radius": "var(--border-radius)",
        "log_card_bg": "var(--card-bg)",
        "log_card_border": "none",
        "log_card_shadow": "var(--card-shadow)",
        "log_card_radius": "var(--border-radius)",
        "gallery_item_bg": "transparent",
        "gallery_item_border": "none",
        "gallery_item_radius": "12px",
        "log_watermark_content": "''",
        "log_watermark_opacity": "0.08",
        "log_watermark_size": "60px",
    }

    # 合并默认值和用户定义的值
    for key, default_value in default_vars.items():
        if key not in variables or not variables[key]:
            variables[key] = default_value

    # 填充CSS模板
    css = CSS_TEMPLATE.format(**variables)

    # 添加自定义CSS
    custom_css = theme.get("custom_css", "")
    if custom_css:
        css += f"\n/* 自定义样式 */\n{custom_css}"

    return css


def generate_theme_by_ai(baby_name: str, user_prompt: str, api_key: str = None) -> dict:
    """
    使用AI根据用户描述生成主题
    返回主题配置字典
    """
    client_config = get_client_config()
    server_url = client_config.get("server_url", "")
    client_id = client_config.get("client_id", "")
    secret_key = client_config.get("secret_key", "")

    if not server_url or not client_id:
        return generate_theme_locally(baby_name, user_prompt)

    system_prompt = """你是一个专业的网页主题设计师。根据用户的描述，生成一个完整的主题配置。

你需要返回一个JSON对象，包含以下字段：
{
  "name": "主题名称（2-4个字）",
  "description": "主题简短描述",
  "decoration": "主题emoji图标（1-2个）",
  "variables": {
    "primary_color": "主色调HEX色值",
    "primary_light": "主色调浅色HEX色值", 
    "primary_dark": "主色调深色HEX色值",
    "secondary_color": "辅助色HEX色值",
    "accent_color": "强调色HEX色值",
    "bg_color": "背景色HEX色值",
    "bg_gradient": "背景渐变CSS",
    "card_bg": "卡片背景色HEX",
    "text_primary": "主要文字颜色HEX",
    "text_secondary": "次要文字颜色HEX",
    "text_light": "浅色文字HEX",
    "border_radius": "圆角大小（如16px, 20px, 24px）",
    "card_shadow": "卡片阴影CSS",
    "hover_transform": "悬停变换（如translateY(-5px)）",
    "transition_speed": "过渡速度（如0.3s）",
    "font_title": "标题字体",
    "font_body": "正文字体"
  },
  "custom_css": "自定义CSS样式（可选，用于特殊效果）"
}

设计原则：
1. 确保颜色搭配协调，文字颜色与背景有足够对比度
2. 根据用户描述的风格选择合适的配色方案
3. 圆角大小根据风格调整（可爱风可以大一些，简约风小一些）
4. emoji图标要符合主题风格

只返回JSON，不要其他说明。"""

    user_request = f"为宝宝'{baby_name}'的成长记录系统设计主题：{user_prompt}"

    try:
        request_body = {
            "client_id": client_id,
            "prompt": user_request,
            "system_prompt": system_prompt,
            "max_tokens": 1500,
            "operation": "generate_theme",
        }
        body_str = json.dumps(request_body)
        headers = {"Content-Type": "application/json", "User-Agent": "CZRZ-Client/2.0"}
        path = "/czrz/ai/proxy/text"

        if client_id and secret_key:
            add_signature_headers(
                headers, client_id, secret_key, "POST", path, body_str
            )

        resp = requests.post(
            f"{server_url}/czrz/ai/proxy/text",
            json=request_body,
            headers=headers,
            timeout=(30, 600),
            verify=False,
        )

        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                content = data.get("result", {}).get("content", "")

                try:
                    theme = json.loads(content)
                except:
                    import re

                    json_match = re.search(r"\{.*\}", content, re.DOTALL)
                    if json_match:
                        theme = json.loads(json_match.group())
                    else:
                        raise Exception("无法解析AI返回的主题")

                theme["baby_name"] = baby_name
                theme["user_prompt"] = user_prompt
                theme["generated_at"] = import_datetime().now().isoformat()
                theme["generated_by_ai"] = True
                return theme
            else:
                print(f"AI代理错误: {data.get('error')}")
        else:
            print(f"服务端错误: {resp.status_code}")
    except Exception as e:
        print(f"AI生成主题失败: {e}")

    return generate_theme_locally(baby_name, user_prompt)


def generate_theme_locally(baby_name: str, user_prompt: str) -> dict:
    """
    本地生成主题（基于关键词匹配，无需API）
    """
    from datetime import datetime

    prompt_lower = user_prompt.lower()

    # 定义风格关键词映射
    style_keywords = {
        "warm": {
            "keywords": [
                "温暖",
                "阳光",
                "暖",
                "橙",
                "黄",
                "橙",
                "sun",
                "warm",
                "orange",
                "yellow",
                "暖色",
            ],
            "config": {
                "name": "温暖阳光",
                "description": "暖色调，温暖舒适",
                "decoration": "☀️",
                "variables": {
                    "primary_color": "#FF8C42",
                    "primary_light": "#FFB347",
                    "primary_dark": "#E67300",
                    "secondary_color": "#FFD700",
                    "accent_color": "#FFA500",
                    "bg_color": "#FFF8F0",
                    "bg_gradient": "linear-gradient(180deg, #FFF8F0 0%, #FFEFD5 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#5D4037",
                    "text_secondary": "#8D6E63",
                    "text_light": "#FFFFFF",
                    "border_radius": "20px",
                    "card_shadow": "0 8px 32px rgba(255,140,66,0.15)",
                    "hover_transform": "translateY(-5px) scale(1.02)",
                    "transition_speed": "0.3s",
                    "font_title": "'Ma Shan Zheng', 'ZCOOL KuaiLe', cursive",
                    "font_body": "'Noto Sans SC', 'PingFang SC', sans-serif",
                },
                "custom_css": ".card::before { content: '☀️'; position: absolute; top: -10px; right: -5px; font-size: 24px; animation: sun-rotate 15s linear infinite; } @keyframes sun-rotate { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }",
            },
        },
        "starry": {
            "keywords": [
                "星空",
                "夜晚",
                "蓝色",
                "梦幻",
                "星",
                "夜",
                "蓝",
                "梦",
                "空",
                "moon",
                "star",
                "night",
                "blue",
            ],
            "config": {
                "name": "星空梦境",
                "description": "深蓝色背景，神秘梦幻",
                "decoration": "🌙",
                "variables": {
                    "primary_color": "#667eea",
                    "primary_light": "#8B9DC3",
                    "primary_dark": "#4A5568",
                    "secondary_color": "#9F7AEA",
                    "accent_color": "#E9D8FD",
                    "bg_color": "#f5f7fa",
                    "bg_gradient": "linear-gradient(135deg, #f5f7fa 0%, #e4e8ec 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#2d3748",
                    "text_secondary": "#4a5568",
                    "text_light": "#FFFFFF",
                    "border_radius": "16px",
                    "card_shadow": "0 8px 32px rgba(102,126,234,0.15)",
                    "hover_transform": "translateY(-3px)",
                    "transition_speed": "0.4s",
                    "font_title": "'ZCOOL XiaoWei', 'Noto Serif SC', serif",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": "",
            },
        },
        "forest": {
            "keywords": [
                "森林",
                "绿色",
                "自然",
                "清新",
                "绿",
                "林",
                "森",
                "草",
                "自然",
                "green",
                "forest",
                "nature",
            ],
            "config": {
                "name": "清新自然",
                "description": "绿色自然，清新可爱",
                "decoration": "🌿",
                "variables": {
                    "primary_color": "#48BB78",
                    "primary_light": "#68D391",
                    "primary_dark": "#2F855A",
                    "secondary_color": "#F6E05E",
                    "accent_color": "#F687B3",
                    "bg_color": "#F0FFF4",
                    "bg_gradient": "linear-gradient(135deg, #F0FFF4 0%, #C6F6D5 50%, #9AE6B4 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#22543D",
                    "text_secondary": "#2F855A",
                    "text_light": "#FFFFFF",
                    "border_radius": "24px",
                    "card_shadow": "0 10px 40px rgba(72,187,120,0.2)",
                    "hover_transform": "translateY(-4px) rotate(1deg)",
                    "transition_speed": "0.35s",
                    "font_title": "'ZCOOL KuaiLe', 'Ma Shan Zheng', cursive",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": ".card::after { content: '🌿'; position: absolute; bottom: -5px; left: 10px; font-size: 20px; opacity: 0.6; }",
            },
        },
        "pink": {
            "keywords": [
                "粉色",
                "公主",
                "甜美",
                "可爱",
                "粉",
                "甜",
                "pink",
                "princess",
                "cute",
                "sweet",
            ],
            "config": {
                "name": "甜美可爱",
                "description": "粉色系，甜美可爱",
                "decoration": "💖",
                "variables": {
                    "primary_color": "#ED64A6",
                    "primary_light": "#F687B3",
                    "primary_dark": "#D53F8C",
                    "secondary_color": "#FBB6CE",
                    "accent_color": "#FDE8FF",
                    "bg_color": "#FFF5F7",
                    "bg_gradient": "linear-gradient(135deg, #FFF5F7 0%, #FED7E2 50%, #FBB6CE 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#97266D",
                    "text_secondary": "#B83280",
                    "text_light": "#FFFFFF",
                    "border_radius": "30px",
                    "card_shadow": "0 8px 30px rgba(237,100,166,0.2)",
                    "hover_transform": "translateY(-3px) scale(1.01)",
                    "transition_speed": "0.3s",
                    "font_title": "'ZCOOL QingKe HuangYou', cursive",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": ".card::before { content: '💖'; position: absolute; top: -10px; right: 10px; font-size: 24px; }",
            },
        },
        "minimal": {
            "keywords": [
                "简约",
                "极简",
                "白色",
                "干净",
                "simple",
                "minimal",
                "clean",
                "white",
            ],
            "config": {
                "name": "简约清新",
                "description": "简约设计，干净利落",
                "decoration": "✨",
                "variables": {
                    "primary_color": "#4A5568",
                    "primary_light": "#718096",
                    "primary_dark": "#2D3748",
                    "secondary_color": "#A0AEC0",
                    "accent_color": "#EDF2F7",
                    "bg_color": "#FFFFFF",
                    "bg_gradient": "linear-gradient(135deg, #FFFFFF 0%, #F7FAFC 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#1A202C",
                    "text_secondary": "#4A5568",
                    "text_light": "#FFFFFF",
                    "border_radius": "12px",
                    "card_shadow": "0 4px 20px rgba(0,0,0,0.08)",
                    "hover_transform": "translateY(-2px)",
                    "transition_speed": "0.25s",
                    "font_title": "'Noto Sans SC', sans-serif",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": "",
            },
        },
        "dinosaur": {
            "keywords": [
                "恐龙",
                "龙",
                "史前",
                "悍龙",
                "dinosaur",
                "dino",
                "dragon",
                "jurassic",
                "t-rex",
            ],
            "config": {
                "name": "恐龙世界",
                "description": "恐龙主题，奇妙冒险",
                "decoration": "🦖",
                "variables": {
                    "primary_color": "#48BB78",
                    "primary_light": "#68D391",
                    "primary_dark": "#2F855A",
                    "secondary_color": "#F6AD55",
                    "accent_color": "#F6E05E",
                    "bg_color": "#F0FFF4",
                    "bg_gradient": "linear-gradient(135deg, #F0FFF4 0%, #C6F6D5 50%, #9AE6B4 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#22543D",
                    "text_secondary": "#2F855A",
                    "text_light": "#FFFFFF",
                    "border_radius": "20px",
                    "card_shadow": "0 10px 40px rgba(72,187,120,0.25)",
                    "hover_transform": "translateY(-4px) scale(1.02)",
                    "transition_speed": "0.3s",
                    "font_title": "'ZCOOL KuaiLe', cursive",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": ".card::before { content: '🦖'; position: absolute; top: -15px; right: 10px; font-size: 32px; transform: rotate(-10deg); } .card::after { content: '🌳'; position: absolute; bottom: -8px; left: 15px; font-size: 20px; }",
            },
        },
        "ocean": {
            "keywords": [
                "海洋",
                "海",
                "蓝色",
                "海底",
                "波浪",
                "水",
                "ocean",
                "sea",
                "blue",
                "water",
                "wave",
                "underwater",
            ],
            "config": {
                "name": "海底世界",
                "description": "海洋主题，清新浪漫",
                "decoration": "🐠",
                "variables": {
                    "primary_color": "#4299E1",
                    "primary_light": "#63B3ED",
                    "primary_dark": "#2B6CB0",
                    "secondary_color": "#4FD1C5",
                    "accent_color": "#9DECFA",
                    "bg_color": "#EBF8FF",
                    "bg_gradient": "linear-gradient(180deg, #EBF8FF 0%, #BEE3F8 50%, #90CDF4 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#2C5282",
                    "text_secondary": "#3182CE",
                    "text_light": "#FFFFFF",
                    "border_radius": "24px",
                    "card_shadow": "0 10px 40px rgba(66,153,225,0.25)",
                    "hover_transform": "translateY(-4px)",
                    "transition_speed": "0.35s",
                    "font_title": "'ZCOOL XiaoWei', serif",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": ".card::before { content: '🐠'; position: absolute; top: -12px; right: 15px; font-size: 28px; animation: swim 3s ease-in-out infinite; } @keyframes swim { 0%, 100% { transform: translateX(0) rotate(0deg); } 50% { transform: translateX(-5px) rotate(5deg); } } .card::after { content: '🐙'; position: absolute; bottom: -5px; left: 10px; font-size: 18px; }",
            },
        },
        "space": {
            "keywords": [
                "太空",
                "宇宙",
                "星星",
                "月球",
                "航天",
                "space",
                "universe",
                "star",
                "moon",
                "planet",
                "galaxy",
            ],
            "config": {
                "name": "星际探险",
                "description": "太空主题，神秘梦幻",
                "decoration": "🚀",
                "variables": {
                    "primary_color": "#805AD5",
                    "primary_light": "#9F7AEA",
                    "primary_dark": "#6B46C1",
                    "secondary_color": "#E9D8FD",
                    "accent_color": "#FDE2FF",
                    "bg_color": "#1A202C",
                    "bg_gradient": "linear-gradient(135deg, #1A202C 0%, #2D3748 50%, #4A5568 100%)",
                    "card_bg": "#2D3748",
                    "text_primary": "#E2E8F0",
                    "text_secondary": "#A0AEC0",
                    "text_light": "#FFFFFF",
                    "border_radius": "20px",
                    "card_shadow": "0 10px 40px rgba(128,90,213,0.3)",
                    "hover_transform": "translateY(-4px) scale(1.02)",
                    "transition_speed": "0.4s",
                    "font_title": "'Orbitron', 'ZCOOL QingKe HuangYou', cursive",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": ".card::before { content: '🚀'; position: absolute; top: -15px; right: 10px; font-size: 32px; transform: rotate(-15deg); } .card::after { content: '⭐'; position: absolute; bottom: 10px; right: 10px; font-size: 12px; animation: twinkle 2s ease-in-out infinite; } @keyframes twinkle { 0%, 100% { opacity: 0.3; } 50% { opacity: 1; } } body { background-image: radial-gradient(circle at 20% 50%, rgba(128,90,213,0.1) 0%, transparent 50%), radial-gradient(circle at 80% 80%, rgba(159,122,234,0.1) 0%, transparent 50%); }",
            },
        },
        "candy": {
            "keywords": [
                "糖果",
                "甜品",
                "彩虹",
                "甜点",
                "蛋糕",
                "candy",
                "sweet",
                "rainbow",
                "dessert",
                "cake",
                "colorful",
            ],
            "config": {
                "name": "糖果甜心",
                "description": "糖果主题，甜蜜多彩",
                "decoration": "🍬",
                "variables": {
                    "primary_color": "#ED64A6",
                    "primary_light": "#F687B3",
                    "primary_dark": "#D53F8C",
                    "secondary_color": "#F6AD55",
                    "accent_color": "#FDE8FF",
                    "bg_color": "#FFF5F7",
                    "bg_gradient": "linear-gradient(135deg, #FFF5F7 0%, #FED7E2 25%, #E9D8FD 50%, #C3DAFE 75%, #BEE3F8 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#97266D",
                    "text_secondary": "#B83280",
                    "text_light": "#FFFFFF",
                    "border_radius": "30px",
                    "card_shadow": "0 10px 30px rgba(237,100,166,0.2)",
                    "hover_transform": "translateY(-3px) rotate(-1deg)",
                    "transition_speed": "0.3s",
                    "font_title": "'ZCOOL QingKe HuangYou', cursive",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": ".card::before { content: '🍬'; position: absolute; top: -12px; right: 15px; font-size: 28px; transform: rotate(10deg); } .card::after { content: '🌈'; position: absolute; bottom: -5px; left: 10px; font-size: 20px; opacity: 0.8; }",
            },
        },
        "animal": {
            "keywords": [
                "动物",
                "小动物",
                "萌宠",
                "猫咪",
                "小狗",
                "animal",
                "pet",
                "cat",
                "dog",
                "cute",
                "kawaii",
            ],
            "config": {
                "name": "萌宠天地",
                "description": "可爱动物主题，温馨有趣",
                "decoration": "🐱",
                "variables": {
                    "primary_color": "#F6AD55",
                    "primary_light": "#FBD38D",
                    "primary_dark": "#DD6B20",
                    "secondary_color": "#FC8181",
                    "accent_color": "#FEEBC8",
                    "bg_color": "#FFFAF0",
                    "bg_gradient": "linear-gradient(135deg, #FFFAF0 0%, #FEEBC8 50%, #FBD38D 100%)",
                    "card_bg": "#FFFFFF",
                    "text_primary": "#7B341E",
                    "text_secondary": "#9C4221",
                    "text_light": "#FFFFFF",
                    "border_radius": "28px",
                    "card_shadow": "0 10px 35px rgba(246,173,85,0.2)",
                    "hover_transform": "translateY(-3px) scale(1.01)",
                    "transition_speed": "0.3s",
                    "font_title": "'ZCOOL KuaiLe', cursive",
                    "font_body": "'Noto Sans SC', sans-serif",
                },
                "custom_css": ".card::before { content: '🐱'; position: absolute; top: -12px; right: 15px; font-size: 28px; } .card::after { content: '🐶'; position: absolute; bottom: -5px; left: 10px; font-size: 18px; }",
            },
        },
    }

    # 匹配关键词
    matched_style = None
    max_matches = 0

    for style_name, style_data in style_keywords.items():
        matches = sum(1 for kw in style_data["keywords"] if kw in prompt_lower)
        if matches > max_matches:
            max_matches = matches
            matched_style = style_data["config"]

    # 如果没有匹配，使用默认温暖风格
    if not matched_style:
        matched_style = style_keywords["warm"]["config"]

    # 创建主题配置
    theme = matched_style.copy()
    theme["baby_name"] = baby_name
    theme["user_prompt"] = user_prompt
    theme["generated_at"] = datetime.now().isoformat()
    theme["generated_by_ai"] = False  # 本地生成标记

    return theme


def import_datetime():
    """延迟导入datetime"""
    from datetime import datetime

    return datetime


def get_or_create_theme(
    client_id: str, baby_name: str, force_new: bool = False
) -> dict:
    """获取或创建主题

    Args:
        client_id: 客户端ID
        baby_name: 宝宝名称
        force_new: 是否强制创建新主题（删除旧主题）
    """
    if force_new:
        # 删除旧主题文件
        theme_file = THEMES_DIR / f"{client_id}.json"
        if theme_file.exists():
            theme_file.unlink()
        return generate_theme_locally(baby_name, "温暖阳光风格")

    theme = load_theme(client_id)
    if theme is None:
        # 创建一个默认主题
        theme = generate_theme_locally(baby_name, "温暖阳光风格")
        save_theme(client_id, theme)
    return theme


def update_theme(client_id: str, user_prompt: str, api_key: str = None) -> dict:
    """根据用户描述更新主题 - 智能调整现有主题

    注意：api_key、api_url、model 都会从配置文件读取，不需要传入
    """
    theme = load_theme(client_id)
    baby_name = theme.get("baby_name", "宝宝") if theme else "宝宝"

    # 使用AI生成新主题（配置从文件读取）
    new_theme = generate_custom_theme_by_ai(baby_name, user_prompt, theme, api_key)

    save_theme(client_id, new_theme)
    return new_theme


def generate_custom_theme_by_ai(
    baby_name: str, user_prompt: str, current_theme: dict = None, api_key: str = None
) -> dict:
    """
    使用AI根据用户描述生成自定义主题 - 完全由AI自由发挥创意

    核心理念：
    - 不对用户输入做关键词限制，AI负责语义理解
    - AI可以自由设计配色、动画、装饰
    - 只设定技术边界（安全、性能、可行性）
    - 只在AI服务不可用时才回退到本地
    """
    client_config = get_client_config()
    server_url = client_config.get("server_url", "")
    client_id = client_config.get("client_id", "")
    secret_key = client_config.get("secret_key", "")

    if not server_url or not client_id:
        print("[主题生成] 未配置服务端地址或客户端ID，回退到本地生成")
        return generate_theme_locally(baby_name, user_prompt)

    system_prompt = """你是专业的网页UI设计师和CSS专家。请根据用户的描述，为宝宝成长记录系统设计一个独特的主题。

【核心原则】
1. **语义理解**：深入理解用户的意图，不要局限于字面关键词
2. **创意自由**：你可以自由设计配色、动画、装饰效果
3. **情感表达**：通过颜色、动画传达用户想要的氛围（可爱、梦幻、活力、宁静等）

【技术边界 - 仅用于确保可行性】
✅ 你可以使用：
   - CSS所有特性：渐变、阴影、动画、变形、滤镜
   - Emoji作为装饰元素（任意emoji）
   - CSS伪元素(::before/::after)创建装饰
   - 纯CSS图案（用渐变创建背景纹理）
   - Keyframes动画（漂浮、闪烁、旋转、缩放等）

❌ 无法实现（如果用户提到，请用替代方案）：
   - 上传图片文件、自定义SVG文件
   - 需要JavaScript的复杂交互
   - 外部字体文件（使用系统字体如 'Noto Sans SC', 'PingFang SC'）
   - WebGL/3D效果（性能考虑）

【输出格式】
必须返回有效的JSON：
{
  "name": "主题名称（2-6字，有创意）",
  "description": "主题描述，说明设计理念",
  "decoration": "代表主题的emoji（1-2个）",
  "variables": {
    "primary_color": "主色HEX",
    "primary_light": "浅色HEX",
    "primary_dark": "深色HEX",
    "secondary_color": "辅助色HEX",
    "accent_color": "强调色HEX",
    "bg_color": "页面背景色HEX",
    "bg_gradient": "页面背景渐变CSS",
    "card_bg": "通用卡片背景色",
    "text_primary": "主文字HEX",
    "text_secondary": "次文字HEX",
    "text_light": "浅色文字HEX",
    "border_radius": "圆角大小",
    "card_shadow": "阴影CSS",
    "hover_transform": "悬停变换效果",
    "transition_speed": "过渡动画时间",
    "font_title": "标题字体",
    "font_body": "正文字体",
    "photo_frame_bg": "照片框背景",
    "photo_frame_border": "照片框边框",
    "photo_frame_shadow": "照片框阴影",
    "photo_frame_radius": "照片框圆角",
    "log_card_bg": "日志框背景",
    "log_card_border": "日志框边框",
    "log_card_shadow": "日志框阴影",
    "log_card_radius": "日志框圆角",
    "log_watermark_content": "日志水印内容（如'⚙️'、'🌸'、'📝'等emoji，或留空）",
    "log_watermark_opacity": "水印透明度（建议0.05-0.15，默认0.08）",
    "log_watermark_size": "水印大小（如40px, 60px, 80px）",
    "gallery_item_bg": "相册项背景",
    "gallery_item_border": "相册项边框",
    "gallery_item_radius": "相册项圆角"
  },
  "custom_css": "完整的自定义CSS，必须包含装饰emoji的伪元素和动画效果"
}

【设计建议】
- 可爱风：圆润的形状、明亮的颜色、弹跳动画
- 梦幻风：柔和渐变、星星/云朵装饰、缓慢漂浮动画
- 自然风：绿色系、叶子装饰、有机曲线
- 科技风：冷色调、简洁线条、脉冲动画
- 复古风：暖色调、纹理背景、优雅过渡

只返回JSON，不要其他说明。"""

    user_request = f"为宝宝'{baby_name}'的成长记录系统设计主题。用户想要：{user_prompt}"

    try:
        request_body = {
            "client_id": client_id,
            "prompt": user_request,
            "system_prompt": system_prompt,
            "max_tokens": 2000,
            "operation": "generate_custom_theme",
        }
        body_str = json.dumps(request_body)
        headers = {"Content-Type": "application/json"}
        path = "/czrz/ai/proxy/text"

        if client_id and secret_key:
            add_signature_headers(
                headers, client_id, secret_key, "POST", path, body_str
            )

        resp = requests.post(
            f"{server_url}/czrz/ai/proxy/text",
            json=request_body,
            headers=headers,
            timeout=(30, 600),
            verify=False,
        )

        if resp.status_code != 200:
            raise Exception(f"服务端错误: {resp.status_code}")

        data = resp.json()
        if not data.get("success"):
            raise Exception(data.get("error", "AI调用失败"))

        content = data.get("result", {}).get("content", "")

        try:
            theme = json.loads(content)
        except json.JSONDecodeError:
            import re

            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                theme = json.loads(json_match.group())
            else:
                raise Exception("无法解析AI返回的内容")

        if "variables" not in theme:
            raise Exception("返回数据格式不正确")

        theme["baby_name"] = baby_name
        theme["user_prompt"] = user_prompt
        theme["generated_at"] = import_datetime().now().isoformat()
        theme["generated_by_ai"] = True

        return theme

    except Exception as e:
        print(f"AI服务不可用，回退到本地生成: {e}")
        fallback = generate_theme_locally(baby_name, user_prompt)
        fallback["ai_error"] = str(e)
        fallback["fallback"] = True
        return fallback


def generate_theme_with_decorations(
    baby_name: str, user_prompt: str, base_theme: dict = None
) -> dict:
    """本地生成带装饰的主题（当AI不可用时）

    优先使用 generate_theme_locally 匹配完整主题，然后添加装饰
    """
    from datetime import datetime

    prompt_lower = user_prompt.lower()

    # 首先尝试用 generate_theme_locally 匹配完整主题（如恐龙、森林等）
    theme = generate_theme_locally(baby_name, user_prompt)

    # 如果没有匹配到特定主题（返回了默认主题），才使用传入的base_theme
    if theme.get("name") == "温暖阳光" and base_theme:
        theme = base_theme.copy()

    # 根据用户要求添加额外装饰
    decorations = {
        "动物": ["🐱", "🐶", "🐰", "🐻", "🐼"],
        "猫": ["🐱", "🐈", "🐾"],
        "狗": ["🐶", "🐕", "🦴"],
        "宠物": ["🐱", "🐶", "🐰", "🐾"],
        "星星": ["⭐", "✨", "🌟"],
        "月亮": ["🌙", "⭐"],
        "花": ["🌸", "🌺", "🌻"],
        "海洋": ["🐠", "🐟", "🌊", "🐚"],
        "森林": ["🌲", "🌿", "🍃"],
    }

    # 检测关键词
    matched_decos = []
    for keyword, decos in decorations.items():
        if keyword in prompt_lower or keyword in user_prompt:
            matched_decos.extend(decos)

    if matched_decos:
        # 添加装饰CSS
        deco_list = matched_decos[:3]  # 最多3个不同装饰
        custom_css = f"""
.card::before {{ content: '{deco_list[0]}'; position: absolute; top: -5px; right: 10px; font-size: 24px; animation: float 3s ease-in-out infinite; }}
"""
        if len(deco_list) > 1:
            custom_css += f".card::after {{ content: '{deco_list[1]}'; position: absolute; bottom: 5px; left: 10px; font-size: 18px; opacity: 0.7; }}\n"

        custom_css += """
@keyframes float {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-5px); }
}
"""

        # 合并CSS
        original_css = theme.get("custom_css", "")
        theme["custom_css"] = (
            (original_css + "\n" + custom_css).strip() if original_css else custom_css
        )

        # 如果主题名不是以+结尾，添加+
        if not theme.get("name", "").endswith("+"):
            theme["name"] = theme.get("name", "自定义") + "+"

        # 更新描述
        theme["description"] = (
            f"{theme.get('description', '')}，添加了{user_prompt}装饰"
        )

    theme["customized"] = True
    theme["user_prompt"] = user_prompt
    return theme


# 为了保持兼容性，保留空预设列表
PRESET_THEMES = {}


