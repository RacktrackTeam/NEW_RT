"""Rack occupancy pipeline.

1. Rows   - the RT-EOS unit ladder (src/unit_grid.py, identical to RT-EOS pipeline/detection.py)
            reads every rack row from weights/rack_unit_finder.pt, numbered u01 at the BOTTOM.
2. Crop   - each row is cut out and squashed to 224x224, exactly like the training crops.
3. Classify - the chosen approach returns P(occupied) per row.
"""
import local_models  # noqa: F401  (model caches inside this folder, hub lookups off)
import json
import os
import pathlib
import warnings

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import unit_grid as ug

# numpy on Apple Accelerate emits spurious float32 matmul FP warnings from sklearn heads; results are unaffected.
warnings.filterwarnings("ignore", category=RuntimeWarning, module="sklearn.utils.extmath")

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"
DETECTOR = ROOT / "weights/rack_unit_finder.pt"
DEVICE = "mps"

OCC_RGB = (231, 76, 60)     # occupied   = red
FREE_RGB = (39, 174, 96)    # unoccupied = green
PICK_RGB = (52, 152, 219)   # highlight  = blue
UNC_RGB = (230, 145, 20)    # uncertain  = amber
STATUS_RGB = {"occupied": OCC_RGB, "unoccupied": FREE_RGB, "uncertain": UNC_RGB}
# When the classifier calls a unit free but the RT-EOS device finder sees a device over it:
#   "warn"     -> the unit is UNCERTAIN, with the device named, so a person checks it (teammate 1 v2's rule;
#                 the team's choice for every approach since 8 Oct 2026);
#   "overrule" -> the device finder's answer is used (the earlier behaviour).
DEVICE_DISAGREEMENT = "warn"
# Device-finder classes that mean "no device here": a blank (closed) panel and an open slot are both unoccupied.
FREE_DEVICE_CLASSES = {"empty", "closed unit"}
UNCERTAIN_BELOW = 0.65      # classifier less sure than this either way -> "uncertain" (teammate's cut-off)


# ---------------------------------------------------------------- detection


def detect_units(img: Image.Image, conf: float = 0.25):
    """The rack's rows exactly as RT-EOS identifies them (same calls as
    unit_grid.build_unit_grid_from_model), plus where each row came from.

    Returns rows top-to-bottom:
      [{'unit': 'u12', 'box': (x1,y1,x2,y2), 'source': 'detected'|'inferred', 'det_conf': float|None}]
    'inferred' rows were added by the ladder rules (a missed row filled in, or the frame
    continuing past the last row the model saw).
    """
    bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    model = ug.load_model(str(DETECTOR))
    small, factor = ug.shrink_for_inference(bgr)
    res = model.predict(small, conf=conf, imgsz=768, verbose=False)
    if not res:
        return []
    raw, racks, rails = ug._unit_rows_from_result(res[0], getattr(model, "names", {}) or {})
    if factor != 1:
        for u in raw:
            u["box"] = [v * factor for v in u["box"]]
        racks = [[v * factor for v in r] for r in racks]
        rails = [[v * factor for v in r] for r in rails]
    if not raw:
        return []
    raw_kept = ug._drop_duplicate_units([dict(u) for u in raw])
    ladder = ug.ladder_from_unit_boxes(raw, racks, bgr.shape[0], bgr.shape[1], rails)

    out = []
    for r in ladder:
        y1, y2 = r["box"][1], r["box"][3]
        best, best_ov = None, 0.0
        for u in raw_kept:
            ov = max(0.0, min(y2, u["box"][3]) - max(y1, u["box"][1])) / max(1.0, y2 - y1)
            if ov > best_ov:
                best, best_ov = u, ov
        detected = best is not None and best_ov >= 0.5
        out.append({"unit": r["label"], "box": tuple(int(v) for v in r["box"]),
                    "source": "detected" if detected else "inferred",
                    "det_conf": float(best["confidence"]) if detected else None})
    return out


def crop_unit(img: Image.Image, box, pad_frac: float = 0.02) -> Image.Image:
    x1, y1, x2, y2 = box
    pw, ph = (x2 - x1) * pad_frac, (y2 - y1) * pad_frac
    x1, y1 = max(0, int(x1 - pw)), max(0, int(y1 - ph))
    x2, y2 = min(img.width, int(x2 + pw)), min(img.height, int(y2 + ph))
    return img.crop((x1, y1, x2, y2)).convert("RGB").resize((224, 224), Image.BILINEAR)


