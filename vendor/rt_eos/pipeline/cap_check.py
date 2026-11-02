"""Dust-cap check: is an empty port open, or is a dust cap on it.

Runs after the port models on the device crop (pipeline/runner.py), on a zone
close-up (pipeline/zone_read.py) and when a person re-counts a device
(pipeline/worker.py). It reads pixels and nothing else: this product has no
network access, so the photo is the only witness.

Where it fits:

    port boxes + status (port_connected_or_empty.pt)
      -> "connected" with confidence CONNECTED_SURE or more stays connected
      -> every other port is checked here
      -> cap_state in {connected, empty, capped, uncertain, too_small}
         cap_reason, a short plain sentence ("gold pins found", "red cap")

The check, in order (check_caps):

  1. a port box under MIN_PORT_WIDTH px is too_small: the photo cannot show
     a cap at that size (the app can ask for a closer picture)
  2. ports are grouped by shape (RJ45 about square, SFP cages wider than
     tall, the model's type when it gave one) and by row, so a port is
     compared with its neighbours: "row 3, RJ45 ports"
  3. each port is cut out with a little padding, resized to a fixed size,
     lit evenly (CLAHE on the brightness channel) and split into a centre
     and the ring around it
  4. features per port: gold ratio, darkness, edge density, centre spread
     and colour strength (port_features)
  5. rules, in order: A gold pins in the centre -> empty; B a centre full
     of one strong colour -> capped; C the group split in two by k-means,
     trusted only when the two clusters stand clearly apart; D a per-port
     fallback, else uncertain
  6. a low-confidence "connected" port or a port of unknown status: a real
     cable leaves the box as a line of one colour; nothing leaving and a
     flat centre -> capped (a thick rubber cap can look like a plug)
  7. SFP cages: an open cage (bare metal, many edges, no colour) -> empty;
     a flat plug face -> capped; a clear plastic plug mostly lands in
     uncertain, which is the honest answer

Every threshold below is a starting point. Pure functions over numpy and
OpenCV, no model weights, tested with drawn ports in
pipeline/tests/test_cap_check.py.
"""

from __future__ import annotations

import logging
import statistics

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── Thresholds (starting points) ─────────────────────────────────────────

# Step 1: a port narrower than this cannot show a cap.
MIN_PORT_WIDTH = 40
# A "connected" port at this confidence or more is not checked.
CONNECTED_SURE = 0.6

# Step 3: the cut-out.
PAD_FRACTION = 0.10
SIZE_RJ45 = (64, 64)  # (width, height)
SIZE_SFP = (96, 64)
CENTRE_FRACTION = 0.5  # the inner half of each side is the centre
# A box wider than this many times its height reads as a cage.
SFP_ASPECT = 1.4

# Step 4: gold pins (OpenCV hue runs 0 to 179; gold sits around 15 to 35).
GOLD_HUE = (15, 35)
GOLD_SAT_MIN = 80
GOLD_VAL_MIN = 120
# Share of centre pixels that are gold before pins are called found. Pins sit
# in a hole with texture around them; a whole flat face of yellow is a cap,
# which rule B names (it needs a spread of at least SPREAD_LOW to be pins).
GOLD_PINS_MIN = 0.03

# Step 5 B: a centre full of one strong colour.
COLOUR_SAT_MIN = 110
COLOUR_VAL_MIN = 60
COLOUR_SHARE_MIN = 0.4

# Steps 5 C and D: what the features look like on an open port and a cap.
DARK_EMPTY = 0.5  # centre brightness over ring brightness, an open hole is dark
DARK_CAPPED = 0.8  # a cap face is about as bright as the faceplate
EDGE_HIGH = 0.08  # share of Canny edge pixels in the centre
EDGE_LOW = 0.03
SPREAD_LOW = 12.0  # std of grey in the centre; a flat face is under this
MIN_GROUP = 4
SILHOUETTE_MIN = 0.5
# The two clusters must also differ in real terms, not only relative to the
# group: this keeps a row of identical open ports from being split in two.
GROUP_MARGIN_DARK = 0.15
GROUP_MARGIN_EDGE = 0.04
GROUP_MARGIN_SPREAD = 8.0

