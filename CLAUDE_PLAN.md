# AI Reel Generator — Claude Prompt Plan & Editor Instruction Guide

## Purpose of This Document

This file serves two audiences:

1. **Dev team** — how to use Claude (AI) effectively at each stage of building
   the platform
2. **Video editors** — prompt templates to use Claude as an intelligent assistant
   during daily reel production

---

## Part 1 — How to Use Claude When Building the Platform

### Principle
Claude is most effective when given **role + context + constraint + output format**.
Never give Claude a vague instruction. Always tell it what you are building,
what already exists, and what format you need back.

---

### Phase 1: FFmpeg Pipeline Development (Week 1–2)

**Prompt: Generate FFmpeg command for a specific task**
```
You are an expert FFmpeg engineer.

Task: {describe exactly what you need}
Input: {file type, resolution, fps}
Output: {target format, resolution, duration}
Constraints: {no re-encoding audio / must run on M1 Mac / etc}

Generate the FFmpeg command with explanation of each flag.
Test it with a dummy file named input.mp4.
```

**Prompt: Debug an FFmpeg error**
```
I am building a reel assembly pipeline in Python using subprocess + FFmpeg.

This is the command I ran:
{paste command}

This is the error:
{paste error}

My input files:
{list files and their properties from ffprobe output}

Diagnose the error and give me the corrected command.
```

---

### Phase 2: LangGraph Agent Development (Week 3–4)

**Prompt: Build a new LangGraph agent**
```
I am building an AI reel generation platform using LangGraph + FastAPI + Python.

Current state schema: {paste ReelState}
Existing agents: {list agents already built}

Build Agent: {agent name}
Purpose: {what it does}
Inputs from state: {fields it reads}
Outputs to state: {fields it writes}
Tools available: {OpenAI API / FFmpeg / Whisper / etc}

Return:
1. The complete agent function
2. How to add it to the existing workflow graph
3. Any new Pydantic fields needed in ReelState
```

**Prompt: Debug a LangGraph state issue**
```
My LangGraph pipeline is failing at the {agent name} step.

State at the point of failure:
{paste state JSON}

Agent code:
{paste agent function}

Error:
{paste traceback}

Find the bug and fix it. Explain what caused it.
```

---

### Phase 3: AI Enhancement Models (Week 5–6)

**Prompt: Integrate an enhancement model**
```
I need to integrate {Zero-DCE / Real-ESRGAN / MediaPipe} into my Python pipeline.

Context:
- Running on {local GPU / AWS ECS / CPU-only}
- Input: MP4 video clips, average 5-10 seconds, 1080p
- Must process in under {X} seconds per clip
- Pipeline is async FastAPI

Show me:
1. Installation steps
2. Python integration code (async-compatible)
3. How to apply it only when scene quality score < 7.0
4. How to skip gracefully if model is unavailable
```

---

### Phase 4: FastAPI Backend (Week 7–8)

**Prompt: Design a FastAPI endpoint**
```
I am building a FastAPI backend for an AI reel generation platform.

New endpoint needed:
- Route: {method + path}
- Purpose: {what it does}
- Request body: {describe fields}
- Response: {describe expected output}
- Background task: {yes/no — if yes, describe the async job}
- Auth: JWT (already implemented)

Generate:
1. Pydantic request/response models
2. Route handler
3. Background task function stub
4. Redis job tracking update
```

**Prompt: Database schema design**
```
I am designing PostgreSQL tables for a video reel generation SaaS.

Platform context:
- Multi-brand (one company, multiple influencer brands)
- Each brand has its own assets, templates, voice settings
- Jobs are async (queued, processing, done, failed)
- Assets are stored in S3 (store only keys, not files)

Design tables for: {users / brands / assets / jobs / renders / templates}

Return:
1. CREATE TABLE SQL with indexes
2. SQLAlchemy models (async, using mapped_column)
3. Any foreign key relationships
```

---

### Phase 5: AWS Infrastructure (Week 9–10)

**Prompt: Generate infrastructure as code**
```
I am deploying an AI video rendering platform on AWS.

Architecture:
- FastAPI app → ECS Fargate
- Python workers (FFmpeg + AI models) → ECS Fargate (GPU optional)
- Job queue → SQS
- File storage → S3
- DB → RDS PostgreSQL
- Cache → ElastiCache Redis

Generate:
1. Docker Compose for local dev
2. ECS Task Definition JSON for {app / worker}
3. SQS queue config (visibility timeout, DLQ)
4. S3 bucket policy for upload + output separation
```

---

## Part 2 — Daily Editor Prompt Guide

These prompts are for your **video editing team** to use Claude
as a production assistant every day.

---

### Before Starting a Reel

**Prompt: Generate a reel brief from a topic**
```
I am creating a {duration}-second Instagram Reel for {brand name}.

Topic: {topic}
Target audience: {describe audience}
Style: {motivational / educational / storytelling / product showcase}
CTA: {what you want viewers to do}

Generate:
1. Hook (first 3 seconds — must stop the scroll)
2. 4-5 scene breakdown with visual descriptions
3. Suggested caption (for each scene, MAX 3 words, ALL CAPS)
4. Voiceover script (natural speaking tone, {duration} seconds when read aloud)
5. Suggested background music mood
```

---

### Script to Scene Plan

**Prompt: Convert a script to scene-by-scene plan**
```
Convert this script into a scene plan for a {duration}-second reel.

Script:
{paste script}

For each scene return:
- Scene number
- Duration (seconds)
- Voiceover text for this scene
- Visual direction (what to show)
- Caption text (3 words max, ALL CAPS)
- Emotion/energy level (1-10)

Format as a table.
```

