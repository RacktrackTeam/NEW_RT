"""Audit the labels of the one device dataset, "data/datasets/devices/" (built on 2026-10-19 from the network team's seven
Roboflow exports: images/, labels/, data.yaml, manifest.csv, provenance/), and build the review pages for people to check them.

    python3 scripts/devices/audit_labels.py run                      # everything, incrementally: the model's boxes for photos not
                                                                     # cached yet, every check, and the review pages
    python3 scripts/devices/audit_labels.py watch [minutes]          # run, then run again whenever a label file changes
    python3 scripts/devices/audit_labels.py cache all                # the RT-EOS device finder's boxes on every photo
                                                                     # -> results/devices/audit_finder_all.json
    python3 scripts/devices/audit_labels.py check                    # every check on every photo -> results/devices/label_audit.json
    python3 scripts/devices/audit_labels.py verify                   # every image against manifest.csv and every label file against
                                                                     # the original export labels in provenance/export_labels.zip
    python3 scripts/devices/audit_labels.py page [parts]             # the review pages -> reports/devices/label_audit/class<n>/,
                                                                     # one per reviewer, whole photos dealt to each

The review covers photos of 1 MP or more; each label instance is one card in one of four tabs (to check, not confirmed, no
label, 100 % correct). results/devices/label_audit_state.json remembers the label files seen by the last run, so `run` and
`watch` report what changed.

Checks (label_audit.json lists every flag with the photo, the label line and the reason; the page and the CSV show only
PAGE_CHECKS):
    span           the units a label covers (a unit belongs to the label covering at least 40 % of it, on our unit ladder,
                   results/devices/rows_<split>.json) differ from the units our model's device box covers there; or the
                   model sees nothing at all under a label (not even a faint box); or the model is at least 80 % sure of
                   a device where nothing is labelled. The model's boxes are the RT-EOS device finder's at 50 % confidence
                   or more, their class ignored (Empty and Closed Unit boxes are not devices). Classes are never compared.
    format         the label line itself is broken: an unknown class id, a coordinate outside the photo, a degenerate
                   polygon (no area), or a wrong number of values
    duplicate      two device labels of different classes on one spot (IoU over 0.5); the same class twice (a box next to a
                   polygon, or the device drawn twice) is not flagged (the user, 2026-10-21)
    contradiction  a device label over an Empty or Closed Unit label (over half of the smaller box)
    class          the class second opinion (the RT-EOS device finder, weights/device_finder.pt, used here only to point
                   at labels to check, not in the pipeline) is at least FINDER_SURE sure of a different class on the same box
    missing        the finder is at least FINDER_SURE sure of a device where no label is
    phantom        a device label where the finder sees nothing at all (no box at FINDER_ANY over the label)
"""
import ast
import collections
import csv
import hashlib
import json
import pathlib
import re
import sys
import time

import numpy as np
from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
RES = ROOT / "results" / "devices"
DATA = ROOT / "data" / "datasets" / "devices"      # the device dataset: images/ + labels/ in one folder (the user, 2026-10-19)
SPLITS = ("all",)                              # one split; a dataset with train/valid/test folders would list them here
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp")   # .avif photos are skipped: the image library here cannot open them
FINDER_SURE, FINDER_ANY = 0.8, 0.1


def photos(split):
    """(name, image path, label path, width, height) for every labelled photo of a split; the size as the photo is shown
    (EXIF orientation applied), which is what the labels and the finder boxes refer to."""
    out = []
    base = DATA / split if (DATA / split / "images").exists() else DATA
    for p in sorted((base / "images").iterdir()):
        if p.suffix.lower() not in IMAGE_TYPES or p.name.startswith("._"):
            continue
        lbl = base / "labels" / (p.stem + ".txt")
        if not lbl.exists():
            continue
        with Image.open(p) as im:
            w, h = im.size
            if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):
                w, h = h, w
        out.append((p.name, p, lbl, w, h))
    return out


def cache(split):
    """Unit rows (results/devices/rows_<split>.json) and the RT-EOS device finder's boxes (audit_finder_<split>.json) for
    every photo of the split; photos already in a cache are not computed again."""
    import unit_grid as ug
    import occupancy as occ
    from eval_step3 import COVER as ROW_COVER, cover, load_gt
    from tune_device_logic import load_bgr
    import torch
    torch.set_num_threads(2)
    ph = photos(split)
    rows_fp = RES / f"rows_{split}.json"
    rows = {pathlib.Path(r["file"]).name: r for r in json.load(open(rows_fp))} if rows_fp.exists() else {}
    todo = [t for t in ph if t[0] not in rows]
    if todo:
        print(f"{split}: unit rows for {len(todo)} photos", flush=True)
        for n, (name, path, lbl, w, h) in enumerate(todo, 1):
            img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            units = occ.detect_units(img); gt = load_gt(lbl, *img.size); truth = []
            for r in units:
                cv = [(cover(r["box"], o), k) for k, o in enumerate(gt)]; best = max(cv) if cv else (0, -1)
                truth.append([("free" if gt[best[1]][4] in (0, 1) else "occupied"), best[1]] if best[0] >= ROW_COVER else [None, -1])
            rows[name] = {"file": str(path), "width": img.width, "height": img.height,
                          "rows": [{"unit": r["unit"], "box": [float(v) for v in r["box"]], "source": r.get("source")} for r in units],
                          "gt": [[float(v) for v in o[:4]] + [int(o[4])] for o in gt], "truth": truth}
            if n % 50 == 0:
                print(f"{split} rows {n}/{len(todo)}", flush=True)
    names_now = {t[0] for t in ph}
    json.dump([dict(rows[n], file=str(path)) for n, path, *_ in ph if n in rows], open(rows_fp, "w"))
    fp = RES / f"audit_finder_{split}.json"
    out = json.load(open(fp))["photos"] if fp.exists() else {}
    out = {k: v for k, v in out.items() if k in names_now}
    model = ug.load_model(str(ROOT / "weights" / "device_finder.pt")); names = model.names
    t0 = time.time(); done = 0
    for name, path, lbl, w, h in ph:
        if name in out:
            continue
        bgr = load_bgr(path); small, f = ug.shrink_for_inference(bgr)
        r = model.predict(small, conf=FINDER_ANY, iou=0.5, verbose=False)[0]
        out[name] = [[round(v * f, 1) for v in b] + [round(float(s), 3), int(c)] for b, c, s in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist())]
        done += 1
        if done % 100 == 0:
            print(f"{split} finder {done}  {(time.time() - t0) / done:.2f}s/photo", flush=True)
            json.dump({"names": names, "photos": out}, open(fp, "w"), separators=(",", ":"))
    json.dump({"names": names, "photos": out}, open(fp, "w"), separators=(",", ":"))
    print(f"written {fp}: {len(out)} photos")


def load_shapes(lbl, w, h):
    """The label file as drawn: [(points [(x, y), ...] in pixels, class id)], one per line; a 4-value line is a box."""
    out = []
    for row in open(lbl).read().split("\n"):
        v = row.split()
        if not v:
            continue
        try:
            c, xy = int(v[0]), [float(x) for x in v[1:]]
        except ValueError:
            out.append(([], -1)); continue
        if len(xy) == 4:
            cx, cy, bw, bh = xy; xy = [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2, cx - bw / 2, cy + bh / 2]
        out.append(([(xy[i] * w, xy[i + 1] * h) for i in range(0, len(xy) - 1, 2)], c))
    return out


def iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1])); inter = ix * iy
    a1 = (a[2] - a[0]) * (a[3] - a[1]); a2 = (b[2] - b[0]) * (b[3] - b[1])
    return inter / max(1e-6, a1 + a2 - inter), inter / max(1e-6, min(a1, a2))


def format_flags(lbl, w, h):
    """The label lines that are broken in themselves: (line index, reason)."""
    out = []
    for i, row in enumerate(open(lbl).read().split("\n")):
        v = row.split()
        if not v:
            continue
        try:
            c = int(v[0]); xy = [float(x) for x in v[1:]]
        except ValueError:
            out.append((i, "not a number in the line")); continue
        if not 0 <= c <= 11:
            out.append((i, f"unknown class id {c}"))
        if len(xy) < 4 or len(xy) % 2 == 1:
            out.append((i, f"{len(xy)} values after the class: not a box and not x,y pairs")); continue
        if min(xy) < -0.002 or max(xy) > 1.002:
            out.append((i, "a coordinate outside the photo"))
        if len(xy) == 4:
            area = xy[2] * xy[3]
        else:
            pts = [(xy[k], xy[k + 1]) for k in range(0, len(xy) - 1, 2)]
            area = abs(sum(pts[k][0] * pts[(k + 1) % len(pts)][1] - pts[(k + 1) % len(pts)][0] * pts[k][1] for k in range(len(pts)))) / 2
        if area * w * h < 4:
            out.append((i, "no area: a degenerate label"))
    return out


MODEL_CONF, COVER = 0.5, 0.4     # a model box counts from this confidence; a unit belongs to the box covering this share of it


def span_flags(split, name, rows, gt, F, NAMES):
    """Every label whose units differ from the units of our model's device there, and every model device over units
    nothing is labelled on. Units: a unit belongs to the single label (or model box) covering at least COVER of it."""
    from eval_step3 import cover
    boxes = [f for f in F if f[4] >= MODEL_CONF and f[5] >= 2]

    def owners(cands):
        out = {}
        for k, r in enumerate(rows):
            best = max(((cover(r["box"], c), j) for j, c in cands), default=(0.0, -1))
            if best[0] >= COVER:
                out.setdefault(best[1], set()).add(k)
        return out
    dev_lbl = {o: ks for o, ks in owners([(o, g) for o, g in enumerate(gt)]).items() if gt[o][4] >= 2}
    dev_mod = owners([(j, b) for j, b in enumerate(boxes)])
    seen_any = set().union(*owners([(j, f) for j, f in enumerate(F) if f[5] >= 2]).values()) if F else set()   # units under any device box, even a faint one
    unit = lambda k: str(rows[k]["unit"]).upper()
    span = lambda ks: (f"{len(ks)} unit" + ("s" if len(ks) != 1 else "")) + (f" ({unit(max(ks))} to {unit(min(ks))})" if len(ks) > 1 else f" ({unit(min(ks))})")
    out, used = [], set()
    for o, S in sorted(dev_lbl.items(), key=lambda kv: min(kv[1])):
        overl = [j for j, T in dev_mod.items() if S & T]
        used.update(overl)
        if len(overl) == 1 and dev_mod[overl[0]] == S:
            continue
        label = f"a device of {span(S)}"
        if not overl:
            if S & seen_any:
                continue                      # the model saw something there, just not surely: not a clear disagreement
            model, conf = "no device here", 0.0
        else:
            overl.sort(key=lambda j: min(dev_mod[j]))
            model = ("a device of " if len(overl) == 1 else f"{len(overl)} devices of ") + " + ".join(span(dev_mod[j]) for j in overl)
            conf = max(boxes[j][4] for j in overl)
        out.append(dict(split=split, photo=name, check="span", boxes=[o], classes=[NAMES.get(gt[o][4], "?")], finder=[boxes[j][:4] for j in overl],
                        label=label, model=model, conf=round(float(conf), 2), detail=f"label: {label}; model: {model}"))
    for j, T in sorted(dev_mod.items(), key=lambda kv: min(kv[1])):
        if j in used or boxes[j][4] < FINDER_SURE:
            continue                          # an unlabelled device only counts when the model is sure of it
        model = f"a device of {span(T)}"
        out.append(dict(split=split, photo=name, check="span", boxes=[], classes=[], finder=[boxes[j][:4]], label="nothing labelled here", model=model,
                        conf=round(float(boxes[j][4]), 2), detail=f"label: nothing; model: {model}"))
    return out


