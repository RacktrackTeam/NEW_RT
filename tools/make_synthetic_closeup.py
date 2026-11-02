"""Render a synthetic close-up of a device label with a printed serial, asset tag, device label and cable label, for the proof of the
close-up reading when no picture of the vdo1 run shows a serial (none does: the video is too small for label text).

    _cache/venv/bin/python tools/make_synthetic_closeup.py <out.jpg> "<line>" ["<line>" ...]
"""
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf")


def font(size):
    for f in FONTS:
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main(out, lines):
    W, H = 1600, 160 + 120 * len(lines)
    img = Image.new("RGB", (W, H), (58, 60, 64))                       # the dark faceplate around the label
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle([60, 50, W - 60, H - 50], radius=18, fill=(246, 246, 240), outline=(30, 30, 30), width=4)
    f = font(72)
    for i, t in enumerate(lines):
        dr.text((120, 90 + 120 * i), t, fill=(18, 18, 18), font=f)
    img = img.rotate(1.5, resample=Image.BICUBIC, fillcolor=(58, 60, 64)).filter(ImageFilter.GaussianBlur(0.8))   # a hand-held photo: a little tilt and blur
    img.save(out, quality=90)
    print(out, img.size)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
