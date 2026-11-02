# RackTrack screen standard

One page for every screen in `mobile/`. The rules are applied in `mobile/standard.css`, loaded after every other style sheet. It changes where things sit and how they look, never what a screen contains. Plain hyphen only, light theme only, accent #1f2a3a.

## Scale and width
- One spacing scale: 8, 16, 24, 32 px. Nothing else is used for gaps, padding or margins between blocks.
- Gap between blocks on a screen: 24 px (16 px inside a block). Gap between the page header and the first block: 24 px.
- One gutter: 32 px on a laptop, 16 px on a phone. Content spans the full width between the gutters. No centered narrow column.

## Page header
- Title on the left (24 px, weight 600, #111). The primary action of the screen on the right of the same line. On a phone the action drops under the title, still at the right.
- The one-line lead (14 px, muted) sits under the title. The tab row (result and tool screens) sits under the header, the rack name on the left of the header and the tabs right aligned on a laptop, the tabs at the top and scrolling sideways on a phone.
- The rack name is always in the header, left. Counts (devices, ports, differences) are always a muted 14 px line directly under the title or under the block heading they count, never in the header.
- Back links are always top left, under the header, as a text link (14 px, weight 600, underlined).

## Buttons (all 40 px high, 8 px radius, 14 px, weight 600)
- Primary: filled #1f2a3a, white text. Secondary: white with a 1 px outline. Text link: no box, underlined on hover.
- The primary button is always at the right of its block. A destructive action is a secondary button with red text.
- A status or error line sits directly under the action that caused it: spinner and text in #1f2a3a while it runs, one red sentence (#b3261e) on a failure.

## Cards, pills, tables
- Card: one 1 px border #e3e7ec, one 8 px radius, 16 px padding, no shadow.
- Pill: 24 px high, 12 px text, weight 600, 999 px radius, 10 px side padding.
- Table: header row 12 px, weight 600, muted, on #f6f7f9; cells 14 px; 12 px by 16 px padding; one 1 px rule between rows.
