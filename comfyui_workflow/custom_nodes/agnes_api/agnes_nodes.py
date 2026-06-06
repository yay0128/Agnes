"""Agnes AI custom nodes for ComfyUI.

Exposes two nodes:
- AgnesTextNode          — expand/enhance a short prompt with Agnes 2.0 Flash
- AgnesVideoGenerateNode — generate a video with Agnes Video V2.0
                            (create task → poll → download to disk)

The API key can be provided via the widget, the AGNES_API_KEY env var, or a
.env file in the ComfyUI installation root / custom-node parent / cwd
(loaded once at import time). Widget value, when non-empty, takes precedence.
"""
from __future__ import annotations

import os
import random
import socket
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import requests


BASE_URL = "https://apihub.agnes-ai.com/v1"
TEXT_MODEL = "agnes-2.0-flash"
VIDEO_MODEL = "agnes-video-v2.0"
ALLOWED_FRAMES = [81, 121, 161, 241, 441]


def _load_dotenv() -> None:
    """Load .env from a few likely locations; populate os.environ if absent.

    Search order (first existing file wins, but existing env vars are
    NEVER overwritten):
    1. <ComfyUI install root>/.env
    2. <custom_nodes>/.env
    3. <this package>/.env
    4. <current working directory>/.env
    """
    try:
        pkg_dir = Path(__file__).resolve().parent
    except NameError:
        pkg_dir = Path.cwd()
    candidates = [
        pkg_dir.parent.parent / ".env",  # ComfyUI install root
        pkg_dir.parent / ".env",         # custom_nodes/.env
        pkg_dir / ".env",                # agnes_api/.env
        Path.cwd() / ".env",
    ]
    for env_path in candidates:
        if not env_path.is_file():
            continue
        try:
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key and key not in os.environ:
                    os.environ[key] = value.strip()
        except OSError:
            continue


_load_dotenv()


def _resolve_key(api_key: str) -> str:
    api_key = (api_key or "").strip()
    if api_key and api_key != "YOUR_API_KEY":
        return api_key
    env = os.environ.get("AGNES_API_KEY", "").strip()
    if not env or env == "YOUR_API_KEY":
        raise ValueError(
            "AGNES_API_KEY missing. Set it on the node widget or export "
            "AGNES_API_KEY in the environment before launching ComfyUI."
        )
    return env


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------
#
# Design goals (per operator feedback):
#   1. Exponential backoff with jitter — first failure waits ~2s, doubles
#      each time, capped at 30s. Random jitter prevents all clients from
#      hammering the server at the same instant when a blip clears.
#   2. Retry only truly recoverable errors. We retry:
#        - Transient network errors (DNS, connection refused, timeouts)
#        - HTTP 429 (upstream saturated — they explicitly want us to back off)
#        - HTTP 5xx (server hiccups, gateway errors)
#      We do NOT retry:
#        - HTTP 400, 401, 403, 404 (client errors — retrying won't help)
#        - 4xx codes in general except 408 (request timeout) and 425 (too early)
#   3. Time-budget aware. 5 attempts with backoff (2, 4, 8, 16, 30) caps
#      total wait at ~60s for POST, ~15s for GET. Bounded so a single bad
#      request can't stall the workflow forever.

# HTTP statuses that mean "the server is having a bad day, try again"
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Network exceptions that mean "we never even got an answer, try again"
_RETRYABLE_NETWORK_EXCEPTIONS = (
    requests.exceptions.ConnectionError,    # DNS, refused, SSLError
    requests.exceptions.Timeout,             # Connect / read timeout
    requests.exceptions.ChunkedEncodingError,  # Stream broke mid-body
)

# Backoff schedule. Index = retry attempt (0-based).
# Capped at 30s so a 5th attempt doesn't wait 5 minutes.
_BACKOFF_BASE = 2.0       # seconds — first retry waits ~2s (was 10s before)
_BACKOFF_FACTOR = 2.0     # exponential growth
_BACKOFF_CAP = 30.0       # max single sleep
_BACKOFF_JITTER = 0.25    # ±25% random jitter


