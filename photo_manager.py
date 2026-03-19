#!/usr/bin/env python3
"""
本地照片管理模块 - 索引机制版本
- 使用索引管理照片，不重复存储文件
- 支持多文件夹扫描
- 兼容旧接口
"""

import os
import re
from pathlib import Path
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS
import shutil


class PhotoManager:
    """
    本地照片管理器 - 索引机制版本

    设计理念：
    - 用户设置一个或多个媒体文件夹（source folders）
    - 系统扫描这些文件夹，为每个文件创建索引（路径、日期、哈希等）
    - 按日期查询时，通过索引找到原文件路径，不复制文件
    - 新上传的文件保存到 data/original 文件夹，自动添加到索引
    - 删除时只删除索引条目，不删除原文件
    """

    def __init__(self, media_folders: list, data_dir: Path = None):
        """
        初始化照片管理器

        Args:
            media_folders: 媒体文件夹路径列表（支持多个）
            data_dir: 数据目录，用于存储索引和上传的文件
        """
        # 导入索引管理器
        from photo_index_manager import PhotoIndexManager

        # 如果没有指定数据目录，使用默认路径
        if data_dir is None:
            data_dir = Path.home() / "Documents" / "CZRZ" / "data"

        self.data_dir = Path(data_dir)
        self.index_manager = PhotoIndexManager(self.data_dir, media_folders)

        # 保存媒体文件夹列表
        self.media_folders = [Path(f) for f in media_folders if Path(f).exists()]

    def extract_date_from_filename(self, filename: str) -> str:
        """
        从文件名中提取日期（兼容旧接口）

        Returns:
            日期字符串 YYYY-MM-DD，如果无法解析返回 None
        """
        return self.index_manager._extract_date_from_filename(filename)

    def extract_date_from_exif(self, image_path: Path) -> str:
        """
        从图片EXIF信息中提取拍摄日期（兼容旧接口）

        Returns:
            日期字符串 YYYY-MM-DD，如果无法解析返回 None
        """
        return self.index_manager._extract_date_from_exif(image_path)

    def get_photo_date(self, file_path: Path) -> str:
        """
        获取照片日期（兼容旧接口）

        Returns:
            日期字符串 YYYY-MM-DD
        """
        return self.index_manager._get_file_date(file_path)

    def save_uploaded_photo(self, file_path: Path, filename: str = None) -> dict:
        """
        保存上传的照片到本地文件夹（兼容旧接口）

        Args:
            file_path: 上传的临时文件路径
            filename: 原始文件名

        Returns:
            包含照片信息的字典
        """
        if filename is None:
            filename = file_path.name

        # 使用索引管理器添加文件
        entry = self.index_manager.add_uploaded_file(file_path, filename)

        return {
            "filename": entry["filename"],
            "original_name": entry.get("original_name", filename),
            "date": entry["date"],
            "path": entry["path"],
            "hash": entry["hash"],
            "is_video": entry.get("is_video", False),
        }

    def save_uploaded_photo_with_date(
        self, file_path: Path, filename: str = None, date: str = None
    ) -> dict:
        """
        保存上传的照片到本地文件夹，使用指定日期

        Args:
            file_path: 上传的临时文件路径
            filename: 原始文件名
            date: 指定的日期字符串 YYYY-MM-DD

        Returns:
            包含照片信息的字典
        """
        if filename is None:
            filename = file_path.name

        # 使用索引管理器添加文件，传入指定日期
        entry = self.index_manager.add_uploaded_file_with_date(
            file_path, filename, date
        )

        return {
            "filename": entry["filename"],
            "original_name": entry.get("original_name", filename),
            "date": entry["date"],
            "path": entry["path"],
            "hash": entry["hash"],
            "is_video": entry.get("is_video", False),
        }

    def get_photos_by_date(self, date: str) -> list:
        """
        获取指定日期的所有照片（通过索引）

        Args:
            date: 日期字符串 YYYY-MM-DD

        Returns:
            照片文件路径列表
        """
        photos = self.index_manager.get_photos_by_date(date)

        # 兼容旧格式，添加 folder 字段
        for photo in photos:
            # 从路径推断文件夹
            path = Path(photo["path"])
            photo["folder"] = str(path.parent)

        return photos

    def get_all_dates(self) -> list:
        """
        获取所有有照片的日期

        Returns:
            日期字符串列表 YYYY-MM-DD
        """
        return self.index_manager.get_all_dates()
    
    def get_dates_by_month(self, year: int, month: int) -> list:
        """
        获取指定月份的所有有照片的日期 - 优化版本

        Args:
            year: 年份
            month: 月份 (1-12)

        Returns:
            日期字符串列表 YYYY-MM-DD
        """
        return self.index_manager.get_dates_by_month(year, month)

    def scan_existing_photos(self, progress_callback=None):
        """
        扫描源文件夹中的照片，更新索引

        Args:
            progress_callback: 进度回调函数 (current, total, message)
        """
        return self.index_manager.scan_source_folders(progress_callback)

    def get_photo_by_filename(self, filename: str) -> dict:
        """
        通过文件名获取照片信息

        Args:
            filename: 文件名

        Returns:
            照片信息字典，如果不存在返回 None
        """
        entry = self.index_manager.get_photo_by_filename(filename)
        if entry:
            return {
                "hash": entry["hash"],
                "filename": entry["filename"],
                "path": entry["path"],
                "date": entry["date"],
                "is_video": entry.get("is_video", False),
                "size": entry.get("size", 0),
            }
        return None

    def get_photo_by_hash(self, file_hash: str) -> dict:
        """
        通过文件哈希获取照片信息

        Args:
            file_hash: 文件哈希

        Returns:
            照片信息字典，如果不存在返回 None
        """
        entry = self.index_manager.get_photo_by_hash(file_hash)
        if entry:
            return {
                "hash": entry["hash"],
                "filename": entry["filename"],
                "path": entry["path"],
                "date": entry["date"],
                "is_video": entry.get("is_video", False),
                "size": entry.get("size", 0),
            }
        return None

    def remove_photo(self, filename: str) -> bool:
        """
        从索引中移除照片（不删除实际文件）

        Args:
            filename: 文件名

        Returns:
            是否成功移除
        """
        return self.index_manager.remove_from_index(filename)

    def get_stats(self) -> dict:
        """获取统计信息"""
        return self.index_manager.get_stats()


# 向后兼容：保持旧的导入方式
PhotoIndexManager = None  # 将在下面导入

