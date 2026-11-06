"""
Wwise Project Exporter.
Exports the parsed BNK/WPK hierarchy to a Wwise project template.
Creates Actor-Mixer hierarchy and Events work units from scratch.
"""

import os
import re
import uuid
import random
import struct
import xml.etree.ElementTree as ET
from typing import Dict, List, Set, Tuple, Optional, Any

from lam.core.binary_reader import BinaryReader
from lam.parsers.hirc_parser import HircObjectData
from lam.core.hashing import HashLookup
from lam.core.logging import get_logger


# Standard Wwise Default IDs
DEFAULT_MASTER_BUS_ID = "{1514A4D8-1DA6-412A-A17E-75CA0C2149F3}"
DEFAULT_MASTER_BUS_WU_ID = "{369ABE4C-65A8-4E42-B131-19EA9DCE2363}"
DEFAULT_CONVERSION_ID = "{6D1B890C-9826-4384-BF07-C15223E9FB56}"
DEFAULT_CONVERSION_WU_ID = "{074B1442-E293-4C13-ACAD-9B61AC71E7A5}"


def compute_fnv32_hash(name: str) -> int:
    """
    Computes the FNV-1 32-bit hash Wwise uses for SoundBank ShortIDs.
    Algorithm: offset=2166136261, prime=16777619, lowercase input.
    """
    h = 2166136261
    for char in name.lower():
        h = ((h * 16777619) & 0xFFFFFFFF) ^ ord(char)
    return h


def generate_guid() -> str:
    """Generate a uppercase curly-braced GUID string."""
    return "{" + str(uuid.uuid4()).upper() + "}"


def generate_short_id() -> str:
    """Generate a random 9-digit short ID string."""
    return str(random.randint(100000000, 999999999))


def find_element_by_local_name(parent: ET.Element, local_name: str) -> List[ET.Element]:
    """Find all descendant elements matching the local name (ignoring namespaces)."""
    results = []
    for elem in parent.iter():
        tag = elem.tag
        if '}' in tag:
            tag = tag.split('}', 1)[1]
        if tag == local_name:
            results.append(elem)
    return results


def find_child_by_local_name(parent: ET.Element, local_name: str) -> Optional[ET.Element]:
    """Find the first direct child matching the local name (ignoring namespaces)."""
    for child in parent:
        tag = child.tag
        if '}' in tag:
            tag = tag.split('}', 1)[1]
        if tag == local_name:
            return child
    return None


def read_node_base_params(pr: BinaryReader, version: int = 145) -> Dict[str, Any]:
    """Parse Wwise NodeBaseParams structure from raw binary payload."""
    p = {
        'direct_parent_id': 0,
        'output_bus_id': 0,
        'props': [],
        'props_int': [],
        'adv_bits': 0,
        'virtual_queue_behavior': 0,
        'max_num_instance': 0,
        'below_threshold_behavior': 0
    }
    try:
        if pr.remaining() < 2:
            return p

        # FX override
        p['override_parent_fx'] = pr.read_u8()
        p['num_fx'] = pr.read_u8()
        if p['num_fx'] > 0:
            if pr.remaining() >= 1:
                pr.read_u8()  # bypass bits
            for _ in range(p['num_fx']):
                if pr.remaining() >= 7:
                    pr.skip(7)  # fxIndex + fxID + isShareSet + isRendered

        # Metadata override
        if pr.remaining() >= 2:
            p['override_parent_metadata'] = pr.read_u8()
            metadata_count = pr.read_u8()
            for _ in range(metadata_count):
                if pr.remaining() >= 8:
                    pr.read_u32()  # id
                    sz = pr.read_u32()
                    if pr.remaining() >= sz:
                        pr.skip(sz)

        # Bus / parent override
        if pr.remaining() >= 1:
            p['override_attachment_params'] = pr.read_u8()
        if pr.remaining() >= 4:
            p['output_bus_id'] = pr.read_u32()
        if pr.remaining() >= 4:
            p['direct_parent_id'] = pr.read_u32()

        # Priority
        if pr.remaining() >= 1:
            p['override_parent_priority'] = pr.read_u8()

        # Float props
        if pr.remaining() >= 1:
            prop_count = pr.read_u8()
            if prop_count > 0 and pr.remaining() >= prop_count:
                ids = list(pr.read_bytes(prop_count))
                for idx in ids:
                    if pr.remaining() >= 4:
                        p['props'].append({'id': idx, 'value': pr.read_f32()})

        # Int props
        if pr.remaining() >= 1:
            prop_count_int = pr.read_u8()
            if prop_count_int > 0 and pr.remaining() >= prop_count_int:
                ids = list(pr.read_bytes(prop_count_int))
                for idx in ids:
                    if pr.remaining() >= 4:
                        p['props_int'].append({'id': idx, 'value': pr.read_i32()})

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

        if version >= 135 and pr.remaining() >= 4:
            pr.read_u32()  # reflectionsAuxBus

        # Advanced settings block (6 bytes in BNK 140+)
        if version >= 140 and pr.remaining() >= 6:
            p['adv_bits'] = pr.read_u8()
            p['virtual_queue_behavior'] = pr.read_u8()
            p['max_num_instance'] = pr.read_u16()
            p['below_threshold_behavior'] = pr.read_u8()
            pr.read_u8()  # hdrBits
        elif version > 0x87 and pr.remaining() >= 1:
            p['adv_bits'] = pr.read_u8()

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

    except Exception:
        pass

    return p


