"""Build and cache tile embeddings for a folder of panel images."""

import hashlib
import os

import numpy as np
from PIL import Image
from tqdm import tqdm

from .embed import Encoder
from .taxonomy import FOLDER_TO_LEAF
from .tiling import make_tiles

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".JPG", ".JPEG", ".PNG"}


def list_gallery(root):
    """Labeled gallery: root/<raw folder>/*.jpg -> [(path, canonical_leaf)]."""
    items = []
    for folder in sorted(os.listdir(root)):
        d = os.path.join(root, folder)
        if not os.path.isdir(d):
            continue
        if folder not in FOLDER_TO_LEAF:
            raise KeyError("Unmapped gallery folder %r -- add it to "
                           "taxonomy.FOLDER_TO_LEAF" % folder)
        leaf = FOLDER_TO_LEAF[folder]
        for f in sorted(os.listdir(d)):
            if os.path.splitext(f)[1].lower() in IMG_EXT:
                items.append((os.path.join(d, f), leaf))
    return items


def list_flat(root):
    """Unlabeled flat folder -> [(path, None)]."""
    return [(os.path.join(root, f), None)
            for f in sorted(os.listdir(root))
            if os.path.splitext(f)[1].lower() in IMG_EXT]


def signature(items, tile_kw):
    """Fingerprint of the input set + tiling, so a stale cache can't be used.

    Counting images is not enough: swapping gallery images while keeping the
    count identical would silently reuse the old vectors. Path, size and
    mtime catch edits, replacements and re-crops; tile_kw catches a changed
    tiling scheme, which changes the embeddings even for identical files.
    """
    h = hashlib.sha1()
    h.update(repr(sorted((tile_kw or {}).items())).encode())
    for path, leaf in items:
        try:
            st = os.stat(path)
            stamp = "%d:%d" % (st.st_size, int(st.st_mtime))
        except OSError:
            stamp = "missing"
        h.update(("%s|%s|%s\n" % (path, leaf, stamp)).encode("utf-8", "replace"))
    return h.hexdigest()


def build(items, encoder, cache=None, tile_kw=None, desc="embedding"):
    """Tile + embed every image. Returns a dict (also saved to `cache`).

    Embeddings are cached to `cache` so repeat runs skip the expensive pass:
    cold is ~11 min for the gallery and ~14 min for the test set on CPU,
    warm is under a second.
    """
    sig = signature(items, tile_kw)
    if cache and os.path.exists(cache):
        z = np.load(cache, allow_pickle=True)
        if (str(z["encoder"]) == encoder.name
                and len(z["paths"]) == len(items)
                and str(z.get("sig", "")) == sig):
            return {k: z[k] for k in z.files}
        print("  cache stale (%s) -- re-embedding" % os.path.basename(cache))

    tile_kw = tile_kw or {}
    tile_emb, tile_img, paths, labels, n_tiles = [], [], [], [], []

    for i, (path, leaf) in enumerate(
            tqdm(items, desc=desc, ncols=78, mininterval=15.0)):
        try:
            with Image.open(path) as im:
                tiles = make_tiles(im, **tile_kw)
        except Exception as e:
            print("  skip %s (%s)" % (os.path.basename(path), e))
            continue
        emb = encoder.encode(tiles)
        tile_emb.append(emb)
        tile_img.append(np.full(len(emb), len(paths), dtype=np.int32))
        n_tiles.append(len(emb))
        paths.append(path)
        labels.append(leaf if leaf is not None else "")

    data = {
        "tile_emb": np.concatenate(tile_emb).astype(np.float32),
        "tile_img": np.concatenate(tile_img),
        "paths": np.array(paths),
        "labels": np.array(labels),
        "n_tiles": np.array(n_tiles),
        "encoder": np.array(encoder.name),
        "sig": np.array(sig),
    }
    # Mean-pooled image vector, used only for near-duplicate grouping.
    img_emb = np.zeros((len(paths), data["tile_emb"].shape[1]), np.float32)
    for i in range(len(paths)):
        m = data["tile_emb"][data["tile_img"] == i].mean(0)
        img_emb[i] = m / (np.linalg.norm(m) + 1e-9)
    data["img_emb"] = img_emb

    if cache:
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        np.savez_compressed(cache, **data)
    return data


def duplicate_groups(img_emb, thresh=0.97):
    """Union-find groups of near-duplicate images.

    The gallery contains the same product shot at several resolutions (e.g.
    61UnG9xzdSL._AC_SL1500_.jpg and 61UnG9xzdSL._AC_SX425_.jpg). If those land
    on opposite sides of a cross-validation split the score is inflated by
    leakage, so CV folds are built over these groups, not over images.
    """
    n = len(img_emb)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    sim = img_emb @ img_emb.T
    for a, b in zip(*np.where(np.triu(sim, 1) >= thresh)):
        union(int(a), int(b))

    roots = {}
    out = np.zeros(n, dtype=np.int32)
    for i in range(n):
        r = find(i)
        out[i] = roots.setdefault(r, len(roots))
    return out
