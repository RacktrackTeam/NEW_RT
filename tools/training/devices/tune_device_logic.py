"""Measure, tune and grade the logic-only device grouping (src/device_logic.py) on the cached photos.

    python3 scripts/devices/tune_device_logic.py features valid      # measure every seam -> results/devices/feats_valid.json
    python3 scripts/devices/tune_device_logic.py auc train valid     # how well each cue separates same / different
    python3 scripts/devices/tune_device_logic.py fit devices_test 4   # tune the rule (a depth-4 decision list) on
                                                                      # train + valid photos NOT in that test set; writes device_logic.RULE
    python3 scripts/devices/tune_device_logic.py fit-score devices_test   # fit the scoring rule (device_logic.SCORE) on the tuning seams
    python3 scripts/devices/tune_device_logic.py contrib devices_test     # what each cue contributes -> contrib_<set>.json
    python3 scripts/devices/tune_device_logic.py grade test          # devices found exactly with the rule in device_logic.RULE
    python3 scripts/devices/tune_device_logic.py grade devices_test combined   # THE step 3 logic: our score + the
                                                                      # final_approach joining model, mixed per seam (src/device_join.py)
    python3 scripts/devices/tune_device_logic.py grade devices_test join   # the joining model alone (join at its 0.80)
    python3 scripts/devices/tune_device_logic.py grade devices_test edges   # another step 3 method: edges, scan, pairs
    python3 scripts/devices/tune_device_logic.py grade devices_test model   # logic + model: Shankar's device detector
                                                                      # decides the seams it covers, the logic the rest (src/device_model.py)
    python3 scripts/devices/tune_device_logic.py grade devices_test model_only   # her detector alone
    python3 scripts/devices/tune_device_logic.py boxes devices_test   # run her detector on every photo ->
                                                                      # results/devices/devmodel_<set>.json (needs DEIMV2_DIR; the file for
                                                                      # the 1,000 photos came from her own run)

Truth per unit comes from results/devices/rows_<split>.json (scripts/devices/cache_rows.py): "taken" is the hand
label (occupied), so only the grouping is judged. Units the labels do not cover count as free.
Grading as in eval_step3.py: a real device is found exactly when one predicted device has exactly its graded units.
Every grade also reports their_unseen: the same counts on the photos whose name is not in the joining model's training
batch (Trained_2511_V1_train / _valid); that is the fair number for anything that uses the joining model.
"""
import json
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
import cv2
import numpy as np
from PIL import Image, ImageOps

import device_join as DJ
import device_logic as DL
import device_model as DM

ROOT = pathlib.Path(__file__).resolve().parents[2]
RES = ROOT / "results" / "devices"


