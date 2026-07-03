"""
Pluggable LLM "brain" — one entry point for every reasoning/vision call.

Replaces hardcoded OpenAI usage in scene_intelligence, image_matcher and
segmenter so the brain can run on OpenAI (default), the direct Anthropic API (Claude),
Amazon Bedrock (Claude, IAM-funded) or Gemini — selected by env LLM_PROVIDER or
config/llm.json, WITHOUT changing the default. Image generation and audio are separate axes and
are NOT routed here.

Public API:
    chat(messages, *, json_mode=False, max_tokens=1024, temperature=None,
         model_tier="reasoning", json_schema=None) -> str
    json_loads_lenient(text) -> obj    # tolerant JSON parse (strips code fences)

Model tiers: "reasoning" (default), "vision", "fast" (cheap vision-capable model
for bulk describe/QC calls). Unknown tiers fall back to "reasoning".
json_schema: optional dict {"name": str, "schema": {...}} — enforced strictly on
OpenAI (structured outputs); injected as a system directive on Bedrock/Gemini.

Message format (provider-agnostic):
    messages = [{"role": "system"|"user"|"assistant", "content": <str | parts>}]
    parts    = [{"type": "text", "text": "..."},
                {"type": "image", "path": "/abs.jpg"} | {"type": "image", "data_uri": "data:..."}]
"""

import base64
import json
import mimetypes
import os
from pathlib import Path

_CONFIG = None
_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "llm.json")


# ── Config ────────────────────────────────────────────────────────────────────

def _config() -> dict:
    global _CONFIG
    if _CONFIG is None:
        try:
            with open(os.path.abspath(_CONFIG_PATH)) as f:
                _CONFIG = json.load(f)
        except Exception:
            _CONFIG = {"provider": "openai",
                       "openai": {"reasoning": "gpt-4.1", "vision": "gpt-4o"}}
    return _CONFIG


def _provider() -> str:
    return (os.environ.get("LLM_PROVIDER") or _config().get("provider") or "openai").lower()


# Clients are expensive to build (TLS pools, botocore service models) and chat()
# is called per frame/image — construct once per process and reuse.
_CLIENTS: dict = {}


def _openai_client():
    if "openai" not in _CLIENTS:
        from openai import OpenAI
        _CLIENTS["openai"] = OpenAI()
    return _CLIENTS["openai"]


def _bedrock_client(region: str):
    key = f"bedrock:{region}"
    if key not in _CLIENTS:
        import boto3
        _CLIENTS[key] = boto3.client("bedrock-runtime", region_name=region)
    return _CLIENTS[key]


def _anthropic_client():
    # Direct Anthropic API (api.anthropic.com) — reads ANTHROPIC_API_KEY from env.
    # Independent of Bedrock/Marketplace; use when Claude isn't entitled on Bedrock.
    if "anthropic" not in _CLIENTS:
        import anthropic
        _CLIENTS["anthropic"] = anthropic.Anthropic()
    return _CLIENTS["anthropic"]


def _model_for(provider: str, tier: str) -> str:
    env = os.environ.get(f"LLM_{tier.upper()}_MODEL")
    if env:
        return env
    return (_config().get(provider, {}) or {}).get(tier) \
        or (_config().get(provider, {}) or {}).get("reasoning", "")


# ── Helpers ───────────────────────────────────────────────────────────────────

def json_loads_lenient(text: str):
    """Parse JSON, tolerating ```json fences / surrounding prose."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
        s = s.strip()
    try:
        return json.loads(s)
    except Exception:
        # last resort: slice the outermost { } or [ ]
        for open_c, close_c in (("{", "}"), ("[", "]")):
            i, j = s.find(open_c), s.rfind(close_c)
            if 0 <= i < j:
                return json.loads(s[i:j + 1])
        raise


def _image_bytes_and_format(part: dict) -> tuple[bytes, str]:
    if part.get("data_uri"):
        header, b64 = part["data_uri"].split(",", 1)
        fmt = "png" if "png" in header else "jpeg"
        return base64.b64decode(b64), fmt
    path = part["path"]
    raw = Path(path).read_bytes()
    # Sniff the REAL format from magic bytes — generators regularly write PNG/WebP
    # bytes into ".jpg" paths, and a wrong declared media type makes strict vision
    # APIs reject the request (Gate B2 was silently skipping every QC because of it).
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        fmt = "png"
    elif raw[:2] == b"\xff\xd8":
        fmt = "jpeg"
    elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        fmt = "webp"
    elif raw[:6] in (b"GIF87a", b"GIF89a"):
        fmt = "gif"
    else:  # unknown magic — fall back to the extension guess
        fmt = (mimetypes.guess_type(path)[0] or "image/jpeg").split("/")[-1]
        fmt = "jpeg" if fmt in ("jpg", "jpeg") else fmt
    return raw, fmt


def _data_uri(part: dict) -> str:
    if part.get("data_uri"):
        return part["data_uri"]
    raw, fmt = _image_bytes_and_format(part)
    return f"data:image/{fmt};base64,{base64.b64encode(raw).decode('ascii')}"


def _norm_content(content):
    """Return content as a list of parts (text/image)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content


