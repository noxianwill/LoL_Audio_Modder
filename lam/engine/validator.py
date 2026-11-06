"""Pre-export validation system. Checks structural integrity before rebuild."""
import struct
from typing import List, Optional
from dataclasses import dataclass
from enum import IntEnum
from lam.model.project import Project
from lam.core.logging import get_logger


class VSeverity(IntEnum):
    INFO = 0
    WARNING = 1
    ERROR = 2


@dataclass
class ValidationResult:
    severity: VSeverity
    category: str
    message: str
    object_id: int = 0
    wem_id: int = 0
    file_path: str = ""


class Validator:
    """Validates project state before export."""

    def __init__(self, project: Project):
        self.project = project
        self.results: List[ValidationResult] = []
        self._log = get_logger()

    def validate_all(self) -> List[ValidationResult]:
        """Run all validation checks. Returns list of issues found."""
        self._log.info("Validator", "Starting validation", operation="validate")
        self.results = []
        self._check_hirc_integrity()
        self._check_didx_consistency()
        self._check_wpk_integrity()
        self._check_wem_payloads()
        self._check_duplicate_ids()
        self._check_dangling_refs()
        self._check_orphaned_objects()

        errors = sum(1 for r in self.results if r.severity == VSeverity.ERROR)
        warnings = sum(1 for r in self.results if r.severity == VSeverity.WARNING)
        self._log.info("Validator",
                       f"Validation complete: {errors} errors, {warnings} warnings",
                       operation="validate_complete")
        return self.results

    @property
    def has_errors(self) -> bool:
        return any(r.severity == VSeverity.ERROR for r in self.results)

    @property
    def error_count(self) -> int:
        return sum(1 for r in self.results if r.severity == VSeverity.ERROR)

    def _add(self, sev: VSeverity, cat: str, msg: str, **kw):
        self.results.append(ValidationResult(severity=sev, category=cat, message=msg, **kw))
        if sev == VSeverity.ERROR:
            self._log.error("Validator", msg, operation="validate")
        elif sev == VSeverity.WARNING:
            self._log.warn("Validator", msg, operation="validate")

    def _check_hirc_integrity(self):
        """Verify HIRC object structural integrity."""
        for obj in self.project.all_hirc_objects:
            if len(obj.raw_payload) < 4:
                self._add(VSeverity.ERROR, "HIRC",
                         f"Object {obj.object_id} payload too small ({len(obj.raw_payload)} bytes)",
                         object_id=obj.object_id)
            # Verify object_id matches payload
            if len(obj.raw_payload) >= 4:
                embedded_id = struct.unpack_from('<I', obj.raw_payload, 0)[0]
                if embedded_id != obj.object_id:
                    self._add(VSeverity.ERROR, "HIRC",
                             f"Object ID mismatch: header={obj.object_id}, payload={embedded_id}",
                             object_id=obj.object_id)

        # Verify total HIRC size
        for path, lb in self.project.events_banks.items():
            if lb.bnk.hirc_data:
                expected_count = struct.unpack_from('<I', lb.bnk.hirc_data, 0)[0]
                actual = len(self.project.all_hirc_objects)
                if expected_count != actual:
                    self._add(VSeverity.WARNING, "HIRC",
                             f"Object count mismatch in {path}: header={expected_count}, actual={actual}",
                             file_path=path)

    def _check_didx_consistency(self):
        """Verify DIDX entries match DATA blob. Skipped if project has pending edits."""
        if self.project.is_dirty:
            return

        for path, lb in self.project.audio_banks.items():
            if lb.bnk.data_blob is None:
                continue
            data_size = len(lb.bnk.data_blob)
            for entry in lb.bnk.didx_entries:
                end = entry.offset + entry.size
                if end > data_size:
                    self._add(VSeverity.ERROR, "DIDX",
                             f"Entry {entry.wem_id}: offset+size ({end}) exceeds DATA ({data_size})",
                             wem_id=entry.wem_id, file_path=path)
                if entry.size == 0:
                    self._add(VSeverity.WARNING, "DIDX",
                             f"Entry {entry.wem_id} has zero size",
                             wem_id=entry.wem_id, file_path=path)
            # Check for overlapping entries
            sorted_entries = sorted(lb.bnk.didx_entries, key=lambda e: e.offset)
            for i in range(len(sorted_entries) - 1):
                curr_end = sorted_entries[i].offset + sorted_entries[i].size
                next_start = sorted_entries[i + 1].offset
                if curr_end > next_start:
                    if sorted_entries[i].offset == next_start:
                        self._add(VSeverity.INFO, "DIDX",
                                 f"Shared memory payload: {sorted_entries[i].wem_id} and {sorted_entries[i+1].wem_id} (Optimization)",
                                 file_path=path)
                    else:
                        self._add(VSeverity.ERROR, "DIDX",
                                 f"Overlapping memory bounds: {sorted_entries[i].wem_id} and {sorted_entries[i+1].wem_id}",
                                 file_path=path)

    def _check_wpk_integrity(self):
        """Verify WPK structure."""
        for path, wpk in self.project.wpk_files.items():
            for entry in wpk.entries:
                if entry.file_size == 0:
                    self._add(VSeverity.WARNING, "WPK",
                             f"Entry {entry.name} has zero size",
                             wem_id=entry.wem_id, file_path=path)
                if entry.has_payload:
                    if len(entry.payload) != entry.file_size:
                        self._add(VSeverity.WARNING, "WPK",
                                 f"Entry {entry.name}: payload size ({len(entry.payload)}) != declared ({entry.file_size})",
                                 wem_id=entry.wem_id, file_path=path)

    def _check_wem_payloads(self):
        """Verify WEM payload integrity."""
        for wem_id, ae in self.project.audio_entries.items():
            if ae.has_payload:
                if len(ae.payload) < 12:
                    self._add(VSeverity.WARNING, "WEM",
                             f"WEM {wem_id} payload very small ({len(ae.payload)} bytes)",
                             wem_id=wem_id)
                elif ae.payload[:4] != b'RIFF':
                    self._add(VSeverity.WARNING, "WEM",
                             f"WEM {wem_id} missing RIFF header",
                             wem_id=wem_id)

    def _check_duplicate_ids(self):
        """Check for duplicate object IDs."""
        seen = {}
        for obj in self.project.all_hirc_objects:
            if obj.object_id in seen:
                self._add(VSeverity.ERROR, "HIRC",
                         f"Duplicate object ID: {obj.object_id} (types: {seen[obj.object_id]}, {obj.type_id})",
                         object_id=obj.object_id)
            else:
                seen[obj.object_id] = obj.type_id

    def _check_dangling_refs(self):
        """Check for references to non-existent objects."""
        all_ids = {obj.object_id for obj in self.project.all_hirc_objects}
        for obj in self.project.all_hirc_objects:
            if obj.type_id == 3 and obj.target_id and obj.target_id not in all_ids:
                self._add(VSeverity.WARNING, "Reference",
                         f"Action {obj.object_id} targets non-existent {obj.target_id}",
                         object_id=obj.object_id)
            if obj.type_id == 4:
                for aid in obj.event_action_ids:
                    if aid not in all_ids:
                        self._add(VSeverity.WARNING, "Reference",
                                 f"Event {obj.object_id} references non-existent action {aid}",
                                 object_id=obj.object_id)

    def _check_orphaned_objects(self):
        """Check for objects with no parent and no event path."""
        parented = set()
        for obj in self.project.all_hirc_objects:
            for cid in obj.child_ids:
                parented.add(cid)
            for aid in obj.event_action_ids:
                parented.add(aid)
        for obj in self.project.all_hirc_objects:
            if obj.type_id in (2, 5, 6, 7) and obj.object_id not in parented:
                if obj.parent_id == 0:
                    self._add(VSeverity.INFO, "Orphan",
                             f"{obj.type_name} {obj.object_id} has no parent",
                             object_id=obj.object_id)
