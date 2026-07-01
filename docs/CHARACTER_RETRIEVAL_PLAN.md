# Character Consistency, Story/Frame Config & Retrieval — Gap Analysis + Implementation Plan

> Investigation artifact (NO code changes yet). Based on a 4-agent codebase audit
> (character/face pipeline, cast/character system, image-matcher ranking, per-frame
> override schema). Date: 2026-07-01. Companion to `CANVAS_STORY_PARITY_BACKLOG.md`.
>
> **Guiding rules:** everything flows through `frames[]` (HLD §4); use the pluggable seams;
> **Auto model mode stays the default** (per owner). Additive only — no pipeline fork.

---

## PART 1 — GAP ANALYSIS (what exists / what's missing)

### 1. Character consistency — does a supplied face reference actually get used?

**YES — but with three real limitations.**

- **The reference IS consumed.** `character_ref_path` → `image_generator.generate_contextual_image(reference_path=…)` → `image_editor.edit_image()` using **OpenAI `gpt-image-1` edit API** with the instruction *"Keep this EXACT person's face and identity"* (`image_generator.py:225-243`, `image_editor.py:23-46`). It only fires if the file **exists on disk** (`use_ref = bool(path) and os.path.exists(path)`) — a broken path is **silently dropped** to text-to-image.
- **Propagation to a speaker's frames works.** `canvas_run.set_character()` tags every frame whose `speaker_id` matches with `character_ref_path`, so anchoring a character once applies to all their shots. Priority order (`web_app.py:2589-2596`): **`talent_ref_path` (Studio) > `character_ref_path` (Canvas/Story) > auto-reuse**.
- **⚠️ Auto per-speaker reuse is OFF by default in Canvas.** The `face_ref` mechanism (`first_portrait_by_speaker` — reuse the first generated portrait for later shots of the same speaker) is gated behind a flag that **`_canvas_render_data` hardcodes to `False`**. So a character who speaks in 5 shots but is anchored on only 1 gets **inconsistent faces on the other 4**. This is the single biggest consistency gap.
- **⚠️ Symbolic frames ignore the reference.** `generate_symbolic_image()` takes no `reference_path` (`image_generator.py:285-318`) — a shot routed `ai_symbolic` that actually implies a person won't use the face.
- **⚠️ Single-provider identity path.** ALL identity-conditioned generation goes through `gpt-image-1` edit — a single point of failure and the slow/costly path. **No text-to-image model in `config/models.json` is used with native reference conditioning** (even `nano_banana`/`flux`, which *can* do reference-conditioned generation, are only used text-to-image). Real-media passthrough correctly ignores the ref (the real photo *is* the identity).

**Answer to "which providers support identity preservation":** today, only OpenAI `gpt-image-1` (edit), hardcoded in `image_editor.py`. `nano_banana` (fal) supports reference/multi-image conditioning natively and is the obvious upgrade to diversify off the single path.

### 2. Cast / character data model — readiness for story-level config: **~35–40%**

- **Exists:** `cast.detect_cast()` extracts `{id, label, gender, age_bracket}` per character + tags frames with `speaker_id/label/gender/age_bracket` (`cast.py:35-58, 80-161`); `subject_descriptor()` turns that into prompt text (`cast.py:248-262`); canvas `characters` add `ref_path` + `consent`; a Studio **talent library** (SQLite `talents`: id, name, ref_sha256, **freeform** descriptor, owner — `product_surface.py:59-63`).
- **Missing:** **role/relationship** (Father/Mother/Brother/Sister/Teacher/Doctor) — *not captured anywhere*; **skin tone / hair / clothing** attributes; a **persistent story-level character sheet** (cast is recomputed every render; talent descriptor is unstructured free text). Everything is per-frame-detected or per-run, not a pre-defined cast.

### 3. Image matching — why "brother" → mother+daughter (role-BLIND)

Three compounding causes, all fixable cheaply:
- **① Timing (root cause):** `smart_match()` runs **before** `detect_cast()` (`run_caption.py:108` before `:116`) — so at match time frames have **no `speaker_id`/gender/role**. The matcher literally can't know the shot is about "brother."
- **② Descriptions lose relationships:** `_DESCRIBE_PROMPT` (`image_matcher.py:143-147`) asks "who or what" but not *relationships/genders/ages* — a mother+father photo → "two adults in a kitchen." The relationship signal is destroyed at describe time (and cached that way).
- **③ Ranker never sees the speaker:** `_match_view()` sends only `caption + depicts + emotion` to `assign_images()` (`:300-308`); `speaker_gender/role` are dropped, and the prompt has **no "match gender/role to the person in the photo"** rule.
- Cache: per-image content-MD5, persistent (`~/.hob_cache/image_descriptions.db`). Not stale on rename; **won't carry relationship info until re-described**.