def check():
    import csv
    from datetime import date
    from eval_step3 import NAMES, load_gt
    flags, summary = [], {}
    for split in SPLITS:
        fp = RES / f"audit_finder_{split}.json"
        finder = json.load(open(fp))["photos"] if fp.exists() else {}
        rows_fp = RES / f"rows_{split}.json"
        rows_by = {pathlib.Path(r["file"]).name: r["rows"] for r in json.load(open(rows_fp))} if rows_fp.exists() else {}
        cnt = {k: 0 for k in ("span", "format", "duplicate", "contradiction", "class", "agree", "missing", "phantom")}
        n_ph = n_box = n_dev = n_finder = n_rows = 0
        for name, path, lbl, w, h in photos(split):
            for i, why in format_flags(lbl, w, h):
                cnt["format"] += 1; flags.append(dict(split=split, photo=name, check="format", boxes=[i], classes=[], detail=why))
            gt = load_gt(lbl, w, h); n_ph += 1
            lens = [len(r.split()) for r in open(lbl).read().split("\n") if r.strip()]
            twin = {i for i, g in enumerate(gt) if i < len(lens) and lens[i] == 5 and g[4] >= 2
                    and any(lens[j] > 5 and gt[j][4] == g[4] and iou(g, gt[j])[0] > 0.5 for j in range(len(gt)))}
            # some exports label every device twice, as a polygon and as a box on the same spot: the box is not a second device
            rows = rows_by.get(name)
            if rows is not None and len(rows) >= 2 and name in finder:
                n_rows += 1
                for fl in span_flags(split, name, rows, gt, finder[name], NAMES):
                    cnt["span"] += 1; flags.append(fl)
            n_box += len(gt); n_dev += sum(g[4] >= 2 for i, g in enumerate(gt) if i not in twin)
            cls = lambda g: NAMES.get(g[4], str(g[4]))
            for i, g in enumerate(gt):
                if g[4] < 2 or i in twin:
                    continue
                for j in range(i + 1, len(gt)):
                    g2 = gt[j]; io, frac = iou(g, g2)
                    if g2[4] >= 2 and j not in twin and io > 0.5 and g2[4] != g[4]:
                        cnt["duplicate"] += 1; flags.append(dict(split=split, photo=name, check="duplicate", boxes=[i, j], classes=[cls(g), cls(g2)], detail=f"two device labels on one spot, overlap {io:.2f}"))
                for j, g2 in enumerate(gt):
                    if g2[4] < 2 and iou(g, g2)[1] > 0.5:
                        cnt["contradiction"] += 1; flags.append(dict(split=split, photo=name, check="contradiction", boxes=[i, j], classes=[cls(g), cls(g2)], detail=f"a {cls(g)} label over a {cls(g2)} label"))
            if name in finder:
                n_finder += 1
                F = finder[name]
                for i, g in enumerate(gt):
                    if i in twin:
                        continue
                    best = max(((iou(g, f)[0], f) for f in F), key=lambda t: t[0], default=(0.0, None))
                    if g[4] >= 2 and best[0] < 0.3:
                        cnt["phantom"] += 1; flags.append(dict(split=split, photo=name, check="phantom", boxes=[i], classes=[cls(g)], detail="the finder sees no device on this label"))
                    elif best[0] >= 0.5 and best[1][4] >= FINDER_SURE and best[1][5] == g[4] and g[4] >= 2:
                        cnt["agree"] += 1; flags.append(dict(split=split, photo=name, check="agree", boxes=[i], classes=[cls(g)], finder=best[1][:4],
                                                             label=cls(g), model=cls(g), conf=round(float(best[1][4]), 2), detail=f"both say {cls(g)} ({best[1][4]:.0%} confidence)"))
                    elif best[0] >= 0.5 and best[1][4] >= FINDER_SURE and best[1][5] != g[4] and (best[1][5] >= 2 or g[4] >= 2):
                        cnt["class"] += 1; flags.append(dict(split=split, photo=name, check="class", boxes=[i], classes=[cls(g)], finder=best[1][:4],
                                                             label=cls(g), model=NAMES.get(best[1][5], "?"), conf=round(float(best[1][4]), 2),
                                                             detail=f"network team label says {cls(g)}, our RackTrack model says {NAMES.get(best[1][5], '?')} ({best[1][4]:.0%} confidence)"))
                for f in F:
                    if f[4] >= FINDER_SURE and f[5] >= 2 and all(iou(g, f)[0] < 0.3 for g in gt):
                        cnt["missing"] += 1; flags.append(dict(split=split, photo=name, check="missing", boxes=[], classes=[], finder=f[:4],
                                                               detail=f"the finder is {f[4]:.2f} sure of a {NAMES.get(f[5], '?')} here, no label"))
        summary[split] = dict(photos=n_ph, boxes=n_box, device_boxes=n_dev, photos_with_finder=n_finder, photos_with_ladder=n_rows, counts=cnt)
        print(split, json.dumps(summary[split]), flush=True)
    out = dict(made=str(date.today()), thresholds=dict(model_conf=MODEL_CONF, cover=COVER, finder_sure=FINDER_SURE, finder_any=FINDER_ANY, duplicate_iou=0.5, contradiction_overlap=0.5),
               splits=summary, flags=flags)
    json.dump(out, open(RES / "label_audit.json", "w"), indent=1)
    print(f"written label_audit.json ({len(flags)} flags, every check)")


CHECKS = {
    "span": ("A device's units differ", "The network team's label (green) marks a device over some units of the rack; our RackTrack model (amber box, its class ignored) finds a device over different units there, no device at all, or a device where nothing is labelled. Most confident first.", "Label right = the label's units are right; Label wrong = our model is right and the label must change"),
    "format": ("The label line itself is broken", "An unknown class id, a coordinate outside the photo, a polygon with no area, or a line that is neither a box nor x,y pairs. Lines without a drawable shape have no picture; they are all in label_audit.csv.", "wrong = the line must be fixed or removed"),
    "duplicate": ("Two labels on one device", "Two device labels cover the same spot (overlap over 0.5). The first label is green, the second blue; when the two boxes coincide only the blue one shows. Usually the device was labelled twice, sometimes two boxes were drawn for one chassis.", "wrong = one of the two labels should go"),
    "contradiction": ("Device over an Empty label", "A device label (green) and an Empty or Closed Unit label (blue) cover the same spot. One of them is wrong.", "wrong = one of the two labels should go"),
    "class": ("Class may be wrong", "The network team's label (green) says one class; our RackTrack model is at least 80 % sure of a different class on the same spot (amber box). Most confident first.", "Label right = keep the label's class; Label wrong = the label's class is wrong, say the right one in the note if you can; Unclear = the device gets the class Unclear"),
    "tocheck": ("To check", "Labels flagged by a label check (red).", "Label wrong = write the right class in the note"),
    "unconf": ("Not confirmed", "Labels the model does not confirm at the threshold (yellow).", "Label wrong = write the right class in the note"),
    "nolabel": ("No label", "Areas where the model sees a device and no label is (purple).", "Label wrong = a device here needs a label"),
    "correct": ("100 % correct", "Labels the model confirms at the threshold (green).", "Label wrong = write the right class in the note"),
    "agree": ("Likely right", "The network team's label (green) and our RackTrack model (amber box) say the same class, the model at least 80 % sure. The least confident ones come first, since those are the most worth a look.", "Label right = keep the class; Label wrong = the class is wrong; Unclear = the device gets the class Unclear"),
    "missing": ("Device without a label", "The finder is at least 80 % sure of a device (amber) where no label is.", "wrong = a label is missing here"),
    "phantom": ("Label on nothing", "No finder box at all, even at 10 % confidence, overlaps this device label (green). The label may be on empty space, a shelf or a cable tray, or the device is just unusual.", "wrong = the label should go"),
}
PARTS = 2                   # (the user, 2026-10-19 evening: two reviewers) the page is split into this many parts (one reviewer each): reports/devices/label_audit/part<n>/
SHOW = ("tocheck", "unconf")   # tabs the reviewers still see (the user, 2026-10-21): No label went to the network team, 100 % correct needs no review
PAGES = {    # the review page sets: folder prefix -> (checks = the tabs, title, the question, a note, the verdicts per tab)
    "class": (("tocheck", "unconf", "nolabel", "correct"), "Device Label Audit", "label instances",
              "Unclear means it cannot be told from the photo.",
              {c: [("fine", "Label right"), ("wrong", "Label wrong"), ("unclear", "Unclear")] for c in ("tocheck", "unconf", "nolabel", "correct")}),
}
# the units page set ("part": the span check) was built from the 15 to 19 October labels; add it back here to rebuild it:
#   "part": (("span",), "Device Label Audit", "a device and how many units it takes", "Classes play no part.", {"span": [("fine", "Label right"), ("wrong", "Label wrong"), ("unclear", "Unclear")]})
CAPS = {"agree": 4000}      # cards per check on the pages (the agree cards are capped: the least confident ones are shown)
PAGE_CHECKS = tuple(c for spec in PAGES.values() for c in spec[0])     # what the CSV holds; label_audit.json keeps every check
CAP = 10000          # cards per check on the pages when CAPS has no entry
TILE_W, TILE_H, PER_SHEET = 600, 255, 150     # 150 cards per sheet: the artifact service allows 200 file uploads a day      # one label instance per card: rack (left) + the device (right)     # every card picture is one tile of a sheet image: the page stays under the file cap


REVIEW_MIN_PIXELS = 1_000_000   # the review covers photos of 1 MP or more (the user, 2026-10-19); smaller ones stay in the folder
CORRECT_CONF = 0.70           # (the user, 2026-10-19: 70 % for 100 % correct) a label is confirmed when the model is at least this sure of the same class on it (overlap >= 0.5)


def photo_flags():
    """One record per photo for the two-tab review (the user, 2026-10-19: "need to review" and "100% correct", by the model and its
    threshold). Each label is red (a check flags it), yellow (not confirmed at CORRECT_CONF) or green (confirmed); a photo is
    correct only when every label is green and no unlabelled device was found. The model only sorts: it never changes a label."""
    from eval_step3 import NAMES, load_gt
    A = json.load(open(RES / "label_audit.json")); F = json.load(open(RES / f"audit_finder_{SPLITS[0]}.json"))["photos"]
    why = {"class": "may have the wrong class", "duplicate": "two labels of different classes on one device", "contradiction": "a device label over an Empty label",
           "phantom": "label on nothing"}
    bad = collections.defaultdict(dict); miss = collections.defaultdict(list)
    for f in A["flags"]:
        if f["check"] == "duplicate" and f["classes"][0] == f["classes"][1]:
            continue                                                # the same class twice is not flagged (the user, 2026-10-21)
        if f["check"] in why:
            for i in f["boxes"]:
                bad[f["photo"]].setdefault(i, why[f["check"]])
        elif f["check"] == "missing" and f.get("finder"):
            miss[f["photo"]].append(f["finder"][:4])
    out = []
    for split in SPLITS:
        for name, path, lbl, w, h in photos(split):
            if w * h < REVIEW_MIN_PIXELS:
                continue
            gt = load_gt(lbl, w, h); fb = F.get(name, []); colours = []; reasons = collections.defaultdict(list); why_i = []
            for i, g in enumerate(gt):
                cname = NAMES.get(g[4], str(g[4]))
                if i in bad[name]:
                    colours.append("red"); reasons[bad[name][i]].append(cname); why_i.append(bad[name][i]); continue
                best = max(((iou(g, b)[0], b) for b in fb), key=lambda t: t[0], default=(0.0, None))
                if best[0] >= 0.5 and best[1][4] >= CORRECT_CONF and best[1][5] == g[4]:
                    colours.append("green"); why_i.append("confirmed")
                else:
                    colours.append("yellow"); reasons["not confirmed"].append(cname); why_i.append("not confirmed")
            n_red = colours.count("red"); n_yel = colours.count("yellow"); n_miss = len(miss[name])
            ok = gt and n_red == 0 and n_yel == 0 and n_miss == 0
            lines = [f"{k[0].upper() + k[1:]} ({len(v)}): {', '.join(sorted(set(v)))}" for k, v in sorted(reasons.items(), key=lambda t: t[0] == "not confirmed")]
            if n_miss:
                lines.append(f"Area with no label, purple ({n_miss})")
            label = f"{len(gt)} labels, all confirmed" if ok else f"{len(gt)} labels: {n_red} to check, {n_yel} not confirmed" + (f", {n_miss} unlabelled area{'s' if n_miss > 1 else ''}" if n_miss else "")
            out.append(dict(split=split, photo=name, check="correct" if ok else "review", colours=colours, miss=miss[name], why=why_i, cls=[NAMES.get(g[4], str(g[4])) for g in gt],
                            reasons=lines, label=label, score=(n_red, n_miss, n_yel)))
    return out


def in_doubt_ids():
    """Card ids of every instance sent to review (red or yellow in photo_flags), as the review pages name them."""
    return {f"{f['split']}__{pathlib.Path(f['photo']).stem}__L{i}" for f in photo_flags() for i, c in enumerate(f["colours"]) if c in ("red", "yellow")}


def page(parts=PARTS):
    """Every page set in PAGES, each dealt out into `parts` folders (reports/devices/label_audit/<prefix><n>/)."""
    for prefix, (checks, title, question, note, verdicts) in PAGES.items():
        build(parts, prefix, checks, title, question, note, verdicts)


FINDER = ROOT / "weights" / "device_finder.pt"
FINDER_KIND = "a YOLOv8m segmentation model (ultralytics), trained at 640 px for 100 epochs"   # from the checkpoint's own train_args


