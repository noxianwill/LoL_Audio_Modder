"""Deterministic rebuild/export pipeline for BNK and WPK files."""
import os
from typing import Dict, Optional, List, Tuple
from lam.model.project import Project
from lam.core.logging import get_logger


class Rebuilder:
    """Rebuilds modified BNK and WPK files for export."""

    def __init__(self, project: Project):
        self.project = project
        self._log = get_logger()

    def rebuild_all(self, output_dir: str) -> List[Tuple[str, str]]:
        """Rebuild all modified files. Returns list of (original_path, output_path)."""
        self._log.info("Rebuilder", f"Starting rebuild to {output_dir}",
                       operation="rebuild_all")
        os.makedirs(output_dir, exist_ok=True)
        outputs = []

        # Rebuild events BNK files
        for path, lb in self.project.events_banks.items():
            basename = os.path.basename(path)
            out_path = os.path.join(output_dir, basename)
            data = lb.bnk.rebuild()
            with open(out_path, 'wb') as f:
                f.write(data)
            outputs.append((path, out_path))
            self._log.info("Rebuilder", f"Rebuilt events BNK: {basename} ({len(data)} bytes)",
                          operation="rebuild_bnk", file_path=out_path, size=len(data))

        # Rebuild audio BNK files
        for path, lb in self.project.audio_banks.items():
            basename = os.path.basename(path)
            out_path = os.path.join(output_dir, basename)
            # Update DATA blob with modified payloads
            wem_payloads: Dict[int, bytes] = {}
            for entry in lb.bnk.didx_entries:
                ae = self.project.audio_entries.get(entry.wem_id)
                if ae and ae.has_payload:
                    wem_payloads[entry.wem_id] = ae.payload
            
            lb.bnk.rebuild_data_blob(wem_payloads)
            data = lb.bnk.rebuild()
            with open(out_path, 'wb') as f:
                f.write(data)
            outputs.append((path, out_path))
            self._log.info("Rebuilder", f"Rebuilt audio BNK: {basename} ({len(data)} bytes)",
                          operation="rebuild_audio_bnk", file_path=out_path, size=len(data))

        # Rebuild WPK files
        for path, wpk in self.project.wpk_files.items():
            basename = os.path.basename(path)
            out_path = os.path.join(output_dir, basename)
            # Ensure payloads are current
            for entry in wpk.entries:
                ae = self.project.audio_entries.get(entry.wem_id)
                if ae and ae.has_payload and ae.is_modified:
                    entry.payload = ae.payload
                    entry.file_size = len(ae.payload)
                elif not entry.has_payload:
                    p = self.project.get_wem_payload(entry.wem_id)
                    if p:
                        entry.payload = p
            data = wpk.rebuild()
            with open(out_path, 'wb') as f:
                f.write(data)
            outputs.append((path, out_path))
            self._log.info("Rebuilder", f"Rebuilt WPK: {basename} ({len(data)} bytes)",
                          operation="rebuild_wpk", file_path=out_path, size=len(data))

        self._log.info("Rebuilder", f"Rebuild complete: {len(outputs)} files",
                       operation="rebuild_complete")
        return outputs

    def export_wem(self, wem_id: int, output_path: str) -> bool:
        """Export a single WEM to disk."""
        payload = self.project.get_wem_payload(wem_id)
        if payload is None:
            self._log.error("Rebuilder", f"WEM {wem_id} payload not found",
                           operation="export_wem", wem_id=wem_id)
            return False
        with open(output_path, 'wb') as f:
            f.write(payload)
        self._log.info("Rebuilder", f"Exported WEM {wem_id} ({len(payload)} bytes)",
                       operation="export_wem", wem_id=wem_id, file_path=output_path)
        return True
