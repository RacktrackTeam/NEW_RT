"""Data for the portal's "Two test sets" tab: (1) every test unit we have, (2) photos of at least 1 megapixel.

    python3 scripts/occupancy/polygon_test.py && python3 scripts/occupancy/two_test_sets.py

Units are cut along their hand-drawn polygons (scripts/occupancy/polygon_test.py); the box cut-out is kept for comparison.
Set 1: the test split (labels fixed) + the new labelled photos of the "test" folder. Set 2: the photos of set 1 of
at least 1 megapixel. For each set: accuracy, and every wrong answer with the by-eye check of why it is wrong.
Writes `testsets` into data.json.
"""
import json
import pathlib
import sys

import numpy as np
import pandas as pd
from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ensemble_portal_data as portal  # noqa: E402
sys.path.insert(0, str(ROOT / "src"))
import side_angle  # noqa: E402
import polygon_test as pt  # noqa: E402

OUT = ROOT / "webapp" / "static" / "ensemble_portal"
X = ROOT / "data/test_sets/occupancy/extra"
CUT = 0.65

# By-eye check of wrong answers that are not in the Ensemble mistake review (9 Oct 2026).
WHY = {
    "Screenshot-2026-05-29-152831#L45": "One thin 1U server in a dense stack of identical servers, in a small 358 x 713 screenshot: the unit is only 14 px tall, so its front detail is lost.",
    "istockphoto-495604876#L09": "A dark switch at the very top of the photo, cut off by the photo edge and covered by a bundle of white cables. The label file has this switch twice (L09 and L24).",
    "istockphoto-495604876#L24": "The same switch as L09, labelled a second time: dark, under cables and cut off at the top edge.",
    "Server__test__Device_annotation_Trained_2511": "The polygon was drawn as a thin slanted sliver across the server, not around it, so the cut the model sees is a dark strip with almost nothing of the server's front.",
    "Switch__test__Device_annotation_Trained_2511": "The polygon was drawn as a thin slanted sliver across the switch, so the cut is mostly the black cable-management panel above it and a strip of dark ports.",
    "Closed_Unit__test__Device_annotation_Trained_2511_V1_train_3448__15": "A patch panel full of blue cables, but its polygon is a thin slanted sliver: the straightened cut shows only cables, which look like a cable bundle in an empty gap.",
}


def why_for(uid, review):
    for k, v in WHY.items():
        a, _, b = k.partition("#")
        if uid.startswith(a) and (not b or uid.endswith("#" + b)):
            return v, "Model wrong"
    u = review.get(uid)
    if u:
        # keep what the unit looks like; drop review remarks about its old outcome ("flagged", "that worked"), which can change with the model
        w = ". ".join(x.rstrip(".") for x in str(u.get("why") or u.get("reason") or "").split(". ")
                      if "that worked" not in x and "flagged" not in x.lower() and "right outcome" not in x)
        w = w + "." if w and not w.endswith(".") else w
        return w, "Model wrong"
    return "", "Model wrong"