def proof_html(S, A, shown, part_note):
    """The "Sources and proof" tab: the chain from the network team's exports to the cards, drawn so it reads at a glance, with
    every number and hash from results/devices/sources.json (written by `sources`) and label_audit.json, so anyone can recompute it."""
    import html
    e = html.escape
    E = S["exports"]; M = S["merged"]; R = S["removed"]
    n_img = sum(x["images"] for x in E)
    n_ph = sum(v["photos"] for v in A["splits"].values()); n_box = sum(v["boxes"] for v in A["splits"].values())
    n_avif = sum(1 for q in (DATA / "images").iterdir() if q.suffix.lower() == ".avif")
    fn = json.load(open(RES / f"audit_finder_{SPLITS[0]}.json"))["names"]
    wsha = hashlib.sha256(FINDER.read_bytes()).hexdigest() if FINDER.exists() else "-"
    wmb = FINDER.stat().st_size / 1e6 if FINDER.exists() else 0
    wdate = time.strftime("%Y-%m-%d", time.localtime(FINDER.stat().st_mtime)) if FINDER.exists() else "-"
    short = lambda n: re.sub(r"^Device[ _]+[Aa]nnotation_+(Not_tra[i]?ned_|not_trained_)?", "", n)
    tick = '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="11"/><path d="M7 12.5l3.2 3.2L17 9"/></svg>'
    ok_cls = S["classes_same_in_every_yaml"] and S["code_list_matches"]
    verdicts = [
        (f"{len(E)} of {len(E)}", "exports list the same 12 classes, in the same order", ok_cls),
        (f"{M.get('renumbered_files', 0):,} files", "of one export (429_v6) renumbered by one proven table; nothing else remapped", True),
        (f"{M['photos'] - M['without_source']:,} of {M['photos']:,}", "photos traced to an export image by content", M["without_source"] == 0),
        (f"{M['polygons_identical']:,} of {M['photos']:,}", "label files hold exactly the export's polygons", M["polygons_identical"] == M["photos"]),
        (f"{M['photos'] - M['without_source']:,} of {M['photos']:,}", "export images in the dataset, byte for byte", M["without_source"] == 0),
    ]
    vrow = "".join(f'<div class="vtile{"" if good else " bad"}">{tick}<b>{v}</b><span>{t}</span></div>' for v, t, good in verdicts)
    # the chain, as steps
    steps = [
        ("Exports", f"{n_img:,}", f"photos from the network team in {len(E)} Roboflow exports"),
        ("Dataset", f"{M['photos']:,}", "photos in one folder, polygons only, each one as in its export; the exports themselves are deleted"),
        ("Review set", f"{S.get('review', {}).get('photos', 0):,}", "photos of 1 MP or more; the smaller ones stay in the dataset"),
        ("Audited", f"{n_ph:,}", f"{n_box:,} labels; the {n_avif} .avif photos cannot be opened here"),
        ("This page", f"{sum(shown.values()):,}", f"cards: {shown.get('tocheck', 0):,} to check, {shown.get('unconf', 0):,} not confirmed, {shown.get('nolabel', 0):,} no label, {shown.get('correct', 0):,} 100 % correct"),
    ]
    chain = "".join(f'<li><i>{k + 1}</i><small>{e(a)}</small><b>{b}</b><span>{c}</span></li>' for k, (a, b, c) in enumerate(steps))
    n_rev = S.get("review", {}).get("photos", 0); kp = 100 * n_rev / max(1, M["photos"])
    split_bar = (f'<div class="sbar"><div class="kept" style="width:{kp:.2f}%"><b>{n_rev:,}</b> in the review (1 MP or more)</div>'
                 f'<div class="gone" style="width:{100 - kp:.2f}%"><b>{M["photos"] - n_rev:,}</b> under 1 MP, in the dataset only</div></div>')
    # per export: one bar each, kept and removed, on one scale
    mx = max(x["images"] for x in E)
    bars = "".join(
        f'<div class="erow"><div class="ename"><b>{e(short(x["name"]))}</b><span>{x["date"]} &middot; {x["label_lines"]:,} labels</span></div>'
        f'<div class="etrack"><div class="ebar" style="width:{100 * x["images"] / mx:.2f}%">'
        f'<div class="kept" style="width:{100 * x["kept"] / x["images"]:.2f}%"></div><div class="gone" style="width:{100 * x["removed"] / x["images"]:.2f}%"></div></div>'
        f'<em>{x["images"]:,}</em></div>'
        f'<div class="eok{"" if x["polygons_identical"] == x["images"] else " bad"}">{tick}{x["polygons_identical"]:,} of {x["images"]:,} identical</div></div>'
        for x in sorted(E, key=lambda x: -x["images"]))
    fps = "".join(f'<tr><td>{e(short(x["name"]))}</td><td>{x["files"]:,}</td><td class="hash">{x["fingerprint"]}</td></tr>' for x in E)
    chips = "".join(f'<div class="chip"><i>{i}</i>{e(n)}</div>' for i, n in enumerate(S["classes"]))
    names = "".join(f'<tr><td class="rname">{e(a)}</td><td class="rname">{e(b)}</td></tr>' for a, b in M["name_examples"])
    changes = [
        ("rename", "Renamed", "all merged photos", "The merge named files Device_annotation_&lt;export&gt;_&lt;split&gt;_&lt;n&gt;; the 368_v7 batch kept its export names. Content untouched."),
        ("remove", "Not reviewed", f"{M['photos'] - S.get('review', {}).get('photos', 0):,} photos", "Photos under 1 MP stay in the dataset with their labels; they are left out of this review only."),
        ("keep", "Exports deleted", "7 exports", "After the dataset was built and checked against them, the exports were deleted at the user's request. Every original export label file is kept in provenance/export_labels.zip and every image's md5 in manifest.csv."),
        ("poly", "Polygons only", f"{M['box_lines_left_out']:,} lines", f"The export YT_455_V3 labels each device twice, as a polygon and as a box. The box lines are left out of {M.get('box_files', 0):,} files; every polygon line is the export's."),
        ("poly", "Renumbered", f"{M.get('renumbered_files', 0):,} files", "The export 429_v6 numbers its classes in another order than its data.yaml; our copy is renumbered by one table. Proof on the Export 429_v6 numbering tab."),
        ("add", "Added", "5 photos", "Five .avif photos the first merge had skipped, copied from the export 370_V4 under their export names."),
        ("keep", "Kept as they are", f"{M['twin_images']} images", f"The same image sits in two exports with different labels; both copies stay ({M['twins']} photos), marked in the manifest (same_image_as) for the network team to settle."),
    ]
    chg = "".join(f'<li class="{c}"><span class="tag">{t}</span><b>{n}</b><p>{d}</p></li>' for c, t, n, d in changes)
    return f"""<section id="proof" class="proof xtab" hidden>
    <div class="pintro"><h2>Where every label on this page comes from</h2><p>The network team's {len(E)} Roboflow exports are the ground truth and were never edited. Each check below can be run again with one command, and gives the same numbers.</p></div>
    <div class="vrow">{vrow}</div>
    <div class="card"><h3>From the exports to this page</h3><ol class="chain">{chain}</ol>{split_bar}</div>
    <div class="cards2">
    <div class="card"><h3>Class ids and names</h3><p class="muted">A label line is <span class="rname">class_id x y x y &hellip;</span>, a polygon. The id is looked up in the names block of the export's own data.yaml. The block below is the same in all {len(E)} exports, and our code uses the same list.</p>
    <div class="chips">{chips}</div>
    <details><summary>The data.yaml block, as the exports hold it</summary><pre class="yaml">{e(E[0]["yaml"][E[0]["yaml"].index("nc:"):E[0]["yaml"].index("roboflow")].strip() if "roboflow" in E[0]["yaml"] else E[0]["yaml"])}</pre></details></div>
    <div class="card"><h3>How the match is proven</h3><p class="muted">The merge renamed the files, so names prove nothing. Every file is matched by its content.</p>
    <div class="how"><div class="hbox"><small>Dataset</small><b>photo</b></div><div class="harrow">same md5</div><div class="hbox"><small>Export</small><b>image</b></div>
    <div class="hbox"><small>Dataset</small><b>label file</b></div><div class="harrow">same polygon lines</div><div class="hbox"><small>Export</small><b>label file</b></div></div>
    <p class="muted">One row per photo with its export file and checksums is in the dataset's manifest.csv; the original export labels are in provenance/export_labels.zip.</p></div>
    </div>
    <div class="card"><h3>Each export, checked</h3><p class="legend2"><i class="sw kept"></i>in the review (1 MP or more) <i class="sw gone"></i>under 1 MP, in the dataset only</p>
    <div class="ebars">{bars}</div>
    <details><summary>Fingerprints (sha256 over every file's md5)</summary><div class="tablewrap"><table><tr><th>Export</th><th>Files</th><th>Fingerprint</th></tr>{fps}<tr><td><b>Dataset folder</b></td><td>{M["files"]:,}</td><td class="hash">{M["fingerprint"]}</td></tr></table></div></details>
    <details><summary>How the merge renamed files: five examples</summary><div class="tablewrap"><table><tr><th>In the dataset</th><th>In the export</th></tr>{names}</table></div></details></div>
    <div class="cards2">
    <div class="card"><h3>What changed on the way</h3><ul class="changes">{chg}</ul></div>
    <div class="card"><h3>The model behind the order</h3><p class="muted">It only sorts which labels are looked at first. Nothing it says is shown on a card, and it never changes a label.</p>
    <dl class="spec"><dt>Name</dt><dd>RT-EOS device finder</dd><dt>Kind</dt><dd>{e(FINDER_KIND)}</dd><dt>Classes</dt><dd>{len(fn)}, the same list</dd>
    <dt>Need review</dt><dd>a photo with any label the model does not confirm (same class, at least {CORRECT_CONF:.0%} sure), a label flagged by a label check, or an area with no label</dd><dt>100 % correct</dt><dd>every label in the photo confirmed at that threshold</dd>
    <dt>File</dt><dd class="rname">{e(str(FINDER.relative_to(ROOT)))}, {wmb:.1f} MB, {wdate}</dd><dt>sha256</dt><dd class="hash">{wsha}</dd></dl></div>
    </div>
    <div class="card recompute"><h3>Run it again</h3><pre class="yaml">python3 scripts/devices/audit_labels.py verify</pre><p class="muted">Checks every image against its md5 in manifest.csv and every label file against the original export label file in provenance/export_labels.zip; last run {S["made"]}.</p></div>
    </section>"""


def issue_html(I, S):
    """The tab that names the export's numbering mistake, with the three proofs and the fix, from results/devices/export_issues.json."""
    import html, base64
    e = html.escape
    names = S["classes"]; short = lambda n: re.sub(r"^Device[ _]+[Aa]nnotation_+(Not_tra[i]?ned_|not_trained_)?", "", n)
    img_dir = ROOT / "reports" / "devices" / "label_audit" / "issue_img"
    uri = lambda f: "data:image/jpeg;base64," + base64.b64encode((img_dir / f).read_bytes()).decode()
    out = []
    for ds, v in I["exports"].items():
        T = {int(k): t for k, t in v["table"].items()}; rb, ra = v["rate_before"], v["rate_after"]
        others = [d for d in I["shares"] if d != ds]; tot = {d: 1 for d in others}
        tiles = [("1 of %d" % len(I["shares"]), "exports affected; the other six follow their data.yaml", "warn"),
                 (f"{v['label_lines']:,}", f"label lines in its {v['images']:,} images carry the shifted numbers; the polygons are right", "warn"),
                 (f"{v['shifted']} of {v['matched']}", f"devices that also appear in another export carry exactly the next class there", "ok"),
                 (f"{rb['pct']} % &rarr; {ra['pct']} %", "model disagreement on this export, before and after the fix", "ok")]
        trow = "".join(f'<div class="vtile {c}"><b>{a}</b><span>{b}</span></div>' for a, b, c in tiles)
        import csv as _csv
        ren = [r for r in _csv.DictReader(open(MANIFEST)) if r["renumbered"] == "yes"]
        ren_lines = sum(int(r["label_lines"] or 0) for r in ren)
        true_order = [names[T[k]] for k in range(12)]
        lst = lambda order, hi: "".join(f'<li{" class=hi" if k in hi else ""}><i>{k}</i>{e(n)}</li>' for k, n in enumerate(order))
        shifted = [k for k in range(12) if T[k] != k]
        three = f"""<div class="wsw">
    <div class="card"><span class="wtag is">What is there</span><p>The export's data.yaml says:</p><ol class="nlist">{lst(names, [])}</ol>
    <p>But its label files use the numbers in another order. A patch panel is stored as <b>5</b>, which this list calls PDU; a switch as <b>9</b> (Storage unit); a server as <b>7</b> (Router); an empty unit as <b>11</b> (UPS).</p></div>
    <div class="card"><span class="wtag should">What should be there</span><p>The order the label files really use, Empty at the end:</p><ol class="nlist">{lst(true_order, shifted)}</ol>
    <p>So either the data.yaml lists the classes in this order, or every label line uses the data.yaml's number: one higher for 1 to 10, and 1 in place of 11.</p></div>
    <div class="card"><span class="wtag fixed">How we fixed it</span><p>In our merged folder only, every label line of this export's {len(ren):,} photos got the number its device should have:</p>
    <div class="fixrow">{"".join(f'<span><b>{k}</b>&rarr;<b>{T[k]}</b></span>' for k in range(12) if T[k] != k)}<span class="same">0 stays 0</span></div>
    <p>{ren_lines:,} label lines renumbered, nothing else changed: the polygons and the export itself are untouched. The result: the same devices in other exports now carry the same class ({v['shifted']} of {v['matched']}), and the model disagrees on {ra['pct']} % of this export's labels, down from {rb['pct']} %.</p></div>
    </div>"""
        table = "".join(f'<div class="mrow{" same" if T[k] == k else ""}"><i>{k}</i><span class="was">{e(names[k])}</span><span class="arr">&rarr;</span><span class="now">{e(names[T[k]])}</span></div>' for k in range(12))
        pairs = "".join(f"<tr><td>{a} &middot; {e(names[a])}</td><td>{b} &middot; {e(names[b])}</td><td>{n}</td><td>{'yes' if T.get(a) == b else 'no'}</td></tr>" for a, b, n in v["pairs"])
        exs = "".join(f'<figure class="ex"><div class="exp"><img src="{uri(x["files"][0])}" alt=""><figcaption><b>{e(short(ds))}</b> id {x["ids"][0]}: data.yaml says <s>{e(names[x["ids"][0]])}</s></figcaption></div>'
                      f'<div class="exp"><img src="{uri(x["files"][1])}" alt=""><figcaption><b>{e(short(x["other"]))}</b> id {x["ids"][1]}: {e(names[x["ids"][1]])}</figcaption></div></figure>' for x in v["examples"])
        # class shares: the other exports pooled, this export as it is, this export through the table
        pooled = [sum(I["shares"][d][i] for d in others) / len(others) for i in range(12)]
        raw = I["shares"][ds]; fixed = [0.0] * 12
        for k in range(12):
            fixed[T[k]] += raw[k]
        mx = max(max(pooled), max(raw), max(fixed))
        bar = lambda val, c: f'<div class="sb {c}" style="width:{82 * val / mx:.1f}%"></div><em>{val:.1f} %</em>'
        shares = "".join(f'<div class="srow"><span>{e(names[i])}</span><div class="sbars"><div>{bar(pooled[i], "oth")}</div><div>{bar(raw[i], "raw")}</div><div>{bar(fixed[i], "fix")}</div></div></div>' for i in range(12))
        rmax = max(r["pct"] for r in I["rates"].values())
        rates = "".join(f'<div class="rrow2"><span>{e(short(d))}</span><div class="rtrack"><div class="rb {"raw" if d == ds else "oth"}" style="width:{82 * r["pct"] / rmax:.1f}%"></div><em>{r["pct"]} %</em></div></div>' for d, r in sorted(I["rates"].items(), key=lambda t: -t[1]["pct"]))
        rates += f'<div class="rrow2"><span><b>{e(short(ds))}, fixed</b></span><div class="rtrack"><div class="rb fix" style="width:{82 * ra["pct"] / rmax:.1f}%"></div><em>{ra["pct"]} %</em></div></div>'
        out.append(f"""<section id="issue" class="proof xtab" hidden>
    <div class="pintro"><h2>Export {e(short(ds))}: right polygons, shifted class numbers</h2><p>In <span class="rname">{e(ds)}</span> the network team drew the devices correctly, but the class numbers follow a different list than the export's own data.yaml: <b>Empty</b> sits at the end of their list, so numbers 1 to 10 point one class too low and 11 means Empty. This one numbering slip made the model disagree with {rb['disagree']:,} labels that were in fact right. We renumbered our copy with the table below and redid the review; the export itself is untouched.</p></div>
    {three}
    <div class="vrow">{trow}</div>
    <div class="cards2">
    <div class="card"><h3>The table</h3><p class="muted">Number in the export, what its data.yaml calls it, and the class the device really is.</p><div class="mtable">{table}</div></div>
    <div class="card"><h3>Proof 1: the same devices in other exports</h3><p class="muted">{v['images_in_other_exports']} image pairs sit in this export and in another one. Matching their polygons by overlap (over 70 %) pairs {v['matched']} devices; no model is involved.</p>
    <div class="tablewrap"><table><tr><th>In {e(short(ds))}</th><th>Same device elsewhere</th><th>Devices</th><th>Follows the table</th></tr>{pairs}</table></div></div>
    </div>
    <div class="card"><h3>What it looks like</h3><p class="muted">Each pair is one photo, in this export on the left and in another export on the right, with the same device outlined.</p><div class="exs">{exs}</div></div>
    <div class="cards2">
    <div class="card"><h3>Proof 2: class shares</h3><p class="legend2"><i class="sw oth"></i>the other six exports <i class="sw raw"></i>{e(short(ds))} as numbered <i class="sw fix"></i>{e(short(ds))} through the table</p><div class="shares">{shares}</div>
    <p class="muted">Racks look alike across exports, so the shares should too. As numbered, this export has almost no Patch Panels, Switches or Empty units and a flood of PDUs, Storage units and UPSs; through the table it matches the others.</p></div>
    <div class="card"><h3>Proof 3: how often the model disagrees</h3><p class="muted">Share of labels where the model is at least {FINDER_SURE:.0%} sure of a different class.</p><div class="rates">{rates}</div></div>
    </div>
    <div class="card"><h3>What we did, and what we ask</h3><ul class="changes">
    <li class="poly"><span class="tag">Renumbered</span><b>{v['label_lines']:,} label lines</b><p>In our merged dataset only, by the table above, when the dataset was built. The polygons and the export are untouched; the content check on the Sources and proof tab counts these files as "identical polygons, class ids renumbered by the table".</p></li>
    <li class="add"><span class="tag">Review redone</span><b>both pages</b><p>The cards were rebuilt on the renumbered labels, so this export's devices are judged by their real class.</p></li>
    <li class="remove"><span class="tag">Ask</span><b>network team</b><p>Re-export {e(short(ds))} with the class list in the order its labels use, or confirm the table. The proof was computed while the exports existed and is kept in results/devices/export_issues.json.</p></li></ul></div>
    </section>""")
    return "".join(out)


