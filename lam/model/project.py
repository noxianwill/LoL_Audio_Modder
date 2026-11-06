"""
Project state manager.
Tracks all loaded files, parsed objects, modifications, and provides
the central coordination point between parsers, model, and GUI.
"""
import os
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from lam.parsers.bnk_parser import BnkFile
from lam.parsers.wpk_parser import WpkFile
from lam.parsers.hirc_parser import HircParser, HircObjectData
from lam.parsers.map_parser import MapBinFile
from lam.model.audio_entry import AudioEntry
from lam.model.hierarchy import HierarchyBuilder, HierarchyNode
from lam.core.hashing import HashLookup
from lam.core.hash_updater import HashUpdater
from lam.core.logging import get_logger


@dataclass
class LoadedBank:
    """A loaded BNK file with its parsed HIRC objects."""
    bnk: BnkFile
    hirc_parser: Optional[HircParser] = None
    label: str = ""  # "events" or "audio"


class Project:
    """Central project state: loaded files, hierarchy, modifications."""

    def __init__(self):
        self._log = get_logger()
        self.map_bin: Optional[MapBinFile] = None
        self.events_banks: Dict[str, LoadedBank] = {}  # path -> LoadedBank
        self.audio_banks: Dict[str, LoadedBank] = {}   # path -> LoadedBank
        self.wpk_files: Dict[str, WpkFile] = {}        # path -> WpkFile
        self.audio_entries: Dict[int, AudioEntry] = {}  # wem_id -> AudioEntry
        self.hash_lookup: HashLookup = HashLookup()
        
        # Initialize hash updater & load local cached hashes
        workspace_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.hash_updater = HashUpdater(workspace_dir, self.hash_lookup)

        self.progress_callback = None
        self.hierarchy_builder: HierarchyBuilder = HierarchyBuilder()
        self.hierarchy_roots: List[HierarchyNode] = []
        self.all_hirc_objects: List[HircObjectData] = []
        self.is_dirty: bool = False
        self.modifications: List[Dict] = []
        self.disabled_event_names: Dict[int, str] = {}

    def set_progress_callback(self, callback):
        """Set a progress callback for project loading and parsing operations."""
        self.progress_callback = callback

    def _report_progress(self, message: str):
        if self.progress_callback:
            try:
                self.progress_callback(message)
            except Exception:
                pass

    def start_hash_update(self):
        """Start the background check and update for CDragon hashes."""
        self.hash_updater.start()

    def load_map_bin(self, path: str):
        """Load and parse a map.bin file."""
        self._log.info("Project", f"Loading map.bin: {path}", operation="load_map",
                       file_path=path)
        data = open(path, 'rb').read()
        self.map_bin = MapBinFile.parse(data, source_path=path)
        # Merge hash lookup
        for name in self.map_bin.event_names:
            self.hash_lookup.register(name)
        self._log.info("Project",
                       f"map.bin loaded: {len(self.map_bin.event_names)} events",
                       operation="load_map_complete")

    def load_events_bnk(self, path: str):
        """Load an events.bnk file (BKHD + HIRC)."""
        self._log.info("Project", f"Loading events BNK: {path}",
                       operation="load_events", file_path=path)
        data = open(path, 'rb').read()
        bnk = BnkFile.parse(data, source_path=path)

        lb = LoadedBank(bnk=bnk, label="events")

        if bnk.hirc_data:
            parser = HircParser()
            parser.progress_callback = lambda i, total: self._report_progress(
                f"Parsing events BNK: {i}/{total} objects..."
            )
            hirc_offset = 0
            hirc_chunk = bnk.get_chunk("HIRC")
            if hirc_chunk:
                hirc_offset = hirc_chunk.offset + 8  # skip tag+size
            version = bnk.bkhd.version if bnk.bkhd else 145
            parser.parse(bnk.hirc_data, base_offset=hirc_offset, version=version)
            parser.resolve_relationships()
            lb.hirc_parser = parser
            self.all_hirc_objects.extend(parser.objects)

        self.events_banks[path] = lb
        self._log.info("Project",
                       f"Events BNK loaded: {len(lb.hirc_parser.objects) if lb.hirc_parser else 0} objects",
                       operation="load_events_complete")

    def load_audio_bnk(self, path: str):
        """Load an audio.bnk file (BKHD + DIDX + DATA) with embedded WEMs."""
        self._log.info("Project", f"Loading audio BNK: {path}",
                       operation="load_audio_bnk", file_path=path)
        data = open(path, 'rb').read()
        bnk = BnkFile.parse(data, source_path=path)
        lb = LoadedBank(bnk=bnk, label="audio")

        # Extract WEM entries from DIDX
        total_entries = len(bnk.didx_entries)
        for idx, entry in enumerate(bnk.didx_entries):
            if idx % 50 == 0:
                self._report_progress(f"Extracting WEMs: {idx}/{total_entries}...")
            payload = bnk.get_wem_data(entry.wem_id)
            ae = AudioEntry(
                wem_id=entry.wem_id,
                name=f"{entry.wem_id}.wem",
                offset=entry.offset,
                size=entry.size,
                source_file=path,
                source_type="bnk",
                payload=payload if payload else b'',
                original_size=entry.size,
            )
            self.audio_entries[entry.wem_id] = ae

        # Parse HIRC if present in the audio bank (some banks contain both audio and events)
        if bnk.hirc_data:
            parser = HircParser()
            parser.progress_callback = lambda i, total: self._report_progress(
                f"Parsing audio BNK HIRC: {i}/{total} objects..."
            )
            hirc_offset = 0
            hirc_chunk = bnk.get_chunk("HIRC")
            if hirc_chunk:
                hirc_offset = hirc_chunk.offset + 8  # skip tag+size
            version = bnk.bkhd.version if bnk.bkhd else 145
            parser.parse(bnk.hirc_data, base_offset=hirc_offset, version=version)
            parser.resolve_relationships()
            lb.hirc_parser = parser
            self.all_hirc_objects.extend(parser.objects)

        self.audio_banks[path] = lb
        self._log.info("Project",
                       f"Audio BNK loaded: {len(bnk.didx_entries)} WEMs, {len(lb.hirc_parser.objects) if lb.hirc_parser else 0} HIRC objects",
                       operation="load_audio_bnk_complete")

    def load_wpk(self, path: str):
        """Load a WPK audio package."""
        self._log.info("Project", f"Loading WPK: {path}",
                       operation="load_wpk", file_path=path)
        data = open(path, 'rb').read()
        wpk = WpkFile.parse(data, source_path=path)
        self.wpk_files[path] = wpk

        # Register WEM entries
        total_entries = len(wpk.entries)
        for idx, entry in enumerate(wpk.entries):
            if idx % 100 == 0:
                self._report_progress(f"Extracting WPK: {idx}/{total_entries} WEMs...")
            ae = AudioEntry(
                wem_id=entry.wem_id,
                name=entry.name,
                offset=entry.file_offset,
                size=entry.file_size,
                source_file=path,
                source_type="wpk",
                payload=entry.payload,
                original_size=entry.file_size,
            )
            self.audio_entries[entry.wem_id] = ae

        self._log.info("Project",
                       f"WPK loaded: {len(wpk.entries)} entries",
                       operation="load_wpk_complete")

    def build_hierarchy(self):
        """Reconstruct the Wwise hierarchy from all loaded objects."""
        self._log.info("Project", "Building hierarchy", operation="build_hierarchy")
        self.hierarchy_roots = self.hierarchy_builder.build(
            self.all_hirc_objects,
            hash_lookup=self.hash_lookup,
            audio_entries=self.audio_entries,
        )
        self._log.info("Project",
                       f"Hierarchy built: {len(self.hierarchy_roots)} roots",
                       operation="build_hierarchy_complete")

    def auto_load(self, paths: List[str]):
        """Auto-detect file types and load them."""
        total = len(paths)
        for idx, path in enumerate(paths):
            basename = os.path.basename(path)
            self._report_progress(f"Parsing ({idx+1}/{total}): {basename}...")
            ext = os.path.splitext(path)[1].lower()
            if ext == '.bin':
                self.load_map_bin(path)
            elif ext == '.wpk':
                self.load_wpk(path)
            elif ext == '.bnk':
                try:
                    with open(path, 'rb') as f:
                        data = f.read()
                        if b'DIDX' in data or b'DATA' in data:
                            self.load_audio_bnk(path)
                        else:
                            self.load_events_bnk(path)
                except Exception as e:
                    self._log.error("Project", f"Failed to auto-load BNK {path}: {e}")
        self._report_progress("Building hierarchy...")
        self.build_hierarchy()

    def get_wem_payload(self, wem_id: int) -> Optional[bytes]:
        """Get WEM payload from any loaded source."""
        ae = self.audio_entries.get(wem_id)
        if ae and ae.has_payload:
            return ae.payload
        # Try WPK sources
        for wpk in self.wpk_files.values():
            p = wpk.get_payload(wem_id)
            if p:
                return p
        # Try audio BNK sources
        for lb in self.audio_banks.values():
            p = lb.bnk.get_wem_data(wem_id)
            if p:
                return p
        return None

    def mark_dirty(self):
        self.is_dirty = True

    @property
    def loaded_file_count(self) -> int:
        count = 0
        if self.map_bin:
            count += 1
        count += len(self.events_banks)
        count += len(self.audio_banks)
        count += len(self.wpk_files)
        return count

    @property
    def total_wem_count(self) -> int:
        return len(self.audio_entries)

    @property
    def total_object_count(self) -> int:
        return len(self.all_hirc_objects)

    def export_wwise_template(self, project_folder: str):
        """Export parsed structures to Wwise project template."""
        from lam.engine.wwise_exporter import WwiseProjectExporter
        
        # Get bnk version
        bnk_version = 145
        for lb in self.events_banks.values():
            if lb.bnk and lb.bnk.bkhd:
                bnk_version = lb.bnk.bkhd.version
                break
                
        # Build bank groups mapping bank path -> list of event IDs in it
        bank_groups = {}
        for path, lb in self.events_banks.items():
            if lb.hirc_parser:
                event_ids = [obj.object_id for obj in lb.hirc_parser.objects if obj.type_id == 4]
                if event_ids:
                    bank_groups[path] = event_ids
                    # Check if there is a matching audio bank
                    base_name = os.path.basename(path)
                    if base_name.endswith("_events.bnk"):
                        prefix = base_name[:-11]  # remove '_events.bnk'
                        for audio_path, audio_lb in self.audio_banks.items():
                            audio_base = os.path.basename(audio_path)
                            if audio_base == prefix + "_audio.bnk":
                                bank_groups[audio_path] = event_ids
                    
        # If no event banks are loaded, check audio banks containing HIRC
        if not bank_groups:
            for path, lb in self.audio_banks.items():
                if lb.hirc_parser:
                    event_ids = [obj.object_id for obj in lb.hirc_parser.objects if obj.type_id == 4]
                    if event_ids:
                        bank_groups[path] = event_ids
                        
        if not bank_groups:
            # Fallback
            event_ids = [obj.object_id for obj in self.all_hirc_objects if obj.type_id == 4]
            if event_ids:
                bank_groups["DefaultSoundBank.bnk"] = event_ids
                
        exporter = WwiseProjectExporter()
        exporter.export(
            objects=self.all_hirc_objects,
            hash_lookup=self.hash_lookup,
            project_folder=project_folder,
            bnk_version=bnk_version,
            bank_groups=bank_groups
        )

    def clear(self):
        """Clear all loaded files and parsed objects to reset the project state."""
        self.map_bin = None
        self.events_banks.clear()
        self.audio_banks.clear()
        self.wpk_files.clear()
        self.audio_entries.clear()
        self.hash_lookup = HashLookup()
        self.hierarchy_builder = HierarchyBuilder()
        self.hierarchy_roots.clear()
        self.all_hirc_objects.clear()
        self.is_dirty = False
        self.modifications.clear()
        self.disabled_event_names.clear()
        self._log.info("Project", "Project state cleared", operation="clear")
