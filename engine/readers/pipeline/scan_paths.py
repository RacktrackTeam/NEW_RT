"""Where a scan's files are, seen from the engine.

The server owns the data tree (server/lib/store.js). It hands the engine two
explicit paths: the scan's engine/ folder (the out_dir every step writes into)
and the photo. Nothing in the pipeline builds a path from a repo-root folder.

A scan folder looks like this:

    <scan>/photo.jpg        the picture as taken
    <scan>/engine/          the engine's working files (out_dir)
    <scan>/crops/           device and port cutouts

So from an engine folder the photo is one level up.
"""

from __future__ import annotations

from pathlib import Path


def find_photo(engine_dir) -> Path | None:
    """The scan's photo for an engine folder, or None when there is none."""
    d = Path(engine_dir)
    for p in (d.parent / "photo.jpg", d / "photo.jpg"):
        if p.is_file():
            return p
    return None


def crops_dir(engine_dir) -> Path:
    """The scan's crops/ folder, beside the engine folder."""
    d = Path(engine_dir).parent / "crops"
    d.mkdir(parents=True, exist_ok=True)
    return d
