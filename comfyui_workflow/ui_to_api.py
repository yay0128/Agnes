"""Convert a ComfyUI UI workflow JSON to the API prompt format used by /prompt.

Useful for testing workflow validity without running it, and for batch
submission of the same workflow to multiple ComfyUI instances.

Usage:
    python3 scripts/ui_to_api.py workflow.json > api_prompt.json
    python3 scripts/ui_to_api.py workflow.json --validate   # dry-run, no output

The converter defensively validates widget counts against the actual
INPUT_TYPES of each node, catching the "widgets_values mismatch" bug
that caused the douyin workflow to fail silently.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


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
    parser.add_argument("--validate", action="store_true",
                        help="Only validate the workflow, don't convert. "
                             "Exits 0 on success, 1 on errors.")
    args = parser.parse_args()

    with args.workflow.open() as f:
        wf = json.load(f)

    if args.validate:
        errors = validate_workflow(wf)
        if errors:
            print(f"Validation FAILED for {args.workflow}:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        node_count = len(wf.get("nodes", []))
        print(f"OK: {node_count} nodes, all widget counts match INPUT_TYPES",
              file=sys.stderr)
        return 0

    api = convert(wf)
    text = json.dumps(api, indent=2)
    if args.output:
        args.output.write_text(text)
        print(f"Wrote {len(api)} nodes to {args.output}", file=sys.stderr)
    else:
        print(text)
    return 0


def _input_types_for(node_type: str) -> dict[str, Any] | None:
    """Look up INPUT_TYPES for a node class.

    Tries ComfyUI's NODE_CLASS_MAPPINGS first (if available in the
    current Python env), then falls back to a small hardcoded table for
    our own Agnes nodes. Returns None if the class is unknown.
    """
    # Try ComfyUI's registry if we're running inside it
    try:
        import nodes  # ComfyUI's nodes module
        cls = nodes.NODE_CLASS_MAPPINGS.get(node_type)
        if cls is not None:
            return cls.INPUT_TYPES()
    except ImportError:
        pass

    # Fallback: hardcoded INPUT_TYPES for the Agnes nodes. Mirrors the
    # order in comfyui_workflow/custom_nodes/agnes_api/agnes_nodes.py.
    AGNES_INPUT_TYPES: dict[str, dict] = {
        "AgnesTextNode": {
            "required": {"user_prompt": ("STRING", {})},
            "optional": {
                "api_key": ("STRING", {}),
                "system_prompt": ("STRING", {}),
                "max_tokens": ("INT", {}),
                "temperature": ("FLOAT", {}),
            },
        },
        "AgnesImageNode": {
            "required": {
                "prompt": ("STRING", {}),
                "size": ("STRING", {}),
            },
            "optional": {
                "api_key": ("STRING", {}),
                "input_image": ("STRING", {}),
                "edit_instruction": ("STRING", {}),
                "response_format": ("STRING", {}),
                "output_dir": ("STRING", {}),
                "skip_upload": ("BOOLEAN", {}),
            },
        },
        "AgnesVideoGenerateNode": {
            "required": {"prompt": ("STRING", {"forceInput": True})},
            "optional": {
                "api_key": ("STRING", {}),
                "image": ("STRING", {}),
                "height": ("INT", {}),
                "width": ("INT", {}),
                "num_frames": ("INT", {}),
                "frame_rate": ("INT", {}),
                "poll_interval": ("INT", {}),
                "max_wait": ("INT", {}),
                "output_dir": ("STRING", {}),
            },
        },
    }
    return AGNES_INPUT_TYPES.get(node_type)


def _expected_widget_count(node: dict) -> tuple[int, list[str]]:
    """Return (expected_count, list_of_forceInput_names) for a node.

    ComfyUI's behavior for widgets_values:
      - forceInput required inputs (e.g. video.prompt) are NEVER widgets
      - All other inputs (required + optional) DO appear in widgets_values,
        even if they're also connected via a link (the empty slot remains,
        the link value just overrides)
    """
    ntype = node["type"]
    it = _input_types_for(ntype)
    if it is None:
        return -1, []

    required = it.get("required", {})
    optional = it.get("optional", {})

    expected = 0
    force_input_names: list[str] = []
    for name, (ntype_val, cfg) in required.items():
        if isinstance(cfg, dict) and cfg.get("forceInput"):
            force_input_names.append(name)
            continue
        expected += 1
    expected += len(optional)
    return expected, force_input_names


def validate_workflow(wf: dict) -> list[str]:
    """Return a list of validation errors (empty = valid).

    Checks each node's widgets_values length against the expected count
    derived from INPUT_TYPES. ComfyUI's behavior:
      - forceInput required inputs (e.g. video.prompt) are NEVER widgets
      - All other inputs (required + optional) DO appear in widgets_values,
        even if also connected via a link (the empty slot remains,
        the link value just overrides at execution time)

    This catches the douyin workflow bug where widgets_values was
    missing one field — ComfyUI's serializer then misread the widgets
    array, shifted everything by 1, and interpreted the long prompt
    text as the 'size' field, causing immediate ValueError.
    """
    errors: list[str] = []
    for node in wf.get("nodes", []):
        ntype = node["type"]
        nid = node.get("id", "?")
        title = node.get("title", "")[:40]
        widgets = node.get("widgets_values", [])

        expected, force_inputs = _expected_widget_count(node)
        if expected < 0:
            # Unknown node type, skip
            continue
        if len(widgets) != expected:
            errors.append(
                f"Node {nid} ({ntype}, '{title}'): "
                f"widgets_values has {len(widgets)} items, expected {expected} "
                f"(forceInput skipped: {force_inputs or 'none'})"
            )
    return errors


if __name__ == "__main__":
    raise SystemExit(main())
