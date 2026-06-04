# Agnes

A two-model pipeline for **text → video** generation that drops straight into
[ComfyUI](https://www.comfy.org/), plus a standalone Python client and YAML
config that capture the same surface in code.

```
[short user idea] → Agnes 2.0 Flash  →  cinematic prompt
                                ↓
                  Agnes Video V2.0   →  MP4 on disk
```

Both endpoints come from [agnes-ai.com](https://agnes-ai.com):

| Model | Role | API |
| --- | --- | --- |
| [Agnes 2.0 Flash](https://agnes-ai.com/doc/agnes-20-flash) | Text — expand brief ideas into cinematic prompts | `POST /v1/chat/completions` |
| [Agnes Video V2.0](https://agnes-ai.com/doc/agnes-video-v20) | Video — async generate from prompt (text-to-video, image-to-video, keyframes) | `POST /v1/videos` + `GET /v1/videos/{task_id}` |

API base: `https://apihub.agnes-ai.com/v1` — auth: `Authorization: Bearer <key>`.
The Agnes platform also ships [Agnes Image 2.1 Flash](https://agnes-ai.com/doc/agnes-image-21-flash)
(text-to-image / image-to-image), which the standalone client in this repo
supports but is **not** part of the bundled ComfyUI workflow.

---

## Table of contents

1. [Quick start — ComfyUI](#quick-start--comfyui)
2. [Quick start — Advanced multi-scene workflow](#quick-start--advanced-multi-scene-workflow)
3. [Quick start — Python](#quick-start--python)
3. [The ComfyUI integration (the main feature)](#the-comfyui-integration)
   - [What gets installed where](#what-gets-installed-where)
   - [Configuration (API key)](#configuration-api-key)
   - [Using the workflow](#using-the-workflow)
   - [Inputs reference](#inputs-reference)
   - [How the custom node auto-loads `.env`](#how-the-custom-node-auto-loads-env)
4. [Standalone Python client](#standalone-python-client)
5. [Project layout](#project-layout)
6. [API reference (raw HTTP)](#api-reference-raw-http)
7. [Troubleshooting](#troubleshooting)
8. [Development](#development)
9. [License](#license)

---

## Quick start — ComfyUI

```bash
# 1. Drop the custom node into your ComfyUI install
cp -R comfyui_workflow/custom_nodes/agnes_api \
      <ComfyUI>/custom_nodes/

# 2. Put your API key somewhere the node can find it
echo 'AGNES_API_KEY=YOUR_API_KEY' > <ComfyUI>/.env
#     — or set it in the node's `api_key` widget (it's a password field)
#     — or `export AGNES_API_KEY=...` before launching ComfyUI

# 3. Launch ComfyUI (or restart if it's already running)

# 4. In the browser: drag comfyui_workflow/workflow.json onto the canvas,
#    edit the `user_prompt` widget, and click "Queue Prompt".
```

That's it. The video node is `OUTPUT_NODE=True` — the resulting MP4 path
appears in ComfyUI's output panel and the file lands in the configured
`output_dir` (default `<ComfyUI>/output/`).

If you'd rather reach for it from the menu instead of dragging a file,
the workflow is also pre-staged at
`<ComfyUI>/user/default/workflows/Agnes_Text_to_Video.json` after step 1
(see the "Standalone Python install" section below for the staging step).

### Advanced multi-scene cinematic pipeline

For a production-grade multi-scene workflow:

```bash
# Option A: Drag the file onto the canvas
cp comfyui_workflow/workflow_advanced.json \
   <ComfyUI>/user/default/workflows/Agnes_MultiScene_Cinematic_Pipeline.json

# Option B: Run the one-command installer
./comfyui_workflow/install.sh /path/to/ComfyUI
```

This stages **two** workflows at once (basic + advanced).

**What the advanced workflow does:**

```
User Prompt (short idea)
        │
        ▼
┌──────────────────────────────┐
│ ① AgnesTextNode (2.0 Flash)   │  Expand → cinematic prompt A
│    Cinematic Expansion         │
└─────────┬────────────────────┘
          │
          ▼
┌──────────────────────────────┐
│ ② AgnesTextNode (2.0 Flash)   │  Refine → more specific lighting,
│    Refinement Pass             │  lens type, color grading (temp 0.5)
└─────────┬────────────┬───────┘
          │            │
          ▼            ▼
  ┌──────────────┐  ┌─────────────────┐
  │③ Scene 1     │  │⑤ Scene Variant  │
  │ Nebula       │  │ (alt continuation│
  │ 441 frames   │  │  for Scene 2)    │
  │ text-to-video│  └────────┬─────────┘
  └──────────────┘           │
          │                  ▼
          │            ┌─────────────────┐
          │            │⑥ Scene 3         │
          ▼            │ Cosmic Climax    │
  ┌──────────────┐     │ 241 frames       │
  │④ Scene 2     │     │ image-to-video   │
  │ Black Hole   │     └─────────────────┘
  │ 241 frames   │
  │ image-to-video│
  └──────────────┘

  Total runtime: ~38 seconds
  Scenes chain via last-frame extraction
  (or use Scene 1 text-to-video as source
   for Scenes 2+3 image-to-video)
```

| Feature | Detail |
| --- | --- |
| **Scene 1** | 441 frames (18.4s), text-to-video, slow cinematic pan |
| **Scene 2** | 241 frames (10s), image-to-video, black hole transition |
| **Scene 3** | 241 frames (10s), image-to-video, cosmic climax |
| **Prompt pipeline** | Expand → Refine (temp 0.5) → Variant (temp 0.8) |
| **Groups** | 5 color-coded groups for visual clarity |

To chain scenes: extract the last frame of Scene N and paste it into
Scene N+1's `image` widget for image-to-video continuity.

---

## Quick start — Python

```bash
git clone <this-repo>
cd Agnes
python3 -m venv .venv && source .venv/bin/activate
pip install requests pyyaml

cp .env.example .env
# edit .env and set AGNES_API_KEY

python scripts/check_config.py        # validate env
python examples/image_text_to_image.py # → PNG in outputs/
python examples/video_text_to_video.py # → MP4 in outputs/
```

The standalone client handles image (sync), video (async + poll + download),
429 retry with backoff, and a structured error type. See
[Standalone Python client](#standalone-python-client).

---

## The ComfyUI integration

### What gets installed where

```
<ComfyUI>/
├── .env                                       ← (1) optional, holds AGNES_API_KEY
├── custom_nodes/
│   └── agnes_api/                             ← (2) the custom node package
│       ├── __init__.py                        registers NODE_CLASS_MAPPINGS
│       └── agnes_nodes.py                     AgnesTextNode + AgnesVideoGenerateNode
└── user/default/workflows/
    └── Agnes_Text_to_Video.json               ← (3) pre-staged workflow
```

(1) and (3) are created by the install script (see
[Standalone Python install](#standalone-python-install-optional));
copying (2) is the only step that is **required** for the nodes to appear.

The custom node exposes two nodes under the **Agnes** category in the
right-click node menu:

| Display name | Class | Purpose |
| --- | --- | --- |
| **Agnes Text (2.0 Flash)** | `AgnesTextNode` | Expand a short user prompt into a cinematic video prompt |
| **Agnes Video Generate (V2.0)** | `AgnesVideoGenerateNode` | Submit to Video V2.0, poll until complete, download MP4 |

### Configuration (API key)

The custom node resolves the API key in this order (first non-empty wins):

1. `api_key` widget on the node (password field)
2. `AGNES_API_KEY` environment variable
3. `.env` file auto-loaded from one of:
   - `<ComfyUI install root>/.env`
   - `<ComfyUI>/custom_nodes/.env`
   - `<ComfyUI>/custom_nodes/agnes_api/.env`
   - `<cwd>/.env`

`.env` loading happens **once at import time** and **never overwrites an
existing env var** — so a key in your shell wins over a key on disk.

`.env.example` ships with the project:

```env
AGNES_API_KEY=YOUR_API_KEY
```

### Using the workflow

Once ComfyUI is running and the workflow is on the canvas:

1. Click the **Agnes Text (2.0 Flash)** node and edit `user_prompt` (the
   multi-line text box on the left). Default: `"A cat walking on a beach
   at sunset"`.
2. Optionally tune the video parameters on **Agnes Video Generate (V2.0)**:
   `height` / `width` (default 768×1152), `num_frames` (default 121 — must
   be 81, 121, 161, 241, or 441; rule 8n+1, max 441), `frame_rate` (default
   24, range 1–60), `output_dir`.
3. Click **Queue Prompt**.
4. Watch the terminal running ComfyUI. You'll see lines like:
   ```
   [AgnesVideoGenerateNode] task created: task_xZ2SzGahrTH14Kki4I6VG81QiWGbS6AF
   [AgnesVideoGenerateNode] [   2s] status=queued       progress=0%
   [AgnesVideoGenerateNode] [ 269s] status=completed    progress=100%
   [AgnesVideoGenerateNode] saved 1.71 MB to /…/output/agnes_video_task_xZ2S….mp4
   ```
5. The MP4 path appears in the workflow's **output panel** on the right.
6. The file is also in `output_dir` on disk (default `<ComfyUI>/output/`).

### Inputs reference

#### Agnes Text (2.0 Flash)

| Input | Type | Default | Notes |
| --- | --- | --- | --- |
| `user_prompt` | STRING (multiline) | `"A cat walking on a beach at sunset"` | Your short idea |
| `api_key` | STRING (password) | `""` | Falls back to env / .env |
| `system_prompt` | STRING (multiline) | cinematic prompt-engineer prompt | Override to change expansion style |
| `max_tokens` | INT | 512 | 64–2048 |
| `temperature` | FLOAT | 0.7 | 0.0–2.0 |

**Output:** `expanded_prompt` (STRING)

#### Agnes Video Generate (V2.0)

| Input | Type | Default | Notes |
| --- | --- | --- | --- |
| `prompt` | STRING (forceInput) | — | Connect to `expanded_prompt` from Agnes Text |
| `api_key` | STRING (password) | `""` | Falls back to env / .env |
| `image` | STRING | `""` | Public URL for image-to-video |
| `height` | INT | 768 | 256–2048 |
| `width` | INT | 1152 | 256–2048 |
| `num_frames` | INT | 121 | Must be 81, 121, 161, 241, or 441 (rule 8n+1, max 441) |
| `frame_rate` | INT | 24 | 1–60 |
| `poll_interval` | INT | 10 | seconds between status polls |
| `max_wait` | INT | 1800 | seconds before giving up |
| `output_dir` | STRING | `"output"` | Where the MP4 is written (resolved relative to ComfyUI's CWD if not absolute) |

**Output:** `video_path` (STRING, absolute path to the downloaded MP4)

### How the custom node auto-loads `.env`

`agnes_api/agnes_nodes.py` calls a private `_load_dotenv()` once at import
time. It walks a list of candidate `.env` paths and sets any **previously
unset** environment variables it finds. This means:

- A key in your shell is never clobbered by a key on disk.
- A misplaced key in a parent directory cannot shadow a more specific one
  in the child.
- It runs only at import; reloading the page in ComfyUI re-imports the
  module and re-runs the loader, so editing `.env` and refreshing picks
  up the new value.

---

## Standalone Python client

Beyond ComfyUI, this repo contains a regular Python wrapper at
`clients/agnes_client.py`:

```python
from clients import AgnesClient

client = AgnesClient.from_env()

# Image (sync) — see examples/image_text_to_image.py
result = client.generate_image(
    prompt="A luminous floating city above a misty canyon at sunrise",
    size="1024x768",
)
print(result["data"][0]["url"])

# Image-to-image
result = client.generate_image(
    prompt="Transform the scene into a rain-soaked cyberpunk night...",
    image_urls=["https://example.com/input.png"],
)

# Video (async task → poll → download)
final = client.generate_video(
    prompt="A cat walking on the beach at sunset...",
    height=768, width=1152, num_frames=121, frame_rate=24,
)
print(final["video_url"])

# Manual task control
task = client.create_video_task(prompt="...", num_frames=121, frame_rate=24)
status = client.retrieve_video_task(task["id"])
# or: final = client.wait_for_video(task_id, on_progress=lambda t: print(t["progress"]))
```

`AgnesClient` features:

- `.from_env()` — reads `AGNES_API_KEY` from the env, falling back to `.env`
- `AgnesError` exception type for non-2xx responses
- 429 retry with linear backoff (10s, 20s, 30s, 40s, 50s — see
  `_post_with_retry`)
- Configurable `poll_interval` (default 10s) and `poll_timeout` (default
  30 min)

See `examples/` for runnable scripts:

| Script | What it does |
| --- | --- |
| `image_text_to_image.py` | Text → image via Agnes Image 2.1 Flash |
| `image_image_to_image.py` | Image + prompt → edited image |
| `video_text_to_video.py` | Text → video (default 121 frames @ 24 fps) |
| `video_image_to_video.py` | Image → video (animate a single image) |
| `video_keyframes.py` | Two keyframes → smooth interpolated video |
| `_video_test_runner.py` | Diagnostic with 5-attempt 429 retry (saves to `outputs/`) |

### Standalone Python install (optional)

To stage the workflow and `.env` into a ComfyUI install from this repo:

```bash
COMFYUI=~/Documents/ComfyUI   # your install

cp -R comfyui_workflow/custom_nodes/agnes_api "$COMFYUI/custom_nodes/"
cp comfyui_workflow/workflow.json \
   "$COMFYUI/user/default/workflows/Agnes_Text_to_Video.json"
echo 'AGNES_API_KEY=YOUR_API_KEY' > "$COMFYUI/.env"
```

---

## Project layout

```
Agnes/
├── README.md                          this file
├── LICENSE                            MIT
├── .env.example                       template (key placeholder only)
├── .gitignore
├── config/
│   └── agnes.yaml                     endpoints, model params, error codes
├── clients/
│   ├── __init__.py
│   └── agnes_client.py                AgnesClient (sync + async-poll video)
├── examples/
│   ├── image_text_to_image.py
│   ├── image_image_to_image.py
│   ├── video_text_to_video.py
│   ├── video_image_to_video.py
│   ├── video_keyframes.py
│   └── _video_test_runner.py          diagnostic, with 429 retry
├── scripts/
│   └── check_config.py                validate env before running examples
├── comfyui_workflow/
│   ├── workflow.json                  drag-drop onto the ComfyUI canvas
│   └── custom_nodes/agnes_api/
│       ├── __init__.py
│       ├── agnes_nodes.py             AgnesTextNode + AgnesVideoGenerateNode
│       └── README.md                  per-package docs
└── outputs/                           (created at runtime, .gitignored)
```

---

## API reference (raw HTTP)

If you don't want the Python client, the underlying calls are:

```bash
# 1. Expand prompt with Agnes 2.0 Flash
curl https://apihub.agnes-ai.com/v1/chat/completions \
  -H "Authorization: Bearer $AGNES_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-2.0-flash",
    "messages": [
      {"role": "system", "content": "You are a cinematic video prompt engineer. ..."},
      {"role": "user",   "content": "A cat on a beach at sunset"}
    ],
    "max_tokens": 512,
    "temperature": 0.7
  }'

# 2. Create a video task
curl -X POST https://apihub.agnes-ai.com/v1/videos \
  -H "Authorization: Bearer $AGNES_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "agnes-video-v2.0",
    "prompt": "<expanded prompt from step 1>",
    "height": 768, "width": 1152,
    "num_frames": 121, "frame_rate": 24
  }'
# → { "id": "task_…", "status": "queued", ... }

# 3. Poll for the result
curl https://apihub.agnes-ai.com/v1/videos/task_… \
  -H "Authorization: Bearer $AGNES_API_KEY"
# → { "status": "completed", "video_url": "https://…/video_…mp4", ... }
```

**Video duration rule:** `seconds = num_frames / frame_rate`; `num_frames`
must satisfy `8n + 1` and be ≤ 441. Allowed values: `81, 121, 161, 241, 441`.

**Error codes:**

| Code | Meaning |
| --- | --- |
| 400 | Invalid request — check params |
| 401 | Unauthorized — check `AGNES_API_KEY` |
| 404 | Task not found (video retrieve only) |
| 429 | Upstream saturated — back off and retry |
| 500 | Server error |
| 503 | Service busy — retry later |

**Pricing (as documented):** image $0.003/image, video $0.005/second
(currently free during promo).

---

## Troubleshooting

### The Agnes node doesn't appear in the menu

- ComfyUI loads custom nodes at startup. **Restart ComfyUI** after copying
  `agnes_api/` into `custom_nodes/`.
- Look at the ComfyUI launch terminal — there should be no Python
  traceback when it imports `agnes_api`. If there is, fix the traceback
  (usually a missing `requests` install: `pip install requests` in the
  venv).
- The package **must** be at `<ComfyUI>/custom_nodes/agnes_api/` with
  `__init__.py` inside, **not** nested under another folder.

### "AGNES_API_KEY missing" when running the node

- Verify the key is reachable: from the ComfyUI launch directory, run
  `python -c "import os, agnes_api; print(os.environ.get('AGNES_API_KEY','MISSING')[:8])"`
  (with `PYTHONPATH=<ComfyUI>/custom_nodes` if needed). It should print
  the first 8 chars of your key.
- The widget value, if non-empty, must **not** be the literal string
  `YOUR_API_KEY` — that's treated as a placeholder.

### 429 "upstream saturated" repeating until timeout

The Agnes platform returns 429 when the model group is at capacity. Both
the Python client and the ComfyUI custom node retry automatically with
linear backoff (10s, 20s, 30s, 40s, 50s for a total of ~150s) and surface
a clear error if it persists. This is the upstream — wait a few minutes
and queue again. Smaller jobs (e.g. `num_frames=81`) hit lighter load.

### Workflow shows a red box on a node

A red box means the node raised. Open the ComfyUI terminal, scroll to
the most recent traceback, and read the message. The most common
causes are an invalid `num_frames` (must be in `[81, 121, 161, 241, 441]`)
or a missing / wrong API key.

---

## Development

```bash
# Compile-check
python3 -m py_compile clients/agnes_client.py clients/__init__.py \
  examples/*.py scripts/check_config.py \
  comfyui_workflow/custom_nodes/agnes_api/agnes_nodes.py \
  comfyui_workflow/custom_nodes/agnes_api/__init__.py

# Validate YAML config
python3 -c "import yaml; yaml.safe_load(open('config/agnes.yaml'))"

# Validate workflow.json shape
python3 -c "
import json
wf = json.load(open('comfyui_workflow/workflow.json'))
assert {n['type'] for n in wf['nodes']} == {'AgnesTextNode', 'AgnesVideoGenerateNode'}
assert wf['version'] == 0.4
print('workflow.json OK')
"
```

The custom node code has a small in-process test you can run with
`python3` against the ComfyUI venv (or any venv with `requests`):

```python
from agnes_api import NODE_CLASS_MAPPINGS
node = NODE_CLASS_MAPPINGS["AgnesTextNode"]()
print(node.expand(user_prompt="A cat on a beach", max_tokens=200))
```

---

## License

MIT — see [LICENSE](LICENSE).
