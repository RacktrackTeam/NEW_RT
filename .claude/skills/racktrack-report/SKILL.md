---
name: racktrack-report
description: The one standard for every RackTrack report, portal, review page, guide or web-app page (HTML pages, artifacts, localhost UI). Use it before building or changing any page that shows results, numbers or pictures.
---

# RackTrack report standard

Apply every rule below to every page. When a rule here and a request differ, the user's latest words win; then update this file.

## Get it right the first time (user, 2026-10-22: "I shouldn't have to prompt again and again")
Build every results tab to this blueprint (the approved Crop resolution tab, device report v58) before showing it:
1. **Tile row**: 3 or 4 tiles, one number each, the numbers the reader cares about (e.g. Box size · Score today · Bigger box). No number that needs defending or that only alarms (the user removed "77 % of crops lose pixels").
2. **One picture card** showing how the thing works, with real images from the data, left to right with an arrow (e.g. photo crop → 512 × 128 box → square box for comparison). Pictures sized to the card; never full-screen, never tiny.
3. **Two cards side by side**, each a row list: label (bold) + small grey detail · thin bar to scale · value right-aligned. Calm: one colour per card. The card title states the finding ("Making crops smaller does not cause mistakes"); the subtitle says what is counted and what would disprove it ("if making them smaller caused mistakes, the top row would be the worst"); one short note under it.
4. **One small card** for what is pending or what comes next, with a status chip ("not tested yet").
5. **Closed dropdown "The numbers"** with the tables. Nothing else in dropdowns unless it is long.

Wording: plain words a non-expert uses: "made smaller / made bigger", "box", "trusted labels"; never jargon on the page ("shrunk", "enlarged", "canvas", "letterbox", "OOD", "logits"). Titles are findings, not questions. Say each fact once.

Rejected by the user (do not repeat): question-and-answer layout; sub-tabs inside tabs; the same fact in two places; every number as a bar chart; many colours; oversized pictures or big empty half-cards; long paragraphs; jargon; a busy multi-series chart where a short row list works; numbers shown without saying what they prove.

Self-check before publishing (screenshot every tab you touched):
- Can a non-expert say what each card proves in five seconds?
- Does every card have a one-line finding?
- Is any fact shown twice, any word jargon, any half-card empty?
- Does the tab follow the five blocks above?
Fix it before showing it.

## Layout
- Page width 90 % of the viewport (100 % below 800 px), 16 px side gutter, no sideways page scroll.
- App shell: left sidebar (brand "RackTrack", grouped nav: Analyse = Rack Snapshot · Rack Sweep / Reference = Models & accuracy, each with a short caption); each page opens with a deep-blue hero (eyebrow, h1, lead, numbered step chips, the action in a white panel on the right), main column with a page header: small crumb, h1, one-line lead, actions on the right.
- Reports with many sections: a sticky TOP header with the tabs in one row, grouped with small group labels and dividers (e.g. Overview · Data · Method · Results), each group a small uppercase label above a segmented control (grey rounded track, active tab a raised white pill); headline score chips on the right of the brand row; it becomes a scrolling tab row on phones. No left tab rail (user, 2026-10-21: "I need top header for tabs"). The summary tab opens first.
- Cards with depth: white cards with a soft shadow on a pale grey page. Never hairline-only white boxes.
- Images: fixed-height frames (object-fit: contain), small thumbnails, click opens full size. Images never take the whole screen.
- Tables full width, no inner scroll box unless they really are wider than the page.
- Detail goes in closed dropdowns (`<details>`), e.g. "Where the rest goes" under each accuracy.
- Light and dark theme through colour tokens; body has its own background.

- Step-by-step reports for an audience (e.g. Device Label Review): attractive and readable at a glance, not too plain and not too heavy (user, 2026-10-21; a hero + donut + coloured cards was 'clumsy, too appealing, not professional', plain tables only was too plain). Pattern: grouped segmented tabs, one tab per step, Summary first with KPI tiles and a clickable step flow (thin to-scale bar per step); each step tab = KPI tiles + one or two cards; tables with a to-scale bar column, numbers right-aligned, a total row; one stacked bar with a legend instead of charts; two colours (blue = ours, purple = network team); long tables in closed dropdowns.

