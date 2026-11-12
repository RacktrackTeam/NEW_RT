"""Turn an extreme-aspect-ratio panel image into port-scale square tiles.

Why this exists: ~95% of both the gallery and the test crops are wider than
4:1 (median ~10:1, test mean 764x87). Feeding those straight into a 224x224
encoder squashes them ~10x horizontally and every port smears into mush --
the embedding then encodes "black rectangular rack panel", which is identical
for all 11 classes. The discriminating signal is the shape of a *single port*,
which is 1-3% of the image area, so we crop at port scale instead.
"""

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def make_tiles(img, scales=(1.0, 0.5), stride_frac=0.5, min_px=40,
               out_size=224, max_tiles=36, flat_std=4.0):
    """Crop `img` into square port-scale tiles.

    scales      window side as a fraction of the image's short edge. 1.0 gives
                a full-height square (a few ports); 0.5 isolates a single row
                of a 2U panel / roughly one port.
    stride_frac step between windows, as a fraction of the window side.
    flat_std    drop tiles whose pixel std is below this (dead black borders,
                blank label strips) -- they carry no port signal and only add
                noise to the vote.

    Returns a list of RGB PIL images, each out_size x out_size.
    """
    img = img.convert("RGB")
    W, H = img.size
    base = min(W, H)
    arr_full = np.asarray(img)

    boxes = []
    for s in scales:
        win = int(round(base * s))
        if win < min_px:
            # Image is already tiny; don't shred it into sub-port confetti.
            continue
        win = min(win, W, H)
        step = max(1, int(round(win * stride_frac)))

        xs = list(range(0, max(1, W - win + 1), step))
        ys = list(range(0, max(1, H - win + 1), step))
        # Always include the flush-right / flush-bottom window so the last
        # ports on a panel are never dropped.
        if xs[-1] != W - win and W > win:
            xs.append(W - win)
        if ys[-1] != H - win and H > win:
            ys.append(H - win)

        for y in ys:
            for x in xs:
                boxes.append((x, y, x + win, y + win))

    if not boxes:
        boxes = [(0, 0, W, H)]

    # Drop featureless tiles before the (expensive) resize.
    kept = []
    for b in boxes:
        patch = arr_full[b[1]:b[3], b[0]:b[2]]
        if patch.size and patch.std() >= flat_std:
            kept.append(b)
    if not kept:
        kept = boxes

    # Bound compute: subsample uniformly rather than truncating, so we keep
    # coverage across the whole panel width instead of just the left end.
    if len(kept) > max_tiles:
        idx = np.linspace(0, len(kept) - 1, max_tiles).round().astype(int)
        kept = [kept[i] for i in sorted(set(idx.tolist()))]

    return [img.crop(b).resize((out_size, out_size), Image.BICUBIC)
            for b in kept]


def tile_count_estimate(size, scales=(1.0, 0.5), stride_frac=0.5, min_px=40):
    W, H = size
    base = min(W, H)
    n = 0
    for s in scales:
        win = int(round(base * s))
        if win < min_px:
            continue
        win = min(win, W, H)
        step = max(1, int(round(win * stride_frac)))
        n += len(range(0, max(1, W - win + 1), step)) * \
             len(range(0, max(1, H - win + 1), step))
    return max(n, 1)
