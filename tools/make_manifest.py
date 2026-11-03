"""models/MANIFEST.txt: every file under models/ and samples/ with its size and SHA-256, and where it comes from.

    _cache/venv-engine/bin/python tools/make_manifest.py          write the manifest
    _cache/venv-engine/bin/python tools/make_manifest.py --check  compare the files on disk with it (exit 1 on a difference)

models/ and samples/ are not in git (too large). A clean Mac gets them copied from a Mac that has them (README.md);
--check says whether the copy is complete and unchanged.
"""
import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "models" / "MANIFEST.txt"
ORIGIN = {
    "models/v2": "racktrack-app: the occupancy classifiers (trained here; classical, CLIP head and k-NN re-saved under numpy 1.26 by tools/resave_numpy2_models.py, originals kept as *.numpy2.*)",
    "models/device_cls": "racktrack-app: the device classifiers (ConvNeXt-Tiny) and the DINOv2-B detector",
    "models/device_join": "racktrack-app: the device join models",
    "models/ports": "racktrack-app: the port models",
    "models/weights": "racktrack-app weights/: the rack unit finder, the device finder, the DEIMv2 device detector",
    "models/rt_eos": "RT-EOS Models/: the readers' models (port type, connected or empty, cable, dust cap, SFP cage, device and unit finders)",
    "models/dark_mobile": "dark_mobile Models/: the cable reader",
    "models/torch": "torch hub: DINOv2 code (facebookresearch/dinov2) and checkpoints (DINOv2 ViT-L/14, ResNet-50), copied from ~/.cache/torch",
    "models/huggingface": "Hugging Face: CLIP ViT-B/32 openai, timm DINOv2 ViT-B/14, the DINOv2-B config, copied from ~/.cache/huggingface",
    "models/easyocr": "easyocr detector and English reader, copied from ~/.EasyOCR",
    "samples": "the test videos, the stored runs of the three videos and the owner's rack record",
}


def files():
    for top in ("models", "samples"):
        for f in sorted((ROOT / top).rglob("*")):
            if f.is_file() and not f.is_symlink() and f.name not in ("MANIFEST.txt", ".DS_Store") and "__pycache__" not in f.parts:
                yield f


def digest(f):
    h = hashlib.sha256()
    with open(f, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def origin(rel):
    return next((v for k, v in sorted(ORIGIN.items(), key=lambda kv: -len(kv[0])) if rel.startswith(k + "/") or rel == k), "")


def main():
    if sys.argv[1:] == ["--check"]:
        want = {}
        for line in OUT.read_text().splitlines():
            if line and not line.startswith("#"):
                sha, size, rel = line.split("  ", 2)
                want[rel] = (sha, int(size))
        bad = [rel for rel, (sha, size) in want.items() if not (ROOT / rel).is_file() or (ROOT / rel).stat().st_size != size or digest(ROOT / rel) != sha]
        print(f"{len(want) - len(bad)} of {len(want)} files present and unchanged" + (": missing or changed " + ", ".join(bad[:10]) if bad else ""))
        return 1 if bad else 0
    lines = ["# models/ and samples/: SHA-256, size in bytes, path (two spaces between). Made by tools/make_manifest.py.", "# Where each folder comes from:"]
    lines += [f"#   {k}: {v}" for k, v in ORIGIN.items()]
    n = 0
    for f in files():
        rel = str(f.relative_to(ROOT))
        lines.append(f"{digest(f)}  {f.stat().st_size}  {rel}"); n += 1
    OUT.write_text("\n".join(lines) + "\n")
    print(f"{n} files in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
