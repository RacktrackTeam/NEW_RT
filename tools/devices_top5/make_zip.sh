#!/bin/sh
# Build the stand-alone device tool for a teammate: tools/devices_top5/make_zip.sh [out.zip]  (default ~/Desktop/RackTrack-devices-top5.zip)
# The zip holds this tool, the stitcher, the frame locator and the model loader from engine/, the DEIMv2 code from
# engine/third_party/, the two device models from models/weights/ and one sample video.
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd); OUT=${1:-$HOME/Desktop/RackTrack-devices-top5.zip}
T=$(mktemp -d)/RackTrack-devices-top5; mkdir -p "$T/weights" "$T/samples" "$T/rack_stitcher" "$T/third_party"
cp "$HERE/identify_devices.py" "$HERE/README.md" "$HERE/requirements.txt" "$T/"
cp "$ROOT/engine/unit_grid.py" "$ROOT/engine/rack_stitch.py" "$ROOT/engine/frame_locator.py" "$T/"
cp "$ROOT"/engine/rack_stitcher/*.py "$T/rack_stitcher/"
cp -R "$ROOT/engine/third_party/DEIMv2" "$T/third_party/DEIMv2"
cp "$ROOT/models/weights/device_detector_deimv2.pth" "$ROOT/models/weights/device_finder.pt" "$T/weights/"
[ -f "$ROOT/videos/vdo1.mp4" ] && cp "$ROOT/videos/vdo1.mp4" "$T/samples/rack-video.mp4"
find "$T" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null
rm -f "$OUT"; (cd "$T/.." && zip -qr "$OUT" RackTrack-devices-top5 -x "*.DS_Store")
echo "$OUT"; unzip -l "$OUT" | tail -1
