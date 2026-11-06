"""Audio entry metadata for WEM files."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class AudioEntry:
    """Represents a single WEM audio asset with its metadata."""
    wem_id: int
    name: str = ""
    offset: int = 0
    size: int = 0
    source_file: str = ""  # which file contains this WEM
    source_type: str = ""  # "wpk" or "bnk"
    payload: bytes = b''
    is_modified: bool = False
    original_size: int = 0

    @property
    def display_name(self) -> str:
        return self.name if self.name else f"{self.wem_id}.wem"

    @property
    def has_payload(self) -> bool:
        return len(self.payload) > 0
