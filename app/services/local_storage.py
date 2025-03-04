# app/services/local_storage.py
import os
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Union, BinaryIO
import logging

logger = logging.getLogger(__name__)


class LocalStorageService:
    """Service for local file storage when not using S3."""

    def __init__(self, base_path: str = "./local_storage"):
        self.base_path = Path(base_path)
        self.bucket_name = "local-bucket"

        # Create base directory if it doesn't exist
        os.makedirs(self.base_path, exist_ok=True)
        logger.info(f"Local storage initialized at {self.base_path}")

    def save_audio(
        self,
        audio_data: Union[bytes, BinaryIO],
        test_id: str,
        call_sid: str,
        turn_number: int,
        speaker: str,
    ) -> str:
        """Save audio data to local storage."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rel_path = f"tests/{test_id}/calls/{call_sid}/audio/{turn_number}_{speaker}_{timestamp}.wav"
        abs_path = self.base_path / rel_path

        # Create directory if it doesn't exist
        os.makedirs(abs_path.parent, exist_ok=True)

        # Save the file
        if isinstance(audio_data, bytes):
            with open(abs_path, "wb") as f:
                f.write(audio_data)
        else:
            with open(abs_path, "wb") as f:
                shutil.copyfileobj(audio_data, f)

        return f"local://{rel_path}"

    def save_recording(self, recording_url: str, test_id: str, call_sid: str) -> str:
        """Mock saving a recording (in local mode, we just create a placeholder)."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rel_path = f"tests/{test_id}/calls/{call_sid}/recordings/{timestamp}.mp3"
        abs_path = self.base_path / rel_path

        # Create directory if it doesn't exist
        os.makedirs(abs_path.parent, exist_ok=True)

        # In local mode, just create an empty file as placeholder
        with open(abs_path, "wb") as f:
            f.write(b"Local recording placeholder")

        return f"local://{rel_path}"

    def save_transcription(
        self,
        transcription: str,
        test_id: str,
        call_sid: str,
        turn_number: int,
        speaker: str,
    ) -> str:
        """Save a transcription to local storage."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        rel_path = f"tests/{test_id}/calls/{call_sid}/transcripts/{turn_number}_{speaker}_{timestamp}.txt"
        abs_path = self.base_path / rel_path

        # Create directory if it doesn't exist
        os.makedirs(abs_path.parent, exist_ok=True)

        # Save the file
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(transcription)

        return f"local://{rel_path}"

    def save_report(self, report_data: Dict[str, Any], report_id: str) -> str:
        """Save a test report to local storage."""
        timestamp = datetime.now().strftime("%Y%m%d")
        rel_path = f"reports/{timestamp}/{report_id}.json"
        abs_path = self.base_path / rel_path

        # Create directory if it doesn't exist
        os.makedirs(abs_path.parent, exist_ok=True)

        # Save the file
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, default=str, indent=2)

        return f"local://{rel_path}"

    def save_test_case(self, test_case_data: Dict[str, Any], test_id: str) -> str:
        """Save a test case configuration to local storage."""
        rel_path = f"tests/{test_id}/config.json"
        abs_path = self.base_path / rel_path

        # Create directory if it doesn't exist
        os.makedirs(abs_path.parent, exist_ok=True)

        # Save the file
        with open(abs_path, "w", encoding="utf-8") as f:
            json.dump(test_case_data, f, default=str, indent=2)

        return f"local://{rel_path}"

    def get_object(self, key: str) -> bytes:
        """Get an object from local storage."""
        # Handle local:// URLs
        if key.startswith("local://"):
            key = key.replace("local://", "")

        try:
            abs_path = self.base_path / key
            with open(abs_path, "rb") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error getting object from local storage: {str(e)}")
            return b""

    def get_json(self, key: str) -> Dict[str, Any]:
        """Get a JSON object from local storage."""
        # Handle local:// URLs
        if key.startswith("local://"):
            key = key.replace("local://", "")

        try:
            abs_path = self.base_path / key
            with open(abs_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error getting JSON from local storage: {str(e)}")
            return {}

    def list_reports(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List available reports from local storage."""
        reports_dir = self.base_path / "reports"
        if not reports_dir.exists():
            return []

        reports = []
        # Walk through the reports directory
        for root, dirs, files in os.walk(reports_dir):
            for file in files:
                if file.endswith(".json"):
                    # Get report ID from filename
                    report_id = file.replace(".json", "")
                    path = Path(root) / file

                    # Get file stats
                    stats = path.stat()

                    # Try to get some content
                    try:
                        with open(path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except:
                        data = {}

                    reports.append(
                        {
                            "report_id": report_id,
                            "date": datetime.fromtimestamp(stats.st_mtime),
                            "size": stats.st_size,
                            "local_path": str(path),
                            "data": data,
                        }
                    )

                    if len(reports) >= limit:
                        break

        # Sort by date (most recent first)
        reports.sort(key=lambda x: x["date"], reverse=True)
        return reports[:limit]

    def generate_presigned_url(self, key: str, expiration: int = 3600) -> str:
        """Generate a mock presigned URL for local files."""
        # In local mode, just return a direct file path
        if key.startswith("local://"):
            key = key.replace("local://", "")

        abs_path = self.base_path / key
        return f"file://{abs_path}"

    def ensure_bucket_exists(self):
        """Ensure local storage directory exists."""
        os.makedirs(self.base_path, exist_ok=True)
        logger.info(f"Local storage directory ensured at {self.base_path}")
