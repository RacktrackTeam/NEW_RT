"""Build the step-by-step walkthrough report (HTML) from scripts/occupancy/walkthrough_data.py output.

    python3 scripts/occupancy/build_walkthrough_report.py <walkthrough_dir>      -> <walkthrough_dir>/index.html
"""
import html
import json
import pathlib
import sys

D = pathlib.Path(sys.argv[1])          # add --standalone for a page with its own <html> wrapper
d = json.load(open(D / "data.json"))
rows = d["rows"]
S = d["summary"]
CUT = d["uncertain_below"]
members = d["members"]
esc = html.escape
U = lambda u: u.upper()
pct = lambda x, n=0: f"{100 * x:.{n}f}%"

n_raw, n_kept = d["raw"]["unit_boxes"], d["raw"]["after_duplicates"]
by = {"classifier": [], "classifier + device detector": [], "device detector": []}
for r in rows:
    by.setdefault(r["decided_by"], []).append(r)
overruled = by["device detector"]
flagged = [r for r in rows if r["attention"] == "warning"]       # classifier free, device finder saw a device
agreed = by["classifier + device detector"]
alone = by["classifier"]
trusted = [x for x in d["devices"] if x["trusted"]]
inferred = [r for r in rows if r["source"] == "inferred"]
unc = [r for r in rows if r["status"] == "uncertain" and r["attention"] == "uncertain"]
STATUS = {"occupied": "Occupied", "unoccupied": "Unoccupied", "uncertain": "Uncertain"}
pill = lambda s: f"<span class='pill {s}'>{STATUS[s]}</span>"
BY = {"classifier": "Classifier alone", "classifier + device detector": "Both agree", "device detector": "Device finder overruled",
      "classifier (device warning)": "They disagree → check"}


def heat(p):
    """Cell tint: green for free, red for occupied, stronger the surer the model is."""
    a = abs(p - 0.5) * 2 * 0.55
    var = "--occ-rgb" if p >= 0.5 else "--free-rgb"
    return f"background:rgba(var({var}),{a:.2f})"


def strip(r, caption=""):
    return (f"<figure class='strip'><img src='{r['strip']}' alt='{U(r['unit'])} cut from the photo'>"
            f"<figcaption>{caption}</figcaption></figure>")


def bars(p):
    return (f"<div class='pb'><span>Occupied</span><div class='pt'><i class='o' style='width:{100*p:.0f}%'></i>"
            f"<b style='left:{100*CUT:.0f}%'></b></div><em>{pct(p)}</em></div>"
            f"<div class='pb'><span>Unoccupied</span><div class='pt'><i class='f' style='width:{100*(1-p):.0f}%'></i>"
            f"<b style='left:{100*CUT:.0f}%'></b></div><em>{pct(1-p)}</em></div>")


def rack_strip(highlight=None, fit=None):
    cells = []
    for r in rows:
        cls = r["status"]
        extra = " fit" if fit and r["unit"] in fit else ""
        cells.append(f"<div class='rc {cls}{extra}' title='{U(r['unit'])} · {STATUS[r['status']]}'>"
                     f"<span>{U(r['unit'])}</span></div>")
    return "<div class='rackcol'>" + "".join(cells) + "</div>"


# ------------------------------------------------------------------ step content
ex_occ = next(r for r in alone + agreed if r["status"] == "occupied" and r["confidence"] > .75)
ex_free = max((r for r in agreed + alone if r["status"] == "unoccupied"), key=lambda r: r["confidence"])
ex_unc = unc[0] if unc else None
ex_over = max(flagged + overruled, key=lambda r: r["confidence"]) if flagged + overruled else None

member_head = "".join(f"<th class='n'>{m['name']}<small>{pct(m['accuracy'],1)}</small></th>" for m in members)
score_rows = ""
for r in rows:
    cells = "".join(f"<td class='n' style='{heat(r['members'][m['key']])}'>{pct(r['members'][m['key']])}</td>" for m in members)
    score_rows += (f"<tr><th>{U(r['unit'])}</th>{cells}<td class='n strong' style='{heat(r['p_occupied'])}'>{pct(r['p_occupied'])}</td>"
                   f"<td class='n strong'>{pct(1 - r['p_occupied'])}</td></tr>")

strip_list = "".join(f"<figure><figcaption>{U(r['unit'])}{' · filled in' if r['source']=='inferred' else ''}</figcaption>"
                     f"<img src='{r['strip']}' alt='{U(r['unit'])} cut out of the photo' loading='lazy'></figure>" for r in rows)
crop_grid = "".join(f"<figure><img src='{r['crop']}' alt='{U(r['unit'])} cut-out' loading='lazy'><figcaption>{U(r['unit'])}"
                    f"{' · filled in' if r['source']=='inferred' else ''}</figcaption></figure>" for r in rows)

