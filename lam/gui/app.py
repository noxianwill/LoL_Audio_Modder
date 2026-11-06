"""
Main application window for League Audio Modder.
Assembles all panels into a resizable, multi-pane layout.
"""
import os
import tkinter as tk
from tkinter import ttk
from typing import Optional

from lam import __version__, __app_name__
from lam.gui.theme import apply_theme, COLORS, FONTS
from lam.gui.toolbar import Toolbar
from lam.gui.tree_view import TreeViewPanel
from lam.gui.property_panel import PropertyPanel
from lam.gui.log_panel import LogPanel
from lam.gui.audio_panel import AudioPanel
from lam.gui import dialogs
from lam.model.project import Project
from lam.model.hierarchy import HierarchyNode
from lam.engine.audio_player import AudioPlayer
from lam.engine.modifier import Modifier
from lam.engine.rebuilder import Rebuilder
from lam.engine.validator import Validator
from lam.engine.wpk_packager import WpkPackager
from lam.engine.bnk_patcher import BnkPatcher
from lam.core.logging import get_logger


class LamApp:
    """Main application controller."""

    def __init__(self):
        self._log = get_logger()
        self._closing = False
        self._log.info("App", f"{__app_name__} v{__version__} starting",
                       operation="init")

        # Core state
        self.project = Project()
        self.player = AudioPlayer()
        self.modifier = Modifier(self.project)

        # Build UI
        self.root = tk.Tk()
        self.root.title(f"{__app_name__} v{__version__}")
        self.root.geometry("1400x850")
        self.root.minsize(900, 600)
        self.root.configure(bg=COLORS["bg_dark"])

        # Set icon via window attributes
        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        apply_theme(self.root)
        self._build_layout()
        self._bind_commands()

        # Configure hash update progress reporting
        def on_hash_progress(msg):
            self.root.after(0, lambda: self._status_var.set(msg))
            if getattr(self.project.hash_updater, "cache_loaded", False) or getattr(self.project.hash_updater, "is_complete", False):
                if hasattr(self, "toolbar"):
                    self.root.after(0, lambda: self.toolbar.set_enabled("import", True))
        self.project.hash_updater.set_progress_callback(on_hash_progress)
        self.project.start_hash_update()

        # Configure project loading progress reporting
        self._project_spinner_idx = 0
        def on_project_progress(msg):
            frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            spinner = frames[self._project_spinner_idx % len(frames)]
            self._project_spinner_idx += 1
            self._status_var.set(f"{spinner} {msg}")
            self.root.update()
        self.project.set_progress_callback(on_project_progress)

        self._log.info("App", "UI initialized", operation="init")

    def _build_layout(self):
        """Build the multi-pane layout."""
        # Title bar / header
        header = ttk.Frame(self.root, style="Dark.TFrame")
        header.pack(fill=tk.X)

        title_frame = ttk.Frame(header, style="Dark.TFrame")
        title_frame.pack(fill=tk.X, padx=12, pady=(8, 0))

        ttk.Label(title_frame, text=f"⚡ {__app_name__}",
                  style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(title_frame, text=f"v{__version__}",
                  style="Dim.TLabel", font=FONTS["small"]
                  ).pack(side=tk.LEFT, padx=(8, 0), pady=(4, 0))

        self._status_var = tk.StringVar(value="Ready")
        ttk.Label(title_frame, textvariable=self._status_var,
                  style="Status.TLabel").pack(side=tk.RIGHT, pady=(4, 0))

        # Toolbar
        self.toolbar = Toolbar(header)
        self.toolbar.pack(fill=tk.X, padx=8, pady=(6, 4))
        self.toolbar.set_enabled("import", False) # Disable until hashes are ready

        ttk.Separator(header, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Main content: horizontal paned window
        main_pane = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pane.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Left panel: TreeView
        left_frame = ttk.Frame(main_pane, style="Panel.TFrame")
        self.tree_panel = TreeViewPanel(left_frame, on_select=self._on_node_select)
        self.tree_panel.pack(fill=tk.BOTH, expand=True)
        main_pane.add(left_frame, weight=3)

        # Right side: vertical paned window
        right_pane = ttk.PanedWindow(main_pane, orient=tk.VERTICAL)
        main_pane.add(right_pane, weight=2)

        # Right top: Properties + Audio preview
        right_top = ttk.Frame(right_pane, style="Panel.TFrame")

        self.property_panel = PropertyPanel(right_top)
        self.property_panel.pack(fill=tk.BOTH, expand=True)

        ttk.Separator(right_top, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8)

        self.audio_panel = AudioPanel(right_top, self.player)
        self.audio_panel.pack(fill=tk.X)

        right_pane.add(right_top, weight=2)

        # Right bottom: Log panel
        log_frame = ttk.Frame(right_pane, style="Panel.TFrame")
        self.log_panel = LogPanel(log_frame)
        self.log_panel.pack(fill=tk.BOTH, expand=True)
        right_pane.add(log_frame, weight=1)

        # Status bar
        status_bar = ttk.Frame(self.root, style="Panel.TFrame")
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self._files_var = tk.StringVar(value="No files loaded")
        ttk.Label(status_bar, textvariable=self._files_var,
                  style="Status.TLabel", font=FONTS["mono_small"]
                  ).pack(side=tk.LEFT, padx=8, pady=4)

        self._objects_var = tk.StringVar(value="")
        ttk.Label(status_bar, textvariable=self._objects_var,
                  style="Status.TLabel", font=FONTS["mono_small"]
                  ).pack(side=tk.RIGHT, padx=8, pady=4)

    def _bind_commands(self):
        """Wire toolbar buttons and tree context menu to handlers."""
        self.toolbar.set_command("import", self._cmd_import)
        self.toolbar.set_command("clear", self._cmd_clear)
        self.toolbar.set_command("replace", self._cmd_replace)
        self.toolbar.set_command("add", self._cmd_add)
        self.toolbar.set_command("remove", self._cmd_remove)
        self.toolbar.set_command("validate", self._cmd_validate)
        self.toolbar.set_command("export", self._cmd_export)
        self.toolbar.set_command("export_wwise", self._cmd_export_wwise)
        self.toolbar.set_command("package_wpk", self._cmd_package_wpk)
        self.toolbar.set_command("refresh", self._cmd_refresh)

        self.tree_panel.on_play = self._play_node
        self.tree_panel.on_replace = lambda n: self._cmd_replace()
        self.tree_panel.on_add = lambda n: self._cmd_add()
        self.tree_panel.on_remove = lambda n: self._cmd_remove()

        # Keyboard shortcuts
        self.root.bind("<Control-o>", lambda e: self._cmd_import())
        self.root.bind("<Control-s>", lambda e: self._cmd_export())
        self.root.bind("<Control-Shift-E>", lambda e: self._cmd_export_wwise())
        self.root.bind("<Control-r>", lambda e: self._cmd_refresh())
        self.root.bind("<F5>", lambda e: self._cmd_refresh())

        # Handle window close button (X) gracefully
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_node_select(self, node: HierarchyNode):
        """Handle tree node selection."""
        self.property_panel.show_node(node)

        # Auto-load audio preview for Sound/MusicTrack nodes
        if node.wem_id:
            payload = self.project.get_wem_payload(node.wem_id)
            if payload:
                self.audio_panel.load_wem(node.wem_id, payload)
                self.audio_panel.play()
            else:
                self.audio_panel.clear()
        else:
            self.audio_panel.clear()

    def _play_node(self, node: HierarchyNode):
        """Play audio for a node."""
        if node.wem_id:
            payload = self.project.get_wem_payload(node.wem_id)
            if payload:
                self.audio_panel.load_wem(node.wem_id, payload)
                self.audio_panel.play()

    def _cmd_clear(self):
        self._status_var.set("Clearing project...")
        self.root.update()

        self.project.clear()
        self.tree_panel.populate([])
        self.audio_panel.clear()
        self.property_panel.clear()
        self._update_status_bar()
        self._status_var.set("Ready")
        self._log.info("App", "Project cleared via UI", operation="clear")

    def _cmd_import(self):
        if not getattr(self.project.hash_updater, "is_complete", False) and not getattr(self.project.hash_updater, "cache_loaded", False):
            dialogs.show_error(self.root, "Import Files", "Hashes are still downloading/loading in the background. Please wait for completion.")
            return
        paths = dialogs.ask_import_files()
        if not paths:
            return
        self._status_var.set("Importing...")
        self.root.update()

        try:
            # Clear previous project and UI state to avoid cumulative state piling
            self.project.clear()
            self.audio_panel.clear()
            self.property_panel.clear()

            self.project.auto_load(list(paths))
            self.tree_panel.populate(self.project.hierarchy_roots)
            self._update_status_bar()
            self._status_var.set("Import complete")
            self._log.info("App", f"Imported {len(paths)} files", operation="import")
        except Exception as e:
            self._log.error("App", f"Import failed: {e}", operation="import")
            dialogs.show_error(self.root, "Import Error", str(e))
            self._status_var.set("Import failed")

    def _cmd_replace(self):
        node = self.tree_panel.get_selected_node()
        if not node or not node.wem_id:
            dialogs.show_error(self.root, "Replace", "Select a Sound node with a WEM first.")
            return

        path = dialogs.ask_audio_file()
        if not path:
            return

        # Warn immediately if the file is not a proper Wwise WEM.
        # WAV/OGG stored as WEM data will produce a BNK/WPK that bnk-gui
        # and the game cannot decode ("broken wem" error).
        ext = os.path.splitext(path)[1].lower()
        if ext in ('.wav', '.ogg'):
            if not dialogs.ask_confirm(
                self.root,
                "⚠️  Not a WEM file",
                f"'{os.path.basename(path)}' is a {ext.upper()} file.\n\n"
                "Only Wwise-encoded .wem files work in-game and in bnk-gui.\n"
                "Importing a WAV/OGG will create a BROKEN BNK/WPK export.\n\n"
                "Continue anyway?"
            ):
                return

        self._status_var.set("Replacing audio...")
        self.root.update()

        if self.modifier.replace_audio(node.wem_id, path):
            node.mod_state = "modified"
            self.tree_panel.refresh_node(node)  # In-place update — no tree rebuild
            self._status_var.set("Audio replaced")
        else:
            dialogs.show_error(self.root, "Replace Error", "Failed to replace audio.")
            self._status_var.set("Replace failed")

    def _cmd_add(self):
        node = self.tree_panel.get_selected_node()
        if not node:
            dialogs.show_error(self.root, "Add", "Select a container node first.")
            return

        if node.type_id not in (5, 6, 7, 9, 10):
            dialogs.show_error(self.root, "Add",
                "Select a container (RandomSeq, Switch, ActorMixer, Blend, MusicSegment).")
            return

        paths = dialogs.ask_audio_files()
        if not paths:
            return

        # Warn if any selected file is not a proper Wwise WEM.
        non_wem = [p for p in paths if os.path.splitext(p)[1].lower() in ('.wav', '.ogg')]
        if non_wem:
            names = ', '.join(os.path.basename(p) for p in non_wem)
            if not dialogs.ask_confirm(
                self.root,
                "⚠️  Non-WEM files selected",
                f"These files are NOT Wwise WEM files:\n{names}\n\n"
                "Only Wwise-encoded .wem files work in-game and in bnk-gui.\n"
                "Importing WAV/OGG will create a BROKEN BNK/WPK export.\n\n"
                "Continue anyway?"
            ):
                return

        self._status_var.set("Adding audio...")
        self.root.update()

        added_ids = []
        failed_paths = []
        for path in paths:
            new_id = self.modifier.add_audio(node.object_id, path)
            if new_id:
                added_ids.append(new_id)
            else:
                failed_paths.append(path)

        if added_ids:
            self.project.build_hierarchy()
            self.tree_panel.populate(self.project.hierarchy_roots)
            self._update_status_bar()
            if failed_paths:
                dialogs.show_error(self.root, "Add Error", 
                                   f"Successfully added: {len(added_ids)} files.\n"
                                   f"Failed to add: {len(failed_paths)} files.")
                self._status_var.set(f"Added {len(added_ids)} WEMs (some failed)")
            else:
                self._status_var.set(f"Added {len(added_ids)} WEMs")
        else:
            dialogs.show_error(self.root, "Add Error", "Failed to add any audio files.")
            self._status_var.set("Add failed")

    def _cmd_remove(self):
        node = self.tree_panel.get_selected_node()
        if not node or not node.wem_id:
            dialogs.show_error(self.root, "Remove", "Select a Sound node with a WEM first.")
            return

        if not dialogs.ask_confirm(self.root, "Confirm Remove",
                                    f"Remove WEM {node.wem_id}?\nThis cannot be undone."):
            return

        self._status_var.set("Removing...")
        self.root.update()

        if self.modifier.remove_audio(node.wem_id):
            self.project.build_hierarchy()
            self.tree_panel.populate(self.project.hierarchy_roots)
            self._update_status_bar()
            self._status_var.set("Audio removed")
        else:
            dialogs.show_error(self.root, "Remove Error", "Failed to remove audio.")
            self._status_var.set("Remove failed")



    def _cmd_validate(self):
        self._status_var.set("Validating...")
        self.root.update()

        validator = Validator(self.project)
        results = validator.validate_all()
        dialogs.show_validation_results(self.root, results)

        if validator.has_errors:
            self._status_var.set(f"Validation: {validator.error_count} errors")
        else:
            self._status_var.set("Validation passed ✓")

    def _cmd_export(self):
        out_dir = dialogs.ask_export_directory()
        if not out_dir:
            return
        self._cmd_export_to(out_dir)

    def _cmd_export_to(self, out_dir: str):
        # Validate first
        validator = Validator(self.project)
        results = validator.validate_all()
        if validator.has_errors:
            if not dialogs.ask_confirm(self.root, "Validation Errors",
                    f"Found {validator.error_count} errors. Export anyway?"):
                return

        self._status_var.set("Exporting...")
        self.root.update()

        try:
            rebuilder = Rebuilder(self.project)
            outputs = rebuilder.rebuild_all(out_dir)
            self._status_var.set(f"Exported {len(outputs)} files")
            dialogs.show_info(self.root, "Export Complete",
                             f"Exported {len(outputs)} files to:\n{out_dir}")
        except Exception as e:
            self._log.error("App", f"Export failed: {e}", operation="export")
            dialogs.show_error(self.root, "Export Error", str(e))
            self._status_var.set("Export failed")

    def _cmd_package_wpk(self):
        """Unified Smart Packager for Wwise generated output."""
        import threading
        from lam.engine.smart_packager import SmartPackager

        input_folder = dialogs.ask_wem_folder()
        if not input_folder:
            return

        export_dir = dialogs.ask_export_directory()
        if not export_dir:
            return

        self._status_var.set("Smart Packaging Wwise Output...")
        self.root.update()

        def worker():
            try:
                packager = SmartPackager(self.project)
                processed = packager.package_wwise_output(input_folder, export_dir)
                msg = "Successfully packaged and patched files:\n\n" + "\n".join(processed)
                self.root.after(0, lambda: self._status_var.set("Packaging Complete!"))
                self.root.after(0, lambda: dialogs.show_info(self.root, "Success", msg))
            except Exception as e:
                self._log.error("App", f"Smart packaging failed: {e}", operation="package_wpk")
                self.root.after(0, lambda: self._status_var.set("Packaging Failed"))
                self.root.after(0, lambda: dialogs.show_error(self.root, "Packaging Error", str(e)))

        threading.Thread(target=worker, daemon=True).start()

    def _cmd_export_wwise(self):
        wproj_dir = dialogs.ask_wwise_project()
        if not wproj_dir:
            return

        self._status_var.set("Exporting Wwise Template...")
        self.root.update()

        try:
            self.project.export_wwise_template(wproj_dir)
            self._status_var.set("Wwise Template Exported")
            dialogs.show_info(self.root, "Export Complete",
                             f"Successfully exported structures to Wwise project at:\n{wproj_dir}")
        except Exception as e:
            self._log.error("App", f"Wwise Export failed: {e}", operation="export_wwise")
            dialogs.show_error(self.root, "Export Error", str(e))
            self._status_var.set("Wwise Export failed")

    def _cmd_refresh(self):
        self.project.build_hierarchy()
        self.tree_panel.populate(self.project.hierarchy_roots)
        self._update_status_bar()
        self._status_var.set("Refreshed")

    def _update_status_bar(self):
        self._files_var.set(
            f"Files: {self.project.loaded_file_count}  ·  "
            f"WEMs: {self.project.total_wem_count}"
        )
        self._objects_var.set(f"Objects: {self.project.total_object_count}")

    def _on_close(self):
        """Gracefully shut down background threads and destroy the window."""
        if self._closing:
            return
        self._closing = True
        self._log.info("App", "Shutdown requested", operation="shutdown")
        try:
            self.player.cleanup()
        except Exception:
            pass
        try:
            if hasattr(self.project, "hash_updater"):
                self.project.hash_updater.stop()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        """Start the main event loop."""
        self._log.info("App", "Starting main loop", operation="run")
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            self._on_close()
        self._log.info("App", "Application closed", operation="shutdown")

    def destroy(self):
        self._on_close()
