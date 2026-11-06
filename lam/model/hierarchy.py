"""
Hierarchy reconstruction: builds the logical Wwise tree from parsed objects.
Event -> Action -> Sound/Container -> Media -> WEM
"""
from typing import List, Dict, Optional, Set
from dataclasses import dataclass, field
from lam.parsers.hirc_parser import HircObjectData
from lam.model.audio_entry import AudioEntry
from lam.core.hashing import HashLookup
from lam.core.logging import get_logger


@dataclass
class HierarchyNode:
    """A node in the reconstructed Wwise hierarchy tree."""
    node_id: str           # unique node key for TreeView
    object_id: int
    type_id: int
    type_name: str
    display_name: str
    resolved_name: str = ""
    wem_id: int = 0
    source_file: str = ""
    offset: int = 0
    size: int = 0
    mod_state: str = ""    # "", "modified", "added", "removed"
    children: List['HierarchyNode'] = field(default_factory=list)
    parent: Optional['HierarchyNode'] = None
    hirc_obj: Optional[HircObjectData] = None
    audio_entry: Optional[AudioEntry] = None

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    @property
    def child_count(self) -> int:
        return len(self.children)

    def add_child(self, child: 'HierarchyNode'):
        child.parent = self
        self.children.append(child)

    def find_by_id(self, object_id: int) -> Optional['HierarchyNode']:
        if self.object_id == object_id:
            return self
        for child in self.children:
            found = child.find_by_id(object_id)
            if found:
                return found
        return None

    def walk(self):
        """Depth-first traversal."""
        yield self
        for child in self.children:
            yield from child.walk()


