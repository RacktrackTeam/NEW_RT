"""Model 1 + Model 2 on one rack photo: name every device, or answer Other when the two models do not agree.

Model 1 (ConvNeXt-Tiny, models/device_cls/convnext_tiny) names each device from its cut-out crop.
Model 2 (the DINOv2-B detector, models/device_cls/dinov2_b/detector.pt) finds and names devices in the whole photo; the
box that overlaps a device most (IoU >= 0.3, score >= 0.3) gives its name. The rule chosen on the test photos is in
models/device_cls/agreement.json ("both agree" today): the answer is Model 1's when both name the same type,
otherwise Other. On the 1,550 test devices: 1,379 right, 14 wrong, 157 Other (Model 1 alone: 1,342 / 44 / 164).

    python scripts/devices/classify_photo.py PHOTO                      # device outlines from the photo's label file
    python scripts/devices/classify_photo.py PHOTO --labels FILE.txt    # or from this label file

    from classify_photo import RackClassifier
    rc = RackClassifier()
    rows = rc.classify(photo, polygons)        # PIL image (upright), polygons as [[(x, y), ...], ...] in pixels
    rows[0]["answer"]                          # "Switch" ... or "Other"
"""
import argparse
import json
import pathlib
import sys

import torch
import yaml
from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts" / "devices"))
from device_classifier import DeviceClassifier, crop_polygon  # noqa: E402
import second_detector as sd  # noqa: E402

AGREE = ROOT / "models" / "device_cls" / "agreement.json"
DATA = ROOT / "data" / "datasets" / "devices"


class RackClassifier:
    def __init__(self, device=None, rule=None):
        self.m1 = DeviceClassifier(device=device)
        self.dev = self.m1.dev
        self.m2, self.names2, _ = sd.load(sd.DET, self.dev)
        self.rule = rule or (json.load(open(AGREE))["chosen"] if AGREE.exists() else "both agree")

    def classify(self, photo, polygons):
        photo = photo.convert("RGB")
        r1 = self.m1.classify([crop_polygon(photo, p) for p in polygons]) if polygons else []
        boxes, labels, scores = sd.detect(self.m2, photo, self.dev)
        keep = [i for i in range(len(scores)) if scores[i] >= sd.SCORE and self.names2[int(labels[i]) - 1] not in sd.DROP]
        rows = []
        for p, a in zip(polygons, r1):
            xs, ys = [q[0] for q in p], [q[1] for q in p]
            box = (min(xs), min(ys), max(xs), max(ys))
            best = max(((sd.iou(box, boxes[i].tolist()), i) for i in keep), default=(0, None))
            m2, s2 = (self.names2[int(labels[best[1]]) - 1], float(scores[best[1]])) if best[0] >= sd.IOU else ("none", 0.0)
            other = a["class"] != m2
            if "unfamiliar" in self.rule and a["other_reason"] and ("unsure" in a["other_reason"] or "unlike" in a["other_reason"]):
                other = True
            if "close" in self.rule and a["label"] == "Other":
                other = True
            reason = ("no box from Model 2" if m2 == "none" else "the two models disagree" if a["class"] != m2 else a["other_reason"]) if other else ""
            rows.append({"answer": "Other" if other else a["class"], "reason": reason, "model1": a["class"], "model1_prob": round(a["prob"], 3),
                         "model1_alone": a["label"], "model2": m2, "model2_score": round(s2, 3), "box": [round(v) for v in box]})
        return rows


def polygons_from(label_file, w, h):
    """Device outlines from a label file ('cls x1 y1 x2 y2 ...', normalised); Empty and Closed Unit skipped."""
    names = yaml.safe_load(open(DATA / "data.yaml"))["names"]
    names = [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)
    out = []
    for line in pathlib.Path(label_file).read_text().splitlines():
        v = line.split()
        if len(v) < 7 or names[int(v[0])] in ("Empty", "Closed Unit"):
            continue
        c = [float(t) for t in v[1:]]
        out.append((names[int(v[0])], [(c[i] * w, c[i + 1] * h) for i in range(0, len(c) - 1, 2)]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("photo")
    ap.add_argument("--labels", help="label file with the device outlines (default: the photo's file in the dataset)")
    a = ap.parse_args()
    photo = ImageOps.exif_transpose(Image.open(a.photo)).convert("RGB")
    lab = a.labels or DATA / "labels" / (pathlib.Path(a.photo).stem + ".txt")
    found = polygons_from(lab, *photo.size)
    rows = RackClassifier().classify(photo, [p for _, p in found])
    right = wrong = other = 0
    print(f"{'labelled':13} {'answer':13} {'Model 1':13} {'Model 2':13} reason")
    for (truth, _), r in zip(found, rows):
        print(f"{truth:13} {r['answer']:13} {r['model1']:13} {r['model2']:13} {r['reason']}")
        other += r["answer"] == "Other"; right += r["answer"] == truth; wrong += r["answer"] not in ("Other", truth)
    print(f"{len(rows)} devices: {right} right, {wrong} wrong, {other} Other")


if __name__ == "__main__":
    main()
