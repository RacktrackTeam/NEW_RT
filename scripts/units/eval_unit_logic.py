"""Measure the no-model unit ladder (src/unit_logic.py) against hand-labelled units.

    python3 scripts/units/select_front_clear.py                       # once: builds the test set + truth
    python3 scripts/units/eval_unit_logic.py --stages all --out results/units/eval
    python3 scripts/units/eval_unit_logic.py PHOTO_OR_FOLDER ... --out DIR     # ad-hoc photos, no truth

With --truth (default: data/test_sets/units/test_set.json) every photo in the test set is run
through each requested stage and graded against the labelled unit boundaries:
  count_exact / count_within_1   unit count against the labelled count
  pitch_err                      unit height error, as a fraction of the labelled pitch
  lines_within_quarter           share of labelled boundaries with a logic line within UNIT_TOL_U
  worst_offset_u                 largest distance from a labelled boundary to the nearest logic line
  top_err_u / bottom_err_u       where the ladder starts / ends against the labelled mounting space
Writes <out>/<stage>/overlays/*.jpg (green = logic, white ticks = labels), <out>/<stage>/per_photo.json
and <out>/summary.json with one block per stage. Also <out>/original/*.jpg: the plain photos.
"""
import argparse
import json
import os
import pathlib
import sys

import cv2
import numpy as np
from PIL import Image, ImageOps

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
import unit_logic  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def photos_from_args(args):
    for a in args:
        p = pathlib.Path(a)
        if p.is_dir():
            yield from sorted(q for q in p.iterdir() if q.suffix.lower() in EXTS)
        elif p.suffix.lower() in EXTS:
            yield p


def load_bgr(path):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)


UNIT_TOL_U = 0.5    # a drawn unit is right when its top and bottom are both within half a unit of a labelled unit's: every
                    # device then keeps its unit number. Was 0.25 until 2026-10-13; the hand-drawn lines are only accurate to
                    # about 0.13 U, so the quarter-unit rule punished label noise (user: "don't keep strict rules").


def unit_matches(rows, truth):
    """For every drawn unit: True when it sits on a labelled unit (top and bottom within UNIT_TOL_U).
    Also the number of labelled units that got such a match (each labelled unit counts once)."""
    tb = np.array(truth["boundaries"], float)
    q = truth["pitch"]
    ok = []
    hit = set()
    for r in rows:
        y1, y2 = r["box"][1], r["box"][3]
        i = int(np.argmin(np.abs(tb[:-1] - y1)))
        good = abs(tb[i] - y1) <= UNIT_TOL_U * q and abs(tb[i + 1] - y2) <= UNIT_TOL_U * q
        ok.append(bool(good))
        if good:
            hit.add(i)
    return ok, len(hit)


def fill_between(ok):
    """The team's rule since 2026-10-12: when drawn units above and below are right, every drawn unit
    between them counts as right too (inside a big device the labelled lines are only a guess)."""
    idx = [k for k, v in enumerate(ok) if v]
    return [bool(idx) and idx[0] <= k <= idx[-1] for k in range(len(ok))]


