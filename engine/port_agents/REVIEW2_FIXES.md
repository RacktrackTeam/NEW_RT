# Fixes for the Port Agents Review (26 October 2026)

The review tested the 24 October package on 42 crops. Every item was changed in all three agents
where it applies, keeping one workflow: identify -> detect ports -> template -> fit -> snap -> result,
with auto mode as the fallback. Items 3 and 8 were left for later on request.

| # | Review finding | Change |
|---|---|---|
| 1 | Auto mode reports every port connected | The port model's own 'empty' class almost never fires (no 'empty' box on 92 of 98 test photos, even on product renders), so every port defaulted to connected. Cable status now comes from RackTrack's port status model, shipped as `port_status.pt` in each agent folder (classes Connected_port / Empty_port). A port it does not see is `unknown`, never assumed connected. All three agents, template and auto mode |
| 2 | Auto mode counts differ per view, doubled boxes | Switch agent: boxes sharing more than half of the smaller one are one port; columns less than 0.6 of a port apart are merged (the doubled 33/34, 49/50). The router and appliance agents use no grid and were unaffected |
| 3 | Template recall | Not changed in this round (on request) |
| 4 | A vendor named with no vendor text on the crop | A vendor is kept only when its name or a product-line word (Catalyst, UniFi, FortiGate ...) is read on the photo, or its model is printed there as is. Otherwise no vendor, and `identify_confidence.vendor` at most 0.5. All three agents |
| 5 | A given make / model is echoed back | A make / model passed in is a hint: confirmed by the template fit or by the panel text; otherwise `identified_by: given`, and `vendor_doubt` names the brand the panel shows. All three agents |
| 6 | The switch agent writes no identify_confidence | Written, same fields as the other agents (`vendor`, `model`, `vendor_doubt`) |
| 7 | Two spellings of one model make two templates | A reading that is a catalogue model without its prefix (SG2428P) becomes the catalogue name (TL-SG2428P) before the template is named |
| 8 | Switch catalogue is not the team workbook | Not changed in this round (the workbook was not available). Since 28 Oct the switch catalogue is an Excel file (`switch_catalog/Switch_Vendors_and_Models.xlsx`, 117 vendors / 6,955 models matched - see `CHANGES_SINCE_2026-10-27.md`), but the team workbook is still not merged |
| 9 | Unreadable photo: no result.json from the switch agent | The switch agent writes result.json with `status: error` for a missing, unreadable or tiny photo and for any failure, like the other agents. It now also writes `status`, `mapped`, `error` and a status on every port itself |

Also changed - single side ports (switch agent): a jack standing alone beside the port block (an MGT
jack, a console, a single SFP uplink) used to be dropped by the grid step unless the port model named
it console or management. It is now kept, in templates and in auto mode, and named from the label printed
next to it (CONSOLE, MGT / MGMT, USB); without a readable label a lone RJ45 is 'Mgmt' and a lone SFP a
numbered port. FS S3900-24T4S now maps 30 ports with its MGT jack. Of the 57 saved switch templates one
gained a port (MikroTik CRS328-24P-4S: its console). The router and appliance agents already kept these jacks.

Tested on 10 photos tied to these fixes (FS S3900, MikroTik CRS328, Dell 2824, Ruckus ICX 7150, Ubiquiti
US-24, TP-Link SG3428MP, Cisco Catalyst 4948, Ubiquiti UDM-Pro, Palo Alto PA-200, Peplink Balance 305)
and on the error cases (missing file, text file named .jpg, 20 x 10 px image, wrong make given).

Known limits: a side jack the port model does not detect at all on the template image (Ruckus ICX 7150
management jack) is still missing; that needs detector training. The count
fixes of #2 were checked on our straight-on test photos, where counts did not change; the cluttered
crops of the review were not available.

Also added - ports the port model misses (`port_recovery.py`, all three agents): the model sometimes
does not find a jack at all (dark jacks on a dark panel, jacks at an angle). Three recoveries:
a brightened second pass when fewer than 4 network ports are found; a search for look-alikes of a
confidently found port along its own row; and a weak detection (below 0.35) accepted when it also looks
like a confident port. Every recovered box must sit in a row of 2+ found ports, directly beside a found
port at the row's port spacing, have the found port's size and look, and not overlap any other box -
so no box lands on an LED, label, logo, fan grille or LCD, and no port is boxed twice. Barracuda Load
Balancer 540 (0 of 4 ports found before) and Quantum QN-S-50 (2 of 5) are now mapped in full instead of
asking for the back side; 16 other photos (LED-only fronts, fan grille, logo, LCD, heavy cabling,
templates) got no added box. PORT_RECOVERY=0 turns it off. A face where too few jacks are found to
compare with (Stormshield SN200) still needs the detector to be trained.

Also changed - port numbering that differs from the panel (switch agent): when the whole-panel read
finds too few printed port numbers, the number above / below each jack is now read one jack at a time
(the strip cut out and enlarged 8 times), and the numbering scheme is checked on the photo's own
jacks. It needs 3+ matching numbers and a clear lead, so a stray digit never renumbers a template.
TP-Link TL-SG3424 and MikroTik CSS610 now get the panel's numbering (2, 4, 6 on top) instead of the
default. A numbering read this way is saved with a learned template (`numbering_read`), so a later
photo whose labels are hidden by cables keeps it; three saved templates were set from their panels
(Zyxel GS1100-16, TP-Link TL-SG3424, MikroTik CSS610). The default (1 top-left) is still used when no
numbers can be read anywhere.
