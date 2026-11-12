"""Router mapper: a photo (vendor + model optional) in, the photo with every router port numbered out.

    1  identify  make and model typed, or read off the photo (router_mapper.ocr: the switch
                 mapper's two-stage OCR + catalog matcher, loaded with the router catalog);
                 vendor read but model not: the vendor's saved templates are tried against
                 the photo. The router catalog then fixes the spelling
    2  fetch     candidate images of the model: the template agent's pick (device="router"),
                 the model's other images in the router store, and online images; only the
                 ones showing network ports are kept, best match with the photo first
    3  visible   does the photo show network ports? If not, the face in the photo has none
                 (ports on the back) and nothing is mapped - ports that are not visible
                 cannot be mapped
    4  template  for each candidate the ported face is cut out and its ports found
    5  fit       switch_fit lays it over the photo, checks the ports land on the ports found
                 in the photo and snaps each box onto its port; the first candidate that
                 passes wins and is kept in the router store (preferred next time). If none
                 fits, the ports found in the photo are numbered directly (auto mode)

Outputs in out_dir: 1_photo.jpg, 2_fetched.png, 3_template.png, 4_overlay.png,
result.json (each port's id, type, status and 4 corners in photo pixels).
Routers only: its own template store, templates folder and output folder.
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "switch_template_agent"))

import yolo_ports  # noqa: E402
from router_mapper import catalog  # noqa: E402
from router_mapper import ports as rports  # noqa: E402
from router_mapper import template as rtemplate  # noqa: E402
from router_mapper.fitting import use_router_limits  # noqa: E402
import device_spec  # noqa: E402

TEMPLATES = ROOT / "router_templates"
OUTPUT = ROOT / "output" / "router_runs"
MIN_VISIBLE_PORTS = 2      # network ports a face needs to be mappable (router_template.py)
MIN_CONF = 0.5
MAX_TRIES = 4           # candidate templates fitted per run
PORTS_AGREE = 0.8       # share of template / photo ports that coincide for a "confident" fit
MIN_PAIRS_ACCEPT = 3    # template / photo port pairs that must line up to accept a fit
MAX_PORT_ERR = 0.2      # their mean offset, in port widths (angled rack photos; snapping fixes the rest)
STRONG_CONF = 0.7       # image-feature match that proves the panel on its own ...
STRONG_POINTS = 60      # ... with this many matching points ...
GOOD_QUALITY = 0.7      # ... for a clean, complete, straight-on template (image_quality)
MAX_ONLINE = 10         # online images downloaded per run
CHASSIS_REFS = 4        # other images of the model compared to find its chassis ports


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def network_ports(bgr):
    """(count, {type: n}) of confident RJ45/SFP/QSFP detections on an image."""
    dets = [d for d in yolo_ports.raw_detections(bgr)
            if d["label"] in yolo_ports.DATA_PORTS and (d["conf"] >= MIN_CONF or d.get("recovered"))]
    by = {}
    for d in dets:
        k = yolo_ports.DATA_PORTS[d["label"]]
        by[k] = by.get(k, 0) + 1
    return len(dets), by


def map_router(photo_path, vendor=None, model=None, out_dir=None, log=print, refresh=False, _retry=False,
               learn=False):
    """Never raises for a bad photo or an internal failure: the result then has mapped=False and
    an 'error' field, and result.json is written as for any other run.
    learn=False (default): nothing outside out_dir is written - a template that fits is used from
    out_dir/template; learn=True also saves it (router_templates/, the template store) for next time."""
    out = Path(out_dir or OUTPUT / Path(str(photo_path)).stem)
    # for this run only: with learn off the template store is read-only and the page / sitemap /
    # datasheet caches go into out/_cache (unless PORT_AGENT_CACHE_DIR names a lasting folder), so
    # nothing outside out_dir is written (review #12)
    saved_env = {k: os.environ.get(k) for k in ("PORT_AGENT_STORE_WRITES", "PORT_AGENT_CACHE_DIR")}
    os.environ["PORT_AGENT_STORE_WRITES"] = "1" if learn else "0"
    run_cache = None
    if not learn and not saved_env["PORT_AGENT_CACHE_DIR"]:
        run_cache = out / "_cache"
        os.environ["PORT_AGENT_CACHE_DIR"] = str(run_cache)
    try:
        res = _map_router(photo_path, vendor, model, out_dir, log, refresh, _retry, learn)
        if not _retry:
            res = confirm_given(res, photo_path, vendor, model, log)
            # the spec of the identified model (ports by type, rack units, data sheet), checked
            # against the ports mapped on the photo
            res = device_spec.add_spec(res, "router", log)
            if (out / 'result.json').exists():
                (out / 'result.json').write_text(json.dumps(res, indent=1))
        return res
    except Exception as e:      # noqa: BLE001 - the caller always gets a result
        import traceback
        out.mkdir(parents=True, exist_ok=True)
        result = {"vendor": vendor, "model": model, "mapped": False,
                  "error": str(e) if isinstance(e, ValueError) else f"{type(e).__name__}: {e}",
                  "error_trace": traceback.format_exc(limit=6)}
        normalise_result(result)
        (out / "result.json").write_text(json.dumps(result, indent=1))
        log(f"ERROR {result['error']}")
        return result
    finally:
        shutil.rmtree(out / "_candidates", ignore_errors=True)     # try folders never outlive the run
        if run_cache is not None:
            shutil.rmtree(run_cache, ignore_errors=True)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _map_router(photo_path, vendor=None, model=None, out_dir=None, log=print, refresh=False, _retry=False,
                learn=False):
    t0 = time.time()
    out = Path(out_dir or OUTPUT / Path(photo_path).stem)
    out.mkdir(parents=True, exist_ok=True)
    photo = cv2.imread(str(photo_path), cv2.IMREAD_COLOR)
    if photo is None:
        raise ValueError(f"could not read the photo {photo_path}")
    if photo.shape[0] < 16 or photo.shape[1] < 16:      # the same answer as the switch agent
        raise ValueError(f"the photo is too small to map ({photo.shape[1]} x {photo.shape[0]} px)")
    cv2.imwrite(str(out / "1_photo.jpg"), photo)
    result = {"vendor": vendor, "model": model, "device": "router", "mapped": False, "steps": {}}

    def done(reason=None):
        if reason:
            result["reason"] = reason
            log(f"RESULT {reason}")
        result["seconds"] = round(time.time() - t0, 1)
        normalise_result(result)
        (out / "result.json").write_text(json.dumps(result, indent=1))
        return result

    # 1. make and model: typed values win; what is missing is read off the photo (OCR)
    log("STEP 1 identify: make and model")
    variant = None              # the exact product ID, when it is a variant of a catalogue model
    if not (vendor and model):
        from router_mapper import ocr
        log("  reading the panel text (OCR)...")
        r = ocr.identify(photo, log)
        result["identify"] = {k: r[k] for k in ("vendor", "model", "found", "how", "raw")}
        # how sure the read is (0-1), apart from the fit's confidence: a caller can treat a weak
        # or doubtful make / model as unconfirmed (vendor_doubt: another brand read almost as well)
        raw = r.get("raw") or {}
        result["identify_confidence"] = {"vendor": raw.get("vendor_score") if r.get("vendor") else 0.0,
                                         "model": raw.get("model_score") if r.get("model") else 0.0,
                                         "vendor_doubt": raw.get("vendor_runner_up")}
        log(f"  OCR: {r['vendor'] or '?'} {r['model'] or '?'}" + (f" ({r['how']})" if r["how"] else ""))
        typed = bool(vendor or model)
        vendor, model = vendor or r["vendor"], model or r["model"]
        variant = r.get("variant") if not typed else None
        result["identified_by"] = "typed + OCR" if typed else "OCR"
    else:
        result["identified_by"] = "typed"
    if not yolo_ports.available():
        return done("the port model (best_merged_ports*.pt + ultralytics) is not available")
    photo_ports, pinfo = rports.detect(photo)
    if vendor and not model:
        log(f"  model not readable; trying the saved {vendor} templates against the photo")
        hit = visual_identify(vendor, photo, photo_ports, log)
        if hit:
            model = hit
            result["identified_by"] += " + visual match to a saved template"
    result.update(vendor=vendor, model=model)
    if not (vendor and model):
        log("  no make and model; numbering the ports found in the photo instead")
        return auto_mode(photo, photo_ports, out, result, done,
                         reason=f"make and model not identified (read: {vendor or '?'} {model or '?'}); "
                                "ports numbered from the photo alone - type them to use a template")
    typed_model = model
    c = catalog.resolve(vendor, model)
    vendor, model = c["vendor"], c["model"]
    # a typed product ID that resolved to its base model (C1161-8PLTEP -> C1161-8P) is a variant
    if not variant and c["found"] and catalog.key(typed_model) != catalog.key(model) \
            and catalog.key(typed_model) in {catalog.key(x) for x in (c["row"]["variants"] or "").split(",")}:
        variant = typed_model.strip().upper()
    result.update(vendor=vendor, model=model, in_catalog=c["found"], variant=variant)
    if variant:
        log(f"  exact product read: {variant} (catalog model {model}); its panel is searched for first")
    if c["found"]:
        row = c["row"]
        result["catalog"] = {k: row[k] for k in ("series", "category", "form", "status", "source")}
        log(f"  {vendor} {model}: in the catalog ({row['series']}, {row['status']})")
    else:
        result["catalog_suggestions"] = c["suggestions"]
        log(f"  {vendor} {model}: not in the router catalog"
            + (f" (close: {', '.join(c['suggestions'][:3])})" if c["suggestions"] else "") + "; looking it up anyway")

    n_photo, by_photo = network_ports(photo)
    result["steps"]["photo_ports"] = {"network_ports": n_photo, "by_type": by_photo}
    log(f"  photo: {n_photo} network ports seen {by_photo}")

    # 2a. saved locally from an earlier run of this model (or exact variant): tried first,
    # without visiting the vendor's site or the web - as the switch mapper reuses its templates
    tdir = TEMPLATES / f"{slug(vendor)}_{slug(variant or model)}"
    # the exact variant's template first, then the base model's: a C1161-8PLTEP read has the
    # same panel as the saved cisco_c1161-8p and must not go online for it
    saved = [d for d in dict.fromkeys([tdir, TEMPLATES / f"{slug(vendor)}_{slug(model)}"])
             if (d / "template.json").exists()]
    for sdir in ([] if refresh else saved):
        local = try_local(sdir, photo, photo_ports, log)
        if local:
            fit, meta, chassis = local
            result["from_local"] = True
            if sdir != tdir:
                log(f"  the base model's saved template fits the {variant} photo")
            src = meta.get("image_source", "")
            fetched = cv2.imread(str(sdir / "source.png")) if (sdir / "source.png").exists() else None
            return finish(result, out, photo, photo_ports, fit, meta, chassis, sdir, src,
                          f"saved template ({meta.get('saved_label', 'earlier run')})", fetched, log, done)

    # 2. candidate template images: the agent's pick, the model's other stored images, and
    # online images - kept only when they show network ports, best photo match first
    log(f"STEP 2 fetch: looking for {vendor} {model} images of the ported face")
    cands = template_candidates(vendor, model, photo, log, refresh=refresh, variant=variant,
                                series=(c["row"] or {}).get("series") if c["found"] else None)
    result["steps"]["fetch"] = {"candidates": [{k: c[k] for k in ("label", "src", "ports", "inliers", "quality",
                                                                  "front_view", "complete", "clean", "trust")}
                                               for c in cands],
                                "left_out_other_models": getattr(template_candidates, "dropped", [])}

    # 3. visible: the photo shows the face being mapped
    log("STEP 3 visible: does the photo show network ports?")
    with_ports = [c for c in cands if c["ports"] >= MIN_VISIBLE_PORTS]
    if any(c["complete"] for c in with_ports):            # never a half panel while a whole one exists
        with_ports = [c for c in with_ports if c["complete"]]
    if n_photo < MIN_VISIBLE_PORTS and not with_ports:
        result["steps"]["visible"] = {"photo_network_ports": n_photo, "verdict": "no"}
        return done(f"the {vendor} {model} shows no network ports on the photographed face and no image of a "
                    "face with ports was found (ports on the back?); ports that are not visible cannot be mapped")
    if n_photo < MIN_VISIBLE_PORTS:
        # the port model misses jacks on many router panels (cables, module cards): the
        # templates are still tried, and only a strong image match can map this photo
        result["steps"]["visible"] = {"photo_network_ports": n_photo, "verdict": "unsure"}
        log(f"  the port model sees only {n_photo} network port(s) in the photo; trying the templates anyway")
    else:
        result["steps"]["visible"] = {"photo_network_ports": n_photo, "verdict": "yes"}
        log(f"  yes: {n_photo} network ports in the photo")
    if not with_ports:
        return done(f"no {vendor} {model} image showing its network ports was found; "
                    "numbering needs a template - give an image URL or check the model name")

    # 4 + 5. build a template from each candidate, fit it; the first that passes wins
    use_router_limits()
    import switch_fit
    best = None
    for i, c in enumerate(with_ports[:MAX_TRIES]):
        log(f"STEP 4 template: candidate {i + 1}/{min(len(with_ports), MAX_TRIES)} ({c['label']}, "
            f"{c['ports']} ports, quality {c['quality']}{'' if c['complete'] else ', panel cut off'}, "
            f"{c['inliers']} matching points) {c['src'][:90]}")
        cdir = out / "_candidates" / f"try{i}"         # in the run's own folder, removed at the end
        try:
            meta = rtemplate.build(c["img"], cdir, vendor, model, source=c["src"])
        except ValueError as e:
            log(f"  skipped: {e}")
            continue
        log(f"  {meta['summary']}")
        log("STEP 5 fit: laying the template over the photo")
        fit = switch_fit.fit(photo, str(cdir), photo_ports=photo_ports, draw=True)
        a = fit.get("port_check") or {}
        agree = min(a.get("on", 0), a.get("covered", 0)) if a else 0
        accepted, why = accept(fit, c["quality"], bool(c["complete"]))
        fit = dict(fit, verdict="confident" if accepted and agree >= PORTS_AGREE else "check",
                   reasons=[why] if why else [])
        score = (accepted, bool(c["complete"]), round(c["quality"], 1), a.get("covered", 0), a.get("on", 0),
                 a.get("pairs", 0), -(a.get("err") or 1))
        log(f"  {'accepted' if accepted else 'rejected'} ({fit.get('verdict')}, image match {fit.get('confidence')} / "
            f"{fit.get('matched_points')} points, {a.get('pairs', 0)} ports line up"
            + (f" within {a.get('err')} port widths" if a.get("err") is not None else "")
            + (f", template {a.get('on', 0):.0%} / photo {a.get('covered', 0):.0%} explained" if a else "")
            + (f": {'; '.join(fit.get('reasons') or [])}" if fit.get("reasons") else "") + ")")
        if fit.get("homography") and (best is None or score > best[0]):
            best = (score, c, cdir, fit, meta)
        if accepted and agree >= PORTS_AGREE:
            break

    if best and best[0][0]:
        _, c, cdir, fit, meta = best
        log("STEP 5 chassis: which template ports belong to every unit of this model?")
        chassis = chassis_votes(cdir, [x for x in with_ports if x is not c][:CHASSIS_REFS], log)
        if learn:
            tdir.mkdir(parents=True, exist_ok=True)
            for f in ("front.png", "template.json"):          # the winner becomes the model's template
                shutil.copy(cdir / f, tdir / f)
            save_local(tdir, c, chassis)                      # next time: no website, no web search
            remember(vendor, model, c)
            used = tdir
        else:
            used = out / "template"                           # this run only: nothing written outside out/
            shutil.rmtree(used, ignore_errors=True)
            used.mkdir(parents=True)
            for f in ("front.png", "template.json"):
                shutil.copy(cdir / f, used / f)
            save_local(used, c, chassis)
        result["learned"] = bool(learn)
        return finish(result, out, photo, photo_ports, fit, meta, chassis, used, c["src"], c["label"], c["img"],
                      log, done)

    if result.get("identified_by") == "OCR" and not _retry:
        # the model was read, not typed, and none of its images fit: maybe a misread
        # (2911 read as 857). The vendor's saved templates are tried against the photo.
        hit = visual_identify(vendor, photo, photo_ports, log, exclude=model)
        if hit:
            log(f"  OCR read {model}, but the photo matches the saved {vendor} {hit} template; using that")
            r2 = map_router(photo_path, vendor, hit, out_dir=out, log=log, refresh=refresh, _retry=True, learn=learn)
            r2["identified_by"] = f"OCR read {model}; corrected to {hit} by a saved template matching the photo"
            (out / "result.json").write_text(json.dumps(r2, indent=1))
            return r2
    if with_ports:
        cv2.imwrite(str(out / "2_fetched.png"), with_ports[0]["img"])
        result["steps"]["fetch"]["shown_not_used"] = with_ports[0]["src"]
    log("  no template lines up with the photo; numbering the ports found in the photo instead")
    return auto_mode(photo, photo_ports, out, result, done)


def accept(fit, quality=0.0, complete=True):
    """(accepted, reason) for one template fit. Routers differ from switches: the same model
    carries different module cards from unit to unit (an ASR1001's SPA, an ISR's EHWICs), so
    the photo and the template need not show the same ports. What proves it is the same
    hardware is precision: the image features match and the ports that both show coincide
    within a fraction of a port width, with the same types. Ports only the photo shows are
    added from the photo (add_photo_ports); ports only the template shows may be under cables."""
    a = fit.get("port_check")
    if not fit.get("homography"):
        return False, "no alignment found"
    strong = (fit.get("confidence") or 0) >= STRONG_CONF and (fit.get("matched_points") or 0) >= STRONG_POINTS \
        and fit.get("aligned_by") == "features"
    # with 3+ ports detected in the photo and none landing, the template is another face: never
    # accepted on image features alone
    none_land = bool(a) and not a["pairs"] and a.get("detections", 0) >= 3
    if strong and not none_land and quality >= GOOD_QUALITY and complete and \
            (not a or not a["pairs"] or (a.get("err") or 0) <= MAX_PORT_ERR):
        # the port model misses or mislabels jacks on many router panels (cables, module
        # cards, RJ45 read as SFP); a clean, complete template that matches the photo's image
        # features this strongly is the same panel - the switch flow trusts the same evidence
        return True, (f"strong image match (confidence {fit.get('confidence')}, {fit.get('matched_points')} points); "
                      f"{a['pairs'] if a else 0} ports confirmed by the port model")
    if not a:
        return False, "too few ports detected in the photo to check the fit"
    if a["pairs"] < MIN_PAIRS_ACCEPT:
        return False, f"only {a['pairs']} ports line up (need {MIN_PAIRS_ACCEPT})"
    if (a.get("err") or 1) > MAX_PORT_ERR:
        return False, f"ports are {a['err']} port widths off (max {MAX_PORT_ERR})"
    if a.get("same_kind", 0) < 0.5 and a["pairs"] >= 3:
        return False, "port types disagree (RJ45 template ports on SFP cages or the reverse)"
    if a["on"] < 0.5:
        return False, f"only {a['on']:.0%} of the template's ports land on ports in the photo"
    if (fit.get("confidence") or 0) < 0.5 and fit.get("aligned_by") != "ports":
        return False, f"image features match weakly (confidence {fit.get('confidence')})"
    if min(a["on"], a["covered"]) < PORTS_AGREE:
        return True, (f"{a['covered']:.0%} of the photo's ports and {a['on']:.0%} of the template's agree "
                      "(different module cards, or ports under cables)")
    return True, ""


def save_local(tdir, c, chassis):
    """Keep what the next run of this model needs offline: the source image, and in
    template.json which ports are chassis (the comparison with the model's other images)."""
    votes, aligned = chassis
    cv2.imwrite(str(Path(tdir) / "source.png"), c["img"])
    meta = json.loads((Path(tdir) / "template.json").read_text())
    meta.update(chassis_votes={str(k): v for k, v in votes.items()}, chassis_aligned=aligned,
                saved_quality=c["quality"], saved_label=c["label"], saved_at=time.strftime("%Y-%m-%d %H:%M"))
    (Path(tdir) / "template.json").write_text(json.dumps(meta, indent=1))


def try_local(tdir, photo, photo_ports, log):
    """The model's saved template laid over the photo, no network. (fit, meta, chassis) when it
    is accepted (same rule as a fresh template), else None - then the vendor site / web is searched."""
    use_router_limits()
    import switch_fit
    meta = json.loads((Path(tdir) / "template.json").read_text())
    log(f"STEP 2 fetch: saved template found locally ({meta.get('saved_at', 'earlier run')}) - trying it first, no web")
    fit = switch_fit.fit(photo, str(tdir), photo_ports=photo_ports, draw=True)
    a = fit.get("port_check") or {}
    agree = min(a.get("on", 0), a.get("covered", 0)) if a else 0
    accepted, why = accept(fit, meta.get("saved_quality", GOOD_QUALITY), True)
    if not accepted:
        log(f"  the saved template does not fit this photo ({why}); searching the vendor's site and the web")
        return None
    fit = dict(fit, verdict="confident" if agree >= PORTS_AGREE else "check", reasons=[why] if why else [])
    votes = {(int(k) if str(k).isdigit() else k): v for k, v in (meta.get("chassis_votes") or {}).items()}
    log(f"  saved template fits ({fit['verdict']}, image match {fit.get('confidence')})")
    return fit, meta, (votes, meta.get("chassis_aligned", 0))


def finish(result, out, photo, photo_ports, fit, meta, chassis, tdir, src, label, fetched, log, done):
    """Chassis ports from the template, module ports from the photo, the overlay and result."""
    fit = keep_chassis_ports(fit, meta, chassis, photo_ports, log)
    fit = add_photo_ports(fit, photo, photo_ports)
    fit = dict(fit, overlay=draw_ports(photo, fit["ports"]))
    if fetched is not None:
        cv2.imwrite(str(out / "2_fetched.png"), fetched)
    cv2.imwrite(str(out / "3_template.png"), rtemplate.draw(tdir))
    cv2.imwrite(str(out / "4_overlay.png"), fit["overlay"])
    result["steps"]["template"] = {"folder": str(tdir), "summary": meta["summary"], "source": src}
    result["steps"]["fit"] = {k: fit.get(k) for k in ("ok", "verdict", "reasons", "confidence",
                                                     "matched_points", "aligned_by", "port_check", "snapped")}
    result.update(mapped=True, mode="template", verdict=fit["verdict"], homography=fit["homography"],
                  ports=fit["ports"], template_source=src)
    result["added_from_photo"] = fit.get("added_from_photo", 0)
    log(f"RESULT mapped with the template from {label}: {fit['verdict']}, {len(fit['ports'])} ports"
        + (f" ({fit['added_from_photo']} added from the photo: a module the template lacks)" if fit.get("added_from_photo") else "")
        + (f" - {fit['reasons'][0]}" if fit.get("reasons") else ""))
    if fit.get("reasons"):
        result["reason"] = fit["reasons"][0]
    return done()


def _centre(poly):
    return np.float32(poly).mean(axis=0)


def _confirmed(meta, H, dets):
    """Template port ids with a detected port (any kind) at their projected place."""
    from router_mapper.fitting import family
    dets = [d for d in dets or [] if d.get("status") != "not seen" and family(d["type"])]
    if not dets:
        return set()
    pw = float(np.median([d["box"][2] for d in dets if d["type"] in ("rj45", "sfp")] or [d["box"][2] for d in dets]))
    dc = np.float32([(d["box"][0] + d["box"][2] / 2, d["box"][1] + d["box"][3] / 2) for d in dets])
    out = set()
    for p in meta["ports"]:
        x, y, w, h = p["box"]
        c = cv2.perspectiveTransform(np.float32([[[x + w / 2, y + h / 2]]]), np.asarray(H, np.float64)).reshape(2)
        if float(np.min(np.linalg.norm(dc - c, axis=1))) < 0.6 * pw:
            out.add(p["id"])
    return out


def chassis_votes(win_dir, others, log):
    """For each port of the winning template, in how many of the model's OTHER images it is
    found at the same place. A port every unit has (FE0/0, CONSOLE, USB on a Cisco 1841)
    is chassis; a port only one image shows sits on a module card that unit happened to
    carry. Returns ({port id: votes}, number of images aligned)."""
    import switch_fit
    meta = json.loads((Path(win_dir) / "template.json").read_text())
    votes, aligned = {p["id"]: 0 for p in meta["ports"]}, 0
    for c in others:
        dets, _ = rports.detect(c["img"])
        f = switch_fit.fit(c["img"], str(win_dir), photo_ports=dets, draw=False)
        if not f.get("homography") or (f.get("confidence") or 0) < 0.55:
            continue
        hit = _confirmed(meta, f["homography"], dets)
        if len(hit) < 2:
            continue
        aligned += 1
        for pid in hit:
            votes[pid] += 1
    log(f"  compared with {aligned} other image(s) of the model: "
        + (", ".join(f"{pid} {v}/{aligned}" for pid, v in votes.items()) if aligned else "none lined up"))
    return votes, aligned


def keep_chassis_ports(fit, meta, chassis, photo_ports, log):
    """Template ports kept: chassis ports (found on other units too), and any port the photo
    itself shows. Dropped: ports only the template image had and the photo does not show -
    a module card of that unit. Data ports are renumbered 1.. in template order."""
    votes, aligned = chassis
    in_photo = _confirmed(meta, fit["homography"], photo_ports)
    names = {p["id"]: p.get("name") for p in meta["ports"] if p.get("name")}
    keep, dropped = [], []
    for p in fit["ports"]:
        is_chassis = aligned == 0 or votes.get(p["id"], 0) >= 1
        if is_chassis or p["id"] in in_photo:
            keep.append(dict(p, source="template", chassis=bool(is_chassis and aligned),
                             **({"name": names[p["id"]]} if p["id"] in names else {})))
        else:
            dropped.append(p["id"])
    if dropped:
        log(f"  left out {len(dropped)} template port(s) on a module this unit does not have: {dropped}")
    n = 0
    for p in keep:
        if isinstance(p["id"], int):
            n += 1
            p["id"] = n
    return dict(fit, ports=keep, dropped_module_ports=dropped)


def add_photo_ports(fit, photo, photo_ports):
    """Ports the photo shows that no kept template port covers - a module card the
    template's unit lacked: added with the next numbers, source 'photo'. Jacks the port
    model calls console or management that sit inside a module (a T1 card's RJ48) count
    too; a second console on a router is a module jack."""
    from router_mapper.fitting import family
    ports = fit["ports"]
    polys = [np.float32(p["polygon"]) for p in ports]
    tpl_polys = list(polys)
    pw = float(np.median([np.ptp(pg[:, 0]) for pg in polys])) if polys else 40.0
    named = {p["id"] for p in ports if isinstance(p["id"], str)}
    nxt = max([p["id"] for p in ports if isinstance(p["id"], int)], default=0) + 1
    extra = []
    for q in sorted([q for q in photo_ports or [] if q.get("status") != "not seen" and family(q["type"]) in ("jack", "cage")],
                    key=lambda q: q["box"][0]):
        x, y, w, h = q["box"]
        c = (float(x + w / 2), float(y + h / 2))
        poly = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
        if any(cv2.pointPolygonTest(pg, c, False) >= 0 for pg in polys):
            continue
        box = np.float32(poly)
        # a detection half over a port that is already there is that port, not another one
        if any(cv2.intersectConvexConvex(box, pg)[0] > 0.3 * w * h for pg in polys):
            continue
        # the router's own console / AUX sits beside its chassis ports; a 'console' out on a
        # module card is a T1/E1 or similar network jack the model mistook
        near_chassis = any(float(np.hypot(*(pg.mean(axis=0) - np.float32(c)))) < 1.6 * pw for pg in tpl_polys)
        if isinstance(q["id"], str) and q["id"] not in named and near_chassis:
            # the router's own console / management jack the template missed: keep its name
            extra.append({"id": q["id"], "type": q["type"], "status": q.get("status"), "source": "photo",
                          "added": True, "polygon": poly})
            named.add(q["id"])
        else:
            extra.append({"id": nxt, "type": q["type"] if q["type"] in ("rj45", "sfp") else "rj45",
                          "status": q.get("status"), "source": "photo", "added": True, "polygon": poly})
            nxt += 1
        polys.append(np.float32(poly))
    # module jacks the first pass was unsure of (T1/E1 jacks, SFPs under cables): kept when
    # they are the size and shape of this router's ports and sit level with them
    def bbox(poly):
        a = np.float32(poly)
        return [float(a[:, 0].min()), float(a[:, 1].min()), float(np.ptp(a[:, 0])), float(np.ptp(a[:, 1]))]
    known = [bbox(p["polygon"]) for p in ports + extra]
    for q in rports.module_port_candidates(photo, known):
        x, y, w, h = q["box"]
        extra.append({"id": nxt, "type": q["type"], "status": "connected", "source": "photo", "added": True,
                      "confidence": q["confidence"], "how": q["how"],
                      "polygon": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]})
        nxt += 1
    return dict(fit, ports=ports + extra, added_from_photo=len(extra))


