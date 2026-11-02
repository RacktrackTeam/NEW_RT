"""Turn raw OCR detections into a device identification (make + model).

THIS MODULE NEVER NEEDS EDITING FOR NEW DEVICES. It works in layers:

1. Brand: exact + fuzzy matching against the vendor knowledge base loaded
   from ``vendors.json`` (fuzzy matching absorbs OCR errors like "D-Lirk").
2. Model: vendor-specific model-number regexes from the same data file.
   A model pattern can also *imply* the brand when the logo is unreadable.
3. Universal fallback (any vendor, zero configuration): if the knowledge
   base has no match, the brand is inferred from the most prominent purely
   alphabetic string on the faceplate (logos are big), and the model from
   generic model-number shapes (letters+digits+dashes).

To improve precision for a vendor, edit ``vendors.json`` or supply an extra
JSON file at runtime (``--vendors my_vendors.json`` / OCRConfig.extra_vendors)
— the code stays untouched.

Pure stdlib — no extra dependencies.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .types import TextDetection

log = logging.getLogger("switch_ocr")

# --------------------------------------------------------------------------- #
# Vendor knowledge base — loaded from vendors.json (data, not code)
# --------------------------------------------------------------------------- #
_KB_PATH = Path(__file__).parent / "vendors.json"

#: brand -> (aliases for logo text, model-number regexes)
VENDORS: Dict[str, Tuple[List[str], List[str]]] = {}
#: Weaker, brand-implying patterns, matched at reduced specificity.
WEAK_PATTERNS: Dict[str, List[str]] = {}
#: brand -> exact model strings (from field-collected data; see
#: scripts/import_models.py). Highest-precision evidence available.
VENDOR_MODELS: Dict[str, List[str]] = {}
#: normalised model key -> [(brand, canonical model), ...]
_MODEL_INDEX: Dict[str, List[Tuple[str, str]]] = {}


def _norm_model(s: str) -> str:
    """Model-key normalisation: uppercase alphanumerics only, so dash/space
    and OCR punctuation differences never break a lookup."""
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _longest_common_run(a: str, b: str) -> int:
    """Length of the longest CONTIGUOUS matching substring between a and b.

    Needed because SequenceMatcher's whole-string ratio can score two
    genuinely different model numbers deceptively high when their digits
    happen to share characters in scattered positions (real case: OCR
    "FSA-3610" vs catalog "S3260-10S" scored ratio 0.667 -- the same ratio
    a genuine OCR-damage rescue elsewhere legitimately needs -- even
    though the two numbers don't actually share any meaningful run: "3610"
    and "3260" are different numbers, not the same one with noise). A real
    same-model rescue keeps a solid unbroken run of the actual model
    number intact (verified: 5-7 characters in the real cases this
    function must not break); a coincidental cross-model overlap does not
    (2 characters in the FSA-3610 case).
    """
    m = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    best = 0
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                m[i][j] = m[i - 1][j - 1] + 1
                best = max(best, m[i][j])
    return best


def _rebuild_model_index() -> None:
    _MODEL_INDEX.clear()
    for brand, models in VENDOR_MODELS.items():
        for m in models:
            key = _norm_model(m)
            # keys must be selective: length, letters AND digits
            if len(key) < 4 or not any(c.isdigit() for c in key) \
                    or not any(c.isalpha() for c in key):
                continue
            _MODEL_INDEX.setdefault(key, []).append((brand, m))


def _load_kb(path: Path) -> None:
    """Merge a vendor knowledge file into the live KB."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for brand, entry in data.get("vendors", {}).items():
        aliases = [a.lower() for a in entry.get("aliases", [])]
        patterns = list(entry.get("patterns", []))
        if brand in VENDORS:  # merge with existing entry
            old_a, old_p = VENDORS[brand]
            aliases = list(dict.fromkeys(old_a + aliases))
            patterns = list(dict.fromkeys(old_p + patterns))
        VENDORS[brand] = (aliases, patterns)
        models = entry.get("models") or []
        if models:
            merged = set(VENDOR_MODELS.get(brand, [])) | set(models)
            VENDOR_MODELS[brand] = sorted(merged)
    for brand, pats in data.get("weak_patterns", {}).items():
        WEAK_PATTERNS[brand] = list(dict.fromkeys(WEAK_PATTERNS.get(brand, []) + pats))
    _rebuild_model_index()


def load_extra_vendors(path) -> None:
    """Add/extend vendors at runtime from a user JSON file (same schema as
    vendors.json). This is the supported way to teach the identifier new
    vendors — no code changes."""
    _load_kb(Path(path))
    log.info("Vendor knowledge extended from %s (now %d vendors)", path, len(VENDORS))


_load_kb(_KB_PATH)

#: Words that appear on nearly every switch faceplate and must never be
#: mistaken for a model number.
_PANEL_STOPWORDS = {
    "GIGABIT", "ETHERNET", "SWITCH", "CONSOLE", "RESET", "POWER", "PWR",
    "LINK", "ACT", "SPEED", "STATUS", "SYS", "SYST", "MODE", "STACK",
    "POE", "POE+", "UPLINK", "MGMT", "LAN", "WAN", "SFP", "SFP+", "10G",
    "1000BASE-T", "100-240V", "50-60HZ", "PORT", "PORTS", "SMART",
    "MANAGED", "UNMANAGED", "SERIES", "PRO", "PLUS", "LITE",
    "IEEE802", "RJ45", "CAT5E", "CAT6", "SFP28", "QSFP28", "BASE-T",
    # LED/status labels — real-world example: a row-joined "ECS2552FP" +
    # "Fault" (nearby LED label) must never be read as one longer model.
    "FAULT", "ALARM", "DIAG", "TEMP", "FAN", "ERROR", "ALERT", "READY",
}
#: Generic shapes of model-ish tokens: fallback for vendors with no pattern
#: (or unknown vendors). Dashed form ("SL-SWTG124AS") and letters+digits
#: form ("EX2028", "GS724T").
_GENERIC_PATTERNS = [
    r"\b[A-Z]{1,6}[A-Z0-9]{0,3}(?:-[A-Z0-9]{1,10}){1,4}\b",
    r"\b[A-Z]{1,5}\d{3,5}[A-Z0-9]*(?:-[A-Z0-9]{1,10})*\b",
]
#: Tokens longer than this are serial numbers / MACs, not model numbers.
_MAX_MODEL_LEN = 20


