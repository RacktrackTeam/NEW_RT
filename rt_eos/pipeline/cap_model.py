"""Dust-cap classifier: the model team's ResNet says capped or uncapped for
each empty port.

Where it fits, in the owner's words (30 Sep 2026): the port-count model
finds the ports and says which are connected and which are empty; a
connected port is not touched; each empty port is cut out and the ResNet
classifier says whether a dust cap is on it or not.

    port boxes + status (port_connected_or_empty.pt)
      -> "connected"            left as it is
      -> "empty"                cut out with 15% padding, classified here
      -> cap_state "capped" | "empty" (no cap) | "uncertain" (the model
         was not sure), cap_reason in plain words, cap_source "model"

This runs after the pixel rules of pipeline/cap_check.py and overrides
their answer on every empty port it classifies with confidence; a port
narrower than MIN_PORT_WIDTH keeps the rules' answer (too_small), because
a crop of a few pixels tells the model nothing. Without the weights file
nothing happens: the slot stays open and the rules keep answering.

The weights (config.json models.cap_classifier, Models/dust_cap_classifier.pth)
are a checkpoint saved by the model team's training script:
    {"model_name": "resnet18" | "resnet34" | "resnet50",
     "classes": ["capped", "uncapped"], "img_size": 224, "state_dict": ...}
The crop is fed as RGB, resized to img_size x img_size, ImageNet-normalised,
which is what test_and_classify.py in the team's folder does. The checkpoint
is loaded with weights_only=True: plain values and tensors only, no pickled
code. Change the
crop, the size or the normalisation only together with the model team.
"""

from __future__ import annotations

import logging
import os

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = "Models/dust_cap_classifier.pth"
PAD_FRACTION = 0.15       # the crop padding the model was trained with
MIN_PORT_WIDTH = 16       # a narrower box is left to the pixel rules
MIN_CONF = 0.60           # under this the model's answer is "uncertain"
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

_BUNDLES: dict[str, object] = {}


def weights_path(config: dict | None) -> str:
    return str((config or {}).get("models", {}).get("cap_classifier") or DEFAULT_WEIGHTS)


CANONICAL_LABELS = {"cap": "capped", "capped": "capped", "uncap": "uncapped", "uncapped": "uncapped"}


def normalize_classes(classes) -> list[str]:
    """The model team's class names in one spelling: cap and capped are
    "capped", uncap and uncapped are "uncapped"."""
    return [CANONICAL_LABELS.get(str(c).strip().lower(), str(c).strip().lower()) for c in classes]


def checkpoint_state(ckpt: dict) -> dict:
    """The weights in a checkpoint, under whichever key it was saved."""
    state = ckpt.get("state_dict") or ckpt.get("model_state_dict")
    if state is None:
        raise ValueError("cap classifier: no state_dict or model_state_dict in the file")
    return state


def infer_model_name(state: dict) -> str:
    """ResNet50 has bottleneck blocks (a conv3 in each); ResNet18 does not."""
    return "resnet50" if any(".conv3." in k for k in state) else "resnet18"


def load_cap_model(path: str):
    """The classifier, its transform and its class names, loaded once per
    path. None when the file is not there: the slot is open."""
    if path in _BUNDLES:
        return _BUNDLES[path]
    if not path or not os.path.exists(path):
        _BUNDLES[path] = None
        return None
    import torch
    import torch.nn as nn
    from torchvision import models, transforms

    ckpt = torch.load(path, map_location="cpu", weights_only=True)
    factory = {"resnet18": models.resnet18, "resnet34": models.resnet34, "resnet50": models.resnet50}
    state = checkpoint_state(ckpt)
    name = ckpt.get("model_name") or infer_model_name(state)
    if name not in factory:
        raise ValueError(f"cap classifier: unknown model_name {name!r}")
    classes = normalize_classes(ckpt.get("classes") or ckpt.get("class_names") or [])
    model = factory[name](weights=None)
    # The model team trains with either a plain final layer or dropout then the layer.
    if "fc.1.weight" in state:
        model.fc = nn.Sequential(nn.Dropout(0.5), nn.Linear(model.fc.in_features, len(classes)))
    else:
        model.fc = nn.Linear(model.fc.in_features, len(classes))
    model.load_state_dict(state)
    model.eval()
    size = int(ckpt.get("img_size") or ckpt.get("input_size") or 224)
    tf = transforms.Compose([
        transforms.Resize((size, size)),
        transforms.ToTensor(),
        transforms.Normalize(IMAGENET_MEAN, IMAGENET_STD),
    ])

    def predict(crop_rgb: np.ndarray) -> tuple[str, float]:
        from PIL import Image

        with torch.no_grad():
            x = tf(Image.fromarray(crop_rgb)).unsqueeze(0)
            probs = torch.softmax(model(x), dim=1)[0]
            i = int(probs.argmax().item())
            return classes[i], float(probs[i].item())

    bundle = {"predict": predict, "classes": classes, "model_name": name, "img_size": size, "path": path}
    _BUNDLES[path] = bundle
    logger.info("cap classifier loaded: %s (%s, %d px)", path, name, size)
    return bundle


