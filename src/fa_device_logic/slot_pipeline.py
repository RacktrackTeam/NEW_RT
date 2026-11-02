"""
Updated approach: rack occupancy from 1U slots only (no device model).

  Step 1  Units model (best 5.pt) finds the rack(s) and every 1U slot.
  Step 2  Each slot crop is described by a CLIP embedding + edge/texture features and
          classified as open / closed / occupied (open + closed = empty).
  Step 3  Every pair of vertically adjacent slots is compared (appearance similarity,
          seam line between them, texture difference). Occupied neighbours that look like
          the same device are merged, so a 4U server is one device occupying 4U.
"""
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import open_clip
import torch
from PIL import Image
from ultralytics import YOLO

HERE = Path(__file__).resolve().parent
MODELS = HERE.parents[1] / "models" / "device_join"                 # the joining model's weights
UNITS_WEIGHTS = HERE.parents[1] / "weights" / "rack_unit_finder.pt"     # the same unit finder the rest of the app uses
MODEL_PATH = MODELS / "slot_models_original.pt"
CLASSES = ["open", "closed", "occupied"]
SEAM_FEATURES = ["seam_grad_ratio", "seam_line_frac", "strip_mean_diff", "strip_std_diff", "strip_profile_corr",
                 "color_diff", "hist_intersection", "gap", "height_ratio"]
torch.set_num_threads(max(1, (torch.get_num_threads())))


