# HOBAILabs

AI pipeline for turning story scripts into short-form social reels (9:16), with a Flask web UI and modular Python agents.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Add API keys to .env
```

## Run

```bash
python web_app.py
# Open http://localhost:7860
```

## CLI (legacy pipeline)

```bash
python main.py --script script.txt --assets assets --duration 60
```

Place photos/videos in `assets/` (or use the web UI assets folder path). See `.env.example` for required API keys.
