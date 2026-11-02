"""Builds methodology/Methodology_Report.html (self-contained). Run:  python build_report.py
Tables of thresholds and warnings are read from the code (rack_stitcher.quality), so they cannot drift from it."""
import base64, json, sys
from dataclasses import fields
from pathlib import Path
import cv2, numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "code"))
from rack_stitcher import quality  # noqa: E402

FIG = HERE / "figures"
CAP = json.loads((HERE / "analysis" / "capture_metrics.json").read_text())
MOD = json.loads((HERE / "analysis" / "module_results.json").read_text())
TH = quality.THRESHOLDS

def img_uri(path, maxw=1600, q=84):
    im = cv2.imread(str(path)); h, w = im.shape[:2]
    if w > maxw: im = cv2.resize(im, None, fx=maxw / w, fy=maxw / w, interpolation=cv2.INTER_AREA)
    return "data:image/jpeg;base64," + base64.b64encode(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, q])[1]).decode()
def fig(name, cap, maxw=1500):
    return f'<figure><img src="{img_uri(FIG / name, maxw)}" alt="{cap}"><figcaption>{cap}</figcaption></figure>'

# ---------------------------------------------------------------- block diagram
def box(x, y, cls, n, title, sub, w=214):
    return (f'<rect class="bx {cls}" x="{x}" y="{y}" width="{w}" height="86" rx="12"/>'
            f'<text class="num" x="{x+14}" y="{y+24}">{n}</text><text x="{x+36}" y="{y+24}">{title}</text>'
            f'<text class="sm" x="{x+14}" y="{y+48}">{sub[0]}</text><text class="sm" x="{x+14}" y="{y+66}">{sub[1]}</text>')
rows = [
  [("b1", 1, "Scan the video", ("score every frame for", "sharpness, read timestamps")),
   ("b1", 2, "Pick keyframes", ("sharpest frame in each", "time window (~36)")),
   ("b2", 3, "Match features", ("SIFT features between", "neighbouring keyframes")),
   ("b2", 4, "Chain homographies", ("rack plane, all frames in", "one coordinate system"))],
  [("b3", 5, "Compose", ("one canvas, seam between", "frames, no averaging")),
   ("b4", 6, "Rough crop", ("find the dark cabinet,", "add a margin")),
   ("b4", 7, "Straighten verticals", ("vanishing point of rails", "-> verticals vertical")),
   ("b4", 8, "Level horizontals", ("shear until shelf edges", "are level"))],
]
xs = [14, 262, 510, 758]
svg = ['<svg viewBox="0 0 986 400" role="img" aria-label="Pipeline block diagram"><defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="var(--mut)"/></marker></defs>']
for r, row in enumerate(rows):
    for x, (c, n, t, sub) in zip(xs, row): svg.append(box(x, 18 + r * 124, c, n, t, sub))
for r in range(2):
    for k in range(3): svg.append(f'<path class="ar" marker-end="url(#arr)" d="M{xs[k]+214} {61+r*124} H{xs[k+1]-3}"/>')
svg.append('<path class="ar" marker-end="url(#arr)" d="M865 104 V120 H121 V140"/>')
svg.append(box(14, 266, "b5", 9, "Final crop", ("side panel to side panel,", "lid to just below base")))
svg.append('<path class="ar" marker-end="url(#arr)" d="M865 228 V244 H121 V264"/>')
svg.append(box(262, 266, "b6", 10, "Quality check and save", ("12 capture checks -> W01-W19,", "write the image")))
svg.append(box(510, 266, "b7", "", "Result", ("StitchResult: image path, size, tilt left,", "capture numbers, warnings")).replace('width="214"', 'width="460"'))
svg.append('<path class="ar" marker-end="url(#arr)" d="M228 309 H259"/><path class="ar" marker-end="url(#arr)" d="M476 309 H507"/>')
svg.append('</svg>')
DIAGRAM = "".join(svg)