# ----------------------------------------------------------------------------- features
def edge_features(rgb):
    """Edge/texture descriptors of the slot interior (outer 8% trimmed: rails, outline)."""
    h, w = rgb.shape[:2]
    nw = int(np.clip(w * 96 / max(h, 1), 32, 768))
    gray = cv2.cvtColor(cv2.resize(rgb, (nw, 96), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
    my, mx = int(96 * 0.08), int(nw * 0.08)
    g = gray[my:96 - my, mx:nw - mx]
    blur = cv2.GaussianBlur(g, (3, 3), 0)
    edges = cv2.Canny(blur, 50, 150) > 0
    gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
    lap = cv2.Laplacian(blur, cv2.CV_32F)
    n_cc, _, stats, _ = cv2.connectedComponentsWithStats(edges.astype(np.uint8), connectivity=8)
    small = ((stats[1:, cv2.CC_STAT_AREA] >= 3) & (stats[1:, cv2.CC_STAT_AREA] <= 200)).sum() if n_cc > 1 else 0
    cols = edges.mean(0)
    ax, ay = np.abs(gx).sum(), np.abs(gy).sum()
    gf = g.astype(np.float32)
    return np.array([edges.mean(), ax / (ax + ay + 1e-6), np.hypot(gx, gy).mean() / 255, np.log1p(lap.var()),
                     gf.std() / 255, small / (g.size / 1000), cols.std(), gf.mean() / 255, (gf > 220).mean(),
                     np.log(w / max(h, 1))], np.float32)


def square_strip(rgb):
    """A 1U slot is a thin strip; cut it into pieces and stack them so CLIP sees the whole slot."""
    h, w = rgb.shape[:2]
    n = int(np.clip(round(np.sqrt(w / max(h, 1))), 1, 6))
    pw = int(np.ceil(w / n))
    pieces = [rgb[:, i * pw:(i + 1) * pw] for i in range(n)]
    pieces = [cv2.copyMakeBorder(p, 0, 0, 0, pw - p.shape[1], cv2.BORDER_REPLICATE) if p.shape[1] < pw else p for p in pieces]
    return cv2.resize(np.vstack(pieces), (224, 224), interpolation=cv2.INTER_AREA)


def seam_features(gray, rgb, a, b):
    """How strongly two vertically adjacent slots a (upper) and b (lower) look like ONE device."""
    x1 = max(a[0], b[0]); x2 = min(a[2], b[2])
    w = x2 - x1
    x1, x2 = int(x1 + 0.08 * w), int(x2 - 0.08 * w)
    h = ((a[3] - a[1]) + (b[3] - b[1])) / 2
    s = (a[3] + b[1]) / 2
    H = gray.shape[0]
    r = lambda y: int(np.clip(round(y), 0, H))
    if x2 - x1 < 8 or h < 4:
        return np.zeros(len(SEAM_FEATURES), np.float32)
    band = slice(r(s - max(1, 0.12 * h)), max(r(s + max(1, 0.12 * h)), r(s - max(1, 0.12 * h)) + 1))
    reg = gray[r(a[1]):r(b[3]), x1:x2]
    gy_all = np.abs(cv2.Sobel(cv2.GaussianBlur(gray[:, x1:x2], (3, 3), 0), cv2.CV_32F, 0, 1, ksize=3))
    seam_grad = gy_all[band].mean()
    inner = np.concatenate([gy_all[r(a[1] + 0.15 * h):r(a[3] - 0.15 * h)].ravel(), gy_all[r(b[1] + 0.15 * h):r(b[3] - 0.15 * h)].ravel()])
    grad_ratio = seam_grad / (inner.mean() + 1e-3) if inner.size else 1.0
    edges = cv2.Canny(cv2.GaussianBlur(gray[band, x1:x2], (3, 3), 0), 50, 150) > 0
    line_frac = edges.any(0).mean() if edges.size else 0.0
    A = gray[r(s - 0.4 * h):r(s - 0.12 * h), x1:x2].astype(np.float32)
    B = gray[r(s + 0.12 * h):r(s + 0.4 * h), x1:x2].astype(np.float32)
    if A.size == 0 or B.size == 0:
        mean_d = std_d = 0.0; corr = 0.0
    else:
        mean_d = abs(A.mean() - B.mean()) / 255
        std_d = abs(A.std() - B.std()) / 255
        pa, pb = A.mean(0), B.mean(0)
        corr = float(np.corrcoef(pa, pb)[0, 1]) if pa.std() > 1e-3 and pb.std() > 1e-3 else 0.0
    ca = rgb[r(a[1]):r(a[3]), x1:x2].reshape(-1, 3).astype(np.float32)
    cb = rgb[r(b[1]):r(b[3]), x1:x2].reshape(-1, 3).astype(np.float32)
    color_d = np.linalg.norm(ca.mean(0) - cb.mean(0)) / 441.7 if len(ca) and len(cb) else 0.0

    def hist(x):
        hsv = cv2.cvtColor(x, cv2.COLOR_RGB2HSV)
        hh = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).ravel()
        return hh / (hh.sum() + 1e-6)
    ra, rb = rgb[r(a[1]):r(a[3]), x1:x2], rgb[r(b[1]):r(b[3]), x1:x2]
    inter = float(np.minimum(hist(ra), hist(rb)).sum()) if ra.size and rb.size else 0.0
    gap = (b[1] - a[3]) / h
    hr = (a[3] - a[1]) / max(b[3] - b[1], 1)
    return np.array([grad_ratio, line_frac, mean_d, std_d, corr, color_d, inter, gap, np.log(hr)], np.float32)


# ----------------------------------------------------------------------------- slots
def box_iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter + 1e-6)


def _inside(pt, box, tol=0.03):
    w, h = box[2] - box[0], box[3] - box[1]
    return box[0] - tol * w <= pt[0] <= box[2] + tol * w and box[1] - tol * h <= pt[1] <= box[3] + tol * h


