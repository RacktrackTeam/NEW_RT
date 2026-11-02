"""The batch runner around the port agents, without loading an agent: the per-crop time limit is a BaseException (the
agents catch Exception around their steps and would otherwise carry on in a weaker mode), a half-written result.json
fails the crop only, and the early stops hold per device group.  Run: python3 -m pytest tests/test_port_agents_batch.py -q"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNNER = ROOT / "src" / "port_agents_batch.py"


def test_timeout_is_not_an_exception():
    src = RUNNER.read_text()
    assert "class CropTimeout(BaseException)" in src
    assert "except CropTimeout" in src and src.index("except CropTimeout") < src.index("except BaseException")


def test_runner_never_imports_our_src():
    """The agents' folder is the only project path: our src/ is removed even though python adds the script's folder."""
    src = RUNNER.read_text()
    assert "sys.path = [p for p in sys.path if pathlib.Path(p or \".\").resolve() != HERE]" in src


def test_batch_survives_a_broken_agent(tmp_path, monkeypatch):
    """A fake agent module in place of the real one: crop 1 raises, crop 2 writes half a json, crop 3 maps, crop 4 maps the
    same model (early stop after it), crop 5 is skipped. The batch ends with os._exit(0) and every crop has an answer."""
    fake = tmp_path / "port_agents"; (fake / "router_agent" / "router_mapper").mkdir(parents=True)
    (fake / "run_agent.py").write_text((ROOT / "port_agents" / "run_agent.py").read_text())
    (fake / "router_agent" / "router_mapper" / "__init__.py").write_text("")
    (fake / "router_agent" / "router_mapper" / "pipeline.py").write_text('''
import json, pathlib
def map_router(photo, vendor=None, model=None, out_dir=None, log=print, **k):
    out = pathlib.Path(out_dir); out.mkdir(parents=True, exist_ok=True); n = pathlib.Path(photo).stem
    if n == "c1": raise RuntimeError("boom")
    if n == "c2": (out / "result.json").write_text("{\\"ports\\": ["); return
    (out / "result.json").write_text(json.dumps({"vendor": "Cisco", "model": "ISR4331", "mode": "template", "ports": [{"id": 1, "type": "rj45", "status": "empty", "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]]}]}))
''')
    runner = tmp_path / "src" / "port_agents_batch.py"; runner.parent.mkdir()
    runner.write_text(RUNNER.read_text())
    jobs = []
    for i in range(1, 6):
        ph = tmp_path / f"c{i}.jpg"; ph.write_bytes(b"x"); jobs.append({"photo": str(ph), "out": str(tmp_path / f"o{i}"), "group": "D1"})
    jf = tmp_path / "jobs.json"; jf.write_text(json.dumps(jobs))
    r = subprocess.run([sys.executable, str(runner), "router", str(jf)], capture_output=True, text=True, cwd=fake / "router_agent",
                       env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(fake / "router_agent"), "PORT_AGENT_CROP_TIMEOUT": "20"}, timeout=60)
    lines = [l.split()[0] for l in r.stdout.splitlines() if l]
    assert r.returncode == 0 and lines == ["done", "done", "done", "done", "skip"], r.stdout + r.stderr
    assert "RuntimeError: boom" in (tmp_path / "o1" / "error.txt").read_text()
    assert "unreadable result.json" in (tmp_path / "o2" / "error.txt").read_text() and not (tmp_path / "o2" / "result.json").exists()
    r3 = json.loads((tmp_path / "o3" / "result.json").read_text()); assert r3["status"] == "mapped_template" and r3["agent"] == "router"
    assert "confirmed by 2 crops" in (tmp_path / "o5" / "error.txt").read_text()
