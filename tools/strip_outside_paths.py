"""Remove the paths into other folders that copied runs still carry (verify pass 2, finding 16).

    _cache/venv-engine/bin/python tools/strip_outside_paths.py [run folders...]

The stored runs were copied from racktrack-app, so their status.json names that app's video file and the port agents'
job files name its run folder. Every JSON string in a run folder that starts with /Volumes/ or /Users/ and passes through
the run's own folder becomes the path inside the run folder (status.json video_file becomes "video.mp4", which the
engine reads from the run folder). A port agent's template picture becomes its path in this folder's port_agents/. Anything else is listed, not changed. With no
argument: every run under data/runs and samples/video/run_of_vdo1.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUTSIDE = re.compile(r"^/(Volumes|Users)/")


def run_dirs():
    yield from (f.parent for f in (ROOT / "data" / "runs").glob("**/status.json"))
    yield ROOT / "samples" / "video" / "run_of_vdo1"


def fix(value, rid, left):
    if isinstance(value, dict):
        return {k: fix(v, rid, left) for k, v in value.items()}
    if isinstance(value, list):
        return [fix(v, rid, left) for v in value]
    if isinstance(value, str) and OUTSIDE.match(value):
        parts = value.split("/")
        if rid in parts:
            return "/".join(parts[parts.index(rid) + 1:]) or "."
        if "port_agents" in parts:                                   # a port agent's template picture: the same file in this folder
            return "/".join(parts[parts.index("port_agents"):])
        if "switch_template_agent" in parts:
            return "port_agents/switch_agent/" + "/".join(parts[parts.index("switch_template_agent"):])
        left.add(value)
    return value


def main():
    dirs = [pathlib.Path(a) for a in sys.argv[1:]] or list(run_dirs())
    changed, left = 0, set()
    for d in dirs:
        names = {d.name}
        st = d / "status.json"
        if st.exists():
            names.add(json.loads(st.read_text()).get("id") or d.name)
        for f in d.rglob("*.json"):
            text = f.read_text()
            if "/Volumes/" not in text and "/Users/" not in text:
                continue
            data = json.loads(text)
            new = data
            for rid in names | {"91af02c09bbc"}:                       # the stored runs are copies of racktrack-app's run 91af02c09bbc
                new = fix(new, rid, set())
            new = fix(new, "\0", left)
            if f.name == "status.json" and isinstance(new, dict) and new.get("video_file"):
                new["video_file"] = pathlib.Path(new["video_file"]).name
            if new != data:
                f.write_text(json.dumps(new, indent=1)); changed += 1
    print(f"{changed} files changed; {len(left)} outside paths not under a run folder left as they were")
    for v in sorted(left)[:20]:
        print("  ", v)


if __name__ == "__main__":
    main()