class WwiseProjectExporter:
    """Exports the current project state to a Wwise project template."""

    def __init__(self):
        self._log = get_logger()
        self._container_id_to_guid: Dict[int, str] = {}
        self._object_name_map: Dict[int, str] = {}
        self._existing_wavs: Set[int] = set()
        self._actor_mixer_wwu_id = ""
        self._interactive_music_wwu_id = ""
        self._master_bus_id = DEFAULT_MASTER_BUS_ID
        self._master_bus_wwu_id = DEFAULT_MASTER_BUS_WU_ID
        self._conversion_settings_id = DEFAULT_CONVERSION_ID
        self._conversion_settings_wwu_id = DEFAULT_CONVERSION_WU_ID

    def export(self,
               objects: List[HircObjectData],
               hash_lookup: HashLookup,
               project_folder: str,
               bnk_version: int,
               bank_groups: Dict[str, List[int]]):
        """Perform full template export workflow."""
        self._log.info("WwiseExporter", f"Starting Wwise Template Export inside: {project_folder}")

        # 0. Discover existing WAV files in Originals/SFX
        sfx_dir = os.path.join(project_folder, "Originals", "SFX")
        self._existing_wavs.clear()
        if os.path.isdir(sfx_dir):
            for fname in os.listdir(sfx_dir):
                if fname.lower().endswith(".wav"):
                    try:
                        self._existing_wavs.add(int(os.path.splitext(fname)[0]))
                    except ValueError:
                        pass
        self._log.info("WwiseExporter", f"Found {len(self._existing_wavs)} existing WAV files.", operation="export")

        # 1. Paths verification
        actor_mixer_wwu = os.path.join(project_folder, "Actor-Mixer Hierarchy", "Default Work Unit.wwu")
        events_wwu = os.path.join(project_folder, "Events", "Default Work Unit.wwu")

        if not os.path.exists(actor_mixer_wwu):
            raise FileNotFoundError(f"Actor-Mixer Default Work Unit not found: {actor_mixer_wwu}")
        if not os.path.exists(events_wwu):
            raise FileNotFoundError(f"Events Default Work Unit not found: {events_wwu}")

        # 2. Parse existing XML documents
        actor_mixer_doc = ET.parse(actor_mixer_wwu)
        events_doc = ET.parse(events_wwu)

        # 3. Extract existing Sound objects imported by user into Default Work Unit
        sound_map = self._extract_existing_sounds(actor_mixer_doc)
        if not sound_map:
            self._log.warn("WwiseExporter",
                "No Sound elements found in Actor-Mixer Hierarchy. "
                "Auto-populating from Originals/SFX and HIRC tree.",
                operation="export")
            sound_map = self._build_sound_map_from_originals(
                objects, project_folder, actor_mixer_doc
            )

        for sound_el in sound_map.values():
            self._ensure_sound_references(sound_el)

        # 4. Find Actor-Mixer WorkUnit element
        actor_mixer_wu = None
        for wu in find_element_by_local_name(actor_mixer_doc.getroot(), "WorkUnit"):
            if wu.attrib.get("Name") == "Default Work Unit":
                actor_mixer_wu = wu
                break

        if actor_mixer_wu is None:
            raise ValueError("Could not find 'Default Work Unit' in Actor-Mixer Hierarchy")

        self._actor_mixer_wwu_id = actor_mixer_wu.attrib.get("ID", "")

        children_list = find_child_by_local_name(actor_mixer_wu, "ChildrenList")
        if children_list is None:
            children_list = ET.Element("ChildrenList")
            actor_mixer_wu.append(children_list)

        # 5. Extract Bus & Conversion settings
        self._extract_references(actor_mixer_doc, project_folder)

        # Clear existing children from Default Work Unit
        children_list.clear()

        # Build ID-to-object maps
        hirc_map: Dict[int, HircObjectData] = {obj.object_id: obj for obj in objects}
        self._hirc_map = hirc_map

        # Build name maps using event names
        self._build_object_name_map(objects, hash_lookup)

        # 6. Build Hierarchies recursively
        self._container_id_to_guid.clear()
        self._recursion_stack: Set[int] = set()

        # Gather all root candidates: objects of types 2, 5, 6, 7, 9, 10, 11, 12, 13
        # whose parent_id is 0 or not in hirc_map
        root_candidates = []
        for obj in objects:
            if obj.type_id in (2, 5, 6, 7, 9, 10, 11, 12, 13):
                parent_id = obj.parent_id
                if parent_id == 0 or parent_id not in hirc_map:
                    root_candidates.append(obj.object_id)

        # Setup Interactive Music Work Unit if it exists
        interactive_music_wwu = os.path.join(project_folder, "Interactive Music Hierarchy", "Default Work Unit.wwu")
        has_interactive_music = os.path.exists(interactive_music_wwu)
        
        interactive_music_wu = None
        interactive_music_doc = None
        interactive_music_children = None
        
        if has_interactive_music:
            try:
                interactive_music_doc = ET.parse(interactive_music_wwu)
                for wu in find_element_by_local_name(interactive_music_doc.getroot(), "WorkUnit"):
                    if wu.attrib.get("Name") == "Default Work Unit":
                        interactive_music_wu = wu
                        # Ensure the Work Unit has a stable ID.  If the .wwu already has one,
                        # keep it; otherwise generate a new one and write it back so that the
                        # event Action ObjectRefs produced later will match.
                        existing_id = wu.attrib.get("ID", "")
                        if not existing_id:
                            existing_id = generate_guid()
                            wu.attrib["ID"] = existing_id
                        self._interactive_music_wwu_id = existing_id
                        break
                if interactive_music_wu is not None:
                    interactive_music_children = find_child_by_local_name(interactive_music_wu, "ChildrenList")
                    if interactive_music_children is None:
                        interactive_music_children = ET.Element("ChildrenList")
                        interactive_music_wu.append(interactive_music_children)
                    interactive_music_children.clear()
            except Exception:
                has_interactive_music = False

        # Separate roots for both hierarchies
        actor_mixer_roots = []
        interactive_music_roots = []
        for root_id in root_candidates:
            obj = hirc_map[root_id]
            if obj.type_id in (2, 5, 6, 7, 9):
                actor_mixer_roots.append(root_id)
            elif obj.type_id in (10, 11, 12, 13):
                interactive_music_roots.append(root_id)

        actor_mixer_elems = []
        # Build Actor-Mixer root objects
        for root_id in actor_mixer_roots:
            suggested_name = self._object_name_map.get(root_id, f"Cnt_{root_id}")
            elem = self._build_hierarchy_recursive(root_id, hirc_map, sound_map, project_folder, bnk_version, suggested_name)
            if elem is not None:
                children_list.append(elem)
                actor_mixer_elems.append(elem)

        interactive_music_elems = []
        # Build Interactive Music root objects
        if has_interactive_music and interactive_music_children is not None:
            for root_id in interactive_music_roots:
                suggested_name = self._object_name_map.get(root_id, f"Music_{root_id}")
                elem = self._build_hierarchy_recursive(root_id, hirc_map, sound_map, project_folder, bnk_version, suggested_name)
                if elem is not None:
                    interactive_music_children.append(elem)
                    interactive_music_elems.append(elem)
        else:
            # Fallback if no Interactive Music work unit exists (append to Actor-Mixer)
            for root_id in interactive_music_roots:
                suggested_name = self._object_name_map.get(root_id, f"Music_{root_id}")
                elem = self._build_hierarchy_recursive(root_id, hirc_map, sound_map, project_folder, bnk_version, suggested_name)
                if elem is not None:
                    children_list.append(elem)
                    actor_mixer_elems.append(elem)

        # Handle unreferenced sounds
        used_wem_ids = set()
        for obj in objects:
            if obj.type_id == 2 and obj.media_id > 0:
                used_wem_ids.add(obj.media_id)
            elif obj.type_id == 11:
                # Add all sources of this music track
                for source_id, _ in obj.music_track_sources:
                    if source_id > 0:
                        used_wem_ids.add(source_id)
                for file_id in obj.playlist_wems:
                    if file_id > 0:
                        used_wem_ids.add(file_id)

        unreferenced_sounds = [el for wem_id, el in sound_map.items() if wem_id not in used_wem_ids]
        
        if unreferenced_sounds:
            unref_folder = ET.Element("Folder", {
                "Name": "Unreferenced_Sounds",
                "ID": generate_guid(),
                "ShortID": generate_short_id()
            })
            unref_children = ET.Element("ChildrenList")
            for el in unreferenced_sounds:
                el.attrib["ShortID"] = generate_short_id()
                unref_children.append(el)
            unref_folder.append(unref_children)
            children_list.append(unref_folder)
            actor_mixer_elems.append(unref_folder)

        # Save Actor-Mixer
        actor_mixer_doc.write(actor_mixer_wwu, encoding="utf-8", xml_declaration=True)
        ET.indent(actor_mixer_doc, space="  ")
        actor_mixer_doc.write(actor_mixer_wwu, encoding="utf-8", xml_declaration=True)

        # Save Interactive Music
        if has_interactive_music and interactive_music_doc is not None:
            interactive_music_doc.write(interactive_music_wwu, encoding="utf-8", xml_declaration=True)
            ET.indent(interactive_music_doc, space="  ")
            interactive_music_doc.write(interactive_music_wwu, encoding="utf-8", xml_declaration=True)

        # 7. Build Events Work Unit
        event_elements = self._build_events_hierarchy(objects, hash_lookup, hirc_map)
        self._modify_events_work_unit(events_doc, event_elements)
        events_doc.write(events_wwu, encoding="utf-8", xml_declaration=True)
        ET.indent(events_doc, space="  ")
        events_doc.write(events_wwu, encoding="utf-8", xml_declaration=True)

        # 8. Build SoundBanks Work Unit
        soundbanks_wwu = os.path.join(project_folder, "SoundBanks", "Default Work Unit.wwu")
        os.makedirs(os.path.join(project_folder, "SoundBanks"), exist_ok=True)
        
        if os.path.exists(soundbanks_wwu):
            sb_doc = ET.parse(soundbanks_wwu)
        else:
            sb_doc = self._create_default_soundbanks_wwu()

        events_wu_id = ""
        for wu in find_element_by_local_name(events_doc.getroot(), "WorkUnit"):
            events_wu_id = wu.attrib.get("ID", "")
            break

        # Generate SoundBank inclusion lists per group/bank.
        #
        # Filter semantics (Wwise SoundBank ObjectInclusionList):
        #   1 = Events only
        #   2 = Structures only
        #   3 = Events + Structures  (no media)
        #   4 = Media only
        #   7 = Events + Structures + Media  (everything)
        #
        # For a split events/audio bank setup:
        #   *_events bank  -> Filter 3 (events + structures).  Media lives in the audio bank.
        #   *_audio  bank  -> Filter 7 (everything).  Wwise embeds all media referenced by
        #                     the included events.  We do NOT add container ObjectRefs here
        #                     because Wwise resolves media through events automatically.
        #   single bank    -> Filter 7 (everything).
        for bank_path, event_ids in bank_groups.items():
            bank_name = os.path.splitext(os.path.basename(bank_path))[0]
            bank_events = [ev for ev in event_elements if int(ev.attrib.get("ShortID", 0)) in event_ids]
            if bank_events:
                is_events_bank = bank_name.endswith("_events")
                is_audio_bank  = bank_name.endswith("_audio")

                if is_events_bank:
                    # Events + Structures only — no media, no container ObjectRefs
                    self._modify_soundbank_inclusions(
                        sb_doc, events_wu_id, bank_events, bank_name,
                        None, self._actor_mixer_wwu_id,
                        None, self._interactive_music_wwu_id,
                        filter_value="3"
                    )
                elif is_audio_bank:
                    # Media bank — include events at filter 7 so Wwise packs the media.
                    # Do NOT pass container elems; Wwise resolves them through the events.
                    self._modify_soundbank_inclusions(
                        sb_doc, events_wu_id, bank_events, bank_name,
                        None, self._actor_mixer_wwu_id,
                        None, self._interactive_music_wwu_id,
                        filter_value="7"
                    )
                else:
                    # Single-bank project — include everything
                    self._modify_soundbank_inclusions(
                        sb_doc, events_wu_id, bank_events, bank_name,
                        actor_mixer_elems, self._actor_mixer_wwu_id,
                        interactive_music_elems, self._interactive_music_wwu_id,
                        filter_value="7"
                    )

        sb_doc.write(soundbanks_wwu, encoding="utf-8", xml_declaration=True)
        ET.indent(sb_doc, space="  ")
        sb_doc.write(soundbanks_wwu, encoding="utf-8", xml_declaration=True)

        # 9. Patch Conversion settings to Vorbis
        self._patch_conversion_settings(project_folder)

        # 10. Write States and Switches Work Units
        self._create_states_switches_wwus(objects, project_folder)

        self._log.info("WwiseExporter", "Export completed successfully!")

    def _create_states_switches_wwus(self, objects: List[HircObjectData], project_folder: str):
        state_groups = {}  # group_id -> set of state_ids
        switch_groups = {} # group_id -> set of switch_ids

        for obj in objects:
            if obj.type_id == 12 and hasattr(obj, 'switch_arguments') and hasattr(obj, 'switch_nodes'):
                if obj.switch_arguments:
                    group_id, group_type = obj.switch_arguments[0]
                    target_dict = state_groups if group_type == 1 else switch_groups
                    if group_id not in target_dict:
                        target_dict[group_id] = set()
                    
                    for state_id, _ in obj.switch_nodes:
                        target_dict[group_id].add(state_id)
                    # Ensure 'None' state is always generated for every group
                    target_dict[group_id].add(0)

        # Write States/Default Work Unit.wwu
        if state_groups:
            states_dir = os.path.join(project_folder, "States")
            os.makedirs(states_dir, exist_ok=True)
            doc = ET.Element("WwiseDocument", {"Type": "WorkUnit", "ID": "{6D7B82A4-E5D8-4561-B09F-83DEB44AC84D}", "SchemaVersion": "110"})
            states_elem = ET.Element("States")
            doc.append(states_elem)
            wu_elem = ET.Element("WorkUnit", {"Name": "Default Work Unit", "ID": "{6D7B82A4-E5D8-4561-B09F-83DEB44AC84D}", "PersistMode": "Standalone"})
            states_elem.append(wu_elem)
            
            children_list = ET.Element("ChildrenList")
            wu_elem.append(children_list)
            for grp_id, state_ids in state_groups.items():
                grp_name = self._object_name_map.get(grp_id, f"StateGroup_{grp_id}")
                grp_guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_OID, str(grp_id))).upper() + "}"
                sg_elem = ET.Element("StateGroup", {"Name": grp_name, "ID": grp_guid, "ShortID": str(grp_id)})
                children_list.append(sg_elem)
                
                if state_ids:
                    sg_children = ET.Element("ChildrenList")
                    sg_elem.append(sg_children)
                    for sid in state_ids:
                        sname = "None" if sid == 0 else self._object_name_map.get(sid, f"State_{sid}")
                        sguid = "{" + str(uuid.uuid5(uuid.NAMESPACE_OID, f"{grp_id}_{sid}")).upper() + "}"
                        short_id = "0" if sid == 0 else str(sid)
                        s_elem = ET.Element("State", {"Name": sname, "ID": sguid, "ShortID": short_id})
                        sg_children.append(s_elem)
            
            xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(doc, encoding="unicode")
            with open(os.path.join(states_dir, "Default Work Unit.wwu"), "w", encoding="utf-8") as f:
                f.write(xml_str)
                
        # Write Switches/Default Work Unit.wwu
        if switch_groups:
            switches_dir = os.path.join(project_folder, "Switches")
            os.makedirs(switches_dir, exist_ok=True)
            doc = ET.Element("WwiseDocument", {"Type": "WorkUnit", "ID": "{7B4B8E9D-A3D2-4822-8321-44B1578F1CC5}", "SchemaVersion": "110"})
            switches_elem = ET.Element("Switches")
            doc.append(switches_elem)
            wu_elem = ET.Element("WorkUnit", {"Name": "Default Work Unit", "ID": "{7B4B8E9D-A3D2-4822-8321-44B1578F1CC5}", "PersistMode": "Standalone"})
            switches_elem.append(wu_elem)
            
            children_list = ET.Element("ChildrenList")
            wu_elem.append(children_list)
            for grp_id, switch_ids in switch_groups.items():
                grp_name = self._object_name_map.get(grp_id, f"SwitchGroup_{grp_id}")
                grp_guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_OID, str(grp_id))).upper() + "}"
                sg_elem = ET.Element("SwitchGroup", {"Name": grp_name, "ID": grp_guid, "ShortID": str(grp_id)})
                children_list.append(sg_elem)
                
                if switch_ids:
                    sg_children = ET.Element("ChildrenList")
                    sg_elem.append(sg_children)
                    for sid in switch_ids:
                        sname = "None" if sid == 0 else self._object_name_map.get(sid, f"Switch_{sid}")
                        sguid = "{" + str(uuid.uuid5(uuid.NAMESPACE_OID, f"{grp_id}_{sid}")).upper() + "}"
                        short_id = "0" if sid == 0 else str(sid)
                        s_elem = ET.Element("Switch", {"Name": sname, "ID": sguid, "ShortID": short_id})
                        sg_children.append(s_elem)
            
            xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(doc, encoding="unicode")
            with open(os.path.join(switches_dir, "Default Work Unit.wwu"), "w", encoding="utf-8") as f:
                f.write(xml_str)

    def _ensure_sound_references(self, sound_el: ET.Element):
        ref_list = sound_el.find("ReferenceList")
        if ref_list is None:
            ref_list = ET.Element("ReferenceList")
            sound_el.append(ref_list)
        
        has_conv = False
        has_out = False
        for ref in ref_list.findall("Reference"):
            if ref.attrib.get("Name") == "Conversion":
                has_conv = True
            elif ref.attrib.get("Name") == "OutputBus":
                has_out = True
                
        if not has_conv:
            ref_conv = self._create_conversion_ref()
            if ref_conv is not None:
                ref_list.append(ref_conv)
        if not has_out:
            ref_list.append(self._create_output_bus_ref())

        # Inject IsStreamingEnabled based on HIRC metadata
        sound_id_str = sound_el.attrib.get("ShortID")
        is_streamed = True
        if sound_id_str:
            try:
                is_streamed = self._is_streamed(int(sound_id_str))
            except ValueError:
                pass
        prop_list = sound_el.find("PropertyList")
        if prop_list is None:
            prop_list = ET.Element("PropertyList")
            sound_el.insert(0, prop_list)
        self._set_xml_property(prop_list, "IsStreamingEnabled", "bool", "True" if is_streamed else "False")

    def _extract_existing_sounds(self, doc: ET.ElementTree) -> Dict[int, ET.Element]:
        """Extract all sound elements mapped by WEM ID."""
        sound_map = {}
        sound_elements = find_element_by_local_name(doc.getroot(), "Sound")
        for el in sound_elements:
            name = el.attrib.get("Name")
            if not name:
                continue
            
            clean_name = os.path.splitext(name)[0]
            wem_id = 0
            found = False
            
            try:
                wem_id = int(clean_name)
                found = True
            except ValueError:
                match = re.search(r'\d+', clean_name)
                if match:
                    try:
                        wem_id = int(match.group())
                        found = True
                    except ValueError:
                        pass
            
            if found:
                # Clone element
                clone = ET.fromstring(ET.tostring(el))
                sound_map[wem_id] = clone

        return sound_map

    def _build_sound_map_from_originals(
        self,
        objects: List[HircObjectData],
        project_folder: str,
        actor_mixer_doc: ET.ElementTree,
    ) -> Dict[int, ET.Element]:
        """Auto-build a sound_map from WAV files in Originals/SFX and HIRC WEM IDs.

        Called when the user has not manually imported sounds into the Actor-Mixer
        Hierarchy.  We:
          1. Collect every WEM ID referenced by the parsed HIRC objects.
          2. Also collect every *.wav file already sitting in Originals/SFX.
          3. Build a minimal Sound element for each unique ID.
          4. Persist those elements into the Actor-Mixer Work Unit so future
             exports and Wwise itself can see them.
        """
        sfx_dir = os.path.join(project_folder, "Originals", "SFX")

        # Gather all WEM IDs from HIRC
        wem_ids: Set[int] = set()
        for obj in objects:
            if obj.type_id == 2 and obj.media_id > 0:
                wem_ids.add(obj.media_id)
            elif obj.type_id == 11:
                for src_id, _ in getattr(obj, "music_track_sources", []):
                    if src_id > 0:
                        wem_ids.add(src_id)
                for fid in getattr(obj, "playlist_wems", []):
                    if fid > 0:
                        wem_ids.add(fid)

        # Filter to only WEMs that actually exist on disk
        wem_ids = wem_ids.intersection(self._existing_wavs)
        # Add all existing wavs just in case they were missed
        wem_ids.update(self._existing_wavs)

        sound_map: Dict[int, ET.Element] = {}
        for wem_id in wem_ids:
            src_guid = generate_guid()
            sound_guid = generate_guid()
            sound_el = ET.Element("Sound", {
                "Name": str(wem_id),
                "ID": sound_guid,
                "ShortID": str(wem_id),
            })
            
            ref_list = ET.Element("ReferenceList")
            ref_conv = self._create_conversion_ref()
            if ref_conv is not None:
                ref_list.append(ref_conv)
            ref_list.append(self._create_output_bus_ref())
            sound_el.append(ref_list)
            
            # ActiveSourceList
            active_list = ET.Element("ActiveSourceList")
            active_list.append(ET.Element("ActiveSource", {
                "Name": str(wem_id),
                "ID": src_guid,
                "Platform": "Linked",
            }))
            sound_el.append(active_list)
            # ChildrenList  (AudioFileSource)
            cl = ET.Element("ChildrenList")
            afs = ET.Element("AudioFileSource", {
                "Name": str(wem_id),
                "ID": src_guid,
                "ShortID": str(wem_id),
            })
            lang = ET.Element("Language")
            lang.text = "SFX"
            afs.append(lang)
            af = ET.Element("AudioFile")
            af.text = f"{wem_id}.wav"
            afs.append(af)
            media_list = ET.Element("MediaIDList")
            media_list.append(ET.Element("MediaID", {"ID": str(wem_id)}))
            afs.append(media_list)
            cl.append(afs)
            sound_el.append(cl)

            sound_map[wem_id] = sound_el

        self._log.info("WwiseExporter",
                       f"Auto-built sound_map with {len(sound_map)} entries from Originals/SFX",
                       operation="export")
        return sound_map


    def _extract_references(self, doc: ET.ElementTree, project_folder: str):
        """Extract Bus and Conversion parameters from project templates."""
        # Find default OutputBus reference
        for ref in find_element_by_local_name(doc.getroot(), "Reference"):
            if ref.attrib.get("Name") == "OutputBus":
                obj_ref = find_child_by_local_name(ref, "ObjectRef")
                if obj_ref is not None:
                    self._master_bus_id = obj_ref.attrib.get("ID", DEFAULT_MASTER_BUS_ID)
                    self._master_bus_wwu_id = obj_ref.attrib.get("WorkUnitID", DEFAULT_MASTER_BUS_WU_ID)

        # Probe Conversion Settings wwu
        conv_dir = os.path.join(project_folder, "Conversion Settings")
        if os.path.exists(conv_dir):
            for file in os.listdir(conv_dir):
                if file.endswith(".wwu") and file != "Factory Conversion Settings.wwu":
                    try:
                        conv_doc = ET.parse(os.path.join(conv_dir, file))
                        for wu in find_element_by_local_name(conv_doc.getroot(), "WorkUnit"):
                            self._conversion_settings_wwu_id = wu.attrib.get("ID", "")
                            break
                        for conv in find_element_by_local_name(conv_doc.getroot(), "Conversion"):
                            if conv.attrib.get("Name") == "Default Conversion Settings":
                                self._conversion_settings_id = conv.attrib.get("ID", "")
                                break
                    except Exception:
                        pass

    def _build_object_name_map(self, objects: List[HircObjectData], hash_lookup: HashLookup):
        """Map every Mixer, Container and Sound target ID to a human readable event string."""
        self._object_name_map.clear()
        
        # Safe default names
        for obj in objects:
            if obj.type_id == 5:
                self._object_name_map[obj.object_id] = f"Container_{obj.object_id}"
            elif obj.type_id == 6:
                self._object_name_map[obj.object_id] = f"Switch_{obj.object_id}"
            elif obj.type_id == 7:
                self._object_name_map[obj.object_id] = f"Mixer_{obj.object_id}"
            elif obj.type_id == 9:
                self._object_name_map[obj.object_id] = f"Blend_{obj.object_id}"
            elif obj.type_id == 10:
                self._object_name_map[obj.object_id] = f"MusicSegment_{obj.object_id}"
            elif obj.type_id == 11:
                self._object_name_map[obj.object_id] = f"MusicTrack_{obj.object_id}"

        # Assign Event-based target names
        hirc_map = {o.object_id: o for o in objects}
        assigned_names = {}
        for obj in objects:
            if obj.type_id != 4:  # Event
                continue
            
            event_name = hash_lookup.resolve(obj.object_id)
            if not event_name:
                event_name = f"Event_{obj.object_id}"

            pr = BinaryReader(obj.raw_payload[4:])
            if pr.remaining() < 1:
                continue
            action_count = pr.read_u8()
            for _ in range(action_count):
                if pr.remaining() < 4:
                    break
                action_id = pr.read_u32()
                action_obj = hirc_map.get(action_id)
                if not action_obj:
                    continue
                
                apr = BinaryReader(action_obj.raw_payload[4:])
                if apr.remaining() < 6:
                    continue
                action_type = apr.read_u16()
                target_id = apr.read_u32()
                target_obj = hirc_map.get(target_id)
                if target_obj:
                    if target_obj.type_id in (5, 6, 7, 9):
                        name = f"{event_name}_container"
                    elif target_obj.type_id == 10:
                        name = f"{event_name}_segment"
                    elif target_obj.type_id == 11:
                        name = f"{event_name}_track"
                    elif target_obj.type_id == 12:
                        name = f"{event_name}_switch"
                    elif target_obj.type_id == 13:
                        name = f"{event_name}_playlist"
                    else:
                        name = event_name
                else:
                    name = event_name
                
                # Check for naming conflicts and make unique
                existing_id = assigned_names.get(name)
                if existing_id is not None and existing_id != target_id:
                    name = f"{name}_{target_id}"
                
                self._object_name_map[target_id] = name
                assigned_names[name] = target_id

    def _find_root_parent(self, target_id: int, hirc_map: Dict[int, HircObjectData], bnk_version: int) -> int:
        """Find the root container ID by traversing NodeBaseParams direct parent chains."""
        curr = target_id
        visited = set()
        
        while curr not in visited:
            visited.add(curr)
            obj = hirc_map.get(curr)
            if not obj:
                break
            
            # Parse parent ID using NodeBaseParams reader
            parent_id = obj.parent_id
            if parent_id == 0:
                # Fallback to binary payload parsing if parent_id is not set (e.g. in unit tests)
                if obj.type_id == 2:  # Sound
                    pr = BinaryReader(obj.raw_payload[4:])
                    if pr.remaining() >= 14:
                        pr.skip(14)
                        p = read_node_base_params(pr, bnk_version)
                        parent_id = p.get('direct_parent_id', 0)
                elif obj.type_id in (5, 6, 7, 9, 10):  # Containers & segments
                    pr = BinaryReader(obj.raw_payload[4:])
                    p = read_node_base_params(pr, bnk_version)
                    parent_id = p.get('direct_parent_id', 0)
                elif obj.type_id == 11:  # Music Track
                    pr = BinaryReader(obj.raw_payload[4:])
                    if pr.remaining() >= 4:
                        count = pr.read_u32()
                        if pr.remaining() >= count * 4:
                            pr.skip(count * 4)
                            p = read_node_base_params(pr, bnk_version)
                            parent_id = p.get('direct_parent_id', 0)
            
            if parent_id == 0 or parent_id == curr:
                break
            if parent_id not in hirc_map:
                break
            curr = parent_id
            
        return curr

    def _build_hierarchy_recursive(self,
                                  target_id: int,
                                  hirc_map: Dict[int, HircObjectData],
                                  sound_map: Dict[int, ET.Element],
                                  project_folder: str,
                                  bnk_version: int,
                                  suggested_name: Optional[str] = None) -> Optional[ET.Element]:
        """Reconstruct XML container element from a parsed HIRC object."""
        if target_id in self._recursion_stack:
            return None
        
        self._recursion_stack.add(target_id)
        try:
            obj = hirc_map.get(target_id)
            if not obj:
                return None
            
            # --- 1. Sound Object (Type 2) ---
            if obj.type_id == 2:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid
                
                # Parse wem ID and base params
                pr = BinaryReader(obj.raw_payload[4:])
                if pr.remaining() < 14:
                    return None
                pr.read_u32()  # plugin_id
                pr.read_u8()   # stream_type
                wem_id = pr.read_u32()
                pr.read_u32()  # in_memory_size
                pr.read_u8()   # source_bits
                p = read_node_base_params(pr, bnk_version)
                
                if wem_id in sound_map:
                    elem = ET.fromstring(ET.tostring(sound_map[wem_id]))
                    elem = self._repair_sound_element(elem, wem_id, project_folder)
                    elem.attrib["ID"] = guid
                    elem.attrib["ShortID"] = str(target_id)
                    # Keep original file name, stripping previous target_id suffix if present
                    orig_name = elem.attrib.get("Name", str(wem_id))
                    if "_" in orig_name:
                        parts = orig_name.split("_")
                        if parts[0].isdigit():
                            orig_name = parts[0]
                    elem.attrib["Name"] = orig_name
                    self._apply_node_properties(elem, p)
                    return elem
                else:
                    elem = self._create_sound_element(wem_id, target_id, guid, project_folder)
                    # Keep original file name, stripping previous target_id suffix if present
                    orig_name = elem.attrib.get("Name", str(wem_id))
                    if "_" in orig_name:
                        parts = orig_name.split("_")
                        if parts[0].isdigit():
                            orig_name = parts[0]
                    elem.attrib["Name"] = orig_name
                    self._apply_node_properties(elem, p)
                    return elem

            # --- 2. Random Container (Type 5) ---
            elif obj.type_id == 5:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid
                
                pr = BinaryReader(obj.raw_payload[4:])
                p = read_node_base_params(pr, bnk_version)
                
                # Random Container Specifics
                loop_count = 1
                transition_time = 0.0
                avoid_repeat = 0
                random_mode = 0
                mode = 0
                by_bit_vector = 0
                playlist = []
                
                if pr.remaining() >= 24:
                    loop_count = pr.read_u16()
                    pr.read_u16()  # loopModMin
                    pr.read_u16()  # loopModMax
                    transition_time = pr.read_f32()
                    pr.read_f32()  # transitionTimeModMin
                    pr.read_f32()  # transitionTimeModMax
                    avoid_repeat = pr.read_u16()
                    pr.read_u8()   # transitionMode
                    random_mode = pr.read_u8()
                    mode = pr.read_u8()
                    by_bit_vector = pr.read_u8()

                children_ids = obj.child_ids if obj.child_ids else []
                if not children_ids:
                    if pr.remaining() >= 4:
                        count = pr.read_u32()
                        for _ in range(count):
                            if pr.remaining() >= 4:
                                children_ids.append(pr.read_u32())
                            
                if pr.remaining() >= 2:
                    playlist_count = pr.read_u16()
                    for _ in range(playlist_count):
                        if pr.remaining() >= 8:
                            playlist.append({
                                'id': pr.read_u32(),
                                'weight': pr.read_i32()
                            })
                            
                name = suggested_name or self._object_name_map.get(target_id, f"Container_{target_id}")
                elem = ET.Element("RandomSequenceContainer", {
                    "Name": name,
                    "ID": guid,
                    "ShortID": str(target_id)
                })
                
                # Apply base properties
                self._apply_node_properties(elem, p)
                
                # Custom Container Properties
                prop_list = elem.find("PropertyList")
                if prop_list is None:
                    prop_list = ET.Element("PropertyList")
                    elem.insert(0, prop_list)
                    
                # RandomOrSequence (0=Sequence, 1=Random)
                xml_mode = 1 if mode == 0 else 0
                self._set_xml_property(prop_list, "RandomOrSequence", "int16", str(xml_mode))
                
                # NormalOrShuffle (0=Shuffle, 1=Standard)
                xml_shuffle = 1 if random_mode == 0 else 0
                self._set_xml_property(prop_list, "NormalOrShuffle", "int16", str(xml_shuffle))
                self._set_xml_property(prop_list, "NormalOrContinuous", "int16", str((by_bit_vector >> 3) & 1))
                self._set_xml_property(prop_list, "TransitionTime", "Real64", f"{transition_time:.17g}")
                self._set_xml_property(prop_list, "AvoidRepeatCount", "int16", str(avoid_repeat))
                self._set_xml_property(prop_list, "ResetPlayListAtEachPlay", "bool", "True" if ((by_bit_vector >> 1) & 1) == 1 else "False")
                self._set_xml_property(prop_list, "IsGlobal", "bool", "True" if ((by_bit_vector >> 4) & 1) == 1 else "False")
                self._set_xml_property(prop_list, "RestartBackward", "bool", "True" if ((by_bit_vector >> 2) & 1) == 1 else "False")
                
                # References
                ref_list = ET.Element("ReferenceList")
                ref_list.append(self._create_output_bus_ref())
                elem.append(ref_list)

                # Children list
                children_list = ET.Element("ChildrenList")
                elem.append(children_list)
                for child_id in children_ids:
                    child_elem = self._build_hierarchy_recursive(child_id, hirc_map, sound_map, project_folder, bnk_version)
                    if child_elem is not None:
                        # Weight overrides
                        item_weight = next((item['weight'] for item in playlist if item['id'] == child_id), 50000)
                        if item_weight != 50000:
                            child_prop_list = child_elem.find("PropertyList")
                            if child_prop_list is None:
                                child_prop_list = ET.Element("PropertyList")
                                child_elem.insert(0, child_prop_list)
                            self._set_xml_property(child_prop_list, "Weight", "Real64", f"{(item_weight / 1000.0):.17g}")
                        children_list.append(child_elem)
                
                self._resolve_child_name_conflicts(children_list)
                        
                playlist_elem = ET.Element("Playlist")
                if len(children_list) == 0:
                    dummy_sound = self._create_dummy_sound_element(name, sound_map, project_folder)
                    children_list.append(dummy_sound)
                    playlist_elem.append(ET.Element("ItemRef", {
                        "Name": dummy_sound.attrib["Name"],
                        "ID": dummy_sound.attrib["ID"]
                    }))
                else:
                    # Playlist Mapping — always append <Playlist/> (required by schema).
                    # Wwise schema validation rejects a RandomSequenceContainer with children
                    # but an empty <Playlist>. If no explicit playlist was stored in the BNK
                    # (playlist_count == 0), fall back to auto-generating ItemRef entries from
                    # every child that was successfully built.
                    if playlist:
                        for item in playlist:
                            child_guid = self._container_id_to_guid.get(item['id'])
                            if child_guid:
                                playlist_elem.append(ET.Element("ItemRef", {
                                    "Name": str(item['id']),
                                    "ID": child_guid
                                }))
                    # Fallback: auto-populate from built children when BNK had no explicit playlist
                    if len(list(playlist_elem)) == 0:
                        for child_id in children_ids:
                            child_guid = self._container_id_to_guid.get(child_id)
                            if child_guid:
                                playlist_elem.append(ET.Element("ItemRef", {
                                    "Name": str(child_id),
                                    "ID": child_guid
                                }))
                elem.append(playlist_elem)
                    
                return elem

            # --- 3. Switch Container (Type 6) ---
            elif obj.type_id == 6:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid
                
                pr = BinaryReader(obj.raw_payload[4:])
                p = read_node_base_params(pr, bnk_version)
                
                # Switch properties
                if pr.remaining() >= 10:
                    pr.read_u8()   # group_type
                    pr.read_u32()  # group_id
                    pr.read_u32()  # default_switch_id
                    pr.read_u8()   # continuous_validation
                    
                children_ids = obj.child_ids if obj.child_ids else []
                if not children_ids:
                    if pr.remaining() >= 4:
                        count = pr.read_u32()
                        for _ in range(count):
                            if pr.remaining() >= 4:
                                children_ids.append(pr.read_u32())
                            
                name = suggested_name or self._object_name_map.get(target_id, f"Switch_{target_id}")
                elem = ET.Element("SwitchContainer", {
                    "Name": name,
                    "ID": guid,
                    "ShortID": str(target_id)
                })
                
                self._apply_node_properties(elem, p)
                
                # References
                ref_list = ET.Element("ReferenceList")
                ref_list.append(self._create_output_bus_ref())
                elem.append(ref_list)
                
                children_list = ET.Element("ChildrenList")
                elem.append(children_list)
                for child_id in children_ids:
                    child_elem = self._build_hierarchy_recursive(child_id, hirc_map, sound_map, project_folder, bnk_version)
                    if child_elem is not None:
                        children_list.append(child_elem)
                
                self._resolve_child_name_conflicts(children_list)
                
                if len(children_list) == 0:
                    children_list.append(self._create_dummy_sound_element(name, sound_map, project_folder))

                # GroupingInfo is required by the Wwise XML schema (ObjectDataSchema.110.xsd)
                # for SwitchContainer — even if we don't know the switch group, an empty element
                # satisfies the validator and Wwise fills it in on project load.
                grouping_info = ET.Element("GroupingInfo")
                grouping_info.append(ET.Element("GroupingBehaviorList"))
                grouping_info.append(ET.Element("GroupingList"))
                elem.append(grouping_info)
                        
                return elem

            # --- 4. Actor Mixer (Type 7) ---
            elif obj.type_id == 7:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid
                
                pr = BinaryReader(obj.raw_payload[4:])
                p = read_node_base_params(pr, bnk_version)
                
                children_ids = obj.child_ids if obj.child_ids else []
                if not children_ids:
                    if pr.remaining() >= 4:
                        count = pr.read_u32()
                        for _ in range(count):
                            if pr.remaining() >= 4:
                                children_ids.append(pr.read_u32())
                            
                name = suggested_name or self._object_name_map.get(target_id, f"Mixer_{target_id}")
                elem = ET.Element("ActorMixer", {
                    "Name": name,
                    "ID": guid,
                    "ShortID": str(target_id)
                })
                
                self._apply_node_properties(elem, p)
                
                # References
                ref_list = ET.Element("ReferenceList")
                ref_list.append(self._create_output_bus_ref())
                elem.append(ref_list)
                
                children_list = ET.Element("ChildrenList")
                elem.append(children_list)
                for child_id in children_ids:
                    child_elem = self._build_hierarchy_recursive(child_id, hirc_map, sound_map, project_folder, bnk_version)
                    if child_elem is not None:
                        children_list.append(child_elem)
                
                self._resolve_child_name_conflicts(children_list)

                # ActorMixer schema (ObjectDataSchema.99 line 602): only allows
                # Comment, PropertyList, ReferenceList, ChildrenList, PositioningInfo, StateInfo.
                # ObjectLists is NOT valid here (it's only for Music types).
                        
                return elem

            # --- 5. Blend Container (Type 9) ---
            elif obj.type_id == 9:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid
                
                pr = BinaryReader(obj.raw_payload[4:])
                p = read_node_base_params(pr, bnk_version)
                
                children_ids = obj.child_ids if obj.child_ids else []
                if not children_ids:
                    if pr.remaining() >= 4:
                        count = pr.read_u32()
                        for _ in range(count):
                            if pr.remaining() >= 4:
                                children_ids.append(pr.read_u32())
                            
                name = suggested_name or self._object_name_map.get(target_id, f"Blend_{target_id}")
                elem = ET.Element("BlendContainer", {
                    "Name": name,
                    "ID": guid,
                    "ShortID": str(target_id)
                })
                
                self._apply_node_properties(elem, p)
                
                # References
                ref_list = ET.Element("ReferenceList")
                ref_list.append(self._create_output_bus_ref())
                elem.append(ref_list)
                
                children_list = ET.Element("ChildrenList")
                elem.append(children_list)
                for child_id in children_ids:
                    child_elem = self._build_hierarchy_recursive(child_id, hirc_map, sound_map, project_folder, bnk_version)
                    if child_elem is not None:
                        children_list.append(child_elem)
                
                self._resolve_child_name_conflicts(children_list)

                # BlendTrackList is required by the Wwise XML schema for BlendContainer.
                # Schema (ObjectDataSchema.99+): BlendTrack children in order:
                #   PropertyList (opt) -> ReferenceList (opt) -> BlendTrackAssocList (opt)
                # BlendTrackAssocList -> BlendTrackAssoc -> ItemRef (ObjectRefType)
                # The ItemRef points to the child Sound/Container GUID.
                # ChildRef and RtpcList do NOT exist in the schema.
                blend_track_list = ET.Element("BlendTrackList")
                if len(children_list) == 0:
                    dummy_sound = self._create_dummy_sound_element(name, sound_map, project_folder)
                    children_list.append(dummy_sound)
                    bt = ET.Element("BlendTrack", {
                        "Name": "Track_0",
                        "ID": generate_guid(),
                    })
                    # BlendTrackAssocList links this track to its child Sound object
                    assoc_list = ET.Element("BlendTrackAssocList")
                    assoc = ET.Element("BlendTrackAssoc")
                    assoc.append(ET.Element("ItemRef", {
                        "Name": dummy_sound.attrib["Name"],
                        "ID": dummy_sound.attrib["ID"],
                    }))
                    assoc_list.append(assoc)
                    bt.append(assoc_list)
                    blend_track_list.append(bt)
                else:
                    for child_id in children_ids:
                        child_guid = self._container_id_to_guid.get(child_id)
                        if not child_guid:
                            continue
                        child_name = self._object_name_map.get(child_id, str(child_id))
                        bt = ET.Element("BlendTrack", {
                            "Name": child_name,
                            "ID": generate_guid(),
                        })
                        # BlendTrackAssocList links this track to its child Sound object
                        assoc_list = ET.Element("BlendTrackAssocList")
                        assoc = ET.Element("BlendTrackAssoc")
                        assoc.append(ET.Element("ItemRef", {
                            "Name": child_name,
                            "ID": child_guid,
                        }))
                        assoc_list.append(assoc)
                        bt.append(assoc_list)
                        blend_track_list.append(bt)
                elem.append(blend_track_list)
                        
                return elem

            # --- 6. Music Segment (Type 10) ---
            elif obj.type_id == 10:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid
                
                pr = BinaryReader(obj.raw_payload[4:])
                p = read_node_base_params(pr, bnk_version)
                
                name = suggested_name or self._object_name_map.get(target_id, f"MusicSegment_{target_id}")
                elem = ET.Element("MusicSegment", {
                    "Name": name,
                    "ID": guid,
                    "ShortID": str(target_id)
                })
                
                self._apply_node_properties(elem, p)
                
                # References
                ref_list = ET.Element("ReferenceList")
                ref_list.append(self._create_output_bus_ref())
                elem.append(ref_list)
                
                children_list = ET.Element("ChildrenList")
                elem.append(children_list)
                for child_id in obj.child_ids:
                    child_elem = self._build_hierarchy_recursive(child_id, hirc_map, sound_map, project_folder, bnk_version)
                    if child_elem is not None:
                        children_list.append(child_elem)
                
                self._resolve_child_name_conflicts(children_list)
                
                # Calculate duration of the music segment
                # Prefer the binary-parsed duration (exact, no I/O needed)
                if obj.segment_duration_ms > 0:
                    segment_duration_ms = obj.segment_duration_ms
                else:
                    segment_duration_ms = self._get_music_segment_duration_ms(obj, hirc_map, project_folder)
                
                # Cues ObjectLists
                object_lists = ET.Element("ObjectLists")
                obj_list_cues = ET.Element("ObjectList", {"Name": "Cues"})
                
                # Entry Cue
                entry_ref = ET.Element("Reference")
                entry_loc = ET.Element("Local")
                entry_cue = ET.Element("MusicCue", {
                    "Name": "Entry Cue",
                    "ID": generate_guid()
                })
                entry_loc.append(entry_cue)
                entry_ref.append(entry_loc)
                obj_list_cues.append(entry_ref)
                
                # Exit Cue
                exit_ref = ET.Element("Reference")
                exit_loc = ET.Element("Local")
                exit_cue = ET.Element("MusicCue", {
                    "Name": "Exit Cue",
                    "ID": generate_guid()
                })
                exit_props = ET.Element("PropertyList")
                exit_props.append(ET.Element("Property", {
                    "Name": "CueType",
                    "Type": "int16",
                    "Value": "1"
                }))
                exit_props.append(ET.Element("Property", {
                    "Name": "TimeMs",
                    "Type": "Real64",
                    "Value": f"{segment_duration_ms:.10f}"
                }))
                exit_cue.append(exit_props)
                exit_loc.append(exit_cue)
                exit_ref.append(exit_loc)
                obj_list_cues.append(exit_ref)
                
                object_lists.append(obj_list_cues)
                elem.append(object_lists)
                
                return elem

            # --- 7. Music Track (Type 11) ---
            elif obj.type_id == 11:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid
                
                unique_wems = []
                seen_wems = set()
                for source_id, _ in obj.music_track_sources:
                    if source_id > 0 and source_id not in seen_wems and source_id in self._existing_wavs:
                        seen_wems.add(source_id)
                        unique_wems.append(source_id)
                for file_id in obj.playlist_wems:
                    if file_id > 0 and file_id not in seen_wems and file_id in self._existing_wavs:
                        seen_wems.add(file_id)
                        unique_wems.append(file_id)

                p = self._get_music_track_node_base_params(obj, bnk_version)
                
                name = suggested_name or self._object_name_map.get(target_id, f"MusicTrack_{target_id}")
                elem = ET.Element("MusicTrack", {
                    "Name": name,
                    "ID": guid,
                    "ShortID": str(target_id)
                })
                
                self._apply_node_properties(elem, p)
                
                prop_list = elem.find("PropertyList")
                if prop_list is None:
                    prop_list = ET.Element("PropertyList")
                    elem.insert(0, prop_list)
                is_streamed = self._is_streamed(target_id)
                self._set_xml_property(prop_list, "IsStreamingEnabled", "bool", "True" if is_streamed else "False")
                
                # References — OutputBus AND Conversion are both required.
                # Without Conversion on the MusicTrack, Wwise silently skips
                # encoding every AudioFileSource in this track's ChildrenList.
                ref_list = ET.Element("ReferenceList")
                ref_list.append(self._create_output_bus_ref())
                ref_list.append(self._create_conversion_ref())
                elem.append(ref_list)

                
                # ChildrenList (AudioFileSource elements)
                children_list = ET.Element("ChildrenList")
                elem.append(children_list)
                
                # We need to construct clips in ObjectLists
                clips_refs = []
                
                for wem_id in unique_wems:
                    child_elem = self._get_audio_file_source_element(wem_id, sound_map, project_folder)
                    children_list.append(child_elem)
                    
                    source_guid = child_elem.attrib.get("ID")
                    source_name = child_elem.attrib.get("Name")
                    
                    duration_ms = self._get_wem_duration_ms(wem_id, project_folder)
                    
                    # Build MusicClip
                    clip_guid = generate_guid()
                    clip_elem = ET.Element("MusicClip", {
                        "Name": source_name,
                        "ID": clip_guid
                    })
                    
                    # PropertyList for clip
                    prop_list = ET.Element("PropertyList")
                    prop_list.append(ET.Element("Property", {
                        "Name": "EndTrimOffset",
                        "Type": "Real64",
                        "Value": str(duration_ms)
                    }))
                    prop_list.append(ET.Element("Property", {
                        "Name": "FadeInShape",
                        "Type": "int16",
                        "Value": "1"
                    }))
                    prop_list.append(ET.Element("Property", {
                        "Name": "FadeOutShape",
                        "Type": "int16",
                        "Value": "8"
                    }))
                    clip_elem.append(prop_list)
                    
                    # AudioSourceRef
                    clip_elem.append(ET.Element("AudioSourceRef", {
                        "Name": source_name,
                        "ID": source_guid
                    }))
                    
                    # Empty PropertyCurveList to match structure
                    clip_elem.append(ET.Element("PropertyCurveList"))
                    
                    # Wrap MusicClip inside Reference -> Local
                    ref = ET.Element("Reference")
                    loc = ET.Element("Local")
                    loc.append(clip_elem)
                    ref.append(loc)
                    clips_refs.append(ref)
                
                # TransitionList
                transition_list = ET.Element("TransitionList")
                music_trans = ET.Element("MusicTransition", {
                    "Name": "Root",
                    "ID": generate_guid()
                })
                # PropertyList for transition
                mt_prop_list = ET.Element("PropertyList")
                for prop_name, prop_val, prop_type in [
                    ("ExitSourceAt", "7", "int16"),
                    ("IsFolder", "True", "bool"),
                    ("PlayDestinationPreEntry", "True", "bool"),
                    ("PlaySourcePostExit", "True", "bool"),
                    ("PlayTransitionPostExit", "True", "bool"),
                    ("PlayTransitionPreEntry", "True", "bool"),
                ]:
                    mt_prop_list.append(ET.Element("Property", {
                        "Name": prop_name,
                        "Type": prop_type,
                        "Value": prop_val
                    }))
                music_trans.append(mt_prop_list)
                music_trans.append(ET.Element("TransitionInfo"))
                transition_list.append(music_trans)
                elem.append(transition_list)
                
                # ObjectLists -> Sequences -> MusicTrackSequence -> Clips
                object_lists = ET.Element("ObjectLists")
                obj_list_seq = ET.Element("ObjectList", {"Name": "Sequences"})
                seq_ref = ET.Element("Reference")
                seq_local = ET.Element("Local")
                
                music_seq = ET.Element("MusicTrackSequence", {
                    "Name": "",
                    "ID": generate_guid()
                })
                
                seq_obj_lists = ET.Element("ObjectLists")
                obj_list_clips = ET.Element("ObjectList", {"Name": "Clips"})
                for clip_ref in clips_refs:
                    obj_list_clips.append(clip_ref)
                
                seq_obj_lists.append(obj_list_clips)
                music_seq.append(seq_obj_lists)
                
                seq_local.append(music_seq)
                seq_ref.append(seq_local)
                obj_list_seq.append(seq_ref)
                object_lists.append(obj_list_seq)
                elem.append(object_lists)
                
                return elem

            # --- 8. Music Switch Container (Type 12) ---
            elif obj.type_id == 12:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid
                
                pr = BinaryReader(obj.raw_payload[4:])
                p = read_node_base_params(pr, bnk_version)
                
                name = suggested_name or self._object_name_map.get(target_id, f"MusicSwitch_{target_id}")
                elem = ET.Element("MusicSwitchContainer", {
                    "Name": name,
                    "ID": guid,
                    "ShortID": str(target_id)
                })
                
                self._apply_node_properties(elem, p)
                
                # References
                ref_list = ET.Element("ReferenceList")
                ref_list.append(self._create_output_bus_ref())
                elem.append(ref_list)
                
                # Build children first so we have their GUIDs for EntryList
                children_list = ET.Element("ChildrenList")
                elem.append(children_list)
                for child_id in obj.child_ids:
                    child_elem = self._build_hierarchy_recursive(child_id, hirc_map, sound_map, project_folder, bnk_version)
                    if child_elem is not None:
                        children_list.append(child_elem)
                
                self._resolve_child_name_conflicts(children_list)
                
                # ArgumentList (States/Switches this container listens to)
                if hasattr(obj, 'switch_arguments') and obj.switch_arguments:
                    arg_list = ET.Element("ArgumentList")
                    for group_id, group_type in obj.switch_arguments:
                        arg_name = self._object_name_map.get(group_id, f"StateGroup_{group_id}")
                        arg_guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_OID, str(group_id))).upper() + "}"
                        arg_ref = ET.Element("ArgumentRef", {
                            "Name": arg_name,
                            "ID": arg_guid
                        })
                        arg_list.append(arg_ref)
                    elem.append(arg_list)

                # EntryList (Paths/Associations mapping states to children)
                if hasattr(obj, 'switch_nodes') and obj.switch_nodes:
                    # Grab the primary group_id to generate unique state GUIDs
                    primary_group_id = 0
                    if hasattr(obj, 'switch_arguments') and obj.switch_arguments:
                        primary_group_id = obj.switch_arguments[0][0]

                    entry_list = ET.Element("EntryList")
                    for state_id, child_id in obj.switch_nodes:
                        child_guid = self._container_id_to_guid.get(child_id)
                        if not child_guid:
                            continue # Child was probably missing from HIRC
                        
                        entry = ET.Element("Entry")
                        
                        # state_id == 0 means Default Path (no <Path> element)
                        if state_id != 0:
                            path = ET.Element("Path")
                            state_name = self._object_name_map.get(state_id, f"State_{state_id}")
                            state_guid = "{" + str(uuid.uuid5(uuid.NAMESPACE_OID, f"{primary_group_id}_{state_id}")).upper() + "}"
                            path_elem_ref = ET.Element("PathElementRef", {
                                "Name": state_name,
                                "ID": state_guid
                            })
                            path.append(path_elem_ref)
                            entry.append(path)
                        
                        audio_node_info = ET.Element("AudioNodeInfo")
                        child_name = self._object_name_map.get(child_id, f"Object_{child_id}")
                        audio_node_ref = ET.Element("AudioNodeRef", {
                            "Name": child_name,
                            "ID": child_guid,
                            "Platform": "Linked"
                        })
                        audio_node_info.append(audio_node_ref)
                        entry.append(audio_node_info)
                        
                        entry_list.append(entry)
                    elem.append(entry_list)
                        
                return elem

            # --- 9. Music Playlist Container / RanSeq (Type 13) ---
            elif obj.type_id == 13:
                guid = generate_guid()
                self._container_id_to_guid[target_id] = guid

                pr = BinaryReader(obj.raw_payload[4:])
                p = read_node_base_params(pr, bnk_version)

                name = suggested_name or self._object_name_map.get(target_id, f"MusicPlaylist_{target_id}")
                elem = ET.Element("MusicPlaylistContainer", {
                    "Name": name,
                    "ID": guid,
                    "ShortID": str(target_id)
                })

                self._apply_node_properties(elem, p)

                # References
                ref_list = ET.Element("ReferenceList")
                ref_list.append(self._create_output_bus_ref())
                elem.append(ref_list)

                # Build ChildrenList (all MusicSegment child elements)
                children_list = ET.Element("ChildrenList")
                elem.append(children_list)
                segment_guid_map: Dict[int, str] = {}  # segment_id -> GUID
                for child_id in obj.child_ids:
                    child_elem = self._build_hierarchy_recursive(child_id, hirc_map, sound_map, project_folder, bnk_version)
                    if child_elem is not None:
                        children_list.append(child_elem)
                        segment_guid_map[child_id] = child_elem.attrib.get("ID", "")
                
                self._resolve_child_name_conflicts(children_list)

                # Generate Playlist element from the parsed playlist_items tree
                playlist = ET.Element("Playlist")
                if obj.playlist_items:
                    for top_item in obj.playlist_items:
                        playlist.append(self._build_ranseq_playlist_item(top_item, segment_guid_map, hirc_map))
                else:
                    # Fallback: flat playlist with one item per segment child
                    root_item = ET.Element("MusicPlaylistItem", {
                        "Name": "", "ID": generate_guid(), "ShortID": generate_short_id()
                    })
                    root_props = ET.Element("PropertyList")
                    root_props.append(ET.Element("Property", {"Name": "PlaylistItemType", "Type": "int16", "Value": "0"}))
                    root_item.append(root_props)
                    root_children = ET.Element("ChildrenList")
                    for child_elem in children_list:
                        child_guid = child_elem.attrib.get("ID", "")
                        child_name = child_elem.attrib.get("Name", "")
                        item = ET.Element("MusicPlaylistItem", {
                            "Name": "", "ID": generate_guid(), "ShortID": generate_short_id()
                        })
                        item_props = ET.Element("PropertyList")
                        item_props.append(ET.Element("Property", {"Name": "PlaylistItemType", "Type": "int16", "Value": "0"}))
                        item.append(item_props)
                        item.append(ET.Element("SegmentRef", {"Name": child_name, "ID": child_guid}))
                        root_children.append(item)
                    root_item.append(root_children)
                    playlist.append(root_item)

                elem.append(playlist)
                return elem

        finally:
            self._recursion_stack.remove(target_id)
        return None

    def _create_sound_element(self, wem_id: int, sound_id: int, sound_guid: str, project_folder: str) -> ET.Element:
        """Create empty default Sound element if not previously imported into work unit."""
        source_guid = generate_guid()
        wem_name = str(wem_id)
        
        elem = ET.Element("Sound", {
            "Name": wem_name,
            "ID": sound_guid,
            "ShortID": str(sound_id)
        })
        
        # Property list
        prop_list = ET.Element("PropertyList")
        is_streamed = self._is_streamed(sound_id)
        self._set_xml_property(prop_list, "IsStreamingEnabled", "bool", "True" if is_streamed else "False")
        elem.append(prop_list)
        
        # References
        ref_list = ET.Element("ReferenceList")
        ref_list.append(self._create_output_bus_ref())
        ref_list.append(self._create_conversion_ref())
        elem.append(ref_list)
        
        # Children (AudioFileSource)
        children_list = ET.Element("ChildrenList")
        audio_src = ET.Element("AudioFileSource", {
            "Name": wem_name,
            "ID": source_guid,
            "ShortID": str(wem_id)
        })
        lang_elem = ET.Element("Language")
        is_sfx = "sfx" in project_folder.lower() or not ("vo" in project_folder.lower() or "voice" in project_folder.lower())
        lang_elem.text = "SFX" if is_sfx else "English(US)"
        audio_src.append(lang_elem)
        file_elem = ET.Element("AudioFile")
        file_elem.text = f"{wem_id}.wav"
        audio_src.append(file_elem)
        
        media_id_list = ET.Element("MediaIDList")
        media_id_list.append(ET.Element("MediaID", {"ID": str(wem_id)}))
        audio_src.append(media_id_list)
        
        children_list.append(audio_src)
        elem.append(children_list)
        
        # Active sources
        active_src_list = ET.Element("ActiveSourceList")
        active_src_list.append(ET.Element("ActiveSource", {
            "Name": wem_name,
            "ID": source_guid,
            "Platform": "Linked"
        }))
        elem.append(active_src_list)
        
        return elem

    def _repair_sound_element(self, sound_el: ET.Element, wem_id: int, project_folder: str) -> ET.Element:
        """Fix/adjust references and settings on an extracted Wwise sound element."""
        # Fix conversion references
        for ref in find_element_by_local_name(sound_el, "Reference"):
            if ref.attrib.get("Name") == "Conversion":
                obj_ref = find_child_by_local_name(ref, "ObjectRef")
                if obj_ref is not None:
                    obj_ref.attrib["WorkUnitID"] = self._conversion_settings_wwu_id
                    obj_ref.attrib["ID"] = self._conversion_settings_id

        # Update AudioFile streaming/language flags
        audio_file = find_element_by_local_name(sound_el, "AudioFile")
        if audio_file:
            audio_file_el = audio_file[0]
            if not audio_file_el.text:
                audio_file_el.text = f"{wem_id}.wav"
            
            # Check localization path
            sfx_path = os.path.exists(os.path.join(project_folder, "Originals", "SFX", f"{wem_id}.wav"))
            
            p_elem = None
            for parent in sound_el.iter():
                if audio_file_el in parent:
                    p_elem = parent
                    break
                    
            if p_elem is not None:
                lang = find_child_by_local_name(p_elem, "Language")
                if lang is not None:
                    if not lang.text:
                        lang.text = "SFX" if sfx_path else "English(US)"

                # Ensure ShortID is exactly the WEM ID
                p_elem.attrib["ShortID"] = str(wem_id)
                
                # Force MediaID
                media_list = find_child_by_local_name(p_elem, "MediaIDList")
                if media_list is None:
                    media_list = ET.Element("MediaIDList")
                    p_elem.append(media_list)
                
                media_list.clear()
                media_list.append(ET.Element("MediaID", {"ID": str(wem_id)}))

        # Reassign unique active source GUID
        new_source_guid = generate_guid()
        src_el = find_element_by_local_name(sound_el, "AudioFileSource")
        if src_el:
            src_el[0].attrib["ID"] = new_source_guid
            
        active_list = find_child_by_local_name(sound_el, "ActiveSourceList")
        if active_list is not None:
            active_src = find_child_by_local_name(active_list, "ActiveSource")
            if active_src is not None:
                active_src.attrib["ID"] = new_source_guid
                active_src.attrib["Platform"] = "Linked"

        # Force Stream StreamingEnabled based on HIRC metadata
        sound_id_str = sound_el.attrib.get("ShortID")
        is_streamed = True
        if sound_id_str:
            try:
                is_streamed = self._is_streamed(int(sound_id_str))
            except ValueError:
                pass
        if wem_id and is_streamed:
            is_streamed = self._is_streamed_by_wem_id(wem_id)
        prop_list = find_child_by_local_name(sound_el, "PropertyList")
        if prop_list is None:
            prop_list = ET.Element("PropertyList")
            sound_el.insert(0, prop_list)
        self._set_xml_property(prop_list, "IsStreamingEnabled", "bool", "True" if is_streamed else "False")

        # Repair OutputBus
        ref_list = find_child_by_local_name(sound_el, "ReferenceList")
        if ref_list is None:
            ref_list = ET.Element("ReferenceList")
            sound_el.append(ref_list)
            
        has_bus = False
        has_conv = False
        for ref in ref_list:
            if ref.attrib.get("Name") == "OutputBus":
                has_bus = True
            elif ref.attrib.get("Name") == "Conversion":
                has_conv = True
                
        if not has_bus:
            ref_list.append(self._create_output_bus_ref())
        if not has_conv:
            ref_list.append(self._create_conversion_ref())

        return sound_el

    def _get_audio_file_source_element(self, wem_id: int, sound_map: Dict[int, ET.Element], project_folder: str) -> ET.Element:
        """Build an AudioFileSource element for use inside a MusicTrack ChildrenList.

        Note: ReferenceList is NOT valid on AudioFileSource inside a MusicTrack.
        The Conversion reference must live on the parent MusicTrack element itself.
        This method only produces Language, AudioFile, and MediaIDList.
        """
        source_guid = generate_guid()
        wem_name = str(wem_id)

        audio_src = ET.Element("AudioFileSource", {
            "Name": wem_name,
            "ID": source_guid,
            "ShortID": str(wem_id),
        })

        lang_elem = ET.Element("Language")
        lang_elem.text = "SFX"
        audio_src.append(lang_elem)

        file_elem = ET.Element("AudioFile")
        file_elem.text = f"{wem_id}.wav"
        audio_src.append(file_elem)

        media_id_list = ET.Element("MediaIDList")
        media_id_list.append(ET.Element("MediaID", {"ID": str(wem_id)}))
        audio_src.append(media_id_list)

        return audio_src



    def _get_music_track_node_base_params(self, obj: HircObjectData, bnk_version: int) -> Dict[str, Any]:
        """Seek and parse NodeBaseParams for interactive music track (type 11)."""
        pr = BinaryReader(obj.raw_payload[4:])
        if pr.remaining() < 5:
            return {}
        try:
            pr.skip(1)  # track flag
            src_count = pr.read_u32()
            pr.skip(src_count * 14)
            playlist_count = pr.read_u32()
            pr.skip(playlist_count * 44)
            if playlist_count > 0 and pr.remaining() >= 4:
                pr.skip(4)
            if pr.remaining() >= 4:
                num_clip = pr.read_u32()
                for _ in range(num_clip):
                    if pr.remaining() < 12:
                        break
                    pr.skip(8)
                    point_count = pr.read_u32()
                    pr.skip(12 * point_count)
            return read_node_base_params(pr, bnk_version)
        except Exception:
            return {}

    def _build_ranseq_playlist_item(self, item: Dict, segment_guid_map: Dict[int, str],
                                      hirc_map: Dict) -> ET.Element:
        """Convert a parsed AkMusicRanSeqPlaylistItem dict into a Wwise MusicPlaylistItem XML element."""
        elem = ET.Element("MusicPlaylistItem", {
            "Name": "",
            "ID": generate_guid(),
            "ShortID": generate_short_id()
        })

        prop_list = ET.Element("PropertyList")
        # PlaylistItemType: 0 = Segment, group items also use 0; folder would be 1
        prop_list.append(ET.Element("Property", {"Name": "PlaylistItemType", "Type": "int16", "Value": "0"}))

        # rs_type: 0=ContinuousSequence, 0xFFFFFFFF (-1)=None (leaf segment)
        rs_type = item.get('rs_type', 0xFFFFFFFF)
        is_leaf = (rs_type == 0xFFFFFFFF or rs_type == 4294967295) and item.get('segment_id', 0) != 0

        # Loop: 0=infinite, 1=play once, -1=use parent
        loop = item.get('loop', 1)
        if loop != 1:
            prop_list.append(ET.Element("Property", {
                "Name": "NumLoops", "Type": "int16", "Value": str(loop)
            }))

        # Random or sequence for group items
        if not is_leaf:
            is_shuffle = item.get('is_shuffle', False)
            prop_list.append(ET.Element("Property", {
                "Name": "RandomOrSequence", "Type": "int16", "Value": "1" if is_shuffle else "0"
            }))
            avoid = item.get('avoid_repeat', 0)
            if avoid > 0:
                prop_list.append(ET.Element("Property", {
                    "Name": "AvoidRepeatCount", "Type": "int16", "Value": str(avoid)
                }))

        # Weight
        weight = item.get('weight', 50000)
        if weight != 50000:
            prop_list.append(ET.Element("Property", {
                "Name": "Weight", "Type": "Real64", "Value": f"{weight / 1000.0:.6g}"
            }))

        elem.append(prop_list)

        # If this item points to a segment, add a SegmentRef
        segment_id = item.get('segment_id', 0)
        if segment_id and segment_id != 0:
            seg_guid = segment_guid_map.get(segment_id)
            if not seg_guid:
                seg_guid = self._container_id_to_guid.get(segment_id)
            if not seg_guid:
                seg_guid = generate_guid()
                self._container_id_to_guid[segment_id] = seg_guid
            
            seg_name = self._object_name_map.get(segment_id, f"MusicSegment_{segment_id}")
            elem.append(ET.Element("SegmentRef", {"Name": seg_name, "ID": seg_guid}))

        # Recurse into children
        children = item.get('children', [])
        if children:
            children_elem = ET.Element("ChildrenList")
            for child in children:
                children_elem.append(self._build_ranseq_playlist_item(child, segment_guid_map, hirc_map))
            elem.append(children_elem)

        return elem

    def _is_streamed_by_wem_id(self, wem_id: int) -> bool:
        if hasattr(self, '_hirc_map'):
            for obj in self._hirc_map.values():
                if obj.type_id == 2 and obj.media_id == wem_id:
                    source_type = None
                    try:
                        if len(obj.raw_payload) >= 9:
                            source_type = obj.raw_payload[8]
                    except Exception:
                        pass
                    if source_type is None:
                        source_type = getattr(obj, 'source_type', 0)
                    return source_type in (1, 2)
        return True

    def _is_streamed(self, sound_id: int) -> bool:
        """Check if a sound/track is streamed/prefetched based on HIRC metadata."""
        if hasattr(self, '_hirc_map') and sound_id in self._hirc_map:
            obj = self._hirc_map[sound_id]
            if obj.type_id == 2:
                source_type = None
                try:
                    if len(obj.raw_payload) >= 9:
                        source_type = obj.raw_payload[8]
                except Exception:
                    pass
                if source_type is None:
                    source_type = getattr(obj, 'source_type', 0)
                return source_type in (1, 2)
            elif obj.type_id == 11:
                try:
                    if len(obj.raw_payload) >= 14:
                        src_count = struct.unpack_from('<I', obj.raw_payload, 5)[0]
                        if src_count > 0:
                            stream_type = obj.raw_payload[13]
                            return stream_type in (1, 2)
                except Exception:
                    pass
        return self._is_streamed_by_wem_id(sound_id)

    def _create_output_bus_ref(self) -> ET.Element:
        ref = ET.Element("Reference", {"Name": "OutputBus"})
        ref.append(ET.Element("ObjectRef", {
            "Name": "Master Audio Bus",
            "ID": self._master_bus_id,
            "WorkUnitID": self._master_bus_wwu_id
        }))
        return ref

    def _create_conversion_ref(self) -> Optional[ET.Element]:
        if not self._conversion_settings_wwu_id:
            return None
        ref = ET.Element("Reference", {"Name": "Conversion"})
        ref.append(ET.Element("ObjectRef", {
            "Name": "Default Conversion Settings",
            "ID": self._conversion_settings_id,
            "WorkUnitID": self._conversion_settings_wwu_id
        }))
        return ref

    def _create_dummy_sound_element(self, parent_name: str, sound_map: Dict[int, ET.Element], project_folder: str) -> ET.Element:
        dummy_sound_guid = generate_guid()
        dummy_sound_id = generate_short_id()
        
        # Find any existing WAV from sound_map to use as a dummy source
        dummy_wem_id = 0
        if sound_map:
            dummy_wem_id = next(iter(sound_map.keys()))
        
        if dummy_wem_id > 0:
            elem = self._create_sound_element(dummy_wem_id, int(dummy_sound_id), dummy_sound_guid, project_folder)
            elem.attrib["Name"] = f"Dummy_Sound_{parent_name}"
            return elem
        
        sound_el = ET.Element("Sound", {
            "Name": f"Dummy_Sound_{parent_name}",
            "ID": dummy_sound_guid,
            "ShortID": dummy_sound_id
        })
        prop_list = ET.Element("PropertyList")
        self._set_xml_property(prop_list, "IsStreamingEnabled", "bool", "True")
        sound_el.append(prop_list)
        ref_list = ET.Element("ReferenceList")
        ref_conv = self._create_conversion_ref()
        if ref_conv is not None:
            ref_list.append(ref_conv)
        ref_list.append(self._create_output_bus_ref())
        sound_el.append(ref_list)
        return sound_el

    def _resolve_child_name_conflicts(self, children_list: ET.Element) -> None:
        """Scan children list of a container and resolve any duplicate Names to prevent Wwise warnings."""
        name_counts = {}
        for child in children_list:
            name = child.attrib.get("Name")
            if name:
                name_counts[name] = name_counts.get(name, 0) + 1
        
        for child in children_list:
            name = child.attrib.get("Name")
            if name and name_counts[name] > 1:
                target_id = child.attrib.get("ShortID")
                if target_id:
                    child.attrib["Name"] = f"{name}_{target_id}"

    def _apply_node_properties(self, elem: ET.Element, p: Dict[str, Any]):
        """Inject parsed HIRC properties into XML PropertyList."""
        prop_list = find_child_by_local_name(elem, "PropertyList")
        if prop_list is None:
            prop_list = ET.Element("PropertyList")
            elem.insert(0, prop_list)

        for prop in p.get('props', []):
            prop_id = prop['id']
            val = prop['value']
            
            # Map float IDs
            name = None
            if prop_id == 0x06:
                name = "Volume" if elem.tag == "Sound" else "MakeUpGain"
            elif prop_id == 0x07:
                name = "Priority"
            elif prop_id == 0x02:
                name = "Pitch"
            elif prop_id == 0x04:
                name = "LFEUsage"

            if name:
                self._set_xml_property(prop_list, name, "Real64", f"{val:.17g}")

        # Advanced Settings mapping
        adv_bits = p.get('adv_bits', 0)
        max_reached = 1 if (adv_bits & 0x01) != 0 else 0
        over_limit = 1 if (adv_bits & 0x04) != 0 else 0
        use_virtual = (adv_bits & 0x02) != 0

        self._set_xml_property(prop_list, "MaxReachedBehavior", "int16", str(max_reached))
        self._set_xml_property(prop_list, "OverLimitBehavior", "int16", str(over_limit))
        self._set_xml_property(prop_list, "UseVirtualBehavior", "bool", "True" if use_virtual else "False")
        self._set_xml_property(prop_list, "VirtualVoiceQueueBehavior", "int16", str(p.get('virtual_queue_behavior', 0)))
        self._set_xml_property(prop_list, "BelowThresholdBehavior", "int16", str(p.get('below_threshold_behavior', 0)))

        max_inst = p.get('max_num_instance', 0)
        if max_inst > 0:
            self._set_xml_property(prop_list, "MaxInstances", "int16", str(max_inst))
            
        if (adv_bits & 0x08) != 0:
            self._set_xml_property(prop_list, "IgnoreParentMaxInstances", "bool", "True")

    def _set_xml_property(self, prop_list: ET.Element, name: str, ptype: str, value: str):
        """Add or update an XML Property child."""
        found = False
        for prop in prop_list:
            if prop.attrib.get("Name") == name:
                prop.attrib["Value"] = value
                found = True
                break
        if not found:
            prop_list.append(ET.Element("Property", {
                "Name": name,
                "Type": ptype,
                "Value": value
            }))

    def _build_events_hierarchy(self,
                                 objects: List[HircObjectData],
                                 hash_lookup: HashLookup,
                                 hirc_map: Dict[int, HircObjectData]) -> List[ET.Element]:
        """Generate high-fidelity Event nodes mapping Play actions to structured containers."""
        events = []
        for obj in objects:
            if obj.type_id != 4:  # Event
                continue
            
            event_name = hash_lookup.resolve(obj.object_id)
            if not event_name:
                event_name = f"Event_{obj.object_id}"

            event_guid = generate_guid()
            actions_list = ET.Element("ChildrenList")

            # Parse event action IDs
            pr = BinaryReader(obj.raw_payload[4:])
            if pr.remaining() < 1:
                continue
            action_count = pr.read_u8()
            for _ in range(action_count):
                if pr.remaining() < 4:
                    break
                action_id = pr.read_u32()
                action_obj = hirc_map.get(action_id)
                if not action_obj:
                    continue
                
                # Read action target
                apr = BinaryReader(action_obj.raw_payload[4:])
                if apr.remaining() < 6:
                    continue
                action_type = apr.read_u16()
                target_id = apr.read_u32()
                
                # Check Play fade curve if available
                fade_curve = 4
                if action_type == 0x0403:  # Play
                    apr.read_u8()  # idExt_4
                    # Bypass float AkPropBundle
                    c_props = apr.read_u8()
                    apr.skip(c_props)
                    apr.skip(c_props * 4)
                    # Bypass int AkPropBundle
                    c_props_int = apr.read_u8()
                    apr.skip(c_props_int)
                    apr.skip(c_props_int * 4)
                    if apr.remaining() >= 1:
                        fade_curve = apr.read_u8()

                target_obj = hirc_map.get(target_id)
                target_type = target_obj.type_id if target_obj else 0
                target_name = self._object_name_map.get(target_id, f"Target_{target_id}")

                container_guid = self._container_id_to_guid.get(target_id)
                if container_guid:
                    action_el = self._create_play_action(target_name, container_guid, action_id, fade_curve, target_type)
                    actions_list.append(action_el)

            if len(actions_list) > 0:
                event_el = ET.Element("Event", {
                    "Name": event_name,
                    "ID": event_guid,
                    "ShortID": str(obj.object_id)
                })
                event_el.append(actions_list)
                events.append(event_el)

        return events

    def _create_play_action(self, target_name: str, target_guid: str, action_id: int, fade_curve: int, target_type: int) -> ET.Element:
        action_guid = generate_guid()
        
        prop_list = ET.Element("PropertyList")
        self._set_xml_property(prop_list, "ActionType", "int16", "1") # 1 = Play
        if fade_curve != 4:
            self._set_xml_property(prop_list, "FadeCurve", "int16", str(fade_curve & 0x07))

        if target_type in (10, 11, 12, 13) and self._interactive_music_wwu_id:
            work_unit_id = self._interactive_music_wwu_id
        else:
            work_unit_id = self._actor_mixer_wwu_id

        ref_list = ET.Element("ReferenceList")
        ref = ET.Element("Reference", {"Name": "Target"})
        ref.append(ET.Element("ObjectRef", {
            "Name": target_name,
            "ID": target_guid,
            "WorkUnitID": work_unit_id
        }))
        ref_list.append(ref)

        elem = ET.Element("Action", {
            "Name": "",
            "ID": action_guid,
            "ShortID": str(action_id)
        })
        elem.append(prop_list)
        elem.append(ref_list)
        return elem

    def _modify_events_work_unit(self, doc: ET.ElementTree, events: List[ET.Element]):
        """Clear and rewrite the Events Default Work Unit."""
        work_unit = None
        for wu in find_element_by_local_name(doc.getroot(), "WorkUnit"):
            if wu.attrib.get("Name") == "Default Work Unit":
                work_unit = wu
                break
                
        if work_unit is None:
            return
            
        children_list = find_child_by_local_name(work_unit, "ChildrenList")
        if children_list is None:
            children_list = ET.Element("ChildrenList")
            work_unit.append(children_list)

        children_list.clear()
        for ev in events:
            children_list.append(ev)

    def _create_default_soundbanks_wwu(self) -> ET.ElementTree:
        guid = generate_guid()
        root = ET.Element("WwiseDocument", {
            "Type": "WorkUnit",
            "ID": guid,
            "SchemaVersion": "110"
        })
        sb = ET.SubElement(root, "SoundBanks")
        wu = ET.SubElement(sb, "WorkUnit", {
            "Name": "Default Work Unit",
            "ID": guid,
            "PersistMode": "Standalone"
        })
        ET.SubElement(wu, "ChildrenList")
        return ET.ElementTree(root)

    def _modify_soundbank_inclusions(self,
                                     doc: ET.ElementTree,
                                     events_wu_id: str,
                                     events: List[ET.Element],
                                     bank_name: str,
                                     actor_mixer_elems: List[ET.Element] = None,
                                     actor_mixer_wwu_id: str = "",
                                     interactive_music_elems: List[ET.Element] = None,
                                     interactive_music_wwu_id: str = "",
                                     filter_value: str = "7"):
        """Update or insert a SoundBank node including references to specified Event elements."""
        work_unit = None
        for wu in find_element_by_local_name(doc.getroot(), "WorkUnit"):
            if wu.attrib.get("Name") == "Default Work Unit":
                work_unit = wu
                break
                
        if work_unit is None:
            return

        children_list = find_child_by_local_name(work_unit, "ChildrenList")
        if children_list is None:
            children_list = ET.Element("ChildrenList")
            work_unit.append(children_list)

        # Remove existing SoundBank with matching name
        for sb in list(children_list):
            if sb.attrib.get("Name") == bank_name:
                children_list.remove(sb)

        bank_guid = generate_guid()
        bank_short_id = str(compute_fnv32_hash(bank_name))

        sb_el = ET.Element("SoundBank", {
            "Name": bank_name,
            "ID": bank_guid,
            "ShortID": bank_short_id
        })
        
        prop_list = ET.Element("PropertyList")
        self._set_xml_property(prop_list, "UseStreamingManager", "bool", "True")
        sb_el.append(prop_list)

        inclusion_list = ET.Element("ObjectInclusionList")
        for ev in events:
            inclusion_list.append(ET.Element("ObjectRef", {
                "Name": ev.attrib.get("Name", "UnknownEvent"),
                "ID": ev.attrib.get("ID", ""),
                "WorkUnitID": events_wu_id,
                "Origin": "Manual",
                "Filter": filter_value
            }))
            
        if actor_mixer_elems:
            for elem in actor_mixer_elems:
                inclusion_list.append(ET.Element("ObjectRef", {
                    "Name": elem.attrib.get("Name", "UnknownObject"),
                    "ID": elem.attrib.get("ID", ""),
                    "WorkUnitID": actor_mixer_wwu_id,
                    "Origin": "Manual",
                    "Filter": filter_value
                }))
                
        if interactive_music_elems:
            for elem in interactive_music_elems:
                inclusion_list.append(ET.Element("ObjectRef", {
                    "Name": elem.attrib.get("Name", "UnknownObject"),
                    "ID": elem.attrib.get("ID", ""),
                    "WorkUnitID": interactive_music_wwu_id,
                    "Origin": "Manual",
                    "Filter": filter_value
                }))

        sb_el.append(inclusion_list)
        sb_el.append(ET.Element("ObjectExclusionList"))
        sb_el.append(ET.Element("GameSyncExclusionList"))

        children_list.append(sb_el)

    def _patch_conversion_settings(self, project_folder: str):
        """Set conversion streams to Vorbis instead of massive uncompressed PCM."""
        conv_dir = os.path.join(project_folder, "Conversion Settings")
        if not os.path.exists(conv_dir):
            return

        for file in os.listdir(conv_dir):
            if file.endswith(".wwu"):
                path = os.path.join(conv_dir, file)
                try:
                    doc = ET.parse(path)
                    modified = False
                    
                    for info in find_element_by_local_name(doc.getroot(), "ConversionPluginInfo"):
                        if info.attrib.get("Platform") == "Windows":
                            plugin = find_child_by_local_name(info, "ConversionPlugin")
                            if plugin is not None and plugin.attrib.get("PluginName") == "PCM":
                                plugin.attrib["PluginName"] = "Vorbis"
                                plugin.attrib["PluginID"] = "4"
                                modified = True
                                
                    if modified:
                        doc.write(path, encoding="utf-8", xml_declaration=True)
                        ET.indent(doc, space="  ")
                        doc.write(path, encoding="utf-8", xml_declaration=True)
                except Exception:
                    pass

    def _get_wem_duration_ms(self, wem_id: int, project_folder: str) -> int:
        """Read duration in milliseconds of the corresponding WAV file.

        Searches both Originals/SFX and Originals/Music so that music WAVs are
        found even in projects whose originals live under the Music sub-folder.
        Returns 0 when the file cannot be found so that Wwise auto-detects the
        clip length instead of locking the voice slot for an absurdly long time.
        """
        import wave
        candidate_paths = [
            os.path.join(project_folder, "Originals", "SFX",   f"{wem_id}.wav"),
            os.path.join(project_folder, "Originals", "Music", f"{wem_id}.wav"),
        ]
        for wav_path in candidate_paths:
            if os.path.exists(wav_path):
                try:
                    with wave.open(wav_path, "rb") as w:
                        frames = w.getnframes()
                        rate = w.getframerate()
                        if rate > 0:
                            return int((frames / float(rate)) * 1000)
                except Exception:
                    pass
        return 0  # 0 → Wwise auto-detects clip length from the encoded audio

    def _get_music_segment_duration_ms(self, obj: HircObjectData, hirc_map: Dict[int, HircObjectData], project_folder: str) -> int:
        """Find the maximum duration among all child music tracks of a music segment."""
        max_duration = 0
        found = False
        for child_id in obj.child_ids:
            child = hirc_map.get(child_id)
            if child and child.type_id == 11:
                seen = set()
                for source_id, _ in child.music_track_sources:
                    if source_id > 0 and source_id not in seen:
                        seen.add(source_id)
                        max_duration = max(max_duration, self._get_wem_duration_ms(source_id, project_folder))
                        found = True
                for file_id in child.playlist_wems:
                    if file_id > 0 and file_id not in seen:
                        seen.add(file_id)
                        max_duration = max(max_duration, self._get_wem_duration_ms(file_id, project_folder))
                        found = True
        return max_duration if found else 0
