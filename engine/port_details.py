"""Step 6 in detail: every port of a switch, empty or connected, with its specification and its cable.

For one switch cut (BGR) and its layout (pipeline.switch_layouts):
  1. ports    the vendor layout's numbered ports when it fitted; otherwise the RT-EOS port finder's ports, marked as
              positions from the photo (no vendor layout)
  2. status   RT-EOS port_type_finder.pt + port_connected_or_empty.pt (the old port_count.pt) on overlapping, enlarged
              tiles of the cut (tiled_ports); each port takes the status box it overlaps most, as RT-EOS port_pattern does
  3. empty    the RT-EOS dust-cap model (dust_cap_classifier.pth): capped, empty or uncertain
     connected  the RT-EOS cable classifier (cable_type_and_colour) on a crop centred on the port: cable type and colour
  4. spec     the RT-EOS spec agent (switch_specs) for the make and model: the port configuration ("24x 1G RJ45 +
              4x 1G SFP") gives each port its type and speed, RJ45 ports first in number order, then SFP

RT-EOS's readers are found at $RACKTRACK_REPO or engine/readers/ in this folder (its Models link points at models/rt_eos).
"""
import os
import pathlib
import re
import sys

import cv2
import numpy as np

RTEOS = pathlib.Path(os.environ.get("RACKTRACK_REPO", pathlib.Path(__file__).resolve().parents[1] / "engine" / "readers"))
M = RTEOS / "Models"
# the cable step is dark_mobile's (user, 2026-10-29): its cable module and its models. port_count.pt finds which ports are
# connected; each connected port is cut at four times its size (1.5 of its width and height each side, as dark_mobile's
# runner does) and its cable read by cable_eff_best, held to what the port can take (fibre on SFP, copper on RJ45)
_ROOT = pathlib.Path(__file__).resolve().parents[1]
DARK_CABLE_CODE = _ROOT / "engine" / "readers" / "dark_mobile_cable.py"          # dark_mobile's pipeline/cable.py, copied in
DARK_CABLE_MODEL = _ROOT / "models" / "dark_mobile" / "cable_eff_best"
DARK_STATUS_MODEL = _ROOT / "models" / "dark_mobile" / "port_count.pt"


def _dark_cable_module():
    """dark_mobile's pipeline/cable.py, loaded from its file (its own imports are torch, torchvision, cv2 and PIL only)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("dark_mobile_cable", str(DARK_CABLE_CODE))
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod
STATUS_COLOURS = {"connected": (207, 95, 29), "empty": (69, 127, 26), "unknown": (130, 130, 130)}    # BGR
_M = {}


def available():
    return all((M / f).exists() for f in ("port_type_finder.pt", "port_connected_or_empty.pt", "cable_type_and_colour"))


def _rteos_modules(*names):
    """RT-EOS's own modules (package "pipeline"), loaded while our engine/pipeline.py steps aside, then put back."""
    import importlib
    ours = sys.modules.pop("pipeline", None)
    sys.path.insert(0, str(RTEOS))
    try:
        mods = [importlib.import_module(f"pipeline.{n}") for n in names]
    finally:
        sys.path.remove(str(RTEOS))
        rteos_pkg = sys.modules.pop("pipeline", None)
        if ours is not None:
            sys.modules["pipeline"] = ours
        sys.modules["rteos_pipeline"] = rteos_pkg
    return mods


def _models():
    if not _M:
        if str(RTEOS / "switch_specs") not in sys.path:
            sys.path.append(str(RTEOS / "switch_specs"))
        from ultralytics import YOLO
        CB0, CM, PP = _rteos_modules("cable", "cap_model", "port_pattern")
        dark = DARK_CABLE_CODE.exists() and DARK_CABLE_MODEL.exists()
        CB = _dark_cable_module() if dark else CB0
        status_path = DARK_STATUS_MODEL if DARK_STATUS_MODEL.exists() else M / "port_connected_or_empty.pt"
        _M.update(PP=PP, CB=CB, CM=CM, typed=YOLO(str(M / "port_type_finder.pt")), status=YOLO(str(status_path)),
                  cable=CB.load_cable_model(str(DARK_CABLE_MODEL if dark else M / "cable_type_and_colour"), device="cpu"),
                  cable_from="dark_mobile" if dark else "RT-EOS")
        try:
            _M["cap"] = CM.load_cap_model(str(M / "dust_cap_classifier.pth"))
        except Exception:
            _M["cap"] = None
    return _M


