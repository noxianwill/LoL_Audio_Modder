"""
CDragon hash auto-updater and cache manager.
Downloads, caches, and automatically updates League of Legends Wwise name mappings
from the CommunityDragon Data repository in the background.
"""
import os
import json
import threading
import time
import urllib.request
import urllib.error
from typing import Dict, List
from lam.core.hashing import HashLookup
from lam.core.logging import get_logger

HASHES_FILENAMES = [
    "hashes.binfields.txt",
    "hashes.binhashes.txt",
    "hashes.binentries.txt",
    "hashes.bintypes.txt",
    "hashes.game.txt.0",
    "hashes.game.txt.1",
    "hashes.game.txt.2",
    "hashes.game.txt.3",
    "hashes.game.txt.4",
    "hashes.game.txt.5",
    "hashes.game.txt.6",
    "hashes.game.txt.7",
    "hashes.game.txt.8",
    "hashes.lcu.txt",
]

BASE_URL = "https://raw.githubusercontent.com/CommunityDragon/Data/master/hashes/lol/"


class HashUpdater:
    """Handles loading cached hashes on startup and checking for updates in the background."""

    def __init__(self, workspace_dir: str, hash_lookup: HashLookup):
        self._log = get_logger()
        self.hashes_dir = os.path.join(workspace_dir, "hashes")
        self.hash_lookup = hash_lookup
        self.metadata_path = os.path.join(self.hashes_dir, "metadata.json")
        self._update_thread = None
        self.progress_callback = None
        self.is_complete = False
        self.cache_loaded = False

    def set_progress_callback(self, callback) -> None:
        """Set progress callback to receive background hash download/update status updates."""
        self.progress_callback = callback

    def _report_progress(self, message: str) -> None:
        """Report update progress to the callback if registered."""
        if self.progress_callback:
            try:
                self.progress_callback(message)
            except Exception as e:
                self._log.warn("HashUpdater", f"Progress callback failed: {e}", operation="callback_error")

    def load_cached_hashes(self) -> None:
        """Synchronously load any hashes currently saved in the local hashes folder."""
        if not os.path.exists(self.hashes_dir):
            self._log.info("HashUpdater", "No local hashes directory found", operation="load_cache")
            self.cache_loaded = True
            return

        self._log.info("HashUpdater", "Loading cached hashes from disk...", operation="load_cache")
        self._report_progress("Loading cached hashes...")
        loaded_count = 0
        total_files = len(HASHES_FILENAMES)
        for idx, filename in enumerate(HASHES_FILENAMES):
            file_path = os.path.join(self.hashes_dir, filename)
            if os.path.exists(file_path):
                self._report_progress(f"Loading cached hashes ({idx+1}/{total_files}): {filename}...")
                count = self._parse_and_register_file(file_path)
                loaded_count += count
                self._log.info("HashUpdater", f"Loaded {count} hashes from {filename}",
                               operation="load_cache_file", file_path=file_path)
        
        self._log.info("HashUpdater", f"Finished loading cache. Total: {loaded_count} hashes registered.",
                       operation="load_cache_complete")
        self.cache_loaded = True
        self._report_progress("Loading cached hashes complete.")

    def start(self) -> None:
        """Start the background check and update thread."""
        self._update_thread = threading.Thread(target=self._run_update, name="HashUpdaterThread", daemon=True)
        self._update_thread.start()

    def _parse_and_register_file(self, path: str) -> int:
        """Parse CDragon `<hash> <name>` file and register names in HashLookup."""
        count = 0
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                pairs = []
                for line in f:
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        hex_str = parts[0].strip()
                        name = parts[1].strip()
                        if name:
                            try:
                                h = int(hex_str, 16)
                                pairs.append((h, name))
                            except ValueError:
                                pass
                self.hash_lookup.register_pairs(pairs)
                count = len(pairs)
        except Exception as e:
            self._log.error("HashUpdater", f"Error parsing hash file {os.path.basename(path)}: {e}",
                            operation="parse_error")
        return count

    def _parse_and_register_content(self, content_str: str) -> int:
        """Parse CDragon file content from a string and register names in HashLookup."""
        pairs = []
        for line in content_str.splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                hex_str = parts[0].strip()
                name = parts[1].strip()
                if name:
                    try:
                        h = int(hex_str, 16)
                        pairs.append((h, name))
                    except ValueError:
                        pass
        self.hash_lookup.register_pairs(pairs)
        return len(pairs)

    def _run_update(self) -> None:
        """Background update loop."""
        self.is_complete = False
        try:
            # Load local cached hashes first in the background thread
            self.load_cached_hashes()
    
            self._log.info("HashUpdater", "Checking for CDragon hash updates in background...",
                           operation="update_check_start")
            self._report_progress("Checking for hash updates...")
            
            # Ensure directory exists
            os.makedirs(self.hashes_dir, exist_ok=True)
    
            # Load metadata
            metadata = {}
            if os.path.exists(self.metadata_path):
                try:
                    with open(self.metadata_path, "r", encoding="utf-8") as f:
                        metadata = json.load(f)
                except Exception as e:
                    self._log.warn("HashUpdater", f"Could not read metadata.json: {e}",
                                   operation="metadata_read_warning")
    
            updated_any = False
            total_files = len(HASHES_FILENAMES)
    
            for idx, filename in enumerate(HASHES_FILENAMES):
                url = BASE_URL + filename
                cached_etag = metadata.get(filename)
                file_path = os.path.join(self.hashes_dir, filename)
    
                # If file doesn't exist locally, ignore cached ETag to force download
                if not os.path.exists(file_path):
                    cached_etag = None
    
                headers = {'User-Agent': 'Mozilla/5.0'}
                if cached_etag:
                    headers['If-None-Match'] = cached_etag
    
                req = urllib.request.Request(url, headers=headers)
                try:
                    self._report_progress(f"Checking hashes ({idx+1}/{total_files}): {filename}...")
                    with urllib.request.urlopen(req) as response:
                        etag = response.info().get('ETag')
                        content_length = response.info().get('Content-Length')
                        
                        total_size = int(content_length) if content_length else None
                        chunks = []
                        bytes_read = 0
                        download_start = time.time()
                        
                        # Read chunk-by-chunk for progress reporting
                        while True:
                            chunk = response.read(1024 * 64)  # 64KB chunks
                            if not chunk:
                                break
                            chunks.append(chunk)
                            bytes_read += len(chunk)
                            
                            elapsed = time.time() - download_start
                            if elapsed > 0.05:
                                speed = bytes_read / elapsed
                                if speed > 1024 * 1024:
                                    speed_str = f"{speed / (1024 * 1024):.1f} MB/s"
                                else:
                                    speed_str = f"{speed / 1024:.1f} KB/s"
                            else:
                                speed_str = "Calculating..."
    
                            if total_size:
                                percent = int(bytes_read * 100 / total_size)
                                self._report_progress(
                                    f"Downloading hashes ({idx+1}/{total_files}): "
                                    f"{percent}% ({bytes_read / (1024*1024):.1f} MB / {total_size / (1024*1024):.1f} MB) @ {speed_str}"
                                )
                            else:
                                self._report_progress(
                                    f"Downloading hashes ({idx+1}/{total_files}): "
                                    f"{bytes_read / (1024*1024):.1f} MB @ {speed_str}"
                                )
                                
                        content = b"".join(chunks)
                        
                        # Save to local file
                        with open(file_path, "wb") as f:
                            f.write(content)
    
                        # Update etag in metadata
                        if etag:
                            metadata[filename] = etag
                        elif filename in metadata:
                            del metadata[filename]
    
                        # Parse and register new hashes
                        try:
                            self._report_progress(f"Parsing hashes ({idx+1}/{total_files}): {filename}...")
                            content_str = content.decode('utf-8', errors='ignore')
                            count = self._parse_and_register_content(content_str)
                            self._log.info("HashUpdater", f"Downloaded and registered {count} new hashes for {filename}",
                                           operation="download_success", file_path=file_path)
                            updated_any = True
                        except Exception as e:
                            self._log.error("HashUpdater", f"Error decoding downloaded hashes for {filename}: {e}",
                                            operation="decode_error")
                except urllib.error.HTTPError as e:
                    if e.code == 304:
                        self._log.info("HashUpdater", f"{filename} is up to date.",
                                       operation="cache_hit", file_path=file_path)
                    else:
                        self._log.warn("HashUpdater", f"HTTP error updating {filename}: {e.code} {e.reason}",
                                       operation="http_error", file_path=file_path)
                except urllib.error.URLError as e:
                    self._log.warn("HashUpdater", f"Network error/offline while updating {filename}: {e.reason}",
                                   operation="network_offline", file_path=file_path)
                    self.is_complete = True
                    self._report_progress("Offline mode: Using cached hashes.")
                    # Network failure, user is likely offline; stop trying for the rest
                    break
                except Exception as e:
                    self._log.error("HashUpdater", f"Unexpected error updating {filename}: {e}",
                                    operation="unexpected_error", file_path=file_path)
    
            # Save metadata if updated
            if updated_any or not os.path.exists(self.metadata_path):
                try:
                    with open(self.metadata_path, "w", encoding="utf-8") as f:
                        json.dump(metadata, f, indent=4)
                except Exception as e:
                    self._log.error("HashUpdater", f"Could not write metadata.json: {e}",
                                    operation="metadata_write_error")
    
            self._log.info("HashUpdater", "Background update check complete.",
                           operation="update_check_complete")
            
            # Report completion status
            with self.hash_lookup._lock:
                total_registered = len(self.hash_lookup._hash_to_name)
            self.is_complete = True
            if updated_any:
                self._report_progress(f"Hashes updated: {total_registered:,} loaded.")
            else:
                self._report_progress(f"Hashes up to date: {total_registered:,} loaded.")
            
            # Reset status bar to Ready after 5 seconds
            def clear_status():
                time.sleep(5)
                self._report_progress("Ready")
                
            threading.Thread(target=clear_status, daemon=True).start()
        finally:
            if not self.is_complete:
                self.is_complete = True
                self._report_progress("Ready")
