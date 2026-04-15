#!/usr/bin/env python3
"""
照片索引管理模块
- 使用索引机制管理照片，不重复存储
- 支持多个源文件夹
- 按日期索引，快速查询
- 上传后可触发后台压缩（由 video_compressor 模块处理）
"""

import os
import re
import json
import hashlib
import shutil
import subprocess
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from PIL import Image
from PIL.ExifTags import TAGS


class PhotoIndexManager:
    """
    照片索引管理器

    设计理念：
    - 用户设置一个或多个媒体文件夹（source folders）
    - 系统扫描这些文件夹，为每个文件创建索引（路径、日期、哈希等）
    - 按日期查询时，通过索引找到原文件路径，不复制文件
    - 新上传的文件保存到 original 文件夹，自动添加到索引
    - 删除时只删除索引条目，不删除原文件
    """

    def __init__(self, data_dir: Path, source_folders: List[str] = None):
        """
        初始化索引管理器

        Args:
            data_dir: 数据目录（存放索引文件）
            source_folders: 源媒体文件夹列表（用户设置的文件夹）
        """
        self.data_dir = Path(data_dir)
        self.index_dir = self.data_dir / "photo_index"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.original_dir = self.data_dir / "original"
        self.original_dir.mkdir(parents=True, exist_ok=True)

        # 索引文件路径
        self.index_file = self.index_dir / "photo_index.json"
        self.config_file = self.index_dir / "config.json"

        # 源文件夹列表
        self.source_folders = []
        if source_folders:
            for folder in source_folders:
                if folder and Path(folder).exists():
                    self.source_folders.append(Path(folder))

        # 加载索引和配置
        self.index = self._load_index()
        self.config = self._load_config()

        # 支持的文件格式
        self.image_extensions = {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
        }
        self.video_extensions = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv"}
        self.all_extensions = self.image_extensions | self.video_extensions
        self.sample_hash_bytes = 1024 * 1024
        self.partial_save_every = 24
        self.partial_save_interval_seconds = 2.5
        self._ffprobe_path = None

    def _load_index(self) -> Dict:
        """加载索引文件"""
        if self.index_file.exists():
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[WARN] 加载索引失败: {e}")
        return {
            "photos": {},
            "videos": {},
        }  # {hash: {path, date, filename, size, modified}}

    def _save_index(self):
        """保存索引文件"""
        try:
            with open(self.index_file, "w", encoding="utf-8") as f:
                json.dump(self.index, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] 保存索引失败: {e}")

    def _load_config(self) -> Dict:
        """加载配置文件"""
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {"source_folders": [], "last_scan": None}

    def _save_config(self):
        """保存配置文件"""
        try:
            self.config["source_folders"] = [str(f) for f in self.source_folders]
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[ERROR] 保存配置失败: {e}")

    def update_source_folders(self, folders: List[str]):
        """更新源文件夹列表"""
        self.source_folders = []
        for folder in folders:
            if folder and Path(folder).exists():
                self.source_folders.append(Path(folder))
        self._save_config()
        print(f"✅ 已更新源文件夹: {len(self.source_folders)} 个")

    def _calculate_hash(self, file_path: Path, fast: bool = False) -> str:
        """计算文件哈希（用于唯一标识）"""
        try:
            if fast:
                return self._calculate_sample_hash(file_path)

            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except:
            # 如果读取失败，使用文件路径+修改时间作为唯一标识
            stat = file_path.stat()
            return hashlib.md5(f"{file_path}_{stat.st_mtime}".encode()).hexdigest()

    def _calculate_sample_hash(self, file_path: Path) -> str:
        """为大文件生成抽样哈希，减少上传请求内的整文件读取时间"""
        stat = file_path.stat()
        sample_size = self.sample_hash_bytes
        hasher = hashlib.md5()
        hasher.update(str(file_path.suffix.lower()).encode("utf-8"))
        hasher.update(str(stat.st_size).encode("utf-8"))
        hasher.update(str(stat.st_mtime_ns).encode("utf-8"))

        with open(file_path, "rb") as f:
            hasher.update(f.read(sample_size))
            if stat.st_size > sample_size:
                tail_size = min(sample_size, stat.st_size)
                f.seek(max(stat.st_size - tail_size, 0))
                hasher.update(f.read(tail_size))

        return hasher.hexdigest()

    def _get_upload_dir(self) -> Path:
        """获取上传目录，权限不足时自动回退到 data/original"""
        if self.source_folders and len(self.source_folders) > 0:
            upload_dir = self.source_folders[0] / "new_upload"
        else:
            upload_dir = self.original_dir

        try:
            upload_dir.mkdir(parents=True, exist_ok=True)
            test_file = upload_dir / ".write_test"
            test_file.touch()
            test_file.unlink()
            return upload_dir
        except PermissionError:
            self.original_dir.mkdir(parents=True, exist_ok=True)
            return self.original_dir
        except Exception:
            upload_dir.mkdir(parents=True, exist_ok=True)
            return upload_dir

    def get_upload_target_path(self, original_filename: str) -> Path:
        """返回上传文件最终落盘路径"""
        return self._get_upload_dir() / original_filename

    def _extract_date_from_filename(self, filename: str) -> Optional[str]:
        """从文件名中提取日期"""
        name = Path(filename).stem
        patterns = [
            r"(\d{4})-(\d{2})-(\d{2})",  # 2024-03-07
            r"(\d{4})_(\d{2})_(\d{2})",  # 2024_03_07
            r"(\d{4})(\d{2})(\d{2})",  # 20240307
        ]

        for pattern in patterns:
            match = re.search(pattern, name)
            if match:
                year, month, day = match.groups()
                year, month, day = int(year), int(month), int(day)
                # 有效性检查：年份在2000-2030，月份1-12，日期1-31
                if 2000 <= year <= 2030 and 1 <= month <= 12 and 1 <= day <= 31:
                    try:
                        date_obj = datetime(year, month, day)
                        return date_obj.strftime("%Y-%m-%d")
                    except ValueError:
                        continue
        return None

    def _extract_image_metadata(
        self, image_path: Path
    ) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
        """一次打开图片，同时提取 EXIF 日期和分辨率。"""
        try:
            with Image.open(image_path) as image:
                width = int(getattr(image, "width", 0) or 0)
                height = int(getattr(image, "height", 0) or 0)
                exif = image._getexif()
                detected_date = None

                if exif:
                    for tag_id, value in exif.items():
                        tag = TAGS.get(tag_id, tag_id)
                        if tag in ["DateTime", "DateTimeOriginal"] and isinstance(
                            value, str
                        ):
                            try:
                                date_str = value.split()[0]
                                date_obj = datetime.strptime(date_str, "%Y:%m:%d")
                                detected_date = date_obj.strftime("%Y-%m-%d")
                                break
                            except Exception:
                                continue
            dimensions = (width, height) if width > 0 and height > 0 else None
            return detected_date, dimensions
        except:
            pass
        return None, None

    def _extract_date_from_exif(self, image_path: Path) -> Optional[str]:
        """从图片EXIF信息中提取日期"""
        date_str, _ = self._extract_image_metadata(image_path)
        return date_str

    def _get_ffprobe_path(self) -> Optional[str]:
        """获取 ffprobe 路径，用于读取视频分辨率。"""
        if self._ffprobe_path is not None:
            return self._ffprobe_path or None

        try:
            import imageio_ffmpeg

            ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
            ffmpeg_binary = Path(ffmpeg_path)
            ffprobe_name = (
                "ffprobe.exe"
                if ffmpeg_binary.name.lower().endswith(".exe")
                else "ffprobe"
            )
            ffprobe_path = ffmpeg_binary.with_name(ffprobe_name)
            if ffprobe_path.exists():
                self._ffprobe_path = str(ffprobe_path)
                return self._ffprobe_path
        except Exception:
            pass

        self._ffprobe_path = shutil.which("ffprobe") or ""
        return self._ffprobe_path or None

    def _get_video_dimensions(self, video_path: Path) -> Optional[Tuple[int, int]]:
        """读取视频分辨率。"""
        ffprobe_path = self._get_ffprobe_path()
        if not ffprobe_path:
            return None

        try:
            result = subprocess.run(
                [
                    ffprobe_path,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=p=0:s=x",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=12,
            )
            if result.returncode != 0:
                return None
            output = result.stdout.strip().splitlines()
            if not output:
                return None
            values = output[0].strip().lower().replace(",", "x").split("x")
            if len(values) < 2:
                return None
            width = int(values[0])
            height = int(values[1])
            if width > 0 and height > 0:
                return (width, height)
        except Exception:
            return None
        return None

    def _get_media_dimensions(
        self, file_path: Path, is_video: bool
    ) -> Optional[Tuple[int, int]]:
        if is_video:
            return self._get_video_dimensions(file_path)
        _, dimensions = self._extract_image_metadata(file_path)
        return dimensions

    def _build_scan_record(self, file_path: Path) -> Optional[Dict]:
        """构建扫描排序所需的轻量信息，优先让最新日期先进入索引。"""
        try:
            stat = file_path.stat()
        except Exception:
            return None

        sort_date_hint = self._extract_date_from_filename(file_path.name)
        if not sort_date_hint:
            sort_date_hint = datetime.fromtimestamp(stat.st_mtime).strftime(
                "%Y-%m-%d"
            )

        return {
            "path": file_path,
            "modified": stat.st_mtime,
            "sort_date_hint": sort_date_hint,
            "is_video": file_path.suffix.lower() in self.video_extensions,
        }

    def _get_file_date(self, file_path: Path) -> str:
        """获取文件日期（优先文件名，其次EXIF，最后修改时间）"""
        # 1. 文件名
        date = self._extract_date_from_filename(file_path.name)
        if date:
            return date

        # 2. EXIF（仅图片）
        if file_path.suffix.lower() in self.image_extensions:
            date = self._extract_date_from_exif(file_path)
            if date:
                return date

        # 3. 文件修改时间
        mtime = os.path.getmtime(file_path)
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")

    def scan_source_folders(
        self, progress_callback=None, start_date=None, end_date=None
    ):
        """
        扫描所有源文件夹，更新索引

        Args:
            progress_callback: 进度回调函数 (current, total, message)
            start_date: 起始日期字符串 (YYYY-MM-DD)，只保留此日期之后的索引
            end_date: 结束日期字符串 (YYYY-MM-DD)，只保留此日期之前的索引
        """
        print("🔍 开始扫描源文件夹...")
        if start_date or end_date:
            date_range = f"{start_date or '开始'} 至 {end_date or '结束'}"
            print(f"📅 将只保留日期范围内的索引: {date_range}")

        # 收集所有文件
        all_files = []
        for folder in self.source_folders:
            if not folder.exists():
                continue
            for root, dirs, files in os.walk(folder):
                # 跳过系统目录（避免重复索引）
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
                for filename in files:
                    file_path = Path(root) / filename
                    if file_path.suffix.lower() in self.all_extensions:
                        record = self._build_scan_record(file_path)
                        if record:
                            all_files.append(record)

        all_files.sort(
            key=lambda item: (
                item.get("sort_date_hint") or "",
                float(item.get("modified") or 0),
                item["path"].name,
            ),
            reverse=True,
        )

        total = len(all_files)
        print(f"📁 找到 {total} 个媒体文件")

        # 更新索引
        new_count = 0
        existing_count = 0
        last_partial_save_at = time.monotonic()

        for i, file_info in enumerate(all_files):
            file_path = file_info["path"]
            if progress_callback:
                progress_callback(i + 1, total, f"扫描中: {file_path.name}")

            try:
                stat = file_path.stat()
            except Exception:
                continue

            is_video = bool(file_info.get("is_video"))
            file_hash = self._calculate_hash(file_path)
            if is_video:
                file_date = self._get_file_date(file_path)
                dimensions = self._get_video_dimensions(file_path)
            else:
                image_date, dimensions = self._extract_image_metadata(file_path)
                file_date = (
                    self._extract_date_from_filename(file_path.name)
                    or image_date
                    or datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d")
                )
            target_key = "videos" if is_video else "photos"
            other_key = "photos" if is_video else "videos"
            path_str = str(file_path)
            width = int(dimensions[0]) if dimensions else 0
            height = int(dimensions[1]) if dimensions else 0

            entry = {
                "hash": file_hash,
                "path": path_str,
                "filename": file_path.name,
                "date": file_date,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "is_video": is_video,
                "width": width,
                "height": height,
            }

            # 同一路径文件若内容被改写，旧哈希条目必须先移除，否则会残留旧日期/旧体积。
            for key in ["photos", "videos"]:
                stale_hashes = [
                    h
                    for h, existing_entry in self.index[key].items()
                    if existing_entry.get("path") == path_str and h != file_hash
                ]
                for stale_hash in stale_hashes:
                    del self.index[key][stale_hash]

            if file_hash in self.index[target_key]:
                self.index[target_key][file_hash].update(entry)
                existing_count += 1
            elif file_hash in self.index[other_key]:
                del self.index[other_key][file_hash]
                self.index[target_key][file_hash] = entry
                existing_count += 1
            else:
                self.index[target_key][file_hash] = entry
                new_count += 1

            should_checkpoint = (
                (i + 1) % self.partial_save_every == 0
                or (time.monotonic() - last_partial_save_at)
                >= self.partial_save_interval_seconds
            )
            if should_checkpoint:
                self._save_index()
                last_partial_save_at = time.monotonic()

        # 清理不存在的文件索引
        self._cleanup_index()

        # 按路径去重（同一文件可能被重复索引，保留最新的）
        self._deduplicate_by_path()

        if start_date or end_date:
            removed_count = self._filter_index_by_date_range(start_date, end_date)
            if removed_count > 0:
                if start_date and not end_date:
                    print(f"🗑️ 已删除 {removed_count} 个旧索引条目（{start_date}之前）")
                else:
                    print(f"🗑️ 已删除日期范围外的 {removed_count} 个索引条目")

        # 保存索引
        self._save_index()
        self.config["last_scan"] = datetime.now().isoformat()
        self._save_config()

        print(f"✅ 扫描完成: 新增 {new_count} 个, 已有 {existing_count} 个")
        return {
            "new": new_count,
            "existing": existing_count,
            "total": len(self.index["photos"]) + len(self.index["videos"]),
        }

    def _cleanup_index(self):
        """清理不存在的文件索引"""
        removed = 0
        for key in ["photos", "videos"]:
            to_remove = []
            for file_hash, entry in self.index[key].items():
                if not Path(entry["path"]).exists():
                    to_remove.append(file_hash)

            for file_hash in to_remove:
                del self.index[key][file_hash]
                removed += 1

        if removed > 0:
            print(f"🗑️ 清理了 {removed} 个失效索引")

    def _deduplicate_by_path(self):
        """
        按路径去重：同一文件路径可能对应多个哈希（文件被修改过）
        保留 modified 时间最新的条目
        """
        from collections import defaultdict

        removed = 0
        for key in ["photos", "videos"]:
            # 按路径分组
            path_to_hashes = defaultdict(list)
            for file_hash, entry in self.index[key].items():
                path = entry.get("path")
                if path:
                    path_to_hashes[path].append((file_hash, entry))

            # 处理重复路径
            for path, entries in path_to_hashes.items():
                if len(entries) > 1:
                    # 按修改时间排序，保留最新的
                    entries.sort(key=lambda x: x[1].get("modified", 0), reverse=True)
                    # 删除旧的
                    for file_hash, entry in entries[1:]:
                        del self.index[key][file_hash]
                        removed += 1
                        print(f"🔄 去重: {Path(path).name} (保留最新)")

        if removed > 0:
            print(f"🔄 路径去重完成: 清理了 {removed} 个重复条目")

    def _filter_index_by_date_range(self, start_date=None, end_date=None):
        """
        根据日期范围过滤索引，只保留范围内的条目

        Args:
            start_date: 起始日期字符串 (YYYY-MM-DD)
            end_date: 结束日期字符串 (YYYY-MM-DD)

        Returns:
            删除的条目数量
        """
        removed = 0
        for key in ["photos", "videos"]:
            to_remove = []
            for file_hash, entry in self.index[key].items():
                file_date = entry.get("date", "")
                if not file_date:
                    continue

                # 检查是否在范围内
                should_remove = False
                if start_date and file_date < start_date:
                    should_remove = True
                if end_date and file_date > end_date:
                    should_remove = True

                if should_remove:
                    to_remove.append(file_hash)

            for file_hash in to_remove:
                del self.index[key][file_hash]
                removed += 1

        return removed

    def add_uploaded_file(self, temp_path: Path, original_filename: str) -> Dict:
        """
        添加新上传的文件到索引

        Args:
            temp_path: 临时文件路径
            original_filename: 原始文件名

        Returns:
            索引条目信息
        """
        # 获取文件日期
        file_date = self._get_file_date(temp_path)

        return self._add_file_with_date(temp_path, original_filename, file_date)

    def add_uploaded_file_with_date(
        self, temp_path: Path, original_filename: str, date: str = None
    ) -> Dict:
        """
        添加新上传的文件到索引，使用指定日期

        Args:
            temp_path: 临时文件路径
            original_filename: 原始文件名
            date: 指定的日期字符串 YYYY-MM-DD，为空则自动检测

        Returns:
            索引条目信息
        """
        # 使用指定日期或自动检测
        if date and self._is_valid_date(date):
            file_date = date
        else:
            file_date = self._get_file_date(temp_path)

        return self._add_file_with_date(temp_path, original_filename, file_date)

    def register_saved_upload_with_date(
        self,
        saved_path: Path,
        original_filename: str,
        date: str = None,
        fast_hash: bool = False,
    ) -> Dict:
        """
        为已直接保存到目标目录的上传文件建立索引，不再重复复制文件
        """
        if date and self._is_valid_date(date):
            file_date = date
        else:
            file_date = self._get_file_date(saved_path)

        return self._index_existing_file(
            saved_path, original_filename, file_date, fast_hash=fast_hash
        )

    def _is_valid_date(self, date_str: str) -> bool:
        """验证日期格式是否为 YYYY-MM-DD"""
        import re

        return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", date_str))

    def _add_file_with_date(
        self, temp_path: Path, original_filename: str, file_date: str
    ) -> Dict:
        """
        内部方法：添加文件到索引，使用指定日期

        Args:
            temp_path: 临时文件路径
            original_filename: 原始文件名
            file_date: 文件日期字符串 YYYY-MM-DD

        Returns:
            索引条目信息
        """
        target_path = self.get_upload_target_path(original_filename)

        # 直接复制源文件（不阻塞压缩）
        try:
            shutil.copy2(temp_path, target_path)
        except PermissionError as e:
            raise PermissionError(
                f"无法写入到 {target_path.parent}，请检查文件夹权限或选择其他保存位置"
            ) from e

        return self._index_existing_file(target_path, original_filename, file_date)

    def _index_existing_file(
        self,
        file_path: Path,
        original_filename: str,
        file_date: str,
        fast_hash: bool = False,
    ) -> Dict:
        """为已存在的媒体文件建立索引并触发后台压缩"""
        ext = Path(original_filename).suffix.lower()
        is_video = ext in self.video_extensions
        file_hash = self._calculate_hash(file_path, fast=fast_hash)
        target_path_str = str(file_path)

        for key in ["photos", "videos"]:
            to_remove = [
                h
                for h, e in self.index[key].items()
                if e.get("path") == target_path_str
            ]
            for h in to_remove:
                del self.index[key][h]
                print(f"🔄 替换旧索引: {original_filename}")

        entry = {
            "hash": file_hash,
            "path": target_path_str,
            "filename": original_filename,
            "date": file_date,
            "size": file_path.stat().st_size,
            "modified": file_path.stat().st_mtime,
            "is_video": is_video,
            "original_name": original_filename,
        }
        dimensions = self._get_media_dimensions(file_path, is_video)
        if dimensions:
            entry["width"] = int(dimensions[0])
            entry["height"] = int(dimensions[1])
        else:
            entry["width"] = 0
            entry["height"] = 0

        if is_video:
            self.index["videos"][file_hash] = entry
        else:
            self.index["photos"][file_hash] = entry

        self._save_index()

        hash_mode = "抽样哈希" if fast_hash else "完整哈希"
        print(
            f"✅ 文件已添加: {original_filename} (日期: {file_date}, 路径: {file_path}, {hash_mode})"
        )

        try:
            from video_compressor import get_compression_manager

            manager = get_compression_manager()
            if manager:
                file_type = "video" if is_video else "image"
                manager.add_to_queue(file_path, file_type)
        except Exception as e:
            print(f"⚠️ 添加到压缩队列失败: {e}")

        return entry

    def get_photos_by_date(self, date: str) -> List[Dict]:
        """
        获取指定日期的所有照片

        Args:
            date: 日期字符串 YYYY-MM-DD

        Returns:
            照片信息列表
        """
        result = []

        for key in ["photos", "videos"]:
            for file_hash, entry in self.index[key].items():
                if entry["date"] == date:
                    # 检查文件是否存在
                    if Path(entry["path"]).exists():
                        result.append(
                            {
                                "hash": file_hash,
                                "filename": entry["filename"],
                                "path": entry["path"],
                                "url": f"/photo/{entry['filename']}",
                                "date": date,
                                "is_video": entry.get("is_video", False),
                                "size": entry.get("size", 0),
                                "width": entry.get("width", 0),
                                "height": entry.get("height", 0),
                            }
                        )

        # 按文件名排序
        result.sort(key=lambda x: x["filename"])
        return result

    def get_all_dates(self) -> List[str]:
        """获取所有有照片的日期"""
        dates = set()
        for key in ["photos", "videos"]:
            for entry in self.index[key].values():
                dates.add(entry["date"])

        return sorted(list(dates), reverse=True)

    def get_dates_by_month(self, year: int, month: int) -> List[str]:
        """获取指定月份的所有有照片的日期 - 优化版本"""
        month_prefix = f"{year}-{month:02d}-"
        dates = set()
        for key in ["photos", "videos"]:
            for entry in self.index[key].values():
                date_str = entry["date"]
                if date_str.startswith(month_prefix):
                    dates.add(date_str)
        return sorted(list(dates), reverse=True)

    def get_photo_by_filename(self, filename: str) -> Optional[Dict]:
        """通过文件名获取照片信息"""
        for key in ["photos", "videos"]:
            for file_hash, entry in self.index[key].items():
                if entry["filename"] == filename:
                    return entry
        return None

    def get_photo_by_hash(self, file_hash: str) -> Optional[Dict]:
        """通过文件哈希获取照片信息"""
        for key in ["photos", "videos"]:
            if file_hash in self.index[key]:
                return self.index[key][file_hash]
        return None

    def remove_from_index(self, filename: str) -> bool:
        """
        从索引中移除文件（不删除实际文件）

        Args:
            filename: 文件名

        Returns:
            是否成功移除
        """
        for key in ["photos", "videos"]:
            to_remove = []
            for file_hash, entry in self.index[key].items():
                if entry["filename"] == filename:
                    to_remove.append(file_hash)

            for file_hash in to_remove:
                del self.index[key][file_hash]
                self._save_index()
                print(f"🗑️ 已从索引移除: {filename}")
                return True

        return False

    def remove_from_index_by_path(self, file_path: str) -> bool:
        """
        按完整路径从索引中移除文件（不删除实际文件）

        Args:
            file_path: 文件完整路径

        Returns:
            是否成功移除
        """
        target_path = str(file_path or "")
        if not target_path:
            return False

        removed = False
        for key in ["photos", "videos"]:
            to_remove = [
                file_hash
                for file_hash, entry in self.index[key].items()
                if entry.get("path") == target_path
            ]
            for file_hash in to_remove:
                del self.index[key][file_hash]
                removed = True

        if removed:
            self._save_index()
            print(f"🗑️ 已从索引按路径移除: {target_path}")
        return removed
