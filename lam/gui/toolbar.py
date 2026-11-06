"""Toolbar with action buttons."""
import tkinter as tk
from tkinter import ttk
from typing import Optional, Callable, Dict
from lam.gui.theme import COLORS, FONTS
from lam.gui.tooltip import ToolTip


class Toolbar(ttk.Frame):
    """Main toolbar with all action buttons."""

    def __init__(self, parent):
        super().__init__(parent, style="Dark.TFrame")
        self.buttons: Dict[str, tk.Button] = {}
        self._setup_ui()

    def _setup_ui(self):
        btn_cfg = {"font": FONTS["default"], "relief": "flat", "padx": 10, "pady": 5,
                   "bg": COLORS["bg_button"], "fg": COLORS["text"],
                   "activebackground": COLORS["bg_button_hover"],
                   "activeforeground": COLORS["text_bright"]}

        actions = [
            ("import", "📂 Import Files", "Load .bnk, .wpk, and .bin files into the project.", None),
            ("clear", "🗑 Clear Treeview", "Unload all current files and reset project state.", None),
            ("sep1", None, None, None),
            ("replace", "🔄 Replace", "Replace the currently selected audio.", None),
            ("add", "➕ Add", "Add a new audio file to the selected container.", None),
            ("remove", "🗑 Remove", "Remove the selected audio from the selected container.", None),
            ("sep2", None, None, None),
            ("validate", "✅ Validate", "Run structural validation checks before exporting", None),
            ("export", "💾 Export to WPK&BNK", "Export the modified files back to .wpk and .bnk formats", None),
            ("export_wwise", "🎛 Export Wwise Template", "Export loaded structures to Wwise project template.", None),
            ("package_wpk", "📦 Package WEMs to WPK", "Scan Wwise output, automatically patch BNKs and bundle WEMs into a game-ready package (WPK/BNK).", None),
            ("sep3", None, None, None),
            ("refresh", "🔃 Refresh", "Refresh the project hierarchy view", None),
        ]

        for key, label, tooltip_text, cmd in actions:
            if key.startswith("sep"):
                sep = ttk.Separator(self, orient=tk.VERTICAL)
                sep.pack(side=tk.LEFT, fill=tk.Y, padx=6, pady=4)
                continue

            if key == "export":
                cfg = {**btn_cfg, "bg": COLORS["bg_button_accent"],
                       "fg": COLORS["text_bright"],
                       "activebackground": COLORS["bg_button_accent_hover"]}
            elif key in ("remove", "clear"):
                cfg = {**btn_cfg, "bg": COLORS["bg_button_danger"],
                       "fg": COLORS["text_bright"]}
            else:
                cfg = btn_cfg.copy()

            btn = tk.Button(self, text=label, command=cmd, **cfg)
            btn.pack(side=tk.LEFT, padx=2, pady=4)
            self.buttons[key] = btn
            if tooltip_text:
                ToolTip(btn, tooltip_text)

    def set_command(self, key: str, command: Callable):
        if key in self.buttons:
            self.buttons[key].configure(command=command)

    def set_enabled(self, key: str, enabled: bool):
        if key in self.buttons:
            self.buttons[key].configure(state=tk.NORMAL if enabled else tk.DISABLED)
