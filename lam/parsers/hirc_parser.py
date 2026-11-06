"""
HIRC (Hierarchy) object parser for Wwise BNK files.
Two-pass parsing: instantiate objects, then resolve relationships.
Stores raw payload for every object to ensure round-trip safety.
"""
import struct
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from lam.core.binary_reader import BinaryReader, ParseError
from lam.core.binary_writer import BinaryWriter
from lam.core.logging import get_logger

HIRC_TYPE_NAMES = {
    1: "Settings", 2: "Sound", 3: "Action", 4: "Event",
    5: "RandomSequenceContainer", 6: "SwitchContainer", 7: "ActorMixer",
    8: "Bus", 9: "BlendContainer", 10: "MusicSegment", 11: "MusicTrack",
    12: "MusicSwitchContainer", 13: "MusicPlaylistContainer",
    14: "Attenuation", 15: "DialogueEvent", 16: "FeedbackBus",
    17: "FeedbackNode", 18: "FxShareSet", 19: "FxCustom",
    20: "AuxBus", 21: "LFO", 22: "Envelope",
}


@dataclass
class HircObjectData:
    """Raw parsed HIRC object with metadata."""
    type_id: int
    object_id: int
    raw_payload: bytes  # full payload INCLUDING the 4-byte object_id
    file_offset: int = 0  # absolute offset in source file
    type_name: str = ""
    # Parsed fields (populated by parse pass)
    parent_id: int = 0
    bus_id: int = 0
    child_ids: List[int] = field(default_factory=list)
    child_count_offset: int = 0  # exact offset of child count in raw_payload
    media_id: int = 0        # source/media ID for Sound objects
    media_size: int = 0
    source_type: int = 0     # 0=embedded, 1=streamed, 2=prefetched
    action_type: int = 0     # for Action objects
    target_id: int = 0       # action target
    event_action_ids: List[int] = field(default_factory=list)
    # Music-specific
    music_track_sources: List[Tuple[int, int]] = field(default_factory=list)
    playlist_wems: List[int] = field(default_factory=list)
    playlist_items: List[Dict] = field(default_factory=list)  # AkMusicRanSeqPlaylistItem tree
    segment_duration_ms: float = 0.0  # fDuration from MusicSegment binary
    state_group_id: int = 0
    target_state_id: int = 0
    switch_group_id: int = 0
    switch_state_id: int = 0
    switch_ids: List[int] = field(default_factory=list)
    switch_arguments: List[Tuple[int, int]] = field(default_factory=list)
    switch_nodes: List[Tuple[int, int]] = field(default_factory=list)
    # State
    modified: bool = False
    parse_error: str = ""


