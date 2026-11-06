"""
map.bin (PROP format) parser.
Extracts event name strings and builds FNV hash lookup tables.
Read-only parser for name resolution.
"""
import re
import struct
from typing import List, Dict, Set
from lam.core.binary_reader import BinaryReader
from lam.core.hashing import fnv1_hash_32, HashLookup
from lam.core.logging import get_logger


class MapBinFile:
    """Parses League of Legends map.bin (PROP format) for event name extraction."""

    def __init__(self):
        self.source_path: str = ""
        self.magic: str = ""
        self.version: int = 0
        self.entry_count: int = 0
        self.event_names: List[str] = []
        self.hash_lookup: HashLookup = HashLookup()
        self._raw_data: bytes = b''

    @staticmethod
    def parse(data: bytes, source_path: str = "") -> 'MapBinFile':
        log = get_logger()
        log.info("MapParser", f"Parsing map.bin: {source_path} ({len(data)} bytes)",
                 operation="parse", file_path=source_path, size=len(data))

        m = MapBinFile()
        m.source_path = source_path
        m._raw_data = data

        if len(data) < 14:
            log.error("MapParser", "File too small for PROP header")
            return m

        # Parse PROP header
        m.magic = data[:4].decode('ascii', errors='replace')
        m.version = struct.unpack_from('<I', data, 4)[0]
        # bytes 8-11 are unknown/zero
        m.entry_count = struct.unpack_from('<H', data, 12)[0]

        log.info("MapParser", f"PROP v{m.version}, entries={m.entry_count}",
                 operation="parse_header")

        # --- Pass 1: Structured binary parsing (primary, most reliable) ---
        # Search for event string containers using known byte patterns.
        # Pattern 1: Events container marker: 84 E3 D8 12 80 10
        # Pattern 2: Music container marker: D4 4F 9C 9F 83
        found_names: Set[str] = set()

        # Events containers
        events_marker = b'\x84\xe3\xd8\x12\x80\x10'
        pos = 0
        while True:
            idx = data.find(events_marker, pos)
            if idx == -1:
                break
            pos = idx + len(events_marker)
            if pos + 8 > len(data):
                break
            pos += 4  # skip object size field
            count = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            log.info("MapParser", f"Found events container with {count} strings at offset {idx}",
                     operation="parse_structured")
            for _ in range(count):
                if pos + 2 > len(data):
                    break
                str_len = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                if str_len == 0 or str_len > 500 or pos + str_len > len(data):
                    break
                name = data[pos:pos + str_len].decode('ascii', errors='ignore')
                pos += str_len
                name = name.rstrip('\x00')
                if len(name) > 2:
                    found_names.add(name)

        # Music containers
        music_marker = b'\xd4\x4f\x9c\x9f\x83'
        pos = 0
        while True:
            idx = data.find(music_marker, pos)
            if idx == -1:
                break
            pos = idx + len(music_marker)
            if pos + 4 > len(data):
                break
            type_hash = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            if type_hash == 0:
                continue
            if pos + 6 > len(data):
                break
            pos += 4  # skip object size
            count = struct.unpack_from('<H', data, pos)[0]
            pos += 2
            for _ in range(count):
                if pos + 5 > len(data):
                    break
                pos += 4  # skip name hash
                bin_type = data[pos]
                pos += 1
                if bin_type != 0x10:
                    break
                if pos + 2 > len(data):
                    break
                str_len = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                if str_len == 0 or str_len > 500 or pos + str_len > len(data):
                    break
                name = data[pos:pos + str_len].decode('ascii', errors='ignore')
                pos += str_len
                name = name.rstrip('\x00')
                if len(name) > 2:
                    found_names.add(name)

        structured_count = len(found_names)
        log.info("MapParser",
                 f"Structured parsing found {structured_count} names",
                 operation="parse_structured_complete")

        # --- Pass 2: Regex fallback (catches names not in structured containers) ---
        import os

        # 1. Path-like strings (e.g. .bnk, .wpk, .wem, .bin)
        for match in re.finditer(rb'[a-zA-Z0-9_\-\/.]+\.(?:bnk|wpk|wem|tex|bin)', data, re.IGNORECASE):
            name = match.group().decode('ascii', errors='ignore')
            clean = name.rstrip('\x00')
            if len(clean) > 4:
                found_names.add(clean)
                # Also add basename and name without extension
                base = os.path.basename(clean)
                found_names.add(base)
                found_names.add(os.path.splitext(base)[0])

        # 2. Case-insensitive common event/action prefixes
        common_prefixes = rb'\b(?:[pP]lay|[sS]top|[sS]et|[pP]ost|[pP]ause|[rR]esume|[mM]ute|[uU]nmute|[tT]rigger|[sS]tate)_[a-zA-Z0-9_]+\b'
        for match in re.finditer(common_prefixes, data):
            name = match.group().decode('ascii', errors='ignore')
            clean = name.rstrip('\x00')
            if len(clean) > 4:
                found_names.add(clean)

        # 3. Audio prefix types (sfx_, vo_, mus_, amb_)
        audio_prefixes = rb'\b(?:vo|sfx|mus|amb|VO|SFX|MUS|AMB)_[a-zA-Z0-9_]+\b'
        for match in re.finditer(audio_prefixes, data):
            name = match.group().decode('ascii', errors='ignore')
            clean = name.rstrip('\x00')
            if len(clean) > 4:
                found_names.add(clean)

        # 4. Words with at least two underscores (e.g. Announcer_Global_Female1)
        multi_underscore = rb'\b[a-zA-Z0-9]+_[a-zA-Z0-9]+_[a-zA-Z0-9_]+\b'
        for match in re.finditer(multi_underscore, data):
            name = match.group().decode('ascii', errors='ignore')
            clean = name.rstrip('\x00')
            if len(clean) > 4:
                found_names.add(clean)

        # Generic CamelCase_pattern scan
        for match in re.finditer(rb'[A-Z][a-z]+_[a-zA-Z0-9_]{3,}', data):
            name = match.group().decode('ascii', errors='ignore').rstrip('\x00')
            if len(name) > 4 and not any(c < ' ' for c in name):
                found_names.add(name)

        m.event_names = sorted(found_names)

        # Build hash lookup
        for name in m.event_names:
            m.hash_lookup.register(name)

        log.info("MapParser",
                 f"Extracted {len(m.event_names)} event names "
                 f"({structured_count} structured + {len(m.event_names) - structured_count} regex), "
                 f"{m.hash_lookup.count} hash entries",
                 operation="parse_complete")
        return m

    def resolve_hash(self, hash_id: int) -> str:
        """Resolve a FNV hash to event name, or hex string."""
        return self.hash_lookup.resolve_or_hex(hash_id)

    def find_events(self, pattern: str) -> List[str]:
        """Find event names matching a substring pattern (case-insensitive)."""
        pat = pattern.lower()
        return [n for n in self.event_names if pat in n.lower()]

    def get_hash(self, event_name: str) -> int:
        """Get FNV-1 hash for an event name."""
        return fnv1_hash_32(event_name)
