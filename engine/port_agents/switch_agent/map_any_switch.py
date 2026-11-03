#!/usr/bin/env python3
"""
map_any_switch.py - map the ports of any switch from one front-panel photo.

    python map_any_switch.py photo.jpg

No hand-made template needed. Steps:
  1. Read make and model off the photo with OCR. If it can't, it asks you.
  2. Use a saved template for that model if there is one (templates/).
  3. Otherwise find vendor product images (vendor website, an image-search API if you
     have a key, or a URL/file you give it), build a template from each automatically
     (front face, RJ45 jacks, SFP cages, console port), and keep the one that lines up
     best with your photo. The new template is saved, so the next photo of the same
     model is instant.
  4. Work out the port numbering from numbers printed on the panel in your photo, or ask.
  5. Write the same outputs as map_switch.py (flow.png, stage images, result.json).

Options:
    --make "TP-Link" --model "TL-SG3424"   skip OCR
    --vendor-image URL_OR_FILE              use this vendor image (repeatable)
    --scheme top-odd|top-even|row           port numbering, skip detection
    --no-ask                                never prompt; fail with a message instead
    --out DIR, --templates DIR

Vendor photos come from the vendor's own website (sitemaps + product pages, see
vendor_images.py and vendors.json, 110 vendors). Image search APIs are optional and
only used when the vendor site gives nothing:
    SERPAPI_KEY                     -> SerpAPI Google Images
    GOOGLE_API_KEY + GOOGLE_CSE_ID  -> Google Programmable Search (image search)
"""
import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np

import autotemplate as at
import image_search
import map_switch as ms
HERE = Path(__file__).resolve().parent
TEMPLATES = HERE / "templates"          # saved templates, one folder per model
VENDOR_PHOTOS = HERE / "vendor_photos"  # saved vendor product photos (+ web/ download cache)
WEB_CACHE = VENDOR_PHOTOS / "web"      # downloaded images; a run without --learn uses its own out folder
RUN_TEMP = []                           # folders a run without --learn removes when it ends
# what the run found out about the make and model, written into result.json at the end (the same
# fields as the router and appliance agents: identify_confidence, identified_by, status, error)
RUN = {"out": None, "photo": None, "idconf": None, "given": False, "given_confirmed": None, "model_read": None}
VENDORS_JSON = HERE / "vendors.json"    # vendor websites for the image search
WEB = HERE / "ui"                       # the UI page
OUTPUT = HERE / "output"                # results; created on first use
UI_RUNS = OUTPUT / "ui_runs"            # one folder per UI run
SHOWCASE = HERE / "showcase"            # UI showcase examples
import racktrack as rt
import text_match as tm
import vendor_images as vi

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
MAX_CANDIDATE_IMAGES = 10
# Wall clock for reading the panel (panel_ocr, both stages). The whole run has a 30 s
# budget and catalog matching and alignment take the rest; stage 2 stops early rather
# than overrunning, so a hard photo gets fewer magnified re-reads instead of a slow run.
PANEL_OCR_BUDGET_S = float(os.environ.get("SWITCH_MAPPER_OCR_BUDGET", "6"))

NOT_MODELS = {"JETSTREAM", "CONSOLE", "SWITCH", "MANAGED", "GIGABIT", "SPEED", "LINK", "POWER",
              "SYSTEM", "RESET", "OMADA", "STREAM"}


# ----------------------------------------------------------------------------- helpers

def ask(prompt, args, default=None):
    if args.no_ask or not sys.stdin.isatty():
        return default
    try:
        v = input(prompt).strip()
    except EOFError:
        return default
    return v or default


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def ocr_tokens(img_bgr, target_h=480):
    """OCR over horizontal tiles (switch photos are very wide and the text is tiny).
    Returns [(text, confidence, cx, cy)] in image coordinates. Empty if no OCR engine."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print("  (OCR not available: pip install rapidocr-onnxruntime)")
        return []
    eng = ocr_tokens.engine = getattr(ocr_tokens, "engine", None) or RapidOCR()
    h, w = img_bgr.shape[:2]
    sc = target_h / h if h < target_h else 1.0
    big = cv2.resize(img_bgr, None, fx=sc, fy=sc, interpolation=cv2.INTER_CUBIC) if sc != 1 else img_bgr
    H, W = big.shape[:2]
    tw = int(min(W, H * 3)); step = max(1, int(tw * 0.75))
    out = []
    for x in range(0, max(1, W - tw // 4), step):
        res, _ = eng(big[:, x:x + tw])
        for box, text, conf in res or []:
            b = np.array(box, float)
            out.append((text, float(conf), (x + b[:, 0].mean()) / sc, b[:, 1].mean() / sc))
    return out


def _box(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def model_near_logo(vendor, detections, ocr_model=None):
    """The model printed next to the brand logo. Vendors print the model beside or under
    the logo; text elsewhere in a rack photo is often another device's label (a power
    strip's "CPS-12" next to a Dell PowerConnect 2824). Text around the logo, including
    lines stacked below it, is joined in reading order and matched against the vendor's
    known models with text_match's OCR-confusion-aware similarity.
    Returns (model, score, how, text next to the logo) or None."""
    if not vendor:
        return None
    v = vi.find_vendor(vendor)
    # the anchor is the brand name itself (aliases can be series names like
    # "PowerConnect", which are part of the model text, not the logo)
    names = {tm.squash(a) for a in ([vendor] + ([v["name"]] if v else [])) if len(tm.squash(a)) >= 3}
    dets = [(t, c, _box(p)) for t, c, p in detections if p and t and t.strip()]
    logos = [d for d in dets if any(tm.squash(d[0]) == n or tm.similarity(n, tm.squash(d[0])) >= 0.8 for n in names)]
    if not logos:
        return None
    logo = max(logos, key=lambda d: d[1])
    lx0, ly0, lx1, ly1 = logo[2]
    lh, lw = max(ly1 - ly0, 4), max(lx1 - lx0, 8)
    # the neighbourhood: right of and below the logo, a few logo sizes out
    near = [d for d in dets if d is not logo
            and lx0 - lw <= (d[2][0] + d[2][2]) / 2 <= lx1 + 4 * lw
            and ly0 - 1.5 * lh <= (d[2][1] + d[2][3]) / 2 <= ly1 + 4 * lh]
    near.sort(key=lambda d: (round(d[2][1] / max(lh * 0.6, 1)), d[2][0]))
    cands = []
    for i in range(len(near)):
        for j in range(i + 1, min(i + 3, len(near)) + 1):
            text = " ".join(d[0] for d in near[i:j])
            if re.search(r"\d", text):
                cands.append(text)
    if not cands:
        return None
    best = None
    for m in _make_models(v["name"] if v else vendor):
        ms_ = tm.squash(m)
        for c in cands:
            sc, _ = tm.best_substring_similarity(ms_, tm.squash(c))
            if best is None or sc > best[1] or (sc == best[1] and len(ms_) > len(tm.squash(best[0]))):
                best = (m, sc, c)
    if best and best[1] >= 0.85:
        return best[0], round(best[1], 2), f"catalog match for '{best[2]}' next to the logo", " ".join(cands)
    # not in any catalog: the most model-like text next to the logo
    # mostly letters and digits: OCR noise like '; wot Wog"' is not a model
    # and short, like a model name: legend text such as "Green - Link at 1G" is not
    model_like = [c for c in cands if re.search(r"[A-Za-z]", c) and 4 <= len(tm.squash(c)) <= 16
                  and len(c.split()) <= 2 and not re.search(r"\b(at|link|act|on|off)\b", c, re.I)
                  and len(tm.squash(c)) >= 0.8 * len(c.replace(" ", "").replace("-", ""))]
    if model_like and ocr_model and tm.squash(ocr_model) not in tm.squash(" ".join(cands)):
        c = max(model_like, key=lambda c: len(tm.squash(c)))
        return c, 0.6, f"text '{c}' next to the logo (no catalog match)", " ".join(cands)
    return None


def _text_pieces(detections):
    """Every piece of text read, plus neighbours joined in reading order (a model can be
    split: "Catalyst" + "4948-10GE")."""
    dets = sorted([(t, _box(p)) for t, c, p in detections if p and t and t.strip()],
                  key=lambda d: (round(d[1][1] / 12), d[1][0]))
    pieces = [t for t, _ in dets]
    pieces += [a[0] + " " + b[0] for a, b in zip(dets, dets[1:])]
    return pieces


def catalog_fix(vendor, model, detections):
    """Check switch_ocr's model against the text actually read, and the text against the
    make's catalog (switch_ocr's 6,182 models + text_match.MODELS), with
    text_match's OCR-confusion-aware similarity (5/S, 7/T, 0/O, G/6 ...).
      - a model that is in the catalog and printed in the photo is kept;
      - otherwise a catalog model clearly matching some printed text wins
        (S3900-24745k -> S3900-24T4S-R);
      - otherwise switch_ocr's model stands (its own corrections are usually right:
        ICX 7150-C12P from "O-C12P"; when one is wrong the port check rejects the template).
    Returns (model, note) or None to leave it."""
    if not vendor:
        return None
    pieces = [p for p in _text_pieces(detections) if re.search(r"\d", p) and re.search(r"[A-Za-z]", p)]
    if not pieces:
        return None
    sq = [tm.squash(p) for p in pieces]
    support = max((tm.best_substring_similarity(tm.squash(model), p)[0] for p in sq), default=0) if model else 0
    cats = _make_models(vendor)
    in_cat = bool(model) and any(tm.squash(m) == tm.squash(model) for m in cats)
    if in_cat and support >= 0.9:
        return None
    scored = {}
    for m in cats:
        ms_ = tm.squash(m)
        if len(ms_) < 4:
            continue
        sc = max(tm.best_substring_similarity(ms_, p)[0] for p in sq)
        scored[m] = sc
    ranked = sorted(scored.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    if ranked and ranked[0][1] >= 0.84:
        top, sc = ranked[0]
        # a sibling within noise (-24T4S-R vs -24F4S-R) is only a tie if not a substring of the winner
        rival = next((kv for kv in ranked[1:] if tm.squash(kv[0]) not in tm.squash(top)), None)
        if not rival or sc - rival[1] >= 0.04:
            if model and tm.squash(top) == tm.squash(model):
                return None
            return top, (f"switch_ocr gave '{model or 'nothing'}'; the photo's text matches the catalog model "
                         f"'{top}' (similarity {sc:.2f}), using that")
    return None


def identify(photo):
    """Make and model from the photo. Uses RackTrack's switch_ocr when available (better
    vendor/model knowledge), else plain OCR. Returns (vendor, model, tokens, engine);
    tokens are (text, confidence, cx, cy) for reading printed port numbers later."""
    r = rt.identify(photo)
    identify.brand_text = 1.0              # how well the brand's name was read; 0 = not on the photo
    identify.extra_tokens = []             # text of the zoomed logo re-read, when it was needed
    if r is not None:
        identify.brand_text = r.get("brand_text", 1.0)
        tokens = [(t, c, sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
                  for t, c, poly in r["detections"] if poly]
        model = r["model"]
        near = model_near_logo(r["brand"], r["detections"], model)
        # switch_ocr can pick another device's label (CPS-12 for a PowerConnect 2824) or
        # "correct" a reading to a sibling model (DES-1210-28P -> DGS-1210-28P). The text
        # beside the logo decides: keep switch_ocr's model when it is printed there,
        # take the near-logo one when that is printed there and switch_ocr's is not,
        # or when the two are different models altogether
        import switch_identify as si
        override = False
        if near:
            printed = si.squash(near[3])
            ocr_there = bool(model) and si.squash(model) in printed
            near_there = si.squash(near[0]) in printed
            override = (not model or (near_there and not ocr_there)
                        or (not ocr_there and si.similarity(si.squash(near[0]), si.squash(model)) < 0.75))
            # a catalog model printed anywhere on the panel is not replaced by near-logo text that
            # matches no catalog model: on a CSS610-8G-2S+IN that text is '12-57V POWER', on a
            # JGS524PE the LED legend '-10/100M 10/'
            printed_anywhere = bool(model) and any(si.squash(model) in si.squash(t)
                                                   for t, c, poly in r["detections"] if t)
            if override and model and near[1] < 0.85 and model_known(r["brand"], model) and printed_anywhere:
                override = False
        if override:
            identify.last_note = (f"switch_ocr picked '{model or 'nothing'}', but the logo is next to "
                                  f"'{near[0]}' ({near[2]}, similarity {near[1]}); using that")
            return r["brand"], near[0], tokens, "switch_ocr"
        identify.last_note = ""
        # a misread snapped to the catalog model the photo's text matches (TL-563424 -> TL-SG3424)
        fixed = catalog_fix(r["brand"], model, r["detections"])
        if fixed and fixed[0] != model:
            identify.last_note = fixed[1]
            model = fixed[0]
        if not model_known(r["brand"], model):
            # switch_ocr reads the panel in one pass and misses a model printed far
            # smaller than the logo (DGS-1024C under the D-Link logo), then the biggest
            # text with a digit wins - a site sticker ("KOMMYTATOP 2"). panel_ocr re-reads
            # the area around the logo at several magnifications; a catalog model of the
            # same make found there is the model
            try:
                import panel_ocr
                doc2 = panel_ocr.read_panel(photo, budget_s=PANEL_OCR_BUDGET_S)
                identify.extra_tokens = panel_ocr.tokens(doc2)
                v2, m2, _ = identify_catalog(doc2)
            except Exception:
                v2 = m2 = None
            same_make = not r["brand"] or not v2 or vi.norm(v2) == vi.norm(r["brand"])
            if m2 and same_make and model_known(v2 or r["brand"], m2):
                identify.last_note = (f"switch_ocr read '{model or 'nothing'}'; the logo area, re-read "
                                      f"zoomed, shows the catalog model '{m2}'; using that")
                return v2 or r["brand"], m2, tokens, "switch_ocr + logo re-read"
        if not model:
            # switch_ocr only accepts readings that fit a known model pattern. Take the raw
            # readings from both OCR passes and prefer one that is a look-alike of a saved
            # model (known_model fixes e.g. TL-5G3434 -> TL-SG3424)
            cands = [m for m in (identify_from_text(tokens)[1], identify_from_text(ocr_tokens(photo))[1]) if m]
            model = next((m for m in cands if known_model(r["brand"], m, HERE / "templates")), None) or \
                (cands[0] if cands else None)
        return r["brand"], model, tokens, "switch_ocr"
    # Two-stage read: the whole frame in both polarities, then the area around the brand
    # name re-read at several magnifications. The model number is printed far smaller
    # than the logo and a single full-frame pass usually loses it - that is what made a
    # TL-SG2428P come back as "TL-602476P" and match the shorter TL-SG1024.
    import panel_ocr
    doc = panel_ocr.read_panel(photo, budget_s=PANEL_OCR_BUDGET_S)
    tokens = panel_ocr.tokens(doc)
    v, m, unconfirmed = identify_catalog(doc)
    if v and m:
        identify.last_note = ("raw OCR guess: no catalog or known-vendor match, "
                              "not yet confirmed by a web hit" if unconfirmed else "")
        return v, m, tokens, "rapidocr 2-stage + catalog"
    v2, m2 = identify_from_text(tokens)
    identify.last_note = ""
    return v or v2, m or m2, tokens, "rapidocr 2-stage"

SWITCH_EXCEL = HERE / "switch_catalog" / "Switch_Vendors_and_Models.xlsx"   # 6,955 models, 117 vendors


def load_catalog_once():
    """Load the model catalog (Switch_Vendors_and_Models.xlsx) into switch_identify, once per process."""
    import switch_identify as si
    if not getattr(load_catalog_once, "ready", False):
        if SWITCH_EXCEL.exists():
            si.load_catalog(SWITCH_EXCEL)
        else:
            print(f"[warn] switch catalogue not found: {SWITCH_EXCEL} - only the built-in model lists are used",
                  file=sys.stderr)
        load_catalog_once.ready = True
    return si


def doc_from_tokens(tokens):
    """Plain (text, conf, cx, cy) tokens as a document, for callers with no magnified
    re-readings of their own."""
    return {"detections": [{"text": t, "conf": c,
                            "box": [[x - 1, y - 1], [x + 1, y - 1], [x + 1, y + 1], [x - 1, y + 1]]}
                           for t, c, x, y in tokens],
            "roi_detections": []}


def identify_catalog(doc):
    """Vendor and model from a read panel via switch_identify's catalog matching: the
    confusion-aware edit distance (5/S, 0/O, 1/I on tiny silkscreen), vendor-gated model
    search and faceplate-text corroboration, over the switch catalogue (Switch_Vendors_and_Models.xlsx).

    `doc` is panel_ocr.read_panel's output - the text read off the whole frame plus the
    magnified re-readings of the logo area, which is where the model number usually
    becomes legible. A list of tokens is also accepted."""
    si = load_catalog_once()
    if isinstance(doc, list):
        doc = doc_from_tokens(doc)
    try:
        res = si.identify(doc)
    except Exception:
        return None, None, False
    identify_catalog.last = res
    unconfirmed = bool(res.get("vendor_unconfirmed") or res.get("model_unconfirmed"))
    vendor, model = res.get("vendor"), res.get("model")
    # only return a model that is actually a known/exact one: a confident catalog match
    # (score >= 0.7) or a model already in the known-models list. A raw OCR guess that
    # matches nothing ("KNISHE20OMRC" for an Araknis) is dropped - the name is not shown
    # or searched for, and the run uses the vendor alone (visual match / auto mode).
    if model and (res.get("model_score", 0) >= 0.7 or model_known(vendor, model)):
        return vendor, model, False
    return vendor, None, unconfirmed


identify_catalog.last = None

def spec_ports(meta):
    """Port counts of a template, in the spec agent's terms."""
    return {"rj45": sum(p["type"] == "rj45" for p in meta["ports"]),
            "sfp": sum(p["type"] == "sfp" for p in meta["ports"])}


