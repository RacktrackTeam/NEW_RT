"""Steps 5 and 6: every classified device goes to the right port agent (port_agents/). The agents are stubbed, so no model loads.
Run: python3 -m pytest tests/test_device_routing.py -q"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import numpy as np
import pytest
from PIL import Image

import pipeline as P
import rack_video as RV

LABELS = ["Switch", "Router", "Firewall", "Gateway", "Server", "Other"]


@pytest.fixture
def stubs(monkeypatch):
    """pipeline.agent_layouts stubbed: switches answer mapped_template, routers mapped_template, firewalls needs_back,
    gateways mapped_auto with two ports; the calls are recorded per agent as the real runner would route them."""
    calls = {"switch": [], "devices": [], "details": []}

    def fake_agents(jobs, work, timeout=None, progress=None):
        out = {}
        for j in jobs:
            kind = j.get("kind") or "switch"
            if kind == "switch":
                calls["switch"].append(j["key"])
                out[j["key"]] = {"ok": True, "kind": "switch", "vendor": "D-Link", "model": "DGS-1024C", "mode": "template", "status": "mapped_template",
                                 "confidence": 0.8, "ports": [], "n_ports": 0, "by_type": {}, "connected": 0, "pictures": {}}
            else:
                calls["devices"].append((j["key"], kind))
                auto = kind == "gateway"
                out[j["key"]] = {"ok": True, "kind": kind, "vendor": None, "model": None, "status": "mapped_auto" if auto else ("needs_back" if kind == "firewall" else "mapped_template"),
                                 "mode": "auto" if auto else ("needs_back" if kind == "firewall" else "template"), "needs_back": kind == "firewall",
                                 "ports": [{"id": 1, "type": "rj45", "status": "connected", "photo_polygon": [[1, 1], [5, 1], [5, 5], [1, 5]]},
                                           {"id": 2, "type": "rj45", "status": "empty", "photo_polygon": [[6, 1], [9, 1], [9, 5], [6, 5]]}] if auto else [],
                                 "n_ports": 2 if auto else 0, "by_type": {"rj45": 2} if auto else {}, "connected": 1 if auto else 0, "pictures": {}}
        return out

    monkeypatch.setattr(P, "agent_layouts", fake_agents)
    monkeypatch.setattr(P, "add_port_details", lambda layout, cut, work, key: calls["details"].append(key) or layout)
    return calls


def test_agent_of():
    assert P.AGENT_OF == {"switch": "switch", "router": "router", "firewall": "appliance", "load_balancer": "appliance", "gateway": "appliance"}


def test_mapper_kinds():
    assert P.MAPPER_KIND == {"Switch": "switch", "Router": "router", "Firewall": "firewall", "Gateway": "gateway"}


def test_photo_routing(stubs, tmp_path):
    devs = [{"index": i + 1, "box": (50, 50 + 90 * i, 750, 120 + 90 * i), "classification": {"label": l}} for i, l in enumerate(LABELS)]
    P.photo_switch_layouts(Image.new("RGB", (800, 700)), devs, tmp_path)
    assert stubs["switch"] == ["D1"]
    assert dict(stubs["devices"]) == {"D2": "router", "D3": "firewall", "D4": "gateway"}
    assert [d.get("layout", {}).get("kind") for d in devs] == ["switch", "router", "firewall", "gateway", None, None]
    assert devs[2]["layout"]["needs_back"] is True and devs[2]["layout"]["ports"] == []    # lights only: no ports shown
    assert devs[3]["layout"]["mode"] == "auto" and devs[3]["layout"]["n_ports"] == 2       # auto mode: the agent's ports are kept
    assert stubs["details"] == ["D1", "D2", "D3", "D4"]     # the RT-EOS port model runs on every mapped device, fitted or not
    assert all(d["layout"]["vendor"] is None for d in devs[1:4] if not d["layout"]["fitted"])   # no make without a fitted model


def test_video_routing(stubs, tmp_path):
    poly = lambda y: [[100, y], [700, y], [700, y + 60], [100, y + 60]]
    devs = [{"index": i + 1, "polygon": poly(100 * (i + 1)), "top": f"u{39 - i:02d}", "bottom": f"u{39 - i:02d}", "height": 1,
             "classification": {"label": l, "prob": 0.9}, "top_frames": [{"rank": 1, "frame": 10 * i}]} for i, l in enumerate(LABELS)]
    frame = np.full((720, 1280, 3), 100, np.uint8)
    views = {d["index"]: {1: (frame, np.eye(3))} for d in devs}
    RV.switch_layouts(devs, views, tmp_path, np.full((1200, 800, 3), 128, np.uint8))
    assert set(stubs["switch"]) == {"D1_rack", "D1_r1"}
    assert dict(stubs["devices"]) == {"D2_rack": "router", "D2_r1": "router", "D3_rack": "firewall", "D3_r1": "firewall",
                                      "D4_rack": "gateway", "D4_r1": "gateway"}
    assert [d.get("layout", {}).get("kind") for d in devs] == ["switch", "router", "firewall", "gateway", None, None]
    assert devs[3]["layout"]["auto"] is True and devs[3]["layout"]["n_ports"] == 2 and devs[3]["layout"]["fitted"] is False
    assert all("rack_polygon" in p for p in devs[3]["layout"]["ports"])                 # auto ports are carried onto the rack too


def test_runner_guards():
    """Our batch runner keeps the guards: every failure caught, a time limit per crop, his status rule, os._exit at the end."""
    src = (pathlib.Path(P.ROOT) / "src" / "port_agents_batch.py").read_text()
    assert "except BaseException" in src
    assert "signal.alarm(LIMIT[0])" in src
    assert "run_agent._status(r)" in src
    assert "os._exit(0)" in src
    assert (P.PORT_AGENTS / "run_agent.py").exists() and all((P.PORT_AGENTS / f"{a}_agent").is_dir() for a in ("switch", "router", "appliance"))


def test_agent_result_mapping(tmp_path):
    """His result.json, in each of the four statuses, into our layout: ports with the origin added, the make only when sure."""
    import json
    o = tmp_path / "x"; o.mkdir()
    def rd(r):
        (o / "result.json").write_text(json.dumps(r)); return P._read_agent_result(o, (100, 50), "switch")
    t = rd({"status": "mapped_template", "vendor": "TP-Link", "model": "TL-SG2428P", "ports": [{"id": 1, "type": "rj45", "status": "empty", "photo_polygon": [[1, 2], [3, 2], [3, 4], [1, 4]]}]})
    assert t["mode"] == "template" and t["vendor"] == "TP-Link" and t["model"] == "TL-SG2428P" and t["ports"][0]["photo_polygon"] == [[101, 52], [103, 52], [103, 54], [101, 54]]
    a = rd({"status": "mapped_auto", "vendor": "Arista Networks", "identify_confidence": {"vendor": 0.8, "model": 0.0}, "ports": [{"id": 1, "type": "rj45", "status": "connected", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}]})
    assert a["mode"] == "auto" and a["n_ports"] == 1 and a["vendor"] is None and a["vendor_read"] == "Arista Networks"     # 0.8 < VENDOR_SURE: not shown
    b = rd({"status": "needs_back", "vendor": "Sophos", "needs_back": True, "identify_confidence": {"vendor": 1.0, "vendor_doubt": False}, "ports": []})
    assert b["mode"] == "needs_back" and b["needs_back"] is True and b["vendor"] == "Sophos" and b["ports"] == []
    e = rd({"status": "error", "error": "boom", "ports": []})
    assert e["ok"] is False and e["mode"] == "none"
    (o / "result.json").unlink(); (o / "error.txt").write_text("no result within 240 s")
    n = P._read_agent_result(o, (0, 0), "router")
    assert n["ok"] is False and "240" in n["error"]


def test_photo_no_make_without_fit(monkeypatch, tmp_path):
    """A switch whose layout did not fit shows no make (the read is kept as vendor_read)."""
    monkeypatch.setattr(P, "agent_layouts", lambda jobs, work, timeout=None, progress=None: {j["key"]: {"ok": True, "vendor": None, "vendor_read": "Aruba", "model": None,
                        "mode": "none", "status": "not_mapped", "ports": [], "pictures": {}} for j in jobs})
    monkeypatch.setattr(P, "add_port_details", lambda layout, cut, work, key: layout)
    devs = [{"index": 1, "box": (50, 50, 750, 120), "classification": {"label": "Switch"}}]
    P.photo_switch_layouts(Image.new("RGB", (800, 200)), devs, tmp_path)
    L = devs[0]["layout"]
    assert L["vendor"] is None and L["vendor_read"] == "Aruba" and L["ports"] == [] and L["fitted"] is False