def pictures(r, name):
    """The unit in its photo with the polygon drawn, and the straightened cut the model saw."""
    lab = (r.photo.replace("/images/", "/labels/").rsplit(".", 1)[0] + ".txt") if r.set == "dataset" else \
        str(ROOT / "data" / "test_sets" / "units" / "device_labels" / (pathlib.Path(r.photo).stem + ".txt"))
    xy = pt.label_xy(lab, int(r.line))
    im = ImageOps.exif_transpose(Image.open(r.photo)).convert("RGB")
    c = pt.corners(xy, *im.size)
    if c is None:
        cx, cy, w, h = xy
        W, H = im.size
        c = np.array([[(cx - w / 2) * W, (cy - h / 2) * H], [(cx + w / 2) * W, (cy - h / 2) * H],
                      [(cx + w / 2) * W, (cy + h / 2) * H], [(cx - w / 2) * W, (cy + h / 2) * H]])
    x1, y1 = c.min(0)
    x2, y2 = c.max(0)
    hh = max(y2 - y1, 20)
    ox, oy = max(0, int(x1 - 0.1 * (x2 - x1))), max(0, int(y1 - 2.2 * hh))
    a = im.crop((ox, oy, min(im.width, int(x2 + 0.1 * (x2 - x1))), min(im.height, int(y2 + 2.2 * hh)))).copy()
    portal.draw_polygon(a, portal.label_polygon(r.photo, r.line, im.width, im.height), ox, oy, max(3, a.width // 110))
    if a.width < 360:
        a = a.resize((360, int(a.height * 360 / a.width)), Image.LANCZOS)
    a.thumbnail((640, 420))
    a.save(OUT / "img" / f"{name}_ctx.jpg", quality=84)
    cut = pt.polygon_crop(im, xy)
    if cut.width < 300:
        cut = cut.resize((300, max(8, int(cut.height * 300 / cut.width))), Image.LANCZOS)
    cut.thumbnail((480, 200))
    cut.save(OUT / "img" / f"{name}_cut.jpg", quality=86)
    return f"img/{name}_ctx.jpg", f"img/{name}_cut.jpg"


def need(d, side_rule=True):
    """How sure the model must be to answer: 65%, or side_angle.SIDE_SURE on a photo flagged as a side angle."""
    return np.where(d.side_flag, side_angle.SIDE_SURE, CUT) if side_rule else np.full(len(d), CUT)


def score(d, col, side_rule=True):
    pred = np.where(d[col] >= .5, "occupied", "unoccupied")
    sure = np.maximum(d[col], 1 - d[col])
    ans = sure >= need(d, side_rule)
    return {"units": len(d), "photos": int(d.photo.nunique()), "occupied": int((d.truth == "occupied").sum()),
            "unoccupied": int((d.truth == "unoccupied").sum()), "right_alone": int((pred == d.truth).sum()),
            "answered": int(ans.sum()), "uncertain": int((~ans).sum()), "wrong_answers": int((ans & (pred != d.truth)).sum()),
            "mistakes_sent_to_person": int((~ans & (pred != d.truth)).sum()),
            "side_photos": int(d[d.side_flag].photo.nunique()), "side_units": int(d.side_flag.sum())}


def main():
    data = json.loads((OUT / "data.json").read_text())
    review = {u["file"]: u for u in data["units"] if not u["leaning_right"]}
    d = pd.read_csv(X / "polygon_units.csv")
    # labels as they are now (a correction in the portal may have moved one since the units were scored)
    test = pd.read_csv(ROOT / "data/datasets/occupancy" / "manifest.csv").query("split == 'test'").set_index("crop").folder
    d = d[(d.set == "new") | d.id.isin(test.index)].copy()
    ds = d.set == "dataset"
    d.loc[ds, "truth"] = test.loc[d.loc[ds, "id"]].values
    # side-angle flag per photo: cross-validated for the test-split photos (the detector never saw that photo),
    # the final detector for the new photos (it never saw them either)
    cv = pd.read_csv(X / "side_angle_scores.csv").set_index("photo").p_side
    flags = {}
    for ph in d.photo.unique():
        n = pathlib.Path(ph).name
        p = cv[n] if n in cv.index else side_angle.p_side(ImageOps.exif_transpose(Image.open(ph)))
        flags[ph] = bool(p >= side_angle.SIDE_P)
    d["side_flag"] = d.photo.map(flags)
    sets = {}
    for key, s in (("overall", d), ("highres", d[d.hires])):
        res = score(s, "p_poly")
        res["box"] = score(s, "p_box")
        res["without_side_rule"] = score(s, "p_poly", side_rule=False)
        pred = np.where(s.p_poly >= .5, "occupied", "unoccupied")
        sure = np.maximum(s.p_poly, 1 - s.p_poly)
        items = []
        for k, (i, r) in enumerate(s[(pred != s.truth) & (sure >= need(s))].iterrows()):
            why, verdict = why_for(r.id, review)
            u = review.get(r.id)
            name = f"ts_{abs(hash(r.id)) % 10**8}"
            ctx, cut = pictures(r, name)
            no = u["review_no"] if u else r.id.split("#")[-1] if "#" in r.id else r.id.split("__")[0]
            items.append({"no": no, "truth": r.truth,
                          "model": "occupied" if r.p_poly >= .5 else "unoccupied", "sure": round(float(max(r.p_poly, 1 - r.p_poly)), 2),
                          "box_wrong": bool((r.p_box >= .5) != (r.truth == "occupied") and max(r.p_box, 1 - r.p_box) >= CUT),
                          "source": "new photo (test folder)" if r.set == "new" else "test split", "side": bool(r.side_flag),
                          "verdict": verdict, "why": why, "img": ctx, "cut": cut})
        res["items"] = sorted(items, key=lambda m: -m["sure"])
        sets[key] = res
    data["testsets"] = sets
    (OUT / "data.json").write_text(json.dumps(data, indent=1))
    for k, s in sets.items():
        print(k, {a: b for a, b in s.items() if a not in ("items", "box")}, "| box:", s["box"]["wrong_answers"], "wrong")
        for m in s["items"]:
            print("   ", m["no"], m["truth"], "->", m["model"], m["sure"], "|", m["why"][:70])


if __name__ == "__main__":
    main()
