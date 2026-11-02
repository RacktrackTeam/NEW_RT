"""Classify a folder of panel crops, then overlay their ports. One command.

    python run_classify_overlay.py --images crops/

Three steps, and the third is free:

  1  classify_4class.py   stage 1 -- DINOv2 over port-scale tiles, matched
                          against the gallery. A first guess at the panel
                          type from overall appearance.
  2  verify_4class.py     stage 2 -- the port detector plus the shape,
                          frame and slot rules. Confirms or corrects
                          stage 1, and this is where final_class comes
                          from.
  3  overlay              a box on every port stage 2 detected.

Stages 1 and 2 run as separate processes on purpose: stage 1 holds DINOv2
and stage 2 holds YOLO, and keeping both models out of one process is the
difference between fitting in memory and not.

Step 3 does NOT run the detector again. Stage 2 already stored every box
and its class in the CSV, so the overlay is drawn from those -- which also
guarantees the picture shows exactly the ports the classification was
based on, rather than a second opinion from a second pass.
"""

import argparse
import json
import os
import subprocess
import sys

import pandas as pd

from port_overlay import PortOverlay

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def banner(step):
    # flush: the child process writes straight to the same terminal, so an
    # unflushed banner lands after the output it is supposed to introduce.
    print("\n%s\n  %s %s\n%s" % ("=" * 70, step, "-" * max(0, 67 - len(step)),
                                 "=" * 70), flush=True)


def run(step, cmd):
    banner(step)
    r = subprocess.run([PY] + cmd, cwd=HERE)
    if r.returncode != 0:
        raise SystemExit("%s failed (exit %d)" % (step, r.returncode))


def main():
    ap = argparse.ArgumentParser(
        description="Classify a folder of patch panel crops and overlay "
                    "their ports")
    ap.add_argument("--images", default=None,
                    help="folder of crops to classify (default: the TEST "
                         "folder in classify_4class.py)")
    ap.add_argument("--name", default="",
                    help="tag for this run; outputs get the suffix, so two "
                         "datasets do not overwrite each other")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--labels", action="store_true",
                    help="write each port's class on its box")
    ap.add_argument("--numbers", action="store_true",
                    help="number the ports left to right")
    ap.add_argument("--max-width", type=int, default=1400)
    ap.add_argument("--quality", type=int, default=90)
    ap.add_argument("--skip-classify", action="store_true",
                    help="reuse an existing predictions CSV (stage 1 is the "
                         "slow one; skip it when only stage 2 changed)")
    ap.add_argument("--skip-verify", action="store_true",
                    help="reuse an existing verified CSV and only redraw")
    args = ap.parse_args()

    sfx = ("_" + args.name) if args.name else ""
    pred = os.path.join(args.out, "predictions_4class%s.csv" % sfx)
    verified = os.path.join(args.out, "verified_4class%s.csv" % sfx)
    overlays = os.path.join(args.out, "overlays%s" % sfx)

    # ---- step 1: what type of panel is this? -------------------------
    if not args.skip_classify:
        cmd = ["classify_4class.py", "--out", args.out]
        if args.name:
            cmd += ["--name", args.name]
        if args.images:
            cmd += ["--test", args.images]
        run("STEP 1/3  classify (DINOv2 gallery match)", cmd)
    elif not os.path.exists(pred):
        raise SystemExit("--skip-classify but %s does not exist" % pred)

    # ---- step 2: check that against the ports -------------------------
    if not args.skip_verify:
        run("STEP 2/3  verify (port detector + rules)",
            ["verify_4class.py", "--pred", pred, "--out", verified])
    elif not os.path.exists(verified):
        raise SystemExit("--skip-verify but %s does not exist" % verified)

    # ---- step 3: draw the ports stage 2 found -------------------------
    banner("STEP 3/3  overlay (draw stage 2's detections)")
    import cv2
    df = pd.read_csv(verified)
    os.makedirs(overlays, exist_ok=True)

    rows, drawn, total = [], 0, 0
    for i, r in enumerate(df.itertuples(), 1):
        boxes = json.loads(r.port_boxes) if isinstance(r.port_boxes, str) \
            else []
        classes = json.loads(r.port_classes) \
            if isinstance(r.port_classes, str) else []
        ports = [{"box": b, "cls": c, "conf": 1.0}
                 for b, c in zip(boxes, classes)]

        fn = ""
        crop = cv2.imread(r.src_path)
        if crop is not None and ports:
            vis = PortOverlay.draw(crop, ports, args.labels, args.numbers)
            if args.max_width and vis.shape[1] > args.max_width:
                s = args.max_width / vis.shape[1]
                vis = cv2.resize(vis, (args.max_width,
                                       max(1, int(vis.shape[0] * s))),
                                 interpolation=cv2.INTER_AREA)
            fn = "%s.jpg" % os.path.splitext(r.file)[0]
            cv2.imwrite(os.path.join(overlays, fn), vis,
                        [cv2.IMWRITE_JPEG_QUALITY, args.quality])
            drawn += 1
            total += len(ports)

        rows.append({"file": r.file, "class": r.final_class,
                     "ports": len(ports), "port_counts": r.port_counts,
                     "verify_status": r.verify_status,
                     "why": r.verify_why, "overlay": fn})
        if i % 25 == 0 or i == len(df):
            print("  drawn %d/%d" % (i, len(df)), end="\r", flush=True)
    print()

    res = pd.DataFrame(rows)
    summary = os.path.join(args.out, "results%s.csv" % sfx)
    res.to_csv(summary, index=False, encoding="utf-8")

    print("\n%d panels classified, %d overlays drawn, %d ports boxed"
          % (len(res), drawn, total))
    for c, n in res["class"].value_counts().items():
        print("  %-20s %4d" % (c, n))
    print("\n  classes + ports   %s" % summary)
    print("  overlay images    %s" % overlays)
    print("  full detail       %s" % verified)


if __name__ == "__main__":
    main()
