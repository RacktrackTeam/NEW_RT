"""Run one of Srikanth's port agents (port_agents/, the 28 Oct 2026 "spec" package) on a list of crops, in one process, the models loaded once.

    python3 src/port_agents_batch.py switch|router|appliance jobs.json

Started by pipeline.agent_layouts with the agent's own folder as the only import path (PYTHONPATH) and as the
working directory, as port_agents/run_agent.py does: the three agents share module names and must never meet in one
process (his review fix #4). Nothing in port_agents/ is changed; each crop is mapped exactly as his command line maps
it, and his result.json is left as he writes it, plus the "status" his run_agent.py adds.

jobs.json: [{"photo": PATH, "out": DIR, "group": DEVICE, "kind": firewall|load_balancer|gateway (appliance),
             "vendor": ..., "model": ...}] in order; a device's crops share a group. Two early stops per group, ours,
to cut the time: once SWITCH_MODEL_AGREE (2) crops read the same model with a fitted template the rest of the device
are skipped, and once SWITCH_NOTHING_AFTER (3) crops read no make at all the rest are skipped. A skipped crop gets
error.txt saying why. Prints "done <out>" or "skip <out> <why>" per crop; exits with os._exit as his entry points do
(the OCR runtime's teardown can abort on macOS after a good result).
"""
import copy
import json
import os
import pathlib
import signal
import sys
import traceback

sys.dont_write_bytecode = True                                        # nothing written into his folders, not even .pyc (as his run_agent.py)

AGENT = sys.argv[1]
HERE = pathlib.Path(__file__).resolve().parent
PORT_AGENTS = HERE.parent / "port_agents"
MODEL_AGREE = int(os.environ.get("SWITCH_MODEL_AGREE", "2"))
NOTHING_AFTER = int(os.environ.get("SWITCH_NOTHING_AFTER", "3"))
PER_CROP = int(os.environ.get("PORT_AGENT_CROP_TIMEOUT", "120"))      # seconds; his agents have no time limit of their own (a crop usually takes 10-25 s)

sys.path.insert(0, str(PORT_AGENTS))
import run_agent  # noqa: E402  (imports nothing from the agents; gives us his _status rule)
sys.path.remove(str(PORT_AGENTS))
sys.path = [p for p in sys.path if pathlib.Path(p or ".").resolve() != HERE]     # never our src/ (python puts the script's folder first)
sys.path.insert(0, str(PORT_AGENTS / f"{AGENT}_agent"))
os.chdir(PORT_AGENTS / f"{AGENT}_agent")

if AGENT == "switch":
    import map_any_switch
    RUN0 = copy.deepcopy(map_any_switch.RUN)                           # his run state as the module starts it
elif AGENT == "router":
    from router_mapper.pipeline import map_router
else:
    from appliance_mapper.pipeline import map_appliance


class CropTimeout(BaseException):
    """Not an Exception: the agents catch Exception around their steps (and would carry on in a weaker mode after a cut
    OCR), so the time limit must pass straight through them."""


LIMIT = [PER_CROP]


def _alarm(*_):
    raise CropTimeout(f"no result within {LIMIT[0]} s")