ladder_rules = [
    ("Drop duplicates", "Two boxes sharing more than 60% of their height are the same row; keep the more confident one.",
     f"{n_raw} boxes → {n_kept} ({n_raw - n_kept} duplicate dropped)" if n_raw != n_kept else "No duplicates"),
    ("Drop boxes that are too tall", "A box more than 2.5× the typical row height covers several rows, so it is ignored.",
     f"Typical row height here: {d['raw']['median_height']:.0f} px. None too tall."),
    ("Split overlaps", "Where two neighbouring rows overlap by a few pixels, the border goes in the middle, so rows touch without gaps.",
     "Applied to every touching pair"),
    ("Fill missed rows", "A gap worth half a row or more between two rows is a row the model missed; it is filled in.",
     "No gaps inside the rack"),
    ("Extend to the rack frame", "If the rack frame carries on past the first or last row by 60% of a row or more, rows are added there. Never past a cross rail.",
     f"{len(inferred)} rows added at the bottom ({', '.join(U(r['unit']) for r in inferred)})" if inferred else "Nothing to add"),
    ("Number the rows", "Rows are numbered from the bottom: the lowest row is U01, as racks are labelled.",
     f"U01 … U{len(rows):02d}"),
]
ladder_tbl = "".join(f"<tr><td><b>{a}</b></td><td>{b}</td><td>{c}</td></tr>" for a, b, c in ladder_rules)

over_tbl = "".join(
    f"<tr><th>{U(r['unit'])}</th><td>{pill(r['leaning'])} <span class='muted'>{pct(r['confidence'])} sure</span></td>"
    f"<td>{esc(r['device'])} <span class='muted'>{pct(r['device_conf'])}</span></td><td>{pill(r['status'])}</td></tr>"
    for r in flagged + overruled)

final_tbl = ""
for r in rows:
    why = r["reason"] or ({"classifier": f"Classifier {pct(r['confidence'])} sure, no device seen",
                           "classifier + device detector": f"Classifier {pct(r['confidence'])} sure; device finder saw {r['device'] or 'the same'}"}
                          .get(r["decided_by"], ""))
    final_tbl += (f"<tr class='st-{r['status']}'><th>{U(r['unit'])}</th><td><img class='thumb' src='{r['strip']}' alt=''></td>"
                  f"<td>{pill(r['status'])}</td><td class='n'>{pct(r['p_occupied'])}</td><td class='n'>{pct(1-r['p_occupied'])}</td>"
                  f"<td>{BY.get(r['decided_by'], r['decided_by'])}</td><td class='why'>{esc(why)}</td></tr>")

free_list = "".join(f"<li><b>{esc(f['range'])}</b><span>{f['size']} U</span></li>" for f in sorted(d["free_runs"], key=lambda f: -f["size"]))
fit = d["fit"]
fit_units = set()
if fit["suggestions"]:
    rng = fit["suggestions"][0]["range"].replace("\u2013", "-").split("-")
    lo, hi = int(rng[0][1:]), int(rng[-1][1:])
    fit_units = {f"u{k:02d}" for k in range(lo, hi + 1)}
fit_list = "".join(f"<li><b>{esc(s['range'])}</b> {esc(s['reason'])}</li>" for s in fit["suggestions"])

STEPS = [
    ("input", "Photo in", "One rack photo"),
    ("find", "Find units", "Unit finder model"),
    ("ladder", "Build the ladder", "Clean, fill, number"),
    ("cut", "Cut out units", "One image per unit"),
    ("score", "Score", "6 models, averaged"),
    ("decide", "Decide", "65% rule"),
    ("device", "Device check", "Whole-photo second opinion"),
    ("result", "Result", "Status of every unit"),
    ("devices", "Find devices", "Join units into devices"),
    ("space", "Free space & fit", "Where a device goes"),
]
flow = "".join(f"<a class='fstep' href='#{k}'><span class='fn'>{i}</span><b>{t}</b><small>{s}</small></a>"
               
               for i, (k, t, s) in enumerate(STEPS, 1))
nav = "".join(f"<a href='#{k}'>{i}. {t}</a>" for i, (k, t, _) in enumerate(STEPS, 1))

_NUM = [0]


def step(_i, key, title, lede, body):
    _NUM[0] += 1                      # numbered in the order the steps are added
    i = _NUM[0]
    return (f"<section id='{key}' class='step'><header><span class='num'>{i}</span><div><h2>{title}</h2>"
            f"<p class='lede'>{lede}</p></div></header>{body}</section>")

