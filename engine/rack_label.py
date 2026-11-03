"""The rack's own label, read from the sweep: its ID (a code such as SP-HYD-RM01-R01-R1) and its name (such as NETWORK RACK 2).

The label sits above the rack's top unit, so it is read from the frames at the start of the sweep that show the top of the rack,
and from the strip of the stitched picture above the first unit. Each frame is read with EasyOCR (the reader RT-EOS uses,
pipeline/ocr_labels.py); the readings are pooled and a label wins by its summed confidence. Camera misreadings of digits
(O for 0, I for 1) are repaired where a segment can only be a number, the way RT-EOS repairs unit segments.

    python3 src/rack_label.py data/runs/<id>      prints what it reads (no file written)
"""
import local_models  # noqa: F401  (model caches inside this folder, hub lookups off)
import json
import os
import pathlib
import re
import sys

import cv2

_READER = None
MIN_CONF = 0.25
DEVICE_LABEL = re.compile(r"(^|-)U\d{2}(-|$)")          # SP-R3-U13-SW02 names a unit: a device's label, not the rack's


def _reader():
    global _READER
    if _READER is None:
        import easyocr
        _READER = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _READER


def _fix_code(text):
    """SP-HYD-RMOI-ROI-R1 -> SP-HYD-RM01-R01-R1: in each segment the run of 0/O/I at its end is a number."""
    out = []
    for seg in re.split(r"([\s\-_]+)", text.upper()):
        m = re.match(r"^([A-Z]*?)([0-9OI]+)$", seg)
        if m and (len(m.group(2)) >= 2 or any(c.isdigit() for c in m.group(2))) and m.group(1):
            seg = m.group(1) + m.group(2).replace("O", "0").replace("I", "1")
        out.append(seg)
    return "".join(out)


def _canon(text):
    return re.sub(r"[^A-Z0-9]", "", _fix_code(text))


def _kind(text):
    t = text.upper()
    if "RACK" in t:
        return "name"
    if re.match(r"^[A-Z0-9]+([\-_][A-Z0-9]+){2,}$", _fix_code(t).replace(" ", "")) and any(c.isdigit() for c in _fix_code(t)) and not DEVICE_LABEL.search(_fix_code(t)):
        return "id"
    return None


WORDS = ("NETWORK", "SERVER", "CABINET", "STORAGE", "TELECOM", "PATCH", "CORE", "ACCESS", "RACK", "ROOM")