# ── Public entry point ────────────────────────────────────────────────────────

def chat(messages, *, json_mode: bool = False, max_tokens: int = 1024,
         temperature: float | None = None, model_tier: str = "reasoning",
         json_schema: dict | None = None) -> str:
    """Run a chat/vision completion on the configured provider. Returns text."""
    provider = _provider()
    model = _model_for(provider, model_tier)
    if provider == "openai":
        return _openai_chat(messages, model, json_mode, max_tokens, temperature, json_schema)
    if provider == "bedrock":
        return _bedrock_chat(messages, model, json_mode, max_tokens, temperature, json_schema)
    if provider == "anthropic":
        return _anthropic_chat(messages, model, json_mode, max_tokens, temperature, json_schema)
    if provider == "gemini":
        return _gemini_chat(messages, model, json_mode, max_tokens, temperature, json_schema)
    raise RuntimeError(f"Unknown LLM_PROVIDER '{provider}' (use openai|anthropic|bedrock|gemini)")


# ── OpenAI backend (default; behaviour-identical to prior code) ───────────────

def _openai_chat(messages, model, json_mode, max_tokens, temperature,
                 json_schema=None) -> str:
    client = _openai_client()

    oai_msgs = []
    for m in messages:
        parts = _norm_content(m["content"])
        if len(parts) == 1 and parts[0]["type"] == "text":
            oai_msgs.append({"role": m["role"], "content": parts[0]["text"]})
            continue
        content = []
        for p in parts:
            if p["type"] == "text":
                content.append({"type": "text", "text": p["text"]})
            elif p["type"] == "image":
                content.append({"type": "image_url",
                                "image_url": {"url": _data_uri(p)}})
        oai_msgs.append({"role": m["role"], "content": content})

    kwargs = {"model": model, "messages": oai_msgs, "max_tokens": max_tokens}
    if json_schema:
        # Structured outputs: the schema is ENFORCED — no truncated/invalid JSON.
        kwargs["response_format"] = {"type": "json_schema", "json_schema": {
            "name": json_schema.get("name", "response"),
            "schema": json_schema["schema"],
            "strict": True,
        }}
    elif json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if temperature is not None:
        kwargs["temperature"] = temperature
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content


# ── Bedrock backend (Claude via Converse; IAM, no API key) ────────────────────

def _bedrock_chat(messages, model, json_mode, max_tokens, temperature,
                  json_schema=None) -> str:
    # Bedrock region is decoupled from the app's AWS_REGION: Claude may not be
    # available where the app runs (e.g. ap-south-1). Precedence:
    #   BEDROCK_REGION env  >  config/llm.json bedrock.region  >  AWS_REGION  >  us-east-1
    region = (os.environ.get("BEDROCK_REGION")
              or (_config().get("bedrock", {}) or {}).get("region")
              or os.environ.get("AWS_REGION", "us-east-1"))
    client = _bedrock_client(region)

    # Converse: system messages go in a separate `system` list; messages only
    # carry user/assistant roles with content blocks.
    system_blocks, conv_msgs = [], []
    for m in messages:
        parts = _norm_content(m["content"])
        if m["role"] == "system":
            for p in parts:
                if p["type"] == "text":
                    system_blocks.append({"text": p["text"]})
            continue
        blocks = []
        for p in parts:
            if p["type"] == "text":
                blocks.append({"text": p["text"]})
            elif p["type"] == "image":
                raw, fmt = _image_bytes_and_format(p)
                blocks.append({"image": {"format": fmt, "source": {"bytes": raw}}})
        conv_msgs.append({"role": m["role"], "content": blocks})

    # Claude has no response_format — instruct JSON via a system directive.
    if json_schema:
        system_blocks.append({"text": "Respond with ONLY valid JSON matching exactly this "
                                      f"JSON Schema (no prose, no code fences):\n"
                                      f"{json.dumps(json_schema['schema'])}"})
    elif json_mode:
        system_blocks.append({"text": "Respond with ONLY valid JSON. No prose, no code fences."})

    inference = {"maxTokens": max_tokens}
    if temperature is not None:
        inference["temperature"] = temperature

    resp = client.converse(
        modelId=model,
        messages=conv_msgs,
        system=system_blocks or [{"text": "You are a helpful assistant."}],
        inferenceConfig=inference,
    )
    return resp["output"]["message"]["content"][0]["text"]


