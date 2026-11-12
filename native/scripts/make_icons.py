"""The app icon and the splash for iOS and Android from RackTrack's own logo (mobile/img/logo.jpg, the mark dark_mobile and
RT-EOS use: the silver R on black). python3 scripts/make_icons.py"""
import pathlib
from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent.parent
LOGO = HERE.parent / "mobile" / "img" / "logo.jpg"


def icon(size, round_=False):
    im = Image.open(LOGO).convert("RGB").resize((size, size), Image.LANCZOS)
    if round_:
        m = Image.new("L", (size, size), 0); ImageDraw.Draw(m).ellipse([0, 0, size, size], fill=255); out = Image.new("RGBA", (size, size)); out.paste(im, mask=m); return out
    return im


def splash(size):
    """The logo, rounded, small in the middle of a sage ground (the app's page colour)."""
    im = Image.new("RGB", (size, size), (233, 236, 232)); m = int(size * 0.075)
    tile = icon(m * 4).resize((m, m), Image.LANCZOS)
    mask = Image.new("L", (m, m), 0); ImageDraw.Draw(mask).rounded_rectangle([0, 0, m, m], radius=int(m * 0.22), fill=255)
    im.paste(tile, ((size - m) // 2, (size - m) // 2), mask); return im


ios = HERE / "ios/App/App/Assets.xcassets"
if ios.exists():
    icon(1024).save(ios / "AppIcon.appiconset/AppIcon-512@2x.png")
    for n in ("splash-2732x2732.png", "splash-2732x2732-1.png", "splash-2732x2732-2.png"):
        splash(2732).save(ios / "Splash.imageset" / n)
res = HERE / "android/app/src/main/res"
if res.exists():
    for folder, px in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
        d = res / f"mipmap-{folder}"; d.mkdir(exist_ok=True)
        icon(px).save(d / "ic_launcher.png"); icon(px, round_=True).save(d / "ic_launcher_round.png")
        s = int(px * 2.25); fg = Image.new("RGBA", (s, s), (0, 0, 0, 0)); inner = icon(int(s * 0.6)); fg.paste(inner, ((s - inner.size[0]) // 2, (s - inner.size[1]) // 2)); fg.save(d / "ic_launcher_foreground.png")
    for p in res.glob("drawable*/splash.png"):
        w, h = Image.open(p).size; big = max(w, h); sp = splash(big).crop(((big - w) // 2, (big - h) // 2, (big - w) // 2 + w, (big - h) // 2 + h)); sp.save(p)
    bg = res / "values/ic_launcher_background.xml"
    if bg.exists():
        bg.write_text('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n    <color name="ic_launcher_background">#000000</color>\n</resources>\n')
print("icons written")
