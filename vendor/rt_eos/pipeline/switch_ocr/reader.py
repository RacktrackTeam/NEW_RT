"""High-level API: SwitchTextReader.

Example
-------
>>> from pipeline.switch_ocr import SwitchTextReader
>>> reader = SwitchTextReader()                 # defaults tuned for switches
>>> result = reader.read("rack_photo.jpg")
>>> for det in result.detections:
...     print(det.text, det.confidence, det.box)
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable, List, Optional, Union

import numpy as np

from .config import OCRConfig
from .engines import create_all_engines, create_engine
from .identify import identify_device
from .merge import merge_detections
from .preprocess import (adaptive_binarize, best_contrast_channel, build_variants,
                         deglare, estimate_skew_angle, find_device_quad,
                         glare_fraction, load_image, otsu_binarize,
                         perspective_correct, plan_zoom_regions, rotate_image,
                         variant_enhanced, zoom_region)
from .types import OCRResult, TextDetection
from .visualize import annotate as _annotate

log = logging.getLogger("switch_ocr")

ImageLike = Union[str, Path, np.ndarray]


class SwitchTextReader:
    """Detects and reads all text on photos of network equipment.

    Thread-safe for concurrent :meth:`read` calls; models are loaded once
    on first use (or eagerly via :meth:`warmup`).
    """

    def __init__(self, config: Optional[OCRConfig] = None):
        self.config = config or OCRConfig()
        self.config.validate()
        if self.config.extra_vendors:
            from .identify import load_extra_vendors
            load_extra_vendors(self.config.extra_vendors)
        # Ensemble mode pools every installed backend's readings for more
        # recall; an explicit --engine choice is always honoured as-is.
        # Gracefully degrades to a single engine when only one is installed.
        if self.config.ensemble_engines and self.config.engine == "auto":
            self.engines = create_all_engines(self.config)
        else:
            self.engines = [create_engine(self.config)]
        self.engine = self.engines[0]  # kept for backward-compat access

    # ------------------------------------------------------------------ #
    def warmup(self) -> None:
        """Download/load models now instead of on the first read()."""
        for engine in self.engines:
            engine.warmup()

    def read(self, image: ImageLike) -> OCRResult:
        """OCR one image (path or BGR numpy array). Never raises on OCR
        problems — check ``result.ok`` / ``result.error``."""
        start = time.perf_counter()
        source = str(image) if not isinstance(image, np.ndarray) else "<ndarray>"
        try:
            img = load_image(str(image)) if not isinstance(image, np.ndarray) else image
            if img is None or img.size == 0:
                raise ValueError("Empty image")
            h, w = img.shape[:2]

            raw: List[TextDetection] = []
            for name, variant_img, scale in build_variants(img, self.config):
                for engine in self.engines:
                    t0 = time.perf_counter()
                    hits = engine.run(variant_img, variant=name)
                    log.debug(
                        "pass=%-9s engine=%-10s hits=%-3d %.0f ms", name, engine.name,
                        len(hits), (time.perf_counter() - t0) * 1000,
                    )
                    for poly, text, score in hits:
                        poly = poly.astype(np.float64) / scale  # back to original coords
                        poly[:, 0] = poly[:, 0].clip(0, w - 1)
                        poly[:, 1] = poly[:, 1].clip(0, h - 1)
                        xs, ys = poly[:, 0], poly[:, 1]
                        raw.append(
                            TextDetection(
                                text=text,
                                confidence=score,
                                box=[int(xs.min()), int(ys.min()),
                                     int(round(xs.max())), int(round(ys.max()))],
                                polygon=[[int(round(x)), int(round(y))] for x, y in poly],
                                variant=name,
                            )
                        )

            merged = merge_detections(
                raw,
                iou_thresh=self.config.merge_iou,
                min_confidence=self.config.min_confidence,
                drop_empty=self.config.drop_empty,
            )
            # Identify make + model from everything we read. Use ALL raw
            # detections too (pre-filter): a blurry logo below the confidence
            # cut can still be decisive evidence for the brand.
            device = identify_device(merged if merged else raw)
            if not device.brand and not device.model and raw:
                device = identify_device(raw)

            # ---- escalation: orientation -> deskew -> zoom -> perspective #
            # -> rotate, under a time budget. Each stage is a different
            # theory for why the baseline pass failed (wrong 90-deg
            # orientation, fine tilt, text too small, camera angle,
            # moderate tilt) and is adopted only if it actually helps.
            deadline = (start + self.config.time_budget
                        if self.config.time_budget > 0 else float("inf"))
            extra: List[TextDetection] = []
            stages = ("orientation", "deskew", "deglare", "zoom", "perspective",
                     "channel", "rotate")
            # Computed once and reused by every stage below that needs it
            # (orientation/deskew/perspective/rotate all start from the same
            # contrast-enhanced base) — four of these stages used to each
            # recompute the identical denoise+CLAHE+unsharp pass
            # independently whenever more than one stage fired on a hard
            # image, which is pure wasted CPU time with zero effect on the
            # result (same image, same config, same output every time).
            enhanced_cache: List[np.ndarray] = []

            def _get_enhanced() -> np.ndarray:
                if not enhanced_cache:
                    enhanced_cache.append(variant_enhanced(img, self.config)[0])
                return enhanced_cache[0]

            for stage in stages:
                unresolved = (not device.model or device.confidence
                              < self.config.zoom_trigger_confidence)
                if not unresolved or time.perf_counter() >= deadline:
                    break
                if stage == "orientation" and self.config.orientation_retry:
                    new = self._orientation_pass(img, _get_enhanced(), deadline)
                elif stage == "deskew" and self.config.deskew_retry:
                    new = self._deskew_pass(img, _get_enhanced(), deadline)
                elif stage == "deglare" and self.config.deglare_retry:
                    new = self._deglare_pass(img, deadline)
                elif stage == "zoom" and self.config.zoom_retry:
                    new = self._zoom_pass(img, merged if merged else raw, deadline)
                elif stage == "perspective" and self.config.perspective_retry:
                    new = self._perspective_pass(img, _get_enhanced(), deadline)
                elif stage == "channel" and self.config.channel_retry:
                    new = self._channel_pass(img, deadline)
                elif stage == "rotate" and self.config.rotate_retry:
                    new = self._rotate_pass(img, _get_enhanced(), deadline)
                else:
                    continue
                if not new:
                    continue
                extra.extend(new)
                merged2 = merge_detections(
                    raw + extra,
                    iou_thresh=self.config.merge_iou,
                    min_confidence=self.config.min_confidence,
                    drop_empty=self.config.drop_empty,
                )
                device2 = identify_device(merged2 if merged2 else raw + extra)
                if not device2.brand and not device2.model:
                    device2 = identify_device(raw + extra)
                # Adopt the second look only when it actually knows more.
                if ((bool(device2.model), bool(device2.brand), device2.confidence)
                        > (bool(device.model), bool(device.brand), device.confidence)):
                    log.debug("%s pass improved: %s -> %s", stage,
                              device.display_name, device2.display_name)
                    device, merged = device2, merged2
            # "Only truth" gate: below the threshold the guess is demoted to
            # an alternate and the device is reported as unknown.
            threshold = self.config.min_device_confidence
            if threshold > 0 and (device.brand or device.model) \
                    and device.confidence < threshold:
                from .identify import DeviceID
                device = DeviceID(alternates=[{
                    "brand": device.brand, "model": device.model,
                    "confidence": round(device.confidence, 3),
                    "note": f"suppressed: below min_device_confidence={threshold}",
                }])
            return OCRResult(
                source=source,
                image_width=w,
                image_height=h,
                detections=merged,
                device=device,
                elapsed_ms=(time.perf_counter() - start) * 1000,
                engine=self._engine_name(),
            )
        except Exception as exc:  # pragma: no cover - defensive path
            log.exception("Failed to OCR %s", source)
            return OCRResult(
                source=source,
                image_width=0,
                image_height=0,
                elapsed_ms=(time.perf_counter() - start) * 1000,
                engine=self._engine_name(),
                error=f"{type(exc).__name__}: {exc}",
            )

    def _engine_name(self) -> str:
        return " + ".join(dict.fromkeys(e.name for e in self.engines))

    def _run_all(self, image, variant: str, deadline: float = float("inf")):
        """Run every configured engine on one image; stops early if the
        escalation time budget is already spent between engines."""
        for engine in self.engines:
            if time.perf_counter() >= deadline:
                break
            t0 = time.perf_counter()
            hits = engine.run(image, variant=variant)
            log.debug("%-11s engine=%-10s hits=%-3d %.0f ms", variant, engine.name,
                      len(hits), (time.perf_counter() - t0) * 1000)
            yield from hits

    def _zoom_pass(self, img: np.ndarray, base: List[TextDetection],
                   deadline: float = float("inf")) -> List[TextDetection]:
        """Magnify + enhance the neighbourhoods of first-pass text and OCR
        them again ("zoom and see"). Coordinates map back to the original."""
        regions = plan_zoom_regions(img.shape, base, self.config)
        out: List[TextDetection] = []
        h, w = img.shape[:2]
        for box in regions:
            if time.perf_counter() >= deadline:
                log.debug("zoom: time budget exhausted, %d region(s) skipped",
                          len(regions) - regions.index(box))
                break
            zoomed, (ox, oy), scale = zoom_region(img, box, self.config)
            if zoomed is None:
                continue
            attempts = [zoomed]
            if self.config.zoom_binarize:
                attempts.append(otsu_binarize(zoomed))
                attempts.append(adaptive_binarize(zoomed))
            for attempt in attempts:
                if time.perf_counter() >= deadline:
                    break
                hits = list(self._run_all(attempt, "zoom", deadline))
                out.extend(self._map_back(hits, w, h, scale=scale, offset=(ox, oy),
                                          variant="zoom"))
        return out

    def _rotate_pass(self, img: np.ndarray, enhanced: np.ndarray,
                     deadline: float = float("inf")) -> List[TextDetection]:
        """Retry at small rotations — rack photos are rarely level."""
        out: List[TextDetection] = []
        h, w = img.shape[:2]
        for angle in self.config.rotate_angles:
            if time.perf_counter() >= deadline:
                log.debug("rotate: time budget exhausted at %s deg", angle)
                break
            rotated, inv = rotate_image(enhanced, angle)
            hits = list(self._run_all(rotated, "rotate", deadline))
            out.extend(self._map_back(hits, w, h, affine_inv=inv, variant="rotate"))
        return out

    def _orientation_pass(self, img: np.ndarray, enhanced: np.ndarray,
                          deadline: float = float("inf")) -> List[TextDetection]:
        """Retry at coarse 90-degree rotations — catches sideways or
        upside-down photos that carry no (or wrong) EXIF orientation tag
        (screenshots, re-saved WhatsApp images, PNG/WebP with no EXIF)."""
        out: List[TextDetection] = []
        h, w = img.shape[:2]
        for angle in self.config.orientation_angles:
            if time.perf_counter() >= deadline:
                log.debug("orientation: time budget exhausted at %s deg", angle)
                break
            rotated, inv = rotate_image(enhanced, angle)
            hits = list(self._run_all(rotated, "orientation", deadline))
            out.extend(self._map_back(hits, w, h, affine_inv=inv, variant="orientation"))
        return out

    def _deskew_pass(self, img: np.ndarray, enhanced: np.ndarray,
                     deadline: float = float("inf")) -> List[TextDetection]:
        """Auto-detect the fine tilt angle (Hough line voting) and rotate by
        exactly that — sharper than guessing from ``rotate_angles``' fixed
        set, and catches skews outside that set entirely."""
        if time.perf_counter() >= deadline:
            return []
        angle = estimate_skew_angle(enhanced)
        if angle == 0.0:
            return []
        rotated, inv = rotate_image(enhanced, angle)
        h, w = img.shape[:2]
        hits = list(self._run_all(rotated, "deskew", deadline))
        log.debug("deskew: estimated %+.1f deg, hits=%d", angle, len(hits))
        return self._map_back(hits, w, h, affine_inv=inv, variant="deskew")

    def _perspective_pass(self, img: np.ndarray, enhanced: np.ndarray,
                          deadline: float = float("inf")) -> List[TextDetection]:
        """Detect the device's quadrilateral outline and warp it to
        fronto-parallel — recovers labels foreshortened by an off-angle
        camera position, which no amount of in-plane rotation fixes."""
        if time.perf_counter() >= deadline:
            return []
        quad = find_device_quad(enhanced)
        if quad is None:
            return []
        warped, minv = perspective_correct(enhanced, quad)
        if warped is None:
            return []
        h, w = img.shape[:2]
        hits = list(self._run_all(warped, "perspective", deadline))
        log.debug("perspective: hits=%d", len(hits))
        return self._map_back(hits, w, h, homography_inv=minv, variant="perspective")

    def _deglare_pass(self, img: np.ndarray,
                      deadline: float = float("inf")) -> List[TextDetection]:
        """Inpaint blown-out specular highlights and retry.

        Checks the blown-out fraction FIRST (near-free) and skips entirely
        when there's nothing to repair, so clean photos never pay for an
        inpaint + OCR pass they don't need.
        """
        if time.perf_counter() >= deadline:
            return []
        if glare_fraction(img) < self.config.deglare_min_fraction:
            return []
        repaired = deglare(img, self.config)
        h, w = img.shape[:2]
        hits = list(self._run_all(repaired, "deglare", deadline))
        log.debug("deglare: hits=%d", len(hits))
        return self._map_back(hits, w, h, variant="deglare")

    def _channel_pass(self, img: np.ndarray,
                      deadline: float = float("inf")) -> List[TextDetection]:
        """Retry using whichever raw colour channel has the strongest local
        contrast — rescues silkscreen colour combinations that nearly
        cancel out in a standard luminance-based grayscale conversion."""
        if time.perf_counter() >= deadline:
            return []
        channel_img = best_contrast_channel(img, self.config)
        h, w = img.shape[:2]
        hits = list(self._run_all(channel_img, "channel", deadline))
        log.debug("channel: hits=%d", len(hits))
        return self._map_back(hits, w, h, variant="channel")

    def _map_back(self, hits, w, h, scale=1.0, offset=(0, 0), affine_inv=None,
                  homography_inv=None, variant="zoom") -> List[TextDetection]:
        """Map engine hits from a transformed image back to original coords."""
        out: List[TextDetection] = []
        for poly, text, score in hits:
            poly = poly.astype(np.float64)
            if homography_inv is not None:
                ones = np.ones((poly.shape[0], 1))
                homog = np.hstack([poly, ones]) @ homography_inv.T
                poly = homog[:, :2] / homog[:, 2:3]
            elif affine_inv is not None:
                ones = np.ones((poly.shape[0], 1))
                poly = np.hstack([poly, ones]) @ affine_inv.T
            else:
                poly = poly / scale
                poly[:, 0] += offset[0]
                poly[:, 1] += offset[1]
            poly[:, 0] = poly[:, 0].clip(0, w - 1)
            poly[:, 1] = poly[:, 1].clip(0, h - 1)
            xs, ys = poly[:, 0], poly[:, 1]
            out.append(TextDetection(
                text=text, confidence=score,
                box=[int(xs.min()), int(ys.min()),
                     int(round(xs.max())), int(round(ys.max()))],
                polygon=[[int(round(x)), int(round(y))] for x, y in poly],
                variant=variant,
            ))
        return out

    def read_batch(self, images: Iterable[ImageLike]) -> List[OCRResult]:
        """OCR many images; one failure never aborts the batch."""
        return [self.read(img) for img in images]

    # ------------------------------------------------------------------ #
    def annotate(self, image: ImageLike, result: OCRResult) -> np.ndarray:
        """Return the original image with detections drawn on it."""
        img = load_image(str(image)) if not isinstance(image, np.ndarray) else image
        return _annotate(img, result.detections)