# ---------------------------------------------------------------- stages and parameters
STAGES = [
 ("1", "Scan the video", "b1", "Reads every frame once, scores its sharpness (mean gradient of the central region) and stores its timestamp. Nothing else is kept, so memory stays small. Timestamps come from the file, so variable-frame-rate phone video (vdo3 averages 47 fps) is timed correctly.", "A blurred frame carries no usable detail; knowing which frames are sharp is the basis of everything after."),
 ("2", "Pick keyframes", "b1", "Splits the video into about 36 equal time windows and keeps the sharpest frame of each. Only these frames are loaded for stitching.", "Sharp frames give sharp ports and labels; a few well-spaced frames overlap enough and keep the job fast."),
 ("3", "Match features", "b2", "Finds SIFT features (after contrast enhancement) in every keyframe and matches them between neighbouring keyframes with a strict ratio test.", "Matched points are the only evidence of how the camera moved between two frames."),
 ("4", "Chain homographies", "b2", "For each neighbouring pair estimates a homography on the dominant plane, the rack front, with a strict inlier threshold, then multiplies the pairs into a chain so every keyframe is expressed in one coordinate system (the middle keyframe is the reference). This also fixes the size of the single canvas. If some neighbours cannot be matched (for example plain wall) the longest aligned run is kept; if even that fails it retries with denser keyframes.", "The rack front is flat, so one homography per pair explains the camera motion; the strict threshold ignores cables sticking out of the plane."),
 ("5", "Compose", "b3", "Warps the frames onto one canvas and joins each new frame to the mosaic along a minimum-difference seam found by dynamic programming (8 px feather). Brightness of neighbours is matched first. If the canvas would exceed the memory budget the frames are shrunk.", "Every output pixel comes from one frame. Averaging overlaps would ghost the cables, which sit in front of the rack plane."),
 ("6", "Rough crop", "b4", "Finds the largest dark, neutral region (the cabinet) and crops to it with a 12% margin.", "Limits the next steps to the rack, so the wall and boxes do not influence the geometry."),
 ("7", "Straighten verticals", "b4", "Detects near-vertical edges (rails, cabinet sides), finds the point where they converge, and applies a homography that sends it to infinity. If the edges are already parallel (front-on capture) nothing is changed.", "Stitching leaves the rack slightly keystoned; straight rails are the cue for a rectilinear image."),
 ("8", "Level horizontals", "b4", "Measures the tilt of near-horizontal edges (shelves, device edges) and applies a shear (y shifted in proportion to x), repeating until the residual is under 0.3 degrees. The correction is always applied to the original image, so it is interpolated once.", "Step 7 only fixes verticals, which makes the axes not quite 90 degrees apart; the shear restores that."),
 ("9", "Final crop", "b5", "Along the equipment rows the share of dark pixels per column is high on the cabinet's two side panels and lower across the equipment, so the first solid dark band on each side of the centre marks the cabinet side. The open door is excluded. Vertically it runs from the cabinet top to just below the base. Empty corners are trimmed.", "Gives a rack-only image of consistent framing for any video."),
 ("10", "Quality check and save", "b6", "Computes how the video was captured (speed, overlap, blur, rack size, drift, rotation, brightness jumps, alignment strength) from data already at hand, raises coded warnings W01 to W19 with advice, and writes the PNG.", "Tells the user, in plain words, what to change in the next recording."),
]
# (stage, parameter, value, origin, note)   origin: A = automatic from the video, F = fixed in code, U = user option
P = [
 ("1", "working size for scoring and matching", "1280 px (long side)", "F", ""),
 ("1", "sharpness region", "central 60% of the frame", "F", ""),
 ("1", "frames read from", "start_frame = 0", "U", "skip frames at the start"),
 ("1", "shrink very large frames on read", "max_frame_side = 2560 px", "U", "bounds memory for 4K video"),
 ("1", "minimum frames", "8", "F", "fewer -> error"),
 ("2", "number of keyframes", "target_keyframes = 36", "U", "8 or more"),
 ("2", "window length", "ceil(frames / 36), at least 2", "A", "set by the video's length"),
 ("2", "retries with denser keyframes", "up to 3 attempts, window halves", "F", ""),
 ("3", "SIFT features per frame", "5000, contrast threshold 0.02", "F", ""),
 ("3", "contrast enhancement", "CLAHE clip 2.0, 8x8 tiles", "F", ""),
 ("3", "match ratio test", "0.72", "F", ""),
 ("3", "homography estimator", "MAGSAC++, 2.0 px threshold, 5000 iterations", "F", "strict: keeps the rack plane"),
 ("3", "minimum inliers per pair", "20", "F", ""),
 ("3", "minimum aligned run", "half of the keyframes (and 4 at least)", "F", ""),
 ("3", "reference frame", "middle keyframe", "A", ""),
 ("3", "the homographies themselves", "estimated per pair", "A", ""),
 ("4", "canvas size", "from the aligned frames", "A", ""),
 ("4", "memory budget", "max_canvas_megapixels = 40", "U", "about 200 bytes per pixel of RAM"),
 ("4", "frame border dropped", "15 px", "F", ""),
 ("4", "brightness matching limit", "gain clipped to 0.85 - 1.18", "F", ""),
 ("4", "seam", "min-cost path, steps of at most 2 rows, bias to the overlap centre (0.2)", "F", "the path itself is automatic"),
 ("4", "seam feather", "8 px", "F", ""),
 ("5", "cabinet = dark and neutral", "HSV value < 85, saturation < 100", "F", "black cabinet vs brown shelves and boxes"),
 ("5", "margin around the cabinet", "12%", "F", ""),
 ("5", "crop box position", "largest dark region", "A", ""),
 ("6", "line detector", "LSD at 1000 px long side", "F", ""),
 ("6", "lines used", "length >= 60 px, within 12 deg of vertical, on the rack", "F", ""),
 ("6", "vanishing-point search", "3000 random pairs, agree within 6 px, point at least 2000 px away", "F", ""),
 ("6", "accept the point if", ">= 3 lines and >= 15% of line length agree", "F", ""),
 ("6", "'already parallel' rule", ">= 50% of line length within 0.8 deg of one direction", "F", ""),
 ("6", "the vanishing point and homography", "from the edges found", "A", ""),
 ("7", "stop when tilt is below", "0.3 deg (at most 6 passes)", "F", ""),
 ("7", "edges used", "length >= 60 px, within 12 deg of horizontal, middle 70% of the width", "F", ""),
 ("7", "shear amount", "measured tilt", "A", ""),
 ("8", "side panel = solid dark band", ">= 75% dark pixels in the column, gaps under 3% of width joined, band >= 1% wide", "F", ""),
 ("8", "margins", "left 5%, right 2% of cabinet width; top 1%, bottom 8% of its height", "F", ""),
 ("8", "crop box position", "from the dark-pixel profile", "A", ""),
 ("8", "empty corners trimmed", "up to 6% per side", "F", ""),
 ("9", "capture-warning limits", "see the warning table (Capture guide tab)", "U", "pass thresholds=Thresholds(...)"),
 ("-", "output folder / name", "integration/output, &lt;video&gt;_rack.png", "U", "output_dir, output_name"),
 ("-", "geometry corrections", "straighten, level, auto_crop = True", "U", "can be switched off"),
]
_to_stage4 = {"homography estimator", "minimum inliers per pair", "minimum aligned run", "reference frame", "the homographies themselves", "canvas size", "memory budget"}
_match = {"SIFT features per frame", "contrast enhancement", "match ratio test"}
def _new_stage(st, p):
    if st == "3": return "3" if p in _match else "4"
    if st == "4": return "4" if p in _to_stage4 else "5"
    return str(int(st) + 1) if st in "56789" else st
