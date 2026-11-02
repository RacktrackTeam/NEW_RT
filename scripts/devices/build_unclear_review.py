"""Build the Label Verification Review for the network team (step 2 of the device class verification): every instance still in
doubt that the AI team (Geethika and Prasanna) marked Unclear or Wrong (the user, 2026-10-21: send the wrong ones as well), split into
two parts with whole images per part, wrong ones first, zipped with a READ ME as reports/devices/Label verification review.zip.

    python3 scripts/devices/build_unclear_review.py
"""
import collections, json, pathlib, re, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "devices")); sys.path.insert(0, str(ROOT / "src"))
import audit_labels as AL                                     # noqa: E402
from PIL import Image                                         # noqa: E402

MARKS = ROOT / "results" / "devices" / "review_marks"
PAGES = ROOT / "reports" / "devices" / "label_audit"
OUT = ROOT / "reports" / "devices" / "verify_labels"           # -> verify_labels_1/, verify_labels_2/
ZIP = ROOT / "reports" / "devices" / "Label verification review.zip"
TOP = "Label verification review"                             # the folder inside the zip
PARTS = 2                                                      # reviewers on the network team, like the No label review
NAMES = ["Closed Unit", "Empty", "Firewall", "Gateway", "Load Balancer", "PDU", "Patch Panel", "Router", "Server", "Storage unit", "Switch", "UPS"]
WHY = {"wrong": "our review marked it wrong", "unclear": "our review could not decide"}

README = """Verification of unclear and wrong device labels

What this is
  {n:,} labels in {nph:,} images: {nw:,} our reviewers marked wrong and {nu:,} they
  could not decide. For each one, say whether the label in the dataset is right;
  if not, pick the right class. Each card says why it is asked; the ones marked
  wrong come first. The work is split in two parts, one per reviewer:
{parts}
How to review
  1. Unzip this file. Keep the "img" folder next to "index.html".
  2. Open your folder and double-click "index.html" (Chrome or Safari).
  3. Each card shows the rack (left) and the device (right); the red outline is the
     label. Choose one answer:
       - Label right
       - Label wrong     ->  then pick the right class
                             (Other: type the class if you know it;
                              Not a device: the label is on no device)
       - Unclear
     Add a note if something needs explaining.
  4. Your marks are kept in this browser on this computer. Finish on the same
     computer and browser.
  5. When done, press "Download my marks (CSV)" and send the CSV file back.

Deadline
  As soon as possible.
"""


def main():
    items_by_id, page_of = {}, {}
    for page in sorted(PAGES.glob("class*/index.html")):
        for it in json.loads(re.search(r"var ITEMS=(\[.*?\]), CHECKS=", page.read_text(), re.S).group(1)):
            items_by_id[it["id"]] = it; page_of[it["id"]] = page.parent
    kind = {}; keep = AL.in_doubt_ids()                            # only instances still sent to review
    for fp in sorted(MARKS.glob("*.json")):
        d = json.loads(fp.read_text())
        for k in ("unclear", "wrong"):
            kind.update({cid: k for cid in d.get(k, []) if cid in items_by_id and cid in keep})
    unclear = list(kind)
    by_photo = collections.defaultdict(list)
    for cid in unclear:
        by_photo[items_by_id[cid]["photo"]].append(cid)
    owner, load = {}, [0] * PARTS                                  # whole photos per reviewer, balanced by labels
    for ph, cs in sorted(by_photo.items(), key=lambda t: (-len(t[1]), t[0])):
        k = load.index(min(load)); owner[ph] = k; load[k] += len(cs)
    outs = [OUT.parent / f"{OUT.name}_{k + 1}" for k in range(PARTS)]
    for o in outs:
        (o / "img").mkdir(parents=True, exist_ok=True)
        for f_ in (o / "img").glob("*.jpg"):
            f_.unlink()
    sheets, cards = {}, [[] for _ in range(PARTS)]
    for n, (ph, cs) in enumerate(sorted(by_photo.items())):
        for j, cid in enumerate(cs):
            it = items_by_id[cid]; key = (page_of[cid], it["sheet"])
            if key not in sheets:
                sheets[key] = Image.open(page_of[cid] / "img" / it["sheet"]).convert("RGB")
            name = f"u{n:04d}_{j}.jpg"
            sheets[key].crop((0, it["row"] * AL.TILE_H, AL.TILE_W, (it["row"] + 1) * AL.TILE_H)).save(outs[owner[ph]] / "img" / name, quality=85)
            cards[owner[ph]].append({"id": cid, "img": f"img/{name}", "label": it["label"], "why": WHY[kind[cid]], "photo": ph})
    for c in cards:
        c.sort(key=lambda x: x["why"] != WHY["wrong"])            # wrong ones first, stable within each group
    for k, o in enumerate(outs):
        page = (TEMPLATE.replace("__ITEMS__", json.dumps(cards[k])).replace("__NAMES__", json.dumps(NAMES + ["Other", "Not a device"]))
                .replace("__N__", f"{len(cards[k]):,}").replace("__NPH__", f"{len({c['photo'] for c in cards[k]}):,}")
                .replace("__PART__", f"{k + 1}").replace("__PARTS__", f"{PARTS}"))
        (o / "index.html").write_text(page)
    parts = "".join(f'    Reviewer {k + 1}  ->  folder "{o.name}"  ({len(cards[k]):,} labels, {len({c["photo"] for c in cards[k]}):,} images)\n' for k, o in enumerate(outs))
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_STORED) as z:
        nw = sum(1 for v in kind.values() if v == "wrong")
        z.writestr(f"{TOP}/READ ME FIRST.txt", README.format(n=len(unclear), nph=len(by_photo), nw=nw, nu=len(unclear) - nw, parts=parts))
        for o in outs:
            for f_ in sorted(o.rglob("*")):
                if f_.is_file():
                    z.write(f_, f"{TOP}/{o.name}/{f_.relative_to(o)}")
    print(f"{len(unclear):,} labels (wrong + unclear) in {len(by_photo):,} photos; parts {[len(c) for c in cards]}; {ZIP.name} {ZIP.stat().st_size / 1e6:.0f} MB")


