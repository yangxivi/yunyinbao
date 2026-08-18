#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成云印宝服务端 F.ico 和客户端 K.ico 图标。"""
from PIL import Image, ImageDraw, ImageFont
import os

OUT_DIR = r"D:\云印宝"

SIZES = [16, 32, 48, 64, 128, 256]


def _find_font(size: int):
    """找一个 Windows 系统字体。"""
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]
    for c in candidates:
        if os.path.exists(c):
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_rounded(draw, box, radius, fill):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def make_letter(letter: str, bg_color: tuple) -> Image.Image:
    """单个字母的 256x256 图（用于导出多分辨率）。"""
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 圆角矩形背景
    pad = 8
    box = (pad, pad, size - pad, size - pad)
    draw.rounded_rectangle(box, radius=48, fill=bg_color)

    # 白色字母，居中
    font = _find_font(190)
    bbox = draw.textbbox((0, 0), letter, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1] - 8
    # 描边让字母更醒目
    draw.text((x, y), letter, font=font, fill=(255, 255, 255, 255),
              stroke_width=4, stroke_fill=(255, 255, 255, 255))
    return img


def build_ico(letter: str, bg_color: tuple, out_path: str):
    base = make_letter(letter, bg_color)
    frames = [base.resize((s, s), Image.LANCZOS) for s in SIZES]
    base.save(out_path, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"saved: {out_path}  ({os.path.getsize(out_path)} bytes)")


if __name__ == "__main__":
    # F → 蓝色（服务端）
    build_ico("F", (30, 111, 255, 255), os.path.join(OUT_DIR, "F.ico"))
    # K → 绿色（客户端）
    build_ico("K", (103, 194, 58, 255), os.path.join(OUT_DIR, "K.ico"))
    print("done")
