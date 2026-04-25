#!/usr/bin/env python3
"""Crop a raw 16:9 generated image to a WeChat cover size and overlay the
article title with a dark gradient for readability.

WeChat displays the cover at roughly 2.35:1 in the article list, but
`draft/add` accepts any reasonable size — we default to 900×383 because
that matches the feed thumbnail aspect and avoids awkward cropping.

Usage:
  python cover_compose.py \\
    --input  /tmp/cover_raw.png \\
    --title  "你的文章标题（最多 2 行）" \\
    --output /tmp/cover.png \\
    [--size 900x383]           # or 900x500
    [--font /path/to/CJK.ttf]  # override font
"""
import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
except ImportError:
    sys.stderr.write("error: Pillow not installed. Run: pip install Pillow\n")
    sys.exit(1)


# Candidate Chinese-capable font paths across common platforms.
# First hit wins; override with --font if none work.
DEFAULT_FONT_CANDIDATES = [
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/arphic/ukai.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
]


def pick_font(override, size):
    paths = [override] if override else DEFAULT_FONT_CANDIDATES
    for p in paths:
        if not p:
            continue
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    sys.stderr.write(
        "warn: no CJK font found; falling back to default (Chinese may render as tofu)\n"
    )
    return ImageFont.load_default()


def center_crop(img, target_w, target_h):
    """Crop `img` to target aspect ratio, centered, then resize to exact size.

    We crop-then-resize (not resize-then-crop) to avoid interpolating twice.
    """
    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h
    if src_ratio > target_ratio:
        # Source is too wide — crop sides.
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, src_h))
    else:
        # Source is too tall — crop top/bottom.
        new_h = int(src_w / target_ratio)
        top = (src_h - new_h) // 2
        img = img.crop((0, top, src_w, top + new_h))
    return img.resize((target_w, target_h), Image.LANCZOS)


def wrap_title(draw, title, font, max_width):
    """Wrap a Chinese title greedily by character (CJK has no word boundaries)."""
    lines = []
    current = ""
    for ch in title:
        test = current + ch
        w = draw.textbbox((0, 0), test, font=font)[2]
        if w > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    return lines[:2]  # cover titles should never exceed 2 visible lines


def draw_gradient_overlay(img, height_frac=0.55):
    """Paint a bottom-anchored dark gradient. Makes white title text readable
    without losing the image identity up top."""
    w, h = img.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    grad_h = int(h * height_frac)
    for y in range(grad_h):
        # Ease-in quadratic for smoother fade than linear.
        t = y / max(1, grad_h - 1)
        alpha = int(180 * (t ** 1.6))
        overlay.paste((0, 0, 0, alpha), (0, h - grad_h + y, w, h - grad_h + y + 1))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


def fit_font_size(draw, title, font_path, max_width, max_height, start=52, minimum=28):
    """Shrink font until title (wrapped) fits. Guarantees ≤ 2 lines."""
    size = start
    while size >= minimum:
        font = (
            ImageFont.truetype(font_path, size)
            if font_path and Path(font_path).exists()
            else pick_font(None, size)
        )
        lines = wrap_title(draw, title, font, max_width)
        line_h = draw.textbbox((0, 0), "汉A", font=font)[3]
        total_h = line_h * len(lines) * 1.25
        if len(lines) <= 2 and total_h <= max_height:
            return font, lines
        size -= 4
    # Last-resort render — will clip, but at least won't hang.
    font = pick_font(font_path, minimum)
    return font, wrap_title(draw, title, font, max_width)[:2]


def compose(input_path, output_path, title, target_w, target_h, font_override):
    img = Image.open(input_path).convert("RGB")
    img = center_crop(img, target_w, target_h)
    img = draw_gradient_overlay(img)

    draw = ImageDraw.Draw(img)
    padding_x = 40
    padding_y_bottom = 32
    max_width = target_w - 2 * padding_x
    max_height = int(target_h * 0.4)

    # Pick a font path to use for sizing pass (fit_font_size needs a path).
    font_path = font_override
    if not font_path:
        for p in DEFAULT_FONT_CANDIDATES:
            if Path(p).exists():
                font_path = p
                break

    font, lines = fit_font_size(
        draw, title, font_path, max_width, max_height, start=56, minimum=28
    )

    # Render lines bottom-up.
    line_h = draw.textbbox((0, 0), "汉A", font=font)[3]
    total_h = int(line_h * 1.25 * len(lines))
    y = target_h - padding_y_bottom - total_h
    for line in lines:
        # Soft shadow for extra contrast on photographic backgrounds.
        draw.text((padding_x + 2, y + 2), line, font=font, fill=(0, 0, 0, 160))
        draw.text((padding_x, y), line, font=font, fill=(255, 255, 255, 255))
        y += int(line_h * 1.25)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "PNG", optimize=True)


def parse_size(s):
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception:
        raise argparse.ArgumentTypeError(f"invalid --size {s!r}, expected e.g. 900x383")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--size", type=parse_size, default=(900, 383))
    ap.add_argument("--font", default=None)
    args = ap.parse_args()

    compose(
        args.input, args.output, args.title,
        args.size[0], args.size[1], args.font,
    )
    print(args.output)


if __name__ == "__main__":
    main()
