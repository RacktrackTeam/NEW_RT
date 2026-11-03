"""Build the "Rack Occupancy" portal: one page, seven tabs, same look as the Rack Unit Finder portal.

    python3 scripts/occupancy/build_occupancy_portal.py

Tabs: Overview · Every approach · The ensemble · Uncertain units · Real photos · Label check · Mistakes.
Inputs (all after the label fixes, the removed test unit left out of every count):
results/occupancy/probs/*.npz, models/v2/*_eval.json, data/datasets/occupancy/labels/test_removed.csv,
data/eval/ensemble_portal/data.json and its img/ (scripts/occupancy/ensemble_portal_data.py, two_test_sets.py,
ensemble_labelcheck.py, ensemble_angle.py). Writes reports/occupancy/{index.html,img/}.
"""
import html
import json
import pathlib
import shutil

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[2]
V2 = ROOT / "models/v2"
PROBS = ROOT / "results" / "occupancy" / "probs"
SRC = ROOT / "data/eval/ensemble_portal"
OUT = ROOT / "reports" / "occupancy"
IMG = OUT / "img"
CUT = 0.65
UNIT_FINDER_URL = "https://claude.ai/artifact/WKEbevo8qtVxGRkGF8dx7a"

D = json.load(open(SRC / "data.json"))
NW = {4: "four", 5: "five", 6: "six", 7: "seven"}[len(D["members"])]       # the ensemble's member count, in words
GONE = set(pd.read_csv(ROOT / "data" / "datasets" / "occupancy" / "labels" / "test_removed.csv").crop)

# key: (name, how it works, libraries / models)
APPROACHES = {
    "ensemble": (f"Ensemble of {len(D['members'])}", f"Averages P(occupied) of the {NW} models below; each counts 1/{len(D['members'])}.",
                 f"numpy mean over the {NW} members"),
    "yolov8m_cls": ("YOLOv8m-cls", "A YOLOv8m classification network fine-tuned end to end on the 224×224 unit crops.",
                    "ultralytics YOLOv8m-cls"),
    "yolo11s_cls": ("YOLO11s-cls", "The smaller YOLO11s classification network, fine-tuned on the same crops.",
                    "ultralytics YOLO11s-cls"),
    "embed_dinov2": ("DINOv2 + logistic regression", "Frozen DINOv2 ViT-B/14 features (768 numbers per unit) and a logistic regression head.",
                     "torch hub DINOv2 ViT-B/14 · sklearn LogisticRegression C=30"),
    "teammate": ("ResNet50 + logistic regression", "Frozen ResNet50 features and a logistic regression head (the teammate's recipe, retrained on v2).",
                 "torchvision ResNet50 · sklearn LogisticRegression"),
    "embed_clip": ("CLIP + small network", "Frozen CLIP ViT-B/32 features (512 numbers) and a one-layer neural network.",
                   "open_clip ViT-B/32 · sklearn MLPClassifier(512)"),
    "classical": ("Hand-made features + trees", "No deep learning: 84 colour, edge and texture measurements per unit and a tree ensemble. Not part of the ensemble.",
                  "OpenCV features · sklearn HistGradientBoosting"),
    "fusion_t2": ("Fusion (teammate 2)", "Device finder + CLIP probe + edge features, combined. Scored here on its CLIP and edge votes only, because the crops have no rack photo for the device-finder vote.",
                  "CLIP · OpenCV edges · YOLO device finder"),
}


def metrics(key):
    z = np.load(PROBS / f"{key}.npz", allow_pickle=False)
    keep = ~np.isin(z["files_test"].astype(str), list(GONE))
    out = {}
    for split, k in (("test", keep), ("train", np.ones(len(z["y_train"]), bool))):
        p, y = z[f"p_{split}"][k].astype(float), z[f"y_{split}"][k]
        right = (p >= 0.5) == (y == 1)
        sure = np.maximum(p, 1 - p) >= CUT
        out[split] = {"n": int(len(y)), "acc": float(right.mean()), "uncertain": float(1 - sure.mean()),
                      "acc_sure": float(right[sure].mean()), "wrong": int((~right).sum()),
                      "wrong_sure": int((~right & sure).sum())}
    return out


def knn_metrics():
    z = np.load(PROBS / "knn_v2_unseen.npz", allow_pickle=False)
    p, y = z["p"], z["y"]
    right = (p >= 0.5) == (y == 1)
    e = json.load(open(V2 / "knn_v2_eval.json"))
    return {"n": int(len(y)), "acc": float(right.mean()), "uncertain": e["uncertain_pct"] / 100,
            "acc_sure": e["accuracy_when_sure"], "wrong": int((~right).sum())}


M = {k: metrics(k) for k in APPROACHES}
KNN = knn_metrics()
F = D["funnel"]


# ----------------------------------------------------------------------------- pictures
def copy(rel):
    """Copy one picture from the ensemble portal into this portal's img/ and return its published path."""
    if not rel:
        return ""
    src = SRC / rel
    if not src.exists():
        return ""
    shutil.copy2(src, IMG / src.name)
    return f"img/{src.name}"


if OUT.exists():
    shutil.rmtree(OUT)
IMG.mkdir(parents=True)

units = []
for u in D["units"]:
    units.append({"id": u["id"], "cls": u["class"], "truth": u["truth"], "p": round(u["p_occupied"], 3),
                  "outcome": u["outcome"], "leaning": u["leaning"], "side": bool(u.get("side")),
                  "votes": {k: round(v, 3) for k, v in u["votes"].items()},
                  "verdict": u.get("verdict"), "reason": u.get("reason", ""), "really": u.get("really"),
                  "crop": copy(u["crop"]), "ctx": copy(u["ctx"]) if u["outcome"] == "wrong" else ""})
