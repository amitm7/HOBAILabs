# HOB IP / property watermarks

Drop one **transparent PNG per IP** in this folder. Each reel is tagged with one IP
(HOB Originals, The HOB Show, …) and its PNG is composited as a **full-frame layer
over the whole video**, in both story and brand modes. This is HOB's own property
branding — it is **separate** from the brand-collab advertiser logo used in Brand mode.

## How it maps
`config/watermarks.json` maps an IP name → a PNG filename in this folder:

```json
{ "ips": { "HOB Originals": "hob_originals.png", "The HOB Show": "the_hob_show.png" } }
```

- To **add** an IP: add a line to `config/watermarks.json` + drop the matching PNG here.
- To **rename** an IP: edit the key in the JSON (the dropdown label follows it).
- An IP with **no PNG present** still shows in the dropdown but applies no overlay
  (graceful no-op) until you add the file — nothing breaks.

## PNG requirements
- **Format:** PNG with an alpha channel (transparency). Transparent everywhere except
  the mark; the video shows through the transparent areas.
- **Size:** author at the reel resolution — **1080×1920** for portrait reels
  (1920×1080 for landscape). The pass scales the PNG to the output frame, so an exact
  match avoids any stretching.
- **Duration:** the layer stays for the entire video (no time gate).

Expected filenames for the IPs currently in `config/watermarks.json`:
`hob_originals.png`, `the_hob_show.png`, `unfiltered_hob.png`, `the_unplanned_hob.png`.