P = [(_new_stage(st, p), p, v, o, n) for st, p, v, o, n in P]
P.sort(key=lambda r: 99 if r[0] == "-" else int(r[0]))
ORIG = {"A": ("Automatic", "calculated from each video"), "F": ("Fixed", "set once in the code, tuned on the 4 sample videos and the synthetic tests"), "U": ("User option", "an argument of stitch_rack()")}
def chip(o): return f'<span class="chip c{o}">{ORIG[o][0]}</span>'
ptable = "".join(f'<tr><td>{s}</td><td>{p}</td><td><code>{v}</code></td><td>{chip(o)}</td><td class="mut">{n}</td></tr>' for s, p, v, o, n in P)
cnt = {k: sum(1 for x in P if x[3] == k) for k in "AFU"}

# ---------------------------------------------------------------- warnings
TRIG = {
 "W01": (f"duration < {TH.min_duration_s:g} s", "T"), "W02": (f"shorter side of the video < {TH.min_short_side_px} px", "T"),
 "W03": (f"median speed > {TH.max_speed_fh_s:g} screen-heights/s", "T"), "W04": (f"vertical overlap of neighbouring keyframes < {TH.min_overlap:.0%} at the weakest pair", "T"),
 "W05": (f"> {TH.max_blur_share:.0%} of frames below 60% of the sharpest nearby frames", "T"), "W06": (f"rack size in frame changes by more than x{TH.max_scale_ratio:g}", "T"),
 "W07": (f"sideways travel > {TH.max_sideways_fw:.0%} of the frame width", "T"), "W08": (f"apparent rotation range > {TH.max_roll_deg:g} deg", "T"),
 "W09": (f"> {TH.max_exposure_clip_share:.0%} of neighbour pairs need more brightness correction than allowed", "T"), "W10": (f"fewer than {TH.min_pair_inliers} matched features between neighbouring keyframes", "O"),
 "W11": (f"less than {TH.min_edge_margin:.0%} of the stitched height above the cabinet", "T"), "W12": (f"less than {TH.min_edge_margin:.0%} of the stitched height below the cabinet", "T"),
 "W13": ("alignment only worked with denser keyframes", "O"), "W14": ("no reliable vertical edges found", "O"),
 "W15": ("horizontal tilt cannot be measured or > 1 deg remains", "O"), "W16": ("cabinet side panels not found", "O"),
 "W17": ("canvas larger than the memory budget", "T"), "W18": (f"median sharpness < {TH.min_sharpness:g}", "T"), "W19": ("start or end of the video could not be aligned and was left out", "O"),
}
SEV = {"W01":"cap","W02":"cap","W03":"cap","W04":"cap","W05":"cap","W06":"cap","W07":"cap","W08":"cap","W09":"cap","W10":"proc","W11":"cap","W12":"cap","W13":"proc","W14":"proc","W15":"proc","W16":"proc","W17":"info","W18":"cap","W19":"proc"}
ADVICE = {"W01":"Take 15 to 30 s.","W02":"Film at 1080p or higher.","W03":"Aim for 0.15 or less (best videos: 0.07 to 0.09).","W04":"Move slower and steadier.","W05":"Slow down, steady the phone, add light.","W06":"Keep a steadier distance.","W07":"Stand centred, move straight down.","W08":"Keep the phone upright.","W09":"Lock exposure; avoid windows and bright lights.","W10":"Slower, more light, sharper frames.","W11":"Start higher: lid and some wall in view.","W12":"Keep filming until the base and floor are in view.","W13":"Movement too fast or jerky.","W14":"Keep cabinet sides and rails visible and in focus.","W15":"Keep shelf and device edges visible.","W16":"Whole cabinet front in view and well lit.","W17":"Raise max_canvas_megapixels or lower the video resolution.","W18":"Tap to focus, clean the lens, add light."}
ADVICE["W19"] = "Keep the rack in view from start to end."
MEAS = {
 "W01": ("Video length", "Timestamp of the last frame, read from the file (so variable-frame-rate video is timed correctly)."),
 "W02": ("Shorter side of the frame, in pixels", "Frame size as stored in the video file."),
 "W03": ("Camera speed in screen-heights per second", "How far the frame centre moves between neighbouring keyframes (from the chained homographies), divided by the frame height and the time between them; the median is used."),
 "W04": ("Vertical overlap between neighbouring keyframes", "One minus the largest vertical step between neighbouring keyframes, as a share of the frame height; the weakest pair counts."),
 "W05": ("Share of blurred frames", "Sharpness of each frame (mean Sobel gradient of its central region) compared with the 90th percentile of the frames within half a second either side; frames below 60% count as blurred."),
 "W06": ("Change of rack size in the frame", "Scale of each keyframe's homography at the frame centre; the largest divided by the smallest."),
 "W07": ("Sideways drift", "Range of the frame centre's horizontal position across the keyframes, in frame widths."),
 "W08": ("Rotation of the phone", "Roll angle of each keyframe's homography; largest minus smallest."),
 "W09": ("Brightness jumps between neighbouring frames", "Mean colour of the overlap between the mosaic and the next frame gives a gain; the share of pairs whose gain falls outside 0.85 to 1.18 is counted."),
 "W10": ("Strength of the alignment between two frames", "Number of feature matches that agree with the homography (RANSAC inliers) for each neighbouring pair; the minimum."),
 "W11": ("Free space above the cabinet", "Distance from the top of the largest dark, neutral region (HSV value < 85, saturation < 100) to the top of the stitched image, as a share of its height."),
 "W12": ("Free space below the cabinet", "The same dark region's distance to the bottom of the stitched image, as a share of its height."),
 "W13": ("Whether alignment needed denser keyframes", "The first attempt with about 36 keyframes failed and the window was halved until it worked."),
 "W14": ("Vertical edges (rails, cabinet sides)", "Line segments found by the LSD detector that are near-vertical and on the rack; at least 3 must agree on one vanishing point covering 15% of the line length."),
 "W15": ("Tilt of horizontal edges", "Length-weighted median angle of near-horizontal line segments after levelling; unknown if fewer than 8 are found."),
 "W16": ("Cabinet side panels", "Share of dark pixels in each image column along the equipment rows; a solid band (75% or more) is needed on each side of the centre."),
 "W17": ("Size of the stitched canvas", "Width times height of the canvas computed from the aligned frames, compared with the memory budget (default 40 megapixels)."),
 "W18": ("Overall sharpness of the video", "Median of the same per-frame sharpness value used for W05."),
 "W19": ("Part of the video that could not be aligned", "Neighbouring keyframe pairs with fewer than 20 matches split the sequence; only the longest aligned run is used and the rest is reported."),
}
fired = {}
for n, d in MOD.items():
    for c in d["warning_codes"]: fired.setdefault(c, []).append(n.split("_")[0].split(".")[0])
