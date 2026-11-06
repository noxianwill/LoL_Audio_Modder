"""
Wwise-compatible hashing utilities.

Implements:
1. FNV-1 32-bit hash (for Events, Banks, Game Syncs)
2. Mixed-endian GUID-based 30-bit hash (for Sound objects, Buses, WEM ShortIDs)
"""

import struct
from typing import Dict, Optional


# FNV-1 constants
FNV1_OFFSET_BASIS_32 = 2166136261
FNV1_PRIME_32 = 16777619
FNV1_MOD_32 = 2**32


def fnv1_hash_32(name: str) -> int:
    """
    Compute Wwise-compatible 32-bit FNV-1 hash.

    Process:
    1. Convert to lowercase
    2. Normalize path separators (backslash -> forward slash)
    3. Strip leading/trailing whitespace
    4. Hash each ASCII byte: h = (h * prime) XOR byte

    Used for: Events, Banks, Game Syncs
    """
    normalized = name.lower().strip().replace('\\', '/')
    h = FNV1_OFFSET_BASIS_32
    for byte in normalized.encode('ascii', errors='ignore'):
        h = ((h * FNV1_PRIME_32) % FNV1_MOD_32) ^ byte
    return h


def fnv1_hash_30_guid(guid_str: str) -> int:
    """
    Compute mixed-endian GUID-based 30-bit hash.

    Process:
    1. Parse GUID string (e.g., "{12345678-1234-1234-1234-123456789ABC}")
    2. Reorder bytes into mixed-endian layout
    3. Compute FNV-1 hash over the reordered bytes
    4. Mask to 30 bits: hash & 0x3FFFFFFF

    Used for: Sound SFX, Sound Voice, Actor Mixers, Buses, WEM ShortIDs
    """
    guid_clean = guid_str.strip('{}').replace('-', '')
    if len(guid_clean) != 32:
        raise ValueError(f"Invalid GUID: {guid_str}")

    guid_bytes = bytes.fromhex(guid_clean)

    # Mixed-endian reorder:
    # Data1 (4 bytes LE) + Data2 (2 bytes LE) + Data3 (2 bytes LE) + Data4 (8 bytes BE)
    data1 = struct.unpack('<I', guid_bytes[0:4])[0]
    data2 = struct.unpack('<H', guid_bytes[4:6])[0]
    data3 = struct.unpack('<H', guid_bytes[6:8])[0]
    data4 = guid_bytes[8:16]

    reordered = struct.pack('>I', data1) + struct.pack('>H', data2) + \
                struct.pack('>H', data3) + data4

    h = FNV1_OFFSET_BASIS_32
    for byte in reordered:
        h = ((h * FNV1_PRIME_32) % FNV1_MOD_32) ^ byte

    return h & 0x3FFFFFFF


def wem_short_id(name: str) -> int:
    """
    Generate a WEM ShortID from a filename.
    Uses standard FNV-1 32-bit hash on the lowercase name without extension.
    """
    base = name.rsplit('.', 1)[0] if '.' in name else name
    return fnv1_hash_32(base)


import threading


class HashLookup:
    """
    Bidirectional hash lookup table.
    Maps FNV-1 hashes to their original strings and vice versa.
    """

    def __init__(self):
        self._hash_to_name: Dict[int, str] = {}
        self._name_to_hash: Dict[str, int] = {}
        self._lock = threading.Lock()

    def register(self, name: str) -> int:
        """Register a name and return its hash."""
        h = fnv1_hash_32(name)
        with self._lock:
            self._hash_to_name[h] = name
            self._name_to_hash[name.lower()] = h
        return h

    def register_batch(self, names) -> None:
        """Register multiple names."""
        for name in names:
            self.register(name)

    def register_pairs(self, pairs) -> None:
        """Register multiple precomputed (hash, name) pairs in bulk under a single lock."""
        local_hash_to_name = {}
        local_name_to_hash = {}
        for h, name in pairs:
            local_hash_to_name[h] = name
            local_name_to_hash[name.lower()] = h

        with self._lock:
            self._hash_to_name.update(local_hash_to_name)
            self._name_to_hash.update(local_name_to_hash)


    def resolve(self, hash_id: int) -> Optional[str]:
        """Resolve a hash to its original name, or None if unknown."""
        with self._lock:
            return self._hash_to_name.get(hash_id)

    def lookup(self, name: str) -> Optional[int]:
        """Look up the hash for a name, or None if not registered."""
        with self._lock:
            return self._name_to_hash.get(name.lower())

    def resolve_or_hex(self, hash_id: int) -> str:
        """Resolve a hash to its name, or return hex representation."""
        with self._lock:
            name = self._hash_to_name.get(hash_id)
        if name is not None:
            return name
        return f"0x{hash_id:08X}"

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._hash_to_name)

    def __contains__(self, hash_id: int) -> bool:
        with self._lock:
            return hash_id in self._hash_to_name

    def __repr__(self) -> str:
        return f"HashLookup(entries={self.count})"