# Step 6: a cable leaving the port.
CABLE_REACH = 1.5  # how far outside the box to look, in box heights
CABLE_COLOUR_TOL = 25.0  # slices of one cable differ less than this (BGR)
CABLE_FACE_DIFF = 25.0  # and the cable differs from the faceplate by more

_SFP_FAMILY = {"SFP", "QSFP", "OSFP", "FC", "LC", "SC"}
_CHECKED_STATES = ("connected", "empty", "capped", "uncertain", "too_small")


# ── Small helpers ────────────────────────────────────────────────────────


def _set(port, state, reason):
    port["cap_state"] = state
    port["cap_reason"] = reason
    return port


def _box(port):
    b = port.get("box")
    if not b or len(b) < 4:
        return None
    x1, y1, x2, y2 = (int(round(float(v))) for v in b[:4])
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def port_kind(port):
    """"sfp" for a transceiver cage or fibre connector, "rj45" for the rest.

    The model's type wins when it gave one; otherwise the box shape decides
    (a cage is clearly wider than tall).
    """
    name = str(port.get("class_name") or "")
    if name in _SFP_FAMILY or port.get("port_category") == "sfp":
        return "sfp"
    if name == "RJ45" or port.get("port_category") in ("main", "console"):
        return "rj45"
    b = _box(port)
    if b is None:
        return "rj45"
    w, h = b[2] - b[0], b[3] - b[1]
    return "sfp" if w >= SFP_ASPECT * h else "rj45"


def group_ports(ports, kinds=None):
    """Group ports by row and by kind.

    Rows are clustered on the vertical centre: a port more than 0.6 of the
    median port height away from the row being built starts a new row. Rows
    are numbered from the top, 1 first. Returns a list of
    {"row": n, "kind": "rj45" | "sfp", "members": [port indices]}.
    """
    kinds = kinds or [port_kind(p) for p in ports]
    idx = [i for i, p in enumerate(ports) if _box(p) is not None]
    if not idx:
        return []
    heights = [max(1.0, float(_box(ports[i])[3] - _box(ports[i])[1])) for i in idx]
    tol = 0.6 * statistics.median(heights)

    def cy(i):
        b = _box(ports[i])
        return (b[1] + b[3]) / 2.0

    rows = []
    for i in sorted(idx, key=cy):
        if rows:
            row = rows[-1]
            mean = sum(cy(j) for j in row) / len(row)
            if abs(cy(i) - mean) <= tol:
                row.append(i)
                continue
        rows.append([i])
    out = []
    for r, row in enumerate(rows, 1):
        by_kind = {}
        for i in sorted(row, key=lambda i: _box(ports[i])[0]):
            by_kind.setdefault(kinds[i], []).append(i)
        for kind in ("rj45", "sfp"):
            if kind in by_kind:
                out.append({"row": r, "kind": kind, "members": by_kind[kind]})
    return out


def colour_name(hue):
    """A plain name for an OpenCV hue (0 to 179)."""
    h = float(hue) % 180.0
    if h < 8 or h >= 165:
        return "red"
    if h < 18:
        return "orange"
    if h < 38:
        return "yellow"
    if h < 85:
        return "green"
    if h < 130:
        return "blue"
    return "purple"


# ── Step 3 and 4: the cut-out and its features ───────────────────────────