wrows = ""
for c in sorted(quality.CATALOG):
    name = quality.CATALOG[c][0]; trig, ver = TRIG[c]; what, how = MEAS[c]
    v = '<span class="ok">tested</span>' if ver == "T" else '<span class="mut">seen, not isolated</span>'
    wrows += f'<tr><td><b>{c}</b></td><td>{name.replace("_"," ").title()}</td><td>{trig}</td><td>{ADVICE[c]}</td><td>{what}</td><td class="mut">{how}</td><td>{v}</td></tr>'

# ---------------------------------------------------------------- capture table
def m(n, k, f="{:.2f}"): return f.format(next(x for x in CAP if x["name"].startswith(n))[k])
names = ["vdo1", "vdo2", "vdo3", "vdo4"]
def row(label, key, fmt="{:.2f}", hint=""):
    return f'<tr><td>{label}</td>' + "".join(f"<td>{m(n, key, fmt)}</td>" for n in names) + f'<td class="mut">{hint}</td></tr>'
ctable = (
 '<table><tr><th>How it was captured</th>' + "".join(f"<th>{n}</th>" for n in names) + '<th>Good range</th></tr>'
 + '<tr><td>Resolution</td>' + "".join(f"<td>{m(n,'resolution','{}')}</td>" for n in names) + '<td class="mut">1080p or more</td></tr>'
 + row("Length (s)", "duration_s", "{:.1f}", "15 - 30 s") + row("Frame rate (average, fps)", "mean_fps", "{:.0f}", "30 or more")
 + row("Median speed (screen-heights/s)", "speed_median_fh_s", "{:.2f}", "0.15 or less") + row("Fastest moment (95th percentile)", "speed_p95_fh_s", "{:.2f}", "under 0.25")
 + '<tr><td>Rack bigger at the end (x)</td>' + "".join(f"<td>{1/next(x for x in CAP if x['name'].startswith(n))['scale_end_over_start']:.2f}</td>" for n in names) + '<td class="mut">under 2.0</td></tr>'
 + row("Phone rotation range (deg)", "roll_range_deg", "{:.0f}", "under 25") + row("Sideways drift (frame widths)", "sideways_range_frame_widths", "{:.2f}", "under 0.4")
 + row("Frames noticeably blurred", "blur_share_60", "{:.1%}", "under 8%")
 + '</table>')
# rack size at the end vs start: report ratio end/start of rack size = 1/scale

# ---------------------------------------------------------------- do / don't svg
def phone(x, y, col="var(--acc)"): return f'<rect x="{x}" y="{y}" width="16" height="30" rx="3" fill="{col}"/><rect x="{x+3}" y="{y+4}" width="10" height="16" rx="1.5" fill="var(--card)" opacity=".85"/>'
def side(title, good):
    s = [f'<svg viewBox="0 0 440 330" role="img" aria-label="{title}"><defs><marker id="m{int(good)}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z" fill="{"var(--good)" if good else "var(--bad)"}"/></marker></defs>']
    s.append('<rect x="318" y="38" width="64" height="232" rx="5" fill="var(--card)" stroke="var(--fg)" stroke-width="2"/><text x="350" y="160" text-anchor="middle" class="t">Rack</text>')
    s.append('<rect x="318" y="38" width="64" height="9" fill="var(--fg)" opacity=".75"/>')
    s.append('<line x1="20" y1="270" x2="420" y2="270" stroke="var(--mut)" stroke-width="2"/>')
    if good:
        for y in (50, 128, 206):
            s.append(phone(70, y) + f'<path d="M88 {y+15} H314" stroke="var(--good)" stroke-width="1.5" stroke-dasharray="5 4"/>')
        s.append('<path d="M126 66 V224" stroke="var(--good)" stroke-width="3" marker-end="url(#m1)" fill="none"/>')
        s.append('<text x="136" y="100" class="t">lower straight down</text><text x="136" y="116" class="sm">(bend the knees),</text><text x="136" y="130" class="sm">stay upright</text>')
        s.append('<line x1="78" y1="296" x2="350" y2="296" stroke="var(--mut)"/><text x="214" y="318" text-anchor="middle" class="sm">same distance all the way</text>')
    else:
        pts = [(70, 50), (110, 128), (190, 206)]
        for x, y in pts: s.append(phone(x, y, "var(--bad)") + f'<path d="M{x+18} {y+15} H314" stroke="var(--bad)" stroke-width="1.5" stroke-dasharray="5 4" opacity=".6"/>')
        s.append('<path d="M84 80 Q100 150 188 222" stroke="var(--bad)" stroke-width="3" marker-end="url(#m0)" fill="none"/>')
        s.append('<text x="206" y="96" class="t">walking toward</text><text x="206" y="112" class="t">the rack</text><text x="206" y="128" class="sm">size keeps changing</text>')
        s.append('<g transform="translate(236 18) rotate(35 8 15)">' + phone(0, 0, "var(--bad)") + '</g><text x="226" y="30" class="sm" text-anchor="end">starts above the lid,</text><text x="226" y="44" class="sm" text-anchor="end">looking down</text>')
        s.append('<line x1="78" y1="296" x2="350" y2="296" stroke="var(--mut)"/><text x="214" y="318" text-anchor="middle" class="sm">distance not constant</text>')
    s.append('</svg>'); return "".join(s)
