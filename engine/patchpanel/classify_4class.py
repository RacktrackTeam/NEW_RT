"""Four-class patch-panel classification from DINOv2 embeddings.

Gallery:  C:\\Users\\JayasreePanitham\\Downloads\\PatchPanel_Images\\PatchPanel_Images
          Fiber Patchpanel / RJ-45 / co-axial / keystone   (398 images)
Test:     the 230 hand-labelled crops in final_test_dataset

Same retrieval recipe as predict.py -- no training. Every gallery image is
tiled at port scale, embedded with DINOv2 ViT-B/14, and each test image is
scored by per-class best-match similarity over its own tiles.

Writes out/predictions_4class.csv; generate_4class_report.py turns that into
the HTML report.
"""

import argparse
import os

import numpy as np
import pandas as pd

from ppc.embed import Encoder
from ppc.index import build, list_flat

HERE = os.path.dirname(os.path.abspath(__file__))

# This dataset adds .avif on top of the extensions ppc.index knows about
# (Pillow decodes it natively here). desktop.ini and friends are ignored.
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif"}

def _first_dir(*paths):
    """First of these directories that exists, else the last one.

    The working copy keeps the gallery and the test crops in Downloads;
    a packaged copy ships them as ./gallery and ./images beside this file.
    Trying the original path first means this file behaves identically
    here and runs unchanged after being copied somewhere else -- and when
    neither exists the last candidate is returned so the error names the
    local path the user can actually create.
    """
    for p in paths:
        if p and os.path.isdir(p):
            return p
    return paths[-1]


GALLERY = _first_dir(
    r"C:\Users\JayasreePanitham\Downloads\PatchPanel_Images\PatchPanel_Images",
    os.path.join(HERE, "gallery"))
TEST = _first_dir(
    r"C:\Users\JayasreePanitham\Downloads\final_test_dataset\final_test_dataset",
    os.path.join(HERE, "images"))

# Four classes, canonical names. The raw folders use a space and mixed case.
FOLDER_TO_CLASS = {
    "Fiber Patchpanel": "Fiber_Patchpanel",
    "RJ-45": "RJ-45",
    "co-axial": "Co-axial",
    "keystone": "Keystone",
}
CLASSES = ["RJ-45", "Fiber_Patchpanel", "Co-axial", "Keystone"]
CLASS_IDX = {c: i for i, c in enumerate(CLASSES)}

# Ground truth was labelled in the 11-leaf connector taxonomy; collapse it
# onto the four panel classes of this dataset. Mixed_media and Unclear have
# no four-class equivalent, so they are reported but excluded from accuracy.
LEAF_TO_CLASS = {
    "RJ-45": "RJ-45",
    "LC_UPC": "Fiber_Patchpanel", "LC_APC": "Fiber_Patchpanel",
    "SC_UPC": "Fiber_Patchpanel", "SC_APC": "Fiber_Patchpanel",
    "ST_Ports": "Fiber_Patchpanel", "FC": "Fiber_Patchpanel",
    "BNC": "Co-axial", "RF_Coax": "Co-axial",
    "F_type": "Co-axial", "TNC": "Co-axial",
    # A blank keystone frame is exactly what the dataset's 'keystone' folder
    # holds: a panel of empty keystone slots.
    "Blank_panel": "Keystone",
}
UNMAPPABLE = {"Mixed_media", "Unclear"}


def list_gallery4(root):
    """root/<class folder>/*.jpg -> [(path, canonical class)]."""
    items = []
    for folder in sorted(os.listdir(root)):
        d = os.path.join(root, folder)
        if not os.path.isdir(d):
            continue
        if folder not in FOLDER_TO_CLASS:
            raise KeyError("Unmapped gallery folder %r -- add it to "
                           "FOLDER_TO_CLASS" % folder)
        cls = FOLDER_TO_CLASS[folder]
        for f in sorted(os.listdir(d)):
            if os.path.splitext(f)[1].lower() in IMG_EXT:
                items.append((os.path.join(d, f), cls))
    return items


