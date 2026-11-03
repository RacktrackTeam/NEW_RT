"""Step 3 examples: what device grouping gets right and wrong, with pictures, and how often each case happens.

    python3 scripts/devices/step3_examples.py [max_images]

Runs the app's pipeline on the labelled test photos (same grading as scripts/devices/eval_step3.py), sorts every real
device into one case, and draws a few examples of each: hand-labelled devices on the left, the app's on the right.
Writes data/eval/step3_examples/{*.jpg, examples.json}.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from PIL import Image, ImageDraw, ImageFont, ImageOps

import device_grouping
import occupancy as occ
from eval_step3 import COVER, DATA, NAMES, cover, load_gt

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "eval" / "step3_examples"   # draw_example() writes here
PER_CASE = 3
CASES = {
    "tall_ok":   "Tall device kept in one piece",
    "rack_ok":   "Whole rack right",
    "check_ok":  "Right, but flagged for a check",
    "split":     "Tall device split into pieces",
    "merged":    "Two devices joined into one",
    "missed":    "Device missed (units called free)",
    "phantom":   "Free unit counted as part of a device",
}


def font(n):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", n)
    except OSError:
        return ImageFont.load_default()


def label(d, xy, text, col, f):
    x, y = xy
    w = d.textlength(text, font=f)
    d.rectangle((x, y, x + w + 10, y + f.size + 8), fill=col)
    d.text((x + 5, y + 3), text, fill="white", font=f)


def panel(img, crop, boxes, title):
    """boxes: [(box, text, colour, dashed, highlight)] in image pixels."""
    x0, y0, x1, y1 = crop
    im = img.crop(crop).convert("RGB")
    s = 460 / max(1, im.width)
    im = im.resize((460, max(1, round(im.height * s))), Image.LANCZOS)
    d = ImageDraw.Draw(im, "RGBA")
    f = font(14)
    for box, text, col, dashed, hi in boxes:
        b = [(box[0] - x0) * s, (box[1] - y0) * s, (box[2] - x0) * s, (box[3] - y0) * s]
        w = 5 if hi else 2
        if hi:
            d.rectangle(b, fill=col + (45,))
        if dashed:
            for k in range(0, int(b[2] - b[0]), 14):
                d.line((b[0] + k, b[1], min(b[2], b[0] + k + 7), b[1]), fill=col, width=w)
                d.line((b[0] + k, b[3], min(b[2], b[0] + k + 7), b[3]), fill=col, width=w)
            d.line((b[0], b[1], b[0], b[3]), fill=col, width=w); d.line((b[2], b[1], b[2], b[3]), fill=col, width=w)
        else:
            d.rectangle(b, outline=col, width=w)
        if text and (hi or b[3] - b[1] > 18):
            label(d, (b[2] - d.textlength(text, font=f) - 14, b[1] + 2), text, col, f)
    head = Image.new("RGB", (460, 30), (245, 246, 248))
    ImageDraw.Draw(head).text((8, 7), title, fill=(24, 32, 42), font=font(15))
    out = Image.new("RGB", (460, im.height + 30), "white")
    out.paste(head, (0, 0)); out.paste(im, (0, 30))
    return out


def draw_example(img, rows, gt, truth, gt_dev, pred, focus_gt, focus_pred, name):
    ys = [rows[k]["box"][1] for k in range(len(rows))] + [rows[k]["box"][3] for k in range(len(rows))]
    xs = [rows[k]["box"][0] for k in range(len(rows))] + [rows[k]["box"][2] for k in range(len(rows))]
    involved = [k for o in focus_gt for k in gt_dev.get(o, ())] + [k for p in focus_pred for k in pred[p]["ks"]]
    if involved:
        unit_h = (max(ys) - min(ys)) / max(1, len(rows))
        top = min(rows[k]["box"][1] for k in involved) - 4 * unit_h
        bot = max(rows[k]["box"][3] for k in involved) + 4 * unit_h
    else:
        top, bot = min(ys), max(ys)
    crop = (int(max(0, min(xs) - 10)), int(max(0, top)), int(min(img.width, max(xs) + 10)), int(min(img.height, bot)))
    left = []
    for o, ks in gt_dev.items():
        b = [min(rows[k]["box"][0] for k in ks), min(rows[k]["box"][1] for k in ks),
             max(rows[k]["box"][2] for k in ks), max(rows[k]["box"][3] for k in ks)]
        left.append((b, f"{NAMES.get(gt[o][4], '?')} · {len(ks)}U", (22, 130, 70), False, o in focus_gt))
    right = []
    for pid, p in pred.items():
        col = (24, 32, 42) if p["status"] == "confirmed" else (200, 120, 0)
        right.append((p["box"], f"D{pid} · {p['h']}U", col, p["status"] != "confirmed", pid in focus_pred))
    for k, r in enumerate(rows):
        if r["status"] == "unoccupied" and truth[k][0] == "occupied" and any(k in gt_dev.get(o, ()) for o in focus_gt):
            right.append((r["box"], "called free", (36, 147, 90), True, True))
    a = panel(img, crop, left, "Hand-labelled (truth)")
    b = panel(img, crop, right, "Our app")
    out = Image.new("RGB", (a.width + b.width + 12, max(a.height, b.height)), "white")
    out.paste(a, (0, 0)); out.paste(b, (a.width + 12, 0))
    out.save(OUT / name, quality=84)


def main(limit=None):
    OUT.mkdir(parents=True, exist_ok=True)
    imgs = sorted(p for p in (DATA / "images").iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if limit:
        imgs = imgs[:int(limit)]
    ens = next(a for a in occ.build_approaches("v2") if a.key == "ensemble")
    grouper = device_grouping.DeviceGrouper()
    counts = {c: 0 for c in CASES}
    devices_total = 0
    examples = {c: [] for c in CASES}
    for n, path in enumerate(imgs, 1):
        lbl = DATA / "labels" / f"{path.stem}.txt"
        if not lbl.exists():
            continue
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        units = occ.detect_units(img)
        if not units:
            continue
        rows = occ.classify_units(img, units, ens, devices=occ.detect_devices(img))
        g = grouper.group(img, rows)
        gt = load_gt(lbl, *img.size)
        truth = []
        for r in rows:
            cv = [(cover(r["box"], o), k) for k, o in enumerate(gt)]
            best = max(cv) if cv else (0, -1)
            truth.append((("free" if gt[best[1]][4] in (0, 1) else "occupied"), best[1]) if best[0] >= COVER else (None, -1))
        idx = {r["unit"]: k for k, r in enumerate(rows)}
        graded = {k for k, t in enumerate(truth) if t[0] is not None}
        gt_dev = {}
        for k in graded:
            if truth[k][0] == "occupied":
                gt_dev.setdefault(truth[k][1], set()).add(k)
        pred = {}
        for d in g["devices"]:
            pred[d["id"]] = {"ks": {idx[u] for u in d["units"]}, "status": d["status"], "box": d["box"], "h": d["height_u"]}
        unit_pred = {k: pid for pid, p in pred.items() for k in p["ks"]}
        found = []        # (case, focus_gt, focus_pred)
        for o, ks in gt_dev.items():
            devices_total += 1
            pids = {unit_pred.get(k) for k in ks}
            pg = {k for k in pred[next(iter(pids))]["ks"] if k in graded} if len(pids) == 1 and None not in pids else None
            if pg is not None and pg == ks:
                p = next(iter(pids))
                case = "check_ok" if pred[p]["status"] == "check" else ("tall_ok" if len(ks) >= 2 else None)
                if case:
                    found.append((case, [o], [p]))
                continue
            if None in pids:
                found.append(("missed", [o], [p for p in pids if p is not None]))
            elif len(pids) > 1:
                found.append(("split", [o], list(pids)))
            else:
                p = next(iter(pids))
                others = {truth[k][1] for k in pred[p]["ks"] & graded if truth[k][0] == "occupied"} - {o}
                found.append(("merged", [o] + sorted(others), [p]) if others else ("phantom", [o], [p]))
        if gt_dev and all(c in ("tall_ok", "check_ok") for c, _, _ in found) and \
                sum(1 for o, ks in gt_dev.items() for k in ks) >= 6 and \
                all({k for k in pred[unit_pred[next(iter(ks))]]["ks"] if k in graded} == ks
                    for ks in gt_dev.values() if unit_pred.get(next(iter(ks))) is not None) and \
                all(unit_pred.get(k) is not None for ks in gt_dev.values() for k in ks):
            found.append(("rack_ok", list(gt_dev), list(pred)))
        for case, fg, fp in found:
            counts[case] += case != "rack_ok"
            if len(examples[case]) < PER_CASE:
                if case in ("split", "tall_ok", "check_ok") and not (2 <= len(gt_dev[fg[0]]) <= 5):
                    continue
                name = f"{case}_{len(examples[case]) + 1}.jpg"
                draw_example(img, rows, gt, truth, gt_dev, pred, [] if case == "rack_ok" else fg,
                             [] if case == "rack_ok" else fp, name)
                examples[case].append({"img": f"/static/step3_examples/{name}", "photo": path.name,
                                       "units": sorted((rows[k]["unit"].upper() for o in fg for k in gt_dev.get(o, ())),
                                                       key=lambda u: -int(u[1:]))})
        if n % 50 == 0:
            print(n, "photos", {c: len(v) for c, v in examples.items()}, flush=True)
    counts["rack_ok"] = sum(1 for _ in examples["rack_ok"])
    out = {"devices": devices_total, "counts": counts, "titles": CASES, "examples": examples, "photos": len(imgs)}
    (OUT / "examples.json").write_text(json.dumps(out, indent=1))
    print(json.dumps({"devices": devices_total, "counts": counts}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:2])
