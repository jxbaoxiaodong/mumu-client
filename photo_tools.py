"""
照片工具模块 - 整合最佳照片选择和低质量照片检测
"""

import os
import base64
import io
import json
import hashlib
import hmac
import time
import requests
from PIL import Image, ExifTags, ImageFilter
from pathlib import Path
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


# =====================
# 配置
# =====================

MAX_IMAGE_SIZE = 1024
ROUND1_BATCH = 20
ROUND1_SELECT = 5
PHASH_THRESHOLD = 8


def get_photo_time(path):
    """
    获取照片拍摄时间（优先EXIF）
    返回时间戳（float），用于排序
    """
    try:
        img = Image.open(path)
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                name = ExifTags.TAGS.get(tag)
                if name == "DateTimeOriginal":
                    # 将EXIF时间字符串转换为时间戳
                    try:
                        from datetime import datetime

                        dt = datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
                        return dt.timestamp()
                    except:
                        pass
    except:
        pass
    return os.path.getmtime(path)


def is_blurry(path, threshold=80):
    """检测照片是否模糊（优先使用cv2，否则使用Pillow）"""
    if CV2_AVAILABLE:
        try:
            img = cv2.imread(str(path))
            if img is None:
                return True
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            variance = cv2.Laplacian(gray, cv2.CV_64F).var()
            return variance < threshold
        except:
            return True
    else:
        return is_blurry_pil(path, threshold)


def is_blurry_pil(path, threshold=100):
    """使用Pillow检测照片是否模糊（无需cv2依赖）"""
    try:
        img = Image.open(path).convert("L")
        edges = img.filter(ImageFilter.FIND_EDGES)
        pixels = list(edges.getdata())
        mean = sum(pixels) / len(pixels)
        variance = sum((p - mean) ** 2 for p in pixels) / len(pixels)
        return variance < threshold
    except:
        return True


def calculate_hash(path):
    """计算图片感知哈希（优先使用imagehash，否则使用Pillow缩略图对比）"""
    if IMAGEHASH_AVAILABLE:
        try:
            img = Image.open(path)
            return imagehash.phash(img)
        except:
            return None
    else:
        return calculate_thumbnail_hash(path)


def calculate_thumbnail_hash(path, size=32):
    """使用Pillow计算缩略图哈希（无需imagehash依赖）"""
    try:
        img = Image.open(path).convert("L")
        img = img.resize((size, size), Image.Resampling.LANCZOS)
        pixels = list(img.getdata())
        avg = sum(pixels) / len(pixels)
        bits = "".join("1" if p > avg else "0" for p in pixels)
        return bits
    except:
        return None


def thumbnail_similarity(hash1, hash2):
    """计算两个缩略图哈希的相似度（返回差异值，越小越相似）"""
    if not hash1 or not hash2 or len(hash1) != len(hash2):
        return 999
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))


def compress_image(path, max_size=MAX_IMAGE_SIZE):
    """压缩图片用于AI处理"""
    try:
        img = Image.open(path)
        img.thumbnail((max_size, max_size))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()
    except:
        return None


