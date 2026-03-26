"""
宝宝照片智能选择与分析模块
为日志生成提供最佳照片选择和AI内容分析
"""

import os
import io
import re
import json
import base64
import hashlib
import hmac
import time
import requests
import sys
from pathlib import Path
from PIL import Image
from datetime import datetime


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


def _check_token_quota() -> tuple:
    """检查 token 配额，返回 (can_use, used, limit)"""
    try:
        config_file = Path.home() / "Documents" / "CZRZ" / "config.json"
        if not config_file.exists():
            return True, 0, 0
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        client_id = config.get("client_id")
        server_url = config.get("server_url", "")
        if not client_id or not server_url:
            return True, 0, 0
        resp = requests.get(
            f"{server_url}/czrz/client/token-check",
            params={"client_id": client_id},
            timeout=5,
            verify=False,
            headers={"User-Agent": "CZRZ-Client/2.0"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("can_use", True), data.get("used", 0), data.get("limit", 0)
    except Exception as e:
        print(f"[Token] 配额检查失败: {e}")
    return True, 0, 0


def _report_token_usage(usage: dict, operation: str = "select_photo"):
    """上报 token 使用到服务端"""
    try:
        config_file = Path.home() / "Documents" / "CZRZ" / "config.json"
        if not config_file.exists():
            return
        with open(config_file, "r", encoding="utf-8") as f:
            config = json.load(f)
        client_id = config.get("client_id")
        server_url = config.get("server_url", "")
        if not client_id or not server_url:
            return
        resp = requests.post(
            f"{server_url}/czrz/client/token-record",
            json={"client_id": client_id, "usage": usage, "operation": operation},
            timeout=5,
            verify=False,
            headers={"User-Agent": "CZRZ-Client/2.0"},
        )
        print(
            f"[Token] 上报 {operation}: {usage.get('total_tokens', 0)} tokens, 状态: {resp.status_code}"
        )
    except Exception as e:
        print(f"[Token] 上报失败: {e}")


# 可选依赖
try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    import imagehash

    IMAGEHASH_AVAILABLE = True
except ImportError:
    IMAGEHASH_AVAILABLE = False

# 添加父目录到路径，以便导入 server_public
sys.path.insert(0, str(Path(__file__).parent))

# =====================
# 配置（从系统配置读取）
# =====================

MAX_IMAGE_SIZE = 1024
ROUND1_BATCH = 20
ROUND1_SELECT = 1  # 只选1张最佳照片
PHASH_THRESHOLD = 8


def get_client_config():
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
    except:
        pass
    return {"server_url": "", "client_id": "", "secret_key": ""}


def get_api_config():
    """
    获取API配置 - 安全架构：不再返回API Token

    返回服务端代理URL和客户端ID，所有AI调用通过服务端代理
    """
    client_config = get_client_config()
    server_url = client_config.get("server_url", "")
    client_id = client_config.get("client_id", "")
    secret_key = client_config.get("secret_key", "")

    if server_url:
        return {
            "proxy_url": f"{server_url}/czrz/ai/proxy/vision",
            "client_id": client_id,
            "secret_key": secret_key,
            "api_key": "",  # 不再使用本地API Key
            "api_url": "",  # 不再直接调用阿里云
            "model": "",  # 模型由服务端决定
        }

    return {
        "proxy_url": "",
        "client_id": "",
        "secret_key": "",
        "api_key": "",
        "api_url": "",
        "model": "",
    }


# =====================
# 获取宝宝画像
# =====================


def get_child_features(child_id):
    """
    从baby_health_ai获取宝宝画像特征

    Returns:
        dict: {
            'top_interests': ['音乐', '运动'],  # 兴趣偏好
            'personality': {'activity': 8.5, ...},  # 性格特征
            'diversity_hints': '之前主要选择了笑的照片'  # 多样性提示
        }
    """
    try:
        import requests as req

        # 从配置获取baby_health_ai服务地址
        from model_config import get_text_model_config

        config = get_text_model_config()

        # 通过服务端代理访问健康AI
        config_file = Path.home() / "Documents" / "CZRZ" / "config.json"
        server_url = ""
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                client_config = json.load(f)
            server_url = client_config.get("server_url", "")

        if not server_url:
            print("[DEBUG] 未配置服务端URL，跳过画像获取")
            return default_result

        # 通过服务端代理调用特征API
        print(f"[DEBUG] 获取宝宝画像: {server_url}/api/ai/features/{child_id}")
        response = req.get(
            f"{server_url}/api/ai/features/{child_id}",
            timeout=1200,
            headers={"User-Agent": "CZRZ-Client/2.0"},
            verify=False,
        )

        if response.status_code == 200:
            data = response.json()
            print(f"[DEBUG] 画像API返回: {data}")
            if data.get("success"):
                features = data["features"]

                # 提取主要兴趣（分数>7的）
                top_interests = []
                for key, value in features["interest"].items():
                    if value > 7.0:
                        interest_names = {
                            "music": "音乐",
                            "sports": "运动",
                            "reading": "阅读",
                            "art": "艺术",
                            "nature": "自然",
                            "technology": "科技",
                        }
                        top_interests.append(interest_names.get(key, key))

                # 提取性格特点（分数>8的）
                personality_highs = []
                for key, value in features["personality"].items():
                    if value > 8.0:
                        personality_names = {
                            "activity": "活泼",
                            "curiosity": "好奇",
                            "social": "社交能力强",
                            "focus": "专注",
                            "emotion_stability": "情绪稳定",
                            "adaptability": "适应性强",
                        }
                        personality_highs.append(personality_names.get(key, key))

                # 提取多样性提示（基于当前数据）
                diversity_hints = []
                if len(top_interests) > 0:
                    diversity_hints.append(
                        f"宝宝对{', '.join(top_interests)}比较感兴趣"
                    )
                if len(personality_highs) > 0:
                    diversity_hints.append(f"性格特点：{', '.join(personality_highs)}")

                return {
                    "top_interests": top_interests,
                    "personality_highs": personality_highs,
                    "diversity_hints": "。".join(diversity_hints)
                    if diversity_hints
                    else "暂无明显偏好",
                    "familiarity": data.get("familiarity", 0),
                }
    except Exception as e:
        print(f"[WARN] 获取宝宝画像失败: {e}")

    # 返回默认值
    return {
        "top_interests": [],
        "personality_highs": [],
        "diversity_hints": "暂无画像数据",
        "familiarity": 0,
    }


def analyze_previous_selections():
    """
    分析之前选择的照片类型，用于多样性约束
    当前实现：返回空数据（首次选择时无历史记录）

    Returns:
        dict: {
            'emotion_types': [],  # 已选择的表情类型
            'scene_types': [],  # 已选择的场景类型
            'suggestion': ''  # 选择建议
        }
    """
    # 当前返回空数据，表示首次选择无历史记录
    # 未来可从baby_health_ai数据库查询历史选择记录
    return {
        "emotion_types": [],
        "scene_types": [],
        "suggestion": "",
    }


# =====================
# 日期目录识别
# =====================


def is_date_folder(name):

    patterns = [r"\d{4}-\d{2}-\d{2}", r"\d{8}"]

    for p in patterns:
        if re.fullmatch(p, name):
            return True

    return False


def normalize_date(name):

    if "-" in name:
        return name

    return f"{name[:4]}-{name[4:6]}-{name[6:]}"


def find_today_folder():

    today = datetime.now().strftime("%Y-%m-%d")

    for root, dirs, files in os.walk(PHOTO_ROOT):
        for d in dirs:
            if is_date_folder(d):
                if normalize_date(d) == today:
                    return os.path.join(root, d)

    return None


# =====================
# 模糊检测（兼容无cv2环境）
# =====================


def is_blurry(path, threshold=80):
    """检测照片是否模糊，无cv2时返回False"""
    if not CV2_AVAILABLE:
        return False  # 无法检测，默认不模糊

    try:
        img = cv2.imread(path)
        if img is None:
            return False

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        variance = cv2.Laplacian(gray, cv2.CV_64F).var()
        return variance < threshold
    except Exception as e:
        print(f"[WARN] 模糊检测失败: {e}")
        return False


# =====================
# 重复照片过滤（兼容无imagehash环境）
# =====================


def remove_similar(images):
    """去重照片，无imagehash时返回原列表"""
    if not IMAGEHASH_AVAILABLE or len(images) <= 1:
        return images

    hashes = []
    result = []

    for path in images:
        try:
            img = Image.open(path)
            h = imagehash.phash(img)

            duplicate = False
            for existing in hashes:
                if abs(h - existing) < PHASH_THRESHOLD:
                    duplicate = True
                    break

            if not duplicate:
                hashes.append(h)
                result.append(path)
        except Exception as e:
            print(f"[WARN] 处理图片失败: {e}")
            result.append(path)  # 失败的保留

    return result

    return result


# =====================
# 图片压缩
# =====================


def compress_image(path):

    img = Image.open(path)

    img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE))

    buf = io.BytesIO()

    img.convert("RGB").save(buf, format="JPEG", quality=85)

    return base64.b64encode(buf.getvalue()).decode()


