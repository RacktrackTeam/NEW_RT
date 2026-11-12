"""Run the pipeline on ONE photo, step by step, and save every intermediate result for the walkthrough report.

    python3 scripts/occupancy/walkthrough_data.py <photo> <out_dir>

Writes <out_dir>/img/*.jpg (one picture per step, plus every unit cut-out) and <out_dir>/data.json.
"""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))   # project root: src/occupancy.py and friends

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

import fit_advisor
import occupancy as occ
import unit_grid as ug

SRC, OUT = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
(OUT / "img").mkdir(parents=True, exist_ok=True)
VIEW_H = 900                       # height of the step pictures in the report


def font(n):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", n)
    except OSError:
        return ImageFont.load_default()


def save(im, name, h=VIEW_H):
    im = im.convert("RGB")
    if im.height > h:
        im = im.resize((round(im.width * h / im.height), h), Image.LANCZOS)
    im.save(OUT / "img" / name, quality=85)
    return f"img/{name}"


def dashed(d, box, col, w=3, dash=12):
    x1, y1, x2, y2 = box
    for xa, ya, xb, yb in ((x1, y1, x2, y1), (x2, y1, x2, y2), (x2, y2, x1, y2), (x1, y2, x1, y1)):
        L = max(abs(xb - xa), abs(yb - ya)); n = max(1, int(L // dash))
        for k in range(0, n, 2):
            t0, t1 = k / n, min(1, (k + 1) / n)
            d.line((xa + (xb - xa) * t0, ya + (yb - ya) * t0, xa + (xb - xa) * t1, ya + (yb - ya) * t1), fill=col, width=w)


def tag(d, xy, text, col, f):
    x, y = xy
    tw = d.textlength(text, font=f)
    d.rectangle((x, y, x + tw + 8, y + f.size + 6), fill=col)
    d.text((x + 4, y + 2), text, fill="white", font=f)


t0 = time.time()
img = ImageOps.exif_transpose(Image.open(SRC)).convert("RGB")
W, H = img.size
data = {"photo": SRC.name, "width": W, "height": H}
data["img_input"] = save(img, "01_input.jpg")

# ---- step 2: shrink + unit finder (raw boxes)
bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
small, factor = ug.shrink_for_inference(bgr)
data["shrink"] = {"long_side_limit": ug.INFERENCE_LONG_SIDE, "factor": factor,
                  "small_w": small.shape[1], "small_h": small.shape[0]}
model = ug.load_model(str(occ.DETECTOR))
res = model.predict(small, conf=0.25, imgsz=768, verbose=False)
raw, racks, rails = ug._unit_rows_from_result(res[0], getattr(model, "names", {}) or {})
if factor != 1:
    for u in raw:
        u["box"] = [v * factor for v in u["box"]]
    racks = [[v * factor for v in r] for r in racks]
    rails = [[v * factor for v in r] for r in rails]
kept = ug._drop_duplicate_units([dict(u) for u in raw])
heights = [u["box"][3] - u["box"][1] for u in kept]
data["raw"] = {"unit_boxes": len(raw), "after_duplicates": len(kept), "racks": len(racks), "rails": len(rails),
               "median_height": float(np.median(heights)) if heights else 0,
               "conf_min": min(u["confidence"] for u in raw), "conf_max": max(u["confidence"] for u in raw),
               "conf_mean": float(np.mean([u["confidence"] for u in raw]))}
im = img.copy(); d = ImageDraw.Draw(im); f = font(max(14, H // 70))
for r in racks:
    dashed(d, r, (124, 58, 237), w=max(3, H // 400))
for r in rails:
    d.rectangle(r, outline=(234, 88, 12), width=max(3, H // 400))
for u in sorted(raw, key=lambda u: u["box"][1]):
    d.rectangle(u["box"], outline=(14, 165, 233), width=max(2, H // 500))
    tag(d, (u["box"][2] - 70 * H / 1700, u["box"][1] + 2), f"{u['confidence']:.2f}", (14, 116, 144), font(max(12, H // 90)))
data["img_raw"] = save(im, "02_raw_boxes.jpg")

# ---- step 3: the ladder
units = occ.detect_units(img)
data["units_total"] = len(units)
data["units_detected"] = sum(u["source"] == "detected" for u in units)
data["units_inferred"] = len(units) - data["units_detected"]
im = img.copy(); d = ImageDraw.Draw(im); f = font(max(13, H // 80))
for u in units:
    col = (37, 99, 235) if u["source"] == "detected" else (219, 39, 119)
    if u["source"] == "detected":
        d.rectangle(u["box"], outline=col, width=max(2, H // 450))
    else:
        dashed(d, u["box"], col, w=max(3, H // 400))
    tag(d, (u["box"][0] + 4, u["box"][1] + 2), u["unit"].upper(), col, f)
data["img_ladder"] = save(im, "03_ladder.jpg")

# ---- step 4: crops
for u in units:
    occ.crop_unit(img, u["box"]).save(OUT / "img" / f"crop_{u['unit']}.jpg", quality=85)
    s = occ.row_strip(img, u["box"]); s.thumbnail((900, 160))
    s.save(OUT / "img" / f"strip_{u['unit']}.jpg", quality=85)

# ---- step 5: classifiers (Ensemble = mean of six)
apps = {a.key: a for a in occ.build_approaches("v2")}
ens = apps["ensemble"]
crops = [occ.crop_unit(img, u["box"]) for u in units]
member = {}
for m in ens.members:
    if m.available():
        member[m.key] = [float(v) for v in m.predict(crops)]
        m.unload(); occ.free_memory()
names = {"yolov8m_cls": "YOLOv8m", "yolo11s_cls": "YOLO11s", "embed_dinov2": "DINOv2", "embed_clip": "CLIP",
         "teammate": "ResNet50", "classical": "Classical"}
data["members"] = [{"key": k, "name": names.get(k, k), "accuracy": apps[k].metrics()["val_accuracy"]} for k in member]

# ---- step 6: device finder
devices = occ.detect_devices(img)
data["devices"] = [{"class": dv["class_name"], "conf": dv["confidence"], "trusted": dv["confidence"] >= occ.DEVICE_TRUST}
                   for dv in devices]
im = img.copy(); d = ImageDraw.Draw(im); f = font(max(13, H // 80))
for dv in sorted(devices, key=lambda v: v["confidence"]):
    trusted = dv["confidence"] >= occ.DEVICE_TRUST
    col = (147, 51, 234) if dv["class_name"].lower() != "empty" else (5, 150, 105)
    if trusted:
        d.rectangle(dv["box"], outline=col, width=max(3, H // 400))
    else:
        dashed(d, dv["box"], (148, 163, 184), w=max(2, H // 600))
    if trusted:
        tag(d, (dv["box"][2] - d.textlength(f"{dv['class_name']} {dv['confidence']:.2f}", font=f) - 12, dv["box"][1] + 2),
            f"{dv['class_name']} {dv['confidence']:.2f}", col, f)
data["img_devices"] = save(im, "05_devices.jpg")
data["device_settings"] = {"conf": occ.DEVICE_CONF, "trust": occ.DEVICE_TRUST, "row_cover": occ.ROW_COVER}

# ---- step 7: decision per unit (same call the app makes)
rows = occ.classify_units(img, units, ens, devices=devices)
summary = occ.summarize(rows)
unit_rows = []
for i, r in enumerate(rows):
    dv = occ._row_device(r, devices)
    unit_rows.append({
        "unit": r["unit"], "source": r["source"], "det_conf": r["det_conf"],
        "members": {k: member[k][i] for k in member}, "p_occupied": r["p_occupied"],
        "leaning": r["leaning"], "confidence": r["confidence"],
        "device": dv["class_name"] if dv else None, "device_conf": dv["confidence"] if dv else None,
        "status": r["status"], "decided_by": r["decided_by"], "reason": r["reason"], "attention": r["attention"],
        "crop": f"img/crop_{r['unit']}.jpg", "strip": f"img/strip_{r['unit']}.jpg"})
data["rows"] = unit_rows
data["summary"] = summary
data["uncertain_below"] = occ.UNCERTAIN_BELOW
data["img_final"] = save(occ.annotate(img, rows), "06_final.jpg")

# ---- step 3: group units into devices (teammate's pair model, src/device_grouping.py)
import device_grouping
grp = device_grouping.DeviceGrouper().group(img, rows)
im = img.copy(); d = ImageDraw.Draw(im, "RGBA"); f = font(max(14, H // 70))
STATUS_RGB = {"occupied": (209, 69, 59), "unoccupied": (36, 147, 90), "uncertain": (212, 139, 11)}
for r in rows:
    c = STATUS_RGB[r["status"]]
    d.rectangle(r["box"], fill=c + (40,), outline=c + (150,), width=1)
for dv in grp["devices"]:
    col = (24, 32, 42) if dv["status"] == "confirmed" else (212, 139, 11)
    if dv["status"] == "confirmed":
        d.rectangle(dv["box"], outline=col, width=max(4, H // 300))
    else:
        dashed(d, dv["box"], col, w=max(4, H // 300))
    lab = f"D{dv['id']} · {dv['height_u']}U"
    tag(d, (dv["box"][2] - d.textlength(lab, font=f) - 14, dv["box"][1] + 3), lab, col, f)
data["img_devices_grouped"] = save(im, "08_device_groups.jpg")
data["groups"] = {"devices": grp["devices"], "pairs": grp["pairs"], "same_thr": grp["same_thr"]}

# ---- step 8: free space + fit a device
fs = fit_advisor.free_space_summary(rows)
data["free_runs"] = [{"range": r["range_label"], "size": r["size"]} for r in fs["free_runs"]]
fit = fit_advisor.advise(rows, "Dell", "PowerEdge R750")
data["fit"] = {"device": "Dell PowerEdge R750", "units": fit.get("units_needed") or fit.get("units"),
               "suggestions": [{"range": s["range_label"], "reason": s["reason"]} for s in fit.get("suggestions", [])][:3],
               "message": fit.get("message", "")}
if fit.get("suggestions"):
    data["img_fit"] = save(occ.annotate(img, rows, fit=fit["suggestions"][0]["bbox"]), "07_fit.jpg")
data["seconds"] = round(time.time() - t0, 1)
json.dump(data, open(OUT / "data.json", "w"), indent=1, default=float)
print("done", data["units_total"], "units", data["seconds"], "s", json.dumps(data["fit"])[:300])
