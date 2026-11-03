"""
more_datailed_1.py - rail-first, end-to-end rack-unit map (classical CV, no ML model).

Builds on rack_units.py and adds:
  1. RACK RAILS   - left/right rail strips (outer edge, inner edge, hole column) and the
                    rack's usable vertical extent (top/bottom limits from frame lines / rail holes).
  2. FULL U GRID  - every U slot from the top of the rack to the bottom, numbered EIA-style
                    (U1 = lowest slot), each with a status: device / empty / inferred / unknown.
  3. DEVICES      - each device with its height in U, the U range it occupies, and a
                    per-unit partition (a 4U device is split into its 4 individual units).

Usage:
    python more_datailed_1.py photo.jpg [more.jpg ...] [--out out] [--debug]
Outputs (per image):
    out/<name>_detailed.png   original photo with rails, U grid, devices and unit partitions
    out/<name>_detailed.json  rails, slots, devices (all coordinates in original-image pixels)
"""
from __future__ import annotations

import argparse
import json
import os

import cv2
import numpy as np

import rack_units as R

SIDE_EDGE_OCCUPIED = 0.5     # faceplate side edges seen on >= 50% of a slot's rows -> something mounted
GRID_SNAP = 0.15             # tolerance (in U) when snapping rack limits onto the U grid


# =====================================================================================
# helpers
# =====================================================================================
def _vertical_edge_mask(gray, U):
    g = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 1.2)
    gx = np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0))
    gy = np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1))
    return ((gx > 1.5 * gy) & (gx > np.percentile(gx, 80))).astype(np.uint8)


class Mapper:
    """work (rectified, resized) coordinates -> original image coordinates."""

    def __init__(self, Hm, scale):
        self.Hinv, self.scale = np.linalg.inv(Hm), scale

    def pts(self, xy):
        p = cv2.perspectiveTransform(np.float32(xy).reshape(-1, 1, 2), self.Hinv).reshape(-1, 2)
        return p / self.scale

    def box(self, x0, y0, x1, y1):
        poly = self.pts([[x0, y0], [x1, y0], [x1, y1], [x0, y1]])
        bbox = [round(float(v), 1) for v in (poly[:, 0].min(), poly[:, 1].min(),
                                              poly[:, 0].max(), poly[:, 1].max())]
        return bbox, poly.round(1).tolist()


# =====================================================================================
# 1. Rails and rack extent
# =====================================================================================
def detect_rails(gray, xa, xb, U, y0, y1, hole_cols):
    """Each rail = vertical strip just outside/at the faceplate ends.
    outer edge : strongest long vertical edge slightly outside the faceplate end
    inner edge : strongest long vertical edge just inside it (EIA opening is 17.75" of 19",
                 so the inner edge is expected ~0.625" = 3.3% of the width inside)
    holes      : the rail-hole column found by rack_units (kept only if it lies on the rail)."""
    sp = xb - xa
    vm = _vertical_edge_mask(gray, U)
    vm = cv2.morphologyEx(vm, cv2.MORPH_OPEN, np.ones((max(3, int(U / 3)), 1), np.uint8))
    prof = vm[int(y0):int(y1)].mean(0)

    def strongest(lo, hi, default):
        lo, hi = int(max(0, lo)), int(min(len(prof), hi))
        if hi - lo < 2:
            return default, 0.0
        k = lo + int(np.argmax(prof[lo:hi]))
        return (float(k), float(prof[k])) if prof[k] >= 0.25 else (default, float(prof[k]))

    rails = []
    for side, edge, sgn, hc in (("left", xa, 1, hole_cols[0]), ("right", xb, -1, hole_cols[1])):
        # rail flanges are ~5/8"-1" wide, so the outer edge is close to the faceplate end;
        # a wider window would grab the cabinet/post edge instead
        out_lo, out_hi = sorted((edge - sgn * 0.045 * sp, edge + sgn * 0.005 * sp))
        in_lo, in_hi = sorted((edge + sgn * 0.02 * sp, edge + sgn * 0.06 * sp))
        outer, s_out = strongest(out_lo, out_hi, edge - sgn * 0.01 * sp)
        inner, s_in = strongest(in_lo, in_hi, edge + sgn * 0.033 * sp)
        lo, hi = sorted((outer, inner))
        hole = hc if (hc is not None and lo - 0.02 * sp <= hc <= hi + 0.02 * sp) else None
        rails.append(dict(side=side, x_outer=outer, x_inner=inner, x_holes=hole,
                          y_top=float(y0), y_bottom=float(y1),
                          edge_strength=round(min(s_out, s_in), 2)))
    return rails


