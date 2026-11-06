"""Audio format conversion: WAV/OGG/WEM handling."""
import struct
import os
import subprocess
from typing import Optional
from lam.core.logging import get_logger


class AudioConverter:
    """Handles audio format detection and conversion."""

    VGMSTREAM_PATH = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))), "tools", "vgmstream-cli.exe")

    @staticmethod
    def detect_format(data: bytes) -> str:
        """Detect audio format from header bytes."""
        if len(data) < 4:
            return "unknown"
        if data[:4] == b'RIFF':
            if len(data) >= 12 and data[8:12] == b'WAVE':
                return "wav"
            return "wem"  # RIFF-based WEM
        if data[:4] == b'OggS':
            return "ogg"
        if data[:4] == b'BKHD':
            return "bnk"
        return "unknown"

    @staticmethod
    def validate_wem(data: bytes) -> bool:
        """Validate that data is a valid WEM file (RIFF container)."""
        if len(data) < 12:
            return False
        if data[:4] != b'RIFF':
            return False
        declared_size = struct.unpack_from('<I', data, 4)[0]
        if declared_size + 8 > len(data) + 64:  # allow small tolerance
            return False
        return True

    @staticmethod
    def wav_to_wem(wav_data: bytes) -> bytes:
        """
        Convert WAV to WEM format.
        WEM is essentially a RIFF container - for PCM WAV files,
        we can use them directly as the game engine accepts RIFF/WAVE.
        For proper Vorbis encoding, external tools would be needed.
        """
        log = get_logger()
        if wav_data[:4] == b'RIFF':
            log.info("AudioConv", "WAV is already RIFF format, using as WEM",
                     operation="wav_to_wem")
            return wav_data
        log.error("AudioConv", "Invalid WAV data - missing RIFF header",
                  operation="wav_to_wem")
        return wav_data

    @staticmethod
    def wem_to_wav(wem_data: bytes, temp_dir: str = ".") -> Optional[bytes]:
        """Decode WEM to WAV using vgmstream-cli if available."""
        log = get_logger()

        # Check if it's already a standard WAV
        if len(wem_data) >= 12 and wem_data[8:12] == b'WAVE':
            # Check for fmt chunk with PCM format
            fmt_pos = wem_data.find(b'fmt ')
            if fmt_pos != -1 and fmt_pos + 10 < len(wem_data):
                audio_fmt = struct.unpack_from('<H', wem_data, fmt_pos + 8)[0]
                if audio_fmt == 1:  # PCM
                    log.info("AudioConv", "WEM is PCM WAV, returning directly",
                             operation="wem_to_wav")
                    return wem_data

        # Try vgmstream
        if os.path.exists(AudioConverter.VGMSTREAM_PATH):
            try:
                in_path = os.path.join(temp_dir, "_lam_temp.wem")
                out_path = os.path.join(temp_dir, "_lam_temp.wav")
                with open(in_path, 'wb') as f:
                    f.write(wem_data)
                result = subprocess.run(
                    [AudioConverter.VGMSTREAM_PATH, "-o", out_path, in_path],
                    capture_output=True, timeout=30
                )
                if result.returncode == 0 and os.path.exists(out_path):
                    wav_data = open(out_path, 'rb').read()
                    os.remove(in_path)
                    os.remove(out_path)
                    log.info("AudioConv", "WEM decoded via vgmstream",
                             operation="wem_to_wav", size=len(wav_data))
                    return wav_data
                os.remove(in_path)
            except Exception as e:
                log.warn("AudioConv", f"vgmstream decode failed: {e}",
                         operation="wem_to_wav")

        # Fallback: return raw RIFF data and let pygame try
        if wem_data[:4] == b'RIFF':
            return wem_data

        log.warn("AudioConv", "Cannot decode WEM - no decoder available",
                 operation="wem_to_wav")
        return None

    @staticmethod
    def load_audio_file(path: str) -> Optional[bytes]:
        """Load an audio file and validate its format."""
        log = get_logger()
        ext = os.path.splitext(path)[1].lower()

        if ext not in ('.wem', '.wav', '.ogg'):
            log.error("AudioConv", f"Unsupported format: {ext}. Use .wem, .wav, or .ogg",
                      operation="load_audio", file_path=path)
            return None

        data = open(path, 'rb').read()
        fmt = AudioConverter.detect_format(data)

        if ext == '.wem':
            if not AudioConverter.validate_wem(data):
                log.warn("AudioConv", "WEM validation warning - may be non-standard",
                         operation="load_audio", file_path=path)
            return data

        if ext == '.wav':
            return AudioConverter.wav_to_wem(data)

        if ext == '.ogg':
            log.info("AudioConv", "OGG file loaded - will be used as-is",
                     operation="load_audio", file_path=path)
            return data

        return data