def run_one(job):
    out = pathlib.Path(job["out"]); out.mkdir(parents=True, exist_ok=True)
    lines = []
    log = lambda *a: lines.append(" ".join(str(x) for x in a))
    LIMIT[0] = int(job.get("timeout") or PER_CROP)
    signal.signal(signal.SIGALRM, _alarm); signal.alarm(LIMIT[0])
    try:
        if AGENT == "switch":
            sys.argv = ["map_any_switch.py", job["photo"], "--no-ask", "--out", str(out)] \
                       + (["--make", job["vendor"]] if job.get("vendor") else []) + (["--model", job["model"]] if job.get("model") else [])
            os.environ["PORT_AGENT_CACHE_DIR"] = str(out / "_cache")   # the spec lookup's cache stays in the run, never in his folder
            map_any_switch.RUN.clear(); map_any_switch.RUN.update(copy.deepcopy(RUN0))   # his entry point runs one crop per process: the run state must not carry over (a model read on one crop was given to the next)
            try:
                map_any_switch.main()
                map_any_switch.finish_result()                          # as his __main__ does: status, identify_confidence and the spec (28 Oct package)
            except SystemExit as e:
                if e.code not in (None, 0):
                    (out / "error.txt").write_text(f"exit {e.code}")
            finally:
                for d in getattr(map_any_switch, "RUN_TEMP", []):    # as his __main__ does: a run without --learn leaves nothing behind
                    import shutil; shutil.rmtree(d, ignore_errors=True)
                try:
                    map_any_switch.RUN_TEMP.clear()
                except Exception:
                    pass
        elif AGENT == "router":
            map_router(job["photo"], vendor=job.get("vendor"), model=job.get("model"), out_dir=str(out), log=log)
        else:
            map_appliance(job["photo"], vendor=job.get("vendor"), model=job.get("model"), out_dir=str(out), log=log, device=job.get("kind"))
    except CropTimeout as ex:
        (out / "error.txt").write_text(str(ex))
        f = out / "result.json"                           # whatever an earlier step wrote is half an answer: kept aside, not used
        if f.exists():
            f.rename(out / "result_partial.json")
    except BaseException as ex:  # noqa: BLE001
        (out / "error.txt").write_text(f"{type(ex).__name__}: {ex}\n{traceback.format_exc()}")
    finally:
        signal.alarm(0)
    if lines:
        (out / "agent_log.txt").write_text("\n".join(lines))
    f = out / "result.json"
    try:
        r = json.loads(f.read_text()) if f.exists() else {}
        if not isinstance(r, dict):
            raise ValueError("result.json is not an object")
    except (ValueError, OSError) as ex:                   # a run cut short mid-write leaves half a file: that crop failed, the batch goes on
        (out / "error.txt").write_text(f"unreadable result.json: {ex}")
        try:
            f.unlink()
        except OSError:
            pass
        return {}
    if r:                                                 # the same normalisation his run_agent.py applies
        r.setdefault("mapped", bool(r.get("ports")) and not r.get("error")); r.setdefault("error", None)
        r["status"] = run_agent._status(r); r["agent"] = AGENT; r["out_dir"] = str(out)
        f.write_text(json.dumps(r, indent=1))
    return r


def main():
    jobs = json.load(open(sys.argv[2]))
    state = {}
    for job in jobs:
        try:
            _one(job, state)
        except BaseException as ex:  # noqa: BLE001  (a bookkeeping error must not lose the rest of the batch)
            pathlib.Path(job["out"]).mkdir(parents=True, exist_ok=True)
            pathlib.Path(job["out"], "error.txt").write_text(f"runner: {type(ex).__name__}: {ex}")
            print(f"done {job['out']}", flush=True)


def _agree_file(job):
    """The agreement ledger shared by every process of one batch: one line per fitted template, "<group>\t<model key>".
    A device's crops are spread over the processes, so the "two crops agree, skip the rest" rule must see across them."""
    return pathlib.Path(job["out"]).parent / "_agree.txt"


def _agreed(job, g):
    f = _agree_file(job)
    if not f.exists():
        return None
    keys = [ln.split("\t", 1)[1] for ln in f.read_text().splitlines() if ln.startswith(g + "\t") and "\t" in ln]
    for k in keys:
        if sum(1 for m in keys if m.endswith(k) or k.endswith(m)) >= MODEL_AGREE:
            return k
    return None


def _one(job, state):
    g = job.get("group") or job["out"]; st = state.setdefault(g, {"models": [], "n": 0, "makes": 0, "stop": None})
    if not st["stop"]:
        k = _agreed(job, g)
        if k:
            st["stop"] = f"model {k} confirmed by {MODEL_AGREE} crops (another process may have read them)"
    if st["stop"]:
        pathlib.Path(job["out"]).mkdir(parents=True, exist_ok=True)
        pathlib.Path(job["out"], "error.txt").write_text(f"skipped: {st['stop']}")
        print(f"skip {job['out']} {st['stop']}", flush=True); return
    r = run_one(job)
    print(f"done {job['out']}", flush=True)
    st["n"] += 1
    if r.get("vendor"):
        st["makes"] += 1
    if r.get("model") and r.get("status") == "mapped_template":
        k = "".join(c for c in str(r["model"]).upper() if c.isalnum())   # "TL-SG2428P" and "SG2428P" are one model
        with open(_agree_file(job), "a") as f:
            f.write(f"{g}\t{k}\n")
        if _agreed(job, g):
            st["stop"] = f"model {r['model']} confirmed by {MODEL_AGREE} crops"
    if st["n"] >= NOTHING_AFTER and st["makes"] == 0:
        st["stop"] = f"no make read on {st['n']} crops"


if __name__ == "__main__":
    try:
        main()
    finally:
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(0)