def _is_retryable_exception(exc: BaseException) -> bool:
    """True for transient network errors (DNS, refused, timeout, SSL)."""
    if isinstance(exc, _RETRYABLE_NETWORK_EXCEPTIONS):
        return True
    # urllib3 wraps ConnectionError in MaxRetryError; unwrap one level.
    cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    if cause is not None and isinstance(cause, _RETRYABLE_NETWORK_EXCEPTIONS):
        return True
    return False


def _is_retryable_status(code: int) -> bool:
    """True for statuses that mean 'try again later'."""
    return code in _RETRYABLE_HTTP_STATUSES


def _compute_backoff(attempt: int) -> float:
    """Exponential backoff with ±jitter random noise.

    attempt=0 -> ~2s, attempt=1 -> ~4s, attempt=2 -> ~8s, attempt=3 -> ~16s,
    attempt=4+ -> capped at 30s. Jitter is uniform in [-25%, +25%].

    The jitter matters: if 100 clients all hit a 500 at the same moment,
    without jitter they'd all retry at exactly +2s, +4s, +8s — re-creating
    the thundering herd. Jitter spreads the retries out across time.

    The cap is applied to the *result*, not the base, so the final value
    is always in [22.5s, 30s] for any attempt >= 4.
    """
    base = min(_BACKOFF_BASE * (_BACKOFF_FACTOR ** attempt), _BACKOFF_CAP)
    jitter = base * _BACKOFF_JITTER
    val = base + random.uniform(-jitter, jitter)
    # Hard cap so a high-jitter sample doesn't sneak past 30s.
    return min(val, _BACKOFF_CAP)


def _sleep_backoff(attempt: int, label: str) -> float:
    """Sleep with jittered exponential backoff; return actual time slept."""
    wait = _compute_backoff(attempt)
    print(f"[Agnes] {label}, sleeping {wait:.1f}s (backoff attempt {attempt + 1})")
    time.sleep(wait)
    return wait


# ---------------------------------------------------------------------------
# Robust file download
# ---------------------------------------------------------------------------
#
# Downloading from storage.googleapis.com is unreliable from some networks
# (DNS flakes, transient IPv6 issues, captive portals). We:
#   1. Pre-resolve the host to an IPv4 address up front
#   2. On connection failure, retry with backoff
#   3. On persistent failure, swap the URL to use the resolved IP
#      directly with a Host: header (forces HTTP/1.1, bypasses some
#      resolver-related issues with HTTP/2)
#
# This is overkill for 99%% of the time, but the other 1%% hits a 3-minute
# workflow that fails to save the final MP4. Worth it.


def _resolve_ipv4(host: str) -> str | None:
    """Resolve hostname to an IPv4 address. Returns None on failure."""
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        return infos[0][4][0] if infos else None
    except (socket.gaierror, OSError):
        return None


