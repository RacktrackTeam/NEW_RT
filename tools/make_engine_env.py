"""Build the engine's own Python environment, _cache/venv-engine, offline, from the packages already on this Mac.

The engine (server/app.py and everything it loads) was built and tested on this Mac's Python 3.10 with racktrack-app's
packages (numpy 2, torch 2.14, transformers, the DEIMv2 detector's faster-coco-eval and tensorboard, opencv-contrib 4.10).
_cache/venv is RT-EOS's environment (numpy 1.26) and lacks several of them, so the engine runs in its own environment;
the RT-EOS readers keep _cache/venv and are run as child processes.

    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3 -I tools/make_engine_env.py

Run it with the Python whose packages should be copied (the tested one). It makes the venv with that Python, repacks each
needed distribution and its requirements, exactly as installed, into a wheel under _cache/wheels, installs them with
<venv>/bin/python -m pip install --no-index --no-deps, and runs pip check. Nothing is downloaded. On a clean Mac use
requirements.txt instead (README.md).
"""
import base64
import csv
import hashlib
import io
import pathlib
import subprocess
import sys
import zipfile
from importlib.metadata import PackageNotFoundError, distribution

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = pathlib.Path(__file__).resolve().parents[1]
VENV = ROOT / "_cache" / "venv-engine"
WHEELS = ROOT / "_cache" / "wheels"
# What the engine imports (a scan of src, server, port_agents, patchpanel_classify_overlay, scripts, tools and tests),
# with the opencv build that is really on disk (opencv-contrib-python 4.10.0.84) and the proofs' playwright.
TOP = ["fastapi", "uvicorn", "python-multipart", "pydantic", "starlette", "pandas", "numpy", "pillow", "opencv-contrib-python",
       "ultralytics", "torch", "torchvision", "timm", "open_clip_torch", "huggingface_hub", "transformers", "tokenizers",
       "scikit-learn", "scipy", "joblib", "requests", "beautifulsoup4", "PyMuPDF", "PyYAML", "RapidFuzz", "easyocr",
       "rapidocr-onnxruntime", "onnxruntime", "faster-coco-eval", "tensorboard", "calflops", "matplotlib", "openpyxl",
       "PyJWT", "cryptography", "rich", "sympy", "tqdm", "packaging", "certifi", "pytest", "playwright"]
SKIP = {"opencv-python", "opencv-python-headless"}      # same cv2 module as opencv-contrib-python; only one may be installed


def closure(names):
    seen, todo = {}, list(names)
    while todo:
        n = canonicalize_name(todo.pop())
        if n in seen or n in SKIP:
            continue
        try:
            d = distribution(n)
        except PackageNotFoundError:
            print(f"  not installed in the source Python, left out: {n}")
            continue
        seen[n] = d
        for r in d.requires or []:
            req = Requirement(r)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                todo.append(req.name)
    return seen


def repack(d, out):
    """One installed distribution as a wheel: its files under site-packages as they are, a new RECORD."""
    info = pathlib.Path(d._path)
    tags = [l.split(": ", 1)[1].split("-") for l in (info / "WHEEL").read_text().splitlines() if l.startswith("Tag: ")]
    tag = "-".join(".".join(dict.fromkeys(t[i] for t in tags)) for i in range(3))      # py2-none-any + py3-none-any: py2.py3-none-any
    whl = out / f"{d.metadata['Name'].replace('-', '_')}-{d.version}-{tag}.whl"
    site = pathlib.Path(d.locate_file(""))
    rows = []
    with zipfile.ZipFile(whl, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.files or []:
            p = str(f)
            if p.startswith("..") or "__pycache__" in p or p.endswith(".pyc"):
                continue                                   # console scripts are made again by pip from entry_points.txt
            parts = pathlib.PurePosixPath(p).parts
            if parts[0] == info.name and parts[-1] in ("RECORD", "INSTALLER", "direct_url.json", "REQUESTED"):
                continue
            src = site / p
            if not src.is_file():
                continue
            data = src.read_bytes()
            zi = zipfile.ZipInfo(p, date_time=(2026, 1, 1, 0, 0, 0))
            zi.external_attr = (src.stat().st_mode & 0xFFFF) << 16          # keep the executable bit (playwright's driver, torch's tools)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
            rows.append((p, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode(), str(len(data))))
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows + [(f"{info.name}/RECORD", "", "")])
        z.writestr(f"{info.name}/RECORD", buf.getvalue())
    return whl


def main():
    if sys.version_info[:2] != (3, 10):
        sys.exit("run this with the tested Python 3.10")
    WHEELS.mkdir(parents=True, exist_ok=True)
    dists = closure(TOP)
    print(f"{len(dists)} distributions to copy")
    wheels = []
    for n, d in sorted(dists.items()):
        if d.read_text("direct_url.json") and '"editable"' in d.read_text("direct_url.json"):
            print(f"  editable install, left out: {n}")
            continue
        wheels.append(repack(d, WHEELS))
    if not (VENV / "bin" / "python").exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    py = str(VENV / "bin" / "python")
    subprocess.run([py, "-m", "pip", "install", "--quiet", "--no-index", "--no-deps", "--force-reinstall", *map(str, wheels)], check=True)
    subprocess.run([py, "-m", "pip", "check"], check=False)
    with open(ROOT / "_cache" / "venv-engine.txt", "w") as f:
        subprocess.run([py, "-m", "pip", "freeze"], stdout=f, check=True)
    print(f"done: {py}; the installed versions are in _cache/venv-engine.txt")


if __name__ == "__main__":
    main()
