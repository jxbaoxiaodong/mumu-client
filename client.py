#!/usr/bin/env python3
"""
成长记录系统 - 公网客户端最终版
从配置文件或环境变量读取服务端地址
"""

import os
import sys
import json
import uuid
import socket
import ctypes
import logging
import logging.handlers
import warnings
import tempfile
import hashlib
import hmac
import ipaddress
import atexit
import signal
from datetime import datetime, timedelta, timezone
import threading
import time
from pathlib import Path
from flask import Flask, render_template, request, jsonify, redirect, send_file
from werkzeug.utils import secure_filename
import requests
import urllib3

# 抑制特定域名的 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# ==================== 日志配置 ====================
def setup_logging():
    """配置客户端日志系统（兼容 PyInstaller 和普通运行）"""
    # 检测是否在 PyInstaller 打包环境中
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 打包环境 - 使用用户文档目录
        log_dir = Path.home() / "Documents" / "CZRZ" / "logs"
    else:
        # 普通运行环境 - 使用当前目录下的 logs
        log_dir = Path(__file__).parent / "logs"

    log_dir.mkdir(parents=True, exist_ok=True)

    # 创建日志记录器
    logger = logging.getLogger("czrz_client")
    logger.setLevel(logging.INFO)

    # 清除现有处理器
    logger.handlers.clear()

    # 文件处理器 - 按大小轮转，最大5MB，保留3个备份
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "client.log",
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # 格式化器
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


# 初始化日志
logger = setup_logging()
logger.info("=" * 60)
logger.info("客户端日志系统初始化完成")


DEFAULT_SERVER_DOMAIN = "xiaohexia.ftir.fun"

USER_DATA_DIR = Path.home() / "Documents" / "CZRZ"
USER_DATA_DIR.mkdir(parents=True, exist_ok=True)

USER_CONFIG_FILE = USER_DATA_DIR / "config.json"


