"""Configuration for the switch-text OCR pipeline.

Everything is a plain dataclass so it can be constructed from code, CLI
flags, or a JSON/YAML config loader without surprises.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional  # noqa: F401  (Optional used in annotations)


@dataclass
class OCRConfig:
    """Tunable settings for :class:`switch_ocr.reader.SwitchTextReader`.

    The defaults are tuned for photos of network equipment: small silkscreen
    labels, engraved/printed port numbers, serial stickers, low contrast,
    motion blur and phone-camera noise.
    """

    # ------------------------------------------------------------------ #
    # Engine / model selection
    # ------------------------------------------------------------------ #
    #: "auto" picks the best installed backend: paddle > rapidocr > tesseract.
    engine: str = "auto"
    lang: str = "en"
    #: Optional path to an extra vendors JSON file (same schema as the
    #: built-in vendors.json) to teach the identifier new vendors/models
    #: WITHOUT code changes.
    extra_vendors: Optional[str] = None
    #: "mobile" = fast CPU models, "server" = highest accuracy (slower).
    model_tier: str = "mobile"
    #: Pin the PaddleOCR generation. PP-OCRv5 has strong small/blurry-text
    #: performance and mature CPU support.
    ocr_version: str = "PP-OCRv5"
    device: str = "cpu"
    cpu_threads: int = field(default_factory=lambda: max(1, os.cpu_count() or 1))
    #: PaddleOCR defaults this to True, which routes CPU inference through
    #: oneDNN-fused kernels. On some Paddle/oneDNN builds that path hits
    #: `NotImplementedError: ConvertPirAttribute2RuntimeAttribute ...
    #: pir::ArrayAttribute<pir::DoubleAttribute>` for ops with array-of-double
    #: attributes (e.g. NMS/interpolate). Off by default to avoid the crash;
    #: flip to True if your Paddle build doesn't hit it (it's faster).
    enable_mkldnn: bool = False

    # ------------------------------------------------------------------ #
    # Detection sensitivity (passed to the PaddleOCR detector)
    # ------------------------------------------------------------------ #
    #: Cap on the longest image side fed to the detector. Higher keeps tiny
    #: text legible at the cost of speed/memory. Must be generous — the
    #: pipeline's own upscaling is pointless if the detector shrinks the
    #: image back down.
    det_limit_side_len: int = 2560
    det_limit_type: str = "max"
    #: Pixel-level text threshold. Lowered from the 0.30 default so faint /
    #: low-contrast strokes still count as text.
    det_thresh: float = 0.20
    #: Region-level threshold. Lowered from the 0.60 default so weak, blurry
    #: regions are still proposed (final filtering happens later, using the
    #: recognizer's confidence).
    det_box_thresh: float = 0.40
    #: How much detected boxes are expanded. Slightly generous so characters
    #: at the edges of tight labels are not clipped.
    det_unclip_ratio: float = 1.8
    #: Classify per-line orientation (labels on switches are often rotated).
    use_textline_orientation: bool = True
    rec_batch_size: int = 8
    #: Tesseract-only: page segmentation mode (11 = sparse text, good for
    #: scattered equipment labels; 6 = single uniform block).
    tesseract_psm: int = 11
    #: Tesseract-only: hard time budget per OCR pass, seconds. Keeps one
    #: pathological (very noisy) photo from stalling a whole batch.
    tesseract_timeout: float = 20.0

    # ------------------------------------------------------------------ #
    # Multi-pass preprocessing ensemble
    # ------------------------------------------------------------------ #
    #: Which image variants are OCR'd and merged. Order does not matter.
    #: Available: "original", "enhanced", "upscaled", "binarized", "gamma".
    variants: List[str] = field(default_factory=lambda: [
        "original", "enhanced", "upscaled", "binarized", "gamma",
    ])
    #: Target size (longest side, px) for the upscaled variant.
    upscale_target: int = 2200
    #: Also make sure the SHORTEST side reaches this many px — rescues wide,
    #: low “rack strip” photos where text height is what matters.
    upscale_min_side: int = 700
    #: Never upscale beyond this factor (avoids inventing detail).
    max_upscale: float = 4.0
    #: CLAHE clip limit for the contrast-enhanced variant.
    clahe_clip: float = 3.0
    #: Strength of unsharp masking (0 disables sharpening).
    sharpen_amount: float = 1.0
    #: Base strength of adaptive fastNlMeans denoising (0 disables). Applied
    #: only when noise is actually detected; scaled up with measured noise.
    denoise_strength: int = 7

    # ------------------------------------------------------------------ #
    # Result filtering / merging
    # ------------------------------------------------------------------ #
    #: Detections below this recognition confidence are dropped.
    min_confidence: float = 0.35
    #: Overlapping boxes (IoU above this) from different passes are merged,
    #: keeping the highest-confidence reading.
    merge_iou: float = 0.50
    #: "Only truth" gate: make/model identifications below this confidence
    #: are demoted to `alternates` and the device is reported as unknown.
    #: 0 = report best guess with its confidence (default). 0.6 is a good
    #: value when wrong answers are worse than no answer.
    min_device_confidence: float = 0.0

    # ------------------------------------------------------------------ #
    # Zoom-and-retry (second-look pass)
    # ------------------------------------------------------------------ #
    #: When the first pass finds no model (or a weak identification), crop
    #: around the text it DID find, magnify + enhance each region and OCR
    #: again — the way a human zooms into a photo.
    zoom_retry: bool = True
    #: Run the zoom pass when device confidence is below this (or the model
    #: is missing entirely).
    zoom_trigger_confidence: float = 0.75
    #: At most this many zoom regions per image (regions are merged when
    #: they overlap, prioritised by detection confidence).
    zoom_max_regions: int = 6
    #: Magnify each crop so its shorter side reaches this many pixels.
    zoom_target_side: int = 640
    #: Never magnify a crop beyond this factor.
    zoom_max_scale: float = 8.0
    #: Also OCR an Otsu-binarized version of each zoom crop (auto polarity):
    #: rescues odd colouring and white-on-dark chassis labels.
    zoom_binarize: bool = True

    # ------------------------------------------------------------------ #
    # Rotate-and-retry (tilted rack photos) + per-image time budget
    # ------------------------------------------------------------------ #
    #: When zoom still hasn't resolved the device, retry the (enhanced)
    #: image at small rotations — rack photos are rarely perfectly level.
    rotate_retry: bool = True
    rotate_angles: List[float] = field(default_factory=lambda: [-8.0, -4.0, 4.0, 8.0])

    # ------------------------------------------------------------------ #
    # Orientation retry (coarse 90-degree steps — sideways/upside-down)
    # ------------------------------------------------------------------ #
    #: Retry at 90/180/270 degrees — catches photos with no (or wrong)
    #: EXIF orientation tag (common for screenshots, WhatsApp re-saves,
    #: PNG/WebP which don't carry EXIF at all).
    orientation_retry: bool = True
    orientation_angles: List[float] = field(default_factory=lambda: [90.0, 180.0, 270.0])

    # ------------------------------------------------------------------ #
    # Auto-deskew (data-driven fine rotation)
    # ------------------------------------------------------------------ #
    #: Estimate the actual tilt angle (Hough line voting) and rotate by
    #: exactly that, instead of guessing from ``rotate_angles``' fixed set.
    deskew_retry: bool = True

    # ------------------------------------------------------------------ #
    # Perspective correction (angled photos of the device body)
    # ------------------------------------------------------------------ #
    #: Detect the device's quadrilateral outline and warp it to
    #: fronto-parallel before a retry — recovers foreshortened labels on
    #: photos taken from an angle rather than straight-on.
    perspective_retry: bool = True

    # ------------------------------------------------------------------ #
    # Glare repair (flash / reflective-metal blowout)
    # ------------------------------------------------------------------ #
    #: Inpaint blown-out specular highlights and retry — only pays the
    #: inpaint+OCR cost when a meaningful blown-out fraction is actually
    #: detected (cheap check first), so clean photos skip it for free.
    deglare_retry: bool = True
    #: Minimum blown-out-pixel fraction before a deglare attempt is worth it.
    deglare_min_fraction: float = 0.01

    # ------------------------------------------------------------------ #
    # Best-contrast-channel retry (colour silkscreen that grayscale hides)
    # ------------------------------------------------------------------ #
    #: Retry using whichever raw B/G/R channel has the strongest local
    #: contrast instead of the standard luminance mix — rescues colour
    #: combinations (e.g. white-on-light-blue) that nearly cancel out in
    #: grayscale.
    channel_retry: bool = True

    # ------------------------------------------------------------------ #
    # Multi-engine ensemble
    # ------------------------------------------------------------------ #
    #: Run every installed OCR backend (paddle + rapidocr + tesseract, or
    #: whichever are actually importable) and pool all their readings —
    #: strictly more recall than any single engine. Only takes effect when
    #: ``engine == "auto"``; an explicit --engine choice is always honoured
    #: as-is. Harmless when only one backend is installed (falls back to
    #: it alone), so it is safe to leave on by default.
    ensemble_engines: bool = True

    #: Hard per-image time budget in seconds for the ESCALATION stages
    #: (orientation, deskew, zoom, perspective, rotate). The baseline pass
    #: always runs. 0 = unlimited.
    time_budget: float = 25.0
    #: Drop results whose text is empty after stripping.
    drop_empty: bool = True

    # ------------------------------------------------------------------ #
    # Presets
    # ------------------------------------------------------------------ #
    @classmethod
    def fast(cls) -> "OCRConfig":
        """Single-pass preset: ~3x faster, still tuned for small text."""
        return cls(variants=["enhanced"], det_limit_side_len=1600,
                   orientation_retry=False, deskew_retry=False,
                   perspective_retry=False, ensemble_engines=False,
                   deglare_retry=False, channel_retry=False)

    @classmethod
    def accurate(cls) -> "OCRConfig":
        """Maximum-accuracy preset: server models + all variants."""
        return cls(model_tier="server", upscale_target=2600,
                   det_limit_side_len=3200)

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self) -> None:
        allowed = {"original", "enhanced", "upscaled", "binarized", "gamma"}
        bad = set(self.variants) - allowed
        if bad:
            raise ValueError(f"Unknown variants {sorted(bad)}; allowed: {sorted(allowed)}")
        if not self.variants:
            raise ValueError("At least one preprocessing variant is required")
        if self.model_tier not in {"mobile", "server"}:
            raise ValueError("model_tier must be 'mobile' or 'server'")
        if self.engine not in {"auto", "paddle", "rapidocr", "tesseract"}:
            raise ValueError("engine must be auto|paddle|rapidocr|tesseract")
        if not (0.0 <= self.min_confidence <= 1.0):
            raise ValueError("min_confidence must be in [0, 1]")
        if not (0.0 <= self.min_device_confidence <= 1.0):
            raise ValueError("min_device_confidence must be in [0, 1]")
        if self.time_budget < 0:
            raise ValueError("time_budget must be >= 0 (0 = unlimited)")