def _plausible_model(token: str) -> bool:
    """Sanity gate for GENERIC (shape-only) model candidates. Real model
    numbers have digits plus either a dash or a letter prefix; strings of
    concatenated port numbers ("I124134") do not.

    A single-letter prefix (Dell "N1524", Huawei/H3C "S5731", etc.) is a
    completely standard real convention, not noise — requiring 2+ letters
    here used to reject those outright. The digit-ratio check just below
    already independently rejects genuine concatenated-number noise like
    "I124134" (1 letter, 6 digits, ratio 0.86), so dropping to "at least 1
    letter" doesn't reopen that case; it only stops rejecting legitimate
    single-letter-prefix models.
    """
    letters = sum(c.isalpha() for c in token)
    digits = sum(c.isdigit() for c in token)
    if digits == 0:
        return False
    if "-" not in token and letters < 1:
        return False
    if len(token) >= 6 and digits / len(token) > 0.8:
        return False
    # Autonomous-system stickers ("AS57199") are ISP labels, not models.
    if re.fullmatch(r"AS\s?\d{5,6}", token.upper()):
        return False
    return True
#: Digit-lookalike letters: safe to convert whenever a digit run is adjacent
#: (chains like "11OO" count — O/I/l are skipped when looking for the digit).
_DIGITLIKE = {"O": "0", "o": "0", "I": "1", "l": "1", "|": "1"}
#: Aggressive confusions: only converted when sandwiched BETWEEN digits.
#: (Adjacent-to-one-digit is exactly how legit prefixes look: the S in
#: "GS1100" touches a digit and must survive.)
_HARD_CONFUSABLE = {"S": "5", "B": "8", "Z": "2"}
_CONFUSABLE = {**_DIGITLIKE, **_HARD_CONFUSABLE}  # kept for reference/tests


#: Reverse map: digits OCR sometimes produces instead of letters.
_CONFUSABLE_REV = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}


def _digit_context_fix(text: str) -> str:
    """Fix digit-lookalikes near digits ("DGS-11OO-16" -> "DGS-1100-16",
    "GSi100-16" -> "GS1100-16") WITHOUT cascading into letter prefixes
    (the S of "GS1100" must never become 5). Single pass over the original
    string; chains of lookalikes are resolved by skipping over them when
    searching for the anchoring digit."""
    chars = list(text)
    n = len(chars)

    def digit_beyond(i: int, step: int) -> bool:
        j = i + step
        while 0 <= j < n and chars[j] in _DIGITLIKE:
            j += step
        return 0 <= j < n and chars[j].isdigit()

    out = list(chars)
    for i, ch in enumerate(chars):
        if ch in _DIGITLIKE:
            if digit_beyond(i, -1) or digit_beyond(i, 1):
                out[i] = _DIGITLIKE[ch]
        elif ch in _HARD_CONFUSABLE:
            prev_d = i > 0 and chars[i - 1].isdigit()
            next_d = i + 1 < n and chars[i + 1].isdigit()
            if prev_d and next_d:
                out[i] = _HARD_CONFUSABLE[ch]
    return "".join(out)


def _letter_context_fix(text: str) -> str:
    """Fix digits that sit purely among letters ("TL-5G2428P" -> "TL-SG2428P").

    A digit with no digit neighbour inside a letter run is usually a misread
    letter. Applied as an ADDITIONAL candidate; the raw string is always
    matched too, so real digit-only tokens are never lost.
    """
    chars = list(text)
    for i, ch in enumerate(chars):
        if ch not in _CONFUSABLE_REV:
            continue
        prev_digit = i > 0 and chars[i - 1].isdigit()
        next_digit = i + 1 < len(chars) and chars[i + 1].isdigit()
        if not prev_digit and not next_digit:
            chars[i] = _CONFUSABLE_REV[ch]
    return "".join(chars)


@dataclass
class DeviceID:
    """Best guess for the device on the photo."""

    brand: Optional[str] = None
    model: Optional[str] = None
    confidence: float = 0.0
    #: Detected strings that support the identification.
    evidence: List[dict] = field(default_factory=list)
    #: Other plausible (brand, model) readings, best first.
    alternates: List[dict] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.brand, self.model) if p]
        return " ".join(parts) if parts else "unknown device"

    def to_dict(self) -> dict:
        return {
            "brand": self.brand,
            "model": self.model,
            "display_name": self.display_name,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "alternates": self.alternates,
        }


