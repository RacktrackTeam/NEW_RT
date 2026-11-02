"""Build the Device Dataset Review: the ONE report of the complete review of the merged device dataset (the user, 2026-10-26: "one
report, complete dataset review"), one white page (report standard). Two reviews from one dataset: the device label review (images of
1 MP or more -> class in doubt -> AI team -> network team) and the missing-device search (every image -> network team). Two tabs
(user, 2026-10-26): No label, Device class verification, Merge issues (user, 2026-10-26); the first two with three blocks: 01 what was sent to review (funnel), 02 the
review results (and whether the unclear places still hold a device, from the reviewers' notes), 03 the dataset before and after
per class. The dataset strip sits above the tabs.

Every number is read from the dataset, the audit's result files (sources.json, export_issues.json, review_results.json,
verify_results.json, nolabel_strict.json, nolabel_results.json), the reviewers' marks and the review pages; nothing is typed in by hand.

    python3 scripts/devices/collect_review_marks.py         # all marks and answers -> results, then this report
    python3 scripts/devices/build_review_report.py          # -> reports/devices/label_review_report/index.html
"""
import collections, csv, html, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "devices")); sys.path.insert(0, str(ROOT / "src"))
import audit_labels as AL                                   # noqa: E402

RES = ROOT / "results" / "devices"
OUT = ROOT / "reports" / "devices" / "label_review_report"
PKG = ROOT / "reports" / "devices"
SENT = "21 Oct 2026"                                        # the day both packages went to the network team
DATES = {"merged": "19 Oct 2026", "review": "19 Oct 2026", "sent": SENT}   # when each step happened
# the page style (report standard: one white page, system font, plain tables)
STYLE = """/* layout: one white page, 90% wide; top bar, finding title, four tiles, one flow, numbered sections of plain tables */
:root { color-scheme:light; --fg:#111827; --muted:#6b7280; --line:#e5e7eb; --head:#f3f4f6; --inset:#f9fafb; --accent:#1f4fbf; --green:#15803d; --red:#b91c1c; --grey:#9ca3af; }
* { box-sizing:border-box; } body { margin:0; background:#ffffff; color:var(--fg); font:16px/1.55 -apple-system,"Segoe UI",Roboto,Arial,sans-serif; }
.top { border-bottom:1px solid var(--line); } .top div { width:90vw; margin:0 auto; padding:12px 0; display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; justify-content:space-between; font-size:14px; }
.top b { font-size:15px; } .chips { display:flex; flex-wrap:wrap; gap:6px; } .chip { font-size:13px; border:1px solid var(--line); border-radius:999px; padding:2px 10px; color:var(--muted); white-space:nowrap; }
.chip.ok { border-color:var(--green); color:var(--green); } .chip.dec { border-color:var(--fg); color:var(--fg); }
main { width:90vw; margin:0 auto; padding:28px 0 60px; } @media (max-width:800px) { .top div, main { width:auto; padding-left:16px; padding-right:16px; } }
h1 { font-size:28px; line-height:1.25; margin:0 0 6px; } .lead { font-size:17px; color:var(--muted); margin:0 0 22px; max-width:900px; }
.tiles { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-bottom:22px; } @media (max-width:800px) { .tiles { grid-template-columns:repeat(2,minmax(0,1fr)); } }
.tile { border:1px solid var(--line); border-radius:10px; padding:14px 16px; box-shadow:0 1px 2px rgba(17,24,39,.04); } .tile b { display:block; font-size:28px; font-variant-numeric:tabular-nums; } .tile span { color:var(--muted); font-size:14px; }
.flow { display:flex; align-items:stretch; overflow-x:auto; padding:2px 0 6px; margin-bottom:8px; }
.fb { flex:1 1 0; min-width:150px; border:1px solid var(--line); border-top:3px solid var(--accent); border-radius:10px; padding:12px 14px; display:grid; gap:2px; align-content:start; } .fb.g { border-top-color:var(--green); }
.fb span { font-size:13px; color:var(--muted); } .fb b { font-size:24px; font-variant-numeric:tabular-nums; } .fb small { font-size:13px; color:var(--muted); }
.arr { flex:0 0 26px; align-self:center; height:1px; background:var(--grey); position:relative; margin:0 4px; } .arr::after { content:""; position:absolute; right:-1px; top:-4px; border:4px solid transparent; border-left-color:var(--grey); }
section { border-top:1px solid var(--line); padding-top:22px; margin-top:28px; } h2 { font-size:20px; margin:0 0 12px; display:flex; gap:12px; align-items:baseline; } h2 span { color:var(--muted); font-weight:500; font-size:15px; }
h3 { font-size:16px; margin:18px 0 8px; } p { margin:0 0 12px; max-width:900px; } .note { color:var(--muted); font-size:14px; }
.tw { overflow-x:auto; margin-bottom:14px; } table { width:100%; border-collapse:collapse; font-size:15px; }
th { background:var(--head); text-align:left; font-size:13px; font-weight:600; color:#374151; padding:8px 12px; } td { padding:9px 12px; border-bottom:1px solid var(--line); vertical-align:top; }
.n { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; } tr.tot td { font-weight:600; border-bottom:0; }
.bar { display:flex; height:12px; border-radius:6px; overflow:hidden; background:var(--head); margin:4px 0 14px; } .bar i { display:block; height:100%; }
.bar .g { background:var(--green); } .bar .r { background:var(--red); } .bar .u { background:var(--grey); } .bar .o { background:var(--line); }
.tl { list-style:none; margin:0; padding:0; display:grid; gap:0; } .tl li { display:grid; grid-template-columns:90px minmax(0,1fr); gap:14px; padding:8px 0; border-bottom:1px solid var(--line); align-items:baseline; }
.tl b { font-size:20px; font-variant-numeric:tabular-nums; text-align:right; } .tl li:last-child b { color:var(--green); }
.cols { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr)); gap:24px; }
.pics { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr)); gap:12px; } figure { margin:0; }
figure img { width:100%; height:130px; object-fit:contain; background:var(--inset); border:1px solid var(--line); border-radius:8px; display:block; } figcaption { font-size:13px; color:var(--muted); margin-top:4px; }
details summary { cursor:pointer; color:var(--accent); font-size:14px; } .names { font-size:14px; color:var(--muted); margin-top:8px; }
footer { border-top:1px solid var(--line); margin-top:36px; padding-top:12px; color:var(--muted); font-size:13px; }
"""
e = html.escape


