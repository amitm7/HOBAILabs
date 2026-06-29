# CEO Likeness Plan — Karishma voice & on-screen presence

> Strategy + red-team artifact for two requests: (1) narrate stories in HOB CEO
> Karishma's cloned voice via ElevenLabs; (2) make her *appear talking* in
> partner-office backgrounds (AWS / Tata) from real photos, without a shoot.
> Grounded in a code audit of the current engine (see "Audit basis" below).
> Status: **strategy locked, pre-ship gate OPEN** — do not publish externally
> until §5 fixes land.

---

## 0. Verdict (TL;DR)

| Question | Answer | Risk |
|---|---|---|
| Can ElevenLabs narrate in Karishma's voice? | **Yes** — clone in ElevenLabs (PVC), drop `voice_id` into the existing seam. | **Low** (audio over real footage) |
| Can she "appear talking" in AWS/Tata offices without a shoot? | **Yes, as a talking-head** — real photo → background edit → Hedra lip-sync → her PVC voice. Not cinematic full-body presence. | **High** (face + premises + partner brands) |
| Is the engine ready to ship either *safely* today? | **No.** Five enforcement gaps (§5) mean the "consented + labeled exception" we *intend* is not what the engine *enforces*. | — |

**Sequencing:** Ship **Option 1 (voice host VO)** first — highest leverage, lowest moat
risk, and it only needs the Voice Kit (§3) + one fix (disclosure burn-in, §5.1).
Treat **Option 2 (on-screen)** as a productized **"CEO Partner Cut"** template for
**Brand/Studio only** — never a Story-mode default — and gate it behind §5 fixes
**1, 3, 4, 5**.

---

## 1. Governing thesis (from MARKET_FIT_REVIEW)

> **Value is inversely proportional to how visible the AI is on a real human.**

HOB's one irreplaceable asset is the *authenticity of real people telling real
stories*. Karishma's **voice off-screen** (host/narrator) barely touches that asset.
Karishma's **synthetic face speaking** touches it directly — and worse, the request
adds **two third parties** (AWS, Tata) whose premises and logos imply an endorsement
that may not be contractually true. So:

- **Option 1** = CEO-as-channel. Acceptable as a labeled, consented default for
  Brand mode and as a *host* VO over real subject footage in Story mode.
- **Option 2** = consented, labeled, **Brand/Studio-only** exception. Never the hero
  path for Humans-of-Bombay story reels.

---

## 2. OODA

- **Observe:** The engine already speaks every primitive these requests need —
  ElevenLabs PVC voice IDs, per-frame `[voice: id]` override, `config/voices.json`
  role map, `[lipsync: yes]` → Hedra (photo→talking-head) / SyncLabs (video re-sync),
  `[edit: …]` image edit, a likeness-consent gate, and a provenance summarizer.
- **Orient:** Capability is *not* the constraint — **enforcement** is. The platform
  honors *intent and labeling* but, today, does not *enforce* disclosure, does not
  moderate brand copy, lets an `[edit:]` silently regenerate a real face while
  keeping it labeled "REAL", takes consent as an operator's word, and has no concept
  of third-party (AWS/Tata) premises/brand consent.
- **Decide:** Voice-first. Close the disclosure gap before any external publish.
  Build Option 2 as a gated template, not a default.
- **Act:** Ship Voice Kit (§3) + CEO Partner Cut template (§4) behind the pre-ship
  gate (§5).

---

## 3. Artifact 1 — "Karishma Voice Kit" (Option 1)

### 3.1 Voice ID seam — where to put the clone

Resolution order the engine actually uses (`agents/cast.py::voice_for_frame`):

```
[voice: <id>] in script   →  UI "Cast voices" map  →  config/voices.json roles/language_voices  →  ELEVENLABS_VOICE_ID (global default)
```

**Recommendation — do NOT commit her voice_id to `config/voices.json`.** That file is
git-tracked; a CEO voice_id in it lets anyone with repo access + the prod ElevenLabs
key narrate as her. Keep it as an **operator-supplied render-time value**:

- **Brand mode:** set `brand.vo_voice_id = "<karishma_pvc_id>"` per render
  (web_app.py reads it at the announcer path).
- **Story host VO:** set the run's `voice_id` (UI voice field) to her PVC id, or use
  per-frame `[voice: <karishma_pvc_id>]` on host-narration frames only.
- **Optional convenience:** a `KARISHMA_VOICE_ID` entry in `.env` (gitignored), with
  the role left **empty** in the tracked `config/voices.json`.

### 3.2 ElevenLabs PVC — the real prerequisite

PVC is **not** "upload 5 min and go." ElevenLabs requires the **voice owner** to
record a spoken verification statement before a Professional Voice Clone activates.
That means **Karishma herself** completes the ElevenLabs verification — which doubles
as a real consent artifact. Capture clean samples (varied emotion; Hindi + English +
Hinglish if she narrates bilingually, because `eleven_multilingual_v2` prosody varies
by language and must be A/B-tested before any client render).

