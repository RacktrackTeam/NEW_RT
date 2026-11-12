"""The proofs behind the No label list, for the No Label Report (results/devices/nolabel_proof.json).

  detector   how far the DEIMv2 device detector agrees with the network team: of its devices on the rack, how many sit on a
             label polygon; of the team's device labels, how many it finds
  removed    examples of what each test removed (other racks, labelled devices, labels reaching in)
  sensitivity  the count for other thresholds of the polygon test and the "label nearby" test
  sample     a random sample per evidence group for the hand check (pictures in reports/devices/nolabel_report/spot/)
  audit      a random audit table with every value, and one worked example with every label line of its file

    python3 scripts/devices/nolabel_proof.py
"""
import collections, csv, json, pathlib, random, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts" / "devices"))
import audit_labels as AL      # noqa: E402
import nolabel_strict as NS    # noqa: E402
from PIL import Image, ImageDraw, ImageOps   # noqa: E402

RES = ROOT / "results" / "devices"; SPOT = ROOT / "reports" / "devices" / "nolabel_report" / "spot"
NAMES = ["Closed Unit", "Empty", "Firewall", "Gateway", "Load Balancer", "PDU", "Patch Panel", "Router", "Server", "Storage unit", "Switch", "UPS"]


def main():
    info = {n: (p, lbl, w, h) for n, p, lbl, w, h in AL.photos(AL.SPLITS[0])}
    F = json.load(open(RES / "audit_finder_all.json"))["photos"]; D = json.load(open(RES / "devmodel_all.json"))
    rows = {pathlib.Path(r["file"]).name: r["rows"] for r in json.load(open(RES / "rows_all.json"))}
    man = {r["photo"]: r for r in csv.DictReader(open(AL.DATA / "manifest.csv"))}
    z = zipfile.ZipFile(AL.DATA / "provenance" / "export_labels.zip"); zn = z.namelist()
    S = json.load(open(RES / "nolabel_strict.json")); K = S["kept"]
    out = {}
    det = collections.Counter(); sens = []; removed = collections.defaultdict(list)
    for name, (p, lbl, w, h) in info.items():
        ours = [l.split() for l in lbl.read_text().split("\n") if l.strip()]
        ds = sorted([d for d in D.get(name, []) if d[4] >= NS.DEIM_SCORE], key=lambda d: -d[4]); keep = []
        for d in ds:
            if all(NS.iou(d, k) <= .5 for k in keep):
                keep.append(d)
        # the team's device labels found by the detector
        for v in ours:
            c = int(v[0]); xy = [float(t) for t in v[1:]]
            if c < 2 or len(xy) < 6:
                continue
            g = (min(xy[0::2]) * w, min(xy[1::2]) * h, max(xy[0::2]) * w, max(xy[1::2]) * h)
            det["device labels"] += 1
            if any(NS.iou(g, d) >= .5 for d in keep):
                det["device labels found by the detector"] += 1
        lad = rows.get(name, [])
        if not keep or not lad:
            continue
        q = pathlib.PurePosixPath(man[name]["export_image"])
        key = next((k for k in zn if k.startswith(man[name]["export"]) and k.endswith(f"labels/{q.stem}.txt")), None)
        ex = [l.split() for l in z.read(key).decode().split("\n") if l.strip()] if key else []
        mo, me = NS.poly_mask(ours, w, h), NS.poly_mask(ex, w, h); lb = NS.label_boxes(ours, w, h) + NS.label_boxes(ex, w, h)
        lx0 = min(r["box"][0] for r in lad); lx1 = max(r["box"][2] for r in lad)
        for d in keep:
            inside = max(0, min(d[2], lx1) - max(d[0], lx0)) / max(1, d[2] - d[0])
            on = inside >= NS.LADDER_IN and any(max(0, min(d[3], r["box"][3]) - max(d[1], r["box"][1])) / max(1, r["box"][3] - r["box"][1]) >= NS.ROW_COVER for r in lad)
            if not on:
                removed["not on the rack"].append((name, d[:4])); continue
            co, ce = NS.cover(mo, d, w, h), NS.cover(me, d, w, h); nb = max((NS.near(d, g) for g in lb), default=0)
            det["detector devices on the rack"] += 1
            if co >= .5:
                det["on a label polygon (half or more covered)"] += 1
            sens.append((max(co, ce), nb))
            if max(co, ce) >= NS.POLY_MAX:
                det["covered by a label polygon"] += 1
                if 0.5 <= co < 0.9 and len(removed["covered by a label polygon"]) < 400:
                    removed["covered by a label polygon"].append((name, d[:4]))
            elif nb >= NS.BOX_NEAR:
                removed["a label reaches into it"].append((name, d[:4]))
    out["detector"] = dict(det)
    grid = {}
    for pm in (0.05, 0.10, 0.20, 0.30):
        for bn in (0.20, 0.30, 0.50, 1.01):
            grid[f"{pm}|{bn}"] = sum(1 for c, n in sens if c < pm and n < bn)
    out["sensitivity"] = grid
    random.seed(11)
    out["removed"] = {k: random.sample(v, min(6, len(v))) for k, v in removed.items()}
    out["removed_counts"] = {k: len(v) for k, v in removed.items()}
    # hand-check sample: 30 per evidence group
    SPOT.mkdir(parents=True, exist_ok=True)
    for f in SPOT.glob("*.jpg"):
        f.unlink()
    samp = {}
    for g in ("both sure", "both see it", "one detector"):
        pool = [i for i, k in enumerate(K) if k["evidence"] == g]
        samp[g] = random.sample(pool, 30)
        for j, i in enumerate(samp[g]):
            k = K[i]; p, lbl, w, h = info[k["photo"]]; b = k["box"]
            img = ImageOps.exif_transpose(Image.open(p)).convert("RGB"); bw, bh = b[2] - b[0], b[3] - b[1]
            x0, y0, x1, y1 = max(0, b[0] - .2 * bw), max(0, b[1] - 1.2 * bh), min(w, b[2] + .2 * bw), min(h, b[3] + 1.2 * bh)
            c = img.crop((int(x0), int(y0), int(x1), int(y1))); s = min(360 / c.width, 220 / c.height, 2.0)
            c = c.resize((max(1, round(c.width * s)), max(1, round(c.height * s))), Image.LANCZOS); dr = ImageDraw.Draw(c)
            dr.rectangle([(b[0] - x0) * s - 3, (b[1] - y0) * s - 3, (b[2] - x0) * s + 3, (b[3] - y0) * s + 3], outline=(200, 40, 170), width=2)
            c.save(SPOT / f"{g.replace(' ', '')}_{j:02d}.jpg", quality=85)
    out["sample"] = samp
    # audit table and a worked example
    audit = random.sample(range(len(K)), 25)
    out["audit"] = audit
    ex = next(i for i, k in enumerate(K) if k["photo"] == "Device_annotation_Trained_2511_V1_train_3372.jpg")
    k = K[ex]; p, lbl, w, h = info[k["photo"]]; b = k["box"]; lines = []
    for n, v in enumerate([l.split() for l in lbl.read_text().split("\n") if l.strip()], 1):
        m = NS.poly_mask([v], w, h)
        g = NS.label_boxes([v], w, h)[0]
        lines.append({"line": n, "class": NAMES[int(v[0])], "polygon_cover": round(NS.cover(m, b, w, h), 4), "box_near": round(NS.near(b, g), 4)})
    out["worked"] = {"index": ex, "photo": k["photo"], "box": b, "lines": lines}
    json.dump(out, open(RES / "nolabel_proof.json", "w"), indent=1)
    print(json.dumps({k: v for k, v in out.items() if k in ("detector", "sensitivity", "removed_counts")}, indent=1))


if __name__ == "__main__":
    main()
