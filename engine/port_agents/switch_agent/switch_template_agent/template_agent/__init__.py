"""template_agent - find the correct front-panel template for any switch vendor + model.

    from template_agent import find_template
    res = find_template("TP-Link", "TL-SG3424", photo, rj45=24, sfp=4, out_dir="vendor_images")
    print(res.path, res.source, res.front_view, res.photo_inliers)

1. sources.py  - candidate images: the vendor's official websites first
                 (found from the vendor name; sitemaps, official search, web
                 shop), then the open internet. No per-vendor code, no logins.
2. selector.py - the correct one, by visual features: front-view checks
                 (panel shape, port grid, level rows) and a homography fit
                 against the test photo.
3. store.py    - every template found is kept in template_store/ (images +
                 SQLite) and checked first next time; the internet is searched
                 only when no stored template fits.
"""
from .selector import TemplateResult, find_template, front_view_score, photo_match, template_filename
from .sources import ImageCandidate, find_template_images, official_domains
from .store import StoredTemplate, TemplateStore

__all__ = ["find_template", "find_template_images", "TemplateResult", "ImageCandidate",
           "front_view_score", "photo_match", "official_domains", "template_filename",
           "TemplateStore", "StoredTemplate"]
