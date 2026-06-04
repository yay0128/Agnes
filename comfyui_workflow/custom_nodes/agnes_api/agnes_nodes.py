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


def _post_with_retry(
    path: str,
    payload: dict[str, Any],
    api_key: str,
    max_attempts: int = 5,
    base_wait: int = 10,
) -> dict[str, Any]:
    """POST that backs off on 429 (upstream saturated) responses."""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(
                f"{BASE_URL}{path}", json=payload, headers=headers, timeout=120
            )
        except requests.RequestException as e:
            last_exc = e
            time.sleep(base_wait * attempt)
            continue
        if resp.status_code == 429:
            wait = base_wait * attempt
            print(f"[Agnes] 429 from {path}, sleeping {wait}s (attempt {attempt})")
            time.sleep(wait)
            continue
        if not resp.ok:
            raise RuntimeError(f"POST {path} failed: {resp.status_code} {resp.text}")
        return resp.json()
    raise RuntimeError(
        f"POST {path} kept returning 429 after {max_attempts} attempts"
        if last_exc is None
        else f"POST {path} failed: {last_exc}"
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
            resp = requests.get(
                f"{BASE_URL}/videos/{task_id}", headers=headers, timeout=60
            )
            if resp.status_code == 429:
                wait = poll_interval
                print(f"[AgnesVideoGenerateNode] 429 on poll, sleeping {wait}s")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            last = resp.json()
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

        # 3) Download
        video_url = last.get("video_url") or last.get("remixed_from_video_id")
        if not video_url:
            raise RuntimeError(f"No video_url in response: {last}")

        os.makedirs(output_dir, exist_ok=True)
        filename = f"agnes_video_{task_id}.mp4"
        out_path = os.path.abspath(os.path.join(output_dir, filename))

        with requests.get(video_url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(out_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)

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
