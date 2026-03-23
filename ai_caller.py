"""
多厂商AI调用器
支持：阿里通义、DeepSeek、字节豆包
"""

import json
import httpx
from typing import Dict, Any, Optional, List
from model_manager import model_manager


class AICaller:
    """多厂商AI调用器"""

    def __init__(self):
        self.client = None

    async def _get_client(self):
        """获取HTTP客户端"""
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=600, verify=False)
        return self.client

    async def call_text_model(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 800,
        temperature: float = 0.8,
    ) -> Dict[str, Any]:
        """调用文本模型"""
        config = model_manager.get_text_config()
        provider = config.get("provider", "tongyi")
        api_token = config.get("api_token", "")
        api_url = config.get("api_url", "")
        model_name = config.get("model_name", "")

        if not api_token:
            raise Exception("未配置文本模型 API Token")
        if not model_name:
            raise Exception("未配置文本模型名称")

        # 根据厂商使用不同的payload格式
        if provider == "tongyi":
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        elif provider == "deepseek":
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
        elif provider == "doubao":
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        else:
            raise Exception(f"不支持的文本模型厂商: {provider}")

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        try:
            client = await self._get_client()
            response = await client.post(api_url, json=payload, headers=headers)
            if response.status_code != 200:
                raise Exception(
                    f"文本模型调用失败: {response.status_code} - {response.text}"
                )

            result = response.json()
            usage = result.get("usage", {})

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                return {
                    "content": content,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                }
            elif "output" in result:
                content = (
                    result["output"]["text"]
                    if isinstance(result["output"], dict)
                    else result["output"]
                )
                return {
                    "content": content,
                    "usage": {
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                }
            else:
                raise Exception(f"未知的响应格式: {result}")
        except Exception as e:
            raise Exception(f"文本模型调用异常: {str(e)}")

    async def call_vision_model(
        self, messages: List[Dict[str, Any]], max_tokens: int = 2000
    ) -> Dict[str, Any]:
        """调用视觉模型"""
        config = model_manager.get_vision_config()
        provider = config.get("provider", "tongyi")
        api_token = config.get("api_token", "")
        api_url = config.get("api_url", "")
        model_name = config.get("model_name", "")

        if not api_token:
            raise Exception("未配置视觉模型 API Token")
        if not model_name:
            raise Exception("未配置视觉模型名称")

        # 视觉模型的payload格式相对统一（OpenAI兼容格式）
        payload = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        try:
            client = await self._get_client()
            response = await client.post(
                api_url, timeout=600, json=payload, headers=headers
            )
            if response.status_code != 200:
                raise Exception(
                    f"视觉模型调用失败: {response.status_code} - {response.text}"
                )

            result = response.json()
            usage = result.get("usage", {})

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                return {
                    "content": content,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                }
            elif "output" in result:
                content = (
                    result["output"]["text"]
                    if isinstance(result["output"], dict)
                    else result["output"]
                )
                return {
                    "content": content,
                    "usage": {
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                }
            else:
                raise Exception(f"未知的响应格式: {result}")
        except Exception as e:
            raise Exception(f"视觉模型调用异常: {str(e)}")

    async def call_speech_model(
        self, audio_data: bytes, audio_format: str = "wav", sample_rate: int = 48000
    ) -> Dict[str, Any]:
        """调用语音识别模型（WebSocket方式）"""
        import asyncio

        config = model_manager.get_speech_config()
        provider = config.get("provider", "tongyi")
        api_token = config.get("api_token", "")
        model_name = config.get("model_name", "")

        if not api_token:
            raise Exception("未配置语音模型 API Token")
        if not model_name:
            raise Exception("未配置语音模型名称")

        if provider != "tongyi":
            raise Exception(f"语音识别暂只支持阿里通义，当前厂商: {provider}")

        return await asyncio.get_event_loop().run_in_executor(
            None, self._call_speech_sync, api_token, model_name, audio_data, sample_rate
        )

    def _call_speech_sync(
        self, api_token: str, model_name: str, audio_data: bytes, sample_rate: int
    ) -> Dict[str, Any]:
        """同步调用语音识别（Recognition.call 方式）"""
        import tempfile
        import dashscope
        from http import HTTPStatus
        from dashscope.audio.asr import Recognition

        dashscope.api_key = api_token

        # 写入临时文件
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            temp_path = f.name

        try:
            recognition = Recognition(
                model=model_name,
                format="wav",
                sample_rate=sample_rate,
                callback=None,
            )

            result = recognition.call(temp_path)

            if result.status_code == HTTPStatus.OK:
                sentences = result.get_sentence()
                text = (
                    "".join(s.get("text", "") for s in sentences) if sentences else ""
                )
                return {
                    "content": text,
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": len(text) // 4,
                        "total_tokens": len(text) // 4,
                    },
                }
            else:
                raise Exception(f"语音识别失败: {result.message}")
        finally:
            import os

            os.unlink(temp_path)

    async def call_profile_model(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = 3000,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """调用画像计算模型"""
        config = model_manager.get_profile_config()
        provider = config.get("provider", "tongyi")
        api_token = config.get("api_token", "")
        api_url = config.get("api_url", "")
        model_name = config.get("model_name", "")

        if not api_token:
            raise Exception("未配置画像模型 API Token")
        if not model_name:
            raise Exception("未配置画像模型名称")

        # 画像模型的payload与文本模型类似
        if provider == "tongyi":
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        elif provider == "deepseek":
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
        elif provider == "doubao":
            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        else:
            raise Exception(f"不支持的画像模型厂商: {provider}")

        headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

        try:
            client = await self._get_client()
            response = await client.post(
                api_url, timeout=600, json=payload, headers=headers
            )
            if response.status_code != 200:
                raise Exception(
                    f"画像模型调用失败: {response.status_code} - {response.text}"
                )

            result = response.json()
            usage = result.get("usage", {})

            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0]["message"]["content"]
                return {
                    "content": content,
                    "usage": {
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                }
            elif "output" in result:
                content = (
                    result["output"]["text"]
                    if isinstance(result["output"], dict)
                    else result["output"]
                )
                return {
                    "content": content,
                    "usage": {
                        "prompt_tokens": usage.get("input_tokens", 0),
                        "completion_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    },
                }
            else:
                raise Exception(f"未知的响应格式: {result}")
        except Exception as e:
            raise Exception(f"画像模型调用异常: {str(e)}")

    async def close(self):
        """关闭HTTP客户端"""
        if self.client:
            await self.client.aclose()
            self.client = None


# 全局实例
ai_caller = AICaller()
