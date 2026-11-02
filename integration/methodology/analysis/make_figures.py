"""Figures for the capture analysis (reads capture_metrics.json, writes ../figures/*.png).  Run after capture_analysis.py."""
import json
import sys
from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE.parent / "figures"
OUT.mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / "code"))
from rack_stitcher import crop  # noqa: E402

M = json.loads((HERE / "capture_metrics.json").read_text())
NAMES = ["vdo1", "vdo2", "vdo3", "vdo4"]
COLOR = dict(zip(NAMES, ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]))        # categorical slots 1-4, fixed order
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e5e4e0", "#fcfcfb"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "text.color": INK, "axes.facecolor": SURFACE,
                     "figure.facecolor": SURFACE, "axes.spines.top": False, "axes.spines.right": False})
short = lambda m: m["name"].split("_")[0].split(".")[0]


def base(title, xlabel, ylabel, size=(8.6, 3.5)):
    fig, ax = plt.subplots(figsize=size)
    ax.set_title(title, loc="left", fontsize=11.5, fontweight="bold", color=INK, pad=10)
    ax.set_xlabel(xlabel); ax.set_ylabel(ylabel)
    ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    return fig, ax


def label_end(ax, x, y, text, color, dy=0.0):
    ax.annotate(text, (x, y), xytext=(6, dy), textcoords="offset points", color=INK, fontsize=9, va="center", fontweight="bold")
    ax.plot([x], [y], "o", ms=5, color=color, mec=SURFACE, mew=1.5)


def save(fig, name):
    fig.tight_layout(); fig.savefig(OUT / name, dpi=200); plt.close(fig); print("saved", name)


# 1. progress through the rack
fig, ax = base("How far down the rack the camera is, over time", "time (s)", "share of the rack covered")
for m in M:
    s = m["series"]; t = np.array(s["t"]); p = np.array(s["progress"]); p = (p - p.min()) / (p.max() - p.min())
    ax.plot(t, p, color=COLOR[short(m)], lw=2, label=f"{short(m)}  {t[-1]:.0f} s")
ax.legend(loc="lower right", frameon=False, fontsize=9)
ax.set_xlim(0, 28); ax.set_ylim(0, 1.04); ax.set_yticks([0, .25, .5, .75, 1]); ax.set_yticklabels(["top", "", "", "", "bottom"])
save(fig, "capture_progress.png")

# 2. speed
fig, ax = base("Camera speed (screen-heights per second; lower = sharper frames)", "time (s)", "speed")
ax.axhspan(0.25, 0.7, color=GRID, alpha=0.55, lw=0); ax.text(30.5, 0.6, "too fast (warning W03 above 0.25)", ha="right", color=MUTED, fontsize=8.5)
ax.axhline(0.15, color=MUTED, lw=1, ls=(0, (4, 3))); ax.text(30.5, 0.165, "recommended: 0.15 or less (best videos: 0.07 to 0.09)", ha="right", color=MUTED, fontsize=8.5)
for m in M:
    s = m["series"]; t = np.array(s["t"]); v = np.array(s["speed_smooth"])
    ax.plot(t, v, color=COLOR[short(m)], lw=2, label=f"{short(m)}  median {m['speed_median_fh_s']:.2f}")
ax.legend(loc="upper right", bbox_to_anchor=(1.0, 0.82), frameon=False, fontsize=9)
ax.set_xlim(0, 31); ax.set_ylim(0, 0.7)
save(fig, "capture_speed.png")

# 3. rack size in the frame
fig, ax = base("Rack size in the frame (1.0 = size at the start)", "share of the rack covered (top to bottom)", "relative size")
for m in M:
    s = m["series"]; p = np.array(s["progress"]); p = (p - p.min()) / (p.max() - p.min()); z = 1 / np.array(s["scale"])
    ax.plot(p, z, color=COLOR[short(m)], lw=2, label=f"{short(m)}  x{z[-1]:.2f} at the end")
ax.legend(loc="upper left", bbox_to_anchor=(0.0, 0.86), frameon=False, fontsize=9)
ax.axhline(2.0, color=MUTED, lw=1, ls=(0, (4, 3))); ax.text(0.01, 2.04, "warning W06 above x2.0 (largest / smallest)", color=MUTED, fontsize=8.5)
ax.set_xlim(0, 1.0); ax.set_ylim(0.9, 2.2); ax.set_xticks([0, .5, 1]); ax.set_xticklabels(["top", "middle", "bottom"])
save(fig, "capture_scale.png")

