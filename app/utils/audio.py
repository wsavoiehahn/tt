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
    Trim leading and trailing silence from audio data.

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


def create_silent_wav(duration_seconds: float = 1.0, sample_rate: int = 44100) -> bytes:
    """
    Create a silent WAV file of the specified duration.

    Args:
        duration_seconds: Duration of the silent audio in seconds
        sample_rate: Sample rate in Hz

    Returns:
        WAV file contents as bytes
    """
    # Calculate parameters
    channels = 1
    sample_width = 2  # bytes per sample (16-bit)
    num_frames = int(duration_seconds * sample_rate)

    # Create a BytesIO to hold the WAV data
    buffer = io.BytesIO()

    # Create a new WAV file
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.setnframes(num_frames)

        # Write silent frames (all zeros)
        wav_file.writeframes(bytes(num_frames * channels * sample_width))

    # Get the bytes data
    buffer.seek(0)
    wav_data = buffer.read()

    return wav_data


def convert_mp3_to_wav(mp3_data: bytes) -> bytes:
    """
    Convert MP3 data to WAV format.

    Args:
        mp3_data: MP3 audio data as bytes

    Returns:
        WAV audio data as bytes
    """
    try:
        # This requires pydub, which requires ffmpeg
        from pydub import AudioSegment

        # Create temporary files for conversion
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp3_file:
            mp3_file.write(mp3_data)
            mp3_path = mp3_file.name

        wav_path = mp3_path.replace(".mp3", ".wav")

        # Convert using pydub
        sound = AudioSegment.from_mp3(mp3_path)
        sound.export(wav_path, format="wav")

        # Read the WAV file
        with open(wav_path, "rb") as wav_file:
            wav_data = wav_file.read()

        # Clean up temporary files
        os.remove(mp3_path)
        os.remove(wav_path)

        return wav_data
    except ImportError as e:
        logger.error(
            "pydub is required for MP3 to WAV conversion. Install with 'pip install pydub'"
        )
        return create_silent_wav()  # Fallback to silent WAV
    except Exception as e:
        logger.error(f"Error converting MP3 to WAV: {str(e)}")
        return create_silent_wav()  # Fallback to silent WAV


def get_audio_duration(wav_data: bytes) -> float:
    """
    Get the duration of a WAV file.

    Args:
        wav_data: WAV audio data as bytes

    Returns:
        Duration in seconds
    """
    try:
        with io.BytesIO(wav_data) as wav_buffer:
            with wave.open(wav_buffer, "rb") as wav_file:
                frames = wav_file.getnframes()
                rate = wav_file.getframerate()
                duration = frames / rate
                return duration
    except Exception as e:
        logger.error(f"Error getting audio duration: {str(e)}")
        return 0.0


def split_audio(wav_data: bytes, segment_duration_ms: int = 5000) -> list:
    """
    Split audio file into fixed-duration segments.

    Args:
        wav_data: WAV audio data as bytes
        segment_duration_ms: Duration of each segment in milliseconds

    Returns:
        List of audio segments as bytes
    """
    try:
        # This requires pydub, which requires ffmpeg
        from pydub import AudioSegment

        # Create temporary files for processing
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
            wav_file.write(wav_data)
            wav_path = wav_file.name

        # Load audio
        sound = AudioSegment.from_wav(wav_path)

        # Split into segments
        segments = []
        segment_duration_ms = min(segment_duration_ms, len(sound))
        for start_ms in range(0, len(sound), segment_duration_ms):
            end_ms = min(start_ms + segment_duration_ms, len(sound))
            segment = sound[start_ms:end_ms]

            # Export segment to a temporary file
            segment_path = f"{wav_path}_{start_ms}.wav"
            segment.export(segment_path, format="wav")

            # Read segment data
            with open(segment_path, "rb") as segment_file:
                segment_data = segment_file.read()

            segments.append(segment_data)

            # Clean up segment file
            os.remove(segment_path)

        # Clean up original file
        os.remove(wav_path)

        return segments
    except ImportError:
        logger.error(
            "pydub is required for audio splitting. Install with 'pip install pydub'"
        )
        return [wav_data]  # Return original data as a single segment
    except Exception as e:
        logger.error(f"Error splitting audio: {str(e)}")
        return [wav_data]  # Return original data as a single segment