samples = {k: [copy(p) for p in v] for k, v in D["samples"].items()}
for s in D["testsets"].values():
    for it in s["items"]:
        it["img"], it["cut"] = copy(it["img"]), copy(it["cut"])
LC = D["label_check"]
lc_show = []
for found in ("batch not_trained_429_v6 + model disagree", "model disagrees"):
    for it in [i for i in LC["items"] if i["found_by"] == found and i["verdict"] == "wrong"][:6]:
        lc_show.append({**it, "img": copy(it["img"])})


# ----------------------------------------------------------------------------- helpers
def e(s):
    return html.escape(str(s))


def pct(x, d=1):
    return f"{100 * x:.{d}f} %"


def n(x):
    return f"{x:,}"


def bars(rows, lo=0.75, hi=1.0, target=None):
    """rows: (label, value 0-1, css class). Horizontal bars on one scale from lo to hi."""
    out = ['<div class="bars"><span class="h"></span><span class="h">'
           f'{pct(lo, 0)} → {pct(hi, 0)}</span><span class="h">right</span>']
    for lab, v, cls in rows:
        w = max(0, min(1, (v - lo) / (hi - lo))) * 100
        t = f'<s style="left:{(target - lo) / (hi - lo) * 100:.1f}%"></s>' if target else ""
        out.append(f'<span class="lab">{e(lab)}</span><span class="bar {cls}"><i style="width:{w:.1f}%"></i>{t}</span>'
                   f'<span class="v">{pct(v)}</span>')
    return "".join(out) + "</div>"


def kpi(v, label, cls=""):
    return f'<div class="{cls}"><b>{v}</b><span>{label}</span></div>'


def tag(status):
    return f'<span class="tag {status}">{status}</span>'


# ----------------------------------------------------------------------------- tabs
ens, best1 = M["ensemble"]["test"], M["yolov8m_cls"]["test"]
order = sorted(APPROACHES, key=lambda k: -M[k]["test"]["acc"])

overview = f"""
<h2>Is each rack unit occupied?</h2>
<p>After the Rack Unit Finder has found every unit in a rack photo, each unit is cut out and given one of three answers:
{tag("occupied")} {tag("unoccupied")} or {tag("uncertain")}. Uncertain units go to a person and are never counted as free space.
This portal shows how that answer is made and how often it is right, on the v2 dataset after the label fixes.</p>
<div class="kpis">
{kpi(pct(ens["acc"]), f"right on {n(ens['n'])} test units (Ensemble of {len(D['members'])})", "best")}
{kpi(pct(ens["acc_sure"]), "right when it gives an answer")}
{kpi(pct(ens["uncertain"]), f"sent to a person ({n(F['uncertain'])} units)")}
{kpi(n(F["decided_wrong"]), "wrong answers that nobody checks")}
</div>
<h3>From photo to answer</h3>
<div class="flow">
<div class="node"><b>1 · Find units</b><span>Rack Unit Finder (weights/rack_unit_finder.pt + unit ladder)</span></div><i class="arr"></i>
<div class="node"><b>2 · Cut out</b><span>each unit squashed to 224×224, like the training crops</span></div><i class="arr"></i>
<div class="node"><b>3 · Score</b><span>the chosen model gives P(occupied) from 0 to 1</span></div><i class="arr"></i>
<div class="node"><b>4 · Second opinion</b><span>RT-EOS device finder looks at the whole photo</span></div><i class="arr"></i>
<div class="node"><b>5 · Answer</b><span>occupied · unoccupied · uncertain</span></div>
</div>
<h3>The rules for step 5</h3>
<div class="two">
<div class="card"><h4>Less than 65 % sure → uncertain</h4><p>P(occupied) ≥ 0.65 is occupied, ≤ 0.35 is unoccupied, anything between goes to a person.</p></div>
<div class="card"><h4>Side-angle photo → 90 % sure needed</h4><p>When a DINOv2 photo classifier says the rack is shot from the side or a steep angle, the bar rises to 90 %. Chosen on 9 Oct 2026.</p></div>
<div class="card"><h4>Model says free, device finder sees a device → uncertain</h4><p>The device finder never overrules; a person decides. Team rule since 8 Oct 2026, for every approach.</p></div>
</div>
<h3>What the models learned from</h3>
<p class="muted">{n(D["datasets"]["train"]["units"])} training crops from {n(D["datasets"]["train"]["photos"])} photos, half occupied and half unoccupied. A blank (closed) panel counts as unoccupied.</p>
<div class="two">
<div><h4>{tag("occupied")} switches, servers, patch panels, PDUs, UPSs …</h4><div class="shots">{"".join(f'<figure class="shot small"><img src="{p}" alt="Occupied training crop" loading="lazy"></figure>' for p in samples["occupied"])}</div></div>
<div><h4>{tag("unoccupied")} empty slots and blank panels</h4><div class="shots">{"".join(f'<figure class="shot small"><img src="{p}" alt="Unoccupied training crop" loading="lazy"></figure>' for p in samples["unoccupied"])}</div></div>
</div>
<p class="stamp">Units on the rack itself: see the <a href="{UNIT_FINDER_URL}" target="_blank" rel="noopener">Rack Unit Finder</a> portal.</p>
"""

rows = "".join(
    f'<tr><td><b>{e(APPROACHES[k][0])}</b><br><span class="muted">{e(APPROACHES[k][1])}</span>'
    f'<div class="tools"><b>Uses</b>{e(APPROACHES[k][2])}</div></td>'
    f'<td class="n">{pct(M[k]["test"]["acc"])}</td><td class="n">{n(M[k]["test"]["wrong"])}</td>'
    f'<td class="n">{pct(M[k]["test"]["uncertain"])}</td><td class="n">{pct(M[k]["test"]["acc_sure"])}</td>'
    f'<td class="n">{pct(M[k]["train"]["acc"])}</td><td class="n">{100 * (M[k]["train"]["acc"] - M[k]["test"]["acc"]):+.1f}</td></tr>'
    for k in order)