def detect_slots(units_model, rgb):
    """Rack boxes and 1U slot boxes (de-duplicated, inside a rack, top to bottom)."""
    r = units_model.predict(rgb[:, :, ::-1], conf=0.25, imgsz=768, verbose=False)[0]
    racks, slots = [], []
    if r.boxes is not None and len(r.boxes):
        b = r.boxes.xyxy.cpu().numpy().astype(int)
        c = r.boxes.cls.cpu().numpy().astype(int)
        s = r.boxes.conf.cpu().numpy()
        racks = [tuple(x) for x, k in zip(b, c) if units_model.names[k] == "Rack"]
        cand = sorted([(sc, tuple(x)) for x, k, sc in zip(b, c, s) if units_model.names[k] == "RackUnit"], reverse=True)
        for _, x in cand:
            if racks and not any(_inside(((x[0] + x[2]) / 2, (x[1] + x[3]) / 2), rk) for rk in racks):
                continue
            if all(box_iou(x, k) < 0.5 for k in slots):
                slots.append(x)
        slots.sort(key=lambda x: (x[1] + x[3]) / 2)
    return racks, slots


def adjacent_pairs(slots):
    """(i, j): slot j sits directly below slot i in the same column."""
    pairs = []
    for i, a in enumerate(slots):
        ha, best = a[3] - a[1], None
        for j, b in enumerate(slots):
            if (b[1] + b[3]) / 2 <= (a[1] + a[3]) / 2:
                continue
            ov = min(a[2], b[2]) - max(a[0], b[0])
            if ov < 0.5 * min(a[2] - a[0], b[2] - b[0]):
                continue
            if best is None or (b[1] + b[3]) < (slots[best][1] + slots[best][3]):
                best = j
        if best is not None:
            b = slots[best]
            if b[1] - a[3] < 0.6 * ha and b[1] - a[1] > 0.4 * ha:
                pairs.append((i, best))
    return pairs


def slot_crop(rgb, box):
    x1, y1, x2, y2 = box
    return rgb[max(0, y1):y2, max(0, x1):x2]


def pair_crops(rgb, slots, pairs):
    """Seam crop (1U tall, centred on the join) and span crop (both slots) for each adjacent pair."""
    H, W = rgb.shape[:2]
    seam, span = [], []
    for i, j in pairs:
        a, b = slots[i], slots[j]
        x1, x2 = int(max(0, min(a[0], b[0]))), int(min(W, max(a[2], b[2])))
        h = ((a[3] - a[1]) + (b[3] - b[1])) / 2
        s = (a[3] + b[1]) / 2
        y1, y2 = int(np.clip(s - h / 2, 0, H - 1)), int(np.clip(s + h / 2, 1, H))
        seam.append(rgb[y1:max(y2, y1 + 2), x1:max(x2, x1 + 2)])
        y1, y2 = int(np.clip(a[1], 0, H - 1)), int(np.clip(b[3], 1, H))
        span.append(rgb[y1:max(y2, y1 + 2), x1:max(x2, x1 + 2)])
    return seam, span


class Clip:
    def __init__(self, device="cpu"):
        self.dev = device
        self.model, _, self.pre = open_clip.create_model_and_transforms("ViT-B-32-quickgelu", pretrained="openai")
        self.model = self.model.to(device).eval()

    @torch.no_grad()
    def embed(self, crops, bs=96):
        if not crops:
            return np.zeros((0, 512), np.float32)
        out = []
        for i in range(0, len(crops), bs):
            x = torch.stack([self.pre(Image.fromarray(square_strip(c))) for c in crops[i:i + bs]]).to(self.dev)
            e = self.model.encode_image(x)
            out.append((e / e.norm(dim=-1, keepdim=True)).cpu().numpy())
        return np.vstack(out).astype(np.float32)


