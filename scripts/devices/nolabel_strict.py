"""Every device in the merged dataset that the network team did not label (the No label list).

Over every photo of the dataset, a device is listed when every test passes:
  1. the team's DEIMv2 device detector (src/device_model.py, the "logic + model" approach) boxes it, score >= 0.5 (one box
     per device); the evidence says whether our RT-EOS device finder agrees: "both sure" (finder >= 80 % on the same spot),
     "both see it" (50-80 %) or "one detector" (finder lower or silent);
  3. it sits on the rack's unit ladder (our unit logic, results/devices/rows_all.json): it covers at least 40 % of one
     unit row and lies at least half inside the ladder's width, so other racks, monitors and the floor drop out;
  4. the network team's label POLYGONS cover less than 10 % of it, in our dataset's label file and in the original export
     label file (provenance/export_labels.zip);
  5. no label of any kind reaches into it: the box of every label line (polygon or plain box, any class, both files) covers
     less than 30 % of it and overlaps it by less than 30 %, so a label drawn slanted or shifted never counts as missing.
DEIMv2 boxes are cached in results/devices/devmodel_all.json.

    python3 scripts/devices/nolabel_strict.py      # -> results/devices/nolabel_strict.json
"""
import csv, json, pathlib, sys, time, zipfile

import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts" / "devices"))
import audit_labels as AL   # noqa: E402

RES = ROOT / "results" / "devices"
CACHE = RES / "devmodel_all.json"
DEIM_SCORE, MATCH_IOU, ROW_COVER, LADDER_IN, POLY_MAX, BOX_NEAR = 0.5, 0.5, 0.4, 0.5, 0.10, 0.30


def iou(a, b):
    return AL.iou(a, b)[0]


def poly_mask(lines, w, h):
    """Mask of the polygon lines (class x y x y ...); 5-value box lines are skipped."""
    m = Image.new("L", (w, h), 0); d = ImageDraw.Draw(m)
    for v in lines:
        xy = [float(t) for t in v[1:]]
        if len(xy) < 6:
            continue
        d.polygon([(xy[i] * w, xy[i + 1] * h) for i in range(0, len(xy) - 1, 2)], fill=255)
    return np.asarray(m) > 0


def cover(mask, b, w, h):
    x0, y0, x1, y1 = int(max(0, b[0])), int(max(0, b[1])), int(min(w, b[2])), int(min(h, b[3]))
    return float(mask[y0:y1, x0:x1].mean()) if x1 > x0 and y1 > y0 else 1.0


def label_boxes(lines, w, h):
    """The box of every label line (polygon or plain box), any class."""
    out = []
    for v in lines:
        xy = [float(t) for t in v[1:]]
        if len(xy) == 4:
            xy = [xy[0] - xy[2] / 2, xy[1] - xy[3] / 2, xy[0] + xy[2] / 2, xy[1] + xy[3] / 2]
        if len(xy) >= 4:
            out.append((min(xy[0::2]) * w, min(xy[1::2]) * h, max(xy[0::2]) * w, max(xy[1::2]) * h))
    return out


def near(b, g):
    """Share of the place covered by a label box, and their overlap (IoU); the larger of the two."""
    i = max(0, min(b[2], g[2]) - max(b[0], g[0])) * max(0, min(b[3], g[3]) - max(b[1], g[1]))
    a = (b[2] - b[0]) * (b[3] - b[1]); u = a + (g[2] - g[0]) * (g[3] - g[1]) - i
    return max(i / max(1e-6, a), i / u if u else 0)