# =====================
# 调用 Qwen
# =====================


def call_qwen(images, select_n, child_id=None):
    config = get_api_config()
    proxy_url = config.get("proxy_url", "")
    client_id = config.get("client_id", "") or child_id
    secret_key = config.get("secret_key", "")

    if not proxy_url or not client_id:
        raise Exception("未配置服务端地址或客户端ID")

    child_features = {}
    if child_id:
        child_features = get_child_features(child_id)
        print(
            f"[INFO] 宝宝画像: 兴趣={child_features.get('top_interests', [])}, 性格={child_features.get('personality_highs', [])}"
        )

    previous_selections = analyze_previous_selections()

    interest_hint = ""
    if child_features.get("top_interests"):
        interests = "、".join(child_features["top_interests"][:3])
        interest_hint = f"宝宝当前兴趣方向：{interests}（仅供参考，不要只选相关照片）"

    personality_hint = ""
    if child_features.get("personality_highs"):
        personality = "、".join(child_features["personality_highs"][:3])
        personality_hint = f"宝宝性格特点：{personality}（但要展现多面性）"

    diversity_hint = ""
    if previous_selections.get("suggestion"):
        diversity_hint = f"历史选择提示：{previous_selections['suggestion']}"

    content = [
        {
            "type": "text",
            "text": f"""
你是宝宝成长记录助手。

请分析这些照片，完成以下任务：

【任务1：为每张照片生成描述】
为每张照片提供：
- description: 照片内容描述（50字以内）
- has_baby: 照片中是否有宝宝（true/false）
- scene: 场景（如：公园、家里、商场）
- activity: 活动（如：玩耍、吃饭、睡觉）

【任务2：选出精选照片】
从有宝宝的照片中选出最有代表性的 {select_n} 张：
- 照片质量好（清晰、构图好）
- 有意义（宝宝表情好、动作清晰）
- 多样性（不同场景、不同表情）

【任务3：识别问题照片】
- blurry: 明显模糊的照片
- duplicates: 内容相似的照片组（连拍、同一场景）
- no_baby: 照片中没有宝宝

只返回 JSON 格式：

{{
  "photos": {{
    "照片1.jpg": {{"description": "宝宝在公园草地上奔跑", "has_baby": true, "scene": "公园", "activity": "玩耍"}},
    "照片2.jpg": {{"description": "公园的树荫", "has_baby": false, "scene": "公园", "activity": ""}}
  }},
  "selected": ["精选照片1.jpg"],
  "reasons": ["选择原因：宝宝表情自然，光线充足"],
  "blurry": ["模糊照片.jpg"],
  "duplicates": [["相似A.jpg", "相似B.jpg"]],
  "no_baby": ["无宝宝照片.jpg"]
}}

注意：
1. photos 字段必须包含所有照片的描述
2. selected 只从 has_baby=true 的照片中选择
3. blurry、duplicates、no_baby 可以为空数组
""",
        }
    ]

    for filename, b64 in images:
        content.append({"type": "text", "text": f"文件名: {filename}"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        )

    try:
        request_body = {
            "client_id": client_id,
            "messages": [{"role": "user", "content": content}],
            "operation": "select_photo",
        }
        body_str = json.dumps(request_body)
        headers = {"Content-Type": "application/json", "User-Agent": "CZRZ-Client/2.0"}
        path = "/czrz/ai/proxy/vision"

        if client_id and secret_key:
            add_signature_headers(
                headers, client_id, secret_key, "POST", path, body_str
            )

        r = requests.post(
            proxy_url,
            json=request_body,
            headers=headers,
            timeout=1200,
            verify=False,
        )

        res = r.json()

        if not res.get("success"):
            error = res.get("error", "未知错误")
            message = res.get("message", error)
            if error == "QUOTA_EXCEEDED" or error == "QUOTA_EXHAUSTED":
                raise Exception(f"ALL_MODELS_EXHAUSTED:{message}")
            raise Exception(message)

        result = res.get("result", {})
        content_result = result.get("content", "")

        if content_result:
            return content_result

        raise Exception("代理API返回空结果")

    except requests.exceptions.RequestException as e:
        raise Exception(f"代理请求失败: {str(e)}")


# =====================
# AI筛选
# =====================


def ai_select(paths, select_n, child_id=None):
    """
    AI智能选择最佳照片，同时返回所有照片的详细描述

    Args:
        paths: 照片路径列表
        select_n: 要选择的照片数量
        child_id: 宝宝ID（用于获取画像特征）

    Returns:
        dict: {
            "selected": [选中的照片路径],
            "blurry": [模糊照片路径],
            "duplicates": [[相似照片组]],
            "no_baby": [无宝宝照片路径],
            "photos": {文件名: {description, has_baby, scene, activity}},
            "reasons": [选择原因]
        }
    """
    batch = []

    for p in paths:
        filename = os.path.basename(p)

        b64 = compress_image(p)

        batch.append((filename, b64))

    result = call_qwen(batch, select_n, child_id)

    data = json.loads(result)

    selected_filenames = data.get("selected", [])
    selected_paths = [p for p in paths if os.path.basename(p) in selected_filenames]

    blurry_filenames = data.get("blurry", [])
    blurry_paths = [p for p in paths if os.path.basename(p) in blurry_filenames]

    duplicate_groups = data.get("duplicates", [])
    duplicate_path_groups = []
    for group in duplicate_groups:
        path_group = [p for p in paths if os.path.basename(p) in group]
        if len(path_group) > 1:
            duplicate_path_groups.append(path_group)

    no_baby_filenames = data.get("no_baby", [])
    no_baby_paths = [p for p in paths if os.path.basename(p) in no_baby_filenames]

    photos = data.get("photos", {})

    if data.get("reasons"):
        print(f"[INFO] 照片选择原因: {data.get('reasons')}")
    if no_baby_paths:
        print(f"[INFO] 无宝宝照片: {len(no_baby_paths)}张")

    return {
        "selected": selected_paths,
        "blurry": blurry_paths,
        "duplicates": duplicate_path_groups,
        "no_baby": no_baby_paths,
        "photos": photos,
        "blurry_filenames": blurry_filenames,
        "duplicate_filenames": duplicate_groups,
        "no_baby_filenames": no_baby_filenames,
        "reasons": data.get("reasons", []),
    }


# =====================
# 主流程
# =====================


def select_best_photo(child_id=None):
    """
    选择最佳照片

    Args:
        child_id: 宝宝ID（用于获取画像特征，实现个性化选择）
    """

    folder = find_today_folder()

    if not folder:
        raise Exception("未找到今日照片目录")

    images = [
        os.path.join(folder, f)
        for f in os.listdir(folder)
        if f.lower().endswith(("jpg", "jpeg", "png"))
    ]

    original_count = len(images)

    # 去重
    images = remove_similar(images)

    dedup_count = len(images)

    # 模糊过滤
    images = [p for p in images if not is_blurry(p)]

    clear_count = len(images)

    if clear_count == 0:
        raise Exception("没有清晰照片")

    # 第一轮
    candidates = []

    for i in range(0, len(images), ROUND1_BATCH):
        chunk = images[i : i + ROUND1_BATCH]

        selected = ai_select(chunk, ROUND1_SELECT, child_id)

        candidates.extend(selected)

    # 第二轮
    final = ai_select(candidates, 1, child_id)

    best = os.path.basename(final[0])

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "photo_folder": folder,
        "best_photo": best,
        "stats": {
            "original": original_count,
            "after_dedup": dedup_count,
            "after_blur_filter": clear_count,
            "round2_candidates": len(candidates),
        },
    }