sections = []
sections.append(step(1, "input", "The photo comes in",
    "Everything starts from one ordinary photo of the front of a rack. Nothing else is needed: no labels, no rack size.",
    f"""<div class='split'><figure class='photo'><img src='{d['img_input']}' alt='The example rack photo'></figure>
    <div class='side'><h3>This example</h3><dl class='facts'>
    <div><dt>File</dt><dd>{esc(d['photo'])}</dd></div><div><dt>Size</dt><dd>{d['width']} × {d['height']} px</dd></div>
    <div><dt>What's in it</dt><dd>A small home-lab rack: switches and patch panels at the top, a power strip (PDU), shelves,
    and two floor-standing APC UPS boxes plus a rack-mount APC UPS at the bottom.</dd></div></dl>
    <div class='note'><b>Why this photo?</b> It has a bit of everything: clear devices, empty space, rows the model
    misses, an unsure unit, and boxes that the image classifier gets wrong but the device check catches.</div>
    <h3>First thing done</h3><p>If the phone saved the photo sideways with a rotation tag (EXIF), the photo is turned upright
    first, so "up" in the picture is "up" in the rack.</p></div></div>"""))

sections.append(step(3, "find", "Find the units",
    "The unit finder model (weights/rack_unit_finder.pt, the same one RT-EOS uses) draws a box around every 1U row it can see, plus the rack frame and its cross rails.",
    f"""<div class='split'><figure class='photo'><img src='{d['img_raw']}' alt='Raw boxes from the unit finder'>
    <figcaption><i class='key' style='--k:var(--det)'></i>unit boxes with the model's confidence &nbsp;
    <i class='key dash' style='--k:var(--frame)'></i>rack frame &nbsp; <i class='key' style='--k:var(--rail)'></i>cross rail</figcaption></figure>
    <div class='side'><h3>What the model found</h3>
    <div class='kpis2'><div><b>{n_raw}</b><span>unit boxes</span></div><div><b>{d['raw']['racks']}</b><span>rack frame</span></div>
    <div><b>{d['raw']['rails']}</b><span>cross rail</span></div><div><b>{pct(d['raw']['conf_mean'])}</b><span>average box confidence</span></div></div>
    <p>Box confidence ranges from {pct(d['raw']['conf_min'])} to {pct(d['raw']['conf_max'])}. That is normal for thin
    strips; a low number does not mean a row is wrong. Rows the model only saw at 25% or more are kept.</p>
    <div class='note warn'><b>Not good enough on its own.</b> The raw boxes overlap, one row is found twice,
    and the bottom of the rack has no boxes at all. Step 4 turns this into a clean list of rows.</div></div></div>"""))

sections.append(step(4, "ladder", "Build the ladder of rows",
    "RT-EOS's ladder rules turn the raw boxes into one clean, gap-free stack of full-width rows, numbered from the bottom. This code is a word-for-word copy of RT-EOS (src/unit_grid.py).",
    f"""<div class='split'><figure class='photo'><img src='{d['img_ladder']}' alt='The final ladder of rows'>
    <figcaption><i class='key' style='--k:var(--det)'></i>row the model found &nbsp; <i class='key dash' style='--k:var(--inf)'></i>row filled in by the rules</figcaption></figure>
    <div class='side'><h3>The rules, in order</h3><div class='tw'><table class='rules'><tr><th>Rule</th><th>What it does</th><th>On this photo</th></tr>{ladder_tbl}</table></div>
    <div class='kpis2'><div><b>{len(rows)}</b><span>rows in the rack</span></div><div><b>{d['units_detected']}</b><span>found by the model</span></div>
    <div><b>{d['units_inferred']}</b><span>filled in by the rules</span></div><div><b>{pct(d['units_detected']/len(rows))}</b><span>detection coverage</span></div></div></div></div>"""))

sections.append(step(5, "cut", "Cut out every unit",
    "Each row is cut out of the photo, with a small margin, so every unit can be judged on its own.",
    f"""<div class='mini-flow'><span class='chip'>Row from the ladder</span>→<span class='chip'>Cut it out of the photo</span>→<span class='chip'>One picture per unit</span>→<span class='chip'>Ready for the models</span></div>
    <div class='strips'>{strip_list}</div>"""))

