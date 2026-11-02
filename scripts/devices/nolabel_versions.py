"""Recompute every version of the No label list from the saved results, for the report (results/devices/nolabel_versions.json).

  v1  the first list: label_audit.json "missing" flags (RT-EOS finder >= 80 %, no label box overlapping it by 0.3 or more),
      photos of 1 MP or more
  v2  the strict list: RT-EOS finder >= 80 % AND the DEIMv2 detector on the same spot AND on the unit ladder AND label polygons
      under 10 % (our file and the export file), first on the photos of 1 MP or more, then on every photo
  v3  the full list: every DEIMv2 device on the unit ladder with label polygons under 10 %, the finder only as evidence
  v4  v3 plus "no label of any kind nearby" (results/devices/nolabel_strict.json, the list on the review page)

    python3 scripts/devices/nolabel_versions.py
"""
import collections, csv, json, pathlib, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts" / "devices"))
import audit_labels as AL      # noqa: E402
import nolabel_strict as NS    # noqa: E402

RES = ROOT / "results" / "devices"


def main():
    info = {n: (p, lbl, w, h) for n, p, lbl, w, h in AL.photos(AL.SPLITS[0])}
    F = json.load(open(RES / "audit_finder_all.json"))["photos"]; D = json.load(open(RES / "devmodel_all.json"))
    rows = {pathlib.Path(r["file"]).name: r["rows"] for r in json.load(open(RES / "rows_all.json"))}
    man = {r["photo"]: r for r in csv.DictReader(open(AL.DATA / "manifest.csv"))}
    z = zipfile.ZipFile(AL.DATA / "provenance" / "export_labels.zip"); zn = z.namelist()
    A = json.load(open(RES / "label_audit.json"))
    big = lambda n: info[n][2] * info[n][3] >= AL.REVIEW_MIN_PIXELS
    masks = {}

    def mask(name):
        if name not in masks:
            p, lbl, w, h = info[name]; ours = [l.split() for l in lbl.read_text().split("\n") if l.strip()]
            q = pathlib.PurePosixPath(man[name]["export_image"])
            key = next((k for k in zn if k.startswith(man[name]["export"]) and k.endswith(f"labels/{q.stem}.txt")), None)
            ex = [l.split() for l in z.read(key).decode().split("\n") if l.strip()] if key else []
            masks[name] = (NS.poly_mask(ours, w, h), NS.poly_mask(ex, w, h))
        return masks[name]

    def ladder(name, b):
        lad = rows.get(name, [])
        if not lad:
            return False
        lx0 = min(r["box"][0] for r in lad); lx1 = max(r["box"][2] for r in lad)
        if max(0, min(b[2], lx1) - max(b[0], lx0)) / max(1, b[2] - b[0]) < NS.LADDER_IN:
            return False
        return any(max(0, min(b[3], r["box"][3]) - max(b[1], r["box"][1])) / max(1, r["box"][3] - r["box"][1]) >= NS.ROW_COVER for r in lad)

    def free(name, b):
        p, lbl, w, h = info[name]; mo, me = mask(name)
        return NS.cover(mo, b, w, h) < NS.POLY_MAX and NS.cover(me, b, w, h) < NS.POLY_MAX

    out = {}
    v1 = [f for f in A["flags"] if f["check"] == "missing" and f["photo"] in info and big(f["photo"])]
    out["v1"] = {"places": len(v1), "photos": len({f["photo"] for f in v1})}
    # v2 on the v1 places (1 MP or more)
    s = [0, 0, 0, 0]
    for f in v1:
        b = f["finder"][:4]; s[0] += 1
        if not any(d[4] >= NS.DEIM_SCORE and NS.iou(b, d) >= NS.MATCH_IOU for d in D.get(f["photo"], [])):
            continue
        s[1] += 1
        if not ladder(f["photo"], b):
            continue
        s[2] += 1
        if free(f["photo"], b):
            s[3] += 1
    out["v2_hires"] = s
    # v2 on every photo
    s = [0, 0, 0, 0]; v2 = []
    for name in info:
        bs = sorted([b for b in F.get(name, []) if b[4] >= AL.FINDER_SURE and b[5] >= 2], key=lambda b: -b[4]); keep = []
        for b in bs:
            if all(NS.iou(b, k) <= 0.5 for k in keep):
                keep.append(b)
        for b in keep:
            s[0] += 1
            if not any(d[4] >= NS.DEIM_SCORE and NS.iou(b, d) >= NS.MATCH_IOU for d in D.get(name, [])):
                continue
            s[1] += 1
            if not ladder(name, b):
                continue
            s[2] += 1
            if free(name, b):
                s[3] += 1; v2.append((name, b))
    out["v2_all"] = s; out["v2_photos"] = len({n for n, _ in v2}); out["v2_hires_places"] = sum(big(n) for n, _ in v2)
    # why v2 missed devices: every DEIMv2 device on the ladder with no label polygon, by what the finder said
    why = collections.Counter(); v3 = []
    for name in info:
        ds = sorted([d for d in D.get(name, []) if d[4] >= NS.DEIM_SCORE], key=lambda d: -d[4]); keep = []
        for d in ds:
            if all(NS.iou(d, k) <= 0.5 for k in keep):
                keep.append(d)
        for d in keep:
            if not ladder(name, d) or not free(name, d):
                continue
            fb = max(((NS.iou(d, f), f) for f in F.get(name, []) if f[5] >= 2), key=lambda t: t[0], default=(0, None))
            c = fb[1][4] if fb[0] >= NS.MATCH_IOU else 0
            why["finder at least 80 %" if c >= .8 else "finder 50 to 80 %" if c >= .5 else "finder under 50 %" if c > 0 else "finder silent"] += 1
            v3.append(name)
    out["v3"] = {"places": len(v3), "photos": len(set(v3)), "by_finder": dict(why)}
    S = json.load(open(RES / "nolabel_strict.json"))
    out["v4"] = {"places": len(S["kept"]), "photos": len({k["photo"] for k in S["kept"]}), "stages": S["stages"],
                 "evidence": S["evidence"], "cover": dict(collections.Counter("0 %" if k["polygon_cover"] == 0 else "under 5 %" if k["polygon_cover"] < .05 else "5 to 10 %" for k in S["kept"]))}
    ex = "Device_annotation_Trained_2511_V1_train_3372.jpg"
    out["example"] = {"photo": ex, "v2": sum(1 for n, _ in v2 if n == ex), "v4": sum(1 for k in S["kept"] if k["photo"] == ex),
                      "labels": len([l for l in info[ex][1].read_text().split("\n") if l.strip()])}
    json.dump(out, open(RES / "nolabel_versions.json", "w"), indent=1); print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
