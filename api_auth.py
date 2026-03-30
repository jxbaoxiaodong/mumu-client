"""
API认证模块 - 验证客户端请求合法性
"""

import hashlib
import time
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict


def get_data_dir():
    """获取数据目录（客户端始终使用用户文档目录）"""
    # 客户端数据始终存储在用户文档目录
    data_dir = Path.home() / "Documents" / "CZRZ" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# API密钥存储文件
API_KEYS_FILE = get_data_dir() / "api_keys.json"

# 请求记录（用于频率限制）
request_log: Dict[str, list] = {}

# 配置 - 分层频率限制策略
# 根据客户端使用模式优化
RATE_LIMITS = {
    "heartbeat": {
        "limit": 120,  # 心跳：每2分钟120次（约1次/秒）
        "window": 120,  # 2分钟窗口
        "description": "心跳连接",
    },
    "normal": {
        "limit": 180,  # 普通API：每3分钟180次（约1次/秒）
        "window": 180,  # 3分钟窗口
        "description": "普通API请求",
    },
    "sensitive": {
        "limit": 30,  # 敏感操作：每5分钟30次
        "window": 300,  # 5分钟窗口
        "description": "敏感操作（AI生成、上传等）",
    },
}

# 根据路径判断请求类型
HEARTBEAT_PATHS = [
    "/czrz/client/heartbeat",
    "/czrz/heartbeat",
    "/czrz/health",
    "/czrz/ping",
]
SENSITIVE_PATHS = [
    "/czrz/baby/log",  # AI日志生成
    "/czrz/upload",  # 文件上传
    "/czrz/voice",  # 语音上传
    "/czrz/photo",  # 照片相关
    "/admin/",  # 管理后台
]

BLOCK_DURATION = 600  # 封禁时长延长到10分钟
blocked_ips: Dict[str, float] = {}


def load_api_keys() -> Dict:
    """加载所有API密钥"""
    if API_KEYS_FILE.exists():
        try:
            import json

            with open(API_KEYS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}