sections.append(step(6, "score", "Score each unit with six models",
    f"The <b>Ensemble</b> (the best approach, 96.6% right on 1,600 test units) asks six different models to look at each cut-out. Each gives the probability that the unit is occupied; the Ensemble takes their average.",
    f"""<div class='modelrow'>{''.join(f"<div class='mcard'><b>{m['name']}</b><span>{pct(m['accuracy'],1)} on test</span></div>" for m in members)}
    <div class='mcard avg'><b>Average</b><span>= Ensemble</span></div></div>
    <div class='note'><b>Two numbers per unit.</b> P(occupied) and P(unoccupied) always add up to 100%. A clearly empty unit has
    a <b>high</b> P(unoccupied), for example U14: occupied {pct(rows[[r['unit'] for r in rows].index('u14')]['p_occupied'])}, unoccupied
    {pct(1-rows[[r['unit'] for r in rows].index('u14')]['p_occupied'])}.</div>
    <h3>Every model's answer for every unit</h3><p class='muted'>Cells show P(occupied). Red = the model thinks occupied, green = free; the stronger the colour, the surer it is.</p>
    <div class='tw'><table class='heat'><tr><th>Unit</th>{member_head}<th class='n'>Ensemble<br>occupied</th><th class='n'>Ensemble<br>unoccupied</th></tr>{score_rows}</table></div>
    <p class='muted'>Why average? Each model makes different mistakes. On U23, YOLO11s, ResNet50 and Classical lean free while the other three are sure it's occupied; the average still lands on occupied ({pct(rows[1]['p_occupied'])}).</p>"""))

dec_cards = ""
for r, title in ((ex_occ, "Sure it's occupied"), (ex_free, "Sure it's free"), (ex_unc, "Not sure")):
    if r is None:
        continue
    st = r["leaning"] if r["confidence"] >= CUT else "uncertain"
    dec_cards += (f"<div class='card dec'><h4>{title}: {U(r['unit'])}</h4>{strip(r)}{bars(r['p_occupied'])}"
                  f"<p>Bigger probability: <b>{r['leaning']}</b>, {pct(r['confidence'])}. "
                  + (f"That reaches {pct(CUT)}, so the classifier says {pill(st)}" if st != "uncertain"
                     else f"That is below {pct(CUT)}, so it is {pill('uncertain')} and a person should look") + "</p></div>")
sections.append(step(7, "decide", "Turn the probabilities into an answer",
    f"The bigger of the two probabilities wins, but only if it reaches {pct(CUT)}. Otherwise the unit is marked uncertain instead of guessing.",
    f"""<div class='decision'><div class='dz f'>P(unoccupied) ≥ {pct(CUT)}<b>Unoccupied</b></div>
    <div class='dz u'>neither reaches {pct(CUT)}<b>Uncertain</b></div><div class='dz o'>P(occupied) ≥ {pct(CUT)}<b>Occupied</b></div></div>
    <p class='muted'>The black line on each bar below marks {pct(CUT)}.</p><div class='cards3'>{dec_cards}</div>"""))

sections.append(step(8, "device", "Get a second opinion from the device finder",
    "A second model, the RT-EOS device finder, looks at the whole photo at once and names devices (switch, patch panel, PDU, UPS …) and empty slots. It sees things a single cut-out cannot, like a big box that spans many rows.",
    f"""<div class='split'><figure class='photo'><img src='{d['img_devices']}' alt='Device finder boxes'>
    <figcaption><i class='key' style='--k:var(--dev)'></i>device it trusts (≥ 40% sure) &nbsp; <i class='key' style='--k:var(--free)'></i>empty slot &nbsp; <i class='key dash' style='--k:var(--muted)'></i>too unsure, ignored</figcaption></figure>
    <div class='side'><h3>How its answer is used</h3><ol class='steps-list'>
    <li>Keep only devices it is at least <b>40%</b> sure of ({len(trusted)} of {len(d['devices'])} here).</li>
    <li>A device counts for a row if it covers at least <b>half the row's height</b>.</li>
    <li>An <b>empty slot</b> or <b>closed (blank) panel</b> seen by the device finder always counts as unoccupied.</li>
    <li>Compare with the classifier: <b>same answer</b> → confirmed. <b>Classifier says free but the device finder sees a device</b>
    → the unit is marked <b>uncertain</b>, with the device named, so a person checks it. Neither model overrules the other.</li></ol>
    <div class='outcomes'><div><b>{len(agreed)}</b><span>both agree</span></div><div class='hl'><b>{len(flagged)}</b><span>disagree → uncertain, check by eye</span></div><div><b>{len(alone)}</b><span>no device seen, classifier alone</span></div></div>
    <div class='note'><b>What happened here.</b> The two APC UPS boxes stand on the floor of the rack. Cut into thin rows,
    each row just looks like a dark opening, so all six models said free. The device finder sees the whole UPS, so these
    {len(flagged)} rows are flagged for a person instead of being called free.</div></div></div>
    <h3>The rows it flagged</h3><div class='tw'><table><tr><th>Unit</th><th>Classifier said</th><th>Device finder saw</th><th>Result</th></tr>{over_tbl}</table></div>
    {('<h3>Example</h3><div class=card>' + strip(ex_over, f"{U(ex_over['unit'])}: classifier {pct(ex_over['confidence'])} sure it is free; device finder sees a {esc(ex_over['device'])} ({pct(ex_over['device_conf'])}) → uncertain") + '</div>') if ex_over else ''}
    <p class='muted'>This is teammate 1's version 2 rule, now used by every approach: when the two models disagree about a device, a person decides. It is safer than letting either model win, at the cost of more units to check.</p>"""))