def padded_box(box, width: int, height: int, pad: float = PAD_FRACTION) -> tuple[int, int, int, int] | None:
    """The port box grown by `pad` on each side, kept inside the image:
    (x1, y1, x2, y2) as ints, or None when nothing is left."""
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    w, h = x2 - x1, y2 - y1
    if w <= 0 or h <= 0:
        return None
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    wp, hp = w * (1 + pad), h * (1 + pad)
    ax1 = max(0, int(round(cx - wp / 2)))
    ay1 = max(0, int(round(cy - hp / 2)))
    ax2 = min(width, int(round(cx + wp / 2)))
    ay2 = min(height, int(round(cy + hp / 2)))
    if ax2 <= ax1 or ay2 <= ay1:
        return None
    return ax1, ay1, ax2, ay2


def state_of(label: str, conf: float, min_conf: float = MIN_CONF) -> tuple[str, str]:
    """The model's answer in the pipeline's words: cap_state and cap_reason."""
    label = str(label).lower()
    if conf < min_conf:
        return "uncertain", f"dust cap model not sure ({label} {conf:.2f})"
    if label == "capped":
        return "capped", f"dust cap model: cap on ({conf:.2f})"
    return "empty", f"dust cap model: no cap ({conf:.2f})"


def classify_caps(img_bgr: np.ndarray, ports: list[dict], predict, pad: float = PAD_FRACTION, min_conf: float = MIN_CONF) -> int:
    """Classify every empty port in place. `predict(crop_rgb) -> (label,
    conf)` is the model (or a stand-in in tests). A connected port is not
    touched. Returns how many ports were classified."""
    if img_bgr is None or not len(ports):
        return 0
    height, width = img_bgr.shape[:2]
    n = 0
    for p in ports:
        if p.get("status") != "empty" or not p.get("box"):
            continue
        box = padded_box(p["box"], width, height, pad)
        if box is None:
            continue
        x1, y1, x2, y2 = box
        if (x2 - x1) < MIN_PORT_WIDTH or (y2 - y1) < MIN_PORT_WIDTH:
            continue
        crop = cv2.cvtColor(img_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2RGB)
        try:
            label, conf = predict(crop)
        except Exception as exc:  # one bad crop must not stop the rest
            logger.warning("cap classifier skipped a port: %s: %s", type(exc).__name__, exc)
            continue
        state, reason = state_of(label, conf, min_conf)
        p["cap_state"] = state
        p["cap_reason"] = reason
        p["cap_source"] = "model"
        p["cap_model"] = {"label": str(label).lower(), "conf": round(float(conf), 3)}
        n += 1
    return n


def apply_cap_model(img_bgr: np.ndarray, ports: list[dict], config: dict | None = None) -> int:
    """The drop-in: classify the empty ports with the configured weights
    when they exist, else do nothing. Never raises."""
    try:
        bundle = load_cap_model(weights_path(config))
    except Exception as exc:
        logger.warning("cap classifier not loaded: %s: %s", type(exc).__name__, exc)
        return 0
    if bundle is None:
        return 0
    return classify_caps(img_bgr, ports, bundle["predict"])
