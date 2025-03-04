# app/utils/audio.py
import io
import os
import wave
import logging
import tempfile
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


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
    except ImportError:
        logger.error(
            "pydub is required for MP3 to WAV conversion. Install with 'pip install pydub'"
        )
        return create_silent_wav()  # Fallback to silent WAV
    except Exception as e:
        logger.error(f"Error converting MP3 to WAV: {str(e)}")
        return create_silent_wav()  # Fallback to silent WAV


def trim_silence(
    wav_data: bytes, threshold: float = 0.01, min_silence_ms: int = 500
) -> bytes:
    """
    Trim silence from the beginning and end of a WAV file.

    Args:
        wav_data: WAV audio data as bytes
        threshold: Amplitude threshold for silence detection (0.0 to 1.0)
        min_silence_ms: Minimum silence duration to trim (milliseconds)

    Returns:
        Trimmed WAV audio data as bytes
    """
    try:
        # This requires pydub, which requires ffmpeg
        from pydub import AudioSegment
        from pydub.silence import detect_leading_silence, detect_trailing_silence

        # Create temporary files for processing
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav_file:
            wav_file.write(wav_data)
            wav_path = wav_file.name

        # Load audio
        sound = AudioSegment.from_wav(wav_path)

        # Convert threshold to dBFS
        threshold_dbfs = sound.dBFS * threshold

        # Detect silence
        start_trim = detect_leading_silence(
            sound, silence_threshold=threshold_dbfs, chunk_size=min_silence_ms
        )
        end_trim = detect_trailing_silence(
            sound, silence_threshold=threshold_dbfs, chunk_size=min_silence_ms
        )

        # Trim the audio
        trimmed_sound = (
            sound[start_trim:-end_trim] if end_trim > 0 else sound[start_trim:]
        )

        # Export to a new file
        trimmed_path = wav_path.replace(".wav", "_trimmed.wav")
        trimmed_sound.export(trimmed_path, format="wav")

        # Read the trimmed WAV file
        with open(trimmed_path, "rb") as trimmed_file:
            trimmed_data = trimmed_file.read()

        # Clean up temporary files
        os.remove(wav_path)
        os.remove(trimmed_path)

        return trimmed_data
    except ImportError:
        logger.error(
            "pydub is required for silence trimming. Install with 'pip install pydub'"
        )
        return wav_data  # Return original data
    except Exception as e:
        logger.error(f"Error trimming silence: {str(e)}")
        return wav_data  # Return original data


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
