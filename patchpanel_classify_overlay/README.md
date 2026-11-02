# Patch panel classification + port overlay

Two things in one folder. Point it at a folder of patch panel crops and
it tells you what each panel is and draws a box on every port it found.

    python run_classify_overlay.py --images crops/

## What is in this folder

    run_classify_overlay.py   the one command - runs all three steps
    classify_4class.py        step 1, the first guess
    verify_4class.py          step 2, the check that decides
    port_overlay.py           step 3, the drawing (works standalone too)
    ppc/                      the modules those three import
    gallery/                  397 labelled panel images, 4 folders
    gallery/manifest.csv      their original filenames
    port_model.pt             the port detector, 52 MB
    out/                      slot gallery + keystone comparison files

## Install

    pip install -r requirements.txt

First run downloads the DINOv2 weights (~330 MB) through timm, and spends
about 11 minutes embedding the gallery on CPU. Both are one-time: the
embeddings are cached in out/cache and every later run is under a minute.

## How it works

Three steps. The first two are the classification, the third is the
overlay.

**Step 1 - classify.** Each crop is cut into port-scale tiles and each
tile is embedded with DINOv2. Those vectors are compared against the same
embeddings for the 397 gallery images, and the panel takes the class of
whatever it matches best. Nothing is trained - it is a lookup against
known panels, so adding a panel to gallery/ is how you teach it something
new.

This step judges the panel by its overall appearance, which is also its
weakness: a densely cabled RJ-45 panel and a fiber panel look alike at
that level, because the few pixels that actually tell them apart are
averaged away. On its own this step is right about 72% of the time.

**Step 2 - verify.** Now look at the ports instead of the panel. The YOLO
port model finds every individual port and names it (RJ45, LC, SC, empty,
and so on), and those get voted, confidence-weighted. Three checks run
over that:

  - **opening shape** - round with a metal body and no contact row means
    coax, and it stops there. LC/SC/FC means fiber. A rectangle with gold
    contacts means RJ-45, and it continues.
  - **keystone frame** - a keystone panel is defined by its frame, not
    its connector, so this measures the frame instead: snap-in cutouts,
    bezel edges, module seams.
  - **hole vs socket** - an `empty` port means "nothing plugged in here",
    which at panel resolution looks exactly like a bare keystone cutout.
    Each empty slot is re-read at twice the resolution and compared with
    labelled hole and socket crops before it is allowed to count as a
    cutout. Without this, one unplugged cable turns a fixed panel into a
    keystone one.

The outcome confirms, corrects or leaves alone the step 1 guess, and that
is `final_class`. Measured over 309 hand-labelled crops: 71.5% after step
1, **95.8% after step 2** (macro F1 93.2%).

**Step 3 - overlay.** A box on every port, green where the model named a
connector and amber where it read the slot as unoccupied. This does not
run the detector again - step 2 already stored every box, so the picture
shows exactly the ports the classification was based on rather than a
second opinion from a second pass.

Steps 1 and 2 run as separate processes on purpose: step 1 holds DINOv2
and step 2 holds YOLO, and keeping both out of one process is the
difference between fitting in memory and not.

## What you get

    out/results<name>.csv        one row per panel: class, port count,
                                 port types, why it decided that
    out/overlays<name>/          the overlay images
    out/verified_4class<name>.csv  every column, including the raw boxes

## Options

    --images crops/     the folder to classify
    --name run2         tag the run, so two datasets do not overwrite
                        each other
    --labels            write each port's class on its box
    --numbers           number the ports left to right
    --skip-classify     reuse an existing predictions CSV - step 1 is the
                        slow one, so skip it when only step 2 changed
    --skip-verify       reuse an existing verified CSV and only redraw

Each step also runs on its own if you need the parts separately:

    python classify_4class.py --test crops/ --name run2
    python verify_4class.py --pred out/predictions_4class_run2.csv \
                            --out out/verified_4class_run2.csv
    python port_overlay.py crops/ --out overlays/

## Using it from your own code

The overlay is a plain object, and it is the part most likely to be
embedded somewhere:

    from port_overlay import PortOverlay
    ov = PortOverlay()                 # loads the model once, ~2 s
    r = ov.render("panel.jpg")
    r["ports"]                         # [{box, cls, conf}, ...]
    r["counts"]                        # {"RJ45": 20, "empty": 4}
    cv2.imwrite("overlay.jpg", r["image"])

For the class as well, run the pipeline over a folder and read
`out/results.csv`; classification needs both models and the gallery, so
it is a folder-level job rather than a single call.

## Known limits, stated plainly

  - **The port detector has no coax class.** It labels coaxial bulkheads
    as RJ45. That is why coax is settled by the opening-shape and ring
    checks before the port vote is allowed to speak, and why a port vote
    of RJ45 over a stage-1 coax guess is treated as a known failure mode
    rather than evidence.
  - **Keystone is never decided by the ports.** It is a property of the
    frame, so the port detector has nothing to say about it and step 2
    neither introduces nor overturns a keystone call.
  - **A port hidden behind a cable gets no box.** Nothing here knows it
    is there. Filling those in needs a reference panel fitted with a
    homography, which is a different piece of machinery and is not in
    this package.
  - **Tier 1 of the keystone rule is off** (`EMPTY_DECIDES = False` in
    ppc/keystone.py), as requested during the build. It is the strongest
    single signal available, so the cost is real and measured: keystone
    recall falls to around 13-17% with it off.