sections.append(step(9, "result", "The result",
    "Every row now has one of three answers and a reason. This is what the app shows.",
    f"""<div class='kpis4'><div><b>{S['total']}</b><span>rack units</span></div><div class='o'><b>{S['occupied']}</b><span>occupied · {S['occupancy_pct']:.0f}%</span></div>
    <div class='f'><b>{S['free']}</b><span>free · {S['available_pct']:.0f}%</span></div><div class='u'><b>{S['uncertain']}</b><span>uncertain, check by eye</span></div></div>
    <div class='split'><figure class='photo'><img src='{d['img_final']}' alt='Final result on the photo'>
    <figcaption><i class='key' style='--k:var(--occ)'></i>occupied <i class='key' style='--k:var(--free)'></i>unoccupied <i class='key' style='--k:var(--unc)'></i>uncertain</figcaption></figure>
    <div class='side'><h3>Rack at a glance</h3><p class='muted'>Top of the rack first.</p>{rack_strip()}</div></div>
    <h3>Every unit</h3><div class='tw'><table class='final'><tr><th>Unit</th><th>Cut-out</th><th>Status</th><th class='n'>P(occ.)</th><th class='n'>P(unocc.)</th><th>Decided by</th><th>Why</th></tr>{final_tbl}</table></div>"""))

G = d.get("groups")
DEV_PILL = {"confirmed": "<span class='pill occupied'>Confirmed</span>", "check": "<span class='pill uncertain'>Check</span>"}
if G:
    dev_rows = "".join(
        f"<tr><th>D{x['id']}</th><td>{esc(x['range_label'])}</td><td class='n'>{x['height_u']} U</td>"
        f"<td>{esc((x['device_seen'] or '-').split(' (')[0])}</td><td>{DEV_PILL[x['status']]}</td></tr>"
        for x in G["devices"])
    joined = [p for p in G["pairs"] if p["joined"]]
    split = [p for p in G["pairs"] if not p["joined"] and not p["why"].startswith("not")]
    ex_j = max(joined, key=lambda p: p["p_same"]) if joined else None
    ex_s = min(split, key=lambda p: p["p_same"]) if split else None
    sections.append(step(10, "devices", "Group the units into devices",
        f"A 4U server fills four units but is one device. Step 3 decides, for every two units on top of each other, whether they belong to the same device.",
        f"""<div class='split'><figure class='photo'><img src='{d['img_devices_grouped']}' alt='Devices found on the rack'>
        <figcaption><i class='key' style='--k:var(--ink)'></i>device, every unit occupied &nbsp; <i class='key dash' style='--k:var(--unc)'></i>device to check (has uncertain units)</figcaption></figure>
        <div class='side'><h3>How two units are compared</h3><ol class='steps-list'>
        <li><b>Is there a dividing line?</b> The seam between the two units is measured: a sharp horizontal edge usually means two separate boxes.</li>
        <li><b>Do they look alike?</b> Texture, colour and a CLIP description of each unit are compared.</li>
        <li><b>Do they look like one box?</b> CLIP also looks at the join itself and at both units together.</li>
        <li>Geethika's AI model turns all of this into <b>P(same device)</b>. Two neighbours are joined when both are taken (occupied or uncertain) and it reaches <b>{pct(G['same_thr'])}</b>.</li></ol>
        <div class='outcomes'><div><b>{len(G['devices'])}</b><span>devices found</span></div><div><b>{len(joined)}</b><span>pairs joined</span></div><div><b>{len(split)}</b><span>pairs kept apart</span></div></div>
        {f"<div class='note'><b>Example.</b> {U(ex_j['upper'])} and {U(ex_j['lower'])}: {pct(ex_j['p_same'])} sure they are one device, so they are joined." + (f" {U(ex_s['upper'])} and {U(ex_s['lower'])}: only {pct(ex_s['p_same'])}, so they stay separate devices." if ex_s else "") + "</div>" if ex_j else ""}
        </div></div>
        <h3>The devices</h3><div class='tw'><table><tr><th>Device</th><th>Units</th><th class='n'>Height</th><th>Device finder saw</th><th>Status</th></tr>{dev_rows}</table></div>"""))