### 4. Per-frame overrides — what's editable vs missing

Editable in the canvas today (5, `canvas_run.py:395`): **caption, director_note, motion_override, negative_prompt, image_prompt**.

| Requested override | Existing key | Editable per-frame? |
|---|---|:--:|
| Character | `speaker_id` (+gender/age) | ❌ (cast panel only) |
| Face reference | `character_ref_path` | ❌ (attach modal, not inline) |
| Emotion | `scene.emotion` | ❌ (LLM-set, read-only) |
| Camera | `scene.camera_angle` + `motion_override` | ⚠️ motion only |
| Environment | `scene.scene_description`/`image_prompt` | ✅ (via image_prompt) |
| **Clothing** | — | ❌ **MISSING key** |
| **Hairstyle** | — | ❌ **MISSING key** |
| **Pose** | — narrative in scene_description only | ❌ **MISSING key** |

---

## PART 2 — IMPLEMENTATION TO-DO (grouped)

Legend: **[S/M/L]** effort · **⚙️UI-only** (engine already supports) · **🔧plumbing** · **P0/1/2** priority.

### A. Story Settings (story-level, inherited by every frame unless overridden)
- **A1 [M] 🔧 P0 — Story character sheet data model.** Add a `characters` list on the story/canvas state: `{id, role, name, gender, age, skin_tone, hair, clothing, source: real|ai, ref_path, consent}`. Roles seeded from a fixed vocab (main/father/mother/brother/sister/friend/teacher/doctor/police) + custom.
- **A2 [M] 🔧 P0 — Inheritance resolver.** A single `resolve_character(frame, story_chars)` that merges story defaults ← per-frame overrides (frame wins). Called once before scene design + generation so prompts + refs are consistent.
- **A3 [S] P1 — Extend `cast` schema with `role`.** `cast.py:_CAST_SCHEMA` + detection prompt → capture relationship role, not just gender/age. Feeds A1 auto-population.
- **A4 [S] P1 — Story-level defaults for existing globals** (orientation, quality tier, caption style) so they live with the story, not just the render call.
- **A5 [M] 🔧 P2 — Persist the sheet** across renders of the same story (`product_surface` `story_characters` table or run payload) so a cast is defined once and reused.

### B. Frame Settings (per-frame, override story defaults)
- **B1 [S] ⚙️ P0 — Make Emotion + Camera-angle editable** (add `scene.emotion`, `scene.camera_angle` to `EDITABLE_FRAME_FIELDS`; both already reach the prompt).
- **B2 [M] 🔧 P1 — New structural override keys:** `clothing`, `hairstyle`, `pose` on the frame; injected into the image prompt by the resolver (A2). (Today only expressible by hand-editing image_prompt.)
- **B3 [S] P1 — Per-frame character picker + face-ref inline** (choose which story character this shot depicts / override its ref) — surfaces `speaker_id` + `character_ref_path` on the card, not a modal.
- **B4 [S] ⚙️ P2 — Per-frame caption-position override** (engine already reads `caption_position`).

### C. Retrieval Improvements (fix wrong stock-photo matches)
- **C1 [S] P0 — Reorder: run cast detection BEFORE `smart_match`** (both CLI `run_caption.py` and the canvas match path) so frames carry `speaker_id/gender/role` at match time. *Highest ROI, ~no new code.*
- **C2 [M] P0 — Relationship-aware image descriptions.** Update `_DESCRIBE_PROMPT` to extract *people count + genders + apparent ages + relationships* ("elderly man + young girl; likely father & daughter") and visible names. Bump the description cache-key version so new descriptions regenerate.
- **C3 [S] P0 — Pass speaker into the ranker + add a role/gender priority tier.** `_match_view()` includes `speaker_role/gender/age`; `assign_images()` gets a top rule: *"match the frame's person (e.g. 'brother' = young male) to a photo containing that gender/age; reject gender/age mismatches."* (C1+C2+C3 together ≈ 90% of mismatches.)
- **C4 [M] P2 — Confidence + review flag.** A cheap per-assignment vision check "does this image fit this beat/person?" → flag low-confidence shots (like ⚡ fidelity-suggest, for content-fit).
- **C5 [L] P2 — CLIP/embedding rerank** of the LLM's top-K candidates (catches "right direction, wrong photo of two similar ones"). Cache embeddings by content hash.
- **C6 [S] P1 — Per-shot "Re-match this shot"** (run matcher for one frame) + show the matched photo's description on hover (explainability). *(Pairs with the existing 🖼 Pick.)*

