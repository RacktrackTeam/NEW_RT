"""
switch_fit - fit a switch's vendor template to a rack photo and draw the port overlay.

    import switch_fit

    # with a template (folder with front.png + template.json) or a vendor front image
    res = switch_fit.fit("rack_photo.jpg", "templates/d-link_des-1210-28p")
    res = switch_fit.fit(photo_bgr, "vendor_front_view.jpg")       # template built on the fly
    if res["ok"]:
        cv2.imwrite("overlay.png", res["overlay"])
        for p in res["ports"]:
            print(p["id"], p["type"], p["status"], p["polygon"])     # 4 corners in photo pixels

    # no template: the port layout the models find in the photo
    lay = switch_fit.layout("rack_photo.jpg")

Both return plain dicts (JSON-serialisable apart from the overlay image). Nothing here
reads text or goes online: the make and model are the caller's business.
"""
import tempfile
from pathlib import Path

import cv2
import numpy as np

from . import fit as _fit
from . import ports as _ports
from .fit import Template, align, passes, port_agreement  # noqa: F401  (for callers)

__all__ = ["fit", "layout", "build_template", "detect_ports", "Template", "available",
           "align", "passes", "port_agreement"]
__version__ = "1.0"


def available():
    """True when the port models (RT-EOS/Models) and ultralytics are installed. Without
    them fit() still aligns by image features, but has no port check, re-fit or snap,
    and layout() returns nothing."""
    return _ports.available()


def _photo(photo):
    if isinstance(photo, np.ndarray):
        return photo
    img = cv2.imread(str(photo), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"cannot read image {photo}")
    return img


def detect_ports(photo, numbering="column"):
    """The ports in the photo: [{id, type (rj45/sfp/console...), box [x, y, w, h], status
    (connected/empty/unknown/not seen), confidence}], numbered 1 top-left, 2 below it, 3 at
    the top of the next column ("row": the top row first). Empty without the port models."""
    if not _ports.available():
        return []
    return _ports.detect_ports(_photo(photo), numbering)[0]


def build_template(vendor_image, out_dir, vendor="", model="", numbering="column"):
    """A template from a vendor product photo: the front panel is cut out, its ports
    detected and numbered, the RJ45 block marked as not used for matching (cables cover
    it in real photos). Writes out_dir/front.png and out_dir/template.json; returns the
    Template. Needs the port models."""
    if not _ports.available():
        raise RuntimeError("building a template needs the port models (see README)")
    _ports.build_template(_photo(vendor_image), out_dir, vendor, model, numbering=numbering)
    return Template(Path(out_dir))


def _template(template):
    if isinstance(template, Template):
        return template
    p = Path(template) if not isinstance(template, np.ndarray) else None
    if p is not None and (p / "template.json").exists():
        return Template(p)
    # a vendor image: build a template in a temporary folder
    return build_template(template, Path(tempfile.mkdtemp(prefix="switch_fit_")))


def fit(photo, template, photo_ports=None, draw=True, add_missing=True):
    """Fit a template to a photo.

    photo        BGR image (numpy) or a path
    template     a Template, a template folder (front.png + template.json), or a vendor
                 front image (numpy or path; a template is built from it)
    photo_ports  detect_ports() output if you already have it (saves ~0.1-1 s)
    draw         also return the overlay image
    add_missing  add ports the photo shows but the template lacks (console, uplinks)

    Returns {
      "ok":         the fit passed (feature match + port check); if False, don't use it
      "verdict":    "confident" | "check" with "reasons" - how far to trust it
      "confidence", "matched_points", "aligned_by": "features" | "ports",
      "port_check": {on, covered, pairs, detections, err} - shares of template ports landing
                    on detected ports and of detected ports covered (both must be >= 0.8),
      "homography": 3x3 template -> photo,
      "ports":      [{id, type, status, polygon [[x, y] x4] in photo pixels, added}],
      "overlay":    BGR image of the photo with every port boxed (when draw=True),
    }"""
    img = _photo(photo)
    tpl = _template(template)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if photo_ports is None:
        photo_ports = detect_ports(img)
    r = align(tpl, gray, photo_ports=photo_ports or None, port_fallback=True)
    ok = bool(r is not None and passes(r))
    out = {"ok": ok, "template": f"{tpl.meta.get('vendor', '')} {tpl.meta.get('model', '')}".strip()
           or "built from the vendor image"}
    if r is None:
        return dict(out, verdict="check", reasons=["no alignment found"], ports=[], overlay=None)
    # snap the boxes onto the detected ports whenever we have an alignment and detections,
    # not only on a strict pass: with the classical detector (no trained port models) the
    # port check rarely reaches 80%, but the openings it did find still pull each box onto
    # its jack. snap_polygons has its own coverage guard (needs enough anchors) so a thin
    # or wrong detection set leaves the boxes on the feature alignment.
    if photo_ports:
        r = _fit.snap_to_ports(tpl, r, img, ports=photo_ports)
    H = np.asarray(r["H"], np.float64)
    polys, status, anchors = _fit.snap_polygons(tpl, H, photo_ports or None)
    added = _fit.add_photo_ports(tpl, H, img, polys, status, save=False) if (ok and add_missing) else []
    added_ids = {p["id"] for p in added}
    a = r.get("ports")
    reasons = []
    if not ok:
        reasons.append("the template does not line up with the photo")
    elif a is None:
        reasons.append("too few ports detected in the photo to check the fit")
    elif min(a["on"], a["covered"]) < 0.85:
        reasons.append(f"only {min(a['on'], a['covered']):.0%} of the ports agree between template and photo")
    out.update(
        verdict="confident" if not reasons else "check", reasons=reasons,
        confidence=round(float(r["confidence"]), 3), matched_points=int(r["inliers"]),
        aligned_by="ports" if r.get("by_ports") else "features",
        port_check=a, refit_pairs=int(r.get("refined_pairs", 0)), snapped=int(anchors),
        homography=H.tolist(),
        ports=[{"id": p["id"], "type": p["type"], "status": status.get(p["id"]),
                "polygon": [[round(float(x), 1), round(float(y), 1)] for x, y in polys[p["id"]]],
                "added": p["id"] in added_ids}
               for p in tpl.meta["ports"]],
    )
    if draw:
        up = max(1.0, min(3.0, 2400 / img.shape[1]))
        pil = _fit.draw_overlay(tpl, img, H, up, polys)
        out["overlay"] = cv2.cvtColor(np.asarray(pil), cv2.COLOR_RGB2BGR)
    for p in added:                       # the template object is reused by callers: undo
        tpl.meta["ports"].remove(p)
    return out


def layout(photo, numbering="column", draw=True):
    """The port layout found in the photo alone (no template): {"ports": detect_ports(),
    "rows", "columns", "pitch_px", "rj45", "sfp", "connected", "overlay"}. Ports hidden in a
    column are filled in ("status": "not seen")."""
    img = _photo(photo)
    if not _ports.available():
        return {"ports": [], "overlay": None, "reason": "port models not available"}
    ports, info = _ports.detect_ports(img, numbering)
    out = {k: info.get(k) for k in ("rows", "columns", "pitch_px", "rj45", "sfp", "connected")}
    out["ports"] = ports
    if draw:
        up = max(1.0, min(3.0, 2400 / img.shape[1]))
        out["overlay"] = cv2.cvtColor(np.asarray(_fit.draw_auto_overlay(img, ports, up)), cv2.COLOR_RGB2BGR)
    return out