sections.append(step(11 if G else 10, "space", "Free space and fitting a device",
    "Free rows next to each other form a block. A device that needs N units fits in any block of N or more. Uncertain rows are never counted as free.",
    f"""<div class='split'><figure class='photo'>{f"<img src='{d['img_fit']}' alt='Suggested position for the device'>" if d.get('img_fit') else ''}
    <figcaption><i class='key' style='--k:var(--pick)'></i>suggested position for a {esc(fit['device'])}</figcaption></figure>
    <div class='side'><h3>Free blocks</h3><ul class='blocks'>{free_list}</ul>
    <p class='muted'>Largest block: {S['largest_free_block']} U.</p>
    <h3>Example: fit a {esc(fit['device'])}</h3><ol class='steps-list'>
    <li>Look up its height: <b>{fit['units']} U</b> (from the built-in catalogue; unknown models go to the RackTrack specs agents).</li>
    <li>Find every free block of at least {fit['units']} U.</li><li>Rank them: heavy gear low in the rack, blocks it fills exactly first.</li></ol>
    <ul class='suggest'>{fit_list}</ul>{rack_strip(fit=fit_units)}</div></div>"""))

def _split(r):
    return ", ".join(m["name"] + " " + pct(r["members"][m["key"]]) for m in members)


unc_items = "".join(f"<li><b>{U(r['unit'])} is uncertain</b>: the models split ({_split(r)}), averaging "
                    f"{pct(r['p_occupied'])} occupied. A person should look.</li>" for r in unc)
watch = f"""<section id='notes' class='step plain'><header><span class='num'>!</span><div><h2>What to keep in mind on this photo</h2></div></header>
<ul class='watch'>
<li><b>{', '.join(U(r['unit']) for r in inferred)} were filled in</b>, not seen by the model. The frame continues below the last row, so the rules added them. Worth a glance.</li>
{unc_items}
<li><b>The floor-standing UPS boxes</b> are not rack-mounted, but they really take up the space. Those {len(flagged)} rows are uncertain, so they are never offered as free space; a person confirms them.</li>
<li>The whole run took about {d['seconds']:.0f} seconds on this Mac, most of it loading the six models.</li></ul></section>"""

