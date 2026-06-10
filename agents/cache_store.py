"""
Blob cache backend — content-addressed large artifacts (animated clips,
lip-sync clips, lip-sync audio).

Why this exists: every paid clip lives under ~/.hob_cache/<namespace>/, which is
INSIDE the container in a cloud deploy. A redeploy wipes it and a second instance
shares nothing — re-spending Kling/fal/Hedra credits and defeating the whole
"never re-bill identical content" principle.

This module keeps the local filesystem cache as a fast read-through layer and
optionally backs it with object storage (S3) so paid blobs survive restarts and
are shared across instances. Selected by env:

    HOB_CACHE_BACKEND = fs (default) | s3
    HOB_CACHE_DIR     = local cache root         (default: ~/.hob_cache)
    HOB_CACHE_S3_BUCKET = <bucket>               (required for s3)
    HOB_CACHE_S3_PREFIX = hob_cache (default key prefix)

Blobs are content-addressed (MD5 keys) → never stale, safe to share. Object keys
are the SAME hashes the callers already compute, so switching backends does not
change cache identity (previously-paid clips still hit).

For small TEXT values (descriptions, run metadata) use agents/_kv.py instead —
a database/SQLite is the wrong tool for MP4s, and the filesystem is the wrong
tool for queryable state.
"""

import os
import shutil
from pathlib import Path

_BACKEND   = (os.environ.get("HOB_CACHE_BACKEND") or "fs").lower()
_CACHE_ROOT = Path(os.environ.get("HOB_CACHE_DIR") or (Path.home() / ".hob_cache"))
_S3_BUCKET = os.environ.get("HOB_CACHE_S3_BUCKET", "")
_S3_PREFIX = (os.environ.get("HOB_CACHE_S3_PREFIX", "hob_cache") or "hob_cache").strip("/")

_s3_client = None


def s3_enabled() -> bool:
    return _BACKEND == "s3" and bool(_S3_BUCKET)


def _get_s3():
    global _s3_client
    if _s3_client is None:
        import boto3  # lazy — only when an S3 backend is actually configured
        _s3_client = boto3.client("s3")
    return _s3_client


def _s3_download(key: str, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _get_s3().download_file(_S3_BUCKET, key, str(dest))
        return True
    except Exception:
        return False  # miss (or transient error) → treat as cache miss


def _s3_upload(key: str, src: Path) -> bool:
    try:
        _get_s3().upload_file(str(src), _S3_BUCKET, key)
        return True
    except Exception as e:
        print(f"[CacheStore] S3 upload failed for {key} ({e}) — local copy kept")
        return False


class BlobCache:
    """A content-addressed blob cache for one namespace (e.g. 'kling_clips').

    Local FS is always the working store; when the S3 backend is enabled it is a
    read-through cache in front of the bucket. The local-path API preserves the
    callers' existing "get a path, copy it" pattern and keeps warm containers fast.
    """

    def __init__(self, namespace: str, ext: str = ".mp4", min_bytes: int = 10_000):
        self.namespace = namespace
        self.ext = ext
        self.min_bytes = min_bytes
        self.local_dir = _CACHE_ROOT / namespace
        self.local_dir.mkdir(parents=True, exist_ok=True)

    # ── key → location helpers ────────────────────────────────────────────────
    def _local(self, key: str) -> Path:
        return self.local_dir / f"{key}{self.ext}"

    def _s3_key(self, key: str) -> str:
        return f"{_S3_PREFIX}/{self.namespace}/{key}{self.ext}"

    def _valid(self, p: Path) -> bool:
        return p.exists() and p.stat().st_size > self.min_bytes

    # ── public API ────────────────────────────────────────────────────────────
    def local_path(self, key: str) -> str | None:
        """Return a local path to the cached blob, fetching from S3 on a local
        miss; or None if not cached anywhere."""
        p = self._local(key)
        if self._valid(p):
            return str(p)
        if s3_enabled() and _s3_download(self._s3_key(key), p) and self._valid(p):
            print(f"[CacheStore] S3 → local read-through: {self.namespace}/{key}")
            return str(p)
        return None

    def has(self, key: str) -> bool:
        return self.local_path(key) is not None

    def get(self, key: str, dest_path: str) -> bool:
        """If cached, materialize the blob at dest_path and return True."""
        src = self.local_path(key)
        if not src:
            return False
        if os.path.abspath(src) != os.path.abspath(dest_path):
            shutil.copy2(src, dest_path)
        return True

    def put(self, key: str, src_path: str) -> str:
        """Store src_path under key (local always; S3 too when enabled).
        Returns the canonical local cache path."""
        dest = self._local(key)
        if os.path.abspath(src_path) != os.path.abspath(dest):
            shutil.copy2(src_path, dest)
        if s3_enabled():
            _s3_upload(self._s3_key(key), dest)
        return str(dest)
