#!/usr/bin/env python3
"""
照片合图生成器
- 根据标签或日期生成有趣的合图
- 支持多种风格：拼图、胶片、杂志、时间轴等
"""

import os
import random
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter


class PhotoCollageGenerator:
    """照片合图生成器"""

    # 合图风格配置
    STYLES = {
        "grid": {
            "name": "九宫格",
            "description": "经典网格布局",
            "min_photos": 2,
            "max_photos": 9,
        },
        "film": {
            "name": "胶片风",
            "description": "复古胶片风格",
            "min_photos": 2,
            "max_photos": 6,
        },
        "magazine": {
            "name": "杂志封面",
            "description": "时尚杂志风格",
            "min_photos": 1,
            "max_photos": 4,
        },
        "polaroid": {
            "name": "拍立得",
            "description": "怀旧拍立得风格",
            "min_photos": 2,
            "max_photos": 6,
        },
        "timeline": {
            "name": "成长时间轴",
            "description": "时间线布局",
            "min_photos": 2,
            "max_photos": 5,
        },
        "comparison": {
            "name": "成长对比",
            "description": "前后对比",
            "min_photos": 2,
            "max_photos": 2,
        },
    }

    def __init__(self, output_dir: str = None):
        """
        初始化合图生成器

        Args:
            output_dir: 输出目录，默认为临时目录
        """
        self.output_dir = Path(output_dir) if output_dir else Path("/tmp/collage")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # 默认字体
        self.font_path = self._find_font()

    def _find_font(self) -> Optional[str]:
        """查找可用的中文字体"""
        font_paths = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for path in font_paths:
            if Path(path).exists():
                return path
        return None

    def _load_image(self, path: str, size: Tuple[int, int] = None) -> Optional[Image.Image]:
        """
        加载并处理图片

        Args:
            path: 图片路径
            size: 目标尺寸 (width, height)

        Returns:
            PIL Image 对象
        """
        try:
            img = Image.open(path)

            # 处理EXIF方向
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass

            # 转换为RGB
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            # 调整尺寸
            if size:
                img = self._fit_image(img, size)

            return img
        except Exception as e:
            print(f"[Collage] 加载图片失败: {path}, {e}")
            return None

    def _fit_image(self, img: Image.Image, size: Tuple[int, int]) -> Image.Image:
        """
        将图片裁剪为指定尺寸（居中裁剪）
        """
        target_w, target_h = size
        src_w, src_h = img.size

        # 计算缩放比例
        ratio = max(target_w / src_w, target_h / src_h)
        new_w = int(src_w * ratio)
        new_h = int(src_h * ratio)

        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

        # 居中裁剪
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2

        return img.crop((left, top, left + target_w, top + target_h))

    def _draw_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        position: Tuple[int, int],
        font_size: int = 24,
        color: Tuple[int, int, int] = (255, 255, 255),
        shadow: bool = True,
    ) -> Tuple[int, int]:
        """
        绘制文字（带阴影）

        Returns:
            文字尺寸
        """
        try:
            font = (
                ImageFont.truetype(self.font_path, font_size)
                if self.font_path
                else ImageFont.load_default()
            )
        except Exception:
            font = ImageFont.load_default()

        # 获取文字尺寸
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        x, y = position

        # 绘制阴影
        if shadow:
            shadow_color = (0, 0, 0)
            for dx, dy in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                draw.text((x + dx, y + dy), text, font=font, fill=shadow_color)

        # 绘制文字
        draw.text((x, y), text, font=font, fill=color)

        return (text_w, text_h)

    def generate_grid(
        self,
        photos: List[str],
        title: str = "",
        subtitle: str = "",
        dates: List[str] = None,
        output_path: str = None,
    ) -> Optional[str]:
        """
        生成九宫格拼图

        Args:
            photos: 照片路径列表
            title: 标题
            subtitle: 副标题
            dates: 每张照片的日期列表
            output_path: 输出路径

        Returns:
            生成的图片路径
        """
        if len(photos) < 2:
            print("[Collage] 至少需要2张照片")
            return None

        # 限制照片数量
        photos = photos[:9]
        count = len(photos)

        # 计算网格布局
        if count <= 2:
            cols, rows = 2, 1
        elif count <= 4:
            cols, rows = 2, 2
        elif count <= 6:
            cols, rows = 3, 2
        else:
            cols, rows = 3, 3

        # 图片尺寸
        cell_w, cell_h = 280, 280
        date_h = 25  # 日期区域高度
        gap = 15
        padding = 40

        # 画布尺寸
        canvas_w = cols * cell_w + (cols - 1) * gap + padding * 2
        canvas_h = rows * (cell_h + date_h) + (rows - 1) * gap + padding * 2

        # 添加标题区域
        title_h = 60 if title else 0
        canvas_h += title_h

        # 创建画布
        canvas = Image.new("RGB", (canvas_w, canvas_h), (245, 245, 245))
        draw = ImageDraw.Draw(canvas)

        # 加载字体
        try:
            date_font = ImageFont.truetype(self.font_path, 14) if self.font_path else ImageFont.load_default()
        except:
            date_font = ImageFont.load_default()

        # 绘制标题
        if title:
            self._draw_text(
                draw, title, (padding, 20), font_size=28, color=(50, 50, 50), shadow=False
            )
            if subtitle:
                self._draw_text(
                    draw, subtitle, (padding, 50), font_size=14, color=(120, 120, 120), shadow=False
                )

        # 放置照片
        for i, photo_path in enumerate(photos):
            if i >= cols * rows:
                break

            col = i % cols
            row = i // cols

            x = padding + col * (cell_w + gap)
            y = title_h + padding + row * (cell_h + date_h + gap)

            img = self._load_image(photo_path, (cell_w, cell_h))
            if img:
                # 添加圆角
                img = self._add_rounded_corners(img, 12)
                canvas.paste(img, (x, y))

            # 在照片下方显示日期
            if dates and i < len(dates):
                date_text = self._format_date(dates[i])
                if date_text:
                    bbox = draw.textbbox((0, 0), date_text, font=date_font)
                    text_w = bbox[2] - bbox[0]
                    draw.text((x + (cell_w - text_w) // 2, y + cell_h + 5), date_text, font=date_font, fill=(100, 100, 100))

        # 保存
        if not output_path:
            output_path = str(self.output_dir / f"grid_{hash(tuple(photos)) % 100000}.jpg")

        canvas.save(output_path, "JPEG", quality=90)
        return output_path

    def generate_film(
        self,
        photos: List[str],
        title: str = "",
        dates: List[str] = None,
        output_path: str = None,
    ) -> Optional[str]:
        """
        生成胶片风格合图
        """
        if len(photos) < 2:
            return None

        photos = photos[:6]
        count = len(photos)

        # 胶片参数
        frame_w, frame_h = 280, 200
        perforation_h = 20
        gap = 15
        padding = 40

        # 画布尺寸
        canvas_w = frame_w + padding * 2
        canvas_h = count * (frame_h + perforation_h + gap) + padding * 2

        # 创建画布（胶片底色）
        canvas = Image.new("RGB", (canvas_w, canvas_h), (30, 30, 30))
        draw = ImageDraw.Draw(canvas)

        # 绘制胶片边框和孔
        for i, photo_path in enumerate(photos):
            y = padding + i * (frame_h + perforation_h + gap)

            # 绘制照片框
            img = self._load_image(photo_path, (frame_w - 20, frame_h - 10))
            if img:
                canvas.paste(img, (padding + 10, y + 5))

            # 绘制胶片孔
            for px in [padding + 5, padding + frame_w - 15]:
                draw.rectangle(
                    [px, y, px + 10, y + perforation_h - 5],
                    fill=(20, 20, 20),
                    outline=(50, 50, 50),
                )

        # 添加标题
        if title:
            self._draw_text(draw, title, (padding, canvas_h - 30), font_size=16, color=(200, 200, 200))

        if not output_path:
            output_path = str(self.output_dir / f"film_{hash(tuple(photos)) % 100000}.jpg")

        canvas.save(output_path, "JPEG", quality=90)
        return output_path

    def generate_polaroid(
        self,
        photos: List[str],
        title: str = "",
        dates: List[str] = None,
        output_path: str = None,
    ) -> Optional[str]:
        """
        生成拍立得风格合图 - 不堆叠，照片更大，每张下面显示日期
        """
        if len(photos) < 2:
            return None

        photos = photos[:4]  # 最多4张
        count = len(photos)

        # 照片参数 - 更大的照片
        photo_w = 280
        photo_h = 320
        gap = 30  # 照片间距
        padding = 40

        # 根据照片数量决定布局
        if count <= 2:
            cols = 2
            rows = 1
        elif count == 3:
            cols = 3
            rows = 1
        else:
            cols = 2
            rows = 2

        # 画布尺寸
        canvas_w = cols * photo_w + (cols - 1) * gap + padding * 2
        canvas_h = rows * (photo_h + 40) + (rows - 1) * gap + padding * 2 + 60  # 额外60给标题

        # 创建画布
        canvas = Image.new("RGB", (canvas_w, canvas_h), (250, 248, 245))
        draw = ImageDraw.Draw(canvas)

        # 加载字体
        try:
            font = ImageFont.truetype(self.font_path, 16) if self.font_path else ImageFont.load_default()
            title_font = ImageFont.truetype(self.font_path, 22) if self.font_path else ImageFont.load_default()
        except:
            font = ImageFont.load_default()
            title_font = ImageFont.load_default()

        # 放置照片
        for i, photo_path in enumerate(photos):
            row = i // cols
            col = i % cols
            
            x = padding + col * (photo_w + gap)
            y = padding + row * (photo_h + 40 + gap)

            # 加载照片
            img = self._load_image(photo_path, (photo_w, photo_h))
            if img:
                # 添加白色边框效果
                border = 8
                white_frame = Image.new("RGB", (photo_w + border * 2, photo_h + border * 2), (255, 255, 255))
                white_frame.paste(img, (border, border))
                canvas.paste(white_frame, (x - border, y - border))
            else:
                # 占位
                placeholder = Image.new("RGB", (photo_w, photo_h), (200, 200, 200))
                canvas.paste(placeholder, (x, y))

            # 在照片下方显示日期
            if dates and i < len(dates):
                date_text = self._format_date(dates[i])
                if date_text:
                    bbox = draw.textbbox((0, 0), date_text, font=font)
                    text_w = bbox[2] - bbox[0]
                    draw.text((x + (photo_w - text_w) // 2, y + photo_h + 8), date_text, font=font, fill=(100, 100, 100))

        # 添加标题
        if title:
            bbox = draw.textbbox((0, 0), title, font=title_font)
            text_w = bbox[2] - bbox[0]
            draw.text(((canvas_w - text_w) // 2, canvas_h - 45), title, font=title_font, fill=(80, 80, 80))

        if not output_path:
            output_path = str(self.output_dir / f"polaroid_{hash(tuple(photos)) % 100000}.jpg")

        canvas.save(output_path, "JPEG", quality=90)
        return output_path

    def generate_comparison(
        self,
        photos: List[str],
        labels: List[str] = None,
        title: str = "",
        dates: List[str] = None,
        output_path: str = None,
    ) -> Optional[str]:
        """
        生成成长对比图
        """
        if len(photos) < 2:
            return None

        photos = photos[:2]
        if not labels:
            labels = ["以前", "现在"]

        # 参数
        photo_w, photo_h = 350, 400
        gap = 60
        padding = 40

        # 画布尺寸
        canvas_w = photo_w * 2 + gap + padding * 2
        canvas_h = photo_h + 100

        # 创建画布
        canvas = Image.new("RGB", (canvas_w, canvas_h), (250, 248, 245))
        draw = ImageDraw.Draw(canvas)

        # 绘制标题
        if title:
            self._draw_text(draw, title, (canvas_w // 2 - 50, 15), font_size=24, color=(60, 60, 60), shadow=False)

        # 放置照片
        for i, photo_path in enumerate(photos):
            x = padding + i * (photo_w + gap)
            y = 50

            img = self._load_image(photo_path, (photo_w, photo_h))
            if img:
                # 添加阴影效果
                shadow = Image.new("RGB", img.size, (200, 200, 200))
                shadow = shadow.filter(ImageFilter.GaussianBlur(10))
                canvas.paste(shadow, (x + 5, y + 5))
                canvas.paste(img, (x, y))

            # 添加标签
            label = labels[i] if i < len(labels) else ""
            self._draw_text(
                draw, label, (x + photo_w // 2 - 20, y + photo_h + 10),
                font_size=18, color=(100, 100, 100), shadow=False
            )

        # 添加箭头
        arrow_x = padding + photo_w + gap // 2
        arrow_y = 50 + photo_h // 2
        draw.text((arrow_x - 15, arrow_y - 10), "→", fill=(255, 150, 100))

        if not output_path:
            output_path = str(self.output_dir / f"comparison_{hash(tuple(photos)) % 100000}.jpg")

        canvas.save(output_path, "JPEG", quality=90)
        return output_path

    def generate_magazine(
        self,
        photos: List[str],
        title: str = "成长日记",
        subtitle: str = "",
        date: str = "",
        dates: List[str] = None,
        output_path: str = None,
    ) -> Optional[str]:
        """
        生成杂志封面风格
        """
        if not photos:
            return None

        main_photo = photos[0]
        sub_photos = photos[1:4] if len(photos) > 1 else []

        # 参数
        canvas_w, canvas_h = 500, 700
        main_photo_size = (480, 380)

        # 创建画布
        canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)

        # 加载字体
        try:
            date_font = ImageFont.truetype(self.font_path, 12) if self.font_path else ImageFont.load_default()
        except:
            date_font = ImageFont.load_default()

        # 主照片
        img = self._load_image(main_photo, main_photo_size)
        if img:
            canvas.paste(img, (10, 10))

        # 主照片日期
        if dates and len(dates) > 0:
            date_text = self._format_date(dates[0])
            if date_text:
                draw.text((20, 395), date_text, font=date_font, fill=(100, 100, 100))

        # 标题区域背景
        draw.rectangle([0, 420, canvas_w, canvas_h], fill=(255, 255, 255))

        # 杂志标题
        self._draw_text(draw, title, (20, 440), font_size=36, color=(40, 40, 40), shadow=False)

        # 副标题
        if subtitle:
            self._draw_text(draw, subtitle, (20, 490), font_size=16, color=(120, 120, 120), shadow=False)

        # 日期
        if date:
            self._draw_text(draw, date, (20, 520), font_size=14, color=(180, 180, 180), shadow=False)

        # 小照片
        if sub_photos:
            small_size = (100, 100)
            for i, photo_path in enumerate(sub_photos):
                x = 20 + i * 130
                y = 560
                img = self._load_image(photo_path, small_size)
                if img:
                    img = self._add_rounded_corners(img, 8)
                    canvas.paste(img, (x, y))

                # 小照片日期
                if dates and i + 1 < len(dates):
                    date_text = self._format_date(dates[i + 1])
                    if date_text:
                        draw.text((x + 30, y + 105), date_text, font=date_font, fill=(100, 100, 100))

        # 装饰线
        draw.line([(20, 550), (canvas_w - 20, 550)], fill=(230, 230, 230), width=1)

        if not output_path:
            output_path = str(self.output_dir / f"magazine_{hash(tuple(photos)) % 100000}.jpg")

        canvas.save(output_path, "JPEG", quality=90)
        return output_path

    def _add_rounded_corners(self, img: Image.Image, radius: int) -> Image.Image:
        """添加圆角"""
        # 创建圆角蒙版
        mask = Image.new("L", img.size, 0)
        draw = ImageDraw.Draw(mask)

        # 绘制圆角矩形
        draw.rounded_rectangle([(0, 0), img.size], radius=radius, fill=255)

        # 应用蒙版
        output = Image.new("RGB", img.size, (255, 255, 255))
        output.paste(img, (0, 0))
        output.putalpha(mask)

        return output.convert("RGB")

    def generate(
        self,
        photos: List[str],
        style: str = "grid",
        title: str = "",
        dates: List[str] = None,
        **kwargs,
    ) -> Optional[str]:
        """
        生成合图

        Args:
            photos: 照片路径列表
            style: 风格 (grid, film, polaroid, comparison, magazine)
            title: 标题
            dates: 每张照片的日期列表
            **kwargs: 其他参数

        Returns:
            生成的图片路径
        """
        if not photos:
            print("[Collage] 没有照片")
            return None

        # 过滤存在的照片
        photos = [p for p in photos if Path(p).exists()]
        if not photos:
            print("[Collage] 所有照片都不存在")
            return None

        # 按日期排序照片
        if dates and len(dates) >= len(photos):
            # 将照片和日期配对，按日期排序
            paired = list(zip(photos, dates[:len(photos)]))
            paired.sort(key=lambda x: x[1] if x[1] else "")
            photos = [p[0] for p in paired]
            dates = [p[1] for p in paired]

        style_config = self.STYLES.get(style)
        if not style_config:
            print(f"[Collage] 未知风格: {style}")
            return None

        min_photos = style_config["min_photos"]
        if len(photos) < min_photos:
            print(f"[Collage] {style}风格至少需要{min_photos}张照片")
            return None

        # 根据风格生成
        if style == "grid":
            return self.generate_grid(photos, title=title, dates=dates, **kwargs)
        elif style == "film":
            return self.generate_film(photos, title=title, dates=dates, **kwargs)
        elif style == "polaroid":
            return self.generate_polaroid(photos, title=title, dates=dates, **kwargs)
        elif style == "comparison":
            return self.generate_comparison(photos, title=title, dates=dates, **kwargs)
        elif style == "magazine":
            return self.generate_magazine(photos, title=title, dates=dates, **kwargs)

        return None

    def _format_date(self, date: str) -> str:
        """
        格式化日期为 YY.MM.DD 格式

        Args:
            date: 日期字符串 (YYYY-MM-DD)

        Returns:
            格式化后的日期 (YY.MM.DD)
        """
        if not date:
            return ""
        try:
            from datetime import datetime
            if "-" in date:
                dt = datetime.strptime(date, "%Y-%m-%d")
                return dt.strftime("%y.%m.%d")
        except:
            pass
        return date


def get_available_styles() -> List[Dict]:
    """获取可用的合图风格"""
    return [
        {"id": k, "name": v["name"], "description": v["description"], "min_photos": v["min_photos"]}
        for k, v in PhotoCollageGenerator.STYLES.items()
    ]


if __name__ == "__main__":
    # 测试
    import sys

    if len(sys.argv) < 2:
        print("用法: python photo_collage.py <照片路径...>")
        print("风格: grid, film, polaroid, comparison, magazine")
        sys.exit(1)

    photos = sys.argv[1:]
    generator = PhotoCollageGenerator()

    print("可用风格:")
    for style in get_available_styles():
        print(f"  - {style['id']}: {style['name']} ({style['description']})")

    # 测试生成
    for style in ["grid", "polaroid", "film"]:
        result = generator.generate(photos, style=style, title="沐沐的成长")
        if result:
            print(f"✅ {style} 生成成功: {result}")