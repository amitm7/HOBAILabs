---
name: ai-reel-generator
description: >
  Use this skill whenever the task involves generating, editing, or automating
  social media video reels (Instagram, YouTube Shorts, Facebook Reels, LinkedIn Video)
  from raw footage, images, and scripts. Triggers include: "create a reel", "automate
  video editing", "generate short from footage", "add captions to video", "AI video
  pipeline", "batch reel generation", "enhance video lighting/bokeh", "sync cuts to
  beat", or any task combining script + media → social video output. Also use when
  building or extending the reel generation platform codebase (agents, FFmpeg pipeline,
  enhancement models, FastAPI workers). Do NOT use for live streaming, long-form video
  editing (>10 min), or tasks unrelated to short-form social video.
---

# AI Reel Generator Skill

This skill guides the creation and automation of short-form social media video reels
from raw inputs: footage, images, and scripts. It covers the full pipeline from media
ingestion to platform-ready output, including AI enhancement, intelligent editing,
caption generation, and brand layer application.

The user provides: raw video files, images, a script or brief, target platform,
duration, and brand guidelines. Output is one or more platform-formatted reels.

---

## Pipeline Mental Model

Always think in this sequence before writing any code or prompt:

```
RAW INPUTS
(footage + images + script)
        ↓
[Agent 1] SCENE ANALYSIS       — what is usable, what is not
        ↓
[Agent 2] SCRIPT SEGMENTATION  — map script to timeline
        ↓
[Agent 3] MEDIA MATCHING       — assign best clip/image to each segment
        ↓
[Agent 4] ENHANCEMENT          — fix lighting, bokeh, upscale
        ↓
[Agent 5] ASSEMBLY             — FFmpeg: cut + transition + voice + music
        ↓
[Agent 6] CAPTION + BRAND      — Whisper SRT + brand fonts/colors/outro
        ↓
PLATFORM OUTPUT
(9:16 MP4 per platform)
```

Never skip or reorder agents. Each agent's output is the next agent's input.

---

## Agent Definitions

### Agent 1 — Scene Analyzer
**Purpose**: Ingest raw footage and identify usable scenes.

**Tools**:
- `PySceneDetect` — detect scene cuts, motion intensity
- `OpenCV` — face detection, blur detection (discard blurry frames)
- `GPT-4o` — score each scene: energy, facial expression, visual quality

**Output JSON**:
```json
{
  "scenes": [
    {
      "id": "s1",
      "source_file": "video1.mp4",
      "start_ms": 0,
      "end_ms": 4200,
      "score": 8.4,
      "tags": ["face", "high_energy", "sharp"],
      "usable": true
    }
  ]
}
```

**Prompt template for GPT scoring**:
```
Given this scene description: {scene_metadata}
Rate it 1-10 for use in a {style} reel targeting {audience}.
Score on: energy, visual clarity, emotional impact, face presence.
Return JSON only: {"score": X, "tags": [], "reason": ""}
```

---

### Agent 2 — Script Segmenter
**Purpose**: Split script into timed segments aligned to target duration.

**Tools**: GPT-4o

**Prompt template**:
```
Split this script into segments for a {duration}-second {platform} reel.
Style: {style}  Audience: {audience}

Rules:
- First segment must be a hook (0-3 seconds, maximum attention)
- Each segment: 3-7 seconds
- Last segment: CTA or brand sign-off
- Return ONLY JSON, no markdown

Script: {script}

Return:
[
  {"id": "seg1", "text": "...", "start_sec": 0, "end_sec": 4, "type": "hook"},
  {"id": "seg2", "text": "...", "start_sec": 4, "end_sec": 10, "type": "body"},
  ...
]
```

---

### Agent 3 — Media Matcher
**Purpose**: Assign best available clip or image to each script segment.

**Tools**:
- `CLIP (OpenAI)` — semantic similarity between segment text and media
- `GPT-4o` fallback — when CLIP score < 0.6, ask GPT to choose from asset list

**Logic**:
```python
for segment in segments:
    scores = clip_score(segment.text, available_media)
    best = max(scores, key=lambda x: x.score)
    if best.score < 0.6:
        best = gpt_fallback_select(segment, asset_descriptions)
    segment.assigned_media = best.asset_id
```

**Never** assign the same clip to two consecutive segments unless it is trimmed
to a different portion. Always trim to segment duration ± 0.5s.

---

### Agent 4 — Enhancement Engine
**Purpose**: Improve visual quality of selected clips/images before assembly.

**Enhancements (apply selectively, not blindly)**:

| Issue | Tool | Command / Model |
|---|---|---|
| Dark / poor lighting | Zero-DCE or FFmpeg eq | `ffmpeg -vf eq=brightness=0.1:contrast=1.2` |
| Wants more bokeh | MediaPipe Selfie Seg + blur BG | Segment subject, gaussian blur background |
| Low resolution | Real-ESRGAN | `realesrgan-ncnn-vulkan -i in.mp4 -o out.mp4` |
| Shaky footage | FFmpeg vidstabdetect | Two-pass stabilization |
| Wrong aspect ratio | FFmpeg crop + scale | `ffmpeg -vf "crop=ih*9/16:ih,scale=1080:1920"` |

**Rule**: Only enhance if the scene score from Agent 1 < 7.0. High-quality footage
needs no processing — over-processing degrades quality.

---

