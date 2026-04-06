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
from urllib.parse import urlencode
from photo_status import (
    PHOTO_STATUS_OK,
    classify_terminal_photo_error,
    build_blocked_photo_record,
    build_blurry_photo_record,
    build_duplicate_photo_record,
    build_other_error_photo_record,
)

CLIENT_USER_AGENT = "CZRZ-Client/2.0"


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


def _running_in_server_process() -> bool:
    main_module = sys.modules.get("__main__")
    main_file = str(getattr(main_module, "__file__", "") or "")
    return main_file.endswith("server_public.py")


def _check_token_quota() -> tuple:
    """检查 token 配额，返回 (can_use, used, limit)"""
    try:
        config = get_client_config()
        client_id = config.get("client_id")
        server_url = config.get("server_url", "")
        if not client_id or not server_url:
            return True, 0, 0

        resp = _signed_server_request(
            "GET",
            "/czrz/client/token-check",
            params={"client_id": client_id},
            timeout=30,
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
        config = get_client_config()
        client_id = config.get("client_id")
        server_url = config.get("server_url", "")
        if not client_id or not server_url:
            return

        resp = _signed_server_request(
            "POST",
            "/czrz/client/token-record",
            json_body={
                "client_id": client_id,
                "usage": usage,
                "operation": operation,
            },
            timeout=30,
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
PHASH_THRESHOLD = 8


def get_client_config():
    """获取客户端配置（server_url, client_id, secret_key）"""
    try:
        if _running_in_server_process():
            return {"server_url": "", "client_id": "", "secret_key": ""}

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


def is_content_inspection_block_error(error) -> bool:
    info = classify_terminal_photo_error(error)
    if not info:
        return False
    return info.get("processed_status") == "blocked"


def _save_processed_photo_descriptions_to_server(client_id: str, date: str, photos: list):
    if photos:
        save_photo_descriptions_to_server(client_id, date, photos)
    return photos


def save_blocked_photo_descriptions_to_server(client_id: str, date: str, photo_paths: list):
    """将内容审核拦截图片标记为已处理，避免后续刷新反复重试。"""
    blocked_photos = [
        build_blocked_photo_record(path)
        for path in (photo_paths or [])
        if path
    ]
    return _save_processed_photo_descriptions_to_server(
        client_id,
        date,
        blocked_photos,
    )


def save_other_error_photo_descriptions_to_server(
    client_id: str,
    date: str,
    photo_paths: list,
    error_detail: str = "",
    error_code: str = "other_error",
):
    """将明确不可重试的图片异常标记为已处理。"""
    errored_photos = [
        build_other_error_photo_record(
            path,
            error_detail=error_detail,
            error_code=error_code,
        )
        for path in (photo_paths or [])
        if path
    ]
    return _save_processed_photo_descriptions_to_server(
        client_id,
        date,
        errored_photos,
    )


def _signed_server_request(
    method: str,
    path: str,
    params: dict = None,
    json_body: dict = None,
    timeout: int = 30,
):
    """向服务端发起签名请求，避免旧直连/裸请求残留。"""
    client_config = get_client_config()
    server_url = client_config.get("server_url", "")
    client_id = client_config.get("client_id", "")
    secret_key = client_config.get("secret_key", "")

    if not server_url:
        raise Exception("未配置服务端地址")

    signed_path = path
    if params:
        query_string = urlencode(params, doseq=True)
        if query_string:
            signed_path = f"{path}?{query_string}"

    headers = {"User-Agent": CLIENT_USER_AGENT}
    request_kwargs = {
        "params": params,
        "headers": headers,
        "timeout": timeout,
        "verify": False,
    }
    body_str = ""

    if json_body is not None:
        body_str = json.dumps(json_body)
        headers["Content-Type"] = "application/json"
        request_kwargs["data"] = body_str

    if client_id and secret_key:
        add_signature_headers(
            headers,
            client_id,
            secret_key,
            method,
            signed_path,
            body_str,
        )

    return requests.request(method, f"{server_url}{path}", **request_kwargs)


def _safe_json_response(response, context: str) -> dict:
    """解析代理返回，确保拿到 JSON 而不是 Cloudflare/网关 HTML。"""
    try:
        return response.json()
    except ValueError:
        snippet = (response.text or "").strip().replace("\n", " ")[:160]
        if response.status_code >= 500:
            raise Exception(f"{context}服务端错误: HTTP {response.status_code}")
        raise Exception(
            f"{context}返回非JSON响应: HTTP {response.status_code}"
            + (f" {snippet}" if snippet else "")
        )


def _post_proxy_json(
    url: str,
    body_str: str,
    headers: dict,
    *,
    timeout: int,
    context: str,
    retries: int = 2,
) -> dict:
    """向服务端代理发请求，自动处理 502/503/504 和非 JSON 返回。"""
    last_error = None
    for attempt in range(retries + 1):
        try:
            response = requests.post(
                url,
                data=body_str.encode("utf-8"),
                headers=headers,
                timeout=timeout,
                verify=False,
            )
        except requests.exceptions.RequestException as e:
            last_error = f"{context}请求失败: {e}"
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            raise Exception(last_error)

        if response.status_code in {502, 503, 504}:
            last_error = f"{context}服务端错误: HTTP {response.status_code}"
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            raise Exception(last_error)

        return _safe_json_response(response, context)

    raise Exception(last_error or f"{context}请求失败")


def _default_child_features():
    return {
        "top_interests": [],
        "personality_highs": [],
        "diversity_hints": "暂无画像数据",
        "familiarity": 0,
    }


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
    default_result = _default_child_features()

    try:
        client_config = get_client_config()
        server_url = client_config.get("server_url", "")
        client_id = client_config.get("client_id", "")

        if not server_url or not client_id:
            print("[DEBUG] 未配置服务端URL，跳过画像获取")
            return default_result

        # 通过服务端代理调用特征API
        print(f"[DEBUG] 获取宝宝画像: {server_url}/api/ai/features/{child_id}")
        response = _signed_server_request(
            "GET",
            f"/api/ai/features/{child_id}",
            timeout=1200,
        )

        if response.status_code == 200:
            data = response.json()
            print(f"[DEBUG] 画像API返回: {data}")
            if data.get("success"):
                features = data.get("features", {})
                interests = features.get("interest", {})
                personality = features.get("personality", {})

                # 提取主要兴趣（分数>7的）
                top_interests = []
                for key, value in interests.items():
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
                for key, value in personality.items():
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
        else:
            print(f"[WARN] 获取宝宝画像失败，状态码: {response.status_code}")
    except Exception as e:
        print(f"[WARN] 获取宝宝画像失败: {e}")

    # 返回默认值
    return default_result


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


# =====================
# 图片压缩
# =====================


def compress_image(path):

    img = Image.open(path)

    img.thumbnail((MAX_IMAGE_SIZE, MAX_IMAGE_SIZE))

    buf = io.BytesIO()

    img.convert("RGB").save(buf, format="JPEG", quality=85)

    return base64.b64encode(buf.getvalue()).decode()


def _extract_json_payload(text):
    """从模型输出中提取 JSON 对象。"""
    raw = (text or "").strip()
    if not raw:
        return {}

    candidates = [raw]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    candidates = fenced + candidates

    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return {}


def describe_photo_batch(paths, child_id=None):
    """
    只对照片做增量描述和质量识别，不负责精选决策。

    Returns:
        dict: {
            "selected": [],
            "blurry": [模糊照片路径],
            "duplicates": [[相似照片组路径]],
            "no_baby": [无宝宝照片路径],
            "photos": {文件名: {description, has_baby, scene, activity}},
            "blurry_filenames": [...],
            "duplicate_filenames": [[...]],
            "no_baby_filenames": [...],
            "reasons": []
        }
    """
    if not paths:
        return {
            "selected": [],
            "blurry": [],
            "duplicates": [],
            "no_baby": [],
            "photos": {},
            "blurry_filenames": [],
            "duplicate_filenames": [],
            "no_baby_filenames": [],
            "reasons": [],
        }

    config = get_api_config()
    proxy_url = config.get("proxy_url", "")
    client_id = config.get("client_id", "") or child_id
    secret_key = config.get("secret_key", "")

    if not proxy_url or not client_id:
        raise Exception("未配置服务端地址或客户端ID")

    batch = []
    for path in paths:
        filename = os.path.basename(path)
        batch.append((filename, compress_image(path)))

    content = [
        {
            "type": "text",
            "text": """
你是宝宝成长记录助手。

请分析这些照片，完成以下任务：

【任务1：为每张照片生成描述】
为每张照片提供：
- description: 照片内容描述（50字以内）
- has_baby: 照片中是否有宝宝（true/false）
- scene: 场景（如：公园、家里、商场）
- activity: 活动（如：玩耍、吃饭、睡觉）

【任务2：识别问题照片】
- blurry: 明显模糊的照片
- duplicates: 内容相似的照片组（连拍、同一场景）
- no_baby: 照片中没有宝宝

只返回 JSON 格式：

{
  "photos": {
    "照片1.jpg": {"description": "宝宝在公园草地上奔跑", "has_baby": true, "scene": "公园", "activity": "玩耍"},
    "照片2.jpg": {"description": "公园的树荫", "has_baby": false, "scene": "公园", "activity": ""}
  },
  "blurry": ["模糊照片.jpg"],
  "duplicates": [["相似A.jpg", "相似B.jpg"]],
  "no_baby": ["无宝宝照片.jpg"]
}

注意：
1. photos 字段必须包含所有照片的描述
2. blurry、duplicates、no_baby 可以为空数组
""",
        }
    ]

    for filename, b64 in batch:
        content.append({"type": "text", "text": f"文件名: {filename}"})
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        )

    request_body = {
        "client_id": client_id,
        "messages": [{"role": "user", "content": content}],
        "operation": "describe_photos",
    }
    body_str = json.dumps(request_body)
    headers = {"Content-Type": "application/json", "User-Agent": CLIENT_USER_AGENT}
    path = "/czrz/ai/proxy/vision"

    if client_id and secret_key:
        add_signature_headers(headers, client_id, secret_key, "POST", path, body_str)

    try:
        res = _post_proxy_json(
            proxy_url,
            body_str,
            headers,
            timeout=1200,
            context="视觉代理",
        )
        if not res.get("success"):
            error = res.get("error", "未知错误")
            message = res.get("message", error)
            if error in {"QUOTA_EXCEEDED", "QUOTA_EXHAUSTED"}:
                raise Exception(f"ALL_MODELS_EXHAUSTED:{message}")
            raise Exception(message)

        content_result = (res.get("result") or {}).get("content", "")
        data = _extract_json_payload(content_result)
        if not data:
            raise Exception("照片描述解析失败")
    except requests.exceptions.RequestException as e:
        raise Exception(f"视觉代理请求失败: {str(e)}")

    blurry_filenames = data.get("blurry", []) or []
    blurry_paths = [p for p in paths if os.path.basename(p) in blurry_filenames]

    duplicate_groups = data.get("duplicates", []) or []
    duplicate_path_groups = []
    for group in duplicate_groups:
        path_group = [p for p in paths if os.path.basename(p) in group]
        if len(path_group) > 1:
            duplicate_path_groups.append(path_group)

    no_baby_filenames = data.get("no_baby", []) or []
    no_baby_paths = [p for p in paths if os.path.basename(p) in no_baby_filenames]

    return {
        "selected": [],
        "blurry": blurry_paths,
        "duplicates": duplicate_path_groups,
        "no_baby": no_baby_paths,
        "photos": data.get("photos", {}) or {},
        "blurry_filenames": blurry_filenames,
        "duplicate_filenames": duplicate_groups,
        "no_baby_filenames": no_baby_filenames,
        "reasons": [],
    }


def _call_text_proxy(prompt, operation="text_call", max_tokens=500, client_id=None):
    """通过服务端代理调用文本模型。"""
    client_config = get_client_config()
    server_url = client_config.get("server_url", "")
    signed_client_id = client_config.get("client_id", "") or client_id
    secret_key = client_config.get("secret_key", "")

    if not server_url or not signed_client_id:
        raise Exception("未配置服务端地址或客户端ID")

    request_body = {
        "client_id": signed_client_id,
        "prompt": prompt,
        "max_tokens": max_tokens,
        "operation": operation,
    }
    body_str = json.dumps(
        request_body,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    headers = {"Content-Type": "application/json", "User-Agent": CLIENT_USER_AGENT}
    path = "/czrz/ai/proxy/text"
    if signed_client_id and secret_key:
        add_signature_headers(headers, signed_client_id, secret_key, "POST", path, body_str)

    res = _post_proxy_json(
        f"{server_url}{path}",
        body_str,
        headers,
        timeout=1200,
        context="文本代理",
    )
    if not res.get("success"):
        error = res.get("error", "未知错误")
        message = res.get("message", error)
        if error in {"QUOTA_EXCEEDED", "QUOTA_EXHAUSTED"}:
            raise Exception(f"ALL_MODELS_EXHAUSTED:{message}")
        raise Exception(message)

    return (res.get("result") or {}).get("content", "")


def select_featured_photo_from_descriptions(photo_records, select_n=1, child_id=None):
    """
    基于已保存的文字描述选择精选照片，避免为选图重复读取整天图片。
    """
    candidates = []
    seen = set()
    for item in photo_records or []:
        path = item.get("path", "") or ""
        filename = item.get("filename") or (os.path.basename(path) if path else "")
        if not filename or filename in seen:
            continue
        seen.add(filename)
        candidates.append(
            {
                "filename": filename,
                "path": path,
                "description": (item.get("description") or "").strip(),
                "has_baby": bool(item.get("has_baby", True)),
                "scene": (item.get("scene") or "").strip(),
                "activity": (item.get("activity") or "").strip(),
            }
        )

    if not candidates:
        return {"selected": [], "selected_filenames": [], "reasons": []}

    eligible = [item for item in candidates if item.get("has_baby")]
    if not eligible:
        eligible = list(candidates)

    if len(eligible) <= select_n:
        chosen = eligible[:select_n]
        return {
            "selected": [item.get("path") or item["filename"] for item in chosen],
            "selected_filenames": [item["filename"] for item in chosen],
            "reasons": [item.get("description") or "当天唯一候选照片" for item in chosen],
        }

    child_features = get_child_features(child_id) if child_id else {}
    interest_hint = ""
    if child_features.get("top_interests"):
        interest_hint = f"宝宝近期兴趣：{'、'.join(child_features['top_interests'][:3])}"

    personality_hint = ""
    if child_features.get("personality_highs"):
        personality_hint = f"宝宝性格：{'、'.join(child_features['personality_highs'][:3])}"

    candidate_lines = []
    for index, item in enumerate(eligible[:60], start=1):
        candidate_lines.append(
            f"{index}. filename={item['filename']} | has_baby={'true' if item['has_baby'] else 'false'} | "
            f"scene={item['scene'] or '未知'} | activity={item['activity'] or '未知'} | "
            f"description={item['description'] or '无描述'}"
        )

    prompt = f"""你是宝宝成长记录助手。

现在不要重新看图片，只能根据已经生成好的照片描述，从候选里挑出最适合作为当天精选封面的 {select_n} 张照片。

优先原则：
1. 优先选择 has_baby=true 的照片
2. 宝宝动作、表情、互动明确，适合代表这一天
3. 场景和活动有记忆点，不要选纯背景或信息太弱的照片
4. 如果描述看起来像重复角度或无明显主体，不优先
5. 理由要具体，基于提供的描述，不要虚构

{interest_hint}
{personality_hint}

候选照片：
{chr(10).join(candidate_lines)}

只返回 JSON：
{{
  "selected": ["xxx.jpg"],
  "reasons": ["为什么选它，20字以内"]
}}"""

    raw = _call_text_proxy(
        prompt,
        operation="select_featured_photo",
        max_tokens=300,
        client_id=child_id,
    )
    payload = _extract_json_payload(raw)
    selected_filenames = [
        name for name in (payload.get("selected") or []) if any(item["filename"] == name for item in eligible)
    ][:select_n]

    if not selected_filenames:
        selected_filenames = [eligible[0]["filename"]]

    eligible_map = {item["filename"]: item for item in eligible}
    reasons = payload.get("reasons") or []
    if not reasons:
        reasons = [
            eligible_map[name].get("description") or "当天代表性照片"
            for name in selected_filenames
        ]

    return {
        "selected": [
            eligible_map[name].get("path") or name for name in selected_filenames
        ],
        "selected_filenames": selected_filenames,
        "reasons": reasons[: len(selected_filenames)],
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

    selection_reason = ""
    try:
        describe_result = describe_photo_batch(images, child_id=child_id)
        analysis = analyze_all_photos(
            images,
            max_photos=len(images),
            ai_result=describe_result,
        )
        selection = select_featured_photo_from_descriptions(
            analysis.get("photos", []),
            select_n=1,
            child_id=child_id,
        )
        best = (
            os.path.basename(selection["selected"][0])
            if selection.get("selected")
            else os.path.basename(images[0])
        )
        selection_reason = (
            selection.get("reasons", [None])[0] if selection.get("reasons") else ""
        )
    except Exception as e:
        print(f"[WARN] 选择精选照片失败: {e}，使用第一张")
        best = os.path.basename(images[0])

    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "photo_folder": folder,
        "best_photo": best,
        "stats": {
            "original": original_count,
            "after_dedup": dedup_count,
            "after_blur_filter": clear_count,
        },
        "method": "description_select",
        "selection_reason": selection_reason,
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
            if _running_in_server_process():
                data_dir = Path(__file__).parent / "data" / "server_photo_index"
            else:
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

    # 先做图片描述，再基于描述选精选，避免把“描述”和“精选”绑死在一次调用里
    all_blurry = []
    all_duplicates = []
    selection_reason = ""

    try:
        describe_result = describe_photo_batch(images, child_id=client_id)
        analysis = analyze_all_photos(
            images,
            max_photos=len(images),
            ai_result=describe_result,
        )
        selection = select_featured_photo_from_descriptions(
            analysis.get("photos", []),
            select_n=1,
            child_id=client_id,
        )
        best = (
            os.path.basename(selection["selected"][0])
            if selection.get("selected")
            else os.path.basename(images[0])
        )
        all_blurry = describe_result.get("blurry_filenames", [])
        all_duplicates = describe_result.get("duplicate_filenames", [])
        selection_reason = (
            selection.get("reasons", [None])[0] if selection.get("reasons") else ""
        )
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
        "method": "description_select",
        "selection_reason": selection_reason,
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

    # 先描述，再根据描述挑选
    try:
        describe_result = describe_photo_batch(images)
        analysis = analyze_all_photos(
            images,
            max_photos=len(images),
            ai_result=describe_result,
        )
        result = select_featured_photo_from_descriptions(
            analysis.get("photos", []), select_n=select_n
        )
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
        headers = {"Content-Type": "application/json", "User-Agent": CLIENT_USER_AGENT}
        path = "/czrz/ai/proxy/vision"

        if client_id and secret_key:
            add_signature_headers(
                headers, client_id, secret_key, "POST", path, body_str
            )

        res = _post_proxy_json(
            proxy_url,
            body_str,
            headers,
            timeout=1200,
            context="单图视觉代理",
        )

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
    "简练": "用温馨平淡、像真人随手记下生活的口吻来写，克制自然，不堆砌辞藻，少用感叹词和语气词，120字左右",
    "诗意": "用文艺清新的语言，可以加入诗意的比喻，如'阳光洒在脸上...'，150字左右",
    "东北腔": "用东北话的口吻写，幽默风趣，可以说'这孩子咋这么招人稀罕'、'杠杠的'等，100字左右",
    "详细": "详细记录当天的活动细节，时间、地点、做了什么，200字左右",
    "童趣": "用轻松可爱的语气，像给宝宝讲故事，可以说'今天宝宝是探险家...'，150字左右",
    "深情": "用温暖感人的语言，表达父母对宝宝的爱，可以说'看着你一天天长大...'，150字左右",
    "IT男风格": "用程序员风格写，可以用代码注释、技术术语，如'// 今日活动：户外探索 v1.0'，100字左右",
}


def analyze_all_photos(
    photo_paths: list,
    max_photos: int | None = None,
    client_id: str = None,
    date: str = None,
    ai_result: dict = None,
) -> dict:
    """
    从 ai_select 的结果中过滤出有效照片，不再调用 AI

    Args:
        photo_paths: 照片路径列表（用于降级时分析）
        max_photos: 最多分析的照片数量，None 或 <=0 表示不设上限
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

    if max_photos is None or max_photos <= 0:
        target_paths = list(photo_paths)
    else:
        target_paths = photo_paths[:max_photos]

    valid_photos = []
    processed_skip_photos = []
    all_scenes = set()
    all_activities = set()
    baby_count = 0
    scenery_count = 0

    for path in target_paths:
        filename = os.path.basename(path)
        photo_info = photos_dict.get(filename, {})

        if not photo_info:
            processed_skip_photos.append(
                build_other_error_photo_record(
                    path,
                    error_detail="AI未返回该照片结果，按已处理跳过",
                    error_code="missing_ai_result",
                )
            )
            continue

        if filename in blurry_filenames:
            processed_skip_photos.append(
                build_blurry_photo_record(path, source_info=photo_info)
            )
            continue
        if filename in dup_to_remove:
            processed_skip_photos.append(
                build_duplicate_photo_record(path, source_info=photo_info)
            )
            continue

        description = photo_info.get("description", "")
        has_baby = photo_info.get("has_baby", True)
        if filename in no_baby_filenames:
            has_baby = False
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
        combined_parts.append(f"{scenery_count}张场景照片（无人像也保留用于地点和氛围叙事）")

    descriptions = [p["description"] for p in valid_photos if p["description"]]
    if descriptions:
        combined_parts.append("活动：" + "、".join(descriptions[:10]))

    combined_summary = "。".join(combined_parts) if combined_parts else "照片分析完成"

    records_to_save = valid_photos + processed_skip_photos
    if client_id and date and records_to_save:
        try:
            save_photo_descriptions_to_server(client_id, date, records_to_save)
        except Exception as e:
            print(f"[WARN] 保存照片描述失败: {e}")

    return {
        "photos": valid_photos,
        "skipped_photos": processed_skip_photos,
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
            file_hash = (p.get("hash", "") or p.get("file_hash", "") or "").strip()
            file_path = p.get("path", "")
            if file_path and not file_hash:
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
                    "processed_status": p.get("processed_status", "") or PHOTO_STATUS_OK,
                    "processed_error_code": p.get("processed_error_code", "") or "",
                    "processed_error_detail": p.get("processed_error_detail", "") or "",
                }
            )

    if not descriptions:
        return

    try:
        client_config = get_client_config()
        server_url = client_config.get("server_url", "")
        secret_key = client_config.get("secret_key", "")

        if not server_url:
            print("[WARN] 未配置 server_url，跳过保存照片描述")
            return

        path = "/czrz/photos/descriptions"
        body = json.dumps(
            {"client_id": client_id, "date": date, "photos": descriptions}
        )
        headers = {"Content-Type": "application/json", "User-Agent": CLIENT_USER_AGENT}
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
