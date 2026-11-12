"""Per-model correction stores + verified-port-layout store.

Layout under the organization's training folder, which the server passes in
(server/lib/store.js trainingDir: <data>/orgs/<org>/training):
    cable/
        corrections.json        # {phash: {label, pred, embedding, ts, source}}
        confirmed_images/       # crop snapshots
    devices/
        corrections.json
    ports/
        verified_ports.json     # MODEL BYPASS - highest priority
    port_type/
    confirmed_racks/

With no training folder given there is no memory: reads find nothing and a
write is refused. The engine never builds a path into the data tree itself.
"""

from __future__ import annotations

import json
import shutil
import threading
from datetime import datetime
from pathlib import Path

from . import memory

_LOCK = threading.Lock()

# Active learning is scoped at the ORGANIZATION level: each org keeps its own
# corrections + confirmed-image snapshots in its own training folder, so all
# Sites of one org share a single model memory while different orgs stay
# isolated. Set once per run via set_org().
_ACTIVE_ORG = None
_ACTIVE_ROOT: Path | None = None

VALID_MODELS = ("cable", "devices", "ports", "port_type")


class NoTrainingFolder(RuntimeError):
    pass


def set_org(org_id, root=None) -> None:
    """Scope all subsequent store reads/writes to one organization's training folder."""
    global _ACTIVE_ORG, _ACTIVE_ROOT
    _ACTIVE_ORG = str(org_id) if org_id not in (None, "", 0, "0") else None
    _ACTIVE_ROOT = Path(root) if root else None


def _root() -> Path | None:
    return _ACTIVE_ROOT


def _need_root() -> Path:
    r = _root()
    if r is None:
        raise NoTrainingFolder("No training folder was given, so nothing can be saved.")
    return r


def _model_dir(model: str, create: bool = True) -> Path | None:
    if model not in VALID_MODELS:
        raise ValueError(f"unknown model: {model!r}")
    r = _root()
    if r is None:
        return None
    d = r / model
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _corrections_path(model: str) -> Path | None:
    d = _model_dir(model)
    return d / "corrections.json" if d else None


def _verified_ports_path() -> Path | None:
    d = _model_dir("ports")
    return d / "verified_ports.json" if d else None


def load_corrections(model: str) -> dict:
    p = _corrections_path(model)
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_corrections(model: str, corrections: dict) -> None:
    _need_root()
    p = _corrections_path(model)
    p.write_text(json.dumps(corrections, indent=2), encoding="utf-8")


