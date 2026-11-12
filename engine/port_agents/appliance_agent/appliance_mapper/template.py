"""Appliance template from a vendor image: front.png (the front panel only) + template.json,
in the same format switch_fit reads.

Same idea as yolo_ports.build_template, sized for routers: a router face can have
as few as 2 network ports, and its console / management / USB jacks are kept as named
ports (they are often the only landmarks besides the data ports). Data ports are
numbered 1 top-left, 2 below it, 3 at the top of the next column, RJ45 then SFP.
"""
import json
import re
from pathlib import Path

import cv2
import numpy as np

import yolo_ports

MIN_DATA_PORTS = 2
FACE_H = 180          # template height in px
NAMED_MIN_CONF = 0.45 # console / USB / management detections kept on a template


def front_panel(bgr):
    """Crop to the band around the detected ports, as wide as the device body in that band.
    Returns (crop, (x0, y0)) or None."""
    from appliance_mapper import ports as rports
    dets = [d for d in rports._tidy(rports._raw(bgr))
            if d["label"] in yolo_ports.DATA_PORTS or d["label"] in yolo_ports.NAMED_PORTS]
    data = [d for d in dets if d["label"] in yolo_ports.DATA_PORTS and d["conf"] >= 0.4]
    if len(data) < MIN_DATA_PORTS:
        return None
    ph = float(np.median([d["xyxy"][3] - d["xyxy"][1] for d in data]))
    # the row band holding the data ports; named ports count only inside that band
    cy = np.median([(d["xyxy"][1] + d["xyxy"][3]) / 2 for d in data])
    keep = [d for d in dets if abs((d["xyxy"][1] + d["xyxy"][3]) / 2 - cy) < 2.5 * ph]
    if not keep or ph <= 1:                 # degenerate detections: no usable port row
        return None
    H, W = bgr.shape[:2]
    y0 = int(max(0, min(d["xyxy"][1] for d in keep) - 0.9 * ph))
    y1 = int(min(H, max(d["xyxy"][3] for d in keep) + 0.9 * ph))
    band = bgr[y0:y1].astype(np.int16)
    bg = np.median(np.concatenate([band[:, :3].reshape(-1, 3), band[:, -3:].reshape(-1, 3)]), axis=0)
    body = (np.abs(band - bg).max(axis=2) > 30).mean(axis=0) > 0.5
    x0 = int(min(d["xyxy"][0] for d in keep))
    x1 = int(max(d["xyxy"][2] for d in keep))
    while x0 > 0 and body[x0 - 1]:
        x0 -= 1
    while x1 < W - 1 and body[x1 + 1]:
        x1 += 1
    return bgr[y0:y1, x0:x1 + 1].copy(), (x0, y0)


def clean_ports(ports):
    """Router-specific clean-up of yolo_ports.detect_ports output:
    - only ports actually detected: its grid fill (right for switches, where cables hide
      jacks) invents ports on a clean product photo of a router;
    - at most one Console / Aux / Mgmt (the most confident): the model also calls T1 and
      WAN module jacks 'console'; USB keeps up to two;
    - data ports renumbered 1.. in their original order."""
    data = [p for p in ports if p["type"] in ("rj45", "sfp") and p.get("status") != "not seen"]
    for i, p in enumerate(data, 1):
        p["id"] = i
    named, count = [], {}
    for p in sorted([p for p in ports if isinstance(p["id"], str)], key=lambda p: -(p.get("confidence") or 0)):
        limit = 2 if p["id"] == "USB" else 1
        if count.get(p["id"], 0) < limit:
            count[p["id"]] = count.get(p["id"], 0) + 1
            named.append(p)
    return data + named


# a port's printed label: FE 0/1, GE0/0, Gi0/0/1, eth1, ETH5, SFP+1, SFP-3, WAN, WAN/LAN, LAN 2,
# CONSOLE, CON, AUX, MGMT, USB, PoE IN, TE0/1/0 ...
LABEL_RE = re.compile(r"^(?:(?:FE|GE|GI|TE|XE|ET|ETH|ETHER|E|SFP\+?|SFP28|QSFP|WAN|LAN|PORT|P|MGMT|MGT|COMBO)"
                      r"[\s.\-·]*\d{0,2}(?:[/\-]\d{1,2}){0,3}(?:/(?:WAN|LAN|POE\w*))?"
                      r"|SFP\s?(?:WAN|LAN)|CONSOLE|CON|AUX|MGMT|USB|WAN/LAN|POE\s?IN|POE\s?OUT)$", re.I)
