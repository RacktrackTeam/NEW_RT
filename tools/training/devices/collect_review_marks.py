"""Collect the reviewers' marks and write the review results for the Device Label Review report.

Put each reviewer's marks in results/devices/review_marks/, named after the reviewer, in any of these forms:
  - <name>.json  the text from "Copy marks and notes" ({"fine": [...], "wrong": [...], "unclear": [...], "notes": {...}});
                 several pastes may go in <name>_1.json, <name>_2.json ... (marks of the same card: the last file wins)
  - <name>.zip   the file from "Download my marks with pictures" (its marks.csv is read)
  - <name>.csv   a marks.csv taken out of such a zip
Every card id is looked up on the two review pages, so each mark gets its tab, label class and photo. Only instances still sent to
review count (audit_labels.in_doubt_ids): a pair of labels of the same class on one device is no longer flagged (the user, 2026-10-21).

    python3 scripts/devices/collect_review_marks.py       # -> results/devices/review_results.json + review_wrong_labels.csv,
                                                           #    then rebuilds the report
"""
import collections, csv, io, json, pathlib, re, subprocess, sys, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "devices")); sys.path.insert(0, str(ROOT / "src"))
import audit_labels as AL   # noqa: E402
RES = ROOT / "results" / "devices"
MARKS = RES / "review_marks"
PAGES = ROOT / "reports" / "devices" / "label_audit"
TABS = [("tocheck", "To check"), ("unconf", "Not confirmed"), ("nolabel", "No label"), ("correct", "100 % correct")]
VERDICT = {"fine": "right", "label right": "right", "wrong": "wrong", "label wrong": "wrong", "unclear": "unclear"}


def cards():
    out = {}
    for page in sorted(PAGES.glob("class*/index.html")):
        s = page.read_text()
        for it in json.loads(re.search(r"var ITEMS=(\[.*?\]), CHECKS=", s, re.S).group(1)):
            out[it["id"]] = {"tab": it["check"], "label": it["label"], "photo": it["photo"], "page": page.parent.name}
    return out


def read(fp):
    """{card id: (verdict, note)} from one marks file."""
    if fp.suffix == ".json":
        d = json.loads(fp.read_text()); notes = d.get("notes", {})
        return {cid: (VERDICT[k], notes.get(cid, "")) for k in ("fine", "wrong", "unclear") for cid in d.get(k, [])} | \
               {cid: ("", n) for cid, n in notes.items() if not any(cid in d.get(k, []) for k in ("fine", "wrong", "unclear"))}
    text = zipfile.ZipFile(fp).read("marks.csv").decode("utf-8-sig") if fp.suffix == ".zip" else fp.read_text(encoding="utf-8-sig")
    return {r["card"]: (VERDICT.get(r["your_mark"].lower(), ""), r.get("note", "")) for r in csv.DictReader(io.StringIO(text))}


def nolabel_answers():
    """The network team's No label answers (review_marks/network_team/nolabel_part1.csv, nolabel_part2.xlsx) -> results/devices/nolabel_results.json,
    each answer joined to its place in nolabel_strict.json (evidence, image size)."""
    import openpyxl
    D = MARKS / "network_team"; rows = []
    for fp in sorted(D.glob("nolabel_part*")):
        if fp.suffix == ".csv":
            rows += list(csv.DictReader(open(fp, encoding="utf-8-sig")))
        elif fp.suffix == ".xlsx":
            it = openpyxl.load_workbook(fp, read_only=True).active.iter_rows(values_only=True); head = [str(h).strip() for h in next(it)]
            rows += [dict(zip(head, ["" if v is None else str(v) for v in r])) for r in it if any(v not in (None, "") for v in r)]
    if not rows:
        return None
    K = json.load(open(RES / "nolabel_strict.json"))["kept"]; byph = collections.defaultdict(list)
    for r in K:
        byph[r["photo"]].append(r)
    photo = {}
    for page in sorted((ROOT / "reports" / "devices").glob("nolabel_review_*/index.html")):
        s = page.read_text()
        photo.update({i["id"]: i["photo"] for i in json.JSONDecoder().raw_decode(s, s.index("var ITEMS=") + 10)[0]})
    V = {"Device here, needs a label": "device", "No device here": "no device", "Unclear": "unclear"}
    out = collections.Counter(); ev = collections.defaultdict(collections.Counter); size = collections.defaultdict(collections.Counter)
    cls = collections.Counter(); other = collections.Counter()
    for r in rows:
        a = r["area"].strip(); v = V.get(r["verdict"].strip(), "no answer"); out[v] += 1
        k = byph[photo[a]][int(a.rsplit("__N", 1)[1])]
        ev[k["evidence"]][v] += 1; size["1 MP or more" if k["megapixels"] >= 1 else "under 1 MP"][v] += 1
        if v == "device":
            c = (r.get("class") or "").strip() or "no class given"; cls[c] += 1
            if c == "Other":
                other[re.sub(r"[\s-]+", " ", (r.get("other_class") or "").strip().lower()) or "not typed"] += 1
    res = {"sent": len(K), "answered": len(rows), "verdicts": dict(out), "by_evidence": {k: dict(v) for k, v in ev.items()},
           "by_size": {k: dict(v) for k, v in size.items()}, "device_classes": dict(cls.most_common()), "other_typed": dict(other.most_common())}
    json.dump(res, open(RES / "nolabel_results.json", "w"), indent=1)
    print(f"No label answers: {len(rows):,} of {len(K):,}", dict(out))
    return res