# ---------------------------------------------------------------- approaches
class Approach:
    key = ""
    name = ""
    short = ""
    description = ""
    weights: pathlib.Path
    eval_file: pathlib.Path

    def available(self):
        return self.weights.exists()

    def metrics(self):
        return json.load(open(self.eval_file)) if self.eval_file.exists() else None

    def predict(self, crops):
        """crops: list of 224x224 PIL -> np.array of P(occupied)."""
        raise NotImplementedError

    def unload(self):
        """Drop this approach's loaded models so their memory can be reused."""
        for attr in ("_m", "_clf", "_embed", "clip"):
            if getattr(self, attr, None) is not None:
                setattr(self, attr, None)
        if hasattr(self, "_ready"):
            self._ready = False


def free_memory():
    import gc
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass


DATASETS = {"v2": "v2 · crops_occupied_vs_unoccupied"}


def model_dir(version):
    return MODELS / version


class YoloClsApproach(Approach):
    """A fine-tuned YOLO classification network (YOLOv8m or YOLO11s)."""
    INFO = {
        "yolov8m_cls": ("YOLOv8m-cls (fine-tuned CNN)", "YOLOv8m",
                        "A YOLOv8m classification network fine-tuned end-to-end on the training crops. "
                        "It learns task-specific cues - bezels, LEDs, ports, drive bays versus cable runs, "
                        "shelves, blank panels and open space."),
        "yolo11s_cls": ("YOLO11s-cls (fine-tuned CNN)", "YOLO11s",
                        "The smaller YOLO11s classification network, fine-tuned on the same crops."),
    }

    def __init__(self, key="yolov8m_cls", version="v2"):
        self.key, self.version = key, version
        self.name, self.short, self.description = self.INFO[key]
        self.weights = model_dir(version) / f"{key}.pt"
        self.eval_file = model_dir(version) / f"{key}_eval.json"
        self._m = None

    def predict(self, crops):
        if self._m is None:
            from ultralytics import YOLO
            self._m = YOLO(str(self.weights))
            self._occ = [k for k, v in self._m.names.items() if v == "occupied"][0]
        if not crops:
            return np.zeros(0)
        res = self._m.predict(crops, imgsz=224, device=DEVICE, verbose=False)
        return np.array([float(r.probs.data[self._occ]) for r in res])


class EmbedApproach(Approach):
    """Frozen foundation-model embeddings + small head (DINOv2 or CLIP)."""
    INFO = {
        "dinov2": ("DINOv2 ViT-B/14 + classifier head", "DINOv2",
                   "A frozen DINOv2 ViT-B/14 encoder (self-supervised, 768-d features) with a small "
                   "classifier head. Its features keep fine visual structure such as vents, port rows and bezel edges."),
        "clip": ("CLIP ViT-B/32 + classifier head", "CLIP",
                 "A frozen CLIP ViT-B/32 image encoder (512-d semantic features) with a small classifier head."),
    }

    def __init__(self, kind="dinov2", version="v2"):
        self.kind, self.version = kind, version
        self.key = f"embed_{kind}"
        self.name, self.short, self.description = self.INFO[kind]
        self.weights = model_dir(version) / f"embed_{kind}_clf.joblib"
        self.eval_file = model_dir(version) / f"embed_{kind}_eval.json"
        self._clf = None

    def predict(self, crops):
        if self._clf is None:
            import joblib
            from train_embed import load_backbone
            # joblib pickle of our own locally trained sklearn head - trusted artefact.
            self._clf = joblib.load(self.weights)["clf"]
            self._embed = load_backbone(self.kind)[1]
        if not crops:
            return np.zeros(0)
        X = np.concatenate([self._embed(crops[i:i + 128]) for i in range(0, len(crops), 128)])
        return self._clf.predict_proba(X)[:, 1]


