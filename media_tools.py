from __future__ import annotations

import io
import textwrap
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _safe_filename(prefix: str, ext: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{ts}.{ext.lstrip('.')}"


def make_meme_image(
    text: str,
    *,
    size: tuple[int, int] = (1024, 1024),
    bg: tuple[int, int, int] = (18, 22, 34),
    fg: tuple[int, int, int] = (245, 245, 245),
) -> Image.Image:
    """
    Надёжный мем без внешних шрифтов/Imagemagick.
    Используем встроенный PIL font, чтобы работало в Docker slim.
    """
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)

    # PIL default font — самый совместимый вариант.
    font = ImageFont.load_default()

    padding = 64
    max_width_px = size[0] - padding * 2

    # Грубый перенос строк по символам: для default font это ок.
    wrapped = textwrap.fill(text.strip(), width=42)
    lines = wrapped.splitlines()[:18]

    y = padding
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        # Если строка всё равно широкая — режем.
        if w > max_width_px:
            line = line[:70] + "…"
            bbox = draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
        x = (size[0] - w) // 2
        # Тень для контраста
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=fg)
        y += (bbox[3] - bbox[1]) + 12

    # Маленький "водяной знак" без эмодзи в конце строк — просто текст.
    stamp = "Svinolink"
    bbox = draw.textbbox((0, 0), stamp, font=font)
    draw.text(
        (size[0] - padding - (bbox[2] - bbox[0]), size[1] - padding),
        stamp,
        font=font,
        fill=(160, 160, 160),
    )
    return img


def image_to_bytes(img: Image.Image, *, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def save_bytes(data: bytes, out_dir: Path, *, prefix: str, ext: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _safe_filename(prefix, ext)
    path.write_bytes(data)
    return path

