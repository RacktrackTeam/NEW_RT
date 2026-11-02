"""
physical_layer.py — the physical layer report for one scanned rack.

One file per rack, built only from what the scan actually produced, in the
order the rack ladder asks for it:

  rack label or id   -> from the rail chips (side_labels.json) or the front
                        labels (labels-front.json); otherwise only the minted
                        RK- id, and it says so
  device labels      -> the front label sitting inside each box, else the
                        identifier inside that box's own OCR text
  ports              -> every socket with its status: connected / empty / unknown
  cables             -> colour and connector on every connected socket
  ocr metadata       -> make, model, raw text and confidences per box

Nothing is invented. A box with no label carries label: null. A socket the
status model did not measure stays "unknown". A rack with no readable label
carries the RK- id and source "minted".

Usage:
    python -m pipeline.physical_layer <rack_id> --dir <engine dir>          # writes <engine dir>/physical_layer.json
    python -m pipeline.physical_layer <rack_id> --dir <engine dir> --json   # prints instead of writing
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

SCHEMA = "racktrack-physical-layer/1"

# ---------------------------------------------------------------- label text

# Same rule as server/app.js normalizeOcrLabelText: repair O/0 and I/1 next to
# digits, then require prefix + separator + letters + digits (RVEW-CORE-SW01).
_LABEL_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)*[-_][A-Z]+\d+$")
_STACK_RE = re.compile(r"(?:stack\s*)?mem(?:ber|rer|8er)\s*(\d+)", re.I)
# What a rack identifier tends to look like on a chip or a sign.
_RACK_RE = re.compile(r"^(?:RACK|RK|CAB|CABINET|R)[-_ ]?[A-Z0-9][A-Z0-9\-_]*$", re.I)


def _repair(tok: str) -> str:
    t = re.sub(r"([A-Z])O(?=\d)", r"\g<1>0", tok)
    t = re.sub(r"(\d)O", r"\g<1>0", t)
    t = re.sub(r"([A-Z])I(?=\d)", r"\g<1>1", t)
    t = re.sub(r"(\d)I", r"\g<1>1", t)
    return t.upper()


_CONFUSABLE = str.maketrans(
    {"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "Q": "0", "D": "0"}
)
_EXEMPT_SEGMENTS = {"USB", "UPS"}


def segment_shapes(label: str) -> list | None:
    """[('A',2,0), ('AD',1,1), ('AD',1,2), ('AD',2,2)] for SP-R1-U17-SW03:
    per segment the kind, how many letters, how many digits.
    """
    if not label:
        return None
    out = []
    for seg in re.split(r"[-_]", label):
        m = re.fullmatch(r"([A-Z]*)(\d*)", seg)
        if not m or not seg:
            return None
        letters, digits = m.group(1), m.group(2)
        out.append(("A" if not digits else "D" if not letters else "AD", len(letters), len(digits)))
    return out


def repair_with_shapes(tok: str, shapes: list | None) -> str | None:
    """Rebuild a mangled token from the shape a clean sibling label proved:
    the letters keep their length, everything after them is read as digits
    with the usual confusions repaired. USB and UPS stay as printed. The last
    segment must end with the pattern's number of digits, or the token is
    refused rather than guessed.
    """
    if not shapes:
        return None
    segs = re.split(r"([-_])", tok.upper())
    parts, seps = segs[0::2], segs[1::2]
    if len(parts) != len(shapes):
        return None
    fixed = []
    for i, (seg, (kind, nl, nd)) in enumerate(zip(parts, shapes)):
        if seg in _EXEMPT_SEGMENTS or kind == "A":
            fixed.append(seg)
            continue
        head, tail = seg[:nl], seg[nl:]
        if kind == "D":
            head, tail = "", seg
        tail = tail.translate(_CONFUSABLE)
        if not tail.isdigit() or not head.isalpha():
            return None
        if i == len(parts) - 1 and len(tail) != nd:
            return None
        fixed.append(head + tail)
    return "".join(f + d for f, d in zip(fixed, seps + [""]))


def normalize_label(raw: str | None, shapes: list | None = None) -> str | None:
    if not raw:
        return None
    for tok in str(raw).split():
        tok = tok.strip(".,:;()[]{}'\"")
        if not tok:
            continue
        if shapes:
            fixed2 = repair_with_shapes(_repair(tok), shapes)
            if fixed2 and _LABEL_RE.match(fixed2):
                return fixed2
            continue
        fixed = _repair(tok)
        if _LABEL_RE.match(fixed):
            return fixed
    return None


def label_pattern(label: str | None) -> dict | None:
    if not label:
        return None
    m = re.match(r"^(.+)([-_])([A-Z]+)(\d+)$", label)
    if not m:
        return None
    return {
        "prefix": m.group(1),
        "sep": m.group(2),
        "class_token": m.group(3),
        "padding": len(m.group(4)),
    }


# ---------------------------------------------------------------- reading


def _read_json(p: Path):
    try:
        with open(p, encoding="utf8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _unit_no(u: str) -> int:
    m = re.search(r"(\d+)", str(u))
    return int(m.group(1)) if m else 0


# ---------------------------------------------------------------- rack label


def rack_identity(rack_id: str, front: dict | None, side: dict | None, rack_dir: Path) -> dict:
    """Best rack label with its source, and every candidate seen."""
    cands = []
    # Rail chips: identifier-shaped text on the rack's own rails.
    #
    # A reading taken from the RACK'S OWN RAIL is exempt from the shape test.
    # The test exists to stop a device chip on the side margin being mistaken
    # for the rack's name, and a customer's rack id is under no obligation to
    # start with the word RACK: the office rack wears SP-HYB-RM01-R01-R1, which
    # this refused, so the one label in the photograph that names the rack was
    # thrown away and every scan ended up asking a person.
    for l in (side or {}).get("labels", []) or []:
        t = _repair(str(l.get("text", "")).strip())
        if not t:
            continue
        on_the_rail = str(l.get("side") or "").lower() == "rail"
        if not on_the_rail and not _RACK_RE.match(t):
            continue
        cands.append(
            {
                "text": t,
                "conf": float(l.get("conf") or 0),
                "source": "rack rail" if on_the_rail else "rail chip",
                "side": l.get("side"),
                "yPct": l.get("yPct"),
            }
        )
    # Front labels that read as a rack identifier (a sign on the door or the top).
    for l in (front or {}).get("labels", []) or []:
        raw = str(l.get("text", "")).strip()
        # The whole reading first: a sign that says "RACK 2" is one identifier,
        # and word by word it would leave "RACK" and lose the number.
        for tok in ([raw] if " " in raw else []) + raw.split():
            t = _repair(tok)
            if _RACK_RE.match(t) and len(t) >= 3 and not _LABEL_RE.match(t):
                cands.append(
                    {
                        "text": t,
                        "conf": float(l.get("conf") or 0),
                        "source": "front label",
                        "yPct": (l.get("bbox") or {}).get("yPct"),
                    }
                )
    best = max(cands, key=lambda c: c["conf"]) if cands else None
    return {
        "id": rack_id,
        "label": {"text": best["text"], "conf": best["conf"], "source": best["source"]}
        if best
        else {"text": None, "conf": None, "source": "minted"},
        "candidates": cands,
    }


# ---------------------------------------------------------------- device labels


def _front_labels_in(box, front: dict | None) -> list:
    """Every front label whose vertical centre sits inside this box (as app.js does), best confidence first."""
    if not front or not box:
        return []
    y0, y1 = box[1], box[3]
    hits = []
    for l in front.get("labels", []) or []:
        bb = l.get("bbox") or {}
        ly, lh = bb.get("y", 0), bb.get("h", 0)
        cy = ly + lh / 2
        if y0 - 6 <= cy <= y1 + 6:
            hits.append(l)
    return sorted(hits, key=lambda l: -(l.get("conf") or 0))


def device_label(
    dev: dict, front: dict | None, ocr_row: dict | None, shapes: list | None = None
) -> dict:
    text, raw, conf, source = None, None, None, None
    fl = None
    for cand in _front_labels_in(dev.get("box"), front):
        t = normalize_label(cand.get("text"), shapes)
        if t:
            fl, raw, text, conf, source = cand, cand.get("text"), t, cand.get("conf"), "front label"
            break
    if not text and ocr_row and ocr_row.get("raw_text"):
        t = normalize_label(ocr_row.get("raw_text"), shapes)
        if t:
            text, raw, conf, source = (
                t,
                ocr_row.get("raw_text"),
                ocr_row.get("ocr_conf"),
                "ocr text",
            )
    stack = None
    for src in [l.get("text") for l in _front_labels_in(dev.get("box"), front)] + [
        ocr_row.get("raw_text") if ocr_row else None
    ]:
        m = _STACK_RE.search(str(src or ""))
        if m:
            stack = int(m.group(1))
            break
    return {
        "text": text,
        "raw": raw if text else None,
        "conf": conf if text else None,
        "source": source,
        "stack_member": stack,
    }


# ---------------------------------------------------------------- ports and cables

_PORT_TYPE = {
    "RJ45": "RJ45",
    "SFP": "SFP",
    "QSFP": "QSFP",
    "CONSOLE": "CONSOLE",
    "AUX": "AUX",
    "MANAGEMENT_PORT": "MGMT",
    "USB_A": "USB",
    "USB_B": "USB",
    "USB_C": "USB",
}


def _port(p: dict, n: int) -> dict:
    status = p.get("status") if p.get("status") in ("connected", "empty") else "unknown"
    out = {
        "port_number": n,
        "port_type": _PORT_TYPE.get(str(p.get("class_name")), str(p.get("class_name") or "port")),
        "status": status,
    }
    if status == "connected" and (p.get("cable_color") or p.get("cable_connector")):
        out["cable"] = {
            "connector": p.get("cable_connector"),
            "color": p.get("cable_color"),
            "confidence": round(float(p.get("cable_confidence") or 0), 3),
        }
    return out


def device_ports(dev: dict) -> list:
    out = []
    for group in ("ports", "sfp_ports", "console_ports", "other_ports"):
        rows = dev.get(group) or []
        rows = sorted(
            rows,
            key=lambda r: (
                r.get("index") is None,
                r.get("index") or 0,
                (r.get("center") or [0, 0])[0],
            ),
        )
        for i, p in enumerate(rows, start=1):
            out.append(_port(p, int(p.get("index") or i) if group == "ports" else i))
    return out


def pdu_outlets(dev: dict) -> list:
    rows = dev.get("power_ports") or []
    out = []
    for i, p in enumerate(rows, start=1):
        st = p.get("status")
        out.append(
            {
                "outlet_number": int(p.get("index") or i),
                "status": "in_use"
                if st == "connected"
                else ("empty" if st == "empty" else "unknown"),
            }
        )
    return out


# ---------------------------------------------------------------- build


def build(rack_id: str, rack_dir) -> dict:
    rack_dir = Path(rack_dir)
    dum = _read_json(rack_dir / "device_unit_map.json")
    if not dum:
        raise SystemExit(f"{rack_id}: no device_unit_map.json under {rack_dir}")
    front = _read_json(rack_dir / "labels-front.json")
    side = _read_json(rack_dir / "side_labels.json")
    ocr = _read_json(rack_dir / "ocr_devices.json") or {}
    ocr_rows = ocr.get("devices", ocr) if isinstance(ocr, dict) else ocr
    ocr_by_pos = {}
    for r in ocr_rows or []:
        if isinstance(r, dict) and r.get("position"):
            ocr_by_pos.setdefault(str(r["position"]).lower(), r)
    meta = _read_json(rack_dir / "scan_meta.json") or {}
    overrides = _read_json(rack_dir / "device_overrides.json") or {}

    devices = dum.get("devices") or []

    # Pass one: the labels that parse as printed teach the pattern's shape,
    # so pass two can repair the ones OCR mangled (SP-RI-UIS-SWO4 -> SP-R1-U15-SW04).
    shapes = None
    seen_shapes = []
    for dev in devices:
        first = str((dev.get("units") or [None])[0] or "").lower()
        row = ocr_by_pos.get(first) or {}
        # every reading of this box teaches the shape, not only the winning one:
        # the OCR text may hold the clean R1 while the sticker read RI
        texts = [l.get("text") for l in _front_labels_in(dev.get("box"), front)] + [
            row.get("raw_text")
        ]
        for t in texts:
            parsed = normalize_label(t)
            sh = segment_shapes(parsed) if parsed else None
            if sh:
                seen_shapes.append(sh)
    if seen_shapes:
        # OCR can only turn digits into letters (R1 -> RI), never the reverse,
        # so per segment the true shape is the fewest letters and the most
        # digits any label showed. Vectors of a different length are another
        # convention and are ignored for this rack.
        n = max(
            set(len(v) for v in seen_shapes),
            key=lambda k: sum(1 for v in seen_shapes if len(v) == k),
        )
        same = [v for v in seen_shapes if len(v) == n]
        shapes = []
        for i in range(n):
            nl = min(v[i][1] for v in same)
            nd = max(v[i][2] for v in same)
            shapes.append(("A" if nd == 0 else "D" if nl == 0 else "AD", nl, nd))

    units_out = {}
    cables = []
    counts = {"ports": 0, "connected": 0, "empty": 0, "unknown": 0, "cables_by_color": {}}
    labels_seen = []

    for dev in devices:
        units = [str(u).lower() for u in (dev.get("units") or [])]
        first = units[0] if units else None
        key = ",".join(sorted(units, key=_unit_no)) if units else "unplaced"
        cls = dev.get("class_name") or "Unidentified"
        ocr_row = ocr_by_pos.get(first) if first else None
        ov = overrides.get(first.upper()) if first else None

        entry = {"device_type": cls, "confidence": round(float(dev.get("confidence") or 0), 3)}
        if cls not in ("Empty",):
            lab = device_label(dev, front, ocr_row, shapes)
            entry["label"] = lab
            if lab.get("text"):
                labels_seen.append(lab["text"])
            entry["ocr"] = {
                "make": (ov or {}).get("make") or (ocr_row or {}).get("make"),
                "model": (ov or {}).get("model") or (ocr_row or {}).get("model"),
                "raw_text": (ocr_row or {}).get("raw_text"),
                "ocr_conf": (ocr_row or {}).get("ocr_conf"),
                "match_conf": (ocr_row or {}).get("match_conf"),
                "source": "override" if ov else (ocr_row or {}).get("source", "not read"),
            }
        if cls == "PDU":
            outs = pdu_outlets(dev)
            entry["outlet_count"] = len(outs) or dev.get("power_total") or 0
            entry["outlets"] = outs
        elif cls not in ("Empty", "Closed Unit", "Unidentified"):
            ports = device_ports(dev)
            entry["port_count"] = (
                dev.get("port_count") if dev.get("port_count") is not None else len(ports)
            )
            entry["port_count_source"] = dev.get("port_count_source")
            entry["ports"] = ports
            for p in ports:
                counts["ports"] += 1
                counts[p["status"]] += 1
                if p.get("cable"):
                    c = p["cable"]
                    cables.append(
                        {
                            "unit": key,
                            "device_type": cls,
                            "port_type": p["port_type"],
                            "port_number": p["port_number"],
                            "connector": c.get("connector"),
                            "color": c.get("color"),
                            "confidence": c.get("confidence"),
                        }
                    )
                    counts["cables_by_color"][str(c.get("color"))] = (
                        counts["cables_by_color"].get(str(c.get("color")), 0) + 1
                    )
        units_out.setdefault(key, []).append(entry)

    rack = rack_identity(rack_id, front, side, rack_dir)
    # A rack segment inside the device labels (SP-R1-U17-SW03 -> R1) is a
    # candidate too, ranked below anything read off the rack itself.
    if rack["label"]["text"] is None and labels_seen:
        segs = [re.split(r"[-_]", t) for t in labels_seen]
        for i in range(min(len(x) for x in segs)):
            col = {x[i] for x in segs}
            if len(col) == 1:
                seg = next(iter(col))
                if re.fullmatch(r"(?:R|RK|RACK|CAB)\d+[A-Z]?", seg):
                    rack["candidates"].append(
                        {
                            "text": seg,
                            "conf": None,
                            "source": "inferred from device labels",
                            "from": labels_seen,
                        }
                    )
                    rack["label"] = {
                        "text": seg,
                        "conf": None,
                        "source": "inferred from device labels",
                    }
                    break
    rack["name"] = rack["label"]["text"]
    rack["space"] = meta.get("space")
    rack["unit_source"] = dum.get("unit_source")
    rack["units_detected"] = dum.get("units_detected")

    pattern = None
    for t in labels_seen:
        pattern = label_pattern(t)
        if pattern:
            break

    def _uk(k):
        return _unit_no(k.split(",")[0]) if k != "unplaced" else 10**6

    return {
        "schema": SCHEMA,
        "rack_id": rack_id,
        "image": dum.get("image"),
        "generated_at": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "units_count": len(dum.get("units_detected") or []),
        "devices_count": len(devices),
        "rack": rack,
        "labels": {
            "front": [
                {
                    "text": l.get("text"),
                    "conf": l.get("conf"),
                    "yPct": (l.get("bbox") or {}).get("yPct"),
                }
                for l in (front or {}).get("labels", []) or []
            ],
            "rail": [
                {
                    "text": l.get("text"),
                    "side": l.get("side"),
                    "yPct": l.get("yPct"),
                    "conf": l.get("conf"),
                }
                for l in (side or {}).get("labels", []) or []
            ],
            "pattern": pattern,
            "sources": {"front": bool(front), "rail": bool(side), "ocr": bool(ocr_rows)},
        },
        "units": [
            {"unit": k, "devices": v}
            for k, v in sorted(units_out.items(), key=lambda kv: _uk(kv[0]))
        ],
        "cables": cables,
        "counts": counts,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("Usage:")[0].strip())
    ap.add_argument("rack_id")
    ap.add_argument("--dir", required=True, help="the scan's engine folder")
    ap.add_argument(
        "--json", action="store_true", help="print to stdout instead of writing the file"
    )
    a = ap.parse_args(argv)
    doc = build(a.rack_id, a.dir)
    text = json.dumps(doc, indent=2)
    if a.json:
        print(text)
        return 0
    out = Path(a.dir) / "physical_layer.json"
    out.write_text(text + "\n", encoding="utf8")
    r = doc["rack"]["label"]
    labelled = sum(
        1 for u in doc["units"] for d in u["devices"] if (d.get("label") or {}).get("text")
    )
    print(
        f"{a.rack_id}: rack label {r['text']!r} ({r['source']}), {doc['devices_count']} boxes, {labelled} labelled, "
        f"{doc['counts']['ports']} sockets ({doc['counts']['connected']} cabled, {doc['counts']['empty']} empty, {doc['counts']['unknown']} unknown), "
        f"{len(doc['cables'])} cables -> {out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
