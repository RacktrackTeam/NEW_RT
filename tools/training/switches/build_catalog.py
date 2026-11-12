"""The switch catalogue, from the workbook: data/catalogues/models_by_vendor.xlsx -> models_by_vendor.json.

    python3 scripts/switches/build_catalog.py            # rebuild the json from the workbook and print what changed

The workbook is the source of truth (user, 2026-10-23): sheet "Models" (Vendor, Model, Source, Source URL, Vendor
aliases, Models for vendor), sheet "Vendors" (Vendor, Models, Aliases, Domains, All model names), sheet "Aliases"
(Alias, Vendor, Also claimed by). The json keeps the shape the switch mapper reads (switch_identify.load_catalog):
{vendor: {"aliases": [...], "models": [...]}}; a vendor with no alias is a plain list. Names that are not a model
(a bandwidth such as "100Mbps") are left out and listed.
"""
import collections
import json
import pathlib
import re
import sys

import openpyxl

ROOT = pathlib.Path(__file__).resolve().parents[2]
XLSX = ROOT / "data" / "catalogues" / "models_by_vendor.xlsx"
JSON = ROOT / "data" / "catalogues" / "models_by_vendor.json"
NOT_A_MODEL = re.compile(r"^\s*[\d.]+\s*(mbps|gbps|ge|g|gb|mb)\s*$", re.I)


def read(xlsx=XLSX):
    wb = openpyxl.load_workbook(xlsx, read_only=True)
    models, aliases, skipped = collections.defaultdict(list), collections.defaultdict(list), []
    for vendor, model, *_ in list(wb["Models"].iter_rows(values_only=True))[1:]:
        if not vendor or not model:
            continue
        name = str(model).strip()
        if NOT_A_MODEL.match(name):
            skipped.append((vendor, name)); continue
        if name not in models[vendor]:
            models[vendor].append(name)
    for alias, vendor, *_ in list(wb["Aliases"].iter_rows(values_only=True))[1:]:
        if alias and vendor and str(alias).strip() not in aliases[vendor]:
            aliases[vendor].append(str(alias).strip())
    for vendor, _, al, *_ in list(wb["Vendors"].iter_rows(values_only=True))[1:]:
        for a in (str(al).split(",") if al else []):
            if a.strip() and a.strip() not in aliases[vendor]:
                aliases[vendor].append(a.strip())
    out = {}
    for vendor in sorted(models, key=str.lower):
        out[vendor] = {"aliases": aliases[vendor], "models": models[vendor]} if aliases.get(vendor) else models[vendor]
    return out, skipped


def main():
    new, skipped = read()
    old = json.load(open(JSON)) if JSON.exists() else {}
    models_of = lambda v: set(v["models"] if isinstance(v, dict) else v)
    n_old = sum(len(models_of(v)) for v in old.values()); n_new = sum(len(models_of(v)) for v in new.values())
    JSON.write_text(json.dumps(new, indent=1, ensure_ascii=False) + "\n")
    print(f"vendors {len(old)} -> {len(new)} | models {n_old} -> {n_new}")
    print("vendors added:", sorted(set(new) - set(old)) or "none"); print("vendors dropped:", sorted(set(old) - set(new)) or "none")
    print("left out (not a model name):", skipped or "none")
    return 0


if __name__ == "__main__":
    sys.exit(main())
