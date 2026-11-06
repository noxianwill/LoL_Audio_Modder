"""
Modification engine: replace, add, remove audio operations.
All operations update the in-memory DOM and mark state as dirty.
"""
import struct
import os
from typing import Optional, List, Tuple
from lam.model.project import Project
from lam.model.audio_entry import AudioEntry
from lam.parsers.hirc_parser import HircObjectData
from lam.engine.audio_converter import AudioConverter
from lam.core.hashing import fnv1_hash_32
from lam.core.logging import get_logger


def detect_wem_codec(data: bytes) -> Optional[int]:
    """Detect Wwise plugin ID from WEM file data."""
    if len(data) < 12 or data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        return None
    fmt_pos = data.find(b'fmt ')
    if fmt_pos == -1 or fmt_pos + 10 > len(data):
        return None
    audio_fmt = struct.unpack_from('<H', data, fmt_pos + 8)[0]
    
    if audio_fmt == 0xffff:    # Wwise Vorbis
        return 0x00040001
    elif audio_fmt == 0x302:   # Wwise Opus
        return 0x00060001
    elif audio_fmt == 0x0002:  # Wwise ADPCM
        return 0x00020002
    elif audio_fmt in (0x0001, 0xfffe):  # PCM (standard or extensible)
        return 0x00010001
    return None