## Content
- Plain, simple English. Short intro, then the visual.
- Explain methods step by step: numbered flow boxes ending in the decision, a worked example, then Good at / Weak at with concrete cases. Rate things in words (Good / Fair / Poor), never dot ratings or abstract scorecards (user, 2026-10-21).
- Prefer the plainest chart that answers the question: one stacked bar with a big headline number, or shapes drawn to scale with their numbers. No log scales, box-and-whisker ranges or percentile bands on report pages (user, 2026-10-21: "too confusing, not understandable"). Keep type compact (13.5 px body, 22 px titles). Firm and simple: no repeated facts, how-to before don'ts.
- Each tab holds only what is needed: a few KPI tiles, then 3 to 6 cards. A fact shown in one card is not repeated in another (a chart that repeats a table goes). Long explanations go in closed dropdowns (user, 2026-10-21: "too much content, too long; detailed and simple, only necessary info").
- At a glance: flowcharts, bars drawn to scale (say the scale), stacked bars, drawn rule trees, one-measure cards (one number, one label).
- One definition per number: the same number reads the same everywhere; every number is read from the eval/result files, never typed in.
- Say what each step uses: model name, approach, why, accuracy, where the rest goes.
- Name steps by their names (Unit detection, Unit occupancy, Device detection, Device classification). No "Step 1/2/3" labels.
- Units are not devices: never use unit or occupancy results as evidence for a device classifier.

## Words never on a page
- "NetBox" - say "ground truth". Ground truth is never shown in a UI at all; it lives only in result files.
- Device types on video pages: labels are `D<i> · <n>U` only, no class names in text, tables or pictures.
- "teammate", "packet", resizing / working-copy sizes, internal file chatter.

## Video pages
- How it works = a six-box flow at a glance, then a "How we are doing it" table (stage · how · what we use · on this rack, settings read from src/rack_video.py), then the stages in pictures.
- Rack image alone on the LEFT, frames on the RIGHT.
- Show the best frame per device; polygons, never bounding boxes.

## Result pictures
- Clean: the final ladder only (white lines, U-number strip). No green/red/dotted debug overlays.

## Current facts to reuse (check the files before quoting)
- Occupancy ensemble = five members (YOLOv8m, YOLO11s, DINOv2, CLIP, ResNet50); Classical left out 2026-10-19.
  Numbers: `webapp/static/ensemble_portal/data.json` (testsets.overall), portal `scripts/occupancy/build_occupancy_portal.py`.
- Units: `results/units/eval*/summary.json`. Devices: `results/devices/grade_*.json`. Classifier: `models/device_cls/convnext_tiny/`.

## Before publishing
- Save screenshots only to the session scratchpad (absolute paths), never into the repo.
- Screenshot at 1500 px and 400 px; no JS errors; no sideways scroll.
- Republish to the same artifact link; never create a second copy of an existing report.

## Visual variety (user, 2026-10-22)
- Do not make every number a bar chart. Pick the visual that fits the message: a 10×10 grid ("each square is one device in 100") for right / wrong / unknown shares; real pictures for anything about images (the same crop at different sharpness or sizes); a meter card for a setting.
- Calm colours: shades of one hue for categories, red only for wrong, grey for unknown.
- Each section: a plain title (not a question), a bold one-line finding, one visual as proof; detail tables in closed dropdowns. No question-and-answer pages.

## Headline numbers (user, 2026-10-22: "are we still at 92? I thought Other improved, I see nothing")
- When a feature changes the result (e.g. the Other answer), the header chips show both figures on every tab: "Accuracy 92.5 %" and "With Other 96.5 % of answers right". Never leave an improvement visible on one tab only.
- Two cards side by side must be about the same height: trim the longer list (top 5, rest in The numbers) or add rows to the shorter one.

## Example pictures (user, 2026-10-22: "all your examples are worst images, use best images"; "why can't I open the images")
- Never pick examples by a number alone (most sure, widest, random). Pick the clearest: sharp, straight-on (fills its polygon), well lit, big enough, the usual shape for its class, one per photo. In the device report this is `quality()` + `clearest()` in build_crop_atlas.py; reuse them.
- Never show a blurry picture (user, 2026-10-22): every example must pass a sharpness bar (device report: `sharpness()` ≥ SHARP_MIN = 450, Laplacian variance at 128 px height). Show fewer examples rather than a soft one. Exception (user, 2026-10-22): an Edge cases page shows blurry crops, marked "blurry", because the blur is part of why the model fails.
- When a mistake is shown, show the mistaken device next to 3 similar devices of the type it was taken for (nearest by the model's features), not one random example.
- Every picture on a page opens full size on click, with its caption, and arrows go through the pictures of the same card. Store pictures large enough to look sharp when opened (about 700 px wide) but keep the page under about 8 MB.
