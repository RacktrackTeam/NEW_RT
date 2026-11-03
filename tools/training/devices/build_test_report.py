"""Model 2 (DINOv2-B detector) test report: one shareable page, every number read from the result files.

Reads results/devices/test_sets.json and test_rows.csv (test_sets.py), models/device_cls/dinov2_b/detector.json and the
device crops for the pictures.

    python scripts/devices/build_test_report.py OUT.html
"""
import base64
import csv
import html
import io
import json
import pathlib
import sys
from collections import Counter

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[2]
CROPS = ROOT / "data" / "device_crops"
RES = ROOT / "results" / "devices"
D2 = ROOT / "models" / "device_cls" / "dinov2_b"
SRC = [("m1", "Model 1", "ConvNeXt-Tiny"), ("m2", "Model 2", "DINOv2-B"), ("m12", "Model 1 + 2", "both must agree")]
SETS = [("all", "Every test photo", "every labelled device in the test photos neither model trained on"),
        ("hi", "Photos of 1 MP or more", "the same devices, only in photos of 1 MP or more"),
        ("clear", "Clear devices only", "of those, the sharp, front-on, well-lit devices")]
C = {"r": "#2f7d4a", "or": "#e7c98f", "ow": "#c9852d", "w": "#b23b3b", "m1": "#9aa6b2", "m2": "#1f5a85", "m12": "#2f7d4a"}


def pct(v, d=1):
    return f"{100 * v:.{d}f} %"


def n(v):
    return f"{v:,}"