def save_api_keys(keys: Dict):
    """保存API密钥"""
    import json

    with open(API_KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(keys, f, indent=2, ensure_ascii=False)


def generate_api_key(client_id: str) -> str:
    """为客户端生成API密钥"""
    timestamp = str(int(time.time()))
    base = f"{client_id}:{timestamp}:czrz_secret"
    key = hashlib.sha256(base.encode()).hexdigest()[:32]
    return key


def create_api_key(client_id: str, client_info: dict = None) -> str:
    """创建并保存API密钥"""
    keys = load_api_keys()

    # 生成新密钥
    api_key = generate_api_key(client_id)

    # 保存密钥信息
    keys[client_id] = {
        "key": api_key,
        "created_at": datetime.now().isoformat(),
        "last_used": None,
        "use_count": 0,
        "enabled": True,
        "client_info": client_info or {},
    }

    save_api_keys(keys)
    return api_key


def verify_api_key(client_id: str, api_key: str) -> bool:
    """验证API密钥是否有效"""
    if not client_id or not api_key:
        return False

    keys = load_api_keys()

    if client_id not in keys:
        return False

    key_info = keys[client_id]

    # 检查是否启用
    if not key_info.get("enabled", True):
        return False

    # 验证密钥
    if key_info["key"] != api_key:
        return False

    # 更新使用记录
    key_info["last_used"] = datetime.now().isoformat()
    key_info["use_count"] = key_info.get("use_count", 0) + 1
    save_api_keys(keys)

    return True


def get_rate_limit_type(request_path: str = "") -> str:
    """根据请求路径确定限制类型"""
    if any(request_path.startswith(path) for path in HEARTBEAT_PATHS):
        return "heartbeat"
    elif any(request_path.startswith(path) for path in SENSITIVE_PATHS):
        return "sensitive"
    else:
        return "normal"


def cleanup_expired_records():
    """清理过期的频率限制记录"""
    now = time.time()

    # 清理 request_log 中过期的记录
    expired_keys = []
    for key, timestamps in list(request_log.items()):
        # 获取限制类型
        if ":" in key:
            ip_part, limit_type_part = key.split(":", 1)
            if limit_type_part in RATE_LIMITS:
                window = RATE_LIMITS[limit_type_part]["window"]
                # 保留最近 window 秒内的记录
                request_log[key] = [t for t in timestamps if now - t < window]
                if not request_log[key]:  # 如果没有有效记录，标记为过期
                    expired_keys.append(key)

    # 删除空记录
    for key in expired_keys:
        del request_log[key]

    # 清理过期的 blocked_ips
    expired_ips = [
        ip
        for ip, block_time in list(blocked_ips.items())
        if now - block_time > BLOCK_DURATION
    ]
    for ip in expired_ips:
        del blocked_ips[ip]

    return len(expired_keys) + len(expired_ips)


def check_rate_limit(client_ip: str, request_path: str = "") -> tuple:
    """
    检查请求频率限制（分层策略）

    Args:
        client_ip: 客户端IP地址
        request_path: 请求路径（可选，用于确定限制类型）

    Returns:
        (allowed: bool, remaining: int, reset_time: int, limit_type: str)
    """
    now = time.time()

    # 每次处理请求时，有1%的概率触发清理过期记录（避免频繁清理）
    import random

    if random.random() < 0.01:
        cleaned = cleanup_expired_records()
        if cleaned > 0:
            import logging

            logging.getLogger("czrz_server").debug(f"清理了 {cleaned} 个过期记录")

    # 确定限制类型
    limit_type = get_rate_limit_type(request_path)
    limit_config = RATE_LIMITS[limit_type]
    limit = limit_config["limit"]
    window = limit_config["window"]

    # 检查是否被封禁
    if client_ip in blocked_ips:
        if now - blocked_ips[client_ip] < BLOCK_DURATION:
            return False, 0, int(blocked_ips[client_ip] + BLOCK_DURATION), limit_type
        else:
            # 解封
            del blocked_ips[client_ip]

    # 为每个IP和限制类型创建独立的记录
    key = f"{client_ip}:{limit_type}"

    # 清理过期记录
    if key in request_log:
        request_log[key] = [t for t in request_log[key] if now - t < window]
    else:
        request_log[key] = []

    # 检查频率
    if len(request_log[key]) >= limit:
        # 封禁IP（所有类型）
        blocked_ips[client_ip] = now
        return False, 0, int(now + BLOCK_DURATION), limit_type

    # 记录请求
    request_log[key].append(now)

    remaining = limit - len(request_log[key])
    reset_time = int(now + window)

    return True, remaining, reset_time, limit_type


def check_rate_limit_simple(client_ip: str) -> tuple:
    """
    简化版本（向后兼容）
    使用普通限制类型
    """
    allowed, remaining, reset_time, _ = check_rate_limit(client_ip, "")
    return allowed, remaining, reset_time


def revoke_api_key(client_id: str) -> bool:
    """吊销客户端API密钥"""
    keys = load_api_keys()

    if client_id in keys:
        keys[client_id]["enabled"] = False
        save_api_keys(keys)
        return True

    return False


def get_api_key_stats(client_id: str = None) -> Dict:
    """获取API密钥统计信息"""
    keys = load_api_keys()

    if client_id:
        return keys.get(client_id, {})

    # 返回所有统计
    total_keys = len(keys)
    active_keys = sum(1 for k in keys.values() if k.get("enabled", True))
    total_requests = sum(k.get("use_count", 0) for k in keys.values())

    return {
        "total_keys": total_keys,
        "active_keys": active_keys,
        "total_requests": total_requests,
        "blocked_ips": len(blocked_ips),
        "keys": keys,
    }


# FastAPI依赖函数
async def verify_request(request):
    """FastAPI依赖：验证请求"""
    from fastapi import HTTPException, Header

    # 获取客户端IP
    client_ip = request.client.host
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()

    # 检查频率限制（使用请求路径确定限制类型）
    request_path = request.url.path
    allowed, remaining, reset_time, limit_type = check_rate_limit(
        client_ip, request_path
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"请求过于频繁（{RATE_LIMITS[limit_type]['description']}），请稍后再试",
        )

    # 获取API密钥
    api_key = request.headers.get("X-API-Key")
    client_id = request.headers.get("X-Client-ID")

    # 验证密钥
    if not verify_api_key(client_id, api_key):
        raise HTTPException(status_code=401, detail="无效的API密钥")

    return {"client_id": client_id, "client_ip": client_ip}


# Flask装饰器
from functools import wraps


def require_api_key(f):
    """Flask装饰器：要求API密钥"""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import request, jsonify

        # 获取客户端IP
        client_ip = request.remote_addr
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        # 检查频率限制（使用请求路径确定限制类型）
        request_path = request.path
        allowed, remaining, reset_time, limit_type = check_rate_limit(
            client_ip, request_path
        )
        if not allowed:
            return jsonify(
                {
                    "success": False,
                    "message": f"请求过于频繁（{RATE_LIMITS[limit_type]['description']}），请稍后再试",
                    "retry_after": reset_time - int(time.time()),
                    "limit_type": limit_type,
                    "limit": RATE_LIMITS[limit_type]["limit"],
                    "window": RATE_LIMITS[limit_type]["window"],
                }
            ), 429

        # 获取API密钥
        api_key = request.headers.get("X-API-Key")
        client_id = request.headers.get("X-Client-ID")

        # 验证密钥
        if not verify_api_key(client_id, api_key):
            return jsonify({"success": False, "message": "无效的API密钥"}), 401

        # 添加请求头信息到kwargs
        kwargs["client_id"] = client_id
        kwargs["client_ip"] = client_ip

        return f(*args, **kwargs)

    return decorated_function


if __name__ == "__main__":
    # 测试
    client_id = "test_client"

    # 创建密钥
    key = create_api_key(client_id, {"baby_name": "测试宝宝"})
    print(f"生成API密钥: {key}")

    # 验证密钥
    is_valid = verify_api_key(client_id, key)
    print(f"验证结果: {is_valid}")

    # 检查频率限制
    allowed, remaining, _ = check_rate_limit("127.0.0.1")
    print(f"频率限制: 允许={allowed}, 剩余={remaining}")

    # 统计
    stats = get_api_key_stats()
    print(f"统计: {stats}")
