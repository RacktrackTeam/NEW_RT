"""Box every port the detector finds. One file, one model, nothing else.

This is the overlay reduced to what the port model alone can support. No
reference panel, no homography, no template library -- so nothing is drawn
that was not measured in this image. A box means "the detector saw a port
here", and that is the whole claim.

What that costs, honestly: a port hidden behind a cable is not drawn,
because nothing here knows it exists. build_overlays_4class.py is the
version that fills those in from a fitted reference panel, and it needs
out/webtemplates plus the two-stage classifier to do it.

    from port_overlay import PortOverlay

    ov = PortOverlay()                      # loads YOLO once, ~2 s
    r = ov.render("panel.jpg")
    cv2.imwrite("out.jpg", r["image"])
    print(r["n"], r["counts"])              # 24 {'RJ45': 24}

Command line, one image or a whole folder:

    python port_overlay.py panel.jpg --out overlay.jpg
    python port_overlay.py crops/ --out overlays/ --labels

The detector is the expensive part. Build one PortOverlay and keep it;
`render_dir` also batches the model properly rather than calling it per
file.
"""

import json
import os

import numpy as np

try:
    import cv2
except ImportError:                                   # drawing needs it
    cv2 = None

HERE = os.path.dirname(os.path.abspath(__file__))

# The weights ship as port_model.pt -- the training-run name,
# "best_merged_ports 2 (1).pt", has spaces and brackets in it and is a
# nuisance to pass through a shell or a config file. Both are accepted so
# this file works unchanged in the original working directory.
WEIGHTS = next(
    (p for p in (os.path.join(HERE, "port_model.pt"),
                 os.path.join(HERE, "best_merged_ports 2 (1).pt"))
     if os.path.exists(p)),
    os.path.join(HERE, "port_model.pt"))

PORT = (90, 230, 110)        # green  - a detected port
EMPTY = (70, 190, 255)       # amber  - a slot the model read as unoccupied
TEXT = (20, 20, 20)

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff")


def dedupe(xy, cls, cf, thresh=0.5):
    """One box per physical port, across classes.

    YOLO runs NMS per class, so a single port detected as both RJ45 and
    'empty' survives as two overlapping boxes. Measured on the 230-panel
    test set: 98 such pairs across 55 panels, almost all RJ45+empty. They
    make the overlay draw two rectangles on one port, which breaks the
    one-port-one-box guarantee the overlay exists to give. Highest
    confidence wins.

    (Same rule as ppc/ports.py in the full pipeline, inlined so this file
    stands alone.)
    """
    order = sorted(range(len(xy)), key=lambda i: -cf[i])
    keep = []
    for i in order:
        a = xy[i]
        dup = False
        for j in keep:
            b = xy[j]
            ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
            iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
            inter = ix * iy
            union = ((a[2] - a[0]) * (a[3] - a[1])
                     + (b[2] - b[0]) * (b[3] - b[1]) - inter)
            if inter / (union + 1e-9) > thresh:
                dup = True
                break
        if not dup:
            keep.append(i)
    keep.sort()
    return ([xy[i] for i in keep], [cls[i] for i in keep],
            [cf[i] for i in keep])


