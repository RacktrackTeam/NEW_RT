#!/usr/bin/env python3
"""Build the Device Crop Atlas page from device_crops/manifest.csv (made by crop_classes.py).

Five tabs: Overview (all images), High resolution (images of 1 MP or more), Classifier, Other devices, Review.
Every number on the page is computed here from the manifest; the classifier and Other-device text cites its sources.

    python3 scripts/devices/build_crop_atlas.py OUT.html
"""
import base64, csv, functools, hashlib, io, json, pathlib, random, statistics as st, sys
from collections import Counter, defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
CROPS = ROOT / "data" / "device_crops"
NAMES = ["Firewall", "Gateway", "Load Balancer", "PDU", "Patch Panel", "Router", "Server", "Storage unit", "Switch", "UPS"]
MP = 1_000_000


def q(a, p):
    a = sorted(a)
    return a[round(p * (len(a) - 1))]


@functools.lru_cache(maxsize=None)
def quality(crop):
    """How clear a crop looks, for picking examples: sharp, straight-on (fills its polygon), well lit, big enough."""
    im = Image.open(CROPS / crop).convert("L")
    w, h = im.size
    if h < 40 or w < 80:
        return 0.0
    a = np.asarray(im.resize((max(8, round(w * 160 / h)), 160)), dtype=np.float32)
    m = a > 10                                          # outside the polygon is painted black
    if m.sum() < 500:
        return 0.0
    lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
    v = a[m]
    light = 1.0 if 55 <= v.mean() <= 205 else 0.4
    return float(np.log1p(lap[m[1:-1, 1:-1]].var()) * m.mean() ** 3 * light * min(1.0, h / 200) ** .5 * min(1.0, v.std() / 40))


SHARP_MIN = 450      # sharpness (Laplacian variance at 128 px height) below which a crop looks soft: never shown


@functools.lru_cache(maxsize=None)
def sharpness(crop):
    im = Image.open(CROPS / crop).convert("L"); w, h = im.size
    a = np.asarray(im.resize((max(8, round(w * 128 / h)), 128)), dtype=np.float32); m = a > 10
    lap = 4 * a[1:-1, 1:-1] - a[:-2, 1:-1] - a[2:, 1:-1] - a[1:-1, :-2] - a[1:-1, 2:]
    return float(lap[m[1:-1, 1:-1]].var()) if m.sum() > 500 else 0.0


def photo_of(crop):
    return crop.rsplit("/", 1)[-1].rsplit("__", 1)[0]


def clearest(rows, k, pool=200, shape=None, need=False):
    """The k clearest crops, one per photo, from the `pool` largest sharp ones (blurry crops are never shown);
    crops of the usual shape (or `shape`, width / height) come first. `need`: fall back to the sharpest if none pass."""
    sharp = [r for r in rows if sharpness(r["crop"]) >= SHARP_MIN]
    if not sharp and need:
        sharp = sorted(rows, key=lambda r: -sharpness(r["crop"]))[:1]
    rows = sharp
    asp = lambda r: int(r["crop_w"]) / max(1, int(r["crop_h"]))
    typical = shape or (st.median(asp(r) for r in rows) if rows else 1)
    rows = sorted(rows, key=lambda r: -int(r["crop_w"]) * int(r["crop_h"]))[:pool]
    out, seen = [], set()
    for r in sorted(rows, key=lambda r: -quality(r["crop"]) * np.exp(-abs(np.log(asp(r) / typical)))):
        if photo_of(r["crop"]) not in seen:
            out.append(r); seen.add(photo_of(r["crop"]))
        if len(out) == k:
            break
    return out


def thumb(r):
    im = Image.open(CROPS / r["crop"])
    im.thumbnail((520, 160))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=74)
    return [base64.b64encode(b.getvalue()).decode(), r["crop"], f"{r['crop_w']} \u00d7 {r['crop_h']}"]


def stats(rows, seed, n_thumbs=8):
    """Counts per class and split, image counts, resolution buckets, median crop size and sample crops."""
    photos, per_photo = defaultdict(set), Counter()
    for r in rows:
        photos[r["class"]].add(r["source_image"])
        per_photo[r["source_image"]] += 1
    out = {"total": len(rows), "photos": len(per_photo), "classes": []}
    for n in NAMES:
        rs = [r for r in rows if r["class"] == n]
        sp = Counter(r["split"] for r in rs)
        ps = {k: len({r["source_image"] for r in rs if r["split"] == k}) for k in ("train", "valid", "test")}
        out["classes"].append(dict(name=n, count=len(rs), train=sp["train"], valid=sp["valid"], test=sp["test"],
                                   photos=len(photos[n]), ptrain=ps["train"], pvalid=ps["valid"], ptest=ps["test"],
                                   w_med=st.median(int(r["crop_w"]) for r in rs), h_med=st.median(int(r["crop_h"]) for r in rs),
                                   w_lo=float(np.percentile([int(r["crop_w"]) for r in rs], 10)), w_hi=float(np.percentile([int(r["crop_w"]) for r in rs], 90)),
                                   h_lo=float(np.percentile([int(r["crop_h"]) for r in rs], 10)), h_hi=float(np.percentile([int(r["crop_h"]) for r in rs], 90))))
    cr = [r for r in rows if r["crop"]]                    # crop resolution: height and width bands, in pixels
    HB, WB = [0, 32, 64, 128, 256, 512, 1e9], [0, 128, 256, 512, 1024, 2048, 1e9]
    out["crop_bands"] = {"h": [[HB[i], HB[i + 1], sum(1 for r in cr if HB[i] <= int(r["crop_h"]) < HB[i + 1])] for i in range(len(HB) - 1)],
                         "w": [[WB[i], WB[i + 1], sum(1 for r in cr if WB[i] <= int(r["crop_w"]) < WB[i + 1])] for i in range(len(WB) - 1)],
                         "n": len(cr), "h_med": st.median(int(r["crop_h"]) for r in cr), "w_med": st.median(int(r["crop_w"]) for r in cr),
                         "under_box": sum(1 for r in cr if int(r["crop_h"]) < 128 and int(r["crop_w"]) < 512)}
    out["split_photos"] = {k: len({r["source_image"] for r in rows if r["split"] == k}) for k in ("train", "valid", "test")}
    out["split_inst"] = dict(Counter(r["split"] for r in rows))
    imgs = {r["source_image"]: int(r["image_w"]) * int(r["image_h"]) for r in rows}
    B = [0, .25, .5, 1, 2, 4, 8, 16, 1e9]
    out["mp"] = [sum(1 for a in imgs.values() if B[i] * MP <= a < B[i + 1] * MP) for i in range(len(B) - 1)]
    out["mp_ge1"] = sum(1 for a in imgs.values() if a >= MP)
    random.seed(seed)
    out["thumbs"] = {}
    for n in NAMES:
        pool = [r for r in rows if r["class"] == n and int(r["crop_w"]) >= 60]
        out["thumbs"][n] = [thumb(r) for r in clearest(pool, n_thumbs)]
    return out


def review(rows, seed=23):
    """Review tab: per class counts, crop width and height ranges, and ten sample crops."""
    random.seed(seed)
    out = {"classes": [], "med_w": int(st.median(int(r["crop_w"]) for r in rows)), "med_h": int(st.median(int(r["crop_h"]) for r in rows))}
    for n in NAMES:
        rs = [r for r in rows if r["class"] == n]
        w, h = [int(r["crop_w"]) for r in rs], [int(r["crop_h"]) for r in rs]
        sp = Counter(r["split"] for r in rs)
        pool = [r for r in rs if int(r["crop_w"]) >= 60]
        out["classes"].append(dict(name=n, count=len(rs), train=sp["train"], valid=sp["valid"], test=sp["test"],
                                   w=[min(w)] + [q(w, p) for p in (.1, .25, .5, .75, .9)] + [max(w)],
                                   h=[min(h)] + [q(h, p) for p in (.1, .25, .5, .75, .9)] + [max(h)],
                                   thumbs=[thumb(r) for r in clearest(pool, 10)]))
    return out


LABELS = {"convnext_tiny": "ConvNeXt-Tiny", "dinov2_b": "DINOv2-B", "efficientnetv2_s": "EfficientNetV2-S",
          "convnext_small": "ConvNeXt-Small"}


def results():
    """Training runs in models/device_cls/<model>/: finished ones have results.json, running ones only history.json."""
    runs = []
    for d in sorted((ROOT / "models" / "device_cls").glob("*/")):
        if d.name not in LABELS:
            continue
        r = {"arch": d.name, "label": LABELS[d.name], "status": "running", "history": []}
        if (d / "history.json").exists():
            r["history"] = json.load(open(d / "history.json"))
        if (d / "results.json").exists():
            r.update(json.load(open(d / "results.json")))
            r["status"] = "done"
        if r["history"] or r["status"] == "done":
            runs.append(r)
    return {"runs": runs, "classes": NAMES}


def run_detail(arch):
    """Live view of one run: history so far, metrics of the best epoch (from best.pt), test results when done."""
    import datetime, torch
    d = ROOT / "models" / "device_cls" / arch
    if not (d / "history.json").exists():
        return None
    r = {"arch": arch, "label": LABELS[arch], "status": "running", "history": json.load(open(d / "history.json")), "best": None, "test": None}
    if (d / "best.pt").exists():
        try:
            ck = torch.load(d / "best.pt", map_location="cpu", weights_only=True)
            r["best"] = {"epoch": ck["epoch"], "val": ck["val"]}; r["classes"] = list(ck["classes"])
        except Exception as e:  # best.pt being written right now
            r["best_error"] = str(e)
    if (d / "results.json").exists():
        res = json.load(open(d / "results.json"))
        r["status"] = "done"; r["test"] = res["test"]; r["best"] = {"epoch": res["best_epoch"], "val": res["val"]}; r["classes"] = res["classes"]
    log = (d / "log.txt").read_text().splitlines() if (d / "log.txt").exists() else []
    r["max_epochs"] = 30 if arch == "convnext_tiny" else 20
    r["last_step"] = next((l.strip() for l in reversed(log) if l.startswith("  ep ")), "")
    r["updated"] = datetime.datetime.now().strftime("%H:%M")
    vc = {}
    for row in csv.DictReader(open(CROPS / "manifest.csv")):
        if row["split"] == "valid" and int(row["image_w"]) * int(row["image_h"]) >= MP:
            vc[row["class"]] = vc.get(row["class"], 0) + 1
    r["valid_counts"] = [vc.get(n, 0) for n in NAMES]
    return r