def select_best_photo_for_client(
    media_folders, client_id=None, date=None, data_dir=None
):
    """
    为客户端选择最佳照片

    Args:
        media_folders: 媒体文件夹列表
        client_id: 客户端ID
        date: 日期字符串 (YYYY-MM-DD)，默认今天
        data_dir: 数据目录，用于存储索引

    Returns:
        dict: {photo_folder, best_photo, stats, blurry, duplicates, method}
    """
    if not media_folders:
        raise Exception("未配置媒体文件夹")

    target_date = date or datetime.now().strftime("%Y-%m-%d")

    # 使用 PhotoManager 从索引获取指定日期的照片
    try:
        from photo_manager import PhotoManager
        from pathlib import Path

        if data_dir is None:
            data_dir = Path.home() / "Documents" / "CZRZ"

        pm = PhotoManager(media_folders, Path(data_dir))
        photos = pm.get_photos_by_date(target_date)

        if not photos:
            raise Exception(f"未找到 {target_date} 的照片")

        # 过滤视频文件，只保留图片
        IMAGE_EXTENSIONS = (
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".heic",
            ".heif",
            ".bmp",
        )
        images = [
            p["path"]
            for p in photos
            if os.path.exists(p.get("path", ""))
            and p.get("path", "").lower().endswith(IMAGE_EXTENSIONS)
        ]

        if not images:
            raise Exception(f"未找到 {target_date} 的有效照片文件")

        found_folder = str(Path(images[0]).parent) if images else ""

    except Exception as e:
        raise Exception(f"获取照片失败: {str(e)}")

    original_count = len(images)

    if not images:
        raise Exception("没有照片")

    # 直接交给AI选择，不再预处理去重和模糊
    # AI会同时识别最佳照片、模糊照片、重复照片

    # 如果只有一张，直接返回
    if len(images) == 1:
        return {
            "date": target_date,
            "photo_folder": found_folder,
            "best_photo": os.path.basename(images[0]),
            "stats": {"original": original_count, "selected": 1},
            "blurry": [],
            "duplicates": [],
            "method": "single",
        }

    # AI选择最佳照片
    all_blurry = []
    all_duplicates = []

    try:
        result = ai_select(images, 1, client_id)
        best = (
            os.path.basename(result["selected"][0])
            if result["selected"]
            else os.path.basename(images[0])
        )
        all_blurry = result.get("blurry_filenames", [])
        all_duplicates = result.get("duplicate_filenames", [])
    except Exception as e:
        print(f"[WARN] AI选择失败: {e}，使用第一张")
        best = os.path.basename(images[0])

    return {
        "date": target_date,
        "photo_folder": found_folder,
        "best_photo": best,
        "stats": {"original": original_count},
        "blurry": all_blurry,
        "duplicates": all_duplicates,
        "method": "ai",
    }


