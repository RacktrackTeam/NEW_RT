"""Command-line interface.

Examples
--------
    switch-ocr photo.jpg
    switch-ocr rack_photos/ -o results --csv
    switch-ocr photo.jpg --accurate --min-conf 0.25
    switch-ocr photo.jpg --fast --json-only
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from typing import List

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def _collect_images(inputs: List[str], recursive: bool) -> List[Path]:
    files: List[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            files.extend(f for f in sorted(it) if f.suffix.lower() in IMAGE_EXTS)
        elif p.is_file():
            files.append(p)
        else:
            matched = sorted(Path().glob(item))
            if not matched:
                logging.getLogger("switch_ocr").warning("No such file: %s", item)
            files.extend(f for f in matched if f.suffix.lower() in IMAGE_EXTS)
    # de-dup, keep order
    seen, unique = set(), []
    for f in files:
        key = f.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="switch-ocr",
        description="Detect and read ALL text on photos of network switches "
                    "(labels, port numbers, serials) — tuned for tiny, blurry, "
                    "low-quality text. 100%% free and offline after first model download.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("inputs", nargs="+", help="Image file(s), folder(s) or globs")
    p.add_argument("-o", "--output", default="ocr_output", help="Output directory")
    p.add_argument("--min-conf", type=float, default=0.35,
                   help="Drop text below this confidence (lower it to catch fainter text)")
    p.add_argument("--no-zoom", action="store_true",
                   help="Disable the zoom-and-retry second look (faster, lower recall)")
    p.add_argument("--no-rotate", action="store_true",
                   help="Disable the fixed-angle rotation retry stage")
    p.add_argument("--no-orientation", action="store_true",
                   help="Disable the coarse 90-degree orientation retry stage")
    p.add_argument("--no-deskew", action="store_true",
                   help="Disable the auto-deskew (data-driven fine rotation) stage")
    p.add_argument("--no-perspective", action="store_true",
                   help="Disable the perspective-correction (angled photo) retry stage")
    p.add_argument("--no-ensemble", action="store_true",
                   help="Use only one OCR engine even if several are installed "
                        "(faster; ensembling all installed engines is on by default)")
    p.add_argument("--no-deglare", action="store_true",
                   help="Disable the glare-inpainting retry stage")
    p.add_argument("--no-channel", action="store_true",
                   help="Disable the best-contrast-channel retry stage")
    p.add_argument("--budget", type=float, default=25.0, metavar="SECONDS",
                   help="Per-image time budget for escalation stages (0 = unlimited)")
    p.add_argument("--workers", type=int, default=10, metavar="N",
                   help="Process N images in parallel (separate processes, each "
                        "with its own OCR engine) — big speedup for folders")
    p.add_argument("--min-device-conf", type=float, default=0.0,
                   help="Only report make/model when identification confidence is at "
                        "least this (e.g. 0.6). Weaker guesses move to 'alternates' "
                        "in the JSON and the device is reported as unknown.")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--fast", action="store_true",
                      help="Single-pass mode (~3x faster, slightly lower recall)")
    mode.add_argument("--accurate", action="store_true",
                      help="Server-grade models + all passes (slowest, best on hard images)")
    p.add_argument("--lang", default="en", help="Recognition language (e.g. en, ch, fr)")
    p.add_argument("--engine", default="auto",
                   choices=["auto", "paddle", "rapidocr", "tesseract"],
                   help="OCR backend (auto = best installed)")
    p.add_argument("--vendors", default=None, metavar="JSON",
                   help="Extra vendor knowledge file (same schema as the "
                        "built-in vendors.json) — extend coverage without code changes")
    p.add_argument("--recursive", action="store_true", help="Recurse into sub-folders")
    p.add_argument("--csv", action="store_true", help="Also write a combined results.csv")
    p.add_argument("--json-only", action="store_true", help="Skip annotated images")
    p.add_argument("--print-text", action="store_true",
                   help="Print every detected string to stdout")
    p.add_argument("-q", "--quiet", action="store_true", help="Errors only")
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    return p


def _process_file(reader, path: Path, out_dir: Path, json_only: bool) -> dict:
    """Process one image and persist its outputs; returns a summary dict
    (picklable, so it also serves the multiprocessing workers)."""
    result = reader.read(path)
    if not result.ok:
        return {"name": path.name, "ok": False, "error": result.error}

    (out_dir / f"{path.stem}.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    if not json_only:
        import cv2
        annotated = reader.annotate(path, result)
        cv2.imwrite(str(out_dir / f"{path.stem}_annotated.jpg"), annotated,
                    [cv2.IMWRITE_JPEG_QUALITY, 92])

    device = result.device
    rows = [{
        "file": path.name,
        "device_brand": device.brand if device else None,
        "device_model": device.model if device else None,
        "text": det.text,
        "confidence": round(det.confidence, 4),
        "x1": det.box[0], "y1": det.box[1], "x2": det.box[2], "y2": det.box[3],
    } for det in result.detections]

    if device and (device.brand or device.model):
        line = f"{path.name}: {device.display_name}  (confidence {device.confidence:.2f})"
    else:
        line = (f"{path.name}: device not identified "
                f"({len(result.detections)} text regions found — see JSON)")
    return {"name": path.name, "ok": True, "line": line, "rows": rows,
            "elapsed": result.elapsed_ms, "count": len(result.detections),
            "texts": [f"  {d.confidence:.2f}\t{d.text}" for d in result.detections]}


# --- multiprocessing workers (one OCR engine per process) ----------------- #
_WORKER_READER = None
_WORKER_OUT = None


def _worker_init(cfg_dict: dict, out_dir: str, json_only: bool) -> None:
    global _WORKER_READER, _WORKER_OUT
    from . import OCRConfig, SwitchTextReader
    _WORKER_READER = SwitchTextReader(OCRConfig(**cfg_dict))
    _WORKER_OUT = (Path(out_dir), json_only)


def _worker_run(path_str: str) -> dict:
    return _process_file(_WORKER_READER, Path(path_str), *_WORKER_OUT)


def main(argv: List[str] | None = None) -> int:
    # Windows consoles default to cp1252, which crashes outright on OCR'd
    # text containing characters outside it (degree signs, Greek letters,
    # box-drawing misreads). Replace what can't be shown rather than crash.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.ERROR if args.quiet else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("switch_ocr")

    files = _collect_images(args.inputs, args.recursive)
    if not files:
        log.error("No images found in: %s", args.inputs)
        return 2

    # Heavy imports AFTER arg parsing so --help stays instant.
    from . import OCRConfig, SwitchTextReader

    if args.accurate:
        cfg = OCRConfig.accurate()
    elif args.fast:
        cfg = OCRConfig.fast()
    else:
        cfg = OCRConfig()
    cfg.lang = args.lang
    cfg.min_confidence = args.min_conf
    cfg.min_device_confidence = args.min_device_conf
    cfg.engine = args.engine
    cfg.extra_vendors = args.vendors
    if args.no_zoom:
        cfg.zoom_retry = False
    if args.no_rotate:
        cfg.rotate_retry = False
    if args.no_orientation:
        cfg.orientation_retry = False
    if args.no_deskew:
        cfg.deskew_retry = False
    if args.no_perspective:
        cfg.perspective_retry = False
    if args.no_ensemble:
        cfg.ensemble_engines = False
    if args.no_deglare:
        cfg.deglare_retry = False
    if args.no_channel:
        cfg.channel_retry = False
    cfg.time_budget = args.budget

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_rows, failures = [], 0

    def _handle(i: int, res: dict) -> None:
        nonlocal failures
        if not res["ok"]:
            failures += 1
            log.error("[%d/%d] %s FAILED: %s", i, len(files), res["name"], res["error"])
            return
        csv_rows.extend(res["rows"])
        log.info("[%d/%d] %s -> %d text regions (%.0f ms)",
                 i, len(files), res["name"], res["count"], res["elapsed"])
        print(res["line"])
        if args.print_text:
            for tline in res["texts"]:
                print(tline)

    workers = min(args.workers, len(files))
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        # Each worker is a separate process running its own OCR engine.
        # cfg.cpu_threads defaults to ALL CPU cores (meant for a single
        # engine running alone) — left as-is, N worker processes would
        # each independently try to claim every core at once, thrashing
        # instead of speeding anything up. Dividing it across workers
        # means the SAME total CPU capacity is shared properly, so
        # multiple images actually progress in parallel instead of
        # fighting each other for the same threads. This changes nothing
        # about detection: cpu_threads only affects inference scheduling,
        # never which pixels are read or what the model computes.
        worker_cfg = cfg.to_dict()
        worker_cfg["cpu_threads"] = max(1, worker_cfg["cpu_threads"] // workers)
        log.info("Processing %d image(s) with %d parallel workers (%d CPU threads each)",
                 len(files), workers, worker_cfg["cpu_threads"])
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_worker_init,
            initargs=(worker_cfg, str(out_dir), bool(args.json_only)),
        ) as pool:
            for i, res in enumerate(pool.map(_worker_run, [str(f) for f in files]), 1):
                _handle(i, res)
    else:
        reader = SwitchTextReader(cfg)
        log.info("Processing %d image(s) with %s", len(files), reader.engine.name)
        for i, path in enumerate(files, 1):
            _handle(i, _process_file(reader, path, out_dir, bool(args.json_only)))

    if args.csv and csv_rows:
        csv_path = out_dir / "results.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
            writer.writeheader()
            writer.writerows(csv_rows)
        log.info("Combined CSV: %s", csv_path)

    log.info("Done. %d ok, %d failed. Output: %s", len(files) - failures, failures, out_dir)
    return 1 if failures else 0


if __name__ == "__main__":  # python -m switch_ocr.cli
    sys.exit(main())
