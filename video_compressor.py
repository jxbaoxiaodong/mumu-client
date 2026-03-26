#!/usr/bin/env python3
"""
视频和图片压缩服务模块
- 后台异步压缩，不阻塞主线程
- 支持多种压缩质量档位
- 不修改源文件，压缩版单独存储
"""

import os
import json
import subprocess
import threading
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class CompressionManager:
    """
    压缩管理器
    管理视频和图片的压缩任务队列
    """

    RESOLUTION_PRESETS = {
        "省流": {
            "video": {"max_width": 854, "bitrate": "1M"},
            "image": {"max_width": 1280, "quality": 70},
        },
        "均衡": {
            "video": {"max_width": 1280, "bitrate": "2M"},
            "image": {"max_width": 1920, "quality": 85},
        },
        "高清": {
            "video": {"max_width": 1920, "bitrate": "4M"},
            "image": {"max_width": 2560, "quality": 95},
        },
    }

    VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm"}
    IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}

    def __init__(self, data_dir: Path, source_folders: List[Path] = None):
        """
        初始化压缩管理器

        Args:
            data_dir: 数据目录
            source_folders: 媒体源文件夹列表
        """
        self.data_dir = Path(data_dir)
        self.source_folders = source_folders or []

        self.state_file = self.data_dir / "compression_state.json"
        self.settings_file = self.data_dir / "compression_settings.json"

        self.state = self._load_state()
        self.settings = self._load_settings()

        self._worker_thread = None
        self._stop_event = threading.Event()
        self._queue_lock = threading.Lock()

        self._ffmpeg_available = None
        self._ffmpeg_path = None

    def _load_state(self) -> Dict:
        """加载压缩状态"""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] 加载压缩状态失败: {e}")

        return {
            "video_compressed": [],
            "image_compressed": [],
            "queue": [],
            "current": None,
            "failed": [],
            "total_videos": 0,
            "total_images": 0,
        }

    def _save_state(self):
        """保存压缩状态"""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] 保存压缩状态失败: {e}")

    def _load_settings(self) -> Dict:
        """加载压缩设置"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] 加载压缩设置失败: {e}")

        return {"video_quality": "原图", "image_quality": "原图"}

    def _save_settings(self):
        """保存压缩设置"""
        try:
            with open(self.settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] 保存压缩设置失败: {e}")

    def get_settings(self) -> Dict:
        """获取当前压缩设置"""
        return self.settings.copy()

    def save_settings(self, settings: Dict):
        """保存压缩设置"""
        self.settings.update(settings)
        self._save_settings()

    def is_enabled(self) -> bool:
        """检查压缩是否启用（始终返回 True）"""
        return True

    def is_ffmpeg_available(self) -> bool:
        """检查 FFmpeg 是否可用"""
        if self._ffmpeg_available is not None:
            return self._ffmpeg_available

        try:
            import imageio_ffmpeg

            self._ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            result = subprocess.run(
                [self._ffmpeg_path, "-version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self._ffmpeg_available = result.returncode == 0
            if self._ffmpeg_available:
                print(f"✅ FFmpeg 可用: {self._ffmpeg_path}")
            return self._ffmpeg_available
        except Exception as e:
            print(f"⚠️ FFmpeg 不可用: {e}")
            self._ffmpeg_available = False
            return False

    def get_ffmpeg_path(self) -> Optional[str]:
        """获取 FFmpeg 路径"""
        if self._ffmpeg_path is None:
            self.is_ffmpeg_available()
        return self._ffmpeg_path

    def get_compressed_dir(self, source_folder: Path, file_type: str) -> Path:
        """
        获取压缩文件存储目录

        Args:
            source_folder: 源文件夹
            file_type: 'videos' 或 'images'

        Returns:
            压缩文件存储目录
        """
        compressed_dir = source_folder / "compressed" / file_type
        compressed_dir.mkdir(parents=True, exist_ok=True)
        return compressed_dir

    def get_compressed_path(self, source_path: Path, file_type: str) -> Path:
        """
        获取压缩文件的目标路径

        Args:
            source_path: 源文件路径
            file_type: 'videos' 或 'images'

        Returns:
            压缩文件的目标路径
        """
        for folder in self.source_folders:
            try:
                if folder in source_path.parents or source_path.parent == folder:
                    compressed_dir = self.get_compressed_dir(folder, file_type)
                    filename = (
                        source_path.stem + ".mp4"
                        if file_type == "videos"
                        else source_path.name
                    )
                    return compressed_dir / filename
            except:
                pass

        return source_path.parent / "compressed" / file_type / source_path.name

    def is_compressed(self, filename: str) -> Tuple[bool, str]:
        """
        检查文件是否已压缩

        Args:
            filename: 文件名

        Returns:
            (是否已压缩, 文件类型 'video'/'image'/'')
        """
        if filename in self.state["video_compressed"]:
            return True, "video"
        if filename in self.state["image_compressed"]:
            return True, "image"
        return False, ""

    def get_compressed_file_path(
        self, source_path: Path, filename: str
    ) -> Optional[Path]:
        """
        获取已压缩文件的路径

        Args:
            source_path: 源文件路径
            filename: 文件名

        Returns:
            压缩文件路径，不存在返回 None
        """
        ext = Path(filename).suffix.lower()

        if ext in self.VIDEO_EXTENSIONS:
            file_type = "videos"
        elif ext in self.IMAGE_EXTENSIONS:
            file_type = "images"
        else:
            return None

        compressed_path = self.get_compressed_path(source_path, file_type)
        if compressed_path.exists():
            return compressed_path
        return None

    def compress_video(self, input_path: Path, output_path: Path) -> bool:
        """
        压缩单个视频

        Args:
            input_path: 输入视频路径
            output_path: 输出视频路径

        Returns:
            是否成功
        """
        if not self.is_ffmpeg_available():
            print("⚠️ FFmpeg 不可用，跳过视频压缩")
            return False

        try:
            quality = self.settings.get("video_quality", "均衡")
            preset = self.RESOLUTION_PRESETS.get(
                quality, self.RESOLUTION_PRESETS["均衡"]
            )
            video_config = preset["video"]

            cmd = [
                self._ffmpeg_path,
                "-y",
                "-i",
                str(input_path),
                "-vf",
                f"scale='min({video_config['max_width']},iw)':-2",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-b:v",
                video_config["bitrate"],
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]

            print(f"🎬 正在压缩视频: {input_path.name}...")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)

            if result.returncode == 0 and output_path.exists():
                original_size = input_path.stat().st_size
                compressed_size = output_path.stat().st_size
                ratio = (
                    (1 - compressed_size / original_size) * 100
                    if original_size > 0
                    else 0
                )
                print(
                    f"✅ 视频压缩完成: {original_size / 1024 / 1024:.1f}MB → {compressed_size / 1024 / 1024:.1f}MB (节省 {ratio:.1f}%)"
                )
                return True
            else:
                print(
                    f"⚠️ 视频压缩失败: {result.stderr[:200] if result.stderr else '未知错误'}"
                )
                return False

        except subprocess.TimeoutExpired:
            print(f"⚠️ 视频压缩超时: {input_path.name}")
            return False
        except Exception as e:
            print(f"⚠️ 视频压缩出错: {e}")
            return False

    def compress_image(self, input_path: Path, output_path: Path) -> bool:
        """
        压缩单个图片

        Args:
            input_path: 输入图片路径
            output_path: 输出图片路径

        Returns:
            是否成功
        """
        try:
            from PIL import Image
            from PIL import ImageOps

            quality = self.settings.get("image_quality", "均衡")
            preset = self.RESOLUTION_PRESETS.get(
                quality, self.RESOLUTION_PRESETS["均衡"]
            )
            image_config = preset["image"]

            print(f"🖼️ 正在压缩图片: {input_path.name}...")

            with Image.open(input_path) as img:
                # 根据EXIF方向自动旋转（解决手机照片方向问题）
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass  # 没有EXIF或处理失败，保持原样

                if img.mode in ("RGBA", "P"):
                    img = img.convert("RGB")

                if img.width > image_config["max_width"]:
                    ratio = image_config["max_width"] / img.width
                    new_height = int(img.height * ratio)
                    img = img.resize(
                        (image_config["max_width"], new_height),
                        Image.Resampling.LANCZOS,
                    )

                output_path.parent.mkdir(parents=True, exist_ok=True)

                if output_path.suffix.lower() in [".jpg", ".jpeg"]:
                    img.save(
                        output_path,
                        "JPEG",
                        quality=image_config["quality"],
                        optimize=True,
                    )
                elif output_path.suffix.lower() == ".png":
                    img.save(output_path, "PNG", optimize=True)
                elif output_path.suffix.lower() == ".webp":
                    img.save(output_path, "WEBP", quality=image_config["quality"])
                else:
                    img.save(output_path, quality=image_config["quality"])

            original_size = input_path.stat().st_size
            compressed_size = output_path.stat().st_size
            ratio = (
                (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
            )
            print(
                f"✅ 图片压缩完成: {original_size / 1024:.1f}KB → {compressed_size / 1024:.1f}KB (节省 {ratio:.1f}%)"
            )
            return True

        except Exception as e:
            print(f"⚠️ 图片压缩出错: {e}")
            return False

    def add_to_queue(self, file_path: Path, file_type: str):
        """
        添加文件到压缩队列

        Args:
            file_path: 文件路径
            file_type: 'video' 或 'image'
        """
        # 检查设置，如果是原图则不压缩
        if file_type == "video" and self.settings.get("video_quality") == "原图":
            print(f"⏭️ 视频设置为原图，跳过压缩: {file_path.name}")
            return
        if file_type == "image" and self.settings.get("image_quality") == "原图":
            print(f"⏭️ 图片设置为原图，跳过压缩: {file_path.name}")
            return

        with self._queue_lock:
            filename = file_path.name
            item = {"path": str(file_path), "type": file_type, "filename": filename}

            if (
                item not in self.state["queue"]
                and filename not in self.state["video_compressed"]
                and filename not in self.state["image_compressed"]
            ):
                self.state["queue"].append(item)
                self._save_state()
                print(f"📥 已添加到压缩队列: {filename}")

    def start_worker(self):
        """启动后台压缩工作线程"""
        if self._worker_thread and self._worker_thread.is_alive():
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        print("🔄 压缩工作线程已启动")

    def stop_worker(self):
        """停止后台压缩工作线程"""
        self._stop_event.set()
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
        print("⏹️ 压缩工作线程已停止")

    def _worker_loop(self):
        """工作线程主循环"""
        while not self._stop_event.is_set():
            try:
                with self._queue_lock:
                    if self.state["queue"]:
                        item = self.state["queue"].pop(0)
                        self.state["current"] = item
                        self._save_state()
                    else:
                        item = None
                        self.state["current"] = None

                if item:
                    self._process_item(item)

                time.sleep(1)

            except Exception as e:
                print(f"[ERROR] 压缩工作线程出错: {e}")
                time.sleep(5)

    def _process_item(self, item: Dict):
        """处理单个压缩任务"""
        file_path = Path(item["path"])
        file_type = item["type"]
        filename = item["filename"]

        if not file_path.exists():
            print(f"⚠️ 文件不存在: {filename}")
            return

        if file_type == "video":
            output_path = self.get_compressed_path(file_path, "videos")
            success = self.compress_video(file_path, output_path)
            compressed_list = "video_compressed"
        else:
            output_path = self.get_compressed_path(file_path, "images")
            success = self.compress_image(file_path, output_path)
            compressed_list = "image_compressed"

        with self._queue_lock:
            if success:
                if filename not in self.state[compressed_list]:
                    self.state[compressed_list].append(filename)
            else:
                if filename not in self.state["failed"]:
                    self.state["failed"].append(filename)

            self.state["current"] = None
            self._save_state()

    def compress_all(self) -> Dict:
        """
        压缩所有未压缩的文件

        Returns:
            统计信息
        """
        if not self.is_ffmpeg_available():
            return {"success": False, "message": "FFmpeg 不可用"}

        videos_added = 0
        images_added = 0

        for folder in self.source_folders:
            if not folder.exists():
                continue

            for root, dirs, files in os.walk(folder):
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in ["compressed", "photo_index", "original", "thumbnails"]
                ]

                for filename in files:
                    file_path = Path(root) / filename
                    ext = Path(filename).suffix.lower()

                    if ext in self.VIDEO_EXTENSIONS:
                        if filename not in self.state["video_compressed"]:
                            self.add_to_queue(file_path, "video")
                            videos_added += 1

                    elif ext in self.IMAGE_EXTENSIONS:
                        if filename not in self.state["image_compressed"]:
                            self.add_to_queue(file_path, "image")
                            images_added += 1

        self.state["total_videos"] = len(self.state["video_compressed"]) + videos_added
        self.state["total_images"] = len(self.state["image_compressed"]) + images_added
        self._save_state()

        return {
            "success": True,
            "videos_added": videos_added,
            "images_added": images_added,
            "message": f"已添加 {videos_added} 个视频和 {images_added} 张图片到压缩队列",
        }

    def clear_compressed_files(self):
        """清空所有压缩文件"""
        for folder in self.source_folders:
            compressed_dir = folder / "compressed"
            if compressed_dir.exists():
                import shutil

                try:
                    shutil.rmtree(compressed_dir)
                    print(f"🗑️ 已删除压缩目录: {compressed_dir}")
                except Exception as e:
                    print(f"⚠️ 删除压缩目录失败: {e}")

        self.state["video_compressed"] = []
        self.state["image_compressed"] = []
        self.state["failed"] = []
        self._save_state()

    def regenerate_all(self) -> Dict:
        """
        重新生成所有压缩文件

        Returns:
            统计信息
        """
        self.clear_compressed_files()
        return self.compress_all()

    def get_status(self) -> Dict:
        """
        获取压缩状态

        Returns:
            状态信息
        """
        queue_length = len(self.state["queue"])
        current = self.state.get("current")

        return {
            "enabled": self.is_enabled(),
            "ffmpeg_available": self.is_ffmpeg_available(),
            "video_quality": self.settings.get("video_quality", "均衡"),
            "image_quality": self.settings.get("image_quality", "均衡"),
            "videos_compressed": len(self.state["video_compressed"]),
            "images_compressed": len(self.state["image_compressed"]),
            "total_videos": self.state.get("total_videos", 0),
            "total_images": self.state.get("total_images", 0),
            "queue_length": queue_length,
            "current_file": current.get("filename") if current else None,
            "failed_count": len(self.state["failed"]),
        }


# 全局实例
compression_manager: Optional[CompressionManager] = None


def init_compression_manager(
    data_dir: Path, source_folders: List[Path] = None
) -> CompressionManager:
    """
    初始化全局压缩管理器

    Args:
        data_dir: 数据目录
        source_folders: 媒体源文件夹列表

    Returns:
        CompressionManager 实例
    """
    global compression_manager
    compression_manager = CompressionManager(data_dir, source_folders)
    return compression_manager


def get_compression_manager() -> Optional[CompressionManager]:
    """获取全局压缩管理器"""
    return compression_manager