approaches = f"""
<h2>Every approach on the same {n(ens["n"])} test units</h2>
<p>None of these units, or their rack photos, were used in training. Only the model's score is tested here; the device finder's second opinion is not included.</p>
{bars([(APPROACHES[k][0], M[k]["test"]["acc"], "best" if k == "ensemble" else ("fail" if M[k]["test"]["acc"] < .9 else "model")) for k in order], lo=0.75)}
<div class="tablewrap"><table class="detail">
<thead><tr><th>Approach</th><th>Right</th><th>Wrong</th><th>Uncertain at 65 %</th><th>Right when sure</th><th>Right on training</th><th>Gap (points)</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="muted">A big gap between training and test means the model partly memorised its training crops. Test numbers are the ones to trust.</p>
<div class="card"><h4>DINOv2-L k-NN (teammate 1, version 2)</h4>
<p>Each unit is embedded with DINOv2 ViT-L/14 and compared with about 5,000 labelled examples; the 25 nearest vote, and similar-looking units in the same photo share votes.
Its example bank contains most v2 test photos, so it is scored only on the {n(KNN["n"])} test units from photos it has never seen:</p>
<div class="kpis">{kpi(pct(KNN["acc"]), f"right on {n(KNN['n'])} unseen units")}{kpi(pct(KNN["uncertain"]), "uncertain")}{kpi(pct(KNN["acc_sure"]), "right when sure")}</div></div>
"""

mem = [m for m in D["members"]]


def wrong_cell(k):
    return f'<span class="badge bad">{k}</span>' if k >= 5 else str(k)


by_cls = "".join(
    f'<tr><td>{e(c["class"])}</td><td>{tag(c["truth"])}</td><td class="n">{c["units"]}</td>'
    f'<td class="n">{pct(c["decided_right"] / c["units"])}</td><td class="n">{c["uncertain"]}</td>'
    f'<td class="n">{wrong_cell(c["decided_wrong"])}</td></tr>'
    for c in D["by_class"])
ensemble = f"""
<h2>The ensemble: {NW} models, one average</h2>
<p>The ensemble is not a separate model. It runs {NW} models that look at a unit in different ways and averages their {NW} scores.
The average then follows the same rules as any single model. It is the default in the app.</p>
<div class="flow">
<div class="node"><b>One unit</b><span>224×224 cut-out</span></div><i class="arr"></i>
<div class="node"><b>{NW.capitalize()} scores</b><span>{", ".join(e(m["name"]) for m in mem)}</span></div><i class="arr"></i>
<div class="node"><b>Average</b><span>each model counts 1/{len(mem)}</span></div><i class="arr"></i>
<div class="node"><b>Answer</b><span>65 % rule, then the device finder check</span></div>
</div>
{bars([(m["name"], m["accuracy"], "model") for m in mem] + [(f"Average of all {NW}", D["ensemble_accuracy"], "best")], lo=0.75)}
<h3>Three real test units</h3>
<p class="muted">Each dot is one model's score; the diamond is the average, which decides. Shaded: free (left), uncertain (middle), occupied (right).</p>
<div id="ens-cases" class="cases"></div>
<h3>By kind of unit</h3>
<div class="tablewrap"><table class="detail"><thead><tr><th>Hand label</th><th>Truth</th><th>Units</th><th>Answered right</th><th>Uncertain</th><th>Answered wrong</th></tr></thead><tbody>{by_cls}</tbody></table></div>
<p class="muted">UPS units cause most wrong answers: floor-standing UPS boxes photographed below the rack look like an empty gap in a unit-sized crop. Many of them are labelling mistakes (see Label check).</p>
"""

sweep = D["sweep"]
NOW = ' class="now"'
sw_rows = "".join(
    f'<tr{NOW if abs(s["cutoff"] - CUT) < 1e-6 else ""}><td class="n">{pct(s["cutoff"], 0)}</td><td class="n">{s["uncertain_n"]} ({pct(s["uncertain"])})</td>'
    f'<td class="n">{s["wrong_unflagged"]}</td><td class="n">{pct(s["accuracy_decided"])}</td></tr>' for s in sweep)
A = D["angle"]
W = 640; H = 240; L = 52; R = 20; T = 16; B = 40


def sx(c):
    return L + (c - 0.5) / 0.45 * (W - L - R)


def sy(v, top):
    return T + (1 - v / top) * (H - T - B)


topu, topw = 0.6, 60
line_u = " ".join(f"{sx(s['cutoff']):.1f},{sy(s['uncertain'], topu):.1f}" for s in sweep)
line_w = " ".join(f"{sx(s['cutoff']):.1f},{sy(s['wrong_unflagged'] / topw * topu, topu):.1f}" for s in sweep)
chart = (f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Units sent to a person and unchecked wrong answers, by cut-off">'
         + "".join(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{sy(v, topu):.1f}" y2="{sy(v, topu):.1f}"/>'
                   f'<text class="axis" x="{L - 6}" y="{sy(v, topu) + 4:.1f}" text-anchor="end">{int(v * 100)} %</text>'
                   f'<text class="axis" x="{W - R + 4}" y="{sy(v, topu) + 4:.1f}" text-anchor="start"></text>' for v in (0, .2, .4, .6))
         + "".join(f'<text class="axis" x="{sx(c):.1f}" y="{H - B + 16}" text-anchor="middle">{int(c * 100)}</text>' for c in (.5, .6, .7, .8, .9))
         + f'<text class="lbl" x="{(L + W - R) / 2}" y="{H - 6}" text-anchor="middle">cut-off: how sure the model must be (%)</text>'
         + f'<line x1="{sx(CUT):.1f}" x2="{sx(CUT):.1f}" y1="{T}" y2="{H - B}" style="stroke:var(--accent);stroke-dasharray:4 3"/>'
         + f'<text class="lbl" x="{sx(CUT) + 4:.1f}" y="{T + 10}">today 65 %</text>'
         + f'<polyline points="{line_u}" style="fill:none;stroke:var(--unc);stroke-width:2.5"/>'
         + f'<polyline points="{line_w}" style="fill:none;stroke:var(--bad);stroke-width:2.5"/>'
         + "".join(f'<circle cx="{sx(s["cutoff"]):.1f}" cy="{sy(s["uncertain"], topu):.1f}" r="3.5" style="fill:var(--unc)"><title>{pct(s["cutoff"], 0)}: {s["uncertain_n"]} to a person</title></circle>'
                   f'<circle cx="{sx(s["cutoff"]):.1f}" cy="{sy(s["wrong_unflagged"] / topw * topu, topu):.1f}" r="3.5" style="fill:var(--bad)"><title>{pct(s["cutoff"], 0)}: {s["wrong_unflagged"]} unchecked wrong</title></circle>' for s in sweep)
         + "</svg>")