_SPECS = {}


def switch_spec(vendor, model):
    """The spec agent's record for this make and model (local database first, then a short web lookup), or None."""
    if not (vendor and model):
        return None
    key = (vendor.lower(), model.upper())
    if key not in _SPECS:
        _models()
        try:
            from agent import SpecAgent
            r = SpecAgent(live=False).answer(f"{vendor} {model}")
            rec = r.get("result") if r.get("type") == "spec" else None
            if not (rec and rec.get("port_config")):
                r = SpecAgent(live=True, live_deadline=8.5).answer(f"{vendor} {model}")
                rec = r.get("result") if r.get("type") == "spec" else rec
        except Exception:
            rec = None
        _SPECS[key] = {k: rec.get(k) for k in ("vendor", "model", "port_count", "port_config", "port_speed_max_gbps", "poe_standard",
                                               "poe_budget_w", "layer", "datasheet_url")} if rec else None
    return _SPECS[key]


def spec_groups(port_config):
    """'24x 1G RJ45 + 4x 1G SFP' -> [("rj45", 24, "1G RJ45"), ("sfp", 4, "1G SFP")]; the first group of each kind only
    (some records repeat the configuration in other words)."""
    out, seen = [], set()
    for n, text in re.findall(r"(\d+)\s*[x×]\s*([^+,;]+)", port_config or ""):
        t = text.strip(); kind = "sfp" if re.search(r"SFP", t, re.I) else "rj45"
        if kind in seen:
            continue
        seen.add(kind)
        sp = re.search(r"(\d+(?:\.\d+)?)\s*G", t, re.I)
        speed = f"{sp.group(1)}G" if sp else ("1G" if re.search(r"1000", t) else ("100M" if re.search(r"10/100\b", t) else ""))
        name = {"rj45": "RJ45", "sfp": "SFP+" if re.search(r"SFP\+", t) else "SFP"}[kind]
        out.append((kind, int(n), f"{speed} {name}".strip()))
    return out


def _box_of(poly):
    xs = [p[0] for p in poly]; ys = [p[1] for p in poly]
    return [min(xs), min(ys), max(xs), max(ys)]


def _iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy; u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


TILE_ASPECT = 2.5          # a switch strip is cut into tiles about this wide for its height, overlapping by a quarter
TILE_SCALE = 2.0           # and each tile is enlarged this much before the port models look at it


def _nms(boxes, iou=0.5):
    keep = []
    for b in sorted(boxes, key=lambda b: -b["conf"]):
        if all(_iou(b["box"], k["box"]) < iou for k in keep):
            keep.append(b)
    return keep


_STATUS_BOXES = {}


def bind_status(box, status_boxes):
    """The status of the status box that overlaps this port most (IoU 0.2), or whose centre lies inside it; else unknown."""
    best = max(status_boxes, key=lambda s_: _iou(box, s_["box"]), default=None)
    if best is None or _iou(box, best["box"]) < 0.2:
        inside = [s_ for s_ in status_boxes if box[0] <= (s_["box"][0] + s_["box"][2]) / 2 <= box[2] and box[1] <= (s_["box"][1] + s_["box"][3]) / 2 <= box[3]]
        best = max(inside, key=lambda s_: s_["conf"], default=None)
    if best is None:
        return "unknown"
    return "connected" if "onnect" in best["name"] else "empty"