def load_bgr(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


def photo_truth(rec):
    """taken per unit (hand label), the graded units and the real devices {box index: set of unit indices}."""
    truth = rec["truth"]
    taken = [t[0] == "occupied" for t in truth]
    graded = {k for k, t in enumerate(truth) if t[0] is not None}
    gt_dev = {}
    for k in graded:
        if truth[k][0] == "occupied":
            gt_dev.setdefault(truth[k][1], set()).add(k)
    return taken, graded, gt_dev


THEIR_TRAINING = re.compile(r"Trained_2511_V1_(train|valid)_")   # photos the final_approach joining model was trained on


def join_cache(split):
    """{photo index: {k: P(same device) for the seam above unit k}} from results/devices/join_<split>.json, the joining
    model's answers on our unit boxes (device_join.their_p_same); None when the file is missing."""
    fp = RES / f"join_{split}.json"
    if not fp.exists():
        return None
    return {int(i): {b: p for (a, b), p in zip(v["pairs"], v["p_same"]) if b == a + 1}
            for i, v in json.load(open(fp))["photos"].items()}


def boxes(split):
    """Run Shankar's device detector on every photo of rows_<split>.json and cache its boxes (device_model.detect)."""
    recs = json.load(open(RES / f"rows_{split}.json"))
    fp = RES / f"devmodel_{split}.json"
    photos = json.load(open(fp))["photos"] if fp.exists() else {}
    t0 = time.time()
    for n, rec in enumerate(recs, 1):
        name = pathlib.Path(rec["file"]).name
        if name in photos:
            continue
        rgb = np.array(ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB"))
        photos[name] = [[round(v, 1) for v in b[:4]] + [round(b[4], 4)] for b in DM.detect(rgb)]
        if n % 25 == 0:
            print(f"{split} {n}/{len(recs)}  {(time.time() - t0) / n:.1f}s/photo", flush=True)
            json.dump({"note": BOXES_NOTE, "photos": photos}, open(fp, "w"), separators=(",", ":"))
    json.dump({"note": BOXES_NOTE, "photos": photos}, open(fp, "w"), separators=(",", ":"))
    print(f"written {fp}: {len(photos)} photos, {sum(len(v) for v in photos.values())} boxes")


BOXES_NOTE = ("Shankar's device detector (DEIMv2 + DINOv3 vit_tinyplus, one class, weights/device_detector_deimv2.pth) on every photo: "
              f"[x1, y1, x2, y2, score] in pixels of the photo, every box at score >= {DM.CACHE_MIN_SCORE}, photo fed at {DM.INPUT} x {DM.INPUT}. "
              "Made by scripts/devices/tune_device_logic.py boxes <set>.")


def features(split):
    recs = json.load(open(RES / f"rows_{split}.json"))
    out, t0 = [], time.time()
    for n, rec in enumerate(recs, 1):
        if len(rec["rows"]) < 2:
            continue
        taken, graded, gt_dev = photo_truth(rec)
        if not any(taken):
            continue
        bgr = load_bgr(rec["file"])
        feats = DL.seam_features(bgr, rec["rows"], taken)
        for k, f in feats.items():
            same = rec["truth"][k - 1][1] == rec["truth"][k][1]
            out.append({"photo": n - 1, "k": k, "same": bool(same), "cls": rec["gt"][rec["truth"][k][1]][4], **f})
        if n % 100 == 0:
            print(f"{split} {n}/{len(recs)}  {(time.time() - t0) / n:.2f}s/photo  seams {len(out)}", flush=True)
    json.dump(out, open(RES / f"feats_{split}.json", "w"))
    print(f"written feats_{split}.json: {len(out)} seams, same {sum(o['same'] for o in out)}")


def auc(*splits):
    rows = [o for s in splits for o in json.load(open(RES / f"feats_{s}.json"))]
    y = np.array([o["same"] for o in rows])
    print(f"{len(rows)} seams: same {y.sum()}  different {(~y).sum()}")
    print(f"{'cue':14s} {'AUC(diff>)':>10s} {'same p10/50/90':>24s} {'diff p10/50/90':>24s}")
    for f in DL.FEATURES:
        x = np.array([o[f] for o in rows], float)
        # AUC of "different seam" against the cue: > 0.5 means larger values mean a boundary
        order = np.argsort(x)
        ranks = np.empty(len(x)); ranks[order] = np.arange(1, len(x) + 1)
        n1, n0 = (~y).sum(), y.sum()
        a = (ranks[~y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
        ps = np.percentile(x[y], [10, 50, 90]); pd = np.percentile(x[~y], [10, 50, 90])
        print(f"{f:14s} {a:10.3f} {ps[0]:7.2f} {ps[1]:7.2f} {ps[2]:7.2f}  {pd[0]:7.2f} {pd[1]:7.2f} {pd[2]:7.2f}")


def tuning_seams(test_split):
    """Seams of the train + valid photos that are not in the test set: X, y (True = boundary), photo keys, records."""
    test = {pathlib.Path(r["file"]).stem for r in json.load(open(RES / f"rows_{test_split}.json"))}
    X, y, keys, R = [], [], [], {}
    for s in ("train", "valid"):
        recs = json.load(open(RES / f"rows_{s}.json")); R[s] = recs
        for o in json.load(open(RES / f"feats_{s}.json")):
            if pathlib.Path(recs[o["photo"]]["file"]).stem in test:
                continue
            X.append([o[f] for f in DL.FEATURES]); y.append(not o["same"]); keys.append((s, o["photo"], o["k"]))
    return np.array(X), np.array(y), keys, R


def devices_exact_from(cuts, keys, R):
    """Devices found exactly when the seams in `keys` are cut as `cuts` says (True = boundary)."""
    C = {}
    for (s, i, k), c in zip(keys, cuts):
        C.setdefault((s, i), {})[k] = bool(c)
    dev = ex = 0
    for (s, i), cut in C.items():
        rec = R[s][i]; taken, graded, gt_dev = photo_truth(rec); devices = []
        for a, b in DL.runs_of(taken):
            cur = [a]
            for k in range(a + 1, b + 1):
                if cut.get(k, False):
                    devices.append(cur); cur = [k]
                else:
                    cur.append(k)
            devices.append(cur)
        ps = [frozenset(set(d) & graded) for d in devices]
        dev += len(gt_dev); ex += sum(frozenset(ks) in ps for ks in gt_dev.values())
    return ex / max(1, dev)


def cv(test_split, depths=(3, 4, 5, 6, 8), leaves=(30, 60, 120)):
    """5-fold by photo on the tuning seams: seam accuracy and devices exact for each tree size, plus a
    gradient-boosted reference (what a trained model gets from the same cues)."""
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.model_selection import GroupKFold, cross_val_predict
    X, y, keys, R = tuning_seams(test_split)
    g = np.array([f"{s}{i}" for s, i, _ in keys])
    print(f"{len(y)} tuning seams, {len(set(g))} photos; cues {len(DL.FEATURES)}")
    print(f"{'rule':22s} {'seams right':>12s} {'devices exact':>14s}")
    for d in depths:
        for m in leaves:
            pred = cross_val_predict(DecisionTreeClassifier(max_depth=d, min_samples_leaf=m), X, y, cv=GroupKFold(5), groups=g)
            print(f"tree depth {d:d} leaf {m:3d}     {(pred == y).mean():12.3f} {devices_exact_from(pred, keys, R):14.3f}")
    pred = cross_val_predict(HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=4), X, y, cv=GroupKFold(5), groups=g)
    print(f"{'boosted trees (ref)':22s} {(pred == y).mean():12.3f} {devices_exact_from(pred, keys, R):14.3f}")


def fit(test_split, depth=4, min_leaf=60):
    """Tune the decision list on the train + valid seams whose photos are not in the test set. Several weightings
    of the two mistakes are tried (cutting a tall device costs more than joining two devices, because a run of 1U
    devices is right by default); the one with the most devices exact on the tuning photos is written into
    src/device_logic.py between the RULE markers."""
    from sklearn.tree import DecisionTreeClassifier
    test = {pathlib.Path(r["file"]).stem for r in json.load(open(RES / f"rows_{test_split}.json"))}
    X, y, photos, R = [], [], [], {}
    for s in ("train", "valid"):
        recs = json.load(open(RES / f"rows_{s}.json")); R[s] = recs
        for o in json.load(open(RES / f"feats_{s}.json")):
            if pathlib.Path(recs[o["photo"]]["file"]).stem in test:
                continue
            X.append([o[f] for f in DL.FEATURES]); y.append(not o["same"]); photos.append((s, o["photo"], o["k"]))
    X, y = np.array(X), np.array(y)

    def to_rule(t):
        tr = t.tree_

        def node(i):
            if tr.children_left[i] == -1:
                return {"boundary": bool(tr.value[i][0][1] >= tr.value[i][0][0])}
            return {"f": DL.FEATURES[tr.feature[i]], "t": round(float(tr.threshold[i]), 3), "lo": node(tr.children_left[i]), "hi": node(tr.children_right[i])}
        return node(0)

    def exact_share(rule):
        dec = {}
        for (s, i, k), x in zip(photos, X):
            dec.setdefault((s, i), {})[k] = DL.is_boundary(dict(zip(DL.FEATURES, x)), rule)
        dev = ex = 0
        for (s, i), d in dec.items():
            rec = R[s][i]; taken, graded, gt_dev = photo_truth(rec); devices = []
            for a, b in DL.runs_of(taken):
                cur = [a]
                for k in range(a + 1, b + 1):
                    if d.get(k, False):
                        devices.append(cur); cur = [k]
                    else:
                        cur.append(k)
                devices.append(cur)
            psets = [frozenset(set(q) & graded) for q in devices]
            dev += len(gt_dev); ex += sum(frozenset(ks) in psets for ks in gt_dev.values())
        return ex / dev
    best = None
    for w in (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0):
        t = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=min_leaf, class_weight={True: 1.0, False: w}).fit(X, y)
        rule = to_rule(t); e = exact_share(rule)
        print(f"  weight of 'same' {w:.2f}: seams {t.score(X, y):.3f}  devices exact {e:.3f}")
        if best is None or e > best[0]:
            best = (e, w, rule)
    e, w, rule = best
    print(f"chosen weight {w}: devices exact {e:.3f} on {len(y)} tuning seams from photos outside {test_split}")
    print(json.dumps(rule))
    src = ROOT / "src" / "device_logic.py"
    s = src.read_text()
    a, b = s.index("# RULE-BEGIN\n") + len("# RULE-BEGIN\n"), s.index("# RULE-END")
    s = s[:a] + "RULE = " + repr(rule) + "\n" + s[b:]
    prior = height_prior(test_split)
    a, b = s.index("# PRIOR-BEGIN\n") + len("# PRIOR-BEGIN\n"), s.index("# PRIOR-END")
    s = s[:a] + "PRIOR = " + repr(prior) + "\n" + s[b:]
    src.write_text(s)
    print("device-height prior (1U..8U):", prior)
    return rule


def height_prior(test_split):
    """Share of devices 1U .. 8U tall on the tuning photos (train + valid outside the test set)."""
    test = {pathlib.Path(r["file"]).stem for r in json.load(open(RES / f"rows_{test_split}.json"))}
    counts = np.zeros(8)
    for s in ("train", "valid"):
        for rec in json.load(open(RES / f"rows_{s}.json")):
            if pathlib.Path(rec["file"]).stem in test or len(rec["rows"]) < 2:
                continue
            for ks in photo_truth(rec)[2].values():
                counts[min(8, len(ks)) - 1] += 1
    counts = np.maximum(counts, 1)
    return [round(float(c), 4) for c in counts / counts.sum()]


def fit_score(test_split, min_w=0.05):
    """The scoring rule: a logistic score on the cues, fitted on the tuning seams. Cues are chosen by an L1 fit on
    the standardised cues (|weight| >= min_w), then refitted; the weights are written on the raw cues into
    src/device_logic.py (SCORE) with the device-height prior (PRIOR). Prints the 5-fold-by-photo check."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.model_selection import GroupKFold, cross_val_predict
    X, y, keys, R = tuning_seams(test_split)
    g = np.array([f"{s}{i}" for s, i, _ in keys])
    sc = StandardScaler().fit(X); Xs = sc.transform(X)
    l1 = LogisticRegression(penalty="l1", C=0.05, solver="liblinear", max_iter=5000).fit(Xs, y)
    keep = [j for j, w in enumerate(l1.coef_[0]) if abs(w) >= min_w]
    lr = LogisticRegression(C=0.5, max_iter=5000)
    p = cross_val_predict(lr, Xs[:, keep], y, cv=GroupKFold(5), groups=g, method="predict_proba")[:, 1]
    cuts_seam = p >= 0.5
    C = {}
    for (s, i, k), pk in zip(keys, p):
        C.setdefault((s, i), {})[k] = pk
    cuts_part = []
    for (s, i, k) in keys:
        pass
    # devices exact with the whole-run partition, from the cross-validated probabilities
    prior = height_prior(test_split)
    dev = ex = 0
    for (s, i), ps in C.items():
        rec = R[s][i]; taken, graded, gt_dev = photo_truth(rec); devices = []
        for a, b in DL.runs_of(taken):
            devices += DL.partition({k: ps.get(k, 0.0) for k in range(a + 1, b + 1)}, a, b, prior=prior)
        pss = [frozenset(set(d) & graded) for d in devices]; dev += len(gt_dev); ex += sum(frozenset(ks) in pss for ks in gt_dev.values())
    print(f"{len(keep)} cues kept of {len(DL.FEATURES)}; 5-fold by photo on {len(y)} tuning seams: seams right {(cuts_seam == y).mean():.3f}, "
          f"devices exact {devices_exact_from(cuts_seam, keys, R):.3f} seam by seam, {ex / dev:.3f} with the whole-run partition")
    lr.fit(Xs[:, keep], y)
    w_std, b_std = lr.coef_[0], float(lr.intercept_[0])
    weights = {DL.FEATURES[j]: float(w / sc.scale_[j]) for j, w in zip(keep, w_std)}
    intercept = b_std - sum(w * sc.mean_[j] / sc.scale_[j] for j, w in zip(keep, w_std))
    score = {"intercept": round(float(intercept), 4), "weights": {c: round(w, 5) for c, w in weights.items()},
             "standardised": {DL.FEATURES[j]: round(float(w), 3) for j, w in zip(keep, w_std)}}
    src = ROOT / "src" / "device_logic.py"; s_ = src.read_text()
    a, b = s_.index("# SCORE-BEGIN\n") + len("# SCORE-BEGIN\n"), s_.index("# SCORE-END")
    s_ = s_[:a] + "SCORE = " + repr(score) + "\n" + s_[b:]
    a, b = s_.index("# PRIOR-BEGIN\n") + len("# PRIOR-BEGIN\n"), s_.index("# PRIOR-END")
    s_ = s_[:a] + "PRIOR = " + repr(prior) + "\n" + s_[b:]
    src.write_text(s_)
    print("standardised weights (bigger = more say):", dict(sorted(score["standardised"].items(), key=lambda kv: -abs(kv[1]))))
    print("prior:", prior)


FAMILIES = {"lines on the seam": ["line_cov", "line_ratio", "nline_cov", "line_off", "peak_cov", "face_line_peak", "gap_peaks", "rc_line_cov", "rc_nline_cov", "rc_peak_cov", "rc_off"],
            "features continue across the seam": ["vcont", "vcont_wide", "ear_vcont", "side_cont", "rc_vcont"],
            "dark gap": ["gap_dark", "side_dark", "rc_gap_dark"], "the two units look alike": ["hist_int", "unit_sim", "unit_sim_next", "rc_hist_int", "rc_tone_l", "tone_l", "tone_ab", "tone_std"],
            "the rest of the run": ["ctx_ratio", "ctx_nb", "run_pos", "run_len", "n_units"]}


def contrib(test_split):
    """What each cue and cue family contributes: devices exact on the test set with the full score, minus the same
    with that cue (family) left out and the score refitted on the tuning seams. Writes contrib_<set>.json."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    X, y, keys, R = tuning_seams(test_split)
    recs = json.load(open(RES / f"rows_{test_split}.json"))
    feats = {}
    for o in json.load(open(RES / f"feats_{test_split}.json")):
        feats.setdefault(o["photo"], {})[o["k"]] = o
    cues = list(DL.SCORE["weights"])

    def fit(keep):
        idx = [DL.FEATURES.index(c) for c in keep]; sc = StandardScaler().fit(X[:, idx])
        lr = LogisticRegression(C=0.5, max_iter=5000).fit(sc.transform(X[:, idx]), y)
        w = {c: float(wj / sc.scale_[j]) for j, (c, wj) in enumerate(zip(keep, lr.coef_[0]))}
        return {"intercept": float(lr.intercept_[0]) - sum(lr.coef_[0][j] * sc.mean_[j] / sc.scale_[j] for j in range(len(keep))), "weights": w}

    def score_grade(score):
        dev = ex = lone = 0
        for i, rec in enumerate(recs):
            if len(rec["rows"]) < 2:
                continue
            taken, graded, gt_dev = photo_truth(rec)
            if not gt_dev:
                continue
            fs = feats.get(i, {}); devices = []
            for a, b in DL.runs_of(taken):
                cur = [a]
                for k in range(a + 1, b + 1):
                    z = score["intercept"] + sum(w * fs[k][c] for c, w in score["weights"].items()) if k in fs else 1e9
                    if z >= 0:
                        devices.append(cur); cur = [k]
                    else:
                        cur.append(k)
                devices.append(cur)
            ps = [frozenset(set(d) & graded) for d in devices]
            for o, ks in gt_dev.items():
                dev += 1; ex += frozenset(ks) in ps
                lone += len(ks) == 1 and not any(k in fs or (k + 1) in fs for k in ks)
        return ex / dev, dev, lone
    base, dev, lone = score_grade(fit(cues))
    out = {"full": base, "devices": dev, "lone": lone, "lone_exact": lone, "drop": {}, "alone": {}}
    for c in cues:
        out["drop"][c] = base - score_grade(fit([x for x in cues if x != c]))[0]
    for name, members in FAMILIES.items():
        keep = [x for x in cues if x not in members]
        out["alone"][name] = base - (score_grade(fit(keep))[0] if keep else 0.75)
    json.dump(out, open(RES / f"contrib_{test_split}.json", "w"), indent=1)
    print(f"full score {base:.4f} on {dev} graded devices ({lone} lone units); family contributions (points):",
          {k: round(v * 100, 2) for k, v in sorted(out["alone"].items(), key=lambda kv: -kv[1])})
    print("top cues:", {k: round(v * 100, 2) for k, v in sorted(out["drop"].items(), key=lambda kv: -kv[1])[:8]})


def annotate(test_split, depth=4, min_leaf=60):
    """Keep the current RULE's tests but refresh each leaf's boundary share and the prior from the tuning seams."""
    X, y, keys, R = tuning_seams(test_split)
    counts = {}
    for x, yy in zip(X, y):
        f = dict(zip(DL.FEATURES, x)); node = DL.RULE; path = []
        while "boundary" not in node:
            lo = f[node["f"]] <= node["t"]; path.append("lo" if lo else "hi"); node = node["lo"] if lo else node["hi"]
        c = counts.setdefault(tuple(path), [0, 0]); c[int(bool(yy))] += 1
    def walk(node, path=()):
        if "boundary" in node:
            c = counts.get(path, [0, 0]); n = c[0] + c[1]
            node["p"] = round(c[1] / n, 3) if n else (1.0 if node["boundary"] else 0.0)
            return
        walk(node["lo"], path + ("lo",)); walk(node["hi"], path + ("hi",))
    rule = json.loads(json.dumps(DL.RULE)); walk(rule)
    src = ROOT / "src" / "device_logic.py"; s = src.read_text()
    a, b = s.index("# RULE-BEGIN\n") + len("# RULE-BEGIN\n"), s.index("# RULE-END")
    s = s[:a] + "RULE = " + repr(rule) + "\n" + s[b:]
    prior = height_prior(test_split)
    a, b = s.index("# PRIOR-BEGIN\n") + len("# PRIOR-BEGIN\n"), s.index("# PRIOR-END")
    s = s[:a] + "PRIOR = " + repr(prior) + "\n" + s[b:]
    src.write_text(s)
    print("leaf shares written; device-height prior (1U..8U):", prior)


def other_method(method, rec, taken):
    """Prasanna's two edge methods or Geethika's pair model, on the same rows and truth (device_grouping.group_units)."""
    import device_grouping as dg
    img = ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB")
    rows = [{"unit": r["unit"], "box": tuple(r["box"]), "status": "occupied" if t else "unoccupied"} for r, t in zip(rec["rows"], taken)]
    if method == "pairs":
        global _GROUPER
        if "_GROUPER" not in globals():
            _GROUPER = dg.DeviceGrouper()
        res = _GROUPER.group(img, rows, taken=taken)
    else:
        res = dg.group_units(method, img, rows, taken=taken)
    idx = {r["unit"]: k for k, r in enumerate(rows)}
    return [[idx[u] for u in d["units"]] for d in res["devices"]]


def grade(split, rule=None, feats_file=None, verbose=True, method="logic", mode="seam"):
    """Devices found exactly (and pair accuracy), using the cached features when present. method: logic (our score),
    combined (our score + the joining model, device_join), join (the joining model alone, our score where it has no
    pair), model (logic + model: Shankar's detector where it covers both units, the combined logic elsewhere,
    device_model), model_only (her detector alone), or another step 3 method (edges, scan, pairs)."""
    rule = rule or DL.RULE
    recs = json.load(open(RES / f"rows_{split}.json"))
    fp = RES / (feats_file or f"feats_{split}.json")
    cached = {}
    if fp.exists():
        for o in json.load(open(fp)):
            cached.setdefault(o["photo"], {})[o["k"]] = o
    joins = (join_cache(split) or {}) if method in ("combined", "join", "model") else {}
    boxes_ = DM.boxes_cache(split) if method in ("model", "model_only") else {}
    join_thr = DJ.their_package().THR if method == "join" else None
    dev = exact = 0; pairs = pair_ok = 0; img_ok = 0; n_img = 0
    single = runs = 0
    unseen = {"photos": 0, "devices": 0, "exact": 0, "all_right": 0}
    per_photo = []
    for i, rec in enumerate(recs):
        if len(rec["rows"]) < 2:
            continue
        taken, graded, gt_dev = photo_truth(rec)
        if not gt_dev:
            continue
        if method in ("logic", "combined", "join", "model"):
            feats = cached.get(i)
            if feats is None:
                feats = DL.seam_features(load_bgr(rec["file"]), rec["rows"], taken)
        if method in ("model", "model_only"):
            bx = boxes_.get(pathlib.Path(rec["file"]).name)
            if bx is None:
                bx = DM.detect(np.array(ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB")))
        if method == "logic":
            devices, split_, _ = DL.group(None, rec["rows"], taken, rule=rule, feats=feats, mode=mode)
        elif method in ("combined", "join"):
            theirs = joins.get(i)
            if theirs is None:
                theirs = DJ.their_p_same(np.array(ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB")), rec["rows"])
            if method == "combined":
                devices, split_, _ = DJ.group(None, rec["rows"], taken, feats=feats, theirs=theirs)
            else:
                split_ = {k: (theirs[k] < join_thr) if k in theirs else DL.seam_prob(f) >= DL.CUT for k, f in feats.items()}
                devices = DJ.cut(taken, split_)
        elif method == "model":
            theirs = joins.get(i)
            if theirs is None:
                theirs = DJ.their_p_same(np.array(ImageOps.exif_transpose(Image.open(rec["file"])).convert("RGB")), rec["rows"])
            devices, split_, _, _ = DM.group(None, rec["rows"], taken, boxes=bx, feats=feats, theirs=theirs)
        elif method == "model_only":
            devices, split_ = DM.model_only(rec["rows"], taken, bx)
        else:
            devices = other_method(method, rec, taken)
            first = {d[0] for d in devices}
            split_ = {k: (k in first) for a, b in DL.runs_of(taken) for k in range(a + 1, b + 1)}
        pred_sets = [frozenset(set(d) & graded) for d in devices if set(d) & graded]
        n_ex = sum(frozenset(ks) in pred_sets for ks in gt_dev.values())
        all_right = n_ex == len(gt_dev) == len(pred_sets)
        dev += len(gt_dev); exact += n_ex
        img_ok += all_right; n_img += 1
        if not THEIR_TRAINING.search(pathlib.Path(rec["file"]).name):
            unseen["photos"] += 1; unseen["devices"] += len(gt_dev); unseen["exact"] += n_ex; unseen["all_right"] += all_right
        single += sum(len(ks) == 1 for ks in gt_dev.values())
        run_sets = [frozenset(set(range(a, b + 1)) & graded) for a, b in DL.runs_of(taken)]
        runs += sum(frozenset(ks) in run_sets for ks in gt_dev.values())
        for k, s in split_.items():
            if k - 1 in graded and k in graded:
                same = rec["truth"][k - 1][1] == rec["truth"][k][1]
                pairs += 1; pair_ok += (not s) == same
        per_photo.append({"photo": i, "file": rec["file"], "devices": len(gt_dev), "exact": n_ex})
    out = {"split": split, "method": method, "mode": mode, "devices": dev, "found_exactly": exact / max(1, dev), "pairs": pairs,
           "pair_accuracy": pair_ok / max(1, pairs), "photos": n_img, "photos_all_right": img_ok / max(1, n_img),
           "baseline_every_unit": single / max(1, dev), "baseline_every_run": runs / max(1, dev),
           "their_unseen": {"photos": unseen["photos"], "devices": unseen["devices"], "found_exactly": unseen["exact"] / max(1, unseen["devices"]),
                            "photos_all_right": unseen["all_right"] / max(1, unseen["photos"])}, "rule": rule}
    if verbose:
        print(json.dumps({k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items() if k not in ("rule", "their_unseen")}))
        print(f"  on the {unseen['photos']} photos outside the joining model's training batch: devices exact {out['their_unseen']['found_exactly'] * 100:.2f} %, "
              f"photos all right {out['their_unseen']['photos_all_right'] * 100:.1f} %")
    out["per_photo"] = per_photo
    return out


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "features":
        features(sys.argv[2])
    elif cmd == "auc":
        auc(*sys.argv[2:])
    elif cmd == "cv":
        cv(sys.argv[2])
    elif cmd == "annotate":
        annotate(sys.argv[2])
    elif cmd == "fit-score":
        fit_score(sys.argv[2])
    elif cmd == "contrib":
        contrib(sys.argv[2])
    elif cmd == "boxes":
        boxes(sys.argv[2])
    elif cmd == "fit":
        fit(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4, int(sys.argv[4]) if len(sys.argv) > 4 else 60)
    elif cmd == "grade":
        method = sys.argv[3] if len(sys.argv) > 3 else "logic"
        mode = sys.argv[4] if len(sys.argv) > 4 else "seam"
        out = grade(sys.argv[2], method=method, mode=mode)
        json.dump(out, open(RES / f"grade_{sys.argv[2]}_{method}.json", "w"), indent=1)
