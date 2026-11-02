"""Build the No Label Review page from the strict list (scripts/devices/nolabel_strict.py -> results/devices/nolabel_strict.json):
places where both device detectors see a device, on the rack's unit ladder, with no label polygon on it in the dataset's label file, in the
photos of 1 MP or more. A reviewer confirms the device is there and gives its class.

    python3 scripts/devices/build_nolabel_review.py      # -> reports/devices/nolabel_review/index.html (one file, pictures inside)
"""
import base64, collections, io, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "devices")); sys.path.insert(0, str(ROOT / "src"))
import audit_labels as AL                                    # noqa: E402
from PIL import Image, ImageDraw, ImageOps                   # noqa: E402

OUT = ROOT / "reports" / "devices" / "nolabel_review"      # one folder per reviewer: nolabel_review_1/, nolabel_review_2/
PARTS = 2                                                   # reviewers (the user, 2026-10-21)
PURPLE, GREEN = (200, 40, 170), (26, 158, 110)
NAMES = ["Closed Unit", "Empty", "Firewall", "Gateway", "Load Balancer", "PDU", "Patch Panel", "Router", "Server", "Storage unit", "Switch", "UPS"]


def jpg(img, q=80):
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=q)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    items = []
    recs = {name: (path, lbl, w, h) for sp in AL.SPLITS for name, path, lbl, w, h in AL.photos(sp)}
    S = json.load(open(ROOT / "results" / "devices" / "nolabel_strict.json")); strict = S["kept"]
    by_photo = {}
    for r in strict:
        by_photo.setdefault(r["photo"], []).append(r)
    owner, load = {}, [0] * PARTS                                         # whole photos per reviewer, balanced by places
    for ph, rs in sorted(by_photo.items(), key=lambda t: (-len(t[1]), t[0])):
        k = load.index(min(load)); owner[ph] = k; load[k] += len(rs)
    outs = [OUT.parent / f"{OUT.name}_{k + 1}" for k in range(PARTS)]
    for o in outs:
        (o / "img").mkdir(parents=True, exist_ok=True)
        for f_ in (o / "img").glob("*.jpg"):
            f_.unlink()
    RACK_H, CW, CH = 520, 520, 300
    for pi, (ph, recs_) in enumerate(sorted(by_photo.items())):
        path, lbl, w, h = recs[ph]; img_dir = outs[owner[ph]] / "img"
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB"); shapes = AL.load_shapes(lbl, w, h)
        sc = min(RACK_H / img.height, 420 / img.width, 2.0); rack = img.resize((max(1, round(img.width * sc)), max(1, round(img.height * sc))), Image.LANCZOS)
        d = ImageDraw.Draw(rack)
        for pts, _ in shapes:                                              # every label in the label file, crisp green
            if len(pts) >= 2:
                d.line([(x * sc, y * sc) for x, y in pts + [pts[0]]], fill=GREEN, width=2)
        for r in recs_:                                                    # every device with no label in this photo, thin purple
            b = r["box"]; d.rectangle([b[0] * sc - 2, b[1] * sc - 2, b[2] * sc + 2, b[3] * sc + 2], outline=PURPLE, width=2)
        rname = f"r{pi:04d}.jpg"; rack.save(img_dir / rname, quality=85)
        for j, r in enumerate(recs_):
            b = r["box"]; bw, bh = b[2] - b[0], b[3] - b[1]
            x0, y0 = max(0, b[0] - .12 * bw), max(0, b[1] - 1.0 * bh); x1, y1 = min(w, b[2] + .12 * bw), min(h, b[3] + 1.0 * bh)
            crop = img.crop((int(x0), int(y0), int(x1), int(y1)))           # the photo itself, no drawing under the device
            k = min(CW / crop.width, CH / crop.height, 2.0)                  # never enlarge more than twice: no blur
            crop = crop.resize((max(1, round(crop.width * k)), max(1, round(crop.height * k))), Image.LANCZOS)
            cd = ImageDraw.Draw(crop); g = 4
            cd.rectangle([(b[0] - x0) * k - g, (b[1] - y0) * k - g, (b[2] - x0) * k + g, (b[3] - y0) * k + g], outline=PURPLE, width=2)
            cname = f"c{pi:04d}_{j}.jpg"; crop.save(img_dir / cname, quality=88)
            items.append({"id": f"{pathlib.Path(ph).stem}__N{j}"[:180], "photo": ph, "rack": f"img/{rname}", "crop": f"img/{cname}",
                          "hl": [round(b[0] / w * 100, 2), round(b[1] / h * 100, 2), round(bw / w * 100, 2), round(bh / h * 100, 2)],
                          "ev": r["evidence"], "part": owner[ph]})
        if pi % 200 == 0:
            print(f"pictures {pi}/{len(by_photo)}", flush=True)
    for k, o in enumerate(outs):
        its = [i for i in items if i["part"] == k]
        page = TEMPLATE.replace("__ITEMS__", json.dumps(its)).replace("__N__", f"{len(its):,}").replace("__NPH__", f"{len({i['photo'] for i in its}):,}")
        page = page.replace("__NAMES__", json.dumps([n for n in NAMES if n not in ("Closed Unit", "Empty")] + ["Other"])).replace("__PROOF__", "")
        page = page.replace("__PART__", f"{k + 1}").replace("__PARTS__", f"{PARTS}")
        (o / "index.html").write_text(page)
        print(f"written {o / 'index.html'}: {len(its)} places in {len({i['photo'] for i in its})} photos, {len(page) / 1e6:.1f} MB")