uncertain = f"""
<h2>Uncertain units: how sure must the model be?</h2>
<p>Every unit gets one of two outcomes: an answer, or a person checks it. The cut-off trades one against the other.
Raising it sends more units to a person and leaves fewer wrong answers unchecked.</p>
<div class="kpis">{kpi(f"{F['uncertain']}", f"units to a person at 65 % ({pct(F['uncertain'] / F['units'])})")}
{kpi(f"{F['uncertain_wrong']}", "of those the model would have got wrong")}{kpi(f"{F['decided_wrong']}", "wrong answers left unchecked")}</div>
<p><span class="sw" style="background:var(--unc)"></span>share of units sent to a person (left scale)
&nbsp; <span class="sw" style="background:var(--bad)"></span>wrong answers nobody checks (0 → {topw}, same height scale)</p>
{chart}
<div class="tablewrap"><table class="detail"><thead><tr><th>Cut-off</th><th>Sent to a person</th><th>Wrong, unchecked</th><th>Right when answered</th></tr></thead><tbody>{sw_rows}</tbody></table></div>
<h3>Side-angle photos</h3>
<p>{A["how"]} {A["side_photos"]} of {A["photos"]} test photos ({n(A["side_units"])} units) were marked by eye.
The remaining wrong answers on high-resolution photos were all side-angle shots, so on those photos the model must now be 90 % sure.</p>
"""

TS = D["testsets"]


def ts_card(key, title, desc):
    s = TS[key]
    rows = ""
    for lab, x in (("With the side-angle rule (in use)", s), ("Without the side-angle rule", s["without_side_rule"])):
        rows += (f'<tr><td>{lab}</td><td class="n">{n(x["answered"])}</td><td class="n">{n(x["uncertain"])} ({pct(x["uncertain"] / x["units"])})</td>'
                 f'<td class="n">{x["wrong_answers"]}</td><td class="n">{pct(1 - x["wrong_answers"] / x["answered"], 2)}</td></tr>')
    items = "".join(
        f'<figure class="shot wide"><img src="{it["img"]}" alt="Unit in its photo" loading="lazy">'
        f'<figcaption>{tag(it["truth"])} model said {e(it["model"])} ({pct(it["sure"], 0)} sure){" · side angle" if it["side"] else ""}<br>{e(it["why"])}</figcaption></figure>'
        for it in s["items"] if it["img"])
    return f"""<div class="card"><div class="head"><h4>{title}</h4><span class="muted">{n(s["units"])} units · {n(s["photos"])} photos · {n(s["occupied"])} occupied / {n(s["unoccupied"])} unoccupied</span></div>
<p>{desc}</p>
<div class="tablewrap"><table class="detail"><thead><tr><th></th><th>Answered</th><th>To a person</th><th>Wrong answers</th><th>Answers right</th></tr></thead><tbody>{rows}</tbody></table></div>
{"<h4>The wrong answers</h4><div class='shots'>" + items + "</div>" if items else ""}</div>"""


photos = f"""
<h2>Real photos, unit by unit</h2>
<p>The crop test above uses units from a hand-drawn polygon. These two sets run the whole rule chain (65 % / 90 % side-angle cut-off) on hand-labelled units in full rack photos.</p>
{ts_card("overall", "All test photos", "Every test-split photo. The 17 extra photos from the test folder (218 units) are no longer on the drive, so they left this set on 19 Oct 2026.")}
{ts_card("highres", "High-resolution photos only", "The subset where the camera was close and sharp: the conditions we ask field teams to shoot in.")}
"""

sp = LC["splits"]
lc_rows = "".join(
    f'<tr><td>{s.title()}</td><td class="n">{n(v["units"])}</td><td class="n">{v["checked"]}</td><td class="n">{v["wrong"]}</td>'
    f'<td class="n">{v["removed"]}</td><td class="n">{v["before"]["occupied"]} / {v["before"]["unoccupied"]}</td><td class="n">{v["after"]["occupied"]} / {v["after"]["unoccupied"]}</td></tr>'
    for s, v in sp.items())
ba = "".join(f'<tr><td>{e(b["class"])}</td><td class="n">{b["batch_units"]}</td><td class="n">{pct(b["batch_agree"], 0)}</td><td class="n">{pct(b["other_agree"], 0)}</td></tr>'
             for b in LC["batch_agreement"] if b["batch_units"] >= 10)
lc_imgs = "".join(
    f'<figure class="shot"><img src="{it["img"]}" alt="Relabelled unit" loading="lazy"><figcaption>{tag(it["before"])} → {tag(it["after"])}<br><span class="muted">{e(it["split"])} · found by: {e(it["found_by"].replace("not_trained_429_v6", "429_v6"))}</span></figcaption></figure>'
    for it in lc_show)