def facts(hi):
    """Numbers the page text quotes, computed from the high-resolution crops."""
    from collections import Counter
    tr = Counter(r["class"] for r in hi if r["split"] == "train")
    learn = {c: n for c, n in tr.items() if n >= 20}
    big, small = max(learn, key=learn.get), min(learn, key=learn.get)
    te = Counter(r["class"] for r in hi if r["split"] == "test")
    va = Counter(r["class"] for r in hi if r["split"] == "valid")
    return {"__NTRAIN__": f"{sum(learn.values()):,}", "__NVALID__": f"{sum(va[c] for c in learn):,}", "__NTEST__": f"{sum(te[c] for c in learn):,}",
            "__NCLS__": str(len(learn)), "__BIG__": big, "__BIGN__": f"{learn[big]:,}", "__SMALL__": small, "__SMALLN__": f"{learn[small]:,}",
            "__SMALLTEST__": str(te[small]), "__SMALLVALID__": str(va[small]), "__RATIO__": str(round(learn[big] / learn[small])),
            "__LBTRAIN__": str(tr.get("Load Balancer", 0)), "__LBVALID__": str(va.get("Load Balancer", 0)), "__LBTEST__": str(te.get("Load Balancer", 0)),
            "__BATCHES__": str(sum(learn.values()) // 64),
            "__NPHOTOS__": f"{sum(1 for _ in csv.DictReader(open(ROOT / 'data' / 'datasets' / 'devices' / 'manifest.csv'))):,}",
            "__NHIPHOTOS__": f"{len({r['source_image'] for r in hi}):,}", "__NHIREMOVED__": str(len({r['source_image'] for r in hi if r.get('status') != 'kept'}))}


def handover():
    """What the integration session needs: the finished run, the Other calibration, file sizes."""
    d = ROOT / "models" / "device_cls" / "convnext_tiny"
    h = {"done": (d / "results.json").exists(), "calibrated": (d / "other_stats.json").exists(), "files": []}
    for f in ("best.pt", "other_cutoffs.json", "train_features.npz", "results.json"):
        if (d / f).exists():
            h["files"].append([f, round((d / f).stat().st_size / 1e6, 1)])
    if h["done"]:
        r = json.load(open(d / "results.json")); h["results"] = {k: r[k] for k in ("classes", "dropped", "best_epoch", "epochs_run", "test", "val")}
    if h["calibrated"]:
        h["other"] = json.load(open(d / "other_stats.json"))
    return h


CLS_RUN = ROOT / "models" / "device_cls" / "convnext_tiny"


def jpg(im, q=80):
    b = io.BytesIO(); im.convert("RGB").save(b, "JPEG", quality=q)
    return base64.b64encode(b.getvalue()).decode()


def res_data():
    """resolution.json (analyse_classifier.py) plus four crops shown as they are and as the model sees them."""
    if not (CLS_RUN / "resolution.json").exists():
        return None
    R = json.load(open(CLS_RUN / "resolution.json"))
    sys.path.insert(0, str(ROOT / "src"))
    from device_classifier import letterbox
    P = list(csv.DictReader(open(CLS_RUN / "test_predictions.csv")))
    # one wide device: at five sharpness levels, and inside three canvas sizes
    sw = clearest([p for p in P if p["class"] == p["pred"] == "Switch" and int(p["crop_w"]) >= 6 * int(p["crop_h"])], 1, need=True)[0]
    im = Image.open(CROPS / sw["crop"]).convert("RGB"); W0, H0 = R["canvas"]; base = letterbox(im, (W0, H0))
    bw, bh = im.size; sc = min(W0 / bw, H0 / bh); dh = round(bh * sc); top = (H0 - dh) // 2
    zoom = (0, max(0, top - 4), min(W0, round(dh * 2.6)), min(H0, top + dh + 4))       # left end of the device: logo and ports
    R["strip"] = [{"factor": t["factor"], "canvas": t["canvas"], "macro_f1": t["macro_f1"],
                   "img": jpg((base if t["factor"] == 1 else base.resize(tuple(t["canvas"]), Image.BICUBIC).resize((W0, H0), Image.BICUBIC)).crop(zoom).resize((3 * (zoom[2] - zoom[0]), 3 * (zoom[3] - zoom[1])), Image.NEAREST), 85)}
                  for t in R["resolution_test"]]
    sm = im.copy(); sm.thumbnail((1100, 200))
    R["fit"] = {"w": bw, "h": bh, "cw": round(bw * sc), "ch": dh, "orig": jpg(sm, 80), "view": jpg(base, 88), "kept": min(1, sc * sc)}
    R["square"] = jpg(letterbox(im, (224, 224)), 85)
    R["sizes"] = [{"canvas": [w, h], "img": jpg(letterbox(im, (w, h)).resize((w // 2, h // 2)), 80)} for w, h in ((512, 128), (1024, 128), (1024, 256))]
    bands = []
    for name, lo, hi in (("very large crops: made 4× smaller or more", 0, .25), ("large crops: made 2 to 4× smaller", .25, .5), ("medium crops: made up to 2× smaller", .5, 1), ("small crops: made bigger", 1, 1e9)):
        B = [p for p in P if lo <= min(R["canvas"][0] / int(p["crop_w"]), R["canvas"][1] / int(p["crop_h"])) < hi]
        bands.append({"name": name, "n": len(B), "wrong": sum(p["class"] != p["pred"] for p in B)})
    R["shrink"] = bands
    if (CLS_RUN / "canvas_compare.json").exists():
        R["canvas_compare"] = json.load(open(CLS_RUN / "canvas_compare.json"))
    return R


def views():
    """The test crops answered by the three sources the Results pages compare, in one row format:
    m1  = Model 1 (ConvNeXt-Tiny) with its Other rule;
    m2  = Model 2 (the DINOv2-B detector): Other when it finds no box on the device or its box score is below the cut-off
          set on valid (agreement.json);
    m12 = Model 1's answer, Other when the two models name different types.
    Row: crop, class, pred, prob, other ("0"/"1"), reason, said (what each model said, for the page), lprob (score of the
    real type, Model 1 only), crop_w, crop_h. Plus the look-alikes file for each source's answers."""
    P = list(csv.DictReader(open(CLS_RUN / "test_predictions.csv")))
    names = json.load(open(CLS_RUN / "results.json"))["classes"]

    def lprob(p):
        sc = np.array([float(v) for v in p["scores"].split()]); pr = np.exp(sc - sc.max()); pr /= pr.sum()
        return float(pr[names.index(p["class"])])
    base = lambda p: {"crop": p["crop"], "class": p["class"], "crop_w": p["crop_w"], "crop_h": p["crop_h"]}
    m1 = [dict(base(p), pred=p["pred"], prob=p["prob"], other=p["other"], reason=p["reason"], lprob=lprob(p),
               said=[[p["pred"], float(p["prob"])], [p["second"], float(p["second_prob"])]]) for p in P]
    near1 = json.load(open(CLS_RUN / "look_alikes.json")) if (CLS_RUN / "look_alikes.json").exists() else {}
    out = {"m1": (m1, near1)}
    d2, agf = CLS_RUN.parent / "dinov2_b", CLS_RUN.parent / "agreement.json"
    if (d2 / "test_predictions.csv").exists() and agf.exists():
        B = {r["crop"]: r for r in csv.DictReader(open(d2 / "test_predictions.csv"))}; cut2 = json.load(open(agf))["model2_cut"]
        say2 = lambda b: "no box" if b["pred"] == "none" else b["pred"]
        sys.path.insert(0, str(ROOT / "scripts" / "devices"))
        from second_detector import unseen_crops
        clean = unseen_crops("test")                      # Model 2 and Model 1 + 2: test photos neither model trained on
        m2, m12 = [], []
        for p in (q for q in P if q["crop"] in clean):
            b = B[p["crop"]]; none = b["pred"] == "none"; low = not none and float(b["prob"]) < cut2
            m2.append(dict(base(p), pred=say2(b), prob=b["prob"], other="1" if none or low else "0", lprob=None,
                           reason="no box found" if none else "low box score" if low else "", said=[[say2(b), None if none else float(b["prob"])]]))
            dis = p["pred"] != b["pred"]
            m12.append(dict(base(p), pred=p["pred"], prob=p["prob"], other="1" if dis else "0", lprob=lprob(p),
                            reason=("no box from Model 2" if none else "the two models disagree") if dis else "",
                            said=[["Model 1: " + p["pred"], float(p["prob"])], ["Model 2: " + say2(b), None if none else float(b["prob"])]]))
        near2 = json.load(open(d2 / "look_alikes.json")) if (d2 / "look_alikes.json").exists() else {}
        out["m2"] = (m2, near2); out["m12"] = (m12, near1)
    return out


def tallies(R):
    """Answering every crop (tb) and with Other (ta), for one source's rows."""
    tb = {"n": len(R), "right": sum(p["class"] == p["pred"] for p in R)}; tb["wrong"] = tb["n"] - tb["right"]
    O = [p for p in R if p["other"] == "1"]
    ta = {"n": len(R), "other": len(O), "right": sum(p["other"] == "0" and p["class"] == p["pred"] for p in R),
          "wrong": sum(p["other"] == "0" and p["class"] != p["pred"] for p in R),
          "caught": sum(p["class"] != p["pred"] for p in O), "lost": sum(p["class"] == p["pred"] for p in O)}
    return tb, ta


def unk_data(R, key):
    """The test crops one source answers Other (unknown): their real type, why, what it would have said."""
    O = [p for p in R if p["other"] == "1"]
    tb, ta = tallies(R)
    classes = sorted({p["class"] for p in R})
    per = []
    for c in classes:
        a = [p for p in R if p["class"] == c]; o = [p for p in a if p["other"] == "1"]
        per.append({"name": c, "n": len(a), "other": len(o), "would_wrong": sum(p["pred"] != c for p in o),
                    "top": Counter(p["pred"] for p in o if p["pred"] != c).most_common(2)})
    reasons = Counter(r for p in O for r in p["reason"].split(" and ") if r)
    pair = (lambda p: " or ".join(sorted((p["said"][0][0], p["said"][1][0])))) if key == "m1" else \
           (lambda p: " / ".join(x[0] for x in p["said"])) if key == "m12" else (lambda p: p["said"][0][0])
    torn = Counter(pair(p) for p in O).most_common(6)
    in_top = sum(p["class"] in [x[0].split(": ")[-1] for x in p["said"]] for p in O)

    def pic(p, size=(720, 200), q=76):
        im = Image.open(CROPS / p["crop"]); im.thumbnail(size)
        return {"img": jpg(im, q), "label": p["class"], "pred": p["pred"], "prob": float(p["prob"]), "said": p["said"], "other": p["other"] == "1", "reason": p["reason"]}
    wide = lambda p: int(p["crop_w"]) >= 4 * int(p["crop_h"])
    shown = [p for p in O if p["pred"] != p["class"] and wide(p)] or [p for p in O if p["pred"] != p["class"]]
    if key == "m1":
        shown = [p for p in shown if p["reason"] == "close call"] or shown
    hard = clearest(shown, 1, pool=400, need=True)[0]
    sure = clearest([p for p in R if p["other"] == "0" and p["pred"] == p["class"] == hard["class"] and float(p["prob"]) >= .9 and wide(p)]
                    or [p for p in R if p["other"] == "0" and p["pred"] == p["class"]], 1, pool=400, need=True)[0]
    ex = sorted(O, key=lambda p: float(p["prob"]))
    pick = clearest([p for p in ex if p["pred"] != p["class"]], 6, pool=400) + clearest([p for p in ex if p["pred"] == p["class"]], 3, pool=400)
    return {"n": len(R), "other": len(O), "would_wrong": sum(p["pred"] != p["class"] for p in O), "tb": tb, "ta": ta,
            "how": [pic(sure, (900, 240), 80), pic(hard, (900, 240), 80)], "in_top2": in_top, "per_class": per,
            "reasons": reasons.most_common(), "torn": torn, "examples": [pic(p) for p in pick]}


def energy_example():
    """Two test crops of the same class that the model is equally sure about, where only the one it got wrong has a
    weak score strength and is turned into Other. Strength = logsumexp of the 9 raw scores = -energy."""
    d = ROOT / "models" / "device_cls" / "convnext_tiny"
    if not (d / "test_predictions.csv").exists():
        return None
    P = list(csv.DictReader(open(d / "test_predictions.csv")))
    if "scores" not in P[0]:
        return None
    cut = json.load(open(d / "other_cutoffs.json"))["energy"]
    classes = json.load(open(d / "results.json"))["classes"]
    for p in P:
        p["e"], p["p"] = float(p["energy"]), float(p["prob"])
    best = None
    for weak in (p for p in P if p["class"] != p["pred"] and p["e"] > cut and p["p"] >= .5 and sharpness(p["crop"]) >= SHARP_MIN):
        near = [p for p in P if p["class"] == p["pred"] == weak["class"] and abs(p["p"] - weak["p"]) < .03 and p["e"] < cut - .5 and sharpness(p["crop"]) >= SHARP_MIN]
        if near:
            strong = max(near, key=lambda p: quality(p["crop"]))
            sc = min(quality(weak["crop"]), quality(strong["crop"]))
            if best is None or sc > best[0]:
                best = (sc, weak, strong)
    if best is None:
        return None
    _, weak, strong = best

    def one(p):
        im = Image.open(CROPS / p["crop"]); im.thumbnail((720, 200))
        b = io.BytesIO(); im.save(b, "JPEG", quality=80)
        sc = [float(v) for v in p["scores"].split()]
        return {"img": base64.b64encode(b.getvalue()).decode(), "label": p["class"], "pred": p["pred"], "prob": p["p"],
                "strength": -p["e"], "scores": sorted(zip(classes, sc), key=lambda t: -t[1])}
    return {"cut": -cut, "pair": [one(strong), one(weak)]}


def why_data(R, near):
    """One source's test mistakes left with Other on, for Misclassified: the top mix-ups, each mistake next to the 3
    training crops of the answered type it looks most like (look-alikes, in Model 1's features)."""
    W = [p for p in R if p["other"] == "0" and p["class"] != p["pred"]]
    tb, ta = tallies(R)

    def duo(p):
        im = Image.open(CROPS / p["crop"]); im.thumbnail((720, 200))
        q = {"img": jpg(im, 76), "label": p["class"], "pred": p["pred"], "prob": float(p["prob"]), "lprob": p["lprob"], "near": []}; seen = set()
        for n in (near.get(p["crop"]) or {}).get("near", []):
            if sharpness(n["crop"]) >= SHARP_MIN and photo_of(n["crop"]) not in seen:
                im = Image.open(CROPS / n["crop"]); im.thumbnail((520, 150))
                q["near"].append({"img": jpg(im, 74), "sim": n["sim"]}); seen.add(photo_of(n["crop"]))
            if len(q["near"]) == 3:
                break
        return q
    ordered = []
    for (a, b), n in Counter((p["class"], p["pred"]) for p in W).most_common():
        if len(ordered) == 5:
            break
        ws = [p for p in W if p["class"] == a and p["pred"] == b]
        ordered.append({"label": a, "pred": b, "n": n, "pics": [q for q in (duo(p) for p in clearest(ws, 3, pool=400)) if q["near"]]})
    pairs = Counter(tuple(sorted((p["class"], p["pred"]))) for p in W)
    return {"n": len(R), "wrong": len(W), "tb": tb, "ta": ta, "ordered": ordered,
            "pairs": [{"a": a, "b": b, "n": n} for (a, b), n in pairs.most_common(6)]}


def outline(lab, W, H, box):
    """The polygon (pixel points) of the label line whose extent best matches `box` (x1, y1, x2, y2), else the box itself.
    Matched by position because line numbers can differ between an export and the merged label files."""
    best, pts = 0.0, None
    for ln in lab:
        v = [float(t) for t in ln.split()[1:]]
        if len(v) < 6:
            continue
        xs, ys = [v[i] * W for i in range(0, len(v) - 1, 2)], [v[i + 1] * H for i in range(0, len(v) - 1, 2)]
        ix = max(0, min(box[2], max(xs)) - max(box[0], min(xs))); iy = max(0, min(box[3], max(ys)) - max(box[1], min(ys)))
        inter = ix * iy; uni = (box[2] - box[0]) * (box[3] - box[1]) + (max(xs) - min(xs)) * (max(ys) - min(ys)) - inter
        if uni > 0 and inter / uni > best:
            best, pts = inter / uni, list(zip(xs, ys))
    return pts if best >= .6 else [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]


@functools.lru_cache(maxsize=None)
def look(crop):
    """Share of the crop's box the device fills (low = tilted, side view or partly hidden) and its mean brightness."""
    a = np.asarray(Image.open(CROPS / crop).convert("L"), dtype=np.float32); m = a > 10
    return float(m.mean()), float(a[m].mean()) if m.any() else 0.0


def edge_data(P, near, key):
    """Edge cases for one source: the test crops still answered wrong with Other on, why, the racks they sit in,
    and (Model 1 only, it works on single crops) the stress test (stress.json)."""
    E = [p for p in P if p["other"] == "0" and p["class"] != p["pred"]]
    answered = [p for p in P if p["other"] == "0"]
    per_photo = Counter(photo_of(p["crop"]) for p in E)

    def why(p):
        fill, bright = look(p["crop"])
        if per_photo[photo_of(p["crop"])] >= 3:
            return "rack"
        if fill < .5:
            return "angle"
        if sharpness(p["crop"]) < SHARP_MIN or bright < 50:
            return "blur"
        return "alike"
    cases = []
    for p in sorted(E, key=lambda p: -float(p["prob"])):
        img = None
        if sharpness(p["crop"]) >= SHARP_MIN:
            im = Image.open(CROPS / p["crop"]); im.thumbnail((520, 150)); img = jpg(im, 74)
        cases.append({"label": p["class"], "pred": p["pred"], "prob": float(p["prob"]), "why": why(p), "img": img, "photo": photo_of(p["crop"])})
    # every rack (photo) that still has a wrong answer: the photo with them outlined, the mistakes, and why
    man = {r["crop"]: r for r in csv.DictReader(open(CROPS / "manifest.csv")) if r["crop"]}
    ds = {r["photo"] for r in csv.DictReader(open(ROOT / "data" / "datasets" / "devices" / "manifest.csv"))}
    by_exp = {r["export_image"].replace(" ", "_"): r["photo"] for r in csv.DictReader(open(ROOT / "data" / "datasets" / "devices" / "manifest.csv"))}
    sim = lambda p: (near.get(p["crop"]) or {}).get("sim")
    racks = []
    for stem, n in per_photo.most_common():
        ws = sorted([p for p in E if photo_of(p["crop"]) == stem], key=lambda p: -float(p["prob"]))
        raw = man[ws[0]["crop"]]["source_image"]; src = raw if raw in ds else by_exp.get(raw.replace(" ", "_"))
        img = None
        if src:
            im = ImageOps.exif_transpose(Image.open(ROOT / "data" / "datasets" / "devices" / "images" / src)).convert("RGB")
            lab = (ROOT / "data" / "datasets" / "devices" / "labels" / (pathlib.Path(src).stem + ".txt")).read_text().splitlines()
            dr = ImageDraw.Draw(im); lw = max(3, im.width // 250)
            for p in ws:
                r = man[p["crop"]]; pts = outline(lab, im.width, im.height, tuple(float(r[k]) for k in ("x1", "y1", "x2", "y2")))
                dr.line(pts + [pts[0]], fill=(255, 120, 0), width=lw)
            im.thumbnail((520, 400)); img = jpg(im, 74)
        mixes = Counter((p["class"], p["pred"]) for p in ws)
        (la, pr), k = mixes.most_common(1)[0]
        sims = [sim(p) for p in ws if p["class"] == la and p["pred"] == pr and sim(p) is not None]
        sm = round(100 * sum(sims) / len(sims)) if sims else None
        why_k = Counter(why(p) for p in ws)
        if k >= 3:
            reason = f"{k} copies of one {la} model, all called {pr}" + (f": this model looks {sm} % like the {pr} devices it was trained on" if sm else "") + ", so the same mistake repeats on every copy."
        else:
            parts = []
            for p in ws:
                w = why(p); s_ = sim(p)
                parts.append({"angle": f"the {p['class']} is tilted or partly hidden, so only part of its front shows",
                              "blur": f"the {p['class']} is blurry or dark in this photo",
                              "alike": f"the {p['class']} looks {round(100 * s_)} % like a {p['pred']} it was trained on" if s_ else f"the {p['class']} looks like a {p['pred']}",
                              "rack": f"the {p['class']} repeats in this rack"}[w])
            reason = "; ".join(dict.fromkeys(parts)); reason = reason[:1].upper() + reason[1:] + "."
        soft = sum(1 for p in ws if sharpness(p["crop"]) < SHARP_MIN)
        if soft * 2 > len(ws) and len(ws) >= 3:
            reason += f" The photo is taken from far away, so {soft} of these {len(ws)} devices are also blurry."
        crops = []
        for p in ws:                              # edge cases show blurry crops too: the blur is part of the reason
            c = Image.open(CROPS / p["crop"]); c.thumbnail((360, 90))
            crops.append({"label": p["class"], "pred": p["pred"], "prob": float(p["prob"]), "img": jpg(c, 72), "soft": sharpness(p["crop"]) < SHARP_MIN})
        racks.append({"img": img, "n": n, "photo": stem, "devices": sum(1 for p in P if photo_of(p["crop"]) == stem),
                      "mixes": [[a, b, c] for (a, b), c in mixes.most_common()], "reason": reason, "crops": crops})
    stress = json.load(open(CLS_RUN / "stress.json")) if key == "m1" and (CLS_RUN / "stress.json").exists() else None
    if stress:
        sys.path.insert(0, str(ROOT / "scripts" / "devices"))
        from analyse_classifier import STRESS
        rng = random.Random(3)
        ex = clearest([p for p in P if p["class"] == p["pred"] == "Switch" and 4 <= int(p["crop_w"]) / int(p["crop_h"]) <= 7], 1, need=True)[0]
        base = Image.open(CROPS / ex["crop"]).convert("RGB"); base.thumbnail((640, 160))
        for row in stress:
            im = STRESS[row["name"]](base, rng); im.thumbnail((320, 80)); row["img"] = jpg(im, 72)
    # the scores without the worst racks: all crops of those photos left out
    def tally(rows):
        return {"n": len(rows), "right": sum(p["class"] == p["pred"] for p in rows),
                "o_right": sum(p["other"] == "0" and p["class"] == p["pred"] for p in rows),
                "o_other": sum(p["other"] != "0" for p in rows), "o_wrong": sum(p["other"] == "0" and p["class"] != p["pred"] for p in rows)}
    without_racks = []
    for k in range(1, 4):
        drop = {r["photo"] for r in racks[:k]}
        without_racks.append(dict(k=k, mistakes=sum(r["n"] for r in racks[:k]), **tally([p for p in P if photo_of(p["crop"]) not in drop])))
    # the same scores with blurry (or dark) and tilted devices left out, judged on the picture alone
    def picture(p):
        fill, bright = look(p["crop"])
        if fill < .5:
            return "tilted"
        if sharpness(p["crop"]) < SHARP_MIN or bright < 50:
            return "blurry"
        return "clear"
    kind = {p["crop"]: picture(p) for p in P}

    sel = lambda f: tally([p for p in P if f(p)])
    clear = {"blurry": sum(k == "blurry" for k in kind.values()), "tilted": sum(k == "tilted" for k in kind.values()),
             "all": tally(P), "clear": sel(lambda p: kind[p["crop"]] == "clear"),
             "groups": [["All test crops", tally(P)], ["Without tilted", sel(lambda p: kind[p["crop"]] != "tilted")],
                        ["Without blurry or dark", sel(lambda p: kind[p["crop"]] != "blurry")], ["Without both", sel(lambda p: kind[p["crop"]] == "clear")]],
             "sharp_bands": [dict(name=n, **sel(lambda p: lo <= sharpness(p["crop"]) < hi)) for n, lo, hi in
                             (("under 100", 0, 100), ("100 to 200", 100, 200), ("200 to 300", 200, 300), ("300 to 450", 300, 450), ("450 to 700", 450, 700), ("700 and up", 700, 1e9))],
             "fill_bands": [dict(name=n, **sel(lambda p: lo <= look(p["crop"])[0] < hi)) for n, lo, hi in
                            (("under 40 %", 0, .4), ("40 to 50 %", .4, .5), ("50 to 60 %", .5, .6), ("60 to 80 %", .6, .8), ("80 to 100 %", .8, 1.01))]}
    examples = {}
    for k in ("rack", "alike", "blur", "angle"):
        rows = [p for p in E if why(p) == k]
        if rows:
            q = clearest(rows, 1, need=True)[0]; c = Image.open(CROPS / q["crop"]); c.thumbnail((360, 90))
            examples[k] = {"img": jpg(c, 72), "label": q["class"], "pred": q["pred"], "soft": sharpness(q["crop"]) < SHARP_MIN}
    return {"n": len(P), "answered": len(answered), "right": len(answered) - len(E), "wrong": len(E), "photos": len(per_photo), "examples": examples,
            "min_prob": min(float(p["prob"]) for p in E) if E else None, "cases": cases, "racks": racks, "stress": stress, "clear": clear, "without_racks": without_racks}


def train_visuals():
    """Pictures for the training steps: one clear training crop as it is, in the box, and four random augmentations
    (the same trims, flip, colour jitter and placement as train_device_cls.py), plus the sampling shares of the class weights."""
    from PIL import ImageEnhance
    rows = [r for r in csv.DictReader(open(CROPS / "manifest.csv")) if r["crop"] and r["split"] == "train" and int(r["image_w"]) * int(r["image_h"]) >= MP]
    PAD = (114, 114, 114)

    def box(im, scale=1.0, dx=.5, dy=.5, W=512, H=128):
        w, h = im.size; sc = min(W / w, H / h) * scale
        nw, nh = max(1, round(w * sc)), max(1, round(h * sc))
        out = Image.new("RGB", (W, H), PAD); out.paste(im.resize((nw, nh), Image.BICUBIC), (int((W - nw) * dx), int((H - nh) * dy)))
        return out
    random.seed(5)
    src = clearest([r for r in rows if r["class"] == "UPS" and 3 <= int(r["crop_w"]) / int(r["crop_h"]) <= 6], 1, need=True)[0]
    im = Image.open(CROPS / src["crop"]).convert("RGB")
    aug = []
    for _ in range(4):
        w, h = im.size; l, t, r, b = (random.uniform(0, .08) for _ in range(4))
        a = im.crop((int(w * l), int(h * t), int(w * (1 - r)), int(h * (1 - b))))
        if random.random() < .5:
            a = a.transpose(Image.FLIP_LEFT_RIGHT)
        a = ImageEnhance.Brightness(a).enhance(random.uniform(.7, 1.3)); a = ImageEnhance.Contrast(a).enhance(random.uniform(.7, 1.3)); a = ImageEnhance.Color(a).enhance(random.uniform(.8, 1.2))
        aug.append(jpg(box(a, random.uniform(.8, 1), random.random(), random.random()), 78))
    sm = im.copy(); sm.thumbnail((900, 180))
    tr = Counter(r["class"] for r in rows); learn = {c: n for c, n in tr.items() if n >= 20}
    wsum = sum(n ** .5 for n in learn.values()); tot = sum(learn.values())
    weights = sorted([{"name": c, "crops": n, "share": n / tot, "draws": n ** .5 / wsum} for c, n in learn.items()], key=lambda x: -x["crops"])
    return {"orig": jpg(sm, 80), "w": im.width, "h": im.height, "box": jpg(box(im), 85), "aug": aug, "weights": weights, "label": src["class"]}


def squash_example(size, keep_shape=False):
    """One test photo as Model 2 sees it (squashed to size x size) and one 1U device in it, next to the same device in
    Model 1's 512 x 128 box."""
    sys.path.insert(0, str(ROOT / "src"))
    from device_classifier import letterbox
    man = [r for r in csv.DictReader(open(CROPS / "manifest.csv")) if r["crop"] and r["split"] == "test" and int(r["image_w"]) * int(r["image_h"]) >= MP]
    D = list(csv.DictReader(open(ROOT / "data" / "datasets" / "devices" / "manifest.csv")))
    ds = {r["photo"] for r in D}; by_exp = {r["export_image"].replace(" ", "_"): r["photo"] for r in D}
    per = Counter(r["source_image"] for r in man)
    tall = [k for k, n in per.most_common() if n >= 8 and any(int(r["image_h"]) > 1.3 * int(r["image_w"]) for r in man if r["source_image"] == k)]
    for stem in tall:
        src = stem if stem in ds else by_exp.get(stem.replace(" ", "_"))
        devs = [r for r in man if r["source_image"] == stem and r["class"] == "Switch"]
        if src and devs:
            break
    else:
        return None
    im = ImageOps.exif_transpose(Image.open(ROOT / "data" / "datasets" / "devices" / "images" / src)).convert("RGB")
    d = clearest(devs, 1, need=True)[0]; x1, y1, x2, y2 = (float(d[k]) for k in ("x1", "y1", "x2", "y2"))
    if keep_shape:                                   # long side to `size`, shape kept
        k = min(1.0, size / max(im.width, im.height)); sx = sy = k
        sq = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.BICUBIC)
    else:
        sx, sy = size / im.width, size / im.height
        sq = im.resize((size, size), Image.BILINEAR)
    b = (round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy)); dev2 = sq.crop(b)
    mark = sq.copy(); ImageDraw.Draw(mark).rectangle(b, outline=(255, 120, 0), width=2)
    crop = Image.open(CROPS / d["crop"]).convert("RGB"); box = letterbox(crop, (512, 128))
    s1 = min(512 / crop.width, 128 / crop.height)
    small = im.copy(); small.thumbnail((600, 600))
    return {"orig": jpg(small, 78), "w": im.width, "h": im.height, "sq": jpg(mark, 88), "label": d["class"],
            "dev2": jpg(dev2.resize((dev2.width * 6, dev2.height * 6), Image.NEAREST), 85), "w2": dev2.width, "h2": dev2.height,
            "dev1": jpg(box, 85), "w1": round(crop.width * s1), "h1": round(crop.height * s1), "sw": sq.width, "sh": sq.height}


def det_data():
    """The team's DINOv2-B detector as the second model: its checkpoint facts (detector.json) and how it scores on the test
    photos (test_predictions.csv from second_detector.py), overall, per type and against the classifier's 1,550 crops."""
    d = CLS_RUN.parent / "dinov2_b"
    if not (d / "detector.json").exists() or not (d / "test_predictions.csv").exists():
        return None
    meta = json.load(open(d / "detector.json"))
    sys.path.insert(0, str(ROOT / "scripts" / "devices"))
    from second_detector import unseen_crops
    clean = unseen_crops("test")                          # test photos neither model trained on
    P = [p for p in csv.DictReader(open(d / "test_predictions.csv")) if p["crop"] in clean]
    hi = {p["crop"] for p in csv.DictReader(open(CLS_RUN / "test_predictions.csv"))} if (CLS_RUN / "test_predictions.csv").exists() else set()
    types = sorted({p["class"] for p in P}); preds = [t for t in meta["class_names"] if t in types] + [t for t in meta["class_names"] if t not in types] + ["none"]
    per = []
    for t in types:
        rows = [p for p in P if p["class"] == t]; found = [p for p in rows if p["pred"] != "none"]
        tp = sum(p["pred"] == t for p in rows); fp = sum(1 for p in P if p["pred"] == t and p["class"] != t)
        prec = tp / max(1, tp + fp); rec = tp / max(1, len(rows))
        per.append({"name": t, "n": len(rows), "found": len(found), "right": tp, "precision": prec, "recall": rec, "f1": 2 * prec * rec / max(1e-9, prec + rec)})
    conf = [[sum(1 for p in P if p["class"] == t and p["pred"] == q) for q in preds] for t in types]
    H = [p for p in P if p["crop"] in hi]
    agree = json.load(open(CLS_RUN.parent / "agreement.json")) if (CLS_RUN.parent / "agreement.json").exists() else None
    return {"squash": squash_example(meta["image_size"], "fcos" in meta.get("format", "")), "meta": meta, "n": len(P), "photos": len({p["crop"].rsplit("/", 1)[-1].rsplit("__", 1)[0] for p in P}),
            "found": sum(p["pred"] != "none" for p in P), "right": sum(p["class"] == p["pred"] for p in P),
            "macro_f1": sum(x["f1"] for x in per) / len(per), "per_class": per, "confusion": {"rows": types, "cols": preds, "cells": conf},
            "hi": {"n": len(H), "right": sum(p["class"] == p["pred"] for p in H), "none": sum(p["pred"] == "none" for p in H)},
            "agreement": agree, "curve": None}


def approaches_data():
    """Model 1, Model 2 and the two together on the same test crops (agreement.json), plus how the pair decides,
    with one real example of each outcome."""
    om = json.load(open(CLS_RUN / "other_stats.json")); M = om["metrics"]
    P = list(csv.DictReader(open(CLS_RUN / "test_predictions.csv")))
    agf = CLS_RUN.parent / "agreement.json"; b2 = CLS_RUN.parent / "dinov2_b" / "test_predictions.csv"
    if not agf.exists() or not b2.exists():
        return None
    ag = json.load(open(agf)); B = {r["crop"]: r for r in csv.DictReader(open(b2))}
    sys.path.insert(0, str(ROOT / "scripts" / "devices"))
    from second_detector import unseen_crops
    clean = unseen_crops("test")                          # the same test crops agreement.json scores: neither model trained on them
    P = [p for p in P if p["crop"] in clean]
    rows = [(p, B[p["crop"]]) for p in P if p["crop"] in B]
    agree = [(x, y) for x, y in rows if x["pred"] == y["pred"]]; dis = [(x, y) for x, y in rows if x["pred"] != y["pred"]]

    def ex(pairs, out):
        if not pairs:
            return None
        x = clearest([q for q, _ in pairs], 1, need=True)[0]; y = B[x["crop"]]
        im = Image.open(CROPS / x["crop"]); im.thumbnail((420, 100))
        return {"img": jpg(im, 76), "label": x["class"], "m1": x["pred"], "p1": float(x["prob"]), "m2": y["pred"], "p2": float(y["prob"]), "out": out}
    how = {"agree": len(agree), "agree_right": sum(x["class"] == x["pred"] for x, _ in agree), "dis": len(dis),
           "dis_right": sum(x["class"] == x["pred"] for x, _ in dis), "dis_m2_right": sum(x["class"] == y["pred"] for x, y in dis),
           "examples": [e for e in (
               ex([(x, y) for x, y in agree if x["class"] == x["pred"]], "answered, right"),
               ex([(x, y) for x, y in dis if x["class"] != x["pred"] and x["class"] == y["pred"]], "Other: a wrong answer avoided"),
               ex([(x, y) for x, y in dis if x["class"] == x["pred"] and y["pred"] not in ("none", x["class"])], "Other: a right answer lost")) if e]}
    left = []
    for x, y in sorted([(x, y) for x, y in agree if x["class"] != x["pred"]], key=lambda q: (q[0]["class"], q[0]["pred"])):
        im = Image.open(CROPS / x["crop"]); im.thumbnail((360, 90))
        left.append({"img": jpg(im, 72), "label": x["class"], "pred": x["pred"], "p1": float(x["prob"]), "p2": float(y["prob"]),
                     "soft": sharpness(x["crop"]) < SHARP_MIN, "photo": photo_of(x["crop"])})
    pe = Counter(q["photo"] for q in left)
    how["left_racks"] = sum(1 for q in left if pe[q["photo"]] >= 2); how["left_rack_photos"] = sum(1 for v in pe.values() if v >= 2)
    det = det_data()
    return {"left": left, "agree": {r["name"]: r for r in ag["rules"]}, "chosen": ag["chosen"], "cut2": ag.get("model2_cut"),
            "detector": det and {"n": det["n"], "right": det["right"], "found": det["found"], "hi": det["hi"]}, "how": how,
            "test": {"n": len(P), "photos": len({photo_of(p["crop"]) for p in P}),
                     "types": sorted(Counter(p["class"] for p in P).items(), key=lambda x: -x[1])}}


def code_sections():
    """The real training script, cut into the pieces the Training code tab explains."""
    src = (ROOT / "scripts" / "devices" / "train_device_cls.py").read_text()
    lines = src.splitlines()

    def between(start, end):
        a = next(i for i, l in enumerate(lines) if l.startswith(start) or l.strip().startswith(start))
        b = next(i for i, l in enumerate(lines) if i > a and (l.startswith(end) or l.strip().startswith(end)))
        return "\n".join(lines[a:b]).rstrip()

    models = between("MODELS = {", "def letterbox")
    return {
        "models": models,
        "model_lines": {k: "\n".join(l for l in models.splitlines() if f'"{k}"' in l or (k == "dinov2_b" and "layer_decay" in l)) for k in LABELS},
        "data": between("def load_items", "class Crops"),
        "letterbox": between("def letterbox", "def load_items"),
        "augment": between("    def __getitem__", "def summarize"),
        "balance": between("    counts = np.bincount", "    say(f\"model"),
        "build": between("    kw = dict(pretrained", "    best, best_ep, bad"),
        "loop": between("    for ep in range(start", "    ck = torch.load(out / \"best.pt\""),
        "select": between("    ck = torch.load(out / \"best.pt\"", "if __name__"),
        "full": src,
    }


def main():
    rows = list(csv.DictReader(open(CROPS / "manifest.csv")))
    hi = [r for r in rows if int(r["image_w"]) * int(r["image_h"]) >= MP]
    ds = {"all": stats(rows, 7), "hi": stats(hi, 11)}
    html = TEMPLATE
    V = views()
    for k, v in facts([r for r in hi if r["crop"]]).items():
        html = html.replace(k, v)
    html = html.replace("__DS__", json.dumps(ds, separators=(",", ":"))).replace("__REV__", json.dumps({"all": review([r for r in rows if r["crop"]]), "hi": review(hi)}, separators=(",", ":"))).replace("__RES__", json.dumps(results(), separators=(",", ":"))).replace("__CODE__", json.dumps(code_sections(), separators=(",", ":"))).replace("__HAND__", json.dumps(handover(), separators=(",", ":"))).replace("__WHY__", json.dumps({k: why_data(R, nr) for k, (R, nr) in V.items()}, separators=(",", ":"))).replace("__ENX__", json.dumps(energy_example(), separators=(",", ":"))).replace("__RESO__", json.dumps(res_data(), separators=(",", ":"))).replace("__UNK__", json.dumps({k: unk_data(R, k) for k, (R, nr) in V.items()}, separators=(",", ":"))).replace("__EDGE__", json.dumps({k: edge_data(R, nr, k) for k, (R, nr) in V.items()}, separators=(",", ":"))).replace("__TRV__", json.dumps(train_visuals(), separators=(",", ":"))).replace("__DET__", json.dumps(det_data(), separators=(",", ":"))).replace("__APR__", json.dumps(approaches_data(), separators=(",", ":"))).replace("__RUNS__", json.dumps([x for x in (run_detail(a) for a in ("convnext_tiny", "dinov2_b")) if x], separators=(",", ":")))
    pathlib.Path(sys.argv[1]).write_text(html)
    print(f"{sys.argv[1]}: {len(html) / 1e6:.1f} MB, {len(rows)} crops, {len(hi)} high-resolution")


TEMPLATE = r'''<title>Device Crop Atlas</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: a standard dashboard. App bar with underline tabs; each tab = page head, four key figures, one full-width card, then two-column cards. Content column is 90 % of the viewport. */
:root{
  --plane:#eef1f4; --card:#ffffff; --bar:#ffffff; --ink:#14181c; --ink2:#4a525b; --muted:#7c848d;
  --grid:#e3e7eb; --base:#c3c9d0; --chip:#f2f4f6; --thumbbg:#0d0f11;
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --accent:#2a78d6; --accent-soft:#e8f1fc; --onaccent:#ffffff;
  --shadow:0 1px 2px rgba(20,28,38,.05),0 4px 14px rgba(20,28,38,.07);
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,monospace;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --plane:#0e1013; --card:#181b1f; --bar:#14171a; --ink:#eef0f2; --ink2:#bcc3ca; --muted:#8a929a;
  --grid:#2a2e33; --base:#3b4047; --chip:#22262a; --thumbbg:#000;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --accent:#5598e7; --accent-soft:#1a2a3f; --onaccent:#0b0d10;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 14px rgba(0,0,0,.35); color-scheme:dark}}
:root[data-theme="dark"]{
  --plane:#0e1013; --card:#181b1f; --bar:#14171a; --ink:#eef0f2; --ink2:#bcc3ca; --muted:#8a929a;
  --grid:#2a2e33; --base:#3b4047; --chip:#22262a; --thumbbg:#000;
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --accent:#5598e7; --accent-soft:#1a2a3f; --onaccent:#0b0d10;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 4px 14px rgba(0,0,0,.35); color-scheme:dark}
body{background:var(--plane);color:var(--ink);font-family:var(--sans);font-size:13.5px;line-height:1.5}
/* shell: sticky top header with grouped tabs, then the main column; 90 % of the viewport */
.shell{width:90%;margin:0 auto;display:grid;grid-template-columns:minmax(0,1fr)}
.side{position:sticky;top:env(safe-area-inset-top,0px);z-index:10;background:var(--bar);box-shadow:0 0 0 100vmax var(--bar);clip-path:inset(0 -100vmax);
  display:flex;flex-wrap:wrap;align-items:center;gap:12px 20px;min-width:0;padding-block:14px 14px}
.brand{display:flex;align-items:center;gap:12px;flex:none}
.logo{width:32px;height:32px;border-radius:10px;background:linear-gradient(135deg,var(--accent),color-mix(in srgb,var(--accent) 60%,#000));display:grid;place-items:center;flex:none;color:var(--onaccent);font-weight:700;font-size:13px;letter-spacing:.02em}
.brand b{display:block;font-size:15px;font-weight:600;line-height:1.2}
.brand small{display:block;color:var(--muted);font-size:12.5px}
.topmeta{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
.topmeta span{font-size:12.5px;color:var(--ink2);background:var(--chip);border-radius:999px;padding:5px 12px;white-space:nowrap}
.topmeta b{color:var(--ink)}
.nav{display:flex;align-items:flex-end;gap:18px;overflow-x:auto;scrollbar-width:none;min-width:0;flex:1 1 100%;padding-bottom:2px}
.nav .grp{display:grid;gap:5px;flex:none}
.nav .gl{font-size:10.5px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;padding-left:6px;white-space:nowrap}
.nav .track{display:flex;gap:2px;background:var(--chip);border:1px solid var(--grid);border-radius:11px;padding:3px}
.nav button{font:inherit;font-size:13px;font-weight:500;color:var(--ink2);background:none;border:0;border-radius:8px;padding:6px 12px;cursor:pointer;white-space:nowrap;display:flex;align-items:center;gap:6px;transition:background .15s,color .15s}
.nav button:hover{color:var(--ink);background:color-mix(in srgb,var(--card) 60%,transparent)}
.nav button[aria-selected="true"]{color:var(--accent);background:var(--card);font-weight:600;box-shadow:0 1px 2px rgba(20,28,38,.12),0 2px 8px rgba(20,28,38,.08)}
.nav button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.nav .dot{width:7px;height:7px;border-radius:50%;background:#d99400}
.side .src{display:none}
main{padding-block:22px 40px;min-width:0;display:grid;gap:16px}
[hidden]{display:none!important}
.panel{display:grid;gap:16px;min-width:0}
.pagehead{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;flex-wrap:wrap;padding-bottom:4px}
.pagehead .crumb{font-size:12px;font-weight:600;color:var(--accent);text-transform:uppercase;letter-spacing:.07em;margin:0 0 4px}
.pagehead h1{font-size:22px;font-weight:600;margin:0 0 4px;letter-spacing:-.01em;text-wrap:balance}
.pagehead p{margin:0;color:var(--ink2);max-width:80ch}
.pagehead .act{display:flex;gap:8px;flex-wrap:wrap}
.chipfact{font-size:12.5px;color:var(--ink2);background:var(--card);border-radius:999px;padding:5px 12px;box-shadow:var(--shadow);white-space:nowrap}
.chipfact b{color:var(--ink)}
.flow4{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.flow4 .st{background:var(--chip);border-radius:10px;padding:10px 16px;display:grid;min-width:120px}
.flow4 .st b{font-size:20px;font-weight:600}.flow4 .st span{font-size:12.5px;color:var(--ink2)}
.flow4 .st.hl{background:var(--accent-soft)}.flow4 .ar{color:var(--muted)}
.szbar{display:flex;height:26px;border-radius:6px;overflow:hidden;margin:10px 0 12px}
.szbar i{display:block;height:100%}
.szleg{display:grid;gap:6px;font-size:13px}.szleg div{display:flex;align-items:center;gap:8px}.szleg i{width:10px;height:10px;border-radius:2px;flex:none}.szleg b{margin-left:auto;font-weight:600}
.big1{font-size:15px}.big1 b{font-size:22px;font-weight:600;color:var(--accent)}
.csz{display:grid;grid-template-columns:110px minmax(0,1fr) 88px;align-items:center;gap:6px 12px;font-size:13px}
.csz .bx{background:color-mix(in srgb,var(--s1) 22%,transparent);border:1.5px solid var(--s1);border-radius:3px}
.csz .px{color:var(--ink2);text-align:right;font-variant-numeric:tabular-nums}
.pick5 td,.pick5 th{white-space:normal;vertical-align:top;text-align:left}
.pick5 td:first-child{font-weight:600;white-space:nowrap}
.pick5 tr.chosen td{background:var(--accent-soft)}
.steps6 td span{display:block;font-weight:400;color:var(--muted);font-size:12px}
.mc2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.mc3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
@media (max-width:1000px){.mc3{grid-template-columns:minmax(0,1fr)}}@media (max-width:700px){.mc2{grid-template-columns:minmax(0,1fr)}}
.mcd{background:var(--chip);border-radius:10px;padding:14px 16px;display:grid;gap:10px;align-content:start;min-width:0}
.mcd.top{background:color-mix(in srgb,#3f9b57 10%,var(--card));outline:1.5px solid color-mix(in srgb,#3f9b57 45%,transparent)}
.mcd h3{margin:0;font-size:14.5px;display:flex;justify-content:space-between;gap:8px;align-items:center}.mcd .sub{margin:0;font-size:12px;color:var(--ink2);line-height:1.4}
.mcd .big{font-size:30px;font-weight:600;letter-spacing:-.01em;line-height:1}.mcd .big small{font-size:12.5px;font-weight:400;color:var(--ink2);margin-left:6px;letter-spacing:0}
.mcd .bastack{height:22px}.mcd dl{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:5px 12px;margin:0;font-size:13px}
.mcd dt{color:var(--ink2)}.mcd dd{margin:0;text-align:right;font-weight:600;font-variant-numeric:tabular-nums}.mcd dt.in{padding-left:14px;font-size:12.5px}.mcd dd.in{font-weight:500;font-size:12.5px;color:var(--ink2)}
.mcd i.sw{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:6px;vertical-align:0}
.mm{display:grid;grid-template-columns:minmax(0,1fr) 22px minmax(0,1.1fr) 22px minmax(0,.8fr) 22px minmax(0,1.4fr);gap:8px;align-items:center}
.mm>span{color:var(--muted);text-align:center;font-size:18px}.mm .col{display:grid;gap:8px}
.mm .bx{background:var(--chip);border-radius:9px;padding:10px 12px;font-size:12.5px;color:var(--ink2);line-height:1.35}.mm .bx b{display:block;font-size:13.5px;color:var(--ink);margin-bottom:2px}
.mm .q{text-align:center;background:var(--accent-soft)}.mm .ok{background:color-mix(in srgb,#3f9b57 12%,transparent)}.mm .oth{background:color-mix(in srgb,#e0a03a 16%,transparent)}
@media (max-width:900px){.mm{grid-template-columns:minmax(0,1fr)}.mm>span{transform:rotate(90deg)}}
.mm.mm3{grid-template-columns:minmax(0,1fr) 22px minmax(0,1fr) 22px minmax(0,1fr) 22px minmax(0,1fr)}
@media (max-width:900px){.mm.mm3{grid-template-columns:minmax(0,1fr)}}
.mm .wr{background:color-mix(in srgb,#d64545 10%,transparent)}
.mlg{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.mlg figure{margin:0;position:relative;background:var(--chip);border-radius:8px;padding:8px;display:grid;gap:5px;min-width:0}
.mlg img{width:100%;height:56px;object-fit:contain;border-radius:4px;background:var(--thumbbg)}
.mlg figcaption{font-size:12px;color:var(--ink2)}.mlg figcaption b{color:var(--ink)}
.mlg .softtag{top:12px;left:12px}
.ck{display:grid;grid-template-columns:28px minmax(0,1fr) 120px;gap:12px;align-items:start;font-size:13px}
.ck .ic{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;font-size:12px;font-weight:700;color:#fff}
.ck .ic.y{background:#3f9b57}.ck .ic.t{background:#e0a03a}.ck b{display:block;font-size:13.5px}.ck span{color:var(--ink2);font-size:12.5px;line-height:1.45}
.ck .st{text-align:right}
.sq4{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.1fr) minmax(0,.9fr) minmax(0,1.2fr);gap:16px;align-items:end}
@media (max-width:1000px){.sq4{grid-template-columns:repeat(2,minmax(0,1fr))}}
.sq4 figure{margin:0;display:grid;gap:6px;min-width:0}.sq4 img{width:100%;height:280px;object-fit:contain;background:var(--thumbbg);border-radius:6px}
.sq4 img.px{image-rendering:pixelated;height:140px}.sq4 figcaption{font-size:12.5px;color:var(--ink2)}.sq4 figcaption b{display:block;color:var(--ink);font-size:13px}
.subtabs{display:flex;gap:4px;flex-wrap:wrap;background:var(--card);border-radius:10px;box-shadow:var(--shadow);padding:5px;justify-self:start}
.subtabs button{font:inherit;font-size:13px;font-weight:600;border:0;background:none;color:var(--ink2);padding:7px 14px;border-radius:7px;cursor:pointer}
.subtabs button[aria-selected="true"]{background:var(--accent-soft);color:var(--accent)}
.subtabs button:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.mx3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}@media (max-width:1000px){.mx3{grid-template-columns:minmax(0,1fr)}}
.mxe{background:var(--chip);border-radius:10px;padding:10px 12px;display:grid;gap:8px;min-width:0}.mxe img{width:100%;height:64px;object-fit:contain;border-radius:5px;background:var(--thumbbg)}
.mxe dl{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 10px;margin:0;font-size:12.5px}.mxe dt{color:var(--muted)}.mxe dd{margin:0;font-weight:600}
.mxe .res{font-size:12.5px;font-weight:600;border-radius:999px;padding:3px 10px;justify-self:start}.mxe .res.ok{background:color-mix(in srgb,#3f9b57 14%,transparent);color:#1d6b32}.mxe .res.av{background:color-mix(in srgb,#e0a03a 18%,transparent);color:#7a4c00}.mxe .res.lo{background:color-mix(in srgb,#d64545 12%,transparent);color:#9b2c2c}
.stps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}
@media (max-width:1100px){.stps{grid-template-columns:repeat(2,minmax(0,1fr))}}@media (max-width:700px){.stps{grid-template-columns:minmax(0,1fr)}}
.stp{background:var(--chip);border-radius:10px;padding:14px 16px;display:grid;gap:10px;align-content:start;min-width:0}
.stp .nm{display:flex;align-items:baseline;gap:10px}.stp .nm i{font-style:normal;font-size:11px;font-weight:700;color:var(--accent);letter-spacing:.06em}.stp .nm b{font-size:14.5px}
.stp .vis{background:var(--card);border-radius:8px;padding:10px;min-height:96px;display:grid;align-content:center;gap:6px}
.stp p{margin:0;font-size:12.5px;color:var(--ink2);line-height:1.45}.stp p b{color:var(--ink)}
.stp .vis img{width:100%;height:auto;border-radius:4px;display:block;background:var(--thumbbg)}
.stp .pair{display:grid;grid-template-columns:minmax(0,1fr) 22px minmax(0,1.3fr);gap:8px;align-items:center}.stp .pair span{text-align:center;color:var(--muted)}
.stp .pair figcaption,.stp .aug figcaption{font-size:11px;color:var(--muted);margin-top:3px;text-align:center}.stp figure{margin:0;min-width:0}
.stp .aug{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.stp .tl{display:flex;flex-wrap:wrap;gap:6px}.stp .tl span{background:var(--chip);border-radius:6px;padding:5px 8px;font-size:12px;line-height:1.3;flex:1 1 0;min-width:0}.stp .tl span b{display:block;font-size:12.5px}.stp .tl .ar{flex:0 0 auto;align-self:center;color:var(--muted);background:none;padding:0}
.stp .wb{display:grid;gap:4px;font-size:11.5px;color:var(--ink2)}.stp .wb div{display:grid;grid-template-columns:96px minmax(0,1fr) 42px;gap:8px;align-items:center}.stp .wb i{display:block;height:8px;background:var(--chip);border-radius:4px;overflow:hidden}.stp .wb em{display:block;height:100%;border-radius:4px}.stp .wb b{text-align:right;font-size:11.5px;color:var(--ink)}
.stp .rounds{display:grid;grid-template-columns:repeat(21,minmax(0,1fr));gap:3px}.stp .rounds i{display:block;height:18px;border-radius:3px;background:var(--base);opacity:.55}.stp .rounds i.best{background:var(--s1);opacity:1;outline:2px solid var(--accent);outline-offset:1px}.stp .rounds i.wait{background:var(--chip);opacity:1;border:1px solid var(--grid)}
.stp .rlg{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
.stp .splt{display:flex;height:24px;border-radius:6px;overflow:hidden;gap:2px}.stp .splt div{display:grid;place-items:center;color:#fff;font-size:11.5px;font-weight:600;white-space:nowrap;overflow:hidden;min-width:0}
.steps6 td:first-child{width:150px}
.codestep{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.6fr);gap:16px;align-items:start}
.codestep h3{margin:0 0 4px;font-size:14px}.codestep p{margin:0;color:var(--ink2);font-size:13px}
.codestep pre.code{max-height:280px;overflow:auto;font-size:12px}
@media (max-width:900px){.codestep{grid-template-columns:minmax(0,1fr)}}
.stage2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
@media (max-width:900px){.stage2{grid-template-columns:minmax(0,1fr)}}
.stg{border-radius:10px;padding:14px 16px;display:grid;gap:8px;align-content:start}
.stg.now{background:var(--accent-soft)}.stg.next{background:var(--chip)}
.stg .when{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--accent)}
.stg h3{margin:0;font-size:16px}.stg .what{margin:0;color:var(--ink2)}
.appills{display:flex;gap:8px;flex-wrap:wrap}
.appills button{font:inherit;font-size:13px;font-weight:500;display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:10px;border:1px solid var(--grid);background:var(--card);color:var(--ink);cursor:pointer}
.appills button[aria-selected="true"]{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft);font-weight:600}
.appills .num{width:20px;height:20px;font-size:11px;margin:0}
.pl{font-size:10.5px;font-weight:600;border-radius:999px;padding:1px 7px}
.pl-now{background:color-mix(in srgb,#0ca30c 16%,transparent);color:#0a7a0a}.pl-later{background:var(--accent-soft);color:var(--accent)}.pl-skip{background:var(--chip);color:var(--muted)}
.apx{border:1px solid var(--grid);border-radius:12px;padding:16px 18px;display:grid;gap:10px}
.apx-head{display:flex;align-items:flex-start;gap:12px;flex-wrap:wrap}.apx-head>div{flex:1;min-width:240px}
.apx-head h3{margin:0;font-size:18px}.apx-head .idea{margin:2px 0 0;font-size:15px;color:var(--ink2)}
.plan{font-size:12px;font-weight:600;border-radius:999px;padding:4px 12px}.plan-now{background:color-mix(in srgb,#0ca30c 16%,transparent);color:#0a7a0a}.plan-later{background:var(--accent-soft);color:var(--accent)}.plan-skip{background:var(--chip);color:var(--muted)}
.apx h4{margin:4px 0 6px;font-size:12px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.apx-flow{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.apx-flow .fs,.apx-flow .fq{background:var(--chip);border-radius:9px;padding:9px 12px;font-size:13px;max-width:220px;display:flex;gap:8px;align-items:flex-start}
.apx-flow .fq{background:var(--accent-soft);font-weight:600}
.apx-flow .fn{flex:none;width:18px;height:18px;border-radius:50%;background:var(--card);display:grid;place-items:center;font-size:10.5px;font-weight:700;color:var(--muted)}
.apx-flow .fa{color:var(--muted)}
.fyn{display:grid;gap:6px}.fyn>div{border-radius:9px;padding:6px 12px;font-size:13px}
.fy{background:color-mix(in srgb,var(--s2) 16%,transparent)}.fno{background:color-mix(in srgb,var(--s1) 12%,transparent)}
.ba{display:grid;gap:12px}
.waf{display:grid;grid-template-columns:repeat(10,1fr);gap:3px;width:150px}
.waf i{aspect-ratio:1;border-radius:3px;background:#cfe9d4}
.waf i.w{background:#d64545}.waf i.b{background:#4a8bd0}.waf i.o{background:#c9ced4}
.wtiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:14px}
.wt{background:var(--chip);border-radius:12px;padding:14px 16px;display:grid;gap:8px;justify-items:start;align-content:start}
.wt .h{font-size:13px;font-weight:600}.wt .n{font-size:24px;font-weight:600;line-height:1;font-variant-numeric:tabular-nums}
.wt .n small{font-size:12.5px;font-weight:400;color:var(--ink2)}.wt p{margin:0;font-size:12.5px;color:var(--ink2)}
.wkey{display:flex;gap:16px;flex-wrap:wrap;font-size:12.5px;color:var(--ink2);margin-bottom:10px}
.wkey i{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.fitx{display:grid;grid-template-columns:minmax(0,1.4fr) 40px minmax(0,1fr);gap:12px;align-items:center}
.fitx figure{margin:0;display:grid;gap:6px}.fitx img{width:100%;height:auto;border-radius:6px;background:var(--thumbbg)}
.fitx figcaption{font-size:12.5px;color:var(--ink2)}.fitx .arr{font-size:26px;color:var(--muted);text-align:center}
@media (max-width:800px){.fitx{grid-template-columns:minmax(0,1fr)}.fitx .arr{transform:rotate(90deg)}}
.whyc{display:grid;grid-template-columns:auto auto minmax(0,1fr);gap:22px;align-items:center}
.whyc figure{margin:0;display:grid;gap:6px;justify-items:start}.whyc figcaption{font-size:12.5px;color:var(--ink2)}
.whyc img{border:2px dashed var(--base);border-radius:4px}
.whyc ul{margin:0;padding-left:18px;display:grid;gap:6px;font-size:13.5px}
@media (max-width:900px){.whyc{grid-template-columns:minmax(0,1fr)}}
.fit3{display:grid;grid-template-columns:minmax(0,1.2fr) 28px minmax(0,1fr) minmax(0,.55fr);gap:16px;align-items:end}
.fit3 figure{margin:0;display:grid;gap:6px;min-width:0}.fit3 img{width:100%;height:auto;border-radius:5px;background:var(--thumbbg)}
.fit3 .sq img{width:auto;max-width:100%;height:96px;border:2px dashed var(--base);background:none}
.fit3 figcaption{font-size:12.5px;color:var(--ink2)}.fit3 figcaption b{color:var(--ink)}
.fit3 .arr{align-self:center;font-size:22px;color:var(--muted);text-align:center}
@media (max-width:900px){.fit3{grid-template-columns:minmax(0,1fr)}.fit3 .arr{display:none}}
.rl{display:grid;gap:10px}
.rr{display:grid;grid-template-columns:var(--c1,150px) minmax(0,1fr) 64px;gap:12px;align-items:center;font-size:13px}
.rr img{width:100%;height:34px;object-fit:cover;object-position:left;border-radius:4px;background:var(--thumbbg)}
.rr .lb b{display:block;font-size:13px}.rr .lb span{font-size:12px;color:var(--ink2)}
.rr .tk{height:10px;background:var(--chip);border-radius:5px;overflow:hidden}.rr .tk div{height:100%;border-radius:5px}
.rr .v{text-align:right;font-weight:600;font-variant-numeric:tabular-nums}
.rr.now .lb b{color:var(--accent)}
.big3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-items:end}
.big3 .bx{display:grid;gap:8px;justify-items:start}
.big3 .fr{border:2px dashed var(--base);border-radius:4px;background:var(--chip)}
.big3 b{font-size:14px}.big3 span{font-size:12.5px;color:var(--ink2)}
.chipst{font-size:12px;font-weight:600;border-radius:999px;padding:3px 10px;background:var(--chip);color:var(--ink2)}
@media (max-width:800px){.big3{grid-template-columns:minmax(0,1fr)}}
.howu{display:grid;gap:14px}
.hrow{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) 26px 190px;gap:16px;align-items:center}
.hrow img{width:100%;height:76px;object-fit:contain;object-position:left;border-radius:5px;background:var(--thumbbg)}
.hrow .sc{display:grid;gap:6px}.hrow .sc div{display:grid;grid-template-columns:110px minmax(0,1fr) 44px;gap:8px;align-items:center;font-size:12.5px}
.hrow .sc i{display:block;height:9px;border-radius:5px;background:var(--s1)}.hrow .sc i.l{background:var(--base)}
.hrow .arr{font-size:20px;color:var(--muted);text-align:center}
.hrow .out{border-radius:9px;padding:9px 12px;font-size:13px}.hrow .out b{display:block;font-size:15px}
.hrow .ok{background:color-mix(in srgb,#0ca30c 12%,transparent)}.hrow .oth{background:var(--chip)}
@media (max-width:800px){.hrow{grid-template-columns:minmax(0,1fr)}.hrow .arr{display:none}}
.lookx{display:grid;gap:14px}
.lrow{display:grid;grid-template-columns:minmax(0,1fr) 28px minmax(0,1fr);gap:14px;align-items:center}
.lrow figure{margin:0;display:grid;gap:5px;min-width:0}
.lrow img{width:100%;height:82px;object-fit:contain;object-position:left;border-radius:5px;background:var(--thumbbg)}
.lrow figcaption{font-size:12.5px;color:var(--ink2)}.lrow figcaption b{color:var(--ink)}
.lrow .eq{font-size:20px;color:var(--muted);text-align:center}
@media (max-width:800px){.lrow{grid-template-columns:minmax(0,1fr)}.lrow .eq{display:none}}
.chip{display:inline-block;font-size:11.5px;font-weight:600;border-radius:999px;padding:2px 9px;background:var(--chip);color:var(--ink2);white-space:nowrap}
.chip.do{background:color-mix(in srgb,#0ca30c 14%,transparent);color:#0a7a0a}.chip.nx{background:var(--accent-soft);color:var(--accent)}
.chip.tr{background:var(--chip);color:var(--muted)}.chip.dq{background:color-mix(in srgb,#e0a03a 20%,transparent);color:#8a5a00}
.misc .body{display:grid;grid-template-columns:240px minmax(0,1fr);gap:18px;align-items:start}
.misc .refb{display:grid;gap:6px}.misc .refb img{width:100%;height:auto;max-height:110px;object-fit:contain;object-position:left;border-radius:5px;background:var(--thumbbg)}
.misc .refb span{font-size:12.5px;color:var(--ink2)}
.misg{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}
.misg figure{margin:0;display:grid;gap:5px;min-width:0}.misg img{width:100%;height:64px;object-fit:contain;object-position:left;border-radius:5px;background:var(--thumbbg)}
.misg figcaption{font-size:12px;color:var(--ink2);display:grid;gap:3px}
@media (max-width:800px){.misc .body{grid-template-columns:minmax(0,1fr)}}
.phg{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px}
.phg figure{margin:0;display:grid;gap:6px}.phg img{width:100%;height:190px;object-fit:contain;background:var(--thumbbg);border-radius:6px}
.phg figcaption{font-size:12.5px;color:var(--ink2)}.phg figcaption b{color:var(--ink)}
.prs{display:grid;gap:12px}
.pr3{display:grid;grid-template-columns:minmax(0,1fr) 26px minmax(0,1.6fr);gap:14px;align-items:center;background:var(--chip);border-radius:12px;padding:12px 14px}
.pr3 figure{margin:0;display:grid;gap:6px;min-width:0}.pr3 .me img{width:100%;height:110px;object-fit:contain;background:var(--thumbbg);border-radius:8px}
.pr3 .eqs{font-size:24px;color:var(--muted);text-align:center}.pr3 .sch{font-size:11px;color:var(--muted)}
.pr3 .nb{display:grid;gap:6px;min-width:0}.nb3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}
.nb3 img{width:100%;height:80px;object-fit:contain;background:var(--thumbbg);border-radius:6px}.nb3 figcaption{font-size:11.5px;color:var(--ink2);text-align:center}
@media (max-width:900px){.pr3{grid-template-columns:minmax(0,1fr)}.pr3 .eqs{display:none}}
.pr{display:grid;grid-template-columns:minmax(0,1fr) 210px minmax(0,1fr);gap:16px;align-items:center;background:var(--chip);border-radius:12px;padding:12px 14px}
.pr figure{margin:0;display:grid;gap:6px;min-width:0}.pr img{width:100%;height:120px;object-fit:contain;background:var(--thumbbg);border-radius:8px}
.pr figcaption{font-size:11.5px;color:var(--muted)}
.tagr{justify-self:start;font-size:12px;font-weight:600;border-radius:999px;padding:3px 10px}
.tagr.ok{background:color-mix(in srgb,#3f9b57 16%,transparent);color:#2d7a42}.tagr.no{background:color-mix(in srgb,#d64545 14%,transparent);color:#a32424}
.pr .mid{display:grid;gap:6px;justify-items:center;text-align:center}
.pr .alike{font-size:12px;font-weight:600;color:var(--ink2)}.pr .eqs{font-size:26px;line-height:1;color:var(--muted)}.pr .sch{font-size:11px;color:var(--muted)}
.sc2{display:grid;gap:4px;width:100%}.sc2 span{display:grid;grid-template-columns:76px minmax(0,1fr) 36px;gap:6px;align-items:center}
.sc2 i{font-style:normal;font-size:11.5px;color:var(--ink2);text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sc2 b{display:block;height:8px;border-radius:4px;min-width:2px}.sc2 b.w{background:#d64545}.sc2 b.r{background:#3f9b57}
.sc2 em{font-style:normal;font-size:11.5px;text-align:right;font-variant-numeric:tabular-nums}
@media (max-width:800px){.pr{grid-template-columns:minmax(0,1fr)}}
.fl{display:grid;gap:10px}
.eg h3{margin:18px 0 4px;font-size:14px}.eg h3:first-child{margin-top:0}.eg>p{margin:0 0 10px;font-size:13px;color:var(--ink2)}
.rk{display:grid;gap:12px}
.rkr{display:grid;grid-template-columns:200px minmax(0,1fr);gap:16px;align-items:start;background:var(--chip);border-radius:12px;padding:12px 14px}
.rkr>img{width:100%;height:180px;object-fit:contain;background:var(--thumbbg);border-radius:8px}
.rkr h3{margin:0 0 4px;font-size:14px}.rkr .rs{margin:0 0 8px;font-size:13px;color:var(--ink)}.rkr .rs b{color:var(--accent)}
.rkm{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
.rkc{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px}
.rkc figure{margin:0;display:grid;gap:3px;min-width:0}.rkc img{width:100%;height:46px;object-fit:contain;background:var(--thumbbg);border-radius:5px}
.rkc .noimg{height:46px;display:grid;place-items:center;border:1px dashed var(--base);border-radius:5px;font-size:11px;color:var(--muted)}
.rkc figcaption{font-size:11.5px;color:var(--ink2)}.rkc figure{position:relative}
.exf{position:relative;margin:0}.exf img{width:100%;object-fit:cover;object-position:left;border-radius:4px;background:var(--thumbbg)}
.rk1g{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}@media (max-width:900px){.rk1g{grid-template-columns:minmax(0,1fr)}}
.rk1{display:grid;grid-template-columns:54px 150px minmax(0,1fr);gap:12px;align-items:center;background:var(--chip);border-radius:8px;padding:8px 10px;min-width:0}
.rk1>img{width:54px;height:66px;object-fit:contain;border-radius:4px;background:var(--thumbbg)}
.rk1 figure{position:relative;margin:0}.rk1 figure img{width:100%;height:38px;object-fit:cover;object-position:left;border-radius:4px;background:var(--thumbbg)}
.rk1 .t{font-size:12.5px;min-width:0}.rk1 .t b{display:block;font-size:12.5px}.rk1 .t span{color:var(--ink2);font-size:12px}.softtag{position:absolute;top:4px;left:4px;font-size:10.5px;font-weight:600;background:#e0a03a;color:#fff;border-radius:999px;padding:1px 7px}.rkc figcaption b{color:var(--ink)}
@media (max-width:800px){.rkr{grid-template-columns:minmax(0,1fr)}}
.egg{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.egc{background:var(--chip);border-radius:10px;padding:8px;display:grid;gap:5px;font-size:12px;color:var(--ink2);min-width:0}
.egc img{width:100%;height:70px;object-fit:contain;background:var(--thumbbg);border-radius:6px}
.egc .noimg{height:70px;display:grid;place-items:center;border-radius:6px;border:1px dashed var(--base);font-size:11.5px;color:var(--muted)}
.egc b{color:var(--ink)}
.egr{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}.egr figure{margin:0;display:grid;gap:6px}
.egr img{width:100%;height:220px;object-fit:contain;background:var(--thumbbg);border-radius:8px}.egr figcaption{font-size:12.5px;color:var(--ink2)}.egr figcaption b{color:var(--ink)}
.gp{display:block;margin-top:4px;font-size:12px;color:var(--ink2);font-variant-numeric:tabular-nums}
.rule1{margin:4px 0 0;font-size:13px;color:var(--ink2);background:var(--accent-soft);border-radius:8px;padding:8px 12px}.rule1 b{color:var(--ink)}
.mxg{display:grid;grid-template-columns:340px minmax(0,1fr);gap:20px;align-items:start}
@media (max-width:900px){.mxg{grid-template-columns:minmax(0,1fr)}}
.mxl{display:grid;gap:6px}
.mxl button{font:inherit;text-align:left;border:1px solid var(--grid);background:var(--card);border-radius:10px;padding:9px 12px;cursor:pointer;display:grid;gap:5px;color:var(--ink)}
.mxl button:hover{border-color:var(--accent)}
.mxl button[aria-selected="true"]{border-color:var(--accent);background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent)}
.mxl .ln{display:flex;align-items:center;gap:6px;flex-wrap:wrap;font-size:13px}.mxl .ln em{font-style:normal;color:var(--muted)}
.mxl .ct{margin-left:auto;font-weight:600;font-variant-numeric:tabular-nums}
.mxl .bar{height:6px;border-radius:3px;background:var(--chip);overflow:hidden}.mxl .bar div{height:100%;background:#d64545;border-radius:3px}
.mxd{display:grid;gap:12px;min-width:0}.mxd h3{margin:0;font-size:16px}.mxd p{margin:0;font-size:13.5px;color:var(--ink2)}
.mxd .pr{grid-template-columns:minmax(0,1fr) 190px minmax(0,1fr)}
@media (max-width:800px){.mxd .pr{grid-template-columns:minmax(0,1fr)}}
@media (max-width:700px){.nxr{grid-template-columns:minmax(0,1fr) auto!important}.nxr>span:nth-child(2){grid-column:1/-1;grid-row:2}.fi .n{font-size:16px;white-space:nowrap}}
.fi{display:grid;grid-template-columns:72px minmax(0,1fr) auto;gap:14px;align-items:center;border-radius:10px;background:var(--chip);padding:10px 14px}
.fi .n{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;color:var(--accent)}.fi b{display:block;font-size:13.5px}.fi span{font-size:12.5px;color:var(--ink2)}
.fi button{font:inherit;font-size:12.5px;border:1px solid var(--grid);background:var(--card);border-radius:8px;padding:5px 10px;cursor:pointer;color:var(--accent);white-space:nowrap}
.fi button:hover{border-color:var(--accent)}
.fd{display:grid;grid-template-columns:minmax(0,1fr) 230px auto;gap:16px;align-items:center;border-radius:10px;background:var(--chip);padding:12px 14px}
.fd>div:first-child b{display:block;font-size:13.5px;margin-bottom:2px}.fd>div:first-child span{font-size:12.5px;color:var(--ink2);line-height:1.4}
.fb{display:grid;gap:5px}.fb div{display:grid;grid-template-columns:86px minmax(0,1fr) 62px;gap:8px;align-items:center;font-size:11.5px;color:var(--ink2)}
.fb i{display:block;height:8px;background:var(--card);border-radius:4px;overflow:hidden}.fb em{display:block;height:100%;background:var(--base);border-radius:4px}.fb em.hi{background:var(--s1)}
.fb b{font-size:12px;color:var(--ink);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.fd button{font:inherit;font-size:12.5px;border:1px solid var(--grid);background:var(--card);border-radius:8px;padding:5px 10px;cursor:pointer;color:var(--accent);white-space:nowrap}
.fd button:hover{border-color:var(--accent)}
@media (max-width:700px){.fd{grid-template-columns:minmax(0,1fr)}}
@media (max-width:700px){.fi{grid-template-columns:minmax(0,1fr)}.fi button{justify-self:start}}
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,230px),1fr));gap:12px}
.strip figure{margin:0;display:grid;gap:6px}.strip img{width:100%;height:auto;border-radius:6px;background:var(--thumbbg)}
.strip figcaption{font-size:12.5px;color:var(--ink2)}.strip figcaption b{display:block;font-size:20px;color:var(--ink)}
.strip .now img{outline:3px solid var(--accent);outline-offset:2px}
.sizes{display:grid;gap:18px}
.sz{display:grid;grid-template-columns:auto minmax(0,1fr);gap:18px;align-items:center}
.sz .frame{border:2px dashed var(--base);border-radius:4px;overflow:hidden;line-height:0;max-width:100%}
.sz .frame img{width:100%;height:auto}
.sz .t b{display:block;font-size:15px}.sz .t span{font-size:12.5px;color:var(--ink2)}.sz .t .r{font-size:20px;font-weight:600;color:var(--ink);display:block;margin-top:4px}
@media (max-width:700px){.sz{grid-template-columns:minmax(0,1fr)}}
.setc{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:14px}
.setc .wt .meter{width:100%;height:12px;background:var(--card);border-radius:6px;overflow:hidden}.setc .wt .meter div{height:100%;background:var(--s1)}
.ans{color:var(--ink)!important;font-size:14px!important}.ans b{color:var(--accent)}
.h3s{margin:0 0 6px;font-size:13px;font-weight:600}
.callout-lite{margin:0;background:var(--accent-soft);border-radius:10px;padding:10px 14px;font-size:13.5px}
.resx{display:grid;gap:16px}
.resrow{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr);gap:16px;align-items:start}
.resrow figure{margin:0;display:grid;gap:5px;min-width:0}
.resrow img{width:100%;height:auto;max-height:150px;object-fit:contain;object-position:left;background:var(--thumbbg);border-radius:6px}
.resrow .mv img{width:auto;max-width:100%;height:auto;image-rendering:pixelated}
.resrow figcaption{font-size:12px;color:var(--ink2)}
@media (max-width:800px){.resrow{grid-template-columns:minmax(0,1fr)}}
.barow{display:grid;grid-template-columns:200px minmax(0,1fr) 150px;gap:14px;align-items:center}
.barow .nm b{display:block;font-size:13.5px}.barow .nm span{font-size:12px;color:var(--ink2)}
.bastack{display:flex;height:30px;border-radius:7px;overflow:hidden;gap:2px}
.bastack div{display:grid;place-items:center;color:#fff;font-size:12px;font-weight:600;min-width:0;overflow:hidden;white-space:nowrap}
.barow .ac{font-size:12.5px;color:var(--ink2)}.barow .ac b{font-size:16px;color:var(--ink);display:block}
@media (max-width:700px){.barow{grid-template-columns:minmax(0,1fr)}}
.enx{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:12px}
@media (max-width:600px){.enx{grid-template-columns:minmax(0,1fr)}}
.enc{background:var(--chip);border-radius:10px;padding:12px;display:grid;gap:8px;align-content:start;min-width:0}
.enc img{height:62px;max-width:100%;object-fit:contain;object-position:left;border-radius:4px;background:var(--thumbbg)}
.enc .st{font-size:11.5px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.enc .sr{display:grid;grid-template-columns:86px minmax(0,1fr) 34px;gap:6px;align-items:center;font-size:12px}
.enc .sr .b{height:9px;border-radius:0 3px 3px 0;background:var(--s1)}
.enc .sr .n{text-align:right;font-variant-numeric:tabular-nums}
.enc .meter{position:relative;height:16px;background:var(--card);border-radius:4px}
.enc .meter .f{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
.enc .meter .c{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink)}
.enc .mk{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
.enc .big{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums}
.enc .verdict{border-radius:8px;padding:7px 10px;font-size:13px}
.enc .ok{background:color-mix(in srgb,#0ca30c 14%,transparent)}.enc .oth{background:color-mix(in srgb,var(--s2) 16%,transparent)}
.enxnote{margin:10px 0 0;font-size:13px;color:var(--ink2)}
.apx-two{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:22px;align-items:start}
@media (max-width:900px){.apx-two{grid-template-columns:minmax(0,1fr)}}
.apx-gb p{margin:0 0 6px;font-size:13.5px}
.apx-gb ul{margin:0 0 6px;padding:0;list-style:none;display:grid;gap:6px;font-size:13.5px}
.apx-gb li{padding-left:24px;position:relative}
.apx-gb ul.good li::before{content:"✓";position:absolute;left:4px;color:#0ca30c;font-weight:700}
.apx-gb ul.bad li::before{content:"✕";position:absolute;left:4px;color:#d03b3b;font-weight:700}
.pairrow{display:grid;grid-template-columns:220px minmax(0,1fr);gap:16px;padding:12px 0;border-bottom:1px solid var(--grid);align-items:start}
.pairrow:last-child{border-bottom:0}
.pairrow .pn b{display:block;font-size:14px}.pairrow .pn span{display:block;color:var(--ink2);font-size:12.5px}
.pairrow .pbar{height:10px;border-radius:0 4px 4px 0;background:var(--s2);margin:6px 0 2px}
.thumbs4{display:flex;gap:8px;flex-wrap:wrap}
.thumbs4 figure{margin:0;display:grid;gap:3px}
.panel img:not(#lb-img){cursor:zoom-in}
.thumbs4 img{height:46px;max-width:210px;object-fit:contain;object-position:left;border-radius:4px;background:var(--thumbbg)}
.thumbs4 figcaption{font-size:11.5px;color:var(--ink2)}
.whygrid figure{margin:0;display:grid;gap:4px;min-width:0}
.whygrid img{height:56px;max-width:100%;object-fit:contain;object-position:left;border-radius:4px;background:var(--thumbbg)}
.whygrid figcaption{font-size:11.5px;color:var(--ink2)}
@media (max-width:700px){.pairrow{grid-template-columns:minmax(0,1fr)}.thumbs4 figure{flex:1 1 calc(50% - 8px);min-width:0}.thumbs4 img{max-width:100%}}
.mix{display:flex;height:34px;border-radius:8px;overflow:hidden;gap:2px;margin:2px 0 8px}
.mix div{display:grid;place-items:center;color:#fff;font-size:12px;font-weight:600;min-width:0;overflow:hidden;white-space:nowrap}
.mixkey{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12.5px;color:var(--ink2);margin-bottom:6px}
.mixkey i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.rgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:12px}
.rcard{background:var(--chip);border-radius:12px;padding:14px 16px;display:grid;gap:6px;align-content:start;border-top:4px solid var(--rc,var(--s1))}
.rcard .rn{font-size:11.5px;font-weight:600;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
.rcard .big{font-size:26px;font-weight:600;line-height:1.1;color:var(--ink);font-variant-numeric:tabular-nums}
.rcard .bl{font-size:12.5px;color:var(--ink2);margin-top:-2px}
.rcard b{font-size:14px;margin-top:6px}.rcard p{margin:0;font-size:13px;color:var(--ink2)}
.quad{display:grid;grid-template-columns:28px minmax(0,1fr) minmax(0,1fr);grid-template-rows:auto auto 26px;gap:10px}
.quad .ax{font-size:11.5px;font-weight:600;color:var(--muted);letter-spacing:.04em;text-transform:uppercase;display:grid;place-items:center}
.quad .ay{writing-mode:vertical-rl;transform:rotate(180deg)}
.quad .q{border-radius:12px;padding:12px 14px;display:grid;gap:8px;align-content:start;min-height:120px}
.quad .q h4{margin:0;font-size:13px}.quad .q h4 span{font-weight:400;color:var(--ink2)}
.quad .q1{background:color-mix(in srgb,#0ca30c 12%,transparent)}.quad .q2{background:var(--accent-soft)}
.quad .q3{background:color-mix(in srgb,#fab219 16%,transparent)}.quad .q4{background:var(--chip)}
.idea{background:var(--card);border-radius:9px;box-shadow:var(--shadow);padding:9px 12px;display:grid;gap:3px}
.idea b{font-size:13.5px}.idea span{font-size:12.5px;color:var(--ink2)}
@media (max-width:700px){.quad{grid-template-columns:minmax(0,1fr)}.quad .ax{display:none}}
.canrows{display:grid;gap:8px}
.crow{display:grid;grid-template-columns:120px minmax(0,1fr) 62px 130px;gap:12px;align-items:center;font-size:13.5px}
.crow .tr{height:14px;background:var(--chip);border-radius:0 5px 5px 0}.crow .tr div{height:100%;border-radius:0 5px 5px 0}
.crow .v{font-variant-numeric:tabular-nums;font-weight:600;text-align:right}
.verd{font-size:12px;font-weight:600;border-radius:999px;padding:3px 10px;justify-self:start}
.v1{background:color-mix(in srgb,#0ca30c 16%,transparent);color:#0a7a0a}.v2{background:color-mix(in srgb,#fab219 22%,transparent);color:#8a5a00}
.v3{background:color-mix(in srgb,#d03b3b 14%,transparent);color:#a32424}.v4{background:var(--chip);color:var(--ink2)}
.cannote{margin-top:8px;background:var(--chip);border-radius:10px;padding:10px 14px;font-size:13px;color:var(--ink2)}
@media (max-width:700px){.crow{grid-template-columns:96px minmax(0,1fr) 58px}.crow .verd{display:none}}
.whygrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}
.go{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:16px}
.go button{font:inherit;text-align:left;background:var(--card);border:0;border-radius:10px;box-shadow:var(--shadow);padding:16px 18px;cursor:pointer;display:grid;gap:6px;color:var(--ink)}
.go button:hover{outline:2px solid var(--accent)}
.go .g1{font-size:11.5px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.go .g2{font-size:16px;font-weight:600}
.go .g3{font-size:13px;color:var(--ink2)}
.go .g4{font-size:22px;font-weight:600;color:var(--accent)}
@media (max-width:860px){
  .shell{width:auto;padding-inline:16px}
  .side{padding-block:10px}
  .topmeta{display:none}
  .nav{gap:8px}
  .nav .gl{display:none}
}
.ph h1{font-size:24px;font-weight:600;margin:0 0 4px;letter-spacing:-.01em;text-wrap:balance}
.ph p{margin:0;color:var(--ink2);max-width:90ch}
.kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}
.kpis.k3{grid-template-columns:repeat(3,minmax(0,1fr))}
.kpi{background:var(--card);border-radius:10px;box-shadow:var(--shadow);padding:12px 16px;min-width:0}
.kpi .k{font-size:12px;font-weight:500;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.kpi .v{font-size:22px;font-weight:600;letter-spacing:-.01em;margin-top:4px;line-height:1.2;overflow-wrap:anywhere}
.kpi .v.t{font-size:17px;padding-block:3px}
.kpi .f{font-size:12.5px;color:var(--ink2);margin-top:4px}
.callout{display:flex;gap:12px;align-items:flex-start;background:var(--accent-soft);border-radius:10px;padding:12px 16px;color:var(--ink)}
.callout .ic{flex:none;width:20px;height:20px;border-radius:50%;background:var(--accent);color:var(--onaccent);font-weight:600;font-size:13px;display:grid;place-items:center;margin-top:1px}
.callout p{margin:0;max-width:110ch}
.grid2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
@media (max-width:960px){.grid2{grid-template-columns:minmax(0,1fr)}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.kpis.k3{grid-template-columns:minmax(0,1fr)}}
.card{background:var(--card);border-radius:10px;box-shadow:var(--shadow);min-width:0;display:flex;flex-direction:column}
.card>header{padding:12px 18px 10px;border-bottom:1px solid var(--grid)}
.card>header h2{font-size:14px;font-weight:600;margin:0}
.card>header p{margin:2px 0 0;color:var(--muted);font-size:13px}
.card .body{padding:14px 18px 16px;min-width:0}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--ink2);margin:0 0 6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
.chart{width:100%;min-width:0;overflow-x:auto}
svg text{font-family:var(--sans)}

/* tables */
.tablebox{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--grid);white-space:nowrap}
th{font-weight:500;color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;background:var(--chip)}
th:first-child,td:first-child{text-align:left}
tbody tr:hover td{background:var(--chip)}
td.mono{font-family:var(--mono);font-size:12.5px;color:var(--ink2);text-align:left}
th.l{text-align:left}
.sum thead tr:first-child th{font-size:12px;color:var(--ink);text-transform:none;letter-spacing:0;font-weight:600;text-align:center;background:var(--card);border-bottom:1px solid var(--grid)}
.sum .gs{border-left:1px solid var(--grid)}
.sum tr.tot td{font-weight:600;border-top:2px solid var(--base);border-bottom:none;background:none}
.share{display:flex;align-items:center;gap:8px;justify-content:flex-end}
.share b{font-weight:400;min-width:44px;text-align:right}
.share i{display:block;height:8px;border-radius:0 3px 3px 0;min-width:2px}
.share .trk{width:90px;background:var(--grid);border-radius:0 3px 3px 0;flex:none}
.rank td,.rank th{white-space:normal;vertical-align:top;text-align:left}
.rank td:first-child{width:2.5em;color:var(--muted);font-weight:600}
.rank td.nm{font-weight:600;white-space:nowrap}
.rank td.nm span{display:block;font-weight:400;color:var(--muted);font-size:12.5px}
.rank td.why{min-width:240px;max-width:62ch}
.rank tr.pick td{background:var(--accent-soft)}
.pill{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;background:var(--accent);color:var(--onaccent);margin-left:6px;vertical-align:1px;white-space:nowrap}

/* lists, samples, files */
.steps{margin:0;padding-left:20px;display:grid;gap:8px}
.steps li::marker{color:var(--muted);font-weight:600}
.files p{margin:0 0 10px;color:var(--ink2)}
code{font-family:var(--mono);font-size:12.5px;background:var(--chip);padding:1px 6px;border-radius:4px}
pre{font-family:var(--mono);font-size:12.5px;background:var(--chip);padding:10px 12px;border-radius:6px;overflow-x:auto;margin:0}
.samples{display:grid;gap:12px}
.srow{display:grid;grid-template-columns:140px minmax(0,1fr);gap:12px;align-items:center;padding-bottom:12px;border-bottom:1px solid var(--grid)}
.srow:last-child{border-bottom:0;padding-bottom:0}
.srow .nm{font-weight:500;font-size:14px}
.srow .nm span{display:block;color:var(--muted);font-size:12px;font-weight:400}
.thumbs{display:flex;flex-wrap:wrap;gap:6px}
.thumbs button{display:block;flex:none;padding:0;border:0;background:none;cursor:zoom-in;border-radius:4px;line-height:0}
.thumbs button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.thumbs img{display:block;height:30px;width:auto;max-width:170px;object-fit:cover;border-radius:4px;background:var(--thumbbg)}
@media (max-width:640px){.srow{grid-template-columns:minmax(0,1fr)}}
#lb{position:fixed;inset:0;z-index:20;background:rgba(8,10,12,.84);display:grid;place-items:center;padding:24px}
#lb figure{margin:0;display:grid;gap:10px;justify-items:center;max-width:min(1100px,100%)}
#lb img{max-width:100%;height:auto;border-radius:6px;background:#000}
#lb figcaption{color:#e9edf1;font-size:13.5px;text-align:center}
#lb figcaption code{background:rgba(255,255,255,.12);color:#e9edf1}
#lb .bar{display:flex;gap:8px}
#lb .bar button{font:inherit;font-size:13px;padding:6px 14px;border-radius:6px;border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.08);color:#fff;cursor:pointer}
#tip{position:fixed;pointer-events:none;background:var(--ink);color:var(--card);font-size:12.5px;padding:6px 9px;border-radius:6px;line-height:1.4;z-index:30;max-width:260px;font-variant-numeric:tabular-nums}

/* added: diagrams, scorecards, flows, timeline */
.chips{display:flex;flex-wrap:wrap;gap:6px}
.lic{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:500;padding:2px 8px;border-radius:999px;white-space:nowrap}
.lic::before{content:"";width:7px;height:7px;border-radius:50%}
.lic.ok{background:color-mix(in srgb,#0ca30c 14%,transparent);color:var(--ink)}.lic.ok::before{background:#0ca30c}
.lic.warn{background:color-mix(in srgb,#fab219 22%,transparent);color:var(--ink)}.lic.warn::before{background:#d99400}
.lic.no{background:color-mix(in srgb,#d03b3b 14%,transparent);color:var(--ink)}.lic.no::before{background:#d03b3b}
.dots{display:inline-flex;gap:3px;vertical-align:-1px}
.dots i{width:9px;height:9px;border-radius:50%;background:var(--grid)}
.dots i.on{background:var(--accent)}
.score td{text-align:left;vertical-align:middle}
.score td:first-child{font-weight:600}
.score th{text-align:left}
.feat{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:22px;align-items:start}
@media (max-width:960px){.feat{grid-template-columns:minmax(0,1fr)}}
.feat .txt{display:grid;gap:10px}
.feat .txt p{margin:0}
.kv{display:grid;grid-template-columns:auto minmax(0,1fr);gap:6px 14px;margin:0}
.kv dt{font-size:11.5px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;padding-top:2px}
.kv dd{margin:0}
.num{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;background:var(--accent);color:var(--onaccent);font-size:12.5px;font-weight:600;margin-right:8px;flex:none}
.card>header h2{display:flex;align-items:center}
.tagrow{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
.tag{font-size:11.5px;font-weight:500;color:var(--ink2);background:var(--chip);padding:2px 8px;border-radius:999px}
.decide{display:grid;gap:14px;justify-items:center}
.decide .box{background:var(--chip);border:1px solid var(--grid);border-radius:10px;padding:10px 16px;text-align:center;max-width:520px}
.decide .q{background:var(--accent-soft);border-color:transparent;font-weight:600}
.decide .br{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px;width:100%;max-width:900px}
.decide .br>div{display:grid;gap:6px;justify-items:center}
.decide .lab{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}
.decide .arrow{width:2px;height:14px;background:var(--base)}
.decide .end{font-weight:600}
@media (max-width:640px){.decide .br{grid-template-columns:minmax(0,1fr)}}
.pipe{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
.pipe span{background:var(--chip);border:1px solid var(--grid);border-radius:8px;padding:6px 12px;font-size:13.5px;font-weight:500}
.pipe span.hl{background:var(--accent);border-color:var(--accent);color:var(--onaccent)}
.pipe b{color:var(--muted);font-weight:400}
.tl{list-style:none;margin:0;padding:0;display:grid}
.tl li{display:grid;grid-template-columns:34px minmax(0,1fr);gap:14px;position:relative;padding-bottom:18px}
.tl li:not(:last-child)::before{content:"";position:absolute;left:16px;top:30px;bottom:0;width:2px;background:var(--grid)}
.tl .n{width:32px;height:32px;border-radius:50%;background:var(--accent);color:var(--onaccent);display:grid;place-items:center;font-weight:600}
.tl h3{margin:4px 0 4px;font-size:15px}
.tl .what{margin:0 0 6px}
.tl .why{margin:0;color:var(--ink2);font-size:13.5px;background:var(--chip);border-radius:8px;padding:8px 12px}
.tl .why b{color:var(--ink)}
.srcs{margin:0;padding-left:18px;display:grid;gap:4px;font-size:13.5px}
.srcs a{color:var(--accent)}
.illus{font-size:11.5px;color:var(--muted);margin:6px 0 0}
.rthumbs img{height:40px;max-width:200px}
.status{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px;background:var(--chip);color:var(--ink2)}
.status::before{content:"";width:7px;height:7px;border-radius:50%;background:var(--muted)}
.status.done::before{background:#0ca30c}.status.running::before{background:#d99400}
.cm{font-variant-numeric:tabular-nums}
.seg{display:flex;gap:4px;margin-left:auto}
.seg button{font:inherit;font-size:12.5px;font-weight:500;padding:4px 12px;border-radius:999px;border:1px solid var(--grid);background:var(--card);color:var(--ink2);cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:var(--onaccent)}
.empty{padding:28px;text-align:center;color:var(--muted)}
details.card{overflow:hidden}
details.card>summary{list-style:none;cursor:pointer;padding:14px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
details.card>summary::-webkit-details-marker{display:none}
details.card>summary .tri{width:22px;height:22px;border-radius:50%;background:var(--chip);display:grid;place-items:center;flex:none;color:var(--ink2);font-size:11px;transition:transform .15s}
details.card[open]>summary .tri{transform:rotate(90deg)}
details.card>summary h2{font-size:15px;font-weight:600;margin:0}
details.card>summary .sum{color:var(--muted);font-size:13px;flex-basis:100%;padding-left:34px}
details.card[open]>summary{border-bottom:1px solid var(--grid)}
pre.code{background:#0f1419;color:#d6dde6;font-family:var(--mono);font-size:12.5px;line-height:1.55;padding:14px 16px;border-radius:8px;overflow-x:auto;margin:0;tab-size:4;white-space:pre-wrap;word-break:break-word}
pre.code .k{color:#ff9f43}pre.code .s{color:#9ee493}pre.code .c{color:#7d8a99;font-style:italic}pre.code .n{color:#82c4ff}pre.code .f{color:#e6c27a}
.why h4{margin:12px 0 4px;font-size:13.5px}
.why h4:first-child{margin-top:0}
.why p,.why li{margin:0;font-size:14px}
.why ul{margin:0;padding-left:18px;display:grid;gap:4px}
.set{display:grid;grid-template-columns:auto minmax(0,1fr);gap:4px 12px;font-size:13.5px;margin:0}
.set dt{font-family:var(--mono);font-size:12.5px;color:var(--accent);padding-top:1px}
.set dd{margin:0}
</style>
<div class="shell">
<aside class="side" aria-label="Report sections">
  <div class="brand"><span class="logo" aria-hidden="true">RT</span><div><b>RackTrack</b><small>Device classification report</small></div></div>
  <div class="topmeta" id="topmeta"></div>
  <nav class="nav" role="tablist" aria-label="Sections">
    <div class="grp"><span class="gl">Start</span>
      <div class="track">
      <button role="tab" data-k="ov" aria-selected="false">Overview</button>
      <button role="tab" data-k="apr" aria-selected="false">Approaches</button></div></div>
    <div class="grp"><span class="gl">Data</span>
      <div class="track">
      <button role="tab" data-k="all" aria-selected="true">All images</button>
      <button role="tab" data-k="hi" aria-selected="false">High resolution</button>
</div></div>
    <div class="grp"><span class="gl">Model</span>
      <div class="track">
      <button role="tab" data-k="clf" aria-selected="false">ConvNeXt-Tiny</button>
      <button role="tab" data-k="det" aria-selected="false">DINOv2-B detector</button>
      <button role="tab" data-k="res:convnext_tiny" aria-selected="false">Resolution</button></div></div>
    <div class="grp"><span class="gl">Results</span>
      <div class="track">
      <button role="tab" data-k="unk:convnext_tiny" aria-selected="false">Unknown</button>
      <button role="tab" data-k="why:convnext_tiny" aria-selected="false">Edge cases</button>
      <button role="tab" data-k="mis:convnext_tiny" aria-selected="false">Misclassified</button></div></div>
    <div class="grp"><span class="gl">Other devices</span>
      <div class="track">
      <button role="tab" data-k="oth" aria-selected="false">Other (unknown)</button>
</div></div>
  </nav>
  <div class="src">device_crops/ · models/device_cls/</div>
</aside>
<main>
<header class="pagehead"><div><p class="crumb" id="ph-crumb"></p><h1 id="ph-title"></h1><p id="ph-lead"></p></div><div class="act" id="ph-act"></div></header>


<!-- ============ Overview (start page) ============ -->
<section class="panel" id="p-ov" hidden>
  <div class="kpis" id="ov-kpis"></div>
  <div class="chips" id="ov-facts"></div>
  <section class="card"><header><h2>How it works</h2><p>From a rack photo to one answer per device. Two real test devices below show the two outcomes.</p></header>
    <div class="body"><div class="flow4" id="ov-flow"></div><div class="howu" id="ov-how" style="margin-top:18px"></div></div></section>
  <section class="card"><header><h2 id="ov-res-t">Results on the test crops</h2><p>Every test crop is a device the model never saw in training. Bars to scale; the same crops both times.</p></header>
    <div class="body"><div class="rl" id="ov-res"></div></div></section>
  <details class="card"><summary><span class="tri">▶</span><h2>All the scores</h2><span class="sum">Every number on this page and what it means, read from the result files.</span></summary>
    <div class="body"><table class="pick5" id="ov-scores"></table></div></details>
  <div class="grid2">
    <section class="card"><header><h2>What we found</h2><p>Each line opens the page with the proof.</p></header>
      <div class="body"><div class="fl" id="ov-find"></div></div></section>
    <section class="card"><header><h2 id="ov-can-t">What it identifies</h2><p>Share of each device type answered right, to scale, with the number of test crops of that type.</p></header>
      <div class="body"><div class="rl" id="ov-can"></div></div></section>
  </div>
  <section class="card"><header><h2>Next</h2><p>What could still raise accuracy, and what was tried.</p></header>
    <div class="body"><div class="rl" id="ov-next"></div></div></section>
</section>

<!-- ============ Overview / High resolution ============ -->
<section class="panel" id="p-dist">
  <div class="kpis" id="dist-kpis"></div>
  <section class="card"><header><h2>Class summary</h2><p>Instances and images per class and split, largest class first. An image with several classes counts once for each, so image shares add up to more than 100 %.</p></header>
    <div class="body"><div class="tablebox"><table class="sum" id="sum"></table></div></div></section>
  <section class="card"><header><h2>Where the data comes from</h2><p>The network team's labelled rack photos, cut into one picture per device.</p></header>
    <div class="body"><div class="flow4" id="dist-flow"></div>
      <p class="illus">One export had its class numbers shifted by one; it was corrected before cropping.</p></div></section>

  <section class="card"><header><h2>Instances per class</h2><p>One instance is one polygon label and one crop.</p></header>
    <div class="body"><div class="legend"><span><i style="background:var(--s1)"></i>train</span><span><i style="background:var(--s2)"></i>valid</span><span><i style="background:var(--s3)"></i>test</span></div><div class="chart" id="c-count"></div></div></section>
  <div class="grid2">
    <section class="card"><header><h2>Photo sizes</h2><p>How sharp the photos are, in megapixels (width × height).</p></header>
      <div class="body"><div id="c-photo"></div></div></section>
    <section class="card"><header><h2>Typical crop size per class</h2><p>Each box is the typical crop of the class, drawn to scale.</p></header>
      <div class="body"><div id="c-cropsz"></div></div></section>
  </div>
  <section class="card"><header><h2 id="cres-t">Crop resolution</h2><p id="cres-s"></p></header>
    <div class="body"><div class="grid2"><div><h3 class="h3s">Height of the crops</h3><div class="rl" id="cres-h"></div></div><div><h3 class="h3s">Width of the crops</h3><div class="rl" id="cres-w"></div></div></div>
      <p class="illus" id="cres-note"></p>
      <details style="margin-top:10px"><summary style="cursor:pointer;font-weight:600">Per class: typical size and the usual range</summary><div class="tablebox"><table class="pick5" id="cres-cls"></table></div></details></div></section>
  <details class="card"><summary><span class="tri">▶</span><h2>Sample crops</h2><span class="sum">The clearest crops of each class: sharp, straight-on, well lit, the usual shape, one per photo. Click a crop to open it.</span></summary>
    <div class="body"><div class="samples" id="r-samples"></div>
      <div class="callout" style="margin-top:14px"><span class="ic">!</span><p><b>Load Balancer is too small to learn.</b> One export, 429_v6, had its class numbers shifted by one, so most of its "Load Balancer" polygons were PDUs. After the fix the whole dataset holds 16 Load Balancer polygons: __LBTRAIN__ training crops, __LBVALID__ valid, __LBTEST__ test. The classifier is trained on the other __NCLS__ classes until more are labelled.</p></div></div></details>
</section>

<!-- ============ Edge cases ============ -->
<section class="panel" id="p-why" hidden>
  <div class="subtabs" role="tablist" aria-label="Which answers"></div>
  <div class="kpis" id="edge-kpis"></div>
  <div class="grid2">
    <section class="card"><header><h2 id="edge-why-t">Why they are still wrong</h2><p id="edge-why-s">Every wrong answer left after Other, sorted into one reason each, to scale.</p></header>
      <div class="body"><div class="rl" id="edge-why"></div></div></section>
    <section class="card"><header><h2 id="edge-st-t">Breaking it on purpose</h2><p id="edge-st-s"></p></header>
      <div class="body"><div class="rl" id="edge-stress"></div></div></section>
  </div>
  <section class="card"><header><h2 id="edge-racks-t">The racks and their mistakes</h2><p>Every rack photo that still has a wrong answer, most first: the wrong devices outlined in orange, what each one is and was called, and why. Blurry devices are shown and marked, because the blur is part of the reason. Click a photo to open it.</p></header>
    <div class="body" style="display:grid;gap:18px"><div class="grid2"><div><h3 class="h3s" id="edge-rg-t">Racks by number of mistakes</h3><div class="rl" id="edge-rgroups"></div></div><div><h3 class="h3s" id="edge-wr-t">Without the worst racks</h3><div class="rl" id="edge-wracks"></div></div></div><div class="rk" id="edge-racks"></div></div></section>
</section>

<!-- ============ Misclassified ============ -->
<section class="panel" id="p-mis" hidden>
  <div class="subtabs" role="tablist" aria-label="Which answers"></div>
  <div class="kpis" id="mis-kpis"></div>
  <section class="card"><header><h2 id="mis-list-t">The biggest mix-ups</h2><p>Pick one on the left. On the right: why it happens, and each mistaken device next to 3 similar devices of the type it was taken for.</p></header>
    <div class="body"><div class="mxg"><div class="mxl" id="mis-list" role="tablist"></div><div class="mxd" id="mis-detail"></div></div></div></section>
</section>

<!-- ============ Crop resolution ============ -->
<section class="panel" id="p-res" hidden>
  <div class="kpis k3" id="res-kpis"></div>
  <section class="card"><header><h2>How a crop goes into the box</h2><p>Made smaller or bigger until it just fits, shape kept, never stretched; grey fills the rest.</p></header>
    <div class="body"><div class="fit3" id="res-fit"></div></div></section>
  <div class="grid2">
    <section class="card"><header><h2>Smaller boxes score lower</h2><p id="res-small-sub"></p></header>
      <div class="body"><div class="rl" id="res-small"></div></div></section>
    <section class="card"><header><h2>Making crops smaller does not cause mistakes</h2><p id="res-lost-sub"></p></header>
      <div class="body"><div class="rl" id="res-lost"></div><p class="illus" id="res-lost-note"></p></div></section>
  </div>
  <section class="card"><header><h2>Bigger boxes</h2><p id="res-big-sub"></p></header>
    <div class="body"><div class="big3" id="res-big"></div></div></section>
  <details class="card"><summary><span class="tri">▶</span><h2>The numbers</h2><span class="sum">Counts per group, each class at full and half detail, and the size test.</span></summary>
    <div class="body"><div class="tablebox"><table class="pick5" id="res-shrink"></table></div><div class="tablebox" style="margin-top:14px"><table class="pick5" id="res-classes"></table></div><div class="tablebox" style="margin-top:14px"><table class="pick5" id="res-canvas"></table></div></div></details>
</section>

<!-- ============ Unknown answers ============ -->
<section class="panel" id="p-unk" hidden>
  <div class="subtabs" role="tablist" aria-label="Which answers"></div>
  <div class="kpis k3" id="unk-kpis"></div>
  <section class="card"><header><h2 id="unk-how-t">How the model decides to say Other</h2><p id="unk-how-s">For every crop it gives a score to each device type. When the top two are too close, it says Other instead of guessing.</p></header>
    <div class="body"><div class="howu" id="unk-how"></div><div id="unk-how-note"></div></div></section>
  <div class="grid2">
    <section class="card"><header><h2 id="unk-ba-t"></h2><p id="unk-ba-s"></p></header>
      <div class="body"><div class="rl" id="unk-ba"></div><p class="illus" id="unk-ba-note"></p></div></section>
    <section class="card"><header><h2 id="unk-cl-t"></h2><p>The five device types answered Other most often, to scale. All types are in The numbers.</p></header>
      <div class="body"><div class="rl" id="unk-classes"></div></div></section>
  </div>
  <details class="card"><summary><span class="tri">▶</span><h2>Examples</h2><span class="sum">Crops answered Other, with their real type and what was said about them.</span></summary>
    <div class="body"><div class="whygrid" id="unk-ex"></div></div></details>
  <details class="card"><summary><span class="tri">▶</span><h2>The numbers</h2><span class="sum">Per device type, which check fired, and what the model was torn between.</span></summary>
    <div class="body"><div class="tablebox"><table class="pick5" id="unk-table"></table></div><div class="grid2" style="margin-top:14px"><div><h3 class="h3s">The check that fired</h3><div class="ba" id="unk-reasons"></div></div><div><h3 class="h3s" id="unk-torn-h">Its top two answers</h3><div class="ba" id="unk-torn"></div><p class="illus" id="unk-torn-note"></p></div></div></div></details>
</section>

<!-- ============ Sample crops ============ -->


<!-- ============ Classifier ============ -->
<!-- ============ Approaches (all of them) ============ -->
<section class="panel" id="p-apr" hidden>
  <div class="kpis" id="apr-kpis"></div>
  <section class="card"><header><h2>The test data</h2><p id="apr-data-s"></p></header>
    <div class="body"><div class="chips" id="apr-data"></div></div></section>
  <section class="card"><header><h2 id="apr-1-t">1 · Answering every device</h2><p>Each model must name a type for every test crop. Bar: the 1,550 crops, to scale.</p></header>
    <div class="body"><div class="mc2" id="apr-1"></div></div></section>
  <section class="card"><header><h2 id="apr-2-t">2 · With Other</h2><p>Other means "not sure, check this one". It is neither right nor wrong, so "answers right" counts only the answers given. For every Other, we also show whether the answer would have been right or wrong.</p></header>
    <div class="body"><div class="legend" id="apr-2-lg"></div><div class="mc3" id="apr-2"></div></div></section>
  <section class="card"><header><h2>How Model 1 + Model 2 works</h2><p>For every device: both models name it, and the answer goes out only when the names match. Model 2 never overrules Model 1; it can only stop its answer.</p></header>
    <div class="body" style="display:grid;gap:16px"><div class="mm" id="apr-flow"></div><div class="mx3" id="apr-ex"></div><p class="rs" id="apr-how-n" style="margin:0"></p></div></section>
  <section class="card"><header><h2 id="apr-mis-t">What happens to the misclassified devices</h2><p>From the wrong answers of Model 1 alone to what is left with both models, and where each goes.</p></header>
    <div class="body" style="display:grid;gap:16px"><div class="mm mm3" id="apr-mis-flow"></div>
      <div><h3 class="h3s" id="apr-left-t"></h3><div class="mlg" id="apr-left"></div></div></div></section>
  <section class="card"><header><h2>Is this the right way?</h2><p>What makes the numbers above trustworthy, and what still has to be confirmed.</p></header>
    <div class="body"><div class="rl" id="apr-check"></div></div></section>
  <section class="card"><header><h2>Not tested yet</h2></header>
    <div class="body"><div class="rl" id="apr-next"></div></div></section>
</section>

<!-- ============ DINOv2-B detector (second model) ============ -->
<section class="panel" id="p-det" hidden>
  <div class="kpis" id="det-kpis"></div>
  <section class="card"><header><h2>What it is, and why a second model</h2><p>The team's device detector: it looks at the whole rack photo, finds every device and names its type. It is the second opinion for the classifier.</p></header>
    <div class="body"><div class="tablebox" id="det-why"></div></div></section>
  <section class="card"><header><h2>How it was trained</h2><p>Trained on another machine by the team with train_dinov2.py; the settings below are read from the checkpoint.</p></header>
    <div class="body"><div class="stps" id="det-steps"></div></div></section>
  <section class="card"><header><h2 id="det-sq-t">What Model 2 sees: the photo squashed to 518 × 518</h2><p id="det-sq-s"></p></header>
    <div class="body"><div class="sq4" id="det-sq"></div></div></section>
  <div class="grid2">
    <section class="card"><header><h2>Training: loss per round</h2><p id="det-curve-s"></p></header>
      <div class="body"><div class="chart" id="det-curve"></div></div></section>
    <section class="card"><header><h2>Test scores</h2><p id="det-test-s"></p></header>
      <div class="body"><div class="rl" id="det-test"></div></div></section>
  </div>
  <details class="card"><summary><span class="tri">▶</span><h2>The numbers</h2><span class="sum">Every device type on the test photos, and the full confusion table.</span></summary>
    <div class="body" style="display:grid;gap:18px"><div class="tablebox"><table class="pick5" id="det-classes"></table></div><div class="tablebox"><table class="pick5" id="det-cm"></table></div></div></details>
</section>

<section class="panel" id="p-clf" hidden>
  <div class="kpis" id="clf-kpis"></div>
  <section class="card"><header><h2>Which model, and why</h2><p>The two strongest of five image models we compared on published results. The first was trained.</p></header>
    <div class="body"><div class="tablebox"><table class="pick5">
      <thead><tr><th class="l">Model</th><th>Size</th><th class="l">Licence</th><th class="l">Verdict</th></tr></thead>
      <tbody>
        <tr class="chosen"><td>ConvNeXt-Tiny</td><td>28.6 M</td><td><span class="lic ok">Apache-2.0</span></td><td><b>Chosen.</b> Among the best in the largest published comparison of image models, small and fast to train.</td></tr>
        <tr><td>DINOv2-B</td><td>86.6 M</td><td><span class="lic ok">Apache-2.0</span></td><td>Second choice. Strongest when a class has few examples, but three times bigger and slower.</td></tr>
      </tbody></table></div>
      <p class="illus">Size is millions of parameters: bigger models can learn more but train and run slower.</p></div></section>
  <section class="card"><header><h2>How it was trained</h2><p>One script, six standard steps. Each card shows the step on a real training crop: what we do, and why.</p></header>
    <div class="body"><div class="stps" id="clf-steps"></div></div></section>
  <details class="card"><summary><span class="tri">▶</span><h2>Each step in more detail</h2><span class="sum">The same six steps with the exact settings and the reasons written out.</span></summary>
    <div class="body" style="display:grid;gap:16px">
      <div class="pipe"><span>Transfer learning</span><b>→</b><span>Resizing</span><b>→</b><span>Augmentation</span><b>→</b><span>Class weights</span><b>→</b><span class="hl">Training</span><b>→</b><span>Patience</span><b>→</b><span class="hl">Evaluation</span></div>
      <div class="tablebox"><table class="pick5 steps6">
        <thead><tr><th class="l">Technique</th><th class="l">How we do it</th><th class="l">Why</th></tr></thead>
        <tbody>
          <tr><td>Transfer learning<span>start from a trained model</span></td><td>Start from ConvNeXt-Tiny already trained on 14 million ImageNet photos, and replace only its last layer with a new 9-class one.</td><td>With about 10,000 crops a model learnt from scratch would not see enough examples. The pretrained one already knows edges, ports and textures; it only has to learn our 9 classes.</td></tr>
          <tr><td>Resizing<span>letterbox</span></td><td>Make each crop smaller or bigger, keeping its proportions, until it fits a 512 × 128 box; fill the rest with grey.</td><td>The model needs every input the same size. Devices are wide, so a wide box leaves less grey than a square and the device stays larger. It has about the pixels of the model's standard 224 × 224 input, so training stays fast.</td></tr>
          <tr><td>Augmentation<span>random changes</span></td><td>Every time a training crop is used: trim up to 8 % off each edge, flip it left to right half the time, change brightness, contrast and colour a little, and place it at a random size and spot in the box.</td><td>So the model learns what a device looks like, not one exact picture. The trims copy the loose boxes the detector gives at run time; the colour changes copy the different lighting in server rooms.</td></tr>
          <tr><td>Class weights<span>balanced sampling</span></td><td>Pick each training crop with a chance of 1 ÷ √(crops in its class), so rare classes come up more often.</td><td>__BIG__ has __RATIO__ times as many training crops as __SMALL__. Without this the model learns to answer the big classes. The square root is a middle path: full balancing would repeat the few rare crops so often that the model memorises them.</td></tr>
          <tr><td>Patience<span>early stopping</span></td><td>After each round, score the valid crops. Keep the weights of the best round; stop after 8 rounds with no improvement (at most 30 rounds).</td><td>Training too long makes the model memorise the training crops and get worse on new ones. This run peaked at round 13 and stopped at round 21.</td></tr>
          <tr><td>Evaluation<span>macro-F1, test once</span></td><td>Choose the best round by valid macro-F1. Score the separate test split once, with those weights.</td><td>Macro-F1 averages the score of every class, so rare classes count as much as big ones. The test split is never used to choose anything, so its score stays honest.</td></tr>
        </tbody></table></div></div></details>
  <div class="grid2">
    <section class="card"><header><h2>Training: score after each round</h2><p>Macro-F1 and accuracy on the __NVALID__ valid crops after each round. The ring marks the best round, whose weights are kept.</p></header>
      <div class="body"><div class="legend"><span><i style="background:var(--s1)"></i>macro-F1</span><span><i style="background:var(--s3)"></i>accuracy</span></div><div class="chart" id="c-run-curve"></div></div></section>
    <section class="card"><header><h2>Test scores</h2><p>On the __NTEST__ test crops, never seen in training. Bars to scale, 0 to 100 %.</p></header>
      <div class="body"><div class="rl" id="clf-test"></div></div></section>
  </div>
  <details class="card"><summary><span class="tri">▶</span><h2>The numbers</h2><span class="sum">The confusion matrix, every test metric explained, and each device type's scores.</span></summary>
    <div class="body" style="display:grid;gap:18px"><div><h3 class="h3s" id="run-cm-title">Confusion matrix</h3><p class="illus">Rows are the real type, columns the answer. Off-diagonal cells are mistakes.</p><div class="chart" id="c-run-cm"></div></div>
      <div class="tablebox"><table id="run-metrics"></table></div><div class="tablebox"><table id="run-scorecard"></table></div></div></details>
  <details class="card"><summary><span class="tri">▶</span><h2>The training script, piece by piece</h2><span class="sum">scripts/devices/train_device_cls.py, read from the file when this page is built. Each piece of code sits next to what it does.</span></summary>
    <div class="body" style="display:grid;gap:18px">
      <div class="codestep"><div><h3>1 · Pick the crops</h3><p>Reads the crop list and keeps the crops whose photo is 1 MP or more, for one split (train, valid or test).</p></div><pre class="code" data-code="data"></pre></div>
      <div class="codestep"><div><h3>2 · Resizing</h3><p>Fits a crop into the 512 × 128 box without changing its shape and fills the rest with grey. <code>scale</code>, <code>dx</code>, <code>dy</code> let augmentation move it around.</p></div><pre class="code" data-code="letterbox"></pre></div>
      <div class="codestep"><div><h3>3 · Augmentation</h3><p>Runs every time a crop is loaded. Training crops get the random trim, flip, colour change and placement; valid and test crops are only resized.</p></div><pre class="code" data-code="augment"></pre></div>
      <div class="codestep"><div><h3>4 · Class weights</h3><p>Counts the crops per class and gives every crop the weight 1 ÷ √(count). The sampler then draws batches of 64 by those weights.</p></div><pre class="code" data-code="balance"></pre></div>
      <div class="codestep"><div><h3>5 · Transfer learning and the optimiser</h3><p>Loads the pretrained model with a new 9-class head, keeps a smoothed copy of the weights, and sets up AdamW with a 2-round warm-up and a slowly falling learning rate.</p></div><pre class="code" data-code="build"></pre></div>
      <div class="codestep"><div><h3>6 · Training with patience</h3><p>One round over all training crops, then a score on valid. Saves the best weights, and stops after 8 rounds without a gain.</p></div><pre class="code" data-code="loop"></pre></div>
      <div class="codestep"><div><h3>7 · Evaluation</h3><p>Reloads the best weights, scores the test split once and writes every number to results.json.</p></div><pre class="code" data-code="select"></pre></div>
    </div></details>
</section>

<!-- ============ Other devices ============ -->
<section class="panel" id="p-oth" hidden>
  <p class="callout-lite">The results of the Other answer on our test crops are under <b>Results › Unknown</b>. This page explains the methods.</p>
  <section class="card"><header><h2>The problem in one picture</h2><p>Each dot is one crop placed by what the model sees. Crops of one class sit together.</p></header>
    <div class="body"><div class="grid2">
      <div><div class="chart" id="d-closed"></div><p class="illus"><b>A normal classifier</b> must pick one of its 9 classes. A new kind of gear, the orange star, is forced into the nearest class.</p></div>
      <div><div class="chart" id="d-open"></div><p class="illus"><b>An open-set classifier</b> also knows where each class ends. Anything outside every class becomes Other.</p></div>
    </div></div></section>
  <section class="card"><header><h2>Which approach to use</h2><p>A two-stage plan: what runs today without any Other examples, and what to add once Other examples are collected.</p></header>
    <div class="body" style="display:grid;gap:18px">
      <div class="stage2">
        <div class="stg now"><span class="when">Stage 1 · today</span><h3>Distance check + energy score + close call</h3><p class="what">Approaches 3, 2 and 1 together, on top of the trained classifier. No Other examples needed.</p>
          <dl class="kv"><dt>How</dt><dd>For each crop the classifier gives three numbers: its score strength (how strongly it recognises the crop; the paper calls it energy), its distance to the nearest training crops (how unlike them it is), and the gap between its top two answers. If the strength is too low, the distance too high or the gap too small, the answer is Other.</dd>
          <dt>Cut-offs</dt><dd>Set on the valid split. Strength and distance together flag about 5 % of real devices: they catch unfamiliar gear. The gap flags about 10 % more: the close calls, where a wrong answer is likely.</dd>
          <dt>Why</dt><dd>It works now, costs nothing to train, catches gear that looks like nothing seen before, and turns most likely mistakes into Other instead of a wrong answer.</dd>
          <dt>Limit</dt><dd>Gear that closely resembles a known class, such as a mini PC that looks like a server, can still pass as that class.</dd></dl></div>
        <div class="stg next"><span class="when">Stage 2 · after collecting Other examples</span><h3>Other class + distance check</h3><p class="what">Approach 4 trained in, with approach 3 kept on top.</p>
          <dl class="kv"><dt>How</dt><dd>Collect a few hundred crops of gear outside the 9 classes, label them Other, and retrain so the model has a 10th answer. Keep the distance check for kinds that are still new.</dd>
          <dt>Collect</dt><dd>Run the device detector on rack photos, keep the boxes that match no label, and let a person confirm them; add photos of cable managers, KVM switches, shelves and mini PCs.</dd>
          <dt>Why</dt><dd>The model then learns what Other gear looks like, which is what catches the look-alikes that stage 1 misses.</dd>
          <dt>Test</dt><dd>Keep some kinds of Other out of training and check they are still caught; report the 9-class macro-F1 and the share of Other caught.</dd></dl></div>
      </div>
      <p class="illus">Approach 5 is not in the plan: it needs the same Other examples as 4 but is harder to train. Approach 1 is used in its sharper form, the gap between the top two answers.</p>
    </div></section>
  <section class="card"><header><h2>The five approaches</h2><p>Choose an approach to see how it works, step by step, with an example.</p></header>
    <div class="body" style="display:grid;gap:16px">
      <div class="appills" role="tablist" id="appills"><button role="tab" data-ap="1" aria-selected="false"><span class="num">1</span>Confidence threshold<span class="pl pl-now">In use today</span></button><button role="tab" data-ap="2" aria-selected="false"><span class="num">2</span>Energy score<span class="pl pl-now">In use today</span></button><button role="tab" data-ap="3" aria-selected="true"><span class="num">3</span>Distance to known classes<span class="pl pl-now">In use today</span></button><button role="tab" data-ap="4" aria-selected="false"><span class="num">4</span>Explicit Other class<span class="pl pl-later">Later</span></button><button role="tab" data-ap="5" aria-selected="false"><span class="num">5</span>Outlier exposure<span class="pl pl-skip">Not used</span></button></div>
      <div id="ap-detail">
        <div class="apx" data-ap="1" hidden>
          <div class="apx-head"><span class="num">1</span><div><h3>Confidence threshold</h3><p class="idea">Answer Other when the model is not sure about any of its 9 classes.</p></div><span class="plan plan-now">Used today as the gap between the top two answers: it turns likely mistakes into Other.</span></div>
          <h4>How it works, step by step</h4>
          <div class="apx-flow"><div class="fs"><span class="fn">1</span>Crop of a device</div><span class="fa">→</span><div class="fs"><span class="fn">2</span>The model gives a score to each of its 9 classes</div><span class="fa">→</span><div class="fq"><span class="fn">3</span>Is the highest score below 50 %?</div><span class="fa">→</span><div class="fyn"><div class="fy"><b>Yes</b> → answer <b>Other</b></div><div class="fno"><b>No</b> → answer the class with the highest score</div></div></div>
          <div class="apx-two">
            <div class="apx-ex"><h4>Example</h4><div class="chart" id="d-msp"></div><p class="illus">How to read it: each group is one crop. The 9 bars show how sure the model is for each of its 9 classes; the first bar is the class it likes most. If even that bar is below the dashed 50 % line, the answer is Other.</p></div>
            <div class="apx-gb"><h4>Good at</h4><ul class="good"><li>Gear that looks like nothing we trained on, such as a fan tray: the model spreads its scores thin, so the top one stays low.</li></ul>
              <h4>Weak at</h4><ul class="bad"><li>Gear that looks like one of our classes, such as a KVM switch: the model is sure it is a Switch, so it passes.</li></ul>
              <h4>What it needs</h4><p>Nothing extra: one line of code on the classifier we already have.</p>
              <h4>How we use it</h4><p>Not the top score alone but the gap between the top two: "Switch 48 %, Router 41 %" is a close call even though 48 % looks fine next to the other seven. On our test crops the gap catches mistakes better than the top score, and as well as all checks combined.</p>
              <h4>Why it works</h4><p>A classifier spreads its belief over the classes it knows. When it has never seen something, that belief is often thin, so the top score is low. Studies show this check gets better as the classifier gets more accurate.</p></div>
          </div>
        </div>
        <div class="apx" data-ap="2" hidden>
          <div class="apx-head"><span class="num">2</span><div><h3>Energy score</h3><p class="idea">Like approach 1, but it looks at how strong the raw scores are, not at the percentages made from them.</p></div><span class="plan plan-now">Used today, together with approach 3.</span></div>
          <h4>How it works, step by step</h4>
          <div class="apx-flow"><div class="fs"><span class="fn">1</span>Crop of a device</div><span class="fa">→</span><div class="fs"><span class="fn">2</span>The model gives a raw score to each of its 9 classes</div><span class="fa">→</span><div class="fs"><span class="fn">3</span>Score strength: about the biggest raw score</div><span class="fa">→</span><div class="fq"><span class="fn">4</span>Is the strength below the cut-off?</div><span class="fa">→</span><div class="fyn"><div class="fy"><b>Yes</b> → answer <b>Other</b></div><div class="fno"><b>No</b> → answer the class with the highest score</div></div></div>
          <div class="apx-two">
            <div class="apx-ex"><h4>Example: two crops of the same type from the test set</h4><div id="d-energy"></div></div>
            <div class="apx-gb"><h4>Good at</h4><ul class="good"><li>Very different gear: all its raw scores are weak, so its strength is low.</li><li>It separates known from unknown gear more cleanly than approach 1.</li></ul>
              <h4>Weak at</h4><ul class="bad"><li>Look-alike gear still gets a strong score for the class it resembles.</li></ul>
              <h4>What it needs</h4><p>Nothing extra: one line of code. The cut-off is set so that about 5 % of real devices trip it.</p>
              <h4>Why it works</h4><p>Turning scores into percentages hides how strong they were: weak scores everywhere can still give one class a high percentage. The strength keeps that, so known and unknown gear overlap less (Liu et al., 2020).</p>
              <h4>Why the name "energy"</h4><p>The paper calls it energy and writes it with a minus sign: energy = − strength. High energy means weak scores. This report shows the strength, so bigger always means more familiar.</p></div>
          </div>
        </div>
        <div class="apx" data-ap="3">
          <div class="apx-head"><span class="num">3</span><div><h3>Distance to known classes</h3><p class="idea">Answer Other when a crop looks unlike every crop the model was trained on.</p></div><span class="plan plan-now">Used today, and kept in the later plan.</span></div>
          <h4>How it works, step by step</h4>
          <div class="apx-flow"><div class="fs"><span class="fn">1</span>Crop of a device</div><span class="fa">→</span><div class="fs"><span class="fn">2</span>The model turns it into a list of numbers (its features)</div><span class="fa">→</span><div class="fs"><span class="fn">3</span>Find the 5 most similar training crops</div><span class="fa">→</span><div class="fq"><span class="fn">4</span>Are they all far away?</div><span class="fa">→</span><div class="fyn"><div class="fy"><b>Yes</b> → answer <b>Other</b></div><div class="fno"><b>No</b> → answer the classifier's answer</div></div></div>
          <div class="apx-two">
            <div class="apx-ex"><h4>Example</h4><div class="chart" id="d-knn"></div><p class="illus">Illustration. Lines go to the nearest training crops.</p></div>
            <div class="apx-gb"><h4>Good at</h4><ul class="good"><li>Any gear unlike the training crops lands far away and is caught, even kinds nobody thought of.</li><li>New classes can be added later just by storing their crops.</li></ul>
              <h4>Weak at</h4><ul class="bad"><li>Look-alike gear sits close to its look-alike class, so only the slightly different ones are caught.</li></ul>
              <h4>What it needs</h4><p>The features of all 10,068 training crops, stored once, and a cut-off.</p>
              <h4>Why it works</h4><p>It asks "have I seen anything like this?" instead of "how sure am I?". That is a different question, so it catches things the confidence checks miss (Sun et al., 2022).</p></div>
          </div>
        </div>
        <div class="apx" data-ap="4" hidden>
          <div class="apx-head"><span class="num">4</span><div><h3>Explicit Other class</h3><p class="idea">Teach the model a 10th answer, Other, from real photos of other gear.</p></div><span class="plan plan-later">Later: once a few hundred photos of other gear are collected.</span></div>
          <h4>How it works, step by step</h4>
          <div class="apx-flow"><div class="fs"><span class="fn">1</span>Collect a few hundred crops of other gear</div><span class="fa">→</span><div class="fs"><span class="fn">2</span>Retrain the model with 10 answers: 9 classes + Other</div><span class="fa">→</span><div class="fs"><span class="fn">3</span>Crop of a device</div><span class="fa">→</span><div class="fs"><span class="fn">4</span>The model picks one of the 10 answers</div></div>
          <div class="apx-two">
            <div class="apx-ex"><h4>Example</h4><div class="chart" id="d-other"></div><p class="illus">Illustration. Grey dots are labelled Other crops.</p></div>
            <div class="apx-gb"><h4>Good at</h4><ul class="good"><li>Look-alike gear: the model has seen KVM switches, cable managers and shelves labelled Other, so it learns how they differ.</li></ul>
              <h4>Weak at</h4><ul class="bad"><li>A kind of gear that was not in the collected examples can still be taken for one of the 9 classes. That is why approach 3 stays on top.</li></ul>
              <h4>What it needs</h4><p>A few hundred labelled crops of other gear, of many kinds, and one retraining run.</p>
              <h4>Why it works</h4><p>The model learns what Other looks like directly, instead of guessing from low confidence. Benchmarks show real examples help most when later unknowns resemble them; ours come from the same racks, so they will.</p></div>
          </div>
        </div>
        <div class="apx" data-ap="5" hidden>
          <div class="apx-head"><span class="num">5</span><div><h3>Outlier exposure</h3><p class="idea">Train the model to be unsure when it sees other gear, then use the cut-off of approach 2.</p></div><span class="plan plan-skip">Not used: needs the same photos as approach 4 and is harder to train.</span></div>
          <h4>How it works, step by step</h4>
          <div class="apx-flow"><div class="fs"><span class="fn">1</span>Collect crops of other gear</div><span class="fa">→</span><div class="fs"><span class="fn">2</span>Train: on these crops, give every class the same low score</div><span class="fa">→</span><div class="fs"><span class="fn">3</span>Crop of a device</div><span class="fa">→</span><div class="fq"><span class="fn">4</span>Is the model unsure?</div><span class="fa">→</span><div class="fyn"><div class="fy"><b>Yes</b> → answer <b>Other</b></div><div class="fno"><b>No</b> → answer the class with the highest score</div></div></div>
          <div class="apx-two">
            <div class="apx-ex"><h4>Example</h4><div class="chart" id="d-oe"></div><p class="illus">How to read it: the same crop of other gear before and after outlier-exposure training. The 9 bars show how sure the model is for each class; below the dashed line the answer is Other.</p></div>
            <div class="apx-gb"><h4>Good at</h4><ul class="good"><li>Look-alike gear it was trained on: it learned to be unsure there.</li><li>Some of that unsureness carries over to gear it never saw.</li></ul>
              <h4>Weak at</h4><ul class="bad"><li>Needs special training code, and still needs a cut-off.</li></ul>
              <h4>What it needs</h4><p>Photos of other gear, as for approach 4, plus changes to the training code.</p>
              <h4>Why it works</h4><p>The model learns that only the 9 classes deserve a confident answer; everything else should get a flat, unsure one (Hendrycks et al., 2019).</p></div>
          </div>
        </div>
      </div>
      <h4 style="margin:6px 0 0">Side by side</h4>
      <div class="tablebox"><table class="pick5 sbs"><thead><tr><th class="l">Approach</th><th class="l">Needs photos of other gear</th><th class="l">Catches look-alike gear</th><th class="l">Catches very different gear</th><th class="l">Work</th><th class="l">In our plan</th></tr></thead><tbody><tr><td>1 Confidence threshold</td><td>No</td><td><span class="lic no">Poor</span></td><td><span class="lic warn">Fair</span></td><td>Very little</td><td>Not used</td></tr><tr><td>2 Energy score</td><td>No</td><td><span class="lic no">Poor</span></td><td><span class="lic ok">Good</span></td><td>Very little</td><td>Today</td></tr><tr><td>3 Distance to known classes</td><td>No</td><td><span class="lic warn">Fair</span></td><td><span class="lic ok">Good</span></td><td>Some</td><td>Today and later</td></tr><tr><td>4 Explicit Other class</td><td>Yes</td><td><span class="lic ok">Good</span></td><td><span class="lic no">Poor</span></td><td>Some</td><td>Later</td></tr><tr><td>5 Outlier exposure</td><td>Yes</td><td><span class="lic ok">Good</span></td><td><span class="lic warn">Fair</span></td><td>A lot</td><td>Not used</td></tr></tbody></table></div>
    </div></section>







</section>





</main>
</div>
<div id="tip" hidden></div>
<div id="lb" hidden role="dialog" aria-modal="true" aria-label="Crop"><figure><img id="lb-img" alt=""><figcaption id="lb-cap"></figcaption>
  <div class="bar"><button id="lb-prev">Previous</button><button id="lb-next">Next</button><button id="lb-close">Close</button></div></figure></div>

<script>
const DS = __DS__;
const byId = id => document.getElementById(id) || document.createElement('div');
const REV = __REV__;
const RES = __RES__;
const CODE = __CODE__;
const RUNS = __RUNS__;
const TRV = __TRV__;
const DET = __DET__;
const APR = __APR__;
const HAND = __HAND__;
const WHYS = __WHY__, WHY = WHYS.m1;
const ENX = __ENX__;
const RESO = __RESO__;
const UNKS = __UNK__, UNK = UNKS.m1;
const EDGES = __EDGE__, EDGE = EDGES.m1;
let D, C, byCount, big, small;
const NS = 'http://www.w3.org/2000/svg';
const fmt = n => n.toLocaleString('en-US');
const pct = (a, b) => (100 * a / b);
const tip = byId('tip');
document.addEventListener('mousemove', e => {
  const t = e.target.closest && e.target.closest('[data-tip]');
  if (!t) { tip.hidden = true; return; }
  tip.innerHTML = t.getAttribute('data-tip'); tip.hidden = false;
  const r = tip.getBoundingClientRect();
  let x = e.clientX + 14, y = e.clientY + 14;
  if (x + r.width > innerWidth - 8) x = e.clientX - r.width - 14;
  if (y + r.height > innerHeight - 8) y = e.clientY - r.height - 14;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
});
function el(tag, attrs, parent, text) {
  const n = document.createElementNS(NS, tag);
  for (const k in attrs) n.setAttribute(k, attrs[k]);
  if (text != null) n.textContent = text;
  if (parent) parent.appendChild(n);
  return n;
}
function svgIn(id, h) {
  const box = byId(id); box.innerHTML = '';
  const w = Math.max(320, box.clientWidth);
  const s = el('svg', {width: w, height: h, viewBox: `0 0 ${w} ${h}`, role: 'img'}, box);
  return [s, w];
}
const nice = m => { const p = Math.pow(10, Math.floor(Math.log10(m))); for (const k of [1, 2, 2.5, 5, 10]) if (k * p >= m) return k * p; };
function axis(v) { const st = nice(v / 4), max = Math.ceil(v / st) * st; return {max, ticks: ticks(max, Math.round(max / st))}; }
function ticks(max, n) { const st = nice(max / n); const out = []; for (let v = 0; v <= max + 1e-9; v += st) out.push(+v.toFixed(6)); return out; }

function drawSummary() {
  const sh = (v, tot, mx, col) => `<div class="share"><b>${pct(v, tot).toFixed(1)} %</b><span class="trk"><i style="width:${(100 * v / mx).toFixed(1)}%;background:${col}"></i></span></div>`;
  const mi = big.count, mp = Math.max(...C.map(c => c.photos));
  const si = D.split_inst, sp = D.split_photos;
  byId('sum').innerHTML =
    '<thead><tr><th></th><th colspan="5" class="gs">Instances</th><th colspan="5" class="gs">Images</th></tr>' +
    '<tr><th>Class</th><th class="gs">Train</th><th>Valid</th><th>Test</th><th>Total</th><th>Share</th><th class="gs">Train</th><th>Valid</th><th>Test</th><th>Total</th><th>Share</th></tr></thead><tbody>' +
    byCount.map(c => `<tr><td>${c.name}</td><td class="gs">${fmt(c.train)}</td><td>${fmt(c.valid)}</td><td>${fmt(c.test)}</td><td><b>${fmt(c.count)}</b></td><td>${sh(c.count, D.total, mi, 'var(--s1)')}</td>` +
      `<td class="gs">${fmt(c.ptrain)}</td><td>${fmt(c.pvalid)}</td><td>${fmt(c.ptest)}</td><td><b>${fmt(c.photos)}</b></td><td>${sh(c.photos, D.photos, mp, 'var(--s3)')}</td></tr>`).join('') +
    `<tr class="tot"><td>All</td><td class="gs">${fmt(si.train)}</td><td>${fmt(si.valid)}</td><td>${fmt(si.test)}</td><td>${fmt(D.total)}</td><td style="text-align:right">100 %</td>` +
    `<td class="gs">${fmt(sp.train)}</td><td>${fmt(sp.valid)}</td><td>${fmt(sp.test)}</td><td>${fmt(D.photos)}</td><td></td></tr></tbody>`;
}

function xLabel(s, x, y, name, bw) {
  const fs = bw < 52 ? 10 : bw < 62 ? 10.5 : 11.5;
  const parts = name.includes(' ') ? name.split(' ') : [name];
  parts.forEach((t, k) => el('text', {x, y: y + k * 13, 'text-anchor': 'middle', 'font-size': fs, fill: 'var(--ink2)'}, s, t));
}
function vbars(id, rows, opt) {
  const top = 22, bottom = 40, left = 44, right = 8, h = 330;
  const [s, w] = svgIn(id, h);
  const pw = w - left - right, ph = h - top - bottom, max = opt.max;
  const y = v => top + ph - ph * v / max, bw = pw / rows.length, cw = Math.min(46, bw * 0.62);
  for (const t of opt.ticks) {
    el('line', {x1: left, x2: w - right, y1: y(t), y2: y(t), stroke: 'var(--grid)', 'stroke-width': 1}, s);
    el('text', {x: left - 6, y: y(t) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--muted)'}, s, opt.tick(t));
  }
  rows.forEach((r, i) => {
    const cx = left + i * bw + bw / 2, x0 = cx - cw / 2;
    let acc = 0;
    r.parts.forEach((p, j) => {
      const last = j === r.parts.length - 1, y1 = y(acc + p.v), y0 = y(acc) - (j ? 2 : 0), hh = y0 - y1;
      if (hh > 0.5) {
        const rr = last ? Math.min(3, hh) : 0;
        el('path', {d: `M${x0},${y0} V${y1 + rr} q0,-${rr} ${rr},-${rr} H${x0 + cw - rr} q${rr},0 ${rr},${rr} V${y0} Z`, fill: p.c}, s);
      }
      acc += p.v;
    });
    el('text', {x: cx, y: y(acc) - 6, 'text-anchor': 'middle', 'font-size': 11.5, fill: 'var(--ink)', 'font-variant-numeric': 'tabular-nums'}, s, r.end);
    xLabel(s, cx, top + ph + 15, r.label, bw);
    el('rect', {x: left + i * bw, y: top, width: bw, height: ph, fill: 'transparent', 'data-tip': r.tip}, s);
  });
  el('line', {x1: left, x2: w - right, y1: y(0), y2: y(0), stroke: 'var(--base)', 'stroke-width': 1}, s);
}
function drawCount() {
  vbars('c-count', byCount.map(c => ({label: c.name, end: fmt(c.count),
    parts: [{v: c.train, c: 'var(--s1)'}, {v: c.valid, c: 'var(--s2)'}, {v: c.test, c: 'var(--s3)'}],
    tip: `<b>${c.name}</b><br>${fmt(c.count)} instances (${pct(c.count, D.total).toFixed(1)} %)<br>train ${fmt(c.train)} · valid ${fmt(c.valid)} · test ${fmt(c.test)}`})),
    {...axis(big.count), tick: t => t >= 1000 ? (t / 1000) + 'k' : t});
}
function drawPhotos() {
  const rows = [...C].sort((a, b) => b.photos - a.photos);
  const mx = rows[0].photos;
  vbars('c-photos', rows.map(c => ({label: c.name, end: fmt(c.photos),
    parts: [{v: c.ptrain, c: 'var(--s1)'}, {v: c.pvalid, c: 'var(--s2)'}, {v: c.ptest, c: 'var(--s3)'}],
    tip: `<b>${c.name}</b><br>${fmt(c.photos)} of ${fmt(D.photos)} images (${pct(c.photos, D.photos).toFixed(1)} %)<br>train ${fmt(c.ptrain)} · valid ${fmt(c.pvalid)} · test ${fmt(c.ptest)}<br>${(c.count / c.photos).toFixed(1)} instances per image that has it`})),
    {...axis(mx), tick: t => t >= 1000 ? (t / 1000) + 'k' : t});
}
function columns(id, data, opt) {
  const top = 20, bottom = 40, left = 44, right = 8, h = 230;
  const [s, w] = svgIn(id, h);
  const max = nice(Math.max(...data.map(d => d.v))), pw = w - left - right, ph = h - top - bottom;
  const y = v => top + ph - ph * v / max, bw = pw / data.length;
  for (const t of ticks(max, 4)) {
    el('line', {x1: left, x2: w - right, y1: y(t), y2: y(t), stroke: 'var(--grid)', 'stroke-width': 1}, s);
    el('text', {x: left - 6, y: y(t) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--muted)'}, s, fmt(t));
  }
  data.forEach((d, i) => {
    const bx = left + i * bw + Math.min(2, bw * .15), ww = bw - 2 * Math.min(2, bw * .15);
    const hh = ph * d.v / max;
    if (hh > 0) el('path', {d: `M${bx},${y(0)} V${y(d.v) + Math.min(3, hh)} q0,-3 3,-3 H${bx + ww - 3} q3,0 3,3 V${y(0)} Z`, fill: d.c || 'var(--s1)'}, s);
    el('rect', {x: left + i * bw, y: top, width: bw, height: ph, fill: 'transparent', 'data-tip': d.tip}, s);
    if (d.label != null) el('text', {x: left + i * bw + bw / 2, y: top + ph + 15, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--muted)'}, s, d.label);
  });
  el('line', {x1: left, x2: w - right, y1: y(0), y2: y(0), stroke: 'var(--base)', 'stroke-width': 1}, s);
  el('text', {x: left + pw / 2, y: h - 6, 'text-anchor': 'middle', 'font-size': 11.5, fill: 'var(--ink2)'}, s, opt.xlabel);
  if (opt.after) opt.after(s, {left, bw, top, ph, y, w});
}
function drawCropRes() {
  const B = D.crop_bands; if (!B) return;
  const lab = (lo, hi) => hi >= 1e8 ? `${fmt(lo)} px or more` : lo === 0 ? `under ${fmt(hi)} px` : `${fmt(lo)} to ${fmt(hi)} px`;
  const rows = (list, mark) => { const mx = Math.max(...list.map(x => x[2])); return list.map(([lo, hi, c]) => `<div class="rr" style="--c1:150px"><div class="lb"><b>${lab(lo, hi)}</b><span>${pc(c / B.n)} of crops${lo <= mark && mark < hi ? ' · the model box' : ''}</span></div><div class="tk"><div style="width:${100 * c / mx}%;background:${lo <= mark && mark < hi ? 'var(--s1)' : 'var(--base)'}"></div></div><span class="v">${fmt(c)}</span></div>`).join(''); };
  byId('cres-t').textContent = `Crop resolution: typically ${fmt(Math.round(B.w_med))} × ${fmt(Math.round(B.h_med))} px`;
  byId('cres-s').textContent = `The size in pixels of each of the ${fmt(B.n)} crops, as cut from its photo. Bars to scale.`;
  byId('cres-h').innerHTML = rows(B.h, 128); byId('cres-w').innerHTML = rows(B.w, 512);
  byId('cres-note').textContent = `Model 1 fits every crop into a 512 × 128 box, keeping its shape: ${fmt(B.under_box)} crops (${pc(B.under_box / B.n)}) are smaller than the box both ways and are made bigger; the rest are made smaller. Crops are wide and short: a typical device is about ${(B.w_med / B.h_med).toFixed(1)} times wider than tall.`;
  byId('cres-cls').innerHTML = '<thead><tr><th class="l">Class</th><th>Crops</th><th>Typical size</th><th>Width, usual range</th><th>Height, usual range</th></tr></thead><tbody>' +
    byCount.map(c => `<tr><td>${c.name}</td><td>${fmt(c.count)}</td><td>${fmt(Math.round(c.w_med))} × ${fmt(Math.round(c.h_med))} px</td><td>${fmt(Math.round(c.w_lo))} to ${fmt(Math.round(c.w_hi))} px</td><td>${fmt(Math.round(c.h_lo))} to ${fmt(Math.round(c.h_hi))} px</td></tr>`).join('') +
    '</tbody><tfoot><tr><td colspan="5" style="color:var(--muted);font-weight:400">Usual range: 8 in 10 crops of the class fall inside it (10th to 90th percentile).</td></tr></tfoot>';
}
function drawAll() { drawCount(); drawFlow(); drawPhotoSizes(); drawCropSizes(); drawCropRes(); }
function drawFlow() {
  const st = (n, t, hl) => `<div class="st${hl ? ' hl' : ''}"><b>${n}</b><span>${t}</span></div>`, ar = '<span class="ar">→</span>';
  byId('dist-flow').innerHTML = [st('__NPHOTOS__', 'photos from 7 exports'), st(fmt(DS.all.total), 'device crops', true), st(fmt(DS.hi.photos), 'photos of 1 MP or more'), st(fmt(DS.hi.total), 'sharp crops used to train', true)].join(ar);
}
function drawPhotoSizes() {
  const m = D.mp, tot = m.reduce((a, b) => a + b, 0) || 1;
  const B = [['under 0.5 MP', m[0] + m[1], 'var(--grid)'], ['0.5 to 1 MP', m[2], 'var(--base)'], ['1 to 4 MP', m[3] + m[4], 'color-mix(in srgb, var(--s1) 55%, var(--card))'], ['4 MP and more', m[5] + m[6] + m[7], 'var(--s1)']];
  byId('c-photo').innerHTML = `<div class="big1"><b>${fmt(D.mp_ge1)}</b> of ${fmt(D.photos)} photos with a device are 1 MP or more (${Math.round(100 * D.mp_ge1 / D.photos)} %)</div>` +
    `<div class="szbar">${B.map(([n, v, c]) => v ? `<i style="width:${100 * v / tot}%;background:${c}" data-tip="<b>${n}</b><br>${fmt(v)} photos"></i>` : '').join('')}</div>` +
    `<div class="szleg">${B.map(([n, v, c]) => `<div><i style="background:${c}"></i>${n}<b>${fmt(v)}</b></div>`).join('')}</div>` +
    `<p class="illus" style="margin-top:12px">The model trains on the photos of 1 MP or more: in smaller ones a device is only a few pixels tall.</p>`;
}
function drawCropSizes() {
  const rows = REVROWS(), mw = Math.max(...rows.map(c => c.w[3])), k = 150 / mw;
  byId('c-cropsz').innerHTML = `<div class="csz">` + rows.map(c => `<span>${c.name}</span><span><span class="bx" style="display:block;width:${Math.max(6, c.w[3] * k)}px;height:${Math.max(4, c.h[3] * k)}px" data-tip="<b>${c.name}</b><br>typical crop ${fmt(c.w[3])} × ${fmt(c.h[3])} px<br>half of the crops are between ${fmt(c.w[2])} and ${fmt(c.w[4])} px wide"></span></span><span class="px">${fmt(c.w[3])} × ${fmt(c.h[3])}</span>`).join('') + `</div>` +
    `<p class="illus" style="margin-top:12px">Typical means the middle crop: half are bigger, half smaller. Width × height in pixels.</p>`;
}

function drawTable() {
byId('tbl').innerHTML =
  '<thead><tr><th>Class</th><th class="l">Folder</th><th>Median size (W × H)</th></tr></thead><tbody>' +
  byCount.map(c => `<tr><td>${c.name}</td><td class="mono">device_crops/${c.name.replace(/ /g, '_')}/</td><td>${Math.round(c.w_med)} × ${Math.round(c.h_med)}</td></tr>`).join('') + '</tbody>';
}

function drawSamples() {
byId('samples').innerHTML = byCount.map(c =>
  `<div class="srow"><div class="nm">${c.name}<span>${fmt(c.count)} instances</span></div><div class="thumbs">` +
  D.thumbs[c.name].map(([b], j) => `<button data-c="${c.name}" data-j="${j}" aria-label="Open ${c.name} crop ${j + 1}"><img alt="${c.name} crop" src="data:image/jpeg;base64,${b}"></button>`).join('') + '</div></div>').join('');
}

/* ---------- small helpers ---------- */
function rng(seed) { return () => { seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function gauss(r) { let u = 0, v = 0; while (!u) u = r(); while (!v) v = r(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
const dots = n => '<span class="dots" aria-label="' + n + ' of 3">' + [1, 2, 3].map(i => `<i class="${i <= n ? 'on' : ''}"></i>`).join('') + '</span>';
function star(s, x, y, r, fill) {
  const p = []; for (let i = 0; i < 10; i++) { const a = -Math.PI / 2 + i * Math.PI / 5, rr = i % 2 ? r * .45 : r; p.push((x + rr * Math.cos(a)).toFixed(1) + ',' + (y + rr * Math.sin(a)).toFixed(1)); }
  return el('polygon', {points: p.join(' '), fill, stroke: 'var(--card)', 'stroke-width': 1.5}, s);
}
function label(s, x, y, t, o = {}) { return el('text', Object.assign({x, y, 'font-size': 11.5, fill: 'var(--ink2)', 'text-anchor': 'middle'}, o), s, t); }

/* ---------- feature-space diagrams ---------- */
const CENT = [['Switch', .14, .2], ['Patch Panel', .38, .14], ['Server', .63, .17], ['PDU', .87, .26], ['UPS', .86, .8], ['Storage unit', .6, .84], ['Router', .36, .84], ['Gateway', .13, .74], ['Firewall', .22, .47]];
function space(id, mode) {
  const h = 280; const [s, w] = svgIn(id, h);
  const px = 24, py = 26, X = x => px + x * (w - 2 * px), Y = y => py + y * (h - 2 * py);
  const r = rng(11), pts = [];
  const rx = .065 * (w - 2 * px), ry = .085 * (h - 2 * py);
  CENT.forEach(([n, cx, cy]) => {
    if (mode === 'open' || mode === 'other') el('ellipse', {cx: X(cx), cy: Y(cy), rx: rx * 1.35, ry: ry * 1.35, fill: 'var(--accent-soft)', stroke: 'var(--accent)', 'stroke-opacity': .45, 'stroke-dasharray': '4 3'}, s);
    for (let i = 0; i < 12; i++) { const x = X(cx) + gauss(r) * rx * .45, y = Y(cy) + gauss(r) * ry * .45; pts.push([x, y]); el('circle', {cx: x, cy: y, r: 3, fill: 'var(--s1)', opacity: .75}, s); }
    label(s, X(cx), cy < .45 ? Y(cy) - ry * 1.35 - 5 : Y(cy) + ry * 1.35 + 13, n);
  });
  const sx = X(.5), sy = Y(.5);
  if (mode === 'closed') {
    const fx = X(.22), fy = Y(.47);
    el('line', {x1: sx - 10, y1: sy, x2: fx + rx * .9, y2: fy + 2, stroke: 'var(--s2)', 'stroke-width': 2, 'stroke-dasharray': '5 4'}, s);
    el('polygon', {points: `${fx + rx * .9},${fy + 2} ${fx + rx * .9 + 9},${fy - 3} ${fx + rx * .9 + 9},${fy + 7}`, fill: 'var(--s2)'}, s);
    star(s, sx, sy, 11, 'var(--s2)');
    label(s, sx, sy + 26, 'forced into Firewall', {fill: 'var(--ink)', 'font-weight': 600});
  } else if (mode === 'open') {
    star(s, sx, sy, 11, 'var(--s2)');
    label(s, sx, sy + 26, 'outside every class → Other', {fill: 'var(--ink)', 'font-weight': 600});
  } else if (mode === 'other') {
    const ox = X(.5), oy = Y(.42);
    el('ellipse', {cx: ox, cy: oy, rx: rx * 1.5, ry: ry * 1.3, fill: 'none', stroke: 'var(--muted)', 'stroke-dasharray': '4 3'}, s);
    for (let i = 0; i < 16; i++) el('circle', {cx: ox + gauss(r) * rx * .5, cy: oy + gauss(r) * ry * .42, r: 3, fill: 'var(--muted)'}, s);
    label(s, ox, oy - ry * 1.3 - 5, 'Other: KVM, shelf, fan tray', {fill: 'var(--ink)', 'font-weight': 600});
    const nx = X(.38), ny = Y(.62);
    star(s, nx, ny, 10, 'var(--s2)');
    label(s, nx, ny + 24, 'new kind, never seen', {fill: 'var(--ink)'});
  } else if (mode === 'knn') {
    const near = [X(.63) + 14, Y(.17) + 22], far = [X(.5), Y(.52)];
    const nn = p => pts.map(q => [q, Math.hypot(q[0] - p[0], q[1] - p[1])]).sort((a, b) => a[1] - b[1]).slice(0, 3);
    for (const [p, c, t] of [[near, 'var(--s3)', 'close: known'], [far, 'var(--s2)', 'far from all: Other']]) {
      nn(p).forEach(([q]) => el('line', {x1: p[0], y1: p[1], x2: q[0], y2: q[1], stroke: c, 'stroke-width': 1.6}, s));
      star(s, p[0], p[1], 9, c);
      label(s, p[0], p[1] + 24, t, {fill: 'var(--ink)', 'font-weight': 600});
    }
  }
}

/* ---------- probability bars (approaches 1 and 5) ---------- */
function probBars(id, groups, cut) {
  const h = 262; const [s, w] = svgIn(id, h);
  const top = 34, ph = 120, gap = 22, gw = (w - gap * (groups.length - 1)) / groups.length, y = v => top + ph - ph * v;
  groups.forEach((g, gi) => {
    const x0 = gi * (gw + gap), bw = gw / g.vals.length;
    label(s, x0 + gw / 2, 14, g.title, {fill: 'var(--ink)', 'font-weight': 600});
    el('line', {x1: x0, x2: x0 + gw, y1: y(0), y2: y(0), stroke: 'var(--base)'}, s);
    g.vals.forEach((v, i) => el('rect', {x: x0 + i * bw + 1.5, y: y(v), width: bw - 3, height: Math.max(1, ph * v), rx: 2, fill: i === 0 ? (g.c || 'var(--s1)') : 'var(--base)'}, s));
    if (cut != null) {
      el('line', {x1: x0, x2: x0 + gw, y1: y(cut), y2: y(cut), stroke: 'var(--ink2)', 'stroke-dasharray': '4 3'}, s);
      if (gi === 0) label(s, x0 + gw, y(cut) - 4, `cut-off ${Math.round(cut * 100)} %`, {'text-anchor': 'end', 'font-size': 10.5, fill: 'var(--muted)'});
    }
    label(s, x0 + gw / 2, top + ph + 18, `most sure: ${Math.round(g.vals[0] * 100)} %` + (cut != null ? (g.vals[0] >= cut ? ' (above the line)' : ' (below the line)') : ''), {fill: 'var(--muted)', 'font-size': 11});
    label(s, x0 + gw / 2, top + ph + 40, g.verdict, {fill: g.bad ? '#d03b3b' : 'var(--ink)', 'font-weight': 600, 'font-size': 12.5});
    if (g.note) label(s, x0 + gw / 2, top + ph + 58, g.note, {fill: g.bad ? '#d03b3b' : 'var(--ink2)', 'font-size': 11});
  });
}
function drawMSP() {
  probBars('d-msp', [
    {title: 'A real switch', vals: [.86, .03, .02, .02, .02, .01, .01, .01, .01], verdict: '✓ answers Switch', note: 'right'},
    {title: 'A fan tray (not one of our 9)', vals: [.22, .14, .12, .1, .1, .09, .08, .06, .05], verdict: '✓ answers Other', note: 'right: unsure about every class', c: 'var(--s2)'},
    {title: 'A KVM switch (not one of our 9)', vals: [.78, .08, .04, .03, .02, .02, .01, .01, .01], verdict: '✕ answers Switch', note: 'wrong: it looks like a switch', c: 'var(--s2)', bad: true}], .5);
}
function drawOE() {
  probBars('d-oe', [
    {title: 'Other gear, before', vals: [.71, .09, .05, .04, .03, .03, .02, .01, .01], verdict: '✕ answers Switch', note: 'wrong: too sure', c: 'var(--s2)', bad: true},
    {title: 'Same gear, after this training', vals: [.13, .12, .11, .11, .11, .11, .11, .1, .1], verdict: '✓ answers Other', note: 'right: now unsure about every class', c: 'var(--s2)'}], .5);
}

/* ---------- energy: two real crops ---------- */
function drawEnergy() {
  if (!ENX) return;
  const [a, b] = ENX.pair, top = Math.max(...ENX.pair.map(q => q.scores[0][1])), mx = 5;
  const card = q => {
    const ok = q.strength >= ENX.cut, others = q.scores.slice(3);
    return `<div class="enc"><img src="data:image/jpeg;base64,${q.img}" alt=""><span>Labelled <b>${q.label}</b></span>
      <span class="st">1 · Raw scores</span>
      ${q.scores.slice(0, 3).map(([c, v]) => `<div class="sr"><span>${c}</span><div><div class="b" style="width:${Math.max(0, 100 * v / top)}%"></div></div><span class="n">${v.toFixed(1)}</span></div>`).join('')}
      <div class="sr"><span style="color:var(--muted)">${others.length} others</span><span style="color:var(--muted)">all below ${Math.max(...others.map(t => t[1])).toFixed(1)}</span><span></span></div>
      <span class="st">2 · As a percentage</span><span><span class="big">${Math.round(q.prob * 100)} %</span> ${q.pred}</span>
      <span class="st">3 · Score strength</span><span class="big">${q.strength.toFixed(2)}</span>
      <div class="meter"><div class="f" style="width:${100 * Math.min(q.strength, mx) / mx}%;background:${ok ? '#0ca30c' : 'var(--s2)'}"></div><div class="c" style="left:${100 * ENX.cut / mx}%"></div></div>
      <div class="mk"><span>0</span><span>cut-off ${ENX.cut.toFixed(2)}</span><span>${mx}</span></div>
      <div class="verdict ${ok ? 'ok' : 'oth'}">${ok ? `Above the cut-off → answer <b>${q.pred}</b>. ${q.pred === q.label ? 'Right.' : 'Wrong.'}` : `Below the cut-off → answer <b>Other</b>. The model's guess, ${q.pred}, was wrong, so this catches a mistake.`}</div></div>`;
  };
  byId('d-energy').innerHTML = `<div class="enx">${card(a)}${card(b)}</div>
    <p class="enxnote">The model is ${Math.round(a.prob * 100)} % and ${Math.round(b.prob * 100)} % sure, so approach 1 would treat both crops the same. The raw scores tell them apart: the first crop's top score is ${a.scores[0][1].toFixed(1)}, the second only ${b.scores[0][1].toFixed(1)}. The strength is about the top score plus a little for the runners-up. Strengths below ${ENX.cut.toFixed(2)} become Other; that cut-off flags about 5 % of the real devices in the valid split.</p>`;
}

/* ---------- scorecards ---------- */
let AP = '3';
function pickAP(n) {
  AP = n; document.querySelectorAll('.apx').forEach(d => d.hidden = d.dataset.ap !== n);
  document.querySelectorAll('#appills button').forEach(b => b.setAttribute('aria-selected', b.dataset.ap === n));
  drawOther();
}
function drawScorecards() {
  const M = [['ConvNeXt-Tiny', 2, 2, 3, 'ok', 'Apache-2.0', 2], ['DINOv2-B', 3, 3, 1, 'ok', 'Apache-2.0', 3], ['EfficientNetV2-S', 2, 2, 3, 'ok', 'Apache-2.0', 2], ['ConvNeXt-Small', 3, 2, 2, 'ok', 'Apache-2.0', 2], ['YOLO11m-cls', 1, 2, 3, 'warn', 'AGPL-3.0', 1]];
  byId('scorecard').innerHTML = '<thead><tr><th>Model</th><th>Accuracy</th><th>Few labels</th><th>Speed</th><th>Helps Other</th><th>Licence</th></tr></thead><tbody>' +
    M.map(([n, a, f, sp, lc, lt, o]) => `<tr><td>${n}</td><td>${dots(a)}</td><td>${dots(f)}</td><td>${dots(sp)}</td><td>${dots(o)}</td><td><span class="lic ${lc}">${lt}</span></td></tr>`).join('') + '</tbody>';

}

/* ---------- classifier and evidence charts ---------- */
function drawClf() {
  const sz = [['ConvNeXt-Tiny', 28.6], ['DINOv2-B', 86.6], ['EfficientNetV2-S', 21.5], ['ConvNeXt-Small', 50.2], ['YOLO11m-cls', 10.4]];
  vbars('c-size', sz.map(([n, v], i) => ({label: n, end: v + ' M', parts: [{v, c: i === 0 ? 'var(--s1)' : 'var(--base)'}], tip: `<b>${n}</b><br>${v} M parameters`})), {...axis(86.6), tick: t => t + ' M'});
  const sm = [['DINOv2-B', 91.66], ['EVA-02-B', 91.07], ['ConvNeXt V2-B', 88.94]];
  vbars('c-small', sm.map(([n, v], i) => ({label: n, end: v.toFixed(1) + ' %', parts: [{v, c: i === 0 ? 'var(--s1)' : 'var(--base)'}], tip: `<b>${n}</b><br>${v} % mean accuracy`})), {max: 100, ticks: [0, 25, 50, 75, 100], tick: t => t + ' %'});
}
function drawOthBA(U) {
  const b = U.tb, a = U.ta, A = t => t.right / Math.max(1, t.right + t.wrong);
  byId('unk-kpis').innerHTML = [
    ['Wrong answers', `${b.wrong} → ${a.wrong}`, `of ${fmt(a.n)} test crops`],
    ['Answers that are right', `${pc(A(b))} → ${pc(A(a))}`, 'Other does not count as an answer'],
    ['Answered Other', fmt(a.other), `${Math.round(100 * a.other / a.n)} in 100 crops`]
  ].map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('');
  byId('unk-ba-t').textContent = `Wrong answers fall from ${b.wrong} to ${a.wrong}`;
  byId('unk-ba-s').textContent = 'Share of its answers that are right, and what Other catches and costs.';
  byId('unk-ba').innerHTML = [['Before', 'always answers', b], ['With Other', 'says Other when unsure', a]].map(([n, d, t]) =>
    `<div class="rr" style="--c1:170px"><div class="lb"><b>${n}</b><span>${d} · ${t.wrong} wrong${t.other ? `, ${t.other} Other` : ''}</span></div><div class="tk"><div style="width:${100 * A(t)}%;background:var(--s1)"></div></div><span class="v">${pc(A(t))}</span></div>`).join('') +
    `<div class="rr" style="--c1:170px"><div class="lb"><b>Mistakes caught</b><span>${a.caught} of ${b.wrong} became Other</span></div><div class="tk"><div style="width:${100 * a.caught / b.wrong}%;background:#3f9b57"></div></div><span class="v">${pc(a.caught / b.wrong)}</span></div>` +
    `<div class="rr" style="--c1:170px"><div class="lb"><b>Right answers given up</b><span>${a.lost} of ${fmt(b.right)} became Other</span></div><div class="tk"><div style="width:${100 * a.lost / b.right}%;background:#e0a03a"></div></div><span class="v">${pc(a.lost / b.right)}</span></div>`;
  byId('unk-ba-note').textContent = 'Bars to scale. Other does not count as an answer, so it is neither right nor wrong.';
}

function drawOther() { space('d-closed', 'closed'); space('d-open', 'open'); drawMSP(); drawEnergy(); space('d-knn', 'knn'); space('d-other', 'other'); drawOE(); }

/* ---------- review tab ---------- */
const REVKEY = () => CUR === 'hi' ? 'hi' : 'all';
const REVROWS = () => [...REV[REVKEY()].classes].sort((a, b) => b.count - a.count);
function drawSteps() {
  const V = TRV, R = HAND.results; if (!V || !byId('clf-steps')) return;
  const big = V.weights[0], small = V.weights[V.weights.length - 1], wmx = Math.max(big.share, big.draws);
  const wrow = (c, lbl, v, col) => `<div><span>${lbl}</span><i><em style="width:${100 * v / wmx}%;background:${col}"></em></i><b>${pc(v)}</b></div>`;
  const best = R ? R.best_epoch + 1 : null, run = R ? R.epochs_run : 21;
  const NTR = +'__NTRAIN__'.replace(/,/g, ''), NVA = +'__NVALID__'.replace(/,/g, ''), NTE = +'__NTEST__'.replace(/,/g, '');
  const card = (n, t, vis, how, why) => `<div class="stp"><div class="nm"><i>STEP ${n}</i><b>${t}</b></div><div class="vis">${vis}</div><p><b>How.</b> ${how}</p><p><b>Why.</b> ${why}</p></div>`;
  byId('clf-steps').innerHTML = [
    card(1, 'Transfer learning', `<div class="tl"><span><b>ImageNet</b>14 million photos</span><span class="ar">→</span><span><b>ConvNeXt-Tiny</b>28.6 M weights, kept</span><span class="ar">→</span><span><b>New last layer</b>9 device types</span></div>`,
      'Start from ConvNeXt-Tiny as trained on ImageNet and replace only its last layer with a new 9-type one.', 'It already knows edges, ports and textures; with about __NTRAIN__ crops it only has to learn our types.'),
    card(2, 'Resizing', `<div class="pair"><figure><img src="data:image/jpeg;base64,${V.orig}" alt=""><figcaption>${fmt(V.w)} × ${fmt(V.h)} px as cut out</figcaption></figure><span>→</span><figure><img src="data:image/jpeg;base64,${V.box}" alt=""><figcaption>in the 512 × 128 box, same shape</figcaption></figure></div>`,
      'Shrink or grow each crop, keeping its proportions, until it fits a 512 × 128 box; fill the rest with grey.', 'The model needs every input the same size. Devices are wide, so a wide box keeps the device large.'),
    card(3, 'Augmentation', `<div class="aug">${V.aug.map(a => `<figure><img src="data:image/jpeg;base64,${a}" alt=""></figure>`).join('')}<figcaption style="grid-column:1/-1">the same ${V.label} four times, as the model sees it in training</figcaption></div>`,
      'Each time a crop is used: trim up to 8 % off each edge, flip it half the time, shift brightness, contrast and colour a little, and place it at a random size and spot in the box.', 'So it learns what a device looks like, not one exact picture; the trims copy the loose boxes the detector gives at run time.'),
    card(4, 'Class weights', `<div class="wb">${wrow(big, `${big.name} · crops`, big.share, 'var(--base)')}${wrow(big, `${big.name} · draws`, big.draws, 'var(--s1)')}${wrow(small, `${small.name} · crops`, small.share, 'var(--base)')}${wrow(small, `${small.name} · draws`, small.draws, 'var(--s1)')}</div>`,
      `Pick each training crop with a chance of 1 ÷ √(crops in its type), so rare types come up more often: ${small.name} is ${pc(small.share)} of the crops but ${pc(small.draws)} of the draws.`, 'Otherwise the model learns to answer the big types. The square root is a middle path: full balancing repeats the rare crops so often that the model memorises them.'),
    card(5, 'Patience', `<div class="rounds">${Array.from({length: run}, (_, i) => `<i class="${i + 1 === best ? 'best' : i + 1 > best ? 'wait' : ''}" title="round ${i + 1}"></i>`).join('')}</div><div class="rlg"><span>round 1</span><span>best: round ${best}</span><span>stopped: round ${run}</span></div>`,
      `After each round, score the ${fmt(NVA)} valid crops. Keep the weights of the best round; stop after 8 rounds with no improvement.`, `Training too long makes the model memorise the training crops and get worse on new ones. This run peaked at round ${best} and stopped at round ${run}.`),
    card(6, 'Evaluation', `<div class="splt"><div style="flex:${NTR};background:var(--base)" title="train"></div><div style="flex:${NVA};background:#e0a03a" title="valid"></div><div style="flex:${NTE};background:#3f9b57" title="test"></div></div><div class="legend" style="margin:0;font-size:11.5px"><span><i style="background:var(--base)"></i>train ${fmt(NTR)} · learns from</span><span><i style="background:#e0a03a"></i>valid ${fmt(NVA)} · picks the best round</span><span><i style="background:#3f9b57"></i>test ${fmt(NTE)} · scored once</span></div>`,
      'Choose the best round by valid macro-F1, then score the separate test crops once with those weights.', 'Macro-F1 counts every type the same, so rare types matter. The test crops never choose anything, so their score stays honest.')
  ].join('');
}
function drawClfKpis() {
  const R = HAND.results, M = HAND.other && HAND.other.metrics;
  byId('clf-kpis').innerHTML = [['Chosen model', 'ConvNeXt-Tiny', '28.6 M parameters · free to use', true], ['Training crops', '__NTRAIN__', 'from photos of 1 MP or more', false],
    ['Training length', R ? `${R.epochs_run} rounds` : '-', R ? `best after round ${R.best_epoch + 1}` : '', false], ['Test', M ? pc(M.accuracy) : '-', M ? `accuracy · macro-F1 ${pc(M.macro_f1)}` : '', false]]
    .map(([k, v, f, t]) => `<div class="kpi"><div class="k">${k}</div><div class="v${t ? ' t' : ''}">${v}</div><div class="f">${f}</div></div>`).join('');
}
function drawClfTest() {
  const M = HAND.other && HAND.other.metrics; if (!M) return;
  byId('clf-test').innerHTML = [['Accuracy', M.accuracy, 'share of crops right'], ['Macro-F1', M.macro_f1, 'every type counts the same'], ['Balanced accuracy', M.balanced_accuracy, 'right per type, averaged'],
    ['Right in the top two', M.top2_accuracy, 'the right type is the first or second answer'], ["Cohen's kappa", M.kappa, 'agreement beyond chance; above 0.8 is very strong']].map(([n, v, d]) =>
    `<div class="rr" style="--c1:190px"><div class="lb"><b>${n}</b><span>${d}</span></div><div class="tk"><div style="width:${100 * v}%;background:var(--s1)"></div></div><span class="v">${n.startsWith('Cohen') ? v.toFixed(3) : pc(v)}</span></div>`).join('');
}
function drawReview() {
  const rows = REVROWS();
  byId('r-samples').innerHTML = rows.map(c => `<div class="srow"><div class="nm">${c.name}<span>${fmt(c.count)} instances</span></div><div class="thumbs rthumbs">` +
    c.thumbs.map(([b], j) => `<button data-r="${c.name}" data-j="${j}" aria-label="Open ${c.name} crop ${j + 1}"><img alt="${c.name} crop" src="data:image/jpeg;base64,${b}"></button>`).join('') + '</div></div>').join('');
}

/* ---------- shared grouped bars ---------- */
const PAL = ['var(--s1)', 'var(--s2)', 'var(--s3)', '#6250d6'];
function groupedBars(id, cats, series, opt) {
  const top = 22, bottom = 40, left = 44, right = 8, h = opt.h || 300;
  const [s, w] = svgIn(id, h);
  const lo = opt.lo || 0, hi = opt.hi || 100, pw = w - left - right, ph = h - top - bottom;
  const y = v => top + ph - ph * (v - lo) / (hi - lo), bw = pw / cats.length, n = series.length, cw = Math.min(30, bw * .8 / n);
  for (let t = lo; t <= hi + 1e-9; t += opt.step || 10) { el('line', {x1: left, x2: w - right, y1: y(t), y2: y(t), stroke: 'var(--grid)'}, s); el('text', {x: left - 6, y: y(t) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--muted)'}, s, t + (opt.unit || '')); }
  cats.forEach((c, i) => {
    const cx = left + i * bw + bw / 2, x0 = cx - cw * n / 2;
    series.forEach((sr, j) => {
      const v = sr.v[i]; if (v == null) return;
      const x = x0 + j * cw, yv = y(Math.max(lo, v));
      el('path', {d: `M${x + 1},${y(lo)} V${yv + 3} q0,-3 3,-3 H${x + cw - 4} q3,0 3,3 V${y(lo)} Z`, fill: sr.c}, s);
      if (opt.labels) el('text', {x: x + cw / 2, y: yv - 4, 'text-anchor': 'middle', 'font-size': 10.5, fill: 'var(--ink)'}, s, v.toFixed(opt.dp == null ? 1 : opt.dp));
    });
    xLabel(s, cx, top + ph + 15, c, bw);
    el('rect', {x: left + i * bw, y: top, width: bw, height: ph, fill: 'transparent', 'data-tip': `<b>${c}</b><br>` + series.map(sr => `${sr.name} ${sr.v[i] == null ? '-' : sr.v[i].toFixed(1) + (opt.unit || '')}`).join('<br>')}, s);
  });
  el('line', {x1: left, x2: w - right, y1: y(lo), y2: y(lo), stroke: 'var(--base)'}, s);
  if (lo > 0) el('text', {x: left, y: 12, 'font-size': 11, fill: 'var(--muted)'}, s, 'axis starts at ' + lo + (opt.unit || ''));
}
/* ---------- live run tab ---------- */
let RUN_HEAD = '', RUN_LEAD = '';
let RUN_SEL = RUNS.length ? RUNS[0].arch : null;
function lineChart(id, series, opt) {
  const h = opt.h || 280, top = 22, bottom = 36, left = 48, right = 14; const [s, w] = svgIn(id, h);
  const n = Math.max(2, ...series.map(sr => sr.v.length)), pw = w - left - right, ph = h - top - bottom;
  const vals = series.flatMap(sr => sr.v), lo = opt.lo != null ? opt.lo : Math.max(0, Math.floor((Math.min(...vals) - 5) / 10) * 10), hi = opt.hi != null ? opt.hi : Math.ceil(Math.max(...vals) / 10) * 10 || 1;
  const X = e => left + pw * e / (n - 1), Y = v => top + ph - ph * (v - lo) / (hi - lo);
  const step = opt.step || (hi - lo) / 5;
  for (let t = lo; t <= hi + 1e-9; t += step) { el('line', {x1: left, x2: w - right, y1: Y(t), y2: Y(t), stroke: 'var(--grid)'}, s); el('text', {x: left - 6, y: Y(t) + 4, 'text-anchor': 'end', 'font-size': 11, fill: 'var(--muted)'}, s, opt.fmt ? opt.fmt(t) : t); }
  const st = n > 20 ? 5 : n > 10 ? 2 : 1;
  for (let e = 0; e < n; e += st) el('text', {x: X(e), y: top + ph + 16, 'text-anchor': 'middle', 'font-size': 11, fill: 'var(--muted)'}, s, e + 1);
  el('text', {x: left + pw / 2, y: h - 4, 'text-anchor': 'middle', 'font-size': 11.5, fill: 'var(--ink2)'}, s, 'epoch');
  series.forEach(sr => {
    const pts = sr.v.map((v, k) => [X(k), Y(v)]);
    if (pts.length > 1) el('polyline', {points: pts.map(p => p.join(',')).join(' '), fill: 'none', stroke: sr.c, 'stroke-width': 2, 'stroke-linejoin': 'round'}, s);
    pts.forEach(([x, y], k) => el('circle', {cx: x, cy: y, r: 3.5, fill: sr.c, 'data-tip': `<b>epoch ${k + 1}</b><br>${sr.name} ${opt.fmt ? opt.fmt(sr.v[k]) : sr.v[k]}`}, s));
    if (sr.mark != null && pts[sr.mark]) el('circle', {cx: pts[sr.mark][0], cy: pts[sr.mark][1], r: 7, fill: 'var(--card)', stroke: sr.c, 'stroke-width': 2.5}, s);
  });
}
function cmChart(id, m, C) {
  const cm = m.confusion, n = C.length, cell = 40, lab = 100, top = 66;
  const [s, w] = svgIn(id, top + n * cell + 10);
  const x0 = Math.max(lab, (w - n * cell) / 2 + lab / 2 - 20);
  el('text', {x: x0 + n * cell / 2, y: 14, 'text-anchor': 'middle', 'font-size': 11.5, fill: 'var(--muted)'}, s, 'answer');
  C.forEach((c, j) => el('text', {x: x0 + j * cell + cell / 2, y: top - 8, 'text-anchor': 'start', 'font-size': 10.5, fill: 'var(--ink2)', transform: `rotate(-35 ${x0 + j * cell + cell / 2} ${top - 8})`}, s, c));
  cm.forEach((row, i) => {
    const tot = row.reduce((a, b) => a + b, 0) || 1;
    el('text', {x: x0 - 8, y: top + i * cell + cell / 2 + 4, 'text-anchor': 'end', 'font-size': 11.5, fill: 'var(--ink2)'}, s, C[i]);
    row.forEach((v, j) => {
      const p = v / tot, diag = i === j;
      const fill = v === 0 ? 'var(--chip)' : diag ? `color-mix(in srgb, var(--s1) ${Math.round(25 + 75 * p)}%, var(--card))` : `color-mix(in srgb, var(--s2) ${Math.round(20 + 80 * Math.min(1, p * 2))}%, var(--card))`;
      el('rect', {x: x0 + j * cell + 1, y: top + i * cell + 1, width: cell - 2, height: cell - 2, rx: 4, fill, 'data-tip': `<b>${C[i]}</b> answered as ${C[j]}<br>${v} of ${tot} crops (${(100 * p).toFixed(0)} %)`}, s);
      if (v) el('text', {x: x0 + j * cell + cell / 2, y: top + i * cell + cell / 2 + 4, 'text-anchor': 'middle', 'font-size': 11.5, 'font-weight': diag ? 600 : 400, fill: (diag && p > .55) || (!diag && p > .35) ? '#fff' : 'var(--ink)', 'pointer-events': 'none'}, s, v);
    });
  });
}
function classTable(id, m, label, C) {
  const counts = m.confusion.map(r => r.reduce((a, b) => a + b, 0));
  byId(id).innerHTML = `<thead><tr><th>Class</th><th>${label} crops</th><th>Recall</th><th>Precision</th><th>F1</th><th class="l">Most confused with</th></tr></thead><tbody>` +
    C.map((c, i) => { const row = m.confusion[i].map((v, j) => [v, j]).filter(([v, j]) => j !== i && v > 0).sort((a, b) => b[0] - a[0])[0];
      return `<tr><td>${c}</td><td>${counts[i]}</td><td>${(m.recall[i] * 100).toFixed(1)} %</td><td>${(m.precision[i] * 100).toFixed(1)} %</td><td><b>${(m.f1[i] * 100).toFixed(1)} %</b></td><td style="text-align:left">${row ? `${C[row[1]]} (${row[0]})` : '-'}</td></tr>`; }).join('') +
    `<tr class="tot"><td>All</td><td>${m.n}</td><td colspan="2">accuracy ${(m.acc * 100).toFixed(1)} %</td><td><b>macro ${(m.macro_f1 * 100).toFixed(1)} %</b></td><td></td></tr></tbody>`;
}
function drawRun() {
  const r = RUNS.find(x => x.arch === RUN_SEL); if (!r) return;
  const H = r.history, done = r.status === 'done', b = r.best, bi = b ? b.epoch : -1;
  const secs = H.length ? H.slice(-3).reduce((a, e) => a + e.secs, 0) / Math.min(3, H.length) : 0;
  const left = Math.max(0, r.max_epochs - H.length), eta = Math.round(left * secs / 60);
  RUN_HEAD = r.label + (done ? ': training run' : ': live training run');
  RUN_LEAD = done
    ? `Trained on the high-resolution set. ${H.length} epochs, best at epoch ${bi + 1}. The test split was scored once with those weights.`
    : `Training now on the high-resolution set. ${H.length} of up to ${r.max_epochs} epochs done. This page is rebuilt after every epoch that sets a new best.`;
  const bv = b ? b.val : null;
  byId('run-kpis').innerHTML = [
    ['Best valid macro-F1', bv ? (bv.macro_f1 * 100).toFixed(1) + ' %' : '-', bv ? `at epoch ${bi + 1} of ${H.length}` : 'no epoch yet'],
    ['Valid accuracy', bv ? (bv.acc * 100).toFixed(1) + ' %' : '-', 'at the best epoch'],
    done ? ['Test macro-F1', HAND.other && HAND.other.metrics ? (HAND.other.metrics.macro_f1 * 100).toFixed(1) + ' %' : '-', HAND.other && HAND.other.metrics ? `accuracy ${(HAND.other.metrics.accuracy * 100).toFixed(1)} %; details in Evaluation` : ''] : ['Epochs done', `${H.length} / ${r.max_epochs}`, `about ${(secs / 60).toFixed(1)} min each · up to ${eta} min left`],
    ['Page updated', r.updated, done ? 'final' : 'rebuilt on each new best epoch']
  ].map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('');
  byId('run-note').innerHTML = done ? '' : `<div class="callout"><span class="ic">i</span><p>Running at half duty cycle so the Mac stays cool. ${H.length && H.length - 1 - bi >= 1 ? `${H.length - 1 - bi} epoch${H.length - 1 - bi > 1 ? 's' : ''} without a gain; training stops after 8.` : 'The last epoch was a new best.'} ${r.last_step ? 'Last log line: <code>' + r.last_step + '</code>' : ''}</p></div>`;
  lineChart('c-run-curve', [{name: 'macro-F1', c: 'var(--s1)', v: H.map(e => e.val_macro_f1 * 100), mark: bi}, {name: 'accuracy', c: 'var(--s3)', v: H.map(e => e.val_acc * 100)}], {lo: 0, hi: 100, step: 20, fmt: v => v.toFixed(0) + ' %'});
  lineChart('c-run-loss', [{name: 'train loss', c: 'var(--s2)', v: H.map(e => e.train_loss)}], {lo: 0, hi: Math.ceil(Math.max(...H.map(e => e.train_loss)) * 2) / 2, step: 0.5, fmt: v => v.toFixed(1)});
  const m = (done && r.val && r.val.confusion) ? r.val : bv, lab = 'Valid';
  byId('run-cls-title').textContent = `Per-class scores at the best epoch (${bi + 1}), valid split`;
  byId('run-cls-sub').textContent = '__SMALL__ has only __SMALLVALID__ valid crops, so its bars are noisy. Load Balancer is left out (__LBTRAIN__ training crops). Test results are in Evaluation.';
  byId('run-cm-title').textContent = `Confusion matrix, valid split, epoch ${bi + 1}`;
  byId('run-tbl-sub').textContent = `Valid split, best epoch.`;
  if (m) {
    groupedBars('c-run-class', r.classes || RES.classes, [{name: 'recall', c: 'var(--s1)', v: m.recall.map(v => v * 100)}, {name: 'precision', c: 'var(--s2)', v: m.precision.map(v => v * 100)}], {lo: 0, hi: 100, step: 20, unit: ' %', labels: true, dp: 0, h: 300});
    cmChart('c-run-cm', m, r.classes || RES.classes); classTable('run-table', m, lab, r.classes || RES.classes);
  } else { for (const id of ['c-run-class', 'c-run-cm']) byId(id).innerHTML = '<div class="empty">Appears after the first epoch.</div>'; byId('run-table').innerHTML = ''; }
  let best = -1;
  byId('run-epochs').innerHTML = '<thead><tr><th>Epoch</th><th>Train loss</th><th>Valid accuracy</th><th>Valid macro-F1</th><th>Minutes</th><th class="l">Note</th></tr></thead><tbody>' +
    H.map((e, k) => { const nb = e.val_macro_f1 > best; if (nb) best = e.val_macro_f1;
      return `<tr${nb ? ' style="font-weight:600"' : ''}><td>${k + 1}</td><td>${e.train_loss.toFixed(3)}</td><td>${(e.val_acc * 100).toFixed(1)} %</td><td>${(e.val_macro_f1 * 100).toFixed(1)} %</td><td>${(e.secs / 60).toFixed(1)}</td><td style="text-align:left">${nb ? 'new best, weights saved' : ''}${k < 2 ? (nb ? ' · ' : '') + 'warm-up' : ''}</td></tr>`; }).join('') + '</tbody>';
}

/* ---------- full metrics on the live tab ---------- */
const pc = v => (v * 100).toFixed(1) + ' %';
function drawMetrics() {
  const M = HAND.other && HAND.other.metrics, tb = byId('run-metrics');
  if (!M) { tb.innerHTML = '<tbody><tr><td class="empty" style="text-align:center">The full metric set is computed on the test split right after training ends (about 19:25).</td></tr></tbody>'; for (const id of ['c-run-howto', 'c-run-rel']) byId(id).innerHTML = '<div class="empty">After training.</div>'; byId('run-howto').innerHTML = ''; byId('run-scorecard').innerHTML = ''; return; }
  const C = M.classes, pcs = M.per_class, worst = C.slice().sort((a, b) => pcs[a].f1 - pcs[b].f1)[0], best = C.slice().sort((a, b) => pcs[b].f1 - pcs[a].f1)[0];
  const ex = pcs[worst];
  const rows = [
    ['Accuracy', M.accuracy, 'Share of all test crops given the right class.', `${fmt(Math.round(M.accuracy * M.n))} of ${fmt(M.n)} crops right. Flattered by the big classes: Switch, Patch Panel and Server are 3 in 4 crops.`],
    ['Balanced accuracy', M.balanced_accuracy, 'Accuracy worked out per class, then averaged, so every class counts the same.', `Lower than accuracy because the small classes are harder. The same as macro recall.`],
    ['Macro precision', M.macro_precision, 'Average over classes of: when the model says this class, how often is it right?', `${worst}: ${pc(ex.precision)} of its ${ex.predicted} answers were right.`],
    ['Macro recall', M.macro_recall, 'Average over classes of: of the real devices of this class, how many did it find?', `${worst}: found ${ex.correct} of ${ex.support} (${pc(ex.recall)}).`],
    ['Macro F1', M.macro_f1, 'Average over classes of F1, the balance of precision and recall. Our main score: every class weighs the same.', `Ranges from ${pc(pcs[worst].f1)} (${worst}) to ${pc(pcs[best].f1)} (${best}).`],
    ['Weighted precision', M.weighted_precision, 'Like macro precision, but big classes count more, by their number of crops.', 'Close to accuracy, because the big classes dominate.'],
    ['Weighted recall', M.weighted_recall, 'Like macro recall, weighted by class size. Always equals accuracy.', ''],
    ['Weighted F1', M.weighted_f1, 'Like macro F1, weighted by class size.', 'The gap to macro F1 shows how much weaker the small classes are.'],
    ['Top-2 accuracy', M.top2_accuracy, 'Share of crops where the right class is the first or second guess.', `High top-2 means most mistakes are near misses between two look-alike classes.`],
    ["Cohen's kappa", M.kappa, 'Agreement with the labels after removing what lucky guessing would get. 1 is perfect, 0 is chance, above 0.8 is very strong.', ''],
    ['Calibration error (ECE)', M.ece, 'Average gap between how sure the model says it is and how often it is right. 0 % is perfectly honest.', `Mean confidence ${pc(M.mean_confidence)} against accuracy ${pc(M.accuracy)}. Label smoothing in training makes it a little under-confident.`]
  ];
  tb.innerHTML = '<thead><tr><th>Metric</th><th>Value</th><th class="l">What it means</th><th class="l">In this model</th></tr></thead><tbody>' +
    rows.map(([n, v, w, e], i) => `<tr${i === 4 ? ' style="font-weight:600"' : ''}><td>${n}</td><td><b>${n.startsWith('Cohen') ? v.toFixed(3) : pc(v)}</b></td><td style="text-align:left;white-space:normal;min-width:260px">${w}</td><td style="text-align:left;white-space:normal;min-width:260px;color:var(--ink2)">${e}</td></tr>`).join('') + '</tbody>';
  // worked example: worst class
  const ci = C.indexOf(worst), col = M.confusion.map(r => r[ci]), row = M.confusion[ci];
  const tp = row[ci], fn = ex.support - tp, fp = ex.predicted - tp;
  byId('run-howto-sub').textContent = `${worst}, the weakest class, worked through with the real test counts.`;
  const h = 260; const [s, w] = svgIn('c-run-howto', h);
  const box = (x, y, bw, bh, fill, t1, t2) => { el('rect', {x, y, width: bw, height: bh, rx: 8, fill}, s); el('text', {x: x + bw / 2, y: y + bh / 2 - 2, 'text-anchor': 'middle', 'font-size': 22, 'font-weight': 600, fill: 'var(--ink)'}, s, t1); el('text', {x: x + bw / 2, y: y + bh / 2 + 18, 'text-anchor': 'middle', 'font-size': 11.5, fill: 'var(--ink2)'}, s, t2); };
  const bw = (w - 40) / 3;
  box(0, 40, bw, 110, 'color-mix(in srgb, var(--s2) 30%, var(--card))', fn, 'missed: called something else');
  box(bw + 20, 40, bw, 110, 'color-mix(in srgb, var(--s1) 40%, var(--card))', tp, 'found: right');
  box(2 * bw + 40, 40, bw, 110, 'color-mix(in srgb, var(--s2) 30%, var(--card))', fp, 'wrongly called ' + worst);
  el('line', {x1: 0, x2: 2 * bw + 20, y1: 24, y2: 24, stroke: 'var(--s1)', 'stroke-width': 2}, s); el('text', {x: (2 * bw + 20) / 2, y: 16, 'text-anchor': 'middle', 'font-size': 12, fill: 'var(--ink)'}, s, `all ${ex.support} real ${worst} crops → recall ${pc(ex.recall)}`);
  el('line', {x1: bw + 20, x2: w, y1: 172, y2: 172, stroke: 'var(--s1)', 'stroke-width': 2}, s); el('text', {x: (bw + 20 + w) / 2, y: 192, 'text-anchor': 'middle', 'font-size': 12, fill: 'var(--ink)'}, s, `all ${ex.predicted} answers "${worst}" → precision ${pc(ex.precision)}`);
  el('text', {x: w / 2, y: 236, 'text-anchor': 'middle', 'font-size': 13, 'font-weight': 600, fill: 'var(--ink)'}, s, `F1 = 2 × precision × recall / (precision + recall) = ${pc(ex.f1)}`);
  const mc = row.map((v, j) => [v, j]).filter(([v, j]) => j !== ci && v).sort((a, b) => b[0] - a[0])[0], mp = col.map((v, j) => [v, j]).filter(([v, j]) => j !== ci && v).sort((a, b) => b[0] - a[0])[0];
  byId('run-howto').innerHTML = `<dl class="kv">
    <dt>Recall</dt><dd>Of the ${ex.support} real ${worst} crops in the test set, the model found ${tp}. Recall = ${tp} / ${ex.support} = ${pc(ex.recall)}.${mc ? ` Most of the misses went to ${C[mc[1]]} (${mc[0]}).` : ''}</dd>
    <dt>Precision</dt><dd>The model answered "${worst}" ${ex.predicted} times and ${tp} of those were right. Precision = ${tp} / ${ex.predicted} = ${pc(ex.precision)}.${mp ? ` Most wrong answers were really ${C[mp[1]]} (${mp[0]}).` : ''}</dd>
    <dt>F1</dt><dd>One number that is high only when both are high. A model that says "${worst}" for everything would have 100 % recall but tiny precision, and a low F1.</dd>
    <dt>Macro</dt><dd>Work out the score for each of the ${C.length} classes, then take the plain average. That is why one weak class pulls macro F1 down.</dd></dl>`;
  // reliability
  const R = M.reliability; groupedBars('c-run-rel', R.map(b => `${Math.round(b[0] * 100)}-${Math.round(b[1] * 100)} %`), [{name: 'actual accuracy', c: 'var(--s1)', v: R.map(b => b[3] * 100)}], {lo: 0, hi: 100, step: 20, unit: ' %', h: 280, labels: true, dp: 0});
  // scorecard
  byId('run-scorecard').innerHTML = '<thead><tr><th>Class</th><th>Crops</th><th>Precision</th><th>Recall</th><th>F1</th><th>Confidence</th></tr></thead><tbody>' +
    C.slice().sort((a, b) => pcs[b].f1 - pcs[a].f1).map(c => { const q = pcs[c]; return `<tr><td>${c}</td><td>${q.support}</td><td><span class="lic ${col3(q.precision)}">${pc(q.precision)}</span></td><td><span class="lic ${col3(q.recall)}">${pc(q.recall)}</span></td><td><span class="lic ${col3(q.f1)}">${pc(q.f1)}</span></td><td>${pc(q.mean_conf)}</td></tr>`; }).join('') + '</tbody>';
}

/* ---------- per-100 grids ---------- */
function per100(parts) {   // parts: [[count, cls], ...] -> 100 squares, largest remainder rounding
  const tot = parts.reduce((a, p) => a + p[0], 0), raw = parts.map(p => 100 * p[0] / tot), fl = raw.map(Math.floor);
  let left = 100 - fl.reduce((a, b) => a + b, 0);
  raw.map((r, i) => [r - fl[i], i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (left > 0) { fl[i]++; left--; } });
  return { html: `<div class="waf">${parts.map((p, i) => `<i class="${p[1]}"></i>`.repeat(fl[i])).join('')}</div>`, n: fl };
}
const WKEY = (other) => `<div class="wkey"><span><i style="background:#cfe9d4"></i>right</span>${other ? '<span><i style="background:#c9ced4"></i>Other (unknown)</span>' : ''}<span><i style="background:#d64545"></i>wrong</span><span>· each square is one device in 100</span></div>`;

/* ---------- why not 100 % ---------- */
const col3 = v => v >= .9 ? 'ok' : v >= .75 ? 'warn' : 'no';
const VCOL = v => v >= .9 ? '#3f9b57' : v >= .75 ? '#e0a03a' : '#d64545';
const REASON = {
  'Server|Storage unit': 'Both are 2U boxes full of drive bays. Many storage units are built on server chassis, so the front alone often cannot tell them apart.',
  'Firewall|Switch': 'Many firewalls are 1U boxes with a row of network ports, just like a switch. The brand name (FortiGate, Sophos) is the main clue, and it is small.',
  'Patch Panel|Switch': 'Both show long rows of sockets with cables plugged in. When cables hide the lights, a switch looks like a patch panel.',
  'Router|Switch': 'Small routers are 1U boxes with a few ports and lights, like a small switch.',
  'Router|Server': 'Large routers are 2U boxes with module slots and vents, like a server.',
  'Gateway|Router': 'Gateways and routers are often the same kind of small box; the difference is their job, not their look.',
  'Firewall|Router': 'Both are small boxes with a few ports; the difference is their job, not their look.'};
const pairRow = q => `<div class="pr3">
  <figure class="me"><span class="tagr ok">Real: ${q.label}</span><img src="data:image/jpeg;base64,${q.img}" alt="${q.label}">
    <div class="sc2"><span><i>${q.pred}</i><b class="w" style="width:${100 * q.prob}%"></b><em>${Math.round(100 * q.prob)} %</em></span>${q.lprob == null ? '' : `<span><i>${q.label}</i><b class="r" style="width:${100 * q.lprob}%"></b><em>${Math.round(100 * q.lprob)} %</em></span>`}</div><span class="sch">model's scores</span></figure>
  <div class="eqs">≈</div>
  <div class="nb"><span class="tagr no">Answered: ${q.pred} · ${q.near.length} similar ${q.pred} devices it was trained on</span><div class="nb3">${q.near.map(n => `<figure><img src="data:image/jpeg;base64,${n.img}" alt="${q.pred}"><figcaption>${Math.round(100 * n.sim)} % alike</figcaption></figure>`).join('')}</div></div></div>`;
let SUB = 'm1';
const SUBS = [['m1', 'Model 1 · ConvNeXt-Tiny'], ['m2', 'Model 2 · DINOv2-B'], ['m12', 'Model 1 + 2']].filter(([k]) => UNKS[k]);
function drawSubtabs() {
  document.querySelectorAll('.subtabs').forEach(el => { el.innerHTML = SUBS.map(([k, n]) => `<button role="tab" data-sub="${k}" aria-selected="${k === SUB}">${n}</button>`).join(''); });
}
document.addEventListener('click', e => { const b = e.target.closest('.subtabs button[data-sub]'); if (b) { SUB = b.dataset.sub; MIS_I = 0; drawSubtabs(); render(); } });
const CUT2 = () => APR && APR.cut2 != null ? APR.cut2 : null;
const SUBTXT = {
  m1: {how: 'How Model 1 decides to say Other', hows: 'For every crop it gives a score to each device type. When the top two are too close, it says Other instead of guessing.',
    yes: 'clear winner: answered', no: q => `too close to call; it is really a ${q.label}`, sure: 'Model 1 was', torn: 'What it was torn between'},
  m2: {how: 'How Model 2 decides to say Other', hows: 'Model 2 finds devices in the whole photo and gives each box a score. No box on the device, or a low score, means Other.',
    yes: 'box found, score high enough: answered', no: q => `${q.reason}; it is really a ${q.label}`, sure: 'Model 2 was', torn: 'What it would have said'},
  m12: {how: 'How Model 1 + 2 decides to say Other', hows: 'Both models name the device. The answer goes out only when the two names match; otherwise it is Other.',
    yes: 'both name the same type: answered', no: q => `the two models disagree; it is really a ${q.label}`, sure: 'Model 1 was', torn: 'What the two models said'}};
const noteFor = (q, key) => {
  if (key === 'm1') return gapLine({prob: q.said[0][1], sprob: q.said[1][1]});
  if (key === 'm2') { const v = q.said[0][1], t = CUT2(); return v == null ? '<span class="gp">no box on this device</span>' : t == null ? '' : `<span class="gp">score ${v.toFixed(2)} ${v >= t ? '≥' : '<'} ${t.toFixed(2)}</span>`; }
  return `<span class="gp">${q.other ? 'different types' : 'same type'}</span>`;
};
const howRow = (q, key) => `<div class="hrow"><img src="data:image/jpeg;base64,${q.img}" alt="">
    <div class="sc">${q.said.map(([n, v], i) => `<div><span>${n}</span><i${i ? ' class="l"' : ''} style="width:${v == null ? 0 : 100 * v}%"></i><b>${v == null ? '-' : Math.round(100 * v) + ' %'}</b></div>`).join('')}</div>
    <div class="arr">→</div><div class="out ${q.other ? 'oth' : 'ok'}">${q.other ? `<b>Other</b>${SUBTXT[key].no(q)}` : `<b>${q.pred}</b>${SUBTXT[key].yes}`}${noteFor(q, key)}</div></div>`;
const ruleFor = key => key === 'm1' ? ruleNote() : key === 'm2' ? `<p class="rule1"><b>The rule:</b> if Model 2 finds no box on the device, or its box score is below ${CUT2() != null ? CUT2().toFixed(2) : 'the cut-off'}, the answer is Other. The cut-off was set on the valid photos so that Model 2 says Other as often as Model 1.</p>`
  : '<p class="rule1"><b>The rule:</b> the answer is Model 1\'s, and it goes out only when Model 2 names the same type. A different type, or no box from Model 2, means Other.</p>';
const GAP = () => HAND.other && HAND.other.cutoffs && HAND.other.cutoffs.margin != null ? Math.round(100 * HAND.other.cutoffs.margin) : null;
const gapLine = q => { const g = Math.round(100 * q.prob) - Math.round(100 * q.sprob), t = GAP(); return t == null ? '' : `<span class="gp">gap ${g} points ${g >= t ? '≥' : '<'} ${t}</span>`; };
const ruleNote = () => GAP() == null ? '' : `<p class="rule1"><b>The rule:</b> if the top two scores are less than ${GAP()} points apart, the answer is Other. It also answers Other when a device looks unlike every device it was trained on.</p>`;
const reason = (a, b) => REASON[[a, b].sort().join('|')] || 'These two look alike from the front.';
const EWHY = {rack: ['Same device repeated in one rack', 'A rack full of one device model that looks like another type: the same mistake on every copy.'],
  angle: ['Tilted, side view or partly hidden', 'The device fills under half of its outline, so the model sees only part of its front.'],
  blur: ['Blurry or dark', 'Too little detail in the photo to tell the type.'],
  alike: ['Looks like another type', 'A clear photo, but the device really looks like the type it was called.']};
function drawWhy() {
  const E = EDGES[SUB]; if (!E) return;
  const CL = E.clear, WR0 = E.without_racks && E.without_racks[E.without_racks.length - 1];
  byId('edge-kpis').innerHTML = [
    ['Answers right after Other', pc(E.right / E.answered), `${fmt(E.right)} of ${fmt(E.answered)} answers`],
    ['Still wrong', fmt(E.wrong), `of ${fmt(E.n)} test crops`],
    ['In racks', fmt(E.racks.length), (() => { const top = E.racks.filter(r => r.n >= 3); return `${top.length} racks hold ${top.reduce((a, r) => a + r.n, 0)} of the ${E.wrong}`; })()],
    WR0 ? [`Without the ${WR0.k} worst racks`, pc(WR0.o_right / (WR0.o_right + WR0.o_wrong)), `of answers right after Other, ${WR0.o_wrong} wrong`] : ['How sure it was', E.min_prob != null ? `≥ ${Math.round(100 * E.min_prob)} %` : '-', 'on every one of them']
  ].map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('');
  const cnt = k => E.cases.filter(c => c.why === k).length, keys = Object.keys(EWHY).filter(k => cnt(k)).sort((a, b) => cnt(b) - cnt(a)), mx = Math.max(...keys.map(cnt));
  byId('edge-why-t').textContent = `Why ${E.wrong} are still wrong`;
  if (E.min_prob != null) byId('edge-why-s').textContent = `Every wrong answer left after Other, sorted into one reason each, to scale. ${SUB === 'm12' ? `Both models named the same wrong type, and Model 1 was at least ${Math.round(100 * E.min_prob)} % sure` : `${SUBTXT[SUB].sure} at least ${Math.round(100 * E.min_prob)} % sure of each of them`}, so Other could not catch them.`;
  const ex = k => { const q = E.examples && E.examples[k]; return q ? `<figure class="exf"><img src="data:image/jpeg;base64,${q.img}" alt="" style="height:30px">${q.soft ? '<span class="softtag">blurry</span>' : ''}</figure>` : '<div></div>'; };
  byId('edge-why').innerHTML = keys.map(k => `<div class="rr" style="grid-template-columns:120px minmax(0,1.1fr) minmax(0,1fr) 40px"><div>${ex(k)}</div><div class="lb"><b>${EWHY[k][0]}</b><span>${EWHY[k][1]}</span></div><div class="tk"><div style="width:${100 * cnt(k) / mx}%;background:#d64545"></div></div><span class="v">${cnt(k)}</span></div>`).join('');
  const S = E.stress;
  if (S) {
    const base = S.find(x => x.name === 'normal'), NM = {normal: ['As photographed', 'as they are'], darker: ['Darker', 'half the light'], glare: ['Glare', 'much brighter'], blurred: ['Blurred', 'out of focus'], tilted: ['Tilted', '8 degrees'], 'cut off': ['Cut off', 'a third outside the photo'], covered: ['Covered', 'a third hidden']};
    const mx = Math.max(...S.map(x => x.wrong_answers)), worst = S.filter(x => x.name !== 'normal').sort((a, b) => b.wrong_answers - a.wrong_answers)[0];
    const DO = {normal: 'the crops as they are, for comparison', darker: 'every crop made much darker', glare: 'every crop made much brighter', blurred: 'every crop put out of focus', tilted: 'every crop turned by 8 degrees', 'cut off': 'a third of every crop cut away', covered: 'a third of every crop hidden'};
    byId('edge-st-t').textContent = `${NM[worst.name][0]} hurts most: ${worst.wrong_answers - base.wrong_answers} more wrong answers`;
    byId('edge-st-s').textContent = `Can it cope with worse photos than it was given? We damage all ${fmt(base.n)} test crops in one way, ask the model again (Other on) and count the wrong answers. The same device shows what each row did to the crops.`;
    byId('edge-stress').innerHTML = S.map(x => `<div class="rr" style="--c1:270px;grid-template-columns:120px 150px minmax(0,1fr) 110px"><img src="data:image/jpeg;base64,${x.img}" alt="" style="height:30px"><div class="lb"><b>${NM[x.name][0]}</b><span>${DO[x.name]}</span></div><div class="tk"><div style="width:${100 * x.wrong_answers / mx}%;background:${x.name === 'normal' ? 'var(--base)' : '#d64545'}"></div></div><span class="v">${x.wrong_answers} wrong${x.name === 'normal' ? '' : ` <small style="font-weight:400;color:var(--ink2)">+${x.wrong_answers - base.wrong_answers}</small>`}</span></div>`).join('');
  } else { byId('edge-st-t').textContent = 'Breaking it on purpose'; byId('edge-st-s').textContent = ''; byId('edge-stress').innerHTML = `<p class="illus">Measured for Model 1 only. The test damages one device crop at a time; ${SUB === 'm2' ? 'Model 2 looks at the whole photo, so it needs its own version of the test' : 'Model 1 + 2 needs Model 2 run on the damaged photos as well'}. Not measured yet.</p>`; }
  byId('edge-racks-t').textContent = `The ${E.racks.length} racks and their ${E.wrong} mistakes`;
  const GR = [['3 or more mistakes', r => r.n >= 3], ['2 mistakes', r => r.n === 2], ['1 mistake', r => r.n === 1]].map(([t, f]) => ({t, racks: E.racks.filter(f)})).filter(g => g.racks.length);
  byId('edge-rgroups').innerHTML = GR.map(g => { const m = g.racks.reduce((a, r) => a + r.n, 0); return `<div class="rr" style="--c1:150px"><div class="lb"><b>${g.t}</b><span>${g.racks.length} rack${g.racks.length > 1 ? 's' : ''}</span></div><div class="tk"><div style="width:${100 * m / E.wrong}%;background:#d64545"></div></div><span class="v">${m} wrong</span></div>`; }).join('');
  const WR = E.without_racks, acc = t => t.right / t.n, oa = t => t.o_right / (t.o_right + t.o_wrong);
  if (WR) {
    byId('edge-wr-t').textContent = `Without the worst racks: ${pc(oa(WR[WR.length - 1]))} of answers right`;
    byId('edge-wracks').innerHTML = `<div class="rr" style="--c1:150px"><div class="lb"><b>All racks</b><span>${fmt(E.n)} crops · ${E.wrong} wrong</span></div><div class="tk"><div style="width:${100 * E.right / E.answered}%;background:var(--base)"></div></div><span class="v">${pc(E.right / E.answered)}</span></div>` +
      WR.map(w => `<div class="rr" style="--c1:150px"><div class="lb"><b>Without rack${w.k > 1 ? `s 1 to ${w.k}` : ' 1'}</b><span>${fmt(w.n)} crops · ${w.mistakes} mistakes gone · ${w.o_wrong} wrong</span></div><div class="tk"><div style="width:${100 * oa(w)}%;background:var(--s1)"></div></div><span class="v">${pc(oa(w))}</span></div>`).join('');
  }
  const card = (r, i) => `<div class="rkr">${r.img ? `<img src="data:image/jpeg;base64,${r.img}" alt="Rack ${i + 1}">` : '<div></div>'}<div>
    <h3>Rack ${i + 1} · ${r.n} of ${r.devices} devices wrong</h3>
    <div class="rkm">${r.mixes.map(([a, b, c]) => `<span class="tagr ok">${c} × ${a}</span><em style="font-style:normal;color:var(--muted);font-size:12px;align-self:center">called</em><span class="tagr no">${b}</span>`).join('<span style="width:10px"></span>')}</div>
    <p class="rs"><b>Why:</b> ${r.reason}</p>
    <div class="rkc">${r.crops.map(c => `<figure class="${c.soft ? 'soft' : ''}"><img src="data:image/jpeg;base64,${c.img}" alt="">${c.soft ? '<span class="softtag">blurry</span>' : ''}<figcaption><b>${c.label}</b> → ${c.pred} · ${Math.round(100 * c.prob)} %</figcaption></figure>`).join('')}</div></div></div>`;
  const row = (r, i) => { const c = r.crops[0]; return `<div class="rk1">${r.img ? `<img src="data:image/jpeg;base64,${r.img}" alt="Rack ${i + 1}">` : '<div></div>'}<figure><img src="data:image/jpeg;base64,${c.img}" alt="">${c.soft ? '<span class="softtag">blurry</span>' : ''}</figure><div class="t"><b>Rack ${i + 1} · ${c.label} called ${c.pred} · ${Math.round(100 * c.prob)} %</b><span>${r.reason}</span></div></div>`; };
  byId('edge-racks').innerHTML = GR.map((g, gi) => `<h3 class="h3s" style="margin:${gi ? 14 : 0}px 0 2px;font-size:14px">${g.t} · ${g.racks.length} rack${g.racks.length > 1 ? 's' : ''}, ${g.racks.reduce((a, q) => a + q.n, 0)} wrong</h3>` +
    (g.racks[0].n === 1 ? `<div class="rk1g">${g.racks.map(r => row(r, E.racks.indexOf(r))).join('')}</div>` : g.racks.map(r => card(r, E.racks.indexOf(r))).join(''))).join('');
}

/* ---------- misclassified examples ---------- */
let MIS_I = 0;
function drawMis() {
  const W = WHYS[SUB]; if (!W) return;
  const O = W.ordered;
  const TA = W.ta, TB = W.tb; MIS_I = Math.min(MIS_I, Math.max(0, O.length - 1));
  byId('mis-kpis').innerHTML = [
    ['Mistakes', TA ? `${fmt(TB.wrong)} → ${fmt(W.wrong)}` : fmt(W.wrong), `of ${fmt(W.n)} test crops: Other off, then on. This page shows the ${fmt(W.wrong)} left with Other on.`],
    TA && ['Wrong answers turned Other', fmt(TA.caught), `of the ${fmt(TB.wrong)} mistakes: caught by Other, no longer wrong`],
    TA && ['Right answers turned Other', fmt(TA.lost), `of ${fmt(TB.right)} right answers: the cost of Other`],
    O.length ? ['Biggest mix-up', `${O[0].n}`, `${O[0].label} called ${O[0].pred}`] : ['Biggest mix-up', '-', 'no mistakes left']
  ].filter(Boolean).map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('');
  if (!O.length) { byId('mis-list').innerHTML = ''; byId('mis-detail').innerHTML = '<p>No mistakes are left on these photos.</p>'; byId('mis-list-t').textContent = 'No mix-ups left'; return; }
  const mx = O[0].n;
  byId('mis-list').innerHTML = O.map((o, i) => `<button role="tab" data-i="${i}" aria-selected="${i === MIS_I}"><span class="ln"><span class="tagr ok">${o.label}</span><em>called</em><span class="tagr no">${o.pred}</span><span class="ct">${o.n}</span></span><span class="bar"><div style="width:${100 * o.n / mx}%"></div></span></button>`).join('');
  const o = O[MIS_I];
  byId('mis-detail').innerHTML = `<h3>${o.label} called ${o.pred}: ${o.n} times</h3><p>${reason(o.label, o.pred)}</p>${o.pics.map(pairRow).join('') || '<p>No sharp example of this mix-up.</p>'}`;
  byId('mis-list-t').textContent = `The ${O.length} biggest mix-ups: ${O.reduce((a, o) => a + o.n, 0)} of the ${W.wrong} mistakes`;
}
byId('mis-list').addEventListener('click', e => { const b = e.target.closest('button[data-i]'); if (!b) return; MIS_I = +b.dataset.i; drawMis(); });

/* ---------- DINOv2-B detector ---------- */
function drawDet() {
  const T = DET;
  if (!T) { byId('det-kpis').innerHTML = '<div class="kpi"><div class="k">Status</div><div class="v t">not here yet</div><div class="f">run second_detector.py once the checkpoint is back</div></div>'; return; }
  const m = T.meta, found = T.found / T.n, right = T.right / T.n, hiAcc = T.hi.right / Math.max(1, T.hi.n), NEW = (m.format || '').includes('fcos'), cf = m.config || {};
  const cap = m.image_size, grid = Math.round(cap / m.patch_size);
  byId('det-kpis').innerHTML = [['Model', NEW ? 'DINOv2-B + anchor-free head' : 'DINOv2-B + Faster R-CNN', `${m.params_m} M parameters, ${m.backbone_params_m} M in the backbone`, true],
    ['Input', NEW ? `whole photo, long side ${cap} px` : `whole photo, ${cap} × ${cap}`, NEW ? `shape kept · ${m.class_names.length} device types` : `${m.class_names.length} device types`, true],
    ['Training', `best epoch ${m.epoch}`, NEW && m.valid ? `valid mAP50-95 ${m.valid.mAP.toFixed(3)} · mAP50 ${m.valid.mAP50.toFixed(3)}` : `so far; best valid loss ${(m.best_val_loss || 0).toFixed(3)}`, false],
    ['Test', pc(right), `of ${fmt(T.n)} devices named right · macro-F1 ${pc(T.macro_f1)}`, false]]
    .map(([k, v, f, t]) => `<div class="kpi"><div class="k">${k}</div><div class="v${t ? ' t' : ''}">${v}</div><div class="f">${f}</div></div>`).join('');
  byId('det-why').innerHTML = '<table class="pick5"><thead><tr><th class="l">Part</th><th class="l">What it does</th><th class="l">Why it is the second opinion</th></tr></thead><tbody>' + [
    ['DINOv2-B backbone', `A vision transformer (ViT-B/14) trained by self-supervision on 142 million images; it turns the photo into a grid of 14-px patches${NEW ? '; the last 4 layers are used together' : ''}.`, 'Our research ranked it second only to ConvNeXt-Tiny, and strongest when a type has few examples. A different architecture makes different mistakes.'],
    NEW ? ['Anchor-free head, stride 7', 'The patch features are upsampled 2× to a 7-px grid; every grid point predicts a type, a box and how central it is (FCOS style).', 'A finer grid keeps thin 1U devices; it sees the whole rack, so height, neighbours and position are visible to it.']
        : ['Faster R-CNN head', 'Proposes boxes on that grid (5 sizes × 3 shapes per cell), then names each box one of 12 types.', 'It sees the whole rack, not one crop: height, neighbours and position are visible to it and invisible to the classifier.'],
    ['Agreement rule', 'For each device the classifier names, the detector box that overlaps it most gives a second name. Same name: answered. Different: Other.', `On the ${fmt(T.hi.n)} test crops: ${T.agreement ? `${T.agreement.rules.find(r => r.name === T.agreement.chosen).wrong} wrong instead of ${T.agreement.rules.find(r => r.name === 'model 1 + Other').wrong}` : 'see Approaches'}. The classifier stays the answer; the detector only vetoes.`]
  ].map(([a, b, c]) => `<tr><td>${a}</td><td>${b}</td><td>${c}</td></tr>`).join('') + '</tbody></table>';
  const card = (n, t, vis, how, why) => `<div class="stp"><div class="nm"><i>STEP ${n}</i><b>${t}</b></div><div class="vis">${vis}</div><p><b>How.</b> ${how}</p><p><b>Why.</b> ${why}</p></div>`;
  const rf = cf.repeat_factors ? Object.entries(cf.repeat_factors).filter(([, v]) => v > 1).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => `${k} ${v}×`).join(', ') : '';
  byId('det-steps').innerHTML = (NEW ? [
    card(1, 'Backbone', `<div class="tl"><span><b>142 M images</b>self-supervised</span><span class="ar">→</span><span><b>DINOv2-B</b>${m.backbone_params_m} M weights</span><span class="ar">→</span><span><b>Last 4 layers</b>to a 7-px grid</span></div>`,
      `Start from DINOv2-B as published; learning rate ${cf.lr_backbone} for the backbone, lower for its early layers (layer decay ${cf.layer_decay}), ${cf.lr_head} for the head.`, 'It already knows shapes and textures; a low rate keeps that knowledge while the head learns devices.'),
    card(2, 'Input', `<div class="tl"><span><b>Rack photo</b>any size</span><span class="ar">→</span><span><b>long side ${cap} px</b>shape kept, padded to 14</span></div>`,
      `Each photo keeps its shape; photos larger than ${cap} px on the long side are shrunk to it. Trained with this cap (the script default is 1344).`, `Shape kept, so devices are not stretched. The cap of ${cap} px fits an 8 GB GPU, but makes small devices only a few pixels tall (below).`),
    card(3, 'Training', `<div class="tl"><span><b>AdamW</b>wd ${cf.weight_decay}</span><span class="ar">→</span><span><b>warm-up + cosine</b>${cf.epochs} epochs max</span><span class="ar">→</span><span><b>batch ${cf.batch_size} × ${cf.accum}</b>${cf.device}</span></div>`,
      `Left-right flip and colour jitter; photos with rare types shown more often (${rf}).`, 'The standard recipe; the cosine decay and rare-type sampling were missing in the first run.'),
    card(4, 'Choosing the best epoch', `<div class="tl"><span><b>Epoch ${m.epoch}</b>best of the run</span><span class="ar">→</span><span><b>valid mAP50-95 ${m.valid.mAP.toFixed(3)}</b>devices right ${pc(m.valid.accuracy)}</span></div>`,
      `After every epoch, scored on its valid photos; the epoch with the best mAP50-95 is kept, stopping after ${cf.patience} epochs without gain.`, 'Chosen by detection quality, not by loss. The loss per epoch comes with its log.csv.')]
  : [card(1, 'Backbone', `<div class="tl"><span><b>DINOv2-B</b>${m.backbone_params_m} M weights</span></div>`, 'Start from DINOv2-B as published.', 'It already knows shapes and textures.'),
     card(2, 'Input', `<div class="tl"><span><b>${cap} × ${cap}</b>squashed</span></div>`, `Each photo is resized to ${cap} × ${cap}.`, 'One fixed size.')]).join('');
  const Q = T.squash;
  if (Q) {
    byId('det-sq-t').textContent = NEW ? `What Model 2 sees: the photo shrunk to a long side of ${cap} px` : `What Model 2 sees: the photo squashed to ${cap} × ${cap}`;
    byId('det-sq-s').textContent = NEW ? `A real test photo. The shape is kept, but the long side is only ${cap} px, so every device becomes small.` : `A real test photo. The whole photo is resized to ${cap} × ${cap}, so a tall rack is squeezed and every device becomes small and stretched sideways.`;
    byId('det-sq').innerHTML = `<figure><img src="data:image/jpeg;base64,${Q.orig}" alt="The photo"><figcaption><b>The photo</b>${fmt(Q.w)} × ${fmt(Q.h)} px</figcaption></figure>
      <figure><img src="data:image/jpeg;base64,${Q.sq}" alt="What Model 2 sees"><figcaption><b>What Model 2 sees</b>${Q.sw} × ${Q.sh} px${NEW ? '' : ', squashed'}; the orange box is the ${Q.label}</figcaption></figure>
      <figure><img class="px" src="data:image/jpeg;base64,${Q.dev2}" alt="The device as Model 2 sees it"><figcaption><b>That ${Q.label} for Model 2</b>${Q.w2} × ${Q.h2} px, about ${(Q.h2 / m.patch_size).toFixed(1)} patches tall (zoomed in)</figcaption></figure>
      <figure><img class="px" src="data:image/jpeg;base64,${Q.dev1}" alt="The device as Model 1 sees it"><figcaption><b>The same ${Q.label} for Model 1</b>${Q.w1} × ${Q.h1} px inside its 512 × 128 box, shape kept</figcaption></figure>`;
  }
  byId('det-curve-s').textContent = T.curve ? 'Validation score after each epoch; the ring marks the epoch in use.' : 'Not here yet: the score per epoch comes with log.csv from the run folder on the other machine.';
  byId('det-curve').innerHTML = T.curve ? '' : `<div class="noimg" style="height:160px;display:grid;place-items:center;border:1px dashed var(--base);border-radius:8px;color:var(--muted);font-size:13px">curve pending · best epoch ${m.epoch}</div>`;
  byId('det-test-s').textContent = `On the ${fmt(T.n)} labelled devices in the ${fmt(T.photos)} test photos neither model trained on. Bars to scale, 0 to 100 %.`;
  byId('det-test').innerHTML = [['Devices found', found, `a box overlaps the device · ${fmt(T.found)} of ${fmt(T.n)}`], ['Named right', right, `right type on all devices · ${fmt(T.right)} of ${fmt(T.n)}`],
    ['Named right when found', T.right / Math.max(1, T.found), 'right type among the devices it found'], ['Macro-F1', T.macro_f1, 'every type counts the same'],
    [`On the classifier's ${fmt(T.hi.n)} crops`, hiAcc, `${fmt(T.hi.right)} right, ${T.hi.none} with no box`]].map(([n, v, d]) =>
    `<div class="rr" style="--c1:210px"><div class="lb"><b>${n}</b><span>${d}</span></div><div class="tk"><div style="width:${100 * v}%;background:var(--s1)"></div></div><span class="v">${pc(v)}</span></div>`).join('');
  byId('det-classes').innerHTML = '<thead><tr><th class="l">Type</th><th>Devices</th><th>Found</th><th>Named right</th><th>Precision</th><th>Recall</th><th>F1</th></tr></thead><tbody>' +
    T.per_class.slice().sort((a, b) => a.f1 - b.f1).map(c => `<tr><td>${c.name}</td><td>${fmt(c.n)}</td><td>${fmt(c.found)}</td><td>${fmt(c.right)}</td><td>${pc(c.precision)}</td><td>${pc(c.recall)}</td><td>${pc(c.f1)}</td></tr>`).join('') + '</tbody>';
  const C = T.confusion;
  byId('det-cm').innerHTML = `<thead><tr><th class="l">Real ↓ · named →</th>${C.cols.map(c => `<th>${c}</th>`).join('')}</tr></thead><tbody>` +
    C.rows.map((r, i) => `<tr><td>${r}</td>${C.cells[i].map((v, j) => `<td style="${C.cols[j] === r ? 'font-weight:600;background:color-mix(in srgb,#3f9b57 14%,transparent)' : v ? 'background:color-mix(in srgb,#d64545 ' + Math.min(40, 6 + v) + '%,transparent)' : ''}">${v || ''}</td>`).join('')}</tr>`).join('') + '</tbody>';
}

/* ---------- approaches (all of them) ---------- */
function drawApr() {
  const A = APR; if (!A) return;
  const G = A.agree, m1 = G['model 1'], m2 = G['model 2'], o1 = G['model 1 + Other'], o2 = G['model 2 + Other'], bo = G[A.chosen], t = A.test, H = A.how;
  const acc = r => r.right / r.n;
  byId('apr-kpis').innerHTML = [['Test data', fmt(t.n), `device crops from ${fmt(t.photos)} photos, never used in training`],
    ['Model 1 · ConvNeXt-Tiny', `${pc(acc(m1))} → ${pc(o1.answers_right)}`, 'every crop → answers, with Other'],
    ['Model 2 · DINOv2-B', `${pc(acc(m2))} → ${pc(o2.answers_right)}`, 'every crop → answers, with Other'],
    ['Model 1 + Model 2', pc(bo.answers_right), `of answers right · ${bo.wrong} wrong, ${bo.other} Other`]]
    .map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('');
  byId('apr-data-s').textContent = `The same ${fmt(t.n)} device crops for both models: every labelled device in the ${fmt(t.photos)} test photos of 1 MP or more that neither model trained on. Neither model chose anything on them; the labels are the ground truth with ${HAND.other.test_label_fixes || 0} corrections. By type:`;
  byId('apr-data').innerHTML = t.types.map(([c, n]) => `<span class="chipfact">${c} <b>${fmt(n)}</b></span>`).join('');
  const C = {r: '#3f9b57', or: '#efcf8f', ow: '#e0a03a', w: '#d64545'};
  const seg = (v, c, label, dark) => v ? `<div style="flex:${v};background:${c};${dark ? 'color:var(--ink)' : ''}" title="${label}: ${v}">${v >= 100 ? fmt(v) : ''}</div>` : '';
  const sw = c => `<i class="sw" style="background:${c}"></i>`;
  const none = A.detector ? A.detector.hi.none : 0;
  byId('apr-1').innerHTML = [['Model 1 · ConvNeXt-Tiny', 'names the cut-out device crop', m1, ''], ['Model 2 · DINOv2-B detector', 'finds and names devices in the whole photo', m2, none ? `${none} of them not found at all` : '']]
    .map(([n, d, r, note]) => `<div class="mcd${r === m2 && m2.right > m1.right ? ' top' : ''}"><h3>${n}</h3><p class="sub">${d}</p><div class="big">${pc(acc(r))}<small>accuracy</small></div>
      <div class="bastack">${seg(r.right, C.r, 'right')}${seg(r.wrong, C.w, 'wrong')}</div>
      <dl><dt>${sw(C.r)}Right</dt><dd>${fmt(r.right)}</dd><dt>${sw(C.w)}Wrong</dt><dd>${r.wrong}</dd>${note ? `<dt class="in">${note}</dt><dd class="in"></dd>` : ''}</dl></div>`).join('');
  byId('apr-2-lg').innerHTML = [[C.r, 'answered right'], [C.or, 'Other, answer would have been right'], [C.ow, 'Other, answer would have been wrong'], [C.w, 'answered wrong']].map(([c, l]) => `<span><i style="background:${c}"></i>${l}</span>`).join('');
  byId('apr-2').innerHTML = [['Model 1 + Other', 'Other when the top two scores are close, or the crop looks unlike training.', o1],
    ['Model 2 + Other', `Other when no box is found, or its score is below ${A.cut2 != null ? A.cut2.toFixed(2) : '-'} (set on the valid photos so Model 2 says Other as often as Model 1).`, o2],
    ['Model 1 + Model 2', 'The answer goes out only when both name the same type; otherwise Other.', bo]]
    .map(([n, d, r]) => `<div class="mcd${r === bo ? ' top' : ''}"><h3>${n}${r === bo ? '<span class="chip do">best</span>' : ''}</h3><p class="sub">${d}</p><div class="big">${pc(r.answers_right)}<small>of answers right</small></div>
      <div class="bastack">${seg(r.right, C.r, 'answered right')}${seg(r.other_right, C.or, 'Other, would have been right', true)}${seg(r.other_wrong, C.ow, 'Other, would have been wrong')}${seg(r.wrong, C.w, 'answered wrong')}</div>
      <dl><dt>${sw(C.r)}Answered right</dt><dd>${fmt(r.right)}</dd><dt>${sw(C.w)}Answered wrong</dt><dd>${r.wrong}</dd><dt>Other</dt><dd>${r.other}</dd>
      <dt class="in">${sw(C.or)}would have been right</dt><dd class="in">${r.other_right}</dd><dt class="in">${sw(C.ow)}would have been wrong</dt><dd class="in">${r.other_wrong}</dd></dl></div>`).join('');
  byId('apr-flow').innerHTML = `<div class="bx"><b>A device</b>in the rack photo</div><span>→</span>
    <div class="col"><div class="bx"><b>Model 1</b>names the cut-out crop</div><div class="bx"><b>Model 2</b>names its own box on the same device</div></div><span>→</span>
    <div class="bx q"><b>Same type?</b></div><span>→</span>
    <div class="col"><div class="bx ok"><b>Yes → answered</b>${fmt(H.agree)} crops: ${fmt(H.agree_right)} right, ${H.agree - H.agree_right} wrong</div><div class="bx oth"><b>No, or no box → Other</b>${H.dis} crops: ${H.dis_right} would have been right, ${H.dis - H.dis_right} would have been wrong</div></div>`;
  const cls = o => o.startsWith('answered') ? 'ok' : o.includes('avoided') ? 'av' : 'lo';
  byId('apr-ex').innerHTML = H.examples.map(e => `<div class="mxe"><img src="data:image/jpeg;base64,${e.img}" alt=""><dl><dt>Really</dt><dd>${e.label}</dd><dt>Model 1</dt><dd>${e.m1} · ${Math.round(100 * e.p1)} %</dd><dt>Model 2</dt><dd>${e.m2 === 'none' ? 'no box' : `${e.m2} · ${Math.round(100 * e.p2)} %`}</dd></dl><span class="res ${cls(e.out)}">${e.out}</span></div>`).join('');
  byId('apr-how-n').innerHTML = `<b>Why it works:</b> the two models are built differently and see different things, so they seldom make the same mistake: both were wrong the same way only ${H.agree - H.agree_right} times. In ${H.dis_m2_right} of the ${H.dis} disagreements, Model 2 had the right type.`;
  const L = A.left, mixes = Object.entries(L.reduce((o, q) => (o[`${q.label} → ${q.pred}`] = (o[`${q.label} → ${q.pred}`] || 0) + 1, o), {})).sort((a, b) => b[1] - a[1]);
  byId('apr-mis-flow').innerHTML = `<div class="bx wr"><b>${m1.wrong} wrong</b>Model 1 alone, answering every device</div><span>→</span>
    <div class="bx oth"><b>${o1.other_wrong} caught by its Other rule</b>${o1.wrong} still wrong with Model 1 + Other</div><span>→</span>
    <div class="bx oth"><b>${bo.other_wrong} caught when the models disagree</b>sent to Other: a person checks them</div><span>→</span>
    <div class="bx wr"><b>${bo.wrong} left</b>both models name the same wrong type</div>`;
  byId('apr-left-t').textContent = `The ${L.length} left: ${mixes.map(([k, n]) => `${n} × ${k}`).join(', ')}. ${H.left_racks} of them sit in ${H.left_rack_photos} rack photos with two or more of the same device.`;
  byId('apr-left').innerHTML = L.map(q => `<figure><img src="data:image/jpeg;base64,${q.img}" alt="">${q.soft ? '<span class="softtag">blurry</span>' : ''}<figcaption><b>${q.label}</b> → both said ${q.pred} · Model 1 ${Math.round(100 * q.p1)} %, Model 2 ${Math.round(100 * q.p2)} %</figcaption></figure>`).join('');
  const ck = (ok, t, d, st) => `<div class="ck"><span class="ic ${ok ? 'y' : 't'}">${ok ? '✓' : '!'}</span><div><b>${t}</b><span>${d}</span></div><span class="st"><span class="chip ${ok ? 'do' : 'dq'}">${st}</span></span></div>`;
  byId('apr-check').innerHTML = [
    ck(true, 'Other goes to a person, never into the inventory', 'A device the models are not sure about is shown as Other for someone to confirm. A missing answer is fixed in seconds; a wrong one stays hidden. This is the standard way to use a model you cannot make perfect.', 'right way'),
    ck(true, 'Every cut-off was set on the valid photos', `Model 1's Other rule and Model 2's score cut-off (${A.cut2 != null ? A.cut2.toFixed(2) : '-'}) were set on the valid photos. The ${fmt(t.n)} test crops were only scored, never used to choose a setting.`, 'right way'),
    ck(true, 'The agreement rule has nothing to tune', 'Same type: answered. Different: Other. With no setting to adjust, it cannot be fitted to the test crops.', 'right way'),
    ck(false, 'Model 2 must not have trained on the test photos', `If any of the ${fmt(t.photos)} test photos were in Model 2's training set, its numbers here are too high. To confirm with the team, then re-score if needed.`, 'to confirm'),
    ck(false, `The last ${bo.wrong} cannot be caught by any rule`, `Both models are sure and agree, so no score or vote can flag them. Most are racks full of one look-alike model; some labels may be wrong. They need the network team's label check, or the rack context (unit height, neighbours).`, 'next step'),
    ck(false, 'The web app still uses Model 1 + Other', 'Model 1 + Model 2 is measured here, not yet in the pipeline. It goes in once the final Model 2 is back and the two points above are confirmed.', 'to do')
  ].join('');
  byId('apr-next').innerHTML = [['Final Model 2 checkpoint', 'Round 11 is in use here; the final round replaces it and every number above is rebuilt.'],
    ['Model 2 training photos', 'To confirm they exclude the test photos; if not, Model 2 is re-scored on photos it never saw.'],
    ['Trust Model 2 when it is very sure', `When the two disagree, give Model 2's answer if its score is high, instead of Other. It had the right type in ${H.dis_m2_right} of the ${H.dis} disagreements.`],
    ['Bigger box for Model 1 (1024 × 256)', 'Needs a larger GPU: 3 hours or more per run on this Mac.'],
    ['Rack context and text', 'Unit height, neighbours and the text on the front, for the look-alike pairs.']]
    .map(([a, b]) => `<div class="rr nxr" style="grid-template-columns:260px minmax(0,1fr) 90px"><div class="lb"><b>${a}</b></div><span style="font-size:12.5px;color:var(--ink2)">${b}</span><span style="text-align:right"><span class="chip nx">Next</span></span></div>`).join('');
}

/* ---------- overview (start page) ---------- */
/* ---------- overview (start page) ---------- */
function drawOv() {
  const M = HAND.other && HAND.other.metrics, T = HAND.other && HAND.other.splits && HAND.other.splits.test, W = WHY, U = UNK;
  if (!M) return;
  const ta = T && T.tally_after, tb = T && T.tally_before, A = t => t.right / Math.max(1, t.right + t.wrong), CL = EDGE && EDGE.clear;
  byId('ov-kpis').innerHTML = [
    ['Accuracy', pc(M.accuracy), `${fmt(Math.round(M.accuracy * M.n))} of ${fmt(M.n)} test crops right`],
    ['With Other', ta ? pc(A(ta)) : '-', ta ? `of its answers right; ${ta.wrong} wrong, ${ta.other} answered Other` : ''],
    ['Without blurry or tilted', CL ? pc(A(CL.clear.o_right ? {right: CL.clear.o_right, wrong: CL.clear.o_wrong} : {right: 0, wrong: 1})) : '-', CL ? `of answers right with Other on ${fmt(CL.clear.n)} clear crops` : ''],
    ['Macro-F1', pc(M.macro_f1), 'every type counts the same, large or small']
  ].map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('');
  const D = DS.hi, R0 = HAND.results;
  byId('ov-facts').innerHTML = [['Model', 'ConvNeXt-Tiny'], ['Input', '512 × 128 box'], ['Device types', `${M.classes.length} + Other`],
    ['Training crops', '__NTRAIN__'], ['Test crops', '__NTEST__'], ['Rack photos', `__NHIPHOTOS__ of 1 MP or more`], R0 && ['Best epoch', `${R0.best_epoch} of ${R0.epochs_run}`]
  ].filter(Boolean).map(([k, v]) => `<span class="chipfact">${k} <b>${v}</b></span>`).join('');
  byId('ov-flow').innerHTML = [['Rack photo', 'as taken'], ['Device outline', 'from device detection'], ['Cut out', 'the device alone'], ['512 × 128 box', 'fitted, same shape'], ['ConvNeXt-Tiny', `scores ${M.classes.length} types`], ['Answer', `clear winner, or Other`]]
    .map(([a, b], i, arr) => `<div class="st${i === arr.length - 1 ? ' hl' : ''}"><b>${a}</b><span>${b}</span></div>${i < arr.length - 1 ? '<span class="ar">→</span>' : ''}`).join('');
  if (ta && tb) {
    byId('ov-res-t').textContent = `${fmt(M.n)} test crops: ${tb.wrong} wrong answers, ${ta.wrong} with Other`;
    const seg = t => `<div class="bastack" style="height:26px"><div style="flex:${t.right};background:#3f9b57" title="right">${fmt(t.right)} right</div>${t.other ? `<div style="flex:${t.other};background:#e0a03a" title="Other">${t.other} Other</div>` : ''}<div style="flex:${t.wrong};background:#d64545" title="wrong">${t.wrong}</div></div>`;
    byId('ov-res').innerHTML = [['Always answers', `${pc(A(tb))} right`, tb], ['With Other', `${pc(A(ta))} of its answers right; a close call is answered Other instead of guessed`, ta]]
      .map(([t, d, q]) => `<div class="rr" style="--c1:170px;grid-template-columns:170px minmax(0,1fr)"><div class="lb"><b>${t}</b><span>${d}</span></div>${seg(q)}</div>`).join('');
    byId('ov-scores').innerHTML = '<thead><tr><th class="l">Score</th><th>Value</th><th class="l">Meaning</th></tr></thead><tbody>' + [
      ['Accuracy', pc(M.accuracy), `${fmt(tb.right)} of ${fmt(M.n)} test crops right, Other off`],
      ['Answers right with Other', pc(A(ta)), `${fmt(ta.right)} right, ${ta.wrong} wrong; ${ta.other} crops answered Other and not counted`],
      ['Mistakes caught by Other', `${ta.caught} of ${tb.wrong}`, 'wrong answers that became Other'],
      ['Right answers given up', `${ta.lost} of ${fmt(tb.right)}`, 'right answers that became Other'],
      ['Top-2 accuracy', pc(M.top2_accuracy), 'the right type is first or second'],
      ['Balanced accuracy', pc(M.balanced_accuracy), 'mean of the per-type shares right'],
      ['Macro-F1', pc(M.macro_f1), 'precision and recall, every type weighted the same'],
      ['Cohen’s kappa', M.kappa.toFixed(3), 'agreement with ground truth beyond chance (1 = perfect)']
    ].map(([a, b, c]) => `<tr><td><b>${a}</b></td><td>${b}</td><td>${c}</td></tr>`).join('') + '</tbody>';
  }
  if (U && U.how) byId('ov-how').innerHTML = howRow(U.how[0], 'm1') + howRow(U.how[1], 'm1') + ruleNote();

  const fd = (t, d, bars, k, b) => { const mx = Math.max(...bars.map(x => x[1])); return `<div class="fd"><div><b>${t}</b><span>${d}</span></div><div class="fb">${bars.map(([l, v, sh, hi]) => `<div><span>${l}</span><i><em style="width:${100 * v / mx}%" class="${hi ? 'hi' : ''}"></em></i><b>${sh}</b></div>`).join('')}</div><button data-go="${k}">${b} →</button></div>`; };
  const look = W ? W.pairs.reduce((a, p) => a + p.n, 0) : 0, half = RESO && RESO.resolution_test.find(t => t.factor === 0.5), full = RESO && RESO.resolution_test[0];
  const SN = {darker: ['Darkness', 'given half the light'], glare: ['Glare', 'made much brighter'], blurred: ['Blur', 'put out of focus'], tilted: ['Tilt', 'tilted 8 degrees'], 'cut off': ['Cutting off', 'a third outside the photo'], covered: ['Covering', 'a third hidden']};
  const ST = EDGE && EDGE.stress && (() => { const b = EDGE.stress.find(x => x.name === 'normal'), w = EDGE.stress.filter(x => x.name !== 'normal').sort((a, c) => c.wrong_answers - a.wrong_answers)[0]; return b && w && {base: b, worst: w, name: SN[w.name][0], how: SN[w.name][1]}; })();
  const WR = EDGE && EDGE.without_racks && EDGE.without_racks[EDGE.without_racks.length - 1], nt = CL && CL.groups[1][1], tilt = CL && {n: CL.all.n - nt.n, right: CL.all.right - nt.right};
  byId('ov-find').innerHTML = [
    W && fd('Most mistakes are look-alikes', `${look} of the ${W.wrong} mistakes left with Other on are one type called the type it resembles; ${W.pairs[0].a} and ${W.pairs[0].b} is the biggest pair.`,
      [['Look-alikes', look, `${look}`, true], ['Other reasons', W.wrong - look, `${W.wrong - look}`]], 'mis:convnext_tiny', 'Misclassified'),
    ta && tb && fd('Other turns close calls into "unknown"', `When the top two scores are close, the answer is Other instead of a guess: ${tb.wrong} wrong answers become ${ta.wrong}, at the cost of ${ta.other} crops answered Other.`,
      [['Always answers', tb.wrong, `${tb.wrong} wrong`], ['With Other', ta.wrong, `${ta.wrong} wrong`, true]], 'unk:convnext_tiny', 'Unknown'),
    WR && fd(`${WR.k} racks hold ${WR.mistakes} of the ${EDGE.wrong} mistakes`, `Racks full of one look-alike device repeat the same mistake on every copy. Without those ${WR.k} racks: ${pc(WR.right / WR.n)} right, ${pc(WR.o_right / (WR.o_right + WR.o_wrong))} of answers with Other.`,
      [['All racks', M.accuracy, pc(M.accuracy)], [`Without ${WR.k}`, WR.right / WR.n, pc(WR.right / WR.n), true]], 'why:convnext_tiny', 'Edge cases'),
    CL && fd('Tilt costs, blur does not', `Devices showing under half their front are answered wrong far more often; sharpness makes no difference.`,
      [[`${tilt.n} tilted`, tilt.right / tilt.n, pc(tilt.right / tilt.n)], [`${fmt(nt.n)} others`, nt.right / nt.n, pc(nt.right / nt.n), true]], 'why:convnext_tiny', 'Edge cases'),
    half && fd('Half the detail barely matters', `Macro-F1 drops ${(100 * (full.macro_f1 - half.macro_f1)).toFixed(1)} points when every test crop is shown at half resolution: the 512 × 128 box keeps what matters.`,
      [['512 × 128', full.macro_f1, pc(full.macro_f1), true], ['Half', half.macro_f1, pc(half.macro_f1)]], 'res:convnext_tiny', 'Resolution'),
    ST && fd(`${ST.name} breaks it most`, `Every test crop ${ST.how}, with Other on; the stress test tries 6 ways and this one hurts most.`,
      [['As photographed', ST.base.wrong_answers, `${ST.base.wrong_answers} wrong`], [ST.name, ST.worst.wrong_answers, `${ST.worst.wrong_answers} wrong`, true]], 'why:convnext_tiny', 'Edge cases')
  ].filter(Boolean).join('');
  const C = M.classes.map(c => ({c, a: M.per_class[c].correct / M.per_class[c].support, n: M.per_class[c].support})).sort((a, b) => b.a - a.a), weak = C.filter(x => x.a < .9).map(x => x.c);
  byId('ov-can-t').textContent = `Reliable on ${C.length - weak.length} of ${C.length} types`;
  byId('ov-can').innerHTML = C.map(x => `<div class="rr" style="--c1:130px"><div class="lb"><b>${x.c}</b><span>${fmt(x.n)} test crops</span></div><div class="tk"><div style="width:${100 * x.a}%;background:${VCOL(x.a)}"></div></div><span class="v">${pc(x.a)}</span></div>`).join('');
  byId('ov-next').innerHTML = [['Use the rack around the device', 'Height in units and neighbours tell storage units from servers.', 'nx', 'Next'],
    ['Read the text on the device', 'Model names tell firewalls from switches.', 'nx', 'Next'],
    ['A second model\'s opinion', 'DINOv2-B is training on another machine; the answer is given only when both models agree, otherwise Other.', 'nx', 'Next'],
    ['A bigger box (1024 × 256)', 'Needs a larger GPU to test.', 'nx', 'Next'],
    ['More training on the same data', 'Tried several ways: no gain.', 'tr', 'Tried']].map(([t, d, c, l]) => `<div class="rr nxr" style="grid-template-columns:260px minmax(0,1fr) 90px"><div class="lb"><b>${t}</b></div><span style="font-size:12.5px;color:var(--ink2)">${d}</span><span style="text-align:right"><span class="chip ${c}">${l}</span></span></div>`).join('');
}
document.addEventListener('click', e => { const b = e.target.closest('button[data-go]'); if (b) go(b.dataset.go); });

/* ---------- crop resolution ---------- */
function drawRes() {
  const R = RESO; if (!R) return;
  const T = R.resolution_test, full = T[0], half = T.find(t => t.factor === 0.5), SD = R.shrink, s0 = SD[0], F = R.fit;
  const CC = R.canvas_compare, b0 = CC && CC.find(c => c.canvas === '512x128');
  const win = CC && b0 && CC.filter(c => c.p != null && c.p < .05 && c.macro_f1 > b0.macro_f1).sort((a, b) => b.macro_f1 - a.macro_f1)[0];
  byId('res-kpis').innerHTML = [
    ['Box size', '512 × 128', 'every crop is fitted into it'],
    ['Score today', pc(full.macro_f1), `macro-F1 on ${fmt(R.n)} test crops`],
    ['Bigger box', !b0 ? 'not tested' : win ? win.canvas.replace('x', ' × ') : 'no gain', !b0 ? 'needs a larger GPU' : win ? 'scores higher' : 'not better by more than chance']
  ].map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('');
  byId('res-fit').innerHTML = `<figure><img src="data:image/jpeg;base64,${F.orig}" alt=""><figcaption><b>From the photo</b> · ${fmt(F.w)} × ${fmt(F.h)} px</figcaption></figure><div class="arr">→</div>
    <figure><img src="data:image/jpeg;base64,${F.view}" alt=""><figcaption><b>In the 512 × 128 box</b> · ${F.cw} × ${F.ch} px, same shape</figcaption></figure>
    <figure class="sq"><img src="data:image/jpeg;base64,${R.square}" alt=""><figcaption><b>A square box</b> would leave it tiny: devices are about ${R.median_aspect.toFixed(0)}× wider than tall</figcaption></figure>`;
  byId('res-small-sub').textContent = `The same device in each box size, zoomed in. Bar: macro-F1 on all ${fmt(R.n)} test crops, to scale.`;
  byId('res-small').innerHTML = R.strip.map(t => `<div class="rr${t.factor === 1 ? ' now' : ''}" style="--c1:150px"><img src="data:image/jpeg;base64,${t.img}" alt=""><div class="lb"><b>${t.canvas[0]} × ${t.canvas[1]}${t.factor === 1 ? ' · today' : ''}</b><div class="tk"><div style="width:${100 * t.macro_f1}%;background:${t.factor === 1 ? 'var(--s1)' : 'var(--base)'}"></div></div></div><span class="v">${pc(t.macro_f1)}</span></div>`).join('');
  byId('res-lost-sub').textContent = 'Test crops sorted by how much they had to be made smaller to fit the box, with the share the model gets right. If making them smaller caused mistakes, the top row would be the worst.';
  byId('res-lost').innerHTML = SD.map(b => `<div class="rr" style="--c1:230px"><div class="lb"><b>${b.name.split(': ')[0][0].toUpperCase() + b.name.split(': ')[0].slice(1)}</b><span>${b.name.split(': ')[1]} · ${b.n} crops</span></div><div class="tk"><div style="width:${100 * (1 - b.wrong / b.n)}%;background:#3f9b57"></div></div><span class="v">${pc(1 - b.wrong / b.n)}</span></div>`).join('');
  byId('res-lost-note').textContent = `It is the best: ${s0.n - s0.wrong} of ${s0.n} right. The most mistakes are in the small crops: they had few pixels in the photo to begin with, and resizing cannot add pixels the photo never had.`;
  byId('res-big-sub').textContent = !b0 ? 'Not tested yet. Training at 1024 × 256 on this Mac takes about 3 hours or more; it needs a machine with a larger GPU. Drawn to scale.'
    : win ? `${win.canvas.replace('x', ' × ')} scores higher by more than chance. Drawn to scale.` : 'Neither bigger box beats 512 × 128 by more than chance, and both need more compute. Drawn to scale.';
  const res = k => CC && CC.find(x => x.canvas === k), note = {'512x128': 'today', '1024x128': 'twice as wide', '1024x256': 'twice as big each way'};
  byId('res-big').innerHTML = [[512, 128], [1024, 128], [1024, 256]].map(([w, h]) => { const k = `${w}x${h}`, r = res(k);
    return `<div class="bx"><div class="fr" style="width:${Math.round(w * .26)}px;height:${Math.round(h * .26)}px"></div><b>${w} × ${h}</b><span>${note[k]}</span>${r ? `<span><b>${pc(r.macro_f1)}</b> macro-F1${b0 && r !== b0 ? ` · ${(r.secs_per_epoch / b0.secs_per_epoch).toFixed(1)}× compute` : ''}</span>` : k === '512x128' ? `<span><b>${pc(full.macro_f1)}</b> macro-F1</span>` : '<span class="chipst">not tested yet</span>'}</div>`; }).join('');
  byId('res-shrink').innerHTML = '<thead><tr><th class="l">Group</th><th>Test crops</th><th>Wrong</th><th>Right</th></tr></thead><tbody>' +
    SD.map(b => `<tr><td><b>${b.name}</b></td><td>${b.n}</td><td>${b.wrong}</td><td>${pc(1 - b.wrong / b.n)}</td></tr>`).join('') + '</tbody>';
  byId('res-classes').innerHTML = '<thead><tr><th class="l">Class</th><th>Test crops</th><th>Right, full detail</th><th>Right, half detail</th></tr></thead><tbody>' +
    Object.entries(R.per_class).sort((a, b) => a[1].acc - b[1].acc).map(([c, q]) => `<tr><td><b>${c}</b></td><td>${q.n}</td><td>${pc(full.per_class[c])}</td><td>${pc(half.per_class[c])}</td></tr>`).join('') + '</tbody>';
  byId('res-canvas').innerHTML = !CC ? '' : '<thead><tr><th class="l">Box</th><th>Accuracy</th><th>Macro-F1</th><th>Fixed / broke vs 512 × 128</th><th>Chance (p)</th><th>Compute</th></tr></thead><tbody>' +
    CC.map(c => `<tr><td><b>${c.canvas.replace('x', ' × ')}</b></td><td>${pc(c.acc)}</td><td>${pc(c.macro_f1)}</td><td>${c.fixed == null ? '-' : `${c.fixed} / ${c.broke}`}</td><td>${c.p == null ? '-' : c.p.toFixed(3)}</td><td>${b0 ? (c.secs_per_epoch / b0.secs_per_epoch).toFixed(1) + '×' : '-'}</td></tr>`).join('') + '</tbody>';
}

/* ---------- unknown answers ---------- */
function drawUnk() {
  const U = UNKS[SUB]; if (!U) return;
  drawOthBA(U);
  byId('unk-how-t').textContent = SUBTXT[SUB].how; byId('unk-how-s').textContent = SUBTXT[SUB].hows;
  byId('unk-how').innerHTML = howRow(U.how[0], SUB) + howRow(U.how[1], SUB);
  byId('unk-how-note').innerHTML = ruleFor(SUB);
  const C = U.per_class.slice().sort((a, b) => b.other / b.n - a.other / a.n), mx = C[0].other / C[0].n;
  byId('unk-cl-t').textContent = C.length > 1 ? `${C[0].name} and ${C[1].name} become Other most often` : 'Device types answered Other';
  byId('unk-classes').innerHTML = C.slice(0, 5).map(c => `<div class="rr" style="--c1:150px"><div class="lb"><b>${c.name}</b><span>${c.other} of ${c.n} crops</span></div><div class="tk"><div style="width:${100 * c.other / c.n / mx}%;background:var(--base)"></div></div><span class="v">${pc(c.other / c.n)}</span></div>`).join('');
  byId('unk-table').innerHTML = '<thead><tr><th class="l">Device type</th><th>Test crops</th><th>Answered Other</th><th>Would have been wrong</th><th>Would have been right</th><th class="l">Instead of</th></tr></thead><tbody>' +
    C.map(c => `<tr><td><b>${c.name}</b></td><td>${c.n}</td><td>${c.other}</td><td>${c.would_wrong}</td><td>${c.other - c.would_wrong}</td><td style="text-align:left">${c.top.map(([k, v]) => `${k} ${v}`).join(' · ') || '-'}</td></tr>`).join('') + '</tbody>';
  const NM = {'unsure': 'Weak scores', 'unlike any training crop': 'Unlike any training crop', 'close call': 'Top two too close'}, rm = Math.max(...U.reasons.map(r => r[1]));
  byId('unk-reasons').innerHTML = U.reasons.map(([r, n]) => `<div class="rr" style="--c1:170px"><div class="lb"><b>${NM[r] || r}</b><span>${n} crops</span></div><div class="tk"><div style="width:${100 * n / rm}%;background:var(--base)"></div></div><span class="v">${pc(n / U.other)}</span></div>`).join('');
  const tm = U.torn.length ? U.torn[0][1] : 1;
  byId('unk-torn').innerHTML = U.torn.map(([t, n]) => `<div class="rr" style="--c1:170px"><div class="lb"><b>${t}</b><span>${n} crops</span></div><div class="tk"><div style="width:${100 * n / tm}%;background:var(--base)"></div></div><span class="v">${pc(n / U.other)}</span></div>`).join('');
  byId('unk-torn-h').textContent = SUBTXT[SUB].torn;
  byId('unk-torn-note').textContent = SUB === 'm2' ? `Model 2's answer would have been right for ${U.in_top2} of the ${U.other} crops answered Other.` : `The real type is one of the two answers for ${U.in_top2} of the ${U.other} crops answered Other.`;
  byId('unk-ex').innerHTML = U.examples.map(q => `<figure><img src="data:image/jpeg;base64,${q.img}" alt=""><figcaption>really <b>${q.label}</b> · ${q.said.map(([n, v]) => `<b>${n}</b>${v == null ? '' : ` ${Math.round(v * 100)} %`}`).join(' · ')}</figcaption></figure>`).join('');
}

/* ---------- training code tab ---------- */
CODE.yolo = `from ultralytics import YOLO

# needs device_crops arranged as <root>/<split>/<Class>/*.jpg
model = YOLO("yolo11m-cls.pt")
model.train(data="device_crops_split", imgsz=384, epochs=30, batch=64)
metrics = model.val(split="test")`;
const PYKW = /\b(def|class|return|if|else|elif|for|in|not|and|or|is|None|True|False|import|from|as|with|while|break|continue|try|except|raise|lambda|yield|pass|global|assert|del)\b/g;
function hl(src) {
  const esc = t => t.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  return src.split('\n').map(line => {
    let out = '', i = 0;
    while (i < line.length) {
      const ch = line[i];
      if (ch === '#') { out += '<span class="c">' + esc(line.slice(i)) + '</span>'; break; }
      if (ch === '"' || ch === "'") {
        const q = line.startsWith(ch.repeat(3), i) ? ch.repeat(3) : ch;
        let j = line.indexOf(q, i + q.length); if (j < 0) j = line.length - q.length;
        out += '<span class="s">' + esc(line.slice(i, j + q.length)) + '</span>'; i = j + q.length; continue;
      }
      const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(line.slice(i));
      if (m) { const w = m[0]; const call = line[i + w.length] === '(';
        out += PYKW.test(w) && (PYKW.lastIndex = 0, true) ? '<span class="k">' + w + '</span>' : call ? '<span class="f">' + w + '</span>' : esc(w);
        PYKW.lastIndex = 0; i += w.length; continue; }
      const n = /^\d[\d._e-]*/.exec(line.slice(i));
      if (n) { out += '<span class="n">' + n[0] + '</span>'; i += n[0].length; continue; }
      out += esc(ch); i++;
    }
    return out;
  }).join('\n');
}
function dedent(t) { const ls = t.split('\n'), ind = Math.min(...ls.filter(l => l.trim()).map(l => l.match(/^ */)[0].length)); return ls.map(l => l.slice(ind)).join('\n'); }
let CODE_DONE = false;
function drawCode() {
  if (CODE_DONE) return; CODE_DONE = true;
  document.querySelectorAll('pre.code[data-code]').forEach(pre => {
    const [a, b] = pre.dataset.code.split('.'); const t = b ? CODE[a][b] : CODE[a];
    pre.innerHTML = hl(dedent(t || ''));
  });
}

/* ---------- crop viewer, shared by both sample grids ---------- */
let LB = null;
function openLB(items, i) { LB = {items, i}; paintLB(); byId('lb').hidden = false; byId('lb-close').focus(); }
function paintLB() {
  const it = LB.items[LB.i], im = byId('lb-img');
  im.onload = () => { const k = Math.min((Math.min(innerWidth, 1100) - 48) / im.naturalWidth, innerHeight * .68 / im.naturalHeight, 4); im.style.width = Math.round(im.naturalWidth * k) + 'px'; };
  im.src = it.src || 'data:image/jpeg;base64,' + it.b; im.alt = it.n ? it.n + ' crop' : '';
  byId('lb-cap').innerHTML = it.cap != null ? it.cap : `<b>${it.n}</b> · ${it.size} px · <code>device_crops/${it.file}</code>`;
}
function stepLB(d) { LB.i = (LB.i + d + LB.items.length) % LB.items.length; paintLB(); }
function closeLB() { byId('lb').hidden = true; LB = null; }
document.addEventListener('click', e => {   // every other picture on the page opens in the viewer too
  const img = e.target.closest('.panel img');
  if (!img || img.closest('button') || img.id === 'lb-img') return;
  const card = img.closest('.card') || img.closest('.panel'), all = [...card.querySelectorAll('img')].filter(x => !x.closest('button'));
  const cap = x => { const f = x.closest('figure'); const c = f && f.querySelector('figcaption'); return c ? c.innerHTML : (x.closest('.hrow, .rr, .pairrow') ? x.closest('.hrow, .rr, .pairrow').innerText.replace(/\s+/g, ' ').slice(0, 160) : ''); };
  openLB(all.map(x => ({src: x.src, cap: cap(x)})), all.indexOf(img));
});
byId('samples').addEventListener('click', e => {
  const b = e.target.closest('button[data-c]'); if (!b) return;
  const items = byCount.flatMap(c => D.thumbs[c.name].map(([b64, file, size], j) => ({n: c.name, j, b: b64, file, size})));
  openLB(items, items.findIndex(x => x.n === b.dataset.c && x.j === +b.dataset.j));
});
byId('r-samples').addEventListener('click', e => {
  const b = e.target.closest('button[data-r]'); if (!b) return;
  const items = REVROWS().flatMap(c => c.thumbs.map(([b64, file, size], j) => ({n: c.name, j, b: b64, file, size})));
  openLB(items, items.findIndex(x => x.n === b.dataset.r && x.j === +b.dataset.j));
});
byId('lb-prev').onclick = () => stepLB(-1);
byId('lb-next').onclick = () => stepLB(1);
byId('lb-close').onclick = closeLB;
byId('lb').addEventListener('click', e => { if (e.target.id === 'lb') closeLB(); });
addEventListener('keydown', e => { if (!LB) return; if (e.key === 'Escape') closeLB(); else if (e.key === 'ArrowRight') stepLB(1); else if (e.key === 'ArrowLeft') stepLB(-1); });

/* ---------- summary ---------- */
function drawEval() {
  const M = HAND.other && HAND.other.metrics;
  byId('eval-kpis').innerHTML = M ? [
    ['Macro-F1', pc(M.macro_f1), 'main score'], ['Accuracy', pc(M.accuracy), `${fmt(M.n)} test crops`], ['Top-2 accuracy', pc(M.top2_accuracy), 'right class in first two'], ["Cohen's kappa", M.kappa.toFixed(3), 'above 0.8 is very strong']
  ].map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('') : '';
}

/* ---------- sections ---------- */
let CUR = 'all';
const SECT = {
  ov: ['Device classification', 'Overview', 'What the device classifier does, how well it works, and what we found.'],
  clf: ['Model', 'ConvNeXt-Tiny classifier', 'The model in use: which one, how it was trained, and how it scores on the test crops.'],
  det: ['Model', 'DINOv2-B detector', 'The second model: finds and names every device in the rack photo. Trained by the team on another machine, scored here on the test photos.'],
  apr: ['Device classification', 'Approaches', 'Model 1 and Model 2 on the same test data: first answering every device, then with Other. Everything else we tried is in the dropdowns below.'],
  oth: ['Other devices', 'Approaches', 'How the classifier answers Other (unknown), for gear outside its 9 classes and for likely mistakes: the problem, the plan, and each approach in full.'],
};
function setHead(k) {
  let [c, t, l] = SECT[k] || ['Data', HEAD[k] ? HEAD[k][0] : '', HEAD[k] ? HEAD[k][1] : ''];
  const sub = k.split(':')[0], arch = k.split(':')[1], isR = ['run', 'eval', 'why', 'mis', 'res', 'unk'].includes(sub);
  if (isR) {
    const r = RUNS.find(x => x.arch === arch);
    [c, t, l] = [['why', 'mis', 'unk'].includes(sub) ? 'Results' : (sub === 'run' || sub === 'eval' ? 'Model · ' : 'Results · ') + (r ? r.label : ''), {run: 'Training run', eval: 'Evaluation', why: 'Edge cases', mis: 'Misclassified', res: 'Crop resolution', unk: 'Unknown answers'}[sub],
      {run: RUN_LEAD, eval: 'Every standard metric on the test split, what it means in plain words, and where the model goes wrong.',
       why: 'The cases that still go wrong after Other, why, and what breaks the model when photos get harder.',
       mis: 'Which device types the model mixes up, why, and real examples of each: the mistakes that stay wrong with Other on.',
       res: 'Every crop is fitted into a 512 × 128 box before the model sees it: how it fits, and whether another size does better.',
       unk: 'When the model is not sure, it answers Other (unknown) instead of guessing: how it decides, and what that changes.'}[sub]];
  }
  byId('ph-crumb').textContent = c; byId('ph-title').textContent = t; byId('ph-lead').textContent = l;
  const M = HAND.other && HAND.other.metrics;
  const TA = HAND.other && HAND.other.splits && HAND.other.splits.test && HAND.other.splits.test.tally_after;
  byId('topmeta').innerHTML = M ? `<span>${M.classes.length} classes</span><span>Accuracy <b>${pc(M.accuracy)}</b></span>${TA ? `<span>With Other <b>${pc(TA.right / (TA.right + TA.wrong))}</b> of answers right</span>` : ''}<span>Macro-F1 <b>${pc(M.macro_f1)}</b></span>` : '';
}
const HEAD = {all: ['All images', 'All __NPHOTOS__ photos of the seven exports, including 2,242 set aside as low resolution. Each device polygon is cut out as one crop.'],
              hi: ['High-resolution images', 'After the filter: only photos of 1 MP or more, meaning width × height of at least 1,000,000 pixels. __NHIPHOTOS__ photos pass; __NHIREMOVED__ of them were set aside for tiny rack units, but they pass the pixel filter.']};
function drawKpis() {
  const sp = D.split_photos;
  byId('dist-kpis').innerHTML = [
    ['Instances', fmt(D.total), 'One crop per polygon'],
    ['Images', fmt(D.photos), (CUR === 'all' ? 'with a device polygon, of __NPHOTOS__ · ' : 'of 1 MP or more · ') + `train ${fmt(sp.train)} · valid ${fmt(sp.valid)} · test ${fmt(sp.test)}`],
    ['Classes', C.length, `Largest ${big.name} · smallest ${small.name}`],
    ['Imbalance', Math.round(big.count / small.count) + '×', `${big.name} vs ${small.name} instances`]
  ].map(([k, v, f]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="f">${f}</div></div>`).join('');
}
function render() {
  drawSubtabs();
  if (CUR.startsWith('eval:')) { RUN_SEL = CUR.slice(5); drawEval(); drawRun(); drawMetrics(); }
  else if (CUR === 'all' || CUR === 'hi') { drawAll(); drawReview(); }
  else if (CUR === 'oth') drawOther();
  else if (CUR === 'ov') drawOv();
  else if (CUR === 'det') drawDet();
  else if (CUR === 'apr') drawApr();
  else if (CUR === 'clf') { RUN_SEL = 'convnext_tiny'; drawCode(); drawClfKpis(); drawSteps(); drawRun(); drawEval(); drawMetrics(); drawClfTest(); }
  else if (CUR.startsWith('run:')) { RUN_SEL = CUR.slice(4); drawRun(); }
  else if (CUR.startsWith('why:')) drawWhy();
  else if (CUR.startsWith('mis:')) drawMis();
  else if (CUR.startsWith('res:')) drawRes();
  else if (CUR.startsWith('unk:')) drawUnk();
}
function show(k) {
  k = {'crops:all': 'all', 'crops:hi': 'hi'}[k] || (/^(run|eval):/.test(k) ? 'clf' : k);
  CUR = k;
  const isD = k === 'all' || k === 'hi';
  byId('p-dist').hidden = !isD;
  for (const p of ['ov', 'clf', 'oth', 'det', 'apr']) byId('p-' + p).hidden = k !== p;
  byId('p-why').hidden = !k.startsWith('why:');
  byId('p-mis').hidden = !k.startsWith('mis:');
  byId('p-res').hidden = !k.startsWith('res:');
  byId('p-unk').hidden = !k.startsWith('unk:');
  document.querySelectorAll('.nav button').forEach(b => b.setAttribute('aria-selected', b.dataset.k === k));
  if (isD) {
    D = DS[k]; C = D.classes; byCount = [...C].sort((a, b) => b.count - a.count);
    big = byCount[0]; small = byCount[byCount.length - 1];
    drawKpis(); drawSummary();
  }
  render();
  setHead(k);
}
drawScorecards();
let rt; addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(render, 120); });
const HASH = {ov: '#overview', all: '#all', hi: '#hires', clf: '#model', det: '#detector', apr: '#approaches', oth: '#other'};
RUNS.forEach(r => { HASH['run:' + r.arch] = '#' + r.arch.replace(/_/g, '-'); HASH['eval:' + r.arch] = '#' + r.arch.replace(/_/g, '-') + '-evaluation'; HASH['why:' + r.arch] = '#' + r.arch.replace(/_/g, '-') + '-edge-cases'; HASH['res:' + r.arch] = '#' + r.arch.replace(/_/g, '-') + '-crop-resolution'; HASH['mis:' + r.arch] = '#' + r.arch.replace(/_/g, '-') + '-misclassified'; HASH['unk:' + r.arch] = '#' + r.arch.replace(/_/g, '-') + '-unknown-answers'; });
function go(k) { show(k); scrollTo({top: 0}); try { history.replaceState(null, '', HASH[k]); } catch (e) {} }
document.querySelectorAll('.nav button').forEach(b => b.addEventListener('click', () => go(b.dataset.k)));
byId('appills').addEventListener('click', e => { const b = e.target.closest('button[data-ap]'); if (b) pickAP(b.dataset.ap); });
document.querySelectorAll('details').forEach(d => d.addEventListener('toggle', () => { if (d.open) render(); }));
show(Object.keys(HASH).find(k => HASH[k] === location.hash) || {'#convnext-tiny-why-not-100': 'why:convnext_tiny', '#crops-all': 'all', '#crops-hires': 'hi', '#convnext-tiny': 'clf', '#convnext-tiny-evaluation': 'clf'}[location.hash] || 'ov');
</script>
'''

if __name__ == "__main__":
    main()
