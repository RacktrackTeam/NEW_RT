"""Accuracy with and without the test photos taken from a side angle.

    python3 scripts/occupancy/ensemble_angle.py

The side-angle photos were picked by eye (models/v2/side_angle_photos.json). Every unit from those photos is left
out, right and wrong alike, so the number is fair and not just "drop the mistakes". Writes `angle` (counts for
the front-facing test units, with the original labels) into the portal's data.json and marks each mistake from a
side-angle photo with `side: true`; the page applies the label fixes and the person's calls on top.
"""
import json
import pathlib

import sys

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "webapp" / "static" / "ensemble_portal" / "data.json"
SIDE = ROOT / "models" / "v2" / "side_angle_photos.json"
CUT = 0.65
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ensemble_portal_data as portal  # noqa: E402


def main():
    side = set(json.loads(SIDE.read_text())["photos"])
    man = pd.read_csv(ROOT / "data/datasets/occupancy" / "manifest.csv")
    test = man[man.split.isin(["test", "removed_from_test"])].set_index("crop")
    ens = np.load(ROOT / "results" / "occupancy" / "probs" / "ensemble.npz", allow_pickle=False)
    files, y, p = ens["files_test"].astype(str), ens["y_test"], ens["p_test"]
    photo = np.array([pathlib.Path(s).name for s in test.loc[files, "source_image"]])
    is_side = np.isin(photo, list(side))
    K = portal.kept(files)
    is_side &= K
    front = ~is_side & K
    right = (p >= 0.5).astype(int) == y
    unc = np.maximum(p, 1 - p) < CUT
    data = json.loads(DATA.read_text())
    data["angle"] = {"how": json.loads(SIDE.read_text())["how"], "side_photos": len(side), "photos": int(len(set(photo[K]))),
                     "side_units": int(is_side.sum()), "units": int(front.sum()), "right": int((front & right).sum()),
                     "uncertain": int((front & unc).sum()), "decided": int((front & ~unc).sum()),
                     "decided_right": int((front & ~unc & right).sum())}
    index = {f: i for i, f in enumerate(files)}
    for u in data["units"]:
        u["side"] = bool(is_side[index[u["file"]]])
    data["angle"]["side_photos"] = int(len(set(photo[is_side])))
    DATA.write_text(json.dumps(data, indent=1))
    print(data["angle"])


if __name__ == "__main__":
    main()