def _download_with_fallback(
    url: str,
    out_path: str,
    max_attempts: int = 5,
    connect_timeout: float = 30.0,
    read_timeout: float = 300.0,
) -> None:
    """Download a file with full retry + DNS-fallback resilience.

    Differences from a plain requests.get(..., stream=True):
      - 5 attempts (vs 3) with exponential backoff between
      - On DNS resolution failure, retries with the host replaced by
        its pre-resolved IP (forces HTTP/1.1, often bypasses flaky DNS)
      - Logs each attempt with the actual error so you can diagnose

    Raises RuntimeError on final failure.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    scheme = parsed.scheme or "https"
    port = parsed.port or (443 if scheme == "https" else 80)

    # Pre-resolve the IP once. If DNS is down, we still have it for the
    # IP-fallback path.
    ip = _resolve_ipv4(host)

    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        # Try the normal hostname-based request first
        try:
            with requests.get(
                url, stream=True, timeout=(connect_timeout, read_timeout)
            ) as r:
                r.raise_for_status()
                with open(out_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
            return
        except (requests.RequestException, OSError) as e:
            last_exc = e
            err_name = type(e).__name__
            # If it's a network-level failure AND we have an IP, try the
            # IP-fallback path on the next attempt.
            if _is_retryable_exception(e) and ip and attempt < max_attempts - 1:
                print(
                    f"[Agnes] download attempt {attempt + 1}/{max_attempts} "
                    f"failed ({err_name}: {e!s:.80}); "
                    f"will try IP-fallback next"
                )
                _sleep_backoff(attempt, "download network error")
                # Build an IP-based URL with Host header
                ip_url = urlunparse(parsed._replace(netloc=f"{ip}:{port}"))
                try:
                    with requests.get(
                        ip_url,
                        stream=True,
                        timeout=(connect_timeout, read_timeout),
                        headers={"Host": host},
                    ) as r:
                        r.raise_for_status()
                        with open(out_path, "wb") as f:
                            for chunk in r.iter_content(chunk_size=1 << 16):
                                f.write(chunk)
                    print(f"[Agnes] IP-fallback succeeded ({ip})")
                    return
                except (requests.RequestException, OSError) as e2:
                    last_exc = e2
                    print(
                        f"[Agnes] IP-fallback attempt {attempt + 1} failed: "
                        f"{type(e2).__name__}: {e2!s:.80}"
                    )
                    # Clean up partial file
                    if os.path.exists(out_path):
                        try:
                            os.remove(out_path)
                        except OSError:
                            pass
                    if attempt < max_attempts - 1:
                        _sleep_backoff(attempt + 1, "IP-fallback failed")
                    continue
            # Not a network error, or last attempt — bail
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            if not _is_retryable_exception(e):
                raise RuntimeError(
                    f"download failed (non-retryable): {e}"
                ) from e
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt, "download network error")
    # All attempts exhausted
    raise RuntimeError(
        f"download failed after {max_attempts} attempts: {last_exc}"
    ) from last_exc


def _post_with_retry(
    path: str,
    payload: dict[str, Any],
    api_key: str,
    max_attempts: int = 5,
) -> dict[str, Any]:
    """POST with smart retry.

    Retries on:
      - Transient network errors (ConnectionError, Timeout, ChunkedEncodingError)
        — covers NameResolutionError, ConnectionRefusedError, ReadTimeout, SSLError
      - HTTP 408, 425, 429, 500, 502, 503, 504

    Fails fast on:
      - HTTP 400, 401, 403, 404, 422 (client errors — retrying won't help)

    Backoff: exponential with ±25% jitter, capped at 30s per attempt.
    """
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(max_attempts):
        try:
            resp = requests.post(
                f"{BASE_URL}{path}", json=payload, headers=headers, timeout=120
            )
        except requests.RequestException as e:
            if not _is_retryable_exception(e):
                raise RuntimeError(f"POST {path} failed: {e}") from e
            last_exc = e
            print(f"[Agnes] POST {path} network error: {type(e).__name__} "
                  f"({e!s:.80})")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt, f"network error on POST {path}")
            continue

        # Got a response — check status
        if _is_retryable_status(resp.status_code):
            last_status = resp.status_code
            print(f"[Agnes] POST {path} returned {resp.status_code}")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt, f"HTTP {resp.status_code} on POST {path}")
            continue

        if not resp.ok:
            # 4xx other than the retryable set — fail fast
            raise RuntimeError(
                f"POST {path} failed: {resp.status_code} {resp.text}"
            )
        return resp.json()

    # Exhausted retries — produce a clear error
    if last_exc is not None:
        raise RuntimeError(
            f"POST {path} failed after {max_attempts} attempts "
            f"(last network error: {last_exc})"
        ) from last_exc
    raise RuntimeError(
        f"POST {path} failed after {max_attempts} attempts "
        f"(last HTTP status: {last_status})"
    )


def _get_with_retry(
    path: str,
    api_key: str,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """GET with smart retry. Used by the poll loop.

    Same retry logic as POST, but fewer attempts (3) and shorter total
    budget — polling is a tight loop, and we'd rather see a fresh status
    than wait forever on a stale one.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    last_exc: Exception | None = None
    last_status: int | None = None

    for attempt in range(max_attempts):
        try:
            resp = requests.get(
                f"{BASE_URL}{path}", headers=headers, timeout=60
            )
        except requests.RequestException as e:
            if not _is_retryable_exception(e):
                raise RuntimeError(f"GET {path} failed: {e}") from e
            last_exc = e
            print(f"[Agnes] GET {path} network error: {type(e).__name__} "
                  f"({e!s:.80})")
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt, f"network error on GET {path}")
            continue

        if _is_retryable_status(resp.status_code):
            last_status = resp.status_code
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt, f"HTTP {resp.status_code} on GET {path}")
            continue

        if not resp.ok:
            raise RuntimeError(
                f"GET {path} failed: {resp.status_code} {resp.text}"
            )
        return resp.json()

    if last_exc is not None:
        raise RuntimeError(
            f"GET {path} failed after {max_attempts} attempts "
            f"(last network error: {last_exc})"
        ) from last_exc
    raise RuntimeError(
        f"GET {path} failed after {max_attempts} attempts "
        f"(last HTTP status: {last_status})"
    )