def status_on_crop(cut_bgr, box, model):
    """The status model (port_count: Connected_port / Empty_port) on the port's own crop, one port width around it:
    the class of its strongest box, or unknown. Used where the tiled pass left a port unknown (user, 2026-10-29)."""
    H, W = cut_bgr.shape[:2]; x1, y1, x2, y2 = box; w, h = x2 - x1, y2 - y1
    c = cut_bgr[max(0, int(y1 - h)):min(H, int(y2 + h)), max(0, int(x1 - w)):min(W, int(x2 + w))]
    if c.size == 0 or min(c.shape[:2]) < 8:
        return "unknown"
    scale = max(1.0, 160.0 / max(c.shape[:2]))
    if scale > 1.0:
        c = cv2.resize(c, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    try:
        r = model.predict(c, conf=0.2, imgsz=320, verbose=False)[0]
    except Exception:
        return "unknown"
    best = max(zip(r.boxes.conf.tolist(), r.boxes.cls.tolist()), default=None)
    if best is None:
        return "unknown"
    return "connected" if "onnect" in r.names[int(best[1])] else "empty"


def tiled_ports(cut_bgr, m, typed=True):
    """The RT-EOS port finder and port status models on overlapping, enlarged tiles of the switch strip (a thin strip
    shrunk to the models' 640 px leaves each port only a few pixels). Each found port takes the status box it overlaps
    most (IoU 0.3, as RT-EOS port_pattern does): [{"box", "class_name", "category", "status"}] in cut pixels."""
    H, W = cut_bgr.shape[:2]
    tw = int(min(W, max(H * TILE_ASPECT, 200))); step = max(1, int(tw * 0.75))
    xs = list(range(0, max(1, W - tw) + 1, step))
    if xs[-1] + tw < W:
        xs.append(W - tw)
    typed_boxes, status = [], []
    for x0 in xs:
        tile = cv2.resize(cut_bgr[:, x0:x0 + tw], None, fx=TILE_SCALE, fy=TILE_SCALE, interpolation=cv2.INTER_CUBIC)
        for model, out in (((m["typed"], typed_boxes),) if typed else ()) + ((m["status"], status),):
            r = model.predict(tile, conf=0.23, verbose=False)[0]
            for b, c, k in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()):
                out.append({"box": [b[0] / TILE_SCALE + x0, b[1] / TILE_SCALE, b[2] / TILE_SCALE + x0, b[3] / TILE_SCALE], "conf": float(c), "name": r.names[int(k)]})
    typed = [t for t in _nms(typed_boxes) if t["name"].upper() in ("RJ45", "SFP", "QSFP", "FC", "LC", "SC")]
    status = _nms(status)
    _STATUS_BOXES[id(cut_bgr)] = status
    out = []
    for t in typed:
        best = max(status, key=lambda s_: _iou(t["box"], s_["box"]), default=None)
        st = ("connected" if "onnect" in best["name"] else "empty") if best is not None and _iou(t["box"], best["box"]) >= 0.3 else "unknown"
        out.append({"box": t["box"], "class_name": t["name"], "port_category": "main" if t["name"].upper() == "RJ45" else "sfp", "status": st})
    return sorted(out, key=lambda d: ((d["box"][0] + d["box"][2]) / 2, d["box"][1]))


