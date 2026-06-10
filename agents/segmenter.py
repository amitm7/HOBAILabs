from agents import llm

WORDS_PER_SEC = 2.5  # natural speaking pace


def _paragraph_split(script: str, target_duration_sec: int) -> list[dict]:
    """Split on blank lines — works when script is already frame-structured."""
    paras = [p.strip() for p in script.strip().split("\n\n") if p.strip()]
    total_words = sum(len(p.split()) for p in paras)
    segments = []
    for i, text in enumerate(paras):
        estimated = round(len(text.split()) / WORDS_PER_SEC, 1)
        segments.append({"id": f"s{i+1}", "text": text, "duration_sec": estimated})
    return segments


def _ai_split(script: str, target_duration_sec: int) -> list[dict]:
    """Use GPT to split a single-block script into segments."""
    prompt = f"""Split this script into segments for a {target_duration_sec}-second video.

Rules:
- Each segment 3-8 seconds at natural pace (~2.5 words/sec)
- First segment: strong hook
- Last segment: clear closing
- Total duration_sec ≈ {target_duration_sec}
- Return ONLY a valid JSON array, no markdown

Script:
{script}

Format:
[{{"id": "s1", "text": "...", "duration_sec": 4}}, ...]"""

    raw = llm.chat([{"role": "user", "content": prompt}],
                   temperature=0.3, model_tier="reasoning")
    return llm.json_loads_lenient(raw)


def segment_script(script: str, target_duration_sec: int) -> list[dict]:
    """Split script into timed segments. Uses paragraph breaks if present, AI otherwise."""
    paras = [p.strip() for p in script.strip().split("\n\n") if p.strip()]

    if len(paras) > 1:
        segments = _paragraph_split(script, target_duration_sec)
        print(f"[Segmenter] {len(segments)} segments (paragraph split — no API needed)")
    else:
        print("[Segmenter] Single block detected — using AI to split...")
        segments = _ai_split(script, target_duration_sec)
        print(f"[Segmenter] {len(segments)} segments (AI split)")

    for seg in segments:
        print(f"  {seg['id']} ({seg['duration_sec']}s): {seg['text'][:60]}...")

    return segments
