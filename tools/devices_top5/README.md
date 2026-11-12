# RackTrack devices and top 5 frames: from a video to every device and its five clearest frames

This is the device step of RackTrack on its own, for testing on a laptop. Give it a video of a rack (one slow sweep of
the front, top to bottom) and it stitches the video into one picture of the whole rack, places the video's frames on
that picture, runs the app's two device detectors on the frames and on the picture, joins the boxes into devices, and
then looks through every frame of the video for the five clearest views of each device. It is the same code and the
same models the app runs; nothing else of the app is here (no units, no ports, no make and model).

## Install (once)

Python 3.10 or newer. In this folder:

    python -m venv .venv
    source .venv/bin/activate          (Windows: .venv\Scripts\activate)
    pip install -r requirements.txt

The first install pulls PyTorch (about 2 GB). The two model files are in weights/ already; the device detector's code
is in third_party/DEIMv2/.

## Run

    python identify_devices.py rack.mp4 --out out
    python identify_devices.py samples --out out            (a whole folder of videos)

For a video you get, in out/:

- `<name>_rack.png`: the stitched picture of the whole rack.
- `<name>_devices.jpg`: that picture with every device outlined in yellow and labelled: D1, the kind the typed finder
  gave it (or "device"), and how many placed frames saw it. D1 is the top device.
- `<name>_devices.json`: every device (its outline on the rack picture, kind, score, which frames saw it) with its five
  frames: frame number, time in the video, the sharpness score, where the device sits in that frame, and the files.
- `<name>_frames/D01_top5.jpg`: the five crops of D1 side by side, the best first, each with its frame number, time
  and score.
- `<name>_frames/D01_r1.jpg` to `D01_r5.jpg`: the crops themselves (the device with a small margin), and
  `D01_r1_frame.jpg` to `D01_r5_frame.jpg`: the whole frame each crop came from, with the device outlined.

The terminal says, per video: the stitch size and the stitcher's filming notes, how many frames were placed, how many
devices, and one line per device with its kind, how many frames saw it and its five frame numbers. A 30 second video
takes a few minutes on a laptop: the stitch, then the device detector on every placed frame, then the frame search.

Options: `--score 0.5` is the device detector's confidence cut (lower finds more boxes, with more mistakes).
`--stride 5` places every 5th frame for detection. `--no-deim` skips the DEIMv2 device detector and uses the typed
finder alone (faster, fewer devices). `--device cpu` forces the CPU. A video filmed sideways is turned upright first.

## How the steps work

1. Frames: every 5th frame is placed on the stitched picture with image features (SIFT and a homography). A frame
   that does not place (fewer than 40 agreeing matches, or a map that is not upright) is skipped.
2. Device detection: on every placed frame and on the rack picture, the device detector (DEIMv2, one class, boxes
   from 0.5 up) draws a box around every device, and the typed finder (YOLO, confidence 0.2 up) names its kind. Every
   box is carried onto the rack picture through its frame's homography, as a four-cornered outline.
3. Devices: outlines on the rack that overlap vertically by at least half are one device; its outline is the
   weighted mean (frames that placed better and boxes that scored higher weigh more). A device needs two frames, or
   the rack picture and one frame, or a rack-picture box scoring 0.7 or more. The kind is the typed finder's sighting
   with the most confidence over that device. In the app the join across frames also uses the unit ladder; here it is
   this plain overlap rule only, so a device count can differ from the app's by one or two where boxes touch.
4. Top 5 frames: the app's frame locator, unchanged. Every frame of the video is matched to the rack picture around
   the device (SIFT on the frame shrunk to 960 px, ratio test 0.75, MAGSAC homography), gated (at least 30 agreeing
   matches, the whole device at least 20 px inside the frame, its size 0.5 to 2 times its size on the rack) and scored
   by the sharpness of the device itself (edge crispness, not brightness). The five best are kept, never two closer
   than 10 frames, and refined at full resolution. A device the locator finds in no frame keeps the best of the placed
   frames instead, ranked by the device's sharpness, how central it sits and how well the frame placed.

## What to check

1. Is every device in the rack outlined once, and nothing outlined that is not a device (a blank panel, a cable
   bundle, the floor)?
2. Does each outline cover the whole device, no more?
3. Is the kind right where one is given?
4. Are the five frames of a device really the clearest views of it (sharp, the whole device in view, not cut by the
   frame edge), and are they five different moments of the video?
5. Where the locator found no frame ("the best placed frames are used" in the terminal), is the device visible in the
   video at all?

Please send back the video, the `_devices.jpg`, the `_top5.jpg` of any device that is wrong, and one line per wrong
device: what it is and what the tool said.

## Videos that work

One slow pass from the top of the rack to the bottom, 20 to 60 seconds, the phone upright, both rails in view the whole
time, the whole width of the rack in the picture, steady distance, good light, 1080p or better.