def _edits(a, b):
    """Levenshtein distance, small words only."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _word(w):
    """NETKORK -> NETWORK: a word one or two letters off a common rack word is that word."""
    u = re.sub(r"[^A-Z0-9]", "", w.upper())
    if len(u) >= 5:
        best = min(WORDS, key=lambda x: _edits(u, x))
        if _edits(u, best) <= 2:
            return best
    return w.upper()



def _lines(found):
    """EasyOCR's boxes joined into lines: NETWORK, RACK, 1 read as three boxes on one tilted label are one line
    'NETWORK RACK 1'. Boxes whose centres sit within 0.8 of a box height share a line; a line reads left to right."""
    boxes = sorted(((sum(p[1] for p in b) / 4, sum(p[0] for p in b) / 4, max(p[1] for p in b) - min(p[1] for p in b), t, c) for b, t, c in found))
    lines = []
    for cy, cx, h, t, c in boxes:
        if lines and abs(cy - lines[-1]["cy"]) <= 0.8 * max(h, lines[-1]["h"]):
            L = lines[-1]; L["items"].append((cx, t, c)); L["cy"] = (L["cy"] * (len(L["items"]) - 1) + cy) / len(L["items"]); L["h"] = max(L["h"], h)
        else:
            lines.append({"cy": cy, "h": h, "items": [(cx, t, c)]})
    for L in lines:
        items = sorted(L["items"])
        if len(items) > 1:
            yield _join_fragments(" ".join(t for _, t, _ in items)), sum(c for _, _, c in items) / len(items)


def _join_fragments(text):
    """NE NORK RACK -> NETWORK RACK: two neighbouring fragments that together are a common rack word (two edits off) are that word."""
    out = []
    for w in text.split():
        if out and len(out[-1]) < 5 and any(_edits((out[-1] + w).upper(), x) <= 2 for x in WORDS):
            out[-1] = min(WORDS, key=lambda x: _edits((out[-1] + w).upper(), x))
        else:
            out.append(w)
    return " ".join(out)


def _read(img, where, reads):
    found = _reader().readtext(img)
    for text, conf in [(t, c) for _, t, c in found] + list(_lines(found)):
        text = " ".join(_word(w) if len(w) >= 5 else w for w in text.split())
        kind = _kind(text)
        if kind and conf >= MIN_CONF:
            reads.append({"kind": kind, "text": text, "conf": round(float(conf), 3), "where": where})


def read_rack_label(run_dir, max_frames=10):
    """What the rack's label says, from one run folder. Returns {id, name, reads, frames}; id and name are None when not read."""
    run_dir = pathlib.Path(run_dir)
    r = json.loads((run_dir / "result.json").read_text())
    reads = []
    rows = (r.get("occupancy_frames") or {}).get("rows") or []
    rack = cv2.imread(str(run_dir / "rack.png"))
    if rack is not None and rows:
        y1 = rows[0]["box"][1]
        band = rack[max(0, y1 - 220): y1 + 30]
        if band.size:
            _read(cv2.resize(band, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC), "rack", reads)
    vmap = (r.get("video") or {}).get("map") or []
    pitch = (r.get("ladder") or {}).get("pitch_px") or 40
    top_frames = [m for m in vmap if m["rack_y"][0] < 1.5 * pitch]               # the frames that still show the rack's top
    pick = sorted(top_frames, key=lambda m: -m["sharpness"])[:max_frames - 3] + sorted(vmap, key=lambda m: m["frame"])[:3]
    seen, frames = set(), []
    for m in pick:
        if m["frame"] not in seen:
            seen.add(m["frame"]); frames.append(m["frame"])
    video = next((run_dir / f for f in ("video_upright.avi", "video.mp4") if (run_dir / f).exists()), None)
    if video is not None:
        cap = cv2.VideoCapture(str(video))
        for f in frames:
            cap.set(cv2.CAP_PROP_POS_FRAMES, f)
            ok, fr = cap.read()
            if ok:
                _read(cv2.resize(fr[: int(fr.shape[0] * 0.55)], None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC), "frame %d" % f, reads)
        cap.release()

    def best(kind):
        votes = {}
        for x in reads:
            if x["kind"] != kind:
                continue
            k = _canon(x["text"])
            v = votes.setdefault(k, {"score": 0.0, "n": 0, "top": x})
            v["score"] += x["conf"]; v["n"] += 1
            if x["conf"] > v["top"]["conf"]:
                v["top"] = x
        votes = {k: v for k, v in votes.items() if k not in ("RACK", "THERACK")}   # the word RACK alone names no rack
        if not votes:
            return None
        k, v = max(votes.items(), key=lambda kv: kv[1]["score"])
        text = _fix_code(v["top"]["text"]) if kind == "id" else v["top"]["text"].upper()
        return {"text": text, "seen": v["n"], "confidence": round(v["score"] / v["n"], 3)}

    name = best("name")
    if name:
        name["text"] = " ".join(_word(w) for w in name["text"].split())
        if name["text"] in ("RACK", "THE RACK"):
            name = None                                 # the word RACK alone names no rack
    if name and not re.search(r"\d", name["text"]):
        name["number_read"] = False                     # NETWORK RACK with its number too small to read
    return {"id": best("id"), "name": name, "reads": reads, "frames": frames}


if __name__ == "__main__":
    os.environ.setdefault("OMP_NUM_THREADS", "2")
    print(json.dumps(read_rack_label(sys.argv[1]), indent=1))