# =====================
# 对外接口（供 baby_log_generator 调用）
# =====================


def select_best_from_list(photo_paths, select_n=1):
    """
    从照片列表中选择最佳照片

    Args:
        photo_paths: 照片路径列表
        select_n: 要选择的照片数量

    Returns:
        选中的照片路径列表
    """
    if not photo_paths:
        return []

    # 去重
    images = remove_similar(photo_paths)

    # 模糊过滤
    images = [p for p in images if not is_blurry(p)]

    if not images:
        return photo_paths[:select_n]  # 如果都模糊，返回前N张

    # 如果数量少，直接返回
    if len(images) <= select_n:
        return images

    # AI选择
    try:
        result = ai_select(images, select_n)
        return result.get("selected", images[:select_n])
    except Exception as e:
        print(f"[WARN] AI选择失败: {e}, 返回第一张")
        return images[:select_n]


def analyze_photo_content(photo_path):
    """
    使用AI分析照片内容，生成描述

    Args:
        photo_path: 照片路径

    Returns:
        照片内容描述字符串
    """
    try:
        config = get_api_config()
        proxy_url = config.get("proxy_url", "")
        client_id = config.get("client_id", "")
        secret_key = config.get("secret_key", "")

        if not proxy_url or not client_id:
            return None

        b64 = compress_image(photo_path)

        content = [
            {
                "type": "text",
                "text": """
请描述这张照片。

要求：
1. 如果照片中有宝宝，描述宝宝的动作、表情、穿着，以及周围环境
2. 如果照片中没有宝宝（如宠物、风景、物品等），开头必须写：【未包含宝宝】
3. 用简洁温馨的语言，50字以内
4. 直接返回描述文字，不要其他内容
""",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            },
        ]

        request_body = {
            "client_id": client_id,
            "messages": [{"role": "user", "content": content}],
            "operation": "analyze_photo",
        }
        body_str = json.dumps(request_body)
        headers = {"Content-Type": "application/json", "User-Agent": "CZRZ-Client/2.0"}
        path = "/czrz/ai/proxy/vision"

        if client_id and secret_key:
            add_signature_headers(
                headers, client_id, secret_key, "POST", path, body_str
            )

        r = requests.post(
            proxy_url,
            json=request_body,
            headers=headers,
            timeout=1200,
            verify=False,
        )

        res = r.json()

        if not res.get("success"):
            print(f"[WARN] 代理API失败: {res.get('error')}")
            return None

        result = res.get("result", {})
        description = result.get("content", "").strip()
        return description if description else None

    except Exception as e:
        print(f"[WARN] 照片分析失败: {e}")
        return None


