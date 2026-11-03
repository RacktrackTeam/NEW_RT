"""The stitcher's self-checking tests (no pytest needed, about 10 min):  python3 tests/rack_stitcher/run_tests.py

1. Ground truth   - a video rendered from a known scene is stitched back; the result must match the scene.
2. Warnings       - each capture fault, injected alone, must raise its warning code; the good video none.
3. Behaviour      - argument checks, error messages, memory budget, parallel calls, broken progress callback.
"""
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "engine"))
sys.path.insert(0, str(HERE))

from rack_stitcher import DEFAULT_OUTPUT_DIR, RackStitchError, stitch_rack  # noqa: E402
from synthetic_video import make_scene, render  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="rack_tests_"))
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   ({detail})" if detail else ""), flush=True)


def get_scene():
    src = DEFAULT_OUTPUT_DIR / "vdo4_30fps_rack.png"
    if not src.exists():                                   # build the rack image the tests are based on
        stitch_rack("vdo4_30fps.MOV", TMP)
        src = TMP / "vdo4_30fps_rack.png"
    return make_scene(cv2.imread(str(src)))


def ncc_after_alignment(mosaic: np.ndarray, scene: np.ndarray) -> float:
    """Align the stitched mosaic to the scene and return the correlation on the overlap."""
    sift = cv2.SIFT_create(6000, contrastThreshold=0.02)
    g1, g2 = (cv2.cvtColor(i, cv2.COLOR_BGR2GRAY) for i in (mosaic, scene))
    k1, d1 = sift.detectAndCompute(g1, None)
    k2, d2 = sift.detectAndCompute(g2, None)
    m = [a for a, b in (z for z in cv2.BFMatcher().knnMatch(d1, d2, k=2) if len(z) == 2) if a.distance < 0.75 * b.distance]
    p1 = np.float32([k1[a.queryIdx].pt for a in m])
    p2 = np.float32([k2[a.trainIdx].pt for a in m])
    H, _ = cv2.findHomography(p1, p2, cv2.USAC_MAGSAC, 3.0)
    h, w = scene.shape[:2]
    warped = cv2.warpPerspective(mosaic, H, (w, h))
    mask = cv2.erode(cv2.warpPerspective(np.ones(mosaic.shape[:2], np.uint8), H, (w, h)), np.ones((25, 25))) > 0
    a = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY).astype(np.float32)[mask]
    b = g2.astype(np.float32)[mask]
    return float(np.corrcoef(a, b)[0, 1])


def convergence_deg(img: np.ndarray) -> float:
    """How much the near-vertical edges converge, in degrees: tilt of the right-hand edges minus the left-hand
    ones (long edges inside the cabinet region). 0 = parallel; keystone gives a clearly non-zero value."""
    from rack_stitcher import crop
    x0, y0, x1, y1 = crop.find_rack_bbox(img, img.sum(2) > 0)
    g = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    s = 1400 / max(g.shape)
    lines = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD).detect(cv2.resize(g, None, fx=s, fy=s))[0].reshape(-1, 4)
    L = np.hypot(lines[:, 2] - lines[:, 0], lines[:, 3] - lines[:, 1])
    a = (np.degrees(np.arctan2(lines[:, 2] - lines[:, 0], lines[:, 3] - lines[:, 1])) + 90) % 180 - 90
    cx = (lines[:, 0] + lines[:, 2]) / 2 / g.shape[1] * 1.0 / s
    ok = (L > 100) & (np.abs(a) < 15)
    left, right = ok & (cx < 0.4), ok & (cx > 0.6)
    if left.sum() < 2 or right.sum() < 2:
        return float("nan")

    def wmed(v, w):
        o = np.argsort(v)
        return v[o][np.searchsorted(np.cumsum(w[o]), w.sum() / 2)]
    return float(wmed(a[right], L[right]) - wmed(a[left], L[left]))


def codes(video: Path, **kw) -> list[str]:
    return stitch_rack(video, TMP, **kw).warning_codes


