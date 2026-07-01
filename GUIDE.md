# HOBAILabs — Complete User Guide

**Platform:** AI-powered storytelling video pipeline (Instagram Reels / YouTube Shorts)  
**Flow:** Script → Scene Intelligence → AI Images → Animated Clips → Captions → Assembled Reel  

---

## Table of Contents

0. [What This App Does (Plain English)](#0-what-this-app-does-plain-english)
1. [Starting the App](#1-starting-the-app)
2. [Recharging Credits](#2-recharging-credits)
3. [Script Format — Complete Reference](#3-script-format--complete-reference)
4. [Web UI — Step by Step](#4-web-ui--step-by-step)
5. [Frame Assignment — All Options](#5-frame-assignment--all-options)
5b. [Smart Matching & Style Exemplars](#5b-smart-matching--style-exemplars)
6. [Director Notes — How to Write Them](#6-director-notes--how-to-write-them)
7. [Camera Angles & Motion](#7-camera-angles--motion)
8. [Lip Sync — Make the Subject Speak](#8-lip-sync--make-the-subject-speak)
9. [Image Edits — Change What's in a Photo](#9-image-edits--change-whats-in-a-photo)
10. [Style & Quality Settings](#10-style--quality-settings)
11. [Models, Providers & Routing](#11-models-providers--routing)
12. [Music Options](#12-music-options)
13. [CLI Reference — All Flags](#13-cli-reference--all-flags)
14. [Cost Management](#14-cost-management)
15. [Cache System](#15-cache-system)
16. [Best Practices for Storytelling Scripts](#16-best-practices-for-storytelling-scripts)
17. [Complete Worked Example — Lalita's Story](#17-complete-worked-example--lalitas-story)

---

## 0. What This App Does (Plain English)

You give the app either a **frame-by-frame script** or a **raw story/notes/transcript**, plus an optional **folder of photos/videos**. It returns a **finished vertical video** ready to post on Instagram Reels or YouTube Shorts — with captions on screen, background music, and cinematic camera movement on each photo.

**Think of it like an automated video editor with a film director's brain.** For each line of your story, it decides what should be on screen, how the camera should move, and how it should feel emotionally — then assembles everything into one reel.

Here is the whole journey, in order:

```
1. YOU WRITE       →  a story in "frames" (one beat per frame) + pick a photo/video folder
2. THE APP READS   →  splits your story into frames, matches each to a photo or video
3. IT DIRECTS      →  for each frame, an AI "director" decides the emotion, lighting, camera move
4. IT CREATES      →  missing visuals are AI-generated (a portrait, or symbolic objects)
5. IT ANIMATES     →  still photos are turned into moving video (camera pans, zooms, orbits)
6. IT CAN SPEAK    →  optionally, a photo can become a talking face narrating the line (lip sync)
7. IT CAPTIONS     →  your text appears on screen, timed to each frame
8. IT SCORES       →  background music is added and ducked under any spoken audio
9. YOU DOWNLOAD    →  one polished 9:16 MP4
```

**Why "frames"?** A frame is one beat of the story — usually one or two sentences. Each frame becomes one shot in the final video. A 10-frame story becomes a 10-shot reel.

**You control everything per frame** using simple bracket tags in the script (like `[note: show pride]` or `[camera: 360 orbit]`) OR by clicking in the web UI. You never touch code.

The rest of this guide explains each control. **Section 17 is a complete worked example** using Lalita's real story — start there if you learn best by example.

---

## 1. Starting the App

```bash
# Start web server
~/.pyenv/versions/3.12.3/bin/python3.12 web_app.py

# Open in browser
http://localhost:7860
```

To stop: `kill $(lsof -ti:7860)`

---

## 2. Recharging Credits

| Service | Used For | Recharge / Sign-up URL |
|---|---|---|
| **fal.ai** | Aggregator: Flux, Seedream, Nano Banana (images) + Seedance, Veo 3, Hailuo (video) — one key, many models | fal.ai → Avatar → Billing |
| **Kling AI** | Image-to-video animation (native, default for most shots) | klingai.com → Account → Recharge |
| **Higgsfield** | Cinematic video + camera-motion presets | cloud.higgsfield.ai → Billing |
| **OpenAI** | Scene intelligence (GPT-4.1) + symbolic images + image edits | platform.openai.com → Billing |
| **ElevenLabs** | Voice-over + lip-sync audio | elevenlabs.io → Billing |
| **Hedra** | Lip sync from a photo (talking portrait) — *optional* | hedra.com → Creator plan → Profile → API |
| **SyncLabs** | Lip sync on a video (mouth re-sync) — *optional* | app.sync.so → API Keys |
| **Suno** | AI music generation | sunoapi.org (third-party wrapper) |

**Lip-sync services (Hedra / SyncLabs) are optional.** If their keys are not set, lip-sync frames silently fall back to a normal animated photo (Ken Burns). Nothing breaks — you just don't get a talking face on those frames.

**Multiple models, auto-chosen per shot.** You no longer pick one provider for the whole video. The pipeline routes each shot to the best model for its type, cost-tier aware (cheap models in Dev, premium models in Production). You can override globally or per frame. See [§11 Models, Providers & Routing](#11-models-providers--routing). Models + routing policy live in `config/models.json`.

**Approximate costs per 10-frame story (Auto routing):**
- Dev / draft tier (Kling Standard video + Seedream images): ~$0.85
- Production / premium tier (Kling Pro / Seedance / Hailuo + Nano Banana): ~$1.00–2.00 depending on which premium video models the router picks
- Higgsfield (when chosen): ~$1.00 · GPT-4.1 scene design: ~$0.01 (cached after first run)

> ⚠ **fal.ai model endpoint slugs and prices in `config/models.json` / `config/pricing.json` are marked `VERIFY`.** Confirm each on fal.ai before a *Production* render — they shift monthly. Dev renders are safe: video uses Kling, and unverified fal image models fall back to gpt-image automatically.

All prices stored in `config/pricing.json`; model capabilities + routing in `config/models.json` — update those files when vendor rates/models change.

---

## 3. Script Format — Complete Reference

The app uses **Format B** — plain text with `Frame N` headers and optional bracket annotations.

### Minimal Script

```
Reels

Frame 1
From a farmer to a model…
This is the story of a desi girl with big dreams.

Frame 2
At 19, I got married into a traditional Rajasthani home.

Caption:
Your full Instagram caption goes here (NOT shown in video).
```

### Full Script with All Annotations

```
Reels

Frame 1
From a farmer to a model… This is the story of a desi girl.
[photo: IMG_1240.MOV]
[note: Opening hook — strong, proud face. Direct gaze into camera. NOT victimized.]
[duration: 6]

Frame 2
At 19, I got married into a traditional Rajasthani home.
[photo: 02_wedding.jpg]
[note: Traditional setting, ghunghat — but eyes show inner fire]

Frame 3
Being a kisan ki beti, I was riding tractors even after marriage.
[photo: ai_portrait]
[note: Strong, proud farmer woman. NOT poor — powerful. Golden hour light.]
[camera: crane up]

Frame 4
After COVID, I was diagnosed with rheumatoid arthritis.
[photo: ai_symbolic]
[note: No person. Medicine bottles, fallen hair, cold blue-grey light. Silence.]
[edit: add frost on the window and grey winter light]

Frame 7
Bedridden, I watched modelling videos dreaming of the ramp.
[photo: lalita_face.jpg]
[lipsync: yes]
[note: She speaks this line herself — hope kindling in her eyes]

Frame 8
One day, the girl who couldn't stand walked the ramp in heels.
[photo: IMG_3020.MOV]
[start: 4]
[note: Use the ramp-walk clip; skip the first 4s of intro]
[duration: 7]

Caption:
"From the farm to the ramp, I am that Desi Girl…"
```

### Annotation Reference

| Annotation | What it does | Example |
|---|---|---|
| `[photo: filename.jpg]` | Use a specific file from your assets folder | `[photo: lalita_ramp.jpg]` |
| `[photo: ai_portrait]` | Generate an AI portrait of the subject | `[photo: ai_portrait]` |
| `[photo: ai_symbolic]` | Generate AI objects/setting (no people) | `[photo: ai_symbolic]` |
| `[note: text]` | Director note — guides AI scene design and image generation | `[note: show grief not anger]` |
| `[camera: move]` | Set the camera movement directly (alias: `[motion:]`) | `[camera: 360 orbit]` |
| `[motion: move]` | Same as `[camera:]` — pick whichever word you prefer | `[motion: crane up]` |
| `[lipsync: yes]` | Make this frame a talking face speaking the caption | `[lipsync: yes]` |
| `[voice: voice_id]` | Use a specific ElevenLabs voice for this frame's lip sync | `[voice: 21m00Tcm4TlvDq8ikWAM]` |
| `[edit: prompt]` | Apply a natural-language edit to the image before animating | `[edit: add thunderstorm]` |
| `[model: id]` | Force a specific model for this frame (image + video) — overrides Auto routing | `[model: seedance]` |
| `[imgmodel: id]` | Force only the image model for this frame | `[imgmodel: nano_banana]` |
| `[vidmodel: id]` | Force only the video model for this frame | `[vidmodel: hailuo]` |
| `[duration: Xs]` | Override the auto-calculated frame duration | `[duration: 8]` |
| `[start: Xs]` | Skip the first X seconds of a raw video clip | `[start: 3]` |

**Rules:**
- Frame numbers don't need to be sequential — the parser reads order, not number
- `Caption:` section at the bottom is the Instagram posting text — it does NOT appear in the video
- Multiple annotations per frame are fine — put each on its own line
- `[note:]` guides the AI director (GPT). `[camera:]` sets the camera move directly. `[edit:]` changes the photo itself. `[lipsync:]` makes the face talk. `[model:]` forces a specific model (otherwise the router auto-picks per shot — see §10).
- Valid model ids are listed in `config/models.json` (e.g. `seedream`, `nano_banana`, `flux`, `gpt_image` for images; `kling_std`, `kling_pro`, `higgsfield`, `seedance`, `veo`, `hailuo` for video). A wrong-kind id is ignored and the router auto-picks instead.
- **`[camera:]` and `[lipsync:]` on the same frame:** lip sync wins. A talking face controls its own head motion, so the camera move is ignored on that frame. Choose one per frame: a cinematic camera move OR a talking face.

### Frame Duration Auto-Calculation

If you don't specify `[duration:]`, the app calculates it from word count:
- Minimum: 3.5 seconds
- Maximum: 9.0 seconds (or 5.0 in dev mode)
- Formula: `max(3.5, min(max_dur, word_count / 2.0))`

A 10-word caption → 5.0s. A 20-word caption → 9.0s.

---

## 3c. Brand / Ad Mode (`/brand`)

A separate page for **branded collaborations and advertisements**, sharing the
same engine (parse, scene intelligence, animation, captions, assembly) plus a
brand layer. Open it from the **🛍 Brand / Ad mode** link in the header. See
[docs/BRAND_PLAN.md](docs/BRAND_PLAN.md) for the full design.

**What's different from story mode:**
- **Brand brief** — fill the structured fields, or paste the brand's brief and
  click *Extract fields →* to pre-fill them. **You supply all ad copy and claims;
  the AI never writes or rephrases a marketing claim** (legal safety). Each beat's
  caption is the brand-supplied on-screen line.
- **Brand kit & assets** — upload the logo (required) and real product shots.
  **Product/logo shots are real-only — never AI-generated.** Mark each product
  shot with the **🛍 Product beat** toggle on its frame card.
- **Audio** — per-project toggles: announcer VO = *AI draft* (reads your script)
  or *brand-supplied audio* (final); background music = *AI* or *brand-supplied*.
  The announcer plays **over** the music (music ducks underneath).
- **Mandatories (hard-block)** — the ad will **not render** until it has: a logo,
  CTA text, the sponsored disclosure, and at least one product beat. Missing items
  are listed before any credits are spent.
- **Output** — an auto **CTA end-card** (logo + tagline + CTA), a burned-in
  *"Paid partnership with {brand}"* disclosure on the first ~3s, and an optional
  corner logo bug. Remember to also set Instagram's native *Paid partnership* label.

Bold animated/ kinetic on-screen text (price tags, badges, word-by-word callouts)
and product picture-in-picture are **Phase B2** — B1 uses bold static captions as
the on-screen text.

## 3e. Director Canvas (`/canvas`) — *work in progress*

A **stage-gated board** that builds your reel one approved step at a time —
**Script → Storyboard → Key Frames → Audio → Video → Final Cut**. Open it from the
**Canvas** link in the header.

**Why it's different from the wizard:**
- **You approve each stage before the next one runs.** Nothing paid happens until
  you click **Generate** on that stage's card — and the card shows the **exact cost
  first** (no surprise charges, no one-click wallet drain).
- **The Storyboard stage** lays out every shot as a card with its framing, a
  **motion arrow** (which way the camera moves), the emotion/beat, and a colour
  badge for the asset: 🟢 **Real** (your real photo, untouched), 🟡 **AI** (a
  generated symbol/object), 🔴 **AI face** (a generated likeness — needs consent
  before it can render). Real media always passes through untouched.
- **Edit any shot inline.** Each card lets you tweak the **caption**, the **camera
  move**, and (after Storyboard) the **actual image prompt** — editing a shot marks
  everything downstream stale so you never ship a stale render.
- **Command box** at the bottom: refine in plain English ("make it darker, add a
  rain shot") and the shot list re-plans — the same idea as a chat assistant, wired
  to our planner.
- **Use a whole folder of your real photos (recommended for real people).** Paste the
  path to your media folder and click **🖼 Use my photos (auto-match)** — it reads your
  images/videos and matches the right one to each story beat, used **untouched** (🟢 REAL).
  This is the right way to make a reel about a *real, named person*: their actual footage,
  not an AI lookalike. AI fills only the beats with no matching photo.
- **Enhance real footage (keeps it 100% real).** After matching your photos, click
  **✨ Enhance real footage** — it upscales, denoises, stabilizes and colour-grades your
  *actual* clips so phone footage reads cinematic. **Non-generative** — no AI faces, no
  fabrication; same person, same moment, just cleaner.
- **Re-create a scene cinematically (optional, ambient shots only).** On a shot that's a
  *place or object* — no person — hover and click **🎬** to generate a polished, cinematic
  version *inspired from* your real footage (the misty road, the kettle, the field). It's
  **opt-in per shot**, never automatic. For safety it **refuses any footage with a person
  in it** — your subjects stay real (use Enhance for those). Re-created scenes are clearly
  labelled **AI · from real**.
- **Storyboard view (✏️).** Click **✏️ Storyboard** to flip the board into **hand-drawn
  pencil-sketch panels** — one comic-board panel per shot (graphite sketch of the framing +
  blocking, with blue motion arrows for the camera move). It's a **planning view** — loose
  concept sketches, *not* the final render and *not* a likeness of anyone — for seeing the
  whole reel's shot flow at a glance. First click renders the panels (cheap, a few seconds
  each, in parallel); after that it's an instant toggle (they're cached). Click again to flip
  back to your photos/AI shots.
- **Upscale a shot (⬆, final-render quality).** Hover a shot with a still and click **⬆** to
  generatively upscale it. It's **routed for safety**: a **real** shot uses a *faithful*
  super-resolution that sharpens without inventing detail — your subject's face stays exactly
  theirs (🟢); an **AI** shot gets a *creative* detail pass. Photos that are already high-res
  (bigger than the reel needs) are skipped automatically, so you never pay to "upscale" a photo
  that's already sharp. Best used on your **final** picks, not every dev iteration.
- **Fidelity per shot + ⚡ auto-suggest.** Every real shot has a **Fidelity** dropdown:
  **Real (untouched)** · **Restore (clean)** · **Re-create (cinematic)** — the three rungs in
  one place, per shot. Click **⚡ Suggest fidelity** and it assesses each real shot's quality
  (resolution + sharpness) and recommends a rung — *keep it real* if it's already clean,
  *Restore* if it's soft or low-res, *Re-create* only for amateur **ambient** B-roll. The
  suggestion is a one-tap chip (⚡); you decide. **Shots that show a real person are never
  offered Re-create** — only Real or Restore, so a real face is never faked. Switching back to
  **Real** always returns the untouched original footage.
- **Add your own photos.** Use *"📎 Add a real photo of the person"* (applies to
  every people-shot) or the per-card **Replace** row, and choose:
  **Real** — your photo appears in the reel *untouched* (🟢, the thing competitors
  can't do); **AI face** — AI keeps the person's *likeness* across shots from a face you
  upload (🔴, labeled **AI · likeness**); **Scene** — a mood/look reference (🟡). A
  confirmation tells you exactly what changed and on how many shots; the badge and cost
  update live, and downstream stages reset so nothing stale ships.
- **Wrong photo on a beat? Fix it in two clicks.** Auto-match is good but not perfect on
  abstract lines. Each card's **Replace** row has **🖼 Pick** — it opens a thumbnail gallery
  of *your own folder*; click the right photo and that shot uses it (🟢 untouched). No
  re-matching the whole story. (The matcher itself now reads what each shot *depicts*, not
  just the caption, so there's less to correct.)
- **Other Replace options per shot:** **📎 Real** (upload a different real photo),
  **🎭 AI face** (AI conditioned on a face you upload), or **🤖 AI** (a fully AI-generated
  image — the escape hatch when a real photo is wrong and Enhance can't fix it). Swapped to
  AI? An **↩ Real** button brings your untouched photo back.
- **Character sheet (people kept consistent).** Click **👥 Characters** — the board reads
  the story and lists the people in it (e.g. *Narrator*, *Father*). For each you can set a
  **role, name, gender, age, skin tone, hair, clothing** *and* attach **one real reference
  photo + consent**. Those attributes + face carry to **every shot that person is in**, so
  they look like the same person across the whole reel — the appearance from your sheet,
  the exact face from your photo (or a consistent AI face if no photo). Per-shot edits still
  override the sheet. This is the moat for multi-person stories: real identity, consent-gated.
- **Wrong photo on one beat?** Hover a card → **⟳ Re-match** auto-picks the best-fitting
  photo for just that shot (now role-aware — a "brother" beat looks for a young man, not any
  family photo), or **🖼 Pick** to choose by hand.
- **Approve each stage: Key Frames → Video → Final Cut.** Click **Generate** on the
  *Key Frames* stage to render just the still images — review them, **↻ re-roll** any
  weak one, edit prompts — *before* spending on the expensive video. When you Generate
  the *Video* stage, it **reuses those approved stills** (no re-charge). This is the
  fix for the competitor's "one click drains your wallet."
- **Pick your audio** (same options as Story mode): **🎵 Music — auto** (generated
  score), **⬆ Upload a song** (your own track), **🎙 Voiceover** (ElevenLabs narrates
  the captions — choose a voice), or **🔇 None**.
- **Captions & format bar.** Set the burned-in captions — **on/off**, **position**
  (top/middle/bottom), **font**, **size**, **color**, and **1-line / 2-line** (or no limit).
  Pick the reel **format**: **9:16** (portrait), **16:9** (landscape), or **1:1** (square) —
  set this *before* generating, since shots are made at that aspect. Settings save as you
  change them. *(Font list shows installed fonts — Montserrat today; more are a deploy step.)*
- **Edit any shot inline:** each card lets you tweak the **caption, camera move, emotion,
  camera angle,** and the image prompt — changes cascade so nothing stale ships.
- **Render the reel** with the **🎬 Render reel** button (top bar). With a music bed it
  **cuts on the beat** (hard cut on a beat, soft dissolve off it) instead of a uniform
  crossfade — so it reads as a film, not a slideshow. Even with no music, cuts follow a
  steady **tempo** (set by the mood); voiceover keeps gentle cuts so they don't fight
  the narration. Each shot's clip
  appears on its card as it finishes, then the full reel plays in a panel below the
  board with a download link. The render reuses the same proven pipeline as the other
  modes, so models, costs, and safety/spend gates are identical. Stage cards show a
  rough **ETA** (~Nm) and a shimmer while generating.
- **Re-roll a shot** — hover a card and click **↻** to regenerate just that one shot
  (new still + clip) without re-rendering the whole reel — the quick fix for a single
  off shot.
- It runs on the **same engine** as Story/Studio/Brand — same models, same costs,
  same safety and spend gates — just surfaced as a board you drive stage by stage.

> Status: the Script and Storyboard stages are live; the paid stages (Key Frames,
> Audio, Video, Final Cut) show their cost and spend-cap check now and wire to the
> existing render pipeline next. See [docs/AGENTIC_CANVAS_PLAN.md](docs/AGENTIC_CANVAS_PLAN.md).

## 3d. Studio Mode (`/studio`)

A third page where you **type a brief and get a full reel** — no script to write.
It shares the same engine plus an **identity library**. Open it from the **🎬
Studio mode** link in the header. See [docs/MODE3_PLAN.md](docs/MODE3_PLAN.md).

**How it works:**
- **Identity library** — save a **Talent** (a face) and/or a **Product** (a photo
  + specs) once. Studio locks them across every shot, so the same person and the
  same product appear consistently. They're reusable across future reels.
- **Brief → shots** — pick a scope (*Commerce* for product/fashion/jewelry ads, or
  *General* for any idea), write a plain-language brief, and click **✨ Plan shots**.
  The AI drafts editable shot cards: on-screen line, camera move, and shot size.
- **Product fidelity** — on shots you mark **🛍 Product beat**, the *real* product
  image is used directly (never re-generated), so logos and fine detail stay exact.
- **Per-shot controls** — each card has a **Talent**/**Product** selector, a
  **Negative prompt** (what to avoid), and a **Continuity lock** (outfit/styling
  that must not change). Sensible defaults are prefilled.
- **The AI may draft on-screen lines** here (you edit them). All text still passes
  the safety moderation gate. Regulated ad claims/CTA/disclosure stay
  operator-supplied only in **Brand mode**.
- Everything else — Preview Stills, cost estimate, Generate, editor hand-off — is
  the same as the other modes.

## 4. Web UI — Step by Step

### App layout (all modes)

The web UI uses a **four-step wizard** with a persistent preview panel:

| Area | What it is |
|------|------------|
| **Header** | Story / Studio / Brand tabs, Guide link, settings (gear) |
| **Step pills** | Start → Frames → Polish → Render (labels vary slightly by mode) |
| **Editor (left)** | Cards for the active step only |
| **Preview (right)** | Phone-frame mockup; timeline strip after parse; finished video after render |
| **Sticky bar (bottom)** | Back / Next, cost chip, **Preview Stills**, **Generate** |
| **Settings drawer** | AI Credits, IP watermark, Performance log (Story mode) |

After you parse frames, the app jumps to the **Frames** step and moves the timeline into
the preview panel. **More** on each frame card expands director note, camera, lip-sync, etc.

### Step 1: Subject (optional)
- **Both fields are optional.** Leave them blank and the director **infers who is
  on screen — their age, gender, and look — from the story itself.** There is no
  default "stock person"; the story decides.
- **Name**: the main subject's first name, if you want to pin it.
- **Description**: physical description to steer AI portrait consistency (e.g.
  `woman, 30s, strong features, traditional clothing`). Helps most with the
  **Consistent face** toggle; safe to leave empty.

### Step 2: Start from a Script or a Raw Story + Set Assets Folder
- **I already have a frame script**: paste your Format B script, then click **Parse Frames →**.
- **I have a story**: paste raw story text, notes, or a transcript. Choose max frames,
  target length, and tone, then click **Draft frames with AI**. The app fills an
  editable Format B script and parses it into frame cards.
- **Important:** AI drafts only. Always review/edit the generated frames before
  previewing or rendering.
- **Assets folder**: point the app at this story's photos/videos one of two ways —
  - **📁 Browse folder…** — pick a folder on *your own computer*. The browser
    uploads every image/video in it (subfolders flattened, non-media skipped) into
    this session's assets dir, and the app returns that server-side path. This is
    the right choice on the hosted app (creative.kevat.ai) — the server can't see
    your local disk, so a typed path won't work. Large folders are sent in small
    size-batched chunks, so there's no practical file-count or size limit.
    Supported: JPG/PNG/HEIC/WEBP images and MP4/MOV/M4V/WEBM videos.
  - **Server folder…** — only when the media already lives on the server, inside
    the allowed assets root. Browse the on-box folders instead of uploading.
  - Leave blank for AI-only generation.
- If you used manual mode, click **Parse Frames →**. If you used story mode, the
  generated script is parsed automatically after drafting.
- After parsing: check `✓ N photos matched` — this confirms auto-matching worked
- On parse the app also **detects who speaks each beat** and **suggests** a camera
  move, image edit, and director note per frame (see below).

### Speakers & cast (multiple voices in one story)
A story usually has one narrator, but a beat may **quote someone else** —
*"My son asked, 'Mom, where is father gone?'"*. The app reads the script and tags
that beat with the **right speaker**, so it shows the **kid's face** (correct
gender/age) and, when lip-sync or voice-over is on, the **kid's voice** — not the
narrator's.
- When more than one speaker is found, each frame card gets a **🎭 Speaker**
  dropdown — change it if a line was attributed wrong.
- A **🎭 Cast voices** panel appears above the frames: pick an ElevenLabs voice
  per speaker (optional; otherwise voices fall back by gender/age, then to your
  global voice). An explicit `[voice: id]` in the script always wins.
- CLI: speaker detection is on by default; pass `--no-speakers` to disable.

### Pickable suggestions (you can always edit)
After parsing, each frame card shows small **clickable chips** under the **camera
move**, **image edit**, and **director note** fields. Click one to fill that field
— then edit it freely, or ignore the chips and type your own. Nothing is applied
automatically; they're just a starting point so you don't face a blank box.

### Step 3: Frame Assignment
See [Section 5](#5-frame-assignment--all-options) for full details.

### Step 4: Target Length
- Presets: 30s / 45s / 60s / 90s / Auto
- Durations are redistributed proportionally by word count across frames
- Silent frames always get 2.5s
- Click **Apply →** to redistribute, or type a custom value

### Step 5: Style & Quality
See [Section 7](#7-style--quality-settings).

### Step 6: Music
See [Section 9](#9-music-options).

### Step 7: Check Cost Estimate
The **💰 Estimated Cost** panel appears after parsing. Shows a per-category breakdown reflecting the **models the router will actually use** (mixed models are listed). Updates live when you change the Image/Video Model, quality tier, or per-frame model.

### Step 8: Generate
- Click **▶ Generate Video**
- Watch the progress log — each step is logged in real time
- **Clips appear in each frame card as they finish** (progressive reveal) — you don't wait for the whole render
- Video plays automatically when done
- Click **⬇ Download .mp4** to save
- **Did this reel perform?** (optional) — once you've posted it, come back to the output panel and jot the **views / likes** and a short **note** (e.g. "hook landed, drop-off at 3s"), then **Save**. It's stored with the run so we can later learn what actually performed.

### Step 9 (optional): Finish in your own editor (Premiere / DaVinci Resolve / Final Cut)
The app generates an **80–90% ready reel**. For the final polish — fine timing, grading, sound design, anything bespoke — hand it off to the editor's own licensed tool instead of re-doing it in-app:

- Click **⬇ Send to editor** (the export button on the output panel) to download `editor_export.zip`.
- Inside the zip:
  - **`timeline.fcpxml`** — open this in **Premiere Pro, DaVinci Resolve, or Final Cut** and the **whole timeline rebuilds** with every clip already placed at the right time. You start at ~80%, not from scratch.
  - **`captions.srt`** — standard subtitles; import/drag onto the timeline.
  - **`clips/`** — the individual clip files the timeline references.
  - **`output.mp4`** — the finished reel (reference, and the source of the music/voice mix).
  - **`edit_list.json`** — machine-readable manifest (timecodes, captions) for any custom tooling.
- **Relinking media:** after unzipping, your NLE may ask to relink the clips — point it at the `clips/` folder next to the FCPXML. This is normal for any interchange package.
- **Notes:** crossfades are flattened (re-apply transitions in your tool — you'll be re-cutting anyway). **CapCut/Descript:** there's no clean FCPXML import — just drag in the `clips/` (and the `output.mp4` into Descript for text-based editing).

> Model in one line: **generate 80–90% here → finish the last 10% in the tool your editors already own and know.** Many reels are postable straight from Step 8; Step 9 is the escape hatch when a human's final hand is worth it.

### Step 7b: Iterate without re-rendering (after Preview Stills)

Once you've clicked **👁 Preview Stills**, each frame card gains two controls so you can steer the output instead of re-rendering blindly:

- **🔄 Redo still** — change a frame's note/photo/edit/camera, click 🔄, and *only that one image* regenerates (cache-aware, a few seconds, no animation spend). It swaps into the card; nothing else is touched.
- **✓ Approved for animation** — ticked by default. Untick frames you're not happy with: on Generate, approved frames get full AI animation while unticked frames fall back to **free Ken Burns**, so the video still assembles complete. The **cost estimate drops** as you untick frames.

Recommended loop: **Preview Stills → 🔄 fix the images you don't like → untick the frames you're unsure about → Generate in Dev → watch clips appear live.** Approve more frames on the next pass. This is the cheap way to test direction — you're never forced to pay for every frame at once.

---

## 5. Frame Assignment — All Options

After clicking Parse Frames, each frame card shows:

```
┌──────────────────────────────────────────────────────────┐
│ f03   Caption text shown here...                  7.5s    │
│                                                            │
│  📁 IMG_3020.MOV   ← matched file badge                   │
│                                                            │
│ [ Auto ] [📁 From Folder] [📷 Upload] [AI Portrait] [AI Symbolic] │
│                                                            │
│ Director note:    ____________________________________     │
│ ✏️ Image edit:     ____________________________________     │
│ 🎥 Camera motion:  ____________________________________     │
│ 🎙 Lip Sync ☐     [voice dropdown appears when ticked]     │
│ Video start:      0  s  (only for video sources)           │
│ Duration:         7.5  s  (auto: 7.5s)                    │
└──────────────────────────────────────────────────────────┘
```

Each row top-to-bottom: pick the **source**, then optionally add a **director note** (emotion), an **image edit** (change the photo), a **camera move**, a **lip-sync toggle**, and finally **duration**.

### Source Buttons

| Button | What happens |
|---|---|
| **Auto** | Reset to auto-matched file from folder (sort order) |
| **📁 From Folder** | Use the auto-matched file (shown in green badge) |
| **📷 Upload Photo** | Browse and upload any image or video from your Mac |
| **🎨 AI Portrait** | Generate an AI photo of the subject for this frame |
| **🖼 AI Symbolic** | Generate AI objects/setting (NO person) for this frame |

### Director Note Field
- Free text instruction for the AI director (GPT)
- Affects: what to generate, how to animate, the emotional tone
- Most powerful field in the UI — see [Section 6](#6-director-notes--how-to-write-them)
- **Suggestions:** clickable chips appear below it; click one to fill (then edit), or ignore. After Preview, the **✨ Suggest from image** button proposes an image-aware note as a chip you apply or discard.

### 🎥 Camera Motion (dropdown)
- A **dropdown**, not a text box — pick the camera move; **no typing**. It's **pre-set to the AI's best pick** for that beat (shown with a ✨ auto note).
- Change it any time by choosing another move from the list (`360 orbit`, `crane up`, `dolly in`, `crash zoom`, `static`, …). Leave it on **✨ Auto** to let the director choose at render.
- **✨ Suggest from image** (after Preview Stills): looks at the actual generated still + caption and sets the dropdown to the best move for *what's really in the frame* — you can still change it.
- Full list and meanings — see [Section 7](#7-camera-angles--motion)

### 🎙 Lip Sync Toggle
- Tick it → this frame becomes a **talking face** speaking the caption aloud
- A **voice dropdown** appears (pick an ElevenLabs voice, or use the global default)
- **Duration goes grey** — it's now set by how long the spoken line takes, not word count
- Full explanation — see [Section 8](#8-lip-sync--make-the-subject-speak)

### ✏️ Image Edit Field
- Natural-language change to the photo *before* it's animated — see [Section 9](#9-image-edits--change-whats-in-a-photo)
- Examples: `add thunderstorm`, `make lighting warmer`, `add rain on the window`

### Video Start Field
- Only shown for frames assigned a video file (.mp4, .mov, etc.)
- Skips the first N seconds of the video — use when the good part starts mid-way
- Example: `3` skips the first 3 seconds

---

## 5b. Smart Matching & Style Exemplars

### 🤖 Smart-match images (AI)
By default, frames you don't pin with `[photo:]` are filled by **alphabetical order** of the folder — so the right photo only lands on the right beat by luck. Tick **🤖 Smart-match images (AI)** (or pass `--smart-match` on the CLI) and the app instead **reads each photo's content** (who/what is in it, *and any text/names written in it*) and places the best-fitting photo on each beat.

- **Videos are matched too:** a clip is sampled into a few keyframes, "watched," and matched like a photo — then played as **real footage** (not animated). Real footage is preferred over an AI-animated still when both fit, because it looks better and costs nothing to animate.
- **Respects your choices:** pinned `[photo:]` frames, AI frames, and the per-frame 🤖 Model picker are never overridden.
- **Cost:** each image/video is described **once** via the LLM and cached forever (pennies). Off by default — when off, behaviour is unchanged.
- Powered by the pluggable LLM brain (`config/llm.json`), so it works on OpenAI / Anthropic / Bedrock / Gemini.

### 🧑 Character face (optional) — lock an AI character to one face
When you have **no asset folder** and the story's people are AI-generated, upload **one face image** (Story start panel, under the assets row) to lock the **narrator/protagonist's** look across every AI scene — instead of letting the model invent (and drift) a face. It reference-edits every AI portrait of that character to your uploaded face.
- **Stronger than the 👤 Consistent face toggle**, which anchors on the *first AI-invented* face — here *you* choose the face.
- **Consent required:** tick the consent box to use it. If the face is a **real person**, use it only with their consent — the AI renders their likeness across scenes and the output is labeled AI-assisted (authenticity rule §5). Without the consent tick the uploaded face is ignored.
- Applies to the **narrator** speaker; other speakers (e.g. a quoted child) still use the auto consistent-face. Per-speaker faces are a Studio-mode feature (Talent library).

### 🎞 Multi-shot coverage (B-roll per beat)
By default each story beat is **one shot** — its matched photo/clip held under the
caption. Tick **🎞 Multi-shot coverage** (or pass `--coverage` on the CLI) and a
long beat is instead covered by the **main shot plus 1–2 supporting B-roll stills**,
played as quick sub-shots under the *same* caption — so the line feels **edited**,
not like a held slide.

- **How it splits:** the beat's duration is divided across its sub-shots (each at
  least ~2.5s), and every sub-shot gets a gentle, distinct camera move. The caption
  is untouched — it spans the whole beat, because the sub-shot durations sum back to
  the beat's length.
- **What it picks:** extra B-roll is chosen by the **same cached content match** as
  Smart-match (no extra GPU/CLIP cost) — only stills that *also* fit that line are
  pulled in, up to 2 (so up to 3 shots per beat).
- **When it stays single-shot:** lip-sync beats (a talking face is one continuous
  shot), silent beats, beats shorter than ~5s, or when there are no spare matching
  stills. It's **opt-in and additive** — off by default, and default one-media-per-beat
  behaviour is unchanged.
- **Cost:** the covered beats animate ~1.5–2× more shots, so turn it on for the
  important moments you want to feel polished, not every render.

### Style Exemplars — teach the AI your lab's hand-made taste
You can feed the pipeline **gold examples** from past manually-edited projects so the AI imitates your editing judgment (pacing, shot grammar, which media goes on which beat, and *why*). This is in-context guidance — **not** model training — and it's **off unless you enable it**.

**Folder layout** — one folder per past project under `exemplars/`:
```
exemplars/<project>/
  script.txt        # the original script
  assets/           # the source images & clips that project used
  final.mp4         # your manually-edited final video (reference / benchmark)
  exemplar.json     # the DISTILLED DECISIONS — the part the AI reads
```
Copy `exemplars/_template/exemplar.json`, fill it in (per beat: the media used, **why**, shot type, motion, duration, emotion + a global `style` block), and that's it. The pipeline reads `exemplar.json`; `final.mp4`/`assets/` are kept for reference and future auto-extraction. See `exemplars/README.md` for the field guide.

**Turn it on:** set `USE_EXEMPLARS=1`. The lab's house-style note + a few worked examples are then injected into scene design and image matching. 3–10 strong, diverse exemplars is plenty. Folders starting with `_` (like `_template`) are ignored.

> Honest note: a finished `.mp4` can't be "trained on" — what teaches the AI is the **decisions** in `exemplar.json`. If you only have final videos + scripts (no shot list), those decisions can be semi-auto-extracted later; for now, hand-author the best few.

---

## 6. Director Notes — How to Write Them

Director notes go to GPT-4.1 which designs the visual for you. Think like a film director telling a DP what to shoot.

**Format:** `[what is in frame] + [how the subject looks/feels] + [light quality]`

### Bad notes (too literal)
```
[note: show her being sad about arthritis]
[note: show the farm]
[note: wedding scene]
```

### Good notes (emotional, specific)
```
[note: Her hands grip the tractor wheel — strong, capable. 
Not poverty, this is PRIDE. Golden hour from the left, dust catching the light.]

[note: Medicine bottles on a windowsill. No person. Cold winter light from outside. 
The silence of an empty room. She is absent — the objects speak for her.]

[note: Ramp walk. Head high, back straight, heels clicking. 
This is the PEAK moment — full confidence. Warm gold backlight. She earned this.]
```

### Director Note Cheat Sheet

| Emotion | Note structure |
|---|---|
| Pride/Triumph | "Head high, direct gaze, warm gold backlight, full confidence" |
| Grief/Loss | "No eye contact, slumped posture, cold blue-grey, objects rather than face" |
| Longing | "Eyes looking just off-frame, window light, half-turned away" |
| Determination | "Jaw set, hands busy, warm practical light, grounded posture" |
| Fear/Uncertainty | "Shadow falling across face, shallow depth of field, out-of-focus background looms" |
| Innocence | "Young face, soft diffused light, looking up slightly, clean background" |
| Turning point | "Spark in eyes, phone glow in dark room, small smile starting — hope igniting" |

### When to Use ai_symbolic
Use `ai_symbolic` (no person) for:
- Illness, trauma, or events that feel exploitative if shown directly
- Abstract emotions (loneliness, confusion, change)
- Scene transitions and atmosphere frames
- When you have no good photo for a moment

```
[photo: ai_symbolic]
[note: No person. A crumpled train ticket on a dirty floor. 
Single overhead fluorescent light. This is the moment of rejection — not shown, felt.]
```

---

## 7. Camera Angles & Motion

A still photo with the right camera move feels like real film. This is what turns your photos into cinema instead of a slideshow.

**Where to set it:** the **🎥 Camera motion** field on each frame card, OR `[camera: ...]` (same as `[motion: ...]`) in the script.

**If you leave it blank,** the AI director chooses a camera move automatically based on the emotion of that line. So you only need to set it when you want a *specific* move.

### Camera move reference

| What you type | Effect | Best for |
|---|---|---|
| `360 orbit` | Full circle around the subject | Hero reveal, triumph |
| `bullet time` | Freeze + orbit (Matrix style) | Peak emotional moment |
| `crash zoom in` | Dramatic fast zoom toward subject | Surprise, shock, realization |
| `dolly in` | Smooth move toward the subject | Intimacy, building emotion |
| `dolly out` | Pull back to reveal surroundings | Scale, loneliness, isolation |
| `Hitchcock zoom` | Zoom in + dolly back (vertigo) | Dread, disorientation |
| `crane up` | Camera lifts upward | Victory, freedom, triumph |
| `crane down` | Camera lowers | Weight, defeat, gravity |
| `overhead` | Bird's-eye view from directly above | Isolation, vulnerability |
| `dutch angle` | Tilted horizon | Tension, unease, things "off" |
| `extreme close on eyes` | Macro on the face/eyes | Deep emotion, connection |
| `handheld` | Shaky, organic, human | Raw truth, documentary feel |
| `static` | No camera movement at all | Weight, stillness, gravity |
| `super 8mm` | Vintage film grain look | Memory, flashback, nostalgia |
| `whip pan` | Fast blurred pan | Energy, passage of time, transition |

### How it works behind the scenes
- **Kling / Seedance / Veo / Hailuo (and other prompt-driven models):** your words are sent as the motion instruction (they understand natural language)
- **Higgsfield:** your words are matched to one of 30 real cinematic presets (e.g. `360 orbit` → their actual 360° Orbit preset)
- **Either way:** type plainly. `crane up`, `slow zoom in`, `orbit around her` all work — and whichever model the router picks for that shot receives them.
- **Tip (avoids face morphing):** keep it to *one* gentle action + a slow camera (e.g. `slow push in`). Over-describing motion is the main cause of distorted faces.

### Important: camera moves vs. lip sync
A frame can be **either** a cinematic camera move **or** a talking face (lip sync) — not both. A talking face controls its own subtle head motion, so if you tick **🎙 Lip Sync** on a frame, any camera move on that frame is ignored. Use camera moves on your *visual* beats and lip sync on your *spoken* beats.

---

## 8. Lip Sync — Make the Subject Speak

Lip sync turns a frame into a **talking face** that narrates the caption in a real voice. Instead of silent text on screen, the person on screen actually speaks the line.

This is the platform's most powerful storytelling feature — the subject appears to tell their own story.

### Two ways it works (chosen automatically)

| Your frame source | Service used | What happens |
|---|---|---|
| A **photo** or AI portrait | **Hedra** | Generates a talking-head video from the still — the face speaks with natural head movement and blinks |
| A **video** of the person | **SyncLabs** | Re-syncs the existing mouth to a new clean voice — keeps the original footage, swaps the audio |

You don't choose the service — the app routes automatically: photos → Hedra, videos → SyncLabs.

### How to turn it on

**In the script:**
```
Frame 7
Bedridden, I watched modelling videos dreaming of the ramp.
[photo: lalita_face.jpg]
[lipsync: yes]
[voice: 21m00Tcm4TlvDq8ikWAM]
```

**In the web UI:** tick the **🎙 Lip Sync** checkbox on the frame card. A voice dropdown appears — pick a voice or leave it on the global default.

### Key things to know

1. **Duration is set by the audio.** When lip sync is on, the frame lasts exactly as long as the spoken line takes. The duration field goes grey — you can't set it manually, because the voice decides it.

2. **The caption is what gets spoken.** Whatever text is in that frame becomes both the on-screen caption AND the spoken words. Keep it natural to say aloud.

3. **Voice selection.** Set a default voice once (in the UI voice dropdown, or `ELEVENLABS_VOICE_ID` in `.env`), or override per frame with `[voice: voice_id]`. For Lalita, use a warm female Hindi-capable voice (`eleven_multilingual_v2` model handles Hindi/Hinglish well).

4. **Background music ducks automatically.** During a talking frame, music drops to 10% so the voice is clear. On non-talking frames it returns to 25%.

5. **It's optional and safe.** If the Hedra/SyncLabs key isn't set, or the service fails, that frame quietly falls back to a normal animated photo. Your render never crashes.

### When to use it
- **Best:** 2–4 emotional "I" statements where hearing the person makes it hit harder — the turning point, the confession, the triumph
- **Don't:** lip-sync every frame. It's expensive and exhausting to watch. Mix talking beats with silent cinematic beats.

### Cost
- Hedra (photo → talking): ~$0.10 per frame
- SyncLabs (video → re-sync): ~$0.012 per second
- The 💰 cost estimate updates the moment you tick the box.

---

## 9. Image Edits — Change What's in a Photo

The **✏️ Image edit** field lets you change a photo with plain English *before* it's animated. The app edits the still image, then animates the edited version.

**Where:** the ✏️ field on each frame card, or `[edit: ...]` in the script.

### What works well
| You type | Result |
|---|---|
| `add thunderstorm and dark clouds` | Storm added to the sky/background |
| `make the lighting warmer and golden` | Warm sunset tone over the whole image |
| `add rain on the window` | Rain streaks added |
| `more trees and greenery behind her` | Background filled with foliage |
| `add soft morning fog` | Atmospheric fog layer |

### Tips
- **Global changes work best** ("add storm", "warmer light"). Precise spatial edits ("move her to the left") are less reliable — that's expected.
- Works on **any** source: a real photo, an AI portrait, or an AI symbolic image.
- Cost: ~$0.04 per edited frame.
- Use it to unify mood — e.g. add the same cold blue tone to several "struggle" frames so they feel like one chapter.

---

## 10. Style & Quality Settings

### Image Model & Video Model (Auto by default)
Two dropdowns in the UI (and `--image-model` / `--video-model` on the CLI). Leave both on **Auto** and the router picks the best model per shot, cost-tier aware. Pick a specific model to force it for the whole video, or override a single frame with its **🤖 Model** dropdown (or `[model:]` in the script). Full behaviour in [§11 Models, Providers & Routing](#11-models-providers--routing).

- **Auto (recommended):** real photos animate via Kling; AI images route by type; Dev uses cheap models, Production uses premium ones.
- **Ken Burns** (in the Video Model dropdown): basic zoom, free, no AI — for testing or when credits are out.

### Clip Quality = cost tier
| Mode | Duration | Models used | Use when |
|---|---|---|---|
| **Dev** | 5s per clip | Draft tier — cheap (Kling Std video, Seedream images) | Testing script + captions; **always start here** |
| **Production** | Up to 9s per clip | Premium tier — best (Kling Pro / Seedance / Hailuo, Nano Banana) | Final render only |

This is the "test cheap, finish expensive" rule baked in: Dev renders cost a fraction; only switch to Production once the script + timing are right. Higgsfield/fal models that are always 5s are auto-extended to longer frames with a freeze-frame.

### Kling Mode (applies when the router uses Kling)
| Mode | Quality | Cost |
|---|---|---|
| **Pro** | Highest | ~$0.14/5s |
| **Standard** | Good | ~$0.08/5s |

### Mood / Colour Palette
Applied on top of every AI-generated image prompt:
| Mood | Effect |
|---|---|
| **Warm Nostalgic** | Amber tones, golden hour, slightly desaturated vintage |
| **Cold Struggle** | Blue-grey palette, overcast, high contrast deep shadows |
| **Triumphant** | Rich golds and saffron, directional sunlight, high saturation |
| **Default** | No overlay — scene intelligence chooses per frame |

### Orientation
- **Portrait 1080×1920** — Instagram Reels, TikTok, YouTube Shorts
- **Landscape 1920×1080** — YouTube (horizontal), LinkedIn

### Transition
- **Crossfade** — 0.4s dissolve between clips (default, smooth)
- **Hard Cut** — instant cut (more aggressive, editorial feel)

### IP / Property Watermark
Every reel can be tagged with one **HOB IP** (HOB Originals, The HOB Show, Unfiltered HOB, The Unplanned HOB…). Pick it from the **IP / Property** dropdown at the top of the page and that IP's **transparent PNG is laid over the whole video** for its full duration — HOB's own property branding.

- **Both modes:** works in story and brand. In brand mode it's **separate from the advertiser logo** (you can have both: the HOB IP watermark *and* the brand's corner logo).
- **Adding/renaming IPs:** they live in `config/watermarks.json` (IP name → PNG filename) with the PNGs in `deploy/watermarks/`. Add a line + drop a transparent PNG (full-frame, e.g. 1080×1920) to add an IP. See `deploy/watermarks/README.md`.
- **Graceful:** an IP with no PNG yet still appears in the dropdown (marked "no image yet") and simply applies no overlay — nothing breaks.
- Leave the dropdown on **No IP watermark** to skip it.

### Caption Styling
| Setting | Options | Recommendation |
|---|---|---|
| **Burn captions** | On / Off | Off = clean video, no subtitles burned (voice-over still plays if enabled) |
| **Font** | Baskerville, **Montserrat**, Satoshi*, Arial, Georgia, Helvetica | Baskerville for storytelling; Montserrat for modern/clean |
| **Size** | 24–96 pt | 52 default; 60–70 for dramatic |
| **Color** | White, Yellow, Black | White — always readable |
| **Position** *(default)* | Bottom, Middle, Top | Bottom for Reels standard |
| **Max lines per caption** *(default)* | No limit, 1, 2, 3 | 1–2 keeps captions clean; long text auto-shrinks to fit |

**Captions are optional and per-frame-controllable** (story **and** brand mode):
- **Burn captions** toggle (global) — turn all on-screen subtitles off for a clean cut.
- **Position** and **Max lines** in *Style & Quality* are the **global defaults**.
- Every captioned frame card has a **💬 Caption** row with its own **Position** and **Lines**
  dropdowns. Set them to override just that frame (e.g. push a caption to **Top** when the
  face sits low in the shot, or force a hero line to **1 line**). Leave on "default" to
  inherit the global setting.
- **Max lines** caps the line count; if a caption is too long the **font auto-shrinks**
  (to ~60% of the base size) so it fits without overflowing — nothing is truncated.
- **Reels safe-zone:** bottom captions are raised by default so they clear Instagram's
  bottom UI on a real phone. The output preview also shows a safe-zone overlay.
- **Keyword highlight:** wrap an important word or phrase in `==double equals==`
  to colour it differently in the burned caption, e.g. `I had ==nothing== left`.

\* **Satoshi** is a commercial font (free for personal use; commercial/hosted use
needs an Indian Type Foundry license). It is **not bundled** — drop a licensed
`Satoshi-Regular.ttf` into `deploy/fonts/` and rebuild to enable it; until then it
falls back to the system default. **Montserrat** ships with the app (OFL).

---

## 11. Models, Providers & Routing

### Model Selection & Routing (how Auto works)
You don't pick one provider for the whole video any more. A **router** (`agents/model_router.py`) chooses a model for each shot, reading metadata the pipeline already produces (real photo vs AI, portrait vs object, lip-sync, frame position) plus the **cost tier** (Dev → draft, Production → premium).

**Rules, in order:**
1. An explicit override wins — UI per-frame **🤖 Model**, `[model:]`/`[imgmodel:]`/`[vidmodel:]` in the script, or the global Image/Video Model dropdowns. (A wrong-kind id is ignored.)
2. **Real photos and videos are never AI-regenerated** at the image step — your real subject is preserved (this is your biggest realism advantage).
3. Otherwise the shot type + cost tier map to the best model via `config/models.json` → `routing`.

**Default routing policy** (edit in `config/models.json`):

| Shot | Dev (draft) | Production (premium) |
|---|---|---|
| Real photo → image | passthrough (kept as-is) | passthrough |
| Real photo → video | Kling Std | Kling Pro / Seedance |
| AI face / portrait (image) | Seedream | Nano Banana |
| AI object / symbolic (image) | Seedream | Flux / Nano Banana |
| Landscape / wide (video) | Kling Std | Hailuo / Seedance |
| Hero / establishing (video) | Kling Std | Seedance / Veo |
| Dialogue / lip-sync (video) | (lip-sync path) | Veo / Kling Pro |

**To add or re-rank a model:** add it under `models` in `config/models.json` and list its id in `routing` — no code change. fal-hosted models just need a `fal_endpoint`. New video models are reached through fal.ai via `agents/fal_video.py`; images via `agents/image_generator.py`.

> ⚠ fal endpoint slugs + prices are marked `VERIFY` — confirm on fal.ai before a Production render. Dev is safe: video = Kling, and unverified fal image models fall back to gpt-image.

### fal.ai-hosted models (Seedance, Veo, Hailuo / Nano Banana, Seedream)
- **One key (`FAL_API_KEY`), many models.** Reached through the same REST pattern.
- **Video:** Seedance (cinematic, multi-shot), Veo 3 (best native audio/dialogue), Hailuo (wide landscapes, motion physics).
- **Image:** Nano Banana (premium photoreal), Seedream (cheap draft/testing).
- **Cache:** fal video clips share `~/.hob_cache/kling_clips/`, keyed per model so switching models never returns the wrong clip; existing Kling/Higgsfield clips keep their old keys (no re-billing).

### Kling AI
- **What it does:** Animates a still photo into cinematic motion — hair moves, atmosphere breathes, camera pans
- **Account:** klingai.com → Top up at klingai.com → Recharge
- **Parallel limit:** 4 simultaneous tasks (Standard plan). Frames 5+ fall back to Ken Burns if all 4 slots are busy
- **Clip cache:** `~/.hob_cache/kling_clips/` — clips reused across renders (key = MD5 of image + prompt + duration)

### Higgsfield
- **What it does:** Cinematic DoP-style animation — generally more film-quality than Kling
- **Account:** cloud.higgsfield.ai → Billing
- **Always 5s:** Frames longer than 5s are extended with a freeze-frame
- **Motion presets:** 121 presets available. Auto-selected from motion_prompt keywords:
  - "zoom in / dolly in / approach" → Dolly In
  - "pull back / reveal / zoom out" → Dolly Out
  - "orbit / circle / arc" → Arc Left
  - "crane up / lift / rise" → Crane Up
  - "vintage / nostalgic / 8mm" → Super 8mm
  - Everything else → **General** (balanced, all-purpose)
- **Image hosting:** Your images are uploaded to Higgsfield's CDN automatically

### Ken Burns (Free)
- **What it does:** Slow zoom in/out on a still image. No AI, no cost.
- **Use for:** Testing scripts, adjusting captions, when animation credits are depleted
- **Always available:** Automatic fallback when Kling/Higgsfield fails or has no credits

---

## 12. Music Options

> **🥁 Cuts ride the beat (automatic).** When you add a music track (Upload or Suno),
> shot cuts now **snap to the music's beat** — a cut landing on a beat plays as a punchy
> hard cut, while off-beat junctions stay a soft dissolve. No setting to toggle; it just
> makes the reel feel *edited* instead of a slideshow. Voice-over reels keep smooth
> dissolves (you don't cut to narration). Falls back to the normal crossfade if the
> track has no clear beat.

### No Music
Silent video. Add your own music in editing.

### Upload Music File
- Browse and upload an MP3, M4A, WAV, or AAC file
- Music loops if shorter than video; fades out in last 3 seconds
- Volume: 25% (background level)

### Auto-Generate with Suno V5.5
- Describe the mood: `Emotional Bollywood instrumental, struggle to triumph, sitar and tabla`
- Generates ~3-4 minute instrumental track
- Cost: ~$0.05 per generation
- Takes 2–3 minutes to generate
- Wait for **✓ Music ready** before clicking Generate

**Good Suno prompts for storytelling:**
```
Emotional Indian classical, slow build to triumph, sarod and tabla, no lyrics
Melancholic Rajasthani folk, personal struggle, raw acoustic feel
Hopeful Bollywood instrumental, journey from hardship to success
Modern Indian cinematic, emotional flashback, piano with sitar
```

### Voice-Over (ElevenLabs)
- Each frame's caption text is read aloud in the selected voice
- **Frame-exact sync:** each spoken line is padded with trailing silence (or trimmed)
  to **exactly that frame's duration**, so the narration always lands with the
  caption and visuals — no drift as the reel goes on. If a line is naturally longer
  than its shot it's trimmed; shorter, it's padded.
- Silent frames get silence (no audio gap)
- Requires ElevenLabs credits
- Choose the **narrator** voice from the dropdown (loads from your ElevenLabs account)
- **Per-speaker voices:** when the script has more than one speaker, the **🎭 Cast
  voices** panel lets a quoted line be read in a different voice (the kid, the
  father). Unassigned speakers fall back by gender/age, then to the narrator voice.
- Leave the music/voice-over prompt **empty** to auto-compose a Suno brief from the
  story (genre + instruments + emotion arc); or type your own.

---

## 13. CLI Reference — All Flags

Run from the project root with the venv Python:

```bash
~/.pyenv/versions/3.12.3/bin/python3.12 run_caption.py [options]
```

### Required

| Flag | Description | Example |
|---|---|---|
| `--script PATH` | Path to your script .txt file | `--script lalita_story.txt` |
| `--assets PATH` | Path to photos/videos folder | `--assets /Downloads/lalita` |

### Common Options

| Flag | Default | Description |
|---|---|---|
| `--subject NAME` | _(empty)_ | Optional subject name/description; leave empty to let the director infer who's on screen from the story | 
| `--no-speakers` | off | Disable per-line speaker/cast detection (every beat becomes the narrator) |
| `--output PATH` | `output/caption_video.mp4` | Output file path |
| `--music PATH` | None | Path to background music MP3 |
| `--provider` | `kling` | Legacy global provider used as router fallback: `kling`, `higgsfield`, `kenburns` |
| `--image-model` | `auto` | Force an image model (e.g. `seedream`, `nano_banana`, `flux`, `gpt_image`) or `auto` to route per shot |
| `--video-model` | `auto` | Force a video model (e.g. `kling_std`, `kling_pro`, `seedance`, `veo`, `hailuo`, `higgsfield`) or `auto` |

### Quality & Rendering

| Flag | Default | Description |
|---|---|---|
| `--dev` | Off | Dev mode: cap all clips at 5s (half Kling cost, full quality) |
| `--width INT` | `1080` | Output width in pixels |
| `--height INT` | `1920` | Output height in pixels (1920 = portrait, 1080 = landscape) |
| `--fps INT` | `30` | Frames per second |

### Pipeline Control

| Flag | Default | Description |
|---|---|---|
| `--dry-run` | Off | Show cost estimate and frame plan without rendering |
| `--face-lock` | Off | V1 face consistency: generate first ai_portrait once, reuse same still for all subsequent portrait frames |
| `--lipsync` | Off | Auto-enable lip sync on all video-source frames (photos still need `[lipsync: yes]` per frame) |
| `--voice-id ID` | env default | ElevenLabs voice for lip sync audio (falls back to `ELEVENLABS_VOICE_ID` in `.env`) |
| `--skip-scene-ai` | Off | Skip GPT scene design, use generic motion prompts (saves ~$0.01, faster) |
| `--keep-temp` | Off | Don't delete temp directory after render (for debugging) |

### Full Example Commands

```bash
# Quick test — Dev mode, Ken Burns, no credits spent
~/.pyenv/versions/3.12.3/bin/python3.12 run_caption.py \
  --script lalita_story.txt \
  --assets /Users/amitmishra/Downloads/lalita \
  --subject "Lalita" \
  --provider kenburns \
  --dev \
  --output output/lalita_test.mp4

# See cost before spending credits
~/.pyenv/versions/3.12.3/bin/python3.12 run_caption.py \
  --script lalita_story.txt \
  --assets /Users/amitmishra/Downloads/lalita \
  --subject "Lalita" \
  --dry-run

# Production render — Auto routing (best model per shot, premium tier) + music
~/.pyenv/versions/3.12.3/bin/python3.12 run_caption.py \
  --script lalita_story.txt \
  --assets /Users/amitmishra/Downloads/lalita \
  --subject "Lalita" \
  --image-model auto --video-model auto \
  --music output/lalita_music.mp3 \
  --face-lock \
  --output output/lalita_final.mp4

# Force a specific model everywhere (e.g. Seedance video + Nano Banana images)
~/.pyenv/versions/3.12.3/bin/python3.12 run_caption.py \
  --script lalita_story.txt \
  --assets /Users/amitmishra/Downloads/lalita \
  --subject "Lalita" \
  --video-model seedance --image-model nano_banana \
  --output output/lalita_seedance.mp4

# Landscape YouTube version
~/.pyenv/versions/3.12.3/bin/python3.12 run_caption.py \
  --script lalita_story.txt \
  --assets /Users/amitmishra/Downloads/lalita \
  --subject "Lalita" \
  --width 1920 --height 1080 \
  --output output/lalita_youtube.mp4

# Skip GPT scene design (use pre-defined scenes only)
~/.pyenv/versions/3.12.3/bin/python3.12 run_caption.py \
  --script surabhi_story.txt \
  --assets surabhi_assets/ \
  --subject "Surabhi" \
  --skip-scene-ai \
  --output output/surabhi_fast.mp4

# Lip sync: talking faces on flagged frames (needs HEDRA_API_KEY / SYNCLABS_API_KEY)
~/.pyenv/versions/3.12.3/bin/python3.12 run_caption.py \
  --script lalita_story.txt \
  --assets /Users/amitmishra/Downloads/lalita \
  --subject "Lalita" \
  --provider higgsfield \
  --lipsync \
  --voice-id 21m00Tcm4TlvDq8ikWAM \
  --music output/lalita_music.mp3 \
  --output output/lalita_final.mp4
```

---

## 14. Cost Management

### Check costs before rendering

**Web UI:** The **💰 Estimated Cost** panel appears after Parse Frames. Updates live.

**CLI:**
```bash
python3.12 run_caption.py --script story.txt --assets /path --dry-run
```

Output:
```
[Dry Run] ── Frame Plan ──────────────────────────────────────────
[Dry Run]  f01  8.0s  real photo [IMG_1240.MOV]  Ken Burns ($0)
[Dry Run]  f02  5.0s  real photo [IMG_3511.JPG]  Kling (~$0.08)
[Dry Run]  f03  9.0s  Flux gen (~$0.05)  Kling (~$0.08)
...
[Dry Run]  Estimated total: ~$0.92 USD
```

### Reduce cost strategies

| Strategy | How | Saving |
|---|---|---|
| **Dev mode** | `--dev` flag or select "Dev" in quality dropdown | ~50% Kling cost |
| **Ken Burns fallback** | Select "Ken Burns" as provider | 100% animation cost |
| **Use real photos/videos** | Photos from assets folder skip AI image generation | ~$0.04–0.05 per frame |
| **Face-lock** | `--face-lock` generates portrait once, reuses for all portrait frames | $0.04–0.05 per duplicate frame |
| **Use Kling Standard** | Select "Standard" in Kling Mode | ~43% vs Pro |

### Updating prices

When vendor prices change, edit `config/pricing.json`:
```json
{
  "kling": {
    "standard_5s_usd": 0.08,
    "pro_5s_usd": 0.14
  },
  "image_gen": {
    "flux_portrait_usd": 0.05,
    "openai_gpt_image_usd": 0.04,
    "openai_edit_usd": 0.04
  },
  "higgsfield": {
    "generation_5s_usd": 0.10
  }
}
```

---

## 15. Cache System

The app caches aggressively to avoid re-spending credits on identical content.

### Editor trust controls
- **Read-only timeline strip:** after parsing, a horizontal strip shows frame order
  and duration so you can judge rhythm before rendering.
- **Posting kit:** story mode can turn the `Caption:` block into an Instagram
  caption/hashtag kit and cover-frame choice. This is story-mode only; brand copy
  and claims stay operator-supplied.
- **Text Card:** choose `Text Card` on a frame, or use `[layout: text_card]`, to
  create a full-screen bold statement card.
- **Export clips + edit list:** after a completed run, download a zip with the
  final MP4, per-frame clips, and `edit_list.json` for a human editor.
- **Redo motion:** after preview/render, redo only a frame's motion while keeping
  the approved still.

### Brand governance
Brand mode now includes a consent / likeness / content-rights confirmation. Brand
renders are blocked until this is checked, alongside logo/CTA/product mandatories.
Spend caps and run ledger rows are enforced server-side through the governance gate.

### What is cached

| Cache | Location | Key | When used |
|---|---|---|---|
| **Kling clips** | `~/.hob_cache/kling_clips/` | MD5(image + motion_prompt + duration) | Same image + same motion on re-render |
| **Scene designs** | `~/.hob_cache/scene_designs/` | MD5(caption + note + type + subject) | Same frame text on re-render |
| **AI images** | Your assets folder | Filename (e.g. `ai_portrait_f03.jpg`) | File exists and > 50KB |

### What breaks the cache

| Cache | What invalidates it |
|---|---|
| Kling clips | Changing image, motion prompt, or duration |
| Scene designs | Changing caption, director note, visual type, or subject description |
| AI images | Deleting the file, or file smaller than 50KB |

### Clear caches manually

```bash
# Clear Kling clip cache (forces all clips to regenerate)
rm -rf ~/.hob_cache/kling_clips/

# Clear scene design cache (forces all GPT scene calls to re-run)
rm -rf ~/.hob_cache/scene_designs/

# Clear AI images for a specific story (regenerate all AI frames)
rm surabhi_assets/ai_*.jpg
```

---

## 16. Best Practices for Storytelling Scripts

### Frame Count & Pacing
- **10–12 frames** is optimal for a 60–90 second Reel
- Under 6 frames: feels too short, no emotional build
- Over 15 frames: each frame too short to land

### Story Arc Structure
```
Frame 1:   HOOK — who is this person, why should I watch?
Frame 2-3: CONTEXT — background, before the event
Frame 4-5: CONFLICT — the problem, the challenge, the loss
Frame 6-7: LOWEST POINT — darkest moment
Frame 8:   TURNING POINT — the decision, the spark
Frame 9:   TRIUMPH — the result
Frame 10:  RESOLUTION — who they are now, the lesson
```

### Photo/Video Assignment Guide

| Frame type | Best source | Notes |
|---|---|---|
| Hook (Frame 1) | Your best video clip | Real motion is most powerful for opening hook |
| Childhood / past | Real old photos if available | If not: `ai_portrait` with age in director note |
| Emotional peak | Real video of the moment | Ramp walk, ceremony, achievement |
| Internal struggle | `ai_symbolic` | Never show trauma directly — show the environment |
| Support / family | Real family photos | Group shots work well here |
| Resolution | Real photo: confident, present-day | Strong, direct gaze |

### Caption Text Writing

The on-screen captions are SHORT (they appear as 3-15 word overlays timed to each frame). Write them:
- In first person, present tense
- Punchy, not complete sentences
- Maximum 15 words — the camera moves before longer text is read
- Build emotionally across frames — each caption is a single beat

```
BAD:  "During the time when I was suffering from rheumatoid arthritis I was completely bedridden"
GOOD: "Bedridden. I lost my hair, my confidence… and myself."
```

### The Caption Section (Instagram)
The `Caption:` section at the bottom is your Instagram post text — **not shown in the video**. Write the full story here for your followers who want to read the details. This can be 500–1000 words. The video is the visual hook; the caption is the full narrative.

---

## Quick Reference Card

```
START:         ~/.pyenv/versions/3.12.3/bin/python3.12 web_app.py → localhost:7860
DRY RUN:       --dry-run flag shows costs without rendering
FREE TEST:     --provider kenburns (no credits, Ken Burns zoom)
HALF COST:     --dev flag (5s Kling clips)
FACE LOCK:     --face-lock (one portrait AI image reused across all portrait frames)
LIP SYNC:      [lipsync: yes] per frame, or --lipsync flag for all video frames
CAMERA:        [camera: 360 orbit] per frame, or 🎥 field in UI
EDIT PHOTO:    [edit: add storm] per frame, or ✏️ field in UI
CLEAR KLING:   rm -rf ~/.hob_cache/kling_clips/
CLEAR SCENES:  rm -rf ~/.hob_cache/scene_designs/
CLEAR LIPSYNC: rm -rf ~/.hob_cache/lipsync_clips/ ~/.hob_cache/lipsync_audio/

RECHARGE FLUX:       fal.ai → Billing
RECHARGE KLING:      klingai.com → Recharge
RECHARGE HIGGSFIELD: cloud.higgsfield.ai → Billing
RECHARGE OPENAI:     platform.openai.com → Billing
RECHARGE HEDRA:      hedra.com → Creator plan
RECHARGE SYNCLABS:   app.sync.so → Billing
```

---

## 17. Complete Worked Example — Lalita's Story

This is a real, full story turned into a finished reel — with the *reasoning* behind every choice explained in plain language. If you read nothing else, read this.

### The raw story (what the client gives you)

> From a farmer to a model. At 19 married into a traditional Rajasthani home. A kisan ki beti who rode tractors. Diagnosed with rheumatoid arthritis after COVID — bedridden, lost her hair and confidence. Family stood by her. Watching modelling videos in bed, a spark: "Ek din main bhi ramp pe chalungi." She fought back with medication, yoga, diet — and walked the ramp in heels. Won Mrs. Rajasthan 1st runner-up. Today: a model, still a farmer, still fighting arthritis, financially independent.

### The thinking — why each frame is built the way it is

We map the story to a 10-frame arc: **Hook → Context → Strength → Restlessness → The Blow → Support → The Spark → The Fight → Triumph → Who She Is Now.** Then for each frame we ask three questions:

1. **What's on screen?** (real photo, real video, or AI-generated)
2. **What's the emotion?** (the director note)
3. **How does the camera move, or does she speak?** (camera vs lip sync)

### The complete annotated script

Paste this into the script box, set the assets folder, and click Parse Frames. Every choice is explained in the comment above it (the `#` lines are just notes for you — don't paste those, or do; the parser ignores unknown lines but cleaner to remove).

```
Reels

Frame 1
From a farmer to a model… This is the story of a desi girl with big dreams.
[photo: ai_portrait]
[note: Strong proud Rajasthani woman, direct gaze into camera, chin up. Golden hour, dust in air. This is a HERO, not a victim.]
[camera: crash zoom in]

Frame 2
At 19, I got married into a traditional Rajasthani home.
[photo: ai_portrait]
[note: Young bride in lehenga and ghunghat, beautiful traditional setting — but her eyes show quiet restraint, like a bird that just noticed the cage. Warm tones, subtle unease.]
[camera: slow dolly in]

Frame 3
Being a kisan ki beti, even after marriage, I was riding tractors and ploughing fields.
[photo: ai_portrait]
[note: Her hands on a tractor wheel, or standing in a mustard field. This is STRENGTH not poverty — proud, capable, powerful. Late afternoon golden light.]
[camera: crane up]

Frame 4
But I knew I was meant for more than "ghunghat, khana, kheti."
[photo: ai_portrait]
[note: Close on face, looking at something just off-frame — a window, a far horizon. Eyes lit with longing, not sadness. "There is more out there." Warm indoor light with a sliver of bright outside.]
[camera: dolly in]

Frame 5
After COVID, I was diagnosed with rheumatoid arthritis. I lost my hair, my confidence… and myself.
[photo: ai_symbolic]
[note: NO person. Medicine bottles on a windowsill, a hairbrush with fallen strands, an empty chair by a rain-streaked window. Cold blue-grey light. Silence. She is absent — the objects carry the grief.]
[edit: add cold grey winter light and frost on the window]
[camera: static]

Frame 6
My husband, father, friends — everyone stood by me. But inside, I felt shattered.
[photo: 06_family.jpg]
[note: Warmth around her — hands held, figures close. But HER face shows the contradiction: grateful and broken at once. Eyes wet, not crying. Warm light on others, shadow across her own face.]
[camera: handheld]

Frame 7
Bedridden, I watched modelling videos thinking, "Ek din main bhi ramp pe chalungi."
[photo: lalita_face.jpg]
[lipsync: yes]
[note: This is the TURNING POINT — she says it herself. Phone glow on her face in a dark room. A spark, a small smile starting. Hope igniting.]

Frame 8
Medication, yoga, diet… I fought back. The girl who couldn't stand walked the ramp in heels.
[photo: IMG_3020.MOV]
[start: 3]
[note: Use the real ramp-walk clip. Head high, back straight, full confidence. The emotional PEAK. Skip the first 3s of intro.]
[duration: 8]

Frame 9
I won Mrs. Rajasthan 1st runner-up. Suddenly, kisan ki beti turned model was everywhere.
[photo: 09_crown.jpg]
[note: Pure victory — crown, sash, stage. NOT humble, she EARNED this. High saturation, saffron and gold. Let the joy be loud.]
[camera: 360 orbit]

Frame 10
Today, I'm a model. But I'm still a farmer. Still fighting arthritis. Most importantly, I'm financially independent.
[photo: lalita_final.jpg]
[lipsync: yes]
[note: Closing image — proud, complete, direct gaze. Both worlds in one frame if possible. Soft warm light. This is what viewers remember.]

Caption:
"From the farm to the ramp, I am that 'Desi Girl' who is ready to conquer the world. I was 19 when I got married, just after completing my BSc… [full Instagram caption here — see client's text]"
```

### Why these specific choices (the plain-English logic)

| Frame | Choice | Why |
|---|---|---|
| 1 | `ai_portrait` + `crash zoom in` | No strong opening photo, so AI-generate a hero shot. Crash zoom grabs attention in the first 1.5s — critical for stopping the scroll. |
| 2 | `ai_portrait` + `slow dolly in` | The "married into tradition" beat. Slow push-in pulls the viewer toward her conflicted eyes. |
| 3 | `ai_portrait` + `crane up` | Her strength. Crane up = rising, powerful — matches "I rode tractors." |
| 4 | `ai_portrait` + `dolly in` | Inner restlessness. Moving closer = getting intimate with her longing. |
| 5 | `ai_symbolic` + `static` + `edit` | The illness. We **never show illness directly** — it's exploitative and weaker. Show the empty room instead. Static camera = the weight of stillness. The edit adds cold light to deepen the mood. |
| 6 | real photo + `handheld` | Family support. A real family photo is most authentic here. Handheld = raw, human, documentary truth. |
| 7 | **lip sync** | The spark. She *says her dream out loud* — hearing her voice makes it land. No camera move; her face talking is the whole shot. |
| 8 | real video + `start: 3` | The triumph moment. Real ramp-walk footage beats anything AI. We skip 3s of intro to land on the strong part. |
| 9 | real photo + `360 orbit` | The win. A full orbit around her crowned moment = the hero reveal. The most cinematic move for the highest point. |
| 10 | **lip sync** | The resolution. She closes the story in her own voice — the most personal, direct way to end. |

### The pattern to copy for any story

- **Open with a hook** (camera move that grabs — crash zoom, or your best video clip)
- **Use `ai_symbolic` for pain** — never show trauma on a face; show the environment
- **Use lip sync on 2 beats max** — the spoken turning point and the closing line
- **Save `360 orbit` / `crane up` for the single highest moment** — overusing big moves cheapens them
- **Use real footage for the literal peak** (the ramp walk, the ceremony) — AI can't beat the real thing
- **End on a strong, present-day, direct-gaze shot** — it's the last frame viewers see

### How to run it

1. Put Lalita's photos/videos in a folder, e.g. `/Users/amitmishra/Downloads/lalita`
2. Rename the key files to match the script (`06_family.jpg`, `09_crown.jpg`, `lalita_face.jpg`, `lalita_final.jpg`, `IMG_3020.MOV`) — or just use `ai_portrait` everywhere you don't have a photo
3. Start the app, paste the script, set the folder, **Parse Frames**
4. Check the **💰 cost estimate** — roughly: 6 Kling clips + 6 AI portraits + 2 lip-sync + 1 edit ≈ **$1.10**
5. Pick **Higgsfield** provider for best camera moves, choose a warm Hindi-capable voice, generate music
6. **Generate Video** → download

That's a complete, professionally-directed reel from a paragraph of raw story.
