#!/usr/bin/env python3
"""
Video story maker.

This script takes:
1. a source video file,
2. a JSON segment file, where each segment has text and optional timing,
3. generates narration with TTS,
4. burns stable ASS subtitles,
5. exports both horizontal and vertical versions.

The default workflow is intentionally simple:
- If a segment has `start` / `end`, that source window is used.
- If a segment has `duration`, that is used as the source window length.
- If neither is provided, the segment duration falls back to the TTS duration
  and the source video is consumed sequentially.

The final segment duration is the max of source window length and TTS duration,
so narration is never cut off. If the source footage is shorter than the voice,
the last frame is held.

Example segments JSON:
[
  {"text": "文本 1", "start": 0, "end": 3.5},
  {"text": "文本 2", "start": 3.5, "end": 7.2},
  {"text": "文本 3"}
]

Outputs:
- <output-dir>/final_horizontal.mp4
- <output-dir>/final_vertical.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_VOICES = (
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-XiaoyiNeural",
)

VOICE_PROFILES = {
    "auto": DEFAULT_VOICES[0],
    "warm": "zh-CN-XiaoxiaoNeural",
    "bright": "zh-CN-YunxiNeural",
    "deep": "zh-CN-YunyangNeural",
    "cartoon": "zh-CN-YunyangNeural",
    "soft": "zh-CN-XiaoyiNeural",
}

DEFAULT_FONT_CANDIDATES = (
    "Microsoft YaHei",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Source Han Sans SC",
    "WenQuanYi Micro Hei",
    "SimHei",
)


@dataclass
class Segment:
    index: int
    text: str
    start: Optional[float] = None
    end: Optional[float] = None
    duration: Optional[float] = None

    @property
    def explicit_source_duration(self) -> Optional[float]:
        if self.duration is not None:
            return max(0.0, float(self.duration))
        if self.start is not None and self.end is not None:
            return max(0.0, float(self.end) - float(self.start))
        return None


@dataclass
class VideoMeta:
    duration: float
    width: int
    height: int
    rotation: int = 0


def run_cmd(cmd: Sequence[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            f"{' '.join(cmd)}\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def ffprobe_exe() -> str:
    return "ffprobe"


def parse_time(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise TypeError(f"Unsupported time value: {value!r}")


def load_segments(path: Path) -> List[Segment]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        if "segments" in raw:
            raw = raw["segments"]
        elif "items" in raw:
            raw = raw["items"]
        else:
            raise ValueError("Segment JSON must contain `segments` or be a list")
    if not isinstance(raw, list):
        raise ValueError("Segment JSON must be a list")

    segments: List[Segment] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Segment #{idx} must be an object")
        text = str(item.get("text", "")).strip()
        if not text:
            raise ValueError(f"Segment #{idx} is missing `text`")
        segments.append(
            Segment(
                index=idx,
                text=text,
                start=parse_time(item.get("start")),
                end=parse_time(item.get("end")),
                duration=parse_time(item.get("duration")),
            )
        )
    return segments


def load_segments_from_text(path: Path) -> List[Segment]:
    lines = path.read_text(encoding="utf-8").splitlines()
    segments: List[Segment] = []
    for idx, raw_line in enumerate(lines, start=1):
        text = raw_line.strip()
        if not text:
            continue
        segments.append(Segment(index=len(segments) + 1, text=text))
    if not segments:
        raise ValueError(f"No non-empty lines found in text file: {path}")
    return segments


def probe_video(path: Path) -> VideoMeta:
    cmd = [
        ffprobe_exe(),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{result.stderr}")

    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    video_stream = next(
        (s for s in streams if s.get("codec_type") == "video"), None
    )
    if not video_stream:
        raise RuntimeError(f"No video stream found in {path}")

    fmt = data.get("format", {})
    duration = float(fmt.get("duration") or 0.0)
    if duration <= 0:
        raise RuntimeError(f"Could not read duration from {path}")

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Could not read dimensions from {path}")

    rotation = 0
    tags = video_stream.get("tags") or {}
    rotate_value = tags.get("rotate")
    if rotate_value is not None:
        try:
            rotation = int(rotate_value) % 360
        except Exception:
            rotation = 0

    return VideoMeta(duration=duration, width=width, height=height, rotation=rotation)


def escape_ass_text(text: str) -> str:
    text = text.replace("\\", r"\\")
    text = text.replace("{", r"\{").replace("}", r"\}")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\n", r"\N")
    return text


def display_width(ch: str) -> int:
    if ch == " ":
        return 1
    if unicodedata.east_asian_width(ch) in {"F", "W", "A"}:
        return 2
    return 1


def wrap_cn_text(text: str, max_units: int, max_lines: int = 2) -> str:
    """Wrap Chinese/ASCII mixed text with stable line width.

    The goal is to keep subtitles in a predictable region without relying on
    ffmpeg auto-wrap.
    """

    text = text.strip()
    if not text:
        return ""

    lines: List[str] = []
    current: List[str] = []
    current_units = 0

    def flush() -> None:
        nonlocal current, current_units
        if current:
            lines.append("".join(current).strip())
            current = []
            current_units = 0

    for ch in text:
        if ch == "\n":
            flush()
            continue
        unit = display_width(ch)
        if current_units + unit > max_units and current:
            flush()
            if len(lines) >= max_lines:
                break
        current.append(ch)
        current_units += unit

    flush()

    if not lines:
        return escape_ass_text(text)

    if len(lines) > max_lines:
        merged = lines[: max_lines - 1]
        merged.append("".join(lines[max_lines - 1 :]))
        lines = merged

    return r"\N".join(escape_ass_text(line) for line in lines)


def ass_time(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def select_font(font_name: Optional[str]) -> str:
    return font_name or DEFAULT_FONT_CANDIDATES[0]


def resolve_voice(voice: str, voice_profile: str) -> str:
    if voice:
        return voice
    return VOICE_PROFILES.get(voice_profile, DEFAULT_VOICES[0])


def make_ass(
    output_path: Path,
    segments: Sequence[Dict[str, Any]],
    width: int,
    height: int,
    font_name: str,
    font_size: int,
    margin_v: int,
    wrap_units: int,
) -> Path:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
Collisions: Normal
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H7F000000,1,0,0,0,100,100,0,0,1,3,0,2,80,80,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [header]
    for item in segments:
        text = wrap_cn_text(str(item["text"]), wrap_units, max_lines=2)
        if not text:
            continue
        lines.append(
            "Dialogue: 0,"
            f"{ass_time(item['start'])},"
            f"{ass_time(item['end'])},"
            "Default,,0,0,0,,"
            f"{text}\n"
        )

    output_path.write_text("".join(lines), encoding="utf-8-sig")
    return output_path


async def synthesize_tts(
    text: str,
    voice: str,
    out_path: Path,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> None:
    try:
        import edge_tts
    except Exception as exc:
        raise RuntimeError(
            "edge-tts is not installed. Install dependencies with pip install -r requirements.txt"
        ) from exc

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(out_path))


def audio_duration(path: Path) -> float:
    cmd = [
        ffprobe_exe(),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}:\n{result.stderr}")
    return float(result.stdout.strip())


def build_video_filter(
    layout: str,
    meta: VideoMeta,
    target_w: int,
    target_h: int,
    fps: int,
) -> str:
    rotation_filter = ""
    if meta.rotation == 90:
        rotation_filter = "transpose=1,"
    elif meta.rotation == 270:
        rotation_filter = "transpose=2,"
    elif meta.rotation == 180:
        rotation_filter = "hflip,vflip,"

    if layout == "horizontal":
        bg_scale = "1920:1080"
        fg_scale = "1920:1080"
        blur = "boxblur=18:1"
    else:
        bg_scale = "1080:1920"
        fg_scale = "1080:1920"
        blur = "boxblur=20:1"

    return (
        f"{rotation_filter}"
        f"split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={bg_scale}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},{blur}[bg];"
        f"[fgsrc]scale={fg_scale}:force_original_aspect_ratio=decrease,"
        f"format=rgba[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1:format=auto,"
        f"fps={fps},format=yuv420p"
    )


def escape_filter_path(path: Path) -> str:
    return path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")


def render_segment(
    source: Path,
    voice_audio: Path,
    ass_file: Path,
    output_file: Path,
    start: float,
    source_len: float,
    final_duration: float,
    layout: str,
    meta: VideoMeta,
    fps: int,
    crf: int,
    preset: str,
    audio_bitrate: str,
) -> None:
    ffmpeg = ffmpeg_exe()
    target_w, target_h = (1920, 1080) if layout == "horizontal" else (1080, 1920)
    pad_seconds = max(0.0, final_duration - source_len)
    video_filter = build_video_filter(layout, meta, target_w, target_h, fps)
    subtitles_path = escape_filter_path(ass_file)

    # Normalize voice audio so different TTS outputs stay consistent.
    audio_chain = "[1:a]volume=1.0,highpass=f=70,lowpass=f=12000,loudnorm=I=-16:TP=-1.5:LRA=11"
    if pad_seconds > 0:
        audio_chain += f",apad,atrim=duration={final_duration:.6f}"
    else:
        audio_chain += f",atrim=duration={final_duration:.6f}"
    audio_chain += "[a]"

    filter_graph = (
        f"[0:v]{video_filter},"
        f"subtitles='{subtitles_path}'[v];"
        f"{audio_chain}"
    )

    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{source_len:.6f}",
        "-i",
        str(source),
        "-i",
        str(voice_audio),
        "-filter_complex",
        filter_graph,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        preset,
        "-crf",
        str(crf),
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-ar",
        "48000",
        "-ac",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(output_file),
    ]

    # If the source is shorter than the target duration, the tpad filter extends
    # the image by cloning the last frame.
    if pad_seconds > 0:
        # Inject tpad right before subtitles so the blur/background stays stable.
        video_filter = build_video_filter(layout, meta, target_w, target_h, fps)
        video_filter = video_filter.replace(
            f",fps={fps},format=yuv420p",
            f",tpad=stop_mode=clone:stop_duration={pad_seconds:.6f},fps={fps},format=yuv420p",
        )
        filter_graph = (
            f"[0:v]{video_filter},"
            f"subtitles='{subtitles_path}'[v];"
            f"[1:a]apad,atrim=duration={final_duration:.6f}[a]"
        )
        cmd[cmd.index("-filter_complex") + 1] = filter_graph

    run_cmd(cmd)


def concat_segments(parts: Sequence[Path], output_file: Path) -> None:
    concat_list = output_file.with_suffix(".concat.txt")

    def quote_concat_path(path: Path) -> str:
        return path.resolve().as_posix().replace("'", r"'\''")

    content = "\n".join(f"file '{quote_concat_path(p)}'" for p in parts) + "\n"
    concat_list.write_text(content, encoding="utf-8")

    ffmpeg = ffmpeg_exe()
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(output_file),
    ]
    run_cmd(cmd)


def make_layout_exports(
    source: Path,
    segments: Sequence[Segment],
    out_dir: Path,
    layout: str,
    voice: str,
    rate: str,
    pitch: str,
    font_name: str,
    voice_profile: str,
    fps: int,
    crf: int,
    preset: str,
    audio_bitrate: str,
    keep_temp: bool,
) -> Path:
    meta = probe_video(source)
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_root = out_dir / f".tmp_{layout}"
    tmp_root.mkdir(parents=True, exist_ok=True)

    rendered_parts: List[Path] = []
    cursor = 0.0

    if layout == "horizontal":
        subtitle_font_size = 50
        margin_v = 84
        wrap_units = 28
    else:
        subtitle_font_size = 58
        margin_v = 190
        wrap_units = 16

    effective_voice = resolve_voice(voice, voice_profile)

    for segment in segments:
        voice_path = tmp_root / f"segment_{segment.index:03d}.mp3"
        ass_path = tmp_root / f"segment_{segment.index:03d}.ass"
        part_path = tmp_root / f"segment_{segment.index:03d}.mp4"

        asyncio.run(
            synthesize_tts(segment.text, effective_voice, voice_path, rate=rate, pitch=pitch)
        )
        tts_len = audio_duration(voice_path)

        if segment.start is not None and segment.end is not None:
            start = max(0.0, segment.start)
            requested_len = max(0.0, segment.end - segment.start)
        elif segment.duration is not None:
            start = cursor
            requested_len = max(0.0, segment.duration)
        else:
            start = cursor
            requested_len = tts_len

        available_len = min(requested_len, max(0.0, meta.duration - start))
        if available_len <= 0:
            raise RuntimeError(
                f"Segment #{segment.index} has no available source footage. "
                f"Start={start:.2f}s, source duration={meta.duration:.2f}s"
            )

        final_duration = max(requested_len, tts_len)
        if segment.start is None or segment.end is None:
            # Sequential mode should advance by the rendered duration so later
            # segments stay aligned with the actual export length.
            cursor = start + final_duration
        else:
            cursor = max(cursor, start + final_duration)

        make_ass(
            ass_path,
            [
                {
                    "text": segment.text,
                    "start": 0.0,
                    "end": final_duration,
                }
            ],
            width=1920 if layout == "horizontal" else 1080,
            height=1080 if layout == "horizontal" else 1920,
            font_name=font_name,
            font_size=subtitle_font_size,
            margin_v=margin_v,
            wrap_units=wrap_units,
        )

        render_segment(
            source=source,
            voice_audio=voice_path,
            ass_file=ass_path,
            output_file=part_path,
            start=start,
            source_len=available_len,
            final_duration=final_duration,
            layout=layout,
            meta=meta,
            fps=fps,
            crf=crf,
            preset=preset,
            audio_bitrate=audio_bitrate,
        )
        rendered_parts.append(part_path)

    final_name = f"final_{layout}.mp4"
    final_path = out_dir / final_name
    if len(rendered_parts) == 1:
        rendered_parts[0].replace(final_path)
    else:
        concat_segments(rendered_parts, final_path)

    if not keep_temp:
        for p in rendered_parts:
            try:
                p.unlink()
            except Exception:
                pass
        for p in tmp_root.glob("*"):
            try:
                p.unlink()
            except Exception:
                pass
        try:
            tmp_root.rmdir()
        except Exception:
            pass

    return final_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate polished horizontal and vertical videos with TTS narration and subtitles."
    )
    parser.add_argument("--source", required=True, help="Source video file")
    parser.add_argument(
        "--segments",
        default="",
        help="JSON file containing ordered segments",
    )
    parser.add_argument(
        "--text-file",
        default="",
        help="Plain text file, one narration segment per non-empty line",
    )
    parser.add_argument(
        "--output-dir",
        default="output_video_story",
        help="Directory for rendered files",
    )
    parser.add_argument(
        "--layout",
        choices=("horizontal", "vertical", "both"),
        default="both",
        help="Export layout(s)",
    )
    parser.add_argument(
        "--voice",
        default="",
        help="edge-tts voice name. Leave empty to use --voice-profile",
    )
    parser.add_argument(
        "--voice-profile",
        choices=tuple(VOICE_PROFILES.keys()),
        default="auto",
        help="Convenience preset for voice selection",
    )
    parser.add_argument(
        "--rate",
        default="+0%",
        help="edge-tts speaking rate, e.g. +10%% or -5%%",
    )
    parser.add_argument(
        "--pitch",
        default="+0Hz",
        help="edge-tts pitch, e.g. +2Hz or -2Hz",
    )
    parser.add_argument(
        "--font",
        default="",
        help="Font family for ASS subtitles. Default tries common CJK fonts.",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=30,
        help="Constant output frame rate",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=18,
        help="x264 CRF quality setting",
    )
    parser.add_argument(
        "--preset",
        default="slow",
        help="x264 preset",
    )
    parser.add_argument(
        "--audio-bitrate",
        default="192k",
        help="AAC audio bitrate",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep intermediate files",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    source = Path(args.source).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser().resolve()

    if not source.exists():
        raise SystemExit(f"Source video not found: {source}")

    if not args.segments and not args.text_file:
        raise SystemExit("Provide either --segments or --text-file")

    if args.text_file:
        text_path = Path(args.text_file).expanduser().resolve()
        if not text_path.exists():
            raise SystemExit(f"Text file not found: {text_path}")
        segments = load_segments_from_text(text_path)
    else:
        segments_path = Path(args.segments).expanduser().resolve()
        if not segments_path.exists():
            raise SystemExit(f"Segments file not found: {segments_path}")
        segments = load_segments(segments_path)

    font_name = select_font(args.font)
    voice = resolve_voice(args.voice, args.voice_profile)

    layouts = [args.layout] if args.layout != "both" else ["horizontal", "vertical"]

    outputs = []
    for layout in layouts:
        output = make_layout_exports(
            source=source,
            segments=segments,
            out_dir=out_dir,
            layout=layout,
            voice=voice,
            rate=args.rate,
            pitch=args.pitch,
            font_name=font_name,
            voice_profile=args.voice_profile,
            fps=args.fps,
            crf=args.crf,
            preset=args.preset,
            audio_bitrate=args.audio_bitrate,
            keep_temp=args.keep_temp,
        )
        outputs.append(output)

    print("Rendered:")
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