def draw_ports(photo, ports):
    """The photo with every port boxed and labelled: chassis / template ports in teal,
    named ports (console, AUX, USB) in amber, ports added from the photo in purple."""
    up = max(1.0, min(3.0, 2400 / photo.shape[1]))
    img = cv2.resize(photo, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    for p in ports:
        pts = (np.float32(p["polygon"]) * up).astype(int)
        col = (200, 60, 200) if p.get("source") == "photo" else (0, 170, 255) if isinstance(p["id"], str) else (200, 200, 0)
        cv2.polylines(img, [pts], True, col, max(2, int(1.5 * up)))
        label = str(p.get("name") or p["id"])
        bw = float(np.ptp(pts[:, 0]))
        (w1, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 1)
        fs = max(0.3, min(0.45 * up, 0.9 * bw / max(w1, 1)))       # the label fits inside its box
        th_ = max(1, int(round(fs * 2)))
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, th_)
        cx, cy = pts.mean(axis=0).astype(int)
        cv2.rectangle(img, (cx - tw // 2 - 2, cy - th // 2 - 3), (cx + tw // 2 + 2, cy + th // 2 + 3), (20, 20, 20), -1)
        cv2.putText(img, label, (cx - tw // 2, cy + th // 2), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255),
                    th_, cv2.LINE_AA)
    return img


VISUAL_MIN_AGREE = 0.6    # share of ports that must agree to name a model from a saved template's look

def visual_identify(vendor, photo, photo_ports, log, exclude=None):
    """The vendor's saved router templates tried against the photo; the model of the best
    one that is accepted (accept(): ports line up, or a strong image match), or None."""
    import json as _json
    use_router_limits()
    import switch_fit
    best = None
    for d in sorted(TEMPLATES.glob(f"{slug(vendor)}_*")):
        if not (d / "template.json").exists():
            continue
        meta = _json.loads((d / "template.json").read_text())
        if exclude and catalog.key(meta["model"]) == catalog.key(exclude):
            continue
        fit = switch_fit.fit(photo, str(d), photo_ports=photo_ports, draw=False)
        a = fit.get("port_check") or {}
        agree = min(a.get("on", 0), a.get("covered", 0)) if a else 0
        score = (agree, fit.get("confidence") or 0)
        # naming the model from a look-alike needs more than mapping a known one: one vendor's models
        # share chassis, vents and logos (an F5 r12000 template matched an r5000 photo at 54% of
        # ports, boxes on the vents). With 3+ ports in the photo, most of them must agree
        if a and a.get("detections", 0) >= 3 and agree < VISUAL_MIN_AGREE:
            continue
        if accept(fit, 1.0, True)[0] and (best is None or score > best[0]):
            best = (score, meta["model"])
    if best:
        log(f"  matches the saved {vendor} {best[1]} template ({best[0][0]:.0%} of ports agree, "
            f"image match {best[0][1]})")
    return best[1] if best else None


def auto_mode(photo, photo_ports, out, result, done, reason=None):
    """No template fits: number the ports the port model found in the photo itself."""
    import sys as _sys
    import switch_fit  # noqa: F401  (loads switch_fit.fit)
    F = _sys.modules["switch_fit.fit"]
    photo_ports = rtemplate.clean_ports([dict(p) for p in photo_ports or []])
    if not photo_ports:
        return done("no template lines up with the photo and no ports were found in it")
    up = max(1.0, min(3.0, 2400 / photo.shape[1]))
    cv2.imwrite(str(out / "4_overlay.png"),
                cv2.cvtColor(__import__("numpy").asarray(F.draw_auto_overlay(photo, photo_ports, up)), cv2.COLOR_RGB2BGR))
    result.update(mapped=True, mode="auto", verdict="check",
                  ports=[{"id": p["id"], "type": p["type"], "status": p["status"],
                          "polygon": [[p["box"][0], p["box"][1]], [p["box"][0] + p["box"][2], p["box"][1]],
                                      [p["box"][0] + p["box"][2], p["box"][1] + p["box"][3]],
                                      [p["box"][0], p["box"][1] + p["box"][3]]]} for p in photo_ports])
    if reason:
        return done(reason)
    return done("no template lined up; ports numbered from the photo alone (auto mode) - check the numbering")


def template_candidates(vendor, model, photo, log, refresh=False, online=MAX_ONLINE, variant=None, series=None):
    """Images of the model that may serve as its template, each with how many network ports
    it shows and how well it matches the photo: [{label, src, img, ports, inliers}], the
    ones with ports and the best photo match first. The vendor's data sheet comes first
    (its figures are captioned with the model), then the exact variant read off the panel,
    then the model."""
    from template_agent import TemplateStore, find_template, find_template_images
    from template_agent.devices import device
    from template_agent.selector import _download, photo_match
    st = TemplateStore(device("router").store_dir)
    seen, out = set(), []
    others = [o for o in other_models(vendor, model) if not variant or o not in catalog.key(variant)]
    dropped = []

    def add(label, src, img, title="", page="", checked=False):
        if img is None or src in seen:
            return
        seen.add(src)
        named = None if checked else names_other_model(others, src, title, page)
        if named:
            dropped.append(f"{named} ({src})")
            return
        n, _ = network_ports(img)
        c = {"label": label, "src": src, "img": img, "ports": n,
             "inliers": photo_match(img, photo, "router") if n >= MIN_VISIBLE_PORTS else 0}
        c.update(image_quality(img, src, page, vendor, label))
        out.append(c)

    for src, img, caption, exact in datasheet_images(vendor, series, model, variant, log):
        add(f"official/datasheet ({caption[:40]})", src, img, caption, checked=True)
        if out and out[-1]["src"] == src:
            out[-1].update(datasheet=True, exact_variant=exact, trust=1.0)
    from router_mapper.official import official_images
    for src, img, where, exact, named in official_images(vendor, model, variant, series, log, others):
        add(f"official/{where}"[:90], src, img, where, checked=named)
        if out and out[-1]["src"] == src:
            # top rank only for images that name the model or come from its data sheet;
            # other pictures on the model's page stay ordinary (trusted) candidates
            out[-1].update(datasheet=named, exact_variant=exact and named, trust=1.0)
    if variant:                      # the exact product read off the panel, before its base model
        for c in find_template_images(vendor, variant, limit=online, device="router")[:online // 2]:
            img = _download(c.url)
            if img is not None and img.shape[1] >= 300:
                add(f"{c.tier}/{c.source} ({variant})", c.url, img, c.title, c.page)
    res = find_template(vendor, model, photo, device="router", exact=True, refresh=refresh)
    if res.path is not None:
        add(f"template agent ({res.source})", res.url or str(res.path), cv2.imread(str(res.path)), page=res.page)
    for t in st.find(vendor, model):
        add(f"router store #{t.id}", t.source_url or str(t.path), cv2.imread(str(t.path)), page=t.page_url)
    n_online = 0
    for c in find_template_images(vendor, model, limit=online * 2, device="router"):
        if n_online >= online:
            break
        if c.url in seen:
            continue
        img = _download(c.url)
        if img is not None and img.shape[1] >= 300:
            n_online += 1
            add(f"{c.tier}/{c.source}", c.url, img, c.title, c.page)
    if sum(c["ports"] >= MIN_VISIBLE_PORTS for c in out) < 2:
        # the web shows this model's other face (a Cisco ISR's bezel): look for the side with
        # the ports by name - routers often call it the rear or back panel
        from template_agent.sources import image_search, names_model
        from urllib.parse import urlparse
        log("  few images show its ports; searching for its front and rear panels")
        extra = 0
        for q in (f"{vendor} {model} front panel", f"{vendor} {model} rear", f"{vendor} {model} back panel",
                  f"{model} ports"):
            for h in image_search(q):
                if extra >= online or h.url in seen:
                    continue
                if not (names_model(urlparse(h.url).path, model) or names_model(h.title, model)
                        or names_model(urlparse(h.page).path, model)):
                    continue
                img = _download(h.url)
                if img is not None and img.shape[1] >= 300:
                    extra += 1
                    add("internet/panel_search", h.url, img, h.title, h.page)
    # like the switch agent: a clean, straight-on, complete front panel from a trusted source
    # first; the match with the photo decides between images of similar quality
    out.sort(key=rank, reverse=True)
    if dropped:
        log(f"  left out {len(dropped)} image(s) of other models: {', '.join(d.split(' (')[0] for d in dropped[:5])}")
    log(f"  {len(out)} images, {sum(c['ports'] >= MIN_VISIBLE_PORTS for c in out)} show network ports")
    template_candidates.dropped = dropped
    return out


def other_models(vendor, model):
    """Squashed names of the vendor's OTHER catalog models an image may be named for: longer
    names starting with this one (ASR1001-X, ASR1001-HX for ASR1001) and any other model of
    5+ characters that does not occur inside this model's own name (CISCO1841 for a 1921)."""
    mk = catalog.key(model)
    v = catalog.find_vendor(vendor) or vendor
    keys = {catalog.key(r["model"]) for r in catalog.models_of(v)} - {mk}
    return sorted((k for k in keys if len(k) >= 5 and k not in mk), key=len, reverse=True)


TRUSTED_SHOPS = ("router-switch.com", "cdw.com", "insight.com", "provantage.com", "bhphotovideo.com",
                 "itprice.com", "senetic", "ebuyer", "comms-express", "linitx.com", "dlink", "ui.com")


def image_quality(img, src, page, vendor, label):
    """How usable an image is as a template, the switch agent's way: front_view (straight-on
    panel of the right shape, level port rows), complete (the panel is not cut off by the
    frame - a video still with half a router fails), clean (catalog shot on a plain
    background), trust (vendor site > shops > video / social / auction sites)."""
    from template_agent.selector import front_view_score
    from template_agent.sources import LOW_TRUST, is_official
    from template_agent.vision.vendor import _light_background
    fv, _ = front_view_score(img, device="router")
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    clean = _light_background(g)
    complete = 1.0
    fp = rtemplate.front_panel(img)
    if fp is not None:
        crop, (_, y0) = fp
        band = img[y0:y0 + crop.shape[0]].astype(np.int16)
        H, W = img.shape[:2]
        k = max(3, min(H, W) // 40)
        corners = np.concatenate([img[:k, :k].reshape(-1, 3), img[:k, -k:].reshape(-1, 3),
                                  img[-k:, :k].reshape(-1, 3), img[-k:, -k:].reshape(-1, 3)]).astype(np.int16)
        bg = np.median(corners, axis=0)
        body = (np.abs(band - bg).max(axis=2) > 35).mean(axis=0) > 0.5     # columns the device fills
        edge = max(2, W // 100)
        touches = int(body[:edge].mean() > 0.5) + int(body[-edge:].mean() > 0.5)
        # the router runs into one frame edge: cut off. Both edges: a tight crop of the panel
        # (complete) on a clean shot, or a scene photo cropped through the device (cut off)
        if touches == 1 or (touches == 2 and not clean):
            complete = 0.0
    host = str(src).lower()
    if any(h in host for h in LOW_TRUST) or "ebay" in host:
        trust = 0.0
    elif str(src).startswith("http") and (is_official(src, vendor) or is_official(page or "", vendor)):
        trust = 1.0
    elif label.startswith("router store") or any(h in host for h in TRUSTED_SHOPS):
        trust = 0.75
    else:
        trust = 0.5
    q = 0.4 * fv + 0.25 * trust + 0.2 * complete + 0.15 * clean
    return {"front_view": round(fv, 2), "complete": complete, "clean": bool(clean), "trust": trust,
            "quality": round(q, 3)}


def _datasheet_cache():
    """Read at call time: a run sets PORT_AGENT_CACHE_DIR for its own duration (map_router)."""
    return (Path(os.environ["PORT_AGENT_CACHE_DIR"]) / "datasheets" if os.environ.get("PORT_AGENT_CACHE_DIR")
            else ROOT / "router_catalog" / "scraped" / "datasheets")


def _cached_page(url, log):
    """A page's HTML through the real browser, cached on disk (vendor sites block scripts)."""
    import hashlib
    from template_agent.sources import _browser_get
    _datasheet_cache().mkdir(parents=True, exist_ok=True)
    f = _datasheet_cache() / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")
    h = f.read_text(errors="replace") if f.exists() else (_browser_get(url, settle_ms=4000) or "")
    if h and not f.exists():
        f.write_text(h)
    try:
        import device_spec
        device_spec.note_page(url, h)      # the spec step reads it too, with no search
    except ImportError:
        pass
    return h


def _face_text(text):
    """Caption text normalised for matching a model: 'C1161X- 8PLTEP' -> 'C1161-8PLTEP'. Cisco's X
    marks a memory option of the same box (identical panel), so it is dropped."""
    t = re.sub(r"\s*-\s*", "-", text)
    return re.sub(r"(?i)(?<=\d)X(?=-?\d)", "", t)


def datasheet_images(vendor, series, model, variant, log, max_pages=3):
    """Front-panel figures from the vendor's data sheet whose caption names the model or the
    exact variant: [(image url, image, caption, exact variant?)]. Cisco: the series support
    page (the one scraped for the catalogue) links its data sheets; each figure is followed
    by its caption ('Figure 3. C1161X-8PLTEP')."""
    import html as _html
    from urllib.parse import urljoin
    from template_agent.selector import _download
    from template_agent.sources import model_mention
    if vendor != "Cisco" or not series:
        return []
    try:
        from scrape_cisco import BASE, SERIES
    except Exception:
        return []
    path = SERIES.get(series)
    if not path:
        return []
    series_url = path if path.startswith("http") else BASE + path
    h = _cached_page(series_url, log)
    pages = list(dict.fromkeys(urljoin("https://www.cisco.com", u) for u in re.findall(
        r'href="([^"#]*(?:datasheet|data-sheet)[^"#]*\.html)"', h, re.I)))[:max_pages]
    names = [n for n in (variant, model) if n]
    found = []
    for page in pages:
        ph = _cached_page(page, log)
        for m in re.finditer(r"<img\b[^>]*>", ph):
            src = re.search(r'src="([^"]+)"', m.group(0))
            if not src:
                continue
            caption = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _html.unescape(ph[m.end():m.end() + 400]))).strip()
            head = _face_text(caption[:60])
            hit = next((n for n in names if model_mention(head, n) == "exact"), None)
            if not hit:
                continue
            url = urljoin(page, src.group(1))
            img = _download(url)
            if img is None:                       # cisco.com answers plain HTTP with 403
                from template_agent.sources import _browser_bytes
                from template_agent.vision.vendor import decode_image
                data = _browser_bytes(url)
                img = decode_image(data) if data else None
            if img is not None and img.shape[1] >= 300:
                found.append((url, img, caption[:80], bool(variant) and hit == variant))
    found.sort(key=lambda f: not f[3])           # the exact variant's figure first
    if found:
        log(f"  data sheet: {len(found)} figure(s) captioned {', '.join(dict.fromkeys(f[2][:30] for f in found))}")
    return found


def rank(c):
    """Candidates with ports first; complete panels before cut-off ones; then quality, with
    the photo match as a tie-breaker between images of similar quality."""
    return (c["ports"] >= MIN_VISIBLE_PORTS, c["complete"] > 0, bool(c.get("exact_variant")), bool(c.get("datasheet")),
            round(c["quality"] + 0.3 * min(c["inliers"], 60) / 60, 3))


def names_other_model(others, *texts):
    """The other model an image's address, title or page names, or None. Licence and bundle
    suffixes that are no model of their own (ASR1001-5G-SEC/K9) do not count."""
    from urllib.parse import unquote, urlparse
    for t in texts:
        t = unquote(str(t or ""))
        if t.startswith("http"):
            u = urlparse(t)
            t = u.path + " " + u.query
        k = catalog.key(t)
        for o in others:
            if o in k:
                return o.upper()
    return None


def remember(vendor, model, c):
    """Keep the image that fitted in the router store (marked as matched to a photo)."""
    from template_agent import TemplateStore
    from template_agent.devices import device
    st = TemplateStore(device("router").store_dir)
    ok, buf = cv2.imencode(".png", c["img"])
    t = st.add(vendor, model, buf.tobytes(), source="router_mapper/" + c["label"].split(" ")[0],
               source_url=c["src"] if str(c["src"]).startswith("http") else "", ports=c["ports"],
               width=c["img"].shape[1], height=c["img"].shape[0], note="fitted a site photo")
    st.mark_used(t.id, photo_matched=True)
    # not marked "preferred": one fitted photo does not prove the image right, and a preferred row is
    # tried first for that model ever after




def confirm_given(result, photo_path, vendor, model, log, **kw):
    """A typed make and model are a hint, never echoed back unchecked (review 2, #5): confirmed by the
    template fit, else by the panel text; identified_by 'given' when neither confirms them."""
    if not (vendor and model) or result.get("error") or result.get("identified_by") != "typed":
        return result
    if result.get("mapped") and result.get("mode") == "template":
        result["identified_by"] = "given, confirmed by the template fit"
        result["identify_confidence"] = {"vendor": 1.0, "model": 1.0, "vendor_doubt": None}
        return result
    try:
        import cv2
        import switch_identify as si
        from router_mapper import ocr
        photo = cv2.imread(str(photo_path), cv2.IMREAD_COLOR)
        r = ocr.identify(photo, lambda *a: None)
        text = [si.squash(t) for t in r.get("text") or [] if t]
        m = si.squash(model)
        ms = max([1.0 if m in t else si.best_substring_similarity(m, t)[0] for t in text] or [0.0])
        same = bool(r.get("vendor")) and catalog.vendor_key(r["vendor"]) == catalog.vendor_key(vendor)
        vs = ((r.get("raw") or {}).get("vendor_score") or 0.0) if same else 0.0
        result["identify_confidence"] = {"vendor": round(min(vs, 1.0), 3), "model": round(ms, 3),
                                         "vendor_doubt": r.get("vendor") if r.get("vendor") and not same else None}
        result["identified_by"] = "given, confirmed by the panel text" if (ms >= 0.9 or same) else "given"
        if r.get("vendor") and not same:
            log(f"  note: the given make {vendor} is not on the panel, which reads {r['vendor']} {r.get('model') or ''}")
    except Exception as e:      # noqa: BLE001 - confirming is extra; the mapping stands
        result["identified_by"] = "given"
        log(f"  could not check the given make / model against the panel: {e}")
    return result


def normalise_result(result):
    """The fields a caller reads, always present and with fixed values, so the integration never
    has to parse the 'reason' text (written for people). status is one of: mapped_template,
    mapped_auto, needs_back, not_mapped, error."""
    for k in ("needs_back", "from_local", "learned"):
        result.setdefault(k, False)
    result.setdefault("spec", None)            # set by device_spec.add_spec at the end of the run
    if result.get("error"):
        status = "error"
    elif result.get("needs_back"):
        status = "needs_back"
    elif result.get("mapped"):
        status = "mapped_template" if result.get("mode") == "template" else "mapped_auto"
    else:
        status = "not_mapped"
    result["status"] = status
    for p in result.get("ports") or []:
        if p.get("status") not in ("connected", "empty", "not seen"):
            p["status"] = "unknown"
        p.setdefault("source", "photo" if result.get("mode") != "template" else "template")
        p.setdefault("chassis", None)          # None: not known (no template to vote on it)
    return result

def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Map the ports of a router from one photo.")
    ap.add_argument("photo")
    ap.add_argument("--vendor", help="omit to read it off the photo (OCR)")
    ap.add_argument("--model", help="omit to read it off the photo (OCR)")
    ap.add_argument("--out")
    ap.add_argument("--refresh", action="store_true", help="ignore stored templates and search online")
    ap.add_argument("--learn", action="store_true",
                    help="save a template that fits (router_templates/, the template store) for next time")
    a = ap.parse_args(argv)
    r = map_router(a.photo, a.vendor, a.model, a.out, refresh=a.refresh, learn=a.learn)
    print(json.dumps({k: v for k, v in r.items() if k not in ("ports", "homography")}, indent=1))


if __name__ == "__main__":
    main()