labels = f"""
<h2>Label check: fixing the answer key</h2>
<p>Some crops carried the wrong label, so a model could be marked wrong for a right answer. Units were checked by eye when the models
disagreed with the label, or when they came from the annotation batch <code>not_trained_429_v6</code>, whose UPS and Empty labels were swapped.</p>
<div class="kpis">{kpi(n(sum(v["checked"] for v in sp.values())), "crops checked by eye")}{kpi(n(sum(v["wrong"] for v in sp.values())), "labels found wrong and fixed")}
{kpi(str(sp["test"]["removed"]), "test unit removed (unclear)")}</div>
<div class="tablewrap"><table class="detail"><thead><tr><th>Split</th><th>Units</th><th>Checked</th><th>Wrong label</th><th>Removed</th><th>Before occ / free</th><th>After occ / free</th></tr></thead><tbody>{lc_rows}</tbody></table></div>
<h3>Why the 429_v6 batch was suspect</h3>
<p class="muted">How often the ensemble agrees with the label, inside that batch and everywhere else.</p>
<div class="tablewrap"><table class="detail"><thead><tr><th>Hand label</th><th>Units in batch</th><th>Agree, in batch</th><th>Agree, elsewhere</th></tr></thead><tbody>{ba}</tbody></table></div>
<h3>Examples of fixed labels</h3>
<div class="shots">{lc_imgs}</div>
"""

mistakes = f"""
<h2>Every mistake and every uncertain unit</h2>
<p>All {F["decided_wrong"]} wrong answers and {F["uncertain"]} uncertain units from the {n(F["units"])} test units, with the {NW} models' scores.
Each wrong answer was looked at by eye: was the model wrong, or the label?</p>
<div class="filters" role="group" aria-label="Filter units">
<button type="button" class="chip" data-f="wrong" aria-pressed="true">Wrong answers</button>
<button type="button" class="chip" data-f="uncertain" aria-pressed="false">Uncertain</button>
<button type="button" class="chip" data-f="v-model" aria-pressed="false">Model wrong</button>
<button type="button" class="chip" data-f="v-label" aria-pressed="false">Label wrong</button>
<button type="button" class="chip" data-f="v-unclear" aria-pressed="false">Unclear</button>
<span id="mcount" class="muted"></span>
</div>
<div id="mgrid" class="mgrid"></div>
"""

TABS = [("overview", "Overview", pct(ens["acc"]), overview), ("approaches", "Every approach", str(len(APPROACHES) + 1), approaches),
        ("ensemble", "The ensemble", "6", ensemble), ("uncertain", "Uncertain units", pct(ens["uncertain"], 0), uncertain),
        ("photos", "Real photos", n(TS["overall"]["units"]), photos), ("labels", "Label check", str(sum(v["wrong"] for v in sp.values()))),
        ("mistakes", "Mistakes", str(F["decided_wrong"] + F["uncertain"]), mistakes)]
TABS[5] = TABS[5] + (labels,)

