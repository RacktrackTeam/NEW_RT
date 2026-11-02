"""Step 3 accuracy for every device-grouping method, with examples, on the labelled test photos.

    python3 scripts/devices/eval_step3.py [max_images]

Methods (device_grouping.METHODS): pair model (teammate's updated approach), edge boundaries and brightness scan
(teammate 3). Each runs twice per photo:
  * "app":   exactly as in the app - our units, the Ensemble's occupied / free / uncertain (uncertain = taken);
  * "truth": the hand labels say which units are taken, so only the grouping itself is judged.
Ground truth: data/datasets/devices in this folder, the photos its manifest.csv marks as the test split (462 photos, device boxes drawn by hand).
Grading (same as the teammate's train_evaluate.py): a unit belongs to the labelled box covering >= 40 % of it;
ids 0 (closed panel) and 1 (empty) are free; a real device is "found exactly" when a predicted device has exactly
its graded units. Every real device is also sorted into one case (kept whole / split / joined with another /
missed / free unit added) and a few of each are drawn: hand labels left, the app right.
Writes models/v2/step3_eval.json and data/eval/step3_examples/.
"""
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import numpy as np
from PIL import Image, ImageOps

import device_grouping as dg
import occupancy as occ

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "datasets" / "devices"     # the one device dataset; the test photos are the old merge's test split (manifest.csv)
OUT = ROOT / "models" / "v2" / "step3_eval.json"
EX = ROOT / "data" / "eval" / "step3_examples"
COVER = 0.4
PER_CASE = 3
NAMES = {0: "Closed Unit", 1: "Empty", 2: "Firewall", 3: "Gateway", 4: "Load Balancer", 5: "PDU", 6: "Patch Panel",
         7: "Router", 8: "Server", 9: "Storage unit", 10: "Switch", 11: "UPS"}
CASES = {"tall_ok": "Tall device kept in one piece", "check_ok": "Right, but flagged for a check",
         "split": "Tall device split into pieces", "merged": "Two devices joined into one",
         "missed": "Device missed (units called free)", "phantom": "Free unit added to a device"}


def load_gt(lbl, w, h):
    gt = []
    for row in lbl.read_text().split("\n"):
        v = row.split()
        if not v:
            continue
        c, xy = int(v[0]), np.array(v[1:], float)
        if len(xy) == 4:
            xy = np.array([xy[0] - xy[2] / 2, xy[1] - xy[3] / 2, xy[0] + xy[2] / 2, xy[1] + xy[3] / 2])
        xs, ys = xy[0::2] * w, xy[1::2] * h
        gt.append((xs.min(), ys.min(), xs.max(), ys.max(), c))
    return gt


def cover(slot, obj):
    ix = max(0, min(slot[2], obj[2]) - max(slot[0], obj[0]))
    iy = max(0, min(slot[3], obj[3]) - max(slot[1], obj[1]))
    return ix * iy / ((slot[2] - slot[0]) * (slot[3] - slot[1]) + 1e-6)


def new_stats():
    return dict(dev=0, exact=0, size=0, img_ok=0, images=0, predicted=0, pairs=0, pair_ok=0, same_true=0,
                same_found=0, diff_true=0, diff_kept=0, by_class={}, cases={c: 0 for c in CASES})


