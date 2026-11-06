"""Property inspector panel."""
import tkinter as tk
from tkinter import ttk
from typing import Optional
from lam.model.hierarchy import HierarchyNode
from lam.gui.theme import COLORS, FONTS


class PropertyPanel(ttk.Frame):
    """Displays properties of the selected hierarchy node."""

    def __init__(self, parent):
        super().__init__(parent, style="Panel.TFrame")
        self._setup_ui()

    def _setup_ui(self):
        ttk.Label(self, text="Properties", style="Heading.TLabel",
                  background=COLORS["bg_panel"]).pack(fill=tk.X, padx=10, pady=(10, 6))

        self._props_frame = ttk.Frame(self, style="Panel.TFrame")
        self._props_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._rows = {}
        fields = [
            ("Type", "type"), ("Object ID", "oid"), ("Name", "name"),
            ("WEM ID", "wem"), ("Media Size", "media_size"),
            ("Source Type", "src_type"), ("Parent ID", "parent"),
            ("Bus ID", "bus"), ("Children", "children"),
            ("Source File", "file"), ("Offset", "offset"), ("State", "state"),
        ]
        for i, (label, key) in enumerate(fields):
            lbl = ttk.Label(self._props_frame, text=label, style="Dim.TLabel",
                           font=FONTS["small"], background=COLORS["bg_panel"])
            lbl.grid(row=i, column=0, sticky=tk.W, pady=2, padx=(0, 10))

            val = tk.Label(self._props_frame, text="—", anchor=tk.W,
                          bg=COLORS["bg_panel"], fg=COLORS["text"],
                          font=FONTS["default"])
            val.grid(row=i, column=1, sticky=tk.W + tk.E, pady=2)
            self._rows[key] = val

        self._props_frame.columnconfigure(1, weight=1)
        self.clear()

    def show_node(self, node: HierarchyNode):
        """Display properties for a hierarchy node."""
        self._set("type", node.type_name)
        self._set("oid", str(node.object_id))
        self._set("name", node.resolved_name or "—")
        self._set("wem", str(node.wem_id) if node.wem_id else "—")

        hirc = node.hirc_obj
        if hirc:
            self._set("media_size", f"{hirc.media_size:,} bytes" if hirc.media_size else "—")
            src_types = {0: "Embedded", 1: "Streamed", 2: "Prefetched"}
            self._set("src_type", src_types.get(hirc.source_type, "—") if hirc.source_type else "—")
            self._set("parent", str(hirc.parent_id) if hirc.parent_id else "—")
            self._set("bus", str(hirc.bus_id) if hirc.bus_id else "—")
            self._set("children", str(len(hirc.child_ids)) if hirc.child_ids else "0")
        else:
            for k in ("media_size", "src_type", "parent", "bus", "children"):
                self._set(k, "—")

        self._set("file", node.source_file.split("\\")[-1] if node.source_file else "—")
        self._set("offset", f"0x{node.offset:X}" if node.offset else "—")
        self._set("state", node.mod_state if node.mod_state else "Original")

        # Color the state
        state_colors = {"modified": COLORS["tree_modified"],
                       "added": COLORS["tree_added"],
                       "removed": COLORS["tree_removed"]}
        color = state_colors.get(node.mod_state, COLORS["text"])
        self._rows["state"].configure(fg=color)

    def clear(self):
        for val in self._rows.values():
            val.configure(text="—", fg=COLORS["text_dim"])

    def _set(self, key: str, text: str):
        if key in self._rows:
            self._rows[key].configure(text=text, fg=COLORS["text"])
