# app/utils/audio.py
import io
import os
import wave
import logging
import tempfile
from typing import Optional, Tuple
import audioop
from pydub import AudioSegment
from pydub.silence import detect_leading_silence

logger = logging.getLogger(__name__)


def trim_silence(
    audio_data: bytes,
    silence_thresh: float = -40.0,
    chunk_size: int = 10,
    format: str = "g711_ulaw",
    sample_width: int = 2,
    frame_rate: int = 8000,
    channels: int = 1,
) -> bytes:
    """
    strip silence from audio data.

    Args:
        audio_data (bytes): Raw audio data (assumed to be g711_ulaw encoded).
        silence_thresh (float): Silence threshold in dBFS (default -40 dBFS).
        chunk_size (int): Chunk size in milliseconds for silence detection.
        format (str): Audio format (default 'g711_ulaw').
        sample_width (int): Sample width in bytes after PCM conversion.
        frame_rate (int): Frame rate of audio (default 8000 for g711).
        channels (int): Number of audio channels (default 1).

    Returns:
        bytes: Trimmed audio data in original g711_ulaw format.
    """
    try:
        # Convert g711_ulaw bytes to PCM
        pcm_audio = audioop.ulaw2lin(audio_data, sample_width)

        # Load PCM data into AudioSegment
        audio_segment = AudioSegment(
            data=pcm_audio,
            sample_width=sample_width,
            frame_rate=frame_rate,
            channels=channels,
        )

        # Trim silence
        trimmed_audio_segment = audio_segment.strip_silence(
            silence_thresh=silence_thresh,
            silence_len=chunk_size,
            padding=chunk_size // 1.1,
        )

        # Export trimmed audio to PCM bytes
        trimmed_pcm = trimmed_audio_segment.raw_data

        # Convert trimmed PCM audio back to g711_ulaw
        trimmed_ulaw = audioop.lin2ulaw(trimmed_pcm, sample_width)

        return trimmed_ulaw

    except Exception as e:
        logger.error(f"Error in trim_silence: {e}")
        return audio_data  # Fallback to original if any error occurs
