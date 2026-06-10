# Exemplars — teaching the AI your lab's hand-made style

These are **gold examples** from past manually-edited projects. The pipeline reads
them as **few-shot guidance** for the LLM "brain" (scene design + image matching),
so automated output imitates your lab's taste — pacing, shot grammar, which media
goes on which beat, and why. This is *not* model training; it's in-context examples.

> Enable with `USE_EXEMPLARS=1` (env) or the UI toggle. When off (default), the
> pipeline behaves exactly as before.

## One folder per past project

```
exemplars/
  <project_name>/
    script.txt        # the original script (frames + captions)
    assets/           # the source images & clips that project used
    final.mp4         # your manually-edited final video (the "gold" output)
    exemplar.json     # the DISTILLED DECISIONS — the part the pipeline reads
```

**Which artifact matters:** the pipeline consumes **`exemplar.json`**. `final.mp4`
and `assets/` are kept for reference, eval/benchmarking, and future auto-extraction
— a whole MP4 can't be fed to the LLM usefully. The *decisions* in the JSON are the
teachable lesson.

## How to author `exemplar.json`
Copy `_template/exemplar.json` into your project folder and fill it in from what you
(the editor) actually decided. The key fields per frame are **`media`**, **`why_this_media`**,
**`shot_type`**, **`motion`**, **`duration_sec`**, **`emotion`** — plus the global
**`style`** block. 3–10 strong, diverse exemplars is plenty.

| Field | What to put |
|---|---|
| `style.mood / palette / pacing / camera / transitions / captions / music` | The house look & rhythm for this project |
| `frames[].caption` | The on-screen / narration line for the beat |
| `frames[].media` + `media_type` | Filename used + `image` or `video` |
| `frames[].shot_type` | establishing / wide / close / detail / payoff |
| `frames[].why_this_media` | **The reasoning** — why this shot for this beat (most valuable) |
| `frames[].motion` | Camera/animation, or "real footage (no animation)" |
| `frames[].duration_sec` | How long the shot held |
| `frames[].emotion` | The beat's feeling |

If you only have the final MP4 + script (no shot list), we can semi-auto extract
durations/cuts/captions later; for now hand-author the few best ones.
