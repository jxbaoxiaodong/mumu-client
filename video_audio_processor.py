"""
视频语音处理模块
从视频中提取语音，进行语音识别，用于分析宝宝语言能力
"""

import json
import base64
import hashlib
import hmac
import time
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional, Any

import requests

requests.Session.trust_env = False


def _safe_json_response(resp, context: str) -> Dict[str, Any]:
    try:
        return resp.json()
    except ValueError:
        snippet = (resp.text or "").strip().replace("\n", " ")[:160]
        if resp.status_code >= 500:
            raise Exception(f"{context}服务端错误: HTTP {resp.status_code}")
        raise Exception(
            f"{context}返回非JSON响应: HTTP {resp.status_code}"
            + (f" {snippet}" if snippet else "")
        )


def _post_proxy_json(
    url: str,
    body_str: str,
    headers: Dict[str, str],
    *,
    timeout: int,
    context: str,
    retries: int = 2,
) -> Dict[str, Any]:
    last_error = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
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

        if resp.status_code in {502, 503, 504}:
            last_error = f"{context}服务端错误: HTTP {resp.status_code}"
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            raise Exception(last_error)

        return _safe_json_response(resp, context)

    raise Exception(last_error or f"{context}请求失败")


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


def get_client_config() -> Dict[str, str]:
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
    except Exception:
        pass
    return {"server_url": "", "client_id": "", "secret_key": ""}


