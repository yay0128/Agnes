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
import time
from pathlib import Path
from typing import Any

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
# Node 2 — video generation with Agnes Video V2.0
# ---------------------------------------------------------------------------


class AgnesVideoGenerateNode:
    """Create a video task, poll until complete, and download the result."""

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
                "num_frames": ("INT", {"default": 121, "min": 81, "max": 441, "step": 8}),
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
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        # 1) Create task
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
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            try:
                last = _get_with_retry(f"/videos/{task_id}", api_key=key)
            except RuntimeError as e:
                # _get_with_retry already printed the network errors with backoff.
                # If we still can't get a response, treat this poll as a miss
                # and continue the loop (we still have time before deadline).
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
                raise RuntimeError(f"Video task {task_id} failed: {last}")
            time.sleep(poll_interval)
        else:
            raise RuntimeError(
                f"Video task {task_id} did not complete within {max_wait}s"
            )

        # 3) Download (with smart retry for transient network errors)
        video_url = last.get("video_url") or last.get("remixed_from_video_id")
        if not video_url:
            raise RuntimeError(f"No video_url in response: {last}")

        os.makedirs(output_dir, exist_ok=True)
        filename = f"agnes_video_{task_id}.mp4"
        out_path = os.path.abspath(os.path.join(output_dir, filename))

        # Download with retry.  We delete a partial file on failure so the
        # next attempt starts clean.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with requests.get(video_url, stream=True, timeout=600) as r:
                    r.raise_for_status()
                    with open(out_path, "wb") as f:
                        for chunk in r.iter_content(chunk_size=1 << 16):
                            f.write(chunk)
                last_exc = None
                break
            except requests.RequestException as e:
                last_exc = e
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except OSError:
                        pass
                if not _is_retryable_exception(e):
                    raise RuntimeError(f"download failed: {e}") from e
                print(
                    f"[AgnesVideoGenerateNode] download network error: "
                    f"{type(e).__name__}"
                )
                if attempt < 2:
                    _sleep_backoff(attempt, "download network error")

        if last_exc is not None:
            raise RuntimeError(
                f"download failed after 3 attempts: {last_exc}"
            ) from last_exc

        size_mb = os.path.getsize(out_path) / (1024 * 1024)
        print(f"[AgnesVideoGenerateNode] saved {size_mb:.2f} MB to {out_path}")

        return (out_path,)


# ---------------------------------------------------------------------------
# ComfyUI registration
# ---------------------------------------------------------------------------

NODE_CLASS_MAPPINGS = {
    "AgnesTextNode": AgnesTextNode,
    "AgnesVideoGenerateNode": AgnesVideoGenerateNode,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "AgnesTextNode": "Agnes Text (2.0 Flash)",
    "AgnesVideoGenerateNode": "Agnes Video Generate (V2.0)",
}
