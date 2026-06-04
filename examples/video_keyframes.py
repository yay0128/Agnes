"""Keyframe animation example using Agnes Video V2.0.

The two image URLs are interpolated into a smooth transition.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clients import AgnesClient


def main() -> None:
    client = AgnesClient.from_env()
    # Replace with two real, publicly accessible keyframe image URLs.
    keyframes = [
        "https://example.com/keyframe1.png",
        "https://example.com/keyframe2.png",
    ]
    result = client.generate_video(
        prompt=(
            "Generate a smooth cinematic transition between the keyframes, "
            "maintaining visual consistency and natural camera movement"
        ),
        extra_body={"image": keyframes, "mode": "keyframes"},
        num_frames=121,
        frame_rate=24,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
