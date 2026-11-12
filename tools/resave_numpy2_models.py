"""Re-save the occupancy models that were pickled under numpy 2 so numpy 1.26 (the engine's environment) can load them.

models/v2/classical_clf.joblib and models/v2/embed_clip_clf.joblib were saved on 7 Oct under numpy 2. numpy 2 pickles
the random-number state inside a fitted sklearn model differently (the bit generator class and a (state, seed) tuple
instead of a name and a dict), so numpy 1.26 refuses to load them. The fitted trees and weights themselves are plain
arrays and load unchanged. This script reads each file with small same-named stand-ins that accept the numpy 2 form,
writes it back in the numpy 1.26 form, keeps the original as <name>.numpy2.joblib, then loads the new file in a clean
process and checks that its predictions on fixed random inputs are identical to the patched load.

models/v2/knn_v2_gallery.pkl (teammate 1 v2's k-NN gallery, plain arrays) names numpy 2's module numpy._core; it is read
with that name mapped to numpy.core, written back, and checked array for array in a clean process.

    _cache/venv/bin/python tools/resave_numpy2_models.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [ROOT / "models/v2/classical_clf.joblib", ROOT / "models/v2/embed_clip_clf.joblib"]
GALLERY = ROOT / "models/v2/knn_v2_gallery.pkl"


def _gallery_digest(d):
    import hashlib
    import numpy as np
    h = hashlib.sha256()
    for k in sorted(d):
        v = d[k]
        h.update(k.encode()); h.update(np.ascontiguousarray(v).tobytes() if isinstance(v, np.ndarray) else repr(v).encode())
    return h.hexdigest()


def resave_gallery():
    import pickle
    import shutil as sh

    class Numpy2(pickle.Unpickler):
        def find_class(self, module, name):
            if module.startswith("numpy._core"):
                module = "numpy.core" + module[len("numpy._core"):]
            return super().find_class(module, name)

    backup = GALLERY.with_suffix(".numpy2.pkl")
    src = backup if backup.exists() else GALLERY
    with open(src, "rb") as f:
        d = Numpy2(f).load()           # numpy arrays + numbers only (inspected)
    if not backup.exists():
        sh.copy2(GALLERY, backup)
    with open(GALLERY, "wb") as f:
        pickle.dump(d, f, protocol=4)
    out = subprocess.run([sys.executable, __file__, "--check-gallery"], capture_output=True, check=True, text=True).stdout.strip()
    same = out == _gallery_digest(d)
    print(f"{GALLERY.relative_to(ROOT)}: re-saved, loads in a clean process, arrays identical: {same}")
    if not same:
        sys.exit(1)


def _patch_numpy2_random_pickles():
    import numpy as np
    import numpy.random._pickle as P
    from numpy.random import Generator, RandomState
    from numpy.random.bit_generator import BitGenerator

    def compat(base):
        class C(base):
            def __setstate__(self, s):
                self.state = s[0] if isinstance(s, tuple) else s
        C.__name__ = C.__qualname__ = base.__name__
        return C

    saved = (P.__bit_generator_ctor, P.__generator_ctor, P.__randomstate_ctor)
    known = {n: compat(getattr(np.random, n)) for n in ("MT19937", "PCG64", "PCG64DXSM", "Philox", "SFC64")}

    def bit_gen(name="MT19937"):
        if isinstance(name, BitGenerator):
            return name
        if isinstance(name, type):
            name = name.__name__
        return known[name]()

    P.__bit_generator_ctor = bit_gen
    P.__generator_ctor = lambda name="MT19937", ctor=None: Generator(bit_gen(name))
    P.__randomstate_ctor = lambda name="MT19937", ctor=None: RandomState(bit_gen(name))

    def restore():
        P.__bit_generator_ctor, P.__generator_ctor, P.__randomstate_ctor = saved
    return restore


def _probe(clf):
    import numpy as np
    n = getattr(clf, "n_features_in_")
    X = np.random.default_rng(7).normal(size=(64, n))
    return clf.predict_proba(X)[:, 1]


def check(path):
    """Clean process: load without any patch and print the probe predictions."""
    import joblib
    print(" ".join(repr(float(v)) for v in _probe(joblib.load(path)["clf"])))


def main():
    import joblib
    import numpy as np
    for f in FILES:
        backup = f.with_suffix(".numpy2.joblib")
        src = backup if backup.exists() else f
        restore = _patch_numpy2_random_pickles()
        d = joblib.load(src)
        restore()
        want = _probe(d["clf"])
        if not backup.exists():
            shutil.copy2(f, backup)
        # A plain save under numpy 1.26 writes the random state in its own form (name and dict).
        joblib.dump(d, f)
        out = subprocess.run([sys.executable, __file__, "--check", str(f)], capture_output=True, check=True).stdout
        got = np.array([float(v) for v in out.split()])
        same = np.array_equal(want, got)
        print(f"{f.relative_to(ROOT)}: re-saved under numpy {np.__version__}, loads in a clean process, "
              f"predictions identical on 64 probes: {same}")
        if not same:
            sys.exit(1)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--check":
        check(sys.argv[2])
    elif sys.argv[1:] == ["--check-gallery"]:
        import pickle
        with open(GALLERY, "rb") as f:
            print(_gallery_digest(pickle.load(f)))
    else:
        main()
        resave_gallery()
