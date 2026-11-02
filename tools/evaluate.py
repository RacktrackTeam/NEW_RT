"""How well each stored video run read its rack: the run's devices (unit, kind, make, model) against the ground truth in
modules/sources/ground_truth, then the same run through the drift compare (src/source_db.py drift) on a record made from that ground
truth, to check that the two agree. No model is loaded: it reads the runs' result.json files only.

  _cache/venv/bin/python tools/evaluate.py            prints the numbers per video
  _cache/venv/bin/python tools/evaluate.py --md       also writes docs/EVALUATION.md

The pairing here is its own (a ground-truth device and a run device are the same device when their units overlap most), not the drift's
(start unit within one), so a disagreement points at a mistake in one of them.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import source_db as SDB  # noqa: E402

GT = ROOT / "modules" / "sources" / "ground_truth"
VIDEOS = [  # video, its ground truth, its stored run
    ("vdo1.mp4 (office rack SP-HYD-RM01-R01-R1)", "vdo1.json", "91af02c09bbc"),
    ("WhatsApp 21.43.13 (NETWORK RACK 1)", "whatsapp_2143.json", "28c3c52cd91e"),
    ("WhatsApp 23.19.19 (NETWORK RACK 2)", "whatsapp_2319.json", "501bcd981b64"),
]


def record_of(truth):
    """The ground truth as the record the drift reads (the shape src/source_db.py stores for every source)."""
    return [{"name": d["name"], "device_key": d["name"], "kind": d.get("kind") or SDB._kind(d.get("role")), "manufacturer": d.get("manufacturer"),
             "model": d.get("model"), "serial": d.get("serial"), "ru_start": d.get("position"), "ru_height": d.get("height") or 1} for d in truth["devices"]]


def units(start, height):
    return set(range(start, start + (height or 1)))


def evaluate(truth, result):
    rec = record_of(truth)
    checkable = [r for r in rec if r["ru_start"] is not None and (r["kind"] or "").lower() not in SDB.NOT_SEEN_KINDS]
    run = SDB.run_devices(result)
    # pairing by the largest unit overlap, greedy
    cand = sorted(((len(units(r["ru_start"], r["ru_height"]) & units(s["ru_start"], s["ru_height"])), i, j) for i, r in enumerate(checkable) for j, s in enumerate(run)), reverse=True)
    pr, ps, pairs = set(), set(), []
    for ov, i, j in cand:
        if ov and i not in pr and j not in ps:
            pr.add(i); ps.add(j); pairs.append((checkable[i], run[j]))
    n = {"in the record (checkable)": len(checkable), "in the video": len(run), "found": len(pairs),
         "units right": sum(1 for r, s in pairs if r["ru_start"] == s["ru_start"] and r["ru_height"] == s["ru_height"]),
         "kind read": sum(1 for r, s in pairs if s["kind"]),
         "kind right": sum(1 for r, s in pairs if s["kind"] and SDB._norm(s["kind"]) == SDB._norm(r["kind"])),
         "make and model in the record": sum(1 for r, s in pairs if r["manufacturer"] and r["model"]),
         "make and model read": sum(1 for r, s in pairs if r["manufacturer"] and r["model"] and s["manufacturer"] and s["model"]),
         "make and model right": sum(1 for r, s in pairs if r["manufacturer"] and r["model"] and s["manufacturer"] and s["model"]
                                     and SDB._same(s["manufacturer"], r["manufacturer"]) and SDB._same(s["model"], r["model"])),
         "missing": len(checkable) - len(pairs), "extra": len(run) - len(pairs)}
    detail = {"missing": sorted(f"{r['name']} at {SDB._units(r['ru_start'], r['ru_height'])}" for i, r in enumerate(checkable) if i not in pr),
              "extra": sorted(f"D{s['index']} ({s['kind'] or 'not sure'}) at {SDB._units(s['ru_start'], s['ru_height'])}" for j, s in enumerate(run) if j not in ps),
              "units off": [f"{r['name']}: record {SDB._units(r['ru_start'], r['ru_height'])}, video {SDB._units(s['ru_start'], s['ru_height'])}"
                            for r, s in pairs if (r["ru_start"], r["ru_height"]) != (s["ru_start"], s["ru_height"])],
              "kind wrong": [f"{r['name']}: record {r['kind']}, video {s['kind']}" for r, s in pairs if s["kind"] and SDB._norm(s["kind"]) != SDB._norm(r["kind"])],
              "kind not sure": [f"{r['name']} (record {r['kind']})" for r, s in pairs if not s["kind"]]}
    # the drift on the same record: its rows must say the same
    dr = SDB.drift(result, rec)
    rows = dr["rows"]
    by = lambda t: sum(1 for x in rows if x["type"] == t)  # noqa: E731
    off_names = {x["device"].split(" / ")[0] for x in rows if x["type"] in ("moved", "height")}
    agree = {"found": (dr["checked"], n["found"]), "missing": (by("missing"), n["missing"]), "extra": (by("extra"), n["extra"]),
             "units off": (len(off_names), n["found"] - n["units right"]), "kind wrong": (by("kind"), n["kind read"] - n["kind right"])}
    return n, detail, agree


def main():
    out = []
    for label, gt_name, run in VIDEOS:
        rf = ROOT / "data" / "runs" / run / "result.json"
        if not rf.is_file():
            print(f"{label}: no run"); continue
        n, detail, agree = evaluate(json.loads((GT / gt_name).read_text()), json.loads(rf.read_text()))
        ok = all(a == b for a, b in agree.values())
        print(f"\n{label}   run {run}")
        for k, v in n.items():
            print(f"  {k:32s} {v}")
        for k, v in detail.items():
            if v:
                print(f"  {k}: " + "; ".join(v))
        print("  drift compare agrees: " + ("yes" if ok else "NO ") + "  " + ", ".join(f"{k} {a} (drift) / {b} (evaluation)" for k, (a, b) in agree.items()))
        out.append((label, run, n, detail, agree, ok))
    if "--md" in sys.argv:
        write_md(out)


def write_md(out):
    keys = ["in the record (checkable)", "in the video", "found", "units right", "kind read", "kind right", "make and model in the record",
            "make and model read", "make and model right", "missing", "extra"]
    L = ["# Evaluation: what each stored video run read, against the ground truth", "",
         "Made by `_cache/venv/bin/python tools/evaluate.py --md` from the stored runs' result.json files (no model loaded, no new run). "
         "The ground truth is modules/sources/ground_truth: vdo1 is the NetBox export of the office rack; the two WhatsApp racks were written "
         "from the videos on 2 Nov and still need the owner's confirmation.", "",
         "Checkable means a record device with a unit and a kind the video's models can tell apart: cable managers, shelves and blank panels "
         "are left out (the models have no class for them), and so is a device with no unit in the record. A record device and a run device are "
         "paired when their units overlap most. Units right: same start unit and same height. Kind right counts only where the video gave a kind "
         "(a correction, then the faceplate read, then the picture); a device left Not sure is counted under kind read as not read. Make and model "
         "right: both read and both the same as the record.", "",
         "| | " + " | ".join(o[0] for o in out) + " |", "| --- | " + " | ".join("---" for _ in out) + " |"]
    L += ["| " + k + " | " + " | ".join(str(o[2][k]) for o in out) + " |" for k in keys]
    L += ["| drift compare agrees | " + " | ".join("yes" if o[5] else "no" for o in out) + " |", ""]
    for label, run, n, detail, agree, ok in out:
        L += [f"## {label} (run {run})", ""]
        for k, v in detail.items():
            L.append(f"- {k.capitalize()}: " + ("; ".join(v) if v else "none") + ".")
        L.append("- Drift compare on the same record: " + ", ".join(f"{k} {a} in the drift, {b} here" for k, (a, b) in agree.items()) + ".")
        L.append("")
    (ROOT / "docs" / "EVALUATION.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
