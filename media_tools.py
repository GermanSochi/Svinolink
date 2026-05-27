from __future__ import annotations

import io
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
    """Надёжный мем без ImageMagick, с кириллицей (DejaVu fallback)."""
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)

    font = _load_font(52)
    stamp_font = _load_font(28)

    padding = 64
    max_width_px = size[0] - padding * 2

    lines = _wrap_by_pixels(draw, text.strip(), font, max_width_px)[:12]

    y = padding
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (size[0] - w) // 2
        # Тень для контраста
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=fg)
        y += (bbox[3] - bbox[1]) + 18

    # Маленький "водяной знак" без эмодзи в конце строк — просто текст.
    stamp = "Svinolink"
    bbox = draw.textbbox((0, 0), stamp, font=stamp_font)
    draw.text(
        (size[0] - padding - (bbox[2] - bbox[0]), size[1] - padding // 2),
        stamp,
        font=stamp_font,
        fill=(160, 160, 160),
    )
    return img


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Пытаемся загрузить DejaVuSans (кириллица). Если нет — падаем на default font.
    """
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\calibri.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_by_pixels(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width_px: int,
) -> list[str]:
    words = [w for w in text.replace("\n", " ").split(" ") if w]
    if not words:
        return [""]

    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        probe = (" ".join(cur + [w])).strip()
        bbox = draw.textbbox((0, 0), probe, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width_px or not cur:
            cur.append(w)
            continue
        lines.append(" ".join(cur))
        cur = [w]
    if cur:
        lines.append(" ".join(cur))

    # Если вдруг слово сверхдлинное — режем.
    fixed: list[str] = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        if (bbox[2] - bbox[0]) <= max_width_px:
            fixed.append(line)
            continue
        while line:
            cut = line[: max(1, len(line) - 1)]
            bbox = draw.textbbox((0, 0), cut + "…", font=font)
            if (bbox[2] - bbox[0]) <= max_width_px:
                fixed.append(cut + "…")
                break
            line = cut
    return fixed


def image_to_bytes(img: Image.Image, *, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def save_bytes(data: bytes, out_dir: Path, *, prefix: str, ext: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / _safe_filename(prefix, ext)
    path.write_bytes(data)
    return path

