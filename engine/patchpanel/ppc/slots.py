"""Slot check: is a detected `empty` position a bare HOLE or an open SOCKET?

The detector's `empty` class marks an unoccupied position. It cannot tell a
bare keystone cutout (or blank plug) from a fixed RJ-45 socket that simply
has no cable in it, so a fixed panel with one or two cables out is called
Keystone by tier 1. Counting empties cannot separate the two; looking at the
slot can.

Each `empty` box is cropped, embedded with the same DINOv2 encoder as stage
1, and compared with a small gallery of slot crops labelled "hole" or
"socket" (build_slot_gallery.py makes it from the PatchPanel_Images gallery,
so the test sets stay untouched).

Used ONLY when a panel has 1..MAX_EMPTY empties. With 4 or more, tier 1 was
65/65 correct on the 211 set and is left alone.
"""

import os

import cv2
import numpy as np

HOLE, SOCKET = "hole", "socket"
MAX_EMPTY = 3        # the zone where tier 1 is unreliable (12/17 correct)
MARGIN = 0.03        # |sim(hole) - sim(socket)| needed to believe a slot
TOPK = 3
PAD = 0.20           # context around the box, as a share of its size
MODEL = "vit_base_patch14_dinov2.lvd142m"   # same encoder as stage 1


class SlotEmbedder:
    """DINOv2 CLS embedding of small crops, L2-normalised. Loaded lazily."""

    def __init__(self, model=MODEL, img_size=224, device="cpu"):
        self.model_name, self.img_size, self.device = model, img_size, device
        self._m = self._tf = None

    def _load(self):
        import timm
        import torch
        self._torch = torch
        self._m = timm.create_model(self.model_name, pretrained=True,
                                    num_classes=0, img_size=self.img_size,
                                    dynamic_img_size=True)
        self._m.eval().to(self.device)
        cfg = timm.data.resolve_model_data_config(self._m)
        self._mean = torch.tensor(cfg["mean"]).view(1, 3, 1, 1)
        self._std = torch.tensor(cfg["std"]).view(1, 3, 1, 1)

    def __call__(self, crops, batch=16):
        """crops: list of BGR uint8 arrays -> (n, d) float32."""
        if not crops:
            return np.zeros((0, 1), dtype=np.float32)
        if self._m is None:
            self._load()
        torch, out, s_ = self._torch, [], self.img_size
        with torch.no_grad():
            for s in range(0, len(crops), batch):
                arr = np.stack([
                    cv2.cvtColor(cv2.resize(c, (s_, s_),
                                            interpolation=cv2.INTER_CUBIC),
                                 cv2.COLOR_BGR2RGB)
                    for c in crops[s:s + batch]]).astype(np.float32) / 255.0
                x = torch.from_numpy(arr).permute(0, 3, 1, 2)
                x = ((x - self._mean) / self._std).to(self.device)
                e = self._m(x)
                e = e / e.norm(dim=1, keepdim=True).clamp_min(1e-8)
                out.append(e.cpu().numpy().astype(np.float32))
        return np.concatenate(out)


def load_image(path, img_w=0, img_h=0):
    """The image at the size the boxes were measured on."""
    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    if img_w and img_h and (bgr.shape[1] != img_w or bgr.shape[0] != img_h):
        bgr = cv2.resize(bgr, (img_w, img_h))
    return bgr


def crop_slot(bgr, box, pad=PAD, min_side=6):
    """One slot with a little frame around it, or None if too small."""
    H, W = bgr.shape[:2]
    x0, y0, x1, y1 = [float(v) for v in box]
    if x1 - x0 < min_side or y1 - y0 < min_side:
        return None
    px, py = pad * (x1 - x0), pad * (y1 - y0)
    x0, y0 = max(0, int(x0 - px)), max(0, int(y0 - py))
    x1, y1 = min(W, int(x1 + px)), min(H, int(y1 + py))
    if x1 - x0 < min_side or y1 - y0 < min_side:
        return None
    return bgr[y0:y1, x0:x1].copy()


class SlotGallery:
    def __init__(self, path):
        z = np.load(path, allow_pickle=False)
        self.emb = z["emb"].astype(np.float32)
        self.label = z["label"].astype(str)
        self.n_hole = int((self.label == HOLE).sum())
        self.n_socket = int((self.label == SOCKET).sum())
        if not self.n_hole or not self.n_socket:
            raise ValueError("slot gallery needs both classes (hole=%d, "
                             "socket=%d)" % (self.n_hole, self.n_socket))

    def margins(self, q, topk=TOPK):
        """sim(hole) - sim(socket) per query; positive means hole."""
        sim = q @ self.emb.T
        out = []
        for lab in (HOLE, SOCKET):
            s = sim[:, self.label == lab]
            k = min(topk, s.shape[1])
            out.append(np.partition(s, -k, axis=1)[:, -k:].mean(1))
        return out[0] - out[1]


def check_panel(image_path, boxes, classes, img_w, img_h, gallery, embedder,
                margin=MARGIN, max_empty=MAX_EMPTY):
    """-> dict(verdict, n_hole, n_socket, margins, why).

    verdict  "hole"    at least one empty position is a bare cutout / blank
                       -> keystone stands
             "socket"  EVERY empty position is confidently an open socket
                       -> not keystone evidence; hand to the ports model
             None      not applicable or not sure -> leave tier 1 as it is
    Unsure defaults to leaving tier 1 alone on purpose: in the 1-3 empties
    zone most panels (12 of 17 measured) really are keystone.
    """
    res = {"verdict": None, "n_hole": 0, "n_socket": 0, "margins": [],
           "why": ""}
    idx = [i for i, c in enumerate(classes) if c == "empty"]
    if not idx or len(idx) > max_empty:
        res["why"] = "slot check not applicable (%d empties)" % len(idx)
        return res
    bgr = load_image(image_path, img_w, img_h)
    if bgr is None:
        res["why"] = "image unreadable"
        return res
    crops = [crop_slot(bgr, boxes[i]) for i in idx]
    if any(c is None for c in crops):
        res["why"] = "an empty slot is too small to inspect"
        return res
    m = gallery.margins(embedder(crops))
    res["margins"] = [round(float(x), 4) for x in m]
    res["n_hole"] = int((m >= margin).sum())
    res["n_socket"] = int((m <= -margin).sum())
    ms = ", ".join("%+.3f" % x for x in m)
    if res["n_hole"]:
        res["verdict"] = HOLE
        res["why"] = ("%d of %d empty positions read as a bare cutout "
                      "(hole-vs-socket %s)" % (res["n_hole"], len(idx), ms))
    elif res["n_socket"] == len(idx):
        res["verdict"] = SOCKET
        res["why"] = ("all %d empty positions are open sockets, not cutouts "
                      "(hole-vs-socket %s) -- a fixed panel with cables out"
                      % (len(idx), ms))
    else:
        res["why"] = "slot check unsure (hole-vs-socket %s)" % ms
    return res


def default_gallery_path(here):
    return os.path.join(here, "out", "slot_gallery.npz")