def add_correction(
    model: str,
    image_path: str,
    pred_label: str,
    final_label: str,
    source_name: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Add or update a correction. Returns the stored record."""
    _need_root()
    with _LOCK:
        h = memory.phash(image_path)
        emb = memory.embed(image_path)
        corrections = load_corrections(model)
        rec = {
            "label": final_label,
            "pred": pred_label,
            "embedding": emb,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source": source_name or Path(image_path).name,
        }
        if extra:
            rec.update(extra)
        corrections[h] = rec
        save_corrections(model, corrections)

        # Snapshot for later model training - copy under confirmed_images/
        snap_dir = _model_dir(model) / "confirmed_images"
        snap_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe_label = "".join(c if c.isalnum() or c in "_-" else "_" for c in final_label)
        try:
            shutil.copyfile(image_path, snap_dir / f"{ts}_{safe_label}.jpg")
        except OSError:
            pass

        rec_out = {**rec, "phash": h, "model": model}
        return rec_out


def find_match(model: str, image_path: str) -> dict | None:
    """Look up a previously-corrected label for this image. None if no match."""
    h = memory.phash(image_path)
    emb = memory.embed(image_path)
    corrections = load_corrections(model)
    rec = memory.nearest_correction(h, emb, corrections)
    if rec is None:
        return None
    return {
        "label": rec.get("label"),
        "pred": rec.get("pred"),
        "timestamp": rec.get("timestamp"),
        "source": rec.get("source"),
        "phash": h,
    }


def stats() -> dict:
    """Return correction counts for all models + verified-port count."""
    out = {}
    for m in VALID_MODELS:
        if m == "ports":
            continue
        corrections = load_corrections(m)
        out[m] = {"corrections": len(corrections)}
    v = load_verified_ports()
    out["ports"] = {"verified_layouts": len(v)}
    return out


# ─────────────────────────────────────────────────────────────────────
# Verified port store - HIGHEST PRIORITY, bypasses YOLO on next upload
# ─────────────────────────────────────────────────────────────────────


def load_verified_ports() -> dict:
    p = _verified_ports_path()
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_verified_ports(v: dict) -> None:
    _need_root()
    p = _verified_ports_path()
    p.write_text(json.dumps(v, indent=2), encoding="utf-8")


def add_verified_port_layout(
    image_path: str,
    image_name: str,
    ports: list,
    img_w: int,
    img_h: int,
) -> dict:
    """Persist a user-verified port layout. ports = [{port_number,x,y}, ...]."""
    _need_root()
    with _LOCK:
        h = memory.phash(image_path)
        emb = memory.embed(image_path)
        verified = load_verified_ports()
        clean_ports = [
            {"port_number": int(p["port_number"]), "x": int(p["x"]), "y": int(p["y"])}
            for p in ports
            if int(p.get("port_number", 0)) >= 1
        ]
        verified[h] = {
            "image_name": image_name,
            "ports": clean_ports,
            "img_w": int(img_w),
            "img_h": int(img_h),
            "embedding": emb,
            "verified": True,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        save_verified_ports(verified)
        return {"phash": h, "n_ports": len(clean_ports)}


def find_verified_port_layout(image_path: str) -> dict | None:
    """Look up a saved verified layout for this image. None if none."""
    h = memory.phash(image_path)
    emb = memory.embed(image_path)
    verified = load_verified_ports()
    if not verified:
        return None
    # Fast path: pHash
    best, best_d = None, memory.HAMMING_TOL + 1
    for stored_h, rec in verified.items():
        d = memory.hamming(h, stored_h)
        if d < best_d:
            best, best_d = rec, d
    if best is not None:
        return {**best, "match_type": "phash", "match_distance": best_d}
    # Robust path: embedding
    best, best_s = None, memory.SIM_THRESH
    for rec in verified.values():
        v = rec.get("embedding")
        if not v:
            continue
        s = memory.cos_sim(emb, v)
        if s > best_s:
            best, best_s = rec, s
    if best is None:
        return None
    return {**best, "match_type": "embedding", "match_score": best_s}


# ─────────────────────────────────────────────────────────────────────
# Confirmed-rack alias store - a perceptual cache so a re-uploaded photo of
# a rack the user already CONFIRMED serves the confirmed result instead of
# re-detecting it. Keyed by the rack image's pHash (fast) + ResNet embedding
# (robust to angle/lighting), value is the prior rackId. Rack-level matching
# uses a STRICTER embedding threshold than crop matching so two different
# racks in similar rooms don't collide.
# ─────────────────────────────────────────────────────────────────────

# Near-duplicate only. A false match shows the WRONG rack's data, which is
# worse than just re-detecting, so this is deliberately strict - a re-shoot
# from a very different angle will (safely) fall through to fresh detection
# rather than risk serving another rack.
_RACK_SIM_THRESH = 0.96


def _confirmed_racks_path() -> Path | None:
    r = _root()
    if r is None:
        return None
    d = r / "confirmed_racks"
    d.mkdir(parents=True, exist_ok=True)
    return d / "confirmed_racks.json"


def load_confirmed_racks() -> dict:
    p = _confirmed_racks_path()
    if p is None or not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_confirmed_racks(v: dict) -> None:
    _need_root()
    _confirmed_racks_path().write_text(json.dumps(v, indent=2), encoding="utf-8")


def add_confirmed_rack(image_path: str, rack_id: str, image_name=None) -> dict:
    """Register a rack image's fingerprint → rackId so a future upload that
    perceptually matches serves this confirmed rack instead of re-detecting.
    """
    _need_root()
    with _LOCK:
        h = memory.phash(image_path)
        emb = memory.embed(image_path)
        confirmed = load_confirmed_racks()
        confirmed[h] = {
            "rack_id": rack_id,
            "image_name": image_name or Path(image_path).name,
            "embedding": emb,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        save_confirmed_racks(confirmed)
        return {"phash": h, "rack_id": rack_id}


def find_confirmed_rack(image_path: str) -> dict | None:
    """Look up a confirmed rackId whose image perceptually matches. None if
    no confident match. pHash first (cheap), then a strict embedding pass.
    """
    confirmed = load_confirmed_racks()
    if not confirmed:
        return None
    h = memory.phash(image_path)
    best, best_d = None, memory.HAMMING_TOL + 1
    for stored_h, rec in confirmed.items():
        d = memory.hamming(h, stored_h)
        if d < best_d:
            best, best_d = rec, d
    if best is not None:
        return {"rack_id": best.get("rack_id"), "match_type": "phash", "match_distance": best_d}
    emb = memory.embed(image_path)
    best, best_s = None, _RACK_SIM_THRESH
    for rec in confirmed.values():
        v = rec.get("embedding")
        if not v:
            continue
        s = memory.cos_sim(emb, v)
        if s > best_s:
            best, best_s = rec, s
    if best is None:
        return None
    return {"rack_id": best.get("rack_id"), "match_type": "embedding", "match_score": best_s}