def matches_spec(meta, expected):
    """True/False if the template's port counts equal the expected ones, None if nothing
    is expected. Only the kinds given are checked: a count taken from the photo itself
    carries RJ45 only (SFP cages are detected less reliably)."""
    if not expected or expected.get("total_only"):
        return None
    got = spec_ports(meta)
    ok = True
    if "rj45" in expected:
        ok &= got["rj45"] == expected["rj45"]
    if any(k in expected for k in ("sfp", "qsfp")):
        ok &= got["sfp"] == sum(v for k, v in expected.items() if k in ("sfp", "qsfp"))
    return ok


def ports_from_photo(photo):
    """RJ45 count of the switch in the photo, from the port models (they see cabled
    ports too), used as the expected layout when there is no spec. None when too few
    ports are found to trust the count."""
    import yolo_ports
    if not yolo_ports.available():
        return None
    _, info = yolo_ports.detect_ports(photo)
    return {"rj45": info["rj45"]} if info.get("rj45", 0) >= 8 else None


identify.last_note = ""


def identify_from_text(tokens):
    vendor, model = None, None
    words = [t[0] for t in tokens if t[1] > 0.5]
    for wd in words:
        v = vi.find_vendor(wd)
        if v and not vendor:
            vendor = v["name"]
    cands = []
    for wd in words:
        for m in re.findall(r"[A-Z]{1,6}-?[A-Z0-9]*\d[A-Z0-9]*(?:-[A-Z0-9]+)*", wd.upper()):
            if len(m) >= 5 and re.search(r"[A-Z]", m) and re.search(r"\d", m) and m not in NOT_MODELS \
                    and not re.fullmatch(r"\d+[A-Z]?", m):
                cands.append(m)
    if cands:
        model = max(cands, key=len)
        if not vendor:
            v = vi.vendor_from_model(model)
            vendor = v["name"] if v else None
    return vendor, model


# ----------------------------------------------------------------------------- OCR look-alikes

# OCR on small panel text confuses these; "TL-SG3424" often comes back as "TL-5G3434"
LOOKALIKE = str.maketrans({"5": "S", "0": "O", "1": "I", "8": "B", "6": "G", "|": "I"})
SWAPS = {"5": "S", "S": "5", "0": "O", "O": "0", "1": "I", "I": "1", "8": "B", "B": "8", "6": "G", "G": "6"}


def _canon(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper()).translate(LOOKALIKE)


