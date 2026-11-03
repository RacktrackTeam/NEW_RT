"""The rack pipeline on one photo, each step with its best approach:

    step 1  units      RackTrack unit model + logic (unit_persp.method_rteos_logic): where every unit begins and ends
    step 2  occupancy  the ensemble of unit classifiers with the device cross-check and the side-angle rule
                       (occupancy.classify_units): occupied / unoccupied / uncertain per unit, with the reason
    step 3  devices    the device detector + the one logic (device_model.group); the logic alone (device_join.group)
                       when the detector's code is not installed (DEIMV2_DIR)
    step 4  kinds      the device classifier on each device's cut (classify_crops: our ConvNeXt-Tiny, to be replaced)
    step 5  device layout  for every Switch: make and model read off its panel, its vendor front image and layout template
    step 6  port layout    that layout fitted onto the photo, every port numbered with its outline (switch_layouts,
                       Srikanth's port agents in port_agents/, template or auto mode)

Command line:  python3 src/pipeline.py PHOTO [--out DIR]   prints the result as JSON and writes the four pictures.
The server (server/app.py) calls units_from_photo, device_rows and the draw_* functions.
"""
import argparse
import json
import os
import pathlib
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine"))
import device_join as DJ      # noqa: E402
import device_model as DM     # noqa: E402
import occupancy as occ       # noqa: E402
import unit_logic             # noqa: E402
import unit_persp             # noqa: E402

STEP1 = "RackTrack unit model + logic"
STEP3_MODEL, STEP3_LOGIC = "device detector + logic", "logic only (detector not installed)"

# ------------------------------------------------------------------ step 4: device classification
# The one place the device classifier is called, for photos and videos (src/rack_video.py uses it too). Our ConvNeXt-Tiny
# (src/device_classifier.py) stands in until the final one: to replace it, change classify_crops() and CLASSIFIER_NAME only.
# It takes crops (the device cut along its shape, outside black) and returns per crop {"label", "class", "prob", ...}.
CLASSIFIER_NAME = "ConvNeXt-Tiny (ours, to be replaced)"
_CLF = None


def _classifier():
    global _CLF
    if _CLF is None:
        from device_classifier import DeviceClassifier
        _CLF = DeviceClassifier()
    return _CLF


def classify_crops(crops):
    return _classifier().classify(crops)


def classify_views(views, weights=None):
    """One answer for one device from several pictures of it (its best video frames): features fused, one answer."""
    return _classifier().classify_views(views, weights)


def classify_photo_devices(img, devices):
    """Step 4 on a photo: each device is cut by its box (the photo has one view, so no best frame) and classified."""
    from device_classifier import crop_polygon
    if not devices:
        return devices
    crops = [crop_polygon(img, [(d["box"][0], d["box"][1]), (d["box"][2], d["box"][1]), (d["box"][2], d["box"][3]), (d["box"][0], d["box"][3])]) for d in devices]
    for d, r in zip(devices, classify_crops(crops)):
        d["classification"] = {"model": CLASSIFIER_NAME, "label": r["label"], "class": r["class"], "prob": round(float(r["prob"]), 3)}
    return devices


# ------------------------------------------------------------------ steps 5 and 6: device layout and port layout
# The port agents are Srikanth's package port_agents/ (24 Oct 2026, the review fixes in), used as delivered: the switch
# agent for switches, the router agent for routers, the appliance agent for firewalls, load balancers and gateways. Each
# runs in its own process with only its own folder on the import path (src/port_agents_batch.py), one process per device
# group, WORKERS of them side by side.
PORT_AGENTS = ROOT / "engine" / "port_agents"
SWITCH_AGENT = PORT_AGENTS / "switch_agent"
CATALOGUES = ROOT / "engine" / "catalogues"               # our switch workbook and its json (the agents carry their own catalogues)
MAPPER_KIND = {"Switch": "switch", "Router": "router", "Firewall": "firewall", "Gateway": "gateway"}
AGENT_OF = {"switch": "switch", "router": "router", "firewall": "appliance", "load_balancer": "appliance", "gateway": "appliance"}
LAYOUT_TIMEOUT = 240                                    # seconds per crop (PORT_AGENT_CROP_TIMEOUT in the runner)
PORT_MODEL = ROOT / "models" / "ports" / "best_merged_ports.pt"
LAYOUT_PAD = 0.15                                       # the switch cut gets this share of its height above and below
VENDOR_SURE = 0.9                                       # a make without a fitted template is shown only above this identify_confidence
WORKERS = int(os.environ.get("RACKTRACK_WORKERS", "2"))          # agent processes side by side (each loads its own models)