def hole_ys(gray, xa, xb, U, cols):
    ys = []
    for (x0, x1), xc, k in zip(R._rail_strips(xa, xb), cols, getattr(cols, "scales", (0.06, 0.06))):
        if xc is None:
            continue
        bl = R._blobs(gray, x0, x1, 0, gray.shape[0], U, k)
        ys += [p[0] for p in bl if abs(p[1] - xc) < 0.05 * U]
    return np.sort(np.array(ys, float))


def rack_extent(m, gray, xa, xb, U, phase, stack_top, stack_bot, H):
    """Top/bottom of the U grid, snapped onto whole slots.

    A horizontal frame line (an edge running past the faceplate on both sides) ends the
    grid only if the rails stop there, i.e. the rail strips show no long vertical edges
    just beyond it.  A top cover or door edge with the rail continuing behind it does not
    count.  Without a real end the grid runs to the image border."""
    sp = xb - xa
    vm = _vertical_edge_mask(gray, U)
    vm = cv2.morphologyEx(vm, cv2.MORPH_OPEN, np.ones((max(3, int(U / 3)), 1), np.uint8))
    strips = [(int(max(0, xa - 0.05 * sp)), int(xa + 0.06 * sp)),
              (int(xb - 0.06 * sp), int(min(m.shape[1], xb + 0.05 * sp)))]

    def rails_continue(y0, y1):
        y0, y1 = int(max(0, y0)), int(min(H, y1))
        if y1 - y0 < 3:
            return False
        return all(vm[y0:y1, a:b].max(1).mean() >= 0.6 for a, b in strips)

    frame = [y for y in range(1, H - 1) if R.is_frame_line(m, y, xa, xb)]
    top_lim, top_src = 0.0, "image edge (rail continues)"
    for y in sorted((y for y in frame if y < stack_top - 0.1 * U), reverse=True):
        if not rails_continue(y - 0.9 * U, y - 0.1 * U):
            top_lim, top_src = float(y), "rack frame"
            break
    bot_lim, bot_src = float(H), "image edge (rail continues)"
    for y in sorted(y for y in frame if y > stack_bot + 0.1 * U):
        if not rails_continue(y + 0.1 * U, y + 0.9 * U):
            bot_lim, bot_src = float(y), "rack frame"
            break

    top = phase + np.ceil((top_lim - GRID_SNAP * U - phase) / U) * U
    bot = phase + np.floor((bot_lim + GRID_SNAP * U - phase) / U) * U
    while top < -0.5:                       # whole slots inside the image only
        top += U
    while bot > H + 0.5:
        bot -= U
    top = min(top, phase + np.round((stack_top - phase) / U) * U)      # never cut the device stack
    bot = max(bot, phase + np.round((stack_bot - phase) / U) * U)
    return float(top), float(bot), top_src, bot_src


