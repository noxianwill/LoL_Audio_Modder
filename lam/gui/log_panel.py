"""Live log viewer panel with severity filtering."""
import tkinter as tk
from tkinter import ttk
from typing import Optional
from lam.core.logging import LamLogger, LogEntry, LogLevel, get_logger
from lam.gui.theme import COLORS, FONTS


class LogPanel(ttk.Frame):
    """Live scrolling log display with filtering."""

    def __init__(self, parent, logger: Optional[LamLogger] = None):
        super().__init__(parent, style="Panel.TFrame")
        self._logger = logger or get_logger()
        self._auto_scroll = True
        self._min_level = LogLevel.INFO
        self._setup_ui()
        self._logger.subscribe(self._on_log_entry)

    def _setup_ui(self):
        # Header bar
        hdr = ttk.Frame(self, style="Panel.TFrame")
        hdr.pack(fill=tk.X, padx=6, pady=(6, 2))

        ttk.Label(hdr, text="Log", style="Heading.TLabel",
                  background=COLORS["bg_panel"]).pack(side=tk.LEFT)

        # Filter buttons
        for level, label in [(LogLevel.DEBUG, "DBG"), (LogLevel.INFO, "INF"),
                              (LogLevel.WARN, "WRN"), (LogLevel.ERROR, "ERR")]:
            btn = tk.Button(hdr, text=label, font=FONTS["mono_small"],
                           bg=COLORS["bg_button"], fg=COLORS["text_dim"],
                           activebackground=COLORS["bg_button_hover"],
                           activeforeground=COLORS["text"], relief="flat",
                           padx=6, pady=1,
                           command=lambda l=level: self._set_filter(l))
            btn.pack(side=tk.RIGHT, padx=1)

        # Clear button
        tk.Button(hdr, text="Clear", font=FONTS["small"],
                  bg=COLORS["bg_button"], fg=COLORS["text_dim"],
                  activebackground=COLORS["bg_button_hover"],
                  relief="flat", padx=6, pady=1,
                  command=self._clear).pack(side=tk.RIGHT, padx=(0, 6))

        # Text widget
        text_frame = ttk.Frame(self, style="Panel.TFrame")
        text_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

        self._text = tk.Text(text_frame, wrap=tk.WORD, state=tk.DISABLED,
                              bg=COLORS["bg_input"], fg=COLORS["text"],
                              font=FONTS["mono_small"], relief="flat",
                              insertbackground=COLORS["text"],
                              highlightthickness=0, padx=6, pady=4)

        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL,
                                   command=self._text.yview)
        self._text.configure(yscrollcommand=scrollbar.set)
        self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Tags for severity colors
        self._text.tag_configure("DEBUG", foreground=COLORS["text_dim"])
        self._text.tag_configure("INFO", foreground=COLORS["info"])
        self._text.tag_configure("WARN", foreground=COLORS["warning"])
        self._text.tag_configure("ERROR", foreground=COLORS["error"])
        self._text.tag_configure("FATAL", foreground=COLORS["error"])
        self._text.tag_configure("timestamp", foreground=COLORS["text_dim"])

    def _on_log_entry(self, entry: LogEntry):
        """Called when a new log entry is added."""
        if entry.level < self._min_level:
            return
        try:
            self._text.after(0, self._append_entry, entry)
        except Exception:
            pass

    def _append_entry(self, entry: LogEntry):
        self._text.configure(state=tk.NORMAL)
        tag = entry.level.name
        line = entry.format_short() + "\n"
        self._text.insert(tk.END, line, tag)
        if self._auto_scroll:
            self._text.see(tk.END)
        self._text.configure(state=tk.DISABLED)

    def _set_filter(self, level: LogLevel):
        self._min_level = level
        self._refresh()

    def _refresh(self):
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        for entry in self._logger.get_entries(level=self._min_level):
            tag = entry.level.name
            self._text.insert(tk.END, entry.format_short() + "\n", tag)
        if self._auto_scroll:
            self._text.see(tk.END)
        self._text.configure(state=tk.DISABLED)

    def _clear(self):
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        self._text.configure(state=tk.DISABLED)
