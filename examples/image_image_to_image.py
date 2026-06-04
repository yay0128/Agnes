"""Image-to-image example using Agnes Image 2.1 Flash."""
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
    input_image = "https://example.com/input-image.png"
    result = client.generate_image(
        prompt=(
            "Transform the scene into a rain-soaked cyberpunk night with "
            "neon reflections while preserving the original composition "
            "and main subject layout."
        ),
        size="1024x768",
        image_urls=[input_image],
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
