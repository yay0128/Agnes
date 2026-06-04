"""Image-to-video example using Agnes Video V2.0."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clients import AgnesClient


def main() -> None:
    client = AgnesClient.from_env()
    # Replace with a real, publicly accessible image URL.
    input_image = "https://example.com/image.png"
    result = client.generate_video(
        prompt=(
            "The woman slowly turns around and looks back at the camera, "
            "natural facial expression, cinematic camera movement"
        ),
        image=input_image,
        num_frames=121,
        frame_rate=24,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