class HierarchyBuilder:
    """Reconstructs the Wwise hierarchy from parsed HIRC objects."""

    def __init__(self):
        self.roots: List[HierarchyNode] = []
        self.all_nodes: Dict[int, HierarchyNode] = {}
        self.enabled_ids: Set[int] = set()
        self._ref_counter: int = 0
        self._log = get_logger()

    def build(self, objects: List[HircObjectData],
              hash_lookup: Optional[HashLookup] = None,
              audio_entries: Optional[Dict[int, AudioEntry]] = None,
              source_file: str = "") -> List[HierarchyNode]:
        """Build hierarchy from HIRC objects."""
        self._log.info("Hierarchy", f"Building hierarchy from {len(objects)} objects",
                       operation="build")
        self.all_nodes.clear()
        self.roots.clear()
        self.enabled_ids.clear()

        obj_map: Dict[int, HircObjectData] = {}
        for obj in objects:
            obj_map[obj.object_id] = obj

        # Populate self.enabled_ids recursively tracing from Events
        def mark_descendants(oid):
            if oid in self.enabled_ids:
                return
            self.enabled_ids.add(oid)
            obj = obj_map.get(oid)
            if obj:
                if obj.type_id == 2 and obj.media_id:
                    self.enabled_ids.add(obj.media_id)
                elif obj.type_id == 11:
                    if hasattr(obj, 'playlist_wems') and obj.playlist_wems:
                        for wid in obj.playlist_wems:
                            self.enabled_ids.add(wid)
                    elif obj.media_id:
                        self.enabled_ids.add(obj.media_id)
                for cid in getattr(obj, 'child_ids', []):
                    mark_descendants(cid)

        events = [o for o in objects if o.type_id == 4]
        actions = {o.object_id: o for o in objects if o.type_id == 3}
        for ev in events:
            self.enabled_ids.add(ev.object_id)
            for aid in getattr(ev, 'event_action_ids', []):
                self.enabled_ids.add(aid)
                act_obj = actions.get(aid)
                if act_obj:
                    if act_obj.target_id:
                        mark_descendants(act_obj.target_id)
                    if getattr(act_obj, 'state_group_id', 0):
                        for switch_obj in [o for o in objects if o.type_id == 12]:
                            if any(gid == act_obj.state_group_id for gid, gtype in getattr(switch_obj, 'switch_arguments', [])):
                                for key, audio_id in getattr(switch_obj, 'switch_nodes', []):
                                    if key == act_obj.target_state_id:
                                        mark_descendants(audio_id)
                    if getattr(act_obj, 'switch_group_id', 0):
                        for track_obj in [o for o in objects if o.type_id == 11]:
                            if track_obj.switch_group_id == act_obj.switch_group_id:
                                if act_obj.switch_state_id in getattr(track_obj, 'switch_ids', []):
                                    mark_descendants(track_obj.object_id)

        # Create nodes for all objects
        for obj in objects:
            name = ""
            if hash_lookup:
                resolved = hash_lookup.resolve(obj.object_id)
                if resolved:
                    name = resolved

            node = HierarchyNode(
                node_id=f"{obj.type_id}_{obj.object_id}",
                object_id=obj.object_id,
                type_id=obj.type_id,
                type_name=obj.type_name,
                display_name=name if name else f"{obj.type_name} [{obj.object_id}]",
                resolved_name=name,
                source_file=source_file,
                offset=obj.file_offset,
                hirc_obj=obj,
            )

            # Sound objects: attach media info
            if obj.type_id == 2 and obj.media_id:
                node.wem_id = obj.media_id
                node.size = obj.media_size
                if audio_entries and obj.media_id in audio_entries:
                    node.audio_entry = audio_entries[obj.media_id]

            # Music tracks: attach media info (supporting multiple subtracks)
            if obj.type_id == 11:
                if hasattr(obj, 'playlist_wems') and len(obj.playlist_wems) > 1:
                    for idx, wid in enumerate(obj.playlist_wems):
                        sub_node = HierarchyNode(
                            node_id=f"subtrack_{obj.object_id}_{idx}_{wid}",
                            object_id=obj.object_id,
                            type_id=2,  # Sound display
                            type_name="MusicSubtrack",
                            display_name=f"Subtrack {idx + 1}",
                            source_file=source_file,
                            wem_id=wid,
                            offset=obj.file_offset,
                        )
                        if audio_entries and wid in audio_entries:
                            sub_node.audio_entry = audio_entries[wid]
                            sub_node.size = audio_entries[wid].size
                        node.add_child(sub_node)
                elif obj.media_id:
                    node.wem_id = obj.media_id
                    node.size = obj.media_size
                    if audio_entries and obj.media_id in audio_entries:
                        node.audio_entry = audio_entries[obj.media_id]

            self.all_nodes[obj.object_id] = node

        # Build container -> children relationships
        containers = [o for o in objects if o.type_id in (5, 6, 7, 9, 10, 12, 13)]
        for cont in containers:
            cont_node = self.all_nodes[cont.object_id]
            for cid in cont.child_ids:
                if cid in self.all_nodes:
                    child_node = self.all_nodes[cid]
                    if child_node.parent is None:
                        cont_node.add_child(child_node)
                    else:
                        ref = self._make_ref(child_node)
                        cont_node.add_child(ref)

        # Build parent_id relationships for unparented nodes
        for obj in objects:
            if obj.parent_id and obj.parent_id in self.all_nodes:
                node = self.all_nodes[obj.object_id]
                if node.parent is None:
                    parent_node = self.all_nodes[obj.parent_id]
                    parent_node.add_child(node)

        # Propagate nice names down from playlists (13) to segments (10) and then to tracks (11)
        for pass_num in range(2):
            for cont in containers:
                cont_node = self.all_nodes[cont.object_id]
                if not cont_node.resolved_name and " [" not in cont_node.display_name:
                    continue  # Parent doesn't have a nice name
                nice_name = cont_node.resolved_name or cont_node.display_name.split(" (")[0]
                for child_node in cont_node.children:
                    if child_node.type_id in (10, 11) and not child_node.resolved_name:
                        child_node.display_name = f"{nice_name} ({child_node.type_name})"
                        child_node.resolved_name = nice_name

        # Build event -> action -> target chains
        events = [o for o in objects if o.type_id == 4]
        actions = {o.object_id: o for o in objects if o.type_id == 3}

        for evt in events:
            evt_node = self.all_nodes[evt.object_id]
            for aid in evt.event_action_ids:
                if aid in self.all_nodes:
                    action_node = self.all_nodes[aid]
                    if action_node.parent is None:
                        evt_node.add_child(action_node)
                    else:
                        # Action already parented (shared by multiple events)
                        ref = self._make_ref(action_node)
                        evt_node.add_child(ref)
                    # Link action -> target (with support for Set State & Set Switch relationship routing)
                    act_obj = actions.get(aid)
                    if act_obj:
                        resolved_targets = []
                        if act_obj.target_id in self.all_nodes:
                            resolved_targets.append(self.all_nodes[act_obj.target_id])
                        elif hasattr(act_obj, 'state_group_id') and act_obj.state_group_id:
                            # Set State routing matching CAkMusicSwitchCntr switch_arguments
                            for switch_obj in [o for o in objects if o.type_id == 12]:
                                if any(gid == act_obj.state_group_id for gid, gtype in switch_obj.switch_arguments):
                                    for key, audio_id in switch_obj.switch_nodes:
                                        if key == act_obj.target_state_id and audio_id in self.all_nodes:
                                            resolved_targets.append(self.all_nodes[audio_id])
                        elif hasattr(act_obj, 'switch_group_id') and act_obj.switch_group_id:
                            # Set Switch routing matching CAkMusicTrack switch variants
                            for track_obj in [o for o in objects if o.type_id == 11]:
                                if track_obj.switch_group_id == act_obj.switch_group_id:
                                    if act_obj.switch_state_id in track_obj.switch_ids:
                                        if track_obj.object_id in self.all_nodes:
                                            resolved_targets.append(self.all_nodes[track_obj.object_id])

                        actual_action = action_node if action_node.parent == evt_node else evt_node.children[-1]
                        for target_node in resolved_targets:
                            if evt_node.resolved_name and not target_node.resolved_name:
                                target_node.display_name = f"{evt_node.resolved_name} ({target_node.type_name})"
                            
                            if target_node.parent is None:
                                actual_action.add_child(target_node)
                            else:
                                ref = self._make_ref(target_node)
                                actual_action.add_child(ref)

        # Roots: events first, then unparented containers/mixers
        parented_ids: Set[int] = set()
        for node in self.all_nodes.values():
            for child in node.children:
                parented_ids.add(child.object_id)

        # Events are always roots
        for evt in events:
            self.roots.append(self.all_nodes[evt.object_id])
            parented_ids.add(evt.object_id)

        # Unparented actor-mixers, containers, sounds, and interactive music objects
        for obj in objects:
            if obj.object_id not in parented_ids and obj.type_id in (2, 5, 6, 7, 9, 10, 11, 12, 13):
                self.roots.append(self.all_nodes[obj.object_id])

        self._log.info("Hierarchy",
                       f"Built {len(self.roots)} root nodes, {len(self.all_nodes)} total",
                       operation="build_complete")
        return self.roots

    def _make_ref(self, original: HierarchyNode) -> HierarchyNode:
        """Create a shallow reference node that mirrors an already-parented node.

        This supports the DAG nature of the Wwise hierarchy where multiple
        actions/containers can reference the same target object.
        The reference node copies display info and recursively clones children.
        """
        self._ref_counter += 1
        ref = HierarchyNode(
            node_id=f"ref_{self._ref_counter}_{original.object_id}",
            object_id=original.object_id,
            type_id=original.type_id,
            type_name=original.type_name,
            display_name=original.display_name,
            resolved_name=original.resolved_name,
            wem_id=original.wem_id,
            source_file=original.source_file,
            offset=original.offset,
            size=original.size,
            mod_state=original.mod_state,
            hirc_obj=original.hirc_obj,
            audio_entry=original.audio_entry,
        )
        # Recursively clone children so the subtree displays correctly
        for child in original.children:
            child_ref = self._make_ref(child)
            ref.add_child(child_ref)
        return ref

    def find_node(self, object_id: int) -> Optional[HierarchyNode]:
        return self.all_nodes.get(object_id)

    def search(self, query: str) -> List[HierarchyNode]:
        """Search nodes by name or ID."""
        q = query.lower().strip()
        results = []
        for node in self.all_nodes.values():
            if q in node.display_name.lower() or q in str(node.object_id):
                results.append(node)
        return results[:100]  # limit results