LOG_STYLE_PROMPTS = {
    "简练": "用简洁明了的语言，像日常记录，100字左右",
    "诗意": "用文艺清新的语言，可以加入诗意的比喻，如'阳光洒在脸上...'，150字左右",
    "东北腔": "用东北话的口吻写，幽默风趣，可以说'这孩子咋这么招人稀罕'、'杠杠的'等，100字左右",
    "详细": "详细记录当天的活动细节，时间、地点、做了什么，200字左右",
    "童趣": "用轻松可爱的语气，像给宝宝讲故事，可以说'今天宝宝是探险家...'，150字左右",
    "深情": "用温暖感人的语言，表达父母对宝宝的爱，可以说'看着你一天天长大...'，150字左右",
    "IT男风格": "用程序员风格写，可以用代码注释、技术术语，如'// 今日活动：户外探索 v1.0'，100字左右",
}


def analyze_all_photos(
    photo_paths: list,
    max_photos: int = 20,
    client_id: str = None,
    date: str = None,
    ai_result: dict = None,
) -> dict:
    """
    从 ai_select 的结果中过滤出有效照片，不再调用 AI

    Args:
        photo_paths: 照片路径列表（用于降级时分析）
        max_photos: 最多分析的照片数量
        client_id: 客户端ID（用于保存到服务端）
        date: 日期（用于保存到服务端）
        ai_result: ai_select 的结果，包含 photos, blurry, no_baby, duplicates

    Returns:
        {
            "photos": [{"path": "...", "description": "...", "has_baby": bool, "scene": "..."}],
            "scenes": ["室内", "公园"],
            "activities": ["玩耍", "吃饭"],
            "baby_photos": 15,
            "scenery_photos": 5,
            "combined_summary": "综合描述..."
        }
    """
    if not photo_paths:
        return {
            "photos": [],
            "scenes": [],
            "activities": [],
            "baby_photos": 0,
            "scenery_photos": 0,
            "combined_summary": "",
        }

    IMAGE_EXTENSIONS = (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".heic",
        ".heif",
        ".bmp",
    )
    photo_paths = [p for p in photo_paths if p.lower().endswith(IMAGE_EXTENSIONS)]

    if not photo_paths:
        return {
            "photos": [],
            "scenes": [],
            "activities": [],
            "baby_photos": 0,
            "scenery_photos": 0,
            "combined_summary": "没有图片文件",
        }

    if not ai_result or not ai_result.get("photos"):
        print(f"[WARN] analyze_all_photos: ai_result 为空或没有 photos 字段，跳过保存")
        return {
            "photos": [],
            "scenes": [],
            "activities": [],
            "baby_photos": 0,
            "scenery_photos": 0,
            "combined_summary": "无 AI 分析结果",
        }

    photos_dict = ai_result.get("photos", {})
    print(f"[DEBUG] analyze_all_photos: ai_result.photos 有 {len(photos_dict)} 条记录")
    blurry_filenames = set(ai_result.get("blurry_filenames", []))
    no_baby_filenames = set(ai_result.get("no_baby_filenames", []))
    duplicate_groups = ai_result.get("duplicate_filenames", [])

    dup_to_remove = set()
    for group in duplicate_groups:
        if len(group) > 1:
            for filename in group[1:]:
                dup_to_remove.add(filename)

    valid_photos = []
    all_scenes = set()
    all_activities = set()
    baby_count = 0
    scenery_count = 0

    for path in photo_paths[:max_photos]:
        filename = os.path.basename(path)

        if filename in blurry_filenames:
            continue
        if filename in no_baby_filenames:
            continue
        if filename in dup_to_remove:
            continue

        photo_info = photos_dict.get(filename, {})
        if not photo_info:
            continue

        description = photo_info.get("description", "")
        has_baby = photo_info.get("has_baby", True)
        scene = photo_info.get("scene", "")
        activity = photo_info.get("activity", "")

        valid_photos.append(
            {
                "path": path,
                "description": description,
                "has_baby": has_baby,
                "scene": scene,
                "activity": activity,
            }
        )

        if has_baby:
            baby_count += 1
        else:
            scenery_count += 1
        if scene:
            all_scenes.add(scene)
        if activity:
            all_activities.add(activity)

    combined_parts = []
    if baby_count > 0:
        combined_parts.append(f"共{baby_count}张宝宝照片")
    if scenery_count > 0:
        combined_parts.append(f"{scenery_count}张场景照片")

    descriptions = [p["description"] for p in valid_photos if p["description"]]
    if descriptions:
        combined_parts.append("活动：" + "、".join(descriptions[:10]))

    combined_summary = "。".join(combined_parts) if combined_parts else "照片分析完成"

    if client_id and date and valid_photos:
        try:
            save_photo_descriptions_to_server(client_id, date, valid_photos)
        except Exception as e:
            print(f"[WARN] 保存照片描述失败: {e}")

    return {
        "photos": valid_photos,
        "scenes": list(all_scenes),
        "activities": list(all_activities),
        "baby_photos": baby_count,
        "scenery_photos": scenery_count,
        "combined_summary": combined_summary,
    }


