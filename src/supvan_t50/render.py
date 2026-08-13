from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .errors import DependencyError, ValidationError
from .models import DrawObject, FontStyle, ObjectFormat, PrintPage

DOTS_PER_MM = 8


def mm_to_px(value: float) -> int:
    return max(1, int(round(value * DOTS_PER_MM)))


def _font_candidates(name: str) -> Iterable[Path | str]:
    if name and Path(name).exists():
        yield Path(name)
    aliases = {
        "黑体": ["simhei.ttf", "msyh.ttc"],
        "微软雅黑": ["msyh.ttc", "msyhbd.ttc"],
        "宋体": ["simsun.ttc"],
        "arial": ["arial.ttf"],
    }
    requested = aliases.get(name.lower(), aliases.get(name, [name]))
    font_dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts")]
    for candidate in requested:
        if not candidate:
            continue
        yield candidate
        for font_dir in font_dirs:
            if font_dir.exists():
                direct = font_dir / candidate
                if direct.exists():
                    yield direct
    yield "DejaVuSans.ttf"


def _contains_cjk(text: str) -> bool:
    return any(
        "\u3400" <= char <= "\u4dbf"
        or "\u4e00" <= char <= "\u9fff"
        or "\uf900" <= char <= "\ufaff"
        for char in text
    )


def load_font(name: str, size_px: int, *, text: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _font_candidates(name):
        try:
            return ImageFont.truetype(str(candidate), size_px)
        except (OSError, ValueError):
            continue
    if _contains_cjk(text):
        raise ValidationError(
            f"无法加载包含中文字形的字体 {name!r}；请安装字体或传入 .ttf/.otf/.ttc 文件路径"
        )
    return ImageFont.load_default()


def _fit_image(image: Image.Image, width: int, height: int) -> Image.Image:
    return ImageOps.contain(image.convert("L"), (width, height), Image.Resampling.LANCZOS)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for char in paragraph:
            trial = current + char
            if current and draw.textlength(trial, font=font) > width:
                lines.append(current)
                current = char
            else:
                current = trial
        lines.append(current)
    return lines


def _render_text(canvas: Image.Image, obj: DrawObject, box: tuple[int, int, int, int]) -> None:
    draw = ImageDraw.Draw(canvas)
    x0, y0, x1, y1 = box
    fill = 0
    if obj.anti_color:
        draw.rectangle(box, fill=0)
        fill = 255
    font = load_font(obj.font_name, mm_to_px(float(obj.font_size)), text=obj.content)
    lines = _wrap_text(draw, obj.content, font, x1 - x0) if obj.auto_return else [obj.content]
    bbox = draw.textbbox((0, 0), "国Ag", font=font)
    line_height = max(1, bbox[3] - bbox[1] + 1)
    y = y0 + max(0, ((y1 - y0) - line_height * len(lines)) // 2)
    for line in lines:
        width = int(draw.textlength(line, font=font))
        x = x0 if obj.align == 0 else x0 + ((x1 - x0 - width) // 2 if obj.align == 1 else x1 - x0 - width)
        draw.text((x, y), line, fill=fill, font=font)
        if obj.font_style & FontStyle.BOLD:
            draw.text((x + 1, y), line, fill=fill, font=font)
        if obj.font_style & FontStyle.UNDERLINE:
            draw.line((x, y + line_height - 1, x + width, y + line_height - 1), fill=fill)
        if obj.font_style & FontStyle.STRIKEOUT:
            draw.line((x, y + line_height // 2, x + width, y + line_height // 2), fill=fill)
        y += line_height
        if y >= y1:
            break


def _render_qr(content: str, width: int, height: int) -> Image.Image:
    try:
        import qrcode
    except ImportError as exc:
        raise DependencyError("二维码渲染需要安装 qrcode：pip install 'supvan-t50[usb]' 或 'supvan-t50[bluetooth]'") from exc
    # Keep QR modules square and integer-sized. Resampling a QR bitmap to an
    # arbitrary rectangle blurs module edges and makes small labels difficult
    # to scan. A four-module quiet zone is required by QR readers.
    qr = qrcode.QRCode(border=4, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(content)
    qr.make(fit=True)
    modules = qr.modules_count + 8
    box_size = max(1, min(width, height) // modules)
    qr.box_size = box_size
    rendered = qr.make_image(fill_color="black", back_color="white").convert("L")
    canvas = Image.new("L", (width, height), 255)
    x = max(0, (width - rendered.width) // 2)
    y = max(0, (height - rendered.height) // 2)
    canvas.paste(rendered, (x, y))
    return canvas


def _render_barcode(obj: DrawObject, width: int, height: int) -> Image.Image:
    try:
        import barcode
        from barcode.writer import ImageWriter
    except ImportError as exc:
        raise DependencyError("条码渲染需要安装 python-barcode：pip install 'supvan-t50[usb]' 或 'supvan-t50[bluetooth]'") from exc
    kind = "ean13" if obj.format == ObjectFormat.EAN_13 else "code128"
    try:
        code = barcode.get(kind, obj.content, writer=ImageWriter())
        stream = io.BytesIO()
        code.write(stream, options={"write_text": False, "quiet_zone": 0, "module_height": max(5.0, obj.height)})
        stream.seek(0)
        return _fit_image(Image.open(stream), width, height)
    except Exception as exc:
        raise ValidationError(f"无法生成 {obj.format.value} 条码：{exc}") from exc


def _render_object(canvas: Image.Image, obj: DrawObject) -> None:
    x0, y0 = mm_to_px(obj.x), mm_to_px(obj.y)
    width, height = mm_to_px(obj.width), mm_to_px(obj.height)
    box = (x0, y0, min(canvas.width, x0 + width), min(canvas.height, y0 + height))
    if box[0] >= canvas.width or box[1] >= canvas.height:
        return
    if obj.format == ObjectFormat.TEXT:
        _render_text(canvas, obj, box)
        return
    if obj.format == ObjectFormat.QR_CODE:
        rendered = _render_qr(obj.content, box[2] - box[0], box[3] - box[1])
    elif obj.format in (ObjectFormat.EAN_13, ObjectFormat.CODE_128):
        rendered = _render_barcode(obj, box[2] - box[0], box[3] - box[1])
    else:
        source = obj.image if obj.image is not None else obj.content
        if isinstance(source, Image.Image):
            image = source
        else:
            image = Image.open(Path(source))
        rendered = _fit_image(image, box[2] - box[0], box[3] - box[1])
    if obj.anti_color:
        rendered = ImageOps.invert(rendered.convert("L"))
    canvas.paste(rendered, (box[0], box[1]))


def render_page(page: PrintPage) -> Image.Image:
    canvas = Image.new("L", (mm_to_px(page.width), mm_to_px(page.height)), 255)
    for obj in page.objects:
        _render_object(canvas, obj)
    return canvas
