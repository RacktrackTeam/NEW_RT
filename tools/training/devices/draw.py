"""Device pictures for the step 3 pages: each device a tinted band with its height in units, the photo dimmed outside the
rack (build_device_report.py and build_edge_review.py draw with these)."""
import pathlib
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
import device_logic as DL   # noqa: E402

_FONT = {}


def font(px):
    if px not in _FONT:
        for p in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/HelveticaNeue.ttc", "DejaVuSans-Bold.ttf"):
            try:
                f = ImageFont.truetype(p, px)
                try:
                    f.set_variation_by_name("Bold")
                except Exception:  # noqa: BLE001 - not a variable font
                    pass
                _FONT[px] = f
                break
            except OSError:
                continue
        else:
            _FONT[px] = ImageFont.load_default()
    return _FONT[px]


def rack_crop(rec, pad_u=0.8):
    geo = DL.Geo(rec["rows"], (rec["height"], rec["width"]))
    return (max(0, int(geo.x0 - 0.15 * geo.W)), max(0, int(geo.ys[0] - pad_u * geo.h)),
            min(rec["width"], int(geo.x1 + 0.15 * geo.W)), min(rec["height"], int(geo.ys[-1] + pad_u * geo.h)))


TONES = [(29, 95, 207), (0, 140, 150)]   # two alternating device tones (RGB)


def draw_devices(rec, devices, wrong=(), crop=None, side=900, badge=True):
    """Each device (a list of unit indices) as a tinted band with a white outline and a tag with its height in units;
    the photo dimmed outside the rack. wrong: device indices whose tag turns amber."""
    img = ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB")
    geo = DL.Geo(rec["rows"], (img.height, img.width))
    x0c, y0c, x1c, y1c = crop or (0, 0, img.width, img.height)
    img = img.crop((x0c, y0c, x1c, y1c))
    s = min(1.0, side / max(img.size)); img = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
    X = lambda v: (v - x0c) * s; Y = lambda v: (v - y0c) * s
    rx0, rx1, ry0, ry1 = X(geo.x0), X(geo.x1), Y(geo.ys[0]), Y(geo.ys[-1])
    base = np.asarray(img).astype(np.float32)
    mask = np.zeros(base.shape[:2], bool); mask[max(0, int(ry0)):max(0, int(ry1)), max(0, int(rx0)):max(0, int(rx1))] = True
    base[~mask] *= 0.55
    img = Image.fromarray(base.astype(np.uint8)).convert("RGBA")
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    h = geo.h * s
    inset, lw = max(2.0, 0.07 * h), max(2, int(round(img.height / 450)))
    fpx = int(np.clip(0.42 * h, 10, 24)); f = font(fpx)
    for n, dev in enumerate(devices):
        ya, yb = Y(rec["rows"][dev[0]]["box"][1]) + inset, Y(rec["rows"][dev[-1]]["box"][3]) - inset
        if yb - ya < 3:
            continue
        r, g, b = TONES[n % 2]
        d.rounded_rectangle((rx0 + inset, ya, rx1 - inset, yb), radius=max(3, int(h * 0.12)), fill=(r, g, b, 52), outline=(255, 255, 255, 230), width=lw)
        tag = f"{len(dev)}U"
        tw = d.textbbox((0, 0), tag, font=f)[2] + fpx; th = min(yb - ya - 4, fpx * 1.5)
        tx, ty = rx0 + inset + 6, (ya + yb) / 2 - th / 2
        col = (232, 140, 20, 245) if n in wrong else (14, 20, 30, 215)
        d.rounded_rectangle((tx, ty, tx + tw, ty + th), radius=th / 2.5, fill=col)
        d.text((tx + tw / 2, ty + th / 2), tag, font=f, fill=(255, 255, 255, 255), anchor="mm")
    if badge:
        bf = font(int(np.clip(img.height / 34, 13, 26))); label = f"{len(devices)} devices"
        bw = d.textbbox((0, 0), label, font=bf)[2] + bf.size * 1.4; bh = bf.size * 1.6
        bx = min(max(4, rx0), img.width - bw - 4)
        by = ry0 - bh - 6 if ry0 - bh - 6 >= 4 else ry0 + 6
        d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=bh / 2, fill=(14, 20, 30, 225))
        d.text((bx + bw / 2, by + bh / 2), label, font=bf, fill=(255, 255, 255, 255), anchor="mm")
    return Image.alpha_composite(img, lay).convert("RGB")