SIDE_DO, SIDE_DONT = side("Do: lower the phone straight down", True), side("Don't: walk toward the rack or start above it", False)

def timeline():
    s = ['<svg viewBox="0 0 900 150" role="img" aria-label="Timeline of a good recording">']
    s.append('<rect x="20" y="52" width="860" height="34" rx="6" fill="var(--card)" stroke="var(--bd)"/>')
    segs = [(20, 110, "var(--bad)", "hold at the top", "2 s"), (130, 640, "var(--good)", "slow, steady descent  (about 12 to 25 s)", ""), (770, 110, "var(--bad)", "hold at the bottom", "2 s")]
    for x, w, c, t, d in segs:
        s.append(f'<rect x="{x}" y="52" width="{w}" height="34" rx="6" fill="{c}" opacity=".28"/><text x="{x+w/2}" y="74" text-anchor="middle" class="t">{t}</text>')
    s.append('<text x="75" y="42" text-anchor="middle" class="sm">lid + wall above</text><text x="825" y="42" text-anchor="middle" class="sm">base + floor</text>')
    s.append('<text x="450" y="118" text-anchor="middle" class="sm">total 15 to 30 s  ·  never under 6 s  ·  0.15 screen-heights per second or slower</text>')
    s.append('</svg>'); return "".join(s)
TIMELINE = timeline()

# ---------------------------------------------------------------- final images
thumbs = []
for n in names:
    key = {"vdo1": "vdo1.mp4", "vdo2": "vdo2.MOV", "vdo3": "vdo3_60fps.MOV", "vdo4": "vdo4_30fps.MOV"}[n]
    p = ROOT / "output" / MOD[key]["output_path"].split("/")[-1]; im = cv2.imread(str(p)); h = 760
    t = cv2.resize(im, (int(im.shape[1] * h / im.shape[0]), h), interpolation=cv2.INTER_AREA); thumbs.append((n, t, MOD[key]))