def grade(rows, truth, graded, gt_dev, gt, res, st):
    """Add one photo's result to the stats; return [(case, gt objects, predicted ids)] for drawing examples."""
    idx = {r["unit"]: k for k, r in enumerate(rows)}
    pred = {d["id"]: {"ks": {idx[u] for u in d["units"]}, "status": d["status"], "box": d["box"], "h": d["height_u"]}
            for d in res["devices"]}
    unit_pred = {k: pid for pid, p in pred.items() for k in p["ks"]}
    pred_sets = [frozenset(p["ks"] & graded) for p in pred.values() if p["ks"] & graded]
    found = []
    for o, ks in gt_dev.items():
        st["dev"] += 1
        bc = st["by_class"].setdefault(NAMES.get(gt[o][4], str(gt[o][4])), [0, 0])
        bc[0] += 1
        exact = frozenset(ks) in pred_sets
        st["exact"] += exact; bc[1] += exact
        p0 = unit_pred.get(min(ks))
        st["size"] += p0 is not None and len(pred[p0]["ks"] & graded) == len(ks)
        pids = {unit_pred.get(k) for k in ks}
        if exact:
            p = unit_pred[min(ks)]
            case = "check_ok" if pred[p]["status"] == "check" else ("tall_ok" if len(ks) >= 2 else None)
        elif None in pids:
            case = "missed"
        elif len(pids) > 1:
            case = "split"
        else:
            p = next(iter(pids))
            others = {truth[k][1] for k in pred[p]["ks"] & graded if truth[k][0] == "occupied"} - {o}
            case = "merged" if others else "phantom"
        if case:
            st["cases"][case] += 1
            fg = [o] + (sorted({truth[k][1] for k in pred[next(iter(pids))]["ks"] & graded
                                if truth[k][0] == "occupied"} - {o}) if case == "merged" else [])
            found.append((case, fg, [p for p in pids if p is not None]))
    for pa in res["pairs"]:
        i, j = idx[pa["upper"]], idx[pa["lower"]]
        if truth[i][0] == truth[j][0] == "occupied":
            same = truth[i][1] == truth[j][1]
            joined = (pa["p_same"] >= res["same_thr"]) if pa.get("p_same") is not None else not pa.get("edge_found", False)
            st["pairs"] += 1; st["pair_ok"] += same == joined
            if same:
                st["same_true"] += 1; st["same_found"] += joined
            else:
                st["diff_true"] += 1; st["diff_kept"] += not joined
    st["images"] += 1; st["predicted"] += len(res["devices"]); st["img_ok"] += len(gt_dev) == len(pred_sets)
    return found, pred


def summary(st):
    d = max(1, st["dev"])
    return {"devices": {"graded": st["dev"], "found_exactly": st["exact"] / d, "right_size": st["size"] / d,
                        "images_with_right_count": st["img_ok"] / max(1, st["images"]), "predicted": st["predicted"]},
            "pairs": {"graded": st["pairs"], "accuracy": st["pair_ok"] / max(1, st["pairs"]),
                      "same_found": st["same_found"] / max(1, st["same_true"]), "same_true": st["same_true"],
                      "different_kept_apart": st["diff_kept"] / max(1, st["diff_true"]), "different_true": st["diff_true"]},
            "by_class": {k: {"devices": v[0], "found_exactly": v[1] / max(1, v[0])}
                         for k, v in sorted(st["by_class"].items(), key=lambda kv: -kv[1][0])},
            "cases": {c: {"count": n, "share": n / d} for c, n in st["cases"].items()}}


def baseline_exact(rows, graded, gt_dev, taken):
    single = [frozenset({k}) for k in range(len(rows)) if taken[k] and k in graded]
    runs, cur = [], []
    for k in range(len(rows)):
        if taken[k] and cur and k == cur[-1] + 1:
            cur.append(k)
        else:
            if cur:
                runs.append(frozenset(c for c in cur if c in graded))
            cur = [k] if taken[k] else []
    if cur:
        runs.append(frozenset(c for c in cur if c in graded))
    return sum(frozenset(s) in single for s in gt_dev.values()), sum(frozenset(s) in runs for s in gt_dev.values())