def port_details(cut_bgr, layout):
    """Every port of one switch: number, type, status, cable or cap, and the spec of its port group."""
    m = _models(); PP, CB, CM = m["PP"], m["CB"], m["CM"]
    fitted = bool(layout.get("fitted"))
    auto = bool(layout.get("auto") or layout.get("mode") == "auto") and any(p.get("polygon") for p in layout.get("ports") or [])
    tiled_ports(cut_bgr, m, typed=not (fitted or auto)); sboxes = _STATUS_BOXES.pop(id(cut_bgr), [])   # the port type model only where no layout names the ports
    if fitted or auto:                           # the vendor layout's numbered ports, or the ports the port detector found (auto mode)
        ports = []
        for p in layout.get("ports") or []:
            if p.get("polygon"):
                b = _box_of(p["polygon"])
                st = bind_status(b, sboxes)
                if st == "unknown":
                    st = status_on_crop(cut_bgr, b, m["status"])
                ports.append({"id": p["id"], "type": p.get("type") or "rj45", "box": [round(v, 1) for v in b], "status": st,
                              "category": "sfp" if str(p.get("type") or "").lower() in ("sfp", "qsfp", "osfp", "lc", "sc", "fc") else "main"})
        source = "vendor layout" if fitted else "port detector"
    else:                                        # RT-EOS port logic: the port grid from its rows and pitch
        found = PP.classify_ports_by_pattern(cut_bgr, m["typed"], conf=0.23, status_model=m["status"])
        det = [dict(p, port_category=k[:-6] if k != "main_ports" else "main") for k in ("main_ports", "sfp_ports") for p in found.get(k) or []]
        det.sort(key=lambda d: (d["port_category"] != "main", d.get("index") if d.get("index") is not None else 0))
        ports = []
        for i, d in enumerate(det):
            b = [round(float(v), 1) for v in d["box"]]
            st = d.get("status") if d.get("status") in ("connected", "empty") else bind_status(b, sboxes)
            ports.append({"id": i + 1, "type": "sfp" if d["port_category"] == "sfp" else "rj45", "box": b, "status": st, "category": d["port_category"]})
        source = "photo"
    # cable on connected ports, dust cap on empty ones
    H, W = cut_bgr.shape[:2]
    for p in ports:
        x1, y1, x2, y2 = p["box"]; w, h = x2 - x1, y2 - y1
        if p["status"] == "connected":
            c = cut_bgr[max(0, int(y1 - 1.5 * h)):min(H, int(y2 + 1.5 * h)), max(0, int(x1 - 1.5 * w)):min(W, int(x2 + 1.5 * w))]
            if c.size:
                try:
                    label, conf = CB.classify_cable(c, m["cable"], device="cpu", port_category=p["category"])
                    conn, colour = CB.parse_cable_type_color(label)
                    p.update(cable_type=label, cable_connector=conn, cable_colour=colour, cable_confidence=round(float(conf), 2))
                except Exception:
                    pass
    if m.get("cap"):
        empties = [dict(box=p["box"], status="empty") for p in ports if p["status"] == "empty"]
        try:
            CM.classify_caps(cut_bgr, empties, m["cap"]["predict"])
        except Exception:
            empties = []
        it = iter(empties)
        for p in ports:
            if p["status"] == "empty":
                e = next(it, {})
                if e.get("cap_state"):
                    p["cap_state"] = e["cap_state"]
    # specification per port group (only with a known model)
    spec = switch_spec(layout.get("vendor"), layout.get("model")) if fitted else None
    groups = spec_groups(spec.get("port_config")) if spec else []
    if fitted and not groups:                    # no usable spec record: the port types are the vendor layout's
        spec = dict(spec or {}, note="no port configuration in the spec database: port types are the vendor layout's")
    for kind, _n, label in groups:
        same = sorted((p for p in ports if p["type"] == kind), key=lambda p: p["id"])
        for k, p in enumerate(same):
            p["spec"] = label + (f", PoE ({spec['poe_standard']})" if kind == "rj45" and spec.get("poe_standard") else "")
    counts = {s: sum(p["status"] == s for p in ports) for s in ("connected", "empty", "unknown")}
    return {"source": source, "spec": spec, "ports": ports, "counts": counts, "n": len(ports)}


def _cable_crop(img, box):
    """dark_mobile's cut for the cable model: the port box grown 1.5 of its width and height on every side (4x the box)."""
    H, W = img.shape[:2]; x1, y1, x2, y2 = box; w, h = x2 - x1, y2 - y1
    return img[max(0, int(y1 - 1.5 * h)):min(H, int(y2 + 1.5 * h)), max(0, int(x1 - 1.5 * w)):min(W, int(x2 + 1.5 * w))]


