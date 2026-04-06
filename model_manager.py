"""
多厂商模型管理器
支持：阿里通义、DeepSeek、字节豆包
"""

import json
from pathlib import Path
from typing import Dict, Any, Optional


class ModelManager:
    """多厂商模型管理器"""

    def __init__(self):
        self.config_path = Path(__file__).parent / "data" / "model_config.json"
        self.config = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        if not self.config_path.exists():
            # 创建默认配置（不包含硬编码的模型名称）
            default_config = {
                "text_model": {
                    "provider": "tongyi",
                    "api_token": "",
                    "model_name": "",
                    "provider_configs": {
                        "tongyi": {
                            "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                            "description": "阿里通义千问文本模型",
                        },
                        "deepseek": {
                            "api_url": "https://api.deepseek.com/v1/chat/completions",
                            "description": "DeepSeek文本模型",
                        },
                        "doubao": {
                            "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                            "description": "字节豆包文本模型",
                        },
                    },
                },
                "vision_model": {
                    "provider": "tongyi",
                    "api_token": "",
                    "model_name": "",
                    "provider_configs": {
                        "tongyi": {
                            "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                            "description": "阿里通义千问视觉模型",
                        },
                        "doubao": {
                            "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                            "description": "字节豆包视觉模型",
                        },
                    },
                },
                "speech_model": {
                    "provider": "tongyi",
                    "api_token": "",
                    "model_name": "",
                    "provider_configs": {
                        "tongyi": {
                            "api_url": "https://dashscope.aliyuncs.com/api/v1/services/aigc/audio/asr/transcription",
                            "description": "阿里语音识别模型",
                        },
                        "doubao": {
                            "api_url": "https://ark.cn-beijing.volces.com/api/v3/audio/transcriptions",
                            "description": "字节豆包语音识别",
                        },
                    },
                },
                "profile_model": {
                    "provider": "tongyi",
                    "api_token": "",
                    "model_name": "",
                    "provider_configs": {
                        "tongyi": {
                            "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                            "description": "阿里通义千问画像计算模型",
                        },
                        "deepseek": {
                            "api_url": "https://api.deepseek.com/v1/chat/completions",
                            "description": "DeepSeek画像计算模型",
                        },
                        "doubao": {
                            "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                            "description": "字节豆包画像计算模型",
                        },
                    },
                },
            }
            self._save_config(default_config)
            return default_config

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                changed = False
                # 如果旧配置没有profile_model，添加它
                if "profile_model" not in config:
                    config["profile_model"] = {
                        "provider": "tongyi",
                        "api_token": "",
                        "model_name": "",
                        "provider_configs": {
                            "tongyi": {
                                "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
                                "description": "阿里通义千问画像计算模型",
                            },
                            "deepseek": {
                                "api_url": "https://api.deepseek.com/v1/chat/completions",
                                "description": "DeepSeek画像计算模型",
                            },
                            "doubao": {
                                "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
                                "description": "字节豆包画像计算模型",
                            },
                        },
                    }
                    changed = True
                # 向量模型链路已废弃，加载时主动清理历史残留配置。
                if config.pop("embedding_model", None) is not None:
                    changed = True
                if changed:
                    self._save_config(config)
                return config
        except Exception as e:
            print(f"[ModelManager] 加载配置失败: {e}")
            return {}

    def _save_config(self, config: Dict[str, Any]):
        """保存配置文件"""
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self.config = config
        except Exception as e:
            print(f"[ModelManager] 保存配置失败: {e}")

    def get_text_config(self) -> Dict[str, Any]:
        """获取文本模型配置"""
        text_cfg = self.config.get("text_model", {})
        provider = text_cfg.get("provider", "tongyi")
        provider_cfg = text_cfg.get("provider_configs", {}).get(provider, {})

        return {
            "provider": provider,
            "api_token": text_cfg.get("api_token", ""),
            "model_name": text_cfg.get("model_name", ""),
            "api_url": provider_cfg.get("api_url", ""),
            "description": provider_cfg.get("description", ""),
        }

    def get_vision_config(self) -> Dict[str, Any]:
        """获取视觉模型配置"""
        vision_cfg = self.config.get("vision_model", {})
        provider = vision_cfg.get("provider", "tongyi")
        provider_cfg = vision_cfg.get("provider_configs", {}).get(provider, {})

        return {
            "provider": provider,
            "api_token": vision_cfg.get("api_token", ""),
            "model_name": vision_cfg.get("model_name", ""),
            "api_url": provider_cfg.get("api_url", ""),
            "description": provider_cfg.get("description", ""),
        }

    def get_speech_config(self) -> Dict[str, Any]:
        """获取语音模型配置"""
        speech_cfg = self.config.get("speech_model", {})
        provider = speech_cfg.get("provider", "tongyi")
        provider_cfg = speech_cfg.get("provider_configs", {}).get(provider, {})

        return {
            "provider": provider,
            "api_token": speech_cfg.get("api_token", ""),
            "model_name": speech_cfg.get("model_name", ""),
            "api_url": provider_cfg.get("api_url", ""),
            "description": provider_cfg.get("description", ""),
        }

    def get_profile_config(self) -> Dict[str, Any]:
        """获取画像计算模型配置"""
        profile_cfg = self.config.get("profile_model", {})
        provider = profile_cfg.get("provider", "tongyi")
        provider_cfg = profile_cfg.get("provider_configs", {}).get(provider, {})

        return {
            "provider": provider,
            "api_token": profile_cfg.get("api_token", ""),
            "model_name": profile_cfg.get("model_name", ""),
            "api_url": provider_cfg.get("api_url", ""),
            "description": provider_cfg.get("description", ""),
        }

    def update_text_config(
        self, provider: str, api_token: str, model_name: str
    ) -> bool:
        """更新文本模型配置"""
        try:
            if "text_model" not in self.config:
                self.config["text_model"] = {}

            self.config["text_model"]["provider"] = provider
            self.config["text_model"]["api_token"] = api_token
            self.config["text_model"]["model_name"] = model_name

            self._save_config(self.config)
            return True
        except Exception as e:
            print(f"[ModelManager] 更新文本配置失败: {e}")
            return False

    def update_vision_config(
        self, provider: str, api_token: str, model_name: str
    ) -> bool:
        """更新视觉模型配置"""
        try:
            if "vision_model" not in self.config:
                self.config["vision_model"] = {}

            self.config["vision_model"]["provider"] = provider
            self.config["vision_model"]["api_token"] = api_token
            self.config["vision_model"]["model_name"] = model_name

            self._save_config(self.config)
            return True
        except Exception as e:
            print(f"[ModelManager] 更新视觉配置失败: {e}")
            return False

    def update_speech_config(
        self, provider: str, api_token: str, model_name: str
    ) -> bool:
        """更新语音模型配置"""
        try:
            if "speech_model" not in self.config:
                self.config["speech_model"] = {}

            self.config["speech_model"]["provider"] = provider
            self.config["speech_model"]["api_token"] = api_token
            self.config["speech_model"]["model_name"] = model_name

            self._save_config(self.config)
            return True
        except Exception as e:
            print(f"[ModelManager] 更新语音配置失败: {e}")
            return False

    def update_profile_config(
        self, provider: str, api_token: str, model_name: str
    ) -> bool:
        """更新画像计算模型配置"""
        try:
            if "profile_model" not in self.config:
                self.config["profile_model"] = {}

            self.config["profile_model"]["provider"] = provider
            self.config["profile_model"]["api_token"] = api_token
            self.config["profile_model"]["model_name"] = model_name

            self._save_config(self.config)
            return True
        except Exception as e:
            print(f"[ModelManager] 更新画像配置失败: {e}")
            return False

    def get_all_providers(self) -> Dict[str, Dict[str, list]]:
        """获取所有支持的厂商（仅作参考，实际模型名称由用户输入）"""
        return {
            "text": {
                "tongyi": [
                    "qwen-turbo",
                    "qwen-plus",
                    "qwen-max",
                    "qwen3.5-flash",
                    "qwen3.5-plus",
                    "qwen3-max",
                ],
                "deepseek": ["deepseek-chat", "deepseek-coder"],
                "doubao": ["ep-20250120002510-1x5l8", "ep-20250224000000-2x5l8"],
            },
            "vision": {
                "tongyi": ["qwen-vl-plus", "qwen3-vl-plus", "qvq-max"],
                "doubao": [
                    "ep-20250120002510-1x5l8-vision",
                    "ep-20250224000000-2x5l8-vision",
                ],
            },
            "speech": {
                "tongyi": ["paraformer-realtime-v2", "qwen3-asr-flash-realtime"],
                "doubao": [
                    "ep-20250120002510-1x5l8-asr",
                    "ep-20250224000000-2x5l8-asr",
                ],
            },
            "profile": {
                "tongyi": ["qwen3-max", "qwen3.5-plus"],
                "deepseek": ["deepseek-chat"],
                "doubao": ["ep-20250224000000-2x5l8"],
            },
        }

    def get_current_config(self) -> Dict[str, Any]:
        """获取当前所有配置"""
        return {
            "text_model": self.get_text_config(),
            "vision_model": self.get_vision_config(),
            "speech_model": self.get_speech_config(),
            "profile_model": self.get_profile_config(),
        }


# 全局实例
model_manager = ModelManager()
