"""Step 3: where are the devices? Groups occupied rack units into devices ("D3 · 4U = device 3 fills 4 units").

Steps 1 and 2 are ours: the RT-EOS unit ladder finds the units, the chosen approach (Ensemble by default) says
which are occupied, free or uncertain. Step 3 by default is Geethika's "updated approach" (their slot_pipeline.py and slot_models.pt), folded
in here with its logic unchanged:
  * every unit is described by a CLIP ViT-B/32 embedding of the strip (cut into pieces and stacked so CLIP sees
    all of it) plus 10 edge/texture numbers; their slot network turns that into open / closed / occupied;
  * every two units on top of each other are compared: the seam between them (is there a dividing line?),
    how alike their texture and colour are, and CLIP views of the join and of both units together;
    their pair network turns that into P(same device);
  * two neighbouring units that are both taken and have P(same device) >= 75 % are joined; joined runs are devices.
"Taken" means occupied OR uncertain by our step 2, so a device the classifier was unsure about (e.g. a UPS the
device finder flagged) still shows up, marked "check" instead of "confirmed".
The model file models/v2/device_pairs.pt is their slot_models.pt; it holds only weights and numpy arrays and is
loaded with torch's weights_only loader.
"""
import local_models  # noqa: F401  (model caches inside this folder, hub lookups off)
import pathlib

import cv2
import numpy as np
import torch
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT / "models" / "v2" / "device_pairs.pt"
SLOT_CLASSES = ["open", "closed", "occupied"]
SEAM_FEATURES = ["seam_grad_ratio", "seam_line_frac", "strip_mean_diff", "strip_std_diff", "strip_profile_corr",
                 "color_diff", "hist_intersection", "gap", "height_ratio"]
N_PAIR_HAND = len(SEAM_FEATURES) + 10 + 4


# ---------------------------------------------------------------- their features (slot_pipeline.py, unchanged)
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
    pieces = [cv2.copyMakeBorder(p, 0, 0, 0, pw - p.shape[1], cv2.BORDER_REPLICATE) if p.shape[1] < pw else p
              for p in pieces]
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
    gy_all = np.abs(cv2.Sobel(cv2.GaussianBlur(gray[:, x1:x2], (3, 3), 0), cv2.CV_32F, 0, 1, ksize=3))
    seam_grad = gy_all[band].mean()
    inner = np.concatenate([gy_all[r(a[1] + 0.15 * h):r(a[3] - 0.15 * h)].ravel(),
                            gy_all[r(b[1] + 0.15 * h):r(b[3] - 0.15 * h)].ravel()])
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
    n = len(emb)
    ctx = np.zeros_like(emb)
    cnt = np.zeros((n, 1), np.float32)
    for i, j in pairs:
        ctx[i] += emb[j]; ctx[j] += emb[i]; cnt[i] += 1; cnt[j] += 1
    ctx = ctx / np.maximum(cnt, 1)
    return np.hstack([emb * 10, (edge - mu) / sd, ctx * 5]).astype(np.float32)


def pair_inputs(f, p_slot, s_mu, s_sd, e_sd):
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