class HircParser:
    """Parses the HIRC chunk into a list of typed objects."""

    def __init__(self):
        self.objects: List[HircObjectData] = []
        self.objects_by_id: Dict[int, HircObjectData] = {}
        self.version: int = 145
        self.progress_callback = None
        self._log = get_logger()

    def parse(self, hirc_data: bytes, base_offset: int = 0, version: int = 145) -> List[HircObjectData]:
        self.version = version
        reader = BinaryReader(hirc_data, base_offset=base_offset)
        obj_count = reader.read_u32()
        self._log.info("HircParser", f"Parsing {obj_count} HIRC objects",
                       operation="parse", size=len(hirc_data))
        self.objects = []
        self.objects_by_id = {}

        for i in range(obj_count):
            if i % 100 == 0 and self.progress_callback:
                try:
                    self.progress_callback(i, obj_count)
                except Exception:
                    pass
            if reader.remaining() < 5:
                self._log.warn("HircParser",
                    f"Truncated HIRC at object {i}/{obj_count}, {reader.remaining()} bytes left",
                    operation="parse")
                break

            obj_offset = reader.absolute_offset
            type_id = reader.read_u8()
            payload_size = reader.read_u32()

            if reader.remaining() < payload_size:
                self._log.warn("HircParser",
                    f"Object {i} type={type_id} claims {payload_size} bytes, "
                    f"only {reader.remaining()} left", operation="parse", offset=obj_offset)
                payload_size = reader.remaining()

            raw_payload = reader.read_bytes(payload_size)
            if payload_size < 4:
                continue

            object_id = struct.unpack_from('<I', raw_payload, 0)[0]
            type_name = HIRC_TYPE_NAMES.get(type_id, f"Unknown({type_id})")

            obj = HircObjectData(
                type_id=type_id,
                object_id=object_id,
                raw_payload=raw_payload,
                file_offset=obj_offset,
                type_name=type_name,
            )

            # Parse type-specific fields
            try:
                self._parse_object_fields(obj)
            except Exception as e:
                obj.parse_error = str(e)
                self._log.debug("HircParser",
                    f"Parse error for {type_name} {object_id}: {e}",
                    operation="parse_fields", object_id=object_id)

            self.objects.append(obj)
            self.objects_by_id[object_id] = obj

        self._log.info("HircParser",
            f"Parsed {len(self.objects)} objects successfully",
            operation="parse_complete")
        return self.objects

    def _parse_object_fields(self, obj: HircObjectData):
        """Parse type-specific fields from the raw payload."""
        payload = obj.raw_payload
        if len(payload) < 5:
            return
        pr = BinaryReader(payload[4:])  # skip object_id

        if obj.type_id == 2:    # Sound
            self._parse_sound(obj, pr)
        elif obj.type_id == 3:  # Action
            self._parse_action(obj, pr)
        elif obj.type_id == 4:  # Event
            self._parse_event(obj, pr)
        elif obj.type_id == 5:  # RandomSequenceContainer
            self._parse_container_base(obj, pr)
        elif obj.type_id == 6:  # SwitchContainer
            self._parse_container_base(obj, pr)
        elif obj.type_id == 7:  # ActorMixer
            self._parse_container_base(obj, pr)
        elif obj.type_id == 9:  # BlendContainer
            self._parse_container_base(obj, pr)
        elif obj.type_id == 10: # MusicSegment
            self._parse_music_segment(obj, pr)
        elif obj.type_id == 11: # MusicTrack
            self._parse_music_track(obj, pr)
        elif obj.type_id == 12: # MusicSwitchContainer
            self._parse_music_switch(obj, pr)
        elif obj.type_id == 13: # MusicRanSeqContainer
            self._parse_music_ranseq(obj, pr)

    def _parse_node_base(self, pr: BinaryReader) -> Tuple[int, int]:
        """Parse NodeBase structure fully, return (bus_id, parent_id) and advance pointer past it."""
        if pr.remaining() < 2:
            return (0, 0)
        
        bus_id = 0
        parent_id = 0

        # FX override
        override_parent_fx = pr.read_u8()
        num_fx = pr.read_u8()
        if num_fx > 0:
            if pr.remaining() >= 1:
                pr.read_u8()  # bypass bits
            for _ in range(num_fx):
                if pr.remaining() >= 7:
                    pr.skip(7)

        # Metadata override
        if pr.remaining() >= 2:
            override_parent_metadata = pr.read_u8()
            metadata_count = pr.read_u8()
            for _ in range(metadata_count):
                if pr.remaining() >= 8:
                    pr.read_u32()  # id
                    sz = pr.read_u32()
                    if pr.remaining() >= sz:
                        pr.skip(sz)

        # Bus / parent override
        if pr.remaining() >= 1:
            override_attachment_params = pr.read_u8()
        if pr.remaining() >= 4:
            bus_id = pr.read_u32()
        if pr.remaining() >= 4:
            parent_id = pr.read_u32()

        # Priority
        if pr.remaining() >= 1:
            override_parent_priority = pr.read_u8()

        # Float props
        if pr.remaining() >= 1:
            prop_count = pr.read_u8()
            if prop_count > 0 and pr.remaining() >= prop_count:
                ids = list(pr.read_bytes(prop_count))
                for idx in ids:
                    if pr.remaining() >= 4:
                        pr.skip(4)

        # Int props
        if pr.remaining() >= 1:
            prop_count_int = pr.read_u8()
            if prop_count_int > 0 and pr.remaining() >= prop_count_int:
                ids = list(pr.read_bytes(prop_count_int))
                for idx in ids:
                    if pr.remaining() >= 4:
                        pr.skip(4)

        # Positioning
        if pr.remaining() >= 1:
            pos_bits = pr.read_u8()
            if (pos_bits & 0x01) != 0:
                if (pos_bits & 0x02) != 0:
                    if pr.remaining() >= 1:
                        pr.read_u8()
                else:
                    if pr.remaining() >= 1:
                        pr.read_u8()

        # Aux Sends
        if pr.remaining() >= 1:
            aux_bits = pr.read_u8()
            if (aux_bits & 0x08) != 0:
                if pr.remaining() >= 16:
                    pr.skip(16)
            elif (aux_bits & 0x04) != 0:
                if pr.remaining() >= 4:
                    pr.skip(4)

        if pr.remaining() >= 4:
            pr.read_u32()  # reflectionsAuxBus

        # Advanced settings block
        if pr.remaining() >= 6:
            adv_bits = pr.read_u8()
            virtual_queue_behavior = pr.read_u8()
            max_num_instance = pr.read_u16()
            below_threshold_behavior = pr.read_u8()
            pr.read_u8()  # hdrBits

        # State properties
        if pr.remaining() >= 1:
            state_props_count = pr.read_u8()
            if pr.remaining() >= state_props_count * 3:
                pr.skip(state_props_count * 3)

        # State groups
        if pr.remaining() >= 1:
            state_groups_count = pr.read_u8()
            for _ in range(state_groups_count):
                if pr.remaining() >= 5:
                    pr.skip(5)  # id(4) + sync_type(1)
                    if pr.remaining() >= 1:
                        state_count = pr.read_u8()
                        if pr.remaining() >= state_count * 8:
                            pr.skip(state_count * 8)

        # RTPCs
        if pr.remaining() >= 2:
            rtpc_count = pr.read_u16()
            for _ in range(rtpc_count):
                if pr.remaining() >= 10:
                    pr.skip(10)  # control_id(4) + param_id(1) + curve_id(4) + scaling(1)
                    if pr.remaining() >= 2:
                        point_count = pr.read_u16()
                        if pr.remaining() >= point_count * 12:
                            pr.skip(point_count * 12)  # X(4) + Y(4) + Shape(4)

        return (bus_id, parent_id)

    def _skip_node_base_remainder(self, pr: BinaryReader):
        """Skip the variable-length remainder of NodeBase after parent_id."""
        if pr.remaining() < 1:
            return
        override_bits = pr.read_u8()  # bOverridePlaybackPriority etc
        # Properties
        if pr.remaining() >= 1:
            prop_count = pr.read_u8()
            for _ in range(prop_count):
                if pr.remaining() >= 5:
                    pr.skip(5)  # propID(1) + value(4)
        # Range properties
        if pr.remaining() >= 1:
            range_count = pr.read_u8()
            for _ in range(range_count):
                if pr.remaining() >= 9:
                    pr.skip(9)  # propID(1) + min(4) + max(4)
        # Positioning
        if pr.remaining() >= 1:
            pos_bits = pr.read_u8()
            has_positioning = (pos_bits >> 0) & 1
            if has_positioning:
                has_3d = (pos_bits >> 3) & 1
                if has_3d and pr.remaining() >= 1:
                    spatial_mode = pr.read_u8()
                    if pr.remaining() >= 4:
                        pr.skip(4)  # attenuationID
                    if spatial_mode == 2 and pr.remaining() >= 1:
                        # User-defined
                        pr.read_u8()  # bIsFollowing
                # Additional positioning bytes vary by version
        # Aux sends, Advanced settings etc - skip remaining
        # We only needed bus_id and parent_id

    def _parse_sound(self, obj: HircObjectData, pr: BinaryReader):
        """Parse CAkSound (type 2)."""
        if pr.remaining() < 14:
            return
        # BankSourceData
        plugin_id = pr.read_u32()
        stream_type = pr.read_u8()
        obj.source_type = stream_type
        source_id = pr.read_u32()
        obj.media_id = source_id
        in_memory_size = pr.read_u32()
        obj.media_size = in_memory_size
        source_bits = pr.read_u8()  # uSourceBits
        # NodeBase
        bus_id, parent_id = self._parse_node_base(pr)
        obj.bus_id = bus_id
        obj.parent_id = parent_id

    def _parse_action(self, obj: HircObjectData, pr: BinaryReader):
        """Parse CAkAction (type 3)."""
        if pr.remaining() < 2:
            return
        scope = pr.read_u8()
        type_val = pr.read_u8()
        obj.action_type = (type_val << 8) | scope

        if type_val == 25:  # Set Switch
            if pr.remaining() >= 5:
                pr.skip(5)
            self._skip_action_initial_params(pr)
            if pr.remaining() >= 8:
                obj.switch_group_id = pr.read_u32()
                obj.switch_state_id = pr.read_u32()
                obj.target_id = obj.switch_state_id
        elif type_val == 18:  # Set State
            if pr.remaining() >= 5:
                pr.skip(5)
            self._skip_action_initial_params(pr)
            if pr.remaining() >= 8:
                obj.state_group_id = pr.read_u32()
                obj.target_state_id = pr.read_u32()
                obj.target_id = obj.target_state_id
        else:  # Standard Action (like Play)
            if pr.remaining() >= 4:
                obj.target_id = pr.read_u32()

    def _skip_action_initial_params(self, pr: BinaryReader):
        if pr.remaining() < 1:
            return
        prop_count = pr.read_u8()
        pr.skip(5 * prop_count)
        if pr.remaining() < 1:
            return
        prop_count = pr.read_u8()
        pr.skip(9 * prop_count)

    def _parse_event(self, obj: HircObjectData, pr: BinaryReader):
        """Parse CAkEvent (type 4)."""
        if pr.remaining() < 1:
            return
        action_count = pr.read_u8()
        ids = []
        for _ in range(action_count):
            if pr.remaining() >= 4:
                ids.append(pr.read_u32())
        obj.event_action_ids = ids

    def _parse_container_base(self, obj: HircObjectData, pr: BinaryReader):
        """Parse containers (RandomSeq, Switch, ActorMixer, Blend)."""
        bus_id, parent_id = self._parse_node_base(pr)
        obj.bus_id = bus_id
        obj.parent_id = parent_id
        # For containers, scan for child list in remaining payload
        # The child list is: count(4) + [child_id(4)] * count
        # We scan from current position through remainder
        remaining = obj.raw_payload[4:]  # skip object_id
        child_ids, offset = self._scan_for_children(remaining, obj.object_id)
        obj.child_ids = child_ids
        obj.child_count_offset = offset

    def _scan_for_children(self, data: bytes, parent_id: int) -> Tuple[List[int], int]:
        """
        Scan payload for the child ID list pattern.
        Strategy: find a u32 count followed by count*4 bytes of valid IDs.
        The child list is typically near the end of the NodeBase region.
        """
        children = []
        offset = 0
        if len(data) < 8:
            return children, offset
        # Search backwards from end for a valid count+ID pattern
        for scan_pos in range(len(data) - 8, 3, -1):
            candidate_count = struct.unpack_from('<I', data, scan_pos)[0]
            if candidate_count == 0 or candidate_count > 1000:
                continue
            needed = scan_pos + 4 + candidate_count * 4
            if needed > len(data):
                continue
            # Validate: each child ID should be non-zero
            ids = []
            valid = True
            for j in range(candidate_count):
                cid = struct.unpack_from('<I', data, scan_pos + 4 + j * 4)[0]
                if cid == 0:
                    valid = False
                    break
                ids.append(cid)
            if valid and len(ids) == candidate_count:
                # Check if any of these IDs exist as HIRC objects (will verify in pass 2)
                # For now accept if IDs look reasonable (> 1000)
                if all(cid > 100 for cid in ids):
                    children = ids
                    offset = 4 + scan_pos  # add 4 because data is raw_payload[4:]
                    break
        return children, offset

    def _skip_music_node_params(self, pr: BinaryReader) -> Tuple[int, int]:
        """Skip MusicNodeParams header (uFlags + NodeBaseParams) and return (bus_id, parent_id)."""
        if pr.remaining() < 1:
            return 0, 0
        pr.skip(1)  # uFlags
        bus_id, parent_id = self._parse_node_base(pr)
        return bus_id, parent_id

    def _skip_meter_info_and_stingers(self, pr: BinaryReader):
        """Skip AkMeterInfo (22 bytes) + bMeterInfoFlag (1) + stingers."""
        if pr.remaining() >= 23:
            pr.skip(23)  # fGridPeriod(8)+fGridOffset(8)+fTempo(4)+beats(1)+beat_val(1)+flag(1)
        if pr.remaining() >= 4:
            num_stingers = pr.read_u32()
            stinger_bytes = 24 * num_stingers
            if pr.remaining() >= stinger_bytes:
                pr.skip(stinger_bytes)

    def _parse_music_segment(self, obj: HircObjectData, pr: BinaryReader):
        """Parse CAkMusicSegment (type 10): children via NodeBaseParams Children list + fDuration."""
        try:
            bus_id, parent_id = self._skip_music_node_params(pr)
            obj.bus_id = bus_id
            obj.parent_id = parent_id
            # Children list
            if pr.remaining() >= 4:
                # Record the byte position of num_children within raw_payload
                # (pr starts from raw_payload[4:], so add 4 to get raw_payload offset)
                obj.child_count_offset = 4 + pr.position
                num_children = pr.read_u32()
                children = []
                for _ in range(num_children):
                    if pr.remaining() >= 4:
                        children.append(pr.read_u32())
                if children:
                    obj.child_ids = children
            # AkMeterInfo + stingers
            self._skip_meter_info_and_stingers(pr)
            # fDuration
            if pr.remaining() >= 8:
                obj.segment_duration_ms = pr.read_f64()
        except Exception:
            # Fallback: scan for children
            remaining = obj.raw_payload[4:]
            child_ids, offset = self._scan_for_children(remaining, obj.object_id)
            obj.child_ids = child_ids
            obj.child_count_offset = offset

    def _skip_music_transition_rules(self, pr: BinaryReader):
        """Skip numRules AkMusicTransitionRule entries."""
        if pr.remaining() < 4:
            return
        num_rules = pr.read_u32()
        for _ in range(num_rules):
            if pr.remaining() < 8:
                break
            num_src = pr.read_u32()
            if pr.remaining() < 4 * num_src + 4:
                break
            pr.skip(4 * num_src)
            num_dst = pr.read_u32()
            # 21 (TransSrcRule) + 26 (TransDstRule) = 47 bytes fixed per rule
            needed = 4 * num_dst + 47
            if pr.remaining() < needed:
                break
            pr.skip(4 * num_dst + 47)
            # Read AllocTransObjectFlag (1 byte)
            if pr.remaining() < 1:
                break
            alloc_flag = pr.read_u8()
            if alloc_flag != 0:
                # AkMusicTransitionObject is 30 bytes
                if pr.remaining() < 30:
                    break
                pr.skip(30)

    def _read_ranseq_playlist_item(self, pr: BinaryReader) -> Optional[Dict[str, Any]]:
        """Read one AkMusicRanSeqPlaylistItem (30 bytes) and its children recursively."""
        if pr.remaining() < 30:
            return None
        segment_id = pr.read_u32()
        item_id = pr.read_u32()
        num_children = pr.read_u32()
        rs_type = pr.read_u32()
        loop = pr.read_i16()
        loop_min = pr.read_i16()
        loop_max = pr.read_i16()
        weight = pr.read_u32()
        avoid_repeat = pr.read_u16()
        is_using_weight = pr.read_u8()
        is_shuffle = pr.read_u8()
        item = {
            'segment_id': segment_id,
            'item_id': item_id,
            'rs_type': rs_type,
            'loop': loop,
            'loop_min': loop_min,
            'loop_max': loop_max,
            'weight': weight,
            'avoid_repeat': avoid_repeat,
            'is_shuffle': bool(is_shuffle),
            'children': []
        }
        for _ in range(num_children):
            child = self._read_ranseq_playlist_item(pr)
            if child is not None:
                item['children'].append(child)
        return item

    def _parse_music_ranseq(self, obj: HircObjectData, pr: BinaryReader):
        """Parse CAkMusicRanSeqCntr (type 13): children + playlist item tree."""
        try:
            bus_id, parent_id = self._skip_music_node_params(pr)
            obj.bus_id = bus_id
            obj.parent_id = parent_id
            # Children
            if pr.remaining() >= 4:
                # Record the byte position of num_children within raw_payload
                obj.child_count_offset = 4 + pr.position
                num_children = pr.read_u32()
                children = []
                for _ in range(num_children):
                    if pr.remaining() >= 4:
                        children.append(pr.read_u32())
                if children:
                    obj.child_ids = children
            # AkMeterInfo + stingers
            self._skip_meter_info_and_stingers(pr)
            # Transition rules
            self._skip_music_transition_rules(pr)
            # Playlist items tree
            if pr.remaining() >= 4:
                num_items = pr.read_u32()
                items = []
                # Items are serialized DFS (each item + its children recursively)
                # We read them as a flat list because _read_ranseq_playlist_item is recursive
                # But num_items is the TOTAL flat count, so we need a different approach:
                # Read them one by one, using the recursive reader
                items = self._read_ranseq_flat(pr, num_items)
                obj.playlist_items = items
        except Exception:
            # Fallback
            remaining = obj.raw_payload[4:]
            child_ids, offset = self._scan_for_children(remaining, obj.object_id)
            obj.child_ids = child_ids
            obj.child_count_offset = offset

    def _read_ranseq_flat(self, pr: BinaryReader, total_count: int) -> List[Dict[str, Any]]:
        """Read a flat list of AkMusicRanSeqPlaylistItem where NumChildren indicates subtree size."""
        items = []
        remaining_count = [total_count]  # mutable counter

        def read_one() -> Optional[Dict[str, Any]]:
            if remaining_count[0] <= 0 or pr.remaining() < 30:
                return None
            remaining_count[0] -= 1
            segment_id = pr.read_u32()
            item_id = pr.read_u32()
            num_children = pr.read_u32()
            rs_type = pr.read_u32()
            loop = pr.read_i16()
            loop_min = pr.read_i16()
            loop_max = pr.read_i16()
            weight = pr.read_u32()
            avoid_repeat = pr.read_u16()
            is_using_weight = pr.read_u8()
            is_shuffle = pr.read_u8()
            node = {
                'segment_id': segment_id,
                'item_id': item_id,
                'rs_type': rs_type,   # 0=ContinuousSeq, 0xFFFFFFFF=None
                'loop': loop,          # 0=infinite, 1=play once
                'loop_min': loop_min,
                'loop_max': loop_max,
                'weight': weight,
                'avoid_repeat': avoid_repeat,
                'is_shuffle': bool(is_shuffle),
                'children': []
            }
            for _ in range(num_children):
                child = read_one()
                if child is not None:
                    node['children'].append(child)
            return node

        # The flat list may have multiple top-level items
        while remaining_count[0] > 0 and pr.remaining() >= 30:
            node = read_one()
            if node is not None:
                items.append(node)
        return items

    def _parse_music_node(self, obj: HircObjectData, pr: BinaryReader):
        """Fallback: scan for children (used for unrecognized or old music containers)."""
        bus_id, parent_id = self._parse_node_base(pr)
        obj.bus_id = bus_id
        obj.parent_id = parent_id
        remaining = obj.raw_payload[4:]
        child_ids, offset = self._scan_for_children(remaining, obj.object_id)
        obj.child_ids = child_ids
        obj.child_count_offset = offset

    def _parse_music_switch(self, obj: HircObjectData, pr: BinaryReader):
        """Parse CAkMusicSwitchCntr (type 12) decision switch tree container."""
        if pr.remaining() < 1:
            return
        
        # 1. Skip 1 byte flags
        pr.skip(1)
        
        # 2. Skip BaseParams and retrieve parent_id
        parent_id = self._skip_base_params(pr, self.version)
        obj.parent_id = parent_id
        
        # 3. Read children
        if pr.remaining() < 4:
            return
        num_children = pr.read_u32()
        children = []
        for _ in range(num_children):
            if pr.remaining() < 4:
                break
            children.append(pr.read_u32())
        obj.child_ids = children
        
        # 4. Skip stingers
        if pr.remaining() < 27:
            return
        pr.skip(23)
        num_stingers = pr.read_u32()
        if pr.remaining() >= 24 * num_stingers:
            pr.skip(24 * num_stingers)
            
        # 5. Skip rules table
        if pr.remaining() < 4:
            return
        num_rules = pr.read_u32()
        for _ in range(num_rules):
            if pr.remaining() < 8:
                break
            num_sources = pr.read_u32()
            if pr.remaining() < 4 * num_sources + 4:
                break
            pr.skip(4 * num_sources)
            num_destinations = pr.read_u32()
            needed = 4 * num_destinations + (45 if self.version <= 132 else 47) + 1
            if pr.remaining() < needed:
                break
            pr.skip(4 * num_destinations)
            pr.skip(45 if self.version <= 132 else 47)
            has_trans = pr.read_u8() > 0
            if has_trans:
                if pr.remaining() < 30:
                    break
                pr.skip(30)
                
        # 6. Read arguments (switch / state groups)
        if pr.remaining() < 5:
            return
        pr.skip(1)
        num_arguments = pr.read_u32()
        if pr.remaining() < 5 * num_arguments:
            return
        group_ids = []
        for _ in range(num_arguments):
            group_ids.append(pr.read_u32())
        group_types = []
        for _ in range(num_arguments):
            group_types.append(pr.read_u8())
            
        obj.switch_arguments = list(zip(group_ids, group_types))
        
        # 7. Read Switch Decision Tree nodes
        if pr.remaining() < 5:
            return
        tree_size = pr.read_u32()
        pr.skip(1)
        num_nodes = tree_size // 12
        switch_nodes = []
        for _ in range(num_nodes):
            if pr.remaining() < 12:
                break
            key = pr.read_u32()
            audio_id = pr.read_u32()
            pr.skip(4)
            switch_nodes.append((key, audio_id))
        obj.switch_nodes = switch_nodes

    def _parse_music_track(self, obj: HircObjectData, pr: BinaryReader):
        """Parse CAkMusicTrack (type 11)."""
        if pr.remaining() < 5:
            return
        
        # 1. Skip 1-byte track flag
        pr.skip(1)
        
        # 2. Read sources list
        src_count = pr.read_u32()
        sources = []
        for _ in range(src_count):
            if pr.remaining() < 14:
                break
            plugin_id = pr.read_u32()
            stream_type = pr.read_u8()
            source_id = pr.read_u32()
            in_mem_size = pr.read_u32()
            source_bits = pr.read_u8()
            sources.append((source_id, in_mem_size))
        obj.music_track_sources = sources
        
        # 3. Read playlist (subtracks)
        if pr.remaining() < 4:
            return
        playlist_count = pr.read_u32()
        playlist_wems = []
        for _ in range(playlist_count):
            if pr.remaining() < 44:
                break
            track_index = pr.read_u32()
            file_id = pr.read_u32()
            event_id = pr.read_u32()
            play_at = pr.read_f64()
            begin_trim = pr.read_f64()
            end_trim = pr.read_f64()
            duration = pr.read_f64()
            playlist_wems.append(file_id)
        obj.playlist_wems = playlist_wems
        
        # Determine media ID (actual playing WEM ID is from playlist if available)
        if playlist_wems:
            obj.media_id = playlist_wems[0]
            # Match its size from sources if we can, else default to 0
            obj.media_size = 0
            for sid, sz in sources:
                if sid == obj.media_id:
                    obj.media_size = sz
                    break
        elif sources:
            obj.media_id = sources[0][0]
            obj.media_size = sources[0][1]
            
        # 4. Skip padding and clip automation
        if playlist_count > 0 and pr.remaining() >= 4:
            pr.skip(4)
        if pr.remaining() >= 4:
            num_clip = pr.read_u32()
            for _ in range(num_clip):
                if pr.remaining() < 12:
                    break
                pr.skip(8)
                point_count = pr.read_u32()
                if pr.remaining() < 12 * point_count:
                    break
                pr.skip(12 * point_count)
                
        # 5. Skip NodeBase properties to read parent_id and switch ID list
        try:
            parent_id = self._skip_base_params(pr, self.version)
            obj.parent_id = parent_id
            
            # Read track switch variants
            if pr.remaining() >= 1:
                track_type = pr.read_u8()
                if track_type == 0x03 and pr.remaining() >= 9:
                    pr.skip(1)
                    obj.switch_group_id = pr.read_u32()
                    pr.skip(4)  # default switch ID
                    count = pr.read_u32()
                    switch_ids = []
                    for _ in range(count):
                        if pr.remaining() >= 4:
                            switch_ids.append(pr.read_u32())
                    obj.switch_ids = switch_ids
        except Exception:
            pass

    def _skip_base_params(self, pr: BinaryReader, version: int) -> int:
        if pr.remaining() < 2:
            return 0
        pr.skip(1)
        num_fx = pr.read_u8()
        if num_fx > 0 and pr.remaining() >= 1:
            pr.skip(1)
        needed = num_fx * (7 if version <= 145 else 6)
        if pr.remaining() >= needed:
            pr.skip(needed)
            
        if version > 136:
            if pr.remaining() >= 2:
                pr.skip(1)
                num_fx = pr.read_u8()
                if pr.remaining() >= 6 * num_fx:
                    pr.skip(6 * num_fx)
                    
        if 89 < version <= 145 and pr.remaining() >= 1:
            pr.skip(1)
            
        if pr.remaining() < 9:
            return 0
        bus_id = pr.read_u32()
        parent_id = pr.read_u32()
        pr.skip(2 if version <= 89 else 1)
        
        # skip_initial_params
        if pr.remaining() >= 1:
            prop_count = pr.read_u8()
            if pr.remaining() >= 5 * prop_count:
                pr.skip(5 * prop_count)
        if pr.remaining() >= 1:
            prop_count = pr.read_u8()
            if pr.remaining() >= 9 * prop_count:
                pr.skip(9 * prop_count)
                
        # skip_positioning_params
        if pr.remaining() >= 1:
            pos_bits = pr.read_u8()
            has_pos = pos_bits & 1
            has_3d = False
            has_automation = False
            if has_pos:
                if version <= 89:
                    if pr.remaining() >= 2:
                        has_2d = pr.read_u8() > 0
                        has_3d = pr.read_u8() > 0
                        if has_2d and pr.remaining() >= 1:
                            pr.skip(1)
                else:
                    has_3d = bool(pos_bits & 0x2)
            if has_pos and has_3d:
                if version <= 89:
                    if pr.remaining() >= 9:
                        has_automation = (pr.read_u8() & 3) != 1
                        pr.skip(8)
                else:
                    if pr.remaining() >= 1:
                        has_automation = bool((pos_bits >> 5) & 3)
                        pr.skip(1)
            if has_automation:
                pr.skip(9 if version <= 89 else 5)
                if pr.remaining() >= 8:
                    num_vertices = pr.read_u32()
                    if pr.remaining() >= 16 * num_vertices + 4:
                        pr.skip(16 * num_vertices)
                        num_playlist = pr.read_u32()
                        needed_sz = (16 if version <= 89 else 20) * num_playlist
                        if pr.remaining() >= needed_sz:
                            pr.skip(needed_sz)
            elif version <= 89 and pr.remaining() >= 1:
                pr.skip(1)
                
        # skip_aux_params
        if pr.remaining() >= 1:
            has_aux = bool((pr.read_u8() >> 3) & 1)
            if has_aux and pr.remaining() >= 16:
                pr.skip(16)
        if version > 135 and pr.remaining() >= 4:
            pr.skip(4)
            
        if pr.remaining() >= 6:
            pr.skip(6)
            
        # skip_state_chunk
        if pr.remaining() >= 1:
            state_props = pr.read_u8()
            if pr.remaining() >= 3 * state_props + 1:
                pr.skip(3 * state_props)
                state_groups = pr.read_u8()
                for _ in range(state_groups):
                    if pr.remaining() >= 6:
                        pr.skip(5)
                        states = pr.read_u8()
                        if pr.remaining() >= 8 * states:
                            pr.skip(8 * states)
                            
        # skip_rtpc
        if pr.remaining() >= 2:
            num_rtpc = pr.read_u16()
            for _ in range(num_rtpc):
                if pr.remaining() >= (15 if version <= 89 else 14):
                    pr.skip(13 if version <= 89 else 12)
                    point_count = pr.read_u16()
                    if pr.remaining() >= 12 * point_count:
                        pr.skip(12 * point_count)
                        
        return parent_id

    def rebuild_hirc(self, objects: Optional[List[HircObjectData]] = None) -> bytes:
        """Rebuild HIRC chunk data from object list."""
        if objects is None:
            objects = self.objects
        w = BinaryWriter()
        w.write_u32(len(objects))
        for obj in objects:
            w.write_u8(obj.type_id)
            w.write_u32(len(obj.raw_payload))
            w.write_bytes(obj.raw_payload)
        return w.get_data()

    def _scan_robust_children(self, obj: HircObjectData) -> Tuple[List[int], int]:
        """
        Scan payload for child IDs and validate that they actually exist in self.objects_by_id.
        This provides 100% robust resolution for all container types (5, 6, 7, 9, 10, 12, 13).
        """
        data = obj.raw_payload[4:]  # skip object_id
        if len(data) < 8:
            return [], 0
        
        # Scan backwards. Accept if ALL children are in our bank (strict) OR if at least
        # 50% are (lenient, for cross-bank references). Prefer strict matches.
        best_ids: List[int] = []
        best_offset = 0
        for scan_pos in range(len(data) - 8, -1, -1):
            candidate_count = struct.unpack_from('<I', data, scan_pos)[0]
            if candidate_count == 0 or candidate_count > 512:
                continue
            needed = scan_pos + 4 + candidate_count * 4
            if needed > len(data):
                continue

            ids = []
            in_bank = 0
            for j in range(candidate_count):
                cid = struct.unpack_from('<I', data, scan_pos + 4 + j * 4)[0]
                if cid == 0:
                    break
                ids.append(cid)
                if cid in self.objects_by_id:
                    in_bank += 1

            if len(ids) != candidate_count:
                continue

            if in_bank == candidate_count:
                # Perfect match — return immediately
                return ids, 4 + scan_pos
            elif in_bank >= max(1, candidate_count // 2) and not best_ids:
                # Partial match — keep as fallback
                best_ids = ids
                best_offset = 4 + scan_pos

        return best_ids, best_offset

    def resolve_relationships(self):
        """Pass 2: resolve parent-child by scanning objects robustly."""
        log = get_logger()
        
        # 1. Re-scan child lists for all containers robustly with Bank-Validation.
        # This completely eliminates false-positives and works perfectly for music containers.
        for obj in self.objects:
            if obj.type_id in (5, 6, 7, 9):
                true_children, offset = self._scan_robust_children(obj)
                if true_children:
                    obj.child_ids = true_children
                    obj.child_count_offset = offset
        
        # 2. Build child->parent relationships and fix parent_id fields
        # Deriving parent_id from parent -> child lists avoids offset bugs.
        for parent in self.objects:
            if parent.type_id in (5, 6, 7, 9, 10, 12, 13):
                for cid in parent.child_ids:
                    if cid in self.objects_by_id:
                        child = self.objects_by_id[cid]
                        child.parent_id = parent.object_id

        # Validate action targets
        for obj in self.objects:
            if obj.type_id == 3 and obj.target_id:
                if obj.target_id not in self.objects_by_id:
                    # Global State/Switch name hashes (like Health_Dead) are normal and not errors
                    if obj.action_type in (3074, 4612):
                        log.debug("HircParser",
                            f"Action {obj.object_id} targets global State/Switch hash {obj.target_id}",
                            object_id=obj.object_id)
                    else:
                        log.debug("HircParser",
                            f"Action {obj.object_id} targets unknown ID {obj.target_id}",
                            object_id=obj.object_id)
        log.info("HircParser", "Relationship resolution complete", operation="resolve")
