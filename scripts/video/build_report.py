"""The video-experiment report for one sample, built from its stored run (results/video_experiment/<sample>/) and
its inputs (data/video/): reports/video/<sample>/index.html with the pictures beside it in pics/.

Tabs: Input · Frames · How it works · Unit detection · Unit occupancy · Device detection · Why each device · Report.
· Why each device · Image vs frames · How it works.

    python3 scripts/video/build_report.py --sample vdo1
"""
import argparse
import html
import json
import pathlib
import re
import shutil

import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import rack_video as RV       # noqa: E402  (frame registration, for the "where a frame lands" picture)


def occ_font(size):
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()
SAMPLES = {
    "vdo1": {"title": "Sample 2", "rack_id": "SP-HYD-RM01-R01-R1", "rack": "vdo1_rack.png", "video": "vdo1.mp4", "selected": "vdo1_device_location.json", "frames": "vdo1_top_5_frames"},
}
PAL = ["#1d5fcf", "#1a7f45", "#b25e0a", "#8c3cb4", "#008c96", "#be285a", "#5a6e1e", "#3c3c3c"]
STATUS = {"occupied": ("occ", "Occupied"), "unoccupied": ("free", "Free"), "uncertain": ("unc", "Uncertain")}
esc = html.escape


# ---------------------------------------------------------------- small html helpers
def pill(status):
    c, n = STATUS.get(status, ("", status or "-"))
    return f'<span class="pill {c}">{esc(n)}</span>'


def table(headers, rows):
    head = "".join(f"<th>{h}</th>" for h in headers)
    def cell(c):
        return '<td class="n">%s</td>' % c[0] if isinstance(c, tuple) else "<td>%s</td>" % c
    body = "".join("<tr>" + "".join(cell(c) for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def n(v):
    """A numeric cell."""
    return (v,)


def fig(src, title, sub=""):
    return f'<figure><figcaption><b>{title}</b><span>{sub}</span></figcaption><img src="{src}" alt="{esc(title)}" loading="lazy"></figure>'


STITCH_NOTE = {   # the stitcher's warning codes in plain words (its own messages say what was measured and what to do)
    "W01": "Video too short", "W02": "Low resolution", "W03": "Camera moved too fast", "W04": "Too little overlap between frames",
    "W05": "Blurry frames", "W06": "Distance to the rack changed", "W07": "Camera drifted sideways", "W08": "Phone rotated",
    "W09": "Brightness jumped", "W10": "Weak match between frames", "W11": "Top of the rack cut off", "W12": "Bottom of the rack cut off",
    "W13": "Frames hard to align", "W14": "No vertical edges found", "W15": "Tilt not fully removed", "W16": "Cabinet sides not found",
    "W17": "Picture made smaller to fit memory", "W18": "Soft focus", "W19": "Part of the video left out"}


def stitch_card(st):
    """The stitching check: a one-line finding, one row per filming note, the measurements in a closed dropdown."""
    if not st:
        return ""
    ws = st.get("warnings") or []
    cap = st.get("capture") or {}
    finding = "The video stitched cleanly: no filming problems found." if not ws else (
        f'The video stitched into one picture, with {len(ws)} filming note{"s" if len(ws) != 1 else ""} for next time.')
    rows = "".join(f'<li><b>{esc(STITCH_NOTE.get(w["code"], w["name"]))}</b><span>{esc(w["text"])}</span></li>' for w in ws)
    meas = [("Camera speed", f'{cap.get("speed_median_fh_s", 0):.2f} screen heights a second', "0.15 or less is best"),
            ("Overlap between frames", f'{100 * cap.get("overlap_min", 0):.0f} % at the weakest point', "35 % or more"),
            ("Blurry frames", f'{100 * cap.get("blur_share", 0):.0f} %', "8 % or fewer"),
            ("Sideways drift", f'{100 * cap.get("sideways_fw", 0):.0f} % of the frame width', "under 40 %"),
            ("Weakest match", f'{st.get("min_pair_inliers")} matched points', "30 or more"),
            ("Straightened", {"corrected": "yes", "parallel": "already straight", "not_found": "no vertical edges found", "off": "off"}.get(st.get("vertical_status"), "-")
             + (f', tilt left {st["residual_tilt_deg"]:+.2f}°' if st.get("residual_tilt_deg") is not None else ""), "")] if cap else []
    tbl = ('<details class="irest"><summary>The numbers</summary><div class="tw"><table><thead><tr><th>Measured</th><th>On this video</th><th>Good when</th></tr></thead><tbody>'
           + "".join(f'<tr><td>{esc(a_)}</td><td class="n">{esc(b_)}</td><td class="muted">{esc(c_)}</td></tr>' for a_, b_, c_ in meas) + "</tbody></table></div></details>") if meas else ""
    turned = (st.get("upright") or {}).get("turned")
    turn = f'<p class="plain">The video lay on its side, as phone takes are saved, so it was turned upright ({turned}) before stitching.</p>' if turned else ""
    return ('<section class="card block"><h2>Stitching check</h2>'
            + f'<p class="find">{esc(finding)}</p>' + turn + (f'<ul class="snotes">{rows}</ul>' if rows else "") + tbl + "</section>")


def why(text):
    return f'<p class="why">{text}</p>'


def cards(items):
    return '<div class="strip">' + "".join(f'<div class="card"><b>{v}</b><span>{k}</span></div>' for v, k in items) + "</div>"


def ccards(items):
    """One measure per card: (title, value from the frames, value on the rack image alone or None, note or "")."""
    out = []
    for t, f_, r_, note in items:
        cmp_ = (f'<em><span>Rack image alone</span><b>{r_}</b></em>' if r_ is not None else (f'<em class="nt">{note}</em>' if note else ""))
        out.append(f'<div class="card cc"><span class="ct">{t}</span><strong>{f_}</strong>{cmp_}</div>')
    return '<div class="strip ccs">' + "".join(out) + "</div>"


def bars(title, counts):
    """One stacked bar to scale: occupied / free / uncertain."""
    tot = max(1, sum(counts.values()))
    segs = "".join(f'<i class="{STATUS[k][0]}" style="width:{100 * counts[k] / tot:.1f}%" title="{STATUS[k][1]} {counts[k]}">{counts[k] if counts[k] else ""}</i>'
                   for k in ("occupied", "unoccupied", "uncertain"))
    return f'<div class="bar"><span>{title}</span><div>{segs}</div><em>{tot} units</em></div>'


def dot(i):
    return f'<span class="dot" style="background:{PAL[(i - 1) % len(PAL)]}"></span>'


def dev_type(d):
    if d.get("unsure"):
        return '<span class="pill unc">unsure</span>' + (f' <span class="muted">the finder suggests {esc(d["type_hint"])}</span>' if d.get("type_hint") else "")
    if d.get("type"):
        return f'{esc(d["type"])} <span class="muted">{round(100 * d["type_confidence"])} % · {d["type_votes"]} votes</span>'
    return "-"


def sentences(text):
    return [s.strip() for s in re.split(r"(?<=\.)\s+(?=[A-Z0-9])", text or "") if s.strip()]


# ---------------------------------------------------------------- visual pieces
def chip(frame, cls, title):
    return f'<i class="ch {cls}" title="frame {frame}: {esc(title)}">{frame}</i>'


def unit_chips(u):
    box, emp = set(u.get("frames_box") or []), set(u.get("frames_emptyslot") or [])
    out = []
    for f in u.get("frames_seen") or []:
        if f in box: out.append(chip(f, "box", "a detector box over this unit"))
        elif f in emp: out.append(chip(f, "empty", "an empty slot or closed panel"))
        else: out.append(chip(f, "none", "no box, no empty slot; only the classifier"))
    return '<span class="chips">' + ("".join(out) or '<span class="muted">no frame saw it</span>') + "</span>"


def seam_chips(s):
    who = s.get("frames_who") or {}
    kinds = [("model_new", "new", "two different detector boxes: a new device"), ("model_same", "same", "one detector box over both units: the same device"),
             ("logic_new", "new logic", "no box owns both; the logic read a new device"), ("logic_same", "same logic", "no box owns both; the logic read the same device")]
    out = [chip(f, cls, t) for k, cls, t in kinds for f in who.get(k, [])]
    out.sort(key=lambda h: int(h.split(">")[1].split("<")[0]))
    return '<span class="chips">' + ("".join(out) or '<span class="muted">no frame saw both units</span>') + "</span>"


def short_unit(u):
    by, n_, p = u["decided_by"], u["frames_voting"], u["p_occupied"]
    if by.startswith("rack image"):
        return "too few frames: the rack image's answer stays"
    if u["status"] == "occupied" and " of " in by:
        return f"detector box in {u['frames_device']} of {n_} frames"
    if u["status"] == "unoccupied" and " of " in by:
        return f"empty slot in {u['frames_empty']} of {n_} frames"
    if by.startswith("classifier over"):
        return f"no box in any of {n_} frames · classifiers {p:.0%} occupied"
    if u["status"] == "uncertain":
        return f"{n_} frames disagree or are unsure · classifiers {p:.0%} occupied"
    return by


def short_seam(s):
    who = s.get("frames_who") or {}
    mn, ms, ln, ls = (len(who.get(k, [])) for k in ("model_new", "model_same", "logic_new", "logic_same"))
    if s["frames_voting"] < 2:
        return "too few frames: the rack image's decision stays"
    parts = []
    if mn: parts.append(f"{mn} saw two boxes")
    if ms: parts.append(f"{ms} saw one box")
    if ln or ls: parts.append(f"{ln + ls} left to the logic")
    return f"{s['frames_voting']} frames: " + ", ".join(parts)


def vs(img_points, frm_points):
    """Two short lists side by side: what the rack image alone did, what the frames did."""
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs)
    return (f'<div class="vs"><div class="m img"><b>Rack image alone</b><ul>{li(img_points)}</ul></div>'
            f'<div class="m frm"><b>From the frames</b><ul>{li(frm_points)}</ul></div></div>')


def best_cell(d):
    if d is None or d.get("best_frame") is None:
        return "-"
    return f'<span class="bf"><img src="pics/section_D{d["index"]}.jpg" alt="D{d["index"]} in frame {d["best_frame"]}" loading="lazy"><b>frame {d["best_frame"]}</b></span>'


def two_msgs(img_text, frames_html):
    return (f'<div class="msgs"><div class="m img"><b>Why the rack image alone gave this</b><p>{esc(img_text or "")}</p></div>'
            f'<div class="m frm"><b>What the frames decided, and how</b>{frames_html}</div></div>')


def ev_row(head, body):
    return f'<details class="ev"><summary>{head}</summary>{body}</details>'


def legend(items):
    return '<div class="legend">' + "".join(f'<span><i class="ch {c}">·</i>{esc(t)}</span>' for c, t in items) + "</div>"


def ladder_svg(rack_rows, rows):
    """The two ladders drawn to scale side by side: the rack image's own and the frames'."""
    ys = [b for x in rack_rows for b in (x["box"][1], x["box"][3])] + [b for x in rows for b in (x["box"][1], x["box"][3])]
    y0, y1 = min(ys), max(ys); H = 820; k = (H - 40) / max(1, y1 - y0); Y = lambda y: 30 + (y - y0) * k
    cover = {}
    for u in rows:
        if u["rack_unit"]: cover[u["rack_unit"]] = cover.get(u["rack_unit"], 0) + 1
    g = ['<text x="210" y="16" text-anchor="middle" class="hd">Rack image alone</text>', '<text x="470" y="16" text-anchor="middle" class="hd">From the frames</text>']
    for x in rack_rows:
        a, b = Y(x["box"][1]), Y(x["box"][3]); merged = cover.get(x["unit"], 0) > 1
        g.append(f'<rect x="120" y="{a:.1f}" width="180" height="{b - a:.1f}" class="{"merged" if merged else "u"}"/>')
        g.append(f'<text x="128" y="{(a + b) / 2 + 4:.1f}">{x["unit"].upper()}</text><text x="292" y="{(a + b) / 2 + 4:.1f}" text-anchor="end" class="px">{x["box"][3] - x["box"][1]} px{" · " + str(cover[x["unit"]]) + " units merged" if merged else ""}</text>')
    miss = [u for u in rows if not u["rack_unit"]]
    if miss:
        a, b = Y(min(u["box"][1] for u in miss)), Y(max(u["box"][3] for u in miss))
        g.append(f'<rect x="120" y="{a:.1f}" width="180" height="{b - a:.1f}" class="miss"/><text x="210" y="{(a + b) / 2 + 4:.1f}" text-anchor="middle" class="missx">not found · {len(miss)} unit{"s" if len(miss) != 1 else ""}</text>')
    for u in rows:
        a, b = Y(u["box"][1]), Y(u["box"][3])
        g.append(f'<rect x="380" y="{a:.1f}" width="180" height="{b - a:.1f}" class="u{" new" if not u["rack_unit"] else ""}"/>')
        g.append(f'<text x="388" y="{(a + b) / 2 + 4:.1f}">{u["unit"].upper()}</text><text x="552" y="{(a + b) / 2 + 4:.1f}" text-anchor="end" class="px">{u["box"][3] - u["box"][1]} px</text>')
    for u in rows:                      # thin links from a frames unit to the rack unit it overlaps
        if u["rack_unit"]:
            ru = next(x for x in rack_rows if x["unit"] == u["rack_unit"]); m1 = Y((ru["box"][1] + ru["box"][3]) / 2); m2 = Y((u["box"][1] + u["box"][3]) / 2)
            g.append(f'<line x1="300" y1="{m1:.1f}" x2="380" y2="{m2:.1f}" class="lk"/>')
    return f'<svg viewBox="0 0 680 {H}" class="ladders" role="img" aria-label="the two ladders to scale">{"".join(g)}</svg>'


def elevation(rows, devices):
    """Unit by unit, top to bottom: the rack image alone, the frames, the devices from the frames."""
    n_ = len(rows); c = ['<div class="el-h" style="grid-column:1">Unit</div><div class="el-h" style="grid-column:2">Rack image alone</div><div class="el-h" style="grid-column:3">From the frames</div><div class="el-h" style="grid-column:4">Device · from the frames</div><div class="el-h" style="grid-column:5">Best frame</div>']
    i = 0
    while i < n_:
        ru = rows[i]["rack_unit"]; j = i
        while j + 1 < n_ and rows[j + 1]["rack_unit"] == ru: j += 1
        if ru is None:
            c.append(f'<div class="el-c miss" style="grid-column:2;grid-row:{i + 2}/{j + 3}">not found on the rack image</div>')
        else:
            st = rows[i]["rack_status"]; cls = STATUS.get(st, ("", ""))[0]
            c.append(f'<div class="el-c {cls}" style="grid-column:2;grid-row:{i + 2}/{j + 3}">{esc(ru.upper())} · {STATUS.get(st, ("", st))[1]}{" · " + str(j - i + 1) + " units merged" if j > i else ""}</div>')
        i = j + 1
    for i, u in enumerate(rows):
        c.append(f'<div class="el-u" style="grid-row:{i + 2}">{esc(u["unit"].upper())}</div>')
        c.append(f'<div class="el-c {STATUS[u["status"]][0]}{" chg" if u["changed"] or not u["rack_unit"] else ""}" style="grid-column:3;grid-row:{i + 2}">{STATUS[u["status"]][1]}</div>')
    idx = {u["unit"]: i for i, u in enumerate(rows)}
    for d in devices:
        a, b = idx[d["top"]], idx[d["bottom"]]; col = PAL[(d["index"] - 1) % len(PAL)]
        c.append(f'<div class="el-d" style="grid-column:4;grid-row:{a + 2}/{b + 3};border-left-color:{col};background:{col}22">{esc(d["label"])}</div>')
        if d.get("best_frame") is not None:
            c.append(f'<div class="el-bf" style="grid-column:5;grid-row:{a + 2}/{b + 3};border-color:{col}"><img src="pics/section_D{d["index"]}.jpg" alt="D{d["index"]} in frame {d["best_frame"]}" loading="lazy"><span>frame {d["best_frame"]}</span></div>')
    return '<div class="elev">' + "".join(c) + "</div>"


def mapping_pictures(fr, rack_bgr, devices, pics, frame_no):
    """The mapping on one frame, as pictures: the matches, the unit lines, the device boxes, the result carried back."""
    import device_model as DM
    import pipeline as P
    hexrgb = lambda h_: tuple(int(h_[i:i + 2], 16) for i in (1, 3, 5))
    sift = cv2.SIFT_create(nfeatures=6000)
    k1, d1 = sift.detectAndCompute(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), None); k2, d2 = sift.detectAndCompute(cv2.cvtColor(rack_bgr, cv2.COLOR_BGR2GRAY), None)
    good = [a for a, b in cv2.BFMatcher(cv2.NORM_L2).knnMatch(d1, d2, k=2) if a.distance < 0.75 * b.distance]
    src = np.float32([k1[m.queryIdx].pt for m in good]); dst = np.float32([k2[m.trainIdx].pt for m in good])
    H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0); inl = mask.ravel().astype(bool)
    h, w = fr.shape[:2]; RH, RW = rack_bgr.shape[:2]
    q = cv2.perspectiveTransform(np.float32([[[0, 0], [w, 0], [w, h], [0, h]]]), H)[0]
    x0, x1 = max(0, int(q[:, 0].min())), min(RW, int(q[:, 0].max())); y0, y1 = max(0, int(q[:, 1].min())), min(RH, int(q[:, 1].max()))
    FH = 380                                                        # every picture is drawn at this height
    fs, rs = FH / h, FH / (y1 - y0)
    def frame_pic(): return Image.fromarray(fr[:, :, ::-1].copy()).resize((int(w * fs), FH))
    def rack_pic(): return Image.fromarray(rack_bgr[y0:y1, x0:x1, ::-1].copy()).resize((int((x1 - x0) * rs), FH))
    to_rack = lambda pts: [((x - x0) * rs, (y - y0) * rs) for x, y in cv2.perspectiveTransform(np.float32([pts]), H)[0]]
    to_frame = lambda pts: [(x * fs, y * fs) for x, y in cv2.perspectiveTransform(np.float32([pts]), np.linalg.inv(H))[0]]
    font = occ_font(15); out = {}
    # 0 · the matches: one picture, frame left, rack right, a line per agreeing match
    a_, b_ = frame_pic(), rack_pic(); gap = 30
    cv_ = Image.new("RGB", (a_.width + gap + b_.width, FH), (255, 255, 255)); cv_.paste(a_, (0, 0)); cv_.paste(b_, (a_.width + gap, 0)); dr = ImageDraw.Draw(cv_, "RGBA")
    idx = np.flatnonzero(inl); rng = np.random.default_rng(3); idx = rng.choice(idx, size=min(45, len(idx)), replace=False)
    for n_, i in enumerate(idx):
        (fx, fy), (rx, ry) = src[i], dst[i]
        if not (x0 <= rx <= x1 and y0 <= ry <= y1): continue
        col = hexrgb(PAL[n_ % len(PAL)]); p1 = (fx * fs, fy * fs); p2 = (a_.width + gap + (rx - x0) * rs, (ry - y0) * rs)
        dr.line([p1, p2], fill=col + (170,), width=2); [dr.ellipse([x - 4, y - 4, x + 4, y + 4], fill=col + (255,)) for x, y in (p1, p2)]
    cv_.save(pics / "map_match.jpg", quality=86); out["matches"], out["inliers"], out["candidates"] = len(idx), int(inl.sum()), len(good)
    # 1 · the frame's own unit lines, and the same lines on the rack
    units, _ = P.units_from_photo(fr)
    fa_, ra_ = frame_pic(), rack_pic(); df, drr = ImageDraw.Draw(fa_, "RGBA"), ImageDraw.Draw(ra_, "RGBA")
    for n_, u in enumerate(units):
        bx1, by1, bx2, by2 = u["box"]; col = hexrgb(PAL[n_ % len(PAL)])
        for yy in ((by1,) if n_ < len(units) - 1 else (by1, by2)):
            df.line([(bx1 * fs, yy * fs), (bx2 * fs, yy * fs)], fill=col + (255,), width=4); drr.line(to_rack([(bx1, yy), (bx2, yy)]), fill=col + (255,), width=4)
        df.text((bx1 * fs + 6, (by1 + by2) / 2 * fs - 8), u["unit"].upper(), fill=col + (255,), font=font)
    fa_.save(pics / "map_units_f.jpg", quality=86); ra_.save(pics / "map_units_r.jpg", quality=86); out["units"] = len(units)
    # 2 · the detector's boxes on the frame, and the same boxes as polygons on the rack
    boxes = [b for b in DM.detect(np.ascontiguousarray(fr[:, :, ::-1])) if b[4] >= DM.SCORE] if P.detector_available() else []
    fb, rb = frame_pic(), rack_pic(); df, drr = ImageDraw.Draw(fb, "RGBA"), ImageDraw.Draw(rb, "RGBA")
    for n_, b in enumerate(boxes):
        col = hexrgb(PAL[n_ % len(PAL)]); bx1, by1, bx2, by2 = b[:4]
        df.rectangle([bx1 * fs, by1 * fs, bx2 * fs, by2 * fs], outline=col + (255,), width=4, fill=col + (40,))
        drr.polygon(to_rack([(bx1, by1), (bx2, by1), (bx2, by2), (bx1, by2)]), outline=col + (255,), width=4, fill=col + (40,))
    fb.save(pics / "map_dev_f.jpg", quality=86); rb.save(pics / "map_dev_r.jpg", quality=86); out["boxes"] = len(boxes)
    # 3 · the final devices on the rack, and carried back onto the frame
    rr, ff = rack_pic(), frame_pic(); drr, df = ImageDraw.Draw(rr, "RGBA"), ImageDraw.Draw(ff, "RGBA"); shown = 0
    for d in devices:
        poly = d["polygon"]; cy = sum(p_[1] for p_ in poly) / 4
        if not (y0 <= cy <= y1): continue
        col = hexrgb(PAL[(d["index"] - 1) % len(PAL)]); shown += 1
        pr = [((x - x0) * rs, (y - y0) * rs) for x, y in poly]; pf = to_frame(poly)
        for drw, pp in ((drr, pr), (df, pf)):
            drw.polygon(pp, outline=col + (255,), width=4, fill=col + (50,))
            tx, ty = max(4, min(x for x, _ in pp) + 6), max(4, min(y for _, y in pp) + 4); drw.rounded_rectangle([tx - 3, ty - 2, tx + 64, ty + 20], 4, fill=col + (235,)); drw.text((tx + 2, ty), d["label"].split(" · ")[0] + " · " + d["label"].split(" · ")[1], fill="white", font=font)
    rr.save(pics / "map_back_r.jpg", quality=86); ff.save(pics / "map_back_f.jpg", quality=86); out["devices"] = shown
    out["frame"] = frame_no
    return out


# ---------------------------------------------------------------- the integration tab: every step, its approach, why, and its accuracy
NUM = {3: "three", 4: "four", 5: "five", 6: "six", 7: "seven"}
WARN_NAME = {'W01': 'Video too short', 'W02': 'Low resolution', 'W03': 'Camera moved too fast', 'W04': 'Too little overlap between frames', 'W05': 'Blurry frames', 'W06': 'Distance to the rack changed', 'W07': 'Camera drifted sideways', 'W08': 'Phone rotated', 'W09': 'Brightness jumped', 'W10': 'Weak match between frames', 'W11': 'Top of the rack cut off', 'W12': 'Bottom of the rack cut off', 'W13': 'Frames hard to align', 'W14': 'No vertical edges found', 'W15': 'Tilt not fully removed', 'W16': 'Cabinet sides not found', 'W17': 'Picture made smaller to fit memory', 'W18': 'Soft focus', 'W19': 'Part of the video left out'}   # the stitcher's warning codes, named as the portal names them


def occ_kinds(items):
    """One sentence on which way the wrong answers go."""
    fr = sum(x["truth"] == "occupied" for x in items); oc = len(items) - fr
    if not oc:
        return f"All {fr} are occupied units answered as free."
    return f"{fr} are occupied units answered as free, {oc} {'is a free unit' if oc == 1 else 'are free units'} answered as occupied."


def _eval_numbers():
    """Accuracy of each step from our own evaluation files (nothing typed in by hand)."""
    J = lambda f: json.load(open(ROOT / f))
    u = J("results/units/eval/summary.json")["stages"]; uh = J("results/units/eval_highres250/summary.json")["stages"]; ub = J("results/units/eval_bottom100/summary.json")["stages"]
    occ_m = {k: J(f"models/v2/{k}_eval.json")["val_accuracy"] for k in ("ensemble", "yolo11s_cls", "embed_dinov2", "teammate", "classical")}
    fun = J("data/eval/ensemble_portal/data.json"); f_ = fun["funnel"]; ts = fun["testsets"]["overall"]
    occ_m["ensemble"] = f_["right"] / f_["units"]            # one definition for the headline and the bar
    dm = {m: J(f"results/devices/grade_devices_test_{m}.json") for m in ("model", "model_only", "logic")}
    cls_f = ROOT / "models" / "device_cls" / "convnext_tiny" / "other_stats.json"   # the device classifier's own test metrics (handover 2026-10-19)
    ue = u["rteos_logic"]
    members = fun.get("members") or []
    cls_conf = None
    try:
        cm = J("models/device_cls/convnext_tiny/other_stats.json")["metrics"]["confusion"]   # test split, current labels
        pairs = sorted(((cm[i][j] + cm[j][i], i, j) for i in range(len(cm)) for j in range(i + 1, len(cm))), reverse=True)
        cls_conf = {"top_pair": pairs[0][0], "wrong": sum(cm[i][j] for i in range(len(cm)) for j in range(len(cm)) if i != j)}
    except Exception:
        pass
    return {"ends": {k: ue[k] for k in ("top_runs_past_by_1u", "top_stops_short_by_1u", "bottom_runs_past_by_1u", "bottom_stops_short_by_1u", "lines_within_quarter_mean", "count_within_1", "photos")},
            "members": members, "cls_conf": cls_conf,
            "units": {"logic": u["grid_vote"]["units_right_mean"], "model": u["model"]["units_right_mean"], "both": u["rteos_logic"]["units_right_mean"], "photos": u["rteos_logic"]["photos"],
                      "hr": uh["rteos_logic"]["units_right_mean"], "hr_n": uh["rteos_logic"]["photos"], "bt": ub["rteos_logic"]["units_right_mean"], "bt_n": ub["rteos_logic"]["photos"]},
            "occ": {"models": occ_m, "units": f_["units"], "right": f_["right"], "answered": ts["answered"], "answered_wrong": ts["wrong_answers"], "all": ts["units"], "photos": ts["photos"], "to_person": ts["uncertain"],
                    "caught": ts["mistakes_sent_to_person"], "box": {k: ts["box"][k] for k in ("answered", "wrong_answers", "uncertain", "mistakes_sent_to_person")}, "items": ts.get("items") or []},
            "dev": {"both": dm["model"]["found_exactly"], "model": dm["model_only"]["found_exactly"], "logic": dm["logic"]["found_exactly"], "devices": dm["model"]["devices"], "photos": dm["model"]["photos"],
                    "unseen": dm["model"]["their_unseen"]["found_exactly"], "unseen_photos": dm["model"]["their_unseen"]["photos"]},
            "cls": _cls_numbers(cls_f) if cls_f.exists() else None}


def _cls_numbers(f):
    o = json.load(open(f)); m = o["metrics"]; res = json.load(open(f.parent / "results.json"))
    t = (o.get("splits") or {}).get("test") or {}
    return {"accuracy": m["accuracy"], "top2": m["top2_accuracy"], "macro_f1": m["macro_f1"], "balanced": m["balanced_accuracy"], "n": m["n"],
            "kinds": len(res.get("classes") or []), "flagged": t.get("flagged_share"), "flagged_n": t.get("flagged"), "flagged_wrong": t.get("wrong_among_flagged"),
            "wrong": t.get("wrong_total"),
            "small": (json.load(open(f.parent / "resolution.json")).get("small_photos") if (f.parent / "resolution.json").exists() else None)}


def _catalogue_size():
    """Vendors and models in the switch mapper's catalogue, and its saved templates, counted from the files."""
    sp = ROOT / "data" / "catalogues"; tp = ROOT / "port_agents" / "switch_agent" / "templates"
    try:
        d = {k: v for k, v in json.load(open(sp / "models_by_vendor.json")).items() if not k.startswith("_")}
        return len(d), sum(len(v["models"] if isinstance(v, dict) else v) for v in d.values()), sum(1 for x in tp.iterdir() if x.is_dir()) if tp.exists() else 0
    except Exception:
        return 0, 0, 0


CAT_N = _catalogue_size()


