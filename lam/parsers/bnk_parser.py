"""
Wwise BNK (SoundBank) parser.
Handles chunk-based BNK structure: BKHD, HIRC, DIDX, DATA.
Unknown chunks preserved for round-trip integrity.
"""
import struct
from typing import List, Optional, Dict
from dataclasses import dataclass, field
from lam.core.binary_reader import BinaryReader, ParseError
from lam.core.binary_writer import BinaryWriter
from lam.core.logging import get_logger


@dataclass
class BnkChunk:
    tag: str
    offset: int
    data: bytes
    @property
    def size(self):
        return len(self.data)


@dataclass
class BkhdData:
    version: int = 0
    bank_id: int = 0
    language_id: int = 0
    raw_data: bytes = b''


@dataclass
class DidxEntry:
    wem_id: int
    offset: int
    size: int


@dataclass
class BnkFile:
    source_path: str = ""
    chunks: List[BnkChunk] = field(default_factory=list)
    bkhd: Optional[BkhdData] = None
    hirc_data: Optional[bytes] = None
    didx_entries: List[DidxEntry] = field(default_factory=list)
    data_blob: Optional[bytes] = None

    @staticmethod
    def parse(data: bytes, source_path: str = "") -> 'BnkFile':
        log = get_logger()
        log.info("BnkParser", f"Parsing BNK: {source_path} ({len(data)} bytes)",
                 operation="parse", file_path=source_path, size=len(data))
        bnk = BnkFile(source_path=source_path)
        reader = BinaryReader(data)
        while reader.remaining() >= 8:
            chunk_offset = reader.tell()
            tag = reader.read_tag()
            chunk_size = reader.read_u32()
            if reader.remaining() < chunk_size:
                chunk_payload = reader.read_bytes(reader.remaining())
            else:
                chunk_payload = reader.read_bytes(chunk_size)
            chunk = BnkChunk(tag=tag, offset=chunk_offset, data=chunk_payload)
            bnk.chunks.append(chunk)
            if tag == "BKHD":
                bnk.bkhd = BnkFile._parse_bkhd(chunk_payload)
            elif tag == "HIRC":
                bnk.hirc_data = chunk_payload
                if len(chunk_payload) >= 4:
                    cnt = struct.unpack_from('<I', chunk_payload, 0)[0]
                    log.info("BnkParser", f"HIRC: {cnt} objects", operation="parse_hirc")
            elif tag == "DIDX":
                bnk.didx_entries = BnkFile._parse_didx(chunk_payload)
                log.info("BnkParser", f"DIDX: {len(bnk.didx_entries)} entries")
            elif tag == "DATA":
                bnk.data_blob = chunk_payload
        log.info("BnkParser", f"Parse complete: {len(bnk.chunks)} chunks")
        return bnk

    @staticmethod
    def _parse_bkhd(data: bytes) -> BkhdData:
        bkhd = BkhdData(raw_data=data)
        if len(data) >= 8:
            bkhd.version = struct.unpack_from('<I', data, 0)[0]
            bkhd.bank_id = struct.unpack_from('<I', data, 4)[0]
        if len(data) >= 12:
            bkhd.language_id = struct.unpack_from('<I', data, 8)[0]
        return bkhd

    @staticmethod
    def _parse_didx(data: bytes) -> List[DidxEntry]:
        entries = []
        for i in range(len(data) // 12):
            off = i * 12
            wid, doff, sz = struct.unpack_from('<III', data, off)
            entries.append(DidxEntry(wem_id=wid, offset=doff, size=sz))
        return entries

    def get_chunk(self, tag: str) -> Optional[BnkChunk]:
        for c in self.chunks:
            if c.tag == tag:
                return c
        return None

    def get_wem_data(self, wem_id: int) -> Optional[bytes]:
        if self.data_blob is None:
            return None
        for e in self.didx_entries:
            if e.wem_id == wem_id:
                return self.data_blob[e.offset:e.offset + e.size]
        return None

    def get_all_wem_ids(self) -> List[int]:
        return [e.wem_id for e in self.didx_entries]

    def rebuild(self) -> bytes:
        w = BinaryWriter()
        for chunk in self.chunks:
            data = chunk.data
            if chunk.tag == "HIRC" and self.hirc_data is not None:
                data = self.hirc_data
            if chunk.tag == "DIDX" and self.didx_entries:
                data = self._rebuild_didx()
            if chunk.tag == "DATA" and self.data_blob is not None:
                data = self.data_blob
            w.write_tag(chunk.tag)
            w.write_u32(len(data))
            w.write_bytes(data)
        return w.get_data()

    def _rebuild_didx(self) -> bytes:
        w = BinaryWriter()
        for e in self.didx_entries:
            w.write_u32(e.wem_id)
            w.write_u32(e.offset)
            w.write_u32(e.size)
        return w.get_data()

    def rebuild_data_blob(self, wem_payloads: Dict[int, bytes], alignment: int = 16):
        dw = BinaryWriter()
        new_didx = []
        for entry in self.didx_entries:
            payload = wem_payloads.get(entry.wem_id)
            if payload is None and self.data_blob:
                payload = self.data_blob[entry.offset:entry.offset + entry.size]
            if payload is None:
                continue
            dw.write_pad_to(alignment)
            new_off = dw.position
            dw.write_bytes(payload)
            new_didx.append(DidxEntry(wem_id=entry.wem_id, offset=new_off, size=len(payload)))
        self.didx_entries = new_didx
        self.data_blob = dw.get_data()
