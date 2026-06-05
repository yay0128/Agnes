"""Convert a ComfyUI UI workflow JSON to the API prompt format used by /prompt.

Useful for testing workflow validity without running it, and for batch
submission of the same workflow to multiple ComfyUI instances.

Usage:
    python3 scripts/ui_to_api.py workflow.json > api_prompt.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def convert(wf: dict) -> dict:
    api: dict = {}
    for node in wf.get("nodes", []):
        nid = str(node["id"])
        api[nid] = {"class_type": node["type"], "inputs": {}}
        widgets = node.get("widgets_values", [])

        # Linked inputs (cable connections)
        for inp in node.get("inputs", []):
            if inp.get("link") is not None:
                for lk in wf.get("links", []):
                    if lk[0] == inp["link"]:
                        api[nid]["inputs"][inp["name"]] = [str(lk[1]), lk[2]]
                        break

        ntype = node["type"]

        if ntype == "AgnesTextNode":
            # widgets: [user_prompt, api_key, system_prompt, max_tokens, temperature]
            # user_prompt is removed from widgets if it's linked
            if "user_prompt" not in api[nid]["inputs"] and len(widgets) >= 1:
                api[nid]["inputs"]["user_prompt"] = widgets[0]
            w = widgets[1:] if "user_prompt" in api[nid]["inputs"] else widgets
            for i, key in enumerate(["api_key", "system_prompt", "max_tokens", "temperature"]):
                if i < len(w):
                    api[nid]["inputs"][key] = w[i]

        elif ntype == "AgnesImageNode":
            # Full: [prompt, size, api_key, input_image, edit_instruction,
            #        response_format, output_dir, skip_upload]
            # If prompt is linked, widgets becomes:
            #        [size, api_key, input_image, edit_instruction,
            #         response_format, output_dir, skip_upload]
            prompt_linked = "prompt" in api[nid]["inputs"]
            input_image_linked = "input_image" in api[nid]["inputs"]
            if not prompt_linked and len(widgets) >= 1:
                api[nid]["inputs"]["prompt"] = widgets[0]
            w = widgets[1:] if prompt_linked else widgets
            # Now w is [size, api_key, input_image, edit_instruction,
            #          response_format, output_dir, skip_upload]
            keys = ["size", "api_key", "input_image", "edit_instruction",
                    "response_format", "output_dir", "skip_upload"]
            idx = 0
            for key in keys:
                if idx >= len(w):
                    break
                if key == "input_image" and input_image_linked:
                    idx += 1
                    continue
                val = w[idx]
                if key == "skip_upload":
                    api[nid]["inputs"][key] = bool(val)
                else:
                    api[nid]["inputs"][key] = val
                idx += 1

        elif ntype == "AgnesVideoGenerateNode":
            # widgets: [api_key, image, height, width, num_frames, frame_rate,
            #          poll_interval, max_wait, output_dir]
            # prompt is forceInput (not a widget); image may be linked
            keys = ["api_key", "image", "height", "width", "num_frames",
                    "frame_rate", "poll_interval", "max_wait", "output_dir"]
            for i, key in enumerate(keys):
                if i >= len(widgets):
                    break
                if key == "image" and "image" in api[nid]["inputs"]:
                    continue
                api[nid]["inputs"][key] = widgets[i]

        # else: unknown node type — leave with just the linked inputs.

    return api


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workflow", type=Path,
                        help="UI workflow JSON file (e.g. workflow.json)")
    parser.add_argument("-o", "--output", type=Path,
                        help="Output file (default: stdout)")
    args = parser.parse_args()

    with args.workflow.open() as f:
        wf = json.load(f)

    api = convert(wf)
    text = json.dumps(api, indent=2)
    if args.output:
        args.output.write_text(text)
        print(f"Wrote {len(api)} nodes to {args.output}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
