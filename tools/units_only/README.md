# RackTrack units only: identify the rack units from a video or a picture

This is the unit step of RackTrack on its own, for testing on a laptop. Give it a video of a rack (one slow sweep of the
front, top to bottom) and it stitches the video into one picture of the whole rack, as the app does, then finds every
unit row, numbered from the bottom, and draws them on the picture and back on the video. A picture of a rack front works
too. It is the same code and the same model the app runs; nothing else of the app is here (no devices, no ports).

## Install (once)

Python 3.10 or newer. In this folder:

    python -m venv .venv
    source .venv/bin/activate          (Windows: .venv\Scripts\activate)
    pip install -r requirements.txt

The first install pulls PyTorch through ultralytics (about 2 GB). The model file is in weights/ already.

## Run

A video:

    python identify_units.py rack.mp4 --out out

Pictures, or a whole folder of videos and pictures:

    python identify_units.py samples --out out
    python identify_units.py photo1.jpg photo2.jpg --out out

For a video you get, in out/:

- `<name>_rack.png`: the stitched picture of the whole rack.
- `<name>_units.jpg`: that picture with a white line at every unit boundary and the unit number on the left. A black
  number is a row the model saw; an amber number is a row the ladder rules added (a row missed between two others, or a
  row at the top or bottom where the rack frame carries on).
- `<name>_units.mp4`: the video with the unit lines and numbers drawn on every 5th frame (`--stride 5`); each frame
  that could be placed on the rack picture gets them, a frame that could not is left as it is. `--no-video` skips it.
- `<name>_units.json`: the rows top to bottom with their box in picture pixels, whether the model saw them, the
  model's confidence, the rack box and the cross rails found.

For a picture you get the jpg and the json.

The terminal says, per input: how many units, how many the model saw, how many the rules added, the top and bottom
unit, and for a video the stitcher's filming notes (resolution, the bottom cut off, and so on). A 30 second video takes
about a minute on a laptop, most of it the stitching.

Options: `--conf 0.25` is the model's confidence cut (lower finds more rows, with more mistakes). `--device cpu` forces
the CPU when a GPU gives trouble. A video filmed sideways is turned upright first, by itself.

## What to check

1. Is the total number of units right?
2. Is every unit line on a real boundary (the gap between two devices, or between a device and a blank)?
3. Are there units drawn outside the rack: on the plinth, the floor, or the roof?
4. Is U1 the lowest real unit?
5. On the video: do the lines stay on the same rows while the camera moves?

Please send back the video or picture, the `_units.jpg`, and one line: the right number of units and what is wrong,
if anything.

## The rules in plain words

- The model finds unit rows, the rack frame and the horizontal cross rails on the stitched picture.
- Two detections on one row become one. A box several rows tall is dropped.
- Rows are made contiguous: where two overlap, the boundary is the middle of the overlap.
- A gap of at least half a row between two rows is a row the model missed; it is filled in.
- At the top and the bottom, rows are added only while the rack frame carries on, at most two at each end, and never
  past a cross rail.
- Units are numbered from the bottom: U1 is the lowest row.

## Videos that work

One slow pass from the top of the rack to the bottom, 20 to 60 seconds, the phone upright, both rails in view the whole
time, the whole width of the rack in the picture, steady distance, good light, 1080p or better. The app's own reading of
a video also votes the units across the frames, so its count can differ by a row from this tool's.
