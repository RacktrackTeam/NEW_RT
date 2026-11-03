"""
Better 1U slot boxes from the units model only (NO device model).

  dedup  : two RackUnit boxes in the same column (horizontal overlap >= 50%) whose vertical overlap is >= V_OVL of the
           smaller height are one unit -> keep the more confident box.
  tiled  : crop to the rack, cut into overlapping horizontal strips, run the units model on each strip (slots look
           2-3x larger), drop boxes cut by a strip edge, merge with the full-photo boxes, then dedup.
"""
import numpy as np

V_OVL = 0.5


def raw(units, rgb, conf=0.25):
    r = units.predict(rgb[:, :, ::-1], conf=conf, imgsz=768, verbose=False)[0]
    if r.boxes is None or not len(r.boxes):
        return [], []
    b = r.boxes.xyxy.cpu().numpy(); c = r.boxes.cls.cpu().numpy().astype(int); s = r.boxes.conf.cpu().numpy()
    racks = [tuple(map(float, x)) for x, k in zip(b, c) if units.names[k] == "Rack"]
    slots = [(float(sc), tuple(map(float, x))) for x, k, sc in zip(b, c, s) if units.names[k] == "RackUnit"]
    return racks, slots


def inside(box, racks, tol=0.03):
    if not racks:
        return True
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    for r in racks:
        w, h = r[2] - r[0], r[3] - r[1]
        if r[0] - tol * w <= cx <= r[2] + tol * w and r[1] - tol * h <= cy <= r[3] + tol * h:
            return True
    return False


def same_unit(a, b, v_ovl):
    hx = min(a[2], b[2]) - max(a[0], b[0])
    if hx < 0.5 * min(a[2] - a[0], b[2] - b[0]):
        return False
    vy = min(a[3], b[3]) - max(a[1], b[1])
    return vy >= v_ovl * min(a[3] - a[1], b[3] - b[1])


def dedup(cands, racks, v_ovl=V_OVL):
    keep = []
    for sc, x in sorted(cands, reverse=True):
        if not inside(x, racks):
            continue
        if all(not same_unit(x, k, v_ovl) for k in keep):
            keep.append(x)
    keep.sort(key=lambda x: (x[1] + x[3]) / 2)
    return [tuple(int(round(v)) for v in x) for x in keep]


def original(racks, cands):
    """The pipeline's own rule (IoU >= 0.5), for reference."""
    def iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        i = ix * iy
        return i / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i + 1e-6)
    keep = []
    for sc, x in sorted(cands, reverse=True):
        if racks and not inside(x, racks):
            continue
        if all(iou(x, k) < 0.5 for k in keep):
            keep.append(x)
    keep.sort(key=lambda x: (x[1] + x[3]) / 2)
    return [tuple(int(v) for v in x) for x in keep]


def tiled(units, rgb, racks, full, strip_ratio=0.6, overlap=0.3):
    H, W = rgb.shape[:2]
    regions = racks or [(0, 0, W, H)]
    cands = list(full)
    for rk in regions:
        x1, y1 = max(0, int(rk[0] - 0.03 * (rk[2] - rk[0]))), max(0, int(rk[1]))
        x2, y2 = min(W, int(rk[2] + 0.03 * (rk[2] - rk[0]))), min(H, int(rk[3]))
        rw, rh = x2 - x1, y2 - y1
        if rw < 50 or rh < 50:
            continue
        sh = max(64, int(rw * strip_ratio))
        step = max(32, int(sh * (1 - overlap)))
        ys = list(range(y1, max(y1 + 1, y2 - sh + 1), step))
        if not ys or ys[-1] + sh < y2:
            ys.append(max(y1, y2 - sh))
        for ty in ys:
            crop = rgb[ty:ty + sh, x1:x2]
            _, sl = raw(units, crop)
            for sc, b in sl:
                gb = (b[0] + x1, b[1] + ty, b[2] + x1, b[3] + ty)
                # a box touching a cut edge is a partial unit (unless the cut is the rack/photo edge)
                if (b[1] < 3 and ty > y1) or (b[3] > crop.shape[0] - 3 and ty + sh < y2):
                    continue
                cands.append((sc, gb))
    return cands
