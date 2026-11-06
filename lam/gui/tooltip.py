import tkinter as tk
from lam.gui.theme import COLORS, FONTS

class ToolTip:
    """A simple tooltip implementation for Tkinter widgets."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.id = None
        self.x = self.y = 0

        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hide_tooltip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(500, self.show_tooltip)

    def unschedule(self):
        id_ = self.id
        self.id = None
        if id_:
            self.widget.after_cancel(id_)

    def show_tooltip(self, event=None):
        if self.tooltip_window or not self.text:
            return
        
        x, y, cx, cy = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        
        # Make sure it's always on top
        try:
            tw.attributes("-topmost", True)
        except Exception:
            pass

        label = tk.Label(tw, text=self.text, justify=tk.LEFT,
                         background=COLORS["bg_panel"], fg=COLORS["text"],
                         relief=tk.SOLID, borderwidth=1,
                         font=FONTS["small"])
        label.pack(ipadx=4, ipady=2)

    def hide_tooltip(self):
        tw = self.tooltip_window
        self.tooltip_window = None
        if tw:
            tw.destroy()
