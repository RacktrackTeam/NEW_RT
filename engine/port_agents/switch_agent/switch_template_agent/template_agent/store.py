"""Template store: every template found once is kept and reused.

    template_store/
      templates.db                     SQLite: one row per template image, plus "not found" memory
      images/<VENDOR>/<MODEL>/<hash>.<ext>   the image files themselves

The image file is what the agent uses (offline, fast, never changes); the source
URL and page are kept only as a record of where it came from. One model can
have several templates (hardware revisions, front/angled views) - the photo
decides between them by homography.

Location: ./template_store next to the project, or $TEMPLATE_STORE_DIR.

Command line:
    python -m template_agent store stats
    python -m template_agent store list [VENDOR] [MODEL]
    python -m template_agent store add VENDOR MODEL IMAGE [--note TEXT]
    python -m template_agent store reject ID      # never use this template again
    python -m template_agent store prefer ID      # use this one first for its model
    python -m template_agent store forget-miss VENDOR MODEL
    python -m template_agent store import-dir DIR # import an old vendor_images/ folder
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DIR = Path(os.environ.get("TEMPLATE_STORE_DIR",
                                  Path(__file__).resolve().parent.parent / "template_store"))
MISS_TTL_DAYS = 7

_COMPANY_SUFFIX = ("corporation", "corp", "incorporated", "inc", "limited", "ltd", "co", "company",
                   "networks", "network", "technologies", "technology", "systems", "communications",
                   "electronics", "international", "group")

SCHEMA = """
CREATE TABLE IF NOT EXISTS templates (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor        TEXT NOT NULL,          -- as given, e.g. 'D-Link'
    model         TEXT NOT NULL,          -- as given, e.g. 'DGS-1024C'
    vendor_key    TEXT NOT NULL,          -- normalized, e.g. 'dlink'
    model_key     TEXT NOT NULL,          -- normalized, e.g. 'dgs1024c'
    image_path    TEXT NOT NULL,          -- relative to the store folder
    sha256        TEXT NOT NULL UNIQUE,   -- same image is stored once
    width         INTEGER,
    height        INTEGER,
    source        TEXT,                   -- official/<how> | internet/<how> | manual | import
    source_url    TEXT,                   -- where the image came from (record only)
    page_url      TEXT,
    front_view    REAL,                   -- 0..1 front-panel quality at the time it was chosen
    ports         INTEGER,                -- ports detected on the template
    verified      INTEGER DEFAULT 1,      -- 0: passed no visual check when stored
    status        TEXT DEFAULT 'active',  -- active | preferred | rejected
    use_count     INTEGER DEFAULT 0,
    photo_matches INTEGER DEFAULT 0,      -- times it matched a site photo by homography
    created_at    REAL,
    last_used_at  REAL,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS ix_templates_key ON templates(vendor_key, model_key);
CREATE TABLE IF NOT EXISTS misses (
    vendor_key  TEXT NOT NULL,
    model_key   TEXT NOT NULL,
    vendor      TEXT,
    model       TEXT,
    searched_at REAL,
    reason      TEXT,
    PRIMARY KEY (vendor_key, model_key)
);
"""


def model_key(model: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (model or "").lower())


def model_numbers(model: str) -> tuple[str, ...]:
    """The numbers in a model name, in order: 'CRS328-24P-4S+RM' -> ('328', '24', '4')."""
    return tuple(re.findall(r"\d+", model or ""))


def vendor_key(vendor: str) -> str:
    """'D-Link', 'DLink', 'D-Link Corporation' -> 'dlink'; 'Juniper Networks' -> 'juniper'."""
    words = re.findall(r"[a-z0-9]+", (vendor or "").lower())
    while len(words) > 1 and words[-1] in _COMPANY_SUFFIX:
        words.pop()
    return "".join(words)


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip()).strip("_").upper() or "UNKNOWN"


@dataclass
class StoredTemplate:
    id: int
    vendor: str
    model: str
    path: Path
    source: str
    source_url: str
    page_url: str
    front_view: float
    ports: int
    verified: bool
    status: str
    use_count: int
    photo_matches: int
    created_at: float
    note: str


class TemplateStore:
    def __init__(self, root: Path | str = DEFAULT_DIR):
        self.root = Path(root)
        self.images = self.root / "images"
        self._lock = threading.Lock()
        # PORT_AGENT_STORE_WRITES=1 (a run with --learn) saves into the store on disk. Otherwise the
        # store is read as usual but works on an in-memory copy, and new images go to a temporary
        # folder: nothing in the package changes
        self.read_only = os.environ.get("PORT_AGENT_STORE_WRITES") != "1"
        self._tmp = None
        if self.read_only:
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
            if (self.root / "templates.db").exists():
                disk = sqlite3.connect(f"file:{self.root / 'templates.db'}?mode=ro", uri=True)
                disk.backup(self._db)
                disk.close()
        else:
            self.images.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(self.root / "templates.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    # ------------------------------------------------------------- reading
    def _row(self, r: sqlite3.Row) -> StoredTemplate:
        return StoredTemplate(r["id"], r["vendor"], r["model"], self.root / r["image_path"], r["source"] or "",
                              r["source_url"] or "", r["page_url"] or "", r["front_view"] or 0.0,
                              r["ports"] or 0, bool(r["verified"]), r["status"], r["use_count"],
                              r["photo_matches"], r["created_at"] or 0.0, r["note"] or "")

    def similar_models(self, vendor: str, model: str, min_ratio: float = 0.8, limit: int = 3,
                       same_numbers: bool = True) -> list[str]:
        """Stored models of the same vendor that are `model` spelled differently: the
        same numbers in the same order and close letters (a typo), closest first."""
        from difflib import SequenceMatcher
        mk = model_key(model)
        with self._lock:
            rows = self._db.execute("SELECT DISTINCT model, model_key FROM templates WHERE vendor_key=?"
                                    " AND status != 'rejected'", (vendor_key(vendor),)).fetchall()
        # the numbers in a model name are its spec (CRS328-24P-2S vs -4S): only names with the
        # same numbers are the same model spelled differently
        scored = [(SequenceMatcher(None, mk, r["model_key"]).ratio(), r["model"]) for r in rows
                  if r["model_key"] != mk and (not same_numbers or model_numbers(r["model"]) == model_numbers(model))]
        return [m for ratio, m in sorted(scored, reverse=True) if ratio >= min_ratio][:limit]

    def find(self, vendor: str, model: str, include_rejected: bool = False) -> list[StoredTemplate]:
        """Templates for this vendor + model, best first (preferred, photo-confirmed, verified, used)."""
        q = "SELECT * FROM templates WHERE vendor_key=? AND model_key=?"
        if not include_rejected:
            q += " AND status != 'rejected'"
        q += (" ORDER BY status='preferred' DESC, photo_matches DESC, verified DESC,"
              " front_view DESC, use_count DESC, id")
        with self._lock:
            rows = self._db.execute(q, (vendor_key(vendor), model_key(model))).fetchall()
        return [t for t in map(self._row, rows) if t.path.exists()]

    def get(self, template_id: int) -> StoredTemplate | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, vendor: str = "", model: str = "") -> list[StoredTemplate]:
        q, args = "SELECT * FROM templates WHERE 1=1", []
        if vendor:
            q, args = q + " AND vendor_key=?", args + [vendor_key(vendor)]
        if model:
            q, args = q + " AND model_key=?", args + [model_key(model)]
        with self._lock:
            rows = self._db.execute(q + " ORDER BY vendor_key, model_key, id", args).fetchall()
        return [self._row(r) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            c = self._db.execute
            return {
                "templates": c("SELECT COUNT(*) FROM templates WHERE status!='rejected'").fetchone()[0],
                "models": c("SELECT COUNT(DISTINCT vendor_key||'/'||model_key) FROM templates "
                            "WHERE status!='rejected'").fetchone()[0],
                "vendors": c("SELECT COUNT(DISTINCT vendor_key) FROM templates WHERE status!='rejected'").fetchone()[0],
                "rejected": c("SELECT COUNT(*) FROM templates WHERE status='rejected'").fetchone()[0],
                "remembered_misses": c("SELECT COUNT(*) FROM misses").fetchone()[0],
                "folder": str(self.root),
            }

    # ------------------------------------------------------------- writing
    def add(self, vendor: str, model: str, image_bytes: bytes, *, ext: str = ".png", source: str = "",
            source_url: str = "", page_url: str = "", front_view: float = 0.0, ports: int = 0,
            verified: bool = True, note: str = "", width: int = 0, height: int = 0) -> StoredTemplate:
        """Store an image for vendor + model. The same image (by content) is stored once;
        adding it again returns the existing record."""
        sha = hashlib.sha256(image_bytes).hexdigest()
        with self._lock:
            r = self._db.execute("SELECT * FROM templates WHERE sha256=?", (sha,)).fetchone()
        if r:
            return self._row(r)
        rel = Path("images") / _safe(vendor) / _safe(model) / f"{sha[:16]}{ext}"
        if self.read_only:            # an absolute path: _row() joins it to root unchanged
            if self._tmp is None:
                import tempfile
                self._tmp = Path(tempfile.mkdtemp(prefix="template_store_"))
            rel = self._tmp / rel
        (self.root / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.root / rel).write_bytes(image_bytes)
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO templates (vendor, model, vendor_key, model_key, image_path, sha256, width, height,"
                " source, source_url, page_url, front_view, ports, verified, created_at, note)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (vendor, model, vendor_key(vendor), model_key(model), str(rel), sha, width, height, source,
                 source_url, page_url, front_view, ports, int(verified), now, note))
            self._db.execute("DELETE FROM misses WHERE vendor_key=? AND model_key=?",
                             (vendor_key(vendor), model_key(model)))
            self._db.commit()
            new_id = cur.lastrowid
        return self.get(new_id)

    def mark_used(self, template_id: int, photo_matched: bool = False) -> None:
        with self._lock:
            self._db.execute("UPDATE templates SET use_count=use_count+1, last_used_at=?,"
                             " photo_matches=photo_matches+? WHERE id=?",
                             (time.time(), int(photo_matched), template_id))
            self._db.commit()

    def set_status(self, template_id: int, status: str) -> None:
        if status not in ("active", "preferred", "rejected"):
            raise ValueError(status)
        with self._lock:
            if status == "preferred":   # one preferred template per model
                r = self._db.execute("SELECT vendor_key, model_key FROM templates WHERE id=?",
                                     (template_id,)).fetchone()
                if r:
                    self._db.execute("UPDATE templates SET status='active' WHERE vendor_key=? AND model_key=?"
                                     " AND status='preferred'", (r[0], r[1]))
            self._db.execute("UPDATE templates SET status=? WHERE id=?", (status, template_id))
            self._db.commit()

    # ------------------------------------------------------------- "not found" memory
    def add_miss(self, vendor: str, model: str, reason: str = "") -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO misses VALUES (?,?,?,?,?,?)",
                             (vendor_key(vendor), model_key(model), vendor, model, time.time(), reason))
            self._db.commit()

    def recent_miss(self, vendor: str, model: str, days: float = MISS_TTL_DAYS) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM misses WHERE vendor_key=? AND model_key=?",
                                 (vendor_key(vendor), model_key(model))).fetchone()
        if r and time.time() - r["searched_at"] < days * 86400:
            return dict(r)
        return None

    def forget_miss(self, vendor: str, model: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM misses WHERE vendor_key=? AND model_key=?",
                             (vendor_key(vendor), model_key(model)))
            self._db.commit()

    # ------------------------------------------------------------- import
    def import_dir(self, folder: Path | str, catalog: dict[str, str] | None = None) -> int:
        """Import templates saved by the older pipeline (vendor_images/: <MODEL>.png with
        <MODEL>.source.json). catalog maps a model to its vendor when no source.json exists."""
        folder, n = Path(folder), 0
        for img in sorted(folder.iterdir()):
            if img.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp") or img.name.endswith(".ports.json"):
                continue
            meta = {}
            src = img.with_suffix(".source.json")
            if src.exists():
                meta = json.loads(src.read_text())
            vendor = meta.get("vendor") or (catalog or {}).get(model_key(img.stem))
            model = meta.get("model") or img.stem
            if not vendor:
                continue
            before = self.stats()["templates"]
            t = self.add(vendor, model, img.read_bytes(), ext=img.suffix.lower(),
                     source=f"import/{meta.get('source', 'manual')}", source_url=meta.get("url", ""),
                     page_url=meta.get("page") or "", front_view=meta.get("front_view") or meta.get("quality") or 0.0,
                     ports=meta.get("ports_found") or 0, verified=meta.get("verified", True),
                     note=f"imported from {img}")
            # keep the port layout / panel crop that was checked for this image
            layout = Path(str(img) + ".ports.json")
            dest = Path(str(t.path) + ".ports.json")
            if layout.exists() and not dest.exists():
                dest.write_text(layout.read_text())
            n += self.stats()["templates"] - before
        return n


def _main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="python -m template_agent store", description="Manage the template store.")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    ls = sub.add_parser("list"); ls.add_argument("vendor", nargs="?", default=""); ls.add_argument("model", nargs="?", default="")
    ad = sub.add_parser("add"); ad.add_argument("vendor"); ad.add_argument("model"); ad.add_argument("image"); ad.add_argument("--note", default="")
    for name in ("reject", "prefer", "activate"):
        sp = sub.add_parser(name); sp.add_argument("id", type=int)
    fm = sub.add_parser("forget-miss"); fm.add_argument("vendor"); fm.add_argument("model")
    im = sub.add_parser("import-dir"); im.add_argument("folder")
    a = p.parse_args(argv)
    store = TemplateStore()
    if a.cmd == "stats":
        print(json.dumps(store.stats(), indent=2))
    elif a.cmd == "list":
        for t in store.list(a.vendor, a.model):
            print(f"{t.id:4d}  {t.vendor} {t.model:18s} {t.status:9s} {'verified' if t.verified else 'unverified':10s}"
                  f" used {t.use_count:3d} photo-matched {t.photo_matches:3d}  {t.source:22s} {t.path.relative_to(store.root)}")
    elif a.cmd == "add":
        img = Path(a.image)
        t = store.add(a.vendor, a.model, img.read_bytes(), ext=img.suffix.lower() or ".png", source="manual",
                      note=a.note or f"added by hand from {img.name}")
        print(f"stored as #{t.id}: {t.path}")
    elif a.cmd in ("reject", "prefer", "activate"):
        store.set_status(a.id, {"reject": "rejected", "prefer": "preferred", "activate": "active"}[a.cmd])
        print(f"template #{a.id}: {a.cmd}")
    elif a.cmd == "forget-miss":
        store.forget_miss(a.vendor, a.model)
        print("forgotten; the next lookup searches online again")
    elif a.cmd == "import-dir":
        print(f"imported {store.import_dir(a.folder)} template(s)")


if __name__ == "__main__":
    _main()