def port_features(crop, box, kind="rj45"):
    """The features of one port cut out of `crop` (BGR), or None when the box
    lies outside the picture.

    gold_ratio    share of centre pixels in the gold range (pins)
    darkness      centre brightness over ring brightness (an open hole is low)
    edge_density  share of Canny edge pixels in the centre
    spread        std of grey in the centre (a flat face is low)
    colour_share  share of centre pixels with a strong colour
    colour_sat    mean saturation of the centre
    colour_hue    circular mean hue of the strongly coloured pixels, or None
    colour_name   its name, when the share is at least COLOUR_SHARE_MIN
    """
    h_img, w_img = crop.shape[:2]
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    px, py = int(round(w * PAD_FRACTION)), int(round(h * PAD_FRACTION))
    cx1, cy1 = max(0, x1 - px), max(0, y1 - py)
    cx2, cy2 = min(w_img, x2 + px), min(h_img, y2 + py)
    if cx2 - cx1 < 4 or cy2 - cy1 < 4:
        return None
    cut = crop[cy1:cy2, cx1:cx2]
    if cut.ndim == 2:
        cut = cv2.cvtColor(cut, cv2.COLOR_GRAY2BGR)
    size = SIZE_SFP if kind == "sfp" else SIZE_RJ45
    cut = cv2.resize(cut, size, interpolation=cv2.INTER_AREA)

    # Lighting is evened out (CLAHE on the L channel) for the reads that look
    # for pins, edges and colour in a dark hole. Darkness and spread are read
    # from the raw grey: equalising would lift a dark hole to mid grey and
    # amplify the noise on a flat face, the two things they measure.
    raw_grey = cv2.cvtColor(cut, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(cut, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(2, 2))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    even = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    hsv = cv2.cvtColor(even, cv2.COLOR_BGR2HSV)
    grey = cv2.cvtColor(even, cv2.COLOR_BGR2GRAY)

    sw, sh = size
    mx = int(round(sw * (1.0 - CENTRE_FRACTION) / 2.0))
    my = int(round(sh * (1.0 - CENTRE_FRACTION) / 2.0))
    mask = np.zeros((sh, sw), dtype=bool)
    mask[my : sh - my, mx : sw - mx] = True

    c_hsv = hsv[mask].astype(np.int32)
    c_grey = raw_grey[mask].astype(np.float64)
    r_grey = raw_grey[~mask].astype(np.float64)
    hue, sat, val = c_hsv[:, 0], c_hsv[:, 1], c_hsv[:, 2]

    gold = (hue >= GOLD_HUE[0]) & (hue <= GOLD_HUE[1]) & (sat >= GOLD_SAT_MIN) & (val >= GOLD_VAL_MIN)
    gold_ratio = float(gold.mean())
    darkness = float(c_grey.mean() / max(1.0, r_grey.mean()))
    edges = cv2.Canny(grey, 50, 150)
    edge_density = float((edges[mask] > 0).mean())
    spread = float(c_grey.std())

    coloured = (sat >= COLOUR_SAT_MIN) & (val >= COLOUR_VAL_MIN)
    colour_share = float(coloured.mean())
    colour_hue = None
    if coloured.any():
        ang = np.deg2rad(hue[coloured].astype(np.float64) * 2.0)
        mean_ang = np.arctan2(np.sin(ang).mean(), np.cos(ang).mean())
        colour_hue = float((np.rad2deg(mean_ang) % 360.0) / 2.0)
    name = colour_name(colour_hue) if colour_hue is not None and colour_share >= COLOUR_SHARE_MIN else None

    return {
        "gold_ratio": gold_ratio,
        "darkness": darkness,
        "edge_density": edge_density,
        "spread": spread,
        "colour_share": colour_share,
        "colour_sat": float(sat.mean()),
        "colour_hue": colour_hue,
        "colour_name": name,
    }


# ── Step 6: does a cable leave the port ──────────────────────────────────


def cable_leaves(crop, box):
    """True when a line of one colour runs out of the box the way a cable does.

    Looks below, above, left and right of the box, CABLE_REACH box heights
    out. In each direction the strip is cut into four slices along its
    length; a cable shows as slices of one colour (they differ by less than
    CABLE_COLOUR_TOL) that is not the faceplate's colour (they differ from
    the band of plate just around the box by more than CABLE_FACE_DIFF). A
    cap ends at the edge of the box, so nothing continues.
    """
    h_img, w_img = crop.shape[:2]
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    # The plate: a thin band just outside the box, read from the raw pixels.
    bx, by = max(2, w // 5), max(2, h // 5)
    fx1, fy1 = max(0, x1 - bx), max(0, y1 - by)
    fx2, fy2 = min(w_img, x2 + bx), min(h_img, y2 + by)
    band = crop[fy1:fy2, fx1:fx2].astype(np.float64)
    inner = crop[y1:y2, x1:x2].astype(np.float64)
    n = band.shape[0] * band.shape[1] - inner.shape[0] * inner.shape[1]
    if n <= 0:
        return False
    face_bgr = (band.reshape(-1, 3).sum(axis=0) - inner.reshape(-1, 3).sum(axis=0)) / n
    reach_y, reach_x = int(round(h * CABLE_REACH)), int(round(w * CABLE_REACH))
    # The middle half of the box's width (or height) so a neighbour's port is
    # not read as this port's cable.
    sx1, sx2 = x1 + w // 4, x2 - w // 4
    sy1, sy2 = y1 + h // 4, y2 - h // 4
    strips = [
        (crop[y2 : min(h_img, y2 + reach_y), sx1:sx2], 0),  # below
        (crop[max(0, y1 - reach_y) : y1, sx1:sx2], 0),  # above
        (crop[sy1:sy2, x2 : min(w_img, x2 + reach_x)], 1),  # right
        (crop[sy1:sy2, max(0, x1 - reach_x) : x1], 1),  # left
    ]
    for strip, axis in strips:
        if strip.size == 0:
            continue
        length = strip.shape[axis]
        if length < 8:
            continue
        parts = np.array_split(strip.astype(np.float64), 4, axis=axis)
        means = [p.reshape(-1, 3).mean(axis=0) for p in parts if p.size]
        if len(means) < 4:
            continue
        spread = max(np.linalg.norm(a - b) for a in means for b in means)
        away = np.linalg.norm(np.mean(means, axis=0) - face_bgr)
        if spread <= CABLE_COLOUR_TOL and away >= CABLE_FACE_DIFF:
            return True
    return False


# ── Step 5 C: a tiny k-means and silhouette (scikit-learn is not installed) ──


def kmeans2(X, iters=30):
    """Two clusters, seeded with the two points farthest apart.

    Returns (labels, centres). Every label is 0 or 1.
    """
    X = np.asarray(X, dtype=np.float64)
    n = len(X)
    if n < 2:
        return np.zeros(n, dtype=int), X[:1].copy()
    d = np.linalg.norm(X[:, None, :] - X[None, :, :], axis=2)
    i, j = np.unravel_index(int(np.argmax(d)), d.shape)
    centres = X[[i, j]].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        dist = np.linalg.norm(X[:, None, :] - centres[None, :, :], axis=2)
        new_labels = np.argmin(dist, axis=1)
        new_centres = centres.copy()
        for k in (0, 1):
            if (new_labels == k).any():
                new_centres[k] = X[new_labels == k].mean(axis=0)
        done = np.array_equal(new_labels, labels) and np.allclose(new_centres, centres)
        labels, centres = new_labels, new_centres
        if done:
            break
    return labels, centres


def silhouette(X, labels):
    """The mean silhouette of a two-cluster split; 0 when a cluster is empty
    or has one member."""
    X = np.asarray(X, dtype=np.float64)
    labels = np.asarray(labels)
    if len(X) < 2 or len(set(labels.tolist())) < 2:
        return 0.0
    d = np.linalg.norm(X[:, None, :] - X[None, :, :], axis=2)
    scores = []
    for i in range(len(X)):
        same = (labels == labels[i]) & (np.arange(len(X)) != i)
        other = labels != labels[i]
        if not same.any():
            scores.append(0.0)
            continue
        a = d[i, same].mean()
        b = d[i, other].mean()
        scores.append((b - a) / max(a, b) if max(a, b) > 0 else 0.0)
    return float(np.mean(scores))


def split_group(feats):
    """Split one group of port features into open ports and capped ports.

    feats: list of feature dicts. The features are scaled within the group,
    split in two by k-means and trusted only when the silhouette is at least
    SILHOUETTE_MIN and the two clusters differ in real terms. Returns a list
    of "empty" | "capped" per port, or None when no split can be trusted.
    """
    if len(feats) < MIN_GROUP:
        return None
    raw = np.array([[f["darkness"], f["edge_density"], f["spread"]] for f in feats], dtype=np.float64)
    std = raw.std(axis=0)
    scaled = (raw - raw.mean(axis=0)) / np.where(std > 1e-9, std, 1.0)
    labels, _ = kmeans2(scaled)
    if len(set(labels.tolist())) < 2:
        return None
    if silhouette(scaled, labels) < SILHOUETTE_MIN:
        return None
    m0, m1 = raw[labels == 0].mean(axis=0), raw[labels == 1].mean(axis=0)
    gap = np.abs(m0 - m1)
    if not (gap[0] >= GROUP_MARGIN_DARK or gap[1] >= GROUP_MARGIN_EDGE or gap[2] >= GROUP_MARGIN_SPREAD):
        return None
    # The darker, more-edged, less-uniform cluster is the open one.
    score = lambda m: -m[0] / max(GROUP_MARGIN_DARK, 1e-9) + m[1] / GROUP_MARGIN_EDGE + m[2] / GROUP_MARGIN_SPREAD  # noqa: E731
    open_label = 0 if score(m0) > score(m1) else 1
    return ["empty" if lab == open_label else "capped" for lab in labels]


# ── Step 5 D: the per-port fallback ──────────────────────────────────────


def fallback(f, kind, row, small_group):
    """What one port's own features say when the group could not be split."""
    if kind == "sfp":
        if f["edge_density"] >= EDGE_HIGH and f["spread"] >= SPREAD_LOW and f["colour_share"] < 0.2:
            return "empty", "open cage, metal edges inside"
        if f["spread"] < SPREAD_LOW and f["edge_density"] < EDGE_LOW:
            return "capped", "flat plug face"
    else:
        if f["darkness"] < DARK_EMPTY and f["edge_density"] >= EDGE_HIGH:
            return "empty", "dark hole with edges inside"
        if f["darkness"] >= DARK_CAPPED and f["spread"] < SPREAD_LOW:
            return "capped", "flat face, no hole"
    if small_group:
        return "uncertain", f"too few ports in row {row} to compare"
    return "uncertain", f"no clear difference in row {row}"


# ── The entry point ──────────────────────────────────────────────────────


def check_caps(crop, ports, min_width=MIN_PORT_WIDTH):
    """Set cap_state and cap_reason on every port dict and return the list.

    crop:  the picture the boxes are in (BGR numpy array)
    ports: port dicts with box [x1, y1, x2, y2] in crop coordinates, status,
           class_name and confidence, as pipeline.port_pattern builds them.
    status is never changed. A "connected" port with confidence
    CONNECTED_SURE or more is "connected" without a look.
    """
    if crop is None or not ports:
        return ports
    n = len(ports)
    kinds = [port_kind(p) for p in ports]
    feats = [None] * n
    pending = []
    for i, p in enumerate(ports):
        status = p.get("status")
        try:
            conf = float(p.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        if status == "connected" and conf >= CONNECTED_SURE:
            _set(p, "connected", "cable in port")
            continue
        box = _box(p)
        if box is None:
            _set(p, "uncertain", "no port box")
            continue
        if box[2] - box[0] < min_width:
            _set(p, "too_small", f"port under {min_width} px")
            continue
        f = port_features(crop, box, kinds[i])
        if f is None:
            _set(p, "uncertain", "port box outside the picture")
            continue
        feats[i] = f
        if status != "empty":
            # Step 6: a low-confidence "connected" port, or one the status
            # model did not tag. A cable that leaves the box settles it.
            if cable_leaves(crop, box):
                _set(p, "connected", "cable leaves the port")
                continue
            if f["spread"] < SPREAD_LOW and f["edge_density"] < EDGE_LOW:
                colour = f"{f['colour_name']} " if f["colour_name"] else ""
                _set(p, "capped", f"no cable leaves the port, flat {colour}face")
                continue
        pending.append(i)

    # Rules A and B on each port by itself.
    rest = []
    for i in pending:
        f = feats[i]
        if f["gold_ratio"] >= GOLD_PINS_MIN and f["spread"] >= SPREAD_LOW:
            _set(ports[i], "empty", "gold pins found")
        elif f["colour_name"]:
            _set(ports[i], "capped", f"{f['colour_name']} cap")
        else:
            rest.append(i)
    if not rest:
        return ports

    # Rules C and D within each group.
    rest_set = set(rest)
    for g in group_ports(ports, kinds):
        members = [i for i in g["members"] if i in rest_set]
        if not members:
            continue
        row = g["row"]
        split = split_group([feats[i] for i in members])
        if split is not None:
            for i, state in zip(members, split):
                word = "open" if state == "empty" else "flat"
                _set(ports[i], state, f"grouped with {word} ports in row {row}")
            continue
        for i in members:
            state, reason = fallback(feats[i], kinds[i], row, len(members) < MIN_GROUP)
            _set(ports[i], state, reason)
    return ports


def summarize(ports):
    """Counts of cap_state over a list of ports: {state: n}."""
    out = {}
    for p in ports:
        s = p.get("cap_state")
        if s:
            out[s] = out.get(s, 0) + 1
    return out