tw = max(t.shape[1] for _, t, _ in thumbs)
sheet = np.hstack([cv2.copyMakeBorder(t, 0, 0, 0, 14, cv2.BORDER_CONSTANT, value=(251, 252, 252)) for _, t, _ in thumbs]); cv2.imwrite(str(FIG / "outputs_sheet.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
orows = "".join(f'<tr><td><b>{n}</b></td><td>{d["image_size"][0]}x{d["image_size"][1]}</td><td>{d["seconds"]:.0f} s</td><td>{d["keyframes_used"]}</td><td>{d["min_pair_inliers"]}</td><td>{d["residual_tilt_deg"]:+.2f}</td><td>{", ".join(d["warning_codes"]) or "none"}</td></tr>' for n, _, d in thumbs)

# ---------------------------------------------------------------- page
CSS = """
:root{--bg:#fcfcfb;--fg:#0b0b0b;--mut:#52514e;--card:#f1f0ec;--bd:#dcdad4;--acc:#2a78d6;--good:#1a9a68;--bad:#d6453d;--warn:#b8780a}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#1a1a19;--fg:#f4f3ef;--mut:#c3c2b7;--card:#262624;--bd:#3d3c39;--acc:#6ea8f0;--good:#34c48b;--bad:#ef6b63;--warn:#e0a238}}
:root[data-theme=dark]{--bg:#1a1a19;--fg:#f4f3ef;--mut:#c3c2b7;--card:#262624;--bd:#3d3c39;--acc:#6ea8f0;--good:#34c48b;--bad:#ef6b63;--warn:#e0a238}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1040px;margin:0 auto;padding:28px 16px 80px}h1{font-size:28px;margin:0 0 4px}h2{font-size:21px;margin:42px 0 10px;padding-top:26px;border-top:1px solid var(--bd)}h3{font-size:16.5px;margin:20px 0 6px}
.sub{color:var(--mut);margin:0 0 20px}.mut{color:var(--mut)}.card{background:var(--card);border-radius:12px;padding:14px 18px;margin:12px 0}.card.note{border-left:4px solid var(--acc)}
.tabbar{display:flex;gap:6px;border-bottom:1px solid var(--bd);margin:0 0 18px;position:sticky;top:0;background:var(--bg);padding-top:8px;z-index:5}
.tabbar button{font:600 15px inherit;font-family:inherit;color:var(--mut);background:none;border:0;border-bottom:3px solid transparent;padding:10px 14px;cursor:pointer}.tabbar button[aria-selected=true]{color:var(--fg);border-bottom-color:var(--acc)}
.panel[hidden]{display:none}code{background:var(--card);padding:1px 6px;border-radius:4px;font-size:13.5px}
table{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0}th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--bd);vertical-align:top}th{color:var(--mut);font-weight:600}
figure{margin:14px 0}figure img{width:100%;height:auto;border-radius:10px;display:block;border:1px solid var(--bd)}figcaption{font-size:13.5px;color:var(--mut);margin-top:6px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:760px){.grid2{grid-template-columns:1fr}table{font-size:13px}th,td{padding:6px 5px}}
.chip{display:inline-block;font-size:12px;font-weight:600;padding:1px 9px;border-radius:99px;white-space:nowrap}.cA{background:#2a78d633;color:var(--acc)}.cF{background:#8884;color:var(--fg)}.cU{background:#eda10033;color:var(--warn)}
.ok{color:var(--good);font-weight:600}svg{width:100%;height:auto;display:block}svg text{fill:var(--fg);font:600 13px -apple-system,Segoe UI,Roboto,sans-serif}svg .sm{font-weight:400;font-size:11.5px;fill:var(--mut)}svg .num{fill:var(--acc);font-size:15px}svg .t{font-size:12.5px}svg .ar{stroke:var(--mut);stroke-width:2;fill:none}svg .bx{stroke-width:1.6}
.b1{fill:#2a78d633;stroke:#2a78d6}.b2{fill:#1baf7a33;stroke:#1baf7a}.b3{fill:#eb683433;stroke:#eb6834}.b4{fill:#a85fd633;stroke:#a85fd6}.b5{fill:#e87ba433;stroke:#e87ba4}.b6{fill:#eda10033;stroke:#eda100}.b7{fill:#8882;stroke:#888}
.stage{display:grid;grid-template-columns:44px 1fr;gap:12px;padding:12px 0;border-bottom:1px solid var(--bd)}.stage .n{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;border:2px solid}.stage p{margin:3px 0}.why{color:var(--mut);font-size:14px}
.do,.dont{border-radius:12px;padding:12px 18px;border:1px solid}.do{background:#1a9a6822;border-color:var(--good)}.dont{background:#d6453d22;border-color:var(--bad)}.do h3,.dont h3{margin:0 0 4px}.do li,.dont li{margin:5px 0}
.pill{display:inline-block;font-size:12px;font-weight:700;padding:2px 10px;border-radius:99px;color:#fff;margin-right:6px}.pg{background:var(--good)}.pb{background:var(--bad)}
.legend{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:14px;color:var(--mut)}
"""
JS = """<script>(function(){var t=[].slice.call(document.querySelectorAll('.tabbar button')),p=[].slice.call(document.querySelectorAll('.panel'));function show(id){t.forEach(function(b){b.setAttribute('aria-selected',b.dataset.tab===id)});p.forEach(function(x){x.hidden=x.id!==id});window.scrollTo(0,0)}t.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.tab);try{history.replaceState(null,'',b.dataset.tab==='p2'?'#guide':'#method')}catch(e){}})});[].forEach.call(document.querySelectorAll('a.tl'),function(a){a.addEventListener('click',function(e){e.preventDefault();show(a.dataset.tab)})});if(location.hash==='#guide')show('p2')})();</script>"""

stages_html = "".join(f'<div class="stage"><div class="n" style="border-color:var(--acc);color:var(--acc)">{n}</div><div><b>{t}</b><p>{d}</p><p class="why">Why: {w}</p></div></div>' for n, t, c, d, w in STAGES)
review = [
 ("Frame timestamps", "Speeds and durations were computed from frame counts, which is wrong for variable-frame-rate phone video (vdo3 averages 47 fps). Now read from the file's timestamps."),
 ("Alignment on textureless frames", "One pair of plain wall or floor frames made the whole run fail. Now the longest aligned run is kept (W19 says what was left out); denser keyframes are tried first."),
 ("False keystone correction", "On a front-on, already-straight rack the vertical correction invented a keystone from noise. Now edges that are already parallel are left alone (a test checks both cases)."),
 ("Unknown tilt looked like 'level'", "When too few edges were found, the tilt was reported as 0. It is now reported as unknown (None) with warning W15."),
 ("Memory", "A very large canvas could exhaust RAM. Frames are now shrunk to fit a budget (default 40 megapixels, W17), and a diverged chain of homographies is caught as an error."),
 ("Blur measure", "Comparing each frame with the whole video's median flagged plain wall frames as blurred. Each frame is now compared with its nearest neighbours in time."),
 ("Robustness of the call", "Arguments are validated, a failing progress callback no longer stops the stitch, and parallel calls are tested to give identical output."),
]
rev_html = "".join(f"<tr><td><b>{a}</b></td><td>{b}</td></tr>" for a, b in review)

page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rack Stitcher Methodology</title><style>{CSS}</style></head><body><main>
<h1>Rack Stitcher: methodology and capture guide</h1>
<p class="sub">How a top-to-bottom video of a server rack becomes one straight, sharp image of the rack, and how to film so it comes out well.</p>
<div class="tabbar" role="tablist"><button role="tab" data-tab="p1" aria-selected="true">Methodology</button><button role="tab" data-tab="p2" aria-selected="false">Capture analysis and guide</button></div>

<section class="panel" id="p1">
<div class="card note"><b>One pipeline, one set of settings.</b> The same ten stages run on every video; nothing is tuned per video. Of {len(P)} settings listed below, {cnt['A']} are calculated automatically from the video, {cnt['F']} are fixed in the code, and {cnt['U']} are options the calling app can change. Call it with <code>stitch_rack("vdo3_60fps.MOV")</code>.</div>

<h2>1. Block diagram</h2>
<div class="legend"><span>Blue: choose frames</span><span>Green: align</span><span>Orange: compose</span><span>Purple: geometry</span><span>Pink: crop</span><span>Yellow: quality check</span></div>
{DIAGRAM}

<h2>2. The stages</h2>
{stages_html}

<h2>3. Parameter values and where they come from</h2>
<div class="card"><div class="legend"><span>{chip('A')} {ORIG['A'][1]}</span><span>{chip('F')} {ORIG['F'][1]}</span><span>{chip('U')} {ORIG['U'][1]}</span></div>
<p class="mut" style="margin:8px 0 0">Nothing is taken from a per-video file. "Fixed" values were chosen by me and checked on the four sample videos and on synthetic videos with a known camera path; they are starting points, not laws, and the quality thresholds can be overridden.</p></div>
<table><tr><th>Stage</th><th>Parameter</th><th>Value</th><th>Origin</th><th>Note</th></tr>{ptable}</table>

<h2>4. Results on the sample videos</h2>
<figure><img src="{img_uri(FIG/'outputs_sheet.jpg', 1700)}" alt="Output images"><figcaption>Output of <code>stitch_rack()</code> with default settings for vdo1 to vdo4 (left to right).</figcaption></figure>
<table><tr><th>Video</th><th>Output (px)</th><th>Time</th><th>Keyframes</th><th>Weakest pair (matches)</th><th>Tilt left (deg)</th><th>Warnings</th></tr>{orows}</table>
<p class="sub">Times are on a 48 GB laptop-class Mac; peak RAM was 1.0 to 2.0 GB. The warnings are explained in the <a class="tl" data-tab="p2" href="#guide">capture guide</a>.</p>

<h2>5. Code recheck</h2>
<p>The module was reviewed again end to end before this report. What turned up and was fixed:</p>
<table><tr><th>Area</th><th>Problem and fix</th></tr>{rev_html}</table>
<div class="card"><b>Tests:</b> <code>python tests/run_tests.py</code> renders videos from a rack image with a known camera path and checks the output against the truth: the stitched mosaic correlates 0.987 with the true scene; a straight rack is left alone; an injected keystone is straightened (edge convergence -6.1 to +1.1 degrees); 13 capture faults, injected one at a time, each raise their warning; the good video raises none. <b>26 of 26 checks pass.</b><br>
<b>Not covered:</b> W10, W13, W14, W15, W16 and W19 were seen firing in the tests and on real videos but are not each triggered in isolation. The thresholds come from four real videos and are not a statistically validated set.</div>
</section>

<section class="panel" id="p2" hidden>
<div class="card note"><b>The short version.</b> Lower the phone <b>straight down, slowly, with the rack facing the camera</b>: 0.15 screen-heights per second or slower, 15 to 30 s in total, at 1080p or more, starting with the lid and some wall in view and ending with the base and floor in view. The two best videos here (vdo3 and vdo4) were the slowest; the weakest (vdo2) was the fastest.</div>

<h2>1. How the four videos were captured</h2>
{ctable}
<p class="sub">Measured by tracking the rack between every pair of consecutive frames. "Screen-height" is the height of the video frame. The rack grows about 1.5 to 1.7 times in the frame in all four, so the phone gets closer as it is lowered; this was tolerated here (limit x2), but a steady distance is safer.</p>
{fig('capture_frames.jpg', 'Frames at 0, 25, 50, 75 and 100% of each video. All four sweep the whole rack top to bottom; vdo2 begins by looking down at the lid.')}
{fig('capture_speed.png', 'Speed through the video. vdo2 peaks at 0.56 screen-heights/s around 4 s, about four times faster than vdo4 at its slowest. vdo1 sits at 0.2 for most of its length.')}
{fig('capture_progress.png', 'Progress down the rack. vdo4 takes 26 s, vdo2 only 8.5 s.')}
{fig('capture_scale.png', 'Rack size in the frame. All four grow by about 1.5 to 1.7 times.')}
{fig('capture_drift_roll.png', 'Sideways drift (left) and apparent rotation (right). Drift is small (0.2 to 0.3 frame widths); the rotation range of 7 to 13 degrees is partly the perspective of lowering the phone.')}
{fig('capture_blur_patches.jpg', 'The same moment in vdo2 (top) and vdo4 (bottom): the sharpest and the blurriest frame within one second, at 3x. In vdo2 the port numbers are readable in one frame and smeared in the next, which is why the stitcher picks the sharpest frame per time window, and why a slow pass gives it more good frames to choose from.')}
{fig('capture_sharpness.png', 'Sharpness through each video, relative to its own median. vdo2 has the deepest and most frequent dips.')}
{fig('capture_brightness.png', 'Brightness over time. All four follow the same curve because the view moves from the bright wall to the dark rack; this is scene content, not exposure hunting. No video shows sudden brightness jumps, so W09 stayed silent.')}

<h2>2. What each video shows</h2>
<table><tr><th>Video</th><th>What was good</th><th>What went wrong</th><th>Result</th></tr>
<tr><td><b>vdo1</b><br><span class="mut">720p, 12.6 s</span></td><td>Smooth, steady speed (0.20), almost no blur, starts with the lid in view.</td><td>720p limits detail. The video ends with the base at the edge of the frame (W12).</td><td>Cabinet label readable; only 7 of 36 port numbers.</td></tr>
<tr><td><b>vdo2</b><br><span class="mut">1080p, 8.5 s</span></td><td>Good resolution.</td><td>Fastest (median 0.36, peak 0.56): 10% of frames blurred. Starts looking down at the lid top (the first 45 frames add nothing). One pair of neighbouring frames matched on only 27 features.</td><td>No port numbers or labels readable.</td></tr>
<tr><td><b>vdo3</b><br><span class="mut">1080p, 19.5 s, 47 fps</span></td><td>Slow (0.09), no blurred frames, lid, base and floor all in view. No warnings.</td><td>Speed creeps up to 0.2 in the middle section.</td><td>All 4 labels, 21 of 36 port numbers.</td></tr>
<tr><td><b>vdo4</b><br><span class="mut">1080p, 26.5 s</span></td><td>Slowest (0.07), no blurred frames, strongest alignment (158 matches), good start and end framing. No warnings.</td><td>Phone rotated through 13 degrees. Long.</td><td>All 4 labels, 25 of 36 port numbers.</td></tr></table>
{fig('capture_speed_vs_legibility.png', 'Slower capture went with more readable port numbers. Four videos are an illustration, not a statistical proof, and vdo1 is also limited by its 720p resolution.')}

<h2>3. Do and don't: the filming guide</h2>
<div class="grid2"><div><span class="pill pg">DO</span>{SIDE_DO}</div><div><span class="pill pb">DON'T</span>{SIDE_DONT}</div></div>
<h3>Start and end of the recording</h3>
{fig('guide_framing.jpg', 'Real frames. Start with the lid and a little wall in view, with the front facing the camera; end with the base and floor in view. vdo2 started above the lid and vdo1 stopped with the base at the edge.', 1300)}
<h3>Pace</h3>
{TIMELINE}
<div class="grid2" style="margin-top:14px">
<div class="do"><h3>Do</h3><ul>
<li><b>Lower the phone straight down</b> (bend the knees) and keep it <b>upright, facing the rack</b>. This is how vdo3 and vdo4 were filmed.</li>
<li><b>Go slowly: 0.15 screen-heights per second or slower</b>, 15 to 30 s in total. The two best videos ran at 0.07 to 0.09 (19 and 26 s).</li>
<li><b>Film at 1080p or more</b>, 30 fps or higher. Small port numbers need the pixels.</li>
<li>Start with the <b>lid and some wall</b> in view; end with the <b>base and floor</b> in view. Hold each end for about 2 s.</li>
<li>Keep the <b>whole cabinet front</b> in view, side panels included, from the first frame.</li>
<li>Stand <b>centred</b> in front of the rack, at a steady distance.</li>
<li><b>Light the rack</b> (room lights on) and <b>lock focus and exposure</b> on the rack before you start.</li>
<li>Open the door fully or remove it; nothing in front of the equipment.</li>
<li>Record <b>two takes</b> and keep the one with the fewest warnings.</li>
</ul></div>
<div class="dont"><h3>Don't</h3><ul>
<li><b>Don't rush.</b> Under 6 s, or faster than 0.25 screen-heights per second, blurs the ports (vdo2: 0.36).</li>
<li><b>Don't start above the cabinet</b> looking down at the lid top; those frames are wasted (vdo2).</li>
<li><b>Don't stop early.</b> The base and a little floor must be in view (vdo1 stopped at the edge).</li>
<li><b>Don't walk toward the rack</b> while filming. The size should change as little as possible (warning above x2).</li>
<li><b>Don't swing sideways</b> or rotate the phone: keep drift under 0.4 frame widths and rotation under 25 degrees.</li>
<li><b>Don't use digital zoom</b>, filters or heavy "cinematic" stabilisation.</li>
<li><b>Don't point at windows or bright lights</b>, and don't let auto-exposure hunt (sudden brightness jumps raise W09).</li>
<li><b>Don't film in 720p</b> if you want port numbers (W02).</li>
<li>Avoid flash and glare on the equipment fronts.</li>
</ul></div></div>
<p class="sub">Numbers in this guide come from the four sample videos and the synthetic tests; they are recommendations, not guarantees. No camera distance in metres is recommended because it was not measured here; the guide uses what was measured: keep the whole cabinet front in view.</p>

<h3>Checklist before you press record</h3>
<div class="card"><div class="grid2"><ul style="list-style:none;padding:0;margin:0"><li>&#9744; Room lights on, door open</li><li>&#9744; Phone set to 1080p or higher, 30 fps or more</li><li>&#9744; Focus and exposure locked on the rack</li><li>&#9744; Standing centred, whole cabinet in view</li></ul><ul style="list-style:none;padding:0;margin:0"><li>&#9744; Start at lid height, wall above visible</li><li>&#9744; Lower straight down, 15 to 30 s, no stops or jerks</li><li>&#9744; Finish with base and floor in view</li><li>&#9744; Two takes; keep the one with fewer warnings</li></ul></div></div>

<h2>4. What triggers a warning</h2>
<p>Every run returns <code>result.warnings</code> (readable lines) and <code>result.warning_codes</code>. Each message states what was measured and what to do next time. The limits are in <code>rack_stitcher.quality.Thresholds</code> and can be changed with <code>stitch_rack(..., thresholds=Thresholds(...))</code>.</p>
<table><tr><th>Code</th><th>Name</th><th>Triggers when</th><th>What to do</th><th>What is measured</th><th>How</th><th>Verified</th></tr>{wrows}</table>
<div class="card"><b>On the sample videos:</b> vdo3 and vdo4 raise no warnings. vdo1 raises W02 (720p) and W12 (base at the edge). vdo2 raises W03 (too fast) and W10 (weak alignment). These match what was seen by eye. "Tested" means the warning is raised by a synthetic video with that single fault in <code>tests/run_tests.py</code>; "seen, not isolated" means it appeared on real or test videos but is not triggered on its own by a dedicated test.</div>
</section>
</main>{JS}</body></html>"""
(HERE / "Methodology_Report.html").write_text(page)
print("written", round(len(page) / 1e6, 2), "MB")
