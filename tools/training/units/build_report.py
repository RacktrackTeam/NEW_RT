"""The rack-unit portal: reports/units/index.html (+ img/).

    python3 scripts/units/build_report.py

Seven tabs: Summary, How it works, Results, Debug, Approaches tried, Test data, Conclusion. Inputs:
results/units/eval/{grid_vote,model,rteos_logic,feature_ends}/ (per_photo.json, failures.json, overlays/),
results/units/trials/{test_sets,rack_sizes,saturation,timing}.json (saturation.json from scripts/units/saturation.py),
results/units/approaches/approaches.json (+ results/ pictures),
reports/units/logic_explained/img/ (the logic's step pictures), data/test_sets/units/test_set.json."""
import html
import json
import pathlib
import sys

import cv2
import numpy as np
from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import unit_logic   # noqa: E402
from eval_unit_logic import unit_matches, fill_between, load_bgr   # noqa: E402
EV = ROOT / "results/units/eval"
OUT = ROOT / "reports/units"
IMG = OUT / "img"
IMG.mkdir(parents=True, exist_ok=True)

ts = json.load(open(ROOT / "data/test_sets/units/test_set.json"))["photos"]
N = len(ts)
STAGES = ("grid_vote", "model", "rteos_logic", "feature_ends")
per = {st: {r["file"]: r for r in json.load(open(EV / st / "per_photo.json"))} for st in STAGES}
S = {st: np.array([per[st][t["file"]]["grade"]["units_right"] for t in ts]) for st in STAGES}
L, M, RL, FE = S["grid_vote"], S["model"], S["rteos_logic"], S["feature_ends"]
UN = np.array([t["split"] != "train" for t in ts])
TSETS = json.load(open(ROOT / "results/units/trials/test_sets.json"))["sets"]   # one set since 2026-10-14: the best test set
RSIZES = json.load(open(ROOT / "results/units/trials/rack_sizes.json"))["sizes"]
APPR = json.load(open(ROOT / "results/units/approaches/approaches.json"))
SAT = json.load(open(ROOT / "results/units/trials/saturation.json"))
TPATH = ROOT / "results/units/trials/timing.json"
TIMING = json.load(open(TPATH))["photos"] if TPATH.exists() else {}   # seconds per photo, per method (see timing.json "machine")
TOT = sum(t["n_units"] for t in ts)
used = set()


def esc(s):
    return html.escape(str(s))


def pct(v, d=1):
    return f"{v * 100:.{d}f}\u00a0%"


def stem(t):
    return pathlib.Path(t["file"]).stem


def shrink(src, name, side=900):
    dst = IMG / name
    if pathlib.Path(src).exists() and (not dst.exists() or dst.stat().st_mtime < pathlib.Path(src).stat().st_mtime):
        im = Image.open(src).convert("RGB")
        im.thumbnail((side, side))
        im.save(dst, quality=80)
    used.add(name)
    return f"img/{name}"


PREFIX = {"model": "rtunits", "grid_vote": "logic", "rteos_logic": "combined", "feature_ends": "fends"}


def pic(stage, i, name=None, side=900):
    return shrink(EV / stage / "overlays" / f"{stem(ts[i])}.jpg", name or f"{PREFIX[stage]}_{i:02d}.jpg", side)


def marked(stage, i, name, side=900):
    """The finished ladder with each wrong unit's number in amber (only where a page explains a mistake)."""
    dst = IMG / name
    r = per[stage][ts[i]["file"]]
    if not dst.exists() or dst.stat().st_mtime < (EV / stage / "per_photo.json").stat().st_mtime:
        Lr = r["lines"]
        rows = [{"box": [int(r["x_left"]), int(Lr[k]), int(r["x_right"]), int(Lr[k + 1])], "label": ""} for k in range(len(Lr) - 1)]
        ok = fill_between(unit_matches(rows, ts[i])[0]) if rows else []
        vis = unit_logic.draw(load_bgr(ROOT / ts[i]["file"]), rows, {"x_left": r["x_left"], "x_right": r["x_right"]}, unit_ok=ok, marks=True)
        im = Image.fromarray(cv2.cvtColor(vis, cv2.COLOR_BGR2RGB)); im.thumbnail((side, side)); im.save(dst, quality=82)
    used.add(name)
    return f"img/{name}"


def case_crop(i, y0, y1, name, w_out=420):
    """A close-up of one unit at a rack's end: two units of context above and below, the unit outlined in amber."""
    dst = IMG / name
    used.add(name)
    from PIL import ImageDraw
    r = per["rteos_logic"][ts[i]["file"]]
    im = Image.open(ROOT / ts[i]["file"]).convert("RGB")
    q = max(8.0, y1 - y0)
    x0, x1 = max(0, int(r["x_left"] - 0.4 * q)), min(im.width, int(r["x_right"] + 0.4 * q))
    ctx = max(2.2 * q, (0.75 * (x1 - x0) - q) / 2)          # rows above and below: the crop is about 4:3
    ya, yb = int(y0 - ctx), int(y1 + ctx)
    if ya < 0:
        ya, yb = 0, min(im.height, yb - ya)
    if yb > im.height:
        ya, yb = max(0, ya - (yb - im.height)), im.height
    crop = im.crop((x0, ya, x1, yb))
    d = ImageDraw.Draw(crop)
    lw = max(3, int(q / 14))
    d.rectangle((int(r["x_left"]) - x0, int(y0) - ya, int(r["x_right"]) - x0, int(y1) - ya), outline=(255, 176, 32), width=lw)
    crop.thumbnail((w_out, w_out)); crop.save(dst, quality=84)
    return f"img/{name}"


def secs(v):
    return f"{v:.1f}\u00a0s" if v < 60 else f"{v / 60:.1f}\u00a0min"


def set_time(x, key):
    """mean seconds per photo and total seconds for one test set and method, from the timing run"""
    ns = [pathlib.Path(t["file"]).name for t in json.load(open(ROOT / x["file"]))["photos"]] if x.get("file") else []
    v = [TIMING[n][key] for n in ns if n in TIMING]
    return (float(np.mean(v)), float(np.sum(v))) if v and len(v) == len(ns) else (None, None)


def bar(label, v, cls="", note=""):
    return (f'<div class="bar"><span class="bl">{label}</span><span class="track"><span class="fill {cls}" style="width:{v * 100:.1f}%"></span>'
            f'</span><b>{pct(v)}</b>{note}</div>')


def tab(name, body):
    return f'<section id="tab-{name}" data-name="{name}" class="tab" hidden>{body}</section>'


# ---------------------------------------------------------------- 1. summary
summary = f"""
<div class="hero"><div class="big"><b>{pct(RL.mean())}</b><span>of rack units found correctly</span>
<small>RT-EOS units + logic on the {N} photos of the test set ({TOT:,} units): front-on, close, well-lit rack photos</small></div>
<div class="what"><h2>What it does</h2><p>From one photo of a rack, it marks where every rack unit begins and ends and numbers the units from the bottom. A trained model (RT-EOS units) draws the units; our image logic then checks the top and bottom of the rack and removes units drawn on the roof, the floor or a stand.</p></div></div>
<h2>The three methods on the same photos</h2>
<div class="bars">{bar("RT-EOS units + logic", RL.mean(), "best")}{bar("RT-EOS units alone", M.mean(), "model")}{bar("Logic alone", L.mean())}</div>
<p class="legend"><span class="key best"></span>best <span class="key model"></span>trained model <span class="key"></span>image logic</p>
<div class="cards3">
<div class="card"><h3>RT-EOS units + logic</h3><p>The units model's ladder, with its outermost units checked against the logic. Better than the model alone on {int((RL > M + 1e-9).sum())} racks, worse on {int((RL < M - 1e-9).sum())}. {int((RL >= 0.95).sum())} of {N} racks are 95 % or more right, {int((RL >= 0.999).sum())} fully right.</p></div>
<div class="card"><h3>RT-EOS units alone</h3><p>The trained units model with its ladder rules, as used in RT-EOS today. Its main mistake is drawing extra units above or below the rack.</p></div>
<div class="card"><h3>Logic alone</h3><p>Image measurements only. It needs no training and works on every rack size, but misreads the unit height on racks with strong repeating patterns.</p></div>
</div>
<h2>The test set</h2>
<div class="tablewrap"><table><tr><th>Test set</th><th>Photos</th><th>Logic alone</th><th>RT-EOS units</th><th>RT-EOS units + logic</th></tr>
{"".join(f'<tr><td>{esc(x["name"].replace(" (current test set)", ""))}</td><td class="n">{x["photos"]}</td><td class="n">{pct(x["logic"])}</td><td class="n">{pct(x["rteos"])}</td><td class="n"><b>{pct(x["best"]) if x.get("best") is not None else "-"}</b></td></tr>' for x in TSETS)}</table></div>
<p class="muted">How a unit is scored: see Test data.</p>"""

