"""
fal.ai image-to-video provider wrapper (Seedance, Veo, Hailuo, …).

Mirrors the agents/higgsfield.py interface (submit → poll_and_download) so
clip_builder can treat any fal-hosted video model as a deferred provider, using
the same throttled polling pool.

The actual model is chosen by the router; the fal endpoint comes from
config/models.json (model_router.model_field(model_id, "fal_endpoint")).

⚠ fal video models differ in the argument keys they accept (duration format,
aspect, end-frame). The minimal {image_url, prompt} works broadly; duration and
aspect_ratio are included as common extras — VERIFY each model on fal.ai.
"""

from agents import fal_client, model_router

DEFAULT_MOTION = "Cinematic, subtle natural motion, slow gentle camera, one action, emotional depth"


def submit(model_id: str, image_path: str, motion_prompt: str = "",
           aspect_ratio: str = "9:16", duration: int = 5,
           end_image_path: str = "") -> dict:
    """
    Upload the start frame (as a data URI) and queue a fal video job.
    Returns the fal queue handle (dict) — non-blocking. Pass it to
    poll_and_download(). Optional end_image_path enables start+end-frame motion
    on models that support it.
    """
    endpoint = model_router.model_field(model_id, "fal_endpoint")
    if not endpoint:
        raise RuntimeError(f"no fal_endpoint configured for video model '{model_id}'")

    args = {
        "image_url":    fal_client.file_to_data_uri(image_path),
        "prompt":       motion_prompt or DEFAULT_MOTION,
        "duration":     int(duration),
        "aspect_ratio": aspect_ratio,
    }
    if end_image_path:
        args["end_image_url"] = fal_client.file_to_data_uri(end_image_path)

    handle = fal_client.submit(endpoint, args)
    handle["model_id"] = model_id
    print(f"[fal:{model_id}] Submitted → {handle.get('request_id')}")
    return handle


def poll_and_download(handle: dict, output_path: str) -> str:
    """Poll the fal queue until done, download the video to output_path."""
    result = fal_client.poll_result(handle, timeout=600)
    url = fal_client.extract_media_url(result, keys=("video",))
    if not url:
        raise RuntimeError(f"fal video {handle.get('model_id')} returned no video: {str(result)[:200]}")
    fal_client.download_media(url, output_path)
    print(f"[fal:{handle.get('model_id')}] ✓ {handle.get('request_id')} → {output_path}")
    return output_path