# ---------------------------------------------------------------- the grouper
class DeviceGrouper:
    def __init__(self, model_path=MODEL_PATH):
        import open_clip
        torch.serialization.add_safe_globals([np._core.multiarray._reconstruct, np.ndarray, np.dtype,
                                              np.dtypes.Float32DType, np.dtypes.Float64DType])
        ck = torch.load(model_path, weights_only=True)            # weights + numpy arrays only
        self.ck = ck
        self.slot_net = SlotNet(ck["slot_dim"]); self.slot_net.load_state_dict(ck["slot_state"]); self.slot_net.eval()
        self.pair_net = PairNet(ck["pair_dim"]); self.pair_net.load_state_dict(ck["pair_state"]); self.pair_net.eval()
        self.same_thr = float(ck.get("same_thr", 0.5))
        self.clip, _, self.pre = open_clip.create_model_and_transforms("ViT-B-32-quickgelu", pretrained="openai")
        self.clip = self.clip.eval()                               # CPU, as in their pipeline

    @torch.no_grad()
    def _embed(self, crops, bs=96):
        if not crops:
            return np.zeros((0, 512), np.float32)
        out = []
        for i in range(0, len(crops), bs):
            x = torch.stack([self.pre(Image.fromarray(square_strip(c))) for c in crops[i:i + bs]])
            e = self.clip.encode_image(x)
            out.append((e / e.norm(dim=-1, keepdim=True)).numpy())
        return np.vstack(out).astype(np.float32)

    @torch.no_grad()
    def group(self, img, rows, taken=None):
        """rows: our classified units (top to bottom). taken: optional per-row True/False overriding "occupied or
        uncertain" (the accuracy test uses the hand labels here to judge the grouping on its own).
        Returns {'devices', 'pairs', 'units', 'same_thr'}."""
        rgb = np.array(img.convert("RGB"))
        H, W = rgb.shape[:2]
        boxes = [tuple(int(v) for v in r["box"]) for r in rows]
        crops = [rgb[max(0, y1):min(H, y2), max(0, x1):min(W, x2)] for x1, y1, x2, y2 in boxes]
        ok = [c.shape[0] >= 4 and c.shape[1] >= 8 for c in crops]
        idx = [k for k, g in enumerate(ok) if g]
        slots = [boxes[k] for k in idx]
        crops = [crops[k] for k in idx]
        emb = self._embed(crops)
        edge = np.stack([edge_features(c) for c in crops]) if crops else np.zeros((0, 10), np.float32)
        pairs = adjacent_pairs(slots)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        seam = (np.stack([seam_features(gray, rgb, slots[i], slots[j]) for i, j in pairs]) if pairs
                else np.zeros((0, len(SEAM_FEATURES)), np.float32))
        seam_c, span_c = pair_crops(rgb, slots, pairs)
        f = dict(emb=emb, edge=edge, pairs=pairs, seam=seam, pair_seam_emb=self._embed(seam_c),
                 pair_span_emb=self._embed(span_c))
        ck = self.ck
        ps = (torch.softmax(self.slot_net(torch.tensor(slot_inputs(emb, edge, pairs, ck["edge_mu"], ck["edge_sd"]))), 1)
              .numpy() if len(slots) else np.zeros((0, 3), np.float32))
        xp = pair_inputs(f, ps, ck["seam_mu"], ck["seam_sd"], ck["edge_sd"])
        pp = torch.sigmoid(self.pair_net(torch.tensor(xp))).numpy() if len(xp) else np.zeros(0, np.float32)

        # join taken neighbours (occupied or uncertain in OUR step 2) that look like one device
        tk = list(taken) if taken is not None else [r["status"] in ("occupied", "uncertain") for r in rows]
        row_pos = {id(r): k for k, r in enumerate(rows)}
        taken = lambda r: tk[row_pos[id(r)]]
        parent = list(range(len(idx)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        pair_out = []
        for (i, j), p in zip(pairs, pp):
            ri, rj = rows[idx[i]], rows[idx[j]]
            both = taken(ri) and taken(rj)
            joined = bool(both and p >= self.same_thr)
            if joined:
                parent[find(j)] = find(i)
            pair_out.append({"upper": ri["unit"], "lower": rj["unit"], "p_same": float(p), "joined": joined,
                             "why": ("same device" if joined else "different devices" if both
                                     else "not compared: one of them is free")})
        groups = {}
        for k in range(len(idx)):
            if taken(rows[idx[k]]):
                groups.setdefault(find(k), []).append(k)
        devices = []
        for g in sorted(groups.values(), key=lambda g: boxes[idx[g[0]]][1]):
            us = [rows[idx[k]] for k in g]
            bx = np.array([boxes[idx[k]] for k in g])
            nums = sorted(int(u["unit"][1:]) for u in us)
            inner = [pp[n] for n, (i, j) in enumerate(pairs) if i in g and j in g]
            devices.append({
                "id": len(devices) + 1, "units": [u["unit"] for u in us], "height_u": len(us),
                "range_label": f"U{nums[0]:02d}" if len(nums) == 1 else f"U{nums[0]:02d}-U{nums[-1]:02d}",
                "box": [int(bx[:, 0].min()), int(bx[:, 1].min()), int(bx[:, 2].max()), int(bx[:, 3].max())],
                "status": "confirmed" if all(u["status"] == "occupied" for u in us) else "check",
                "p_same_min": float(min(inner)) if inner else None,
                "device_seen": next((u.get("device") for u in us if u.get("device")), None)})
        unit_out = {rows[idx[k]]["unit"]: {"slot_probs": dict(zip(SLOT_CLASSES, map(float, ps[k]))),
                                           "device": next((d["id"] for d in devices if rows[idx[k]]["unit"] in d["units"]), None)}
                    for k in range(len(idx))}
        return {"devices": devices, "pairs": pair_out, "units": unit_out, "same_thr": self.same_thr, "method": "pairs"}


# ---------------------------------------------------------------- every step 3 method behind one call
METHODS = {
    "pairs": ("Geethika's method", "An AI model looks at every two units on top of each other and decides: same device "
                                   "or not? It learned this from thousands of hand-labelled racks."),
    "edges": ("Prasanna's method 1", "Looks for the straight lines between devices in the photo. Where a line sits on "
                                     "a unit border, a new device starts. No training needed."),
    "scan":  ("Prasanna's method 2", "Reads the photo from top to bottom and watches the brightness. A sudden change "
                                     "means a new device starts. No training needed."),
}


def _image_uri(bgr, crop_x, crop_y, width=640):
    import base64
    x0, x1 = crop_x; y0, y1 = crop_y
    im = bgr[max(0, y0):y1, max(0, x0):x1]
    if im.size == 0:
        return None
    s = min(1.0, width / im.shape[1])
    im = cv2.resize(im, (int(im.shape[1] * s), int(im.shape[0] * s)), interpolation=cv2.INTER_AREA) if s < 1 else im
    ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode() if ok else None


def group_units(method, img, rows, grouper=None, taken=None, visual=False):
    """Step 3 with the chosen method. Same output for every method: {'devices', 'pairs', 'units', 'method', ...}."""
    if method == "pairs":
        return (grouper or DeviceGrouper()).group(img, rows, taken=taken)
    import step3_edges as E
    if not rows:
        return {"devices": [], "pairs": [], "units": {}, "same_thr": None, "method": method}
    bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    tk = list(taken) if taken is not None else [r["status"] in ("occupied", "uncertain") for r in rows]
    geo = E.Geo(rows, tk, bgr.shape)
    r = E.approach8(bgr, geo) if method == "edges" else E.approach1(bgr, geo)
    devices = []
    for g in r["groups"]:
        us = [rows[k] for k in g]
        bx = np.array([list(u["box"]) for u in us])
        nums = sorted(int(u["unit"][1:]) for u in us)
        devices.append({
            "id": len(devices) + 1, "units": [u["unit"] for u in us], "height_u": len(us),
            "range_label": f"U{nums[0]:02d}" if len(nums) == 1 else f"U{nums[0]:02d}-U{nums[-1]:02d}",
            "box": [int(bx[:, 0].min()), int(bx[:, 1].min()), int(bx[:, 2].max()), int(bx[:, 3].max())],
            "status": "confirmed" if all(u["status"] == "occupied" for u in us) else "check",
            "p_same_min": None, "device_seen": next((u.get("device") for u in us if u.get("device")), None)})
    pairs = []
    for k in range(1, len(rows)):
        both = tk[k - 1] and tk[k]
        edge = bool(r["split"].get(k, False))
        pairs.append({"upper": rows[k - 1]["unit"], "lower": rows[k]["unit"], "p_same": None,
                      "joined": bool(both and not edge),
                      "why": ("same device" if both and not edge else "different devices" if both
                              else "not compared: one of them is free"),
                      "edge_found": edge})
    out = {"devices": devices, "pairs": pairs, "same_thr": None, "method": method,
           "units": {u: {"device": d["id"]} for d in devices for u in d["units"]}}
    if visual:
        pad = int(0.6 * geo.h)
        cy = (geo.ys[0] - pad, geo.ys[-1] + pad)
        if method == "edges":
            out["visual"] = _image_uri(r["layout"], (geo.x0 - pad, geo.x1 + pad), cy)
            out["visual_caption"] = ("Edge picture of the rack. White = unit lines; green = a device edge found on a "
                                     "unit line (splits there); red = an edge between unit lines (ignored).")
        else:
            _g, scan, _s = E.vis_approach1(bgr, geo, r)
            out["visual"] = _image_uri(scan, (geo.x0 - pad, scan.shape[1]), cy)
            out["visual_caption"] = ("Grayscale photo with the scan on the right: yellow = brightness change, orange = "
                                     "how busy the area is. Line colours: green = split by a brightness step, red = "
                                     "split by a strong outline, teal = no split.")
    return out