# ---------------------------------------------------------------- 2. how it works
EX = next(i for i, t in enumerate(ts) if "OIP (101)" in t["file"])   # server-room network cabinet: the model runs onto its roof and floor
ex_steps = [("1", "RT-EOS units draws the units", f"The trained units model finds a box for every unit it sees, and its ladder rules join them into one ladder. Here it draws {per['model'][ts[EX]['file']]['rows']} units for a rack of {ts[EX]['n_units']}: the extra ones sit under the cabinet's roof and on the floor (numbers in amber).", marked("model", EX, "ex_model.jpg")),
            ("2", "The logic measures the rack on its own", "Separately, the image logic finds the mounting rails and the edges of the equipment, measures the unit height four ways and lays one even ladder. Its ladder ends where the equipment and the rails end.", pic("grid_vote", EX, "ex_logic.jpg")),
            ("3", "Check the units at the top and bottom", f"For each outermost unit the software asks: was the model sure about a unit there, and does the unit lie beyond the logic's end or outside the rack's outline? A unit the model was unsure about, lying outside the rack, is removed. Here {per['model'][ts[EX]['file']]['rows'] - per['rteos_logic'][ts[EX]['file']]['rows']} units go: {pct(M[EX], 0)} of units right before, {pct(RL[EX], 0)} after.", marked("rteos_logic", EX, "ex_result.jpg"))]
LX = ROOT / "reports/units/logic_explained/img"
logic_steps = [("s02", "Find the rack", "The units model marks the cabinet (pink) and its top and bottom bars (yellow). Everything after this is measured inside that area."),
               ("s05", "Straighten the photo", "Long straight edges on the equipment show the tilt; the photo is turned and squared so the edges run level."),
               ("s03", "Find the mounting rails", "The holes and screws on the two rails repeat at every unit. Scanning across the photo, the two rails stand out as the two tall peaks."),
               ("s04", "Measure the rack's width", "Several possible left and right edges are scored; the best pair is kept. One unit is a known share of that width for each rack size."),
               ("s07", "Find the equipment edges", "The top and bottom of every device is a long level line across the rack (red)."),
               ("s08", "Measure the gaps", "Devices are whole units tall, so the gaps between their edges are 1, 2 or 3 units: another ruler."),
               ("s09", "Vote, then lay one ladder", "Four measurements of the unit height vote; the ladder with that step is slid until its lines sit on the most edges."),
               ("s10", "Find the top and bottom", "The ladder is carried over empty units up to the rack's outline and bars. End units where the rails' vertical edges fade are then removed, at most two per end."),
               ("s11", "Draw the units", "The ladder is mapped back onto the original photo; each unit becomes a band numbered from the bottom.")]
how = f"""
<h2>How RT-EOS units + logic works</h2>
<p class="lead">Each picture shows the finished ladder, numbered from U1 at the bottom. In steps 1 and 3 the number of a wrong unit is amber.</p>
<div class="steps">{"".join(f'<article class="step"><span class="num">{n}</span><div class="txt"><h3>{esc(h)}</h3><p>{esc(p)}</p></div><figure><img src="{src}" alt="{esc(h)}" data-zoom loading="lazy"></figure></article>' for n, h, p, src in ex_steps)}</div>
<div class="card rule"><h3>The rule, exactly</h3><p>Look at the outermost units, up to six at the top and six at the bottom. Remove a unit when the model's best unit box over it has confidence below 0.3, or below 0.5 while the unit lies beyond the logic's end or outside the rack's outline. Stop at the first unit that stays. Units are only removed, never added: the model's ladder almost never stops short of the rack.</p>
<p class="muted">Chosen on separate tuning photos, then checked once on the test photos. Code: <code>unit_persp.trim_end_units</code>.</p></div>
<h2>The logic, step by step</h2>
<p class="lead">How the image logic draws its own ladder, on a datacenter rack.</p>
<div class="lsteps">{"".join(f'<figure class="lstep"><img src="{shrink(LX / (k + ".jpg"), "logic_" + k + ".jpg", 700)}" alt="{esc(h)}" data-zoom loading="lazy"><figcaption><b>{j}. {esc(h)}</b><span>{esc(p)}</span></figcaption></figure>' for j, (k, h, p) in enumerate(logic_steps, 1))}</div>"""

# ---------------------------------------------------------------- 3. results (switch between the three methods)
order = sorted(range(N), key=lambda i: RL[i])
VIEWS = (("rteos_logic", "RT-EOS units + logic", RL), ("model", "RT-EOS units", M), ("grid_vote", "Logic alone", L))
cells = []
for i in order:
    imgs = "".join(f'<img class="v v-{st}" src="{pic(st, i, side=600)}" alt="photo {i}, {esc(nm)}" data-zoom loading="lazy"{"" if st == "rteos_logic" else " hidden"}>' for st, nm, _ in VIEWS)
    pills = "".join(f'<span class="pill v v-{st} {"ok" if a[i] >= 0.95 else ("mid" if a[i] >= 0.8 else "bad")}"{"" if st == "rteos_logic" else " hidden"}>{pct(a[i], 0)}</span>' for st, _, a in VIEWS)
    cells.append(f'<figure class="cell">{imgs}<figcaption>{pills} photo {i} · {ts[i]["n_units"]} units</figcaption></figure>')
results = f"""
<h2>Every test photo, worst first</h2>
<div class="switch" role="group" aria-label="Method">{"".join(f'<button type="button" data-view="{st}" aria-pressed="{"true" if st == "rteos_logic" else "false"}">{nm}</button>' for st, nm, _ in VIEWS)}</div>
<p class="lead">Sorted by RT-EOS units + logic. Each picture is the finished result; the score is the share of its units that match the hand-drawn ones. Click a picture to enlarge it.</p>
<div class="grid">{"".join(cells)}</div>"""