# =====================================================================================
# 2-3. Slots, devices, partitions
# =====================================================================================
def build_map(path, debug=False):
    res, d = R.analyse(path, debug)
    gray, m, U, xa, xb = d["gray"], d["m"], d["U"], d["xa"], d["xb"]
    H, W = gray.shape
    mp = Mapper(d["Hm"], d["scale"])
    phase = d["phase"]

    blocks = []     # (kind, top, bottom, units, seg) in work coordinates
    for (t, bt), seg in zip(d["work_y"], res.segments):
        blocks.append(dict(kind=seg["kind"], top=t, bottom=bt, units=seg["units"], seg=seg))
    stack = [b for b in blocks if b["kind"] != "frame"]
    stack_top = min((b["top"] for b in stack), default=0.0)
    stack_bot = max((b["bottom"] for b in stack), default=float(H))

    top, bot, top_src, bot_src = rack_extent(m, gray, xa, xb, U, phase, stack_top, stack_bot, H)
    n_slots = int(round((bot - top) / U))
    rails = detect_rails(gray, xa, xb, U, top, bot, d["cols"])
    # interior span between the rails' inner edges
    x_in_l, x_in_r = rails[0]["x_inner"], rails[1]["x_inner"]

    # --- slot grid -------------------------------------------------------------------
    slots = []
    for i in range(n_slots):
        t = top + i * U
        slots.append(dict(index_from_top=i + 1, u=n_slots - i, top=t, bottom=t + U,
                          status="unknown", device=None, unit_of_device=None))

    def overlap(a0, a1, b0, b1):
        return max(0.0, min(a1, b1) - max(a0, b0))

    # assign detected blocks (largest overlap wins)
    owner = [None] * n_slots
    for bi, b in enumerate(stack):
        for s_i, s in enumerate(slots):
            ov = overlap(b["top"], b["bottom"], s["top"], s["bottom"])
            if ov >= 0.5 * U and (owner[s_i] is None or ov > owner[s_i][1]):
                owner[s_i] = (bi, ov)

    # unresolved slots: rail holes -> empty, faceplate side edges -> occupied (inferred)
    vm = _vertical_edge_mask(gray, U)
    sp = xb - xa

    def side_edges(t, b):
        def one(x):
            x0, x1 = int(max(0, x - 0.012 * sp)), int(min(W, x + 0.012 * sp + 1))
            return vm[int(t):int(b), x0:x1].max(1).mean() if x1 > x0 and b > t else 0.0
        return (one(xa) + one(xb)) / 2              # empty slots are low on both sides

    for s_i, s in enumerate(slots):
        if owner[s_i] is not None:
            continue
        if R.slot_is_empty(gray, xa, xb, s["top"], s["bottom"], U, d["cols"]):
            s["status"] = "empty"
        elif side_edges(s["top"] + 0.1 * U, s["bottom"] - 0.1 * U) >= SIDE_EDGE_OCCUPIED:
            s["status"] = "inferred"
        else:
            s["status"] = "unknown"

    # --- devices ---------------------------------------------------------------------
    devices = []

    def add_device(kind, slot_idx, conf, off_grid=False, det=None):
        """A device owns whole grid slots; its box is exactly those slots, so every U
        belongs to at most one device.  `det` keeps the raw detected extent."""
        units = len(slot_idx)
        dev = dict(id=f"D{len(devices) + 1}", kind=kind, units=units,
                   top=slots[slot_idx[0]]["top"], bottom=slots[slot_idx[-1]]["bottom"],
                   det_top=det[0] if det else None, det_bottom=det[1] if det else None,
                   confidence=conf, off_grid=off_grid, unit_cells=[])
        for k, s_i in enumerate(slot_idx):
            dev["unit_cells"].append(dict(unit=k + 1, u=slots[s_i]["u"],
                                          top=slots[s_i]["top"], bottom=slots[s_i]["bottom"]))
            slots[s_i]["device"], slots[s_i]["unit_of_device"] = dev["id"], f"{k + 1}/{units}"
            slots[s_i]["status"] = kind            # device | inferred | irregular
        devices.append(dev)

    def runs_of(idx):
        out, cur = [], []
        for s_i in sorted(idx):
            if cur and s_i != cur[-1] + 1:
                out.append(cur)
                cur = []
            cur.append(s_i)
        return out + ([cur] if cur else [])

    for bi, b in enumerate(stack):
        mine = [s_i for s_i in range(n_slots) if owner[s_i] and owner[s_i][0] == bi]
        if b["kind"] == "empty":
            for s_i in mine:
                slots[s_i]["status"] = "empty"
            continue
        if b["kind"] not in ("device", "irregular"):
            continue
        for run in runs_of(mine):
            off = bool(abs(b["top"] - slots[run[0]]["top"]) > 0.2 * U or b["seg"]["off_grid"])
            if b["kind"] == "device":
                conf = "high" if b["seg"]["confidence"] >= 0.7 else "medium"
                add_device("device", run, conf, off, det=(b["top"], b["bottom"]))
            else:                                   # height is not n*U: still owns whole slots
                add_device("irregular", run, "low", off, det=(b["top"], b["bottom"]))

    def edge_at(y):
        w = max(1, int(0.06 * U))
        seg = d["b"][max(0, int(y) - w):int(y) + w + 1]
        return float(seg.max()) if len(seg) else 0.0

    # consecutive inferred slots -> one inferred device (no internal boundary visible)
    i = 0
    while i < n_slots:
        if slots[i]["status"] == "inferred":
            j = i
            # stop at a U line that carries an edge: separate stacked boxes, not one tall one
            while j + 1 < n_slots and slots[j + 1]["status"] == "inferred" and \
                    edge_at(slots[j]["bottom"]) < R.GRID_SPLIT_MIN:
                j += 1
            add_device("inferred", list(range(i, j + 1)), "low")
            i = j + 1
        else:
            i += 1
    devices.sort(key=lambda dv: dv["top"])
    for k, dv in enumerate(devices):                       # renumber top-down
        old, new = dv["id"], f"D{k + 1}"
        dv["id"] = new
        for s in slots:
            if s["device"] == old:
                s["device"] = "tmp" + new
    for s in slots:
        if s["device"]:
            s["device"] = s["device"].replace("tmp", "")

    # --- convert to original-image coordinates ---------------------------------------
    for r in rails:
        lo, hi = sorted((r["x_outer"], r["x_inner"]))
        r["bbox"], r["polygon"] = mp.box(lo, r["y_top"], hi, r["y_bottom"])
        r["holes_line"] = mp.pts([[r["x_holes"], r["y_top"]], [r["x_holes"], r["y_bottom"]]]).round(1).tolist() \
            if r["x_holes"] is not None else None
        for k in ("x_outer", "x_inner", "x_holes", "y_top", "y_bottom"):
            r.pop(k)
    for s in slots:
        s["bbox"], s["polygon"] = mp.box(x_in_l, s["top"], x_in_r, s["bottom"])
        s.pop("top"), s.pop("bottom")
    for dv in devices:
        dv["u_top"] = dv["unit_cells"][0]["u"]
        dv["u_bottom"] = dv["unit_cells"][-1]["u"]
        dv["bbox"], dv["polygon"] = mp.box(xa, dv["top"], xb, dv["bottom"])
        if dv["det_top"] is not None:
            dv["detected_bbox"], dv["detected_polygon"] = mp.box(xa, dv["det_top"], xb, dv["det_bottom"])
        else:
            dv["detected_bbox"], dv["detected_polygon"] = None, None
        dv.pop("det_top"), dv.pop("det_bottom")
        for c in dv["unit_cells"]:
            c["bbox"], c["polygon"] = mp.box(xa, c["top"], xb, c["bottom"])
            c.pop("top"), c.pop("bottom")
        dv.pop("top"), dv.pop("bottom")

    out = dict(
        image=os.path.basename(path),
        u_pitch_px=round(U / d["scale"], 2),
        rotation_deg=round(float(d["rot"]), 2),
        rack=dict(total_units=n_slots,
                  top_limit_from=top_src, bottom_limit_from=bot_src,
                  numbering="U1 = lowest slot of the grid" + ("" if bot_src == "rack frame" else
                                                          " (rack bottom not confirmed by a frame line)"),
                  bbox=mp.box(xa, top, xb, bot)[0]),
        rails=rails,
        summary=dict(devices=len([x for x in devices if x["kind"] == "device"]),
                     inferred=len([x for x in devices if x["kind"] == "inferred"]),
                     irregular=len([x for x in devices if x["kind"] == "irregular"]),
                     used_units=sum(s["status"] in ("device", "inferred", "irregular") for s in slots),
                     empty_units=sum(s["status"] == "empty" for s in slots),
                     unknown_units=sum(s["status"] == "unknown" for s in slots)),
        devices=devices,
        slots=slots,
    )
    return out, d["original"]


