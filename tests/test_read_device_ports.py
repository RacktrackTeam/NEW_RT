"""Ports on demand: rack_video.read_device_ports reads one device of a finished run back from the video (its top frames'
homographies are stored in result.json), sends only that device to the agents, and updates result.json in place.
The agents are stubbed.  Run: python3 -m pytest tests/test_read_device_ports.py -q"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine"))
import cv2
import numpy as np

import pipeline as P
import rack_video as RV


def _video(path, n=8):
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (640, 360))
    for i in range(n):
        f = np.full((360, 640, 3), 40 + 20 * i, np.uint8); cv2.rectangle(f, (100, 100), (540, 160), (200, 200, 200), -1); w.write(f)
    w.release()


def test_one_device_only(tmp_path, monkeypatch):
    calls = []
    def fake_agents(jobs, work, timeout=None, progress=None):
        calls.extend(j["key"] for j in jobs)
        return {j["key"]: {"ok": True, "kind": "switch", "vendor": "TP-Link", "model": "TL-SG2428P", "mode": "template", "status": "mapped_template", "confidence": 0.9,
                           "ports": [{"id": 1, "type": "rj45", "status": "empty", "photo_polygon": [[110, 110], [120, 110], [120, 120], [110, 120]]}],
                           "n_ports": 1, "by_type": {"rj45": 1}, "connected": 0, "pictures": {}} for j in jobs}
    monkeypatch.setattr(P, "agent_layouts", fake_agents)
    monkeypatch.setattr(P, "add_port_details", lambda layout, cut, work, key: layout)
    monkeypatch.setattr(RV, "rack_overlay", lambda *a, **k: None)
    video = tmp_path / "v.mp4"; _video(video)
    poly = [[100, 100], [540, 100], [540, 160], [100, 160]]
    H = np.eye(3).tolist()
    devs = [{"index": 1, "polygon": poly, "top": "u20", "bottom": "u20", "height": 1, "units": ["u20"], "classification": {"label": "Switch", "prob": 0.9},
             "top_frames": [{"rank": 1, "frame": 2, "H": H}, {"rank": 2, "frame": 5, "H": H}]},
            {"index": 2, "polygon": [[100, 200], [540, 200], [540, 260], [100, 260]], "top": "u19", "bottom": "u19", "height": 1, "units": ["u19"],
             "classification": {"label": "Switch", "prob": 0.9}, "top_frames": [{"rank": 1, "frame": 3, "H": H}]}]
    (tmp_path / "result.json").write_text(json.dumps({"devices": devs, "times_s": {"rack": 1.0}}))
    rack = np.full((800, 700, 3), 90, np.uint8)
    dv = RV.read_device_ports(tmp_path, video, rack, 1)
    assert sorted(calls) == ["D1_r1", "D1_r2", "D1_rack"]                      # device 1 only: its rack crop and its two frames
    assert dv["layout"]["fitted"] is True and dv["layout"]["model"] == "TL-SG2428P" and dv["layout_seconds"] >= 0
    r = json.loads((tmp_path / "result.json").read_text())
    assert r["devices"][0].get("layout") and not r["devices"][1].get("layout")   # device 2 untouched
    assert r["times_s"]["layout"] == r["devices"][0]["layout_seconds"] and r["times_s"]["rack"] == 1.0
    # reading again replaces, it does not duplicate
    RV.read_device_ports(tmp_path, video, rack, 1)
    assert len(calls) == 6 and len(json.loads((tmp_path / "result.json").read_text())["devices"]) == 2


def test_not_a_port_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "agent_layouts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    video = tmp_path / "v.mp4"; _video(video)
    devs = [{"index": 1, "polygon": [[100, 100], [540, 100], [540, 160], [100, 160]], "top": "u20", "bottom": "u20", "height": 1, "units": ["u20"],
             "classification": {"label": "Patch Panel", "prob": 0.9}, "top_frames": [{"rank": 1, "frame": 2, "H": np.eye(3).tolist()}]}]
    (tmp_path / "result.json").write_text(json.dumps({"devices": devs}))
    dv = RV.read_device_ports(tmp_path, video, np.full((800, 700, 3), 90, np.uint8), 1)
    assert "layout" not in dv


def test_rack_crop_only_when_frames_are_missing(tmp_path, monkeypatch):
    """An old run without stored homographies: the device is read from its rack crop alone, and an unfitted answer does
    not crash the chooser."""
    calls = []
    monkeypatch.setattr(P, "agent_layouts", lambda jobs, work, timeout=None, progress=None: (calls.extend(j["key"] for j in jobs) or
                        {j["key"]: {"ok": True, "kind": "switch", "vendor": None, "vendor_read": "D-Link", "model": None, "mode": "auto", "status": "mapped_auto",
                                    "ports": [{"id": 1, "type": "rj45", "status": "connected", "photo_polygon": [[110, 110], [120, 110], [120, 120], [110, 120]]}],
                                    "n_ports": 1, "by_type": {"rj45": 1}, "connected": 1, "pictures": {}} for j in jobs}))
    monkeypatch.setattr(P, "add_port_details", lambda layout, cut, work, key: layout)
    monkeypatch.setattr(RV, "rack_overlay", lambda *a, **k: None)
    video = tmp_path / "v.mp4"; _video(video)
    devs = [{"index": 1, "polygon": [[100, 100], [540, 100], [540, 160], [100, 160]], "top": "u20", "bottom": "u20", "height": 1, "units": ["u20"],
             "classification": {"label": "Switch", "prob": 0.9}, "top_frames": [{"rank": 1, "frame": 2}]}]
    (tmp_path / "result.json").write_text(json.dumps({"devices": devs}))
    dv = RV.read_device_ports(tmp_path, video, np.full((800, 700, 3), 90, np.uint8), 1)
    assert calls == ["D1_rack"] and dv["layout"]["auto"] is True and dv["layout"]["n_ports"] == 1