# ---------------------------------------------------------------- 4. debug: where the last points go, and why it stops there
B50 = SAT["sets"]["best"]
LOSS = B50["loss_by_cause"]                      # share of the score lost to each cause; they add up to the whole loss
LOST = sum(LOSS.values())
ES, LE = SAT["end_check_settings"], SAT["learned_end_check"]
HS, XT, GC = SAT["logic_height_when_they_disagree"], SAT["extend_to_logic_ends"], SAT["gap_cutoff"]
TUNE_NOW = SAT["tune"]["combined"]
rng = lambda d: (min(v for k, v in d.items() if k != "now"), max(v for k, v in d.items() if k != "now"))
hs_lo, hs_hi = rng(HS); xt_lo, xt_hi = rng(XT)
CAUSES = {
    "disputed": ("Disputed ends", "Both methods draw a unit here; the hand labels do not.",
                 "At the top or bottom of the rack, the model and the logic both see one more unit than the labels. Some are empty units the labels skip, some are the rack's top bar or a name plate.",
                 "No change to the method can settle this: it is a question of what the labels should count.",
                 "Agree what counts at the ends (blank panels, empty units, top bars), then correct the labels."),
    "height": ("Unit height off", "The model reads the unit 9 to 17 % too short or too tall.",
               "On a few racks the model's boxes are all a little too small or too large, so it fits too many or too few units between a correct top and bottom.",
               f"Taking the logic's unit height whenever the two methods disagree: {pct(hs_lo)} to {pct(hs_hi)} on the tuning photos, against {pct(TUNE_NOW)} now. When they disagree, the model is usually the one that is right.",
               "Retrain the unit model with more racks like these."),
    "model_only": ("Confident extra end units", "The model is sure about a unit the logic does not see.",
                   "A name plate, the top bar or a plinth gets a unit with high confidence. The end check removes only units the model was unsure about.",
                   f"All {ES['settings']} end-check settings on the tuning photos (the current one ranks first), and two learned end checks: {pct(LE['logistic']['unseen'])} and {pct(LE['boosted_trees']['unseen'])} on {LE['unseen_photos']} unseen photos, against {pct(LE['rule']['unseen'])} for the rule.",
                   "Retrain the unit model with top bars, name plates and plinths marked as not a unit."),
    "missing": ("Missing end units", "The ladder stops before the rack does.",
                "Empty units at the very top or bottom that the model did not box. The end check never adds units.",
                f"Extending the ladder to where the logic says the rack ends: {pct(xt_lo)} to {pct(xt_hi)} on the tuning photos, against {pct(TUNE_NOW)} now.",
                "More photos with empty units at the ends in the model's training data."),
    "offset": ("Lines just off", "An end line sits just over half a unit from the hand-drawn one.",
               "This is within the hand labels' own precision.", "Nothing to try.", "Nothing to fix in the method."),
}
order_c = sorted(CAUSES, key=lambda c: -LOSS[c])
mx_c = max(LOSS.values()) or 1
loss_rows = "".join(f'<div class="bar"><span class="bl">{esc(CAUSES[c][0])}</span><span class="track"><span class="fill warn" style="width:{LOSS[c] / mx_c * 100:.0f}%"></span></span><b>{LOSS[c] * 100:.1f} pts</b></div>' for c in order_c)
cause_cards = []
for c in order_c:
    if LOSS[c] < 0.002:
        continue
    title, short, why, tried_txt, fix = CAUSES[c]
    ids = [i for i in B50["examples"][c] if i != EX][:2]
    figs = "".join(f'<figure><img src="{marked("rteos_logic", i, f"dbg_{c}_{k}.jpg", 700)}" alt="photo {i}" data-zoom loading="lazy"><figcaption>photo {i} · {pct(RL[i], 0)}</figcaption></figure>' for k, i in enumerate(ids))
    cause_cards.append(f'<div class="card cause"><div class="chead"><h3>{esc(title)}</h3><span class="pill bad">−{LOSS[c] * 100:.1f} pts</span></div><p class="short">{esc(short)}</p>'
                       f'<dl><dt>Why</dt><dd>{esc(why)}</dd><dt>What we tried</dt><dd>{esc(tried_txt)}</dd><dt>What would fix it</dt><dd>{esc(fix)}</dd></dl>'
                       + (f'<div class="pair">{figs}</div>' if figs else "") + "</div>")
SETN = {"best": "Best 107", "highres250": "High-resolution 250", "bottom100": "Bottom 100"}
per_set = "".join(f'<tr><td>{SETN[k]}</td><td class="n"><b>{pct(v["combined"])}</b></td>' + "".join(f'<td class="n">{v["loss_by_cause"][c] * 100:.1f}</td>' for c in order_c)
                  + f'<td class="n">{pct(v["best_of_three"])}</td></tr>' for k, v in SAT["sets"].items())
ov = SAT["overlap_with_tuning"]
PROOF = [
    (f"Every end-check setting: {ES['settings']} combinations of the two confidence limits and how far to look, replayed on the {SAT['tune']['photos']} tuning photos",
     f"The current setting ranks first ({pct(ES['current']['score'])}; the worst is {pct(ES['worst']['score'])}). No setting does better."),
    ("Which gaps between two model boxes count as a unit: 7 cut-offs on the tuning photos",
     f"All between {pct(min(v for k, v in GC.items() if k != 'no_fix'))} and {pct(max(v for k, v in GC.items() if k != 'no_fix'))}; {pct(GC['no_fix'])} before the sliver fix. The fix is kept; the cut-off hardly matters."),
    (f"A learned end check: logistic regression and boosted trees on 7 signals, trained on the {LE['end_units']} end units of the tuning photos",
     f"On {LE['unseen_photos']} photos neither method was tuned on: {pct(LE['logistic']['unseen'])} and {pct(LE['boosted_trees']['unseen'])}, against {pct(LE['rule']['unseen'])} for the rule. The trees score {pct(LE['boosted_trees']['tune'])} on their own training photos: they memorise them."),
    ("The logic's unit height whenever the two methods disagree (5 thresholds)",
     f"{pct(hs_lo)} to {pct(hs_hi)}, against {pct(TUNE_NOW)} now."),
    ("Extending the ladder to the logic's ends (4 distances)",
     f"{pct(xt_lo)} to {pct(xt_hi)}, against {pct(TUNE_NOW)} now."),
    ("The best of the three methods for each photo, chosen with the answers",
     f"{pct(B50['best_of_three'])} on the test set: choosing between the methods could add at most {(B50['best_of_three'] - B50['combined']) * 100:.1f} points, and only by knowing the answer."),
    ("A perfect end check, chosen with the answers",
     f"{pct(B50['perfect_end_check'])} on the test set. An upper bound only: it needs the labels, and because units between two right units count as right, trimming can also hide a wrong unit height."),
    ("Tuning photos against test photos",
     f"{pct(SAT['tune']['combined'])} on the tuning photos, {pct(B50['combined'])} on the test set: the settings are not fitted to the tuning photos. The test set shares {ov['best']} photos with the tuning set; on its other {SAT['unseen_only']['best']['photos']} photos it scores {pct(SAT['unseen_only']['best']['combined'])}."),
    ("Earlier ideas (Approaches tried): mirrored photo, second pass on the rack, neighbour matching, device boxes, edge snapping",
     "None scored higher than the end check (measured earlier, with the stricter quarter-unit rule)."),
]
proof_rows = "".join(f"<tr><td>{esc(a)}</td><td>{esc(b)}</td></tr>" for a, b in PROOF)
labels_fix = B50["combined"] + LOSS["disputed"]
model_fix = LOSS["height"] + LOSS["model_only"] + LOSS["missing"]
disp = [(r["i"], c) for r in B50["per_photo"] for c in r.get("cases", []) if c["kind"] == "disputed"]
disp_figs = ""
for k, (i, c) in enumerate(disp):
    src = case_crop(i, c["y"][0], c["y"][1], "disp_%02d_%s_%d.jpg" % (i, c["end"], k))
    disp_figs += (f'<figure class="dcell"><img src="{src}" alt="photo {i}, {c["end"]}" data-zoom>'
                  f'<figcaption>photo {i} · {c["end"]} · model {c["model_confidence"]:.2f}</figcaption></figure>')
disputed_html = (f"""<h2>The disputed units: what should the labels count?</h2>
<p class="lead">All {len(disp)} disputed units on the best {N}, outlined in amber: the model and the logic both draw a unit here, the hand labels do not. The number after "model" is how sure the units model was. If the labels counted these, the best {N} would read up to {pct(B50["combined"] + LOSS["disputed"])}.</p>
<div class="dgrid">{disp_figs}</div>""" if disp else "")
debug = f"""
<div class="hero"><div class="big"><b>{LOST * 100:.1f} pts</b><span>left to gain on the best {N}</span>
<small>RT-EOS units + logic finds {pct(RL.mean())} of the units; this tab shows where the other {LOST * 100:.1f} points go, what we tried, and why the method stops there.</small></div>
<div class="what"><h2>The short answer</h2><p>With today's units model and today's hand labels, {pct(RL.mean())} is the saturation point for rules on top of the model. {LOSS['disputed'] * 100:.1f} points are units both methods see but the labels leave out; {model_fix * 100:.1f} points are mistakes only a better-trained units model can fix; the rest is within the labels' own precision. Every change to the rules we tried was tested on the tuning photos first, and none does better.</p></div></div>
<h2>Where the units are lost</h2>
<p class="lead">Every unit the method gets wrong on the best {N}, by cause. The causes add up to the whole loss.</p>
<div class="bars">{loss_rows}</div>
<div class="cards2">{"".join(cause_cards)}</div>
{disputed_html}
<h2>Why it stops at {pct(RL.mean())}: the evidence</h2>
<p class="lead">Every change was chosen on the {SAT['tune']['photos']} tuning photos only, then graded once on photos it had not seen.</p>
<div class="tablewrap"><table><tr><th>What we tested</th><th>Result</th></tr>{proof_rows}</table></div>
<div class="card rule verdict"><h3>Verdict</h3><p><b>{pct(RL.mean())} is the saturation point</b> of RT-EOS units + logic with this units model and these labels. Going further needs new inputs, not new rules: agreed labels at the rack's ends (the score would read up to {pct(labels_fix)}), and a units model retrained on top bars, name plates, plinths, empty end units and the racks it misreads (up to {model_fix * 100:.1f} more points).</p>
<p class="muted">Every number on this tab is rerun by <code>scripts/units/saturation.py</code> (results/units/trials/saturation.json), without running the model again.</p></div>
<h2>The same on every test set</h2>
<p class="lead">Points lost to each cause, and the best score a perfect choice between the three methods could reach.</p>
<div class="tablewrap"><table><tr><th>Test set</th><th>Units right</th>{"".join(f"<th>{esc(CAUSES[c][0])}</th>" for c in order_c)}<th>Best of three, with the answers</th></tr>{per_set}</table></div>
<p class="muted">Every set keeps only front-on, close photos (the quality rule of 14 October 2026); the whole-rack set is small, so one photo moves its numbers by several points.</p>"""