def main(limit=None):
    from step3_examples import draw_example, panel
    EX.mkdir(parents=True, exist_ok=True)
    for f in EX.glob("**/*.jpg"):
        f.unlink()
    import csv as _csv
    test = {r["photo"] for r in _csv.DictReader(open(DATA / "manifest.csv")) if r["old_merge_split"] == "test"}
    imgs = sorted(DATA / "images" / n for n in test if pathlib.Path(n).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"))
    if limit:
        imgs = imgs[:int(limit)]
    ens = next(a for a in occ.build_approaches("v2") if a.key == "ensemble")
    grouper = dg.DeviceGrouper()
    methods = list(dg.METHODS)
    stats = {m: {"app": new_stats(), "truth": new_stats()} for m in methods}
    base = {"app": [0, 0], "truth": [0, 0]}
    occ_tot = dict(graded=0, right=0, uncertain=0, units=0)
    examples = {m: {c: [] for c in CASES} for m in methods}
    compare = []
    per_image = []
    t0 = time.time()
    for n, path in enumerate(imgs, 1):
        lbl = DATA / "labels" / f"{path.stem}.txt"
        if not lbl.exists():
            continue
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        units = occ.detect_units(img)
        if not units:
            continue
        rows = occ.classify_units(img, units, ens, devices=occ.detect_devices(img))
        gt = load_gt(lbl, *img.size)
        truth = []
        for r in rows:
            cv = [(cover(r["box"], o), k) for k, o in enumerate(gt)]
            best = max(cv) if cv else (0, -1)
            truth.append((("free" if gt[best[1]][4] in (0, 1) else "occupied"), best[1]) if best[0] >= COVER else (None, -1))
        graded = {k for k, t in enumerate(truth) if t[0] is not None}
        gt_dev = {}
        for k in graded:
            if truth[k][0] == "occupied":
                gt_dev.setdefault(truth[k][1], set()).add(k)
        app_taken = [r["status"] in ("occupied", "uncertain") for r in rows]
        true_taken = [truth[k][0] == "occupied" if k in graded else app_taken[k] for k in range(len(rows))]
        occ_tot["units"] += len(rows); occ_tot["graded"] += len(graded)
        occ_tot["uncertain"] += sum(rows[k]["status"] == "uncertain" for k in graded)
        occ_tot["right"] += sum((rows[k]["status"] == "occupied") == (truth[k][0] == "occupied")
                                for k in graded if rows[k]["status"] != "uncertain")
        for mode, tk in (("app", app_taken), ("truth", true_taken)):
            s1, s2 = baseline_exact(rows, graded, gt_dev, tk)
            base[mode][0] += s1; base[mode][1] += s2
        rec = {"name": path.name, "gt_devices": len(gt_dev), "exact": {}}
        preds = {}
        for m in methods:
            for mode, tk in (("app", None), ("truth", true_taken)):
                res = dg.group_units(m, img, rows, grouper, taken=tk)
                found, pred = grade(rows, truth, graded, gt_dev, gt, res, stats[m][mode])
                if mode == "app":
                    preds[m] = pred
                    pred_sets = [frozenset(p["ks"] & graded) for p in pred.values() if p["ks"] & graded]
                    rec["exact"][m] = sum(frozenset(s) in pred_sets for s in gt_dev.values())
                    for case, fg, fp in found:
                        lst = examples[m][case]
                        key = sorted(rows[k]["unit"] for o in fg for k in gt_dev.get(o, ()))
                        if any(x["photo"] == path.name and sorted(u.lower() for u in x["units"]) == key for x in lst):
                            continue      # the same spot, seen from another real device of the same group
                        if len(lst) >= PER_CASE or (case in ("split", "tall_ok", "check_ok") and not 2 <= len(gt_dev[fg[0]]) <= 5):
                            continue
                        name = f"{m}/{case}_{len(lst) + 1}.jpg"
                        (EX / m).mkdir(exist_ok=True)
                        draw_example(img, rows, gt, truth, gt_dev, pred, fg, fp, name)
                        lst.append({"img": f"/static/step3_examples/{name}", "photo": path.name,
                                    "units": sorted((rows[k]["unit"].upper() for o in fg for k in gt_dev.get(o, ())),
                                                    key=lambda u: -int(u[1:])),
                                    "type": NAMES.get(gt[fg[0]][4], "")})
        per_image.append(rec)
        # same rack, every method: photos where the methods disagree most clearly (4-8 real devices)
        ex = rec["exact"]
        if 4 <= len(gt_dev) <= 8 and len(compare) < 4 and max(ex.values()) == len(gt_dev) and min(ex.values()) <= len(gt_dev) - 2:
            ys = [r["box"][1] for r in rows] + [r["box"][3] for r in rows]
            xs = [r["box"][0] for r in rows] + [r["box"][2] for r in rows]
            crop = (int(max(0, min(xs) - 10)), int(max(0, min(ys) - 10)), int(min(img.width, max(xs) + 10)), int(min(img.height, max(ys) + 10)))
            left = []
            for o, ks in gt_dev.items():
                b = [min(rows[k]["box"][0] for k in ks), min(rows[k]["box"][1] for k in ks),
                     max(rows[k]["box"][2] for k in ks), max(rows[k]["box"][3] for k in ks)]
                left.append((b, f"{len(ks)}U", (22, 130, 70), False, False))
            panels = [panel(img, crop, left, f"Marked by a person · {len(gt_dev)} devices")]
            for m in methods:
                bx = [(p["box"], f"D{pid}·{p['h']}U", (24, 32, 42) if p["status"] == "confirmed" else (200, 120, 0),
                       p["status"] != "confirmed", False) for pid, p in preds[m].items()]
                panels.append(panel(img, crop, bx, f"{dg.METHODS[m][0]} · {ex[m]} of {len(gt_dev)} right"))
            H = max(p.height for p in panels)
            sheet = Image.new("RGB", (sum(p.width for p in panels) + 12 * (len(panels) - 1), H), "white")
            x = 0
            for p in panels:
                sheet.paste(p, (x, 0)); x += p.width + 12
            name = f"compare_{len(compare) + 1}.jpg"
            sheet.save(EX / name, quality=82)
            compare.append({"img": f"/static/step3_examples/{name}", "photo": path.name, "gt_devices": len(gt_dev), "exact": ex})
        if n % 25 == 0:
            print(f"{n}/{len(imgs)} " + " | ".join(f"{m} {stats[m]['app']['exact'] / max(1, stats[m]['app']['dev']):.3f}"
                                                for m in methods) + f"  {(time.time() - t0) / n:.1f}s/img", flush=True)
    g = max(1, occ_tot["graded"])
    D = max(1, stats[methods[0]]["app"]["dev"])
    out = {
        "test_set": f"{len(per_image)} labelled test rack photos ({DATA})",
        "units": occ_tot["units"], "units_graded": occ_tot["graded"],
        "same_device_threshold": grouper.same_thr,
        "occupancy": {"accuracy_when_decided": occ_tot["right"] / max(1, occ_tot["graded"] - occ_tot["uncertain"]),
                      "decided": occ_tot["graded"] - occ_tot["uncertain"], "uncertain": occ_tot["uncertain"],
                      "uncertain_share": occ_tot["uncertain"] / g},
        "methods": {m: {"name": dg.METHODS[m][0], "description": dg.METHODS[m][1],
                        "app": summary(stats[m]["app"]), "truth": summary(stats[m]["truth"])} for m in methods},
        "baselines": {mode: {"every_unit_its_own_device": base[mode][0] / D, "join_every_run_of_taken_units": base[mode][1] / D}
                      for mode in base},
        "case_titles": CASES, "examples": examples, "compare": compare, "per_image": per_image,
        "seconds_per_image": (time.time() - t0) / max(1, len(per_image)),
    }
    # the default method's numbers at the top level, as before
    out.update({k: out["methods"]["pairs"]["app"][k] for k in ("devices", "pairs", "by_class")})
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({m: {mode: out["methods"][m][mode]["devices"]["found_exactly"] for mode in ("app", "truth")}
                      for m in methods}, indent=1), json.dumps(out["baselines"]))


if __name__ == "__main__":
    main(*sys.argv[1:2])
