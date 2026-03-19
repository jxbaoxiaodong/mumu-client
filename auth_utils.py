"""
认证工具模块
1. 动态安装密钥 - 防止扫描器
2. HMAC 签名验证 - API 安全通信
"""

import hashlib
import hmac
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any


INSTALL_KEY_SEED = "czrz-installer-v2"
SIGNATURE_EXPIRY = 300  # 签名有效期 5 分钟


def get_install_key(date_str: Optional[str] = None) -> str:
    """
    生成安装密钥（基于 UTC 日期）

    Args:
        date_str: 日期字符串 "YYYY-MM-DD"，默认今天

    Returns:
        格式: "xxxx-xxxx-xxxx"
    """
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    combined = f"{INSTALL_KEY_SEED}:{date_str}"
    hash_val = hashlib.md5(combined.encode()).hexdigest()
    return f"{hash_val[:4]}-{hash_val[4:8]}-{hash_val[8:12]}"


def verify_install_key(provided_key: str) -> bool:
    """
    验证安装密钥（允许今天和昨天的密钥）

    Args:
        provided_key: 客户端提供的密钥

    Returns:
        是否有效
    """
    if not provided_key:
        return False

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (
        datetime.now(timezone.utc) - __import__("datetime").timedelta(days=1)
    ).strftime("%Y-%m-%d")

    expected_today = get_install_key(today)
    expected_yesterday = get_install_key(yesterday)

    return provided_key == expected_today or provided_key == expected_yesterday


def generate_secret_key(client_id: str) -> str:
    """
    为客户端生成 secret_key

    Args:
        client_id: 客户端 ID

    Returns:
        32 字符的密钥
    """
    import secrets

    return secrets.token_hex(16)


def create_signature(
    secret_key: str, method: str, path: str, timestamp: int, body: str = ""
) -> str:
    """
    创建 HMAC 签名

    签名内容: timestamp + method + path + body

    Args:
        secret_key: 客户端密钥
        method: HTTP 方法 (GET/POST/PUT/DELETE)
        path: 请求路径
        timestamp: 时间戳
        body: 请求体 JSON 字符串

    Returns:
        签名字符串
    """
    message = f"{timestamp}{method.upper()}{path}{body}"
    signature = hmac.new(
        secret_key.encode(), message.encode(), hashlib.sha256
    ).hexdigest()
    return signature


def verify_signature(
    secret_key: str,
    provided_signature: str,
    method: str,
    path: str,
    timestamp: int,
    body: str = "",
) -> Dict[str, Any]:
    """
    验证 HMAC 签名

    Args:
        secret_key: 客户端密钥
        provided_signature: 客户端提供的签名
        method: HTTP 方法
        path: 请求路径
        timestamp: 时间戳
        body: 请求体

    Returns:
        {"valid": bool, "error": str}
    """
    current_time = int(time.time())

    if abs(current_time - timestamp) > SIGNATURE_EXPIRY:
        return {"valid": False, "error": "签名已过期"}

    expected = create_signature(secret_key, method, path, timestamp, body)

    if not hmac.compare_digest(expected, provided_signature):
        return {"valid": False, "error": "签名无效"}

    return {"valid": True}


SKIP_SIGNATURE_PATHS = [
    "/czrz/register",  # 注册时需要（无secret_key，使用install_key）
    "/czrz/health",  # 健康检查（公开）
    "/czrz/ping",  # 探测（公开）
    # 注意：以下端点需要签名验证（客户端已实现signed_request）
    # "/czrz/weather" - 天气查询（有AI兜底）
    # "/czrz/news" - 获取新闻列表
    # "/czrz/news/china" - 获取国内新闻
    # "/czrz/news/global" - 获取国际新闻
    # "/czrz/today-news" - 获取今日新闻
    # "/czrz/notifications" - 获取通知
    # "/czrz/notifications/mark-read" - 标记通知已读
    "/czrz/version/latest",  # 版本检查（公开）
    "/czrz/qrcode",  # 获取二维码（只读配置）
    "/czrz/client/server-config",  # 初始配置（无secret_key）
    "/czrz/client/heartbeat",  # 心跳（使用install_key验证）
    "/czrz/client/token-check",  # Token检查（已验证）
    "/czrz/client/download/",  # 下载（文件分发）
    "/admin/",  # 管理后台（独立认证）
    "/static/",  # 静态文件（公开）
    "/favicon.ico",  # 图标（公开）
    "/api/",  # 健康AI代理（路径自己处理签名验证）
]

SKIP_SIGNATURE_PREFIXES = []


def should_skip_signature(path: str) -> bool:
    """
    判断路径是否跳过签名验证

    Args:
        path: 请求路径

    Returns:
        是否跳过
    """
    for skip_path in SKIP_SIGNATURE_PATHS:
        if path.startswith(skip_path):
            return True

    for prefix in SKIP_SIGNATURE_PREFIXES:
        if path.startswith(prefix):
            return True

    return False
