"""Persistent worker: keeps Python + all YOLO models warm in memory, so each
request avoids the ~10s cold start that `pipeline.runner` pays when spawned
per-request.

Node dispatches JSON requests line-by-line via stdin; worker responds on stdout.
All progress logs + ultralytics spam go to stderr so the stdout protocol stays
parseable. Protocol: each line is one complete JSON object.

Handshake:
    Worker emits {"ready": true} on stdout when models are loaded.

Request shapes (stdin):
    {"id": "<uuid>", "command": "quality_check", "image_path": "..."}
    {"id": "<uuid>", "command": "analyze",  "image_path": "...", "config_path": "...", "output_dir": "..."}
    {"id": "<uuid>", "command": "select",   "image_path": "...", "config_path": "...", "output_dir": "...", "device_index": 0, "port": 1}
    {"id": "<uuid>", "command": "zone_read", "image_path": "...", "config_path": "...", "from": 1, "to": 12}
    {"id": "<uuid>", "command": "cable_flags", "image_path": "..."}

Response shape (stdout):
    {"id": "<uuid>", "ok": true,  ...}
    {"id": "<uuid>", "ok": false, "error": "..."}
"""

import io
import json
import os
import sys
import traceback
from contextlib import redirect_stdout

# Silence ultralytics' per-inference progress lines before anything imports it,
# otherwise they escape our redirect_stdout and corrupt the JSON protocol.
os.environ.setdefault("YOLO_VERBOSE", "False")


def log(msg):
    print(f"[worker] {msg}", file=sys.stderr, flush=True)


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


log("importing pipeline modules (slow, one-time)")
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from pipeline import runner  # noqa: E402
from pipeline.cable import load_cable_model, load_port_identify_model  # noqa: E402
from pipeline.detection import load_model, normalize_class_name  # noqa: E402
from pipeline.occlusion_model import classify_occlusion  # noqa: E402
from pipeline.quality_check import (  # noqa: E402
    check_letterbox,
    check_occlusion,
    check_side_view,
    check_tilt,
)