### D. Character Consistency (same person across the story)
- **D1 [S] 🔧 P0 — Enable auto per-speaker face reuse by default in Canvas.** Set `face_ref=True` in `_canvas_render_data` so every un-anchored shot of a speaker reuses that speaker's first portrait → consistent face without anchoring each frame. *Directly answers "ensure every frame automatically uses the supplied identity whenever available."*
- **D2 [S] P0 — Fix the silent ref drop.** If `character_ref_path` is set but the file is missing, log + surface it (don't silently fall back to a random face).
- **D3 [M] P1 — Symbolic-with-person path.** When a shot implies a person but is routed symbolic, either route it to the portrait path or pass the ref — stop losing identity on those frames.
- **D4 [M] P1 — Diversify the identity provider.** Route reference-conditioned generation through a **native ref model (`nano_banana`)** via the router, with `gpt-image-1` edit as fallback — removes the single point of failure and usually improves likeness.
- **D5 [M] P2 — Multi-reference per character** (front + profile) for stronger identity lock on varied angles.

### E. AI Generation (prompt / face / pose / style / scene continuity)
- **E1 [S] P1 — Resolver-built prompts.** Prompts assembled from resolved character attributes (gender/age/skin/hair/clothing) + scene + role — consistent wardrobe/appearance across shots by construction.
- **E2 [S] P1 — Re-roll re-derives the prompt** from the (edited) caption/attributes first, instead of just reseeding — fixes "AI keeps generating the wrong thing."
- **E3 [M] P2 — Wardrobe/appearance continuity lock** across a character's shots (reuse `continuity_lock` mechanism, keyed per character not per frame).
- **E4 [M] P2 — Pose control** (pose text → prompt now; ControlNet/pose-ref later — see Future).
- **E5 [S] P1 — Negative-prompt defaults per role** (e.g., "no other people" on a solo-speaker portrait).

### F. UI
- **F1 [M] P0 — Story Settings panel** with a **Character Sheet** (add/edit characters: role, name, gender, age, skin, hair, clothing, real-photo/AI/upload, consent). Mirrors the requested layout.
- **F2 [S] P0 — Per-frame override affordances** on the board card: character picker, face-ref, emotion, camera-angle (+ clothing/hair/pose once B2 lands).
- **F3 [S] P1 — Consistency + match indicators:** a badge when a shot's face is anchored/auto-reused; a ⚠️ on low-confidence matches (C4); description-on-hover (C6).
- **F4 [S] P2 — "Advanced" model picker** (image/video/kling) — **Auto stays default**, this is opt-in.

### G. Backend (schema / APIs / storage / caching / pipeline / migration)
- **G1 [M] 🔧 P0 — Character schema + resolver** (A1/A2) as a shared module (`agents/characters.py`) reused by Story/Brand/Canvas (kills the current `derive_characters` vs `character_refs` divergence).
- **G2 [S] P0 — Routes:** `GET/POST /api/canvas/<id>/story-characters` (CRUD the sheet); extend `/frame` to accept the new override keys.
- **G3 [S] P0 — Reorder cast→match in the pipeline** (C1) + thread `speaker_*` through `_match_view`.
- **G4 [S] P1 — Description cache versioning** (C2) + a migration note (old rows degrade gracefully).
- **G5 [S] P1 — Extend talent table** with structured `gender/age/skin/hair` columns (optional; today freeform `descriptor`).
- **G6 [S] P1 — Pricing/estimate:** relationship-aware describe is a slightly bigger vision call — reflect in `pricing.estimate` if material.
- **G7 [M] P2 — Persist story characters** (A5) — table + migration.

### H. Future Enhancements (track, don't build now)
- **H1 — Face-recognition identity library:** embed operator photos, auto-find "all photos of my father" in a folder (privacy boundary; consent-gated). The real "character-aware retrieval."
- **H2 — Relationship-aware embeddings / dependency parsing** for caption→role extraction beyond the LLM (subject/object/relationship parse).
- **H3 — Multimodal reranker** (image+text cross-encoder) replacing the single LLM assignment call at scale.
- **H4 — ControlNet/pose-ref** for true pose control; IP-Adapter for stronger identity than edit-prompt.
- **H5 — LoRA per recurring character** (only for original/synthetic talent, never for a real named person — moat rule).
- **H6 — Cross-story character reuse** (a HOB-wide talent/character library with governance).

---

## Recommended first slice (P0, high ROI, low risk)
1. **Retrieval:** C1 (cast-before-match) + C2 (relationship descriptions) + C3 (speaker into ranker). → kills the "brother→mother" class of bugs.
2. **Consistency:** D1 (auto face-reuse default in Canvas) + D2 (surface missing-ref). → same face across the story automatically.
3. **Config:** A1/A2/G1 (character schema + resolver) + F1 (Story Settings sheet) + B1/F2 (make emotion/camera editable). → story-level cast with per-frame overrides.

None require Kafka/Redis or a pipeline fork; all are additive through `frames[]` + the existing seams.