def switch_cut(img, box):
    """The switch's front with a little margin, as a PIL image, and the cut's top-left corner in the photo."""
    x1, y1, x2, y2 = box; h = y2 - y1
    cx1, cy1 = max(0, int(x1 - 0.02 * (x2 - x1))), max(0, int(y1 - LAYOUT_PAD * h))
    cx2, cy2 = min(img.width, int(x2 + 0.02 * (x2 - x1))), min(img.height, int(y2 + LAYOUT_PAD * h))
    return img.crop((cx1, cy1, cx2, cy2)), (cx1, cy1)


def _agent_env():
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.pop("PORT_AGENT_NORMAL_EXIT", None)
    try:                                                  # python.org builds on macOS ship no root certificates (his run_agent.py does the same)
        import certifi
        env.setdefault("SSL_CERT_FILE", certifi.where()); env.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError:
        pass
    cache = pathlib.Path(os.environ.get("RACKTRACK_DATA") or ROOT / "data") / "port_agent_cache"; cache.mkdir(parents=True, exist_ok=True)
    env.setdefault("PORT_AGENT_CACHE_DIR", str(cache))    # his lasting cache for vendor pages and sitemaps: repeat lookups are fast
    env["OMP_NUM_THREADS"] = os.environ.get("OMP_NUM_THREADS", "2")
    env["PORT_AGENT_CROP_TIMEOUT"] = str(LAYOUT_TIMEOUT)
    # the switch agent's own switches (user, 2026-10-26): no web search during a tap, and 10 s instead of 45 s looking for the
    # template of a model it cannot find. Same answers on the test crops, an unknown-model crop 69 s -> 25 s.
    env.setdefault("SWITCH_MAPPER_WEB_SEARCH", "0")
    env.setdefault("SWITCH_MAPPER_AGENT_BUDGET", "10")
    return env


def _own_box(photo, box):
    """The device's own box in the cut: the device detector's box that overlaps the expected one most (IoU 0.3), else
    the expected box. Keeps a neighbour's ports out of an auto-mode read (user, 2026-10-29: D2 showed D3's ports)."""
    if not box:
        return None, "none"
    try:
        import device_model
        im = cv2.imread(str(photo))
        if im is None:
            return box, "expected"
        def iou(a, b):
            ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1])); inter = ix * iy
            u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
            return inter / u if u > 0 else 0.0
        eh = box[3] - box[1]                                        # a box much taller than the device is the cut itself or two devices: not it
        cands = [(iou(box, b[:4]), [float(v) for v in b[:4]]) for b in device_model.detect(im[:, :, ::-1]) if (b[3] - b[1]) <= 1.15 * eh]
        best = max(cands, default=(0, None))
        if best[0] >= 0.3:
            return best[1], "detector"
    except Exception:
        pass
    return box, "expected"


_OCR = [None]


def _make_outside(photo, own, vendor):
    """True when the make the agent read is printed outside the device's own box only (on the device above or below):
    the cut is read again with RapidOCR and every token naming the make is placed. False when it is inside, or not found."""
    try:
        if _OCR[0] is None:
            from rapidocr_onnxruntime import RapidOCR
            _OCR[0] = RapidOCR()
        im = cv2.imread(str(photo))
        res, _ = _OCR[0](cv2.resize(im, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC))
    except Exception:
        return False
    key = "".join(ch for ch in str(vendor).lower() if ch.isalnum())
    ys = [sum(q[1] for q in b) / 4 / 3 for b, txt, _s in (res or []) if key and key in "".join(ch for ch in str(txt).lower() if ch.isalnum())]
    return bool(ys) and all(not (own[1] <= y <= own[3]) for y in ys)


