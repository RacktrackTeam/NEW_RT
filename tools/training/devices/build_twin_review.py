"""Build the Twin Photos Review page: the photos the network team exported twice (the same image in two exports, found by md5
in the dataset's manifest.csv), each copy shown with its own labels, every difference numbered on both pictures and listed, so a
reviewer can say which copy is right.

    python3 scripts/devices/build_twin_review.py        # -> reports/devices/twin_review/index.html (one file, pictures inside)
"""
import base64, collections, csv, html, io, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "devices"))
import draw                                                   # noqa: E402
from PIL import Image, ImageDraw, ImageOps                    # noqa: E402

DATA = ROOT / "data" / "datasets" / "devices"
OUT = ROOT / "reports" / "devices" / "twin_review"
NAMES = ["Closed Unit", "Empty", "Firewall", "Gateway", "Load Balancer", "PDU", "Patch Panel", "Router", "Server", "Storage unit", "Switch", "UPS"]
SAME, DIFF, ONLY = (26, 158, 110), (232, 140, 20), (217, 54, 54)
e = html.escape


def short(n):
    return re.sub(r"^Device[ _]+[Aa]nnotation_+(Not_tra[i]?ned_|not_trained_)?", "", n)


def shapes(photo):
    out = []
    for l in (DATA / "labels" / (pathlib.Path(photo).stem + ".txt")).read_text().split("\n"):
        v = l.split()
        if v:
            xy = [float(x) for x in v[1:]]
            out.append((int(v[0]), list(zip(xy[0::2], xy[1::2]))))
    return out


def box(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def iou(a, b):
    i = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - i
    return i / u if u else 0.0


def group(copies):
    """Devices across the copies: each cluster = the same spot (overlap > 0.5) in one or more copies, with each copy's class."""
    clusters = []
    for k, c in enumerate(copies):
        for cls, pts in shapes(c["photo"]):
            b = box(pts); best = max(((iou(b, cl["box"]), cl) for cl in clusters if k not in cl["by"]), key=lambda t: t[0], default=(0, None))
            if best[0] > 0.5:
                best[1]["by"][k] = (cls, pts)
            else:
                clusters.append({"box": b, "by": {k: (cls, pts)}})
    n = len(copies); num = 0
    for cl in clusters:
        classes = {v[0] for v in cl["by"].values()}
        cl["kind"] = "same" if len(cl["by"]) == n and len(classes) == 1 else ("class" if len(cl["by"]) == n else "only")
        if cl["kind"] != "same":
            num += 1; cl["num"] = num
    return clusters


def picture(copy, k, clusters):
    img = ImageOps.exif_transpose(Image.open(DATA / "images" / copy["photo"])).convert("RGB")
    s = min(1.0, 760 / img.width, 620 / img.height); img = img.resize((round(img.width * s), round(img.height * s)), Image.LANCZOS)
    d = ImageDraw.Draw(img); W, H = img.size; f = draw.font(16)
    for cl in sorted(clusters, key=lambda c: c["kind"] != "same"):            # differences drawn on top
        if k not in cl["by"]:
            continue
        pts = [(x * W, y * H) for x, y in cl["by"][k][1]]; col = {"same": SAME, "class": DIFF, "only": ONLY}[cl["kind"]]
        d.line(pts + [pts[0]], fill=col, width=2 if cl["kind"] == "same" else 4)
        if cl["kind"] != "same":
            x, y = min(p[0] for p in pts), min(p[1] for p in pts); r = 12
            d.ellipse((x - 2, y - 2, x + 2 * r, y + 2 * r), fill=col); t = str(cl["num"])
            d.text((x + r - d.textlength(t, font=f) / 2, y + r - 10), t, fill=(255, 255, 255), font=f)
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=82)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main():
    man = list(csv.DictReader(open(DATA / "manifest.csv")))
    by = collections.defaultdict(list)
    for r in man:
        by[r["image_md5"]].append(r)
    groups = [sorted(v, key=lambda r: r["export"]) for v in by.values() if len(v) > 1]
    items = []
    for g in groups:
        cl = group(g); diffs = [c for c in cl if c["kind"] != "same"]
        rows = [{"num": c["num"], "kind": c["kind"], "cells": [NAMES[c["by"][k][0]] if k in c["by"] else None for k in range(len(g))]} for c in diffs]
        items.append({"id": g[0]["image_md5"][:16], "tab": "diff" if diffs else "same", "copies": [{"export": short(r["export"]), "photo": r["photo"], "split": r["export_split"],
                      "n": len(shapes(r["photo"])), "img": picture(r, k, cl)} for k, r in enumerate(g)], "rows": rows,
                      "same": sum(c["kind"] == "same" for c in cl), "order": -len(diffs)})
    items.sort(key=lambda it: it["order"])
    n_diff = sum(it["tab"] == "diff" for it in items); n_same = len(items) - n_diff
    page = (TEMPLATE.replace("__ITEMS__", json.dumps(items)).replace("__N__", str(len(items))).replace("__NDIFF__", str(n_diff))
            .replace("__NSAME__", str(n_same)).replace("__NCOPIES__", str(sum(len(g) for g in groups))))
    OUT.mkdir(parents=True, exist_ok=True); (OUT / "index.html").write_text(page)
    print(f"written {OUT / 'index.html'}: {len(items)} photos ({n_diff} with different labels, {n_same} with the same classes), {len(page) / 1e6:.1f} MB")