def verify_answers():
    """The network team's Label verification answers (review_marks/network_team/verify_part*.csv) -> results/devices/verify_results.json,
    each answer joined to the AI team's mark (wrong or unclear) and the label's class."""
    rows = []
    for fp in sorted((MARKS / "network_team").glob("verify_part*.csv")):
        rows += list(csv.DictReader(open(fp, encoding="utf-8-sig")))
    if not rows:
        return None
    sent = {}
    for page in sorted((ROOT / "reports" / "devices").glob("verify_labels_*/index.html")):
        sent.update({i["id"]: i for i in json.loads(re.search(r"var ITEMS=(\[.*?\]), NAMES=", page.read_text(), re.S).group(1))})
    D = {"Label right": "right", "Label wrong": "wrong", "Unclear": "unclear"}
    out = collections.Counter(); by_ai = collections.defaultdict(collections.Counter); by_cls = collections.defaultdict(collections.Counter)
    moves = collections.Counter(); other = collections.Counter(); notes = collections.Counter()
    for r in rows:
        cid = r["card"].strip(); v = D.get(r["decision"].strip(), "no answer"); out[v] += 1
        ai = "wrong" if "wrong" in sent[cid]["why"] else "unclear"; by_ai[ai][v] += 1; by_cls[r["label_in_dataset"].strip()][v] += 1
        if v == "wrong":
            to = (r.get("right_class") or "").strip() or "no class given"
            moves[f'{r["label_in_dataset"].strip()} -> {to}'] += 1
            if to == "Other":
                other[re.sub(r"[\s-]+", " ", (r.get("other_class") or "").strip().lower()) or "not typed"] += 1
        if (r.get("note") or "").strip():
            notes[r["note"].strip().lower()] += 1
    res = {"sent": len(sent), "answered": len(rows), "decisions": dict(out), "by_ai_mark": {k: dict(v) for k, v in by_ai.items()},
           "by_label_class": {k: dict(v) for k, v in sorted(by_cls.items(), key=lambda t: -sum(t[1].values()))},
           "corrections": dict(moves.most_common()), "other_typed": dict(other.most_common()), "notes": dict(notes.most_common(30))}
    json.dump(res, open(RES / "verify_results.json", "w"), indent=1)
    print(f"Label verification answers: {len(rows):,} of {len(sent):,}", dict(out))
    return res


def main():
    C = cards(); keep = AL.in_doubt_ids(); marks = collections.defaultdict(dict); unknown = collections.Counter(); left_out = 0
    for fp in sorted(MARKS.glob("*")):
        if fp.suffix not in (".json", ".zip", ".csv") or fp.name.startswith("."):
            continue
        who = re.sub(r"_\d+$", "", fp.stem).capitalize()
        for cid, (v, note) in read(fp).items():
            if cid in C and cid not in keep:
                left_out += 1                                       # no longer in doubt
            elif cid in C:
                marks[who][cid] = (v, note)
            else:
                unknown[who] += 1
    rows, per, wrong = [], {}, []
    for tab, name in TABS:
        c = collections.Counter(v for who in marks for cid, (v, _) in marks[who].items() if C[cid]["tab"] == tab and v)
        rows.append({"category": name, "right": c["right"], "wrong": c["wrong"], "unclear": c["unclear"]})
    for who, m in marks.items():
        per[who] = {name: dict(collections.Counter(v for cid, (v, _) in m.items() if C[cid]["tab"] == tab and v)) for tab, name in TABS}
        total = collections.Counter(C[cid]["page"] for cid in C)
        per[who]["marked"] = sum(1 for v, _ in m.values() if v)
        for cid, (v, note) in m.items():
            if v == "wrong":
                wrong.append({"reviewer": who, "card": cid, "tab": dict(TABS)[C[cid]["tab"]], "label": C[cid]["label"], "photo": C[cid]["photo"], "note": note})
    n = sum(r["right"] + r["wrong"] + r["unclear"] for r in rows); nw = sum(r["wrong"] for r in rows)
    summary = (f"{n:,} labels marked by {len(marks)} reviewer{'s' if len(marks) != 1 else ''} so far: "
               f"{sum(r['right'] for r in rows):,} right, {nw:,} wrong, {sum(r['unclear'] for r in rows):,} unclear.")
    json.dump({"rows": rows, "per_reviewer": per, "summary": summary, "files": sorted(p.name for p in MARKS.glob("*") if not p.name.startswith("."))},
              open(RES / "review_results.json", "w"), indent=1)
    with open(RES / "review_wrong_labels.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["reviewer", "card", "tab", "label", "photo", "note"]); w.writeheader(); w.writerows(wrong)
    for who, m in marks.items():
        print(f"{who}: {per[who]['marked']:,} marks", {k: v for k, v in per[who].items() if k != "marked" and v})
    if left_out:
        print(f"{left_out} marks on instances no longer sent to review (same class twice on one device, confirmed by the model) left out")
    if unknown:
        print("card ids not on the review pages:", dict(unknown))
    nolabel_answers(); verify_answers()
    print(summary); subprocess.run([sys.executable, str(ROOT / "scripts" / "devices" / "build_review_report.py")], check=True)


if __name__ == "__main__":
    main()