def load_user_config():
    default_config = {
        "server_domain": DEFAULT_SERVER_DOMAIN,
        "server_url": f"https://{DEFAULT_SERVER_DOMAIN}",
        "client_id": None,
        "secret_key": None,
        "baby_name": "宝宝",
        "baby_gender": None,
        "baby_birthday": None,
        "user_city": None,
        "log_style": "简练",
        "custom_style": "",
        "media_folders": [],
        "admin_password": None,
        "is_paid": False,
        "subdomain": None,
        "public_url": None,
        "avatar_url": None,
        "client_version": "2.0.0",
        "ai_child_id": None,
    }

    if USER_CONFIG_FILE.exists():
        try:
            with open(USER_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                for key in default_config:
                    if key in saved:
                        default_config[key] = saved[key]
        except Exception as e:
            logger.warning(f"加载用户配置失败: {e}")

    env_domain = os.environ.get("SERVER_DOMAIN")
    if env_domain:
        default_config["server_domain"] = env_domain
        default_config["server_url"] = f"https://{env_domain}"

    return default_config


USER_CONFIG = load_user_config()


def save_user_config():
    try:
        with open(USER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(USER_CONFIG, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存用户配置失败: {e}")


INSTALL_KEY_SEED = "czrz-installer-v2"


def get_install_key(date_str=None):
    """生成安装密钥（与服务端算法相同）"""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    combined = f"{INSTALL_KEY_SEED}:{date_str}"
    hash_val = hashlib.md5(combined.encode()).hexdigest()
    return f"{hash_val[:4]}-{hash_val[4:8]}-{hash_val[8:12]}"


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


def update_server_domain(new_domain: str):
    USER_CONFIG["server_domain"] = new_domain
    USER_CONFIG["server_url"] = f"https://{new_domain}"
    save_user_config()
    logger.info(f"服务端域名已更新: {new_domain}")


class ClientConfig:
    def __init__(self):
        self._data = {
            "server": {
                "domain": USER_CONFIG["server_domain"],
                "url": USER_CONFIG["server_url"],
            },
            "ai_service": {
                "enabled": True,
                "url": USER_CONFIG["server_url"],
            },
            "local": {
                "port": int(os.environ.get("CLIENT_PORT", "3000")),
                "auto_open_browser": True,
            },
            "features": {
                "enable_cloudflare_tunnel": True,
                "enable_auto_update": True,
            },
        }

    def get(self, key, default=None):
        if key == "ai_service":
            result = self._data.get("ai_service", {}).copy()
            result["child_id"] = USER_CONFIG.get("ai_child_id", "")
            return result
        return self._data.get(key, default)

    def __getitem__(self, key):
        result = self.get(key)
        if result is None:
            raise KeyError(key)
        return result


CLIENT_CONFIG = ClientConfig()


# ==================== 权限控制装饰器 ====================
from functools import wraps


def require_local_or_password_real(f):
    """
    权限控制装饰器：本地连接免密码，远程连接需要密码
    用于保护所有写操作API

    注意：此装饰器已禁用，保留代码供将来需要时恢复
    """

    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 检查是否本地连接
        remote_addr = request.remote_addr
        if remote_addr in ["127.0.0.1", "localhost"]:
            # 本地连接，免密码
            return f(*args, **kwargs)

        # 远程连接，需要验证密码
        # 从请求头或请求体获取密码
        password = None

        # 1. 尝试从请求头获取
        password = request.headers.get("X-Admin-Password")

        # 2. 尝试从请求体获取（JSON）
        if not password and request.is_json:
            try:
                data = request.get_json() or {}
                password = data.get("admin_password") or data.get("password")
            except:
                pass

        # 3. 尝试从表单获取
        if not password:
            password = request.form.get("admin_password") or request.form.get(
                "password"
            )

        # 检查是否设置了密码
        stored_password = getattr(public_client, "admin_password", None)

        if not stored_password:
            # 没有设置密码，允许操作（但记录警告）
            logger.warning(
                f"[SECURITY] 远程访问未设置密码: {remote_addr} -> {request.endpoint}"
            )
            return f(*args, **kwargs)

        # 验证密码
        if password == stored_password:
            return f(*args, **kwargs)
        else:
            logger.warning(
                f"[SECURITY] 远程访问密码错误: {remote_addr} -> {request.endpoint}"
            )
            return jsonify(
                {"success": False, "message": "需要密码验证", "require_password": True}
            ), 403

    return decorated_function


def require_local_or_password(f):
    """
    权限控制装饰器：本地连接免密码，远程连接需要密码
    支持 session 记住验证状态（永久有效，直到手动清除浏览器cookie）
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 检查是否本地连接
        remote_addr = request.remote_addr
        if remote_addr in ["127.0.0.1", "localhost"]:
            # 本地连接，免密码
            return f(*args, **kwargs)

        # 检查 session 中是否已经验证过密码
        from flask import session
        if session.get('password_verified'):
            # 已验证过，直接通过
            return f(*args, **kwargs)

        # 远程连接，需要验证密码
        # 从请求头或请求体获取密码
        password = None

        # 1. 尝试从请求头获取
        password = request.headers.get("X-Admin-Password")

        # 2. 尝试从请求体获取（JSON）
        if not password and request.is_json:
            try:
                data = request.get_json() or {}
                password = data.get("admin_password") or data.get("password")
            except:
                pass

        # 3. 尝试从表单获取
        if not password:
            password = request.form.get("admin_password") or request.form.get(
                "password"
            )

        # 检查是否设置了密码
        stored_password = getattr(public_client, "admin_password", None)

        if not stored_password:
            # 没有设置密码，允许操作（但记录警告）
            logger.warning(
                f"[SECURITY] 远程访问未设置密码: {remote_addr} -> {request.endpoint}"
            )
            return f(*args, **kwargs)

        # 验证密码
        if password == stored_password:
            # 验证成功，设置 session（永久有效）
            session['password_verified'] = True
            session.permanent = True  # 使用配置的10年有效期（相当于永久）
            return f(*args, **kwargs)
        else:
            logger.warning(
                f"[SECURITY] 远程访问密码错误: {remote_addr} -> {request.endpoint}"
            )
            return jsonify(
                {"success": False, "message": "需要密码验证", "require_password": True}
            ), 403

    return decorated_function


def is_local_request():
    """检查是否为本地请求"""
    remote_addr = request.remote_addr
    return remote_addr in ["127.0.0.1", "localhost"]


def check_local_or_password():
    """
    检查本地或密码验证（用于函数内部调用，而非装饰器）
    本地连接免密码，远程连接需要密码验证（session 记住验证状态）
    返回 None 表示验证通过，返回 Response 表示验证失败
    """
    from flask import session

    remote_addr = request.remote_addr
    if remote_addr in ["127.0.0.1", "localhost"]:
        return None

    # 检查 session 中是否已经验证过密码
    if session.get('password_verified'):
        return None

    # 尝试从请求头获取密码
    password = request.headers.get("X-Admin-Password")

    # 尝试从请求体获取（JSON）
    if not password and request.is_json:
        try:
            data = request.get_json() or {}
            password = data.get("admin_password") or data.get("password")
        except Exception as e:
            logger.info(f"[Quota] 获取配额失败: {e}")

    # 尝试从表单获取
    if not password:
        password = request.form.get("admin_password") or request.form.get("password")

    # 检查是否设置了密码
    stored_password = getattr(public_client, "admin_password", None)

    if not stored_password:
        # 没有设置密码，允许操作
        return None

    # 验证密码
    if password == stored_password:
        # 验证成功，设置 session（永久有效）
        session['password_verified'] = True
        session.permanent = True
        return None
    else:
        return jsonify(
            {"success": False, "message": "需要密码验证", "require_password": True}
        ), 403


def _is_private_or_loopback_ip(ip_text: str) -> bool:
    if not ip_text:
        return False
    try:
        ip_obj = ipaddress.ip_address(ip_text.strip())
        return ip_obj.is_private or ip_obj.is_loopback
    except Exception:
        return False


def get_request_source_ip():
    """
    获取请求真实来源IP。
    - 直连请求：使用 request.remote_addr
    - 本机反向代理/隧道转发：仅当 remote_addr 为回环时，信任转发头
    """
    remote_addr = (request.remote_addr or "").strip()
    if _is_private_or_loopback_ip(remote_addr):
        cf_ip = (request.headers.get("CF-Connecting-IP") or "").strip()
        if cf_ip:
            return cf_ip
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return xff.split(",")[0].strip()
    return remote_addr


def post_with_retry(session, url, json_data, max_retries=3, timeout=10):
    """带重试的POST请求"""
    for attempt in range(max_retries):
        try:
            response = session.post(url, json=json_data, timeout=timeout)
            if response.status_code < 500:
                return response
            print(
                f"[WARN] 服务端返回{response.status_code}，重试 {attempt + 1}/{max_retries}"
            )
        except Exception as e:
            print(f"[WARN] 请求失败: {e}，重试 {attempt + 1}/{max_retries}")
        if attempt < max_retries - 1:
            time.sleep(1)
    return None


class PublicClient:
    """公网客户端"""

    def __init__(self, auto_connect=True):
        self.server_domain = USER_CONFIG["server_domain"]
        self.server_url = USER_CONFIG["server_url"]
        self.client_id = USER_CONFIG["client_id"]
        self.secret_key = USER_CONFIG.get("secret_key")
        self.baby_name = USER_CONFIG["baby_name"]
        self.baby_gender = USER_CONFIG.get("baby_gender")
        self.baby_birthday = USER_CONFIG.get("baby_birthday")
        self.user_city = USER_CONFIG.get("user_city")
        self.log_style = USER_CONFIG.get("log_style", "简练")
        self.custom_style = USER_CONFIG.get("custom_style", "")
        self.media_folders = USER_CONFIG.get("media_folders") or []
        self.admin_password = USER_CONFIG.get("admin_password")
        self.is_paid = USER_CONFIG.get("is_paid", False)
        self.subdomain = USER_CONFIG.get("subdomain")
        self.public_url = USER_CONFIG.get("public_url")
        self.avatar_url = (
            USER_CONFIG.get("avatar_url") or "/static/images/default-avatar.png"
        )
        self.client_version = USER_CONFIG.get("client_version", "2.0.0")
        self.index_after_date = USER_CONFIG.get("index_after_date", "")
        self.tunnel_active = False
        self.client_port = CLIENT_CONFIG["local"]["port"]
        self.tunnel_process = None
        self._pending_tunnel_credentials = None  # 待启动的 Tunnel 凭证
        self._tunnel_credentials = None  # 保存凭证用于重启
        self._last_tunnel_check = 0  # 上次检查 tunnel 的时间
        self.data_dir = USER_DATA_DIR

        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "CZRZ-Client/2.0", "Accept": "application/json"}
        )
        self.session.trust_env = False

        logger.info(
            f"客户端初始化: baby_name={self.baby_name}, client_id={self.client_id}"
        )

        if auto_connect and not self.is_first_run():
            self.connect_to_public_server()

    def is_first_run(self) -> bool:
        """检查是否首次运行

        只检查 baby_name 和 media_folders，不检查 client_id
        因为 client_id 可能在注册时因服务端未就绪而未保存
        """
        return (
            not USER_CONFIG_FILE.exists()
            or not self.baby_name
            or self.baby_name == "宝宝"
            or not self.media_folders
        )

    def load_config(self):
        pass

    def save_config(self):
        USER_CONFIG["client_id"] = self.client_id
        USER_CONFIG["secret_key"] = self.secret_key
        USER_CONFIG["baby_name"] = self.baby_name
        USER_CONFIG["baby_gender"] = self.baby_gender
        USER_CONFIG["baby_birthday"] = self.baby_birthday
        USER_CONFIG["user_city"] = self.user_city
        USER_CONFIG["log_style"] = self.log_style
        USER_CONFIG["custom_style"] = self.custom_style
        USER_CONFIG["media_folders"] = self.media_folders
        USER_CONFIG["admin_password"] = self.admin_password
        USER_CONFIG["is_paid"] = self.is_paid
        USER_CONFIG["subdomain"] = self.subdomain
        USER_CONFIG["public_url"] = self.public_url
        USER_CONFIG["server_domain"] = self.server_domain
        USER_CONFIG["server_url"] = self.server_url
        USER_CONFIG["avatar_url"] = self.avatar_url
        USER_CONFIG["client_version"] = self.client_version
        USER_CONFIG["index_after_date"] = getattr(self, "index_after_date", "")
        save_user_config()

    def signed_request(self, method, url, **kwargs):
        """发送带签名的请求"""
        from urllib.parse import urlparse, urlencode, quote

        headers = kwargs.pop("headers", {})
        json_data = kwargs.get("json")
        params = kwargs.get("params")
        body = json.dumps(json_data) if json_data else ""

        if self.client_id and self.secret_key:
            # 构建完整路径（包括 query string），确保 URL 编码一致
            path = url.replace(self.server_url, "")

            # 解析 URL 并重新编码，确保签名和服务端一致
            parsed = urlparse(path)
            if parsed.query:
                # 重新编码 query string，确保中文字符被正确编码
                query_params = {}
                for param in parsed.query.split("&"):
                    if "=" in param:
                        key, value = param.split("=", 1)
                        query_params[key] = value
                # 使用 quote 对值进行编码，保持与服务端一致
                query_string = "&".join(
                    f"{k}={quote(str(v), safe='')}" for k, v in query_params.items()
                )
                path = f"{parsed.path}?{query_string}"
            elif params:
                query_string = urlencode(params)
                path = f"{path}?{query_string}"

            add_signature_headers(
                headers, self.client_id, self.secret_key, method, path, body
            )

        kwargs["headers"] = headers
        kwargs.setdefault("verify", False)

        if method.upper() == "GET":
            return self.session.get(url, **kwargs)
        elif method.upper() == "POST":
            return self.session.post(url, **kwargs)
        elif method.upper() == "PUT":
            return self.session.put(url, **kwargs)
        elif method.upper() == "DELETE":
            return self.session.delete(url, **kwargs)
        else:
            return self.session.request(method, url, **kwargs)

    def init_photo_index(self):
        """
        初始化照片索引
        - 扫描所有源文件夹
        - 建立索引（如果不存在）

        Returns:
            dict: {"first_time": bool, "estimated_files": int, "estimated_minutes": float}
        """
        media_folders = getattr(self, "media_folders", [])
        if not media_folders:
            print("⚠️ 未配置媒体文件夹，跳过索引初始化")
            return {"first_time": False, "error": "未配置媒体文件夹"}

        try:
            from photo_manager import PhotoManager

            pm = PhotoManager(media_folders, self.data_dir)

            # 检查是否已有索引
            index_file = self.data_dir / "photo_index" / "photo_index.json"
            if index_file.exists():
                # 已有索引，检查是否需要更新
                import json

                with open(index_file, "r", encoding="utf-8") as f:
                    index = json.load(f)
                total_files = len(index.get("photos", {})) + len(
                    index.get("videos", {})
                )
                print(f"📂 已有索引: {total_files} 个文件")

                # 异步扫描更新（不阻塞启动）
                import threading

                def async_scan():
                    try:
                        # 读取日期范围设置（从配置文件读取）
                        index_after = USER_CONFIG.get("index_after_date", "") or None
                        print(
                            f"[索引] 配置中的index_after_date: '{USER_CONFIG.get('index_after_date')}'"
                        )
                        print(f"[索引] 传递给scan的start_date: '{index_after}'")
                        result = pm.scan_existing_photos(start_date=index_after)
                        print(
                            f"✅ 索引更新完成: 新增 {result.get('new', 0)} 个文件, 总计 {result.get('total', 0)} 个"
                        )
                    except Exception as e:
                        print(f"⚠️ 索引更新失败: {e}")

                thread = threading.Thread(target=async_scan, daemon=True)
                thread.start()
                return {"first_time": False, "existing_files": total_files}
            else:
                # 首次建立索引，先快速估算文件数量
                print("🔍 首次建立照片索引...")

                estimated_files = 0
                for folder in media_folders:
                    folder_path = Path(folder)
                    if folder_path.exists():
                        for root, dirs, files in os.walk(folder_path):
                            dirs[:] = [
                                d
                                for d in dirs
                                if d
                                not in [
                                    "original",
                                    "photo_index",
                                    "by_date",
                                    "compressed",
                                    "thumbnails",
                                ]
                            ]
                            for f in files:
                                if Path(f).suffix.lower() in {
                                    ".jpg",
                                    ".jpeg",
                                    ".png",
                                    ".gif",
                                    ".bmp",
                                    ".tiff",
                                    ".webp",
                                    ".mp4",
                                    ".mov",
                                    ".avi",
                                    ".mkv",
                                    ".flv",
                                    ".wmv",
                                }:
                                    estimated_files += 1

                # 估算时间：每秒约处理 50 个文件
                estimated_seconds = estimated_files / 50
                estimated_minutes = estimated_seconds / 60

                print(
                    f"📊 预估 {estimated_files} 个文件，约 {estimated_minutes:.1f} 分钟"
                )

                return {
                    "first_time": True,
                    "estimated_files": estimated_files,
                    "estimated_minutes": round(estimated_minutes, 1),
                }

        except Exception as e:
            print(f"⚠️ 初始化照片索引失败: {e}")
            return {"first_time": False, "error": str(e)}

    def build_photo_index(self):
        """执行照片索引建立（首次）"""
        media_folders = getattr(self, "media_folders", [])
        if not media_folders:
            return {"success": False, "error": "未配置媒体文件夹"}

        try:
            from photo_manager import PhotoManager
            from flask import current_app as app

            pm = PhotoManager(media_folders, self.data_dir)
            print("🔍 开始建立照片索引，这可能需要一些时间...")
            result = pm.scan_existing_photos()
            print(f"✅ 索引建立完成: 共 {result.get('total', 0)} 个文件")

            # 清理日历缓存
            self.clear_calendar_cache()

            return {"success": True, "result": result}
        except Exception as e:
            print(f"⚠️ 建立照片索引失败: {e}")
            return {"success": False, "error": str(e)}

    def clear_calendar_cache(self):
        """清理日历缓存"""
        try:
            from flask import current_app as app

            # 清理所有以 _photo_dates_cache_ 开头的属性
            attrs_to_delete = [
                attr
                for attr in dir(app)
                if attr.startswith("_photo_dates_cache_")
                or attr.startswith("_photo_dates_cache_time_")
            ]
            for attr in attrs_to_delete:
                delattr(app, attr)
            print(f"[Calendar] 清理了 {len(attrs_to_delete)} 个缓存项")
        except Exception as e:
            print(f"[WARN] 清理日历缓存失败: {e}")

    def show_first_run_setup(self):
        """初次启动配置向导 - 设置宝宝名字和城市"""
        import tkinter as tk
        from tkinter import ttk, messagebox

        print("\n" + "=" * 50)
        print("🎉 欢迎使用成长记录系统")
        print("=" * 50)
        print("\n为了更好地生成宝宝成长日志，请完成以下设置：\n")

        # 中国省市数据
        china_cities = {
            "自动检测": ["自动检测"],
            "北京": ["北京"],
            "天津": ["天津"],
            "河北": [
                "石家庄",
                "唐山",
                "秦皇岛",
                "邯郸",
                "邢台",
                "保定",
                "张家口",
                "承德",
                "沧州",
                "廊坊",
                "衡水",
            ],
            "山西": [
                "太原",
                "大同",
                "阳泉",
                "长治",
                "晋城",
                "朔州",
                "晋中",
                "运城",
                "忻州",
                "临汾",
                "吕梁",
            ],
            "内蒙古": [
                "呼和浩特",
                "包头",
                "乌海",
                "赤峰",
                "通辽",
                "鄂尔多斯",
                "呼伦贝尔",
                "巴彦淖尔",
                "乌兰察布",
            ],
            "辽宁": [
                "沈阳",
                "大连",
                "鞍山",
                "抚顺",
                "本溪",
                "丹东",
                "锦州",
                "营口",
                "阜新",
                "辽阳",
                "盘锦",
                "铁岭",
                "朝阳",
                "葫芦岛",
            ],
            "吉林": [
                "长春",
                "吉林",
                "四平",
                "辽源",
                "通化",
                "白山",
                "松原",
                "白城",
                "延边",
            ],
            "黑龙江": [
                "哈尔滨",
                "齐齐哈尔",
                "鸡西",
                "鹤岗",
                "双鸭山",
                "大庆",
                "伊春",
                "佳木斯",
                "七台河",
                "牡丹江",
                "黑河",
                "绥化",
                "大兴安岭",
            ],
            "上海": ["上海"],
            "江苏": [
                "南京",
                "无锡",
                "徐州",
                "常州",
                "苏州",
                "南通",
                "连云港",
                "淮安",
                "盐城",
                "扬州",
                "镇江",
                "泰州",
                "宿迁",
            ],
            "浙江": [
                "杭州",
                "宁波",
                "温州",
                "嘉兴",
                "湖州",
                "绍兴",
                "金华",
                "衢州",
                "舟山",
                "台州",
                "丽水",
            ],
            "安徽": [
                "合肥",
                "芜湖",
                "蚌埠",
                "淮南",
                "马鞍山",
                "淮北",
                "铜陵",
                "安庆",
                "黄山",
                "滁州",
                "阜阳",
                "宿州",
                "六安",
                "亳州",
                "池州",
                "宣城",
            ],
            "福建": [
                "福州",
                "厦门",
                "莆田",
                "三明",
                "泉州",
                "漳州",
                "南平",
                "龙岩",
                "宁德",
            ],
            "江西": [
                "南昌",
                "景德镇",
                "萍乡",
                "九江",
                "新余",
                "鹰潭",
                "赣州",
                "吉安",
                "宜春",
                "抚州",
                "上饶",
            ],
            "山东": [
                "济南",
                "青岛",
                "淄博",
                "枣庄",
                "东营",
                "烟台",
                "潍坊",
                "济宁",
                "泰安",
                "威海",
                "日照",
                "莱芜",
                "临沂",
                "德州",
                "聊城",
                "滨州",
                "菏泽",
            ],
            "河南": [
                "郑州",
                "开封",
                "洛阳",
                "平顶山",
                "安阳",
                "鹤壁",
                "新乡",
                "焦作",
                "濮阳",
                "许昌",
                "漯河",
                "三门峡",
                "南阳",
                "商丘",
                "信阳",
                "周口",
                "驻马店",
            ],
            "湖北": [
                "武汉",
                "黄石",
                "十堰",
                "宜昌",
                "襄阳",
                "鄂州",
                "荆门",
                "孝感",
                "荆州",
                "黄冈",
                "咸宁",
                "随州",
                "恩施",
            ],
            "湖南": [
                "长沙",
                "株洲",
                "湘潭",
                "衡阳",
                "邵阳",
                "岳阳",
                "常德",
                "张家界",
                "益阳",
                "郴州",
                "永州",
                "怀化",
                "娄底",
                "湘西",
            ],
            "广东": [
                "广州",
                "韶关",
                "深圳",
                "珠海",
                "汕头",
                "佛山",
                "江门",
                "湛江",
                "茂名",
                "肇庆",
                "惠州",
                "梅州",
                "汕尾",
                "河源",
                "阳江",
                "清远",
                "东莞",
                "中山",
                "潮州",
                "揭阳",
                "云浮",
            ],
            "广西": [
                "南宁",
                "柳州",
                "桂林",
                "梧州",
                "北海",
                "防城港",
                "钦州",
                "贵港",
                "玉林",
                "百色",
                "贺州",
                "河池",
                "来宾",
                "崇左",
            ],
            "海南": ["海口", "三亚", "三沙", "儋州"],
            "重庆": ["重庆"],
            "四川": [
                "成都",
                "自贡",
                "攀枝花",
                "泸州",
                "德阳",
                "绵阳",
                "广元",
                "遂宁",
                "内江",
                "乐山",
                "南充",
                "眉山",
                "宜宾",
                "广安",
                "达州",
                "雅安",
                "巴中",
                "资阳",
                "阿坝",
                "甘孜",
                "凉山",
            ],
            "贵州": [
                "贵阳",
                "六盘水",
                "遵义",
                "安顺",
                "毕节",
                "铜仁",
                "黔西南",
                "黔东南",
                "黔南",
            ],
            "云南": [
                "昆明",
                "曲靖",
                "玉溪",
                "保山",
                "昭通",
                "丽江",
                "普洱",
                "临沧",
                "楚雄",
                "红河",
                "文山",
                "西双版纳",
                "大理",
                "德宏",
                "怒江",
                "迪庆",
            ],
            "西藏": ["拉萨", "日喀则", "昌都", "林芝", "山南", "那曲", "阿里"],
            "陕西": [
                "西安",
                "铜川",
                "宝鸡",
                "咸阳",
                "渭南",
                "延安",
                "汉中",
                "榆林",
                "安康",
                "商洛",
            ],
            "甘肃": [
                "兰州",
                "嘉峪关",
                "金昌",
                "白银",
                "天水",
                "武威",
                "张掖",
                "平凉",
                "酒泉",
                "庆阳",
                "定西",
                "陇南",
                "临夏",
                "甘南",
            ],
            "青海": ["西宁", "海东", "海北", "黄南", "海南", "果洛", "玉树", "海西"],
            "宁夏": ["银川", "石嘴山", "吴忠", "固原", "中卫"],
            "新疆": [
                "乌鲁木齐",
                "克拉玛依",
                "吐鲁番",
                "哈密",
                "昌吉",
                "博尔塔拉",
                "巴音郭楞",
                "阿克苏",
                "克孜勒苏",
                "喀什",
                "和田",
                "伊犁",
                "塔城",
                "阿勒泰",
            ],
            "台湾": ["台北", "高雄", "台中", "台南", "基隆", "新竹", "嘉义"],
            "香港": ["香港"],
            "澳门": ["澳门"],
        }

        # 创建配置窗口
        root = tk.Tk()
        root.title("初次设置 - 成长记录系统")
        root.geometry("500x450")
        root.resizable(False, False)

        # 居中窗口
        root.update_idletasks()
        width = root.winfo_width()
        height = root.winfo_height()
        x = (root.winfo_screenwidth() // 2) - (width // 2)
        y = (root.winfo_screenheight() // 2) - (height // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")

        # 标题
        title_label = ttk.Label(
            root, text="🍼 成长记录系统", font=("Microsoft YaHei", 16, "bold")
        )
        title_label.pack(pady=15)

        desc_label = ttk.Label(
            root,
            text="设置宝宝信息，用于生成个性化的成长日志",
            font=("Microsoft YaHei", 10),
            foreground="gray",
        )
        desc_label.pack(pady=(0, 20))

        # 宝宝名字输入
        name_frame = ttk.Frame(root)
        name_frame.pack(fill="x", padx=30, pady=10)

        ttk.Label(name_frame, text="宝宝名字 *", font=("Microsoft YaHei", 11)).pack(
            anchor="w"
        )
        name_entry = ttk.Entry(name_frame, font=("Microsoft YaHei", 11))
        name_entry.pack(fill="x", pady=(5, 0))
        name_entry.insert(0, "宝宝")

        # 省份选择
        province_frame = ttk.Frame(root)
        province_frame.pack(fill="x", padx=30, pady=10)

        ttk.Label(
            province_frame, text="所在省份（可选）", font=("Microsoft YaHei", 11)
        ).pack(anchor="w")

        provinces = list(china_cities.keys())
        province_var = tk.StringVar(value="自动检测")
        province_combo = ttk.Combobox(
            province_frame,
            textvariable=province_var,
            values=provinces,
            font=("Microsoft YaHei", 11),
            state="readonly",
        )
        province_combo.pack(fill="x", pady=(5, 0))

        # 城市选择
        city_frame = ttk.Frame(root)
        city_frame.pack(fill="x", padx=30, pady=10)

        ttk.Label(
            city_frame, text="所在城市（可选）", font=("Microsoft YaHei", 11)
        ).pack(anchor="w")

        city_var = tk.StringVar(value="自动检测")
        city_combo = ttk.Combobox(
            city_frame,
            textvariable=city_var,
            font=("Microsoft YaHei", 11),
            state="readonly",
        )
        city_combo.pack(fill="x", pady=(5, 0))

        # 级联更新城市列表
        def update_cities(*args):
            province = province_var.get()
            if province in china_cities:
                cities = china_cities[province]
                city_combo["values"] = cities
                city_var.set(cities[0] if cities else "")

        province_var.trace("w", update_cities)

        ttk.Label(
            city_frame,
            text="选择城市后，成长日志将包含当地天气信息",
            font=("Microsoft YaHei", 9),
            foreground="gray",
        ).pack(anchor="w", pady=(5, 0))

        # 照片文件夹选择
        photo_frame = ttk.Frame(root)
        photo_frame.pack(fill="x", padx=30, pady=15)

        ttk.Label(
            photo_frame, text="宝宝照片保存文件夹 *", font=("Microsoft YaHei", 11)
        ).pack(anchor="w")

        photo_path_var = tk.StringVar()
        photo_path_entry = ttk.Entry(
            photo_frame, textvariable=photo_path_var, font=("Microsoft YaHei", 11)
        )
        photo_path_entry.pack(fill="x", pady=(5, 0))

        def choose_photo_folder():
            from tkinter import filedialog

            folder = filedialog.askdirectory(title="选择宝宝照片保存文件夹")
            if folder:
                photo_path_var.set(folder)

        choose_btn = ttk.Button(
            photo_frame, text="选择文件夹...", command=choose_photo_folder
        )
        choose_btn.pack(anchor="w", pady=(5, 0))

        ttk.Label(
            photo_frame,
            text="上传的照片将保存在此文件夹，按拍摄日期自动分类",
            font=("Microsoft YaHei", 9),
            foreground="gray",
        ).pack(anchor="w", pady=(5, 0))

        # 按钮区域
        btn_frame = ttk.Frame(root)
        btn_frame.pack(fill="x", padx=30, pady=30)

        def on_confirm():
            baby_name = name_entry.get().strip()
            if not baby_name:
                messagebox.showwarning("提示", "请输入宝宝名字")
                return

            photo_folder = photo_path_var.get().strip()
            if not photo_folder:
                messagebox.showwarning("提示", "请选择宝宝照片保存文件夹")
                return

            # 检查文件夹是否存在，不存在则创建
            photo_path = Path(photo_folder)
            if not photo_path.exists():
                try:
                    photo_path.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("错误", f"无法创建文件夹: {e}")
                    return

            self.baby_name = baby_name
            self.photo_folder = str(photo_path)  # 保存照片文件夹路径

            # 保存城市信息
            province = province_var.get()
            city = city_var.get()
            if province == "自动检测" or city == "自动检测":
                self.user_city = None
            else:
                self.user_city = city  # 保存城市名

            # 保存配置
            self.save_config()

            print(f"\n✅ 设置完成！")
            print(f"   宝宝名字: {self.baby_name}")
            print(f"   所在城市: {self.user_city or '自动检测'}")
            print(f"   照片文件夹: {self.photo_folder}")
            print(f"\n{'=' * 50}\n")

            root.destroy()

        confirm_btn = ttk.Button(btn_frame, text="开始记录", command=on_confirm)
        confirm_btn.pack(fill="x", pady=5)

        # 运行窗口
        root.mainloop()

    def save_credentials(self, credentials, subdomain=None):
        """保存 tunnel 凭证到本地

        Args:
            credentials: 凭证信息字典
            subdomain: 可选，子域名信息
        """
        import platform
        import shutil

        try:
            tunnel_id = credentials.get("TunnelID", "")
            tunnel_secret = credentials.get("TunnelSecret", "")
            account_tag = credentials.get("AccountTag", "")

            if not tunnel_id or not tunnel_secret:
                print(f"⚠ 凭证信息不完整")
                return False

            system = platform.system()

            # 确定凭证目录
            if system == "Windows":
                creds_dir = Path(os.environ.get("USERPROFILE", ".")) / ".cloudflared"
            else:
                creds_dir = Path.home() / ".cloudflared"

            creds_dir.mkdir(parents=True, exist_ok=True)
            creds_file = creds_dir / f"{tunnel_id}.json"

            # 保存凭证文件
            creds_data = {
                "AccountTag": account_tag,
                "TunnelSecret": tunnel_secret,
                "TunnelID": tunnel_id,
                "Endpoint": "",
            }

            with open(creds_file, "w", encoding="utf-8") as f:
                json.dump(creds_data, f, indent=2)

            # 同时保存到客户端目录（供启动脚本使用）
            client_creds_file = self.data_dir / "cloudflared_credentials.json"
            with open(client_creds_file, "w", encoding="utf-8") as f:
                json.dump(creds_data, f, indent=2)

            # 如果提供了子域名，保存到 client_config.json
            if subdomain:
                self._save_subdomain_to_config(subdomain)

            print(f"💾 凭证已保存到: {creds_file}")
            return True

        except Exception as e:
            print(f"❌ 保存凭证失败: {e}")
            return False

    def _save_subdomain_to_config(self, subdomain):
        """保存子域名到 client_config.json"""
        try:
            config_file = self.data_dir / "client_config.json"
            config = {}
            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)

            # 更新 server.domain
            if "server" not in config:
                config["server"] = {}
            config["server"]["domain"] = subdomain

            with open(config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)

            print(f"💾 子域名已保存: {subdomain}")
        except Exception as e:
            print(f"⚠ 保存子域名失败: {e}")

    def load_local_credentials(self):
        """读取本地保存的 tunnel 凭证

        优先级：
        1. client_tunnels/{client_id}/credentials.json（如果有 client_id）
        2. 遍历 client_tunnels/ 下所有子目录（如果没有 client_id 或上述不存在）
        3. cloudflared_credentials.json（备用位置）

        不依赖 client_id，确保即使注册失败也能加载本地凭证
        """
        try:
            # 1. 如果有 client_id，优先从对应目录读取
            if self.client_id:
                client_tunnel_dir = self.data_dir / "client_tunnels" / self.client_id
                client_creds_file = client_tunnel_dir / "credentials.json"
                if client_creds_file.exists():
                    with open(client_creds_file, "r", encoding="utf-8") as f:
                        credentials = json.load(f)
                    tunnel_id = credentials.get("TunnelID", "")
                    if tunnel_id:
                        # 检查 cloudflared 目录中的凭证文件是否存在
                        creds_file = Path.home() / ".cloudflared" / f"{tunnel_id}.json"
                        if creds_file.exists():
                            print(f"📂 发现本地凭证: {tunnel_id[:8]}...")
                            return credentials
                        else:
                            # 凭证文件不存在，需要创建
                            print(f"⚠ cloudflared 目录缺少凭证文件，尝试创建...")
                            creds_dir = Path.home() / ".cloudflared"
                            creds_dir.mkdir(parents=True, exist_ok=True)
                            with open(creds_file, "w", encoding="utf-8") as f:
                                json.dump(credentials, f, indent=2)
                            print(f"✅ 已创建凭证文件: {creds_file}")
                            return credentials

            # 2. 遍历 client_tunnels 下所有子目录（不依赖 client_id）
            client_tunnels_dir = self.data_dir / "client_tunnels"
            if client_tunnels_dir.exists() and client_tunnels_dir.is_dir():
                for subdir in client_tunnels_dir.iterdir():
                    if subdir.is_dir():
                        creds_file = subdir / "credentials.json"
                        if creds_file.exists():
                            with open(creds_file, "r", encoding="utf-8") as f:
                                credentials = json.load(f)
                            tunnel_id = credentials.get("TunnelID", "")
                            if tunnel_id:
                                cf_creds_file = (
                                    Path.home() / ".cloudflared" / f"{tunnel_id}.json"
                                )
                                if cf_creds_file.exists():
                                    print(f"📂 发现本地凭证(遍历): {tunnel_id[:8]}...")
                                    # 同时恢复 client_id
                                    if not self.client_id:
                                        self.client_id = subdir.name
                                        print(
                                            f"📝 从凭证目录恢复 client_id: {self.client_id[:8]}..."
                                        )
                                    return credentials
                                else:
                                    # 创建凭证文件
                                    creds_dir = Path.home() / ".cloudflared"
                                    creds_dir.mkdir(parents=True, exist_ok=True)
                                    with open(
                                        cf_creds_file, "w", encoding="utf-8"
                                    ) as f:
                                        json.dump(credentials, f, indent=2)
                                    print(f"✅ 已创建凭证文件: {cf_creds_file}")
                                    if not self.client_id:
                                        self.client_id = subdir.name
                                    return credentials

            # 3. 从备用位置读取
            client_creds_file = self.data_dir / "cloudflared_credentials.json"
            if client_creds_file.exists():
                with open(client_creds_file, "r", encoding="utf-8") as f:
                    credentials = json.load(f)
                tunnel_id = credentials.get("TunnelID", "")
                if tunnel_id:
                    creds_file = Path.home() / ".cloudflared" / f"{tunnel_id}.json"
                    if creds_file.exists():
                        print(f"📂 发现本地凭证(备用): {tunnel_id[:8]}...")
                        return credentials

            return None
        except Exception as e:
            print(f"⚠ 读取本地凭证失败: {e}")
            return None

    def load_local_subdomain(self):
        """从本地配置读取子域名信息"""
        try:
            config_file = self.data_dir / "client_config.json"
            if config_file.exists():
                with open(config_file, "r", encoding="utf-8") as f:
                    config = json.load(f)
                # 从 server.domain 提取子域名
                domain = config.get("server", {}).get("domain", "")
                if domain:
                    return domain
            return None
        except Exception as e:
            print(f"⚠ 读取本地子域名失败: {e}")
            return None

    def connect_to_public_server(self):
        """强制连接到公网服务端"""
        print(f"🌐 强制连接到公网服务端: {self.server_url}")
        print(f"📡 注册地址: {self.server_url}/czrz/register")
        logger.info(f"连接到公网服务端: {self.server_url}")

        # 测试连接
        try:
            response = self.session.get(
                f"{self.server_url}/czrz/health",
                timeout=10,
                verify=False,  # 禁用 SSL 验证，避免证书错误
            )

            if response.status_code == 200:
                server_info = response.json()
                print("✅ 公网服务端连接成功!")
                print(f"📊 服务端版本: {server_info.get('version')}")
                print(f"🌍 公网域名: {server_info.get('public_domain')}")
                logger.info(f"公网服务端连接成功: version={server_info.get('version')}")

                try:
                    config_resp = self.session.get(
                        f"{self.server_url}/czrz/client/server-config",
                        timeout=5,
                        verify=False,
                    )
                    if config_resp.status_code == 200:
                        config_data = config_resp.json()
                        new_domain = config_data.get("server_domain")
                        if new_domain and new_domain != self.server_domain:
                            print(
                                f"🔄 服务端域名已更新: {self.server_domain} → {new_domain}"
                            )
                            self.server_domain = new_domain
                            self.server_url = config_data.get(
                                "server_url", f"https://{new_domain}"
                            )
                            update_server_domain(new_domain)
                            CLIENT_CONFIG["server"]["domain"] = new_domain
                            CLIENT_CONFIG["server"]["url"] = self.server_url
                except Exception as e:
                    logger.warning(f"获取服务端配置失败: {e}")

                # 如果已有 client_id，发送心跳
                if self.client_id:
                    heartbeat_ok = self.send_heartbeat()
                    if not heartbeat_ok:
                        # 心跳失败，需要重新注册
                        print("🔄 客户端未注册或已失效，需要重新注册...")
                        logger.warning("心跳失败，需要重新注册")
                        self.register_client()
                    else:
                        # 心跳成功，检查是否需要获取子域名和凭证并启动 tunnel
                        if self.subdomain and self.public_url:
                            print(f"💓 心跳成功，已有公网地址: {self.public_url}")
                            # 即使有公网地址，也需要确保 tunnel 正在运行
                            if not self.tunnel_active:
                                print("🔄 Tunnel 未运行，正在启动...")
                                self.fetch_subdomain()
                        else:
                            print("💓 心跳成功，获取子域名和凭证...")
                            self.fetch_subdomain()
                        # 初始化照片索引
                        index_info = self.init_photo_index()
                        if index_info and index_info.get("first_time"):
                            # 首次建立索引，后台执行
                            import threading

                            def async_build():
                                self.build_photo_index()

                            threading.Thread(target=async_build, daemon=True).start()
                else:
                    # 注册新客户端
                    self.register_client()
            else:
                print(f"⚠ 服务端响应异常: {response.status_code}")
                logger.warning(f"服务端响应异常: {response.status_code}")
                print("🔧 尝试HTTP连接...")
                self.try_http_connection()

        except requests.exceptions.SSLError as e:
            print(f"🔒 SSL证书错误: {e}")
            logger.error(f"SSL证书错误: {e}")
            print("🔧 尝试HTTP连接...")
            self.try_http_connection()
        except Exception as e:
            print(f"❌ 连接错误: {e}")
            logger.error(f"连接错误: {e}")
            print("🔧 尝试HTTP连接...")
            self.try_http_connection()

    def try_http_connection(self):
        """尝试HTTP连接"""
        http_url = f"http://{self.server_domain}"
        print(f"🔄 尝试HTTP连接: {http_url}")

        try:
            response = self.session.get(f"{http_url}/czrz/health", timeout=10)
            if response.status_code == 200:
                print("✅ HTTP连接成功")
                self.server_url = http_url
                self.connect_to_public_server()
            else:
                print(f"❌ HTTP连接失败: {response.status_code}")
                self._enter_local_mode(f"HTTP连接失败 ({response.status_code})")
        except Exception as e:
            print(f"❌ HTTP连接错误: {e}")
            self._enter_local_mode(f"HTTP连接错误: {e}")

    def _enter_local_mode(self, reason="服务端不可用"):
        """进入本地模式"""
        print("=" * 50)
        print(f"⚠️  无法连接到服务端: {reason}")
        print("🏠 已切换到【本地模式】运行")
        print("   - 数据仅保存在本地，不会同步到云端")
        print("   - AI 功能将不可用")
        print("   - 请检查网络连接或稍后重试")
        print("=" * 50)
        logger.warning(f"进入本地模式: {reason}")
        if not self.client_id:
            self.client_id = f"local_{uuid.uuid4().hex[:12]}"
            self.save_config()
            print(f"📱 已生成本地客户端ID: {self.client_id}")
        global LOCAL_MODE
        LOCAL_MODE = True

    def register_client(self):
        """注册客户端"""
        print("📝 注册新客户端到公网服务端...")

        install_key = get_install_key()
        print(f"🔑 安装密钥: {install_key}")

        device_info = {
            "baby_name": self.baby_name,
            "baby_gender": getattr(self, "baby_gender", None),
            "baby_birthday": getattr(self, "baby_birthday", None),
            "user_city": getattr(self, "user_city", None),
            "ip": self.get_local_ip(),
            "mac_address": self.get_mac_address(),
            "client_port": self.find_available_port(),
            "install_key": install_key,
        }

        try:
            response = self.session.post(
                f"{self.server_url}/czrz/register", json=device_info, timeout=15
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    self.client_id = data.get("client_id") or ""
                    self.is_paid = data.get("is_paid", False)
                    self.registered_at = (
                        data.get("registered_at") or datetime.now().isoformat()
                    )
                    secret_key = data.get("secret_key") or ""
                    if secret_key:
                        USER_CONFIG["secret_key"] = secret_key
                        self.secret_key = secret_key
                        print(f"🔐 密钥已保存")

                    print(f"✅ 注册成功!")
                    print(f"📱 客户端ID: {self.client_id}")
                    print(f"💰 付费状态: {'付费用户' if self.is_paid else '免费用户'}")
                    logger.info(
                        f"注册成功: client_id={self.client_id}, is_paid={self.is_paid}"
                    )

                    self.fetch_subdomain()
                    self.save_config()
                    # 初始化照片索引
                    index_info = self.init_photo_index()
                    if index_info and index_info.get("first_time"):
                        # 首次建立索引，后台执行
                        import threading

                        def async_build():
                            self.build_photo_index()

                        threading.Thread(target=async_build, daemon=True).start()
                else:
                    print(f"❌ 注册失败: {data.get('message')}")
                    logger.error(f"注册失败: {data.get('message')}")
            else:
                print(f"❌ 注册请求失败: {response.status_code}")
                logger.error(f"注册请求失败: {response.status_code}")

        except Exception as e:
            print(f"❌ 注册错误: {e}")
            logger.error(f"注册错误: {e}")
            self._enter_local_mode(f"注册失败: {e}")

    def fetch_subdomain(self):
        """获取子域名并启动 Cloudflare Tunnel"""
        if not self.client_id:
            return

        def do_fetch():
            """执行获取子域名的实际逻辑"""
            print("🔑 获取子域名...")
            logger.info("获取子域名...")

            try:
                response = self.signed_request(
                    "POST",
                    f"{self.server_url}/czrz/cloudflare/credentials",
                    json={"client_id": self.client_id},
                    timeout=15,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("success"):
                        client_info = data.get("client_info", {})
                        self.subdomain = client_info.get("subdomain") or ""
                        self.public_url = client_info.get("public_url") or ""

                        print(f"🎉 子域名获取成功!")
                        print(f"🌐 您的子域名: {self.subdomain}")
                        print(f"🔗 公网访问地址: {self.public_url}")
                        logger.info(f"子域名获取成功: {self.subdomain}")

                        # 保存凭证到本地
                        credentials = data.get("credentials", {})
                        if credentials:
                            self.save_credentials(credentials, self.subdomain)
                            self.start_cloudflare_tunnel(credentials)
                        return True
                    else:
                        print(f"⚠ 获取子域名失败: {data.get('message')}")
                        logger.warning(f"获取子域名失败: {data.get('message')}")
                else:
                    print(f"⚠ 子域名请求失败: {response.status_code}")
                    logger.warning(f"子域名请求失败: {response.status_code}")

            except Exception as e:
                print(f"⚠ 获取子域名错误: {e}")
                logger.error(f"获取子域名错误: {e}")

            return False

        # 先尝试一次
        if do_fetch():
            return

        # 如果失败，启动后台重试线程（每10秒重试一次）
        def retry_task():
            print("⏳ 启动后台重试，每10秒尝试获取子域名...")
            max_retries = 60  # 最多重试10分钟
            for i in range(max_retries):
                time.sleep(10)
                print(f"🔄 重试获取子域名 ({i + 1}/{max_retries})...")
                if do_fetch():
                    return
            print("⚠ 获取子域名重试超时（10分钟），将在下次心跳时继续重试")

        import threading

        thread = threading.Thread(target=retry_task, daemon=True)
        thread.start()

    def fetch_subdomain_only(self):
        """获取子域名信息但不启动 Tunnel（Tunnel 在 Flask 启动后启动）

        优先级：
        1. 先尝试读取本地已保存的凭证
        2. 如果本地凭证存在且有效，直接使用
        3. 如果本地凭证不存在或无效，再从服务端获取
        """
        if not self.client_id:
            return

        # 1. 先尝试读取本地凭证
        local_credentials = self.load_local_credentials()
        if local_credentials:
            tunnel_id = local_credentials.get("TunnelID", "")
            # 尝试从本地配置读取子域名
            local_domain = self.load_local_subdomain()
            if local_domain:
                # 构造子域名（假设格式为 tunnel_id前8位 + mumu.ftir.fun）
                # 或者直接使用保存的域名
                self.subdomain = local_domain
                self.public_url = f"https://{local_domain}"
                self._pending_tunnel_credentials = local_credentials
                print(f"✅ 使用本地凭证启动 Tunnel")
                print(f"🌐 子域名: {self.subdomain}")
                logger.info(f"使用本地凭证: {tunnel_id[:8]}...")
                return

        # 2. 本地凭证不存在，从服务端获取
        print("🔑 获取子域名...")
        logger.info("获取子域名...")

        try:
            response = self.signed_request(
                "POST",
                f"{self.server_url}/czrz/cloudflare/credentials",
                json={"client_id": self.client_id},
                timeout=15,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    client_info = data.get("client_info", {})
                    self.subdomain = client_info.get("subdomain") or ""
                    self.public_url = client_info.get("public_url") or ""

                    print(f"🎉 子域名获取成功!")
                    print(f"🌐 您的子域名: {self.subdomain}")
                    logger.info(f"子域名获取成功: {self.subdomain}")

                    # 保存凭证到本地
                    credentials = data.get("credentials", {})
                    if credentials:
                        # 保存凭证和子域名到本地
                        self.save_credentials(credentials, self.subdomain)
                        self._pending_tunnel_credentials = credentials
                        print("⏳ Tunnel 将在服务启动后自动启动...")
                else:
                    print(f"⚠ 获取子域名失败: {data.get('message')}")
                    logger.warning(f"获取子域名失败: {data.get('message')}")
            else:
                print(f"⚠ 子域名请求失败: {response.status_code}")
                logger.warning(f"子域名请求失败: {response.status_code}")
                # 如果是 5xx 错误，可能是服务端 tunnel 还没就绪，稍后重试
                if response.status_code >= 500:
                    self._retry_fetch_subdomain()

        except Exception as e:
            print(f"⚠ 获取子域名错误: {e}")
            logger.error(f"获取子域名错误: {e}")

    def _retry_fetch_subdomain(self):
        """在后台线程中延迟重试获取子域名"""

        def retry_task():
            import time

            max_retries = 3
            for i in range(max_retries):
                time.sleep(5)  # 等待 5 秒后重试
                print(f"🔄 重试获取子域名 ({i + 1}/{max_retries})...")
                try:
                    response = self.signed_request(
                        "POST",
                        f"{self.server_url}/czrz/cloudflare/credentials",
                        json={"client_id": self.client_id},
                        timeout=15,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        if data.get("success"):
                            client_info = data.get("client_info", {})
                            self.subdomain = client_info.get("subdomain") or ""
                            self.public_url = client_info.get("public_url") or ""
                            print(f"🎉 子域名获取成功!")
                            logger.info(f"子域名获取成功: {self.subdomain}")
                            # 保存凭证并启动 Tunnel
                            credentials = data.get("credentials", {})
                            if credentials:
                                self.save_credentials(credentials, self.subdomain)
                                self.start_cloudflare_tunnel(credentials)
                            return
                except Exception as e:
                    print(f"⚠ 重试失败: {e}")
            print(f"⚠ 获取子域名重试 {max_retries} 次后仍失败")

        import threading

        thread = threading.Thread(target=retry_task, daemon=True)
        thread.start()

    def start_cloudflare_tunnel(self, credentials):
        """启动 Cloudflare Tunnel（使用配置文件方式）"""
        import subprocess
        import platform

        try:
            system = platform.system()
            tunnel_id = credentials.get("TunnelID", "")
            if not tunnel_id:
                print("⚠ 凭证中缺少 TunnelID")
                return

            if not self.subdomain:
                print("⚠ 缺少子域名信息，无法启动 Tunnel")
                return

            if system == "Windows":
                creds_dir = Path(os.environ.get("USERPROFILE", ".")) / ".cloudflared"
            else:
                creds_dir = Path.home() / ".cloudflared"

            creds_dir.mkdir(parents=True, exist_ok=True)
            creds_file = creds_dir / f"{tunnel_id}.json"

            with open(creds_file, "w", encoding="utf-8") as f:
                json.dump(credentials, f, indent=2)

            config_content = f"""tunnel: {tunnel_id}
credentials-file: {creds_file}

ingress:
  - hostname: {self.subdomain}
    service: http://localhost:{self.client_port}
  - service: http_status:404
"""
            config_file = self.data_dir / "tunnel_config.yml"
            with open(config_file, "w", encoding="utf-8") as f:
                f.write(config_content)

            if system == "Windows":
                exe_name = "cloudflare.exe"
            else:
                exe_name = "cloudflared"

            exe_paths = [
                Path(__file__).parent / exe_name,
                Path(sys._MEIPASS) / exe_name if hasattr(sys, "_MEIPASS") else None,
                Path(__file__).parent / "cloudflared" / exe_name,
                Path(exe_name),
            ]
            exe_paths = [p for p in exe_paths if p is not None]

            exe_path = None
            for path in exe_paths:
                if path.exists():
                    exe_path = path
                    break

            if not exe_path:
                print(f"⚠ 未找到 {exe_name}，请确保它在程序目录中")
                return

            cmd = [
                str(exe_path),
                "tunnel",
                "--config",
                str(config_file),
                "run",
            ]

            print(f"🚀 启动 Cloudflare Tunnel...")
            print(f"   本地地址: http://localhost:{self.client_port}")
            print(f"   公网地址: {self.public_url}")
            print(f"   配置文件: {config_file}")

            if system == "Windows":
                # CREATE_NO_WINDOW = 0x08000000 - 隐藏窗口
                self.tunnel_process = subprocess.Popen(
                    cmd,
                    creationflags=0x08000000,  # CREATE_NO_WINDOW
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                self.tunnel_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )

            print(f"✅ Cloudflare Tunnel 已启动 (PID: {self.tunnel_process.pid})")
            self.tunnel_active = True
            self._tunnel_credentials = credentials  # 保存凭证用于重启
            self._last_tunnel_check = time.time()

        except Exception as e:
            print(f"⚠ 启动 Tunnel 失败: {e}")

    def stop_cloudflare_tunnel(self):
        """停止 Cloudflare Tunnel"""
        import subprocess
        import platform
        import signal

        try:
            system = platform.system()

            # 优先使用保存的进程引用停止
            if self.tunnel_process and self.tunnel_process.poll() is None:
                try:
                    self.tunnel_process.terminate()
                    self.tunnel_process.wait(timeout=5)
                    print(
                        f"🛑 Cloudflare Tunnel 已停止 (PID: {self.tunnel_process.pid})"
                    )
                except:
                    # 强制终止
                    self.tunnel_process.kill()
                    print(f"🛑 Cloudflare Tunnel 已强制停止")
            elif system == "Windows":
                # 备用：使用 taskkill（只杀当前用户的进程）
                subprocess.run(
                    [
                        "taskkill",
                        "/F",
                        "/IM",
                        "cloudflare.exe",
                        "/FI",
                        f"STATUS eq RUNNING",
                    ],
                    capture_output=True,
                    stderr=subprocess.DEVNULL,
                )
                print(f"🛑 Cloudflare Tunnel 已停止")
            else:
                # Linux/Mac: 查找并终止进程
                try:
                    result = subprocess.run(
                        ["pgrep", "-f", "cloudflared.*tunnel.*run"],
                        capture_output=True,
                        text=True,
                    )
                    if result.stdout:
                        pids = result.stdout.strip().split("\n")
                        for pid in pids:
                            try:
                                os.kill(int(pid), signal.SIGTERM)
                            except:
                                pass
                except:
                    pass
                print(f"🛑 Cloudflare Tunnel 已停止")

            self.tunnel_active = False
            self.tunnel_process = None

        except Exception as e:
            print(f"⚠ 停止 Tunnel 失败: {e}")

    def send_heartbeat(self):
        """发送心跳，返回是否成功"""
        if not self.client_id:
            install_key = get_install_key()
            print(f"🔑 无客户端ID，尝试注册，安装密钥: {install_key}")
            payload = {
                "client_id": None,
                "baby_name": self.baby_name,
                "ip": self.get_local_ip(),
                "mac_address": self.get_mac_address(),
                "client_port": self.client_port,
                "install_key": install_key,
            }
        else:
            payload = {
                "client_id": self.client_id,
                "baby_name": self.baby_name,
                "ip": self.get_local_ip(),
                "mac_address": self.get_mac_address(),
                "client_port": self.client_port,
            }

        try:
            global ERROR_REPORT_QUEUE
            errors_to_send = ERROR_REPORT_QUEUE.copy() if ERROR_REPORT_QUEUE else []
            payload["errors"] = errors_to_send

            try:
                from card_cache import get_card_cache

                cache = get_card_cache()
                payload["cached_daily_card_ids"] = [
                    card_id
                    for card_id in (
                        (card.get("id") or card.get("card_id"))
                        for card in cache.get_all_cards()
                    )
                    if card_id
                ]
            except Exception as e:
                print(f"⚠ 读取本地卡片ID失败: {e}")

            response = self.session.post(
                f"{self.server_url}/czrz/client/heartbeat",
                json=payload,
                timeout=10,
            )

            if errors_to_send:
                ERROR_REPORT_QUEUE = []

            if response.status_code == 200:
                data = response.json()

                if data.get("error") == "INVALID_INSTALL_KEY":
                    print(f"❌ 无效的安装密钥")
                    logger.error("无效的安装密钥")
                    return False

                if data.get("auto_registered"):
                    print(f"✅ 自动注册成功")
                    logger.info("自动注册成功")
                    self.client_id = data.get("client_id")
                    secret_key = data.get("secret_key")
                    if secret_key:
                        USER_CONFIG["secret_key"] = secret_key
                        self.secret_key = secret_key
                        print(f"🔐 密钥已保存")
                    self.save_config()

                # 服务端要求刷新配置（根据MAC匹配到已有客户端）
                if data.get("requires_refresh") and not data.get("auto_registered"):
                    new_client_id = data.get("client_id")
                    new_secret_key = data.get("secret_key")
                    if new_client_id and new_client_id != self.client_id:
                        print(f"🔄 服务端识别到已有客户端，更新配置...")
                        print(f"   原ID: {self.client_id}")
                        print(f"   新ID: {new_client_id}")
                        logger.info(f"根据MAC匹配更新客户端ID: {self.client_id} -> {new_client_id}")
                        self.client_id = new_client_id
                        if new_secret_key:
                            USER_CONFIG["secret_key"] = new_secret_key
                            self.secret_key = new_secret_key
                        self.save_config()
                        print(f"✅ 配置已更新，下次请求将使用新ID")

                if data.get("disabled"):
                    print(f"⚠ 客户端已被禁用: {data.get('message')}")
                    logger.warning(f"客户端已被禁用: {data.get('message')}")
                    print("🏠 切换到本地模式运行")
                    global LOCAL_MODE, CLIENT_DISABLED
                    LOCAL_MODE = True
                    CLIENT_DISABLED = True
                    return False

                print(f"💓 心跳发送成功")
                logger.debug("心跳发送成功")

                # 保存服务端推送的每日卡片到本地缓存
                daily_cards = data.get("daily_cards", [])
                daily_card_ids = data.get("daily_card_ids", [])
                if daily_cards or daily_card_ids:
                    try:
                        from card_cache import get_card_cache

                        cache = get_card_cache()
                        removed = 0

                        # 删除不在服务端列表中的缓存卡片
                        # 只有当服务端返回了有效的卡片ID列表时才进行过滤
                        if daily_card_ids and len(daily_card_ids) > 0:
                            server_id_set = set(daily_card_ids)
                            before_count = len(cache.cards)
                            cache.cards = [
                                c
                                for c in cache.cards
                                if c.get("id") in server_id_set
                                or c.get("card_id") in server_id_set
                            ]
                            removed = before_count - len(cache.cards)
                            if removed > 0:
                                print(f"🗑️ 清理了 {removed} 张过期缓存卡片")

                        # 添加新卡片
                        added = 0
                        for card in daily_cards:
                            card_id = card.get("card_id")
                            if not cache.get_card_by_id(card_id) and not cache.has_equivalent_card(card):
                                card["id"] = card_id
                                card["category"] = "extended"
                                # 将照片文件名转为本地URL
                                if "photo_paths" in card and card["photo_paths"]:
                                    import urllib.parse

                                    card["photo_paths"] = [
                                        f"/photo/{urllib.parse.quote(p)}"
                                        if not p.startswith("/photo/")
                                        else p
                                        for p in card["photo_paths"]
                                    ]
                                cache.cards.append(card)
                                added += 1
                        if added > 0:
                            print(f"📦 从服务端新增 {added} 张卡片到本地缓存")

                        # 只有当有实际变化时才保存缓存
                        if added > 0 or removed > 0:
                            cache._save_cache()
                    except Exception as e:
                        print(f"⚠ 保存卡片失败: {e}")

                # 同步服务端公网地址（用于客户端连接服务端）
                server_public_url = data.get("server_public_url")
                if server_public_url:
                    if self.server_url != server_public_url:
                        self.server_url = server_public_url
                        self.save_config()
                        print(f"🔗 服务端地址已同步: {self.server_url}")
                        logger.info(f"服务端地址已同步: {self.server_url}")

                # 检查是否需要刷新凭证（tunnel被重新分配）
                if data.get("requires_refresh"):
                    print(f"🔄 凭证已失效，正在重新获取...")
                    logger.info("凭证已失效，正在重新获取...")
                    self.check_and_refresh_credentials()

                # 更新付费状态和配额信息（如果服务端返回了）
                if data.get("success") and "client_info" in data:
                    client_info = data["client_info"]
                    old_is_paid = self.is_paid
                    new_is_paid = client_info.get("is_paid", False)

                    if old_is_paid != new_is_paid:
                        self.is_paid = new_is_paid
                        self.save_config()
                        print(
                            f"💰 付费状态已更新: {'付费用户' if new_is_paid else '免费用户'}"
                        )
                        logger.info(f"付费状态已更新: is_paid={new_is_paid}")

                    # 同步 subdomain 和 public_url
                    server_subdomain = client_info.get("subdomain")
                    server_public_url = client_info.get("public_url")
                    if server_subdomain and server_public_url:
                        if (
                            self.subdomain != server_subdomain
                            or self.public_url != server_public_url
                        ):
                            self.subdomain = server_subdomain
                            self.public_url = server_public_url
                            self.save_config()
                            print(f"🔗 公网地址已同步: {self.public_url}")
                            logger.info(f"公网地址已同步: {self.public_url}")

                    # 同步 ai_child_id
                    server_ai_child_id = client_info.get("ai_child_id")
                    if server_ai_child_id:
                        current_ai_child_id = USER_CONFIG.get("ai_child_id")
                        if current_ai_child_id != server_ai_child_id:
                            USER_CONFIG["ai_child_id"] = server_ai_child_id
                            self.save_config()
                            print(f"🤖 AI关联已同步: {server_ai_child_id[:8]}...")
                            logger.info(f"AI关联已同步: {server_ai_child_id}")
                            # 更新 CLIENT_CONFIG
                            CLIENT_CONFIG["ai_service"]["child_id"] = server_ai_child_id

                    # 保存配额信息到全局变量，供前端使用
                    if "quota" in client_info:
                        quota = client_info["quota"]
                        # 这里可以添加保存配额信息的逻辑

                return True
            else:
                print(f"⚠ 心跳失败: {response.status_code}")
                logger.warning(f"心跳失败: {response.status_code}")
                return False

        except Exception as e:
            print(f"⚠ 心跳错误: {e}")
            logger.error(f"心跳错误: {e}")
            return False

    def start_heartbeat_loop(self):
        """启动后台心跳循环（每 5 分钟发送一次心跳）"""

        def heartbeat_worker():
            print("💓 心跳循环已启动（每 5 分钟一次）")
            last_restart_hour = -1  # 记录上次重启的小时

            while True:
                try:
                    time.sleep(300)  # 5 分钟

                    # 检查并重启 Tunnel（每天凌晨 3 点）
                    current_hour = datetime.now().hour
                    if current_hour == 3 and last_restart_hour != 3:
                        if self._tunnel_credentials and self.tunnel_active:
                            print("🔄 定时重启 Tunnel（凌晨 3 点）...")
                            logger.info("定时重启 Tunnel")
                            self.stop_cloudflare_tunnel()
                            time.sleep(2)
                            self.start_cloudflare_tunnel(self._tunnel_credentials)
                        last_restart_hour = 3
                    elif current_hour != 3:
                        last_restart_hour = -1

                    # 检查 Tunnel 进程是否存活
                    if self.tunnel_active and self._tunnel_credentials:
                        if (
                            self.tunnel_process
                            and self.tunnel_process.poll() is not None
                        ):
                            print("⚠ Tunnel 进程已退出，尝试重启...")
                            logger.warning("Tunnel 进程已退出，尝试重启")
                            self.tunnel_active = False
                            time.sleep(2)
                            self.start_cloudflare_tunnel(self._tunnel_credentials)

                    # 如果 tunnel 从未启动过，尝试启动
                    if not self.tunnel_active:
                        # 优先使用本地凭证
                        if not self._tunnel_credentials:
                            local_credentials = self.load_local_credentials()
                            if local_credentials:
                                self._tunnel_credentials = local_credentials
                                # 尝试读取本地子域名
                                local_domain = self.load_local_subdomain()
                                if local_domain:
                                    self.subdomain = local_domain
                                    self.public_url = f"https://{local_domain}"
                                print(f"📂 加载本地凭证用于启动 Tunnel")

                        # 如果有凭证，启动 tunnel
                        if self._tunnel_credentials and self.subdomain:
                            print("🔄 Tunnel 未启动，尝试启动...")
                            logger.info("Tunnel 未启动，尝试启动")
                            self.start_cloudflare_tunnel(self._tunnel_credentials)
                        elif not self._tunnel_credentials:
                            # 本地凭证也不存在，才从服务端获取
                            print("🔄 本地凭证不存在，尝试从服务端获取...")
                            logger.info("本地凭证不存在，尝试从服务端获取")
                            self.fetch_subdomain()

                    # 发送心跳
                    if self.client_id:
                        success = self.send_heartbeat()
                        if success:
                            print("✅ 心跳保持成功")
                        else:
                            print("⚠ 心跳失败，可能需要重新连接")
                    else:
                        print("⚠ client_id 为空，尝试重新注册...")
                        logger.warning("client_id 为空，尝试重新注册")
                        self.register_client()
                        if self.client_id:
                            print("✅ 重新注册成功")
                            logger.info("重新注册成功")
                except Exception as e:
                    print(f"❌ 心跳循环错误：{e}")

        # 启动后台线程
        thread = threading.Thread(target=heartbeat_worker, daemon=True)
        thread.start()
        return thread

    def start_ai_sync_loop(self):
        """AI数据同步由服务端负责，客户端不再执行同步"""
        print("🤖 AI数据同步由服务端负责执行")
        return None

    def check_and_refresh_credentials(self):
        """
        检查是否需要刷新凭证，如果需要则重新获取
        返回: 是否成功刷新
        """
        try:
            response = self.signed_request(
                "POST",
                f"{self.server_url}/czrz/cloudflare/credentials",
                json={"client_id": self.client_id},
                timeout=15,
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    credentials = data.get("credentials", {})
                    if credentials:
                        # 保存凭证到本地
                        self.save_credentials(credentials)

                        # 如果 tunnel 正在运行，先停止
                        if self.tunnel_active:
                            self.stop_cloudflare_tunnel()

                        # 启动 tunnel
                        self.start_cloudflare_tunnel(credentials)

                        print(f"🔄 凭证已刷新")
                        return True
            elif response.status_code == 403:
                # 客户端被禁用
                print(f"⚠ 客户端已被禁用，无法获取凭证")
                logger.warning("客户端已被禁用，无法获取凭证")
                print("🏠 公网访问功能不可用，仅可本地使用")
                return False

            print(f"⚠ 凭证刷新失败")
            return False

        except Exception as e:
            print(f"⚠ 凭证刷新异常: {e}")
            return False

    def get_local_ip(self):
        """获取本地IP"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def get_mac_address(self):
        """获取MAC地址"""
        try:
            import uuid

            mac = ":".join(
                [
                    "{:02x}".format((uuid.getnode() >> elements) & 0xFF)
                    for elements in range(0, 2 * 6, 8)
                ][::-1]
            )
            return mac
        except:
            return "00:00:00:00:00:00"

    def find_available_port(self):
        """
        查找可用端口 - 简化版（假设已有管理员权限）

        扫描范围：3000-3010, 8000-8010, 9000（缩小范围提高性能）
        逻辑：
        1. 按顺序扫描端口
        2. 遇到可用 → 立即使用
        3. 全部占用 → 报错退出
        """
        import ctypes

        # 定义扫描端口范围（缩小范围提高性能）
        priority_ports = [
            3000,
            3001,
            3002,
            3003,
            3004,
            3005,
            3006,
            3007,
            3008,
            3009,
            3010,
        ]
        extended_ports = [
            8000,
            8001,
            8002,
            8003,
            8004,
            8005,
            8006,
            8007,
            8008,
            8009,
            8010,
            9000,
        ]

        # 合并去重
        all_ports = priority_ports + [
            p for p in extended_ports if p not in priority_ports
        ]

        # 记录被占用的端口用于错误提示
        occupied_ports = []

        print("🔍 扫描可用端口...")

        for port in all_ports:
            if self._is_port_available(port):
                print(f"✅ 使用端口: {port}")
                return port
            else:
                occupied_ports.append(port)

        # 全部占用，弹窗提示
        self._show_error_dialog(
            "端口全部被占用",
            "无法找到可用端口 (3000-3010, 8000-8010, 9000)\n"
            "所有端口均被其他程序占用。\n\n"
            "请关闭以下类型程序后重试:\n"
            "• Docker / WSL\n"
            "• 其他 Web 服务器\n"
            "• IDE 调试服务\n\n"
            "如仍无法解决，请联系技术支持。",
            occupied_ports,
        )
        sys.exit(1)

    def _is_port_available(self, port):
        """检查端口是否可用（简化版）"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.3)
            result = sock.connect_ex(("127.0.0.1", port))
            sock.close()
            return result != 0  # 非0表示可用
        except:
            return False

    def _show_error_dialog(self, title, message, occupied_ports=None):
        """显示错误弹窗（Windows）"""
        try:
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)  # MB_ICONERROR
        except:
            print(f"\n❌ {title}")
            print(message)

        # 显示被占用的端口列表
        if occupied_ports:
            for port in occupied_ports[:15]:
                print(f"  - 端口 {port}")
            if len(occupied_ports) > 15:
                print(f"  ... 还有 {len(occupied_ports) - 15} 个端口被占用")
            print("\n常见占用者: Docker、WSL、其他Web服务器、IDE调试服务")

        input("\n按回车键退出...")
        sys.exit(1)


# ==================== Flask 应用配置 ====================
def get_template_folder():
    """获取模板文件夹路径（兼容 PyInstaller）"""
    if hasattr(sys, "_MEIPASS"):
        # PyInstaller 打包环境
        return os.path.join(sys._MEIPASS, "templates")
    else:
        # 普通运行环境
        return os.path.join(os.path.dirname(__file__), "templates")


def get_static_folder():
    """获取静态文件夹路径（兼容 PyInstaller）"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, "static")
    else:
        return os.path.join(os.path.dirname(__file__), "static")


# 创建 Flask 应用
template_folder = get_template_folder()
static_folder = get_static_folder()

app = Flask(__name__, template_folder=template_folder, static_folder=static_folder)

# 配置 Flask session
app.secret_key = USER_CONFIG.get("secret_key") or USER_CONFIG.get("client_id") or "mumu_default_secret_key"
app.config['SESSION_TYPE'] = 'filesystem'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=3650)  # 10年，相当于永久有效
app.config['SESSION_COOKIE_NAME'] = 'mumu_session'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
# 注意：在本地开发或非HTTPS环境下，不要设置 SESSION_COOKIE_SECURE


@app.before_request
def enforce_public_read_only():
    """
    公网只读策略：
    - 内网/本机请求：允许读写
    - 公网请求：仅允许 GET/HEAD/OPTIONS
    """
    source_ip = get_request_source_ip()
    is_private_source = _is_private_or_loopback_ip(source_ip)
    method = (request.method or "").upper()
    is_read_only_method = method in {"GET", "HEAD", "OPTIONS"}
    public_write_whitelist = {
        ("POST", "/api/card/share"),  # 公网允许标记分享
        ("POST", "/message"),  # 公网允许提交留言
    }
    is_whitelisted_public_write = (method, request.path) in public_write_whitelist

    if not is_private_source and not is_read_only_method and not is_whitelisted_public_write:
        logger.warning(
            f"[SECURITY] 拒绝公网写请求: ip={source_ip}, method={method}, path={request.path}"
        )
        return jsonify(
            {
                "success": False,
                "message": "公网访问仅允许只读操作（GET/HEAD/OPTIONS）",
                "source_ip": source_ip,
            }
        ), 403

# 调试信息
logger.info(f"模板文件夹: {template_folder}")
logger.info(f"静态文件夹: {static_folder}")
logger.info(f"模板是否存在: {os.path.exists(template_folder)}")
logger.info(f"静态文件夹是否存在: {os.path.exists(static_folder)}")


# 添加 datetime_format 过滤器
@app.template_filter("datetime_format")
def datetime_format(value, format="%Y-%m-%d %H:%M"):
    """格式化日期时间"""
    if isinstance(value, str):
        try:
            # 尝试解析 ISO 格式字符串
            from datetime import datetime

            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except:
            return value
    if hasattr(value, "strftime"):
        return value.strftime(format)
    return value


@app.context_processor
def inject_quota_info():
    """自动注入配额信息到所有模板"""
    quota_used = 0
    quota_total = 50000
    quota_percent = 0
    token_usage = {"total_tokens": 0, "total_prompt": 0, "total_completion": 0}

    if (
        public_client
        and hasattr(public_client, "client_id")
        and public_client.client_id
    ):
        try:
            resp = public_client.signed_request(
                "GET",
                f"{public_client.server_url}/czrz/quota/status",
                params={"client_id": public_client.client_id},
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    quota = data.get("quota", {})
                    quota_used = quota.get("used", 0)
                    quota_total = quota.get("limit", 50000)
                    quota_percent = (
                        int(quota_used / quota_total * 100) if quota_total > 0 else 0
                    )
        except Exception as e:
            logger.info(f"[Quota] 获取Token使用失败: {e}")

        try:
            resp = public_client.signed_request(
                "GET",
                f"{public_client.server_url}/czrz/client/token-usage",
                params={"client_id": public_client.client_id},
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    token_usage = data.get("token_usage", token_usage)
        except:
            pass

    return dict(
        quota_used=quota_used,
        quota_total=quota_total,
        quota_percent=quota_percent,
        token_usage=token_usage,
    )


# 全局客户端实例（在文件末尾创建，使用延迟连接）
public_client = None

# 本地模式标志（服务端不可用或被禁用时为True）
LOCAL_MODE = False
# 客户端被禁用标志
CLIENT_DISABLED = False


@app.route("/")
def index():
    """首页 - 快速渲染，异步加载数据"""
    from datetime import datetime

    date_param = request.args.get("date")
    if date_param:
        try:
            datetime.strptime(date_param, "%Y-%m-%d")
            display_date = date_param
        except ValueError:
            display_date = datetime.now().strftime("%Y-%m-%d")
    else:
        display_date = datetime.now().strftime("%Y-%m-%d")

    if public_client.is_first_run():
        return redirect("/setup")

    weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    display_datetime = datetime.strptime(display_date, "%Y-%m-%d")
    weekday = weekdays[display_datetime.weekday()]
    today = display_date

    # 获取农历信息
    lunar_date = None
    try:
        from calendar_utils import get_calendar_info

        calendar_info = get_calendar_info(display_datetime.date())
        lunar_date = calendar_info.get("lunar_display")
    except Exception as e:
        print(f"[WARN] 获取农历失败: {e}")

    local_url = f"http://{public_client.get_local_ip()}:{public_client.client_port}"

    today_messages = []
    today_photos = []
    try:
        from photo_manager import PhotoManager
        from pathlib import Path

        media_folders = getattr(public_client, "media_folders", [])
        if media_folders:
            pm = PhotoManager(media_folders, public_client.data_dir)
            photo_list = pm.get_photos_by_date(display_date)
            for photo_info in photo_list:
                from photo_tags import get_photo_tag

                tag_info = get_photo_tag(
                    public_client.client_id, photo_info["filename"]
                )
                today_photos.append(
                    {
                        "filename": photo_info["filename"],
                        "path": photo_info.get("path", photo_info["filename"]),
                        "hash": photo_info.get("hash", ""),
                        "caption": tag_info.get("tag", "") if tag_info else "",
                        "date": display_date,
                        "is_video": Path(photo_info["filename"]).suffix.lower()
                        in [".mp4", ".mov", ".avi"],
                        "tag": tag_info,
                    }
                )
    except Exception as e:
        print(f"[WARN] 扫描照片失败：{e}")

    theme_css = ""
    try:
        from theme_generator import get_or_create_theme, generate_css

        theme = get_or_create_theme(public_client.client_id, public_client.baby_name)
        if theme:
            theme_css = generate_css(theme)
    except Exception as e:
        print(f"[WARN] 加载主题失败: {e}")

    default_photo = None
    default_is_video = False
    if today_photos:
        for p in today_photos:
            if not p.get("is_video"):
                default_photo = p.get("filename")
                break
        if not default_photo:
            default_photo = today_photos[0].get("filename")
            default_is_video = today_photos[0].get("is_video", False)

    featured_photo = None
    featured_photo_hash = None
    try:
        featured_info = get_featured_photo_info(display_date, public_client.client_id)
        if featured_info:
            featured_photo = featured_info.get("filename") or ""
            featured_photo_hash = featured_info.get("file_hash") or ""
    except Exception as e:
        print(f"[WARN] 获取精选照片失败: {e}")

    # 获取天气数据
    weather = None
    is_today = display_date == datetime.now().strftime("%Y-%m-%d")
    if is_today:
        # 今天：从服务端获取实时天气
        try:
            user_city = getattr(public_client, "user_city", "") or "上海"
            weather_url = f"{public_client.server_url}/czrz/baby/log?city={user_city}&date={display_date}"
            if public_client.client_id:
                weather_url += f"&client_id={public_client.client_id}"

            weather_resp = public_client.signed_request("GET", weather_url, timeout=5)
            if weather_resp.status_code == 200:
                weather_data = weather_resp.json()
                weather = weather_data.get("weather") or {}
        except Exception as e:
            print(f"[WARN] 获取天气失败: {e}")

    return render_template(
        "index.html",
        baby_name=public_client.baby_name,
        client_id=public_client.client_id,
        is_paid=public_client.is_paid,
        subdomain=public_client.subdomain,
        public_url=public_client.public_url,
        today=today,
        weekday=weekday,
        lunar_date=lunar_date,
        local_url=local_url,
        theme_css=theme_css,
        client_version=getattr(public_client, "client_version", "2.0.0"),
        today_messages=today_messages,
        today_photos=today_photos,
        avatar_url=getattr(
            public_client, "avatar_url", "/static/images/default-avatar.png"
        ),
        user_city=getattr(public_client, "user_city", ""),
        local_mode=LOCAL_MODE,
        client_disabled=CLIENT_DISABLED,
        default_photo=default_photo,
        default_is_video=default_is_video,
        featured_photo=featured_photo,
        featured_photo_hash=featured_photo_hash,
        index_after_date=getattr(public_client, "index_after_date", ""),
        weather=weather,
    )


@app.route("/cards")
def cards_page():
    """成长卡片页面"""
    baby_name = USER_CONFIG.get("baby_name", "宝宝")
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()
        sync_card_cache_silently()
        cards = [
            convert_photo_path_for_client(card) for card in cache.get_all_cards()
        ]
        cards = select_rotating_cards(cards, per_day=50)

        for card in cards:
            card_id = card.get("id", "")
            card["shared"] = cache.is_shared(card_id)
    except Exception as e:
        print(f"[Card] 读取缓存失败: {e}")
        cards = []

    avatar_url = getattr(
        public_client, "avatar_url", "/static/images/default-avatar.png"
    )

    return render_template("cards.html", baby_name=baby_name, cards=cards, avatar_url=avatar_url)


@app.route("/setup")
def setup():
    """初次设置页面"""
    return render_template(
        "setup.html",
        is_first_run=public_client.is_first_run(),
        baby_name=getattr(public_client, "baby_name", ""),
        baby_gender=getattr(public_client, "baby_gender", ""),
        baby_birthday=getattr(public_client, "baby_birthday", ""),
        user_city=getattr(public_client, "user_city", ""),
        media_folders=getattr(public_client, "media_folders", []),
    )


@app.route("/api/setup", methods=["POST"])
def api_setup():
    """初次设置API"""
    try:
        data = request.get_json()
        baby_name = data.get("baby_name", "").strip()
        baby_gender = data.get("baby_gender", "").strip()
        baby_birthday = data.get("baby_birthday", "").strip()
        user_city = data.get("user_city", "").strip()
        log_style = data.get("log_style", "简练").strip()
        custom_style = data.get("custom_style", "").strip()
        media_folders = data.get("media_folders", [])
        admin_password = data.get("admin_password", "").strip()

        if not baby_name:
            return jsonify({"success": False, "message": "请输入宝宝名字"})

        if not media_folders or len(media_folders) == 0:
            return jsonify({"success": False, "message": "请至少选择一个文件夹"})

        if not public_client.admin_password and not admin_password:
            return jsonify({"success": False, "message": "初次设置需要设置管理密码"})

        from pathlib import Path

        valid_folders = []
        for folder in media_folders:
            if folder.strip():
                folder_path = Path(folder.strip())
                if not folder_path.exists():
                    try:
                        folder_path.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        return jsonify(
                            {
                                "success": False,
                                "message": f"无法创建文件夹 {folder}: {e}",
                            }
                        )
                valid_folders.append(str(folder_path))

        public_client.baby_name = baby_name
        public_client.baby_gender = baby_gender if baby_gender else None
        public_client.baby_birthday = baby_birthday if baby_birthday else None
        public_client.user_city = user_city if user_city else None
        public_client.log_style = log_style if log_style else "简练"
        public_client.custom_style = custom_style
        public_client.media_folders = valid_folders

        index_after_date = data.get("index_after_date", "").strip()
        if index_after_date:
            public_client.index_after_date = index_after_date

        if admin_password:
            public_client.admin_password = admin_password

        # 保存压缩设置（如果提供了）
        compression_settings = data.get("compression_settings")
        if compression_settings:
            try:
                from video_compressor import get_compression_manager

                manager = get_compression_manager()
                if manager:
                    manager.save_settings(compression_settings)
                    print(f"💾 已保存初始压缩设置: {compression_settings}")
            except Exception as e:
                print(f"⚠️ 保存压缩设置失败: {e}")

        public_client.save_config()

        is_first_registration = not public_client.client_id
        if is_first_registration:
            print("🚀 首次设置完成，正在注册到服务端...")
            public_client.connect_to_public_server()

        # 检查索引状态
        index_info = public_client.init_photo_index()

        return jsonify(
            {
                "success": True,
                "message": "设置完成",
                "baby_name": baby_name,
                "folders_count": len(valid_folders),
                "is_first_registration": is_first_registration,
                "index_info": index_info,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"设置失败: {str(e)}"})


@app.route("/api/photo-index/build", methods=["POST"])
@require_local_or_password
def build_photo_index_api():
    """执行照片索引建立（首次）"""
    try:
        result = public_client.build_photo_index()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/api/settings/verify", methods=["POST"])
def verify_settings_password():
    """验证设置页面密码"""
    try:
        data = request.get_json()
        password = data.get("password", "")

        # 检查是否来自 localhost 或 127.0.0.1（免密码）
        remote_addr = request.remote_addr
        if remote_addr in ["127.0.0.1", "localhost"]:
            return jsonify({"success": True, "message": "localhost 访问免密码"})

        # 获取保存的密码
        stored_password = getattr(public_client, "admin_password", None)

        if not stored_password:
            # 没有设置密码，允许进入
            return jsonify({"success": True, "message": "未设置密码"})

        if password == stored_password:
            return jsonify({"success": True, "message": "验证通过"})
        else:
            return jsonify({"success": False, "message": "密码错误"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/settings/current", methods=["GET"])
def get_current_settings():
    """获取当前设置"""
    try:
        return jsonify(
            {
                "success": True,
                "baby_name": getattr(public_client, "baby_name", ""),
                "baby_gender": getattr(public_client, "baby_gender", ""),
                "baby_birthday": getattr(public_client, "baby_birthday", ""),
                "user_city": getattr(public_client, "user_city", ""),
                "log_style": getattr(public_client, "log_style", "简练"),
                "custom_style": getattr(public_client, "custom_style", ""),
                "media_folders": getattr(public_client, "media_folders", []),
                "server_url": getattr(public_client, "server_url", ""),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/profile")
def profile_page():
    """成长画像页面"""
    if public_client.is_first_run():
        return redirect("/setup")

    baby_birthday = getattr(public_client, "baby_birthday", None)
    age_text = ""
    if baby_birthday:
        from datetime import datetime

        try:
            birth = datetime.strptime(baby_birthday, "%Y-%m-%d")
            today = datetime.now()
            months = (today.year - birth.year) * 12 + (today.month - birth.month)
            days = today.day - birth.day
            if days < 0:
                months -= 1
            if months >= 12:
                years = months // 12
                remaining_months = months % 12
                if remaining_months > 0:
                    age_text = f"{years}岁{remaining_months}个月"
                else:
                    age_text = f"{years}岁"
            else:
                age_text = f"{months}个月"
        except:
            age_text = ""

    return render_template(
        "profile.html",
        baby_name=public_client.baby_name,
        baby_birthday=baby_birthday,
        age_text=age_text,
        client_id=public_client.client_id,
        avatar_url=getattr(
            public_client, "avatar_url", "/static/images/default-avatar.png"
        ),
    )


@app.route("/settings")
def settings():
    """设置页面 - 重定向到首页，使用模态框"""
    return redirect("/")


@app.route("/usage")
def usage():
    """使用明细页面"""
    return render_template("usage.html")


@app.route("/quota-exceeded")
def quota_exceeded():
    """配额用完页面"""
    return render_template("quota-exceeded.html")


@app.route("/health")
def health():
    """健康检查"""
    return jsonify(
        {
            "status": "ok",
            "client_id": public_client.client_id,
            "baby_name": public_client.baby_name,
            "is_paid": public_client.is_paid,
            "subdomain": public_client.subdomain,
            "public_url": public_client.public_url,
            "server_domain": public_client.server_domain,
            "server_connected": public_client.client_id is not None,
            "time": datetime.now().isoformat(),
            "version": "public-final-2.0.1",
        }
    )


@app.route("/api/quota/status")
def quota_status():
    """配额状态 - 从服务端获取"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/quota/status",
            params={"client_id": public_client.client_id},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                quota = data.get("quota", {})
                return jsonify(
                    {
                        "success": True,
                        "token_used": quota.get("used", 0),
                        "token_limit": quota.get("limit", 50000),
                        "token_remaining": quota.get("remaining", 50000),
                        "is_paid": data.get("is_paid", False),
                        "tier": "paid" if data.get("is_paid") else "free",
                    }
                )

        return jsonify({"success": False, "message": "获取配额失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/usage/list")
def usage_list():
    """使用明细列表 - 从服务端获取"""
    try:
        filter_type = request.args.get("filter", "today")
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/client/usage-list",
            params={"client_id": public_client.client_id, "filter": filter_type},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "today_tokens": data.get("today_tokens", 0),
                        "total_tokens": data.get("total_tokens", 0),
                        "records": data.get("records", []),
                    }
                )
        return jsonify({"success": False, "message": "获取使用明细失败"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def get_token_usage():
    """从服务端获取token用量"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/client/token-usage",
            params={"client_id": public_client.client_id},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                return data.get(
                    "token_usage",
                    {
                        "total_prompt": 0,
                        "total_completion": 0,
                        "total_tokens": 0,
                    },
                )
    except Exception as e:
        print(f"[WARN] 获取token用量失败: {e}")
    return {"total_prompt": 0, "total_completion": 0, "total_tokens": 0}


@app.route("/photo/<filename>")
def get_photo(filename):
    """获取照片"""
    from flask import send_file
    from pathlib import Path

    try:
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        # 递归搜索所有媒体文件夹
        for folder in media_folders:
            folder_path = Path(folder)
            for file_path in folder_path.rglob(filename):
                if file_path.is_file():
                    return send_file(file_path)

        return jsonify({"success": False, "message": "照片不存在"}), 404

    except Exception as e:
        print(f"[ERROR] 获取照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/photo/hash/<file_hash>")
def get_photo_by_hash(file_hash):
    """通过哈希获取照片 - 用于精选照片显示"""
    from flask import send_file
    from pathlib import Path

    try:
        from photo_manager import PhotoManager
        from video_compressor import get_compression_manager

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_hash(file_hash)

        if not entry or not Path(entry["path"]).exists():
            return jsonify({"success": False, "message": "照片不存在"}), 404

        # 获取压缩设置
        manager = get_compression_manager()
        if manager:
            settings = manager.get_settings()
            if settings.get("image_quality") != "原图" and not entry.get(
                "is_video", False
            ):
                compressed_path = manager.get_compressed_file_path(
                    Path(entry["path"]), entry["filename"]
                )
                if compressed_path and compressed_path.exists():
                    return send_file(compressed_path)

        return send_file(Path(entry["path"]))

    except Exception as e:
        print(f"[ERROR] 通过哈希获取照片失败: {e}")
        return jsonify({"success": False, "message": str(e)}), 500


# ==================== 缩略图和视频服务 ====================

# 缩略图缓存目录
THUMB_DIR = USER_DATA_DIR / "thumbnails"
THUMB_DIR.mkdir(exist_ok=True)


def get_or_create_thumbnail(image_path: Path, size=(400, 400)) -> Path:
    """生成或获取照片缩略图（本地处理，自动修正EXIF方向）"""
    thumb_filename = f"{image_path.stem}_thumb{image_path.suffix}"
    thumb_path = THUMB_DIR / thumb_filename

    # 如果缩略图已存在且比原图新，直接返回
    if thumb_path.exists():
        if thumb_path.stat().st_mtime >= image_path.stat().st_mtime:
            return thumb_path

    # 生成缩略图
    try:
        from PIL import Image
        from PIL import ImageOps

        with Image.open(image_path) as img:
            # 根据EXIF方向自动旋转（解决手机照片方向问题）
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass  # 没有EXIF或处理失败，保持原样

            # 转换为RGB（处理RGBA等模式）
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # 等比例缩放
            img.thumbnail(size, Image.Resampling.LANCZOS)
            img.save(thumb_path, quality=85, optimize=True)

        return thumb_path
    except Exception as e:
        print(f"[WARN] 生成缩略图失败: {e}")
        return image_path  # 失败时返回原图


def get_or_create_video_thumbnail(video_path: Path) -> Path:
    """生成或获取视频缩略图（本地处理）
    优先使用 imageio-ffmpeg（自动处理旋转），失败时尝试 OpenCV
    """
    thumb_filename = f"{video_path.stem}_vthumb.jpg"
    thumb_path = THUMB_DIR / thumb_filename

    # 如果缩略图已存在且比原视频新，直接返回
    if thumb_path.exists():
        if thumb_path.stat().st_mtime >= video_path.stat().st_mtime:
            return thumb_path

    # 方法1: 使用 imageio-ffmpeg（自动处理旋转）
    try:
        import subprocess

        ffmpeg_path = None
        try:
            import imageio_ffmpeg

            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
        except:
            pass

        if ffmpeg_path:
            cmd = [
                ffmpeg_path,
                "-y",
                "-i",
                str(video_path),
                "-ss",
                "00:00:01",
                "-vframes",
                "1",
                "-vf",
                "scale=400:-1",
                "-q:v",
                "2",
                str(thumb_path),
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=300)

            if thumb_path.exists():
                return thumb_path
            else:
                raise Exception("ffmpeg 未能生成缩略图")

    except Exception as e:
        print(f"[INFO] imageio-ffmpeg 生成缩略图失败: {e}，尝试 OpenCV...")

    # 方法2: 使用 OpenCV
    try:
        import cv2

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise Exception("无法打开视频文件")

        # 获取视频旋转信息
        rotation = 0
        try:
            # 尝试获取旋转元数据
            meta = int(cap.get(cv2.CAP_PROP_ORIENTATION_META))
            if meta == 90:
                rotation = cv2.ROTATE_90_CLOCKWISE
            elif meta == 180:
                rotation = cv2.ROTATE_180
            elif meta == 270:
                rotation = cv2.ROTATE_90_COUNTERCLOCKWISE
        except:
            pass

        fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frame_pos = int(fps * 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_pos)

        ret, frame = cap.read()
        cap.release()

        if ret and frame is not None:
            # 应用旋转
            if rotation != 0:
                frame = cv2.rotate(frame, rotation)

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            from PIL import Image

            img = Image.fromarray(frame)
            img.thumbnail((400, 400), Image.Resampling.LANCZOS)
            img.save(thumb_path, quality=85, optimize=True)
            return thumb_path
        else:
            raise Exception("无法读取视频帧")

    except Exception as e1:
        print(f"[INFO] OpenCV 生成缩略图失败: {e1}")

    # 返回默认视频缩略图
    default_thumb = Path(__file__).parent / "static" / "images" / "video-thumb.jpg"
    if default_thumb.exists():
        return default_thumb

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (400, 300), color="#333333")
    draw = ImageDraw.Draw(img)
    draw.text((150, 140), "视频", fill="white")
    img.save(thumb_path, quality=85)
    return thumb_path


@app.route("/photo/thumb/<filename>")
def get_photo_thumb(filename):
    """获取照片缩略图"""
    from flask import send_file

    photo_path = None
    try:
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_filename(filename)

        if entry:
            photo_path = Path(entry["path"])
            if not photo_path.exists():
                photo_path = None

        if not photo_path:
            for folder in media_folders:
                folder_path = Path(folder)
                for file_path in folder_path.rglob(filename):
                    if file_path.is_file():
                        photo_path = file_path
                        break
                if photo_path:
                    break

        if not photo_path:
            return jsonify({"success": False, "message": "照片不存在"}), 404

        thumb_path = get_or_create_thumbnail(photo_path, size=(400, 400))
        return send_file(thumb_path)

    except Exception as e:
        print(f"[ERROR] 获取缩略图失败: {e}")
        if photo_path and photo_path.exists():
            try:
                print(f"[INFO] 缩略图失败，回退原图: {photo_path}")
                return send_file(photo_path)
            except Exception as fallback_error:
                print(f"[ERROR] 回退原图也失败: {fallback_error}")
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/video/thumb/<filename>")
def get_video_thumb(filename):
    """获取视频缩略图（通过索引查找）"""
    from flask import send_file

    try:
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_filename(filename)

        if not entry:
            return jsonify({"success": False, "message": "视频不在索引中"}), 404

        video_path = Path(entry["path"])
        if not video_path.exists():
            return jsonify({"success": False, "message": "视频文件不存在"}), 404

        thumb_path = get_or_create_video_thumbnail(video_path)
        return send_file(thumb_path)

        # 返回默认缩略图
        default_thumb = Path(__file__).parent / "static" / "images" / "video-thumb.jpg"
        if default_thumb.exists():
            return send_file(default_thumb)

        return jsonify({"success": False, "message": "视频不存在"}), 404

    except Exception as e:
        print(f"[ERROR] 获取视频缩略图失败: {e}")
        # 返回默认缩略图
        default_thumb = Path(__file__).parent / "static" / "images" / "video-thumb.jpg"
        if default_thumb.exists():
            return send_file(default_thumb)
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/video/<filename>")
def get_video(filename):
    """获取视频文件 - 根据设置返回原视频或压缩版"""
    from flask import send_file

    try:
        from photo_manager import PhotoManager
        from video_compressor import get_compression_manager

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"}), 404

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_filename(filename)

        if not entry or not Path(entry["path"]).exists():
            return jsonify({"success": False, "message": "视频不存在"}), 404

        # 获取压缩设置
        manager = get_compression_manager()
        if manager:
            settings = manager.get_settings()
            # 如果设置不是原图，尝试返回压缩版
            if settings.get("video_quality") != "原图":
                compressed_path = manager.get_compressed_file_path(
                    Path(entry["path"]), filename
                )
                if compressed_path and compressed_path.exists():
                    return send_file(
                        compressed_path,
                        mimetype="video/mp4",
                        conditional=True,
                        download_name=filename,
                    )

        # 返回原视频
        return send_file(
            Path(entry["path"]),
            mimetype="video/mp4",
            conditional=True,
            download_name=filename,
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/voice/<filename>")
def get_voice(filename):
    """获取语音文件 - 从服务端代理"""
    from flask import Response

    try:
        # 安全检查：防止目录遍历
        if ".." in filename or "/" in filename:
            return jsonify({"success": False, "message": "无效文件名"}), 400

        # 从服务端获取语音文件
        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/voice/{public_client.client_id}/{filename}",
            timeout=30,
            stream=True,
        )

        if response.status_code == 200:
            # 确定 MIME 类型
            mime_type = "audio/mpeg" if filename.endswith(".mp3") else "audio/webm"

            # 流式返回
            return Response(
                response.iter_content(chunk_size=8192),
                mimetype=mime_type,
                headers={"Content-Disposition": f"inline; filename={filename}"},
            )
        else:
            return jsonify(
                {"success": False, "message": "语音文件不存在", "filename": filename}
            ), 404

    except Exception as e:
        return jsonify({"success": False, "message": f"获取语音失败: {str(e)}"}), 500


@app.route("/api/photo/delete", methods=["POST"])
@require_local_or_password
def delete_photo():
    """删除照片 - 从索引中移除（不删除实际文件）"""
    try:
        data = request.get_json()
        filename = data.get("filename")

        if not filename:
            return jsonify({"success": False, "message": "缺少文件名"})

        # 使用 PhotoManager 从索引中移除
        media_folders = getattr(public_client, "media_folders", [])
        if media_folders:
            from photo_manager import PhotoManager

            pm = PhotoManager(media_folders, public_client.data_dir)
            success = pm.remove_photo(filename)

            if success:
                return jsonify(
                    {
                        "success": True,
                        "message": "照片已从索引中移除（文件仍保留在原始位置）",
                    }
                )

        return jsonify({"success": False, "message": "照片不存在或移除失败"})

    except Exception as e:
        return jsonify({"success": False, "message": f"删除失败: {str(e)}"})


@app.route("/api/photo/<filename>")
def get_photo_detail(filename):
    """获取照片详情 - 使用索引"""
    try:
        from photo_manager import PhotoManager

        media_folders = getattr(public_client, "media_folders", [])

        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"})

        pm = PhotoManager(media_folders, public_client.data_dir)
        entry = pm.get_photo_by_filename(filename)

        if entry:
            return jsonify(
                {
                    "success": True,
                    "filename": entry["filename"],
                    "path": entry["path"],
                    "date": entry["date"],
                    "url": f"/photo/{filename}",
                    "is_video": entry.get("is_video", False),
                    "size": entry.get("size", 0),
                }
            )
        else:
            return jsonify({"success": False, "message": "照片不存在"})

    except Exception as e:
        return jsonify({"success": False, "message": f"查询失败: {str(e)}"})


# ==================== 照片上传API ====================


@app.route("/upload", methods=["POST"])
@require_local_or_password
def upload_photos():
    """上传照片到本地文件夹"""
    try:
        # 检查是否配置了媒体文件夹
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify(
                {"success": False, "message": "未配置媒体保存文件夹，请在设置中配置"}
            )

        # 初始化照片管理器（支持多文件夹）
        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)

        # 获取上传的文件和日期
        files = request.files.getlist("files")
        caption = request.form.get("caption", "")
        upload_date = request.form.get("date", "")  # 用户指定的日期

        if not files:
            return jsonify({"success": False, "message": "没有选择文件"})

        saved_files = []
        skipped_files = []
        for file in files:
            if file.filename:
                # 保存到临时文件（使用安全文件名）
                from pathlib import Path
                import tempfile

                temp_dir = Path(tempfile.gettempdir())
                safe_filename = secure_filename(file.filename)

                # 检查文件是否已存在
                target_path = Path(media_folders[0]) / "new_upload" / safe_filename
                if target_path.exists():
                    print(f"⏭️ 跳过已存在文件: {safe_filename}")
                    skipped_files.append(safe_filename)
                    continue

                temp_path = temp_dir / safe_filename
                file.save(temp_path)

                # 使用PhotoManager保存并分类，传入指定日期
                result = pm.save_uploaded_photo_with_date(
                    temp_path, safe_filename, upload_date
                )

                # 添加标注（如果有）
                if caption:
                    result["caption"] = caption

                saved_files.append(result)

                # 清理临时文件
                temp_path.unlink(missing_ok=True)

        # ========== 勋章检测逻辑（已禁用） ==========
        # 勋章功能已暂停，代码保留备用
        badges_earned = []
        # try:
        #     from badge_detector import (
        #         check_count_badges,
        #         check_achievement_badges,
        #         check_special_date_badges,
        #         save_badge,
        #     )
        #     from select_best_photo import analyze_all_photos
        #     from database import user_session
        #     from datetime import datetime
        #
        #     # 获取上传日期
        #     today = upload_date if upload_date else datetime.now().strftime("%Y-%m-%d")
        #
        #     # 检查照片数量勋章
        #     with user_session(public_client.client_id) as session:
        #         count_badges = check_count_badges(
        #             public_client.client_id, today, len(saved_files), session
        #         )
        #         for badge_type in count_badges:
        #             save_badge(public_client.client_id, badge_type, today, today)
        #             badges_earned.append(badge_type)
        #
        #     # AI分析照片内容（异步执行，不阻塞上传）
        #     if saved_files:
        #         photo_paths = [f["path"] for f in saved_files if f.get("path")]
        #         if photo_paths:
        #             # 在后台线程中执行
        #             def analyze_and_check_badges():
        #                 try:
        #                     from badge_config import BADGE_CONFIG
        #
        #                     # AI分析
        #                     photo_analysis = analyze_all_photos(
        #                         photo_paths,
        #                         max_photos=20,
        #                         client_id=public_client.client_id,
        #                         date=today
        #                     )
        #
        #                     # 检查成就勋章
        #                     with user_session(public_client.client_id) as session:
        #                         achievement_badges = check_achievement_badges(
        #                             public_client.client_id, today, photo_analysis, session
        #                         )
        #                         for badge_info in achievement_badges:
        #                             badge_type = badge_info["badge_type"]
        #                             trigger_photo = badge_info.get("trigger_photo")
        #                             save_badge(
        #                                 public_client.client_id,
        #                                 badge_type,
        #                                 today,
        #                                 today,
        #                                 trigger_photo
        #                             )
        #
        #                     # 检查特殊日期勋章
        #                     with user_session(public_client.client_id) as session:
        #                         special_badges = check_special_date_badges(
        #                             public_client.client_id, today, session
        #                         )
        #                         for badge_type in special_badges:
        #                             save_badge(public_client.client_id, badge_type, today, today)
        #
        #                 except Exception as e:
        #                     print(f"[Badge] 勋章检测异常: {e}")
        #
        #             # 启动后台线程
        #             import threading
        #             badge_thread = threading.Thread(target=analyze_and_check_badges, daemon=True)
        #             badge_thread.start()
        #
        # except Exception as e:
        #     print(f"[Badge] 勋章检测失败: {e}")

        # 返回结果，包含新获得的勋章
        badge_info = []
        for badge_type in badges_earned:
            from badge_config import get_badge_config

            config = get_badge_config(badge_type)
            if config:
                badge_info.append(
                    {
                        "type": badge_type,
                        "name": config["name"],
                        "icon": config["icon"],
                        "description": config["description"],
                    }
                )

        return jsonify(
            {
                "success": True,
                "message": f"成功上传 {len(saved_files)} 个文件",
                "count": len(saved_files),
                "files": saved_files,
                "badges": badge_info,  # 新获得的勋章
            }
        )

    except PermissionError as e:
        print(f"[ERROR] 上传权限错误: {e}")
        return jsonify(
            {
                "success": False,
                "message": "保存照片失败：目标文件夹没有写入权限，请检查文件夹权限或更换保存位置",
            }
        )
    except Exception as e:
        print(f"[ERROR] 上传失败: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": f"上传失败: {str(e)}"})


@app.route("/api/settings/photo_folder", methods=["GET", "POST"])
@require_local_or_password
def photo_folder_settings():
    """获取/修改照片文件夹设置"""
    if request.method == "GET":
        # 获取当前设置
        return jsonify(
            {
                "success": True,
                "photo_folder": getattr(public_client, "photo_folder", None),
            }
        )

    elif request.method == "POST":
        # POST修改设置需要权限检查（使用统一的 session 检查）
        check_result = check_local_or_password()
        if check_result:
            return check_result

        # 修改设置
        data = request.get_json()
        new_folder = data.get("photo_folder", "").strip()

        if not new_folder:
            return jsonify({"success": False, "message": "文件夹路径不能为空"})

        # 验证文件夹
        from pathlib import Path

        folder_path = Path(new_folder)

        try:
            if not folder_path.exists():
                folder_path.mkdir(parents=True, exist_ok=True)

            # 更新配置
            public_client.photo_folder = str(folder_path)
            return jsonify(
                {
                    "success": True,
                    "message": "照片文件夹已更新",
                    "photo_folder": str(folder_path),
                }
            )

        except Exception as e:
            return jsonify({"success": False, "message": f"无法设置文件夹: {str(e)}"})


@app.route("/api/settings/basic", methods=["POST"])
@require_local_or_password
def save_basic_settings():
    """保存基本设置"""
    try:
        data = request.get_json()
        baby_name = data.get("baby_name", "").strip()
        baby_gender = data.get("baby_gender", "").strip()
        baby_birthday = data.get("baby_birthday", "").strip()
        user_city = data.get("user_city", "").strip()

        if not baby_name:
            return jsonify({"success": False, "message": "宝宝名字不能为空"})

        public_client.baby_name = baby_name
        public_client.baby_gender = baby_gender if baby_gender else None
        public_client.baby_birthday = baby_birthday if baby_birthday else None
        if user_city:
            public_client.user_city = user_city

        public_client.save_config()

        return jsonify(
            {
                "success": True,
                "message": "设置已保存",
                "baby_name": baby_name,
                "baby_gender": baby_gender,
                "baby_birthday": baby_birthday,
                "user_city": user_city,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"保存失败: {str(e)}"})


@app.route("/api/settings/log-style", methods=["POST"])
@require_local_or_password
def save_log_style():
    """保存日志风格设置"""
    try:
        data = request.get_json()
        log_style = data.get("log_style", "简练").strip()
        custom_style = data.get("custom_style", "").strip()

        public_client.log_style = log_style
        public_client.custom_style = custom_style
        public_client.save_config()

        return jsonify(
            {
                "success": True,
                "message": "日志风格已保存",
                "log_style": log_style,
                "custom_style": custom_style,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"保存失败: {str(e)}"})


@app.route("/api/settings/avatar", methods=["POST"])
@require_local_or_password
def upload_avatar():
    """上传头像"""
    try:
        from werkzeug.utils import secure_filename
        from PIL import Image

        if "avatar" not in request.files:
            return jsonify({"success": False, "message": "没有选择文件"})

        file = request.files["avatar"]
        if not file or not file.filename:
            return jsonify({"success": False, "message": "文件名为空"})

        # 检查文件类型
        allowed_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        ext = Path(file.filename).suffix.lower()
        if ext not in allowed_extensions:
            return jsonify(
                {"success": False, "message": "仅支持 JPG, PNG, GIF, WebP 格式"}
            )

        # 创建头像目录
        avatar_dir = USER_DATA_DIR / "avatars"
        avatar_dir.mkdir(exist_ok=True)

        # 保存并压缩头像
        avatar_filename = f"avatar_{public_client.client_id}{ext}"
        avatar_path = avatar_dir / avatar_filename

        # 保存文件
        file.save(avatar_path)

        # 压缩并裁剪为圆形
        with Image.open(avatar_path) as img:
            # 转换为RGBA
            if img.mode != "RGBA":
                img = img.convert("RGBA")

            # 裁剪为正方形（从中心）
            size = min(img.size)
            left = (img.width - size) // 2
            top = (img.height - size) // 2
            img = img.crop((left, top, left + size, top + size))

            # 缩放为 200x200
            img = img.resize((200, 200), Image.Resampling.LANCZOS)

            # 创建圆形遮罩
            mask = Image.new("L", (200, 200), 0)
            from PIL import ImageDraw

            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, 200, 200), fill=255)

            # 应用遮罩
            output = Image.new("RGBA", (200, 200), (255, 255, 255, 0))
            output.paste(img, (0, 0))
            output.putalpha(mask)

            # 保存
            output.save(avatar_path, "PNG")

        # 更新配置
        avatar_url = f"/api/avatar/{avatar_filename}"
        public_client.avatar_url = avatar_url
        public_client.save_config()

        return jsonify(
            {"success": True, "message": "头像上传成功", "avatar_url": avatar_url}
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"上传失败: {str(e)}"})


@app.route("/api/avatar/<filename>")
def get_avatar(filename):
    """获取头像文件"""
    try:
        avatar_dir = USER_DATA_DIR / "avatars"
        avatar_path = avatar_dir / filename

        if avatar_path.exists():
            return send_file(avatar_path, mimetype="image/png")
        else:
            # 返回默认头像
            default_avatar = (
                Path(__file__).parent / "static" / "images" / "default-avatar.png"
            )
            if default_avatar.exists():
                return send_file(default_avatar)
            return jsonify({"success": False, "message": "头像不存在"}), 404

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/api/photos/by_date/<date>")
def get_photos_by_date(date):
    """获取指定日期的照片"""
    try:
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"})

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)

        photos = pm.get_photos_by_date(date)

        return jsonify(
            {"success": True, "date": date, "count": len(photos), "photos": photos}
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


scan_progress = {
    "running": False,
    "current": 0,
    "total": 0,
    "message": "",
    "result": None,
}


def scan_progress_callback(current, total, message):
    """扫描进度回调"""
    global scan_progress
    scan_progress["current"] = current
    scan_progress["total"] = total
    scan_progress["message"] = message


def run_scan_in_background(media_folders, data_dir, start_date=None, end_date=None):
    """后台扫描线程"""
    global scan_progress
    from photo_manager import PhotoManager

    try:
        pm = PhotoManager(media_folders, data_dir)
        result = pm.scan_existing_photos(
            progress_callback=scan_progress_callback,
            start_date=start_date,
            end_date=end_date,
        )
        scan_progress["result"] = result
        scan_progress["message"] = (
            f"扫描完成：新增 {result['new']} 个，已有 {result['existing']} 个，总计 {result['total']} 个"
        )
    except Exception as e:
        scan_progress["result"] = {"error": str(e)}
        scan_progress["message"] = f"扫描失败：{str(e)}"
    finally:
        scan_progress["running"] = False


@app.route("/api/photos/scan", methods=["POST"])
@require_local_or_password
def scan_photos():
    """手动触发照片扫描（后台执行）"""
    global scan_progress

    if scan_progress["running"]:
        return jsonify({"success": False, "message": "扫描正在进行中，请稍候"})

    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders:
        return jsonify({"success": False, "message": "未配置媒体文件夹"})

    # 获取起始日期参数
    data = request.get_json() or {}
    start_date = data.get("start_date", "")

    # 保存日期范围设置到配置
    if start_date:
        public_client.index_after_date = start_date
        public_client.save_config()

    scan_progress = {
        "running": True,
        "current": 0,
        "total": 0,
        "message": "正在启动扫描...",
        "result": None,
    }

    import threading

    thread = threading.Thread(
        target=run_scan_in_background,
        args=(media_folders, public_client.data_dir, start_date or None, None),
    )
    thread.daemon = True
    thread.start()

    return jsonify({"success": True, "message": "扫描已开始"})


@app.route("/api/photos/scan/progress", methods=["GET"])
def get_scan_progress():
    """获取扫描进度"""
    global scan_progress
    return jsonify(scan_progress)


@app.route("/api/calendar/<month>")
def get_calendar_data(month):
    """获取指定月份的日历数据 - 优化版本，使用增量缓存"""
    try:
        year, month_num = map(int, month.split("-"))
        import calendar
        from datetime import datetime, timedelta

        # 获取该月第一天和最后一天
        first_day = 1
        last_day = calendar.monthrange(year, month_num)[1]
        month_start = f"{year}-{month_num:02d}-01"
        month_end = f"{year}-{month_num:02d}-{last_day:02d}"

        # 获取照片日期 - 优先使用按月缓存
        media_folders = getattr(public_client, "media_folders", [])
        photo_dates = set()

        if media_folders:
            cache_key = f"_photo_dates_cache_{month}"
            cache_time_key = f"_photo_dates_cache_time_{month}"

            # 检查缓存是否有效（5分钟内）
            cache_valid = False
            if hasattr(app, cache_key) and hasattr(app, cache_time_key):
                cache_age = (
                    datetime.now() - getattr(app, cache_time_key)
                ).total_seconds()
                if cache_age < 300:  # 5分钟缓存
                    cache_valid = True
                    photo_dates = getattr(app, cache_key)

            if not cache_valid:
                # 缓存失效，重新加载
                from photo_manager import PhotoManager

                pm = PhotoManager(media_folders, public_client.data_dir)

                # 使用优化的按月查询方法
                photo_dates = set(pm.get_dates_by_month(year, month_num))

                # 更新缓存
                setattr(app, cache_key, photo_dates)
                setattr(app, cache_time_key, datetime.now())

        # 从服务端获取该月日志状态
        log_dates = set()
        try:
            resp = public_client.signed_request(
                "GET",
                f"{public_client.server_url}/czrz/client/logs/dates",
                params={"client_id": public_client.client_id, "month": month},
                timeout=5,
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    log_dates = set(result.get("dates", []))
        except Exception as e:
            print(f"[WARN] 获取日志日期失败: {e}")

        # 为每一天生成数据
        calendar_data = {}
        for day in range(first_day, last_day + 1):
            date_str = f"{year}-{month_num:02d}-{day:02d}"

            has_photos = date_str in photo_dates
            has_log = date_str in log_dates

            calendar_data[date_str] = {
                "has_content": has_photos or has_log,
                "has_photos": has_photos,
                "has_log": has_log,
            }

        return jsonify({"success": True, "month": month, "data": calendar_data})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/date/<date>")
def get_date_details(date):
    """获取指定日期的详细信息（AJAX调用）- 优先从已保存数据读取"""
    try:
        from datetime import datetime, timedelta

        is_today = date == datetime.now().strftime("%Y-%m-%d")

        # 初始化精选照片变量
        featured_photo = None
        featured_photo_hash = None

        # 获取照片
        media_folders = getattr(public_client, "media_folders", [])
        photos = []
        if media_folders:
            from photo_manager import PhotoManager
            from photo_tags import get_photo_tag

            pm = PhotoManager(media_folders, public_client.data_dir)
            photo_list = pm.get_photos_by_date(date)

            # 添加标签信息
            for photo_info in photo_list:
                tag_info = get_photo_tag(
                    public_client.client_id, photo_info["filename"]
                )
                photo_info["tag"] = tag_info
                photo_info["caption"] = tag_info.get("tag", "") if tag_info else ""
                photos.append(photo_info)

        # 获取精选照片信息（从服务端获取）
        try:
            featured_info = get_featured_photo_info(date, public_client.client_id)
            print(
                f"[DEBUG] 获取精选照片: date={date}, client_id={public_client.client_id}, info={featured_info}"
            )
            if featured_info:
                featured_photo = featured_info.get("filename") or ""
                featured_photo_hash = featured_info.get("file_hash") or ""
        except Exception as e:
            print(f"[WARN] 获取精选照片失败: {e}")

        # 获取日志和相关数据（从服务端获取）
        response_data = {
            "date": date,
            "is_today": is_today,
            "photos": photos,
            "has_content": len(photos) > 0,
            "featured_photo": featured_photo,
            "featured_photo_hash": featured_photo_hash,
        }

        # 从服务端获取日志
        try:
            user_city = getattr(public_client, "user_city", None) or "上海"
            log_url = (
                f"{public_client.server_url}/czrz/baby/log?city={user_city}&date={date}"
            )
            if public_client.client_id:
                log_url += f"&client_id={public_client.client_id}"

            log_resp = public_client.signed_request("GET", log_url, timeout=10)
            if log_resp.status_code == 200:
                resp_data = log_resp.json()
                if resp_data.get("success"):
                    # 日志内容
                    if resp_data.get("log"):
                        response_data["log"] = {
                            "content": resp_data.get("log", ""),
                            "generated_at": resp_data.get(
                                "generated_at", datetime.now().isoformat()
                            ),
                        }
                        response_data["has_content"] = True

                    # 天气、农历等信息（即使没有日志也返回）
                    if resp_data.get("weather"):
                        response_data["weather"] = resp_data.get("weather")
                    if resp_data.get("lunar"):
                        response_data["lunar"] = resp_data.get("lunar")
                    if resp_data.get("weekday"):
                        response_data["weekday"] = resp_data.get("weekday")
                    if resp_data.get("news"):
                        response_data["news"] = resp_data.get("news")
        except Exception as e:
            print(f"[WARN] 从服务端获取日志失败: {e}")

        # 从服务端获取留言
        try:
            if public_client.client_id:
                messages_url = f"{public_client.server_url}/czrz/messages/{public_client.client_id}/{date}"
                messages_resp = public_client.signed_request(
                    "GET", messages_url, timeout=10
                )
                if messages_resp.status_code == 200:
                    messages_data = messages_resp.json()
                    if messages_data.get("success"):
                        response_data["messages"] = messages_data.get("messages", [])
                        if response_data["messages"]:
                            response_data["has_content"] = True
        except Exception as e:
            print(f"[WARN] 从服务端获取留言失败: {e}")
            response_data["messages"] = []

        return jsonify({"success": True, **response_data})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/log/generate", methods=["POST"])
@require_local_or_password
def api_generate_log():
    """生成日志API - 支持照片和用户输入"""
    try:
        data = request.get_json()
        include_news = data.get("include_news", True)
        user_note = data.get("user_note", "")
        photo_filename = data.get("photo_filename")

        photo_description = None
        if photo_filename:
            try:
                time_part = (
                    photo_filename.split("_")[1].split(".")[0]
                    if "_" in photo_filename
                    else ""
                )
                photo_description = f"照片拍摄于今天{time_part[:2]}:{time_part[2:4] if len(time_part) >= 4 else ''}"
            except:
                photo_description = "今天的照片"

        log_city = public_client.user_city

        params = {
            "client_id": public_client.client_id,
            "include_news": include_news,
            "user_note": user_note,
            "photo_description": photo_description,
        }
        if log_city:
            params["city"] = log_city

        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/baby/log",
            params=params,
            timeout=1200,
        )

        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "log": result.get("log"),
                        "city": result.get("city"),
                        "message": "生成成功",
                    }
                )
            else:
                return jsonify(
                    {"success": False, "message": result.get("message", "生成失败")}
                )
        else:
            return jsonify(
                {"success": False, "message": f"服务端错误: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/log/save", methods=["POST"])
@require_local_or_password
def api_save_log():
    """保存编辑后的日志到服务端"""
    try:
        data = request.get_json()
        content = data.get("content", "")
        date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
        metadata = data.get("metadata", {})

        if not content:
            return jsonify({"success": False, "message": "内容不能为空"})

        # 上报到服务端数据库
        try:
            report_data = {
                "client_id": public_client.client_id,
                "date": date,
                "content": content,
                "metadata": {
                    "city": metadata.get("city", public_client.user_city or "未知"),
                    "temperature": metadata.get("temperature"),
                    "condition": metadata.get("condition") or metadata.get("weather"),
                    "lunar": metadata.get("lunar"),
                    "weekday": metadata.get("weekday"),
                    "news": metadata.get("news"),
                },
            }

            resp = post_with_retry(
                public_client.session,
                f"{public_client.server_url}/czrz/log/save",
                report_data,
            )
            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    # 添加通知
                    try:
                        from baby_log_generator import add_notification

                        add_notification(
                            client_id=public_client.client_id,
                            title=f"{public_client.baby_name}的成长日志已更新",
                            message=f"家长编辑了{date}的成长日志",
                            notification_type="log",
                        )
                    except Exception as e:
                        logger.info(f"[通知] 添加通知失败: {e}")
                    return jsonify({"success": True, "message": "保存成功"})
                else:
                    return jsonify(
                        {"success": False, "message": result.get("message", "保存失败")}
                    )
            else:
                return jsonify(
                    {"success": False, "message": f"服务端错误: {resp.status_code}"}
                )
        except Exception as e:
            print(f"[ERROR] 上报服务端失败: {e}")
            return jsonify({"success": False, "message": f"保存失败: {str(e)}"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ========== 勋章API（已禁用） ==========
# @app.route("/api/badges", methods=["GET"])
# @require_local_or_password
# def api_get_badges():
#     """获取用户所有勋章"""
#     try:
#         from badge_detector import get_all_badges, get_badge_stats
#
#         badges = get_all_badges(public_client.client_id)
#         stats = get_badge_stats(public_client.client_id)
#
#         return jsonify({
#             "success": True,
#             "badges": badges,
#             "stats": stats
#         })
#     except Exception as e:
#         print(f"[ERROR] 获取勋章失败: {e}")
#         return jsonify({"success": False, "message": str(e)})


# @app.route("/api/badges/stats", methods=["GET"])
# @require_local_or_password
# def api_get_badge_stats():
#     """获取勋章统计"""
#     try:
#         from badge_detector import get_badge_stats
#
#         stats = get_badge_stats(public_client.client_id)
#         return jsonify({
#             "success": True,
#             "stats": stats
#         })
#     except Exception as e:
#         print(f"[ERROR] 获取勋章统计失败: {e}")
#         return jsonify({"success": False, "message": str(e)})


# 勋章API已禁用，保留代码备用
@app.route("/api/badges", methods=["GET"])
@require_local_or_password
def api_get_badges():
    """获取用户所有勋章（已禁用）"""
    return jsonify(
        {"success": False, "message": "勋章功能已暂停", "badges": [], "stats": {}}
    )


@app.route("/api/badges/stats", methods=["GET"])
@require_local_or_password
def api_get_badge_stats():
    """获取勋章统计（已禁用）"""
    return jsonify({"success": False, "message": "勋章功能已暂停", "stats": {}})


@app.route("/api/photo/tag", methods=["POST", "DELETE"])
@require_local_or_password
def api_photo_tag():
    """照片标签管理，上报服务端存储"""
    try:
        if request.method == "POST":
            data = request.get_json()
            filename = data.get("filename")
            tag = data.get("tag", "").strip()
            note = data.get("note", "").strip()

            if not filename:
                return jsonify({"success": False, "message": "文件名不能为空"})

            if not tag:
                return jsonify({"success": False, "message": "标签不能为空"})

            resp = public_client.signed_request(
                "POST",
                f"{public_client.server_url}/czrz/client/photo/tag",
                json={
                    "client_id": public_client.client_id,
                    "filename": filename,
                    "tag": tag,
                    "note": note,
                },
                timeout=10,
            )

            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    return jsonify(
                        {
                            "success": True,
                            "message": "标签保存成功",
                            "filename": filename,
                            "tag": tag,
                        }
                    )
                else:
                    return jsonify(
                        {"success": False, "message": result.get("message", "保存失败")}
                    )
            else:
                return jsonify({"success": False, "message": "服务端请求失败"})

        elif request.method == "DELETE":
            data = request.get_json()
            filename = data.get("filename")

            if not filename:
                return jsonify({"success": False, "message": "文件名不能为空"})

            resp = public_client.signed_request(
                "DELETE",
                f"{public_client.server_url}/czrz/client/photo/tag",
                json={
                    "client_id": public_client.client_id,
                    "filename": filename,
                },
                timeout=10,
            )

            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    return jsonify(
                        {"success": True, "message": "标签已删除", "filename": filename}
                    )
                else:
                    return jsonify(
                        {"success": False, "message": result.get("message", "删除失败")}
                    )
            else:
                return jsonify({"success": False, "message": "服务端请求失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photos/tagged")
def api_tagged_photos():
    """获取所有带标签的照片（从服务端获取）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/client/photos/tagged",
            params={"client_id": public_client.client_id},
            timeout=10,
        )

        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return jsonify(
                    {
                        "success": True,
                        "count": result.get("count", 0),
                        "photos": result.get("photos", []),
                    }
                )
            else:
                return jsonify(
                    {"success": False, "message": result.get("message", "获取失败")}
                )
        else:
            return jsonify({"success": False, "message": "服务端请求失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photo/tag/<filename>")
def api_get_photo_tag(filename):
    """获取单张照片的标签（从服务端获取）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/client/photo/tag",
            params={"client_id": public_client.client_id, "filename": filename},
            timeout=10,
        )

        if resp.status_code == 200:
            result = resp.json()
            return jsonify(result)
        else:
            return jsonify({"success": False, "message": "服务端请求失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/notifications")
def api_get_notifications():
    """获取服务端通知"""
    try:
        import requests

        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/notifications",
            params={"client_id": public_client.client_id},
            timeout=5,
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({"success": False, "message": "获取失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/feedback", methods=["POST"])
@require_local_or_password
def api_submit_feedback():
    """提交用户反馈"""
    try:
        data = request.get_json()
        name = data.get("name", "匿名用户")
        content = data.get("content", "").strip()

        if not content:
            return jsonify({"success": False, "message": "反馈内容不能为空"})

        # 保存到本地文件
        feedback_file = USER_DATA_DIR / "feedback.json"
        feedback_list = []

        if feedback_file.exists():
            try:
                feedback_list = json.loads(feedback_file.read_text(encoding="utf-8"))
            except Exception as e:
                logger.info(f"[反馈] 读取本地反馈失败: {e}")

        feedback_entry = {
            "id": len(feedback_list) + 1,
            "name": name,
            "content": content,
            "client_id": public_client.client_id,
            "baby_name": public_client.baby_name,
            "submitted_at": datetime.now().isoformat(),
        }

        feedback_list.append(feedback_entry)
        feedback_file.write_text(
            json.dumps(feedback_list, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 上报到服务端
        try:
            public_client.signed_request(
                "POST",
                f"{public_client.server_url}/czrz/client/feedback",
                json=feedback_entry,
                timeout=10,
            )
        except Exception as e:
            print(f"[WARN] 上报反馈到服务端失败: {e}")

        print(f"[INFO] 收到用户反馈: {name} - {content[:50]}...")

        return jsonify({"success": True, "message": "感谢您的反馈！"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/notifications/mark-read", methods=["POST"])
@require_local_or_password
def api_mark_notification_read():
    """标记通知为已读"""
    try:
        import requests

        data = request.get_json()
        notification_id = data.get("notification_id")

        response = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/notifications/mark-read",
            json={
                "client_id": public_client.client_id,
                "notification_id": notification_id,
            },
            timeout=5,
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({"success": False, "message": "标记失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/qrcode")
def api_get_qrcode():
    """获取二维码图片"""
    try:
        response = public_client.signed_request(
            "GET", f"{public_client.server_url}/czrz/qrcode", timeout=5
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify({"success": False, "message": "获取失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# 检测是否为 Windows EXE 打包环境
def is_windows_exe():
    """检测是否运行在 Windows EXE 打包环境"""
    return hasattr(sys, "_MEIPASS") and sys.platform == "win32"


def get_exe_path():
    """获取 Windows EXE 的实际路径（非临时目录）"""
    if is_windows_exe():
        # sys.executable 指向真实的 EXE 路径
        return Path(sys.executable).resolve()
    return None


def get_manual_update_url():
    """手动更新入口（非 Windows EXE 环境使用）"""
    base = (public_client.server_url or "").rstrip("/")
    if not base:
        return "/"
    return f"{base}/download"


@app.route("/api/version/check")
def api_check_version():
    """检查版本更新（支持多平台）"""
    try:
        # 当前版本（从配置文件中获取）
        current_version = getattr(public_client, "client_version", "2.0.0")

        # 确定当前平台
        if is_windows_exe():
            platform = "windows"
        elif sys.platform == "linux" or sys.platform.startswith("linux"):
            platform = "linux"
        elif sys.platform == "darwin":
            platform = "macos"
        else:
            platform = "unknown"

        # 请求对应平台的版本信息
        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/version/latest?platform={platform}",
            timeout=5,
        )

        if response.status_code == 200:
            data = response.json()
            latest_version = data.get("version", current_version)

            # 版本比较（支持 x.y.z 格式）
            def version_tuple(v):
                try:
                    return tuple(map(int, v.split(".")))
                except:
                    return (0, 0, 0)

            has_update = version_tuple(latest_version) > version_tuple(current_version)

            # 检测是否为 Windows EXE 环境
            windows_exe = is_windows_exe()
            exe_path = str(get_exe_path()) if windows_exe else None

            auto_update_supported = is_windows_exe()
            return jsonify(
                {
                    "success": True,
                    "has_update": has_update,
                    "current_version": current_version,
                    "latest_version": latest_version,
                    "description": data.get("description", ""),
                    "download_url": data.get("download_url", ""),
                    "file_type": data.get("file_type", "auto"),  # exe, tar.gz, zip
                    "is_windows_exe": windows_exe,
                    "exe_path": exe_path,
                    "platform": platform,
                    "auto_update_supported": auto_update_supported,
                    "manual_update_required": not auto_update_supported,
                    "manual_update_url": get_manual_update_url(),
                }
            )
        else:
            return jsonify({"success": False, "message": "检查失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/version/download", methods=["POST"])
@require_local_or_password
def api_download_update():
    """下载更新（支持多平台格式）"""
    try:
        if not is_windows_exe():
            return jsonify(
                {
                    "success": False,
                    "manual_update_required": True,
                    "manual_update_url": get_manual_update_url(),
                    "message": "当前平台请手动下载新版本覆盖安装",
                }
            )

        data = request.get_json() or {}
        download_url = data.get("download_url", "")
        expected_md5 = data.get("md5", "")
        file_type = data.get("file_type", "auto")  # exe, tar.gz, zip, binary

        if not download_url:
            return jsonify({"success": False, "message": "缺少下载地址"})

        # 完整URL
        if download_url.startswith("/"):
            download_url = f"{public_client.server_url}{download_url}"

        # 下载目录
        update_dir = Path(tempfile.gettempdir()) / "czrz_updates"
        update_dir.mkdir(exist_ok=True)

        # 下载文件
        response = public_client.signed_request(
            "GET", download_url, timeout=600, stream=True
        )  # 增加超时时间用于大文件
        response.raise_for_status()

        # 根据文件类型确定保存的文件名
        original_filename = download_url.split("/")[-1]

        if file_type == "exe" or original_filename.endswith(".exe"):
            filename = f"client_update_{file_type}.exe"
        elif (
            file_type == "tar.gz"
            or original_filename.endswith(".tar.gz")
            or original_filename.endswith(".tgz")
        ):
            filename = f"client_update_{file_type}.tar.gz"
        elif file_type == "zip" or original_filename.endswith(".zip"):
            filename = f"client_update_{file_type}.zip"
        else:
            # 保持原始文件名
            filename = original_filename if original_filename else "client_update"

        update_file = update_dir / filename

        # 下载并显示进度
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0

        with open(update_file, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    # 可以在这里添加进度回调

        # 验证MD5
        if expected_md5:
            import hashlib

            md5_hash = hashlib.md5()
            with open(update_file, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5_hash.update(chunk)

            actual_md5 = md5_hash.hexdigest()
            if actual_md5 != expected_md5:
                update_file.unlink()
                return jsonify(
                    {"success": False, "message": f"MD5校验失败，文件可能已损坏"}
                )

        return jsonify(
            {
                "success": True,
                "message": "下载完成",
                "download_path": str(update_file),
                "filename": filename,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"下载失败: {str(e)}"})


@app.route("/api/version/install", methods=["POST"])
@require_local_or_password
def api_install_update():
    """安装更新 - 静默替换当前客户端"""
    try:
        if not is_windows_exe():
            return jsonify(
                {
                    "success": False,
                    "manual_update_required": True,
                    "manual_update_url": get_manual_update_url(),
                    "message": "当前平台不支持自动安装，请手动更新",
                }
            )

        import subprocess
        import os
        import sys

        data = request.get_json() or {}
        update_file = data.get("update_file", "")

        if not update_file or not os.path.exists(update_file):
            return jsonify({"success": False, "message": "更新文件不存在"})

        update_path = Path(update_file)

        # 获取服务端版本号用于更新配置
        try:
            version_resp = public_client.signed_request(
                "GET", f"{public_client.server_url}/czrz/version/latest", timeout=5
            )
            new_version = (
                version_resp.json().get("version", "2.0.0")
                if version_resp.status_code == 200
                else "2.0.0"
            )
        except:
            new_version = "2.0.0"

        # Windows EXE 打包环境的特殊处理
        if is_windows_exe():
            exe_path = get_exe_path()
            if not exe_path:
                return jsonify({"success": False, "message": "无法获取 EXE 路径"})

            # EXE 文件路径
            exe_dir = exe_path.parent
            exe_name = exe_path.name
            backup_exe = exe_path.with_suffix(".exe.bak")
            update_exe = Path(update_file)

            # 创建 Windows 批处理更新脚本
            batch_script = exe_dir / "_update_helper.bat"
            vbs_script = exe_dir / "_update_helper.vbs"  # 用于隐藏窗口

            batch_content = f'''@echo off
chcp 65001 >nul
title CZRZ 更新助手
echo 正在更新，请稍候...
timeout /t 3 /nobreak >nul

REM 备份原文件
if exist "{exe_path}" (
    copy /Y "{exe_path}" "{backup_exe}" >nul 2>&1
)

REM 替换文件
copy /Y "{update_exe}" "{exe_path}" >nul 2>&1
if errorlevel 1 (
    echo 更新失败，恢复备份...
    if exist "{backup_exe}" (
        copy /Y "{backup_exe}" "{exe_path}" >nul 2>&1
    )
    pause
    exit /b 1
)

REM 更新版本号
echo 更新配置文件...
python -c "import json; f=open(r'{Path.home() / "Documents" / "CZRZ" / "config.json"}','r',encoding='utf-8'); d=json.load(f); f.close(); d['client_version']='{new_version}'; f=open(r'{Path.home() / "Documents" / "CZRZ" / "config.json"}','w',encoding='utf-8'); json.dump(d,f,indent=2,ensure_ascii=False); f.close()" 2>nul

REM 清理临时文件
del /F /Q "{update_exe}" 2>nul
del /F /Q "{backup_exe}" 2>nul
del /F /Q "{batch_script}" 2>nul
del /F /Q "{vbs_script}" 2>nul

REM 启动新版本
echo 启动新版本...
start "" "{exe_path}"

exit
'''

            # VBS 脚本用于隐藏窗口运行
            vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "{batch_script}" & Chr(34), 0
Set WshShell = Nothing
'''

            with open(batch_script, "w", encoding="utf-8") as f:
                f.write(batch_content)

            with open(vbs_script, "w", encoding="utf-8") as f:
                f.write(vbs_content)

            # 使用 WScript 启动隐藏的更新进程
            subprocess.Popen(
                ["wscript.exe", str(vbs_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )

            return jsonify({"success": True, "message": "更新程序已启动，即将重启..."})

        else:
            # 普通 Python 环境的更新逻辑
            current_file = Path(__file__).resolve()
            backup_file = current_file.with_suffix(".py.bak")

            # 创建更新脚本
            update_script = current_file.parent / "_update_helper.py"
            script_content = f'''#!/usr/bin/env python3
import os
import sys
import time
import shutil
import json
from pathlib import Path

# 等待原进程退出
time.sleep(3)

try:
    # 备份原文件
    if os.path.exists("{current_file}"):
        shutil.copy2("{current_file}", "{backup_file}")
    
    # 替换文件
    shutil.copy2("{update_path}", "{current_file}")
    os.chmod("{current_file}", 0o755)
    
    # 更新配置文件中的版本号
    try:
        config_file = Path.home() / "Documents" / "CZRZ" / "config.json"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            config['client_version'] = "{new_version}"
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"[✓] 版本号已更新为: {new_version}")
    except Exception as e:
        print(f"[WARN] 更新版本号失败: {{e}}")
    
    # 启动新版本
    if sys.platform == 'win32':
        os.startfile("{current_file}")
    else:
        os.system("cd {current_file.parent} && ./start_client.sh")
    
    # 清理
    os.remove("{update_path}")
    os.remove("{update_script}")
    
except Exception as e:
    print(f"更新失败: {{e}}")
    # 恢复备份
    if os.path.exists("{backup_file}"):
        shutil.copy2("{backup_file}", "{current_file}")
'''

            with open(update_script, "w") as f:
                f.write(script_content)
            os.chmod(update_script, 0o755)

            # 后台运行更新脚本
            subprocess.Popen(
                [sys.executable, str(update_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            return jsonify({"success": True, "message": "更新程序已启动，即将重启..."})

    except Exception as e:
        return jsonify({"success": False, "message": f"安装失败: {str(e)}"})


@app.route("/api/version/auto-update", methods=["POST"])
@require_local_or_password
def api_auto_update():
    """一键自动更新：下载并安装"""
    try:
        if not is_windows_exe():
            return jsonify(
                {
                    "success": False,
                    "manual_update_required": True,
                    "manual_update_url": get_manual_update_url(),
                    "message": "当前平台不支持一键自动更新，请手动下载",
                }
            )

        # 1. 获取版本信息
        response = public_client.signed_request(
            "GET", f"{public_client.server_url}/czrz/version/latest", timeout=10
        )

        if response.status_code != 200:
            return jsonify({"success": False, "message": "获取版本信息失败"})

        version_info = response.json()
        download_url = version_info.get("download_url", "")
        expected_md5 = version_info.get("md5", "")
        latest_version = version_info.get("version", "2.0.0")

        if not download_url:
            return jsonify({"success": False, "message": "没有可用的更新"})

        # 2. 下载
        update_dir = Path(tempfile.gettempdir()) / "czrz_updates"
        update_dir.mkdir(exist_ok=True)

        if download_url.startswith("/"):
            download_url = f"{public_client.server_url}{download_url}"

        dl_response = public_client.signed_request(
            "GET", download_url, timeout=300, stream=True
        )
        dl_response.raise_for_status()

        # 根据环境决定文件名
        if is_windows_exe():
            filename = download_url.split("/")[-1]
            if not filename.endswith(".exe"):
                filename = "client_update.exe"
        else:
            filename = download_url.split("/")[-1]
            if not filename.endswith(".py"):
                filename = "client_update.py"

        update_file = update_dir / filename

        with open(update_file, "wb") as f:
            for chunk in dl_response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # 3. 验证MD5
        if expected_md5:
            import hashlib

            md5_hash = hashlib.md5()
            with open(update_file, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    md5_hash.update(chunk)

            if md5_hash.hexdigest() != expected_md5:
                update_file.unlink()
                return jsonify({"success": False, "message": "MD5校验失败"})

        # 4. 安装 - Windows EXE 环境
        if is_windows_exe():
            exe_path = get_exe_path()
            if not exe_path:
                return jsonify({"success": False, "message": "无法获取 EXE 路径"})

            exe_dir = exe_path.parent
            backup_exe = exe_path.with_suffix(".exe.bak")
            update_exe = update_file

            # 创建 Windows 批处理和 VBS 脚本
            batch_script = exe_dir / "_update_helper.bat"
            vbs_script = exe_dir / "_update_helper.vbs"

            batch_content = f'''@echo off
chcp 65001 >nul
title CZRZ 更新助手
echo 正在更新，请稍候...
timeout /t 3 /nobreak >nul

REM 备份原文件
if exist "{exe_path}" (
    copy /Y "{exe_path}" "{backup_exe}" >nul 2>&1
)

REM 替换文件
copy /Y "{update_exe}" "{exe_path}" >nul 2>&1
if errorlevel 1 (
    echo 更新失败，恢复备份...
    if exist "{backup_exe}" (
        copy /Y "{backup_exe}" "{exe_path}" >nul 2>&1
    )
    pause
    exit /b 1
)

REM 更新版本号
echo 更新配置文件...
python -c "import json; f=open(r'{Path.home() / "Documents" / "CZRZ" / "config.json"}','r',encoding='utf-8'); d=json.load(f); f.close(); d['client_version']='{latest_version}'; f=open(r'{Path.home() / "Documents" / "CZRZ" / "config.json"}','w',encoding='utf-8'); json.dump(d,f,indent=2,ensure_ascii=False); f.close()" 2>nul

REM 清理临时文件
del /F /Q "{update_exe}" 2>nul
del /F /Q "{backup_exe}" 2>nul
del /F /Q "{batch_script}" 2>nul
del /F /Q "{vbs_script}" 2>nul

REM 启动新版本
echo 启动新版本...
start "" "{exe_path}"

exit
'''

            vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "{batch_script}" & Chr(34), 0
Set WshShell = Nothing
'''

            with open(batch_script, "w", encoding="utf-8") as f:
                f.write(batch_content)

            with open(vbs_script, "w", encoding="utf-8") as f:
                f.write(vbs_content)

            # 启动隐藏更新进程
            subprocess.Popen(
                ["wscript.exe", str(vbs_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )

            return jsonify({"success": True, "message": "更新已开始，即将重启..."})

        # 4. 安装 - 普通 Python 环境
        else:
            import subprocess

            current_file = Path(__file__).resolve()
            update_script = current_file.parent / "_update_helper.py"

            script_content = f'''#!/usr/bin/env python3
import os
import sys
import time
import shutil
import json
from pathlib import Path

time.sleep(3)

try:
    backup = "{current_file}.bak"
    if os.path.exists("{current_file}"):
        shutil.copy2("{current_file}", backup)
    
    shutil.copy2("{update_file}", "{current_file}")
    os.chmod("{current_file}", 0o755)
    
    # 更新配置文件中的版本号
    try:
        config_file = Path.home() / "Documents" / "CZRZ" / "config.json"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            config['client_version'] = "{latest_version}"
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            print(f"[✓] 版本号已更新为: {latest_version}")
    except Exception as e:
        print(f"[WARN] 更新版本号失败: {{e}}")
    
    if sys.platform == 'win32':
        os.startfile("{current_file}")
    else:
        os.system("cd {current_file.parent} && ./start_client.sh")
    
    os.remove("{update_file}")
    os.remove("{update_script}")
except Exception as e:
    print(f"更新失败: {{e}}")
    if os.path.exists(backup):
        shutil.copy2(backup, "{current_file}")
'''

            with open(update_script, "w") as f:
                f.write(script_content)
            os.chmod(update_script, 0o755)

            subprocess.Popen(
                [sys.executable, str(update_script)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            return jsonify({"success": True, "message": "更新已开始，即将重启..."})

    except Exception as e:
        return jsonify({"success": False, "message": f"自动更新失败: {str(e)}"})


@app.route("/api/theme")
def get_theme():
    """获取当前主题CSS"""
    try:
        from theme_generator import get_or_create_theme, generate_css

        theme = get_or_create_theme(public_client.client_id, public_client.baby_name)

        if theme:
            css = generate_css(theme)
            return jsonify({"success": True, "theme": theme, "css": css})
        else:
            return jsonify({"success": False, "message": "主题不存在"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/theme/update", methods=["POST"])
@require_local_or_password
def update_theme():
    """根据用户描述更新主题"""
    try:
        from theme_generator import update_theme, generate_css

        data = request.get_json()
        user_prompt = data.get("prompt", "")

        if not user_prompt:
            return jsonify({"success": False, "message": "请描述你想要的风格"})

        theme = update_theme(public_client.client_id, user_prompt)

        if theme:
            css = generate_css(theme)
            response = {
                "success": True,
                "theme": theme,
                "css": css,
                "message": f"已切换到「{theme['name']}」主题",
            }

            if theme.get("limitations"):
                response["limitations"] = theme["limitations"]
            if theme.get("fallback"):
                response["fallback"] = theme["fallback"]
            if theme.get("ai_error"):
                response["ai_error"] = theme["ai_error"]

            return jsonify(response)
        else:
            return jsonify({"success": False, "message": "更新主题失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/theme/presets")
def get_preset_themes():
    """获取预设主题列表 - 新版本使用AI生成主题，预设已废弃"""
    try:
        # 新版本不再使用预设主题，返回空列表
        # AI主题生成通过 /api/theme/update 端点处理
        return jsonify(
            {
                "success": True,
                "presets": [],
                "message": "请使用AI主题生成功能创建自定义主题",
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/theme/apply", methods=["POST"])
@require_local_or_password
def apply_preset_theme():
    """应用预设主题 - 新版本已废弃，请使用AI主题生成"""
    try:
        return jsonify(
            {
                "success": False,
                "message": "预设主题功能已废弃，请使用AI主题生成器创建自定义主题",
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/theme/reset", methods=["POST"])
@require_local_or_password
def reset_theme():
    """重置为默认主题"""
    try:
        from theme_generator import get_or_create_theme

        # 删除自定义主题，重新生成默认主题
        theme = get_or_create_theme(
            public_client.client_id, public_client.baby_name, force_new=True
        )

        if theme:
            # 清除保存的自定义CSS
            theme_file = (
                USER_DATA_DIR / "themes" / f"{public_client.client_id}_custom.css"
            )
            if theme_file.exists():
                theme_file.unlink()

            return jsonify({"success": True, "message": "已恢复默认主题"})
        else:
            return jsonify({"success": False, "message": "重置主题失败"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photos/dates")
def get_photo_dates():
    """获取所有有照片的日期"""
    try:
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": True, "dates": []})

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)

        dates = pm.get_all_dates()

        return jsonify({"success": True, "dates": dates})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# 创建客户端实例（不自动连接，等端口确定后再连接）
public_client = PublicClient(auto_connect=False)

# ==================== 留言功能（转发到服务端） ====================


@app.route("/message", methods=["POST"])
@require_local_or_password
def post_message():
    """提交留言（文字或语音）- 转发到服务端，支持角色选择"""
    try:
        message_type = request.form.get("type", "text")
        content = request.form.get("message", "").strip()
        audio_data = request.form.get("audio", "")
        author = request.form.get("author", "家人").strip()

        # 如果作者为空，默认为"家人"
        if not author:
            author = "家人"

        # 构建 JSON 数据转发到服务端
        json_data = {
            "client_id": public_client.client_id,
            "type": message_type,
            "message": content,
            "author": author,
        }

        if message_type == "voice":
            json_data["audio"] = audio_data

        # 发送到服务端
        response = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/messages",
            json=json_data,
            timeout=1200,  # 语音上传可能需要更长时间
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(
                {"success": False, "message": f"服务端错误: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"发送失败: {str(e)}"})


@app.route("/api/messages/<date>")
def get_messages(date):
    """获取指定日期的留言 - 从服务端获取"""
    try:
        response = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/messages/{public_client.client_id}/{date}",
            timeout=10,
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(
                {"success": False, "message": f"服务端错误: {response.status_code}"}
            )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/messages/<message_id>", methods=["DELETE"])
@require_local_or_password
def delete_message(message_id):
    """删除留言 - 发送到服务端"""
    try:
        response = public_client.signed_request(
            "DELETE",
            f"{public_client.server_url}/czrz/messages/{public_client.client_id}/{message_id}",
            timeout=10,
        )

        if response.status_code == 200:
            return jsonify(response.json())
        else:
            return jsonify(
                {"success": False, "message": f"服务端错误: {response.status_code}"}
            )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/verify-password", methods=["POST"])
def verify_password():
    """通用的密码验证API - 验证成功后设置 session"""
    try:
        data = request.get_json()
        password = data.get("password", "")
        action = data.get("action", "unknown")

        # localhost 免密码
        remote_addr = request.remote_addr
        if remote_addr in ["127.0.0.1", "localhost"]:
            return jsonify({"success": True, "message": "localhost 免密码"})

        stored_password = getattr(public_client, "admin_password", None)

        if not stored_password:
            # 没有设置密码，允许操作
            return jsonify({"success": True, "message": "未设置密码，允许操作"})

        if password == stored_password:
            # 验证成功，设置 session（永久有效）
            from flask import session
            session['password_verified'] = True
            session.permanent = True  # 使用配置的10年有效期（相当于永久）
            return jsonify({"success": True, "message": "验证通过"})
        else:
            return jsonify({"success": False, "message": "密码错误，请询问家人或朋友"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


# ==================== 照片工具API ====================


@app.route("/api/photo/featured", methods=["GET", "POST"])
@require_local_or_password
def featured_photo_api():
    """获取/保存精选照片"""
    try:
        if request.method == "GET":
            client_id = request.args.get("client_id", public_client.client_id)
            date = request.args.get("date")
            response = public_client.signed_request(
                "GET",
                f"{public_client.server_url}/czrz/photo/featured",
                params={"client_id": client_id, "date": date},
                timeout=10,
            )
        else:
            # POST操作需要权限验证
            check_result = check_local_or_password()
            if check_result:
                return check_result
            data = request.get_json()
            response = public_client.signed_request(
                "POST",
                f"{public_client.server_url}/czrz/photo/featured/update",
                json=data,
                timeout=10,
            )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photos/best", methods=["POST"])
@require_local_or_password
def select_best_photo_api():
    """选择最佳照片"""
    try:
        from photo_tools import select_best_photo_from_list

        data = request.get_json()
        date = data.get("date")

        # 获取指定日期的照片
        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"})

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)
        photos = pm.get_photos_by_date(date)

        if not photos:
            return jsonify({"success": False, "message": "该日期没有照片"})

        # 获取照片路径
        photo_paths = [p["path"] for p in photos if os.path.exists(p["path"])]

        if not photo_paths:
            return jsonify({"success": False, "message": "照片文件不存在"})

        # 选择最佳照片
        best_path = select_best_photo_from_list(
            photo_paths, api_key=os.environ.get("DASHSCOPE_API_KEY")
        )

        if best_path:
            best_filename = os.path.basename(best_path)
            return jsonify(
                {
                    "success": True,
                    "message": "已选择最佳照片",
                    "filename": best_filename,
                    "path": best_path,
                }
            )
        else:
            return jsonify({"success": False, "message": "无法选择最佳照片"})

    except Exception as e:
        return jsonify({"success": False, "message": f"选择失败: {str(e)}"})


@app.route("/api/photos/cleanup", methods=["POST"])
@require_local_or_password
def cleanup_photos_api():
    """清理功能已合并到AI精选，提示用户使用精选功能"""
    return jsonify(
        {
            "success": False,
            "message": "清理功能已合并到「挑选精选照片」，点击「精选」按钮可同时完成：\n1. 选择最佳照片\n2. 识别模糊照片\n3. 识别重复照片\n\nAI识别更精准，误杀率更低。",
            "suggest_select_best": True,
        }
    )


@app.route("/api/photos/remove-from-index", methods=["POST"])
@require_local_or_password
def remove_photos_from_index_api():
    """从索引中移除指定照片（不删除原文件）"""
    try:
        data = request.get_json()
        filenames = data.get("filenames", [])
        date = data.get("date")

        if not filenames:
            return jsonify({"success": False, "message": "未指定要移除的照片"})

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"})

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)

        removed = []
        for filename in filenames:
            try:
                pm.remove_photo(filename)
                removed.append(filename)
            except Exception as e:
                print(f"移除失败 {filename}: {e}")

        return jsonify(
            {
                "success": True,
                "message": f"已从索引移除 {len(removed)} 张照片",
                "removed": removed,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"移除失败: {str(e)}"})


@app.route("/api/ai/generate-log", methods=["POST"])
@require_local_or_password
def proxy_ai_generate_log():
    """
    AI生成日志 - 统一流程

    流程：
    1. ai_select 分析照片（一次AI调用）
    2. 过滤有效照片
    3. 处理视频语音
    4. 生成日志
    """
    import time

    start_time = time.time()

    try:
        data = request.get_json() or {}
        date = data.get("date")
        client_id = data.get("client_id") or getattr(public_client, "client_id", "")
        log_style = data.get("log_style", "简练")
        custom_style = data.get("custom_style", "")
        user_note = data.get("user_note", "")
        include_news = data.get("include_news", False)

        print(
            f"[INFO] 开始生成日志: date={date}, style={log_style}, include_news={include_news}"
        )

        media_folders = getattr(public_client, "media_folders", [])
        child_id = CLIENT_CONFIG.get("ai_service", {}).get("child_id", "") or client_id

        photo_analysis = None
        valid_photo_descs = []

        # 步骤1：AI分析照片
        if media_folders and date:
            try:
                from photo_manager import PhotoManager
                from select_best_photo import ai_select, analyze_all_photos

                pm = PhotoManager(media_folders, public_client.data_dir)
                all_media = pm.get_photos_by_date(date)
                photos = [m for m in all_media if not m.get("is_video", False)]

                if photos:
                    photo_paths = [
                        p["path"] for p in photos if Path(p.get("path", "")).exists()
                    ]
                    print(f"[INFO] 找到 {len(photo_paths)} 张照片，开始AI分析...")

                    step_start = time.time()
                    ai_result = ai_select(
                        photo_paths[:20], select_n=1, child_id=child_id
                    )
                    logger.info(f"[TIME] AI分析耗时: {time.time() - step_start:.2f}s")

                    # 【调试】输出 AI 返回结果
                    logger.info(f"[DEBUG] AI 返回结果:")
                    logger.info(f"  - selected: {len(ai_result.get('selected', []))}张")
                    logger.info(
                        f"  - photos 字段：{len(ai_result.get('photos', {}))}条"
                    )
                    logger.info(f"  - blurry: {len(ai_result.get('blurry', []))}张")
                    logger.info(f"  - no_baby: {len(ai_result.get('no_baby', []))}张")
                    logger.info(
                        f"  - duplicates: {len(ai_result.get('duplicates', []))}组"
                    )
                    if ai_result.get("photos"):
                        logger.info(
                            f"  - photos 样例：{list(ai_result['photos'].items())[:2]}"
                        )

                    blurry_count = len(ai_result.get("blurry", []))
                    no_baby_count = len(ai_result.get("no_baby", []))
                    dup_groups = ai_result.get("duplicates", [])
                    dup_count = sum(len(g) - 1 for g in dup_groups if len(g) > 1)
                    print(
                        f"[INFO] 模糊{blurry_count}张, 无宝宝{no_baby_count}张, 重复{dup_count}张"
                    )

                    photo_analysis = analyze_all_photos(
                        photo_paths,
                        max_photos=20,
                        client_id=client_id,
                        date=date,
                        ai_result=ai_result,
                    )
                    valid_photos = photo_analysis.get("photos", [])
                    valid_photo_descs = [
                        p.get("description", "")
                        for p in valid_photos
                        if p.get("description")
                    ]
                    print(f"[INFO] 有效照片: {len(valid_photos)}张")

            except Exception as e:
                print(f"[WARN] 照片分析失败: {e}")
                import traceback

                traceback.print_exc()

        # 步骤2：处理视频语音
        video_results = []
        if media_folders and date:
            try:
                from photo_manager import PhotoManager

                pm = PhotoManager(media_folders, public_client.data_dir)
                all_media = pm.get_photos_by_date(date)
                videos = [m for m in all_media if m.get("is_video", False)]

                if videos:
                    print(f"[INFO] 找到 {len(videos)} 个视频，开始语音识别...")

                    from video_audio_processor import (
                        process_video_speech,
                        analyze_language_ability,
                    )

                    for video in videos[:5]:
                        video_path = Path(video.get("path", ""))
                        if not video_path.exists():
                            continue

                        try:
                            step_start = time.time()
                            speech_result = process_video_speech(
                                video_path, max_duration=60
                            )
                            print(
                                f"[TIME] 视频语音处理耗时: {time.time() - step_start:.2f}s"
                            )

                            if speech_result.get("success") and speech_result.get(
                                "transcript"
                            ):
                                video_results.append(
                                    {
                                        "path": str(video_path),
                                        "transcript": speech_result["transcript"],
                                        "duration": speech_result.get("duration", 0),
                                    }
                                )
                        except Exception as e:
                            print(f"[WARN] 视频语音处理失败 {video_path}: {e}")

                    if video_results:
                        video_results.sort(
                            key=lambda x: x.get("duration", 0), reverse=True
                        )

                        baby_info = getattr(public_client, "baby_info", {}) or {}
                        baby_name = baby_info.get("baby_name", "宝宝")
                        age_months = baby_info.get("age_months", 0)

                        for vr in video_results:
                            analysis = analyze_language_ability(
                                vr["transcript"],
                                baby_name=baby_name,
                                age_months=age_months,
                            )

                            upload_resp = public_client.signed_request(
                                "POST",
                                f"{public_client.server_url}/czrz/speech/record",
                                json={
                                    "client_id": client_id,
                                    "date": date,
                                    "video_path": vr["path"],
                                    "transcript": vr["transcript"],
                                    "duration": vr["duration"],
                                    "analysis": analysis,
                                },
                                timeout=30,
                            )
                            if upload_resp.status_code == 200:
                                print(
                                    f"[INFO] 语音记录已上传: {vr['transcript'][:50]}..."
                                )

            except Exception as e:
                print(f"[WARN] 视频语音处理失败: {e}")

        # 步骤3：生成日志
        combined_desc = None
        if valid_photo_descs:
            combined_desc = "、".join(valid_photo_descs[:5])

        request_data = {
            "client_id": client_id,
            "date": date,
            "user_note": user_note,
            "log_style": log_style,
            "custom_style": custom_style,
            "auto_save": True,
            "photo_analysis": photo_analysis,
            "include_news": include_news,
        }

        step_start = time.time()
        response = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/ai/generate-log",
            json=request_data,
            timeout=1200,
        )
        logger.info(f"[TIME] 服务端生成日志耗时: {time.time() - step_start:.2f}s")

        result = response.json()
        logger.info(f"[TIME] 总耗时: {time.time() - start_time:.2f}s")

        return jsonify(result), response.status_code
    except Exception as e:
        print(f"[ERROR] 生成日志失败: {e}")
        import traceback

        traceback.print_exc()
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/photo/select-best", methods=["POST"])
@require_local_or_password
def select_best_photo_local():
    """本地选择精选照片（同时返回模糊和重复照片列表）"""
    try:
        from select_best_photo import (
            select_best_photo_for_client,
            analyze_photo_content,
        )

        data = request.get_json() or {}
        date = data.get("date")

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify(
                {
                    "success": False,
                    "message": "未配置媒体文件夹，请在设置中添加媒体文件夹",
                }
            )

        child_id = CLIENT_CONFIG.get("ai_service", {}).get("child_id", "")
        data_dir = getattr(public_client, "data_dir", None)

        result = select_best_photo_for_client(media_folders, child_id, date, data_dir)

        if not result or not result.get("best_photo"):
            return jsonify({"success": False, "message": "未能选出精选照片"})

        photo_folder = result.get("photo_folder", "")
        best_photo = result.get("best_photo", "")
        photo_path = os.path.join(photo_folder, best_photo) if photo_folder else ""

        ai_description = None
        if photo_path and os.path.exists(photo_path):
            try:
                ai_description = analyze_photo_content(photo_path)
            except Exception as e:
                print(f"[WARN] 分析照片内容失败: {e}")

        # 保存精选照片
        if best_photo:
            save_featured_photo_server(
                date or datetime.now().strftime("%Y-%m-%d"),
                public_client.client_id,
                photo_path,
                ai_description or "",
            )

        return jsonify(
            {
                "success": True,
                "photo": best_photo,
                "ai_description": ai_description,
                "message": "已选择精选照片",
                "blurry": result.get("blurry", []),
                "duplicates": result.get("duplicates", []),
                "method": result.get("method", "ai"),
            }
        )

    except Exception as e:
        import traceback

        print(f"[ERROR] 选择精选照片失败: {e}")
        print(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/ai/ask", methods=["POST"])
@require_local_or_password
def ai_ask():
    """向本地健康AI服务提问"""
    try:
        # 检查AI服务是否启用
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = ai_config.get("child_id", "") or public_client.client_id
        if not child_id:
            return jsonify(
                {
                    "success": False,
                    "message": "未配置宝宝ID，请先在配置文件中设置AI_CHILD_ID",
                }
            )

        # 通过服务端代理访问健康AI
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        data = request.get_json() or {}
        question = data.get("question", "")
        photo_filename = data.get("photo_filename", "")
        date = data.get("date", "")

        if not question:
            return jsonify({"success": False, "message": "请输入问题"})

        # 获取照片完整路径
        photo_path = None
        if photo_filename:
            from photo_manager import PhotoManager

            media_folders = getattr(public_client, "media_folders", [])
            pm = PhotoManager(media_folders, public_client.data_dir)
            photo_info = pm.get_photo_by_filename(photo_filename)
            if photo_info and os.path.exists(photo_info.get("path", "")):
                photo_path = photo_info["path"]

        # 调用AI服务
        payload = {
            "child_id": child_id,
            "question": question,
            "file_path": photo_path,
            "context": {
                "date": date,
                "city": getattr(public_client, "user_city", None),
                "baby_name": public_client.baby_name,
            },
        }

        response = public_client.signed_request(
            "POST",
            f"{server_url}/api/ai/ask",
            json=payload,
            timeout=1200,
        )

        if response.status_code == 200:
            result = response.json()
            return jsonify(
                {
                    "success": True,
                    "answer": result.get("answer", "抱歉，我暂时无法回答这个问题"),
                    "evidence_size": result.get("evidence_size", 0),
                }
            )
        else:
            return jsonify(
                {"success": False, "message": f"AI服务响应错误: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"请求失败: {str(e)}"})


@app.route("/api/ai/feedback", methods=["POST"])
@require_local_or_password
def ai_feedback():
    """提交画像反馈到服务端"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = ai_config.get("child_id", "") or public_client.client_id
        if not child_id:
            return jsonify({"success": False, "message": "未配置宝宝ID"})

        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        data = request.get_json() or {}

        # 提取反馈文本
        feedback_text = (
            data.get("answer", "")
            or data.get("feedback_text", "")
            or data.get("user_correction", "")
        )
        if not feedback_text:
            return jsonify({"success": False, "message": "反馈内容不能为空"})

        payload = {
            "client_id": public_client.client_id,
            "ai_child_id": child_id,
            "feedback_text": feedback_text,
            "feedback_type": data.get("feedback_type", "user_input"),
        }

        response = public_client.signed_request(
            "POST",
            f"{server_url}/czrz/client/profile-feedback",
            json=payload,
            timeout=10,
        )

        if response.status_code == 200:
            return jsonify({"success": True, "message": "反馈已提交"})
        else:
            return jsonify(
                {"success": False, "message": f"提交失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"提交失败: {str(e)}"})


@app.route("/api/ai/profile", methods=["GET"])
def ai_get_profile():
    """获取宝宝成长画像数据"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = ai_config.get("child_id", "") or public_client.client_id
        if not child_id:
            return jsonify({"success": False, "message": "未配置宝宝ID"})

        # 通过服务端代理访问健康AI
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai/children/{child_id}/profile",
            timeout=1200,
        )

        if response.status_code == 200:
            ai_data = response.json()
            profile_data = ai_data.get("profile", {})

            dimensions = profile_data.get("dimensions", {})
            guidance = profile_data.get("guidance", {})
            evolution = profile_data.get("evolution", {})
            growth_curve = profile_data.get("growth_curve", {})

            personality = dimensions.get("personality", {})
            interests = dimensions.get("interests", {})
            development = dimensions.get("development", {})

            baby_birthday = getattr(public_client, "baby_birthday", None)
            birth_date_str = baby_birthday or ""
            age_text = ""
            age_months = profile_data.get("age_months", 0)
            if age_months:
                if age_months >= 12:
                    years = age_months // 12
                    months = age_months % 12
                    age_text = f"{years}岁{months}个月" if months > 0 else f"{years}岁"
                else:
                    age_text = f"{age_months}个月"

            personality_scores = personality.get("scores", {})
            tags = profile_data.get("tags", [])
            if not tags:
                tags = [
                    {"tag": t, "category": "other", "confidence": 0.5}
                    for t in personality.get("traits", [])
                ]

            total_events = profile_data.get("total_events", 0)
            total_tags = profile_data.get("total_tags", len(tags))
            knowledge_growth = profile_data.get("knowledge_growth", {})
            familiarity = min(100, total_events * 2 + total_tags * 5)

            profile = {
                "age": age_text,
                "age_months": age_months,
                "knowledge_level": int(familiarity),
                "completeness": min(100, int(familiarity)),
                "total_events": total_events,
                "total_tags": total_tags,
                "tags": tags,
                "milestones": profile_data.get("milestones", []),
                "knowledge_growth": knowledge_growth,
                "dimensions": {
                    "personality": {
                        "traits": personality.get("traits", []),
                        "scores": personality_scores,
                        "evidence": personality.get("evidence", []),
                    },
                    "interests": {
                        "current": interests.get("current", []),
                        "emerging": interests.get("emerging", []),
                        "faded": interests.get("faded", []),
                    },
                    "development": development,
                },
                "guidance": {
                    "summary": guidance.get("summary", ""),
                    "strengths": guidance.get("strengths", []),
                    "areas_to_nurture": guidance.get("areas_to_nurture", []),
                    "areas_to_watch": guidance.get("areas_to_watch", []),
                    "suggestions": guidance.get("suggestions", []),
                    "activities": guidance.get("activities", []),
                    "development_notes": guidance.get("development_notes", ""),
                },
                "evolution": {
                    "recent_changes": evolution.get("recent_changes", []),
                    "trends": evolution.get("trends", {}),
                },
            }

            return jsonify({"success": True, "profile": profile})
        else:
            return jsonify(
                {"success": False, "message": f"获取失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"})


@app.route("/api/ai/features", methods=["GET"])
def ai_get_features():
    """获取宝宝特征数据（用于雷达图）"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = ai_config.get("child_id", "") or public_client.client_id
        if not child_id:
            return jsonify({"success": False, "message": "未配置宝宝ID"})

        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai/features/{child_id}",
            timeout=1200,
        )

        if response.status_code == 200:
            return response.json()
        else:
            return jsonify(
                {"success": False, "message": f"获取失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"获取失败: {str(e)}"})


# ========== 成长卡片生成API ==========


@app.route("/api/card/types", methods=["GET"])
def api_card_types():
    """获取可用的卡片类型"""
    try:
        from card_generator import get_available_card_types

        # 获取画像数据
        profile = get_profile_data()
        if profile is None:
            return jsonify({"success": False, "message": "无法获取画像数据"})

        types = get_available_card_types(profile)
        return jsonify({"success": True, "types": types})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/card/styles", methods=["GET"])
def api_card_styles():
    """获取可用的卡片风格"""
    try:
        from card_generator import get_card_styles

        styles = get_card_styles()
        return jsonify({"success": True, "styles": styles})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/card/generate", methods=["POST"])
@require_local_or_password
def api_card_generate():
    """生成成长卡片"""
    try:
        from card_generator import generate_card_content

        data = request.get_json() or {}
        card_type = data.get("card_type", "daily")
        style = data.get("style", "warm")
        template_id = data.get("template")  # 可选：指定模板

        # 直接调用本地画像API获取数据
        profile = get_local_profile_data()
        if profile is None:
            profile = {}

        # 获取宝宝信息
        baby_name = USER_CONFIG.get("baby_name", "宝宝")
        age_text = calculate_age_text()

        # 获取本地端口
        local_port = public_client.client_port if public_client else 3000

        # 生成卡片数据
        card_data = generate_card_content(
            profile=profile,
            baby_name=baby_name,
            age_text=age_text,
            style=style,
            template_id=template_id,
            base_url=f"http://localhost:{local_port}",
        )

        return jsonify({"success": True, "card": card_data})
    except Exception as e:
        print(f"[Card] 生成卡片失败: {e}")
        return jsonify({"success": False, "message": str(e)})


# ========== 卡片缓存API ==========


@app.route("/api/card/cache/status")
def api_card_cache_status():
    """获取卡片缓存状态"""
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()
        status = cache.get_cache_status()
        return jsonify({"success": True, "status": status})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/card/cache/list")
def api_card_cache_list():
    """获取所有缓存的卡片（不含里程碑）"""
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()
        sync_card_cache_silently()
        cards = [convert_photo_path_for_client(card) for card in cache.get_all_cards()]
        cards = select_rotating_cards(cards, per_day=50)

        # 添加分享状态
        for card in cards:
            card_id = card.get("id", "")
            card["shared"] = cache.is_shared(card_id)

        return jsonify({"success": True, "cards": cards, "total": len(cards)})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/card/milestone/today")
def api_card_milestone_today():
    """获取今日里程碑卡片"""
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()
        cards = [
            convert_photo_path_for_client(card)
            for card in cache.get_today_milestone_cards()
        ]

        # 添加分享状态
        for card in cards:
            card_id = card.get("id", "")
            card["shared"] = cache.is_shared(card_id)

        return jsonify(
            {
                "success": True,
                "cards": cards,
                "total": len(cards),
                "has_milestone": len(cards) > 0,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def convert_photo_path_for_client(obj):
    """将照片路径转换为客户端可访问的URL"""
    if not obj:
        return obj

    path_keys = {
        "path",
        "photo",
        "before_photo",
        "after_photo",
        "cover_path",
        "thumbnail",
        "thumb",
        "src",
    }

    if isinstance(obj, str):
        if obj.startswith("/photo/"):
            return obj
        import urllib.parse

        if obj.startswith("/"):
            filename = obj.split("/")[-1]
            return f"/photo/{urllib.parse.quote(filename)}"
        return f"/photo/{urllib.parse.quote(obj)}"

    if isinstance(obj, list):
        return [convert_photo_path_for_client(item) for item in obj]

    if isinstance(obj, dict):
        converted = {}
        for key, value in obj.items():
            if key in path_keys:
                converted[key] = convert_photo_path_for_client(value)
            elif key in {
                "photo_paths",
                "photos",
                "year_samples",
                "seasons",
                "evolution_photos",
                "assets",
                "cards",
            }:
                converted[key] = convert_photo_path_for_client(value)
            else:
                converted[key] = value
        return converted

    return obj


def select_rotating_cards(cards, per_day: int = 50, date_str: str = None):
    """
    按天轮换展示卡片：
    - 不删除、不过滤总库，仅决定“今天展示哪一页”
    - 稳定排序 + 按日偏移，避免每天都看同一批
    """
    if not cards:
        return []

    if per_day <= 0:
        return cards

    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    try:
        day_seed = datetime.strptime(date_str, "%Y-%m-%d").toordinal()
    except Exception:
        day_seed = datetime.now().toordinal()

    def stable_key(card):
        card_id = str(card.get("id") or card.get("card_id") or "")
        # 稳定哈希，保证每天分页可复现
        return hashlib.md5(card_id.encode("utf-8")).hexdigest()

    ordered_cards = sorted(cards, key=stable_key)
    total = len(ordered_cards)
    if total <= per_day:
        return ordered_cards

    # 先保证每个类别至少展示 1 张（如果有）
    by_type = {}
    for card in ordered_cards:
        card_type = card.get("type") or card.get("card_type") or "unknown"
        by_type.setdefault(card_type, []).append(card)

    selected = []
    selected_ids = set()
    for card_type, type_cards in sorted(by_type.items()):
        if len(selected) >= per_day:
            break
        if not type_cards:
            continue
        idx = day_seed % len(type_cards)
        chosen = type_cards[idx]
        card_id = chosen.get("id") or chosen.get("card_id")
        if card_id not in selected_ids:
            selected.append(chosen)
            selected_ids.add(card_id)

    # 再按日轮换补齐剩余名额
    remaining = per_day - len(selected)
    if remaining <= 0:
        return selected

    total_pages = (total + remaining - 1) // remaining
    page_idx = day_seed % total_pages
    start = page_idx * remaining
    end = start + remaining
    for card in ordered_cards[start:end]:
        card_id = card.get("id") or card.get("card_id")
        if card_id in selected_ids:
            continue
        selected.append(card)
        selected_ids.add(card_id)
        if len(selected) >= per_day:
            break

    # 如果还有空位，从头补齐
    if len(selected) < per_day:
        for card in ordered_cards:
            card_id = card.get("id") or card.get("card_id")
            if card_id in selected_ids:
                continue
            selected.append(card)
            selected_ids.add(card_id)
            if len(selected) >= per_day:
                break

    return selected


def sync_card_cache_silently():
    """静默从服务端同步一次卡片缓存"""
    try:
        if not public_client or not getattr(public_client, "client_id", None):
            return False
        return public_client.send_heartbeat()
    except Exception as e:
        print(f"[Card] 静默同步失败: {e}")
        return False


@app.route("/api/card/cache/random")
def api_card_cache_random():
    """获取随机卡片"""
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()

        count = request.args.get("count", 5, type=int)
        exclude_ids = (
            request.args.get("exclude", "").split(",")
            if request.args.get("exclude")
            else []
        )

        cards = [
            convert_photo_path_for_client(card)
            for card in cache.get_random_cards(count, exclude_ids)
        ]

        if not cards:
            sync_card_cache_silently()
            cards = [
                convert_photo_path_for_client(card)
                for card in cache.get_random_cards(count, exclude_ids)
            ]

        # 添加分享状态
        for card in cards:
            card_id = card.get("id", "")
            card["shared"] = cache.is_shared(card_id)

        return jsonify({"success": True, "cards": cards})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/card/share", methods=["POST"])
@require_local_or_password
def api_card_share():
    """标记卡片已分享"""
    try:
        from card_cache import get_card_cache

        cache = get_card_cache()

        data = request.get_json() or {}
        card_id = data.get("card_id", "")

        if not card_id:
            return jsonify({"success": False, "message": "缺少card_id"})

        cache.mark_shared(card_id)

        return jsonify({"success": True, "message": "已标记为已分享"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def get_local_profile_data():
    """获取本地画像数据（直接从服务端获取，不走API）"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return None

        child_id = ai_config.get("child_id", "") or public_client.client_id
        if not child_id:
            return None

        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return None

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai/children/{child_id}/profile",
            timeout=1200,
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("profile", {})
        return None
    except Exception as e:
        print(f"[Card] 获取画像数据失败: {e}")
        return None


@app.route("/api/card/share-log", methods=["POST"])
@require_local_or_password
def api_card_share_log():
    """记录分享行为"""
    try:
        from card_generator import log_share_event

        data = request.get_json() or {}
        template_id = data.get("template_id", "unknown")
        card_style = data.get("card_style", "warm")
        shared = data.get("shared", True)

        log_share_event(
            card_style=card_style,
            template_id=template_id,
            shared=shared,
        )

        return jsonify({"success": True})
    except Exception as e:
        print(f"[Card] 记录分享行为失败: {e}")
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/card/share-stats", methods=["GET"])
def api_card_share_stats():
    """获取分享统计数据"""
    try:
        from card_generator import get_share_stats

        stats = get_share_stats()
        return jsonify({"success": True, "stats": stats})
    except Exception as e:
        print(f"[Card] 获取分享统计失败: {e}")
        return jsonify({"success": False, "message": str(e)})


def get_profile_data():
    """获取画像数据（内部函数）"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return None

        child_id = ai_config.get("child_id", "") or public_client.client_id
        if not child_id:
            return None

        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return None

        response = public_client.signed_request(
            "GET",
            f"{server_url}/api/ai/children/{child_id}/profile",
            timeout=1200,
        )

        if response.status_code == 200:
            data = response.json()
            return data.get("profile", {})
        return None
    except Exception as e:
        print(f"[Card] 获取画像数据失败: {e}")
        return None


def calculate_age_text():
    """计算年龄文本"""
    try:
        birthday_str = USER_CONFIG.get("birthday", "")
        if not birthday_str:
            return ""

        birthday = datetime.strptime(birthday_str, "%Y-%m-%d")
        today = datetime.now()

        delta = today - birthday
        days = delta.days

        if days < 0:
            return ""

        if days < 30:
            return f"{days}天"
        elif days < 365:
            months = days // 30
            remaining_days = days % 30
            if remaining_days > 15:
                months += 1
            return f"{months}个月"
        else:
            years = days // 365
            remaining_months = (days % 365) // 30
            if remaining_months > 0:
                return f"{years}岁{remaining_months}个月"
            return f"{years}岁"
    except:
        return ""


@app.route("/api/ai/children/<child_id>/feedback", methods=["GET", "POST"])
@require_local_or_password
def ai_feedback_proxy(child_id):
    """反馈API代理"""
    server_url = USER_CONFIG.get("server_url", "")
    if not server_url:
        return jsonify({"success": False, "message": "未配置服务端地址"})

    try:
        if request.method == "GET":
            limit = request.args.get("limit", 20)
            response = public_client.signed_request(
                "GET",
                f"{server_url}/api/ai/children/{child_id}/feedback",
                params={"limit": limit},
                timeout=30,
            )
        else:
            # POST请求需要权限验证（使用统一的 session 检查）
            check_result = check_local_or_password()
            if check_result:
                return check_result

            data = request.get_json()
            response = public_client.signed_request(
                "POST",
                f"{server_url}/api/ai/children/{child_id}/feedback",
                json=data,
                timeout=30,
            )

        if response.status_code == 200:
            return response.json()
        else:
            return jsonify(
                {"success": False, "message": f"请求失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"请求失败: {str(e)}"})


@app.route("/api/ai/children/<child_id>/feedback/<int:feedback_id>", methods=["DELETE"])
@require_local_or_password
def ai_delete_feedback(child_id, feedback_id):
    """删除反馈"""
    server_url = USER_CONFIG.get("server_url", "")
    if not server_url:
        return jsonify({"success": False, "message": "未配置服务端地址"})

    try:
        response = public_client.signed_request(
            "DELETE",
            f"{server_url}/api/ai/children/{child_id}/feedback/{feedback_id}",
            timeout=30,
        )

        if response.status_code == 200:
            return response.json()
        else:
            return jsonify(
                {"success": False, "message": f"删除失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"删除失败: {str(e)}"})


@app.route("/api/ai/children/<child_id>/tags/<int:tag_id>/reject", methods=["POST"])
@require_local_or_password
def ai_reject_tag(child_id, tag_id):
    """否决标签"""
    server_url = USER_CONFIG.get("server_url", "")
    if not server_url:
        return jsonify({"success": False, "message": "未配置服务端地址"})

    try:
        response = public_client.signed_request(
            "POST",
            f"{server_url}/api/ai/children/{child_id}/tags/{tag_id}/reject",
            timeout=30,
        )

        if response.status_code == 200:
            return response.json()
        else:
            return jsonify(
                {"success": False, "message": f"操作失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"操作失败: {str(e)}"})


@app.route("/api/ai/children/<child_id>/tags/<int:tag_id>/restore", methods=["POST"])
@require_local_or_password
def ai_restore_tag(child_id, tag_id):
    """恢复标签"""
    server_url = USER_CONFIG.get("server_url", "")
    if not server_url:
        return jsonify({"success": False, "message": "未配置服务端地址"})

    try:
        response = public_client.signed_request(
            "POST",
            f"{server_url}/api/ai/children/{child_id}/tags/{tag_id}/restore",
            timeout=30,
        )

        if response.status_code == 200:
            return response.json()
        else:
            return jsonify(
                {"success": False, "message": f"操作失败: {response.status_code}"}
            )

    except Exception as e:
        return jsonify({"success": False, "message": f"操作失败: {str(e)}"})


@app.route("/api/ai/sync/batch", methods=["POST"])
@require_local_or_password
def ai_batch_sync():
    """批量同步历史数据到AI系统"""
    try:
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = ai_config.get("child_id", "")
        if not child_id:
            return jsonify({"success": False, "message": "未配置宝宝ID"})

        # 通过服务端代理访问健康AI
        server_url = USER_CONFIG.get("server_url", "")
        if not server_url:
            return jsonify({"success": False, "message": "未配置服务端地址"})

        data = request.get_json() or {}
        start_date = data.get("start_date", "")
        end_date = data.get("end_date", "")

        if not start_date or not end_date:
            return jsonify({"success": False, "message": "请选择同步的时间范围"})

        # 转换日期范围
        from datetime import datetime, timedelta

        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")

        if start > end:
            return jsonify({"success": False, "message": "开始日期不能晚于结束日期"})

        # 获取日期范围内的所有日期
        date_generated = [
            start + timedelta(days=x) for x in range(0, (end - start).days + 1)
        ]
        total_days = len(date_generated)
        success_count = 0

        from photo_manager import PhotoManager

        media_folders = getattr(public_client, "media_folders", [])
        pm = PhotoManager(media_folders, public_client.data_dir)

        for date_obj in date_generated:
            date_str = date_obj.strftime("%Y-%m-%d")
            try:
                # 获取当天照片
                photos = pm.get_photos_by_date(date_str)

                # 从服务端获取当天日志
                log_content = ""
                try:
                    log_resp = public_client.signed_request(
                        "GET",
                        f"{public_client.server_url}/czrz/baby/log",
                        params={"client_id": public_client.client_id, "date": date_str},
                        timeout=10,
                    )
                    if log_resp.status_code == 200:
                        log_data = log_resp.json()
                        if log_data.get("success") and log_data.get("log"):
                            log_content = log_data.get("log", "")
                except Exception as e:
                    print(f"[WARN] 获取日志失败 {date_str}: {e}")

                # 同步到AI
                payload = {
                    "child_id": child_id,
                    "date": date_str,
                    "photos": [
                        {
                            "path": p.get("path"),
                            "caption": p.get("caption"),
                            "tag": p.get("tag", {}).get("tag"),
                        }
                        for p in photos
                    ],
                    "logs": [{"content": log_content}] if log_content else [],
                    "context": {
                        "baby_name": public_client.baby_name,
                        "city": getattr(public_client, "user_city", None),
                    },
                }

                response = public_client.signed_request(
                    "POST",
                    f"{server_url}/api/ai/mumu/sync",
                    json=payload,
                    timeout=1200,
                )
                if response.status_code == 200:
                    success_count += 1

            except Exception as e:
                print(f"同步 {date_str} 失败: {e}")
                continue

        return jsonify(
            {
                "success": success_count == total_days,
                "message": f"同步完成，成功 {success_count}/{total_days} 天",
                "total": total_days,
                "success_count": success_count,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": f"同步失败: {str(e)}"})


# ==================== AI智能回顾 ====================

AI_REVIEW_TASKS = {}


def _is_ai_review_running() -> bool:
    """检查是否已有AI刷新任务在执行"""
    for task in AI_REVIEW_TASKS.values():
        if not task.get("completed"):
            return True
    return False


def _cleanup_stale_ai_review_tasks(timeout_seconds: int = 6 * 60 * 60) -> bool:
    """
    清理可能卡死的AI刷新任务

    Returns:
        bool: 是否清理过任务
    """
    now_ts = time.time()
    cleaned = False

    for task_id, task in list(AI_REVIEW_TASKS.items()):
        if task.get("completed"):
            continue

        started_at = task.get("started_at")
        if not started_at:
            continue

        try:
            started_dt = datetime.fromisoformat(started_at)
        except Exception:
            continue

        if (now_ts - started_dt.timestamp()) > timeout_seconds:
            task["completed"] = True
            task["message"] = "任务超时已自动结束"
            task["status"] = "timeout"
            cleaned = True

    return cleaned


@app.route("/api/ai/auto-review", methods=["POST"])
@require_local_or_password
def start_ai_auto_review():
    """启动AI智能回顾任务"""
    import threading
    import uuid

    try:
        _cleanup_stale_ai_review_tasks()
        if _is_ai_review_running():
            return jsonify({"success": False, "message": "刷新正在进行中，请稍后再试"})

        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if not ai_config.get("enabled", False):
            return jsonify({"success": False, "message": "AI服务未启用"})

        child_id = ai_config.get("child_id", "") or public_client.client_id
        if not child_id:
            return jsonify({"success": False, "message": "未配置宝宝ID"})

        if not public_client.client_id:
            return jsonify({"success": False, "message": "未注册客户端，请先连接服务端"})

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            return jsonify({"success": False, "message": "未配置媒体文件夹"})

        data = request.get_json() or {}

        task_id = str(uuid.uuid4())[:8]
        AI_REVIEW_TASKS[task_id] = {
            "status": "running",
            "processed": 0,
            "total": 0,
            "success_count": 0,
            "skipped": 0,
            "message": "正在扫描照片...",
            "details": [],
            "completed": False,
            "started_at": datetime.now().isoformat(),
        }

        thread = threading.Thread(
            target=run_ai_auto_review,
            args=(task_id, media_folders, child_id, ai_config),
            daemon=True,
        )
        thread.start()

        return jsonify({"success": True, "task_id": task_id})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


def run_ai_auto_review(task_id, media_folders, child_id, ai_config):
    """执行AI重新认识任务

    简化逻辑：对所有日期进行全量处理，基于哈希检查是否跳过
    - 处理所有索引中有日期的项
    - 数据库存在且哈希相等 → 跳过处理
    - 不存在或哈希不对 → 处理并保存/覆盖
    - 所有日期处理完毕后触发同步到健康AI和画像生成
    """
    from photo_manager import PhotoManager
    from datetime import datetime, timedelta
    import requests as req
    import re

    def is_valid_date(date_str: str) -> bool:
        """检查日期是否有效"""
        if not date_str:
            return False
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            return False
        try:
            year, month, day = map(int, date_str.split("-"))
            if not (2000 <= year <= 2030):
                return False
            if not (1 <= month <= 12):
                return False
            if not (1 <= day <= 31):
                return False
            return True
        except:
            return False

    try:
        pm = PhotoManager(media_folders, public_client.data_dir)
        server_client_id = public_client.client_id
        all_dates = pm.get_all_dates()

        today = datetime.now().strftime("%Y-%m-%d")

        # 获取索引日期过滤设置（从配置文件读取，确保重启后生效）
        index_after = USER_CONFIG.get("index_after_date", "") or ""
        logger.info(f"[AI回顾] index_after_date = '{index_after}'")

        # 过滤无效日期、未来日期和索引设置之前的日期，按日期倒序处理（最近优先）
        dates_to_process = sorted(
            [
                d
                for d in all_dates
                if d < today
                and is_valid_date(d)
                and (not index_after or d >= index_after)
            ],
            reverse=True,
        )

        if index_after:
            logger.info(f"[AI回顾] 索引日期过滤: 只处理 {index_after} 之后的日期")

        AI_REVIEW_TASKS[task_id]["total"] = len(dates_to_process)
        AI_REVIEW_TASKS[task_id]["message"] = f"正在准备处理数据..."

        # 检查初始配额
        can_use, used, limit = _check_token_quota()
        if not can_use:
            AI_REVIEW_TASKS[task_id]["completed"] = True
            AI_REVIEW_TASKS[task_id]["quota_exceeded"] = True
            AI_REVIEW_TASKS[task_id]["message"] = (
                f"Token配额已用完（已用 {used:,} / 限额 {limit:,}）"
            )
            return

        success_count = 0
        skipped_count = 0
        import time

        for i, date in enumerate(dates_to_process):
            try:
                AI_REVIEW_TASKS[task_id]["message"] = (
                    f"正在处理 {date} ({i + 1}/{len(dates_to_process)})"
                )

                # 【调试】输出当前处理的日期
                logger.info(f"\n{'=' * 60}")
                logger.info(f"[DEBUG] 处理日期：{date}")
                logger.info(f"{'=' * 60}")

                # 性能监控：记录开始时间
                date_start_time = time.time()

                # 获取当天所有媒体（照片+视频）
                all_media = pm.get_photos_by_date(date)
                photos = [p for p in all_media if not p.get("is_video", False)]
                videos = [p for p in all_media if p.get("is_video", False)]

                photo_paths = [p.get("path") for p in photos if p.get("path")]
                video_paths = [p.get("path") for p in videos if p.get("path")]

                # 简化逻辑：基于哈希检查是否处理
                needs_process = False
                processed_photos = []
                processed_videos = []
                unprocessed_photos = []
                unprocessed_videos = []
                photo_hash_map = {}  # 存储照片哈希用于检查

                # 1. 获取数据库中的照片哈希记录
                db_photos = get_processed_photos(date, server_client_id)
                db_photo_map = {}
                for db_photo in db_photos:
                    if isinstance(db_photo, dict):
                        path = db_photo.get("path", "")
                        db_hash = db_photo.get("hash", "") or db_photo.get(
                            "file_hash", ""
                        )
                        if path and db_hash:
                            db_photo_map[path] = db_hash

                # 2. 检查照片是否需要处理
                for photo_path in photo_paths:
                    # 如果数据库中没有记录，直接需要处理
                    if photo_path not in db_photo_map:
                        unprocessed_photos.append(photo_path)
                        continue

                    db_hash = db_photo_map[photo_path]

                    # 从photo_manager获取照片信息（包含索引中已有的哈希）
                    photo_hash = ""
                    try:
                        photo_info = pm.get_photo_by_path(photo_path)
                        if photo_info and "hash" in photo_info:
                            photo_hash = photo_info["hash"]
                    except Exception as e:
                        logger.info(f"[AI回顾] 获取照片哈希失败 {photo_path}: {e}")

                    if photo_hash and photo_hash == db_hash:
                        # 哈希匹配，跳过
                        processed_photos.append(photo_path)
                    else:
                        # 哈希不匹配、获取失败或没有哈希，需要处理
                        unprocessed_photos.append(photo_path)

                # 4. 检查视频是否需要处理
                db_videos = get_processed_videos(date, server_client_id)
                db_video_map = {}
                for db_video in db_videos:
                    if isinstance(db_video, dict):
                        db_path = db_video.get("video_path", "")
                        db_hash = db_video.get("file_hash", "")
                        if db_path and db_hash:
                            db_video_map[Path(db_path).name] = db_hash
                    elif isinstance(db_video, str):
                        # 旧格式，没有哈希信息
                        db_video_map[Path(db_video).name] = None

                for video_path in video_paths:
                    video_name = Path(video_path).name
                    db_hash = db_video_map.get(video_name)

                    # 情况1: 数据库中没有记录或没有哈希信息
                    if db_hash is None:
                        unprocessed_videos.append(video_path)
                        continue

                    # 情况2: 有哈希信息，从photo_manager获取视频哈希
                    video_hash = ""
                    try:
                        # 尝试从photo_manager获取视频信息
                        # 注意：photo_manager可能也有视频的索引
                        video_info = None
                        for media in all_media:
                            if media.get("path") == video_path and media.get(
                                "is_video", False
                            ):
                                video_info = media
                                break

                        if video_info and "hash" in video_info:
                            video_hash = video_info["hash"]
                        else:
                            # 如果没有索引哈希，需要计算
                            import hashlib

                            hasher = hashlib.md5()
                            with open(video_path, "rb") as f:
                                for chunk in iter(lambda: f.read(8192), b""):
                                    hasher.update(chunk)
                            video_hash = hasher.hexdigest()
                    except Exception as e:
                        logger.info(f"[AI回顾] 获取视频哈希失败 {video_path}: {e}")

                    if video_hash and video_hash == db_hash:
                        processed_videos.append(video_path)
                    else:
                        unprocessed_videos.append(video_path)

                # 5. 决定是否处理该日期
                needs_analyze = len(unprocessed_photos) > 0
                needs_video_process = len(unprocessed_videos) > 0
                needs_process = needs_analyze or needs_video_process

                # 6. 处理跳过的情况
                if not needs_process:
                    skipped_count += 1
                    date_end_time = time.time()
                    process_time = date_end_time - date_start_time
                    logger.info(
                        f"[AI回顾] ★★★ 跳过 {date}：所有媒体哈希匹配，无变化 ★★★"
                    )
                    logger.info(
                        f"[AI回顾]   原因: 照片={len(processed_photos)}张匹配，视频={len(processed_videos)}个匹配"
                    )
                    logger.info(f"[AI回顾]   性能: 处理耗时 {process_time:.2f}秒")
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {
                            "date": date,
                            "success": True,
                            "message": "所有媒体哈希匹配，跳过",
                        }
                    )
                    AI_REVIEW_TASKS[task_id]["skipped"] = skipped_count
                    AI_REVIEW_TASKS[task_id]["processed"] = i + 1
                    continue

                logger.info(f"[AI回顾] ★★★ 需要处理 {date} ★★★")
                logger.info(
                    f"[AI回顾]   原因: 未处理照片={len(unprocessed_photos)}张，未处理视频={len(unprocessed_videos)}个"
                )

                if not photo_paths and not video_paths:
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {"date": date, "success": False, "message": "无有效媒体路径"}
                    )
                    AI_REVIEW_TASKS[task_id]["processed"] = i + 1
                    continue

                # 步骤1：如果需要分析照片，进行AI分析
                ai_result = None
                analysis_result = None
                needs_sync = False  # 是否需要同步到健康AI

                if needs_analyze and unprocessed_photos:
                    # 只分析未处理的照片
                    try:
                        from select_best_photo import ai_select

                        ai_result = ai_select(
                            unprocessed_photos[:20], select_n=1, child_id=child_id
                        )

                        # 【调试】输出 AI 返回结果
                        logger.info(f"[DEBUG] AI 返回结果:")
                        logger.info(
                            f"  - selected: {len(ai_result.get('selected', []))}张"
                        )
                        logger.info(
                            f"  - photos 字段：{len(ai_result.get('photos', {}))}条"
                        )
                        logger.info(f"  - blurry: {len(ai_result.get('blurry', []))}张")
                        logger.info(
                            f"  - no_baby: {len(ai_result.get('no_baby', []))}张"
                        )
                        logger.info(
                            f"  - duplicates: {len(ai_result.get('duplicates', []))}组"
                        )
                        if ai_result.get("photos"):
                            logger.info(
                                f"  - photos 样例：{list(ai_result['photos'].items())[:2]}"
                            )

                        blurry_count = len(ai_result.get("blurry", []))
                        no_baby_count = len(ai_result.get("no_baby", []))
                        dup_groups = ai_result.get("duplicates", [])
                        dup_count = sum(len(g) - 1 for g in dup_groups if len(g) > 1)

                        print(
                            f"[AI回顾] {date}: 模糊{blurry_count}张, 无宝宝{no_baby_count}张, 重复{dup_count}张"
                        )

                    except Exception as e:
                        error_msg = str(e)
                        if (
                            "Token配额" in error_msg
                            or "ALL_MODELS_EXHAUSTED" in error_msg
                        ):
                            AI_REVIEW_TASKS[task_id]["completed"] = True
                            AI_REVIEW_TASKS[task_id]["quota_exceeded"] = True
                            if "ALL_MODELS_EXHAUSTED:" in error_msg:
                                friendly_msg = error_msg.split("ALL_MODELS_EXHAUSTED:")[
                                    1
                                ]
                            else:
                                friendly_msg = error_msg
                            AI_REVIEW_TASKS[task_id]["message"] = friendly_msg
                            report_error(
                                "ai_review_quota", friendly_msg, f"date={date}"
                            )
                            return
                        logger.info(f"[AI回顾] AI分析失败 {date}: {e}")
                        report_error(
                            "ai_review_failed", f"AI分析失败 {date}", error_msg
                        )
                        AI_REVIEW_TASKS[task_id]["details"].append(
                            {
                                "date": date,
                                "success": False,
                                "message": f"AI分析失败: {e}",
                            }
                        )
                        AI_REVIEW_TASKS[task_id]["processed"] = i + 1
                        continue

                # 步骤2：分析新照片并保存描述（如果有新照片）
                if needs_analyze and ai_result:
                    try:
                        from select_best_photo import analyze_all_photos

                        # 分析新照片并保存到数据库
                        analyze_all_photos(
                            unprocessed_photos,  # 只分析新照片
                            max_photos=20,
                            client_id=server_client_id,
                            date=date,
                            ai_result=ai_result,
                        )
                        print(
                            f"[DEBUG] 已保存 {len(unprocessed_photos)} 张新照片描述到数据库"
                        )
                        needs_sync = True
                    except Exception as e:
                        logger.info(f"[AI回顾] 保存新照片描述失败 {date}: {e}")

                # 步骤3：从数据库获取当天所有照片描述（用于后续处理）
                analysis_result = None
                try:
                    logger.info(f"[DEBUG] 从数据库获取当天所有照片描述")
                    all_descriptions = get_processed_photos(date, server_client_id)

                    if all_descriptions:
                        # 构建 analysis_result 格式
                        valid_photos = []
                        all_scenes = set()
                        all_activities = set()
                        baby_count = 0
                        scenery_count = 0

                        for desc in all_descriptions:
                            photo_info = {
                                "path": desc.get("path", ""),
                                "description": desc.get("description", ""),
                                "has_baby": desc.get("has_baby", True),
                                "scene": desc.get("scene", ""),
                                "activity": desc.get("activity", ""),
                            }
                            valid_photos.append(photo_info)

                            if photo_info["has_baby"]:
                                baby_count += 1
                            else:
                                scenery_count += 1
                            if photo_info["scene"]:
                                all_scenes.add(photo_info["scene"])
                            if photo_info["activity"]:
                                all_activities.add(photo_info["activity"])

                        # 构建综合摘要
                        combined_parts = []
                        if baby_count > 0:
                            combined_parts.append(f"共{baby_count}张宝宝照片")
                        if scenery_count > 0:
                            combined_parts.append(f"{scenery_count}张场景照片")

                        descriptions = [
                            p["description"] for p in valid_photos if p["description"]
                        ]
                        if descriptions:
                            combined_parts.append(
                                "活动：" + "、".join(descriptions[:10])
                            )

                        combined_summary = (
                            "。".join(combined_parts)
                            if combined_parts
                            else "照片分析完成"
                        )

                        analysis_result = {
                            "photos": valid_photos,
                            "scenes": list(all_scenes),
                            "activities": list(all_activities),
                            "baby_photos": baby_count,
                            "scenery_photos": scenery_count,
                            "combined_summary": combined_summary,
                        }
                        logger.info(
                            f"[DEBUG] 从数据库获取到 {len(valid_photos)} 条照片描述"
                        )

                        AI_REVIEW_TASKS[task_id]["details"].append(
                            {
                                "date": date,
                                "success": True,
                                "message": f"有效照片{len(valid_photos)}张",
                            }
                        )
                        needs_sync = True
                    else:
                        # 数据库中没有照片描述
                        analysis_result = {
                            "photos": [],
                            "scenes": [],
                            "activities": [],
                            "baby_photos": 0,
                            "scenery_photos": 0,
                            "combined_summary": "",
                        }
                        logger.info(f"[DEBUG] 数据库中没有照片描述")
                except Exception as e:
                    logger.info(f"[AI回顾] 获取照片描述失败 {date}: {e}")
                    analysis_result = None

                # 步骤4：保存精选照片（总是生成或更新）
                featured_info = None
                if ai_result:
                    selected = ai_result.get("selected", [])
                    reasons = ai_result.get("reasons", [])

                    if selected:
                        best_photo = selected[0]
                        ai_description = reasons[0] if reasons else "今日照片"
                        best_photo_filename = Path(best_photo).name

                        # 计算精选照片哈希用于比较
                        best_photo_hash = ""
                        try:
                            hasher = hashlib.md5()
                            with open(best_photo, "rb") as f:
                                for chunk in iter(lambda: f.read(8192), b""):
                                    hasher.update(chunk)
                            best_photo_hash = hasher.hexdigest()
                        except Exception as e:
                            logger.info(
                                f"[AI回顾] 计算精选照片哈希失败 {best_photo}: {e}"
                            )

                        # 检查是否已有相同的精选照片
                        existing_featured = get_featured_photo_info(
                            date, server_client_id
                        )
                        existing_hash = (
                            existing_featured.get("file_hash", "")
                            if existing_featured
                            else ""
                        )

                        if not existing_featured or best_photo_hash != existing_hash:
                            # 保存或更新精选照片
                            save_featured_photo_server(
                                date,
                                server_client_id,
                                best_photo,
                                ai_description,
                                file_hash=best_photo_hash,
                            )
                            featured_info = {
                                "filename": best_photo_filename,
                                "ai_description": ai_description,
                                "file_hash": best_photo_hash,
                            }
                            needs_sync = True
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": True,
                                    "message": f"精选: {best_photo_filename[:25]}...",
                                }
                            )
                        else:
                            featured_info = existing_featured
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": True,
                                    "message": "精选照片哈希匹配，跳过",
                                }
                            )
                    else:
                        featured_info = None
                        AI_REVIEW_TASKS[task_id]["details"].append(
                            {
                                "date": date,
                                "success": False,
                                "message": "未选出精选照片",
                            }
                        )
                else:
                    featured_info = get_featured_photo_info(date, server_client_id)
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {
                            "date": date,
                            "success": True,
                            "message": "无新照片，使用已有精选照片",
                        }
                    )

                # 步骤5：处理视频语音
                video_count = 0
                if needs_video_process and unprocessed_videos:
                    try:
                        print(
                            f"[AI回顾] {date}: 找到 {len(unprocessed_videos)} 个未处理视频"
                        )
                        from video_audio_processor import (
                            process_video_speech,
                            analyze_language_ability,
                        )

                        baby_info = getattr(public_client, "baby_info", {}) or {}
                        baby_name = baby_info.get("baby_name", "宝宝")
                        age_months = baby_info.get("age_months", 0)

                        for video_path_str in unprocessed_videos[:5]:
                            video_path = Path(video_path_str)
                            if not video_path.exists():
                                continue

                            try:
                                import time
                                import hashlib

                                time.sleep(2)
                                logger.info(
                                    f"[DEBUG-VIDEO] 开始处理视频: {video_path.name}"
                                )
                                speech_result = process_video_speech(
                                    video_path, max_duration=60
                                )

                                # 计算视频文件 hash
                                video_hash = ""
                                try:
                                    hasher = hashlib.md5()
                                    with open(video_path, "rb") as f:
                                        for chunk in iter(lambda: f.read(8192), b""):
                                            hasher.update(chunk)
                                    video_hash = hasher.hexdigest()
                                except Exception as e:
                                    logger.info(f"[AI回顾] 计算视频hash失败: {e}")

                                # 准备上传数据
                                upload_data = {
                                    "client_id": server_client_id,
                                    "date": date,
                                    "video_path": str(video_path),
                                    "file_hash": video_hash,
                                    "duration": speech_result.get("duration", 0),
                                }

                                if speech_result.get("success") and speech_result.get(
                                    "transcript"
                                ):
                                    time.sleep(2)
                                    analysis = analyze_language_ability(
                                        speech_result["transcript"],
                                        baby_name=baby_name,
                                        age_months=age_months,
                                    )
                                    upload_data["transcript"] = speech_result[
                                        "transcript"
                                    ]
                                    upload_data["analysis"] = analysis
                                else:
                                    # 语音识别失败或无内容
                                    error_msg = speech_result.get("error", "未知错误")
                                    upload_data["transcript"] = ""
                                    upload_data["analysis"] = {
                                        "error": error_msg,
                                        "note": "语音识别失败或无有效语音",
                                    }

                                # 上传视频处理结果
                                upload_resp = public_client.signed_request(
                                    "POST",
                                    f"{public_client.server_url}/czrz/speech/record",
                                    json=upload_data,
                                    timeout=30,
                                    verify=False,
                                )

                                if upload_resp.status_code == 200:
                                    video_count += 1
                                    needs_sync = True
                                    logger.info(
                                        f"[AI回顾] 视频处理成功: {video_path.name}"
                                    )
                                else:
                                    logger.info(
                                        f"[AI回顾] 视频处理失败: status={upload_resp.status_code}"
                                    )

                            except Exception as e:
                                logger.info(f"[AI回顾] 视频处理失败 {video_path}: {e}")

                        if video_count > 0:
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": True,
                                    "message": f"处理{video_count}个视频",
                                }
                            )
                        elif unprocessed_videos:
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": False,
                                    "message": f"视频处理失败",
                                }
                            )

                    except Exception as e:
                        logger.info(f"[AI回顾] 视频处理失败 {date}: {e}")

                # 步骤6：生成日志（总是生成或更新）
                log_generated = False

                # 获取照片描述用于生成日志
                all_descriptions = get_processed_photos(date, server_client_id)
                valid_photos = []
                for desc in all_descriptions:
                    if isinstance(desc, dict) and desc.get("description"):
                        valid_photos.append(desc)

                if valid_photos:
                    # 有照片描述，生成日志
                    photo_descs = [
                        p.get("description", "")
                        for p in valid_photos
                        if p.get("description")
                    ]

                    if photo_descs:
                        combined_desc = "、".join(photo_descs[:5])
                    else:
                        combined_desc = (
                            featured_info.get("ai_description")
                            if featured_info
                            else None
                        )

                    if combined_desc:
                        log_style = getattr(public_client, "log_style", "简练")
                        custom_style = getattr(public_client, "custom_style", "")
                        time.sleep(2)

                        try:
                            log_content = generate_ai_log(
                                date,
                                combined_desc,
                                server_client_id,
                                log_style,
                                custom_style,
                            )

                            if log_content:
                                featured_desc = (
                                    featured_info.get("ai_description")
                                    if featured_info
                                    else None
                                )
                                save_log_server(
                                    date, server_client_id, log_content, featured_desc
                                )
                                needs_sync = True
                                log_generated = True
                                AI_REVIEW_TASKS[task_id]["details"].append(
                                    {
                                        "date": date,
                                        "success": True,
                                        "message": "日志已生成",
                                    }
                                )
                            else:
                                AI_REVIEW_TASKS[task_id]["details"].append(
                                    {
                                        "date": date,
                                        "success": False,
                                        "message": "日志生成失败",
                                    }
                                )
                        except Exception as e:
                            logger.info(f"[AI回顾] 生成日志失败 {date}: {e}")
                            AI_REVIEW_TASKS[task_id]["details"].append(
                                {
                                    "date": date,
                                    "success": False,
                                    "message": f"日志生成异常: {str(e)[:30]}",
                                }
                            )
                    else:
                        AI_REVIEW_TASKS[task_id]["details"].append(
                            {
                                "date": date,
                                "success": False,
                                "message": "无照片描述，无法生成日志",
                            }
                        )
                elif photo_paths:
                    # 有照片但无描述
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {
                            "date": date,
                            "success": False,
                            "message": "照片无描述，无法生成日志",
                        }
                    )
                else:
                    # 无照片
                    AI_REVIEW_TASKS[task_id]["details"].append(
                        {
                            "date": date,
                            "success": True,
                            "message": "无照片，跳过日志生成",
                        }
                    )

            except Exception as e:
                AI_REVIEW_TASKS[task_id]["details"].append(
                    {"date": date, "success": False, "message": str(e)[:50]}
                )

            # 记录处理结果
            if needs_sync:
                success_count += 1

            AI_REVIEW_TASKS[task_id]["success_count"] = success_count
            AI_REVIEW_TASKS[task_id]["skipped"] = skipped_count
            AI_REVIEW_TASKS[task_id]["processed"] = i + 1

            # 性能监控：记录结束时间
            date_end_time = time.time()
            process_time = date_end_time - date_start_time
            logger.info(f"[AI回顾]   性能: 处理 {date} 耗时 {process_time:.2f}秒")

        AI_REVIEW_TASKS[task_id]["completed"] = True

        # 计算实际处理天数（跳过不算）
        actual_processed = success_count

        if not dates_to_process:
            AI_REVIEW_TASKS[task_id]["message"] = "没有需要处理的日期"
        elif actual_processed == 0:
            AI_REVIEW_TASKS[task_id]["message"] = "完成！数据已是最新状态"
        elif skipped_count > 0:
            AI_REVIEW_TASKS[task_id]["message"] = "完成！AI刷新已完成"
        else:
            AI_REVIEW_TASKS[task_id]["message"] = "完成！AI刷新已完成"

        # 触发同步到健康AI（无论是否有变化）
        try:
            server_url = USER_CONFIG.get("server_url", "")
            if server_url:
                # 触发服务端同步（需要签名）
                public_client.signed_request(
                    "POST",
                    f"{server_url}/czrz/ai/sync-trigger",
                    json={
                        "client_id": public_client.client_id,
                        "child_id": child_id,
                    },
                    timeout=30,
                )
                logger.info("[AI回顾] 已触发服务端同步到健康AI")

                # 等待片刻后触发画像生成
                import time as time_module

                time_module.sleep(2)

                public_client.signed_request(
                    "POST",
                    f"{server_url}/czrz/profile/generate-trigger",
                    json={
                        "client_id": public_client.client_id,
                        "child_id": child_id,
                    },
                    timeout=30,
                )
                logger.info("[AI回顾] 已触发画像生成")
        except Exception as e:
            logger.info(f"[AI回顾] 触发同步/画像生成失败: {e}")

    except Exception as e:
        AI_REVIEW_TASKS[task_id]["completed"] = True
        AI_REVIEW_TASKS[task_id]["message"] = f"任务失败: {str(e)}"


def check_has_featured_photo(date: str, client_id: str) -> bool:
    """检查是否已有精选照片（从服务端检查）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/photo/featured",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success") and result.get("photo"):
                return True
    except Exception as e:
        logger.info(f"[AI回顾] 检查服务端精选照片失败: {e}")
    return False


def get_featured_photo_info(date: str, client_id: str) -> dict:
    """获取精选照片信息（从服务端获取，包含哈希）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/photo/featured",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        logger.info(
            f"[DEBUG] get_featured_photo_info: status={resp.status_code}, url={resp.url}"
        )
        if resp.status_code == 200:
            result = resp.json()
            logger.info(f"[DEBUG] get_featured_photo_info: result={result}")
            if result.get("success") and result.get("photo"):
                return result["photo"]
        else:
            logger.info(
                f"[DEBUG] get_featured_photo_info: error status={resp.status_code}, text={resp.text[:200]}"
            )
    except Exception as e:
        logger.info(f"[DEBUG] get_featured_photo_info: exception={e}")
    return None


def get_featured_photo_by_hash(file_hash: str) -> dict:
    """通过哈希获取精选照片的本地文件信息"""
    from photo_manager import PhotoManager

    media_folders = getattr(public_client, "media_folders", [])
    if not media_folders:
        return None

    pm = PhotoManager(media_folders, public_client.data_dir)
    entry = pm.get_photo_by_hash(file_hash)
    if entry:
        path = entry.get("path")
        if path and Path(path).exists():
            return entry
    return None


def check_has_log(date: str, client_id: str) -> bool:
    """检查是否已有日志（从服务端检查）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/baby/log",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success") and result.get("log"):
                return True
    except Exception as e:
        logger.info(f"[AI回顾] 检查服务端日志失败: {e}")
    return False


def get_processed_photos(date: str, client_id: str) -> list:
    """获取某天已处理的照片列表（从服务端）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/photo/descriptions",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return result.get("photos", [])
    except Exception as e:
        logger.info(f"[AI回顾] 获取已处理照片失败: {e}")
    return []


def check_new_feedback(date: str, client_id: str) -> bool:
    """检查某天是否有新的画像反馈"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/feedback/check",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return result.get("has_new", False)
    except Exception as e:
        logger.info(f"[AI回顾] 检查新反馈失败: {e}")
    return False


def check_new_health_metrics(date: str, client_id: str) -> bool:
    """检查某天是否有新的健康指标"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/health/check",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return result.get("has_new", False)
    except Exception as e:
        logger.info(f"[AI回顾] 检查新健康指标失败: {e}")
    return False


def get_processed_videos(date: str, client_id: str) -> list:
    """获取某天已处理的视频列表（从服务端）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/video/processed",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success"):
                return result.get("videos", [])
    except Exception as e:
        logger.info(f"[AI回顾] 获取已处理视频失败: {e}")
    return []


# ==================== 画像输入变更检测（方案2完整版）====================


def _get_input_sync_state_file() -> Path:
    """获取输入同步状态文件路径（统一存储所有画像输入的状态）"""
    if hasattr(sys, "_MEIPASS"):
        data_dir = Path.home() / "Documents" / "CZRZ"
    else:
        data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "profile_input_sync_state.json"


def _load_input_sync_state() -> dict:
    """加载输入同步状态"""
    state_file = _get_input_sync_state_file()
    logger.info(f"[DEBUG-STATE] 加载状态文件: {state_file}")
    logger.info(f"[DEBUG-STATE] 文件存在: {state_file.exists()}")
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                content = f.read()
                logger.info(f"[DEBUG-STATE] 文件内容长度: {len(content)} 字符")
                data = json.loads(content)
                logger.info(f"[DEBUG-STATE] 解析成功，顶层keys: {list(data.keys())}")
                return data
        except Exception as e:
            logger.info(f"[DEBUG-STATE] 加载失败: {e}")
            pass
    logger.info(f"[DEBUG-STATE] 返回空状态")
    return {}


def _save_input_sync_state(state: dict):
    """保存输入同步状态"""
    state_file = _get_input_sync_state_file()
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.info(f"[输入同步] 保存状态失败: {e}")


def _get_input_hash(date: str, client_id: str, input_type: str) -> str:
    """获取某天的某种输入的保存 hash"""
    state = _load_input_sync_state()
    logger.info(
        f"[DEBUG-HASH-READ] 查询: client_id={client_id[:8]}..., date={date}, type={input_type}"
    )
    client_state = state.get(client_id, {})
    date_state = client_state.get(date, {})
    result = date_state.get(input_type, "")
    logger.info(f"[DEBUG-HASH-READ] 结果: '{result[:16] if result else '<空>'}'")
    return result


def _save_input_hash(date: str, client_id: str, input_type: str, hash_value: str):
    """保存某天某种输入的 hash"""
    logger.info(
        f"[DEBUG-HASH-SAVE] 保存: date={date}, type={input_type}, hash={hash_value[:16] if hash_value else '<空>'}"
    )
    state = _load_input_sync_state()
    if client_id not in state:
        state[client_id] = {}
    if date not in state[client_id]:
        state[client_id][date] = {}
    state[client_id][date][input_type] = hash_value
    _save_input_sync_state(state)


# ========== 1. 标签变更检测 ==========


def get_photo_tags_hash(date: str, client_id: str) -> str:
    """
    计算某天所有照片标签的 hash
    通过文件名解析日期，获取当天的所有标签
    """
    try:
        from photo_tags import get_all_photo_tags

        all_tags = get_all_photo_tags(client_id)
        if not all_tags:
            return ""

        # 筛选当天的标签（从文件名解析日期）
        day_tags = []
        for filename, tag_info in all_tags.items():
            try:
                # 文件名格式: 2024-03-07_143022.jpg
                file_date = filename.split("_")[0]
                if file_date == date:
                    day_tags.append(
                        {
                            "filename": filename,
                            "tag": tag_info.get("tag", ""),
                            "note": tag_info.get("note", ""),
                            "updated_at": tag_info.get("updated_at", ""),
                        }
                    )
            except Exception:
                continue

        if not day_tags:
            return ""

        # 按文件名排序，确保一致性
        day_tags.sort(key=lambda x: x["filename"])

        # 计算 hash：包含标签内容、备注和更新时间
        hash_content = json.dumps(day_tags, sort_keys=True, ensure_ascii=True)
        return hashlib.md5(hash_content.encode()).hexdigest()[:16]

    except Exception as e:
        logger.info(f"[标签同步] 计算标签 hash 失败: {e}")
        return ""


def check_tag_changes(date: str, client_id: str) -> bool:
    """检查某天的标签是否有变化"""
    logger.info(f"[DEBUG-TAG] ========== 开始检查标签变化: {date} ==========")
    current_hash = get_photo_tags_hash(date, client_id)
    saved_hash = _get_input_hash(date, client_id, "tags")

    logger.info(f"[DEBUG-TAG] 当前hash: '{current_hash}'")
    logger.info(f"[DEBUG-TAG] 保存hash: '{saved_hash}'")
    logger.info(f"[DEBUG-TAG] hash相等: {current_hash == saved_hash}")

    if not current_hash and not saved_hash:
        logger.info(f"[DEBUG-TAG] 结果: 无标签数据，返回False")
        return False
    if current_hash and not saved_hash:
        logger.info(f"[DEBUG-TAG] 结果: 新增标签，返回True")
        return True

    has_changes = current_hash != saved_hash
    logger.info(f"[DEBUG-TAG] 结果: 是否有变化={has_changes}")
    return has_changes


# ========== 2. 照片描述变更检测 ==========


def get_photo_descriptions_hash(date: str, client_id: str) -> str:
    """
    计算某天所有照片描述的 hash
    """
    try:
        all_descriptions = get_processed_photos(date, client_id)
        if not all_descriptions:
            return ""

        # 提取关键字段计算 hash
        desc_list = []
        for desc in all_descriptions:
            desc_list.append(
                {
                    "path": desc.get("path", ""),
                    "description": desc.get("description", ""),
                    "has_baby": desc.get("has_baby", True),
                    "scene": desc.get("scene", ""),
                    "activity": desc.get("activity", ""),
                }
            )

        # 按路径排序
        desc_list.sort(key=lambda x: x["path"])

        hash_content = json.dumps(desc_list, sort_keys=True, ensure_ascii=True)
        return hashlib.md5(hash_content.encode()).hexdigest()[:16]

    except Exception as e:
        logger.info(f"[描述同步] 计算描述 hash 失败: {e}")
        return ""


def check_photo_description_changes(date: str, client_id: str) -> bool:
    """检查某天的照片描述是否有变化"""
    logger.info(f"[DEBUG-DESC] ========== 开始检查描述变化: {date} ==========")
    current_hash = get_photo_descriptions_hash(date, client_id)
    saved_hash = _get_input_hash(date, client_id, "photo_descriptions")

    logger.info(f"[DEBUG-DESC] 当前hash: '{current_hash}'")
    logger.info(f"[DEBUG-DESC] 保存hash: '{saved_hash}'")
    logger.info(f"[DEBUG-DESC] hash相等: {current_hash == saved_hash}")

    if not current_hash and not saved_hash:
        logger.info(f"[DEBUG-DESC] 结果: 无描述数据，返回False")
        return False
    if current_hash and not saved_hash:
        logger.info(f"[DEBUG-DESC] 结果: 新增描述，返回True")
        return True

    has_changes = current_hash != saved_hash
    logger.info(f"[DEBUG-DESC] 结果: 是否有变化={has_changes}")
    return has_changes


# ========== 3. 语音记录变更检测 ==========


def get_speech_records_hash(date: str, client_id: str) -> str:
    """
    计算某天所有语音记录的 hash
    """
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/speech/records",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        if resp.status_code != 200:
            return ""

        result = resp.json()
        if not result.get("success"):
            return ""

        records = result.get("records", [])
        if not records:
            return ""

        # 提取关键字段
        speech_list = []
        for record in records:
            speech_list.append(
                {
                    "video_path": record.get("video_path", ""),
                    "transcript": record.get("transcript", ""),
                    "duration": record.get("duration", 0),
                    "language_analysis": record.get("language_analysis", {}),
                }
            )

        # 按视频路径排序
        speech_list.sort(key=lambda x: x["video_path"])

        hash_content = json.dumps(speech_list, sort_keys=True, ensure_ascii=True)
        return hashlib.md5(hash_content.encode()).hexdigest()[:16]

    except Exception as e:
        logger.info(f"[语音同步] 计算语音 hash 失败: {e}")
        return ""


def check_speech_changes(date: str, client_id: str) -> bool:
    """检查某天的语音记录是否有变化"""
    logger.info(f"[DEBUG-SPEECH] ========== 开始检查语音变化: {date} ==========")
    current_hash = get_speech_records_hash(date, client_id)
    saved_hash = _get_input_hash(date, client_id, "speech_records")

    logger.info(f"[DEBUG-SPEECH] 当前hash: '{current_hash}'")
    logger.info(f"[DEBUG-SPEECH] 保存hash: '{saved_hash}'")
    logger.info(f"[DEBUG-SPEECH] hash相等: {current_hash == saved_hash}")

    if not current_hash and not saved_hash:
        logger.info(f"[DEBUG-SPEECH] 结果: 无语音数据，返回False")
        return False
    if current_hash and not saved_hash:
        logger.info(f"[DEBUG-SPEECH] 结果: 新增语音，返回True")
        return True

    has_changes = current_hash != saved_hash
    logger.info(f"[DEBUG-SPEECH] 结果: 是否有变化={has_changes}")
    return has_changes


# ========== 状态初始化（避免重复处理）==========


def _init_sync_state_for_processed_dates(client_id: str, dates_to_check: list):
    """
    为已处理的日期初始化状态文件
    避免首次运行时误判已有数据为"新增"
    只处理最近30天，避免初始化时间过长
    """
    # 只处理最近30天，避免初始化时间过长
    recent_dates = dates_to_check[:30]
    print(f"[状态初始化] 检查最近 {len(recent_dates)} 个日期...")
    initialized_count = 0
    initialized_dates = []

    for date in recent_dates:
        # 检查是否已有数据
        has_data = False

        # 检查照片描述
        desc_hash = get_photo_descriptions_hash(date, client_id)
        if desc_hash:
            _save_input_hash(date, client_id, "photo_descriptions", desc_hash)
            has_data = True

        # 检查标签
        tag_hash = get_photo_tags_hash(date, client_id)
        if tag_hash:
            _save_input_hash(date, client_id, "tags", tag_hash)
            has_data = True

        # 检查语音
        speech_hash = get_speech_records_hash(date, client_id)
        if speech_hash:
            _save_input_hash(date, client_id, "speech_records", speech_hash)
            has_data = True

        if has_data:
            initialized_count += 1
            initialized_dates.append(date)

    print(f"[状态初始化] 已为 {initialized_count} 个日期初始化状态")
    return initialized_dates  # 返回初始化的日期列表


# ========== 4. 日志内容变更检测 ==========


def get_log_content_hash(date: str, client_id: str) -> str:
    """
    计算某天日志内容的 hash
    """
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/baby/log",
            params={"client_id": client_id, "date": date},
            timeout=10,
        )
        if resp.status_code != 200:
            return ""

        result = resp.json()
        if not result.get("success"):
            return ""

        log = result.get("log", {})
        if not log:
            return ""

        # log 可能是 dict 或 str
        if isinstance(log, dict):
            content = log.get("content", "")
        else:
            content = str(log) if log else ""
        if not content:
            return ""

        return hashlib.md5(content.encode()).hexdigest()[:16]

    except Exception as e:
        logger.info(f"[日志同步] 计算日志 hash 失败: {e}")
        return ""


def check_log_changes(date: str, client_id: str) -> bool:
    """检查某天的日志内容是否有变化"""
    logger.info(f"[DEBUG-LOG] ========== 开始检查日志变化: {date} ==========")
    current_hash = get_log_content_hash(date, client_id)
    saved_hash = _get_input_hash(date, client_id, "log_content")

    logger.info(f"[DEBUG-LOG] 当前hash: '{current_hash}'")
    logger.info(f"[DEBUG-LOG] 保存hash: '{saved_hash}'")
    logger.info(f"[DEBUG-LOG] hash相等: {current_hash == saved_hash}")

    if not current_hash and not saved_hash:
        logger.info(f"[DEBUG-LOG] 结果: 无日志数据，返回False")
        return False
    if current_hash and not saved_hash:
        logger.info(f"[DEBUG-LOG] 结果: 新增日志，返回True")
        return True

    has_changes = current_hash != saved_hash
    logger.info(f"[DEBUG-LOG] 结果: 是否有变化={has_changes}")
    return has_changes


# ========== 统一保存所有输入状态 ==========


def save_input_sync_state(date: str, client_id: str):
    """在画像生成完成后保存所有输入的 hash"""
    try:
        # 保存标签 hash
        tag_hash = get_photo_tags_hash(date, client_id)
        logger.info(f"[DEBUG-SAVE] 标签hash: '{tag_hash}'")
        if tag_hash:
            _save_input_hash(date, client_id, "tags", tag_hash)

        # 保存照片描述 hash
        desc_hash = get_photo_descriptions_hash(date, client_id)
        logger.info(f"[DEBUG-SAVE] 描述hash: '{desc_hash}'")
        if desc_hash:
            _save_input_hash(date, client_id, "photo_descriptions", desc_hash)

        # 保存语音记录 hash
        speech_hash = get_speech_records_hash(date, client_id)
        logger.info(f"[DEBUG-SAVE] 语音hash: '{speech_hash}'")
        if speech_hash:
            _save_input_hash(date, client_id, "speech_records", speech_hash)

        # 保存日志内容 hash
        log_hash = get_log_content_hash(date, client_id)
        logger.info(f"[DEBUG-SAVE] 日志hash: '{log_hash}'")
        if log_hash:
            _save_input_hash(date, client_id, "log_content", log_hash)

        logger.info(f"[DEBUG-SAVE] 已保存所有输入状态: {date}")
    except Exception as e:
        logger.info(f"[输入同步] 保存状态失败: {e}")


def select_best_photo_ai(photo_paths: list, max_photos: int = 20) -> tuple:
    """AI选择最佳照片，同时返回描述和无宝宝照片列表

    优化：
    - 跳过预处理（AI会识别模糊/重复/无宝宝）
    - 后处理校验：如果选中的照片明显模糊，换下一张
    - 返回无宝宝照片列表，用于后续分析排除

    Returns:
        tuple: (best_photo, ai_description, no_baby_list)
    """
    if not photo_paths:
        return None, None, []

    try:
        from select_best_photo import ai_select, is_blurry, CV2_AVAILABLE

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
        images = [p for p in photo_paths if p.lower().endswith(IMAGE_EXTENSIONS)]

        if not images:
            return None, None, []

        limited = images[:max_photos]

        result = ai_select(limited, select_n=1)
        selected = result.get("selected", [])
        blurry_list = result.get("blurry", [])
        no_baby_list = result.get("no_baby", [])

        if selected:
            best_photo = selected[0]

            # 后处理：如果选中的照片明显模糊，尝试换一张
            if CV2_AVAILABLE and is_blurry(best_photo):
                print(f"[AI选图] AI选中的照片模糊，尝试换一张")
                # 从非模糊、有宝宝的照片中选一张
                non_blurry = [
                    p
                    for p in limited
                    if p not in blurry_list
                    and p not in no_baby_list
                    and not is_blurry(p)
                ]
                if non_blurry:
                    best_photo = non_blurry[0]

            reasons = result.get("reasons", [])
            ai_description = reasons[0] if reasons else "今日照片"
            return best_photo, ai_description, no_baby_list

    except Exception as e:
        logger.info(f"[AI回顾] AI选图失败: {e}")
        import traceback

        traceback.print_exc()

    # 降级：选第一张非模糊照片
    if photo_paths:
        try:
            from select_best_photo import is_blurry

            for p in photo_paths[:5]:
                if not is_blurry(p):
                    return p, "今日照片", []
        except:
            pass
        return photo_paths[0], "今日照片", []

    return None, None, []


def save_featured_photo_server(
    date: str,
    client_id: str,
    photo_path: str,
    ai_description: str,
    file_hash: str = "",
):
    """保存精选照片到服务端（使用文件哈希）"""
    try:
        from photo_manager import PhotoManager
        from pathlib import Path as PathLib

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            logger.info("[AI回顾] 未配置媒体文件夹")
            return False

        pm = PhotoManager(media_folders, public_client.data_dir)
        filename = PathLib(photo_path).name

        entry = pm.get_photo_by_filename(filename)
        if not entry:
            logger.info(f"[AI回顾] 未找到照片索引: {filename}")
            return False

        if not file_hash:
            file_hash = entry.get("hash")
        if not file_hash:
            logger.info(f"[AI回顾] 照片缺少哈希: {filename}")
            return False

        resp = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/photo/featured/update",
            json={
                "client_id": client_id,
                "date": date,
                "file_hash": file_hash,
                "filename": filename,
                "ai_description": ai_description,
                "selected_by": "auto",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"[AI回顾] 精选照片已保存到服务端: {filename}")
            return True
        logger.info(
            f"[AI回顾] 保存精选照片到服务端失败: status={resp.status_code}"
        )
        return False
    except Exception as e:
        logger.info(f"[AI回顾] 保存精选照片到服务端失败: {e}")
        return False


def _check_token_quota() -> tuple:
    """检查 token 配额，返回 (can_use, used, limit)"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/client/token-check",
            params={"client_id": public_client.client_id},
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("can_use", True), data.get("used", 0), data.get("limit", 0)
    except Exception as e:
        print(f"[Token] 配额检查失败: {e}")
    return True, 0, 0


def _report_token_usage(usage: dict, operation: str = "ai_call", context: dict = None):
    """上报 token 使用到服务端"""
    try:
        resp = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/client/token-record",
            json={
                "client_id": public_client.client_id,
                "usage": usage,
                "operation": operation,
                "context": context or {},
            },
            timeout=5,
        )
        print(
            f"[Token] 上报 {operation}: {usage.get('total_tokens', 0)} tokens, 状态: {resp.status_code}"
        )
    except Exception as e:
        print(f"[Token] 上报失败: {e}")


@app.route("/api/today-news")
def api_today_news():
    """获取今日新闻 - 代理到服务端"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/today-news",
            timeout=10,
        )
        if resp.status_code == 200:
            return jsonify(resp.json())
        else:
            return jsonify(
                {"success": False, "message": f"服务端错误: {resp.status_code}"}
            )
    except Exception as e:
        print(f"[新闻] 获取今日新闻失败: {e}")
        return jsonify({"success": False, "message": str(e)})


def get_today_news():
    """从服务端获取今日新闻（内部调用）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/today-news",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("news"):
                return [n.get("title", "") for n in data["news"][:3]]
    except Exception as e:
        print(f"[新闻] 获取今日新闻失败: {e}")
    return []


def generate_ai_log(
    date: str,
    photo_description: str,
    client_id: str,
    log_style: str = "简练",
    custom_style: str = "",
    include_news: bool = True,
) -> str:
    """生成AI日志 - 通过服务端代理

    Args:
        date: 日期
        photo_description: 照片描述
        client_id: 客户端ID
        log_style: 日志风格
        custom_style: 自定义风格
        include_news: 是否包含今日新闻（默认True，用于AI重新认识）
    """
    weather = get_weather_for_date(date)
    news_list = get_today_news() if include_news else []

    style_instructions = {
        "简练": "用简洁明了的语言，像日常记录，100字左右",
        "诗意": "用文艺清新的语言，可以加入诗意的比喻，150字左右",
        "东北腔": "用东北话的口吻写，幽默风趣，可以说'这孩子咋这么招人稀罕'、'杠杠的'等，100字左右",
        "详细": "详细记录当天的活动细节，时间、地点、做了什么，200字左右",
        "童趣": "用轻松可爱的语气，像给宝宝讲故事，150字左右",
        "深情": "用温暖感人的语言，表达父母对宝宝的爱，150字左右",
        "IT男风格": "用程序员风格写，可以用代码注释、技术术语，如'// 今日活动：户外探索 v1.0'，100字左右",
    }

    if log_style == "自定义" and custom_style:
        style_instruction = custom_style
    else:
        style_instruction = style_instructions.get(
            log_style, style_instructions["简练"]
        )

    news_section = ""
    if news_list:
        news_section = f"\n今日新闻：\n{chr(10).join(f'- {n}' for n in news_list)}\n"

    prompt = f"""请根据以下信息为宝宝写一条成长日志。

日期：{date}
天气：{weather or "未知"}
{news_section}
照片内容：
{photo_description or "无照片"}

风格要求：{style_instruction}

注意事项：
1. 语言要自然、温馨，像父母写的日记
2. 内容要基于照片描述，不要虚构
3. 如果照片中有风景、活动场景但看不到宝宝，说明是活动场景记录，也要记录
{"4. 如果有今日新闻，可以根据新闻内容与照片描述的关联性，自然地融入日志中。例如：宝宝爬得很快，恰好今天是马拉松比赛，可以联想宝宝以后会不会也喜欢跑步；宝宝在看球赛视频，恰好今天有足球比赛新闻，可以联系起来。不要生硬地附加在末尾，而是找到合适的切入点自然融入。如果新闻与照片内容没有关联，则不必强行加入。" if news_list else ""}

请直接输出日志内容，不要其他说明："""

    try:
        logger.info(f"[AI回顾] 通过服务端代理生成日志: {date}")
        resp = public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/ai/proxy/text",
            json={
                "client_id": public_client.client_id,
                "prompt": prompt,
                "max_tokens": 500,
                "operation": "generate_log",
            },
            timeout=300,
        )

        logger.info(f"[AI回顾] API响应状态: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                log_content = data.get("result", {}).get("content", "")
                print(
                    f"[AI回顾] 生成日志成功，长度: {len(log_content) if log_content else 0}"
                )
                return log_content if log_content else None
            else:
                logger.info(f"[AI回顾] 代理错误: {data.get('error')}")
        else:
            logger.info(f"[AI回顾] 服务端错误: {resp.status_code}")
    except Exception as e:
        logger.info(f"[AI回顾] 生成日志失败: {e}")

    return None


def save_log_server(
    date: str, client_id: str, log_content: str, photo_description: str
):
    """保存日志到服务端数据库"""
    try:
        public_client.signed_request(
            "POST",
            f"{public_client.server_url}/czrz/log/save",
            json={
                "client_id": client_id,
                "date": date,
                "content": log_content,
                "metadata": {
                    "photo_description": photo_description,
                    "generated_by": "ai_auto_review",
                },
            },
            timeout=10,
        )
        logger.info(f"[AI回顾] 日志已保存到服务端: {date}")
        return True
    except Exception as e:
        logger.info(f"[AI回顾] 保存日志到服务端失败: {e}")
        return False


def get_weather_for_date(date: str) -> str:
    """获取指定日期的天气（从服务端日志获取）"""
    try:
        resp = public_client.signed_request(
            "GET",
            f"{public_client.server_url}/czrz/baby/log",
            params={"client_id": public_client.client_id, "date": date},
            timeout=10,
        )
        if resp.status_code == 200:
            result = resp.json()
            if result.get("success") and result.get("weather"):
                weather = result["weather"]
                if isinstance(weather, dict):
                    return weather.get("condition") or weather.get("city")
                return str(weather)
    except Exception as e:
        logger.info(f"[天气] 获取日志天气失败: {e}")
    return None


@app.route("/api/ai/auto-review/status/<task_id>")
def get_ai_review_status(task_id):
    """获取AI智能回顾任务状态"""
    if task_id in AI_REVIEW_TASKS:
        return jsonify(
            {"success": True, "status": AI_REVIEW_TASKS[task_id], "task_id": task_id}
        )
    else:
        return jsonify({"success": False, "message": "任务不存在"})


@app.route("/api/ai/auto-review/status/latest")
def get_latest_ai_review_status():
    """获取最新的AI智能回顾任务状态"""
    if not AI_REVIEW_TASKS:
        return jsonify({"success": False, "message": "没有任务"})

    # 找到最新的未完成任务
    latest_task_id = None
    latest_task = None

    for task_id, task in AI_REVIEW_TASKS.items():
        if not task.get("completed", False):
            if latest_task is None or task.get("started_at", "") > latest_task.get(
                "started_at", ""
            ):
                latest_task_id = task_id
                latest_task = task

    if latest_task_id:
        return jsonify(
            {"success": True, "status": latest_task, "task_id": latest_task_id}
        )
    else:
        return jsonify({"success": False, "message": "没有运行中的任务"})


# ==================== 压缩服务 API ====================


@app.route("/api/compression/status")
def get_compression_status():
    """获取压缩状态"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if manager:
            status = manager.get_status()
            return jsonify({"success": True, "data": status})
        else:
            return jsonify(
                {
                    "success": True,
                    "data": {
                        "enabled": False,
                        "ffmpeg_available": False,
                        "videos_compressed": 0,
                        "images_compressed": 0,
                        "queue_length": 0,
                        "current_file": None,
                    },
                }
            )
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/compression/settings", methods=["GET", "POST"])
@require_local_or_password
def compression_settings():
    """获取或保存压缩设置"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"})

        if request.method == "GET":
            settings = manager.get_settings()
            return jsonify({"success": True, "data": settings})

        else:
            # POST请求需要权限验证（使用统一的 session 检查）
            check_result = check_local_or_password()
            if check_result:
                return check_result

            data = request.get_json()
            if data:
                manager.save_settings(data)
            return jsonify({"success": True, "message": "设置已保存"})

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/compression/update", methods=["POST"])
@require_local_or_password
def update_compression_settings():
    """更新压缩设置并重新生成压缩文件"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"})

        data = request.get_json() or {}

        current_settings = manager.get_settings()
        settings_changed = (
            data.get("video_quality")
            and data.get("video_quality") != current_settings.get("video_quality")
            or data.get("image_quality")
            and data.get("image_quality") != current_settings.get("image_quality")
        )

        if settings_changed:
            manager.clear_compressed_files()
            print("🔄 设置已更改，清空现有压缩文件")

        if data:
            manager.save_settings(data)

        result = manager.compress_all()

        return jsonify(
            {
                "success": True,
                "message": "设置已保存，正在重新压缩所有文件"
                if settings_changed
                else "设置已保存",
                "data": result,
            }
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/compression/regenerate", methods=["POST"])
@require_local_or_password
def regenerate_compression():
    """重新生成所有压缩文件"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"})

        result = manager.regenerate_all()

        return jsonify(
            {"success": True, "message": "正在重新生成所有压缩文件", "data": result}
        )

    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/compression/check-ffmpeg")
def check_ffmpeg():
    """检查 FFmpeg 是否可用"""
    try:
        from video_compressor import get_compression_manager

        manager = get_compression_manager()

        if manager:
            available = manager.is_ffmpeg_available()
            return jsonify(
                {
                    "success": True,
                    "available": available,
                    "message": "FFmpeg 可用" if available else "FFmpeg 不可用",
                }
            )
        else:
            return jsonify(
                {"success": False, "available": False, "message": "压缩管理器未初始化"}
            )

    except Exception as e:
        return jsonify({"success": False, "available": False, "message": str(e)})


photo_index_rebuild_status = {"running": False, "result": None, "error": None}


@app.route("/api/photo-index/rebuild", methods=["POST"])
@require_local_or_password
def rebuild_photo_index():
    """重建素材索引（后台执行）"""
    global photo_index_rebuild_status

    if photo_index_rebuild_status["running"]:
        return jsonify({"success": False, "message": "索引重建正在进行中..."})

    def do_rebuild():
        global photo_index_rebuild_status
        try:
            from photo_index_manager import PhotoIndexManager

            media_folders = getattr(public_client, "media_folders", [])
            data_dir = getattr(public_client, "data_dir", None)

            print(f"[索引重建] media_folders: {media_folders}")
            print(f"[索引重建] data_dir: {data_dir}")

            if not data_dir:
                photo_index_rebuild_status = {
                    "running": False,
                    "result": None,
                    "error": "数据目录未初始化",
                }
                return

            if not media_folders:
                photo_index_rebuild_status = {
                    "running": False,
                    "result": None,
                    "error": "未设置媒体文件夹，请先在设置页面添加媒体文件夹",
                }
                return

            manager = PhotoIndexManager(data_dir, media_folders)
            manager.index = {"photos": {}, "videos": {}}
            manager._save_index()

            result = manager.scan_source_folders()
            photo_index_rebuild_status = {
                "running": False,
                "result": result,
                "error": None,
            }
            print(f"✅ 索引重建完成: {result}")

        except Exception as e:
            photo_index_rebuild_status = {
                "running": False,
                "result": None,
                "error": str(e),
            }
            print(f"❌ 索引重建失败: {e}")

    photo_index_rebuild_status = {"running": True, "result": None, "error": None}
    threading.Thread(target=do_rebuild, daemon=True).start()

    return jsonify(
        {"success": True, "message": "索引重建已开始，请稍后刷新页面查看结果"}
    )


@app.route("/api/photo-index/status")
def photo_index_status():
    """获取索引重建状态"""
    return jsonify(photo_index_rebuild_status)


@app.route("/video/compressed/<filename>")
def get_compressed_video(filename):
    """获取压缩后的视频（用于自动降级）"""
    try:
        from video_compressor import get_compression_manager
        from photo_manager import PhotoManager

        manager = get_compression_manager()
        media_folders = getattr(public_client, "media_folders", [])

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"}), 404

        if media_folders:
            pm = PhotoManager(media_folders, public_client.data_dir)
            entry = pm.get_photo_by_filename(filename)

            if entry and Path(entry["path"]).exists():
                source_path = Path(entry["path"])
                compressed_path = manager.get_compressed_file_path(
                    source_path, filename
                )

                if compressed_path and compressed_path.exists():
                    return send_file(
                        compressed_path,
                        mimetype="video/mp4",
                        conditional=True,
                        download_name=filename,
                    )

        return jsonify({"success": False, "message": "压缩视频不存在"}), 404

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


@app.route("/image/compressed/<filename>")
def get_compressed_image(filename):
    """获取压缩后的图片（用于自动降级）"""
    try:
        from video_compressor import get_compression_manager
        from photo_manager import PhotoManager

        manager = get_compression_manager()
        media_folders = getattr(public_client, "media_folders", [])

        if not manager:
            return jsonify({"success": False, "message": "压缩管理器未初始化"}), 404

        if media_folders:
            pm = PhotoManager(media_folders, public_client.data_dir)
            entry = pm.get_photo_by_filename(filename)

            if entry and Path(entry["path"]).exists():
                source_path = Path(entry["path"])
                compressed_path = manager.get_compressed_file_path(
                    source_path, filename
                )

                if compressed_path and compressed_path.exists():
                    ext = Path(filename).suffix.lower()
                    mime_type = (
                        "image/jpeg" if ext in [".jpg", ".jpeg"] else f"image/{ext[1:]}"
                    )

                    return send_file(compressed_path, mimetype=mime_type)

        return jsonify({"success": False, "message": "压缩图片不存在"}), 404

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500


MATERIAL_PROCESS_STATUS = {
    "running": False,
    "last_run": None,
    "last_result": None,
}

ERROR_REPORT_QUEUE = []
REPORTED_ERRORS = set()
MAX_ERROR_QUEUE_SIZE = 10


def report_error(error_type: str, message: str, details: str = ""):
    """添加错误到上报队列（去重）"""
    error_key = f"{error_type}:{message[:100]}"
    if error_key in REPORTED_ERRORS:
        return

    REPORTED_ERRORS.add(error_key)
    ERROR_REPORT_QUEUE.append(
        {
            "type": error_type,
            "message": message[:200],
            "details": details[:500],
            "time": datetime.now().isoformat(),
        }
    )

    if len(ERROR_REPORT_QUEUE) > MAX_ERROR_QUEUE_SIZE:
        ERROR_REPORT_QUEUE.pop(0)


def auto_process_and_upload_materials(target_date: str = None):
    """
    自动处理当天素材并上传到服务端
    包括：照片分析 + 视频语音识别
    """
    import time
    from datetime import datetime
    from pathlib import Path

    global MATERIAL_PROCESS_STATUS

    if MATERIAL_PROCESS_STATUS["running"]:
        print("[素材处理] 已有任务在运行，跳过")
        return

    MATERIAL_PROCESS_STATUS["running"] = True

    try:
        if not target_date:
            target_date = datetime.now().strftime("%Y-%m-%d")

        print(f"[素材处理] 开始处理 {target_date} 的素材...")

        client_id = getattr(public_client, "client_id", "")
        if not client_id:
            print("[素材处理] 未配置 client_id，跳过")
            return

        media_folders = getattr(public_client, "media_folders", [])
        if not media_folders:
            print("[素材处理] 未配置媒体文件夹，跳过")
            return

        from photo_manager import PhotoManager

        pm = PhotoManager(media_folders, public_client.data_dir)
        all_media = pm.get_photos_by_date(target_date)

        if not all_media:
            print(f"[素材处理] {target_date} 没有素材，跳过")
            MATERIAL_PROCESS_STATUS["last_result"] = {
                "success": True,
                "message": "没有素材",
            }
            return

        photos = [m for m in all_media if not m.get("is_video", False)]
        videos = [m for m in all_media if m.get("is_video", False)]

        print(f"[素材处理] 找到 {len(photos)} 张照片, {len(videos)} 个视频")

        result = {"photos": 0, "videos": 0, "errors": []}

        # 处理照片（最多10张）
        if photos:
            try:
                from select_best_photo import analyze_all_photos

                photo_paths = [
                    p["path"] for p in photos[:15] if Path(p.get("path", "")).exists()
                ]

                if photo_paths:
                    print(f"[素材处理] 分析 {len(photo_paths)} 张照片...")
                    step_start = time.time()
                    photo_analysis = analyze_all_photos(
                        photo_paths,
                        max_photos=10,
                        client_id=client_id,
                        date=target_date,
                    )
                    print(
                        f"[素材处理] 照片分析完成，耗时 {time.time() - step_start:.1f}s"
                    )

                    upload_resp = public_client.signed_request(
                        "POST",
                        f"{public_client.server_url}/czrz/photos/analysis",
                        json={
                            "client_id": client_id,
                            "date": target_date,
                            "analysis": photo_analysis,
                        },
                        timeout=1200,
                    )
                    if upload_resp.status_code == 200:
                        result["photos"] = len(photo_analysis.get("photos", []))
                        print(f"[素材处理] 照片分析已上传")

            except Exception as e:
                result["errors"].append(f"照片处理失败: {e}")
                print(f"[素材处理] 照片处理失败: {e}")
                report_error("material_photo", f"照片处理失败", str(e))

        # 处理视频语音（最多5个，按时长排序）
        if videos:
            try:
                from video_audio_processor import (
                    process_video_speech,
                    analyze_language_ability,
                )

                video_results = []

                for video in videos:
                    video_path = Path(video.get("path", ""))
                    if not video_path.exists():
                        continue

                    try:
                        step_start = time.time()
                        speech_result = process_video_speech(
                            video_path, max_duration=60
                        )
                        print(
                            f"[素材处理] 视频处理耗时 {time.time() - step_start:.1f}s"
                        )

                        if speech_result.get("success") and speech_result.get(
                            "transcript"
                        ):
                            video_results.append(
                                {
                                    "path": str(video_path),
                                    "transcript": speech_result["transcript"],
                                    "duration": speech_result.get("duration", 0),
                                }
                            )
                    except Exception as e:
                        print(f"[素材处理] 视频处理失败 {video_path}: {e}")

                # 按时长排序，取前5个
                if video_results:
                    video_results.sort(key=lambda x: x.get("duration", 0), reverse=True)
                    video_results = video_results[:5]

                    baby_info = getattr(public_client, "baby_info", {}) or {}
                    baby_name = baby_info.get("baby_name", "宝宝")
                    age_months = baby_info.get("age_months", 0)

                    for vr in video_results:
                        analysis = analyze_language_ability(
                            vr["transcript"], baby_name=baby_name, age_months=age_months
                        )

                        upload_resp = public_client.signed_request(
                            "POST",
                            f"{public_client.server_url}/czrz/speech/record",
                            json={
                                "client_id": client_id,
                                "date": target_date,
                                "video_path": vr["path"],
                                "transcript": vr["transcript"],
                                "duration": vr["duration"],
                                "analysis": analysis,
                            },
                            timeout=30,
                        )
                        if upload_resp.status_code == 200:
                            result["videos"] += 1

                    print(f"[素材处理] 语音记录已上传 {result['videos']} 条")

            except Exception as e:
                result["errors"].append(f"视频处理失败: {e}")
                print(f"[素材处理] 视频处理失败: {e}")
                report_error("material_video", f"视频处理失败", str(e))

        MATERIAL_PROCESS_STATUS["last_result"] = result
        print(f"[素材处理] 完成: 照片 {result['photos']}, 视频 {result['videos']}")

    except Exception as e:
        print(f"[素材处理] 失败: {e}")
        MATERIAL_PROCESS_STATUS["last_result"] = {"success": False, "error": str(e)}
        report_error("material_process", f"素材处理失败", str(e))

    finally:
        MATERIAL_PROCESS_STATUS["running"] = False
        MATERIAL_PROCESS_STATUS["last_run"] = datetime.now().isoformat()


def material_process_scheduler():
    """素材处理定时任务：每天18:00自动处理"""
    import time
    from datetime import datetime, timedelta

    print("[素材处理定时] 已启动，将在每天 18:00 自动处理素材")

    while True:
        try:
            now = datetime.now()
            target_time = now.replace(hour=18, minute=0, second=0, microsecond=0)

            if now >= target_time:
                target_time += timedelta(days=1)

            wait_seconds = (target_time - now).total_seconds()
            print(
                f"[素材处理定时] 下次处理时间: {target_time}, 等待 {wait_seconds / 3600:.1f} 小时"
            )

            time.sleep(wait_seconds)

            print(f"[素材处理定时] {datetime.now()} 开始自动处理素材")
            auto_process_and_upload_materials()

        except Exception as e:
            print(f"[素材处理定时] 错误: {e}")
            time.sleep(3600)


def show_welcome_dialog():
    """显示欢迎对话框（支持 Windows/macOS/Linux 打包环境）"""
    # 只在打包环境下显示
    if not hasattr(sys, "_MEIPASS"):
        return

    title = "宝宝成长记录系统"

    message = """欢迎使用宝宝成长记录系统！

【系统简介】
这是一款专为记录宝宝成长点滴设计的智能系统，
支持照片管理、AI 生成成长日志、公网分享等功能。

【使用步骤】
1. 首次使用会自动打开设置向导页面
2. 填写宝宝信息（名字、生日、性别）
3. 选择照片所在的文件夹
4. 点击"完成设置"保存配置
5. 系统会自动打开主页，开始记录美好时光

【功能说明】
• 首页：查看今日照片和成长日志
• 画像：AI 生成的宝宝性格兴趣画像
• 日历：按日期浏览历史记录
• 设置：修改配置、查看配额

【提示】
• 首次运行会自动打开浏览器
• 如需公网访问，请确保网络正常
• 日志文件位于：文档/CZRZ/logs/

点击"确定"开始使用！"""

    try:
        if sys.platform == "win32":
            # Windows: 使用 Windows API MessageBox
            MB_OK = 0
            MB_ICONINFORMATION = 64
            MB_SETFOREGROUND = 0x10000
            ctypes.windll.user32.MessageBoxW(
                0, message, title, MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND
            )
        elif sys.platform == "darwin":
            # macOS: 使用 osascript (AppleScript)
            import subprocess

            # AppleScript 语法显示对话框
            applescript = f'display dialog "{message}" with title "{title}" buttons {{"确定"}} default button "确定" with icon note'
            subprocess.run(["osascript", "-e", applescript], check=False)
        else:
            # Linux: 使用 zenity (GNOME) 或 kdialog (KDE)
            import subprocess

            # 优先尝试 zenity
            try:
                subprocess.run(
                    ["zenity", "--info", f"--title={title}", f"--text={message}"],
                    check=False,
                    capture_output=True,
                )
            except FileNotFoundError:
                # 如果 zenity 不存在，尝试 kdialog
                try:
                    subprocess.run(
                        ["kdialog", f"--title={title}", f"--msgbox={message}"],
                        check=False,
                        capture_output=True,
                    )
                except FileNotFoundError:
                    # 都不存在，跳过对话框
                    print("提示: 安装 zenity 或 kdialog 可显示图形欢迎对话框")
                    print(message)
    except Exception as e:
        print(f"显示欢迎对话框失败: {e}")


if __name__ == "__main__":
    # 显示欢迎对话框（仅 Windows 打包环境）
    show_welcome_dialog()

    print("\n" + "=" * 60)
    print("  成长记录系统 - 公网客户端")
    print("=" * 60)
    print(f"  强制连接: {public_client.server_domain}")
    print("=" * 60)

    # 使用统一的端口查找方法（排除1024以下端口，范围3000-9000）
    port = public_client.find_available_port()

    if port:
        public_client.client_port = port

        print(f"\n🚀 启动服务器 (端口: {port})")
        print(f"🌐 本地访问: http://localhost:{port}")
        print(f"🌐 局域网访问: http://{public_client.get_local_ip()}:{port}")
        print(f"📡 监听地址: 0.0.0.0:{port} (所有网络接口)")

        # 先获取子域名信息（不启动 tunnel）
        if not public_client.is_first_run():
            # 获取子域名但不启动 tunnel（tunnel 在 Flask 启动后启动）
            if public_client.client_id:
                public_client.fetch_subdomain_only()

        if public_client.public_url:
            print(f"🔗 公网访问: {public_client.public_url}")

        print(f"🏢 管理后台: https://{public_client.server_domain}/admin")
        print(f"🔑 管理员密码: 请在服务端设置 ADMIN_PASSWORD 环境变量")
        print("=" * 60)

        # 打开浏览器
        try:
            import webbrowser

            webbrowser.open(f"http://localhost:{port}")
            print("✅ 浏览器已打开")
        except Exception as e:
            print(f"⚠️ 打开浏览器失败: {e}")

        # 启动后台心跳循环
        if public_client.client_id:
            public_client.start_heartbeat_loop()
            print("✅ 心跳循环已启动")

        # 初始化压缩管理器
        try:
            from video_compressor import (
                init_compression_manager,
                get_compression_manager,
            )

            media_folders = [
                Path(f)
                for f in getattr(public_client, "media_folders", [])
                if Path(f).exists()
            ]
            init_compression_manager(public_client.data_dir, media_folders)

            manager = get_compression_manager()
            if manager:
                manager.start_worker()
                # 启动时自动压缩已有文件
                if manager.is_ffmpeg_available():
                    manager.compress_all()
                print("✅ 压缩服务已启动")
        except Exception as e:
            print(f"⚠️ 压缩服务初始化失败: {e}")

        # 启动AI数据同步循环
        ai_config = CLIENT_CONFIG.get("ai_service", {})
        if ai_config.get("enabled", False) and ai_config.get("child_id", ""):
            public_client.start_ai_sync_loop()
            print("✅ AI数据同步循环已启动")

        # 启动素材处理定时任务
        if public_client.client_id:
            import threading

            material_thread = threading.Thread(
                target=material_process_scheduler, daemon=True
            )
            material_thread.start()
            print("✅ 素材处理定时任务已启动（每天 18:00）")

        # 启动 Tunnel（在后台线程中，等待 Flask 绑定端口后启动）
        def start_tunnel_after_flask():
            import time

            # 等待 Flask 启动（最多等待 5 秒）
            for _ in range(50):
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    result = sock.connect_ex(("127.0.0.1", port))
                    sock.close()
                    if result == 0:  # 端口已被绑定
                        break
                except:
                    pass
                time.sleep(0.1)

            # 启动 Tunnel
            if public_client._pending_tunnel_credentials:
                time.sleep(0.5)  # 再等一下确保 Flask 完全就绪
                public_client.start_cloudflare_tunnel(
                    public_client._pending_tunnel_credentials
                )
                public_client._pending_tunnel_credentials = None

        if public_client._pending_tunnel_credentials:
            tunnel_thread = threading.Thread(
                target=start_tunnel_after_flask, daemon=True
            )
            tunnel_thread.start()

        # 注册退出处理：确保退出时杀死 cloudflared 进程
        def cleanup_on_exit():
            if public_client.tunnel_active:
                print("\n🛑 正在停止 Cloudflare Tunnel...")
                public_client.stop_cloudflare_tunnel()

        atexit.register(cleanup_on_exit)

        # 信号处理：捕获 SIGTERM 和 SIGINT
        def signal_handler(signum, frame):
            print(f"\n收到信号 {signum}，正在退出...")
            cleanup_on_exit()
            sys.exit(0)

        signal.signal(signal.SIGTERM, signal_handler)
        signal.signal(signal.SIGINT, signal_handler)

        # 启动 Flask（threaded=True 支持多用户）
        try:
            app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
        except Exception as e:
            print(f"\n❌ 服务器启动失败: {e}")
            cleanup_on_exit()
            input("按回车键退出...")
    else:
        # 端口查找失败（find_available_port 内部已处理提示）
        pass
