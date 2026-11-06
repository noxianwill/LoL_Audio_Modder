"""Custom dark theme for LAM using ttk styling."""
import tkinter as tk
from tkinter import ttk

# Color palette
COLORS = {
    "bg_dark": "#0D1117",
    "bg_panel": "#161B22",
    "bg_input": "#1C2128",
    "bg_hover": "#1F2937",
    "bg_selected": "#1A3A5C",
    "bg_button": "#21262D",
    "bg_button_hover": "#30363D",
    "bg_button_accent": "#1F6FEB",
    "bg_button_accent_hover": "#388BFD",
    "bg_button_danger": "#DA3633",
    "border": "#30363D",
    "border_focus": "#58A6FF",
    "text": "#E6EDF3",
    "text_dim": "#8B949E",
    "text_bright": "#FFFFFF",
    "accent": "#58A6FF",
    "accent_gold": "#D29922",
    "success": "#3FB950",
    "warning": "#D29922",
    "error": "#F85149",
    "info": "#58A6FF",
    "tree_event": "#D2A8FF",
    "tree_action": "#79C0FF",
    "tree_sound": "#7EE787",
    "tree_container": "#FFA657",
    "tree_mixer": "#FF7B72",
    "tree_music": "#F778BA",
    "tree_modified": "#D29922",
    "tree_added": "#3FB950",
    "tree_removed": "#F85149",
    "scrollbar": "#30363D",
    "scrollbar_hover": "#484F58",
}

FONTS = {
    "default": ("Segoe UI", 10),
    "small": ("Segoe UI", 9),
    "heading": ("Segoe UI Semibold", 11),
    "title": ("Segoe UI Semibold", 14),
    "mono": ("Cascadia Code", 9),
    "mono_small": ("Cascadia Code", 8),
    "icon": ("Segoe UI", 12),
}


def apply_theme(root: tk.Tk):
    """Apply the dark theme to the root window and all ttk widgets."""
    style = ttk.Style()
    root.configure(bg=COLORS["bg_dark"])

    # General
    style.configure(".", background=COLORS["bg_dark"], foreground=COLORS["text"],
                    font=FONTS["default"], borderwidth=0, relief="flat")

    # Frame
    style.configure("TFrame", background=COLORS["bg_dark"])
    style.configure("Panel.TFrame", background=COLORS["bg_panel"])
    style.configure("Dark.TFrame", background=COLORS["bg_dark"])

    # Label
    style.configure("TLabel", background=COLORS["bg_dark"], foreground=COLORS["text"],
                    font=FONTS["default"])
    style.configure("Heading.TLabel", font=FONTS["heading"], foreground=COLORS["accent"])
    style.configure("Title.TLabel", font=FONTS["title"], foreground=COLORS["text_bright"])
    style.configure("Dim.TLabel", foreground=COLORS["text_dim"])
    style.configure("Panel.TLabel", background=COLORS["bg_panel"])
    style.configure("Status.TLabel", background=COLORS["bg_panel"],
                    foreground=COLORS["text_dim"], font=FONTS["small"])

    # Button
    style.configure("TButton", background=COLORS["bg_button"], foreground=COLORS["text"],
                    font=FONTS["default"], padding=(12, 6), borderwidth=1,
                    relief="solid")
    style.map("TButton",
              background=[("active", COLORS["bg_button_hover"]),
                         ("pressed", COLORS["bg_button_hover"])],
              relief=[("pressed", "sunken")])

    style.configure("Accent.TButton", background=COLORS["bg_button_accent"],
                    foreground=COLORS["text_bright"])
    style.map("Accent.TButton",
              background=[("active", COLORS["bg_button_accent_hover"])])

    style.configure("Danger.TButton", background=COLORS["bg_button_danger"],
                    foreground=COLORS["text_bright"])

    # Entry
    style.configure("TEntry", fieldbackground=COLORS["bg_input"],
                    foreground=COLORS["text"], insertcolor=COLORS["text"],
                    borderwidth=1, relief="solid")

    # Treeview
    style.configure("Treeview", background=COLORS["bg_panel"],
                    foreground=COLORS["text"], fieldbackground=COLORS["bg_panel"],
                    font=FONTS["default"], rowheight=26, borderwidth=0)
    style.configure("Treeview.Heading", background=COLORS["bg_button"],
                    foreground=COLORS["text_dim"], font=FONTS["small"],
                    borderwidth=1, relief="solid")
    style.map("Treeview",
              background=[("selected", COLORS["bg_selected"])],
              foreground=[("selected", COLORS["text_bright"])])

    # Notebook (tabs)
    style.configure("TNotebook", background=COLORS["bg_dark"], borderwidth=0)
    style.configure("TNotebook.Tab", background=COLORS["bg_button"],
                    foreground=COLORS["text_dim"], padding=(14, 6),
                    font=FONTS["default"])
    style.map("TNotebook.Tab",
              background=[("selected", COLORS["bg_panel"])],
              foreground=[("selected", COLORS["text_bright"])])

    # Progressbar
    style.configure("TProgressbar", background=COLORS["accent"],
                    troughcolor=COLORS["bg_input"], borderwidth=0, thickness=4)

    # Separator
    style.configure("TSeparator", background=COLORS["border"])

    # PanedWindow
    style.configure("TPanedwindow", background=COLORS["border"])
    style.configure("Sash", sashthickness=4, background=COLORS["border"])

    # Scrollbar
    style.configure("Vertical.TScrollbar", background=COLORS["scrollbar"],
                    troughcolor=COLORS["bg_dark"], borderwidth=0, arrowsize=0)
    style.map("Vertical.TScrollbar",
              background=[("active", COLORS["scrollbar_hover"])])

    # LabelFrame
    style.configure("TLabelframe", background=COLORS["bg_panel"],
                    foreground=COLORS["text_dim"], borderwidth=1, relief="solid")
    style.configure("TLabelframe.Label", background=COLORS["bg_panel"],
                    foreground=COLORS["accent"], font=FONTS["small"])

    # Scale
    style.configure("TScale", background=COLORS["bg_dark"],
                    troughcolor=COLORS["bg_input"])

    return style