# ---------------------------------------------------------------- 5. approaches tried
AP = {a["key"]: a for a in APPR["approaches"]}
AP["grid_vote"] = {**AP["grid_vote"], "extra": ""}   # its stored line uses the earlier rule
MATE = {st: np.array([{r["file"]: r for r in json.load(open(EV / st / "per_photo.json"))}[t["file"]]["grade"]["units_right"] for t in ts]) for st in ("mate_map", "mate_seg")}
CHIP = {"use": ("ok", "in use"), "kept": ("ok", "kept"), "drop": ("bad", "dropped"), "mate": ("plain", "teammate"), "ref": ("plain", "part of it")}


def detail(a):
    """The opened part of an idea: why, what happened, conclusion, pictures."""
    figs = "".join(f'<figure><img src="{shrink(ROOT / "results/units/approaches" / q["file"], "ap_" + a["key"].replace("+", "plus") + f"_{k}.jpg", 500)}" alt="{esc(a["name"])}" data-zoom loading="lazy"><figcaption><span class="pill {"ok" if q["ok"] else "bad"}">{"works here" if q["ok"] else "fails here"}</span></figcaption></figure>' for k, q in enumerate(a.get("pics", [])[:4]))
    what = f"<p><b>What happened.</b> {esc(a['extra'])}</p>" if a.get("extra") else ""
    return (f'<div class="tdet"><p><b>Why we tried it.</b> {esc(a["why"])}</p>{what}<p><b>Conclusion.</b> {esc(a["concl"])}</p>'
            + (f'<div class="apics">{figs}</div>' if figs else "") + "</div>")


def idea(name, goal, right, a=None):
    body = detail(a) if a else ""
    tag = "details" if body else "div"
    return (f'<{tag} class="idea"><{"summary" if body else "div"} class="irow">'
            f'<span class="iname"><b>{esc(name)}</b><span>{esc(goal)}</span></span><span class="iscore">{right}</span></{"summary" if body else "div"}>{body}</{tag}>')


def mini(v, cls=""):
    return f'<span class="tbar"><span class="track"><span class="fill {cls}" style="width:{v * 100:.1f}%"></span></span><b>{v * 100:.0f}\u00a0%</b></span>'


# 1. the whole methods, all on the best 50 with today's rule
ROAD = [("Teammate: devices split into units", "The teammate's devices and empty slots, each split into its units.", "mate", MATE["mate_seg"].mean(), "fail", AP["mate_seg"]),
        ("Teammate: full rack map", "The teammate's straightened rack map, split into devices that are whole units tall.", "mate", MATE["mate_map"].mean(), "fail", AP["mate_map"]),
        ("Logic only", "Image logic alone: unit height by a vote of four cues, then one even grid.", "ref", L.mean(), "", AP["grid_vote"]),
        ("RT-EOS units", "The trained model draws one box per unit; the boxes become a ladder.", "ref", M.mean(), "model", AP["model"]),
        ("RT-EOS unit height, ends from the logic", "The model's ladder, cut where the logic says the rack starts and stops.", "drop", FE.mean(), "fail",
         dict(key="fends", name="", why="The model measures the unit height well; the logic often finds the rack's ends.", extra="",
              concl="The logic's ends are wrong too often, so cutting at them loses more units than it saves.")),
        ("RT-EOS units + logic (end check)", "The model's ladder; each end unit the model is unsure about is checked against the logic and the Rack box.", "use", RL.mean(), "best",
         dict(key="rl", name="", why="Most of the model's lost units are extra units at the top and bottom of the rack.", extra="",
              concl="The best method: unsure end units beyond the logic's ends or outside the Rack box are dropped."))]
road = "".join(f'<details class="rrow"><summary>{bar(esc(n), v, cls)}<span class="rgoal">{esc(g)}</span><span class="pill {CHIP[c][0]}">{CHIP[c][1]}</span></summary>{detail(a)}</details>' for n, g, c, v, cls, a in ROAD)

# 2. every idea, grouped by the problem it tackled
HELP = [("Drop sliver units between two model boxes", "A gap under a tenth of a unit between two boxes is not a unit of its own.", "kept"),
        ("A learned end check", f"Logistic regression and boosted trees on 7 signals, trained on {SAT['learned_end_check']['end_units']} end units.", "no gain"),
        ("Take the logic's unit height when they disagree", "When the two unit heights differ by 5 to 30 %, use the logic's.", "lower"),
        ("Extend the ladder to the logic's ends", "Add units where the logic says the rack goes on.", "lower"),
        ("Neighbour matching on the rails", "Neighbouring units should look alike on the mounting rails.", "no gain"),
        ("Pick the ladder that fits the equipment edges", "Several ladders per photo; keep the one on the most equipment edges.", "no gain"),
        ("Unit height from the device boxes", "Measure a unit from the model's equipment boxes.", "no gain"),
        ("Run the model on the mirrored photo too", "Merge the ladders from the photo and its mirror image.", "lower"),
        ("Second pass on the rack alone", "Cut out the rack and run the model again on it.", "no gain"),
        ("Snap lines to the nearest strong edge", "Move each line of the model's ladder to the closest edge.", "no gain")]
GROUPS = [("Helping the RackTrack model", "Only the end check and the sliver fix moved the score; every other helper scored the same or lower. The Debug tab has the numbers.", None),
          ("Measuring the unit height", "Any single cue fails on some racks. A vote of four cues, the logic we use, fails far less often.", ["current", "autocorr", "pairs", "ocr", "curved"]),
          ("Finding the rack's width", "Most long lines are texture inside equipment, so the width came out wrong. The model's Rack box gives it reliably.", ["tolerant", "outer"]),
          ("Placing the unit lines", "A chain of lines that may bend drifts between devices; one rigid, even grid cannot drift.", ["posts_dp", "dp", "dp2", "dpflex", "lsd", "runs"]),
          ("Straightening the photo", "Straightening first helps a little, so the logic keeps it.", ["rectify", "frame", "frame_flat"]),
          ("Finding the top and bottom", "Equipment ears, cage nuts and cables hide the holes on most racks. The ends are still the hardest part.", ["holes", "grid+holes", "columns"]),
          ("Combining several methods", "Picking one method's ladder per photo picks the wrong one too often.", ["vote", "ours+mate"])]
groups = []
for title, learn, keys in GROUPS:
    if keys is None:
        rows = "".join(idea(n, g, f'<span class="pill {"ok" if r == "kept" else "plain"}">{esc(r)}</span>') for n, g, r in HELP)
    else:
        rows = "".join(idea(AP[k]["name"], AP[k]["goal"], mini(AP[k]["score_dev"], "fail"), AP[k]) for k in sorted(keys, key=lambda k: -AP[k]["score_dev"]))
    n = len(HELP) if keys is None else len(keys)
    groups.append(f'<div class="group"><div class="ghead"><h3>{esc(title)}</h3><span class="muted">{n} ideas</span></div><p class="learn">{esc(learn)}</p><div class="ideas">{rows}</div></div>')
