"""Router limits for switch_fit's alignment checks.

switch_fit was tuned on 24-48 port switches: its port check needs 6+ ports detected
in the photo and 8+ template/photo port pairs. A router front panel often has 4-16
ports, so with those limits most router fits would be judged on image features
alone, or rejected. use_panel_limits() swaps in the same checks with router-sized
minimums. It changes switch_fit only inside the process that calls it (the router
mapper runs as its own process), so the switch mapper is unaffected.
"""
import cv2
import numpy as np

MIN_SEEN = 3          # detected ports needed before the port check is used (switch_fit: 6)
MIN_PAIRS = 4         # port pairs that prove a port-only alignment (switch_fit: 8)

_done = False

# port kinds that look alike to the port model: an RJ45 data jack, a console, AUX or
# management jack are one family (it often reads one as another); cages and USB are others
FAMILY = {"rj45": "jack", "console": "jack", "aux": "jack", "management_port": "jack", "mgmt": "jack",
          "sfp": "cage", "qsfp": "cage", "usb_a": "usb", "usb_b": "usb", "usb_c": "usb", "usb": "usb"}


def family(kind):
    return FAMILY.get((kind or "").lower())


def use_panel_limits():
    global _done
    if _done:
        return
    import sys
    import switch_fit
    F = sys.modules["switch_fit.fit"]       # the module; switch_fit.fit is also the name of a function

    def port_agreement(tpl, H, photo_ports, photo_shape=None):
        # every jack is a landmark on a router: its chassis often has only two network ports
        # (a Cisco 1841: FE0/0, FE0/1), the rest is console, AUX and USB
        seen = [p for p in photo_ports or [] if p.get("status") != "not seen" and family(p["type"])]
        if len(seen) < MIN_SEEN:
            return None
        data = [q for q in seen if q["type"] in ("rj45", "sfp")] or seen
        pw = float(np.median([q["box"][2] for q in data]))
        dc = np.float32([(q["box"][0] + q["box"][2] / 2, q["box"][1] + q["box"][3] / 2) for q in seen])
        tp = [p for p in tpl.meta["ports"] if family(p["type"])]
        if not tp:
            return None
        tc = np.float32([(p["box"][0] + p["box"][2] / 2, p["box"][1] + p["box"][3] / 2) for p in tp])
        proj = cv2.perspectiveTransform(tc.reshape(-1, 1, 2), np.asarray(H, np.float64)).reshape(-1, 2)
        if photo_shape is not None:
            h, w = photo_shape[:2]
            inside = [i for i, (x, y) in enumerate(proj)
                      if -0.3 * pw <= x <= w + 0.3 * pw and -0.3 * pw <= y <= h + 0.3 * pw]
        else:
            inside = list(range(len(tp)))
        if not inside:
            return dict(on=0.0, covered=0.0, same_kind=0.0, pairs=0, detections=len(seen), checked=0, err=None)
        d = np.linalg.norm(proj[inside][:, None, :] - dc[None, :, :], axis=2)
        pairs, used_t, used_d = [], set(), set()
        for k in np.argsort(d, axis=None):
            a, b = divmod(int(k), d.shape[1])
            if d[a, b] > 0.5 * pw:
                break
            if a not in used_t and b not in used_d:
                used_t.add(a); used_d.add(b); pairs.append((inside[a], b))
        same = sum(family(tp[a]["type"]) == family(seen[b]["type"]) for a, b in pairs)
        errs = [float(np.linalg.norm(proj[a] - dc[b])) / pw for a, b in pairs]
        return dict(on=round(len(pairs) / len(inside), 3), covered=round(len(pairs) / len(seen), 3),
                    same_kind=round(same / max(len(pairs), 1), 3), pairs=len(pairs), detections=len(seen),
                    checked=len(inside), err=round(float(np.mean(errs)), 3) if errs else None)

    def passes(r):
        if r is None or r.get("ports_ok") is False:
            return False
        if r.get("by_ports"):
            return r["inliers"] >= MIN_PAIRS and r["confidence"] >= 0.8
        a = r.get("ports")
        if r.get("ports_ok") and a and a["pairs"] >= MIN_PAIRS and min(a["on"], a["covered"]) >= 0.95:
            return r["inliers"] >= 20 and r["confidence"] >= 0.3
        return r["inliers"] >= F.MIN_INLIERS and r["confidence"] >= F.MIN_CONFIDENCE

    F.port_agreement = port_agreement
    F.passes = passes
    switch_fit.passes = passes
    F.MIN_SNAP_ANCHORS = MIN_SEEN      # snap small panels too (2-8 jacks); switch_fit: 8
    switch_fit.port_agreement = port_agreement
    _done = True
