"""Validate environment and config without hitting the API.

Run before any example to confirm AGNES_API_KEY is loaded.
Auto-loads a .env file from the project root if present.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env(ROOT / ".env")

from clients import AgnesClient  # noqa: E402  (after env load)


def main() -> int:
    api_key = os.environ.get("AGNES_API_KEY", "")
    if not api_key or api_key == "YOUR_API_KEY":
        print(
            "AGNES_API_KEY is missing. Copy .env.example to .env and set your key.\n"
            "Get one at https://platform.agnes-ai.com/"
        )
        return 1

    masked = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else "***"
    print(f"AGNES_API_KEY loaded: {masked}")
    base = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
    print(f"AGNES_BASE_URL:      {base}")
    print("Config OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