SHOW_LAYOUT = True         # the OCR, agent, overlay and fallback steps are part of the flow report (user, 2026-10-23)


def integration_tab(lad, rack, os_, ro, devices):
    E = _eval_numbers(); pc = lambda x: f"{100 * x:.1f} %"
    def bars(rows_):
        return '<div class="ibars">' + "".join(
            f'<div class="ib{" on" if on else ""}"><span>{esc(nm)}</span><div><i style="width:{100 * v:.1f}%"></i></div><b>{pc(v)}</b></div>' for nm, v, on in rows_) + '<p class="iax">bars drawn from 0 to 100 %</p></div>'
    def step(no, name, status, approach, why_, acc, acc_note, bars_html, extra="", acc_label="Accuracy", model="", rest=""):
        st = '<span class="ist live">In use</span>' if status == "live" else '<span class="ist next">Coming next</span>'
        return (f'<li class="istep"><div class="inum">{no}</div><div class="icard"><header><h3>{name}</h3>{st}</header>'
                f'<div class="igrid"><div class="icol">' + (f'<span class="ik">Model</span><p class="imodel">{model}</p>' if model else "") + f'<span class="ik">Approach</span><p>{approach}</p><span class="ik">Why this approach</span><p>{why_}</p>{extra}</div>'
                f'<div class="icol iacc"><span class="ik">{acc_label}</span>' + (f'<strong>{acc}</strong><p class="inote">{acc_note}</p>{bars_html}' if acc else f'<p class="inote">{acc_note}</p>')
                + (f'<details class="irest"><summary>Where the rest goes</summary><div class="irb">{rest}</div></details>' if rest else "") + "</div></div></div></li>")
    U, O, D, C = E["units"], E["occ"], E["dev"], E["cls"]
    # where the remaining units go: right / wrong / sent to a person, to scale of every test unit
    def split(label, answered, wrong, person):
        right = answered - wrong; tot = O["all"]
        seg = lambda n_, cls, txt: f'<i class="{cls}" style="width:{100 * n_ / tot:.2f}%" title="{txt}">{txt if n_ / tot > 0.08 else ""}</i>'
        return (f'<div class="osplit"><div class="oshead"><b>{label}</b><span>{pc(right / answered)} right on the {answered:,} units it answers</span></div>'
                f'<div class="osbar">{seg(right, "r", f"{right:,} right")}{seg(wrong, "w", f"{wrong} wrong")}{seg(person, "p", f"{person} to a person")}</div>'
                f'<div class="oslegend"><span><i class="r"></i>{right:,} right</span><span><i class="w"></i>{wrong} wrong ({pc(wrong / answered)})</span><span><i class="p"></i>{person} sent to a person ({pc(person / tot)})</span></div></div>')
    groups = {}
    for x in O["items"]:
        w = (x.get("why") or "").lower()
        g = "free" if x.get("truth") == "unoccupied" else "label" if "labelled" in w else "sliver" if "sliver" in w else "dark"
        groups.setdefault(g, []).append(x)
    gtxt = {"dark": "the device is dark, tiny or half hidden in the photo, so its unit looks like an empty gap",
            "sliver": "the unit's label was drawn as a thin slanted sliver, so the cut the model sees shows mostly cables or frame",
            "label": "the label marks the unit as an empty slot although a device sits there",
            "free": "a free slot with a vented blanking panel, whose holes look like a device front"}
    occ_extra = ('<div class="orest">'
                 + split("With the side-angle rule", O["answered"], O["answered_wrong"], O["to_person"])
                 + split("Plus the device-box check", O["box"]["answered"], O["box"]["wrong_answers"], O["box"]["uncertain"])
                 + f'<p class="inote">Sending a unit to a person is not a mistake: it is the rule working. {O["caught"]} of the {O["to_person"]} units sent to a person would have been answered wrongly.</p>'
                 + (f'<span class="ik">The {O["answered_wrong"]} wrong answers</span><ul class="owrong">' + "".join(f'<li><b>{len(v_)}</b> {gtxt[k]}</li>' for k, v_ in sorted(groups.items(), key=lambda kv: -len(kv[1]))) + "</ul>" if groups else "")
                 + f'<p class="inote">{occ_kinds(O["items"])} The device-box check sends {O["answered_wrong"] - O["box"]["wrong_answers"]} of them to a person instead, leaving {O["box"]["wrong_answers"]}.</p></div>')
    m = O["models"]
    steps = [
        step(1, "Unit detection", "live", "The RackTrack unit model draws the units; a rule-based check then confirms the first and last unit by the regular spacing of the rail holes.",
             "The model is accurate in the middle of the rack but sometimes misses or adds a unit at the ends. The check fixes the ends. Together they beat either one alone.",
             pc(U["both"]), f'of units right on {U["photos"]} clear test photos. On harder photos: {pc(U["hr"])} on {U["hr_n"]} high-resolution photos, {pc(U["bt"])} on the {U["bt_n"]} hardest.',
             bars([("Logic alone", U["logic"], False), ("Model alone", U["model"], False), ("Model + logic", U["both"], True)]),
             model="RackTrack unit detector (a YOLO object detector trained on rack units), plus a rule-based end check.",
             rest=(f'<p>The missing {pc(1 - U["both"])} sits almost only at the two ends of the rack, where the ladder is one unit off:</p><ul class="owrong">'
                   f'<li><b>{E["ends"]["bottom_stops_short_by_1u"]}</b> photos: the bottom stops one unit short</li><li><b>{E["ends"]["top_runs_past_by_1u"]}</b> photos: the top runs one unit too far</li>'
                   f'<li><b>{E["ends"]["top_stops_short_by_1u"]}</b> photo{"s" if E["ends"]["top_stops_short_by_1u"] != 1 else ""}: the top stops one unit short</li><li><b>{E["ends"]["bottom_runs_past_by_1u"]}</b> photo{"s" if E["ends"]["bottom_runs_past_by_1u"] != 1 else ""}: the bottom runs one unit too far</li></ul>'
                   f'<p class="inote">Inside the rack the lines are placed well: {pc(E["ends"]["lines_within_quarter_mean"])} of unit lines sit within a quarter of a unit of the true line, and the unit count is within one on {E["ends"]["count_within_1"]} of {E["ends"]["photos"]} photos. Harder photos (far, dark or angled) lose more, as the lower scores above show.</p>')),
        step(2, "Unit occupancy", "live", f"{NUM[len(E['members'])].capitalize()} classifiers each judge one unit as occupied or free, and their average decides. When they are not sure enough, or a device finder disagrees, the unit is sent to a person.",
             f"No single classifier is right on every kind of rack. The average of {NUM[len(E['members'])]} is more reliable than the best one, and the uncertain rule turns the remaining mistakes into questions for a person.",
             pc(1 - O["answered_wrong"] / O["answered"]), f'right on the units it answers, {O["answered"]:,} of {O["all"]:,} test units on {O["photos"]} photos, as the app runs (side-angle rule on); {pc(1 - O["box"]["wrong_answers"] / O["box"]["answered"])} with the device-box check added. Before any rule, the ensemble alone gets {pc(O["right"] / O["units"])} of {O["units"]:,} test units right.',
             bars(([(x["name"], x["accuracy"], False) for x in sorted(E["members"], key=lambda x: x["accuracy"])] or [("Best single model", m["yolo11s_cls"], False)]) + [(f"Ensemble of {NUM[len(E['members'])]}, before any rule", m["ensemble"], True)]),
             model=f"{NUM[len(E['members'])].capitalize()} classifiers averaged: " + ", ".join(x["name"] for x in E["members"]) + ".", rest=occ_extra),
        step(3, "Device detection", "live", "The team's device detector draws a box around each device; a box over two neighbouring units makes them one device. Where no box covers a unit, a rule reads the line between the units.",
             "The detector finds device edges far better than any rule, and the rule only fills the few places the detector leaves empty.",
             pc(D["both"]), f'of {D["devices"]:,} devices found with exactly the right span, on {D["photos"]} photos; {pc(D["unseen"])} on the {D["unseen_photos"]} photos the detector never saw in training.',
             bars([("Logic alone", D["logic"], False), ("Detector alone", D["model"], False), ("Detector + logic", D["both"], True)]),
             model="DEIMv2 object detector (DINOv3 ViT backbone), one class: device; plus a rule-based boundary check.",
             rest=(f'<p>{D["devices"] - round(D["devices"] * D["both"])} of {D["devices"]:,} devices are not found exactly. They come from 170 mistakes, each joining or cutting devices:</p><ul class="owrong">'
                   '<li><b>42</b> two different devices joined into one</li><li><b>38</b> a stack of identical devices joined into one</li>'
                   '<li><b>50</b> one device cut in two by the rule, where no box covered it</li><li><b>40</b> one device cut in two by the detector\'s boxes</li></ul>'
                   f'<p class="inote">Every other device has exactly the right span. Some of these 170 are label mistakes rather than ours; they are under review.</p>')),
    ]
    if C:
        steps.append(step(4, "Device classification", "live",
                          f'An image classifier (ConvNeXt-Tiny) looks at the cut-out of each detected device and names one of {C["kinds"]} kinds of device. When it is unsure, or the device looks unlike anything it learnt, it answers "Other" instead of guessing.',
                          "It ranked first in our comparison of image classifiers for small, detailed datasets, and its licence allows company use. The \"Other\" rule keeps equipment it has never seen from being forced into a wrong kind.",
                          pc(C["accuracy"]), f'of {C["n"]:,} test devices named correctly at the first guess. Trained on photos of 1 MP or more; '
                          + (f'on {C["small"]["n"]:,} test devices from smaller photos it scores {pc(C["small"]["acc"])}, about the same.' if C.get("small") else 'video frames are smaller, so expect lower accuracy and more "Other" answers there.')
                          + (f' About {pc(C["flagged"])} of real devices are answered "Other".' if C.get("flagged") else ""),
                          bars([("Average over the kinds", C["macro_f1"], False), ("Right first guess", C["accuracy"], True), ("Right in the top two", C["top2"], False)]),
                          model="ConvNeXt-Tiny image classifier (pre-trained on ImageNet-22k), fine-tuned on device cut-outs; plus an \"Other\" check on its confidence and on its distance to the training examples.",
                          rest=(f'<p>{C["wrong"]} of {C["n"]:,} test devices get the wrong kind at the first guess:</p><ul class="owrong">'
                                f'<li><b>{round(C["n"] * (C["top2"] - C["accuracy"]))}</b> have the right kind as the second guess</li>'
                                + (f'<li><b>{E["cls_conf"]["top_pair"]}</b> are between the same two kinds, which look much alike</li>' if E.get("cls_conf") else "")
                                + f'<li><b>{C["flagged_n"]}</b> are answered "Other"; {C["flagged_wrong"]} of them would have been wrong anyway</li></ul>'
                                f'<p class="inote">The rarest kinds are the hardest: the average over the kinds ({pc(C["macro_f1"])}) is lower than the overall rate because a few kinds have only 37 to 76 test examples.</p>')))
    else:
        steps.append(step(4, "Device classification", "next", "Decides what kind of device each detected device is.",
                          "This step is being finished in a separate piece of work. Its approach and accuracy will appear here as soon as its results are in.", None,
                          "Results pending.", ""))
    if SHOW_LAYOUT:
      steps.append(step(5, "Device layout", "live", "For every device classified as a Switch, Router, Firewall or Gateway (on a video: in each of its top 5 best frames): OCR reads the make and model off its panel; a saved template gives that model's official front image, which becomes a layout template (every port, its type and number). Switches go to the switch mapper; routers, firewalls and gateways to the team's mappers, which run offline from saved templates only.",
                      "A switch's own front image is the only reliable map of its ports: cables hide most jacks in a real photo, but the logo, labels and LEDs still line up with the vendor's front view.",
                      None, "Not measured on a labelled test set yet: the make and model are checked by eye for now.", "",
                      model=f"RapidOCR panel reading + catalogues: switches {CAT_N[0]} vendors and {CAT_N[1]:,} models with {CAT_N[2]} saved templates; routers 131 vendors, 2,791 models, 15 saved templates; firewalls, gateways and load balancers 3,155 models, 58 saved templates."))
    if SHOW_LAYOUT:
      steps.append(step(6, "Port layout", "live", "The layout template is fitted onto the switch in the photo (feature matching, then a homography); a fit counts only when 80 % of its ports land on ports the port detector found. Ports are numbered 1 top left, 2 below it. Without a fitted vendor layout, RT-EOS's port logic finds the port grid in the photo (marked as such). Each port is then read with RT-EOS's models: empty or connected, the cable's type and colour, a dust cap on empty ports, and its spec from the spec agent.",
                      "The port check stops a 28-port template passing on a 24-port switch; ports guessed from the photo alone are not shown.",
                      None, "Not measured on a labelled test set yet.", "",
                      model="Switch mapper port detector (best_merged_ports.pt); RT-EOS port_type_finder + port_connected_or_empty (port_count), cable_type_and_colour, dust_cap_classifier, switch_specs."))
    names = ["Rack photo or video", "Unit detection", "Unit occupancy", "Device detection", "Device classification"] + (["Device layout", "Port layout"] if SHOW_LAYOUT else []) + ["Rack report"]
    flow = ('<div class="iflow">' + "".join(f'<span>{x}</span>' + ('<em>→</em>' if i < len(names) - 1 else "") for i, x in enumerate(names)) + "</div>")
    return ('<section class="card block"><h2>The full pipeline, step by step</h2>'
            + '<p class="plain">Each step takes the result of the one before it. For every step: the approach we use, why we chose it over the alternatives, and how accurate it is on our test sets.</p>'
            + flow + f'<ol class="isteps">{"".join(steps)}</ol></section>')


# ---------------------------------------------------------------- inputs and pictures
def thumb(src, dst, width, box=None):
    im = Image.open(src).convert("RGB")
    s = width / im.width
    im = im.resize((width, int(im.height * s)))
    if box:
        d = ImageDraw.Draw(im)
        x1, y1, x2, y2 = [v * s for v in box]
        d.rectangle([x1, y1, x2, y2], outline=(230, 120, 20), width=4)
    im.save(dst, quality=86)
    return im.size


def video_facts(path, dst_first):
    cap = cv2.VideoCapture(str(path))
    w, h, nfr, fps = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)), int(cap.get(cv2.CAP_PROP_FRAME_COUNT)), cap.get(cv2.CAP_PROP_FPS) or 0
    ok, fr = cap.read()
    cap.release()
    if ok:
        im = Image.fromarray(fr[:, :, ::-1]); im.thumbnail((700, 700)); im.save(dst_first, quality=86)
    return {"w": w, "h": h, "frames": nfr, "fps": fps, "seconds": nfr / fps if fps else 0}


def build(sample):
    """A stored sample's report: reports/video/<sample>/."""
    S = SAMPLES[sample]; src = ROOT / "data" / "video"
    res = ROOT / "results" / "video_experiment" / sample
    rack = res / "rack.png" if (res / "rack.png").exists() else src / S["rack"]       # the picture the run stitched from the video
    return build_from(res, ROOT / "reports" / "video" / sample, rack, src / S["video"],
                      S["title"], sample, src / S["selected"], src / S["frames"], S.get("rack_id"))


