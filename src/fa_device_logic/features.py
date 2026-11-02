"""Describe the unit boxes: CLIP + edge features per unit, seam features + CLIP views per neighbour pair."""
import cv2
import numpy as np

from .slot_pipeline import SEAM_FEATURES, adjacent_pairs, edge_features, pair_crops, seam_features, slot_crop


def features(clip, rgb, slots, racks):
    crops = [slot_crop(rgb, s) for s in slots]
    keep = [k for k, c in enumerate(crops) if c.shape[0] >= 4 and c.shape[1] >= 8]
    slots, crops = [slots[k] for k in keep], [crops[k] for k in keep]
    emb = clip.embed(crops)
    edg = np.stack([edge_features(c) for c in crops]) if crops else np.zeros((0, 10), np.float32)
    pairs = adjacent_pairs(slots)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    seam = np.stack([seam_features(gray, rgb, slots[i], slots[j]) for i, j in pairs]) if pairs else np.zeros((0, len(SEAM_FEATURES)), np.float32)
    seam_c, span_c = pair_crops(rgb, slots, pairs)
    return dict(racks=racks, slots=slots, emb=emb, edge=edg, pairs=pairs, seam=seam,
                pair_seam_emb=clip.embed(seam_c), pair_span_emb=clip.embed(span_c))