### 3.3 Consent record (engine-level)

The run gate (`governance.validate_likeness_consent`) blocks `/run` when a named
subject + AI voice/face is used without consent. For voice host VO supply, per run:

```json
"likeness_consent": { "voice": true },
"subject_name": "Karishma <surname>"
```

⚠️ **This is self-attested** (§5.4) — a boolean in the request, not a checked
document. Pair it with an **off-engine signed release** filed against her name +
the usage scope (internal drafts vs published ads vs Story host VO).

### 3.4 Disclosure line (must be visible — see §5.1)

> *"Narration uses an AI voice of Karishma <surname>, created and used with her consent."*

### 3.5 Two template scripts

**(a) Partner intro (Brand mode, announcer):**
```
[Tata Sampurna and AWS are building <verbatim, brand-approved claim>.]
[Here's what it means for <audience>.]
```
*(Spoken verbatim by her PVC voice; copy is brand-supplied and must pass moderation — §5.2.)*

**(b) Story host VO (Story mode — safest use):**
```
This is the story of <subject>.  [host framing, not impersonation]
...
At Humans of Bombay, we believe <editorial framing>.
```
**Hard rule:** her cloned voice **hosts/frames** — it must **never speak first-person
lines attributed to a story subject** ("I grew up in…"). That is impersonation and
defeats the moat.

---

## 4. Artifact 2 — "CEO Partner Cut" template (Option 2)

**Mode:** Brand or Studio commerce only. **Never** a Story-mode default.
**Form factor:** 2–4 talking-head beats — her face, natural blink/head motion, her
PVC voice, an office-ish background, captions. **Not** full-body walk-and-talk; Hedra
is a talking-portrait engine, not virtual production.

### 4.1 Exemplar frame script (annotated)

```
[Tata Sampurna partners with AWS to bring <verbatim brand-approved claim>.]
[photo: karishma_headshot.jpg]
[edit: professional studio backdrop, soft daylight, neutral office tone]   ← see WARNING
[lipsync: yes]
[voice: <karishma_pvc_voice_id>]
```

> **⚠️ `[edit:]` WARNING (red-team, §5.3):** `[edit:]` runs **gpt-image-1 over the
> whole still — including her face** — and the output is *still classified REAL*.
> Two consequences: (1) it violates the real-media-preservation invariant by
> AI-regenerating a real person's face; (2) it defeats provenance. **Prefer
> compositing her real cutout onto a real/licensed office plate** (background swap
> that leaves her face pixels untouched) over an `[edit:]` that repaints her. If you
> must use `[edit:]`, keep the prompt strictly background-only and **manually set the
> frame's provenance to AI_PORTRAIT** until §5.3 auto-flips it.

### 4.2 Third-party guardrail (no engine support today — §5.5)

"In front of the AWS office / Tata building" implies **AWS/Tata endorsement and uses
their premises + trademarks**. The engine has **zero** trademark/premises check. So:

- Get **written co-marketing sign-off from AWS/Tata** for any frame implying their
  premises or logo. HOB's consent is necessary but **not sufficient**.
- Default to **neutral/generic office** backgrounds unless the partner has explicitly
  approved their identifiable premises.

### 4.3 Positioning

Externally label this **"AI-assisted message from Karishma"** — never imply
"shot on location." Disclosure is **non-negotiable** here (it reads as a CEO message,
not candid documentary).

### 4.4 Cost (per `config/pricing.json`)

Hedra `$0.10`/generation + ElevenLabs (~25k chars/$) + any `[edit:]` gpt-image-1 cost,
**per beat**. Iterate on dev tier; production tier for the final cut only.

---

## 5. Pre-ship gate — red-team gaps that MUST be closed first

The earlier strategic read assumed "provenance already ships / label outputs" and
"brand copy passes moderation." **Both are false in the current code.** These are the
load-bearing fixes. (Audit basis: `agents/provenance.py`, `web_app.py`,
`agents/governance.py`, `agents/image_editor.py`, `run_caption.py`,
`agents/lipsync_coordinator.py`, `agents/safety.py`.)