# ── Anthropic backend (direct API; ANTHROPIC_API_KEY, no Bedrock/Marketplace) ──

# Opus 4.7/4.8, Fable 5 and Mythos reject sampling params (temperature/top_p) with
# a 400 — only forward temperature to models that accept it (e.g. Sonnet/Haiku).
_ANTHROPIC_NO_TEMP = ("claude-opus-4-8", "claude-opus-4-7",
                      "claude-fable", "claude-mythos")


def _anthropic_chat(messages, model, json_mode, max_tokens, temperature,
                    json_schema=None) -> str:
    client = _anthropic_client()

    # Anthropic Messages API: system is a top-level param; messages carry only
    # user/assistant roles with typed content blocks.
    system_texts, conv_msgs = [], []
    for m in messages:
        parts = _norm_content(m["content"])
        if m["role"] == "system":
            system_texts += [p["text"] for p in parts if p["type"] == "text"]
            continue
        blocks = []
        for p in parts:
            if p["type"] == "text":
                blocks.append({"type": "text", "text": p["text"]})
            elif p["type"] == "image":
                raw, fmt = _image_bytes_and_format(p)
                blocks.append({"type": "image", "source": {
                    "type": "base64",
                    "media_type": f"image/{fmt}",
                    "data": base64.b64encode(raw).decode("ascii"),
                }})
        conv_msgs.append({"role": m["role"], "content": blocks})

    # Claude has no response_format — instruct JSON via the system prompt, same as
    # the Bedrock path. json_loads_lenient() on the caller side tolerates fences.
    if json_schema:
        system_texts.append("Respond with ONLY valid JSON matching exactly this "
                            f"JSON Schema (no prose, no code fences):\n"
                            f"{json.dumps(json_schema['schema'])}")
    elif json_mode:
        system_texts.append("Respond with ONLY valid JSON. No prose, no code fences.")

    kwargs = {"model": model, "max_tokens": max_tokens, "messages": conv_msgs}
    if system_texts:
        kwargs["system"] = "\n".join(system_texts)
    if temperature is not None and not any(model.startswith(p) for p in _ANTHROPIC_NO_TEMP):
        kwargs["temperature"] = temperature

    resp = client.messages.create(**kwargs)
    # content is a list of blocks; concatenate the text blocks.
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


# ── Gemini backend ────────────────────────────────────────────────────────────

def _gemini_chat(messages, model, json_mode, max_tokens, temperature,
                 json_schema=None) -> str:
    import google.generativeai as genai
    from PIL import Image
    import io

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    system_text = "\n".join(
        p["text"] for m in messages if m["role"] == "system"
        for p in _norm_content(m["content"]) if p["type"] == "text"
    )
    gmodel = genai.GenerativeModel(model, system_instruction=system_text or None)

    parts = []
    for m in messages:
        if m["role"] == "system":
            continue
        for p in _norm_content(m["content"]):
            if p["type"] == "text":
                parts.append(p["text"])
            elif p["type"] == "image":
                raw, _ = _image_bytes_and_format(p)
                parts.append(Image.open(io.BytesIO(raw)))

    gen_cfg = {"max_output_tokens": max_tokens}
    if temperature is not None:
        gen_cfg["temperature"] = temperature
    if json_mode or json_schema:
        gen_cfg["response_mime_type"] = "application/json"
    if json_schema:
        # Gemini's typed response_schema rejects plain JSON Schema dicts on some
        # SDK versions — inject as an instruction instead (mime type still set).
        parts.insert(0, "Respond with ONLY valid JSON matching exactly this JSON Schema:\n"
                        + json.dumps(json_schema["schema"]))

    resp = gmodel.generate_content(parts, generation_config=gen_cfg)
    return resp.text