def thumb(crop, h=56):
    im = Image.open(CROPS / crop).convert("RGB"); im.thumbnail((int(h * 6), h * 2))
    b = io.BytesIO(); im.save(b, "JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


def bar(parts, total):
    """A to-scale stacked bar: parts = [(value, colour, label)]."""
    segs = "".join(f'<i style="flex:{v};background:{c}" title="{html.escape(l)}: {n(v)}">{n(v) if v / total > .07 else ""}</i>' for v, c, l in parts if v)
    return f'<div class="sb">{segs}</div>'


def versus(w):
    """Model 1 against Model 2 on the same devices: only-Model-1-right and only-Model-2-right counts, and the chance
    that a difference this size is luck (two-sided sign test)."""
    from scipy.stats import binomtest
    a, b = w.get("m1 only", 0), w.get("m2 only", 0)
    p = binomtest(min(a, b), a + b, .5).pvalue if a + b else 1.0
    who = "a tie: the difference is within chance" if p >= .05 else ("Model 1 is better" if a > b else "Model 2 is better")
    return a, b, p, who


def band_label(lo, hi, unit):
    if lo == 0:
        return f"under {hi:g} {unit}"
    if hi >= 1e8:
        return f"{lo:g} {unit} or more"
    return f"{lo:g} to {hi:g} {unit}"


def said(b, score):
    return "no box" if b == "none" else f"{b} · {float(score):.2f}"


def hbar(v, colour, vmax=1.0):
    return f'<span class="hb"><i style="width:{100 * v / vmax:.1f}%;background:{colour}"></i></span>'


def main(out):
    T = json.load(open(RES / "test_sets.json"))
    rows = list(csv.DictReader(open(RES / "test_rows.csv")))
    meta = json.load(open(D2 / "detector.json")); cf = meta["config"]
    S = T["sets"]; A, H, CL = S["all"], S["hi"], S["clear"]
    t = lambda s, k: S[s]["tally"][k]

    # ---------- 02 how Model 1 + 2 works
    Hr = [r for r in rows if r["hi"] == "1"]
    agree = [r for r in Hr if r["m1"] == r["m2"]]; dis = [r for r in Hr if r["m1"] != r["m2"]]
    ag_r = sum(r["m1"] == r["class"] for r in agree); ds_r = sum(r["m1"] == r["class"] for r in dis)
    m2_fix = sum(r["m2"] == r["class"] != r["m1"] for r in dis); none = sum(r["m2"] == "none" for r in dis)

    def example(pool, label):
        pool = sorted(pool, key=lambda r: -float(r["sharpness"]))
        if not pool:
            return ""
        r = pool[0]
        return (f'<div class="ex"><img src="{thumb(r["crop"], 64)}" alt="{r["class"]}"><dl><dt>Really</dt><dd>{r["class"]}</dd>'
                f'<dt>Model 1</dt><dd>{r["m1"]} · {float(r["m1_prob"]):.0%}</dd><dt>Model 2</dt><dd>{said(r["m2"], r["m2_score"])}</dd></dl><span class="res {label[1]}">{label[0]}</span></div>')
    exs = (example([r for r in agree if r["m1"] == r["class"]], ("same type: answered, right", "ok"))
           + example([r for r in dis if r["m1"] != r["class"] and r["m2"] == r["class"]], ("different: Other, a wrong answer avoided", "av"))
           + example([r for r in dis if r["m1"] == r["class"] and r["m2"] not in ("none", r["class"])], ("different: Other, a right answer lost", "lo")))
    how = f"""
<section class="sec" id="s1"><div class="sh"><span class="no">01</span><h2>How Model 1 + 2 works</h2></div>
<div class="card">
<div class="flow">
  <div class="fb"><b>A device</b>in the rack photo</div><span class="ar">→</span>
  <div class="fcol"><div class="fb"><b>Model 1 · ConvNeXt-Tiny</b>names the cut-out device</div><div class="fb"><b>Model 2 · DINOv2-B</b>finds devices in the whole photo; its box on this device gives a name</div></div><span class="ar">→</span>
  <div class="fb q"><b>Same type?</b></div><span class="ar">→</span>
  <div class="fcol"><div class="fb ok"><b>Yes: answered</b>{n(len(agree))} devices · {n(ag_r)} right, {n(len(agree) - ag_r)} wrong</div>
    <div class="fb oth"><b>No, or no box: Other</b>{n(len(dis))} devices · a person checks them; {n(ds_r)} would have been right, {n(len(dis) - ds_r)} wrong</div></div>
</div>
<p class="sub" style="margin-top:12px">Counts for the {n(len(Hr))} devices in test photos of 1 MP or more. The answer is always Model 1's type;
Model 2 can only stop it. There is nothing to tune: same type is answered, a different type is Other.</p>
<div class="exs">{exs}</div>
<p class="note">Why it works: the two models are built differently (a crop classifier and a whole-photo detector) and make different mistakes,
so they rarely agree on a wrong type: {n(len(agree) - ag_r)} times here. In {n(m2_fix)} of the {n(len(dis))} disagreements Model 2 had the right type,
and in {n(none)} it found no box. The cost: {n(ds_r)} right answers of Model 1 go to a person as Other.</p>
</div></section>"""

    # ---------- 03 results on the three sets
    def set_card(key, title, desc):
        s = S[key]
        head = f'<div class="ch"><h3>{title}</h3><span class="chip">{n(s["n"])} devices · {n(s["photos"])} photos</span></div><p class="sub">{desc}</p>'
        tb = '<table class="t"><thead><tr><th></th><th class="r">Accuracy</th><th class="r">Macro-F1</th><th class="r">Answered right</th><th class="r">Answered wrong</th><th class="r">Other</th><th class="r">Other: would be right / wrong</th><th class="r">Answers right</th><th>The devices, with Other</th></tr></thead><tbody>'
        for k, name, sub in SRC:
            x = t(key, k)
            acc = "" if k == "m12" else pct(x["accuracy"]); mf = "" if k == "m12" else pct(x["macro_f1"])
            tb += (f'<tr class="{"hl" if k == "m12" else ""}"><td><b>{name}</b><span>{sub}</span></td><td class="r">{acc}</td><td class="r">{mf}</td>'
                   f'<td class="r">{n(x["right"])}</td><td class="r">{n(x["wrong"])}</td><td class="r">{n(x["other"])}</td>'
                   f'<td class="r">{n(x["other_right"])} / {n(x["other_wrong"])}</td><td class="r"><b>{pct(x["answers_right"])}</b></td>'
                   f'<td class="w">{bar([(x["right"], C["r"], "answered right"), (x["other_right"], C["or"], "Other, would be right"), (x["other_wrong"], C["ow"], "Other, would be wrong"), (x["wrong"], C["w"], "answered wrong")], x["n"])}</td></tr>')
        a, b, p, who = versus(T["who_right"][key])
        vs = f'<p class="note">Model 1 against Model 2, answering every device: only Model 1 right on {n(a)}, only Model 2 right on {n(b)} (chance p = {p:.2f}): <b>{who}</b>.</p>'
        return f'<div class="card">{head}<div class="tw">{tb}</tbody></table></div>{vs}</div>'
    legend = "".join(f'<span><i style="background:{c}"></i>{l}</span>' for c, l in
                     [(C["r"], "answered right"), (C["or"], "Other, answer would have been right"), (C["ow"], "Other, answer would have been wrong"), (C["w"], "answered wrong")])
    res = f"""
<section class="sec" id="s2"><div class="sh"><span class="no">02</span><h2>Results on the three test sets</h2></div>
<p class="lead2">Accuracy: each model must name every device. With Other: a model may answer "not sure, check this one", which is
neither right nor wrong, so "answers right" counts only the answers given. Model 1 + 2 answers only when both name the same type.
Labels are the ground truth with 10 corrections.</p>
<div class="legend">{legend}</div>
{''.join(set_card(*x) for x in SETS)}
<p class="note">Model 1 + 2 has no accuracy of its own: when it answers, it gives Model 1's type. Clear devices: sharpness 450 or more,
the device fills half its outline or more, brightness 50 or more.</p></section>"""

    # ---------- 03 where Model 2 fails
    W = [r for r in rows if r["m2"] != r["class"]]
    pt = A["per_type"]
    types = sorted(pt["m2"], key=lambda c: pt["m2"][c]["recall"])
    by_type = "".join(f'<div class="row"><span class="lab">{c}<em>{n(pt["m2"][c]["n"])} devices</em></span>'
                      f'<span class="pair">{hbar(pt["m1"][c]["recall"], C["m1"])}{hbar(pt["m2"][c]["recall"], C["m2"])}</span>'
                      f'<b>{pct(pt["m1"][c]["recall"], 0)} · {pct(pt["m2"][c]["recall"], 0)}</b></div>' for c in types)
    mix = Counter((r["class"], r["m2"]) for r in W).most_common(6)
    mixcards = ""
    for (a, b), k in mix:
        ex = [r for r in W if r["class"] == a and r["m2"] == b]
        ex = sorted(ex, key=lambda r: -float(r["sharpness"]))[:3]
        figs = "".join(f'<figure><img src="{thumb(r["crop"])}" alt="{a}"><figcaption>Model 2: {said(b, r["m2_score"])} · Model 1: {r["m1"]}</figcaption></figure>' for r in ex)
        mixcards += f'<div class="mx"><div class="mxh"><b>{a}</b><span>called</span><b class="bad">{b if b != "none" else "not found"}</b><span class="chip">{k} times</span></div><div class="figs">{figs}</div></div>'
    share3 = sum(k for _, k in mix[:3]) / max(1, len(W))
    hb = T["by_model2_height"]
    hrows = "".join(f'<div class="row"><span class="lab">{band_label(x["lo"], x["hi"], "px")}<em>{n(x["n"])} devices</em></span>'
                    f'<span class="pair">{hbar(x["m1_right"], C["m1"])}{hbar(x["m2_right"], C["m2"])}</span><b>{pct(x["m1_right"], 0)} · {pct(x["m2_right"], 0)}</b></div>'
                    for x in hb if x["n"] >= 20)
    q = T["by_quality"]
    qrows = "".join(f'<div class="row"><span class="lab">{k}<em>{n(v["n"])} devices</em></span><span class="pair">{hbar(v["m1_right"], C["m1"])}{hbar(v["m2_right"], C["m2"])}</span>'
                    f'<b>{pct(v["m1_right"], 0)} · {pct(v["m2_right"], 0)}</b></div>' for k, v in q.items())
    mp = T["by_photo_mp"]
    mprows = "".join(f'<div class="row"><span class="lab">{band_label(x["lo"], x["hi"], "MP")}<em>{n(x["n"])} devices</em></span>'
                     f'<span class="pair">{hbar(x["m1_right"], C["m1"])}{hbar(x["m2_right"], C["m2"])}</span><b>{pct(x["m1_right"], 0)} · {pct(x["m2_right"], 0)}</b></div>' for x in mp)
    cm = A["confusion"]["m2"]
    cmt = '<table class="t cm"><thead><tr><th>Real ↓ · Model 2 said →</th>' + "".join(f"<th>{c}</th>" for c in cm["cols"]) + "</tr></thead><tbody>"
    for i, r_ in enumerate(cm["rows"]):
        cmt += f"<tr><td><b>{r_}</b></td>" + "".join(
            f'<td class="{"d" if cm["cols"][j] == r_ else "e" if v else ""}">{v or ""}</td>' for j, v in enumerate(cm["cells"][i])) + "</tr>"
    cmt += "</tbody></table>"
    key = f'<div class="legend"><span><i style="background:{C["m1"]}"></i>Model 1</span><span><i style="background:{C["m2"]}"></i>Model 2</span><span>share named right, to scale, 0 to 100 %</span></div>'
    fails = f"""
<section class="sec" id="s3"><div class="sh"><span class="no">03</span><h2>Where Model 2 fails</h2></div>
<p class="lead2">On every test photo, Model 2 names {n(len(W))} of {n(len(rows))} devices wrong or misses them ({pct(len(W) / len(rows))}).
The three biggest mix-ups hold {pct(share3, 0)} of those mistakes.</p>
<div class="card"><h3>The mix-ups, with the devices themselves</h3><p class="sub">The 6 most common, each with up to 3 real test devices.</p>
<div class="mxs">{mixcards}</div></div>
<div class="grid2">
  <div class="card"><h3>By device type</h3><p class="sub">Share of each type named right (recall), on every test photo. Weakest first.</p>{key}<div class="rows">{by_type}</div></div>
  <div class="card"><h3>By device height, as Model 2 sees it</h3><p class="sub">Model 2 shrinks every photo to a long side of {meta['image_size']} px, so a device's height in pixels drops with it.</p>{key}<div class="rows">{hrows}</div>
    <h3 style="margin-top:18px">By picture quality</h3><p class="sub">Photos of 1 MP or more.</p><div class="rows">{qrows}</div></div>
</div>
<div class="grid2">
  <div class="card"><h3>By photo size</h3><p class="sub">Every test photo, by megapixels.</p><div class="rows">{mprows}</div></div>
  <div class="card"><h3>Who gets it right</h3><p class="sub">Every test photo, both models answering every device.</p>
    <div class="rows">{''.join(f'<div class="row"><span class="lab">{l}</span>{hbar(T["who_right"]["all"].get(k, 0) / len(rows), c)}<b>{n(T["who_right"]["all"].get(k, 0))}</b></div>' for k, l, c in [("both", "Both right", C["r"]), ("m2 only", "Only Model 2 right", C["m2"]), ("m1 only", "Only Model 1 right", C["m1"]), ("neither", "Both wrong", C["w"])])}</div>
    <p class="note">The two models are wrong on different devices: only {n(T["who_right"]["all"].get("neither", 0))} are missed by both. That is why answering only when they agree removes most mistakes.</p></div>
</div>
<details class="card"><summary>The full confusion table, Model 2, every test photo</summary><div class="tw">{cmt}</div></details>
</section>"""

    # ---------- 04 what raises accuracy
    hb_small = next((x for x in hb if x["lo"] == 12), None); hb_mid = next((x for x in hb if x["lo"] == 16), None)
    worst = types[0]
    tA = A["tally"]
    tilt, front = q["tilted or partly hidden"], q["sharp, front-on"]
    m1gap, m2gap = front["m1_right"] - tilt["m1_right"], front["m2_right"] - tilt["m2_right"]
    gm = "Model 1" if m1gap >= m2gap else "Model 2"; gk = "m1_right" if gm == "Model 1" else "m2_right"
    weak2 = sorted(pt["m2"], key=lambda c: pt["m2"][c]["recall"])[0]; weak1 = sorted(pt["m1"], key=lambda c: pt["m1"][c]["recall"])[0]
    items = [
        ("Use both models together", f"Wrong answers on every test photo: Model 1 {n(tA['m1']['wrong'])}, Model 2 {n(tA['m2']['wrong'])}, both together {n(tA['m12']['wrong'])} (with Other {n(tA['m12']['other'])}). "
                                     f"They miss different devices: only {n(T['who_right']['all'].get('neither', 0))} are missed by both.",
         "Answer only when both name the same type; send the rest to a person. Wire Model 1 + 2 into the app.", "biggest gain"),
        ("Each model covers the other's weak types", f"Model 2 is stronger on {', '.join(c for c in pt['m2'] if pt['m2'][c]['recall'] - pt['m1'][c]['recall'] >= .1)}; "
                                     f"Model 1 on {', '.join(c for c in pt['m1'] if pt['m1'][c]['recall'] - pt['m2'][c]['recall'] >= .05) or 'none by 5 points or more'}.",
         "Keep both. When they disagree, the type each model is strong on is the better guess for the person who checks.", "proven"),
        ("A larger input for Model 2", (f"Devices {hb_small['lo']:g} to {hb_small['hi']:g} px tall as Model 2 sees them: {pct(hb_small['m2_right'])} right, against "
                                        f"{pct(hb_mid['m2_right'])} at {hb_mid['lo']:g} to {hb_mid['hi']:g} px. Model 2 sees every photo at a long side of {meta['image_size']} px.") if hb_small and hb_mid else "",
         f"One run with --max_side 1036 instead of {meta['image_size']} (it fits 8 GB with --batch_size 1 and gradient checkpointing), compared item by item.", "worth testing"),
        ("Photos from the front", f"{gm}: tilted or partly hidden devices {pct(tilt[gk])} right, sharp front-on ones {pct(front[gk])} ({n(tilt['n'])} and {n(front['n'])} devices).",
         "The capture guide asks for a straight sweep; more angled examples in training also help.", "small sample" if tilt["n"] < 50 else "proven" if max(m1gap, m2gap) >= .05 else "small effect"),
        ("The look-alike pairs", f"{', '.join(f'{a} called {b} ({k})' for (a, b), k in mix[:3] if b != 'none')}: Model 2's most common mix-ups.",
         "Check these labels first with the network team, and add examples of these pairs.", "proven"),
        (f"More {weak2} examples", f"{weak2} is Model 2's weakest type: {pct(pt['m2'][weak2]['recall'])} named right ({n(pt['m2'][weak2]['n'])} test devices); "
                                   f"Model 1's weakest is {weak1} ({pct(pt['m1'][weak1]['recall'])}).",
         f"Collect and label more {weak2} and {weak1} photos.", "likely gain")]
    raise_ = '<section class="sec" id="s4"><div class="sh"><span class="no">04</span><h2>What raises accuracy</h2></div><div class="items">' + "".join(
        f'<div class="it"><div class="ih"><span class="badge">{i + 1}</span><b>{a}</b><span class="chip {"dark" if s in ("biggest gain",) else ""}">{s}</span></div>'
        f'<div class="ins"><div><i>Proof</i>{p}</div><div class="do"><i>What to do</i>{d}</div></div></div>' for i, (a, p, d, s) in enumerate(items)) + "</div></section>"

    # ---------- page
    tiles = "".join(f'<div class="tile"><div class="v">{v}</div><div class="k">{k}</div><div class="f">{f}</div></div>' for v, k, f in [
        (n(A["n"]), "test devices", f"in {n(A['photos'])} test photos neither model trained on; {n(H['n'])} in photos of 1 MP or more"),
        (pct(tA["m1"]["accuracy"]), "Model 1", "named right, every test photo"),
        (pct(tA["m2"]["accuracy"]), "Model 2", "named right, every test photo"),
        (pct(tA["m12"]["answers_right"]), "Model 1 + 2", f"of answers right; {n(tA['m12']['wrong'])} wrong, {n(tA['m12']['other'])} Other")])
    a_all, b_all, p_all, who_all = versus(T["who_right"]["all"])
    headline = (f"On photos neither model trained on, Model 1 and Model 2 are level; together they leave {n(tA['m12']['wrong'])} of {n(A['n'])} devices wrong"
                if p_all >= .05 else f"{'Model 1' if a_all > b_all else 'Model 2'} names more devices right; together they leave {n(tA['m12']['wrong'])} of {n(A['n'])} devices wrong")
    page = f"""<title>DINOv2 Test Report</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,600&family=Source+Sans+3:wght@400;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{color-scheme:light;--page:#fff;--card:#fff;--inset:#f4f6f7;--line:#e2e6e9;--ink:#1b2126;--ink2:#47525b;--muted:#6d7881;--accent:#1f5a85;--soft:#e8f0f6;
--title:"Newsreader",Georgia,serif;--body:"Source Sans 3",-apple-system,"Segoe UI",sans-serif;--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;--shadow:0 1px 2px rgba(20,30,40,.04),0 2px 10px rgba(20,30,40,.04)}}
*{{box-sizing:border-box}}body{{background:var(--page);color:var(--ink);font-family:var(--body);font-size:14.5px;line-height:1.5;margin:0}}
.wrap{{width:90%;margin:0 auto;padding-block:0 48px}}@media (max-width:800px){{.wrap{{width:100%;padding-left:16px;padding-right:16px}}}}
.top{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:center;padding-block:14px;border-bottom:1px solid var(--line)}}.top b{{letter-spacing:.01em}}.top span.m{{color:var(--muted);margin-left:8px}}
.chips{{display:flex;gap:6px;flex-wrap:wrap}}.chip{{font-family:var(--mono);font-size:11.5px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--ink2);white-space:nowrap;background:var(--card)}}
.chip.dark{{background:var(--ink);color:#fff;border-color:var(--ink)}}.mono{{font-family:var(--mono);font-size:12.5px}}
h1{{font-family:var(--title);font-weight:600;font-size:28px;line-height:1.2;margin:26px 0 6px;max-width:46ch;text-wrap:balance}}.lead{{color:var(--ink2);margin:0 0 20px;max-width:90ch}}
.tiles{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-bottom:8px}}@media (max-width:900px){{.tiles{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.tile,.card{{border:1px solid var(--line);border-radius:12px;background:var(--card);box-shadow:var(--shadow);min-width:0}}.tile{{padding:12px 14px}}
.tile .v{{font-family:var(--title);font-size:26px;font-weight:600;line-height:1.1}}.tile .k{{font-weight:600;font-size:13px;margin-top:4px}}.tile .f{{font-size:12.5px;color:var(--muted)}}
.card{{padding:16px 18px;margin-bottom:14px}}.card h3{{margin:0;font-size:15.5px}}.sub{{margin:2px 0 10px;color:var(--muted);font-size:13px}}
.ch{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.sec{{margin-top:30px}}.sh{{display:flex;gap:12px;align-items:baseline;border-bottom:1px solid var(--line);padding-bottom:8px;margin-bottom:14px}}
.sh .no{{font-family:var(--mono);font-size:13px;color:var(--accent)}}.sh h2{{font-family:var(--title);font-weight:600;font-size:21px;margin:0}}
.lead2{{color:var(--ink2);max-width:95ch;margin:0 0 12px}}.note{{color:var(--muted);font-size:13px;margin:10px 0 0}}.cap{{color:var(--muted);font-size:12px;margin:6px 0 0}}
.two{{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:24px}}.grid2{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
@media (max-width:900px){{.two,.grid2{{grid-template-columns:minmax(0,1fr)}}}}.two p{{margin:0 0 10px}}
.tw{{overflow-x:auto}}table.t{{border-collapse:collapse;width:100%;font-size:13.5px;font-variant-numeric:tabular-nums}}
.t th{{text-align:left;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);border-bottom:1px solid var(--line);padding:7px 8px;white-space:nowrap}}
.t td{{border-bottom:1px solid var(--line);padding:8px;vertical-align:middle}}.t td span{{display:block;color:var(--muted);font-size:12px}}.t .r{{text-align:right}}.t td.w{{min-width:240px}}
.t tr.hl td{{background:#f1f8f3}}.cm td,.cm th{{text-align:center;font-size:12.5px}}.cm td:first-child{{text-align:left}}.cm td.d{{background:#e6f2ea;font-weight:600}}.cm td.e{{background:#f8e9e9}}
.sb{{display:flex;height:22px;border-radius:5px;overflow:hidden;gap:1px}}.sb i{{display:grid;place-items:center;color:#fff;font-size:11px;font-style:normal;font-weight:600;min-width:0;overflow:hidden;white-space:nowrap}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--ink2);margin:0 0 10px}}.legend i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}}
.rows{{display:grid;gap:8px}}.row{{display:grid;grid-template-columns:200px minmax(0,1fr) 110px;gap:10px;align-items:center;font-size:13px}}
.row .lab em{{display:block;font-style:normal;color:var(--muted);font-size:12px}}.row b{{text-align:right;font-variant-numeric:tabular-nums}}
.hb{{display:block;height:9px;background:var(--inset);border-radius:5px;overflow:hidden}}.hb i{{display:block;height:100%;border-radius:5px}}.pair{{display:grid;gap:3px}}
.mxs{{display:grid;gap:12px}}.mx{{background:var(--inset);border-radius:10px;padding:10px 12px}}.mxh{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:8px}}.mxh span{{color:var(--muted)}}.mxh .bad{{color:#9b2c2c}}
.figs{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}}@media (max-width:700px){{.figs{{grid-template-columns:minmax(0,1fr)}}}}
.figs figure{{margin:0;min-width:0}}.figs img{{width:100%;height:64px;object-fit:contain;background:#1b2126;border-radius:5px}}.figs figcaption{{font-size:12px;color:var(--ink2);margin-top:3px}}
.items{{display:grid;gap:12px}}.it{{border:1px solid var(--line);border-radius:12px;padding:12px 14px;box-shadow:var(--shadow)}}
.ih{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.ih b{{font-size:15px}}.badge{{font-family:var(--mono);font-size:12.5px;width:26px;height:26px;display:grid;place-items:center;border-radius:7px;background:var(--soft);color:var(--accent)}}
.ins{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:8px}}@media (max-width:800px){{.ins{{grid-template-columns:minmax(0,1fr)}}}}
.ins>div{{background:var(--inset);border-radius:8px;padding:9px 11px;font-size:13.5px;color:var(--ink2)}}.ins>div.do{{background:var(--soft);color:var(--ink)}}
.ins i{{display:block;font-style:normal;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--muted);margin-bottom:2px}}
.flow{{display:grid;grid-template-columns:minmax(0,.8fr) 22px minmax(0,1.3fr) 22px minmax(0,.6fr) 22px minmax(0,1.5fr);gap:8px;align-items:center}}
.flow .ar{{color:var(--muted);text-align:center;font-size:18px}}.fcol{{display:grid;gap:8px}}
.fb{{background:var(--inset);border-radius:9px;padding:10px 12px;font-size:13px;color:var(--ink2);line-height:1.35}}.fb b{{display:block;color:var(--ink);font-size:13.5px;margin-bottom:2px}}
.fb.q{{text-align:center;background:var(--soft)}}.fb.ok{{background:#e6f2ea}}.fb.oth{{background:#f7ecd9}}
@media (max-width:900px){{.flow{{grid-template-columns:minmax(0,1fr)}}.flow .ar{{transform:rotate(90deg)}}}}
.exs{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}}@media (max-width:900px){{.exs{{grid-template-columns:minmax(0,1fr)}}}}
.ex{{background:var(--inset);border-radius:10px;padding:10px 12px;display:grid;gap:8px;min-width:0}}.ex img{{width:100%;height:64px;object-fit:contain;background:#1b2126;border-radius:5px}}
.ex dl{{display:grid;grid-template-columns:auto minmax(0,1fr);gap:3px 10px;margin:0;font-size:12.5px}}.ex dt{{color:var(--muted)}}.ex dd{{margin:0;font-weight:600}}
.res{{font-size:12.5px;font-weight:600;border-radius:999px;padding:3px 10px;justify-self:start}}.res.ok{{background:#e6f2ea;color:#1d6b32}}.res.av{{background:#f7ecd9;color:#7a4c00}}.res.lo{{background:#f8e9e9;color:#9b2c2c}}
details.card summary{{cursor:pointer;font-weight:600}}details.card[open] summary{{margin-bottom:10px}}
.foot{{margin-top:28px;padding-top:12px;border-top:1px solid var(--line);color:var(--muted);font-size:12.5px}}
</style>
<div class="wrap">
<div class="top"><div><b>RackTrack</b><span class="m">Device classification · DINOv2-B test report</span></div>
<div class="chips"><span class="chip">best epoch {meta['epoch']}</span><span class="chip">long side {meta['image_size']} px</span><span class="chip">26 Oct 2026</span></div></div>
<h1>{headline}</h1>
<p class="lead">The new DINOv2-B detector tested like Model 1, on the test photos neither model trained on: every photo, photos of 1 MP or more, and clear devices only;
where it fails, what it confuses, and what would raise accuracy. Model 1 is ConvNeXt-Tiny, the classifier in use.</p>
<div class="tiles">{tiles}</div>
{how}{res}{fails}{raise_}
<section class="sec" id="s5"><div class="sh"><span class="no">05</span><h2>Not tested yet</h2></div>
<div class="card"><div class="rows">
<div class="row"><span class="lab"><b>Model 2's score per epoch</b></span><span>Needs log.csv from the run folder.</span><b><span class="chip">pending</span></b></div>
<div class="row"><span class="lab"><b>Load Balancer</b></span><span>Too few labelled test devices to measure; left out above.</span><b><span class="chip">too few</span></b></div>
</div></div></section>
<p class="foot">RackTrack AI team · DINOv2-B test report · built by scripts/devices/build_test_report.py from results/devices/test_sets.json</p>
</div>"""
    pathlib.Path(out).write_text(page)
    print(out, f"{len(page) / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1])