PAGE = f"""<title>Rack Analysis Walkthrough</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: 80%-wide reading column; sticky step bar; each step = numbered section with picture left, explanation right */
:root{{
  --bg:#f5f6f8;--panel:#fff;--ink:#18202a;--ink2:#465263;--muted:#7a8595;--line:#e0e4ea;--accent:#3d5afe;
  --occ:#d1453b;--free:#24935a;--unc:#d48b0b;--det:#2563eb;--inf:#db2777;--dev:#9333ea;--frame:#7c3aed;--rail:#ea580c;--pick:#2563eb;
  --occ-rgb:209,69,59;--free-rgb:36,147,90;
  --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}}
@media (prefers-color-scheme: dark){{:root:not([data-theme="light"]){{--bg:#11151b;--panel:#191f28;--ink:#e6eaf0;--ink2:#b2bccb;--muted:#8691a2;--line:#2a323e;--accent:#8c9eff;
  --occ:#ef6b61;--free:#46c27f;--unc:#f0b445;--det:#60a5fa;--inf:#f472b6;--dev:#c084fc;--frame:#a78bfa;--rail:#fb923c;--pick:#60a5fa;--occ-rgb:239,107,97;--free-rgb:70,194,127;color-scheme:dark}}}}
:root[data-theme="dark"]{{--bg:#11151b;--panel:#191f28;--ink:#e6eaf0;--ink2:#b2bccb;--muted:#8691a2;--line:#2a323e;--accent:#8c9eff;
  --occ:#ef6b61;--free:#46c27f;--unc:#f0b445;--det:#60a5fa;--inf:#f472b6;--dev:#c084fc;--frame:#a78bfa;--rail:#fb923c;--pick:#60a5fa;--occ-rgb:239,107,97;--free-rgb:70,194,127;color-scheme:dark}}
*{{box-sizing:border-box}}
body{{background:var(--bg);color:var(--ink);font:15px/1.6 var(--sans);padding-inline:16px}}
.wrap{{max-width:80%;margin:0 auto;padding-block:28px 80px}}
@media (max-width:1000px){{.wrap{{max-width:100%}}}}
h1{{font-size:30px;line-height:1.2;margin:0 0 8px;text-wrap:balance}}
h2{{font-size:22px;margin:0;text-wrap:balance}} h3{{font-size:16px;margin:18px 0 8px}} h4{{margin:0 0 8px;font-size:15px}}
p{{margin:0 0 10px;max-width:72ch}} .muted{{color:var(--muted)}} .n{{text-align:right;font-variant-numeric:tabular-nums}}
.intro{{color:var(--ink2);font-size:16.5px;max-width:75ch}}
nav.steps{{position:sticky;top:env(safe-area-inset-top,0px);z-index:9;background:var(--bg);border-bottom:1px solid var(--line);display:flex;gap:2px;overflow-x:auto;margin:18px 0 26px;padding:6px 0}}
nav.steps a{{white-space:nowrap;color:var(--ink2);text-decoration:none;font-size:13.5px;font-weight:500;padding:7px 11px;border-radius:7px}}
nav.steps a:hover,nav.steps a:focus-visible{{background:var(--panel);color:var(--accent);outline:none}}
.flow{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}}
.fstep{{min-width:0;display:flex;flex-direction:column;gap:2px;padding:12px;border-radius:10px;border:1px solid var(--line);text-decoration:none;color:var(--ink)}}
.fstep:hover,.fstep:focus-visible{{border-color:var(--accent);outline:none}}
.fstep b{{font-size:14px}} .fstep small{{color:var(--muted);font-size:12px;line-height:1.35}}
.fn{{width:24px;height:24px;border-radius:50%;background:var(--accent);color:#fff;font:600 12px var(--mono);display:grid;place-items:center;margin-bottom:4px}}
@media (max-width:900px){{.flow{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.step{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:24px 26px;margin-top:22px;scroll-margin-top:64px}}
.step>header{{display:flex;gap:16px;align-items:flex-start;margin-bottom:18px}}
.num{{flex:none;width:40px;height:40px;border-radius:10px;background:var(--accent);color:#fff;font:600 18px var(--mono);display:grid;place-items:center}}
.lede{{color:var(--ink2);margin:4px 0 0}}
.split{{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,6fr);gap:26px;align-items:start}}
@media (max-width:900px){{.split{{grid-template-columns:1fr}}}}
.photo{{margin:0}} .photo img{{width:100%;border-radius:10px;border:1px solid var(--line);display:block}}
.photo figcaption{{font-size:12.5px;color:var(--muted);margin-top:8px;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center}}
.key{{display:inline-block;width:18px;height:10px;border:3px solid var(--k);border-radius:2px;vertical-align:-1px;margin-right:4px}} .key.dash{{border-style:dashed}}
.side{{min-width:0}}
.facts{{margin:0;display:grid;gap:8px}} .facts div{{display:grid;grid-template-columns:110px 1fr;gap:10px}} .facts dt{{color:var(--muted);font-size:13px}} .facts dd{{margin:0}}
.note{{border-left:4px solid var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent);padding:10px 14px;border-radius:6px;margin:14px 0;font-size:14.5px}}
.note.warn{{border-color:var(--unc);background:color-mix(in srgb,var(--unc) 10%,transparent)}}
.cards3{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:16px}}
.card{{border:1px solid var(--line);border-radius:10px;padding:14px 16px;min-width:0}}
table{{border-collapse:collapse;width:100%;font-size:14px}} th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:middle}}
th{{font-size:12.5px;color:var(--ink2);font-weight:600}} .tw{{overflow-x:auto}}
table.mini td,table.mini th{{padding:5px 6px;font-size:13px}}
table.rules td:last-child{{color:var(--ink);font-weight:500}} table.rules td:nth-child(2){{color:var(--ink2)}}
.kpis2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin:12px 0}}
.kpis2 div,.outcomes div,.kpis4 div{{border:1px solid var(--line);border-radius:10px;padding:10px 12px}}
.kpis2 b,.outcomes b,.kpis4 b{{display:block;font:500 24px var(--mono)}} .kpis2 span,.outcomes span,.kpis4 span{{font-size:12.5px;color:var(--ink2)}}
.outcomes{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}} .outcomes .hl{{border-color:var(--dev)}} .outcomes .hl b{{color:var(--dev)}}
.kpis4{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:20px}}
.kpis4 .o b{{color:var(--occ)}} .kpis4 .f b{{color:var(--free)}} .kpis4 .u b{{color:var(--unc)}}
.mini-flow{{display:flex;flex-wrap:wrap;gap:8px;align-items:center;color:var(--muted);margin-bottom:10px}}
.chip{{border:1px solid var(--line);border-radius:999px;padding:4px 12px;color:var(--ink);font-size:13.5px;background:var(--bg)}}
.strips{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px 16px}}
.strips figure{{margin:0;display:grid;grid-template-columns:64px 1fr;gap:8px;align-items:center}}
.strips figcaption{{font:500 12px var(--mono);color:var(--muted)}}
.strips img{{width:100%;border-radius:5px;border:1px solid var(--line);display:block}}
.cropgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px}}
.cropgrid figure{{margin:0}} .cropgrid img{{width:100%;aspect-ratio:1;border-radius:6px;border:1px solid var(--line);display:block}}
.cropgrid figcaption{{font:500 11.5px var(--mono);color:var(--muted);text-align:center;margin-top:3px}}
.modelrow{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}}
.mcard{{border:1px solid var(--line);border-radius:9px;padding:8px 12px;display:flex;flex-direction:column}} .mcard span{{font-size:12px;color:var(--muted)}}
.mcard.avg{{border-color:var(--accent)}} .mcard.avg b{{color:var(--accent)}}
table.heat td,table.heat th{{padding:5px 8px;font-size:13px}} table.heat th small{{display:block;font-weight:400;color:var(--muted)}}
.strong{{font-weight:600}}
.pill{{display:inline-block;font-size:12px;font-weight:600;padding:1px 9px;border-radius:999px;border:1.5px solid currentColor;white-space:nowrap}}
.pill.occupied{{color:var(--occ)}} .pill.unoccupied{{color:var(--free)}} .pill.uncertain{{color:var(--unc)}}
.decision{{display:grid;grid-template-columns:1fr 1fr 1fr;border-radius:10px;overflow:hidden;margin-bottom:6px;gap:3px}}
.dz{{padding:10px 12px;font-size:12.5px;color:#fff}} .dz b{{display:block;font-size:16px}}
.dz.f{{background:var(--free)}} .dz.u{{background:var(--unc)}} .dz.o{{background:var(--occ)}}
.dec p{{font-size:14px;margin-top:8px}}
.strip{{margin:0 0 8px}} .strip img{{width:100%;border-radius:6px;border:1px solid var(--line);display:block}} .strip figcaption{{font-size:13px;color:var(--ink2);margin-top:6px}}
.pb{{display:grid;grid-template-columns:86px 1fr 44px;gap:8px;align-items:center;font-size:13px;margin:4px 0}}
.pt{{position:relative;height:12px;border-radius:6px;background:color-mix(in srgb,var(--muted) 22%,transparent)}}
.pt i{{position:absolute;inset:0 auto 0 0;border-radius:6px}} .pt i.o{{background:var(--occ)}} .pt i.f{{background:var(--free)}}
.pt b{{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink)}} .pb em{{font-style:normal;font-weight:600;text-align:right;font-variant-numeric:tabular-nums}}
.steps-list{{padding-left:20px;margin:0 0 10px}} .steps-list li{{margin:5px 0}}
.rackcol{{display:grid;gap:2px;max-width:260px;margin-top:8px}}
.rc{{height:20px;border-radius:3px;display:flex;align-items:center;padding-left:8px;font:500 11px var(--mono);color:#fff}}
.rc.occupied{{background:var(--occ)}} .rc.unoccupied{{background:var(--free)}} .rc.uncertain{{background:var(--unc)}}
.rc.fit{{background:var(--pick);outline:2px solid var(--pick);outline-offset:1px}}
table.final img.thumb{{height:30px;width:auto;max-width:170px;border-radius:4px;display:block}}
table.final td.why{{color:var(--ink2);font-size:13px;white-space:normal;min-width:220px}}
table.final tr.st-uncertain{{background:color-mix(in srgb,var(--unc) 10%,transparent)}}
.blocks{{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px}}
.blocks li{{border:1.5px solid var(--free);border-radius:999px;padding:3px 12px;display:flex;gap:8px;font-size:14px}} .blocks span{{color:var(--free);font-weight:600}}
.suggest{{padding-left:18px;font-size:14px}} .suggest li{{margin:4px 0}}
.watch li{{margin:8px 0;max-width:80ch}}
.step.plain{{background:transparent}}
footer{{margin-top:30px;color:var(--muted);font-size:13px}}
</style>
<div class="wrap">
<header>
<p class="muted" style="margin:0 0 6px;font:500 12px var(--mono);letter-spacing:.06em;text-transform:uppercase">RackTrack · rack occupancy</p>
<h1>From one rack photo to units, occupancy and devices</h1>
<p class="intro">A step-by-step walk through everything the system does with a photo, using one real rack as the example.
Each step shows the picture at that stage, the rule that is applied, and the numbers for this photo.</p>
</header>
<div class="flow">{flow}</div>
<nav class="steps" aria-label="Steps">{nav}<a href="#notes">Keep in mind</a></nav>
{''.join(sections)}
{watch}
<footer>Example: {esc(d['photo'])} · approach: Ensemble of six models (v2 dataset) · cut-off {pct(CUT)} · device finder trusted at ≥ 40%, covering ≥ 50% of a row.</footer>
</div>"""
if "--standalone" in sys.argv:
    PAGE = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            + PAGE.replace("<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">", "") + "</html>")
(D / "index.html").write_text(PAGE)
print("written", D / "index.html", len(PAGE) // 1024, "KB")