def build_from(res_dir, out, rack_path, video_path, title, name, selected_path=None, frames_dir=None, rack_id=None, pending=False):
    """The report of any finished run (a folder with result.json and its pictures) into out/index.html + out/pics/."""
    res_dir, out, rack_path, video_path = pathlib.Path(res_dir), pathlib.Path(out), pathlib.Path(rack_path), pathlib.Path(video_path)
    pics = out / "pics"
    pics.mkdir(parents=True, exist_ok=True)
    r = json.load(open(res_dir / "result.json"))
    for f in res_dir.glob("*.jpg"):
        shutil.copy2(f, pics / f.name)
    selected = json.load(open(selected_path)) if selected_path and pathlib.Path(selected_path).exists() else None
    rack_im = Image.open(rack_path); rack_wh = rack_im.size
    thumb(rack_path, pics / "input_rack.jpg", 700)
    vf = video_facts(video_path, pics / "input_video.jpg")
    top5 = []
    for f in sorted(pathlib.Path(frames_dir).glob("rank*_frame*.png")) if frames_dir and pathlib.Path(frames_dir).exists() else []:
        m = re.search(r"rank(\d+)_frame(\d+)", f.name); k, idx = int(m.group(1)), int(m.group(2))
        thumb(f, pics / f"input_rank{k}.jpg", 560); top5.append((k, idx))
    lad, of, os_, ro, v = r["ladder"], r["occupancy_frames"], r["occupancy_frames"]["summary"], r["rack"]["occupancy"], r["video"]
    rows, devices, seams, rack = of["rows"], r["devices"], r["seams"], r["rack"]
    quality = {m["frame"]: m["quality"] for m in v["map"]}
    dev_of = {u: d for d in devices for u in d["units"]}
    rh, lh = rack["unit_height_px"], lad["unit_height_px"]
    used = set(v["frames_used"])

    # the selected device against ours
    match = None
    if selected:
        bx = selected["bbox_xyxy"]
        def inter(a, b): return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
        def area(a): return (a[2] - a[0]) * (a[3] - a[1])
        best = None
        for d in devices:
            pb = [min(p[0] for p in d["polygon"]), min(p[1] for p in d["polygon"]), max(p[0] for p in d["polygon"]), max(p[1] for p in d["polygon"])]
            iou = inter(pb, bx) / max(1, area(pb) + area(bx) - inter(pb, bx))
            if best is None or iou > best[0]:
                best = (iou, d)
        match = best

    # ---------------------------------------------------------------- tabs
    T = {}
    # Input
    st = r.get("stitch") or {}
    T["input"] = (
        ccards([("Video length", f'{vf["seconds"]:.1f} s', None, f'{vf["frames"]} frames, {vf["fps"]:.0f} per second'), ("Video size", f'{vf["w"]}×{vf["h"]}', None, "pixels per frame"),
                ("Rack picture", f'{rack_wh[0]}×{rack_wh[1]}', None, f'stitched from the video in {st["seconds"]:.0f} s' if st.get("seconds") else "stitched from the video"),
                ("Clearest frames used", len(used), None, "for the analysis")])
        + '<section class="card block"><h2>The video is the only input</h2>'
        + '<p class="plain">The video is stitched into one straight picture of the whole rack; every result in this report is drawn on that picture, and the clearest frames of the video check every unit and every device again.</p>'
        + '<div class="flowpics">' + fig("pics/input_video.jpg", "The video", f'{vf["w"]}×{vf["h"]} · {vf["seconds"]:.1f} s · top to bottom of the rack')
        + '<span class="farrow">→</span>' + fig("pics/input_rack.jpg", "The rack picture stitched from it", f'{rack_wh[0]}×{rack_wh[1]} px' + (f' · {st["keyframes_used"]} sharp frames joined' if st.get("keyframes_used") else "")) + "</div></section>"
        + stitch_card(st) + "</section>")
    # Step 1
    miss = [u for u in rows if not u["rack_unit"]]
    cover = {}
    for u in rows:
        if u["rack_unit"]: cover.setdefault(u["rack_unit"], []).append(u["unit"])
    merged = {k: v for k, v in cover.items() if len(v) > 1}
    def pair_crop(y1, y2, tag):
        """The same band of the rack from both ladder pictures, saved side by side for one finding."""
        outs = []
        for name, key in (("rack_units_rackonly.jpg", "rack"), ("rack_units.jpg", "frames")):
            im = Image.open(res_dir / name); k = im.height / rack_wh[1]
            band = max(1.0, y2 - y1); a_ = max(0, int((y1 - 0.6 * band) * k)); b_ = min(im.height, int((y2 + 0.6 * band) * k))
            fn = f"s1_{tag}_{key}.jpg"; im.crop((0, a_, im.width, b_)).save(pics / fn, quality=86); outs.append(fn)
        return outs
    findings = []
    if miss:
        y1, y2 = min(u["box"][1] for u in miss), max(u["box"][3] for u in miss); c1, c2 = pair_crop(y1, y2, "miss")
        findings.append(("Not found", f'{len(miss)} unit{"s" if len(miss) != 1 else ""}', ", ".join(u["unit"].upper() for u in miss), c1, c2,
                         ["The rack image's ladder stops before this band.", "Here the stitched image is dark and the rail holes are faint, so no unit line was drawn."],
                         [f"The frames show this band up close, in sharp photos.", f"Their ladders reach it, so {len(miss)} unit{'s are' if len(miss) != 1 else ' is'} added."]))
    for k_, v_ in merged.items():
        ru = next(x for x in rack["rows"] if x["unit"] == k_); c1, c2 = pair_crop(ru["box"][1], ru["box"][3], f"merge_{k_}")
        findings.append(("Merged", f'{len(v_)} units in one', f'{k_.upper()} = {" + ".join(x.upper() for x in v_)}', c1, c2,
                         [f"Its {k_.upper()} is {ru['box'][3] - ru['box'][1]} px tall, against a {rack['pitch_px']:.0f} px pitch.", f"One row covers {len(v_)} devices."],
                         [f"Every frame draws its own line between them.", f"The voted line splits the row into {' and '.join(x.upper() for x in v_)}."]))
    n_merged = sum(len(v_) - 1 for v_ in merged.values())
    eq = (f'<div class="eq"><div class="eqt frm"><b>{lad["units"]}</b><span>units from the frames</span></div>'
          + (f'<em>−</em><div class="eqt bad"><b>{len(miss)}</b><span>not found</span></div>' if miss else "")
          + (f'<em>−</em><div class="eqt bad"><b>{n_merged}</b><span>lost to merging</span></div>' if n_merged else "")
          + f'<em>=</em><div class="eqt img"><b>{rack["units"]}</b><span>units on the rack image alone</span></div></div>')
    li = lambda xs: "".join(f"<li>{x}</li>" for x in xs)
    cards_ = "".join(
        f'<article class="find"><header><span class="ftag">{esc(kind)}</span><b>{esc(what)}</b><span class="muted">{esc(units)}</span></header>'
        f'<div class="fpics"><figure><figcaption>Rack image alone</figcaption><img src="pics/{c1}" alt="rack image alone" loading="lazy"></figure>'
        f'<figure><figcaption>From the frames</figcaption><img src="pics/{c2}" alt="from the frames" loading="lazy"></figure></div>'
        f'<div class="vs"><div class="m img"><b>Why the rack image missed it</b><ul>{li(w1)}</ul></div><div class="m frm"><b>How the frames fixed it</b><ul>{li(w2)}</ul></div></div></article>'
        for kind, what, units, c1, c2, w1, w2 in findings)
    ladder_story = cards_
    frame_rows = [(n(f["frame"]), n(f["quality"]), n(f'{f["rack_y"][0]}-{f["rack_y"][1]}'), n(f["units"]), n(f'{f["pitch_on_rack"]} px'), n(f'{f["occupied"]} · {f["free"]} · {f["uncertain"]}'), n(f["boxes"])) for f in r["frames"]]
    T["step1"] = (
        ccards([("Units", lad["units"], rack["units"], ""), ("Unit height", f'{lh[0]}-{lh[1]} px', f'{rh[0]}-{rh[1]} px', ""),
                ("Unit pitch", f'{lad["pitch_px"]:.0f} px', f'{rack["pitch_px"]:.0f} px', ""), ("Unit lines voted", lad["votes"], None, f'from {lad["frames"]} frames'),
                ("Lines filled in", lad["inserted"], None, "where no frame drew one")])
        + '<section class="card block"><div class="pair">'
        + fig("pics/rack_units_rackonly.jpg", "Rack image alone", f'{rack["units"]} units · {rh[0]}-{rh[1]} px tall')
        + fig("pics/rack_units.jpg", "From the frames", f'{lad["units"]} units · {lh[0]}-{lh[1]} px tall')
        + "</div>"
        + "</section>"
        + '<section class="card block"><h2>Why ' + str(rack['units']) + ' and not ' + str(lad['units']) + '</h2>' + eq + '<div class="ladwrap">' + ladder_svg(rack["rows"], rows)
        + '<div class="ladtext">' + ladder_story + "</div></div>"
        + vs([f"One ladder fitted once on the stitched image, pitch {rack['pitch_px']:.0f} px.", f"{rack['units']} units, {rh[0]}-{rh[1]} px tall.",
              "Where the stitching ghosts or stretches the rails, units merge or are missed."],
             [f"Every one of the {lad['frames']} frames gets its own ladder, as a real photo.", f"Their {lad['votes']} unit lines are mapped onto the rack image and voted.",
              f"{lad['units']} units, {lh[0]}-{lh[1]} px tall" + (f", {lad['inserted']} filled in at the local pitch." if lad["inserted"] else ".")])
        + "</section>")
    # Step 2
    changed_units = [u for u in rows if u["changed"]]
    by_frames = sum(1 for u in rows if u["frames_voting"] >= 2)
    max_votes = max([u["frames_voting"] for u in rows] + [0])

    # why the stitched picture left so many units uncertain, and what the frames decided for them
    unc_rack = [x for x in rack["rows"] if x["status"] == "uncertain"]
    disagree = [x for x in unc_rack if "warning" in (x.get("decided_by") or "")]
    unsure = [x for x in unc_rack if "warning" not in (x.get("decided_by") or "")]
    side = any("Side-angle" in (x.get("reason") or "") for x in unsure)
    fr_of = [u for u in rows if u["rack_status"] == "uncertain"]
    became = {k: [u for u in fr_of if u["status"] == k] for k in ("occupied", "unoccupied", "uncertain")}
    ulist = lambda xs: ", ".join(x["unit"].upper() for x in xs)
    occ_cards = []
    if disagree:
        occ_cards.append(("bad", f"{len(disagree)} units: the two checks disagreed",
                          "On the stitched picture the device finder saw a device on these units, but the classifier, which looks only at the blurred strip of one unit, said free. "
                          "Our rule is that when the two disagree, a person should check, so the unit is marked uncertain.", ulist(disagree)))
    if unsure:
        occ_cards.append(("bad", f"{len(unsure)} unit{'s' if len(unsure) != 1 else ''}: the classifier was not sure",
                          ("The stitched picture looks as if it was taken from the side, so the rule asks the classifier to be 90 % sure before deciding, and it was not. " if side else
                           "The classifier was less than 65 % sure either way, so it did not decide. "), ulist(unsure)))
    dec = []
    if became["occupied"]: dec.append(f"<li><b>{len(became['occupied'])} became occupied</b>: the detector drew a box over them in most of the frames that saw them. <span class=muted>{ulist(became['occupied'])}</span></li>")
    if became["unoccupied"]: dec.append(f"<li><b>{len(became['unoccupied'])} became free</b>: no frame saw a device there, and the classifiers agreed over many frames. <span class=muted>{ulist(became['unoccupied'])}</span></li>")
    if became["uncertain"]: dec.append(f"<li><b>{len(became['uncertain'])} stayed uncertain</b>: the frames still disagree, so it is left for a person. <span class=muted>{ulist(became['uncertain'])}</span></li>")
    split = len(fr_of) - len(unc_rack)
    occ_story = ('<section class="card block"><h2>Why the stitched picture left ' + f'{len(unc_rack)} of {rack["units"]}' + ' units uncertain, and how the frames decided them</h2><div class="w3o">'
                 + "".join(f'<div class="wbox {c}"><b>{esc(t)}</b><p>{esc(txt)}</p><p class="muted">{esc(us)}</p></div>' for c, t, txt, us in occ_cards)
                 + f'<div class="wbox good"><b>How the frames decided them</b><p>Each frame is a sharp photo, so the front of the device, its ports and its lights are visible. And every unit is seen by several frames, so one blurred view no longer decides.</p>'
                 + f'<ul>{"".join(dec)}</ul>' + (f'<p class="muted">The frames see {len(fr_of)} units here, not {len(unc_rack)}, because they split one unit the stitched picture had merged.</p>' if split > 0 else "") + "</div></div></section>")
    T["step2"] = (
        ccards([("Occupied", os_["occupied"], ro["occupied"], ""), ("Free", os_["free"], ro["free"], ""), ("Uncertain", os_["uncertain"], ro["uncertain"], ""),
                ("Decided by the frames", f'{by_frames} of {lad["units"]}', None, "units seen by 2 or more frames"), ("Changed by the frames", of["changed"], None, "units")])
        + '<section class="card block"><div class="pair">'
        + fig("pics/rack_occupancy.jpg", "Rack image alone", f'{ro["occupied"]} occupied · {ro["free"]} free · {ro["uncertain"]} uncertain')
        + fig("pics/rack_occupancy_frames.jpg", "From the frames", f'{os_["occupied"]} occupied · {os_["free"]} free · {os_["uncertain"]} uncertain')
        + "</div>"
        + '<div class="bars">' + bars("From the frames", {"occupied": os_["occupied"], "unoccupied": os_["free"], "uncertain": os_["uncertain"]}) + bars("Rack image alone", {"occupied": ro["occupied"], "unoccupied": ro["free"], "uncertain": ro["uncertain"]}) + "</div>"
        + vs(["Every unit is seen once, on the stitched pixels.", f"{ro['uncertain']} of {rack['units']} units left uncertain."],
             [f"Every unit is seen by up to {max_votes} frames, each run as a photo.", "A detector box in at least half of them makes it occupied; an empty slot makes it free; otherwise the classifiers' mean.",
              f"{by_frames} of {lad['units']} units decided by the frames · {of['changed']} changed."])
        + "</section>" + occ_story + '<section class="card block">'
        + "<h2>Verification done in the frames, unit by unit</h2>"
        + legend([("box", "a detector box over the unit in this frame"), ("empty", "an empty slot or closed panel"), ("none", "neither; only the classifier spoke")])
        + '<p class="muted hint">Each chip is one frame that saw the unit, with its frame number. Click a row for the two full messages.</p>'
        + '<div class="evs">' + "".join(ev_row(
            f'<span class="u">{esc(u["unit"].upper())}</span><span class="rs">{pill(u["rack_status"]) if u["rack_status"] else "<span class=muted>not found</span>"}<em>→</em>{pill(u["status"])}{" <b class=chg>changed</b>" if u["changed"] else ""}</span>{unit_chips(u)}<span class="sv">{esc(short_unit(u))}</span>',
            two_msgs(u.get("why_image"), f'<p>{esc(u["reason"])}</p>')) for u in rows) + "</div>"
        + "</section>")
    # Step 3
    sm = sl = sr = 0
    for s in seams:
        m = re.search(r"\((\d+) model, (\d+) logic\)", s["decided_by"] or "")
        if m:
            if int(m.group(1)) >= int(m.group(2)): sm += 1
            else: sl += 1
        else:
            sr += 1

    T["step3"] = (
        ccards([("Devices", len(devices), len(rack["devices"]), ""), ("Same span on both", f'{sum(1 for d in devices if d["same_span_as_rack"])} of {len(devices)}', None, "devices"),
                ("Boundaries decided by boxes", sm, None, f'{sl} by the logic' + (f', {sr} by the rack image' if sr else "")), ("Boundaries changed", r["changed_seams"], None, "against the rack image"),
                ("Device boxes", r["frame_polygons"], None, f'from {lad["frames"]} frames')])
        + '<section class="card block"><div class="pair">'
        + fig("pics/rack_devices_rackonly.jpg", "Rack image alone", f'{len(rack["devices"])} devices, each drawn as the rack image\'s own detector box')
        + fig("pics/rack_devices.jpg", "From the frames", f'{len(devices)} devices, each drawn as the polygon the frames saw')
        + "</div>"
        + vs(["One pass of the detector on the stitched image.", "Where no box owns both neighbouring units, the logic reads the pixels."],
             ["Every frame's boxes vote each seam: one box over both units = the same device, two boxes = a new device.",
              f"{sm} seams decided mostly by the boxes, {sl} by the logic" + (f", {sr} left to the rack image." if sr else "."),
              f"{r['changed_seams']} seams changed · every device drawn as the polygon the frames saw."])
        + "<h2>Devices</h2>"
        + '<p class="muted hint">Each chip is a frame whose detector box shaped the device. Click a row for the two full messages.</p>'
        + '<div class="evs">' + "".join(ev_row(
            f'<span class="u">{dot(d["index"])}D{d["index"]}</span><span class="rs"><b>{esc(d["top"].upper())}{" - " + esc(d["bottom"].upper()) if d["height"] > 1 else ""}</b> · {d["height"]}U</span>'
            f'<span class="chips">{"".join(chip(f_, "box", "its detector box shaped this device") for f_ in d.get("frames_shape") or []) or "<span class=muted>no frame box owns it</span>"}</span>'
            f'<span class="sv">{best_cell(d)}{"" if d["same_span_as_rack"] else " <b class=chg>not on the rack image</b>"}</span>',
            two_msgs(d.get("why_image"), "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in sentences(d["reason"])) + "</ul>")) for d in devices) + "</div>"
        + "<h2>Seams, every one between two occupied units</h2>"
        + legend([("new", "two detector boxes: a new device"), ("same", "one box over both: the same device"), ("new logic", "no box owns both; the logic said new"), ("same logic", "no box owns both; the logic said same")])
        + '<div class="evs">' + "".join(ev_row(
            f'<span class="u">{esc(s_["above"].upper())}/{esc(s_["below"].upper())}</span><span class="rs">{("<span class=pill>" + ("new" if s_["rack_p_new_device"] >= 0.5 else "same") + "</span>") if s_["rack_p_new_device"] is not None else "<span class=muted>no seam</span>"}<em>→</em><span class="pill {"newp" if s_["new_device"] else "samep"}">{"new device" if s_["new_device"] else "same device"}</span>{" <b class=chg>changed</b>" if s_["changed"] else ""}</span>{seam_chips(s_)}<span class="sv">{esc(short_seam(s_))}</span>',
            two_msgs(s_.get("why_image"), f'<p>{esc(s_["reason"])}</p>')) for s_ in seams) + "</div>"
        + "</section>"
        + '<section class="card block">' + fig("pics/rack_frame_boxes.jpg", "Every frame's device boxes on the rack", f'{r["frame_polygons"]} boxes from {lad["frames"]} frames, each mapped by its four corners · one colour per frame')
        + why("Each box is drawn exactly where that frame's homography puts it, as a polygon. Where the frames agree the polygons pile up on the device; the small tilt is the frames' own tilt against the rack image.") + "</section>")
    # Report
    T["report"] = (
        ccards([("Units", lad["units"], rack["units"], ""), ("Occupied", os_["occupied"], ro["occupied"], ""), ("Free", os_["free"], ro["free"], ""), ("Uncertain", os_["uncertain"], ro["uncertain"], ""),
                ("Devices", len(devices), len(rack["devices"]), ""), ("Run time", f'{sum(r["times_s"].values()):.0f} s', None, f'{len(used)} frames used')])
        + '<section class="card block"><h2>The rack, unit by unit</h2>' + elevation(rows, devices)
        + why("Read across each row: what the rack image alone said, what the frames decided, and the device the unit belongs to. Orange-edged cells are where the frames changed the answer or found a unit the rack image did not have.") + "</section>"
        )
    # Frames: in plain words
    cap = cv2.VideoCapture(str(video_path)); k_ = 0; need = set(used)
    while need:
        ok, fr = cap.read()
        if not ok: break
        if k_ in need:
            im = Image.fromarray(fr[:, :, ::-1]); im.thumbnail((480, 480)); im.save(pics / f"used_f{k_}.jpg", quality=84); need.discard(k_)
        k_ += 1
    cap.release()
    def shows(f):
        inside = [u["unit"].upper() for u in rows if u["box"][1] >= f["rack_y"][0] and u["box"][3] <= f["rack_y"][1]]
        return f"{inside[0]} to {inside[-1]}" if len(inside) > 1 else (inside[0] if inside else "the top of the rack")
    clear = lambda q: ("very clear", "good") if q >= 1.3 else ("clear", "ok") if q >= 1.0 else ("a little blurry", "soft")
    funnel = ('<div class="funnel">'
              + f'<div class="fn"><b>{vf["frames"]}</b><span>pictures in the video ({vf["seconds"]:.0f} seconds)</span></div><em>→</em>'
              + f'<div class="fn"><b>{v["frames_read"]}</b><span>we looked at one in every {v["stride"]}</span></div><em>→</em>'
              + f'<div class="fn"><b>{v["frames_mapped"]}</b><span>could be placed on the rack picture</span></div><em>→</em>'
              + f'<div class="fn on"><b>{len(used)}</b><span>were the clearest, and were used</span></div></div>')
    cardsf = "".join(
        f'<figure class="fcard"><img src="pics/used_f{f["frame"]}.jpg" alt="frame {f["frame"]}" loading="lazy"><figcaption><b>Frame {f["frame"]}</b>'
        f'<span>shows {esc(shows(f))}</span><span class="cl {clear(f["quality"])[1]}">{clear(f["quality"])[0]}</span>'
        f'<span>found {f["units"]} units and {f["boxes"]} devices</span></figcaption></figure>' for f in r["frames"])
    film = "".join(f'<figure><img src="pics/{p_["file"]}" alt="frame {p_["frame"]}" loading="lazy"><figcaption>Frame {p_["frame"]}</figcaption></figure>' for p_ in r["projected"])
    T["frames"] = (
        '<section class="card block"><h2>What we took from the video</h2>' + funnel
        + '<p class="plain">The video walks down the rack from top to bottom. We do not need every picture: many are almost the same. We keep the clearest ones, so that every part of the rack is seen by at least three of them.</p></section>'
        + '<section class="card block"><h2>Which part of the rack each frame shows</h2>' + "__PLACE__"
        + '<p class="plain">One frame shows only a part of the rack. To find which part, we look for the same small details, such as screws, labels and ports, in the frame and in the big rack picture. Once they match, we know exactly where the frame sits: the outline of the same colour on the right.</p></section>'
        + f'<section class="card block"><h2>The {len(used)} frames we used</h2><p class="plain">Each one is treated like a normal photo of the rack: we count its units, check which are full, and find its devices. Then all of them vote together.</p>'
        + f'<div class="fcards">{cardsf}</div></section>'
        + f'<section class="card block"><h2>The final answer, drawn back on the frames</h2><p class="plain">After the vote, the devices are drawn back on the frames, so anyone can check them by eye.</p><div class="thumbs film">{film}</div></section>')
    # Best frames
    blocks = []
    for d in devices:
        tfs = d.get("top_frames") or []
        figs = "".join(f'<figure><img src="pics/{t["file"]}" alt="{esc(d["label"])} frame {t["frame"]}" loading="lazy"><figcaption><b>{["1st", "2nd", "3rd", "4th", "5th"][t["rank"] - 1]}</b> · frame {t["frame"]} · sharpness {t["score"]:.2f}</figcaption></figure>' for t in tfs if t.get("file"))
        blocks.append(f'<div class="devframes"><header>{dot(d["index"])}<b>{esc(d["label"])}</b><span>{esc(d["top"].upper())}{" - " + esc(d["bottom"].upper()) if d["height"] > 1 else ""} · {len(tfs)} best frame{"s" if len(tfs) != 1 else ""}</span></header><div class="thumbs crops">{figs or "<p class=muted>no mapped frame holds the whole device</p>"}</div></div>')
    T["best"] = ('<section class="card block"><h2>The five best frames of every device</h2>'
                 + '<p class="plain">For every device found, every frame of the video is checked: a frame counts only when the whole device is inside it, and the frames that count are ranked by how sharp the device itself is. These are the five sharpest, at least 10 frames apart, so they are five different views. The first one is the picture of the device used everywhere else in this report.</p>'
                 + "".join(blocks) + "</section>")
    T["input"] += T.pop("best")                 # the devices' best frames live in the Input tab, second section
    # Why each device: one picture, three short lines, details folded away
    seam_below = {s_["below"]: s_ for s_ in seams}
    idx_of = {u["unit"]: i for i, u in enumerate(rows)}
    def edge_line(d, top):
        k = idx_of[d["top"]] if top else idx_of[d["bottom"]] + 1          # the seam above the top unit, or above the unit under the bottom
        side = "Starts" if top else "Ends"
        if (top and k == 0) or (not top and k >= len(rows)):
            return f"{side} at the {'top' if top else 'bottom'} of the rack."
        sm = seam_below.get(rows[k]["unit"])
        if sm is None:
            return f"{side} {'under' if top else 'above'} a free unit."
        n_ = sm["frames_voting"]
        return f"{side} where {'another' if top else 'the next'} device {'ends' if top else 'begins'}: {'two separate boxes in ' + str(n_) + ' frames' if n_ >= 2 else 'decided on the rack image'}."
    def join_line(d):
        if d["height"] == 1:
            return f"One unit, {d['top'].upper()}: a device box over it in {len(d.get('frames_shape') or [])} frames."
        inner = [seam_below[u] for u in d["units"][1:] if u in seam_below]
        n_ = min((x["frames_voting"] for x in inner), default=0)
        return f"{d['height']} units, {d['top'].upper()} to {d['bottom'].upper()}: one box covers them all in {n_} frames, so they are one device."
    def rack_line(d):
        if d["same_span_as_rack"]:
            return "The rack image alone found the same device."
        return "The rack image alone did not find this device the same way." if "drew no device" not in (d.get("why_image") or "") else "The rack image alone found no device here."
    dcards = []
    for d in devices:
        sec = next((s_ for s_ in r["sections"] if s_["device"] == d["index"]), None)
        more = "".join(f"<li>{esc(x)}</li>" for x in sentences(d["reason"]))
        dcards.append(f'<article class="dev2" style="border-top-color:{PAL[(d["index"] - 1) % len(PAL)]}"><header>{dot(d["index"])}<b>D{d["index"]}</b><span>{esc(d["top"].upper())}{" - " + esc(d["bottom"].upper()) if d["height"] > 1 else ""} · {d["height"]}U</span>'
                      f'<em>best frame {d["best_frame"] if d["best_frame"] is not None else "-"}</em></header>'
                      + (f'<img src="pics/{sec["file"]}" alt="D{d["index"]}" loading="lazy">' if sec else "")
                      + f'<ul class="three"><li><i class="ico j"></i>{esc(join_line(d))}</li><li><i class="ico t"></i>{esc(edge_line(d, True))}</li><li><i class="ico b"></i>{esc(edge_line(d, False))}</li></ul>'
                      + f'<p class="rk {"same" if d["same_span_as_rack"] else "diff"}">{esc(rack_line(d))}</p>'
                      + f'<details><summary>All the details</summary><p class="muted">{esc(d.get("why_image") or "")}</p><ul>{more}</ul></details></article>')
    # device classification: our stand-in classifier on each device's top 5 best frames
    cl_ = [d for d in devices if (d.get("classification") or {}).get("label")]
    def kchip(lbl, prob):
        return f'<span class="kc{" oth" if lbl == "Other" else ""}"><b>{esc(lbl)}</b> {round(100 * prob)} %</span>'
    def kfrom(c):
        """Where a device's answer came from: the close-up, all its frames together (fused) or one frame."""
        if c.get("source") == "close-up":
            return "the close-up photo"
        if c.get("frame") is None and c.get("fused"):
            return f'all {c["fused"].get("views", 5)} frames together'
        return "frame " + str(c.get("frame"))

    def kframe(d, x):
        tf = next((t for t in d.get("top_frames") or [] if t["frame"] == x["frame"]), {})
        kept = x["frame"] == d["classification"].get("frame")
        sc = f'sharpness {tf["score"]:.2f} · {tf["inliers"]} matches' if tf.get("score") is not None else ""
        return (f'<figure class="kfig{" kept" if kept else ""}"><img src="pics/{esc(x.get("file") or "")}" alt="D{d["index"]}, frame {x["frame"]}" loading="lazy">'
                f'<figcaption><span class="kr">#{x["rank"]} · frame {x["frame"]}</span>{kchip(x["label"], x["prob"])}<span class="muted ks">{sc}</span></figcaption></figure>')
    if cl_:
        named = sum(1 for d in cl_ if d["classification"]["label"] != "Other")
        same_rk = sum(1 for d in cl_ if (d["classification"].get("rack") or {}).get("label") == d["classification"]["label"])
        rows_k = "".join(
            f'<tr><td class="n"><span class="u">{dot(d["index"])}D{d["index"]}</span><span class="muted kf">{esc(d["top"].upper())}{" - " + esc(d["bottom"].upper()) if d["height"] > 1 else ""} · {d["height"]}U</span></td>'
            f'<td>{kchip(d["classification"]["rack"]["label"], d["classification"]["rack"]["prob"]) if d["classification"].get("rack") else "<span class=muted>-</span>"}</td>'
            f'<td><div class="k3">{"".join(kframe(d, x) for x in d["classification"]["frames"])}</div></td>'
            f'<td>{kchip(d["classification"]["label"], d["classification"]["prob"])}<span class="muted kf">from {kfrom(d["classification"])}</span>'
            f'{"" if (d["classification"].get("rack") or {}).get("label") == d["classification"]["label"] else "<b class=chg kf>differs from the rack image</b>"}</td></tr>'
            for d in cl_)
        T["classify"] = (
            ccards([("Devices named", f"{named} of {len(cl_)}", None, f"{len(cl_) - named} called Other"),
                    ("Same as the rack image alone", f"{same_rk} of {len(cl_)}", None, "devices"),
                    ("Classifier", "ConvNeXt-Tiny", None, "ours, a stand-in until the final one")])
            + '<section class="card block"><h2>How we pick a device\'s best frames</h2>'
            + f'<p class="plain">Every frame of the video is checked for every device, not only the {len(r["frames"])} frames used for the rack. A frame passes three checks, and the frames that pass are ranked by one number:</p>'
            + '<div class="kform"><span><b>Found</b>the frame matches the rack around the device: at least 30 agreeing points</span><em>→</em>'
            + '<span><b>Whole</b>the device lies at least 20 px inside the frame, at 0.5-2× its size on the rack</span><em>→</em>'
            + '<span><b>Sharpness</b>edge strength of the device\'s own area divided by its contrast, so a bright frame is not mistaken for a sharp one</span><em>=</em>'
            + f'<span class="kres"><b>Top {RV.TOP_FRAMES}</b>the sharpest, at least 10 frames apart: five different views</span></div>'
            + f'<p class="plain">Each of the {NUM.get(RV.CLS_FRAMES, RV.CLS_FRAMES)} frames is cut along the device\'s shape and classified. The answer kept is the #1 frame\'s; a lower one speaks only when every frame above it said "Other". '
            + 'The same device is also cut from the stitched rack picture alone, for comparison.</p>'
            + '<p class="inote">This classifier is ours and will be replaced. It was trained on photos of 1 MP or more; video frames are smaller, so expect more "Other" answers here than on photos.</p></section>'
            + '<section class="card block"><h2>Every device</h2><div class="tw"><table class="ktab"><thead><tr><th>Device</th>'
            + f'<th>Rack image alone</th><th>Top {RV.CLS_FRAMES} best frames</th><th>Answer</th></tr></thead><tbody>' + rows_k + "</tbody></table></div></section>")
    else:
        T["classify"] = '<section class="card block"><h2>Device classification</h2><p class="plain">This run has no device classes yet. Run the video again to add them.</p></section>'
    # steps 5 and 6: device layout and port layout of every switch (the switch mapper on its best frame)
    sw_ = [d for d in devices if d.get("layout")]
    USED = lambda L: bool(L.get("fitted") or L.get("auto"))                               # a template fitted, or the agent's auto mode
    ok_ = [d for d in sw_ if d["layout"].get("ok") and USED(d["layout"])]
    for d in sw_:                                   # a frame's make is shown only when it is the switch's confirmed make
        conf = {str(v).lower() for v in ((d.get("closeup") or {}).get("vendor"), d["layout"].get("vendor")) if v}
        for k in ("reads", "extra"):
            d["layout"][k] = [dict(x, vendor=x["vendor"] if str(x.get("vendor")).lower() in conf else None) for x in d["layout"].get(k) or []]
    capt = r.get("capture") or {}
    def read_txt(x):
        """What one crop read, in a few words."""
        if x.get("vendor") and x.get("model"):
            return f'{esc(x["vendor"])} {esc(x["model"])}'
        if x.get("model"):
            return esc(x["model"])
        return f'{esc(x["vendor"])} · no model' if x.get("vendor") else "nothing read"
    def lthumbs(items, used=None):
        """A strip of small same-size crops, one line of caption each; the crop used is marked."""
        out = []
        for x in items:
            on = used is not None and x.get("frame") == used
            img = (f'<img src="pics/{esc(x["file"])}" alt="frame {x["frame"]}" loading="lazy">' if x.get("file") else '<div class="lnoimg"></div>')
            out.append(f'<figure class="lthumb{" on" if on else ""}">{img}<figcaption><b>#{x["rank"]} · frame {x["frame"]}'
                       f'{"<em>used</em>" if on else ""}</b><span>{read_txt(x)}</span></figcaption></figure>')
        return '<div class="lstrip">' + "".join(out) + "</div>"
    def lname(L):
        return (esc(L["vendor"]) + (f' {esc(L["model"])}' if L.get("model") else ' <span class="muted">· model not read</span>')) if L.get("vendor") else '<span class="muted">make not read</span>'
    def lfig(f_, cap, cls=""):
        return f'<figure class="lfig{cls}"><img src="pics/{esc(f_)}" alt="{esc(cap)}" loading="lazy"><figcaption>{cap}</figcaption></figure>' if f_ else ""
    if ok_:
        # the rack with every mapped port, drawn from the ports carried onto the rack picture
        rk = rack_im.convert("RGB").copy(); dr = ImageDraw.Draw(rk, "RGBA")
        for d in ok_:
            c_ = tuple(int(PAL[(d["index"] - 1) % len(PAL)][i:i + 2], 16) for i in (1, 3, 5))
            for pt in d["layout"]["ports"]:
                if pt.get("rack_polygon"):
                    dr.polygon([tuple(q) for q in pt["rack_polygon"]], fill=c_ + (120 if pt.get("status") == "connected" else 30,), outline=c_ + (255,))
        rk.thumbnail((1400, 2400)); rk.save(pics / "rack_ports.jpg", quality=86)
    PL = lambda d: d["closeup"] if USED(d.get("closeup") or {}) else d["layout"]     # the layout that gives the ports
    pok_ = [d for d in sw_ if USED(PL(d))]
    AUTO = lambda d: bool(PL(d).get("auto")) and not PL(d).get("fitted")
    n_ports = sum(PL(d).get("n_ports") or 0 for d in pok_); n_cab = sum(PL(d).get("connected") or 0 for d in pok_)
    def lclose(d):
        """Upload a close-up of this switch (works when the report is opened from the app)."""
        return (f'<div class="lclose" data-dev="{d["index"]}"><div class="lcrow"><label class="btnup"><input type="file" accept="image/*" hidden>Upload a close-up ↑</label>'
                '<span class="muted lcnote">a sharp front photo: make, model and ports are read from it</span></div><div class="cres"></div></div>')
    def lcard(d):
        """One switch = one card: header (name, make + model, status), the three sources in order, the pictures, its top frames, the upload."""
        L = d["layout"]; C = d.get("closeup") or {}; R = L.get("rack_read") or {}; I = RV.identity(d)
        vendor, model = I["vendor"], I["model"]
        st = lstat(d)
        note = ""
        if not model and L.get("mode") == "conflict" and L.get("conflict"):
            note = "frames read " + " and ".join(esc(m) for m in L["conflict"])
        elif not model and L.get("mode") == "lone_read" and L.get("lone_read"):
            note = f'one frame read {esc(L["lone_read"])}, not confirmed'
        head = (f'<div class="lhd"><div class="lid"><span class="lnum">{dot(d["index"])}D{d["index"]} · {esc(d["top"].upper())}</span>'
                f'<span class="lmk">{lname({"vendor": vendor, "model": model})}</span></div><div class="lst"><span class="pill {st[0]}">{st[1]}</span></div></div>'
                + (f'<div class="lsub"><span class="muted">{note}</span></div>' if note else ""))
        # the three sources, in the order they are tried
        reads = L.get("reads") or []
        fr_ = next((x for x in reads if x.get("frame") == L.get("frame")), reads[0] if reads else {})
        old = "source" not in L and "make_source" not in L           # a run from before the ordered sources
        s2 = {"vendor": L.get("vendor") if (old or L.get("source") == 2 or L.get("make_source") == 2) else None,
              "model": L.get("model") if (old or L.get("source") == 2) else None}
        m_ = C.get("match") or {}
        s3_sub = ""
        if C and m_:
            s3_sub = ("not matched to the rack image" if m_.get("matched") is False else
                      f'matched by {esc(m_.get("by") or "rack image")}' + (f' · {m_["inliers"]} points' if m_.get("inliers") else ""))
        n_ = I["source"] or I["make_source"]
        boxes = [(1, R.get("file"), read_txt(R) if R else "not read", ""),
                 (2, fr_.get("file"), read_txt(s2) if reads else "no frames", f'frame {fr_["frame"]}' if fr_.get("frame") is not None else ""),
                 (3, (C.get("pictures") or {}).get("cut"), read_txt(C) if C else "no upload yet", s3_sub)]
        srcs = '<ol class="l3">' + "".join(
            f'<li class="{"on" if k == n_ else ("off" if n_ and k > n_ else "")}"><span class="l3n">{k}</span><div class="l3b"><b>{RV.SOURCES[k - 1].capitalize()}'
            f'{"<em>used</em>" if k == n_ else ""}</b>' + (f'<img src="pics/{esc(f_)}" alt="{RV.SOURCES[k - 1]}" loading="lazy">' if f_ else '<div class="lnoimg"></div>')
            + f'<span>{t_}</span>' + (f'<small>{sub}</small>' if sub else "") + "</div></li>"
            for k, f_, t_, sub in boxes) + "</ol>"
        # the pictures of the source used, when its vendor layout fitted
        figs = []
        lay = I["layout"]
        if lay and I["fitted"]:
            pics_ = lay.get("pictures") or {}
            figs.append(lfig(pics_.get("cut"), {1: "Stitched rack crop", 2: f'The switch in frame {L.get("frame")}', 3: "Uploaded crop"}.get(I["source"], "The switch")))
            figs.append(lfig(pics_.get("vendor_ports"), "Vendor front image, ports numbered"))
            figs.append(lfig(pics_.get("rack_trace"), "Vendor layout on the rack image") if pics_.get("rack_trace")
                        else '<figure class="lfig none"><span>No rack-image overlay for this switch yet</span></figure>')
        res = f'<div class="lres">{"".join(f for f in figs if f)}</div>' if any(figs) else ""
        strip = (f'<div class="lblk"><span class="ik">Step 2 in detail: the top {len(reads)} best frames</span>' + lthumbs(reads, L.get("frame") if I["source"] == 2 else None) + "</div>") if reads else ""
        return f'<section class="card lsw" data-sw="{d["index"]}"{"" if d is sw_[0] else " hidden"}>{head}{srcs}{res}{strip}{lclose(d)}</section>'
    def lstat(d):
        I = RV.identity(d)
        if I["fitted"]:
            return "free", "vendor layout fitted"
        if AUTO(d):
            return "unc", "auto mode: ports from the photo"
        if I["model"]:
            return "", "no vendor layout"
        if d["layout"].get("mode") == "conflict":
            return "unc", "frames disagree"
        return "", "model not read"
    def lpicker():
        """The switches on the stitched rack image + a chip list; clicking one shows its card."""
        W, H = rack_wh
        ys = [q[1] for d in sw_ for q in d.get("polygon") or []]
        y0 = max(0, min(ys) - 120) if ys else 0; y1 = min(H, max(ys) + 120) if ys else H
        polys = "".join(
            f'<g class="lpg{" on" if d is sw_[0] else ""}" data-pick="{d["index"]}"><polygon points="{" ".join(f"{x:.0f},{y:.0f}" for x, y in d["polygon"])}" style="stroke:{PAL[(d["index"] - 1) % len(PAL)]}"/>'
            f'<text x="{min(q[0] for q in d["polygon"]) + 10:.0f}" y="{min(q[1] for q in d["polygon"]) + 34:.0f}">D{d["index"]} · {esc(d["top"].upper())}</text></g>'
            for d in sw_ if d.get("polygon"))
        svg = (f'<svg class="lrack" viewBox="0 {y0:.0f} {W} {y1 - y0:.0f}" role="img" aria-label="the switches on the rack image">'
               f'<image href="pics/input_rack.jpg" x="0" y="0" width="{W}" height="{H}"/>{polys}</svg>')
        chips = "".join(
            f'<button type="button" class="lchip{" on" if d is sw_[0] else ""}" data-pick="{d["index"]}"><span class="lsd {lstat(d)[0]}" title="{lstat(d)[1]}"></span>'
            f'<b>D{d["index"]} · {esc(d["top"].upper())}</b><span>{lname(RV.identity(d))}</span></button>'
            for d in sw_)
        return f'<aside class="card lpick">{svg}<div class="lchips">{chips}</div></aside>'
    T["layout"] = (
        ccards([("Switches", len(sw_), None, "on this rack"),
                ("Make read", f'{sum(1 for d in sw_ if RV.identity(d)["vendor"])} of {len(sw_)}', None, "from the panel text"),
                ("Model read", f'{sum(1 for d in sw_ if RV.identity(d)["model"])} of {len(sw_)}', None, "exact model" + (", with uploaded crops" if any(d.get("closeup") for d in sw_) else "")),
                ("Vendor layout fitted", f'{sum(1 for d in sw_ if RV.identity(d)["fitted"])} of {len(sw_)}', None, "numbered from the vendor's front image"),
                ("Auto mode", f'{sum(1 for d in sw_ if AUTO(d))} of {len(sw_)}', None, "ports boxed from the photo, no template")])
        + '<section class="card block"><h2>How a switch gets its layout</h2><ol class="lflow">'
        + '<li><b>Stitched rack crop</b><span>the switch\'s crop of the rack image is read first</span></li>'
        + f'<li><b>Top {RV.LAYOUT_FRAMES} frames</b><span>then its {NUM.get(RV.LAYOUT_FRAMES, RV.LAYOUT_FRAMES)} sharpest video frames</span></li>'
        + '<li><b>Uploaded crop</b><span>then your close-up photo, if any</span></li>'
        + '<li><b>Vendor layout</b><span>the model\'s front view: every port, its type and number</span></li></ol>'
        + '<p class="inote">The first step that reads the model gives make, model and layout; with no model, the first that reads the make. With no fit the agent\'s auto mode boxes the ports it sees in the photo and numbers them left to right.</p></section>'
        + ('<div class="lmd">' + lpicker() + '<div class="lsws">' + "".join(lcard(d) for d in sw_) + "</div></div>" if sw_ else ""))
    # Port layout: every switch, its port map and every port (src/port_details.py); a close-up's details win when given
    PDL = lambda d: (d.get("closeup") or {}).get("port_details") or (d["layout"].get("port_details") or {})
    allp = [p for d in sw_ for p in PDL(d).get("ports") or []]
    STAT = {"connected": ("occ", "connected"), "empty": ("free", "empty"), "unknown": ("unc", "not sure")}
    def port_row(p):
        st = STAT.get(p.get("status"), STAT["unknown"])
        more = (f'{esc(p.get("cable_connector") or "")} cable, {esc(p.get("cable_colour") or "")} <span class="muted">({round(100 * (p.get("cable_confidence") or 0))} %)</span>'
                if p.get("cable_type") else (f'dust cap: {esc(p["cap_state"])}' if p.get("cap_state") else ""))
        return (f'<tr><td class="n">{p["id"]}</td><td>{esc((p.get("type") or "").upper())}</td><td>{esc(p.get("spec") or "-")}</td>'
                f'<td><span class="pill {st[0]}">{st[1]}</span></td><td>{more or "<span class=muted>-</span>"}</td></tr>')
    def ports_block(d):
        P_ = PDL(d)
        if not P_.get("ports"):
            return f'<p class="muted">No ports found on this switch{(": " + esc(P_["error"][:120])) if P_.get("error") else ""}.</p>'
        c = P_.get("counts") or {}; caps = sum(1 for p in P_["ports"] if p.get("cap_state") == "capped")
        src_ = ('numbered from the vendor layout' if P_.get("source") == "vendor layout" else
                'positions from the photo: no vendor layout, so the numbers are the order found and there is no spec until the model is known (upload a close-up in Device layout)')
        spec = P_.get("spec") or {}
        spec_txt = (esc(spec["port_config"]) + (f' · PoE {esc(spec["poe_standard"])}' if spec.get("poe_standard") else "")) if spec.get("port_config") else (
            esc(spec.get("note") or "") if spec else "")
        pic = P_.get("picture")
        return ('<div class="pmeta">' + f'<span class="pill">{P_["n"]} ports</span><span class="pill occ">{c.get("connected", 0)} connected</span>'
                f'<span class="pill free">{c.get("empty", 0)} empty{f" ({caps} capped)" if caps else ""}</span><span class="pill unc">{c.get("unknown", 0)} not sure</span>'
                f'<span class="muted">{src_}</span></div>'
                + (f'<p class="pspec"><b>Specification</b> {spec_txt}</p>' if spec_txt else "")
                + (f'<figure class="lfig wide"><img src="pics/{esc(pic)}" alt="every port of the switch" loading="lazy"><figcaption>blue filled: a cable is plugged in · green: empty · grey: not sure</figcaption></figure>' if pic else "")
                + '<details class="ptab"><summary>Every port</summary><div class="tw"><table><thead><tr><th>Port</th><th>Type</th><th>Spec</th><th>Status</th><th>Cable or cap</th></tr></thead><tbody>'
                + "".join(port_row(p) for p in P_["ports"]) + "</tbody></table></div></details>")
    T["ports"] = (
        ccards([("Switches", len(sw_), None, "every switch on the rack"), ("Ports found", len(allp), None, "on all switches"),
                ("Connected", sum(p.get("status") == "connected" for p in allp), None, "a cable is plugged in"),
                ("Empty", sum(p.get("status") == "empty" for p in allp), None, "no cable"),
                ("Not sure", sum(p.get("status") == "unknown" for p in allp), None, "hidden or blurred")])
        + '<section class="card block"><h2>How every port is read</h2><ol class="flow">'
        + '<li><b>Find the ports</b><span>the vendor layout\'s numbered ports when it fitted; otherwise RT-EOS\'s port logic finds the port grid in the photo</span></li>'
        + '<li><b>Empty or connected</b><span>RT-EOS\'s port status model (port_connected_or_empty, the old port_count) on enlarged pieces of the switch</span></li>'
        + '<li><b>Connected</b><span>RT-EOS\'s cable model reads the cable\'s type and colour around the port</span></li>'
        + '<li><b>Empty</b><span>RT-EOS\'s dust-cap model says whether a cap is fitted</span></li>'
        + '<li><b>Specification</b><span>RT-EOS\'s spec agent gives the port configuration of the make and model: each port\'s type and speed</span></li></ol>'
        + '<p class="inote">"Not sure" means the status model saw no port there: a cable or blur hides it. Nothing is guessed.</p></section>'
        + '<section class="card block"><h2>Every switch</h2><div class="lrows">' + "".join(
            f'<div class="lrow"><div class="lh"><span class="u">{dot(d["index"])}D{d["index"]}</span><span class="muted">{esc(d["top"].upper())}</span>'
            f'<b>{lname(d["closeup"]) if (d.get("closeup") or {}).get("port_details") else lname(d["layout"])}</b>'
            + ('<span class="pill">from a close-up photo</span>' if (d.get("closeup") or {}).get("port_details") else "") + "</div>"
            + ports_block(d) + "</div>" for d in sw_) + "</div></section>")
    if not sw_:
        T["layout"] = T["ports"] = '<section class="card block"><p class="plain">No device on this rack is classified as a Switch, Router, Firewall or Gateway, so there is no device or port layout.</p></section>'
    T["why"] = ('<section class="card block"><h2>Why each device is what it is</h2>'
                + '<p class="plain">For every device: what holds its units together, where it starts, where it ends, and whether the rack image alone agreed. Open "All the details" for the full reasons.</p>'
                + f'<div class="devs2">{"".join(dcards)}</div></section>')
    # Image vs frames
    cu = "".join(f'<li>{esc(u["unit"].upper())}: {pill(u["rack_status"])} on the rack image, {pill(u["status"])} from the frames <span class="muted">{esc(u["decided_by"])}</span></li>' for u in changed_units) or "<li>none</li>"
    cs = "".join(f'<li>{esc(s["above"].upper())} / {esc(s["below"].upper())}: {"same device" if s["rack_p_new_device"] >= 0.5 else "new device"} on the rack image, {"new device" if s["new_device"] else "same device"} from the frames <span class="muted">{esc(s["decided_by"])}</span></li>' for s in seams if s["changed"]) or "<li>none</li>"
    diff_dev = [d for d in devices if not d["same_span_as_rack"]]
    cd = "".join(f'<li>{dot(d["index"])}{esc(d["label"])} over {esc(d["top"].upper())}{" - " + esc(d["bottom"].upper()) if d["height"] > 1 else ""}: no device with this span on the rack image</li>' for d in diff_dev) or "<li>none</li>"
    T["compare"] = (
        '<section class="card block"><h2>Rack image alone and frames, side by side</h2>'
        + table(["", "Rack image alone", "From the frames", "What the frames changed"], [
            ("Units", n(f'{rack["units"]} · {rh[0]}-{rh[1]} px'), n(f'{lad["units"]} · {lh[0]}-{lh[1]} px'), f'{abs(lad["units"] - rack["units"])} unit{"s" if abs(lad["units"] - rack["units"]) != 1 else ""} {"more" if lad["units"] > rack["units"] else "fewer"}; heights spread {rh[1] - rh[0]} px on the rack image, {lh[1] - lh[0]} px from the frames'),
            ("Occupancy", n(f'{ro["occupied"]} · {ro["free"]} · {ro["uncertain"]}'), n(f'{os_["occupied"]} · {os_["free"]} · {os_["uncertain"]}'), f'{of["changed"]} units changed; {ro["uncertain"]} uncertain became {os_["uncertain"]}'),
            ("Devices", n(f'{len(rack["devices"])}'), n(f'{len(devices)}'), f'{r["changed_seams"]} seams changed; {len(diff_dev)} device{"s" if len(diff_dev) != 1 else ""} with a span the rack image did not have')])
        + '<div class="bars">' + bars("From the frames", {"occupied": os_["occupied"], "unoccupied": os_["free"], "uncertain": os_["uncertain"]}) + bars("Rack image alone", {"occupied": ro["occupied"], "unoccupied": ro["free"], "uncertain": ro["uncertain"]}) + "</div></section>"
        + f'<section class="card block"><div class="cols"><div><h2>Units that changed</h2><ul class="list">{cu}</ul></div><div><h2>Seams that changed</h2><ul class="list">{cs}</ul></div><div><h2>Device spans the rack image did not have</h2><ul class="list">{cd}</ul></div></div></section>'
        + '<section class="card block"><h2>Why the rack image alone gives these answers, and how the frames cover them</h2>'
        + vs(["One stitched picture: where the stitching ghosts the rail holes or stretches a band, the ladder merges or misses units.",
              "The classifier sees blurred or doubled pixels and stays uncertain.", "One set of detector boxes, with nothing to check it against."],
             ["Real photos, sharp where they look; the same device appears in many of them.", "The whole photo pipeline runs on every chosen frame, and the frames vote unit by unit, seam by seam.",
              "What they cannot fix: a kind of device no model has learnt; those units stay free in every frame."])
        + "</section>")
    T["report"] += T.pop("compare")
    # How it works: a picture story from this run
    t = r["times_s"]
    used_sorted = sorted(used)
    pick = [used_sorted[round(k * (len(used_sorted) - 1) / 4)] for k in range(5)] if len(used_sorted) >= 5 else used_sorted
    ex = match[1] if match and match[0] > 0.3 else max(devices, key=lambda d: len(d.get("frames_shape") or []))
    cap = cv2.VideoCapture(str(video_path)); k_ = 0; want = set(pick) | set(used) | ({ex["best_frame"]} if ex.get("best_frame") is not None else set()); raw = {}
    while want:
        ok, fr = cap.read()
        if not ok: break
        if k_ in want:
            im = Image.fromarray(fr[:, :, ::-1]); im.thumbnail((420, 420)); im.save(pics / f"how_f{k_}.jpg", quality=84); want.discard(k_); raw[k_] = fr
        k_ += 1
    cap.release()
    # where each picked frame lands on the rack: its four corners through its homography
    # the fewest used frames whose areas together cover the rack from its top unit to its bottom unit
    y_top, y_bot = rows[0]["box"][1], rows[-1]["box"][3]; span = {f["frame"]: f["rack_y"] for f in r["frames"]}; qual = {f["frame"]: f["quality"] for f in r["frames"]}
    three, pos = [], y_top
    while pos < y_bot:
        cand = [f_ for f_, (a_, b_) in span.items() if a_ <= pos + 5 and b_ > pos and f_ not in three]
        if not cand: break
        reach = max(span[f_][1] for f_ in cand)
        best = max((f_ for f_ in cand if span[f_][1] >= reach - 120), key=lambda f_: qual[f_])     # nearly as far, but the clearest
        three.append(best); pos = span[best][1]
    seen_by = [sum(1 for a_, b_ in span.values() if a_ <= u["box"][1] and u["box"][3] <= b_) for u in rows]
    rack_bgr = cv2.imread(str(rack_path)); regs_ = RV.register([(f_, raw[f_]) for f_ in three if f_ in raw], rack_bgr)
    rt = Image.fromarray(rack_bgr[:, :, ::-1]).convert("RGB"); sc = 900 / rt.width; rt = rt.resize((900, int(rt.height * sc))); dr = ImageDraw.Draw(rt, "RGBA")
    hexrgb = lambda h_: tuple(int(h_[i:i + 2], 16) for i in (1, 3, 5))
    for k2, rg in enumerate(regs_):
        if rg["H"] is None: continue
        h_, w_ = raw[rg["index"]].shape[:2]
        q = cv2.perspectiveTransform(np.float32([[[0, 0], [w_, 0], [w_, h_], [0, h_]]]), rg["H"])[0] * sc
        col = hexrgb(PAL[k2 % len(PAL)]); dr.polygon([tuple(v) for v in q], outline=col + (255,), width=8, fill=col + (34,))
        own_top = max(min(q[:, 1]), (span[regs_[k2 - 1]["index"]][1] * sc) if k2 else 0)        # the label sits in the part only this frame adds
        x_ = max(10, min(q[:, 0]) + 16); y_ = max(10, own_top + 14); dr.rounded_rectangle([x_, y_, x_ + 210, y_ + 54], 10, fill=col + (240,)); dr.text((x_ + 16, y_ + 10), f"frame {rg['index']}", fill="white", font=occ_font(32))
    rt.save(pics / "how_place.jpg", quality=86)
    mp = mapping_pictures(raw[ex["best_frame"]], rack_bgr, devices, pics, ex["best_frame"]) if ex.get("best_frame") in raw else None
    def mrow(no, title, left, right, mid, text):
        return (f'<div class="mrow"><div class="mhead"><span class="no">{no}</span><b>{title}</b></div><div class="mpics"><figure><figcaption>Frame {mp["frame"]}</figcaption><img src="pics/{left}" alt="frame" loading="lazy"></figure>'
                f'<div class="mmid"><span>{mid}</span><em>→</em></div><figure><figcaption>Rack image</figcaption><img src="pics/{right}" alt="rack image" loading="lazy"></figure></div><p>{text}</p></div>')
    mapping = "" if mp is None else (
        '<section class="card block"><h2>How the frames and the rack are mapped, on one frame</h2>'
        + f'<div class="mrow"><div class="mhead"><span class="no">1</span><b>Match the same details in both pictures</b></div><img class="mwide" src="pics/map_match.jpg" alt="matched details" loading="lazy">'
        + f'<p>The same small details, screws, labels, port edges, are found in the frame (left) and in the rack image (right); each line joins one detail to its twin. {mp["inliers"]} of {mp["candidates"]} matches agree on one mapping, a homography, which turns any point of the frame into a point of the rack image.</p></div>'
        + mrow(2, "Carry the frame's unit lines onto the rack", "map_units_f.jpg", "map_units_r.jpg", "the same mapping",
               f"The frame finds its own {mp['units']} units, as on any photo. Each unit line is carried point by point onto the rack image, in the same colour. Every frame does this; the lines that land together on the rack become the final units.")
        + mrow(3, "Carry the frame's device boxes onto the rack", "map_dev_f.jpg", "map_dev_r.jpg", "four corners each",
               f"The detector finds {mp['boxes']} devices on the frame. Each box is carried by its four corners, so on the rack image it becomes a polygon, slightly tilted, never a stretched box. These polygons vote on occupancy and seams.")
        + mrow(4, "Carry the final devices back onto the frame", "map_back_r.jpg", "map_back_f.jpg", "the mapping in reverse",
               f"Once all frames have voted, the final devices exist on the rack image. The same mapping, reversed, draws them on the frame, which is how every device gets its best frames and its crop.").replace("<figcaption>Frame " + str(mp["frame"]) + "</figcaption><img src=\"pics/map_back_r.jpg\"", "<figcaption>Rack image</figcaption><img src=\"pics/map_back_r.jpg\"").replace("<figcaption>Rack image</figcaption><img src=\"pics/map_back_f.jpg\"", "<figcaption>Frame " + str(mp["frame"]) + "</figcaption><img src=\"pics/map_back_f.jpg\"")
        + "</section>")
    place = ('<div class="place"><div class="pfs three">' + "".join(f'<figure style="border-color:{PAL[k2 % len(PAL)]}"><img src="pics/how_f{rg["index"]}.jpg" alt="frame {rg["index"]}" loading="lazy"><figcaption style="background:{PAL[k2 % len(PAL)]}">frame {rg["index"]}</figcaption></figure>' for k2, rg in enumerate(regs_) if rg["H"] is not None)
             + '</div><div class="parrow">→</div><img class="prack" src="pics/how_place.jpg" alt="where each frame lands on the rack image" loading="lazy"></div>'
             + f'<p class="plain">These {len(three)} frames together already cover the whole rack, from {rows[0]["unit"].upper()} at the top to {rows[-1]["unit"].upper()} at the bottom. All {len(used)} frames used overlap much more: every unit is seen by at least {min(seen_by)} of them.</p>'
             + '<div class="cov"><span class="cvl">Frames that see each unit</span><div class="cvs">' + "".join(f'<div class="cv{" lo" if c_ < 3 else ""}" title="{u["unit"].upper()}: seen by {c_} frames"><b>{c_}</b><span>{u["unit"].upper()}</span></div>' for u, c_ in zip(rows, seen_by)) + "</div></div>")
    # the device followed through: the selected one if it matches, else the best-seen device
    rd = Image.open(res_dir / "rack_devices.jpg"); xs = [q[0] for q in ex["polygon"]]; ys = [q[1] for q in ex["polygon"]]
    pad = 0.3 * (max(ys) - min(ys))
    rd.crop((max(0, int(min(xs) - 40)), max(0, int(min(ys) - pad)), min(rd.width, int(max(xs) + 40)), min(rd.height, int(max(ys) + pad)))).save(pics / "how_result.jpg", quality=86)
    ex_units = [u for u in rows if u["unit"] in ex["units"]]
    ex_seams = [s_ for s_ in seams if s_["above"] in ex["units"] and s_["below"] in ex["units"]]
    def stage(no, title, pic, line_in, line_out, what, why_, example=""):
        return (f'<li class="stg{" big" if no == 3 else ""}"><span class="no">{no}</span><div class="sp">{pic}</div><div class="st"><b>{title}</b>'
                f'<p><span class="tag in">in</span>{line_in}</p><p><span class="tag out">out</span>{line_out}</p>'
                f'<div class="wh"><span>What happens</span><p>{what}</p></div><div class="wy"><span>Why it matters</span><p>{why_}</p></div>'
                + (f'<div class="ex"><span>In this rack</span><p>{example}</p></div>' if example else "") + "</div></li>")
    img = lambda src_, alt: f'<img src="{src_}" alt="{esc(alt)}" loading="lazy">'
    strip = '<div class="mini">' + "".join(img(f"pics/how_f{f_}.jpg", f"frame {f_}") for f_ in pick) + "</div>"
    fps_ = round(vf["fps"]) if vf["fps"] else 0
    ex_u = next((u for u in rows if u["status"] == "occupied" and u["rack_status"] == "uncertain" and u["frames_device"] == u["frames_voting"] and u["frames_voting"] >= 4), None)
    ex_s = next((s_ for s_ in seams if s_["frames_voting"] >= 4 and (s_.get("frames_who") or {}).get("model_new")), None)
    vote_ex = ""
    if ex_u:
        vote_ex += (f"Unit {ex_u['unit'].upper()}: the rack picture alone was unsure. {ex_u['frames_voting']} frames saw it, and the device detector found a device there in all "
                    f"{ex_u['frames_device']}, so it is occupied.")
    if ex_s:
        nn = len(ex_s["frames_who"]["model_new"]); vote_ex += (f" The boundary between {ex_s['above'].upper()} and {ex_s['below'].upper()}: {ex_s['frames_voting']} frames saw both units, "
                                                              f"{nn} of them saw two separate devices, so a new device starts there.")
    stages = [
        stage(1, "Two inputs", '<div class="mini two">' + img("pics/input_rack.jpg", "rack image") + img("pics/input_video.jpg", "video") + "</div>",
              f"the rack video ({vf['frames']} frames, {vf['seconds']:.1f} s) and the stitched rack picture ({rack_wh[0]}×{rack_wh[1]})",
              "one map of the whole rack, and many real photos of it",
              "The rack is filmed slowly from the top to the bottom. The frames of that video are also joined into one tall picture of the whole rack, the stitched picture.",
              "The stitched picture is the map: every answer is drawn on it. The video gives the sharp, close views that the stitched picture loses when frames are joined."),
        stage(2, "Read the frames", strip, f"the video, {fps_} pictures every second", f"{v['frames_read']} frames, each with a sharpness score",
              f"Frames that follow each other are almost the same picture, so we take one in every {v['stride']}. For each one we measure how sharp it is: a blurred frame has soft edges, a sharp one has crisp edges.",
              "Fewer frames keep the run fast, and the sharpness score tells us later which frames to trust."),
        stage(3, "Find where each frame sits on the rack", place,
              "one frame and the stitched picture",
              f"the exact part of the rack each frame shows, its coloured outline; {v['frames_mapped']} of {v['frames_read']} frames found their place",
              "In both pictures we pick out thousands of small, distinctive details: screw heads, label corners, port edges. Details that look the same are paired. "
              "Most pairs agree on one way of laying the frame over the rack picture; the few that disagree are mistakes and are thrown away.",
              "Once a frame is placed, any point in it, a unit line or a device corner, has a known place on the rack picture."),
        stage(4, "Choose the best frames", '<div class="mini">' + "".join(img(f"pics/how_f{f_}.jpg", f"frame {f_}") for f_ in pick[:3]) + "</div>",
              "every placed frame", f"{len(used)} frames, the clearest ones for every part of the rack",
              "We cut the rack into bands one unit tall. For each band we keep the three frames that show it best: sharp, well placed, and with the band near the middle of the picture rather than at its edge.",
              "Every unit is now seen by several clear photos, so one bad view can never decide on its own.",
              f"{len(used)} frames were chosen; every unit is seen by {min(seen_by)} to {max(seen_by)} of them."),
        stage(5, "Treat each frame like a normal photo", '<div class="mini">' + img(f"pics/how_f{pick[len(pick) // 2]}.jpg", "a frame") + '<div class="steps3"><span>unit detection</span><span>unit occupancy</span><span>device detection</span></div></div>',
              "one chosen frame", "its own units, which of them are full, and its device boxes",
              "Each chosen frame goes through the same three steps as a photo uploaded to our app: find the units, decide which are full or free, and find the devices. Nothing is changed for video.",
              "These steps are already tested on photos. A frame is simply a better photo of one part of the rack."),
        stage(6, "Put every answer on the same map", img("pics/rack_frame_boxes.jpg", "boxes on the rack"),
              "each frame's units and device boxes", f"{lad['votes']} unit lines and {r['frame_polygons']} device shapes, all on the stitched picture",
              "Using the placement from stage 3, every unit line and every device box found on a frame is moved onto the stitched picture. A box is moved by its four corners, so it keeps its true, slightly tilted shape.",
              "Now the opinions of all frames sit side by side on one map, where they can be compared unit by unit."),
        stage(7, "Let the frames vote", img("pics/rack_devices.jpg", "result"),
              "all the answers on the map", f"{lad['units']} units, {os_['occupied']} occupied, {len(devices)} devices, each with its reason",
              "For every unit, the frames that see it vote: if most of them find a device there, the unit is occupied. For every boundary between two full units, they vote again: one box over both means one device, two boxes mean two devices. "
              "When the frames disagree, the unit is left for a person to check, and it is never turned into a device on its own.",
              "Many clear opinions replace one blurred guess, and every final answer can be traced back to the frames that gave it.",
              vote_ex),
    ]
    follow = ('<div class="follow">'
              + '<div class="fcol"><h4>Its five best frames</h4><div class="mini five">' + "".join(img(f'pics/{tf_["file"]}', f'frame {tf_["frame"]}') for tf_ in ex.get("top_frames") or [] if tf_.get("file")) + "</div>"
              + '<p class="muted">' + ", ".join(f'frame {tf_["frame"]}' for tf_ in ex.get("top_frames") or []) + "</p></div>"
              + '<div class="farrow">→</div>'
              + '<div class="fcol"><h4>What each frame said</h4>'
              + "".join(f'<div class="frow"><span class="u">{esc(u["unit"].upper())}</span>{unit_chips(u)}</div>' for u in ex_units)
              + "".join(f'<div class="frow"><span class="u">{esc(s_["above"].upper())}/{esc(s_["below"].upper())}</span>{seam_chips(s_)}</div>' for s_ in ex_seams)
              + '<p class="muted">red: a detector box over the unit · purple: one box over both units</p></div>'
              + '<div class="farrow">→</div>'
              + f'<div class="fcol"><h4>The result</h4>{img("pics/how_result.jpg", "the device on the rack")}<p><b>{esc(ex["label"])}</b> over {esc(ex["top"].upper())}{" - " + esc(ex["bottom"].upper()) if ex["height"] > 1 else ""}, '
              + (f'every unit occupied by {ex_units[0]["frames_device"]} of {ex_units[0]["frames_voting"]} frames' if ex_units else "") + (f', held together by {ex_seams[0]["frames_voting"]} frames' if ex_seams else "") + "</p></div></div>")
    # the four rules, each with a real example from this rack
    def rrow(no, q, rule_txt, example_html, answer):
        return (f'<div class="rrow"><div class="rq"><span class="no">{no}</span><div><b>{q}</b><p><span class="k2">The rule</span>{rule_txt}</p></div></div>'
                f'<div class="rex"><span class="k2">Example from this rack</span>{example_html}</div><div class="rans"><span class="k2">The answer</span>{answer}</div></div>')
    # 1 · unit lines: a labelled drawing
    L1 = [26, 58, 90, 122]
    cols = "".join(f'<text x="{40 + 90 * c}" y="14" text-anchor="middle" class="h">frame {c + 1}</text>' + "".join(f'<line x1="{10 + 90 * c}" y1="{y}" x2="{70 + 90 * c}" y2="{y}" class="ok"/>' for y in L1) for c in range(3))
    stray = '<line x1="100" y1="74" x2="160" y2="74" class="bad"/><text x="130" y="152" text-anchor="middle" class="bad">only frame 2 drew this line</text>'
    final = '<text x="345" y="14" text-anchor="middle" class="h">final</text>' + "".join(f'<line x1="310" y1="{y}" x2="380" y2="{y}" class="fin"/>' for y in L1) + '<text x="290" y="78" class="ar">→</text>'
    svg1 = f'<svg viewBox="0 0 400 160" class="rsvg" role="img" aria-label="unit lines voted by three frames">{cols}{stray}{final}</svg>'
    ex1 = svg1 + f'<p>The four lines drawn by all three frames are kept. The line only frame 2 drew is dropped.</p>'
    ans1 = f'<p><b>{lad["units"]} units</b>, from {lad["votes"]} lines drawn by {lad["frames"]} frames.</p>'
    # 2 · full or free: one occupied and one free unit, with their frames
    u_occ = next((u for u in rows if u["status"] == "occupied" and u["frames_device"] == u["frames_voting"] and u["frames_voting"] >= 4), None)
    u_free = next((u for u in rows if u["status"] == "unoccupied" and u["frames_voting"] >= 4 and not u["frames_device"]), None)
    ex2 = ""
    for u, verdict in ((u_occ, "occupied"), (u_free, "free")):
        if u:
            ex2 += (f'<div class="rline"><span class="u">{esc(u["unit"].upper())}</span>{unit_chips(u)}<span class="vd {"occ" if verdict == "occupied" else "free"}">'
                    f'{u["frames_device"]} of {u["frames_voting"]} saw a device → {verdict}</span></div>')
    ex2 += '<p class="muted">Each box is one frame. Red: that frame saw a device on the unit. Green: it saw an empty slot. Grey: it saw neither.</p>'
    ans2 = f'<p><b>{os_["occupied"]} occupied, {os_["free"]} free</b>, and {os_["uncertain"]} left for a person where the frames disagree.</p>'
    # 3 · one device or two: a boundary voted "new" and one voted "same", with the picture of the joined device
    s_new = next((x for x in seams if x["new_device"] and (x.get("frames_who") or {}).get("model_new")), None)
    s_same = next((x for x in seams if not x["new_device"] and (x.get("frames_who") or {}).get("model_same")), None)
    ex3 = ""
    for x, verdict in ((s_new, "new"), (s_same, "same")):
        if x:
            who = x["frames_who"]; k_ = len(who.get("model_new" if verdict == "new" else "model_same", []))
            ex3 += (f'<div class="rline"><span class="u">{esc(x["above"].upper())}/{esc(x["below"].upper())}</span>{seam_chips(x)}<span class="vd {"newv" if verdict == "new" else "samev"}">'
                    f'{k_} of {x["frames_voting"]} saw {"two devices → a new device starts" if verdict == "new" else "one device → the same device"}</span></div>')
    d_join = next((d for d in devices if s_same and s_same["above"] in d["units"] and s_same["below"] in d["units"]), None)
    if d_join:
        ex3 += f'<figure class="rfig"><img src="pics/section_D{d_join["index"]}.jpg" alt="D{d_join["index"]}" loading="lazy"><figcaption>D{d_join["index"]}: one box covers both units, so it is one device, {d_join["height"]} units tall.</figcaption></figure>'
    ex3 += '<p class="muted">Blue: the frame saw two separate devices. Purple: it saw one device over both units.</p>'
    ans3 = f'<p><b>{len(devices)} devices</b>. A unit nobody is sure about never becomes a device on its own.</p>'
    # 4 · best frame: the five frames of one device, ranked
    tf = (ex.get("top_frames") or [])[:5]
    fs_ = ex.get("frame_search") or {}
    ex4 = '<div class="rbest">' + "".join(f'<figure><img src="pics/{t["file"]}" alt="frame {t["frame"]}" loading="lazy"><figcaption><b>{["1st", "2nd", "3rd", "4th", "5th"][i]}</b> · frame {t["frame"]} · sharpness {t["score"]:.2f}</figcaption></figure>' for i, t in enumerate(tf) if t.get("file")) + "</div>"
    ex4 += (f'<p>Device D{ex["index"]}: {fs_.get("eligible", "-")} of the video\'s {fs_.get("scanned", "-")} frames show the whole device; they are ranked from the sharpest down. A frame that cuts the device off is never used.</p>')
    ans4 = '<p><b>Five best frames for every device</b>; the 1st is its picture in this report.</p>'
    rules_html = ('<div class="rrows">'
                  + rrow(1, "Units: which unit lines are real?", "Every frame draws its own unit lines. A line is kept if most of the frames that see that spot drew it.", ex1, ans1)
                  + rrow(2, "Occupancy: is a unit full or free?", "The frames that see a unit vote. If most of them find a device on it, it is occupied. If most find nothing, it is free.", ex2, ans2)
                  + rrow(3, "Devices: one device or two?", "Look at the line between two full units. If the frames see one box covering both, it is one device. If they see two boxes, a new device starts.", ex3, ans3)
                  + rrow(4, "Pictures: which frame shows a device best?", "Check every frame of the video. Use only frames that show the whole device, and rank them by how sharp the device itself is.", ex4, ans4)
                  + "</div>")
    tt = [("rack image alone", t.get("rack", 0)), ("reading and placing the frames", t.get("register", 0)), (f"{len(used)} frames as photos", t.get("frames", 0)), ("voting and drawing", t.get("fuse", 0) + t.get("draw", 0))]
    tot = sum(x for _, x in tt) or 1
    timebar = ('<div class="tbar">' + "".join(f'<i style="width:{100 * x / tot:.1f}%" class="t{k}" title="{esc(nm)} {x:.1f} s">{f"{x:.0f} s" if x / tot > 0.06 else ""}</i>' for k, (nm, x) in enumerate(tt)) + "</div>"
               + '<div class="legend">' + "".join(f'<span><i class="sw t{k}"></i>{esc(nm)}</span>' for k, (nm, _) in enumerate(tt)) + f'<span><b>{tot:.0f} s in all</b> · voting and drawing take {tt[3][1]:.1f} s</span></div>')
    io3 = ('<div class="io3">'
           + '<div class="ioc"><h4>Goes in</h4><ul><li>the constructed rack image</li><li>the source video</li></ul></div>'
           + '<div class="ioc"><h4>Is used</h4><ul><li>the photo pipeline, unchanged, on every chosen frame</li><li>each frame\'s map onto the rack</li><li>a majority vote per unit and per seam</li></ul></div>'
           + '<div class="ioc"><h4>Comes out</h4><ul><li>one ladder of units</li><li>occupied / free / uncertain per unit</li><li>devices with their spans and shapes</li><li>five best frames per device, and a reason for every answer</li></ul></div></div>')
    # at a glance: the whole method in six boxes, each with what it gave on this rack
    glance = ('<ol class="flow">' + "".join(f'<li><b>{a_}</b><span>{b_}</span><span class="fnum">{c_}</span></li>' for a_, b_, c_ in [
        ("Film the rack", "top to bottom, slowly", f"{vf['frames']} frames"),
        ("Keep clear frames", f"one in every {v['stride']}, then the sharpest for each part", f"{v['frames_read']} read · {len(used)} used"),
        ("Place each frame", "find where it sits on the stitched picture", f"{v['frames_mapped']} of {v['frames_read']} placed"),
        ("Analyse each frame", "like a normal photo: units, full or free, devices", "3 steps per frame"),
        ("Let the frames vote", "unit by unit and boundary by boundary", f"{lad['votes']} lines, {r['frame_polygons']} shapes"),
        ("Result", "on the stitched picture, with a reason for each answer", f"{lad['units']} units · {len(devices)} devices")]) + "</ol>")
    # how we are doing it: the exact method and setting behind every stage, read from src/rack_video.py and the models in use
    mem_ = _eval_numbers()["members"]
    how_rows = [
        ("Read the frames", f"Take one frame in every {RV.STRIDE}; neighbouring frames are almost the same picture. Give each a sharpness score: how crisp its edges are.",
         "OpenCV · edge sharpness (variance of the Laplacian)", f"{v['frames_read']} frames read"),
        ("Place each frame on the rack", f"Find up to 8,000 small details (screws, label corners, port edges) in the frame and in the stitched picture and pair the look-alikes. "
         f"Keep a pair only when it is clearly better than the next best (ratio 0.75). RANSAC keeps the pairs that agree on one placement, the homography. A frame counts as placed with at least {RV.MIN_INLIERS} agreeing pairs.",
         "OpenCV SIFT · RANSAC homography", f"{v['frames_mapped']} of {v['frames_read']} placed"),
        ("Choose the best frames", f"Cut the rack into bands one unit tall. For each band keep the {RV.FRAMES_PER_BAND} frames that show it best: sharp, well placed, the band near the middle of the frame.",
         "frame quality = agreeing pairs × sharpness", f"{len(used)} frames, each unit seen by {min(seen_by)} to {max(seen_by)}"),
        ("Unit detection, on each frame", "The unit detector draws every unit line; a rule checks the first and last unit against the regular spacing of the rail holes.",
         "RackTrack unit detector (YOLO) + rail-hole end check", f"{lad['votes']} unit lines from all frames"),
        ("Unit occupancy, on each frame", f"{NUM[len(mem_)].capitalize()} classifiers each give a chance that the unit is full; their average decides. Under 65 % sure, the unit is left uncertain; a device box over a unit the classifiers call free also makes it uncertain.",
         ", ".join(x["name"] for x in mem_) + " (averaged)", "full / free / uncertain per frame"),
        ("Device detection, on each frame", "The device detector draws a box around each device. Where no box covers a unit, a rule reads the line between two units.",
         "DEIMv2 detector, one class: device + boundary rule", f"{r['frame_polygons']} device shapes"),
        ("Put every answer on the map", "The frame's placement moves each unit line onto the stitched picture point by point, and each device box by its four corners, so it stays a true, slightly tilted shape.",
         "the same homography", "all frames on one picture"),
        ("Vote: units", f"Lines from different frames that land within {int(RV.LINE_TOL * 100)} % of a unit of each other are one line. A gap wider than {RV.MAX_GAP:g} units gets lines added at the local spacing.",
         "majority of the frames that see that spot", f"{lad['units']} units"),
        ("Vote: full or free", f"A frame's device shape claims a unit when it covers more than half the unit's height. The unit is occupied when at least half of the frames that see it, and at least {RV.VOTE_MIN}, find a device there. A tie is left for a person.",
         "majority vote, weighted by frame quality", f"{os_['occupied']} occupied, {os_['free']} free, {os_['uncertain']} uncertain"),
        ("Vote: one device or two", "For the line between two full units: one box over both means the same device, two boxes mean a new device starts. A detector box counts fully, the rule counts 0.7.",
         "weighted vote per boundary", f"{len(devices)} devices"),
        ("Best frames per device", "Every frame of the video is matched to the rack around the device. A frame counts when the match is sound and the whole device is inside it. Score = the sharpness of the device's own area (edge strength over contrast). The five best, at least 10 frames apart, are kept.",
         "frame locator: SIFT + MAGSAC, Tenengrad sharpness", f"top {RV.TOP_FRAMES} per device"),
        ("Device classification", f"Each of the top {RV.CLS_FRAMES} frames is cut along the device's shape and classified; the #1 frame's answer is kept, a lower one only if every frame above it said Other.",
         "ConvNeXt-Tiny (ours, to be replaced)", f"{sum(1 for d in devices if (d.get('classification') or {}).get('label') not in (None, 'Other'))} of {len(devices)} named")]
    how_tbl = ('<div class="tw"><table class="howt"><thead><tr><th>Stage</th><th>How we do it</th><th>What we use</th><th>On this rack</th></tr></thead><tbody>'
               + "".join(f'<tr><td><b>{esc(a_)}</b></td><td>{esc(b_)}</td><td class="muted">{esc(c_)}</td><td class="n">{esc(d_)}</td></tr>' for a_, b_, c_, d_ in how_rows)
               + "</tbody></table></div>")
    T["how"] = ('<section class="card block hero"><p class="idea">We run our photo analysis on many sharp frames of the video, put every answer on the stitched rack picture, and let the frames vote.</p>'
                + '<p class="muted">The stitched picture alone is judged once. The frames are many close, sharp photos of the same rack, each part judged several times.</p>'
                + glance + "</section>"
                + '<section class="card block"><h2>How we are doing it</h2><p class="plain">Every stage, the exact method, and the tool or model behind it.</p>' + how_tbl + "</section>"
                + '<section class="card block"><h2>The same stages, in pictures</h2><ol class="stages">' + "".join(stages) + "</ol></section>"
                + mapping
                + f'<section class="card block"><h2>One device, followed through: {esc(ex["label"])}</h2>' + follow + "</section>"
                + '<section class="card block"><h2>The four rules the frames follow</h2>' + rules_html + "</section>"
                + '<section class="card block"><h2>Where the time goes</h2>' + timebar + "</section>"
                + '<section class="card block"><details><summary><b>Words used in this report</b></summary><dl class="gloss">' + "".join(f"<dt>{esc(a_)}</dt><dd>{esc(b_)}</dd>" for a_, b_ in [('Unit', 'one slot of the rack, 1U high; devices take one or more units'), ('Stitched picture', 'the tall picture of the whole rack, made by joining the frames of the video'), ('Frame', 'one picture of the video'), ('Occupied, free, uncertain', 'a unit with a device in it, an empty unit, or a unit left for a person to check'), ('Device', 'one piece of equipment, covering one or more units'), ('Boundary', 'the line between two full units: the same device continues, or a new one starts'), ('Best frame', 'the sharpest frame that shows a whole device; its picture is used in this report')]) + "</dl></details></section>")

    T["frames"] = T["frames"].replace("__PLACE__", place)
    T["models"] = integration_tab(r["ladder"], r["rack"], r["occupancy_frames"]["summary"], r["rack"]["occupancy"], r["devices"]).replace(
        "<h2>The full pipeline, step by step</h2>", "<h2>Models and accuracy, step by step</h2>")
    # ---------------------------------------------------------------- the flow: one page, the steps in order, detail folded away
    import datetime as _dt
    def rest_of(key):
        """A tab's content without its leading measure strip (the strip's numbers are on the step's front)."""
        t_ = T.get(key) or ""
        return t_[t_.find("<section"):] if t_.startswith('<div class="strip') and "<section" in t_ else t_
    def more(body, label="Read more: how it works, why, and the proof"):
        return f'<details class="more"><summary>{label}</summary><div class="moreb">{body}</div></details>' if body else ""
    def front_fig(src, cap, sub=""):
        return f'<figure class="ffig"><img src="{src}" alt="{esc(cap)}" loading="lazy"><figcaption><span>{cap}</span>{f"<span class=muted>{sub}</span>" if sub else ""}</figcaption></figure>'
    def step(no, key, title, result, body, tone="ok"):
        """One step of the flow: number, title, the one-line result, what came out (pictures, numbers), then Read more."""
        return (f'<section class="step card" id="{key}"><header class="shead"><span class="sno">{no}</span><div class="stt"><h2>{esc(title)}</h2>'
                f'<p class="sres {tone}">{result}</p></div></header>{body}</section>')
    warns = st.get("warnings") or []
    cap_ = r.get("capture") or {}
    kinds_ = {"switch": "switch", "router": "router", "firewall": "firewall", "gateway": "gateway", "load_balancer": "load balancer"}
    kind_of = lambda d: kinds_.get((d.get("layout") or {}).get("kind") or "switch", "device")
    _ident = RV.identity
    def ident(d):
        """RV.identity, with the make kept only when a catalogue model fitted (user, 2026-10-23); the raw read is in vendor_read."""
        I = dict(_ident(d)); I["vendor_read"] = I.get("vendor"); I["auto"] = AUTO(d)
        if not I.get("fitted"):
            I["model"] = None
            if not (I["auto"] and (PL(d).get("vendor"))):   # auto mode keeps the make only when the agent is sure of it (identify_confidence)
                I["vendor"] = None
        if I["auto"] and not I.get("layout"):
            I["layout"] = PL(d)
        return I
    cl_all = [d for d in devices if (d.get("classification") or {}).get("label")]
    steps = []
    # 1 input
    steps.append(step(1, "input", "Input: the video",
        f'A {vf["seconds"]:.1f} s video, {vf["w"]}×{vf["h"]} px at {vf["fps"]:.0f} frames per second, filmed top to bottom. Nothing else is given.',
        '<div class="front">' + front_fig("pics/input_video.jpg", "The first frame of the video", f'{vf["frames"]} frames')
        + ccards([("Length", f'{vf["seconds"]:.1f} s', None, f'{vf["frames"]} frames'), ("Frame size", f'{vf["w"]}×{vf["h"]}', None, "pixels"),
                  ("Bit rate", f'{cap_["mbit_s"]} Mbit/s' if cap_.get("mbit_s") else "-", None, "as filmed"),
                  ("Camera speed", f'{cap_["motion_px_per_frame"]} px' if cap_.get("motion_px_per_frame") else "-", None, "per frame, downwards")]) + "</div>"
        + more('<section class="card block"><h2>What the video must be</h2><p class="plain">One slow sweep from the top of the rack to the bottom, the phone upright, both rails in the picture, no stops and no going back up. '
               'The phone guide in Rack Sweep checks speed, direction, distance, steadiness and light while filming; a video that comes in any other way is checked by the stitcher instead (step 2).</p>'
               + (f'<p class="plain">The video was turned {esc(st["upright"]["turned"])} before anything else, because it was filmed sideways (picture motion {st["upright"]["motion"]}).</p>' if (st.get("upright") or {}).get("turned") else "")
               + "</section>")))
    # 2 quality
    q_ok = not warns
    q_res = ("Quality pass: the stitcher raised no warning about speed, overlap, blur, resolution or drift." if q_ok else
             f'Quality check: {len(warns)} warning{"s" if len(warns) != 1 else ""} from the stitcher. The analysis went on; read them before trusting the weak parts.')
    steps.append(step(2, "quality", "Quality: pass or not", q_res,
        '<div class="front">' + ccards([("Verdict", "Pass" if q_ok else "Check", None, "from the stitcher's capture checks"),
                                        ("Warnings", len(warns), None, "speed, overlap, blur, resolution, drift"),
                                        ("Sharp frames found", st.get("keyframes_used") or "-", None, f'of {st["frames_read"]} read' if st.get("frames_read") else ""),
                                        ("Alignment", f'{st["min_pair_inliers"]} points' if st.get("min_pair_inliers") else "-", None, "weakest pair of frames")])
        + (('<ul class="snotes">' + "".join(f'<li><b>{esc(WARN_NAME.get(w.get("code"), (w.get("name") or w.get("code") or "").replace("_", " ").capitalize()))}</b><span>{esc(w.get("text") or "")}</span></li>' for w in warns) + "</ul>") if warns else "")
        + "</div>"
        + more('<section class="card block"><h2>What is checked, and what happens on a fail</h2><ul class="list rules">'
               '<li><b>Speed</b>: the picture must move slowly enough that neighbouring sharp frames overlap; too fast and the rack cannot be joined.</li>'
               '<li><b>Overlap and drift</b>: every pair of joined frames must share enough matching points; a sideways drift bends the rack.</li>'
               '<li><b>Blur and resolution</b>: frames below the sharpness floor are skipped; a video too small to read unit holes is refused.</li>'
               '<li><b>Vertical</b>: the stitched rack is straightened; a tilt that remains is reported.</li></ul>'
               '<p class="plain">A video that cannot be stitched stops here with a plain message in the app ("Running" never turns into a wrong report). Warnings do not stop the run: they are listed so a weak result can be traced to the filming.</p>'
               + (f'<p class="plain">Capture as measured by the stitcher: {esc(json.dumps(st.get("capture")))}</p>' if st.get("capture") else "") + "</section>", "Read more: the five checks and the fail rule")
        , "ok" if q_ok else "warn"))
    # 3 stitched rack
    steps.append(step(3, "stitch", "Stitched rack picture",
        f'One straight picture of the whole rack, {rack_wh[0]}×{rack_wh[1]} px' + (f', joined from {st["keyframes_used"]} sharp frames in {st["seconds"]:.0f} s' if st.get("keyframes_used") else "") + '. Every later result is drawn on it.',
        '<div class="front two">' + front_fig("pics/input_rack.jpg", "The stitched rack picture", "the canvas for every step")
        + '<div>' + ccards([("Size", f'{rack_wh[0]}×{rack_wh[1]}', None, "pixels"), ("Frames joined", st.get("keyframes_used") or "-", None, "the sharpest, in order"),
                           ("Straightened", (st.get("vertical_status") or "-"), None, f'{st["horizontal_correction_deg"]:.1f}° corrected' if st.get("horizontal_correction_deg") is not None else ""),
                           ("Time", f'{st["seconds"]:.0f} s' if st.get("seconds") else "-", None, "stitching")]) + "</div></div>"
        + more(stitch_card(st) + '<section class="card block"><h2>How it is stitched</h2><p class="plain">The sharpest frames are picked along the video, aligned on the rack plane by their matching points, joined along seams that avoid cutting through devices, then straightened and cropped to the cabinet. The settings are fixed for every video, so two videos of the same rack give the same picture.</p></section>', "Read more: how the stitching works and what it measured")))
    # 4 frames
    steps.append(step(4, "frames", "Frames",
        f'{len(used)} clear frames were used to check the rack picture, and every device got its {RV.TOP_FRAMES} sharpest frames out of all {v["frames_total"]}.',
        '<div class="front">' + ccards([("Frames in the video", v["frames_total"], None, ""), ("Looked at", v["frames_read"], None, f'one in {v["stride"]}'),
                                        ("Placed on the rack", v["frames_mapped"], None, "matched to the rack picture"), ("Used for the checks", len(used), None, "the clearest")]) + "</div>"
        + more(rest_of("frames") + T.get("best", ""), "Read more: every frame, where it lands on the rack, and each device's five best")))
    # 5 unit detection
    steps.append(step(5, "units", "Unit detection",
        f'{lad["units"]} units found and numbered from the frames; the rack picture alone gave {rack["units"]}.' + (" The frames added what the stitched picture missed." if lad["units"] != rack["units"] else " Both agree."),
        '<div class="front">' + front_fig("pics/rack_units.jpg", "Every unit, numbered", "from the frames, on the rack picture") + "</div>"
        + more(rest_of("step1"), "Read more: how the units are found, and every difference between the frames and the rack picture")))
    # 6 unit occupancy
    steps.append(step(6, "occupancy", "Unit classification: full, free or uncertain",
        f'{os_["occupied"]} units occupied, {os_["free"]} free, {os_["uncertain"]} for a person to check.',
        '<div class="front">' + front_fig("pics/rack_occupancy_frames.jpg", "Every unit marked", "red occupied · green free · amber uncertain")
        + '<div class="bars">' + bars("Units", {"occupied": os_["occupied"], "unoccupied": os_["free"], "uncertain": os_["uncertain"]}) + "</div></div>"
        + more(rest_of("step2"), "Read more: the five classifiers, the vote, and every unit's reasons")))
    # 7 device detection
    steps.append(step(7, "devices", "Device detection",
        f'{len(devices)} devices, each with the units it spans, from the device boxes agreed across frames.',
        '<div class="front">' + front_fig("pics/rack_devices.jpg", "Every device and its span", "one colour per device") + "</div>"
        + more(rest_of("step3") + T.get("why", ""), "Read more: how a device's span is decided, and why each device is what it is")))
    # 8 device classification
    kc_ = lambda lbl, pr: f'<span class="kc{" oth" if lbl == "Other" else ""}"><b>{esc(lbl)}</b> {round(100 * pr)} %</span>'
    cl_rows = "".join(f'<tr><td class="n">{dot(d["index"])}D{d["index"]}</td><td>{esc(d["top"].upper())}{" - " + esc(d["bottom"].upper()) if d["height"] > 1 else ""} · {d["height"]}U</td>'
                      f'<td>{kc_(d["classification"]["label"], d["classification"]["prob"])}</td><td class="muted">{kfrom(d["classification"])}</td></tr>' for d in cl_all)
    named_ = sum(1 for d in cl_all if d["classification"]["label"] != "Other")
    steps.append(step(8, "classify", "Device classification",
        (f'{named_} of {len(cl_all)} devices named; {len(cl_all) - named_} called Other (not sure). Each answer comes from the device\'s sharpest frame'
         + (f'; {sum(1 for d in cl_all if d["classification"].get("source") == "close-up")} from a close-up photo, which is sharper than any frame' if any(d["classification"].get("source") == "close-up" for d in cl_all) else "") + "." if cl_all else "This run has no device classes."),
        ('<div class="front two"><div class="tw"><table><thead><tr><th>Device</th><th>Units</th><th>Kind</th><th>From</th></tr></thead><tbody>' + cl_rows + '</tbody></table></div>'
         + '<div class="k3 front-k">' + "".join(f'<figure class="kfig"><img src="pics/{esc(d["classification"].get("file") or "")}" alt="D{d["index"]}" loading="lazy"><figcaption><span class="kr">D{d["index"]}</span>{kc_(d["classification"]["label"], d["classification"]["prob"])}</figcaption></figure>' for d in cl_all) + "</div></div>" if cl_all else "")
        + more(rest_of("classify"), "Read more: the classifier, how the five frames vote, and every frame's answer")))
    # 9 OCR identification (the mapped kinds only)
    if pending:
        wait_ = ('<div class="front"><div class="lowres" style="background:var(--accent-soft)"><div class="lowtxt"><b>Running in the background.</b>'
                 '<p>The make and model reading, the agents, the overlay and the port fallback are going now on every switch, router, firewall and gateway. '
                 'This page refreshes itself when they are done; everything above is final already.</p></div></div></div>')
        for no, key, title in ((9, "ocr", "OCR identification: make and model"), (10, "agent", "Agent: the three port mappers"), (11, "overlay", "Overlay: the port layout on the device"),
                               (12, "fallback", "If the agent fails: the RT-EOS port model alone"), (13, "ports", "Port review: every port of every device")):
            steps.append(step(no, key, title, "Waiting for the background phase.", wait_, "muted"))
        sw_ = []
    LOW_PX = 160                                     # a 1U device under this height in its best frame: port numbers and labels cannot be read
    def crop_px(d):
        """The device's size in its best frame (the classification cut), in pixels."""
        f_ = (d.get("classification") or {}).get("file") or f'section_D{d["index"]}.jpg'
        try:
            return Image.open(res_dir / f_).size
        except Exception:
            return None
    def low_res(d):
        px = crop_px(d); return bool(px) and (px[1] < LOW_PX * max(1, d.get("height") or 1) or vf["h"] < 1080)
    def where_in_rack(d):
        """A small picture of the rack around this device, the device outlined: where to photograph."""
        W, H = rack_wh; ys = [q[1] for q in d.get("polygon") or []]; xs = [q[0] for q in d.get("polygon") or []]
        if not ys:
            return ""
        y0 = max(0, min(ys) - 260); y1 = min(H, max(ys) + 260)
        return (f'<svg class="lrack where" viewBox="0 {y0:.0f} {W} {y1 - y0:.0f}" role="img" aria-label="where D{d["index"]} is in the rack">'
                f'<image href="pics/input_rack.jpg" x="0" y="0" width="{W}" height="{H}"/>'
                f'<polygon points="{" ".join(f"{x:.0f},{y:.0f}" for x, y in d["polygon"])}" style="fill:rgba(29,78,216,.22);stroke:{PAL[(d["index"] - 1) % len(PAL)]};stroke-width:8"/>'
                f'<text x="{min(xs) + 12:.0f}" y="{min(ys) - 14:.0f}" style="font:600 34px sans-serif;fill:#fff;paint-order:stroke;stroke:rgba(0,0,0,.75);stroke-width:6px">D{d["index"]} · {esc(d["top"].upper())}</text></svg>')
    def src_read(d):
        L = d["layout"]; R = L.get("rack_read") or {}; I = ident(d); reads = L.get("reads") or []
        n_model = sum(1 for x in reads if x.get("model")); n_make = sum(1 for x in reads if x.get("vendor"))
        s1 = (esc(f'{R.get("vendor") or ""} {R.get("model") or ""}'.strip()) or "nothing read") if R else "not tried"
        s2 = (f'{n_model} of {len(reads)} frames read a model, {n_make} a make' if reads else "no frames")
        C = d.get("closeup") or {}; s3 = (esc(f'{C.get("vendor") or ""} {C.get("model") or ""}'.strip()) or "nothing read") if C else "none"
        got = (f'{esc(I["vendor"])} {esc(I["model"])}' if I["fitted"] else
               (f'<span class="muted">{esc(I["vendor_read"])} read, no model: not confirmed</span>' if I["vendor_read"] else '<span class="muted">nothing confirmed</span>'))
        by = {1: "rack crop", 2: "best frames", 3: "close-up"}.get(I["source"] or I["make_source"], "-")
        return f'<tr><td class="n">{dot(d["index"])}D{d["index"]}</td><td>{esc(d["top"].upper())} · {kind_of(d)}</td><td>{s1}</td><td>{s2}</td><td>{s3}</td><td><b>{got}</b></td><td class="muted">{by}</td></tr>'
    def ask_block(d):
        """Ask for a close-up of this device: why (too small, or the model was not read), where it is in the rack, the upload."""
        px = crop_px(d); lo = low_res(d)
        why = (f'<b>The video is too small to read this device.</b><p>It is {px[0]} × {px[1]} px in its sharpest frame' + (f' (the video is {vf["w"]}×{vf["h"]}; make, model and port numbers need 1080p or more)' if vf["h"] < 1080 else "") + ". "
               if lo and px else '<b>The make and model could not be read from the video.</b><p>')
        return (f'<div class="orow"><div class="ahead"><span class="lnum">{dot(d["index"])}D{d["index"]} · {esc(d["top"].upper())} · {kind_of(d)}</span>'
                + (f'<span class="pill unc">resolution too low</span>' if lo else '<span class="pill">model not read</span>') + "</div>"
                + f'<div class="lowres"><div class="lowtxt">{why}Photograph the device at <b>{esc(d["top"].upper())}</b>, the one outlined on the right, from the front, straight on, at 1080p or better, and upload it here: its make, model and ports are read from the photo.</p>'
                + lclose(d) + "</div>" + where_in_rack(d) + "</div></div>")
    need_ = [d for d in sw_ if not ident(d)["fitted"]]
    n_make = sum(1 for d in sw_ if ident(d)["vendor_read"]); n_model = sum(1 for d in sw_ if ident(d)["model"])
    unread_ = [d for d in devices if (d.get("classification") or {}).get("label") in RV.P.MAPPER_KIND and not d.get("layout")]
    _names = ", ".join("D%d" % d["index"] for d in unread_); _n = len(unread_)
    unread_note = (" %d device%s (%s) %s not read yet: select %s in the app and the reading starts." % (_n, "s" if _n != 1 else "", _names, "are" if _n != 1 else "is", "them" if _n != 1 else "it")) if unread_ else ""
    if not pending: steps.append(step(9, "ocr", "OCR identification: make and model",
        ((f'{len(sw_)} device{"s" if len(sw_) != 1 else ""} went to the port mappers ({", ".join(f"{sum(1 for d in sw_ if kind_of(d) == k)} {k}" for k in dict.fromkeys(kind_of(d) for d in sw_))}). '
         f'The panel text gave a make for {n_make} and an exact model for {n_model}; a make counts only once its model fitted.'
         + (f' {len(need_)} device{"s" if len(need_) != 1 else ""} still need{"" if len(need_) != 1 else "s"} a close-up photo: upload it below.' if need_ else "") if sw_ else
          ("No device read yet." if unread_ else "No switch, router, firewall or gateway on this rack, so nothing to read.")) + unread_note),
        ('<div class="front"><div class="tw"><table><thead><tr><th>Device</th><th>Unit · kind</th><th>1 · rack crop</th><th>2 · best frames</th><th>3 · close-up</th><th>Read</th><th>Decided by</th></tr></thead><tbody>'
         + "".join(src_read(d) for d in sw_) + '</tbody></table></div>' + "".join(ask_block(d) for d in need_) + '</div>' if sw_ else "")
        + more('<section class="card block"><h2>How the panel is read</h2><ol class="lflow">'
               '<li><b>Stitched rack crop</b><span>the device\'s crop of the rack picture is read first</span></li>'
               f'<li><b>Top {RV.LAYOUT_FRAMES} frames</b><span>then its {NUM.get(RV.LAYOUT_FRAMES, RV.LAYOUT_FRAMES)} sharpest frames; a model counts when {RV.MODEL_AGREE} frames agree</span></li>'
               '<li><b>Uploaded crop</b><span>then a close-up photo, if one is given</span></li>'
               '<li><b>Catalogue match</b><span>the text is matched against the make and model catalogues; a make alone, with no model, is not shown</span></li></ol>'
               '<p class="plain">OCR (RapidOCR) reads every printed word on the crop with an 8 s budget. The words are matched to the catalogue with a distance that forgives the usual confusions (0 and O, 5 and S, 1 and I). '
               'A make is kept only when a catalogue model fitted too, because a vendor name can also come from a sticker or a cable label. '
               'Frames that disagree on the model leave it unread rather than guessing.</p></section>', "Read more: the three sources, the catalogue match, and the agreement rule")))
    # 10 agent
    def agent_row(d):
        I = ident(d); lay = I["layout"] or {}; pics_ = lay.get("pictures") or {}
        if I["fitted"]:
            what, tone = "template fitted: the vendor front image matched the device", "ok"
        elif I["auto"]:
            what, tone = f'no template: auto mode boxed {lay.get("n_ports") or 0} ports from the photo', "warn"
        elif _ident(d)["model"]:
            what, tone = "model read, but no template fitted on this device", "warn"
        elif lay.get("mode") == "conflict":
            what, tone = "frames disagreed on the model, so no template was tried", "warn"
        else:
            what, tone = "no model read, so there is no template to fetch: the port model takes over (step 12)", "muted"
        img = lfig(pics_.get("vendor_ports"), "Vendor front image, ports numbered") or lfig(pics_.get("vendor"), "Vendor front image") or '<figure class="lfig none"><span>no front image</span></figure>'
        return f'<div class="arow"><div class="ahead"><span class="lnum">{dot(d["index"])}D{d["index"]} · {esc(d["top"].upper())}</span><b>{lname({"vendor": I["vendor"], "model": I["model"]})}</b><span class="sres {tone} small">{what}</span></div>{img}</div>'
    n_fit = sum(1 for d in sw_ if ident(d)["fitted"]); n_auto = sum(1 for d in sw_ if AUTO(d))
    agents_ = {"switch": "switch agent", "router": "router agent", "firewall": "appliance agent", "gateway": "appliance agent", "load balancer": "appliance agent"}
    sent_ = {}
    for d in sw_:
        sent_.setdefault(agents_.get(kind_of(d), "switch mapper"), []).append(f'D{d["index"]}')
    if not pending: steps.append(step(10, "agent", "Agent: the three port mappers fetch the vendor's front image and its port template",
        ((" · ".join(f'{", ".join(v_)} to the {k_}' for k_, v_ in sent_.items()) + f'. A template fitted on {n_fit} of {len(sw_)} devices' + (f', auto mode boxed the ports on {n_auto}' if n_auto else "") + '.') if sw_ else "Nothing to fetch."),
        ('<div class="front agrid">' + "".join(agent_row(d) for d in sw_) + "</div>" if sw_ else "")
        + more(rest_of("layout") + '<section class="card block"><h2>What the agents do</h2><p class="plain">Three agents, one per kind: the switch agent for switches, the router agent for routers, the appliance agent for firewalls, gateways and load balancers. Each runs in its own process with its own models, every failure caught and a time limit per crop. For a model read in step 9 the agent looks for that model\'s official front image: first the saved templates, then the vendor\'s site and an image search. '
               'The front image becomes a template: every port, its type and its number. The template is fitted onto the device by matching points and a homography, and the fit counts only when 80 % of its ports land on ports the port detector also found. '
               'When no template fits, the agent\'s auto mode boxes the ports it finds in the photo and numbers them left to right (status mapped_auto); a photo of the back is asked for when the ports are on the back (needs_back).</p></section>', "Read more: every device's sources, frames and template, and how the agent works")))
    # 11 overlay
    def ov_row(d):
        I = ident(d); lay = I["layout"] or {}; pics_ = lay.get("pictures") or {}
        src_ = {1: "the rack crop", 2: f'frame {lay.get("frame")}', 3: "the close-up"}.get(I["source"], "the device")
        return (f'<div class="orow"><div class="ahead"><span class="lnum">{dot(d["index"])}D{d["index"]} · {esc(d["top"].upper())}</span><b>{lname({"vendor": I["vendor"], "model": I["model"]})}</b>'
                f'<span class="muted">{lay.get("n_ports") or 0} ports · {lay.get("connected") or 0} with a cable</span></div><div class="lres">'
                + lfig(pics_.get("trace") or pics_.get("overlay"), (f"Auto mode: the ports boxed on {src_}, numbered left to right" if I["auto"] else f"The vendor layout laid over {src_}")) + lfig(pics_.get("rack_trace"), "The same ports on the rack picture") + "</div></div>")
    if not pending: steps.append(step(11, "overlay", "Overlay: the port layout on the device",
        ((f'{n_fit} device{"s" if n_fit != 1 else ""} carry a vendor port layout' + (f' and {n_auto} the agent\'s auto-mode ports' if n_auto else "") + f': {n_ports} ports, {n_cab} with a cable. The ports are projected from the frame onto the rack picture.') if pok_ else "No port layout, so there is no overlay. Step 12 reads the ports from the photo instead."),
        (('<div class="front">' + (front_fig("pics/rack_ports.jpg", "Every mapped port on the rack picture", "filled: a cable is plugged in") if ok_ else "")
          + "".join(ov_row(d) for d in pok_) + "</div>") if pok_ else "")
        + more('<section class="card block"><h2>How the overlay is made</h2><p class="plain">The template\'s ports are drawn where the homography puts them on the device, then snapped onto the jacks the port detector found, so the numbers sit on the real ports. '
               'The frame\'s ports are carried onto the rack picture through the frame\'s own registration, so the rack view and the frame view show the same layout.</p></section>', "Read more: how the ports are placed and carried onto the rack picture")))
    # 12 fallback: the RT-EOS port model
    fb_ = [d for d in sw_ if not USED(PL(d))]
    def fb_row(d):
        P_ = PDL(d); c = P_.get("counts") or {}; pic = P_.get("picture"); lo = low_res(d)
        head = (f'<div class="ahead"><span class="lnum">{dot(d["index"])}D{d["index"]} · {esc(d["top"].upper())} · {kind_of(d)}</span>'
                + (f'<span class="pill">{P_.get("n", len(P_.get("ports") or []))} ports</span><span class="pill occ">{c.get("connected", 0)} connected</span><span class="pill free">{c.get("empty", 0)} empty</span><span class="pill unc">{c.get("unknown", 0)} not sure</span>' if P_.get("ports") else '<span class="muted">no port found on this face</span>')
                + (f'<span class="pill unc">resolution too low: positions only</span>' if lo else "") + "</div>")
        return f'<div class="orow">{head}' + (lfig(pic, "Ports found by the RT-EOS port model, numbered in the order found") if pic else "") + "</div>"
    n_low = sum(1 for d in fb_ if low_res(d))
    if not pending: steps.append(step(12, "fallback", "If the agent fails: the RT-EOS port model alone",
        ((f'{len(fb_)} device{"s" if len(fb_) != 1 else ""} got no port layout from the agents, template or auto mode. For them the RT-EOS port model finds the ports in the photo on its own.'
          + (f' {n_low} of them {"are" if n_low != 1 else "is"} too small in the video for the port model to read properly: the ports are positions only until a close-up is uploaded in step 9.' if n_low else "")) if fb_ else "Every mapped device got a vendor layout, so the fallback was not needed."),
        ('<div class="front">' + "".join(fb_row(d) for d in fb_) + "</div>" if fb_ else "")
        + more('<section class="card block"><h2>The rule</h2><p class="plain">The agents answer in one of four ways: a template fitted (mapped_template), the ports boxed from the photo (mapped_auto), the ports are on the back (needs_back), or nothing mapped. '
               'Only in the last case does this fallback run: RT-EOS\'s own models alone, the port finder for the positions, the status model for empty or connected, the cable model for the cable\'s type and colour, the dust-cap model for empty ports. '
               'Without a model the ports have no specification and their numbers are the order found.</p></section>', "Read more: when the fallback runs, and what RT-EOS reads")))
    # 13 port review
    if not pending: steps.append(step(13, "ports", "Port review: every port of every device",
        (f'{len(allp)} ports on {len(sw_)} devices: {sum(p.get("status") == "connected" for p in allp)} connected, {sum(p.get("status") == "empty" for p in allp)} empty, {sum(p.get("status") == "unknown" for p in allp)} not sure.' if allp else "No ports to review on this rack."),
        '<div class="front">' + rest_of("ports") + "</div>" if sw_ else ""))
    # 14 rack report
    tm = r.get("times_s") or {}
    tnames = [("rack", "Stitched rack picture (step 3)"), ("register", "Frames placed on the rack (step 4)"), ("frames", "Units, occupancy and devices on the frames (steps 5 to 7)"),
              ("best_frames", "Five best frames per device (step 4)"), ("classify", "Device classification (step 8)"), ("layout", "OCR, agents, overlay and port fallback (steps 9 to 13)"), ("draw", "Pictures")]
    def tfmt(x):
        return "-" if x is None else (f"{x:.0f} s" if x < 90 else f"{x / 60:.1f} min")
    ttot = sum(v for k, v in tm.items() if k in dict(tnames) and k != "draw" and isinstance(v, (int, float)))
    time_tab = ('<div class="tw"><table><thead><tr><th>Step</th><th>Time</th><th>Share</th></tr></thead><tbody>'
                + "".join(f'<tr><td>{esc(lbl)}</td><td class="n">{tfmt(tm.get(k))}</td><td class="n">{(100 * tm[k] / ttot):.0f} %</td></tr>' for k, lbl in tnames if isinstance(tm.get(k), (int, float)) and k != "draw")
                + f'<tr><td><b>Whole run</b></td><td class="n"><b>{tfmt(ttot)}</b></td><td class="n">100 %</td></tr></tbody></table></div>') if tm else ""
    steps.append(step(14, "report", "Rack report", "The rack as the app reports it: every unit, its state and its device, in one elevation; and how long each step took.",
        '<div class="front">' + rest_of("report") + (f'<section class="card block"><h2>Time per step</h2>{time_tab}<p class="inote">Measured on this Mac on 2 threads. Steps 9 to 13 take most of the time: every switch, router, firewall or gateway is read on its rack crop and its five best frames.</p></section>' if time_tab else "") + "</div>"))
    # reference
    steps.append(step(15, "models", "Models and accuracy", "What runs at each step, which approach was chosen and why, and how accurate it is on our test sets.",
        more(T["models"], "Read more: every step's model, approach and accuracy")))
    rail = '<nav class="rail" aria-label="steps">' + "".join(
        f'<a href="#{k}"><span>{i}</span>{esc(t)}</a>' for i, (k, t) in enumerate(
            [("input", "Input"), ("quality", "Quality"), ("stitch", "Stitched rack"), ("frames", "Frames"), ("units", "Units"), ("occupancy", "Occupancy"), ("devices", "Devices"),
             ("classify", "Classes"), ("ocr", "OCR"), ("agent", "Agent"), ("overlay", "Overlay"), ("fallback", "Fallback"), ("ports", "Ports"), ("report", "Rack report"), ("models", "Models")], 1)) + "</nav>"
    info = " · ".join(x for x in ((f"Rack {rack_id}" if rack_id else None), f"Video {vf['seconds']:.1f} s, {vf['frames']} frames", f"{len(used)} frames used",
                                   f"Generated {_dt.date.today().strftime('%d %B %Y').lstrip('0')}") if x)
    page = (f'<!doctype html><meta charset="utf-8"><title>Rack Video Analysis</title>' + CSS + FLOW_CSS
            + f'<div class="page"><div class="stick"><header><div class="brand">RackTrack</div><h1>Rack Video Analysis</h1><p class="info">{esc(info)}</p></header>{rail}</div>'
            + '<div class="steps">' + "".join(steps) + "</div></div>"
            + '<div id="lb" hidden><img id="lbimg" alt=""><span>click anywhere or press Esc to close</span></div>' + JS_FLOW.replace("__KEY__", f"video-report-{name}")
            + ('<script>(function(){var m=location.pathname.match(/\\/video-report\\/([^\\/]+)\\//); if(!m) return; var id=decodeURIComponent(m[1]);'
               'setInterval(function(){fetch("/api/video/"+id,{cache:"no-store",credentials:"same-origin"}).then(function(r){return r.json();}).then(function(j){if(j.status==="done"||j.status==="failed") location.reload();}).catch(function(){});},8000);})();</script>' if pending else ""))
    (out / "index.html").write_text(page.replace("\u2013", "-").replace("\u2014", "-"))      # plain hyphens only
    for old_ in ("layout.html",):                                        # the separate switch page is part of the flow now
        (out / old_).unlink(missing_ok=True)
    return out


CSS = """<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* layout: one 90 % column of white cards on a cool pale grey; the step pictures in pairs, frames next to the rack image alone */
:root { --bg:#f3f5f8; --panel:#fff; --surface:#f7f8fa; --ink:#14171c; --muted:#525a67; --line:#e4e7ec; --accent:#1d5fcf; --accent-soft:#eaf1fc;
  --occ:#b4372a; --occ-soft:#fbe7e3; --free:#1a7f45; --free-soft:#e7f4ec; --unc:#b25e0a; --unc-soft:#fdf1e2; --same:#8c3cb4; --same-soft:#f3e9f8; --shadow:0 1px 2px rgba(16,24,40,.05), 0 4px 14px rgba(16,24,40,.06);
  --sans:"IBM Plex Sans","Segoe UI",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0f1114; --panel:#181b20; --surface:#1e2228; --ink:#e8eaee; --muted:#9aa3af; --line:#2a2f37; --accent:#6ea0ff; --accent-soft:#1b2a45;
  --occ:#f09a86; --occ-soft:#40221d; --free:#58c986; --free-soft:#17301f; --unc:#e9a558; --unc-soft:#3a2a14; --same:#c99be6; --same-soft:#2e2238; --shadow:0 1px 2px rgba(0,0,0,.4), 0 4px 14px rgba(0,0,0,.25); color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#0f1114; --panel:#181b20; --surface:#1e2228; --ink:#e8eaee; --muted:#9aa3af; --line:#2a2f37; --accent:#6ea0ff; --accent-soft:#1b2a45;
  --occ:#f09a86; --occ-soft:#40221d; --free:#58c986; --free-soft:#17301f; --unc:#e9a558; --unc-soft:#3a2a14; --same:#c99be6; --same-soft:#2e2238; --shadow:0 1px 2px rgba(0,0,0,.4), 0 4px 14px rgba(0,0,0,.25); color-scheme:dark; }
* { box-sizing:border-box; } [hidden] { display:none !important; }
body { margin:0; background:var(--bg); color:var(--ink); font:400 15px/1.55 var(--sans); padding-inline:16px; padding-block:0 60px; -webkit-font-smoothing:antialiased; }
.page { width:90%; margin:0 auto; } @media (max-width:700px) { .page { width:100%; } } img { max-width:100%; }
header { padding:24px 0 4px; } h1 { margin:2px 0 0; font-size:1.7rem; letter-spacing:-.015em; text-wrap:balance; } header p { margin:4px 0 0; color:var(--muted); max-width:none; }
.brand { font:600 .72rem/1 var(--mono); letter-spacing:.14em; text-transform:uppercase; color:var(--accent); } .info { font-size:.9rem; }
h2 { margin:0; font-size:1.1rem; } h4 { margin:10px 0 2px; font-size:.85rem; color:var(--muted); font-weight:600; }
.tabs { display:flex; gap:4px; margin:10px 0 18px; border-bottom:1px solid var(--line); overflow-x:auto; scrollbar-width:none; } .tabs::-webkit-scrollbar { display:none; }
.tabs button { font:500 .93rem var(--sans); padding:11px 14px 10px; border:0; border-bottom:3px solid transparent; background:transparent; color:var(--muted); cursor:pointer; white-space:nowrap; margin-bottom:-1px; }
.tabs button:hover { color:var(--ink); } .tabs button[aria-selected="true"] { color:var(--accent); border-bottom-color:var(--accent); font-weight:600; } .tabs button:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); }
.block { padding:18px 20px; margin-bottom:16px; display:flex; flex-direction:column; gap:14px; min-width:0; }
.strip { margin:0 0 16px; display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; } .strip .card { padding:14px 18px; } .strip b { display:block; font:600 1.35rem/1.15 var(--sans); font-variant-numeric:tabular-nums; } .strip span { color:var(--muted); font-size:.84rem; }
.pair { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; } @media (max-width:1000px) { .pair { grid-template-columns:1fr; } }
.flowpics { display:grid; grid-template-columns:minmax(0,1fr) auto minmax(0,1fr); gap:14px; align-items:center; } .flowpics figure img { max-height:380px; object-fit:contain; }
.farrow { font-size:1.6rem; color:var(--muted); } @media (max-width:800px) { .flowpics { grid-template-columns:1fr; } .farrow { transform:rotate(90deg); justify-self:center; } }
.find { font-weight:600; margin:4px 0 10px; } .snotes { list-style:none; margin:0 0 6px; padding:0; display:flex; flex-direction:column; gap:8px; }
.snotes li { display:flex; flex-direction:column; gap:2px; padding:10px 12px; border-radius:10px; background:var(--surface); border-left:3px solid var(--unc); } .snotes li span { color:var(--muted); font-size:.9rem; }
figure { margin:0; display:flex; flex-direction:column; gap:8px; min-width:0; } figcaption { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; font-size:.9rem; } figcaption span { color:var(--muted); }
figure img { width:100%; max-height:460px; object-fit:contain; background:var(--surface); border-radius:8px; display:block; }
img { cursor:zoom-in; } .ladders { max-height:640px; }
#lb { position:fixed; inset:0; background:rgba(10,12,16,.92); display:flex; align-items:center; justify-content:center; z-index:50; cursor:zoom-out; padding:24px; } #lb img { max-width:96vw; max-height:92vh; width:auto; height:auto; object-fit:contain; background:transparent; cursor:zoom-out; }
#lb span { position:fixed; top:14px; right:20px; color:#fff; font:600 .9rem var(--sans); opacity:.8; }
.why { margin:0; padding:10px 14px; border-left:3px solid var(--accent); background:var(--surface); border-radius:0 8px 8px 0; font-size:.94rem; line-height:1.55; max-width:none; } .why b { font-weight:600; }
.tw { border:1px solid var(--line); border-radius:8px; } @media (max-width:800px) { .tw { overflow-x:auto; } } table { width:100%; border-collapse:collapse; font:400 .86rem/1.45 var(--sans); } th, td { text-align:left; padding:7px 9px; border-top:1px solid var(--line); vertical-align:top; }
thead th { border-top:0; color:var(--muted); font:500 .72rem/1 var(--mono); letter-spacing:.08em; text-transform:uppercase; background:var(--panel); } td.n { font-variant-numeric:tabular-nums; white-space:nowrap; }
.muted { color:var(--muted); } .chg { color:var(--unc); font-weight:600; } .why-cell { color:var(--muted); display:block; min-width:260px; }
.pill { display:inline-block; border-radius:999px; padding:3px 9px; font-size:.8rem; font-weight:600; background:var(--surface); color:var(--ink); white-space:nowrap; } .pill.occ { background:var(--occ-soft); color:var(--occ); } .pill.free { background:var(--free-soft); color:var(--free); } .pill.unc { background:var(--unc-soft); color:var(--unc); }
.dot { display:inline-block; width:10px; height:10px; border-radius:999px; margin-right:6px; vertical-align:middle; }
.bars { display:flex; flex-direction:column; gap:8px; } .bar { display:grid; grid-template-columns:150px 1fr 70px; align-items:center; gap:12px; font-size:.86rem; } .bar div { display:flex; height:26px; border-radius:6px; overflow:hidden; background:var(--surface); }
.bar i { display:flex; align-items:center; justify-content:center; font:600 .78rem var(--sans); color:#fff; font-style:normal; } .bar i.occ { background:var(--occ); } .bar i.free { background:var(--free); } .bar i.unc { background:var(--unc); } .bar em { color:var(--muted); font-style:normal; text-align:right; }
@media (max-width:700px) { .bar { grid-template-columns:1fr; } }
.thumbs { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:10px; } .thumbs img { width:100%; height:170px; object-fit:contain; } .thumbs figcaption { font-size:.8rem; color:var(--muted); display:block; }
.thumbs.film { grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); } .thumbs.crops { grid-template-columns:repeat(5,minmax(0,1fr)); } .thumbs.crops img { height:140px; } @media (max-width:1100px) { .thumbs.crops { grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); } }
.map { width:100%; height:auto; display:block; } .map text { font:12px var(--sans); fill:var(--muted); } .meta { color:var(--muted); font-size:.86rem; }
.devframes { display:flex; flex-direction:column; gap:8px; padding-top:10px; border-top:1px solid var(--line); } .devframes header { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:0; } .devframes header b { font-weight:600; } .devframes header span { color:var(--muted); font-size:.86rem; }
.devs { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:14px; } .dev { border:1px solid var(--line); border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:8px; min-width:0; background:var(--surface); }
.dev header { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:0; } .dev header b { font-weight:600; } .dev header span { color:var(--muted); font-size:.86rem; } .dev img { width:100%; max-height:150px; object-fit:contain; background:var(--panel); border-radius:6px; }
.dev ul { margin:0; padding-left:18px; font-size:.88rem; } .dev ul.units li { margin:6px 0; } .dev .type { font-size:.9rem; } .dev .msg { margin:0; font-size:.88rem; }
.two-msg { display:grid; gap:4px; margin-top:4px; font-size:.84rem; color:var(--muted); } .two-msg b { color:var(--ink); font-weight:600; margin-right:4px; }
.cols { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; } @media (max-width:1000px) { .cols { grid-template-columns:1fr; } } .list { margin:8px 0 0; padding-left:18px; font-size:.9rem; } .list li { margin:4px 0; } .rules li { margin:8px 0; max-width:95ch; }
.flow { list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:10px; counter-reset:f; } .flow li { position:relative; border:1px solid var(--line); border-radius:10px; padding:12px 14px 12px 14px; background:var(--surface); display:flex; flex-direction:column; gap:4px; min-width:0; }
.flow li::before { counter-increment:f; content:counter(f); position:absolute; top:-10px; left:12px; background:var(--accent); color:#fff; font:600 .72rem/1 var(--mono); padding:4px 7px; border-radius:999px; } .flow b { font-weight:600; font-size:.95rem; } .flow span { color:var(--muted); font-size:.85rem; }
.stick { position:sticky; top:env(safe-area-inset-top,0px); z-index:10; background:var(--bg); } .stick header { padding:18px 0 4px; } .stick .tabs { margin:8px 0 16px; }
.vs { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; } @media (max-width:800px) { .vs { grid-template-columns:1fr; } } .vs .m { font-size:.92rem; }
.bf { display:inline-flex; align-items:center; gap:8px; } .bf img { height:40px; width:auto; max-width:160px; border-radius:4px; object-fit:contain; background:var(--surface); } .bf b { font:600 .78rem var(--mono); color:var(--muted); white-space:nowrap; }
.legend { display:flex; gap:16px; flex-wrap:wrap; font-size:.85rem; color:var(--muted); } .legend span { display:flex; align-items:center; gap:6px; } .hint { margin:0; font-size:.85rem; }
.chips { display:flex; flex-wrap:wrap; gap:4px; min-width:0; } .ch { display:inline-flex; align-items:center; justify-content:center; min-width:30px; height:22px; padding:0 5px; border-radius:5px; font:600 .72rem/1 var(--mono); font-style:normal; color:#fff; }
.ch.box { background:var(--occ); } .ch.empty { background:var(--free); } .ch.none { background:var(--surface); color:var(--muted); border:1px solid var(--line); }
.ch.new { background:var(--accent); } .ch.same { background:var(--same); } .ch.logic { background:transparent; border:2px dashed currentColor; } .ch.new.logic { color:var(--accent); } .ch.same.logic { color:var(--same); }
.pill.newp { background:var(--accent-soft); color:var(--accent); } .pill.samep { background:var(--same-soft); color:var(--same); }
.evs { display:flex; flex-direction:column; border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.ev { border-top:1px solid var(--line); } .ev:first-child { border-top:0; } .ev summary { list-style:none; cursor:pointer; display:grid; grid-template-columns:90px minmax(200px,auto) minmax(0,1fr) minmax(220px,auto); gap:12px; align-items:center; padding:9px 12px; font-size:.88rem; }
.ev summary::-webkit-details-marker { display:none; } .ev summary:hover { background:var(--surface); } .ev[open] summary { background:var(--accent-soft); } .ev summary:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
.ev .u { font:600 .9rem var(--mono); display:flex; align-items:center; } .ev .rs { display:flex; align-items:center; gap:6px; flex-wrap:wrap; } .ev .rs em { color:var(--muted); font-style:normal; } .ev .sv { color:var(--muted); text-align:right; }
@media (max-width:1000px) { .ev summary { grid-template-columns:1fr; } .ev .sv { text-align:left; } }
.msgs { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; padding:4px 12px 14px; } @media (max-width:800px) { .msgs { grid-template-columns:1fr; } }
.m { border-radius:8px; padding:10px 12px; font-size:.88rem; line-height:1.5; } .m b { display:block; font-size:.78rem; text-transform:uppercase; letter-spacing:.06em; margin-bottom:4px; } .m p { margin:0; } .m ul { margin:0; padding-left:18px; }
.m.img { background:var(--surface); border-left:3px solid var(--muted); } .m.img b { color:var(--muted); } .m.frm { background:var(--accent-soft); border-left:3px solid var(--accent); } .m.frm b { color:var(--accent); }
.ladwrap { display:grid; grid-template-columns:minmax(0,680px) minmax(0,1fr); gap:20px; align-items:start; } @media (max-width:1100px) { .ladwrap { grid-template-columns:1fr; } }
.ladders { width:100%; height:auto; } .ladders rect.u { fill:var(--surface); stroke:var(--line); } .ladders rect.u.new { fill:var(--unc-soft); stroke:var(--unc); }
.ladders rect.merged { fill:var(--unc-soft); stroke:var(--unc); stroke-width:2; } .ladders rect.miss { fill:url(#none); fill:var(--occ-soft); stroke:var(--occ); stroke-dasharray:6 4; }
.ladders text { font:500 11px var(--mono); fill:var(--ink); } .ladders text.px { fill:var(--muted); font-size:10px; } .ladders text.hd { font:600 13px var(--sans); } .ladders text.missx { fill:var(--occ); font:600 12px var(--sans); }
.ladders line.lk { stroke:var(--line); stroke-width:1; }
.elev { display:grid; grid-template-columns:64px minmax(0,1fr) minmax(0,1fr) minmax(0,0.8fr) minmax(0,1.4fr); grid-auto-rows:44px; gap:4px 8px; font-size:.84rem; }
.el-bf { display:flex; align-items:center; gap:8px; border-left:5px solid; border-radius:5px; background:var(--surface); padding:2px 8px 2px 4px; min-width:0; overflow:hidden; }
.el-bf img { height:100%; max-height:100%; width:auto; max-width:78%; object-fit:contain; border-radius:3px; } .el-bf span { font:600 .74rem var(--mono); color:var(--muted); white-space:nowrap; }
.el-h { grid-row:1; color:var(--muted); font:500 .7rem/44px var(--mono); letter-spacing:.08em; text-transform:uppercase; } .el-u { grid-column:1; font:600 .8rem/44px var(--mono); color:var(--muted); }
.el-c { border-radius:5px; display:flex; align-items:center; padding:0 10px; font-weight:600; min-width:0; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.el-c.occ { background:var(--occ-soft); color:var(--occ); } .el-c.free { background:var(--free-soft); color:var(--free); } .el-c.unc { background:var(--unc-soft); color:var(--unc); }
.el-c.miss { background:repeating-linear-gradient(135deg,var(--surface),var(--surface) 6px,var(--line) 6px,var(--line) 8px); color:var(--muted); font-weight:500; } .el-c.chg { box-shadow:inset 0 0 0 2px var(--unc); }
.el-d { border-left:5px solid; border-radius:5px; display:flex; align-items:center; padding:0 10px; font-weight:600; min-width:0; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
.hero { background:linear-gradient(135deg,var(--accent-soft),var(--panel) 70%); } .idea { margin:0; font-size:1.3rem; font-weight:600; line-height:1.4; max-width:60ch; text-wrap:balance; }
.stages { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:0; }
.stg { display:grid; grid-template-columns:44px minmax(0,360px) minmax(0,1fr); gap:18px; align-items:center; padding:14px 0; border-top:1px dashed var(--line); position:relative; } .stg:first-child { border-top:0; }
.stg .no { width:36px; height:36px; border-radius:999px; background:var(--accent); color:#fff; display:flex; align-items:center; justify-content:center; font:600 1rem var(--mono); }
.stg .sp img, .stg .sp svg { width:100%; max-height:180px; object-fit:contain; border-radius:8px; background:var(--surface); display:block; } .stg .st b { font-size:1.05rem; } .stg .st p { margin:6px 0 0; font-size:.92rem; display:flex; gap:8px; align-items:baseline; }
.tag { font:600 .66rem/1 var(--mono); text-transform:uppercase; letter-spacing:.08em; padding:4px 6px; border-radius:4px; flex:none; } .tag.in { background:var(--surface); color:var(--muted); border:1px solid var(--line); } .tag.out { background:var(--accent); color:#fff; }
.mini { display:flex; gap:6px; align-items:center; } .mini img { flex:1 1 0; min-width:0; height:110px; object-fit:cover; border-radius:6px; } .mini.two img { height:150px; object-fit:contain; background:var(--surface); } .stg .sp .mini img { height:110px; max-height:none; object-fit:cover; } .stg .sp .mini.two img { height:160px; object-fit:contain; }
.steps3 { display:flex; flex-direction:column; gap:6px; flex:none; } .steps3 span { font:600 .78rem var(--mono); padding:6px 10px; border-radius:6px; background:var(--accent-soft); color:var(--accent); white-space:nowrap; }
.map.small { max-height:200px; }
@media (max-width:900px) { .stg { grid-template-columns:44px 1fr; } .stg .st { grid-column:2; } }
.follow { display:grid; grid-template-columns:minmax(0,1.3fr) 40px minmax(0,1fr) 40px minmax(0,1fr); gap:10px; align-items:start; } .farrow { font-size:2rem; color:var(--accent); align-self:center; text-align:center; }
.fcol { display:flex; flex-direction:column; gap:8px; min-width:0; } .fcol h4 { margin:0; color:var(--ink); font-size:.92rem; } .fcol img { width:100%; border-radius:6px; background:var(--surface); } .fcol p { margin:0; font-size:.88rem; }
.mini.five { display:grid; grid-template-columns:1fr; gap:4px; } .mini.five img { height:auto; max-height:52px; object-fit:contain; }
.frow { display:flex; align-items:center; gap:10px; } .frow .u { font:600 .82rem var(--mono); min-width:64px; }
@media (max-width:1000px) { .follow { grid-template-columns:1fr; } .farrow { transform:rotate(90deg); } }
.rules4 { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; } @media (max-width:1100px) { .rules4 { grid-template-columns:repeat(2,minmax(0,1fr)); } } @media (max-width:600px) { .rules4 { grid-template-columns:1fr; } }
.rule { border:1px solid var(--line); border-radius:10px; padding:14px; background:var(--surface); display:flex; flex-direction:column; gap:6px; } .rule svg { width:100%; height:auto; background:var(--panel); border-radius:8px; } .rule p { margin:0; font-size:.88rem; color:var(--muted); }
.rule line { stroke:var(--muted); stroke-width:2; } .rule line.ok { stroke:var(--accent); stroke-width:3; } .rule .ar { font-size:28px; fill:var(--accent); }
.rule rect.unit { fill:var(--surface); stroke:var(--line); } .rule rect.box { fill:var(--occ); } .rule rect.none { fill:var(--surface); stroke:var(--line); }
.rule rect.same { fill:none; stroke:var(--same); stroke-width:3; } .rule rect.new { fill:none; stroke:var(--accent); stroke-width:3; } .rule rect.frame { fill:var(--surface); stroke:var(--muted); } .rule rect.dev { fill:var(--accent-soft); stroke:var(--accent); stroke-width:2; } .rule rect.dev.cut { stroke:var(--occ); stroke-dasharray:4 3; }
.rule text.lbl { font:500 12px var(--sans); fill:var(--muted); } .rule text.sm { font:600 11px var(--sans); fill:var(--ink); }
.io3 { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; } @media (max-width:900px) { .io3 { grid-template-columns:1fr; } } .ioc { border-radius:10px; padding:14px 16px; background:var(--surface); border-top:4px solid var(--accent); } .ioc:first-child { border-top-color:var(--muted); } .ioc:last-child { border-top-color:var(--free); } .ioc h4 { margin:0 0 6px; color:var(--ink); } .ioc ul { margin:0; padding-left:18px; font-size:.9rem; }
.tbar { display:flex; height:34px; border-radius:8px; overflow:hidden; } .tbar i, .sw { font-style:normal; display:flex; align-items:center; justify-content:center; color:#fff; font:600 .8rem var(--sans); } .sw { width:14px; height:14px; border-radius:3px; display:inline-block; }
.t0 { background:var(--muted); } .t1 { background:var(--accent); } .t2 { background:var(--same); } .t3 { background:var(--free); }
.eq { display:flex; align-items:stretch; gap:10px; flex-wrap:wrap; } .eq em { font:600 1.6rem/1 var(--sans); color:var(--muted); font-style:normal; align-self:center; }
.eqt { border-radius:10px; padding:10px 14px; min-width:110px; display:flex; flex-direction:column; } .eqt b { font:600 1.8rem/1.1 var(--sans); font-variant-numeric:tabular-nums; } .eqt span { font-size:.8rem; color:var(--muted); }
.eqt.frm { background:var(--accent-soft); } .eqt.frm b { color:var(--accent); } .eqt.bad { background:var(--occ-soft); } .eqt.bad b { color:var(--occ); } .eqt.img { background:var(--surface); border:1px solid var(--line); }
.ladtext { display:flex; flex-direction:column; gap:16px; min-width:0; }
.find { border:1px solid var(--line); border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:10px; background:var(--panel); }
.find header { display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:0; } .find header b { font-size:1rem; } .ftag { font:600 .7rem/1 var(--mono); text-transform:uppercase; letter-spacing:.08em; padding:5px 8px; border-radius:5px; background:var(--occ); color:#fff; }
.fpics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; } .fpics img { width:100%; max-height:150px; object-fit:contain; border-radius:6px; background:var(--surface); } .fpics figcaption { font-size:.8rem; color:var(--muted); }
.place { display:grid; grid-template-columns:minmax(0,1fr) 30px minmax(0,1.1fr); gap:10px; align-items:center; } .parrow { font-size:1.8rem; color:var(--accent); text-align:center; }
.pfs { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:6px; } .pfs figure { margin:0; border:3px solid; border-radius:7px; overflow:hidden; gap:0; } .pfs img { width:100%; height:70px; object-fit:cover; display:block; border-radius:0; }
.pfs figcaption { color:#fff; font:600 .72rem var(--mono); padding:2px 6px; display:block; } .prack { width:100%; max-height:420px; object-fit:contain; border-radius:8px; background:var(--surface); }
.stg .sp .place img.prack { max-height:300px; } .stg .sp .pfs img { height:56px; object-fit:cover; max-height:none; }
.block > .place { max-width:1100px; } .block > .place .pfs img { height:110px; } .block > .place .prack { max-height:460px; }
.mrow { display:flex; flex-direction:column; gap:10px; padding:14px 0; border-top:1px dashed var(--line); } .mrow:first-of-type { border-top:0; } .mrow p { margin:0; font-size:.92rem; max-width:110ch; }
.mhead { display:flex; align-items:center; gap:10px; } .mhead .no { width:30px; height:30px; border-radius:999px; background:var(--accent); color:#fff; display:flex; align-items:center; justify-content:center; font:600 .9rem var(--mono); } .mhead b { font-size:1.02rem; }
.mpics { display:grid; grid-template-columns:minmax(0,1.7fr) 110px minmax(0,1fr); gap:10px; align-items:center; } .mpics figure { gap:4px; } .mpics figcaption { font-size:.8rem; color:var(--muted); display:block; }
.mpics img, .mwide { width:100%; max-height:320px; object-fit:contain; border-radius:8px; background:var(--surface); display:block; }
.mmid { display:flex; flex-direction:column; align-items:center; gap:2px; color:var(--accent); text-align:center; } .mmid span { font:600 .72rem var(--mono); text-transform:uppercase; letter-spacing:.06em; } .mmid em { font-size:2rem; font-style:normal; line-height:1; }
@media (max-width:900px) { .mpics { grid-template-columns:1fr; } .mmid em { transform:rotate(90deg); } }
.stg.big { grid-template-columns:44px minmax(0,1.6fr) minmax(0,1fr); } .stg.big .sp .place { grid-template-columns:minmax(0,0.8fr) 30px minmax(0,1fr); }
.pfs.three { grid-template-columns:1fr; } .stg.big .sp .pfs img { height:110px; } .stg.big .sp .place img.prack { max-height:520px; } .pfs figcaption { font-size:.85rem; padding:4px 8px; }
.plain { margin:0; font-size:1rem; line-height:1.6; max-width:85ch; }
.funnel { display:flex; align-items:stretch; gap:10px; flex-wrap:wrap; } .funnel em { align-self:center; font-size:1.6rem; color:var(--muted); font-style:normal; }
.fn { flex:1 1 160px; border-radius:10px; padding:14px 16px; background:var(--surface); border:1px solid var(--line); display:flex; flex-direction:column; gap:4px; } .fn b { font:600 2rem/1 var(--sans); } .fn span { color:var(--muted); font-size:.92rem; }
.fn.on { background:var(--accent-soft); border-color:var(--accent); } .fn.on b { color:var(--accent); }
.fcards { display:grid; grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); gap:12px; } .fcard { border:1px solid var(--line); border-radius:10px; overflow:hidden; gap:0; background:var(--surface); }
.fcard img { width:100%; height:140px; object-fit:cover; display:block; border-radius:0; } .fcard figcaption { display:flex; flex-direction:column; gap:3px; padding:10px 12px; font-size:.86rem; } .fcard figcaption span { color:var(--muted); }
.cl { align-self:flex-start; border-radius:999px; padding:2px 8px; font-size:.76rem; font-weight:600; } .cl.good { background:var(--free-soft); color:var(--free) !important; } .cl.ok { background:var(--accent-soft); color:var(--accent) !important; } .cl.soft { background:var(--unc-soft); color:var(--unc) !important; }
.embed body { padding-inline:0; padding-block:0 20px; } .embed .page { width:100%; } .embed .stick { position:static; } .embed .stick header { display:none; }
.w3o { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; } @media (max-width:1000px) { .w3o { grid-template-columns:1fr; } }
.wbox { border-radius:10px; padding:12px 14px; display:flex; flex-direction:column; gap:6px; } .wbox b { font-size:1rem; } .wbox p { margin:0; font-size:.93rem; } .wbox ul { margin:0; padding-left:18px; font-size:.93rem; } .wbox li { margin:4px 0; }
.wbox.bad { background:var(--occ-soft); } .wbox.bad b { color:var(--occ); } .wbox.good { background:var(--accent-soft); } .wbox.good > b { color:var(--accent); }
.devs2 { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:14px; }
.dev2 { border:1px solid var(--line); border-top:4px solid; border-radius:10px; padding:14px; display:flex; flex-direction:column; gap:10px; background:var(--panel); min-width:0; }
.dev2 header { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:0; } .dev2 header b { font-size:1.1rem; } .dev2 header span { font:600 .9rem var(--mono); } .dev2 header em { margin-left:auto; font-style:normal; color:var(--muted); font-size:.82rem; }
.dev2 img { width:100%; height:110px; object-fit:contain; background:var(--surface); border-radius:6px; }
.three { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:7px; font-size:.92rem; } .three li { display:flex; gap:8px; align-items:flex-start; }
.ico { flex:none; width:18px; height:18px; border-radius:4px; margin-top:2px; } .ico.j { background:var(--same-soft); border:2px solid var(--same); } .ico.t { background:var(--accent-soft); border-top:4px solid var(--accent); } .ico.b { background:var(--accent-soft); border-bottom:4px solid var(--accent); }
.rk { margin:0; font-size:.88rem; padding:6px 10px; border-radius:6px; } .rk.same { background:var(--free-soft); color:var(--free); } .rk.diff { background:var(--unc-soft); color:var(--unc); }
.dev2 details summary { cursor:pointer; color:var(--accent); font-size:.86rem; font-weight:600; } .dev2 details ul { margin:6px 0 0; padding-left:18px; font-size:.84rem; color:var(--muted); } .dev2 details p { margin:6px 0 0; font-size:.84rem; }
.cov { display:flex; flex-direction:column; gap:6px; margin-top:10px; } .cvl { font:600 .72rem/1 var(--mono); text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
.cvs { display:grid; grid-auto-flow:column; grid-auto-columns:minmax(0,1fr); gap:3px; } .cv { background:var(--free-soft); border-radius:5px; padding:6px 0; display:flex; flex-direction:column; align-items:center; gap:2px; min-width:0; }
.cv b { font:600 .95rem var(--sans); color:var(--free); } .cv span { font:500 .6rem var(--mono); color:var(--muted); } .cv.lo { background:var(--unc-soft); } .cv.lo b { color:var(--unc); }
@media (max-width:800px) { .cvs { grid-auto-flow:row; grid-template-columns:repeat(8,minmax(0,1fr)); } }
.wh, .wy, .ex { margin-top:8px; } .wh span, .wy span, .ex span { display:block; font:600 .68rem/1 var(--mono); text-transform:uppercase; letter-spacing:.08em; margin-bottom:4px; }
.wh span { color:var(--ink); } .wy span { color:var(--accent); } .ex span { color:var(--free); } .stg .st .wh p, .stg .st .wy p, .stg .st .ex p { display:block; margin:0; font-size:.94rem; line-height:1.55; }
.ex { background:var(--free-soft); border-radius:8px; padding:8px 10px; } .wy p { color:var(--muted); }
.gloss { display:grid; grid-template-columns:minmax(140px,220px) minmax(0,1fr); gap:8px 18px; margin:0; } .gloss dt { font-weight:600; } .gloss dd { margin:0; color:var(--muted); } @media (max-width:600px) { .gloss { grid-template-columns:1fr; } }
.stg { align-items:start; }
.rrows { display:flex; flex-direction:column; gap:0; } .rrow { display:grid; grid-template-columns:minmax(0,0.9fr) minmax(0,1.5fr) minmax(0,0.6fr); gap:20px; padding:16px 0; border-top:1px dashed var(--line); align-items:start; } .rrow:first-child { border-top:0; }
@media (max-width:1000px) { .rrow { grid-template-columns:1fr; } }
.rq { display:flex; gap:12px; align-items:flex-start; } .rq .no { width:32px; height:32px; flex:none; border-radius:999px; background:var(--accent); color:#fff; display:flex; align-items:center; justify-content:center; font:600 .9rem var(--mono); }
.rq b { font-size:1.05rem; display:block; margin-bottom:6px; } .rq p, .rex p, .rans p { margin:0; font-size:.94rem; line-height:1.55; } .rex p { margin-top:8px; }
.k2 { display:block; font:600 .66rem/1 var(--mono); text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-bottom:6px; }
.rex { background:var(--surface); border-radius:10px; padding:12px 14px; } .rans { background:var(--accent-soft); border-radius:10px; padding:12px 14px; } .rans b { color:var(--accent); }
.rline { display:grid; grid-template-columns:70px minmax(0,1fr) auto; gap:10px; align-items:center; margin:6px 0; } .rline .u { font:600 .85rem var(--mono); }
.vd { font-size:.85rem; font-weight:600; padding:3px 8px; border-radius:6px; white-space:nowrap; } .vd.occ { background:var(--occ-soft); color:var(--occ); } .vd.free { background:var(--free-soft); color:var(--free); } .vd.newv { background:var(--accent-soft); color:var(--accent); } .vd.samev { background:var(--same-soft); color:var(--same); }
@media (max-width:700px) { .rline { grid-template-columns:1fr; } .vd { white-space:normal; } }
.rsvg { width:100%; max-width:440px; height:auto; display:block; } .rsvg line { stroke-width:4; stroke-linecap:round; } .rsvg line.ok { stroke:var(--muted); } .rsvg line.bad { stroke:var(--occ); stroke-dasharray:6 5; } .rsvg line.fin { stroke:var(--accent); }
.rsvg text { font:600 12px var(--sans); fill:var(--muted); } .rsvg text.bad { fill:var(--occ); } .rsvg text.ar { font-size:26px; fill:var(--accent); } .rsvg text.h { fill:var(--ink); }
.rfig { margin-top:10px; } .rfig img { max-height:120px; } .rbest { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; } .rbest img { height:70px; object-fit:contain; } .rbest figcaption { font-size:.76rem; color:var(--muted); display:block; }
.ccs { grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); } .cc { padding:14px 16px; display:flex; flex-direction:column; gap:4px; }
.cc .ct { font:600 .7rem/1.2 var(--mono); letter-spacing:.06em; text-transform:uppercase; color:var(--muted); } .cc strong { font:600 1.7rem/1.1 var(--sans); color:var(--accent); font-variant-numeric:tabular-nums; }
.cc em { font-style:normal; display:flex; justify-content:space-between; gap:6px; align-items:baseline; border-top:1px solid var(--line); padding-top:6px; margin-top:2px; font-size:.82rem; color:var(--muted); } .cc em b { color:var(--ink); font-weight:600; font-variant-numeric:tabular-nums; }
.cc em.nt { display:block; }
.iflow { display:flex; align-items:center; gap:8px; flex-wrap:wrap; padding:12px 0 4px; } .iflow span { border:1px solid var(--line); border-radius:999px; padding:6px 12px; font-size:.86rem; font-weight:500; background:var(--surface); }
.iflow em { color:var(--muted); font-style:normal; }
.lrows { display:flex; flex-direction:column; gap:12px; } .lrow { border:1px solid var(--line); border-radius:10px; padding:12px 14px; display:flex; flex-direction:column; gap:10px; }
.lh { display:flex; align-items:center; gap:10px; flex-wrap:wrap; } .lh b { font-weight:600; } .lpair { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; } .lpair.three { grid-template-columns:repeat(3,minmax(0,1fr)); } @media (max-width:800px) { .lpair { grid-template-columns:1fr; } }
.lclose { display:flex; flex-direction:column; gap:8px; border-top:1px solid var(--line); padding-top:12px; } .lcrow { display:flex; align-items:center; gap:6px 12px; flex-wrap:wrap; } .btnup { align-self:flex-start; display:inline-flex; gap:6px; align-items:center; border:1px solid var(--accent); color:var(--accent); border-radius:8px; padding:5px 12px; font-weight:600; font-size:.82rem; cursor:pointer; background:var(--panel); }
.btnup:hover { background:var(--accent-soft); } .btnup.off { opacity:.5; cursor:default; } .btnapp { display:inline-flex; align-items:center; gap:4px; background:var(--accent); color:#fff; border-radius:8px; padding:5px 12px; font-weight:600; text-decoration:none; margin:0 4px; } .btnapp:hover { filter:brightness(1.08); } .lcnote { font-size:.8rem; } .cres p { margin:4px 0; font-size:.9rem; }
.pmeta { display:flex; flex-wrap:wrap; gap:6px; align-items:center; } .pmeta .muted { font-size:.82rem; flex-basis:100%; } .pspec { margin:0; font-size:.9rem; } .pspec b { font-weight:600; margin-right:6px; }
.lfig.wide img { height:auto; max-height:240px; } details.ptab summary { cursor:pointer; color:var(--accent); font-weight:600; font-size:.9rem; } details.ptab table { margin-top:6px; }
.lsec { margin:22px 0 10px; font-size:1.1rem; } .lsws { display:flex; flex-direction:column; gap:14px; margin-bottom:16px; } .lsw { padding:16px 20px; display:flex; flex-direction:column; gap:14px; min-width:0; }
.lhd { display:flex; align-items:center; justify-content:space-between; gap:8px 16px; flex-wrap:wrap; } .lid { display:flex; align-items:baseline; gap:6px 14px; flex-wrap:wrap; min-width:0; } .lnum { font:600 .88rem var(--mono); white-space:nowrap; } .lmk { font-size:1.08rem; font-weight:600; }
.lsub { display:flex; flex-wrap:wrap; gap:4px 14px; margin-top:-8px; font-size:.84rem; } .lsrc { color:var(--muted); }
.lres { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; } .lres .lfig img { height:150px; } .lres .lfig.none { min-height:150px; align-self:start; } .lres .lfig.none span { color:var(--muted); font-size:.8rem; padding:0 12px; text-align:center; }
.lblk { display:flex; flex-direction:column; gap:8px; } .lstrip { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; } @media (max-width:800px) { .lstrip { grid-template-columns:repeat(2,minmax(0,1fr)); } .lres { grid-template-columns:1fr; } .lres .lfig img { height:120px; } .lres .lfig.none { display:none; } .lsw { padding:14px; } }
.lthumb { margin:0; padding:6px; border:1px solid var(--line); border-radius:8px; display:flex; flex-direction:column; gap:5px; min-width:0; } .lthumb.on { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); background:var(--accent-soft); }
.lthumb img, .lnoimg { width:100%; height:56px; object-fit:contain; background:var(--surface); border-radius:5px; display:block; } .lthumb figcaption { flex-direction:column; flex-wrap:nowrap; gap:1px; font-size:.76rem; line-height:1.35; } .lthumb figcaption b { font-weight:600; display:flex; align-items:center; gap:6px; }
.lthumb figcaption span { color:var(--muted); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; } .lthumb em { font:600 .6rem/1 var(--mono); text-transform:uppercase; letter-spacing:.06em; background:var(--accent); color:#fff; padding:3px 5px; border-radius:4px; font-style:normal; }
.l3 { list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:10px; } @media (max-width:800px) { .l3 { grid-template-columns:1fr; } }
.l3 li { display:grid; grid-template-columns:22px minmax(0,1fr); gap:8px; padding:8px 10px; border:1px solid var(--line); border-radius:8px; min-width:0; } .l3 li.on { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); background:var(--accent-soft); } .l3 li.off { opacity:.5; }
.l3n { width:22px; height:22px; border-radius:999px; background:var(--muted); color:#fff; font:600 .7rem/22px var(--mono); text-align:center; } .l3 li.on .l3n { background:var(--accent); }
.l3b { display:flex; flex-direction:column; gap:4px; min-width:0; font-size:.8rem; } .l3b b { font-weight:600; font-size:.84rem; display:flex; align-items:center; gap:6px; } .l3b img, .l3b .lnoimg { width:100%; height:56px; object-fit:contain; background:var(--surface); border-radius:5px; display:block; }
.l3b em { font:600 .6rem/1 var(--mono); text-transform:uppercase; letter-spacing:.06em; background:var(--accent); color:#fff; padding:3px 5px; border-radius:4px; font-style:normal; } .l3b small { color:var(--muted); font-size:.74rem; }
.lmd { display:grid; grid-template-columns:minmax(260px,340px) minmax(0,1fr); gap:16px; align-items:start; margin-bottom:16px; } @media (max-width:900px) { .lmd { grid-template-columns:1fr; } }
.lpick { padding:12px; display:flex; flex-direction:column; gap:10px; position:sticky; top:12px; min-width:0; } @media (max-width:900px) { .lpick { position:static; } }
.lrack { width:100%; height:auto; max-height:420px; display:block; border-radius:8px; background:var(--surface); } .lpg { cursor:pointer; } .lpg polygon { fill:rgba(255,255,255,.06); stroke-width:5; } .lpg.on polygon { fill:rgba(29,95,207,.28); stroke-width:10; }
.lpg text { font:600 30px var(--mono); fill:#fff; paint-order:stroke; stroke:rgba(0,0,0,.75); stroke-width:6px; pointer-events:none; }
.lchips { display:flex; flex-direction:column; gap:6px; } .lchip { display:grid; grid-template-columns:10px auto minmax(0,1fr); gap:8px; align-items:center; text-align:left; font:inherit; font-size:.84rem; color:var(--ink); background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:7px 10px; cursor:pointer; }
.lchip b { font:600 .8rem var(--mono); white-space:nowrap; } .lchip > span:last-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .lchip:hover { background:var(--surface); } .lchip.on { border-color:var(--accent); box-shadow:0 0 0 1px var(--accent); background:var(--accent-soft); }
.lsd { width:10px; height:10px; border-radius:999px; background:var(--muted); } .lsd.free { background:var(--free); } .lsd.unc { background:var(--unc); }
.lflow { list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; counter-reset:f; } .lflow li { display:grid; grid-template-columns:22px minmax(0,1fr); column-gap:8px; align-items:baseline; padding:9px 12px; background:var(--surface); border-radius:8px; min-width:0; }
.lflow li::before { counter-increment:f; content:counter(f); grid-row:span 2; width:22px; height:22px; border-radius:999px; background:var(--accent); color:#fff; font:600 .7rem/22px var(--mono); text-align:center; align-self:start; } .lflow b { font-weight:600; font-size:.88rem; } .lflow span { color:var(--muted); font-size:.8rem; }
@media (max-width:1000px) { .lflow { grid-template-columns:repeat(2,minmax(0,1fr)); } } @media (max-width:520px) { .lflow { grid-template-columns:1fr; } }
.lfig { margin:0; display:flex; flex-direction:column; gap:4px; } .lfig img { width:100%; height:170px; object-fit:contain; background:var(--surface); border-radius:6px; cursor:zoom-in; display:block; } .lfig figcaption { font-size:.8rem; color:var(--muted); }
.lfig.none { justify-content:center; align-items:center; min-height:170px; background:var(--surface); border-radius:6px; } .flow.one { grid-template-columns:1fr; }
.kc { display:inline-flex; align-items:baseline; gap:4px; border-radius:999px; padding:3px 10px; background:var(--accent-soft); color:var(--accent); font-size:.8rem; white-space:nowrap; } .kc b { font-weight:600; }
.kc.oth { background:var(--surface); color:var(--muted); border:1px dashed var(--line); } .kfin { display:flex; flex-direction:column; gap:4px; align-items:flex-start; } .kfin .kc { font-size:.9rem; padding:4px 12px; }
.kf { font-size:.78rem; } .kimg { width:220px; height:80px; object-fit:contain; background:var(--surface); border-radius:6px; cursor:zoom-in; display:block; }
table.ktab td { vertical-align:middle; } table.ktab .kc { font-size:.86rem; } .kf { display:block; font-size:.78rem; margin-top:4px; }
.k3 { display:grid; grid-template-columns:repeat(5,minmax(130px,1fr)); gap:8px; } .kfig { margin:0; border:1px solid var(--line); border-radius:8px; padding:6px; display:flex; flex-direction:column; gap:5px; min-width:0; }
.kfig.kept { border:2px solid var(--accent); } .kfig img { width:100%; height:62px; object-fit:contain; background:var(--surface); border-radius:5px; cursor:zoom-in; display:block; }
.kfig figcaption { display:flex; flex-direction:column; gap:3px; align-items:flex-start; } .kr { font:600 .74rem var(--mono); color:var(--muted); } .ks { font-size:.7rem; line-height:1.3; }
.kform { display:flex; align-items:stretch; gap:8px; flex-wrap:wrap; margin:10px 0 14px; } .kform span { flex:1 1 180px; background:var(--surface); border-radius:10px; padding:10px 12px; font-size:.84rem; color:var(--muted); display:flex; flex-direction:column; gap:2px; }
.kform span b { color:var(--ink); font-size:.95rem; } .kform em { align-self:center; font-style:normal; font-weight:600; color:var(--muted); } .kform .kres { background:var(--accent-soft); } .kform .kres b { color:var(--accent); }
@media (max-width:900px) { .k3 { grid-template-columns:1fr; } }
.hero .flow { margin-top:16px; grid-template-columns:repeat(6,minmax(0,1fr)); } @media (max-width:1000px) { .hero .flow { grid-template-columns:repeat(3,minmax(0,1fr)); } } @media (max-width:560px) { .hero .flow { grid-template-columns:repeat(2,minmax(0,1fr)); } }
@media (max-width:700px) { table.howt thead { display:none; } table.howt tr { display:block; padding:8px 0; border-top:1px solid var(--line); } table.howt td { display:block; width:auto !important; border:0; padding:2px 10px; } table.howt td.n { color:var(--accent); } } .flow .fnum { margin-top:auto; padding-top:6px; color:var(--accent); font:600 .82rem var(--mono); }
table.howt td:first-child { width:17%; } table.howt td:nth-child(3) { width:22%; } table.howt td.n { width:15%; font-variant-numeric:tabular-nums; }
.isteps { list-style:none; margin:8px 0 0; padding:0; display:flex; flex-direction:column; gap:0; position:relative; } .isteps::before { content:""; position:absolute; left:17px; top:8px; bottom:8px; width:2px; background:var(--line); }
.istep { display:grid; grid-template-columns:36px minmax(0,1fr); gap:16px; padding:10px 0; position:relative; } .inum { width:36px; height:36px; border-radius:999px; background:var(--accent); color:#fff; display:flex; align-items:center; justify-content:center; font:600 .95rem var(--mono); z-index:1; box-shadow:0 0 0 4px var(--panel); }
.icard { border:1px solid var(--line); border-radius:12px; padding:16px 18px; background:var(--panel); min-width:0; } .icard header { display:flex; align-items:center; gap:10px; padding:0; margin-bottom:10px; } .icard h3 { margin:0; font-size:1.12rem; }
.ist { font:600 .68rem/1 var(--mono); text-transform:uppercase; letter-spacing:.08em; padding:5px 8px; border-radius:999px; } .ist.live { background:var(--free-soft); color:var(--free); } .ist.next { background:var(--unc-soft); color:var(--unc); }
.igrid { display:grid; grid-template-columns:minmax(0,1.15fr) minmax(0,1fr); gap:22px; } @media (max-width:900px) { .igrid { grid-template-columns:1fr; } }
.icol { display:flex; flex-direction:column; gap:4px; min-width:0; } .icol p { margin:0 0 8px; font-size:.94rem; line-height:1.55; } .ik { font:600 .66rem/1 var(--mono); text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin-top:2px; }
.iacc strong { font:600 2rem/1.1 var(--sans); color:var(--accent); } .inote { color:var(--muted); font-size:.88rem !important; }
.ibars { display:flex; flex-direction:column; gap:6px; margin-top:4px; } .ib { display:grid; grid-template-columns:150px minmax(0,1fr) 62px; gap:10px; align-items:center; font-size:.84rem; color:var(--muted); }
.ib div { height:10px; background:var(--surface); border-radius:999px; overflow:hidden; } .ib i { display:block; height:100%; background:var(--line); border-radius:999px; } .ib b { font-weight:600; text-align:right; font-variant-numeric:tabular-nums; color:var(--ink); }
.ib.on { color:var(--ink); font-weight:600; } .ib.on i { background:var(--accent); } .ib.on b { color:var(--accent); } .iax { margin:2px 0 0 !important; font-size:.72rem !important; color:var(--muted); }
@media (max-width:600px) { .ib { grid-template-columns:110px minmax(0,1fr) 54px; } }
.orest { display:flex; flex-direction:column; gap:8px; margin-top:12px; padding-top:12px; border-top:1px solid var(--line); }
.osplit { display:flex; flex-direction:column; gap:5px; } .oshead { display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; font-size:.88rem; } .oshead span { color:var(--muted); }
.osbar { display:flex; height:22px; border-radius:6px; overflow:hidden; background:var(--surface); } .osbar i { display:flex; align-items:center; justify-content:center; color:#fff; font:600 .74rem var(--sans); font-style:normal; white-space:nowrap; }
.osbar i.r, .oslegend i.r { background:var(--free); } .osbar i.w, .oslegend i.w { background:var(--occ); } .osbar i.p, .oslegend i.p { background:var(--unc); }
.oslegend { display:flex; gap:14px; flex-wrap:wrap; font-size:.8rem; color:var(--muted); } .oslegend span { display:flex; align-items:center; gap:5px; } .oslegend i { width:10px; height:10px; border-radius:3px; display:inline-block; }
.owrong { margin:0; padding-left:18px; font-size:.88rem; } .owrong li { margin:3px 0; } .owrong b { color:var(--occ); margin-right:4px; }
.tabs.grouped { display:flex; gap:0; align-items:stretch; border-bottom:1px solid var(--line); overflow-x:auto; }
.tg { display:flex; flex-direction:column; gap:2px; padding:0 14px; border-left:1px solid var(--line); } .tg:first-child { padding-left:0; border-left:0; }
.tgl { font:600 .62rem/1 var(--mono); letter-spacing:.12em; text-transform:uppercase; color:var(--muted); padding:6px 14px 0; opacity:.8; } .tg:first-child .tgl { padding-left:14px; }
.tgb { display:flex; gap:2px; } .tabs.grouped button { padding:9px 14px 10px; }
.imodel { font-weight:600; } details.irest { margin-top:10px; border:1px solid var(--line); border-radius:8px; background:var(--surface); }
details.irest summary { cursor:pointer; padding:9px 12px; font:600 .86rem var(--sans); color:var(--accent); list-style:none; display:flex; align-items:center; gap:8px; } details.irest summary::-webkit-details-marker { display:none; }
details.irest summary::before { content:"▸"; transition:transform .15s; } details.irest[open] summary::before { transform:rotate(90deg); } .irb { padding:0 12px 12px; display:flex; flex-direction:column; gap:8px; } .irb p { margin:0; font-size:.9rem; }
.orest { margin-top:0; padding-top:0; border-top:0; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior:auto !important; } }
</style>
"""

FLOW_CSS = """<style>
/* the flow page: white only, one system font, the steps as numbered cards, detail in Read more */
:root, :root[data-theme="dark"], :root:not([data-theme="light"]) { --bg:#fff; --panel:#fff; --surface:#f5f6f8; --ink:#111827; --muted:#5b6472; --line:#e5e7eb; --accent:#1d4ed8; --accent-soft:#eaf0fb;
  --occ:#b4372a; --occ-soft:#fbe7e3; --free:#1a7f45; --free-soft:#e7f4ec; --unc:#b25e0a; --unc-soft:#fdf1e2; --same:#8c3cb4; --same-soft:#f3e9f8;
  --good-soft:#e8f5ec; --shadow:0 1px 2px rgba(17,24,39,.05), 0 6px 18px rgba(17,24,39,.06); --sans:-apple-system,"Segoe UI",Roboto,Arial,sans-serif; --mono:-apple-system,"Segoe UI",Roboto,Arial,sans-serif; color-scheme:light; }
body { background:#fff; font-size:16px; }
.rail { display:flex; gap:4px; overflow-x:auto; scrollbar-width:none; padding:8px 0 14px; border-bottom:1px solid var(--line); margin-bottom:22px; } .rail::-webkit-scrollbar { display:none; }
.rail a { display:inline-flex; align-items:center; gap:7px; white-space:nowrap; text-decoration:none; color:var(--muted); font-size:.9rem; font-weight:500; padding:7px 12px; border-radius:999px; border:1px solid transparent; }
.rail a span { width:20px; height:20px; border-radius:999px; background:var(--surface); color:var(--ink); font-size:.72rem; font-weight:600; display:inline-flex; align-items:center; justify-content:center; }
.rail a:hover { background:var(--surface); color:var(--ink); } .rail a.on { color:var(--accent); background:var(--accent-soft); border-color:#c7d7f7; } .rail a.on span { background:var(--accent); color:#fff; }
.steps { display:flex; flex-direction:column; gap:22px; } .step[hidden] { display:none !important; }
.snav { display:grid; grid-template-columns:1fr auto 1fr; gap:12px; align-items:center; padding-top:16px; margin-top:4px; border-top:1px solid var(--line); }
.snav .scount { color:var(--muted); font-size:.9rem; text-align:center; }
.sbtn { font:inherit; font-size:.95rem; font-weight:600; border-radius:10px; padding:10px 16px; cursor:pointer; border:1px solid var(--line); background:var(--panel); color:var(--ink); max-width:100%; }
.sbtn.prev { justify-self:start; } .sbtn.next { justify-self:end; background:var(--accent); border-color:var(--accent); color:#fff; } .sbtn:hover { filter:brightness(1.05); box-shadow:var(--shadow); }
@media (max-width:700px) { .snav { grid-template-columns:1fr; } .sbtn { justify-self:stretch !important; } }
.rail a span { flex:none; }
.step { padding:22px 24px 20px; display:flex; flex-direction:column; gap:16px; min-width:0; } @media (max-width:700px) { .step { padding:16px; } }
.shead { display:grid; grid-template-columns:44px minmax(0,1fr); gap:16px; align-items:start; padding:0; }
.sno { width:44px; height:44px; border-radius:999px; background:var(--accent); color:#fff; display:flex; align-items:center; justify-content:center; font-size:1.1rem; font-weight:600; }
.stt h2 { font-size:1.35rem; margin:4px 0 6px; letter-spacing:-.01em; } .sres { margin:0; font-size:1.02rem; line-height:1.55; max-width:100ch; padding-left:12px; border-left:3px solid var(--free); }
.sres.warn { border-left-color:var(--unc); } .sres.muted { border-left-color:var(--line); color:var(--muted); } .sres.small { font-size:.9rem; padding-left:10px; border-left-width:2px; max-width:none; }
.front { display:flex; flex-direction:column; gap:14px; } .front.two { display:grid; grid-template-columns:minmax(0,1.1fr) minmax(0,1fr); gap:18px; align-items:start; } @media (max-width:900px) { .front.two { grid-template-columns:1fr; } }
.front .strip { margin:0; } .front .tw { overflow-x:auto; }
.ffig { margin:0; display:flex; flex-direction:column; gap:8px; } .ffig img { width:100%; max-height:560px; object-fit:contain; background:var(--surface); border-radius:10px; display:block; }
.ffig figcaption { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; font-size:.92rem; font-weight:500; }
.front-k { grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); }
details.more { border:1px solid var(--line); border-radius:10px; background:var(--surface); }
details.more > summary { list-style:none; cursor:pointer; padding:12px 16px; font-weight:600; color:var(--accent); display:flex; align-items:center; gap:10px; font-size:.95rem; }
details.more > summary::-webkit-details-marker { display:none; } details.more > summary::before { content:"▸"; font-size:1rem; transition:transform .15s; } details.more[open] > summary::before { transform:rotate(90deg); }
details.more > summary:hover { background:#eef0f3; border-radius:10px; } details.more[open] > summary { border-bottom:1px solid var(--line); }
.moreb { padding:16px; display:flex; flex-direction:column; gap:14px; background:#fff; border-radius:0 0 10px 10px; } .moreb .strip { margin:0; } .moreb > .card.block, .moreb .block { margin-bottom:0; box-shadow:none; }
.agrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(330px,1fr)); gap:14px; } .arow { border:1px solid var(--line); border-radius:10px; padding:12px 14px; display:flex; flex-direction:column; gap:10px; min-width:0; }
.ahead { display:flex; align-items:center; gap:8px 14px; flex-wrap:wrap; } .ahead b { font-size:1rem; } .arow .lfig img { height:130px; }
.orow { border:1px solid var(--line); border-radius:10px; padding:12px 14px; display:flex; flex-direction:column; gap:10px; min-width:0; } .orow .lres { grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); } .orow .lres .lfig img { height:auto; max-height:340px; }
@media (max-width:800px) { .orow .lres { grid-template-columns:1fr; } }
.lsw[hidden] { display:none !important; }
.lowres { display:grid; grid-template-columns:minmax(0,1.4fr) minmax(220px,0.8fr); gap:16px; align-items:start; background:var(--unc-soft); border-radius:10px; padding:14px 16px; }
.lowtxt b { font-size:1rem; } .lowtxt p { margin:6px 0 10px; font-size:.95rem; line-height:1.55; } .lowres .lclose { border-top:0; padding-top:0; }
.lrack.where { max-height:260px; background:var(--surface); } @media (max-width:800px) { .lowres { grid-template-columns:1fr; } }
.embed .rail { position:static; } .embed .step { scroll-margin-top:12px; }
/* no lines (user, 2026-10-26): shadows, tints and spacing separate things */
.card, .step, details.more, .arow, .orow, .lthumb, .l3 li, .lrow, .lsw, .find, .dev, .dev2, .fn, .flow li, .rule, .icard, .evs, .ev, .tw, .lpick, .lchip, .kfig, .snotes li, .wbox, .kform span { border:0 !important; }
.card, .step { box-shadow:var(--shadow); } .arow, .orow, .lrow, .lsw, .find, .dev2, .icard { box-shadow:0 1px 2px rgba(17,24,39,.04), 0 4px 14px rgba(17,24,39,.05); }
.rail { border-bottom:0; } .rail a.on { border-color:transparent; }
th, td { border:0 !important; } thead th { background:var(--surface); } tbody tr:nth-child(even) td { background:var(--surface); }
details.more > summary { border-bottom:0 !important; } details.more[open] > summary { box-shadow:inset 0 -1px 0 rgba(17,24,39,.04); }
.snav, .lclose, .lowres .lclose { border-top:0 !important; } .sres { border-left-width:3px; } .stg, .mrow, .rrow { border-top:0 !important; }
.ffig img, .lfig img, .pic img { box-shadow:none; }
.cc em { border-top:0 !important; padding-top:2px; } .sres { border-left:0 !important; padding:10px 14px; background:var(--good-soft, #e8f5ec); border-radius:10px; } .sres.warn { background:#fdf3e4; } .sres.muted { background:var(--surface); } .sres.small { padding:6px 10px; }
.step h2 { margin-top:0; } .snotes li { border-left:0 !important; } .why { border-left:0 !important; } .m.img, .m.frm { border-left:0 !important; } .rk { border:0; }
</style>
"""

JS_FLOW = """<script>
(function(){var embedded=false; try{embedded=!!(window.frameElement&&(window.frameElement.id==='vreport'||window.frameElement.id==='voverview'));}catch(e){embedded=false;} if(embedded) document.documentElement.classList.add('embed');
var links=[].slice.call(document.querySelectorAll('.rail a')), steps=[].slice.call(document.querySelectorAll('.step')), ids=steps.map(function(s){return s.id;});
var titles={}; links.forEach(function(a){titles[a.getAttribute('href').slice(1)]=a.textContent.replace(/^\d+\s*/,'');});
steps.forEach(function(s,i){var nav=document.createElement('div'); nav.className='snav';
  nav.innerHTML=(i>0?'<button type="button" class="sbtn prev" data-go="'+ids[i-1]+'">&larr; '+(i)+' '+titles[ids[i-1]]+'</button>':'<span></span>')
    +'<span class="scount">Step '+(i+1)+' of '+steps.length+'</span>'
    +(i<steps.length-1?'<button type="button" class="sbtn next" data-go="'+ids[i+1]+'">Next: '+(i+2)+' '+titles[ids[i+1]]+' &rarr;</button>':'<span></span>');
  s.appendChild(nav);});
function show(id,clicked){if(ids.indexOf(id)<0) id=ids[0]; steps.forEach(function(s){s.hidden=s.id!==id;});
  links.forEach(function(a){a.classList.toggle('on',a.getAttribute('href')==='#'+id);});
  try{localStorage.setItem('__KEY__',id);}catch(e){} try{history.replaceState(null,'','#'+id);}catch(e){}
  var a=links.filter(function(x){return x.getAttribute('href')==='#'+id;})[0]; if(a&&a.scrollIntoView) try{a.scrollIntoView({block:'nearest',inline:'center'});}catch(e){}
  if(!clicked) return; if(embedded){try{window.frameElement.scrollIntoView({block:'start'});}catch(e){}} else window.scrollTo(0,0);}
links.forEach(function(a){a.addEventListener('click',function(e){e.preventDefault(); show(a.getAttribute('href').slice(1),true);});});
document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-go]'); if(b){show(b.getAttribute('data-go'),true);}});
document.addEventListener('keydown',function(e){if(e.target&&/INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return; var i=ids.indexOf(ids.filter(function(x){return !document.getElementById(x).hidden;})[0]);
  if(e.key==='ArrowRight'&&i<ids.length-1) show(ids[i+1],true); if(e.key==='ArrowLeft'&&i>0) show(ids[i-1],true);});
var want=(location.hash||'').slice(1); if(ids.indexOf(want)<0){try{want=localStorage.getItem('__KEY__')||'';}catch(e){want='';}}
show(ids.indexOf(want)>=0?want:ids[0]); window.rtShow=function(id){show(id,true);};
var lb=document.getElementById('lb'), lbimg=document.getElementById('lbimg');
function open_(src){ if(embedded){ try{ var pd=window.parent.document, plb=pd.getElementById('lb'), pimg=pd.getElementById('lbimg'); if(plb&&pimg){ pimg.src=src; plb.hidden=false; return; } }catch(e){} } lbimg.src=src; lb.hidden=false; }
document.addEventListener('click',function(e){ var i=e.target.closest&&e.target.closest('img'); if(i&&i.id!=='lbimg'){ e.preventDefault(); open_(i.currentSrc||i.src); } });
lb.addEventListener('click',function(){ lb.hidden=true; lbimg.src=''; }); document.addEventListener('keydown',function(e){ if(e.key==='Escape'){ lb.hidden=true; } });
[].forEach.call(document.querySelectorAll('[data-pick]'),function(el){el.addEventListener('click',function(){var id=el.getAttribute('data-pick');
  [].forEach.call(document.querySelectorAll('.lsw[data-sw]'),function(c){c.hidden=c.getAttribute('data-sw')!==id;});
  [].forEach.call(document.querySelectorAll('[data-pick]'),function(x){x.classList.toggle('on',x.getAttribute('data-pick')===id);});});});
var mj=location.pathname.match(/\\/video-report\\/([^\\/]+)\\//), job=mj?decodeURIComponent(mj[1]):null;
function esc2(t){return String(t==null?'':t).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
[].forEach.call(document.querySelectorAll('.lclose'),function(box){var inp=box.querySelector('input'), lab=box.querySelector('.btnup'), note=box.querySelector('.lcnote'), out=box.querySelector('.cres');
  if(!job){lab.classList.add('off'); inp.disabled=true; var nm='__KEY__'.replace('video-report-','');
    note.innerHTML='uploads work in the RackTrack app: <a class="btnapp" href="http://localhost:8600/video-report/example-'+encodeURIComponent(nm)+'/index.html#agent" target="_blank" rel="noopener">Open this report in the app ↗</a> <span class="muted">(the app must be running on this computer)</span>'; return;}
  inp.addEventListener('change',function(){var f=inp.files[0]; if(!f)return; var fd=new FormData(); fd.append('file',f); fd.append('job',job); fd.append('device',box.dataset.dev);
    out.innerHTML='<p class="muted">Reading the close-up: about 20 s, a few minutes for a model seen for the first time.</p>';
    fetch('/api/closeup',{method:'POST',body:fd}).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||j.detail||r.statusText);return j;});}).then(function(j){
      var also=(j.also||[]).map(function(i){return 'D'+i;}).join(', ');
      if(j.shows_device===false){out.innerHTML='<p>'+esc2(j.detail)+'</p>'+(j.saved_to_report?'<p class="muted">Reload this page in a minute to see the report rebuilt with it.</p>':''); return;}
      var P=j.pictures||{}, bt=j.by_type||{}, fig=function(k,c){return P[k]?'<figure class="lfig"><img src="'+esc2(P[k])+'" alt="'+c+'"><figcaption>'+c+'</figcaption></figure>':'';};
      out.innerHTML='<p><b>'+(j.vendor?esc2(j.vendor)+(j.model?' '+esc2(j.model):' (model not read)'):'make not read')+'</b> · '
        +((j.fitted||j.auto)?(j.n_ports+' ports'+(j.auto?' (auto mode, numbered from the photo)':'')+': '+Object.keys(bt).map(function(k){return bt[k]+' '+k.toUpperCase();}).join(' + ')+', '+j.connected+' with a cable'):(j.needs_back?'the ports are on the back: upload a photo of the back':'no port layout'))+'</p>'
        +'<div class="lres">'+fig('cut','Close-up photo')+fig('vendor_ports','Vendor front image, ports numbered')+fig('rack_trace','Vendor layout on the rack image')+'</div>'
        +(also?'<p class="muted">The photo also shows '+esc2(also)+': kept for '+(j.also.length>1?'them':'it')+' too.</p>':'')
        +(j.saved_to_report?'<p class="muted">Kept with this switch: reload this page in a minute to see the report rebuilt with it.</p>':'');
    }).catch(function(e){out.innerHTML='<p class="muted">Could not read it: '+esc2(e.message)+'</p>';});});});
})();
</script>
"""

JS = """<script>
(function(){var embedded=false; try{embedded=!!(window.frameElement&&(window.frameElement.id==='vreport'||window.frameElement.id==='voverview'));}catch(e){embedded=false;} if(embedded) document.documentElement.classList.add('embed');  /* only inside the localhost Video tab, never in other viewers */var btns=[].slice.call(document.querySelectorAll('.tabs button')),tabs=[].slice.call(document.querySelectorAll('.tab'));
function show(id,clicked){tabs.forEach(function(t){t.hidden=t.dataset.tab!==id;});btns.forEach(function(b){b.setAttribute('aria-selected',b.dataset.tab===id?'true':'false');});try{localStorage.setItem('__KEY__',id);}catch(e){}try{history.replaceState(null,'','#'+id);}catch(e){} if(!clicked) return; if(embedded){try{window.frameElement.scrollIntoView({block:'start'});}catch(e){}} else window.scrollTo(0,0);}
btns.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.tab,true);});});
var ids=btns.map(function(b){return b.dataset.tab;}),want=(location.hash||'').slice(1);if(ids.indexOf(want)<0){try{want=localStorage.getItem('__KEY__')||'';}catch(e){want='';}}
show(ids.indexOf(want)>=0?want:ids[0]); window.rtShow=function(id){ if(ids.indexOf(id)>=0) show(id); };
var lb=document.getElementById('lb'), lbimg=document.getElementById('lbimg');
function open_(src){ if(embedded){ try{ var pd=window.parent.document, plb=pd.getElementById('lb'), pimg=pd.getElementById('lbimg'); if(plb&&pimg){ pimg.src=src; plb.hidden=false; return; } }catch(e){} }
  lbimg.src=src; lb.hidden=false; }
document.addEventListener('click',function(e){ var i=e.target.closest&&e.target.closest('img'); if(i&&i.id!=='lbimg'){ e.preventDefault(); open_(i.currentSrc||i.src); } });
lb.addEventListener('click',function(){ lb.hidden=true; lbimg.src=''; }); document.addEventListener('keydown',function(e){ if(e.key==='Escape'){ lb.hidden=true; } });
// device layout: pick a switch on the rack picture or the list, show only its card
[].forEach.call(document.querySelectorAll('[data-pick]'),function(el){el.addEventListener('click',function(){var id=el.getAttribute('data-pick');
  [].forEach.call(document.querySelectorAll('.lsw[data-sw]'),function(c){c.hidden=c.getAttribute('data-sw')!==id;});
  [].forEach.call(document.querySelectorAll('[data-pick]'),function(x){x.classList.toggle('on',x.getAttribute('data-pick')===id);});});});
// close-ups: sent to the RackTrack app that serves this report (/video-report/<job>/); elsewhere the button explains where to upload
var mj=location.pathname.match(/\/video-report\/([^\/]+)\//), job=mj?decodeURIComponent(mj[1]):null;
function esc2(t){return String(t==null?'':t).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
[].forEach.call(document.querySelectorAll('.lclose'),function(box){var inp=box.querySelector('input'), lab=box.querySelector('.btnup'), note=box.querySelector('.lcnote'), out=box.querySelector('.cres');
  if(!job){lab.classList.add('off'); inp.disabled=true; var nm='__KEY__'.replace('video-report-','');
    note.innerHTML='uploads work in the RackTrack app: <a class="btnapp" href="http://localhost:8600/video-report/example-'+encodeURIComponent(nm)+'/index.html#layout" target="_blank" rel="noopener">Open this report in the app ↗</a> <span class="muted">(the app must be running on this computer)</span>'; return;}
  inp.addEventListener('change',function(){var f=inp.files[0]; if(!f)return; var fd=new FormData(); fd.append('file',f); fd.append('job',job); fd.append('device',box.dataset.dev);
    out.innerHTML='<p class="muted">Reading the close-up: about 20 s, a few minutes for a model seen for the first time.</p>';
    fetch('/api/closeup',{method:'POST',body:fd}).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||j.detail||r.statusText);return j;});}).then(function(j){
      var also=(j.also||[]).map(function(i){return 'D'+i;}).join(', ');
      if(j.shows_device===false){out.innerHTML='<p>'+esc2(j.detail)+'</p>'+(j.saved_to_report?'<p class="muted">Reload this page in a minute to see the report rebuilt with it.</p>':''); return;}
      var P=j.pictures||{}, bt=j.by_type||{}, fig=function(k,c){return P[k]?'<figure class="lfig"><img src="'+esc2(P[k])+'" alt="'+c+'"><figcaption>'+c+'</figcaption></figure>':'';};
      out.innerHTML='<p><b>'+(j.vendor?esc2(j.vendor)+(j.model?' '+esc2(j.model):' (model not read)'):'make not read')+'</b> · '
        +((j.fitted||j.auto)?(j.n_ports+' ports'+(j.auto?' (auto mode, numbered from the photo)':'')+': '+Object.keys(bt).map(function(k){return bt[k]+' '+k.toUpperCase();}).join(' + ')+', '+j.connected+' with a cable'):(j.needs_back?'the ports are on the back: upload a photo of the back':'no port layout'))+'</p>'
        +'<div class="lres">'+fig('cut','Close-up photo')+fig('vendor_ports','Vendor front image, ports numbered')+fig('rack_trace','Vendor layout on the rack image')+'</div>'
        +(also?'<p class="muted">The photo also shows '+esc2(also)+': kept for '+(j.also.length>1?'them':'it')+' too.</p>':'')
        +(j.saved_to_report?'<p class="muted">Kept with this switch: reload this page in a minute to see the report rebuilt with it.</p>':'');
    }).catch(function(e){out.innerHTML='<p class="muted">Could not read it: '+esc2(e.message)+'</p>';});});});
})();
</script>
"""


def build_overview(sample="vdo1"):
    """reports/video/overview/index.html: every step of the pipeline, its model, approach, accuracy and where the rest goes."""
    r = json.load(open(ROOT / "results" / "video_experiment" / sample / "result.json"))
    body = integration_tab(r["ladder"], r["rack"], r["occupancy_frames"]["summary"], r["rack"]["occupancy"], r["devices"])
    body = body.replace("<h2>The full pipeline, step by step</h2>", "<h2>Models and accuracy, step by step</h2>")
    out = ROOT / "reports" / "video" / "overview"; out.mkdir(parents=True, exist_ok=True)
    js = ("<script>(function(){var e=false;try{e=!!(window.frameElement&&window.frameElement.id==='voverview');}catch(x){} if(e)document.documentElement.classList.add('embed');})();</script>")
    page = ('<!doctype html><meta charset="utf-8"><title>Models and Accuracy</title>' + CSS
            + '<div class="page"><div class="stick"><header><div class="brand">RackTrack</div><h1>Models and Accuracy</h1><p class="info">Every step of the rack analysis: the model, why we chose it, and how accurate it is</p></header></div>'
            + body + "</div>" + js)
    (out / "index.html").write_text(page)
    return out / "index.html"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sample", default="vdo1", choices=sorted(SAMPLES))
    args = ap.parse_args()
    out = build(args.sample); build_overview()
    print(out / "index.html", sum(f.stat().st_size for f in (out / "pics").iterdir()) // 1024, "KB of pictures")


if __name__ == "__main__":
    main()
