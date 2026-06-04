"""Agnes AI client for Image 2.1 Flash and Video V2.0.

Models configured per:
- https://agnes-ai.com/doc/agnes-image-21-flash
- https://agnes-ai.com/doc/agnes-video-v20
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


def _load_dotenv(path: Path | None = None) -> None:
    """Populate os.environ from a .env file (KEY=VALUE per line, # = comment)."""
    env_path = path or Path.cwd() / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


DEFAULT_BASE_URL = "https://apihub.agnes-ai.com/v1"

IMAGE_MODEL = "agnes-image-2.1-flash"
VIDEO_MODEL = "agnes-video-v2.0"

IMAGE_ENDPOINT = "/images/generations"
VIDEO_CREATE_ENDPOINT = "/videos"
VIDEO_RETRIEVE_ENDPOINT = "/videos/{task_id}"


class AgnesError(Exception):
    """Raised for non-2xx responses or unexpected API behavior."""


@dataclass
class AgnesClient:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    timeout: int = 600
    poll_interval: int = 10
    poll_timeout: int = 1800

    @classmethod
    def from_env(cls) -> "AgnesClient":
        api_key = os.environ.get("AGNES_API_KEY")
        if not api_key or api_key == "YOUR_API_KEY":
            raise AgnesError(
                "AGNES_API_KEY is not set. Copy .env.example to .env and "
                "fill in your key from https://platform.agnes-ai.com/."
            )
        return cls(
            api_key=api_key,
            base_url=os.environ.get("AGNES_BASE_URL", DEFAULT_BASE_URL),
            timeout=int(os.environ.get("AGNES_TIMEOUT", 600)),
            poll_interval=int(os.environ.get("AGNES_POLL_INTERVAL", 10)),
            poll_timeout=int(os.environ.get("AGNES_POLL_TIMEOUT", 1800)),
        )

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}{path}"

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(
            self._url(path), json=payload, headers=self._headers, timeout=self.timeout
        )
        if not resp.ok:
            raise AgnesError(f"POST {path} failed: {resp.status_code} {resp.text}")
        return resp.json()

    def _get(self, path: str) -> dict[str, Any]:
        resp = requests.get(self._url(path), headers=self._headers, timeout=self.timeout)
        if not resp.ok:
            raise AgnesError(f"GET {path} failed: {resp.status_code} {resp.text}")
        return resp.json()

    # -------- Image 2.1 Flash --------

    def generate_image(
        self,
        prompt: str,
        size: str = "1024x768",
        image_urls: list[str] | None = None,
        response_format: str = "url",
    ) -> dict[str, Any]:
        """Generate or edit an image with Agnes Image 2.1 Flash.

        Pass ``image_urls`` (>=1) for image-to-image generation.
        """
        payload: dict[str, Any] = {
            "model": IMAGE_MODEL,
            "prompt": prompt,
            "size": size,
        }
        if image_urls:
            payload["extra_body"] = {
                "image": image_urls,
                "response_format": response_format,
            }
        return self._post(IMAGE_ENDPOINT, payload)

    # -------- Video V2.0 --------

    def create_video_task(self, **fields: Any) -> dict[str, Any]:
        """Create an asynchronous video generation task.

        Common fields: prompt, image, mode, height, width, num_frames,
        num_inference_steps, seed, frame_rate, negative_prompt,
        extra_body (dict).
        """
        payload: dict[str, Any] = {"model": VIDEO_MODEL, **fields}
        return self._post(VIDEO_CREATE_ENDPOINT, payload)

    def retrieve_video_task(self, task_id: str) -> dict[str, Any]:
        """Get the current status/result of a video task."""
        return self._get(VIDEO_RETRIEVE_ENDPOINT.format(task_id=task_id))

    def wait_for_video(
        self,
        task_id: str,
        poll_interval: int | None = None,
        timeout: int | None = None,
        on_progress=None,
    ) -> dict[str, Any]:
        """Poll the task endpoint until completion, failure, or timeout.

        ``on_progress(task_dict)`` is called on every poll for UI/logging.
        Returns the final task dict.
        """
        interval = poll_interval or self.poll_interval
        deadline = time.monotonic() + (timeout or self.poll_timeout)
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            last = self.retrieve_video_task(task_id)
            status = last.get("status")
            if on_progress:
                on_progress(last)
            if status == "completed":
                return last
            if status == "failed":
                raise AgnesError(f"Video task {task_id} failed: {last}")
            time.sleep(interval)
        raise AgnesError(
            f"Video task {task_id} did not complete within "
            f"{timeout or self.poll_timeout} seconds"
        )

    def generate_video(self, **fields: Any) -> dict[str, Any]:
        """Create a video task and block until it completes.

        Accepts the same kwargs as :meth:`create_video_task`.
        """
        task = self.create_video_task(**fields)
        task_id = task.get("id") or task.get("task_id")
        if not task_id:
            raise AgnesError(f"No task id in response: {task}")
        return self.wait_for_video(task_id)
