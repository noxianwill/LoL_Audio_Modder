"""TreeView panel for displaying the Wwise hierarchy."""
import tkinter as tk
from tkinter import ttk
from typing import Optional, List, Callable, Set
from lam.model.hierarchy import HierarchyNode
from lam.gui.theme import COLORS, FONTS
from lam.gui.tooltip import ToolTip


class TreeViewPanel(ttk.Frame):
    """Searchable, filterable TreeView for the Wwise hierarchy."""

    def __init__(self, parent, on_select: Optional[Callable] = None):
        super().__init__(parent, style="Panel.TFrame")
        self._on_select = on_select
        self._nodes_map = {}  # tree_id -> HierarchyNode
        self._all_roots: List[HierarchyNode] = []
        self._setup_ui()

    def _setup_ui(self):
        # Search bar
        search_frame = ttk.Frame(self, style="Panel.TFrame")
        search_frame.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Label(search_frame, text="🔍", style="Panel.TLabel",
                  font=FONTS["icon"]).pack(side=tk.LEFT, padx=(0, 4))

        # Collapse all button on the right
        self._btn_collapse_all = tk.Button(search_frame, text="➖", bg=COLORS["bg_button"], fg=COLORS["text"],
                                           activebackground=COLORS["bg_button_hover"], activeforeground=COLORS["text_bright"],
                                           relief="flat", font=FONTS["small"], command=self.collapse_all,
                                           highlightthickness=0, bd=0, padx=6, pady=2)
        self._btn_collapse_all.pack(side=tk.RIGHT, padx=(2, 0))
        ToolTip(self._btn_collapse_all, "Collapse All Nodes")

        # Expand all button on the right
        self._btn_expand_all = tk.Button(search_frame, text="➕", bg=COLORS["bg_button"], fg=COLORS["text"],
                                         activebackground=COLORS["bg_button_hover"], activeforeground=COLORS["text_bright"],
                                         relief="flat", font=FONTS["small"], command=self.expand_all,
                                         highlightthickness=0, bd=0, padx=6, pady=2)
        self._btn_expand_all.pack(side=tk.RIGHT, padx=(2, 2))
        ToolTip(self._btn_expand_all, "Expand All Nodes")

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search)
        self._search_entry = tk.Entry(search_frame, textvariable=self._search_var,
                                       bg=COLORS["bg_input"], fg=COLORS["text"],
                                       insertbackground=COLORS["text"],
                                       relief="flat", font=FONTS["default"],
                                       highlightthickness=1,
                                       highlightcolor=COLORS["border_focus"],
                                       highlightbackground=COLORS["border"])
        self._search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        # Info label
        self._info_var = tk.StringVar(value="No files loaded")
        ttk.Label(self, textvariable=self._info_var, style="Dim.TLabel",
                  font=FONTS["small"]).pack(fill=tk.X, padx=8, pady=(0, 4))


        # Treeview
        tree_frame = ttk.Frame(self, style="Panel.TFrame")
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        self._tree = ttk.Treeview(tree_frame, show="tree", selectmode="extended")

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                   command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._tree.bind("<Button-3>", self._on_right_click)

        # Type prefix tags for coloring
        self._tree.tag_configure("event", foreground=COLORS["tree_event"])
        self._tree.tag_configure("action", foreground=COLORS["tree_action"])
        self._tree.tag_configure("sound", foreground=COLORS["tree_sound"])
        self._tree.tag_configure("container", foreground=COLORS["tree_container"])
        self._tree.tag_configure("mixer", foreground=COLORS["tree_mixer"])
        self._tree.tag_configure("music", foreground=COLORS["tree_music"])
        self._tree.tag_configure("modified", foreground=COLORS["tree_modified"])
        self._tree.tag_configure("added", foreground=COLORS["tree_added"])
        self._tree.tag_configure("removed", foreground=COLORS["tree_removed"])
        self._tree.tag_configure("default", foreground=COLORS["text"])
        self._tree.tag_configure("search_match", background="#1F3A60", foreground=COLORS["text_bright"])

        # Context menu
        self._context_menu = tk.Menu(self, tearoff=0,
                                      bg=COLORS["bg_panel"], fg=COLORS["text"],
                                      activebackground=COLORS["bg_selected"],
                                      activeforeground=COLORS["text_bright"],
                                      font=FONTS["default"])
        self._context_menu.add_command(label="▶  Play Audio", command=self._ctx_play)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="📂  Expand Selected", command=self._ctx_expand)
        self._context_menu.add_command(label="📂  Expand Recursively", command=self._ctx_expand_recursive)
        self._context_menu.add_command(label="📁  Collapse Selected", command=self._ctx_collapse)
        self._context_menu.add_separator()
        self._context_menu.add_command(label="🔄  Replace Audio", command=self._ctx_replace)
        self._context_menu.add_command(label="➕  Add Audio", command=self._ctx_add)
        self._context_menu.add_command(label="🗑  Remove Audio", command=self._ctx_remove)
        self._context_menu.add_separator()

        # Copy submenu
        self._copy_menu = tk.Menu(self._context_menu, tearoff=0,
                                  bg=COLORS["bg_panel"], fg=COLORS["text"],
                                  activebackground=COLORS["bg_selected"],
                                  activeforeground=COLORS["text_bright"],
                                  font=FONTS["default"])
        self._copy_menu.add_command(label="📄  Copy Display Name", command=self._ctx_copy_name)
        self._copy_menu.add_command(label="🆔  Copy Object ID", command=self._ctx_copy_id)
        self._copy_menu.add_command(label="🎵  Copy WEM ID", command=self._ctx_copy_wem_id)
        self._copy_menu.add_command(label="ℹ️  Copy Type", command=self._ctx_copy_type)
        self._copy_menu.add_command(label="📂  Copy Hierarchy Path", command=self._ctx_copy_path)
        self._copy_menu.add_command(label="📁  Copy Hierarchy Tree", command=self._ctx_copy_tree)
        self._copy_menu.add_command(label="📝  Copy Row Text", command=self._ctx_copy_row_text)

        self._context_menu.add_cascade(label="📋  Copy", menu=self._copy_menu)

        # Callbacks for context menu actions (set by app)
        self.on_play: Optional[Callable] = None
        self.on_replace: Optional[Callable] = None
        self.on_add: Optional[Callable] = None
        self.on_remove: Optional[Callable] = None

    def _update_tree(self):
        # 1. Determine active roots
        tab_roots = self._all_roots
            
        # 2. Apply search filter if query is present
        query = self._search_var.get().strip()
        
        # Save selection state
        selected_path = self._get_selected_path()
        self._to_select_id = None
        
        self._tree.delete(*self._tree.get_children())
        self._nodes_map.clear()
        
        if query:
            q = query.lower()
            for root in tab_roots:
                self._search_insert("", root, q, force=False, parent_path=(), selected_path=selected_path)
            
            if self._to_select_id:
                self._tree.selection_set(self._to_select_id)
                self._tree.see(self._to_select_id)
        else:
            # Normal population without search query
            open_paths = self._get_open_paths()
            for root in sorted(tab_roots, key=lambda r: r.display_name.lower()):
                self._insert_node("", root, depth=0, parent_path=(),
                                 open_paths=open_paths, selected_path=selected_path)
                                 
            if self._to_select_id:
                self._tree.selection_set(self._to_select_id)
                self._tree.see(self._to_select_id)
                
        # 3. Update info counts
        self._info_var.set(f"Loaded: {len(self._all_roots)} objects")

    def populate(self, roots: List[HierarchyNode]):
        """Populate the tree with hierarchy nodes."""
        self._all_roots = roots
        self._update_tree()

    def _get_open_paths(self) -> Set[tuple]:
        open_paths = set()
        def walk(item_id):
            if item_id:
                is_open = self._tree.item(item_id, "open")
                if is_open:
                    path = self._get_item_path(item_id)
                    open_paths.add(path)
            for child in self._tree.get_children(item_id):
                walk(child)
        walk("")
        return open_paths

    def _get_selected_path(self) -> Optional[tuple]:
        sel = self._tree.selection()
        if sel:
            return self._get_item_path(sel[0])
        return None

    def _get_item_path(self, item_id) -> tuple:
        path = []
        curr = item_id
        while curr:
            node = self._nodes_map.get(curr)
            if node:
                # Use only stable identifiers — wem_id is intentionally excluded
                # because it changes on replace, which would break open/selection restoration.
                path.append((node.type_id, node.object_id, node.display_name))
            else:
                path.append(curr)
            curr = self._tree.parent(curr)
        path.reverse()
        return tuple(path)

    def _insert_node(self, parent_id: str, node: HierarchyNode, depth: int = 0,
                     parent_path: tuple = (), open_paths: Set[tuple] = None,
                     selected_path: Optional[tuple] = None):
        """Recursively insert a hierarchy node into the treeview."""
        tag = self._get_tag(node)
        text = self._get_node_display_text(node)

        # Construct path for this node (wem_id excluded — see _get_item_path)
        node_key = (node.type_id, node.object_id, node.display_name)
        current_path = parent_path + (node_key,)

        # Determine if this node should be open
        is_open = False
        if open_paths and current_path in open_paths:
            is_open = True

        tree_id = self._tree.insert(parent_id, tk.END, text=text, tags=(tag,),
                                     open=is_open)
        self._nodes_map[tree_id] = node
        node.node_id = tree_id

        # Check if this node matches the saved selected path
        if selected_path and current_path == selected_path:
            self._to_select_id = tree_id

        for child in sorted(node.children, key=lambda c: c.display_name.lower()):
            self._insert_node(tree_id, child, depth + 1, parent_path=current_path,
                             open_paths=open_paths, selected_path=selected_path)

    def _get_tag(self, node: HierarchyNode) -> str:
        if node.mod_state == "added":
            return "added"
        if node.mod_state == "modified":
            return "modified"
        if node.mod_state == "removed":
            return "removed"
        type_tags = {4: "event", 3: "action", 2: "sound", 11: "sound",
                     5: "container", 6: "container", 7: "mixer", 9: "container",
                     10: "music", 12: "music", 13: "music"}
        return type_tags.get(node.type_id, "default")

    def _get_prefix(self, node: HierarchyNode) -> str:
        prefixes = {
            4: "🎯", 3: "⚡", 2: "🔊", 5: "📦", 6: "🔀", 7: "🎭",
            9: "🔄", 10: "🎵", 11: "🎶", 12: "🎼", 13: "📋",
            1: "⚙️", 8: "🔌",
        }
        return prefixes.get(node.type_id, "●")

    def _on_tree_select(self, event):
        sel = self._tree.selection()
        if sel and self._on_select:
            node = self._nodes_map.get(sel[0])
            if node:
                self._on_select(node)

    def _on_search(self, *args):
        self._update_tree()

    def _search_insert(self, parent_id: str, node: HierarchyNode, query: str, force: bool = False,
                       parent_path: tuple = (), selected_path: Optional[tuple] = None) -> bool:
        # Check if the node itself matches the query
        self_matched = (
            query in node.display_name.lower() or
            query in str(node.object_id) or
            (node.wem_id is not None and query in str(node.wem_id)) or
            query in node.type_name.lower()
        )
        
        tag = self._get_tag(node)
        text = self._get_node_display_text(node)
            
        # Highlight directly matching nodes
        tags_list = [tag]
        if self_matched:
            tags_list.append("search_match")
            
        # Construct path for this node (wem_id excluded — see _get_item_path)
        node_key = (node.type_id, node.object_id, node.display_name)
        current_path = parent_path + (node_key,)

        # Start collapsed by default
        tree_id = self._tree.insert(parent_id, tk.END, text=text, tags=tuple(tags_list), open=False)
        self._nodes_map[tree_id] = node
        node.node_id = tree_id

        # Check if this node matches the saved selected path
        if selected_path and current_path == selected_path:
            self._to_select_id = tree_id

        # Recursively search children
        child_has_direct_match = False
        next_force = force or self_matched
        for child in sorted(node.children, key=lambda c: c.display_name.lower()):
            if self._search_insert(tree_id, child, query, force=next_force,
                                   parent_path=current_path, selected_path=selected_path):
                child_has_direct_match = True

        # Keep this node if it matches, if an ancestor matched (force), or if a descendant matched directly
        keep = force or self_matched or child_has_direct_match
        
        if keep:
            # Only expand if a descendant matched directly (not via force)
            if child_has_direct_match:
                self._tree.item(tree_id, open=True)
            return self_matched or child_has_direct_match
        else:
            self._tree.delete(tree_id)
            if tree_id in self._nodes_map:
                del self._nodes_map[tree_id]
            # Clear selection reference if this node was selected but got filtered out
            if self._to_select_id == tree_id:
                self._to_select_id = None
            return False

    def get_selected_node(self) -> Optional[HierarchyNode]:
        sel = self._tree.selection()
        if sel:
            return self._nodes_map.get(sel[0])
        return None

    def get_selected_nodes(self) -> List[HierarchyNode]:
        sel = self._tree.selection()
        nodes = []
        for item_id in sel:
            node = self._nodes_map.get(item_id)
            if node:
                nodes.append(node)
        return nodes

    def _get_node_display_text(self, node: HierarchyNode) -> str:
        prefix = self._get_prefix(node)
        text = f"{prefix} {node.display_name}"
        if node.wem_id:
            text += f"  [{node.wem_id}]"
        if node.mod_state:
            text += f"  {node.mod_state}"
        return text

    def _get_node_path_str(self, node: HierarchyNode) -> str:
        path_nodes = []
        curr = node
        while curr:
            text = curr.display_name
            if curr.wem_id:
                text += f" [{curr.wem_id}]"
            path_nodes.append(text)
            curr = curr.parent
        path_nodes.reverse()
        return " > ".join(path_nodes)

    def _format_node_tree(self, node: HierarchyNode, indent_level: int = 0) -> List[str]:
        text = f"{'  ' * indent_level}{self._get_node_display_text(node)}"
        lines = [text]
        for child in sorted(node.children, key=lambda c: c.display_name.lower()):
            lines.extend(self._format_node_tree(child, indent_level + 1))
        return lines

    def _on_right_click(self, event):
        item = self._tree.identify_row(event.y)
        if item:
            current_selection = self._tree.selection()
            if item not in current_selection:
                self._tree.selection_set(item)
                        
            self._context_menu.post(event.x_root, event.y_root)

    def _ctx_play(self):
        node = self.get_selected_node()
        if node and self.on_play:
            self.on_play(node)

    def _ctx_expand(self):
        for item_id in self._tree.selection():
            self._set_node_open(item_id, True, recursive=False)

    def _ctx_expand_recursive(self):
        for item_id in self._tree.selection():
            self._set_node_open(item_id, True, recursive=True)

    def _ctx_collapse(self):
        for item_id in self._tree.selection():
            self._set_node_open(item_id, False, recursive=True)

    def _set_node_open(self, item_id, open_state: bool, recursive: bool = True):
        self._tree.item(item_id, open=open_state)
        if recursive:
            for child in self._tree.get_children(item_id):
                self._set_node_open(child, open_state, recursive)

    def expand_all(self):
        """Expand all nodes in the tree view recursively."""
        for child in self._tree.get_children():
            self._set_node_open(child, True, recursive=True)

    def collapse_all(self):
        """Collapse all nodes in the tree view recursively."""
        for child in self._tree.get_children():
            self._set_node_open(child, False, recursive=True)

    def _ctx_replace(self):
        node = self.get_selected_node()
        if node and self.on_replace:
            self.on_replace(node)

    def _ctx_add(self):
        node = self.get_selected_node()
        if node and self.on_add:
            self.on_add(node)

    def _ctx_remove(self):
        node = self.get_selected_node()
        if node and self.on_remove:
            self.on_remove(node)



    def _ctx_copy_name(self):
        nodes = self.get_selected_nodes()
        if nodes:
            text = "\n".join(node.display_name for node in nodes)
            self.clipboard_clear()
            self.clipboard_append(text)

    def _ctx_copy_id(self):
        nodes = self.get_selected_nodes()
        if nodes:
            text = "\n".join(str(node.object_id) for node in nodes)
            self.clipboard_clear()
            self.clipboard_append(text)

    def _ctx_copy_wem_id(self):
        nodes = self.get_selected_nodes()
        wem_ids = [str(node.wem_id) for node in nodes if node.wem_id]
        if wem_ids:
            text = "\n".join(wem_ids)
            self.clipboard_clear()
            self.clipboard_append(text)

    def _ctx_copy_type(self):
        nodes = self.get_selected_nodes()
        if nodes:
            text = "\n".join(node.type_name for node in nodes)
            self.clipboard_clear()
            self.clipboard_append(text)

    def _ctx_copy_path(self):
        nodes = self.get_selected_nodes()
        if nodes:
            text = "\n".join(self._get_node_path_str(node) for node in nodes)
            self.clipboard_clear()
            self.clipboard_append(text)

    def _ctx_copy_tree(self):
        nodes = self.get_selected_nodes()
        if nodes:
            lines = []
            for node in nodes:
                lines.extend(self._format_node_tree(node, indent_level=0))
            text = "\n".join(lines)
            self.clipboard_clear()
            self.clipboard_append(text)

    def _ctx_copy_row_text(self):
        nodes = self.get_selected_nodes()
        if nodes:
            text = "\n".join(self._get_node_display_text(node) for node in nodes)
            self.clipboard_clear()
            self.clipboard_append(text)

    def refresh_node(self, node):
        """Update only the display text of a single node in-place, without rebuilding the tree.
        
        Use this for lightweight changes (e.g. wem_id or mod_state change on an existing node)
        where the tree structure itself hasn't changed.
        """
        tree_id = getattr(node, "node_id", None)
        if tree_id and tree_id in self._nodes_map:
            tag = self._get_tag(node)
            text = self._get_node_display_text(node)
            self._tree.item(tree_id, text=text, tags=(tag,))
            self._tree.selection_set(tree_id)
            self._tree.see(tree_id)

    def refresh(self):
        """Refresh the tree from current roots (full rebuild, preserving open/selection state)."""
        self._update_tree()