# ----------------------------------------------------------------------------- page
base_css = """
/* layout: sticky left tab rail + one reading column at 90 % of the viewport; cards carry the facts, strips carry the pictures */
:root { --bg:#f6f7f4; --panel:#ffffff; --fg:#1b221e; --muted:#5d675f; --line:#d9ded8; --soft:#eceee8;
  --accent:#1f6f5f; --accent-ink:#ffffff; --ok:#1f7a3f; --ok-bg:#dff2e4; --mid:#8a6d00; --mid-bg:#fbf1cc; --bad:#a83a28; --bad-bg:#f9e0da;
  --bar:#1f6f5f; --bar-model:#7a7f7c; --bar-hyb:#3d8fd1; --bar-fail:#b9b3a6; --target:#a83a28;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,Consolas,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#161a18; --panel:#1e2421; --fg:#e7ebe7; --muted:#a3aca5; --line:#303732; --soft:#232a26;
  --accent:#6fc3ae; --accent-ink:#0f1f1a; --ok:#7fd09c; --ok-bg:#1e3a29; --mid:#e3c76a; --mid-bg:#3d3418; --bad:#f09a86; --bad-bg:#432621;
  --bar:#6fc3ae; --bar-model:#8e9590; --bar-hyb:#6fb0e6; --bar-fail:#5a564e; --target:#f09a86; color-scheme:dark } }
:root[data-theme="dark"] { --bg:#161a18; --panel:#1e2421; --fg:#e7ebe7; --muted:#a3aca5; --line:#303732; --soft:#232a26;
  --accent:#6fc3ae; --accent-ink:#0f1f1a; --ok:#7fd09c; --ok-bg:#1e3a29; --mid:#e3c76a; --mid-bg:#3d3418; --bad:#f09a86; --bad-bg:#432621;
  --bar:#6fc3ae; --bar-model:#8e9590; --bar-hyb:#6fb0e6; --bar-fail:#5a564e; --target:#f09a86; color-scheme:dark }
body { background:var(--bg); color:var(--fg); font:16px/1.55 var(--sans); padding-inline:16px; padding-block:0 60px; }
.wrap { width:90%; margin:0 auto; } @media (max-width:800px) { .wrap { width:100%; } }
header { padding-block:22px 8px; } h1 { font-size:1.9rem; margin:0 0 4px; text-wrap:balance; }
.lead { margin:0; max-width:95ch; font-size:1.05rem; color:var(--muted); }
.layout { display:grid; grid-template-columns:17rem minmax(0,1fr); gap:30px; align-items:start; }
nav { position:sticky; top:env(safe-area-inset-top,0px); max-height:100vh; overflow-y:auto; padding-block:16px; display:flex; flex-direction:column; gap:2px; border-right:1px solid var(--line); padding-right:12px; }
nav .grp { font-size:.72rem; text-transform:uppercase; letter-spacing:.07em; color:var(--muted); margin:14px 0 4px 10px; }
nav button { display:flex; justify-content:space-between; gap:8px; width:100%; font:inherit; font-size:.94rem; text-align:left; padding:7px 10px; border:0; border-radius:6px; background:none; color:var(--fg); cursor:pointer; }
nav button:hover { background:var(--soft); } nav button[aria-selected="true"] { background:var(--soft); color:var(--accent); font-weight:600; }
nav button .sc { font-family:var(--mono); font-size:.8rem; color:var(--muted); font-weight:400; }
nav button:focus-visible, .toggle:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
@media (max-width:800px) { .layout { grid-template-columns:1fr; gap:0; } nav { flex-direction:row; flex-wrap:nowrap; overflow-x:auto; max-height:none; padding:8px 0; border-right:0; border-bottom:1px solid var(--line); } nav .grp { display:none; } nav button { white-space:nowrap; width:auto; } nav button .sc { display:none; } }
main { min-width:0; } section[role="tabpanel"] { padding-block:18px; }
h2 { font-size:1.45rem; margin:0 0 10px; text-wrap:balance; } h3 { font-size:1.12rem; margin:28px 0 8px; } h4 { font-size:1rem; margin:14px 0 4px; }
p, li { max-width:95ch; } ul { padding-left:22px; } .muted { color:var(--muted); } .stamp { color:var(--muted); font-size:.9rem; }
code { font-family:var(--mono); font-size:.86em; background:var(--soft); padding:0 4px; border-radius:4px; }
.kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:12px; margin:14px 0; }
.kpis div { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 14px; min-width:0; }
.kpis b { display:block; font-size:1.55rem; font-family:var(--mono); font-variant-numeric:tabular-nums; } .kpis span { color:var(--muted); font-size:.9rem; }
.kpis .hyb b { color:var(--bar-hyb); } .kpis .best b { color:var(--accent); }
.flow { display:flex; flex-wrap:wrap; align-items:stretch; gap:6px; margin:10px 0 6px; }
.flow .node { flex:1 1 150px; max-width:260px; background:var(--panel); border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:6px; padding:8px 10px; min-width:0; }
.flow .node b { display:block; font-size:.95rem; } .flow .node span { display:block; font-size:.8rem; color:var(--muted); line-height:1.35; margin-top:2px; }
.flow .arr { align-self:center; width:0; height:0; border:7px solid transparent; border-left:10px solid var(--muted); border-right:0; flex:0 0 auto; }
.flow.model .node { border-left-color:var(--bar-model); } .flow.hyb .node { border-left-color:var(--bar-hyb); }
.bars { display:grid; grid-template-columns:minmax(10rem,18rem) 1fr 4.6rem; gap:6px 12px; align-items:center; margin:10px 0; }
.bars .h { font-size:.8rem; color:var(--muted); } .bars .lab { text-align:right; font-size:.93rem; min-width:0; }
.bars .bar { position:relative; height:18px; background:var(--soft); border-radius:4px; overflow:hidden; } .bars .bar i { display:block; height:100%; background:var(--bar); }
.bars .model i { background:var(--bar-model); } .bars .hyb i { background:var(--bar-hyb); } .bars .fail i { background:var(--bar-fail); }
.bars .bar s { position:absolute; top:0; bottom:0; width:2px; background:var(--target); } .bars .v { font-family:var(--mono); font-size:.88rem; font-variant-numeric:tabular-nums; }
@media (max-width:600px) { .bars { grid-template-columns:7rem 1fr 3.6rem; } }
.shots { display:flex; gap:10px; overflow-x:auto; padding:4px 0 10px; scroll-snap-type:x proximity; }
.shot { flex:0 0 auto; width:min(260px,80vw); scroll-snap-align:start; margin:0; background:var(--panel); border:1px solid var(--line); border-radius:8px; overflow:hidden; }
.shot.wide { width:min(420px,88vw); } .shot img { display:block; width:100%; height:230px; object-fit:contain; background:#0d100e; cursor:zoom-in; }
.shot.wide img { height:300px; } .shot figcaption { padding:8px 10px 10px; font-size:.84rem; line-height:1.4; }
.badge { display:inline-block; font-weight:700; padding:0 7px; border-radius:6px; font-family:var(--mono); font-size:.82rem; }
.ok { color:var(--ok); background:var(--ok-bg); } .mid { color:var(--mid); background:var(--mid-bg); } .bad { color:var(--bad); background:var(--bad-bg); } .neutral { color:var(--muted); background:var(--soft); }
.card { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px 16px; margin:12px 0; min-width:0; }
.card h4 { margin:0 0 6px; font-size:1.05rem; } .card .head { display:flex; flex-wrap:wrap; gap:8px 12px; align-items:baseline; }
.facts { display:grid; grid-template-columns:8.5rem 1fr; gap:4px 12px; margin:8px 0 0; } .facts dt { color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.05em; padding-top:3px; }
.facts dd { margin:0; min-width:0; } @media (max-width:600px) { .facts { grid-template-columns:1fr; } .facts dt { padding-top:6px; } }
.tools { margin:8px 0 0; font-size:.86rem; color:var(--muted); } .tools b { color:var(--accent); font-size:.75rem; text-transform:uppercase; letter-spacing:.06em; margin-right:6px; }
.two { display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:14px; align-items:start; }
.step { display:grid; grid-template-columns:minmax(200px,300px) minmax(0,1fr); gap:16px; align-items:start; }
.step img { width:100%; height:auto; max-height:360px; object-fit:contain; background:#0d100e; border-radius:6px; cursor:zoom-in; display:block; }
@media (max-width:700px) { .step { grid-template-columns:1fr; } }
table.detail { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; font-size:.93rem; } .tablewrap { overflow-x:auto; }
table.detail th, table.detail td { text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
table.detail th { font-size:.8rem; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; } table.detail td.n { font-family:var(--mono); white-space:nowrap; }
.toggle { font:inherit; font-weight:600; background:var(--accent); color:var(--accent-ink); border:0; border-radius:8px; padding:10px 16px; cursor:pointer; margin:10px 0; }
.toggle:hover { filter:brightness(1.08); }
.limits { border:2px solid var(--accent); border-radius:10px; padding:4px 16px 14px; margin-top:10px; }
.note { background:var(--soft); border-radius:8px; padding:12px 14px; max-width:110ch; } .note p { margin:0 0 6px; }
.sw { display:inline-block; width:22px; height:12px; vertical-align:middle; margin-right:6px; border-radius:3px; }
svg.chart { width:100%; height:auto; display:block; background:var(--panel); border:1px solid var(--line); border-radius:8px; } svg.chart text { font-family:var(--sans); fill:var(--fg); }
svg.chart .grid { stroke:var(--line); } svg.chart .axis { fill:var(--muted); font-size:11px; } svg.chart .lbl { font-size:11px; fill:var(--muted); }
.lightbox { position:fixed; inset:0; background:rgba(0,0,0,.9); display:flex; align-items:center; justify-content:center; z-index:10; padding:16px; }
.lightbox img { max-width:96vw; max-height:88vh; object-fit:contain; } .lightbox button { position:absolute; top:12px; right:16px; font:inherit; font-size:1.2rem; background:#fff; color:#000; border:0; border-radius:6px; padding:4px 12px; cursor:pointer; }
a { color:var(--accent); }
.others { display:flex; flex-direction:column; gap:4px; padding:2px 10px 8px; font-size:.88rem; } .others a { text-decoration:none; } .others a:hover { text-decoration:underline; }
@media (max-width:800px) { .others { flex-direction:row; flex-wrap:wrap; gap:10px; } }
@media (prefers-reduced-motion: no-preference) { nav button, .toggle { transition:background .15s; } }
"""
extra_css = """
/* occupancy states use the app's colours: occupied red, unoccupied green, uncertain amber */
:root { --occ:#c0392b; --occ-bg:#fbe3df; --free:#1e8449; --free-bg:#dcf3e3; --unc:#b9770e; --unc-bg:#fcf0d6; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --occ:#f1948a; --occ-bg:#47231f; --free:#7dcea0; --free-bg:#1d3a28; --unc:#f5c26b; --unc-bg:#3f3218; } }
:root[data-theme="dark"] { --occ:#f1948a; --occ-bg:#47231f; --free:#7dcea0; --free-bg:#1d3a28; --unc:#f5c26b; --unc-bg:#3f3218; }
.tag { display:inline-block; font-size:.74rem; font-weight:700; text-transform:uppercase; letter-spacing:.05em; padding:1px 7px; border-radius:4px; }
.tag.occupied { color:var(--occ); background:var(--occ-bg); } .tag.unoccupied { color:var(--free); background:var(--free-bg); } .tag.uncertain { color:var(--unc); background:var(--unc-bg); }
.bars .best i { background:var(--accent); } .kpis .best b { color:var(--accent); }
.shot.small { width:min(200px,70vw); } .shot.small img { height:90px; cursor:zoom-in; }
.two .card h4 { font-size:1rem; } .two .card p { margin:0; font-size:.93rem; }
table.detail tr.now td { background:var(--soft); font-weight:600; }
.filters { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin:12px 0; }
.chip { font:inherit; font-size:.9rem; border:1px solid var(--line); background:var(--panel); color:var(--fg); border-radius:999px; padding:5px 14px; cursor:pointer; }
.chip[aria-pressed="true"] { background:var(--accent); color:var(--accent-ink); border-color:var(--accent); }
.chip:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.mgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:12px; }
.mcard { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:10px 12px; display:flex; flex-direction:column; gap:8px; min-width:0; }
.mcard .imgs { display:flex; gap:6px; } .mcard .imgs img { flex:1 1 0; min-width:0; height:120px; object-fit:contain; background:#0d100e; border-radius:4px; cursor:zoom-in; }
.mcard .head { display:flex; flex-wrap:wrap; gap:6px; align-items:center; font-size:.88rem; } .mcard p { margin:0; font-size:.86rem; }
.votes { width:100%; height:auto; display:block; } .votes text { font-family:var(--sans); font-size:10px; fill:var(--muted); }
.votes .zf { fill:var(--free-bg); } .votes .zu { fill:var(--soft); } .votes .zo { fill:var(--occ-bg); }
.votes .dot { fill:var(--muted); stroke:var(--panel); stroke-width:1.5; } .votes .avg { fill:var(--accent); stroke:var(--panel); stroke-width:1.5; }
.cases { display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:12px; }
"""

