"""Dialog windows for import, export, validation results, and errors."""
import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Optional, Tuple
from lam.gui.theme import COLORS, FONTS
from lam.engine.validator import ValidationResult, VSeverity


def ask_import_files() -> List[str]:
    """Show file picker for importing game assets."""
    paths = filedialog.askopenfilenames(
        title="Import Game Assets",
        filetypes=[
            ("All Supported", "*.bin *.bnk *.wpk"),
            ("Map Files", "*.bin"),
            ("Sound Banks", "*.bnk"),
            ("Audio Packages", "*.wpk"),
            ("All Files", "*.*"),
        ]
    )
    return list(paths)


def ask_audio_file() -> Optional[str]:
    """Show file picker for audio import (replace).
    Only .wem files produce a game-compatible BNK/WPK.
    WAV/OGG are listed for convenience but will NOT work in-game.
    """
    path = filedialog.askopenfilename(
        title="Select Audio File  [.wem recommended — WAV/OGG will NOT work in-game]",
        filetypes=[
            ("Wwise Audio (game-compatible)", "*.wem"),
            ("WAV — NOT game-compatible", "*.wav"),
            ("OGG — NOT game-compatible", "*.ogg"),
            ("All Files", "*.*"),
        ]
    )
    return path if path else None


def ask_audio_files() -> List[str]:
    """Show file picker for multiple audio import (add).
    Only .wem files produce a game-compatible BNK/WPK.
    WAV/OGG are listed for convenience but will NOT work in-game.
    """
    paths = filedialog.askopenfilenames(
        title="Select Audio Files  [.wem recommended — WAV/OGG will NOT work in-game]",
        filetypes=[
            ("Wwise Audio (game-compatible)", "*.wem"),
            ("WAV — NOT game-compatible", "*.wav"),
            ("OGG — NOT game-compatible", "*.ogg"),
            ("All Files", "*.*"),
        ]
    )
    return list(paths)


def ask_export_directory() -> Optional[str]:
    """Show directory picker for export."""
    path = filedialog.askdirectory(title="Select Export Directory")
    return path if path else None


def ask_wem_folder() -> Optional[str]:
    """Show folder picker for WEM source folder."""
    path = filedialog.askdirectory(title="Select Folder containing .wem files")
    return path if path else None


def ask_save_wpk(default_name: str = "output.wpk") -> Optional[str]:
    """Show save file picker for WPK file."""
    path = filedialog.asksaveasfilename(
        title="Save WPK File",
        filetypes=[("WPK files", "*.wpk"), ("All Files", "*.*")],
        defaultextension=".wpk",
        initialfile=default_name
    )
    return path if path else None


def ask_wwise_project() -> Optional[str]:
    """Show file picker for Wwise project (.wproj) file."""
    path = filedialog.askopenfilename(
        title="Select Wwise Project File",
        filetypes=[
            ("Wwise Project", "*.wproj"),
            ("All Files", "*.*")
        ]
    )
    return os.path.dirname(path) if path else None


def show_validation_results(parent: tk.Widget, results: List[ValidationResult]):
    """Show validation results in a dialog."""
    win = tk.Toplevel(parent)
    win.title("Validation Results")
    win.configure(bg=COLORS["bg_dark"])
    win.geometry("600x400")
    win.transient(parent)

    errors = sum(1 for r in results if r.severity == VSeverity.ERROR)
    warnings = sum(1 for r in results if r.severity == VSeverity.WARNING)
    infos = sum(1 for r in results if r.severity == VSeverity.INFO)

    summary = f"Results: {errors} errors, {warnings} warnings, {infos} info"
    color = COLORS["success"] if errors == 0 else COLORS["error"]
    ttk.Label(win, text=summary, foreground=color,
              font=FONTS["heading"]).pack(padx=10, pady=10)

    text = tk.Text(win, bg=COLORS["bg_input"], fg=COLORS["text"],
                   font=FONTS["mono_small"], relief="flat", wrap=tk.WORD)
    text.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

    text.tag_configure("ERROR", foreground=COLORS["error"])
    text.tag_configure("WARNING", foreground=COLORS["warning"])
    text.tag_configure("INFO", foreground=COLORS["info"])

    for r in results:
        tag = r.severity.name
        line = f"[{r.severity.name}] [{r.category}] {r.message}\n"
        text.insert(tk.END, line, tag)

    text.configure(state=tk.DISABLED)

    ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 10))


def show_error(parent: tk.Widget, title: str, message: str):
    messagebox.showerror(title, message, parent=parent)


def show_info(parent: tk.Widget, title: str, message: str):
    messagebox.showinfo(title, message, parent=parent)


def ask_confirm(parent: tk.Widget, title: str, message: str) -> bool:
    return messagebox.askyesno(title, message, parent=parent)


def ask_bnk_file(title: str = "Select BNK File") -> Optional[str]:
    """Show file picker for a single .bnk file."""
    path = filedialog.askopenfilename(
        title=title,
        filetypes=[
            ("SoundBank files", "*.bnk"),
            ("All Files", "*.*"),
        ]
    )
    return path if path else None