def raw_features(units_model, clip, rgb):
    """Everything the classifiers need for one image (+ time spent per stage, in seconds)."""
    t0 = time.perf_counter()
    racks, slots = detect_slots(units_model, rgb)
    t1 = time.perf_counter()
    crops = [slot_crop(rgb, s) for s in slots]
    keep = [k for k, c in enumerate(crops) if c.shape[0] >= 4 and c.shape[1] >= 8]
    slots, crops = [slots[k] for k in keep], [crops[k] for k in keep]
    emb = clip.embed(crops)
    edg = np.stack([edge_features(c) for c in crops]) if crops else np.zeros((0, 10), np.float32)
    t2 = time.perf_counter()
    pairs = adjacent_pairs(slots)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    seam = np.stack([seam_features(gray, rgb, slots[i], slots[j]) for i, j in pairs]) if pairs else np.zeros((0, len(SEAM_FEATURES)), np.float32)
    seam_c, span_c = pair_crops(rgb, slots, pairs)
    f = dict(racks=racks, slots=slots, emb=emb, edge=edg, pairs=pairs, seam=seam,
             pair_seam_emb=clip.embed(seam_c), pair_span_emb=clip.embed(span_c))
    f["timing"] = dict(detect=t1 - t0, describe=t2 - t1, compare=time.perf_counter() - t2)
    return f


# ----------------------------------------------------------------------------- classifiers
class SlotNet(torch.nn.Module):
    """open / closed / occupied from [CLIP 512 | edge 10 | neighbour context 512]."""
    def __init__(self, d_in, hidden=256):
        super().__init__()
        self.net = torch.nn.Sequential(torch.nn.Linear(d_in, hidden), torch.nn.GELU(), torch.nn.Dropout(0.3),
                                       torch.nn.Linear(hidden, 3))

    def forward(self, x):
        return self.net(x)


class PairNet(torch.nn.Module):
    """P(same device) for two adjacent slots."""
    def __init__(self, d_in, hidden=128):
        super().__init__()
        self.net = torch.nn.Sequential(torch.nn.Linear(d_in, hidden), torch.nn.GELU(), torch.nn.Dropout(0.2),
                                       torch.nn.Linear(hidden, 1))

    def forward(self, x):
        return self.net(x).squeeze(-1)


def slot_inputs(emb, edge, pairs, mu, sd):
    """Per-slot input vector: own CLIP + standardised edge features + mean CLIP of neighbours above/below."""
    n = len(emb)
    ctx = np.zeros_like(emb)
    cnt = np.zeros((n, 1), np.float32)
    for i, j in pairs:
        ctx[i] += emb[j]; ctx[j] += emb[i]; cnt[i] += 1; cnt[j] += 1
    ctx = ctx / np.maximum(cnt, 1)
    return np.hstack([emb * 10, (edge - mu) / sd, ctx * 5]).astype(np.float32)


N_PAIR_HAND = len(SEAM_FEATURES) + 10 + 4          # hand-made pair features come first


def pair_inputs(f, p_slot, s_mu, s_sd, e_sd):
    """[seam features | edge-feature difference | CLIP similarity | slot probs | CLIP seam crop | CLIP span crop]."""
    emb, edge, pairs, seam = f["emb"], f["edge"], f["pairs"], f["seam"]
    if not pairs:
        return np.zeros((0, N_PAIR_HAND + 1024), np.float32)
    i = np.array([p[0] for p in pairs]); j = np.array([p[1] for p in pairs])
    cos = (emb[i] * emb[j]).sum(1, keepdims=True)
    ediff = np.abs(edge[i] - edge[j]) / e_sd
    both = np.stack([p_slot[i, 2] * p_slot[j, 2], np.abs(p_slot[i, 2] - p_slot[j, 2])], 1)
    hand = np.hstack([(seam - s_mu) / s_sd, ediff, (cos - 0.85) * 10, both, (1 - cos) * 10])
    views = np.hstack([np.asarray(f["pair_seam_emb"], np.float32) * 10, np.asarray(f["pair_span_emb"], np.float32) * 10])
    return np.hstack([hand, views]).astype(np.float32)


# ----------------------------------------------------------------------------- inference
@dataclass
class SlotResult:
    id: int
    box: tuple
    probs: dict
    status: str = ""          # open / closed / occupied
    device: int | None = None