### Agent 5 — Assembler
**Purpose**: Combine all segments into a single video with voice, transitions, music.

**Stack**: FFmpeg (primary), MoviePy (for complex operations)

**Voice generation**:
```python
# ElevenLabs (preferred for quality)
voice = elevenlabs.generate(text=segment.text, voice=brand.voice_id)

# OpenAI TTS (fallback, faster/cheaper)
voice = openai.audio.speech.create(model="tts-1-hd", voice="nova", input=segment.text)
```

**Beat-synced cuts** (when music is enabled):
```python
import librosa
y, sr = librosa.load(music_file)
beat_times = librosa.beat.beat_track(y=y, sr=sr)[1]
beat_times_sec = librosa.frames_to_time(beat_times, sr=sr)
# Align segment cut points to nearest beat
```

**FFmpeg assembly template**:
```bash
# Concat all processed segments
ffmpeg \
  -i seg1.mp4 -i seg2.mp4 -i seg3.mp4 \
  -i voice.mp3 \
  -i music.mp3 \
  -filter_complex "
    [0:v][1:v]xfade=transition=fade:duration=0.3:offset=4.0[v01];
    [v01][2:v]xfade=transition=fade:duration=0.3:offset=9.5[vout];
    [3:a][4:a]amix=inputs=2:weights=1 0.15[aout]
  " \
  -map "[vout]" -map "[aout]" \
  -c:v libx264 -crf 18 -preset fast \
  -c:a aac -b:a 192k \
  -r 30 -t {duration} \
  output_raw.mp4
```

---

### Agent 6 — Caption & Brand Layer
**Purpose**: Add captions, brand identity, and platform formatting.

**Caption generation**:
```python
import whisper
model = whisper.load_model("base")
result = model.transcribe("voice.mp3", word_timestamps=True)
# Generate .srt from result["segments"]
```

**Caption style** (for personal brand / influencer):
- Font: Bold, ALL CAPS, large (min 72px at 1080p)
- Position: Lower third or center
- Color: White text + black stroke OR brand primary color
- Max 3 words per caption frame — never full sentences on screen

**Brand layer FFmpeg**:
```bash
ffmpeg -i output_raw.mp4 \
  -i brand_intro.mp4 -i brand_outro.mp4 \
  -vf "subtitles=captions.srt:force_style='FontName=Montserrat-Bold,
       FontSize=22,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,
       Outline=3,Alignment=2'" \
  -filter_complex "[1:v][0:v][2:v]concat=n=3:v=1:a=0[vfinal]" \
  final_reel.mp4
```

**Platform export formats**:
```python
PLATFORM_SPECS = {
    "instagram_reel": {"w": 1080, "h": 1920, "fps": 30, "max_sec": 90},
    "youtube_short":  {"w": 1080, "h": 1920, "fps": 60, "max_sec": 60},
    "facebook_reel":  {"w": 1080, "h": 1920, "fps": 30, "max_sec": 90},
    "linkedin_video": {"w": 1080, "h": 1920, "fps": 30, "max_sec": 60},
}
```

---

## LangGraph Wiring

```python
from langgraph.graph import StateGraph

workflow = StateGraph(ReelState)
workflow.add_node("scene_analyzer",   scene_analyzer_agent)
workflow.add_node("script_segmenter", script_segmenter_agent)
workflow.add_node("media_matcher",    media_matcher_agent)
workflow.add_node("enhancer",         enhancement_agent)
workflow.add_node("assembler",        assembler_agent)
workflow.add_node("brand_layer",      brand_agent)

workflow.set_entry_point("scene_analyzer")
workflow.add_edge("scene_analyzer",   "script_segmenter")
workflow.add_edge("script_segmenter", "media_matcher")
workflow.add_edge("media_matcher",    "enhancer")
workflow.add_edge("enhancer",         "assembler")
workflow.add_edge("assembler",        "brand_layer")
workflow.set_finish_point("brand_layer")

app = workflow.compile()
```

---

## State Schema

```python
from pydantic import BaseModel
from typing import List, Optional

class ReelState(BaseModel):
    # Inputs
    script: str
    media_folder: str
    brand_id: str
    target_platform: str
    target_duration_sec: int
    style: str  # motivational | educational | product | storytelling

    # Agent outputs (populated progressively)
    scenes: Optional[List[dict]] = None
    segments: Optional[List[dict]] = None
    media_assignments: Optional[dict] = None
    enhanced_clips: Optional[List[str]] = None
    raw_assembly_path: Optional[str] = None
    final_reel_path: Optional[str] = None
    error: Optional[str] = None
```

---

## Error Handling Rules

- If `scene_analyzer` finds < 3 usable scenes → pause, request more footage
- If `media_matcher` CLIP score < 0.4 for any segment → flag for human review
- If `assembler` output duration deviates > 3s from target → re-trim and retry once
- If any agent fails → log full state to S3, alert via webhook, do NOT silently skip

---

## Quality Checklist (before marking a reel complete)

- [ ] Hook is in first 3 seconds
- [ ] No segment longer than 8 seconds
- [ ] Captions readable on mobile (test at 375px width)
- [ ] Voice and music levels balanced (voice -3dB, music -18dB)
- [ ] Brand intro ≤ 2 seconds, brand outro ≤ 3 seconds
- [ ] Correct aspect ratio (9:16) verified
- [ ] No jump cuts without transition
- [ ] CTA present in final segment
