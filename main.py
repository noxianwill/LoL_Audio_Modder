#!/usr/bin/env python3
"""
League Audio Modder (LAM) — Entry Point

A professional-grade Wwise audio reconstruction and modding suite
for League of Legends asset files.

Usage:
    python main.py
    python main.py <file1.bnk> <file2.wpk> ...
"""
import sys
import os

# Ensure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lam.gui.app import LamApp
from lam.core.logging import get_logger


def main():
    logger = get_logger()
    logger.info("Main", "League Audio Modder starting", operation="main")

    app = LamApp()

    # Auto-load files passed as command-line arguments
    if len(sys.argv) > 1:
        paths = [os.path.abspath(p) for p in sys.argv[1:] if os.path.isfile(p)]
        if paths:
            logger.info("Main", f"Auto-loading {len(paths)} files from CLI",
                       operation="auto_load")
            try:
                app.project.auto_load(paths)
                app.tree_panel.populate(app.project.hierarchy_roots)
                app._update_status_bar()
            except Exception as e:
                logger.error("Main", f"Auto-load failed: {e}", operation="auto_load")

    try:
        app.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
