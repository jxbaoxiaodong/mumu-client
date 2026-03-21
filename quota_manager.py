"""
额度管理器 - 记录和管理AI功能的使用额度
仅保留数据存储功能，配额检查已迁移到 server_public.py 的 token 配额系统
"""

import json
import sys
from pathlib import Path


def get_data_dir():
    """获取数据目录（兼容 PyInstaller 和普通运行）"""
    if hasattr(sys, "_MEIPASS"):
        data_dir = Path.home() / "Documents" / "CZRZ" / "data"
    else:
        data_dir = Path(__file__).parent / "data"

    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


DATA_DIR = get_data_dir()
QUOTA_FILE = DATA_DIR / "quota.json"


def load_quota_data():
    """加载额度数据"""
    if QUOTA_FILE.exists():
        with open(QUOTA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_quota_data(data):
    """保存额度数据"""
    with open(QUOTA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
