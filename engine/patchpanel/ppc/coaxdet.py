"""Single-class YOLO detector for coax bulkheads.

The 13-class port detector (ppc/ports.py) has no coaxial class at all -- it
was never shown one -- and reads a coax bulkhead as RJ45. That is a training
gap, not a reconciliation bug, so no amount of reasoning after the fact can
recover it: Check 1 (ppc/connector.py) tries to tell coax from RJ-45 by the
opening's pixels, and on the 309-image set every true-coax panel measures
below ROUND_MIN regardless. This detector is trained to find the thing
directly instead of inferring it from shape statistics.

Weights come from train_coax_detector.py, fine-tuned from COCO yolov8n on
data/coax/ (one class: coax). This module only loads and runs them.
"""

import gc
import os

import numpy as np


class CoaxDetector:
    def __init__(self, weights, imgsz=960, conf=0.30, device="cpu"):
        from ultralytics import YOLO
        self.model = YOLO(weights)
        self.imgsz, self.conf, self.device = imgsz, conf, device

    def detect_many(self, paths, chunk=8):
        """Detect over many images without exhausting CPU RAM.

        Same chunk-then-collect shape as PortVerifier.detect_many
        (ppc/ports.py): predicting the whole list at once opens every image
        up front, and predicting one at a time in a loop accumulates
        Results objects on the predictor until the process runs out of
        memory. Missing files yield an empty summary instead of killing the
        batch.
        """
        paths = list(paths)
        for s in range(0, len(paths), chunk):
            batch = paths[s:s + chunk]
            live = [p for p in batch if os.path.exists(p)]
            res_by_path = {}
            if live:
                for p, res in zip(live, self.model.predict(
                        live, imgsz=self.imgsz, conf=self.conf,
                        device=self.device, stream=True, verbose=False)):
                    res_by_path[p] = self._summarize(res)
                    del res
            for p in batch:
                yield res_by_path.get(p, self._empty())
            res_by_path.clear()
            gc.collect()

    def detect(self, path):
        res = self.model.predict(path, imgsz=self.imgsz, conf=self.conf,
                                 device=self.device, verbose=False)[0]
        return self._summarize(res)

    @staticmethod
    def _empty():
        return {"n_coax": 0, "boxes": [], "mean_conf": 0.0}

    @staticmethod
    def _summarize(res):
        cf = res.boxes.conf.tolist()
        xy = res.boxes.xyxy.tolist()
        boxes = [[int(round(c)) for c in b] for b in xy]
        return {
            "n_coax": len(boxes),
            "boxes": boxes,
            "mean_conf": float(np.mean(cf)) if cf else 0.0,
        }
