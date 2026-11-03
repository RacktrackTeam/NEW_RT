"""Run one of the three port-mapping agents on one photo and return its result.

Each agent runs in its own Python process with only its own folder on the import path: the three
agents share module names (panel_ocr, yolo_ports, switch_fit, switch_identify, template_agent) with
different contents, so they must never be imported into one process (review finding #4).

Command line:
    python run_agent.py switch    photo.jpg --out out/
    python run_agent.py router    photo.jpg --out out/ [--vendor Cisco --model ISR4321]
    python run_agent.py appliance photo.jpg --out out/ --device firewall|load_balancer|gateway

From Python (this module imports nothing from the agents):
    from run_agent import map_switch, map_router, map_appliance
    result = map_appliance("photo.jpg", device="firewall", out_dir="out/")

Every call returns the dict the agent writes to <out_dir>/result.json, with these fields always present:
    status    mapped_template | mapped_auto | needs_back | not_mapped | error
    mapped    true when ports were mapped
    error     set when the run failed (the result is still returned)
    spec      the identified model's specification: ports by type, rack units, data sheet link, and
              whether the ports mapped on the photo agree with it (found false with a reason when
              there is none; null when the run failed)
    out_dir   the folder with result.json and the overlay images
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENTS = {"switch": HERE / "switch_agent", "router": HERE / "router_agent", "appliance": HERE / "appliance_agent"}


def _env(extra):
    e = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    e.pop("PORT_AGENT_NORMAL_EXIT", None)
    e.setdefault("PYTHONDONTWRITEBYTECODE", "1")   # nothing written into the agent folders, not even .pyc
    try:                                   # python.org builds on macOS ship no root certificates
        import certifi
        e.setdefault("SSL_CERT_FILE", certifi.where())
        e.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except ImportError:
        pass
    e.update(extra or {})
    return e


def _status(r):
    """The same status field for every agent (the switch agent does not write one itself)."""
    if r.get("status"):
        return r["status"]
    if r.get("error"):
        return "error"
    if r.get("needs_back"):
        return "needs_back"
    if r.get("ports"):
        return "mapped_template" if r.get("mode") == "template" else "mapped_auto"
    return "not_mapped"


def _run(agent, photo, out_dir, args, python, timeout, env):
    folder = AGENTS[agent]
    out = Path(out_dir).resolve() if out_dir else Path(tempfile.mkdtemp(prefix=f"{agent}_agent_"))
    out.mkdir(parents=True, exist_ok=True)
    photo = str(Path(photo).resolve())
    if agent == "switch":
        cmd = [python or sys.executable, "map_any_switch.py", photo, "--no-ask", "--out", str(out)] + args
    else:
        cmd = [python or sys.executable, "-m", f"{agent}_mapper", photo, "--out", str(out)] + args
    e = _env(env)
    e["PYTHONPATH"] = str(folder)          # this agent's folder only
    try:
        p = subprocess.run(cmd, cwd=str(folder), env=e, capture_output=True, text=True, timeout=timeout)
        log = p.stdout + p.stderr
    except subprocess.TimeoutExpired as x:
        p, log = None, f"no result within {timeout} s"
    (out / "agent_log.txt").write_text(log if isinstance(log, str) else "")
    f = out / "result.json"
    if f.exists():
        r = json.loads(f.read_text())
    else:
        last = [l for l in (log or "").strip().splitlines() if l.strip()][-1:] or [""]
        r = {"mapped": False, "error": log if p is None else
             f"the {agent} agent exited with code {p.returncode} and wrote no result: {last[0].strip()[:300]}"}
    r.setdefault("mapped", bool(r.get("ports")) and not r.get("error"))
    r.setdefault("error", None)
    r.setdefault("spec", None)
    r["status"] = _status(r)
    r["agent"] = agent
    r["out_dir"] = str(out)
    f.write_text(json.dumps(r, indent=1))
    return r


def map_switch(photo, vendor=None, model=None, out_dir=None, learn=False, python=None, timeout=None, env=None):
    """The switch agent. vendor/model are optional (read off the photo otherwise). learn=True saves a
    new template (catalogue models only) for next time; without it nothing outside out_dir is written."""
    args = (["--make", vendor] if vendor else []) + (["--model", model] if model else []) + \
           (["--learn"] if learn else [])
    return _run("switch", photo, out_dir, args, python, timeout, env)


def map_router(photo, vendor=None, model=None, out_dir=None, learn=False, refresh=False,
               python=None, timeout=None, env=None):
    """The router agent. learn=True saves a template that fits, for next time."""
    args = (["--vendor", vendor] if vendor else []) + (["--model", model] if model else []) + \
           (["--learn"] if learn else []) + (["--refresh"] if refresh else [])
    return _run("router", photo, out_dir, args, python, timeout, env)


def map_appliance(photo, device=None, vendor=None, model=None, out_dir=None, learn=False, refresh=False,
                  python=None, timeout=None, env=None):
    """The firewall / load balancer / gateway agent. device: firewall, load_balancer or gateway
    (any case, 'load balancer' works); None takes it from the catalogue match."""
    args = (["--device", device] if device else []) + (["--vendor", vendor] if vendor else []) + \
           (["--model", model] if model else []) + (["--learn"] if learn else []) + (["--refresh"] if refresh else [])
    return _run("appliance", photo, out_dir, args, python, timeout, env)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Map the ports on one photo of a switch, router, firewall, "
                                             "load balancer or gateway.")
    ap.add_argument("agent", choices=list(AGENTS))
    ap.add_argument("photo")
    ap.add_argument("--out", required=True)
    ap.add_argument("--device", help="appliance only: firewall, load_balancer or gateway")
    ap.add_argument("--vendor")
    ap.add_argument("--model")
    ap.add_argument("--learn", action="store_true", help="save a template that fits, for next time")
    ap.add_argument("--refresh", action="store_true", help="router / appliance: ignore saved templates")
    ap.add_argument("--timeout", type=float)
    a = ap.parse_args(argv)
    if a.agent == "switch":
        r = map_switch(a.photo, a.vendor, a.model, a.out, a.learn, timeout=a.timeout)
    elif a.agent == "router":
        r = map_router(a.photo, a.vendor, a.model, a.out, a.learn, a.refresh, timeout=a.timeout)
    else:
        r = map_appliance(a.photo, a.device, a.vendor, a.model, a.out, a.learn, a.refresh, timeout=a.timeout)
    print(json.dumps({k: v for k, v in r.items() if k not in ("ports", "homography", "steps")}, indent=1))
    return 0 if r["status"] != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