TEMPLATE = r"""<meta charset="utf-8"><title>Twin Photos Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* layout: one card per photo, its copies side by side, 90 % of the window wide; cards with depth on pale grey */
:root { --bg:#f2f4f7; --panel:#ffffff; --surface:#f6f7f9; --fg:#151a22; --muted:#596273; --line:#e2e6ec; --accent:#2252c9; --accent-soft:#e8eefb;
  --same:#1a9e6e; --diff:#e88c14; --only:#d93636; --shadow:0 1px 2px rgba(16,24,40,.05),0 6px 18px rgba(16,24,40,.07);
  --sans:"IBM Plex Sans","Segoe UI",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#11151b; --panel:#1a2029; --surface:#212833; --fg:#e8ecf2; --muted:#9aa4b4; --line:#2c3441; --accent:#7ea2ff; --accent-soft:#1f2b46;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35); color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#11151b; --panel:#1a2029; --surface:#212833; --fg:#e8ecf2; --muted:#9aa4b4; --line:#2c3441; --accent:#7ea2ff; --accent-soft:#1f2b46;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35); color-scheme:dark; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font:15px/1.5 var(--sans); }
.wrap { width:90vw; margin:0 auto; padding-block:26px 60px; display:grid; gap:16px; } .list { display:grid; gap:16px; min-width:0; }
@media (max-width:700px) { .wrap { width:auto; padding-inline:16px; } }
h1 { font-size:1.7rem; margin:0; } p { margin:4px 0; max-width:80ch; } .muted { color:var(--muted); }
.top { display:flex; flex-wrap:wrap; gap:10px 20px; align-items:end; justify-content:space-between; }
.stats { display:flex; flex-wrap:wrap; gap:8px; } .stats div { background:var(--panel); box-shadow:var(--shadow); border-radius:10px; padding:7px 12px; display:flex; gap:6px; align-items:baseline; }
.stats b { font-size:1.05rem; font-variant-numeric:tabular-nums; } .stats span { font-size:.82rem; color:var(--muted); }
.legend { display:flex; flex-wrap:wrap; gap:14px; font-size:.86rem; color:var(--muted); } .k { display:inline-block; width:14px; height:10px; border:3px solid; border-radius:2px; margin-right:5px; vertical-align:-1px; }
.k.s { border-color:var(--same); } .k.d { border-color:var(--diff); } .k.o { border-color:var(--only); }
.bar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.switch { display:inline-flex; flex-wrap:wrap; background:var(--panel); box-shadow:var(--shadow); border-radius:10px; padding:4px; gap:2px; }
.switch button, .btn { font:500 .9rem var(--sans); border:0; background:transparent; color:var(--fg); padding:7px 13px; border-radius:7px; cursor:pointer; }
.switch button[aria-pressed="true"] { background:var(--accent); color:#fff; } .btn { background:var(--panel); box-shadow:var(--shadow); } .btn.primary { background:var(--accent); color:#fff; }
button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.banner { padding:9px 14px; border-radius:10px; background:var(--accent-soft); font-size:.9rem; }
.card { background:var(--panel); border-radius:12px; box-shadow:var(--shadow); padding:14px 16px; display:grid; gap:10px; min-width:0; border:2px solid transparent; }
.card.marked { border-color:var(--accent); }
.head { display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap; align-items:baseline; } .head h2 { font-size:1.02rem; margin:0; }
.copies { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr)); gap:12px; }
.copy { background:var(--surface); border-radius:10px; padding:8px; min-width:0; } .copy img { width:100%; max-height:520px; object-fit:contain; display:block; border-radius:6px; background:#fff; }
.copy .cap { display:flex; justify-content:space-between; gap:8px; font-size:.84rem; margin-top:6px; } .copy .cap b { font-weight:600; } .mono { font:.74rem var(--mono); color:var(--muted); overflow-wrap:anywhere; }
.tablewrap { overflow-x:auto; } table { border-collapse:collapse; font-size:.88rem; width:100%; } th { text-align:left; font:600 .72rem var(--mono); color:var(--muted); text-transform:uppercase; letter-spacing:.04em; padding:5px 10px 5px 0; border-bottom:1px solid var(--line); }
td { padding:6px 10px 6px 0; border-bottom:1px solid var(--line); } td.none { color:var(--only); font-style:italic; }
.num { display:inline-grid; place-items:center; width:22px; height:22px; border-radius:50%; color:#fff; font:600 .74rem var(--mono); } .num.class { background:var(--diff); } .num.only { background:var(--only); }
.btns { display:flex; flex-wrap:wrap; gap:8px; } .rb { font:500 .86rem var(--sans); border:1px solid var(--line); background:var(--surface); color:var(--fg); padding:7px 12px; border-radius:8px; cursor:pointer; }
.rb[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
textarea { width:100%; min-height:44px; font:.88rem/1.4 var(--sans); border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:var(--panel); color:var(--fg); resize:vertical; }
</style>
<div class="wrap">
<div class="top"><div><h1>Twin Photos Review</h1><p class="muted">The network team exported these photos twice, in two exports, with different labels. Both copies are in the dataset. Say which copy is right.</p></div>
<div class="stats"><div><b>__N__</b><span>photos</span></div><div><b>__NCOPIES__</b><span>copies in the dataset</span></div><div><b id="n-done">0</b><span>marked</span></div></div></div>
<div class="legend"><span><i class="k s"></i>same device, same class in every copy</span><span><i class="k d"></i>same device, different class</span><span><i class="k o"></i>labelled in this copy only</span></div>
<div class="bar"><div class="switch" role="group" aria-label="Tab"><button type="button" data-tab="diff" aria-pressed="true">Labels differ (__NDIFF__)</button><button type="button" data-tab="same" aria-pressed="false">Same classes, outlines differ (__NSAME__)</button></div>
<button type="button" class="btn primary" id="dl">Download my marks (CSV)</button></div>
<div id="banner" class="banner" hidden></div>
<div id="grid" class="list"></div>
</div>
<script>
(function(){
var ITEMS=__ITEMS__, KEY='twin-review', state={}, tab='diff', db=null, banner=document.getElementById('banner');
try{state=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function st(id){return state[id]||{verdict:'',note:''};}
function choices(it){var c=it.copies.map(function(x,k){return ['copy'+k,x.export+' copy right'];}); c.push(['neither','Neither, both need fixing']); c.push(['unclear','Unclear']); return c;}
function card(it){var s=st(it.id);
  var copies=it.copies.map(function(c){return '<div class="copy"><img src="'+c.img+'" alt="Copy in '+esc(c.export)+'"><div class="cap"><span><b>'+esc(c.export)+'</b> · '+esc(c.split)+' · '+c.n+' labels</span></div><div class="mono">'+esc(c.photo)+'</div></div>';}).join('');
  var rows=it.rows.length?('<div class="tablewrap"><table><tr><th>#</th>'+it.copies.map(function(c){return '<th>'+esc(c.export)+'</th>';}).join('')+'</tr>'
    +it.rows.map(function(r){return '<tr><td><span class="num '+r.kind+'">'+r.num+'</span></td>'+r.cells.map(function(x){return x?'<td>'+esc(x)+'</td>':'<td class="none">no label</td>';}).join('')+'</tr>';}).join('')+'</table></div>')
    :'<p class="muted">Every device has the same class in all copies ('+it.same+' devices); only the outlines are drawn slightly differently.</p>';
  return '<article class="card'+(s.verdict?' marked':'')+'" data-id="'+it.id+'"><div class="head"><h2>'+it.copies.map(function(c){return esc(c.export);}).join(' and ')+'</h2><span class="muted">'+(it.rows.length?it.rows.length+' difference'+(it.rows.length>1?'s':''):'no class difference')+' · '+it.same+' devices the same</span></div>'
    +'<div class="copies">'+copies+'</div>'+rows+'<div class="btns">'+choices(it).map(function(p){return '<button type="button" class="rb" data-v="'+p[0]+'" aria-pressed="'+(s.verdict===p[0]?'true':'false')+'">'+esc(p[1])+'</button>';}).join('')+'</div>'
    +'<textarea placeholder="Notes: which label is wrong, and what it should be">'+esc(s.note||'')+'</textarea></article>';}
function render(){document.getElementById('grid').innerHTML=ITEMS.filter(function(it){return it.tab===tab;}).map(card).join(''); count();}
function count(){var n=0; ITEMS.forEach(function(it){if(st(it.id).verdict)n++;}); document.getElementById('n-done').textContent=n+' of '+ITEMS.length;}
function save(id,patch){var s=st(id),n={verdict:s.verdict||'',note:s.note||''}; for(var k in patch)n[k]=patch[k]; state[id]=n;
  try{localStorage.setItem(KEY,JSON.stringify(state));}catch(e){}
  if(db){db.doc('twin_review/'+id).set({verdict:n.verdict,note:n.note,updated:new Date().toISOString()}).catch(function(){});}
  var el=document.querySelector('.card[data-id="'+id+'"]'); if(el){el.classList.toggle('marked',!!n.verdict); el.querySelectorAll('[data-v]').forEach(function(b){b.setAttribute('aria-pressed',b.dataset.v===n.verdict?'true':'false');});} count();}
document.addEventListener('click',function(ev){var b=ev.target.closest&&ev.target.closest('[data-v]'); if(b){var id=b.closest('.card').dataset.id; save(id,{verdict:st(id).verdict===b.dataset.v?'':b.dataset.v}); return;}
  var t=ev.target.closest&&ev.target.closest('[data-tab]'); if(t){tab=t.dataset.tab; document.querySelectorAll('[data-tab]').forEach(function(x){x.setAttribute('aria-pressed',x===t?'true':'false');}); render();}});
document.addEventListener('input',function(ev){if(ev.target.tagName==='TEXTAREA'){var id=ev.target.closest('.card').dataset.id; save(id,{note:ev.target.value});}});
var DL=null; if(window.claude&&window.claude.use){window.claude.use('downloads').then(function(d){DL=d;}).catch(function(){}); window.claude.use('db').then(function(d){db=d; if(!d)return;
  d.collection('twin_review').onSnapshot(function(snap){snap.docs.forEach(function(doc){if(doc.exists){var x=doc.data(); state[doc.id]={verdict:x.verdict||'',note:x.note||''};}}); render();},function(){db=null;});}).catch(function(){});}
document.getElementById('dl').addEventListener('click',function(){
  var q=function(v){v=String(v==null?'':v); return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;};
  var rows=[['photo_group','copies','verdict','note']]; ITEMS.forEach(function(it){var s=st(it.id); if(!s.verdict&&!s.note)return; var lab={}; choices(it).forEach(function(p){lab[p[0]]=p[1];});
    rows.push([it.id,it.copies.map(function(c){return c.export+': '+c.photo;}).join(' | '),lab[s.verdict]||'',s.note||'']);});
  if(rows.length<2){banner.textContent='Nothing marked yet.'; banner.hidden=false; return;}
  var txt='﻿'+rows.map(function(r){return r.map(q).join(',');}).join('\r\n'), name='twin-photos-review-'+new Date().toISOString().slice(0,10)+'.csv', blob=new Blob([txt],{type:'text/csv'});
  var plain=function(){var a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=name; document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href); a.remove();},60000);};
  (DL?DL.save({filename:name,data:blob}).catch(function(e){if(!(e&&e.code==='declined'))plain();}):Promise.resolve(plain())).then(function(){banner.textContent='Saved '+(rows.length-1)+' marked photos as '+name+'.'; banner.hidden=false;});});
render();
})();
</script>
"""

if __name__ == "__main__":
    main()
