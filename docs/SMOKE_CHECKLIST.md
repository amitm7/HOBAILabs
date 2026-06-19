# HOBAILabs Smoke Checklist

Run this before and after each governed-roadmap slice.

## Static Checks

```bash
~/.pyenv/versions/3.12.3/bin/python3.12 -m py_compile agents/*.py web_app.py
node --check web/static/main.js
node --check web/static/brand.js
~/.pyenv/versions/3.12.3/bin/python3.12 -m unittest tests.test_core_behaviour
```

## Offline Route Checks

- `/parse-script` extracts frames and the `Caption:` posting block.
- `/posting-kit` returns story-mode caption, hashtags, and cover frame.
- Brand `/run` blocks when logo, CTA, product beat, or rights confirmation is missing.
- `/api/estimate` returns server-side cost totals.

## Manual UI Checks

- Parse a short story script.
- Confirm the read-only timeline strip appears above frame cards.
- Preview stills, redo one still, and confirm approvals still update the estimate.
- Select a `Text Card` frame and preview it.
- Generate a cheap Dev/Ken Burns render and confirm the output video shows the Reels safe-zone overlay.
- Download the MP4 and the clips + edit-list export.

## Boundaries

- AI-generated posting copy is story-mode only.
- Brand claims, CTA, captions, and announcer copy remain operator-supplied.
- Any paid/external/real-person growth feature must pass consent and spend governance first.
