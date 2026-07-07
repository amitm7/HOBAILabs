# HOBAILabs — single-container image (Flask + FFmpeg + gunicorn)
FROM python:3.12-slim

# System deps:
#   ffmpeg            — clip normalization / assembly
#   fonts-liberation  — a serif caption face (macOS Baskerville is absent on Linux)
#   fontconfig        — fc-cache so libass finds the bundled caption fonts by name
#   openssl           — dev-fallback C2PA signing chain (agents/content_credential)
#   libheif/PIL come from the pillow-heif wheel, so no apt libheif needed
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-liberation fontconfig openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Caption fonts: install the bundled TTFs (Montserrat OFL + Satoshi by Indian Type
# Foundry / Fontshare, free for commercial use) so libass can render them by family
# name. fc-cache registers them with fontconfig. Add another font = drop its TTF here.
COPY deploy/fonts/ /usr/share/fonts/truetype/hob/
RUN fc-cache -f

# Install deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code. CACHEBUST changes every deploy (prod.sh passes the timestamped image
# tag) so this COPY always re-copies current source — the pip layer above stays
# cached, but docs/templates/code never go stale in the image.
ARG CACHEBUST=0
COPY . .

# Caches/outputs are written under $HOME/.hob_cache and the system temp dir.
# Mount a persistent volume at /data and point HOME there so caches survive
# container restarts (see deploy/README.md).
ENV HOME=/data

# Caption rendering on Linux: point libass at the installed fonts and use a face
# that exists in the image. For brand-exact Baskerville, drop a TTF into
# deploy/fonts/, COPY it to /app/fonts, and set HOB_FONT_DIR=/app/fonts +
# HOB_CAPTION_FONT="Libre Baskerville".
ENV HOB_FONT_DIR=/usr/share/fonts \
    HOB_CAPTION_FONT="Liberation Serif"

EXPOSE 7860

# 1 worker keeps the in-memory run-state (_runs) coherent; threads give
# concurrency; -t 0 disables the worker timeout so long renders aren't killed.
CMD ["gunicorn", "-w", "1", "--threads", "8", "-t", "0", "-b", "0.0.0.0:7860", "web_app:app"]
