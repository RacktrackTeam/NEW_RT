"""Final device classification report: one test dataset, Model 1, Model 2 and Model 1 + 2, without and with Other.

The dataset: every labelled device in the test photos neither model trained on (the "all" set of test_sets.py).
Reads results/devices/test_rows.csv and test_sets.json, models/device_cls/agreement.json, the Model 1 cut-offs and raw scores
(other_cutoffs.json, test_predictions.csv) and the device crops.
Three tabs: Results (the three models), What the number means (where the headline comes from, how it moves, the limits)
and When it goes to Other (every condition the app applies, with examples).

    python scripts/devices/build_final_report.py [OUT.html]      (default reports/devices/final_report/index.html)
"""
import base64
import csv
import io
import json
import pathlib
import math
import sys
from collections import Counter, defaultdict

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
CROPS = ROOT / "data" / "device_crops"
RES = ROOT / "results" / "devices"
SRC = [("m1", "Model 1", "ConvNeXt-Tiny: names the cut-out device"),
       ("m2", "Model 2", "DINOv2-B: finds and names devices in the whole photo"),
       ("m12", "Model 1 + 2", "answers only when both name the same type")]
C = {"r": "#2f7d4a", "o": "#d9a441", "or": "#e7c98f", "ow": "#c9852d", "w": "#b23b3b"}


def pct(v, d=1):
    return f"{100 * v:.{d}f} %"


def n(v):
    return f"{v:,}"