class ClassicalApproach(Approach):
    key = "classical"
    name = "Classical features + tree ensemble"
    short = "Classical"
    description = ("No deep learning: ~84 hand-crafted colour, edge and texture statistics per unit feed a "
                   "tree-ensemble classifier.")

    def __init__(self, version="v2"):
        self.version = version
        self.weights = model_dir(version) / "classical_clf.joblib"
        self.eval_file = model_dir(version) / "classical_eval.json"
        self._clf = None

    def predict(self, crops):
        if self._clf is None:
            import joblib
            # joblib pickle of our own locally trained sklearn model - trusted artefact.
            self._clf = joblib.load(self.weights)["clf"]
        if not crops:
            return np.zeros(0)
        from train_classical import features
        X = np.stack([features(cv2.cvtColor(np.array(c.convert("RGB")), cv2.COLOR_RGB2BGR)) for c in crops])
        return self._clf.predict_proba(X)[:, 1]


class EnsembleApproach(Approach):
    key = "ensemble"
    name = "Ensemble (average of all approaches)"
    short = "Ensemble"
    description = "Averages P(occupied) from every other approach that is ready."

    def __init__(self, members, version="v2"):
        self.members, self.version = members, version
        self.weights = model_dir(version)
        self.eval_file = model_dir(version) / "ensemble_eval.json"

    def available(self):
        return any(m.available() for m in self.members)

    def predict(self, crops):
        return np.mean([m.predict(crops) for m in self.members if m.available()], axis=0)


def build_approaches(version="v2"):
    """Every approach, trained on the v2 dataset."""
    from teammate_approach import TeammateApproach
    singles = [YoloClsApproach("yolov8m_cls", version), YoloClsApproach("yolo11s_cls", version),
               EmbedApproach("dinov2", version), EmbedApproach("clip", version), TeammateApproach(version),
               ClassicalApproach(version)]
    # the ensemble averages five of them: the classical-features model is left out (user, 2026-10-19), which also
    # scores better: test 96.6 -> 96.9 %, valid 96.25 -> 96.50 %, units sent to a person 84 -> 72
    out = singles + [EnsembleApproach([m for m in singles if m.key != "classical"], version)]
    if version == "v2":
        from teammate1v2_approach import KnnV2Approach    # teammate 1, version 2: k-NN + uncertainty features
        from teammate2_approach import FusionApproach      # teammate 2: v2 semantics (closed panel = empty)
        out.insert(len(singles), FusionApproach(version))
        out.insert(len(singles), KnnV2Approach(version))
    return out


# ---------------------------------------------------------------- RT-EOS device cross-check
# RT-EOS's device detector (same file, same settings as RT-EOS config.json: devices_conf 0.2,
# iou_dedup 0.5, run through the same shrink-for-inference). It sees whole devices on the full
# photo, so it catches what a row crop can miss: shelf-mounted UPS / NAS boxes, multi-U chassis.
# RT-EOS device finder: the copy shipped in this folder, else the RT-EOS checkout on this machine.
DEVICE_FINDER = next((p for p in (ROOT / "weights/device_finder.pt", pathlib.Path(os.environ.get("RACKTRACK_REPO", ROOT / "rt_eos")) / "Models" / "device_finder.pt")
                      if p.exists()), ROOT / "weights/device_finder.pt")
DEVICE_CONF = 0.2
DEVICE_TRUST = 0.4      # only detections at least this confident override the classifier
ROW_COVER = 0.5         # a device must cover at least half a row's height to claim it


def device_finder_available():
    return DEVICE_FINDER.exists()


def detect_devices(img: Image.Image):
    """[{'class_name', 'confidence', 'box'}] from the RT-EOS device detector, in image pixels."""
    if not device_finder_available():
        return []
    bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    model = ug.load_model(str(DEVICE_FINDER))
    small, factor = ug.shrink_for_inference(bgr)
    res = model.predict(small, conf=DEVICE_CONF, iou=0.5, verbose=False)
    if not res or res[0].boxes is None:
        return []
    names = getattr(model, "names", {}) or {}
    out = []
    for b, c, s in zip(res[0].boxes.xyxy.tolist(), res[0].boxes.cls.tolist(), res[0].boxes.conf.tolist()):
        raw = str(names.get(int(c), c)).strip()
        cname = {"pdu": "PDU", "ups": "UPS", "psu": "PSU"}.get(raw.lower(), raw.title())
        out.append({"class_name": cname, "confidence": float(s),
                    "box": [v * factor for v in b]})
    return out


