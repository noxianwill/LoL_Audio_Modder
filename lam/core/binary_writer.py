"""
Binary writer for deterministic reconstruction of Wwise assets.
Provides alignment-aware writing with offset tracking.
"""

import struct
from typing import Optional


class BinaryWriter:
    """
    A binary writer that builds byte sequences for Wwise asset reconstruction.
    Supports alignment, offset tracking, and patch-back of previously written fields.
    """

    __slots__ = ('_buffer',)

    def __init__(self, initial_capacity: int = 4096):
        self._buffer = bytearray()

    @property
    def position(self) -> int:
        return len(self._buffer)

    @property
    def length(self) -> int:
        return len(self._buffer)

    def write_bytes(self, data: bytes) -> int:
        offset = len(self._buffer)
        self._buffer.extend(data)
        return offset

    def write_u8(self, value: int) -> int:
        offset = len(self._buffer)
        self._buffer.append(value & 0xFF)
        return offset

    def write_u16(self, value: int) -> int:
        offset = len(self._buffer)
        self._buffer.extend(struct.pack('<H', value & 0xFFFF))
        return offset

    def write_u32(self, value: int) -> int:
        offset = len(self._buffer)
        self._buffer.extend(struct.pack('<I', value & 0xFFFFFFFF))
        return offset

    def write_i8(self, value: int) -> int:
        offset = len(self._buffer)
        self._buffer.extend(struct.pack('<b', value))
        return offset

    def write_i16(self, value: int) -> int:
        offset = len(self._buffer)
        self._buffer.extend(struct.pack('<h', value))
        return offset

    def write_i32(self, value: int) -> int:
        offset = len(self._buffer)
        self._buffer.extend(struct.pack('<i', value))
        return offset

    def write_u64(self, value: int) -> int:
        offset = len(self._buffer)
        self._buffer.extend(struct.pack('<Q', value))
        return offset

    def write_f32(self, value: float) -> int:
        offset = len(self._buffer)
        self._buffer.extend(struct.pack('<f', value))
        return offset

    def write_f64(self, value: float) -> int:
        offset = len(self._buffer)
        self._buffer.extend(struct.pack('<d', value))
        return offset

    def write_tag(self, tag: str) -> int:
        assert len(tag) == 4, f"Tag must be exactly 4 chars: {tag!r}"
        return self.write_bytes(tag.encode('ascii'))

    def write_string_utf16(self, text: str) -> int:
        offset = len(self._buffer)
        encoded = text.encode('utf-16-le')
        self._buffer.extend(encoded)
        return offset

    def write_cstring(self, text: str, encoding: str = 'ascii') -> int:
        offset = len(self._buffer)
        self._buffer.extend(text.encode(encoding))
        self._buffer.append(0)
        return offset

    def write_pad(self, count: int, value: int = 0) -> int:
        offset = len(self._buffer)
        self._buffer.extend(bytes([value]) * count)
        return offset

    def write_pad_to(self, alignment: int, value: int = 0) -> int:
        """Pad to the next alignment boundary."""
        current = len(self._buffer)
        remainder = current % alignment
        if remainder != 0:
            pad_count = alignment - remainder
            self._buffer.extend(bytes([value]) * pad_count)
            return pad_count
        return 0

    def patch_u32(self, offset: int, value: int) -> None:
        """Patch a previously written u32 at the given offset."""
        struct.pack_into('<I', self._buffer, offset, value & 0xFFFFFFFF)

    def patch_u16(self, offset: int, value: int) -> None:
        struct.pack_into('<H', self._buffer, offset, value & 0xFFFF)

    def patch_bytes(self, offset: int, data: bytes) -> None:
        self._buffer[offset:offset + len(data)] = data

    def reserve_u32(self) -> int:
        """Write a placeholder u32 (0) and return its offset for later patching."""
        offset = len(self._buffer)
        self._buffer.extend(b'\x00\x00\x00\x00')
        return offset

    def get_data(self) -> bytes:
        return bytes(self._buffer)

    def get_buffer(self) -> bytearray:
        return self._buffer

    def clear(self) -> None:
        self._buffer.clear()

    def __len__(self) -> int:
        return len(self._buffer)

    def __repr__(self) -> str:
        return f"BinaryWriter(length={len(self._buffer)})"