# ---------------------------------------------------------------------------
# Public image hosting (for image-to-image / image-to-video inputs)
# ---------------------------------------------------------------------------
#
# Agnes APIs only accept PUBLIC URLs for image inputs. To make the
# workflow self-contained, the image node auto-uploads the result to a
# free public host (0x0.st) so the next node can chain on it.
#
# 0x0.st is:
#   - Free, no auth, no rate limit on a per-IP basis (soft)
#   - Returns a permanent public URL
#   - Accepts up to 512 MB
# If it fails, the user can paste a URL manually into the next node.

PUBLIC_UPLOAD_URL = "https://0x0.st"


def _upload_to_public_host(local_path: str, max_attempts: int = 3) -> str:
    """Upload a local image file to 0x0.st and return the public URL.

    Raises RuntimeError after retries exhausted.  This is best-effort
    infrastructure — we deliberately don't fail the whole workflow if
    upload fails, the caller should handle that case (e.g. by
    instructing the user to upload manually).
    """
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            with open(local_path, "rb") as f:
                resp = requests.post(
                    PUBLIC_UPLOAD_URL,
                    files={"file": (Path(local_path).name, f)},
                    timeout=120,
                )
            if not resp.ok:
                raise RuntimeError(
                    f"upload failed: {resp.status_code} {resp.text[:200]}"
                )
            public_url = resp.text.strip()
            if not public_url.startswith("http"):
                raise RuntimeError(
                    f"unexpected upload response: {public_url[:200]}"
                )
            print(f"[Agnes] uploaded to {public_url}")
            return public_url
        except (requests.RequestException, RuntimeError, OSError) as e:
            last_exc = e
            if attempt < max_attempts - 1:
                _sleep_backoff(attempt, f"upload to {PUBLIC_UPLOAD_URL} failed")
    raise RuntimeError(
        f"upload to {PUBLIC_UPLOAD_URL} failed after {max_attempts} attempts: {last_exc}"
    )


# ---------------------------------------------------------------------------
# Node 1 — prompt expansion with Agnes 2.0 Flash
# ---------------------------------------------------------------------------


DEFAULT_SYSTEM_PROMPT = (
    "You are a cinematic video prompt engineer. Expand the user's brief idea "
    "into a detailed, vivid, production-ready video prompt. "
    "Use the structure: [Subject] + [Action] + [Scene] + [Camera Movement] + "
    "[Lighting] + [Style]. Output only the expanded prompt, no preamble, no "
    "explanation, no labels."
)