---

### Reviewing a Draft Reel

**Prompt: Get AI feedback on a reel plan**
```
Review this reel plan and identify weaknesses.

Platform: Instagram Reel
Duration: {X} seconds
Audience: {describe}
Style: {style}

Scene plan:
{paste scene plan}

Check for:
1. Is the hook strong enough to stop scrolling in 1.5 seconds?
2. Is pacing too slow anywhere?
3. Are any scenes too long (> 7 seconds)?
4. Is the CTA clear and urgent?
5. Overall score out of 10 with reasoning.

Be direct. Don't sugarcoat weak points.
```

---

### Music Selection Prompt

**Prompt: Recommend music mood for a reel**
```
I am editing a reel with this brief:

Topic: {topic}
Emotion: {what the viewer should feel}
Audience: {describe}
Duration: {X} seconds
Brand vibe: {luxury / aspirational / fun / educational / spiritual}

Recommend:
1. Music genre and tempo (BPM range)
2. 3 specific royalty-free tracks or Epidemic Sound search terms
3. At what second in the reel should the music drop or change energy
```

---

### Caption Writing

**Prompt: Generate impactful captions for all scenes**
```
Write on-screen captions for each scene of this reel.

Rules:
- MAX 3 words per caption
- ALL CAPS
- Must be punchy, not descriptive
- Match the energy of the voiceover
- No filler words (the, a, an, is, are)

Scenes and voiceover:
{paste scene list with voiceover text}

Return a table: Scene | Voiceover | Caption
```

---

### Batch Planning (5-6 Reels/Day)

**Prompt: Generate a full day's content plan**
```
I need to plan {N} reels for today's production.

Brand: {brand name}
Content pillars: {list 3-4 content themes}
Platforms: Instagram Reels + YouTube Shorts
Total new assets available: {describe footage/images available today}

For each reel generate:
- Title / internal reference
- Core message (1 sentence)
- Hook
- Scene count and total duration
- Which existing assets to use
- Priority (1 = urgent, 3 = flexible)

Format as a production schedule table.
```

---

## Part 3 — Platform Build Roadmap

### 12-Week Execution Plan

| Week | Focus | Deliverable | Owner |
|---|---|---|---|
| 1–2 | FFmpeg mastery + Python pipeline basics | Script → Voice → Raw video | Dev |
| 3–4 | LangGraph agents 1–3 (Analyzer, Segmenter, Matcher) | Auto scene selection working | Dev |
| 5–6 | Enhancement engine + Agent 4 (Assembler) | First AI-assembled reel | Dev |
| 7–8 | Captions + Brand layer + multi-platform export | Social-media-ready output | Dev |
| 9–10 | FastAPI backend + job queue (Redis/SQS) | API accepting jobs, async processing | Dev |
| 11–12 | AWS deploy (ECS + S3 + RDS) + basic UI | Public MVP accessible by team | Dev |
| 13+ | Beat sync, enhancement models, CLIP matching | Quality parity with human editor | Dev |

---

### Tech Stack Decision

| Layer | Choice | Reason |
|---|---|---|
| AI Pipeline | Python + LangGraph | All AI tools are Python-native |
| API Backend | FastAPI | Async, typed, fast to learn for Java devs |
| Database | PostgreSQL + SQLAlchemy (async) | Familiar relational model |
| Job Queue | Redis (dev) → AWS SQS (prod) | Simple → scalable path |
| File Storage | AWS S3 | Industry standard, cheap |
| Deploy | Docker → ECS Fargate | Containerized, no server management |
| Frontend | Flutter Web or React | Flutter if mobile app planned too |
| Voice | ElevenLabs (quality) / OpenAI TTS (speed) | Use both based on job priority |
| Enhancement | Zero-DCE, Real-ESRGAN, MediaPipe | Open source, self-hostable |

---

### Cost Estimate (MVP, 5-6 reels/day)

| Service | Usage | Est. Monthly Cost |
|---|---|---|
| OpenAI GPT-4o | ~150 API calls/day | ~$45 |
| ElevenLabs | ~6 voice generations/day | ~$22 |
| OpenAI Whisper | ~6 transcriptions/day | ~$5 |
| AWS ECS Fargate | 2 tasks (API + worker) | ~$40 |
| AWS S3 | ~50GB storage + transfer | ~$10 |
| AWS RDS (t3.micro) | PostgreSQL | ~$15 |
| ElastiCache (t3.micro) | Redis | ~$15 |
| **Total** | | **~$150/month** |

---

### Risk Register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Enhancement models too slow for 5-6/day | Medium | Run on GPU ECS task, or skip enhancement for fast jobs |
| CLIP matching quality poor | Medium | GPT-4o fallback + human review queue |
| ElevenLabs voice sounds unnatural | Low | Per-brand voice cloning (ElevenLabs supports this) |
| FFmpeg assembly audio sync drift | Medium | Always re-mux audio last, use `-async 1` flag |
| Editor team resists AI workflow | Medium | Position AI as "assistant", not replacement. Editors review before publish |

---

## Quick Reference — Claude Model Recommendations

| Task | Best Claude Model | Why |
|---|---|---|
| Code generation (agents, API) | Claude Sonnet 4.6 | Best code quality |
| Script writing / hooks | Claude Sonnet 4.6 | Creative + fast |
| Scene scoring / analysis | Claude Sonnet 4.6 | Vision capable |
| Batch planning (many reels) | Claude Haiku 4.5 | Fast + cheap for repetitive tasks |
| Architecture decisions | Claude Sonnet 4.6 | Reasoning depth |
