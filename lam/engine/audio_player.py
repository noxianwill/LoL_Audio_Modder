"""Audio playback controller using pygame-ce or native Windows fallback."""
import os
import io
import time
import struct
import threading
import tempfile
from typing import Optional, Callable
from lam.core.logging import get_logger
from lam.engine.audio_converter import AudioConverter

_pygame_available = False
try:
    import pygame
    _pygame_available = True
except ImportError:
    pass

_winsound_available = False
try:
    import winsound
    _winsound_available = True
except ImportError:
    pass


class AudioPlayer:
    """Audio playback with play/pause/stop/seek controls."""

    def __init__(self):
        self._log = get_logger()
        self._initialized = False
        self._backend: Optional[str] = None
        self._playing = False
        self._paused = False
        self._current_file: Optional[str] = None
        self._temp_dir = tempfile.mkdtemp(prefix="lam_audio_")
        self._duration_ms: int = 0
        self._on_complete: Optional[Callable] = None
        self._lock = threading.Lock()
        self._start_time: float = 0.0
        self._pause_offset_ms: float = 0.0

        if _pygame_available:
            try:
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
                self._initialized = True
                self._backend = "pygame"
                self._log.info("AudioPlayer", "pygame mixer initialized",
                              operation="init")
            except Exception as e:
                self._log.warn("AudioPlayer", f"pygame mixer init failed: {e}",
                              operation="init")

        if not self._initialized and _winsound_available:
            try:
                self._initialized = True
                self._backend = "winsound"
                self._log.info("AudioPlayer", "Windows native audio backend initialized (winsound fallback)",
                              operation="init")
            except Exception as e:
                self._log.warn("AudioPlayer", f"winsound backend init failed: {e}",
                              operation="init")

    @property
    def is_available(self) -> bool:
        return self._initialized

    @property
    def backend(self) -> Optional[str]:
        return self._backend

    @property
    def is_playing(self) -> bool:
        if self._backend == "winsound":
            if self._playing and not self._paused:
                if self._duration_ms > 0 and (time.time() - self._start_time) * 1000 >= self._duration_ms:
                    self._playing = False
                    if self._on_complete:
                        cb = self._on_complete
                        self._on_complete = None
                        try:
                            cb()
                        except Exception:
                            pass
            return self._playing and not self._paused
        return self._playing and not self._paused

    @property
    def is_paused(self) -> bool:
        return self._paused

    def play_wem(self, wem_data: bytes, on_complete: Optional[Callable] = None):
        """Play a WEM file from raw bytes."""
        if not self._initialized:
            self._log.warn("AudioPlayer", "No audio backend available")
            return

        self.stop()

        # Convert WEM to WAV
        wav_data = AudioConverter.wem_to_wav(wem_data, temp_dir=self._temp_dir)
        if wav_data is None:
            self._log.error("AudioPlayer", "Failed to decode WEM for playback",
                           operation="play")
            return

        try:
            temp_path = os.path.join(self._temp_dir, "_playback.wav")
            with open(temp_path, 'wb') as f:
                f.write(wav_data)

            # Calculate duration from WAV header
            self._duration_ms = self._get_wav_duration_ms(wav_data)

            if self._backend == "pygame":
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.play()
            elif self._backend == "winsound":
                winsound.PlaySound(temp_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                self._start_time = time.time()
                self._pause_offset_ms = 0.0

            self._playing = True
            self._paused = False
            self._current_file = temp_path
            self._on_complete = on_complete

            self._log.info("AudioPlayer", f"Playback started ({self._backend})",
                          operation="play", size=len(wem_data))
        except Exception as e:
            self._log.error("AudioPlayer", f"Playback failed: {e}",
                           operation="play")

    def play_file(self, path: str, on_complete: Optional[Callable] = None):
        """Play an audio file from disk."""
        if not self._initialized:
            return
        data = open(path, 'rb').read()
        ext = os.path.splitext(path)[1].lower()
        if ext == '.wem':
            self.play_wem(data, on_complete)
        elif ext in ('.wav', '.ogg'):
            self.stop()
            try:
                if self._backend == "pygame":
                    pygame.mixer.music.load(path)
                    pygame.mixer.music.play()
                elif self._backend == "winsound":
                    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    self._start_time = time.time()
                    self._pause_offset_ms = 0.0

                self._playing = True
                self._paused = False
                self._current_file = path
                self._on_complete = on_complete
                if ext == '.wav':
                    self._duration_ms = self._get_wav_duration_ms(data)
            except Exception as e:
                self._log.error("AudioPlayer", f"Play file failed: {e}")

    def pause(self):
        if self._initialized and self._playing:
            if self._backend == "pygame":
                pygame.mixer.music.pause()
            elif self._backend == "winsound":
                winsound.PlaySound(None, winsound.SND_PURGE)
                self._pause_offset_ms = (time.time() - self._start_time) * 1000
            self._paused = True

    def resume(self):
        if self._initialized and self._paused:
            if self._backend == "pygame":
                pygame.mixer.music.unpause()
            elif self._backend == "winsound":
                if self._current_file and os.path.exists(self._current_file):
                    winsound.PlaySound(self._current_file, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    self._start_time = time.time() - (self._pause_offset_ms / 1000.0)
            self._paused = False

    def stop(self):
        if self._initialized:
            if self._backend == "pygame":
                try:
                    pygame.mixer.music.stop()
                    pygame.mixer.music.unload()
                except Exception:
                    pass
            elif self._backend == "winsound":
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:
                    pass
            self._playing = False
            self._paused = False

    def seek(self, position_ms: int):
        if self._initialized and self._playing:
            if self._backend == "pygame":
                try:
                    pygame.mixer.music.set_pos(position_ms / 1000.0)
                except Exception:
                    pass

    def get_position_ms(self) -> int:
        if self._initialized and self._playing:
            if self._backend == "pygame":
                try:
                    return pygame.mixer.music.get_pos()
                except Exception:
                    pass
            elif self._backend == "winsound":
                if self._paused:
                    return int(self._pause_offset_ms)
                elapsed = int((time.time() - self._start_time) * 1000)
                if self._duration_ms > 0 and elapsed >= self._duration_ms:
                    self._playing = False
                    return self._duration_ms
                return max(0, elapsed)
        return 0

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    def _get_wav_duration_ms(self, wav_data: bytes) -> int:
        """Calculate WAV duration from header."""
        try:
            fmt_pos = wav_data.find(b'fmt ')
            if fmt_pos == -1:
                return 0
            chunk_size = struct.unpack_from('<I', wav_data, fmt_pos + 4)[0]
            if fmt_pos + 8 + chunk_size > len(wav_data):
                return 0
            channels = struct.unpack_from('<H', wav_data, fmt_pos + 10)[0]
            sample_rate = struct.unpack_from('<I', wav_data, fmt_pos + 12)[0]
            bits = struct.unpack_from('<H', wav_data, fmt_pos + 22)[0]
            data_pos = wav_data.find(b'data', fmt_pos)
            if data_pos == -1:
                return 0
            data_size = struct.unpack_from('<I', wav_data, data_pos + 4)[0]
            if sample_rate == 0 or channels == 0 or bits == 0:
                return 0
            bytes_per_sample = (bits // 8) * channels
            total_samples = data_size // bytes_per_sample
            return int(total_samples / sample_rate * 1000)
        except Exception:
            return 0

    def cleanup(self):
        self.stop()
        try:
            import shutil
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        except Exception:
            pass
        if self._initialized and self._backend == "pygame":
            try:
                pygame.mixer.quit()
            except Exception:
                pass
