"""Text-to-video example using Agnes Video V2.0.

num_frames / frame_rate target ~5 seconds (121 / 24).
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
    result = client.generate_video(
        prompt=(
            "A cinematic shot of a cat walking on the beach at sunset, "
            "soft ocean waves, warm golden lighting, realistic motion"
        ),
        height=768,
        width=1152,
        num_frames=121,
        frame_rate=24,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
