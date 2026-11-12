#!/bin/sh
# Pack the RESULTS of identify_devices.py for a teammate, without any code or model:
#   tools/devices_top5/make_results_zip.sh <out folder> [out.zip]   (default ~/Desktop/RackTrack-devices-top5-results.zip)
# The zip holds, per video: <name>_rack.png, <name>_devices.jpg, <name>_devices.json and <name>_frames/ (the five crops,
# the five whole frames and the top5 sheet of every device), plus a short README.
OUT_DIR=${1:?give the out folder of identify_devices.py}; OUT=${2:-$HOME/Desktop/RackTrack-devices-top5-results.zip}
T=$(mktemp -d)/RackTrack-devices-top5-results; mkdir -p "$T"
for f in "$OUT_DIR"/*_rack.png "$OUT_DIR"/*_devices.jpg "$OUT_DIR"/*_devices.json; do [ -f "$f" ] && cp "$f" "$T/"; done
for d in "$OUT_DIR"/*_frames; do [ -d "$d" ] && cp -R "$d" "$T/"; done
cat > "$T/README.txt" <<'TXT'
RackTrack: the devices found in each rack video and the five clearest frames of each device.

Per video (<name> is the video's file name):
  <name>_rack.png          the video stitched into one picture of the whole rack
  <name>_devices.jpg       that picture with every device outlined in yellow and labelled D1, D2 ... from the top,
                           with the kind the detector gave it and how many frames saw it
  <name>_devices.json      the same, as data: each device's outline on the rack picture, its kind and score, and its
                           five frames (frame number, time in the video, sharpness score, where it sits in the frame)
  <name>_frames/D01_top5.jpg      the five crops of D1 side by side, the best first, with frame number, time and score
  <name>_frames/D01_r1.jpg ..r5   the crops themselves (the device with a small margin)
  <name>_frames/D01_r1_frame.jpg  the whole frame each crop came from, with the device outlined

How the five frames are chosen: every frame of the video is matched to the rack picture around the device; a frame
counts when the whole device is inside it and the match is sound; the frames are ranked by the sharpness of the device
itself and the five best are kept, never two closer than ten frames apart.

What to check and send back: a device outlined that is not one, a device missed, an outline that does not cover the
whole device, a wrong kind, and any of the five frames that is not a clear view of its device.
TXT
rm -f "$OUT"; (cd "$T/.." && zip -qr "$OUT" RackTrack-devices-top5-results -x "*.DS_Store")
echo "$OUT"; unzip -l "$OUT" | tail -1