# =====================================================================================
# drawing
# =====================================================================================
DEV_COLORS = [(0, 200, 0), (255, 140, 0), (230, 0, 230), (0, 170, 255), (255, 60, 60), (0, 220, 220),
              (180, 105, 255), (60, 180, 255)]
KIND_STYLE = {"inferred": (0, 165, 255), "irregular": (0, 0, 255)}


def _poly(img, poly, color, th, closed=True):
    cv2.polylines(img, [np.int32(np.round(poly))], closed, color, th, cv2.LINE_AA)


def _dashed(img, p0, p1, color, th, dash=12):
    p0, p1 = np.float32(p0), np.float32(p1)
    n = max(1, int(np.linalg.norm(p1 - p0) / dash))
    for k in range(0, n, 2):
        a = p0 + (p1 - p0) * k / n
        b = p0 + (p1 - p0) * min(k + 1, n) / n
        cv2.line(img, tuple(np.int32(a)), tuple(np.int32(b)), color, th, cv2.LINE_AA)


def _label(img, text, org, color, fs, th, fg=(0, 0, 0)):
    (tw, tht), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
    x, y = int(org[0]), int(org[1])
    cv2.rectangle(img, (x, y - tht - th), (x + tw + 2 * th, y + base), color, -1)
    cv2.putText(img, text, (x + th, y), cv2.FONT_HERSHEY_SIMPLEX, fs, fg, th, cv2.LINE_AA)