nav = "".join(f'<button role="tab" id="t-{k}" aria-controls="p-{k}" aria-selected="{"true" if i == 0 else "false"}" data-tab="{k}">{e(t)}<span class="sc">{e(sc)}</span></button>'
              for i, (k, t, sc, _) in enumerate(TABS))
panels = "".join(f'<section role="tabpanel" id="p-{k}" aria-labelledby="t-{k}"{"" if i == 0 else " hidden"}>{body}</section>'
                 for i, (k, _, _, body) in enumerate(TABS))
CASES = []
for want in (lambda u: u["outcome"] == "wrong" and u["verdict"] == "model",
             lambda u: u["outcome"] == "uncertain" and min(u["votes"].values()) < .2 and max(u["votes"].values()) > .8,
             lambda u: u["outcome"] == "uncertain" and u["leaning"] == u["truth"]):
    c = next((u["id"] for u in units if want(u) and u["id"] not in CASES), None)
    if c:
        CASES.append(c)

script = """
const U = __UNITS__, CASES = __CASES__, CUT = __CUT__;
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const tag = s => `<span class="tag ${s}">${s}</span>`;
function votes(u) {
  const names = Object.keys(u.votes), W = 420, L = 70, R = 12, row = 13, H = 14 + row * (names.length + 1) + 16;
  const x = v => L + v * (W - L - R), lo = 1 - CUT;
  let s = `<svg class="votes" viewBox="0 0 ${W} ${H}" role="img" aria-label="The model scores and their average">`;
  s += `<rect class="zf" x="${x(0)}" y="6" width="${x(lo) - x(0)}" height="${row * (names.length + 1) + 6}"/>`;
  s += `<rect class="zu" x="${x(lo)}" y="6" width="${x(CUT) - x(lo)}" height="${row * (names.length + 1) + 6}"/>`;
  s += `<rect class="zo" x="${x(CUT)}" y="6" width="${x(1) - x(CUT)}" height="${row * (names.length + 1) + 6}"/>`;
  names.forEach((nm, i) => { const y = 16 + i * row;
    s += `<text x="${L - 6}" y="${y + 3}" text-anchor="end">${esc(nm)}</text><circle class="dot" cx="${x(u.votes[nm])}" cy="${y}" r="4"><title>${esc(nm)}: ${u.votes[nm].toFixed(2)}</title></circle>`; });
  const y = 16 + names.length * row, a = x(u.p);
  s += `<text x="${L - 6}" y="${y + 3}" text-anchor="end" style="font-weight:700;fill:var(--fg)">Average</text><path class="avg" d="M${a} ${y - 6} L${a + 6} ${y} L${a} ${y + 6} L${a - 6} ${y} Z"><title>Average: ${u.p.toFixed(2)}</title></path>`;
  [[0, "0"], [lo, lo.toFixed(2)], [CUT, CUT.toFixed(2)], [1, "1"]].forEach(([v, t]) => s += `<text x="${x(v)}" y="${H - 3}" text-anchor="middle">${t}</text>`);
  return s + "</svg>";
}
function card(u) {
  const ans = u.outcome === "uncertain" ? `${tag("uncertain")} leans ${esc(u.leaning)}` : `answered ${tag(u.leaning)}`;
  const v = u.verdict ? `<span class="badge ${u.verdict === "model" ? "bad" : u.verdict === "label" ? "ok" : "neutral"}">${u.verdict === "model" ? "model wrong" : u.verdict === "label" ? "label wrong" : "unclear"}</span>` : "";
  return `<article class="mcard"><div class="head"><b>${esc(u.cls)}</b> truth ${tag(u.truth)} · ${ans} ${v}${u.side ? ' <span class="badge neutral">side angle</span>' : ""}</div>
    <div class="imgs"><img src="${u.crop}" alt="What the model saw" loading="lazy">${u.ctx ? `<img src="${u.ctx}" alt="The unit in its photo" loading="lazy">` : ""}</div>
    ${votes(u)}${u.reason ? `<p>${esc(u.reason)}</p>` : ""}</article>`;
}
document.getElementById("ens-cases").innerHTML = CASES.map(id => card(U.find(u => u.id === id))).join("");
const grid = document.getElementById("mgrid"), count = document.getElementById("mcount");
const chips = [...document.querySelectorAll(".chip")];
function show(f) {
  chips.forEach(c => c.setAttribute("aria-pressed", String(c.dataset.f === f)));
  const pick = U.filter(u => f.startsWith("v-") ? u.verdict === f.slice(2) : u.outcome === f);
  grid.innerHTML = pick.map(card).join(""); count.textContent = `${pick.length} units`;
}
chips.forEach(c => c.addEventListener("click", () => show(c.dataset.f)));
show("wrong");
const tabs = [...document.querySelectorAll('[role="tab"]')];
function open(k, push) {
  tabs.forEach(t => t.setAttribute("aria-selected", String(t.dataset.tab === k)));
  document.querySelectorAll('[role="tabpanel"]').forEach(p => p.hidden = p.id !== "p-" + k);
  if (push) { history.replaceState(null, "", "#" + k); window.scrollTo(0, 0); }
}
tabs.forEach(t => t.addEventListener("click", () => open(t.dataset.tab, true)));
const h = location.hash.slice(1); if (tabs.some(t => t.dataset.tab === h)) open(h, false);
document.addEventListener("click", ev => {
  const img = ev.target.closest("img"); if (!img || img.closest(".lightbox")) return;
  const box = document.createElement("div"); box.className = "lightbox";
  box.innerHTML = `<img src="${img.src}" alt="${esc(img.alt)}"><button type="button">Close</button>`;
  box.addEventListener("click", () => box.remove()); document.body.append(box);
});
document.addEventListener("keydown", ev => { if (ev.key === "Escape") document.querySelector(".lightbox")?.remove(); });
""".replace("__UNITS__", json.dumps(units)).replace("__CASES__", json.dumps(CASES)).replace("__CUT__", str(CUT))

page = f"""<title>Rack Occupancy</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;600;700&display=swap">
<style>{base_css}{extra_css}</style>
<div class="wrap">
<header><h1>Rack Occupancy</h1>
<p class="lead">Step 2 of the rack pipeline: is each unit occupied, unoccupied or uncertain? v2 dataset after the label fixes · {n(F["units"])} test units · built 12 October 2026.</p></header>
<div class="layout"><nav role="tablist" aria-label="Sections" aria-orientation="vertical">{nav}</nav><main>{panels}</main></div>
</div>
<script>{script}</script>
"""
(OUT / "index.html").write_text(page)
print("wrote", OUT / "index.html", f"{len(page) / 1e3:.0f} kB,", len(list(IMG.iterdir())), "pictures | cases", CASES)
for k in order:
    print(f"{APPROACHES[k][0]:32s} test {pct(M[k]['test']['acc'])}  unc {pct(M[k]['test']['uncertain'])}  sure {pct(M[k]['test']['acc_sure'])}")