def _lev(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def known_model(vendor, model, tdir):
    """Saved template whose model is the same as an OCR reading, allowing look-alike
    characters and one other wrong character (two for long names).
    Returns (vendor, model, differences) or None."""
    best = None
    for d in sorted(tdir.iterdir()) if tdir.is_dir() else []:
        if not (d / "template.json").exists():
            continue
        m = json.loads((d / "template.json").read_text())
        if vendor and vi.norm(m["vendor"]) != vi.norm(vendor):
            continue
        dist = _lev(_canon(model), _canon(m["model"]))
        if best is None or dist < best[2]:
            best = (m["vendor"], m["model"], dist)
    allowed = 1 if len(_canon(model)) < 10 else 2
    return best if best and best[2] <= allowed else None


def model_variants(model, limit=4):
    """The OCR reading first, then single look-alike swaps (TL-5G3434 -> TL-SG3434)."""
    out = [model]
    for i, c in enumerate(model):
        if c in SWAPS:
            v = model[:i] + SWAPS[c] + model[i + 1:]
            if v not in out:
                out.append(v)
    return out[:limit]


_LOOKUPS = {}          # (vendor, model, lookalikes) -> result, for the life of the process
IMAGE_SOURCES = {}     # image URL or file -> which search found it (for reports)
VENDOR_PHOTOS = VENDOR_PHOTOS
# Internet image search (Bing, read directly) can be switched off: it works without a
# key but is unofficial and can be throttled. Official vendor sites are always used.
WEB_SEARCH = os.environ.get("SWITCH_MAPPER_WEB_SEARCH", "1") != "0"

TEMPLATE_AGENT_DIR = HERE / "switch_template_agent"
# switch_template_agent is the first vendor-image source: its store answers offline in
# about a second, and on a miss it searches the vendor's official site + the image
# engines itself and picks the hardware revision by matching each candidate to the photo.
# The online half has no timeout of its own, so it is bounded here; on an overrun the
# pipeline falls through to the direct web search below. 0 waits forever.
AGENT_BUDGET_S = float(os.environ.get("SWITCH_MAPPER_AGENT_BUDGET", "45"))


def _local_photos(model):
    """Vendor photos already saved in vendor_photos/ for this exact model."""
    key = re.sub(r"[^A-Z0-9]", "", model.upper())
    out = []
    for p in sorted(VENDOR_PHOTOS.glob("*")) if VENDOR_PHOTOS.is_dir() else []:
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and re.sub(r"[^A-Z0-9]", "", p.stem.upper()) == key:
            out.append(str(p))
            IMAGE_SOURCES[str(p)] = "saved vendor photo"
    return out


def _search_ours(vendor, model, log, budget_s):
    found, _ = vi.find_images(vendor, model, log=log, budget_s=budget_s)
    for u in found:
        IMAGE_SOURCES.setdefault(u, "vendor website")
    return found


def _search_bing_front(vendor, model):
    """image_search.search_reference_image: Bing image search, results
    must name the model, keeps the best wide front-panel shot. On 24 test models it gave
    a usable template for 20, including vendors whose sites block scripts."""
    if not WEB_SEARCH:
        return []
    hit = image_search.search_reference_image(vendor, model)
    if not hit:
        return []
    path = WEB_CACHE / (re.sub(r"[^A-Za-z0-9._-]+", "_", f"{vendor}_{model}")[:100] + ".jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(hit["bytes"])
    IMAGE_SOURCES[str(path)] = f"image search (front view): {hit.get('url', '')}"
    return [str(path)]


_AGENT_PICKS = {}      # (vendor, model, photo) -> the agent's answer, once per run


def _template_agent(vendor, model, photo=None, expected=None):
    """The agent's answer (_ask_template_agent), asked once per vendor, model and photo:
    the pipeline asks before the saved-template check and again for the vendor images."""
    k = (vendor.lower(), model.upper(), id(photo))
    if k not in _AGENT_PICKS:
        _AGENT_PICKS[k] = _ask_template_agent(vendor, model, photo, expected)
        for p in _AGENT_PICKS[k]:
            _agent_face(p)                  # the agent's front face, so the builder cuts the same
    return list(_AGENT_PICKS[k])


def _same_image(image_source, picks):
    """Was a saved template built from one of these images? By file name, so templates
    built on another machine (other folder) still count."""
    name = lambda s: re.split(r"[\\/]", str(s or ""))[-1]
    return bool(image_source) and name(image_source) in {name(p) for p in picks}


AGENT_FACES = {}       # template agent image -> its front face (x, y, w, h), used to build the template


def _agent_face(path, img=None):
    """Remember the agent's front face for an image (the template builder cuts the same)."""
    try:
        from template_agent.vision.vendor import find_face
        img = cv2.imread(str(path), cv2.IMREAD_COLOR) if img is None else img
        x0, y0, x1, y1 = find_face(img)
        if (x1 - x0) > 4 * (y1 - y0) > 0:
            AGENT_FACES[str(path)] = (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
    except Exception:
        pass


def _ask_template_agent(vendor, model, photo=None, expected=None):
    """Vendor image from switch_template_agent (the first choice): its template store
    first (offline), then the vendor's official site + image search, picking the hardware
    revision by matching each candidate to THIS photo with SIFT homography. The online
    search is bounded by AGENT_BUDGET_S; on an overrun this returns [] and the caller
    falls through to the direct web search. Returns [image path] or []."""
    if not _agent_on_path():
        return []

    # the store first, offline: a stored image for this model is offered even when it does
    # not homography-match the photo, because our evaluate_sources does its own alignment
    # and may accept it - cheaper than letting the agent go online to look for a better one
    try:
        from template_agent import TemplateStore
        from template_agent.selector import _from_store
        st = TemplateStore()
        res = _from_store(st, vendor, model, photo) or _from_store(st, vendor, model, None)
        if res is not None and getattr(res, "path", None):
            print(f"  template agent: {Path(res.path).name} from store"
                  + (f", {res.photo_inliers} inliers against the photo" if res.photo_inliers else ""))
            IMAGE_SOURCES.setdefault(str(res.path), "template agent (store)")
            return [_pick_revision(vendor, model, str(res.path), photo)]
    except Exception as exc:
        print(f"  template agent store failed ({exc})")

    if not WEB_SEARCH:
        return []
    import threading
    out = {}

    def work():
        try:
            from template_agent import find_template
            out["res"] = find_template(vendor, model, photo,
                                       rj45=(expected or {}).get("rj45", 0) or 0,
                                       sfp=(expected or {}).get("sfp", 0) or 0,
                                       use_search=WEB_SEARCH)
        except Exception as exc:
            out["err"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(AGENT_BUDGET_S if AGENT_BUDGET_S > 0 else None)
    if t.is_alive():
        print(f"  template agent: slower than {AGENT_BUDGET_S:.0f}s, trying the direct web search")
        return []
    if "err" in out:
        print(f"  template agent failed ({out['err']})")
        return []
    res = out.get("res")
    if res is None or not getattr(res, "path", None):
        reason = (res.details.get("reason") if res is not None else None) or "nothing usable"
        print(f"  template agent: no template ({reason}); trying the direct web search")
        return []
    where = "store" if res.from_store else (res.source or "web")
    print(f"  template agent: {Path(res.path).name} from {where}"
          + (f", {res.photo_inliers} inliers against the photo" if res.photo_inliers else ""))
    IMAGE_SOURCES.setdefault(str(res.path), f"template agent ({where})")
    return [_pick_revision(vendor, model, str(res.path), photo)]


def _agent_on_path():
    """Put switch_template_agent on the import path (the template agent from
    switch-mapper: store -> official sites -> internet, with vision/ for its own panel
    and port code). False when it is not there."""
    if not TEMPLATE_AGENT_DIR.is_dir():
        return False
    # appended, not inserted: racktrack.py puts the RT-EOS repo at the front of sys.path
    # for its own switch_ocr, which must keep winning there
    if str(TEMPLATE_AGENT_DIR) not in sys.path:
        sys.path.append(str(TEMPLATE_AGENT_DIR))
    return True


_LAYOUTS = {}          # image path -> RJ45 blocks of the template built from it


def image_layout(path, vendor="", model=""):
    """RJ45 blocks (map_switch.template_layout) of the template an image gives, e.g.
    [4, 4, 4]; [] when no template can be built from it."""
    path = str(path)
    if path not in _LAYOUTS:
        _LAYOUTS[path] = []
        img = cv2.imread(path, cv2.IMREAD_COLOR)
        if img is not None:
            work = Path(tempfile.mkdtemp(prefix="switchlayout_"))
            try:
                _agent_face(path, img)
                build_template(img, work, vendor, model, source=path, face_box=AGENT_FACES.get(path))
                _LAYOUTS[path] = ms.template_layout(json.loads((work / "template.json").read_text()))
            except Exception:
                pass
            shutil.rmtree(work, ignore_errors=True)
    return _LAYOUTS[path]


def _pick_revision(vendor, model, pick, photo):
    """The agent's pick, or - when the photo's port layout rules it out (another hardware
    revision of the same model, e.g. DGS-1024C as 3 blocks of 4 or 2 blocks of 6) - a
    stored template of this model whose layout fits the photo."""
    if photo is None:
        return pick
    ph = ms.photo_layout(cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY))
    ok, text = ms.compare_layouts(ph, image_layout(pick, vendor, model))
    if ok is not False:
        return pick
    try:
        from template_agent import TemplateStore
        others = [str(t.path) for t in TemplateStore().find(vendor, model)
                  if t.status != "rejected" and str(t.path) != pick and Path(t.path).exists()]
    except Exception:
        others = []
    verdicts = [(ms.compare_layouts(ph, image_layout(o, vendor, model))[0], o) for o in others]
    for want in (True, None):
        for v, o in verdicts:
            if v is want:
                print(f"  agent's pick is another revision ({text}); using the stored {Path(o).name}")
                IMAGE_SOURCES[o] = f"template agent (store): the first pick is another hardware revision ({text})"
                return o
    return pick


AGENT_ONLINE = {}      # local copy of an agent candidate -> (url, image bytes, front view), for the store


def more_agent_images(vendor, model, expected=None, exclude=(), online=True, limit=8, photo=None):
    """When the agent's first template does not line up (another hardware revision, a
    different photo angle), the agent's other images: every template it has stored for
    the model, then its online candidates (official sites, then the internet), downloaded
    and kept when they look like a straight front panel, best front view first. Angled
    shop shots are straightened (rectify.py). With the photo, its port layout filters the
    candidates first: images of another hardware revision are dropped before fitting.
    Returns local paths."""
    if not _agent_on_path():
        return []
    out = []
    try:
        from template_agent import TemplateStore, find_template_images, front_view_score
        from template_agent.selector import _download
        for s in TemplateStore().find(vendor, model):
            p = str(s.path)
            if p not in exclude and s.status != "rejected" and Path(p).exists():
                out.append(p)
                IMAGE_SOURCES[p] = f"template agent (template store, {s.source})"
                _agent_face(p)
        if not online or not WEB_SEARCH:
            return out
        exp = expected or {}
        ph = ms.photo_layout(cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)) if photo is not None else None
        # the agent searches the internet only when the official sites give nothing; an
        # older hardware revision is often only on shop sites, so with the photo's layout
        # known both tiers are searched
        cands = find_template_images(vendor, model, limit=100 if ph else 24, use_search=True,
                                     min_official=10 ** 9 if ph else 1)
    except Exception as e:
        print(f"  template agent candidates failed: {e}")
        return out
    from concurrent.futures import ThreadPoolExecutor
    import hashlib
    import rectify
    urls = [u for u in dict.fromkeys(c.url for c in cands) if u not in exclude]
    with ThreadPoolExecutor(8) as ex:
        imgs = list(ex.map(_download, urls))
    seen, scored = set(), []
    for url, img in zip(urls, imgs):
        if img is None or min(img.shape[:2]) < 100:
            continue
        try:
            fv, _ = front_view_score(img, exp.get("rj45", 0), exp.get("sfp", 0))
            # angled shop photos (often the only pictures of an older hardware revision):
            # the straightened panel is used when it is the better front view
            straight = rectify.rectify_front(img)
            if straight is not None:
                fv2, _ = front_view_score(straight, exp.get("rj45", 0), exp.get("sfp", 0))
                if fv2 > fv:
                    img, fv, url = straight, fv2, url + "#straightened"
        except Exception:
            continue
        ok, buf = cv2.imencode(".png", img)
        sha = hashlib.sha256(buf.tobytes()).hexdigest()
        if not ok or sha in seen:
            continue
        seen.add(sha)
        if fv >= 0.4:
            scored.append((fv, url, img, buf.tobytes(), sha))
    scored.sort(key=lambda t: -t[0])
    folder = WEB_CACHE / "agent"
    folder.mkdir(parents=True, exist_ok=True)
    kept = []
    for fv, url, img, data, sha in scored:
        p = folder / f"{slug(vendor)}_{slug(model)}_{sha[:12]}.png"
        if not p.exists():
            p.write_bytes(data)
        verdict = ms.compare_layouts(ph, image_layout(p, vendor, model))[0] if ph else None
        if verdict is not False:                    # another hardware revision: not fitted
            kept.append((verdict is not True, fv, url, img, data, p))
    dropped = len(scored) - len(kept)
    if dropped:
        print(f"  {dropped} agent candidates show another port layout (hardware revision); left out")
    kept.sort(key=lambda t: (t[0], -t[1]))          # layout confirmed first, then front view
    for _, fv, url, img, data, p in kept[:limit]:
        out.append(str(p))
        IMAGE_SOURCES[str(p)] = (f"template agent (online candidate{', straightened from an angled shot' if url.endswith('#straightened') else ''}, "
                                 f"front view {fv:.2f}): {url.removesuffix('#straightened')}")
        AGENT_ONLINE[str(p)] = (url, data, fv)
        _agent_face(p, img)
    return out


def remember_template(src, vendor, model, r=None):
    """An online agent candidate that lined up with the photo goes into the agent's
    template store, so the next photo of this model (this hardware revision) finds it
    without searching. Only when the ports confirmed the fit (the port models, or the
    open jacks in the photo): a point count alone has let wrong images through."""
    if src not in AGENT_ONLINE or not r or not (r.get("ports_ok") or r.get("open_ok")):
        return
    if not in_catalog(vendor, model):
        # a garbled read ('TL-602420P', 'SWTCH24') must not become a stored template
        print(f"  not added to the template store: {vendor} {model} is not in the catalogue")
        return
    url, data, fv = AGENT_ONLINE[src]
    try:
        from template_agent import TemplateStore
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        blocks = image_layout(src, vendor, model)
        TemplateStore().add(vendor, model, data, ext=".png", source="internet/image_search", source_url=url,
                            front_view=fv, note="chosen by port_overlay: lined up with a site photo"
                                                 + (f"; port layout {' + '.join(map(str, blocks))} columns"
                                                    if blocks else ""),
                            width=img.shape[1], height=img.shape[0])
        print(f"  added to the template agent's store: {url}")
    except Exception as e:
        print(f"  could not add to the template store: {e}")


def _search_agent(vendor, model):
    """RackTrack's spec agent with our image search (agent_images.py): official
    product pages from the agent's vendor scrapers and datasheet links, vendor web shops,
    then image search when those give nothing."""
    import agent_images
    out = []
    for c in agent_images.agent_product_images(vendor, model, use_search=WEB_SEARCH, limit=10):
        out.append(c.url)
        IMAGE_SOURCES.setdefault(c.url, "spec agent: " + ("official page" if c.source == "official_page" else "image search"))
    return out


def _gather(vendor, model, log, budget_s):
    """All sources at once (the slowest one sets the wait), merged so every source gets
    a place near the top: saved photos, the front-view search hit, then ours and the
    spec agent's results alternating."""
    from concurrent.futures import ThreadPoolExecutor
    local = _local_photos(model)
    with ThreadPoolExecutor(3) as ex:
        jobs = {"ours": ex.submit(_search_ours, vendor, model, log, budget_s),
                "front": ex.submit(_search_bing_front, vendor, model),
                "agent": ex.submit(_search_agent, vendor, model)}
        got = {}
        for k, fut in jobs.items():
            try:
                got[k] = fut.result(timeout=budget_s + 30)
            except Exception as e:
                log(f"  {k} image search failed: {e}")
                got[k] = []
    merged = list(local) + got["front"]
    a, b = got["ours"], got["agent"]
    for i in range(max(len(a), len(b))):
        merged += [u for u in (a[i:i + 1] + b[i:i + 1]) if u not in merged]
    counts = {"saved": len(local), "front-view search": len(got["front"]),
              "vendor website": len(a), "spec agent": len(b)}
    where = ", ".join(f"{n} from {k}" for k, n in counts.items() if n) or "nowhere"
    return merged, where


def catalog_name(vendor, model):
    """The make's catalogue model whose name ends with the reading (a prefix like TL- or DCS- left
    off the label or the OCR), when exactly one does; else None."""
    if not (vendor and model):
        return None
    r = tm.squash(model)
    if len(r) < 5:
        return None
    hits = {m for m in _make_models(vendor) if tm.squash(m).endswith(r) and tm.squash(m) != r}
    return hits.pop() if len(hits) == 1 else None


def _make_models(vendor):
    """Known model names for a make, from the catalog (Switch_Vendors_and_Models.xlsx).

    This feeds model_near_logo(), catalog_fix() and shortlist_by_layout(). It used to
    read RT-EOS's own vendors.json plus the small built-in text_match.MODELS, which on a
    machine without RT-EOS meant it saw 39 Cisco models out of the catalog's 541 - the
    fallback ladder was searching 7 % of what the main path searches. It now reads the
    same catalog, matching the make against its name and its aliases."""
    si = load_catalog_once()
    names = []
    for k, ms_ in si.MODELS.items():
        if vi.norm(k.split("/")[0]) == vi.norm(vendor) or \
                any(vi.norm(a) == vi.norm(vendor) for a in si.VENDOR_ALIASES.get(k, [])):
            names += ms_
    # real model names start with a letter, or carry a letter pair after digits (Arista 7050SX3-48YC8);
    # list fragments like "24P-4G" do neither
    return [m for m in dict.fromkeys(names) if (re.match(r"[A-Za-z]", m) or re.search(r"[A-Za-z]{2}", m))
            and re.search(r"\d", m) and len(m) >= 5]


def in_catalog(vendor, model):
    """Is this model in the make's catalogue itself (Switch_Vendors_and_Models.xlsx)? Unlike model_known()
    a saved template's look-alike does not count: only catalogue models are saved as templates."""
    return bool(vendor and model) and any(_canon(m) == _canon(model) for m in _make_models(vendor))


def model_known(vendor, model):
    """Is this an actual known model of the vendor (catalog/known-models list or a saved
    template look-alike)? Used to drop OCR-garbage model names that match nothing."""
    if not (vendor and model):
        return False
    canon = _canon(model)
    if any(_canon(m) == canon for m in _make_models(vendor)):
        return True
    return known_model(vendor, model, TEMPLATES) is not None


def _model_port_total(model):
    """Total ports a model name implies (CRS328-24P-4S = 28, TL-SG2428P = 28), or None."""
    spec = rt.ports_from_model_name(model)
    if spec:
        return spec.get("rj45", 0) + spec.get("sfp", 0)
    spec = rt.ports_from_model_digits(model)
    return spec["rj45"] if spec else None


def port_count_fix(vendor, model, rj45, sfp, min_similarity=0.65):
    """A blurred reading's port count disagrees with the ports found in the photo: look
    for the catalog model that both looks like the reading and has the photo's port count.

    TL-SG2428P read off a blurred panel as TL-602420P implies 20 ports; the port models
    find 24 RJ45 + 4 SFP = 28. Of the make's catalog models with 28 ports, TL-SG2428P is
    the closest look-alike of TL-602420P (G/6, S/0, 8/0 are glyph swaps), so it is used.
    A reading that is itself a catalog model is trusted and left alone, so a port
    detector miscount cannot talk a correct reading into another model.
    Returns (model, note, alternatives) or None."""
    if not (vendor and model) or rj45 + sfp < 4:
        return None
    seen = {rj45, rj45 + sfp}
    implied = _model_port_total(model)
    if implied is None or implied in seen:
        return None
    cats = _make_models(vendor)
    if any(_canon(m) == _canon(model) for m in cats):
        return None
    read = tm.squash(model)
    scored = sorted(((tm.similarity(read, tm.squash(m)), m) for m in cats
                     if _model_port_total(m) in seen), key=lambda sm: (-sm[0], len(sm[1])))
    scored = [sm for sm in scored if sm[0] >= min_similarity]
    if not scored:
        return None
    top_sc, top = scored[0]
    # equally good look-alikes (TL-SG2428P / TL-SL2428P) cannot be told apart by text:
    # a saved template of one of them decides, else the first is used and the others named
    ties = [m for sc, m in scored if top_sc - sc < 0.04]
    saved = [m for m in ties if known_model(vendor, m, TEMPLATES) and
             _canon(known_model(vendor, m, TEMPLATES)[1]) == _canon(m)]
    if saved:
        top = saved[0]
    others = [m for m in ties if m != top]
    note = (f"read '{model}' ({implied} ports), but the photo shows {rj45} RJ45 + {sfp} SFP; "
            f"the look-alike {top} has {_model_port_total(top)} ports, using that"
            + (f" (also possible: {', '.join(others)})" if others else ""))
    return top, note, others


def shortlist_by_layout(vendor, rj45, sfp, k=4):
    """That make's models whose names fit the port layout counted in the photo:
    exact split spelled out (CRS328-24P-4S -> 24 + 4) first, then names carrying the
    RJ45 or total port count (GS724T, DGS-1210-28P), shortest first."""
    exact, close, partial = [], [], []
    for m in _make_models(vendor):
        spec = rt.ports_from_model_name(m)
        if spec and spec.get("rj45") == rj45 and spec.get("sfp") == sfp:
            exact.append(m)
        elif spec and ((spec.get("sfp") == sfp and abs(spec.get("rj45", 0) - rj45) <= 2)
                       or (spec.get("rj45") == rj45 and abs(spec.get("sfp", 0) - sfp) <= 2)):
            close.append(m)        # the port models miss or add a port or two (CRS317-1G-16S+: 1 + 16, seen 2 + 16)
        elif re.search(r"\d*(%d|%d)[A-Za-z]{0,3}(?![0-9A-Za-z])" % (rj45, rj45 + sfp), m.replace("-", " ")):
            partial.append(m)      # GS724T, DGS-1210-28P, FortiSwitch 1048E, USW-24
    return (sorted(exact, key=len) + sorted(close, key=len) + sorted(partial, key=len))[:k]


def visual_identify(vendor, gray, tdir, log=print, photo=None, candidates=4):
    """The make is readable but the model number isn't, so align every
    saved template of that make to the photo and keep the best that passes the normal
    alignment test. Only feature matching counts here (no port-only alignment): two
    models of one vendor can share a port layout. Returns (template, alignment) or None."""
    # saved vendor photos of this make (vendor_photos/) become templates first
    for src in sorted(VENDOR_PHOTOS.glob("*.source.json")) if VENDOR_PHOTOS.is_dir() else []:
        try:
            info = json.loads(src.read_text())
        except ValueError:
            continue
        if vi.norm(info.get("vendor", "")) != vi.norm(vendor or ""):
            continue
        key = f"{slug(info['vendor'])}_{slug(info['model'])}"
        if (Path(tdir) / key / "template.json").exists():
            continue
        saved = next((p for p in VENDOR_PHOTOS.glob(src.name.replace(".source.json", ".*"))
                      if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")), None)
        img = load_image(str(saved)) if saved else None
        if img is not None:
            try:
                build_template(img, Path(tdir) / key, info["vendor"], info["model"], source=info.get("url", ""))
                log(f"  built a template for {info['vendor']} {info['model']} from a saved vendor photo")
            except Exception as e:
                log(f"  saved photo of {info['model']} gave no template ({e})")
    best = None
    # the switch in the photo is the reference: a candidate must have its RJ45 count,
    # and its ports must land on the ports the models found (ms.port_agreement)
    expected = ports_from_photo(photo) if photo is not None else None
    photo_ports = None
    if photo is not None:
        import yolo_ports
        if yolo_ports.available():
            photo_ports = yolo_ports.detect_ports(photo)[0]
    for d in sorted(Path(tdir).iterdir()) if Path(tdir).is_dir() else []:
        if not (d / "template.json").exists():
            continue
        t = ms.Template(d)
        if vi.norm(t.meta.get("vendor", "")) != vi.norm(vendor or ""):
            continue
        # the photo's count is the detector's own and can be off (cable heads, LEDs): only a
        # template clearly the wrong size is skipped, the alignment test decides the rest
        if expected and expected.get("rj45") and \
                abs(spec_ports(t.meta)["rj45"] - expected["rj45"]) > 0.25 * expected["rj45"]:
            log(f"  {t.label}: {spec_ports(t.meta)['rj45']} RJ45, the photo has {expected['rj45']}; skipped")
            continue
        # feature matching only (port_fallback=False): two models of one vendor can
        # share a port layout, so lining up by ports alone says nothing about the model
        r = ms.align(t, gray, photo_ports=photo_ports, port_fallback=False)
        log(f"  visual check {t.label}: confidence {r['confidence']:.2f}, {r['inliers']} points{ports_note(r)}")
        if ms.passes(r) and (best is None or fit_rank(r) > fit_rank(best[1])):
            best = (t, r)
    if best or photo is None or not candidates:
        return best
    # no saved template fits: shortlist this make's models by the photo's port layout,
    # fetch a front view of each, and see which one lines up
    import yolo_ports
    if not yolo_ports.available():
        return None
    ports, info = yolo_ports.detect_ports(photo)
    rj45, sfp = info.get("rj45", 0), info.get("sfp", 0)
    if rj45 + sfp < 6:
        return None
    short = shortlist_by_layout(vendor, rj45, sfp, candidates)
    log(f"  photo shows {rj45} RJ45 + {sfp} SFP; {vendor} models that fit: {', '.join(short) or 'none'}")
    for m in short:
        imgs = _search_bing_front(vendor, m) or _local_photos(m)
        for src in imgs[:1]:
            img = load_image(src)
            if img is None:
                continue
            key = f"{slug(vendor)}_{slug(m)}"
            if (Path(tdir) / key / "template.json").exists():
                continue     # saved templates were checked above; never overwrite or delete them here
            try:
                build_template(img, Path(tdir) / key, vendor, m, source=IMAGE_SOURCES.get(src, src))
            except Exception as e:
                log(f"  {m}: no template ({e})")
                continue
            t = ms.Template(Path(tdir) / key)
            if matches_spec(t.meta, expected) is False:
                log(f"  candidate {m}: template has {spec_ports(t.meta)['rj45']} RJ45, the photo has {rj45}; skipped")
                import shutil
                shutil.rmtree(Path(tdir) / key, ignore_errors=True)
                continue
            r = ms.align(t, gray, photo_ports=photo_ports, port_fallback=False)
            log(f"  candidate {m}: confidence {r['confidence']:.2f}, {r['inliers']} points{ports_note(r)}")
            if ms.passes(r) and (best is None or fit_rank(r) > fit_rank(best[1])):
                best = (t, r)
            elif not ms.passes(r):
                import shutil
                shutil.rmtree(Path(tdir) / key, ignore_errors=True)   # keep only templates that matched something
    return best


def fit_rank(r):
    """Which of two passing alignments fits better: more of the photo's ports explained
    first (GS724T's SFP cages beat GS324PP, which has none), then confidence."""
    a = r.get("ports")
    return (round(min(a["on"], a["covered"]) / 0.05) if a else 0, r["confidence"])


def ports_note(r):
    """', 20 of 24 ports line up' style note for an alignment checked against the ports."""
    a = r.get("ports") if r else None
    if not a:
        return ""
    return (f", {a['pairs']} of {a['checked']} template ports land on the {a['detections']} ports found in the photo"
            + ("" if r.get("ports_ok") is not False else " (too few: rejected)"))


def find_vendor_images(vendor, model, log=print, lookalikes=True, budget_s=45, photo=None, expected=None,
                       agent=True):
    """Vendor photos for the model. The template agent (_template_agent) is the template
    source: when it gives a template, that is the answer. Only when it gives none (or
    agent=False, after its template did not fit) every other source runs: saved photos,
    our vendor-website search, the front-view image search (image_search.py) and the spec
    agent's search (agent_images.py). If the exact name finds none and it came from OCR
    (lookalikes=True), the look-alike spellings are tried (front-view search and our
    search only, to keep it quick). Results, misses included, are remembered so a bulk
    run doesn't repeat a slow lookup. Returns (images, where, model_used)."""
    if agent:
        found = _template_agent(vendor, model, photo, expected)
        if found:
            return found, "the template agent", model
    key = (vendor.lower(), model.upper(), lookalikes)
    if key in _LOOKUPS:
        return _LOOKUPS[key]
    found, where = _gather(vendor, model, log, budget_s)
    out = (found, where, model) if found else ([], "nowhere", model)
    if not found and lookalikes:
        for m in model_variants(model)[1:]:
            found = _search_bing_front(vendor, m) or _search_ours(vendor, m, log, budget_s // 4)
            if found:
                out = found, "look-alike search", m
                break
    _LOOKUPS[key] = out
    return out


# ----------------------------------------------------------------------------- vendor images

def http_get(url, timeout=25, max_bytes=15_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(max_bytes), r.geturl()




def load_image(src):
    """src is a local path or URL. Returns BGR image or None."""
    try:
        if re.match(r"https?://", src):
            data, _ = http_get(src)
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        else:
            img = cv2.imread(src, cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        print(f"  could not load {src}: {e}")
        return None


# ----------------------------------------------------------------------------- numbering

_LABEL_OCR = []


def port_label_tokens(photo, ports, up=8):
    """The number printed above or below each jack, read one jack at a time: the strip just above
    and just below every RJ45 box is cut out, enlarged `up` times and read on its own. On a 187 px
    high photo of a TL-SG3424 the whole-panel read finds no port number; this finds 12, 14, 16 above
    the top row and 21 below the bottom one. Returns (text, conf, cx, cy) tokens in photo pixels,
    the form choose_scheme reads."""
    if not _LABEL_OCR:
        from rapidocr_onnxruntime import RapidOCR
        _LABEL_OCR.append(RapidOCR())
    ocr = _LABEL_OCR[0]
    H_, W_ = photo.shape[:2]
    toks = []
    for p in ports:
        if p["type"] != "rj45" or p.get("status") == "not seen":
            continue
        x, y, w, h = p["box"]
        for a, b in ((y - 0.8 * h, y), (y + h, y + 1.8 * h)):
            y0, y1 = int(max(0, a)), int(min(H_, b))
            x0, x1 = int(max(0, x - 0.2 * w)), int(min(W_, x + 1.2 * w))
            if y1 - y0 < 3 or x1 - x0 < 3:
                continue
            crop = cv2.resize(photo[y0:y1, x0:x1], None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
            res, _ = ocr(crop)
            for _box, text, conf in res or []:
                s = str(text).strip()
                if re.fullmatch(r"\d{1,2}F?", s) and float(conf) >= 0.5:
                    toks.append((s, float(conf), (x0 + x1) / 2.0, (y0 + y1) / 2.0))
    return toks


def choose_scheme_on_photo(jacks, tokens):
    """choose_scheme on the photo's own RJ45 grid (detected jacks plus the cells filled in under
    cables), for labels read jack by jack: no homography between them, which on the first, rough
    alignment put the small labels next to the wrong template port."""
    meta = {"ports": [{"type": "rj45", "box": list(p["box"])} for p in jacks if p["type"] == "rj45"]}
    return choose_scheme(meta, np.eye(3), tokens)


def choose_scheme(tpl_meta, H, tokens):
    """Score each numbering scheme against port numbers read from the photo."""
    rj = [tuple(p["box"]) for p in tpl_meta["ports"] if p["type"] == "rj45"]
    if not rj or not tokens:
        return None, {}, None
    Hinv = np.linalg.inv(H)
    nums = []
    sfp_tokens = []
    for text, conf, x, y in tokens:
        t = text.strip().upper()
        q = cv2.perspectiveTransform(np.float32([[[x, y]]]), Hinv)[0, 0]
        if re.fullmatch(r"\d{1,2}", t):
            nums.append((int(t), q))
        elif re.fullmatch(r"\d{1,2}F", t):
            sfp_tokens.append((int(t[:-1]), q))
    ph = np.median([b[3] for b in rj])
    scores = {}
    for scheme in at.SCHEMES:
        numbered = at.number_ports(rj, scheme)
        hit = 0
        for n, q in nums:
            # label sits just above or below its jack: nearest jack by distance
            best = min(numbered, key=lambda nb: np.hypot(q[0] - (nb[1][0] + nb[1][2] / 2),
                                                          q[1] - (nb[1][1] + nb[1][3] / 2)))
            d = np.hypot(q[0] - (best[1][0] + best[1][2] / 2), q[1] - (best[1][1] + best[1][3] / 2))
            if d < 1.6 * ph and best[0] == n:
                hit += 1
        scores[scheme] = hit
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    # one stray digit read near a jack used to renumber a template for good (TL-SG1016D
    # ended up with 2 top-left); change from the 1-top-left default only on clear evidence
    scheme = best if ranked[0] >= 3 and (len(ranked) < 2 or ranked[0] - ranked[1] >= 2) else None
    sfp_names = None
    if sfp_tokens:
        # combo SFP ports labelled like 21F..24F: name the cages from the lowest label seen
        sfps = sorted([p for p in tpl_meta["ports"] if p["type"] == "sfp"], key=lambda p: p["box"][0])
        if sfps:
            n0, q0 = min(sfp_tokens, key=lambda t: t[1][0])
            idx = int(np.argmin([abs(q0[0] - (p["box"][0] + p["box"][2] / 2)) for p in sfps]))
            start = n0 - idx
            sfp_names = [f"{start + k}F" for k in range(len(sfps))]
    return scheme, scores, sfp_names


# ----------------------------------------------------------------------------- main

# product lines printed in place of (or beside) the maker's name; they name the maker too
PRODUCT_LINES = {"cisco": ["Catalyst", "Nexus", "Meraki", "Small Business"], "ubiquiti": ["UniFi", "EdgeSwitch", "EdgeMAX"],
                 "hp": ["ProCurve", "OfficeConnect", "ProLiant"], "hpe": ["ProCurve", "OfficeConnect", "Aruba"],
                 "aruba": ["ArubaOS", "Instant On"], "dell": ["PowerConnect", "PowerSwitch", "Force10"],
                 "netgear": ["ProSAFE", "Nighthawk", "Insight"], "tplink": ["JetStream", "Omada"],
                 "dlink": ["DGS", "DES", "xStack"], "juniper": ["Junos"], "mikrotik": ["RouterBOARD", "Cloud Router Switch"],
                 "brocade": ["FastIron"], "ruckus": ["ICX"], "zyxel": ["Nebula"], "arista": ["EOS"],
                 "extremenetworks": ["Summit", "ExtremeXOS"], "huawei": ["CloudEngine", "S5700"]}


def _vendor_aliases(vendor):
    """Names the vendor is printed under: its own name, the catalogue's aliases, switch_ocr's,
    its product lines."""
    si = load_catalog_once()
    names = {vendor} | set(PRODUCT_LINES.get(re.sub(r"[^a-z0-9]", "", vendor.lower()), []))
    for v, al in si.VENDOR_ALIASES.items():
        if vi.norm(v) == vi.norm(vendor):
            names |= {v, *al}
    try:
        from switch_ocr.identify import VENDORS
        for brand, (al, _pat) in VENDORS.items():
            if vi.norm(brand) == vi.norm(vendor):
                names |= set(al)
    except Exception:
        pass
    return [n for n in names if n and len(tm.squash(n)) >= 2]


def vendor_text_score(vendor, tokens):
    """How well the vendor's name is printed on the photo, 0-1: a whole word for names of up to 4
    letters (A10, F5, HPE), else the best window of any reading. Serial / MAC stickers excluded."""
    import switch_identify as si
    best = 0.0
    texts = [t for t, *_ in tokens if t and not si.is_serial_like(t)]
    for name in _vendor_aliases(vendor):
        a = tm.squash(name)
        for t in texts:
            if len(a) <= 4:
                # a whole word; one extra character at either end allowed (the FS logo's icon reads 'GFS')
                words = [tm.squash(w) for w in re.split(r"[^A-Za-z0-9]+", t) if w]
                s = 1.0 if any(w == a or (len(w) == len(a) + 1 and (w.endswith(a) or w.startswith(a)))
                               for w in words) else 0.0
            else:
                s = si.best_substring_similarity(a, tm.squash(t))[0]
            best = max(best, s)
    return round(best, 3)


def model_text_score(model, tokens):
    """How well the model is printed on the photo, 0-1 (1.0 = printed as is)."""
    import switch_identify as si
    if not model:
        return 0.0
    m = tm.squash(model)
    best = 0.0
    for t, *_ in tokens:
        if not t:
            continue
        s = 1.0 if m in tm.squash(t) else si.best_substring_similarity(m, tm.squash(t))[0]
        best = max(best, s)
    return round(best, 3)


VENDOR_TEXT_MIN = 0.82                  # the vendor's name read on the photo (switch_identify's VENDOR_MIN)


def id_confidence(vendor, model, tokens):
    """identify_confidence, as the router and appliance agents write it: vendor and model scores 0-1
    and vendor_doubt (another vendor whose name is printed about as well). A vendor without its name
    on the photo scores at most 0.5 (review 2, #4)."""
    v = vendor_text_score(vendor, tokens) if vendor else 0.0
    if vendor and getattr(identify, "brand_text", 0.0) >= VENDOR_TEXT_MIN and not RUN.get("given"):
        v = max(v, getattr(identify, "brand_text", 0.0))      # switch_ocr's own read of the brand name
    m = model_text_score(model, tokens) if model else 0.0
    if vendor and v < VENDOR_TEXT_MIN:
        v = min(v, 0.5)
    doubt = None
    if vendor and v >= VENDOR_TEXT_MIN:
        si = load_catalog_once()
        for other in si.VENDOR_ALIASES:
            if vi.norm(other) != vi.norm(vendor) and len(tm.squash(other)) > 4 \
                    and vendor_text_score(other, tokens) >= max(VENDOR_TEXT_MIN, v - 0.05):
                doubt = other
                break
    return {"vendor": round(v, 3), "model": round(m, 3), "vendor_doubt": doubt}


def write_error(out, photo, message):
    """result.json for a run that could not map anything (unreadable photo, crash): the same
    fields as every other result, status 'error'."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "result.json").write_text(json.dumps(
        {"photo": str(photo), "status": "error", "mapped": False, "error": message, "mode": None,
         "vendor": None, "model": None, "ports": [], "identify_confidence": None, "spec": None}, indent=1))


def finish_result():
    """Adds the fields every agent writes: status, mapped, error, identify_confidence, a status on
    every port, spec (device_spec: ports by type, rack units, data sheet, checked against the
    mapped ports); and says how a given make / model was confirmed."""
    out = RUN.get("out")
    f = Path(out) / "result.json" if out else None
    if not f or not f.exists():
        return
    r = json.loads(f.read_text())
    if r.get("status") == "error":
        return
    ports = r.get("ports") or []
    for p in ports:
        if p.get("status") not in ("connected", "empty", "unknown", "not seen"):
            p["status"] = "unknown"
    mode = r.get("mode")
    r["mapped"] = bool(ports)
    r["status"] = ("mapped_template" if mode == "template" and ports else
                   "mapped_auto" if mode == "auto" and ports else "not_mapped")
    r.setdefault("error", None)
    if RUN.get("idconf") is not None:
        r["identify_confidence"] = RUN["idconf"]
    if RUN.get("given"):
        if mode == "template" and ports:
            RUN["given_confirmed"] = RUN.get("given_confirmed") or "the template fit"
        r["identified_by"] = (f"given, confirmed by {RUN['given_confirmed']}" if RUN.get("given_confirmed")
                              else "given")
    import device_spec
    # auto mode leaves the model out of the result; a model read clearly still gets its spec
    read = RUN.get("model_read") if (RUN.get("idconf") or {}).get("model", 0) >= 0.8 or RUN.get("given") else None
    device_spec.add_spec(r, "switch", read_model=read)
    f.write_text(json.dumps(r, indent=1))


def main():
    ap = argparse.ArgumentParser(description="Map the ports of any switch from one photo.")
    ap.add_argument("photo", nargs="?", help="the device photo; optional with --frames (then the "
                                              "device is cut from the folder's rack image)")
    ap.add_argument("--make"); ap.add_argument("--model")
    ap.add_argument("--frames", help="the device's video-frames folder (switch_location.json, the rack "
                                     "image, rank*_frame*.png), read when the photo gives no known model")
    ap.add_argument("--label-photo", help="close-up of the vendor/model label, read when the "
                                          "photo and the video frames give no known model")
    ap.add_argument("--vendor-image", action="append", default=[])
    ap.add_argument("--scheme", choices=list(at.SCHEMES))
    ap.add_argument("--no-ask", action="store_true")
    ap.add_argument("--out"); ap.add_argument("--templates", default=str(TEMPLATES))
    ap.add_argument("--learn", action="store_true",
                    help="save new templates, the template store and caches in the package for next time; "
                         "without it nothing outside --out is written")
    args = ap.parse_args()
    if not (args.photo or args.frames):
        ap.error("give a photo, or --frames with the device's video-frames folder")

    if not args.photo:
        import video_frames as vf
        out = Path(args.out or OUTPUT / Path(args.frames).name)
        out.mkdir(parents=True, exist_ok=True)
        args.photo = str(out / "0_device_from_rack.png")
        cv2.imwrite(args.photo, vf.device_from_rack(args.frames))
        print(f"No photo given; the device is cut from the rack image in {args.frames}")
    out = Path(args.out or OUTPUT / Path(args.photo).stem)
    RUN.update(out=str(out), photo=str(Path(args.photo).resolve()))
    photo = cv2.imread(args.photo, cv2.IMREAD_COLOR)
    if photo is None or photo.shape[0] < 16 or photo.shape[1] < 16:
        msg = (f"could not read the photo {args.photo}" if photo is None else
               f"the photo is too small to map ({photo.shape[1]} x {photo.shape[0]} px)")
        write_error(out, RUN["photo"], msg)
        print(msg)
        sys.exit(1)
    gray = cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)
    global WEB_CACHE
    if args.learn:
        os.environ["PORT_AGENT_STORE_WRITES"] = "1"
    else:
        # nothing outside --out is written: the saved templates are used from a working copy in the
        # run's folder, the template store is read-only, caches and downloads go to the run's folder;
        # all of it is removed when the run ends
        out.mkdir(parents=True, exist_ok=True)
        os.environ["PORT_AGENT_STORE_WRITES"] = "0"
        if not os.environ.get("PORT_AGENT_CACHE_DIR"):
            os.environ["PORT_AGENT_CACHE_DIR"] = str(out / "_cache")
            RUN_TEMP.append(out / "_cache")
        WEB_CACHE = out / "_cache" / "vendor_photos_web"
        RUN_TEMP.append(WEB_CACHE)
        work = out / "_templates"
        shutil.rmtree(work, ignore_errors=True)
        if Path(args.templates).is_dir():
            shutil.copytree(args.templates, work, ignore=shutil.ignore_patterns("__pycache__"))
        args.templates = str(work)
        RUN_TEMP.append(work)
    tdir = Path(args.templates)
    tdir.mkdir(parents=True, exist_ok=True)

    # 1. make and model, read in up to three steps; each later one only when the ones
    # before it gave no known model: (1) the photo, (2) the device's video frames,
    # (3) a close-up of the label
    print("Reading the panel text...")
    vendor, model, tokens, engine = identify(photo)
    print(f"  ({engine})")
    # a vendor is only taken from the photo when its name is printed there, or its exact catalogue
    # model is: switch_ocr can name a vendor from a digit pattern alone ("Arista" on a crop with no
    # Arista text, and not on every run) - review 2, #4
    if vendor:
        seen = tokens + list(getattr(identify, "extra_tokens", []) or [])
        vs, ms_ = vendor_text_score(vendor, seen), model_text_score(model, seen)
        printed_model = ms_ >= 0.95 and len(tm.squash(model or "")) >= 5 and re.search(r"[A-Za-z]", model or "") \
            and re.search(r"\d", model or "")
        brand_read = getattr(identify, "brand_text", 1.0) >= 0.7     # switch_ocr read the brand's name
        if vs < VENDOR_TEXT_MIN and not printed_model and not brand_read:
            print(f"  '{vendor}' is not written on the photo (best match {vs}) and '{model or '?'}' is not "
                  f"one of its catalogue models printed there; vendor not used")
            vendor = model = None
    RUN["idconf"] = id_confidence(vendor, model, tokens + list(getattr(identify, "extra_tokens", []) or []))
    # tokens stay from the main photo: they are in its pixels and read the port numbers later
    given = bool(args.make and args.model)
    read_ok = model_known(vendor, model)
    source = "photo"
    if not read_ok and args.frames and not given:
        import video_frames as vf
        print(f"Not read confidently from the photo ({vendor or '?'} {model or '?'}); "
              "reading the device's video frames...")
        try:
            fr = vf.read_frames(args.frames, identify, model_known, log=print, out_dir=out, device=photo)
        except (ValueError, OSError) as e:
            print(f"  video frames not usable: {e}")
            fr = None
        if fr and fr["confident"]:
            print(f"  note: main photo read '{vendor or '?'} {model or '?'}', the video frame {fr['frame']} "
                  f"'{fr['vendor']} {fr['model']}'; using the frames")
            vendor, model, source, read_ok = fr["vendor"], fr["model"], "frames", True
        elif fr:
            print(f"  the video frames gave no known model either (best: {fr['vendor'] or '?'} "
                  f"{fr['model'] or '?'})")
            # what the frames agree on fills in what the photo lacks
            vendor, model = vendor or fr["vendor"], model or fr["model"]
    label_read = False
    if args.label_photo and not read_ok and not given:
        label = cv2.imread(args.label_photo, cv2.IMREAD_COLOR)
        if label is None:
            print(f"  could not read the label close-up {args.label_photo}; ignoring it")
        else:
            print("Reading the label close-up...")
            lv, lm, _, lengine = identify(label)
            print(f"  ({lengine}) label reads: {lv or '?'} {lm or '?'}")
            if lv or lm:
                if (lv, lm) != (vendor, model):
                    print(f"  note: main photo read '{vendor or '?'} {model or '?'}', the label close-up "
                          f"'{lv or '?'} {lm or '?'}'; using the label")
                # the close-up wins; the main photo only fills in what the label lacks
                vendor, model = lv or vendor, lm or model
                label_read = bool(lv and lm)
                if label_read:
                    source = "label"
    elif args.label_photo and read_ok:
        print("  the label close-up is not needed: the make and model were read confidently")
    how = None
    if args.make or args.model:
        # a given make / model is a hint, confirmed against the panel text here or by the template fit
        # later; never echoed back as if it had been read (review 2, #5)
        read_v, read_m = vendor, model
        vendor, model = args.make or vendor, args.model or model
        how = "Given on the command line"
        RUN["given"] = True
        vtxt = vendor_text_score(vendor, tokens) if vendor else 0.0
        mtxt = model_text_score(model, tokens) if model else 0.0
        if mtxt >= 0.9 or (vtxt >= VENDOR_TEXT_MIN and not args.model):
            RUN["given_confirmed"] = "the panel text"
        if read_v and vendor and vi.norm(read_v) != vi.norm(vendor) and vtxt < VENDOR_TEXT_MIN:
            print(f"  note: the given make {vendor} is not written on the photo; the panel reads "
                  f"{read_v} {read_m or ''}".rstrip())
    elif vendor and model:
        how = {"label": "Read from the label close-up (OCR)",
               "frames": "Read from the video frames (OCR)"}.get(source, "Read from the photo (OCR)")
        print(f"  found: {vendor} {model}")
        ok = ask(f"  Is this {vendor} {model}? [Y/n] ", args, "y")
        if ok.lower().startswith("n"):
            vendor = model = None
    if vendor and not model and not (args.make or args.model):
        v = vi.find_vendor(vendor)
        vis = visual_identify(v["name"] if v else vendor, gray, tdir, photo=photo)
        if vis:
            tpl0, r0 = vis
            print(f"  model not readable; identified visually as {tpl0.label}")
            vendor, model, how = tpl0.meta["vendor"], tpl0.meta["model"], "Identified visually against saved templates"
    if not (vendor and model):
        seen = f" (read so far: {vendor or '?'} {model or '?'})" if (vendor or model) else ""
        print(f"Could not read an exact make and model from the photo{seen}.")
        vendor = ask(f"  Make (e.g. TP-Link, Cisco) [{vendor or ''}]: ", args, vendor)
        model = ask(f"  Model (e.g. TL-SG3424) [{model or ''}]: ", args, model)
        how = "Given by the user"
        if not (vendor and model):
            # no exact model (and none given): do not show a guessed/garbled name - number
            # the ports visible in the photo directly instead (give --model for a template)
            print("No exact model to look up; detecting ports directly in the photo.")
            args.numbering = "row" if args.scheme == "row" else "column"
            ms.run_auto(photo, args, out, "Make and model not identified.",
                        vendor=vendor, expected=None)
            return
    v = vi.find_vendor(vendor)
    if v:
        vendor = v["name"]
    model = model.upper() if re.match(r"^[a-z0-9-]+$", model, re.I) else model
    import yolo_ports
    photo_ports, photo_info = yolo_ports.detect_ports(photo) if yolo_ports.available() else (None, {})
    if how and how.startswith("Read from"):
        fix = port_count_fix(vendor, model, photo_info.get("rj45", 0), photo_info.get("sfp", 0))
        if fix:
            print(f"  note: {fix[1]}")
            model = fix[0]
            how = how.replace("(OCR)", "(OCR), corrected by the port count")
        # a reading that is itself a catalogue model is kept: renaming it to a saved template one or
        # two characters away turned TL-SG1016DE into TL-SG1016D and CSS326 into CRS326
        k = None if in_catalog(vendor, model) else known_model(vendor, model, tdir)
        if k and k[1] != model and not ms.passes(ms.align(ms.Template(tdir / f"{slug(k[0])}_{slug(k[1])}"), gray,
                                                          photo_ports=photo_ports, port_fallback=False)):
            print(f"  {model} looks like the saved {k[1]}, but that template does not fit the photo; keeping {model}")
            k = None
        if k and k[1] != model:
            print(f"  OCR read {model}; a saved template has {k[1]} (look-alike), using that")
            vendor, model = k[0], k[1]
            how = how.replace("(OCR)", "(OCR), corrected to a saved model")
    # one name per model: a reading missing the catalogue name's prefix (SG2428P for TL-SG2428P) is
    # that catalogue model, so both spellings use and save one template (review 2, #7)
    if not any(tm.squash(m) == tm.squash(model) for m in _make_models(vendor)):
        full = catalog_name(vendor, model)
        if full and full != model:
            print(f"  '{model}' is the catalogue model {full} without its prefix; using {full}")
            model = full
    key = f"{slug(vendor)}_{slug(model)}"
    print(f"Make and model: {vendor} {model}")
    RUN["idconf"] = id_confidence(vendor, model, tokens + list(getattr(identify, "extra_tokens", []) or []))
    RUN["model_read"] = model
    if RUN["given"] and not RUN["idconf"]["vendor_doubt"]:
        try:
            if read_v and vi.norm(read_v) != vi.norm(vendor):
                RUN["idconf"]["vendor_doubt"] = read_v
        except NameError:
            pass


    # 2. saved template? The template agent is asked first; a template saved earlier is
    # reused only when it was built from the image the agent picks now (or the agent has none)
    tpl, r, src = None, None, None
    agent_pick = [] if args.vendor_image else _template_agent(vendor, model, photo)
    saved_tried = False
    if (tdir / key / "template.json").exists() and \
            (not agent_pick or _same_image(ms.Template(tdir / key).meta.get("image_source"), agent_pick)):
        saved_tried = True
        t = ms.Template(tdir / key)
        r0 = ms.align(t, gray, photo_ports=photo_ports)
        print(f"  saved template: confidence {r0['confidence']:.2f}, {r0['inliers']} points")
        if ms.passes(r0):
            tpl, r = t, r0
            src = agent_pick[0] if agent_pick else t.meta.get("image_source")
        else:
            print("  saved template does not line up with this photo; looking for vendor images")

    # 3. build one from vendor images; the spec (port counts) picks between candidates
    if tpl is None:
        spec = rt.specs(vendor, model)
        expected = spec.get("ports") if spec and spec.get("found") else None
        if expected:
            print(f"  spec: {spec.get('port_config')}")
        elif rt.ports_from_model_name(model):
            expected = rt.ports_from_model_name(model)
            print(f"  ports from the model name: {expected['rj45']} RJ45 + {expected['sfp']} SFP")
        elif rt.ports_from_model_digits(model):
            # the model number's trailing digits are the port count on nearly every
            # family (TL-SG1016 is 16 ports). A total, not a split - so it sizes the
            # port block and is never used to reject a template on its RJ45 count.
            expected = dict(rt.ports_from_model_digits(model), total_only=True)
            print(f"  ports from the model number: {expected['rj45']} "
                  f"(the '{model}' in the name)")
        elif ports_from_photo(photo):
            expected = ports_from_photo(photo)
            print(f"  no spec; the photo shows {expected['rj45']} RJ45 ports, templates are checked against that")
        sources = list(args.vendor_image)
        where = "given"
        if not sources:
            print(f"Looking for {vendor} {model} product photos...")
            # the template agent first (its store, then the official sites + the internet,
            # the hardware revision matched to this photo); every other source only when
            # it has nothing
            found, where, used = find_vendor_images(vendor, model, lookalikes=how.startswith("Read"),
                                                    photo=photo, expected=expected)
            if used != model:
                print(f"  nothing for {model}; found the look-alike {used}")
                model, key = used, f"{slug(vendor)}_{slug(used)}"
            print(f"  {len(found)} images from {where}")
            sources += found
            if where != "the template agent":
                for u in ([spec.get("image_url")] if spec and spec.get("image_url") else []) + \
                        (vi.page_images(spec["datasheet_url"], model) if spec and spec.get("datasheet_url") else []):
                    if u and u not in sources:
                        sources.append(u)
        vendor_img_found = sources[0] if sources else None   # shown even if nothing aligns
        tries, tried = 0, []
        while tpl is None:
            tpl, r, src = evaluate_sources(sources[:MAX_CANDIDATE_IMAGES], gray, vendor, model, tdir, key,
                                           expected=expected, photo_ports=photo_ports)
            if tpl is not None:
                remember_template(src, vendor, model, r)
                break
            if not args.vendor_image and where in ("the template agent", "the agent's other images"):
                # the agent's template did not line up with the photo: the agent's other
                # templates and online candidates first, then every other source
                tried += sources
                if where == "the template agent":
                    where = "the agent's other images"
                    sources = [u for u in more_agent_images(vendor, model, expected, exclude=tried, photo=photo)
                               if u not in tried][:MAX_CANDIDATE_IMAGES]
                    print(f"  the template agent's template did not line up; {len(sources)} more from the agent")
                else:
                    where = "the other sources"
                    sources = [u for u in find_vendor_images(vendor, model, lookalikes=how.startswith("Read"),
                                                             agent=False)[0] if u not in tried]
                    print(f"  none of the agent's images lined up; {len(sources)} images from the other sources")
                if vendor_img_found is None and sources:
                    vendor_img_found = sources[0]
                if sources:
                    continue
            # nothing online lined up: the template saved earlier for this model is tried
            # before giving up (step 2 skips it when the agent picks a different image)
            if not saved_tried and (tdir / key / "template.json").exists():
                saved_tried = True
                t = ms.Template(tdir / key)
                r0 = ms.align(t, gray, photo_ports=photo_ports)
                print(f"  no online image lined up; the saved template: confidence {r0['confidence']:.2f}, "
                      f"{r0['inliers']} points")
                if ms.passes(r0):
                    tpl, r, src = t, r0, t.meta.get("image_source")
                    break
            tries += 1
            print("No vendor image lined up with the photo." if sources else
                  "No vendor images found automatically.")
            more = ask("  Paste a URL or file path of a straight-on front photo of this model "
                       "(Enter to stop): ", args) if tries <= 3 else None
            if not more:
                # no usable vendor image from any source: never dead-end - number the ports
                # visible in the photo directly instead (a --vendor-image makes it exact)
                print("Could not build a template from any source; detecting ports directly in the photo.")
                args.numbering = "row" if args.scheme == "row" else "column"
                ms.run_auto(photo, args, out, "No vendor image lined up with the photo.",
                            vendor=vendor, expected=expected)
                # the vendor image WAS found (agent or web) - show it so the vendor-image
                # step is never "failed"; only the alignment to this photo fell short
                if vendor_img_found is not None:
                    vi_img = load_image(vendor_img_found)
                    if vi_img is not None:
                        cv2.imwrite(str(out / "3_vendor.png"), vi_img)
                    save_fetched(out, vendor_img_found, fitted=False)
                return
            sources = [more]

    # 4. numbering
    scheme, scores, sfp_names = (args.scheme, {}, None) if args.scheme else choose_scheme(tpl.meta, r["H"], tokens)
    num_how = "matched"
    if scheme is None and not args.scheme and tpl.meta.get("auto_generated"):
        # the whole-panel read found too few port numbers: read the label of each jack on its own
        try:
            jacks = photo_ports if photo_ports else yolo_ports.detect_ports(photo)[0]
            s2, sc2, names2 = choose_scheme_on_photo(jacks, port_label_tokens(photo, jacks))
        except Exception as e:                       # noqa: BLE001 - numbering falls back below
            print(f"  could not read the port labels one by one: {e}")
            s2, sc2, names2 = None, {}, None
        if s2:
            scheme, scores, sfp_names, num_how = s2, sc2, sfp_names or names2, "read jack by jack"
        elif tpl.meta.get("numbering_read") and tpl.meta.get("numbering_scheme") in at.SCHEMES:
            # read off an earlier photo of this model and saved with its template
            scheme, num_how = tpl.meta["numbering_scheme"], "saved with the template"
            print(f"Port numbering: {scheme} (read off an earlier photo of this model)")
    if tpl.meta.get("auto_generated"):
        if scheme is None:
            print("Could not read the port numbers off the photo to work out the numbering.")
            for i, (k, v) in enumerate(at.SCHEMES.items(), 1):
                print(f"  {i}. {k}: {v}")
            pick = ask("  Which numbering does this switch use? [1] ", args, "1")
            scheme = list(at.SCHEMES)[int(pick) - 1] if pick.isdigit() and 1 <= int(pick) <= 3 else "top-odd"
            print(f"  using {scheme}")
        elif args.scheme:
            print(f"Port numbering: {scheme} (given)")
        elif num_how != "saved with the template":
            print(f"Port numbering: {scheme} ({num_how} {scores.get(scheme, 0)} printed numbers)")
        # the template's own folder: a template used for this photo only is not under templates/
        at.renumber(tpl.folder, scheme, sfp_names)
        if num_how in ("matched", "read jack by jack") and scores.get(scheme, 0) >= 3:
            meta_f = Path(tpl.folder) / "template.json"
            meta = json.loads(meta_f.read_text())
            meta["numbering_read"] = True          # printed numbers decided it: later photos keep it
            meta_f.write_text(json.dumps(meta, indent=1))
        tpl = ms.Template(tpl.folder)

    # 5. outputs. switch_fit does the final fit - its own alignment, port check, re-fit
    # through the detected ports and moving-least-squares snap - and ms.render draws the
    # stage images with that homography (its draw_overlay/snap_polygons are switch_fit's).
    import switch_fit
    photo_ports = None
    import yolo_ports
    if yolo_ports.available():
        photo_ports = yolo_ports.detect_ports(photo)[0]
    try:
        sf = switch_fit.fit(photo, str(tpl.folder), photo_ports=photo_ports, draw=False)
        if sf.get("homography"):
            pc = sf.get("port_check") or {}
            print(f"  switch_fit: {sf['verdict']} ({sf.get('confidence', 0):.2f}), aligned by "
                  f"{sf.get('aligned_by')}, {sf.get('refit_pairs', 0)} re-fit, {sf.get('snapped', 0)} snapped"
                  + (f", {pc['err']:.3f} port-width error" if pc.get('err') is not None else ""))
            r = dict(r, H=np.asarray(sf["homography"], float), photo_ports=photo_ports,
                     refined_pairs=sf.get("refit_pairs", 0),
                     confidence=sf.get("confidence", r["confidence"]),
                     inliers=sf.get("matched_points", r["inliers"]))
        else:
            print(f"  switch_fit found no better fit ({', '.join(sf.get('reasons', []))}); "
                  "keeping the pipeline alignment")
            r = ms.snap_to_ports(tpl, r, photo)
    except Exception as e:
        print(f"  (switch_fit fit failed: {e}; using the pipeline alignment)")
        r = ms.snap_to_ports(tpl, r, photo)
    try:
        ms.render(args.photo, photo, tpl, r, out, how=how)
        save_fetched(out, src)
    except Exception as e:
        # drawing the template overlay failed for some reason: never leave the run with no
        # overlay - fall back to numbering the ports the port model finds on the photo
        print(f"  (drawing the template overlay failed: {e}; detecting ports directly)")
        args.numbering = "row" if args.scheme == "row" else "column"
        ms.run_auto(photo, args, out, "The template overlay could not be drawn.",
                    vendor=vendor, expected=expected)


def save_fetched(out, src, fitted=True):
    """The template image as it was fetched (template agent store / online, web search,
    vendor site), uncropped, with where it came from: out/2_fetched_template.png and
    2_fetched_template.json, for the UI. Never raises."""
    if not src:
        return
    try:
        out.mkdir(parents=True, exist_ok=True)
        info = {"source": str(src), "found_by": IMAGE_SOURCES.get(str(src)) or "", "fitted": bool(fitted)}
        # a template agent store image: its record says where the agent got it
        if _agent_on_path() and str(src).startswith(str(TEMPLATE_AGENT_DIR)):
            from template_agent import TemplateStore
            st = TemplateStore()
            for row in st._db.execute("SELECT * FROM templates WHERE image_path LIKE ?",
                                      ("%" + Path(src).name,)).fetchall():
                info.update(store_id=row["id"], store_status=row["status"], origin=row["source"] or "",
                            url=row["source_url"] or "", page=row["page_url"] or "",
                            store_vendor=row["vendor"], store_model=row["model"])
                info["found_by"] = info["found_by"] or "template agent (template store)"
                break
        elif str(src) in AGENT_ONLINE:
            info["url"] = AGENT_ONLINE[str(src)][0]
        elif re.match(r"https?://", str(src)):
            info["url"] = str(src)
        img = load_image(str(src))
        if img is not None:
            cv2.imwrite(str(out / "2_fetched_template.png"), img)
            info["size"] = [int(img.shape[1]), int(img.shape[0])]
        (out / "2_fetched_template.json").write_text(json.dumps(info, indent=1))
        print(f"Fetched template: {Path(str(src)).name} ({info['found_by'] or 'vendor image'})")
    except Exception as e:
        print(f"  (could not save the fetched template: {e})")


def _agent_ports_sidecar(source):
    """Path to switch_template_agent's '<image>.ports.json' for a source, if it exists.
    That file holds the agent's own detected panel crop and port boxes - far more
    reliable than re-detecting them, especially on renders our detector struggles with."""
    try:
        p = Path(str(source))
        side = p.with_name(p.name + ".ports.json")
        return side if side.is_file() else None
    except Exception:
        return None


def build_template_from_agent(img, folder, vendor, model, ports_json, source=""):
    """Build a template from switch_template_agent's stored layout instead of re-detecting
    the ports. The agent already found the front-panel crop and every port box (as 0-1
    fractions of that crop) and kept them in <image>.ports.json, so this just crops the
    face and converts those boxes to pixels - no classical port detection, which is what
    was failing on the agent's clean renders."""
    import cv2
    data = json.loads(Path(ports_json).read_text(encoding="utf-8"))
    fx0, fy0, fx1, fy1 = [int(round(v)) for v in data["face_crop"]]
    h, w = img.shape[:2]
    fx0, fy0 = max(0, fx0), max(0, fy0)
    fx1, fy1 = min(w, fx1), min(h, fy1)
    cw, ch = fx1 - fx0, fy1 - fy0
    if cw < 20 or ch < 10 or not data.get("ports"):
        raise ValueError("agent layout: empty crop or no ports")
    U = at.UPSCALE_TO_FACE_H / ch          # same face height the detector-built ones use
    crop = cv2.resize(img[fy0:fy1, fx0:fx1], None, fx=U, fy=U, interpolation=cv2.INTER_CUBIC)
    H, W = crop.shape[:2]
    ports, rj_boxes = [], []
    for p in data["ports"]:
        bx0, by0, bx1, by1 = p["box"]
        x, y = bx0 * cw * U, by0 * ch * U
        bw, bh = (bx1 - bx0) * cw * U, (by1 - by0) * ch * U
        box = [int(round(x)), int(round(y)), int(round(bw)), int(round(bh))]
        kind = "sfp" if str(p.get("kind", "rj45")).lower().startswith("sfp") else \
               ("console" if "console" in str(p.get("kind", "")).lower() else "rj45")
        ports.append({"id": p.get("number"), "type": kind, "poe": None, "box": box})
        if kind == "rj45":
            rj_boxes.append(box)
    # the agent's layout lists the data ports only (TL-SG3424: 24 RJ45 + 4 SFP, no
    # console); console, management and USB ports come from the port models, where they
    # don't sit on a port already there
    try:
        import yolo_ports
        if yolo_ports.available():
            def inside(c, b):
                return b[0] <= c[0] <= b[0] + b[2] and b[1] <= c[1] <= b[1] + b[3]
            named, _ = yolo_ports.detect_ports(crop)
            for q in named:
                if q["type"] in ("rj45", "sfp"):
                    continue
                b = [int(round(v)) for v in q["box"]]
                c = (b[0] + b[2] / 2, b[1] + b[3] / 2)
                if any(inside(c, e["box"]) for e in ports):
                    continue
                pid, n = q["id"], 2
                while any(e["id"] == pid for e in ports):    # two USB ports: USB, USB 2
                    pid, n = f"{q['id']} {n}", n + 1
                ports.append({"id": pid, "type": q["type"], "poe": None, "box": b})
    except Exception as e:                    # the agent's ports are still a template
        print(f"  (named ports not added: {e})")
    folder.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(folder / "front.png"), crop)
    # align on the logo/LEDs/edges, not the RJ45 block (cables cover it in the photo)
    ignore = []
    if rj_boxes:
        xs = [b[0] for b in rj_boxes]; ys = [b[1] for b in rj_boxes]
        xe = [b[0] + b[2] for b in rj_boxes]; ye = [b[1] + b[3] for b in rj_boxes]
        pad = int(np.median([b[3] for b in rj_boxes]) * 0.25)
        ignore = [[max(0, min(xs) - pad), max(0, min(ys) - pad),
                   max(xe) - min(xs) + 2 * pad, max(ye) - min(ys) + 2 * pad]]
    meta = {
        "vendor": vendor, "model": model, "description": "", "summary": at._summary(ports),
        "image": "front.png", "image_source": source, "auto_generated": True,
        "numbering_scheme": "top-odd",
        "match_regions": [[0, 0, W, H]], "ignore_regions": ignore, "ports": ports,
    }
    (folder / "template.json").write_text(json.dumps(meta, indent=1))
    return meta


def build_template(img, folder, vendor, model, source="", face_box=None):
    """Template from a vendor photo. If the image came from switch_template_agent, use the
    agent's stored port layout (its '<image>.ports.json'); otherwise the RackTrack port
    models when available, else autotemplate's classical detector."""
    sidecar = _agent_ports_sidecar(source)
    if sidecar is not None:
        try:
            return build_template_from_agent(img, folder, vendor, model, sidecar, source=source)
        except Exception as e:
            print(f"  (agent layout unusable: {e}; detecting ports in the image instead)")
    import yolo_ports
    if yolo_ports.available():
        return yolo_ports.build_template(img, folder, vendor, model, source=source)
    return at.build_template(img, folder, vendor, model, source=source, face_box=face_box)


def evaluate_sources(sources, gray, vendor, model, tdir, key, report=None, expected=None, photo_ports=None):
    """Build a template from each vendor image and keep the one that aligns best; with
    `expected` port counts from the spec, templates with the right counts come first.
    `report(i, src, template_or_None, alignment_or_error)` is called for each image."""
    best = None
    work = Path(tempfile.mkdtemp(prefix="switchtpl_"))
    for i, src in enumerate(sources):
        img = load_image(src)
        if img is None or min(img.shape[:2]) < 100:
            if report:
                report(i, src, None, "could not load, or smaller than 100 px")
            continue
        folder = work / f"c{i}"
        try:
            build_template(img, folder, vendor, model, source=src, face_box=AGENT_FACES.get(src))
            t = ms.Template(folder)
            r = ms.align(t, gray, photo_ports=photo_ports)
        except Exception as e:
            print(f"  candidate {i + 1}: skipped ({e})")
            if report:
                report(i, src, None, str(e))
            continue
        if report:
            report(i, src, t, r)
        n_ports = len(t.meta["ports"])
        n_rj = sum(p["type"] == "rj45" for p in t.meta["ports"])
        # A template has to account for the ports the photo actually shows. Port
        # detection on a product render can find a handful of jacks and stop - that
        # template then aligns happily onto a small patch of panel (often the LED
        # strip), scoring well while explaining none of the ports. Worse, it gets
        # saved, and every later photo of the model reuses it. The photo's own
        # detections are a floor that needs no spec; the model's own count, when its
        # name states one, is a second.
        # RJ45 against RJ45: counting the photo's SFP cages too rejected every right
        # template of a switch with uplinks (24 RJ45 in the image vs 24 + 4 seen)
        n_seen = len([q for q in photo_ports or []
                      if q.get("status") != "not seen" and q["type"] == "rj45"])
        # a count read off the model number is a total, never a reason to reject a template
        want_rj = 0 if (expected or {}).get("total_only") else (expected or {}).get("rj45") or 0
        # the photo's count is a floor with a margin: the detector can over-count (32 on a 24-port
        # switch: cable heads, LEDs), and a hard floor then dropped every right template. The port
        # check after the fit still rejects a template that leaves the photo's ports uncovered
        floor = max(int(0.75 * n_seen), int(0.6 * want_rj) if want_rj else 0)
        # a lone management jack (SFP switches: CRS309-1G-8S+IN) is no port block to check
        if floor >= 4 and n_rj < floor:
            print(f"  candidate {i + 1}: skipped ({n_rj} RJ45 in the vendor image, but the "
                  f"photo shows {n_seen}"
                  + (f" and this model has {want_rj}" if want_rj else "") + ")")
            if report:
                report(i, src, None, f"only {n_rj} RJ45 ports, too few for this photo")
            continue
        print(f"  candidate {i + 1}: {n_ports} ports found, confidence {r['confidence']:.2f}, "
              f"{r['inliers']} points")
        # usable alignments first, then templates matching the spec, then the one whose
        # ports land best on the ports found in the photo (the fit the overlay shows),
        # then the best feature match; near-ties go to the higher-resolution image
        rank = (ms.passes(r), r.get("layout_ok") is not False, matches_spec(t.meta, expected) is not False,
                r.get("layout_ok") is True, fit_rank(r)[0],
                not r.get("by_ports"), r["inliers"] // 8, round(r["confidence"], 2), img.shape[1])
        if best is None or rank > best[3]:
            best = (folder, r, src, rank)
    def _accept(r, src):
        if ms.passes(r):
            return True
        # a template built from switch_template_agent's own layout: the agent already
        # SIFT-matched this image to the photo (>=20 inliers) before returning it, so
        # trust a near-miss on our second-opinion patch aligner, which falls just short
        # on the cabled/angled panels the agent handles. Still above the noise floor
        # (random matches score ~26-29 inliers / 0.25-0.33).
        if _agent_ports_sidecar(src) is not None and r.get("ports_ok") is not False \
                and r.get("layout_ok") is not False and r.get("open_ok") is not False \
                and r["inliers"] >= 30 and r["confidence"] >= 0.4:
            print(f"  accepting the agent template on its own match (near-miss on our "
                  f"aligner: {r['inliers']} points, {r['confidence']:.2f})")
            return True
        return False

    if best is None or not _accept(best[1], best[2]):
        shutil.rmtree(work, ignore_errors=True)
        return None, None, None
    dest = tdir / key
    if not in_catalog(vendor, model):
        # used for this photo only: a model the catalogue does not know (often a garbled read)
        # is never saved, or every later photo would try that template first
        keep = Path(tempfile.mkdtemp(prefix="switchtpl_unsaved_")) / key
        shutil.copytree(best[0], keep)
        shutil.rmtree(work, ignore_errors=True)
        print(f"Template used for this photo only (not saved): {vendor} {model} is not in the catalogue")
        return ms.Template(keep), best[1], best[2]
    if dest.exists() and (dest / "template.json").exists():
        # an existing template is replaced only by a better fit on this photo
        old_t = ms.Template(dest)
        r_old = ms.align(old_t, gray, photo_ports=photo_ports)
        if ms.passes(r_old) and r_old["confidence"] >= best[1]["confidence"]:
            shutil.rmtree(work, ignore_errors=True)
            print(f"Kept the saved template (fits as well or better: {r_old['confidence']:.2f})")
            return old_t, r_old, old_t.meta.get("image_source")
        backup = tdir.parent / "templates_replaced" / f"{key}_{int(time.time())}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(backup))
        print(f"  the saved template fits worse ({r_old['confidence']:.2f}); moved to {backup}")
    elif dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(best[0], dest)
    shutil.rmtree(work, ignore_errors=True)
    print(f"Saved new template: {dest}")
    t = ms.Template(dest)
    return t, best[1], best[2]


if __name__ == "__main__":
    code = 0
    try:
        main()
        finish_result()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (1 if e.code else 0)
        if code and isinstance(e.code, str):
            print(e.code)
    except Exception as e:                 # every failure still leaves a result.json with status error
        import traceback
        traceback.print_exc()
        code = 1
        if RUN.get("out"):
            write_error(RUN["out"], RUN.get("photo"), f"{type(e).__name__}: {e}")
    finally:
        for d in RUN_TEMP:                 # a run without --learn leaves nothing behind
            shutil.rmtree(d, ignore_errors=True)
    # the result is written: exit now, before the OCR runtime and PyTorch tear their threads down
    # (on macOS that can abort with exit code 134 after a good result). PORT_AGENT_NORMAL_EXIT=1
    # keeps the normal exit (for tracing tools)
    if not os.environ.get("PORT_AGENT_NORMAL_EXIT"):
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
    sys.exit(code)
