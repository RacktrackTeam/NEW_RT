"""
    python -m template_agent "TP-Link" "TL-SG3424"                       # list candidate images
    python -m template_agent "TP-Link" "TL-SG3424" --photo rack.jpg \\
                             --rj45 24 --sfp 4 --out vendor_images        # choose + save the template
    python -m template_agent store stats|list|add|reject|prefer|...       # manage the template store
"""
import argparse
import json
import logging
import sys

if len(sys.argv) > 1 and sys.argv[1] == "store":      # python -m template_agent store ...
    from template_agent.store import _main
    _main(sys.argv[2:])
    sys.exit(0)

import cv2

from template_agent import find_template, find_template_images, official_domains

p = argparse.ArgumentParser(prog="template_agent",
                            description="Find the front-panel template image for a switch vendor + model.")
p.add_argument("vendor")
p.add_argument("model")
p.add_argument("--photo", help="test photo the template will be aligned to (enables homography matching)")
p.add_argument("--rj45", type=int, default=0, help="expected RJ45 port count, if known")
p.add_argument("--sfp", type=int, default=0, help="expected SFP port count, if known")
p.add_argument("--out", help="save the chosen template here; without it, only list candidates")
p.add_argument("--limit", type=int, default=10)
p.add_argument("--no-search", action="store_true", help="official websites only, no internet search")
p.add_argument("--refresh", action="store_true", help="ignore the template store and search online")
p.add_argument("--verbose", action="store_true")
a = p.parse_args()
logging.basicConfig(level=logging.INFO if a.verbose else logging.WARNING, format="%(name)s: %(message)s")

print(f"Official domains: {', '.join(official_domains(a.vendor)) or '(none found)'}")
if not a.out and not a.photo:
    imgs = find_template_images(a.vendor, a.model, limit=a.limit, use_search=not a.no_search)
    for i in imgs:
        print(f"{i.score:4d}  {i.tier:8s} {i.source:15s} {i.url}")
    if not imgs:
        print("No candidate images found.")
else:
    photo = cv2.imread(a.photo) if a.photo else None
    res = find_template(a.vendor, a.model, photo, rj45=a.rj45, sfp=a.sfp, out_dir=a.out,
                        max_candidates=a.limit, use_search=not a.no_search, refresh=a.refresh)
    print(json.dumps({"saved": str(res.path) if res.path else None, "from_store": res.from_store,
                      "template_id": res.template_id, "url": res.url, "source": res.source,
                      "front_view": res.front_view, "photo_inliers": res.photo_inliers,
                      **res.details}, indent=2))