class AgnesTextNode:
    """Expand/enhance a short user prompt into a cinematic video prompt."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "user_prompt": (
                    "STRING",
                    {"multiline": True, "default": "A cat walking on a beach at sunset"},
                ),
            },
            "optional": {
                "api_key": (
                    "STRING",
                    {"default": "", "password": True, "tooltip": "Leave empty to use AGNES_API_KEY env var"},
                ),
                "system_prompt": (
                    "STRING",
                    {"multiline": True, "default": DEFAULT_SYSTEM_PROMPT},
                ),
                "max_tokens": ("INT", {"default": 512, "min": 64, "max": 2048}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0, "step": 0.05}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("expanded_prompt",)
    FUNCTION = "expand"
    CATEGORY = "Agnes/text"

    def expand(
        self,
        user_prompt: str,
        api_key: str = "",
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> tuple[str]:
        key = _resolve_key(api_key)
        data = _post_with_retry(
            "/chat/completions",
            {
                "model": TEXT_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": temperature,
            },
            api_key=key,
            max_attempts=3,  # text endpoint is fast; 3 attempts is plenty
        )
        content = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        print(
            f"[AgnesTextNode] {TEXT_MODEL} tokens "
            f"prompt={usage.get('prompt_tokens')} "
            f"completion={usage.get('completion_tokens')}"
        )
        return (content,)


# ---------------------------------------------------------------------------
# Node 1.5 — image generation with Agnes Image 2.1 Flash
# ---------------------------------------------------------------------------


# Image sizes supported by Agnes Image 2.1 Flash. Width×height, must be
# a multiple of 64. Common values shown; the API may support more.
ALLOWED_IMAGE_SIZES = (
    "512x512", "768x768", "1024x1024",
    "1024x768", "1152x768", "1280x720",
    "768x1024", "720x1280",
)


def _parse_size(size: str) -> tuple[int, int]:
    """Parse 'WxH' into (width, height). Both must be multiples of 64."""
    try:
        w, h = size.lower().split("x")
        w, h = int(w), int(h)
    except (ValueError, AttributeError):
        raise ValueError(
            f"size must be 'WxH' (e.g. '1024x768'), got {size!r}"
        )
    if w % 64 or h % 64:
        raise ValueError(
            f"size dimensions must be multiples of 64, got {w}x{h}"
        )
    return w, h


class AgnesImageNode:
    """Generate or edit images with Agnes Image 2.1 Flash.

    Two modes:
      - **Text-to-image**: leave `input_image` empty. The model generates
        a fresh image from the prompt.
      - **Image-to-image**: pass a local file path in `input_image`. The
        node uploads it to 0x0.st, then sends the public URL to Agnes
        for editing. The result preserves the original composition.

    Always auto-uploads the result to 0x0.st and returns the public URL
    on the `public_url` output, so the next node in the chain (e.g.
    `AgnesVideoGenerateNode`) can consume it directly via its `image`
    widget. The local file path is also returned on `local_path` for
    users who want to preview or save it.
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "A lone astronaut floating through a nebula, cinematic lighting",
                    },
                ),
                "size": (
                    "STRING",
                    {
                        "default": "1024x768",
                        "tooltip": f"WxH, multiples of 64. Allowed: {', '.join(ALLOWED_IMAGE_SIZES)}",
                    },
                ),
            },
            "optional": {
                "api_key": (
                    "STRING",
                    {"default": "", "password": True,
                     "tooltip": "Leave empty to use AGNES_API_KEY env var"},
                ),
                "input_image": (
                    "STRING",
                    {
                        "default": "",
                        "multiline": False,
                        "tooltip": "Local file path (T2I/edit mode). Leave empty for pure text-to-image.",
                    },
                ),
                "edit_instruction": (
                    "STRING",
                    {
                        "multiline": True,
                        "default": "",
                        "tooltip": "Optional: how to edit the input image. "
                                   "If set, this REPLACES the prompt for the "
                                   "image-to-image call. If empty, the prompt "
                                   "is used for both T2I and I2I.",
                    },
                ),
                "response_format": (
                    "STRING",
                    {
                        "default": "url",
                        "choices": ["url"],
                        "tooltip": "Always 'url' — Agnes returns a CDN URL we can re-host.",
                    },
                ),
                "output_dir": (
                    "STRING",
                    {"default": "outputs/agnes"},
                ),
                "skip_upload": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": "If true, skip the public-host upload and just "
                                   "return the local file path. Faster but the "
                                   "next node can't chain automatically.",
                    },
                ),
            },
        }

    RETURN_TYPES = ("STRING", "STRING", "STRING")
    RETURN_NAMES = ("local_path", "public_url", "edit_instruction")
    FUNCTION = "generate"
    CATEGORY = "Agnes/image"
    OUTPUT_NODE = True

    def generate(
        self,
        prompt: str,
        size: str = "1024x768",
        api_key: str = "",
        input_image: str = "",
        edit_instruction: str = "",
        response_format: str = "url",
        output_dir: str = "outputs/agnes",
        skip_upload: bool = False,
    ) -> tuple[str, str, str]:
        _parse_size(size)  # validate

        key = _resolve_key(api_key)
        os.makedirs(output_dir, exist_ok=True)

        # Decide the actual prompt and image inputs for the API call
        actual_prompt = prompt
        image_urls: list[str] = []

        if input_image.strip():
            # Image-to-image mode: upload the local file, then call with image
            ip = input_image.strip()
            if not os.path.exists(ip):
                raise FileNotFoundError(f"input_image not found: {ip}")
            print(f"[AgnesImageNode] uploading input image: {ip}")
            input_url = _upload_to_public_host(ip)
            image_urls = [input_url]
            if edit_instruction.strip():
                actual_prompt = edit_instruction.strip()
            else:
                actual_prompt = (
                    f"{prompt} (preserving the original composition and subject)"
                )
            print(
                f"[AgnesImageNode] image-to-image mode: "
                f"prompt={actual_prompt[:80]!r}"
            )
        else:
            # Pure text-to-image
            print(f"[AgnesImageNode] text-to-image: prompt={prompt[:80]!r}")

        # Build the API payload (matches clients/agnes_client.py format)
        payload: dict = {
            "model": "agnes-image-2.1-flash",
            "prompt": actual_prompt,
            "size": size,
        }
        if image_urls:
            payload["extra_body"] = {
                "image": image_urls,
                "response_format": response_format,
            }

        data = _post_with_retry("/images/generations", payload, api_key=key)

        # Extract the image URL from the response.
        # Format (OpenAI-compatible): {"created": ..., "data": [{"url": "..."}]}
        items = data.get("data") or []
        if not items:
            raise RuntimeError(f"no images in response: {data}")
        first = items[0]
        # Support both `url` and `b64_json` shapes
        image_url = first.get("url")
        if not image_url:
            raise RuntimeError(
                f"no url in response item; b64_json returned? ({list(first.keys())})"
            )

        print(f"[AgnesImageNode] image generated: {image_url}")

        # Download to disk
        os.makedirs(output_dir, exist_ok=True)
        # Use a stable filename pattern so re-runs overwrite (cheap re-render)
        ts = int(time.time() * 1000)
        local_path = os.path.abspath(
            os.path.join(output_dir, f"agnes_image_{ts}.png")
        )
        # Robust download: 5 attempts with DNS pre-resolution and IP
        # fallback. The API returns a storage.googleapis.com URL which
        # can be flaky from some networks.
        _download_with_fallback(
            image_url, local_path,
            max_attempts=5, connect_timeout=30, read_timeout=120,
        )

        size_kb = os.path.getsize(local_path) / 1024
        print(f"[AgnesImageNode] saved {size_kb:.1f} KB to {local_path}")

        # Optionally re-upload to a public host so the next node can chain.
        # We re-upload rather than reuse the API-returned URL because the
        # API URL may be a one-shot signed link that expires.
        public_url = ""
        if not skip_upload:
            try:
                public_url = _upload_to_public_host(local_path)
            except RuntimeError as e:
                print(
                    f"[AgnesImageNode] WARNING: public upload failed: {e}\n"
                    f"  Local file is at: {local_path}\n"
                    f"  To continue the chain, manually upload this file to "
                    f"any public host and paste the URL into the next node."
                )

        # Return the edit_instruction so downstream nodes can see what
        # the user actually asked for (useful for the video prompt).
        return (local_path, public_url, actual_prompt)