def build(parts, prefix, checks, title, question, note, verdicts):
    import html
    import re
    from PIL import ImageDraw
    A = json.load(open(RES / "label_audit.json"))
    OUT = ROOT / "reports" / "devices" / "label_audit"
    OUT.mkdir(parents=True, exist_ok=True)
    recs = {}
    for split in SPLITS:
        for name, path, lbl, w, h in photos(split):
            recs[(split, name)] = dict(file=str(path), lbl=lbl, w=w, h=h)
    GREEN, BLUE, AMBER, PURPLE = (26, 158, 110), (37, 99, 235), (232, 140, 20), (200, 40, 170)

    def draw_shape(img, shape, scale, origin, width):
        """shape: (points, colour) draws the label polygon as drawn; (x1, y1, x2, y2, colour) draws a finder box."""
        d = ImageDraw.Draw(img)
        if len(shape) == 2:
            pts = [((x - origin[0]) * scale, (y - origin[1]) * scale) for x, y in shape[0]]
            d.line(pts + [pts[0]], fill=shape[1], width=width, joint="curve")
        else:
            d.rectangle(((shape[0] - origin[0]) * scale, (shape[1] - origin[1]) * scale, (shape[2] - origin[0]) * scale, (shape[3] - origin[1]) * scale), outline=shape[4], width=width)

    def extent(shape):
        if len(shape) == 2:
            xs = [x for x, _ in shape[0]]; ys = [y for _, y in shape[0]]
            return min(xs), min(ys), max(xs), max(ys)
        return shape[:4]

    def instance_tiles(rec, f, cap_px=1800):
        """Every label instance of one photo (and every area with no label): {key: jpeg bytes}, each a TILE_W x TILE_H picture
        of the whole rack with only that label outlined (left) and the device cropped (right). The photo is opened once."""
        import io
        img = ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB"); W0, H0 = img.size
        k = min(1.0, cap_px / max(W0, H0))
        if k < 1.0:
            img = img.resize((round(W0 * k), round(H0 * k)), Image.LANCZOS)
        if "shapes" not in rec:
            rec["shapes"] = load_shapes(rec["lbl"], rec["w"], rec["h"])
        COLS = {"red": (225, 45, 45), "yellow": (245, 190, 0), "green": GREEN}
        jobs = [(f"L{i}", rec["shapes"][i][0], COLS[c]) for i, c in enumerate(f["colours"]) if i < len(rec["shapes"]) and len(rec["shapes"][i][0]) >= 2]
        jobs += [(f"M{j}", b, PURPLE) for j, b in enumerate(f["miss"])]
        s0 = min(TILE_H / img.height, 170 / img.width); base = img.resize((max(1, round(img.width * s0)), max(1, round(img.height * s0))), Image.LANCZOS)
        out = {}
        for key, geo, col in jobs:
            sh = ([(x * k, y * k) for x, y in geo], col) if key[0] == "L" else (geo[0] * k, geo[1] * k, geo[2] * k, geo[3] * k, col)
            rack = base.copy(); draw_shape(rack, sh, s0, (0, 0), 2)
            x0, y0, x1, y1 = extent(sh); bw, bh = max(30.0, x1 - x0), max(20.0, y1 - y0)
            box = (int(max(0, x0 - 0.12 * bw)), int(max(0, y0 - 0.7 * bh)), int(min(img.width, x1 + 0.12 * bw)), int(min(img.height, y1 + 0.7 * bh)))
            crop = img.crop(box); avail = TILE_W - rack.width - 8; sc = min(avail / max(1, crop.width), TILE_H / max(1, crop.height))
            crop = crop.resize((max(1, round(crop.width * sc)), max(1, round(crop.height * sc))), Image.LANCZOS)
            draw_shape(crop, sh, sc, (box[0], box[1]), 3)
            t = Image.new("RGB", (TILE_W, TILE_H), (238, 240, 244))
            t.paste(rack, (0, (TILE_H - rack.height) // 2)); t.paste(crop, (rack.width + 8 + (avail - crop.width) // 2, (TILE_H - crop.height) // 2))
            buf = io.BytesIO(); t.save(buf, "JPEG", quality=80); out[key] = buf.getvalue()
        return out

    def photo_tile(rec, shapes, cells):
        """One TILE_W x TILE_H picture: the whole rack (left) with every shape, and a grid of device crops (right), one per
        entry of `cells` = [(shape, caption)], at most 8 (the last one says how many more there are)."""
        import draw as _draw
        img = ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB"); drawn = img.copy()
        s0 = min(TILE_H / img.height, 260 / img.width); lw = max(2, round(2.2 / s0))
        for sh in shapes:
            draw_shape(drawn, sh, 1.0, (0, 0), lw)
        rack = drawn.resize((max(1, round(img.width * s0)), max(1, round(img.height * s0))), Image.LANCZOS)
        out = Image.new("RGB", (TILE_W, TILE_H), (238, 240, 244))
        ImageDraw.Draw(rack).rectangle((0, 0, rack.width - 1, rack.height - 1), outline=(200, 204, 210))
        out.paste(rack, (0, (TILE_H - rack.height) // 2))
        x_off = rack.width + 10; aw = TILE_W - x_off; cols = 2; rows = 4; cw, ch = (aw - 6 * (cols - 1)) // cols, (TILE_H - 6 * (rows - 1)) // rows
        more = max(0, len(cells) - cols * rows); cells = cells[:cols * rows]; f = _draw.font(15); d = ImageDraw.Draw(out)
        for k, (sh, cap) in enumerate(cells):
            cx, cy = x_off + (k % cols) * (cw + 6), (k // cols) * (ch + 6)
            x0, y0, x1, y1 = extent(sh); bw, bh = max(30.0, x1 - x0), max(20.0, y1 - y0)
            box = (int(max(0, x0 - 0.12 * bw)), int(max(0, y0 - 0.5 * bh)), int(min(img.width, x1 + 0.12 * bw)), int(min(img.height, y1 + 0.5 * bh)))
            crop = img.crop(box); ih = ch - 22; sc = min(cw / max(1, crop.width), ih / max(1, crop.height))
            crop = crop.resize((max(1, round(crop.width * sc)), max(1, round(crop.height * sc))), Image.LANCZOS)
            draw_shape(crop, sh, sc, (box[0], box[1]), 2)          # only this device's outline on its crop
            d.rectangle((cx, cy, cx + cw - 1, cy + ch - 1), fill=(255, 255, 255))
            out.paste(crop, (cx + (cw - crop.width) // 2, cy + (ih - crop.height) // 2))
            col = sh[-1]; d.rectangle((cx, cy + ih, cx + cw - 1, cy + ch - 1), fill=col)
            text = cap if not (more and k == len(cells) - 1) else f"{cap}   +{more} more"
            d.text((cx + 6, cy + ih + 2), text, fill=(20, 23, 28) if col == (245, 190, 0) else (255, 255, 255), font=f)
        return out

    def tile(rec, shapes):
        """One TILE_W x TILE_H picture: the whole rack (left) and a close-up (right), the shapes drawn on both."""
        img = ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB")
        s = min(TILE_H / img.height, 220 / img.width); rack = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)
        lw = max(2, round(rack.height / 200))
        for sh in shapes:
            draw_shape(rack, sh, s, (0, 0), lw)
        ex = [extent(sh) for sh in shapes]
        bh = max(20.0, ex[0][3] - ex[0][1]); bw = max(60.0, ex[0][2] - ex[0][0])
        x0 = max(0, int(min(e[0] for e in ex) - 0.1 * bw)); x1 = min(img.width, int(max(e[2] for e in ex) + 0.1 * bw))
        y0 = max(0, int(min(e[1] for e in ex) - 2.0 * bh)); y1 = min(img.height, int(max(e[3] for e in ex) + 2.0 * bh))
        close = img.crop((x0, y0, x1, y1)); avail = TILE_W - rack.width - 8
        sc = min(2.5, avail / max(1, close.width), TILE_H / max(1, close.height))      # a small photo's close-up may grow up to 2.5x
        close = close.resize((max(1, round(close.width * sc)), max(1, round(close.height * sc))), Image.LANCZOS)
        lw = max(2, round(close.width / 180))
        for sh in shapes:
            draw_shape(close, sh, sc, (x0, y0), lw)
        out = Image.new("RGB", (TILE_W, TILE_H), (238, 240, 244))
        ImageDraw.Draw(rack).rectangle((0, 0, rack.width - 1, rack.height - 1), outline=(200, 204, 210))
        ImageDraw.Draw(close).rectangle((0, 0, close.width - 1, close.height - 1), outline=(200, 204, 210))
        out.paste(rack, (0, (TILE_H - rack.height) // 2)); out.paste(close, (rack.width + 8 + (avail - close.width) // 2, (TILE_H - close.height) // 2))
        return out

    items, tiles = [], []
    PF = photo_flags() if any(c in ("tocheck", "unconf", "nolabel", "correct") for c in checks) else []
    from eval_step3 import NAMES
    COL = {"red": (225, 45, 45), "yellow": (245, 190, 0), "green": GREEN}
    for check in checks:
        if check in ("tocheck", "unconf", "nolabel", "correct"):
            if "inst" not in locals():
                inst, made = [], {}
                for f in PF:
                    rec = recs.get((f["split"], f["photo"]))
                    if rec is None:
                        continue
                    made[f["photo"]] = instance_tiles(rec, f)
                    stem = pathlib.Path(f["photo"]).stem
                    rank = {"may have the wrong class": 0, "two labels on one device": 1, "a device label over an Empty label": 2, "label on nothing": 3}
                    for i, c in enumerate(f["colours"]):
                        if f"L{i}" not in made[f["photo"]]:
                            continue
                        why = f["why"][i]
                        inst.append(dict(check={"green": "correct", "yellow": "unconf", "red": "tocheck"}[c], key=f"L{i}", photo=f["photo"], split=f["split"], label=f["cls"][i],
                                         status=("To check: " + why) if c == "red" else ("Not confirmed" if c == "yellow" else "Confirmed"),
                                         order=(rank.get(why, 5) if c == "red" else 6 if c == "yellow" else 9), iid=f"{f['split']}__{stem}__L{i}"))
                    for j in range(len(f["miss"])):
                        inst.append(dict(check="nolabel", key=f"M{j}", photo=f["photo"], split=f["split"], label="nothing (no label in the outlined area)",
                                         status="To check: a device may be here without a label", order=4, iid=f"{f['split']}__{stem}__M{j}"))
                    if len(made) % 300 == 0:
                        print(f"instance pictures: {len(made)} of {len(PF)} photos", flush=True)
                inst.sort(key=lambda d: d["order"])
            for d in [d for d in inst if d["check"] == check]:
                tiles.append(made[d["photo"]][d["key"]])
                items.append(dict(id=re.sub(r"[^A-Za-z0-9_.~:@+-]", "_", d["iid"])[:180], check=check, split=d["split"], photo=d["photo"], classes=[d["label"]],
                                  detail=d["label"], label=d["label"], reasons=[d["status"]]))
            continue
        fl = [f for f in A["flags"] if f["check"] == check]
        if check == "duplicate":
            fl.sort(key=lambda f: -float(re.search(r"([0-9.]+)$", f["detail"]).group(1)))          # the clearest overlaps first
        elif check == "agree":
            fl.sort(key=lambda f: f["conf"])                                                         # least confident first
        elif check in ("class", "missing", "span"):
            fl.sort(key=lambda f: -float(f["conf"] if f.get("conf") is not None else re.search(r"([0-9.]+) sure", f["detail"]).group(1)))      # most confident first
        for n, f in enumerate(fl[:CAPS.get(check, CAP)]):
            rec = recs.get((f["split"], f["photo"]))
            if rec is None:
                continue
            if "shapes" not in rec:
                rec["shapes"] = load_shapes(rec["lbl"], rec["w"], rec["h"])
            if any(i >= len(rec["shapes"]) or len(rec["shapes"][i][0]) < 2 for i in f["boxes"]):
                continue
            shapes = [(rec["shapes"][i][0], GREEN if k == 0 else BLUE) for k, i in enumerate(f["boxes"])]
            if check == "missing" and f.get("finder"):
                shapes = [tuple(f["finder"][:4]) + (PURPLE,)]            # the area to check: no label there
            if not shapes:
                continue
            iid = re.sub(r"[^A-Za-z0-9_.~:@+-]", "_", f"{f['split']}__{pathlib.Path(f['photo']).stem}__{check}__{'-'.join(map(str, f['boxes'])) or 'f%d' % n}")[:180]
            tiles.append(tile(rec, shapes))
            cl = f["classes"]
            say = {"duplicate": f"{' and '.join(cl)}, two labels on one device" if len(cl) == 2 else " + ".join(cl),
                   "contradiction": f"{cl[0]}, over a label that says {cl[1]}" if len(cl) == 2 else " + ".join(cl),
                   "missing": "nothing, no label in the marked area"}.get(check) or f.get("label") or " + ".join(cl)
            items.append(dict(id=iid, check=check, split=f["split"], photo=f["photo"], classes=cl, detail=say,
                              label=say, model=f.get("model", ""), conf=f.get("conf", 0)))
    summary = ""
    def tally(its):
        lab = [it for it in its if not it["id"].rsplit("__", 1)[-1].startswith("M")]
        rv_ph = {it["photo"] for it in its if it["check"] != "correct"}
        return dict(photos=len({it["photo"] for it in its}), inst=len(lab), red=sum(it["check"] == "tocheck" for it in lab),
                    yel=sum(it["check"] == "unconf" for it in lab), ok=sum(it["check"] == "correct" for it in lab), rv_ph=len(rv_ph), areas=len(its) - len(lab))
    TOT = tally(items) if PF else None
    n_small = sum(1 for sp in SPLITS for t in photos(sp) if t[3] * t[4] < REVIEW_MIN_PIXELS) if PF else 0
    items_all, tiles_all = items, tiles
    owner = {}
    if any(it["check"] in ("tocheck", "unconf", "nolabel", "correct") for it in items_all):        # whole photos per reviewer, balanced by label count
        per_photo = collections.Counter(it["photo"] for it in items_all); load = [0] * parts
        for ph, n in sorted(per_photo.items(), key=lambda t: (-t[1], t[0])):
            k = load.index(min(load)); owner[ph] = k; load[k] += n
    keep = [k for k, it in enumerate(items_all) if it["check"] in SHOW or it["check"] not in ("tocheck", "unconf", "nolabel", "correct")]
    items_all = [items_all[k] for k in keep]; tiles_all = [tiles_all[k] for k in keep]   # reviewers keep their photos; only the shown tabs stay
    checks = tuple(c for c in checks if c in SHOW or c not in ("tocheck", "unconf", "nolabel", "correct"))
    for part in range(parts):
        sel = [k for k in range(len(items_all)) if (owner[items_all[k]["photo"]] if owner else k % parts) == part]
        items = [dict(items_all[k]) for k in sel]; tiles = [tiles_all[k] for k in sel]
        out_dir = OUT / f"{prefix}{part + 1}"; img_dir = out_dir / "img"
        img_dir.mkdir(parents=True, exist_ok=True)
        for f in img_dir.glob("*.jpg"):
            f.unlink()
        part_name = f" {part + 1}" if parts > 1 else ""; part_note = f"part {part + 1} of {parts}, " if parts > 1 else ""
        sheets = []
        for k in range(0, len(tiles), PER_SHEET):
            chunk = tiles[k:k + PER_SHEET]; sheet = Image.new("RGB", (TILE_W, TILE_H * len(chunk)), (238, 240, 244))
            for j, t in enumerate(chunk):
                sheet.paste(t if isinstance(t, Image.Image) else Image.open(__import__("io").BytesIO(t)), (0, j * TILE_H))
            name = f"sheet_{len(sheets):03d}.jpg"; sheet.save(img_dir / name, quality=70); sheets.append(name)
            for j, it in enumerate(items[k:k + PER_SHEET]):
                it["sheet"] = name; it["row"] = j
        counts = {c: {s: A["splits"].get(s, {}).get("counts", {}).get(c, 0) for s in SPLITS} for c in checks}
        shown = {c: sum(it["check"] == c for it in items) for c in checks}
        n_photos = sum(v["photos"] for v in A["splits"].values()); n_boxes = sum(v["boxes"] for v in A["splits"].values())
        CSS = (ROOT / "scripts" / "units" / "build_report.py").read_text()
        CSS = CSS[CSS.index('CSS = """') + 9:CSS.index('"""', CSS.index('CSS = """') + 9)]
        CSS = CSS + ""
        CSS += """
    .tstrip { grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); } .tstrip b { font-size:1.7rem; } .tstrip b.wrong { color:var(--bad); } .tstrip b.fine { color:var(--ok); } .tstrip b.unclear { color:var(--muted); } .tstrip b.model { color:#d07a0a; }
    .toolbar { display:flex; flex-wrap:wrap; gap:10px 14px; align-items:center; margin:14px 0 18px; }
    .banner { margin:10px 0 14px; padding:10px 14px; border-radius:10px; background:var(--mid-bg); color:var(--mid); font-size:.92rem; } .banner.ok { background:var(--ok-bg); color:var(--ok); }
    .rgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr)); gap:14px; }
    .rcard { background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); padding:10px; display:flex; flex-direction:column; gap:8px; min-width:0; }
    .rcard.wrong { border-color:var(--bad); } .rcard.fine { border-color:var(--ok); } .rcard.unclear { border-color:var(--line-strong); } .rcard.model { border-color:#d07a0a; }
    .pic { position:relative; width:100%; padding-top:PADPCT; overflow:hidden; background:var(--surface); border-radius:8px; cursor:zoom-in; }
    .pic img { position:absolute; left:0; width:100%; max-width:none; height:auto; display:block; border-radius:0; cursor:zoom-in; }
    #lbpic { position:relative; width:min(92vw,1400px); overflow:hidden; border-radius:8px; background:var(--surface); }
    #lbpic img { position:absolute; left:0; width:100%; max-width:none; height:auto; display:block; cursor:default; }
    .rmeta { display:flex; gap:8px; align-items:center; flex-wrap:wrap; font-size:.9rem; } .rmeta b { font-weight:500; }
    .rname { font:.74rem var(--mono); color:var(--muted); overflow-wrap:anywhere; }
    .say { display:flex; flex-direction:column; gap:3px; font-size:.98rem; } .say .who { color:var(--muted); } .say b.lab { color:#1a9e6e; } .say b.fnd { color:#d07a0a; }
    .rbtns { display:flex; gap:8px; flex-wrap:wrap; }
    .rbtn { font:500 .86rem var(--sans); border:1px solid var(--line); background:var(--surface); color:var(--fg); padding:7px 12px; border-radius:8px; cursor:pointer; } .rbtn:hover { border-color:var(--line-strong); }
    .rbtn[data-v="wrong"][aria-pressed="true"] { background:var(--bad); border-color:var(--bad); color:#fff; } .rbtn[data-v="fine"][aria-pressed="true"] { background:var(--ok); border-color:var(--ok); color:#fff; } .rbtn[data-v="unclear"][aria-pressed="true"] { background:var(--logic); border-color:var(--logic); color:#fff; } .rbtn[data-v="model"][aria-pressed="true"] { background:#d07a0a; border-color:#d07a0a; color:#fff; }
    textarea.note { width:100%; min-height:44px; font:.88rem/1.45 var(--sans); border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:var(--panel); color:var(--fg); resize:vertical; }
    textarea.note:focus { outline:2px solid var(--accent); outline-offset:1px; } .saved { font:.74rem var(--mono); color:var(--muted); min-height:1em; }
    .k { display:inline-block; width:12px; height:12px; border-radius:2px; border:2.5px solid; margin:0 5px 0 8px; vertical-align:-2px; } .k.lab { border-color:#1a9e6e; } .k.lab2 { border-color:#2563eb; } .k.fnd { border-color:#e88c14; } .legend .k:first-child { margin-left:0; }
    .legend { font-size:.85rem; color:var(--muted); margin:0 0 8px; }
    .tablewrap td, .tablewrap th { text-align:right; } .tablewrap td:first-child, .tablewrap th:first-child { text-align:left; }
    .explain { display:none; } .explain.on { display:block; }
    .proof { display:grid; gap:18px; } .proof h3 { font-size:1.02rem; margin:0 0 8px; } .proof .card p { margin:6px 0; }
    .pintro h2 { font-size:1.35rem; margin:4px 0 4px; } .pintro p { margin:0; color:var(--muted); max-width:760px; }
    .vrow { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:12px; }
    .vtile { background:var(--panel); border-radius:12px; box-shadow:var(--shadow); padding:14px 16px; display:grid; grid-template-columns:30px 1fr; column-gap:10px; align-items:center; border-top:3px solid var(--ok); }
    .vtile svg { width:28px; height:28px; grid-row:span 2; fill:var(--ok-bg); stroke:var(--ok); stroke-width:2.2; stroke-linecap:round; stroke-linejoin:round; }
    .vtile b { font-size:1.35rem; font-weight:600; color:var(--ok); } .vtile span { font-size:.83rem; color:var(--muted); line-height:1.35; }
    .vtile.bad { border-top-color:var(--bad); } .vtile.bad b { color:var(--bad); } .vtile.bad svg { stroke:var(--bad); fill:var(--bad-bg); }
    .chain { list-style:none; margin:4px 0 14px; padding:0; display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; counter-reset:none; }
    .chain li { position:relative; background:var(--surface); border-radius:10px; padding:12px 12px 12px 14px; }
    .chain li + li::before { content:""; position:absolute; left:-9px; top:50%; border:6px solid transparent; border-left-color:var(--line-strong); transform:translateY(-50%); }
    .chain i { font-style:normal; display:inline-grid; place-items:center; width:20px; height:20px; border-radius:50%; background:var(--accent); color:#fff; font-size:.72rem; font-weight:600; margin-right:6px; }
    .chain small { font-size:.78rem; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; } .chain b { display:block; font-size:1.45rem; font-weight:600; margin:6px 0 2px; } .chain span { display:block; font-size:.8rem; color:var(--muted); line-height:1.35; }
    .sbar { display:flex; height:30px; border-radius:8px; overflow:hidden; font-size:.8rem; } .sbar div { display:flex; align-items:center; padding:0 10px; white-space:nowrap; overflow:hidden; } .sbar b { margin-right:4px; }
    .sbar .kept, .ebar .kept, .sw.kept { background:var(--accent); color:#fff; } .sbar .gone, .ebar .gone, .sw.gone { background:#c9d2df; color:var(--fg); }
    .legend2 { font-size:.82rem; color:var(--muted); margin:0 0 10px; } .sw { display:inline-block; width:12px; height:12px; border-radius:3px; margin:0 5px -1px 12px; } .legend2 .sw:first-child { margin-left:0; }
    .ebars { display:grid; gap:10px; } .erow { display:grid; grid-template-columns:minmax(150px,210px) 1fr minmax(150px,auto); gap:14px; align-items:center; }
    .ename b { display:block; font-weight:600; font-size:.9rem; } .ename span { font-size:.76rem; color:var(--muted); }
    .etrack { display:flex; align-items:center; gap:8px; } .ebar { display:flex; height:18px; border-radius:5px; overflow:hidden; min-width:4px; } .etrack em { font-style:normal; font-size:.82rem; color:var(--muted); }
    .eok { display:flex; align-items:center; gap:6px; font-size:.82rem; color:var(--ok); font-weight:500; } .eok svg { width:18px; height:18px; fill:var(--ok-bg); stroke:var(--ok); stroke-width:2.4; stroke-linecap:round; stroke-linejoin:round; } .eok.bad { color:var(--bad); }
    .chips { display:grid; grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:6px; margin:10px 0; }
    .chip { display:flex; align-items:center; gap:8px; background:var(--surface); border-radius:8px; padding:6px 8px; font-size:.86rem; } .chip i { font:600 .76rem var(--mono); font-style:normal; background:var(--accent-soft); color:var(--accent); border-radius:5px; min-width:24px; text-align:center; padding:2px 4px; }
    .how { display:grid; grid-template-columns:1fr auto 1fr; gap:10px 8px; align-items:center; margin:12px 0; }
    .hbox { background:var(--surface); border-radius:10px; padding:10px 12px; text-align:center; } .hbox small { display:block; font-size:.74rem; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; } .hbox b { font-weight:600; }
    .harrow { font-size:.76rem; color:var(--ok); font-weight:600; text-align:center; padding:0 4px; } .harrow::after { content:""; display:block; height:2px; background:var(--ok); margin-top:4px; border-radius:2px; }
    .changes { list-style:none; margin:4px 0 0; padding:0; display:grid; gap:10px; } .changes li { display:grid; grid-template-columns:auto 1fr; column-gap:10px; align-items:baseline; }
    .changes .tag { font-size:.74rem; font-weight:600; border-radius:999px; padding:2px 9px; grid-row:span 2; white-space:nowrap; } .changes b { font-size:.92rem; } .changes p { margin:2px 0 0 !important; font-size:.84rem; color:var(--muted); grid-column:2; }
    .changes .rename .tag, .changes .poly .tag { background:var(--accent-soft); color:var(--accent); } .changes .remove .tag { background:var(--mid-bg); color:var(--mid); } .changes .add .tag { background:var(--ok-bg); color:var(--ok); } .changes .keep .tag { background:var(--soft); color:var(--logic); }
    .spec { display:grid; grid-template-columns:auto 1fr; gap:6px 14px; margin:10px 0 0; font-size:.86rem; } .spec dt { color:var(--muted); } .spec dd { margin:0; }
    .proof details { margin-top:10px; font-size:.86rem; } .proof summary { cursor:pointer; color:var(--accent); font-weight:500; }
    .hash { font:.74rem var(--mono); overflow-wrap:anywhere; } pre.yaml { font:.8rem/1.45 var(--mono); background:var(--surface); padding:10px 12px; border-radius:8px; overflow:auto; margin:6px 0; }
    .proof .tablewrap { margin:8px 0; } .proof table td, .proof table th { font-size:.82rem; padding:5px 8px; text-align:left !important; }
    .vtile.warn { border-top-color:var(--warn); } .vtile.warn b { color:var(--mid); } .vtile:not(:has(svg)) { grid-template-columns:1fr; }
    .wsw { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr)); gap:16px; }
    .wsw .card p { font-size:.88rem; } .wtag { display:inline-block; font-size:.76rem; font-weight:600; border-radius:999px; padding:3px 11px; margin-bottom:6px; }
    .wtag.is { background:var(--bad-bg); color:var(--bad); } .wtag.should { background:var(--ok-bg); color:var(--ok); } .wtag.fixed { background:var(--accent-soft); color:var(--accent); }
    .nlist { list-style:none; margin:6px 0; padding:0; display:grid; grid-template-columns:1fr 1fr; gap:3px 10px; font-size:.84rem; }
    .nlist li { display:flex; gap:6px; align-items:center; } .nlist i { font:600 .72rem var(--mono); font-style:normal; background:var(--soft); border-radius:4px; min-width:22px; text-align:center; padding:1px 3px; }
    .nlist li.hi { color:var(--ok); font-weight:600; } .nlist li.hi i { background:var(--ok-bg); color:var(--ok); }
    .fixrow { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0; } .fixrow span { font:.8rem var(--mono); background:var(--accent-soft); color:var(--accent); border-radius:6px; padding:3px 8px; } .fixrow span.same { background:var(--soft); color:var(--muted); }
    .mtable { display:grid; gap:4px; margin-top:8px; } .mrow { display:grid; grid-template-columns:28px 1fr 22px 1fr; align-items:center; gap:6px; background:var(--surface); border-radius:8px; padding:5px 8px; font-size:.86rem; }
    .mrow i { font:600 .76rem var(--mono); font-style:normal; background:var(--accent-soft); color:var(--accent); border-radius:5px; text-align:center; padding:2px 0; }
    .mrow .was { color:var(--bad); text-decoration:line-through; } .mrow .now { color:var(--ok); font-weight:600; } .mrow .arr { color:var(--muted); text-align:center; }
    .mrow.same .was { color:var(--fg); text-decoration:none; } .mrow.same .now { color:var(--fg); font-weight:400; }
    .exs { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr)); gap:14px; margin-top:8px; }
    .ex { margin:0; display:grid; grid-template-columns:1fr 1fr; gap:8px; background:var(--surface); border-radius:10px; padding:8px; }
    .exp img { width:100%; height:170px; object-fit:contain; background:#fff; border-radius:6px; display:block; cursor:default; } .exp figcaption { font-size:.8rem; margin-top:4px; color:var(--muted); } .exp figcaption b { color:var(--fg); }
    .exp figcaption s { color:var(--bad); }
    .shares { display:grid; gap:7px; margin-top:6px; } .srow { display:grid; grid-template-columns:110px 1fr; gap:10px; align-items:center; font-size:.82rem; }
    .sbars > div { display:flex; align-items:center; gap:6px; height:8px; margin:2px 0; } .sb { height:8px; border-radius:3px; min-width:2px; } .sbars em { white-space:nowrap; font-style:normal; font-size:.7rem; color:var(--muted); }
    .sb.oth, .sw.oth, .rb.oth { background:#b9c3d1; } .sb.raw, .sw.raw, .rb.raw { background:var(--bad); } .sb.fix, .sw.fix, .rb.fix { background:var(--ok); }
    .rates { display:grid; gap:8px; margin-top:8px; } .rrow2 { display:grid; grid-template-columns:150px 1fr; gap:10px; align-items:center; font-size:.84rem; }
    .rtrack { display:flex; align-items:center; gap:8px; } .rb { height:16px; border-radius:4px; min-width:3px; } .rtrack em { white-space:nowrap; font-style:normal; font-size:.8rem; color:var(--muted); }
    .cards2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr)); gap:18px; }
    @media (max-width:700px) { .erow { grid-template-columns:1fr; gap:4px; } .how { grid-template-columns:1fr; } }
    .ptitle small { display:block; font-size:.8rem; font-weight:500; color:var(--muted); letter-spacing:0; margin-top:2px; }
    .howto { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin:16px 0 6px; }
    .howto div { background:var(--panel); border-radius:12px; box-shadow:var(--shadow); padding:12px 14px; display:grid; grid-template-columns:28px 1fr; column-gap:10px; }
    .howto > div > i { font-style:normal; grid-row:span 2; display:grid; place-items:center; width:26px; height:26px; border-radius:50%; background:var(--accent-soft); color:var(--accent); font-weight:600; font-size:.85rem; }
    .howto b { font-size:.92rem; } .howto span { font-size:.84rem; color:var(--muted); line-height:1.4; }
    .rbtn.primary { background:var(--accent); border-color:var(--accent); color:#fff; } .rbtn.primary:hover { filter:brightness(1.08); } .rbtn.primary:disabled { opacity:.6; cursor:progress; }
    .tstrip div.prog { height:6px; padding:0; margin-top:10px; background:var(--soft); border:0; border-radius:999px; box-shadow:none; overflow:hidden; } .tstrip div.prog div { height:100%; width:0; padding:0; border:0; border-radius:0; box-shadow:none; background:var(--accent); transition:width .3s; }
    .dsum { margin-bottom:16px; } .dsum h3 { margin:0 0 10px; font-size:1.05rem; }
    .dtiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; margin-bottom:12px; }
    .dtiles > div { background:var(--surface); border-radius:10px; padding:12px 14px; } .dtiles > div > b { display:block; font-size:1.6rem; font-weight:600; } .dtiles span { font-size:.84rem; color:var(--muted); }
    .dtiles .okt > b { color:var(--ok); } .dtiles .rvt > b { color:var(--bad); }
    .dlab { margin:8px 0 4px; font-size:.78rem; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.03em; }
    .dbar { display:flex; height:28px; border-radius:8px; overflow:hidden; font-size:.8rem; } .dbar div { display:flex; align-items:center; padding:0 8px; white-space:nowrap; overflow:hidden; color:#fff; } .dbar b { margin-right:4px; }
    .dbar .g { background:var(--ok); } .dbar .rv, .dbar .r { background:#e12d2d; } .dbar .y { background:#f5be00; color:#14171c; }
    .tstrip.mini { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 14px; } .tstrip.mini > div { padding:7px 12px; display:flex; align-items:baseline; gap:6px; min-height:0; }
    .tstrip.mini > div > b { font-size:1rem; } .tstrip.mini > div > span { font-size:.8rem; } .tstrip.mini div.prog { width:70px; height:5px; margin:0 0 0 4px; align-self:center; }
    .morewrap { grid-column:1/-1; display:flex; justify-content:center; padding:10px 0 20px; }
    .share { margin-bottom:14px; padding:12px 16px; } .share table { width:100%; border-collapse:collapse; font-size:.9rem; }
    .share th { text-align:left; font-size:.76rem; color:var(--muted); font-weight:600; text-transform:uppercase; letter-spacing:.03em; padding:4px 10px 6px 0; }
    .share td { padding:6px 10px 6px 0; border-top:1px solid var(--line); } .share tr.you td { color:var(--fg); } .share tr.you td b { color:var(--bad); } .share p { margin:8px 0 0; font-size:.82rem; }
    .switch button[data-chk="tocheck"]::before, .switch button[data-chk="unconf"]::before, .switch button[data-chk="nolabel"]::before, .switch button[data-chk="correct"]::before { content:""; display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:7px; vertical-align:0; border:2px solid #fff; }
    .switch button[data-chk="tocheck"]::before { background:#e12d2d; } .switch button[data-chk="unconf"]::before { background:#f5be00; } .switch button[data-chk="nolabel"]::before { background:#c828aa; } .switch button[data-chk="correct"]::before { background:#1a9e6e; }
    .dot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; } .dot.r { background:#e12d2d; } .dot.y { background:#f5be00; } .dot.p { background:#c828aa; } .dot.g { background:#1a9e6e; }
    ul.why { margin:2px 0 0; padding-left:18px; font-size:.86rem; color:var(--muted); } ul.why li { margin:1px 0; }
    .k2 { display:inline-block; width:12px; height:9px; border:2.5px solid; border-radius:2px; margin:0 3px 0 6px; vertical-align:-1px; }
    .hint { margin:0 0 12px; padding:9px 14px; background:var(--accent-soft); color:var(--fg); border-radius:10px; font-size:.9rem; }
    .pur-k { font-style:normal; display:inline-block; width:14px; height:10px; border:2.5px solid #c828aa; border-radius:2px; vertical-align:-1px; margin:0 4px; }
    .lab-k { font-style:normal; display:inline-block; width:14px; height:10px; border:2.5px solid #1a9e6e; border-radius:2px; vertical-align:-1px; margin-right:4px; }
    .say .who { font-size:.76rem; text-transform:uppercase; letter-spacing:.04em; display:block; } .say b.lab { font-size:1.15rem; font-weight:600; }
    .rbtn[data-v="wrong"] { } .rcard { transition:border-color .15s, box-shadow .15s; } .rcard:hover { box-shadow:var(--shadow-hi); }
    #lb { position:fixed; inset:0; background:rgba(15,17,21,.9); display:grid; place-items:center; padding:20px; z-index:20; }
    #lb img { max-width:96vw; max-height:88vh; cursor:default; } #lb button { position:absolute; top:calc(12px + env(safe-area-inset-top,0px)); right:16px; font:500 .9rem var(--sans); border:0; border-radius:8px; padding:8px 14px; background:var(--panel); color:var(--fg); cursor:pointer; }
    @media (max-width:820px) { .rgrid { grid-template-columns:1fr; } }
    """
        JS = """
    (function(){
    var ITEMS=%(items)s, CHECKS=%(checks)s, LOCAL='label-audit';
    var state={}, db=null, chain={}, timers={}, banner=document.getElementById('banner');
    var chk=%(first)s, spl='all', filter='all';
    function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
    function st(id){return state[id]||{verdict:'',note:''};}
    var VERDICTS=%(verdicts)s, LABELS={}; Object.keys(VERDICTS).forEach(function(c){VERDICTS[c].forEach(function(p){LABELS[p[0]]=p[1];});});
    var ALLV=[]; Object.keys(VERDICTS).forEach(function(c){VERDICTS[c].forEach(function(p){if(ALLV.indexOf(p[0])<0)ALLV.push(p[0]);});});
    function card(it){var s=st(it.id);
      return '<figure class="rcard'+(s.verdict?' '+s.verdict:'')+'" data-id="'+it.id+'">'
      +'<div class="pic" data-zoom><img data-src="img/'+it.sheet+'" alt="'+esc(it.detail)+'" style="top:-'+(it.row*100)+'%%"></div>'
      +'<div class="say"><div><span class="who">'+esc(it.who||'Network team label in dataset says:')+'</span><b class="lab">'+esc(it.label)+'</b></div>'
      +'</div>'
      +(it.reasons&&it.reasons.length?'<ul class="why">'+it.reasons.map(function(r){return '<li>'+esc(r)+'</li>';}).join('')+'</ul>':'')
      +'<div class="rmeta"><span class="pill plain">'+esc(it.split)+'</span><span class="rname">'+esc(it.photo)+'</span></div>'
      +'<div class="rbtns">'+(VERDICTS[it.check]||[]).map(function(p){var v=p[0]; return '<button type="button" class="rbtn" data-v="'+v+'" aria-pressed="'+(s.verdict===v?'true':'false')+'">'+LABELS[v]+'</button>';}).join('')+'</div>'
      +'<textarea class="note" placeholder="Notes">'+esc(s.note||'')+'</textarea><div class="saved"></div></figure>';}
    function visible(it){var s=st(it.id); if(chk!=='all'&&it.check!==chk)return false; if(spl!=='all'&&it.split!==spl)return false;
      if(filter==='unmarked')return !s.verdict; if(filter!=='all')return s.verdict===filter; return true;}
    var HINTS={"tocheck":"Red: labels a label check flagged; the reason is under each card. Rack on the left with only this label outlined, the device on the right. Is the name right and does the outline fit? If not, mark Label wrong and write the right class in the note.",
      "unconf":"Yellow: labels the model does not confirm (it is not 70 %% sure of the same class). Check the name and the outline; mark Label wrong and write the right class if needed.",
      "nolabel":"Purple: areas where the model sees a device but no label is. Label right: nothing is missing here. Label wrong: a device here needs a label; write its class in the note.",
      "correct":"Green: labels the model confirms (at least 70 %% sure of the same class). A quick look is enough; mark Label wrong only if the name or the outline is wrong.","class":"Labels whose class may be wrong. Label wrong: write the right class in the note.",
      "duplicate":"One device carries two labels (green and blue). Label right: both belong there. Label wrong: one of them should go.",
      "contradiction":"A device label (green) and an Empty or Closed Unit label (blue) cover the same spot. Label wrong: one of them should go.",
      "phantom":"Labels that may sit on empty space, a shelf or a cable tray. Label wrong: there is no device to label.",
      "missing":"Areas with no label where a device may be (purple box). Label right: nothing is missing. Label wrong: a device here needs a label.",
      "agree":"Labels that are very likely right. A quick look is enough."};
    function render(){var hn=document.getElementById('hint'); if(hn){hn.textContent=HINTS[chk]||''; hn.hidden=!HINTS[chk];} var isx=!!document.getElementById(chk)&&document.getElementById(chk).classList.contains('xtab');
      document.querySelectorAll('.xtab').forEach(function(x){x.hidden=(x.id!==chk);}); document.getElementById('grid').hidden=isx;
      var v=isx?[]:ITEMS.filter(visible); var rest=v.length-lim;
      document.getElementById('grid').innerHTML=(v.slice(0,lim).map(card).join('')||'<p class="muted">Nothing in this view.</p>')+(rest>0?'<div class="morewrap"><button type="button" class="rbtn primary" id="more">Show '+Math.min(PAGE,rest)+' more ('+rest.toLocaleString()+' left)</button></div>':'');
      document.querySelectorAll('.explain').forEach(function(e){e.classList.toggle('on',chk==='all'||e.dataset.check===chk);}); counters();}
    function counters(){var n={}; ALLV.forEach(function(v){n[v]=0;}); ITEMS.forEach(function(it){var v=st(it.id).verdict; if(n[v]!==undefined)n[v]++;});
      var tot=0; ALLV.forEach(function(v){tot+=n[v]; document.getElementById('n-'+v).textContent=n[v];}); document.getElementById('n-marked').textContent=tot+' of '+ITEMS.length; var pb=document.getElementById('prog'); if(pb)pb.style.width=(ITEMS.length?100*tot/ITEMS.length:0)+'%%';}
    function refresh(id){var el=document.querySelector('.rcard[data-id="'+id+'"]'); if(!el)return; var s=st(id);
      el.className='rcard'+(s.verdict?' '+s.verdict:''); el.querySelectorAll('[data-v]').forEach(function(b){b.setAttribute('aria-pressed',b.dataset.v===s.verdict?'true':'false');});
      var ta=el.querySelector('textarea'); if(document.activeElement!==ta&&ta.value!==(s.note||''))ta.value=s.note||''; counters();}
    function note(id,txt){var el=document.querySelector('.rcard[data-id="'+id+'"] .saved'); if(el)el.textContent=txt;}
    function persist(id){var s=state[id]; var it=ITEMS.filter(function(x){return x.id===id;})[0]; s.photo=it?it.photo:id; s.split=it?it.split:''; s.check=it?it.check:''; s.classes=it?it.classes:[]; s.updated=new Date().toISOString();
      if(!db){try{localStorage.setItem(LOCAL,JSON.stringify(state));}catch(e){} note(id,'kept in this browser only'); return;}
      note(id,'saving...'); chain[id]=(chain[id]||Promise.resolve()).then(function(){return db.doc('label_audit/'+id).set(s);}).then(function(){note(id,'saved');}).catch(function(e){note(id,'not saved: '+(e&&e.code||'error'));});}
    function save(id,patch){var s=st(id); var next={verdict:s.verdict||'',note:s.note||''}; for(var k in patch)next[k]=patch[k];
      if(next.verdict===(s.verdict||'')&&next.note===(s.note||''))return; state[id]=next; refresh(id); persist(id);}
    document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('[data-v]'); if(!b)return; var id=b.closest('.rcard').dataset.id; var v=b.dataset.v; save(id,{verdict:st(id).verdict===v?'':v});});
    document.addEventListener('input',function(e){if(!e.target.classList||!e.target.classList.contains('note'))return; var id=e.target.closest('.rcard').dataset.id; clearTimeout(timers[id]); var v=e.target.value; timers[id]=setTimeout(function(){save(id,{note:v});},700);});
    document.addEventListener('focusout',function(e){if(!e.target.classList||!e.target.classList.contains('note'))return; var id=e.target.closest('.rcard').dataset.id; clearTimeout(timers[id]); save(id,{note:e.target.value});});
    var PAGE=150, lim=PAGE;
    document.addEventListener('click',function(e){if(e.target&&e.target.id==='more'){lim+=PAGE;render();}});
    function group(attr,setter){document.querySelectorAll('['+attr+']').forEach(function(b){b.addEventListener('click',function(){setter(b.getAttribute(attr)); lim=PAGE; document.querySelectorAll('['+attr+']').forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false');}); render();});});}
    group('data-chk',function(v){chk=v;}); group('data-spl',function(v){spl=v;}); group('data-filter',function(v){filter=v;});
    document.getElementById('export').addEventListener('click',function(){var out={notes:{}}; ALLV.forEach(function(v){out[v]=[];});
      ITEMS.forEach(function(it){var s=st(it.id); if(out[s.verdict])out[s.verdict].push(it.id); if(s.note&&s.note.trim())out.notes[it.id]=s.note.trim();});
      var txt=JSON.stringify(out,null,1); var ta=document.getElementById('exportbox'); ta.value=txt; ta.hidden=false; ta.focus(); ta.select();
      try{navigator.clipboard.writeText(txt).then(function(){banner.textContent='Copied to the clipboard (and shown below).';banner.className='banner ok';banner.hidden=false;}).catch(function(){});}catch(e){}});
    var lb=document.getElementById('lb'),box=document.getElementById('lbpic'),im=document.getElementById('lbimg');
    document.addEventListener('click',function(e){var i=e.target.closest&&e.target.closest('[data-zoom]');if(i&&!lb.contains(i)){e.preventDefault();var src=i.querySelector('img');
      lb.hidden=false; box.style.height=Math.round(box.clientWidth*%(tile_h)d/%(tile_w)d)+'px'; im.src=src.getAttribute('src')||src.getAttribute('data-src'); im.style.top=src.style.top;}});
    var LZ=('IntersectionObserver' in window)?new IntersectionObserver(function(es){es.forEach(function(e){if(e.isIntersecting){var im=e.target.querySelector('img[data-src]');if(im){im.src=im.getAttribute('data-src');im.removeAttribute('data-src');}LZ.unobserve(e.target);}});},{rootMargin:'1500px 0px'}):null;
    function lazy(){document.querySelectorAll('.pic img[data-src]').forEach(function(im){if(LZ)LZ.observe(im.parentNode);else{im.src=im.getAttribute('data-src');im.removeAttribute('data-src');}});}
    new MutationObserver(function(){lazy();}).observe(document.body,{childList:true,subtree:true}); lazy();
    // download: every card this reviewer marked, as marks.csv + the card pictures in one zip
    var DL=null, dlBtn=document.getElementById('dl'), TW=%(tile_w)d, TH=%(tile_h)d, FNAME=%(fname)s;
    function crc32(u){var t=crc32.t,c;if(!t){t=crc32.t=[];for(var n=0;n<256;n++){c=n;for(var k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;t[n]=c>>>0;}}c=0xFFFFFFFF;for(var i=0;i<u.length;i++)c=t[(c^u[i])&255]^(c>>>8);return (c^0xFFFFFFFF)>>>0;}
    function zip(files){var enc=new TextEncoder(),parts=[],central=[],off=0,csize=0;
      files.forEach(function(f){var nm=enc.encode(f.name),d=f.data,crc=crc32(d),h=new DataView(new ArrayBuffer(30)),c=new DataView(new ArrayBuffer(46));
        h.setUint32(0,0x04034b50,true);h.setUint16(4,20,true);h.setUint16(6,0x0800,true);h.setUint16(12,0x21,true);h.setUint32(14,crc,true);h.setUint32(18,d.length,true);h.setUint32(22,d.length,true);h.setUint16(26,nm.length,true);
        c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(8,0x0800,true);c.setUint16(14,0x21,true);c.setUint32(16,crc,true);c.setUint32(20,d.length,true);c.setUint32(24,d.length,true);c.setUint16(28,nm.length,true);c.setUint32(42,off,true);
        parts.push(h.buffer,nm,d);central.push(c.buffer,nm);off+=30+nm.length+d.length;csize+=46+nm.length;});
      var e=new DataView(new ArrayBuffer(22));e.setUint32(0,0x06054b50,true);e.setUint16(8,files.length,true);e.setUint16(10,files.length,true);e.setUint32(12,csize,true);e.setUint32(16,off,true);
      return new Blob(parts.concat(central,[e.buffer]),{type:'application/zip'});}
    var sheets={};
    function sheet(src){return sheets[src]||(sheets[src]=new Promise(function(res,rej){var im=new Image();im.onload=function(){res(im);};im.onerror=rej;im.src=src;}));}
    function tile(it){return sheet('img/'+it.sheet).then(function(im){var cv=document.createElement('canvas');cv.width=TW;cv.height=TH;
      cv.getContext('2d').drawImage(im,0,it.row*TH,TW,TH,0,0,TW,TH);return new Promise(function(r){cv.toBlob(function(b){b.arrayBuffer().then(function(a){r(new Uint8Array(a));});},'image/jpeg',0.92);});});}
    function csvq(v){v=String(v==null?'':v);return /[",\\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}
    function plain(name,blob){var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;document.body.appendChild(a);a.click();setTimeout(function(){URL.revokeObjectURL(a.href);a.remove();},60000);return Promise.resolve({status:'plain'});}
    function offer(name,blob){if(!DL)return plain(name,blob);   // shared links opened on their own get no viewer save: a normal browser download
      return DL.save({filename:name,data:blob}).catch(function(e){var c=e&&e.code;if(c==='declined'||c==='rate_limited'||c==='too_large')throw e;return plain(name,blob);});}
    function say(t,ok){banner.textContent=t;banner.className='banner'+(ok?' ok':'');banner.hidden=false;}
    if(window.claude&&window.claude.use){window.claude.use('downloads').then(function(d){DL=d;}).catch(function(){});}   // the button stays for everyone
    dlBtn.addEventListener('click',function(){
      var picked=ITEMS.filter(function(it){var s=st(it.id);return s.verdict||(s.note&&s.note.trim());});
      if(!picked.length){say('Nothing marked yet: mark some cards, then download.');return;}
      dlBtn.disabled=true;var files=[],rows=[['card','tab','photo','network_team_label','your_mark','note','picture']],k=0;
      (function next(){if(k>=picked.length){
          files.unshift({name:'marks.csv',data:new TextEncoder().encode('\\ufeff'+rows.map(function(r){return r.map(csvq).join(',');}).join('\\r\\n'))});
          var blob=zip(files),name=FNAME+'-'+new Date().toISOString().slice(0,10)+'.zip';
          offer(name,blob).then(function(r){say((r&&r.status==='plain'?'Downloaded ':'Saved ')+picked.length+' marked cards with their pictures as '+name+'.',true);})
            .catch(function(e){var c=e&&e.code;say(c==='declined'?'Download cancelled.':c==='rate_limited'?'A save is already open: finish it, then try again.':'Downloads are not available in this view: use "Copy marks and notes".');})
            .then(function(){dlBtn.disabled=false;dlBtn.textContent='Download my marks with pictures';});return;}
        var it=picked[k],s=st(it.id),mark=LABELS[s.verdict]||'Note only',dir=mark.replace(/[^A-Za-z]+/g,'_'),pic=dir+'/'+String(k+1).padStart(4,'0')+'_'+it.photo.replace(/\\.[^.]+$/,'').replace(/[^A-Za-z0-9_.-]+/g,'_')+'.jpg';
        rows.push([it.id,(CHECKS[it.check]||[it.check])[0],it.photo,it.label,mark,s.note||'',pic]);
        dlBtn.textContent='Preparing '+(k+1)+' of '+picked.length+'...';
        tile(it).then(function(d){files.push({name:pic,data:d});}).catch(function(){rows[rows.length-1][6]='(picture not loaded)';}).then(function(){k++;next();});})();});
    function close(){lb.hidden=true;im.src='';} document.getElementById('lbclose').addEventListener('click',close);
    lb.addEventListener('click',function(e){if(e.target===lb)close();}); document.addEventListener('keydown',function(e){if(e.key==='Escape')close();});
    try{var loc=JSON.parse(localStorage.getItem(LOCAL)||'{}'); for(var k in loc)state[k]=loc[k];}catch(e){}
    render();
    if(window.claude&&window.claude.use){window.claude.use('db').then(function(d){db=d;
      if(!db){banner.textContent='The shared record is not available in this view: marks and notes stay in this browser. Use "Copy marks and notes" to hand them over.';banner.hidden=false;return;}
      db.collection('label_audit').onSnapshot(function(snap){snap.docs.forEach(function(doc){if(doc.exists){var d=doc.data(); state[doc.id]={verdict:d.verdict||'',note:d.note||''}; refresh(doc.id);}}); counters();},
        function(err){banner.textContent='The shared record stopped answering ('+(err&&err.code||'error')+'): marks made now stay in this browser.';banner.hidden=false;db=null;});
    }).catch(function(){});}
    else{banner.textContent='Opened outside the viewer: marks and notes stay in this browser.';banner.hidden=false;}
    })();
    """
        rows_html = "".join(f"<tr><td>{html.escape(CHECKS[c][0])}</td>"
                            + "".join(f"<td>{counts[c][s]:,}</td>" for s in SPLITS) + f"<td>{sum(counts[c].values()):,}</td></tr>" for c in counts)
        heads = "".join(f'<tr><th>Check</th>{"".join(f"<th>{s}</th>" for s in SPLITS)}<th>All</th></tr>' for _ in [0])
        explains = "".join(f'<div class="card explain on" data-check="{c}"><h3>{html.escape(CHECKS[c][0])}</h3><p>{html.escape(CHECKS[c][1])}</p><p class="muted">{html.escape(CHECKS[c][2])}' + (f" · showing {shown[c]} of {sum(counts[c].values()):,}" if shown[c] < sum(counts[c].values()) else "") + "</p></div>" for c in checks)
        chk_buttons = "".join(f'<button type="button" data-chk="{c}" aria-pressed="false">{html.escape(CHECKS[c][0])} ({shown[c]})</button>' for c in checks)
        allv = []
        for vs in verdicts.values():
            for v, lab in vs:
                if v not in dict(allv):
                    allv.append((v, lab))
        strip_v = "".join(f'<div><b id="n-{v}" class="{v}">0</b><span>{html.escape(lab.lower())}</span></div>' for v, lab in allv)
        filter_v = "".join(f'<button type="button" data-filter="{v}" aria-pressed="false">{html.escape(lab)}</button>' for v, lab in allv)
        choices = ", ".join(lab for _, lab in allv[:-1]) + f", or {allv[-1][1]}"
        tabs = "".join(f'<button type="button" data-chk="{c}" aria-pressed="{"true" if k == 0 else "false"}">{html.escape(CHECKS[c][0])} ({shown[c]:,})</button>' for k, c in enumerate(checks))
        src_fp = RES / "sources.json"
        proof = proof_html(json.load(open(src_fp)), A, shown, part_note) if src_fp.exists() else ""
        if proof:
            tabs += '<button type="button" data-chk="proof" aria-pressed="false">Sources and proof</button>'
        iss_fp = RES / "export_issues.json"
        issue = issue_html(json.load(open(iss_fp)), json.load(open(src_fp))) if (iss_fp.exists() and src_fp.exists() and REMAP) else ""
        if issue:
            tabs += '<button type="button" data-chk="issue" aria-pressed="false">Export 429_v6 numbering</button>'
        spl_buttons = "".join(f'<button type="button" data-spl="{s}" aria-pressed="false">{s} ({sum(it["split"] == s for it in items)})</button>' for s in SPLITS)
        split_switch = ('<div class="switch" role="group" aria-label="Split"><button type="button" data-spl="all" aria-pressed="true">All splits</button>' + spl_buttons + "</div>") if len(SPLITS) > 1 else ""
        CSS = CSS.replace("PADPCT", f"{100 * TILE_H / TILE_W:.2f}%")
        mine = ""
        if TOT:
            M_ = tally(items)
            row = lambda name, t, cls: (f'<tr class="{cls}"><td>{name}</td><td>{t["photos"]:,}</td><td>{t["inst"]:,}</td><td><i class="dot r"></i>{t["red"]:,}</td>'
                                        f'<td><i class="dot y"></i>{t["yel"]:,}</td><td><i class="dot p"></i>{t["areas"]:,}</td><td><i class="dot g"></i>{t["ok"]:,}</td></tr>')
            mine = (f'<div class="card share"><table><tr><th></th><th>Photos</th><th>Label instances</th><th>To check</th><th>Not confirmed</th><th>No label (areas)</th><th>100 % correct</th></tr>'
                    + row("All reviewers (photos of 1 MP or more)", TOT, "all") + row(f"<b>You: reviewer {part + 1} of {parts}</b>", M_, "you")
                    + f'</table><p class="muted">To check: flagged by a label check. Not confirmed: the model is not {CORRECT_CONF:.0%} sure of the same class. No label: the model is {FINDER_SURE:.0%} sure of a device where no label is. 100 % correct: the model is at least {CORRECT_CONF:.0%} sure of the same class. Photos under 1 MP ({n_small:,}) are not part of this review.</p></div>')
        page_html = f"""<title>{title}{part_name}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
    <style>{CSS}</style>
    <div class="page">
    <header><div class="bar-top"><h1 class="ptitle">{title}{part_name}<small>Network team device labels{(" · review " + part_note.rstrip(", ")) if part_note else ""}</small></h1><nav><span class="muted">{n_photos:,} photos · {n_boxes:,} labels audited</span></nav></div></header>
    <section>
    {mine}
    <div class="tstrip mini"><div><b>{len(items):,}</b><span>{html.escape(question)} on this page{(": " + ", ".join(f"{shown.get(c, 0):,} {CHECKS[c][0].lower()}" for c in checks if c in shown)) if "correct" in shown else ""}</span></div><div><b id="n-marked">0 of {len(items)}</b><span>checked so far</span><div class="prog"><div id="prog"></div></div></div>{strip_v}</div>
    <div id="banner" class="banner" hidden></div>
    <div class="toolbar">
    <div class="switch" role="group" aria-label="Tab">{tabs}</div>
    {split_switch}
    <div class="switch" role="group" aria-label="Show"><button type="button" data-filter="all" aria-pressed="true">All</button><button type="button" data-filter="unmarked" aria-pressed="false">Unchecked</button>{filter_v}</div>
    <button type="button" class="rbtn primary" id="dl">Download my marks with pictures</button>
    <button type="button" class="rbtn" id="export">Copy marks and notes</button>
    </div>
    <textarea id="exportbox" class="note" hidden readonly aria-label="Marks and notes as text"></textarea>
    <p id="hint" class="hint"></p>
    <div id="grid" class="rgrid"></div>
    {proof}
    {issue}
    </section>
    </div>
    <div id="lb" hidden><button type="button" id="lbclose">Close</button><div id="lbpic"><img id="lbimg" alt="Enlarged picture"></div></div>
    <script>{JS % {"items": json.dumps([{k: v for k, v in it.items() if k not in ("model", "conf", "finder", "detail")} for it in items]), "checks": json.dumps({c: [t] for c, (t, _, _) in CHECKS.items()}), "verdicts": json.dumps(verdicts), "first": json.dumps(checks[0]), "tile_w": TILE_W, "tile_h": TILE_H, "fname": json.dumps(re.sub(r"[^a-z0-9]+", "-", (title + part_name).lower()).strip("-"))}}</script>
    """
        (out_dir / "index.html").write_text(page_html)
        json.dump([f"img/{n}" for n in sheets], open(out_dir / "sheets.json", "w"))
        print(f"written {out_dir / 'index.html'} | {len(items)} cards {shown} | {len(sheets)} sheet pictures of {PER_SHEET} tiles")






# Export 429_v6 numbers its classes in another order than its own data.yaml: Empty moved to the end, so ids 1-10 point one class
# too low and 11 means Empty (proven 2026-10-19 without any model: the same devices labelled in other exports carry id + 1; see
# `issues`). The user asked to remap it in our copy; the export itself stays untouched.
REMAP = {"Device_annotation_not_trained_429_v6": {0: 0, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 11, 11: 1}}


def remapped(ds, ls):
    m = REMAP.get(ds)
    return ls if not m else [(str(m[int(l[0])]),) + tuple(l[1:]) for l in ls]


def _md5(fp):
    h = hashlib.md5()
    with open(fp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _lines(fp):
    return [tuple(v.split()) for v in fp.read_text(errors="replace").split("\n") if v.strip()] if fp.exists() else []


MANIFEST = DATA / "manifest.csv"        # one row per photo: its export file and hashes (written by `merge`)
PROVENANCE = DATA / "provenance"         # the exports' data.yaml / README files and every original export label file (one zip)


def verify():
    """The dataset against what the network team exported, now that the exports are deleted: every image's md5 against
    manifest.csv, every label file against the original export label file in provenance/export_labels.zip (polygon lines,
    ids through REMAP), and every export's data.yaml class list. Updates the dataset facts in results/devices/sources.json."""
    import csv as _csv, zipfile
    from eval_step3 import NAMES
    rows = list(_csv.DictReader(open(MANIFEST))); z = zipfile.ZipFile(PROVENANCE / "export_labels.zip"); zn = set(z.namelist())
    def zlines(ei):
        q = pathlib.PurePosixPath(ei); key = str(q.parent.parent / "labels" / (q.stem + ".txt"))
        return [tuple(v.split()) for v in z.read(key).decode(errors="replace").split("\n") if v.strip()] if key in zn else None
    ok_img = ok_lab = same = boxf = boxl = renum = 0; per = collections.defaultdict(collections.Counter); fing = []
    for r in rows:
        img = DATA / "images" / r["photo"]; lf = DATA / "labels" / (pathlib.Path(r["photo"]).stem + ".txt")
        h = _md5(img) if img.exists() else ""; ok_img += h == r["image_md5"]; now = _lines(lf); raw = zlines(r["export_image"]) or []
        poly = [l for l in raw if len(l) != 5]; want = remapped(r["export"], poly); good = now == want
        ok_lab += good; same += now == raw; boxf += len(poly) != len(raw); boxl += len(raw) - len(poly); renum += r["export"] in REMAP
        per[r["export"]]["images"] += 1; per[r["export"]]["identical"] += good; per[r["export"]]["review"] += r["in_review"] == "yes"
        fing += [f"images/{r['photo']}\t{h}", f"labels/{lf.name}\t{_md5(lf) if lf.exists() else '-'}"]
    yamls = {f.name.split("__")[0]: f.read_text() for f in PROVENANCE.glob("*__data.yaml")}
    def yl(v):
        m = re.search(r"^names:\s*(\[.*\])\s*$", v, re.M)
        return [str(x) for x in ast.literal_eval(m.group(1))] if m else [mm.group(1).strip().strip("'\"") for mm in re.finditer(r"^\s+\d+:\s*(.+?)\s*$", v, re.M)]
    lists = {k: yl(v) for k, v in yamls.items()}
    first = next(iter(lists.values()))
    S = json.load(open(RES / "sources.json"))
    n_rev = sum(r["in_review"] == "yes" for r in rows)
    S.update(made=time.strftime("%Y-%m-%d %H:%M"), classes_same_in_every_yaml=all(v == first for v in lists.values()),
             code_list_matches=[NAMES[i] for i in range(len(NAMES))] == first, sources_folder=str(PROVENANCE / "export_labels.zip"),
             review={"min_pixels": REVIEW_MIN_PIXELS, "photos": n_rev})
    for x in S["exports"]:
        c = per.get(x["name"], {})
        x.update(kept=c.get("review", 0), removed=c.get("images", 0) - c.get("review", 0), identical=c.get("identical", 0), polygons_identical=c.get("identical", 0))
    S["merged"].update(folder=str(DATA), photos=len(rows), identical_labels=same, polygons_identical=ok_lab, without_source=len(rows) - ok_img,
                       box_lines_left_out=boxl, box_files=boxf, renumbered_files=renum, files=len(fing),
                       fingerprint=hashlib.sha256("\n".join(sorted(fing)).encode()).hexdigest())
    S["removed"] = {"n": 0, "with_copy": 0, "identical_labels": 0, "rule": "", "date": "", "old_split": ""}
    json.dump(S, open(RES / "sources.json", "w"), indent=1)
    print(f"{len(rows):,} photos: images identical to the export {ok_img:,}; label files = export polygons (through the table) {ok_lab:,}; "
          f"byte for byte the export's {same:,}; box lines left out {boxl:,} in {boxf:,} files; renumbered (429_v6) {renum:,}; "
          f"same class list in all {len(lists)} data.yaml: {S['classes_same_in_every_yaml']}; in the review (1 MP or more) {n_rev:,}")


def fingerprint():
    """{split/label file: (size, mtime)} for every label file."""
    out = {}
    for split in SPLITS:
        d = DATA / split / "labels"
        if d.exists():
            for p in d.iterdir():
                if p.suffix == ".txt" and not p.name.startswith("._"):
                    st = p.stat(); out[f"{split}/{p.name}"] = [st.st_size, round(st.st_mtime, 3)]
    return out


def run():
    """Everything, incrementally: the second opinion for photos not cached yet, every check, the CSV and the page."""
    from datetime import datetime
    state_fp = RES / "label_audit_state.json"
    before = json.load(open(state_fp))["labels"] if state_fp.exists() else {}
    now = fingerprint()
    new = [k for k in now if k not in before]; changed = [k for k in now if k in before and before[k] != now[k]]; gone = [k for k in before if k not in now]
    print(f"label files: {len(now)} ({len(new)} new, {len(changed)} changed, {len(gone)} removed since the last run)", flush=True)
    for split in SPLITS:
        if (DATA / split / "images").exists():
            cache(split)
    check(); page()
    json.dump({"ran": datetime.now().isoformat(timespec="seconds"), "labels": now, "new": new, "changed": changed, "removed": gone}, open(state_fp, "w"))


def watch(minutes=10):
    run()
    while True:
        time.sleep(minutes * 60)
        state = json.load(open(RES / "label_audit_state.json"))["labels"]
        if fingerprint() != state:
            print(f"{time.strftime('%H:%M')} label files changed: running the audit again", flush=True); run()


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "cache":
        cache(sys.argv[2] if len(sys.argv) > 2 else SPLITS[0])
    elif cmd == "check":
        check()
    elif cmd in ("verify", "sources"):
        verify()
    elif cmd == "page":
        page(int(sys.argv[2]) if len(sys.argv) > 2 else PARTS)
    elif cmd == "run":
        run()
    elif cmd == "watch":
        watch(float(sys.argv[2]) if len(sys.argv) > 2 else 10)
    else:
        raise SystemExit(f"unknown command {cmd}")