def short(n):
    return re.sub(r"^Device[ _]+[Aa]nnotation_+(Not_tra[i]?ned_|not_trained_)?", "", n)


def polygons(f):
    return sum(1 for l in f.read_text().split("\n") if len(l.split()) > 5)


def main():
    man = list(csv.DictReader(open(AL.DATA / "manifest.csv")))
    inst = {r["photo"]: polygons(AL.DATA / "labels" / (pathlib.Path(r["photo"]).stem + ".txt")) for r in man}
    n_img = len(man); n_inst = sum(inst.values())                   # the complete dataset
    per_export = collections.defaultdict(lambda: [0, 0])
    for r in man:
        per_export[r["export"]][0] += 1; per_export[r["export"]][1] += inst[r["photo"]]
    PF = AL.photo_flags()                                    # the images of 1 MP or more, one colour per instance
    n_mp = len(PF); col = collections.Counter(c for f in PF for c in f["colours"]); n_mp_inst = sum(col.values())
    n_sent = col["red"] + col["yellow"]                      # labels whose class was in doubt
    by_cls = collections.defaultdict(collections.Counter)
    for f in PF:
        for c, k in zip(f["colours"], f["cls"]):
            by_cls[k]["sent" if c in ("red", "yellow") else "not"] += 1
    R = json.load(open(RES / "review_results.json"))["rows"]
    r_ok, r_bad, r_unc = (sum(r[k] for r in R) for k in ("right", "wrong", "unclear"))
    uc = []
    for k in (1, 2):
        s = (PKG / f"verify_labels_{k}" / "index.html").read_text()
        uc += json.loads(re.search(r"var ITEMS=(\[.*?\]), NAMES=", s, re.S).group(1))
    assert len(uc) == r_unc + r_bad, "the step 2 package is older than the marks: run build_unclear_review.py"
    NS = json.load(open(RES / "nolabel_strict.json")); NL = NS["kept"]
    NA = json.load(open(RES / "nolabel_results.json")) if (RES / "nolabel_results.json").exists() else None   # the network team's No label answers
    ANSWERED = "23 Oct 2026"                                    # the day the No label answers came back
    VR = json.load(open(RES / "verify_results.json")) if (RES / "verify_results.json").exists() else None   # the network team's step 2 answers
    VERIFIED = "26 Oct 2026"                                    # the day the step 2 answers came back
    vrr, vrw, vru = ((VR["decisions"].get(k, 0) for k in ("right", "wrong", "unclear")) if VR else (0, 0, 0))
    v_other = sum(v for k, v in VR["corrections"].items() if k.endswith("-> Other")) if VR else 0
    v_notdev = sum(v for k, v in VR["corrections"].items() if k.endswith("-> Not a device")) if VR else 0
    v_move = vrw - v_other - v_notdev

    # ---------- extra facts
    V = next(iter(json.load(open(RES / "export_issues.json"))["exports"].values()))
    items = {}
    for cls_page in ("class1", "class2"):
        sp = (ROOT / "reports" / "devices" / "label_audit" / cls_page / "index.html").read_text()
        for it in json.loads(re.search(r"var ITEMS=(\[.*?\]), CHECKS=", sp, re.S).group(1)):
            items[it["id"]] = it["label"]
    keep = AL.in_doubt_ids(); res_cls = collections.defaultdict(collections.Counter)
    for fp in sorted((RES / "review_marks").glob("*.json")):
        d = json.loads(fp.read_text())
        for k, v in (("fine", "right"), ("wrong", "wrong"), ("unclear", "unclear")):
            for cid in d.get(k, []):
                if cid in keep and cid in items:
                    res_cls[items[cid]][v] += 1

    # ---------- pieces (report standard: one white page, plain tables, one flow)
    def table(head, rows, total=None, num=()):
        c = lambda k: ' class="n"' if k in num else ""
        th = "".join(f"<th{c(k)}>{h}</th>" for k, h in enumerate(head))
        tr = lambda r, cl="": f"<tr{cl}>" + "".join(f"<td{c(k)}>{v}</td>" for k, v in enumerate(r)) + "</tr>"
        return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{"".join(tr(r) for r in rows)}{tr(total, " class=tot") if total else ""}</tbody></table></div>'

    def bar(parts, total):
        return '<div class="bar">' + "".join(f'<i class="{c}" style="width:{100 * v / max(1, total):.2f}%" title="{v:,}"></i>' for c, v in parts if v) + "</div>"

    pc = lambda n, t: f"{100 * n / max(1, t):.0f}%"
    nd = NA["verdicts"].get("device", 0) if NA else 0; nn = NA["verdicts"].get("no device", 0) if NA else 0; nu = NA["verdicts"].get("unclear", 0) if NA else 0
    n_other = NA["device_classes"].get("Other", 0) if NA else 0
    n_known = sum(v for c, v in NA["device_classes"].items() if c not in ("Other", "no class given")) if NA else 0

    # ---------- the reviewers' notes on the unclear places: is a device there?  (and example pictures)
    import base64, openpyxl, yaml
    DEV = "a device is there: hidden, partly visible, of unknown type, or the box covers more than one device or only part of one"
    OTH = "no reason given, or another reason"
    def why(n):                                                 # two groups only (the user, 2026-10-26)
        n = (n or "").lower().strip()
        if not n or ("cable manager" in n and "cover" not in n):
            return OTH
        if any(k in n for k in ("unknown", "unclear to tell", "not sure", "cannot", "can't", "cover", "hidden", "visible", "visable", "water", "panel", "hand", "cables", "not clear",
                                "blur", "dark", "two device", "many device", "more device", "more than", "not a single", "not a 1", "multiple", "complete", "compelete", "incomplete",
                                "detect", "etect", "cropped", "outside", "out of the rack", "not part of", "not mapped", "single")):
            return DEV
        return OTH
    WHY_ORDER = [DEV, OTH]
    DEV_WHY = [DEV]                                             # notes that still say a device is there
    ND = RES / "review_marks" / "network_team"
    nl_rows = list(csv.DictReader(open(ND / "nolabel_part1.csv", encoding="utf-8-sig")))
    it_ = openpyxl.load_workbook(ND / "nolabel_part2.xlsx", read_only=True).active.iter_rows(values_only=True); head = [str(h).strip() for h in next(it_)]
    nl_rows += [dict(zip(head, ["" if v is None else str(v) for v in r])) for r in it_ if any(v not in (None, "") for v in r)]
    nl_unc = [r for r in nl_rows if (r.get("verdict") or "").strip() == "Unclear"]; nl_why = collections.Counter(why(r.get("note")) for r in nl_unc)
    vr_rows = []
    for k in (1, 2):
        vr_rows += list(csv.DictReader(open(ND / f"verify_part{k}.csv", encoding="utf-8-sig")))
    vr_unc = [r for r in vr_rows if r["decision"].strip() == "Unclear"]; vr_why = collections.Counter(why(r.get("note")) for r in vr_unc)
    crops = {}
    for k in (1, 2):
        sp = (PKG / f"nolabel_review_{k}" / "index.html").read_text()
        crops.update({i["id"]: PKG / f"nolabel_review_{k}" / i["crop"] for i in json.JSONDecoder().raw_decode(sp, sp.index("var ITEMS=") + 10)[0]})
    tiles_v = {}
    for k in (1, 2):
        sp = (PKG / f"verify_labels_{k}" / "index.html").read_text()
        tiles_v.update({i["id"]: PKG / f"verify_labels_{k}" / i["img"] for i in json.loads(re.search(r"var ITEMS=(\[.*?\]), NAMES=", sp, re.S).group(1))})
    pic = lambda path, cap: f'<figure><img src="data:image/jpeg;base64,{base64.b64encode(path.read_bytes()).decode()}" alt="{e(cap)}"><figcaption>{e(cap[:100])}</figcaption></figure>'
    # labels per class in the dataset today
    names = yaml.safe_load(open(AL.DATA / "data.yaml"))["names"]; names = [names[i] for i in sorted(names)] if isinstance(names, dict) else list(names)
    cls_before = collections.Counter()
    for f in (AL.DATA / "labels").glob("*.txt"):
        for l in f.read_text().split("\n"):
            p_ = l.split()
            if len(p_) > 5:
                cls_before[names[int(p_[0])]] += 1

    # ---------- visual pieces (report standard: white page; the goal drawn, not tabled)
    def bw(frac):                                               # a bar width that leaves room for its number
        return f"width:calc((100% - 120px) * {max(.004, frac):.4f})"

    def funnel(rows, cls="a"):                                  # rows: (label, n, sub); to scale of the first row; "x% of the row above"
        top = max(n for _, n, _ in rows) or 1; out = '<div class="fun">'
        for k, (l, n, s_) in enumerate(rows):
            share = f'<small>{pc(n, rows[k - 1][1])} of the row above</small>' if k else ""
            out += f'<div class="fr"><div class="fl"><b>{l}</b><span>{s_}</span></div><div class="ft"><i class="{cls}" style="{bw(n / top)}"></i><em>{n:,}{share}</em></div></div>'
        return out + "</div>"

    def stack(parts):                                           # parts: (cls, n, label)
        tot = sum(n for _, n, _ in parts) or 1
        return ('<div class="bar">' + "".join(f'<i class="{c}" style="width:{100 * n / tot:.2f}%" title="{l}: {n:,}"></i>' for c, n, l in parts if n) + "</div>"
                + '<ul class="leg">' + "".join(f'<li><i class="sw {c}"></i><b>{n:,}</b><span>{l}</span></li>' for c, n, l in parts if n) + "</ul>")

    def step(no, who, when, n_in, in_label, parts):
        return (f'<div class="step"><div class="sh"><span class="sn">{no}</span><div><b>{who}</b><small>{when}</small></div></div>'
                f'<div class="io"><div class="inb"><span>In</span><b>{n_in:,}</b><small>{in_label}</small></div><span class="ar">&rarr;</span><div class="outb"><span>Out</span>{stack(parts)}</div></div></div>')

    def bigs(items):                                            # (cls, n, label, of)
        return '<div class="bigs">' + "".join(f'<div class="bg k{c}"><b>{n if isinstance(n, str) else format(n, ",")}</b><span>{l}</span>{f"<small>{pc(n, of)} of {of:,}</small>" if of else ""}</div>' for c, n, l, of in items) + "</div>"

    def flowtree(node, parent=None):
        """A compact left-to-right flowchart: node = (number, label, colour class, children). Each box shows the number, what it is,
        and its share of the box it comes from (the first box shows none)."""
        n_, label, c, kids = node
        share = (f'<div class="fbar"><i class="{c}" style="width:{max(1.5, 100 * n_ / max(1, parent)):.1f}%"></i></div><small>{pc(n_, parent)} of {parent:,}</small>'
                 if parent else "")
        box = f'<div class="fn {c}"><b>{n_:,}</b><span>{label}</span>{share}</div>'
        if not kids:
            return f'<div class="tn">{box}</div>'
        return f'<div class="tn">{box}<div class="kids">' + "".join(f'<div class="kid">{flowtree(k_, n_)}</div>' for k_ in kids) + "</div></div>"

    def before_after(rows, note=""):                            # rows: (before, after, n, cls); bars to scale of the largest
        top = max(n for _, _, n, _ in rows) or 1
        return ('<div class="ba"><div class="bah"><span>Labelled as</span><span></span><span>Really</span><span>Labels</span></div>'
                + "".join(f'<div class="bar-row"><span class="bef">{b}</span><span class="ar">&rarr;</span><span class="aft">{a}</span><div class="ft"><i class="{c}" style="{bw(n / top)}"></i><em>{n:,}</em></div></div>' for b, a, n, c in rows)
                + "</div>" + (f'<p class="note">{note}</p>' if note else ""))

    def hbars(rows, cls="a"):                                   # (label, n) or (label, n, cls), to scale
        top = max(r[1] for r in rows) or 1
        return '<div class="hb">' + "".join(f'<div class="hr"><span>{r[0]}</span><div class="ft"><i class="{r[2] if len(r) > 2 else cls}" style="{bw(r[1] / top)}"></i><em>{r[1]:,}</em></div></div>' for r in rows) + "</div>"

    def change(rows):                                           # (label, before, after): before -> after, and the change as a bar centred on zero
        top = max(abs(a - b) for _, b, a in rows) or 1
        out = '<div class="ch"><div class="chh"><span>Class</span><span class="n">Before</span><span class="n">After</span><span class="c">Removed &larr; change &rarr; added</span></div>'
        for l, b, a in rows:
            d = a - b; w = 40 * abs(d) / top                     # 40% of the half leaves room for the number
            bar = (f'<i class="r" style="right:50%;width:{w:.2f}%"></i>' if d < 0 else f'<i class="g" style="left:50%;width:{w:.2f}%"></i>') if d else ""
            ds = f"+{d:,}" if d > 0 else f"{d:,}"
            out += (f'<div class="cr"><span>{l}</span><span class="n">{b:,}</span><span class="n"><b>{a:,}</b></span>'
                    f'<div class="cz"><span class="zero"></span>{bar}<em style="{"left" if d >= 0 else "right"}:calc(50% + {w:.2f}% + 6px)">{ds}</em></div></div>')
        return out + "</div>"

    def unclear_block(n_unc, counter, examples, what):
        dev = sum(counter.get(k, 0) for k in DEV_WHY); none = counter.get(OTH, 0)
        return (f'<h3>The {n_unc:,} unclear {what}: is a device there?</h3>'
                f'<p>In <b>{dev:,}</b> of the {n_unc:,}, the reviewer\'s note says a device is there but it could not be judged. The other {none:,} have no reason, or another reason.</p>'
                + hbars([(k, counter[k], "am" if k in DEV_WHY else "u") for k in WHY_ORDER if counter.get(k)])
                + (f'<div class="pics">{"".join(examples)}</div>' if examples else ""))

    def banner(kicker, title, text, stats):                    # stats: (number, label, colour class)
        stat = ('<div class="bs">' + "".join(f'<div class="s{c}"><b>{n}</b><span>{l}</span></div>' for n, l, c in stats) + "</div>") if stats else ""
        return f'<div class="bn"><span class="bk">{kicker}</span><h2 class="bt">{title}</h2><p>{text}</p>{stat}</div>'

    sec = lambda n, t, b, lead="": f'<section><h2><span>{n}</span>{t}</h2>{f"<p class=lead2>{lead}</p>" if lead else ""}{b}</section>'

    # ================= shared: the families of the Other devices, answers by place, pictures
    dc = NA["device_classes"] if NA else {}
    nl_none = (NA["sent"] - NA["answered"] + NA["verdicts"].get("no answer", 0)) if NA else 0
    FAMILIES = [("Computers and mini PCs", ("desktop", "computer", "pc", "raspberry", "workstation", "chassis", "tower", "case", "console", "server", "hyperconverged", "processor", "printer")),
                ("Video recorders (NVR, DVR)", ("nvr", "dvr", "recorder", "recoder", "recoreder", "video")),
                ("Audio equipment", ("amplifier", "amplifer", "audio", "tuner", "speaker", "sound", "dsp", "signal processor", "mixer", "streamer", "player", "driverack", "pre amp", "pre-amp")),
                ("Controllers and cloud keys", ("controller", "contoller", "contraoller", "cotroller", "cloud key", "cloudkey", "control unit", "control", "plc")),
                ("Modems, phone systems and network boxes", ("modem", "ont", "onu", "router", "gateway", "repeater", "wifi", "wireless", "transmit", "receiver", "reciver", "access", "converter", "media",
                                                             "fiber", "fibre", "optical", "multiplexer", "mux", "network", "sd-wan", "sd wan", "vpn", "pbx", "pabx", "voip", "telephone", "phone",
                                                             "ethernet", "switch", "distribution", "patch", "kvm", "modulator", "encoder", "decoder", "matrix", "hub", "poe", "trunk", "intercom", "antenna")),
                ("Power: batteries, conditioners, supplies", ("battery", "power", "rectifier", "ups", "psu", "rps", "supply", "conditioner", "breaker", "regulator", "pdu", "inverter", "multiverter")),
                ("Monitoring and sensors", ("monitor", "sensor", "probe", "thermo", "hygro", "temperature", "environment", "security", "cooling", "fan", "display", "analyzer", "enclosure"))]
    def families(named):
        f = collections.Counter()
        for k_, v_ in named.items():
            if k_ == "not typed":
                f["not named by the reviewer"] += v_
            elif k_ == "unknown":
                f["named \"unknown\""] += v_
            else:
                f[next((n_ for n_, keys in FAMILIES if any(x in k_ for x in keys)), "other names")] += v_
        return [(n_, f[n_]) for n_, _ in FAMILIES if f[n_]] + [(k_, f[k_]) for k_ in ("other names", "named \"unknown\"", "not named by the reviewer") if f[k_]]
    fam_nl = families(NA["other_typed"]) if NA else []
    fam_vr = families(VR["other_typed"]) if VR else []
    def pics(rows):
        return f'<div class="pics">{"".join(rows)}</div>' if rows else ""
    def questions(items):
        return '<div class="qs"><b>Questions for the network team</b><ol>' + "".join(f"<li>{q}</li>" for q in items) + "</ol></div>"
    # example pictures: one per image and, where it applies, one per class, so the proofs show different cases
    def famof(name):
        n_ = (name or "").lower()
        return next((f_ for f_, keys in FAMILIES if any(x in n_ for x in keys)), None)
    Vn = {"Device here, needs a label": "device", "No device here": "no device", "Unclear": "unclear"}
    px_known, px_other, px_nodev, px_unc, seen_ph, seen_k = [], [], [], [], set(), set()
    def take(lst, path, cap, photo, key=None, n=4):
        if len(lst) >= n or photo in seen_ph or (key and key in seen_k):
            return
        lst.append(pic(path, cap)); seen_ph.add(photo)
        if key:
            seen_k.add(key)
    for r in nl_rows:
        a_ = r["area"].strip(); v_ = Vn.get((r.get("verdict") or "").strip()); cl = (r.get("class") or "").strip(); oc = (r.get("other_class") or "").strip(); nt = (r.get("note") or "").strip()
        if a_ not in crops:
            continue
        ph = r["photo"].strip()
        if v_ == "device" and cl and cl != "Other":
            take(px_known, crops[a_], f"missing label: {cl}", ph, "k" + cl)
        elif v_ == "device" and cl == "Other" and famof(oc):
            take(px_other, crops[a_], f"missing label, outside our classes: {oc}", ph, "o" + famof(oc))
        elif v_ == "no device" and nt:
            take(px_nodev, crops[a_], f"no device: {nt}", ph)
        elif v_ == "unclear" and nt and why(nt) == DEV:
            take(px_unc, crops[a_], f"unclear: {nt}", ph)
    px_move, px_wother, px_notdev, px_vunc = [], [], [], []
    for r in vr_rows:
        c_ = r["card"].strip(); d_ = r["decision"].strip(); lab = r["label_in_dataset"].strip(); rc = (r.get("right_class") or "").strip(); oc = (r.get("other_class") or "").strip()
        nt = (r.get("note") or "").strip(); ph = r["photo"].strip()
        if c_ not in tiles_v:
            continue
        if d_ == "Label wrong" and rc and rc not in ("Other", "Not a device"):
            take(px_move, tiles_v[c_], f"labelled {lab}, really {rc}", ph, "m" + lab + rc)
        elif d_ == "Label wrong" and rc == "Other":
            take(px_wother, tiles_v[c_], f"labelled {lab}, really outside our classes" + (f": {oc}" if oc else ""), ph, "w" + lab)
        elif d_ == "Label wrong" and rc == "Not a device":
            take(px_notdev, tiles_v[c_], f"labelled {lab}, not a device", ph, n=1)
        elif d_ == "Unclear":
            take(px_vunc, tiles_v[c_], f"labelled {lab}, now unclear" + (f": {nt}" if nt else ""), ph, "u" + lab)

    # ================= tab 1: the dataset issue (one export of seven)
    I_ = json.load(open(RES / "export_issues.json")); bad_exp = next(iter(I_["exports"])); bad_short = e(short(bad_exp))
    T_ = {int(k): v for k, v in V["table"].items()}
    shifted, matched = V["shifted"], V["matched"]
    iimg = PKG / "label_audit" / "issue_img"
    v6 = {c: k for k, c in T_.items()}   # real class -> the number the bad export uses for it
    others = ", ".join(e(short(x)) for x in per_export if x != bad_exp)
    pairs = "".join(f'<div class="pair"><figure><img src="data:image/jpeg;base64,{base64.b64encode((iimg / x["files"][0]).read_bytes()).decode()}" alt="the device in {bad_short}">'
                    f'<figcaption><b>{bad_short}</b>: class number {x["ids"][0]}, which its own class list calls <s>{e(names[x["ids"][0]])}</s></figcaption></figure>'
                    f'<figure><img src="data:image/jpeg;base64,{base64.b64encode((iimg / x["files"][1]).read_bytes()).decode()}" alt="the same device in another export">'
                    f'<figcaption><b>{e(short(x["other"]))}</b>, the same image: <b>{e(names[x["ids"][1]])}</b></figcaption></figure></div>'
                    for x in V["examples"][:2] if (iimg / x["files"][0]).exists() and (iimg / x["files"][1]).exists())
    t1 = (sec("1", "The same images in other exports",
              f'<p>{shifted} of the {matched} devices that also appear in another export are exactly one class off there.</p>'
              + stack([("r", shifted, "exactly one class off"), ("o", matched - shifted, "other")]) + pairs)
          + sec("2", "Class numbers in the seven exports",
                f'<p>All seven class lists say the same. The labels of {bad_short} use other numbers for 11 of the 12 classes (in red). The other six exports: {others}.</p>'
                + '<div class="idt">' + table(["Class", "Other six exports", f"{bad_short} labels"],
                                               [[e(names[c]), f"{c}", f'<span class="off">{v6[c]}</span>' if v6[c] != c else f"{c}"] for c in range(12)], num=(1, 2)) + "</div>")
          + sec("3", "The fix", f'<p>Moving every class number back to its class. Our device model then disagrees with {V["rate_after"]["pct"]}% of its labels, instead of {V["rate_before"]["pct"]}%.</p>')
          + questions([f"Which class order was used when {bad_short} was labelled? Please confirm the renumbering.",
                       f"Were any other labels in {bad_short} exported with a different class list?"]))
    tab_ds = banner("1 &middot; Dataset issue", f"Export {bad_short} uses the wrong class numbers",
                    f"Six exports number the 12 classes the same way. {bad_short} uses a different order from its own class list, so its labels read one class off.",
                    [(f"{V['rate_before']['pct']}%", "of its labels wrong as exported", "r"), (f"{shifted} of {matched}", "devices one class off in another export", "r"), (f"{per_export[bad_exp][1]:,}", f"labels in {per_export[bad_exp][0]:,} images", "a")]) + t1

    # ================= tab 2: no label
    nd_img = len({r["photo"].strip() for r in nl_rows if (r.get("verdict") or "").strip() == "Device here, needs a label"})   # images with a confirmed missing label
    nl_dev_note = sum(nl_why.get(k, 0) for k in DEV_WHY)
    flow_nl_chart = ('<div class="ftw">' + flowtree((len(NL), "places with no label, sent to review", "nt", [
        (nd, "right: a device is there, its label is missing", "g", [(n_known, "in one of our 12 classes", "g", []), (n_other + dc.get("no class given", 0), f"outside our 12 classes (Other), {dc.get('no class given', 0)} of them with no class", "g2", [])]),
        (nn, "wrong: no device", "r", []),
        (nu, "unclear", "u", [(nl_dev_note, "the note says a device is there", "am", []), (nu - nl_dev_note, "no reason, or another reason", "u", [])]),
        (nl_none, "no answer", "o", [])])) + "</div>") if NA else ""
    t2 = (flow_nl_chart
          + sec("1", f"The {nd:,} devices with no label",
                '<div class="cols"><div><h3>In our 12 classes: ' + f"{n_known:,}</h3>" + hbars([(e(c), v) for c, v in dc.items() if c not in ("Other", "no class given")], "g") + pics(px_known[:2]) + "</div>"
                + '<div><h3>Outside our 12 classes: ' + f"{n_other:,}</h3>" + hbars(fam_nl, "g2") + pics(px_other[:2]) + "</div></div>")
          + sec("2", f"The {nu:,} unclear places",
                f'<div class="callout"><b>The network team labelled these images and reviewed them,</b> yet could not say whether a device is there. In {nl_dev_note:,} their own note says a device is there.</div>'
                + pics(px_unc[:4]))
          + questions([f"Why were {n_known:,} devices of our 12 classes left without a label?",
                       f"Should the {n_other:,} devices outside our 12 classes get a new class (Other), or be left out?",
                       f"What should the {nu:,} unclear places be?"]))
    tab_nl = banner("2 &middot; No label", f"{nd:,} devices on the rack had no label",
                    f"Our search found {len(NL):,} places with no label, in every image of the dataset. The network team checked each one.",
                    [(f"{nd:,}", f"devices with no label: {nd / n_inst * 100:.1f}% on top of the {n_inst:,} labelled devices", "g"),
                     (f"{n_known:,}", f"of them in our 12 classes: {n_known / n_inst * 100:.1f}% of the labelled devices", "g"),
                     (f"{nd_img:,}", f"images with a missing label: {pc(nd_img, n_img)} of all {n_img:,} images", "a")]) + t2

    # ================= tab 3: class mislabelled (images of 1 MP or more)
    tot_r, tot_w, tot_u = r_ok + vrr, vrw, vru; tot_o = n_sent - tot_r - tot_w - tot_u
    vr_dev_note = sum(vr_why.get(k, 0) for k in DEV_WHY)
    flow_cv_chart = ('<div class="ftw">' + flowtree((n_mp_inst, f"labels in the {n_mp:,} images of 1 MP or more", "nt", [
        (n_mp_inst - n_sent, "class confirmed by our device model, not sent", "o", []),
        (n_sent, "class in doubt, sent to review", "a", [
        (tot_r, "right", "g", []),
        (tot_w, "wrong", "r", [(v_move, "really another of our 12 classes", "a", []), (v_other, "really outside our 12 classes (Other)", "g2", []), (v_notdev, "not a device", "r", [])]),
        (tot_u, "unclear", "u", [(vr_dev_note, "the note says a device is there", "am", []), (tot_u - vr_dev_note, "no reason, or another reason", "u", [])]),
        (tot_o, "no answer", "o", [])])])) + "</div>") if VR else ""
    unc_cls = sorted(((k, d.get("unclear", 0)) for k, d in VR["by_label_class"].items()), key=lambda t: -t[1]) if VR else []
    moves = [(k, v) for k, v in VR["corrections"].items() if not k.endswith(("-> Other", "-> Not a device"))] if VR else []
    t3 = (flow_cv_chart
          + (sec("1", f"The {tot_w:,} wrong labels",
                 '<div class="cols"><div><h3>Another of our 12 classes: ' + f"{v_move:,}</h3>"
                 + before_after([(e(k.split(" -> ")[0]), e(k.split(" -> ")[1]), v, "a") for k, v in moves[:6]]) + pics(px_move[:2]) + "</div>"
                 + '<div><h3>Outside our 12 classes: ' + f"{v_other:,}</h3>" + hbars(fam_vr, "g2") + pics(px_wother[:2]) + "</div></div>")
             + sec("2", f"The {tot_u:,} unclear labels",
                   '<div class="callout"><b>The network team drew each of these labels with a clear class,</b> and now marks them unclear.</div>'
                   + '<h3>By the class they had drawn (top six)</h3>' + hbars([(e(k), v) for k, v in unc_cls if v][:6], "u") + pics(px_vunc[:4])) if VR else "")
          + questions([f"What caused the {v_move:,} class swaps, such as Server and Storage unit, or Router and Gateway?",
                       f"Should the {v_other:,} labels outside our 12 classes move to a new class (Other)?",
                       f"For the {tot_u:,} unclear labels: is the class you drew right, or should the label be removed?"]))
    tab_cv = banner("3 &middot; Class mislabelled", f"{tot_w:,} labels have the wrong class and {tot_u:,} are now unclear",
                    f"In the {n_mp:,} images of 1 MP or more, {n_sent:,} of the {n_mp_inst:,} labels had a class in doubt. Each one was checked.",
                    [(f"{tot_w:,}", f"wrong: {tot_w / n_mp_inst * 100:.1f}% of all {n_mp_inst:,} labels in these images", "r"),
                     (f"{tot_u:,}", f"unclear: {tot_u / n_mp_inst * 100:.1f}% of all {n_mp_inst:,} labels", "u"),
                     (f"{tot_w + tot_u:,}", f"wrong or unclear: {(tot_w + tot_u) / n_mp_inst * 100:.1f}% of all labels", "r")]) + t3

    tabs_nav = ('<nav class="tabs" role="tablist" aria-label="Report sections">'
                + "".join(f'<button type="button" role="tab" data-t="{k}" aria-selected="{"true" if i == 0 else "false"}"><i>{i + 1}</i><span>{t}<small>{h}</small></span></button>'
                          for i, (k, t, h) in enumerate([("dataset", "Dataset issue", f"export {bad_short}"), ("nolabel", "No label", f"{nd:,} devices"), ("classver", "Class mislabelled", f"{tot_w:,} wrong, {tot_u:,} unclear")]))
                + "</nav>")
    body = (f'<div id="dataset" class="tab on" role="tabpanel">{tab_ds}</div><div id="nolabel" class="tab" role="tabpanel">{tab_nl}</div><div id="classver" class="tab" role="tabpanel">{tab_cv}</div>')
    page = f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Device Dataset Review</title>