# ---------------------------------------------------------------------------
# Node 2 — video generation with Agnes Video V2.0
# ---------------------------------------------------------------------------


# Server-side failure modes that may resolve on their own (shared GPU
# resources, transient GPU contention).  We retry the entire pipeline
# (new task) for these instead of failing hard.
_RETRYABLE_SERVER_ERRORS = (
    "cuda out of memory",
    "out of memory",
    "oom",
    "gpu memory",
    "resource exhausted",
    "service unavailable",
    "internal server error",
)


def _is_retryable_server_error(task_info: dict) -> bool:
    """True if the server-side failure is the kind that may resolve on retry.

    We don't retry on every 500 — some are deterministic (bad prompt, model
    bug). But transient resource exhaustion (CUDA OOM, GPU memory pressure)
    is shared across all tenants, so a 60-90s wait usually clears it.
    """
    error = task_info.get("error") or {}
    message = (error.get("message") or "").lower()
    code = str(error.get("code") or "")
    if code in {"500", "502", "503", "504"}:
        return any(marker in message for marker in _RETRYABLE_SERVER_ERRORS)
    return False


def _humanize_task_failure(task_info: dict) -> str:
    """Extract the most useful part of a failed task response.

    The raw response includes the full task object with timestamps, IDs,
    and a JSON error blob. 90% of the time the user just wants to know
    "why did it fail" — pull out the error message and the task ID.
    """
    task_id = task_info.get("id", "<unknown>")
    error = task_info.get("error") or {}
    msg = error.get("message", "<no error message>")
    code = error.get("code", "?")
    return f"task {task_id} failed ({code}): {msg}"


