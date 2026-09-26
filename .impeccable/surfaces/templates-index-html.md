---
version: 1
slug: "templates-index-html"
primary_target: "templates/index.html"
related_targets: ["app.py"]
---

# Surface brief: Odin's Kin dashboard + desktop tracker

Scope: `templates/index.html` (web dashboard, Operate) and `app.py` (Tkinter desktop tracker, Operate). They are one visual world.
Audience/job: the machine's owner, reflecting on where a work block's time went (web) and running the start/stop ritual (desktop). The owner also wants their YouTube history, with links.
Content: real sessions and focus events from the local DB, real app icons and names read from each executable, and YouTube video titles with links (the only window text kept).
Constraints: local-first, so no third-party requests at runtime (no YouTube thumbnails). Window titles are redacted before storage and again on output; exe paths never reach the page. Tk has no anti-aliasing, so shapes are rendered with Pillow. Keep CPU low while recording.
Unresolved: idle detection, excluding the tracker's own window, and a remote ingest API.

## Direction contract

THESIS: The session as an iOS Screen Time report whose live pulse is the Instagram story ring. It refuses the incumbent neon-on-black hacker terminal and the generic SaaS card grid.

OWN-WORLD: iOS grouped materials: #F2F2F7 ground, white inset groups with 12px radius, hairline separators; true black with #1C1C1E in dark. Facts are iOS list rows (label left, value right), never stat tiles. One face, Inter (self-hosted; the open SF-class grotesk, since SF can't be hosted), with tabular numerals on a strict ladder: 64 / 44 / 34 / 22 / 17 / 15 / 13 / 11. Text is ink only. Color exists only as the Instagram spectrum (#FEDA75 → #FA7E1E → #D62976 → #962FBF → #4F5BD5). Rings speak three ways, like Instagram and Apple Watch together: a gradient ring means live or new (recording, or an unopened session); a gray ring means seen; a ring segmented in per-app hues is data (a day's or session's app split), never decoration.

STORY: Within a glance the user knows how long they worked, which apps took the time, and how the week trends. They trust the numbers, open any session to see its timeline and events, and find the YouTube videos they watched with links back to them.

FIRST VIEWPORT: Web (Day view, the default): a large "Today" title with steppers, then a tray of seven story rings for the week (each ring filled in proportion to that day's total and split by app; the selected day is an ink disc), then the day's total in huge numerals with its delta against the 7-day average and the hourly stacked chart. Below: facts rows, "Most used" ranked rows with real icons and bars, "YouTube" rows with links, and sessions (ring avatar plus an app-icon stack). Opening a session pushes its detail (donut, timeline, facts, apps, YouTube, events) into the right column, or replaces the page on mobile. Desktop: a 232px ring with timer numerals centered, ranked app rows beneath (the live app wears the gradient ring; a YouTube video shows as a third line), and a full-width pill action at the bottom.

FORM: Screen Time Ring. #1 on my ordered list (user-selected pick); seed key e5fd6bea. Signature interaction: the desktop ring turns slowly as a gradient while recording, then sweeps into the session's app split on stop. On the web, tapping a day in the ring tray or a day's bar re-scopes the whole report to that day. Motion: 180–240ms exponential ease-out on state only, and bars grow once from their visible default; reduced motion disables both.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
