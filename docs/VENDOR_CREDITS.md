# VENDOR_CREDITS — every key, what it powers, where to recharge

**Status:** LIVE · 2026-07-07 · Production credit checklist, assuming all balances are zero.
URLs verified against the actual API endpoints in `agents/*` (not guessed from vendor names —
e.g. Suno is billed at **sunoapi.org**, a proxy, not suno.com). Check live balances in-app:
**💳 AI Credits panel** / `GET /balances` (`agents/balances.py`) — ElevenLabs, Kling, Suno,
fal report live numbers; the rest expose no balance API.

Before any spend: 💰 estimate (`/api/estimate`) or `--dry-run`. Every vendor below degrades
gracefully (rule 4) — a missing key = ledger warns + fallbacks, not a failed render.

## Tier 1 — core (a normal render won't run without)

| Vendor | Env key(s) | Powers | Recharge / billing URL |
|---|---|---|---|
| **OpenAI** | `OPENAI_API_KEY` | Default LLM brain (scene intelligence, matcher, segmenter), TTS voiceover, `gpt_image`/`gpt_image_edit` | https://platform.openai.com/settings/organization/billing/overview · keys: https://platform.openai.com/api-keys |
| **fal.ai** | `FAL_API_KEY` | **Most load-bearing key**: seedream (draft stills), nano_banana + nano_banana_edit (identity), flux / flux_kontext, seedance / veo / hailuo (video), aura_sr + clarity (upscale) | https://fal.ai/dashboard/billing · keys: https://fal.ai/dashboard/keys |
| **Kling** | `KLING_ACCESS_KEY` + `KLING_SECRET_KEY` | `kling_std` (the DRAFT video tier — dev renders route here) + `kling_pro` (premium) | https://app.klingai.com/global/dev (API console → billing/resource packs) |

## Tier 2 — full feature coverage

| Vendor | Env key(s) | Powers | Recharge / billing URL |
|---|---|---|---|
| **ElevenLabs** | `ELEVENLABS_API_KEY` | Narration voices, per-character voices, dubbing | https://elevenlabs.io/app/subscription |
| **Google Gemini** | `GEMINI_API_KEY` | **Lyria 3 — the DEFAULT music engine** (`config/music.json`) + optional LLM provider | key: https://aistudio.google.com/apikey · billing: https://console.cloud.google.com/billing |
| **Suno (via proxy)** | `SUNO_API_KEY` | Music fallback / vocal songs. ⚠ Billed at **api.sunoapi.org** (third-party proxy), NOT suno.com | https://sunoapi.org (account → credits) |

## Tier 3 — premium / feature-specific (buy when the feature is used)

| Vendor | Env key(s) | Powers | Recharge / billing URL |
|---|---|---|---|
| **Higgsfield** | `HIGGSFIELD_KEY_ID` + `HIGGSFIELD_KEY_SECRET` | Premium video (DoP tier for client renders; falls back to Kling/fal) | https://platform.higgsfield.ai (console → billing) |
| **Hedra** | `HEDRA_API_KEY` | Lip-sync talking faces (primary) | https://www.hedra.com (app → subscription/API) |
| **sync.so (SyncLabs)** | `SYNCLABS_API_KEY` | Lip-sync alternative | https://sync.so (dashboard → billing) |
| **Anthropic (direct)** | `ANTHROPIC_API_KEY` | Claude brain via `LLM_PROVIDER=anthropic` — the working Opus 4.8 path (Bedrock lacks the entitlement); recommended for client/final renders | https://console.anthropic.com/settings/billing |
| **AWS Bedrock** | *(IAM role — no key)* | Prod Claude brain path. ⚠ Sonnet 4.6 model access NOT enabled in us-east-1 (silently falls back) — enable per-model in Bedrock → Model access, billed to the kevat.ai AWS account | https://console.aws.amazon.com/bedrock (Model access) · https://console.aws.amazon.com/billing |
| **HuggingFace** | `HF_API_KEY` | Aux/minor | https://huggingface.co/settings/billing · tokens: https://huggingface.co/settings/tokens |

## Free — no credits needed

| Service | Used by | Note |
|---|---|---|
| RFC-3161 TSA (digicert/sectigo) | C2PA signing timestamp (`HOB_C2PA_TSA`) | Free public service; outbound HTTP only |
| Ken Burns / tempo-grid / libass | Video, music, caption fallbacks | The zero-credit degradation floor — a render completes with ONLY OpenAI funded |

## Recommended top-up order from zero

1. **OpenAI $10** → C2PA smoke render (real photos + Ken Burns + no music ≈ cents).
2. **fal $20 + Kling $10** → full dev-tier render (stills + real motion; exercises decision log).
3. **Gemini key (free tier)** → music.
4. **ElevenLabs** when narration is needed; **Anthropic** when switching the brain for client quality.
5. Tier 3 on demand.
