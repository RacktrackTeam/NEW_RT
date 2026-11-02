"""
Final device pipeline (NO device model).

  1. units model (tiled + overlap removal)          -> 1U unit boxes U1..Un, top to bottom
  2. describe each unit (CLIP + 10 edge features)    -> slot classifier: empty / occupied
  3. for each pair of occupied neighbours            -> 21 join edge measurements + seam features + CLIP views
                                                        P(same) = 0.75 * edge joining model + 0.25 * original pair classifier
  4. join when P(same) >= 0.80                        -> devices, each with its units and size in U

Usage:
    from device_logic import DevicePipeline
    pipe = DevicePipeline()
    result = pipe.run("rack.jpg")                  # or a PIL image / RGB numpy array
    result["devices"]  -> [{"id": "D1", "units": [3, 4], "size_u": 2, "box": [x1, y1, x2, y2]}, ...]
    result = pipe.run("rack.jpg", occupied=[...])  # optional: give the occupied units yourself (list of bools)
"""
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

from .features import features
from .seam_edges import pair_edge_feats
from .slot_pipeline import MODELS, PairNet, SlotAnalyzer, SlotNet, pair_inputs, slot_inputs
from .units import dedup, raw, tiled

W_OLD, THR = 0.25, 0.80            # joining: weight of the original pair classifier, join threshold


def group(pairs, occ, join, n):
    """Union-find: occupied neighbours i, j are one device when join[k] is True for pair k = (i, j)."""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for (i, j), jn in zip(pairs, join):
        if occ[i] and occ[j] and jn:
            parent[find(j)] = find(i)
    dev = np.full(n, -1, int)
    for k in range(n):
        if occ[k]:
            dev[k] = find(k)
    return dev


class DevicePipeline(SlotAnalyzer):
    def __init__(self):
        super().__init__()                                            # units model, CLIP, original classifiers
        self.ck_new = torch.load(MODELS / "slot_models_tiled.pt", weights_only=False)
        self.snet = SlotNet(self.ck_new["slot_dim"]); self.snet.load_state_dict(self.ck_new["slot_state"]); self.snet.eval()
        self.old = PairNet(self.ck["pair_dim"]); self.old.load_state_dict(self.ck["pair_state"]); self.old.eval()
        j = torch.load(MODELS / "join_model.pt", weights_only=False)
        self.j = j
        self.join = torch.nn.Sequential(torch.nn.Linear(len(j["mu"]), j["hidden"]), torch.nn.GELU(), torch.nn.Dropout(0.0),
                                        torch.nn.Linear(j["hidden"], 1))
        self.join.load_state_dict(j["state"]); self.join.eval()
        self.empty_thr = float(self.ck_new["empty_thr"])

    # ---------------------------------------------------------------- steps
    def find_units(self, rgb):
        racks, full = raw(self.units, rgb)
        return racks, dedup(tiled(self.units, rgb, racks, full), racks)

    def describe(self, rgb, slots, racks):
        f = features(self.clip, rgb, slots, racks)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        f["seam2"] = (np.stack([pair_edge_feats(gray, f["slots"][i], f["slots"][j]) for i, j in f["pairs"]])
                      if f["pairs"] else np.zeros((0, 21), np.float32))
        return f

    @torch.no_grad()
    def probabilities(self, f):
        """Per unit: P(open), P(closed), P(occupied). Per neighbour pair: P(same device)."""
        if not len(f["slots"]):
            return np.zeros((0, 3), np.float32), np.zeros(0, np.float32)
        c = self.ck_new
        ps = torch.softmax(self.snet(torch.tensor(slot_inputs(f["emb"], f["edge"], f["pairs"], c["edge_mu"], c["edge_sd"]))), 1).numpy()
        if not f["pairs"]:
            return ps, np.zeros(0, np.float32)
        base = pair_inputs(f, ps, self.ck["seam_mu"], self.ck["seam_sd"], self.ck["edge_sd"])
        p_old = torch.sigmoid(self.old(torch.tensor(base))).numpy()
        e = (f["seam2"] - self.j["mu2"]) / self.j["sd2"]
        P = f["pairs"]
        above = {j: k for k, (i, j) in enumerate(P)}; below = {i: k for k, (i, j) in enumerate(P)}
        ctx = [np.concatenate([e[above[i]] if i in above else np.zeros(21), e[below[j]] if j in below else np.zeros(21),
                               [float(i in above), float(j in below)]]) for i, j in P]
        X = np.hstack([base, e, np.array(ctx)]).astype(np.float32)
        p_new = torch.sigmoid(self.join(torch.tensor((X - self.j["mu"]) / self.j["sd"])).squeeze(-1)).numpy()
        return ps, (W_OLD * p_old + (1 - W_OLD) * p_new).astype(np.float32)

    # ---------------------------------------------------------------- one call
    def run(self, image, occupied=None):
        t0 = time.perf_counter()
        if isinstance(image, (str, Path)):
            image = Image.open(image)
        rgb = np.array(image.convert("RGB")) if isinstance(image, Image.Image) else image
        racks, slots = self.find_units(rgb)
        f = self.describe(rgb, slots, racks)
        ps, pp = self.probabilities(f)
        n = len(f["slots"])
        occ = np.array(occupied, bool) if occupied is not None else ((ps[:, 0] + ps[:, 1]) < self.empty_thr if n else np.zeros(0, bool))
        dev = group(f["pairs"], occ, pp >= THR, n) if n else np.zeros(0, int)
        S = np.asarray(f["slots"], int) if n else np.zeros((0, 4), int)
        order = sorted(set(dev[dev >= 0].tolist()), key=lambda d: S[dev == d][:, 1].min())
        devices = []
        for k, d in enumerate(order, 1):
            ks = np.where(dev == d)[0]
            b = S[ks]
            devices.append(dict(id=f"D{k}", units=[int(x) + 1 for x in ks], size_u=int(len(ks)),
                                box=[int(b[:, 0].min()), int(b[:, 1].min()), int(b[:, 2].max()), int(b[:, 3].max())]))
        units = [dict(unit=f"U{k + 1}", box=[int(v) for v in S[k]], occupied=bool(occ[k]),
                      p_occupied=round(float(ps[k, 2]), 3), device=next((d["id"] for d in devices if k + 1 in d["units"]), None))
                 for k in range(n)]
        joins = [dict(upper=f"U{i + 1}", lower=f"U{j + 1}", p_same=round(float(pp[k]), 3),
                      decision=("joined" if pp[k] >= THR else "boundary") if occ[i] and occ[j] else "not compared")
                 for k, (i, j) in enumerate(f["pairs"])]
        return dict(units=units, devices=devices, joins=joins, racks=[[int(v) for v in r] for r in racks],
                    seconds=round(time.perf_counter() - t0, 2), _features=f, _occupied=occ, _device_index=dev, _p_same=pp)
