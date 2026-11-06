"""Audio preview panel with playback controls."""
import tkinter as tk
from tkinter import ttk
from typing import Optional
from lam.engine.audio_player import AudioPlayer
from lam.gui.theme import COLORS, FONTS


class AudioPanel(ttk.Frame):
    """Audio preview with play/pause/stop controls and progress."""

    def __init__(self, parent, player: AudioPlayer):
        super().__init__(parent, style="Panel.TFrame")
        self._player = player
        self._current_wem_id: int = 0
        self._update_job = None
        self._setup_ui()

    def _setup_ui(self):
        ttk.Label(self, text="Audio Preview", style="Heading.TLabel",
                  background=COLORS["bg_panel"]).pack(fill=tk.X, padx=10, pady=(10, 6))

        # Info
        self._info_var = tk.StringVar(value="No audio selected")
        ttk.Label(self, textvariable=self._info_var, style="Dim.TLabel",
                  font=FONTS["small"], background=COLORS["bg_panel"]
                  ).pack(fill=tk.X, padx=10)

        # Progress bar
        self._progress = ttk.Progressbar(self, mode="determinate",
                                          style="TProgressbar", maximum=100)
        self._progress.pack(fill=tk.X, padx=10, pady=8)

        # Time label
        self._time_var = tk.StringVar(value="00:00 / 00:00")
        ttk.Label(self, textvariable=self._time_var, style="Dim.TLabel",
                  font=FONTS["mono_small"], background=COLORS["bg_panel"]
                  ).pack(fill=tk.X, padx=10)

        # Controls
        ctrl = ttk.Frame(self, style="Panel.TFrame")
        ctrl.pack(fill=tk.X, padx=10, pady=(8, 10))

        btn_style = {"font": FONTS["icon"], "bg": COLORS["bg_button"],
                     "fg": COLORS["text"], "activebackground": COLORS["bg_button_hover"],
                     "activeforeground": COLORS["text_bright"], "relief": "flat",
                     "padx": 14, "pady": 4}

        tk.Button(ctrl, text="⏹", command=self._stop, **btn_style).pack(side=tk.LEFT, padx=2)
        self._play_btn = tk.Button(ctrl, text="▶", command=self._toggle_play, **btn_style)
        self._play_btn.pack(side=tk.LEFT, padx=2)

    def load_wem(self, wem_id: int, wem_data: bytes):
        """Load a WEM for preview."""
        self._stop()
        self._current_wem_id = wem_id
        size_kb = len(wem_data) / 1024
        self._info_var.set(f"WEM {wem_id}  ·  {size_kb:.1f} KB")
        self._wem_data = wem_data
        self._progress["value"] = 0
        self._time_var.set("00:00 / 00:00")

    def play(self):
        """Force play the currently loaded WEM."""
        if hasattr(self, '_wem_data'):
            self._stop()
            self._player.play_wem(self._wem_data)
            self._play_btn.configure(text="⏸")
            self._start_progress_update()

    def _toggle_play(self):
        if self._player.is_playing:
            if self._player.is_paused:
                self._player.resume()
                self._play_btn.configure(text="⏸")
            else:
                self._player.pause()
                self._play_btn.configure(text="▶")
        elif hasattr(self, '_wem_data'):
            self._player.play_wem(self._wem_data)
            self._play_btn.configure(text="⏸")
            self._start_progress_update()

    def _stop(self):
        self._player.stop()
        self._play_btn.configure(text="▶")
        self._progress["value"] = 0
        self._time_var.set("00:00 / 00:00")
        if self._update_job:
            self.after_cancel(self._update_job)
            self._update_job = None

    def _start_progress_update(self):
        if self._update_job:
            self.after_cancel(self._update_job)
        self._update_progress()

    def _update_progress(self):
        if self._player.is_playing:
            pos = self._player.get_position_ms()
            dur = self._player.duration_ms
            if dur > 0:
                self._progress["value"] = (pos / dur) * 100
                self._time_var.set(f"{self._fmt_time(pos)} / {self._fmt_time(dur)}")
            self._update_job = self.after(100, self._update_progress)
        else:
            self._play_btn.configure(text="▶")

    def _fmt_time(self, ms: int) -> str:
        s = ms // 1000
        return f"{s // 60:02d}:{s % 60:02d}"

    def clear(self):
        self._stop()
        self._info_var.set("No audio selected")
        self._current_wem_id = 0
