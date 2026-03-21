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
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
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

    def _calculate_hash(self, file_path: Path) -> str:
        """计算文件哈希（用于唯一标识）"""
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except:
            # 如果读取失败，使用文件路径+修改时间作为唯一标识
            stat = file_path.stat()
            return hashlib.md5(f"{file_path}_{stat.st_mtime}".encode()).hexdigest()

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

    def _extract_date_from_exif(self, image_path: Path) -> Optional[str]:
        """从图片EXIF信息中提取日期"""
        try:
            image = Image.open(image_path)
            exif = image._getexif()

            if exif:
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, tag_id)
                    if tag in ["DateTime", "DateTimeOriginal"]:
                        date_str = value.split()[0]
                        date_obj = datetime.strptime(date_str, "%Y:%m:%d")
                        return date_obj.strftime("%Y-%m-%d")
        except:
            pass
        return None

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
                # 跳过 original 和 index 目录（避免重复索引）
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
                        all_files.append(file_path)

        total = len(all_files)
        print(f"📁 找到 {total} 个媒体文件")

        # 更新索引
        new_count = 0
        existing_count = 0

        for i, file_path in enumerate(all_files):
            if progress_callback:
                progress_callback(i + 1, total, f"扫描中: {file_path.name}")

            file_hash = self._calculate_hash(file_path)

            if file_hash in self.index["photos"] or file_hash in self.index["videos"]:
                # 已存在，更新路径（文件可能移动了）
                entry = self.index["photos"].get(file_hash) or self.index["videos"].get(
                    file_hash
                )
                if entry["path"] != str(file_path):
                    entry["path"] = str(file_path)
                existing_count += 1
            else:
                # 新文件，添加到索引
                file_date = self._get_file_date(file_path)
                is_video = file_path.suffix.lower() in self.video_extensions

                entry = {
                    "hash": file_hash,
                    "path": str(file_path),
                    "filename": file_path.name,
                    "date": file_date,
                    "size": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime,
                    "is_video": is_video,
                }

                if is_video:
                    self.index["videos"][file_hash] = entry
                else:
                    self.index["photos"][file_hash] = entry

                new_count += 1

        # 清理不存在的文件索引
        self._cleanup_index()

        # 如果只设置起始日期（不设置结束日期），先删除旧索引再扫描
        # 这样新加入的文件会自动被扫描到索引中
        if start_date and not end_date:
            # 先删除 start_date 之前的旧索引
            removed_count = self._filter_index_by_date_range(start_date, None)
            if removed_count > 0:
                print(f"🗑️ 已删除 {removed_count} 个旧索引条目（{start_date}之前）")
            # 重新扫描添加所有文件（包括 start_date 及之后的全部）
            start_date = None  # 扫描时不带日期限制，添加所有文件
            end_date = None
        elif start_date or end_date:
            # 旧逻辑：同时指定起始和结束日期时，只保留范围内的
            removed_count = self._filter_index_by_date_range(start_date, end_date)
            if removed_count > 0:
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
        # 生成唯一文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = Path(original_filename).suffix.lower()
        is_video = ext in self.video_extensions

        new_filename = f"{file_date}_{timestamp}{ext}"

        # 保存到第一个媒体文件夹的 new_upload 子目录
        if self.source_folders and len(self.source_folders) > 0:
            upload_dir = self.source_folders[0] / "new_upload"
        else:
            # 如果没有设置源文件夹，使用 data/original 作为备选
            upload_dir = self.original_dir

        upload_dir.mkdir(parents=True, exist_ok=True)
        target_path = upload_dir / new_filename

        import shutil

        # 直接复制源文件（不阻塞压缩）
        shutil.copy2(temp_path, target_path)

        # 计算哈希
        file_hash = self._calculate_hash(target_path)

        # 添加到索引
        entry = {
            "hash": file_hash,
            "path": str(target_path),
            "filename": new_filename,
            "date": file_date,
            "size": target_path.stat().st_size,
            "modified": target_path.stat().st_mtime,
            "is_video": is_video,
            "original_name": original_filename,
        }

        if is_video:
            self.index["videos"][file_hash] = entry
        else:
            self.index["photos"][file_hash] = entry

        self._save_index()

        print(f"✅ 文件已添加: {new_filename} (日期: {file_date}, 路径: {target_path})")

        # 触发后台压缩
        try:
            from video_compressor import get_compression_manager

            manager = get_compression_manager()
            if manager:
                file_type = "video" if is_video else "image"
                manager.add_to_queue(target_path, file_type)
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

    def get_stats(self) -> Dict:
        """获取统计信息"""
        photo_count = len(self.index["photos"])
        video_count = len(self.index["videos"])
        total_size = sum(e.get("size", 0) for e in self.index["photos"].values())
        total_size += sum(e.get("size", 0) for e in self.index["videos"].values())

        return {
            "photo_count": photo_count,
            "video_count": video_count,
            "total_files": photo_count + video_count,
            "total_size_mb": round(total_size / (1024 * 1024), 2),
            "dates_count": len(self.get_all_dates()),
            "source_folders": len(self.source_folders),
        }


# 兼容旧接口的适配器
class PhotoManagerAdapter:
    """兼容旧 PhotoManager 接口的适配器"""

    def __init__(self, media_folders: list, data_dir: Path = None):
        """
        初始化适配器

        Args:
            media_folders: 媒体文件夹列表
            data_dir: 数据目录，默认使用 ~/Documents/CZRZ/data
        """
        if data_dir is None:
            data_dir = Path.home() / "Documents" / "CZRZ" / "data"

        self.index_manager = PhotoIndexManager(data_dir, media_folders)

    def save_uploaded_photo(self, file_path: Path, filename: str = None) -> dict:
        """保存上传的照片（兼容旧接口）"""
        if filename is None:
            filename = file_path.name
        return self.index_manager.add_uploaded_file(file_path, filename)

    def get_photos_by_date(self, date: str) -> list:
        """获取指定日期的照片（兼容旧接口）"""
        return self.index_manager.get_photos_by_date(date)

    def get_all_dates(self) -> list:
        """获取所有日期（兼容旧接口）"""
        return self.index_manager.get_all_dates()

    def scan_existing_photos(self):
        """扫描已有照片（兼容旧接口，实际调用 scan_source_folders）"""
        return self.index_manager.scan_source_folders()


if __name__ == "__main__":
    # 测试代码
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试文件
        test_dir = Path(tmpdir) / "test_photos"
        test_dir.mkdir()

        # 创建索引管理器
        pim = PhotoIndexManager(Path(tmpdir), [str(test_dir)])

        print("测试索引管理器...")
        print(f"索引文件: {pim.index_file}")
        print(f"Original 目录: {pim.original_dir}")

        # 扫描
        result = pim.scan_source_folders()
        print(f"扫描结果: {result}")

        # 统计
        stats = pim.get_stats()
        print(f"统计: {stats}")