def grade(rows, info, truth):
    """Compare one ladder with the labelled units of the same photo.

    The one number for the report is units_right: drawn units that sit on a labelled unit, plus every
    drawn unit between two such units (fill_between), at most the labelled count, divided by the larger
    of (labelled units, drawn units) so that drawing far too many units does not score.
    units_right_strict is the older rule: labelled units that got a correctly placed drawn unit. The other fields explain why a photo scored as it did.
    """
    tb = np.array(truth["boundaries"], float)
    n_true = truth["n_units"]
    q_true = truth["pitch"]
    g = {"n_true": n_true, "n_logic": len(rows)}
    if not rows:
        g.update(units_right=0.0, units_right_strict=0.0, units_matched=0, units_filled=0, count_exact=False, count_within_1=False, pitch_err=None,
                 lines_within_quarter=0.0, worst_offset_u=None, top_err_u=None, bottom_err_u=None)
        return g
    rows = sorted(rows, key=lambda r: r["box"][1])
    ok, matched = unit_matches(rows, truth)
    filled = min(sum(fill_between(ok)), n_true)
    lines = np.array([rows[0]["box"][1]] + [r["box"][3] for r in rows], float)
    d = np.abs(tb[:, None] - lines[None, :]).min(axis=1) / q_true
    pitch = info.get("pitch") or float(np.median([r["box"][3] - r["box"][1] for r in rows]))
    g.update(units_right=filled / max(n_true, len(rows)), units_right_strict=matched / max(n_true, len(rows)), units_matched=matched, units_filled=filled,
             count_exact=len(rows) == n_true, count_within_1=abs(len(rows) - n_true) <= 1,
             pitch_err=float(pitch / q_true - 1), lines_within_quarter=float((d <= UNIT_TOL_U).mean()),
             worst_offset_u=float(d.max()), top_err_u=float((lines[0] - truth["top"]) / q_true),
             bottom_err_u=float((lines[-1] - truth["bottom"]) / q_true))
    return g


def summarise(per):
    graded = [p["grade"] for p in per if p.get("grade")]
    n = len(graded)
    with_ladder = [g for g in graded if g["n_logic"]]
    pe = np.array([abs(g["pitch_err"]) for g in with_ladder]) if with_ladder else np.array([])
    return {
        "photos": n,
        "units_right_mean": float(np.mean([g["units_right"] for g in graded])) if n else 0.0,
        "units_right_strict_mean": float(np.mean([g.get("units_right_strict", g["units_right"]) for g in graded])) if n else 0.0,
        "photos_all_units_right": sum(g["units_right"] >= 0.999 for g in graded),
        "photos_90pct_units_right": sum(g["units_right"] >= 0.9 for g in graded),
        "ladder_found": len(with_ladder),
        "count_exact": sum(g["count_exact"] for g in graded),
        "count_within_1": sum(g["count_within_1"] for g in graded),
        "pitch_within_5pct": int((pe <= 0.05).sum()),
        "pitch_within_10pct": int((pe <= 0.10).sum()),
        "lines_within_quarter_mean": float(np.mean([g["lines_within_quarter"] for g in graded])) if n else 0.0,
        "all_lines_within_quarter": sum(g["lines_within_quarter"] >= 0.999 for g in graded),
        "median_worst_offset_u": float(np.median([g["worst_offset_u"] for g in with_ladder])) if with_ladder else None,
        # where the ladder ends against the labelled mounting space, top and bottom separately
        # (negative top / positive bottom = the ladder runs past the rack)
        "top_runs_past_by_1u": sum(g["top_err_u"] <= -1 for g in with_ladder),
        "top_stops_short_by_1u": sum(g["top_err_u"] >= 1 for g in with_ladder),
        "bottom_runs_past_by_1u": sum(g["bottom_err_u"] >= 1 for g in with_ladder),
        "bottom_stops_short_by_1u": sum(g["bottom_err_u"] <= -1 for g in with_ladder),
        "median_abs_top_err_u": float(np.median([abs(g["top_err_u"]) for g in with_ladder])) if with_ladder else None,
        "median_abs_bottom_err_u": float(np.median([abs(g["bottom_err_u"]) for g in with_ladder])) if with_ladder else None,
    }


