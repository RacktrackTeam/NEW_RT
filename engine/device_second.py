"""Model 2 for device classification: the DINOv2-B detector (models/device_cls/dinov2_b/detector.pt, trained by
scripts/devices/train_dinov2.py) run on a device's crop; the detection covering the crop's centre names the device.
device_classifier asks it for a second opinion and, by agreement.json ("both agree"), a device the two models name
differently is left as Other for a person (user, 2026-10-27: 'use model 1 + model 2'). Loaded once; about a second per
crop on this Mac.

    python3 engine/device_second.py <crop.jpg> ...     prints each crop's type and score
"""
import math
import os
import pathlib
import sys

import torch
import torchvision.transforms as T
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUN = ROOT / "models" / "device_cls" / "dinov2_b"
_SCRIPTS = ROOT / "engine"                                       # train_dinov2.py, the detector's own code


def available():
    return (RUN / "detector.pt").exists() and (_SCRIPTS / "train_dinov2.py").exists()


class SecondOpinion:
    def __init__(self, run=RUN, device=None):
        if str(_SCRIPTS) not in sys.path:
            sys.path.insert(0, str(_SCRIPTS))
        import train_dinov2 as TD                                        # the detector's own code: Detector, decode, fit, MEAN, STD
        self.TD = TD
        ck = torch.load(run / "detector.pt", map_location="cpu", weights_only=True)
        self.types = list(ck["types"]); self.max_side = int(ck.get("max_side") or 518)
        self.model = TD.Detector(ck["model_name"], len(self.types), pretrained=False)
        self.model.load_state_dict(ck["model"]); self.model.eval()
        self.dev = torch.device(device or ("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"))
        self.model.to(self.dev)
        self.norm = T.Compose([T.ToTensor(), T.Normalize(TD.MEAN, TD.STD)])

    @torch.no_grad()
    def top(self, crop):
        """(type, score) of the best detection covering the crop's centre, or (None, 0.0) when it finds no device there."""
        TD = self.TD
        im = crop.convert("RGB"); W, H = im.size
        s, w, h = TD.fit(W, H, self.max_side)
        if s < 1:
            im = im.resize((w, h), Image.BICUBIC)
        x = self.norm(im)
        Hp, Wp = math.ceil(h / TD.PATCH) * TD.PATCH, math.ceil(w / TD.PATCH) * TD.PATCH
        pad = torch.zeros(3, Hp, Wp); pad[:, :h, :w] = x
        bx, sc, c = TD.decode(self.model(pad[None].to(self.dev)), [(h, w)], score_thr=0.05)[0]
        cx, cy = w / 2, h / 2
        inside = (bx[:, 0] <= cx) & (bx[:, 2] >= cx) & (bx[:, 1] <= cy) & (bx[:, 3] >= cy)
        if not bool(inside.any()):
            return None, 0.0
        j = int(torch.nonzero(inside)[int(sc[inside].argmax())])
        return self.types[int(c[j])], float(sc[j])


_ONE = None


def second_opinion():
    """The loaded model, once per process; None when its files are not there."""
    global _ONE
    if _ONE is None and available():
        _ONE = SecondOpinion()
    return _ONE


def confirmed(dv):
    """True when the device's kind is settled by more than the classifiers: a vendor layout fitted to its make and model,
    the panel reader finding its ports, or a person's own choice. Model 2's disagreement does not reopen those."""
    L = dv.get("layout") or {}
    return bool(dv.get("kind_override")) or bool(L.get("fitted")) or (L.get("mode") == "patchpanel" and L.get("ports"))


def recheck(run_dir):
    """A finished run checked by model 2 on each device's clean crop (section_D<i>_clean.jpg); devices the two models
    name differently become Other, 'the two models disagree'. A kind a person set is left alone. Returns the changes."""
    import json
    run_dir = pathlib.Path(run_dir); rf = run_dir / "result.json"; r = json.loads(rf.read_text()); m = SecondOpinion(); changed = []
    for dv in r["devices"]:
        cl = dv.get("classification") or {}; crop = run_dir / f"section_D{dv['index']}_clean.jpg"
        if not cl.get("label") or not crop.exists():
            continue
        t2, p2 = m.top(Image.open(crop))
        cl["second"] = {"model": "dinov2_b", "class": t2, "prob": round(p2, 3)}
        if confirmed(dv):                                                # a fitted vendor layout, a read panel or a person settle the kind
            if cl.get("model1_label") and cl.get("other_reason") == "the two models disagree":
                cl["label"], cl["other_reason"] = cl.pop("model1_label"), None
            continue
        if t2 and cl["label"] != "Other" and t2 != cl["label"]:
            changed.append((dv["index"], cl["label"], t2)); cl["model1_label"] = cl["label"]; cl["label"] = "Other"; cl["other_reason"] = "the two models disagree"
    rf.write_text(json.dumps(r, indent=1))
    return changed


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    if len(sys.argv) > 2 and sys.argv[1] == "--recheck":
        for run in sys.argv[2:]:
            print(run, "changed", recheck(run))
    else:
        m = SecondOpinion()
        for p in sys.argv[1:]:
            print(p, m.top(Image.open(p)))
