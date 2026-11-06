"""
Position-tracked binary reader with absolute offset tracking.
Provides safe, bounds-checked reading of all primitive types
used in Wwise binary formats.
"""

import struct
from typing import Optional


class ParseError(Exception):
    """Raised when binary parsing fails due to structural issues."""
    def __init__(self, message: str, offset: int = -1, expected: int = 0, available: int = 0):
        self.offset = offset
        self.expected = expected
        self.available = available
        super().__init__(message)


class BinaryReader:
    """
    A position-tracked binary reader that provides safe, bounds-checked
    access to binary data with absolute offset tracking.

    The base_offset parameter allows tracking the reader's position
    relative to the original file, even when operating on sliced sub-regions.
    """

    __slots__ = ('_data', '_pos', '_base_offset', '_length')

    def __init__(self, data: bytes, base_offset: int = 0):
        if isinstance(data, (bytearray, memoryview)):
            self._data = bytes(data)
        else:
            self._data = data
        self._pos = 0
        self._base_offset = base_offset
        self._length = len(self._data)

    @property
    def data(self) -> bytes:
        return self._data

    @property
    def position(self) -> int:
        return self._pos

    @property
    def base_offset(self) -> int:
        return self._base_offset

    @property
    def absolute_offset(self) -> int:
        return self._base_offset + self._pos

    @property
    def length(self) -> int:
        return self._length

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > self._length:
            raise ParseError(
                f"Seek out of bounds: {pos} (data length: {self._length})",
                offset=self.absolute_offset
            )
        self._pos = pos

    def skip(self, count: int) -> None:
        new_pos = self._pos + count
        if new_pos < 0 or new_pos > self._length:
            raise ParseError(
                f"Skip out of bounds: {self._pos} + {count} > {self._length}",
                offset=self.absolute_offset
            )
        self._pos = new_pos

    def remaining(self) -> int:
        return self._length - self._pos

    def is_eof(self) -> bool:
        return self._pos >= self._length

    def _check_bounds(self, size: int) -> None:
        if self._pos + size > self._length:
            raise ParseError(
                f"Read overrun at offset {self.absolute_offset}: "
                f"need {size} bytes, have {self.remaining()}",
                offset=self.absolute_offset,
                expected=size,
                available=self.remaining()
            )

    def read_bytes(self, count: int) -> bytes:
        self._check_bounds(count)
        result = self._data[self._pos:self._pos + count]
        self._pos += count
        return result

    def peek_bytes(self, count: int) -> bytes:
        self._check_bounds(count)
        return self._data[self._pos:self._pos + count]

    def read_u8(self) -> int:
        self._check_bounds(1)
        val = self._data[self._pos]
        self._pos += 1
        return val

    def read_u16(self) -> int:
        self._check_bounds(2)
        val = struct.unpack_from('<H', self._data, self._pos)[0]
        self._pos += 2
        return val

    def read_u32(self) -> int:
        self._check_bounds(4)
        val = struct.unpack_from('<I', self._data, self._pos)[0]
        self._pos += 4
        return val

    def read_i8(self) -> int:
        self._check_bounds(1)
        val = struct.unpack_from('<b', self._data, self._pos)[0]
        self._pos += 1
        return val

    def read_i16(self) -> int:
        self._check_bounds(2)
        val = struct.unpack_from('<h', self._data, self._pos)[0]
        self._pos += 2
        return val

    def read_i32(self) -> int:
        self._check_bounds(4)
        val = struct.unpack_from('<i', self._data, self._pos)[0]
        self._pos += 4
        return val

    def read_u64(self) -> int:
        self._check_bounds(8)
        val = struct.unpack_from('<Q', self._data, self._pos)[0]
        self._pos += 8
        return val

    def read_f32(self) -> float:
        self._check_bounds(4)
        val = struct.unpack_from('<f', self._data, self._pos)[0]
        self._pos += 4
        return val

    def read_f64(self) -> float:
        self._check_bounds(8)
        val = struct.unpack_from('<d', self._data, self._pos)[0]
        self._pos += 8
        return val

    def read_string(self, length: int, encoding: str = 'ascii') -> str:
        raw = self.read_bytes(length)
        null_pos = raw.find(b'\x00')
        if null_pos != -1:
            raw = raw[:null_pos]
        return raw.decode(encoding, errors='replace')

    def read_string_utf16(self, char_count: int) -> str:
        raw = self.read_bytes(char_count * 2)
        null_pos = raw.find(b'\x00\x00')
        if null_pos != -1 and null_pos % 2 == 0:
            raw = raw[:null_pos]
        return raw.decode('utf-16-le', errors='replace')

    def read_cstring(self, encoding: str = 'ascii') -> str:
        start = self._pos
        while self._pos < self._length and self._data[self._pos] != 0:
            self._pos += 1
        result = self._data[start:self._pos].decode(encoding, errors='replace')
        if self._pos < self._length:
            self._pos += 1  # skip null terminator
        return result

    def read_tag(self) -> str:
        return self.read_bytes(4).decode('ascii', errors='replace')

    def read_struct(self, fmt: str) -> tuple:
        size = struct.calcsize(fmt)
        self._check_bounds(size)
        result = struct.unpack_from(fmt, self._data, self._pos)
        self._pos += size
        return result

    def slice(self, length: int) -> 'BinaryReader':
        """Create a sub-reader over a region of this reader's data."""
        self._check_bounds(length)
        sub_data = self._data[self._pos:self._pos + length]
        sub_reader = BinaryReader(sub_data, base_offset=self.absolute_offset)
        self._pos += length
        return sub_reader

    def slice_from_current(self, length: Optional[int] = None) -> 'BinaryReader':
        """Create a sub-reader from current position without advancing."""
        if length is None:
            length = self.remaining()
        self._check_bounds(length)
        sub_data = self._data[self._pos:self._pos + length]
        return BinaryReader(sub_data, base_offset=self.absolute_offset)

    def __repr__(self) -> str:
        return (
            f"BinaryReader(pos={self._pos}, length={self._length}, "
            f"base={self._base_offset}, remaining={self.remaining()})"
        )