def _read_agent_result(d, origin, kind, box=None):
    """His result.json into the layout the rest of the app reads. mode: template (his mapped_template), auto
    (mapped_auto: ports boxed from the photo, numbered left to right), needs_back, none. The make is shown with a
    fitted template, or when his identify_confidence for the vendor is at least VENDOR_SURE with no doubt."""
    rf = d / "result.json"
    if not rf.exists():
        err = (d / "error.txt").read_text()[-400:] if (d / "error.txt").exists() else "no result (time limit)"
        return {"ok": False, "error": err, "kind": kind}
    r = json.loads(rf.read_text()); ox, oy = origin; st = r.get("status") or ""
    ports = []
    for p in r.get("ports") or []:
        poly = p.get("photo_polygon") or p.get("polygon") or []
        ports.append({"id": p.get("id"), "type": p.get("type"), "status": p.get("status"), "name": p.get("name"), "source": p.get("source"),
                      "polygon": poly, "photo_polygon": [[x + ox, y + oy] for x, y in poly]})
    pics = {}
    for key, names in (("cut", ("1_photo.jpg", "1_original.png")), ("vendor", ("2_fetched.png", "3_vendor.png")), ("vendor_ports", ("3_template.png", "3b_vendor_ports.png")),
                       ("overlay", ("4_overlay.png", "5_overlay.png")), ("trace", ("5b_overlay_trace.png",))):
        for n in names:
            if (d / n).exists():
                pics[key] = str(d / n); break
    conf = r.get("identify_confidence") or {}
    mode = {"mapped_template": "template", "mapped_auto": "auto", "needs_back": "needs_back"}.get(st, "none")
    fitted = mode == "template"
    if fitted and ports:                                              # a known make and model: the port types are the specification's (user, 2026-10-30);
        spec_ports = ((r.get("spec") or {}).get("ports") or {})       # the vendor layout names them, and where it does not the spec's order fills them in
        counts = {str(k).lower(): int(v) for k, v in spec_ports.items() if v}
        if counts and sum(counts.values()) == len(ports):
            have = {str(p.get("type") or "").lower() for p in ports}
            if len(have) <= 1 or have - set(counts):                  # one type for all, or types the spec does not know: assign in the spec's order, by number
                order = sorted((p for p in ports if isinstance(p.get("id"), int)), key=lambda p: p["id"]); k = 0
                for typ, n in counts.items():
                    for p in order[k:k + n]:
                        p["type"] = typ
                    k += n
                for p in ports:
                    p["type_from"] = "specification"
            else:
                for p in ports:
                    p["type_from"] = "vendor layout"
    detector = None
    own_pre = None
    if box and mode != "template" and r.get("vendor"):                # a make printed on the neighbour, not on this device, is dropped
        own_pre, how_pre = _own_box(pathlib.Path(str(d) + ".jpg"), box)
        if how_pre == "detector" and _make_outside(pathlib.Path(str(d) + ".jpg"), own_pre, r["vendor"]):
            detector = {"make_dropped": r["vendor"]}; r = dict(r, vendor=None)
    if mode in ("auto", "none") and not r.get("error"):   # no vendor layout: every port and its type from the 13-class port detector (auto mode, 29 Oct 2026)
        try:
            import port_detect
            found = port_detect.auto_layout(pathlib.Path(str(d) + ".jpg"), d)
        except Exception as ex:                             # the agent's own grid stays when the detector cannot run
            found = None; detector = {"error": f"{type(ex).__name__}: {ex}"}
        if found:
            own, how = _own_box(pathlib.Path(str(d) + ".jpg"), box)
            if own:                                                 # only the ports inside the device's own box (6 % of its height of slack)
                slack = 0.06 * (own[3] - own[1]) if how == "detector" else 0.12 * (own[3] - own[1])
                def inside(p):                                      # its centre in the box, and most of its height too
                    ys = [q[1] for q in p["polygon"]]; xs = [q[0] for q in p["polygon"]]; cy = sum(ys) / len(ys); cx = sum(xs) / len(xs)
                    share = max(0.0, min(max(ys), own[3]) - max(min(ys), own[1])) / max(1e-6, max(ys) - min(ys))
                    return own[1] - slack <= cy <= own[3] + slack and own[0] - slack <= cx <= own[2] + slack and share >= 0.5
                kept = [p for p in found["ports"] if inside(p)]
                try:                                                # the picture again: only this device's ports, cut to the device (a little margin)
                    import port_detect
                    im = cv2.imread(str(d) + ".jpg"); H_, W_ = im.shape[:2]
                    keep_d = [q for q in found["dets"] if inside({"polygon": [[q["box"][0], q["box"][1]], [q["box"][2], q["box"][1]], [q["box"][2], q["box"][3]], [q["box"][0], q["box"][3]]]})]
                    m_ = 0.12 * (own[3] - own[1]); y0_, y1_ = int(max(0, own[1] - m_)), int(min(H_, own[3] + m_))
                    pic = pathlib.Path(d) / "auto_ports.jpg"; cv2.imwrite(str(pic), port_detect.draw(im, keep_d)[y0_:y1_], [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                    found["picture"] = str(pic); found["dets_kept"] = keep_d
                except Exception:
                    pass
                if len(kept) < len(found["ports"]):
                    n = 0
                    for p in kept:
                        if isinstance(p["id"], int):
                            n += 1; p["id"] = n
                    found["ports"] = kept; found["dropped"] = len(found["dets"]) - len(kept); found["own_box"] = [round(v) for v in own]; found["own_box_from"] = how
            ports = [dict(p, photo_polygon=[[x + ox, y + oy] for x, y in p["polygon"]]) for p in found["ports"]]
            mode, st = "auto", "mapped_auto"
            if found.get("dets_kept") is not None:                  # the counts by type are this device's own
                bt = {}
                for q in found["dets_kept"]:
                    bt[q["type"].lower()] = bt.get(q["type"].lower(), 0) + 1
                found["by_type"] = bt
            detector = {"model": "13-class port detector", "found": len(found["dets"]), "by_type": found["by_type"], "dropped": found.get("dropped", 0), "own_box": found.get("own_box"), "own_box_from": found.get("own_box_from"),
                        "make_dropped": (detector or {}).get("make_dropped")}
            if found.get("picture"):
                pics["overlay"] = found["picture"]
    sure = fitted or (float(conf.get("vendor") or 0) >= VENDOR_SURE and not conf.get("vendor_doubt"))
    return {"ok": not r.get("error"), "kind": kind, "status": st, "vendor": r.get("vendor") if (sure and r.get("vendor")) else None,
            "model": r.get("model") if fitted else None, "vendor_read": r.get("vendor"), "model_read": r.get("model"), "mode": mode,
            "confidence": r.get("confidence"), "identify_confidence": conf or None, "needs_back": bool(r.get("needs_back")) or mode == "needs_back",
            "suggested_device": r.get("suggested_device"), "numbering": ("column" if detector and not detector.get("error") else None) or r.get("numbering_scheme") or r.get("numbering") or ("left to right" if mode == "auto" else None),
            "error": r.get("error"), "ports": ports, "n_ports": len(ports), "by_type": {t: sum(p["type"] == t for p in ports) for t in sorted({p["type"] for p in ports if p.get("type")})},
            "connected": sum(p.get("status") == "connected" for p in ports), "pictures": pics,
            "spec": r.get("spec"), "identified_by": r.get("identified_by"),      # the 28 Oct package: the model's specification and how the make and model were found
            "port_detector": detector}


def agent_layouts(jobs, work, timeout=None, progress=None):
    """Steps 5 and 6 for a list of cuts: jobs = [{"key", "image": PIL, "origin": (x, y), "kind", ["group", "vendor", "model"]}].
    Every cut goes to the agent of its kind (AGENT_OF), one process per device group with the agent's models loaded once,
    WORKERS processes side by side; returns {key: layout} (see _read_agent_result)."""
    import subprocess
    import threading
    import time as _time
    work = pathlib.Path(work).resolve(); work.mkdir(parents=True, exist_ok=True)
    spec = []
    for j in jobs:
        ph = work / f"{j['key']}.jpg"; j["image"].save(ph, quality=95)
        kind = j.get("kind") or "switch"
        spec.append({"photo": str(ph), "out": str(work / j["key"]), "group": j.get("group") or j["key"], "kind": kind, "agent": AGENT_OF.get(kind, "appliance"),
                     "vendor": j.get("vendor"), "model": j.get("model")})
        j["_box"] = j.get("box")
    if not spec:
        return {}
    groups = {}
    for sp in spec:
        groups.setdefault((sp["agent"], sp["group"]), []).append(sp)
    # one process per (agent, bucket); the switch agent, which has most crops, gets up to WORKERS buckets, the others one
    # each; at most WORKERS processes run at a time (each loads its own models: the Mac must stay cool)
    per_agent = {a: sum(1 for (a_, _g) in groups if a_ == a) for a in {a for a, _ in groups}}
    buckets = {a: [[] for _ in range(max(1, min(WORKERS, n if len(per_agent) > 1 else WORKERS)))] for a, n in per_agent.items()}
    for (agent, _g), js in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(buckets[agent]) > per_agent[agent]:         # fewer devices than workers (one device on demand): its crops are
            for k, j in enumerate(js):                      # dealt round-robin, the rack crop first, so the workers share it
                buckets[agent][k % len(buckets[agent])].append(j)
        else:
            min(buckets[agent], key=len).extend(js)
    queue = [(a, k, b) for a, bl in buckets.items() for k, b in enumerate(bl) if b]
    queue.sort(key=lambda t: -len(t[2]))                      # the longest bucket starts first
    done, total, lock = [0], len(spec), threading.Lock()
    env = _agent_env(); running = []; threads = []
    def start(agent, k, b):
        jf = work / f"jobs_{agent}_{k}.json"; jf.write_text(json.dumps(b))
        e = dict(env, PYTHONPATH=str(PORT_AGENTS / f"{agent}_agent"))
        log = open(work / f"log_{agent}_{k}.txt", "w")
        p = subprocess.Popen([sys.executable, str(ROOT / "engine" / "port_agents_batch.py"), agent, str(jf)], cwd=PORT_AGENTS / f"{agent}_agent",
                             env=e, stdout=subprocess.PIPE, stderr=log, text=True)
        p._rt_log = log
        t = threading.Thread(target=pump, args=(p,), daemon=True); t.start(); threads.append(t)
        return p
    def pump(p):
        for line in p.stdout:
            if line.startswith(("done ", "skip ")):
                with lock:
                    done[0] += 1; n = done[0]
                if progress:
                    progress(f"ports: {n} of {total} crops read" + (f" ({min(WORKERS, len(queue))} agents in parallel)" if min(WORKERS, len(queue)) > 1 else ""))
    deadline = _time.monotonic() + (timeout or LAYOUT_TIMEOUT * (total // max(1, WORKERS) + 1))
    pending = list(queue)
    while pending or running:
        while pending and len(running) < WORKERS:
            running.append(start(*pending.pop(0)))
        for p in list(running):
            try:
                p.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue
            running.remove(p); p._rt_log.close()
        if _time.monotonic() > deadline:
            for p in running:
                p.kill(); p._rt_log.close()
            running = []; pending = []
    for t in threads:
        t.join(timeout=5)
    return {j["key"]: _read_agent_result(work / j["key"], j["origin"], j.get("kind") or "switch", j.get("box")) for j in jobs}


def switch_layouts(jobs, work, timeout=None, progress=None):
    """Switch cuts through the switch agent (the old name, kept for the callers)."""
    for j in jobs:
        j.setdefault("kind", "switch")
    return agent_layouts(jobs, work, timeout, progress)


def device_layouts(jobs, work, progress=None):
    """Router, firewall, gateway and load balancer cuts through the router and appliance agents."""
    return agent_layouts(jobs, work, None, progress)


def photo_switch_layouts(img, devices, work):
    """Steps 5 and 6 on a photo: every device classified as a Switch, Router, Firewall or Gateway is cut out and sent
    to its mapper (the switch mapper, or the team's router / appliance mappers). The ports shown are only those of a
    fitted vendor layout; ports read from the photo alone (auto mode) are not used."""
    sw, other = [], []
    for d in devices:
        kind = MAPPER_KIND.get((d.get("classification") or {}).get("label"))
        if not kind:
            continue
        cut, origin = switch_cut(img, d["box"])
        (sw if kind == "switch" else other).append({"key": f"D{d['index']}", "image": cut, "origin": origin, "device": d, "kind": kind})
    res = agent_layouts(sw + other, work)
    for j in sw + other:
        lay = res.get(j["key"]) or {"ok": False, "error": "no result", "kind": j["kind"]}
        if lay.get("mode") not in ("template", "auto"):   # nothing mapped: no ports; the agent's auto mode (ports boxed from the photo) counts
            lay = dict(lay, ports=[], n_ports=0, by_type={}, connected=0, numbering=None,
                       pictures={k: v for k, v in (lay.get("pictures") or {}).items() if k == "cut"})
        j["device"]["layout"] = dict(lay, kind=j["kind"], fitted=lay.get("mode") == "template")
        # when no vendor layout fitted (the agent failed), the RT-EOS port model alone finds the ports (port_details.py);
        # the mappers' own auto-mode ports are never used (user, 2026-10-23)
        add_port_details(j["device"]["layout"], j["image"], work, j["key"])
    return devices


def add_port_details(layout, cut_pil, work, key):
    """Step 6 in detail on one switch cut (src/port_details.py): every port, empty or connected, its spec and cable."""
    try:
        import port_details as PD
        if not PD.available():
            return layout
        cut = cv2.cvtColor(np.asarray(cut_pil.convert("RGB")), cv2.COLOR_RGB2BGR)
        det = PD.port_details(cut, layout)
        f = pathlib.Path(work) / f"{key}_portmap.jpg"; cv2.imwrite(str(f), PD.draw(cut, det))
        layout["port_details"] = dict(det, picture=str(f))
        layout.setdefault("pictures", {})["portmap"] = str(f)
    except Exception as ex:
        layout["port_details"] = {"error": f"{type(ex).__name__}: {ex}"}
    return layout


def draw_ports(img, devices):
    """Step 6 picture: every mapped switch's ports outlined on the whole photo, cabled ports filled."""
    base = img.convert("RGBA"); lay = Image.new("RGBA", base.size, (0, 0, 0, 0)); d = ImageDraw.Draw(lay)
    lw = max(1, int(min(base.size) / 700))
    for dev in devices:
        L = dev.get("layout") or {}
        if not L.get("ok"):
            continue
        col = PALETTE[(dev["index"] - 1) % len(PALETTE)]
        for p in L["ports"]:
            if p.get("photo_polygon"):
                pts = [tuple(q) for q in p["photo_polygon"]]
                d.polygon(pts, fill=col + (110 if p.get("status") == "connected" else 25,), outline=col + (240,), width=lw)
    return Image.alpha_composite(base, lay).convert("RGB")


def public_layout(dev):
    """Steps 5 and 6 of one device, without the work-folder paths."""
    L = dev.get("layout") or {}
    keep = ("ok", "kind", "vendor", "model", "mode", "fitted", "confidence", "numbering", "n_ports", "by_type", "connected", "error", "reads", "needs_back", "verdict")
    return dict({"index": dev["index"], "top": dev["top"], "bottom": dev["bottom"], "height": dev["height"]}, **{k: L.get(k) for k in keep},
                ports=[{k: p.get(k) for k in ("id", "type", "status", "name")} for p in L.get("ports") or []],
                port_details={k: v for k, v in (L.get("port_details") or {}).items() if k != "picture"})


def draw_classes(img, rows, devices):
    """Step 4 picture: the device bands of step 3, each labelled with its kind."""
    return draw_devices(img, rows, [dict(d, label=f"D{d['index']} · {d['classification']['label']} {round(100 * d['classification']['prob'])} %")
                                    if d.get("classification") else d for d in devices])
_detector_state = None


# ---------------------------------------------------------------- step 1
def units_from_photo(bgr):
    """Rows top to bottom in the web app's format: unit label, box, whether the model drew it or the logic added it,
    and the model's confidence. Also the method's info (pitch, rack edges)."""
    rows, info = unit_persp.method_rteos_logic(bgr)
    raw = info.get("model_units", [])
    out = []
    for r in rows:
        y1, y2 = r["box"][1], r["box"][3]
        best, best_ov = None, 0.0
        for u in raw:
            ov = max(0.0, min(y2, u["box"][3]) - max(y1, u["box"][1])) / max(1.0, y2 - y1)
            if ov > best_ov:
                best, best_ov = u, ov
        det = best is not None and best_ov >= 0.5
        out.append({"unit": r["label"], "box": tuple(int(v) for v in r["box"]),
                    "source": "detected" if det else "inferred", "det_conf": float(best["confidence"]) if det else None})
    return out, info


def draw_units(bgr, units, info):
    """Step 1 picture: the finished ladder (white lines, U-number strip), as on the portal."""
    rows = [{"box": list(u["box"]), "label": ""} for u in units]
    vis = unit_logic.draw(bgr, rows, {"x_left": info.get("x_left", 0), "x_right": info.get("x_right", bgr.shape[1] - 1)})
    return Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB))


# ---------------------------------------------------------------- step 2
def occupancy_rows(img, units, approach, devices=None, thresh=0.5):
    """Each unit occupied / unoccupied / uncertain with the reason. devices: the RT-EOS cross-check detections
    (computed here when None and the finder is available)."""
    if not units:
        return []
    if devices is None and occ.device_finder_available():
        devices = occ.detect_devices(img)
    rows = occ.classify_units(img, units, approach, thresh, devices)
    return [{k: v for k, v in r.items() if k != "crop"} for r in rows]


def draw_occupancy(img, rows):
    return occ.annotate(img, rows)


# ---------------------------------------------------------------- step 3
def detector_available():
    """True when the device detector's code and weights load (checked once per process)."""
    global _detector_state
    if _detector_state is None:
        import contextlib
        try:
            with contextlib.redirect_stdout(sys.stderr):      # the detector's code prints its build notes
                DM.detector()
            _detector_state = True
        except Exception:
            _detector_state = False
    return _detector_state


def device_rows(bgr, rows):
    """Devices on the occupied units (uncertain units count as occupied here). Returns the devices, the seams and
    the method used."""
    units = [{"unit": r["unit"], "box": list(r["box"])} for r in rows]
    taken = [r["status"] != "unoccupied" for r in rows]
    if not any(taken):
        return [], [], STEP3_MODEL if detector_available() else STEP3_LOGIC
    if detector_available():
        devices, new_dev, probs, source = DM.group(bgr, units, taken)
        how = STEP3_MODEL
    else:
        devices, new_dev, probs = DJ.group(bgr, units, taken)
        source, how = {k: "logic" for k in probs}, STEP3_LOGIC
    first = {d[0] for d in devices}
    out = []
    for i, d in enumerate(devices, 1):
        top, bot = rows[d[0]], rows[d[-1]]
        out.append({"index": i, "units": [rows[k]["unit"] for k in d], "top": top["unit"], "bottom": bot["unit"], "height": len(d),
                    "box": [int(min(rows[k]["box"][0] for k in d)), int(top["box"][1]), int(max(rows[k]["box"][2] for k in d)), int(bot["box"][3])],
                    "uncertain": any(rows[k]["status"] == "uncertain" for k in d),
                    "decided_by": source.get(d[0]) if d[0] in probs else ("alone in its run" if len(d) == 1 and (d[-1] + 1 >= len(rows) or not taken[d[-1] + 1]) else "first in its run")})
    seams = [{"above": rows[k - 1]["unit"], "below": rows[k]["unit"], "p_new_device": round(float(p), 3),
              "new_device": bool(new_dev.get(k, False)), "decided_by": source.get(k, "logic")} for k, p in sorted(probs.items())]
    return out, seams, how


PALETTE = [(29, 95, 207), (26, 127, 69), (178, 94, 10), (140, 60, 180), (0, 140, 150), (190, 40, 90), (90, 110, 30), (60, 60, 60)]


def draw_devices(img, rows, devices):
    """Step 3 picture: one translucent band per device with its number and height, free units left clear."""
    base = img.convert("RGBA")
    W, H = base.size
    lay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    hs = [r["box"][3] - r["box"][1] for r in rows] or [20]
    px = int(max(11, min(float(np.median(hs)) * 0.6, W / 40)))
    font = occ._font(px)
    lw = max(2, int(min(W, H) / 400))
    for dev in devices:
        col = PALETTE[(dev["index"] - 1) % len(PALETTE)]
        x1, y1, x2, y2 = dev["box"]
        d.rectangle([x1, y1, x2, y2], fill=col + (60,), outline=col + (230,), width=lw)
        t = dev.get("label") or f"D{dev['index']} · {dev['height']}U"
        tw, th = d.textbbox((0, 0), t, font=font)[2:]
        d.rounded_rectangle([x2 - tw - 12 - lw, y1 + lw, x2 - lw, y1 + th + 8], radius=4, fill=col + (235,))
        d.text((x2 - tw - 6 - lw, y1 + 3), t, fill="white", font=font)
    return Image.alpha_composite(base, lay).convert("RGB")


# ---------------------------------------------------------------- the whole run
def public_row(r):
    return {"unit": r["unit"], "box": [int(v) for v in r["box"]], "source": r["source"],
            "det_conf": None if r.get("det_conf") is None else round(float(r["det_conf"]), 3),
            "p_occupied": round(float(r["p_occupied"]), 3), "status": r["status"], "confidence": round(float(r["confidence"]), 3),
            "decided_by": r["decided_by"], "attention": r.get("attention"), "reason": r.get("reason") or "", "device": r.get("device"),
            "candidate_box": r.get("candidate_box")}


def run(img, approach):
    """All six steps on one PIL photo with one occupancy approach. Returns the data and the pictures."""
    bgr = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
    t0 = time.perf_counter()
    units, info = units_from_photo(bgr)
    t1 = time.perf_counter()
    rows = occupancy_rows(img, units, approach)
    t2 = time.perf_counter()
    devices, seams, how = device_rows(bgr, rows)
    t3 = time.perf_counter()
    classify_photo_devices(img, devices)
    t4 = time.perf_counter()
    import tempfile
    photo_switch_layouts(img, devices, pathlib.Path(tempfile.mkdtemp(prefix="racktrack_layout_")))
    t5 = time.perf_counter()
    return {
        "units": {"method": STEP1, "n": len(units), "pitch_px": None if info.get("pitch") is None else round(float(info["pitch"]), 1),
                  "rows": [{"unit": u["unit"], "box": [int(v) for v in u["box"]], "source": u["source"], "det_conf": u["det_conf"]} for u in units],
                  "time_s": round(t1 - t0, 2), "image": draw_units(bgr, units, info)},
        "occupancy": {"method": getattr(approach, "name", str(approach)), "rows": [public_row(r) for r in rows],
                      "summary": occ.summarize(rows) if rows else None, "time_s": round(t2 - t1, 2), "image": draw_occupancy(img, rows)},
        "devices": {"method": how, "devices": devices, "seams": seams, "n": len(devices), "time_s": round(t3 - t2, 2),
                    "image": draw_devices(img, rows, devices)},
        "classification": {"method": CLASSIFIER_NAME, "devices": [{"index": d["index"], "top": d["top"], "bottom": d["bottom"], "height": d["height"], **d.get("classification", {})} for d in devices],
                           "time_s": round(t4 - t3, 2), "image": draw_classes(img, rows, devices)},
        "layout": {"method": "the port agents (port_agents/: switch, router, appliance)", "switches": [public_layout(d) for d in devices if d.get("layout")],
                   "time_s": round(t5 - t4, 2), "image": draw_ports(img, devices)},
        "total_time_s": round(t5 - t0, 2),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("photo")
    ap.add_argument("--out", default=None, help="folder for the three pictures (default: next to the photo)")
    ap.add_argument("--approach", default="ensemble")
    args = ap.parse_args()
    approach = {a.key: a for a in occ.build_approaches("v2")}[args.approach]
    img = Image.open(args.photo)
    from PIL import ImageOps
    img = ImageOps.exif_transpose(img).convert("RGB")
    res = run(img, approach)
    out = pathlib.Path(args.out) if args.out else pathlib.Path(args.photo).parent
    out.mkdir(parents=True, exist_ok=True)
    stem = pathlib.Path(args.photo).stem
    for step in ("units", "occupancy", "devices", "classification", "layout"):
        res[step]["image"].save(out / f"{stem}_{step}.jpg", quality=88)
        res[step]["image"] = str(out / f"{stem}_{step}.jpg")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
