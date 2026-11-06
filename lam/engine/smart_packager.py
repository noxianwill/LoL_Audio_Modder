"""
Smart Packager.
Automatically scans Wwise output directory, detects loaded project types (music, announcer, sfx, env),
repackages WEMs into WPK or audio BNK, and patches event/metadata BNKs if needed.
"""
import os
import shutil
import re
from typing import Dict, List, Tuple
from lam.core.logging import get_logger
from lam.engine.bnk_patcher import BnkPatcher
from lam.parsers.bnk_parser import BnkFile, DidxEntry
from lam.parsers.wpk_parser import WpkFile
from lam.core.binary_writer import BinaryWriter
from lam.model.project import Project


class SmartPackager:
    """Unified packaging and patching coordinator for all game sound types."""

    def __init__(self, project: Project):
        self.project = project
        self.log = get_logger()

    def _parse_id_map(self, search_dir: str) -> Dict[int, int]:
        """
        Parses SoundbanksInfo.xml to build a mapping of:
        Wwise-assigned internal File ID -> Original numeric WEM ID.
        """
        id_map = {}
        xml_path = os.path.join(search_dir, "SoundbanksInfo.xml")
        if not os.path.exists(xml_path):
            self.log.info("SmartPackager", "SoundbanksInfo.xml not found. ID remapping will be skipped.")
            return id_map

        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(xml_path)
            root = tree.getroot()
            files_count = 0
            for file_elem in root.findall(".//File"):
                file_id_str = file_elem.get("Id")
                if not file_id_str:
                    continue
                file_id = int(file_id_str)

                cache_path_elem = file_elem.find("CachePath")
                short_name_elem = file_elem.find("ShortName")

                source_str = ""
                if cache_path_elem is not None and cache_path_elem.text:
                    source_str = os.path.basename(cache_path_elem.text)
                elif short_name_elem is not None and short_name_elem.text:
                    source_str = short_name_elem.text

                if source_str:
                    match = re.match(r'^(\d+)', source_str)
                    if match:
                        orig_id = int(match.group(1))
                        id_map[file_id] = orig_id
                        files_count += 1

            self.log.info("SmartPackager", f"Parsed SoundbanksInfo.xml: mapped {files_count} Wwise internal IDs.")
        except Exception as e:
            self.log.warn("SmartPackager", f"Failed to parse SoundbanksInfo.xml: {e}")

        return id_map

    def package_wwise_output(self, wwise_output_dir: str, export_dir: str) -> List[str]:
        """
        Scans wwise_output_dir for generated assets (.bnk and .wem files recursively).
        Identifies loaded files in the current project (events BNKs, audio WPKs, audio BNKs).
        Applies BnkPatcher if needed and packages/embeds the new .wem files.
        Outputs new audio archives (WPK/Audio BNK) to export_dir.
        Overwrites/patches generated event BNK files directly in-place.
        Returns a list of modified/created file paths.
        """
        # Resolve smart search root directory by climbing up parent directories
        search_dir = os.path.abspath(wwise_output_dir)
        current = search_dir
        for _ in range(5):
            basename = os.path.basename(current).lower()
            if basename == "generatedsoundbanks":
                win_path = os.path.join(current, "Windows")
                if os.path.exists(win_path):
                    search_dir = win_path
                else:
                    search_dir = current
                break
            elif basename == "windows" and os.path.basename(os.path.dirname(current)).lower() == "generatedsoundbanks":
                search_dir = current
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        self.log.info("SmartPackager", f"Smart packaging starting. Scan dir: {wwise_output_dir} (resolved: {search_dir}), export dir: {export_dir}")
        if not os.path.exists(export_dir):
            os.makedirs(export_dir, exist_ok=True)

        # Parse Wwise internal File ID -> Original WEM ID mapping
        id_map = self._parse_id_map(search_dir)

        # 1. Scan for all generated files recursively (both Output and Cache directories)
        generated_bnks = {}  # basename_lower -> full_path
        generated_wems = {}  # wem_id -> full_path

        # Determine if cache folder is accessible
        project_root = os.path.dirname(os.path.dirname(search_dir))
        cache_dir = os.path.join(project_root, ".cache", "Windows", "SFX")
        
        scan_dirs = [search_dir]
        if os.path.exists(cache_dir):
            scan_dirs.append(cache_dir)
            self.log.info("SmartPackager", f"Found cache directory to scan: {cache_dir}")

        for s_dir in scan_dirs:
            for root, _, files in os.walk(s_dir):
                for file in files:
                    file_lower = file.lower()
                    full_path = os.path.join(root, file)
                    if file_lower.endswith(".bnk"):
                        generated_bnks[file_lower] = full_path
                    elif file_lower.endswith(".wem"):
                        name_base = os.path.splitext(file)[0]
                        wem_id = None
                        try:
                            wem_id = int(name_base)
                        except ValueError:
                            # Extract numerical ID from cache name (e.g. 123456_ABCDEF.wem -> 123456)
                            match = re.match(r'^(\d+)', name_base)
                            if match:
                                try:
                                    wem_id = int(match.group(1))
                                except ValueError:
                                    pass

                        if wem_id is not None:
                            # Remap Wwise internal File ID to original game WEM ID
                            orig_wem_id = id_map.get(wem_id, wem_id)
                            generated_wems[orig_wem_id] = full_path

        self.log.info("SmartPackager", f"Wwise output scan found {len(generated_bnks)} BNKs, {len(generated_wems)} WEMs.")

        processed_files = []

        # 2. Process loaded Event/Metadata BNKs
        for orig_path, lb in self.project.events_banks.items():
            orig_name = os.path.basename(orig_path)
            orig_name_lower = orig_name.lower()

            # Find the corresponding generated BNK.
            gen_path = generated_bnks.get(orig_name_lower)
            if not gen_path:
                # If name doesn't match exactly, fallback to any bnk that is generated
                if len(generated_bnks) == 1:
                    gen_path = list(generated_bnks.values())[0]
                else:
                    self.log.warn("SmartPackager", f"Could not find matching generated bank for {orig_name}")
                    continue

            out_events_path = os.path.join(export_dir, orig_name)
            self.log.info("SmartPackager", f"Reconstructing and patching events BNK: {orig_name}...")
            patcher = BnkPatcher()
            try:
                # Copy generated events BNK to export_dir first, then patch it there
                shutil.copy2(gen_path, out_events_path)
                patcher.patch(orig_path, out_events_path, id_map)
                self.log.info("SmartPackager", f"Successfully patched event BNK: {out_events_path}")
                processed_files.append(out_events_path)
            except Exception as e:
                self.log.error("SmartPackager", f"Event BNK patching failed: {e}")

        # 3. Process loaded WPK files (e.g. mus_map14_audio.wpk or announcer_global_female1_vo_audio.wpk)
        if self.project.wpk_files:
            for orig_path, wpk_file in self.project.wpk_files.items():
                orig_name = os.path.basename(orig_path)
                out_path = os.path.join(export_dir, orig_name)

                self.log.info("SmartPackager", f"Packaging WEMs to WPK: {orig_name}...")

                wpk = WpkFile(source_path=out_path)

                # Build the full WEM ID pool:
                #   - All WEMs from the ORIGINAL WPK (unmodified tracks carried over as-is)
                #   - Override/add with WEMs from Wwise output (newly generated loose files)
                # This ensures unmodified music tracks still have their audio data in the output WPK.
                all_wpk_wem_ids = set()
                orig_wpk_payloads: dict = {}

                for entry in wpk_file.entries:
                    payload = wpk_file.get_payload(entry.wem_id)
                    if payload:
                        orig_wpk_payloads[entry.wem_id] = payload
                        all_wpk_wem_ids.add(entry.wem_id)

                # Add any new WEM IDs from Wwise output (user-added or replaced tracks)
                all_wpk_wem_ids.update(generated_wems.keys())

                added_orig = 0
                added_new = 0
                for wem_id in sorted(all_wpk_wem_ids):
                    payload = None
                    # Priority: Wwise-generated loose file > original WPK
                    if wem_id in generated_wems:
                        with open(generated_wems[wem_id], "rb") as f:
                            payload = f.read()
                        added_new += 1
                    elif wem_id in orig_wpk_payloads:
                        payload = orig_wpk_payloads[wem_id]
                        added_orig += 1
                    if payload:
                        wpk.add_entry(wem_id, f"{wem_id}.wem", payload)

                wpk_bytes = wpk.rebuild()
                with open(out_path, "wb") as f:
                    f.write(wpk_bytes)

                self.log.info("SmartPackager",
                    f"Created WPK: {out_path} with {len(all_wpk_wem_ids)} WEMs "
                    f"({added_orig} original, {added_new} new/modified).")
                processed_files.append(out_path)

        # 4. Process loaded Audio BNKs (embedded audio, e.g. champion SFX *_audio.bnk)
        for orig_path, lb in self.project.audio_banks.items():
            orig_name = os.path.basename(orig_path)
            orig_name_lower = orig_name.lower()
            out_path = os.path.join(export_dir, orig_name)

            self.log.info("SmartPackager",
                f"Rebuilding Audio BNK from original + generated WEMs: {orig_name}...")

            bnk = BnkFile.parse(open(orig_path, "rb").read(), orig_path)

            all_wem_ids = set(entry.wem_id for entry in bnk.didx_entries)
            all_wem_ids.update(generated_wems.keys())

            dw = BinaryWriter()
            new_didx = []
            for wem_id in sorted(all_wem_ids):
                payload = None
                if wem_id in generated_wems:
                    with open(generated_wems[wem_id], "rb") as f:
                        payload = f.read()
                else:
                    payload = bnk.get_wem_data(wem_id)

                if payload:
                    dw.write_pad_to(16)
                    new_off = dw.position
                    dw.write_bytes(payload)
                    new_didx.append(DidxEntry(wem_id=wem_id, offset=new_off, size=len(payload)))

            bnk.didx_entries = new_didx
            bnk.data_blob = dw.get_data()

            data = bnk.rebuild()
            with open(out_path, "wb") as f:
                f.write(data)

            self.log.info("SmartPackager",
                f"Rebuilt Audio BNK: {out_path} with {len(new_didx)} WEMs.")
            processed_files.append(out_path)

        return processed_files