# Ultralytics monkey-patches cv2.imread to use np.fromfile (multilang filename
# support). On Python 3.13 + Windows, np.fromfile raises
# "'NoneType' object has no attribute 'flush'" when called while sys.stdout is
# redirected (which we do via redirect_stdout in handle_pipeline). Replace it
# with an open()-based equivalent that avoids np.fromfile entirely.
def _safe_imread(filename, flags=cv2.IMREAD_COLOR):
    try:
        with open(filename, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if not data:
        return None
    file_bytes = np.frombuffer(data, np.uint8)
    # Hand back exactly what cv2.imread would. The shape matters: a caller that
    # asks for IMREAD_GRAYSCALE is promised a 2-D (h, w) array, and easyocr
    # takes that promise literally — `maximum_y, maximum_x = img.shape` in its
    # get_image_list. This used to expand a grayscale read to (h, w, 1), so
    # every label read inside the warm worker died on "too many values to
    # unpack (expected 2)" and the server quietly fell back to a fresh process
    # per photo: a 15-30 second wait instead of one or two. IMREAD_COLOR — the
    # default, and what the ultralytics callers this patch exists for use —
    # already decodes to three channels, so nothing needed the extra axis.
    return cv2.imdecode(file_bytes, flags)


cv2.imread = _safe_imread
log("cv2.imread patched to open()+imdecode (bypasses ultralytics np.fromfile)")


QUALITY_ERROR = (
    "Please upload a clearer photo of the rack — keep the camera "
    "steady and make sure the full rack fits in the frame."
)


def preload_models(config_path):
    with open(config_path) as f:
        config = json.load(f)
    models = config.get("models", {})
    mode = config.get("detection", {}).get("device_detect_mode", "dual")
    # Device-detection mode picks which YOLO heads we warm up at boot:
    #   dual: devices_general (best 32.pt) + devices_server (best 33.pt)
    #   seg : devices_seg     (seg_devices.pt)
    # Port + cable models are mode-independent.
    if mode == "dual":
        device_keys = ("devices_general", "devices_server")
    else:
        device_keys = ("devices_seg",)
    for key in ("units",) + device_keys + ("ports_typed", "ports_status", "sfp_type", "pdu_ports"):
        p = models.get(key)
        if not p:
            continue
        if not os.path.exists(p):
            log(f"skip {key}: file not found at {p}")
            continue
        log(f"preloading {key} ({p})")
        load_model(p)
    if models.get("cable_classifier"):
        log(f"preloading cable_classifier ({models['cable_classifier']})")
        load_cable_model(models["cable_classifier"])
    if models.get("port_identify"):
        log(f"preloading port_identify ({models['port_identify']})")
        load_port_identify_model(models["port_identify"])
    # The model team's dust-cap classifier (pipeline/cap_model.py); without
    # its weights the slot stays open and the pixel rules answer alone.
    cap = models.get("cap_classifier")
    if cap and os.path.exists(cap):
        from pipeline.cap_model import load_cap_model

        log(f"preloading cap_classifier ({cap})")
        load_cap_model(cap)
    elif cap:
        log(f"skip cap_classifier: file not found at {cap}")


def handle_quality_check(req):
    img = cv2.imread(req["image_path"])
    if img is None:
        return {"ok": False, "error": QUALITY_ERROR}
    lb = check_letterbox(img)
    if not lb["ok"]:
        return lb
    tilt = check_tilt(img)
    if not tilt["ok"]:
        return tilt
    side = check_side_view(img)

    # Occlusion check — trained MobileNetV2 classifier (Models/photo_quality_check.pth)
    # judging clear vs occluded directly, instead of inferring cable clutter from
    # edge-orientation and saturation proxies. May return a hard fail
    # (ok=False, kind='occlusion') for severely cabled racks, which the client
    # surfaces as a choice: multi-angle capture, or proceed anyway.
    #
    # Falls back to the heuristic when the model can't load (torch absent, weights
    # not deployed). A quality gate must not block uploads on a box that shipped
    # without the checkpoint.
    occ = classify_occlusion(img)
    if occ is None:
        occ = check_occlusion(img)
        occ.setdefault("metrics", {})["occlusion_detector"] = "heuristic"
    if not occ.get("ok"):
        return occ

    merged = {}
    merged.update(tilt.get("metrics", {}))
    merged.update(side.get("metrics", {}))
    merged.update(occ.get("metrics", {}))
    result = {"ok": True, "metrics": merged}
    # side_view + occlusion can both emit soft warnings; prefer occlusion
    # since it points to a remediable action (multi-angle), and side_view
    # is more advisory.
    if occ.get("warning"):
        result["warning"] = occ["warning"]
        result["warning_msg"] = occ["warning_msg"]
    elif side.get("warning"):
        result["warning"] = side["warning"]
        result["warning_msg"] = side["error"]
    return result


def handle_detect_only(req):
    """Stateless, lightweight YOLO bbox detection — used by the live
    in-viewfinder overlay (POSTed at ~1 Hz). Skips the full analyze
    pipeline (no rack folder, no port detection, no OCR, no image
    renders). Returns just devices with class_name + confidence + bbox.

    Coordinates are in the input image's pixel space. The caller is
    responsible for any back-scaling to its source resolution.
    """
    import json as _json

    img_path = req.get("image_path")
    config_path = req.get("config_path")
    if not img_path or not os.path.exists(img_path):
        return {"ok": False, "error": "image_path missing"}
    if not config_path or not os.path.exists(config_path):
        return {"ok": False, "error": "config_path missing"}

    img = cv2.imread(img_path)
    if img is None:
        return {"ok": False, "error": "could not read image"}
    h, w = img.shape[:2]

    with open(config_path) as f:
        config = _json.load(f)
    models = config.get("models", {})
    mode = config.get("detection", {}).get("device_detect_mode", "dual")
    # Live in-viewfinder overlay runs a SINGLE forward pass at imgsz=320 for
    # throughput. In dual-detect mode we still only run the general head
    # here — the server head is reserved for the full analyze path. Skipping
    # the second pass keeps the live overlay snappy without changing the
    # production result.
    if mode == "dual":
        det_path = models.get("devices_general")
        if not det_path:
            return {"ok": False, "error": "no devices_general model configured"}
    else:
        det_path = models.get("devices_seg")
        if not det_path:
            return {"ok": False, "error": "no devices_seg model configured"}

    model = load_model(det_path)

    # Inline the YOLO call so we can pass imgsz=320 — full /api/analyze
    # uses YOLO's default 640, but on CPU that's ~1s per frame; halving
    # the inference resolution roughly triples throughput while still
    # finding rack devices (40+ px tall in the source frame). Confidence
    # bumped to 0.4 to keep the live overlay from strobing on noise.
    names = getattr(model, "names", {}) or {}
    results = model(img, conf=0.4, imgsz=320, verbose=False)

    devices = []
    if results and results[0].boxes is not None and len(results[0].boxes) > 0:
        xyxy = results[0].boxes.xyxy.cpu().numpy()
        cls_ids = results[0].boxes.cls.cpu().numpy().astype(int)
        scores = results[0].boxes.conf.cpu().numpy()
        for box, cid, score in zip(xyxy, cls_ids, scores):
            x1, y1, x2, y2 = [int(v) for v in box]
            if (x2 - x1) < 10 or (y2 - y1) < 10:
                continue
            cn = normalize_class_name(str(names.get(int(cid), cid)))
            if cn in ("Empty", "Closed Unit", "Unidentified"):
                continue
            devices.append(
                {
                    "class_name": cn,
                    "confidence": float(score),
                    "bbox": [x1, y1, x2 - x1, y2 - y1],
                    "box": [x1, y1, x2, y2],
                }
            )

    return {"ok": True, "devices": devices, "image_size": {"w": w, "h": h}}


def handle_extract_best_frame(req):
    from pipeline.frame_selector import extract_best_frame

    frame = extract_best_frame(req["video_path"])
    if frame is None:
        return {
            "ok": False,
            "error": "Could not read a usable frame from the video. Please record a clearer video of the rack.",
        }
    output_path = req["output_path"]
    if not cv2.imwrite(output_path, frame):
        return {"ok": False, "error": f"Failed to write extracted frame to {output_path}"}
    return {"ok": True, "image_path": output_path}


def handle_split_video_racks(req):
    """Split a multi-rack pan video into one best-frame per detected rack.
    Returns a list of {position, label, best_frame_path, ...}. The Node
    server then runs the existing single-rack analyze() on each path.
    """
    from pipeline.multi_rack_split import split_video_into_racks

    video_path = req["video_path"]
    output_dir = req.get("output_dir")
    if not output_dir:
        return {"ok": False, "error": "output_dir missing"}
    # Forward config_path like every other handler. The server sends it and the
    # splitter accepts it, but this handler dropped it on the floor — so the
    # splitter always fell back to its own config.json lookup and a deployment
    # pointing RACKTRACK_CONFIG elsewhere silently got a different model and
    # threshold here than on the single-rack path. The fix on the Node side was
    # inert until this line existed.
    config_path = req.get("config_path")
    try:
        racks = split_video_into_racks(video_path, output_dir=output_dir, config_path=config_path)
    except Exception as e:
        return {"ok": False, "error": f"multi-rack split failed: {e}"}
    if not racks:
        return {
            "ok": False,
            "error": "No racks detected in the video. Please re-record a clear pan across the racks.",
        }
    return {"ok": True, "racks": racks, "count": len(racks)}


def handle_closeup_ocr(req):
    """Identify one device from a close-up photo of its label.

    Runs in the warm worker rather than a spawned process on purpose: the
    OCR weights take ~3s to load, and a user who is standing in front of the
    rack waiting for this answer will just type the model in instead if we
    make them wait for a cold start on every photo. The reader is cached
    inside pipeline.ocr_closeup, so only the first close-up per worker pays
    it. The import is deferred for the same reason in reverse — a worker
    that never sees a close-up should not carry the OCR models in memory.
    """
    from pipeline.ocr_closeup import run as closeup_run

    image_path = req.get("image_path")
    if not image_path or not os.path.exists(image_path):
        return {"ok": False, "error": "image_path missing"}
    # The OCR backends log to stderr, but a Paddle build that ever printed to
    # stdout would corrupt the line-per-JSON protocol. Cheap insurance.
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = closeup_run(image_path, preset=req.get("preset") or "closeup")
    stray = buf.getvalue().strip()
    if stray:
        log(f"closeup_ocr stdout (suppressed): {stray[-300:]}")
    return result


def handle_ocr_labels(req):
    """Read every label in one rack photograph.

    In the warm worker for the same reason as the close-up: building the OCR
    reader costs several seconds, and the old path spawned a fresh Python for
    each call and paid it every time. pipeline.ocr_labels caches the reader,
    so only the first rack photo a worker sees pays for it. The import stays
    deferred — a worker that never reads a label should not be holding the
    models in memory.
    """
    from pipeline.ocr_labels import extract_labels

    image_path = req.get("image_path")
    if not image_path or not os.path.exists(image_path):
        return {"ok": False, "error": "image_path missing"}
    # The OCR backends log to stderr; a build that ever printed to stdout would
    # corrupt the line-per-JSON protocol.
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = extract_labels(image_path, min_conf=float(req.get("min_conf", 0.25)))
    stray = buf.getvalue().strip()
    if stray:
        log(f"ocr_labels stdout (suppressed): {stray[-300:]}")
    return {"ok": True, **result}


def handle_cable_flags(req):
    """Read the printed flags on the cables in one photo (pipeline/cable_flags.py).

    In the warm worker: the flag reader keeps its own RapidOCR reader, built
    on the first call, and a rack photo is read in tiles, so a cold process
    per photo would pay the model load on top of ten seconds of reading.
    """
    from pipeline.cable_flags import read_flags

    image_path = req.get("image_path")
    if not image_path or not os.path.exists(image_path):
        return {"ok": False, "error": "image_path missing"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        vocab = req.get("vocab")
        result = read_flags(image_path, vocab=[str(v) for v in vocab] if isinstance(vocab, list) else None)
    stray = buf.getvalue().strip()
    if stray:
        log(f"cable_flags stdout (suppressed): {stray[-300:]}")
    return {"ok": True, **result}


def handle_zone_read(req):
    """Read one zone close-up: its labels, its ports, which label is on which port.

    In the warm worker so the port models and the label reader are already in
    memory when a technician takes close-up after close-up (pipeline/zone_read.py).
    """
    from pipeline.zone_read import read_zone

    image_path = req.get("image_path")
    if not image_path or not os.path.exists(image_path):
        return {"ok": False, "error": "image_path missing"}
    try:
        from_no = int(req["from"])
        to_no = int(req["to"])
    except (KeyError, TypeError, ValueError):
        return {"ok": False, "error": "from and to must be whole numbers"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = read_zone(
            image_path, from_no, to_no, req.get("config_path") or os.environ.get("RACKTRACK_CONFIG", "config.json")
        )
    stray = buf.getvalue().strip()
    if stray:
        log(f"zone_read stdout (suppressed): {stray[-300:]}")
    return result


def handle_side_labels(req):
    """Read the rack's own label and the chips on its rails, and file them.

    In the warm worker, with the reader the worker already holds, for the reason
    written on extract_side_labels: a second Python with a second copy of the
    models is what ran the demo server out of memory. Writes side_labels.json
    into the rack's folder, which is what the physical layer reads.
    """
    import datetime as _dt
    import json as _json
    from pathlib import Path

    from pipeline.ocr_labels import _reader
    from pipeline.side_labels import _resolve_image, extract_side_labels

    rack_dir = Path(str(req.get("output_dir") or ""))
    if not rack_dir.is_dir():
        return {"ok": False, "error": "output_dir missing"}
    img_path = _resolve_image(rack_dir)
    if not img_path:
        return {"ok": False, "error": "no photo found"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = extract_side_labels(str(img_path), reader=_reader())
    payload = {
        "ok": True,
        "rack_id": req.get("rack_id") or rack_dir.name,
        "generated_at": _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        **result,
    }
    (rack_dir / "side_labels.json").write_text(_json.dumps(payload, indent=2))
    return {"ok": True, "labels": len(result.get("labels") or [])}


def handle_ocr_devices(req):
    """Read the make and model off every device in one rack photograph.

    In the warm worker for the same reason as the other two OCR passes: the
    switch-ocr reader loads its models on construction, and this used to be a
    fresh Python process per rack — the whole load paid before the first label
    was read. pipeline.ocr_devices caches the reader now, so only the first
    rack a worker sees pays for it.
    """
    from pipeline.ocr_devices import run as ocr_devices_run

    rack_id = req.get("rack_id")
    rack_dir = req.get("rack_dir")
    if not rack_id or not rack_dir:
        return {"ok": False, "error": "rack_id and rack_dir are required"}
    buf = io.StringIO()
    with redirect_stdout(buf):
        result = ocr_devices_run(rack_id, rack_dir)
    stray = buf.getvalue().strip()
    if stray:
        log(f"ocr_devices stdout (suppressed): {stray[-300:]}")
    return {"ok": True, **(result or {})}


def handle_relabel_port_count(req):
    """Re-detect ports for one device with a user-supplied target count.
    Updates that device's entry in device_unit_map.json and returns it.
    """
    import json as _json

    rack_dir = req["rack_dir"]
    device_index = int(req["device_index"])
    target_count = int(req["target_count"])
    config_path = req["config_path"]

    map_path = os.path.join(rack_dir, "device_unit_map.json")
    if not os.path.exists(map_path):
        return {"ok": False, "error": "device_unit_map.json missing"}
    with open(map_path) as f:
        data = _json.load(f)
    devices = data.get("devices", [])
    if not (1 <= device_index <= len(devices)):
        return {"ok": False, "error": "device_index out of range"}
    device = devices[device_index - 1]

    # The scan's photo sits beside its engine folder (pipeline/scan_paths.py).
    from pipeline.scan_paths import find_photo

    _photo = find_photo(rack_dir)
    img_path = str(_photo) if _photo else None
    if img_path is None:
        return {"ok": False, "error": "the scan's photo was not found"}

    img = cv2.imread(img_path)
    if img is None:
        return {"ok": False, "error": "could not read original image"}

    bx1, by1, bx2, by2 = [int(v) for v in device["box"]]
    crop = img[by1:by2, bx1:bx2]
    if crop.size == 0:
        return {"ok": False, "error": "device crop is empty"}

    with open(config_path) as f:
        config = _json.load(f)
    # Typed model (ports_13) carries port category. Status model (port_count)
    # carries Connected_port / Empty_port and is IoU-bound onto the typed
    # ports. Patch panels go through the same pair: ports_13 was trained on
    # them, so the status model no longer has to stand in as their detector.
    port_model = load_model(config["models"]["ports_typed"])
    status_model_rel = config["models"].get("ports_status")
    status_model = load_model(status_model_rel) if status_model_rel else port_model
    ports_conf = config.get("detection", {}).get("ports_conf", 0.23)
    sfp_model_rel = config["models"].get("sfp_type")
    sfp_model = load_model(sfp_model_rel) if sfp_model_rel and os.path.exists(sfp_model_rel) else None
    sfp_conf = config.get("detection", {}).get("sfp_conf", 0.30)

    from pipeline.port_pattern import (
        classify_ports_with_target_count,
        detect_patch_panel_ports,
    )

    if device.get("class_name") == "Patch Panel":
        classified = detect_patch_panel_ports(
            crop, port_model, conf=ports_conf, status_model=status_model
        )
        # Honor the user-supplied target count — patch panels are uniform
        # grids of 24 or 48, so trimming/padding by index is meaningful.
        mp = classified.get("main_ports", [])
        if len(mp) > target_count:
            mp = mp[:target_count]
            for i, p in enumerate(mp, 1):
                p["index"] = i
            classified["main_ports"] = mp
    else:
        classified = classify_ports_with_target_count(
            crop,
            port_model,
            target_count,
            conf=ports_conf,
            status_model=status_model,
            sfp_model=sfp_model,
            sfp_conf=sfp_conf,
        )

    main_ports = classified.get("main_ports", [])

    # If detection came back with FEWER ports than the operator says exist
    # (e.g. model found 11, user says 24), lay out exactly `target_count` ports
    # as a clean grid so the device shows the count they entered. The real
    # detections' STATUS is preserved by copying it from the nearest grid cell.
    # (When detection found MORE than the target, the branches above already
    # trimmed to the first `target_count` real ports.) Coordinates are in CROP
    # SPACE to match what runner.py expects.
    if len(main_ports) < target_count and target_count > 0:
        crop_h, crop_w = crop.shape[:2]
        real = list(main_ports)
        # Two rows when the count is even and the face is short & wide (typical
        # switch); otherwise a single row (patch panel / small device).
        rows = 2 if (target_count % 2 == 0 and crop_w > crop_h * 2.5) else 1
        per_row = target_count // rows
        cell_w = crop_w / per_row
        cell_h = crop_h / rows
        tol2 = (cell_w * 0.75) ** 2

        def _status_near(cx, cy):
            best, bd = None, tol2
            for p in real:
                pc = p.get("center", [0, 0])
                d = (pc[0] - cx) ** 2 + (pc[1] - cy) ** 2
                if d < bd:
                    bd, best = d, p
            return best.get("status", "unknown") if best else "unknown"

        # Column-major numbering for 2 rows (top of a column, then bottom),
        # left→right for a single row — matches the app's switch convention.
        grid, idx = [], 1
        for c in range(per_row):
            cx = int(cell_w * (c + 0.5))
            px1, px2 = max(0, int(cx - cell_w * 0.4)), min(crop_w, int(cx + cell_w * 0.4))
            for r in range(rows):
                cy = int(cell_h * (r + 0.5))
                py1, py2 = max(0, int(cy - cell_h * 0.34)), min(crop_h, int(cy + cell_h * 0.34))
                grid.append(
                    {
                        "index": idx,
                        "box": [px1, py1, px2, py2],
                        "center": [cx, cy],
                        "status": _status_near(cx, cy),
                        "class_name": "synthesized",
                        "confidence": 0.0,
                        "port_category": "main",
                        "inferred": True,
                        "synthesized": True,
                    }
                )
                idx += 1
        main_ports = grid
        classified["main_ports"] = main_ports
        log(
            f"relabel_port_count: laid out {target_count} ports ({rows} row(s)) "
            f"for {device.get('class_name')} (model found {len(real)})"
        )
    # Is an empty port open or under a dust cap: the pixel rules
    # (pipeline/cap_check.py), then the model team's classifier on every
    # empty port (pipeline/cap_model.py), read on the same crop the ports
    # were laid out on. A failure is logged and
    # leaves cap_state off the ports; the re-count still lands.
    try:
        from pipeline.cap_check import check_caps

        check_caps(
            crop,
            list(main_ports)
            + list(classified.get("sfp_ports") or [])
            + list(classified.get("console_ports") or [])
            + list(classified.get("other_ports") or []),
        )
        from pipeline.cap_model import apply_cap_model

        apply_cap_model(
            crop,
            list(main_ports)
            + list(classified.get("sfp_ports") or [])
            + list(classified.get("console_ports") or [])
            + list(classified.get("other_ports") or []),
            config,
        )
    except Exception as _e:
        log(f"relabel_port_count: cap check skipped: {type(_e).__name__}: {_e}")

    device["port_count"] = len(main_ports)
    device["ports"] = main_ports
    device["console_ports"] = classified.get("console_ports", [])
    device["sfp_ports"] = classified.get("sfp_ports", [])
    device["connected_ports"] = [p for p in main_ports if p.get("status") == "connected"]
    device["port_count_source"] = "user_relabeled"
    # The count changed, so the class may have to: a "Switch" corrected to
    # eight ports is a Router, and a Router we named that way corrected to
    # twenty-four is a Switch again.
    from pipeline.classify import router_rule

    router_rule(device)

    # Hardware rule: a Patch Panel is just RJ-45 jacks — no SFP cages, no
    # console port. Force-clear those so the picker doesn't show e.g. "24p 3s".
    if device.get("class_name") == "Patch Panel":
        device["console_ports"] = []
        device["sfp_ports"] = []

    # Save back atomically (write to .tmp then rename)
    tmp = map_path + ".tmp"
    with open(tmp, "w") as f:
        _json.dump(data, f, indent=2)
    os.replace(tmp, map_path)

    # Redraw the selected-device image so the port view actually SHOWS the
    # corrected count (a dot + index per port). Keep the previously-selected
    # port highlighted if we still have one in range.
    image_updated = False
    try:
        from pipeline.port import draw_classified

        images_dir = os.path.join(rack_dir, "images")
        os.makedirs(images_dir, exist_ok=True)
        hi = None
        sel_path = os.path.join(rack_dir, "selected_port_info.json")
        if os.path.exists(sel_path):
            try:
                with open(sel_path) as sf:
                    hi = _json.load(sf).get("port_number")
            except Exception:
                hi = None
        annotated = draw_classified(crop, classified, highlight_idx=hi, highlight_category="main")
        cv2.imwrite(os.path.join(images_dir, "5_selected_device_with_port.png"), annotated)
        image_updated = True
    except Exception as _e:
        log(f"relabel_port_count: image redraw skipped: {_e}")

    return {
        "ok": True,
        "device_index": device_index,
        "port_count": device["port_count"],
        "device": device,
        "image_updated": image_updated,
    }


def handle_pipeline(req):
    argv = [
        "pipeline.runner",
        "--image",
        req["image_path"],
        "--config",
        req["config_path"],
        "--output_dir",
        req["output_dir"],
    ]
    if req.get("command") == "analyze":
        argv.append("--detect_only")
    elif req.get("command") == "enrich_cables":
        argv.append("--enrich_cables")
    elif req.get("command") == "select":
        # Coerce here so a bad value fails as a clean error rather than
        # reaching argparse, which would sys.exit() and kill the worker.
        try:
            _dev = int(req["device_index"])
            _port = int(req["port"])
        except (KeyError, TypeError, ValueError):
            return {"ok": False, "error": "device_index and port must be integers"}
        argv += ["--device_index", str(_dev), "--port", str(_port)]
        port_category = req.get("port_category")
        if port_category:
            argv += ["--port_category", str(port_category)]
        # User-confirmed main-port count → force the same layout on select.
        if req.get("target_count"):
            argv += ["--target_count", str(req["target_count"])]
        # User's port-number shift → offset the DRAWN index labels so the
        # corrected numbering shows on the image (not the raw model indexes).
        if req.get("index_offset"):
            argv += ["--index_offset", str(req["index_offset"])]
    # Org scope is needed on BOTH analyze and select so device-class active
    # learning (find_match on "devices") can apply the org's prior corrections
    # while devices are being detected — mirrors the cable learning path.
    if req.get("org_id") is not None:
        argv += ["--org_id", str(req["org_id"])]
    # The organization's training folder (server/lib/store.js trainingDir),
    # passed in so the engine never builds a path into the data tree.
    if req.get("al_root"):
        argv += ["--al_root", str(req["al_root"])]

    old_argv = sys.argv
    sys.argv = argv
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            runner.main()
        # Forward pipeline tail to stderr so the operator keeps visibility.
        tail = buf.getvalue()[-500:].replace("\n", " | ")
        log(f"pipeline tail: {tail}")
        return {"ok": True}
    except SystemExit as e:
        # argparse calls sys.exit() when an argument will not parse, and
        # SystemExit derives from BaseException — so `except Exception` below
        # does NOT catch it. Unhandled, it tears down this long-lived worker
        # process, and one malformed device_index or port from a request takes
        # a worker out of the pool for every user, not just the caller.
        # Turn it into an ordinary failed response instead.
        tb = traceback.format_exc()
        log(f"pipeline REJECTED ARGS (exit {e.code}): argv={argv}\n--- traceback ---\n{tb}")
        return {"ok": False, "error": f"invalid pipeline arguments (exit {e.code})", "trace": tb}
    except Exception as e:
        # Log the full traceback to stderr so it shows up in the Node server's
        # worker output — otherwise only str(e) propagates to the client.
        tb = traceback.format_exc()
        captured_tail = buf.getvalue()[-2000:]
        log(
            f"pipeline FAILED: {e.__class__.__name__}: {e}\n--- captured stdout tail ---\n{captured_tail}\n--- traceback ---\n{tb}"
        )
        return {"ok": False, "error": str(e), "trace": tb}
    finally:
        sys.argv = old_argv


def handle_request(req):
    command = req.get("command")
    if command == "quality_check":
        return handle_quality_check(req)
    if command == "detect_only":
        return handle_detect_only(req)
    if command == "extract_best_frame":
        return handle_extract_best_frame(req)
    if command == "split_video_racks":
        return handle_split_video_racks(req)
    if command == "relabel_port_count":
        return handle_relabel_port_count(req)
    if command == "closeup_ocr":
        return handle_closeup_ocr(req)
    if command == "side_labels":
        return handle_side_labels(req)
    if command == "ocr_labels":
        return handle_ocr_labels(req)
    if command == "cable_flags":
        return handle_cable_flags(req)
    if command == "zone_read":
        return handle_zone_read(req)
    if command == "ocr_devices":
        return handle_ocr_devices(req)
    if command in ("analyze", "select", "enrich_cables"):
        return handle_pipeline(req)
    return {"ok": False, "error": f"Unknown command: {command}"}


def main():
    config_path = os.environ.get("RACKTRACK_CONFIG", "config.json")
    log(f"config: {config_path}")
    preload_models(config_path)
    log("ready")
    emit({"ready": True})

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        req_id = None
        try:
            req = json.loads(line)
            req_id = req.get("id")
            result = handle_request(req)
            result["id"] = req_id
            emit(result)
        except Exception as e:
            emit({"id": req_id, "ok": False, "error": str(e), "trace": traceback.format_exc()})


if __name__ == "__main__":
    main()