def save_photo_descriptions_to_server(client_id: str, date: str, photos: list):
    """
    保存照片描述到服务端（用于喂给健康AI）

    Args:
        client_id: 客户端ID
        date: 日期
        photos: 照片描述列表
    """
    import requests as req
    import hashlib

    descriptions = []
    for p in photos:
        if p.get("description") and p["description"] != "分析失败":
            # 计算照片文件哈希
            file_hash = ""
            file_path = p.get("path", "")
            if file_path:
                try:
                    hasher = hashlib.md5()
                    with open(file_path, "rb") as f:
                        for chunk in iter(lambda: f.read(8192), b""):
                            hasher.update(chunk)
                    file_hash = hasher.hexdigest()
                except Exception as e:
                    print(f"[WARN] 计算照片哈希失败 {file_path}: {e}")

            descriptions.append(
                {
                    "path": file_path,
                    "hash": file_hash,
                    "description": p.get("description", ""),
                    "has_baby": p.get("has_baby", True),
                    "scene": p.get("scene", ""),
                    "activity": p.get("activity", ""),
                }
            )

    if not descriptions:
        return

    try:
        # 从客户端配置获取 server_url, secret_key
        client_config_file = Path.home() / "Documents" / "CZRZ" / "config.json"
        if client_config_file.exists():
            with open(client_config_file, "r", encoding="utf-8") as f:
                client_config = json.load(f)
            server_url = client_config.get("server_url", "")
            secret_key = client_config.get("secret_key", "")
        else:
            server_url = ""
            secret_key = ""

        if not server_url:
            print("[WARN] 未配置 server_url，跳过保存照片描述")
            return

        path = "/czrz/photos/descriptions"
        body = json.dumps(
            {"client_id": client_id, "date": date, "photos": descriptions}
        )
        headers = {"Content-Type": "application/json"}
        if client_id and secret_key:
            add_signature_headers(headers, client_id, secret_key, "POST", path, body)

        resp = req.post(
            f"{server_url}{path}",
            data=body,
            headers=headers,
            timeout=1200,
            verify=False,
        )

        if resp.status_code == 200:
            print(f"[INFO] 已保存{len(descriptions)}条照片描述到服务端")
        else:
            print(f"[WARN] 保存照片描述失败: {resp.status_code}")
    except Exception as e:
        print(f"[WARN] 保存照片描述异常: {e}")


# =====================
# CLI
# =====================

if __name__ == "__main__":
    result = select_best_photo()

    print(json.dumps(result, ensure_ascii=False, indent=2))