_ocr = None


def port_labels(full, s):
    """Printed labels on the full-resolution panel crop: [(text, cx, cy)] in template pixels
    (the crop scaled by s). Read with RapidOCR on an upscaled copy, in overlapping tiles."""
    global _ocr
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return []
    _ocr = _ocr or RapidOCR()
    up = max(1.0, 700 / full.shape[0])
    big = cv2.resize(full, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    H, W = big.shape[:2]
    tw = int(min(W, H * 2.2))
    step = max(1, int(tw * 0.7))
    out = []
    for x in range(0, max(1, W - tw // 3), step):
        res, _ = _ocr(big[:, x:x + tw])
        for box, text, conf in res or []:
            t = re.sub(r"^[^\w]+|[^\w+/]+$", "", text.strip()).replace("·", "-")
            # OCR reads a zero as the letter O: ethO -> eth0, FE O/1 -> FE 0/1
            t = re.sub(r"(?i)(?<=[a-z])O(?=$|[/\-])|(?<=[\s/])O(?=$|/)", "0", t)
            if conf < 0.5 or not LABEL_RE.match(t) or re.search(r"\d\s*[-~]\s*\d", t.replace("/", "")) and not re.search(r"\d/\d", t):
                continue                                  # 'LAN 1-5' labels a group of ports, not one
            b = np.float32(box)
            cx, cy = (x + b[:, 0].mean()) / up * s, b[:, 1].mean() / up * s
            if all(abs(cx - o[1]) > 3 or abs(cy - o[2]) > 3 or o[0] != t for o in out):
                out.append((t.upper(), float(cx), float(cy)))
    return out


def name_ports(ports, labels):
    """Each port takes the label printed right above or below it (horizontally over the
    port, within about one port height of its edge); a label names one port."""
    pairs = []
    for i, p in enumerate(ports):
        x, y, w, h = p["box"]
        for j, (t, cx, cy) in enumerate(labels):
            if abs(cx - (x + w / 2)) > 0.75 * w:
                continue
            if y + h - 0.1 * h <= cy <= y + h + 1.3 * h:
                d = cy - (y + h)
            elif y - 1.3 * h <= cy <= y + 0.1 * h:
                d = y - cy
            else:
                continue
            pairs.append((abs(d) + abs(cx - (x + w / 2)) * 0.3, i, j))
    used_p, used_l = set(), set()
    for _, i, j in sorted(pairs):
        if i in used_p or j in used_l:
            continue
        used_p.add(i); used_l.add(j)
        ports[i]["name"] = re.sub(r"\s+", " ", labels[j][0]).strip()
    retype(ports)
    # names only where they are consistent: when most network ports could not be named
    # ("2, 3, LAN, LAN, 6"), all of them keep numbers; named ports (CONSOLE, AUX) keep theirs
    data = [p for p in ports if isinstance(p["id"], int)]
    if data and sum(1 for p in data if p.get("name")) < 0.6 * len(data):
        for p in data:
            p.pop("name", None)


def retype(ports):
    """The printed label wins over the port model's guess of the kind: a cage labelled
    SFP WAN is an SFP port, not a console; a jack labelled CONSOLE / AUX / MGMT is a named
    port, not a network port. Network ports are then renumbered 1.. in their order."""
    for p in ports:
        n = (p.get("name") or "").upper()
        if not n:
            continue
        if n.startswith(("SFP", "QSFP")):
            p["type"] = "sfp"
            if isinstance(p["id"], str):
                p["id"] = 0
        elif n in ("CONSOLE", "CON"):
            p["type"], p["id"] = "console", "Console"
        elif n == "AUX":
            p["type"], p["id"] = "aux", "Aux"
        elif n in ("MGMT", "MGT"):
            p["type"], p["id"] = "management_port", "Mgmt"
        elif n == "USB":
            p["type"], p["id"] = "usb_a", "USB"
        elif isinstance(p["id"], str) and p["type"] not in ("usb_a", "usb_b", "usb_c"):
            p["type"], p["id"] = "rj45", 0              # a network port the model took for a console
    k, seen = 0, {}
    for p in ports:
        if isinstance(p["id"], int):
            k += 1
            p["id"] = k
        else:                                           # one id per named port: Console, Console 2
            seen[p["id"]] = seen.get(p["id"], 0) + 1
            if seen[p["id"]] > 1:
                p["id"] = f"{p['id']} {seen[p['id']]}"


def build(bgr, out_dir, vendor, model, source="", device="firewall"):
    """Write out_dir/front.png + template.json; returns the template meta.
    Raises ValueError when the image shows fewer than MIN_DATA_PORTS network ports."""
    fp = front_panel(bgr)
    if fp is None:
        raise ValueError(f"fewer than {MIN_DATA_PORTS} network ports found in the template image")
    crop, _ = fp
    full = crop
    s = FACE_H / crop.shape[0]
    crop = cv2.resize(crop, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC if s > 1 else cv2.INTER_AREA)
    from appliance_mapper import ports as rports
    ports, info = rports.detect(crop)
    ports = clean_ports(ports)
    data = [p for p in ports if p["type"] in ("rj45", "sfp")]
    if len(data) < MIN_DATA_PORTS:
        raise ValueError(f"only {len(data)} network ports found on the cropped front panel")
    rj = [p["box"] for p in ports if p["type"] == "rj45"]
    pad = int(np.median([b[3] for b in rj]) * 0.25) if rj else 0
    # the RJ45 block is left out of feature matching: cables cover it in rack photos
    ignore = [[int(min(b[0] for b in rj)) - pad, int(min(b[1] for b in rj)) - pad,
               int(max(b[0] + b[2] for b in rj) - min(b[0] for b in rj)) + 2 * pad,
               int(max(b[1] + b[3] for b in rj) - min(b[1] for b in rj)) + 2 * pad]] if len(rj) >= 4 else []
    h, w = crop.shape[:2]
    # padding can push the region past the image edge: clip it (a negative x or y voids the mask)
    ignore = [[max(0, x), max(0, y), min(w, x + bw) - max(0, x), min(h, y + bh) - max(0, y)]
              for x, y, bw, bh in ignore]
    named = {}
    out_ports = []
    for p in ports:
        if isinstance(p["id"], str) and (p.get("confidence") or 0) < NAMED_MIN_CONF:
            continue                                  # unsure console/USB hits sit on screws and latches
        pid = p["id"]
        if isinstance(pid, str):                      # Console, Mgmt, USB - number repeats
            named[pid] = named.get(pid, 0) + 1
            pid = pid if named[pid] == 1 else f"{pid} {named[pid]}"
        out_ports.append({"id": pid, "type": p["type"], "poe": None, "box": [int(round(v)) for v in p["box"]]})
    labels = port_labels(full, s)
    name_ports(out_ports, labels)
    meta = {
        "vendor": vendor, "model": model, "device": device, "description": "",
        "summary": f"{info['rj45']}x RJ45 | {info['sfp']}x SFP"
                   + (f" | {len(named)} named port type(s): {', '.join(named)}" if named else "")
                   + " | built from the vendor image by the appliance mapper",
        "image": "front.png", "image_source": (str(source) if re.match(r"https?://", str(source or "")) else re.split(r"[\\/]", str(source or ""))[-1]), "auto_generated": True, "numbering_scheme": "top-odd",
        "match_regions": [[0, 0, w, h]], "ignore_regions": ignore, "ports": out_ports,
        "labels_read": [t for t, _, _ in labels],
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / "front.png"), crop)
    (out_dir / "template.json").write_text(json.dumps(meta, indent=1))
    return meta


def draw(out_dir):
    """front.png with each template port boxed and numbered (the 'template' step image)."""
    out_dir = Path(out_dir)
    img = cv2.imread(str(out_dir / "front.png"))
    meta = json.loads((out_dir / "template.json").read_text())
    up = 3
    img = cv2.resize(img, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    colors = {"rj45": (40, 170, 40), "sfp": (230, 120, 20)}
    for p in meta["ports"]:
        x, y, w, h = [v * up for v in p["box"]]
        c = colors.get(p["type"], (40, 40, 220))
        cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), c, 3)
        cv2.putText(img, str(p.get("name") or p["id"]), (int(x + 4), int(y + h - 8)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7 if isinstance(p["id"], int) and not p.get("name") else 0.5, c, 2, cv2.LINE_AA)
    return img
