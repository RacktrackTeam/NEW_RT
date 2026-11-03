"""Patch panels: the team's patchpanel_classify_overlay package run on one device's crop. Step 1 names the panel's type by
a DINOv2 match against its gallery, step 2 checks that with the YOLO port detector and boxes every port (a named connector
= a cable plugged in, "empty" = nothing in the slot), step 3 draws them. Nothing in the package is changed: it is run as
its own command (its two models stay in separate processes, as its README asks) and its CSV is read back.

    python3 src/patch_panels.py data/runs/<id> <device index>        prints what it reads
"""
import csv
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = pathlib.Path(os.environ.get("RACKTRACK_PATCHPANELS", ROOT / "patchpanel_classify_overlay"))
PANEL_WORDS = {"rj45": "RJ45", "fiber": "Fiber", "fibre": "Fiber", "keystone": "Keystone", "coax": "Coax"}
STANDARD_SIZES = (12, 24, 48, 72, 96)    # patch panels come in these sizes (user, 2026-10-27: "12, 24, 48, 72...")
SEEN_SHARE = 0.8                          # ... and the size is taken when this share of it was seen


def available():
    return (PKG / "run_classify_overlay.py").exists() and (PKG / "port_model.pt").exists()


def _iou(a, b):
    ax1, ay1, ax2, ay2 = min(q[0] for q in a), min(q[1] for q in a), max(q[0] for q in a), max(q[1] for q in a)
    bx1, by1, bx2, by2 = min(q[0] for q in b), min(q[1] for q in b), max(q[0] for q in b), max(q[1] for q in b)
    iw, ih = max(0, min(ax2, bx2) - max(ax1, bx1)), max(0, min(ay2, by2) - max(ay1, by1)); inter = iw * ih
    return inter / max(1e-6, (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter)


def dedupe(ports):
    """Two boxes on one jack: the first stays (a 24-port panel read as 25 otherwise). Jacks never overlap, so two boxes
    whose centres sit closer than 0.6 of a box's width and height are one jack."""
    def c(q):
        xs = [v[0] for v in q]; ys = [v[1] for v in q]
        return (sum(xs) / len(xs), sum(ys) / len(ys), max(xs) - min(xs), max(ys) - min(ys))
    kept = []
    for p in ports:
        x, y, w, h = c(p["polygon"])
        if not any(abs(x - kx) < 0.6 * min(w, kw) and abs(y - ky) < 0.6 * min(h, kh) for kx, ky, kw, kh in (c(k["polygon"]) for k in kept)):
            kept.append(p)
    return kept


def complete_grid(ports, size):
    """Patch panel ports sit in a fixed grid: rows of 24 (12 for a 12-port panel), in groups with a wider gap between them
    (user, 2026-10-27: 'patch panels follow the rows of clustered patterns'). The boxes the detector found are laid on that
    grid and the slots it could not see (cables over them) are filled in as hidden ports, so every port has its place and
    its number, 1 to size, row by row and left to right. Returns the ports with id, polygon, status and seen."""
    if not ports or not size:
        return ports
    ports = dedupe(ports)
    per_row = 12 if size <= 12 else 24
    n_rows = max(1, round(size / per_row))
    boxes = [(sum(q[0] for q in p["polygon"]) / 4, sum(q[1] for q in p["polygon"]) / 4, max(q[0] for q in p["polygon"]) - min(q[0] for q in p["polygon"]),
              max(q[1] for q in p["polygon"]) - min(q[1] for q in p["polygon"]), p) for p in ports]
    w = sorted(b[2] for b in boxes)[len(boxes) // 2]; h = sorted(b[3] for b in boxes)[len(boxes) // 2]
    rows = []                                                           # cluster by centre y
    for b in sorted(boxes, key=lambda t: t[1]):
        if rows and abs(b[1] - rows[-1]["y"]) <= 0.6 * h:
            r = rows[-1]; r["items"].append(b); r["y"] = sum(x[1] for x in r["items"]) / len(r["items"])
        else:
            rows.append({"y": b[1], "items": [b]})
    rows = sorted(rows, key=lambda r: -len(r["items"]))[:n_rows]; rows.sort(key=lambda r: r["y"])
    if len(rows) < n_rows:
        return ports                                                    # a row not seen at all: nothing to lay the grid on
    # the port pitch and the group gap from the row with most boxes. The detector's boxes are often wider than the jacks'
    # spacing, so the pitch is the spacing itself and each slot's box is narrowed to fit inside it (user, 2026-10-27: ports
    # come one after another, never overlapping); widening the pitch to the box instead made a tilted panel's grid drift
    best = max(rows, key=lambda r: len(r["items"])); xs = sorted(b[0] for b in best["items"])
    gaps = [b - a for a, b in zip(xs, xs[1:])]
    if not gaps:
        return ports
    p0 = sorted(gaps)[len(gaps) // 2]
    small = sorted(g for g in gaps if g < 1.5 * p0); big = sorted(g for g in gaps if 1.5 * p0 <= g < 2.6 * p0)
    if not small:
        return ports
    pitch = small[len(small) // 2]; gap = max(big[len(big) // 2] if big else pitch, pitch)
    def slots(x0, pt, gp, group):
        return [x0 + k * pt + (k // group) * (gp - pt) for k in range(per_row)]
    # the columns are shared by every row. The group size (ports between two wider gaps) and the first column are the ones
    # that put most boxes of all rows on a slot: any box can anchor the grid, so a stray box on a screw cannot, and jacks
    # missed under cables cannot break the groups up; then pitch, group gap and start are fitted to the matched boxes
    allb = [b for r in rows for b in r["items"]]
    def score(xs_):
        m = [(min(range(per_row), key=lambda k: abs(b[0] - xs_[k])), b) for b in allb]
        m = [(k, b) for k, b in m if abs(b[0] - xs_[k]) < 0.4 * pitch]
        return len(m), -sum(abs(b[0] - xs_[k]) for k, b in m), m
    best_fit = None
    for group in [g for g in (6, 4, 8, 12, 3) if per_row % g == 0 and g < per_row] or [per_row]:
        for b in best["items"]:
            for k in range(per_row):
                x0 = b[0] - (k * pitch + (k // group) * (gap - pitch)); sc = score(slots(x0, pitch, gap, group))
                if best_fit is None or sc[:2] > best_fit[0][:2]:
                    best_fit = (sc, x0, group)
    (n_hit, _, matched), x0, group = best_fit
    pt, gp = pitch, gap
    if n_hit >= 8 and len({k // group for k, _ in matched}) >= 2:       # least squares on x = x0 + k*pitch + group*(gap - pitch)
        import numpy as np
        A = np.array([[1.0, k, k // group] for k, _ in matched]); y = np.array([b[0] for _, b in matched])
        (a0, a1, a2), *_ = np.linalg.lstsq(A, y, rcond=None)
        if 0.85 * pitch < a1 < 1.15 * pitch and a2 >= 0:
            fit = slots(a0, a1, a1 + a2, group)
            if score(fit)[0] >= n_hit:
                x0, pt, gp = a0, a1, a1 + a2
    xs_ = slots(x0, pt, gp, group); bw = min(w, 0.9 * pt)
    out = []
    for ri, r in enumerate(rows):
        items = sorted(r["items"], key=lambda b: b[0])
        on = [(b[0], b[1]) for b in items if min(abs(b[0] - x) for x in xs_) < 0.4 * pt]
        if len(on) >= 4:                                                # the row's own tilt: its centre line through its boxes
            mx = sum(x for x, _ in on) / len(on); my = sum(y for _, y in on) / len(on)
            vx = sum((x - mx) ** 2 for x, _ in on)
            sl = sum((x - mx) * (y - my) for x, y in on) / vx if vx else 0.0
            sl = max(-0.08, min(0.08, sl))
        else:
            mx, my, sl = 0.0, r["y"], 0.0
        used = set()
        for k, x in enumerate(xs_):
            near = min(items, key=lambda b: abs(b[0] - x))
            y = my + sl * (x - mx)
            poly = [[round(v, 1) for v in q] for q in ([x - bw / 2, y - h / 2], [x + bw / 2, y - h / 2], [x + bw / 2, y + h / 2], [x - bw / 2, y + h / 2])]   # every port is its slot's box
            if abs(near[0] - x) < 0.4 * pt and id(near) not in used:
                used.add(id(near)); p = near[4]
                out.append(dict(p, polygon=poly, seen=True, row=ri, col=k))
            else:
                out.append({"type": "hidden", "status": "connected", "inferred": True, "polygon": poly, "seen": False, "row": ri, "col": k})
    for i, p in enumerate(out):
        p["id"] = i + 1
    return out


def read_patch_panel(run_dir, index, crop_path, timeout=1200):
    """The panel type and every port of device D<index> from its crop. Returns the layout kept with the device:
    {mode: "patchpanel", panel, panel_status, why, ports: [{id, type, status, polygon (crop px)}], n_ports, connected,
    by_type, pictures: {cut, overlay}}. The first run embeds the package's gallery (about ten minutes, once)."""
    run_dir = pathlib.Path(run_dir); crop_path = pathlib.Path(crop_path)
    work = run_dir / "patch_panels" / f"D{index}"; shutil.rmtree(work, ignore_errors=True); (work / "crops").mkdir(parents=True)
    shutil.copy(crop_path, work / "crops" / f"D{index}.jpg")
    name = f"{run_dir.name}_D{index}"
    env = dict(os.environ, OMP_NUM_THREADS=os.environ.get("OMP_NUM_THREADS", "2"))
    p = subprocess.run([sys.executable, "run_classify_overlay.py", "--images", str(work / "crops"), "--name", name, "--numbers"],
                       cwd=PKG, env=env, capture_output=True, text=True, timeout=timeout)
    (work / "log.txt").write_text(p.stdout[-20000:] + "\n" + p.stderr[-20000:])
    ver = PKG / "out" / f"verified_4class_{name}.csv"
    if p.returncode != 0 or not ver.exists():
        raise RuntimeError(f"the patch panel reader failed (exit {p.returncode}): {p.stderr.strip().splitlines()[-1:] or p.stdout[-200:]}")
    row = next(csv.DictReader(open(ver)))
    boxes = json.loads(row.get("port_boxes") or "[]"); classes = json.loads(row.get("port_classes") or "[]")
    found = sorted(zip(boxes, classes), key=lambda t: (round(t[0][1] / max(1.0, t[0][3] - t[0][1])), t[0][0]))   # row by row, left to right
    ports = dedupe([{"type": c, "status": "empty" if c == "empty" else "connected",
                     "polygon": [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]} for b, c in found])
    for i, p_ in enumerate(ports):
        p_["id"] = i + 1
    by_type = {}
    for p_ in ports:
        by_type[p_["type"]] = by_type.get(p_["type"], 0) + 1
    pics = {"cut": crop_path.name}
    ov = PKG / "out" / f"overlays_{name}" / f"D{index}.jpg"
    if ov.exists():
        dst = run_dir / f"panel_D{index}_overlay.jpg"; shutil.copy(ov, dst); pics["overlay"] = dst.name
    for f in (PKG / "out").glob(f"*_{name}.csv"):                      # the package's out/ keeps only its own gallery files
        f.unlink()
    shutil.rmtree(PKG / "out" / f"overlays_{name}", ignore_errors=True)
    for f in (PKG / "out" / "cache").glob(f"test_{name}_*.npz"):        # this panel's embeddings: runtime data, not the package's
        f.unlink()
    # a port hidden behind a cable gets no box: when at least SEEN_SHARE of a standard panel size is seen, the panel is that size
    size = next((n for n in STANDARD_SIZES if len(ports) <= n), None)
    below = max((n for n in STANDARD_SIZES if n < len(ports)), default=None)
    if below and len(ports) <= 1.1 * below:                              # a box or two over a standard size: a stray box, the grid drops it
        total = below
    else:
        total = size if size and len(ports) >= SEEN_SHARE * size else len(ports)
    seen = len(ports); size_known = total in STANDARD_SIZES                 # too few seen for any standard size: the size stays open
    if total != seen:
        ports = complete_grid(ports, total)                              # every slot of the panel, the unseen ones as hidden
        seen = sum(1 for p_ in ports if p_.get("seen", True))            # boxes off the grid (a screw, a cable tag) are not ports
    final = (row.get("final_class") or "").strip().lower(); key = "".join(ch for ch in final if ch.isalnum())   # "RJ-45" -> rj45
    return {"ok": True, "mode": "patchpanel", "panel": (PANEL_WORDS.get(key, final.capitalize() or None) if ports else None),   # no port seen: no type either (a sealed enclosure) "panel_status": row.get("verify_status"),
            "why": row.get("verify_why"), "port_counts": row.get("port_counts"), "ports": ports, "n_ports": max(total, len(ports)), "seen": seen, "hidden": max(total, len(ports)) - seen, "size_known": size_known,
            "connected": sum(p_["status"] == "connected" for p_ in ports), "by_type": by_type, "fitted": False, "auto": False,
            "vendor": None, "model": None, "pictures": pics}


if __name__ == "__main__":
    d = pathlib.Path(sys.argv[1]); i = int(sys.argv[2])
    out = read_patch_panel(d, i, d / f"section_D{i}_clean.jpg")
    print(json.dumps({k: v for k, v in out.items() if k != "ports"}, indent=1), "ports", len(out["ports"]))
