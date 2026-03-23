"""
模型配置工具
统一读取和管理三个模型的配置
"""

import json
from pathlib import Path
from typing import Dict, Optional, Any


# 配置文件路径
API_CONFIG_FILE = Path(__file__).parent / "data" / "api_config.json"


def load_api_config() -> Dict[str, Any]:
    """加载API配置"""
    if not API_CONFIG_FILE.exists():
        return {
            "cf_api_token": "",
            "cf_account_id": "",
            "text_model": {
                "api_token": "",
                "api_url": "",
                "model_name": "",
                "description": "文本生成模型",
            },
            "vision_model": {
                "api_token": "",
                "api_url": "",
                "model_name": "",
                "description": "视觉模型",
            },
            "embedding_model": {
                "api_token": "",
                "api_url": "",
                "model_name": "",
                "description": "向量模型",
            },
            "speech_model": {
                "api_token": "",
                "api_url": "",
                "model_name": "",
                "description": "语音识别模型",
            },
        }

    try:
        with open(API_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] 加载配置失败: {e}")
        return {}


def save_api_config(config: Dict[str, Any]):
    """保存API配置"""
    try:
        with open(API_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] 保存配置失败: {e}")


def get_model_config(model_type: str) -> Dict[str, str]:
    """
    获取指定类型模型的配置

    Args:
        model_type: 模型类型，可选值: 'text', 'vision', 'embedding'

    Returns:
        {
            'api_token': 'xxx',
            'api_url': 'https://...',
            'model_name': 'qwen-max'
        }
    """
    config = load_api_config()

    # 根据类型获取配置
    key = f"{model_type}_model"
    model_config = config.get(key, {})

    return {
        "api_token": model_config.get("api_token", ""),
        "api_url": model_config.get("api_url", ""),
        "model_name": model_config.get("model_name", ""),
    }


def get_text_model_config() -> Dict[str, str]:
    """获取文本模型配置"""
    return get_model_config("text")


def get_vision_model_config() -> Dict[str, str]:
    """获取视觉模型配置"""
    return get_model_config("vision")


def get_embedding_model_config() -> Dict[str, str]:
    """获取向量模型配置"""
    return get_model_config("embedding")


def get_speech_model_config() -> Dict[str, str]:
    """获取语音识别模型配置"""
    return get_model_config("speech")


def get_model_for_task(task_type: str) -> str:
    """
    根据任务类型返回合适的模型名称

    Args:
        task_type: 任务类型
            - 'text_generation': 文本生成（日志、问答）
            - 'feature_analysis': 特征分析
            - 'photo_analysis': 照片分析
            - 'photo_selection': 照片优选
            - 'embedding': 文本向量化

    Returns:
        模型名称
    """
    task_model_mapping = {
        "text_generation": "text",
        "feature_analysis": "text",
        "photo_analysis": "vision",
        "photo_selection": "vision",
        "embedding": "embedding",
        "speech_recognition": "speech",
    }

    model_type = task_model_mapping.get(task_type, "text")
    config = get_model_config(model_type)

    return config.get("model_name", "")


# 测试代码
if __name__ == "__main__":
    print("=== 文本模型配置 ===")
    text_config = get_text_model_config()
    print(f"API URL: {text_config['api_url']}")
    print(f"Model: {text_config['model_name']}")

    print("\n=== 视觉模型配置 ===")
    vision_config = get_vision_model_config()
    print(f"API URL: {vision_config['api_url']}")
    print(f"Model: {vision_config['model_name']}")

    print("\n=== 向量模型配置 ===")
    embedding_config = get_embedding_model_config()
    print(f"API URL: {embedding_config['api_url']}")
    print(f"Model: {embedding_config['model_name']}")

    print("\n=== 语音识别模型配置 ===")
    speech_config = get_speech_model_config()
    print(f"API URL: {speech_config['api_url']}")
    print(f"Model: {speech_config['model_name']}")
