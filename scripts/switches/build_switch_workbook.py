"""Build Switch_Vendors_and_Models.xlsx: the switch list in the same layout as the router, firewall,
gateway and load-balancer catalogues, with the offline fixes applied and logged (2026-10-23).

    python3 scripts/switches/build_switch_workbook.py

Inputs (data/catalogues/):
  models_by_vendor.xlsx   the team's switch list of 23 Oct (8,855 names, 117 vendors; 'catalog' rows from the
                          vendor workbooks of 6 Oct, 'recovered' rows added later)
  switch_catalog.json     the export of those vendor workbooks: model name, product IDs, port counts, description
  vendors.json            the mapper's vendor file: aliases, domains, sitemaps

Fixes applied here, each written to the Change log sheet:
  - the one duplicate row dropped
  - order codes used as names replaced by the model name the vendor workbook gives for that code
  - names that are a series number or a spec, not a model, flagged (kept; the web check decides)
  - the five aliases claimed by two vendors settled: Aruba keeps the HP / ProCurve names (HPE's switch brand),
    Westermo keeps WESTERMO (Beijer Electronics owns the brand, the box says Westermo)
  - 'recovered' rows traced to the vendor workbook by name or product ID, or marked 'no trace'
  - vendor domains filled from vendors.json, and from product knowledge for the rest (marked 'check')

Online check: scripts/switches/site_check.py writes switch_site_checks.json (vendor-site pass through sitemaps);
when that file exists the builder fills Status, Source and 'Checked at (link)' from it, and logs each confirmation.
"""
import collections
import json
import re
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[2]
SW = ROOT / "data" / "catalogues"
OUT = SW / "Switch_Vendors_and_Models.xlsx"
FONT = "Arial"
HEAD = PatternFill("solid", fgColor="1F3864")

# domains for the vendors the team's list leaves blank (product knowledge: check on first use)
KNOWN_DOMAINS = {
    "3Com": "hpe.com", "Advantech Co. Ltd": "advantech.com", "Aerohive Switches": "extremenetworks.com",
    "Antaira Technologies": "antaira.com", "Arista Networks": "arista.com", "Asterfusion Data Technologies": "asterfusion.com",
    "ASUS": "asus.com", "AvaLan Wireless Systems Inc": "avalanwireless.com", "Beckhoff Automation": "beckhoff.com",
    "buffalo-technology": "buffalo.jp", "CERIO Corporation": "cerio.com.tw", "Delta Electronics (Agema Systems)": "deltaww.com",
    "Edgecore Networks": "edge-core.com", "Edimax Technology Co., Ltd": "edimax.com", "FS (FiberStore)": "fs.com",
    "Hikvision Digital Technology": "hikvision.com", "Intellisystem Technologies": "intellisystem.it",
    "IP Infusion (OcNOS)": "ipinfusion.com", "Korenix Technology": "korenix.com", "Lantech Communications": "lantechcom.tw",
    "Luxul": "luxul.com", "Nokia Networks": "nokia.com", "NoviFlow (NoviWare OS)": "noviflow.com",
    "Omnitron Systems Technology": "omnitron-systems.com", "Oracle Corporation": "oracle.com",
    "ORing Industrial Networking": "oringnet.com", "Perle Systems": "perle.com",
    "Phoenix Contact (including Hirschmann)": "phoenixcontact.com", "Proscend Communications Inc": "proscend.com",
    "QNAP Systems": "qnap.com", "Quanta Cloud Technology (QCT)": "qct.io", "Red Lion Controls": "redlion.net",
    "Ruijie Networks": "ruijie.com", "Schweitzer Engineering Laboratories": "selinc.com", "Silicom Ltd": "silicom-usa.com",
    "Teltonika Networks": "teltonika-networks.com", "Tenda": "tendacn.com", "Wi-Tek": "wireless-tek.com",
}
ALIAS_FIX = {  # alias -> the one vendor that keeps it
    "ARUBA": "Aruba", "HEWLETT PACKARD": "Aruba", "HP NETWORKING": "Aruba", "PROCURVE": "Aruba", "WESTERMO": "Westermo",
}
SERIES_OR_SPEC = re.compile(r"^(\d{3,5}|[\d\.]+\s*(mbps|gbps|ge|g)|\d+ge\+\d+g|en\d{5}|lite-l\d|[a-z]+)$", re.I)
ORDER_CODE = re.compile(r"^\d{6,}[A-Z0-9]*$")


def norm_vendor(v):
    return str(v).strip().lower().replace(" copy", "")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def form_of(desc):
    d = (desc or "").lower()
    if "din" in d:
        return "DIN rail"
    if re.search(r"\b\d+\s*u\b|rack", d):
        return "Rack"
    if "desktop" in d or "wall" in d:
        return "Desktop"
    return ""


