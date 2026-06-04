"""Agnes AI custom nodes for ComfyUI.

Drop this folder into <ComfyUI>/custom_nodes/ and restart ComfyUI.
The two nodes will appear under the "Agnes" category in the node menu.
"""
from .agnes_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]
