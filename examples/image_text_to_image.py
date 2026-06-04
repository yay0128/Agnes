"""Text-to-image example using Agnes Image 2.1 Flash."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clients import AgnesClient


def main() -> None:
    client = AgnesClient.from_env()
    result = client.generate_image(
        prompt=(
            "A luminous floating city above a misty canyon at sunrise, "
            "cinematic realism, wide-angle composition, rich architectural "
            "details, soft golden light, high visual density"
        ),
        size="1024x768",
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
