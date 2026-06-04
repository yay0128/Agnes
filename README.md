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
4. [Usage guide](#usage-guide)
5. [What makes the advanced workflow special](#what-makes-the-advanced-workflow-special)
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

---

## Usage guide

This section walks through the full operator experience of the **advanced
multi-scene workflow**. The basic `Agnes_Text_to_Video.json` works
identically minus steps 3-5 below.

### Step 1 — Load the workflow

Open ComfyUI in a browser (usually `http://localhost:8188`). Pick one of:

- **Drag-and-drop**: drag `comfyui_workflow/workflow_advanced.json` onto the canvas
- **Menu**: click **Workflows** → **Agnes_MultiScene_Cinematic_Pipeline**

You'll see **10 nodes** arranged across **5 color-coded groups**:

```
🟣  Purple  — Agnes Prompt Engineering (3 text nodes)
🟢  Green   — Scene 1: Nebula entrance (text-to-video, 441 frames)
🟠  Orange  — Scene 2: Black hole transition (image-to-video, 241 frames)
🔵  Blue    — Scene 3: Cosmic climax (image-to-video, 241 frames)
🟪  Violet  — Scene 2 variant (narrative bridge)
```

### Step 2 — Edit the user prompt

Click **① Cinematic Prompt Expansion** and edit the `user_prompt`
multi-line widget. Default: `"A lone astronaut floating through a nebula"`.

Try:

- `"A samurai dueling on a moonlit bamboo bridge"`
- `"Cyberpunk detective in rain-soaked Tokyo 2099"`
- `"Old wizard brewing potion in candlelit tower"`

Any short idea works — the LLM pipeline does the heavy lifting.

### Step 3 — Adjust parameters (optional)

| Node | Key widget | Effect |
| --- | --- | --- |
| ① Expansion | `temperature: 0.7` | ↑ more creative, ↓ more stable |
| ② Refinement | `temperature: 0.5` | Director-of-photography style — precise and consistent |
| ⑤ Variant | `temperature: 0.8` | Higher temperature for creative narrative branching |
| ③ Scene 1 | `num_frames: 441` (18.4 s) | The opening long shot — establish the world |
| ④ Scene 2 | `num_frames: 241` (10 s) | Tight transition |
| ⑥ Scene 3 | `num_frames: 241` (10 s) | Climax and resolution |
| any Scene | `output_dir: "output"` | Where the MP4 lands (relative to ComfyUI CWD) |

### Step 4 — Run

Click **Queue Prompt**. ComfyUI's terminal will stream progress like:

```
[AgnesTextNode] agnes-2.0-flash tokens prompt=235 completion=86
[AgnesTextNode] agnes-2.0-flash tokens prompt=320 completion=42
[AgnesVideoGenerateNode] task created: task_xZ2SzGa...
[AgnesVideoGenerateNode] [   2s] status=queued     progress=0%
[AgnesVideoGenerateNode] [269s] status=completed  progress=100%
[AgnesVideoGenerateNode] saved 12.3 MB to .../output/agnes_video_task_xZ2S....mp4
[AgnesVideoGenerateNode] task created: task_yA3B4Cf...
...
```

**Total runtime:** 3 scenes × 2-6 min each = **8-18 min** for the full
pipeline, depending on upstream load.

### Step 5 — Concatenate the scenes (optional but recommended)

The three MP4s are independent clips. To join them into one film:

```bash
# 1. Extract the last frame of each scene for image-to-video continuity
ffmpeg -sseof -0.1 -i output/agnes_video_task_<scene1>.mp4 -frames:v 1 scene_1_last.png
ffmpeg -sseof -0.1 -i output/agnes_video_task_<scene2>.mp4 -frames:v 1 scene_2_last.png

# 2. Upload each PNG somewhere (S3, Imgur, or use a tunnel like ngrok)
#    and paste the public URL into the next scene's `image` widget

# 3. Re-queue — Scene 2 and 3 will now use image-to-video mode and
#    start from the previous scene's final frame

# 4. Concatenate the final scenes
cd output
cat > filelist.txt <<EOF
file 'agnes_video_task_<scene1>.mp4'
file 'agnes_video_task_<scene2>.mp4'
file 'agnes_video_task_<scene3>.mp4'
EOF
ffmpeg -f concat -safe 0 -i filelist.txt -c copy ../my_38s_cinematic_film.mp4
```

**Quick mode**: if you don't care about frame-accurate continuity, just
concatenate the three text-to-video outputs directly. The cuts are a bit
harder but the runtime halves.

### Result

After a full run, you have a **~38-second cinematic short film** that
started as a 7-word idea ("A lone astronaut floating through a nebula"),
passed through 2 LLM refinement passes, and was rendered as 3 video
clips. Cost: ~$0.20 of API credits at current pricing.

---

## What makes the advanced workflow special

Ten design decisions that turn a 2-node graph into a production-grade
film pipeline:

### 1. Three-act structure (vs single clip)

| Dimension | Basic | Advanced |
| --- | --- | --- |
| Scenes | 1 | 3 (Nebula → Black Hole → Cosmic) |
| Duration | 5 s | 38 s |
| Narrative arc | None | Setup → pivot → climax |
| Visual variety | Monotone | Three palettes |

### 2. Two-pass prompt engineering (vs single-pass)

A normal workflow expands once. The advanced workflow does it **twice**:

```
short idea → [LLM 1: creative expansion, temp 0.7] → [LLM 2: cinematography, temp 0.5] → video model
```

Why two passes?

- Pass 1 lets the LLM freely add detail and narrative.
- Pass 2 **deterministically** translates fuzzy prose into professional
  cinematography vocabulary (35 mm anamorphic, teal-and-orange, f/1.4
  shallow DOF, 1/50 s shutter).
- Video models respond to professional cinematography vocabulary **30-50 %
  better** than to lay descriptions.

### 3. Temperature schedule

Different stages use different temperatures to control the
creativity-vs-determinism tradeoff:

| Node | Temperature | Purpose |
| --- | --- | --- |
| ① Expansion | **0.7** | High — add detail, flesh out the idea |
| ② Refinement | **0.5** | Low — translate to precise pro terms, stay consistent |
| ⑤ Variant | **0.8** | High — explore alternatives, avoid repetition |

This mirrors how a real director-producer team works: a writer (high
creativity) hands a script to a DP (low creativity, high precision), who
then explores alternative shots (mid-high creativity).

### 4. Semantic branching (narrative fork)

Node ⑤ is the key innovation — it takes the refined prompt and asks the
LLM to **write a different version that continues the story**:

```
Original:  astronaut floating in a nebula
Variant:   the astronaut pulled toward a black hole
```

Benefits:

- Avoids three visually identical scenes
- Auto-generates narrative tension (defamiliarization, twist)
- LLM acts as a "second screenwriter" exploring side plots

### 5. Frame-count rhythm (visual pacing)

| Scene | Frames | Duration | Editing role |
| --- | --- | --- | --- |
| Scene 1 | 441 (max) | 18.4 s | **Long shot** — establish the world, character, mood |
| Scene 2 | 241 | 10 s | **Mid-tempo** — transition, introduce a new element |
| Scene 3 | 241 | 10 s | **Resolution** — climax, close the loop |

This follows classical film editing: **slow opening, fast pivot, stable
denouement**. All numeric parameters reflect that.

### 6. Auto multi-link (one prompt → many scenes)

`AgnesTextNode` ② outputs to **three links simultaneously** (link 2, 3, 8):

```python
"links": [2, 3, 8]    # same output → Scene 1, Scene 2, Variant
```

The refined prompt is **shared across all scenes** without copy-paste.
This also lets each scene start at a different time (Scene 2 can wait for
Scene 1's last frame before kicking off).

### 7. Schema-enforced prompts

Every text node has a carefully designed `system_prompt` that constrains
the output:

- ①: *"Use the structure: [Subject] + [Action] + [Scene] + [Camera
  Movement] + [Lighting] + [Style]"*
- ②: *"specific about lighting ratios, camera lens type, film grain,
  color grading style, and depth of field"*
- ⑤: *"DIFFERENT scene that CONTINUES the narrative"*

**Schema-enforced prompting** gives more controllable output than open
prompts.

### 8. Scene chaining is opt-in

Scenes 2 and 3 have `image` input with `link: null` — disabled by
default. Two operating modes:

- **Clean mode** (default): Scene 1/2/3 each run independently. Concatenate
  with FFmpeg afterward. ~8 min runtime.
- **Continuity mode**: paste Scene 1's last frame into Scene 2's
  `image` widget, Scene 2's last frame into Scene 3's. Both become
  image-to-video with strong visual continuity. ~12 min runtime.

### 9. Visual groups + annotations

The workflow ships with **3 Note nodes** and **5 color groups** so anyone
(even a first-time user) can:

- Understand the whole flow in 30 seconds
- Know what each step does
- Know how to tune parameters (Pro Tips node)

### 10. Error isolation

If Scene 2 fails, Scene 1 is already on disk. There's **no
all-or-nothing** — failures are local, and you can re-queue a single
node by right-clicking → "Run this node".

---