def main():
    import torch
    torch.set_num_threads(2)
    import device_model as DM
    info = {n: (p, lbl, w, h) for n, p, lbl, w, h in AL.photos(AL.SPLITS[0])}
    F = json.load(open(RES / f"audit_finder_{AL.SPLITS[0]}.json"))["photos"]
    rows = {pathlib.Path(r["file"]).name: r["rows"] for r in json.load(open(RES / f"rows_{AL.SPLITS[0]}.json"))}
    man = {r["photo"]: r for r in csv.DictReader(open(AL.DATA / "manifest.csv"))}
    z = zipfile.ZipFile(AL.DATA / "provenance" / "export_labels.zip"); zn = z.namelist()
    cache = json.load(open(CACHE)) if CACHE.exists() else {}
    todo = sorted(set(info) - set(cache)); t0 = time.time()
    print(f"detector to run on {len(todo):,} photos", flush=True)
    for k, name in enumerate(todo, 1):
        img = ImageOps.exif_transpose(Image.open(info[name][0])).convert("RGB")
        cache[name] = [[round(v, 1) for v in b[:4]] + [round(b[4], 3)] for b in DM.detect(img)]
        if k % 100 == 0:
            json.dump(cache, open(CACHE, "w")); print(f"detector {k}/{len(todo)}  {(time.time() - t0) / k:.1f}s/photo", flush=True)
    json.dump(cache, open(CACHE, "w"))
    n1 = n2 = n3 = 0; kept = []
    for name, (p, lbl, w, h) in info.items():
        ds = sorted([d for d in cache.get(name, []) if d[4] >= DEIM_SCORE], key=lambda d: -d[4]); keep = []
        for d in ds:                                                # one box per device
            if all(iou(d, k) <= 0.5 for k in keep):
                keep.append(d)
        if not keep:
            continue
        n1 += len(keep)
        lad = rows.get(name, [])
        if not lad:
            continue
        lx0 = min(r["box"][0] for r in lad); lx1 = max(r["box"][2] for r in lad)
        ours = [l.split() for l in lbl.read_text().split("\n") if l.strip()]; mo = None; me = None
        for d in keep:
            if max(0, min(d[2], lx1) - max(d[0], lx0)) / max(1, d[2] - d[0]) < LADDER_IN:
                continue
            units = [r["unit"] for r in lad if max(0, min(d[3], r["box"][3]) - max(d[1], r["box"][1])) / max(1, r["box"][3] - r["box"][1]) >= ROW_COVER]
            if not units:
                continue
            n2 += 1
            if mo is None:
                m_ = man[name]; q = pathlib.PurePosixPath(m_["export_image"])
                key = next((k for k in zn if k.startswith(m_["export"]) and k.endswith(f"labels/{q.stem}.txt")), None)
                ex = [l.split() for l in z.read(key).decode().split("\n") if l.strip()] if key else []
                mo, me = poly_mask(ours, w, h), poly_mask(ex, w, h); nex = sum(1 for v in ex if len(v) != 5)
                lb = label_boxes(ours, w, h) + label_boxes(ex, w, h)
            co, ce = cover(mo, d, w, h), cover(me, d, w, h)
            if co >= POLY_MAX or ce >= POLY_MAX:
                continue
            n3 += 1
            if max((near(d, g) for g in lb), default=0) >= BOX_NEAR:      # a label of any kind reaches into it: not sure it is missing
                continue
            fb = max(((iou(d, f), f) for f in F.get(name, []) if f[5] >= 2), key=lambda t: t[0], default=(0, None))
            fconf = fb[1][4] if fb[0] >= MATCH_IOU else 0.0
            ev = "both sure" if fconf >= AL.FINDER_SURE else "both see it" if fconf >= 0.5 else "one detector"
            kept.append({"photo": name, "box": [round(v, 1) for v in d[:4]], "deim_score": round(d[4], 3), "finder_conf": round(fconf, 3),
                         "evidence": ev, "units": units, "export": man[name]["export"], "megapixels": round(w * h / 1e6, 2),
                         "label_lines": len(ours), "export_polygon_lines": nex, "polygon_cover": round(co, 3), "export_polygon_cover": round(ce, 3)})
    import collections
    ev = collections.Counter(k["evidence"] for k in kept)
    stages = [["the DEIMv2 device detector finds a device (score at least 50 %)", n1],
              ["it sits on the rack's unit ladder", n2],
              ["no label polygon covers 10 % of it, in our file and in the export file", n3],
              ["no label of any kind reaches into it: no label box covers 30 % of it or overlaps it by 30 %", len(kept)]]
    json.dump({"rules": {"finder_sure": AL.FINDER_SURE, "deim_score": DEIM_SCORE, "match_iou": MATCH_IOU, "row_cover": ROW_COVER,
                         "ladder_in": LADDER_IN, "polygon_max": POLY_MAX, "box_near": BOX_NEAR},
               "photos": len(info), "stages": stages, "evidence": dict(ev), "kept": kept}, open(RES / "nolabel_strict.json", "w"), indent=1)
    print(f"{len(info):,} photos; " + " -> ".join(f"{n:,}" for _, n in stages) + f" kept in {len({k['photo'] for k in kept}):,} photos; {dict(ev)}")


if __name__ == "__main__":
    main()
