"""Test runner for Video V2.0 with live progress logging and 429 retry.

Lightweight defaults (81 frames @ 15 fps) and 5 retries with backoff
to ride out upstream load-saturation responses.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clients import AgnesClient, AgnesError


def create_with_retry(client: AgnesClient, max_attempts: int = 5, base_wait: int = 30) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return client.create_video_task(
                prompt=(
                    "A cat walking on the beach at sunset, warm golden lighting, "
                    "realistic motion, cinematic"
                ),
                height=768,
                width=1152,
                num_frames=81,
                frame_rate=15,
            )
        except AgnesError as e:
            msg = str(e)
            if "429" in msg or "fail_to_fetch_task" in msg or "饱和" in msg:
                wait = base_wait * attempt
                print(
                    f"[attempt {attempt}/{max_attempts}] upstream saturated, "
                    f"sleeping {wait}s then retrying...",
                    flush=True,
                )
                last_err = e
                time.sleep(wait)
                continue
            raise
    raise last_err  # type: ignore[misc]


def main() -> None:
    client = AgnesClient.from_env()
    print("Creating video task (81 frames @ 15 fps, ~5.4s)...")
    task = create_with_retry(client)
    print("Create-task response:")
    print(json.dumps(task, indent=2))
    task_id = task.get("id") or task.get("task_id")
    if not task_id:
        raise SystemExit(f"No task id in response: {task}")

    start = time.monotonic()

    def on_progress(t: dict) -> None:
        elapsed = int(time.monotonic() - start)
        print(
            f"[{elapsed:>4}s] status={t.get('status'):<12} "
            f"progress={t.get('progress')}%",
            flush=True,
        )

    final = client.wait_for_video(task_id, on_progress=on_progress, poll_interval=5)
    print("\nFinal result:")
    print(json.dumps(final, indent=2))

    video_url = final.get("video_url") or final.get("remixed_from_video_id")
    if video_url:
        out = ROOT / "outputs" / "test_video_v2_0.mp4"
        out.parent.mkdir(exist_ok=True)
        print(f"\nDownloading {video_url} -> {out}")
        import requests

        with requests.get(video_url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(out, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        size_mb = out.stat().st_size / (1024 * 1024)
        print(f"Downloaded {size_mb:.2f} MB to {out}")


if __name__ == "__main__":
    main()