def _row_device(row, devices):
    """Most confident trusted device covering >= ROW_COVER of the row's height, or None."""
    y1, y2 = row["box"][1], row["box"][3]
    best = None
    for d in devices:
        if d["confidence"] < DEVICE_TRUST:
            continue
        cover = max(0.0, min(y2, d["box"][3]) - max(y1, d["box"][1])) / max(1.0, y2 - y1)
        if cover >= ROW_COVER and (best is None or d["confidence"] > best["confidence"]):
            best = d
    return best


# ---------------------------------------------------------------- end-to-end
def classify_units(img, units, approach, thresh: float = 0.5, devices=None, side_angle_check: bool = True):
    """Label every row occupied / unoccupied / uncertain and say why when a person should look.

    The classifier gives P(occupied). When the RT-EOS device detector confidently sees something over the row:
      * device_disagreement = "warn" (default, DEVICE_DISAGREEMENT): a device over a row the classifier calls
        free is NOT overruled; the row becomes uncertain with the device named, so a person checks it.
        An empty slot or closed (blank) panel seen by the detector always means unoccupied (FREE_DEVICE_CLASSES);
      * device_disagreement = "overrule": the detector's answer is used.
    A row is also uncertain when the classifier alone decided and is less than `uncertain_below` sure. On a photo
    taken from a side angle (src/side_angle.py) it must be at least side_angle.SIDE_SURE sure instead.
    Each row gets `attention` ("warning" / "uncertain" / "info" / None) and a plain-English `reason`.
    """
    crops = [crop_unit(img, u["box"]) for u in units]
    kinds, notes = [None] * len(units), [{}] * len(units)
    if hasattr(approach, "predict_with_context"):      # approaches that also look at the whole photo
        out = approach.predict_with_context(img, units, devices or detect_devices(img))
        p, kinds = out[0], out[1]
        if len(out) > 2:
            notes = out[2]
    else:
        p = approach.predict(crops)
    mode = getattr(approach, "device_disagreement", DEVICE_DISAGREEMENT)
    below = getattr(approach, "uncertain_below", UNCERTAIN_BELOW)
    side = False
    if side_angle_check and units:
        import side_angle
        if side_angle.available():
            side = side_angle.is_side_angle(img)[0]
            below = max(below, side_angle.SIDE_SURE) if side else below
    rows = []
    for u, c, pi, kind, note in zip(units, crops, p, kinds, notes):
        pi = float(pi)
        cls_status = "occupied" if pi >= thresh else "unoccupied"
        status, by, dev, attention, reason = cls_status, "classifier", None, None, ""
        d = _row_device(u, devices or [])
        if d is not None:
            dev = f"{d['class_name']} ({d['confidence']:.2f})"
            det_status = "unoccupied" if d["class_name"].lower() in FREE_DEVICE_CLASSES else "occupied"
            if det_status == cls_status:
                by = "classifier + device detector"
            elif mode == "warn":
                if det_status == "occupied":          # classifier says free, detector sees a device
                    status, by, attention = "uncertain", "classifier (device warning)", "warning"
                    reason = (f"Device detector sees a {d['class_name']} here ({d['confidence']:.0%}) but the "
                              f"classifier says free. Check whether this unit is really empty.")
                else:                                 # detector sees an empty slot / closed panel: unoccupied
                    what = "an empty slot" if d["class_name"].lower() == "empty" else "a closed (blank) panel"
                    status, by, attention = "unoccupied", "device detector", "info"
                    reason = (f"Device detector saw {what} here ({d['confidence']:.0%}), which counts as unoccupied "
                              f"(the classifier leaned occupied, score {pi:.2f}).")
            else:
                status, by, attention = det_status, "device detector", "info"
                reason = (f"Device detector saw {('an empty slot' if d['class_name'].lower() == 'empty' else 'a closed (blank) panel') if det_status == 'unoccupied' else 'a ' + d['class_name']} "
                          f"({d['confidence']:.0%}) and overruled the classifier (score {pi:.2f}).")
        conf = float(max(pi, 1 - pi))
        if by == "classifier" and conf < below:
            status, attention = "uncertain", "uncertain"
            reason = (f"Side-angle photo: the classifier must be {below:.0%} sure here and is {conf:.0%} (leans {cls_status}). "
                      f"Check this unit by eye." if side else f"Classifier only {conf:.0%} sure (leans {cls_status}). Check this unit by eye.")
        if note.get("matched_with") and attention is None:
            attention = "info"
            reason = f"Answer taken from {note['matched_with']}, which looks almost the same in this photo."
        rows.append({**u, "crop": c, "p_occupied": pi, "status": status, "leaning": cls_status,
                     "confidence": conf, "uncertain": status == "uncertain", "decided_by": by,
                     "attention": attention, "reason": reason, "side_angle": side,
                     "device": dev or (kind.capitalize() if kind and status == "unoccupied" else None)})
    return rows