def proof_html(S):
    """The overall proof that these places carry no label, from results/devices/nolabel_strict.json."""
    import collections, html
    K = S["kept"]; st = S["stages"]; top = max(n for _, n in st) or 1
    funnel = "".join(f'<div class="fr"><span class="fl">{html.escape(t)}</span><div class="ft"><div class="fb" style="width:{100 * n / top:.1f}%"></div><b>{n:,}</b></div></div>' for t, n in st)
    mx_o = max((k["polygon_cover"] for k in K), default=0); mx_e = max((k["export_polygon_cover"] for k in K), default=0)
    zero = sum(k["polygon_cover"] == 0 for k in K); hi = sum(k["megapixels"] >= 1 for k in K)
    evc = collections.Counter(k["evidence"] for k in K)
    tiles = [(f"{evc['both sure']:,}", "both detectors sure (device finder at least 80 % on the same spot)"),
             (f"{evc['both see it']:,}", "both detectors see it (device finder 50 % to 80 %)"),
             (f"{evc['one detector']:,}", "one detector only (the device finder is unsure or silent)"),
             (f"{mx_o:.1%}", "the most any listed device is covered by a label polygon, in our label file"),
             (f"{mx_e:.1%}", "the same, in the original export label file"),
             (f"{zero:,} of {len(K):,}", "not touched by any label polygon at all")]
    tl = "".join(f'<div class="pt"><b>{a}</b><span>{html.escape(b)}</span></div>' for a, b in tiles)
    ex = collections.Counter(k["export"] for k in K)
    short = lambda n: n.replace("Device annotation_", "").replace("Device_annotation_", "").replace("Device_Annotation_", "").replace("Not_trained_", "").replace("not_trained_", "").replace("Not_traned_", "")
    emax = max(ex.values() or [1])
    exb = "".join(f'<div class="fr"><span class="fl">{html.escape(short(e))}</span><div class="ft"><div class="fb" style="width:{100 * n / emax:.1f}%"></div><b>{n:,}</b></div></div>' for e, n in ex.most_common())
    R = S["rules"]
    lab_files = [f for f in (AL.DATA / "labels").glob("*.txt") if not f.name.startswith("._")]
    n_poly = sum(sum(1 for l in f.read_text().split("\n") if len(l.split()) > 5) for f in lab_files)
    n_ph = sum(1 for f in (AL.DATA / "images").iterdir() if not f.name.startswith("._"))
    ds = [(f"{n_ph:,}", "photos in the merged dataset"), (f"{n_poly:,}", "label polygons in the label files"),
          (f"{len(K):,}", f"devices with no label, to check (about {len(K) / max(1, n_poly):.1%} on top of the labels)")]
    dst = "".join(f'<div class="pt big"><b>{a}</b><span>{html.escape(b)}</span></div>' for a, b in ds)
    funnel_c = "".join(f'<div class="fr"><span class="fl">{html.escape(t)}</span><div class="ft"><div class="fb" style="width:{100 * n / top:.1f}%"></div><b>{n:,}</b></div></div>' for t, n in st)
    return f"""<section class="sum card">
<div class="ptiles three">{dst}</div>
<div class="howc"><p class="muted">How the {len(K):,} were found, over every photo ({S['photos']:,} readable): a device is listed when it passes every test (bars to scale).</p>{funnel_c}</div>
<details><summary>Proof details: the tests, evidence, coverage, exports</summary>
<ol class="tests">
<li><b>Device detector.</b> The DEIMv2 device detector of the logic + model approach finds a device, score at least {R['deim_score']:.0%}, one box per device.</li>
<li><b>On the rack.</b> It covers at least {R['row_cover']:.0%} of a unit on our unit ladder and lies at least half inside the rack, so other racks, monitors and the floor drop out.</li>
<li><b>No label polygon.</b> Label polygons, not boxes, cover less than {R['polygon_max']:.0%} of it, in the dataset's label file and in the original export label file.</li>
<li><b>No label nearby.</b> No label of any kind, polygon or box, any class, in either file, covers {R.get('box_near', .3):.0%} of it or overlaps it by {R.get('box_near', .3):.0%}. A label drawn slanted or shifted never counts as missing.</li>
<li><b>Evidence.</b> Our RT-EOS device finder is a second opinion: both sure, both see it, or one detector only.</li></ol>
<div class="ptiles">{tl}</div>
<p class="muted" style="margin:10px 0 4px">By export ({hi:,} of the {len(K):,} are in photos of 1 MP or more)</p>{exb}
</details></section>"""


