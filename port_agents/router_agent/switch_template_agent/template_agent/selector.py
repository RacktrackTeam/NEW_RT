"""Choose the correct template among candidate images, by visual features.

Product galleries mix front, back, angled and box shots, other revisions and
lookalike models. Each candidate is downloaded and scored:

  front view (0..1)
    * shape    - the cropped front panel has a switch-panel aspect ratio
    * ports    - the detected port grid matches the expected port count
    * straight - the port rows are level (angled marketing shots slope)
  photo match (when a test photo is given - the strongest signal)
    * homography - SIFT keypoints matched between the candidate's front
      panel and the photo, a RANSAC (MAGSAC) homography fitted (an affine
      fit from the same matches if the homography is implausible), and the
      inliers counted. Implausible warps (flipped, sheared, too small) count
      as no match. The image we must align to decides.

The chosen image is kept in the template store (template_agent.store) and reused
on later runs: stored templates are checked first, and only when none fits is the
internet searched again.
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import requests

from .vision.align import _gray, best_feature_fit
from .vision.catalog import CatalogEntry
from .vision.vendor import TEMPLATE_WIDTH, decode_image, detect_ports, find_face, layout_slope

from urllib.parse import urlparse

from .devices import Device, device as _device
from .sources import find_template_images, model_mention, suggest_models
from .store import TemplateStore, model_numbers

logger = logging.getLogger("template_agent.selector")

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
MIN_INLIERS = 20          # homography inliers that prove a candidate matches the photo
MIN_FRONT_VIEW = 0.6      # without that proof: a clearly front-on panel...
MAX_ROW_SLOPE = 0.03      # ...with level port rows (rejects angled marketing shots)


@dataclass
class TemplateResult:
    path: Path | None                 # saved template image, None if nothing usable
    url: str = ""
    page: str = ""
    source: str = ""                  # e.g. official/sitemap_page, internet/image_search
    front_view: float = 0.0           # 0..1
    photo_inliers: int = 0            # homography inliers against the photo
    details: dict = field(default_factory=dict)
    others: list = field(default_factory=list)   # runners-up with their scores
    verified: bool = True             # False: best-ranked image that passed no visual check
    from_store: bool = False          # True: reused from the template store, no internet used
    template_id: int | None = None    # id in the template store


def clean_name(text: str) -> str:
    """Vendor / model as typed or pasted: drop a trailing description after ':' or
    ' - ' ("CRS328-24P-4S+RM: 24 ports"), quotes and repeated spaces."""
    text = re.split(r"\s*:\s|\s+[-–]\s+", (text or "").strip(), maxsplit=1)[0]
    text = text.strip(" \"'`").rstrip(":")
    return re.sub(r"\s+", " ", text)


def template_filename(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model.strip()).upper()


def _download(url: str) -> np.ndarray | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200 or len(r.content) < 5_000:
            return None
        return decode_image(r.content)
    except requests.RequestException as e:
        logger.info("download %s failed: %s", url, e)
        return None


def _front_face(img: np.ndarray, dev: Device | str | None = None) -> np.ndarray | None:
    x0, y0, x1, y1 = find_face(img, _device(dev).aspect)
    if x1 - x0 < 300 or y1 - y0 < 20:
        return None
    return cv2.resize(img[y0:y1, x0:x1], (TEMPLATE_WIDTH, max(1, round(TEMPLATE_WIDTH * (y1 - y0) / (x1 - x0)))))


def front_view_score(img: np.ndarray, rj45: int = 0, sfp: int = 0,
                     numbering: str = "odd_top", device: Device | str = "switch") -> tuple[float, dict]:
    """0..1: how usable the image is as a straight-on front-panel template."""
    dev = _device(device)
    x0, y0, x1, y1 = find_face(img, dev.aspect)
    w, h = x1 - x0, y1 - y0
    face = _front_face(img, dev)
    if face is None:
        return 0.0, {"reason": "front panel too small", "face_px": [w, h]}
    aspect = w / h
    # switch front panels are ~5-14x wider than tall; 10 is a typical 1U panel
    # (routers: 1U or 2U, see devices.py)
    shape = max(0.0, 1 - abs(math.log(aspect / dev.aspect_target)) / math.log(2.5))
    found = detect_ports(face, CatalogEntry("", "", rj45_ports=rj45, sfp_ports=sfp, numbering=numbering))
    n, exp = len(found), rj45 + sfp
    ports = max(0.0, 1 - abs(n - exp) / exp) if exp else min(1.0, n / dev.typical_ports)
    slope = layout_slope(found)
    straight = max(0.0, 1 - slope / 0.04)
    return 0.25 * shape + 0.45 * ports + 0.30 * straight, {
        "aspect": round(aspect, 2), "ports_found": n, "ports_expected": exp or None,
        "row_slope": round(slope, 4)}


def photo_match(img: np.ndarray, photo: np.ndarray, device: Device | str = "switch") -> int:
    """Homography inliers between the candidate's front panel and the photo (0 = no plausible fit).
    Devices that are not wide rack panels (DIN-rail, desktop) are matched as a whole image."""
    face = _front_face(img, device)
    if face is None:
        h, w = img.shape[:2]
        scale = TEMPLATE_WIDTH / max(w, h)
        face = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))))
    # homography first, affine fit from the same matches if the homography is implausible
    H, inliers = best_feature_fit(face.shape, photo.shape, _gray(face), _gray(photo))
    return inliers if H is not None else 0


def _search_online(vendor: str, model: str, photo, rj45: int, sfp: int, numbering: str,
                   max_candidates: int, use_search: bool,
                   dev: Device | None = None) -> tuple[TemplateResult, np.ndarray | None]:
    """Candidates from the official sites / internet, chosen by visual features."""
    dev = _device(dev)
    cands = find_template_images(vendor, model, limit=max_candidates * 2, use_search=use_search, device=dev)
    scored = []
    for c in cands[:max_candidates]:
        img = _download(c.url)
        if img is None:
            continue
        fv, info = front_view_score(img, rj45, sfp, numbering, dev)
        # the photo decides even when the image is not a rack-style panel
        inl = photo_match(img, photo, dev) if photo is not None else 0
        total = fv + min(inl, 60) / 60 * 0.6 + c.score / 100 * 0.2
        scored.append((total, fv, inl, c, img, info))
        logger.info("candidate %.2f (front view %.2f, inliers %d) %s", total, fv, inl, c.url)

    if not scored:
        details = {"reason": "no candidate images found"}
        if use_search:
            details["did_you_mean"] = suggest_models(vendor, model, noun=dev.noun)   # same numbers: typos
            details["near_models"] = suggest_models(vendor, model, same_numbers=False, noun=dev.noun)
        return TemplateResult(None, details=details), None

    def usable(s) -> bool:
        _, fv, inl, _, _, info = s
        return inl >= MIN_INLIERS or (fv >= MIN_FRONT_VIEW and info.get("row_slope", 1) <= MAX_ROW_SLOPE)

    def other_model(s) -> bool:
        """True if the image is named for a longer model (TL-SG1016D for TL-SG1016)."""
        c = s[3]
        mention = model_mention(urlparse(c.url).path, model)
        if mention == "none":
            mention = model_mention(c.title, model)
        return mention == "variant"

    # usable first; the exact model before a variant that merely looks similar; then score
    scored.sort(key=lambda s: (not usable(s), other_model(s), -s[0]))
    total, fv, inl, c, img, info = scored[0]
    others = [{"url": s[3].url, "front_view": round(s[1], 3), "photo_inliers": s[2]} for s in scored[1:]]
    verified = usable(scored[0])
    if not verified:
        if photo is not None:
            # a photo was given and nothing matches it: no template beats a wrong one
            return TemplateResult(None, details={"reason": "no candidate matches the photo",
                                                 "best_front_view": round(fv, 3)}, others=others), None
        # no photo to check against and no rack-style front view (DIN-rail, desktop and
        # odd-shaped devices): hand back the best-ranked image, clearly marked unverified
        c = max(cands[:max_candidates], key=lambda x: x.score)
        pick = next((x for x in scored if x[3] is c), None) or scored[0]
        total, fv, inl, c, img, info = pick
        info = {**info, "note": "unverified: no visual check passed; best-ranked image returned"}

    res = TemplateResult(None, c.url, c.page, f"{c.tier}/{c.source}", round(fv, 3), inl, info, others,
                         verified=verified)
    return res, img


def _from_store(store: TemplateStore, vendor: str, model: str, photo,
                dev: Device | None = None) -> TemplateResult | None:
    """A stored template for vendor + model: with a photo, the one that matches it by
    homography (None if none does - maybe another hardware revision); without, the best one."""
    cached = store.find(vendor, model)
    if not cached:
        return None
    if photo is None:
        t, inl, matched = cached[0], 0, False
    else:
        best = None
        for t in cached[:8]:
            img = decode_image(t.path.read_bytes())
            inl = photo_match(img, photo, dev or "switch") if img is not None else 0
            if best is None or inl > best[1]:
                best = (t, inl)
        t, inl = best
        if inl < MIN_INLIERS:
            logger.info("%d stored template(s) for %s %s, none matches the photo (best %d inliers)",
                        len(cached), vendor, model, inl)
            return None
        matched = True
    store.mark_used(t.id, photo_matched=matched)
    return TemplateResult(t.path, t.source_url, t.page_url, f"store/{t.source}", t.front_view, inl,
                          {"ports_found": t.ports, "stored_at": time.strftime("%Y-%m-%d", time.localtime(t.created_at))},
                          verified=t.verified, from_store=True, template_id=t.id)


def fit_quality(img: np.ndarray, photo: np.ndarray, device: Device | str = "switch") -> float:
    """How well a template fits the photo, for telling lookalike models apart: the
    homography inliers, minus a penalty for how much the template must be stretched or
    shrunk to cover the switch (a model with a different panel layout fits worse)."""
    face = _front_face(img, device)
    if face is None:
        h, w = img.shape[:2]
        scale = TEMPLATE_WIDTH / max(w, h)
        face = cv2.resize(img, (max(1, round(w * scale)), max(1, round(h * scale))))
    H, inliers = best_feature_fit(face.shape, photo.shape, _gray(face), _gray(photo))
    if H is None:
        return 0.0
    from .vision.align import width_coverage
    return inliers - 40 * abs(math.log(max(1e-3, width_coverage(H, face.shape, photo.shape))))


def _best_near_in_store(st: TemplateStore, vendor: str, model: str, photo,
                        dev: Device | None = None) -> TemplateResult | None:
    """Of the stored near-identical models (numbers may differ), the one whose template
    matches the photo BEST - not the first that passes: lookalike models of one vendor
    can all match a little."""
    best = None
    for name in st.similar_models(vendor, model, same_numbers=False, limit=6):
        hit = _from_store(st, vendor, name, photo, dev)
        if hit is None:
            continue
        q = fit_quality(decode_image(Path(hit.path).read_bytes()), photo, dev or "switch")
        if best is None or q > best[0]:
            best = (q, name, hit)
    if best is None:
        return None
    _, name, hit = best
    hit.details.update(corrected_from=model, corrected_to=name, by_photo=True,
                       note=f"'{model}' was not found; '{name}' matches your photo "
                            f"({hit.photo_inliers} inliers).")
    return hit


def find_template(vendor: str, model: str, photo: np.ndarray | None = None, *,
                  rj45: int = 0, sfp: int = 0, numbering: str = "odd_top",
                  out_dir: Path | str | None = None, max_candidates: int = 16,
                  use_search: bool = True, store: TemplateStore | bool | None = True,
                  refresh: bool = False, device: Device | str = "switch",
                  exact: bool = False) -> TemplateResult:
    """The template for vendor + model.

    1. The template store first: a stored template is reused - with a photo only if
       it matches the photo by homography. No internet is used.
    2. Otherwise the official sites, then the internet; the chosen image is added to
       the store (several templates per model are kept, e.g. hardware revisions).
    A model that could not be found is not searched again for a week (refresh=True
    forces a search, and also skips step 1).

    rj45 / sfp: expected port counts if known (0 = unknown). photo: the switch photo
    the template will be aligned to. store: True = the default store folder, a
    TemplateStore, or False/None to use no store. out_dir: also copy the template there.
    device: 'switch' (default) or 'router' - search words, panel shape and the store
    folder differ (template_agent.devices); everything else is the same.
    exact: never answer with another model's template (no typo / near-model
    corrections) - for building catalogs, where the name is known to be right."""
    dev = _device(device)
    vendor, model = clean_name(vendor), clean_name(model)
    st = TemplateStore(dev.store_dir) if store is True else (store or None)

    if st is not None and not refresh:
        hit = _from_store(st, vendor, model, photo, dev)
        if hit is not None:
            return _copy_out(hit, out_dir, vendor, model)
        # not stored under this exact name: stored models with a close name
        # (a typo or misread of one already in the database)
        # Not stored under this exact name. A stored model with the SAME numbers and a
        # slightly different spelling is this model with a typo ("RS328-24P-4S+RM" for
        # "CRS328-24P-4S+RM"): use it, saying so. Different numbers are a different
        # model (2S vs 4S) and are never used in its place.
        in_db = [] if exact or st.find(vendor, model) else st.similar_models(vendor, model)
        for name in in_db:
            fixed = _from_store(st, vendor, name, photo, dev)   # with a photo it must match it
            if fixed is not None:
                fixed.details.update(corrected_from=model, corrected_to=name,
                                     note=f"'{model}' corrected to '{name}' (same model numbers, "
                                          f"spelling differs).")
                return _copy_out(fixed, out_dir, vendor, name)
        miss = st.recent_miss(vendor, model)
        if miss and photo is not None and not exact:
            # the typed model is known not to exist; still try near models against this photo
            hit = _best_near_in_store(st, vendor, model, photo, dev)
            if hit is not None:
                return _copy_out(hit, out_dir, vendor, hit.details["corrected_to"])
            stored = json.loads(miss["reason"]) if (miss.get("reason") or "").startswith("{") else {}
            for name in stored.get("near_models", []):
                cand, cand_img = _search_online(vendor, name, photo, rj45, sfp, numbering, max_candidates, use_search, dev)
                if cand_img is not None and cand.photo_inliers >= MIN_INLIERS:
                    t = st.add(vendor, name, cv2.imencode(".png", cand_img)[1].tobytes(), source=cand.source,
                               source_url=cand.url, page_url=cand.page, front_view=cand.front_view,
                               ports=cand.details.get("ports_found") or 0, verified=cand.verified,
                               width=cand_img.shape[1], height=cand_img.shape[0])
                    st.mark_used(t.id, photo_matched=True)
                    cand.path, cand.template_id = t.path, t.id
                    cand.details.update(corrected_from=model, corrected_to=name, by_photo=True,
                                        note=f"'{model}' was not found; '{name}' matches your photo "
                                             f"({cand.photo_inliers} inliers).")
                    return _copy_out(cand, out_dir, vendor, name, cand_img)
        if miss and not st.find(vendor, model):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(miss["searched_at"]))
            online = (json.loads(miss["reason"]).get("did_you_mean", [])
                      if (miss.get("reason") or "").startswith("{") else [])
            return TemplateResult(None, details={
                "reason": f"not found online on {when}; it is searched again after "
                          f"7 days, or now with refresh", "remembered_miss": True,
                "did_you_mean": [m for m in online if model_numbers(m) == model_numbers(model)],
                "near_models": list(dict.fromkeys(
                    st.similar_models(vendor, model, same_numbers=False)
                    + (json.loads(miss["reason"]).get("near_models", [])
                       if (miss.get("reason") or "").startswith("{") else [])))},
                from_store=True)

    res, img = _search_online(vendor, model, photo, rj45, sfp, numbering, max_candidates, use_search, dev)
    if img is None and res.details.get("did_you_mean") and not exact:
        # nothing names the model as typed, but the web names one with the same numbers
        # and nearly the same spelling: that is this model with a typo - look it up
        name = res.details["did_you_mean"][0]
        fixed, fixed_img = _search_online(vendor, name, photo, rj45, sfp, numbering, max_candidates, use_search, dev)
        if fixed_img is not None:
            fixed.details.update(corrected_from=model, corrected_to=name,
                                 note=f"'{model}' corrected to '{name}' (same model numbers, spelling differs).")
            res, img, model = fixed, fixed_img, name
    if img is None and photo is not None and not exact:
        # The model as typed exists nowhere. Near-identical models (also with different
        # numbers - the web often knows only CRS326-24G-2S+RM for "-4S") are tried
        # against the test image: one whose template matches the photo by homography
        # is the switch in the photo. Stored ones first (instant), then the web.
        near = res.details.get("near_models") or []
        if st is not None:
            hit = _best_near_in_store(st, vendor, model, photo, dev)
            if hit is not None:
                return _copy_out(hit, out_dir, vendor, hit.details["corrected_to"])
        best = None
        for name in near:   # every near model, keep the best match with the photo
            cand, cand_img = _search_online(vendor, name, photo, rj45, sfp, numbering, max_candidates, use_search, dev)
            if cand_img is not None and cand.photo_inliers >= MIN_INLIERS:
                q = fit_quality(cand_img, photo, dev)
                if best is None or q > best[0]:
                    best = (q, cand, cand_img, name)
        if best is not None:
            _, cand, cand_img, name = best
            cand.details.update(corrected_from=model, corrected_to=name, by_photo=True,
                                note=f"'{model}' was not found; '{name}' matches your photo "
                                     f"({cand.photo_inliers} inliers).")
            res, img, model = cand, cand_img, name
    if img is None:
        # remember only "nothing found online" - not offline-only lookups, and not
        # "found, but none matches this photo" (another photo may match)
        if st is not None and use_search and res.details.get("reason") == "no candidate images found":
            st.add_miss(vendor, model, json.dumps({"reason": res.details["reason"],
                                                   "did_you_mean": res.details.get("did_you_mean", []),
                                                   "near_models": res.details.get("near_models", [])}))
        return res

    if st is not None:
        ok, buf = cv2.imencode(".png", img)
        t = st.add(vendor, model, buf.tobytes(), source=res.source, source_url=res.url, page_url=res.page,
                   front_view=res.front_view, ports=res.details.get("ports_found") or 0, verified=res.verified,
                   width=img.shape[1], height=img.shape[0])
        st.mark_used(t.id, photo_matched=res.photo_inliers >= MIN_INLIERS)
        res.path, res.template_id = t.path, t.id
    return _copy_out(res, out_dir, vendor, model, img)


def _copy_out(res: TemplateResult, out_dir, vendor: str, model: str, img: np.ndarray | None = None):
    """Also write the template to out_dir (older callers expect <MODEL>.png there)."""
    if out_dir is None or (res.path is None and img is None):
        return res
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{template_filename(model)}.png"
    if img is None:
        img = decode_image(res.path.read_bytes())
    cv2.imwrite(str(dest), img)
    dest.with_suffix(".source.json").write_text(json.dumps({
        "vendor": vendor, "model": model, "url": res.url, "page": res.page, "source": res.source,
        "front_view": res.front_view, "photo_inliers": res.photo_inliers, "verified": res.verified,
        "template_id": res.template_id, **res.details, "others": res.others}, indent=1))
    if res.path is None:
        res.path = dest
    return res