def transcribe_via_proxy(
    audio_path: Path, server_url: str, client_id: str, secret_key: str = ""
) -> Dict[str, Any]:
    """
    通过服务端代理调用语音识别

    Args:
        audio_path: 音频文件路径
        server_url: 服务端地址
        client_id: 客户端ID
        secret_key: 客户端密钥

    Returns:
        识别结果 {"success": bool, "transcript": str, "error": str}
    """
    audio_base64 = audio_to_base64(audio_path)

    try:
        request_body = {
            "client_id": client_id,
            "audio_base64": audio_base64,
            "operation": "speech_recognition",
        }
        body_str = json.dumps(
            request_body,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        headers = {"Content-Type": "application/json", "User-Agent": "CZRZ-Client/2.0"}
        path = "/czrz/ai/proxy/speech"

        if client_id and secret_key:
            add_signature_headers(
                headers, client_id, secret_key, "POST", path, body_str
            )

        return _post_proxy_json(
            f"{server_url}/czrz/ai/proxy/speech",
            body_str,
            headers,
            timeout=300,
            context="语音代理",
        )
    except Exception as e:
        return {"success": False, "error": str(e)}


def analyze_language_via_proxy(
    transcript: str,
    baby_name: str,
    age_months: int,
    server_url: str,
    client_id: str,
    secret_key: str = "",
) -> Dict[str, Any]:
    """
    通过服务端代理调用LLM分析语言能力

    Args:
        transcript: 语音识别的文字
        baby_name: 宝宝名字
        age_months: 月龄
        server_url: 服务端地址
        client_id: 客户端ID
        secret_key: 客户端密钥

    Returns:
        分析结果
    """
    if not transcript or len(transcript) < 5:
        return {
            "vocabulary_level": "数据不足",
            "sentence_complexity": "数据不足",
            "pronunciation": "数据不足",
            "expression": "数据不足",
            "tags": [],
        }

    prompt = f"""分析以下宝宝的语言表达内容，评估语言发展情况。

宝宝信息：
- 姓名：{baby_name}
- 月龄：{age_months}个月

语音转写内容：
{transcript}

请分析并输出 JSON 格式：
{{
  "vocabulary_level": "词汇量评估（丰富/一般/较少/数据不足）",
  "sentence_complexity": "句子复杂度（能说完整句子/简单短语/单词/数据不足）",
  "pronunciation": "发音情况（清晰/基本清晰/需要关注/数据不足）",
  "expression": "表达能力（表达流畅/基本表达/需要引导/数据不足）",
  "tags": ["语言相关标签1", "标签2"],
  "summary": "一句话总结语言发展情况"
}}

注意：
1. 基于实际内容分析，不要臆测
2. 如果转写内容太少，标注"数据不足"
3. 标签要具体，如"会说叠词"、"能说短句"、"词汇丰富"等"""

    try:
        request_body = {
            "client_id": client_id,
            "prompt": prompt,
            "max_tokens": 500,
            "operation": "language_analysis",
        }
        body_str = json.dumps(
            request_body,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        headers = {"Content-Type": "application/json", "User-Agent": "CZRZ-Client/2.0"}
        path = "/czrz/ai/proxy/text"

        if client_id and secret_key:
            add_signature_headers(
                headers, client_id, secret_key, "POST", path, body_str
            )

        data = _post_proxy_json(
            f"{server_url}/czrz/ai/proxy/text",
            body_str,
            headers,
            timeout=180,
            context="文本代理",
        )
        if data.get("success"):
            content = data.get("result", {}).get("content", "")
            try:
                analysis = json.loads(content)
            except json.JSONDecodeError:
                import re

                match = re.search(r"\{[\s\S]*\}", content)
                if match:
                    analysis = json.loads(match.group())
                else:
                    analysis = {
                        "vocabulary_level": "解析失败",
                        "sentence_complexity": "解析失败",
                        "pronunciation": "解析失败",
                        "expression": "解析失败",
                        "tags": [],
                    }
            return analysis
        else:
            return {
                "vocabulary_level": "分析失败",
                "sentence_complexity": "分析失败",
                "pronunciation": "分析失败",
                "expression": "分析失败",
                "tags": [],
                "error": data.get("message") or data.get("error", "未知错误"),
            }
    except Exception as e:
        return {
            "vocabulary_level": "分析失败",
            "sentence_complexity": "分析失败",
            "pronunciation": "分析失败",
            "expression": "分析失败",
            "tags": [],
            "error": str(e),
        }


def extract_audio_from_video(
    video_path: Path, output_path: Optional[Path] = None
) -> Path:
    """
    从视频中提取音频

    Args:
        video_path: 视频文件路径
        output_path: 输出音频路径，默认为视频同目录下的 .wav 文件

    Returns:
        音频文件路径
    """
    import imageio_ffmpeg

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()

    if output_path is None:
        output_path = video_path.with_suffix(".wav")

    cmd = [
        ffmpeg_path,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "48000",
        "-ac",
        "1",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        stderr_lower = result.stderr.lower()
        if "no audio" in stderr_lower or "audio stream" in stderr_lower:
            print(f"[音频提取] 视频无音频轨道: {video_path}")
            raise Exception("视频无音频轨道")
        print(f"[音频提取] ffmpeg 错误: {result.stderr}")
        raise Exception(f"音频提取失败: {result.stderr}")

    return output_path


def remove_silence_and_trim(
    audio_path: Path,
    max_duration: int = 60,
    silence_thresh: int = -40,
    min_silence_len: int = 500,
) -> Optional[Path]:
    """
    去除静音片段并限制时长

    Args:
        audio_path: 音频文件路径
        max_duration: 最大时长（秒）
        silence_thresh: 静音阈值（dB）
        min_silence_len: 最小静音长度（毫秒）

    Returns:
        处理后的音频文件路径
    """
    from pydub import AudioSegment
    from pydub.silence import detect_nonsilent

    audio = AudioSegment.from_wav(str(audio_path))

    nonsilent_ranges = detect_nonsilent(
        audio,
        min_silence_len=min_silence_len,
        silence_thresh=silence_thresh,
        seek_step=100,
    )

    if nonsilent_ranges:
        speech_audio = AudioSegment.empty()
        for start, end in nonsilent_ranges:
            speech_audio += audio[start:end]
    else:
        print(f"[静音检测] 未检测到非静音片段")
        return None

    max_ms = max_duration * 1000
    if len(speech_audio) > max_ms:
        speech_audio = speech_audio[:max_ms]

    speech_audio.export(str(audio_path), format="wav")

    return audio_path


def audio_to_base64(audio_path: Path) -> str:
    """将音频文件转换为 Base64 编码"""
    with open(audio_path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def process_video_speech(video_path: Path, max_duration: int = 60) -> Dict[str, Any]:
    """
    处理视频语音：提取音频、去除静音、语音识别

    Args:
        video_path: 视频文件路径
        max_duration: 最大音频时长（秒）

    Returns:
        {
            "success": bool,
            "transcript": str,  # 识别的文字
            "duration": float,  # 音频时长（秒）
            "error": str,       # 错误信息
        }
    """
    temp_audio = None

    try:
        config = get_client_config()
        server_url = config.get("server_url", "")
        client_id = config.get("client_id", "")
        secret_key = config.get("secret_key", "")

        if not server_url or not client_id:
            return {
                "success": False,
                "error": "未配置服务端地址或客户端ID",
            }

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_audio = Path(f.name)

        print(f"[语音处理] 提取音频: {video_path}")
        extract_audio_from_video(video_path, temp_audio)

        print(f"[语音处理] 去除静音并限制时长: {max_duration}秒")
        result_audio = remove_silence_and_trim(temp_audio, max_duration=max_duration)

        if result_audio is None:
            return {
                "success": False,
                "error": "音频全是静音，未检测到有效语音",
            }

        from pydub import AudioSegment

        audio = AudioSegment.from_wav(str(temp_audio))
        duration = len(audio) / 1000.0
        print(f"[语音处理] 音频时长: {duration:.1f}秒")

        if duration < 0.5:
            return {
                "success": False,
                "error": "音频时长过短，未检测到有效语音",
            }

        print(f"[语音处理] 通过服务端代理调用语音识别 API")
        result = transcribe_via_proxy(temp_audio, server_url, client_id, secret_key)

        if not result.get("success"):
            return {
                "success": False,
                "error": result.get("error", "语音识别失败"),
            }

        transcript = result.get("transcript", "")

        if not transcript:
            print(f"[语音处理] API 返回空结果")

        return {
            "success": True,
            "transcript": transcript,
            "duration": duration,
        }

    except Exception as e:
        print(f"[语音处理] 错误: {e}")
        return {
            "success": False,
            "error": str(e),
        }

    finally:
        if temp_audio and temp_audio.exists():
            try:
                temp_audio.unlink()
            except Exception:
                pass


def analyze_language_ability(
    transcript: str, baby_name: str = "宝宝", age_months: int = 0
) -> Dict[str, Any]:
    """
    分析语言能力（通过服务端代理调用 LLM）

    Args:
        transcript: 语音识别的文字
        baby_name: 宝宝名字
        age_months: 月龄

    Returns:
        {
            "vocabulary_level": str,  # 词汇量水平
            "sentence_complexity": str,  # 句子复杂度
            "pronunciation": str,  # 发音情况
            "expression": str,  # 表达能力
            "tags": list,  # 语言相关标签
        }
    """
    if not transcript or len(transcript) < 5:
        return {
            "vocabulary_level": "数据不足",
            "sentence_complexity": "数据不足",
            "pronunciation": "数据不足",
            "expression": "数据不足",
            "tags": [],
        }

    config = get_client_config()
    server_url = config.get("server_url", "")
    client_id = config.get("client_id", "")
    secret_key = config.get("secret_key", "")

    if not server_url or not client_id:
        return {
            "vocabulary_level": "配置缺失",
            "sentence_complexity": "配置缺失",
            "pronunciation": "配置缺失",
            "expression": "配置缺失",
            "tags": [],
        }

    return analyze_language_via_proxy(
        transcript, baby_name, age_months, server_url, client_id, secret_key
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python video_audio_processor.py <视频文件路径>")
        sys.exit(1)

    video_path = Path(sys.argv[1])
    if not video_path.exists():
        print(f"文件不存在: {video_path}")
        sys.exit(1)

    result = process_video_speech(video_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["success"] and result.get("transcript"):
        print("\n=== 语言能力分析 ===")
        analysis = analyze_language_ability(result["transcript"])
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