# ---- clean rendering (Pillow) ---------------------------------------------------------
PANEL_BG = (20, 24, 28)
INK = (236, 240, 243)
INK_DIM = (140, 150, 158)
GRID = (255, 255, 255, 70)
RAIL = (120, 200, 210, 140)
KIND_RGB = {"device": [(46, 196, 182), (78, 168, 222)],      # alternate so neighbours differ
            "inferred": [(244, 162, 89)], "irregular": [(179, 136, 235)]}
FONT_PATHS = ["/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
              "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "C:/Windows/Fonts/segoeui.ttf"]


def _font(size):
    from PIL import ImageFont
    for fp in FONT_PATHS:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except OSError:
                pass
    return ImageFont.load_default(size=size)


def _dotted(d, p0, p1, fill, width, dash, gap):
    p0, p1 = np.float32(p0), np.float32(p1)
    L = float(np.linalg.norm(p1 - p0))
    if L < 1:
        return
    u = (p1 - p0) / L
    t = 0.0
    while t < L:
        a, b = p0 + u * t, p0 + u * min(t + dash, L)
        d.line([tuple(a), tuple(b)], fill=fill, width=width)
        t += dash + gap


def draw(img, out):
    """Clean overview: the photo carries only light tints and thin outlines; all text
    lives in a U ruler on the left, a slot list on the right and a header on top."""
    from PIL import Image, ImageDraw

    H0, W0 = img.shape[:2]
    disp_h = min(max(H0, 900), 1400)                  # readable size whatever the input
    s = disp_h / H0
    k = disp_h / 1000                                 # type/line scale
    photo = cv2.resize(img, (int(round(W0 * s)), disp_h),
                       interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
    ph, pw = photo.shape[:2]
    hh, lw, rw = int(78 * k), int(70 * k), int(330 * k)
    Wc, Hc = lw + pw + rw, hh + ph
    lw_ = max(1, int(round(2 * k)))

    # dim everything outside the rack so the rack reads first
    rb = out["rack"]["bbox"]
    rx0, ry0, rx1, ry1 = [int(round(v * s)) for v in rb]
    dim = (photo * 0.45).astype(np.uint8)
    keep = np.zeros(photo.shape[:2], bool)
    keep[max(0, ry0):max(0, ry1), max(0, rx0):max(0, rx1)] = True
    photo = np.where(keep[..., None], photo, dim)

    canvas = Image.new("RGB", (Wc, Hc), PANEL_BG)
    canvas.paste(Image.fromarray(cv2.cvtColor(photo, cv2.COLOR_BGR2RGB)), (lw, hh))
    ov = Image.new("RGBA", (Wc, Hc), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    def P(poly):
        return [(x * s + lw, y * s + hh) for x, y in poly]

    f_head, f_sub = _font(int(24 * k)), _font(int(15 * k))
    f_row, f_row_b, f_tag = _font(int(15 * k)), _font(int(15 * k)), _font(int(14 * k))

    # rails: faint tint only, so they never compete with the device outlines
    for r in out["rails"]:
        d.polygon(P(r["polygon"]), fill=RAIL[:3] + (28,))

    # device colours (alternating per kind so stacked neighbours are distinguishable)
    colour, count = {}, {}
    for dv in out["devices"]:
        pal = KIND_RGB.get(dv["kind"], KIND_RGB["device"])
        n = count.get(dv["kind"], 0)
        colour[dv["id"]] = pal[n % len(pal)]
        count[dv["kind"]] = n + 1

    # device tints, outlines, unit dividers
    for dv in out["devices"]:
        c = colour[dv["id"]]
        poly = np.float32(P(dv["polygon"]))
        ins = poly.copy()
        ins[[0, 1], 1] += 2 * k
        ins[[2, 3], 1] -= 2 * k
        ins[[0, 3], 0] += 2 * k
        ins[[1, 2], 0] -= 2 * k
        d.polygon([tuple(p) for p in ins], fill=c + (46,), outline=c + (235,), width=lw_)
        for cell in dv["unit_cells"][1:]:
            cp = P(cell["polygon"])
            _dotted(d, (cp[0][0] + 6 * k, cp[0][1]), (cp[1][0] - 6 * k, cp[1][1]), c + (170,),
                    max(1, lw_ - 1), 6 * k, 5 * k)
        # small ID tag in the top-left corner of the box
        tx, ty = ins[0][0] + 6 * k, ins[0][1] + 5 * k
        tb = d.textbbox((tx, ty), dv["id"], font=f_tag)
        d.rounded_rectangle((tb[0] - 5 * k, tb[1] - 3 * k, tb[2] + 5 * k, tb[3] + 3 * k),
                            radius=4 * k, fill=c + (235,))
        d.text((tx, ty), dv["id"], font=f_tag, fill=(16, 20, 24, 255))

    # U grid: faint lines between slots, across the rack interior
    slots = out["slots"]
    for sl in slots:
        q = P(sl["polygon"])
        d.line([q[0], q[1]], fill=GRID, width=1)
    if slots:
        q = P(slots[-1]["polygon"])
        d.line([q[3], q[2]], fill=GRID, width=1)

    canvas = Image.alpha_composite(canvas.convert("RGBA"), ov)
    d = ImageDraw.Draw(canvas)

    # left ruler + right slot list, one row per slot, level with the slot
    dev_by_id = {dv["id"]: dv for dv in out["devices"]}
    x_list = lw + pw + int(18 * k)
    for sl in slots:
        q = P(sl["polygon"])
        y0, y1 = min(p[1] for p in q), max(p[1] for p in q)
        yc = (y0 + y1) / 2
        st = sl["status"]
        occupied = st in ("device", "inferred", "irregular")
        # ruler
        d.line([(lw - int(14 * k), y0), (lw - 2, y0)], fill=INK_DIM, width=1)
        lab = f"U{sl['u']}"
        tb = d.textbbox((0, 0), lab, font=f_row_b)
        d.text((lw - int(18 * k) - (tb[2] - tb[0]), yc - (tb[3] - tb[1]) / 2 - tb[1]), lab,
               font=f_row_b, fill=INK if occupied else INK_DIM)
        # list row
        d.line([(lw + pw + 2, y0), (Wc - int(12 * k), y0)], fill=(44, 50, 56), width=1)
        sq = int(10 * k)
        x_id, x_txt, x_rng = x_list + sq + int(10 * k), x_list + sq + int(52 * k), Wc - int(16 * k)

        def put(x, txt, font, fill, right=False):
            tb = d.textbbox((0, 0), txt, font=font)
            d.text((x - (tb[2] - tb[0]) if right else x, yc - (tb[3] - tb[1]) / 2 - tb[1]), txt,
                   font=font, fill=fill)

        if occupied:
            dv = dev_by_id[sl["device"]]
            c = colour[dv["id"]]
            d.rectangle((x_list, yc - sq / 2, x_list + sq, yc + sq / 2), fill=c)
            unit, tot = sl["unit_of_device"].split("/")
            if unit == "1":
                kind = {"device": "device", "inferred": "inferred", "irregular": "uneven height"}[dv["kind"]]
                put(x_id, dv["id"], f_row_b, INK)
                desc = f"{dv['units']}U {kind}"
                put(x_txt, desc, f_row, INK)
                if dv["off_grid"]:
                    tb = d.textbbox((0, 0), desc, font=f_row)
                    put(x_txt + (tb[2] - tb[0]) + int(10 * k), "off-grid", f_tag, INK_DIM)
                rng = f"U{dv['u_top']}" if dv["units"] == 1 else f"U{dv['u_top']}-U{dv['u_bottom']}"
                put(x_rng, rng, f_row, INK_DIM, right=True)
            else:
                put(x_id, dv["id"], f_row, INK_DIM)
                put(x_txt, f"unit {unit} of {tot}", f_row, INK_DIM)
        else:
            d.rectangle((x_list, yc - sq / 2, x_list + sq, yc + sq / 2), outline=INK_DIM, width=1)
            put(x_id, "empty" if st == "empty" else "unknown", f_row, INK_DIM)
    if slots:
        y_last = max(p[1] for p in P(slots[-1]["polygon"]))
        d.line([(lw - int(14 * k), y_last), (lw - 2, y_last)], fill=INK_DIM, width=1)
        d.line([(lw + pw + 2, y_last), (Wc - int(12 * k), y_last)], fill=(44, 50, 56), width=1)

    # header: title, summary, legend
    sm = out["summary"]
    d.text((int(16 * k), int(12 * k)), f"Rack map  ·  {out['rack']['total_units']}U", font=f_head, fill=INK)
    sub = (f"{sm['used_units']} used   ·   {sm['empty_units']} empty   ·   {sm['unknown_units']} unknown"
           f"   ·   1U = {out['u_pitch_px']:.0f} px   ·   U1 = bottom")
    d.text((int(16 * k), int(46 * k)), sub, font=f_sub, fill=INK_DIM)
    lx = Wc - int(16 * k)
    for name, c in reversed([("device", KIND_RGB["device"][0]), ("inferred", KIND_RGB["inferred"][0]),
                             ("uneven height", KIND_RGB["irregular"][0]), ("empty", None)]):
        tb = d.textbbox((0, 0), name, font=f_sub)
        lx -= tb[2] - tb[0]
        d.text((lx, int(46 * k)), name, font=f_sub, fill=INK_DIM)
        lx -= int(18 * k)
        box = (lx, int(49 * k), lx + int(11 * k), int(60 * k))
        if c:
            d.rectangle(box, fill=c)
        else:
            d.rectangle(box, outline=INK_DIM, width=1)
        lx -= int(20 * k)

    return cv2.cvtColor(np.array(canvas.convert("RGB")), cv2.COLOR_RGB2BGR)


def print_report(out):
    rk = out["rack"]
    print(f"\n{out['image']}: 1U = {out['u_pitch_px']} px, {rk['total_units']} U slots "
          f"(top from {rk['top_limit_from']}, bottom from {rk['bottom_limit_from']})")
    for r in out["rails"]:
        print(f"  {r['side']:>5} rail  bbox={r['bbox']}  holes={'yes' if r['holes_line'] else 'not found'}")
    print("  devices:")
    for dv in out["devices"]:
        rng = f"U{dv['u_top']}" if dv["units"] == 1 else f"U{dv['u_top']}-U{dv['u_bottom']}"
        flag = "  [off-grid]" if dv["off_grid"] else ""
        print(f"    {dv['id']:<4} {dv['kind']:<9} {dv['units']}U  {rng:<9} conf={dv['confidence']:<6} "
              f"bbox={dv['bbox']}{flag}")
        if dv["units"] > 1:
            print("         units: " + ", ".join(f"{dv['id']}.{c['unit']}=U{c['u']}" for c in dv["unit_cells"]))
    print("  slots (top -> bottom):")
    for s in out["slots"]:
        who = f"{s['device']} unit {s['unit_of_device']}" if s["device"] else ""
        print(f"    U{s['u']:<3} {s['status']:<9} {who}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+")
    ap.add_argument("--out", default="out")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for p in a.images:
        out, original = build_map(p, a.debug)
        stem = os.path.splitext(os.path.basename(p))[0]
        cv2.imwrite(os.path.join(a.out, f"{stem}_detailed.png"), draw(original, out))
        with open(os.path.join(a.out, f"{stem}_detailed.json"), "w") as f:
            json.dump(out, f, indent=2)
        print_report(out)


if __name__ == "__main__":
    main()
