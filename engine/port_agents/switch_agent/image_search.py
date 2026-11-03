"""Bing image search for a straight-on front view of a switch model.

Adapted from teammate 1's (Jaya) switch_identify.py. The async Bing endpoint with
adlt=moderate returns real results to a script (the /images/search page does not).
A result must name the model in its title or URL, and its subject must be a wide,
short strip: a front-panel view rather than a three-quarter render.
"""
import os
import html
import json
import re
import urllib.parse
import urllib.request

from text_match import squash


_UA = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                   " (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8",
}


def _safe_url(url):
    """Percent-encode a URL so non-ASCII characters survive the request.

    Image results routinely carry accented or CJK characters in their path, and
    http.client encodes the request line as ASCII, so these raise rather than
    404 -- an unhandled crash in the middle of a long run.
    """
    parts = urllib.parse.urlsplit(url)
    try:
        netloc = parts.netloc.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        netloc = parts.netloc
    return urllib.parse.urlunsplit((
        parts.scheme, netloc,
        urllib.parse.quote(parts.path, safe="/%@:"),
        urllib.parse.quote(parts.query, safe="=&%?+/:,"),
        ""))


def http_get(url, timeout=25):
    req = urllib.request.Request(_safe_url(url), headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# A rack switch photographed straight on is a wide, short strip; the same unit
# shot at an angle is much closer to square.  Aspect ratio is therefore a direct
# test of "is this a front-panel view", which is the only view worth putting
# beside a faceplate photo.
STRAIGHT_MIN_ASPECT = 3.2
GOOD_ENOUGH_ASPECT = 4.5
MAX_CANDIDATES = 10


def _content_box(arr):
    """Bounding box of the subject, ignoring a flat background.

    Product photos are routinely padded onto a square white canvas, so the file's
    own proportions say nothing about the shape of the thing in it: the official
    TP-Link shot of a 1U switch is a 4:3 image of a 5.4:1 switch.  Measuring the
    subject instead is what makes "is this a front-panel view" answerable.
    """
    import cv2
    import numpy as np
    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY) if arr.ndim == 3 else arr
    h, w = gray.shape[:2]
    if h < 8 or w < 8:
        return None
    border = np.concatenate([gray[0], gray[-1], gray[:, 0], gray[:, -1]])
    mask = np.abs(gray.astype(np.int16) - int(np.median(border))) > 18
    ys, xs = np.where(mask)
    if len(xs) < 32:
        return None
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    if x1 <= x0 or y1 <= y0:
        return None
    return x0, y0, x1, y1


def _aspect(data):
    """(subject width / height, subject width) for encoded bytes, or (0, 0)."""
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return 0.0, 0
        box = _content_box(arr)
        if not box:
            h, w = arr.shape[:2]
            return (w / float(h), w) if h else (0.0, 0)
        x0, y0, x1, y1 = box
        return (x1 - x0 + 1) / float(y1 - y0 + 1), x1 - x0 + 1
    except Exception:
        return 0.0, 0


def search_reference_image(vendor, model, timeout=25):
    """Find a straight-on product photo online for a model the workbooks lack.

    Two filters, both necessary:

    * the model number must appear in the result's title or URL - image search
      happily returns another product from the same brand, and a confident photo
      of the wrong switch is worse than no photo;
    * the image must be wide and short - a front-panel view.  Most product shots
      are three-quarter renders, which are useless for comparing against a
      faceplate.
    """
    if os.environ.get("PORT_AGENT_SEARCH_ENGINES", "0") != "1":
        return None   # search-engine scraping (Bing/Yandex through a browser) is opt-in (review #27)
    query = "%s %s switch front panel" % (vendor or "", model)
    url = ("https://www.bing.com/images/async?q=%s&first=0&count=35"
           "&adlt=moderate&mmasync=1" % urllib.parse.quote(query))
    try:
        page = http_get(url, timeout).decode("utf-8", "replace")
    except Exception:
        # A reference photo is a nicety; nothing here is worth failing a run over.
        return None

    target = squash(model)
    best = None
    tried = 0
    for attr in re.findall(r'\sm="([^"]*)"', page):
        if tried >= MAX_CANDIDATES:
            break
        if "murl" not in attr:
            continue
        try:
            item = json.loads(html.unescape(attr))
        except ValueError:
            continue
        murl, title = item.get("murl") or "", item.get("t") or ""
        if not murl or target not in squash(title + murl):
            continue
        try:
            data = http_get(murl, timeout)
        except Exception:
            continue  # dead host, bad certificate, unencodable URL - try the next
        if len(data) < 2000:
            continue  # a placeholder or tracking pixel, not a product shot
        tried += 1

        ratio, width = _aspect(data)
        if ratio < STRAIGHT_MIN_ASPECT:
            continue
        candidate = {"bytes": data, "url": murl, "title": title,
                     "aspect": round(ratio, 2)}
        # Wider is more side-on; among those, prefer the larger file.
        if not best or (ratio, width) > (best["aspect"], best.get("width", 0)):
            candidate["width"] = width
            best = candidate
        if ratio >= GOOD_ENOUGH_ASPECT:
            break  # clearly a front-panel strip, no need to keep downloading
    return best