def call_qwen_vision(images, select_n, api_key=None):
    config = get_client_config()
    server_url = config.get("server_url", "")
    client_id = config.get("client_id", "")
    secret_key = config.get("secret_key", "")

    if not server_url or not client_id:
        raise Exception("未配置服务端地址或客户端ID")

    proxy_url = f"{server_url}/czrz/ai/proxy/vision"

    content = [
        {
            "type": "text",
            "text": f"""你是宝宝成长记录助手。

请从这些照片中选出最有代表性的 {select_n} 张。

优先考虑：
1. 宝宝表情自然、清晰
2. 有纪念意义的活动（如第一次站立、爬行等）
3. 光线充足、构图良好
4. 避免重复场景

只返回 JSON 格式：
{{"selected": ["文件名1", "文件名2", ...]}}""",
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
            "operation": "photo_analyze",
        }
        body_str = json.dumps(request_body)
        headers = {"Content-Type": "application/json"}
        path = "/czrz/ai/proxy/vision"

        if client_id and secret_key:
            add_signature_headers(
                headers, client_id, secret_key, "POST", path, body_str
            )

        response = requests.post(
            proxy_url,
            json=request_body,
            headers=headers,
            timeout=600,
            verify=False,
        )

        result = response.json()

        if not result.get("success"):
            error = result.get("error", "未知错误")
            message = result.get("message", error)
            if error == "QUOTA_EXCEEDED" or error == "QUOTA_EXHAUSTED":
                raise Exception(f"ALL_MODELS_EXHAUSTED:{message}")
            raise Exception(message)

        res = result.get("result", {})
        content_result = res.get("content", "")

        if content_result:
            return content_result

        raise Exception("代理API返回空结果")

    except requests.exceptions.RequestException as e:
        raise Exception(f"代理请求失败: {str(e)}")


def select_best_photo_from_list(photo_paths, api_key=None):
    """
    从照片列表中选择最佳照片
    返回最佳照片的路径
    """
    import json
    import re

    if not photo_paths:
        return None

    if len(photo_paths) == 1:
        return photo_paths[0]

    # 第一步：去重
    unique_photos = []
    hashes = []
    for path in photo_paths:
        h = calculate_hash(path)
        if h:
            is_dup = any(abs(h - existing) < PHASH_THRESHOLD for existing in hashes)
            if not is_dup:
                hashes.append(h)
                unique_photos.append(path)
        else:
            unique_photos.append(path)

    # 第二步：模糊过滤
    clear_photos = [p for p in unique_photos if not is_blurry(p)]
    if not clear_photos:
        clear_photos = unique_photos  # 如果都模糊，返回原列表

    # 第三步：AI选择
    if len(clear_photos) <= 5:
        # 照片少，直接选择
        return clear_photos[0]

    try:
        # 分批处理
        candidates = []
        for i in range(0, len(clear_photos), ROUND1_BATCH):
            batch = clear_photos[i : i + ROUND1_BATCH]
            batch_data = []
            for p in batch:
                b64 = compress_image(p)
                if b64:
                    batch_data.append((os.path.basename(p), b64))

            if batch_data:
                result = call_qwen_vision(
                    batch_data, min(ROUND1_SELECT, len(batch_data)), api_key
                )
                # 解析JSON
                try:
                    data = json.loads(result)
                    selected = data.get("selected", [])
                    candidates.extend(
                        [p for p in batch if os.path.basename(p) in selected]
                    )
                except:
                    # 尝试正则提取
                    match = re.search(r'\{[^}]*"selected"[^}]*\}', result)
                    if match:
                        data = json.loads(match.group())
                        selected = data.get("selected", [])
                        candidates.extend(
                            [p for p in batch if os.path.basename(p) in selected]
                        )

        if candidates:
            # 从候选者中再选一张
            if len(candidates) > 5:
                final_data = [
                    (os.path.basename(p), compress_image(p)) for p in candidates[:10]
                ]
                final_data = [x for x in final_data if x[1]]
                if final_data:
                    result = call_qwen_vision(final_data, 1, api_key)
                    try:
                        data = json.loads(result)
                        selected = data.get("selected", [])
                        if selected:
                            for p in candidates:
                                if os.path.basename(p) == selected[0]:
                                    return p
                    except:
                        pass
            return candidates[0]

    except Exception as e:
        print(f"AI选择照片失败: {e}")

    # 如果AI失败，返回第一张清晰的
    return clear_photos[0] if clear_photos else photo_paths[0]


def find_bad_photos(
    photo_paths, blur_threshold=80, phash_threshold=8, compare_window=5
):
    """
    检测重复和模糊照片（自动选择最佳方法）

    返回：{
        "duplicate": [重复照片路径列表],
        "blurry": [模糊照片路径列表],
        "all": [所有问题照片路径列表]
    }
    """
    # 按拍摄时间排序
    sorted_paths = sorted(photo_paths, key=get_photo_time)

    duplicates = []
    blurry = []
    recent_hashes = []

    for path in sorted_paths:
        # 模糊检测
        if is_blurry(path, blur_threshold):
            blurry.append(path)
            continue

        # 重复检测
        h = calculate_hash(path)
        if h:
            is_dup = False
            for old_h in recent_hashes:
                if IMAGEHASH_AVAILABLE:
                    if abs(h - old_h) < phash_threshold:
                        duplicates.append(path)
                        is_dup = True
                        break
                else:
                    if thumbnail_similarity(h, old_h) < 100:
                        duplicates.append(path)
                        is_dup = True
                        break

            if not is_dup:
                recent_hashes.append(h)
                if len(recent_hashes) > compare_window:
                    recent_hashes.pop(0)

    all_bad = list(set(duplicates + blurry))

    return {"duplicate": duplicates, "blurry": blurry, "all": all_bad}


def find_bad_photos_pil(photo_paths, blur_threshold=100, similarity_threshold=100):
    """
    使用Pillow检测重复和模糊照片（简单过滤，无需额外依赖）

    Args:
        photo_paths: 照片路径列表
        blur_threshold: 模糊检测阈值（越小越严格）
        similarity_threshold: 相似度阈值（越小越严格）

    Returns:
        {
            "duplicate": [重复照片路径列表],
            "blurry": [模糊照片路径列表],
            "duplicate_groups": [[相似照片组1], [相似照片组2]],
            "all": [所有问题照片路径列表],
            "method": "pillow"
        }
    """
    sorted_paths = sorted(photo_paths, key=get_photo_time)

    blurry = []
    valid_photos = []
    hashes = []

    for path in sorted_paths:
        if is_blurry_pil(path, blur_threshold):
            blurry.append(path)
        else:
            valid_photos.append(path)

    duplicate_groups = []
    duplicates = []
    used_indices = set()

    for i, path1 in enumerate(valid_photos):
        if i in used_indices:
            continue
        h1 = calculate_thumbnail_hash(path1)
        if not h1:
            continue

        similar_group = [path1]
        for j, path2 in enumerate(valid_photos[i + 1 :], start=i + 1):
            if j in used_indices:
                continue
            h2 = calculate_thumbnail_hash(path2)
            if h2 and thumbnail_similarity(h1, h2) < similarity_threshold:
                similar_group.append(path2)
                used_indices.add(j)

        if len(similar_group) > 1:
            duplicate_groups.append(similar_group)
            duplicates.extend(similar_group[1:])

    all_bad = list(set(duplicates + blurry))

    return {
        "duplicate": duplicates,
        "blurry": blurry,
        "duplicate_groups": duplicate_groups,
        "all": all_bad,
        "method": "pillow",
    }


def remove_photos_from_index(photo_paths, pm):
    """
    从索引中移除照片（不删除物理文件）
    pm: PhotoManager实例
    """
    removed = []
    for path in photo_paths:
        try:
            filename = os.path.basename(path)
            # 使用PhotoManager的移除方法
            if hasattr(pm, "remove_photo"):
                pm.remove_photo(filename)
            removed.append(filename)
        except Exception as e:
            print(f"移除照片失败 {path}: {e}")

    return removed