n_ideas = len(ROAD) + len(HELP) + sum(len(k) for _, _, k in GROUPS if k)

# 3. tuning the logic: one column per family of settings, in the order tried
F = APPR["families"]
lo, hi, W, H, pad = 0.50, 0.85, 1000, 250, 44
cw = (W - pad - 10) / len(F)
ys = lambda v: H - 28 - (v - lo) / (hi - lo) * (H - 46)
cols, best, pts = [], 0, []
for k, f in enumerate(F):
    v = max(f["scores"]) if f["scores"] else lo
    kept = not f["use"].startswith(("Nothing", "Replaced"))
    best = max(best, v)
    x = pad + k * cw
    cols.append(f'<rect x="{x + cw * .18:.1f}" y="{ys(v):.1f}" width="{cw * .64:.1f}" height="{ys(lo) - ys(v):.1f}" rx="3" class="{"ck" if kept else "cd"}"><title>{k + 1}. {esc(f["name"])}: {v * 100:.1f} %</title></rect>'
                f'<text x="{x + cw / 2:.1f}" y="{H - 10}" class="cx">{k + 1}</text>')
    pts.append(f"{x + cw * .18:.1f},{ys(best):.1f} {x + cw * .82:.1f},{ys(best):.1f}")
ticks = "".join(f'<line x1="{pad}" x2="{W - 10}" y1="{ys(t):.1f}" y2="{ys(t):.1f}" class="gl"/><text x="{pad - 8}" y="{ys(t) + 4:.1f}" class="cy">{t * 100:.0f} %</text>' for t in (0.5, 0.6, 0.7, 0.8))
stair = (f'<div class="chartwrap"><svg viewBox="0 0 {W} {H}" role="img" aria-label="Best development score after each family of settings">{ticks}{"".join(cols)}'
         f'<polyline points="{" ".join(pts)}" class="run"/></svg></div>')
fam = "".join(f'<tr><td class="n">{k + 1}</td><td><b>{esc(f["name"])}</b><div class="muted">{esc(f["goal"])}</div></td><td>{esc(f["use"] if not f["use"].startswith(("Nothing", "Replaced")) else f["concl"])}</td><td class="n">{pct(max(f["scores"])) if f["scores"] else "-"}</td></tr>' for k, f in enumerate(F))

tried = f"""
<div class="tstrip"><div><b>{n_ideas}</b><span>ideas tried</span></div><div><b>3</b><span>parts in use: the model, the logic, the end check</span></div><div><b>{pct(RL.mean())}</b><span>best, on the test set</span></div></div>
<h2>From the first method to the best</h2>
<p class="lead">Every complete method, graded with today's rule on the 50-photo test set of 13 October (the trials were run before the sets were merged). The red line is the 95 % target. Click a method to see why we tried it.</p>
<div class="road">{road}</div>
<h2>Every idea, by the problem it tackled</h2>
<p class="lead">None of these is used on its own; what each group taught us is in its first line. The bars score each idea on the development photos with the earlier, stricter rule. They compare the ideas with each other; they are not test results.</p>
<div class="groups">{"".join(groups)}</div>
<h2>Tuning the logic, step by step</h2>
<p class="lead">{len(F)} families of settings, tried in this order. Each column is the best development score in that family; the line is the best so far. Green columns changed the logic we use.</p>
<div class="legend"><span><span class="key"></span>kept</span><span><span class="key fail"></span>not kept</span><span><span class="key line"></span>best so far</span></div>
{stair}
<details class="more"><summary>What each family changed</summary>
<div class="tablewrap"><table><tr><th>#</th><th>What was changed</th><th>Result</th><th>Best</th></tr>{fam}</table></div>
<p class="muted">{esc(APPR["also_tried"])}</p></details>"""

# ---------------------------------------------------------------- 6. test data
UNSEEN_TXT = f"on the other {SAT['unseen_only']['best']['photos']} photos RT-EOS units + logic scores {pct(SAT['unseen_only']['best']['combined'])}"
TPAR = json.load(open(TPATH)).get("parallel", {}) if TPATH.exists() else {}
PAR_NAMES = {"test_set": "test set"}
PAR_TXT = (" With all 15 cores of the laptop each working on a different photo (photos read from disk included), RT-EOS units + logic takes "
           + ", ".join(f"{secs(v['seconds'])} for the {PAR_NAMES[k]}" for k, v in TPAR.items()) + ".") if TPAR else ""
TKEYS = (("logic", "Logic alone"), ("model", "RT-EOS units"), ("both", "RT-EOS units + logic"))
if TIMING:
    trows = "".join(f'<tr><td>{esc(x["name"].replace(" (current test set)", ""))}</td><td class="n">{x["photos"]}</td>'
                    + "".join(f'<td class="n">{secs(set_time(x, k)[0])} · {secs(set_time(x, k)[1])}</td>' if set_time(x, k)[0] is not None else '<td class="n">-</td>' for k, _ in TKEYS) + "</tr>" for x in TSETS)
    timing_html = f"""<h2>Time</h2>
<p class="lead">Seconds per photo, then for the whole set, one photo at a time on an Apple M5 Pro laptop (CPU only, model already loaded, photo already read from disk).</p>
<div class="tablewrap"><table><tr><th>Test set</th><th>Photos</th>{"".join(f"<th>{n}: per photo · whole set</th>" for _, n in TKEYS)}</tr>{trows}</table></div>
<p class="muted">Before the speed-ups of 14 October 2026 (the same ladders, computed with fewer steps), RT-EOS units + logic took {secs(set_time(TSETS[0], "both_before")[0])} per photo on this set; now {secs(set_time(TSETS[0], "both")[0])}. Whole set = one photo after another.{PAR_TXT}</p>"""
else:
    timing_html = ""
testdata = f"""
<h2>How a unit is scored</h2>
<div class="cards3">
<div class="card"><h3>Half a unit</h3><p>A drawn unit is right when its top and bottom are both within half a unit (about 22 mm) of a hand-drawn unit, so every device keeps its unit number. The hand-drawn lines themselves are only accurate to about a seventh of a unit.</p></div>
<div class="card"><h3>Units in between count</h3><p>A drawn unit between two right units also counts as right: inside a large device the hand-drawn lines are only a guess.</p></div>
<div class="card"><h3>Per rack, then averaged</h3><p>Each photo's score is its right units divided by the larger of the hand-drawn and drawn counts, so extra units cost score. The photos are then averaged.</p></div></div>
<h2>The test set</h2>
<div class="tablewrap"><table><tr><th>Test set</th><th>Photos</th><th>Logic alone</th><th>RT-EOS units</th><th>RT-EOS units + logic</th></tr>
{"".join(f'<tr><td>{esc(x["name"].replace(" (current test set)", ""))}</td><td class="n">{x["photos"]}</td><td class="n">{pct(x["logic"])}</td><td class="n">{pct(x["rteos"])}</td><td class="n"><b>{pct(x["best"]) if x.get("best") is not None else "-"}</b></td></tr>' for x in TSETS)}</table></div>
<p class="muted">The test set shares {SAT["overlap_with_tuning"]["best"]} photos with the photos the end check was tuned on; {UNSEEN_TXT}. It is one set since 14 October 2026: the former best 50, 100, 200 and whole-rack sets merged, each photo once, after the quality rule and two reviews by eye.</p>
{timing_html}
<h2>Rack sizes</h2>
<p class="lead">A rack unit is 44.45 mm on 19-, 23- and 10-inch racks; only the width changes. Open Rack and ETSI racks use their own unit. The logic reads 19-inch, 23-inch and Open Rack racks on its own; 10-inch and ETSI racks are set by name, because in a photo they look like a 19-inch rack measured two units, or half a unit, at a time.</p>
<div class="tablewrap"><table><tr><th>Standard</th><th>Used for</th><th>Unit</th><th>How it is set</th><th>Logic: units right</th></tr>
{"".join(f'<tr><td><b>{esc(r["standard"])}</b></td><td>{esc(r["use"])}</td><td class="n">{r["unit_mm"]:g} mm</td><td><span class="pill {"ok" if r["how"] == "auto" else "plain"}">{r["how"]}</span></td><td class="n">{pct(r["score"])}</td></tr>' for r in RSIZES)}</table></div>
<p class="muted">Rack-size scores: the logic on the {N} test photos, each also stretched sideways to the standard's proportions.</p>"""