# --------------------------------------------------------------------------- #
def _norm(text: str) -> str:
    """Normalise for brand comparison: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _brand_score(detection_text: str, aliases: List[str], floor: float = 0.80,
                 length_strict: bool = False) -> float:
    """0..1 similarity between a detected string and a vendor's aliases.

    Compared in TWO forms: alphanumeric-only AND punctuation-preserving.
    The dash in a damaged "to-link" is precisely what distinguishes
    tp-link from TOTOLINK — stripping it loses real evidence.

    ``floor`` is the acceptance threshold; a relaxed second pass (0.70)
    rescues heavier OCR damage like "QHAP" -> QNAP, "NETOIAR" -> Netgear,
    but only when the strict pass found nothing at all.
    """
    hay = _norm(detection_text)
    if not hay:
        return 0.0
    hay_raw = re.sub(r"\s+", "", detection_text.lower())
    best = 0.0
    for alias in aliases:
        alias_norm = _norm(alias)
        if len(alias_norm) <= 3:
            # Short aliases ("hp", "fs"): whole-token match only (never
            # substring/fuzzy against the full string), else too many
            # false positives. Exact token match scores full; a token
            # that's the alias plus exactly one stray character ("OFS"
            # for "FS" — a single leading/trailing OCR-noise character on
            # an otherwise-clean short logo read) still scores highly
            # rather than zero, since that one-character margin is tight
            # enough to stay safe from unrelated short words.
            tokens = re.split(r"[^a-z0-9]+", detection_text.lower())
            for tok in tokens:
                if tok == alias_norm:
                    best = max(best, 1.0)
                elif abs(len(tok) - len(alias_norm)) == 1 and \
                        SequenceMatcher(None, tok, alias_norm).ratio() >= 0.8:
                    best = max(best, 0.85)
            continue
        if alias_norm in hay:
            best = max(best, 1.0)
            continue
        if length_strict and abs(len(_norm(hay_raw)) - len(alias_norm)) > 1:
            ratio = 0.0
        else:
            ratio = max(SequenceMatcher(None, alias_norm, hay).ratio(),
                        SequenceMatcher(None, alias, hay_raw).ratio())
        # Also compare against individual words for strings like
        # "D-Link Green Technology".
        for word in re.split(r"\s+", detection_text.lower()):
            w = _norm(word)
            if not w:
                continue
            if length_strict and abs(len(w) - len(alias_norm)) > 1:
                # In the relaxed pass, "pee" must not reach "Perle": heavy
                # damage is only believable at comparable word length.
                continue
            word_ratio = max(SequenceMatcher(None, alias_norm, w).ratio(),
                             SequenceMatcher(None, alias, word).ratio())
            # Punctuation-position agreement is strong structural evidence:
            # "to-link" (damaged logo) shares its dash position with
            # "tp-link" but not with "totolink".
            if ("-" in alias and "-" in word
                    and alias.index("-") == word.index("-")
                    and word_ratio >= 0.75):
                word_ratio = min(0.99, word_ratio + 0.06)
            ratio = max(ratio, word_ratio)
        if ratio >= floor:
            best = max(best, ratio)
    return best


def _model_candidates(
    texts: List[Tuple[str, float]], patterns: List[str], specificity: float = 1.0
) -> List[Tuple[str, float, float]]:
    """Find (model, ocr_conf, specificity) among detected strings."""
    hits = []
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    for text, conf in texts:
        upper = text.upper()
        variants = [upper]
        for fixed in (_digit_context_fix(upper), _letter_context_fix(upper)):
            if fixed != upper and fixed not in variants:
                variants.append(fixed)
        for i, variant in enumerate(variants):
            penalty = 1.0 if i == 0 else 0.85  # OCR-corrected match is less certain
            for pat in compiled:
                for m in pat.finditer(variant):
                    token = m.group(0).strip()
                    if token.upper() in _PANEL_STOPWORDS or len(token) > _MAX_MODEL_LEN:
                        continue
                    hits.append((token, conf, specificity * penalty))
    return hits


def _model_agreement_count(model_token: str, texts_v: List[Tuple[str, float, str]]) -> int:
    """How many INDEPENDENT passes (original/enhanced/upscaled/zoom/rotate/
    deskew/orientation/perspective/engine) separately produced text matching
    this model number.

    A single confident-looking read can still be a plausible misread; the
    same model string turning up again from a DIFFERENT view of the photo
    (a different crop, angle, engine, or contrast treatment) is much
    stronger corroborating evidence, free of any extra OCR cost since it
    only re-examines detections already collected.
    """
    target = _norm_model(model_token)
    if len(target) < 4:
        return 1
    compiled = [re.compile(p) for p in _GENERIC_PATTERNS]
    variants = set()
    for text, conf, variant in texts_v:
        if conf < 0.30:
            continue
        upper = text.upper()
        candidates = {upper}
        for pat in compiled:
            candidates.update(m.group(0) for m in pat.finditer(upper))
        for cand in candidates:
            nk = _norm_model(cand)
            if not nk:
                continue
            if nk == target or (len(nk) >= 5 and len(target) >= 5
                                and (nk in target or target in nk)):
                variants.add(variant)
                break
    return max(1, len(variants))


def _row_joined_texts(detections: List[TextDetection]) -> List[Tuple[str, float]]:
    """Rebuild strings that OCR split into fragments on the same line.

    Model numbers often come back in pieces ("DGS-1100" + "-16", or
    "GS724" + "T"). Detections that share a horizontal band and sit close
    together are joined (with and without a space) and offered as extra
    candidates, scored by their weakest member.
    """
    dets = [d for d in detections if d.text.strip()]
    joined: List[Tuple[str, float]] = []
    used_rows = set()
    for i, a in enumerate(dets):
        if i in used_rows:
            continue
        row = [a]
        ha = max(1, a.height)
        for j, b in enumerate(dets):
            if j == i:
                continue
            # vertical overlap >= 50% of the smaller height -> same line
            ov = min(a.box[3], b.box[3]) - max(a.box[1], b.box[1])
            if ov < 0.5 * min(ha, max(1, b.height)):
                continue
            row.append(b)
        if len(row) < 2:
            continue
        row.sort(key=lambda d: d.box[0])
        # only join chains whose neighbours are close (gap < 1.5x line height)
        chain = [row[0]]
        for nxt in row[1:]:
            gap = nxt.box[0] - chain[-1].box[2]
            if -0.2 * ha <= gap <= 1.5 * ha:
                chain.append(nxt)
            else:
                if len(chain) > 1:
                    _emit_chain(chain, joined)
                chain = [nxt]
        if len(chain) > 1:
            _emit_chain(chain, joined)
        used_rows.update(idx for idx, d in enumerate(dets) if d in row)
    return joined


def _emit_chain(chain: List[TextDetection], out: List[Tuple[str, float]]) -> None:
    conf = min(d.confidence for d in chain)
    if conf < 0.25:
        return
    texts = [d.text.strip() for d in chain]
    out.append((" ".join(texts), conf))
    out.append(("".join(texts), conf))


def _prefer_extension(best, models):
    """Upgrade the winner to a longer candidate that contains it: suffix
    extensions ("GS724" -> "GS724T", "DGS-1100" -> "DGS-1100-16") and prefix
    extensions ("SG2428P" -> "TL-SG2428P", where the exact-model DB stores
    the unprefixed form but OCR read the fuller label)."""
    if best is None:
        return None
    upgraded = best
    for cand in models:
        if len(cand[0]) <= len(upgraded[0]):
            continue
        c, u = cand[0].upper(), upgraded[0].upper()
        if not (c.startswith(u) or c.endswith(u)):
            continue
        if cand[1] >= 0.6 * best[1] and cand[2] >= 0.75 * best[2]:
            upgraded = cand
    return upgraded


#: Corporate suffix words stripped from inferred brand candidates.
_BRAND_SUFFIXES = {"NETWORKS", "TECHNOLOGY", "TECHNOLOGIES", "SYSTEMS",
                   "INC", "CORP", "LTD", "CO"}
#: Faceplate words that are never a brand (in addition to _PANEL_STOPWORDS).
_NON_BRAND = _PANEL_STOPWORDS | {
    "ROUTER", "SWITCHES", "DESKTOP", "RACKMOUNT", "WEB", "EASY", "FAST",
    "FAN", "MAX", "MIN", "ACTIVITY", "GREEN", "SETUP", "SELECT",
    # stock-photo watermarks
    "ALAMY", "SHUTTERSTOCK", "GETTY", "ISTOCK", "DREAMSTIME",
}
#: Inferred-brand quality gates: OCR noise ("Toy peal" @0.12) must never be
#: promoted to a brand name.
_INFER_MIN_CONF = 0.65
_INFER_MIN_CONF_SOLO = 0.75


def _infer_brand(detections: List[TextDetection], exclude: Optional[str] = None):
    """Zero-knowledge brand guess for vendors missing from the KB.

    Logos are the visually dominant, purely alphabetic strings on a
    faceplate. Candidates: 1-2 words, letters only (plus .&-), not panel
    vocabulary, not model-shaped. Ranked by text height (relative to the
    photo's median text height) times OCR confidence. Accepted when clearly
    prominent, or when it is the only alphabetic string present.
    """
    heights = sorted(d.height for d in detections if d.text.strip())
    if not heights:
        return None
    median_h = heights[len(heights) // 2] or 1

    candidates = []
    eligible_alpha = 0
    for d in detections:
        if d.variant == "zoom":  # magnified noise never names a brand
            continue
        text = d.text.strip()
        if not text or (exclude and text.upper() == exclude.upper()):
            continue
        if float(d.confidence) < _INFER_MIN_CONF:
            continue
        if any(ch.isdigit() for ch in text):
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z .&-]{1,19}", text):
            continue
        words = [w for w in re.split(r"[\s]+", text) if w]
        if len(words) > 2 or any(len(w.strip(".&-")) < 2 for w in words):
            continue
        core = [w for w in words if w.upper().strip(".&-") not in _BRAND_SUFFIXES]
        if not core:
            continue
        name = " ".join(core)
        if len(name) < 3:
            continue
        if name.upper() in _NON_BRAND or any(w.upper() in _NON_BRAND for w in core):
            continue
        # LED rows and port-dot artwork OCR as repeated letters ("YYYYYY",
        # "CCCCCR") — no real brand is >50% one character.
        letter_counts = [name.upper().count(c) for c in set(name.upper()) if c.isalpha()]
        if letter_counts and max(letter_counts) / sum(letter_counts) > 0.5:
            continue
        # A string (or any of its words) that occurs INSIDE a known alias is
        # an OCR fragment of that brand ("INET." from FORTINET, "Swit rs"
        # from Cloud Smart Switch), not a new vendor.
        all_aliases = [al for aliases, _ in VENDORS.values() for al in aliases]
        frags = [_norm(name)] + [_norm(w) for w in core]
        if any(len(fr) >= 3 and fr in al for fr in frags for al in all_aliases):
            continue
        eligible_alpha += 1
        rel_h = d.height / median_h
        score = float(d.confidence) * min(rel_h, 3.0) / 1.5
        candidates.append({"text": name, "conf": float(d.confidence),
                           "rel_h": rel_h, "score": min(1.0, score)})

    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["score"])
    # Clearly prominent logo, or the only alphabetic string on the plate —
    # the solo case demands higher confidence and a longer name.
    if best["rel_h"] >= 1.3:
        return best
    if eligible_alpha == 1 and best["conf"] >= _INFER_MIN_CONF_SOLO and len(best["text"]) >= 4:
        return best
    return None


def _exact_model_hits(model_texts) -> Dict[str, List[Tuple[str, float, float]]]:
    """Look detected strings up in the exact-model database.

    Tokens are extracted with the generic model shapes (plus the whole
    string), normalised (case/punctuation-insensitive, OCR-corrected) and
    matched against _MODEL_INDEX. An exact hit is the strongest model
    evidence there is, so it carries specificity 1.2 — above any regex.
    """
    if not _MODEL_INDEX:
        return {}
    compiled = [re.compile(p) for p in _GENERIC_PATTERNS]
    hits: Dict[str, List[Tuple[str, float, float]]] = {}
    for text, conf in model_texts:
        upper = text.upper()
        for vi, variant in enumerate(dict.fromkeys(
                (upper, _digit_context_fix(upper), _letter_context_fix(upper)))):
            penalty = 1.0 if vi == 0 else 0.9
            tokens = {variant}
            for pat in compiled:
                tokens.update(m.group(0) for m in pat.finditer(variant))
            for token in tokens:
                for brand, canonical in _MODEL_INDEX.get(_norm_model(token), []):
                    hits.setdefault(brand, []).append((canonical, conf, 1.2 * penalty))
    return hits


def _catalog_adjust(brand: str, candidates, allow_hard_reject: bool = False):
    """Reconcile generic (shape-only) model candidates with the brand's
    known-model catalog: snap near-misses to the canonical spelling, and
    dampen tokens a well-populated catalog has never heard of.

    ``allow_hard_reject`` gates dropping a candidate outright when it
    barely resembles anything in the catalog. That's safe for candidates
    from the BRAND-AGNOSTIC generic-shape fallback (no vendor-specific
    signal connects them to this brand at all — a real Juniper-adjacent
    case verified this at ratio 0.375). It is NOT safe for candidates that
    matched the vendor's OWN curated pattern/weak_pattern: those represent
    a real, known naming convention for that brand even when the
    (necessarily incomplete, field-collected) catalog has no example of
    it yet — verified with a real Aruba CX-series marketing name
    ("6300M-24G4M") that legitimately matches Aruba's own weak_pattern but
    scores low against a catalog that only has older J####A part numbers.
    """
    models = VENDOR_MODELS.get(brand) or []
    if not models:
        return candidates
    model_keys = {_norm_model(m): m for m in models}
    out = []
    for token, conf, spec in candidates:
        tk = _norm_model(token)
        # Strip a trailing panel/LED stopword BEFORE the length-gated fuzzy
        # search below: row-joining sometimes glues a nearby label word
        # directly onto an otherwise-exact model read ("ECS2552FP" +
        # "Fault" -> "ECS2552FPFAULT"). The suffix can push the combined
        # string's length far enough past the real model's that the fuzzy
        # search's length-difference gate would never even consider the
        # correct catalog entry as a comparison candidate — this direct
        # exact-lookup-after-stripping catches it regardless of length.
        stripped_hit = None
        for stop in _PANEL_STOPWORDS:
            if len(stop) >= 3 and tk.endswith(stop) and len(tk) > len(stop):
                candidate_key = tk[:-len(stop)]
                if candidate_key in model_keys:
                    stripped_hit = model_keys[candidate_key]
                    break
        if stripped_hit is not None:
            out.append((stripped_hit, conf, 1.0))
            continue
        best_ratio, second_ratio, best_model = 0.0, 0.0, None
        for m in models:
            mk = _norm_model(m)
            if abs(len(mk) - len(tk)) > 4:
                continue
            r = SequenceMatcher(None, tk, mk).ratio()
            if r > best_ratio:
                second_ratio, best_ratio, best_model = best_ratio, r, m
            elif r > second_ratio:
                second_ratio = r
        bk = _norm_model(best_model) if best_model else ""
        contaminated_suffix = (tk.startswith(bk) and len(tk) > len(bk)
                               and tk[len(bk):] in _PANEL_STOPWORDS)
        # A tie (or near-tie) between the top two catalog candidates means
        # two DIFFERENT real models are equally plausible from this text
        # alone (verified real case: "ES-504PH" damaged-reads scored
        # EXACTLY 0.933 against both real Edimax "ES-5804PH" and
        # "ES-5104PH" — dropping either the 8 or the 1 produces the same
        # string). Snapping to whichever the search happened to see first
        # is a coin flip; keep the original reading instead of confidently
        # picking the wrong one of two real products.
        tied = best_ratio > 0 and (best_ratio - second_ratio) < 0.03
        if best_ratio >= 0.78 and tied:
            out.append((token, conf, spec))
        elif best_ratio >= 0.78 and contaminated_suffix:
            # tk is a COMPLETE catalog model plus a trailing panel/LED
            # label — this is row-joining noise, not a longer SKU variant.
            # Snap to the clean spelling instead of the branch below, which
            # would otherwise preserve the contamination.
            out.append((best_model, conf, 1.0))
        elif best_ratio >= 0.78 and (bk.startswith(tk) or tk.startswith(bk)):
            # Pure prefix/suffix relationship: the label may legitimately be
            # the base SKU ("S3900-24T4S" vs catalog "S3900-24T4S-R") — keep
            # what the photo says, corroborated by the catalog.
            out.append((token, conf, 0.9))
        elif best_ratio >= 0.78:
            out.append((best_model, conf, 1.0))       # internal misread: snap
        elif best_ratio < 0.60 and len(models) >= 10:
            if best_ratio >= 0.45 or not allow_hard_reject:
                out.append((token, conf, 0.35))       # catalog never heard of it,
                                                       # but marginally plausible
                                                       # (or trusted-pattern source)
            # else: essentially unrelated to this brand's real lineup (real
            # verified damaged matches score >= 0.57; a confirmed unrelated
            # token like a random Juniper-adjacent generic-shape read scored
            # 0.375) — drop rather than report a wrong model just because
            # nothing else was competing for the slot. Only reachable when
            # allow_hard_reject=True (brand-agnostic fallback candidates).
        else:
            out.append((token, conf, spec))
    return out


def _catalog_from_texts(brand: str, texts) -> list:
    """Last-resort model recovery: fuzzy WHOLE-STRING match against the
    brand's catalog. Rescues fragments like "clate 100F" -> "FortiGate
    100F" when no token-level candidate exists.

    Uses a whole-string similarity ratio rather than the single longest
    contiguous matching block: OCR damage on a multi-segment model number
    ("CRS317-1G-16S+RM" read as "CAS 317-16-165") is typically SCATTERED
    across several segments rather than one clean break, so the longest
    single matching run can be short even when the overall string is
    clearly the same model — the whole-string ratio still reads it
    correctly (0.667 in that example) where a single-block fraction
    collapses to near zero.
    """
    models = VENDOR_MODELS.get(brand) or []
    if not models:
        return []
    out = []
    for text, conf in texts:
        tk = _norm_model(text)
        if len(tk) < 5 or not any(c.isdigit() for c in tk):
            continue
        best_ratio, second_ratio, best_model = 0.0, 0.0, None
        for m in models:
            mk = _norm_model(m)
            if len(mk) < 5 or abs(len(mk) - len(tk)) > 6:
                continue
            if mk.startswith(tk) or tk.startswith(mk):
                # A pure prefix/truncation relationship ("N1524" is just
                # the start of catalog "N1524-ON") is NOT what this
                # function is for — that's scattered internal damage
                # recovery ("CAS317..." -> "CRS317..."), not "OCR read a
                # shorter, real, valid base SKU and the catalog happens to
                # also carry a longer SKU that starts with it." The latter
                # must never invent characters the photo never showed;
                # it's handled correctly (and separately) by
                # _catalog_adjust's own prefix-preserving branch, which
                # keeps the shorter reading exactly as detected.
                continue
            r = SequenceMatcher(None, tk, mk).ratio()
            if r > best_ratio:
                second_ratio, best_ratio, best_model = best_ratio, r, m
            elif r > second_ratio:
                second_ratio = r
        # An absolute floor alone isn't enough: two near-identical catalog
        # SKUs (port-count variants of the same family) can BOTH score the
        # same ratio against damaged text, and picking one at random is
        # worse than admitting the family, not the exact SKU, is all the
        # photo supports. Requiring a real margin over the runner-up catches
        # a genuine TIE (verified real case: two Edgecore SKUs both scored
        # exactly 0.600, margin 0.000 — correctly declined). It must NOT
        # require as much margin as that when the top match is comfortably
        # ahead: a real D-Link case scored "DGS-1210-28P" at 0.700 against
        # a same-family sibling "DGS-1210-28MP" at 0.667 (margin 0.033) —
        # a real, single, correct answer, just with a smaller gap because
        # the two SKUs happen to be similarly named, not because either
        # reading is ambiguous.
        if (best_model and best_ratio >= 0.55 and (best_ratio - second_ratio) >= 0.03
                and _longest_common_run(tk, _norm_model(best_model)) >= 3):
            out.append((best_model, conf * best_ratio, 0.85))
    return out


def _closest_known_vendor_name(text: str):
    """Last-resort BRAND recovery: compare OCR text against every known
    vendor's actual DISPLAY NAME (not just its alias list), for use only
    when normal alias-based fuzzy matching found nothing for ANY vendor.

    A garbled reading of a KNOWN vendor ("0FS", "Dlinsis", "Netgeer") must
    resolve to that real, verified vendor rather than ever being displayed
    as an unverified, invented brand name. Verified against real data:
    genuine near-misses of a known name score ratio 0.60-0.86 with a
    0.17-0.44 margin over the next-closest vendor name; unrelated or
    genuinely-unlisted words score ratio ~0.47 with ~0.01 margin — a clean
    gap at ratio >= 0.60 with margin >= 0.10.
    """
    tk = _norm(text)
    if len(tk) < 3:
        return None
    scored = []
    for brand in VENDORS:
        bn = _norm(brand)
        if len(bn) < 2:
            continue
        r = SequenceMatcher(None, tk, bn).ratio()
        scored.append((r, brand))
    if len(scored) < 2:
        return None
    scored.sort(key=lambda s: s[0], reverse=True)
    top_r, top_brand = scored[0]
    second_r = scored[1][0]
    if top_r >= 0.60 and (top_r - second_r) >= 0.10:
        return (top_brand, top_r)
    return None


def _best_cross_vendor_catalog_match(texts):
    """Scan EVERY vendor's catalog for a confident whole-string match.

    Used only in the universal fallback, when NO vendor scored any
    evidence at all — not even a fuzzy brand-name hit. Without this, a
    photo with an illegible/absent logo but a garbled-but-recognizable
    model number (e.g. a near-exact OCR read of a real catalog model)
    was reported brandless even though its model closely and uniquely
    matches exactly one vendor's catalog.

    Much stricter than the single-(already-known)-vendor case in
    :func:`_catalog_from_texts`, and margin is computed GLOBALLY across
    every vendor's models combined, not per-vendor: verified against real
    data — genuinely clean readings of a real model (even lightly OCR-
    damaged) score >= 0.769 with a >= 0.05 margin over the next-closest
    model from ANY vendor, while a truly ambiguous/noisy reading with no
    real single answer tops out around 0.63 with ~0.00 margin (several
    unrelated vendors' models tie). A per-vendor-local margin check is not
    enough here: with ~100+ catalogs in play, an unrelated vendor's
    smaller catalog can produce a "clean" local winner by pure chance that
    is no better than another vendor's true match — only a global
    top-1-vs-top-2 comparison (across ALL vendors together) catches that.
    """
    best_overall = None  # (ratio, brand, model, conf)
    for text, conf in texts:
        tk = _norm_model(text)
        if len(tk) < 6 or not any(c.isdigit() for c in tk):
            continue
        scored = []
        for brand, models in VENDOR_MODELS.items():
            for m in models:
                mk = _norm_model(m)
                if len(mk) < 5 or abs(len(mk) - len(tk)) > 6:
                    continue
                r = SequenceMatcher(None, tk, mk).ratio()
                scored.append((r, brand, m))
        if len(scored) < 2:
            continue
        scored.sort(key=lambda s: s[0], reverse=True)
        top_r, top_brand, top_m = scored[0]
        second_r = scored[1][0]
        if top_r >= 0.75 and (top_r - second_r) >= 0.05:
            if best_overall is None or top_r * conf > best_overall[0] * best_overall[3]:
                best_overall = (top_r, top_brand, top_m, conf)
    if best_overall is None:
        return None
    top_r, top_brand, top_m, conf = best_overall
    return (top_brand, top_m, conf * top_r)


def identify_device(detections: List[TextDetection]) -> DeviceID:
    """Infer make + model from OCR output."""
    texts_v = [(d.text, float(d.confidence), d.variant)
               for d in detections if d.text.strip()]
    texts = [(t, c) for t, c, _ in texts_v]
    if not texts:
        return DeviceID()
    #: Zoom-pass strings help find MODELS, but magnified noise must never
    #: invent BRANDS: brand discovery (relaxed fuzzy + prominence inference)
    #: works only on primary-pass detections, with their geometry.
    primary = [d for d in detections if d.variant != "zoom"] or detections
    heights = sorted(d.height for d in primary if d.text.strip()) or [1]
    # lower-middle median: with few detections the logo must still be able
    # to stand out as "prominent"
    median_h = heights[(len(heights) - 1) // 2] or 1
    relaxed_texts = [
        (d.text, float(d.confidence)) for d in primary
        if len(d.text.strip()) >= 4 and len(d.text.split()) == 1
        and (d.height / median_h >= 1.15 or float(d.confidence) >= 0.85)
    ]
    #: Separate, slightly more permissive pool for the closest-known-
    #: vendor-NAME check only (down to 3 chars, matching that function's
    #: own floor): a heavily truncated fragment like "KUS" (from
    #: "Ruckus") is too short for the length-4 rule above, but that rule
    #: exists to protect the ALIAS-relaxed pass specifically — this
    #: mechanism has its own independent, globally-verified-unique safety
    #: margin (ratio >= 0.60 AND >= 0.10 clear of every other vendor
    #: name), so it doesn't need to inherit a threshold tuned for a
    #: different, less rigorous check.
    name_match_texts = [
        (d.text, float(d.confidence)) for d in primary
        if len(d.text.strip()) >= 3 and len(d.text.split()) == 1
        and (d.height / median_h >= 1.15 or float(d.confidence) >= 0.85)
    ]
    #: Fragment-joined strings participate in MODEL matching only (joining
    #: unrelated words must not invent brand evidence).
    joined = _row_joined_texts(detections)
    model_texts = texts + joined
    #: Generic (shape-only) matching gets stricter input: joined fragments
    #: qualify only when every member read confidently — low-confidence
    #: fragment soup is where fake models ("OOR310") come from.
    generic_texts = texts + [j for j in joined if j[1] >= 0.6]
    exact_by_brand = _exact_model_hits(model_texts)

    # ---- 1. score every vendor ---------------------------------------- #
    def _find_best_model(brand: str, patterns: List[str], aliases: List[str],
                         brand_hits: list):
        """Best model candidate for one vendor, given its own regex
        patterns/catalog and the detected texts. Shared by normal
        per-vendor scoring AND the closest-known-vendor-name fallback
        below, so a vendor recovered only by a name-typo match still gets
        the SAME model-matching pipeline as a vendor found normally."""
        # Exact catalog hits are already ground truth — never re-run them
        # through catalog reconciliation (it would incorrectly cap their
        # specificity below the 1.2 exact-match bonus).
        exact_models = list(exact_by_brand.get(brand, []))
        pattern_models = _model_candidates(model_texts, patterns)
        if brand in WEAK_PATTERNS:
            # Weak patterns are, by definition, less specific (e.g. a bare
            # digit run) — they need the SAME plausibility gate the
            # cross-vendor fallback path already applies below, or a
            # contextless number with no letters and no dash ("9368")
            # sails through as "model evidence" for whichever brand's weak
            # pattern happens to match its digit shape.
            pattern_models += [
                m for m in _model_candidates(model_texts, WEAK_PATTERNS[brand],
                                             specificity=0.7)
                if _plausible_model(m[0])
            ]
        # Reconcile the vendor's OWN pattern hits against its real model
        # catalog too, not just the cross-vendor fallback path below. Two
        # concrete failure modes this closes: (1) a generic regex shared
        # loosely by two vendors (e.g. a bare "S####" shape) lets the WRONG
        # vendor claim another brand's model number outright; (2) OCR/
        # row-joining noise tacks a stray trailing character onto an
        # otherwise-real model — the catalog snap corrects or dampens it
        # instead of it sailing through untouched.
        pattern_models = _catalog_adjust(brand, pattern_models)
        models = exact_models + pattern_models
        if brand_hits and not models:
            # The brand is on the photo but its model format is unknown to
            # us: fall back to model-shaped tokens, reconciled with the
            # brand's model catalog. Brand-text lookalikes excluded.
            gen = [m for m in _model_candidates(generic_texts, _GENERIC_PATTERNS,
                                                specificity=0.75)
                   if _plausible_model(m[0]) and _brand_score(m[0], aliases) < 0.8]
            models = _catalog_adjust(brand, gen, allow_hard_reject=True)
            # Always ALSO try whole-string fuzzy-window recovery, not only
            # when the shape-only pass found nothing: a weak, dampened
            # shape-only candidate (specificity 0.35) must never block a
            # much stronger catalog-backed reading from ever competing for
            # best_model — they're merged and the final max() picks
            # whichever is actually better.
            models += _catalog_from_texts(brand, generic_texts)
        best_model = max(models, key=lambda m: (m[2], m[1], len(m[0])), default=None)
        return _prefer_extension(best_model, models)

    def _build_scored_entry(brand: str, brand_hits: list, best_model):
        if not brand_hits and best_model is None:
            return None
        brand_part = max((h["match"] * h["ocr_confidence"] for h in brand_hits),
                         default=0.0)
        model_part = min(1.0, best_model[1] * best_model[2]) if best_model else 0.0
        if brand_hits and best_model:
            confidence = 0.45 * brand_part + 0.55 * model_part
        elif best_model:  # model number alone still implies the brand
            confidence = 0.70 * model_part
        else:
            confidence = 0.50 * brand_part

        agreement = 1
        if best_model:
            agreement = _model_agreement_count(best_model[0], texts_v)
            if agreement >= 2:
                # Independent corroboration: cap the bonus so a flood of
                # near-duplicate zoom crops can't out-vote genuine
                # uncertainty (max +0.15 at 4+ agreeing passes).
                confidence = min(1.0, confidence + 0.05 * min(agreement - 1, 3))

        evidence = brand_hits[:]
        if best_model:
            evidence.append({"text": best_model[0],
                             "ocr_confidence": round(best_model[1], 3),
                             "match": round(best_model[2], 3), "role": "model",
                             "agreement": agreement})
        return {"brand": brand, "model": best_model[0].upper() if best_model else None,
               "confidence": confidence, "evidence": evidence}

    def _score_pass(floor: float) -> List[dict]:
        # The relaxed pass may only look at single-word, PROMINENT (or very
        # confident) primary-pass strings: multi-word descriptors
        # ("Vertical Rackmount" -> "Vertiv"), zoomed-in noise, and small
        # low-confidence text are how false brands get invented.
        brand_texts = texts_v if floor >= 0.80 else \
            [(t, c, "primary") for t, c in relaxed_texts]
        passed: List[dict] = []
        for brand, (aliases, patterns) in VENDORS.items():
            brand_hits = []
            for text, conf, variant in brand_texts:
                s = _brand_score(text, aliases, floor=floor,
                                 length_strict=floor < 0.80)
                # Fuzzy hits need READABLE text behind them (a 0.13-conf
                # smear matching "Perle" at 0.8 is noise), and magnified
                # zoom/rotate strings may only claim a brand via EXACT
                # alias containment, never fuzzily.
                fuzzy_ok = conf >= 0.40 and variant in (
                    "original", "enhanced", "upscaled", "binarized", "gamma",
                    "rotate", "orientation", "deskew", "perspective", "deglare",
                    "channel", "primary")
                if s >= 1.0 or (s > 0 and fuzzy_ok):
                    brand_hits.append({"text": text, "ocr_confidence": round(conf, 3),
                                       "match": round(s, 3), "role": "brand"})
            best_model = _find_best_model(brand, patterns, aliases, brand_hits)
            entry = _build_scored_entry(brand, brand_hits, best_model)
            if entry is not None:
                passed.append(entry)
        return passed

    scored = _score_pass(0.80)
    strict_hit = bool(scored)
    if not scored:
        # Relaxed second chance: heavier OCR damage ("QHAP" -> QNAP,
        # "NETOIAR" -> Netgear) — correcting to a known vendor beats
        # inventing a brand that doesn't exist.
        scored = _score_pass(0.70)

    # ---- 1b. closest-KNOWN-vendor NAME cross-check ---------------------- #
    # Skipped once a STRICT (floor 0.80, exact-alias-containment) hit
    # exists — that's already unambiguous. Otherwise ALWAYS computed, not
    # only when the relaxed alias pass found nothing: that pass checks
    # each vendor's aliases independently and can accept a coincidental
    # cross-vendor overlap uncontested — a real, verified case: "Dlinsis"
    # scored 0.714 against Cisco's "linksys" alias (same length, so a
    # length-similarity gate let it through) while its comparison against
    # D-Link's OWN "dlink" alias was rejected outright by that same gate
    # (length differs by 2) — so the correct vendor never got to compete
    # at all. Checking the text against every vendor's actual NAME (no
    # such gate, but a strict global-uniqueness margin instead) catches
    # what the alias pass structurally cannot.
    if not strict_hit:
        name_hits = []
        for text, conf in name_match_texts:
            match = _closest_known_vendor_name(text)
            if match is None:
                continue
            brand, ratio = match
            aliases, patterns = VENDORS[brand]
            # A small bonus reflects that this match has ALREADY been
            # verified unique against all 117 vendor names — evidence the
            # plain relaxed alias pass never checks — so it can fairly
            # outrank an uncontested but coincidental alias overlap.
            brand_hits = [{"text": text, "ocr_confidence": round(conf, 3),
                          "match": round(min(1.0, ratio + 0.15), 3),
                          "role": "brand (name match, verified unique)"}]
            best_model = _find_best_model(brand, patterns, aliases, brand_hits)
            entry = _build_scored_entry(brand, brand_hits, best_model)
            if entry is not None:
                name_hits.append(entry)
        if name_hits:
            # Merge rather than replace: keep the best entry per brand
            # across both mechanisms, then let the normal confidence sort
            # below pick the overall winner.
            by_brand = {s["brand"]: s for s in scored}
            for nh in name_hits:
                cur = by_brand.get(nh["brand"])
                if cur is None or nh["confidence"] > cur["confidence"]:
                    by_brand[nh["brand"]] = nh
            scored = list(by_brand.values())

    # ---- 2. universal fallback: vendor not in the knowledge base ------- #
    if not scored:
        all_aliases = [a for aliases, _ in VENDORS.values() for a in aliases]
        # Without ANY brand anchor, only contiguous OCR strings are trusted
        # for models — row-joined fragments invent them ("P0-MMN910").
        generic = [m for m in _model_candidates(texts, _GENERIC_PATTERNS)
                   if _plausible_model(m[0]) and _brand_score(m[0], all_aliases) < 0.8]
        best = max(generic, key=lambda m: (m[1], len(m[0])), default=None)
        best = _prefer_extension(best, generic)
        brand_guess = _infer_brand(detections, exclude=best[0] if best else None)

        # Before settling for a brandless guess, check whether the garbled
        # text actually matches some vendor's real catalog closely and
        # uniquely. This is ALWAYS preferred over the brandless guess below
        # when found — it already passed its own strict, data-calibrated
        # bar (global ratio >= 0.75 with a >= 0.05 margin over every other
        # vendor's models combined), which is fundamentally stronger
        # evidence than "some prominent word looks like a brand name" plus
        # "some generic-shaped string exists" (which is what `best` and
        # `brand_guess` below are). Comparing the two on a shared
        # confidence SCALE doesn't work: cross-vendor confidence already
        # multiplies ratio x OCR-confidence, so it reads as artificially
        # low next to the fallback formula's additive weights even when it
        # is the objectively more reliable reading.
        cross = _best_cross_vendor_catalog_match(texts)
        if cross:
            cbrand, cmodel, cscore = cross
            return DeviceID(
                brand=cbrand,
                model=cmodel.upper(),
                confidence=min(1.0, 0.5 + 0.5 * cscore),
                evidence=[{"text": cmodel, "ocr_confidence": round(cscore, 3),
                          "match": round(cscore, 3),
                          "role": "model (cross-vendor catalog match)"}],
            )

        if best or brand_guess:
            # brand_guess is a prominence-based guess ("biggest alphabetic
            # string on the panel"), NOT a name verified against any real
            # vendor — it already failed both the alias-fuzzy passes above
            # AND the closest-known-vendor-name check. It is kept as
            # supporting evidence (useful for debugging/manual review) but
            # is NEVER displayed as the reported brand: only a name that
            # exists in vendors.json may ever populate that field.
            evidence = []
            confidence = 0.0
            if brand_guess:
                evidence.append({"text": brand_guess["text"],
                                 "ocr_confidence": round(brand_guess["conf"], 3),
                                 "match": round(brand_guess["score"], 3),
                                 "role": "brand candidate (NOT in vendors.json — not displayed)"})
            if best:
                evidence.append({"text": best[0], "ocr_confidence": round(best[1], 3),
                                 "match": 0.6, "role": "model (generic shape)"})
                confidence += 0.40 * best[1]
            return DeviceID(
                brand=None,
                model=best[0].upper() if best else None,
                confidence=min(1.0, confidence),
                evidence=evidence,
            )
        return DeviceID()

    # Confidence first; on ties prefer the vendor whose model reading is the
    # most complete (e.g. Zyxel "GS1100-16" over Netgear's partial "GS1100").
    scored.sort(key=lambda s: (s["confidence"], len(s["model"] or "")), reverse=True)
    top = scored[0]
    return DeviceID(
        brand=top["brand"],
        model=top["model"],
        confidence=min(1.0, top["confidence"]),
        evidence=top["evidence"],
        alternates=[{"brand": s["brand"], "model": s["model"],
                     "confidence": round(s["confidence"], 3)}
                    for s in scored[1:4]],
    )
