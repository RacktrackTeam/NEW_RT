"""
racktrack.py - use the RackTrack tools (RT-EOS repo) when they are available.

  - switch_ocr (RT-EOS/pipeline/switch_ocr): reads panel text and identifies make and model
    against 117 vendors with model patterns and known-model lists. On 87 real switch
    photos it named the brand in 75 (our plain OCR: 50) and snaps readings to real
    catalogue models. Slower: about 3-35 s per photo.
  - switch-spec-agent (RT-EOS/switch_specs): port configuration, rack units and
    datasheet link for a make/model, from its local database first (fast, ~64 models),
    then a live web lookup.

Set RACKTRACK_REPO to the RT-EOS folder (default: the RT-EOS copy inside this folder; never a
folder outside it). Both the
current layout (pipeline/switch_ocr, switch_specs) and the one before 8 October 2026
(switch_ocr, Agent/Agent_scrap) are found. Without RT-EOS everything here returns None
and callers fall back to map_any_switch's own OCR.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

def _find_repo():
    """$RACKTRACK_REPO (engine.sh sets it to engine/readers), else the RT-EOS copy inside this folder
    (port_agents/switch_agent/RT-EOS, then engine/readers). Never a folder outside this one."""
    if os.environ.get("RACKTRACK_REPO"):
        return Path(os.environ["RACKTRACK_REPO"])
    here = Path(__file__).resolve().parent
    for c in (here / "RT-EOS", here.parent.parent / "readers"):
        if c.is_dir():
            return c
    return here / "RT-EOS"


REPO = _find_repo()


def _first(*candidates):
    """The first folder that exists (current RT-EOS layout first), else the first one."""
    return next((c for c in candidates if c.exists()), candidates[0])


# folder that contains the switch_ocr package, and the spec agent's folder
OCR_PARENT = REPO / "pipeline" if (REPO / "pipeline" / "switch_ocr").exists() else REPO
SWITCH_OCR = OCR_PARENT / "switch_ocr"
SPEC_AGENT = _first(REPO / "switch_specs", REPO / "Agent" / "Agent_scrap")
_reader = None


def available():
    return (SWITCH_OCR / "__init__.py").exists()


def identify(photo_bgr):
    """Make and model with switch_ocr. Returns a dict, or None if switch_ocr is not
    available: {brand, model, confidence, alternates, detections: [(text, conf, polygon)]}."""
    global _reader
    if not available():
        return None
    if str(OCR_PARENT) not in sys.path:
        sys.path.append(str(OCR_PARENT))     # appended, so it never shadows our own modules
    from switch_ocr import OCRConfig, SwitchTextReader, identify_device
    if _reader is None:
        _reader = SwitchTextReader(OCRConfig())
    res = _reader.read(photo_bgr)
    dev = res.device or identify_device(res.detections)
    dets = []
    for d in res.detections:
        poly = getattr(d, "polygon", None) or getattr(d, "box", None)
        dets.append((d.text, float(d.confidence), [[float(x), float(y)] for x, y in poly] if poly is not None else None))
    alts = []
    for a in getattr(dev, "alternates", None) or []:
        alts.append(a if isinstance(a, str) else " ".join(str(v) for v in (getattr(a, "brand", None), getattr(a, "model", None)) if v))
    # how well the brand's own name was read (0 = the brand came from a model pattern or the nearest
    # catalogue model of any vendor, with no brand text on the photo)
    brand_text = max((float(e.get("match", 0) or 0) for e in getattr(dev, "evidence", None) or []
                      if str(e.get("role", "")).startswith("brand")
                      and not str(e.get("role", "")).startswith("brand candidate")), default=0.0)
    return {"brand": getattr(dev, "brand", None), "model": getattr(dev, "model", None),
            "confidence": float(getattr(dev, "confidence", 0) or 0), "alternates": alts[:5], "detections": dets,
            "brand_text": round(brand_text, 3)}


_SPECS = {}            # (vendor, model) -> answer, for the life of the process


def specs(vendor, model, deadline=25):
    """Spec-agent answer for a make/model, or None. Returns the agent's row
    (vendor, model, port_count, port_config, rack_units, datasheet_url, image_url ...)
    plus 'ports' parsed from port_config and 'confidence'. Answers are remembered."""
    if not (SPEC_AGENT / "cli.py").exists():
        return None
    key = (str(vendor).lower(), str(model).upper())
    if key not in _SPECS:
        _SPECS[key] = _specs(vendor, model, deadline)
    return _SPECS[key]


def _specs(vendor, model, deadline):
    try:
        out = subprocess.run([sys.executable, "cli.py", "--json", "--deadline", str(deadline), f"{vendor} {model}"],
                             cwd=SPEC_AGENT, capture_output=True, text=True, timeout=deadline + 15).stdout
        d = json.loads(out.strip().splitlines()[-1])
    except Exception:
        return None
    r = d.get("response") or {}
    if r.get("type") != "spec" or not r.get("result"):
        return {"found": False, "suggestions": r.get("suggestions") or []}
    row = dict(r["result"])
    row.update(found=True, confidence=r.get("confidence"), ports=parse_port_config(row.get("port_config") or ""))
    return row


def parse_port_config(text):
    """'24x 1G RJ45 + 4x 1G SFP' -> {'rj45': 24, 'sfp': 4}. SFP+, SFP28 and QSFP count as
    their own kinds; combo ports are counted once, under SFP."""
    out = {}
    for n, kind in re.findall(r"(\d+)\s*[x×]\s*(?:[\d.]+\s*[GM](?:b(?:ase-?T)?)?\s*)?([A-Za-z0-9+/-]+)", text):
        k = kind.lower()
        k = "rj45" if k in ("rj45", "rj-45", "base-t", "copper", "gbe") else \
            "qsfp" if k.startswith("qsfp") else "sfp" if k.startswith("sfp") else k
        out[k] = out.get(k, 0) + int(n)
    return out


def ports_from_model_name(model):
    """Port counts spelled out in the model name, e.g. CRS328-24P-4S+RM -> 24 RJ45 + 4 SFP,
    C1000-24T-4G-L -> 24 + 4, S3900-24T4S -> 24 + 4, CRS326-24G-2S+RM -> 24 + 2.
    Only the explicit '<n><copper letter>-<m><uplink letter>' form is trusted; returns
    None otherwise (names like TL-SG3424 or GS724T don't say how ports are split)."""
    m = re.search(r"(?<!\d)(\d{1,2})(?:TP|GP|FP|MP|PS|HP|GT|P|T|G)-?(\d{1,2})(?:XS|SFP|S|X|G|Q)(?![0-9])",
                  model.upper())
    if not m:
        # text_match.parse_port_spec reads a few more forms;
        # used only when it gives both counts, since a partial count could reject a
        # correct template
        try:
            import text_match
            spec = text_match.parse_port_spec(model)
        except Exception:
            spec = None
        if spec and "rj45" in spec and "sfp" in spec and 4 <= spec["rj45"] <= 52 and 1 <= spec["sfp"] <= 8:
            return {"rj45": spec["rj45"], "sfp": spec["sfp"]}
        return None
    rj, up = int(m.group(1)), int(m.group(2))
    # SFP switches spell it the other way round: CRS317-1G-16S+ is 1 RJ45 + 16 SFP
    if not (1 <= rj <= 52 and 1 <= up <= 48 and rj + up <= 56 and (rj >= 4 or up >= 4)):
        return None
    return {"rj45": rj, "sfp": up}

# Port counts switches are actually sold in. A trailing pair of digits that is not
# one of these is a series number, not a port count.
_SOLD_PORT_COUNTS = {5, 8, 9, 10, 12, 16, 18, 20, 24, 26, 28, 48, 50, 52}


def ports_from_model_digits(model):
    """Port count implied by the digits at the end of a model number.

    Most families encode it in the last two digits: TL-SG1016 is 16 ports, TL-SG1024
    is 24, TL-SG3424 is 24, DGS-1016D is 16, GS724T is 24, PowerConnect 2824 is 24,
    TL-SG2008 is 8. ports_from_model_name only reads the explicit "24P-4S" form and
    returns nothing for any of these, which leaves the common case with no count at
    all.

    This is a naming convention rather than a rule, so it is the last thing asked:
    only counts switches are really sold in are believed, and the answer is a TOTAL -
    it does not say how the ports split between RJ45 and SFP. DES-1210-28P comes back
    28 though the panel is 24 copper + 4 combo, which is right as a total and wrong as
    a copper count, so callers must treat it as the size of the port block, not as a
    spec to reject a template on.
    """
    if not model:
        return None
    text = re.sub(r"[^A-Z0-9]", " ", model.upper())
    best = None
    for tok in text.split():
        if re.fullmatch(r"\d+W", tok):
            continue      # a PoE power budget, not a port count: US-8-150W is 8 ports, not 50
        digits = re.sub(r"[^0-9]", "", tok)
        if not digits:
            continue
        for n in (int(digits[-2:]) if len(digits) >= 2 else None,
                  int(digits) if len(digits) <= 2 else None):
            if n in _SOLD_PORT_COUNTS:
                best = n          # later tokens win: the count trails the series
                break
    return {"rj45": best} if best else None