# ---------------------------------------------------------------- 7. conclusion
B = {x["name"].split(" (")[0].replace(" datacenter racks", ""): x for x in TSETS}
t50 = set_time(TSETS[0], "both")[0] if TIMING else None
LB = SAT["sets"]["best"]["loss_by_cause"]
RTS = SAT["retrain_summary"]
MODEL_FIX = LB["height"] + LB["model_only"] + LB["missing"]
crow = "".join(f'<tr><td>{esc(k)}</td><td class="n">{x["photos"]}</td><td class="n">{pct(x["logic"])}</td><td class="n">{pct(x["rteos"])}</td><td class="n"><b>{pct(x["best"])}</b></td></tr>' for k, x in B.items())
conclusion = f"""
<div class="hero"><div class="big"><b>{pct(RL.mean())}</b><span>of rack units found correctly</span>
<small>RT-EOS units + logic on the {N} photos of the test set; {pct(SAT["unseen_only"]["best"]["combined"])} on the {SAT["unseen_only"]["best"]["photos"]} the end check was not tuned on{f" · about {secs(t50)} per photo" if t50 else ""}</small></div>
<div class="what"><h2>Conclusion</h2><p>Use RT-EOS units + logic: the trained units model draws the ladder, and the image logic checks its top and bottom units. That is the number for front-on, close, well-lit rack photos. It is the saturation point of this approach: the remaining mistakes need agreed labels or a retrained units model, not more rules (see Debug).</p></div></div>
<h2>Final results</h2>
<div class="tablewrap"><table><tr><th>Test set</th><th>Photos</th><th>Logic alone</th><th>RT-EOS units</th><th>RT-EOS units + logic</th></tr>{crow}</table></div>
<div class="cards3">
<div class="card"><h3>What we use</h3><p>The units model draws one box per unit and its ladder rules join them. The logic measures the rack on its own. Each end unit the model was unsure about, lying beyond the logic's end or outside the rack, is removed.</p></div>
<div class="card"><h3>What it gains</h3><p>{(RL.mean() - M.mean()) * 100:.1f} points over the units model alone on the best {N}: better on {int((RL > M + 1e-9).sum())} racks, worse on {int((RL < M - 1e-9).sum())}. {int((RL >= 0.999).sum())} of the {N} racks have every unit right.</p></div>
<div class="card"><h3>Where it stops</h3><p>{LB["disputed"] * 100:.1f} points are units the labels leave out, and about {MODEL_FIX * 100:.1f} points need a better units model.</p></div></div>
<h2>Next steps</h2>
<div class="card"><ul>
<li><b>Agree the labels at the rack's ends.</b> Decide whether blank panels, empty units and top bars count, then correct the hand labels: worth up to {LB["disputed"] * 100:.1f} points of score on the test set.</li>
<li><b>Retrain the units model</b> with top bars, name plates and plinths marked as not a unit, more empty end units, and more racks whose unit height it misreads: worth up to {MODEL_FIX * 100:.1f} points. The list of cases is ready in <code>results/units/trials/retrain_cases.json</code>: {RTS["photos"]} photos, with {RTS["model_only"]} confident extra units, {RTS["missing"]} runs of missing end units and {RTS["height"]} racks whose unit height is off; {RTS["in_training_split"]} of these photos are already in the model's training photos, so the labels there need checking too.</li>
<li><b>Add front-on, close photos of whole racks</b> to the model's training data: that is what the product will see, and where the remaining misses are.</li>
<li><b>Keep the end check as it is.</b> Its settings rank first of {SAT["end_check_settings"]["settings"]} on the tuning photos, and learned checks do no better on unseen photos.</li></ul></div>"""

TABS = [("summary", "Summary", summary), ("how", "How it works", how), ("results", "Results", results), ("debug", "Debug", debug),
        ("tried", "Approaches tried", tried), ("data", "Test data", testdata), ("conclusion", "Conclusion", conclusion)]