def main():
    team = load_workbook(SW / "models_by_vendor.xlsx", read_only=True, data_only=True)
    rows = [r for r in team["Models"].iter_rows(values_only=True)][1:]
    tvend = {r[0]: r for r in list(team["Vendors"].iter_rows(values_only=True))[1:]}
    export = json.load(open(SW / "switch_catalog.json"))["models"]
    mapper = {v["name"].lower(): v for v in json.load(open(SW / "vendors.json"))}
    checks_file = SW / "switch_site_checks.json"
    checks = json.load(open(checks_file))["found"] if checks_file.exists() else {}
    by_name, by_id = {}, {}
    for e in export:
        v = norm_vendor(e.get("vendor"))
        by_name.setdefault((v, norm(e.get("name"))), e)
        for pid in str(e.get("model") or "").split():
            by_id.setdefault((v, norm(pid)), e)

    log, out, seen = [], [], set()
    n_dup = n_code = n_flag = n_traced = n_notrace = n_conf = 0
    for vendor, model, source, _url, _al, _n in rows:
        model = str(model).strip()
        key = (vendor.lower(), model.upper())
        if key in seen:
            n_dup += 1
            log.append(("Removed", vendor, model, "duplicate row (same vendor and model twice)"))
            continue
        seen.add(key)
        v = norm_vendor(vendor)
        e = by_name.get((v, norm(model))) or by_id.get((v, norm(model)))
        note, ids, ports, breakdown, form = "", "", "", "", ""
        if e:
            ids = str(e.get("model") or "").strip()
            ports = e.get("total_ports") or ""
            breakdown = (e.get("ports_breakdown") or "").strip()
            form = form_of(e.get("description"))
            if ORDER_CODE.match(model) and e.get("name") and norm(e["name"]) != norm(model):
                log.append(("Renamed", vendor, f"{model} -> {e['name']}", "order code used as the name; the vendor workbook gives the model name"))
                model, n_code = str(e["name"]).strip(), n_code + 1
                if (vendor.lower(), model.upper()) in seen:
                    n_dup += 1
                    log.append(("Removed", vendor, model, "same model already listed under its name"))
                    continue
                seen.add((vendor.lower(), model.upper()))
        if source == "catalog":
            src = f"Vendor workbook {e['source']} (6 Oct)" if e else "Vendor workbook (6 Oct)"
        elif e:
            src, n_traced = f"Recovered, traced to {e['source']}", n_traced + 1
        else:
            src, n_notrace = "Recovered, no trace: confirm online", n_notrace + 1
        if SERIES_OR_SPEC.match(model):
            note, n_flag = "Series number or spec, not a model name: confirm the full name", n_flag + 1
            log.append(("Flagged", vendor, model, note))
        status, link = "Check", ""
        hit = (checks.get(vendor) or {}).get(model)
        if hit:
            status, link = hit["status"], hit["url"]
            src = "Vendor site (sitemap) " + src.replace("Vendor workbook", "+ workbook").replace("Recovered, no trace: confirm online", "+ recovered row")
            log.append(("Confirmed", vendor, model, f"{status}: {link}"))
            n_conf = n_conf + 1
        out.append((vendor, model, ids if ids and norm(ids) != norm(model) else "", ports, breakdown, form, status, src, link, note))

    # vendors sheet
    vendors = collections.OrderedDict()
    for r in out:
        vendors.setdefault(r[0], []).append(r[1])
    vrows = []
    for vendor, models in vendors.items():
        t = tvend.get(vendor) or (vendor, 0, None, None, None)
        aliases = [a.strip() for a in str(t[2] or "").split(",") if a.strip()]
        aliases = [a for a in aliases if ALIAS_FIX.get(a.upper(), vendor) == vendor]
        if vendor == "HPE" and not aliases:
            aliases = ["HPE", "HEWLETT PACKARD ENTERPRISE"]      # its own names stay; the Aruba ones moved to Aruba
        if t[2] and len(aliases) != len([a for a in str(t[2]).split(",") if a.strip()]):
            log.append(("Alias", vendor, "", f"aliases now: {', '.join(aliases) or '-'} (shared names settled)"))
        m = mapper.get(vendor.lower()) or mapper.get(norm_vendor(vendor))
        domains = [d.strip() for d in str(t[3] or "").split(",") if d.strip()]
        dsrc = "team list"
        if not domains and m and m.get("domains"):
            domains, dsrc = list(m["domains"]), "mapper vendor file"
            log.append(("Domain", vendor, "", f"{', '.join(domains)} (from the mapper's vendor file)"))
        if not domains and vendor in KNOWN_DOMAINS:
            domains, dsrc = [KNOWN_DOMAINS[vendor]], "product knowledge (check)"
            log.append(("Domain", vendor, "", f"{domains[0]} (product knowledge: check)"))
        sitemaps = ", ".join((m or {}).get("sitemaps", []))
        traced = sum(1 for r in out if r[0] == vendor and r[7].startswith("Vendor workbook"))
        vrows.append((vendor, len(models), ", ".join(aliases), ", ".join(domains), dsrc, sitemaps, traced, len(models) - traced,
                      sum(1 for r in out if r[0] == vendor and r[9]), sum(1 for r in out if r[0] == vendor and r[8])))
    for alias, keep in ALIAS_FIX.items():
        log.append(("Alias", keep, alias, "kept by this vendor only; removed from the other claimant"))

    wb = Workbook()
    ws = wb.active
    ws.title = "Switches"
    cols = [("Vendor", 22), ("Model", 26), ("Product IDs", 30), ("Ports", 8), ("Port breakdown", 48), ("Form factor", 12),
            ("Status", 12), ("Source", 40), ("Checked at (link)", 40), ("Note", 50)]
    header(ws, cols)
    for r in sorted(out, key=lambda r: (r[0].lower(), r[1].lower())):
        ws.append(list(r))
    style(ws, len(out))
    ws2 = wb.create_sheet("Vendors")
    header(ws2, [("Vendor", 30), ("Models", 8), ("Aliases", 34), ("Domains", 34), ("Domain source", 22), ("Sitemaps", 40),
                 ("From the vendor workbooks", 12), ("Recovered", 10), ("Flagged names", 10), ("Confirmed on vendor site", 12)])
    for r in vrows:
        ws2.append(list(r))
    style(ws2, len(vrows))
    ws3 = wb.create_sheet("Change log")
    header(ws3, [("Change", 12), ("Vendor", 24), ("Model", 30), ("Detail", 90)])
    for r in log:
        ws3.append(list(r))
    style(ws3, len(log))
    ws4 = wb.create_sheet("Notes")
    notes = [
        ("What this is", "Switch models of every vendor in the team's list of 23 Oct, in the layout of the router, firewall, "
                         "gateway and load-balancer catalogues. One row per model; product IDs (order codes) of that model in "
                         "their own column; port count and breakdown and form factor from the vendor workbooks where the row "
                         "could be traced to them."),
        ("Source", "'Vendor workbook <file> (6 Oct)': the row comes from that workbook of the 6 Oct set. 'Recovered, traced to "
                   "<file>': a row added later whose name or product ID is in that workbook. 'Recovered, no trace': a row added "
                   "later with no match in the workbooks; confirm it online before relying on it."),
        ("Status", "'Current' or 'End-of-sale' when the model has a page on the vendor's site (sitemap check, link in "
                   "'Checked at'); 'End-of-sale' when that page sits under an end-of-life / legacy path. 'Check': not found "
                   "on the vendor's site yet; the web search pass (data sheets, distributors) is next."),
        ("Note", "'Series number or spec, not a model name': the row names a series ('6000') or a spec ('100Mbps'); the "
                 "online check replaces it with the full model names or removes it."),
        ("Done offline (23 Oct)", f"{n_dup} duplicate rows removed, {n_code} order codes replaced by the model name, {n_flag} "
                                  f"names flagged, {n_traced} recovered rows traced to a workbook, {n_notrace} recovered rows "
                                  "without a trace, 5 shared aliases settled, vendor domains filled. Every change is in the Change log."),
        ("Online check", f"Pass 1 (vendor sites through sitemaps, scripts/switches/site_check.py): {n_conf} models confirmed with a "
                         "link. Still to do: pass 2, a web search for the rows still at 'Check'; end-of-life notices for the status."),
    ]
    ws4.column_dimensions["A"].width, ws4.column_dimensions["B"].width = 26, 120
    for i, (k, v) in enumerate(notes, 1):
        ws4.cell(row=i, column=1, value=k).font = Font(name=FONT, bold=True)
        c = ws4.cell(row=i, column=2, value=v)
        c.font = Font(name=FONT)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(OUT)
    print(f"{OUT.name}: {len(out)} models, {len(vrows)} vendors; removed {n_dup}, renamed {n_code}, flagged {n_flag}, "
          f"recovered traced {n_traced} / no trace {n_notrace}; confirmed on vendor site {n_conf}; log {len(log)} lines")


def header(ws, cols):
    for i, (name, width) in enumerate(cols, 1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEAD
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = "A2"


def style(ws, n):
    for row in ws.iter_rows(min_row=2, max_row=n + 1):
        for c in row:
            c.font = Font(name=FONT)
    ws.auto_filter.ref = ws.dimensions


if __name__ == "__main__":
    main()
