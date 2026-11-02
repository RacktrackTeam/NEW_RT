"""Group Cisco hardware IDs (cisco_models.py) into base models: one row per router a
template would be made for. Licence/region/cellular-carrier variants stay listed as
variants of their base model (C1111-8PLTEEA -> C1111-8P)."""
import re

from cisco_models import models

# series -> regex whose first group is the base model; IDs that don't match are not routers
RULES = {
    "900 ISR": r"^(ISR9\d\d)",
    "1000 ISR": r"^(C1\d{3}[A-Z]*-\d+P|C1100TGX?)",
    "1900 ISR": r"^(?:CISCO|C)(19\d\d)",
    "2900 ISR": r"^(?:CISCO|C)(29\d\d)",
    "3900 ISR": r"^(?:CISCO|C)(39\d\dE?)",
    "4000 ISR": r"^(?:ISR|CISCO)(4\d\d\d(?:-X|X)?)",
    "8100 Secure Router": r"^(C81\d\d[A-Z]*-(?:[A-Z0-9]+-)?G2)",
    "8200 Secure Router": r"^(C82\d\d[A-Z]*-(?:E-)?G2(?:-UCSXE)?)",
    "ASR 900": r"^(ASR-90\d[U]?|ASR-914)",
    "ASR 901": r"^(A901-\d+[A-Z]+)",
    "ASR 901 10G": r"^(A901-6CZ)",
    "ASR 920": r"^(ASR-920U?-\d+[A-Z]+(?:-[A-Z]+)?)",
    "ASR 1000": r"^(ASR100\d(?:-X|-HX|-XA|X)?)",
    "ASR 9000": r"^(ASR-9\d{3})",
    "800 Series": r"^(C8\d\d[A-Z]*(?:-\d+X)?)",
    "8000 Series": r"^(8\d{3}-[0-9A-Z]+)",
    "Catalyst 8300 Edge": r"^(C8300-\d+N\d*S?-[0-9A-Z]+)",
    "Catalyst 8500 Edge": r"^(C85\d\dL?-[0-9A-Z]+)",
    "NCS 520": r"^(N520-(?:X-)?\d+G\d+Z)",
    "NCS 540": r"^(N540X?-[0-9A-Z]+(?:-[A-Z]+-SYS|-SYS)?)",
    "NCS 5000": r"^(NCS-50\d\d)",
    "NCS 5500": r"^(NCS-55[0-9A-Z]{2}(?:-[0-9A-Z]+)?)",
    "NCS 5700": r"^(NCS-57[A-Z0-9]{2}-[0-9A-Z]+)",
    "NCS 6000": r"^(NCS-6\d{3})",
    "Carrier Routing System": r"^(CRS\d?-\d+)",
    "800 Industrial ISR": r"^(IR8\d\d[A-Z]*)",
    "1000 Connected Grid": r"^(CGR-?\d{4}|IR8140H(?:-P)?)",
    "2000 Connected Grid": r"^(CGR-2010)",
    "IR1100 Catalyst Rugged": r"^(IR1101|IR100\d-G2)",
    "IR1800 Catalyst Rugged": r"^(IR18\d\d)",
}
NAME = {"1900 ISR": "CISCO{}", "2900 ISR": "CISCO{}", "3900 ISR": "CISCO{}", "4000 ISR": "ISR{}"}


def base_models():
    """{series: (status, {base model: [IDs]})}"""
    out = {}
    for series, status, hw, pages in models():
        rx = RULES.get(series)
        groups = {}
        if rx:
            for p in hw:
                m = re.match(rx, p)
                if not m:
                    continue
                b = NAME.get(series, "{}").format(m.group(1))
                b = re.sub(r"(-SYS)?-[AD]$", "", b) if series.startswith("NCS") else b
                groups.setdefault(b, []).append(p)
        out[series] = (status, groups)
    return out


if __name__ == "__main__":
    n = 0
    for s, (st, g) in base_models().items():
        n += len(g)
        print(f"{s} [{st}] {len(g)}: {', '.join(sorted(g))}")
    print("total base models", n)