| # | Gap (verified in code) | Why it matters | Fix (altitude: seam, not fork) | Blocks |
|---|---|---|---|---|
| **5.1** | **Provenance label is NOT burned into the MP4.** `provenance.summarize()` is written only to `provenance.json` (web_app.py:894) + UI badge + ZIP. `assembler.apply_brand_overlay` burns *brand* disclosure only; provenance is never passed. | An AI voice/face reel exports with **no disclosure the audience can see** → an *undisclosed* deepfake, not the "labeled exception" we claim. | When `provenance.summarize(data).real_person_ai` is true, pass its `label` into `apply_brand_overlay(disclosure_text=…)` (or a dedicated provenance overlay) so it burns on-screen. | **Opt 1 & 2** |
| **5.2** | **Brand announcer script is spoken verbatim with NO moderation.** `generate_single_tts(brand["announcer_script"], …)` (web_app.py:~2055) is never gated by `safety.moderate_*`. Brand validation checks files, not text. | AI voice of the CEO can speak an unmoderated/false/regulated claim. BRAND_PLAN §5 forbids AI *authoring* claims, but says nothing stops an operator pasting a bad one. | Run `safety.moderate_script` on `announcer_script` (and on-screen brand copy) before TTS/spend; keep it operator-supplied + now moderated. | **Opt 1 & 2** |
| **5.3** | **`[edit:]` regenerates a real face but stays classified REAL.** The edit pass (run_caption.py:~287) calls gpt-image-1 on `visual_path` regardless of source; `classify_frame` keys off `photo_spec` only, which stays `""` → REAL. | A real photo of Karishma edited "into the AWS office" becomes an **AI face exported as REAL** — violates real-media preservation *and* dodges the face-consent gate. | If `edit_prompt` touches a frame whose source is a real photo of a person, flip `photo_spec`→`ai_portrait` (trips face consent + provenance), OR restrict `[edit:]` to background-only compositing that preserves face pixels. | **Opt 2** |
| **5.4** | **Likeness consent is self-attested.** `validate_likeness_consent` only checks a boolean dict in the request; `record_likeness_consent` stores the flag + operator id, no document/signature. | The "consent gate" is an honor-system checkbox. Fine for an internal tool with trusted operators; **insufficient** for a CEO-likeness legal posture. | Require an off-engine signed release filed per subject+scope; optionally attach a consent-artifact reference (doc id / URL) to the consent record. Document this as policy. | **Opt 1 & 2 (policy)** |
| **5.5** | **No third-party trademark / premises check.** `[edit:]` prompts and lip-sync backgrounds pass through with zero check for "AWS"/"Tata"/competitor premises. `safety.critique_brand` runs only on *generated* `product_beat` frames, not edited real photos. | Implying AWS/Tata premises/endorsement without their sign-off is a legal/brand exposure independent of HOB's own consent. | Policy gate: backgrounds implying a third party's identifiable premises/logo require partner sign-off; default to neutral office. (Engine check optional/later.) | **Opt 2 (policy)** |

> **Docs-sync note (CLAUDE.md §4):** the §5.1–5.3 code fixes are real engine changes —
> when they land they must update `docs/HLD.md` (provenance→export is a new
> cross-cutting concern), `docs/LLD.md` (overlay call + `classify_frame`/`photo_spec`
> behavior + brand-moderation gate), and `GUIDE.md` + `OPERATOR_GUIDE.html`
> (the new visible disclosure + the `[edit:]`-on-real-face behavior change).

---

## 6. Phased plan

| Phase | Deliverable | Mode | Pre-reqs | Risk |
|---|---|---|---|---|
| **0** | Close **5.1** (disclosure burn-in) — universal, ship before anything | engine | — | — |
| **1** | Karishma PVC + signed release + Voice Kit; story **host** VO over real footage | Story / Brand | 5.1 | **Low** |
| **2** | 60s "Partner intro" — verbatim moderated copy, **VO only**, real product B-roll | Brand | 5.1, **5.2** | **Low–Med** |
| **3** | "CEO Partner Cut" — 2 talking-head beats, neutral office, disclosed | Brand / Studio | 5.1, 5.2, **5.3**, partner sign-off (5.5) | **Med** |
| **never** | Synthetic Karishma face as a Story *subject*; her voice speaking a subject's first-person lines | Story | — | **High — do not build** |

---

## 7. Pre-spend checklist (operator)

1. ElevenLabs PVC created **by Karishma** (verification = consent artifact); `voice_id` saved.
2. Signed likeness release on file, scoped (internal / published ad / Story host VO).
3. `HEDRA_API_KEY` set (and `SYNCLABS_API_KEY` if re-syncing real video of her).
4. `likeness_consent: { voice: true }` (+ `face: true` only if her image is animated) on the run.
5. **5.1 disclosure burn-in shipped** — confirm the exported MP4 shows the label.
6. Brand copy moderated (**5.2**) and human-approved verbatim — especially regulated/partner claims.
7. Option 2 only: partner (AWS/Tata) sign-off for any identifiable premises/logo; else neutral office.
8. `--dry-run` / 💰 estimate before spend; dev tier to iterate, production tier for final.

---

## Audit basis

Both passes read the live code on `main` (2026-06-28). Capability claims (voice
override, voices.json roles, Hedra/SyncLabs routing, `[edit:]`, consent gate,
provenance summarizer, pricing) **verified TRUE**. Enforcement gaps §5.1–5.5
**verified present** with file:line evidence. This artifact corrects the earlier
strategic read on two points it got wrong: provenance is **not** burned into the
video, and brand announcer copy is **not** moderated.