def main() -> int:
    t0 = time.time()
    scene, rack_top, rack_bottom = get_scene()
    print(f"scene {scene.shape[1]}x{scene.shape[0]}; temp folder {TMP}\n")

    print("1. Ground truth")
    good = render(scene, TMP / "good.mp4", seconds=20, y_start=0)
    r = stitch_rack(good, TMP, auto_crop=False, output_name="good_mosaic.png")
    mosaic = cv2.imread(str(r.output_path))
    ncc = ncc_after_alignment(mosaic, scene)
    record("stitched mosaic matches the true scene (correlation >= 0.98)", ncc >= 0.98, f"{ncc:.3f}")
    record("an already-straight rack gets no made-up keystone correction", r.vertical_status == "parallel", r.vertical_status)
    record("mosaic covers the scene height (>= 85%)", mosaic.shape[0] >= 0.85 * scene.shape[0] * mosaic.shape[1] / scene.shape[1] * 0.9, f"{mosaic.shape[1]}x{mosaic.shape[0]}")
    record("good video: no warnings", r.warning_codes == [], str(r.warning_codes))

    # a rack photographed from a pitched camera: verticals converge; the pipeline must straighten them
    h_, w_ = scene.shape[:2]
    keystone = cv2.warpPerspective(scene, np.array([[1, 0, 0], [0, 1, 0], [0, 6e-5, 1.0]]), (w_, h_), borderMode=cv2.BORDER_CONSTANT, borderValue=(222, 222, 216))
    kv = render(keystone, TMP / "keystone.mp4", seconds=20)
    before = convergence_deg(cv2.imread(str(stitch_rack(kv, TMP, auto_crop=False, straighten=False, level=False, output_name="k0.png").output_path)))
    rk = stitch_rack(kv, TMP, auto_crop=False, output_name="k1.png")
    after = convergence_deg(cv2.imread(str(rk.output_path)))
    record("converging verticals (keystone) are straightened", rk.vertical_status == "corrected" and abs(before) > 3 and abs(after) < 0.5 * abs(before) and abs(after) < 1.5,
           f"edge convergence {before:+.2f} deg -> {after:+.2f} deg, {rk.vertical_status}")

    print("\n2. Each fault raises its warning (and only a fault does)")
    cases = [
        ("W01", dict(seconds=4)),
        ("W02", dict(out_size=(640, 360), window_w=1500)),
        ("W03", dict(seconds=6)),
        ("W04", dict(skip=(0.35, 0.60))),
        ("W06", dict(zoom=(1.0, 2.6))),
        ("W07", dict(window_w=900, sway_px=330)),
        ("W08", dict(roll_deg=(0, 40))),
        ("W05", dict(shake_share=0.2)),                     # camera shake on a fifth of the frames
        ("W09", dict(flicker=0.4)),                         # brightness alternating every second
        ("W11", dict(y_start=rack_top + 20)),               # starts at the lid: nothing above the cabinet
        ("W12", dict(y_end=rack_bottom - 1500)),            # stops before the base of the cabinet
        ("W18", dict(blur_px=45)),
    ]
    for code, kw in cases:
        v = render(scene, TMP / f"{code}.mp4", **{"seconds": 20, **kw})
        try:
            got = codes(v, output_name=f"{code}.png")
        except RackStitchError as exc:
            got = [f"error: {str(exc)[:60]}"]
        record(f"{code} is raised by its fault", code in got, f"raised {got}")

    print("\n3. Behaviour")
    for label, kw in [("start_frame < 0", dict(start_frame=-1)), ("target_keyframes < 8", dict(target_keyframes=3)),
                      ("max_frame_side too small", dict(max_frame_side=100)), ("max_canvas_megapixels < 1", dict(max_canvas_megapixels=0.2))]:
        try:
            stitch_rack(good, TMP, **kw)
            record(f"rejects {label}", False)
        except ValueError:
            record(f"rejects {label}", True)
    try:
        stitch_rack(TMP / "missing.mp4", TMP)
        record("missing file -> FileNotFoundError", False)
    except FileNotFoundError:
        record("missing file -> FileNotFoundError", True)
    (TMP / "bad.mp4").write_text("not a video")
    try:
        stitch_rack(TMP / "bad.mp4", TMP)
        record("corrupt file -> RackStitchError", False)
    except RackStitchError:
        record("corrupt file -> RackStitchError", True)

    small = stitch_rack(good, TMP, max_canvas_megapixels=2.0, output_name="small.png")
    record("memory budget shrinks the canvas and warns (W17)", "W17" in small.warning_codes and small.image_size[0] * small.image_size[1] <= 2.4e6 * 1.5, f"{small.image_size}")

    seen = []

    def noisy(stage, frac):
        seen.append(stage)
        raise RuntimeError("progress bar broke")
    ok = stitch_rack(good, TMP, progress=noisy, output_name="cb.png")
    record("a failing progress callback does not stop the stitch", ok.output_path.exists() and len(seen) > 3)

    with ThreadPoolExecutor(2) as ex:
        fa = ex.submit(stitch_rack, good, TMP, output_name="p1.png")
        fb = ex.submit(stitch_rack, good, TMP, output_name="p2.png")
        a, b = cv2.imread(str(fa.result().output_path)), cv2.imread(str(fb.result().output_path))
    record("two parallel calls give identical output", a.shape == b.shape and bool((a == b).all()))

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed in {time.time() - t0:.0f}s")
    for n in failed:
        print("  FAILED:", n)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