# 4. sideways drift and roll
fig, axes = plt.subplots(1, 2, figsize=(8.6, 3.3))
for ax, key, title, unit in [(axes[0], "sideways", "Sideways drift", "frame widths"), (axes[1], "roll", "Apparent rotation", "degrees")]:
    ax.set_title(title, loc="left", fontsize=11.5, fontweight="bold", pad=10); ax.grid(axis="y", color=GRID, lw=0.8); ax.set_axisbelow(True)
    for m in M:
        s = m["series"]; t = np.array(s["t"]); v = np.array(s[key]); ax.plot(t / t[-1], v, color=COLOR[short(m)], lw=2)
    ax.set_xlabel("share of the video"); ax.set_ylabel(unit)
axes[0].text(0.02, 0.9 * axes[0].get_ylim()[1] if axes[0].get_ylim()[1] > 0 else 0, "", fontsize=8)
for m in M:
    s = m["series"]; axes[0].plot([], [], color=COLOR[short(m)], lw=2, label=short(m))
axes[1].legend(loc="upper left", frameon=False, ncol=2, fontsize=9)
save(fig, "capture_drift_roll.png")

# 5. sharpness relative to the video's own median
fig, ax = base("Frame sharpness relative to the video's median", "share of the video", "sharpness / median")
ax.axhline(0.6, color=MUTED, lw=1, ls=(0, (4, 3))); ax.text(0.99, 0.62, "frames below 60% of their neighbours count as blurred (W05)", ha="right", color=MUTED, fontsize=8.5)
for m in M:
    s = m["series"]; t = np.array(s["t"]); v = np.array(s["sharpness"]); v = v / np.median(v)
    k = max(3, int(len(v) / 40)); sm = np.convolve(v, np.ones(k) / k, mode="same")
    ax.plot(t / t[-1], sm, color=COLOR[short(m)], lw=1.8)
ax.set_xlim(0, 1.0); ax.set_ylim(0.3, 1.9)
for m in M:
    ax.plot([], [], color=COLOR[short(m)], lw=2, label=f"{short(m)}  sharpest-10% / median = {np.percentile(m['series']['sharpness'], 90) / np.median(m['series']['sharpness']):.2f}")
ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=2)
save(fig, "capture_sharpness.png")

# 6. speed vs readable port numbers (the result of the capture)
legible = {"vdo1": 7, "vdo2": 0, "vdo3": 21, "vdo4": 25}         # port numbers read of 36 in the acceptance test (report section 9)
fig, ax = base("Slower capture, more readable port numbers", "median camera speed (screen-heights per second)", "port numbers read (of 36)", size=(7.6, 3.6))
for m in M:
    n = short(m); ax.plot(m["speed_median_fh_s"], legible[n], "o", ms=11, color=COLOR[n], mec=SURFACE, mew=2)
    ax.annotate(f"{n}  ({m['resolution']})", (m["speed_median_fh_s"], legible[n]), xytext=(10, 4), textcoords="offset points", fontsize=9, fontweight="bold")
ax.set_xlim(0, 0.42); ax.set_ylim(-2, 30)
ax.text(0.41, 28.3, "vdo1 is 720p, which also limits detail", ha="right", color=MUTED, fontsize=8.5)
save(fig, "capture_speed_vs_legibility.png")

# 7. frames strip: what each video looked like at 0, 25, 50, 75, 100 %
files = {"vdo1": "vdo1.mp4", "vdo2": "vdo2.MOV", "vdo3": "vdo3_60fps.MOV", "vdo4": "vdo4_30fps.MOV"}
rows, widths = [], {}
for n, f in files.items():
    cap = cv2.VideoCapture(str(ROOT / "sample_vdo" / f)); frames = []
    while True:
        ok, fr = cap.read()
        if not ok: break
        frames.append(cv2.resize(fr, (480, 270)) if fr.shape[1] >= fr.shape[0] else cv2.resize(fr, (270, 480)))
    cap.release()
    picks = [int(q * (len(frames) - 1)) for q in (0, .25, .5, .75, 1)]
    tiles = []
    for q, i in zip((0, 25, 50, 75, 100), picks):
        t = frames[i].copy(); cv2.rectangle(t, (0, 0), (92, 24), (252, 252, 251), -1); cv2.putText(t, f"{n} {q}%", (5, 17), 0, 0.5, (11, 11, 11), 1, cv2.LINE_AA); tiles.append(t)
    rows.append(np.hstack([cv2.copyMakeBorder(t, 0, 0, 0, 4, cv2.BORDER_CONSTANT, value=(251, 252, 252)) for t in tiles]))
    # how much of the frame width the rack fills (dark cabinet region), at the quarter points
    ws = []
    for i in picks[1:4]:
        fr = frames[i]; x0, y0, x1, y1 = crop.find_rack_bbox(fr); ws.append((x1 - x0) / fr.shape[1])
    widths[n] = ws