def vote_frames(details, cut_bgr, to_rack, views, max_views=4):
    """The cable of every connected port, and the state of a port the chosen view left unknown, read in the device's
    other video frames too, then voted (user, 2026-10-29: "the cable is not working good, use frames").
    to_rack(pts) maps points of the chosen cut to the stitched rack; views = [(bgr, from_rack)] where from_rack(pts) maps
    rack points into that view's cut. The cable is the label with the highest summed confidence over all the views that
    see the port; 'cable_views' says how many views read it and how many agreed."""
    m = _models(); CB = m["CB"]
    views = views[:max_views]
    if not views:
        return details
    for p in details.get("ports") or []:
        x1, y1, x2, y2 = p["box"]
        rack = to_rack([[x1, y1], [x2, y1], [x2, y2], [x1, y2]])
        boxes = []
        for img, from_rack in views:
            q = from_rack(rack); xs = [a for a, _ in q]; ys = [b for _, b in q]
            b = [min(xs), min(ys), max(xs), max(ys)]; H, W = img.shape[:2]
            if b[2] - b[0] < 4 or b[3] - b[1] < 4 or b[0] < 0 or b[1] < 0 or b[2] > W or b[3] > H:
                continue                                            # the port is not wholly in that frame
            boxes.append((img, b))
        if p["status"] == "unknown" and boxes:                       # the state, by the other views' majority
            said = [status_on_crop(img, b, m["status"]) for img, b in boxes]
            said = [s_ for s_ in said if s_ != "unknown"]
            if said:
                top = max(set(said), key=said.count)
                if said.count(top) * 2 > len(said):
                    p["status"] = top; p["status_views"] = {"views": len(said), "agree": said.count(top)}
        if p["status"] != "connected":
            continue
        score, n = {}, 0
        if p.get("cable_type"):
            score[p["cable_type"]] = float(p.get("cable_confidence") or 0.5); n += 1
        for img, b in boxes:
            c = _cable_crop(img, b)
            if c.size == 0:
                continue
            try:
                label, conf = CB.classify_cable(c, m["cable"], device="cpu", port_category=p.get("category"))
            except Exception:
                continue
            if label:
                score[label] = score.get(label, 0.0) + float(conf); n += 1
        if score:
            label = max(score, key=score.get); conn, colour = CB.parse_cable_type_color(label)
            p.update(cable_type=label, cable_connector=conn, cable_colour=colour, cable_confidence=round(score[label] / max(1, n), 2),
                     cable_views={"views": n, "score": round(score[label] / sum(score.values()), 2)})
    details["counts"] = {s_: sum(q["status"] == s_ for q in details.get("ports") or []) for s_ in ("connected", "empty", "unknown")}
    details["frames_voted"] = len(views)
    return details


def draw(cut_bgr, details):
    """The switch with every port outlined: blue filled = connected (its number), green = empty, grey = not sure."""
    im = cut_bgr.copy(); over = im.copy()
    for p in details["ports"]:
        x1, y1, x2, y2 = map(int, p["box"]); col = STATUS_COLOURS.get(p["status"], STATUS_COLOURS["unknown"])
        if p["status"] == "connected":
            cv2.rectangle(over, (x1, y1), (x2, y2), col, -1)
        cv2.rectangle(im, (x1, y1), (x2, y2), col, 2)
    im = cv2.addWeighted(over, 0.35, im, 0.65, 0)
    for p in details["ports"]:
        x1, y1, x2, y2 = map(int, p["box"]); t = str(p["id"]); fs = max(0.35, (y2 - y1) / 40)
        (tw, th), _ = cv2.getTextSize(t, cv2.FONT_HERSHEY_SIMPLEX, fs, 1)
        cx, cy = (x1 + x2) // 2 - tw // 2, (y1 + y2) // 2 + th // 2
        cv2.putText(im, t, (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, fs, (0, 0, 0), 3, cv2.LINE_AA); cv2.putText(im, t, (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255), 1, cv2.LINE_AA)
    return im