class PortOverlay:
    """Detect ports with the 13-class model and draw them.

    weights   path to best_merged_ports 2 (1).pt
    imgsz     960 is what the pipeline was measured at; raising it finds
              more small ports and costs time, lowering it loses them
    conf      0.25, likewise
    device    "cpu" or "cuda:0"
    """

    def __init__(self, weights=WEIGHTS, imgsz=960, conf=0.25, device="cpu"):
        if not os.path.exists(weights):
            raise FileNotFoundError("port detector not found: %s" % weights)
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.names = self.model.names
        self.imgsz, self.conf, self.device = imgsz, conf, device

    # ---------------------------------------------------------------- detect

    def _summarize(self, res):
        cls = [self.names[int(c)] for c in res.boxes.cls.tolist()]
        cf = res.boxes.conf.tolist()
        xy = res.boxes.xyxy.tolist()
        xy, cls, cf = dedupe(xy, cls, cf)
        ports = [{"box": [int(round(v)) for v in b], "cls": c,
                  "conf": round(float(f), 3)}
                 for b, c, f in zip(xy, cls, cf)]
        # Left to right, then top to bottom, so port 1 in the output is
        # port 1 on the panel. Rows are grouped by a half-box tolerance on
        # the centre y, which holds on a tilted photo too.
        if ports:
            h = np.median([p["box"][3] - p["box"][1] for p in ports]) or 1.0
            ports.sort(key=lambda p: (round((p["box"][1] + p["box"][3]) / 2
                                            / max(h, 1.0)),
                                      p["box"][0]))
        return ports

    def detect(self, image_path):
        """Ports in one image: [{box: [x1,y1,x2,y2], cls, conf}, ...]."""
        res = self.model.predict(image_path, imgsz=self.imgsz,
                                 conf=self.conf, device=self.device,
                                 verbose=False)[0]
        return self._summarize(res)

    def detect_many(self, paths, chunk=8):
        """Ports for many images, yielding one list per path.

        Two traps this avoids. Calling predict() per image accumulates
        Results on the predictor until the process runs out of memory;
        handing predict() the whole list is no better, because it opens
        every image up front. So it goes chunk by chunk. A missing file
        yields [] instead of killing the batch.
        """
        import gc
        paths = list(paths)
        for s in range(0, len(paths), chunk):
            batch = paths[s:s + chunk]
            live = [p for p in batch if os.path.exists(p)]
            got = {}
            if live:
                for p, res in zip(live, self.model.predict(
                        live, imgsz=self.imgsz, conf=self.conf,
                        device=self.device, stream=True, verbose=False)):
                    got[p] = self._summarize(res)
                    del res
            for p in batch:
                yield got.get(p, [])
            got.clear()
            gc.collect()

    # ------------------------------------------------------------------ draw

    @staticmethod
    def draw(bgr, ports, labels=False, numbers=False, thickness=0):
        """Draw the ports onto a copy of the image.

        Green on a port the model gave a connector type, amber on one it
        read as an unoccupied slot -- a real distinction the model makes,
        and the only place two colours are used.

        labels/numbers are off by default. Text burned into the picture
        covers the very ports the overlay exists to show; it belongs beside
        the image in the UI. They are here for when you are debugging a
        detection rather than showing a result.
        """
        if cv2 is None:
            raise ImportError("drawing needs opencv-python")
        out = bgr.copy()
        t = thickness or max(1, int(round(min(out.shape[:2]) / 400)))
        for i, p in enumerate(ports, 1):
            x1, y1, x2, y2 = p["box"]
            col = EMPTY if p["cls"] == "empty" else PORT
            cv2.rectangle(out, (x1, y1), (x2, y2), col, t, cv2.LINE_AA)
            tag = ""
            if numbers:
                tag = str(i)
            if labels:
                tag = ("%s %s" % (tag, p["cls"])).strip()
            if tag:
                fs = max(0.3, min(out.shape[:2]) / 1200.0)
                (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX,
                                              fs, 1)
                ty = max(th + 2, y1 - 2)
                cv2.rectangle(out, (x1, ty - th - 2), (x1 + tw + 2, ty + 2),
                              col, -1)
                cv2.putText(out, tag, (x1 + 1, ty), cv2.FONT_HERSHEY_SIMPLEX,
                            fs, TEXT, 1, cv2.LINE_AA)
        return out

    # ---------------------------------------------------------------- render

    def render(self, image, ports=None, labels=False, numbers=False,
               max_width=1400):
        """Detect and draw. Returns a dict, never raises on a bad image.

        image      path to the crop, or a BGR array (an array needs
                   `ports` supplied, since the model reads from disk)
        ports      skip detection and draw these instead

        Keys:
          image    BGR array with the overlay, or None if unreadable
          ok       False means nothing was drawn; `reason` says why
          ports    [{box, cls, conf}, ...], ordered left-to-right by row
          n        how many ports were drawn
          counts   per class, e.g. {"RJ45": 22, "empty": 2}
          scale    what the image was resized by, so boxes in `ports`
                   (which are in ORIGINAL pixels) can be mapped onto it
          reason   one sentence fit for a UI
        """
        rec = {"image": None, "ok": False, "ports": [], "n": 0, "counts": {},
               "scale": 1.0, "reason": ""}

        if isinstance(image, str):
            if cv2 is None:
                raise ImportError("reading images needs opencv-python")
            bgr = cv2.imread(image)
            if bgr is None:
                rec["reason"] = "image unreadable: %s" % image
                return rec
            if ports is None:
                ports = self.detect(image)
        else:
            bgr = image
            if ports is None:
                raise ValueError("detection needs a file path; pass `ports` "
                                 "when handing in an array")

        counts = {}
        for p in ports:
            counts[p["cls"]] = counts.get(p["cls"], 0) + 1
        rec.update(ports=ports, n=len(ports), counts=counts)

        if not ports:
            rec["image"] = bgr
            rec["reason"] = ("no ports detected at conf %.2f, imgsz %d"
                             % (self.conf, self.imgsz))
            return rec

        vis = self.draw(bgr, ports, labels, numbers)
        if max_width and vis.shape[1] > max_width:
            s = max_width / vis.shape[1]
            vis = cv2.resize(vis, (max_width, max(1, int(vis.shape[0] * s))),
                             interpolation=cv2.INTER_AREA)
            rec["scale"] = s
        rec.update(image=vis, ok=True,
                   reason="%d ports boxed (%s)"
                          % (len(ports),
                             ", ".join("%d %s" % (n, c) for c, n
                                       in sorted(counts.items(),
                                                 key=lambda kv: -kv[1]))))
        return rec

    def render_to(self, image, out_path, labels=False, numbers=False,
                  quality=90, max_width=1400):
        """render(), then write the JPEG. `file` is "" if nothing drawn."""
        r = self.render(image, None, labels, numbers, max_width)
        r["file"] = ""
        if r["ok"] and r["image"] is not None:
            cv2.imwrite(out_path, r["image"], [cv2.IMWRITE_JPEG_QUALITY,
                                               quality])
            r["file"] = out_path
        return r

    def render_dir(self, in_dir, out_dir, labels=False, numbers=False,
                   quality=90, max_width=1400, chunk=8, progress=True):
        """Overlay every image in a folder. Returns {filename: record}.

        Writes the overlays plus a ports.json carrying the boxes, so the
        detections can be reused without running the model again.
        """
        paths = [os.path.join(in_dir, f) for f in sorted(os.listdir(in_dir))
                 if f.lower().endswith(IMAGE_EXT)]
        os.makedirs(out_dir, exist_ok=True)
        results = {}
        for i, (p, ports) in enumerate(
                zip(paths, self.detect_many(paths, chunk=chunk)), 1):
            name = os.path.basename(p)
            fn = "%s.jpg" % os.path.splitext(name)[0]
            r = self.render(p, ports, labels, numbers, max_width)
            r["file"] = ""
            if r["ok"] and r["image"] is not None:
                cv2.imwrite(os.path.join(out_dir, fn), r["image"],
                            [cv2.IMWRITE_JPEG_QUALITY, quality])
                r["file"] = fn
            r.pop("image", None)
            results[name] = r
            if progress and (i % 20 == 0 or i == len(paths)):
                print("  %d/%d" % (i, len(paths)), end="\r", flush=True)
        if progress:
            print()
        with open(os.path.join(out_dir, "ports.json"), "w",
                  encoding="utf-8") as f:
            json.dump(results, f, indent=1)
        return results


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Box every port the detector finds, on one image or a "
                    "folder of them")
    ap.add_argument("image", help="image file or a directory of them")
    ap.add_argument("--out", default=None,
                    help="output file (single image) or directory (folder); "
                         "defaults to overlay.jpg / overlays/")
    ap.add_argument("--weights", default=WEIGHTS)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--labels", action="store_true",
                    help="write the port class on each box")
    ap.add_argument("--numbers", action="store_true",
                    help="number the ports left to right")
    ap.add_argument("--max-width", type=int, default=1400)
    ap.add_argument("--quality", type=int, default=90)
    args = ap.parse_args()

    ov = PortOverlay(args.weights, args.imgsz, args.conf, args.device)

    if os.path.isdir(args.image):
        out = args.out or os.path.join(HERE, "overlays")
        res = ov.render_dir(args.image, out, args.labels, args.numbers,
                            args.quality, args.max_width)
        drawn = [r for r in res.values() if r["ok"]]
        total = sum(r["n"] for r in res.values())
        counts = {}
        for r in res.values():
            for c, n in r["counts"].items():
                counts[c] = counts.get(c, 0) + n
        print("%d images, %d with ports, %d ports total"
              % (len(res), len(drawn), total))
        for c, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print("  %-16s %5d" % (c, n))
        print("wrote %s" % out)
        return

    out = args.out or os.path.join(HERE, "overlay.jpg")
    r = ov.render_to(args.image, out, args.labels, args.numbers,
                     args.quality, args.max_width)
    print("ports   %d" % r["n"])
    for c, n in sorted(r["counts"].items(), key=lambda kv: -kv[1]):
        print("  %-16s %5d" % (c, n))
    print("reason  %s" % r["reason"])
    print("wrote   %s" % (r["file"] or "nothing"))


if __name__ == "__main__":
    main()