def summarize(rows):
    n = len(rows)
    occ = sum(r["status"] == "occupied" for r in rows)
    free = sum(r["status"] == "unoccupied" for r in rows)
    unc = n - occ - free
    det = [r for r in rows if r["source"] == "detected"]
    # longest run of contiguous free rows
    best = cur = 0
    for r in rows:
        cur = cur + 1 if r["status"] == "unoccupied" else 0
        best = max(best, cur)
    return {"total": n, "occupied": occ, "free": free, "uncertain": unc,
            "occupancy_pct": 100 * occ / n if n else 0.0,
            "available_pct": 100 * free / n if n else 0.0,
            "uncertain_pct": 100 * unc / n if n else 0.0,
            "largest_free_block": best,
            "detected_rows": len(det), "inferred_rows": n - len(det),
            "mean_det_conf": float(np.mean([r["det_conf"] for r in det])) if det else 0.0}


def _font(size):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except OSError:
        return ImageFont.load_default()


def annotate(img: Image.Image, rows, show=None, spotlight=None, fit=None) -> Image.Image:
    """Draw rows on the photo.

    show      : set of unit labels to draw (None = all). Rows not in it are not drawn at all.
    spotlight : one unit label - the photo is dimmed everywhere except that row, nothing else drawn.
    fit       : (x1,y1,x2,y2) of a suggested placement - spotlighted in blue.
    """
    base = img.convert("RGBA")
    W, H = base.size
    by_unit = {r["unit"]: r for r in rows}
    hs = [r["box"][3] - r["box"][1] for r in rows] or [20]
    row_h = float(np.median(hs))
    lw = max(2, int(min(W, H) / 400))
    font = _font(int(max(10, min(row_h * 0.55, W / 45))))

    def tag(d, r, col):
        x1, y1 = r["box"][0], r["box"][1]
        t = r["unit"].upper()
        tw, th = d.textbbox((0, 0), t, font=font)[2:]
        d.rounded_rectangle([x1 + lw, y1 + lw, x1 + tw + 10, y1 + th + 6], radius=3, fill=col + (230,))
        d.text((x1 + 5, y1 + 2), t, fill="white", font=font)

    target = None
    if fit is not None:
        target, col = tuple(fit), PICK_RGB
    elif spotlight in by_unit:
        r = by_unit[spotlight]
        target, col = r["box"], STATUS_RGB[r["status"]]

    if target is not None:
        x1, y1, x2, y2 = target
        shade = Image.new("RGBA", base.size, (0, 0, 0, 150))
        ImageDraw.Draw(shade).rectangle([x1, y1, x2, y2], fill=(0, 0, 0, 0))
        out = Image.alpha_composite(base, shade)
        d = ImageDraw.Draw(out)
        d.rectangle([x1, y1, x2, y2], outline=col + (255,), width=lw * 2)
        if fit is None:
            tag(d, by_unit[spotlight], col)
        return out.convert("RGB")

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for r in rows:
        if show is not None and r["unit"] not in show:
            continue
        col = STATUS_RGB[r["status"]]
        x1, y1, x2, y2 = r["box"]
        d.rectangle([x1, y1, x2, y2], fill=col + (38,), outline=col + (200,), width=lw)
        tag(d, r, col)
    return Image.alpha_composite(base, layer).convert("RGB")


def row_strip(img: Image.Image, box, pad_frac: float = 0.25) -> Image.Image:
    """The row as it looks in the photo - native aspect, a little context above and below."""
    x1, y1, x2, y2 = box
    ph = (y2 - y1) * pad_frac
    return img.crop((max(0, x1), max(0, int(y1 - ph)), min(img.width, x2), min(img.height, int(y2 + ph))))