def thumb(crop, h=60):
    im = Image.open(CROPS / crop).convert("RGB"); im.thumbnail((h * 6, h * 2))
    b = io.BytesIO(); im.save(b, "JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def bar(parts, total):
    segs = "".join(f'<i style="flex:{v};background:{c}" title="{l}: {n(v)}">{n(v) if v / total > .08 else ""}</i>' for v, c, l in parts if v)
    return f'<div class="sb">{segs}</div>'


def main(out=None):
    T = json.load(open(RES / "test_sets.json")); S = T["sets"]["all"]
    rows = list(csv.DictReader(open(RES / "test_rows.csv")))
    ag = json.load(open(ROOT / "models" / "device_cls" / "agreement.json")); cut2 = ag["model2_cut"]
    tl = S["tally"]; N = S["n"]
    types = sorted({r["class"] for r in rows}, key=lambda c: -sum(r["class"] == c for r in rows))

    def per_type(k, other):
        out = {}
        for c in types:
            R = [r for r in rows if r["class"] == c]
            if other:
                ans = [r for r in R if r[f"{k}_other"] == "0"]
                out[c] = (sum(r[k] == c for r in ans), len(ans), len(R) - len(ans))
            else:
                out[c] = (sum(r[k] == c for r in R), len(R), 0)
        return out

    m1, m2, m12 = tl["m1"], tl["m2"], tl["m12"]
    rule = {"m1": "Says Other when it is unsure, when its top two scores are close, or when the device looks unlike anything it learned.",
            "m2": f"Says Other when it finds no box on the device, or its box score is below {cut2:.2f}.",
            "m12": "Says Other when the two models name different types."}

    WHY = {"rack": ("Repeats in one rack", "the same mistake on 2 or more devices in one photo, usually one device model that looks like another type"),
           "tilted": ("Tilted or partly hidden", "less than half of the device's front shows"),
           "blurry": ("Blurry or dark", "too little detail in the photo"),
           "alike": ("Looks like the other type", "a clear picture, but the device looks like the type it was called")}

    def wrong_rows(k):
        return [r for r in rows if r[f"{k}_other"] == "0" and r[k] != r["class"]]

    def why_of(k):
        W_ = wrong_rows(k); pp = Counter(r["photo"] for r in W_)
        def why(r):
            if pp[r["photo"]] >= 2:
                return "rack"
            if float(r["fill"]) < .5:
                return "tilted"
            if float(r["sharpness"]) < 450 or float(r["brightness"]) < 50:
                return "blurry"
            return "alike"
        return {r["crop"]: why(r) for r in W_}

    def shares(vals, total):
        """Percent of all devices for each count, rounded to one decimal so that they add up to exactly 100.0."""
        raw = [1000 * v / total for v in vals]; base = [int(x) for x in raw]
        for i_ in sorted(range(len(raw)), key=lambda i_: base[i_] - raw[i_])[:1000 - sum(base)]:
            base[i_] += 1
        return [f"{b / 10:.1f} %" for b in base]

    def card(k, name, sub):
        x = tl[k]
        if k == "m12":
            before = '<div class="blk"><div class="lbl">Without Other</div><p class="na">Not possible: when the two models disagree, it has no answer.</p></div>'
        else:
            pr, pw = shares([x["all_right"], x["all_wrong"]], x["n"])
            before = (f'<div class="blk"><div class="lbl">Without Other</div><div class="big">{pr}<small>right</small></div>'
                      f'{bar([(x["all_right"], C["r"], "right"), (x["all_wrong"], C["w"], "wrong")], x["n"])}'
                      f'<div class="parts"><div><i style="background:{C["r"]}"></i><em><b>{n(x["all_right"])}</b> right</em><span>{pr}</span></div>'
                      f'<div><i style="background:{C["w"]}"></i><em><b>{n(x["all_wrong"])}</b> wrong</em><span>{pw}</span></div>'
                      f'<div class="tot"><i></i><em><b>{n(x["n"])}</b> devices</em><span>100 %</span></div></div></div>')
        pr, por, pow_, pw = shares([x["right"], x["other_right"], x["other_wrong"], x["wrong"]], x["n"])
        po = shares([x["right"], x["other"], x["wrong"]], x["n"])[1]
        after = (f'<div class="blk"><div class="lbl">With Other</div><div class="big">{pct(x["answers_right"])}<small>of answers right</small></div>'
                 f'<p class="how">{n(x["right"])} right of the {n(x["right"] + x["wrong"])} devices it answered; the other {n(x["other"])} went to Other.</p>'
                 f'{bar([(x["right"], C["r"], "answered right"), (x["other_right"], C["or"], "Other, would have been right"), (x["other_wrong"], C["ow"], "Other, would have been wrong"), (x["wrong"], C["w"], "answered wrong")], x["n"])}'
                 f'<div class="parts"><div><i style="background:{C["r"]}"></i><em><b>{n(x["right"])}</b> answered right</em><span>{pr}</span></div>'
                 f'<div><i style="background:{C["o"]}"></i><em><b>{n(x["other"])}</b> Other, a person checks</em><span>{po}</span></div>'
                 f'<div class="in"><i style="background:{C["or"]}"></i><em>{n(x["other_right"])} would have been right</em><span>{por}</span></div>'
                 f'<div class="in"><i style="background:{C["ow"]}"></i><em>{n(x["other_wrong"])} would have been wrong</em><span>{pow_}</span></div>'
                 f'<div><i style="background:{C["w"]}"></i><em><b>{n(x["wrong"])}</b> answered wrong</em><span>{pw}</span></div>'
                 f'<div class="tot"><i></i><em><b>{n(x["n"])}</b> devices</em><span>100 %</span></div></div>'
                 f'<p class="rule">{rule[k]}</p></div>')
        wy = Counter(why_of(k).values())
        why_html = (f'<div class="blk"><div class="lbl">Why the {n(x["wrong"])} are wrong</div><div class="parts">'
                    + "".join(f'<div><i style="background:{C["w"]};opacity:{.35 + .65 * c / max(wy.values())}"></i><em><b>{n(c)}</b> {WHY[w][0]}<small>{WHY[w][1]}</small></em><span>{pct(c / x["wrong"], 0)}</span></div>'
                              for w, c in wy.most_common()) + '</div></div>') if x["wrong"] else ""
        after += why_html
        return f'<div class="mc{" best" if k == "m12" else ""}"><h3>{name}</h3><p class="sub">{sub}</p>{before}{after}</div>'
    cards = f'<div class="g3">{"".join(card(*x) for x in SRC)}</div>'
    W = wrong_rows("m12"); W12 = why_of("m12")
    W = sorted(W, key=lambda r: (list(WHY).index(W12[r["crop"]]), r["photo"]))
    man = {r["crop"]: r for r in csv.DictReader(open(CROPS / "manifest.csv")) if r["crop"]}
    by_photo = Counter(r["photo"] for r in W)

    def issue(r):
        w = W12[r["crop"]]
        if w == "rack":
            same = [q for q in W if q["photo"] == r["photo"]]
            kinds = Counter(q["class"] for q in same)
            what = " and ".join(f"{k} {c}{'s' if k > 1 else ''}" for c, k in kinds.items())
            return f"{what} in this one photo were all called {r['m12']}. The rack holds several devices of one model that looks like a {r['m12']}, so the same mistake repeats."
        if w == "tilted":
            return f"The device covers only {pct(float(r['fill']), 0)} of its outline (a front-on device covers 80 % or more): it is seen from the side or partly hidden, so most of its front is missing."
        if w == "blurry":
            sh, br = float(r["sharpness"]), float(r["brightness"])
            parts = []
            if sh < 450:
                parts.append(f"sharpness {sh:.0f} (a clear crop is 450 or more)")
            if br < 50:
                parts.append(f"brightness {br:.0f} of 255 (dark below 50)")
            return f"Too little detail: {', '.join(parts)}. The ports, labels and logos that tell the types apart are not readable."
        return (f"A clear crop, but both models are sure it is a {r['m12']} (Model 1 {float(r['m1_prob']):.0%}, Model 2 {float(r['m2_score']):.2f}): "
                f"this {r['class']} looks like the {r['m12']} devices they learned from. Possibly a label to check.")

    def small(cw, ch, iw, ih, sc):
        bits = []
        if ch < 32:
            bits.append(f"the crop is only {ch} px tall, so Model 1 has to enlarge it {sc:.1f}× and the details are lost")
        if iw * ih < 300_000:
            bits.append(f"the whole photo is only {iw} × {ih} px ({iw * ih / 1e6:.1f} MP)")
        return f" <b>Low resolution:</b> {'; '.join(bits)}." if bits else ""

    def card16(r):
        m = man[r["crop"]]; cw, ch = int(m["crop_w"]), int(m["crop_h"]); iw, ih = int(m["image_w"]), int(m["image_h"])
        sc = min(512 / cw, 128 / ch); bw, bh = round(cw * sc), round(ch * sc)
        k2 = min(1.0, 518 / max(iw, ih))
        return (f'<div class="w16"><img src="{thumb(r["crop"], 80)}" alt="{r["class"]}"><div class="w16b">'
                f'<div class="w16h"><b>Really {r["class"]}</b><span>both said {r["m12"]}</span><span class="why">{WHY[W12[r["crop"]]][0]}</span></div>'
                f'<p class="iss"><b>Why wrong:</b> {issue(r)}{small(cw, ch, iw, ih, sc)}</p>'
                f'<dl><dt>Crop</dt><dd>{n(cw)} × {n(ch)} px</dd>'
                f'<dt>Photo</dt><dd>{n(iw)} × {n(ih)} px · {iw * ih / 1e6:.1f} MP</dd>'
                f'<dt>Model 1 sees</dt><dd>{bw} × {bh} px in its 512 × 128 box ({"enlarged" if sc > 1 else "shrunk"} {sc:.2f}×)</dd>'
                f'<dt>Model 2 sees</dt><dd>{round(cw * k2)} × {round(ch * k2)} px (photo at a long side of 518 px)</dd>'
                f'<dt>Scores</dt><dd>Model 1 {float(r["m1_prob"]):.0%} · Model 2 {float(r["m2_score"]):.2f}</dd></dl></div></div>')
    figs = "".join(card16(r) for r in W)
    left = f"""<section class="sec"><h2>The {n(len(W))} devices still wrong with Model 1 + 2</h2>
<p class="sub">Both models named the same wrong type, so Other cannot catch them. For each: what it really is, what both said, why it went wrong, and its size at every step. These labels are the first to check.</p><div class="w16s">{figs}</div></section>"""
    # ---------- tab 2: what the headline number means (every number from test_rows.csv)
    def tally(R):
        a = [r for r in R if r["m12_other"] == "0"]; ok = sum(r["m12"] == r["class"] for r in a)
        return len(R), len(a), ok, len(R) - len(a)

    def wilson(k, m, z=1.96):
        if not m:
            return 0.0, 0.0
        p = k / m; d = 1 + z * z / m; c = (p + z * z / (2 * m)) / d; h = z * math.sqrt(p * (1 - p) / m + z * z / (4 * m * m)) / d
        return c - h, c + h

    def look_(r):
        if float(r["fill"]) < .5:
            return "Tilted or partly hidden"
        return "Blurry or dark" if float(r["sharpness"]) < 450 or float(r["brightness"]) < 50 else "Sharp and front-on"
    mp_bands = [("under 0.5 MP", 0, .5), ("0.5 to 1 MP", .5, 1), ("1 to 3 MP", 1, 3), ("3 MP or more", 3, 1e9)]
    groups = [("By device type", [(c, [r for r in rows if r["class"] == c]) for c in types]),
              ("By how the device shows", [(k, [r for r in rows if look_(r) == k]) for k in ("Sharp and front-on", "Blurry or dark", "Tilted or partly hidden")]),
              ("By photo size", [(k, [r for r in rows if lo <= float(r["photo_mp"]) < hi]) for k, lo, hi in mp_bands])]
    LO = 0.80                                                      # bars start at 80 %

    def grow(name, R):
        nn, a, ok, oth = tally(R); v = ok / a if a else 0
        w = max(0.0, (v - LO) / (1 - LO)) * 100
        cl = "lowv" if v < .95 else ""
        return (f'<div class="gr"><span class="gn">{name}<small>{n(nn)} devices</small></span>'
                f'<span class="gb"><i class="{cl}" style="width:{w:.1f}%"></i></span><b class="{cl}">{pct(v)}</b><span class="go">{pct(oth / nn, 0)}</span></div>')
    gcards = "".join(f'<div class="gc"><h3>{g}</h3><div class="gh"><span></span><span>answers right</span><span>Other</span></div>{"".join(grow(k, R) for k, R in G)}</div>' for g, G in groups)
    N_, A_, OK_, OT_ = tally(rows); lo_, hi_ = wilson(OK_, A_)
    hi_rows = [r for r in rows if r["hi"] == "1"]; hN, hA, hOK, hOT = tally(hi_rows)
    oreason = Counter(r["m12_reason"] for r in rows if r["m12_other"] == "1")
    n_dis, n_nobox = oreason.get("the two models disagree", 0), oreason.get("no box from Model 2", 0)
    per_photo = defaultdict(list)
    for r in rows:
        per_photo[r["photo"]].append(r)
    pv = [ok / a for _, a, ok, _ in (tally(R) for R in per_photo.values()) if a]
    n_rack = sum(1 for v in W12.values() if v == "rack")
    fw = [r for r in rows if r["class"] == "Firewall"]; fN, fA, fOK, _ = tally(fw); flo, fhi = wilson(fOK, fA)
    weak = sorted(((c, *tally([r for r in rows if r["class"] == c])) for c in types), key=lambda t: t[3] / max(1, t[2]))[:3]
    weak_txt = ", ".join(f"{c} {pct(ok / a)} with {pct(o / nn, 0)} sent to Other" for c, nn, a, ok, o in weak)
    tilt = [r for r in rows if look_(r) == "Tilted or partly hidden"]; tN, tA, tOK, tOT = tally(tilt)
    limits = [
        ("It is a share of answers, not of all devices",
         f"{n(OT_)} of the {n(N_)} devices ({pct(OT_ / N_)}) go to Other and need a person. Counted over all devices, {pct(OK_ / N_)} are named right with no person."),
        ("It is measured on a sample, so it is a range",
         f"With {n(A_)} answers, the true value is most likely between {pct(lo_)} and {pct(hi_)}. Small groups have wide ranges: Firewall has {n(fN)} test devices, so its value lies between {pct(flo)} and {pct(fhi)}."),
        ("Some device types are weaker",
         f"The three lowest: {weak_txt}. Server and Storage unit look alike, and so do Router and Switch."),
        ("Tilted devices are weaker",
         f"Devices seen from the side or partly hidden: {pct(tOK / tA)} of answers right, {pct(tOT / tN, 0)} sent to Other. Blur and darkness make little difference."),
        ("Mistakes come in groups",
         f"{n(n_rack)} of the {n(len(W))} wrong answers repeat in one rack: several devices of one model that looks like another type. A rack full of such devices scores lower than the average."),
        ("One test set, from the same seven exports",
         f"The {n(S['photos'])} test photos come from the same seven exports as the training photos. A new site, a new camera or video frames are not measured here and may score lower."),
        ("Nine device types only",
         "Load Balancer is not learned (too few labels). The test set holds the nine types only, so how well Other catches a device of another type is not measured here."),
        ("The labels are not perfect",
         "The score is checked against the ground truth labels, and the dataset review found wrong and unclear labels in the same exports. A few answers counted wrong may be label mistakes, and the other way round."),
        ("Model 1 is tested on outlines drawn by people",
         f"Model 1 names a device cut out along its labelled outline. In the app the outline comes from device detection, so a missed or badly cut device adds mistakes not counted here. Model 2 finds devices itself; it found no box on {n(n_nobox)} test devices."),
    ]
    lim_html = "".join(f'<div class="lim"><b>{t}</b><p>{x}</p></div>' for t, x in limits)
    tab2 = f"""<h1>{pct(OK_ / A_)} is measured on one test set. It is not fixed for every photo</h1>
<p class="lead">How the headline number of Model 1 + 2 is worked out, how it changes with the device and the photo, and what it does not cover.</p>
<div class="kp"><div><b>{pct(OK_ / A_)}</b><span>of answers right, all test photos</span></div><div><b>{pct(lo_)} to {pct(hi_)}</b><span>likely range (95 % confidence)</span></div>
<div><b>{pct(hOK / hA)}</b><span>of answers right, photos of 1 MP or more</span></div><div><b>{pct(OK_ / N_)}</b><span>of all devices right with no person</span></div></div>
<section class="sec"><h2>Where {pct(OK_ / A_)} comes from</h2>
<div class="flow"><div class="fb"><b>{n(N_)}</b><span>devices in {n(S['photos'])} test photos that neither model trained on</span></div><div class="fa">&rarr;</div>
<div class="fb"><b>Both models</b><span>each name every device</span></div><div class="fa">&rarr;</div>
<div class="fcol"><div class="fb fg"><b>{n(A_)}</b><span>both name the same type: answered</span><small>{n(OK_)} right &middot; {n(A_ - OK_)} wrong</small></div>
<div class="fb fo"><b>{n(OT_)}</b><span>Other: a person checks</span><small>{n(n_dis)} the models disagree &middot; {n(n_nobox)} Model 2 found no box</small></div></div></div>
<p class="calc"><b>{n(OK_)} right &divide; {n(A_)} answered = {pct(OK_ / A_)}</b>. Other is left out because no answer is given.
Over all {n(N_)} devices: {n(OK_)} &divide; {n(N_)} = {pct(OK_ / N_)} right with no person.</p></section>
<section class="sec"><h2>It changes with the device and the photo</h2>
<p class="sub">The same rule on parts of the test set. Bars start at 80 %; red is under 95 %.
Per photo: in {sum(v == 1 for v in pv)} of the {len(pv)} photos with an answer, every answer was right; in {sum(v < .9 for v in pv)} photos fewer than 9 in 10 were right (lowest {pct(min(pv), 0)}).</p>
<div class="g3">{gcards}</div></section>
<section class="sec"><h2>Limits</h2><div class="lims">{lim_html}</div></section>"""
    # ---------- tab 3: when a device goes to Other. The rule the app applies: device_classifier._decide, then rack_video.second_check
    cuts = json.load(open(ROOT / "models" / "device_cls" / "convnext_tiny" / "other_cutoffs.json"))
    RAW = {r["crop"]: r for r in csv.DictReader(open(ROOT / "models" / "device_cls" / "convnext_tiny" / "test_predictions.csv"))}
    m2_says = lambda r: r["m2"] != "none" and float(r["m2_score"]) >= cut2 and r["m2"] != r["m1"]    # Model 2 disagrees, with a score that counts
    CHECK = {"unsure": lambda r: "unsure" in r["m1_reason"], "unlike": lambda r: "unlike" in r["m1_reason"], "close": lambda r: "close call" in r["m1_reason"],
             "disagree": m2_says}

    def fired(k):
        R = [r for r in rows if CHECK[k](r)]; ok = sum(r["m1"] == r["class"] for r in R)
        if k == "disagree":
            alone = sum(r["m1_other"] == "0" for r in R)
        else:
            alone = sum(1 for r in R if sum(x in r["m1_reason"] for x in ("unsure", "unlike", "close call")) == 1)
        return len(R), ok, len(R) - ok, alone
    O3 = [r for r in rows if r["m1_other"] == "1" or m2_says(r)]; A3 = [r for r in rows if r["m1_other"] == "0" and not m2_says(r)]
    ok3 = sum(r["m1"] == r["class"] for r in A3); o_ok = sum(r["m1"] == r["class"] for r in O3)
    both_same = sum(r["m1_other"] == "1" and r["m2"] == r["m1"] and float(r["m2_score"]) >= cut2 for r in rows)
    no_box = sum(r["m2"] == "none" and r["m1_other"] == "0" for r in rows); low_diff = sum(r["m2"] != "none" and float(r["m2_score"]) < cut2 and r["m2"] != r["m1"] and r["m1_other"] == "0" for r in rows)
    by_crop = {r["crop"]: r for r in rows}

    def ex(crop, lines):
        r = by_crop[crop]; right = r["m1"] == r["class"]
        chip = f'<span class="vd {"vh" if right else "vs"}">{"Held back a right answer" if right else "Stopped a wrong answer"}</span>'
        return (f'<figure class="ex"><img src="{thumb(crop, 70)}" alt="{r["class"]}"><figcaption><b>Really {r["class"]}</b>{chip}'
                + "".join(f"<span>{x}</span>" for x in lines(r, RAW[crop])) + "</figcaption></figure>")
    pp_ = lambda v: f"{100 * float(v):.0f} %"
    m1_line = lambda r, p: f'Model 1: {r["m1"]} {pp_(r["m1_prob"])}, next {p["second"]} {pp_(p["second_prob"])}'
    m2_line = lambda r: "Model 2: no box" if r["m2"] == "none" else f'Model 2: {r["m2"]}, score {float(r["m2_score"]):.2f}'
    strength = lambda p: -float(p["energy"]); gap_pts = lambda p: 100 * float(p["gap"])
    s_lim, d_lim, g_lim = -cuts["energy"], cuts["distance"], 100 * cuts["margin"]
    picks = {"unsure": ["Router/test__Device_Annotation_Not_traned_667_v2_test_1463__2.jpg", "Firewall/test__Device_annotation_Trained_2511_V1_test_3755__8.jpg"],
             "unlike": ["Router/test__Device_annotation_Trained_2511_V1_test_3904__13.jpg", "Router/test__455_V3__14_rack_duckduckgo_jpg.rf.361d1ffe1539ea776f9f3fe4badbb05a__31.jpg"],
             "close": ["Switch/test__120_jpg.rf.V1PrGiZnRZKPenBbnGQB__2.jpg", "Server/test__Device_annotation_Trained_2511_V1_test_3755__5.jpg"],
             "disagree": ["Switch/test__Device_Annotation_Not_traned_667_v2_test_1443__6.jpg", "Switch/test__Device_annotation_Trained_2511_V1_test_3755__7.jpg"]}
    L_ = {"unsure": lambda r, p: [m1_line(r, p), f"Strength {strength(p):.2f}, limit {s_lim:.2f}: too weak"],
          "unlike": lambda r, p: [m1_line(r, p), f"Distance {float(p['distance']):.2f}, limit {d_lim:.2f}: too far"],
          "close": lambda r, p: [m1_line(r, p), f"Gap {gap_pts(p):.0f} points, limit {g_lim:.0f}: too close", m2_line(r)],
          "disagree": lambda r, p: [m1_line(r, p), m2_line(r) + f", limit {cut2:.2f}", "The two names differ"]}
    CONDS = [("unsure", "Model 1 is unsure", f"Its overall score strength is below <b>{s_lim:.2f}</b>. No device type scores strongly."),
             ("unlike", "The device looks unlike the training devices", f"Its distance to the 5th closest training device is above <b>{d_lim:.2f}</b>. Model 1 has seen nothing close to it."),
             ("close", "Close call between two types", f"Its top two types are less than <b>{g_lim:.0f} points</b> apart. For example 60 % against 30 % is 30 points apart."),
             ("disagree", "Model 2 names a different type", f"Model 2 names another type with a score of <b>{cut2:.2f}</b> or more. A lower score does not count.")]

    def cond_card(i, c):
        k, name, rule_ = c; nn, ok, bad, alone = fired(k)
        extra = f"{n(alone)} only by this check" if k != "disagree" else f"{n(alone)} that no other check caught"
        return (f'<article class="cc"><div class="cch"><span class="num">{i}</span><div><h3>{name}</h3><p>{rule_}</p></div>'
                f'<div class="cst"><b>{n(nn)}</b><span>test devices</span></div></div>'
                f'<p class="csub">{n(ok)} would have been right, {n(bad)} wrong &middot; {extra}</p>'
                f'<div class="exs">{"".join(ex(x, L_[k]) for x in picks[k])}</div></article>')
    cond_html = "".join(cond_card(i + 1, c) for i, c in enumerate(CONDS))
    stay = [("Server/test__Device_annotation_not_trained_429_v6_test_4488__2.jpg", "Both models name the same type", "All checks pass, so the answer is given.", lambda r, p: [m1_line(r, p), m2_line(r)]),
            ("Storage_unit/test__Device_annotation_Trained_2511_V1_test_3785__0.jpg", "Model 2 finds no box", f"Finding nothing is no evidence, so Model 1's answer stands. {n(no_box)} test devices.", lambda r, p: [m1_line(r, p), m2_line(r)]),
            ("Switch/test__97_jpeg.rf.zgWefUzWSjO73ZHqd67e__1.jpg", f"Model 2 disagrees with a score under {cut2:.2f}", f"Too weak to count, so Model 1's answer stands. {n(low_diff)} test devices.", lambda r, p: [m1_line(r, p), m2_line(r)])]
    stay_html = "".join(f'<figure class="ex st"><img src="{thumb(c_, 70)}" alt="{by_crop[c_]["class"]}"><figcaption><b>{t_}</b><em>{w_}</em>'
                        + "".join(f"<span>{x}</span>" for x in l_(by_crop[c_], RAW[c_])) + "</figcaption></figure>" for c_, t_, w_, l_ in stay)
    tab3 = f"""<h1>A device goes to Other when any one of four checks fires</h1>
<p class="lead">Other means "not sure, a person checks this one". In the rack reports it reads Not sure. The counts are from the {n(N)} test devices.</p>
<div class="kp"><div><b>{n(len(O3))}</b><span>of {n(N)} test devices go to Other ({pct(len(O3) / N)})</span></div><div><b>{n(o_ok)} / {n(len(O3) - o_ok)}</b><span>of those would have been right / wrong</span></div>
<div><b>{n(len(A3))}</b><span>get an answer: {n(ok3)} right, {n(len(A3) - ok3)} wrong</span></div><div><b>{pct(ok3 / len(A3))}</b><span>of answers right with these checks</span></div></div>
<section class="sec"><h2>The order of the checks</h2>
<div class="flow fl3"><div class="fb"><b>Model 1</b><span>names the device</span></div><div class="fa">&rarr;</div>
<div class="fb fc"><small>Check 1</small><span>Unsure?</span></div><div class="fa">&rarr;</div><div class="fb fc"><small>Check 2</small><span>Unlike training?</span></div><div class="fa">&rarr;</div>
<div class="fb fc"><small>Check 3</small><span>Close call?</span></div><div class="fa">&rarr;</div><div class="fb"><b>Model 2</b><span>looks at the same device</span></div><div class="fa">&rarr;</div>
<div class="fb fc"><small>Check 4</small><span>Different type?</span></div><div class="fa">&rarr;</div><div class="fb fg"><b>Answer</b><span>all four said no</span></div></div>
<p class="calc">Any check that says yes sends the device to <b>Other</b>. Model 1's checks are not cancelled by Model 2: in {n(both_same)} test devices both models named the same type and the device still went to Other.</p></section>
<section class="sec"><h2>The four checks, with examples</h2><div class="ccs">{cond_html}</div></section>
<section class="sec"><h2>What does not send a device to Other</h2><div class="exs ex3">{stay_html}</div>
<ul class="nl"><li><b>A person's choice:</b> when a person sets the type, that type is used.</li>
<li><b>A settled device:</b> if a vendor layout was fitted or the panel reader found its ports, check 4 does not apply.</li></ul></section>
<section class="sec"><h2>Also shown as Not sure, without a check firing</h2>
<ul class="nl"><li><b>No frame shows the whole device:</b> Model 1 has nothing to look at, so there is no type.</li>
<li><b>Every unit of the device is uncertain and Model 1 is under 70 %:</b> the type is hidden.</li></ul></section>
<section class="sec"><h2>Good to know</h2>
<ul class="nl"><li><b>A device of another type has no check of its own.</b> It reaches Other only if one of these checks fires: on our own racks, 9 of 13 such devices did.</li>
<li><b>Load Balancer</b> is not learned by Model 1, so the same applies to it.</li>
<li><b>Videos:</b> checks 1 to 3 run once on the best 5 frames put together. Model 2 looks at the best frame.</li>
<li><b>The test:</b> these counts come from the stored test answers, with Model 1 on one cut-out and Model 2 on the whole photo. Real racks can differ.</li>
<li><b>The Results tab</b> uses a simpler rule: Other whenever the two names differ, with no other check. That gives {n(OT_)} Other and {pct(OK_ / A_)}. The checks on this tab are what the app applies.</li></ul></section>"""
    limits_extra = (f'<div class="lim"><b>The app uses a stricter rule</b><p>{pct(OK_ / A_)} uses one rule: Other whenever the two names differ. The app also applies Model 1\'s own checks. '
                    f'On the same test devices that gives {n(len(O3))} Other and {pct(ok3 / len(A3))} of answers right. See the next tab.</p></div>')
    tab2 = tab2.replace(lim_html, lim_html + limits_extra)
    out = out or ROOT / "reports" / "devices" / "final_report" / "index.html"
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    page = f"""<title>Device Classification Final Report</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,600&family=Source+Sans+3:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{color-scheme:light;--page:#fff;--card:#fff;--inset:#f4f6f7;--line:#e2e6e9;--ink:#1b2126;--ink2:#47525b;--muted:#6d7881;--accent:#1f5a85;--soft:#e8f0f6;
--title:"Newsreader",Georgia,serif;--body:"Source Sans 3",-apple-system,"Segoe UI",sans-serif;--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;--shadow:0 1px 2px rgba(20,30,40,.04),0 2px 10px rgba(20,30,40,.04)}}
*{{box-sizing:border-box}}body{{background:var(--page);color:var(--ink);font-family:var(--body);font-size:14.5px;line-height:1.5;margin:0}}
.wrap{{width:90%;margin:0 auto;padding-block:0 48px}}@media (max-width:800px){{.wrap{{width:100%;padding-left:16px;padding-right:16px}}}}
.top{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center;padding-block:14px;border-bottom:1px solid var(--line)}}.top .m{{color:var(--muted);margin-left:8px}}
.chips{{display:flex;gap:6px;flex-wrap:wrap}}.chip{{font-family:var(--mono);font-size:11.5px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--ink2);white-space:nowrap;background:var(--card)}}.chip b{{color:var(--ink)}}
h1{{font-family:var(--title);font-weight:600;font-size:28px;line-height:1.2;margin:26px 0 6px;max-width:46ch;text-wrap:balance}}.lead{{color:var(--ink2);margin:0 0 20px;max-width:90ch}}
.g3{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-top:8px}}@media (max-width:900px){{.g3{{grid-template-columns:minmax(0,1fr)}}}}
.mc{{border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow);padding:16px 18px;min-width:0}}.mc.best{{border-color:#9cc7a9;background:#f6fbf7}}
.mc h3{{font-family:var(--title);font-size:21px;font-weight:600;margin:0}}.sub{{color:var(--muted);font-size:13px;margin:2px 0 12px}}
.blk{{border-top:1px solid var(--line);padding-top:12px;margin-top:12px}}.lbl{{font-size:11.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--muted)}}
.big{{font-family:var(--title);font-size:34px;font-weight:600;line-height:1.1;margin:4px 0 8px}}.big small{{font-family:var(--body);font-size:14px;font-weight:400;color:var(--ink2);margin-left:6px}}
.parts{{display:grid;gap:4px;margin-top:10px;font-size:13.5px}}.parts div{{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:6px;align-items:center}}
.parts i{{display:block;width:10px;height:10px;border-radius:2px}}.parts em{{font-style:normal;min-width:0}}.parts em small{{display:block;color:var(--muted);font-size:12px}}
.w16s{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:12px}}@media (max-width:900px){{.w16s{{grid-template-columns:minmax(0,1fr)}}}}
.w16{{border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow);padding:12px;display:grid;gap:10px;min-width:0}}
.w16 img{{width:100%;height:80px;object-fit:contain;background:#1b2126;border-radius:6px}}.w16h{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.w16h b{{font-size:15px}}.w16h span{{color:var(--ink2)}}
.w16 .why{{font-size:11.5px;font-weight:600;color:#9b2c2c;background:#f8e9e9;border-radius:999px;padding:1px 8px}}.iss{{margin:6px 0;font-size:13.5px}}
.w16 dl{{display:grid;grid-template-columns:110px minmax(0,1fr);gap:3px 10px;margin:0;font-size:13px}}.w16 dt{{color:var(--muted)}}.w16 dd{{margin:0;font-variant-numeric:tabular-nums}}
.figs .why{{display:inline-block;margin-top:4px;font-size:11.5px;font-weight:600;color:#9b2c2c;background:#f8e9e9;border-radius:999px;padding:1px 8px}}.parts span{{font-variant-numeric:tabular-nums;color:var(--ink2);text-align:right}}
.parts .in{{padding-left:20px;font-size:12.5px;color:var(--ink2)}}.parts .tot{{border-top:1px solid var(--line);padding-top:4px;margin-top:2px}}
.nums{{display:flex;gap:14px;flex-wrap:wrap;font-size:13.5px;margin:8px 0 0}}.g{{color:#2f7d4a;font-weight:600}}.ot{{color:#9a6a12;font-weight:600}}.rd{{color:#b23b3b;font-weight:600}}
.how{{font-size:13px;color:var(--ink2);margin:-4px 0 8px}}.rule{{font-size:13px;color:var(--ink2);margin:8px 0 0}}.na{{color:var(--muted);font-size:13.5px;margin:8px 0 0}}
.sb{{display:flex;height:22px;border-radius:5px;overflow:hidden;gap:1px}}.sb i{{display:grid;place-items:center;color:#fff;font-size:11px;font-style:normal;font-weight:600;min-width:0;overflow:hidden;white-space:nowrap}}
.sec{{margin-top:32px}}.sec h2{{font-family:var(--title);font-weight:600;font-size:21px;margin:0}}
.figs{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin-top:10px}}.figs figure{{margin:0;min-width:0;background:var(--inset);border-radius:8px;padding:8px}}
.figs img{{width:100%;height:56px;object-fit:contain;background:#1b2126;border-radius:5px}}.figs figcaption{{font-size:12.5px;color:var(--ink2);margin-top:4px}}.figs b{{color:var(--ink)}}
.hd{{position:sticky;top:0;z-index:5;background:var(--page)}}.tabs{{display:flex;gap:4px;flex-wrap:wrap}}
.tabs button{{font:600 14px var(--body);border:0;background:none;color:var(--ink2);padding:8px 12px;border-bottom:2px solid transparent;cursor:pointer}}.tabs button[aria-selected="true"]{{color:var(--accent);border-bottom-color:var(--accent)}}
.kp{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}@media (max-width:900px){{.kp{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.kp div{{border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow);padding:12px 14px}}.kp b{{display:block;font-family:var(--title);font-size:26px;font-weight:600;line-height:1.15}}.kp span{{color:var(--ink2);font-size:13px}}
.flow{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:12px}}.fa{{color:var(--muted);font-size:20px}}.fcol{{display:grid;gap:8px}}
.fb{{border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;background:var(--card);box-shadow:var(--shadow);padding:10px 14px;max-width:300px}}.fb b{{display:block;font-size:20px}}.fb span{{color:var(--ink2);font-size:13px}}.fb small{{display:block;color:var(--muted);font-size:12.5px;margin-top:2px}}
.fb.fg{{border-left-color:#2f7d4a}}.fb.fo{{border-left-color:#d9a441}}.calc{{margin:14px 0 0;padding:10px 14px;background:var(--inset);border-radius:10px;max-width:90ch}}
.gc{{border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow);padding:14px 16px;min-width:0}}.gc h3{{font-family:var(--title);font-size:18px;font-weight:600;margin:0 0 8px}}
.gh,.gr{{display:grid;grid-template-columns:minmax(0,9fr) minmax(0,8fr) 52px 40px;gap:8px;align-items:center}}.gh{{font-size:11.5px;color:var(--muted);text-align:right}}.gr{{padding:5px 0;border-top:1px solid var(--line);font-size:13.5px}}
.gn small{{display:block;color:var(--muted);font-size:12px}}.gb{{height:10px;background:var(--inset);border-radius:5px;overflow:hidden}}.gb i{{display:block;height:100%;background:#2f7d4a}}.gb i.lowv{{background:#b23b3b}}
.gr b{{text-align:right;font-variant-numeric:tabular-nums}}.gr b.lowv{{color:#b23b3b}}.go{{text-align:right;color:var(--ink2);font-variant-numeric:tabular-nums}}
.lims{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;margin-top:10px}}@media (max-width:900px){{.lims{{grid-template-columns:minmax(0,1fr)}}}}
.lim{{border:1px solid var(--line);border-left:4px solid #d9a441;border-radius:10px;background:var(--card);box-shadow:var(--shadow);padding:12px 14px}}.lim b{{font-size:14.5px}}.lim p{{margin:4px 0 0;color:var(--ink2);font-size:13.5px}}
.fl3 .fb{{max-width:170px}}.fb.fc{{border-left-color:#d9a441}}.fb.fc small{{margin:0}}.fb.fc span{{display:block;color:var(--ink);font-weight:600;font-size:14px}}
.ccs{{display:grid;gap:14px;margin-top:10px}}.cc{{border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow);padding:14px 16px}}
.cch{{display:grid;grid-template-columns:30px minmax(0,1fr) auto;gap:10px;align-items:start}}.num{{display:grid;place-items:center;width:26px;height:26px;border-radius:50%;background:var(--accent);color:#fff;font-weight:700;font-size:13px}}
.cch h3{{font-family:var(--title);font-size:18px;font-weight:600;margin:0}}.cch p{{margin:2px 0 0;color:var(--ink2);font-size:13.5px}}.cst{{text-align:right}}.cst b{{display:block;font-family:var(--title);font-size:24px;line-height:1.1}}.cst span{{font-size:12px;color:var(--muted)}}
.csub{{margin:8px 0 10px 40px;font-size:13px;color:var(--muted)}}.exs{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.ex3{{grid-template-columns:repeat(3,minmax(0,1fr));margin-top:10px}}@media (max-width:900px){{.exs,.ex3{{grid-template-columns:minmax(0,1fr)}}}}
.ex{{margin:0;background:var(--inset);border-radius:10px;padding:10px;min-width:0}}.ex img{{display:block;width:100%;height:84px;object-fit:contain;background:#1b2126;border-radius:6px}}.ex figcaption{{margin-top:7px;font-size:13px;color:var(--ink2)}}.ex figcaption span,.ex figcaption em{{display:block}}.ex figcaption em{{font-style:normal;color:var(--muted);margin:1px 0 3px}}.ex figcaption b{{color:var(--ink)}}
.ex figcaption .vd{{display:inline-block;margin-left:8px;font-size:11.5px;font-weight:600;border-radius:999px;padding:1px 8px}}.vh{{background:#fbf1dc;color:#7c5a12}}.vs{{background:#e6f3ea;color:#22643b}}
.nl{{margin:8px 0 0;padding-left:20px;color:var(--ink2);max-width:100ch}}.nl li{{margin:5px 0}}.nl b{{color:var(--ink)}}
.foot{{margin-top:28px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:12.5px}}
</style>
<div class="wrap">
<header class="hd"><div class="top"><div><b>RackTrack</b><span class="m">Device classification · final report</span></div>
<nav class="tabs" role="tablist"><button role="tab" data-t="res" aria-selected="true">Results</button><button role="tab" data-t="num" aria-selected="false">What {pct(OK_ / A_)} means</button><button role="tab" data-t="oth" aria-selected="false">When it goes to Other</button></nav></div></header>
<div class="pn" data-p="res">
<h1>With both models and Other, {n(m12['wrong'])} of {n(N)} devices are wrong, against {n(m1['all_wrong'])} for Model 1 alone</h1>
<p class="lead">One test set: <b>{n(N)} devices in {n(S['photos'])} test photos</b> that neither model trained on. "Other" means
"not sure, a person checks this one"; it is not counted as right or wrong.</p>
{cards}
{left}
</div>
<div class="pn" data-p="num" hidden>{tab2}</div>
<div class="pn" data-p="oth" hidden>{tab3}</div>
<p class="foot">RackTrack AI team · built by scripts/devices/build_final_report.py from results/devices/test_rows.csv and test_sets.json</p>
</div>
<script>
document.querySelectorAll('[data-t]').forEach(function(b){{b.addEventListener('click',function(){{
  document.querySelectorAll('[data-t]').forEach(function(x){{x.setAttribute('aria-selected',x===b?'true':'false');}});
  document.querySelectorAll('[data-p]').forEach(function(p){{p.hidden=p.dataset.p!==b.dataset.t;}}); window.scrollTo(0,0);}});}});
</script>"""
    pathlib.Path(out).write_text(page)
    print(out, f"{len(page) / 1e6:.2f} MB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
