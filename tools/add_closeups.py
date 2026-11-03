"""Add close-up photos to a stored video run, as the app does for an upload (rack_video.closeup_layouts): every device
in a photo goes to the switch it is (the photo matched to the rack picture, else the unit on its label); where two
photos give the same switch, a rack-picture match beats a label one, then a fitted read, then more matching points.

    python3 scripts/video/add_closeups.py results/video_experiment/vdo1 crops
"""
import json
import pathlib
import shutil
import sys

import cv2
from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import rack_video as RV
import pipeline as P  # noqa: E402


def main(run, photos, keep_others=False):
    run, photos = ROOT / run, ROOT / photos
    r = json.loads((run / "result.json").read_text())
    rack = cv2.imread(str(run / "rack.png"))
    switches = {dv["index"]: {"polygon": dv["polygon"], "units": dv["units"], "kind": (dv.get("layout") or {}).get("kind")} for dv in r["devices"] if dv.get("polygon")}   # every device takes a cut
    mapped = {dv["index"] for dv in r["devices"] if dv.get("layout")}                                                        # the mapper kinds are read
    shutil.rmtree(run / "closeups", ignore_errors=True)
    best = {}
    for k, f in enumerate(sorted(p for p in photos.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))):
        lays = RV.closeup_layouts(ImageOps.exif_transpose(Image.open(f)).convert("RGB"), rack, switches, run / "closeups" / f"photo{k + 1}", mapped=mapped)
        for i, lay in lays.items():
            m = lay["match"]
            print(f"{f.name} -> D{i} by {m['by']} ({m['inliers']} points): {lay.get('vendor')} {lay.get('model')} fitted={lay.get('fitted')}", flush=True)
            rank = (m["by"] == "rack image", bool(lay.get("fitted")), bool(lay.get("model")), m["inliers"] or 0)
            if i not in best or rank > best[i][0]:
                best[i] = (rank, lay, f.name)
    for dv in r["devices"]:
        if dv["index"] in best or not keep_others:
            dv.pop("closeup", None)
        if dv["index"] in best:
            dv["closeup"] = RV.closeup_record(best[dv["index"]][1], run, dv["index"]) | {"photo": best[dv["index"]][2]}
            c = dv["closeup"]
            print(f"D{dv['index']} {dv['top']} keeps {c['photo']}: {c['vendor']} {c['model']} fitted={c['fitted']}")
    reclassify(r, run)
    (run / "result.json").write_text(json.dumps(r, indent=1))
    shutil.rmtree(run / "closeups", ignore_errors=True)     # the mapper's work files; the pictures kept are closeup_D<i>_*.jpg


def reclassify(r, run):
    """Step 8 again on the close-ups: a device covered by a close-up is classified on the close-up's cut (a 1080p photo,
    where the frames are 720p), and that answer replaces the frames' answer when it is more confident or when the frames
    said Other (user, 2026-10-23: the frames called the firewall Other). The frames' own answer is kept as "frames"."""
    from PIL import Image
    pic = lambda dv: (dv.get("closeup") or {}).get("pictures", {}).get("device") or (dv.get("closeup") or {}).get("pictures", {}).get("cut")
    jobs = [(dv, run / pic(dv)) for dv in r["devices"] if pic(dv) and (run / pic(dv)).exists() and dv.get("classification")]
    if not jobs:
        return
    res = P.classify_crops([Image.open(f).convert("RGB") for _, f in jobs])
    for (dv, f), x in zip(jobs, res):
        c = dv["classification"]; ans = {"label": x["label"], "class": x["class"], "prob": round(x["prob"], 3), "file": f.name}
        c["closeup"] = ans
        c.setdefault("frames_answer", {k: c.get(k) for k in ("label", "class", "prob", "frame", "rank", "file")})
        better = x["label"] != "Other" and (c.get("label") == "Other" or x["prob"] > (c.get("prob") or 0))
        print(f'D{dv["index"]} {dv["top"]} close-up says {x["label"]} {round(100 * x["prob"])} % (frames: {c.get("label")} {round(100 * (c.get("prob") or 0))} %)'
              + (" -> kept" if better else ""))
        if better:
            c.update(label=x["label"], **{"class": x["class"]}, prob=ans["prob"], source="close-up", file=f.name, frame=None, rank=None)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--reclassify"]:                    # only step 8 again, on the close-ups already kept with a run
        run = pathlib.Path(sys.argv[2]); r = json.loads((run / "result.json").read_text()); reclassify(r, run)
        (run / "result.json").write_text(json.dumps(r, indent=1))
    else:
        main(*sys.argv[1:3], keep_others="--keep" in sys.argv)      # --keep: other devices keep their close-ups (a re-run on one photo)