def model_rows(bgr):
    """The old detector's ladder (weights/rack_unit_finder.pt + the RT-EOS ladder rules), for comparison only."""
    import unit_grid as ug
    model = ug.load_model(str(ROOT / "weights/rack_unit_finder.pt"))
    small, f = ug.shrink_for_inference(bgr)
    res = model.predict(small, conf=0.25, imgsz=768, verbose=False)
    units, racks, rails = ug._unit_rows_from_result(res[0], model.names)
    for u in units:
        u["box"] = [v * f for v in u["box"]]
    racks = [[v * f for v in r] for r in racks]
    rails = [[v * f for v in r] for r in rails]
    rows = ug.ladder_from_unit_boxes(units, racks, bgr.shape[0], bgr.shape[1], rails) if units else []
    info = {"ok": bool(rows), "stage": "model", "reason": "" if rows else "detector found no units"}
    if rows:
        info.update(x_left=rows[0]["box"][0], x_right=rows[0]["box"][2],
                    pitch=float(np.median([r["box"][3] - r["box"][1] for r in rows])))
    return rows, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inputs", nargs="*", help="photos or folders; omit to use the truth file's test set")
    ap.add_argument("--truth", default=str(ROOT / "data" / "test_sets" / "units" / "test_set.json"))
    ap.add_argument("--out", default=str(ROOT / "results" / "units" / "eval"))
    ap.add_argument("--stages", default="current", help="comma list of stages, or 'all'; add 'model' for the old detector")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-overlays", action="store_true")
    ap.add_argument("--regrade", action="store_true", help="re-grade the stored ladders (<out>/<stage>/per_photo.json) without running the stages")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    import unit_persp
    # the approaches that can still be run; every other approach's result is kept in results/units/trials/trials.json
    runnable = {"grid_vote": unit_persp.method_grid_vote, "feature_ends": unit_persp.method_feature_ends, "rteos_logic": unit_persp.method_rteos_logic}
    if args.stages == "all":
        stages = list(runnable) + ["mate_map", "mate_seg", "model"]   # feature_ends runs both the logic and RT-EOS units
    else:
        stages = args.stages.split(",")

    truth_by_file = {}
    if args.inputs:
        items = [(p, None) for p in photos_from_args(args.inputs)]
    else:
        ts = json.load(open(args.truth))
        items = [(ROOT / t["file"], t) for t in ts["photos"]]
        truth_by_file = {t["file"]: t for t in ts["photos"]}
    if args.limit:
        items = items[:args.limit]

    (out / "original").mkdir(parents=True, exist_ok=True)
    summary = {"stages": {}, "photos": len(items)}
    cache = {}
    for stage in stages:
        (out / stage / "overlays").mkdir(parents=True, exist_ok=True)
        per = []
        stored = {}
        if args.regrade:   # stored rows are found by their path, else by their file name (the photo may have moved)
            for r in json.load(open(out / stage / "per_photo.json")):
                stored[r["file"]] = r; stored.setdefault(pathlib.Path(r["file"]).name, r)
        for path, truth in items:
            if path not in cache:
                cache[path] = load_bgr(path)
            bgr = cache[path]
            orig = out / "original" / (path.stem + ".jpg")
            if not orig.exists():
                h = bgr.shape[0]
                small = cv2.resize(bgr, None, fx=1400 / h, fy=1400 / h, interpolation=cv2.INTER_AREA) if h > 1400 else bgr
                cv2.imwrite(str(orig), small, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if args.regrade:
                r0 = stored.get(str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path)) or stored.get(path.name) or {}
                L, xl, xr = r0.get("lines", []), r0.get("x_left") or 0, r0.get("x_right") or bgr.shape[1] - 1
                rows = [{"box": [int(xl), int(L[k]), int(xr), int(L[k + 1])], "label": f"u{len(L) - 1 - k:02d}"} for k in range(len(L) - 1)]
                info = {"pitch": r0.get("pitch"), "x_left": r0.get("x_left"), "x_right": r0.get("x_right"), "reason": r0.get("reason", "")}
            elif stage in ("mate_map", "mate_seg"):
                import teammate_units_adapter as mate
                try:
                    rows, info = (mate.full_map if stage == "mate_map" else mate.segments)(path)
                except Exception as e:  # noqa: BLE001
                    rows, info = [], {"ok": False, "stage": stage, "reason": f"crashed: {type(e).__name__}: {e}"}
            elif stage == "model":
                rows, info = model_rows(bgr)
            elif stage in runnable:
                try:
                    rows, info = runnable[stage](bgr)
                except Exception as e:  # noqa: BLE001 - one photo's crash must not stop the grading
                    rows, info = [], {"ok": False, "stage": stage, "reason": f"crashed: {type(e).__name__}: {e}"}
            else:
                raise SystemExit(f"unknown stage {stage}: runnable are {sorted(runnable)} + mate_map, mate_seg, model")
            rec = {"file": str(path.relative_to(ROOT)) if str(path).startswith(str(ROOT)) else str(path),
                   "split": truth["split"] if truth else None, "tier": truth.get("tier") if truth else None,
                   "rows": len(rows), "pitch": info.get("pitch"), "reason": info.get("reason", ""),
                   "rack_width": (info["x_right"] - info["x_left"]) if info.get("x_left") is not None else None,
                   # the drawn unit boundaries in original pixels, top to bottom, for the failure analysis
                   "lines": [round(float(rows[0]["box"][1]), 1)] + [round(float(r["box"][3]), 1) for r in rows] if rows else [],
                   "x_left": info.get("x_left"), "x_right": info.get("x_right")}
            unit_ok = None
            if truth:
                rec["grade"] = grade(rows, info, truth)
                unit_ok = fill_between(unit_matches(rows, truth)[0]) if rows else None
            if not args.no_overlays:
                vis = unit_logic.draw(bgr, rows, info, truth=truth["boundaries"] if truth else None, unit_ok=unit_ok)
                cv2.imwrite(str(out / stage / "overlays" / (path.stem + ".jpg")), vis, [cv2.IMWRITE_JPEG_QUALITY, 85])
            per.append(rec)
            g = rec.get("grade")
            tail = (f"  truth {g['n_true']:2d}  units right {g['units_right'] * 100:3.0f}%  pitch {g['pitch_err'] * 100:+5.1f}%"
                    if g and g["pitch_err"] is not None else (f"  truth {g['n_true']:2d}  no ladder: {rec['reason']}" if g else ""))
            print(f"[{stage:8s}] {path.name[:38]:38s} rows {len(rows):3d}{tail}", flush=True)
        json.dump(per, open(out / stage / "per_photo.json", "w"), indent=1)
        if truth_by_file:
            summary["stages"][stage] = summarise(per)
            # the detector was trained on the train split of this very data: its fair score is on the rest
            held_out = [p for p in per if p.get("split") in ("valid", "test")]
            summary["stages"][stage]["held_out"] = summarise(held_out)
            best = [p for p in per if p.get("tier") == "best"]
            if best:
                summary["stages"][stage]["best"] = summarise(best)
            s = summary["stages"][stage]
            tier = f"  best-{len(best)} {s['best']['units_right_mean'] * 100:.0f}%" if best else ""
            print(f"== {stage}: units right {s['units_right_mean'] * 100:.0f}% (held-out {s['held_out']['units_right_mean'] * 100:.0f}%){tier}  "
                  f"photos >=90% right {s['photos_90pct_units_right']}  count exact {s['count_exact']}  pitch<=10% {s['pitch_within_10pct']}")
    if truth_by_file:
        ts = json.load(open(args.truth))
        summary["testset"] = {k: ts.get(k) for k in ("rules", "funnel", "source")}
    # keep the scores of stages not run this time (one summary for every stage in the folder)
    old_path = out / "summary.json"
    if old_path.exists() and truth_by_file:
        try:
            old = json.load(open(old_path))
            if old.get("photos") == summary["photos"]:
                for k_, v_ in old.get("stages", {}).items():
                    summary["stages"].setdefault(k_, v_)
        except Exception:  # noqa: BLE001
            pass
    json.dump(summary, open(out / "summary.json", "w"), indent=1)
    print("written", out)


if __name__ == "__main__":
    main()
