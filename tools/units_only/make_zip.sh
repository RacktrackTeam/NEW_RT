#!/bin/sh
# Build the stand-alone unit tool for a teammate: tools/units_only/make_zip.sh [out.zip]  (default ~/Desktop/RackTrack-units-only.zip)
# The zip holds this tool, the ladder code and the stitcher from engine/, the unit model from models/ and three sample pictures.
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd); OUT=${1:-$HOME/Desktop/RackTrack-units-only.zip}
T=$(mktemp -d)/RackTrack-units-only; mkdir -p "$T/weights" "$T/samples" "$T/rack_stitcher"
cp "$HERE/identify_units.py" "$HERE/README.md" "$HERE/requirements.txt" "$T/"
cp "$ROOT/engine/unit_grid.py" "$ROOT/engine/rack_stitch.py" "$T/"
cp "$ROOT"/engine/rack_stitcher/*.py "$T/rack_stitcher/"
cp "$ROOT/models/weights/rack_unit_finder.pt" "$T/weights/"
for s in "$ROOT"/tools/units_only/samples/*.jpg; do [ -f "$s" ] && cp "$s" "$T/samples/"; done
rm -f "$OUT"; (cd "$T/.." && zip -qr "$OUT" RackTrack-units-only -x "*.DS_Store" "*/__pycache__/*")
echo "$OUT"; unzip -l "$OUT" | tail -1