cv2.imwrite(str(OUT / "capture_frames.jpg"), np.vstack([cv2.copyMakeBorder(r, 0, 4, 0, 0, cv2.BORDER_CONSTANT, value=(251, 252, 252)) for r in rows]), [cv2.IMWRITE_JPEG_QUALITY, 90]); print("saved capture_frames.jpg")
print("dark-cabinet width as a share of the frame width at 25/50/75%:", {k: [round(x, 2) for x in v] for k, v in widths.items()})
json.dump(widths, open(HERE / "rack_width_share.json", "w"))

# 8. sharp vs blurred frame: same place in the rack, the sharpest and the blurriest frame within one second
def patch_pair(name, file, at=0.5):
    cap = cv2.VideoCapture(str(ROOT / "sample_vdo" / file)); fr = []
    while True:
        ok, f = cap.read()
        if not ok: break
        fr.append(f)
    cap.release()
    m = next(x for x in M if short(x) == name); sc = np.array(m["series"]["sharpness"]); t = np.array(m["series"]["t"])
    c = int(at * len(fr)); fps = (len(fr) - 1) / t[-1]; lo, hi = max(0, c - int(fps / 2)), min(len(fr), c + int(fps / 2))
    best, worst = lo + int(np.argmax(sc[lo:hi])), lo + int(np.argmin(sc[lo:hi]))
    H, W = fr[0].shape[:2]; y0, x0 = int(H * 0.32), int(W * 0.30); ph, pw = int(H * 0.22), int(W * 0.26)
    out = []
    for lab, i in (("sharpest frame", best), ("blurriest frame", worst)):
        p = cv2.resize(fr[i][y0:y0 + ph, x0:x0 + pw], None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        cv2.rectangle(p, (0, 0), (300, 30), (252, 252, 251), -1); cv2.putText(p, f"{name}: {lab} ({sc[i]:.0f})", (6, 21), 0, 0.6, (11, 11, 11), 1, cv2.LINE_AA); out.append(p)
    return np.hstack([out[0], np.full((out[0].shape[0], 6, 3), 252, np.uint8), out[1]])
a, b = patch_pair("vdo2", files["vdo2"]), patch_pair("vdo4", files["vdo4"])
w = max(a.shape[1], b.shape[1]); pad = lambda x: cv2.copyMakeBorder(x, 0, 6, 0, w - x.shape[1], cv2.BORDER_CONSTANT, value=(252, 252, 251))
cv2.imwrite(str(OUT / "capture_blur_patches.jpg"), np.vstack([pad(a), pad(b)]), [cv2.IMWRITE_JPEG_QUALITY, 92]); print("saved capture_blur_patches.jpg")

# 9. brightness over time (auto-exposure)
fig, ax = base("Brightness over time (relative to the video's median)", "share of the video", "brightness / median")
for m in M:
    s = m["series"]; t = np.array(s["t"]); v = np.array(s["brightness"]); v = v / np.median(v)
    ax.plot(t / t[-1], v, color=COLOR[short(m)], lw=1.8)
for m in M:
    ax.plot([], [], color=COLOR[short(m)], lw=2, label=f"{short(m)}  range {m['bright_range_pct']:.0f}% of median")
ax.legend(loc="upper left", frameon=False, fontsize=8.5, ncol=2); ax.set_xlim(0, 1); ax.set_ylim(0.4, 1.9)
save(fig, "capture_brightness.png")

# 10. framing guide from real frames: start and end
def frame_at(file, q):
    cap = cv2.VideoCapture(str(ROOT / "sample_vdo" / file)); fr = []
    while True:
        ok, f = cap.read()
        if not ok: break
        fr.append(f)
    cap.release(); return fr[int(q * (len(fr) - 1))]
def card(img, label, good):
    img = cv2.resize(img, (640, 360)); col = (122, 175, 27) if good else (72, 73, 227)      # BGR green / red
    img = cv2.copyMakeBorder(img, 0, 0, 0, 0, cv2.BORDER_CONSTANT)
    cv2.rectangle(img, (0, 0), (639, 359), col, 8)
    cv2.rectangle(img, (0, 318), (639, 359), col, -1); cv2.putText(img, label, (12, 345), 0, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
    return img
cards = [card(frame_at("vdo4_30fps.MOV", 0.0), "OK start: lid + wall above, front faces camera", True),
         card(frame_at("vdo2.MOV", 0.0), "Avoid: looking down at the lid top", False),
         card(frame_at("vdo4_30fps.MOV", 1.0), "OK end: base and floor in view", True),
         card(frame_at("vdo1.mp4", 1.0), "Avoid: stops with the base at the edge", False)]
gap = np.full((360, 12, 3), 252, np.uint8)
cv2.imwrite(str(OUT / "guide_framing.jpg"), np.vstack([np.hstack([cards[0], gap, cards[1]]), np.full((12, 1292, 3), 252, np.uint8), np.hstack([cards[2], gap, cards[3]])]), [cv2.IMWRITE_JPEG_QUALITY, 90]); print("saved guide_framing.jpg")