<style>
{STYLE}
.lead2 {{ color:var(--muted); margin:-4px 0 14px; }} h3 {{ margin:24px 0 8px; }}
.strip {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); border:1px solid var(--line); border-radius:10px; overflow:hidden; margin:0 0 10px; }} @media (max-width:800px) {{ .strip {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
.strip div {{ padding:12px 16px; border-right:1px solid var(--line); }} .strip div:last-child {{ border-right:0; }} .strip b {{ display:block; font-size:24px; font-variant-numeric:tabular-nums; }} .strip span {{ color:var(--muted); font-size:14px; }}
.ds {{ margin:0 0 18px; }} .ds summary {{ font-size:14px; color:var(--accent); cursor:pointer; }} .ds table {{ margin-top:12px; }}
.hd {{ position:sticky; top:0; z-index:10; background:#fff; border-bottom:1px solid var(--line); box-shadow:0 2px 10px rgba(17,24,39,.06); }}
.hin {{ width:90vw; margin:0 auto; display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:8px 24px; padding:8px 0; }} @media (max-width:800px) {{ .hin {{ width:auto; padding:8px 16px; }} }}
.brand {{ display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }} .brand b {{ font-size:16px; }} .brand span {{ font-size:15px; color:#374151; }} .brand small {{ font-size:12px; color:var(--muted); }}
.tabs {{ display:flex; gap:6px; background:#f3f4f6; border-radius:12px; padding:4px; overflow-x:auto; max-width:100%; }}
.tabs button {{ display:flex; align-items:center; gap:10px; font:600 15px -apple-system,"Segoe UI",Roboto,Arial,sans-serif; color:#374151; background:none; border:0; border-radius:9px; padding:7px 14px; cursor:pointer; white-space:nowrap; text-align:left; }}
.tabs button i {{ font-style:normal; width:24px; height:24px; border-radius:50%; display:grid; place-items:center; background:#e5e7eb; color:#374151; font-size:13px; flex:0 0 24px; }}
.tabs button small {{ display:block; font-weight:400; font-size:12px; color:var(--muted); }}
.tabs button[aria-selected="true"] {{ background:#fff; color:var(--fg); box-shadow:0 1px 4px rgba(17,24,39,.14); }} .tabs button[aria-selected="true"] i {{ background:var(--accent); color:#fff; }}
.tabs button:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
.bn {{ background:linear-gradient(180deg,#f5f8ff,#fff); border:1px solid #dbe4fb; border-radius:14px; padding:20px 22px; margin:6px 0 22px; }}
.bk {{ font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.06em; color:var(--accent); }} .bt {{ font-size:24px; line-height:1.25; margin:4px 0 6px; display:block; }} .bn p {{ color:#374151; margin:0 0 14px; max-width:900px; }}
.bs {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr)); gap:12px; }} .bs div {{ background:#fff; border:1px solid var(--line); border-left:4px solid var(--grey); border-radius:10px; padding:10px 14px; }}
.bs b {{ display:block; font-size:24px; font-variant-numeric:tabular-nums; }} .bs span {{ font-size:13px; color:var(--muted); }}
.bs .sg {{ border-left-color:var(--green); }} .bs .sg2 {{ border-left-color:#6aa84f; }} .bs .sr {{ border-left-color:var(--red); }} .bs .su {{ border-left-color:var(--grey); }} .bs .sa {{ border-left-color:var(--accent); }}
section h2 span {{ display:inline-grid; place-items:center; min-width:28px; height:28px; border-radius:50%; background:var(--accent); color:#fff !important; font-size:13px !important; font-weight:700 !important; align-self:center; }}
.tab {{ display:none; }} .tab.on {{ display:block; }} .tab > section:first-child {{ border-top:0; margin-top:0; padding-top:0; }}
.fun {{ display:grid; gap:10px; }} .fr {{ display:grid; grid-template-columns:minmax(0,320px) minmax(0,1fr); gap:16px; align-items:center; }} @media (max-width:800px) {{ .fr {{ grid-template-columns:1fr; gap:4px; }} }}
.fl b {{ display:block; font-size:15px; }} .fl span {{ font-size:13px; color:var(--muted); }}
.ft {{ display:flex; align-items:center; gap:10px; min-width:0; }} .tr {{ flex:1; min-width:0; }} .ft i {{ display:block; height:28px; border-radius:6px; }} .ft em {{ font-style:normal; font-weight:600; font-size:17px; font-variant-numeric:tabular-nums; white-space:nowrap; }} .ft em small {{ font-weight:400; color:var(--muted); font-size:13px; }}
.a {{ background:var(--accent); }} .nt {{ background:#5b4fb0; }} .g {{ background:var(--green); }} .g2 {{ background:#6aa84f; }} .r {{ background:var(--red); }} .u {{ background:var(--grey); }} .o {{ background:var(--line); }} .o2 {{ background:#c7cdd6; }}
.steps {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr)); gap:16px; }}
.step {{ border:1px solid var(--line); border-radius:12px; padding:16px 18px; box-shadow:0 1px 2px rgba(17,24,39,.04); }}
.sh {{ display:flex; gap:12px; align-items:center; margin-bottom:14px; }} .sn {{ width:30px; height:30px; border-radius:50%; background:var(--fg); color:#fff; display:grid; place-items:center; font-weight:700; font-size:14px; }}
.sh b {{ display:block; font-size:16px; }} .sh small {{ color:var(--muted); font-size:13px; }}
.io {{ display:grid; grid-template-columns:150px 30px minmax(0,1fr); gap:12px; align-items:start; }} @media (max-width:600px) {{ .io {{ grid-template-columns:1fr; }} .io .ar {{ display:none; }} }}
.inb {{ background:var(--inset); border-radius:10px; padding:12px 14px; }} .inb span, .outb > span {{ display:block; font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); margin-bottom:4px; }}
.inb b {{ display:block; font-size:28px; font-variant-numeric:tabular-nums; line-height:1.1; }} .inb small {{ color:var(--muted); font-size:13px; }}
.io .ar {{ font-size:22px; color:var(--grey); text-align:center; padding-top:22px; }} .outb .bar {{ height:14px; margin:6px 0 10px; }}
.bar {{ display:flex; height:12px; border-radius:6px; overflow:hidden; background:var(--head); }} .bar i {{ display:block; height:100%; }}
.leg {{ list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:6px 14px; }} .leg li {{ display:flex; gap:8px; align-items:baseline; font-size:14px; }}
.leg b {{ font-variant-numeric:tabular-nums; }} .leg span {{ color:var(--muted); }} .sw {{ width:12px; height:12px; border-radius:3px; display:inline-block; flex:0 0 12px; align-self:center; }}
.bigs {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr)); gap:14px; margin-bottom:12px; }}
.bg {{ border:1px solid var(--line); border-top:4px solid var(--grey); border-radius:12px; padding:14px 18px; }} .bg.kg {{ border-top-color:var(--green); }} .bg.kr {{ border-top-color:var(--red); }} .bg.ku {{ border-top-color:var(--grey); }} .bg.ko2 {{ border-top-color:#c7cdd6; }} .bg.ka {{ border-top-color:var(--accent); }}
.bg b {{ display:block; font-size:34px; font-variant-numeric:tabular-nums; line-height:1.1; }} .bg span {{ display:block; font-size:15px; margin-top:2px; }} .bg small {{ color:var(--muted); font-size:13px; }}
.hb {{ display:grid; gap:6px; margin:8px 0; }} .hr {{ display:grid; grid-template-columns:minmax(0,340px) minmax(0,1fr); gap:10px; align-items:center; font-size:14px; }} .hr .ft i {{ height:16px; }} .hr .ft em {{ font-size:14px; font-weight:500; }}
@media (max-width:700px) {{ .hr {{ grid-template-columns:1fr; gap:2px; }} }}
.pb {{ display:grid; gap:8px; margin:8px 0; }} .pbh, .pr {{ display:grid; grid-template-columns:150px minmax(0,1fr) minmax(0,1fr); gap:12px; align-items:center; }}
.pbh span {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }} .pr {{ padding:5px 0; border-bottom:1px solid var(--line); }} .pr .pl {{ font-size:15px; }} .pr .ft i {{ height:18px; }} .pr .ft em {{ font-size:15px; }}
@media (max-width:700px) {{ .pbh {{ display:none; }} .pr {{ grid-template-columns:1fr; gap:3px; }} }}
.pics {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,220px),1fr)); gap:12px; margin-top:10px; }} figure {{ margin:0; }}
figure img {{ width:100%; height:130px; object-fit:contain; background:var(--inset); border:1px solid var(--line); border-radius:8px; display:block; }} figcaption {{ font-size:13px; color:var(--muted); margin-top:4px; }}
.ch {{ display:grid; gap:0; margin:8px 0; }} .chh, .cr {{ display:grid; grid-template-columns:minmax(0,170px) 90px 90px minmax(0,1fr); gap:12px; align-items:center; }}
.chh span {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); padding-bottom:6px; }} .chh .c {{ text-align:center; }}
.cr {{ padding:7px 0; border-top:1px solid var(--line); font-size:15px; }} .cr .n, .chh .n {{ text-align:right; font-variant-numeric:tabular-nums; }}
.cz {{ position:relative; height:22px; }} .cz .zero {{ position:absolute; left:50%; top:-4px; bottom:-4px; width:1px; background:#9ca3af; }}
.cz i {{ position:absolute; top:3px; height:16px; border-radius:4px; min-width:2px; }} .cz em {{ position:absolute; top:1px; font-style:normal; font-size:13px; font-weight:600; font-variant-numeric:tabular-nums; white-space:nowrap; }}
@media (max-width:700px) {{ .chh, .cr {{ grid-template-columns:minmax(0,1fr) 70px 70px; }} .chh .c {{ display:none; }} .cz {{ grid-column:1 / -1; }} }}
.am {{ background:#c98a12; }} .ft em small {{ display:block; font-weight:400; color:var(--muted); font-size:12px; white-space:normal; }}
.ba {{ display:grid; gap:4px; }} .bah, .bar-row {{ display:grid; grid-template-columns:minmax(0,1fr) 22px minmax(0,1fr) minmax(0,1.2fr); gap:8px; align-items:center; }}
.bah span {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }} .bar-row {{ padding:5px 0; border-bottom:1px solid var(--line); font-size:14px; }}
.bar-row .aft {{ font-weight:600; }} .bar-row .ar {{ color:var(--grey); text-align:center; }} .bar-row .ft i {{ height:14px; }} .bar-row .ft em {{ font-size:14px; }}
.ftw {{ overflow-x:auto; padding:4px 2px 10px; margin:0 0 6px; }}
.tn {{ display:flex; align-items:center; }} .kids {{ display:flex; flex-direction:column; gap:8px; padding-left:36px; position:relative; }}
.kids::before {{ content:""; position:absolute; left:0; top:50%; width:18px; border-top:2px solid #cbd5e1; }}
.kid {{ position:relative; }} .kid::before {{ content:""; position:absolute; left:-18px; top:0; bottom:0; border-left:2px solid #cbd5e1; }}
.kid:first-child::before {{ top:50%; }} .kid:last-child::before {{ bottom:50%; }} .kid:only-child::before {{ display:none; }}
.kid::after {{ content:""; position:absolute; left:-18px; top:50%; width:18px; border-top:2px solid #cbd5e1; }}
.fn {{ width:230px; flex:0 0 230px; background:#fff; border:1px solid var(--line); border-left:5px solid var(--grey); border-radius:10px; padding:8px 12px; box-shadow:0 1px 3px rgba(17,24,39,.06); }}
.fn b {{ display:block; font-size:20px; line-height:1.2; font-variant-numeric:tabular-nums; }} .fn span {{ display:block; font-size:13px; color:#374151; line-height:1.3; }}
.fn small {{ font-size:12px; color:var(--muted); }} .fbar {{ height:6px; background:#f1f5f9; border-radius:3px; margin:6px 0 2px; overflow:hidden; }} .fbar i {{ display:block; height:100%; border-radius:3px; }}
.fn.g {{ border-left-color:var(--green); background:#fff; }} .fn.g2 {{ border-left-color:#6aa84f; background:#fff; }} .fn.r {{ border-left-color:var(--red); background:#fff; }} .fn.u {{ border-left-color:var(--grey); background:#fff; }}
.fn.am {{ border-left-color:#c98a12; background:#fff; }} .fn.a {{ border-left-color:var(--accent); background:#fff; }} .fn.nt {{ border-left-color:#5b4fb0; background:#fff; }} .fn.o {{ border-left-color:#d1d5db; background:#fff; }}
.qs {{ background:#f5f8ff; border:1px solid #dbe4fb; border-left:4px solid var(--accent); border-radius:10px; padding:14px 18px; margin:28px 0 6px; }} .qs b {{ font-size:16px; }} .qs ol {{ margin:8px 0 0; padding-left:20px; }} .qs li {{ margin:4px 0; }}
.pair {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:0 0 14px; }} .pair img {{ height:170px; }} .pair s {{ color:var(--red); }}
.idt {{ max-width:620px; }} .idt .off {{ color:var(--red); font-weight:700; }}
.callout {{ border:1px solid #f3d0d0; border-left:4px solid var(--red); border-radius:10px; padding:14px 18px; margin:14px 0; background:#fff7f7; }}
.hr span small {{ color:var(--muted); font-size:12px; }}
.names {{ font-size:14px; color:var(--muted); margin-top:8px; }}
</style>
<header class="hd"><div class="hin"><div class="brand"><b>RackTrack</b><span>Device Dataset Review</span><small>updated {VERIFIED if VR else ANSWERED}</small></div>{tabs_nav}</div></header>
<main>
<h1>Device dataset review: {(vrw if VR else r_bad):,} wrong labels and {nd:,} missing devices found</h1>
<p class="lead">For the network team: what we found in the seven exports, with proof, and the questions we need answered.</p>
{body}
<footer>Built from the dataset, results/devices/review_results.json, verify_results.json, nolabel_strict.json, nolabel_results.json and the network team's answer files.</footer>
</main>
<script>
document.querySelectorAll('.tabs button').forEach(function(b){{ b.addEventListener('click', function(){{
  document.querySelectorAll('.tabs button').forEach(function(x){{ x.setAttribute('aria-selected', x===b ? 'true' : 'false'); }});
  document.querySelectorAll('.tab').forEach(function(t){{ t.classList.toggle('on', t.id===b.dataset.t); }});
  history.replaceState(null, '', '#' + b.dataset.t); window.scrollTo({{top: 0}}); }}); }});
(function(){{ var h = location.hash.slice(1); var b = document.querySelector('.tabs button[data-t="' + h + '"]'); if (b) b.click(); }})();
</script>
"""
    assert "–" not in page and "—" not in page, "long dash on the page"
    OUT.mkdir(parents=True, exist_ok=True); (OUT / "index.html").write_text(page)
    print(f"written {OUT / 'index.html'} ({len(page) / 1e6:.2f} MB)")


if __name__ == "__main__":
    main()