class AgnesVideoGenerateNode:
    """Create a video task, poll until complete, and download the result.

    Features:
      - Smart retry: exponential backoff with jitter on transient network
        and HTTP errors (covered in _post_with_retry / _get_with_retry).
      - GPU OOM auto-retry: if the server reports CUDA out of memory, the
        shared GPU may be released by another tenant in a minute. We
        automatically re-submit up to 2 times before giving up.
      - Friendly errors: parses the error blob and surfaces the message
        instead of dumping the full task object.
    """

    # How many times to retry the entire pipeline on a server-side
    # resource-exhaustion error. 2 retries × ~60-90s backoff is enough
    # for the GPU to free up under normal load.
    SERVER_RETRY_MAX = 2
    SERVER_RETRY_BACKOFF = 60  # seconds between server-side retries

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"forceInput": True, "multiline": True}),
            },
            "optional": {
                "api_key": (
                    "STRING",
                    {"default": "", "password": True, "tooltip": "Leave empty to use AGNES_API_KEY env var"},
                ),
                "image": (
                    "STRING",
                    {"default": "", "multiline": False, "tooltip": "Public URL of input image for image-to-video"},
                ),
                "height": ("INT", {"default": 768, "min": 256, "max": 2048, "step": 64}),
                "width": ("INT", {"default": 1152, "min": 256, "max": 2048, "step": 64}),
                "num_frames": ("INT", {"default": 121, "min": 81, "max": 441, "step": 8,
                                        "tooltip": "121=5s, 241=10s, 441=18.4s. Larger values need more GPU memory server-side."}),
                "frame_rate": ("INT", {"default": 24, "min": 1, "max": 60}),
                "poll_interval": ("INT", {"default": 10, "min": 1, "max": 60}),
                "max_wait": ("INT", {"default": 1800, "min": 60, "max": 7200}),
                "output_dir": ("STRING", {"default": "outputs/agnes"}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("video_path",)
    FUNCTION = "generate"
    CATEGORY = "Agnes/video"
    OUTPUT_NODE = True

    def _run_pipeline(
        self,
        key: str,
        payload: dict,
        num_frames: int,
        max_wait: int,
        poll_interval: int,
        output_dir: str,
    ) -> str:
        """Single pipeline run: create → poll → download. Returns output path.

        Raises RuntimeError on hard failure. _generate() is responsible
        for deciding whether to call this again on server-side resource
        exhaustion errors.
        """
        # 1) Create task
        task = _post_with_retry("/videos", payload, api_key=key)
        task_id = task.get("id") or task.get("task_id")
        if not task_id:
            raise RuntimeError(f"No task id in response: {task}")
        print(
            f"[AgnesVideoGenerateNode] task created: {task_id} "
            f"({task.get('seconds')}s, {task.get('size')})"
        )

        # 2) Poll
        start = time.monotonic()
        deadline = start + max_wait
        last: dict = {}
        while time.monotonic() < deadline:
            try:
                last = _get_with_retry(f"/videos/{task_id}", api_key=key)
            except RuntimeError as e:
                # Treat poll miss as "still trying" — we have time left
                print(f"[AgnesVideoGenerateNode] poll failed: {e}, continuing...")
                time.sleep(poll_interval)
                continue
            elapsed = int(time.monotonic() - start)
            print(
                f"[AgnesVideoGenerateNode] [{elapsed:>4}s] "
                f"status={last.get('status'):<12} progress={last.get('progress')}%"
            )
            status = last.get("status")
            if status == "completed":
                break
            if status == "failed":
                # Don't raise here — return the failure so _generate can
                # decide whether to retry the whole pipeline.
                # Make sure the task_id is set in the humanized message,
                # even if the upstream response omits it.
                last.setdefault("id", task_id)
                raise RuntimeError(_humanize_task_failure(last))
            time.sleep(poll_interval)
        else:
            raise RuntimeError(
                f"Video task {task_id} did not complete within {max_wait}s"
            )

        # 3) Download
        video_url = last.get("video_url") or last.get("remixed_from_video_id")
        if not video_url:
            raise RuntimeError(f"No video_url in response: {last}")

        os.makedirs(output_dir, exist_ok=True)
        filename = f"agnes_video_{task_id}.mp4"
        out_path = os.path.abspath(os.path.join(output_dir, filename))

        # Robust download: 5 attempts with DNS pre-resolution and IP
        # fallback. 5-min read timeout for the (typically 1-5 MB) MP4.
        _download_with_fallback(
            video_url, out_path,
            max_attempts=5, connect_timeout=30, read_timeout=300,
        )

        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        print(f"[AgnesVideoGenerateNode] saved {size_mb:.2f} MB to {out_path}")
        return out_path

    def generate(
        self,
        prompt: str,
        api_key: str = "",
        image: str = "",
        height: int = 768,
        width: int = 1152,
        num_frames: int = 121,
        frame_rate: int = 24,
        poll_interval: int = 10,
        max_wait: int = 1800,
        output_dir: str = "outputs/agnes",
    ) -> tuple[str]:
        if num_frames not in ALLOWED_FRAMES:
            raise ValueError(
                f"num_frames must be in {ALLOWED_FRAMES} (rule 8n+1, max 441), got {num_frames}"
            )

        key = _resolve_key(api_key)

        payload: dict[str, Any] = {
            "model": VIDEO_MODEL,
            "prompt": prompt,
            "height": height,
            "width": width,
            "num_frames": num_frames,
            "frame_rate": frame_rate,
        }
        if image.strip():
            payload["image"] = image.strip()

        # Pipeline loop with server-side retry for resource exhaustion.
        last_err: Exception | None = None
        for server_attempt in range(self.SERVER_RETRY_MAX + 1):
            try:
                path = self._run_pipeline(
                    key=key,
                    payload=payload,
                    num_frames=num_frames,
                    max_wait=max_wait,
                    poll_interval=poll_interval,
                    output_dir=output_dir,
                )
                return (path,)
            except RuntimeError as e:
                last_err = e
                msg = str(e).lower()
                # Detect server-side resource exhaustion. The poll loop
                # returns the last task info as a JSON dump, but our
                # _humanize_task_failure already pulled the error message.
                is_resource_error = any(
                    marker in msg for marker in _RETRYABLE_SERVER_ERRORS
                )
                if is_resource_error and server_attempt < self.SERVER_RETRY_MAX:
                    print(
                        f"[AgnesVideoGenerateNode] server resource error "
                        f"(attempt {server_attempt + 1}/{self.SERVER_RETRY_MAX + 1}): "
                        f"{e!s:.200}\n"
                        f"  Retrying in {self.SERVER_RETRY_BACKOFF}s — "
                        f"GPU may be released by then."
                    )
                    time.sleep(self.SERVER_RETRY_BACKOFF)
                    continue
                # Not a resource error, or out of retries — give up
                raise

        # Shouldn't reach here, but just in case
        raise last_err or RuntimeError("video generation failed")


# ---------------------------------------------------------------------------
# ComfyUI registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "AgnesTextNode": AgnesTextNode,
    "AgnesImageNode": AgnesImageNode,
    "AgnesVideoGenerateNode": AgnesVideoGenerateNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AgnesTextNode": "Agnes Text (2.0 Flash)",
    "AgnesImageNode": "Agnes Image (2.1 Flash)",
    "AgnesVideoGenerateNode": "Agnes Video Generate (V2.0)",
}