def per_class_sim(q_tiles, g_tiles, g_cls, n_cls, topm=1):
    """(n_query_tiles, n_cls) per-class best-match similarity.

    Per-class max rather than a global k-NN vote: the gallery is only roughly
    balanced (101/100/100/97) but tile counts are not, and taking each class's
    own best matches makes the score independent of how many tiles it brought.
    """
    out = np.full((len(q_tiles), n_cls), np.nan, dtype=np.float32)
    if len(q_tiles) == 0:
        return out
    sim = q_tiles @ g_tiles.T
    for ci in range(n_cls):
        cols = np.where(g_cls == ci)[0]
        if len(cols) == 0:
            continue
        s = sim[:, cols]
        m = min(topm, s.shape[1])
        out[:, ci] = np.partition(s, -m, axis=1)[:, -m:].mean(1)
    return out


def aggregate(pc, gamma=4.0, temp=0.07):
    """Confidence-weighted vote over one image's tiles -> class distribution.

    Most tiles of a panel are rack ears, label strips and blank metal that
    match every class equally; raising each tile's peak similarity to gamma
    lets the few tiles that actually landed on a port dominate the vote.
    """
    n_cls = pc.shape[1]
    valid = ~np.isnan(pc).all(1)
    if not valid.any():
        return np.full(n_cls, 1.0 / n_cls), 0.0
    pc = pc[valid]
    live = ~np.isnan(pc).any(0)

    conf = np.nanmax(pc, axis=1)
    w = np.clip(conf - conf.min() + 1e-3, 1e-6, None) ** gamma
    w = w / w.sum()

    raw = np.where(live, np.nansum(pc * w[:, None], axis=0), -np.inf)
    z = (raw - raw[np.isfinite(raw)].max()) / temp
    e = np.exp(np.where(np.isfinite(z), z, -np.inf))
    return (e / e.sum()).astype(np.float64), float(conf.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gallery", default=GALLERY)
    ap.add_argument("--test", default=TEST)
    # Prefers the hand-reviewed four-class labels (make_ground_truth_4class.py)
    # and falls back to the old eleven-type file, mapped through
    # LEAF_TO_CLASS. The old file predates the Keystone class and labels
    # modular keystone frames "RJ-45", so it is only a fallback.
    ap.add_argument("--truth", default=None)
    # The configuration validated by sweep.py: ViT-B @224, scales 1.0/0.5,
    # topm=1, gamma=4. Raising img-size to 336 measured WORSE (tiles are
    # already port-scale) and misses the cache.
    ap.add_argument("--model", default="b")
    ap.add_argument("--img-size", type=int, default=224)
    ap.add_argument("--scales", default="1.0,0.5")
    ap.add_argument("--max-tiles", type=int, default=36)
    ap.add_argument("--topm", type=int, default=1)
    ap.add_argument("--gamma", type=float, default=4.0)
    # Absolute match quality. Relative scores always elect *a* winner even
    # when nothing in the gallery resembles the crop; this flags that case.
    ap.add_argument("--min-peak-sim", type=float, default=0.60)
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    # Names a dataset so a second test set gets its own cache, predictions
    # and ground truth instead of overwriting the first one's.
    ap.add_argument("--name", default="")
    args = ap.parse_args()
    sfx = ("_" + args.name) if args.name else ""

    scales = tuple(float(x) for x in args.scales.split(","))
    tag = "%s_%d_%s_mt%d" % (
        args.model, args.img_size,
        "-".join(str(x).replace(".", "") for x in scales), args.max_tiles)
    cdir = os.path.join(args.out, "cache")
    os.makedirs(cdir, exist_ok=True)

    enc = Encoder(size=args.model, img_size=args.img_size)
    tk = dict(scales=scales, out_size=args.img_size, max_tiles=args.max_tiles)

    gal_items = list_gallery4(args.gallery)
    print("gallery: %d images in %d classes" % (len(gal_items), len(CLASSES)))
    g = build(gal_items, enc,
              cache=os.path.join(cdir, "gal4_%s.npz" % tag),
              tile_kw=tk, desc="gallery4")
    # Same tag as predict.py, so the 230 test crops are already embedded.
    t = build(list_flat(args.test), enc,
              cache=os.path.join(cdir, "test%s_%s.npz" % (sfx, tag)),
              tile_kw=tk, desc="test")

    g_labels = list(g["labels"])
    g_cls = np.array([CLASS_IDX[g_labels[i]] for i in g["tile_img"]])
    print("gallery tiles: %d   test tiles: %d"
          % (len(g["tile_emb"]), len(t["tile_emb"])))
    for c in CLASSES:
        print("   %-18s %3d imgs  %5d tiles"
              % (c, g_labels.count(c), int((g_cls == CLASS_IDX[c]).sum())))

    truth, leafs, conf = {}, {}, {}
    gt4 = os.path.join(args.out, "ground_truth_4class%s.csv" % sfx)
    src = args.truth or (gt4 if os.path.exists(gt4)
                         else os.path.join(args.out, "ground_truth.csv")
                         if not sfx else gt4)
    if os.path.exists(src):
        gt = pd.read_csv(src)
        if "true_class" in gt.columns:
            truth = dict(zip(gt.file, gt.true_class))
            leafs = dict(zip(gt.file, gt.get("old_leaf", gt.true_class)))
            conf = dict(zip(gt.file, gt.get("confidence", "normal")))
        else:
            leafs = dict(zip(gt.file, gt.true_leaf))
            truth = {f: LEAF_TO_CLASS.get(v, "") for f, v in leafs.items()}
        print("ground truth: %s (%d rows)" % (os.path.basename(src), len(gt)))

    rows = []
    for i, path in enumerate(t["paths"]):
        q = t["tile_emb"][t["tile_img"] == i]
        pc = per_class_sim(q, g["tile_emb"], g_cls, len(CLASSES),
                           topm=args.topm)
        peak = (float(np.nanmax(pc, axis=1).mean())
                if len(pc) and not np.isnan(pc).all() else 0.0)
        s, _ = aggregate(pc, gamma=args.gamma)

        order = np.argsort(-s)
        pred = CLASSES[int(order[0])]
        name = os.path.basename(path)
        true_cls = str(truth.get(name, "") or "")
        leaf = str(leafs.get(name, "") or "")
        rows.append({
            "truth_conf": conf.get(name, "normal"),
            "file": os.path.basename(path),
            "src_path": path,
            "n_tiles": int(len(q)),
            "pred_class": pred,
            "pred_conf": round(float(s[order[0]]), 4),
            "runner_up": CLASSES[int(order[1])],
            "runner_up_conf": round(float(s[order[1]]), 4),
            "margin": round(float(s[order[0]] - s[order[1]]), 4),
            "peak_sim": round(peak, 4),
            "scores": "; ".join("%s=%.3f" % (CLASSES[j], s[j]) for j in order),
            "true_leaf": leaf,
            "true_class": true_cls,
            "scored": bool(true_cls),
            "correct": (pred == true_cls) if true_cls else None,
            "low_match": peak < args.min_peak_sim,
        })

    df = pd.DataFrame(rows)
    dest = os.path.join(args.out, "predictions_4class%s.csv" % sfx)
    df.to_csv(dest, index=False)

    ev = df[df.scored]
    acc = ev.correct.mean() if len(ev) else float("nan")
    print("\n%d test crops classified  (encoder %s)" % (len(df), enc.name))
    print("predicted:")
    for k, v in df.pred_class.value_counts().items():
        print("   %-18s %4d  (%4.1f%%)" % (k, v, v / len(df) * 100))
    if len(ev):
        print("\nscored against ground truth: %d of %d  (%d unmappable: %s)"
              % (len(ev), len(df), len(df) - len(ev),
                 ", ".join(sorted(set(df[~df.scored].true_leaf) - {""}))
                 or "-"))
        print("accuracy: %.1f%%  (%d/%d)"
              % (acc * 100, int(ev.correct.sum()), len(ev)))
        maj = ev.true_class.value_counts()
        print("always-'%s' baseline: %.1f%%"
              % (maj.index[0], maj.iloc[0] / len(ev) * 100))
    else:
        print("\nNO GROUND TRUTH for this set -- predictions only, no "
              "accuracy.\nLabel them with the labelling tool, export, then "
              "run:\n  python make_ground_truth_4class.py --from-csv "
              "<file> --name %s" % (args.name or "<name>"))
    print("low-match (peak_sim < %.2f): %d" % (args.min_peak_sim,
                                               int(df.low_match.sum())))
    print("\nwrote %s" % dest)


if __name__ == "__main__":
    main()