# scripts/devices/build_device_report.py copies the string below by searching for its opening line: keep it one plain
# triple-quoted string, and never repeat that opening line anywhere else in this file
CSS = """
/* Layout: 90 % wide; white cards with soft depth on a pale cool-grey canvas; sticky white header with tabs; colour only for the three methods and the score pills */
:root { --bg:#f3f5f8; --panel:#ffffff; --surface:#f7f8fa; --fg:#14171c; --muted:#525a67; --line:#e4e7ec; --line-strong:#cfd4dc; --soft:#eef0f4;
  --shadow:0 1px 2px rgba(16,24,40,.05), 0 4px 14px rgba(16,24,40,.06); --shadow-hi:0 2px 4px rgba(16,24,40,.06), 0 12px 28px rgba(16,24,40,.10);
  --accent:#1d5fcf; --accent-soft:#eaf1fc; --best:#1d5fcf; --model:#9aa3af; --logic:#55657f; --warn:#d08a3c; --fail:#cfd4dc;
  --ok:#1a7f45; --ok-bg:#e7f4ec; --mid:#8a6200; --mid-bg:#fbf2d5; --bad:#b4372a; --bad-bg:#fbe7e3;
  --sans:"IBM Plex Sans","Segoe UI",system-ui,-apple-system,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#0f1114; --panel:#181b20; --surface:#1e2228; --fg:#e8eaee; --muted:#9aa3af; --line:#2a2f37; --line-strong:#3b424c; --soft:#252a31;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 4px 14px rgba(0,0,0,.25); --shadow-hi:0 2px 4px rgba(0,0,0,.45), 0 12px 28px rgba(0,0,0,.35);
  --accent:#6ea0ff; --accent-soft:#1b2a45; --best:#6ea0ff; --model:#6b7380; --logic:#9fb0cc; --warn:#e9a558; --fail:#3b424c;
  --ok:#58c986; --ok-bg:#17301f; --mid:#e3c76a; --mid-bg:#3a3118; --bad:#f09a86; --bad-bg:#40221d; color-scheme:dark } }
:root[data-theme="dark"] { --bg:#0f1114; --panel:#181b20; --surface:#1e2228; --fg:#e8eaee; --muted:#9aa3af; --line:#2a2f37; --line-strong:#3b424c; --soft:#252a31;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 4px 14px rgba(0,0,0,.25); --shadow-hi:0 2px 4px rgba(0,0,0,.45), 0 12px 28px rgba(0,0,0,.35);
  --accent:#6ea0ff; --accent-soft:#1b2a45; --best:#6ea0ff; --model:#6b7380; --logic:#9fb0cc; --warn:#e9a558; --fail:#3b424c;
  --ok:#58c986; --ok-bg:#17301f; --mid:#e3c76a; --mid-bg:#3a3118; --bad:#f09a86; --bad-bg:#40221d; color-scheme:dark }
* { box-sizing:border-box; } [hidden] { display:none !important; }
body { margin:0; background:var(--bg); color:var(--fg); font:400 16px/1.6 var(--sans); padding-inline:16px; padding-block:0 72px; -webkit-font-smoothing:antialiased; overflow-x:clip; }
.page { width:90%; margin:0 auto; }
b, strong { font-weight:600; }
header { position:sticky; top:env(safe-area-inset-top,0px); z-index:5; background:var(--panel); box-shadow:0 1px 0 var(--line), 0 4px 12px rgba(16,24,40,.04); margin-inline:calc(-5.56% - 16px); padding-inline:calc(5.56% + 16px); }
header .bar-top { display:flex; align-items:center; justify-content:space-between; gap:24px; flex-wrap:wrap; }
header h1 { font-size:1.15rem; font-weight:600; margin:0; letter-spacing:-.01em; white-space:nowrap; padding:14px 0; }
nav { display:flex; gap:4px; overflow-x:auto; padding:8px 0; }
nav button { font:500 .93rem var(--sans); color:var(--muted); background:none; border:0; border-radius:8px; padding:8px 14px; cursor:pointer; white-space:nowrap; }
nav button:hover { color:var(--fg); background:var(--soft); }
nav button[aria-selected="true"] { color:var(--accent); background:var(--accent-soft); }
button:focus-visible, summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
section { padding-top:32px; }
h2 { font-size:1.3rem; font-weight:600; letter-spacing:-.015em; margin:44px 0 14px; text-wrap:balance; }
section > h2:first-child { margin-top:0; }
h3 { font-size:1.05rem; font-weight:600; margin:0 0 6px; text-wrap:balance; }
p { margin:0 0 10px; max-width:80ch; } .lead { font-size:1rem; color:var(--muted); } .muted { color:var(--muted); font-size:.9rem; }
code { font:.86em var(--mono); background:var(--soft); padding:1px 5px; border-radius:4px; }
/* cards */
.card, .big, .what, .step, .tablewrap, .lstep, .cell, .rrow, .group, .tile, .tstrip div, .road, .chartwrap, .more, .bars-card { background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); }
.hero { display:grid; grid-template-columns:minmax(260px,0.75fr) minmax(0,2fr); gap:20px; align-items:stretch; }
.big { padding:28px; display:flex; flex-direction:column; justify-content:center; gap:10px; border-top:3px solid var(--best); }
.big b { font-size:clamp(3rem,5vw,4.2rem); line-height:1; font-weight:600; letter-spacing:-.03em; color:var(--best); font-variant-numeric:tabular-nums; }
.big span { font-weight:600; font-size:1.05rem; } .big small { color:var(--muted); font-size:.9rem; line-height:1.5; }
.what { padding:26px 28px; } .what h2 { margin:0 0 8px; } .what p { font-size:1.03rem; color:var(--muted); margin:0; }
.bars { display:flex; flex-direction:column; gap:14px; margin:0 0 10px; background:var(--panel); border:1px solid var(--line); border-radius:12px; box-shadow:var(--shadow); padding:22px 24px; }
.bar { display:grid; grid-template-columns:minmax(150px,260px) 1fr 5.5em; gap:16px; align-items:center; }
.bl { font-weight:500; } .bar b { text-align:right; font-variant-numeric:tabular-nums; font-family:var(--mono); font-weight:500; font-size:.95rem; }
.track { display:block; position:relative; height:12px; background:var(--soft); border-radius:6px; }
.fill { display:block; position:absolute; left:0; top:0; bottom:0; border-radius:6px; background:var(--logic); } .fill.model { background:var(--model); } .fill.best { background:var(--best); } .fill.warn { background:var(--warn); } .fill.fail { background:var(--fail); }
.legend { color:var(--muted); font-size:.86rem; display:flex; gap:6px 16px; flex-wrap:wrap; align-items:center; margin-top:4px; }
.key { display:inline-block; width:12px; height:8px; border-radius:2px; background:var(--logic); margin-right:4px; } .key.model { background:var(--model); } .key.best { background:var(--best); }
.cards3, .cards2 { display:grid; gap:16px; margin:18px 0; } .cards3 { grid-template-columns:repeat(auto-fit,minmax(260px,1fr)); } .cards2 { grid-template-columns:repeat(auto-fit,minmax(440px,1fr)); }
.card { padding:20px 22px; min-width:0; }
.card p { color:var(--muted); font-size:.95rem; } .card p:last-child { margin-bottom:0; }
.card ul { margin:6px 0 0; padding-left:1.1em; display:flex; flex-direction:column; gap:6px; color:var(--muted); } .card ul b { color:var(--fg); }
.cards3 .card:nth-child(1) { border-top:3px solid var(--best); } .cards3 .card:nth-child(2) { border-top:3px solid var(--model); } .cards3 .card:nth-child(3) { border-top:3px solid var(--logic); }
.card.rule { margin-top:20px; border-left:3px solid var(--accent); } .card.rule p { color:var(--fg); }
.tablewrap { overflow-x:auto; padding:4px 20px; } table { border-collapse:collapse; width:100%; }
th, td { text-align:left; padding:12px 16px 12px 0; border-bottom:1px solid var(--line); vertical-align:top; }
th { font:500 .72rem/1.2 var(--mono); letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }
tr:last-child td { border-bottom:0; } tr:not(:first-child):hover td { background:var(--surface); }
td.n { font-family:var(--mono); font-variant-numeric:tabular-nums; white-space:nowrap; font-size:.93rem; }
.pill { display:inline-block; font:500 .78rem/1 var(--mono); padding:4px 8px; border-radius:999px; background:var(--soft); color:var(--fg); white-space:nowrap; }
.pill.ok { background:var(--ok-bg); color:var(--ok); } .pill.mid { background:var(--mid-bg); color:var(--mid); } .pill.bad { background:var(--bad-bg); color:var(--bad); } .pill.plain { background:var(--soft); color:var(--muted); }
.steps { display:flex; flex-direction:column; gap:16px; }
.step { display:grid; grid-template-columns:44px minmax(0,1fr) minmax(0,1.1fr); gap:28px; padding:24px; align-items:start; }
.num { font:600 .95rem/1 var(--mono); color:#fff; background:var(--accent); width:36px; height:36px; border-radius:999px; display:grid; place-items:center; box-shadow:0 2px 6px rgba(29,95,207,.3); }
.txt p { color:var(--muted); }
figure { margin:0; min-width:0; } img { max-width:100%; display:block; border-radius:8px; cursor:zoom-in; }
.step img { width:100%; max-height:460px; object-fit:contain; background:var(--surface); border:1px solid var(--line); }
.lsteps { display:grid; grid-template-columns:repeat(auto-fill,minmax(240px,1fr)); gap:18px; }
.lstep, .cell { padding:10px; display:flex; flex-direction:column; gap:10px; transition:box-shadow .15s, transform .15s; }
.lstep:hover, .cell:hover, .tile:hover, .rrow:hover { box-shadow:var(--shadow-hi); }
.lstep img { width:100%; aspect-ratio:4/5; object-fit:cover; background:var(--surface); } .lstep figcaption { display:flex; flex-direction:column; gap:4px; color:var(--fg); font-size:.92rem; line-height:1.5; margin:0; padding:0 4px 6px; } .lstep figcaption span { color:var(--muted); }
.switch { display:inline-flex; gap:2px; background:var(--panel); border:1px solid var(--line); box-shadow:var(--shadow); padding:4px; border-radius:10px; margin:0 0 16px; flex-wrap:wrap; }
.switch button { font:500 .9rem var(--sans); border:0; background:none; color:var(--muted); padding:7px 14px; border-radius:7px; cursor:pointer; }
.switch button[aria-pressed="true"] { background:var(--accent); color:#fff; }
.grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:16px; }
.cell img { width:100%; aspect-ratio:3/4; object-fit:cover; } figcaption { font-size:.85rem; color:var(--muted); display:flex; gap:8px; align-items:center; font-variant-numeric:tabular-nums; }
.cell figcaption { padding:0 4px 4px; }
.pair { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:12px; } .pair img { width:100%; aspect-ratio:3/4; object-fit:cover; } .pair figcaption { margin-top:6px; }
.tbar { display:grid; grid-template-columns:1fr auto; gap:10px; align-items:center; } .tbar .track { height:6px; } .tbar b { font-variant-numeric:tabular-nums; font:500 .85rem var(--mono); }
.tdet { padding:4px 18px 18px; border-top:1px solid var(--line); padding-top:14px; } .tdet p { font-size:.95rem; }
.apics { display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:12px; margin-top:10px; } .apics img { width:100%; aspect-ratio:3/4; object-fit:cover; }
.more { margin-top:20px; padding:16px 20px; } .more summary { cursor:pointer; font-weight:600; color:var(--accent); } .more[open] summary { margin-bottom:12px; } .more .tablewrap { box-shadow:none; padding:0; border:0; }
.tstrip { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:16px; margin:0 0 8px; }
.tstrip div { padding:20px 22px; display:flex; flex-direction:column; gap:6px; }
.tstrip b { font-size:2rem; line-height:1; font-weight:600; letter-spacing:-.02em; font-variant-numeric:tabular-nums; color:var(--best); } .tstrip span { color:var(--muted); font-size:.9rem; }
.road { display:flex; flex-direction:column; gap:10px; background:none; border:0; box-shadow:none; }
.rrow summary { list-style:none; cursor:pointer; padding:16px 20px; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px 16px; align-items:center; }
.rrow summary::-webkit-details-marker, .idea summary::-webkit-details-marker { display:none; }
.rrow .bars, .rrow .bar { box-shadow:none; } .rrow .bar { grid-column:1; grid-template-columns:minmax(180px,340px) 1fr 5.5em; } .rrow .pill { grid-column:2; grid-row:1; width:7.5em; text-align:center; } .rgoal { grid-column:1 / -1; color:var(--muted); font-size:.9rem; }
.groups { columns:2 520px; column-gap:16px; }
.group { min-width:0; break-inside:avoid; margin-bottom:16px; padding:20px 22px; }
.ghead { display:flex; justify-content:space-between; align-items:baseline; gap:10px; padding-bottom:6px; } .ghead h3 { margin:0; }
.learn { margin:0 0 12px; color:var(--fg); font-size:.93rem; background:var(--accent-soft); border-radius:8px; padding:10px 12px; }
.ideas { display:flex; flex-direction:column; }
.idea { border-top:1px solid var(--line); }
.irow { display:grid; grid-template-columns:minmax(0,1fr) minmax(120px,190px); gap:16px; align-items:center; padding:10px 0; }
summary.irow { list-style:none; cursor:pointer; } summary.irow:hover .iname b { color:var(--accent); }
.iname { display:flex; flex-direction:column; min-width:0; } .iname b { font-weight:500; } .iname span { color:var(--muted); font-size:.86rem; line-height:1.45; }
.iscore { justify-self:stretch; text-align:right; } .iscore .tbar { width:100%; }
.idea .tdet { padding:2px 0 16px; border-top:0; }
.tiles { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:14px; align-items:start; }
.tile summary { list-style:none; cursor:pointer; padding:16px 18px; display:grid; grid-template-columns:auto 1fr; gap:6px 10px; align-items:center; }
.tile summary::-webkit-details-marker { display:none; }
.tile summary h3, .tile summary p, .tile summary .tbar { grid-column:1 / -1; } .tile summary h3 { margin:0; } .tile summary p { color:var(--muted); font-size:.9rem; margin:0; }
.tnum { color:var(--muted); font:500 .78rem var(--mono); } .tile summary .pill { justify-self:start; }
.chartwrap { overflow-x:auto; padding:16px 20px; } .chartwrap svg { width:100%; min-width:560px; height:auto; display:block; }
svg .gl { stroke:var(--line); } svg .cy, svg .cx { fill:var(--muted); font:12px var(--mono); } svg .cy { text-anchor:end; } svg .cx { text-anchor:middle; }
svg .ck { fill:var(--logic); } svg .cd { fill:var(--fail); } svg .run { fill:none; stroke:var(--best); stroke-width:2.5; }
.key.fail { background:var(--fail); } .key.line { height:3px; background:var(--best); }
.cause .chead { display:flex; justify-content:space-between; align-items:baseline; gap:12px; } .cause .chead h3 { margin:0; }
.cause .short { color:var(--fg); font-weight:500; margin:6px 0 10px; }
.cause dl { margin:0 0 12px; display:grid; grid-template-columns:max-content minmax(0,1fr); gap:6px 14px; font-size:.93rem; }
.cause dt { color:var(--muted); font-weight:500; } .cause dd { margin:0; color:var(--fg); }
.dgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:12px; }
.dcell { background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:8px; box-shadow:var(--shadow); } .dcell img { width:100%; height:auto; border-radius:8px; }
.dcell figcaption { font-size:.85rem; color:var(--muted); margin-top:6px; }
.verdict { border-left:3px solid var(--best); } .verdict p { font-size:1.02rem; }
@media (max-width:640px) { .cause dl { grid-template-columns:1fr; gap:2px; } .cause dd { margin-bottom:6px; } }
#lb { position:fixed; inset:0; background:rgba(15,17,21,.9); display:grid; place-items:center; padding:20px; z-index:20; }
#lb img { max-width:96vw; max-height:88vh; cursor:default; } #lb button { position:absolute; top:calc(12px + env(safe-area-inset-top,0px)); right:16px; font:500 .9rem var(--sans); border:0; border-radius:8px; padding:8px 14px; background:var(--panel); color:var(--fg); cursor:pointer; }
@media (prefers-reduced-motion: reduce) { .lstep, .cell { transition:none; } }
@media (max-width:1000px) { .hero { grid-template-columns:1fr; } .step { grid-template-columns:44px minmax(0,1fr); } .step figure { grid-column:2; } }
@media (max-width:820px) { .page { width:100%; } header { margin-inline:-16px; padding-inline:16px; } header h1 { padding-bottom:0; } .step { grid-template-columns:1fr; gap:14px; padding:18px; } .step figure { grid-column:auto; } .bar { grid-template-columns:1fr 4.8em; } .bl { grid-column:1 / -1; } .cards2 { grid-template-columns:1fr; } }
@media (max-width:640px) { .irow { grid-template-columns:1fr; gap:4px; } .iscore { text-align:left; } .rrow summary { grid-template-columns:1fr; } .rrow .pill { grid-column:1; grid-row:auto; } }
"""
JS = """
(function(){var tabs=[].slice.call(document.querySelectorAll('nav button')),secs=[].slice.call(document.querySelectorAll('section.tab'));
function show(id){secs.forEach(function(s){s.hidden=s.dataset.name!==id;});tabs.forEach(function(b){b.setAttribute('aria-selected',b.dataset.tab===id?'true':'false');});
try{localStorage.setItem('units-portal-tab',id);}catch(e){} window.scrollTo(0,0);}
tabs.forEach(function(b){b.addEventListener('click',function(){show(b.dataset.tab);try{history.replaceState(null,'','#'+b.dataset.tab);}catch(e){}});});
var start=(location.hash||'').slice(1);if(!start){try{start=localStorage.getItem('units-portal-tab')||'';}catch(e){}}
show(secs.some(function(s){return s.dataset.name===start;})?start:'summary');
document.querySelectorAll('.switch button').forEach(function(b){b.addEventListener('click',function(){var v=b.dataset.view;
document.querySelectorAll('.switch button').forEach(function(x){x.setAttribute('aria-pressed',x===b?'true':'false');});
document.querySelectorAll('#tab-results .v').forEach(function(e){e.hidden=!e.classList.contains('v-'+v);});});});
var lb=document.getElementById('lb'),im=document.getElementById('lbimg');
document.addEventListener('click',function(e){var i=e.target.closest&&e.target.closest('img[data-zoom]');if(i&&!lb.contains(i)){e.preventDefault();im.src=i.src;im.alt=i.alt;lb.hidden=false;}});
function close(){lb.hidden=true;im.src='';} document.getElementById('lbclose').addEventListener('click',close);
lb.addEventListener('click',function(e){if(e.target===lb)close();}); document.addEventListener('keydown',function(e){if(e.key==='Escape')close();});})();
"""
page = f"""<title>Rack Unit Finder</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>{CSS}</style>
<div class="page">
<header><div class="bar-top"><h1>Rack Unit Finder</h1><nav role="tablist">{"".join(f'<button role="tab" data-tab="{k}" aria-selected="false">{v}</button>' for k, v, _ in TABS)}</nav></div></header>
{"".join(tab(k, body) for k, _, body in TABS)}
</div>
<div id="lb" hidden><button type="button" id="lbclose">Close</button><img id="lbimg" alt=""></div>
<script>{JS}</script>
"""
(OUT / "index.html").write_text(page)
stale = [p for p in IMG.iterdir() if p.name not in used]
for p in stale:
    p.unlink()   # pictures no tab uses any more
print(f"written {OUT / 'index.html'} | {len(TABS)} tabs | {len(used)} pictures ({len(stale)} unused removed) | best {pct(RL.mean())} model {pct(M.mean())} logic {pct(L.mean())}")
