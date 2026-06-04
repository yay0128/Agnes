#!/usr/bin/env bash
#
# ComfyUI Agnes Setup — installs the custom node, stages workflows,
# and configures the API key for a given ComfyUI installation.
#
# Usage:
#   ./install.sh /Users/oly/Documents/ComfyUI
#   ./install.sh ~/Documents/ComfyUI
#   COMFYUI=~/Documents/ComfyUI ./install.sh
#
# If AGNES_API_KEY is set in the environment it will be used;
# otherwise the script prompts for it interactively.

set -euo pipefail

COMFYUI="${COMFYUI:-${1:?Usage: install.sh <ComfyUI-install-path>}}"
NODE_DIR="$COMFYUI/custom_nodes/agnes_api"
WORKFLOW_DIR="$COMFYUI/user/default/workflows"

# ---------------------------------------------------------------------------
# Resolve API key
# ---------------------------------------------------------------------------
if [[ -n "${AGNES_API_KEY:-}" ]]; then
    API_KEY="$AGNES_API_KEY"
else
    read -rsp "Enter AGNES_API_KEY (hidden): " API_KEY
    echo
fi
if [[ "$API_KEY" == "YOUR_API_KEY" || -z "$API_KEY" ]]; then
    echo "ERROR: AGNES_API_KEY is missing or placeholder. Aborting."
    exit 1
fi

# ---------------------------------------------------------------------------
# 1. Custom node
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "→ Installing custom node to $NODE_DIR"
if [[ -d "$NODE_DIR" ]]; then
    echo "  (already exists — will overwrite)"
fi
cp -R "$SCRIPT_DIR/custom_nodes/agnes_api" "$COMFYUI/custom_nodes/"
echo "  OK: $(ls "$NODE_DIR" | wc -l | tr -d ' ') files"

# ---------------------------------------------------------------------------
# 2. Workflows (stage both)
# ---------------------------------------------------------------------------
echo "→ Staging workflows to $WORKFLOW_DIR"
mkdir -p "$WORKFLOW_DIR"
cp -f "$SCRIPT_DIR/workflow.json" "$WORKFLOW_DIR/Agnes_Text_to_Video.json"
cp -f "$SCRIPT_DIR/workflow_advanced.json" "$WORKFLOW_DIR/Agnes_MultiScene_Cinematic_Pipeline.json"
echo "  OK: 2 workflows staged"

# ---------------------------------------------------------------------------
# 3. .env (auto-loaded by custom node at import time)
# ---------------------------------------------------------------------------
echo "→ Writing .env to $COMFYUI/.env"
cat > "$COMFYUI/.env" <<EOF
# Agnes AI credentials — auto-loaded by custom_nodes/agnes_api
# Get your API key at https://platform.agnes-ai.com/
AGNES_API_KEY=$API_KEY
EOF
chmod 600 "$COMFYUI/.env"
echo "  OK: .env written (mode 600)"

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "Setup complete!"
echo ""
echo "  Custom node: $NODE_DIR"
echo "  Workflows:   $WORKFLOW_DIR/"
echo "  API key:     $COMFYUI/.env (mode 600, hidden)"
echo ""
echo "Next steps:"
echo "  1. Launch (or restart) ComfyUI"
echo "  2. Open the browser (usually http://localhost:8188)"
echo "  3. Drag Agnes_Text_to_Video.json OR"
echo "     select from Workflows → Agnes_MultiScene_Cinematic_Pipeline"
echo "  4. Edit the user_prompt widget"
echo "  5. Click 'Queue Prompt'"
echo "═══════════════════════════════════════════════════════════════"