class Modifier:
    """Performs non-destructive audio modification operations."""

    def __init__(self, project: Project):
        self.project = project
        self._log = get_logger()
        self.deleted_sound_templates = {}

    def replace_audio(self, target_wem_id: int, new_audio_path: str) -> bool:
        """Replace a WEM's audio payload with a new file."""
        self._log.info("Modifier", f"Replacing WEM {target_wem_id} with {new_audio_path}",
                       operation="replace", wem_id=target_wem_id, file_path=new_audio_path)

        new_data = AudioConverter.load_audio_file(new_audio_path)
        if new_data is None:
            self._log.error("Modifier", "Failed to load replacement audio",
                           operation="replace", file_path=new_audio_path)
            return False

        # Update audio entry
        ae = self.project.audio_entries.get(target_wem_id)
        if ae is None:
            self._log.error("Modifier", f"WEM {target_wem_id} not found in project",
                           operation="replace", wem_id=target_wem_id)
            return False

        ae.payload = new_data
        ae.size = len(new_data)
        ae.is_modified = True

        # Update WPK entries
        for wpk in self.project.wpk_files.values():
            wpk.replace_payload(target_wem_id, new_data)

        # Update audio BNK entries
        for lb in self.project.audio_banks.values():
            for didx_entry in lb.bnk.didx_entries:
                if didx_entry.wem_id == target_wem_id:
                    didx_entry.size = len(new_data)

        # Auto-detect codec from the new WEM file
        detected_plugin = detect_wem_codec(new_data)

        # Update HIRC Sound and MusicTrack objects referencing this media
        for obj in self.project.all_hirc_objects:
            if obj.type_id == 2 and obj.media_id == target_wem_id:
                # source_type: 0=embedded, 1=streamed, 2=prefetched
                if obj.source_type == 0:
                    # Embedded: media_size IS the full file size
                    self._patch_sound_media_size(obj, len(new_data))
                elif obj.source_type == 2:
                    # Prefetched: media_size is the prefetch buffer size, not the
                    # full file size. Only shrink it if the new file is smaller.
                    if len(new_data) < obj.media_size:
                        self._patch_sound_media_size(obj, len(new_data))
                    # else: keep original prefetch buffer size unchanged
                # source_type 1 (streamed): media_size is typically 0, leave unchanged

                # Update plugin ID to match the new codec if detected
                if detected_plugin is not None:
                    self._patch_sound_plugin_id(obj, detected_plugin)

            elif obj.type_id == 11 and (obj.media_id == target_wem_id or target_wem_id in obj.playlist_wems):
                # Update MusicTrack size and plugin ID
                self._patch_music_track_media(obj, target_wem_id, len(new_data), detected_plugin)

        self.project.mark_dirty()
        self._log.info("Modifier",
                       f"Replaced WEM {target_wem_id}: {ae.original_size} -> {len(new_data)} bytes",
                       operation="replace_complete", wem_id=target_wem_id,
                       size=len(new_data))
        return True

    def add_audio(self, parent_container_id: int, new_audio_path: str,
                  template_sound_id: Optional[int] = None) -> Optional[int]:
        """Add a new audio entry as a variation under a container."""
        self._log.info("Modifier",
                       f"Adding audio to container {parent_container_id}",
                       operation="add", object_id=parent_container_id,
                       file_path=new_audio_path)

        new_data = AudioConverter.load_audio_file(new_audio_path)
        if new_data is None:
            self._log.error("Modifier", "Failed to load audio file",
                            operation="add", file_path=new_audio_path)
            return None

        # Generate WEM ID from filename
        basename = os.path.splitext(os.path.basename(new_audio_path))[0]
        try:
            new_wem_id = int(basename)
        except ValueError:
            new_wem_id = fnv1_hash_32(basename)

        # Block only if the target container already has a live child Sound/MusicTrack
        # that references this exact WEM ID — prevents adding a duplicate variation
        # to the same container. A WEM referenced by a *different* container is fine
        # to reuse here (Wwise allows the same media in multiple Sound objects).
        # The old audio_entries check was wrong: WEMs already in the audio BNK pool
        # (e.g. 73 embedded WEMs in darius_base_sfx_audio.bnk) are not duplicates
        # if their Sound object has already been removed from the HIRC.
        parent_for_dup = None
        for obj in self.project.all_hirc_objects:
            if obj.object_id == parent_container_id:
                parent_for_dup = obj
                break
        if parent_for_dup is not None:
            for cid in parent_for_dup.child_ids:
                for obj in self.project.all_hirc_objects:
                    if obj.object_id == cid:
                        if (obj.type_id == 2 and obj.media_id == new_wem_id) or \
                           (obj.type_id == 11 and new_wem_id in obj.playlist_wems):
                            self._log.warn("Modifier",
                                           f"WEM ID {new_wem_id} is already a child of container {parent_container_id}",
                                           operation="add", wem_id=new_wem_id)
                            return None

        # Find the parent container object
        parent = None
        for obj in self.project.all_hirc_objects:
            if obj.object_id == parent_container_id:
                parent = obj
                break

        if parent is None:
            self._log.error("Modifier", f"Parent container {parent_container_id} not found",
                           operation="add")
            return None

        is_music = (parent.type_id == 10)

        # Find template sound/track to clone
        template = None
        if is_music:
            # Look for a template MusicTrack (type 11)
            if template_sound_id:
                for obj in self.project.all_hirc_objects:
                    if obj.object_id == template_sound_id and obj.type_id == 11:
                        template = obj
                        break
            
            if template is None:
                # Find first MusicTrack child of the container
                if parent.child_ids:
                    for cid in parent.child_ids:
                        for obj in self.project.all_hirc_objects:
                            if obj.object_id == cid and obj.type_id == 11:
                                template = obj
                                break
                        if template:
                            break
            
            if template is None:
                # Fallback: Find ANY MusicTrack in the entire bank to use as a template
                for obj in self.project.all_hirc_objects:
                    if obj.type_id == 11:
                        template = obj
                        self._log.info("Modifier", f"Using fallback MusicTrack template {obj.object_id} from parent {obj.parent_id}",
                                       operation="add_music_fallback")
                        break
            
            if template is None:
                self._log.error("Modifier", "No template MusicTrack object found to clone (even with fallback)",
                               operation="add", object_id=parent_container_id)
                return None
        else:
            # Look for standard Sound (type 2) template
            if template_sound_id:
                for obj in self.project.all_hirc_objects:
                    if obj.object_id == template_sound_id and obj.type_id == 2:
                        template = obj
                        break

            if template is None:
                # Find first Sound child of the container
                if parent.child_ids:
                    for cid in parent.child_ids:
                        for obj in self.project.all_hirc_objects:
                            if obj.object_id == cid and obj.type_id == 2:
                                template = obj
                                break
                        if template:
                            break

            if template is None:
                # Try to use a recently deleted sound template from this same container
                template = self.deleted_sound_templates.get(parent_container_id)
                if template:
                    self._log.info("Modifier", f"Using saved deleted Sound template {template.object_id} for container {parent_container_id}",
                                   operation="add_deleted_template")

            if template is None:
                # Fallback: Find ANY Sound object in the entire bank to use as a template
                for obj in self.project.all_hirc_objects:
                    if obj.type_id == 2:
                        template = obj
                        self._log.info("Modifier", f"Using fallback Sound template {obj.object_id} from parent {obj.parent_id}",
                                       operation="add_fallback")
                        break

            if template is None:
                self._log.error("Modifier", "No template Sound object found to clone (even with fallback)",
                               operation="add", object_id=parent_container_id)
                return None

        # Clone the Sound or MusicTrack object
        if is_music:
            new_sound = self._clone_music_track(template, new_wem_id, new_data, parent_container_id)
        else:
            new_sound = self._clone_sound(template, new_wem_id, new_data, parent_container_id)

        # Insert the cloned object immediately BEFORE its parent container to preserve Wwise HIRC ordering
        parent_idx = -1
        for idx, obj in enumerate(self.project.all_hirc_objects):
            if obj.object_id == parent_container_id:
                parent_idx = idx
                break
        if parent_idx != -1:
            self.project.all_hirc_objects.insert(parent_idx, new_sound)
        else:
            self.project.all_hirc_objects.append(new_sound)

        # Add to parent container's child list
        if new_wem_id not in parent.child_ids:
            parent.child_ids.append(new_wem_id)
        # Patch the raw payload to include new child
        self._patch_container_children(parent, new_wem_id)

        # Add WEM to audio entries
        ae = AudioEntry(
            wem_id=new_wem_id,
            name=f"{new_wem_id}.wem",
            size=len(new_data),
            payload=new_data,
            source_type="added",
            is_modified=True,
        )
        self.project.audio_entries[new_wem_id] = ae

        # Add to WPK files
        for wpk in self.project.wpk_files.values():
            wpk.add_entry(new_wem_id, f"{new_wem_id}.wem", new_data)

        # Add to audio BNK files
        from lam.parsers.bnk_parser import DidxEntry
        for lb in self.project.audio_banks.values():
            existing = None
            for e in lb.bnk.didx_entries:
                if e.wem_id == new_wem_id:
                    existing = e
                    break
            if existing is not None:
                existing.size = len(new_data)
            else:
                lb.bnk.didx_entries.append(DidxEntry(wem_id=new_wem_id, offset=0, size=len(new_data)))

        # Rebuild HIRC data for all events banks
        self._rebuild_all_hirc()

        self.project.mark_dirty()
        self._log.info("Modifier",
                       f"Added WEM {new_wem_id} under container {parent_container_id}",
                       operation="add_complete", wem_id=new_wem_id,
                       object_id=parent_container_id, size=len(new_data))
        return new_wem_id

    def remove_audio(self, target_wem_id: int) -> bool:
        """Remove a WEM and its associated Sound or MusicTrack object."""
        self._log.info("Modifier", f"Removing WEM {target_wem_id}",
                       operation="remove", wem_id=target_wem_id)

        # Find the Sound or MusicTrack object
        sound_obj = None
        for obj in self.project.all_hirc_objects:
            if obj.type_id == 2 and obj.media_id == target_wem_id:
                sound_obj = obj
                break
            elif obj.type_id == 11 and (obj.media_id == target_wem_id or target_wem_id in obj.playlist_wems):
                sound_obj = obj
                break

        if sound_obj is None:
            self._log.error("Modifier", f"No Sound or MusicTrack object found for WEM {target_wem_id}",
                           operation="remove", wem_id=target_wem_id)
            return False

        # Check dependencies: is this sound referenced by actions?
        referencing_actions = [
            o for o in self.project.all_hirc_objects
            if o.type_id == 3 and o.target_id == sound_obj.object_id
        ]
        if referencing_actions:
            # Check if actions are only targeting this sound
            for act in referencing_actions:
                events = [
                    e for e in self.project.all_hirc_objects
                    if e.type_id == 4 and act.object_id in e.event_action_ids
                ]
                for evt in events:
                    if len(evt.event_action_ids) <= 1:
                        self._log.error("Modifier",
                            f"Cannot remove: Event {evt.object_id} would have no actions",
                            operation="remove", wem_id=target_wem_id,
                            object_id=evt.object_id)
                        return False

        # Remove from parent containers
        for obj in self.project.all_hirc_objects:
            if sound_obj.object_id in obj.child_ids:
                obj.child_ids.remove(sound_obj.object_id)
                self._patch_container_remove_child(obj, sound_obj.object_id)
                # Keep a backup of this deleted Sound object as a template for this specific container
                if sound_obj.type_id == 2:
                    self.deleted_sound_templates[obj.object_id] = sound_obj

        # Remove the Sound or MusicTrack object
        self.project.all_hirc_objects = [
            o for o in self.project.all_hirc_objects
            if o.object_id != sound_obj.object_id
        ]

        # Remove WEM entry
        if target_wem_id in self.project.audio_entries:
            del self.project.audio_entries[target_wem_id]

        # Remove from WPK
        for wpk in self.project.wpk_files.values():
            wpk.remove_entry(target_wem_id)

        # Remove from audio BNK files
        for lb in self.project.audio_banks.values():
            lb.bnk.didx_entries = [e for e in lb.bnk.didx_entries if e.wem_id != target_wem_id]

        # Rebuild HIRC
        self._rebuild_all_hirc()

        self.project.mark_dirty()
        self._log.info("Modifier", f"Removed WEM {target_wem_id}",
                       operation="remove_complete", wem_id=target_wem_id)
        return True

    def _find_parent_id_offset(self, payload: bytes) -> Optional[int]:
        """Parse raw Sound payload to find the exact byte offset of the parent_id field."""
        if len(payload) < 26:
            return None
        
        pos = 18
        if pos >= len(payload):
            return None
        # override_flags
        pos += 1
        
        if pos >= len(payload):
            return None
        fx_count = payload[pos]
        pos += 1
        
        if fx_count > 0:
            bitmask_size = (fx_count + 7) // 8
            pos += bitmask_size
            pos += fx_count * 7
            
        # v145+ fields (Metadata override + entries)
        if pos + 2 > len(payload):
            return None
        override_metadata = payload[pos]
        pos += 1
        metadata_count = payload[pos]
        pos += 1
        
        pos += metadata_count * 5
        
        # Override attachment params
        pos += 1
        
        # bus_id (4 bytes)
        # parent_id (4 bytes)
        parent_id_pos = pos + 4
        if parent_id_pos + 4 <= len(payload):
            return parent_id_pos
        return None

    def _clone_sound(self, template: HircObjectData, new_id: int,
                     new_data: bytes, parent_container_id: Optional[int] = None) -> HircObjectData:
        """Clone a Sound object with new source ID and media size."""
        payload = bytearray(template.raw_payload)
        new_size = len(new_data)
        # Patch object_id (first 4 bytes)
        struct.pack_into('<I', payload, 0, new_id)
        # Patch source_id at offset 9 (after OID(4) + pluginID(4) + streamType(1))
        if len(payload) >= 13:
            struct.pack_into('<I', payload, 9, new_id)
            
        # Detect and patch plugin ID at offset 4
        detected_plugin = detect_wem_codec(new_data)
        if detected_plugin is not None:
            if len(payload) >= 8:
                struct.pack_into('<I', payload, 4, detected_plugin)
                
        # Patch media size at offset 13 — only for embedded sources.
        # For prefetched (source_type=2), media_size is the prefetch buffer,
        # not the full file size. Keep the template's prefetch size.
        effective_media_size = template.media_size  # default: keep template size
        if template.source_type == 0:
            # Embedded: media_size IS the full file size
            effective_media_size = new_size
            if len(payload) >= 17:
                struct.pack_into('<I', payload, 13, new_size)
        elif template.source_type == 2:
            # Prefetched: the in-memory (prefetch) buffer size must never exceed
            # the actual file size — cap it so the game doesn't try to prefetch
            # more bytes than the WEM contains.
            effective_media_size = min(new_size, template.media_size)
            if len(payload) >= 17:
                struct.pack_into('<I', payload, 13, effective_media_size)
        # source_type 1 (streamed): media_size is typically 0, leave unchanged

        # Patch parent_id if provided (useful for fallbacks where template is from another container)
        final_parent_id = template.parent_id
        if parent_container_id is not None:
            final_parent_id = parent_container_id
            parent_id_off = self._find_parent_id_offset(payload)
            if parent_id_off is not None:
                struct.pack_into('<I', payload, parent_id_off, parent_container_id)
            else:
                self._log.warn("Modifier", f"Could not find parent_id offset to patch in cloned Sound object",
                               operation="clone_sound")

        clone = HircObjectData(
            type_id=2, object_id=new_id, raw_payload=bytes(payload),
            file_offset=0, type_name="Sound",
            parent_id=final_parent_id, bus_id=template.bus_id,
            media_id=new_id, media_size=effective_media_size,
            source_type=template.source_type, modified=True,
        )
        return clone

    def _clone_music_track(self, template: HircObjectData, new_id: int,
                           new_data: bytes, parent_container_id: int) -> HircObjectData:
        """Clone a MusicTrack object with new source ID, media size, and parent Segment."""
        payload = bytearray(template.raw_payload)
        new_size = len(new_data)
        
        # 1. Patch object_id (first 4 bytes)
        struct.pack_into('<I', payload, 0, new_id)
        
        # 2. Patch sources list (first source's source_id and in_mem_size)
        if len(payload) >= 22:
            src_count = struct.unpack_from('<I', payload, 5)[0]
            if src_count > 0:
                # First source source_id is at offset 14, in_mem_size is at offset 18
                struct.pack_into('<I', payload, 14, new_id)
                struct.pack_into('<I', payload, 18, new_size)
                
                # Detect and patch plugin ID at offset 9
                detected_plugin = detect_wem_codec(new_data)
                if detected_plugin is not None:
                    struct.pack_into('<I', payload, 9, detected_plugin)
                
        # 3. Patch playlist (first playlist record's file_id)
        if len(payload) >= 22:
            src_count = struct.unpack_from('<I', payload, 5)[0]
            playlist_count_offset = 9 + src_count * 14
            if playlist_count_offset + 4 <= len(payload):
                playlist_count = struct.unpack_from('<I', payload, playlist_count_offset)[0]
                if playlist_count > 0:
                    # First playlist file_id is at offset playlist_count_offset + 8
                    first_file_id_offset = playlist_count_offset + 8
                    if first_file_id_offset + 4 <= len(payload):
                        struct.pack_into('<I', payload, first_file_id_offset, new_id)
                        
        # 4. Patch parent_id by searching for the original template parent_id
        if template.parent_id:
            target_parent = struct.pack('<I', template.parent_id)
            idx = payload.find(target_parent, 4)
            if idx != -1:
                struct.pack_into('<I', payload, idx, parent_container_id)

        clone = HircObjectData(
            type_id=11, object_id=new_id, raw_payload=bytes(payload),
            file_offset=0, type_name="MusicTrack",
            parent_id=parent_container_id, bus_id=template.bus_id,
            media_id=new_id, media_size=new_size,
            modified=True,
        )
        clone.playlist_wems = [new_id]
        clone.music_track_sources = [(new_id, new_size)]
        return clone

    def _patch_sound_media_size(self, obj: HircObjectData, new_size: int):
        """Patch the media size field in a Sound object's raw payload."""
        if len(obj.raw_payload) >= 17:
            payload = bytearray(obj.raw_payload)
            struct.pack_into('<I', payload, 13, new_size)
            obj.raw_payload = bytes(payload)
            obj.media_size = new_size
            obj.modified = True

    def _patch_sound_plugin_id(self, obj: HircObjectData, plugin_id: int):
        """Patch the plugin ID field in a Sound object's raw payload."""
        if len(obj.raw_payload) >= 8:
            payload = bytearray(obj.raw_payload)
            struct.pack_into('<I', payload, 4, plugin_id)
            obj.raw_payload = bytes(payload)
            obj.modified = True

    def _patch_music_track_media(self, obj: HircObjectData, target_wem_id: int, new_size: int, plugin_id: Optional[int] = None):
        """Patch size and plugin ID for a specific source in a MusicTrack object's raw payload."""
        payload = bytearray(obj.raw_payload)
        if len(payload) >= 9:
            src_count = struct.unpack_from('<I', payload, 5)[0]
            for i in range(src_count):
                off = 9 + i * 14
                if off + 14 <= len(payload):
                    source_id = struct.unpack_from('<I', payload, off + 5)[0]
                    if source_id == target_wem_id:
                        struct.pack_into('<I', payload, off + 9, new_size)
                        if plugin_id is not None:
                            struct.pack_into('<I', payload, off, plugin_id)
            obj.raw_payload = bytes(payload)
            # Update internal model references
            obj.media_size = new_size
            new_sources = []
            for sid, sz in obj.music_track_sources:
                if sid == target_wem_id:
                    new_sources.append((sid, new_size))
                else:
                    new_sources.append((sid, sz))
            obj.music_track_sources = new_sources
            obj.modified = True

    def _patch_container_children(self, obj: HircObjectData, new_child_id: int):
        """Patch container payload to add a new child ID and playlist entry.

        RandomSequenceContainers require child_count == playlist_count.
        We must patch BOTH the child list and the playlist, or the game
        rejects the entire BNK.
        """
        payload = bytearray(obj.raw_payload)
        
        # Check if we have child_count_offset stored
        count_off = getattr(obj, 'child_count_offset', 0)
        
        if count_off > 0 and count_off < len(payload):
            current_count = struct.unpack_from('<I', payload, count_off)[0]
            # --- Step 1: Patch child list ---
            struct.pack_into('<I', payload, count_off, current_count + 1)
            child_insert_pos = count_off + 4 + current_count * 4
            new_id_bytes = struct.pack('<I', new_child_id)
            payload[child_insert_pos:child_insert_pos] = new_id_bytes

            # --- Step 2: Patch playlist ---
            # RandomSequenceContainer (5), SwitchContainer (6), ActorMixer (7) store a
            # u16 playlist count immediately after their child IDs.  BlendContainer (9)
            # does NOT — it stores BlendTrackList binary data instead.  Touching those
            # bytes as if they were a playlist count corrupts the HIRC payload.
            if obj.type_id in (5, 6, 7):
                playlist_off = child_insert_pos + 4
                if playlist_off + 2 <= len(payload):
                    playlist_len = struct.unpack_from('<H', payload, playlist_off)[0]
                    
                    # Read weight from first existing playlist entry if available
                    first_entry_off = playlist_off + 2
                    if playlist_len > 0 and first_entry_off + 8 <= len(payload):
                        existing_weight = struct.unpack_from('<i', payload, first_entry_off + 4)[0]
                    else:
                        existing_weight = 50000  # default Wwise weight

                    # Increment playlist count
                    struct.pack_into('<H', payload, playlist_off, playlist_len + 1)
                    # Insert new entry after last existing entry
                    playlist_insert_pos = playlist_off + 2 + playlist_len * 8
                    new_entry = struct.pack('<Ii', new_child_id, existing_weight)
                    payload[playlist_insert_pos:playlist_insert_pos] = new_entry

            obj.raw_payload = bytes(payload)
            return

        # Fallback: Find the child count in the payload by scanning for known child IDs
        data = bytes(payload)
        for existing_id in obj.child_ids:
            if existing_id == new_child_id:
                continue
            target = struct.pack('<I', existing_id)
            idx = data.find(target, 4)
            if idx != -1 and idx >= 8:
                count_off = idx - 4
                current_count = struct.unpack_from('<I', data, count_off)[0]
                if 0 < current_count < 1000:
                    # --- Step 1: Patch child list ---
                    struct.pack_into('<I', payload, count_off, current_count + 1)
                    child_insert_pos = idx + current_count * 4
                    new_id_bytes = struct.pack('<I', new_child_id)
                    payload[child_insert_pos:child_insert_pos] = new_id_bytes

                    # --- Step 2: Patch playlist ---
                    # BlendContainer (9) has no playlist — skip it to avoid corruption.
                    if obj.type_id in (5, 6, 7):
                        playlist_off = child_insert_pos + 4
                        if playlist_off + 2 <= len(payload):
                            playlist_len = struct.unpack_from('<H', payload, playlist_off)[0]
                            if 0 < playlist_len < 1000:
                                first_entry_off = playlist_off + 2
                                if first_entry_off + 8 <= len(payload):
                                    existing_weight = struct.unpack_from('<i', payload, first_entry_off + 4)[0]
                                else:
                                    existing_weight = 50000  # default Wwise weight

                                # Increment playlist count
                                struct.pack_into('<H', payload, playlist_off, playlist_len + 1)
                                # Insert new entry after last existing entry
                                playlist_insert_pos = playlist_off + 2 + playlist_len * 8
                                new_entry = struct.pack('<Ii', new_child_id, existing_weight)
                                payload[playlist_insert_pos:playlist_insert_pos] = new_entry

                    obj.raw_payload = bytes(payload)
                    return
        obj.raw_payload = bytes(payload)

    def _patch_container_remove_child(self, obj: HircObjectData, child_id: int):
        """Patch container payload to remove a child ID and its playlist entry."""
        payload = bytearray(obj.raw_payload)
        
        # Check if we have child_count_offset stored
        count_off = getattr(obj, 'child_count_offset', 0)
        
        if count_off > 0 and count_off < len(payload):
            current_count = struct.unpack_from('<I', payload, count_off)[0]
            if 0 < current_count <= 1000:
                child_idx = -1
                for j in range(current_count):
                    cid_off = count_off + 4 + j * 4
                    if cid_off + 4 <= len(payload):
                        cid = struct.unpack_from('<I', payload, cid_off)[0]
                        if cid == child_id:
                            child_idx = j
                            break
                            
                if child_idx != -1:
                    # Remove from child list
                    struct.pack_into('<I', payload, count_off, current_count - 1)
                    del payload[count_off + 4 + child_idx * 4 : count_off + 4 + child_idx * 4 + 4]

                    # Remove from playlist if present.
                    # BlendContainer (type 9) has no playlist — skip to avoid corrupting
                    # the BlendTrackList binary data that follows the child IDs.
                    if obj.type_id in (5, 6, 7):
                        playlist_off = count_off + 4 + (current_count - 1) * 4
                        if playlist_off + 2 <= len(payload):
                            playlist_len = struct.unpack_from('<H', payload, playlist_off)[0]
                            for j in range(playlist_len):
                                entry_off = playlist_off + 2 + j * 8
                                if entry_off + 4 <= len(payload):
                                    play_id = struct.unpack_from('<I', payload, entry_off)[0]
                                    if play_id == child_id:
                                        # Decrement playlist length
                                        struct.pack_into('<H', payload, playlist_off, playlist_len - 1)
                                        # Remove the 8-byte entry
                                        del payload[entry_off:entry_off + 8]
                                        break

                    obj.raw_payload = bytes(payload)
                    return

        # Fallback: scan-based remove if offset is not set
        target = struct.pack('<I', child_id)
        idx = payload.find(target, 4)
        if idx != -1 and idx >= 8:
            count_off = idx - 4
            for cid in obj.child_ids:
                t = struct.pack('<I', cid)
                ci = payload.find(t, 4)
                if ci != -1 and ci < idx:
                    count_off = ci - 4
                    break
            current_count = struct.unpack_from('<I', payload, count_off)[0]
            if 0 < current_count <= 1000:
                # Remove from child list
                struct.pack_into('<I', payload, count_off, current_count - 1)
                del payload[idx:idx + 4]

                # Remove from playlist if present.
                # BlendContainer (type 9) has no playlist — skip to avoid corruption.
                if obj.type_id in (5, 6, 7):
                    playlist_off = count_off + 4 + (current_count - 1) * 4
                    if playlist_off + 2 <= len(payload):
                        playlist_len = struct.unpack_from('<H', payload, playlist_off)[0]
                        if 0 < playlist_len < 1000:
                            for j in range(playlist_len):
                                entry_off = playlist_off + 2 + j * 8
                                if entry_off + 4 <= len(payload):
                                    play_id = struct.unpack_from('<I', payload, entry_off)[0]
                                    if play_id == child_id:
                                        # Decrement playlist length
                                        struct.pack_into('<H', payload, playlist_off, playlist_len - 1)
                                        # Remove the 8-byte entry
                                        del payload[entry_off:entry_off + 8]
                                        break

                obj.raw_payload = bytes(payload)



    def _rebuild_all_hirc(self):
        """Rebuild HIRC data for all events banks."""
        from lam.parsers.hirc_parser import HircParser
        for path, lb in self.project.events_banks.items():
            if lb.hirc_parser:
                lb.hirc_parser.objects = self.project.all_hirc_objects
                lb.bnk.hirc_data = lb.hirc_parser.rebuild_hirc()