TEMPLATE = r"""<meta charset="utf-8"><title>Label Verification Review</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* layout: a grid of label cards, rack and device in each picture, 90 % of the window wide */
:root { --bg:#f2f4f7; --panel:#ffffff; --surface:#f6f7f9; --fg:#151a22; --muted:#596273; --line:#e2e6ec; --accent:#2252c9; --accent-soft:#e8eefb; --green:#16895a;
  --shadow:0 1px 2px rgba(16,24,40,.05),0 6px 18px rgba(16,24,40,.07); --sans:"IBM Plex Sans","Segoe UI",system-ui,sans-serif; --mono:"IBM Plex Mono",ui-monospace,Menlo,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#11151b; --panel:#1a2029; --surface:#212833; --fg:#e8ecf2; --muted:#9aa4b4; --line:#2c3441; --accent:#7ea2ff; --accent-soft:#1f2b46; --green:#3fc68b;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35); color-scheme:dark; } }
:root[data-theme="dark"] { --bg:#11151b; --panel:#1a2029; --surface:#212833; --fg:#e8ecf2; --muted:#9aa4b4; --line:#2c3441; --accent:#7ea2ff; --accent-soft:#1f2b46; --green:#3fc68b;
  --shadow:0 1px 2px rgba(0,0,0,.4),0 6px 18px rgba(0,0,0,.35); color-scheme:dark; }
* { box-sizing:border-box; }
body { background:var(--bg); color:var(--fg); font:15px/1.5 var(--sans); }
.wrap { width:90vw; margin:0 auto; padding-block:26px 60px; display:grid; gap:16px; } @media (max-width:700px) { .wrap { width:auto; padding-inline:16px; } }
h1 { font-size:1.7rem; margin:0; } p { margin:4px 0; max-width:80ch; } .muted { color:var(--muted); }
.top { display:flex; flex-wrap:wrap; gap:10px 20px; align-items:end; justify-content:space-between; }
.stats { display:flex; flex-wrap:wrap; gap:8px; } .stats div { background:var(--panel); box-shadow:var(--shadow); border-radius:10px; padding:7px 12px; display:flex; gap:6px; align-items:baseline; }
.stats b { font-size:1.05rem; font-variant-numeric:tabular-nums; } .stats span { font-size:.82rem; color:var(--muted); }
.bar { display:flex; flex-wrap:wrap; gap:10px; align-items:center; }
.switch { display:inline-flex; flex-wrap:wrap; background:var(--panel); box-shadow:var(--shadow); border-radius:10px; padding:4px; gap:2px; }
.switch button, .btn { font:500 .9rem var(--sans); border:0; background:transparent; color:var(--fg); padding:7px 13px; border-radius:7px; cursor:pointer; }
.switch button[aria-pressed="true"] { background:var(--accent); color:#fff; } .btn.primary { background:var(--accent); color:#fff; box-shadow:var(--shadow); }
button:focus-visible, select:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.banner { padding:9px 14px; border-radius:10px; background:var(--accent-soft); font-size:.9rem; }
.list { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,520px),1fr)); gap:14px; }
.card { background:var(--panel); border-radius:12px; box-shadow:var(--shadow); padding:12px; display:grid; gap:8px; min-width:0; border:2px solid transparent; } .card.marked { border-color:var(--accent); }
.card img { width:100%; aspect-ratio:600/255; object-fit:contain; display:block; border-radius:8px; cursor:zoom-in; background:var(--surface); }
.part { font-size:.9rem; font-weight:500; color:var(--muted); margin-left:8px; } input.other { font:.88rem var(--sans); border:1px solid var(--line); border-radius:8px; padding:7px 9px; background:var(--panel); color:var(--fg); } .who { font-size:.74rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }
.lab { font-size:1.15rem; font-weight:600; color:var(--green); } .meta { font-size:.84rem; color:var(--muted); } .mono { font:.74rem var(--mono); color:var(--muted); overflow-wrap:anywhere; }
.btns { display:flex; flex-wrap:wrap; gap:8px; align-items:center; } .rb { font:500 .86rem var(--sans); border:1px solid var(--line); background:var(--surface); color:var(--fg); padding:7px 12px; border-radius:8px; cursor:pointer; }
.rb[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
select, textarea { font:.88rem var(--sans); border:1px solid var(--line); border-radius:8px; padding:7px 9px; background:var(--panel); color:var(--fg); } textarea { width:100%; min-height:40px; resize:vertical; }
.more { display:flex; justify-content:center; grid-column:1/-1; }
#lb[hidden] { display:none; } #lb { position:fixed; inset:0; background:rgba(15,17,21,.9); display:grid; place-items:center; padding:20px; z-index:20; } #lb img { max-width:96vw; max-height:90vh; cursor:zoom-out; }
</style>
<div class="wrap">
<div class="top"><div><h1>Label Verification Review <span class="part">Reviewer __PART__ of __PARTS__</span></h1><p class="muted">Our reviewers marked these labels wrong or could not decide (each card says which; the wrong ones come first). Look at the rack (left) and the device (right, the label in red): is the label right? If not, pick the right class.</p></div>
<div class="stats"><div><b>__N__</b><span>labels</span></div><div><b>__NPH__</b><span>photos</span></div><div><b id="n-done">0</b><span>marked</span></div></div></div>
<div class="bar"><div class="switch" role="group" aria-label="Show"><button type="button" data-f="all" aria-pressed="true">All</button><button type="button" data-f="open" aria-pressed="false">Not marked</button><button type="button" data-f="right" aria-pressed="false">Label right</button><button type="button" data-f="wrong" aria-pressed="false">Label wrong</button><button type="button" data-f="unclear" aria-pressed="false">Unclear</button></div>
<button type="button" class="btn primary" id="dl">Download my marks (CSV)</button></div>
<div id="banner" class="banner" hidden></div>
<div id="grid" class="list"></div>
</div>
<div id="lb" hidden><img alt="Enlarged picture"></div>
<script>
(function(){
var ITEMS=__ITEMS__, NAMES=__NAMES__, KEY='label-verify-__PART__', state={}, filt='all', lim=100, db=null, banner=document.getElementById('banner');
var V=[['right','Label right'],['wrong','Label wrong'],['unclear','Unclear']];
try{state=JSON.parse(localStorage.getItem(KEY)||'{}');}catch(e){}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function st(id){return state[id]||{verdict:'',cls:'',other:'',note:''};}
function card(it){var s=st(it.id);
  return '<article class="card'+(s.verdict?' marked':'')+'" data-id="'+it.id+'"><img src="'+it.img+'" alt="The rack and the device" loading="lazy" data-zoom><div><span class="who">Label in the dataset:</span><div class="lab">'+esc(it.label)+'</div></div>'
   +'<div class="meta">Asked because '+esc(it.why)+'</div>'
   +'<div class="btns">'+V.map(function(p){return '<button type="button" class="rb" data-v="'+p[0]+'" aria-pressed="'+(s.verdict===p[0]?'true':'false')+'">'+p[1]+'</button>';}).join('')
   +'<select aria-label="The right class"'+(s.verdict==='wrong'?'':' hidden')+'><option value="">The right class</option>'+NAMES.map(function(n){return '<option'+(s.cls===n?' selected':'')+'>'+esc(n)+'</option>';}).join('')+'</select><input type="text" class="other" aria-label="Type the class" placeholder="Type the class (optional)" value="'+esc(s.other||'')+'"'+(s.verdict==='wrong'&&s.cls==='Other'?'':' hidden')+'></div>'
   +'<textarea placeholder="Notes">'+esc(s.note||'')+'</textarea><div class="mono">'+esc(it.photo)+'</div></article>';}
function visible(it){var v=st(it.id).verdict; return filt==='all'||(filt==='open'?!v:v===filt);}
function render(){var v=ITEMS.filter(visible); document.getElementById('grid').innerHTML=v.slice(0,lim).map(card).join('')+(v.length>lim?'<div class="more"><button type="button" class="btn primary" id="more">Show more ('+(v.length-lim)+' left)</button></div>':''); count();}
function count(){var n=0; ITEMS.forEach(function(it){if(st(it.id).verdict)n++;}); document.getElementById('n-done').textContent=n+' of '+ITEMS.length;}
function save(id,patch){var s=st(id),n={verdict:s.verdict||'',cls:s.cls||'',other:s.other||'',note:s.note||''}; for(var k in patch)n[k]=patch[k]; state[id]=n;
  try{localStorage.setItem(KEY,JSON.stringify(state));}catch(e){} if(db){db.doc('unclear_review/'+id).set({verdict:n.verdict,cls:n.cls,other:n.other,note:n.note,updated:new Date().toISOString()}).catch(function(){});}
  var el=document.querySelector('.card[data-id="'+id+'"]'); if(el){el.classList.toggle('marked',!!n.verdict); el.querySelectorAll('[data-v]').forEach(function(b){b.setAttribute('aria-pressed',b.dataset.v===n.verdict?'true':'false');}); el.querySelector('select').hidden=n.verdict!=='wrong'; el.querySelector('input.other').hidden=!(n.verdict==='wrong'&&n.cls==='Other');} count();}
var lb=document.getElementById('lb');
document.addEventListener('click',function(ev){var z=ev.target.closest&&ev.target.closest('[data-zoom]'); if(z){lb.querySelector('img').src=z.src; lb.hidden=false; return;} if(ev.target.closest&&ev.target.closest('#lb')){lb.hidden=true; return;}
  var b=ev.target.closest&&ev.target.closest('[data-v]'); if(b){var id=b.closest('.card').dataset.id; save(id,{verdict:st(id).verdict===b.dataset.v?'':b.dataset.v}); return;}
  var f=ev.target.closest&&ev.target.closest('[data-f]'); if(f){filt=f.dataset.f; lim=100; document.querySelectorAll('[data-f]').forEach(function(x){x.setAttribute('aria-pressed',x===f?'true':'false');}); render(); return;}
  if(ev.target.id==='more'){lim+=100; render();}});
document.addEventListener('keydown',function(e){if(e.key==='Escape')lb.hidden=true;});
document.addEventListener('change',function(ev){if(ev.target.tagName==='SELECT'){save(ev.target.closest('.card').dataset.id,{cls:ev.target.value});}});
document.addEventListener('input',function(ev){if(ev.target.tagName==='TEXTAREA'){save(ev.target.closest('.card').dataset.id,{note:ev.target.value});} else if(ev.target.classList.contains('other')){save(ev.target.closest('.card').dataset.id,{other:ev.target.value});}});
var DL=null; if(window.claude&&window.claude.use){window.claude.use('downloads').then(function(d){DL=d;}).catch(function(){}); window.claude.use('db').then(function(d){db=d; if(!d)return;
  d.collection('unclear_review').onSnapshot(function(snap){snap.docs.forEach(function(doc){if(doc.exists){var x=doc.data(); state[doc.id]={verdict:x.verdict||'',cls:x.cls||'',other:x.other||'',note:x.note||''};}}); render();},function(){db=null;});}).catch(function(){});}
document.getElementById('dl').addEventListener('click',function(){
  var q=function(v){v=String(v==null?'':v); return /[",\n]/.test(v)?'"'+v.replace(/"/g,'""')+'"':v;}, lab={}; V.forEach(function(p){lab[p[0]]=p[1];});
  var rows=[['card','photo','label_in_dataset','decision','right_class','other_class','note']]; ITEMS.forEach(function(it){var s=st(it.id); if(s.verdict||s.note)rows.push([it.id,it.photo,it.label,lab[s.verdict]||'',s.cls||'',s.cls==='Other'?(s.other||''):'',s.note||'']);});
  if(rows.length<2){banner.textContent='Nothing marked yet.'; banner.hidden=false; return;}
  var txt='﻿'+rows.map(function(r){return r.map(q).join(',');}).join('\r\n'), name='label-verification-__PART__-'+new Date().toISOString().slice(0,10)+'.csv', blob=new Blob([txt],{type:'text/csv'});
  var plain=function(){var a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download=name; document.body.appendChild(a); a.click(); setTimeout(function(){URL.revokeObjectURL(a.href); a.remove();},60000);};
  (DL?DL.save({filename:name,data:blob}).catch(function(e){if(!(e&&e.code==='declined'))plain();}):Promise.resolve(plain())).then(function(){banner.textContent='Saved '+(rows.length-1)+' marks as '+name+'.'; banner.hidden=false;});});
render();
})();
</script>
"""

if __name__ == "__main__":
    main()