@dataclass
class Device:
    id: int
    slots: list = field(default_factory=list)   # slot ids, top to bottom
    box: tuple = ()

    @property
    def units(self):
        return len(self.slots)


class RackResult:
    def __init__(self, rgb, racks, slots, devices, pairs, p_same):
        self.rgb, self.racks, self.slots, self.devices, self.pairs, self.p_same = rgb, racks, slots, devices, pairs, p_same

    @property
    def counts(self):
        c = {k: sum(s.status == k for s in self.slots) for k in CLASSES}
        c["empty"] = c["open"] + c["closed"]
        c["total"] = len(self.slots)
        return c


class SlotAnalyzer:
    def __init__(self, units_weights=UNITS_WEIGHTS, model_path=MODEL_PATH):
        self.units = YOLO(str(units_weights))
        self.clip = Clip()
        ck = torch.load(model_path, weights_only=False)
        self.ck = ck
        self.slot_net = SlotNet(ck["slot_dim"]); self.slot_net.load_state_dict(ck["slot_state"]); self.slot_net.eval()
        self.pair_net = PairNet(ck["pair_dim"]); self.pair_net.load_state_dict(ck["pair_state"]); self.pair_net.eval()

    @torch.no_grad()
    def predict(self, f):
        ck = self.ck
        if not len(f["slots"]):
            return np.zeros((0, 3), np.float32), np.zeros(0, np.float32)
        xs = slot_inputs(f["emb"], f["edge"], f["pairs"], ck["edge_mu"], ck["edge_sd"])
        ps = torch.softmax(self.slot_net(torch.tensor(xs)) / ck.get("slot_T", 1.0), 1).numpy()
        xp = pair_inputs(f, ps, ck["seam_mu"], ck["seam_sd"], ck["edge_sd"])
        pp = torch.sigmoid(self.pair_net(torch.tensor(xp))).numpy() if len(xp) else np.zeros(0, np.float32)
        return ps, pp

    def analyze(self, image):
        rgb = np.array(image.convert("RGB")) if isinstance(image, Image.Image) else image
        f = raw_features(self.units, self.clip, rgb)
        t = time.perf_counter()
        ps, pp = self.predict(f)
        res = assemble(rgb, f, ps, pp, self.ck.get("empty_thr", 0.5), self.ck.get("same_thr", 0.5))
        res.timing = dict(f["timing"], decide=time.perf_counter() - t)
        res.features = f                      # raw process data (edge + seam features) for reporting
        return res


def assemble(rgb, f, ps, pp, empty_thr=0.5, same_thr=0.5):
    slots = []
    for k, (b, p) in enumerate(zip(f["slots"], ps)):
        empty = p[0] + p[1] >= empty_thr
        st = ("open" if p[0] >= p[1] else "closed") if empty else "occupied"
        slots.append(SlotResult(k + 1, tuple(int(v) for v in b), dict(zip(CLASSES, p.round(3).tolist())), st))
    # union occupied neighbours that look like the same device
    parent = list(range(len(slots)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for (i, j), p in zip(f["pairs"], pp):
        if slots[i].status == slots[j].status == "occupied" and p >= same_thr:
            parent[find(j)] = find(i)
    groups = {}
    for k, s in enumerate(slots):
        if s.status == "occupied":
            groups.setdefault(find(k), []).append(k)
    devices = []
    for idx in sorted(groups.values(), key=lambda g: (slots[g[0]].box[1] + slots[g[0]].box[3]) / 2):
        d = Device(len(devices) + 1, [slots[k].id for k in idx])
        bx = np.array([slots[k].box for k in idx])
        d.box = (int(bx[:, 0].min()), int(bx[:, 1].min()), int(bx[:, 2].max()), int(bx[:, 3].max()))
        for k in idx:
            slots[k].device = d.id
        devices.append(d)
    return RackResult(rgb, f["racks"], slots, devices, f["pairs"], pp)
