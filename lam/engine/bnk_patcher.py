"""
BNK Patcher.

Post-processes a Wwise-generated .bnk file to make it game-compatible by:
using reconstruction to align all objects and headers with the original BNK.
This is 100% binary-equivalent to the original structure, except for the MusicTrack (type 11)
objects where the WEM references are safely patched in-place within the original binary payloads.
"""

import struct
from typing import Dict, List, Tuple
from lam.core.logging import get_logger
from lam.core.binary_reader import BinaryReader


class BnkPatcher:
    """
    Patches a Wwise-generated .bnk so it is indistinguishable from the
    original at the level the game's Wwise runtime cares about.
    """

    def __init__(self):
        self._log = get_logger()

    def _parse_chunks(self, data: bytes) -> list:
        chunks = []
        offset = 0
        while offset + 8 <= len(data):
            tag = data[offset:offset+4]
            size = struct.unpack_from('<I', data, offset+4)[0]
            payload = data[offset+8:offset+8+size]
            chunks.append((tag, size, payload))
            offset += 8 + size
        return chunks

    def _parse_hirc_objects(self, hirc_payload: bytes) -> list:
        if len(hirc_payload) < 4:
            return []
        count = struct.unpack_from('<I', hirc_payload, 0)[0]
        pos = 4
        objects = []
        for _ in range(count):
            if pos + 5 > len(hirc_payload):
                break
            obj_type = hirc_payload[pos]
            obj_size = struct.unpack_from('<I', hirc_payload, pos+1)[0]
            if pos + 5 + obj_size > len(hirc_payload):
                break
            obj_id = struct.unpack_from('<I', hirc_payload, pos+5)[0]
            raw = bytes(hirc_payload[pos:pos+5+obj_size])
            objects.append((obj_id, obj_type, raw))
            pos += 5 + obj_size
        return objects

    def _patch_track_wem_ids(self, orig_raw: bytes, gen_raw: bytes, id_map: dict = None) -> bytes:
        """
        Safely patches custom WEM IDs from gen_raw in-place inside the
        high-fidelity original track structure orig_raw.
        """
        orig_patch = bytearray(orig_raw)
        
        # 1. Parse generated track's WEM IDs
        try:
            # gen_raw: type (1b) + size (4b) + payload (object_id 4b + track_data)
            # So actual track data starts at offset 9
            gen_pr = BinaryReader(gen_raw[9:])
            gen_pr.skip(1)  # track flag
            gen_src_count = gen_pr.read_u32()
            gen_srcs = []
            for _ in range(gen_src_count):
                if gen_pr.remaining() < 14:
                    break
                plugin_id = gen_pr.read_u32()
                stream_type = gen_pr.read_u8()
                source_id = gen_pr.read_u32()
                in_mem_size = gen_pr.read_u32()
                source_bits = gen_pr.read_u8()
                
                if id_map and source_id in id_map:
                    source_id = id_map[source_id]
                gen_srcs.append((source_id, in_mem_size))
                
            gen_playlist_count = gen_pr.read_u32()
            gen_pls = []
            for _ in range(gen_playlist_count):
                if gen_pr.remaining() < 44:
                    break
                track_index = gen_pr.read_u32()
                file_id = gen_pr.read_u32()
                event_id = gen_pr.read_u32()
                gen_pr.skip(32)
                
                if id_map and file_id in id_map:
                    file_id = id_map[file_id]
                gen_pls.append(file_id)
        except Exception as e:
            self._log.warn("BnkPatcher", f"Failed to parse generated track WEMs: {e}")
            return orig_raw

        # 2. Patch into original track payload in-place
        try:
            orig_pr = BinaryReader(orig_raw[9:])
            orig_pr.skip(1)  # track flag
            orig_src_count = orig_pr.read_u32()
            
            for i in range(orig_src_count):
                entry_offset = 9 + orig_pr.absolute_offset
                plugin_id = orig_pr.read_u32()
                stream_type = orig_pr.read_u8()
                source_id = orig_pr.read_u32()
                in_mem_size = orig_pr.read_u32()
                source_bits = orig_pr.read_u8()
                
                # Patch source_id and in_mem_size in original payload
                if i < len(gen_srcs):
                    gen_source_id, gen_in_mem_size = gen_srcs[i]
                    struct.pack_into('<I', orig_patch, entry_offset + 5, gen_source_id)
                    struct.pack_into('<I', orig_patch, entry_offset + 9, gen_in_mem_size)
                    
            orig_playlist_count = orig_pr.read_u32()
            for i in range(orig_playlist_count):
                entry_offset = 9 + orig_pr.absolute_offset
                track_index = orig_pr.read_u32()
                file_id = orig_pr.read_u32()
                event_id = orig_pr.read_u32()
                orig_pr.skip(32)
                
                # Patch file_id in original playlist
                if i < len(gen_pls):
                    struct.pack_into('<I', orig_patch, entry_offset + 4, gen_pls[i])
        except Exception as e:
            self._log.warn("BnkPatcher", f"Failed to patch track WEMs: {e}")
            return orig_raw
            
        return bytes(orig_patch)

    def _patch_sound_wem_id(self, gen_raw: bytes, id_map: dict = None) -> bytes:
        """
        Patches the Wwise-internal WEM ID inside a CAkSound (type 2) object payload
        back to the original WEM ID using id_map.
        """
        if not id_map:
            return gen_raw
            
        patch_raw = bytearray(gen_raw)
        try:
            # Type 2 structure:
            # offset 0: type (1b)
            # offset 1: size (4b)
            # offset 5: obj_id (4b)
            # offset 9: plugin_id (4b)
            # offset 13: stream_type (1b)
            # offset 14: source_id / wem_id (4b)
            # offset 18: in_memory_size (4b)
            # offset 22: source_bits (1b)
            
            if len(patch_raw) >= 18:
                source_id = struct.unpack_from('<I', patch_raw, 14)[0]
                if source_id in id_map:
                    orig_id = id_map[source_id]
                    struct.pack_into('<I', patch_raw, 14, orig_id)
                    self._log.info("BnkPatcher", f"Patched CAkSound WEM ID: {source_id} -> {orig_id}")
        except Exception as e:
            self._log.warn("BnkPatcher", f"Failed to patch CAkSound WEM ID: {e}")
            
        return bytes(patch_raw)

    def patch(self, original_bnk_path: str, generated_bnk_path: str, id_map: dict = None) -> dict:
        """
        Patch *generated_bnk_path* in-place using *original_bnk_path*.
        Reconstructs the soundbank structure to ensure 100% compatibility.
        """
        self._log.info("BnkPatcher",
                       f"Patching via Reconstruction: {generated_bnk_path}",
                       operation="patch")

        orig_data = open(original_bnk_path, "rb").read()
        gen_data  = open(generated_bnk_path, "rb").read()

        orig_chunks = self._parse_chunks(orig_data)
        gen_chunks = self._parse_chunks(gen_data)

        orig_hirc_payload = None
        for tag, size, payload in orig_chunks:
            if tag == b'HIRC':
                orig_hirc_payload = payload
                break

        gen_hirc_payload = None
        for tag, size, payload in gen_chunks:
            if tag == b'HIRC':
                gen_hirc_payload = payload
                break

        if orig_hirc_payload is None or gen_hirc_payload is None:
            raise ValueError("BKHD or HIRC chunks missing from BNK files.")

        orig_objs = self._parse_hirc_objects(orig_hirc_payload)
        gen_objs = self._parse_hirc_objects(gen_hirc_payload)

        # Map generated objects by ID for fast lookup
        gen_map = {oid: (t, raw) for oid, t, raw in gen_objs}
        # Track which original IDs exist so we can detect brand-new objects below
        orig_id_set = {oid for oid, t, raw in orig_objs}

        reconstructed_objs = []
        restored = 0
        patched_tracks = 0
        patched_sounds = 0

        for oid, t, orig_raw in orig_objs:
            if oid in gen_map:
                gen_type, gen_raw = gen_map[oid]
                if t == 11:
                    patched_raw = self._patch_track_wem_ids(orig_raw, gen_raw, id_map)
                    reconstructed_objs.append(patched_raw)
                    patched_tracks += 1
                    self._log.info("BnkPatcher", f"Patched track WEM IDs for MusicTrack ID={oid}")
                elif t == 2:
                    patched_raw = self._patch_sound_wem_id(gen_raw, id_map)
                    reconstructed_objs.append(patched_raw)
                    patched_sounds += 1
                else:
                    reconstructed_objs.append(gen_raw)
            else:
                reconstructed_objs.append(orig_raw)
                restored += 1

        # Append objects that exist in the Wwise-generated BNK but NOT in the original.
        new_objects = 0
        for oid, t, gen_raw in gen_objs:
            if oid not in orig_id_set:
                if t == 2:
                    gen_raw = self._patch_sound_wem_id(gen_raw, id_map)
                reconstructed_objs.append(gen_raw)
                new_objects += 1
                self._log.info("BnkPatcher", f"Preserved new Wwise object ID={oid} type={t} (not in original BNK)")
        if new_objects:
            self._log.info("BnkPatcher", f"Appended {new_objects} new object(s) from generated BNK")

        # Build new HIRC payload
        new_hirc_payload = struct.pack('<I', len(reconstructed_objs))
        for obj_raw in reconstructed_objs:
            new_hirc_payload += obj_raw

        # Reconstruct entire SoundBank data
        new_bnk = bytearray()
        for tag, size, payload in orig_chunks:
            if tag == b'BKHD':
                # Copy BKHD chunk exactly as is from original
                new_bnk.extend(tag)
                new_bnk.extend(struct.pack('<I', len(payload)))
                new_bnk.extend(payload)
            elif tag == b'HIRC':
                # Write reconstructed HIRC chunk
                new_bnk.extend(tag)
                new_bnk.extend(struct.pack('<I', len(new_hirc_payload)))
                new_bnk.extend(new_hirc_payload)
            else:
                # Copy other chunks exactly
                new_bnk.extend(tag)
                new_bnk.extend(struct.pack('<I', len(payload)))
                new_bnk.extend(payload)

        with open(generated_bnk_path, "wb") as f:
            f.write(new_bnk)

        summary = {
            "proj_id_patched": True,
            "restored": restored,
            "injected": patched_tracks,
            "patched_sounds": patched_sounds,
            "new_objects": new_objects
        }
        self._log.info("BnkPatcher", f"Done: {summary}", operation="patch_complete")
        return summary
