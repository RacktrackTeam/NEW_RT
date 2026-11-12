"""Excel catalog: maps make/model to vendor image and port layout, plus fuzzy lookup."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

COLUMNS = [
    "vendor",        # e.g. TP-Link
    "model",         # e.g. TL-SG2428P
    "aliases",       # ';'-separated alternative spellings / part numbers
    "description",
    "vendor_image",  # path (relative to the catalog) or URL of a front-view image
    "rj45_ports",    # number of copper ports
    "sfp_ports",     # number of SFP/SFP+ cages
    "numbering",     # odd_top | sequential_top | sequential_bottom
    "face_crop",     # optional "x0,y0,x1,y1" of the front face in vendor_image
]

# Characters OCR commonly confuses; applied to both sides for a "loose" comparison.
_CONFUSIONS = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1",
                             "S": "5", "B": "8", "Z": "2", "G": "6",
                             "U": "11"})  # 'LI' often reads as 'U' ('TP-UNK')


def norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def loose(s: str) -> str:
    return norm(s).translate(_CONFUSIONS)


@dataclass
class CatalogEntry:
    vendor: str
    model: str
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    vendor_image: str = ""
    rj45_ports: int = 0
    sfp_ports: int = 0
    numbering: str = "odd_top"
    face_crop: tuple[int, int, int, int] | None = None
    base_dir: Path = Path(".")

    @property
    def image_path(self) -> Path | None:
        if not self.vendor_image or self.vendor_image.startswith(("http://", "https://")):
            return None
        p = Path(self.vendor_image)
        return p if p.is_absolute() else self.base_dir / p

    @property
    def names(self) -> list[str]:
        return [self.model, *self.aliases]


@dataclass
class Match:
    entry: CatalogEntry
    score: float          # 0-100
    matched_text: str


def _cell(v, default=""):
    return default if pd.isna(v) else v


def load_catalog(path: str | Path) -> list[CatalogEntry]:
    path = Path(path)
    df = pd.read_excel(path, dtype=str)
    missing = {"vendor", "model"} - set(df.columns)
    if missing:
        raise ValueError(f"{path}: missing required columns {sorted(missing)}")
    entries = []
    for _, r in df.iterrows():
        crop = _cell(r.get("face_crop"))
        entries.append(CatalogEntry(
            vendor=_cell(r["vendor"]).strip(),
            model=_cell(r["model"]).strip(),
            aliases=[a.strip() for a in _cell(r.get("aliases")).split(";") if a.strip()],
            description=_cell(r.get("description")),
            vendor_image=_cell(r.get("vendor_image")).strip(),
            rj45_ports=int(float(_cell(r.get("rj45_ports"), 0) or 0)),
            sfp_ports=int(float(_cell(r.get("sfp_ports"), 0) or 0)),
            numbering=_cell(r.get("numbering"), "odd_top").strip() or "odd_top",
            face_crop=tuple(int(v) for v in crop.split(",")) if crop.strip() else None,
            base_dir=path.parent,
        ))
    return entries


def _candidates(tokens: list[str]) -> list[str]:
    """Single tokens plus joins of 2-3 neighbours (OCR often splits 'TL-SG2428P')."""
    toks = [t for t in tokens if norm(t)]
    out = set(toks)
    for n in (2, 3):
        for i in range(len(toks) - n + 1):
            out.add("".join(toks[i:i + n]))
    return list(out)


def detected_vendors(texts: list[str], catalog: list[CatalogEntry], hint: str | None = None) -> set[str]:
    """Vendors whose name appears in the OCR text (tolerant of e.g. 'TPUNK' for TP-Link)."""
    blob = " ".join(texts) + " " + (hint or "")
    b, bl = norm(blob), loose(blob)
    scores = {}
    for e in catalog:
        v, vl = norm(e.vendor), loose(e.vendor)
        if v and len(b) >= len(v):
            # a match that needs OCR-confusion folding is weaker evidence
            scores[e.vendor] = max(fuzz.partial_ratio(v, b), fuzz.partial_ratio(vl, bl) - 10)
    top = max(scores.values(), default=0)
    # 'TP-Link' and 'D-Link' share 'LINK': keep only the clearly better reading
    return {v for v, s in scores.items() if s >= 88 and s >= top - 5}


MIN_NAME_LEN = 5          # shorter names/aliases match too much by accident
OTHER_VENDOR_PENALTY = 20


def match_model(tokens: list[str], catalog: list[CatalogEntry],
                vendor_hint: str | None = None) -> list[Match]:
    """Rank catalog entries against OCR tokens (reading order). Best first."""
    cands = _candidates(tokens)
    full = "".join(norm(t) for t in tokens)
    full_loose = full.translate(_CONFUSIONS)
    vendors = detected_vendors(tokens, catalog, vendor_hint)

    results = []
    for e in catalog:
        best, best_text = 0.0, ""
        for name in e.names:
            n, nl = norm(name), loose(name)
            if len(n) < MIN_NAME_LEN:
                continue
            for c in cands:
                s = max(fuzz.ratio(n, norm(c)), fuzz.ratio(nl, loose(c)))
                if s > best:
                    best, best_text = s, c
            # model embedded in a longer run of text, e.g. "TLSG2428P28PORT"
            if len(full) > len(n):
                s = max(fuzz.partial_ratio(n, full), fuzz.partial_ratio(nl, full_loose)) * 0.95
                if s > best:
                    best, best_text = s, name
        if vendors:
            best = best + 5 if e.vendor in vendors else best - OTHER_VENDOR_PENALTY
        results.append(Match(e, round(max(0.0, min(100.0, best)), 1), best_text))
    return sorted(results, key=lambda m: m.score, reverse=True)