TEMPLATE = r"""<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>No Label Review __PART__</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* layout: a grid of cards, rack on the left and the unlabelled area on the right, 90 % of the window wide */
:root { --bg:#f2f4f7; --panel:#ffffff; --surface:#f6f7f9; --fg:#151a22; --muted:#596273; --line:#e2e6ec; --accent:#2252c9; --accent-soft:#e8eefb; --purple:#b5249a;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 6px 18px rgba(16,24,40,.07); --sans:"IBM Plex Sans","Segoe UI",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#11151b; --panel:#1a2029; --surface:#212833; --fg:#e8ecf2; --muted:#9aa4b4; --line:#2c3441; --accent:#7ea2ff; --accent-soft:#1f2b46; --purple:#e070cc;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35); color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#11151b; --panel:#1a2029; --surface:#212833; --fg:#e8ecf2; --muted:#9aa4b4; --line:#2c3441; --accent:#7ea2ff; --accent-soft:#1f2b46; --purple:#e070cc;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35); color-scheme:dark; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font:15px/1.5 var(--sans); }
.wrap { width:90vw; margin:0 auto; padding-block:26px 60px; display:grid; gap:16px; } @media (max-width:700px) { .wrap { width:auto; padding-inline:16px; } }
h1 { font-size:1.7rem; margin:0; } p { margin:4px 0; max-width:80ch; } .muted { color:var(--muted); }
.top { display:flex; flex-wrap:wrap; gap:10px 20px; align-items:end; justify-content:space-between; }
.stats { display:flex; flex-wrap:wrap; gap:8px; } .stats div { background:var(--panel); box-shadow:var(--shadow); border-radius:10px; padding:7px 12px; display:flex; gap:6px; align-items:baseline; }
.stats b { font-size:1.05rem; font-variant-numeric:tabular-nums; } .stats span { font-size:.82rem; color:var(--muted); }
.legend { font-size:.88rem; color:var(--muted); } .k { display:inline-block; width:14px; height:10px; border:3px solid; border-radius:2px; margin:0 5px 0 0; vertical-align:-1px; } .k.p { border-color:var(--purple); } .k.g { border-color:#1a9e6e; }
.bar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.switch { display:inline-flex; flex-wrap:wrap; background:var(--panel); box-shadow:var(--shadow); border-radius:10px; padding:4px; gap:2px; }
.switch button, .btn { font:500 .9rem var(--sans); border:0; background:transparent; color:var(--fg); padding:7px 13px; border-radius:7px; cursor:pointer; }
.switch button[aria-pressed="true"] { background:var(--accent); color:#fff; } .btn.primary { background:var(--accent); color:#fff; box-shadow:var(--shadow); }
button:focus-visible, select:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.banner { padding:9px 14px; border-radius:10px; background:var(--accent-soft); font-size:.9rem; }
.list { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,560px),1fr)); gap:14px; }
.card { background:var(--panel); border-radius:12px; box-shadow:var(--shadow); padding:12px; display:grid; gap:9px; min-width:0; align-content:start; border:2px solid transparent; } .card.marked { border-color:var(--accent); }
.pics { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,2fr); gap:8px; align-items:center; background:var(--surface); border-radius:8px; padding:6px; }
.pics img { width:100%; max-height:300px; object-fit:contain; display:block; border-radius:5px; }
.q { font-weight:600; } .mono { font:.74rem var(--mono); color:var(--muted); overflow-wrap:anywhere; }
.btns { display:flex; flex-wrap:wrap; gap:8px; align-items:center; } .rb { font:500 .86rem var(--sans); border:1px solid var(--line); background:var(--surface); color:var(--fg); padding:7px 12px; border-radius:8px; cursor:pointer; }
.rb[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
select, textarea { font:.88rem var(--sans); border:1px solid var(--line); border-radius:8px; padding:7px 9px; background:var(--panel); color:var(--fg); }
textarea { width:100%; min-height:40px; resize:vertical; } .more { display:flex; justify-content:center; grid-column:1/-1; }
.wrap > *, .list > *, .card > *, .top > * { min-width:0; } body { margin:0; }
.tabs { position:sticky; top:env(safe-area-inset-top,0px); z-index:5; display:flex; gap:4px; background:var(--bg); padding:8px 0; border-bottom:1px solid var(--line); overflow-x:auto; }
.tabs button { font:600 .95rem var(--sans); border:0; background:transparent; color:var(--muted); padding:8px 14px; border-bottom:3px solid transparent; cursor:pointer; }
.tabs button[aria-pressed="true"] { color:var(--fg); border-bottom-color:var(--accent); }
.sum { padding:14px 16px; display:grid; gap:12px; } .sum details summary { cursor:pointer; color:var(--accent); font-weight:600; font-size:.9rem; }
.sum .pt { box-shadow:none; background:var(--surface); padding:10px 14px; } .howc .fr { margin:4px 0; } .howc p { margin:0 0 4px; font-size:.86rem; }
.proof { display:grid; gap:16px; } .pc { padding:16px 18px; align-content:start; } .pc h2 { font-size:1.05rem; margin:0 0 6px; }
.fr { display:grid; grid-template-columns:minmax(0,1.3fr) minmax(0,2fr); gap:12px; align-items:center; margin:8px 0; font-size:.9rem; }
.ft { display:flex; align-items:center; gap:8px; } .fb { height:16px; border-radius:4px; background:var(--purple); min-width:3px; } .ft b { font-variant-numeric:tabular-nums; }
.ptiles.three { grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr)); } .pt.big b { font-size:2rem; color:var(--accent); }
.ptiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr)); gap:12px; }
.pt { background:var(--panel); border-radius:12px; box-shadow:var(--shadow); padding:14px 16px; } .pt b { display:block; font-size:1.5rem; } .pt span { font-size:.84rem; color:var(--muted); }
.grid2 { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); gap:16px; }
.tests { margin:6px 0 0; padding-left:20px; font-size:.9rem; } .tests li { margin:6px 0; } .note { margin:0; }
@media (max-width:700px) { .fr { grid-template-columns:1fr; gap:2px; } }
.rk { position:relative; justify-self:center; } .rk img { max-height:none; } .hl { position:absolute; outline:3px solid #ff2bd6; box-shadow:0 0 0 9999px rgba(0,0,0,.28); border-radius:2px; pointer-events:none; }
.pics { grid-template-columns:minmax(0,1fr) minmax(0,1.6fr); } .pics img { max-height:340px; }
.ev { font-size:.78rem; font-weight:600; border-radius:999px; padding:2px 10px; justify-self:start; } .ev-bothsure { background:#e5f5ec; color:#14703f; } .ev-bothseeit { background:#fdf3d7; color:#8a6200; } .ev-onedetector { background:#eef0f4; color:#525a67; }
.rk { overflow:hidden; border-radius:5px; }
input.other { font:.88rem var(--sans); border:1px solid var(--line); border-radius:8px; padding:7px 9px; background:var(--panel); color:var(--fg); min-width:200px; flex:1 1 200px; }
h1 .part { display:block; font-size:.9rem; font-weight:600; color:var(--accent); margin-top:4px; }
</style>
<div class="wrap">
<div class="top"><div><h1>No Label Review <span class="part">Reviewer __PART__ of __PARTS__ &middot; __N__ places in __NPH__ photos</span></h1><p class="muted">In each purple box a rack device seems to have no label in the dataset's label file. Say whether a device is there and, if so, pick its class.</p></div>
<div class="stats"><div><b id="n-done">0</b><span>marked</span></div></div></div>
__PROOF__
<div id="revhead"><p class="legend"><i class="k g"></i>every label in the label file <i class="k p" style="margin-left:12px"></i>devices with no label; the one to check is lit up on the rack and shown up close on the right</p>
<div class="bar"><div class="switch" role="group" aria-label="Show"><button type="button" data-f="all" aria-pressed="true">All</button><button type="button" data-f="open" aria-pressed="false">Unchecked</button><button type="button" data-f="done" aria-pressed="false">Checked</button></div>
<button type="button" class="btn primary" id="dl">Download my marks (CSV)</button></div>
</div>
<div id="banner" class="banner" hidden></div>
<div id="grid" class="list"></div>
</div>
<script>
(function(){
var ITEMS=__ITEMS__, NAMES=__NAMES__, KEY='nolabel-review-__PART__', state={}, filt='all', lim=120, db=null, banner=document.getElementById('banner');
var EVN={"both sure":"Both detectors sure","both see it":"Both detectors see it","one detector":"One detector only"}, evf='all';
var V=[['device','Device here, needs a label'],['none','No device here'],['unclear','Unclear']];
try{state=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function st(id){return state[id]||{verdict:'',cls:'',other:'',note:''};}
function card(it){var s=st(it.id);
  return '<article class="card'+(s.verdict?' marked':'')+'" data-id="'+it.id+'"><div class="pics"><div class="rk"><img src="'+it.rack+'" alt="The rack with all labels" loading="eager"><span class="hl" style="left:'+it.hl[0]+'%;top:'+it.hl[1]+'%;width:'+it.hl[2]+'%;height:'+it.hl[3]+'%"></span></div><img src="'+it.crop+'" alt="The device with no label"></div>'
   +'<div class="q">Is there a device in the purple box?</div><div class="btns">'+V.map(function(p){return '<button type="button" class="rb" data-v="'+p[0]+'" aria-pressed="'+(s.verdict===p[0]?'true':'false')+'">'+p[1]+'</button>';}).join('')
   +'<select aria-label="Class of the device"'+(s.verdict==='device'?'':' hidden')+'><option value="">Class of the device</option>'+NAMES.map(function(n){return '<option'+(s.cls===n?' selected':'')+'>'+esc(n)+'</option>';}).join('')+'</select><input type="text" class="other" aria-label="Type the class" placeholder="Type the class (optional)" value="'+esc(s.other||'')+'"'+(s.verdict==='device'&&s.cls==='Other'?'':' hidden')+'></div>'
   +'<textarea placeholder="Notes">'+esc(s.note||'')+'</textarea><div class="mono">'+esc(it.photo)+'</div></article>';}
function visible(it){var v=st(it.id).verdict; return filt==='all'||(filt==='open'?!v:filt==='done'?!!v:v===filt);}
function render(){var v=ITEMS.filter(visible); document.getElementById('grid').innerHTML=v.slice(0,lim).map(card).join('')+(v.length>lim?'<div class="more"><button type="button" class="btn primary" id="more">Show more ('+(v.length-lim)+' left)</button></div>':''); count();}
function count(){var n=0; ITEMS.forEach(function(it){if(st(it.id).verdict)n++;}); document.getElementById('n-done').textContent=n+' of '+ITEMS.length;}
function save(id,patch){var s=st(id),n={verdict:s.verdict||'',cls:s.cls||'',other:s.other||'',note:s.note||''}; for(var k in patch)n[k]=patch[k]; state[id]=n;
  try{localStorage.setItem(KEY,JSON.stringify(state));}catch(e){} if(db){db.doc('nolabel_review___PART__/'+id).set({verdict:n.verdict,cls:n.cls,other:n.other,note:n.note,updated:new Date().toISOString()}).catch(function(){});}
  var el=document.querySelector('.card[data-id="'+id+'"]'); if(el){el.classList.toggle('marked',!!n.verdict); el.querySelectorAll('[data-v]').forEach(function(b){b.setAttribute('aria-pressed',b.dataset.v===n.verdict?'true':'false');}); el.querySelector('select').hidden=n.verdict!=='device'; el.querySelector('input.other').hidden=!(n.verdict==='device'&&n.cls==='Other');} count();}
document.addEventListener('click',function(ev){var b=ev.target.closest&&ev.target.closest('[data-v]'); if(b){var id=b.closest('.card').dataset.id; save(id,{verdict:st(id).verdict===b.dataset.v?'':b.dataset.v}); return;}
  var f=ev.target.closest&&ev.target.closest('[data-f]'); if(f){filt=f.dataset.f; lim=120; document.querySelectorAll('[data-f]').forEach(function(x){x.setAttribute('aria-pressed',x===f?'true':'false');}); render(); return;}
  var e=ev.target.closest&&ev.target.closest('[data-ev]'); if(e){evf=e.dataset.ev; lim=120; document.querySelectorAll('[data-ev]').forEach(function(x){x.setAttribute('aria-pressed',x===e?'true':'false');}); render(); return;}
  if(ev.target.id==='more'){lim+=120; render();}});
document.addEventListener('change',function(ev){if(ev.target.tagName==='SELECT'){save(ev.target.closest('.card').dataset.id,{cls:ev.target.value});}});
document.addEventListener('input',function(ev){if(ev.target.classList&&ev.target.classList.contains('other')){save(ev.target.closest('.card').dataset.id,{other:ev.target.value}); return;} if(ev.target.tagName==='TEXTAREA'){save(ev.target.closest('.card').dataset.id,{note:ev.target.value});}});
var DL=null; if(window.claude&&window.claude.use){window.claude.use('downloads').then(function(d){DL=d;}).catch(function(){}); window.claude.use('db').then(function(d){db=d; if(!d)return;
  d.collection('nolabel_review___PART__').onSnapshot(function(snap){snap.docs.forEach(function(doc){if(doc.exists){var x=doc.data(); state[doc.id]={verdict:x.verdict||'',cls:x.cls||'',other:x.other||'',note:x.note||''};}}); render();},function(){db=null;});}).catch(function(){});}
document.getElementById('dl').addEventListener('click',function(){
  var q=function(v){v=String(v==null?'':v); return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}, lab={}; V.forEach(function(p){lab[p[0]]=p[1];});
  var rows=[['area','photo','verdict','class','other_class','note']]; ITEMS.forEach(function(it){var s=st(it.id); if(s.verdict||s.note)rows.push([it.id,it.photo,lab[s.verdict]||'',s.cls||'',s.cls==='Other'?(s.other||''):'',s.note||'']);});
  if(rows.length<2){banner.textContent='Nothing marked yet.'; banner.hidden=false; return;}
  var txt='﻿'+rows.map(function(r){return r.map(q).join(',');}).join('\r\n'), name='no-label-review-__PART__-'+new Date().toISOString().slice(0,10)+'.csv', blob=new Blob([txt],{type:'text/csv'});
  var plain=function(){var a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=name; document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href); a.remove();},60000);};
  (DL?DL.save({filename:name,data:blob}).catch(function(e){if(!(e&&e.code==='declined'))plain();}):Promise.resolve(plain())).then(function(){banner.textContent='Saved '+(rows.length-1)+' marked areas as '+name+'.'; banner.hidden=false;});});
render();
})();
</script>
"""

if __name__ == "__main__":
    main()
