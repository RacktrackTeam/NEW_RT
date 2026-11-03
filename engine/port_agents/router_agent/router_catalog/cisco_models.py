"""Cisco product IDs (scraped/cisco.json) -> distinct hardware models.

A series page lists every orderable ID: the router itself plus licence bundles
(-SEC/K9, -DNA), refurbished/spare/web-store codes (-RF, =, -WS, ++), Cisco One
bundles (C1-) and the series' modules, cables and licences. Only the router
hardware is kept; IDs that differ only by those commercial suffixes are one model.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

NOT_ROUTER = re.compile(
    r"^(NIM|SM-|C-SM|C-NIM|PWR|ACS|MEM|SL-|FL-|CON-|SW|PVDM|EHWIC|HWIC|VWIC|GLC|SFP|QSFP|CAB|"
    r"LIC|L-|DNA-|SVS|PI-|ASR1000-|A9K|A99|NC55-|NC57|NC6|N540-FAN|N540-PWR|FAN|EM-|ISR-|SPA|"
    r"MODULE|CBR-|CRS-|8800-|8K-|DIS|NIM|C1-NIM|UCS|E-|ENC|HDD|SSD|STK|RCKMNT|ACC|AIR-|ANT|"
    r"4G-|5G-|P-LTE|LTE|MA-|IRM|IRMH|IR-|IOX|M-ASR|POE|ASA|C9|C3|SM-X|NM|ENCS|BOOT|SR-|C1100-|"
    r"C1000-|C1100TG-|CGM-|CGR-ACC|CGR-PWR|CGR-LIC|CGR-ANT|CGR-SATA|DC-|AC-|C8000|C8200-FAN|"
    r"C8300-FAN|C8500-FAN|C-|PSU)", re.I)
TRAIL = re.compile(r"(\+\+|=|-RF|-WS|/K9|-K9|-DNA(-PM)?|-CDNA|-SPM(-8G)?|-PM\d*|-DPRM|-AXV|-AX|"
                   r"-VSEC|-SEC|-IWAN|-B|-V|-S|-DSA|-SL|-RTU|-C1|-BUN|-BNDL|-BUNDLE|-PAK)$", re.I)


def hardware(pid):
    p = pid.strip().upper()
    if p.startswith("C1-"):
        p = p[3:]
    if NOT_ROUTER.match(p) or len(p) < 4:
        return None
    prev = None
    while prev != p:
        prev, p = p, TRAIL.sub("", p)
    return p if re.search(r"\d", p) else None


def models():
    data = json.loads((HERE / "scraped" / "cisco.json").read_text())
    out = []
    for series, d in data.items():
        names = {}
        for pid in d.get("pids", []):
            hw = hardware(pid)
            if hw:
                names.setdefault(hw, pid)
        for m in d.get("models", []):          # model pages ("4331 Integrated Services Router")
            names.setdefault("__page__" + m["model"], m["model"])
        out.append((series, d.get("status", ""), sorted(k for k in names if not k.startswith("__page__")),
                    [m["model"] for m in d.get("models", [])]))
    return out


if __name__ == "__main__":
    total = 0
    for series, st, hw, pages in models():
        total += len(hw)
        print(f"{series} [{st}] {len(hw)}: {', '.join(hw[:40])}{' ...' if len(hw) > 40 else ''}")
    print("total", total)
