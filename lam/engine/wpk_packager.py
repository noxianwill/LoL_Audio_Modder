"""
WPK Packager utility.
Bundles a folder of .wem files into a single Riot-compliant .wpk package.
"""
import os
import re
from lam.parsers.wpk_parser import WpkFile
from lam.core.logging import get_logger


class WpkPackager:
    """Professional WPK packager mirroring Riot's internal format."""

    @staticmethod
    def package_wems(input_folder: str, output_path: str):
        """
        Packages all .wem files from the input folder into a Riot-compliant .wpk file.
        Files are sorted numerically by their ID.
        """
        log = get_logger()
        log.info("WpkPackager", f"WPK PACKAGING START: {input_folder} -> {output_path}")

        if not os.path.exists(input_folder):
            log.error("WpkPackager", f"Source folder not found: {input_folder}")
            raise FileNotFoundError(f"Source folder not found: {input_folder}")

        # Gather and sort by ID (numerical)
        wem_files = []
        for file in os.listdir(input_folder):
            if file.lower().endswith(".wem"):
                name_base = os.path.splitext(file)[0]
                try:
                    # extract numeric ID
                    wem_id = int(name_base)
                    wem_files.append((wem_id, file, os.path.join(input_folder, file)))
                except ValueError:
                    match = re.search(r'\d+', name_base)
                    if match:
                        try:
                            wem_id = int(match.group())
                            wem_files.append((wem_id, file, os.path.join(input_folder, file)))
                        except ValueError:
                            pass

        if not wem_files:
            log.error("WpkPackager", f"No .wem files found in {input_folder}")
            raise FileNotFoundError("No .wem files found in the source folder.")

        # Sort numerically by ID
        wem_files.sort(key=lambda x: x[0])
        log.info("WpkPackager", f"Found {len(wem_files)} WEM files to package.")

        wpk = WpkFile(source_path=output_path)
        for wem_id, filename, filepath in wem_files:
            with open(filepath, "rb") as f:
                payload = f.read()
            wpk.add_entry(wem_id, filename, payload)

        # Rebuild WPK bytes
        wpk_bytes = wpk.rebuild()

        with open(output_path, "wb") as f:
            f.write(wpk_bytes)

        log.info("WpkPackager", f"WPK packaging complete! Output size: {len(wpk_bytes)} bytes")
