#!/usr/bin/env python3
"""Validate all ComfyUI workflow JSONs in the repo against their nodes' INPUT_TYPES.

Catches the "widgets_values count mismatch" bug that caused the douyin
workflow to fail silently: ComfyUI's serializer misread widgets when
their count didn't match the expected count from INPUT_TYPES, shifting
everything by 1 and interpreting the long prompt text as the 'size'
field.

Run as a pre-commit hook or in CI:
    python3 scripts/validate_workflows.py
    echo "exit: $?"   # 0 = pass, 1 = errors

See POST_MORTEM_douyin_workflow_widget_order.md for the full story.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_DIR = ROOT / "comfyui_workflow"

# Reuse the validator from ui_to_api.py so we have a single source of truth
sys.path.insert(0, str(ROOT / "scripts"))
from ui_to_api import validate_workflow  # type: ignore


def main() -> int:
    workflow_files = sorted(WORKFLOW_DIR.glob("*.json"))
    if not workflow_files:
        print(f"No workflow JSONs found in {WORKFLOW_DIR}", file=sys.stderr)
        return 1

    all_ok = True
    for wf_path in workflow_files:
        try:
            with wf_path.open() as f:
                wf = json.load(f)
        except json.JSONDecodeError as e:
            print(f"FAIL: {wf_path.name} is not valid JSON: {e}")
            all_ok = False
            continue

        errors = validate_workflow(wf)
        if errors:
            print(f"FAIL: {wf_path.name}")
            for e in errors:
                print(f"  - {e}")
            all_ok = False
        else:
            node_count = len(wf.get("nodes", []))
            print(f"OK:   {wf_path.name} ({node_count} nodes)")

    print()
    if all_ok:
        print(f"All {len(workflow_files)} workflows validate cleanly.")
        return 0
    print("One or more workflows have validation errors. See above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
