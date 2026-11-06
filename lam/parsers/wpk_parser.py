"""
WPK (r3d2) audio package parser.
Parses the Riot r3d2 format used for .wpk files containing WEM audio payloads.
"""
import struct
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from lam.core.binary_reader import BinaryReader
from lam.core.binary_writer import BinaryWriter
from lam.core.logging import get_logger


@dataclass
class WpkEntry:
    """A single WEM entry in a WPK package."""
    index: int
    name: str            # e.g. "899480.wem"
    wem_id: int          # numeric ID parsed from name
    file_offset: int     # absolute offset of WEM data in WPK
    file_size: int       # size of WEM data
    meta_offset: int     # offset of metadata entry in WPK
    payload: bytes = b'' # actual WEM bytes (loaded on demand)

    @property
    def has_payload(self) -> bool:
        return len(self.payload) > 0


@dataclass
class WpkFile:
    """Parsed WPK (r3d2) audio package."""
    source_path: str = ""
    magic: str = "r3d2"
    version: int = 1
    entries: List[WpkEntry] = field(default_factory=list)
    _raw_data: bytes = field(default=b'', repr=False)

    @staticmethod
    def parse(data: bytes, source_path: str = "",
              load_payloads: bool = True) -> 'WpkFile':
        log = get_logger()
        log.info("WpkParser", f"Parsing WPK: {source_path} ({len(data)} bytes)",
                 operation="parse", file_path=source_path, size=len(data))

        wpk = WpkFile(source_path=source_path, _raw_data=data)
        reader = BinaryReader(data)

        # Header: magic(4) + version(4) + count(4)
        wpk.magic = reader.read_bytes(4).decode('ascii', errors='replace')
        wpk.version = reader.read_u32()
        count = reader.read_u32()

        log.info("WpkParser", f"Magic={wpk.magic}, version={wpk.version}, count={count}",
                 operation="parse_header")

        if wpk.magic != "r3d2":
            log.error("WpkParser", f"Invalid magic: {wpk.magic!r}, expected 'r3d2'")
            return wpk

        # Offset table: count * 4 bytes (offsets to metadata entries)
        meta_offsets = []
        for i in range(count):
            if reader.remaining() >= 4:
                meta_offsets.append(reader.read_u32())

        # Parse each metadata entry
        for i, meta_off in enumerate(meta_offsets):
            if meta_off + 12 > len(data):
                log.warn("WpkParser", f"Entry {i} meta_offset {meta_off} out of bounds",
                         operation="parse_entry")
                continue

            reader.seek(meta_off)
            f_offset = reader.read_u32()
            f_size = reader.read_u32()
            name_len = reader.read_u32()

            # Name is UTF-16LE, name_len chars
            name = ""
            if name_len > 0 and name_len < 1000 and reader.remaining() >= name_len * 2:
                name = reader.read_string_utf16(name_len)
            # Strip null chars
            name = name.rstrip('\x00')

            # Parse WEM ID from name
            wem_id = 0
            name_base = name.replace('.wem', '').replace('.WEM', '')
            try:
                wem_id = int(name_base)
            except ValueError:
                pass

            payload = b''
            if load_payloads and f_offset > 0 and f_size > 0:
                if f_offset + f_size <= len(data):
                    payload = data[f_offset:f_offset + f_size]
                else:
                    log.warn("WpkParser",
                        f"Entry {i} ({name}) payload out of bounds: "
                        f"off={f_offset} size={f_size} file_len={len(data)}",
                        operation="parse_entry", wem_id=wem_id)

            entry = WpkEntry(
                index=i, name=name, wem_id=wem_id,
                file_offset=f_offset, file_size=f_size,
                meta_offset=meta_off, payload=payload
            )
            wpk.entries.append(entry)

        log.info("WpkParser", f"Parsed {len(wpk.entries)} entries",
                 operation="parse_complete")
        return wpk

    def get_entry(self, wem_id: int) -> Optional[WpkEntry]:
        for e in self.entries:
            if e.wem_id == wem_id:
                return e
        return None

    def get_payload(self, wem_id: int) -> Optional[bytes]:
        entry = self.get_entry(wem_id)
        if entry and entry.has_payload:
            return entry.payload
        if entry and self._raw_data:
            if entry.file_offset + entry.file_size <= len(self._raw_data):
                return self._raw_data[entry.file_offset:entry.file_offset + entry.file_size]
        return None

    def get_all_wem_ids(self) -> List[int]:
        return [e.wem_id for e in self.entries]

    def add_entry(self, wem_id: int, name: str, payload: bytes) -> WpkEntry:
        """Add a new WEM entry to the package (appended at end)."""
        # Append at end — this matches the pre-regression behavior.
        # Sorted insert was tried but caused all sounds to be silent.
        existing = self.get_entry(wem_id)
        if existing is not None:
            existing.payload = payload
            existing.file_size = len(payload)
            get_logger().info("WpkParser", f"Updated existing entry: {name}",
                             operation="add_entry_update", wem_id=wem_id)
            return existing

        insert_pos = len(self.entries)

        entry = WpkEntry(
            index=insert_pos, name=name, wem_id=wem_id,
            file_offset=0, file_size=len(payload),
            meta_offset=0, payload=payload
        )
        self.entries.append(entry)

        get_logger().info("WpkParser", f"Added entry: {name} ({len(payload)} bytes) at index {insert_pos}",
                         operation="add_entry", wem_id=wem_id, size=len(payload))
        return entry

    def remove_entry(self, wem_id: int) -> bool:
        for i, e in enumerate(self.entries):
            if e.wem_id == wem_id:
                self.entries.pop(i)
                # Re-index
                for j in range(i, len(self.entries)):
                    self.entries[j].index = j
                get_logger().info("WpkParser", f"Removed entry: {e.name}",
                                 operation="remove_entry", wem_id=wem_id)
                return True
        return False

    def replace_payload(self, wem_id: int, new_payload: bytes) -> bool:
        entry = self.get_entry(wem_id)
        if entry is None:
            return False
        entry.payload = new_payload
        entry.file_size = len(new_payload)
        get_logger().info("WpkParser", f"Replaced payload: {entry.name}",
                         operation="replace_payload", wem_id=wem_id, size=len(new_payload))
        return True

    def rebuild(self, alignment: int = 16) -> bytes:
        """Rebuild the complete WPK file with recalculated offsets."""
        log = get_logger()
        log.info("WpkParser", f"Rebuilding WPK with {len(self.entries)} entries",
                 operation="rebuild")

        count = len(self.entries)
        # Calculate layout sizes
        header_size = 12  # magic(4) + version(4) + count(4)
        offset_table_size = count * 4

        # Build metadata entries to calculate sizes
        meta_blocks = []
        for entry in self.entries:
            name_encoded = entry.name.encode('utf-16-le')
            name_char_count = len(entry.name)
            # meta entry: f_offset(4) + f_size(4) + name_len(4) + name(name_len*2)
            meta_size = 12 + name_char_count * 2
            meta_blocks.append((name_char_count, name_encoded, meta_size))

        # Calculate meta section start with 8-byte aligned meta entries
        meta_section_start = header_size + offset_table_size
        remainder = meta_section_start % 8
        if remainder != 0:
            meta_section_start += 8 - remainder

        # Calculate aligned meta offsets (each entry starts on 8-byte boundary)
        aligned_meta_offsets = []
        current_meta_off = meta_section_start
        for name_len, name_encoded, raw_size in meta_blocks:
            aligned_meta_offsets.append(current_meta_off)
            current_meta_off += raw_size
            # Pad to 8-byte alignment for next entry
            remainder = current_meta_off % 8
            if remainder != 0:
                current_meta_off += 8 - remainder

        # Data section starts right after last padded meta entry
        data_start = current_meta_off

        # Calculate payload offsets - 8-byte aligned
        payload_offsets = []
        current_data_off = data_start
        for entry in self.entries:
            # Align to 8 bytes
            remainder = current_data_off % 8
            if remainder != 0:
                current_data_off += 8 - remainder
            payload_offsets.append(current_data_off)
            current_data_off += len(entry.payload)

        # Build the file
        w = BinaryWriter()
        # Header
        w.write_bytes(b'r3d2')
        w.write_u32(self.version)
        w.write_u32(count)

        # Offset table (offsets to metadata entries)
        for off in aligned_meta_offsets:
            w.write_u32(off)

        # Metadata entries with 8-byte alignment padding
        for i, entry in enumerate(self.entries):
            name_len, name_encoded, _ = meta_blocks[i]
            # Pad to aligned position
            while w.position < aligned_meta_offsets[i]:
                w.write_u8(0)
            w.write_u32(payload_offsets[i])  # file offset
            w.write_u32(len(entry.payload))  # file size
            w.write_u32(name_len)            # name length in chars
            w.write_bytes(name_encoded)      # name UTF-16LE

        # Pad to data start
        while w.position < data_start:
            w.write_u8(0)

        # Payload data
        for i, entry in enumerate(self.entries):
            while w.position < payload_offsets[i]:
                w.write_u8(0)
            w.write_bytes(entry.payload)

        result = w.get_data()
        log.info("WpkParser", f"Rebuild complete: {len(result)} bytes",
                 operation="rebuild_complete", size=len(result))
        return result
