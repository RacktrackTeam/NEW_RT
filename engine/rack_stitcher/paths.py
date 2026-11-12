"""Folder conventions (in this folder): videos in videos/ (one folder at the root for every video), stitched rack pictures in data/stitched/ (runtime).
The app always passes its own paths; these defaults serve the command line (python3 -m rack_stitcher, from src/)."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = ROOT / "videos"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "stitched"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def resolve_input(video: str | Path, input_dir: str | Path | None = None) -> Path:
    """Find the video file.

    `video` may be an existing path (absolute or relative to the current directory) or just a
    file name, which is then looked up in `input_dir` (default: videos/).
    """
    candidate = Path(video).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    folder = Path(input_dir).expanduser() if input_dir else DEFAULT_INPUT_DIR
    in_folder = folder / candidate.name if candidate.parent == Path(".") else folder / candidate
    if in_folder.is_file():
        return in_folder.resolve()
    raise FileNotFoundError(
        f"Video '{video}' not found (looked at '{candidate}' and '{in_folder}')."
    )


def list_videos(input_dir: str | Path | None = None) -> list[Path]:
    """All video files in the input folder, sorted by name."""
    folder = Path(input_dir).expanduser() if input_dir else DEFAULT_INPUT_DIR
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS)


def prepare_output(video_path: Path, output_dir: str | Path | None, output_name: str | None) -> Path:
    """Create the output folder if needed and return the output file path."""
    folder = Path(output_dir).expanduser() if output_dir else DEFAULT_OUTPUT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    name = output_name or f"{video_path.stem}_rack.png"
    if Path(name).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
        name += ".png"
    return folder / name
